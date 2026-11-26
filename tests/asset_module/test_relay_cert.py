"""憑證到期探測（2.042.02）：中繼匯入的 cert 來源、探測結果對到憑證項目、未匹配、探測與人工比對、探測清單匯出、無機密。

不連任何網路：批次在暫存資料夾現做（示範主機名 *.example.test、IP 192.0.2.x）。腳本只做靜態檢查（另見 test_devkit_content）。
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import certs  # noqa: E402
import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402
from test_relay_import import SAT, write_batch  # noqa: E402

NOW = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
#: 私鑰標頭在執行時才組出來（原始碼裡不放完整標頭，免得被密鑰掃描誤判）
PEM_HEAD = "-----" + "BEGIN {}" + "PRIVATE KEY" + "-----"
SHA = "63e9df512cc52c8539cd651248c3d12420319aa54c144f1b12c2af0473e03584"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)
    qw.set_clock(lambda: SAT(10))
    db.set_setting(c, ri.DIR_KEY, str(drop))
    db.set_setting(c, ri.ENABLED_KEY, "1")
    yield c, drop
    qw.set_clock(None)
    c.close()


def item(c, sni="web.example.test", endpoint="web.example.test", port=443, env_="正式", expiry=None, active=True):
    iid = certs.create_item(c, {"name": sni or endpoint, "environment": env_, "endpoint": endpoint, "port": port, "sni": sni,
                                "mount_type": "F5"}, "u", True)["id"]
    if expiry:
        certs.add_observation(c, iid, {"not_after": expiry}, "u")
    if not active:
        certs.set_active(c, iid, False, "u", reason_code="retired")
    return iid


def probe(endpoint="web.example.test", port=443, sni="web.example.test", ok=True, not_after="2027-01-01T00:00:00Z", **kw):
    d = {"endpoint": endpoint, "port": port, "sni": sni, "ok": ok, "error": "" if ok else "connect timeout",
         "subject": "CN=" + (sni or endpoint), "issuer": "CN=Example CA", "serial": "1234ABCD", "sha256": SHA if ok else "",
         "not_before_utc": "2026-01-01T00:00:00Z" if ok else "", "not_after_utc": not_after if ok else "", "san": [sni] if sni else []}
    d.update(kw)
    return d


def batch(drop, name, files, collected="2026-11-02T21:05:00+08:00", **kw):
    return write_batch(drop, name, files, source="cert", collected=collected, **kw)


def run(c):
    return ri.run_scan(c, "manual", "admin")


def row(c, iid):
    return next(r for r in certs.all_rows(c, NOW) if r["id"] == iid)


def last(c, name):
    return c.execute("SELECT status, reason_code, reason, devices, points, unmatched, bad_values FROM relay_import_batches WHERE batch_id=?",
                     (name,)).fetchone()


# ---------------------------------------------------------------- 匹配與寫入
def test_對到項目_寫探測觀測與探測狀態_不改人工值(env):
    c, drop = env
    iid = item(c, expiry="2027-01-01")
    before = row(c, iid)
    rev = c.execute("SELECT rev FROM cert_item WHERE id=?", (iid,)).fetchone()[0]
    batch(drop, "cert_b1", {"ep0001.json": probe(not_after="2026-12-01T00:00:00Z")})
    out = run(c)
    assert out["imported"] == 1, out
    r = row(c, iid)
    assert r["not_after_utc"] == before["not_after_utc"] and r["status"] == before["status"]       # 人工到期日不變
    assert r["probe_at_utc"] == "2026-11-02T13:05:00Z" and r["probe_ok"] is True
    assert r["probe_not_after_utc"] == "2026-12-01T00:00:00Z" and r["probe_diff"] == "differs" and r["probe_differs"]
    o = c.execute("SELECT issuer_dn, serial, fingerprint_sha256, san_json, first_seen_utc, batch_id FROM cert_probe_observation WHERE item_id=?",
                  (iid,)).fetchone()
    assert o["issuer_dn"] == "CN=Example CA" and o["serial"] == "1234ABCD" and o["fingerprint_sha256"] == SHA and o["batch_id"] == "cert_b1"
    assert c.execute("SELECT count(*) FROM cert_observation WHERE item_id=?", (iid,)).fetchone()[0] == 1          # 人工觀測表只有人工那一筆
    assert json.loads(o["san_json"]) == ["web.example.test"]
    assert c.execute("SELECT rev FROM cert_item WHERE id=?", (iid,)).fetchone()[0] == rev          # 探測不動 cert_item（批次復原不誤判衝突）
    b = last(c, "cert_b1")
    assert b["status"] == "imported" and b["devices"] == 1 and b["points"] == 1 and b["unmatched"] == 0


def test_探測與人工同日_一致_僅探測_探測失敗_未探測(env):
    c, drop = env
    same = item(c, sni="a.example.test", endpoint="a.example.test", expiry="2027-01-01")       # 人工填日期（UTC+8 當天 23:59:59）
    only = item(c, sni="b.example.test", endpoint="b.example.test")
    fail = item(c, sni="c.example.test", endpoint="c.example.test", expiry="2027-01-01")
    none = item(c, sni="d.example.test", endpoint="d.example.test", expiry="2027-01-01")
    batch(drop, "cert_b2", {
        "ep0001.json": probe("a.example.test", sni="a.example.test", not_after="2027-01-01T03:00:00Z"),
        "ep0002.json": probe("b.example.test", sni="b.example.test"),
        "ep0003.json": probe("c.example.test", sni="c.example.test", ok=False),
    })
    assert run(c)["imported"] == 1
    assert row(c, same)["probe_diff"] == "same" and not row(c, same)["probe_differs"]
    assert row(c, only)["probe_diff"] == "probe_only" and row(c, only)["status"] == "none"          # 沒人工值：仍是「無到期資料」，不拿探測值補
    f = row(c, fail)
    assert f["probe_diff"] == "failed" and f["probe_ok"] is False and f["probe_error"] == "connect timeout"
    assert c.execute("SELECT count(*) FROM cert_probe_observation WHERE item_id=?", (fail,)).fetchone()[0] == 0
    assert row(c, none)["probe_diff"] == "" and row(c, none)["probe_diff_label"] == "未探測"
    assert last(c, "cert_b2")["bad_values"] == 1


def test_到期卡片只看人工_探測不影響卡片與狀態(env):
    c, drop = env
    iid = item(c, expiry="2027-06-01")
    s0 = certs.summary(c, NOW)
    batch(drop, "cert_b3", {"ep0001.json": probe(not_after="2026-11-05T00:00:00Z")})               # 探測說 2 天後到期
    run(c)
    assert certs.summary(c, NOW)["cards"] == s0["cards"]
    assert row(c, iid)["status"] == "ok" and row(c, iid)["probe_differs"]
    assert c.execute("SELECT count(*) FROM cert_observation WHERE item_id=?", (iid,)).fetchone()[0] == 1          # 探測不進人工觀測表


def test_對不到_或對到多筆_或已停用_都列未匹配_不自動建項目(env):
    c, drop = env
    item(c, sni="dup.example.test", endpoint="dup.example.test", env_="正式")
    item(c, sni="dup.example.test", endpoint="dup.example.test", env_="測試")                     # 同端點同 SNI 兩個環境 → 多筆
    item(c, sni="old.example.test", endpoint="old.example.test", active=False)
    n_items = c.execute("SELECT count(*) FROM cert_item").fetchone()[0]
    batch(drop, "cert_b4", {
        "ep0001.json": probe("nobody.example.test", sni="nobody.example.test"),
        "ep0002.json": probe("dup.example.test", sni="dup.example.test"),
        "ep0003.json": probe("old.example.test", sni="old.example.test"),
        "ep0004.json": probe("192.0.2.9", port=8443, sni=""),
    })
    assert run(c)["imported"] == 1
    um = {r["ident"]: r for r in ri.list_unmatched(c, "cert")}
    assert set(um) == {"nobody.example.test:443,nobody.example.test", "dup.example.test:443,dup.example.test",
                       "old.example.test:443,old.example.test", "192.0.2.9:8443"}
    assert um["dup.example.test:443,dup.example.test"]["reason"] == "ambiguous" and um["dup.example.test:443,dup.example.test"]["candidates"] == 2
    assert um["nobody.example.test:443,nobody.example.test"]["reason"] == "no_item"
    assert c.execute("SELECT count(*) FROM cert_item").fetchone()[0] == n_items
    assert c.execute("SELECT count(*) FROM cert_probe_status").fetchone()[0] == 0
    assert last(c, "cert_b4")["unmatched"] == 4
    # 之後建了項目 → 下一批對得到就從未匹配移除
    item(c, sni="nobody.example.test", endpoint="nobody.example.test")
    batch(drop, "cert_b5", {"ep0001.json": probe("nobody.example.test", sni="nobody.example.test")}, collected="2026-11-02T22:00:00+08:00")
    run(c)
    assert "nobody.example.test:443,nobody.example.test" not in {r["ident"] for r in ri.list_unmatched(c, "cert")}


def test_比對不分大小寫_同一張憑證不重複_較舊批次不蓋較新探測狀態(env):
    c, drop = env
    iid = item(c, expiry="2027-01-01")
    batch(drop, "cert_new", {"ep0001.json": probe("WEB.Example.TEST", sni="Web.Example.Test")}, collected="2026-11-02T22:00:00+08:00")
    run(c)
    batch(drop, "cert_old", {"ep0001.json": probe(ok=False)}, collected="2026-11-01T22:00:00+08:00")
    run(c)
    assert c.execute("SELECT count(*) FROM cert_probe_observation WHERE item_id=?", (iid,)).fetchone()[0] == 1
    r = row(c, iid)
    assert r["probe_ok"] is True and r["probe_at_utc"] == "2026-11-02T14:00:00Z"
    batch(drop, "cert_again", {"ep0001.json": probe()}, collected="2026-11-02T23:00:00+08:00")
    run(c)
    assert c.execute("SELECT count(*) FROM cert_probe_observation WHERE item_id=?", (iid,)).fetchone()[0] == 1   # 同一張不重複
    assert last(c, "cert_again")["points"] == 0 and row(c, iid)["probe_at_utc"] == "2026-11-02T15:00:00Z"


# ---------------------------------------------------------------- 驗證（整批拒收、一筆都不寫）
@pytest.mark.parametrize("bad,code", [
    ({"port": 0}, "bad_data"), ({"port": "443"}, "bad_data"), ({"endpoint": "bad host/x"}, "bad_data"), ({"endpoint": None}, "bad_data"),
    ({"ok": "yes"}, "bad_data"), ({"sha256": "abc"}, "bad_data"), ({"not_after_utc": "not-a-date"}, "bad_data"),
    ({"not_after_utc": ""}, "bad_data"), ({"serial": "XYZ!"}, "bad_data"), ({"san": "web.example.test"}, "bad_data"),
    ({"subject": "x" * 501}, "bad_data"), ({"sni": "bad sni"}, "bad_data"),
    ({"private_key": "x"}, "secret_field"), ({"pem": PEM_HEAD.format("RSA ") + "\nAAAA"}, "secret_field"),
    ({"subject": PEM_HEAD.format("") + " AAAA"}, "secret_field"), ({"password": "p"}, "secret_field"), ({"token": "t"}, "secret_field"),
])
def test_壞資料或機密欄位_整批拒收_不寫入(env, bad, code):
    c, drop = env
    iid = item(c, expiry="2027-01-01")
    bad_file = {**probe("other.example.test", sni="other.example.test"), **bad}
    batch(drop, "cert_bad", {"ep0001.json": probe(), "ep0002.json": bad_file})
    out = run(c)
    assert out["rejected"] == 1, out
    b = last(c, "cert_bad")
    assert b["status"] == "rejected" and b["reason_code"] == code, dict(b)
    assert c.execute("SELECT count(*) FROM cert_probe_observation").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM cert_probe_status").fetchone()[0] == 0
    assert row(c, iid)["probe_diff"] == ""


def test_失敗探測要有原因_同批重複端點拒收(env):
    c, drop = env
    item(c)
    batch(drop, "cert_noerr", {"ep0001.json": probe(ok=False, error="")})
    batch(drop, "cert_dup", {"ep0001.json": probe(), "ep0002.json": probe("WEB.example.test")})
    run(c)
    assert last(c, "cert_noerr")["status"] == "rejected" and "error" in last(c, "cert_noerr")["reason"]
    assert last(c, "cert_dup")["status"] == "rejected" and "同一個端點" in last(c, "cert_dup")["reason"]


def test_驗證失敗但讀到憑證_仍是ok_原因保留(env):
    c, drop = env
    iid = item(c, expiry="2027-01-01")
    batch(drop, "cert_selfsigned", {"ep0001.json": probe(error="validation: RemoteCertificateChainErrors", not_after="2027-01-01T05:00:00Z")})
    run(c)
    r = row(c, iid)
    assert r["probe_ok"] is True and r["probe_error"].startswith("validation:") and r["probe_diff"] == "same"


def test_不存未知欄位_快照不含憑證本體(env):
    c, drop = env
    iid = item(c)
    batch(drop, "cert_extra", {"ep0001.json": probe(raw_der="MIIBszCCAVmgAwIBAgIUQ", chain=["x"])})
    assert run(c)["imported"] == 1
    dump = json.dumps([dict(r) for r in c.execute("SELECT * FROM cert_probe_observation WHERE item_id=?", (iid,))])
    assert "MIIBszCC" not in dump and "chain" not in dump


# ---------------------------------------------------------------- 探測清單匯出
def test_探測清單_只含啟用中_合法端點_去重_IPv6加中括號(env):
    c, _ = env
    item(c, sni="a.example.test", endpoint="a.example.test")
    item(c, sni="", endpoint="192.0.2.7", port=8443)
    item(c, sni="v6.example.test", endpoint="2001:db8::5")
    item(c, sni="old.example.test", endpoint="old.example.test", active=False)
    bad = item(c, sni="", endpoint="待補")
    out = certs.probe_list(c)
    lines = out["text"].splitlines()
    assert lines == ["a.example.test:443,a.example.test", "192.0.2.7:8443", "[2001:db8::5]:443,v6.example.test"]
    seq = c.execute("SELECT seq FROM cert_item WHERE id=?", (bad,)).fetchone()[0]
    assert out["skipped"] == [f"C{seq}"] and out["lines"] == 3
    assert all(ord(ch) < 128 for ch in out["text"])


def test_探測清單端點_只有管理員_寫稽核_純文字(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import api
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "a.db"))
    db.init_db()
    c = db.get_connection()
    item(c, sni="a.example.test", endpoint="a.example.test")
    c.close()
    who = {"v": {"username": "admin", "role": "admin"}}

    def _db():
        x = db.get_connection()
        try:
            yield x
        finally:
            x.close()
    api.app.dependency_overrides[api.get_db] = _db
    api.app.dependency_overrides[api.require_auth] = lambda: who["v"]
    try:
        cl = TestClient(api.app, raise_server_exceptions=False)
        r = cl.get("/api/certs/probe-list.txt")
        assert r.status_code == 200, r.text
        assert r.text == "a.example.test:443,a.example.test\n" and r.headers["x-probe-lines"] == "1"
        assert "attachment" in r.headers["content-disposition"] and r.headers["content-type"].startswith("text/plain")
        for role in ("user", "viewer"):
            who["v"] = {"username": "u", "role": role}
            assert cl.get("/api/certs/probe-list.txt").status_code == 403, role
    finally:
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)
    c = db.get_connection()
    assert c.execute("SELECT count(*) FROM activity_log WHERE action='cert_probe_list'").fetchone()[0] == 1
    c.close()


# ---------------------------------------------------------------- 腳本靜態守門（盤中拒絕的提示文字）
def test_腳本盤中拒絕的提示文字_與WUG腳本同一句():
    if not (ROOT / "AI" / "tools_relay" / "cert_collect.ps1").exists():
        pytest.skip("relay 產出物沒有 AI/")
    sc = (ROOT / "AI" / "tools_relay" / "cert_collect.ps1").read_text(encoding="utf-8")
    msg = "Stopped: this is weekday trading hours (08:30-15:00). Run it after 15:00, or add -AllowMarketHours if a person really needs it now."
    assert msg in sc
    assert msg in (ROOT / "AI" / "tools_relay" / "wug_collect.ps1").read_text(encoding="utf-8")
    assert sc.index(msg) < sc.index("function Probe")                                                # 先判斷盤中，才做任何連線
    assert "source = 'cert'" in sc and ri.CertAdapter.name == "cert" and ri.ADAPTERS["cert"].implemented


def test_鮮度燈有憑證來源(env):
    c, drop = env
    item(c)
    v = {s["source"]: s for s in ri.sources_view(c)}
    assert v["cert"]["status"] == "never" and v["cert"]["label"] == "憑證到期探測"
    batch(drop, "cert_b9", {"ep0001.json": probe()})
    run(c)
    assert {s["source"]: s for s in ri.sources_view(c, SAT(10))}["cert"]["last_batch"] == "cert_b9"


# ---------------------------------------------------------------- 審查補強（2.042.03）
def test_先被探測到_之後人工登錄同一張憑證_不會撞唯一鍵(env):
    """同一組 簽發者＋序號＋指紋＋到期時間：探測先看到，人工之後照樣登錄得進去（探測存在另一張表）。"""
    c, drop = env
    iid = item(c)
    batch(drop, "cert_first", {"ep0001.json": probe(not_after="2027-01-01T00:00:00Z")})
    assert run(c)["imported"] == 1
    out = certs.add_observation(c, iid, {"not_after": "2027-01-01T00:00:00Z", "issuer_dn": "CN=Example CA", "serial": "1234ABCD",
                                          "fingerprint_sha256": SHA}, "u")
    assert out["not_after_utc"] == "2027-01-01T00:00:00Z"
    r = row(c, iid)
    assert r["not_after_utc"] == "2027-01-01T00:00:00Z" and r["probe_diff"] == "same"
    # 探測不擋調查表匯入的「第一筆觀測」：匯入只看人工觀測表
    iid2 = item(c, sni="imp.example.test", endpoint="imp.example.test")
    batch(drop, "cert_second", {"ep0001.json": probe("imp.example.test", sni="imp.example.test")}, collected="2026-11-02T22:00:00+08:00")
    run(c)
    res = {"observations": 0}
    certs._import_obs(c, iid2, {"expiry_utc": "2027-01-01T00:00:00Z"}, "u", res)
    assert res["observations"] == 1
    # 詳情：探測紀錄另列，觀測歷史不混入
    d = certs.item_detail(c, iid, True)
    assert len(d["observations"]) == 1 and d["observations"][0]["source"] == "manual"
    assert len(d["probe_observations"]) == 1 and d["probe_observations"][0]["fingerprint_sha256"] == SHA


def test_同一張再被探測到_只更新最後看到時間(env):
    c, drop = env
    iid = item(c)
    batch(drop, "cert_s1", {"ep0001.json": probe()}, collected="2026-11-02T20:00:00+08:00")
    run(c)
    batch(drop, "cert_s2", {"ep0001.json": probe()}, collected="2026-11-02T22:00:00+08:00")
    run(c)
    o = c.execute("SELECT first_seen_utc, last_seen_utc, batch_id FROM cert_probe_observation WHERE item_id=?", (iid,)).fetchall()
    assert len(o) == 1 and o[0]["first_seen_utc"] == "2026-11-02T12:00:00Z" and o[0]["last_seen_utc"] == "2026-11-02T14:00:00Z"
    assert o[0]["batch_id"] == "cert_s2"


def _future(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_收集時間比現在快超過10分鐘_整批拒收_不會變成最新而擋住之後的批次(env):
    c, drop = env
    iid = item(c, expiry="2027-01-01")
    batch(drop, "cert_future", {"ep0001.json": probe(not_after="2026-12-01T00:00:00Z")}, collected=_future(60))
    run(c)
    b = last(c, "cert_future")
    assert b["status"] == "rejected" and b["reason_code"] == "manifest_invalid" and "10 分鐘" in b["reason"]
    assert row(c, iid)["probe_diff"] == ""
    # 共通規則（v2.041.96）只擋「超過 1 天」；憑證來源更嚴：2 小時後也擋
    batch(drop, "cert_future2", {"ep0001.json": probe()}, collected=_future(120))
    run(c)
    assert last(c, "cert_future2")["status"] == "rejected"
    # 時鐘只快幾分鐘可以；之後正常時間的批次也照常寫入
    batch(drop, "cert_skew", {"ep0001.json": probe()}, collected=_future(5))
    run(c)
    assert last(c, "cert_skew")["status"] == "imported"
    batch(drop, "cert_normal", {"ep0001.json": probe(ok=False)}, collected=_future(8))           # 比上一批晚、仍在容許範圍內
    run(c)
    assert last(c, "cert_normal")["status"] == "imported" and row(c, iid)["probe_ok"] is False


def test_共通規則仍擋超過一天的未來時間(env):
    c, drop = env
    item(c)
    batch(drop, "cert_far", {"ep0001.json": probe()}, collected=_future(60 * 48))
    run(c)
    b = last(c, "cert_far")
    assert b["status"] == "rejected" and b["reason_code"] == "manifest_invalid"


def test_腳本用了AllowMarketHours會先印警告():
    if not (ROOT / "AI" / "tools_relay" / "cert_collect.ps1").exists():
        pytest.skip("relay 產出物沒有 AI/")
    sc = (ROOT / "AI" / "tools_relay" / "cert_collect.ps1").read_text(encoding="utf-8")
    line = "if ($AllowMarketHours) { Write-Host 'WARNING: -AllowMarketHours was given."
    assert line in sc and sc.index(line) < sc.index("function Probe")
