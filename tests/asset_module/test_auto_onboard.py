"""B 排程自動納管：授權閘門、只碰未納管、憑證不落地、稽核 trigger=auto。

這個功能會「無人在場自動對主機跑會建帳號/改 sshd 的腳本」，所以每一道安全設計都要有測試守：
1. 沒授權的網段一律不碰（就算裡面有未納管主機）
2. 只碰「已登記未納管」，不自動把未登記主機建成資產
3. 登入密碼絕不進回傳值、稽核、DB
4. 每一次自動納管都留 trigger='auto' 的稽核（不含密碼）
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auto_onboard as ao  # noqa: E402
import credential_store as cs  # noqa: E402
import db  # noqa: E402
import onboard_engine as eng  # noqa: E402

PUBKEY = "ssh-ed25519 AAAATESTKEY webit3-collector"
FAKE_PW = "FAKE-TEST-PW-not-real-B"


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed_scan(conn, ip, scan_time="2026-07-20 01:00:00", ports=""):
    """在最新一次掃描裡放一台存活主機（scan_ok=1），才會被判為『掃得到』。"""
    conn.execute(
        "INSERT INTO scan_history (scan_time, ip, hostname, scan_ok, open_ports) "
        "VALUES (?,?,?,1,?)", (scan_time, ip, None, ports))
    conn.commit()


def _seed_host(conn, ip, serial, collect_ok=0):
    """一台已登記主機。collect_ok=0 → 未納管；=1 → 已納管。"""
    db.insert_hardware(conn, asset_serial=serial, ip=ip, collect_ok=collect_ok,
                       environment="正式", asset_status="使用中")


def _ok_executor(host, username, password, platform, script, collector_ip, **kw):
    """假執行器：假裝納管成功，並把它拿到的密碼記下來供外洩檢查。"""
    _ok_executor.saw[host] = password
    return eng.OnboardResult(True, "execute", "納管腳本執行完成", "完成。")


_ok_executor.saw = {}


def _fake_ssh_cred(name="ssh-lab", user="svc_boot", pw=FAKE_PW):
    return lambda ip: (name, user, pw)


# ===== 授權閘門 =====

def test_沒授權網段_一律不碰任何主機():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed_host(conn, "192.168.1.50", "A-50")
            _seed_scan(conn, "192.168.1.50")
            # 沒新增任何授權網段
            assert ao.find_candidates(conn) == []
            r = ao.run_auto_onboard(conn, "YOUR_SERVER_IP",
                                    executor=_ok_executor, cred_lookup=_fake_ssh_cred())
            assert r["candidates"] == 0 and r["onboarded"] == 0
            # 完全沒動作 → 沒有稽核
            assert conn.execute("SELECT COUNT(*) c FROM onboard_audit").fetchone()["c"] == 0
        finally:
            conn.close()


def test_停用的授權網段不算授權():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed_host(conn, "10.0.0.5", "A-5")
            _seed_scan(conn, "10.0.0.5")
            ao.save_segment(conn, "10.0.0.", enabled=False)   # 有這段但停用
            assert ao.find_candidates(conn) == []
        finally:
            conn.close()


def test_只碰授權網段內的未納管主機():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed_host(conn, "192.168.1.50", "IN")     # 授權段內、未納管
            _seed_scan(conn, "192.168.1.50")
            _seed_host(conn, "172.16.0.9", "OUT")      # 授權段外、未納管
            _seed_scan(conn, "172.16.0.9")
            ao.save_segment(conn, "192.168.1.")
            cands = ao.find_candidates(conn)
            ips = {c["ip"] for c in cands}
            assert ips == {"192.168.1.50"}, f"只該有段內那台，實得 {ips}"
        finally:
            conn.close()


# ===== 只碰已登記未納管 =====

def test_已納管的主機不會被再次納管():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed_host(conn, "192.168.1.60", "DONE", collect_ok=1)   # 已納管
            _seed_scan(conn, "192.168.1.60")
            ao.save_segment(conn, "192.168.1.")
            assert ao.find_candidates(conn) == []
        finally:
            conn.close()


def test_未登記主機不會被自動建成資產():
    """未登記＝掃到但還不是資產，收不收是人的決定（防假資料混進真實盤點）。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            # 只在掃描裡、hardware 沒有 → 未登記
            _seed_scan(conn, "192.168.1.70")
            ao.save_segment(conn, "192.168.1.")
            before = conn.execute("SELECT COUNT(*) c FROM hardware").fetchone()["c"]
            r = ao.run_auto_onboard(conn, "YOUR_SERVER_IP",
                                    executor=_ok_executor, cred_lookup=_fake_ssh_cred())
            after = conn.execute("SELECT COUNT(*) c FROM hardware").fetchone()["c"]
            assert r["candidates"] == 0
            assert before == after, "自動納管不該新增資產列"
        finally:
            conn.close()


# ===== Linux 納管成功 + 憑證不落地 =====

def test_Linux未納管_用庫裡憑證納管成功_並留auto稽核():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _ok_executor.saw = {}
            _seed_host(conn, "192.168.1.80", "L-80")
            _seed_scan(conn, "192.168.1.80", ports="22")   # 無 3389 → linux
            ao.save_segment(conn, "192.168.1.")
            r = ao.run_auto_onboard(conn, "YOUR_SERVER_IP", pubkey=PUBKEY,
                                    executor=_ok_executor,
                                    cred_lookup=_fake_ssh_cred(user="svc_boot"))
            assert r["onboarded"] == 1 and r["failed"] == 0
            # 執行器確實收到密碼（否則登不進去）
            assert _ok_executor.saw.get("192.168.1.80") == FAKE_PW
            # 稽核是 auto、帳號名有留、成敗有留
            row = conn.execute(
                "SELECT * FROM onboard_audit WHERE target_ip='192.168.1.80'").fetchone()
            assert row["trigger"] == "auto" and row["ok"] == 1
            assert row["login_user"] == "svc_boot"
        finally:
            conn.close()


def test_密碼絕不進稽核任何欄位():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed_host(conn, "192.168.1.81", "L-81")
            _seed_scan(conn, "192.168.1.81", ports="22")
            ao.save_segment(conn, "192.168.1.")
            ao.run_auto_onboard(conn, "YOUR_SERVER_IP", pubkey=PUBKEY,
                                executor=_ok_executor, cred_lookup=_fake_ssh_cred())
            row = conn.execute(
                "SELECT * FROM onboard_audit WHERE target_ip='192.168.1.81'").fetchone()
            for v in dict(row).values():
                assert FAKE_PW not in str(v), f"密碼外洩到稽核欄位：{v}"
        finally:
            conn.close()


def test_回傳明細不含密碼():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed_host(conn, "192.168.1.82", "L-82")
            _seed_scan(conn, "192.168.1.82", ports="22")
            ao.save_segment(conn, "192.168.1.")
            r = ao.run_auto_onboard(conn, "YOUR_SERVER_IP", pubkey=PUBKEY,
                                    executor=_ok_executor, cred_lookup=_fake_ssh_cred())
            assert FAKE_PW not in str(r)
        finally:
            conn.close()


# ===== Windows 不跑 bootstrap =====

def test_Windows候選被跳過_不跑bootstrap():
    """Windows 走 WinRM 收集、不動目標機，不該對它跑建帳號的 bootstrap。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _ok_executor.saw = {}
            _seed_host(conn, "192.168.1.90", "W-90")
            _seed_scan(conn, "192.168.1.90", ports="3389")   # 3389 → windows
            ao.save_segment(conn, "192.168.1.")
            r = ao.run_auto_onboard(conn, "YOUR_SERVER_IP",
                                    executor=_ok_executor, cred_lookup=_fake_ssh_cred())
            assert r["skipped"] == 1 and r["onboarded"] == 0
            # 沒有對它動用執行器
            assert "192.168.1.90" not in _ok_executor.saw
            # 沒有寫失敗稽核（跳過不是失敗）
            assert conn.execute(
                "SELECT COUNT(*) c FROM onboard_audit WHERE target_ip='192.168.1.90'"
            ).fetchone()["c"] == 0
        finally:
            conn.close()


# ===== 憑證缺漏的處理 =====

def test_找不到SSH憑證_記為失敗且稽核不含密碼():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed_host(conn, "192.168.1.83", "L-83")
            _seed_scan(conn, "192.168.1.83", ports="22")
            ao.save_segment(conn, "192.168.1.")
            # cred_lookup 回 None＝沒有適用憑證
            r = ao.run_auto_onboard(conn, "YOUR_SERVER_IP",
                                    executor=_ok_executor, cred_lookup=lambda ip: None)
            assert r["failed"] == 1 and r["onboarded"] == 0
            row = conn.execute(
                "SELECT * FROM onboard_audit WHERE target_ip='192.168.1.83'").fetchone()
            assert row["trigger"] == "auto" and row["ok"] == 0
            assert "憑證" in row["message"]
        finally:
            conn.close()


def test_憑證解不開_記為失敗():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed_host(conn, "192.168.1.84", "L-84")
            _seed_scan(conn, "192.168.1.84", ports="22")
            ao.save_segment(conn, "192.168.1.")
            # 回 (name, None, None)＝找到但解不開
            r = ao.run_auto_onboard(conn, "YOUR_SERVER_IP", executor=_ok_executor,
                                    cred_lookup=lambda ip: ("ssh-lab", None, None))
            assert r["failed"] == 1
            row = conn.execute(
                "SELECT message FROM onboard_audit WHERE target_ip='192.168.1.84'").fetchone()
            assert "解不開" in row["message"]
        finally:
            conn.close()


# ===== 憑證庫 kind 過濾（B 靠這個挑對類型的憑證）=====

def test_pick_for_host_依kind過濾():
    """同一網段可能同時有 winrm 與 ssh 憑證；Linux 納管要挑 ssh，不能誤拿 winrm。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        kp = str(Path(tmp) / "key.bin")
        try:
            cs.save(conn, "win-lab", "winrm", "svc_win", FAKE_PW, scope="192.168.1.", key_path=kp)
            cs.save(conn, "ssh-lab", "ssh", "svc_ssh", FAKE_PW, scope="192.168.1.", key_path=kp)
            assert cs.pick_for_host(conn, "192.168.1.5", kind="ssh") == "ssh-lab"
            assert cs.pick_for_host(conn, "192.168.1.5", kind="winrm") == "win-lab"
        finally:
            conn.close()


# ===== 總開關 =====

def test_總開關預設關閉():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            assert ao.is_enabled(conn) is False
            ao.set_enabled(conn, True)
            assert ao.is_enabled(conn) is True
        finally:
            conn.close()
