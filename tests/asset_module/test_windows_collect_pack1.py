"""Windows 收集包 1（2026-10-20）：WinRM 納管的 Windows 主機，納管後六樣不再是「軟體 0 服務 0 帳號 0」。

公司機實測：Windows Server 2022、WinRM（PSRP、Microsoft.PowerShell 端點）、收集帳號 webit3sc
是 Remote Management Users 成員（非管理員）。這裡用「錄下來的／照格式造的」PSRP 輸出驗：

1. 根因：WinRM 收到的 facts 沒寫回資產、os 空、掃描沒有 3389 → 判成 linux → 拿 SSH 去收 → 全 0。
2. 軟體：讀登錄檔 Uninstall（不用 Win32_Product），source=winrm_registry。
3. 服務：Win32_Service 清單＋監聽埠；非管理員拿不到 → 退回登錄檔、講「權限不足（需 X）」。
4. 帳號：本機帳號＋本機特權群組成員（網域成員標 domain），進同一張 host_account、同一套規則。
5. 每一項拿不到都講「權限不足（需 X）」，不靜默變 0。

真機驗證：**還沒有**——221 與家中沒有 Windows；要等使用者在公司 Windows 測試機上按「收集全部」。
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_collector  # noqa: E402
import account_inventory  # noqa: E402
import account_rules  # noqa: E402
import credential_store  # noqa: E402
import db  # noqa: E402
import manage_state  # noqa: E402
import post_onboard  # noqa: E402
import service_collector  # noqa: E402
import service_inventory  # noqa: E402
import software_collector  # noqa: E402
import software_inventory  # noqa: E402
import win_fcb  # noqa: E402
import win_rebuild_collector  # noqa: E402
import winrm_collector  # noqa: E402


@pytest.fixture(autouse=True)
def _windows_opted_in(monkeypatch):
    """這支測的是 Windows 收集本身（解析、寫庫、講原因），主機一律當成「已納管」。
    「沒納管就不登入」那道閘另在 test_win_onboard.py 測（2026-10-22 Windows 勾選才收）。"""
    import win_onboard

    monkeypatch.setattr(win_onboard, "collect_block", lambda *a, **k: None)
    monkeypatch.setattr(win_onboard, "is_opted_in", lambda *a, **k: True)

FAKE_PW = "FAKE-TEST-PW-not-real-2022"
IP = "203.0.113.142"
SERIAL = "DYN-WIN-0142"

# ---- 照 PSRP（Out-String -Stream）輸出格式造的樣本；欄位順序與腳本一致 ----
FACTS_OUT = """hostname=WINAP01
os=Microsoft Windows Server 2022 Standard
os_version=10.0.20348
device_model=VMware7,1
vendor=VMware, Inc.
hw_serial=VMware-42 0b aa
mac=00:50:56:AA:BB:CC
is_vm=1
"""

# 非管理員：Win32_Service 被拒 → 退回登錄檔；監聽埠拿得到
SERVICES_OUT_NONADMIN = (
    "SRC=winrm\n"
    "ERR\tservices\tDENIED\tAccess denied \n"
    "SVCREG\tWinRM\tWindows Remote Management (WS-Management)\tAuto\tNT AUTHORITY\\NetworkService\n"
    "SVCREG\tSpooler\t\tAuto\tLocalSystem\n"
    "SVCREG\tMSSQLSERVER\tSQL Server (MSSQLSERVER)\tAuto\tCORP\\svc_sql\n"
    "LISTEN 0 0 0.0.0.0:5985 *:* users:((\"System\"))\n"
    "LISTEN 0 0 0.0.0.0:1433 *:* users:((\"sqlservr\"))\n"
    "LISTEN 0 0 :::135 *:* users:((\"svchost\"))\n"
)
# 管理員或 SCM 有授權：有狀態、PID 對得到服務
SERVICES_OUT_FULL = (
    "SRC=winrm\n"
    "SVC\tWinRM\tWindows Remote Management (WS-Management)\tRunning\tAuto\tNT AUTHORITY\\NetworkService\t1220\n"
    "SVC\tSpooler\tPrint Spooler\tStopped\tDisabled\tLocalSystem\t0\n"
    "LISTEN 0 0 0.0.0.0:5985 *:* users:((\"System\"))\n"
    "LISTEN 0 0 0.0.0.0:47001 *:* users:((\"svchost (WinRM)\"))\n"
)
SERVICES_OUT_ALL_DENIED = (
    "SRC=winrm\n"
    "ERR\tservices\tDENIED\tAccess denied\n"
    "ERR\tservices_registry\tDENIED\tRequested registry access is not allowed.\n"
    "ERR\tports\tDENIED\tAccess denied\n"
)

SOFTWARE_OUT = (
    "SRC=registry\n"
    "PKG\t7-Zip 23.01 (x64)\t23.01\tx64\t20240115\tIgor Pavlov\n"
    "PKG\tMicrosoft SQL Server 2019 (64-bit)\t15.0.2000.5\tx64\t\tMicrosoft Corporation\n"
    "PKG\tNotepad++ (32-bit x86)\t8.6\tx86\t20240301\tNotepad++ Team\n"
)
SOFTWARE_OUT_DENIED = (
    "SRC=registry\n"
    "ERR\tsoftware\tDENIED\tRequested registry access is not allowed.\n"
)

# 中文版 Windows：ObjectClass 是「使用者／群組」（實測本機 Win11 也是中文）
ACCOUNTS_OUT = (
    "SRC=localaccounts\n"
    "COMPUTER=WINAP01\n"
    "DOMAIN=corp.example\n"
    "PARTOFDOMAIN=1\n"
    "USER\tlocaladm\t1\t2026-10-01 09:00:00\t2026-09-01 10:00:00\t\t\t1\t500\tBuilt-in account for administering\tsamr\n"
    "USER\tGuest\t0\t\t\t\t\t0\t501\tBuilt-in account for guest access\tsamr\n"
    "USER\tDefaultAccount\t0\t\t\t\t\t0\t503\t\tsamr\n"
    "USER\twebit3sc\t1\t2026-10-20 18:00:00\t2026-10-19 08:00:00\t\t\t1\t1001\t\tsamr\n"
    "USER\talice\t1\t\t2025-01-01 08:00:00\t2025-03-01 08:00:00\t\t1\t1002\t\tsamr\n"
    "MEMBER\tS-1-5-32-544\tAdministrators\tWINAP01\\localadm\t使用者\tLocal\tS-1-5-21-1-2-3-500\n"
    "MEMBER\tS-1-5-32-544\tAdministrators\tCORP\\Domain Admins\t群組\tActiveDirectory\tS-1-5-21-9-9-9-512\n"
    "MEMBER\tS-1-5-32-544\tAdministrators\tCORP\\bob\t使用者\tActiveDirectory\tS-1-5-21-9-9-9-1105\n"
    "MEMBER\tS-1-5-32-580\tRemote Management Users\tWINAP01\\webit3sc\t使用者\tLocal\tS-1-5-21-1-2-3-1001\n"
    "MEMBER\tS-1-5-32-555\tRemote Desktop Users\tNT AUTHORITY\\INTERACTIVE\t群組\tUnknown\tS-1-5-4\n"
    "ERR\tmembers:Backup Operators\tDENIED\tAccess is denied.\n"
)
#: 組態檢核（公司基準 286 條，2026-10-21）：收集帳號是本機管理員時，包裝層回去識別化的真機 TXT
_FCB_TXT = (ROOT / "tests" / "asset_module" / "fixtures" / "config_audit" /
            "fcb_windows_sample_sanitized.txt").read_text(encoding="utf-8-sig")
AUDIT_OUT = ("__W3CFG_RUNAS__\tWINAP01\\webit3adm\t1\n__W3CFG_BEGIN__\n" + _FCB_TXT
             + "\n__W3CFG_END__\n")
#: 不是本機管理員：包裝層只回身分，整段不跑
AUDIT_OUT_NOT_ADMIN = "__W3CFG_RUNAS__\tWINAP01\\webit3sc\t0\n"

ACCOUNTS_OUT_DENIED = (
    "SRC=localaccounts\nCOMPUTER=WINAP01\nDOMAIN=corp.example\nPARTOFDOMAIN=1\n"
    "ERR\tusers\tDENIED\tAccess denied\n"
    "ERR\tusers_cim\tDENIED\tAccess denied\n"
)


class _TempKey:
    """憑證加密金鑰導到暫存檔（同 test_service_windows）。"""

    def __init__(self, tmp):
        self.kp = str(Path(tmp) / "cred.key")
        self._orig = None

    def __enter__(self):
        self._orig = credential_store.get_for_use
        orig, kp = self._orig, self.kp
        credential_store.get_for_use = lambda c, n, key_path=kp: orig(c, n, key_path=key_path)
        return self

    def __exit__(self, *exc):
        credential_store.get_for_use = self._orig
        return False

    def save(self, conn, **kw):
        return credential_store.save(conn, key_path=self.kp, **kw)


@pytest.fixture
def env(monkeypatch):
    """一台「2-1 開始收集走 WinRM 收成功」的 Windows：os 空、掃描指紋沒有 3389、有 winrm 憑證。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        db.insert_hardware(conn, asset_serial=SERIAL, hostname=None, ip=IP, collect_ok=1)
        conn.execute("INSERT INTO scan_history (ip, scan_ok, open_ports, scan_time) "
                     "VALUES (?, 1, '445,5985', '2026-10-20 10:00:00')", (IP,))
        conn.execute("INSERT INTO collect_dispatch_run (trigger, status, started_at) "
                     "VALUES ('manual','ok','2026-10-20 10:00:00')")
        conn.execute("INSERT INTO collect_dispatch_result (run_id, ip, alive, open_ports, route, "
                     "status, asset_serial) VALUES (1, ?, 1, '445,5985', 'winrm', 'collected', ?)",
                     (IP, SERIAL))
        conn.commit()
        # 沒有 22：uname 實問一定問不到（不要真的去 SSH）
        monkeypatch.setattr(manage_state, "probe_uname", lambda *a, **k: None)
        with _TempKey(tmp) as tk:
            tk.save(conn, name="win-collect", kind="winrm", username="webit3sc", password=FAKE_PW)
            outputs = {"facts": FACTS_OUT, "services": SERVICES_OUT_NONADMIN,
                       "software": SOFTWARE_OUT, "accounts": ACCOUNTS_OUT,
                       "audit": AUDIT_OUT,
                       "rebuild": "@@SEC os\nR\tcaption\tMicrosoft Windows Server 2022\n@@END"}
            calls = []

            def fake_psrp(host, username, password, script, timeout, deadline_s=None):
                assert password == FAKE_PW            # 憑證庫的密碼真的被拿去用
                if script == winrm_collector.FACTS_PS:
                    key = "facts"
                elif script == winrm_collector.SERVICES_PS:
                    key = "services"
                elif script == software_collector.WINDOWS_PS:
                    key = "software"
                elif script == winrm_collector.ACCOUNTS_PS:
                    key = "accounts"
                elif win_fcb.MARK_RUNAS in script:
                    assert deadline_s == win_fcb.DEADLINE_S   # 組態檢核有自己的總時限
                    key = "audit"
                elif script == win_rebuild_collector.REBUILD_PS:
                    key = "rebuild"
                else:
                    raise AssertionError("送了不認得的腳本")
                calls.append((host, key))
                return outputs[key]

            monkeypatch.setattr(winrm_collector, "_psrp_run", fake_psrp)
            yield conn, outputs, calls
        conn.close()


# ---------------- 1. 根因 ----------------

def test_根因_os空且沒有3389_但上次走WinRM收成功_判成windows(env):
    conn, _, _ = env
    assert manage_state.collect_platform_for(conn, IP, asset_serial=SERIAL) == "windows"


def test_根因反證_沒有WinRM紀錄就照舊退回埠號猜(env):
    conn, _, _ = env
    conn.execute("DELETE FROM collect_dispatch_result")
    conn.commit()
    # 這就是公司機的情形：掃描沒有 3389 → linux → 拿 SSH 去收 Windows
    assert manage_state.collect_platform_for(conn, IP, asset_serial=SERIAL) == "linux"


def test_開始收集走WinRM成功_facts寫回資產(env):
    import collect_dispatch

    conn, _, _ = env
    asset = {"asset_serial": SERIAL}
    status, msg = collect_dispatch._do_winrm(conn, IP, asset, None, None)
    assert status == collect_dispatch.STATUS_COLLECTED, msg
    row = conn.execute("SELECT os, hostname, hw_serial FROM hardware WHERE asset_serial = ?",
                       (SERIAL,)).fetchone()
    assert row["os"] == "Microsoft Windows Server 2022 Standard"
    assert row["hw_serial"] == "VMware-42 0b aa"


# ---------------- 2. 軟體 ----------------

def test_軟體_讀登錄檔_不用Win32_Product():
    assert "Win32_Product" not in software_collector.WINDOWS_PS
    assert "Uninstall" in software_collector.WINDOWS_PS
    assert "WOW6432Node" in software_collector.WINDOWS_PS


def test_軟體_收到後source是winrm_registry(env):
    conn, _, _ = env
    r = software_inventory.collect_software(conn, only_serial=SERIAL)
    assert r["packages"] == 3, r
    rows = conn.execute("SELECT name, version, arch, vendor, installed_at, source "
                        "FROM host_package WHERE ip = ?", (IP,)).fetchall()
    assert {x["source"] for x in rows} == {"winrm_registry"}
    seven = [x for x in rows if x["name"].startswith("7-Zip")][0]
    assert (seven["version"], seven["arch"], seven["vendor"], seven["installed_at"]) == \
        ("23.01", "x64", "Igor Pavlov", "2024-01-15")


def test_軟體_登錄檔讀不到_講權限不足不寫0筆(env):
    conn, outputs, _ = env
    outputs["software"] = SOFTWARE_OUT_DENIED
    r = software_inventory.collect_software(conn, only_serial=SERIAL)
    assert r["packages"] == 0 and len(r["failed"]) == 1
    assert "權限不足（需" in r["failed"][0]["error"]


# ---------------- 3. 服務 ----------------

def test_服務_非管理員退回登錄檔_狀態留空_埠照收(env):
    conn, _, _ = env
    r = service_inventory.collect_services(conn, only_serial=SERIAL)
    assert r["services"] == 3 and not r["failed"], r
    host = r["hosts"][0]
    assert host["winservices"] == 3
    assert any("權限不足（需" in n for n in host["notes"])
    ws = {w["name"]: w for w in service_inventory.list_winservices(conn, asset_serial=SERIAL)}
    assert ws["WinRM"]["state"] is None                 # 登錄檔沒有狀態：不知道，不是停止
    assert ws["WinRM"]["start_mode"] == "auto"
    assert ws["MSSQLSERVER"]["account"] == "CORP\\svc_sql"
    assert ws["WinRM"]["source"] == "winrm_registry"
    ports = {s["port"] for s in service_inventory.list_services(conn, asset_serial=SERIAL)}
    assert ports == {5985, 1433, 135}


def test_服務_有權限時有狀態_PID對到服務名(env):
    conn, outputs, _ = env
    outputs["services"] = SERVICES_OUT_FULL
    service_inventory.collect_services(conn, only_serial=SERIAL)
    ws = {w["name"]: w for w in service_inventory.list_winservices(conn, asset_serial=SERIAL)}
    assert ws["WinRM"]["state"] == "running" and ws["WinRM"]["source"] == "winrm_cim"
    assert ws["Spooler"]["start_mode"] == "disabled"
    by_port = {s["port"]: s for s in service_inventory.list_services(conn, asset_serial=SERIAL)}
    assert by_port[47001]["process"] == "svchost (WinRM)"
    assert by_port[47001]["guess_source"] == "process"


def test_服務_全部被拒_失敗且講權限_不把舊資料標成消失(env):
    conn, outputs, _ = env
    service_inventory.collect_services(conn, only_serial=SERIAL)      # 先有一輪資料
    outputs["services"] = SERVICES_OUT_ALL_DENIED
    r = service_inventory.collect_services(conn, only_serial=SERIAL)
    assert r["failed"] and "權限不足（需" in r["failed"][0]["error"]
    assert all(s["gone_at"] is None
               for s in service_inventory.list_services(conn, asset_serial=SERIAL))
    assert len(service_inventory.list_winservices(conn, asset_serial=SERIAL)) == 3


def test_服務_舊版腳本只有LISTEN行也吃得下():
    r = service_collector.parse_windows_services(
        'LISTEN 0 0 0.0.0.0:3389 *:* users:(("svchost"))\n')
    assert [p["port"] for p in r["ports"]] == [3389]
    assert r["winservices"] == [] and r["errors"] == []


# ---------------- 4. 帳號 ----------------

def test_帳號_解析_本機帳號與網域成員():
    res = account_collector.parse_windows_accounts(ACCOUNTS_OUT)
    acc = {a["username"]: a for a in res["accounts"]}
    assert acc["localadm"]["kind"] == "default"          # RID 500＝改過名的 Administrator
    assert acc["localadm"]["is_sudoer"] is True
    assert acc["Guest"]["kind"] == "default" and acc["Guest"]["pw_status"] == "locked"
    assert acc["DefaultAccount"]["kind"] == "builtin"
    assert acc["webit3sc"]["kind"] == "service"
    assert acc["webit3sc"]["priv_groups"] == "Remote Management Users"
    assert acc["alice"]["kind"] == "human"
    assert acc["alice"]["never_logged_in"] is True
    assert acc["alice"]["pw_max_days"] == "59"
    assert acc["localadm"]["pw_max_days"] == "99999"     # PasswordExpires 空＝永不過期
    assert acc["CORP\\Domain Admins"]["kind"] == "domain"
    assert acc["CORP\\Domain Admins"]["is_sudoer"] is True
    assert acc["CORP\\Domain Admins"]["can_login"] is False   # 群組不是可登入的帳號
    assert acc["CORP\\bob"]["kind"] == "domain"
    assert "NT AUTHORITY\\INTERACTIVE" not in acc             # 內建主體不是帳號
    assert res["part_of_domain"] is True
    assert [e["item"] for e in res["errors"]] == ["members:Backup Operators"]
    # 沒有任何密碼雜湊欄位
    assert not any(k for a in res["accounts"] for k in a if "hash" in k.lower())


def test_帳號_規則_windows不適用的不亮未知():
    res = account_collector.parse_windows_accounts(ACCOUNTS_OUT)
    th = {"acct_pw_max_days": 90, "acct_idle_days": 90}
    f = account_rules.evaluate(res["accounts"], th)
    keyed = {(x["username"], x["rule_id"]): x for x in f}
    assert not any(r == "A4" for _, r in keyed)                       # 沒有 SSH 金鑰這回事
    assert ("CORP\\bob", "A1") not in keyed                           # 網域帳號密碼歸 AD
    assert keyed[("alice", "R2")]["verdict"] == "fail"                # 密碼逾期未更換
    assert keyed[("alice", "R5")]["detail"] == "從未登入過"
    assert keyed[("localadm", "R9")]["verdict"] == "fail"             # 內建管理員仍啟用
    assert not any("需 root" in x["detail"] for x in f)


def test_帳號_收進host_account_同一套儀表板(env):
    conn, _, _ = env
    r = account_inventory.collect_accounts(conn, only_serial=SERIAL)
    assert not r["failed"] and not r["skipped"], r
    assert r["accounts"] == 7
    rows = {x["username"]: x for x in conn.execute(
        "SELECT username, kind, is_sudoer, source, os_family FROM host_account WHERE ip = ?",
        (IP,)).fetchall()}
    assert rows["CORP\\Domain Admins"]["kind"] == "domain"
    assert rows["localadm"]["is_sudoer"] == 1
    assert {x["source"] for x in rows.values()} == {"winrm_local"}
    assert {x["os_family"] for x in rows.values()} == {"windows"}
    assert any("權限不足（需" in n for n in r["hosts"][0]["notes"])  # Backup Operators 那一組


def test_帳號_完全拿不到_失敗且講權限不足_不寫成0個帳號(env):
    conn, outputs, _ = env
    outputs["accounts"] = ACCOUNTS_OUT_DENIED
    r = account_inventory.collect_accounts(conn, only_serial=SERIAL)
    assert r["accounts"] == 0 and len(r["failed"]) == 1
    assert "權限不足（需" in r["failed"][0]["error"]
    assert "RestrictRemoteSAM" in r["failed"][0]["error"]


def test_帳號_KIND_LABEL_有網域帳號():
    assert account_inventory.KIND_LABEL["domain"] == "網域帳號"


# ---------------- 5. 納管後六樣（端到端，假 PSRP）----------------

def test_納管後六樣_windows_四樣收到_兩樣略過_不再是全0(env):
    conn, _, calls = env
    res = {r["job"]: r for r in post_onboard.run_now(conn, SERIAL, source="manual")}
    assert res["spec"]["ok"] is True, res["spec"]
    assert res["service"]["ok"] is True, res["service"]
    assert res["software"]["ok"] is True, res["software"]
    assert res["account"]["ok"] is True, res["account"]
    assert res["rebuild"]["ok"] is True, res["rebuild"]    # OS 重建 Windows 版（WinRM）
    assert res["config_audit"]["ok"] is True, res["config_audit"]   # 公司基準 286 條（本機管理員）
    assert "權限不足（需" in res["service"]["message"]          # 部分收到要講少了什麼
    # 全部走 WinRM，沒有任何一樣去碰 SSH
    assert {k for _, k in calls} == {"facts", "services", "software", "accounts", "audit", "rebuild"}
    os_now = conn.execute("SELECT os FROM hardware WHERE asset_serial = ?",
                          (SERIAL,)).fetchone()["os"]
    assert "Windows Server 2022" in os_now


def test_納管後_組態檢核_非本機管理員_打叉講原因_不算合規也不算不合規(env):
    import config_audit

    conn, outputs, _ = env
    outputs["audit"] = AUDIT_OUT_NOT_ADMIN
    res = {r["job"]: r for r in post_onboard.run_now(conn, SERIAL, source="manual")}
    assert res["config_audit"]["ok"] is False, res["config_audit"]
    assert "需本機管理員才能做 Windows 組態檢核" in res["config_audit"]["message"]
    assert config_audit.summary(conn)["hosts"] == 0                  # 不進任何計數
    d = config_audit.by_asset(conn, SERIAL)
    assert d["run"] is None and "需本機管理員" in d["last_failure"]["note"]


def test_納管後_組態檢核_設定關掉_略過講原因_什麼都不送(env):
    conn, _, calls = env
    db.set_setting(conn, win_fcb.SETTING_ENABLED, "0")
    conn.commit()
    res = {r["job"]: r for r in post_onboard.run_now(conn, SERIAL, source="manual")}
    assert res["config_audit"]["ok"] is None, res["config_audit"]   # 略過，不是失敗
    assert res["config_audit"]["message"] == win_fcb.DISABLED_REASON
    assert "audit" not in {k for _, k in calls}


def test_每段腳本都設了逐項錯誤回報():
    for ps in (winrm_collector.SERVICES_PS, winrm_collector.ACCOUNTS_PS,
               software_collector.WINDOWS_PS):
        assert "function W3Err" in ps and "W3Err '" in ps


def test_explain_error_格式():
    t = winrm_collector.explain_error({"item": "ports", "kind": "DENIED", "message": "x"})
    assert t.startswith("監聽埠：權限不足（需 ")
    t2 = winrm_collector.explain_error({"item": "members:Administrators", "kind": "FAIL",
                                        "message": "boom"})
    assert "Administrators" in t2 and "收集失敗（boom）" in t2


def test_帳號_群組成員被拒_不把舊管理員洗成沒有_也不標消失(env):
    """審查 blocker：非管理員查群組被拒是常態——不可以因此把 is_sudoer 洗成 0、把網域成員標消失。"""
    conn, outputs, _ = env
    account_inventory.collect_accounts(conn, only_serial=SERIAL)          # 第一輪：群組收得到
    outputs["accounts"] = "\n".join(
        ln for ln in ACCOUNTS_OUT.splitlines() if not ln.startswith(("MEMBER\t", "ERR\t"))
    ) + "\nERR\tmembers:Administrators\tDENIED\tAccess is denied.\n"
    r = account_inventory.collect_accounts(conn, only_serial=SERIAL)
    assert not r["failed"]
    rows = {x["username"]: x for x in conn.execute(
        "SELECT username, is_sudoer, gone_at FROM host_account WHERE ip = ?", (IP,)).fetchall()}
    assert rows["localadm"]["is_sudoer"] == 1
    assert rows["CORP\\Domain Admins"]["gone_at"] is None
    assert any("權限不足（需" in n for n in r["hosts"][0]["notes"])


def test_軟體_某個機碼被拒_那一側舊套件不標移除(env):
    conn, outputs, _ = env
    software_inventory.collect_software(conn, only_serial=SERIAL)
    outputs["software"] = ("SRC=registry\n"
                           "PKG\t7-Zip 23.01 (x64)\t23.01\tx64\t20240115\tIgor Pavlov\n"
                           "ERR\tsoftware\tDENIED\tRequested registry access is not allowed.\n")
    software_inventory.collect_software(conn, only_serial=SERIAL)
    gone = conn.execute("SELECT COUNT(*) FROM host_package WHERE ip = ? AND gone_at IS NOT NULL",
                        (IP,)).fetchone()[0]
    assert gone == 0


def test_部分權限不足_標警告不打勾_每樣有秒數(env):
    """公司機實測（v1.475.0）：服務三部分全被拒卻顯示 ✓ 收到了。✓ 只給全部收到。"""
    conn, _, _ = env
    res = {r["job"]: r for r in post_onboard.run_now(conn, SERIAL, source="manual")}
    assert res["service"]["ok"] is True and res["service"]["warn"] is True
    assert res["service"]["message"].startswith("部分收到（")
    assert res["software"]["warn"] is False and res["software"]["message"] == "收到了（3 筆）"
    assert all(isinstance(r.get("secs"), float) for r in res.values())


def test_中文系統的拒絕存取也算權限不足():
    assert "拒絕存取" in winrm_collector._PS_ERR_FN


def test_登錄檔讀到一半被拒_服務清單不標消失(env):
    conn, outputs, _ = env
    service_inventory.collect_services(conn, only_serial=SERIAL)          # 3 個服務
    outputs["services"] = (
        "SRC=winrm\nERR\tservices\tDENIED\tAccess denied\n"
        "SVCREG\tWinRM\t\tAuto\tNT AUTHORITY\\NetworkService\n"
        "ERR\tservices_registry\tDENIED\t拒絕存取\n"
        "LISTEN 0 0 0.0.0.0:5985 *:* users:((\"System\"))\n")
    service_inventory.collect_services(conn, only_serial=SERIAL)
    assert len(service_inventory.list_winservices(conn, asset_serial=SERIAL)) == 3


def test_狀態API_進度帶已跑秒數_已完成的幾樣(env, monkeypatch):
    conn, _, _ = env
    post_onboard._progress[SERIAL] = {"state": "running", "job": "software", "label": "軟體",
                                      "step": 3, "total": 6, "since": "2026-10-20 00:00:00",
                                      "done": [{"label": "硬體規格", "ok": True, "warn": False,
                                                "secs": 2.0}]}
    post_onboard._running.add(SERIAL)
    try:
        import routers.onboard as ob

        r = ob.onboard_post_collect_status(SERIAL, session=None, conn=conn)
        assert r["running"] and r["progress"]["elapsed_s"] > 0
        assert r["progress"]["stale"] is True                      # 很久以前開始＝可能已中斷
        assert r["progress"]["done"][0]["label"] == "硬體規格"
    finally:
        post_onboard._running.discard(SERIAL)
        post_onboard._progress.pop(SERIAL, None)


# ---------------- 決策 WINRM-ADMIN1：記錄是否本機管理員、非管理員只講一句 ----------------

def test_facts_帶是否本機管理員():
    f = winrm_collector.parse_facts(FACTS_OUT + "is_admin=0\nrun_as=CORP\\webit3sc\n")
    assert f["is_admin"] == 0 and f["run_as"] == "CORP\\webit3sc"
    assert "IsInRole" in winrm_collector.FACTS_PS


def test_非管理員_2_1結論講出來_資產頁一句總說明_各樣不散列(env):
    import collect_dispatch
    import routers.onboard as ob

    conn, outputs, _ = env
    outputs["facts"] = FACTS_OUT + "is_admin=0\nrun_as=CORP\\webit3sc\n"
    status, msg = collect_dispatch._do_winrm(conn, IP, {"asset_serial": SERIAL}, None, None)
    assert "本機管理員：否" in msg
    assert winrm_collector.session_for(conn, IP)["is_admin"] == 0
    res = {r["job"]: r for r in post_onboard.run_now(conn, SERIAL, source="manual")}
    assert res["service"]["warn"] and "非本機管理員" in res["service"]["message"]
    assert "權限不足（需" not in res["service"]["message"]
    st = ob.onboard_post_collect_status(SERIAL, session=None, conn=conn)
    assert st["host_notice"] == winrm_collector.NOT_ADMIN_NOTICE


def test_管理員_沒有總說明(env):
    import collect_dispatch
    import routers.onboard as ob

    conn, outputs, _ = env
    outputs["facts"] = FACTS_OUT + "is_admin=1\nrun_as=CORP\\webit3sc\n"
    status, msg = collect_dispatch._do_winrm(conn, IP, {"asset_serial": SERIAL}, None, None)
    assert "本機管理員：是" in msg
    st = ob.onboard_post_collect_status(SERIAL, session=None, conn=conn)
    assert st["host_notice"] is None


# ---------------- IIS（2026-10-20）：站台→應用程式／虛擬目錄→集區＋綁定 ----------------
# 格式照 IIS_PS 實跑的輸出（開發機以假 applicationHost.config 跑過 PowerShell 5.1）
IIS_OUT = (
    "@@IIS\nIIS=1\n"
    "POOL\tDefaultAppPool\tStarted\tv4.0\tIntegrated\tApplicationPoolIdentity\t\n"
    "POOL\tApiPool\tStarted\tNoManagedCode\tClassic\tSpecificUser\tCORP\\svc_api\n"
    "SITE\t1\tDefault Web Site\tStarted\t%SystemDrive%\\inetpub\\wwwroot\ttrue\n"
    "BIND\t1\thttp\t*:80:\t\t\t\n"
    "BIND\t1\thttps\t*:443:\tAB12CD\t2027-01-31\tCN=winap01\n"
    "APP\t1\t/\tDefaultAppPool\t%SystemDrive%\\inetpub\\wwwroot\n"
    "VDIR\t1\t/\t/static\tD:\\static\n"
    "APP\t1\t/api\tApiPool\tD:\\apps\\api\n"
)


def test_IIS_樹狀_綁定回填服務名(env):
    import iis_inventory

    conn, outputs, _ = env
    outputs["services"] = SERVICES_OUT_FULL.replace(
        "LISTEN 0 0 0.0.0.0:5985", "LISTEN 0 0 0.0.0.0:443 *:* users:((\"System\"))\nLISTEN 0 0 0.0.0.0:5985") + IIS_OUT
    service_inventory.collect_services(conn, only_serial=SERIAL)
    d = iis_inventory.for_asset(conn, SERIAL)
    assert d["state"] == "ok" and len(d["sites"]) == 1
    site = d["sites"][0]
    assert [a["path"] for a in site["apps"]] == ["/", "/api"]
    assert site["apps"][0]["vdirs"][0]["path"] == "/static"
    https = [b for b in site["bindings"] if b["protocol"] == "https"][0]
    assert https["port"] == 443 and https["cert_expires"] == "2027-01-31"
    pools = {p["name"]: p for p in d["pools"]}
    assert pools["ApiPool"]["user"] == "CORP\\svc_api"
    assert not any("password" in k.lower() for p in d["pools"] for k in p)
    by_port = {s["port"]: s for s in service_inventory.list_services(conn, asset_serial=SERIAL)}
    assert by_port[443]["service_guess"] == "IIS：Default Web Site"


def test_IIS_沒裝是此機未使用_讀不到講權限且保留舊資料(env):
    import iis_inventory

    conn, outputs, _ = env
    outputs["services"] = SERVICES_OUT_FULL + IIS_OUT
    service_inventory.collect_services(conn, only_serial=SERIAL)
    outputs["services"] = SERVICES_OUT_FULL + "@@IIS\nIIS=1\nERR\tiis\tDENIED\tAccess to the path is denied.\n"
    r = service_inventory.collect_services(conn, only_serial=SERIAL)
    assert any("IIS：權限不足" in n for n in r["hosts"][0]["notes"])
    d = iis_inventory.for_asset(conn, SERIAL)
    assert d["last_state"] == "denied" and len(d["sites"]) == 1       # 舊資料還在
    outputs["services"] = SERVICES_OUT_FULL + "@@IIS\nIIS=0\n"
    service_inventory.collect_services(conn, only_serial=SERIAL)
    assert iis_inventory.for_asset(conn, SERIAL)["state"] == "unused"


def test_IIS_腳本不用appcmd_不讀密碼():
    ps = winrm_collector.IIS_PS
    assert "appcmd" not in ps.lower() and "password" not in ps.lower()
    assert "@@IIS" in winrm_collector.SERVICES_PS
