"""Windows 服務收集走 WinRM（不是 SSH）：分流、憑證用完即丟、解析器共用、失敗原因可行動。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import credential_store  # noqa: E402
import db  # noqa: E402
import service_collector  # noqa: E402
import service_inventory  # noqa: E402
import winrm_collector  # noqa: E402

# 測試用的假密碼。刻意用一望即知是假的字串，也讓 checks.py 的「無寫死密鑰」
# 守門員放行——真密碼永遠不該出現在 repo 裡，連測試檔也一樣。
FAKE_PW = "FAKE-TEST-PW-not-real-9527"
FAKE_PW_AUDIT = "FAKE-TEST-PW-audit-check-3141"

# Get-NetTCPConnection 那段 PS 產出的樣子（格式刻意跟 ss 對齊）
WIN_OUT = """LISTEN 0 0 0.0.0.0:3389 *:* users:(("svchost"))
LISTEN 0 0 0.0.0.0:445 *:* users:(("System"))
LISTEN 0 0 127.0.0.1:5432 *:* users:(("postgres"))
LISTEN 0 0 0.0.0.0:5985 *:*
"""


def _db(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


class _TempKey:
    """把憑證加密金鑰導到暫存檔。

    正式路徑 /opt/webit3/.credential_key 在開發機上不存在，而 get_for_use 的
    key_path 是預設參數（定義時就綁定），改模組變數沒用——所以包一層轉呼叫。
    這樣加解密仍走真流程，只是金鑰換個地方放。
    """

    def __init__(self, tmp):
        self.kp = str(Path(tmp) / "cred.key")
        self._orig_get = None

    def __enter__(self):
        self._orig_get = credential_store.get_for_use
        orig = self._orig_get
        kp = self.kp
        credential_store.get_for_use = lambda c, n, key_path=kp: orig(c, n, key_path=key_path)
        return self

    def __exit__(self, *exc):
        credential_store.get_for_use = self._orig_get
        return False

    def save(self, conn, **kw):
        return credential_store.save(conn, key_path=self.kp, **kw)


def _seed_windows_host(conn, ip="203.0.113.101", serial="W-001"):
    db.insert_hardware(conn, asset_serial=serial, hostname="win-app", ip=ip, collect_ok=1)
    # 掃描指紋有 3389 → 平台判定成 windows（跟 facts 收集同一套判準）
    conn.execute(
        "INSERT INTO scan_history (ip, scan_ok, open_ports, scan_time) "
        "VALUES (?, 1, '3389,445', '2026-07-21 10:00:00')", (ip,)
    )
    conn.commit()


def test_windows輸出用同一支解析器():
    """解析器共用才不會 Linux 修了 bug、Windows 那條還留著舊行為。"""
    rows = service_collector.parse_listen(WIN_OUT)
    by_port = {r["port"]: r for r in rows}
    assert by_port[3389]["process"] == "svchost"
    assert by_port[5432]["process"] == "postgres"
    assert service_collector.exposure_of(by_port[5432]["bind"]) == "localhost"
    assert by_port[5985]["process"] is None      # 沒有行程名就留空，不假裝


def test_winrm收服務可注入runner且不碰網路():
    seen = {}

    def fake(host, ps):
        seen["host"] = host
        seen["ps"] = ps
        return WIN_OUT

    out = winrm_collector.collect_services("203.0.113.101", "u", "p", runner=fake)
    assert out == WIN_OUT
    assert seen["host"] == "203.0.113.101"
    assert "Get-NetTCPConnection" in seen["ps"]   # 用的是 Windows 原生做法，不是 netstat


def test_windows主機走winrm而不是ssh():
    """走 WinRM 納管的機器沒裝 OpenSSH，走錯路必定連不上——這條要釘住。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed_windows_host(conn)
            called = {}

            def fake_collect_services(ip, user, pw, **kw):
                called["ip"] = ip
                called["user"] = user
                return WIN_OUT

            orig = winrm_collector.collect_services
            winrm_collector.collect_services = fake_collect_services
            try:
                with _TempKey(tmp) as tk:
                    tk.save(conn, name="win-svc", kind="winrm",
                            username="svc", password=FAKE_PW)
                    r = service_inventory.collect_services(conn)
            finally:
                winrm_collector.collect_services = orig

            assert called.get("ip") == "203.0.113.101", "Windows 主機沒有走 WinRM"
            assert called.get("user") == "svc"        # 憑證庫的帳號真的被拿去用
            assert r["services"] == 4
            rows = service_inventory.list_services(conn)
            assert {x["source"] for x in rows} == {"winrm_nettcp"}
            assert {x["port"] for x in rows} == {3389, 445, 5432, 5985}
        finally:
            conn.close()


def test_沒有憑證時給的是能行動的原因():
    """「沒有憑證」跟「WinRM 沒開」要做的事完全不同，混成一句『連線失敗』等於沒講。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed_windows_host(conn)
            r = service_inventory.collect_services(conn)
            assert r["services"] == 0
            assert len(r["failed"]) == 1
            assert "憑證" in r["failed"][0]["error"]
        finally:
            conn.close()


def test_winrm連不上要附上開通指引():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed_windows_host(conn)

            def boom(ip, user, pw, **kw):
                raise ConnectionError("[WinError 10061] actively refused")

            orig = winrm_collector.collect_services
            winrm_collector.collect_services = boom
            try:
                with _TempKey(tmp) as tk:
                    tk.save(conn, name="win-svc", kind="winrm",
                            username="svc", password=FAKE_PW)
                    r = service_inventory.collect_services(conn)
            finally:
                winrm_collector.collect_services = orig

            err = r["failed"][0]["error"]
            assert "Enable-PSRemoting" in err        # 給 Windows 原生解法，不是一句失敗
            assert "5985" in err                     # 含防火牆那一步（實測 .101 踩過）
        finally:
            conn.close()


def test_憑證使用有稽核且不含密碼():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _db(tmp)
        try:
            _seed_windows_host(conn)

            orig = winrm_collector.collect_services
            winrm_collector.collect_services = lambda ip, u, p, **kw: WIN_OUT
            try:
                with _TempKey(tmp) as tk:
                    tk.save(conn, name="win-svc", kind="winrm",
                            username="svc", password=FAKE_PW_AUDIT)
                    service_inventory.collect_services(conn)
            finally:
                winrm_collector.collect_services = orig

            audit = [dict(r) for r in conn.execute("SELECT * FROM credential_use_audit")]
            assert len(audit) == 1 and audit[0]["ok"] == 1
            assert FAKE_PW_AUDIT not in str(audit)         # 密碼絕不進稽核
        finally:
            conn.close()
