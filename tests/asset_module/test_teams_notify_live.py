"""ACT-03：admin 人為主動「送測試訊息到已設定的網址」（真的會送到 Teams 頻道）的釘子。

全部不對外：http_transport 一律被 monkeypatch 成假函式。
釘住：必須同時已設網址＋已勾對外送出已確認＋非本機且通過驗證的網址＋登入未停用；每 60 秒最多 1 次（429）；
稽核與投遞紀錄同交易；viewer／user 403；回應／稽核／紀錄不含網址；排程路徑不受影響。
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import teams_notify as tn  # noqa: E402
from test_teams_notify_hardening import REAL, T0, Rec, _enable, _login, client, env  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _reset_mono():
    tn._LIVE_LAST_MONO = None
    yield
    tn._LIVE_LAST_MONO = None


class Fake:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, url, payload, timeout=8, dns_guard=True):
        self.calls.append((url, payload))
        return self.results.pop(0) if self.results else (202, "", "")


DB_PATH = {}


@pytest.fixture(autouse=True)
def _dbpath(env):
    DB_PATH["p"] = env.execute("PRAGMA database_list").fetchone()[2]
    yield


def _ready(c, env, **kw):
    _login(c, env)
    assert c.put("/api/teams-notify/webhook", json={"url": REAL}).status_code == 200
    assert c.put("/api/teams-notify/settings", json={"live_confirmed": True, **kw}).status_code == 200


def _post(c, confirm=True):
    return c.post("/api/teams-notify/test-live", json={"confirm": confirm})


def test_成功送出_內容固定_回報狀態碼_稽核不含網址(env, client, monkeypatch):
    c, api = client
    fake = Fake((202, "", ""))
    monkeypatch.setattr(tn, "http_transport", fake)
    _ready(c, env)
    r = _post(c)
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["outcome"] == "sent" and r.json()["http"] == 202
    assert len(fake.calls) == 1 and fake.calls[0][0] == REAL
    card = json.loads(fake.calls[0][1])
    txt = json.dumps(card, ensure_ascii=False)
    assert "🧪 測試｜IT 戰情室連線測試（不是告警）" in txt and "_test:" in txt      # 2.060.x 新格式：一行
    assert "wug:" not in txt and "sw-core" not in txt
    acts = [r_[0] for r_ in env.execute("SELECT action FROM activity_log WHERE path='/api/teams-notify/test-live' AND action != 'change'")]
    assert len(acts) == 2 and any("開始" in a for a in acts) and any("結果：成功" in a and "202" in a for a in acts)     # 開始／結果兩段稽核
    everything = r.text + " ".join(acts) + json.dumps(tn.delivery_log(env), ensure_ascii=False) + json.dumps(
        [dict(x) for x in env.execute("SELECT * FROM teams_notify_delivery")], ensure_ascii=False)
    for frag in ("azure", "abc123", "sig=", "workflows"):
        assert frag not in everything, frag
    assert [x["kind"] for x in tn.delivery_log(env)] == ["live_test"]


def test_速率限制_連按第二次回429_有Retry_After(env, client, monkeypatch):
    c, api = client
    fake = Fake()
    monkeypatch.setattr(tn, "http_transport", fake)
    _ready(c, env)
    assert _post(c).status_code == 200
    r2 = _post(c)
    assert r2.status_code == 429 and int(r2.headers["Retry-After"]) >= 1 and "60" in r2.text
    assert len(fake.calls) == 1                                          # 第二次完全沒送


def test_速率限制_伺服器端時間窗_60秒後可再送(env, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(tn, "_mono", lambda: clock["t"])
    tn.set_webhook(env, REAL)
    tn.save_settings(env, {**tn.DEFAULTS, "live_confirmed": True}); env.commit()
    fake = Fake()
    assert tn.send_live_test(env, fake, now=T0)["outcome"] == "sent"; env.commit()
    clock["t"] = 30
    r = tn.send_live_test(env, fake, now=T0 + timedelta(seconds=30))
    assert r["code"] == "rate_limited" and 25 <= r["retry_after"] <= 31
    clock["t"] = 61
    assert tn.send_live_test(env, fake, now=T0 + timedelta(seconds=61))["outcome"] == "sent"
    assert len(fake.calls) == 2


def test_速率限制_換了行程_單調時鐘重置_仍由DB時間窗擋住(env, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(tn, "_mono", lambda: clock["t"])
    tn.set_webhook(env, REAL)
    tn.save_settings(env, {**tn.DEFAULTS, "live_confirmed": True}); env.commit()
    fake = Fake()
    assert tn.send_live_test(env, fake, now=T0)["outcome"] == "sent"; env.commit()
    tn._LIVE_LAST_MONO = None                                             # 模擬另一個行程（沒有記憶）
    r = tn.send_live_test(env, fake, now=T0 + timedelta(seconds=10))
    assert r["code"] == "rate_limited" and len(fake.calls) == 1


def test_速率限制_系統時間被往前撥也擋得住(env, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(tn, "_mono", lambda: clock["t"])
    tn.set_webhook(env, REAL)
    tn.save_settings(env, {**tn.DEFAULTS, "live_confirmed": True}); env.commit()
    fake = Fake()
    assert tn.send_live_test(env, fake, now=T0)["outcome"] == "sent"; env.commit()
    clock["t"] = 5                                                        # 單調時鐘只過了 5 秒，但牆上時鐘被撥快 10 分鐘
    r = tn.send_live_test(env, fake, now=T0 + timedelta(minutes=10))
    assert r["code"] == "rate_limited" and len(fake.calls) == 1


def test_未勾確認不能送(env, client, monkeypatch):
    c, api = client
    fake = Fake()
    monkeypatch.setattr(tn, "http_transport", fake)
    _login(c, env)
    c.put("/api/teams-notify/webhook", json={"url": REAL})
    r = _post(c)
    assert r.status_code == 409 and "對外送出已確認" in r.text and not fake.calls
    assert not tn._has(env, "teams_notify_delivery") or env.execute("SELECT COUNT(*) FROM teams_notify_delivery").fetchone()[0] == 0     # 被拒不占速率額度


def test_沒設網址_與_沒帶confirm_被拒(env, client, monkeypatch):
    c, api = client
    fake = Fake()
    monkeypatch.setattr(tn, "http_transport", fake)
    _login(c, env)
    assert _post(c).status_code == 409
    c.put("/api/teams-notify/webhook", json={"url": REAL})
    c.put("/api/teams-notify/settings", json={"live_confirmed": True})
    assert _post(c, confirm=False).status_code == 400
    assert c.post("/api/teams-notify/test-live", json={}).status_code == 400
    assert not fake.calls


def test_網址不合法或是本機模擬網址_拒絕(env, client, monkeypatch):
    c, api = client
    fake = Fake()
    monkeypatch.setattr(tn, "http_transport", fake)
    _ready(c, env)
    bad = tn._fernet(None).encrypt(b"https://evil.example.com/hook").decode()          # 直接塞進 DB（繞過存入檢查）
    env.execute("UPDATE app_settings SET value=? WHERE key=?", (bad, tn.SECRET_KEY)); env.commit()
    r = _post(c)
    assert r.status_code == 409 and "網址驗證未通過" in r.text and "evil" not in r.text and not fake.calls
    loop = tn._fernet(None).encrypt(b"http://127.0.0.1:9/x").decode()
    env.execute("UPDATE app_settings SET value=? WHERE key=?", (loop, tn.SECRET_KEY)); env.commit()
    r = _post(c)
    assert r.status_code == 409 and "本機模擬" in r.text and not fake.calls


def test_登入停用_viewer與user都403(env, client, monkeypatch):
    c, api = client
    fake = Fake()
    monkeypatch.setattr(tn, "http_transport", fake)
    _ready(c, env)
    c.post("/api/auth/logout")
    for who, role in (("u1", "user"), ("v1", "viewer")):
        _login(c, env, who, role)
        assert _post(c).status_code == 403
        c.post("/api/auth/logout")
    _login(c, env, "admin2", "admin")
    env.execute("INSERT INTO app_settings (key, value) VALUES ('auth_disabled','1') ON CONFLICT(key) DO UPDATE SET value='1'"); env.commit()
    r = _post(c)
    assert r.status_code == 403 and "登入停用" in r.text and not fake.calls


def test_開始稽核失敗_什麼都沒送_不占限流(env, client, monkeypatch):
    c, api = client
    fake = Fake()
    monkeypatch.setattr(tn, "http_transport", fake)
    _ready(c, env)
    env.execute("CREATE TRIGGER audit_boom BEFORE INSERT ON activity_log WHEN NEW.path = '/api/teams-notify/test-live' AND NEW.action LIKE '%開始%' BEGIN SELECT RAISE(ABORT, 'audit boom'); END")
    env.commit()
    r = _post(c)
    assert r.status_code == 500 and not fake.calls                                  # (a) 失敗：沒有送出
    assert env.execute("SELECT COUNT(*) FROM teams_notify_delivery").fetchone()[0] == 0
    env.execute("DROP TRIGGER audit_boom"); env.commit()
    tn._LIVE_LAST_MONO = None
    assert _post(c).status_code == 200                                              # 回滾後沒有占住限流


def test_結果稽核失敗_已送出但列保留_限流仍有效_如實說明(env, client, monkeypatch):
    c, api = client
    fake = Fake((202, "", ""))
    monkeypatch.setattr(tn, "http_transport", fake)
    _ready(c, env)
    env.execute("CREATE TRIGGER audit_boom BEFORE INSERT ON activity_log WHEN NEW.path = '/api/teams-notify/test-live' AND NEW.action LIKE '%結果%' BEGIN SELECT RAISE(ABORT, 'audit boom'); END")
    env.commit()
    r = _post(c)
    assert r.status_code == 500 and "接收端可能已收到" in r.text and "結果未記錄" in r.text and len(fake.calls) == 1      # (c) 失敗：如實說明
    rows = tn.delivery_log(env)
    assert len(rows) == 1 and rows[0]["kind"] == "live_test" and rows[0]["status"] == "sending"          # 這一列沒有消失
    assert rows[0]["status_label"] == "結果未記錄（可能已送達）"
    r2 = _post(c)                                                                                     # 限流依據已提交 → 60 秒內不能再送
    assert r2.status_code == 429 and len(fake.calls) == 1
    # 租約過期後（排程 tick 或下次處理）轉成不確定、顯示同樣的說明
    tn._LIVE_LAST_MONO = None
    tn.process_queue(env, tn.load_settings(env), tn.now_taipei() + timedelta(seconds=tn.LEASE_SEC + 5), Rec())
    row = tn.delivery_log(env)[0]
    assert row["status"] == "uncertain" and "結果未記錄" in (row["last_error"] or "")


def test_網路呼叫期間不持有寫入鎖(env, client, monkeypatch):
    import sqlite3 as _s
    c, api = client
    seen = {}

    def slow(url, payload, timeout=8, dns_guard=True):
        c2 = _s.connect(str(DB_PATH["p"]), timeout=0.3)             # 另一條連線在送出期間寫入：被鎖住就會 database is locked
        try:
            c2.execute("INSERT INTO app_settings (key, value) VALUES ('probe_during_send','1') ON CONFLICT(key) DO UPDATE SET value='1'")
            c2.commit()
            seen["wrote"] = True
        except _s.OperationalError as exc:
            seen["err"] = str(exc)
        finally:
            c2.close()
        return 202, "", ""

    monkeypatch.setattr(tn, "http_transport", slow)
    _ready(c, env)
    r = _post(c)
    assert r.status_code == 200 and seen.get("wrote") is True and "err" not in seen, seen


def test_queue統計與紀錄_排除test與live_test(env, client, monkeypatch):
    c, api = client
    monkeypatch.setattr(tn, "http_transport", Fake())
    _ready(c, env)
    _post(c)
    j = c.get("/api/teams-notify/settings").json()
    assert sum((j["queue"] or {}).values()) == 0                    # live_test 那一筆不算進佇列統計


def test_powerplatform網址形狀_通過驗證_DNS檢查正常(env, monkeypatch):
    import socket
    url = ("https://abc123def.4a.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/"
           "00000000000000000000000000000000/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=FAKEFAKEFAKE")
    assert tn.validate_webhook_url(url) == url
    tn.set_webhook(env, url); env.commit()
    assert tn.get_webhook(env) == url
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("20.50.60.70", 443))])           # 公開位址（mock，CI 無網路）
    seen = []
    def fake_conn(addr, timeout=None, **k):
        seen.append(addr)
        raise ConnectionRefusedError()
    monkeypatch.setattr(socket, "create_connection", fake_conn)
    assert tn.http_transport(url, b"{}")[2] == "connect" and seen == [("20.50.60.70", 443)]                    # 通過 DNS 檢查、連驗證過的 IP
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 443))])
    assert tn.http_transport(url, b"{}")[2] == "refused"                                                      # 解析到內部位址：拒絕
    out = tn.scrub("fail " + url, url)
    assert "FAKEFAKE" not in out and "powerplatform" not in out and "00000000" not in out


@pytest.mark.parametrize("res,outcome,ok", [((None, "TimeoutError", "uncertain"), "uncertain", False), ((500, "HTTP 500", ""), "failed", False),
                                            ((None, "ConnectionRefusedError", "connect"), "failed", False), ((202, "", ""), "sent", True)])
def test_結果分三態(env, res, outcome, ok):
    tn.set_webhook(env, REAL)
    tn.save_settings(env, {**tn.DEFAULTS, "live_confirmed": True}); env.commit()
    r = tn.send_live_test(env, Fake(res), now=T0)
    assert r["outcome"] == outcome and r["ok"] is ok
    if outcome == "uncertain":
        assert r["sent_out"] is True
    assert "azure" not in json.dumps(r, ensure_ascii=False)


def test_GET只給admin網域_不含完整網址(env, client):
    c, api = client
    _ready(c, env)
    j = c.get("/api/teams-notify/settings").json()
    assert j["webhook_host"] == "prod-12.japaneast.logic.azure.com"
    assert "workflows" not in json.dumps(j) and "sig=" not in json.dumps(j)
    c.post("/api/auth/logout")
    _login(c, env, "v2", "viewer")
    assert c.get("/api/teams-notify/settings").json()["webhook_host"] is None


def test_排程路徑不變_真實網址未確認仍不送_live_test列不進佇列(env):
    _enable(env, live_confirmed=False)
    tn.ensure(env)
    tn._enqueue(env, "wug:1:new", "alert", "wug:1", "critical", "A", tn._card("t", "Good", []), T0)
    env.execute("INSERT INTO teams_notify_delivery (event_key, kind, source_ref, payload_json, status, created_at) VALUES ('live_test:x','live_test','live_test','{}','pending',?)", (tn._ts(T0),))
    env.commit()
    rec = Rec()
    out = tn.process_queue(env, tn.load_settings(env), T0 + timedelta(seconds=1), rec)
    assert not rec.calls and out["held"]
    tn.save_settings(env, {**tn.load_settings(env), "live_confirmed": True}); env.commit()
    tn.process_queue(env, tn.load_settings(env), T0 + timedelta(seconds=2), rec)
    assert len(rec.calls) == 1                                           # 只有真正的告警列；live_test 列不被排程碰
    assert env.execute("SELECT status FROM teams_notify_delivery WHERE kind='live_test'").fetchone()[0] == "pending"
    assert tn.retry_dead(env, 2) is None


def test_函式層也擋登入停用(env):
    tn.set_webhook(env, REAL)
    tn.save_settings(env, {**tn.DEFAULTS, "live_confirmed": True})
    env.execute("INSERT INTO app_settings (key, value) VALUES ('auth_disabled','1') ON CONFLICT(key) DO UPDATE SET value='1'")
    env.commit()
    fake = Fake()
    r = tn.send_live_test(env, fake, now=T0)
    assert r["code"] == "auth_disabled" and not fake.calls
