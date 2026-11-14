"""Teams／信件通知新格式（2.060.x，使用者 11-05「看不懂重點」；交接摘要 2026-11-04_NB）。每一種都釘：
第一行（燈號＋級別｜什麼事：幾台）、最多 5 行、「另有 N 台」、結尾「開啟系統」、信件不放事件ID／維護說明／不重複主旨。
另含記憶體／SWAP 新告警（預設關閉、同日同種一次、SWAP 行帶記憶體）。全部假資料（IP 192.0.2.x／10.99.x）。"""
import gzip
import json
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import capacity_analysis as ca  # noqa: E402
import db  # noqa: E402
import mem_alert  # noqa: E402
import notice_fmt as nf  # noqa: E402
import relay_import  # noqa: E402
import teams_notify as tn  # noqa: E402
import verify_numbers as vn  # noqa: E402

NOW = datetime(2026, 11, 11, 16, 10)
DAY = "2026-11-10"


def _texts(card):
    c = card["attachments"][0]["content"]
    return [b["text"] for b in c["body"]], [a["url"] for a in c.get("actions") or []]


# ---------------------------------------------------------------- 共同格式
def test_共同格式_第一行_最多5行_另有N台_開啟系統():
    lines = nf.cap([f"h{i}" for i in range(8)])
    assert lines == ["h0", "h1", "h2", "h3", "h4", "另有 3 台"]
    assert nf.cap(["a", "b"]) == ["a", "b"]
    first = nf.head("🔴", "緊急", "磁碟使用率 ≥90%", 8)
    assert first == "🔴 緊急｜磁碟使用率 ≥90%：8 台"
    card = nf.card(first, lines, "http://10.99.0.1:3000/v2/storage")
    texts, urls = _texts(card)
    assert texts[0] == first and len(texts) == 7 and urls == ["http://10.99.0.1:3000/v2/storage"]
    assert nf.plain(first, lines, "http://x").splitlines()[-1] == "開啟系統：http://x"


def test_信件_不放事件ID_不放維護說明_內文不重複主旨(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def ehlo(self):
            pass

        def send_message(self, m):
            sent.append(m)

        def quit(self):
            pass
    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    card = tn._with_event_id(nf.card("🔴 緊急｜磁碟使用率 ≥90%：1 台", ["srv01｜C:｜93%"], "http://10.99.0.1:3000/v2/storage"), "disk:b1:critical:1")
    code, _, _ = tn.mail_transport({"smtp_host": "mail.example.test", "mail_from": "w@example.test"}, "mailto:ops@example.test",
                                   json.dumps(card, ensure_ascii=False).encode())
    assert code == 200
    m: EmailMessage = sent[0]
    body = m.get_body(("plain",)).get_content()
    assert m["Subject"] == "WEBIT3-ALERT 🔴 緊急｜磁碟使用率 ≥90%：1 台"
    assert body.strip().splitlines() == ["srv01｜C:｜93%", "開啟系統：http://10.99.0.1:3000/v2/storage"]
    assert "事件ID" not in body and "維護說明" not in body and not hasattr(tn, "MAIL_FOOTER")


# ---------------------------------------------------------------- WhatsUp 斷線／恢復、測試
def test_WhatsUp斷線_恢復帶斷線時長():
    cfg = {**tn.DEFAULTS, "base_url": "http://10.99.0.1:3000"}
    t, u = _texts(tn.alert_card(cfg, {"device_id": "7", "name": "srv01", "ip": "192.0.2.7", "new_state": "Down", "problem": "Ping 不通", "at": "2026-11-11 08:00:00"}))
    assert t[0] == "🔴 嚴重｜WhatsUp 斷線：1 台" or t[0].endswith("｜WhatsUp 斷線：1 台")
    assert t[1] == "srv01｜192.0.2.7｜Down｜Ping 不通｜2026-11-11 08:00:00 開始" and u == ["http://10.99.0.1:3000/v2/incident"]
    t, _ = _texts(tn.alert_card(cfg, {"device_id": "7", "name": "srv01", "ip": "192.0.2.7", "old_state": "Down", "at": "2026-11-11 09:05:00",
                                      "down_at": "2026-11-11 08:00:00"}, recovery=True))
    assert t[0] == "🟢 恢復｜WhatsUp 斷線已恢復：1 台" and t[1].endswith("｜斷線 1 小時 5 分")


def test_測試訊息一行():
    card = nf.card(f"{nf.EMOJI['test']} 測試｜Teams 通知測試（不是告警）08:00", [], color="Accent")
    t, _ = _texts(card)
    assert t == ["🧪 測試｜Teams 通知測試（不是告警）08:00"]


# ---------------------------------------------------------------- 記憶體／SWAP
def _perf(c, ip, mem_max):
    obj = {"ts": [f"{DAY}T00:00:00", f"{DAY}T10:00:00"], "cols": {"cpu_busy": [10, 10], "mem_used": [50, 60]},
           "max": {"cpu_busy": [10, 10], "mem_used": [mem_max, 55]}, "interval_s": 300, "kind": "bucket", "tz": "UTC", "src": "relay"}
    c.execute("INSERT INTO capacity_whatsup_perf (host, day, interval_s, samples, data, updated_at) VALUES (?,?,?,?,?,?)",
              (ip, DAY, 300, 2, gzip.compress(json.dumps(obj).encode()), "x"))


def _disk(c, ip, disk, peak):
    d = {"ts": [f"{DAY}T00:00:00", f"{DAY}T10:00:00"], "avg": [10, peak], "max": [10, peak], "min": [10, peak], "tz": "UTC"}
    c.execute("INSERT INTO relay_disk_util (host, day, disk, interval_s, samples, data, batch_id, updated_at) VALUES (?,?,?,?,?,?,?,?)",
              (ip, DAY, disk, 300, 2, gzip.compress(json.dumps(d).encode()), "b1", "x"))


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    relay_import.ensure(c)
    c.execute(ca.WU_TABLE_SQL)
    db.insert_hardware(c, asset_serial="H-1", hostname="app01", ip="10.99.1.1")
    for i in range(1, 9):
        _perf(c, f"10.99.1.{i}", 90 + i * 0.5)          # 8 台都 ≥90
    _perf(c, "10.99.1.20", 50)
    _disk(c, "10.99.1.1", "Swap", 70)
    _disk(c, "10.99.1.2", "Virtual Memory", 40)
    _disk(c, "10.99.1.3", "C:", 99)                   # 不是 swap
    c.commit()
    yield c
    c.close()


def test_記憶體SWAP_預設關閉_不送(conn):
    assert mem_alert.load_settings(conn) == {"mem_enabled": False, "mem_threshold": 90.0, "swap_enabled": False, "swap_threshold": 50.0}
    assert mem_alert.evaluate(conn, "b1", NOW) == {"status": "disabled"}
    assert mem_alert.history(conn) == []


def test_記憶體_第一行_5行上限_另有N台_同日同種一次(conn):
    mem_alert.save_settings(conn, {"mem_enabled": True})
    out = mem_alert.evaluate(conn, "b1", NOW)
    assert out["day"] == DAY and out["sent"][0]["kind"] == "mem" and out["sent"][0]["hosts"] == 8
    h = mem_alert.history(conn)[0]
    lines = h["summary"].splitlines()
    assert lines[0] == f"🟠 記憶體｜記憶體使用率 ≥90%（{DAY}）：8 台"
    assert len(lines) == 7 and lines[-1] == "另有 3 台" and lines[1].startswith("192.0.2") is False
    assert "記憶體最高 94%" in lines[1]                          # 最高的在最上面
    assert mem_alert.evaluate(conn, "b2", NOW)["sent"] == []     # 同一天同一種只一次


def test_SWAP_同一行帶記憶體_只看名稱像swap的(conn):
    mem_alert.save_settings(conn, {"swap_enabled": True, "swap_threshold": 50})
    mem_alert.evaluate(conn, "b1", NOW)
    h = mem_alert.history(conn)[0]
    lines = h["summary"].splitlines()
    assert lines[0] == f"🟠 SWAP｜SWAP 使用率 ≥50%（{DAY}）：1 台"
    assert len(lines) == 2 and "SWAP 70%（Swap）" in lines[1] and "記憶體最高 90.5%" in lines[1]
    assert json.loads(h["items_json"])[0]["mem"] == 90.5


def test_記憶體告警_門檻範圍與Teams排入(conn):
    with pytest.raises(ValueError):
        mem_alert.save_settings(conn, {"mem_threshold": 0})
    tn.ensure(conn)
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('teams_notify', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (json.dumps({**tn.DEFAULTS, "enabled": True}),))
    mem_alert.save_settings(conn, {"mem_enabled": True})
    try:
        on = tn.load_settings(conn)["enabled"]
    except tn.SettingsCorrupt:
        on = False
    out = mem_alert.evaluate(conn, "b1", NOW)
    if on:
        q = conn.execute("SELECT kind, title FROM teams_notify_delivery").fetchall()
        assert q[0][0] == "mem_alert" and q[0][1].startswith("🟠 記憶體｜")
    assert out["sent"][0]["key"] == (f"mem:{DAY}:mem" if on else None)


def test_匯入掛勾_只看WhatsUp_失敗不影響(conn, monkeypatch):
    mem_alert.save_settings(conn, {"mem_enabled": True})
    mem_alert.after_import(conn, {"status": "imported", "source": "aria_host", "batch_id": "x"})
    assert mem_alert.history(conn) == []

    def boom(*a, **k):
        raise RuntimeError("壞了")
    monkeypatch.setattr(mem_alert, "evaluate", boom)
    mem_alert.after_import(conn, {"status": "imported", "source": "whatsup", "batch_id": "x"})     # 不丟例外


def test_verify_numbers_記憶體告警台數等於明細(conn):
    mem_alert.save_settings(conn, {"mem_enabled": True, "swap_enabled": True})
    mem_alert.evaluate(conn, "b1", NOW)
    before = vn.FAILS
    vn.check_mem_alert(conn)
    assert vn.FAILS == before
    conn.execute("UPDATE mem_alert_sent SET hosts = 99 WHERE kind = 'mem'")
    conn.commit()
    vn.check_mem_alert(conn)
    assert vn.FAILS == before + 1
    vn.FAILS = before


def test_第一行太長_保留結尾台數_不切壞跳脫():
    import notice_fmt
    first = "🔴 緊急｜磁碟使用率 ≥90%（" + "很長的說明*_" * 60 + "）：8 台"
    c = notice_fmt.card(first, [])
    t = c["attachments"][0]["content"]["body"][0]["text"]
    assert t.endswith("：8 台") and "…" in t
    assert not t.endswith("\\")


def test_記憶體_後到的資料才超標_補發一次只列新增(conn):
    mem_alert.save_settings(conn, {"mem_enabled": True})
    mem_alert.evaluate(conn, "b1", NOW)                       # 第一則：8 台
    _perf(conn, "10.99.1.30", 97)                             # 後到的批次才有這台
    _perf(conn, "10.99.1.31", 91)
    conn.commit()
    out = mem_alert.evaluate(conn, "b2", NOW)
    assert [s["kind"] for s in out["sent"]] == ["mem+"] and out["sent"][0]["hosts"] == 2
    h = [x for x in mem_alert.history(conn) if x["kind"] == "mem+"][0]
    first = h["summary"].splitlines()[0]
    assert "補充" in first and first.endswith("：2 台")
    _perf(conn, "10.99.1.32", 99)
    conn.commit()
    assert mem_alert.evaluate(conn, "b3", NOW)["sent"] == []   # 每天每種最多補一次
    import verify_numbers as vn
    before = vn.FAILS
    vn.check_mem_alert(conn)
    assert vn.FAILS == before
