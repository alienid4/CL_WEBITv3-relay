"""使用者肉眼抓到、112 項測試卻全綠的四個 bug。

寫這組測試之前先承認一件事：原本的測試驗的是「程式有沒有照我的假設跑」。假設錯了，
測試就跟著錯，而且永遠是綠的。底下每一支都刻意用**真實環境的資料形狀**（空字串主機名、
混合環境、已登記卻還掛著問題），而不是我自己捏的乾淨資料。
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import comparison_engine  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_STUB_CREDENTIAL = "test-password-123"
SCAN_T1 = "2026-07-18 10:00:00"
SCAN_T2 = "2026-07-18 11:00:00"


def _client(tmp):
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)

    def _override_get_db():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    client = TestClient(api.app)
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password(_STUB_CREDENTIAL))
        conn.commit()
    finally:
        conn.close()
    assert client.post(
        "/api/auth/login", json={"username": "tester", "password": _STUB_CREDENTIAL}
    ).status_code == 200
    return client, db_path


def _scan(conn, scan_time, hosts):
    """hosts: [(ip, hostname)]。hostname 用空字串——這就是真實掃描的樣子。"""
    for ip, hn in hosts:
        conn.execute(
            "INSERT INTO scan_history (scan_time, ip, hostname, segment, scan_ok) "
            "VALUES (?,?,?,?,1)",
            (scan_time, ip, hn, "192.168.1.0/24"),
        )
    conn.commit()


# ===== Bug 1：空字串主機名讓多台未登記主機被去重成一台 =====

def test_多台無主機名的未登記主機各自產生漏登記():
    """實際故障：網段上有 3 台未登記主機，反解不到名稱（hostname=''），
    問題清單卻只出現 1 筆——去重只比對 hostname，`hostname = ''` 讓它們全部長得一樣。"""
    with tempfile.TemporaryDirectory() as t:
        db_path = Path(t) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            _scan(conn, SCAN_T1, [
                ("YOUR_CLIENT_IP", ""),
                ("YOUR_CLIENT_IP2", ""),
                ("192.168.1.230", ""),
            ])
            n = comparison_engine.detect_missing_from_ica(conn, SCAN_T1)
            rows = conn.execute(
                "SELECT ip FROM comparison_result WHERE issue_type='漏登記' ORDER BY ip"
            ).fetchall()
        finally:
            conn.close()

        assert n == 3, f"應產生 3 筆漏登記，實際 {n}"
        # 用集合比對而不是有序清單：relay 匯出會把內網位址換成佔位符，被換到的與沒被換到的
        # 混在一起會讓 ORDER BY ip 的排序改變，斷言就假性失敗。這裡在乎的是「三台都各自
        # 成為一筆」，不是它們的排列順序。
        assert {r["ip"] for r in rows} == {
            "YOUR_CLIENT_IP", "YOUR_CLIENT_IP2", "192.168.1.230"
        }, "三台未登記主機必須各自成為一筆，不能被空主機名合併掉"


def test_同一台重複掃描不會重複產生漏登記():
    """去重本身仍要有效——修 bug 1 不能把「同一個未結案問題不要一天生一筆」弄壞。"""
    with tempfile.TemporaryDirectory() as t:
        db_path = Path(t) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            _scan(conn, SCAN_T1, [("YOUR_CLIENT_IP", "")])
            comparison_engine.detect_missing_from_ica(conn, SCAN_T1)
            _scan(conn, SCAN_T2, [("YOUR_CLIENT_IP", "")])
            second = comparison_engine.detect_missing_from_ica(conn, SCAN_T2)
            total = conn.execute(
                "SELECT COUNT(*) FROM comparison_result WHERE issue_type='漏登記'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert second == 0 and total == 1, "同一台的未結案漏登記不該重複累積"


def test_空主機名不會誤配到空主機名的資產():
    """比對方向的同一個 bug：hardware 裡若有 hostname='' 的列，
    `hostname = ''` 會把毫不相干的掃描結果判定成已登記，於是漏登記整個消失。"""
    with tempfile.TemporaryDirectory() as t:
        db_path = Path(t) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            db.insert_hardware(conn, asset_serial="HW-1", hostname="", ip="10.1.1.1",
                               environment="正式")
            _scan(conn, SCAN_T1, [("YOUR_CLIENT_IP", "")])
            n = comparison_engine.detect_missing_from_ica(conn, SCAN_T1)
        finally:
            conn.close()
        assert n == 1, "YOUR_CLIENT_IP 沒登記，不該因為空主機名而被誤判成已登記"


# ===== Bug 2：主機已登記後，舊的漏登記不會撤銷 =====

def test_主機納管後舊的漏登記會自動結案():
    """實際故障：畫面上 2 筆待處理的漏登記，指的兩台其實早就登記好了——
    已經不成立的問題掛在那裡，反而掩蓋掉真正待處理的。"""
    with tempfile.TemporaryDirectory() as t:
        db_path = Path(t) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            _scan(conn, SCAN_T1, [("YOUR_CLIENT_IP", "")])
            comparison_engine.detect_missing_from_ica(conn, SCAN_T1)
            assert conn.execute(
                "SELECT COUNT(*) FROM comparison_result WHERE is_read=0"
            ).fetchone()[0] == 1

            # 使用者把它納入管理
            db.insert_hardware(conn, asset_serial="ADOPT-1", hostname="new-host",
                               ip="YOUR_CLIENT_IP", environment="正式")

            resolved = comparison_engine.resolve_issues_for_registered_hosts(conn)
            pending = conn.execute(
                "SELECT COUNT(*) FROM comparison_result WHERE is_read=0"
            ).fetchone()[0]
            handled = conn.execute(
                "SELECT handled_at FROM comparison_result WHERE id=1"
            ).fetchone()[0]
        finally:
            conn.close()

        assert resolved == 1
        assert pending == 0, "納管後那筆漏登記應自動結案"
        assert handled, "結案要留下 handled_at，保留歷史而不是刪掉"


# ===== Bug 3：時區 =====

def test_時間欄位用本地時間而非UTC():
    """實際故障：scan_history 用 SQLite datetime('now')（UTC），scan_runs 用 Python
    datetime.now()（本地），同一次掃描在兩張表差 8 小時，畫面顯示的掃描時間是錯的。"""
    with tempfile.TemporaryDirectory() as t:
        db_path = Path(t) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            conn.execute(
                "INSERT INTO scan_history (ip, hostname, segment, scan_ok) VALUES (?,?,?,1)",
                ("10.0.0.1", "h1", "10.0.0.0/24"),
            )
            db.insert_comparison_result(conn, "h1", "10.0.0.1", "漏登記")
            conn.commit()
            scan_t = conn.execute("SELECT scan_time FROM scan_history").fetchone()[0]
            det_t = conn.execute("SELECT detected_at FROM comparison_result").fetchone()[0]
        finally:
            conn.close()

        now = datetime.now()
        for label, value in (("scan_history.scan_time", scan_t),
                             ("comparison_result.detected_at", det_t)):
            delta = abs((datetime.strptime(value, "%Y-%m-%d %H:%M:%S") - now).total_seconds())
            assert delta < 120, (
                f"{label} = {value}，與本地時間 {now:%Y-%m-%d %H:%M:%S} 差 {delta/3600:.1f} 小時"
            )


def test_既有UTC資料會被換算成本地時間且只換一次():
    """遷移必須冪等——跑第二次再加一次時差，資料就毀了。"""
    with tempfile.TemporaryDirectory() as t:
        db_path = Path(t) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            # 模擬舊資料：直接寫入 UTC 值並把遷移標記拿掉
            conn.execute("DELETE FROM app_settings WHERE key = ?", (db._TZ_MIGRATION_KEY,))
            conn.execute(
                "INSERT INTO scan_history (scan_time, ip, segment, scan_ok) "
                "VALUES (datetime('now'), '10.0.0.9', 'x', 1)"
            )
            conn.commit()
            before = conn.execute(
                "SELECT scan_time FROM scan_history WHERE ip='10.0.0.9'").fetchone()[0]
            db._migrate_timestamps_to_localtime(conn)
            after = conn.execute(
                "SELECT scan_time FROM scan_history WHERE ip='10.0.0.9'").fetchone()[0]
            db._migrate_timestamps_to_localtime(conn)  # 再跑一次
            after2 = conn.execute(
                "SELECT scan_time FROM scan_history WHERE ip='10.0.0.9'").fetchone()[0]
        finally:
            conn.close()

        assert after2 == after, f"遷移不冪等：第二次又動了（{after} -> {after2}）"
        now = datetime.now()
        delta = abs((datetime.strptime(after, "%Y-%m-%d %H:%M:%S") - now).total_seconds())
        assert delta < 120, f"換算後 {after} 仍不是本地時間（差 {delta/3600:.1f} 小時）"
        # 若本機時區就是 UTC，before==after 是合理的，不強制要求兩者不同


def test_既有資料庫的舊預設值不會讓新資料又寫成UTC():
    """改 schema.sql 只對「全新」資料庫有效——這是我修時區時差點漏掉的破口。

    `CREATE TABLE IF NOT EXISTS` 不會修改既有資料表的欄位預設值，所以正式環境那張
    已經存在的 comparison_result 仍帶著舊的 `DEFAULT (datetime('now'))`（UTC）。
    實測時就是這樣：遷移把舊資料換算對了，新寫入的卻還是 UTC。
    正解是不倚賴 DEFAULT，由 Python 明確寫入。

    測法：故意把欄位預設值設成一個明顯錯誤的固定值，如果程式有乖乖自己寫時間，
    這個假預設值就永遠不會出現。
    """
    with tempfile.TemporaryDirectory() as t:
        db_path = Path(t) / "test.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            # 重建一張帶「錯誤預設值」的表，模擬既有資料庫殘留的舊 DEFAULT
            conn.execute("DROP TABLE comparison_result")
            conn.execute(
                "CREATE TABLE comparison_result ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  detected_at TEXT DEFAULT '1999-01-01 00:00:00',"
                "  hostname TEXT, ip TEXT, issue_type TEXT,"
                "  is_read INTEGER DEFAULT 0, handled_at TEXT)"
            )
            conn.commit()
            db.insert_comparison_result(conn, "h1", "10.0.0.1", "漏登記")
            got = conn.execute("SELECT detected_at FROM comparison_result").fetchone()[0]
        finally:
            conn.close()

        assert got != "1999-01-01 00:00:00", (
            "時間是資料表 DEFAULT 填的——既有資料庫上的舊 UTC 預設會繼續生效"
        )
        delta = abs((datetime.strptime(got, "%Y-%m-%d %H:%M:%S") - datetime.now()).total_seconds())
        assert delta < 120, f"detected_at = {got}，不是本地時間"


# ===== Bug 4：磚塊數字與下鑽清單對不上 =====

def test_掃到卻沒登記_磚塊與納管清單筆數一致_跨環境():
    """我原本的下鑽測試把資料全設成同一個環境，剛好避開了這個破口。

    真實情況：一台登記為「測試」環境的機器**是有登記的**。儀表板預設看「正式」，
    若拿環境子集去比，它會被算成未登記 -> 磚塊 4；但 /adopt 不分環境 -> 只有 3。
    """
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            conn = db.get_connection(db_path)
            try:
                db.insert_hardware(conn, asset_serial="P-1", hostname="prod-1",
                                   ip="10.99.0.10", environment="正式")
                # 關鍵：這台登記在「測試」環境，但它確實已登記
                db.insert_hardware(conn, asset_serial="T-1", hostname="test-1",
                                   ip="YOUR_SERVER_IP", environment="測試")
                _scan(conn, SCAN_T1, [
                    ("10.99.0.10", "prod-1"),     # 已登記(正式)
                    ("YOUR_SERVER_IP", "test-1"),    # 已登記(測試)
                    ("YOUR_CLIENT_IP", ""),          # 未登記
                    ("YOUR_CLIENT_IP2", ""),          # 未登記
                ])
            finally:
                conn.close()

            stats = client.get("/api/dashboard/stats", params={"environment": "正式"}).json()
            unreg = client.get("/api/scan/unregistered").json()

            assert stats["scan_only_count"] == len(unreg), (
                f"磚塊說 {stats['scan_only_count']} 台掃到沒登記，"
                f"點進去的納管清單卻有 {len(unreg)} 台"
            )
            assert stats["scan_only_count"] == 2, "只有 .222/.223 真的沒登記"
        finally:
            api.app.dependency_overrides.clear()


# ===== Bug 5：一致率語意反了（登記越少分數越高） =====

def test_一致率分母含未登記_不會因為少登記而變高分():
    """使用者在 221 上看到「一致率 100%」，但網路上 6 台、只登記 2 台、4 台完全沒納管。

    舊公式是 相符 ÷ ICA登記 = 2÷2 = 100%：只問「我登記的東西還在不在」，
    不問「網路上有多少東西我沒登記」——**一台都不登記反而是滿分**。
    對盤點系統語意剛好相反，而且這是頭條數字。

    這支測試守的是分母：必須是聯集（登記的 ∪ 掃到的），
    所以 2 ÷ (2+4) = 33%，不是 100%。
    """
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            conn = db.get_connection(db_path)
            try:
                db.insert_hardware(conn, asset_serial="A-1", hostname="reg-1",
                                   ip="10.99.0.1", environment="正式")
                db.insert_hardware(conn, asset_serial="A-2", hostname="reg-2",
                                   ip="10.99.0.101", environment="正式")
                _scan(conn, SCAN_T1, [
                    ("10.99.0.1", "reg-1"),      # 登記且掃得到
                    ("10.99.0.101", "reg-2"),    # 登記且掃得到
                    ("10.99.0.110", ""),         # 沒登記
                    ("10.99.0.113", ""),         # 沒登記
                    ("YOUR_CLIENT_IP", ""),         # 沒登記
                    ("YOUR_CLIENT_IP2", ""),         # 沒登記
                ])
            finally:
                conn.close()

            s = client.get("/api/dashboard/stats", params={"environment": "正式"}).json()

            denom = s["total_ica_count"] + s["scan_only_count"]
            rate = s["total_overlap_count"] / denom * 100

            assert s["scan_only_count"] == 4, "四台沒登記的必須被算出來"
            assert denom == 6, f"分母要是聯集 6（登記2＋未登記4），實際 {denom}"
            assert 30 < rate < 40, f"一致率應為 33%，實際 {rate:.1f}%（若是 100% 就是舊公式）"
        finally:
            api.app.dependency_overrides.clear()


def test_一致率不隨環境下拉跳動():
    """分母含「掃到卻沒登記」，而那些機器不屬於任何環境。

    若拿環境篩選過的登記數去配全站的未登記數，換個環境分數就跳動，
    但實際盤點狀況根本沒變。所以一致率固定用全站數字。
    """
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            conn = db.get_connection(db_path)
            try:
                db.insert_hardware(conn, asset_serial="P-1", hostname="prod-1",
                                   ip="10.99.0.1", environment="正式")
                db.insert_hardware(conn, asset_serial="T-1", hostname="test-1",
                                   ip="10.99.0.101", environment="測試")
                _scan(conn, SCAN_T1, [
                    ("10.99.0.1", "prod-1"),
                    ("10.99.0.101", "test-1"),
                    ("YOUR_CLIENT_IP", ""),
                ])
            finally:
                conn.close()

            rates = {}
            for env in ("正式", "正式+測試", "全部"):
                s = client.get("/api/dashboard/stats", params={"environment": env}).json()
                denom = s["total_ica_count"] + s["scan_only_count"]
                rates[env] = s["total_overlap_count"] / denom * 100

            assert len(set(round(r, 6) for r in rates.values())) == 1, (
                f"一致率隨環境跳動了：{rates}"
            )
        finally:
            api.app.dependency_overrides.clear()


# ===== Bug 6：頭條數字被環境偷偷過濾 =====

def test_頭條資產數不可被環境篩選矇騙():
    """使用者實際踩到：掃描結果頁明明 8 台已登記，儀表板「目前資產」卻寫 2
    ——因為儀表板預設只看「正式」，另外 6 台登記在「測試」。
    標籤沒說被過濾，人就會直接誤讀成「我只有 2 台資產」。

    守的是：後端要提供**不受環境影響**的全站數字給頭條用，
    且它必須等於資產總數，不隨環境下拉變動。
    """
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            conn = db.get_connection(db_path)
            try:
                db.insert_hardware(conn, asset_serial="P-1", hostname="p1",
                                   ip="10.99.0.1", environment="正式")
                db.insert_hardware(conn, asset_serial="P-2", hostname="p2",
                                   ip="192.168.1.2", environment="正式")
                for i in range(6):   # 六台登記在「測試」
                    db.insert_hardware(conn, asset_serial=f"T-{i}", hostname=f"t{i}",
                                       ip=f"10.99.0.1{i}", environment="測試")
            finally:
                conn.close()

            totals = set()
            for env in ("正式", "正式+測試", "全部"):
                s = client.get("/api/dashboard/stats", params={"environment": env}).json()
                totals.add(s["total_ica_count"])
                # 環境篩選過的那個數字本來就會變，但全站的不能變
                assert s["total_ica_count"] == 8, (
                    f"環境={env} 時全站資產數變成 {s['total_ica_count']}，應恆為 8"
                )
            assert len(totals) == 1, f"全站資產數隨環境跳動了：{totals}"

            # 對照：環境篩選過的數字確實只有 2（證明兩者是不同的東西，不是巧合相等）
            s = client.get("/api/dashboard/stats", params={"environment": "正式"}).json()
            assert s["ica_count"] == 2
        finally:
            api.app.dependency_overrides.clear()
