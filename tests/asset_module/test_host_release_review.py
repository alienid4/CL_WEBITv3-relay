"""重複登記釋放：GPT 獨立審查 6 項必修的回歸測試（2.041.98）。

1 舊端點不能繞過（見 test_host_release）｜2 指紋涵蓋列內容｜3 同表同列只快照一次｜
4 還原依主鍵接回並驗筆數｜5 欄名含 serial 的未納入表 fail-closed｜6 密碼／金鑰類不進快照也不刪＋快照大小上限。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import host_release  # noqa: E402
from test_host_release import _do, _dump, _extra_rows, _key, _prev, env  # noqa: E402,F401

S = "ADOPT-10.99.167.12"


def _exec(p, *sqls):
    c = db.get_connection(p)
    for q in sqls:
        c.execute(q)
    c.commit()
    c.close()


def _audit_id(p):
    c = db.get_connection(p)
    try:
        return c.execute("SELECT id FROM merge_audit ORDER BY id DESC").fetchone()[0]
    finally:
        c.close()


def _intact(p, serial=S):
    c = db.get_connection(p)
    try:
        return bool(c.execute("SELECT 1 FROM hardware WHERE asset_serial = ?", (serial,)).fetchone())
    finally:
        c.close()


# ---------------------------------------------------------------- 2 指紋涵蓋列內容
def test_預覽後從屬資料內容變了_筆數不變_實刪409(env):
    cl, p, _ = env
    k = _key(p)
    pv = _prev(cl, k).json()
    _exec(p, "UPDATE software SET db_software = 'changed-after-preview' WHERE asset_serial = '%s'" % S)
    r = _do(cl, k, fp=pv["fingerprint"])
    assert r.status_code == 409 and "預覽之後" in r.json()["detail"]
    assert _intact(p)
    # 重新預覽後指紋不同、可以執行
    pv2 = _prev(cl, k).json()
    assert pv2["fingerprint"] != pv["fingerprint"]
    assert _do(cl, k, fp=pv2["fingerprint"]).status_code == 200


def test_預覽後hardware本身內容變了_也要重新預覽(env):
    cl, p, _ = env
    k = _key(p)
    pv = _prev(cl, k).json()
    _exec(p, "UPDATE hardware SET device_model = 'X-after' WHERE asset_serial = '%s'" % S)
    assert _do(cl, k, fp=pv["fingerprint"]).status_code == 409


# ---------------------------------------------------------------- 3 同表同列只快照一次
def test_同表同時有asset_serial與外鍵欄_同一列只快照一次_還原成功逐列相同(env, monkeypatch):
    cl, p, _ = env
    monkeypatch.setitem(host_release.SNAPSHOT_TABLES, "zz_dual", frozenset({"id", "asset_serial", "owner_serial", "note"}))
    k = _key(p)
    _exec(p, "CREATE TABLE zz_dual (id INTEGER PRIMARY KEY AUTOINCREMENT, asset_serial TEXT, "
             "owner_serial TEXT REFERENCES hardware(asset_serial), note TEXT)",
          "INSERT INTO zz_dual (asset_serial, owner_serial, note) VALUES ('%s', '%s', 'both')" % (S, S),
          "INSERT INTO zz_dual (asset_serial, owner_serial, note) VALUES (NULL, '%s', 'owner-only')" % S)
    c = db.get_connection(p)
    cat = host_release._catalog(c)
    assert ("zz_dual", "asset_serial") in cat["serial"] and ("zz_dual", "owner_serial") in cat["serial"]
    before = [dict(r) for r in c.execute("SELECT * FROM zz_dual ORDER BY id")]
    c.close()
    pv = _prev(cl, k).json()
    cnt = pv["release"][0]["counts"]
    assert cnt["zz_dual"] + cnt.get("zz_dual.owner_serial", 0) == 2, cnt          # 兩列各一次，不是三次
    assert _do(cl, k, fp=pv["fingerprint"]).status_code == 200
    c = db.get_connection(p)
    assert c.execute("SELECT COUNT(*) FROM zz_dual").fetchone()[0] == 0
    snap = json.loads(c.execute("SELECT snapshot FROM merge_audit").fetchone()[0])
    assert sum(len(v["rows"]) for v in snap["tables"].values() if v["table"] == "zz_dual") == 2
    aid = c.execute("SELECT id FROM merge_audit").fetchone()[0]
    c.close()
    r = cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": aid})
    assert r.status_code == 200, r.text
    c = db.get_connection(p)
    assert [dict(r) for r in c.execute("SELECT * FROM zz_dual ORDER BY id")] == before
    c.close()


# ---------------------------------------------------------------- 4 還原依主鍵接回並驗筆數
def _with_source_record(p):
    c = db.get_connection(p)
    hid = c.execute("SELECT id FROM hardware WHERE asset_serial = ?", (S,)).fetchone()[0]
    c.execute("INSERT INTO source_record (source, source_key, payload, resolved_status, resolved_hardware_id) "
              "VALUES ('excel', 'r1', '{}', 'matched', ?)", (hid,))
    c.commit()
    sid = c.execute("SELECT id FROM source_record WHERE source_key = 'r1'").fetchone()[0]
    c.close()
    return hid, sid


def test_還原接回_關聯列已被刪_整筆回滾409(env):
    cl, p, _ = env
    k = _key(p)
    _with_source_record(p)
    assert _do(cl, k).status_code == 200
    _exec(p, "DELETE FROM source_record WHERE source_key = 'r1'")
    r = cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": _audit_id(p)})
    assert r.status_code == 409 and "source_record" in r.json()["detail"]
    assert not _intact(p), "接不回去就不能留下半套（hardware 也要回滾）"
    c = db.get_connection(p)
    assert c.execute("SELECT restored_at FROM merge_audit").fetchone()[0] is None
    assert c.execute("SELECT COUNT(*) FROM software WHERE asset_serial = ?", (S,)).fetchone()[0] == 0
    c.close()


def test_還原接回_關聯欄位已被改成別台_整筆回滾409(env):
    cl, p, _ = env
    k = _key(p)
    _with_source_record(p)
    assert _do(cl, k).status_code == 200
    c = db.get_connection(p)
    other = c.execute("SELECT id FROM hardware WHERE asset_serial = 'H-OTHER'").fetchone()[0]
    c.close()
    _exec(p, "UPDATE source_record SET resolved_hardware_id = %d WHERE source_key = 'r1'" % other)
    r = cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": _audit_id(p)})
    assert r.status_code == 409
    assert not _intact(p)
    c = db.get_connection(p)
    assert c.execute("SELECT resolved_hardware_id FROM source_record WHERE source_key = 'r1'").fetchone()[0] == other
    c.close()


def test_沒有主鍵的可空關聯表_改成完整快照並刪除_可還原(env, monkeypatch):
    """沒有主鍵就沒法精準定位『接回』那一列，所以不用 rowid 賭運氣：整列快照＋刪除，還原時整列寫回。"""
    cl, p, _ = env
    monkeypatch.setitem(host_release.SNAPSHOT_TABLES, "zz_nopk", frozenset({"note", "hw_id"}))
    k = _key(p)
    c = db.get_connection(p)
    hid = c.execute("SELECT id FROM hardware WHERE asset_serial = ?", (S,)).fetchone()[0]
    c.close()
    _exec(p, "CREATE TABLE zz_nopk (note TEXT, hw_id INTEGER REFERENCES hardware(id))",
          "INSERT INTO zz_nopk VALUES ('a', %d)" % hid, "INSERT INTO zz_nopk VALUES ('b', %d)" % hid)
    c = db.get_connection(p)
    assert not [x for x in host_release._catalog(c)["link"] if x[0] == "zz_nopk" and x[2]]
    c.close()
    assert _do(cl, k).status_code == 200
    c = db.get_connection(p)
    assert c.execute("SELECT COUNT(*) FROM zz_nopk").fetchone()[0] == 0
    c.close()
    assert cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": _audit_id(p)}).status_code == 200
    c = db.get_connection(p)
    assert sorted(r[0] for r in c.execute("SELECT note FROM zz_nopk")) == ["a", "b"]
    c.close()


# ---------------------------------------------------------------- 5 欄名含 serial 的未納入表 fail-closed
def test_欄名含serial但沒外鍵的表_有該序號就擋下_預覽也列出(env):
    cl, p, _ = env
    k = _key(p)
    _exec(p, "CREATE TABLE zz_other (id INTEGER PRIMARY KEY, hw_serial_no TEXT)",
          "INSERT INTO zz_other (hw_serial_no) VALUES ('%s')" % S)
    pv = _prev(cl, k).json()
    assert pv["blocked"] is True
    assert any("zz_other.hw_serial_no" in b for b in pv["blockers"])
    assert [(x["table"], x["column"], x["rows"], x["reason"]) for x in pv["release"][0]["protected"]] == \
        [("zz_other", "hw_serial_no", 1, "未列入可快照清單")]
    r = _do(cl, k, fp=pv["fingerprint"])
    assert r.status_code == 409 and "zz_other.hw_serial_no" in r.json()["detail"]
    assert _intact(p)
    c = db.get_connection(p)
    assert c.execute("SELECT COUNT(*) FROM software WHERE asset_serial = ?", (S,)).fetchone()[0] == 1
    c.close()
    # 該序號不在其中 → 不擋
    _exec(p, "DELETE FROM zz_other")
    pv2 = _prev(cl, k).json()
    assert pv2["blocked"] is False
    assert _do(cl, k, fp=pv2["fingerprint"]).status_code == 200


# ---------------------------------------------------------------- 6 密碼／金鑰類不進快照也不刪＋大小上限
def test_清單外的表_該序號有列就擋_沒有列才放行(env):
    """不在 SNAPSHOT_TABLES 的表（不管名字像不像祕密）：該序號有列就擋「未列入可快照清單」；沒有列才放行，且不進快照、不刪。"""
    cl, p, _ = env
    k = _key(p)
    for t in ("zz_secret", "zz_plain"):
        _exec(p, f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, asset_serial TEXT, val TEXT)",
              f"INSERT INTO {t} (asset_serial, val) VALUES ('{S}', 'VAL-{t}')")
    c = db.get_connection(p)
    cat = host_release._catalog(c)
    assert ("zz_plain", "asset_serial", "serial") in cat["unlisted"] and ("zz_plain", "asset_serial") not in cat["serial"]
    c.close()
    pv = _prev(cl, k).json()
    assert pv["blocked"] is True
    for t in ("zz_secret", "zz_plain"):
        assert any(t in b and "未列入可快照清單" in b for b in pv["blockers"]), t
    r = _do(cl, k, fp=pv["fingerprint"])
    assert r.status_code == 409 and "未列入可快照清單" in r.json()["detail"]
    assert _intact(p)
    _exec(p, "UPDATE zz_secret SET asset_serial = 'H-OTHER'", "UPDATE zz_plain SET asset_serial = 'H-OTHER'")
    pv2 = _prev(cl, k).json()
    assert pv2["blocked"] is False
    assert _do(cl, k, fp=pv2["fingerprint"]).status_code == 200
    c = db.get_connection(p)
    snap = c.execute("SELECT snapshot FROM merge_audit").fetchone()[0]
    assert "VAL-zz_secret" not in snap and "VAL-zz_plain" not in snap
    assert c.execute("SELECT COUNT(*) FROM zz_plain").fetchone()[0] == 1
    c.close()


import pytest  # noqa: E402


@pytest.mark.parametrize("col", ["value", "payload", "data", "signing_key_value", "nopasswd_token", "access_key", "encryption_key", "ssh_key",
                                 "keyring", "pem_data", "passphrase", "has_secret", "authorized_keys_extra"])
def test_清單內的表出現未審核疑似祕密欄位_有列就擋(env, col):
    """software 在允許清單內；臨時加一個像祕密的欄位（沒審核）→ 該序號在 software 有列，釋放就被擋。"""
    cl, p, _ = env
    k = _key(p)
    _exec(p, f"ALTER TABLE software ADD COLUMN {col} TEXT")
    c = db.get_connection(p)
    cat = host_release._catalog(c)
    c.close()
    assert ("software", "asset_serial") not in cat["serial"]
    assert any(g[0] == "software" and col in g[3] for g in cat["guarded"]), col
    pv = _prev(cl, k).json()
    assert pv["blocked"] and any("software" in b and col in b for b in pv["blockers"]), pv["blockers"]
    r = _do(cl, k, fp=pv["fingerprint"])
    assert r.status_code == 409 and col in r.json()["detail"]
    assert _intact(p)


def test_護欄名稱規則_業務鍵放行_其餘含key或祕密字樣一律算(env):
    for n in ["key", "machine_key", "item_key", "source_key", "module_key", "metric_key", "row_key", "event_key", "sys_key",
              "group_key", "state_key", "rule_key", "env_key", "field_key", "dim_key", "diff_key", "target_key", "natural_key",
              "sort_key", "check_key", "legacy_machine_key", "hostname", "ip", "asset_serial"]:
        assert not host_release._sensitive_name(n), n
    for n in ["signing_key_value", "nopasswd_token", "sudo_nopasswd", "has_secrets", "authorized_keys", "public_key", "keyring",
              "access_key", "encryption_key", "ssh_key", "api_key", "root_key_at", "passphrase", "pem", "password_hash", "token",
              "credential_name", "host_api_key"]:
        assert host_release._sensitive_name(n), n
    # 允許清單內、已逐欄審核的欄位不會把整張表擋掉；既有業務表仍照常處理
    c = db.get_connection(env[1])
    cat = host_release._catalog(c)
    c.close()
    assert cat["hardware_bad"] == [] and cat["guarded"] == []
    for t in ("ci_node", "host_metric_latest", "baseline_drift", "golive_check", "host_account", "software", "personnel", "doc_archive"):
        assert (t, "asset_serial") in cat["serial"], t
    assert ("host_api_key", "asset_serial", "serial") in cat["unlisted"]


def test_舊格式2快照_還原409不是500_資料不動(env):
    cl, p, _ = env
    k = _key(p)
    assert _do(cl, k).status_code == 200
    aid = _audit_id(p)
    c = db.get_connection(p)
    snap = json.loads(c.execute("SELECT snapshot FROM merge_audit WHERE id = ?", (aid,)).fetchone()[0])
    snap["format"] = 2
    snap["links"] = {"source_record.resolved_hardware_id": {"table": "source_record", "column": "resolved_hardware_id",
                                                             "kind": "relink", "hardware_id": 1, "rowids": [1]}}
    c.execute("UPDATE merge_audit SET snapshot = ?, snapshot_format = 2 WHERE id = ?", (json.dumps(snap), aid))
    c.commit()
    c.close()
    r = cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": aid})
    assert r.status_code == 409 and "舊格式" in r.json()["detail"]
    assert not _intact(p)
    it = cl.get("/api/v2/hosts/duplicates/releases").json()["items"][0]
    assert it["state"] == "無法還原" and "舊格式" in it["reason"] and not it["restorable"]


def test_格式3但links缺pk或keys_還原409(env):
    cl, p, _ = env
    k = _key(p)
    assert _do(cl, k).status_code == 200
    aid = _audit_id(p)
    c = db.get_connection(p)
    snap = json.loads(c.execute("SELECT snapshot FROM merge_audit WHERE id = ?", (aid,)).fetchone()[0])
    snap["links"] = {"x.y": {"table": "source_record", "column": "resolved_hardware_id", "hardware_id": 1, "rowids": [1]}}
    c.execute("UPDATE merge_audit SET snapshot = ? WHERE id = ?", (json.dumps(snap), aid))
    c.commit()
    c.close()
    r = cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": aid})
    assert r.status_code == 409 and "舊格式" in r.json()["detail"]
    assert not _intact(p)


def test_密碼金鑰類表_綁著該序號的外鍵_擋下釋放(env):
    """host_api_key（表名含 apikey）對 hardware 有外鍵：不進快照也不刪，那就不能刪 hardware → 預覽擋下、實刪拒絕。"""
    cl, p, _ = env
    k = _key(p)
    _exec(p, "INSERT INTO host_api_key (asset_serial, key_hash) VALUES ('%s', 'hash-xyz')" % S)
    pv = _prev(cl, k).json()
    assert pv["blocked"] and any("host_api_key" in b for b in pv["blockers"])
    r = _do(cl, k, fp=pv["fingerprint"])
    assert r.status_code == 409 and "host_api_key" in r.json()["detail"]
    assert _intact(p)
    c = db.get_connection(p)
    assert c.execute("SELECT COUNT(*) FROM host_api_key").fetchone()[0] == 1
    c.close()


def test_快照超過上限_預覽擋下_實刪拒絕(env, monkeypatch):
    cl, p, _ = env
    k = _key(p)
    monkeypatch.setattr(host_release, "MAX_SNAPSHOT_BYTES", 200)
    pv = _prev(cl, k).json()
    assert pv["blocked"] and any("超過上限" in b for b in pv["blockers"])
    assert pv["release"][0]["snapshot_bytes"] > 200
    r = _do(cl, k, fp=pv["fingerprint"])
    assert r.status_code == 409 and "超過上限" in r.json()["detail"]
    assert _intact(p)
    monkeypatch.setattr(host_release, "MAX_SNAPSHOT_BYTES", 20 * 1024 * 1024)
    assert _do(cl, k).status_code == 200


def test_verify_numbers孤兒檢查不把密碼金鑰類算進去(env):
    import verify_numbers as vn
    cl, p, _ = env
    _exec(p, "PRAGMA foreign_keys = OFF")
    c = db.get_connection(p)
    c.execute("CREATE TABLE zz_secret (id INTEGER PRIMARY KEY, asset_serial TEXT, api_token TEXT)")
    c.execute("INSERT INTO zz_secret (asset_serial, api_token) VALUES ('GHOST-9', 't')")
    c.commit()
    f0 = vn.FAILS
    vn.check_duplicate_release(c, strict=True)
    assert vn.FAILS == f0
    c.close()


@pytest.mark.parametrize("fmt", [None, 2, 4, "3"])
def test_快照內嵌format缺少或不符_還原409_資料不動(env, fmt):
    cl, p, _ = env
    k = _key(p)
    assert _do(cl, k).status_code == 200
    aid = _audit_id(p)
    c = db.get_connection(p)
    snap = json.loads(c.execute("SELECT snapshot FROM merge_audit WHERE id = ?", (aid,)).fetchone()[0])
    if fmt is None:
        snap.pop("format")
    else:
        snap["format"] = fmt
    c.execute("UPDATE merge_audit SET snapshot = ? WHERE id = ?", (json.dumps(snap), aid))   # snapshot_format 欄仍是 3
    c.commit()
    c.close()
    r = cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": aid})
    assert r.status_code == 409 and "舊格式" in r.json()["detail"]
    assert not _intact(p)
    c = db.get_connection(p)
    assert c.execute("SELECT restored_at FROM merge_audit WHERE id = ?", (aid,)).fetchone()[0] is None
    c.close()


# ---------------------------------------------------------------- 第四輪：逐欄審核、不分大小寫、guarded 表的 serial 欄
def test_審核清單等於正式schema_每張清單表都全部審核過(env):
    """允許清單的欄位集合要跟 schema 實際欄位一致：新庫上不應有任何 guarded／hardware_bad（否則就是清單漏審）。"""
    c = db.get_connection(env[1])
    cat = host_release._catalog(c)
    hw = {r[1] for r in c.execute("PRAGMA table_info(hardware)")}
    c.close()
    assert cat["guarded"] == [] and cat["hardware_bad"] == []
    assert hw == set(host_release.HARDWARE_COLUMNS)


def test_hardware出現未審核欄位_任何釋放都擋(env):
    cl, p, _ = env
    k = _key(p)
    _exec(p, "ALTER TABLE hardware ADD COLUMN extra_blob TEXT")
    pv = _prev(cl, k).json()
    assert pv["blocked"] and any("資產主檔" in b and "extra_blob" in b for b in pv["blockers"])
    assert _do(cl, k, fp=pv["fingerprint"]).status_code == 409
    assert _intact(p)


def test_外鍵與欄名比對不分大小寫_清單外會被擋(env):
    cl, p, _ = env
    k = _key(p)
    c = db.get_connection(p)
    hid = c.execute("SELECT id FROM hardware WHERE asset_serial = ?", (S,)).fetchone()[0]
    c.close()
    _exec(p, "CREATE TABLE ZZ_Case (id INTEGER PRIMARY KEY, Owner_Ref TEXT REFERENCES Hardware(Asset_Serial), "
             "Hw INTEGER REFERENCES HARDWARE(ID))",
          f"INSERT INTO ZZ_Case (Owner_Ref, Hw) VALUES ('{S}', NULL)",
          f"INSERT INTO ZZ_Case (Owner_Ref, Hw) VALUES (NULL, {hid})")
    c = db.get_connection(p)
    cat = host_release._catalog(c)
    c.close()
    assert ("ZZ_Case", "Owner_Ref", "serial") in cat["unlisted"] and ("ZZ_Case", "Hw", "id") in cat["unlisted"]
    pv = _prev(cl, k).json()
    assert pv["blocked"] and sum(1 for b in pv["blockers"] if "ZZ_Case" in b) == 2
    assert _do(cl, k, fp=pv["fingerprint"]).status_code == 409
    assert _intact(p)


def test_外鍵與欄名比對不分大小寫_清單內可快照並還原(env, monkeypatch):
    cl, p, _ = env
    k = _key(p)
    monkeypatch.setitem(host_release.SNAPSHOT_TABLES, "zz_case", frozenset({"id", "owner_ref", "note"}))
    _exec(p, "CREATE TABLE ZZ_Case (ID INTEGER PRIMARY KEY, Owner_Ref TEXT REFERENCES Hardware(Asset_Serial), Note TEXT)",
          f"INSERT INTO ZZ_Case (Owner_Ref, Note) VALUES ('{S}', 'n1')")
    c = db.get_connection(p)
    cat = host_release._catalog(c)
    before = [dict(r) for r in c.execute("SELECT * FROM ZZ_Case")]
    c.close()
    assert ("ZZ_Case", "Owner_Ref") in cat["serial"]
    assert _do(cl, k).status_code == 200
    c = db.get_connection(p)
    assert c.execute("SELECT COUNT(*) FROM ZZ_Case").fetchone()[0] == 0
    c.close()
    assert cl.post("/api/v2/hosts/duplicates/restore", json={"audit_id": _audit_id(p)}).status_code == 200
    c = db.get_connection(p)
    assert [dict(r) for r in c.execute("SELECT * FROM ZZ_Case")] == before
    c.close()


def test_guarded表的額外serial欄仍登記_只靠該欄對到也要擋(env, monkeypatch):
    cl, p, _ = env
    k = _key(p)
    monkeypatch.setitem(host_release.SNAPSHOT_TABLES, "zz_g", frozenset({"id", "asset_serial", "other_serial"}))
    _exec(p, "CREATE TABLE zz_g (id INTEGER PRIMARY KEY, asset_serial TEXT, other_serial TEXT, value TEXT)",
          f"INSERT INTO zz_g (asset_serial, other_serial, value) VALUES ('H-OTHER', '{S}', 'v')")
    c = db.get_connection(p)
    cat = host_release._catalog(c)
    c.close()
    assert any(g[0] == "zz_g" for g in cat["guarded"])                    # value 未審核 → guarded
    assert ("zz_g", "other_serial") in cat["suspect"]                     # 但額外 serial 欄仍登記
    pv = _prev(cl, k).json()
    assert pv["blocked"] and any("zz_g.other_serial" in b for b in pv["blockers"])
    assert not any("zz_g.asset_serial" in b for b in pv["blockers"])      # asset_serial 對不到該序號，不是靠它擋的
    assert _do(cl, k, fp=pv["fingerprint"]).status_code == 409
    assert _intact(p)
