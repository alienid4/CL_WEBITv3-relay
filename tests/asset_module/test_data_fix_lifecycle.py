"""補登分派（資料品質分數）＋新設備生命週期（v1.499.1）。"""
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import data_fix as df  # noqa: E402
import db  # noqa: E402
import device_lifecycle as dl  # noqa: E402
import pipeline  # noqa: E402

NOW = dt.datetime.now()


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


FULL = dict(physical_location="內湖機房", rack_no="M11/10-11U", user_name="承辦甲", custodian="保管乙",
            api_id="N-901", device_model="DELL R750", os="RHEL 8", environment="正式", usage_unit="數位平台部")


def _hw(c, sn, host, ip, **kw):
    db.insert_hardware(c, asset_serial=sn, hostname=host, ip=ip, **kw)


# ---------------------------------------------------------------- 補登分派
def test_逐項缺漏_VM不看機櫃與型號_帳外退役不算(tmp_path):
    c = _conn(tmp_path)
    _hw(c, "P1", "p1", "10.99.7.1", **FULL)
    _hw(c, "P2", "p2", "10.99.7.2", **{**FULL, "rack_no": "", "custodian": "", "usage_unit": "專案開發部"})
    _hw(c, "V1", "v1", "10.99.7.3", **{**FULL, "rack_no": "(VM)", "device_model": "(VM)", "is_vm": 1, "os": ""})
    _hw(c, "R1", "r1", "10.99.7.4", **{**FULL, "asset_status": "報廢"})
    _hw(c, "DYN-1", "d1", "10.99.7.5")
    ms = {m["serial"]: m for m in df.machines(c)}
    assert set(ms) == {"P1", "P2", "V1"}
    assert ms["P1"]["missing"] == ["exist"], "只有登記、沒有任何其他證據＝存在可信度只有登記"
    assert ms["P2"]["missing"] == ["rack_u", "custodian", "exist"]
    assert ms["V1"]["required"] == 7 and "rack_u" not in ms["V1"]["missing"] and "os" in ms["V1"]["missing"]
    assert ms["P1"]["score"] == round(100 * 8 / 9, 1)


def test_應填項可設定_分組加總_每週快照一次(tmp_path):
    c = _conn(tmp_path)
    _hw(c, "P1", "p1", "10.99.7.1", **FULL)
    _hw(c, "P2", "p2", "10.99.7.2", **{**FULL, "rack_no": "", "usage_unit": "專案開發部", "api_id": ""})
    df.save_required(c, ["rack_u", "api_id"])
    with pytest.raises(ValueError):
        df.save_required(c, ["nope"])
    o = df.overview(c, now=dt.datetime(2026, 10, 19, 9))
    assert o["required_total"] == 4 and o["filled_total"] == 2 and o["score"] == 50.0
    assert sum(u["machine_count"] for u in o["units"]) == o["machine_count"] == 2
    u = {x["key"]: x for x in o["units"]}
    assert u["專案開發部"]["missing_by_item"]["rack_u"] == 1 and u["專案開發部"]["missing_by_item"]["api_id"] == 1
    assert {x["key"] for x in o["systems"]} == {"N-901", df.NO_APID}
    assert len(o["trend"]) == 1
    df.overview(c, now=dt.datetime(2026, 10, 22, 9))          # 同一週不再寫
    assert len(df.trend(c)) == 1
    o3 = df.overview(c, now=dt.datetime(2026, 10, 27, 9))     # 下一週 → 兩點，趨勢出來
    assert [t["week"] for t in o3["trend"]] == ["2026-W43", "2026-W44"]


# ---------------------------------------------------------------- 生命週期
def _fresh(c, sn):
    c.execute("UPDATE hardware SET collect_ok = 1, collect_checked_at = ? WHERE asset_serial = ?",
              (NOW.strftime("%Y-%m-%d %H:%M:%S"), sn))
    c.commit()


def test_停在第一個沒做到的那一步_出口不算卡住(tmp_path):
    c = _conn(tmp_path)
    _hw(c, "DYN-00:50:56:00:00:09", "new1", "10.99.8.1")                         # 帳外 → 卡在登記
    _hw(c, "A1", "a1", "10.99.8.2", **{**FULL, "rack_no": ""})                   # 實體沒機櫃 → 卡在機櫃
    _hw(c, "A2", "a2", "10.99.8.3", **FULL)                                      # 有機櫃、沒掃到 → 卡在掃描
    _hw(c, "A3", "a3", "10.99.8.4", **FULL)                                      # 連得上 → 掃描過；收集沒齊 → 卡在收集
    _fresh(c, "A3")
    _hw(c, "V1", "v1", "10.99.8.5", **{**FULL, "rack_no": "(VM)", "device_model": "(VM)", "is_vm": 1})  # VM 跳過機櫃
    _hw(c, "R1", "r1", "10.99.8.6", **{**FULL, "asset_status": "報廢"})
    _hw(c, "R2", "r2", "10.99.8.7", **{**FULL, "asset_status": "停用"})
    c.execute("INSERT INTO onboard_audit (target_ip, trigger, ok, created_at) VALUES ('10.99.8.4','manual',1,'2026-10-01 10:00:00')")
    c.commit()
    lc = dl.compute(c, NOW)
    st = {r["serial"]: r for r in lc["rows"]}
    assert st["DYN-00:50:56:00:00:09"]["stage"] == "registered"
    assert st["A1"]["stage"] == "racked" and st["A1"]["stuck_since_src"] == "登記"
    assert st["A2"]["stage"] == "scanned"
    assert st["A3"]["stage"] == "collecting" and st["A3"]["stuck_since"] == "2026-10-01 10:00:00"
    assert st["V1"]["done"]["racked"] and st["V1"]["stage"] == "scanned"
    assert st["R1"]["stage"] == "scrapped" and st["R2"]["stage"] == "retired"
    assert sum(lc["counts"].values()) == lc["total"] == len(lc["rows"])
    assert lc["reached"]["registered"] == lc["active"] - lc["counts"]["registered"]


def test_沒有第二套漏斗_pipeline計數不受影響(tmp_path):
    c = _conn(tmp_path)
    _hw(c, "A1", "a1", "10.99.9.1", **FULL)
    _hw(c, "A2", "a2", "10.99.9.2", **FULL)
    _fresh(c, "A2")
    before = pipeline.summarize(c)["counts"]
    lc = dl.compute(c, NOW)
    assert lc["pipeline_counts"] == before == pipeline.summarize(c)["counts"]
    # 第 5 步只認漏斗的「資料齊全／已收集」
    for r in lc["rows"]:
        assert r["done"]["collecting"] == bool(set(r["pipeline_stages"]) & {"complete", "collected"})
