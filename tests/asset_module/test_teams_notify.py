"""ACT-03 Teams 單向通知（teams_notify.py）。

不對外送任何訊息：transport 用注入的假函式，或本機 127.0.0.1 假接收端（http.server）。
釘住：Adaptive Card 格式、去重、恢復只在送過告警後才送、重試退避與上限、靜默時段、
預覽不寫表、網址是密鑰（加密、不回傳、不進紀錄／稽核）、對外閘門、測試送出只限 loopback。
"""
import json
import sys
import tempfile
import threading
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

REAL = "https://prod-12.japaneast.logic.azure.com:443/workflows/abc123SECRETtoken/triggers/manual/paths/invoke?sig=SECRETSIG"
T0 = datetime(2026, 11, 2, 9, 0, 0)


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.delenv("WEBIT_TEAMS_ALLOW_LOOPBACK", raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(credential_store, "KEY_PATH_DEFAULT", str(Path(tmp) / "k.key"))
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        wu.ensure_tables(conn)
        yield conn
        conn.close()


def _hist(conn, event, did="d1", name="sw-core-01", new_state=None, old_state=None, at="2026-11-02 08:59:00"):
    conn.execute("INSERT INTO whatsup_alert_history (at, device_id, name, ip, event, old_state, new_state, problem, group_name) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", (at, did, name, "192.0.2.1", event, old_state, new_state, "Ping 無回應", "核心"))
    conn.commit()


class Rec:
    """假 transport：記錄收到的 (url, payload)，依序回預設結果。"""
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        return self.results.pop(0) if self.results else (202, "")


def _enable(conn, **kw):
    tn.set_webhook(conn, REAL)
    cfg = {**tn.DEFAULTS, "enabled": True, "live_confirmed": True, **kw}
    tn.save_settings(conn, cfg)
    return tn.load_settings(conn)


def _prime(conn, now=T0):
    """第一次 tick 只記游標（不補發歷史）。"""
    tn.tick(conn, now, Rec())


# ── 格式 ──
def test_卡片是官方Workflows格式():
    card = tn.alert_card(dict(tn.DEFAULTS), {"device_id": "1", "name": "n", "ip": "i", "new_state": "當機", "at": "x"})
    assert card["type"] == "message" and len(card["attachments"]) == 1
    a = card["attachments"][0]
    assert a["contentType"] == "application/vnd.microsoft.card.adaptive"
    c = a["content"]
    assert c["type"] == "AdaptiveCard" and c["$schema"].endswith("adaptive-card.json") and c["version"]
    assert c["body"][0]["color"] == "Attention"
    assert len(json.dumps(card).encode()) < 28 * 1024


def test_大卡片會被縮到28KB內():
    big = [("k" * 40, "v" * 300)] * 25
    c = tn._card("t", "Accent", big)
    c["attachments"][0]["content"]["body"].append({"type": "TextBlock", "text": "x"})
    assert len(json.dumps(tn._fit(c), ensure_ascii=False).encode()) < 28 * 1024


# ── 告警／恢復／去重 ──
def test_第一次啟用不補發歷史(env):
    _hist(env, "new", new_state="當機")
    _enable(env)
    rec = Rec()
    out = tn.tick(env, T0, rec)
    assert out["queued"] == 0 and not rec.calls


def test_告警送出_重複輪詢與重跑不重發(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", new_state="當機")
    rec = Rec()
    tn.tick(env, T0, rec)
    tn.tick(env, T0 + timedelta(minutes=1), rec)
    tn.tick(env, T0 + timedelta(minutes=2), rec)
    assert len(rec.calls) == 1
    rows = tn.delivery_log(env)
    assert len(rows) == 1 and rows[0]["status"] == "sent" and rows[0]["last_http"] == 202 and rows[0]["attempts"] == 1
    # 游標被清掉重來（模擬重送）：同一事件的 event_key 仍擋住
    env.execute("DELETE FROM teams_notify_state WHERE ref='__cursor_wug__'"); env.execute("INSERT INTO teams_notify_state (ref,cursor_id) VALUES ('__cursor_wug__',0)"); env.commit()
    tn.tick(env, T0 + timedelta(minutes=3), rec)
    assert len(rec.calls) == 1


def test_門檻_輕微不通知_嚴重通知(env):
    _enable(env, min_severity="warning")
    _prime(env)
    _hist(env, "new", did="a", new_state="輕微")
    _hist(env, "new", did="b", new_state="嚴重")
    rec = Rec()
    tn.tick(env, T0, rec)
    assert len(rec.calls) == 1 and "嚴重" in rec.calls[0][1].decode()


def test_恢復只在送過告警後才送(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", did="a", new_state="輕微")          # 低於門檻：沒通知
    _hist(env, "cleared", did="a", old_state="輕微")
    _hist(env, "new", did="b", new_state="當機")
    _hist(env, "cleared", did="b", old_state="當機")
    rec = Rec()
    tn.tick(env, T0, rec)
    tn.tick(env, T0 + timedelta(seconds=1), rec)       # 恢復要等告警 sent 才排得進送件名單，下一輪才送
    kinds = sorted(r["kind"] for r in tn.delivery_log(env))
    assert kinds == ["alert", "recovery"] and len(rec.calls) == 2


def test_同嚴重度的changed不重發_升級才發(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", new_state="警告")
    _hist(env, "changed", old_state="警告", new_state="警告")
    _hist(env, "changed", old_state="警告", new_state="當機")
    rec = Rec()
    tn.tick(env, T0, rec)
    assert len(rec.calls) == 2


# ── 重試 ──
def test_失敗退避重試_上限後放棄(env):
    cfg = _enable(env, max_attempts=3, base_backoff_sec=60)
    _prime(env)
    _hist(env, "new", new_state="當機")
    rec = Rec((503, "HTTP 503"), (503, "HTTP 503"), (503, "HTTP 503"))
    tn.tick(env, T0, rec)
    r = tn.delivery_log(env)[0]
    assert r["status"] == "failed" and r["attempts"] == 1 and r["last_http"] == 503
    tn.tick(env, T0 + timedelta(seconds=30), rec)          # 還沒到退避時間
    assert len(rec.calls) == 1
    tn.tick(env, T0 + timedelta(seconds=61), rec)          # 第 2 次，退避 120 秒
    assert len(rec.calls) == 2
    tn.tick(env, T0 + timedelta(seconds=61 + 100), rec)
    assert len(rec.calls) == 2
    tn.tick(env, T0 + timedelta(seconds=61 + 121), rec)    # 第 3 次＝上限
    r = tn.delivery_log(env)[0]
    assert r["status"] == "dead" and r["attempts"] == 3
    assert tn.backoff_seconds(cfg, 1) == 60 and tn.backoff_seconds(cfg, 2) == 120 and tn.backoff_seconds(cfg, 20) == tn.MAX_BACKOFF_SEC


def test_4xx不重試直接放棄_429要重試(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", did="a", new_state="當機")
    _hist(env, "new", did="b", new_state="當機")
    rec = Rec((400, "HTTP 400"), (429, "HTTP 429"))
    tn.tick(env, T0, rec)
    st = {r["source_ref"]: r["status"] for r in tn.delivery_log(env)}
    assert st == {"wug:a": "dead", "wug:b": "failed"}


def test_重試成功後記成功(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", new_state="當機")
    rec = Rec((None, "ConnectionError"), (202, ""))
    tn.tick(env, T0, rec)
    tn.tick(env, T0 + timedelta(seconds=61), rec)
    r = tn.delivery_log(env)[0]
    assert r["status"] == "sent" and r["attempts"] == 2


def test_太久沒送出的即時告警過期_可手動重送(env):
    _enable(env)
    _prime(env)
    _hist(env, "new", new_state="當機")
    tn.tick(env, T0, Rec((503, "x")))
    tn.tick(env, T0 + timedelta(hours=tn.EXPIRE_HOURS + 1), Rec())
    r = tn.delivery_log(env)[0]
    assert r["status"] == "expired"
    assert tn.retry_dead(env, r["id"], T0 + timedelta(hours=8))
    rec = Rec()
    tn.tick(env, T0 + timedelta(hours=8), rec)
    assert tn.delivery_log(env)[0]["status"] == "sent" and len(rec.calls) == 1


# ── 靜默時段 ──
def test_靜默時段非嚴重延後_嚴重照送(env):
    _enable(env, quiet_start="22:00", quiet_end="02:00", min_severity="info")
    night = datetime(2026, 11, 2, 23, 0)
    _prime(env, night)
    _hist(env, "new", did="a", new_state="警告")
    _hist(env, "new", did="b", new_state="當機")
    rec = Rec()
    tn.tick(env, night, rec)
    assert len(rec.calls) == 1 and "當機" in rec.calls[0][1].decode() or "嚴重" in rec.calls[0][1].decode()
    tn.tick(env, datetime(2026, 11, 3, 2, 1), rec)
    assert len(rec.calls) == 2
    assert tn.in_quiet({"quiet_start": "22:00", "quiet_end": "07:00"}, datetime(2026, 1, 1, 3, 0))
    assert not tn.in_quiet({"quiet_start": "22:00", "quiet_end": "07:00"}, datetime(2026, 1, 1, 12, 0))
    assert not tn.in_quiet({"quiet_start": "", "quiet_end": ""}, datetime(2026, 1, 1, 3, 0))


# ── 摘要 ──
def test_每日摘要_到點才排_一天一則_時區台北(env):
    _enable(env, summary_daily=True, summary_time="08:00")
    _hist(env, "new", did="a", new_state="當機", at="2026-11-02 03:00:00")
    env.execute("INSERT INTO whatsup_alerts (device_id,name,ip,state,problem,since,group_name,first_seen,last_seen) VALUES ('a','n','i','當機','p','s','g','x','x')")
    env.commit()
    rec = Rec()
    tn.tick(env, datetime(2026, 11, 2, 7, 59), rec)        # 還沒到點
    assert not rec.calls
    tn.tick(env, datetime(2026, 11, 2, 8, 0), rec)
    tn.tick(env, datetime(2026, 11, 2, 12, 0), rec)
    assert len(rec.calls) == 1
    body = rec.calls[0][1].decode()
    assert "每日摘要 2026-11-02" in body and "目前告警中" in body
    assert tn.now_taipei().tzinfo is None
    assert tn.TPE.utcoffset(None) == timedelta(hours=8)


def test_每週摘要只在指定星期_每週一則(env):
    _enable(env, summary_weekly=True, weekly_day=0)
    rec = Rec()
    tn.tick(env, datetime(2026, 11, 3, 9, 0), rec)          # 週二
    assert not rec.calls
    tn.tick(env, datetime(2026, 11, 2, 9, 0), rec)          # 週一
    tn.tick(env, datetime(2026, 11, 2, 15, 0), rec)
    assert len(rec.calls) == 1 and "每週摘要" in rec.calls[0][1].decode()


def test_摘要送失敗由佇列重試_不重排新的(env):
    _enable(env, summary_daily=True)
    rec = Rec((500, "x"), (202, ""))
    tn.tick(env, T0, rec)
    tn.tick(env, T0 + timedelta(seconds=61), rec)
    rows = tn.delivery_log(env)
    assert len(rows) == 1 and rows[0]["status"] == "sent" and rows[0]["attempts"] == 2


# ── 事件 ──
def test_正式事件開立與結案各通知一次_演練不通知(env):
    _enable(env)
    import incident
    incident.ensure(env) if hasattr(incident, "ensure") else None
    cols = {r[1] for r in env.execute("PRAGMA table_info(incident_event)")}
    assert "mode" in cols
    _prime(env)
    ins = ("INSERT INTO incident_event (type,title,started_at,status,created_at,mode,category) VALUES ('電力',?,?, 'open', ?, ?, 'incident')")
    env.execute(ins, ("UPS 異常", "2026-11-02 08:00:00", "2026-11-02 08:00:00", "formal"))
    env.execute(ins, ("演練", "2026-11-02 08:00:00", "2026-11-02 08:00:00", "drill"))
    env.commit()
    rec = Rec()
    tn.tick(env, T0, rec)
    assert len(rec.calls) == 1 and "UPS 異常" in rec.calls[0][1].decode()
    env.execute("UPDATE incident_event SET status='closed', closed_at='2026-11-02 09:00:00' WHERE title='UPS 異常'")
    env.commit()
    tn.tick(env, T0 + timedelta(minutes=1), rec)
    tn.tick(env, T0 + timedelta(minutes=2), rec)
    kinds = [r["kind"] for r in tn.delivery_log(env)]
    assert sorted(kinds) == ["incident", "incident_closed"] and len(rec.calls) == 2


# ── 預覽 ──
def test_預覽不送不寫表(env):
    _enable(env)
    before = {r[0] for r in env.execute("SELECT name FROM sqlite_master")}
    for k in ("alert", "recovery", "daily", "weekly"):
        p = tn.preview(env, k)
        assert p["card"]["type"] == "message"
    assert {r[0] for r in env.execute("SELECT name FROM sqlite_master")} == before
    assert not env.execute("SELECT name FROM sqlite_master WHERE name='teams_notify_delivery'").fetchone()
    assert "示範" in tn.preview(env, "alert")["title"]


# ── 密鑰 ──
def test_網址是密鑰_加密_不回傳_不進紀錄(env):
    _enable(env, summary_daily=True)
    raw = env.execute("SELECT value FROM app_settings WHERE key=?", (tn.SECRET_KEY,)).fetchone()[0]
    assert "logic.azure.com" not in raw and "SECRET" not in raw
    assert tn.get_webhook(env) == REAL
    pub = json.dumps(tn.public_settings(env), ensure_ascii=False)
    assert "SECRET" not in pub and "azure" not in pub and tn.public_settings(env)["webhook_set"] is True
    _prime(env)
    _hist(env, "new", new_state="當機")
    bad = lambda url, payload: (None, f"ConnectionError to {REAL} failed host prod-12.japaneast.logic.azure.com")   # noqa: E731
    tn.tick(env, T0, bad)
    dump = json.dumps(tn.delivery_log(env), ensure_ascii=False) + json.dumps(
        [dict(r) for r in env.execute("SELECT * FROM teams_notify_delivery")], ensure_ascii=False)
    assert "SECRET" not in dump and "azure" not in dump and "workflows/" not in dump
    assert tn.get_webhook(env) is not None
    tn.clear_webhook(env)
    assert tn.get_webhook(env) is None and not tn.webhook_is_set(env)


@pytest.mark.parametrize("bad", ["http://prod-1.logic.azure.com/x", "https://evil.example.com/x", "https://169.254.169.254/latest",
                                 "https://user:pw@prod-1.logic.azure.com/x", "ftp://x.logic.azure.com", "", "https://logic.azure.com.evil.com/x",
                                 "http://127.0.0.1:9/x"])
def test_網址檢查擋SSRF(env, bad):
    with pytest.raises(tn.WebhookError):
        tn.set_webhook(env, bad)


def test_網址檢查接受官方網域(env):
    for ok in ("https://prod-1.westus.logic.azure.com:443/workflows/a/triggers/manual/paths/invoke?sig=s",
               "https://abc.environment.api.powerplatform.com/powerautomate/automations/direct/workflows/a/triggers/manual/paths/invoke?sig=s"):
        tn.set_webhook(env, ok)


# ── 閘門 ──
def test_對外送出要先確認live_confirmed(env):
    _enable(env, live_confirmed=False)
    _prime(env)
    _hist(env, "new", new_state="當機")
    rec = Rec()
    out = tn.tick(env, T0, rec)
    assert not rec.calls and out["delivery"]["held"]
    assert tn.delivery_log(env)[0]["status"] == "pending"
    env.execute("UPDATE app_settings SET value=json_set(value,'$.live_confirmed',json('true')) WHERE key=?", (tn.SETTING_KEY,))
    env.commit()
    tn.tick(env, T0 + timedelta(seconds=5), rec)
    assert len(rec.calls) == 1


def test_沒啟用或設定壞掉不動作(env):
    assert tn.tick(env, T0, Rec())["skipped"] == "未啟用"
    env.execute("INSERT INTO app_settings (key,value) VALUES (?, '{bad')", (tn.SETTING_KEY,))
    env.commit()
    assert tn.tick(env, T0, Rec())["skipped"] == "設定壞掉"
    with pytest.raises(tn.SettingsCorrupt):
        tn.load_settings(env)


def test_設定驗證():
    for bad in ({"min_severity": "x"}, {"summary_time": "25:00"}, {"quiet_start": "22:00", "quiet_end": ""},
                {"max_attempts": 0}, {"max_attempts": 99}, {"base_backoff_sec": 1}, {"weekly_day": 7}):
        with pytest.raises(ValueError):
            tn.validate({**tn.DEFAULTS, **bad})
    assert tn.validate(tn.DEFAULTS)["enabled"] is False


# ── 本機假接收端（真的走 HTTP） ──
class _Sink(BaseHTTPRequestHandler):
    got: list = []
    code = 202

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        _Sink.got.append((self.path, self.headers.get("Content-Type"), self.rfile.read(n)))
        self.send_response(_Sink.code); self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture()
def sink():
    _Sink.got, _Sink.code = [], 202
    srv = HTTPServer(("127.0.0.1", 0), _Sink)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/hook?sig=SECRETSIG"
    srv.shutdown()


def test_本機模擬接收端_完整流程(env, sink, monkeypatch):
    monkeypatch.setenv("WEBIT_TEAMS_ALLOW_LOOPBACK", "1")
    tn.set_webhook(env, sink)
    tn.save_settings(env, {**tn.DEFAULTS, "enabled": True})     # loopback 不需 live_confirmed
    tn.tick(env, T0)                                            # 預設 http transport，真的 POST 到本機
    _hist(env, "new", new_state="當機")
    _hist(env, "cleared", old_state="當機")
    tn.tick(env, T0 + timedelta(seconds=1))
    tn.tick(env, T0 + timedelta(seconds=2))
    assert len(_Sink.got) == 2
    path, ctype, body = _Sink.got[0]
    assert ctype == "application/json" and json.loads(body)["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert [r["status"] for r in tn.delivery_log(env)] == ["sent", "sent"]
    _Sink.code = 500
    r = tn.send_test(env)
    assert r["ok"] is False and r["http"] == 500
    _Sink.code = 202
    assert tn.send_test(env)["ok"] is True


def test_測試送出對真實網址一律拒絕(env):
    tn.set_webhook(env, REAL)
    rec = Rec()
    r = tn.send_test(env, rec)
    assert r["ok"] is False and not rec.calls and "模擬" in r["reason"]


# ── 路由 ──
def test_API_權限與遮罩(env, monkeypatch):
    import api
    import auth
    from fastapi.testclient import TestClient

    def _dep():
        yield env

    api.app.dependency_overrides[api.get_db] = _dep
    try:
        client = TestClient(api.app)
        assert client.get("/api/teams-notify/settings").status_code == 401
        db.create_user(env, "admin", auth.hash_password("test-password-123"))
        db.create_user(env, "bob", auth.hash_password("test-password-123"), role="user")
        client.post("/api/auth/login", json={"username": "bob", "password": "test-password-123"})
        assert client.get("/api/teams-notify/settings").status_code == 200
        for m, p, kw in (("put", "/api/teams-notify/settings", {"json": {"enabled": False}}), ("put", "/api/teams-notify/webhook", {"json": {"url": REAL}}),
                         ("delete", "/api/teams-notify/webhook", {}), ("post", "/api/teams-notify/test", {}), ("post", "/api/teams-notify/log/1/retry", {})):
            assert getattr(client, m)(p, **kw).status_code == 403, p
        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"username": "admin", "password": "test-password-123"})
        assert client.put("/api/teams-notify/settings", json={"enabled": True}).status_code == 400      # 沒網址不能啟用
        assert client.put("/api/teams-notify/webhook", json={"url": "https://evil.example.com/x"}).status_code == 400
        r = client.put("/api/teams-notify/webhook", json={"url": REAL})
        assert r.status_code == 200
        assert client.put("/api/teams-notify/settings", json={"enabled": True, "max_attempts": 99}).status_code == 400
        assert client.put("/api/teams-notify/settings", json={"enabled": True, "live_confirmed": False}).status_code == 200
        everything = ""
        for p in ("/api/teams-notify/settings", "/api/teams-notify/log", "/api/teams-notify/preview?kind=alert", "/api/activity"):
            rr = client.get(p)
            assert rr.status_code == 200, p
            everything += rr.text
        assert "SECRET" not in everything and "workflows" not in everything and "sig=" not in everything     # admin 只會看到網域（確認對話框用），看不到網址
        assert client.get("/api/teams-notify/settings").json()["settings"]["webhook_set"] is True
        assert client.get("/api/teams-notify/preview?kind=zzz").status_code == 400
        assert client.post("/api/teams-notify/test").json()["ok"] is False
    finally:
        api.app.dependency_overrides.clear()
