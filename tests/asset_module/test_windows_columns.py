"""Windows 主機用 Windows 的欄位（2026-10-21 使用者：「Windows 沒有 sudo，也沒有 shell」「服務也是同樣概念」）。

釘住：
- 帳號：SID、全部本機群組隸屬、種類（使用者／群組／內建）、來源（本機／網域）；網域群組掛在非特權本機群組
  也要列、但不算本機管理員；sudo→本機管理員、UID 0／金鑰→不適用（合規表與匯出同字）。
- 服務：路徑／說明／延遲啟動／公司名解析；未加引號路徑、非微軟、執行帳號種類、監聽埠（PID→服務）、IIS 站台。
- 排程：非管理員 Get-ScheduledTask 被拒時有 COM 退路，且 ERR 仍保留（畫面講得出原因）。
- 收集全部：進度中已收完的每一樣帶 job＋message（✕ 一定有原因，畫面據 job 重抓分頁）。
全部是假資料（221 是 Linux，Windows 真機未驗）。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import account_collector  # noqa: E402
import account_inventory  # noqa: E402
import account_rules  # noqa: E402
import db  # noqa: E402
import schedule_inventory  # noqa: E402
import service_collector  # noqa: E402
import service_inventory  # noqa: E402
import winrm_collector  # noqa: E402

FRONT = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"

ACCT_OUT = "\n".join([
    "SRC=localaccounts", "COMPUTER=WINSRV01", "DOMAIN=corp.example", "PARTOFDOMAIN=1",
    "USER\tAdministrator\t1\t2026-10-01 09:00:00\t2026-01-01 10:00:00\t\t\t1\t500\tBuilt-in admin\tsamr\tS-1-5-21-1-2-3-500",
    "USER\tGuest\t0\t\t\t\t\t0\t501\tBuilt-in guest\tsamr\tS-1-5-21-1-2-3-501",
    "USER\tbob\t1\t2026-10-20 08:00:00\t2026-09-01 10:00:00\t2026-11-30 10:00:00\t\t1\t1001\tOps Bob\tsamr\tS-1-5-21-1-2-3-1001",
    "MEMBER\tS-1-5-32-544\tAdministrators\tWINSRV01\\Administrator\tUser\tLocal\tS-1-5-21-1-2-3-500",
    "MEMBER\tS-1-5-32-544\tAdministrators\tCORP\\Domain Admins\tGroup\tActiveDirectory\tS-1-5-21-9-9-9-512",
    "MEMBER\tS-1-5-32-555\tRemote Desktop Users\tWINSRV01\\bob\tUser\tLocal\tS-1-5-21-1-2-3-1001",
    "MEMBER\tS-1-5-32-545\tUsers\tWINSRV01\\bob\tUser\tLocal\tS-1-5-21-1-2-3-1001\tother",
    "MEMBER\tS-1-5-32-545\tUsers\tCORP\\Domain Users\tGroup\tActiveDirectory\tS-1-5-21-9-9-9-513\tother",
    "MEMBER\tS-1-5-32-545\tUsers\tNT AUTHORITY\\INTERACTIVE\tGroup\tUnknown\tS-1-5-4\tother",
    "NOTE\tlocalgroups\t2",
])


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _by(res):
    return {a["username"]: a for a in res["accounts"]}


def test_帳號_Windows欄位解析():
    res = account_collector.parse_windows_accounts(ACCT_OUT)
    a = _by(res)
    adm, bob, guest = a["Administrator"], a["bob"], a["Guest"]
    assert adm["sid"] == "S-1-5-21-1-2-3-500" and adm["principal_class"] == "builtin"
    assert adm["principal_source"] == "local" and adm["is_sudoer"] is True
    assert guest["principal_class"] == "builtin" and guest["can_login"] is False
    # bob：特權群組只有 RDP（不是本機管理員），本機群組列全部（含非特權的 Users）
    assert bob["is_sudoer"] is False and bob["priv_groups"] == "Remote Desktop Users"
    assert bob["local_groups"].split(",") == ["Remote Desktop Users", "Users"]
    assert bob["principal_class"] == "user"
    # 網域群組：在 Administrators 的是本機管理員；只在 Users 的也列，但不是特權
    da, du = a["CORP\\Domain Admins"], a["CORP\\Domain Users"]
    assert da["principal_source"] == "domain" and da["principal_class"] == "group" and da["is_sudoer"] is True
    assert da["local_groups"] == "Administrators" and da["sid"].endswith("-512")
    assert du["is_sudoer"] is False and du["priv_groups"] is None and du["local_groups"] == "Users"
    assert "NT AUTHORITY\\INTERACTIVE" not in a          # 內建主體不是帳號
    assert res["local_groups_failed"] == 2


def test_帳號_舊腳本輸出也吃得下():
    old = ACCT_OUT.replace("\tsamr\tS-1-5-21-1-2-3-1001", "\tsamr").replace("\tother", "")
    a = _by(account_collector.parse_windows_accounts(old))
    assert a["bob"]["sid"] is None
    # 沒有 other 標記＝全部當特權群組成員（舊行為），不會壞
    assert "Users" in a["bob"]["local_groups"]


def test_帳號_寫入後列得出Windows欄():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            res = account_collector.parse_windows_accounts(ACCT_OUT)
            account_inventory.upsert_accounts(conn, "10.99.0.5", None, res["accounts"], "winrm",
                                              os_info={"family": "windows", "id": "windows"})
            rows = {r["username"]: r for r in account_inventory.list_accounts(conn, ip="10.99.0.5")}
            assert rows["bob"]["local_groups"] == "Remote Desktop Users,Users"
            assert rows["Administrator"]["sid"] == "S-1-5-21-1-2-3-500"
            assert rows["CORP\\Domain Admins"]["principal_class"] == "group"
            # 帳號→主機下鑽帶 os_family，前端才標得出「本機管理員」
            h = account_inventory.hosts_with_account(conn, "Administrator")
            assert h and h[0]["os_family"] == "windows"
        finally:
            conn.close()


def test_合規表_sudo對應本機管理員_UID金鑰不適用():
    a = _by(account_collector.parse_windows_accounts(ACCT_OUT))
    cols = account_inventory.MATRIX_EXPORT_COLS
    assert cols["sudo"][1](a["Administrator"]) == "本機管理員"
    assert cols["sudo"][1](a["bob"]) == "否"
    assert cols["uid0"][1](a["bob"]) == "不適用"
    assert cols["keys"][1](a["bob"]) == "不適用"
    assert cols["disabled"][1](a["Guest"]) == "已停用"
    # Linux 不受影響
    lin = {"uid": 0, "username": "root", "is_sudoer": 1, "authorized_keys": None}
    assert cols["sudo"][1](lin) == "UID 0" and cols["keys"][1](lin) == "需 root"


def test_合規規則_Windows不拿shell判():
    a = _by(account_collector.parse_windows_accounts(ACCT_OUT))
    th = {"acct_pw_max_days": 90, "acct_idle_days": 90}
    by_rule = {(f["username"], f["rule_id"]): f for f in account_rules.evaluate(list(a.values()), th)}
    for (u, rid), f in by_rule.items():
        assert "shell" not in (f.get("detail") or ""), (u, rid, f)
    assert ("bob", "A2") not in by_rule or by_rule[("bob", "A2")]["verdict"] != "fail"


def test_前端_帳號與服務用Windows欄位():
    v = (FRONT / "pages" / "assets" / "[serial].vue").read_text(encoding="utf-8")
    for col in ("來源", "種類", "啟用", "本機群組", "密碼最後變更", "密碼永不過期", "本機管理員 ✓", ">SID<"):
        assert col in v, col
    for col in ("啟動類型", "執行檔路徑", "監聽埠", "IIS 站台", "非微軟", "未加引號", "顯示全部"):
        assert col in v, col
    # 過時的「Windows 的 OS 重建資料還在做」不可以再出現（v1.479.0 起 Windows 有重建資料）
    assert "OS 重建資料還在做" not in v
    m = (FRONT / "pages" / "account-matrix.vue").read_text(encoding="utf-8")
    assert "'本機管理員'" in m and "isWin(a)" in m


SVC_OUT = "\n".join([
    "SRC=winrm",
    "SVC\tAcmeAgent\tAcme Agent\tRunning\tAuto\tCORP\\svc_acme\t4321\tC:\\Program Files\\Acme Corp\\agent.exe -run\t1\tAcme monitoring\tAcme Ltd",
    "SVC\tWinRM\tWindows Remote Management\tRunning\tAuto\tNT AUTHORITY\\NetworkService\t900\tC:\\Windows\\System32\\svchost.exe -k NetworkService\t0\tWS-Man\tMicrosoft Corporation",
    "SVC\tFax\tFax\tStopped\tManual\tNT AUTHORITY\\NetworkService\t0\tC:\\Windows\\system32\\fxssvc.exe\t0\tFax svc\tMicrosoft Corporation",
    "SVC\tW3SVC\tWorld Wide Web Publishing Service\tRunning\tAuto\tLocalSystem\t1200\tC:\\Windows\\system32\\svchost.exe -k iissvcs\t0\tIIS\tMicrosoft Corporation",
    "LISTEN 0 0 0.0.0.0:5985 *:* users:((\"svchost (WinRM)\"))",
    "LISTEN 0 0 0.0.0.0:8443 *:* users:((\"agent (AcmeAgent)\"))",
    "LISTEN 0 0 0.0.0.0:80 *:* users:((\"svchost (W3SVC,WAS)\"))",
])


def test_服務_Windows欄位解析與衍生():
    got = service_collector.parse_windows_services(SVC_OUT)
    w = {s["name"]: s for s in got["winservices"]}
    assert w["AcmeAgent"]["delayed"] == 1 and w["AcmeAgent"]["vendor"] == "Acme Ltd"
    assert w["AcmeAgent"]["path_name"].startswith("C:\\Program Files\\Acme Corp")
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            ip = "10.99.0.6"
            service_inventory.upsert_winservices(conn, ip, None, got["winservices"])
            for s in got["ports"]:
                conn.execute("INSERT INTO host_service (ip, proto, port, bind_addr, process, source) "
                             "VALUES (?,?,?,?,?,?)", (ip, "tcp", s["port"], s["bind"], s["process"], "winrm"))
            conn.execute("INSERT INTO host_iis (ip, state, payload) VALUES (?,?,?)",
                         (ip, "ok", json.dumps({"sites": [{"name": "Default Web Site"}], "pools": []})))
            conn.commit()
            rows = {r["name"]: r for r in service_inventory.list_winservices(conn, ip=ip)}
        finally:
            conn.close()
    acme, winrm, fax, w3 = rows["AcmeAgent"], rows["WinRM"], rows["Fax"], rows["W3SVC"]
    assert acme["unquoted_path"] is True and acme["is_microsoft"] is False
    assert acme["account_kind"] == "domain" and acme["ports"] == ["8443/tcp"]
    assert winrm["is_microsoft"] is True and winrm["ports"] == ["5985/tcp"]
    assert winrm["unquoted_path"] is False and winrm["account_kind"] == "system"
    assert fax["ports"] == [] and fax["state"] == "stopped"
    assert w3["iis_sites"] == ["Default Web Site"] and w3["ports"] == ["80/tcp"]


def test_服務_舊腳本輸出_新欄是不知道():
    old = "SVC\tX\tX svc\tRunning\tAuto\tLocalSystem\t10"
    s = service_collector.parse_windows_services(old)["winservices"][0]
    assert s["path_name"] is None and s["delayed"] is None and s["vendor"] is None
    assert service_inventory.is_microsoft(None, None) is None


def test_收集腳本_帶出新欄且只讀():
    ps = winrm_collector.SERVICES_PS
    assert "DelayedAutoStart" in ps and "PathName" in ps and "CompanyName" in ps
    assert "Get-LocalGroup " in winrm_collector.ACCOUNTS_PS or "Get-LocalGroup -" in winrm_collector.ACCOUNTS_PS
    assert "$u.SID" in winrm_collector.ACCOUNTS_PS
    assert "Schedule.Service" in schedule_inventory.SCHEDULE_PS


def test_排程_COM退路的輸出照樣解析():
    out = "\n".join([
        "SRC=schtasks",
        "ERR\ttasks\tDENIED\tAccess is denied. 0x80070005",
        "TASK\t\\Acme\\Nightly\t\\Acme\\\tNightly\tReady\tCORP\\svc_acme\t1\t0\tAcme\t1899-12-30 00:00:00\t267011\t2026-10-22 02:00:00\tnightly job",
        "ACT\t\\Acme\\Nightly\tC:\\Acme\\run.exe\t-q\t\t",
        "TRG\t\\Acme\\Nightly\tMSFT_TaskDailyTrigger\tTrue\t2026-01-01T02:00:00\t1\t\t\t\t\t\t",
        "SRC2=com\t1",
    ])
    p = schedule_inventory.parse_windows_schedule(out)
    assert len(p["tasks"]) == 1 and p["tasks"][0]["last_run"] is None    # 1899-12-30＝從沒跑過
    assert p["errors"] and p["errors"][0]["item"] == "tasks"               # 權限不足仍講得出來
    note = schedule_inventory.explain_win_error(p["errors"][0])
    assert "權限不足" in note


def test_收集全部_進度帶job與原因():
    src = (Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend" / "post_onboard.py").read_text(encoding="utf-8")
    assert '"done": [{"job": r["job"]' in src and '"message": r.get("message")' in src
    v = (FRONT / "pages" / "assets" / "[serial].vue").read_text(encoding="utf-8")
    assert "reloadDoneJobs(r.progress?.done" in v and ':title="d.message' in v
