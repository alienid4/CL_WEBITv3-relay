"""Teams 通知「內部寄信」投遞方式（channel=email）的釘子。

正式區連不到外網：改用「寄信設定（SMTP）」的郵件主機寄信，主旨開頭 WEBIT3-ALERT，
由收件人的 Power Automate 流程轉成 Teams 私訊。全部不對外：smtplib.SMTP 一律換成假物件。
"""
import smtplib
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import teams_notify as tn  # noqa: E402
import vuln_notify  # noqa: E402
from test_teams_notify_hardening import T0, _login, client, env  # noqa: E402,F401

SMTP = {"smtp_host": "10.0.0.25", "smtp_port": 25, "mail_from": "webit3@example.com"}


@pytest.fixture(autouse=True)
def _reset_mono():
    tn._LIVE_LAST_MONO = None
    yield
    tn._LIVE_LAST_MONO = None


class FakeSMTP:
    sent: list = []
    fail_connect = False
    fail_send: Exception | None = None

    def __init__(self, host, port, timeout=None):
        if FakeSMTP.fail_connect:
            raise OSError("connect refused")
        self.host, self.port = host, port

    def ehlo(self):
        pass

    def send_message(self, msg):
        if FakeSMTP.fail_send:
            raise FakeSMTP.fail_send
        FakeSMTP.sent.append(msg)

    def quit(self):
        pass


@pytest.fixture(autouse=True)
def _fake_smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail_connect, FakeSMTP.fail_send = [], False, None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    yield


def _mail_on(conn, smtp=True, **kw):
    if smtp:
        vuln_notify.save_settings(conn, dict(SMTP))
    tn.save_settings(conn, {**tn.DEFAULTS, "enabled": True, "live_confirmed": True, "channel": "email",
                            "mail_to": "ops@example.com", **kw})
    conn.commit()


def _seed(conn, n=1):
    tn.ensure(conn)
    card = tn._card("嚴重告警 host-01", "Attention", [("IP", "192.0.2.10"), ("狀態", "當機")], link="http://10.0.0.1:3000/x")
    for i in range(n):
        tn._enqueue(conn, f"wug:{i}:new", "alert", f"wug:{i}", "critical", f"A{i}", card, T0)
    conn.commit()


def test_validate_收件信箱格式與投遞方式():
    out = tn.validate({**tn.DEFAULTS, "channel": "email", "mail_to": " a@x.com ; b@y.com.tw "})
    assert out["mail_to"] == "a@x.com,b@y.com.tw"
    with pytest.raises(ValueError):
        tn.validate({**tn.DEFAULTS, "channel": "email", "mail_to": "not-an-email"})
    with pytest.raises(ValueError):
        tn.validate({**tn.DEFAULTS, "channel": "sms"})


def test_missing_寄信模式指名缺什麼(env):
    tn.save_settings(env, {**tn.DEFAULTS, "channel": "email"})
    miss = tn.missing(env)
    assert "收件信箱" in miss and any("SMTP 主機" in m for m in miss)
    assert "Workflow 網址" not in miss
    _mail_on(env)
    assert tn.missing(env) == []


def test_排程寄信_主旨帶標記_內容含卡片資料(env):
    _mail_on(env)
    _seed(env)
    out = tn.process_queue(env, tn.load_settings(env), T0)
    assert out["sent"] == 1 and out["held"] is None
    msg = FakeSMTP.sent[0]
    assert msg["Subject"].startswith("WEBIT3-ALERT ") and "嚴重告警 host-01" in msg["Subject"]
    assert msg["To"] == "ops@example.com" and msg["From"] == "webit3@example.com"
    body = msg.get_body(("plain",)).get_content()
    assert "IP：192.0.2.10" in body and "http://10.0.0.1:3000/x" in body
    assert "\\" not in body                          # Markdown 轉義已還原
    assert "【維護說明】" in body and "投遞紀錄" in body  # 信尾有來源／路徑／排查說明
    row = env.execute("SELECT status FROM teams_notify_delivery").fetchone()
    assert row[0] == "sent"


def test_沒設SMTP_不送_留在待送(env):
    _mail_on(env, smtp=False)
    _seed(env)
    out = tn.process_queue(env, tn.load_settings(env), T0)
    assert out["held"] and "寄信設定" in out["held"]
    assert FakeSMTP.sent == []
    assert env.execute("SELECT status FROM teams_notify_delivery").fetchone()[0] == "pending"


def test_沒勾對外送出已確認_不寄(env):
    _mail_on(env, live_confirmed=False)
    _seed(env)
    out = tn.process_queue(env, tn.load_settings(env), T0)
    assert out["held"] and FakeSMTP.sent == []


def test_連不上郵件主機_可重試(env):
    _mail_on(env)
    _seed(env)
    FakeSMTP.fail_connect = True
    tn.process_queue(env, tn.load_settings(env), T0)
    assert env.execute("SELECT status FROM teams_notify_delivery").fetchone()[0] == "failed"


def test_收件者被拒_不重試(env):
    _mail_on(env)
    _seed(env)
    FakeSMTP.fail_send = smtplib.SMTPRecipientsRefused({"ops@example.com": (550, b"no")})
    tn.process_queue(env, tn.load_settings(env), T0)
    assert env.execute("SELECT status FROM teams_notify_delivery").fetchone()[0] == "dead"


def test_SMTP_4xx_暫時失敗重試(env):
    _mail_on(env)
    _seed(env)
    FakeSMTP.fail_send = smtplib.SMTPDataError(451, b"try later")
    tn.process_queue(env, tn.load_settings(env), T0)
    assert env.execute("SELECT status FROM teams_notify_delivery").fetchone()[0] == "failed"


def test_送信途中斷線_不確定不自動重送(env):
    _mail_on(env)
    _seed(env)
    FakeSMTP.fail_send = smtplib.SMTPServerDisconnected("gone")
    tn.process_queue(env, tn.load_settings(env), T0)
    assert env.execute("SELECT status FROM teams_notify_delivery").fetchone()[0] == "uncertain"


def test_webhook模式不受影響_仍要網址(env):
    tn.save_settings(env, {**tn.DEFAULTS, "enabled": True, "live_confirmed": True, "mail_to": "ops@example.com"})
    env.commit()
    assert tn.missing(env) == ["Workflow 網址"]
    assert "Workflow" in tn.hold_reason(env, tn.load_settings(env))


def test_API_寄信模式_送測試訊息(env, client):
    c, _api = client
    _login(c, env)
    vuln_notify.save_settings(env, dict(SMTP))
    r = c.put("/api/teams-notify/settings", json={"channel": "email", "mail_to": "ops@example.com", "live_confirmed": True, "enabled": True})
    assert r.status_code == 200, r.text
    g = c.get("/api/teams-notify/settings").json()
    assert g["missing"] == [] and g["webhook_host"] == "ops@example.com"
    r = c.post("/api/teams-notify/test-live", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "sent"
    assert FakeSMTP.sent and FakeSMTP.sent[0]["Subject"].startswith("WEBIT3-ALERT ")


def test_API_寄信模式缺收件信箱_不能啟用(env, client):
    c, _api = client
    _login(c, env)
    vuln_notify.save_settings(env, dict(SMTP))
    r = c.put("/api/teams-notify/settings", json={"channel": "email", "enabled": True})
    assert r.status_code == 400 and "收件信箱" in r.json()["detail"]
