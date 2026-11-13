"""ACT-03 Teams 通知：GPT 審查 9 項修正的釘子。

登入停用 fail-closed／恢復與結案要等對應告警真的 sent／不跟隨重導與 443／DB 原子 claim（兩 connection 並行只送一次）／
至少一次與「不確定」／資料函式不自 commit、稽核同交易／每次投遞重驗網址與環境閘門／Markdown 轉義與可信 origin／絕對有效期。
全部不對外送：假 transport 或本機 127.0.0.1 假接收端。
"""
import json
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import credential_store  # noqa: E402
import db  # noqa: E402
import teams_notify as tn  # noqa: E402
import whatsup_service as wu  # noqa: E402

REAL = "https://prod-12.japaneast.logic.azure.com:443/workflows/abc123/triggers/manual/paths/invoke?sig=zzz"
T0 = datetime(2026, 11, 2, 9, 0, 0)
DB = {}


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.delenv("WEBIT_TEAMS_ALLOW_LOOPBACK", raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(credential_store, "KEY_PATH_DEFAULT", str(Path(tmp) / "k.key"))
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        wu.ensure_tables(conn)
        DB['p'] = p
        yield conn
        conn.close()


def _hist(conn, event, did="d1", name="sw-core-01", new_state=None, old_state=None):
    conn.execute("INSERT INTO whatsup_alert_history (at, device_id, name, ip, event, old_state, new_state, problem, group_name) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", ("2026-11-02 08:59:00", did, name, "192.0.2.1", event, old_state, new_state, "p", "g"))
    conn.commit()


class Rec:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        return self.results.pop(0) if self.results else (202, "")


def _enable(conn, **kw):
    tn.set_webhook(conn, REAL)
    tn.save_settings(conn, {**tn.DEFAULTS, "enabled": True, "live_confirmed": True, **kw})
    conn.commit()


def _prime(conn):
    tn.tick(conn, T0, Rec())


# ── 2. 恢復／結案要等對應告警真的 sent ──
def test_告警沒送成功_恢復不單獨送(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", new_state="當機")
    _hist(env, "cleared", old_state="當機")
    rec = Rec((503, "x"), (503, "x"))
    tn.tick(env, T0, rec)
    st = {r["kind"]: r["status"] for r in tn.delivery_log(env)}
    assert st == {"alert": "failed", "recovery": "pending"} and len(rec.calls) == 1      # 恢復被擋住
    rec2 = Rec()
    tn.tick(env, T0 + timedelta(seconds=61), rec2)                                        # 告警重試成功 → 恢復才送
    assert len(rec2.calls) == 1 or len(rec2.calls) == 2
    tn.tick(env, T0 + timedelta(seconds=62), rec2)
    st = {r["kind"]: r["status"] for r in tn.delivery_log(env)}
    assert st == {"alert": "sent", "recovery": "sent"}


def test_告警放棄_恢復略過(env):
    _enable(env, max_attempts=1)
    _prime(env)
    _hist(env, "new", new_state="當機")
    _hist(env, "cleared", old_state="當機")
    rec = Rec((500, "x"))
    tn.tick(env, T0, rec)
    tn.tick(env, T0 + timedelta(seconds=1), rec)
    st = {r["kind"]: r["status"] for r in tn.delivery_log(env)}
    assert st == {"alert": "dead", "recovery": "skipped"} and len(rec.calls) == 1


def test_事件結案要等開立sent(env):
    _enable(env)
    _prime(env)
    env.execute("INSERT INTO incident_event (type,title,started_at,status,created_at,mode,category) VALUES ('電力','UPS','2026-11-02 08:00:00','open','2026-11-02 08:00:00','formal','incident')")
    env.commit()
    rec = Rec((503, "x"))
    tn.tick(env, T0, rec)
    env.execute("UPDATE incident_event SET status='closed', closed_at='2026-11-02 08:30:00'")
    env.commit()
    tn.tick(env, T0 + timedelta(seconds=5), rec)
    st = {r["kind"]: r["status"] for r in tn.delivery_log(env)}
    assert st == {"incident": "failed", "incident_closed": "pending"} and len(rec.calls) == 1
    tn.tick(env, T0 + timedelta(seconds=70), rec)
    tn.tick(env, T0 + timedelta(seconds=71), rec)
    assert {r["kind"]: r["status"] for r in tn.delivery_log(env)} == {"incident": "sent", "incident_closed": "sent"}


# ── 3/7. 重導、埠、每次投遞重驗 ──
class _Sink(BaseHTTPRequestHandler):
    got: list = []
    mode = "ok"
    other = ""

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        _Sink.got.append((self.path, self.rfile.read(n)))
        if _Sink.mode == "redirect":
            self.send_response(302); self.send_header("Location", _Sink.other); self.end_headers()
        elif _Sink.mode == "slow":
            time.sleep(0.4); self.send_response(202); self.end_headers()
        else:
            self.send_response(202); self.end_headers()

    def log_message(self, *a):
        pass


def _serve():
    srv = HTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture()
def sinks():
    _Sink.got, _Sink.mode = [], "ok"
    a, b = _serve(), _serve()
    yield f"http://127.0.0.1:{a.server_address[1]}/a", f"http://127.0.0.1:{b.server_address[1]}/b"
    a.shutdown(); b.shutdown()


def test_不跟隨重導(env, sinks, monkeypatch):
    monkeypatch.setenv("WEBIT_TEAMS_ALLOW_LOOPBACK", "1")
    a, b = sinks
    _Sink.mode, _Sink.other = "redirect", b
    http, err, phase = tn.http_transport(a, b"{}")
    assert http == 302                                   # 當失敗，不是 2xx
    assert [p for p, _ in _Sink.got] == ["/a"]           # 第二個站台一次都沒被打到
    tn.set_webhook(env, a)
    tn.save_settings(env, {**tn.DEFAULTS, "enabled": True})
    env.commit()
    tn.tick(env, T0)
    _hist(env, "new", new_state="當機")
    tn.tick(env, T0 + timedelta(seconds=1))
    assert tn.delivery_log(env)[0]["status"] == "dead" and all(p != "/b" for p, _ in _Sink.got)


@pytest.mark.parametrize("bad", ["https://prod-1.logic.azure.com:8443/x", "https://prod-1.logic.azure.com:80/x"])
def test_對外只允許443(env, bad):
    with pytest.raises(tn.WebhookError):
        tn.validate_webhook_url(bad)
    tn.validate_webhook_url("https://prod-1.logic.azure.com:443/x")
    tn.validate_webhook_url("https://prod-1.logic.azure.com/x")


def test_環境閘門移除後既存loopback網址不再送(env, sinks, monkeypatch):
    monkeypatch.setenv("WEBIT_TEAMS_ALLOW_LOOPBACK", "1")
    a, _ = sinks
    tn.set_webhook(env, a)
    tn.save_settings(env, {**tn.DEFAULTS, "enabled": True})
    env.commit()
    tn.tick(env, T0)
    _hist(env, "new", new_state="當機")
    monkeypatch.delenv("WEBIT_TEAMS_ALLOW_LOOPBACK")
    rec = Rec()
    out = tn.tick(env, T0 + timedelta(seconds=1), rec)
    assert not rec.calls and not _Sink.got and out["delivery"]["held"]
    assert tn.send_test(env, rec)["ok"] is False and not _Sink.got
    assert tn.http_transport(a, b"{}")[2] == "refused" and not _Sink.got           # 連 transport 自己也再驗一次
    assert tn.delivery_log(env)[0]["status"] == "pending"


# ── 4. 原子 claim ──
def test_兩個連線並行只送一次(env, sinks, monkeypatch):
    monkeypatch.setenv("WEBIT_TEAMS_ALLOW_LOOPBACK", "1")
    a, _ = sinks
    tn.set_webhook(env, a)
    tn.save_settings(env, {**tn.DEFAULTS, "enabled": True})
    env.commit()
    tn.tick(env, T0)
    _hist(env, "new", new_state="當機")
    tn.enqueue_alerts(env, tn.load_settings(env), T0)
    _Sink.mode = "slow"
    cfg = tn.load_settings(env)
    barrier = threading.Barrier(2)
    results = []

    def worker():
        c = db.get_connection(DB['p'])
        try:
            barrier.wait()
            results.append(tn.process_queue(c, cfg, T0 + timedelta(seconds=1)))
        finally:
            c.close()

    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(_Sink.got) == 1
    assert sum(r["sent"] for r in results) == 1
    assert tn.delivery_log(env)[0]["status"] == "sent"


def test_已被claim的列別人不能再claim_租約過期變不確定(env):
    _enable(env)
    now = T0
    tn.ensure(env)
    tn._enqueue(env, "x:1", "alert", "wug:1", "critical", "t", tn._card("t", "Good", []), now)
    env.commit()
    assert tn._claim(env, 1, now)
    assert tn._claim(env, 1, now) is None
    rec = Rec()
    out = tn.process_queue(env, tn.load_settings(env), now + timedelta(seconds=tn.LEASE_SEC + 1), rec)
    assert out["uncertain"] == 1 and not rec.calls
    assert tn.delivery_log(env)[0]["status"] == "uncertain"


# ── 5. 至少一次、不確定 ──
def test_卡片尾端有穩定事件ID(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", new_state="當機")
    rec = Rec()
    tn.tick(env, T0, rec)
    body = json.loads(rec.calls[0][1])["attachments"][0]["content"]["body"]
    assert body[-1]["text"].startswith("事件ID") and "wug:" in body[-1]["text"]


def test_讀取逾時標不確定_不自動重送_連線前失敗才重試(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", did="a", new_state="當機")
    _hist(env, "new", did="b", new_state="當機")
    rec = Rec((None, "TimeoutError", "uncertain"), (None, "URLError", "connect"))
    tn.tick(env, T0, rec)
    st = {r["source_ref"]: r["status"] for r in tn.delivery_log(env)}
    assert st == {"wug:a": "uncertain", "wug:b": "failed"}
    tn.tick(env, T0 + timedelta(hours=1), Rec())             # 不確定的不會被自動送
    assert {r["source_ref"]: r["status"] for r in tn.delivery_log(env)}["wug:a"] == "uncertain"
    row = [r for r in tn.delivery_log(env) if r["source_ref"] == "wug:a"][0]
    assert "不確定" in row["status_label"]
    assert tn.retry_dead(env, row["id"], T0 + timedelta(hours=1))["status"] == "uncertain"
    env.commit()
    rec2 = Rec()
    tn.tick(env, T0 + timedelta(hours=1, seconds=1), rec2)
    assert {r["source_ref"]: r["status"] for r in tn.delivery_log(env)}["wug:a"] == "sent"


def test_http_transport_分類(env, monkeypatch):
    monkeypatch.setenv("WEBIT_TEAMS_ALLOW_LOOPBACK", "1")
    http, err, phase = tn.http_transport("http://127.0.0.1:9/x", b"{}", timeout=2)      # 沒人聽的埠＝連線建立前失敗
    assert http is None and phase == "connect"
    assert "127.0.0.1" not in err


# ── 6. 不自 commit、稽核同交易 ──
def test_資料函式不自行commit(env):
    env.commit()
    tn.set_webhook(env, REAL)
    tn.save_settings(env, {**tn.DEFAULTS})
    assert env.in_transaction
    env.rollback()
    assert not tn.webhook_is_set(env)
    tn.set_webhook(env, REAL)
    env.commit()
    tn.clear_webhook(env)
    env.rollback()
    assert tn.webhook_is_set(env)


@pytest.fixture()
def client(env):
    import api
    from fastapi.testclient import TestClient

    def _dep():
        yield env

    api.app.dependency_overrides[api.get_db] = _dep
    yield TestClient(api.app, raise_server_exceptions=False), api
    api.app.dependency_overrides.clear()


def _login(client, env, who="admin", role=None):
    import auth
    kw = {"role": role} if role else {}
    db.create_user(env, who, auth.hash_password("test-password-123"), **kw)
    assert client.post("/api/auth/login", json={"username": who, "password": "test-password-123"}).status_code == 200


def test_稽核失敗整體回滾(env, client, monkeypatch):
    c, api = client
    _login(c, env)
    import activity

    def boom(*a, **k):
        raise RuntimeError("audit down")

    monkeypatch.setattr(activity, "log", boom)
    assert c.put("/api/teams-notify/webhook", json={"url": REAL}).status_code == 500
    assert not tn.webhook_is_set(env)
    assert c.put("/api/teams-notify/settings", json={"min_severity": "critical"}).status_code == 500
    assert tn.load_settings(env)["min_severity"] == "warning"


def test_重送有專屬稽核(env, client):
    c, api = client
    _login(c, env)
    _enable(env)
    tn.ensure(env)
    tn._enqueue(env, "wug:1:new", "alert", "wug:1", "critical", "t", tn._card("t", "Good", []), T0)
    env.execute("UPDATE teams_notify_delivery SET status='dead'")
    env.commit()
    assert c.post("/api/teams-notify/log/1/retry").status_code == 200
    rows = [r["action"] for r in env.execute("SELECT action FROM activity_log WHERE path LIKE '/api/teams-notify/log/%'")]
    assert any("wug:1:new" in a and "重送" in a for a in rows)
    assert c.post("/api/teams-notify/log/1/retry").status_code == 409


# ── 1. 登入停用 fail-closed ──
def test_登入停用時所有寫入403(env, client):
    c, api = client
    env.execute("INSERT INTO app_settings (key, value) VALUES ('auth_disabled', '1') ON CONFLICT(key) DO UPDATE SET value='1'")
    env.commit()
    from _app_shared import auth_disabled
    assert auth_disabled(env)
    for m, p, kw in (("put", "/api/teams-notify/settings", {"json": {"live_confirmed": True}}), ("put", "/api/teams-notify/webhook", {"json": {"url": REAL}}),
                     ("delete", "/api/teams-notify/webhook", {}), ("post", "/api/teams-notify/test", {}), ("post", "/api/teams-notify/log/1/retry", {})):
        r = getattr(c, m)(p, **kw)
        assert r.status_code == 403 and "登入停用" in r.text, (p, r.status_code, r.text)
    assert not tn.webhook_is_set(env) and tn.load_settings(env)["live_confirmed"] is False
    r = c.get("/api/teams-notify/settings")
    assert r.status_code == 200 and r.json()["can_edit"] is False


# ── 8. Markdown 轉義、可信 origin ──
def test_卡片文字轉義_連結粗體mention標籤():
    evil = "[點我](http://evil.example/x) **粗** _斜_ @everyone <at>全員</at> `c` # h"
    card = tn.alert_card(dict(tn.DEFAULTS), {"device_id": "1", "name": evil, "ip": "i", "new_state": "當機", "problem": evil, "group_name": evil, "at": "x"})
    txt = json.dumps(card, ensure_ascii=False)
    body = card["attachments"][0]["content"]["body"]
    flat = [body[1]["text"]]                       # 2.060.x 新格式：設備與原因在第二行（一台一行）
    for t in flat:
        assert "\\[" in t and "\\]" in t and "\\(" in t and "\\*\\*" in t and "\\<at\\>" in t
        assert "[點我](" not in t and "@everyone" not in t and "@​everyone" in t
    assert "<at>" not in txt.replace("\\<at\\>", "")


@pytest.mark.parametrize("bad", ["http://10.99.0.10:3000/evil/path", "http://user:pw@10.99.0.10", "ftp://10.99.0.10", "http://h/?q=1", "javascript:alert(1)", "http://h#frag"])
def test_base_url只收可信origin(bad):
    with pytest.raises(ValueError):
        tn.validate({**tn.DEFAULTS, "base_url": bad})


def test_base_url正規化與連結():
    cfg = tn.validate({**tn.DEFAULTS, "base_url": "HTTP://Web.Example.Test:3000/"})
    assert cfg["base_url"] == "http://web.example.test:3000"
    c = tn.alert_card(cfg, {"device_id": "1", "name": "n", "new_state": "當機"})
    assert c["attachments"][0]["content"]["actions"][0]["url"] == "http://web.example.test:3000/v2/incident"
    assert tn.validate(tn.DEFAULTS)["base_url"] == ""


# ── 9. 絕對有效期 ──
def test_有效期不被重試或靜默延長(env):
    _enable(env, max_attempts=10, base_backoff_sec=3600)
    _prime(env)
    _hist(env, "new", new_state="當機")
    rec = Rec(*[(503, "x")] * 20)
    for h in range(0, 6):
        tn.tick(env, T0 + timedelta(hours=h), rec)
    assert tn.delivery_log(env)[0]["status"] == "failed"
    n = len(rec.calls)
    tn.tick(env, T0 + timedelta(hours=tn.EXPIRE_HOURS, minutes=1), rec)           # 重試一直在排，仍照絕對時間過期
    assert tn.delivery_log(env)[0]["status"] == "expired" and len(rec.calls) == n


def test_靜默延後不延長有效期(env):
    _enable(env, quiet_start="22:00", quiet_end="07:00", min_severity="info")
    night = datetime(2026, 11, 2, 23, 0)
    tn.tick(env, night, Rec())
    _hist(env, "new", new_state="警告")
    rec = Rec()
    tn.tick(env, night + timedelta(minutes=1), rec)
    tn.tick(env, datetime(2026, 11, 3, 7, 1), rec)          # 超過 6 小時絕對有效期：不再送一則過時告警
    assert not rec.calls and tn.delivery_log(env)[0]["status"] == "expired"


# ══ 獨立審查者補充（A～F）══
# A. 網址驗證
@pytest.mark.parametrize("bad", [
    "https://evil.com\\.logic.azure.com/x", "https://prod-1.logic.azure.com:99999/x", "https://prod-1.logic.azure.com:abc/x",
    "https://prod-1.logic.azure.com/x\r\nHost: evil", "https://prod-1.logic.azure.com/x y", "https://prod-1.logic.azure.com/x\t",
    "https://prod-é.logic.azure.com/x", "https://prod-1.logic.azure.com:8443/x", "https://-bad.logic.azure.com/x",
    "https://prod_1.logic.azure.com/x", "https://prod-1.logic.azure.com\x00/x", "https://pro d.logic.azure.com/x"])
def test_網址驗證擋各種怪網址(bad):
    with pytest.raises(tn.WebhookError):
        tn.validate_webhook_url(bad)


# B. 停用後重新啟用不補發
def test_停用期間的歷史重新啟用不補發(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", did="a", new_state="當機")
    tn.tick(env, T0, Rec())                                  # 啟用中：a 告警已送、已排為 notified
    tn.save_settings(env, {**tn.load_settings(env), "enabled": False})
    env.commit()
    for i in range(5):                                       # 停用期間 5 筆（含 a 恢復、b 新告警）
        _hist(env, "new", did=f"x{i}", new_state="當機")
    _hist(env, "cleared", did="a", old_state="當機")
    tn.save_settings(env, {**tn.load_settings(env), "enabled": True})
    env.commit()
    rec = Rec()
    tn.tick(env, T0 + timedelta(minutes=30), rec)             # 重新啟用的第一輪：只記游標
    assert not rec.calls and tn.delivery_log(env)[0]["source_ref"] == "wug:a"
    assert len(tn.delivery_log(env)) == 1
    _hist(env, "new", did="a", new_state="當機")             # 之後的新告警照常通知（a 的舊狀態已被重置，不會誤判重複）
    tn.tick(env, T0 + timedelta(minutes=31), rec)
    assert len(rec.calls) == 1


def test_直接改DB停用_tick也重置(env):
    _enable(env)
    _prime(env)
    env.execute("UPDATE app_settings SET value=json_set(value,'$.enabled',json('false')) WHERE key=?", (tn.SETTING_KEY,))
    env.commit()
    tn.tick(env, T0, Rec())
    assert env.execute("SELECT COUNT(*) FROM teams_notify_state").fetchone()[0] == 0


def test_關閉事件通知再開_不補發停用期間的事件(env):
    _enable(env)
    _prime(env)
    ins = "INSERT INTO incident_event (type,title,started_at,status,created_at,mode,category) VALUES ('電力',?,'2026-11-02 08:00:00','open','2026-11-02 08:00:00','formal','incident')"
    tn.save_settings(env, {**tn.load_settings(env), "send_incidents": False})
    env.commit()
    env.execute(ins, ("期間事件",))
    env.commit()
    tn.save_settings(env, {**tn.load_settings(env), "send_incidents": True})
    env.commit()
    rec = Rec()
    tn.tick(env, T0, rec)
    assert not rec.calls
    env.execute(ins, ("新事件",))
    env.commit()
    tn.tick(env, T0 + timedelta(seconds=1), rec)
    assert len(rec.calls) == 1 and "新事件" in rec.calls[0][1].decode()


# C. retry_dead
def test_retry_dead_舊庫沒跑過tick也不炸_不重送測試列(env):
    assert tn.retry_dead(env, 1) is None                       # 表還不存在也不 500
    tn.ensure(env)
    tn._enqueue(env, "test:1", "test", "test", "info", "t", tn._card("t", "Good", []), T0)
    env.execute("UPDATE teams_notify_delivery SET status='dead'")
    env.commit()
    assert tn.retry_dead(env, 1) is None
    assert tn.delivery_log(env)[0]["status"] == "dead"


# D. scrub
def test_scrub_遮URL路徑段與query值(env):
    url = REAL
    leaked = "boom at /workflows/abc123/triggers/manual/paths/invoke?sig=zzz and token=ABCDEF1234567890ABCDEF1234567890 host prod-12.japaneast.logic.azure.com"
    out = tn.scrub(leaked, url)
    for frag in ("abc123", "zzz", "ABCDEF", "prod-12", "azure", "workflows", "invoke"):
        assert frag not in out, (frag, out)
    out2 = tn.scrub("failed https://other.example/p/q?x=1 and Bearer-ish AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", None)
    assert "other.example" not in out2 and "AAAAAAAAAAAAAAAA" not in out2


def test_deliver_one_錯誤文字不留網址片段(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", new_state="當機")
    bad = lambda url, payload: (500, "err abc123 sig=zzz /workflows/abc123/triggers prod-12.japaneast.logic.azure.com")   # noqa: E731
    tn.tick(env, T0, bad)
    dump = json.dumps([dict(r) for r in env.execute("SELECT * FROM teams_notify_delivery")], ensure_ascii=False)
    for frag in ("abc123", "zzz", "prod-12", "azure", "workflows"):
        assert frag not in dump, frag


def test_deliver_one自己也會重驗網址(env):
    _enable(env)
    tn.ensure(env)
    tn._enqueue(env, "x:1", "alert", "wug:1", "critical", "t", tn._card("t", "Good", []), T0)
    env.commit()
    assert tn._claim(env, 1, T0)
    row = env.execute("SELECT * FROM teams_notify_delivery WHERE id=1").fetchone()
    rec = Rec()
    assert tn.deliver_one(env, row, dict(tn.DEFAULTS), "http://127.0.0.1:5/x", rec, T0) == "held"        # 環境閘門沒開
    assert tn.deliver_one(env, row, dict(tn.DEFAULTS), "https://evil.example.com/x", rec, T0) == "held"   # 非 Teams 網域
    assert not rec.calls
    assert env.execute("SELECT status FROM teams_notify_delivery WHERE id=1").fetchone()[0] == "pending"


# E. 單次 tick 上限
def test_單次tick最多送TICK_LIMIT則(env):
    _enable(env)
    _prime(env)
    for i in range(tn.TICK_LIMIT + 3):
        _hist(env, "new", did=f"d{i}", new_state="當機")
    rec = Rec()
    tn.tick(env, T0, rec)
    assert len(rec.calls) == tn.TICK_LIMIT
    tn.tick(env, T0 + timedelta(seconds=1), rec)
    assert len(rec.calls) == tn.TICK_LIMIT + 3
    import inspect
    assert inspect.signature(tn.http_transport).parameters["timeout"].default <= 10


# F. 唯讀
def test_預覽與GET在唯讀連線_任何寫入被SQLite擋下(env):
    _enable(env)
    with tn.read_only(env):
        tn.preview(env, "alert")
        tn.preview(env, "daily")
        tn.public_settings(env)
        tn.delivery_log(env)
        with pytest.raises(Exception) as ei:
            env.execute("INSERT INTO app_settings (key, value) VALUES ('zz','1')")
        assert "readonly" in str(ei.value).lower()
    env.execute("INSERT INTO app_settings (key, value) VALUES ('zz','1')")      # 離開區塊恢復可寫
    env.rollback()


def test_API的預覽與GET用唯讀連線(env, client):
    c, api = client
    _login(c, env)
    seen = []

    def auth_cb(action, a1, a2, db_name, trigger):
        import sqlite3
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE):
            seen.append((action, a1))
        return sqlite3.SQLITE_OK

    env.commit()
    env.set_authorizer(auth_cb)
    try:
        for p in ("/api/teams-notify/preview?kind=alert", "/api/teams-notify/preview?kind=daily", "/api/teams-notify/preview?kind=weekly",
                  "/api/teams-notify/preview?kind=recovery", "/api/teams-notify/settings", "/api/teams-notify/log"):
            seen.clear()
            r = c.get(p)
            assert r.status_code == 200, (p, r.text)
            tables = {t for _, t in seen if t not in ("sessions", "activity_log", "users")}
            assert not tables, (p, tables)         # 除了登入驗證的 session 維護，這些路徑沒有寫任何東西
    finally:
        env.set_authorizer(None)


# ══ 第三輪（GPT 複查 1～8）══
import socket  # noqa: E402


class _Mute(BaseHTTPRequestHandler):
    """收到請求後不回應（模擬送出後讀取逾時）。"""
    got = 0

    def do_POST(self):
        _Mute.got += 1
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        time.sleep(2.0)

    def log_message(self, *a):
        pass


# 1. 傳輸階段分類
def test_送出後讀取逾時是不確定_不是connect(env, monkeypatch):
    monkeypatch.setenv("WEBIT_TEAMS_ALLOW_LOOPBACK", "1")
    _Mute.got = 0
    srv = HTTPServer(("127.0.0.1", 0), _Mute)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        http, err, phase = tn.http_transport(f"http://127.0.0.1:{srv.server_address[1]}/x", b"{}", timeout=1)
        assert http is None and phase == "uncertain" and _Mute.got == 1
    finally:
        srv.shutdown()


def test_只有證明尚未送出的錯誤才算connect(env, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("nodename")))
    assert tn.http_transport(REAL, b"{}")[2] == "connect"                              # DNS 失敗：沒送出
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    def refuse(*a, **k):
        raise ConnectionRefusedError()
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert tn.http_transport(REAL, b"{}")[2] == "connect"                              # 拒絕連線：沒送出
    def slow(*a, **k):
        raise socket.timeout("connect timed out")
    monkeypatch.setattr(socket, "create_connection", slow)
    assert tn.http_transport(REAL, b"{}")[2] == "connect"                              # 連線建立階段逾時：沒送出


def test_408與其他無回應列不確定_不自動重試(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", did="a", new_state="當機")
    _hist(env, "new", did="b", new_state="當機")
    tn.tick(env, T0, Rec((408, "HTTP 408"), (None, "ConnectionResetError", "uncertain")))
    assert {r["source_ref"]: r["status"] for r in tn.delivery_log(env)} == {"wug:a": "uncertain", "wug:b": "uncertain"}
    rec = Rec()
    tn.tick(env, T0 + timedelta(hours=1), rec)
    assert not rec.calls


# 2. fencing
def test_舊送件者晚回來不得覆寫新狀態(env):
    _enable(env)
    tn.ensure(env)
    tn._enqueue(env, "wug:1:new", "alert", "wug:1", "critical", "t", tn._card("t", "Good", []), T0)
    env.commit()
    tok_a = tn._claim(env, 1, T0)
    row_a = env.execute("SELECT * FROM teams_notify_delivery WHERE id=1").fetchone()
    # A 卡住；租約過期 → 轉不確定 → 人工重送 → B claim 並送成功
    later = T0 + timedelta(seconds=tn.LEASE_SEC + 5)
    tn.process_queue(env, tn.load_settings(env), later, Rec())
    assert env.execute("SELECT status FROM teams_notify_delivery WHERE id=1").fetchone()[0] == "uncertain"
    tn.retry_dead(env, 1, later); env.commit()
    # 還在 pending 時 A 回來：不得覆寫
    assert tn.deliver_one(env, row_a, tn.load_settings(env), REAL, Rec((503, "x")), later, tok_a) == "stale"
    assert env.execute("SELECT status FROM teams_notify_delivery WHERE id=1").fetchone()[0] == "pending"
    tok_b = tn._claim(env, 1, later)
    assert tok_b and tok_b != tok_a
    row_b = env.execute("SELECT * FROM teams_notify_delivery WHERE id=1").fetchone()
    # B 還在送（sending）時 A 回來：token 不同，不得覆寫
    assert tn.deliver_one(env, row_a, tn.load_settings(env), REAL, Rec((500, "x")), later, tok_a) == "stale"
    assert env.execute("SELECT status FROM teams_notify_delivery WHERE id=1").fetchone()[0] == "sending"
    assert tn.deliver_one(env, row_b, tn.load_settings(env), REAL, Rec((202, "")), later, tok_b) == "sent"
    # B 已 sent，A 再回來也不能改
    assert tn.deliver_one(env, row_a, tn.load_settings(env), REAL, Rec((500, "x")), later, tok_a) == "stale"
    r = env.execute("SELECT status, last_http, attempts FROM teams_notify_delivery WHERE id=1").fetchone()
    assert (r[0], r[1]) == ("sent", 202)


# 3. 依賴不占 limit、子通知可復活
def test_被依賴擋住的列不占名額(env):
    _enable(env)
    tn.ensure(env)
    c = tn._card("t", "Good", [])
    tn._enqueue(env, "wug:1:new", "alert", "wug:1", "critical", "A", c, T0)
    tn._enqueue(env, "wug:1:cleared", "recovery", "wug:1", "info", "R", c, T0, depends_on="wug:1:new")
    for i in range(3):
        tn._enqueue(env, f"wug:{10+i}:new", "alert", f"wug:{10+i}", "critical", f"B{i}", c, T0 + timedelta(seconds=1))
    env.execute("UPDATE teams_notify_delivery SET status='failed', next_try_at=? WHERE event_key='wug:1:new'", (tn._ts(T0 + timedelta(hours=1)),))
    env.commit()
    rec = Rec()
    out = tn.process_queue(env, tn.load_settings(env), T0 + timedelta(seconds=5), rec, limit=2)
    assert out["sent"] == 2 and len(rec.calls) == 2                      # 前面卡住的 A、R 沒有吃掉名額
    st = {r["title"]: r["status"] for r in tn.delivery_log(env)}
    assert st["R"] == "pending" and st["A"] == "failed"


def test_父通知人工重送成功_子通知連帶復活(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", new_state="當機")
    _hist(env, "cleared", old_state="當機")
    tn.tick(env, T0, Rec((None, "ConnectionResetError", "uncertain")))
    tn.tick(env, T0 + timedelta(hours=7), Rec())                              # 父不確定期間，子通知有效期不倒數：不會先顯示 expired
    assert {r["kind"]: r["status"] for r in tn.delivery_log(env)} == {"alert": "uncertain", "recovery": "pending"}
    parent = [r for r in tn.delivery_log(env) if r["kind"] == "alert"][0]
    info = tn.retry_dead(env, parent["id"], T0 + timedelta(hours=7)); env.commit()
    assert info["dependents"] == 0
    rec = Rec()
    tn.tick(env, T0 + timedelta(hours=7, seconds=1), rec)
    tn.tick(env, T0 + timedelta(hours=7, seconds=2), rec)
    assert {r["kind"]: r["status"] for r in tn.delivery_log(env)} == {"alert": "sent", "recovery": "sent"} and len(rec.calls) == 2


# 4. 排程在登入停用時 fail-closed
@pytest.mark.parametrize("how", ["setting", "env"])
def test_登入停用時tick不對外送出(env, monkeypatch, how):
    _enable(env)
    _prime(env)
    _hist(env, "new", new_state="當機")
    if how == "setting":
        env.execute("INSERT INTO app_settings (key, value) VALUES ('auth_disabled','1') ON CONFLICT(key) DO UPDATE SET value='1'")
        env.commit()
    else:
        monkeypatch.setenv("WEBIT3_DISABLE_AUTH", "1")
    rec = Rec()
    out = tn.tick(env, T0, rec)
    assert not rec.calls and "登入停用" in out["delivery"]["held"]
    assert tn.delivery_log(env)[0]["status"] == "pending"
    assert "登入停用" in tn.hold_reason(env, tn.load_settings(env))


# 5. DNS 內部位址
@pytest.mark.parametrize("ip", ["10.1.2.3", "192.168.0.9", "127.0.0.1", "169.254.169.254", "224.0.0.5", "0.0.0.0", "::1", "fe80::1", "::ffff:10.0.0.1", "100.64.0.1"])
def test_DNS解析到內部位址一律拒絕_且不連線(env, monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 443))])
    called = []
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: called.append(a) or (_ for _ in ()).throw(AssertionError("不該連線")))
    http, err, phase = tn.http_transport(REAL, b"{}")
    assert phase == "refused" and not called


def test_DNS混合結果只要有一個內部位址就拒絕_公開位址用已驗證IP連線(env, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("10.0.0.5", 443))])
    assert tn.http_transport(REAL, b"{}")[2] == "refused"
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    seen = []
    def fake(addr, timeout=None, **k):
        seen.append(addr)
        raise ConnectionRefusedError()
    monkeypatch.setattr(socket, "create_connection", fake)
    assert tn.http_transport(REAL, b"{}")[2] == "connect"
    assert seen == [("93.184.216.34", 443)]                  # 連的是驗證過的 IP，不是再用主機名解析一次（防 rebinding）


def test_dns_guard可由admin關閉_公司proxy情境(env, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.9.9.9", 443))])
    seen = []
    def fake(addr, timeout=None, **k):
        seen.append(addr)
        raise ConnectionRefusedError()
    monkeypatch.setattr(socket, "create_connection", fake)
    assert tn.http_transport(REAL, b"{}", dns_guard=False)[2] == "connect" and seen
    assert tn.DEFAULTS["dns_guard"] is True and tn.validate(tn.DEFAULTS)["dns_guard"] is True


def test_dns_guard關閉要寫稽核(env, client):
    c, api = client
    _login(c, env)
    assert c.put("/api/teams-notify/settings", json={"dns_guard": False}).status_code == 200
    acts = [r[0] for r in env.execute("SELECT action FROM activity_log WHERE path='/api/teams-notify/settings'")]
    assert any("關閉" in a and "DNS" in a for a in acts)
    assert c.put("/api/teams-notify/settings", json={"dns_guard": True}).status_code == 200


@pytest.mark.parametrize("bad", ["https://a..logic.azure.com/x", "https://-a.logic.azure.com/x", "https://a-.logic.azure.com/x", "https://.logic.azure.com/x"])
def test_主機名label規則(bad):
    with pytest.raises(tn.WebhookError):
        tn.validate_webhook_url(bad)


# 6. send_test 同交易
def test_測試送出不自行commit_稽核失敗整體回滾並如實說明(env, client, monkeypatch):
    c, api = client
    _login(c, env)
    monkeypatch.setenv("WEBIT_TEAMS_ALLOW_LOOPBACK", "1")
    _Sink.got, _Sink.mode = [], "ok"
    srv = _serve()
    try:
        tn.set_webhook(env, f"http://127.0.0.1:{srv.server_address[1]}/t")
        env.commit()
        tn.ensure(env); env.commit()
        r = tn.send_test(env)
        assert r["ok"] and r["sent_out"]
        env.rollback()                                                   # 沒 commit → 紀錄不存在
        assert env.execute("SELECT COUNT(*) FROM teams_notify_delivery").fetchone()[0] == 0
        import activity
        monkeypatch.setattr(activity, "log", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit down")))
        before = len(_Sink.got)
        resp = c.post("/api/teams-notify/test")
        assert resp.status_code == 500 and "接收端可能已收到" in resp.text      # 外部已送但記錄失敗：如實告知，不吞
        assert len(_Sink.got) == before + 1
        assert env.execute("SELECT COUNT(*) FROM teams_notify_delivery").fetchone()[0] == 0
    finally:
        srv.shutdown()


# 7. 停用競態與殘留佇列
def test_停用後六小時內重啟用_殘留佇列不補送(env):
    _enable(env, live_confirmed=False)
    _prime(env)
    _hist(env, "new", new_state="當機")
    tn.tick(env, T0, Rec())                                              # 未確認對外 → 留在 pending
    assert tn.delivery_log(env)[0]["status"] == "pending"
    tn.save_settings(env, {**tn.load_settings(env), "enabled": False}); env.commit()
    assert tn.delivery_log(env)[0]["status"] == "cancelled"
    tn.save_settings(env, {**tn.load_settings(env), "enabled": True, "live_confirmed": True}); env.commit()
    rec = Rec()
    tn.tick(env, T0 + timedelta(hours=1), rec)
    tn.tick(env, T0 + timedelta(hours=1, seconds=5), rec)
    assert not rec.calls and tn.delivery_log(env)[0]["status"] == "cancelled"


def test_關閉某類別_該類殘留標cancelled(env):
    _enable(env, live_confirmed=False, summary_daily=True, send_incidents=True, send_recovery=True)
    tn.ensure(env)
    c = tn._card("t", "Good", [])
    for k, kind in (("a", "recovery"), ("b", "incident"), ("c", "incident_closed"), ("d", "summary_daily"), ("e", "alert")):
        tn._enqueue(env, f"x:{k}", kind, f"s:{k}", "info", k, c, T0)
    env.commit()
    cfg = tn.load_settings(env)
    tn.save_settings(env, {**cfg, "send_recovery": False}); env.commit()
    tn.save_settings(env, {**tn.load_settings(env), "send_incidents": False}); env.commit()
    tn.save_settings(env, {**tn.load_settings(env), "summary_daily": False}); env.commit()
    st = {r["title"]: r["status"] for r in tn.delivery_log(env)}
    assert st == {"a": "cancelled", "b": "cancelled", "c": "cancelled", "d": "cancelled", "e": "pending"}
    assert tn.STATUS_LABEL["cancelled"] == "已取消"


def test_舊tick拿著過期設定也不能在停用後送(env):
    _enable(env)
    tn.ensure(env)
    c = tn._card("t", "Good", [])
    for i in range(3):
        tn._enqueue(env, f"wug:{i}:new", "alert", f"wug:{i}", "critical", f"A{i}", c, T0)
    env.commit()
    stale_cfg = tn.load_settings(env)                                    # 舊 tick 手上的設定（enabled=True）
    calls = []

    def disabling_transport(url, payload):
        calls.append(1)
        env2 = db.get_connection(DB["p"])
        env2.execute("UPDATE app_settings SET value=json_set(value,'$.enabled',json('false')) WHERE key=?", (tn.SETTING_KEY,))
        env2.commit(); env2.close()                                      # 第一封送出的同時，另一人停用並提交
        return 202, ""

    out = tn.process_queue(env, stale_cfg, T0 + timedelta(seconds=1), disabling_transport)
    assert len(calls) == 1 and out["sent"] == 1                          # 停用提交後不再送第二、第三封
    assert tn._claim(env, 2, T0 + timedelta(seconds=2)) is None          # claim 本身就帶「目前仍啟用」條件


def test_md_escape_清單語法():
    for t, want in (("- a", "\\- a"), ("+ a", "\\+ a"), ("1. a", "1\\. a"), ("12. a", "12\\. a")):
        assert tn.md_escape(t) == want
    assert tn.md_escape("2026-11-02 ok") == "2026-11-02 ok"


def test_API顯示目前為何不送(env, client):
    c, api = client
    _login(c, env)
    assert c.put("/api/teams-notify/webhook", json={"url": REAL}).status_code == 200
    assert c.put("/api/teams-notify/settings", json={"enabled": True, "live_confirmed": False}).status_code == 200
    j = c.get("/api/teams-notify/settings").json()
    assert "對外送出已確認" in (j["hold"] or "")
    assert c.put("/api/teams-notify/settings", json={"live_confirmed": True}).status_code == 200
    assert c.get("/api/teams-notify/settings").json()["hold"] is None


# ══ 第四輪（最後一輪）══
REAL2 = "https://prod-77.westus.logic.azure.com:443/workflows/zzz999/triggers/manual/paths/invoke?sig=qqq"


def _seed(env, n, now=T0):
    tn.ensure(env)
    c = tn._card("t", "Good", [])
    for i in range(n):
        tn._enqueue(env, f"wug:{i}:new", "alert", f"wug:{i}", "critical", f"A{i}", c, now)
    env.commit()


def test_tick進行中輪替URL_後續列送新URL(env):
    _enable(env)
    _seed(env, 3)
    urls = []

    def rotating(url, payload):
        urls.append(url)
        if len(urls) == 1:
            c2 = db.get_connection(DB["p"])
            tn.set_webhook(c2, REAL2); c2.commit(); c2.close()          # 管理員在第一封送出時輪替網址
        return 202, ""

    tn.process_queue(env, tn.load_settings(env), T0 + timedelta(seconds=1), rotating)
    assert urls == [REAL, REAL2, REAL2]


def test_tick進行中改dns_guard_後續列套用新設定(env, monkeypatch):
    _enable(env, dns_guard=False)
    _seed(env, 3)
    seen = []

    def fake_transport(url, payload, timeout=8, dns_guard=True):
        seen.append(dns_guard)
        if len(seen) == 1:
            c2 = db.get_connection(DB["p"])
            tn.save_settings(c2, {**tn.load_settings(c2), "dns_guard": True}); c2.commit(); c2.close()    # 中途把檢查由關改開
        return 202, "", ""

    monkeypatch.setattr(tn, "http_transport", fake_transport)
    tn.process_queue(env, tn.load_settings(env), T0 + timedelta(seconds=1))
    assert seen == [False, True, True]


def test_已claim的舊tick在送出前再確認_停用後不送且標cancelled(env, monkeypatch):
    _enable(env)
    _seed(env, 2)
    real_claim = tn._claim

    def claim_then_disable(conn, rid, now):
        tok = real_claim(conn, rid, now)
        c2 = db.get_connection(DB["p"])
        tn.save_settings(c2, {**tn.load_settings(c2), "enabled": False}); c2.commit(); c2.close()      # claim 完成、送出前被停用
        return tok

    monkeypatch.setattr(tn, "_claim", claim_then_disable)
    rec = Rec()
    out = tn.process_queue(env, tn.load_settings(env), T0 + timedelta(seconds=1), rec)
    assert not rec.calls and out["cancelled"] >= 1
    st = {r["title"]: r["status"] for r in tn.delivery_log(env)}
    assert st == {"A0": "cancelled", "A1": "cancelled"}


def test_claim後hold放回待送(env, monkeypatch):
    _enable(env)
    _seed(env, 1)
    real_claim = tn._claim

    def claim_then_unconfirm(conn, rid, now):
        tok = real_claim(conn, rid, now)
        c2 = db.get_connection(DB["p"])
        tn.save_settings(c2, {**tn.load_settings(c2), "live_confirmed": False}); c2.commit(); c2.close()
        return tok

    monkeypatch.setattr(tn, "_claim", claim_then_unconfirm)
    rec = Rec()
    tn.process_queue(env, tn.load_settings(env), T0 + timedelta(seconds=1), rec)
    assert not rec.calls and tn.delivery_log(env)[0]["status"] == "pending"


class _Flaky:
    """對 auth_disabled 查詢丟 SQLite 錯誤，其餘委派給真連線。"""
    def __init__(self, conn):
        self._c = conn

    def execute(self, sql, *a):
        if "auth_disabled" in sql:
            raise sqlite3.OperationalError("database is locked")
        return self._c.execute(sql, *a)

    def __getattr__(self, n):
        return getattr(self._c, n)


import sqlite3  # noqa: E402


def test_查不出登入是否停用_一律視同停用_不對外送出(env, caplog):
    _enable(env)
    _seed(env, 1)
    flaky = _Flaky(env)
    with caplog.at_level("ERROR", logger="teams_notify"):
        assert tn._auth_off(flaky) is True
    assert any("保守視為停用" in r.message for r in caplog.records)
    assert "azure" not in caplog.text and "OperationalError" in caplog.text
    rec = Rec()
    out = tn.process_queue(flaky, tn.load_settings(env), T0 + timedelta(seconds=1), rec)
    assert not rec.calls and "登入停用" in out["held"]
    # loopback 目標不受登入停用影響（本機模擬）
    assert tn._auth_off(env) is False


def test_父通知不確定期間子通知不倒數有效期(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", new_state="當機")
    _hist(env, "cleared", old_state="當機")
    tn.tick(env, T0, Rec((None, "ConnectionResetError", "uncertain")))
    for h in (3, 7, 12, 30):
        tn.tick(env, T0 + timedelta(hours=h), Rec())
        assert {r["kind"]: r["status"] for r in tn.delivery_log(env)} == {"alert": "uncertain", "recovery": "pending"}, h


def test_父通知dead_子通知skipped_人工重送父_子連帶復活(env):
    _enable(env, max_attempts=1)
    _prime(env)
    _hist(env, "new", new_state="當機")
    _hist(env, "cleared", old_state="當機")
    tn.tick(env, T0, Rec((500, "x")))
    tn.tick(env, T0 + timedelta(seconds=1), Rec())
    assert {r["kind"]: r["status"] for r in tn.delivery_log(env)} == {"alert": "dead", "recovery": "skipped"}
    parent = [r for r in tn.delivery_log(env) if r["kind"] == "alert"][0]
    assert tn.retry_dead(env, parent["id"], T0 + timedelta(minutes=5))["dependents"] == 1
    env.commit()
    rec = Rec()
    tn.tick(env, T0 + timedelta(minutes=5, seconds=1), rec)
    tn.tick(env, T0 + timedelta(minutes=5, seconds=2), rec)
    assert {r["kind"]: r["status"] for r in tn.delivery_log(env)} == {"alert": "sent", "recovery": "sent"}


def test_send_test讀取逾時_sent_out回可能已送達(env, monkeypatch):
    monkeypatch.setenv("WEBIT_TEAMS_ALLOW_LOOPBACK", "1")
    tn.set_webhook(env, "http://127.0.0.1:9/x")
    r = tn.send_test(env, lambda u, b: (None, "TimeoutError", "uncertain"))
    assert r["ok"] is False and r["sent_out"] is True
    r2 = tn.send_test(env, lambda u, b: (None, "ConnectionRefusedError", "connect"))
    assert r2["sent_out"] is False
