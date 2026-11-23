"""「只涵蓋已納管主機」類頁面（ESJE 第 2 點）接線：1.3 逾期未重開機／硬體異常／目錄清理、5.2 實體主機磁碟、
主機履歷 連線關聯／軟體與服務／帳號與安全／系統重建資料。判定一律沿用共用的 data_coverage（新增 spec／storage／hk／
relation／rebuild 五類），清單頁頁首＝V2Coverage，主機履歷頁籤頂端＝data_coverage.for_host（同一支 _row）。

每頁都要：頁首（清單頁）或頁籤頂端（主機履歷）顯示狀態；逐筆有資料時間；查不到時講得出是不支援、收集失敗（含權限不足）、
未納管、資料過期還是已納管尚無資料（不寫 0、不寫正常）。全部假資料、例子 IP 一律 10.99.x。"""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import daily_risk as dr  # noqa: E402
import data_coverage as dc  # noqa: E402
import db  # noqa: E402
import storage_capacity as sc  # noqa: E402
import v2_sections as vs  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _now_s(days_ago=0):
    return (dt.datetime.now() - dt.timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 1.3 設備風險：逐筆資料時間＋過期
def test_逾期未重開機_每列帶資料時間與資料天數(tmp_path):
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="A1", hostname="app01", ip="10.99.0.1", os="RHEL 8", collect_ok=1)
    db.insert_hardware(c, asset_serial="A2", hostname="app02", ip="10.99.0.2", os="RHEL 8", collect_ok=1)
    for sn, ip, ago in (("A1", "10.99.0.1", 1), ("A2", "10.99.0.2", 40)):
        c.execute("INSERT INTO host_spec (asset_serial, ip, platform, spec_json, collected_at) VALUES (?,?,?,?,?)",
                  (sn, ip, "linux", json.dumps({"uptime": "up 2 years"}), _now_s(ago)))
    c.commit()
    o = dr.risk_overview(c)
    rows = {r["serial"]: r for r in o["uptime"]["rows"]}
    assert rows["A1"]["data_age_days"] == 1 and rows["A2"]["data_age_days"] == 40
    assert o["data_stale_days"] == dc.stale_days_for(c, "spec")
    assert rows["A1"]["collected_at"], "逐筆可追資料時間"


def test_硬體異常_每列帶資料時間(tmp_path):
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="A1", hostname="app01", ip="10.99.0.1", os="RHEL 8", collect_ok=1)
    c.execute("INSERT INTO host_spec (asset_serial, ip, platform, spec_json, collected_at) VALUES (?,?,?,?,?)",
              ("A1", "10.99.0.1", "linux", json.dumps({"disks": [{"disk": "sda", "health": "FAILED"}]}), "2026-11-10 08:00:00"))
    c.commit()
    r = dr.risk_overview(c)["hardware"]["rows"][0]
    assert r["data_at"] == "2026-11-10 08:00:00"


# ---------------------------------------------------------------- 5.2 實體主機磁碟
def test_實體主機磁碟_每列帶資料天數與期限(tmp_path):
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="A1", hostname="app01", ip="10.99.0.1", os="RHEL 8", collect_ok=1)
    c.execute("INSERT INTO host_spec (asset_serial, ip, platform, spec_json, storage_json, collected_at) VALUES (?,?,?,?,?,?)",
              ("A1", "10.99.0.1", "linux", "{}", json.dumps({"by": "mount", "items": [{"name": "/", "size_gb": 10, "free_gb": 5}]}),
               _now_s(20)))
    c.commit()
    p = sc.physical(c)
    assert p["rows"][0]["data_age_days"] == 20 and p["data_stale_days"] == dc.stale_days_for(c, "storage")


def test_實體主機磁碟_沒資料時間不是0天(tmp_path):
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="A1", hostname="app01", ip="10.99.0.1", os="RHEL 8", collect_ok=1)
    c.execute("INSERT INTO host_spec (asset_serial, ip, platform, spec_json, storage_json, collected_at) VALUES (?,?,?,?,?,?)",
              ("A1", "10.99.0.1", "linux", "{}", json.dumps({"by": "mount", "items": []}), None))
    c.commit()
    assert sc.physical(c)["rows"][0]["data_age_days"] is None


# ---------------------------------------------------------------- 1.3／5.2 頁首覆蓋率：沿用 data_coverage，新增的類別
def _c2(tmp_path):
    c = _conn(tmp_path)
    for sn, ip, ok in (("A1", "10.99.0.1", 1), ("A2", "10.99.0.2", 1), ("A3", "10.99.0.3", 1), ("A4", "10.99.0.4", None)):
        db.insert_hardware(c, asset_serial=sn, hostname=f"app{sn}", ip=ip, os="RHEL 8", collect_ok=ok,
                           collect_checked_at=_now_s(0) if ok else None, asset_status="使用中")
    db.insert_hardware(c, asset_serial="E1", hostname="esx01", ip="10.99.0.9", os="VMware ESXi 8.0", asset_status="使用中")
    st = json.dumps({"by": "mount", "items": [{"name": "/", "size_gb": 10, "free_gb": 5}]})
    c.execute("INSERT INTO host_spec (asset_serial, ip, platform, spec_json, storage_json, collected_at) VALUES (?,?,?,?,?,?)",
              ("A1", "10.99.0.1", "linux", "{}", st, _now_s(1)))
    c.execute("INSERT INTO host_spec (asset_serial, ip, platform, spec_json, storage_json, collected_at) VALUES (?,?,?,?,?,?)",
              ("A2", "10.99.0.2", "linux", "{}", None, _now_s(40)))
    c.execute("INSERT INTO collect_ssh_trace (asset_serial, ip, connected, transport_error, cmd_total, cmd_ok, cmd_failed, "
              "collected_at) VALUES ('A3','10.99.0.3',0,'Permission denied (publickey)',0,0,0,?)", (_now_s(0),))
    c.commit()
    return c


def _st(cov):
    return {r["serial"]: r["state"] for r in cov["rows"]}


def test_規格覆蓋率_各態各落其格(tmp_path):
    c = _c2(tmp_path)
    cov = dc.coverage(c, "spec")
    assert _st(cov) == {"A1": "ok", "A2": "stale", "A3": "failed", "A4": "unmanaged", "E1": "unsupported"}
    r = next(x for x in cov["rows"] if x["serial"] == "A3")
    assert "權限不足" in r["reason"], "權限不足跟連不上分開講"
    assert cov["scope_total"] == 4 and cov["valid"] == 1 and cov["pct"] == 25.0


def test_磁碟覆蓋率_只認有磁碟資料的收集(tmp_path):
    c = _c2(tmp_path)
    st = _st(dc.coverage(c, "storage"))
    assert st["A1"] == "ok" and st["A2"] == "pending", "A2 有規格沒磁碟＝已納管尚無資料，不算有資料也不算過期"


def test_目錄掃描覆蓋率_失敗與成功與期限30天(tmp_path):
    import dir_housekeeping as hk
    c = _c2(tmp_path)
    hk.ensure_table(c)
    c.execute("INSERT INTO hk_scan (asset_serial, ip, status, finished_at) VALUES ('A1','10.99.0.1','ok',?)", (_now_s(20),))
    c.execute("INSERT INTO hk_scan (asset_serial, ip, status, finished_at) VALUES ('A2','10.99.0.2','busy',?)", (_now_s(1),))
    c.execute("INSERT INTO hk_scan (asset_serial, ip, status, finished_at) VALUES ('A4','10.99.0.4','ok',?)", (_now_s(40),))
    c.commit()
    cov = dc.coverage(c, "hk")
    rows = {r["serial"]: r for r in cov["rows"]}
    assert cov["stale_days"] == 30
    assert rows["A1"]["state"] == "ok", "20 天前掃過，目錄掃描 30 天內有效"
    assert rows["A2"]["state"] == "failed" and "忙碌" in rows["A2"]["reason"]
    assert rows["A4"]["state"] == "stale"


def test_覆蓋率_新類別格子等於下鑽(tmp_path):
    c = _c2(tmp_path)
    for kind in ("spec", "storage", "hk", "relation", "rebuild"):
        cov = dc.coverage(c, kind)
        for s, n in cov["counts"].items():
            assert n == len(dc.drill(c, kind, s)["rows"]), (kind, s)


def test_verify_numbers_獨立重算有資料台數_正常通過_造假會紅(tmp_path, monkeypatch):
    import verify_numbers as vn
    c = _c2(tmp_path)
    before, checks = vn.FAILS, vn.CHECKS
    vn.check_data_coverage_host_pages(c)
    vn.check_data_coverage(c)
    assert vn.FAILS == before and vn.CHECKS > checks
    real = dc.coverage

    def fake(conn, kind, now=None):
        out = real(conn, kind, now)
        out["valid"] += 1                       # 頁首「有資料」多算一台
        return out
    monkeypatch.setattr(dc, "coverage", fake)
    vn.check_data_coverage_host_pages(c)
    assert vn.FAILS > before
    vn.FAILS = before


# ---------------------------------------------------------------- 審查修正（P2／P3）
def test_審查P2_單平台類別_作業系統未知不離開分母(tmp_path):
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="U-1", hostname="unk01", ip="10.99.5.1", os=None, collect_ok=1,
                       collect_checked_at=_now_s(0), asset_status="使用中")
    db.insert_hardware(c, asset_serial="U-2", hostname="unk02", ip="10.99.5.2", os=None, asset_status="使用中")
    db.insert_hardware(c, asset_serial="L-1", hostname="lnx01", ip="10.99.5.3", os="RHEL 8", asset_status="使用中")
    c.commit()
    for kind in ("perf", "spec"):
        r = {x["serial"]: x for x in dc.coverage(c, kind)["rows"]}
        assert r["U-1"]["state"] == dc.PENDING and r["U-1"]["reason"].startswith("作業系統未知"), kind
        assert r["U-2"]["state"] == dc.UNMANAGED and r["U-2"]["reason"].startswith("作業系統未知"), kind
    assert {x["serial"]: x["state"] for x in dc.coverage(c, "perf")["rows"]}["L-1"] == dc.UNSUPPORTED, "已知非 AIX 才算不支援"


def test_審查P3_獨立重算接受分鐘與時區格式_不假紅(tmp_path):
    import dir_housekeeping as hk
    import verify_numbers as vn
    c = _c2(tmp_path)
    hk.ensure_table(c)
    t = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")          # 只到分鐘
    c.execute("INSERT INTO hk_scan (asset_serial, ip, status, finished_at) VALUES ('A1','10.99.0.1','ok',?)", (t,))
    tz = (dt.datetime.utcnow() - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")  # 帶時區
    c.execute("UPDATE host_spec SET collected_at = ? WHERE asset_serial = 'A1'", (tz,))
    c.commit()
    before = vn.FAILS
    vn.check_data_coverage_host_pages(c)
    assert vn.FAILS == before


def test_審查P3_帳號軟體獨立重算_造假會紅(tmp_path, monkeypatch):
    import account_inventory
    import verify_numbers as vn
    c = _c2(tmp_path)
    account_inventory.upsert_accounts(c, "10.99.0.1", "A1", [{"username": "root", "uid": 0, "kind": "default"}], "ssh",
                                      os_info={"family": "rhel"})
    before = vn.FAILS
    vn.check_data_coverage(c)
    assert vn.FAILS == before
    real = dc.coverage

    def fake(conn, kind, now=None):
        out = real(conn, kind, now)
        if kind == "account":
            for r in out["rows"]:                # 把有資料改成尚無資料，計數跟著改：恆等式全過，只有獨立重算抓得到
                if r["state"] == dc.OK:
                    r["state"] = dc.PENDING
            out["counts"] = {s: sum(1 for r in out["rows"] if r["state"] == s) for s in dc.STATES}
            out["valid"] = out["counts"][dc.OK]
        return out
    monkeypatch.setattr(dc, "coverage", fake)
    vn.check_data_coverage(c)
    assert vn.FAILS > before
    vn.FAILS = before


def test_審查P3_覆蓋率清單用machine_key連結():
    v = (FE / "components" / "v2" / "Coverage.vue").read_text(encoding="utf-8")
    assert "encodeURIComponent(r.key || r.serial)" in v, "沒有序號的主機也要點得進主機履歷"


# ---------------------------------------------------------------- 主機履歷四個頁籤
def _seed_host(c, sn="L-1", ip="10.99.2.14", os="Red Hat Enterprise Linux 9", collect_ok=1):
    db.insert_hardware(c, asset_serial=sn, hostname=f"h{sn}", ip=ip, os=os, collect_ok=collect_ok,
                       collect_checked_at=_now_s(0) if collect_ok else None, asset_status="使用中")


def _states(ts, tab):
    return [x["state"] for x in ts[tab]]


def test_主機履歷_四個頁籤都有狀態與資料時間(tmp_path):
    import account_inventory
    import software_inventory

    c = _conn(tmp_path)
    _seed_host(c)
    software_inventory.upsert_packages(c, "10.99.2.14", "L-1", [{"name": "bash", "version": "5.1", "arch": "x86_64"}], "ssh_rpm")
    account_inventory.upsert_accounts(c, "10.99.2.14", "L-1", [{"username": "root", "uid": 0, "kind": "default"}], "ssh",
                                      os_info={"family": "rhel"})
    ts = vs.summary(c, "L-1")["tab_state"]
    assert set(ts) == {"sw", "sec", "relation", "rebuild"}
    assert [x["kind"] for x in ts["sw"]] == ["software", "service"], "軟體與服務兩類各一行"
    assert ts["sw"][0]["state"] == "ok" and ts["sw"][0]["updated_at"]
    assert _states(ts, "sec") == ["ok"]
    assert _states(ts, "relation") == ["pending"], "已納管、沒收到連線：寫尚無資料，不寫 0 條"
    assert _states(ts, "rebuild") == ["pending"]


def test_主機履歷_未納管與不支援_講得出來(tmp_path):
    c = _conn(tmp_path)
    _seed_host(c, "U-1", "10.99.3.1", collect_ok=None)
    _seed_host(c, "E-1", "10.99.3.2", os="VMware ESXi 7.0", collect_ok=None)
    ts = vs.summary(c, "U-1")["tab_state"]
    assert {x["state"] for tab in ts.values() for x in tab} == {"unmanaged"}
    ts = vs.summary(c, "E-1")["tab_state"]
    assert {x["state"] for tab in ts.values() for x in tab} == {"unsupported"}
    sec = next(s for s in vs.summary(c, "E-1")["sections"] if s["key"] == "accounts")
    assert sec["count"] is None and sec["unknown"].startswith("不支援"), "不支援不寫成 0 筆"


def test_主機履歷_收集失敗_權限不足_講得出來(tmp_path):
    c = _conn(tmp_path)
    _seed_host(c, "F-1", "10.99.3.3")
    c.execute("INSERT INTO collect_ssh_trace (asset_serial, ip, connected, transport_error, cmd_total, cmd_ok, cmd_empty, "
              "cmd_failed, samples, collected_at) VALUES ('F-1','10.99.3.3',0,'Permission denied (publickey)',0,0,0,0,'[]',?)",
              (_now_s(1),))
    c.commit()
    ts = vs.summary(c, "F-1")["tab_state"]
    assert _states(ts, "sec") == ["failed"] and "權限不足" in ts["sec"][0]["reason"]
    sec = next(s for s in vs.summary(c, "F-1")["sections"] if s["key"] == "accounts")
    assert sec["unknown"].startswith("收集失敗") and "權限不足" in sec["unknown"]


def test_主機履歷_資料過期(tmp_path):
    import software_inventory

    c = _conn(tmp_path)
    _seed_host(c)
    software_inventory.upsert_packages(c, "10.99.2.14", "L-1", [{"name": "bash", "version": "5.1", "arch": "x86_64"}], "ssh_rpm")
    c.execute("UPDATE host_package SET last_seen = ?", (_now_s(30),))
    c.commit()
    ts = vs.summary(c, "L-1")["tab_state"]
    assert ts["sw"][0]["state"] == "stale"


def test_主機履歷_頁籤狀態跟清單頁頁首同一把尺(tmp_path):
    c = _c2(tmp_path)
    for kind in ("spec", "storage", "relation"):
        for row in dc.coverage(c, kind)["rows"]:
            one = dc.for_host(c, row["key"], kind)
            assert (one["state"], one["reason"], one["updated_at"]) == (row["state"], row["reason"], row["updated_at"])


# ---------------------------------------------------------------- 畫面接線（改壞就紅）
def test_清單頁頁首都有覆蓋率():
    risk = (FE / "pages" / "v2" / "risk.vue").read_text(encoding="utf-8")
    assert risk.count('<V2Coverage kind="spec" />') == 2, "逾期未重開機、硬體異常兩個頁籤"
    assert '<V2Coverage kind="hk" />' in risk
    assert "label: '資料時間'" in risk
    storage = (FE / "pages" / "v2" / "storage.vue").read_text(encoding="utf-8")
    assert '<V2Coverage kind="storage" />' in storage and "label: '資料時間'" in storage


def test_主機履歷頁籤頂端都有狀態():
    m = (FE / "pages" / "v2" / "machine" / "[key].vue").read_text(encoding="utf-8")
    assert "summary?.tab_state?.relation" in m
    assert "tab === 'sw' || tab === 'sec' || tab === 'rebuild'" in m
    ds = (FE / "components" / "v2" / "DataState.vue").read_text(encoding="utf-8")
    assert "updated_at" in ds and "state_label" in ds
