"""2.x 新版畫面（A1 首頁、A2 病歷表）的彙整：類型標記對應、搜尋、病歷表 6 格、同系統台數＝下鑽、首頁數字＝對帳橋。
全部假資料。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import v2_views as v  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, serial, host, ip, rack, model=None, os=None, api=None, is_vm=None, hw_serial=None, loc="01_甲機房"):
    db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location=loc, rack_no=rack,
                       device_model=model, os=os, api_id=api, is_vm=is_vm, hw_serial=hw_serial)


def _seed(conn):
    conn.execute("INSERT OR REPLACE INTO business_system (api_id, name, sys_class) VALUES ('N-099', '假交易系統', 'S1-第一類系統軟體（核心）')")
    _hw(conn, "A-1", "aix01", "10.99.1.18", "B2/12-15U", "IBM S1024", "AIX 7.2", "N-099")
    _hw(conn, "A-2", "aix02", "10.99.1.19", "B2/20-23U", "IBM S1024", "AIX 7.2", "N-099")
    _hw(conn, "W-1", "win01", "10.99.2.1", "VM", "(VM)", "Windows Server 2019", "N-099", is_vm="Y")
    _hw(conn, "O-1", "fake-oda-t1", "10.99.3.1", "B3/29-34U", "Oracle ODA X7-2 HA", "Oracle Linux 7")
    _hw(conn, "S-1", "store01", "10.99.4.1", "B3/10U", "IBM FS5300")
    _hw(conn, "X-1", "mystery", "10.99.5.1", None, None, None)
    conn.commit()


def test_類型標記對應():
    b = v.type_badge
    assert b({"os": "AIX 7.2", "device_model": "IBM S1024"})["code"] == "AIX"
    assert b({"os": "V7R4M0", "device_model": "IBM AS400"})["code"] == "400"
    w = b({"os": "Windows Server 2019", "device_model": "(VM)", "is_vm": "Y"})
    assert {k: w[k] for k in ("code", "label", "vm", "guessed", "os_class")} == \
        {"code": "WIN", "label": "Windows", "vm": True, "guessed": False, "os_class": "Windows"}
    assert b({"os": "Red Hat Enterprise Linux 9", "device_model": "Dell R750"})["code"] == "LNX"
    assert b({"os": "Red Hat Enterprise Linux CoreOS 4.14", "device_model": "(VM)"})["code"] == "OCP"
    assert b({"os": "Oracle Linux 7", "device_model": "Oracle ODA X7-2 HA"})["code"] == "ORA"
    assert b({"os": "VMware ESXi 8.0", "device_model": "Dell R750"})["code"] == "ESX"
    assert b({"os": None, "device_model": "IBM 7063-CR2"})["code"] == "HMC"
    assert b({"os": None, "device_model": None})["code"] == "?"
    assert b({"os": None, "device_model": "x"}, role="phys_storage")["code"] == "STO"
    g = b({"os": "", "device_model": "Cisco C9300", "group_name": "網路設備"})
    assert g["code"] == "NET" and g["guessed"] is True                  # OS 欄空、從群組推出來
    assert set(v.TYPE_LABEL) == {"LNX", "WIN", "AIX", "400", "ORA", "OCP", "ESX", "NET", "STO", "HMC", "?"}


def test_狀態():
    s = v.status_chip
    assert s({"exist_level": "scan"}, {})["label"] == "運作中"
    assert s({"exist_level": "collect_stale"}, {})["label"] == "逾期未偵測"
    assert s({"exist_level": "none"}, {})["label"] == "未偵測到"
    assert s({"exist_level": "cia_only"}, {})["label"].startswith("無資料（")
    assert s({"exist_level": "scan", "retired": True}, {})["label"] == "無資料（已退役）"


def test_搜尋_打字就出(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = v.search(conn, "aix0")
        assert [m["hostname"] for m in r["machines"]] == ["aix01", "aix02"] and r["top"] is None   # 兩台：不替人挑
        r = v.search(conn, "10.99.1.18")
        assert r["top"]["hostname"] == "aix01" and r["top"]["badge"]["code"] == "AIX" and r["top"]["system"] == "假交易系統"
        r = v.search(conn, "假交易")
        assert r["systems"] == [{"api_id": "N-099", "name": "假交易系統", "machines": 3}]
        r = v.search(conn, "N-099")
        assert r["top"]["kind"] == "system"
        assert v.search(conn, "a")["machines"] == []                        # 少於 2 個字不查
    finally:
        conn.close()


def test_病歷表_六格與同系統台數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        m = v.machine(conn, "aix01")
        assert m["mode"] == "machine" and m["badge"]["code"] == "AIX"
        assert m["core"]["api_id"] == "N-099"
        assert m["conclusion"].startswith("業務系統「假交易系統」的 AIX 7.2 主機，位於甲機房 B2 櫃")
        assert m["who_is"]["hw_serial"] is None and m["who_is"]["hw_serial_note"] == "無資料（CIA 未填）"
        ss = m["same_system"][0]
        assert ss["count"] == len(ss["machines"]) == 2 and {x["hostname"] for x in ss["machines"]} == {"aix02", "win01"}
        ri = m["where"]["rack_info"]
        assert (ri["room"], ri["rack"], ri["u"]) == ("甲機房", "B2", "12-15U") and ri["others"] == 1
        assert m["impact"]["at_least"] >= 1 and m["impact"]["systems_all"][0]["api_id"] == "N-099"
        assert m["alive"]["onboarded"] is False
        # IP、資產序號、代號都對到同一台
        assert v.machine(conn, "10.99.1.18")["key"] == m["key"] == v.machine(conn, "A-1")["key"] == v.machine(conn, m["key"])["key"]
        vm = v.machine(conn, "win01")
        assert vm["badge"]["vm"] is True and "虛擬機" in vm["conclusion"]
        assert v.machine(conn, "aix0")["mode"] == "candidates"
        assert v.machine(conn, "N-099")["mode"] == "system"
        assert v.machine(conn, "zzzz")["mode"] == "none"
        x = v.machine(conn, "mystery")
        assert x["badge"]["code"] == "?" and x["systems"] == [] and "未對應業務系統" in x["conclusion"]
        assert v.connections(conn, m["key"])["reason"]                      # 沒有連線資料要講原因
        reg = v.registrations(conn, m["key"])
        assert reg["records"][0]["fields"]["hostname"] == "aix01"
        assert v.history(conn, m["key"])["items"] == []
    finally:
        conn.close()


def test_首頁_數字跟對帳橋一樣_沒事要說(tmp_path):
    import count_bridge
    import verify_numbers as vn

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        h = v.home(conn)
        trial = {t["key"]: t["value"] for t in count_bridge.compute(conn)["summary"]["trial"]}
        assert (h["physical"], h["hosts"]) == (trial["physical"], trial["hosts"])
        assert isinstance(h["todo"], list) and all(t["link"] for t in h["todo"])
        vn.RESULTS.clear()
        vn.check_v2(conn)
        got = [r for r in vn.RESULTS if r["page"].startswith("2.x")]
        assert len(got) == 14 and all(r["ok"] for r in got), got     # 首頁待處理每一項＝下鑽頁同一份＋key 不重複＋無資料不消失
    finally:
        conn.close()


def test_API_接線(tmp_path):
    from routers import v2 as r2

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        assert r2.v2_machine_endpoint("aix01", None, conn)["mode"] == "machine"
        assert r2.v2_search_endpoint("aix", None, conn)["total"] == 2
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as e:
            r2.v2_connections_endpoint("nope", None, conn)
        assert e.value.status_code == 404
    finally:
        conn.close()


def test_首頁待處理_有進行中事件就不能是空的_連到同一份清單(tmp_path):
    """找碴 C1（2.006.3）：4.1 有進行中事件、首頁卻綠底寫「待處理 0 項」。有就要列、而且連到 4.1 進行中頁籤。"""
    import incident

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        incident.ensure_tables(conn)
        incident.create_event(conn, "電力", "測試事件", None, None, "tester", mode="formal")
        conn.commit()
        todo = {t["key"]: t for t in v.home(conn)["todo"]}
        assert todo["incident_open"]["n"] == 1 and todo["incident_open"]["link"] == "/v2/incident?tab=open&mode=formal"
        assert all(t["link"].startswith("/v2/") for t in todo.values()), "首頁待處理連到舊頁"
    finally:
        conn.close()


def test_每日異動_資料來源沒更新_不可以當成0異動(tmp_path):
    """找碴 C16（2.006.6）：快照照常每天跑，但掃描／收集／匯入都沒有新資料時，「0 異動」只代表沒有新資料。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        ch = {"date": None, "prev_date": None}
        assert v.changes_freshness(conn, ch)["reason"].startswith("無資料")
        from datetime import date
        ch = {"date": "2000-01-02", "prev_date": "2000-01-01"}
        assert "今日未收集" in v.changes_freshness(conn, ch)["reason"]
        ch = {"date": date.today().isoformat(), "prev_date": None}
        assert v.changes_freshness(conn, ch)["reason"] is None
    finally:
        conn.close()


def test_本週異動_沒收集的日子是無資料不是0(tmp_path):
    """找碴 C25（2026-10-28）：1.1「本週異動」每天的數字＝1.2 每日異動同一份；沒快照的日子寫無資料。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        w = v.week_changes(conn)
        assert len(w["rows"]) == 7 and all(r["n"] is None and r["status"].startswith("無資料") for r in w["rows"])
        assert w["freshness"]["reason"].startswith("無資料")
    finally:
        conn.close()


def _seed_risks(conn):
    """逾期未重開機、硬體異常、容量預警各 1 筆（審查 M2：原本單元測試沒有這三種資料，首頁漏列也照樣綠）。"""
    import json
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    spec = {"uptime": "up 3 years, 2 weeks", "disks": [{"disk": "sda", "model": "X", "health": "FAILED"}]}
    conn.execute("INSERT INTO host_spec (asset_serial, ip, platform, spec_json, collected_at) VALUES (?,?,?,?,?)",
                 ("A-1", "10.99.1.18", "aix", json.dumps(spec), now))
    conn.execute("INSERT INTO source_record (source, source_key, payload, collected_at) VALUES (?,?,?,?)",
                 ("vcenter_extra:vDatastore", "ds1", json.dumps({"Name": "DS-1", "Capacity MiB": 1000, "In Use MiB": 950,
                                                                 "Free MiB": 50, "URL": "ds:///vmfs/volumes/x1/"}), now))
    conn.commit()


def test_首頁待處理_有資料就列_數字等於下鑽(tmp_path):
    import verify_numbers as vn

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _seed_risks(conn)
        todo = {t["key"]: t for t in v.home(conn)["todo"]}
        assert todo["uptime"]["n"] == 1 and todo["hardware"]["n"] == 1 and todo["storage_alert"]["n"] == 1, todo
        assert todo["contract"]["n"] is None and "無資料" in todo["contract"]["label"]          # 沒匯入保固＝無資料，不是 0
        keys = [t["key"] for t in v.home(conn)["todo"]]
        assert len(keys) == len(set(keys))
        vn.RESULTS.clear()
        vn.check_v2(conn)
        bad = [r for r in vn.RESULTS if r["page"] == "2.x 首頁" and not r["ok"]]
        assert not bad, bad
    finally:
        conn.close()


def test_首頁待處理_來源沒有資料不能綠(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        todo = {t["key"]: t for t in v.home(conn)["todo"]}
        for k in ("uptime", "hardware", "contract", "storage_alert"):
            assert todo[k]["n"] is None and "無資料" in todo[k]["label"], k
    finally:
        conn.close()


def test_首頁容量預警_只有實體主機資料也要列(tmp_path):
    """審查 N1：沒匯入 RVTools、但收集到實體主機檔案系統快滿——預警要列（不可以寫成無資料蓋掉），datastore 那半邊另列無資料。"""
    import json
    import verify_numbers as vn

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        st = {"by": "mount", "items": [{"name": "/", "size_gb": 100, "free_gb": 3}], "total_gb": 100}
        conn.execute("INSERT INTO host_spec (asset_serial, ip, platform, storage_json, collected_at) VALUES "
                     "('A-1','10.99.1.18','linux',?,'2026-10-28 01:00:00')", (json.dumps(st),))
        conn.commit()
        todo = {t["key"]: t for t in v.home(conn)["todo"]}
        assert todo["storage_alert"]["n"] == 1, todo.get("storage_alert")
        assert todo["storage_ds_nodata"]["n"] is None
        vn.RESULTS.clear()
        vn.check_v2(conn)
        bad = [r for r in vn.RESULTS if r["page"] == "2.x 首頁" and not r["ok"]]
        assert not bad, bad
    finally:
        conn.close()
