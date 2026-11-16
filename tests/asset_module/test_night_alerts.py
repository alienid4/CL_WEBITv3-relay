"""AUD-03 夜間速查「目前告警中」：後端誠實規則。
未啟用／從未輪詢 ≠ 沒有告警；過舊要標；0 筆有效來源才說沒有；歧義待核；分割區抽取；GET 不寫庫；權限。
全部假資料（10.99.x、TEST-*），不連任何外部。真實 WhatsUp 告警文字格式未驗證（只能在公司端）。"""
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import night_alerts as na  # noqa: E402
import whatsup_service as wu  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLE_AWARE_TESTS = True
NOW = datetime(2026, 11, 3, 2, 0, 0)


def ts(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    wu.ensure_tables(c)
    return c, p


def _hw(conn, serial, host, ip):
    db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location="01_甲機房", rack_no="M10/10U")


def _enable(conn, on=True):
    conn.execute("UPDATE feature_flags SET enabled=? WHERE module_key='whatsup_api'", (1 if on else 0,))
    db.set_setting(conn, "whatsup_enabled", "1" if on else "0")
    conn.commit()


def _run(conn, minutes_ago, status="ok"):
    conn.execute("INSERT INTO whatsup_runs (at, trigger, status, message) VALUES (?,?,?,?)", (ts(minutes_ago), "schedule", status, "x"))
    conn.commit()


def _alert(conn, did, name, ip, state, problem, since_min=60):
    conn.execute("INSERT INTO whatsup_alerts (device_id,name,ip,state,problem,since,group_name,first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                 (did, name, ip, state, problem, ts(since_min), "g", ts(since_min), ts(1)))
    conn.commit()


def test_來源未啟用_不是沒有告警():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _conn(tmp)
        _alert(conn, "1", "TEST-A", "10.99.1.10", "當機", "Ping")          # 舊快取還在也不能當現況
        _run(conn, 1)
        r = na.build(conn, NOW)
        assert r["status"] == "not_enabled" and r["status_label"] == "未取得"
        assert r["rows"] == [] and "不代表目前沒有告警" in r["message"] and "目前沒有告警（" not in r["message"]
        conn.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='whatsup_api'"); conn.commit()   # 模組開了、輪詢設定沒開
        assert na.build(conn, NOW)["status"] == "not_enabled"
        conn.close()


def test_從未輪詢_不是沒有告警():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _conn(tmp)
        _enable(conn)
        r = na.build(conn, NOW)
        assert r["status"] == "never" and r["status_label"] == "未取得" and r["rows"] == [] and r["data_time"] is None
        _run(conn, 3, "failed")                                           # 只有失敗過＝還是沒成功取得過
        assert na.build(conn, NOW)["status"] == "never"
        conn.close()


def test_過舊要標_且過舊的零筆不能說沒有告警():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _conn(tmp)
        _enable(conn)
        _run(conn, 45)
        r = na.build(conn, NOW)
        assert r["status"] == "stale" and r["status_label"] == "資料過舊" and r["age_min"] == 45 and r["fresh_min"] == 30
        assert "目前沒有告警" not in r["message"] and "不代表沒有告警" in r["message"]
        db.set_setting(conn, na.K_FRESH, "60"); conn.commit()              # 管理員放寬門檻 → 45 分鐘就算新鮮
        assert na.build(conn, NOW)["status"] == "ok"
        conn.close()


def test_零筆但來源有效_才說目前沒有告警():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _conn(tmp)
        _enable(conn)
        _run(conn, 5)
        r = na.build(conn, NOW)
        assert r["status"] == "ok" and r["rows"] == [] and r["counts"]["total"] == 0
        assert r["message"] == "目前沒有告警（資料時間 01:55）"
        conn.close()


def test_最近一次失敗_零筆不得說沒有告警_狀態是無法確認():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _conn(tmp)
        _enable(conn)
        _run(conn, 10, "ok")                       # 成功 → 0 筆 → 之後失敗
        _run(conn, 2, "failed")
        r = na.build(conn, NOW)
        assert r["status"] == "unconfirmed" and "無法確認" in r["status_label"] and r["last_failed"] is True
        assert "目前沒有告警" not in r["message"] and r["data_time"] == ts(10)
        _run(conn, 1, "ok")                        # 下一輪成功就恢復
        assert na.build(conn, NOW)["status"] == "ok"
        conn.close()


def test_從未成功只有失敗紀錄_last_failed為真():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _conn(tmp)
        _enable(conn); _run(conn, 3, "failed")
        r = na.build(conn, NOW)
        assert r["status"] == "never" and r["last_failed"] is True and "目前沒有告警（" not in r["message"]
        conn.close()


def test_資料時間在未來_時間異常_不得ok_也不說沒有告警():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _conn(tmp)
        _enable(conn)
        _run(conn, -60)                            # 資料時間比現在晚 60 分鐘
        r = na.build(conn, NOW)
        assert r["status"] == "clock" and "時間異常" in r["status_label"] and r["age_min"] is None
        assert "目前沒有告警" not in r["message"]
        conn.execute("DELETE FROM whatsup_runs"); conn.commit()
        _run(conn, -3)                             # 5 分鐘內的小差容忍，視為剛剛
        r = na.build(conn, NOW)
        assert r["status"] == "ok" and r["age_min"] == 0
        conn.close()


def test_ISO時區_Z與offset先換成本機時間再比():
    from datetime import timezone
    utc = NOW.astimezone().astimezone(timezone.utc)              # 把「NOW 本機時間」換成 UTC 的同一瞬間
    for s in (utc.isoformat(), utc.strftime("%Y-%m-%dT%H:%M:%SZ"), utc.isoformat().replace("+00:00", "+00:00")):
        assert na._parse(s) == NOW, s
    plus8 = NOW.astimezone().astimezone(timezone(timedelta(hours=8))).isoformat()
    assert na._parse(plus8) == NOW
    assert na._parse("2026-11-03 02:00:00") == NOW and na._parse("壞") is None and na._parse(None) is None
    with tempfile.TemporaryDirectory() as tmp:                   # 帶 +00:00 的未來 60 分鐘 → 時間異常
        conn, _ = _conn(tmp)
        _enable(conn)
        fut = (NOW + timedelta(minutes=60)).astimezone().astimezone(timezone.utc).isoformat()
        conn.execute("INSERT INTO whatsup_runs (at, trigger, status, message) VALUES (?,?,?,?)", (fut, "schedule", "ok", "x"))
        conn.commit()
        assert na.build(conn, NOW)["status"] == "clock"
        conn.close()


def test_verify區段_夜間與容量各自有section():
    import verify_numbers as vn
    vn.RESULTS.clear()
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("CREATE TABLE feature_flags (module_key TEXT, enabled INTEGER)")
    vn.check_night_alerts(c, strict=False)
    assert vn.RESULTS and all(r["sec"] == "夜間速查：目前告警中（AUD-03）" for r in vn.RESULTS)
    assert "10.3 容量分析" not in {r["sec"] for r in vn.RESULTS}
    # 容量檢查仍被 section 包裝（wrapped）：缺資料表時由 section 接住並標在自己的區段，不是拋出
    vn.RESULTS.clear()
    vn.check_capacity(c)
    assert vn.RESULTS and all(r["sec"].startswith("10.3 容量分析") for r in vn.RESULTS)
    assert vn.check_capacity.__name__ == "wrapped" and vn.check_night_alerts.__name__ == "wrapped"


def test_對回資產_歧義列待核_不猜():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _conn(tmp)
        _hw(conn, "S-1", "TEST-APP01", "10.99.1.10")
        _hw(conn, "S-3", "TEST-SHARED-A", "10.99.1.50")
        _hw(conn, "S-4", "TEST-SHARED-B", "10.99.1.50")
        _enable(conn); _run(conn, 5)
        _alert(conn, "1", "TEST-APP01", "10.99.1.10", "嚴重", "CPU")        # IP＋主機名一致 → 已對應
        _alert(conn, "2", "TEST-SHARED-A", "10.99.1.50", "警告", "Ping")    # IP 登記在兩台 → 待核
        _alert(conn, "3", "TEST-NOPE", "10.99.9.9", "警告", "Ping")         # 資產庫沒有 → 未登記
        _alert(conn, "4", "TEST-OTHER", "10.99.1.10", "警告", "Ping")       # 只有 IP 對上、名稱不符 → 待核
        r = na.build(conn, NOW)
        by = {x["name"]: x for x in r["rows"]}
        assert by["TEST-APP01"]["match"] == "matched" and by["TEST-APP01"]["machine_key"]
        assert by["TEST-SHARED-A"]["match"] == "pending" and by["TEST-SHARED-A"]["machine_key"] is None and by["TEST-SHARED-A"]["candidates"] >= 2
        assert by["TEST-NOPE"]["match"] == "none" and by["TEST-NOPE"]["machine_key"] is None
        assert by["TEST-OTHER"]["match"] == "pending" and by["TEST-OTHER"]["machine_key"] is None
        assert r["counts"] == {"total": 4, "critical": 1, "warning": 3, "unmapped": 3}
        conn.close()


def test_分割區抽取_抽不到標未取得():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _conn(tmp)
        _enable(conn); _run(conn, 5)
        _alert(conn, "1", "T1", "10.99.2.1", "警告", "Disk Free Space /var")
        _alert(conn, "2", "T2", "10.99.2.2", "警告", "Disk Free Space C:、Disk Free Space /data")
        _alert(conn, "3", "T3", "10.99.2.3", "警告", "磁碟使用率過高")          # 磁碟類但看不出哪個分割區
        _alert(conn, "4", "T4", "10.99.2.4", "警告", "Memory")
        _alert(conn, "5", "T5", "10.99.2.5", "警告", "")                      # 空白原文
        by = {x["name"]: x for x in na.build(conn, NOW)["rows"]}
        assert by["T1"]["partitions"] == "/var" and by["T1"]["categories"] == ["磁碟"]
        assert by["T2"]["partitions"] == "C:、/data"
        assert by["T3"]["partitions"] == "未取得"
        assert by["T4"]["partitions"] is None and by["T4"]["categories"] == ["記憶體"]
        assert by["T5"]["categories"] == ["未取得"] and by["T5"]["truncated"] is True
        conn.close()


def test_持續時間與排序_嚴重先_項次固定():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _conn(tmp)
        _enable(conn); _run(conn, 5)
        _alert(conn, "1", "W-OLD", "10.99.3.1", "警告", "Ping", since_min=500)
        _alert(conn, "2", "C-NEW", "10.99.3.2", "當機", "Ping", since_min=20)
        _alert(conn, "3", "C-OLD", "10.99.3.3", "嚴重", "Ping", since_min=200)
        r = na.build(conn, NOW)
        assert [x["name"] for x in r["rows"]] == ["C-OLD", "C-NEW", "W-OLD"] and [x["__no"] for x in r["rows"]] == [1, 2, 3]
        assert r["rows"][0]["duration_min"] == 200
        conn.close()


def test_GET不寫庫_唯讀連線也能跑():
    with tempfile.TemporaryDirectory() as tmp:
        conn, p = _conn(tmp)
        _hw(conn, "S-1", "TEST-APP01", "10.99.1.10")
        _enable(conn); _run(conn, 5)
        _alert(conn, "1", "TEST-APP01", "10.99.1.10", "嚴重", "Disk /var")
        conn.close()
        before = p.read_bytes()
        ro = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        r = na.build(ro, NOW)                       # 任何寫入都會丟 OperationalError
        assert r["status"] == "ok" and len(r["rows"]) == 1
        ro.close()
        assert p.read_bytes() == before


def test_沒有輪詢表的空庫_不炸_也不寫():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "e.db"
        db.init_db(p)
        c = db.get_connection(p)
        for t in ("whatsup_alerts", "whatsup_alert_history", "whatsup_runs"):
            c.execute(f"DROP TABLE IF EXISTS {t}")
        c.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='whatsup_api'")
        db.set_setting(c, "whatsup_enabled", "1"); c.commit(); c.close()
        ro = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        assert na.build(ro, NOW)["status"] == "never"
        ro.close()


# ===== API：權限 =====
def _client(tmp):
    db_path = Path(tmp) / "api.db"
    db.init_db(db_path)

    def _dep():
        c = db.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _dep
    return TestClient(api.app), db_path


def _login(client, db_path, username):
    c = db.get_connection(db_path)
    try:
        db.create_user(c, username, auth.hash_password("test-password-123"))
    finally:
        c.close()
    assert client.post("/api/auth/login", json={"username": username, "password": "test-password-123"}).status_code == 200


def test_權限_未登入401_登入可看_模組關閉不是403而是未取得_設定只有admin():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            assert client.get("/api/night/alerts").status_code == 401
            assert client.put("/api/night/alerts/settings", json={"fresh_min": 10}).status_code == 401
            _login(client, db_path, "oncall")
            r = client.get("/api/night/alerts")
            assert r.status_code == 200 and r.json()["status"] == "not_enabled" and r.json()["can_refresh"] is False
            assert r.json()["can_edit_settings"] is False
            assert client.put("/api/night/alerts/settings", json={"fresh_min": 10}).status_code == 403
            client.post("/api/auth/logout")
            _login(client, db_path, "admin")
            assert client.get("/api/night/alerts").json()["can_edit_settings"] is True
            assert client.put("/api/night/alerts/settings", json={"fresh_min": 2}).status_code == 400
            assert client.put("/api/night/alerts/settings", json={"fresh_min": 45}).json() == {"fresh_min": 45}
            assert client.get("/api/night/alerts").json()["fresh_min"] == 45
        finally:
            api.app.dependency_overrides.clear()


def test_可更新按鈕條件_啟用且admin():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            c = db.get_connection(db_path); _enable(c); c.close()
            _login(client, db_path, "oncall")
            assert client.get("/api/night/alerts").json()["can_refresh"] is False      # 既有輪詢端點只收 admin
            client.post("/api/auth/logout")
            _login(client, db_path, "admin")
            assert client.get("/api/night/alerts").json()["can_refresh"] is True
        finally:
            api.app.dependency_overrides.clear()


# ===== verify_numbers 不變式（預設觀察模式 ⚠、verify_strict_night=1 才 ❌）=====
def _verify(conn, strict):
    import verify_numbers as vn
    vn.RESULTS.clear()
    vn.check_night_alerts(conn, strict=strict)
    return [r for r in vn.RESULTS if r["page"] == "夜間速查告警"]


def test_verify_一致時全過_不一致時觀察模式只警告_strict才失敗():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _conn(tmp)
        _enable(conn); _run(conn, 1)
        _alert(conn, "1", "TEST-A", "10.99.1.10", "當機", "Ping")
        _alert(conn, "2", "TEST-B", "10.99.1.11", "警告", "Ping")
        assert all(r["ok"] is True for r in _verify(conn, True))
        orig = na.counts_of
        na.counts_of = lambda rows: {**orig(rows), "critical": 2}             # 模擬：數字磚跟清單對不上
        try:
            obs = _verify(conn, False)
            assert any(r["ok"] is None for r in obs) and not any(r["ok"] is False for r in obs)
            assert any(r["ok"] is False for r in _verify(conn, True))
        finally:
            na.counts_of = orig
        conn.close()


def test_verify_未取得時不得有列():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _conn(tmp)
        _alert(conn, "1", "TEST-A", "10.99.1.10", "當機", "Ping")             # 未啟用但有舊快取
        res = _verify(conn, True)
        assert res and all(r["ok"] is True for r in res)
        conn.close()


# ===== 結構釘子：這個功能不能自己去查 =====
def test_前端只讀快取_沒有排程_不自己打輪詢():
    import re
    fe = ROOT / "APP" / "asset-module" / "frontend"
    comp = (fe / "components" / "v2" / "NightAlerts.vue").read_text(encoding="utf-8")
    lib = (fe / "composables" / "nightAlerts.ts").read_text(encoding="utf-8")
    assert not re.search(r"setInterval|setTimeout|useIntervalFn|watchEffect", comp + lib)
    urls = set(re.findall(r"apiFetch[^(]*\(\s*['`](/api/[^'`?]+)", comp + lib))
    assert urls == {"/api/night/alerts", "/api/night/alerts/settings"}
    assert lib.count("'/api/integrations/whatsup/poll'") == 1 and "'/api/integrations/whatsup/poll" not in comp
    # 輪詢只在 refreshByHand（人按按鈕）裡，且盤中要先確認
    assert comp.count("refreshByHand(") == 1 and "confirm_in_window=true" in lib and "IN_WINDOW_CONFIRM" in lib


def test_後端模組不寫庫_不對外():
    import re
    src = (ROOT / "APP" / "asset-module" / "backend" / "night_alerts.py").read_text(encoding="utf-8")
    body = src[src.index("def build("):]
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP)\b|\.commit\(|executescript|ensure_tables|httpx|requests|urllib|socket|poll\(", body)
