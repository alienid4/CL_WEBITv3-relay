"""Dell Unity 中繼匯入（devkit D2/D3 的 WEBIT3 端）：批次照 unity_collect.ps1 的寫法現做——
資料夾＝日期、每台陣列一個 unity_<位址>.json（ConvertTo-Json -Compress）、manifest 最後寫且**沒有 range／batch_id**。

涵蓋：parse（含 PowerShell 5.1 陣列包裝）、apply、新批次整份取代、舊批次不蓋新、壞資料整批拒收、主機名對資產（machine_key）、
未匹配清單、verify_numbers 不變式、API 要登入。示範資料只用 *.example.test、192.0.2.x、假序號。
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import machine_identity as mi  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402
import storage_relay as sr  # noqa: E402
from test_relay_import import SAT, _api  # noqa: E402

T1 = "2026-11-06T21:05:00+08:00"
T2 = "2026-11-07T21:05:00+08:00"
GB = 1024 ** 3


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)
    qw.set_clock(lambda: SAT(10))
    db.set_setting(c, ri.DIR_KEY, str(drop))
    db.set_setting(c, ri.ENABLED_KEY, "1")
    yield c, drop
    qw.set_clock(None)
    c.close()


def device(mh="unity-a.example.test", serial="FAKESN0001", pools=None, luns=None, hosts=None, **extra):
    """一台陣列的檔案內容，欄位順序與型別照腳本的 [ordered]@{...}（Get-List 回的是 entries[].content）。"""
    doc = {"source": "unity", "schema_version": 1, "collector_version": "unity_collect v1", "collected_at": T1,
           "management_host": mh, "complete": True,
           "system": [{"id": "0", "name": "UNITY-A", "model": "Unity 480", "serialNumber": serial}],
           "pools": pools if pools is not None else [
               {"id": "pool_1", "name": "Pool-Gold", "sizeTotal": 100 * GB, "sizeUsed": 60 * GB, "sizeFree": 40 * GB},
               {"id": "pool_2", "name": "Pool-Silver", "sizeTotal": 50 * GB, "sizeUsed": 10 * GB, "sizeFree": 40 * GB}],
           "luns": luns if luns is not None else [
               {"id": "sv_1", "name": "LUN-DB01", "sizeTotal": 20 * GB,
                "hostAccess": [{"host": {"id": "Host_1"}, "accessMask": 1}, {"host": {"id": "Host_2"}, "accessMask": 3}]},
               {"id": "sv_2", "name": "LUN-AP01", "sizeTotal": 10 * GB, "hostAccess": [{"host": {"id": "Host_3"}, "accessMask": 1}]},
               {"id": "sv_3", "name": "LUN-SPARE", "sizeTotal": 5 * GB}],
           "hosts": hosts if hosts is not None else [
               {"id": "Host_1", "name": "db01.example.test", "type": 1},
               {"id": "Host_2", "name": "DB02", "type": 1},
               {"id": "Host_3", "name": "ghost01", "type": 1},
               {"id": "Host_4", "name": "192.0.2.0/24", "type": 2}]}
    doc.update(extra)
    return doc


def write_batch(drop, devices, folder="2026-11-06", collected=T1, manifest_mut=None, raw_override=None):
    """照腳本寫：資料檔先寫（Compress），最後 manifest（source／schema_version／collector_version／collected_at／count／files）。"""
    d = Path(drop) / folder
    d.mkdir(parents=True)
    files = []
    for doc in devices:
        doc = {**doc, "collected_at": collected}
        name = "unity_" + doc["management_host"].replace("/", "_") + ".json"
        raw = (raw_override or {}).get(name) or json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        (d / name).write_bytes(raw)
        files.append({"name": name, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)})
    m = {"source": "unity", "schema_version": 1, "collector_version": "unity_collect v1", "collected_at": collected,
         "count": len(files), "files": files}
    if manifest_mut:
        manifest_mut(m)
    (d / "manifest.json").write_text(json.dumps(m, indent=4), encoding="utf-8")
    return d


def hw(c, serial, hostname, ip, status="使用中"):
    c.execute("INSERT INTO hardware (asset_serial, hostname, ip, asset_status) VALUES (?,?,?,?)", (serial, hostname, ip, status))
    c.commit()


def batch_row(c, bid):
    return c.execute("SELECT status, reason_code, reason, devices, unmatched FROM relay_import_batches WHERE source='unity' AND batch_id=?",
                     (bid,)).fetchone()


def run(c):
    return ri.run_scan(c, "manual", "admin")


# ---------------------------------------------------------------- 匯入與快照
def test_腳本格式整批匯入_沒有range也收_容量與對應寫進去(env):
    c, drop = env
    hw(c, "A001", "db01", "192.0.2.21")
    hw(c, "A002", "db02.example.test", "192.0.2.22")
    write_batch(drop, [device()])
    res = run(c)
    assert res["imported"] == 1, res
    r = batch_row(c, "2026-11-06")
    assert r["status"] == "imported" and r["devices"] == 1 and r["unmatched"] == 1
    b = c.execute("SELECT range_from, range_to FROM relay_import_batches WHERE batch_id='2026-11-06'").fetchone()
    assert (b[0], b[1]) == ("2026-11-06", "2026-11-06")          # 沒有 range：視為 collected_at（台北）那天
    a = c.execute("SELECT * FROM storage_array").fetchone()
    assert a["serial"] == "FAKESN0001" and a["size_total"] == 150 * GB and a["size_used"] == 70 * GB and a["size_free"] == 80 * GB
    assert (a["pool_count"], a["lun_count"], a["host_count"], a["map_count"]) == (2, 3, 4, 3)
    hs = {h["host_id"]: (h["match_status"], h["asset_serial"]) for h in c.execute("SELECT * FROM storage_host")}
    assert hs == {"Host_1": ("matched", "A001"), "Host_2": ("matched", "A002"), "Host_3": ("no_asset", None), "Host_4": ("not_host", None)}
    o = sr.overview(c)
    assert o["cards"]["arrays"] == 1 and o["cards"]["luns"] == 3 and o["cards"]["unmatched"] == 1 and o["cards"]["used_pct"] == 46.7
    lun = {x["lun_id"]: x for x in o["luns"]}
    assert lun["sv_1"]["host_count"] == 2 and lun["sv_1"]["hosts"] == ["DB02", "db01.example.test"] and lun["sv_3"]["host_count"] == 0
    assert lun["sv_1"]["used"] is None and lun["sv_1"]["used_pct"] is None        # 腳本 v1 沒收 LUN 已用：不知道≠0
    um = ri.list_unmatched(c, "unity")
    assert [(u["ident"], u["name"], u["reason"]) for u in um] == [("FAKESN0001/Host_3", "ghost01", "no_asset")]
    assert [d["pools"] for d in o["daily"]] == [2] and o["daily"][0]["day"] == "2026-11-06"
    assert o["freshness"]["status"] in ("ok", "stale") and o["freshness"]["last_batch"] == "2026-11-06"


def test_新批次整份取代該陣列_別台不動_每日容量留歷史(env):
    c, drop = env
    other = device(mh="unity-b.example.test", serial="FAKESN0002", hosts=[], luns=[])
    write_batch(drop, [device(), other])
    assert run(c)["imported"] == 1
    newer = device(pools=[{"id": "pool_1", "name": "Pool-Gold", "sizeTotal": 100 * GB, "sizeUsed": 80 * GB, "sizeFree": 20 * GB}],
                   luns=[{"id": "sv_9", "name": "LUN-NEW", "sizeTotal": 1 * GB, "hostAccess": None}],
                   hosts=[{"id": "Host_1", "name": "db01.example.test", "type": 1}])
    write_batch(drop, [newer], folder="2026-11-07", collected=T2)
    assert run(c)["imported"] == 1
    assert {r[0] for r in c.execute("SELECT lun_id FROM storage_lun WHERE serial='FAKESN0001'")} == {"sv_9"}
    assert c.execute("SELECT COUNT(*) FROM storage_host_map WHERE serial='FAKESN0001'").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM storage_pool WHERE serial='FAKESN0002'").fetchone()[0] == 2        # 別台不動
    days = [r[0] for r in c.execute("SELECT day FROM storage_pool_daily WHERE serial='FAKESN0001' AND pool_id='pool_1' ORDER BY day")]
    assert days == ["2026-11-06", "2026-11-07"]
    # DB02、ghost01 已不在 Unity 上：未匹配清單也跟著移除（只剩這次仍對不到的 db01）
    assert [u["ident"] for u in ri.list_unmatched(c, "unity")] == ["FAKESN0001/Host_1"]


def test_較舊的批次不蓋掉較新的快照(env):
    c, drop = env
    write_batch(drop, [device()], folder="2026-11-07", collected=T2)
    assert run(c)["imported"] == 1
    write_batch(drop, [device(luns=[], hosts=[])], folder="2026-11-06", collected=T1)
    res = run(c)
    assert res["imported"] == 1
    assert c.execute("SELECT COUNT(*) FROM storage_lun").fetchone()[0] == 3
    assert "略過 1 台" in batch_row(c, "2026-11-06")["reason"]


def test_同一批同一天再匯不會重複寫入(env):
    c, drop = env
    write_batch(drop, [device()])
    assert run(c)["imported"] == 1
    assert run(c)["imported"] == 0
    assert c.execute("SELECT COUNT(*) FROM storage_pool_daily").fetchone()[0] == 2


def test_PowerShell51_陣列包裝與單筆物件照原意展開(env):
    c, drop = env
    d = device()
    d["system"] = {"value": d["system"], "Count": 1}
    d["hosts"] = {"value": d["hosts"], "Count": 4}
    d["luns"][1]["hostAccess"] = {"host": {"id": "Host_3"}, "accessMask": 1}      # 單筆變物件
    write_batch(drop, [d])
    assert run(c)["imported"] == 1
    assert c.execute("SELECT COUNT(*) FROM storage_host_map").fetchone()[0] == 3


# ---------------------------------------------------------------- 壞資料：整批拒收、一筆都不寫
BAD = [
    ("缺序號", lambda d: d["system"][0].pop("serialNumber"), "serialNumber"),
    ("兩筆system", lambda d: d["system"].append(dict(d["system"][0])), "剛好一筆"),
    ("容量是字串", lambda d: d["pools"][0].update(sizeTotal="100"), "pool.sizeTotal"),
    ("容量負數", lambda d: d["luns"][0].update(sizeTotal=-1), "lun.sizeTotal"),
    ("pool缺id", lambda d: d["pools"][0].pop("id"), "pool 的 id"),
    ("id重複", lambda d: d["luns"][1].update(id="sv_1"), "重複"),
    ("指到不存在的主機", lambda d: d["luns"][0]["hostAccess"].append({"host": {"id": "Host_99"}, "accessMask": 1}), "Host_99"),
    ("不完整快照", lambda d: d.update(complete=False), "complete"),
    ("management_host有路徑", lambda d: d.update(management_host="unity-a.example.test/api"), "management_host"),
    ("pools不是清單", lambda d: d.update(pools="x"), "pools 要是清單"),
    ("source不對", lambda d: d.update(source="whatsup"), "source"),
    ("host.type不是整數", lambda d: d["hosts"][0].update(type="1"), "host.type"),
    ("缺complete", lambda d: d.pop("complete"), "complete"),           # 審查 P2-1：必填
    ("缺schema_version", lambda d: d.pop("schema_version"), "schema_version"),
]


@pytest.mark.parametrize("label,mut,expect", BAD, ids=[b[0] for b in BAD])
def test_壞資料整批拒收(env, label, mut, expect):
    c, drop = env
    good_one = device(mh="unity-b.example.test", serial="FAKESN0002")
    bad = device()
    mut(bad)
    write_batch(drop, [good_one, bad])
    res = run(c)
    assert res["rejected"] == 1, res
    r = batch_row(c, "2026-11-06")
    assert r["reason_code"] == "bad_data" and expect in r["reason"], r["reason"]
    assert c.execute("SELECT COUNT(*) FROM storage_array").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM storage_pool").fetchone()[0] == 0


def test_同一批兩檔是同一台陣列_拒收(env):
    c, drop = env
    write_batch(drop, [device(), device(mh="unity-a2.example.test")])
    assert run(c)["rejected"] == 1
    assert "同一台陣列" in batch_row(c, "2026-11-06")["reason"]


def test_檔名不是unity開頭_拒收_含密碼欄位_拒收(env):
    c, drop = env
    d = write_batch(drop, [device()], folder="b-name", manifest_mut=lambda m: m["files"][0].update(name="x.json"))
    (d / "unity_unity-a.example.test.json").rename(d / "x.json")
    run(c)
    assert batch_row(c, "b-name")["reason_code"] == "bad_data"
    write_batch(drop, [device(password="x")], folder="b-secret")
    run(c)
    assert batch_row(c, "b-secret")["reason_code"] == "secret_field"


def test_未來時間的批次拒收(env):
    c, drop = env
    write_batch(drop, [device()], folder="b-future", collected="2099-01-01T00:00:00+08:00")
    run(c)
    assert batch_row(c, "b-future")["reason_code"] == "manifest_invalid"


def test_別的來源沒有range仍然拒收(env):
    c, drop = env
    d = Path(drop) / "w1"
    d.mkdir()
    raw = b'{"ip":"192.0.2.10","reports":{}}'
    (d / "192.0.2.10.json").write_bytes(raw)
    (d / "manifest.json").write_text(json.dumps({"source": "whatsup", "schema_version": 1, "collector_version": "x", "collected_at": T1,
                                                 "count": 1, "files": [{"name": "192.0.2.10.json", "sha256": hashlib.sha256(raw).hexdigest(),
                                                                         "size": len(raw)}]}), encoding="utf-8")
    run(c)
    r = c.execute("SELECT reason_code, reason FROM relay_import_batches WHERE batch_id='w1'").fetchone()
    assert r[0] == "manifest_invalid" and "range" in r[1]


# ---------------------------------------------------------------- 主機名對資產（只用 machine_key）
def test_對應_同名兩筆不同IP_舊口徑是兩台_列多台(env):
    c, drop = env
    hw(c, "A001", "db01", "192.0.2.21")
    hw(c, "A009", "db01", "192.0.2.29")
    hw(c, "A002", "DB02", "192.0.2.22")
    hw(c, "A003", "db02", "192.0.2.22")              # 同名同 IP 兩筆＝同一台：對到，取最小序號
    hw(c, "R001", "ghost01", "192.0.2.30", status="報廢")       # 退役不參與
    write_batch(drop, [device(hosts=[{"id": "Host_1", "name": "db01.example.test", "type": 1}, {"id": "Host_2", "name": "DB02", "type": 1},
                                     {"id": "Host_3", "name": "ghost01", "type": 1}, {"id": "Host_5", "name": "localhost", "type": 1}])])
    assert run(c)["imported"] == 1
    hs = {h["host_id"]: (h["match_status"], h["asset_serial"], h["candidates"]) for h in c.execute("SELECT * FROM storage_host")}
    assert hs["Host_1"] == ("ambiguous", None, 2)
    assert hs["Host_2"] == ("matched", "A002", 1)
    assert hs["Host_3"] == ("no_asset", None, 0)
    assert hs["Host_5"] == ("generic_name", None, 0)
    assert {u["reason"] for u in ri.list_unmatched(c, "unity")} == {"ambiguous", "no_asset", "generic_name"}


def test_對應_新口徑同名多IP是同一台(env):
    c, drop = env
    hw(c, "A001", "db01", "192.0.2.21")
    hw(c, "A009", "db01", "192.0.2.29")
    db.set_setting(c, "machine_key_mode", "hostname")
    c.commit()
    mi.refresh(c)
    try:
        if mi.mode() != "hostname":
            pytest.skip("這版 machine_identity 的口徑開關不是 machine_key_mode=hostname")
        write_batch(drop, [device(hosts=[{"id": "Host_1", "name": "DB01.example.test", "type": 1}], luns=[])])
        assert run(c)["imported"] == 1
        h = c.execute("SELECT match_status, asset_serial FROM storage_host").fetchone()
        assert (h[0], h[1]) == ("matched", "A001")
    finally:
        db.set_setting(c, "machine_key_mode", "legacy")
        c.commit()
        mi.refresh(c)


def test_之後補登資產_下一批自動對到並移出未匹配(env):
    c, drop = env
    write_batch(drop, [device()])
    run(c)
    assert len(ri.list_unmatched(c, "unity")) == 3
    first = {u["ident"]: u["first_seen"] for u in ri.list_unmatched(c, "unity")}
    hw(c, "A001", "db01", "192.0.2.21")
    write_batch(drop, [device()], folder="2026-11-07", collected=T2)
    run(c)
    left = {u["ident"]: u["first_seen"] for u in ri.list_unmatched(c, "unity")}
    assert set(left) == {"FAKESN0001/Host_2", "FAKESN0001/Host_3"} and all(left[k] == first[k] for k in left)


def test_不自動建資產(env):
    c, drop = env
    before = c.execute("SELECT COUNT(*) FROM hardware").fetchone()[0]
    write_batch(drop, [device()])
    run(c)
    assert c.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == before


# ---------------------------------------------------------------- verify_numbers
def test_verify_numbers_不變式全綠_竄改就紅(env):
    import importlib

    import verify_numbers as vn
    c, drop = env
    hw(c, "A001", "db01", "192.0.2.21")
    write_batch(drop, [device(), device(mh="unity-b.example.test", serial="FAKESN0002")])
    assert run(c)["imported"] == 1
    importlib.reload(vn)
    vn.check_storage_unity(c)
    assert vn.FAILS == 0, [r for r in vn.RESULTS if r["ok"] is False]
    c.execute("UPDATE storage_array SET size_total = size_total + 1 WHERE serial='FAKESN0001'")
    c.execute("DELETE FROM storage_host_map WHERE lun_id='sv_1' AND host_id='Host_2'")
    c.commit()
    importlib.reload(vn)
    vn.check_storage_unity(c)
    bad = {r["name"] for r in vn.RESULTS if r["ok"] is False}
    assert any("容量＝Pool 加總" in n for n in bad) and any("全部陣列容量" in n for n in bad), bad


# ---------------------------------------------------------------- API
def test_API_要登入_登入後看得到(monkeypatch):
    qw.set_clock(lambda: SAT(10))
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, p = _api(tmp, [("vera", "viewer")])
            from fastapi.testclient import TestClient

            import api
            assert TestClient(api.app, raise_server_exceptions=False).get("/api/v2/relay-import/unity").status_code == 401
            r = client("vera").get("/api/v2/relay-import/unity")
            assert r.status_code == 200 and r.json()["cards"]["arrays"] == 0
            src = {s["source"]: s for s in client("vera").get("/api/v2/relay-import").json()["sources"]}
            assert src["unity"]["implemented"] is True
            api.app.dependency_overrides.clear()
    finally:
        qw.set_clock(None)


# ---------------------------------------------------------------- 獨立審查 P2（cdfad7ba）
def test_P2_原本有資料_這次空Pool或空LUN_整批拒收不清空(env):
    c, drop = env
    write_batch(drop, [device()])
    assert run(c)["imported"] == 1
    write_batch(drop, [device(pools=[], luns=[], hosts=[])], folder="2026-11-07", collected=T2)
    run(c)
    r = batch_row(c, "2026-11-07")
    assert r["status"] == "rejected" and r["reason_code"] == "empty_snapshot" and r["reason"].startswith("Unity 回報 0 個 Pool，已保留舊資料")
    al = sr.overview(c)["empty_alert"]
    assert al == {"batch_id": "2026-11-07", "text": "Unity 回報 0 個 Pool，已保留舊資料", "serial": "FAKESN0001"}
    write_batch(drop, [device(luns=[])], folder="2026-11-08", collected="2026-11-08T21:05:00+08:00")
    run(c)
    assert batch_row(c, "2026-11-08")["reason_code"] == "empty_snapshot"
    assert c.execute("SELECT COUNT(*) FROM storage_lun").fetchone()[0] == 3        # 原快照完整保留
    assert c.execute("SELECT COUNT(*) FROM storage_pool").fetchone()[0] == 2


def test_P2_新陣列本來就沒有Pool可以收(env):
    c, drop = env
    write_batch(drop, [device(pools=[], luns=[], hosts=[])])
    assert run(c)["imported"] == 1


def test_P2_資產表其他錯誤往上丟_批次failed_不灌未匹配(env, monkeypatch):
    c, drop = env
    real = sr.host_index
    monkeypatch.setattr(sr, "host_index", lambda conn: real(_Raising(conn)))
    write_batch(drop, [device()])
    res = run(c)
    assert res["failed"] == 1, res
    assert ri.list_unmatched(c, "unity") == [] and c.execute("SELECT COUNT(*) FROM storage_array").fetchone()[0] == 0


class _Raising:
    """只讓 SELECT * FROM hardware 丟非「沒有這張表」的 OperationalError。"""

    def __init__(self, conn):
        self._c = conn

    def execute(self, sql, *a):
        import sqlite3
        if "FROM hardware" in sql:
            raise sqlite3.OperationalError("database is locked")
        return self._c.execute(sql, *a)


def test_P2_沒有資產表_記日誌當全部找不到(env, caplog):
    import logging
    c, drop = env
    c.execute("ALTER TABLE hardware RENAME TO hardware_x")
    c.commit()
    with caplog.at_level(logging.WARNING, logger="storage_relay"):
        assert sr.host_index(c) == {}
    assert any("資產表不存在" in r.message for r in caplog.records)
    c.execute("ALTER TABLE hardware_x RENAME TO hardware")
    c.commit()


def test_P2_verify唯讀_不建表不提交(tmp_path):
    import importlib

    import verify_numbers as vn
    p = tmp_path / "bare.db"
    import sqlite3
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    importlib.reload(vn)
    vn.check_storage_unity(conn)
    assert vn.FAILS == 0
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0     # 沒建任何表
    assert not conn.in_transaction


def test_P2_verify唯讀_有資料時也不寫(env):
    import importlib

    import verify_numbers as vn
    c, drop = env
    write_batch(drop, [device()])
    run(c)
    importlib.reload(vn)
    calls = []
    c.set_trace_callback(calls.append)
    vn.check_storage_unity(c)
    c.set_trace_callback(None)
    assert vn.FAILS == 0
    assert not [s for s in calls if s.lstrip().upper().startswith(("CREATE", "INSERT", "UPDATE", "DELETE", "COMMIT"))]


def test_P2_taipei_day_壞時間丟RelayReject():
    with pytest.raises(ri.RelayReject):
        sr._taipei_day("not-a-time")
    assert sr._taipei_day("2026-11-06T17:00:00Z") == "2026-11-07"


# ---------------------------------------------------------------- 2026-11-10 挑剔使用者審查（GPT）採納項
def test_Pool使用率預警_卡片等於下鑽_門檻用整數比(env):
    c, drop = env
    pools = [{"id": "pool_1", "name": "Hot", "sizeTotal": 100 * GB, "sizeUsed": 80 * GB, "sizeFree": 20 * GB},            # 剛好 80%：列入
             {"id": "pool_2", "name": "Edge", "sizeTotal": 100 * GB, "sizeUsed": 80 * GB - 1, "sizeFree": 20 * GB + 1},   # 差 1 byte：不列
             {"id": "pool_3", "name": "Cool", "sizeTotal": 100 * GB, "sizeUsed": 10 * GB, "sizeFree": 90 * GB}]
    write_batch(drop, [device(pools=pools)])
    assert run(c)["imported"] == 1
    o = sr.overview(c)
    assert o["cards"]["pools_hot"] == 1 and [p["name"] for p in o["pools_hot"]] == ["Hot"]
    assert o["settings"]["pool_warn_pct"] == sr.POOL_WARN_PCT == 80


def _daily(c, n, start_used=10 * GB, step=GB, total=100 * GB, last_day="2026-11-10"):
    from datetime import date, timedelta as td
    end = date.fromisoformat(last_day)
    for i in range(n):
        d = (end - td(days=n - 1 - i)).isoformat()
        used = start_used + i * step
        c.execute("INSERT OR REPLACE INTO storage_pool_daily VALUES (?,?,?,?,?,?,?,?)", ("FAKESN0001", "pool_1", d, "Pool-Gold", total, used, total - used, "x"))
    c.commit()


def test_趨勢_不足7天寫資料不足_足夠才估幾天後滿(env):
    from datetime import datetime as dt
    c, drop = env
    write_batch(drop, [device()])
    run(c)
    now = dt(2026, 11, 10, 22, 0)
    c.execute("UPDATE storage_array SET collected_at = '2026-11-10T21:00:00+08:00'")      # 資料是新的（不過期）
    c.execute("DELETE FROM storage_pool_daily")
    _daily(c, 5)
    p = {x["pool_id"]: x for x in sr.overview(c, now)["pools"]}["pool_1"]
    assert p["trend_days"] == 5 and p["days_to_full"] is None and p["trend_note"] == "資料不足（5 天）"
    _daily(c, 10)                                   # 10 天、每天 +1 GiB；跨 9 天增加 9 GiB；目前 Pool 剩 40 GiB（不是最後一筆每日列的 81）→ 40 天
    p = {x["pool_id"]: x for x in sr.overview(c, now)["pools"]}["pool_1"]
    assert p["trend_days"] == 10 and p["growth_30d"] == 9 * GB and p["growth_days"] == 9
    assert p["days_to_full"] == 40 and p["trend_note"] == "約 40 天"
    c.execute("UPDATE storage_pool SET size_free = 0 WHERE pool_id = 'pool_1'")
    c.commit()
    p = {x["pool_id"]: x for x in sr.overview(c, now)["pools"]}["pool_1"]
    assert p["days_to_full"] == 0 and p["trend_note"] == "已滿"
    p = {x["pool_id"]: x for x in sr.overview(c, dt(2026, 11, 20, 22, 0))["pools"]}["pool_1"]      # 陣列資料過期：不估
    assert p["days_to_full"] is None and p["trend_note"] in ("資料過期", "資料不足（0 天）")
    p2 = sr.pool_trend([{"day": f"2026-11-{d:02d}", "size_used": d * GB, "size_free": GB} for d in range(1, 11)], "2026-11-10", 5 * GB, stale=True)
    assert p2["trend_note"] == "資料過期" and p2["days_to_full"] is None
    p3 = sr.pool_trend([{"day": f"2026-11-{d:02d}", "size_used": d * GB, "size_free": GB} for d in range(1, 11)], "2026-11-10", -1)
    assert p3["days_to_full"] == 0 and p3["trend_note"] == "已滿"
    c.execute("UPDATE storage_pool SET size_free = 40 * 1073741824 WHERE pool_id = 'pool_1'")
    c.commit()
    c.execute("UPDATE storage_pool_daily SET size_used = 10 * 1073741824, size_free = 90 * 1073741824")
    c.commit()
    p = {x["pool_id"]: x for x in sr.overview(c, now)["pools"]}["pool_1"]
    assert p["days_to_full"] is None and p["trend_note"] == "未增加"
    pd = [r for r in sr.overview(c, now)["pool_daily"] if r["pool_id"] == "pool_1"]
    assert [r["day"] for r in pd][:2] == ["2026-11-10", "2026-11-09"] and pd[0]["pool"] == "Pool-Gold"


def test_每台陣列自己的資料時間與過期標示(env):
    from datetime import datetime as dt
    c, drop = env
    write_batch(drop, [device()])                   # 收集 2026-11-06 21:05（台北）
    run(c)
    a = sr.overview(c, dt(2026, 11, 7, 21, 5))["arrays"][0]
    assert a["collected_text"] == "2026-11-06 21:05:00" and a["age_hours"] == 24.0 and a["stale"] is False
    o = sr.overview(c, dt(2026, 11, 10, 21, 6))
    assert o["arrays"][0]["stale"] is True and o["cards"]["stale_arrays"] == 1


def test_P2b_管理員接受空快照_只生效一次_記進批次備註(env):
    c, drop = env
    write_batch(drop, [device()])
    run(c)
    write_batch(drop, [device(pools=[], luns=[], hosts=[])], folder="2026-11-07", collected=T2)
    run(c)
    assert batch_row(c, "2026-11-07")["reason_code"] == "empty_snapshot"
    with pytest.raises(ValueError):
        sr.accept_empty(c, "NOSUCH")
    res = sr.accept_empty(c, "FAKESN0001")
    assert res == {"serial": "FAKESN0001", "retry_batches": 1}
    run(c)                                            # 同一批（內容沒變）重跑
    r = batch_row(c, "2026-11-07")
    assert r["status"] == "imported" and "管理員已接受空快照：FAKESN0001" in r["reason"]
    assert c.execute("SELECT COUNT(*) FROM storage_pool WHERE serial='FAKESN0001'").fetchone()[0] == 0
    assert db.get_setting(c, sr.ACCEPT_EMPTY_KEY, "") == ""          # 用掉即清空
    assert sr.overview(c)["empty_alert"] is None
    # 只生效一次：再一次「有資料 → 空」照樣擋
    write_batch(drop, [device()], folder="2026-11-08", collected="2026-11-08T21:05:00+08:00")
    run(c)
    write_batch(drop, [device(pools=[], luns=[], hosts=[])], folder="2026-11-09", collected="2026-11-09T21:05:00+08:00")
    run(c)
    assert batch_row(c, "2026-11-09")["reason_code"] == "empty_snapshot"


def test_P2b_接受空快照API_只有管理員(monkeypatch):
    qw.set_clock(lambda: SAT(10))
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, p = _api(tmp, [("admin", "admin"), ("alice", "user")])
            import api
            cn = db.get_connection(p)
            sr.ensure(cn)
            cn.execute("INSERT INTO storage_array (serial, name) VALUES ('FAKESN0001', 'UNITY-A')")
            cn.commit()
            cn.close()
            assert client("alice").post("/api/v2/relay-import/unity/accept-empty", json={"serial": "FAKESN0001"}).status_code == 403
            r = client("admin").post("/api/v2/relay-import/unity/accept-empty", json={"serial": "FAKESN0001"})
            assert r.status_code == 200 and r.json()["serial"] == "FAKESN0001"
            assert client("admin").post("/api/v2/relay-import/unity/accept-empty", json={"serial": "../x"}).status_code == 400
            api.app.dependency_overrides.clear()
    finally:
        qw.set_clock(None)
