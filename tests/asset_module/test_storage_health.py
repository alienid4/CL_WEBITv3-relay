"""批 S3：Dell Unity 每日唯讀健康與有效告警檢查（排程 07:00 台北、GET only、結果存本機）。

全部對 127.0.0.1 的假 Unity 伺服器測，不連任何真實位址。⚠️ 假回應依 Unity REST 公開文件的形狀手造，不是真機輸出。
涵蓋 139 的案例：AUTH-1、ALERT-1~4、PAGE-1~3、ISOLATE-1、HEALTH-1、TIME-1；另有每日去重、失敗重試上限、窗內不連線、
執行中進窗中止、viewer／登入停用 fail-closed、全程不送非 GET、沒有任何通知。
"""
import json
import sys
import tempfile
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import credential_store  # noqa: E402
import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import storage_health as sh  # noqa: E402
import storage_unity as su  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLE_AWARE_TESTS = True
PW = "unity-pw-for-health-8765"
USER = "svc_unity"


def MON(h, m=0, s=0):
    return datetime(2026, 11, 2, h, m, s)


def TUE(h, m=0, s=0):
    return datetime(2026, 11, 3, h, m, s)


def SAT(h, m=0, s=0):
    return datetime(2026, 11, 7, h, m, s)


def SUN(h, m=0, s=0):
    return datetime(2026, 11, 8, h, m, s)


def _at(t):
    qw.set_clock(lambda: t)


def A(aid, state, ack=False, sev=3, msg="msg"):
    d = {"id": aid, "severity": sev, "timestamp": "2026-11-01T01:02:03.000Z", "message": msg, "component": {"id": "sp_a"}}
    if state is not ...:
        d["state"] = state
    if ack is not ...:
        d["isAcknowledged"] = ack
    return d


class FakeUnity:
    """health：system 回的 health 區塊（None＝缺欄位）；system_fail＝system 端點狀態碼；alert_pages＝每頁一個清單；
    fail：{"alert:2": 401|"sleep"}；next_override：{頁碼: href}；on_request(path)：每個請求進來時呼叫（測試用來移動時鐘）。"""

    def __init__(self, tag="a", health=..., system_fail=None, alert_pages=None, fail=None, next_override=None, on_request=None, version="5.3.0.0"):
        self.tag, self.version = tag, version
        self.health = {"value": 5, "descriptionIds": ["ALRT_OK"], "descriptions": ["The component is operating normally."]} if health is ... else health
        self.system_fail = system_fail
        self.alert_pages = alert_pages if alert_pages is not None else [[]]
        self.fail, self.next_override, self.on_request = fail or {}, next_override or {}, on_request
        self.log = []
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                if code == 200 and self.path.partition("?")[0] != su.PATH_BASIC:      # 只有受保護 GET 成功才發 session cookie
                    self.send_header("Set-Cookie", f"sid={outer.tag}; Path=/")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self):
                import base64
                path, _, query = self.path.partition("?")
                with outer.lock:
                    outer.log.append({"method": self.command, "path": path, "query": query, "authz": bool(self.headers.get("Authorization")),
                                      "cookie": self.headers.get("Cookie", "")})
                if outer.on_request:
                    outer.on_request(path)
                if self.command != "GET":
                    return self._send(405, {"error": "read-only"})
                if path == su.PATH_BASIC:
                    return self._send(200, {"entries": [{"content": {"name": f"unity-{outer.tag}", "model": "Unity 380",
                                                                      "softwareVersion": outer.version, "apiVersion": "12.0"}}]})
                basic_ok = self.headers.get("Authorization", "") == "Basic " + base64.b64encode(f"{USER}:{PW}".encode()).decode()
                if not basic_ok and f"sid={outer.tag}" not in self.headers.get("Cookie", ""):      # 第一次 Basic 換 cookie，之後認 cookie
                    return self._send(401, {"error": {"errorCode": 401}})
                if path == su.PATH_SYSTEM:
                    if outer.system_fail:
                        return self._send(outer.system_fail, {"error": {"errorCode": outer.system_fail}})
                    c = {"id": "0", "name": f"unity-{outer.tag}", "model": "Unity 380", "serialNumber": f"FNM-{outer.tag}"}
                    if outer.health is not None:
                        c["health"] = outer.health
                    return self._send(200, {"entries": [{"content": c}]})
                if path == "/api/types/alert/instances":
                    q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                    n = int(q.get("page", "1"))
                    f = outer.fail.get(f"alert:{n}")
                    if f == "sleep":
                        time.sleep(1.5)
                    elif f:
                        return self._send(f, {"error": {"errorCode": f}})
                    data = outer.alert_pages[n - 1] if n <= len(outer.alert_pages) else []
                    body = {"entries": [{"content": c} for c in data]}
                    if n in outer.next_override:
                        body["links"] = [{"rel": "next", "href": outer.next_override[n]}]
                    elif n < len(outer.alert_pages):
                        body["links"] = [{"rel": "next", "href": f"/api/types/alert/instances?page={n + 1}&per_page=100"}]
                    return self._send(200, body)
                self._send(404, {"error": "unknown"})

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


def _setup(tmp):
    db_path = Path(tmp) / "t.db"
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    su.KEY_PATH = str(Path(tmp) / "cred.key")
    su.ALLOW_PLAINTEXT_FOR_TEST = True
    su.ALLOW_LOOPBACK_FOR_TEST = True
    conn.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='storage_api'")
    conn.commit()
    su.ensure_tables(conn)
    credential_store.save(conn, "stor-ro", "storage", USER, PW, scope="127.0.0.", key_path=su.KEY_PATH)
    return conn, db_path


def _cleanup():
    su.ALLOW_PLAINTEXT_FOR_TEST = False
    su.ALLOW_LOOPBACK_FOR_TEST = False
    su.KEY_PATH = credential_store.KEY_PATH_DEFAULT
    qw.set_clock(None)


def _add(conn, srv, name=None, **kw):
    return su.create_device(conn, "admin", name=name or f"unity-{srv.tag}", host="127.0.0.1", port=srv.port, credential="stor-ro", **kw)


def _batches(conn, device_id=None):
    q = "SELECT * FROM storage_health_batches" + (" WHERE device_id=?" if device_id else "") + " ORDER BY id"
    return [dict(r) for r in conn.execute(q, (device_id,) if device_id else ())]


def _alerts(conn, batch_id):
    return [dict(r) for r in conn.execute("SELECT * FROM storage_health_alerts WHERE batch_id=? ORDER BY id", (batch_id,))]


def _run(conn, did, **kw):
    return sh.check_device(conn, did, trigger=kw.pop("trigger", "manual"), user="admin", **kw)


# ================================================================ 139 案例
def test_AUTH1_basic_200_受保護GET_401_不算成功_也不往下打告警():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a")
        srv.alert_pages = [[A("alert_1", "Active_Auto")]]
        dev = _add(conn, srv)
        try:
            su.KEY_PATH = su.KEY_PATH
            conn.execute("UPDATE storage_devices SET credential='stor-ro' WHERE id=?", (dev["id"],))
            credential_store.save(conn, "stor-ro", "storage", USER, "wrong-password-xyz", scope="127.0.0.", key_path=su.KEY_PATH)
            r = _run(conn, dev["id"])
            assert r["ok"] is False and r["status"] == "failed" and r["code"] == "unauthorized", r
            assert r["alert_status"] == "skipped" and r["n_effective"] is None and r["n_total"] is None       # 不是 0
            assert not any(x["path"] == "/api/types/alert/instances" for x in srv.log)                       # 401 之後不再打
            assert srv.log[0]["authz"] is False                                                              # 先不帶帳密探測
            assert "wrong-password-xyz" not in json.dumps(r, ensure_ascii=False)
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_ALERT1_已讀且Active_Auto_仍是有效_已讀不減有效數():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", alert_pages=[[A("alert_1", "Active_Auto", ack=True)]])
        try:
            r = _run(conn, _add(conn, srv)["id"])
            assert r["status"] == "ok" and r["n_effective"] == 1 and r["n_acked"] == 1 and r["n_resolved"] == 0 and r["n_uncertain"] == 0
            a = _alerts(conn, r["batch_id"])[0]
            assert a["state_class"] == "effective" and a["acked"] == 1 and a["state_raw"] == "Active_Auto"      # 原始 state 文字保留
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_ALERT2_未讀但Inactive_是已解除_不冒成目前故障():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", alert_pages=[[A("alert_1", "Inactive", ack=False)]])
        try:
            r = _run(conn, _add(conn, srv)["id"])
            assert r["n_effective"] == 0 and r["n_resolved"] == 1 and r["n_acked"] == 0 and r["n_total"] == 1
            assert _alerts(conn, r["batch_id"])[0]["state_class"] == "resolved"
            assert "有效告警 0" in sh.verdict(_batches(conn)[0], datetime(2026, 11, 7, 10, 0))["text"]
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_ALERT3_Active_Manual_列有效_只讀_不送ack_delete_或任何非GET():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", alert_pages=[[A("alert_1", "Active_Manual")]])
        try:
            r = _run(conn, _add(conn, srv)["id"])
            assert r["n_effective"] == 1 and r["n_manual"] == 1
            a = _alerts(conn, r["batch_id"])[0]
            assert a["state_class"] == "effective" and a["manual"] == 1
            assert srv.log and {x["method"] for x in srv.log} == {"GET"}
            assert not any("/action/" in x["path"] or "acknowledge" in x["path"].lower() or "delete" in x["path"].lower() for x in srv.log)
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_ALERT4_Updating_未知枚舉_state缺失_數值_一律不確定_不顯示零有效或全正常():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        pg = [[A("u1", "Updating"), A("u2", "Frobnicated"), A("u3", ...), A("u4", 7)]]
        srv = FakeUnity("a", alert_pages=pg)
        try:
            r = _run(conn, _add(conn, srv)["id"])
            assert r["n_uncertain"] == 4 and r["n_effective"] == 0 and r["n_resolved"] == 0 and r["n_total"] == 4
            rows = {a["alert_id"]: a for a in _alerts(conn, r["batch_id"])}
            assert all(a["state_class"] == "uncertain" for a in rows.values())
            assert "Updating" in rows["u1"]["state_note"] and "未知" in rows["u2"]["state_note"] and "缺少" in rows["u3"]["state_note"] and "數值" in rows["u4"]["state_note"]
            assert rows["u4"]["state_raw"] == "7" and rows["u2"]["state_raw"] == "Frobnicated"             # 原始文字保留
            v = sh.verdict(_batches(conn)[0], datetime(2026, 11, 7, 10, 0))["text"]
            assert "不確定 4" in v and "全部正常" not in v and "正常" not in v.replace("不代表正常", "")
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_classify_state_單元():
    c = sh.classify_state
    assert c("Inactive")[0] == "resolved" and c("inactive")[0] == "resolved"
    assert c("Active_Auto") == ("effective", False, "") and c("ACTIVE-AUTO")[0] == "effective"
    assert c("Active_Manual") == ("effective", True, "")
    for bad in (None, "", "  ", "Updating", "weird", 0, 1, 2, 3, True, ["Active_Auto"], {"a": 1}):
        assert c(bad)[0] == "uncertain", bad
    assert sh.STATE_NUMERIC == {}                                                                              # 數值常數未驗證，不猜


def test_PAGE1_第一頁零筆但有next_第二頁有告警_繼續取頁():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", alert_pages=[[], [A("alert_9", "Active_Auto")]])
        try:
            r = _run(conn, _add(conn, srv)["id"])
            assert r["status"] == "ok" and r["n_total"] == 1 and r["n_effective"] == 1
            assert sum(1 for x in srv.log if x["path"] == "/api/types/alert/instances") == 2
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_PAGE2_第二頁401_保留第一頁為部分證據_標不完整_計數是下限不是全部():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", alert_pages=[[A("a1", "Active_Auto")], [A("a2", "Active_Auto")]], fail={"alert:2": 401})
        try:
            r = _run(conn, _add(conn, srv)["id"])
            assert r["status"] == "partial" and r["complete"] is False and r["alert_status"] == "partial"
            assert r["n_total"] == 1 and r["n_effective"] == 1
            a = _alerts(conn, r["batch_id"])
            assert len(a) == 1 and a[0]["incomplete"] == 1
            b = _batches(conn)[0]
            assert b["alert_code"] == "unauthorized"
            v = sh.verdict(b, datetime(2026, 11, 7, 10, 0))
            assert v["level"] == "incomplete" and "至少" in v["text"]
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_PAGE2_循環next_有限次停止_不無限迴圈():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", alert_pages=[[A("a1", "Active_Auto")], [A("a2", "Active_Auto")]],
                        next_override={2: "/api/types/alert/instances?page=2&per_page=100"})
        try:
            r = _run(conn, _add(conn, srv)["id"])
            assert r["status"] == "partial" and _batches(conn)[0]["alert_code"] == "page_loop" and r["n_total"] == 2
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_PAGE3_next跨主機或指向action_拒絕_列不完整_不轉送密碼():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        evil = FakeUnity("evil")
        srv = FakeUnity("a", alert_pages=[[A("a1", "Active_Auto")]], next_override={1: f"http://127.0.0.1:{evil.port}/api/types/alert/instances?page=2"})
        srv2 = FakeUnity("b", alert_pages=[[A("b1", "Active_Auto")]], next_override={1: "/api/types/alert/action/acknowledge"})
        try:
            r = _run(conn, _add(conn, srv)["id"])
            assert r["status"] == "partial" and _batches(conn, 1)[0]["alert_code"] == "bad_next" and evil.log == []        # 沒有任何請求（含密碼）送到別台
            r2 = _run(conn, _add(conn, srv2)["id"])
            assert r2["status"] == "partial" and _batches(conn, 2)[0]["alert_code"] == "bad_next"
            assert not any("action" in x["path"] for x in srv2.log) and {x["method"] for x in srv2.log} == {"GET"}
        finally:
            evil.stop(); srv.stop(); srv2.stop(); conn.close(); _cleanup()


def test_ISOLATE1_兩台都有alert_1_Cookie各自獨立_資料依設備分開不互蓋():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        sa = FakeUnity("a", alert_pages=[[A("alert_1", "Active_Auto", msg="from-A")]])
        sb = FakeUnity("b", alert_pages=[[A("alert_1", "Inactive", msg="from-B")]])
        try:
            da, db_ = _add(conn, sa), _add(conn, sb)
            ra, rb = _run(conn, da["id"]), _run(conn, db_["id"])
            assert ra["batch_id"] != rb["batch_id"]
            aa, ab = _alerts(conn, ra["batch_id"])[0], _alerts(conn, rb["batch_id"])[0]
            assert (aa["message"], aa["state_class"]) == ("from-A", "effective") and (ab["message"], ab["state_class"]) == ("from-B", "resolved")
            assert all("sid=a" not in x["cookie"] for x in sb.log)                                               # A 的 Cookie 不會帶到 B
            assert _batches(conn, da["id"])[0]["device_id"] == da["id"] and _batches(conn, db_["id"])[0]["device_id"] == db_["id"]
            d1 = sh.get_detail(conn, da["id"])
            assert [x["message"] for x in d1["alerts"]] == ["from-A"]
        finally:
            sa.stop(); sb.stop(); conn.close(); _cleanup()


def test_HEALTH1_告警成功為空_但健康GET失敗_告警空與健康未知並存_不升格全正常():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", system_fail=500, alert_pages=[[]])
        try:
            r = _run(conn, _add(conn, srv)["id"])
            assert r["status"] == "partial" and r["complete"] is False and r["ok"] is False
            assert r["alert_status"] == "ok" and r["n_total"] == 0 and r["health_status"] == "failed"
            b = _batches(conn)[0]
            v = sh.verdict(b, datetime(2026, 11, 7, 10, 0))
            assert v["level"] == "incomplete" and "健康未知" in v["text"] and "告警空不代表正常" in v["text"]
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_HEALTH1_健康欄位缺失_也是健康未知_部分成功():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", health=None, alert_pages=[[]])
        try:
            r = _run(conn, _add(conn, srv)["id"])
            assert r["status"] == "partial" and r["health_status"] == "missing"
            assert "健康未知" in sh.verdict(_batches(conn)[0], datetime(2026, 11, 7, 10, 0))["text"]
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_HEALTH1_資料過期_標過期():
    v = sh.verdict({"finished_at": "2026-11-01 07:00:05", "health_status": "read", "health_value": "5", "alert_status": "ok", "status": "ok",
                    "n_effective": 0, "n_resolved": 0, "n_uncertain": 0, "n_total": 0}, datetime(2026, 11, 3, 7, 0))
    assert v["stale"] is True and "已過期" in v["text"]
    v = sh.verdict({"finished_at": "2026-11-03 07:00:05", "health_status": "read", "health_value": "5", "alert_status": "ok", "status": "ok",
                    "n_effective": 0, "n_resolved": 0, "n_uncertain": 0, "n_total": 0}, datetime(2026, 11, 3, 9, 0))
    assert v["stale"] is False
    assert sh.verdict(None)["level"] == "none"


def test_健康原始值照存_不翻譯數值常數_不憑說明文字判故障():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", health={"value": 20, "descriptionIds": ["X"], "descriptions": ["Everything is on fire"]}, alert_pages=[[]])
        try:
            r = _run(conn, _add(conn, srv)["id"])
            assert r["status"] == "ok"                                                                          # 讀取完整；是否「故障」不由我們推定
            b = _batches(conn)[0]
            assert b["health_value"] == "20" and b["health_status"] == "read"
            d = sh.get_detail(conn, 1)["batch"]
            assert d["health_detail"]["value"] == 20 and d["health_detail"]["descriptions"] == ["Everything is on fire"]
            v = sh.verdict(b, datetime(2026, 11, 7, 10, 0))["text"]
            assert "健康原始值 20" in v and "故障" not in v and "OK" not in v
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_告警失敗時各計數是NULL_不是0():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", fail={"alert:1": 500})
        try:
            r = _run(conn, _add(conn, srv)["id"])
            assert r["status"] == "partial" and r["alert_status"] == "failed" and r["health_status"] == "read"
            b = _batches(conn)[0]
            assert b["n_total"] is None and b["n_effective"] is None and b["n_resolved"] is None and b["n_uncertain"] is None
            assert "告警未取得" in sh.verdict(b, datetime(2026, 11, 7, 10, 0))["text"]
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_全部失敗_連不上_批次failed_沒有資料():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a")
        d = _add(conn, srv)
        srv.stop()
        try:
            r = _run(conn, d["id"])
            assert r["status"] == "failed" and r["code"] in ("unreachable", "timeout") and r["n_total"] is None
            assert sh.verdict(_batches(conn)[0], datetime(2026, 11, 7, 10, 0))["level"] == "failed"
        finally:
            conn.close(); _cleanup()


# ================================================================ TIME-1 與排程
def _tick(conn, t):
    _at(t)
    return sh.tick(conn, t)


def test_TIME1_週六07點會跑_06點59不跑_同日成功後不再跑_隔天07點再一批():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", alert_pages=[[A("a1", "Active_Auto")]])
        _add(conn, srv)
        try:
            assert _tick(conn, SAT(6, 59, 59)) == 0 and srv.log == []
            assert _tick(conn, SAT(7, 0, 0)) == 1
            b = _batches(conn)
            assert len(b) == 1 and b[0]["trigger"] == "auto" and b[0]["attempt_no"] == 1 and b[0]["status"] == "ok" and b[0]["day"] == "2026-11-07"
            n = len(srv.log)
            for t in (SAT(7, 1), SAT(9, 30), SAT(23, 59, 59)):
                assert _tick(conn, t) == 0
            assert len(srv.log) == n and len(_batches(conn)) == 1                                                # 同日只成功一批
            assert _tick(conn, SUN(7, 0)) == 1 and len(_batches(conn)) == 2 and _batches(conn)[1]["day"] == "2026-11-08"   # 週日也跑
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_TIME1_失敗重試_最多3次_間隔遞增_失敗與重試分清_第四次失敗後當天不再試():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", system_fail=500, fail={"alert:1": 500})
        _add(conn, srv)
        try:
            assert _tick(conn, SAT(7, 0)) == 1
            assert _tick(conn, SAT(7, 4, 59)) == 0                                                                # 5 分鐘內不重試
            assert _tick(conn, SAT(7, 5)) == 1
            assert _tick(conn, SAT(7, 19, 59)) == 0                                                               # 第二次失敗後等 15 分鐘
            assert _tick(conn, SAT(7, 20)) == 1
            assert _tick(conn, SAT(8, 4, 59)) == 0                                                                # 第三次失敗後等 45 分鐘
            assert _tick(conn, SAT(8, 5)) == 1
            assert _tick(conn, SAT(20, 0)) == 0 and _tick(conn, SAT(23, 59)) == 0                                 # 用完（首次+3 次重試）
            b = _batches(conn)
            assert [(x["trigger"], x["attempt_no"], x["status"]) for x in b] == [("auto", 1, "failed"), ("retry", 2, "failed"), ("retry", 3, "failed"), ("retry", 4, "failed")]
            info = sh.due_info(conn, su.get_device(conn, 1), SAT(21, 0))
            assert info["due"] is False and "用完" in info["reason"]
            assert _tick(conn, SUN(7, 0)) == 1                                                                    # 隔天重新開始
            assert _batches(conn)[-1]["attempt_no"] == 1 and _batches(conn)[-1]["trigger"] == "auto"
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_TIME1_重試成功後當天停止_一天只有一個成功批次():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", system_fail=500, fail={"alert:1": 500})
        _add(conn, srv)
        try:
            assert _tick(conn, SAT(7, 0)) == 1
            srv.system_fail, srv.fail = None, {}
            assert _tick(conn, SAT(7, 5)) == 1
            assert _tick(conn, SAT(7, 40)) == 0 and _tick(conn, SAT(12, 0)) == 0
            b = _batches(conn)
            assert [x["status"] for x in b] == ["failed", "ok"] and sum(x["status"] == "ok" for x in b) == 1
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_TIME1_帳密被拒_同日不重試_隔天再試一次():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a")
        d = _add(conn, srv)
        credential_store.save(conn, "stor-ro", "storage", USER, "wrong-password-xyz", scope="127.0.0.", key_path=su.KEY_PATH)
        try:
            assert _tick(conn, SAT(7, 0)) == 1
            assert _tick(conn, SAT(7, 10)) == 0 and _tick(conn, SAT(10, 0)) == 0
            n401 = sum(1 for x in srv.log if x["path"] == su.PATH_SYSTEM)
            assert n401 == 1 and _batches(conn)[0]["code"] == "unauthorized"                                     # 只送了一次錯的帳密
            assert "不會因重試而好轉" in sh.due_info(conn, su.get_device(conn, d["id"]), SAT(10, 0))["reason"]
            assert _tick(conn, SUN(7, 0)) == 1 and len(_batches(conn)) == 2
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_TIME1_08點30邊界_29分59秒照跑_30分0秒起窗內不連線並記延後_15點整補跑():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", alert_pages=[[A("a1", "Active_Auto")]])
        _add(conn, srv)
        try:
            assert _tick(conn, MON(8, 29, 59)) == 1 and _batches(conn)[0]["status"] == "ok"                       # 窗外最後一秒：照跑
        finally:
            srv.stop(); conn.close(); _cleanup()
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", system_fail=500, fail={"alert:1": 500})
        _add(conn, srv)
        try:
            assert _tick(conn, MON(8, 25, 0)) == 1 and _batches(conn)[0]["status"] == "failed"                    # 重試在 08:30:00 到期
            n = len(srv.log)
            assert _tick(conn, MON(8, 29, 59)) == 0 and len(srv.log) == n                                         # 還沒到期
            assert _tick(conn, MON(8, 30, 0)) == 0 and len(srv.log) == n                                          # 到期但進窗：不連線
            assert _tick(conn, MON(14, 59, 59)) == 0 and len(srv.log) == n
            row = conn.execute("SELECT count FROM quiet_window_log WHERE entry='storage_health_tick' AND kind='deferred'").fetchone()
            assert row and row["count"] == 2                                                                      # 記了延後
            srv.system_fail, srv.fail = None, {}
            assert _tick(conn, MON(15, 0, 0)) == 1 and len(srv.log) > n                                           # 15:00:00 窗外第一個 tick 補
            b = _batches(conn)
            assert [(x["trigger"], x["status"], x["day"]) for x in b] == [("auto", "failed", "2026-11-02"), ("retry", "ok", "2026-11-02")]
            assert _tick(conn, MON(15, 1)) == 0
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_TIME1_伺服器07點沒起來_週間盤中補不了_窗外當天補一次():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a")
        _add(conn, srv)
        try:
            assert _tick(conn, MON(10, 0)) == 0 and srv.log == []                                                 # 從沒跑過、現在盤中：不動
            assert _tick(conn, TUE(6, 59)) == 0
            assert _tick(conn, MON(15, 0)) == 1 and len(_batches(conn)) == 1 and _batches(conn)[0]["day"] == "2026-11-02"
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_執行中進入靜默窗_中止_不寫批次_不算失敗_窗外下一個tick補():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        state = {"n": 0}

        def flip(path):
            state["n"] += 1
            if path == su.PATH_BASIC:
                _at(MON(8, 30, 0))                                                                                # 探測完成後時鐘進入窗內

        srv = FakeUnity("a", on_request=flip, alert_pages=[[A("a1", "Active_Auto")]])
        _add(conn, srv)
        try:
            _at(MON(8, 29, 58))
            assert sh.tick(conn, MON(8, 29, 58)) == 0
            assert _batches(conn) == []                                                                           # 沒有批次＝也沒有失敗紀錄
            assert [x["path"] for x in srv.log] == [su.PATH_BASIC]                                                # 進窗後不再送新請求（尤其不送帳密）
            assert srv.log[0]["authz"] is False
            srv.on_request = None
            assert _tick(conn, MON(15, 0)) == 1
            b = _batches(conn)
            assert len(b) == 1 and b[0]["trigger"] == "auto" and b[0]["attempt_no"] == 1 and b[0]["status"] == "ok"
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_窗內tick完全不連線_模組關閉_設備停用_沒憑證_都不跑():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a")
        d = _add(conn, srv)
        try:
            assert _tick(conn, MON(10, 0)) == 0 and srv.log == []
            conn.execute("UPDATE storage_devices SET enabled=0 WHERE id=?", (d["id"],)); conn.commit()
            assert _tick(conn, SAT(7, 0)) == 0 and srv.log == []
            conn.execute("UPDATE storage_devices SET enabled=1, credential='' WHERE id=?", (d["id"],)); conn.commit()
            assert _tick(conn, SAT(7, 0)) == 0 and srv.log == []
            assert sh.schedule_info(conn, su.get_device(conn, d["id"]), SAT(7, 0))["included"] is False
            conn.execute("UPDATE storage_devices SET credential='no-such-cred' WHERE id=?", (d["id"],)); conn.commit()
            assert _tick(conn, SAT(7, 0)) == 0 and "不存在" in sh.schedule_info(conn, su.get_device(conn, d["id"]), SAT(7, 0))["reason"]
            conn.execute("UPDATE storage_devices SET credential='stor-ro' WHERE id=?", (d["id"],))
            conn.execute("UPDATE feature_flags SET enabled=0 WHERE module_key='storage_api'"); conn.commit()
            assert _tick(conn, SAT(7, 0)) == 0 and srv.log == []                                                  # 模組關閉
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_多台_一個tick一台_逐台隔離_一台壞不拖累另一台():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        bad = FakeUnity("bad", system_fail=500, fail={"alert:1": 500})
        good = FakeUnity("good", alert_pages=[[A("g1", "Active_Auto")]])
        _add(conn, bad); _add(conn, good)
        try:
            assert _tick(conn, SAT(7, 0)) == 1                                                                    # 第一台（壞的）
            assert _tick(conn, SAT(7, 1)) == 1                                                                    # 壞的在等重試 → 輪到第二台
            b = _batches(conn)
            assert [(x["device_id"], x["status"]) for x in b] == [(1, "failed"), (2, "ok")]
            assert all("sid=bad" not in x["cookie"] for x in good.log)
        finally:
            bad.stop(); good.stop(); conn.close(); _cleanup()


def test_人為立即檢查成功_當天自動就不再跑_但人為批次不計入自動的重試次數():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a")
        d = _add(conn, srv)
        try:
            r = _run(conn, d["id"], now=SAT(6, 0))
            assert r["status"] == "ok" and _batches(conn)[0]["trigger"] == "manual"
            n = len(srv.log)
            assert _tick(conn, SAT(7, 0)) == 0 and len(srv.log) == n
            # 先有自動失敗、再有人為成功 → 後續不再自動
            srv2 = FakeUnity("b", system_fail=500, fail={"alert:1": 500})
            d2 = _add(conn, srv2)
            conn.execute("UPDATE storage_devices SET enabled=0 WHERE id=?", (d["id"],)); conn.commit()
            assert _tick(conn, SUN(7, 0)) == 1
            srv2.system_fail, srv2.fail = None, {}
            assert _run(conn, d2["id"], now=SUN(7, 2))["status"] == "ok"
            assert _tick(conn, SUN(7, 30)) == 0
            assert [x["trigger"] for x in _batches(conn, d2["id"])] == ["auto", "manual"]
            srv2.stop()
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_設定在檢查進行中被改_舊結果不寫入():
    with tempfile.TemporaryDirectory() as tmp:
        conn, db_path = _setup(tmp)
        srv = FakeUnity("a")
        d = _add(conn, srv)

        def edit(path):
            if path == su.PATH_SYSTEM:
                c2 = db.get_connection(db_path)
                c2.execute("UPDATE storage_devices SET host='127.0.0.1', port=? WHERE id=?", (srv.port + 1, d["id"])); c2.commit(); c2.close()

        srv.on_request = edit
        try:
            r = _run(conn, d["id"])
            assert r.get("stale") is True and r["batch_id"] is None and _batches(conn) == []
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_保留批次數與端點變更清除舊結果():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", alert_pages=[[A("a1", "Active_Auto")]])
        d = _add(conn, srv)
        old_k, old_a = sh.KEEP_BATCHES, sh.KEEP_ALERT_BATCHES
        try:
            sh.KEEP_BATCHES, sh.KEEP_ALERT_BATCHES = 3, 2
            for _ in range(5):
                _run(conn, d["id"])
            b = _batches(conn)
            assert len(b) == 3
            with_alerts = [x["id"] for x in b if _alerts(conn, x["id"])]
            assert len(with_alerts) == 2 and with_alerts == [x["id"] for x in b][-2:]                              # 只有最近 2 批留明細
            assert sh.get_detail(conn, d["id"])["batch"]["id"] == b[-1]["id"]
            su.update_device(conn, d["id"], name=d["name"], host="127.0.0.1", port=srv.port + 5, credential="stor-ro")
            assert _batches(conn) == []                                                                           # 端點換了：舊結果不再顯示成這台的現況
            assert su.delete_device(conn, d["id"]) and conn.execute("SELECT COUNT(*) FROM storage_health_alerts").fetchone()[0] == 0
        finally:
            sh.KEEP_BATCHES, sh.KEEP_ALERT_BATCHES = old_k, old_a
            srv.stop(); conn.close(); _cleanup()


def test_沒有通知_沒有非GET_沒有執行緒_原始碼守門():
    src = (ROOT / "APP" / "asset-module" / "backend" / "storage_health.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    for bad in ('"POST"', '"PUT"', '"DELETE"', '"PATCH"', ".post(", ".put(", ".delete(", ".patch(", "Thread(", "Timer(",
                "teams_notify", "smtp", "sendmail", "import notify", "send_mail", "webhook", "/acknowledge", "acknowledge(", "/action/"):
        assert bad.lower() not in code.lower().replace("threading.lock", ""), bad
    sched = (ROOT / "APP" / "asset-module" / "backend" / "scan_service.py").read_text(encoding="utf-8")
    fn = sched[sched.index("def _storage_health_tick"):sched.index("def _paste_watch_tick")]
    assert "storage_health.tick" in fn and "teams" not in fn.lower() and "mail" not in fn.lower()
    assert qw.ENTRIES["storage_health_tick"]["cls"] == "A" and qw.TICK_SITES["storage_health.py:tick"] == "storage_health_tick"
    assert qw.SCHEDULER_CALLS["_storage_health_tick"] == "storage_health_tick" and qw.STAGGER["storage"] >= 1


# ================================================================ API
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
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, username, auth.hash_password("test-password-123"), "admin" if username == "admin" else "user")
    finally:
        conn.close()
    assert client.post("/api/auth/login", json={"username": username, "password": "test-password-123"}).status_code == 200


def test_API_viewer不能立即檢查_窗內無確認409_確認後放行_明細唯讀_列表帶最近檢查():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        su.KEY_PATH = str(Path(tmp) / "cred.key")
        su.ALLOW_PLAINTEXT_FOR_TEST = True
        su.ALLOW_LOOPBACK_FOR_TEST = True
        srv = FakeUnity("api", alert_pages=[[A("a1", "Active_Auto"), A("a2", "Inactive")]])
        try:
            c = db.get_connection(db_path)
            c.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='storage_api'"); c.commit()
            credential_store.save(c, "stor-ro", "storage", USER, PW, scope="127.0.0.", key_path=su.KEY_PATH); c.close()
            assert client.post("/api/storage/devices/1/health-check").status_code == 401
            _login(client, db_path, "oncall")
            assert client.post("/api/storage/devices/1/health-check").status_code == 403
            client.post("/api/auth/logout")
            _login(client, db_path, "admin")
            did = client.post("/api/storage/devices", json=dict(name="api", host="127.0.0.1", port=srv.port, credential="stor-ro")).json()["id"]
            assert client.post("/api/storage/devices/999/health-check").status_code == 404
            _at(MON(10, 0))
            r = client.post(f"/api/storage/devices/{did}/health-check")
            assert r.status_code == 409 and r.json()["detail"]["code"] == "quiet_window_confirm" and srv.log == []
            r = client.post(f"/api/storage/devices/{did}/health-check?confirm_in_window=true")
            assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["trigger"] == "manual" and PW not in r.text
            _at(SAT(10, 0))
            assert client.post(f"/api/storage/devices/{did}/health-check").status_code == 200                  # 窗外直接放行
            lst = client.get("/api/storage/devices").json()["rows"][0]
            assert lst["last_health"]["n_effective"] == 1 and lst["last_health"]["n_resolved"] == 1 and "verdict" in lst["last_health"]
            assert lst["health_schedule"]["included"] is True
            det = client.get(f"/api/storage/devices/{did}/health").json()
            assert det["batch"]["status"] == "ok" and len(det["alerts"]) == 2 and det["alerts"][0]["__no"] == 1 and det["daily_at"] == "07:00"
            assert len(det["history"]) == 2
            assert client.get("/api/storage/devices/999/health").status_code == 404
            assert client.get(f"/api/storage/devices/{did}/health?batch_id=99999").status_code == 404
            client.post("/api/auth/logout")
            _login(client, db_path, "viewer2")
            assert client.get(f"/api/storage/devices/{did}/health").status_code == 200                           # 明細唯讀：一般帳號可看
            assert client.post(f"/api/storage/devices/{did}/health-check").status_code == 403
            assert {x["method"] for x in srv.log} == {"GET"}
        finally:
            qw.set_clock(None)
            srv.stop(); api.app.dependency_overrides.clear(); _cleanup()


def test_API_登入停用時立即檢查fail_closed_模組關閉時明細與檢查都403():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path, "admin")
            assert client.get("/api/storage/devices/1/health").status_code == 403
            assert client.post("/api/storage/devices/1/health-check").status_code == 403
            c = db.get_connection(db_path)
            c.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='storage_api'"); c.commit(); c.close()
            import _app_shared
            orig = _app_shared.auth_disabled
            try:
                import routers.storage as rs
                rs.auth_disabled = lambda conn: True
                r = client.post("/api/storage/devices/1/health-check")
                assert r.status_code == 403 and "登入停用" in r.text
            finally:
                rs.auth_disabled = orig
        finally:
            api.app.dependency_overrides.clear(); _cleanup()


def test_未預期例外後冷卻_不每分鐘重連():
    import storage_health as sh
    assert sh.EXC_COOLDOWN_MIN >= 30 and isinstance(sh._EXC_UNTIL, dict)
