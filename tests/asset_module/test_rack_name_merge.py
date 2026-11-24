"""2.043.13／.14（D3）：同一個實體櫃的兩種寫法合一——CIA「1-1」＝Excel「A1」，以 Excel 為主（docs/ui-v2/機櫃差異待辦_規格_2026-11-04.md）。
全站顯示 A1、台數不重複、CIA 原始資料不改；只有「套用」或管理員設定才生效；位置更正／預留／重疊決策用同一個實體櫃鍵；
verify_numbers 的「同一實體櫃合一」不變式。全部假資料、IP 用 10.99.x。
2.043.14 依 GPT 獨立審查補「會紅」的測試：只預覽不生效、對照改了舊 override 跟著走、舊名預留擋得住搬移、舊名決策套得上／套不上會標出、
合櫃後判成同箱會被 verify 列出、首次位置更正記 CIA 原名、每日快照換名不算位置改變。"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import rack_layout  # noqa: E402
from test_rack_excel_import import ROOM, _client, _hw, make_xlsx  # noqa: E402

XL = {"A1": [(26, 27, "Riverbed 10.99.9.21")]}


def _setup(tmp, monkeypatch):
    c, db_path = _client(tmp, monkeypatch)
    conn = db.get_connection(db_path)
    try:
        _hw(conn, "R-1", "riverbed", "10.99.9.21", "1-1(A1)/26-27U", model="Riverbed 3800")
        _hw(conn, "R-2", "apic", "10.99.9.22", "1-1/10U", model="APIC")
        _hw(conn, "R-3", "a1host", "10.99.9.23", "A1/30U")              # 有人直接寫 A1
        _hw(conn, "R-4", "other", "10.99.9.24", "1-2/5U")               # 別的櫃，不受影響
        conn.commit()
    finally:
        conn.close()
    return c, db_path


def _preview(c):
    return c.post("/api/racks/excel-import/preview", params={"room": ROOM},
                  files={"file": ("x.xlsx", make_xlsx(XL, height=42), "application/octet-stream")}).json()


def _apply(c):
    pv = _preview(c)
    return c.post("/api/racks/excel-import/apply", json={"run_id": pv["run_id"], "row_ids": [pv["rows"][0]["id"]]}).json()


def _racks(db_path):
    conn = db.get_connection(db_path)
    try:
        return {m["asset_serial"]: m for m in rack_layout._machines(conn, read_only=True) if m["asset_serial"].startswith("R-")}
    finally:
        conn.close()


def test_只預覽不生效_按套用才換名(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _setup(tmp, monkeypatch)
        _preview(c)
        by = _racks(db_path)
        assert by["R-1"]["rack"] == "1-1" and by["R-3"]["rack"] == "A1", "只做預覽、沒按套用：畫面不變（GPT 審查 1）"
        assert c.get("/api/racks/name-map").json()["effective"] == {}
        assert c.get("/api/racks/name-map").json()["derived"] == {f"{ROOM}/1-1": "A1"}, "推得的只是建議"
        out = _apply(c)
        assert "rack_names" in out or out.get("applied") is not None
        by = _racks(db_path)
        assert by["R-1"]["rack"] == "A1" and by["R-1"]["rack_cia"] == "1-1"
        assert by["R-2"]["rack"] == "A1", "同一櫃只寫 1-1 的也一起換"
        assert by["R-3"]["rack"] == "A1" and by["R-3"]["rack_cia"] is None
        assert by["R-4"]["rack"] == "1-2"
        assert (by["R-1"]["u_start"], by["R-1"]["u_end"]) == (26, 27)
        conn = db.get_connection(db_path)
        try:
            assert conn.execute("SELECT rack_no FROM hardware WHERE asset_serial = 'R-2'").fetchone()[0] == "1-1/10U", "CIA 原始資料不改"
            dc = rack_layout.datacenter(conn)
            racks = [t["rack"] for r in dc["rooms"] if r["room"] == ROOM for t in r["racks"]]
            assert racks.count("A1") == 1 and "1-1" not in racks
        finally:
            conn.close()
        det = c.get("/api/racks/detail", params={"room": ROOM, "rack": "1-1"}).json()
        assert det["rack"] == "A1" and det["rack_cia_names"] == ["1-1"]
        assert {d["asset_serial"] for d in det["devices"]} >= {"R-1", "R-2", "R-3"}


def _set_map(c, items):
    r = c.put("/api/racks/name-map", json={"items": items})
    assert r.status_code == 200, r.text
    return r.json()


def test_位置更正存實體櫃鍵_對照改了跟著走_首次更正記CIA原名(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _setup(tmp, monkeypatch)
        _set_map(c, {f"{ROOM}/1-1": "A1"})
        conn = db.get_connection(db_path)
        try:
            key = next(m["key"] for m in rack_layout._machines(conn, read_only=True) if m["asset_serial"] == "R-2")
            rack_layout.set_position(conn, key, ROOM, "A1", 12, 1, "現場更正", "admin")      # 用畫面櫃名寫
            row = conn.execute("SELECT rack, cia_rack FROM rack_position_override WHERE key = ?", (key,)).fetchone()
            assert row["rack"] == "1-1", "寫入存實體櫃鍵（系統櫃名），不存畫面櫃名（GPT 審查 2）"
            assert row["cia_rack"] == "1-1", "首次更正的 CIA 基準記 CIA 原名，不是 A1（GPT 審查 6）"
            audit = json.loads(conn.execute("SELECT before_json FROM rack_position_audit WHERE key = ? ORDER BY id DESC", (key,)).fetchone()[0])
            assert audit["rack"] == "1-1"
        finally:
            conn.close()
        assert _racks(db_path)["R-2"]["rack"] == "A1" and _racks(db_path)["R-2"]["u_start"] == 12
        _set_map(c, {f"{ROOM}/1-1": "XB"})             # 管理員改對照：1-1 改叫 XB
        by = _racks(db_path)
        assert by["R-2"]["rack"] == "XB" and by["R-1"]["rack"] == "XB", "有位置更正的那台也跟著到新名字，不會被拆在舊名 A1"


def test_舊名預留_換名後照樣出現_擋得住搬移與重複預留(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _setup(tmp, monkeypatch)
        conn = db.get_connection(db_path)
        try:
            rack_layout.reserve(conn, ROOM, "1-1", 20, 2, "新主機", "2099-12-31", "admin")       # 換名前用舊名預留
        finally:
            conn.close()
        _set_map(c, {f"{ROOM}/1-1": "A1"})
        det = c.get("/api/racks/detail", params={"room": ROOM, "rack": "A1"}).json()
        assert any(r["label"] == "新主機" for r in det["reservations"]), "舊名的預留在 A1 圖上看得到（GPT 審查 3）"
        conn = db.get_connection(db_path)
        try:
            key = next(m["key"] for m in rack_layout.machines(conn) if m["asset_serial"] == "R-4")
            chk = rack_layout.move_check(conn, key, ROOM, "A1", 20, 1)
            assert not chk["ok"] and any("預留" in h.get("name", "") for h in chk.get("collisions", [])) or "預留" in chk.get("error", ""), chk
            res = rack_layout.reserve(conn, ROOM, "A1", 21, 1, "又一台", "2099-12-31", "admin")
            assert any("預留" in h["name"] for h in res.get("collisions", res.get("hit", []))), "重複預留同一段 U 會警告"
            st = conn.execute("SELECT rack FROM rack_reservation WHERE label = '又一台'").fetchone()[0]
            assert st == "1-1", "新的預留也存實體櫃鍵"
        finally:
            conn.close()


def test_舊名重疊決策_換名後套得上_套不上會標出來(monkeypatch):
    import overlap_queue
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _setup(tmp, monkeypatch)
        conn = db.get_connection(db_path)
        try:
            ms = rack_layout._machines(conn, read_only=True)
            k1 = next(m["key"] for m in ms if m["asset_serial"] == "R-1")
            k2 = next(m["key"] for m in ms if m["asset_serial"] == "R-2")
            k4 = next(m["key"] for m in ms if m["asset_serial"] == "R-4")
            overlap_queue.ensure_table(conn)
            for rk, keys in (("1-1", [k1, k2]), ("1-1", [k4, k1])):
                conn.execute("INSERT INTO rack_overlap_decision (room, rack, keys_json, action, detail_json, decided_by, decided_at) "
                             "VALUES (?,?,?,?,?,?,?)", (ROOM, rk, json.dumps(sorted(keys)), "accept_half", "null", "admin", "2026-11-01 00:00:00"))
            conn.commit()
        finally:
            conn.close()
        _set_map(c, {f"{ROOM}/1-1": "A1"})
        conn = db.get_connection(db_path)
        try:
            acts = overlap_queue.active_decisions(conn)
            assert {d["rack"] for d in acts} == {"A1"} and {d["rack_stored"] for d in acts} == {"1-1"}, "舊名決策換成畫面櫃名後照樣比得到（GPT 審查 4）"
            g = overlap_queue.groups(conn, ROOM, "A1")
            stale = g["rack_stale"]
            assert len(stale) == 0 or all(k4 in s["keys"] for s in stale)
            g_all = overlap_queue.groups(conn)
            assert not any(set(s["keys"]) == {k1, k2} for s in g_all["rack_stale"]), "還套得上的不列 stale"
        finally:
            conn.close()
        # 把 R-1、R-2 都搬到別櫃：那筆決定記的 A1 已經沒有它的設備 → 標 stale，不靜默失效
        conn = db.get_connection(db_path)
        try:
            for k in (k1, k2):
                rack_layout.set_position(conn, k, ROOM, "1-2", 30 if k == k1 else 33, 1, "搬走", "admin")
            g_all = overlap_queue.groups(conn)
            assert any(set(s["keys"]) == {k1, k2} and s["text"] for s in g_all["rack_stale"])
        finally:
            conn.close()


def test_合櫃後判成同箱_verify會列出來(monkeypatch):
    import verify_numbers as vn
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _client(tmp, monkeypatch)
        conn = db.get_connection(db_path)
        try:
            # 同一台登記兩次：一筆寫 1-1、一筆寫 A1，同主機名、同機型、同 U → 合櫃後在同一櫃才會被判成同一箱
            db.insert_hardware(conn, asset_serial="D-1", hostname="dup01", ip="10.99.9.31", physical_location="02_內湖機房",
                               rack_no="1-1(A1)/15U", asset_status="使用中", device_model="Dell R760", hw_serial="SNX1")
            db.insert_hardware(conn, asset_serial="D-2", hostname="dup01", ip="10.99.9.32", physical_location="02_內湖機房",
                               rack_no="A1/15U", asset_status="使用中", device_model="Dell R760", hw_serial="SNX1")
            conn.commit()
        finally:
            conn.close()
        _set_map(c, {f"{ROOM}/1-1": "A1"})
        conn = db.get_connection(db_path)
        try:
            vn.RESULTS.clear()
            vn.check_rack_name_merge(conn)
            rs = {r["name"]: r for r in vn.RESULTS if r["page"] == "機櫃合一" or r["name"] == "這段檢查自己出錯"}
            assert "這段檢查自己出錯" not in rs, rs
            hard = [r for n, r in rs.items() if r["ok"] is False and not n.startswith("合櫃前後在用台數")]
            assert not hard, hard
            cnt = next(r for n, r in rs.items() if n.startswith("合櫃前後在用台數"))
            det = rack_layout.rack_detail(conn, ROOM, "A1")
            counted = [d for d in det["devices"] if d["state"] == "active" and d.get("box_role") in rack_layout.COUNTED]
            # 前提：兩筆主機名、機型一樣、同 U → 在同一櫃裡就會被判成同一箱
            assert len(counted) == 1, [d["name"] for d in counted]
            # 合櫃前（1-1、A1 各自一櫃）是 2 台，合櫃後 1 台：對照剛啟用、還沒確認＝寬限中只警告（2.043.16，GPT 第三次複核 P1 雞生蛋）
            assert cnt["ok"] is None and "寬限中" in cnt["detail"] and "合併前 2 台、合併後 1 台" in cnt["detail"] and "dup01" in cnt["detail"], cnt
            # 對照啟用超過 7 天還沒確認＝FAIL 擋部署
            conn.execute("UPDATE app_settings SET value = ? WHERE key = 'rack_name_map_since'",
                         (json.dumps({f"{ROOM}/1-1": "2000-01-01 00:00:00"}),))
            conn.commit()
            vn.RESULTS.clear()
            vn.check_rack_name_merge(conn)
            cnt = next(x for x in vn.RESULTS if x["name"].startswith("合櫃前後在用台數"))
            assert cnt["ok"] is False and "未確認" in cnt["detail"] and "已超過 7 天" in cnt["detail"], cnt
        finally:
            conn.close()
        g = c.get("/api/racks/name-map").json()
        ch = g["merge_changes"]
        assert len(ch) == 1 and ch[0]["before"] == 2 and ch[0]["after"] == 1 and not ch[0]["confirmed"], ch
        assert c.put("/api/racks/merge-confirm", json={"room": ROOM, "rack": "A1", "keys": []}).status_code == 400, "少給設備不能確認"
        r = c.put("/api/racks/merge-confirm", json={"room": ROOM, "rack": "A1", "keys": ch[0]["keys"]})
        assert r.status_code == 200, r.text
        conn = db.get_connection(db_path)
        try:
            vn.RESULTS.clear()
            vn.check_rack_name_merge(conn)
            cnt = next(x for x in vn.RESULTS if x["name"].startswith("合櫃前後在用台數"))
            assert cnt["ok"] is True and "已確認" in cnt["detail"] and r.json()["by"] in cnt["detail"] and r.json()["at"] in cnt["detail"], cnt
            stored = json.loads(conn.execute("SELECT value FROM app_settings WHERE key = 'rack_merge_confirmed'").fetchone()[0])
            assert stored[0]["keys"] == ch[0]["keys"] and stored[0]["by"]
        finally:
            conn.close()
        assert c.put("/api/racks/merge-confirm", json={"room": ROOM, "rack": "1-2", "keys": []}).status_code == 400, "沒有差異的櫃不能確認"


def test_HTML報告_警告顯示警告不是不符(tmp_path):
    import verify_numbers as vn
    vn.RESULTS.clear()
    old = (vn.FAILS, vn.CHECKS)
    try:
        vn.CURRENT_SECTION = "測試段"
        vn.warn("頁", "待處理的事", False, "還沒套用")
        vn.inv("頁", "對得上", True)
        out = tmp_path / "r.html"
        vn.write_html(str(out))
        h = out.read_text(encoding="utf-8")
        assert "⚠ 警告" in h and "✗ 不符" not in h, "警告不可以顯示成不符（GPT 複核 E）"
        assert 'class="rowbad"' not in h and '<span class="secbadge ok">全過・⚠ 1</span>' in h
    finally:
        vn.FAILS, vn.CHECKS = old
        vn.RESULTS.clear()


def test_套用只啟用這一次Excel推得的對照(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _setup(tmp, monkeypatch)
        conn = db.get_connection(db_path)
        try:
            _hw(conn, "R-5", "b9host", "10.99.9.25", "1-3(B9)/7U")       # CIA 有括號代碼 B9，但這次的 Excel 裡沒有 B9
            conn.commit()
        finally:
            conn.close()
        _preview(c)                                               # 先前預覽過、放棄的那次
        assert f"{ROOM}/1-3" in c.get("/api/racks/name-map").json()["derived"], "前提：B9 也是建議之一"
        _apply(c)
        eff = c.get("/api/racks/name-map").json()["effective"]
        assert eff == {f"{ROOM}/1-1": "A1"}, f"只開這份 Excel 有寫的 A1，不把 B9 一起打開（GPT 複核 B）：{eff}"
        assert _racks(db_path)["R-5"]["rack"] == "1-3"


def test_多個系統櫃名合一_寫入固定取排序第一個_改對照不失聯(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _setup(tmp, monkeypatch)
        conn = db.get_connection(db_path)
        try:
            _hw(conn, "R-6", "zero", "10.99.9.26", "1-0/3U")
            conn.commit()
        finally:
            conn.close()
        _set_map(c, {f"{ROOM}/1-1": "A1", f"{ROOM}/1-0": "A1"})
        assert rack_layout.stored_rack({f"{ROOM}/1-1": "A1", f"{ROOM}/1-0": "A1"}, ROOM, "A1") == "1-0"
        conn = db.get_connection(db_path)
        try:
            key = next(m["key"] for m in rack_layout._machines(conn, read_only=True) if m["asset_serial"] == "R-2")
            rack_layout.set_position(conn, key, ROOM, "A1", 12, 1, "現場更正", "admin")
            assert conn.execute("SELECT rack FROM rack_position_override WHERE key = ?", (key,)).fetchone()[0] == "1-0", \
                "兩個系統櫃名合成 A1：固定存排序第一個（GPT 複核 C），不存畫面名 A1"
            rack_layout.reserve(conn, ROOM, "A1", 40, 1, "預留X", "2099-12-31", "admin")
            assert conn.execute("SELECT rack FROM rack_reservation WHERE label = '預留X'").fetchone()[0] == "1-0"
        finally:
            conn.close()
        _set_map(c, {f"{ROOM}/1-1": "XB", f"{ROOM}/1-0": "XB"})        # 之後改對照
        assert _racks(db_path)["R-2"]["rack"] == "XB", "改對照後，更正過位置的那台跟著到新名字"
        det = c.get("/api/racks/detail", params={"room": ROOM, "rack": "XB"}).json()
        assert any(r["label"] == "預留X" for r in det["reservations"])


def _old_rows(conn, key, rack):
    import overlap_queue
    rack_layout.ensure_position_table(conn)
    rack_layout.ensure_reservation_table(conn)
    overlap_queue.ensure_table(conn)
    conn.execute("INSERT INTO rack_position_override (key, room, rack, u_start, u_end, note, status) "
                 "VALUES (?, ?, ?, 12, 12, '舊更正', 'active')", (key, ROOM, rack))
    conn.execute("INSERT INTO rack_reservation (room, rack, u_start, u_end, label, expires_at) VALUES (?, ?, 40, 40, '舊預留', '2099-12-31')", (ROOM, rack))
    conn.execute("INSERT INTO rack_overlap_decision (room, rack, keys_json, action, decided_by, decided_at) "
                 "VALUES (?, ?, '[]', 'accept_half', 'admin', '2026-11-01 00:00:00')", (ROOM, rack))
    conn.commit()


def test_舊紀錄遷移_預覽_改存系統櫃名_留紀錄_可重跑(monkeypatch):
    import rack_name_migrate as rnm
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _setup(tmp, monkeypatch)
        _set_map(c, {f"{ROOM}/1-2": "B7"})                     # CIA 沒有叫 B7 的櫃
        conn = db.get_connection(db_path)
        try:
            key = next(m["key"] for m in rack_layout._machines(conn, read_only=True) if m["asset_serial"] == "R-4")
            _old_rows(conn, key, "B7")                           # 舊版本存成畫面櫃名 B7 的三種紀錄（GPT 複核 A）
            conn.execute("INSERT INTO rack_reservation (room, rack, u_start, u_end, label, expires_at, status) "
                         "VALUES (?, 'B7', 41, 41, '已結束', '2099-12-31', 'closed')", (ROOM,))      # 不生效的不動
            conn.execute("INSERT INTO rack_reservation (room, rack, u_start, u_end, label, expires_at) VALUES (?, '1-1', 9, 9, '別櫃', '2099-12-31')", (ROOM,))
            conn.commit()
            plan = rnm.preview(conn)
            assert sorted((x["tbl"], x["old_rack"], x["new_rack"]) for x in plan) == [
                ("rack_overlap_decision", "B7", "1-2"), ("rack_position_override", "B7", "1-2"), ("rack_reservation", "B7", "1-2")], plan
            assert conn.execute("SELECT COUNT(*) FROM rack_reservation WHERE rack = 'B7'").fetchone()[0] == 2, "預覽不寫任何東西"
            assert not rnm._has_table(conn, "rack_name_migration_log"), "預覽不建表"
            done = rnm.apply(conn, "tester")
            assert len(done) == 3
            assert conn.execute("SELECT rack FROM rack_position_override WHERE key = ?", (key,)).fetchone()[0] == "1-2"
            assert conn.execute("SELECT rack FROM rack_reservation WHERE label = '已結束'").fetchone()[0] == "B7"
            assert conn.execute("SELECT rack FROM rack_reservation WHERE label = '別櫃'").fetchone()[0] == "1-1"
            log = conn.execute("SELECT tbl, old_rack, new_rack, run_by FROM rack_name_migration_log").fetchall()
            assert len(log) == 3 and all(x["run_by"] == "tester" and x["old_rack"] == "B7" for x in log)
            assert rnm.preview(conn) == [] and rnm.apply(conn) == [], "可重跑：第二次沒東西可改"
        finally:
            conn.close()
        _set_map(c, {f"{ROOM}/1-2": "XB"})          # 改對照：遷移過的紀錄跟著走
        assert _racks(db_path)["R-4"]["rack"] == "XB" and _racks(db_path)["R-4"]["u_start"] == 12
        det = c.get("/api/racks/detail", params={"room": ROOM, "rack": "XB"}).json()
        assert any(x["label"] == "舊預留" for x in det["reservations"])
        conn = db.get_connection(db_path)
        try:
            assert rnm.revert(conn, "tester") == 3, "可以退回最近一次遷移"
            assert conn.execute("SELECT rack FROM rack_reservation WHERE label = '舊預留'").fetchone()[0] == "B7"
            assert rnm.revert(conn, "tester") == 0
        finally:
            conn.close()


def test_舊紀錄遷移_只改確定的_CIA有同名櫃不動_認不出來列待確認(monkeypatch):
    import rack_name_migrate as rnm
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _setup(tmp, monkeypatch)
        conn = db.get_connection(db_path)
        try:
            _hw(conn, "R-7", "seven", "10.99.9.27", "1-3/2U")
            conn.commit()
        finally:
            conn.close()
        # A1：CIA 真的有 A1 櫃（R-3 寫 A1/30U）→ 存 A1 的紀錄本身就是實體櫃，不動、也不列待確認（GPT 第三次複核 P1）
        # C9：1-2、1-3 兩個系統櫃名都叫 C9 → 不知道原本是哪一櫃 → 待確認，不自動改到排序第一個
        _set_map(c, {f"{ROOM}/1-1": "A1", f"{ROOM}/1-2": "C9", f"{ROOM}/1-3": "C9"})
        conn = db.get_connection(db_path)
        try:
            ms = rack_layout._machines(conn, read_only=True)
            _old_rows(conn, next(m["key"] for m in ms if m["asset_serial"] == "R-2"), "A1")
            conn.execute("INSERT INTO rack_reservation (room, rack, u_start, u_end, label, expires_at) VALUES (?, 'C9', 20, 20, '兩櫃同名', '2099-12-31')", (ROOM,))
            conn.commit()
            plan, pending = rnm.scan(conn)
            assert plan == [], f"A1 有真的櫃、C9 認不出來：都不自動改 {plan}"
            assert [(x["tbl"], x["old_rack"], x["candidates"]) for x in pending] == [("rack_reservation", "C9", ["1-2", "1-3"])], pending
            assert rnm.apply(conn) == []
            assert conn.execute("SELECT rack FROM rack_reservation WHERE label = '兩櫃同名'").fetchone()[0] == "C9"
            assert conn.execute("SELECT rack FROM rack_reservation WHERE label = '舊預留'").fetchone()[0] == "A1"
        finally:
            conn.close()


def test_舊紀錄遷移_失敗整個退回連log表都不留_並行改掉的不記(monkeypatch):
    import rack_name_migrate as rnm
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _setup(tmp, monkeypatch)
        _set_map(c, {f"{ROOM}/1-2": "B7"})
        conn = db.get_connection(db_path)
        try:
            _old_rows(conn, next(m["key"] for m in rack_layout._machines(conn, read_only=True) if m["asset_serial"] == "R-4"), "B7")
            # log 表少一欄 → 寫紀錄那步失敗：資料與建表都要退回（GPT 第三次複核 P2）
            monkeypatch.setattr(rnm, "LOG_SQL", "CREATE TABLE IF NOT EXISTS rack_name_migration_log (id INTEGER PRIMARY KEY)")
            with pytest.raises(Exception):
                rnm.apply(conn, "tester")
            assert not rnm._has_table(conn, "rack_name_migration_log"), "建表也在交易裡，失敗不留空表"
            assert conn.execute("SELECT COUNT(*) FROM rack_reservation WHERE rack = 'B7'").fetchone()[0] == 1, "資料沒改"
            monkeypatch.undo()
            real = rnm.preview(conn)
            stale = [{**real[0], "old_rack": "已被別人改掉"}] + real[1:]
            monkeypatch.setattr(rnm, "preview", lambda _c: stale)
            done = rnm.apply(conn, "tester")
            assert len(done) == len(real) - 1, "UPDATE 沒改到一列的不算"
            assert conn.execute("SELECT COUNT(*) FROM rack_name_migration_log").fetchone()[0] == len(real) - 1, "也不記紀錄"
        finally:
            conn.close()


def test_舊紀錄遷移_命令列先備份才改_結束碼照實講(monkeypatch):
    import backup
    import rack_name_migrate as rnm
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _setup(tmp, monkeypatch)
        _set_map(c, {f"{ROOM}/1-2": "B7"})
        conn = db.get_connection(db_path)
        try:
            rack_layout.ensure_reservation_table(conn)
            conn.execute("INSERT INTO rack_reservation (room, rack, u_start, u_end, label, expires_at) VALUES (?, 'B7', 40, 40, '舊預留', '2099-12-31')", (ROOM,))
            conn.commit()
        finally:
            conn.close()
        monkeypatch.setenv("ASSET_DB_PATH", str(db_path))
        bk = Path(tmp) / "bk"
        assert rnm._cli(["preview"]) == 0
        assert not list(bk.glob("*.db")), "預覽不備份、不改"
        assert rnm._cli(["apply"]) == 5, "改了資料＝5（deploy 據此重跑數字核對）"
        assert list(bk.glob("*.db")), "改之前要先有備份"
        assert rnm._cli(["apply"]) == 0, "沒東西可改＝0"
        conn = db.get_connection(db_path)
        try:
            assert conn.execute("SELECT rack FROM rack_reservation WHERE label = '舊預留'").fetchone()[0] == "1-2"
            conn.execute("INSERT INTO rack_reservation (room, rack, u_start, u_end, label, expires_at) VALUES (?, 'B7', 41, 41, '再一筆', '2099-12-31')", (ROOM,))
            conn.commit()
        finally:
            conn.close()
        monkeypatch.setattr(backup, "run_backup", lambda *a, **k: type("R", (), {"ok": False, "error": "磁碟滿"})())
        assert rnm._cli(["apply"]) == 1, "備份失敗不改"
        conn = db.get_connection(db_path)
        try:
            assert conn.execute("SELECT rack FROM rack_reservation WHERE label = '再一筆'").fetchone()[0] == "B7"
        finally:
            conn.close()


def test_對照生效時間_新增改值才重算_舊對照部署時補記(monkeypatch):
    import rack_name_migrate as rnm
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _setup(tmp, monkeypatch)
        _set_map(c, {f"{ROOM}/1-1": "A1"})
        conn = db.get_connection(db_path)
        try:
            since = rack_layout.name_map_since(conn)
            assert set(since) == {f"{ROOM}/1-1"}
            conn.execute("UPDATE app_settings SET value = ? WHERE key = 'rack_name_map_since'", (json.dumps({f"{ROOM}/1-1": "2000-01-01 00:00:00"}),))
            conn.commit()
        finally:
            conn.close()
        _set_map(c, {f"{ROOM}/1-1": "A1", f"{ROOM}/1-2": "B7"})
        conn = db.get_connection(db_path)
        try:
            since = rack_layout.name_map_since(conn)
            assert since[f"{ROOM}/1-1"] == "2000-01-01 00:00:00", "值沒變的保留原本生效時間"
            assert since[f"{ROOM}/1-2"] > "2026-01-01"
            conn.execute("DELETE FROM app_settings WHERE key = 'rack_name_map_since'")       # 2.043.15 以前寫的：沒有生效時間
            conn.commit()
        finally:
            conn.close()
        monkeypatch.setenv("ASSET_DB_PATH", str(db_path))
        assert rnm._cli(["apply"]) == 0
        conn = db.get_connection(db_path)
        try:
            assert set(rack_layout.name_map_since(conn)) == {f"{ROOM}/1-1", f"{ROOM}/1-2"}, "部署時補記，寬限才有起點"
        finally:
            conn.close()


def test_deploy遷移後重跑數字核對_訊息照實講():
    d = (ROOT / "APP" / "asset-module" / "deploy.sh").read_text(encoding="utf-8")
    seg = d[d.index("[3a3/7]"):d.index("[3b/7]")]
    assert "5)" in seg and "verify_numbers.py" in seg and "rack_name_migrate.py revert" in seg and "exit 1" in seg, "改了資料要重跑核對、沒過中止並講怎麼退回"
    assert "正式庫沒動，部署繼續" not in seg, "不可以對所有失敗都宣稱正式庫沒動（GPT 第三次複核 P2）"


def test_位置更正過_快照與詳細頁講清楚差在哪(monkeypatch):
    fe = ROOT / "APP" / "asset-module" / "frontend" / "pages" / "v2"
    m = (fe / "machine" / "[key].vue").read_text(encoding="utf-8")
    assert "rack_info?.position_corrected" in m and "CIA 登記" in m and "cia_where" in m, "詳細頁位置要標出現場更正過、CIA 登記在哪（GPT 複核 F）"
    ch = (fe / "changes.vue").read_text(encoding="utf-8")
    assert "不套用機櫃圖上的「位置更正」" in ch
    import v2_views
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _setup(tmp, monkeypatch)
        conn = db.get_connection(db_path)
        try:
            ms = rack_layout._machines(conn, read_only=True)
            key = next(m["key"] for m in ms if m["asset_serial"] == "R-4")
            rack_layout.set_position(conn, key, ROOM, "1-2", 9, 1, "現場更正", "admin")
            ri = v2_views._rack_info(conn, key, ["R-4"])
        finally:
            conn.close()
        assert ri["position_corrected"] and ri["cia_where"] == "1-2 5U", ri


def test_verify不變式在一般情況全綠(monkeypatch):
    import verify_numbers as vn
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _setup(tmp, monkeypatch)
        _apply(c)
        conn = db.get_connection(db_path)
        try:
            vn.RESULTS.clear()
            vn.check_rack_name_merge(conn)
            rs = [r for r in vn.RESULTS if r["page"] == "機櫃合一" or r["name"] == "這段檢查自己出錯"]
            assert len(rs) == 7 and all(r["ok"] for r in rs), rs
        finally:
            conn.close()


def test_每日快照_換名不算位置改變():
    import daily_digest as dd
    cfg = {"count_change_pct": 10, "count_change_min": 5, "uptime_days": 365, "gap_days": 3}
    base = {"hostname": "h", "ips": "10.99.9.1", "api_ids": "", "os": "RHEL", "room": ROOM, "u": "10", "retired": 0}
    prev = {"k": {**base, "rack": "1-1"}}
    cur = {"k": {**base, "rack": "A1"}}
    assert dd.diff_rows(prev, cur, cfg, "2026-11-05", rack_names={f"{ROOM}/1-1": "A1"})["items"]["location"] == []
    assert len(dd.diff_rows(prev, cur, cfg, "2026-11-05")["items"]["location"]) == 1, "沒給對照時照舊（確認測試真的會紅）"


def test_IP提示不把查詢結果當判定():
    v = (ROOT / "APP" / "asset-module" / "frontend" / "components" / "RackElevation.vue").read_text(encoding="utf-8")
    assert "查得到＝系統有" not in v and "到 2.1 主機查詢查這個 IP" in v


@pytest.mark.parametrize("bad", [{"沒有斜線": "A1"}])
def test_對照鍵格式錯會擋(monkeypatch, bad):
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _setup(tmp, monkeypatch)
        assert c.put("/api/racks/name-map", json={"items": bad}).status_code == 400
