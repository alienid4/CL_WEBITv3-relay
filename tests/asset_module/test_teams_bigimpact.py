"""夜間速查「影響範圍大」自動提醒（Teams，kind=big_impact）。

不對外送任何訊息：只組卡片、寫本機佇列表；夜間速查資料用假的（只換資料來源，口徑 BIG_HOSTS 取 night_desk 本身）。
釘住：>3 台才發（3 台不發、4 台發）、重複 tick 只一則、隔天同一台再發、恢復後不發（含排隊後才恢復）、
impact 算不出來不發、開關關閉不排且取消待送、收件目標不變（不發給主管）、主管聯絡查不到的文字、
靜默時段仍發、同一台同一天最多一則、預覽唯讀＋沒資料給示範、不含真實 IP。
"""
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import big_impact as bi  # noqa: E402
import db  # noqa: E402
import night_desk as nd  # noqa: E402
import teams_notify as tn  # noqa: E402

REAL = "https://prod-12.japaneast.logic.azure.com:443/workflows/abc123SECRETtoken/triggers/manual/paths/invoke?sig=SECRETSIG"
NOW = datetime(2026, 11, 16, 2, 10, 0)
ITEMS: list = []
CONTACTS: list = []


def _item(i="wug:1", mk="host-a", hosts=4, open_=True, reason=None, name="demo-esxi-01", ip="10.99.0.21", systems=4):
    big = hosts is not None and hosts > nd.BIG_HOSTS
    return {"id": i, "kind": "wug", "machine_key": mk, "name": name, "ip": ip, "platform": "esxi", "title": "設備 Down",
            "notified_at": "2026-11-16 01:55:00", "open": open_,
            "impact": {"hosts": hosts, "systems": systems, "big": big, "reason": reason}}


@pytest.fixture()
def conn(monkeypatch):
    monkeypatch.delenv("WEBIT_TEAMS_ALLOW_LOOPBACK", raising=False)
    ITEMS.clear()
    CONTACTS.clear()
    monkeypatch.setattr(nd, "notified", lambda c, h=None, now=None: {"items": list(ITEMS)})
    monkeypatch.setattr(nd, "_World", lambda c: object())
    monkeypatch.setattr(nd, "impact", lambda c, w, k, p=None: {"systems": [{"name": n} for n in ("系統甲", "系統乙", "系統丙", "系統丁")]})
    monkeypatch.setattr(nd, "contacts_of", lambda c, k, w: list(CONTACTS))
    with tempfile.TemporaryDirectory() as tmp:
        import credential_store
        monkeypatch.setattr(credential_store, "KEY_PATH_DEFAULT", str(Path(tmp) / "k.key"))
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = db.get_connection(p)
        tn.ensure(c)
        yield c
        c.close()


def _cfg(conn, **kw):
    tn.set_webhook(conn, REAL)
    cfg = {**tn.DEFAULTS, "enabled": True, "live_confirmed": True, "big_impact_notify": True, "base_url": "http://10.99.0.10:3000", **kw}
    tn.save_settings(conn, cfg)
    conn.commit()
    return tn.load_settings(conn)


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM teams_notify_delivery WHERE kind='big_impact' ORDER BY id")]


def _texts(row):
    card = json.loads(row["payload_json"])
    return [b["text"] for b in card["attachments"][0]["content"]["body"] if b["type"] == "TextBlock"]


def _ok(u, b):
    return (202, "")


# ---------------------------------------------------------------- 口徑與觸發
def test_口徑跟夜間速查同一把尺():
    assert nd.BIG_HOSTS == 3


def test_3台不發_4台發(conn):
    cfg = _cfg(conn)
    ITEMS.append(_item(hosts=3))
    assert tn.tick(conn, NOW, transport=_ok)["big_impact"] == [] and _rows(conn) == []
    ITEMS[:] = [_item(i="wug:2", mk="host-b", hosts=4)]
    assert tn.tick(conn, NOW, transport=_ok)["big_impact"] == ["big:wug:2"]
    r = _rows(conn)
    assert len(r) == 1 and r[0]["status"] == "sent" and r[0]["severity"] == "critical"


def test_重複tick只一則(conn):
    _cfg(conn)
    ITEMS.append(_item())
    for _ in range(4):
        tn.tick(conn, NOW, transport=_ok)
    assert len(_rows(conn)) == 1


def test_同一台同一天最多一則_即使代表項目換了(conn):
    _cfg(conn)
    ITEMS.append(_item(i="wug:1"))
    tn.tick(conn, NOW, transport=_ok)
    ITEMS[:] = [_item(i="disk:h:C:2026-11-16:t1")]
    tn.tick(conn, NOW, transport=_ok)
    assert len(_rows(conn)) == 1


def test_隔天同一台再發生會再發一次(conn):
    _cfg(conn)
    ITEMS.append(_item(i="wug:1"))
    tn.tick(conn, NOW, transport=_ok)
    ITEMS[:] = [_item(i="wug:9")]                      # 隔天新的事件
    tn.tick(conn, datetime(2026, 11, 17, 2, 10), transport=_ok)
    assert [r["event_key"] for r in _rows(conn)] == ["big:wug:1", "big:wug:9"]


def test_跨午夜同一個未恢復事件不重發(conn):
    """獨立審查 P2：23:30 發過，00:10 仍未恢復且代表項目換了，不可再發。"""
    _cfg(conn)
    ITEMS.append(_item(i="wug:1"))
    tn.tick(conn, datetime(2026, 11, 16, 23, 30), transport=_ok)
    ITEMS[:] = [_item(i="wug:2")]
    tn.tick(conn, datetime(2026, 11, 17, 0, 10), transport=_ok)
    assert [r["event_key"] for r in _rows(conn)] == ["big:wug:1"]


def test_單一項目組卡失敗不拖垮整個tick(conn, monkeypatch):
    """獨立審查 P2：build 拋例外只略過該項，其他項目照排、tick 不中斷。"""
    import big_impact as bi
    _cfg(conn)
    ITEMS[:] = [_item(i="wug:1", mk="host-a"), _item(i="wug:2", mk="host-b")]
    real = bi.build
    def flaky(c, cfg, it, demo=False):
        if it["machine_key"] == "host-a":
            raise RuntimeError("x")
        return real(c, cfg, it, demo)
    monkeypatch.setattr(bi, "build", flaky)
    tn.tick(conn, NOW, transport=_ok)
    assert [r["event_key"] for r in _rows(conn)] == ["big:wug:2"]


def test_已恢復不發(conn):
    _cfg(conn)
    ITEMS.append(_item(open_=False))
    tn.tick(conn, NOW, transport=_ok)
    assert _rows(conn) == []


def test_排隊後送出前恢復就取消不送(conn):
    cfg = _cfg(conn)
    ITEMS.append(_item())
    assert bi.enqueue(conn, cfg, NOW) == ["big:wug:1"]
    ITEMS[:] = [_item(open_=False)]
    sent = []
    tn.tick(conn, NOW, transport=lambda u, b: sent.append(b) or (202, ""))
    assert sent == [] and _rows(conn)[0]["status"] == "cancelled"


def test_送出前讀不到速查_留著待送不猜(conn, monkeypatch):
    cfg = _cfg(conn)
    ITEMS.append(_item())
    bi.enqueue(conn, cfg, NOW)
    monkeypatch.setattr(nd, "notified", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    sent = []
    tn.tick(conn, NOW, transport=lambda u, b: sent.append(b) or (202, ""))
    assert sent == [] and _rows(conn)[0]["status"] == "pending"


@pytest.mark.parametrize("kw", [dict(hosts=None, reason="資產庫對不到這台，影響範圍算不出來"), dict(hosts=5, reason="vCenter 資料讀不到"),
                                dict(hosts=None)])
def test_impact算不出來不發(conn, kw):
    _cfg(conn)
    it = _item(**kw)
    it["impact"]["big"] = True                          # 就算旗標被設成真，算不出來也不發
    ITEMS.append(it)
    tn.tick(conn, NOW, transport=_ok)
    assert _rows(conn) == []


def test_速查讀不到不拖垮tick(conn, monkeypatch):
    _cfg(conn)
    monkeypatch.setattr(nd, "notified", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert tn.tick(conn, NOW, transport=_ok)["big_impact"] == []


# ---------------------------------------------------------------- 開關與設定
def test_預設關閉():
    assert tn.DEFAULTS["big_impact_notify"] is False and tn.validate(dict(tn.DEFAULTS))["big_impact_notify"] is False


def test_開關關閉不排(conn):
    _cfg(conn, big_impact_notify=False)
    ITEMS.append(_item())
    tn.tick(conn, NOW, transport=_ok)
    assert _rows(conn) == []


def test_總開關關閉不排(conn):
    _cfg(conn, enabled=False)
    ITEMS.append(_item())
    tn.tick(conn, NOW, transport=_ok)
    assert _rows(conn) == []


def test_關掉開關取消待送(conn):
    cfg = _cfg(conn)
    ITEMS.append(_item())
    bi.enqueue(conn, cfg, NOW)
    tn.save_settings(conn, {**cfg, "big_impact_notify": False})
    conn.commit()
    assert _rows(conn)[0]["status"] == "cancelled"


def test_送出當下開關已關_取消(conn):
    cfg = _cfg(conn)
    ITEMS.append(_item())
    bi.enqueue(conn, cfg, NOW)
    conn.execute("UPDATE app_settings SET value = json_set(value, '$.big_impact_notify', json('false')) WHERE key = ?", (tn.SETTING_KEY,))
    conn.commit()
    sent = []
    tn.tick(conn, NOW, transport=lambda u, b: sent.append(b) or (202, ""))
    assert sent == [] and _rows(conn)[0]["status"] == "cancelled"


def test_設定驗證存成布林():
    assert tn.validate({**tn.DEFAULTS, "big_impact_notify": 1})["big_impact_notify"] is True


# ---------------------------------------------------------------- 靜默、有效期、目標
def test_靜默時段內仍發(conn):
    _cfg(conn, quiet_start="22:00", quiet_end="07:00")
    ITEMS.append(_item())
    sent = []
    tn.tick(conn, NOW, transport=lambda u, b: sent.append(b) or (202, ""))
    assert len(sent) == 1 and _rows(conn)[0]["status"] == "sent"


def test_有效期用EXPIRE_HOURS(conn):
    _cfg(conn)
    ITEMS.append(_item())
    tn.tick(conn, NOW, transport=lambda u, b: (500, "x"))
    r = _rows(conn)[0]
    d = datetime.strptime(r["expires_at"], "%Y-%m-%d %H:%M:%S") - datetime.strptime(r["created_at"], "%Y-%m-%d %H:%M:%S")
    assert d.total_seconds() == tn.EXPIRE_HOURS * 3600


def test_收件目標不變_不發給主管(conn):
    _cfg(conn)
    CONTACTS.append({"role": "主管", "name": "王大明", "phone": "0912-000-111", "state": "ok"})
    ITEMS.append(_item())
    urls = []
    tn.tick(conn, NOW, transport=lambda u, b: urls.append(u) or (202, ""))
    assert urls == [REAL]                               # 只有設定好的那一個目標；主管不是收件者
    assert tn.load_settings(conn)["mail_to"] == ""


# ---------------------------------------------------------------- 卡片
def test_卡片內容_哪台_什麼事_影響_主管_連結(conn):
    _cfg(conn)
    CONTACTS.append({"role": "主管", "name": "王大明", "phone": "0912-000-111", "state": "ok"})
    ITEMS.append(_item(hosts=5, systems=4))
    tn.tick(conn, NOW, transport=_ok)
    r = _rows(conn)[0]
    t = _texts(r)
    assert "影響範圍大，請通知主管" in t[0]
    assert any("demo-esxi-01" in x and "10.99.0.21" in x for x in t)
    assert any("設備 Down" in x for x in t)
    imp = next(x for x in t if x.startswith("影響："))
    assert "5 台" in imp and "4 個業務系統" in imp and "系統甲、系統乙、系統丙等" in imp and "系統丁" not in imp    # 最多列 3 個
    assert any("王大明" in x and "0912-000-111" in x for x in t)
    url = json.loads(r["payload_json"])["attachments"][0]["content"]["actions"][0]["url"]
    assert url == "http://10.99.0.10:3000/v2/night?host=host-a"


def test_主管聯絡查不到的文字(conn):
    _cfg(conn)
    CONTACTS.extend([{"role": "主管", "name": None, "phone": None, "state": "no_source", "note": "系統沒有主管聯絡資料來源"},
                     {"role": "值班", "name": "李四", "phone": "0900", "state": "ok"}])
    ITEMS.append(_item())
    tn.tick(conn, NOW, transport=_ok)
    assert "主管聯絡資料查不到" in _texts(_rows(conn)[0])


def test_補細節失敗仍發_主管寫查不到(conn, monkeypatch):
    _cfg(conn)
    monkeypatch.setattr(nd, "contacts_of", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    ITEMS.append(_item())
    tn.tick(conn, NOW, transport=_ok)
    r = _rows(conn)
    assert len(r) == 1 and "主管聯絡資料查不到" in _texts(r[0])


def test_不含真實IP(conn):
    _cfg(conn)
    ITEMS.append(_item())
    tn.tick(conn, NOW, transport=_ok)
    raw = _rows(conn)[0]["payload_json"]
    assert not re.search(r"\b10\.9[0-8]\.\d+\.\d+", raw)
    pv = json.dumps(tn.preview(conn, "bigimpact")["card"], ensure_ascii=False)
    assert not re.search(r"\b10\.9[0-8]\.\d+\.\d+", pv)


# ---------------------------------------------------------------- 預覽
def test_預覽唯讀_有資料用真資料(conn):
    _cfg(conn)
    ITEMS.append(_item())
    before = conn.execute("SELECT COUNT(*) FROM teams_notify_delivery").fetchone()[0]
    with tn.read_only(conn):
        pv = tn.preview(conn, "bigimpact")
    assert pv["demo"] is False and "請通知主管" in json.dumps(pv["card"], ensure_ascii=False) and "預覽" in json.dumps(pv["card"], ensure_ascii=False)
    assert conn.execute("SELECT COUNT(*) FROM teams_notify_delivery").fetchone()[0] == before


def test_預覽沒資料給示範(conn):
    _cfg(conn)
    with tn.read_only(conn):
        pv = tn.preview(conn, "bigimpact")
    s = json.dumps(pv["card"], ensure_ascii=False)
    assert pv["demo"] is True and "【示範】" in s and "請通知主管" in s


def test_預覽路由接受bigimpact():
    src = (BACKEND / "routers" / "digest.py").read_text(encoding="utf-8")
    assert '"bigimpact"' in src and "big_impact_notify" in src
