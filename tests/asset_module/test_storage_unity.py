"""批 S1：Dell Unity Storage 設定與連線測試。

全部對「本機假 Unity 伺服器」（127.0.0.1 隨機埠的 http.server）測，不連任何真實位址。
⚠️ 假回應是依 Unity REST 公開文件的形狀手造的，不是真機輸出；真機格式要在公司網路實測。

涵蓋 139 的隔離案例：AUTH-1（basicSystemInfo 成功不算帳密有效）、PAGE-1/2/3（分頁完整／循環／next 同主機）、
ISOLATE-1（兩台 session／Cookie 不共用、同物件 ID 不互蓋）；另測 302、HTML 登入頁、401（不重試）、403、逾時、TLS。
"""
import datetime
import json
import ssl
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import credential_store  # noqa: E402
import db  # noqa: E402
import integration_http as ih  # noqa: E402
import storage_unity as su  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLE_AWARE_TESTS = True      # 要測 admin／一般帳號的差別（見 tests/conftest.py）
PW = "unity-pw-for-test-9876"
USER = "svc_unity"


class FakeUnity:
    """一台假 Unity。cfg 控制各種異常；log 記下每個請求（方法、路徑、Cookie、是否帶帳密）。"""

    def __init__(self, name="unity-a", serial="FNM00000000001", tls=None, **cfg):
        self.name, self.serial = name, serial
        self.cfg = {"basic": True, "auth": (USER, PW), "redirect": False, "html": False, "status": None, "sleep": 0,
                    "pages": None, "next_mode": "normal", "echo_pw": False, "evil_url": "", **cfg}
        self.log = []
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=None, ctype="application/json", extra=None):
                raw = (json.dumps(body) if not isinstance(body, (str, bytes)) else body)
                raw = raw.encode() if isinstance(raw, str) else raw
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self):
                import base64
                path, _, query = self.path.partition("?")
                authz = self.headers.get("Authorization", "")
                rec = {"method": self.command, "path": path, "query": query, "cookie": self.headers.get("Cookie"), "authz": bool(authz),
                       "emc": self.headers.get("X-EMC-REST-CLIENT"), "accept": self.headers.get("Accept")}
                with outer.lock:
                    outer.log.append(rec)
                if self.command != "GET":
                    return self._send(405, {"error": "read-only test server"})
                if outer.cfg["sleep"]:
                    time.sleep(outer.cfg["sleep"])
                if path == su.PATH_BASIC:
                    if outer.cfg["redirect"] == "basic":
                        return self._send(302, "", extra={"Location": "/login"})
                    if not outer.cfg["basic"]:
                        return self._send(404, {"error": "no"})
                    return self._send(200, {"entries": [{"content": {"id": "0", "name": outer.name, "model": "Unity 380"}}]})
                exp = "Basic " + base64.b64encode(f"{outer.cfg['auth'][0]}:{outer.cfg['auth'][1]}".encode()).decode()
                has_sess = f"mod_sess=SESS-{outer.name}" in (self.headers.get("Cookie") or "")
                cas = outer.cfg["redirect"] if str(outer.cfg["redirect"]).startswith("cas") else None
                if path == "/cas/login" and cas:
                    if cas == "cas_html":
                        return self._send(200, "<html><body>CAS login</body></html>", ctype="text/html")
                    if cas == "cas_loop":
                        return self._send(302, "", extra={"Location": "/cas/login?again=1"})
                    if cas == "cas_evil":
                        return self._send(302, "", extra={"Location": outer.cfg["evil_url"]})
                    if authz != exp:
                        return self._send(401, {"error": {"errorCode": 401}})
                    orig = unquote(query.partition("orig=")[2])
                    return self._send(302, "", extra={"Location": orig + ("&" if "?" in orig else "?") + "ticket=ST-1",
                                                      "Set-Cookie": f"mod_sess=SESS-{outer.name}; Path=/; HttpOnly"})
                if path.startswith("/api/types/") and path.endswith("/instances"):
                    if cas and not has_sess:
                        orig = path + (f"?{query}" if query else "")
                        return self._send(302, "", extra={"Location": "/cas/login?orig=" + quote(orig, safe="")})
                    if outer.cfg["redirect"] is True:
                        return self._send(302, "", extra={"Location": "https://sso.example.invalid/login"})
                    if outer.cfg["html"]:
                        return self._send(200, "<html><body>Please sign in</body></html>", ctype="text/html")
                    if outer.cfg["status"]:
                        body = {"error": {"message": f"boom {PW}" if outer.cfg["echo_pw"] else "boom"}}
                        return self._send(outer.cfg["status"], body)
                    if authz != exp and not has_sess:
                        return self._send(401, {"error": {"errorCode": 401}})
                    extra = {"Set-Cookie": f"mod_sess=SESS-{outer.name}; Path=/; HttpOnly"}
                    if path == su.PATH_SYSTEM:
                        return self._send(200, {"entries": [{"content": {"id": "0", "name": outer.name, "model": "Unity 380",
                                                                          "serialNumber": outer.serial}}]}, extra=extra)
                    return self._send(200, outer.page(path, query), extra=extra)
                self._send(404, {"error": "unknown"})

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        if tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*tls)
            self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def page(self, path, query):
        q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        n = int(q.get("page", "1"))
        mode = self.cfg["next_mode"]
        pages = self.cfg["pages"] or [[{"id": "res_1", "name": f"{self.name}-lun1"}]]
        body = {"entries": [{"content": c} for c in (pages[min(n, len(pages)) - 1] if mode in ("normal", "fragment") else [{"id": f"r{n}"}])]}
        if mode == "normal":
            nxt = f"{path}?page={n + 1}&per_page=100" if n < len(pages) else None
        elif mode == "fragment":
            nxt = f"&page={n + 1}" if n < len(pages) else None       # 真機 Unity：next 是相對 @base 的查詢片段
        elif mode == "loop":
            nxt = f"{path}?page=2&per_page=100"
            if n == 1:
                nxt = f"{path}?page=2&per_page=100"
        elif mode == "endless":
            nxt = f"{path}?page={n + 1}&per_page=100"
        else:
            nxt = self.cfg["next_mode"]
        if nxt:
            body["links"] = [{"rel": "self", "href": f"{path}?page={n}"}, {"rel": "next", "href": nxt}]
        return body

    def hits(self, path=None):
        with self.lock:
            return [r for r in self.log if path is None or r["path"] == path]

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


def _setup(tmp, scope="127.0.0."):
    db_path = Path(tmp) / "t.db"
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    su.KEY_PATH = str(Path(tmp) / "cred.key")
    su.ALLOW_PLAINTEXT_FOR_TEST = True
    su.ALLOW_LOOPBACK_FOR_TEST = True
    conn.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='storage_api'")
    conn.commit()
    su.ensure_tables(conn)
    credential_store.save(conn, "stor-ro", "storage", USER, PW, scope=scope, key_path=su.KEY_PATH)
    return conn, db_path


def _cleanup():
    su.ALLOW_PLAINTEXT_FOR_TEST = False
    su.ALLOW_LOOPBACK_FOR_TEST = False
    su.KEY_PATH = credential_store.KEY_PATH_DEFAULT


def _add(conn, srv, name=None, **kw):
    return su.create_device(conn, "admin", name=name or srv.name, host="127.0.0.1", port=srv.port, credential=kw.pop("credential", "stor-ro"), **kw)


# ---------------------------------------------------------------- AUTH-1
def test_AUTH1_basicSystemInfo成功不算帳密有效_受保護GET才算():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        bad = FakeUnity("bad", auth=("someone", "else"))
        good = FakeUnity("good")
        try:
            r = su.check_connection(conn, _add(conn, bad)["id"], "admin")
            assert r["ok"] is False and r["code"] == "unauthorized"
            assert r["reachable"] is True and r["authenticated"] is False      # 位址有回應 ≠ 帳密有效
            assert bad.hits(su.PATH_BASIC) and not bad.hits(su.PATH_BASIC)[0]["authz"]     # 探測不送帳密
            assert len(bad.hits(su.PATH_SYSTEM)) == 1                                       # 401 不重試（避免鎖帳號）
            r = su.check_connection(conn, _add(conn, good)["id"], "admin")
            assert r["ok"] is True and r["authenticated"] is True
            assert r["identity"] == {"name": "good", "model": "Unity 380", "serial": "FNM00000000001"}
            assert good.hits(su.PATH_SYSTEM)[0]["authz"] is True
        finally:
            bad.stop(); good.stop(); conn.close(); _cleanup()


def test_只發GET_沒有任何寫入或action方法():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("ro")
        try:
            su.check_connection(conn, _add(conn, srv)["id"], "admin")
            assert srv.log and {r["method"] for r in srv.log} == {"GET"}
        finally:
            srv.stop(); conn.close(); _cleanup()
    src = (ROOT / "APP" / "asset-module" / "backend" / "storage_unity.py").read_text(encoding="utf-8")
    for bad in ('"POST"', '"PUT"', '"DELETE"', '"PATCH"', ".post(", ".put(", ".delete(", ".patch("):
        assert bad not in src, bad


# ---------------------------------------------------------------- 異常回應
def test_302跨源或非API路徑重新導向_拒絕_視為失敗():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        s1, s2 = FakeUnity("r1", redirect=True), FakeUnity("r2", redirect="basic")
        try:
            r = su.check_connection(conn, _add(conn, s1)["id"], "admin")
            assert r["ok"] is False and r["code"] == "redirect" and r["authenticated"] is False
            assert "302" in r["message"] and "sso.example.invalid" not in r["message"]
            r = su.check_connection(conn, _add(conn, s2)["id"], "admin")
            assert r["code"] == "redirect" and r["reachable"] is False      # /login 不在 /api/ 或 /cas/ 之下
            assert not s2.hits(su.PATH_SYSTEM)        # 探測就失敗：不再把帳密送出去
        finally:
            s1.stop(); s2.stop(); conn.close(); _cleanup()


def test_CAS同源轉址_Basic換cookie_之後只用cookie_且每個請求帶兩個header():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("cas1", redirect="cas")
        try:
            r = su.check_connection(conn, _add(conn, srv)["id"], "admin")
            assert r["ok"] is True and r["authenticated"] is True and r["identity"]["serial"] == "FNM00000000001"
            assert all(h["emc"] == "true" and h["accept"] == "application/json" for h in srv.log)
            assert srv.hits("/cas/login") and srv.hits("/cas/login")[0]["authz"] is True
            assert {h["method"] for h in srv.log} == {"GET"}
            with _sess(srv) as sess:
                sess.get_pages("/api/types/lun/instances")                 # 第一次：Basic → 302 → /cas/login → 200（拿到 cookie）
                n_before = len(srv.log)
                sess.get("/api/types/system/instances")                   # 之後只用 cookie
                last = srv.log[n_before:]
                assert len(last) == 1 and last[0]["authz"] is False and "SESS-cas1" in last[0]["cookie"]
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_CAS登入頁是HTML_視為失敗():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("cas2", redirect="cas_html")
        try:
            r = su.check_connection(conn, _add(conn, srv)["id"], "admin")
            assert r["ok"] is False and r["code"] == "html" and r["authenticated"] is False
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_轉址循環_超過3次_停止():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("cas3", redirect="cas_loop")
        try:
            r = su.check_connection(conn, _add(conn, srv)["id"], "admin")
            assert r["ok"] is False and r["code"] == "redirect" and "3" in r["message"]
            assert len(srv.hits()) <= 1 + 1 + 3 + 1       # 探測 + 首跳 + 最多 3 次轉址
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_轉址到別的來源_拒絕_Authorization與cookie絕不外送():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        evil = FakeUnity("evil")
        srv = FakeUnity("cas4", redirect="cas_evil", evil_url=f"http://127.0.0.1:{evil.port}/api/types/system/instances")
        try:
            r = su.check_connection(conn, _add(conn, srv)["id"], "admin")
            assert r["ok"] is False and r["code"] == "redirect"
            assert str(evil.port) not in r["message"]
            assert evil.hits() == []            # 別的來源完全沒被碰到（不同埠＝不同來源）
        finally:
            srv.stop(); evil.stop(); conn.close(); _cleanup()


def test_redirect_target_各種惡意Location一律拒絕():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        x = FakeUnity("x")
        try:
            with _sess(x) as sess:
                cur = sess.base + "/api/types/system/instances"
                for loc in ("//evil.example.invalid/api/x", "https://evil.example.invalid/api/x", "http://user:pw@127.0.0.1/api/x",
                            "/other/path", "/api/../admin", "/api\\x", "", None, "/api/x\r\nSet-Cookie: a=b",
                            "ftp://127.0.0.1/api/x"):
                    try:
                        sess._redirect_target(cur, loc, 302)
                    except su.UnityError as exc:
                        assert exc.code == "redirect" and "evil" not in exc.message
                    else:
                        raise AssertionError(f"應拒絕：{loc!r}")
                assert sess._redirect_target(cur, "/cas/login?service=a", 302) == sess.base + "/cas/login?service=a"
                assert sess._redirect_target(cur, "next?page=2", 302) == sess.base + "/api/types/system/next?page=2"
        finally:
            x.stop(); conn.close(); _cleanup()


def test_session結束_cookie清空_不跨次():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("cas5", redirect="cas")
        try:
            with _sess(srv) as sess:
                sess.get("/api/types/system/instances")
                assert len(sess._client.cookies) > 0
            assert len(sess._client.cookies) == 0
            with _sess(srv) as sess2:
                assert sess2._established is False
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_HTML登入頁_拒收():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("h", html=True)
        try:
            r = su.check_connection(conn, _add(conn, srv)["id"], "admin")
            assert r["ok"] is False and r["code"] == "html" and r["authenticated"] is False
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_403_權限不足_與一般錯誤_訊息不含密碼():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        s403, s500 = FakeUnity("f", status=403), FakeUnity("e", status=500, echo_pw=True)
        try:
            r = su.check_connection(conn, _add(conn, s403)["id"], "admin")
            assert r["code"] == "forbidden" and r["authenticated"] is False
            r = su.check_connection(conn, _add(conn, s500)["id"], "admin")
            assert r["code"] == "http_error" and PW not in json.dumps(r, ensure_ascii=False)
            dump = json.dumps([dict(x) for x in conn.execute("SELECT * FROM storage_devices")]
                              + [dict(x) for x in conn.execute("SELECT * FROM credential_use_audit")], ensure_ascii=False)
            assert PW not in dump
        finally:
            s403.stop(); s500.stop(); conn.close(); _cleanup()


def test_逾時():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("slow", sleep=3.0)
        old = su.TIMEOUT_S
        su.TIMEOUT_S = 0.3
        try:
            t0 = time.monotonic()
            r = su.check_connection(conn, _add(conn, srv)["id"], "admin")
            assert r["ok"] is False and r["code"] == "timeout" and time.monotonic() - t0 < 2.5          # 機器忙時（平行測試、relay 產出物自測）0.3 秒逾時也可能拖到 1 秒多；上限仍遠小於伺服器 3 秒延遲
        finally:
            su.TIMEOUT_S = old
            srv.stop(); conn.close(); _cleanup()


def test_連不上_連線被拒():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("gone")
        dev = _add(conn, srv)
        srv.stop()
        try:
            r = su.check_connection(conn, dev["id"], "admin")
            assert r["ok"] is False and r["code"] == "unreachable"
        finally:
            conn.close(); _cleanup()


# ---------------------------------------------------------------- 分頁 PAGE-1/2/3
def _sess(srv):
    return su.UnitySession("127.0.0.1", srv.port, USER, PW)


def test_PAGE_真機next是查詢片段_也能讀完整份且不跳出集合():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("frag", pages=[[{"id": "a"}], [{"id": "b"}], [{"id": "c"}]], next_mode="fragment")
        try:
            with _sess(srv) as sess:
                rows = sess.get_pages("/api/types/lun/instances", {"fields": "name"})
            assert [r["id"] for r in rows] == ["a", "b", "c"]
            q = [h["query"] for h in srv.hits("/api/types/lun/instances")]
            assert len(q) == 3 and all("fields=name" in x and "per_page=100" in x for x in q) and "page=3" in q[2]
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_PAGE1_分頁讀完整份():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        pages = [[{"id": "a"}, {"id": "b"}], [{"id": "c"}], [{"id": "d"}, {"id": "e"}]]
        srv = FakeUnity("pg", pages=pages)
        try:
            with _sess(srv) as s:
                rows = s.get_pages("/api/types/lun/instances")
            assert [r["id"] for r in rows] == list("abcde")
            assert len(srv.hits("/api/types/lun/instances")) == 3
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_PAGE2_next循環或無限延伸_會停():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        loop, endless = FakeUnity("loop", next_mode="loop"), FakeUnity("endless", next_mode="endless")
        try:
            with _sess(loop) as s:
                try:
                    s.get_pages("/api/types/lun/instances")
                    raise AssertionError("循環要丟錯")
                except su.UnityError as exc:
                    assert exc.code == "page_loop"
            assert len(loop.hits()) <= 3
            with _sess(endless) as s:
                try:
                    s.get_pages("/api/types/lun/instances", max_pages=5)
                    raise AssertionError("無限延伸要丟錯")
                except su.UnityError as exc:
                    assert exc.code == "page_limit"
            assert len(endless.hits()) == 5
        finally:
            loop.stop(); endless.stop(); conn.close(); _cleanup()


def test_PAGE3_next只能同主機同協定():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        other = FakeUnity("other")
        try:
            for bad in (f"http://127.0.0.1:{other.port}/api/types/lun/instances?page=2",       # 別的埠＝別的主機
                        "https://127.0.0.1:%d/api/types/lun/instances?page=2",                 # 同主機不同協定（%d 下面補）
                        f"//127.0.0.1:{other.port}/api/types/lun/instances?page=2",            # protocol-relative
                        "/etc/passwd", "http://user:pw@127.0.0.1:%d/api/x"):
                srv = FakeUnity("main", next_mode=bad.replace("%d", "0") if "%d" in bad else bad)
                try:
                    bad_url = bad if "%d" not in bad else bad.replace("%d", str(srv.port))
                    srv.cfg["next_mode"] = bad_url
                    with _sess(srv) as s:
                        try:
                            s.get_pages("/api/types/lun/instances")
                            raise AssertionError(f"應拒絕：{bad_url}")
                        except su.UnityError as exc:
                            assert exc.code == "bad_next", (bad_url, exc.code)
                finally:
                    srv.stop()
            assert other.hits() == []         # 別台完全沒被碰到（也就沒收到帳密）
        finally:
            other.stop(); conn.close(); _cleanup()


# ---------------------------------------------------------------- ISOLATE-1
def test_ISOLATE1_兩台Cookie不共用_同物件ID不互蓋():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        a, b = FakeUnity("unity-a", serial="SN-A"), FakeUnity("unity-b", serial="SN-B")      # 兩台都有 id="0"、res_1
        try:
            da, db_ = _add(conn, a), _add(conn, b)
            ra = su.check_connection(conn, da["id"], "admin")
            rb = su.check_connection(conn, db_["id"], "admin")
            su.check_connection(conn, da["id"], "admin")          # A 再測一次：不能帶著上一次的 Cookie
            assert ra["identity"]["serial"] == "SN-A" and rb["identity"]["serial"] == "SN-B"
            assert all(r["cookie"] is None for r in a.hits() + b.hits()), "每次測試都是全新 session，不該帶任何 Cookie"
            with _sess(a) as sa, _sess(b) as sb:
                pa = sa.get_pages("/api/types/lun/instances")
                pb = sb.get_pages("/api/types/lun/instances")
                assert pa == [{"id": "res_1", "name": "unity-a-lun1"}] and pb == [{"id": "res_1", "name": "unity-b-lun1"}]
                assert sa._client.cookies is not sb._client.cookies
                assert "SESS-unity-b" not in str(dict(sa._client.cookies)) and "SESS-unity-a" not in str(dict(sb._client.cookies))
                # 同一個 session 內第二個請求才會帶自己的 Cookie；永遠不是對方的
                sa.get("/api/types/system/instances")
            assert all("SESS-unity-a" not in (r["cookie"] or "") for r in b.hits())
            assert all("SESS-unity-b" not in (r["cookie"] or "") for r in a.hits())
            rows = {d["name"]: d for d in su.list_devices(conn)}
            assert rows["unity-a"]["last_identity"]["serial"] == "SN-A" and rows["unity-b"]["last_identity"]["serial"] == "SN-B"
        finally:
            a.stop(); b.stop(); conn.close(); _cleanup()


# ---------------------------------------------------------------- TLS
def _selfsigned(tmp):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "unity.example.local")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cp, kp = Path(tmp) / "c.pem", Path(tmp) / "k.pem"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    return str(cp), str(kp)


def test_TLS_預設驗證_自簽失敗_可指定CA或明示信任自簽():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        su.ALLOW_PLAINTEXT_FOR_TEST = False           # 這支走真的 https
        cert, key = _selfsigned(tmp)
        srv = FakeUnity("tls", tls=(cert, key))
        try:
            dev = _add(conn, srv)
            assert dev["trust_self_signed"] is False                       # 預設不信任
            r = su.check_connection(conn, dev["id"], "admin")
            assert r["ok"] is False and r["code"] == "tls" and srv.hits() == []      # 憑證不過＝一個請求都沒送出
            su.update_device(conn, dev["id"], name="tls", host="127.0.0.1", port=srv.port, ca_bundle=cert)
            r = su.check_connection(conn, dev["id"], "admin")
            assert r["ok"] is True and r["tls_verified"] is True           # 指定 CA（這張自簽就是根）→ 驗證通過
            su.update_device(conn, dev["id"], name="tls", host="127.0.0.1", port=srv.port, trust_self_signed=True)
            r = su.check_connection(conn, dev["id"], "admin")
            assert r["ok"] is True and r["tls_verified"] is False and "未驗證 TLS" in r["message"]
            assert su.get_device(conn, dev["id"])["last_tls_verified"] is False
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_正式環境只收https():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        _cleanup()
        assert su._base_url("10.99.0.5", 443) == "https://10.99.0.5:443"
        assert su._base_url("fe80::1", 8443) == "https://[fe80::1]:8443"
        conn.close()


# ---------------------------------------------------------------- 設定與憑證
def test_設定驗證_品牌_位址_憑證():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        credential_store.save(conn, "wug-x", "whatsup", "u", "p", key_path=su.KEY_PATH)
        try:
            ok = dict(name="u1", host="10.99.0.5", credential="stor-ro")
            bad_ok = dict(ok, name="uX")
            d = su.create_device(conn, "admin", **ok)
            assert d["vendor"] == "dell_unity" and d["port"] == 443 and d["trust_self_signed"] is False and d["__no"] == d["id"]
            for kw, frag in ((dict(vendor="netapp"), "不支援"), (dict(host="https://10.99.0.5/"), "只填 IP"), (dict(host="a b"), "只填 IP"),
                             (dict(host=""), "請填"), (dict(port=70000), "埠"), (dict(name="u1", host="10.99.0.6"), "同名"),
                             (dict(name="u2", credential="nope"), "不存在"), (dict(name="u3", credential="wug-x"), "storage"),
                             (dict(name="u4", ca_bundle="Z:/no/such.pem"), "CA")):
                try:
                    su.create_device(conn, "admin", **{**bad_ok, **kw})
                    raise AssertionError(f"應拒絕：{kw}")
                except ih.ConfigError as exc:
                    assert frag in str(exc), (kw, str(exc))
            assert len(su.list_devices(conn)) == 1
        finally:
            conn.close(); _cleanup()


def test_沒選憑證_或憑證沒有適用範圍_不連線():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        credential_store.save(conn, "no-scope", "storage", USER, PW, scope=None, key_path=su.KEY_PATH)
        credential_store.save(conn, "other-scope", "storage", USER, PW, scope="10.99.7.", key_path=su.KEY_PATH)
        srv = FakeUnity("nc")
        try:
            for cred in ("", "no-scope", "other-scope"):
                dev = su.create_device(conn, "admin", name=f"d-{cred or 'none'}", host="127.0.0.1", port=srv.port, credential=cred)
                r = su.check_connection(conn, dev["id"], "admin")
                assert r["ok"] is False and r["code"] == "credential", cred
            assert srv.hits() == []
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_改位址或憑證_清掉上次測試結果():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("up")
        try:
            dev = _add(conn, srv)
            su.check_connection(conn, dev["id"], "admin")
            assert su.get_device(conn, dev["id"])["last_test_ok"] is True
            su.update_device(conn, dev["id"], name="up", host="127.0.0.1", port=srv.port, note="只改備註")
            assert su.get_device(conn, dev["id"])["last_test_ok"] is True        # 備註不影響
            su.update_device(conn, dev["id"], name="up", host="127.0.0.2", port=srv.port)
            d = su.get_device(conn, dev["id"])
            assert d["last_test_ok"] is None and d["last_identity"] is None and d["credential"] == "stor-ro"    # credential=None＝沿用
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


def test_API_模組關閉403_viewer只讀_admin設定與測試():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        su.KEY_PATH = str(Path(tmp) / "cred.key")
        su.ALLOW_PLAINTEXT_FOR_TEST = True
        su.ALLOW_LOOPBACK_FOR_TEST = True
        srv = FakeUnity("api")
        try:
            assert client.get("/api/storage/devices").status_code == 401
            _login(client, db_path, "oncall")
            assert client.get("/api/storage/devices").status_code == 403            # 模組預設關
            c = db.get_connection(db_path)
            c.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='storage_api'"); c.commit(); c.close()
            r = client.get("/api/storage/devices")
            assert r.status_code == 200 and r.json()["rows"] == [] and r.json()["vendors"][0]["key"] == "dell_unity"
            body = dict(name="api", host="127.0.0.1", port=srv.port, credential="stor-ro")
            rr = client.post("/api/storage/devices", json=body); assert rr.status_code == 403, rr.text            # 非 admin 不能寫
            client.post("/api/auth/logout")
            _login(client, db_path, "admin")
            c = db.get_connection(db_path)
            credential_store.save(c, "stor-ro", "storage", USER, PW, scope="127.0.0.", key_path=su.KEY_PATH); c.close()
            r = client.post("/api/storage/devices", json={**body, "password": "should-not-echo-123"})
            assert r.status_code == 400 and "should-not-echo-123" not in r.text
            r = client.post("/api/storage/devices", json={**body, "vendor": "netapp"})
            assert r.status_code == 400 and "不支援" in r.json()["detail"]
            r = client.post("/api/storage/devices", json=body)
            assert r.status_code == 200 and PW not in r.text, r.text
            did = r.json()["id"]
            t = client.post(f"/api/storage/devices/{did}/test")
            assert t.status_code == 200 and t.json()["ok"] is True and PW not in t.text
            assert client.get("/api/storage/devices").json()["rows"][0]["last_test_ok"] is True
            r = client.put(f"/api/storage/devices/{did}", json={**body, "name": "api2", "credential": None})
            assert r.status_code == 200 and r.json()["name"] == "api2" and r.json()["credential"] == "stor-ro"
            assert client.put("/api/storage/devices/999", json=body).status_code == 404
            assert client.post("/api/storage/devices/999/test").status_code == 404
            assert client.delete(f"/api/storage/devices/{did}").status_code == 200
            assert client.get("/api/storage/devices").json()["rows"] == []
        finally:
            srv.stop(); api.app.dependency_overrides.clear(); _cleanup()


# ---------------------------------------------------------------- GPT 5.6 審查補強（S1）
def test_SSRF_正式模式不收本機與保留位址():
    assert su.ALLOW_LOOPBACK_FOR_TEST is False
    for bad in ("127.0.0.1", "169.254.169.254", "0.0.0.0", "::1", "localhost", "224.0.0.1"):
        try:
            su.normalize_host(bad)
            raise AssertionError(f"應拒絕：{bad}")
        except Exception as exc:  # noqa: BLE001
            assert exc.__class__.__name__ == "ConfigError", (bad, exc)
    assert su.normalize_host("10.99.1.5") == "10.99.1.5"


def test_PAGE1_第一頁零筆但有next仍讀第二頁():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("main", pages=[[], [{"id": "late_1"}]])
        try:
            with _sess(srv) as s:
                got = s.get_pages("/api/types/lun/instances")
            assert [g["id"] for g in got] == ["late_1"]
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_PAGE3_同主機next指向action路徑也拒絕():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        srv = FakeUnity("main", next_mode="/api/instances/lun/res_1/action/delete")
        try:
            with _sess(srv) as s:
                try:
                    s.get_pages("/api/types/lun/instances")
                    raise AssertionError("應拒絕")
                except su.UnityError as exc:
                    assert exc.code == "bad_next"
                    assert len(exc.partial) == 1      # 已讀到的第一頁保留為部分證據
        finally:
            srv.stop(); conn.close(); _cleanup()


def test_測試進行中改設定_舊結果不寫入最近測試欄位():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _ = _setup(tmp)
        a = FakeUnity("unity-a", serial="SN-A")
        try:
            d = _add(conn, a)
            real_pages = su.UnitySession.get

            def slow_get(self, *args, **kw):          # 第一個請求時偷偷把設備改成別的 IP（模擬測試進行中被改設定）
                su.update_device(conn, d["id"], name=d["name"], host="10.99.0.56", port=a.port, credential="stor-ro")
                return real_pages(self, *args, **kw)
            su.UnitySession.get = slow_get
            try:
                res = su.check_connection(conn, d["id"], "admin")
            finally:
                su.UnitySession.get = real_pages
            assert res.get("stale") is True
            row = su.get_device(conn, d["id"])
            assert row["last_test_ok"] is None and row["last_identity"] is None      # 新設定「尚未測試」，沒被舊端點結果覆蓋
        finally:
            a.stop(); conn.close(); _cleanup()


def test_轉址目標含百分比編碼路徑一律拒絕():
    s = su.UnitySession("storage-a.example.invalid", 443, "DEMO", "XXXX")
    try:
        for loc in ("/api/%2e%2e/x", "/cas/%252e%252e/login", "/api/types/lun%2Finstances"):
            try:
                s._redirect_target("https://storage-a.example.invalid:443/api/types/system/instances", loc, 302)
                raise AssertionError(f"應拒絕：{loc}")
            except su.UnityError as exc:
                assert exc.code == "redirect"
    finally:
        s.close()
