"""2.059.17 EOS 指定收件人規則（主機＞業務系統＞設備類別＞推算）。

守的事：
1. 優先序對；同一級多條＝聯集
2. 指定的人寄的當下停用／不在 AD → 進沒人收，**不改寄部門主管**
3. 存規則時收件人要在 AD 唯一、在職、有信箱（存員編，不存名字）
4. （種類、對象、軌別）唯一：再設一次＝取代
5. 規則壞掉要報錯，不可以靜默當沒規則（那會把信寄回原本的人）
6. 通知、追蹤吃同一份（規則套在 eos_candidates）
"""
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import eos_assign as ea  # noqa: E402
import eos_notify as en  # noqa: E402
ROLE_AWARE_TESTS = True     # 要測「一般帳號不能改設備類別規則」，不吃測試環境預設建成 admin

from test_eos_notify import TODAY, conn, fake_eos_tables, _person, _hw, _sw  # noqa: E402,F401


def _biz(c, api_id, name, ap=None):
    c.execute("INSERT INTO business_system (api_id, name, ap_owner) VALUES (?,?,?)", (api_id, name, ap))


def _setapi(c, serial, api_id):
    c.execute("UPDATE hardware SET api_id = ? WHERE asset_serial = ?", (api_id, serial))


def _base(c):
    _person(c, "E1", "張根榮", "head@example.invalid")
    _person(c, "E2", "王網路", "net1@example.invalid")
    _person(c, "E3", "李網路", "net2@example.invalid")
    _person(c, "E4", "陳威廷", "chen@example.invalid")
    _person(c, "E5", "林AP", "ap@example.invalid")
    # 網路設備（os_class 用 OS 字串判）＋硬體型號有 EOS；擁有者寫張根榮
    _hw(c, "S1", "rt01", "10.99.0.1", os_="Cisco IOS", model="Dell PowerEdge R760", owner="張根榮")
    _hw(c, "S2", "srv01", "10.99.0.2", os_="Rocky Linux 9", model="Dell PowerEdge R760", owner="張根榮")
    _sw(c, "S2", "林AP")
    c.commit()


def _to(pv, track, host):
    return sorted(g["name"] for g in pv["groups"] for i in g["items"] if i["track"] == track and i["hostname"] == host)


def test_沒有規則照原本推算(conn):
    _base(conn)
    pv = en.preview(conn, TODAY)
    assert _to(pv, "hw", "rt01") == ["張根榮"]


def test_設備類別規則_窗口多人都收(conn):
    _base(conn)
    cls = en.eos_candidates(conn, TODAY)[0]
    rt_cls = next(c["devclass"] for c in cls if c["hostname"] == "rt01" and c["track"] == "hw")
    ea.upsert(conn, "devclass", rt_cls, "hw", ["王網路", "李網路"], by="admin")
    pv = en.preview(conn, TODAY)
    assert _to(pv, "hw", "rt01") == ["李網路", "王網路"]
    it = next(i for g in pv["groups"] for i in g["items"] if i["hostname"] == "rt01" and i["track"] == "hw")
    assert "設備類別規則" in (it["assign"] or "")
    # 作業系統軌不受硬體規則影響
    assert all(i["track"] == "hw" for g in pv["groups"] if g["name"] == "王網路" for i in g["items"])


def test_主機規則勝過設備類別(conn):
    _base(conn)
    mk = next(c["mk"] for c in en.eos_candidates(conn, TODAY)[0] if c["hostname"] == "srv01")
    cls = next(c["devclass"] for c in en.eos_candidates(conn, TODAY)[0] if c["hostname"] == "srv01" and c["track"] == "hw")
    ea.upsert(conn, "devclass", cls, "hw", ["王網路"])
    ea.upsert(conn, "host", mk, "hw", ["陳威廷"], label="srv01")
    pv = en.preview(conn, TODAY)
    assert _to(pv, "hw", "srv01") == ["陳威廷"]


def test_業務系統規則_一台多系統聯集(conn):
    _base(conn)
    _biz(conn, "A01", "大洲系統")
    _biz(conn, "A02", "小洲系統")
    _setapi(conn, "S2", "A01")
    # 同一台（同主機名＝machine_key 同一台）另一筆登記掛另一個系統
    _hw(conn, "S2b", "srv01", "10.99.0.2", os_="Rocky Linux 9", model="Dell PowerEdge R760", owner="張根榮")
    _setapi(conn, "S2b", "A02")
    conn.commit()
    ea.upsert(conn, "system", "A01", "os", ["陳威廷"])
    ea.upsert(conn, "system", "A02", "os", ["王網路"])
    pv = en.preview(conn, TODAY)
    assert _to(pv, "os", "srv01") == ["王網路", "陳威廷"]


def test_指定的人停用_進沒人收不改寄部主管(conn):
    _base(conn)
    mk = next(c["mk"] for c in en.eos_candidates(conn, TODAY)[0] if c["hostname"] == "rt01")
    ea.upsert(conn, "host", mk, "hw", ["陳威廷"])
    conn.execute("UPDATE ad_person SET enabled = 0 WHERE employee_id = 'E4'")
    conn.commit()
    pv = en.preview(conn, TODAY)
    assert _to(pv, "hw", "rt01") == []
    o = next(o for o in pv["orphans"] if o["hostname"] == "rt01" and o["track"] == "hw")
    assert "停用" in o["why"] and not o["fallback_dept"]


def test_存規則時收件人查不到要擋(conn):
    _base(conn)
    with pytest.raises(ValueError, match="不能用"):
        ea.upsert(conn, "devclass", "網路設備", "hw", ["不存在的人"])
    assert ea.load_rules(conn) == []


def test_存員編不存名字_同一對象同軌再設一次是取代(conn):
    _base(conn)
    a = ea.upsert(conn, "system", "A01", "os", ["陳威廷"])
    b = ea.upsert(conn, "system", "A01", "os", ["王網路"])
    rules = ea.load_rules(conn)
    assert len(rules) == 1 and a["id"] == b["id"]
    assert rules[0]["people"] == [{"employee_id": "E2", "name": "王網路"}]
    ea.upsert(conn, "system", "A01", "hw", ["李網路"])
    assert len(ea.load_rules(conn)) == 2


def test_規則壞掉要報錯不可靜默(conn):
    _base(conn)
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (ea.SETTING_KEY, "{壞掉"))
    conn.commit()
    with pytest.raises(ea.RulesCorrupt):
        en.eos_candidates(conn, TODAY)


def test_刪掉規則就回到推算(conn):
    _base(conn)
    mk = next(c["mk"] for c in en.eos_candidates(conn, TODAY)[0] if c["hostname"] == "rt01")
    r = ea.upsert(conn, "host", mk, "hw", ["陳威廷"])
    assert ea.delete(conn, r["id"])["id"] == r["id"]
    assert _to(en.preview(conn, TODAY), "hw", "rt01") == ["張根榮"]


def test_設備類別不在清單要擋(conn):
    with pytest.raises(ValueError, match="不在清單"):
        ea.upsert(conn, "devclass", "外星設備", "hw", ["x"])


def test_追蹤跟通知同一份收件人(conn):
    import eos_follow as ef
    _base(conn)
    mk = next(c["mk"] for c in en.eos_candidates(conn, TODAY)[0] if c["hostname"] == "rt01")
    ea.upsert(conn, "host", mk, "hw", ["陳威廷"])
    rows = ef.overview(conn, TODAY)["rows"] if hasattr(ef, "overview") else None
    if rows is None:
        pytest.skip("eos_follow 沒有 overview()")
    r = [x for x in rows if x["hostname"] == "rt01" and x["track"] == "hw"]
    assert [x["owner_ref"] for x in r] == ["陳威廷"]


def test_依業務系統_口徑跟預覽同一份(conn):
    _base(conn)
    _biz(conn, "A01", "大洲系統", "林AP")
    _setapi(conn, "S2", "A01")
    conn.commit()
    pv = en.preview(conn, TODAY)
    bs = en.by_system(conn, TODAY)
    uniq = {(i["track"], i["mk"], i["item"]) for g in pv["groups"] for i in g["items"]} | \
           {(o["track"], o["mk"], o["item"]) for o in pv["orphans"]}
    assert bs["total_items"] == len(uniq)
    dz = next(r for r in bs["rows"] if r["api_id"] == "A01")
    assert dz["name"] == "大洲系統" and dz["hosts"] == 1
    assert dz["count"] == len(dz["items"]) == dz["hw"] + dz["os"]
    none = next(r for r in bs["rows"] if r["api_id"] == "")
    assert none["name"] == en.NO_SYSTEM and none["hosts"] == 1          # rt01 沒填 APID
    # 每列筆數 = 下鑽筆數；沒有重複掛系統時各列加總 = 總數
    assert sum(r["count"] for r in bs["rows"]) == bs["total_items"]


def test_API_權限_轉派一般可以_設備類別規則限管理員(tmp_path):
    import ad_import
    import db
    from test_eos_notify import _client, _login
    api, client, path = _client(tmp_path)
    c = db.get_connection(path)
    c.execute(ad_import.TABLE_SQL)
    _person(c, "E4", "陳威廷", "chen@example.invalid")
    c.commit()
    c.close()
    try:
        assert client.get("/api/eos-notify/assign").status_code == 401
        _login(client, "bob")
        assert client.get("/api/eos-notify/assign").status_code == 200
        r = client.put("/api/eos-notify/assign", json={"kind": "devclass", "key": "網路設備", "track": "hw", "names": ["陳威廷"]})
        assert r.status_code == 403, r.text
        r = client.post("/api/eos-notify/transfer", json={"targets": [{"mk": "h:x", "track": "hw", "hostname": "x"}], "names": ["陳威廷"]})
        assert r.status_code == 200 and r.json()["count"] == 1
        bad = client.post("/api/eos-notify/transfer", json={"targets": [{"mk": "h:y", "track": "hw"}], "names": ["沒這人"]})
        assert bad.status_code == 400
        rules = client.get("/api/eos-notify/assign").json()["rules"]
        assert len(rules) == 1 and rules[0]["names_text"] == "陳威廷"
        assert client.delete(f"/api/eos-notify/assign/{rules[0]['id']}").status_code == 200
        _login(client, "admin")
        r = client.put("/api/eos-notify/assign", json={"kind": "devclass", "key": "網路設備", "track": "hw", "names": ["陳威廷"]})
        assert r.status_code == 200
    finally:
        api.app.dependency_overrides.clear()


def test_verify_numbers_依業務系統不變式(conn):
    import verify_numbers as vnum
    _base(conn)
    _biz(conn, "A01", "大洲系統", "林AP")
    _setapi(conn, "S2", "A01")
    conn.commit()
    before = vnum.FAILS
    vnum.check_eos_by_system(conn, TODAY)
    assert vnum.FAILS == before


def test_追蹤頁_指定的人停用_不改寄部門主管(conn):
    """審查 P2：追蹤頁／核數也要用員編重查，跟通知同一個答案（停用＝沒有收件信箱，不是部門主管）。"""
    import eos_follow as ef
    _base(conn)
    mk = next(c["mk"] for c in en.eos_candidates(conn, TODAY)[0] if c["hostname"] == "rt01")
    ea.upsert(conn, "host", mk, "hw", ["陳威廷"])
    conn.execute("UPDATE ad_person SET enabled = 0 WHERE employee_id = 'E4'")
    conn.commit()
    r = next(x for x in ef.overview(conn, TODAY)["rows"] if x["hostname"] == "rt01" and x["track"] == "hw")
    assert r["recipient"] is None


def test_規則壞掉_預覽回原因不是500(conn):
    _base(conn)
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (ea.SETTING_KEY, "{壞掉"))
    conn.commit()
    pv = en.preview(conn, TODAY)
    assert pv["ok"] is False and "規則" in pv["reason"]
    assert en.by_system(conn, TODAY)["ok"] is False
