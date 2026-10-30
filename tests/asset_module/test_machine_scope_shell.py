"""同一台兩筆登記（掃描空殼 DYN- ＋ CIA 正本 HW-）：收集只收一次、上下數字一致、
掃描證據掛正本、空殼可預覽併回並還原（2026-10-16 公司機實例，IP 換成 10.99.x）。

實例：DYN-00:50:56:ba:fe:76 與 HW-00012109，主機名 secsvr004-012t、同一個 IP。
修之前公司機看到：上方「收到」軟體 1054／服務 34／帳號 48（＝527／17／24 的兩倍），
空殼頁服務 0／帳號 0，可信度空殼 80、正本 50，vCenter 資訊掛在空殼。
"""
import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_collector  # noqa: E402
import account_inventory  # noqa: E402
import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import dyn_shell  # noqa: E402
import dynassets_import  # noqa: E402
import service_collector  # noqa: E402
import service_inventory  # noqa: E402
import software_collector  # noqa: E402
import software_inventory  # noqa: E402
import trust_score  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"
IP = "10.99.4.12"
HOST = "secsvr004-012t"
MAC = "00:50:56:ba:fe:76"
SHELL = f"DYN-{MAC}"
CIA = "HW-00012109"
N_PKG, N_SVC, N_ACC = 527, 17, 24


def _seed(conn, shell_mac=MAC):
    """實例：CIA 正本（業務欄位齊全、沒有 MAC）＋掃描空殼（只有機器事實）。"""
    db.insert_hardware(conn, asset_serial=CIA, hostname=HOST, ip=IP, asset_name="證券阿發",
                       asset_purpose="阿發 AP 測試智能文字客服(新機 RHEL)", api_id="N-113",
                       environment="測試", asset_status="使用中", is_vm=1,
                       os="Red Hat Enterprise Linux 8.10", collect_ok=1)
    db.insert_hardware(conn, asset_serial=SHELL, hostname=HOST, ip=IP, asset_name="證券阿發",
                       mac=shell_mac, is_vm=1, os="RedHat 8.10", collect_ok=1,
                       remark="來源=dynassets存活掃描")


def _hid(conn, serial):
    return conn.execute("SELECT id FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()[0]


def _evidence_on_shell(conn):
    """掃描與 vCenter 的來源紀錄都掛在空殼上（公司機現況）。"""
    sid = _hid(conn, SHELL)
    conn.execute("INSERT INTO source_record (source, source_key, payload, resolved_status, "
                 "resolved_hardware_id) VALUES ('dynassets', ?, ?, 'matched', ?)",
                 (IP, f'{{"ip": "{IP}", "hostname": "{HOST}", "mac": "{MAC}", '
                      f'"os": "RedHat 8.10"}}', sid))
    conn.execute("INSERT INTO source_record (source, source_key, payload, resolved_status, "
                 "resolved_hardware_id) VALUES ('vcenter', 'vm-1', '{}', 'matched', ?)", (sid,))
    conn.commit()


def _fake_collectors(monkeypatch, calls):
    pkgs = [{"name": f"pkg{i:03d}", "version": "1.0", "arch": "x86_64"} for i in range(N_PKG)]
    svcs = [{"proto": "tcp", "port": 1000 + i, "bind": "0.0.0.0", "exposure": "all"}
            for i in range(N_SVC)]
    accs = [{"username": f"user{i:02d}", "uid": 1000 + i, "kind": "human"} for i in range(N_ACC)]

    def sw(run, ip, platform):
        calls.append(("sw", ip))
        return {"source": "rpm", "packages": pkgs}

    def sv(run, ip, platform):
        calls.append(("svc", ip))
        return {"services": svcs, "process_visible": True}

    def ac(run, ip, platform):
        calls.append(("acc", ip))
        return {"accounts": accs, "needs_root": False, "os": {"id": "rhel", "version": "8.10"}}

    monkeypatch.setattr(software_collector, "collect", sw)
    monkeypatch.setattr(service_collector, "collect", sv)
    monkeypatch.setattr(account_collector, "collect", ac)


def _runner(host, cmd):
    return ""


def _collect_from(conn, serial):
    return (software_inventory.collect_software(conn, runner=_runner, only_serial=serial),
            service_inventory.collect_services(conn, runner=_runner, only_serial=serial),
            account_inventory.collect_accounts(conn, runner=_runner, only_serial=serial))


def test_同一台兩筆登記_收到N筆不重複計_同一個IP只收一次(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            _seed(conn)
            calls: list = []
            _fake_collectors(monkeypatch, calls)
            sw, sv, ac = _collect_from(conn, SHELL)
            # 修之前是 1054／34／48（N × 登記筆數）
            assert sw["packages"] == N_PKG
            assert sv["services"] == N_SVC
            assert ac["accounts"] == N_ACC
            assert sorted(c[0] for c in calls) == ["acc", "svc", "sw"]   # 一類只 SSH 一次
            # 在空殼頁按的收集，資料就掛空殼（診斷紀錄落在使用者按的那一頁）
            assert {r[0] for r in conn.execute("SELECT DISTINCT asset_serial FROM host_service")} == {SHELL}
        finally:
            conn.close()


def _db(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return p


def _client(tmp):
    db_path = _db(tmp)

    def _override():
        c = db.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    client = TestClient(api.app)
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password(_PW))
    finally:
        conn.close()
    assert client.post("/api/auth/login",
                       json={"username": "tester", "password": _PW}).status_code == 200
    return client, db_path


def test_兩筆登記的明細頁_上方與分頁數字一致而且兩頁一樣(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            conn = db.get_connection(db_path)
            try:
                _seed(conn)
                calls: list = []
                _fake_collectors(monkeypatch, calls)
                _collect_from(conn, CIA)          # 資料掛正本（空殼頁以前就是看不到這些）
            finally:
                conn.close()
            for serial in (SHELL, CIA):
                d = client.get(f"/api/assets/{serial}").json()
                cs = d["collect_status"]
                svc = client.get("/api/services", params={"asset_serial": serial}).json()["items"]
                acc = client.get("/api/accounts", params={"asset_serial": serial}).json()["items"]
                assert len(d["packages"]) == cs["software"]["count"] == N_PKG, serial
                assert len(svc) == cs["service"]["count"] == N_SVC, serial
                assert len(acc) == cs["account"]["count"] == N_ACC, serial
            # 重複登記看得到：空殼頁標自己是掃描建的，並指出同一台的 CIA 正本
            cs = client.get(f"/api/assets/{SHELL}").json()["collect_status"]
            assert cs["self"]["off_book"] is True and cs["self"]["no_business"] is True
            twin = [r for r in cs["same_ip"] if r["asset_serial"] == CIA][0]
            assert twin["same_machine"] is True and twin["off_book"] is False
        finally:
            api.app.dependency_overrides.clear()


def test_同IP但不同主機名_提示另有一筆但不算同一台():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            conn = db.get_connection(db_path)
            try:
                _seed(conn)
                db.insert_hardware(conn, asset_serial="HW-OLD", hostname="oldbox", ip=IP,
                                   asset_status="使用中")
            finally:
                conn.close()
            same_ip = client.get(f"/api/assets/{CIA}").json()["collect_status"]["same_ip"]
            by = {r["asset_serial"]: r for r in same_ip}
            assert by["HW-OLD"]["same_machine"] is False
            assert by[SHELL]["same_machine"] is True
        finally:
            api.app.dependency_overrides.clear()


def _dyn_csv(tmp, rows):
    p = Path(tmp) / "dyn.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["IP", "Hostname", "MAC", "作業系統"])
        w.writerows(rows)
    return p


def test_掃描匯入_同一台的證據掛到CIA正本_不另建DYN_也不覆蓋CIA欄位():
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            _seed(conn)                     # 空殼已存在（帶 MAC）：以前 MAC 那一關永遠對到它
            r = dynassets_import.import_dynassets(_dyn_csv(tmp, [[IP, HOST, MAC, "RedHat 8.10"]]), conn)
            assert r["inserted"] == 0
            sr = conn.execute("SELECT resolved_hardware_id, resolved_rule FROM source_record "
                              "WHERE source='dynassets'").fetchone()
            assert sr["resolved_hardware_id"] == _hid(conn, CIA)
            assert sr["resolved_rule"] == dyn_shell.CIA_RULE
            hw = conn.execute("SELECT hostname, ip, os, os_cia, mac FROM hardware WHERE asset_serial = ?",
                              (CIA,)).fetchone()
            assert (hw["hostname"], hw["ip"]) == (HOST, IP)          # CIA 身分欄不蓋
            assert hw["os"] == "RedHat 8.10"                          # 決策 Q80：顯示值實測優先
            assert hw["os_cia"] == "Red Hat Enterprise Linux 8.10"   # CIA 原值另存，不消失
            assert hw["mac"] == MAC                                  # CIA 沒有的 MAC 補上
        finally:
            conn.close()


def test_掃描匯入_沒有空殼也對不上就照舊建DYN_只有IP相同不硬併():
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            db.insert_hardware(conn, asset_serial="HW-1", hostname="other", ip="10.99.4.13",
                               asset_status="使用中")
            r = dynassets_import.import_dynassets(_dyn_csv(tmp, [
                ["10.99.4.99", "newbox", "00:50:56:00:00:01", "RedHat 8.10"],   # 完全沒登記
                ["10.99.4.13", "renamed", "00:50:56:00:00:02", "RedHat 8.10"],  # 只有 IP 相同
            ]), conn)
            assert r["inserted"] == 1 and r["pending_review"] == 1
        finally:
            conn.close()


def test_空殼預覽三類_併回後可信度正本高於空殼原分_還原回原狀(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            _seed(conn)
            _evidence_on_shell(conn)
            calls: list = []
            _fake_collectors(monkeypatch, calls)
            service_inventory.collect_services(conn, runner=_runner, only_serial=SHELL)
            # 另外兩類：真的未登記、只有 IP 相同（IP 回收）
            db.insert_hardware(conn, asset_serial="DYN-10.99.4.50", hostname="ghost",
                               ip="10.99.4.50")
            db.insert_hardware(conn, asset_serial="HW-2", hostname="realname", ip="10.99.4.60",
                               asset_status="使用中")
            db.insert_hardware(conn, asset_serial="DYN-10.99.4.60", hostname="othername",
                               ip="10.99.4.60")
            p = dyn_shell.preview(conn)
            kinds = {i["shell_serial"]: i["kind"] for i in p["items"]}
            assert kinds == {SHELL: "mergeable", "DYN-10.99.4.50": "unregistered",
                             "DYN-10.99.4.60": "review"}
            assert p["counts"] == {"mergeable": 1, "review": 1, "unregistered": 1}

            before = trust_score.compute_all(conn)
            assert before[SHELL]["score"] > before[CIA]["score"]     # 公司機現況：80 vs 50

            # 畫面傳錯對象 → 後端重新比對後拒絕
            bad = dyn_shell.apply(conn, [{"shell_serial": "DYN-10.99.4.60",
                                          "target_serial": "HW-2"}], "tester")
            assert bad["merged"] == [] and bad["skipped"]

            r = dyn_shell.apply(conn, [{"shell_serial": SHELL, "target_serial": CIA}], "tester")
            assert r["merged"][0]["moved"]["source_record"] == 2
            after = trust_score.compute_all(conn)
            assert SHELL not in after                                # 空殼搬進合併紀錄（2026-10-20）
            assert after[CIA]["score"] > before[SHELL]["score"]      # CIA＋掃描＋vCenter > 只有掃描
            assert after[CIA]["sources"]["dynassets"] and after[CIA]["sources"]["rvtools"]
            # 2026-10-20：不再標停用（資產狀態只有 CIA 可寫）——整列搬進 hardware_merged、記 merged_into
            import lifecycle
            assert conn.execute("SELECT COUNT(*) FROM hardware WHERE asset_serial = ?",
                                (SHELL,)).fetchone()[0] == 0
            assert lifecycle.merged_into_of(conn, SHELL) == CIA
            assert {x[0] for x in conn.execute("SELECT DISTINCT asset_serial FROM host_service")} == {CIA}
            hw = conn.execute("SELECT mac, asset_purpose FROM hardware WHERE asset_serial = ?",
                              (CIA,)).fetchone()
            assert hw["mac"] == MAC and hw["asset_purpose"].startswith("阿發")

            audit = dyn_shell.history(conn)[0]
            dyn_shell.restore(conn, audit["id"], "tester")
            back = trust_score.compute_all(conn)
            assert back[SHELL]["score"] == before[SHELL]["score"]
            assert back[CIA]["score"] == before[CIA]["score"]
            assert conn.execute("SELECT mac FROM hardware WHERE asset_serial = ?",
                                (CIA,)).fetchone()[0] is None
            assert {x[0] for x in conn.execute("SELECT DISTINCT asset_serial FROM host_service")} == {SHELL}
        finally:
            conn.close()


def test_MAC不同不併_IP被回收給別台():
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            _seed(conn)
            conn.execute("UPDATE hardware SET mac = '00:50:56:aa:aa:aa' WHERE asset_serial = ?", (CIA,))
            conn.commit()
            item = dyn_shell.preview(conn)["items"][0]
            assert item["kind"] == "review" and "MAC" in item["reason"]
        finally:
            conn.close()


def test_實測與登記並列_作業系統不符要標出來():
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            _seed(conn)
            _evidence_on_shell(conn)
            hw = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (CIA,)).fetchone()
            obs = dyn_shell.observed_facts(conn, [CIA, SHELL], hw)
            f = {x["key"]: x for x in obs["fields"]}
            assert f["os"]["observed"] == "RedHat 8.10" and f["os"]["mismatch"] is False
            assert f["hostname"]["mismatch"] is False
            conn.execute("UPDATE hardware SET os = 'Windows Server 2019' WHERE asset_serial = ?", (CIA,))
            hw = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (CIA,)).fetchone()
            f = {x["key"]: x for x in dyn_shell.observed_facts(conn, [CIA, SHELL], hw)["fields"]}
            assert f["os"]["mismatch"] is True
        finally:
            conn.close()


def test_空殼端點要登入():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _db(tmp)

        def _override():
            c = db.get_connection(db_path)
            try:
                yield c
            finally:
                c.close()

        api.app.dependency_overrides[api.get_db] = _override
        try:
            c = TestClient(api.app)
            assert c.get("/api/scan-shells").status_code == 401
            assert c.post("/api/scan-shells/apply", json={"pairs": []}).status_code == 401
            assert c.post("/api/scan-shells/restore/1").status_code == 401
        finally:
            api.app.dependency_overrides.clear()


def test_併回只有使用者確認才會跑_部署啟動開頁都不會自動觸發():
    """清理既有空殼＝先給清單、點頭才執行（使用者 2026-10-16）。
    dyn_shell.apply／restore 只能由 /api/scan-shells/apply、/restore 端點呼叫；
    deploy.sh、啟動、匯入、預覽（GET）都不准碰。

    2026-10-18 決策 DYN2 放寬一條：**證據確定是同一台的**由 `dyn_shell.py auto-merge`
    （deploy.sh／patch.sh）自動併，規則全在 dyn_shell.auto_check（見 test_dyn_auto_merge.py）。
    auto_merge 本身不准被任何後端模組直接呼叫（啟動、匯入、GET 都不行）。"""
    import re

    app_dir = ROOT / "APP" / "asset-module"
    callers = []
    for f in list((app_dir / "backend").rglob("*.py")) + list(app_dir.rglob("*.sh")):
        if "node_modules" in f.parts or f.name == "dyn_shell.py":
            continue
        txt = f.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"dyn_shell\.(apply|restore)\s*\(", txt):
            callers.append(f.relative_to(app_dir).as_posix())
    assert callers == ["backend/routers/config_audit.py"], callers
    auto_callers = [f.relative_to(app_dir).as_posix() for f in (app_dir / "backend").rglob("*.py")
                    if f.name != "dyn_shell.py"
                    and re.search(r"dyn_shell\.auto_merge\s*\(", f.read_text(encoding="utf-8", errors="ignore"))]
    assert auto_callers == [], auto_callers
    src = (app_dir / "backend" / "routers" / "config_audit.py").read_text(encoding="utf-8")
    get_block = src.split('@router.get("/api/scan-shells")')[1].split("@router.")[0]
    assert "apply(" not in get_block and "restore(" not in get_block


# ---- 決策 Q80（2026-10-16）：實測聽掃描、業務聽 CIA、掃描不寫 CIA 欄位 ----

def test_Q80_掃描直接對到CIA正本_也不寫主機名IP():
    """identity 本來就對到 CIA 正本（不是從空殼改掛）時也一樣不寫身分欄。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            db.insert_hardware(conn, asset_serial=CIA, hostname=HOST, ip=IP, os="Windows Server 2019",
                               mac=MAC, environment="測試", asset_status="使用中", api_id="N-113")
            conn.commit()
            dynassets_import.import_dynassets(
                _dyn_csv(tmp, [[IP, HOST.upper() + ".corp.example.com", MAC, "Windows Server 2022"]]), conn)
            hw = conn.execute("SELECT hostname, ip, os, os_cia FROM hardware WHERE asset_serial = ?",
                              (CIA,)).fetchone()
            assert hw["hostname"] == HOST and hw["ip"] == IP
            assert hw["os"] == "Windows Server 2022" and hw["os_cia"] == "Windows Server 2019"
        finally:
            conn.close()


def test_Q80_CIA重匯_有實測就不蓋顯示OS_只更新os_cia_沒實測照舊():
    import excel_import

    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            db.insert_hardware(conn, asset_serial="HW-M", hostname="m1", ip="10.99.7.1", os="RHEL 9.4",
                               collect_ok=1)
            db.insert_hardware(conn, asset_serial="HW-U", hostname="u1", ip="10.99.7.2", os="RHEL 7")
            conn.commit()
            m = excel_import._cia_os_split(conn, {"asset_serial": "HW-M", "os": "RHEL 8"})
            u = excel_import._cia_os_split(conn, {"asset_serial": "HW-U", "os": "RHEL 8"})
            n = excel_import._cia_os_split(conn, {"asset_serial": "HW-NEW", "os": "AIX 7.2"})
            assert "os" not in m and m["os_cia"] == "RHEL 8"          # 有實測：顯示值不被 CIA 蓋回
            assert u["os"] == "RHEL 8" and u["os_cia"] == "RHEL 8"    # 沒實測：跟以前一樣
            assert n["os"] == "AIX 7.2" and n["os_cia"] == "AIX 7.2"  # 新列
        finally:
            conn.close()


def test_Q80_登記與實測不符_用CIA原值比():
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            _seed(conn)
            _evidence_on_shell(conn)
            conn.execute("UPDATE hardware SET os = 'RedHat 8.10', os_cia = 'Windows Server 2019' "
                         "WHERE asset_serial = ?", (CIA,))
            hw = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (CIA,)).fetchone()
            f = {x["key"]: x for x in dyn_shell.observed_facts(conn, [CIA, SHELL], hw)["fields"]}
            assert f["os"]["registered"] == "Windows Server 2019" and f["os"]["mismatch"] is True
        finally:
            conn.close()


def test_Q80補洞_SSH收集不蓋CIA主機名_另存並標不符():
    """manage_state 的 SSH／WinRM 收集（FACT_FIELDS 含 hostname）寫 CIA 正本前一律過 cia_guard_collected。"""
    src = (ROOT / "APP" / "asset-module" / "backend" / "manage_state.py").read_text(encoding="utf-8")
    assert src.count("dyn_shell.cia_guard_collected(") == 2          # Windows 與 SSH 兩條寫入路徑
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.get_connection(_db(tmp))
        try:
            db.insert_hardware(conn, asset_serial=CIA, hostname=HOST, ip=IP, os="RHEL 8", asset_status="使用中")
            db.insert_hardware(conn, asset_serial=SHELL, hostname=HOST, ip=IP)
            conn.commit()
            got = dyn_shell.cia_guard_collected(conn, CIA, {"hostname": "real-name", "os": "RHEL 9", "mac": MAC})
            assert "hostname" not in got and got["hostname_observed"] == "real-name"
            assert got["os"] == "RHEL 9" and got["os_cia"] == "RHEL 8" and got["mac"] == MAC
            assert dyn_shell.cia_guard_collected(conn, SHELL, {"hostname": "x"}) == {"hostname": "x"}  # 帳外原樣
            conn.execute("UPDATE hardware SET hostname_observed = 'real-name' WHERE asset_serial = ?", (CIA,))
            hw = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (CIA,)).fetchone()
            f = {x["key"]: x for x in dyn_shell.observed_facts(conn, [CIA], hw)["fields"]}
            assert f["hostname"]["observed"] == "real-name" and f["hostname"]["mismatch"] is True
        finally:
            conn.close()
