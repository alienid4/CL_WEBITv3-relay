"""帳號盤點的兩種匯出格式，以及「不知道就留空」這條線。

使用者 2026-08-28 給了公司現行的 18 欄 Excel 格式，要兩種匯出：
標準帳號盤點（直接交出去）與全匯出（自己人查）。

最重要的一條：**`type_id` 判不出來就留空，不要填一個看起來對的值。**
使用者原話：「目前還沒討論出一個邏輯…在還沒有修改程式之前，都要人工判斷。」
公司的分類是**用途**（程式運行／資料庫運行／Anchor·APPM），而 /etc/passwd
只看得到 shell、uid、名字——那三類從系統面看起來一模一樣。

填錯的代價是稽核文件上的假資料，而且**沒有人會發現**，因為那一欄看起來有值。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_export as ae  # noqa: E402
import business_system as bs  # noqa: E402
import db  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn):
    db.insert_hardware(
        conn, asset_serial="A1", ip="10.0.0.10", hostname="app01",
        api_id="N-008", usage_unit="金融資訊部", user_name="林某明",
        custodian="林某昌", inventory_division="資訊管理處",
        inventory_department="資訊架構部")
    bs.upsert(conn, "N-008", "權證系統", "金融資訊部", "林某明")
    rows = [
        # username, uid, gid, shell, kind, can_login
        ("root", 0, 0, "/bin/bash", "human", 1),
        ("bin", 1, 1, "/sbin/nologin", "builtin", 0),
        ("webit3scan", 7001, 7002, "/bin/bash", "mgmt", 1),
        ("alice", 1001, 1001, "/bin/bash", "human", 1),
        ("appsvc", 800, 800, "/sbin/nologin", "service", 0),   # 3/4/6 分不出來
    ]
    for u, uid, gid, shell, kind, login in rows:
        conn.execute(
            "INSERT INTO host_account (ip, asset_serial, username, uid, gid, shell, "
            "kind, can_login, gecos, home) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("10.0.0.10", "A1", u, uid, gid, shell, kind, login, u, f"/home/{u}"))
    conn.commit()


# ---------------------------------------------------------------------------
# type_id：只填確定的
# ---------------------------------------------------------------------------

def test_確定的四種要自動填():
    assert ae.classify_type({"uid": 0, "username": "root", "kind": "human"}) == 1
    assert ae.classify_type({"uid": 1, "username": "bin", "kind": "builtin"}) == 2
    assert ae.classify_type({"uid": 7001, "username": "webit3scan", "kind": "mgmt"}) == 5
    assert ae.classify_type({"uid": 1001, "username": "alice", "kind": "human"}) == 7


def test_分不出來的一定要留空():
    """3 程式運行／4 資料庫運行／6 Anchor·APPM 從 /etc/passwd 看起來一模一樣：
    都是 nologin、低 uid、叫不出名字。猜一個等於在稽核文件上填假資料。"""
    for acc in (
        {"uid": 800, "username": "appsvc", "kind": "service"},
        {"uid": 645, "username": "sysinfra", "kind": "mgmt"},
        {"uid": 54321, "username": "oracle", "kind": "service"},
    ):
        assert ae.classify_type(acc) is None, f"竟然猜了一個 type_id：{acc}"


def test_root_優先於其他判斷():
    """uid=0 就是最高權限，不管它 kind 被判成什麼。"""
    assert ae.classify_type({"uid": 0, "username": "toor", "kind": "service"}) == 1


# ---------------------------------------------------------------------------
# 標準格式
# ---------------------------------------------------------------------------

def test_標準格式的欄位與順序():
    assert ae.STANDARD_COLUMNS[:6] == [
        "system_id", "system", "ap_department", "ap_owner", "hostname", "ip_addr"]
    assert ae.STANDARD_COLUMNS[-1] == "login_status"
    assert len(ae.STANDARD_COLUMNS) == 18


def test_department_是處加部串起來():
    """2026-08-28 用真實資料查證：inventory_division 3643 筆全是「資訊管理處」、
    inventory_department 全是「資訊架構部」，串起來正好是使用者範例的
    「資訊管理處資訊XX部」。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed(conn)
            rows, _ = ae.standard_rows(conn)
            assert rows[0]["department"] == "資訊管理處資訊架構部"
        finally:
            conn.close()


def test_ap_department_不可以用_inventory_department():
    """那一欄 3643 筆全是同一個值（我們自己部門）。用它的話所有機器的 AP 部門
    會變成同一個值，通知會全部寄回我們自己這裡。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed(conn)
            rows, _ = ae.standard_rows(conn)
            assert rows[0]["ap_department"] == "金融資訊部"
            assert rows[0]["ap_department"] != rows[0]["department"]
        finally:
            conn.close()


def test_對照表提供系統名稱():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed(conn)
            rows, _ = ae.standard_rows(conn)
            assert rows[0]["system_id"] == "N-008"
            assert rows[0]["system"] == "權證系統"
        finally:
            conn.close()


def test_摘要要說出還差多少才完整():
    """只給一個檔案而不講「其中 N 筆的類型要人工填」，人會以為它是完整的。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed(conn)
            rows, summary = ae.standard_rows(conn)
            assert summary["rows"] == 5
            assert summary["unclassified_type"] == 1, "appsvc 應該算成待人工分類"
            assert "rows_without_system_name" in summary
        finally:
            conn.close()


def test_password欄固定是x():
    """照抄公司格式。/etc/passwd 第 2 欄本來就是佔位，真值在 shadow——
    這支程式從來不收集也不匯出真密碼。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed(conn)
            rows, _ = ae.standard_rows(conn)
            assert {r["password"] for r in rows} == {"x"}
        finally:
            conn.close()


def test_沒採集到登入狀態不可以說成無法登入():
    """can_login 是 NULL 代表沒採到，不是「無法登入」——三態要分開。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed(conn)
            conn.execute("INSERT INTO host_account (ip, username, uid) "
                         "VALUES ('10.0.0.10', 'unknown', 999)")
            conn.commit()
            rows, _ = ae.standard_rows(conn)
            got = {r["username"]: r["login_status"] for r in rows}
            assert got["unknown"] == "未採集"
            assert got["root"] == "可登入"
            assert got["bin"] == "無法登入"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 全匯出
# ---------------------------------------------------------------------------

def test_全匯出要標明系統名稱查不到的原因():
    """空白有兩種原因：機器沒填 api_id ／ 對照表沒有這個代碼。
    要補的地方不同，長得一樣的話人不知道該補哪邊。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed(conn)
            db.insert_hardware(conn, asset_serial="A2", ip="10.0.0.11", api_id="X-999")
            db.insert_hardware(conn, asset_serial="A3", ip="10.0.0.12")
            for ip in ("10.0.0.11", "10.0.0.12"):
                conn.execute("INSERT INTO host_account (ip, username, uid, kind) "
                             "VALUES (?, 'svc', 700, 'service')", (ip,))
            conn.commit()

            rows, cols = ae.full_rows(conn)
            by_ip = {r["ip"]: r for r in rows if r["username"] == "svc"}
            assert by_ip["10.0.0.11"]["system_lookup"] == "對照表沒有這個代碼"
            assert by_ip["10.0.0.12"]["system_lookup"] == "機器沒填 api_id"
            assert "type_info" in cols
        finally:
            conn.close()


def test_全匯出的待人工判斷要講出來():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed(conn)
            rows, _ = ae.full_rows(conn)
            svc = next(r for r in rows if r["username"] == "appsvc")
            assert svc["type_id"] == ""
            assert svc["type_info"] == "待人工判斷", "空白會被讀成「查過了沒有」"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 對照表匯入
# ---------------------------------------------------------------------------

def test_對照表重匯是更新不是長重複():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            bs.upsert(conn, "N-008", "舊名", "A部", "甲")
            bs.upsert(conn, "N-008", "新名")
            n = conn.execute("SELECT COUNT(*) FROM business_system").fetchone()[0]
            assert n == 1
            r = bs.lookup(conn, "N-008")
            assert r["name"] == "新名"
            assert r["ap_owner"] == "甲", "只給新名稱不該把既有的負責人洗掉"
        finally:
            conn.close()


def test_對帳要說出還有多少台查不到():
    """只回「匯了 N 筆」等於沒回答問題。人要知道的是還差多少。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _seed(conn)
            db.insert_hardware(conn, asset_serial="A2", ip="10.0.0.11", api_id="X-999")
            db.insert_hardware(conn, asset_serial="A3", ip="10.0.0.12")
            conn.commit()
            cov = bs.coverage(conn)
            assert cov["assets_with_unmapped_api_id"] == 1
            assert cov["assets_without_api_id"] == 1
            assert "X-999" in cov["unmapped_codes"], "缺的代碼要具名列出，不能只給數字"
        finally:
            conn.close()
