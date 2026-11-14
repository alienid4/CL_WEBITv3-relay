"""2026-10-26 機櫃畫面：一個實體箱子＝一台（管理口併進主機、同箱多筆、疑似登記錯誤、一格多段 U），
拖拉確認框邊打邊驗、搬移紀錄與復原。全部假資料。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import overlap_queue as oq  # noqa: E402
import rack_layout as rl  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, serial, host, ip, rack, model=None, os=None, hw_serial=None):
    db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location="01_甲機房",
                       rack_no=rack, device_model=model, os=os, hw_serial=hw_serial)


def _tile(conn, rack):
    return next(t for r in rl.datacenter(conn)["rooms"] for t in r["racks"] if t["rack"] == rack)


def _key(conn, serial):
    return next(x["key"] for x in rl.machines(conn) if x["asset_serial"] == serial)


def test_管理口後綴規則():
    assert rl.mgmt_suffix_base("fake-oda-t1-ilom") == "fake-oda-t1"
    assert rl.mgmt_suffix_base("web01_iDRAC") == "web01"
    assert rl.mgmt_suffix_base("db01-xcc") == "db01" and rl.mgmt_suffix_base("db01-bmc") == "db01"
    assert rl.mgmt_suffix_base("silo01") is None and rl.mgmt_suffix_base("ilom") is None
    assert rl.mgmt_kind({"name": "a-ilom"}) == "ILOM" and rl.mgmt_kind({"name": "x", "os": "iDRAC9"}) == "iDRAC"
    assert rl.is_mgmt_record({"name": "fake-oda-t1-ilom"})
    # OOB 交換器／防火牆／PDU 是實體設備，不是管理口
    assert not rl.is_mgmt_record({"name": "XX-B08-OOB-9300-01(SW1)", "model": "Cisco C9300-48T-E"})
    assert not rl.is_mgmt_record({"name": "XX-M03-OOB-FG101-FW1", "model": "Fortinet FG-101F"})
    assert not rl.is_mgmt_record({"name": "pdu01", "model": "Raritan", "asset_names": ["硬體遠端管理"]})


def test_管理口用主機名登記_或服務標籤對得上_U不同也併(tmp_path):
    conn = _conn(tmp_path)
    try:
        _hw(conn, "E-1", "esxfake-105", "10.99.2.5", "M7/20-21U", "DELL R750", "VMware ESXi 8", hw_serial="TAG0105")
        _hw(conn, "E-2", "esxfake-105", "10.99.2.105", "M7/24U", "DELL R750", "Idrac 7.00")          # 名字同主機、OS 是 iDRAC
        _hw(conn, "E-3", "esxfake-106", "10.99.2.6", "M7/10-11U", "DELL R750", "VMware ESXi 8", hw_serial="TAG0106")
        _hw(conn, "E-4", "APPOOB-106-TAG0106", "10.99.2.106", "M7/30U", "DELL R750")               # 標籤對得上、U 不同
        _hw(conn, "E-5", "XX-OOB-9200L-01(SW1)", "10.99.2.200", "M7/40U", "Cisco C9200L-48T-4X-E")  # OOB 交換器：一台
        conn.commit()
        by = {x["name"] + x["asset_serial"]: x for x in rl.rack_detail(conn, "甲機房", "M7")["devices"]}
        assert by["esxfake-105E-2"]["mgmt_of"] == by["esxfake-105E-1"]["key"]
        assert by["APPOOB-106-TAG0106E-4"]["mgmt_of"] == by["esxfake-106E-3"]["key"]
        assert by["XX-OOB-9200L-01(SW1)E-5"]["box_role"] == "box"
        t = _tile(conn, "M7")
        assert t["device_count"] == 3 and t["breakdown"]["port"] == 2
    finally:
        conn.close()


def _seed_oda(conn):
    # ODA 一套：兩台伺服器同一段 U、各一個 ILOM（ILOM 登記的 U 不一定跟本體一樣）
    _hw(conn, "O-1", "fake-oda-t1", "10.99.3.1", "B3/29-34U", "Oracle ODA X9-2-HA")
    _hw(conn, "O-2", "fake-oda-t1-ilom", "10.99.4.1", "B3/33U", "Oracle ODA X9-2-HA")
    _hw(conn, "O-3", "fake-oda-t2", "10.99.3.2", "B3/29-34U", "Oracle ODA X9-2-HA")
    _hw(conn, "O-4", "fake-oda-t2-ilom", "10.99.4.2", "B3/29-34U", "Oracle ODA X9-2-HA")
    _hw(conn, "O-5", "lonely-ilom", "10.99.4.9", "B3/10U", "X")                       # 找不到本體
    _hw(conn, "O-6", "other-host-ilom", "10.99.4.8", "B4/10U", "X")                   # 本體在別櫃 → 不併
    _hw(conn, "O-7", "other-host", "10.99.3.8", "B3/1U", "X")
    conn.commit()


def test_ILOM併進主機_不算台不佔U_找不到本體標出(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_oda(conn)
        d = rl.rack_detail(conn, "甲機房", "B3")
        by = {x["name"]: x for x in d["devices"]}
        assert by["fake-oda-t1-ilom"]["mgmt_of"] == by["fake-oda-t1"]["key"]          # U 不同也併（主機名規則）
        assert by["fake-oda-t2-ilom"]["mgmt_of"] == by["fake-oda-t2"]["key"]
        assert by["fake-oda-t1"]["mgmt"][0]["kind"] == "ILOM" and by["fake-oda-t1"]["mgmt"][0]["rule"] == "hostname"
        assert not by["fake-oda-t1"].get("merged_into") and not by["fake-oda-t2"].get("merged_into")   # 兩台伺服器
        assert by["lonely-ilom"]["orphan_port"] is True and by["lonely-ilom"]["mgmt_of"] is None
        assert by["other-host"]["mgmt"] == []                                          # 別櫃的 ILOM 不併過來
        t = _tile(conn, "B3")
        assert t["device_count"] == 4 and t["record_count"] == 6                       # t1、t2、other-host＋找不到本體的照算
        assert t["breakdown"]["port"] == 2 and t["breakdown"]["orphan"] == 1
        assert t["power"]["known_count"] + t["power"]["unknown_count"] == t["power"]["active_count"] == 6
        # 33U 的 ILOM 不佔 U：只有 29-34（6U）＋10U（找不到本體的照樣佔）＋1U
        assert t["power"]["used_u"] == 8
        assert _tile(conn, "B4")["breakdown"]["orphan"] == 1
    finally:
        conn.close()


def test_查ILOM的IP找得到主機_A4與標籤一箱一列(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_oda(conn)
        res = rl.search(conn, "10.99.4.1")
        names = {x["name"] for x in res["angles"]["host"]["matches"]}
        assert "fake-oda-t1" in names
        sh = rl.print_sheets(conn, "甲機房", "B3")["sheets"][0]
        rows = {r["name"]: r for r in sh["rows"]}
        assert set(rows) == {"fake-oda-t1", "fake-oda-t2", "lonely-ilom", "other-host"}
        assert "管理 IP：10.99.4.1（ILOM）" in rows["fake-oda-t1"]["marks"]
        assert "管理介面・找不到本體" in rows["lonely-ilom"]["marks"]
        assert sh["device_count"] == 4 and sh["record_count"] == 6                     # A4：一箱一列（找不到本體的也一列）
        items = {x["name"]: x for x in rl.label_items(conn, "甲機房", "B3")["items"]}
        assert "fake-oda-t1-ilom" not in items and items["fake-oda-t1"]["mgmt"] == "管理 IP：10.99.4.1（ILOM）"
    finally:
        conn.close()


def test_同一箱多筆_角色_台數只算一次_疑似登記錯誤不併(tmp_path):
    conn = _conn(tmp_path)
    try:
        _hw(conn, "F-1", "FakeStore-FS5300", "10.99.5.1", "B2/33U", "IBM FS5300", hw_serial="78F0000")
        _hw(conn, "F-2", "FakeStore-FS5300", "10.99.5.2", "B2/33U", "IBM FS5300")
        _hw(conn, "F-3", "FakeStore-FS5300", "10.99.5.3", "B2/33U", "IBM FS5300")
        _hw(conn, "L-1", "fakelpar21", "10.99.6.1", "B2/7-10U", "IBM S914", "OS400 V7R4")
        _hw(conn, "L-2", "fakelpar23", "10.99.6.2", "B2/7-10U", "IBM S914", "OS400 V7R4")
        _hw(conn, "S-1", "app-a", "10.99.6.10", "B2/28-31U", "DELL R750")
        _hw(conn, "S-2", "app-b", "10.99.6.11", "B2/12-15U", "DELL R750")
        for i in range(10):                                                            # 3-4U 登記 10 台
            _hw(conn, f"X-{i}", f"dl{i:02d}", f"10.99.7.{i + 1}", "M9/3-4U", "HPE DL380")
        _hw(conn, "X-99", "dl99", "10.99.7.99", "M9/20U", "HPE DL380")
        conn.commit()
        d = rl.rack_detail(conn, "甲機房", "B2")
        by = {x["name"]: x for x in d["devices"]}
        heads = [x for x in d["devices"] if x.get("merged")]
        assert len(heads) == 2
        fs = next(x for x in heads if x["name"] == "FakeStore-FS5300")
        assert {m["role"] for m in fs["merged"]} == {"多管理 IP"} and len(fs["merged"]) == 2
        lp = next(x for x in heads if x["name"].startswith("fakelpar"))
        assert lp["merged"][0]["role"] == "分割區 LPAR"
        assert not by["app-a"].get("merged_into") and not by["app-b"].get("merged_into")
        t = _tile(conn, "B2")
        assert t["device_count"] == 4 and t["record_count"] == 7 and t["breakdown"]["member"] == 3
        assert d["box_count"] == 4
        m9 = rl.rack_detail(conn, "甲機房", "M9")
        assert not any(x.get("merged") or x.get("merged_into") for x in m9["devices"])
        assert len(m9["suspect"]) == 1 and len(m9["suspect"][0]["keys"]) == 10 and "3-4U" in m9["suspect"][0]["reason"]
        t9 = _tile(conn, "M9")
        assert t9["device_count"] == 11 and t9["breakdown"]["suspect"] == 10           # 各算一台、列出來
    finally:
        conn.close()


def test_一格多段U_分開畫_佔U(tmp_path):
    conn = _conn(tmp_path)
    try:
        _hw(conn, "M-1", "multi01", "10.99.8.1", "M10/27U、30-31U", "X")
        conn.commit()
        m = next(x for x in rl.machines(conn) if x["asset_serial"] == "M-1")
        assert (m["u_start"], m["u_end"]) == (27, 27) and m["extra_ranges"] == [[30, 31]]
        t = _tile(conn, "M10")
        assert t["power"]["used_u"] == 3 and t["device_count"] == 1
        d = rl.rack_detail(conn, "甲機房", "M10")["devices"][0]
        assert d["extra_ranges"] == [[30, 31]]
    finally:
        conn.close()


def _seed_move(conn):
    _hw(conn, "A-1", "app01", "10.99.6.1", "B10/20U", "DELL R750")
    _hw(conn, "A-2", "app02", "10.99.6.2", "B11/10U", "DELL R750")
    _hw(conn, "A-3", "app03", "10.99.6.3", "B11/30-31U", "DELL R750")
    _hw(conn, "A-4", "app03-idrac", "10.99.6.4", "B11/30U", "DELL R750")
    conn.commit()


def test_確認框即時驗證(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_move(conn)
        k = _key(conn, "A-2")
        r = rl.move_check(conn, k, "甲機房", "B10", 20, 1)
        assert r["ok"] is False and r["kind"] == "collision" and r["collisions"][0]["name"] == "app01"
        assert rl.move_check(conn, k, "甲機房", "Z9", 1, 1)["kind"] == "unknown_rack"
        assert rl.move_check(conn, k, "甲機房", "B10", 42, 2)["kind"] == "out_of_range"
        assert rl.move_check(conn, k, "甲機房", "B11", 10, 1)["kind"] == "no_change"
        ok = rl.move_check(conn, k, "甲機房", "B10", 5, 2)
        assert ok["ok"] is True and (ok["u_start"], ok["u_end"]) == (5, 6)
        # 管理口不佔 U：搬到跟 idrac 同一格不算撞到（但撞到本體 app03 要擋）
        assert rl.move_check(conn, k, "甲機房", "B11", 30, 1)["collisions"][0]["name"] == "app03"
        assert conn.execute("SELECT COUNT(*) FROM rack_position_override").fetchone()[0] == 0   # 驗證不寫入
    finally:
        conn.close()


def test_搬移紀錄_復原回到搬之前(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_move(conn)
        k3 = _key(conn, "A-3")
        r = rl.move_box(conn, k3, "甲機房", "B10", 1, 2, "拖拉更正", "alice")
        assert set(r["moved"]) == {"app03", "app03-idrac"}                            # 管理口跟著搬
        mv = rl.recent_moves(conn, "甲機房", "B11")
        assert mv[0]["id"] == r["move_id"] and mv[0]["from_text"] == "B11／30-31U" and mv[0]["to_text"] == "B10／1-2U"
        out = rl.undo_move(conn, r["move_id"], "alice")
        assert out["undone"] and out["count"] == 2
        assert conn.execute("SELECT COUNT(*) FROM rack_position_override").fetchone()[0] == 0   # 回 CIA 位置
        m = next(x for x in rl.machines(conn) if x["key"] == k3)
        assert (m["rack"], m["u_start"], m["u_end"]) == ("B11", 30, 31)
        assert rl.recent_moves(conn, "甲機房")[0]["undone"] is True
        with pytest.raises(ValueError):
            rl.undo_move(conn, r["move_id"], "alice")                                  # 不能復原兩次
        acts = [x["action"] for x in rl.position_audit(conn, k3)]
        assert acts.count("undo_move") == 1 and acts.count("set") == 1
        # 搬之前就有位置更正 → 復原回那一筆更正（不是回 CIA）
        k2 = _key(conn, "A-2")
        rl.move_box(conn, k2, "甲機房", "B11", 5, 1, "第一次", "alice")
        r2 = rl.move_box(conn, k2, "甲機房", "B10", 10, 1, "第二次", "alice")
        rl.undo_move(conn, r2["move_id"], "alice")
        m = next(x for x in rl.machines(conn) if x["key"] == k2)
        assert (m["rack"], m["u_start"]) == ("B11", 5)
        assert conn.execute("SELECT note FROM rack_position_override WHERE key = ?", (k2,)).fetchone()[0] == "第一次"
    finally:
        conn.close()


def test_之後又改過_要確認才復原(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed_move(conn)
        k2 = _key(conn, "A-2")
        r1 = rl.move_box(conn, k2, "甲機房", "B11", 5, 1, "一", "alice")
        rl.move_box(conn, k2, "甲機房", "B11", 7, 1, "二", "bob")
        with pytest.raises(rl.MoveChanged) as e:
            rl.undo_move(conn, r1["move_id"], "alice")
        assert e.value.changes
        assert conn.execute("SELECT u_start FROM rack_position_override WHERE key = ?", (k2,)).fetchone()[0] == 7   # 沒動
        rl.undo_move(conn, r1["move_id"], "alice", force=True, note="確認")
        assert conn.execute("SELECT COUNT(*) FROM rack_position_override WHERE key = ?", (k2,)).fetchone()[0] == 0
        # 原位置被別台佔了 → 也要確認
        k1 = _key(conn, "A-1")
        r3 = rl.move_box(conn, k1, "甲機房", "B10", 25, 1, "搬走", "alice")
        rl.move_box(conn, k2, "甲機房", "B10", 20, 1, "搬進原位", "bob")
        with pytest.raises(rl.MoveChanged) as e:
            rl.undo_move(conn, r3["move_id"], "alice")
        assert any("app02" in c["what"] for c in e.value.changes)
    finally:
        conn.close()


def test_復原API_管理員_守門(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from routers import overlaps as ro

    conn = _conn(tmp_path)
    try:
        _seed_move(conn)
        r = rl.move_box(conn, _key(conn, "A-2"), "甲機房", "B11", 5, 1, "一", "alice")
        req = type("R", (), {"headers": {}, "client": type("C", (), {"host": "10.99.0.9"})()})()
        monkeypatch.setattr(ro, "_can_edit_nav", lambda s, c: False)
        with pytest.raises(HTTPException) as e:
            ro.rack_move_undo_endpoint(r["move_id"], ro.MoveUndoBody(), req, None, conn)
        assert e.value.status_code == 403
        monkeypatch.setattr(ro, "_can_edit_nav", lambda s, c: True)
        called = []
        monkeypatch.setattr(ro, "_guard", lambda c, rq: called.append(1))
        out = ro.rack_move_undo_endpoint(r["move_id"], ro.MoveUndoBody(), req, None, conn)
        assert out["undone"] and called == [1]                                         # 有過管理密碼守門
        with pytest.raises(HTTPException) as e:
            ro.rack_move_undo_endpoint(r["move_id"], ro.MoveUndoBody(), req, None, conn)
        assert e.value.status_code == 400
    finally:
        conn.close()


def test_位置不明的設備可以拖進空的U位_也能復原(tmp_path):
    """2026-10-28：只知道在這櫃、沒填 U 的設備，從「位置不明」清單拖進空 U 位（走同一個確認、稽核、復原）。"""
    conn = _conn(tmp_path)
    try:
        _seed_move(conn)
        _hw(conn, "A-9", "app09", "10.99.6.9", "B11", "DELL R750")            # 只填櫃、沒填 U
        conn.commit()
        k9 = _key(conn, "A-9")
        m0 = next(x for x in rl.machines(conn) if x["key"] == k9)
        assert m0["kind"] == "rack" and not m0["u_start"]
        # 驗證不寫入；撞到別台要擋；放空位可以
        assert rl.move_check(conn, k9, "甲機房", "B11", 10, 1)["kind"] == "collision"
        ok = rl.move_check(conn, k9, "甲機房", "B11", 12, 2)
        assert ok["ok"] is True and (ok["u_start"], ok["u_end"]) == (12, 13) and ok["count"] == 1
        assert conn.execute("SELECT COUNT(*) FROM rack_position_override").fetchone()[0] == 0
        r = rl.move_box(conn, k9, "甲機房", "B11", 12, 2, "現場拖進去", "alice")
        assert r["moved"] == ["app09"] and (r["u_start"], r["u_end"]) == (12, 13)
        m1 = next(x for x in rl.machines(conn) if x["key"] == k9)
        assert (m1["rack"], m1["u_start"], m1["u_end"]) == ("B11", 12, 13)
        assert rl.recent_moves(conn, "甲機房", "B11")[0]["from_text"] == "B11／"       # 搬之前沒有 U
        acts = [x["action"] for x in rl.position_audit(conn, k9)]
        assert acts.count("set") == 1                                                    # 有稽核紀錄
        rl.undo_move(conn, r["move_id"], "alice")
        m2 = next(x for x in rl.machines(conn) if x["key"] == k9)
        assert not m2["u_start"]                                                         # 復原回「位置不明」
        # 完全沒登記在機櫃裡的，仍然不能拖（走「更正位置」）
        _hw(conn, "A-8", "app08", "10.99.6.8", "")
        conn.commit()
        assert rl.move_check(conn, _key(conn, "A-8"), "甲機房", "B11", 12, 1)["kind"] == "bad_input"
    finally:
        conn.close()
