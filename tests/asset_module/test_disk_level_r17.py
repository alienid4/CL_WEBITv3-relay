"""R17（2.044.0）：磁碟使用率全站一把尺——80% 列「處理」、90% 列「高」、查不到是「未收集」。

驗收：79.9% 不列、80.0% 列處理、90.0% 列高；摘要數等於下鑽；缺值顯示未收集。
用到的地方：5.2 容量預警（實體主機檔案系統）、1.3 硬體異常（健康基準磁碟）、1.4 追蹤處理、Teams 磁碟告警預設門檻。
全部假資料（IP 用 192.0.2.x）。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import daily_risk  # noqa: E402
import db  # noqa: E402
import disk_alert  # noqa: E402
import disk_level  # noqa: E402
import storage_capacity as sc  # noqa: E402
import v2_today  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    yield c
    c.close()


@pytest.mark.parametrize("pct, want", [(79.9, None), (79.94, None), (79.95, "handle"), (80, "handle"), (89.9, "handle"),
                                       (90, "high"), (100, "high"), (None, "uncollected"), ("abc", "uncollected")])
def test_分級邊界(pct, want):
    assert disk_level.level(pct) == want


def test_剩餘轉使用率():
    assert disk_level.used_from_free(20.1) == 79.9 and disk_level.used_from_free(20) == 80.0
    assert disk_level.used_from_free(10) == 90.0 and disk_level.used_from_free(None) is None


def _mount(c, serial, ip, free_gb, size=100):
    db.insert_hardware(c, asset_serial=serial, hostname=serial.lower(), ip=ip)
    items = [{"name": "/data", "size_gb": size, "free_gb": free_gb}]
    c.execute("INSERT INTO host_spec (asset_serial, ip, storage_json, collected_at) VALUES (?,?,?,?)",
              (serial, ip, json.dumps({"by": "mount", "items": items}), "2026-11-04"))


def test_容量預警_實體主機_80處理_90高_未收集另計(conn):
    _mount(conn, "H-799", "192.0.2.1", 20.1)     # 使用 79.9%：不列
    _mount(conn, "H-800", "192.0.2.2", 20.0)     # 80.0%：處理
    _mount(conn, "H-900", "192.0.2.3", 10.0)     # 90.0%：高
    _mount(conn, "H-UNK", "192.0.2.4", None)     # 查不到：未收集
    conn.commit()
    al = sc.alerts(sc.datastores(conn), sc.physical(conn))
    got = {p["asset_serial"]: p["level_label"] for p in al["physical"]}
    assert got == {"H-800": "處理", "H-900": "高"}
    assert al["physical_uncollected"] == 1 and al["count"] == 2
    # 1.4 追蹤處理：容量預警卡片＝下鑽列數，等級跟 5.2 一樣
    t = v2_today.track(conn, datetime(2026, 11, 4, 10, 0), record=False)
    cap = next(c for c in t["categories"] if c["key"] == "capacity")
    rows = [r for r in t["rows"] if r["cat"] == "capacity"]
    assert cap["n"] == len(rows) == 2 and cap["high"] == 1
    assert {r["level"] for r in rows} == {"處理", "高"}
    assert "未收集 1 台" in cap["note"]


def test_硬體異常_健康基準磁碟_同一把尺(conn):
    for serial, ip in (("S-A", "192.0.2.11"), ("S-B", "192.0.2.12"), ("S-C", "192.0.2.13")):
        db.insert_hardware(conn, asset_serial=serial, hostname=serial.lower(), ip=ip)
    for ip, disks in (("192.0.2.11", {"/": 79.9}), ("192.0.2.12", {"/": 80.0}), ("192.0.2.13", {"/": 85, "/var": 90.0})):
        conn.execute("INSERT INTO health_snapshot (ip, taken_at, disks_json) VALUES (?,?,?)", (ip, "2026-11-04 01:00:00", json.dumps(disks)))
    conn.commit()
    rk = daily_risk.risk_overview(conn)
    lv = {r["serial"]: r["level"] for r in rk["hardware"]["rows"]}
    assert lv == {"S-B": "處理", "S-C": "高"}
    assert rk["settings"]["disk_pct"] == 80 and rk["settings"]["disk_high_pct"] == 90


def test_門檻不能再從設備風險頁改(conn):
    with pytest.raises(ValueError):
        daily_risk.save_settings(conn, None, 90)
    assert daily_risk.save_settings(conn, 400, 80)["uptime_days"] == 400    # 帶跟全站一樣的值不擋（舊頁面相容）


def test_Teams磁碟告警預設門檻是高那一級():
    assert disk_alert.DEFAULTS["threshold"] == int(disk_level.HIGH_PCT) == 90


def test_A4_實體主機磁碟頁不再用Datastore門檻標示():
    """2.044.3（GPT A4）：v2 與舊版儲存容量頁的「實體主機磁碟」標示都用 physLevelText／physListed（80／90），不跟 threshold。"""
    fe = ROOT / "APP" / "asset-module" / "frontend" / "pages"
    v2 = (fe / "v2" / "storage.vue").read_text(encoding="utf-8")
    old = (fe / "storage.vue").read_text(encoding="utf-8")
    assert "physLevelText(p)" in v2 and "p.free_pct < threshold.value ? '低於門檻'" not in v2
    assert "physListed(p)" in old and ":class=\"{ low: p.free_pct !== null && p.free_pct < threshold }\"" not in old
