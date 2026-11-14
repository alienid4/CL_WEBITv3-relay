"""已知例外（不提醒）：capacity_exempt 單一判斷＋各消費端（2026-11-14）。

範例 IP 只用 10.99.x、名稱都是假的。釘住：Datastore 例外不列處理但照算總量、到期回來＋1.4 列一次、
用量連續 2 個樣本上升才失效、剩餘少 10%／容量變小失效（變大不失效）、期限只能 30／90／180／365／EOS（最多 365 天）、
編輯寫稽核（舊→新）、只有管理員能設定、讀不到例外表＝照常提醒、磁碟名稱正規化。
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import capacity_exempt as ce  # noqa: E402
import db  # noqa: E402
import storage_capacity as sc  # noqa: E402

GIB = 1024
TODAY = date.today()


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _vol(conn, name, free_gb, cap_gb=10000, at=None):
    at = at or datetime.now().replace(microsecond=0).isoformat(sep=" ")
    rec = {"Name": name, "URL": f"ds:///vmfs/volumes/{name}/", "VI SDK Server": "10.99.0.1", "Type": "VMFS",
           "Capacity MiB": cap_gb * GIB, "In Use MiB": (cap_gb - free_gb) * GIB, "Provisioned MiB": cap_gb * GIB,
           "Free MiB": free_gb * GIB, "# VMs": 1, "# Hosts": 2, "Hosts": "esxi-zz"}
    conn.execute("INSERT INTO source_record (source, source_key, payload, resolved_status, collected_at) VALUES (?,?,?,'extra',?)",
                 ("vcenter_extra:vDatastore", f"{name}#{at}", json.dumps(rec), at))
    conn.commit()


def _set_vol(conn, name, free_gb, cap_gb=10000, at=None):
    conn.execute("DELETE FROM source_record WHERE source = 'vcenter_extra:vDatastore' AND payload LIKE ?", (f'%"Name": "{name}"%',))
    _vol(conn, name, free_gb, cap_gb, at)


def _cand(conn, kind, label_part):
    return next(c for c in ce.candidates(conn)["rows"] if c["kind"] == kind and label_part in c["label"])


def test_磁碟名稱正規化():
    assert ce.norm_mount("C:\\ Label:OS  Serial Number 1234") == "C:"
    assert ce.norm_mount("c:") == "C:"
    assert ce.norm_mount("/var 50GB") == "/var" and ce.norm_mount("/data/") == "/data" and ce.norm_mount("/") == "/"
    assert ce.norm_mount("") is None and ce.disk_key(None, "C:") is None and ce.disk_key("h:x", "C:\\") == "h:x::C:"


def test_期限_只有預設選項_最多365天_EOS封頂():
    assert ce.expires_for(90, TODAY) == ((TODAY + timedelta(days=90)).isoformat(), "90")
    for bad in (0, 7, 400, 3650, "forever", None):
        with pytest.raises(ValueError):
            ce.expires_for(bad, TODAY)
    far = (TODAY + timedelta(days=900)).isoformat()
    assert ce.expires_for("eos", TODAY, far) == ((TODAY + timedelta(days=365)).isoformat(), "eos")
    near = (TODAY + timedelta(days=40)).isoformat()
    assert ce.expires_for("eos", TODAY, near) == (near, "eos")
    with pytest.raises(ValueError):
        ce.expires_for("eos", TODAY, None)                                  # 沒有 EOS 日不能選
    with pytest.raises(ValueError):
        ce.expires_for("eos", TODAY, (TODAY - timedelta(days=1)).isoformat())


def test_Datastore例外_不列處理_照算總量_分類加總等於全部_稽核(tmp_path):
    conn = _conn(tmp_path)
    try:
        _vol(conn, "DS_FULL", 100)            # 用 99%、剩 100 GB → 處理
        _vol(conn, "DS_OK", 5000)
        assert {v["name"] for v in sc.alerts(sc.datastores(conn), {"rows": []})["volumes"]} == {"DS_FULL"}
        with pytest.raises(ValueError):
            ce.set_exempt(conn, _cand(conn, "ds", "DS_FULL"), "  ", 90, "admin")          # 原因必填
        eid = ce.set_exempt(conn, _cand(conn, "ds", "DS_FULL"), "已不使用、空間不會再長", 90, "admin")
        conn.commit()
        with pytest.raises(ValueError):
            ce.set_exempt(conn, _cand(conn, "ds", "DS_FULL"), "重複", 90, "admin")
        ds = sc.datastores(conn)
        v = {x["name"]: x for x in ds["volumes"]}
        assert v["DS_FULL"]["exempt_on"] and not v["DS_FULL"]["lun_low"] and v["DS_FULL"]["lun_why"]      # 例外：觸發條件照寫
        assert sc.alerts(ds, {"rows": []})["volumes"] == []
        assert ds["volume_count"] == 2 and ds["totals"]["capacity_mib"] == 20000 * GIB                   # 總量照算
        o = sc.overview(conn)
        assert o["vmdisk"]["exempt_count"] == 1 and not any(r["low"] for r in o["vmdisk"]["rows"])
        assert sum(r["near_full_count"] for r in o["rooms"]) == 0
        lg = [dict(r) for r in conn.execute("SELECT * FROM capacity_exempt_log")]
        assert [x["action"] for x in lg] == ["set"] and lg[0]["actor"] == "admin" and json.loads(lg[0]["detail"])["used_pct"] == 99.0
        row = conn.execute("SELECT * FROM capacity_exempt WHERE id = ?", (eid,)).fetchone()
        assert row["base_free_gb"] == 100 and row["base_cap_gb"] == 10000 and row["expires"] == (TODAY + timedelta(days=90)).isoformat()
    finally:
        conn.close()


def test_到期_回到處理_tick轉狀態_1點4列一次_知道了就消失(tmp_path):
    conn = _conn(tmp_path)
    try:
        _vol(conn, "DS_FULL", 100)
        eid = ce.set_exempt(conn, _cand(conn, "ds", "DS_FULL"), "封存碟", 30, "admin")
        conn.execute("UPDATE capacity_exempt SET expires = ? WHERE id = ?", (TODAY.isoformat(), eid))   # 今天起恢復
        conn.commit()
        ds = sc.datastores(conn)
        assert {v["name"] for v in sc.alerts(ds, {"rows": []})["volumes"]} == {"DS_FULL"}           # 不用等 tick 就恢復
        assert "已到期" in ds["volumes"][0]["exempt_text"]
        assert ce.tick(conn, force=True)["expired"] == 1
        assert conn.execute("SELECT status FROM capacity_exempt WHERE id = ?", (eid,)).fetchone()[0] == "expired"
        import v2_today
        rows, _ = v2_today._rows_capacity(conn)
        assert [r["id"] for r in rows if r["id"].startswith("capacity:exempt:")] == [f"capacity:exempt:{eid}"]
        ce.ack(conn, eid, "admin")
        conn.commit()
        rows, _ = v2_today._rows_capacity(conn)
        assert not [r for r in rows if r["id"].startswith("capacity:exempt:")]
        assert [r[0] for r in conn.execute("SELECT action FROM capacity_exempt_log ORDER BY id")] == ["set", "expire", "ack"]
    finally:
        conn.close()


def test_用量上升_連續2個樣本才失效(tmp_path):
    conn = _conn(tmp_path)
    try:
        _vol(conn, "DS_A", 5000, at="2026-11-10 09:00:00")       # 用 50%
        eid = ce.set_exempt(conn, _cand(conn, "ds", "DS_A"), "觀察中", 90, "admin")
        conn.commit()
        ce.tick(conn, force=True)
        _set_vol(conn, "DS_A", 4790, at="2026-11-11 09:00:00")   # 52.1%（≥ 50＋2）第 1 個樣本 → 仍是例外
        assert sc.datastores(conn)["volumes"][0]["exempt_on"]
        ce.tick(conn, force=True)
        assert sc.datastores(conn)["volumes"][0]["exempt_on"]     # 同一個樣本再看一次不算第 2 個
        _set_vol(conn, "DS_A", 4780, at="2026-11-12 09:00:00")   # 第 2 個樣本 → 失效
        v = sc.datastores(conn)["volumes"][0]
        assert not v["exempt_on"] and v["exempt_text"].startswith("例外失效：用量從 50% 升到 52.2%")
        assert ce.tick(conn, force=True)["void"] == 1
        assert conn.execute("SELECT status FROM capacity_exempt WHERE id = ?", (eid,)).fetchone()[0] == "void"
        assert "void" in [r[0] for r in conn.execute("SELECT action FROM capacity_exempt_log")]
    finally:
        conn.close()


def test_上升後又回來_計數歸零(tmp_path):
    conn = _conn(tmp_path)
    try:
        _vol(conn, "DS_A", 5000, at="2026-11-10 09:00:00")
        ce.set_exempt(conn, _cand(conn, "ds", "DS_A"), "觀察中", 90, "admin")
        conn.commit()
        _set_vol(conn, "DS_A", 4790, at="2026-11-11 09:00:00")
        ce.tick(conn, force=True)
        _set_vol(conn, "DS_A", 4990, at="2026-11-12 09:00:00")   # 回到 50.1%
        ce.tick(conn, force=True)
        _set_vol(conn, "DS_A", 4790, at="2026-11-13 09:00:00")   # 又上升：只算第 1 個
        assert sc.datastores(conn)["volumes"][0]["exempt_on"]
    finally:
        conn.close()


@pytest.mark.parametrize("free,cap,ok", [(1810, 10000, True), (1800, 10000, False), (2000, 9000, False), (2000, 12000, True)])
def test_剩餘少10趴或容量變小失效_容量變大不失效(tmp_path, free, cap, ok):
    conn = _conn(tmp_path)
    try:
        _vol(conn, "DS_A", 2000, at="2026-11-10 09:00:00")
        ce.set_exempt(conn, _cand(conn, "ds", "DS_A"), "觀察中", 90, "admin")
        conn.commit()
        _set_vol(conn, "DS_A", free, cap_gb=cap, at="2026-11-11 09:00:00")
        v = sc.datastores(conn)["volumes"][0]
        assert v["exempt_on"] is ok
    finally:
        conn.close()


def test_編輯_原因期限備註_稽核寫舊新_非生效中不能編輯(tmp_path):
    conn = _conn(tmp_path)
    try:
        _vol(conn, "DS_FULL", 100)
        eid = ce.set_exempt(conn, _cand(conn, "ds", "DS_FULL"), "原因一", 90, "admin")
        ch = ce.edit(conn, eid, "admin2", reason="原因二", duration=180, note="跟 AP 確認過")
        assert ch["reason"] == ["原因一", "原因二"] and ch["expires"][1] == (TODAY + timedelta(days=180)).isoformat()
        assert ce.edit(conn, eid, "admin2", reason="原因二") == {}                 # 沒變動不寫稽核
        with pytest.raises(ValueError):
            ce.edit(conn, eid, "admin2", duration=400)
        with pytest.raises(ValueError):
            ce.edit(conn, eid, "admin2", duration="eos")                          # Datastore 沒有 EOS 日
        lg = [dict(r) for r in conn.execute("SELECT action, actor, detail FROM capacity_exempt_log ORDER BY id")]
        assert [x["action"] for x in lg] == ["set", "edit"] and lg[1]["actor"] == "admin2"
        assert json.loads(lg[1]["detail"])["note"] == [None, "跟 AP 確認過"]
        ce.remove(conn, eid, "admin")
        with pytest.raises(ValueError):
            ce.edit(conn, eid, "admin", reason="x")
        assert ce.notices(conn) == []                                             # 人移除的不進 1.4
    finally:
        conn.close()


def test_例外表讀不到_照常提醒(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    try:
        _vol(conn, "DS_FULL", 100)
        ce.set_exempt(conn, _cand(conn, "ds", "DS_FULL"), "x", 90, "admin")
        conn.commit()
        monkeypatch.setattr(ce, "_has", lambda *_a: (_ for _ in ()).throw(RuntimeError("boom")))
        st = ce.load(conn)
        assert st.error and ce.check(st, "ds", "anything") is None
        ds = sc.datastores(conn)
        assert ds["volumes"][0]["lun_low"] and ds["exempt_error"]
    finally:
        conn.close()


def test_主機磁碟例外_只排除那個掛載點_整台改用其餘掛載點判(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="S-1", hostname="appx01", ip="10.99.3.1")
        items = [{"name": "/arch", "size_gb": 1000, "free_gb": 10}, {"name": "/", "size_gb": 100, "free_gb": 60}]
        conn.execute("INSERT INTO host_spec (asset_serial, ip, storage_json, collected_at) VALUES (?,?,?,?)",
                     ("S-1", "10.99.3.1", json.dumps({"by": "mount", "items": items}), "2026-11-13 10:00:00"))
        conn.commit()
        al = sc.alerts(sc.datastores(conn), sc.physical(conn))
        assert [p["asset_serial"] for p in al["physical"]] == ["S-1"]
        c = _cand(conn, "disk", "/arch")
        assert c["key"].endswith("::/arch") and c["used_pct"] == 99.0 and c["eos_date"] is None
        ce.set_exempt(conn, c, "歸檔碟、不再寫入", 90, "admin")
        conn.commit()
        al = sc.alerts(sc.datastores(conn), sc.physical(conn))
        assert al["physical"] == [] and [p["asset_serial"] for p in al["physical_exempt"]] == ["S-1"]
        ph = sc.physical(conn)["rows"][0]
        assert ph["exempt_mounts"] == 1 and ph["alert_free_pct"] == 60.0 and ph["free_pct"] == round(70 * 100 / 1100, 1)
        # 另一個掛載點滿了 → 照樣列處理
        items[1]["free_gb"] = 5
        conn.execute("UPDATE host_spec SET storage_json = ? WHERE asset_serial = 'S-1'", (json.dumps({"by": "mount", "items": items}),))
        al = sc.alerts(sc.datastores(conn), sc.physical(conn))
        assert [p["asset_serial"] for p in al["physical"]] == ["S-1"] and al["physical"][0]["used_pct"] == 95.0
    finally:
        conn.close()


def test_到EOS日_顯示來源_沒有EOS不能選(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="S-1", hostname="appx01", ip="10.99.3.1")
        conn.execute("INSERT INTO host_spec (asset_serial, ip, storage_json, collected_at) VALUES (?,?,?,?)",
                     ("S-1", "10.99.3.1", json.dumps({"by": "mount", "items": [{"name": "/arch", "size_gb": 100, "free_gb": 1}]}), "2026-11-13"))
        conn.commit()
        eos_d = (TODAY + timedelta(days=500)).isoformat()
        monkeypatch.setattr(ce, "host_eos", lambda _c, s: (eos_d, "作業系統 EOS（FakeOS 1）") if s == "S-1" else (None, None))
        c = _cand(conn, "disk", "/arch")
        assert c["eos_date"] == eos_d and "FakeOS" in c["eos_source"]
        eid = ce.set_exempt(conn, c, "等下線", "eos", "admin")
        r = conn.execute("SELECT expires, duration, eos_source FROM capacity_exempt WHERE id = ?", (eid,)).fetchone()
        assert r["expires"] == (TODAY + timedelta(days=365)).isoformat() and r["duration"] == "eos" and "FakeOS" in r["eos_source"]
    finally:
        conn.close()


# ── API：只有管理員 ──
@pytest.fixture
def api_env(tmp_path, monkeypatch):
    import api
    import auth
    from fastapi.testclient import TestClient
    p = tmp_path / "t.db"
    monkeypatch.setenv("ASSET_DB_PATH", str(p))
    db.init_db(p)
    c = db.get_connection(p)

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
    yield c, client
    api.app.dependency_overrides.clear()
    c.close()


def test_API_只有管理員能設定編輯移除_一般人看得到(api_env):
    c, client = api_env
    _vol(c, "DS_FULL", 100)
    key = _cand(c, "ds", "DS_FULL")["key"]
    body = {"kind": "ds", "key": key, "reason": "封存", "duration": 90}
    for who in ("alice", "vera"):
        cl = client(who)
        assert cl.post("/api/storage/exempt", json=body).status_code == 403
        assert cl.get("/api/storage/exempt").status_code == 200
    adm = client("admin")
    assert adm.post("/api/storage/exempt", json={**body, "duration": 400}).status_code == 400
    assert adm.post("/api/storage/exempt", json={**body, "key": "url:nope"}).status_code == 400
    r = adm.post("/api/storage/exempt", json={**body, "note": "備註一"})
    assert r.status_code == 200
    eid = r.json()["id"]
    assert client("alice").post(f"/api/storage/exempt/{eid}/edit", json={"reason": "x"}).status_code == 403
    assert adm.post(f"/api/storage/exempt/{eid}/edit", json={"reason": "封存（已確認）"}).json()["changed"]["reason"][1] == "封存（已確認）"
    assert client("alice").post(f"/api/storage/exempt/{eid}/remove", json={}).status_code == 403
    assert adm.post(f"/api/storage/exempt/{eid}/remove", json={"note": "改回提醒"}).status_code == 200
    j = adm.get("/api/storage/exempt").json()
    assert [x["action"] for x in j["log"]][::-1] == ["set", "edit", "edit", "remove"]
    assert j["rows"][0]["status"] == "removed" and j["presets"] == [30, 90, 180, 365]


def test_verify_numbers_有例外時_儲存容量與追蹤處理全綠(tmp_path):
    """處理＋例外＋正常＝全部、例外表獨立重判＝App、1.4 容量＝預警＋到期／失效待確認。"""
    import verify_numbers as vn
    conn = _conn(tmp_path)
    try:
        _vol(conn, "DS_FULL", 100)
        _vol(conn, "DS_FULL2", 100)
        _vol(conn, "DS_OK", 5000)
        db.insert_hardware(conn, asset_serial="S-1", hostname="appx01", ip="10.99.3.1")
        conn.execute("INSERT INTO host_spec (asset_serial, ip, storage_json, collected_at) VALUES (?,?,?,?)",
                     ("S-1", "10.99.3.1", json.dumps({"by": "mount", "items": [{"name": "/arch", "size_gb": 1000, "free_gb": 10},
                                                                              {"name": "/", "size_gb": 100, "free_gb": 60}]}), "2026-11-13"))
        ce.set_exempt(conn, _cand(conn, "ds", "DS_FULL"), "封存", 90, "admin")
        ce.set_exempt(conn, _cand(conn, "disk", "/arch"), "歸檔", 90, "admin")
        e2 = ce.set_exempt(conn, _cand(conn, "ds", "DS_FULL2"), "封存", 90, "admin")
        conn.execute("UPDATE capacity_exempt SET expires = ? WHERE id = ?", (TODAY.isoformat(), e2))
        conn.commit()
        ce.tick(conn, force=True)
        vn.FAILS, vn.RESULTS = 0, []
        vn.check_storage(conn)
        vn.check_v2_today(conn)
        vn.check_v2(conn)
        bad = [r for r in vn.RESULTS if r["ok"] is False]
        assert not bad, bad
        assert any("已知例外" in r["name"] for r in vn.RESULTS)
    finally:
        conn.close()


# ── 2026-11-14 獨立審查修正 ──
def test_P2_1_Windows掛載資料夾保留完整路徑_跟磁碟機不同鍵():
    assert ce.norm_mount("D:\\SQLData\\Log\\ Label:LOG  Serial Number 99") == "D:\\sqldata\\log"
    assert ce.norm_mount("D:\\ Label:DATA  Serial Number 12") == "D:"
    assert ce.norm_mount("/var - 50 GB") == "/var"
    assert ce.unique_keys([("D:\\ Label:A", "h::D:"), ("D:", "h::D:"), ("D:\\SQL\\", "h|D:\\sql")]) == {"h|D:\\sql": "D:\\SQL\\"}


def test_P2_1_同一台兩個項目正規化成同一鍵_不套例外(tmp_path):
    conn = _conn(tmp_path)
    try:
        db.insert_hardware(conn, asset_serial="S-1", hostname="appx01", ip="10.99.3.1")
        items = [{"name": "D:", "size_gb": 100, "free_gb": 1}, {"name": "D:\\", "size_gb": 100, "free_gb": 1}, {"name": "C:", "size_gb": 100, "free_gb": 50}]
        conn.execute("INSERT INTO host_spec (asset_serial, ip, storage_json, collected_at) VALUES (?,?,?,?)",
                     ("S-1", "10.99.3.1", json.dumps({"by": "mount", "items": items}), "2026-11-13"))
        mk = ce.mk_of_serial(conn, "S-1")
        ce.set_exempt(conn, {"kind": "disk", "key": ce.disk_key(mk, "D:"), "label": "x", "used_pct": 99.0, "source": "spec"}, "x", 90, "admin")
        conn.commit()
        assert not any(c["key"] == ce.disk_key(mk, "D:") for c in ce.candidates(conn)["rows"])    # 分不出來：不列成可設定項目
        ph = sc.physical(conn)["rows"][0]
        assert ph["exempt_mounts"] == 0 and [p["asset_serial"] for p in sc.alerts(sc.datastores(conn), sc.physical(conn))["physical"]] == ["S-1"]
    finally:
        conn.close()


def test_P2_3_基準分來源_其他來源第一次取樣當基準_只跟同來源比(tmp_path):
    conn = _conn(tmp_path)
    try:
        _vol(conn, "DS_A", 5000)                                   # RVTools 50%
        eid = ce.set_exempt(conn, _cand(conn, "ds", "DS_A"), "x", 90, "admin")
        conn.commit()
        st = ce.load(conn)
        key = _cand(conn, "ds", "DS_A")["key"]
        # vROps 第一次看到 55%：沒有自己的基準，不判上升
        assert ce.check(st, "ds", key, 55.0, 4500, 10000, "vrops", "b1", "r1")["exempt"]
        conn.execute("INSERT INTO capacity_exempt_obs (exempt_id, source, item, base_used_pct, base_free_gb, base_cap_gb, sample_key, over_count) "
                     "VALUES (?, 'vrops', 'r1', 55.0, 4500, 10000, 'b1', 0)", (eid,))
        conn.execute("INSERT INTO capacity_exempt_obs (exempt_id, source, item, base_used_pct, sample_key, over_count) "
                     "VALUES (?, 'wug_daily', 'C:', 60.0, '2026-11-12', 1)", (eid,))
        conn.commit()
        st = ce.load(conn)
        assert ce.check(st, "ds", key, 56.9, 4310, 10000, "vrops", "b2", "r1")["exempt"]       # 55＋2 以內
        r = ce.check(st, "ds", key, 62.5, None, None, "wug_daily", "2026-11-13", "C:")          # 第 2 個上升樣本 → 失效（Teams 也看得到）
        assert not r["exempt"] and "60% 升到 62.5%" in r["text"]
    finally:
        conn.close()


def test_P2_2_vROps對RVTools_兩邊同名不唯一就不對_有UUID用vCenter加名稱(tmp_path):
    conn = _conn(tmp_path)
    try:
        _vol(conn, "LUN-A", 5000)
        _vol(conn, "LUN-B", 5000)
        rows = [{"res_id": "r1", "name": "LUN-A", "vcid": "vc-1"}, {"res_id": "r2", "name": "LUN-B", "vcid": "vc-1"},
                {"res_id": "r3", "name": "LUN-B", "vcid": "vc-2"}]
        k = ce.vrops_keys(conn, rows)
        assert set(k) == {"r1"}                                        # LUN-B 在 vROps 有兩個：不對
        for name, uu in (("LUN-C", "VC-1"), ("LUN-C", "vc-2")):
            rec = {"Name": name, "URL": f"ds:///vmfs/volumes/{name}-{uu}/", "VI SDK Server": "10.99.0.1", "VI SDK UUID": uu, "Type": "VMFS",
                   "Capacity MiB": 1024, "In Use MiB": 10, "Free MiB": 1014}
            conn.execute("INSERT INTO source_record (source, source_key, payload, resolved_status, collected_at) VALUES "
                         "('vcenter_extra:vDatastore', ?, ?, 'extra', '2026-11-13 09:00:00')", (f"{name}{uu}", json.dumps(rec)))
        conn.commit()
        k = ce.vrops_keys(conn, [{"res_id": "x1", "name": "LUN-C", "vcid": "vc-1"}, {"res_id": "x2", "name": "LUN-C", "vcid": "VC-2"}])
        assert k == {"x1": "url:ds:///vmfs/volumes/LUN-C-VC-1/", "x2": "url:ds:///vmfs/volumes/LUN-C-vc-2/"}
    finally:
        conn.close()


def test_P3_1_同一項只能一筆生效中_唯一索引(tmp_path):
    import sqlite3
    conn = _conn(tmp_path)
    try:
        conn.execute("INSERT INTO capacity_exempt (kind, key, reason, duration, expires, created_at) VALUES ('ds','k','x','90','2099-01-01','t')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO capacity_exempt (kind, key, reason, duration, expires, created_at) VALUES ('ds','k','y','90','2099-01-01','t')")
        conn.execute("UPDATE capacity_exempt SET status = 'removed' WHERE key = 'k'")
        conn.execute("INSERT INTO capacity_exempt (kind, key, reason, duration, expires, created_at) VALUES ('ds','k','y','90','2099-01-01','t')")
    finally:
        conn.close()


def test_P3_2_例外數字只算擋下處理的_生效中另計(tmp_path):
    conn = _conn(tmp_path)
    try:
        _vol(conn, "DS_LOW", 5000)                                  # 沒超標
        ce.set_exempt(conn, _cand(conn, "ds", "DS_LOW"), "x", 90, "admin")
        conn.commit()
        ds = sc.datastores(conn)
        assert ds["exempt_count"] == 0 and ds["exempt_active_count"] == 1
        o = sc.overview(conn)
        assert o["vmdisk"]["exempt_count"] == 0 and o["vmdisk"]["exempt_active_count"] == 1
    finally:
        conn.close()


def test_P3_5_tick遇到已被移除的例外_略過不回滾(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    try:
        _vol(conn, "DS_A", 100)
        _vol(conn, "DS_B", 100)
        ea = ce.set_exempt(conn, _cand(conn, "ds", "DS_A"), "x", 90, "admin")
        eb = ce.set_exempt(conn, _cand(conn, "ds", "DS_B"), "x", 90, "admin")
        conn.execute("UPDATE capacity_exempt SET expires = ? WHERE id IN (?, ?)", (TODAY.isoformat(), ea, eb))
        conn.commit()
        stale = ce.load(conn)
        ce.remove(conn, ea, "admin")                                 # 排程讀完之後、寫之前被人移除
        conn.commit()
        monkeypatch.setattr(ce, "load", lambda *_a, **_k: stale)
        assert ce.tick(conn, force=True)["expired"] == 1
        assert {r[0]: r[1] for r in conn.execute("SELECT id, status FROM capacity_exempt")} == {ea: "removed", eb: "expired"}
    finally:
        conn.close()
