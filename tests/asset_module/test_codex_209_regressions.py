"""Codex 固定 209 回歸工單（1137～1189、939）在現行程式的重現與修正釘子。

每一題都是「雙向」：故障／競態時不能留下半套（或要明講衝突），正常路徑照舊可用。
故障注入一律用 SQLite trigger（RAISE(ABORT)）或包一層連線代理，全部合成資料、隔離暫存庫。
⚠ 含 D3（資料品質口徑）、D4（人名合併、CIA 待異動、CIA 匯入設定／原始檔）的修正，需獨立審查。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import api  # noqa: E402
import db  # noqa: E402
import doc_import  # noqa: E402
import excel_import  # noqa: E402

from test_api import _client, _insert_hardware  # noqa: E402


def _now(days: float = 0) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _conn(db_path):
    return db.get_connection(db_path)


def _add_doc(db_path, values=None, review_status="pending") -> int:
    c = _conn(db_path)
    try:
        cur = c.execute(
            "INSERT INTO doc_archive (doc_type, file_name, file_path, values_json, review_status, "
            "bind_confidence, imported_at) VALUES ('provision_form', 'f.docx', '/nonexistent/f.docx', ?, ?, "
            "'review', ?)",
            (json.dumps(values or {}, ensure_ascii=False), review_status, _now()))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


# ---------------------------------------------------------------- 1137 單據綁定
def test_1137_綁定不存在的單據回404_不是ok(tmp_path):
    client, db_path = _client(tmp_path)
    _insert_hardware(db_path, "DOC-1", "h1", "10.99.0.1")
    r = client.post("/api/documents/99999/bind", json={"asset_serial": "DOC-1"})
    assert r.status_code == 404
    r = client.post("/api/documents/99999/bind", json={"asset_serial": None})
    assert r.status_code == 404


def test_1137_資產已被刪除就不能綁_有效綁定與解除照舊(tmp_path):
    client, db_path = _client(tmp_path)
    _insert_hardware(db_path, "DOC-1", "h1", "10.99.0.1")
    _insert_hardware(db_path, "DOC-2", "h2", "10.99.0.2")
    doc = _add_doc(db_path)
    assert client.post(f"/api/documents/{doc}/bind", json={"asset_serial": "DOC-1"}).status_code == 200
    c = _conn(db_path)
    c.execute("DELETE FROM hardware WHERE asset_serial = 'DOC-2'")
    c.commit()
    c.close()
    assert client.post(f"/api/documents/{doc}/bind", json={"asset_serial": "DOC-2"}).status_code == 400
    c = _conn(db_path)
    row = c.execute("SELECT asset_serial, bind_confidence FROM doc_archive WHERE id = ?", (doc,)).fetchone()
    c.close()
    assert (row[0], row[1]) == ("DOC-1", "manual"), "被拒的綁定不可以改到既有綁定"
    assert client.post(f"/api/documents/{doc}/bind", json={"asset_serial": None}).status_code == 200


def test_1137_檢查完到寫入之間資產被刪_寫入那一句自己擋(tmp_path):
    """直接在模組層模擬：檢查用的 SELECT 之後、UPDATE 之前另一個連線刪掉資產。"""
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "DOC-1", "h1", "10.99.0.1")
    doc = _add_doc(db_path)
    main = _conn(db_path)

    class P:
        def __init__(self, c):
            self.c = c

        def execute(self, sql, *a):
            if sql.startswith("UPDATE doc_archive"):
                o = _conn(db_path)
                o.execute("DELETE FROM hardware WHERE asset_serial = 'DOC-1'")
                o.commit()
                o.close()
            return self.c.execute(sql, *a)

        def __getattr__(self, k):
            return getattr(self.c, k)

    with pytest.raises(ValueError):
        doc_import.bind_document(P(main), doc, "DOC-1")
    row = main.execute("SELECT asset_serial FROM doc_archive WHERE id = ?", (doc,)).fetchone()
    main.close()
    assert row[0] is None


# ---------------------------------------------------------------- 1139 審核覆蓋並行更正
def test_1139_只確認的舊請求不能蓋掉別人剛做的更正(tmp_path):
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    doc = _add_doc(db_path, {"cpu": {"label": "CPU", "value": "4", "source": "其他填空"}})
    main = _conn(db_path)

    class P:
        """讀完單據那一刻，另一個使用者完成更正（4→8）。"""
        fired = False

        def __init__(self, c):
            self.c = c

        def execute(self, sql, *a):
            cur = self.c.execute(sql, *a)
            if sql.startswith("SELECT * FROM doc_archive") and not P.fired:
                P.fired = True
                row = cur.fetchone()
                o = _conn(db_path)
                doc_import.review_document(o, doc, {"cpu": "8"}, "other")
                o.close()

                class R:
                    def fetchone(self_inner):
                        return row
                return R()
            return cur

        def __getattr__(self, k):
            return getattr(self.c, k)

    with pytest.raises(doc_import.ReviewConflict):
        doc_import.review_document(P(main), doc, None, "admin")
    main.close()
    c = _conn(db_path)
    v = json.loads(c.execute("SELECT values_json FROM doc_archive WHERE id = ?", (doc,)).fetchone()[0])
    c.close()
    assert v["cpu"]["value"] == "8", "更正要保留"


def test_1139_依序提交照舊成功_路由衝突回409(tmp_path):
    client, db_path = _client(tmp_path)
    doc = _add_doc(db_path, {"cpu": {"label": "CPU", "value": "4", "source": "其他填空"}})
    assert client.post(f"/api/documents/{doc}/review", json={"values": {"cpu": "8"}}).status_code == 200
    r = client.post(f"/api/documents/{doc}/review", json={"values": None})
    assert r.status_code == 200 and r.json()["values"]["cpu"]["value"] == "8"
    assert client.post("/api/documents/99999/review", json={"values": None}).status_code == 404


# ---------------------------------------------------------------- 1141 批次貼標中途寫入失敗
def _tag_trigger(db_path, serial):
    import asset_tags
    c = _conn(db_path)
    asset_tags._ensure(c)
    mk = asset_tags.machine_of(c, serial)
    c.execute(f"CREATE TRIGGER t_fault BEFORE INSERT ON asset_tag WHEN NEW.machine_key = '{mk}' "
              "BEGIN SELECT RAISE(ABORT, 'injected fault'); END")
    c.commit()
    c.close()


def test_1141_第二台寫入失敗_回逐筆三種狀態_不是500(tmp_path):
    client, db_path = _client(tmp_path)
    for i in (1, 2, 3):
        _insert_hardware(db_path, f"TG-{i}", f"tg{i}", f"10.99.1.{i}")
    _tag_trigger(db_path, "TG-2")
    items = [{"asset_serial": f"TG-{i}"} for i in (1, 2, 3)]
    r = client.post("/api/asset-tags/batch", json={"items": items, "tag": "搬遷"})
    assert r.status_code == 200
    body = r.json()
    assert [x["state"] for x in body["results"]] == ["saved", "write_failed", "not_processed"]
    assert body["aborted"] is True and body["ok"] == 1 and body["failed"] == 2
    c = _conn(db_path)
    n = c.execute("SELECT COUNT(*) FROM asset_tag WHERE tag = '搬遷'").fetchone()[0]
    act = c.execute("SELECT action FROM activity_log WHERE path = '/api/asset-tags/batch'").fetchall()
    c.execute("DROP TRIGGER t_fault")
    c.commit()
    c.close()
    assert n == 1
    assert any("中途寫入失敗" in (a[0] or "") for a in act), "管理員要查得到這次失敗"
    r2 = client.post("/api/asset-tags/batch", json={"items": items, "tag": "搬遷"})
    assert r2.status_code == 200 and r2.json()["ok"] == 3 and r2.json()["aborted"] is False


# ---------------------------------------------------------------- 1163／1159 單筆編輯部分保存
def _fails(call):
    """故障注入：路由要嘛丟例外、要嘛回 5xx（看例外處理器怎麼包），兩種都算「這次沒成功」。"""
    try:
        r = call()
    except Exception:  # noqa: BLE001 - TestClient 預設把伺服器例外丟出來
        return
    assert r.status_code >= 500, r.status_code


def _trigger(db_path, sql):
    c = _conn(db_path)
    c.execute(sql)
    c.commit()
    c.close()


def test_1163_主機名寫入失敗_待異動與CIA稽核一起復原(tmp_path):
    client, db_path = _client(tmp_path)
    _insert_hardware(db_path, "CIA-1", "old-host", "10.99.2.1", asset_status="使用中")
    _trigger(db_path, "CREATE TRIGGER t_fault BEFORE UPDATE OF hostname ON hardware "
                      "BEGIN SELECT RAISE(ABORT, 'injected fault'); END")
    _fails(lambda: client.put("/api/assets/CIA-1", json={"fields": {"asset_status": "停用", "hostname": "new-host"}}))
    c = _conn(db_path)
    row = c.execute("SELECT hostname, pending_status, manual_updated_at FROM hardware WHERE asset_serial='CIA-1'").fetchone()
    try:
        n = c.execute("SELECT COUNT(*) FROM cia_pending_change").fetchone()[0]
    except sqlite3.OperationalError:
        n = 0
    c.execute("DROP TRIGGER t_fault")
    c.commit()
    c.close()
    assert tuple(row) == ("old-host", None, None)
    assert n == 0, "主機名沒改成，待異動稽核也不能留"
    r = client.put("/api/assets/CIA-1", json={"fields": {"asset_status": "停用", "hostname": "new-host"}})
    assert r.status_code == 200
    c = _conn(db_path)
    row = c.execute("SELECT hostname, pending_status, manual_updated_at FROM hardware WHERE asset_serial='CIA-1'").fetchone()
    n = c.execute("SELECT COUNT(*) FROM cia_pending_change").fetchone()[0]
    c.close()
    assert row[0] == "new-host" and row[1] == "停用" and row[2]
    assert n == 1


def test_1159_人工維護時間寫入失敗_欄位也不留(tmp_path):
    client, db_path = _client(tmp_path)
    _insert_hardware(db_path, "CIA-2", "h", "10.99.2.2", user_name="王小明")
    _trigger(db_path, "CREATE TRIGGER t_fault BEFORE UPDATE OF manual_updated_at ON hardware "
                      "BEGIN SELECT RAISE(ABORT, 'injected fault'); END")
    _fails(lambda: client.put("/api/assets/CIA-2", json={"fields": {"user_name": "李大華"}}))
    c = _conn(db_path)
    row = c.execute("SELECT user_name, manual_updated_at FROM hardware WHERE asset_serial='CIA-2'").fetchone()
    c.execute("DROP TRIGGER t_fault")
    c.commit()
    c.close()
    assert tuple(row) == ("王小明", None)
    assert client.put("/api/assets/CIA-2", json={"fields": {"user_name": "李大華"}}).status_code == 200


# ---------------------------------------------------------------- 1145／1149 人名合併
def test_1145_預覽後同筆數換了對象_執行回409不改(tmp_path):
    client, db_path = _client(tmp_path)
    _insert_hardware(db_path, "PM-A", "a", "10.99.3.1", user_name="李太名")
    _insert_hardware(db_path, "PM-B", "b", "10.99.3.2", user_name="李太明")
    pv = client.post("/api/data-quality/person-merge/preview", json={"from_name": "李太名", "to_name": "李太明"}).json()
    assert pv["total"] == 1 and pv["preview_token"]
    c = _conn(db_path)
    c.execute("UPDATE hardware SET user_name = '甲更正' WHERE asset_serial = 'PM-A'")
    c.execute("UPDATE hardware SET user_name = '李太名' WHERE asset_serial = 'PM-B'")
    c.commit()
    c.close()
    r = client.post("/api/data-quality/person-merge",
                    json={"from_name": "李太名", "to_name": "李太明", "preview_token": pv["preview_token"]})
    assert r.status_code == 409
    c = _conn(db_path)
    names = dict(c.execute("SELECT asset_serial, user_name FROM hardware").fetchall())
    c.close()
    assert names == {"PM-A": "甲更正", "PM-B": "李太名"}
    pv2 = client.post("/api/data-quality/person-merge/preview", json={"from_name": "李太名", "to_name": "李太明"}).json()
    r = client.post("/api/data-quality/person-merge",
                    json={"from_name": "李太名", "to_name": "李太明", "preview_token": pv2["preview_token"]})
    assert r.status_code == 200 and r.json()["total"] == 1


def test_1149_沒預覽直接送回409(tmp_path):
    client, db_path = _client(tmp_path)
    _insert_hardware(db_path, "PM-A", "a", "10.99.3.1", user_name="李太名")
    r = client.post("/api/data-quality/person-merge", json={"from_name": "李太名", "to_name": "李太明"})
    assert r.status_code == 409
    c = _conn(db_path)
    assert c.execute("SELECT user_name FROM hardware").fetchone()[0] == "李太名"
    c.close()


def test_1145_最後一欄寫入失敗整筆復原(tmp_path):
    client, db_path = _client(tmp_path)
    _insert_hardware(db_path, "PM-A", "a", "10.99.3.1", user_name="李太名", custodian="李太名")
    c = _conn(db_path)
    c.execute("INSERT INTO personnel (asset_serial, person_name) VALUES ('PM-A', '李太名')")
    c.execute("CREATE TRIGGER t_fault BEFORE UPDATE OF person_name ON personnel "
              "BEGIN SELECT RAISE(ABORT, 'injected fault'); END")
    c.commit()
    c.close()
    pv = client.post("/api/data-quality/person-merge/preview", json={"from_name": "李太名", "to_name": "李太明"}).json()
    assert pv["total"] == 3
    _fails(lambda: client.post("/api/data-quality/person-merge",
                               json={"from_name": "李太名", "to_name": "李太明", "preview_token": pv["preview_token"]}))
    c = _conn(db_path)
    hw = c.execute("SELECT user_name, custodian FROM hardware").fetchone()
    pn = c.execute("SELECT person_name FROM personnel").fetchone()[0]
    c.close()
    assert tuple(hw) == ("李太名", "李太名") and pn == "李太名"


# ---------------------------------------------------------------- 1151／1155／1157 資料品質
def _dq(db_path):
    import data_quality
    c = _conn(db_path)
    try:
        m = data_quality.measure(c)
        dims = {d["key"]: d for d in m["dimensions"]}
        return m, dims, (lambda k: data_quality.list_offenders(c, k)), c
    except Exception:
        c.close()
        raise


def test_1151_退役資產沒有觀測不算符合_不進分母(tmp_path):
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "R-1", "r1", "10.99.4.1", asset_status="停用")
    _insert_hardware(db_path, "R-2", "r2", None, asset_status="報廢")
    m, dims, _, c = _dq(db_path)
    c.close()
    assert dims["retired_alive"]["checked"] == 0
    assert m["score"] is None, "沒有任何證據時不能給 100 分"
    # 對照：該網段掃過、退役機近期仍在線 → 列異常且下鑽就是那一台
    c = _conn(db_path)
    c.execute("INSERT INTO scan_history (scan_time, hostname, ip, device_model, is_vm, segment, scan_ok) "
              "VALUES (?, 'r1', '10.99.4.1', 'X', 0, 'A', 1)", (_now(1),))
    c.commit()
    c.close()
    m, dims, off, c = _dq(db_path)
    rows = off("retired_alive")
    c.close()
    assert dims["retired_alive"]["checked"] == 1 and dims["retired_alive"]["bad"] == 1
    assert [r["asset_serial"] for r in rows] == ["R-1"]


def _acct(c, ip, serial, os_id, ver, seen, user):
    c.execute("INSERT INTO host_account (ip, asset_serial, username, os_id, os_version, last_seen) "
              "VALUES (?,?,?,?,?,?)", (ip, serial, user, os_id, ver, seen))


def test_1155_同IP別台序號的舊帳號資料不算這台的OS證據(tmp_path):
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "NEW-1", "u1", "10.99.5.1", asset_status="使用中", os="Ubuntu 22.04")
    c = _conn(db_path)
    _acct(c, "10.99.5.1", "OLD-9", "rhel", "8.6", "2020-01-01 00:00:00", "root")
    c.commit()
    c.close()
    m, dims, off, c = _dq(db_path)
    rows = off("os_match")
    c.close()
    assert dims["os_match"]["checked"] == 0 and rows == []
    c = _conn(db_path)
    _acct(c, "10.99.5.1", "NEW-1", "ubuntu", "22.04", _now(), "admin")
    c.commit()
    c.close()
    m, dims, off, c = _dq(db_path)
    c.close()
    assert dims["os_match"]["checked"] == 1 and dims["os_match"]["ok"] == 1


@pytest.mark.parametrize("order", ["ubuntu_first", "rhel_first"])
def test_1157_OS證據取最新一筆_不隨寫入順序變(tmp_path, order):
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "S-1", "s1", "10.99.6.1", asset_status="使用中", os="Ubuntu 22.04")
    c = _conn(db_path)
    rows = [("ubuntu", "22.04", _now(1), "a"), ("rhel", "8", "2020-01-01 00:00:00", "b")]
    if order == "rhel_first":
        rows.reverse()
    for os_id, ver, seen, user in rows:
        _acct(c, "10.99.6.1", "S-1", os_id, ver, seen, user)
    c.commit()
    c.close()
    m, dims, off, c = _dq(db_path)
    c.close()
    assert (dims["os_match"]["checked"], dims["os_match"]["ok"]) == (1, 1)


def test_1145_P3_rowid重用給別台_指紋要變(tmp_path):
    """審查 P3：預覽後某列被刪、同一個 rowid 被別台（同名）重用，筆數與 rowid 都沒變，指紋仍要不同。"""
    import data_quality
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "PM-A", "a", "10.99.3.1")
    _insert_hardware(db_path, "PM-B", "b", "10.99.3.2")
    c = _conn(db_path)
    c.execute("INSERT INTO personnel (id, asset_serial, person_name) VALUES (7, 'PM-A', '李太名')")
    c.commit()
    t1 = data_quality._merge_token(c, "李太名", "李太明")
    c.execute("DELETE FROM personnel WHERE id = 7")
    c.execute("INSERT INTO personnel (id, asset_serial, person_name) VALUES (7, 'PM-B', '李太名')")
    c.commit()
    t2 = data_quality._merge_token(c, "李太名", "李太明")
    with pytest.raises(data_quality.MergePreviewStale):
        data_quality.apply_person_merge(c, "李太名", "李太明", t1)
    assert c.execute("SELECT person_name FROM personnel").fetchone()[0] == "李太名"
    assert data_quality.apply_person_merge(c, "李太名", "李太明", t2)["total"] == 1
    c.close()
    assert t1 != t2


# ---------------------------------------------------------------- 1167 原始檔改名失敗
def test_1167_改名失敗保留上一份原始檔_不留暫存(tmp_path, monkeypatch):
    import import_export
    monkeypatch.setattr(import_export, "_dir_for", lambda s: tmp_path / "orig" / s)
    p1 = import_export.save_original("cia_excel", "a.xlsx", b"GOOD")
    assert p1.read_bytes() == b"GOOD"

    real = Path.replace

    def boom(self, target):
        raise OSError("injected rename fault")
    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        import_export.save_original("cia_excel", "b.xlsx", b"NEW")
    monkeypatch.setattr(Path, "replace", real)
    left = sorted(p.name for p in (tmp_path / "orig" / "cia_excel").iterdir())
    assert left == ["a.xlsx"]
    assert import_export.original_for("cia_excel").read_bytes() == b"GOOD"
    p2 = import_export.save_original("cia_excel", "b.xlsx", b"NEW")
    assert sorted(p.name for p in p2.parent.iterdir()) == ["b.xlsx"]


# ---------------------------------------------------------------- 1171 整台預覽換了登記
def test_1171_整台預覽後換登記_帶預覽清單送出回409(tmp_path):
    client, db_path = _client(tmp_path)
    _insert_hardware(db_path, "M-1", "mach", "10.99.7.1", asset_status="使用中")
    _insert_hardware(db_path, "M-2", "mach", "10.99.7.1", asset_status="使用中")
    pv = client.post("/api/assets/scope-preview", json={"serials": ["M-1"], "scope": "machine"}).json()
    seen = [r["asset_serial"] for r in pv["rows"]]
    assert sorted(seen) == ["M-1", "M-2"]
    c = _conn(db_path)
    c.execute("UPDATE hardware SET hostname = 'other', ip = '10.99.7.9' WHERE asset_serial = 'M-2'")
    _ = c.execute("INSERT INTO hardware (asset_serial, hostname, ip, asset_status) VALUES ('M-3','mach','10.99.7.1','使用中')")
    c.commit()
    c.close()
    body = {"serials": ["M-1"], "status": "停用", "reason": "現場確認", "scope": "machine", "expected_serials": seen}
    assert client.post("/api/assets/batch-status", json=body).status_code == 409
    c = _conn(db_path)
    n = c.execute("SELECT COUNT(*) FROM hardware WHERE pending_status IS NOT NULL").fetchone()[0]
    c.close()
    assert n == 0
    pv = client.post("/api/assets/scope-preview", json={"serials": ["M-1"], "scope": "machine"}).json()
    body["expected_serials"] = [r["asset_serial"] for r in pv["rows"]]
    r = client.post("/api/assets/batch-status", json=body)
    assert r.status_code == 200 and sorted(r.json()["updated"]) == ["M-1", "M-3"]


def test_1171_P3_勾到已刪除序號_整台照預覽送出不會永遠409(tmp_path):
    """審查 P3：預覽只列 hardware 還在的；勾選裡混一筆已刪序號時，比對要先濾掉查無的，查無的回 not_found。"""
    client, db_path = _client(tmp_path)
    _insert_hardware(db_path, "M-1", "mach", "10.99.7.1", asset_status="使用中")
    _insert_hardware(db_path, "M-2", "mach", "10.99.7.1", asset_status="使用中")
    sel = ["M-1", "GONE-9"]
    pv = client.post("/api/assets/scope-preview", json={"serials": sel, "scope": "machine"}).json()
    seen = [r["asset_serial"] for r in pv["rows"]]
    assert sorted(seen) == ["M-1", "M-2"]
    r = client.post("/api/assets/batch-status", json={"serials": sel, "status": "停用", "reason": "現場確認",
                                                       "scope": "machine", "expected_serials": seen})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["updated"]) == ["M-1", "M-2"] and r.json()["not_found"] == ["GONE-9"]


def test_1171_P3_展開與比對在寫入鎖裡(tmp_path):
    """審查 P3（TOCTOU）：比對時已持有寫入鎖——另一個連線此刻要寫會被擋（database is locked）。"""
    import cia_pending
    import manage_state
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    _insert_hardware(db_path, "M-1", "mach", "10.99.7.1", asset_status="使用中")
    main = _conn(db_path)
    seen_lock = {}
    real = manage_state.expand_to_machines

    def spy(conn, serials):
        o = sqlite3.connect(str(db_path), timeout=0)
        try:
            o.execute("UPDATE hardware SET hostname = hostname")
            seen_lock["blocked"] = False
        except sqlite3.OperationalError:
            seen_lock["blocked"] = True
        finally:
            o.close()
        return real(conn, serials)

    manage_state.expand_to_machines = spy
    try:
        cia_pending.batch_set_status(main, ["M-1"], "停用", "現場確認", "t", scope="machine", expected_serials=["M-1"])
    finally:
        manage_state.expand_to_machines = real
        main.close()
    assert seen_lock == {"blocked": True}


# ---------------------------------------------------------------- 1185／1187 欄位對應
@pytest.fixture
def fm_env(monkeypatch, tmp_path):
    real = excel_import.MAPPING_PATH.read_bytes()
    mp = tmp_path / "field_mapping.json"
    mp.write_bytes(real)
    monkeypatch.setattr(excel_import, "MAPPING_PATH", mp)
    original = api.MAPPING_PATH
    api.MAPPING_PATH = mp
    client, db_path = _client(tmp_path)
    try:
        yield client, mp
    finally:
        api.MAPPING_PATH = original


def _strip(fm):
    return {k: v for k, v in fm.items() if not k.startswith("_")}


def test_1185_拿舊版本存檔回409_不恢復別人停用的欄位(fm_env):
    client, mp = fm_env
    a = client.get("/api/import/field-mapping").json()
    b = client.get("/api/import/field-mapping").json()
    host_h = next(h for h, f in a["硬體"].items() if f == "hostname")
    ma = _strip(a)
    ma["硬體"] = {**ma["硬體"], host_h: "不匯入"}
    assert client.put("/api/import/field-mapping", json={"mapping": ma, "base_version": a["_version"]}).status_code == 200
    after_first = mp.read_bytes()
    mb = _strip(b)
    r = client.put("/api/import/field-mapping", json={"mapping": mb, "base_version": b["_version"]})
    assert r.status_code == 409
    assert mp.read_bytes() == after_first
    cur = client.get("/api/import/field-mapping").json()
    assert host_h not in cur["硬體"]
    mc = _strip(cur)
    assert client.put("/api/import/field-mapping", json={"mapping": mc, "base_version": cur["_version"]}).status_code == 200


def test_1187_資產序號不能設為不匯入(fm_env):
    client, mp = fm_env
    before = mp.read_bytes()
    fm = client.get("/api/import/field-mapping").json()
    for sheet in ("硬體", "人員", "軟體"):
        m = _strip(fm)
        ser_h = next(h for h, f in m[sheet].items() if f == "asset_serial")
        m[sheet] = {**m[sheet], ser_h: "不匯入"}
        r = client.put("/api/import/field-mapping", json={"mapping": m})
        assert r.status_code == 400, sheet
        assert mp.read_bytes() == before
    # 舊頁把 GET 原封送回（含 _version 字串）不能 422
    assert client.put("/api/import/field-mapping", json={"mapping": fm}).status_code == 200


# ---------------------------------------------------------------- 1189 來源摘要日期
def test_1189_有成功紀錄時來源摘要以紀錄為準(tmp_path, monkeypatch):
    import import_export
    from routers import imports as r_imp
    _client(tmp_path)
    db_path = Path(tmp_path) / "test.db"
    c = _conn(db_path)
    db.create_import_log(c, imported_by="t", hardware_count=1, personnel_count=0, software_count=0,
                         error_count=0, source="cia_excel", file_name="CIA_2026-01-15.xlsx")
    c.execute("UPDATE import_log SET imported_at = '2026-01-15 09:00:00'")
    c.commit()
    orig = tmp_path / "CIA_2026-02-20.xlsx"
    orig.write_bytes(b"x")
    got = r_imp._last_import(c, "cia_excel", orig)
    none_log = r_imp._last_import(c, "business_system", orig)
    c.close()
    assert got["last_imported_at"].startswith("2026-01-15")
    assert got["data_date"] == "2026-01-15"
    assert none_log["last_imported_at"] is not None and none_log["data_date"] == "2026-02-20", "沒紀錄才看留存檔"
