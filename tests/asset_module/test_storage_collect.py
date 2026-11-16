"""批 S2：Dell Unity 人為資料收集（GET only，結果存本機）。

全部對 127.0.0.1 的假 Unity 伺服器測，不連任何真實位址。⚠️ 假回應依 Unity REST 公開文件的形狀手造，不是真機輸出。
涵蓋：收集成功、部分成功（第二頁 401／逾時）、失敗不當零筆、兩台同物件 id 不互蓋、歧義列待核、
窗內無票證拒絕、viewer 403、全程不送任何非 GET、設定被改時舊結果不寫入。
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
import storage_collect as sc  # noqa: E402
import storage_unity as su  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLE_AWARE_TESTS = True
PW = "unity-pw-for-collect-4321"
USER = "svc_unity"
PER = 2        # 假伺服器每頁筆數（小一點，才好測分頁）


def _std_types(tag="a"):
    """標準資料集。物件 id 刻意兩台一樣（pool_1 / res_1 …），測不互蓋。"""
    return {
        "pool": [{"id": "pool_1", "name": f"{tag}-pool1", "sizeTotal": 1000, "sizeUsed": 400, "sizeFree": 600, "sizeSubscribed": 700}],
        "lun": [{"id": "res_1", "name": f"{tag}-lun1", "wwn": f"60:{tag}:1", "sizeTotal": 100, "sizeUsed": 10, "sizeAllocated": 20, "pool": {"id": "pool_1"}},
                {"id": "res_2", "name": f"{tag}-lun2", "wwn": f"60:{tag}:2", "sizeTotal": 200, "sizeUsed": 50, "sizeAllocated": 60, "pool": {"id": "pool_1"}},
                {"id": "res_3", "name": f"{tag}-lun3", "wwn": f"60:{tag}:3", "sizeTotal": 300, "sizeUsed": 80, "sizeAllocated": 90, "pool": {"id": "pool_1"}}],
        "host": [{"id": "Host_1", "name": f"{tag}-esx1", "description": "", "osType": "ESXi", "type": 1}],
        "hostInitiator": [{"id": "HostInitiator_1", "initiatorId": f"20:00:{tag}:01", "type": 1, "parentHost": {"id": "Host_1"}}],
        "hostLUN": [{"id": "HostLUN_1", "host": {"id": "Host_1"}, "lun": {"id": "res_1"}, "hlu": 1, "type": 1},
                    {"id": "HostLUN_2", "host": {"id": "Host_1"}, "lun": {"id": "res_2"}, "hlu": 2, "type": 1}],
    }


class FakeUnity:
    """cfg: types（型別→筆數清單）、fail（"lun:2"→狀態碼；"lun:2"→"sleep" 逾時）、auth。log 記每個請求。"""

    def __init__(self, tag="a", serial="FNM-A", types=None, fail=None, auth_ok=(USER, PW), version="5.3.0.0"):
        self.tag, self.serial, self.version = tag, serial, version
        self.types = types if types is not None else _std_types(tag)
        self.fail = fail or {}
        self.auth_ok = auth_ok
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
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self):
                import base64
                path, _, query = self.path.partition("?")
                with outer.lock:
                    outer.log.append({"method": self.command, "path": path, "query": query, "authz": bool(self.headers.get("Authorization"))})
                if self.command != "GET":
                    return self._send(405, {"error": "read-only"})
                if path == su.PATH_BASIC:
                    return self._send(200, {"entries": [{"content": {"name": f"unity-{outer.tag}", "model": "Unity 380",
                                                                      "softwareVersion": outer.version, "apiVersion": "12.0"}}]})
                exp = "Basic " + base64.b64encode(f"{outer.auth_ok[0]}:{outer.auth_ok[1]}".encode()).decode()
                if self.headers.get("Authorization", "") != exp:
                    return self._send(401, {"error": {"errorCode": 401}})
                if path == su.PATH_SYSTEM:
                    return self._send(200, {"entries": [{"content": {"id": "0", "name": f"unity-{outer.tag}", "model": "Unity 380",
                                                                      "serialNumber": outer.serial}}]})
                if path.startswith("/api/types/") and path.endswith("/instances"):
                    t = path.split("/")[3]
                    q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                    n = int(q.get("page", "1"))
                    f = outer.fail.get(f"{t}:{n}")
                    if f == "sleep":
                        time.sleep(1.5)
                    elif f:
                        return self._send(f, {"error": {"errorCode": f}})
                    data = outer.types.get(t)
                    if data is None:
                        return self._send(404, {"error": "no such type"})
                    chunk = data[(n - 1) * PER: n * PER]
                    body = {"entries": [{"content": c} for c in chunk]}
                    if n * PER < len(data):
                        body["links"] = [{"rel": "next", "href": f"{path}?page={n + 1}&per_page=100"}]
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


def _add(conn, srv, name=None, **kw):
    return su.create_device(conn, "admin", name=name or f"unity-{srv.tag}", host="127.0.0.1", port=srv.port, credential="stor-ro", **kw)


def _items(conn, run_id, kind=None):
    q = "SELECT * FROM storage_collect_items WHERE run_id = ?" + (" AND kind = ?" if kind else "") + " ORDER BY id"
    return [dict(r) for r in conn.execute(q, (run_id, kind) if kind else (run_id,))]


# ---------------------------------------------------------------- 收集成功
def test_收集成功_身份Pool_LUN_host_initiator_映射都存下_分頁讀完整():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a")
        try:
            r = sc.collect_device(conn, _add(conn, srv)["id"], "admin")
            assert r["ok"] is True and r["status"] == "ok" and r["complete"] is True, r
            assert r["identity"] == {"name": "unity-a", "model": "Unity 380", "serial": "FNM-A", "os_version": "5.3.0.0", "api_version": "12.0"}
            assert {s["key"]: (s["status"], s["rows"]) for s in r["sections"]} == {
                "system": ("ok", 1), "pool": ("ok", 1), "lun": ("ok", 3), "host": ("ok", 1), "initiator": ("ok", 1), "host_lun": ("ok", 2)}
            luns = _items(conn, r["run_id"], "lun")
            assert [x["name"] for x in luns] == ["a-lun1", "a-lun2", "a-lun3"]        # 3 筆跨 2 頁（每頁 2）
            assert luns[0]["size_total"] == 100 and luns[0]["parent"] == "pool_1" and luns[0]["review"] == ""
            pool = _items(conn, r["run_id"], "pool")[0]
            assert (pool["size_total"], pool["size_used"], pool["size_free"]) == (1000, 400, 600)
            maps = _items(conn, r["run_id"], "host_lun")
            assert [(m["parent"], m["name"]) for m in maps] == [("a-esx1", "a-lun1"), ("a-esx1", "a-lun2")] and all(m["review"] == "" for m in maps)
            assert r["review_count"] == 0 and r["item_count"] == 1 + 1 + 3 + 1 + 1 + 2
            row = conn.execute("SELECT * FROM storage_collect_runs WHERE id=?", (r["run_id"],)).fetchone()
            assert row["complete"] == 1 and row["collected_by"] == "admin" and row["finished_at"]
            assert PW not in json.dumps([dict(x) for x in conn.execute("SELECT * FROM storage_collect_runs")], ensure_ascii=False)
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_只發GET_沒有任何寫入或action():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a")
        try:
            sc.collect_device(conn, _add(conn, srv)["id"], "admin")
            assert srv.log and {x["method"] for x in srv.log} == {"GET"}
            assert not any("/action/" in x["path"] for x in srv.log)
            assert not srv.log[0]["authz"]                       # 先不帶帳密探測位址
        finally:
            srv.stop(); conn.close(); _cleanup()
    src = (ROOT / "APP" / "asset-module" / "backend" / "storage_collect.py").read_text(encoding="utf-8")
    for bad in ('"POST"', '"PUT"', '"DELETE"', '"PATCH"', ".post(", ".put(", ".delete(", ".patch(", "Thread(", "Timer("):
        assert bad not in src, bad


# ---------------------------------------------------------------- 部分成功／失敗
def test_部分成功_LUN第二頁401_已讀的頁保留且標不完整_後續區段略過_不當零筆():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", fail={"lun:2": 401})
        try:
            r = sc.collect_device(conn, _add(conn, srv)["id"], "admin")
            assert r["status"] == "partial" and r["complete"] is False and r["ok"] is False
            st = {s["key"]: s for s in r["sections"]}
            assert st["pool"]["status"] == "ok"
            assert st["lun"]["status"] == "partial" and st["lun"]["rows"] == 2 and st["lun"]["code"] == "unauthorized"
            assert all(st[k]["status"] == "skipped" for k in ("host", "initiator", "host_lun"))
            luns = _items(conn, r["run_id"], "lun")
            assert [x["name"] for x in luns] == ["a-lun1", "a-lun2"] and all(x["incomplete"] == 1 for x in luns)
            assert _items(conn, r["run_id"], "host") == []               # 略過≠零筆：靠 sections 狀態區分
            assert "不完整" in r["message"] and "LUN" in r["message"]
            assert len([x for x in srv.log if x["path"].endswith("/host/instances")]) == 0       # 401 後不再打其他區段（避免鎖帳號）
            assert len([x for x in srv.log if "/lun/" in x["path"] and "page=2" in x["query"]]) == 1    # 401 不重試
            det = sc.get_detail(conn, 1)
            assert det["run"]["complete"] is False and det["latest"]["id"] == r["run_id"]
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_部分成功_第二頁逾時():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", fail={"lun:2": "sleep"})
        old = su.TIMEOUT_S
        su.TIMEOUT_S = 0.3
        try:
            r = sc.collect_device(conn, _add(conn, srv)["id"], "admin")
            st = {s["key"]: s for s in r["sections"]}
            assert r["status"] == "partial" and st["lun"]["status"] == "partial" and st["lun"]["code"] == "timeout"
            assert st["lun"]["rows"] == 2 and st["host_lun"]["status"] == "skipped"
        finally:
            su.TIMEOUT_S = old
            srv.stop(); conn.close(); _cleanup()


def test_單一區段非驗證類失敗_其餘區段照收_該區段標失敗而非零筆():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a", fail={"host:1": 500})
        try:
            r = sc.collect_device(conn, _add(conn, srv)["id"], "admin")
            st = {s["key"]: s for s in r["sections"]}
            assert r["status"] == "partial"
            assert st["host"]["status"] == "failed" and st["host"]["code"] == "http_error" and st["host"]["rows"] == 0
            assert st["lun"]["status"] == "ok" and st["host_lun"]["status"] == "ok"
            # Host 清單不完整 → 映射不能確認 host，列待核而不是當成「沒有 host」
            maps = _items(conn, r["run_id"], "host_lun")
            assert maps and all("Host 清單不完整" in m["review"] for m in maps)
            assert r["review_count"] >= 2
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_帳密被拒_整次失敗_寫失敗紀錄_上一批好資料仍在():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a")
        try:
            dev = _add(conn, srv)
            ok = sc.collect_device(conn, dev["id"], "admin")
            srv.auth_ok = ("someone", "else")
            bad = sc.collect_device(conn, dev["id"], "admin")
            assert bad["status"] == "failed" and bad["code"] == "unauthorized" and bad["item_count"] == 0 and bad["identity"] is None
            assert len([x for x in srv.log if x["path"] == su.PATH_SYSTEM and x["authz"]]) == 2      # 失敗那次只打一次 system（不重試）
            det = sc.get_detail(conn, dev["id"])
            assert det["run"]["id"] == ok["run_id"] and det["latest"]["id"] == bad["run_id"] and det["latest"]["status"] == "failed"
            assert len(det["rows"]) == ok["item_count"]
            last = sc.latest_runs(conn)[dev["id"]]
            assert last["status"] == "failed" and last["code"] == "unauthorized"
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_沒選憑證_不連線_仍記失敗原因():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a")
        try:
            dev = su.create_device(conn, "admin", name="nocred", host="127.0.0.1", port=srv.port, credential="")
            r = sc.collect_device(conn, dev["id"], "admin")
            assert r["status"] == "failed" and r["code"] == "credential" and srv.log == []
        finally:
            srv.stop(); conn.close(); _cleanup()


# ---------------------------------------------------------------- 隔離
def test_兩台同物件id_不互蓋_各自一批():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        a, b = FakeUnity("a", serial="SN-A"), FakeUnity("b", serial="SN-B")
        try:
            da, db_ = _add(conn, a), _add(conn, b)
            ra, rb = sc.collect_device(conn, da["id"], "admin"), sc.collect_device(conn, db_["id"], "admin")
            assert ra["run_id"] != rb["run_id"]
            assert [x["name"] for x in _items(conn, ra["run_id"], "pool")] == ["a-pool1"]
            assert [x["name"] for x in _items(conn, rb["run_id"], "pool")] == ["b-pool1"]
            assert _items(conn, ra["run_id"], "lun")[0]["obj_id"] == _items(conn, rb["run_id"], "lun")[0]["obj_id"] == "res_1"       # 同 id
            assert ra["identity"]["serial"] == "SN-A" and rb["identity"]["serial"] == "SN-B"
            # 再收 A 一次：B 的資料不動
            sc.collect_device(conn, da["id"], "admin")
            assert sc.get_detail(conn, db_["id"])["run"]["id"] == rb["run_id"]
            assert {r["name"] for r in sc.get_detail(conn, db_["id"])["rows"] if r["kind"] == "pool"} == {"b-pool1"}
            # 每台 session 獨立：A 的探測請求都只打 A、B 只打 B
            assert all(x["path"] for x in a.log) and a.log and b.log
        finally:
            a.stop(); b.stop(); conn.close(); _cleanup()


def test_歧義列待核_不自動合併或刪除():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        types = _std_types("a")
        types["lun"].append({"id": "res_9", "name": "a-lun1", "wwn": "60:a:1", "sizeTotal": 5, "sizeUsed": 9, "sizeAllocated": 1, "pool": {"id": "pool_X"}})     # 同名、同 WWN、Pool 不存在、已用>總量
        types["hostInitiator"].append({"id": "HostInitiator_2", "initiatorId": "20:00:a:01", "type": 1, "parentHost": {"id": "Host_9"}})     # 同 initiator 掛不存在的 host
        types["hostLUN"].append({"id": "HostLUN_3", "host": {"id": "Host_1"}, "lun": {"id": "res_404"}, "hlu": 3, "type": 1})
        srv = FakeUnity("a", types=types)
        try:
            r = sc.collect_device(conn, _add(conn, srv)["id"], "admin")
            assert r["status"] == "ok"                      # 資料收齊；歧義不等於失敗
            luns = {x["obj_id"]: x for x in _items(conn, r["run_id"], "lun")}
            assert "LUN 名稱重複" in luns["res_9"]["review"] and "WWN 重複" in luns["res_9"]["review"]
            assert "Pool 在已收 Pool 清單中找不到" in luns["res_9"]["review"] and "已用容量大於總容量" in luns["res_9"]["review"]
            assert "LUN 名稱重複" in luns["res_1"]["review"]
            assert len(luns) == 4                           # 沒有任何一筆被合併或刪掉
            inits = _items(conn, r["run_id"], "initiator")
            assert "找不到" in inits[1]["review"] and all("多個 Host" in i["review"] for i in inits)
            maps = {x["obj_id"]: x for x in _items(conn, r["run_id"], "host_lun")}
            assert "LUN 在已收 LUN 清單中找不到" in maps["HostLUN_3"]["review"] and maps["HostLUN_1"]["review"] == ""
            assert r["review_count"] >= 4
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_收集進行中改設定_舊結果不寫入():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a")
        try:
            d = _add(conn, srv)
            real = su.UnitySession.get

            def slow_get(self, *a, **kw):
                su.update_device(conn, d["id"], name=d["name"], host="10.99.0.56", port=srv.port, credential="stor-ro")
                return real(self, *a, **kw)
            su.UnitySession.get = slow_get
            try:
                r = sc.collect_device(conn, d["id"], "admin")
            finally:
                su.UnitySession.get = real
            assert r.get("stale") is True and r["ok"] is False and r["run_id"] is None
            assert conn.execute("SELECT COUNT(*) FROM storage_collect_runs").fetchone()[0] == 0
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_只保留最近N次_改端點或刪設備會清掉收集結果():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a")
        old = sc.KEEP_RUNS
        sc.KEEP_RUNS = 2
        try:
            d = _add(conn, srv)
            for _ in range(4):
                sc.collect_device(conn, d["id"], "admin")
            assert conn.execute("SELECT COUNT(*) FROM storage_collect_runs").fetchone()[0] == 2
            n_items = conn.execute("SELECT COUNT(*) FROM storage_collect_items").fetchone()[0]
            assert n_items == 2 * 9
            su.update_device(conn, d["id"], name=d["name"], host="127.0.0.1", port=srv.port, note="只改備註")
            assert conn.execute("SELECT COUNT(*) FROM storage_collect_runs").fetchone()[0] == 2
            su.update_device(conn, d["id"], name=d["name"], host="127.0.0.2", port=srv.port)
            assert conn.execute("SELECT COUNT(*) FROM storage_collect_runs").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM storage_collect_items").fetchone()[0] == 0
            su.update_device(conn, d["id"], name=d["name"], host="127.0.0.1", port=srv.port)
            sc.collect_device(conn, d["id"], "admin")
            assert su.delete_device(conn, d["id"]) is True
            assert conn.execute("SELECT COUNT(*) FROM storage_collect_runs").fetchone()[0] == 0
        finally:
            sc.KEEP_RUNS = old
            srv.stop(); conn.close(); _cleanup()


def test_容量欄位非整數_不採用並列待核():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        types = _std_types("a")
        types["pool"][0]["sizeTotal"] = "1000"        # 字串，不猜轉換
        srv = FakeUnity("a", types=types)
        try:
            r = sc.collect_device(conn, _add(conn, srv)["id"], "admin")
            p = _items(conn, r["run_id"], "pool")[0]
            assert p["size_total"] is None and "容量欄位不是非負整數" in p["review"]
        finally:
            srv.stop(); conn.close(); _cleanup()


# ---------------------------------------------------------------- API
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


def test_API_viewer不能收_admin窗外可收_窗內無票證409_帶確認放行_明細唯讀():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        su.KEY_PATH = str(Path(tmp) / "cred.key")
        su.ALLOW_PLAINTEXT_FOR_TEST = True
        su.ALLOW_LOOPBACK_FOR_TEST = True
        srv = FakeUnity("api")
        try:
            c = db.get_connection(db_path)
            c.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='storage_api'"); c.commit()
            credential_store.save(c, "stor-ro", "storage", USER, PW, scope="127.0.0.", key_path=su.KEY_PATH); c.close()
            assert client.post("/api/storage/devices/1/collect").status_code == 401
            _login(client, db_path, "oncall")
            r = client.post("/api/storage/devices/1/collect")
            assert r.status_code == 403, r.text                                          # 非 admin
            client.post("/api/auth/logout")
            _login(client, db_path, "admin")
            did = client.post("/api/storage/devices", json=dict(name="api", host="127.0.0.1", port=srv.port, credential="stor-ro")).json()["id"]
            assert client.post("/api/storage/devices/999/collect").status_code == 404

            qw.set_clock(lambda: datetime(2026, 11, 2, 10, 0))                           # 週一 10:00 盤中
            r = client.post(f"/api/storage/devices/{did}/collect")
            assert r.status_code == 409 and r.json()["detail"]["code"] == "quiet_window_confirm"
            assert srv.log == []                                                          # 沒票證：一個請求都沒送
            r = client.post(f"/api/storage/devices/{did}/collect?confirm_in_window=true")
            assert r.status_code == 200 and r.json()["status"] == "ok" and PW not in r.text
            n_after_ok = len(srv.log)
            qw.set_clock(lambda: datetime(2026, 11, 7, 10, 0))                           # 週六：窗外直接放行
            r = client.post(f"/api/storage/devices/{did}/collect")
            assert r.status_code == 200 and r.json()["status"] == "ok" and len(srv.log) == 2 * n_after_ok

            lst = client.get("/api/storage/devices").json()["rows"][0]
            assert lst["last_collect"]["status"] == "ok" and lst["last_collect"]["item_count"] == 9
            det = client.get(f"/api/storage/devices/{did}/collected").json()
            assert det["run"]["complete"] is True and len(det["rows"]) == 9 and det["rows"][0]["__no"] == 1
            assert client.get("/api/storage/devices/999/collected").status_code == 404
            assert client.get(f"/api/storage/devices/{did}/collected?run_id=99999").status_code == 404

            client.post("/api/auth/logout")
            _login(client, db_path, "viewer2")
            assert client.get(f"/api/storage/devices/{did}/collected").status_code == 200      # 明細唯讀：一般帳號可看
            assert client.post(f"/api/storage/devices/{did}/collect").status_code == 403
            assert {x["method"] for x in srv.log} == {"GET"}
        finally:
            qw.set_clock(None)
            srv.stop(); api.app.dependency_overrides.clear(); _cleanup()


def test_API_模組關閉_明細與收集都403():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _login(client, db_path, "admin")
            assert client.get("/api/storage/devices/1/collected").status_code == 403
            assert client.post("/api/storage/devices/1/collect").status_code == 403
        finally:
            api.app.dependency_overrides.clear(); _cleanup()


# ---------------------------------------------------------------- GPT 5.6 審查補強（S2）
def test_格式異常項目不靜默丟棄_區段標不完整():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        types = _std_types("a")
        types["host"] = [{"id": "Host_1", "name": "a-esx1", "osType": "ESXi", "type": 1}, "壞項目-不是物件"]
        srv = FakeUnity("a", types=types)
        try:
            r = sc.collect_device(conn, _add(conn, srv)["id"], "admin")
            host = next(s for s in r["sections"] if s["key"] == "host")
            assert host["status"] == "partial" and "格式異常" in host["message"]
            assert r["status"] == "partial" and r["complete"] is False
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_重複id的關聯標不確定_不任選一筆填名稱():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        types = _std_types("a")
        types["pool"].append({"id": "pool_1", "name": "a-pool-dup", "sizeTotal": 5, "sizeUsed": 1, "sizeFree": 4})
        types["host"].append({"id": "Host_1", "name": "a-esx-dup", "osType": "ESXi", "type": 1})
        srv = FakeUnity("a", types=types)
        try:
            r = sc.collect_device(conn, _add(conn, srv)["id"], "admin")
            luns = _items(conn, r["run_id"], "lun")
            assert all("Pool 的 id 在來源中重複" in x["review"] for x in luns)
            assert all("pool_name" not in json.loads(x["extra"] or "{}") for x in luns)
            maps = _items(conn, r["run_id"], "host_lun")
            assert all("Host id 在來源中重複" in m["review"] for m in maps) and all(m["parent"] == "" for m in maps)
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_整次時間上限_在頁與頁之間生效_部分資料保留():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("a")
        old = sc.RUN_BUDGET_S
        try:
            sc.RUN_BUDGET_S = -1.0        # 一開始就超時
            r = sc.collect_device(conn, _add(conn, srv)["id"], "admin")
            assert r["status"] in ("partial", "sections_failed", "failed")
            assert all(s["status"] in ("ok", "skipped", "failed", "partial") for s in r["sections"])
            assert any(s["code"] == "run_timeout" for s in r["sections"] if s["status"] != "ok")
        finally:
            sc.RUN_BUDGET_S = old; srv.stop(); conn.close(); _cleanup()
