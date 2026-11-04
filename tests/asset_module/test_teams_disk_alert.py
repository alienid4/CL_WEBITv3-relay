"""WhatsUp 磁碟使用率 → Teams 告警（disk_alert.py）。

不連網路：批次在暫存資料夾現做（範例 IP 只用 192.0.2.x），Teams 送出一律 monkeypatch 成假 transport。
釘住：兩級門檻邊界（準備／緊急，取當天最高值）、準備→緊急同日再發、暴增偵測、舊單一門檻設定相容、去重（同台同碟同天一次）、Teams 未啟用只記錄、超過 30 台截斷、主機名對應、
範例按鈕（真實格式、標【測試】、不套門檻）、設定驗證與權限、評估失敗不影響匯入。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import credential_store  # noqa: E402
import db  # noqa: E402
import disk_alert as da  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402
import teams_notify as tn  # noqa: E402
from test_relay_import import SAT, rows_of, write_batch  # noqa: E402

REAL = "https://abc123def.4a.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/0000/triggers/manual/paths/invoke?api-version=1&sv=1.0&sig=FAKEFAKE"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(credential_store, "KEY_PATH_DEFAULT", str(tmp_path / "k.key"))
    monkeypatch.delenv("WEBIT_TEAMS_ALLOW_LOOPBACK", raising=False)
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)
    qw.set_clock(lambda: SAT(10))
    db.set_setting(c, ri.DIR_KEY, str(drop))
    db.set_setting(c, ri.ENABLED_KEY, "1")
    tn._LIVE_LAST_MONO = None
    yield c, drop, tmp_path / "t.db"
    qw.set_clock(None)
    tn._LIVE_LAST_MONO = None
    c.close()


def dev(ip, peak, disk="C:", day="2026-11-01"):
    pts = [(f"{day}T00:00:00Z", peak - 5, peak - 1, peak - 10), (f"{day}T01:00:00Z", peak - 3, peak, peak - 8)]   # 當天最高＝peak
    return {"ip": ip, "id": 1, "name": "n", "reports": {"disk-utilization": rows_of("disk", pts, diskId=1, disk=disk)}}


def batch(c, drop, name, devices):
    write_batch(drop, name, {d["ip"] + ".json": d for d in devices})
    return ri.run_scan(c, "manual")


def alert_on(c, thr=90, teams=True, warn=80, **extra):
    da.save_settings(c, {"enabled": True, "critical": thr, "warn": warn, **extra})
    if teams:
        tn.set_webhook(c, REAL)
        tn.save_settings(c, {**tn.DEFAULTS, "enabled": True, "live_confirmed": True})
    c.commit()


def hist(c):
    return da.history(c)


def deliveries(c):
    if not tn._has(c, "teams_notify_delivery"):
        return []
    return [dict(r) for r in c.execute("SELECT * FROM teams_notify_delivery WHERE kind='disk_alert' ORDER BY id")]


# ── 門檻邊界 ──
def test_兩級門檻邊界_取當天最高值_緊急先送(env):
    c, drop, _ = env
    alert_on(c)
    r = batch(c, drop, "b1", [dev("192.0.2.10", 79.9), dev("192.0.2.11", 80.0), dev("192.0.2.12", 89.9), dev("192.0.2.13", 90.0)])
    assert r["imported"] == 1
    h = {x["tier"]: x for x in hist(c)}
    assert set(h) == {"critical", "warn"} and all(x["status"] == "queued" for x in h.values())
    assert h["critical"]["hosts"] == 1 and "192.0.2.13｜C:｜90%" in h["critical"]["summary"] and h["critical"]["threshold"] == 90
    assert h["warn"]["hosts"] == 2 and "192.0.2.11｜C:｜80%" in h["warn"]["summary"] and "192.0.2.12｜C:｜89.9%" in h["warn"]["summary"]
    assert "192.0.2.10" not in h["warn"]["summary"] + h["critical"]["summary"]
    d = deliveries(c)
    assert len(d) == 2 and [x["severity"] for x in d] == ["critical", "warning"]          # 緊急那則先排
    assert d[0]["title"] == "磁碟使用率 ≥90%（緊急）：1 台" and d[1]["title"] == "磁碟使用率 ≥80%（準備）：2 台"
    txt = json.dumps(json.loads(d[0]["payload_json"]), ensure_ascii=False)
    assert "當天最高值" in txt and "0.2.13" in txt and "0.2.12" not in txt and "C:" in txt and "2026-11-01" in txt   # 行首「192.」會被轉義成 192\.（清單語法），所以只比對後段


def test_門檻可改_低門檻抓到更多(env):
    c, drop, _ = env
    alert_on(c, thr=80, warn=70)
    batch(c, drop, "b1", [dev("192.0.2.10", 85), dev("192.0.2.11", 79.9), dev("192.0.2.12", 69.9)])
    h = {x["tier"]: x for x in hist(c)}
    assert "192.0.2.10" in h["critical"]["summary"] and "192.0.2.11" in h["warn"]["summary"] and h["warn"]["hosts"] == 1


# ── 去重 ──
def test_同台同碟同天只告警一次_新的一天會再告警(env):
    c, drop, _ = env
    alert_on(c)
    batch(c, drop, "b1", [dev("192.0.2.10", 95)])
    batch(c, drop, "b2", [dev("192.0.2.10", 97)])                          # 同一天再來一批（數字更高）：不重發
    assert len(hist(c)) == 1 and len(deliveries(c)) == 1
    batch(c, drop, "b3", [dev("192.0.2.10", 95, day="2026-11-02")])        # 新的一天
    assert len(hist(c)) == 2 and len(deliveries(c)) == 2
    batch(c, drop, "b4", [dev("192.0.2.10", 95, disk="D:", day="2026-11-02")])   # 同台同天另一顆磁碟
    assert len(hist(c)) == 3


def test_低於門檻不寫歷史(env):
    c, drop, _ = env
    alert_on(c)
    batch(c, drop, "b1", [dev("192.0.2.10", 50)])
    assert hist(c) == [] and deliveries(c) == []


# ── 2.047.6：WhatsUp 原生回 0～1 小數（公司實測 0.96＝96%），以前原樣入庫，告警永遠不會響 ──
def _raw_dev(ip, pts, disk="C:"):
    return {"ip": ip, "id": 1, "name": "n", "reports": {"disk-utilization": [
        {"pollTimeUtc": t, "avgPercent": a, "maxPercent": m, "minPercent": n, "diskId": 1, "disk": disk} for t, a, m, n in pts]}}


def test_WhatsUp原樣小數_0點96會告警_卡片寫96百分比_0點4不會(env):
    c, drop, _ = env
    alert_on(c)
    write_batch(drop, "b1", {"192.0.2.10.json": _raw_dev("192.0.2.10", [("2026-11-01T00:00:00Z", 0.9, 0.96, 0.85)]),
                             "192.0.2.11.json": _raw_dev("192.0.2.11", [("2026-11-01T00:00:00Z", 0.4, 0.4, 0.4)]),
                             "192.0.2.12.json": _raw_dev("192.0.2.12", [("2026-11-01T00:00:00Z", 0.9, 0.9, 0.9)])})
    assert ri.run_scan(c, "manual")["imported"] == 1
    h = hist(c)
    assert len(h) == 1 and h[0]["hosts"] == 2, h                                 # 0.96 與剛好 0.9（＝90%）都算
    assert "192.0.2.10｜C:｜96%" in h[0]["summary"] and "192.0.2.12｜C:｜90%" in h[0]["summary"] and "192.0.2.11" not in h[0]["summary"]
    seen = {r[0]: r[1] for r in c.execute("SELECT host, pct FROM disk_alert_seen")}
    assert seen == {"ip:192.0.2.10": 96.0, "ip:192.0.2.12": 90.0}                # 去重鍵不受影響（台＋碟＋天），記的是百分比
    write_batch(drop, "b2", {"192.0.2.10.json": _raw_dev("192.0.2.10", [("2026-11-01T02:00:00Z", 0.97, 0.97, 0.97)])})
    ri.run_scan(c, "manual")
    assert len(hist(c)) == 1                                                     # 同台同碟同天：不重發


def test_範例卡片_原樣0點4顯示40百分比不是0點4(env):
    c, drop, _ = env
    write_batch(drop, "b1", {"192.0.2.10.json": _raw_dev("192.0.2.10", [("2026-11-01T00:00:00Z", 0.4, 0.4, 0.4)])})
    ri.run_scan(c, "manual")
    card, _title, _summary, _hosts = da.sample_card(c)
    txt = json.dumps(card, ensure_ascii=False)
    assert "40%" in txt and "0.4%" not in txt


# ── 開關 ──
def test_Teams未啟用_只記錄不送_之後啟用也不補送(env):
    c, drop, _ = env
    alert_on(c, teams=False)
    batch(c, drop, "b1", [dev("192.0.2.10", 95)])
    h = hist(c)
    assert len(h) == 1 and h[0]["status"] == "recorded" and h[0]["delivery_key"] is None
    assert deliveries(c) == []
    tn.set_webhook(c, REAL)
    tn.save_settings(c, {**tn.DEFAULTS, "enabled": True, "live_confirmed": True}); c.commit()
    batch(c, drop, "b2", [dev("192.0.2.10", 96)])                          # 同台同天：已記錄過，不補送
    assert deliveries(c) == []


def test_告警關閉時什麼都不做(env):
    c, drop, _ = env
    tn.set_webhook(c, REAL)
    tn.save_settings(c, {**tn.DEFAULTS, "enabled": True, "live_confirmed": True}); c.commit()
    batch(c, drop, "b1", [dev("192.0.2.10", 99)])
    assert hist(c) == [] and deliveries(c) == []
    cfg = da.load_settings(c)
    assert cfg["enabled"] is False and cfg["critical"] == 90 and cfg["warn"] == 80
    assert (cfg["surge_hours"], cfg["surge_rise"], cfg["surge_floor"]) == (5, 15, 85)


# ── 截斷與主機名 ──
def test_超過30台只列30行_其餘寫另有N台(env):
    c, drop, _ = env
    alert_on(c)
    batch(c, drop, "b1", [dev(f"192.0.2.{i}", 91 + (i % 9)) for i in range(1, 36)])
    h = hist(c)[0]
    lines = h["summary"].split("\n")
    assert h["hosts"] == 35 and len(lines) == 31 and lines[-1] == "另有 5 台"
    body = json.loads(deliveries(c)[0]["payload_json"])["attachments"][0]["content"]["body"]
    assert sum(1 for b in body if "｜" in b["text"]) == 30 and any("另有 5 台" in b["text"] for b in body)
    pcts = [float(x.split("｜")[2].rstrip("%")) for x in lines[:30]]
    assert pcts == sorted(pcts, reverse=True)                              # 最嚴重的在最上面


def test_主機名用資產對應_對不到顯示IP_多顆磁碟寫最高那顆(env):
    c, drop, _ = env
    c.execute("INSERT INTO hardware (asset_serial, hostname, ip) VALUES ('S-1', 'web01', '192.0.2.10')"); c.commit()
    alert_on(c)
    d1 = dev("192.0.2.10", 95)
    d1["reports"]["disk-utilization"] += rows_of("disk", [("2026-11-01T02:00:00Z", 97, 98, 96)], diskId=2, disk="D:")
    batch(c, drop, "b1", [d1, dev("192.0.2.99", 93)])
    s = hist(c)[0]["summary"]
    assert "web01（192.0.2.10）｜D:｜98%｜2026-11-01，等 2 顆" in s
    assert "192.0.2.99｜C:｜93%" in s


def test_卡片文字經過Markdown轉義(env):
    c, drop, _ = env
    alert_on(c)
    batch(c, drop, "b1", [dev("192.0.2.10", 95, disk="[x](http://e)")])
    txt = json.dumps(json.loads(deliveries(c)[0]["payload_json"]), ensure_ascii=False)
    assert "[x](http" not in txt


def test_同一台換IP匯入_同碟同天不重發_去重用machine_key(env):
    import machine_identity
    c, drop, _ = env
    db.set_setting(c, "machine_key_mode", "hostname")
    machine_identity.refresh(c)
    try:
        c.execute("INSERT INTO hardware (asset_serial, hostname, ip) VALUES ('S-1', 'web01', '192.0.2.10')")
        c.execute("INSERT INTO hardware (asset_serial, hostname, ip) VALUES ('S-2', 'web01', '192.0.2.12')")
        c.commit()
        alert_on(c)
        batch(c, drop, "b1", [dev("192.0.2.10", 95)])
        batch(c, drop, "b2", [dev("192.0.2.12", 96)])                      # 同一台（同 machine_key）另一個 IP、同碟同天
        assert len(hist(c)) == 1 and len(deliveries(c)) == 1
        keys = [r[0] for r in c.execute("SELECT host FROM disk_alert_seen")]
        assert keys == ["mk:h:web01"]
        batch(c, drop, "b3", [dev("192.0.2.99", 95)])                      # 對不到資產：用 ip: 當身分
        assert sorted(r[0] for r in c.execute("SELECT host FROM disk_alert_seen")) == ["ip:192.0.2.99", "mk:h:web01"]
        assert "web01（192.0.2.10）" in hist(c)[-1]["summary"]               # 訊息仍顯示主機名／IP
    finally:
        db.set_setting(c, "machine_key_mode", "legacy")
        machine_identity.refresh(c)


def test_after_import拋例外_匯入仍成功(env, monkeypatch):
    c, drop, _ = env
    alert_on(c)
    monkeypatch.setattr(da, "after_import", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = batch(c, drop, "b1", [dev("192.0.2.10", 95)])
    assert r["imported"] == 1 and r["results"][0]["status"] == "imported"
    assert c.execute("SELECT status FROM relay_import_batches WHERE batch_id='b1'").fetchone()[0] == "imported"
    assert c.execute("SELECT COUNT(*) FROM relay_disk_util").fetchone()[0] == 1          # 已提交的資料還在


def test_告警模組載入失敗_匯入仍成功(env, monkeypatch):
    c, drop, _ = env
    monkeypatch.setitem(sys.modules, "disk_alert", None)                    # import disk_alert 會丟 ImportError
    r = batch(c, drop, "b1", [dev("192.0.2.10", 95)])
    assert r["imported"] == 1 and r["results"][0]["status"] == "imported"


def test_評估失敗不影響匯入結果(env, monkeypatch):
    c, drop, _ = env
    alert_on(c)
    monkeypatch.setattr(da, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = batch(c, drop, "b1", [dev("192.0.2.10", 95)])
    assert r["imported"] == 1
    assert c.execute("SELECT status FROM relay_import_batches WHERE batch_id='b1'").fetchone()[0] == "imported"


def test_排程送出走既有teams佇列(env):
    c, drop, _ = env
    alert_on(c)
    batch(c, drop, "b1", [dev("192.0.2.10", 95)])
    calls = []
    tn.process_queue(c, tn.load_settings(c), tn.now_taipei(), lambda u, b: calls.append(b) or (202, "", ""))
    assert len(calls) == 1 and deliveries(c)[0]["status"] == "sent"
    assert hist(c)[0]["delivery_status"] == "sent"


# ── 設定驗證 ──
@pytest.mark.parametrize("bad", [49, 101, -1, "90", True, None])
def test_門檻驗證(bad):
    with pytest.raises(ValueError):
        da.validate({"enabled": True, "threshold": bad})


def test_門檻接受邊界與小數():
    v = da.validate({"warn": 50, "critical": 51})
    assert (v["warn"], v["critical"]) == (50, 51)
    assert da.validate({"critical": 100, "warn": 99.5})["critical"] == 100
    assert da.validate({"critical": 87.5})["critical"] == 87.5
    assert da.validate({"threshold": 100})["critical"] == 100              # 舊單一門檻鍵：當成緊急


@pytest.mark.parametrize("bad", [
    {"warn": 90, "critical": 90}, {"warn": 95, "critical": 90}, {"warn": 49}, {"warn": 101, "critical": 100},
    {"surge_hours": 0.4}, {"surge_hours": 25}, {"surge_rise": 0}, {"surge_rise": 101}, {"surge_floor": 49}, {"surge_floor": 101},
    {"surge_hours": "5"}, {"surge_rise": True}, {"warn": float("nan")},
])
def test_兩級與暴增設定驗證(bad):
    with pytest.raises(ValueError):
        da.validate({"enabled": True, **bad})


def test_暴增設定接受邊界():
    v = da.validate({"surge_hours": 0.5, "surge_rise": 1, "surge_floor": 50})
    assert (v["surge_hours"], v["surge_rise"], v["surge_floor"]) == (0.5, 1, 50)
    v = da.validate({"surge_hours": 24, "surge_rise": 100, "surge_floor": 100})
    assert (v["surge_hours"], v["surge_rise"], v["surge_floor"]) == (24, 100, 100)


# ── API ──
@pytest.fixture
def api_env(env, monkeypatch):
    import api
    import auth
    from fastapi.testclient import TestClient
    c, drop, p = env

    def _ov():
        cn = db.get_connection(p)
        try:
            yield cn
        finally:
            cn.close()
    api.app.dependency_overrides[api.get_db] = _ov
    for name, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(c, name, auth.hash_password("test-password-123"))
        c.execute("UPDATE users SET role = ? WHERE username = ?", (role, name))
    c.commit()

    def client(name):
        cl = TestClient(api.app, raise_server_exceptions=False)
        assert cl.post("/api/auth/login", json={"username": name, "password": "test-password-123"}).status_code == 200
        tok = cl.cookies.get("session_token")
        if tok:
            cl.headers["X-CSRF-Token"] = auth.csrf_token_for(tok)
        return cl
    yield c, drop, client
    api.app.dependency_overrides.clear()


class Fake:
    def __init__(self):
        self.calls = []

    def __call__(self, url, payload, timeout=8, dns_guard=True):
        self.calls.append((url, payload))
        return 202, "", ""


def test_API_設定權限與驗證(api_env):
    c, drop, client = api_env
    admin, alice, vera = client("admin"), client("alice"), client("vera")
    for who in (alice, vera):
        assert who.put("/api/teams-notify/disk-alert/settings", json={"threshold": 80}).status_code == 403
        assert who.post("/api/teams-notify/disk-alert/sample", json={"confirm": True}).status_code == 403
        r = who.get("/api/teams-notify/disk-alert")
        assert r.status_code == 200 and r.json()["can_edit"] is False
    for bad in (49, 101):
        assert admin.put("/api/teams-notify/disk-alert/settings", json={"threshold": bad}).status_code == 400
    assert admin.put("/api/teams-notify/disk-alert/settings", json={"threshold": "abc"}).status_code == 422
    r = admin.put("/api/teams-notify/disk-alert/settings", json={"enabled": True, "threshold": 85})      # 舊呼叫端：當成緊急門檻
    assert r.status_code == 200 and r.json()["settings"] == {"enabled": True, "warn": 80, "critical": 85, "surge_hours": 5, "surge_rise": 15, "surge_floor": 85}
    acts = [x[0] for x in c.execute("SELECT action FROM activity_log WHERE path='/api/teams-notify/disk-alert/settings' AND action != 'change'")]
    assert any("緊急=85%" in a and "準備=80%" in a for a in acts)
    assert admin.put("/api/teams-notify/disk-alert/settings", json={"warn": 85}).status_code == 400          # 準備不能 ≥ 緊急
    r = admin.put("/api/teams-notify/disk-alert/settings", json={"warn": 70, "critical": 95, "surge_hours": 3, "surge_rise": 20, "surge_floor": 90})
    assert r.status_code == 200 and r.json()["settings"] == {"enabled": True, "warn": 70, "critical": 95, "surge_hours": 3, "surge_rise": 20, "surge_floor": 90}
    j = admin.get("/api/teams-notify/disk-alert").json()
    assert j["settings"]["critical"] == 95 and j["settings"]["warn"] == 70 and j["can_edit"] is True and j["history"] == []


def test_API_登入停用時設定與範例都403(api_env):
    c, drop, client = api_env
    admin = client("admin")
    c.execute("INSERT INTO app_settings (key, value) VALUES ('auth_disabled','1') ON CONFLICT(key) DO UPDATE SET value='1'"); c.commit()
    assert admin.put("/api/teams-notify/disk-alert/settings", json={"threshold": 80}).status_code == 403
    assert admin.post("/api/teams-notify/disk-alert/sample", json={"confirm": True}).status_code == 403


def test_API_範例按鈕_用最近資料組真實格式_標測試_不套門檻(api_env, monkeypatch):
    c, drop, client = api_env
    admin = client("admin")
    fake = Fake()
    monkeypatch.setattr(tn, "http_transport", fake)
    tn.set_webhook(c, REAL)
    tn.save_settings(c, {**tn.DEFAULTS, "live_confirmed": True}); c.commit()          # Teams 通知不用啟用也能測
    assert admin.post("/api/teams-notify/disk-alert/sample", json={"confirm": True}).status_code == 409    # 還沒有資料
    batch(c, drop, "b1", [dev("192.0.2.10", 40)])
    batch(c, drop, "b2", [dev("192.0.2.11", 60, day="2026-11-02")])
    assert admin.post("/api/teams-notify/disk-alert/sample", json={"confirm": False}).status_code == 400
    r = admin.post("/api/teams-notify/disk-alert/sample", json={"confirm": True})
    assert r.status_code == 200 and r.json()["outcome"] == "sent" and len(fake.calls) == 1
    txt = json.dumps(json.loads(fake.calls[0][1]), ensure_ascii=False)
    assert "【測試】" in txt and "未套門檻" in txt and "0.2.11" in txt and "60%" in txt and "0.2.10" not in txt   # 最近一天的資料
    assert "緊急 ≥90%、準備 ≥80%" in txt
    h = admin.get("/api/teams-notify/disk-alert").json()["history"]
    assert len(h) == 1 and h[0]["kind"] == "sample" and h[0]["delivery_status"] == "sent"
    assert c.execute("SELECT COUNT(*) FROM disk_alert_seen").fetchone()[0] == 0          # 範例不記去重
    assert admin.post("/api/teams-notify/disk-alert/sample", json={"confirm": True}).status_code == 429      # 與送測試訊息共用 60 秒限流
    for frag in ("FAKEFAKE", "workflows", "sig="):
        assert frag not in json.dumps(h, ensure_ascii=False) + r.text


def test_API_範例未勾對外送出已確認不能送(api_env, monkeypatch):
    c, drop, client = api_env
    admin = client("admin")
    fake = Fake()
    monkeypatch.setattr(tn, "http_transport", fake)
    tn.set_webhook(c, REAL); c.commit()
    batch(c, drop, "b1", [dev("192.0.2.10", 40)])
    r = admin.post("/api/teams-notify/disk-alert/sample", json={"confirm": True})
    assert r.status_code == 409 and "對外送出已確認" in r.text and not fake.calls


# ── 2.049.0：兩級（準備／緊急）同日升級、重匯不重發、舊設定相容 ──
def test_準備後同日升到緊急會再發_之後不再發(env):
    c, drop, _ = env
    alert_on(c)
    batch(c, drop, "b1", [dev("192.0.2.10", 85)])
    assert [x["tier"] for x in hist(c)] == ["warn"]
    batch(c, drop, "b2", [dev("192.0.2.10", 92)])                          # 同台同碟同天升到緊急：再發一次（緊急）
    assert [x["tier"] for x in hist(c)] == ["critical", "warn"]
    assert "192.0.2.10｜C:｜92%" in hist(c)[0]["summary"]
    batch(c, drop, "b3", [dev("192.0.2.10", 95)])                          # 已發過緊急：不再發
    batch(c, drop, "b4", [dev("192.0.2.10", 85, day="2026-11-01", disk="C:")])
    assert len(hist(c)) == 2 and len(deliveries(c)) == 2
    tiers = sorted(r[0] for r in c.execute("SELECT tier FROM disk_alert_seen"))
    assert tiers == ["critical", "warn"]


def test_直接到緊急不補發準備(env):
    c, drop, _ = env
    alert_on(c)
    batch(c, drop, "b1", [dev("192.0.2.10", 95)])
    batch(c, drop, "b2", [dev("192.0.2.10", 85)])                          # 當天最高仍是 95（同列合併）；不會倒退發準備
    assert [x["tier"] for x in hist(c)] == ["critical"]


def test_同一份資料重新匯入不重發(env):
    c, drop, _ = env
    alert_on(c)
    devs = [dev("192.0.2.10", 85), dev("192.0.2.11", 95), _surge_dev("192.0.2.12", [("00:00:00", 70), ("02:00:00", 88)])]
    batch(c, drop, "b1", devs)
    n = len(hist(c))
    assert {x["tier"] for x in hist(c)} == {"critical", "warn", "surge"}
    batch(c, drop, "b1-again", devs)
    assert len(hist(c)) == n and len(deliveries(c)) == n


def test_舊單一門檻設定照樣能用_當成緊急_準備預設80(env):
    c, drop, _ = env
    c.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
              (da.SETTING_KEY, json.dumps({"enabled": True, "threshold": 85})))
    tn.set_webhook(c, REAL)
    tn.save_settings(c, {**tn.DEFAULTS, "enabled": True, "live_confirmed": True})
    c.commit()
    cfg = da.load_settings(c)
    assert cfg["critical"] == 85 and cfg["warn"] == 80 and "threshold" not in cfg
    batch(c, drop, "b1", [dev("192.0.2.10", 82), dev("192.0.2.11", 86), dev("192.0.2.12", 79)])
    h = {x["tier"]: x for x in hist(c)}
    assert "192.0.2.11" in h["critical"]["summary"] and "192.0.2.10" in h["warn"]["summary"]
    assert "192.0.2.12" not in h["critical"]["summary"] + h["warn"]["summary"]
    c.execute("UPDATE app_settings SET value = ? WHERE key = ?", (json.dumps({"enabled": True, "threshold": 80}), da.SETTING_KEY))
    assert da.load_settings(c)["warn"] == 75                                # 舊門檻 ≤ 80：準備退成門檻 − 5


def test_舊版去重表升級_舊列當緊急(env):
    c, drop, _ = env
    c.execute("DROP TABLE IF EXISTS disk_alert_seen")
    c.execute("CREATE TABLE disk_alert_seen (host TEXT NOT NULL, disk TEXT NOT NULL, day TEXT NOT NULL, pct REAL, batch_id TEXT, first_at TEXT NOT NULL, PRIMARY KEY (host, disk, day))")
    c.execute("INSERT INTO disk_alert_seen VALUES ('ip:192.0.2.10', 'C:', '2026-11-01', 95, 'old', '2026-11-01 05:00:00')")
    c.execute("DROP TABLE IF EXISTS disk_alert_history")
    c.execute("CREATE TABLE disk_alert_history (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL, batch_id TEXT, threshold REAL, "
              "hosts INTEGER, items INTEGER, status TEXT NOT NULL, delivery_key TEXT, summary TEXT)")
    c.commit()
    alert_on(c)
    batch(c, drop, "b1", [dev("192.0.2.10", 96), dev("192.0.2.11", 96)])     # .10 舊版已發過（緊急）：不重發
    rows = sorted(tuple(r) for r in c.execute("SELECT host, tier FROM disk_alert_seen"))
    assert rows == [("ip:192.0.2.10", "critical"), ("ip:192.0.2.11", "critical")]
    h = hist(c)
    assert len(h) == 1 and "192.0.2.11" in h[0]["summary"] and "192.0.2.10" not in h[0]["summary"] and h[0]["tier"] == "critical"


# ── 2.049.0：暴增 ──
def _surge_dev(ip, pts, disk="C:", day="2026-11-01"):
    """pts＝[(UTC HH:MM:SS, 值)]；avg／max／min 同值。"""
    return {"ip": ip, "id": 1, "name": "n", "reports": {"disk-utilization": rows_of(
        "disk", [(f"{day}T{t}Z", v, v, v) for t, v in pts], diskId=1, disk=disk)}}


def _pts(*pairs, day="2026-11-01"):
    from datetime import datetime, timezone
    return [(datetime.fromisoformat(f"{day}T{t}").replace(tzinfo=timezone.utc), float(v)) for t, v in pairs]


@pytest.mark.parametrize("pairs,hit", [
    ((("00:00:00", 71), ("02:00:00", 85)), False),           # 只升 14 點
    ((("00:00:00", 70), ("02:00:00", 85)), True),            # 升 15 點、終點 85
    ((("00:00:00", 69), ("02:00:00", 84)), False),           # 升 15 點但終點 84
    ((("00:00:00", 70), ("05:00:00", 85)), True),            # 剛好 5 小時
    ((("00:00:00", 70), ("05:01:00", 85)), False),           # 5 小時 01 分
    ((("00:00:00", 70), ("03:00:00", 95), ("04:00:00", 85)), True),
    ((("00:00:00", 85), ("02:00:00", 99)), False),           # 本來就高，只升 14
    ((("00:00:00", 90), ("01:00:00", 70), ("03:00:00", 86)), True),   # 先降再升：從低點算
])
def test_暴增判斷邊界(pairs, hit):
    r = da.detect_surge(_pts(*pairs), 5, 15, 85)
    assert (r is not None) is hit, r


def test_暴增取最大那段_回起訖點():
    r = da.detect_surge(_pts(("00:00:00", 60), ("01:00:00", 70), ("02:00:00", 86), ("03:00:00", 90)), 5, 15, 85)
    assert (r["from_v"], r["to_v"], r["rise"]) == (60, 90, 30) and r["from_t"].hour == 0 and r["to_t"].hour == 3


def test_暴增卡片_台北時間_從幾到幾_去重同日一次(env):
    c, drop, _ = env
    alert_on(c, thr=95, warn=94)                                             # 讓門檻告警不干擾
    batch(c, drop, "b1", [_surge_dev("192.0.2.10", [("00:00:00", 70), ("02:30:00", 88)]),
                          _surge_dev("192.0.2.11", [("00:00:00", 71), ("02:30:00", 85)])])    # .11 只升 14
    h = hist(c)
    assert len(h) == 1 and h[0]["tier"] == "surge" and h[0]["hosts"] == 1
    assert h[0]["summary"] == "192.0.2.10｜C:｜70%→88%｜11-01 08:00→10:30"           # UTC 00:00 = 台北 08:00
    d = deliveries(c)
    assert len(d) == 1 and d[0]["severity"] == "warning" and d[0]["title"] == "磁碟暴增：1 台"
    batch(c, drop, "b2", [_surge_dev("192.0.2.10", [("03:00:00", 70), ("04:00:00", 90)])])   # 同台同碟同天：不重發
    assert len(hist(c)) == 1
    batch(c, drop, "b3", [_surge_dev("192.0.2.10", [("00:00:00", 70), ("02:00:00", 88)], day="2026-11-02")])
    assert len(hist(c)) == 2


def test_暴增終點達緊急門檻就是critical(env):
    c, drop, _ = env
    alert_on(c, thr=90)
    batch(c, drop, "b1", [_surge_dev("192.0.2.10", [("00:00:00", 70), ("02:00:00", 92)])])
    d = {x["title"]: x for x in deliveries(c)}
    assert d["磁碟暴增：1 台"]["severity"] == "critical"
    assert [x["severity"] for x in deliveries(c)] == ["critical", "critical"]           # 緊急那則在前、暴增在後
    assert deliveries(c)[0]["title"].endswith("（緊急）：1 台")


def test_暴增跨UTC午夜_接前一天的點(env):
    c, drop, _ = env
    alert_on(c, thr=95, warn=94)
    batch(c, drop, "b1", [_surge_dev("192.0.2.10", [("22:00:00", 70)], day="2026-10-31")])
    assert hist(c) == []
    batch(c, drop, "b2", [_surge_dev("192.0.2.10", [("01:00:00", 88)], day="2026-11-01")])
    h = hist(c)
    assert len(h) == 1 and h[0]["summary"] == "192.0.2.10｜C:｜70%→88%｜11-01 06:00→09:00"


def test_暴增用max序列_沒有max才用avg(env):
    c, drop, _ = env
    alert_on(c, thr=95, warn=94)
    pts = [("2026-11-01T00:00:00Z", 70, 70, 70), ("2026-11-01T01:00:00Z", 75, 90, 70)]      # avg 只升 5、max 升 20
    batch(c, drop, "b1", [{"ip": "192.0.2.10", "id": 1, "name": "n", "reports": {"disk-utilization": rows_of("disk", pts, diskId=1, disk="C:")}}])
    assert [x["tier"] for x in hist(c)] == ["surge"]


def test_範例卡片分段列出兩級與暴增(env):
    c, drop, _ = env
    alert_on(c, teams=False)
    batch(c, drop, "b1", [dev("192.0.2.10", 95), dev("192.0.2.11", 85), dev("192.0.2.12", 40),
                          _surge_dev("192.0.2.13", [("00:00:00", 60), ("01:00:00", 86)])])
    card, title, summary, hosts = da.sample_card(c)
    assert title.startswith("【測試】") and hosts == 4
    heads = [l for l in summary.split("\n") if l.startswith("■")]
    assert heads == ["■ 緊急（≥90%）", "■ 準備（≥80%）", "■ 未達門檻（只為了看格式）", "■ 暴增"]
    txt = json.dumps(card, ensure_ascii=False)
    assert "緊急（≥90%）" in txt and "60%→86%" in txt
