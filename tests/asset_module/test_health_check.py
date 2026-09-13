"""主機體檢：每一項檢查都要說得出「跟什麼比」，而且不能亂報。

這組測試守兩件事：
  1. 該報的有報（每一項各一個案例）
  2. **不該報的不報**——假警報比漏報更糟，人會習慣性忽略紅燈，然後真的紅燈也被忽略
"""
import contextlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import health_check  # noqa: E402

FULL = {"physical_location": "內湖", "environment": "正式", "custodian": "王小明",
        "vm_uuid": "uuid-1", "os": "Rocky Linux 9.7"}


@contextlib.contextmanager
def _conn():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            yield conn
        finally:
            conn.close()


def _host(conn, serial="A1", hostname="web01", ip="10.99.1.1", **over):
    f = dict(FULL)
    f.update(over)
    return db.insert_hardware(conn, asset_serial=serial, hostname=hostname, ip=ip, **f)


def _scanned(conn, ip, hostname="web01", when="2026-08-24 01:00:00"):
    conn.execute("INSERT INTO scan_history (scan_time, ip, hostname, segment, scan_ok) "
                 "VALUES (?,?,?,'10.99.1.0/24',1)", (when, ip, hostname))
    conn.commit()


def _doc(conn, serial, ip="10.99.1.1"):
    conn.execute("INSERT INTO doc_archive (doc_type, file_name, file_path, asset_serial, ip) "
                 "VALUES ('需求單','a.docx','/x/a.docx',?,?)", (serial, ip))
    conn.commit()


def test_資料齊全又掃得到又有單據_全綠():
    with _conn() as conn:
        hid = _host(conn, collect_ok=1)
        _scanned(conn, "10.99.1.1")
        _doc(conn, "A1")
        ev = health_check.evaluate_all(conn)[hid]
        assert ev["machine"] == "ok" and ev["data"] == "ok", ev["issues"]
        assert ev["headline"] == ""


def test_同網段掃得到別台卻掃不到它_才算失聯():
    """關鍵是「同網段」：那個網段這次確實掃過（掃到了鄰居），偏偏沒掃到它。"""
    with _conn() as conn:
        hid = _host(conn, collect_ok=1)
        _scanned(conn, "10.99.1.250", hostname="neighbor")   # 同 /24 的別台掃得到
        _doc(conn, "A1")
        ev = health_check.evaluate_all(conn)[hid]
        assert ev["machine"] == "bad"
        lost = next(i for i in ev["issues"] if i["key"] == "lost")
        assert "最近一次掃描" in lost["basis"] and lost["action"]


def test_從來沒掃過不能說人家失聯():
    """沒有任何掃描紀錄時報失聯＝假警報，會把真正要看的機器淹掉。"""
    with _conn() as conn:
        hid = _host(conn, collect_ok=1)
        _doc(conn, "A1")
        ev = health_check.evaluate_all(conn)[hid]
        assert all(i["key"] != "lost" for i in ev["issues"])


def test_必填缺漏是資料燈不是機器燈():
    """機器好好的但沒填機房——處置是找人補資料，不是打電話問機器還在不在。"""
    with _conn() as conn:
        hid = _host(conn, collect_ok=1, physical_location="", custodian="")
        _scanned(conn, "10.99.1.1")
        _doc(conn, "A1")
        ev = health_check.evaluate_all(conn)[hid]
        assert ev["machine"] == "ok"
        assert ev["data"] == "warn"
        req = next(i for i in ev["issues"] if i["key"] == "required")
        assert "資產實體位置" in req["detail"] and "保管者" in req["detail"]


def test_同主機名同IP重複登記_資料紅燈():
    with _conn() as conn:
        h1 = _host(conn, serial="A1", collect_ok=1)
        _host(conn, serial="A2", collect_ok=1)      # 同名同 IP
        _scanned(conn, "10.99.1.1")
        _doc(conn, "A1")
        ev = health_check.evaluate_all(conn)[h1]
        assert ev["data"] == "bad"
        assert any(i["key"] == "duplicate" for i in ev["issues"])


def test_三個強識別碼都空就是身分不明():
    with _conn() as conn:
        hid = _host(conn, collect_ok=1, vm_uuid="")
        _scanned(conn, "10.99.1.1")
        _doc(conn, "A1")
        ev = health_check.evaluate_all(conn)[hid]
        ident = next(i for i in ev["issues"] if i["key"] == "identity")
        assert "vm_uuid" in ident["basis"]


def test_OS未知要報_但不能連著報EOS過保():
    """OS 都不知道了還說它過保＝憑空捏造。這兩件事要分開。"""
    with _conn() as conn:
        hid = _host(conn, collect_ok=1, os="N/A")
        _scanned(conn, "10.99.1.1")
        _doc(conn, "A1")
        ev = health_check.evaluate_all(conn)[hid]
        assert any(i["key"] == "os_unknown" for i in ev["issues"])
        assert all(i["key"] != "eos" for i in ev["issues"])


def test_沒有單據要報_但前提是系統裡確實有人在放單據():
    with _conn() as conn:
        hid = _host(conn, collect_ok=1)
        _host(conn, serial="OTHER", hostname="other01", ip="10.99.1.9", collect_ok=1)
        _doc(conn, "OTHER")          # 別台有單據＝這個模組有在用
        _scanned(conn, "10.99.1.1")
        ev = health_check.evaluate_all(conn)[hid]
        assert any(i["key"] == "doc" for i in ev["issues"])


def test_單據檔案室一筆都沒有_就不要對每台報沒單據():
    """實查 221：doc_archive 0 筆，於是 4641 台全部被報「沒有單據」。那報的是
    「這個模組還沒開始用」，不是「這台機器有問題」——純雜訊，會淹掉真的要看的。"""
    with _conn() as conn:
        hid = _host(conn, collect_ok=1)
        _scanned(conn, "10.99.1.1")
        ev = health_check.evaluate_all(conn)[hid]
        assert all(i["key"] != "doc" for i in ev["issues"])


def test_那個網段根本沒掃過_不能說它失聯():
    """實查 221：最近一次掃描只涵蓋 1 個網段共 5 台，結果 4641 台全被判失聯。
    那不是失聯，是那個網段沒被掃——要做的事完全不同（去加掃描目標 vs 去問機器還在不在）。"""
    with _conn() as conn:
        hid = _host(conn, ip="10.99.5.1", collect_ok=1)
        _scanned(conn, "10.99.0.10", hostname="somewhere-else")
        ev = health_check.evaluate_all(conn)[hid]
        assert ev["machine"] == "ok", "機器燈不該因為我們沒去掃就變紅"
        nc = next(i for i in ev["issues"] if i["key"] == "not_covered")
        assert "10.99.5.0/24" in nc["detail"] and nc["light"] == "data"
        assert all(i["key"] != "lost" for i in ev["issues"])


def test_退役資產不體檢():
    """報廢的機器本來就不該在網路上，對它報失聯是雜訊。"""
    with _conn() as conn:
        hid = _host(conn, collect_ok=1, asset_status="報廢")
        ev = health_check.evaluate_all(conn)
        assert hid not in ev


def test_summary算得出幾台全綠幾台要看():
    with _conn() as conn:
        _host(conn, serial="A1", hostname="ok01", ip="10.99.1.1", collect_ok=1)
        _doc(conn, "A1")
        _host(conn, serial="A2", hostname="bad01", ip="10.99.1.2", collect_ok=1,
              physical_location="")
        _scanned(conn, "10.99.1.1", hostname="ok01")
        _scanned(conn, "10.99.1.2", hostname="bad01")
        s = health_check.summary(conn)
        assert s["total"] == 2
        assert s["clean"] == 1 and s["needs_review"] == 1
        assert s["by_issue"]


def test_evaluate_all不寫任何東西():
    with _conn() as conn:
        _host(conn, collect_ok=1)
        before = conn.execute("SELECT count(*) FROM hardware").fetchone()[0]
        health_check.evaluate_all(conn)
        assert conn.execute("SELECT count(*) FROM hardware").fetchone()[0] == before


def test_體檢欄可以排序_要看的排最上面():
    """天條：表格每一欄都要能點排序。體檢欄不能排的話，「把要看的機器排到最上面」
    就得自己一台一台掃，這一欄等於白做。health_rank 不是資料表欄位，所以排序
    是在 Python 端做的（分頁之前），這個測試守的就是那條路徑。"""
    import api
    import auth
    from fastapi.testclient import TestClient

    with _conn() as conn:
        _host(conn, serial="OK1", hostname="ok01", ip="10.99.1.1", collect_ok=1)
        _doc(conn, "OK1")
        _host(conn, serial="BAD1", hostname="bad01", ip="10.99.1.2", collect_ok=1,
              physical_location="")          # 資料缺漏 → 要看
        _host(conn, serial="DUP1", hostname="dup01", ip="10.99.1.3", collect_ok=1)
        _host(conn, serial="DUP2", hostname="dup01", ip="10.99.1.3", collect_ok=1)  # 重複＝紅
        _scanned(conn, "10.99.1.1", hostname="ok01")
        _scanned(conn, "10.99.1.2", hostname="bad01")
        _scanned(conn, "10.99.1.3", hostname="dup01")
        db.create_user(conn, "t", auth.hash_password("pw-for-test-123"))
        conn.commit()

        def _override():
            yield conn

        api.app.dependency_overrides[api.get_db] = _override
        client = TestClient(api.app)
        try:
            assert client.post("/api/auth/login",
                               json={"username": "t", "password": "pw-for-test-123"}).status_code == 200
            rows = client.get("/api/assets", params={"sort_by": "health_rank", "order": "asc"}).json()
            serials = [r["asset_serial"] for r in rows]
            assert serials[0] in ("DUP1", "DUP2"), f"紅燈要排最前面，實際：{serials}"
            assert serials[-1] == "OK1", f"全綠的排最後，實際：{serials}"
            assert rows[-1]["health_headline"] == "" and rows[-1]["health_machine"] == "ok"
        finally:
            api.app.dependency_overrides.clear()


def test_主機詳細頁回傳體檢明細():
    """清單只夠回答「要不要看這台」，詳細頁才回答「那我到底要做什麼」——
    所以每一項都必須有 basis（跟什麼比）與 action（下一步）。"""
    import api
    import auth
    from fastapi.testclient import TestClient

    with _conn() as conn:
        _host(conn, serial="D1", hostname="d01", ip="10.99.1.1", collect_ok=1,
              physical_location="")
        _scanned(conn, "10.99.1.1", hostname="d01")
        db.create_user(conn, "t", auth.hash_password("pw-for-test-123"))
        conn.commit()

        def _override():
            yield conn

        api.app.dependency_overrides[api.get_db] = _override
        client = TestClient(api.app)
        try:
            assert client.post("/api/auth/login",
                               json={"username": "t", "password": "pw-for-test-123"}).status_code == 200
            d = client.get("/api/assets/D1").json()
            assert d["health"] and d["health"]["data"] == "warn"
            for i in d["health"]["issues"]:
                assert i["basis"] and i["action"], f"{i['key']} 少了對照基準或下一步"
        finally:
            api.app.dependency_overrides.clear()


def test_退役資產的詳細頁不給體檢而不是給全綠():
    import api
    import auth
    from fastapi.testclient import TestClient

    with _conn() as conn:
        _host(conn, serial="R1", hostname="r01", ip="10.99.1.5", collect_ok=1,
              asset_status="報廢")
        db.create_user(conn, "t", auth.hash_password("pw-for-test-123"))
        conn.commit()

        def _override():
            yield conn

        api.app.dependency_overrides[api.get_db] = _override
        client = TestClient(api.app)
        try:
            client.post("/api/auth/login", json={"username": "t", "password": "pw-for-test-123"})
            d = client.get("/api/assets/R1").json()
            assert d["health"] is None, "退役的要是 None，不能假裝全綠"
        finally:
            api.app.dependency_overrides.clear()


def test_health_summary端點():
    import api
    import auth
    from fastapi.testclient import TestClient

    with _conn() as conn:
        _host(conn, serial="S1", hostname="s01", ip="10.99.1.1", collect_ok=1)
        _scanned(conn, "10.99.1.1", hostname="s01")
        db.create_user(conn, "t", auth.hash_password("pw-for-test-123"))
        conn.commit()

        def _override():
            yield conn

        api.app.dependency_overrides[api.get_db] = _override
        client = TestClient(api.app)
        try:
            client.post("/api/auth/login", json={"username": "t", "password": "pw-for-test-123"})
            s = client.get("/api/health/summary").json()
            assert s["total"] == 1
            assert s["clean"] + s["needs_review"] == s["total"], "兩個數字加起來要等於總數"
        finally:
            api.app.dependency_overrides.clear()


# ===== 「收不到資料」拆成「真的什麼都不知道」vs「有被動來源、只是沒驗證過」 =====
#
# 2026-08-25 使用者：CIA/dynassets/RVTools 三個匯入合起來就有完整盤點，為什麼
# 沒納管的機器全部被講成「收不到資料」？——這句話原本對所有沒收集成功的機器一律
# 亮黃燈，沒有分清楚兩件事：完全沒人告訴我們這台是什麼，跟三個被動來源都已經
# 講了不少、只是沒有親自登進去驗證過。改成只有前者才算「收不到資料」的問題，
# 後者不算問題，只在 verified 欄位誠實標記。

def _link_source_record(conn, source, hardware_id):
    """模擬 dynassets/RVTools 匯入留下的來源紀錄——只需要 resolved_hardware_id
    這個判定用得到的欄位，其餘欄位塞最小值。"""
    conn.execute(
        "INSERT INTO source_record (source, source_key, payload, resolved_status, "
        "resolved_hardware_id) VALUES (?,?,?,?,?)",
        (source, f"k-{hardware_id}", "{}", "matched", hardware_id),
    )
    conn.commit()


def test_CIA登記的機器沒被SSH驗證過_不算收不到資料的問題():
    """CIA 登記本身就是一種被動來源，不該因為沒納管就被講成「什麼都不知道」。"""
    with _conn() as conn:
        hid = _host(conn, collect_ok=0)   # 沒有 collect_ok=1，模擬還沒納管
        _scanned(conn, "10.99.1.1")
        _doc(conn, "A1")
        ev = health_check.evaluate_all(conn)[hid]
        assert all(i["key"] != "collect" for i in ev["issues"]), ev["issues"]
        assert ev["verified"] is False, "沒收集成功就是沒驗證過，這個要誠實"


def test_帳外資產有dynassets來源_不算收不到資料的問題():
    with _conn() as conn:
        hid = _host(conn, serial="DYN-abc", hostname="dyn01", ip="10.99.1.1", collect_ok=0)
        _link_source_record(conn, "dynassets", hid)
        _scanned(conn, "10.99.1.1", hostname="dyn01")
        ev = health_check.evaluate_all(conn)[hid]
        assert all(i["key"] != "collect" for i in ev["issues"]), ev["issues"]
        assert ev["verified"] is False


def test_帳外資產有RVTools來源_不算收不到資料的問題():
    with _conn() as conn:
        hid = _host(conn, serial="VC-xyz", hostname="vc01", ip="10.99.1.1", collect_ok=0)
        _link_source_record(conn, "vcenter", hid)
        _scanned(conn, "10.99.1.1", hostname="vc01")
        ev = health_check.evaluate_all(conn)[hid]
        assert all(i["key"] != "collect" for i in ev["issues"]), ev["issues"]


def test_帳外資產完全沒有任何被動來源_才是真正收不到資料():
    """AUTO- 這種納管流程自己建的、CIA/dynassets/RVTools 都沒提過的，才是真的
    什麼都不知道——這才該亮黃燈。"""
    with _conn() as conn:
        hid = _host(conn, serial="AUTO-10.99.1.1", hostname=None, ip="10.99.1.1", collect_ok=0)
        _scanned(conn, "10.99.1.1")
        ev = health_check.evaluate_all(conn)[hid]
        collect_issue = next(i for i in ev["issues"] if i["key"] == "collect")
        assert "沒有任何登記/掃描來源提過這台" in collect_issue["detail"]
        assert ev["verified"] is False


def test_收集成功就是已驗證():
    with _conn() as conn:
        hid = _host(conn, collect_ok=1)
        _scanned(conn, "10.99.1.1")
        _doc(conn, "A1")
        ev = health_check.evaluate_all(conn)[hid]
        assert ev["verified"] is True


def test_summary的unverified數字反映實況_比收不到資料的問題數大很多():
    """絕大部分「未驗證」的機器其實有被動來源撐著，不該跟「收不到資料」問題數一樣大
    ——這正是這次要修的事：原本兩個數字是同一個，改完應該分開。"""
    with _conn() as conn:
        _host(conn, serial="A1", hostname="cia01", ip="10.99.1.1", collect_ok=1)
        _doc(conn, "A1")
        _host(conn, serial="A2", hostname="cia02", ip="10.99.1.2", collect_ok=0)  # CIA、未驗證
        _host(conn, serial="AUTO-10.99.1.3", hostname=None, ip="10.99.1.3", collect_ok=0)  # 真黑洞
        _scanned(conn, "10.99.1.1", hostname="cia01")
        _scanned(conn, "10.99.1.2", hostname="cia02")
        _scanned(conn, "10.99.1.3", hostname=None)
        s = health_check.summary(conn)
        assert s["unverified"] == 2          # A2 + AUTO 都沒 collect_ok=1
        assert s["by_issue"].get("收不到資料", 0) == 1   # 只有真黑洞那台算問題
