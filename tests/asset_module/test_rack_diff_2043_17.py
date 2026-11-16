"""2.043.17 機櫃差異待辦（需求表 R4–R8；docs/ui-v2/機櫃差異待辦_規格_2026-11-04.md 第二階段、附錄 A）。D3／D4。
現場回報不改正式資料；viewer 403；六種狀態與非法轉換；結案動作（關機保留 U 用電 0 不算台數、實體移除、位置更正、綁定、退役核准）
各有成功與拒絕；每步留稽核、可還原（被別人改過就擋）；批次先預覽、筆數對不上整批不做；重匯 Excel 不改舊快照、相同差異不重開、
同 U 不同文字列待確認。全部假資料、IP 用 10.99.x。"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import rack_diff  # noqa: E402
import rack_layout  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_rack_excel_import import ROOM, _client, make_xlsx  # noqa: E402

PW = "test-password-123"
XL = {"B8": [(5, 5, "moved2 10.99.7.2"), (30, 30, "Ghost HPE 10.99.7.50")]}


def _env(tmp, monkeypatch):
    c, db_path = _client(tmp, monkeypatch)
    conn = db.get_connection(db_path)
    try:
        for name, role in (("alice", "user"), ("vera", "viewer")):
            db.create_user(conn, name, auth.hash_password(PW))
            conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, name))
        conn.commit()
    finally:
        conn.close()
    return c, db_path


def _as(name):
    cl = TestClient(api.app, raise_server_exceptions=False)
    assert cl.post("/api/auth/login", json={"username": name, "password": PW}).status_code == 200
    tok = cl.cookies.get("session_token")
    if tok:
        cl.headers["X-CSRF-Token"] = auth.csrf_token_for(tok)
    return cl


def _key(db_path, serial):
    conn = db.get_connection(db_path)
    try:
        return next(m["key"] for m in rack_layout.machines(conn) if m["asset_serial"] == serial)
    finally:
        conn.close()


def _key_ro(db_path, serial):
    conn = db.get_connection(db_path)
    try:
        return next(m["key"] for m in rack_layout._machines(conn, read_only=True) if m["asset_serial"] == serial)
    finally:
        conn.close()


def _m(db_path, serial):
    conn = db.get_connection(db_path)
    try:
        return next(m for m in rack_layout._machines(conn, read_only=True) if m["asset_serial"] == serial)
    finally:
        conn.close()


def _sys(c, db_path, who, reason="off", **kw):
    return c.post("/api/racks/diff", json={"subject": "sys", "reason": reason, "room": ROOM, "rack": "B8",
                                           "sys_key": _key(db_path, who), **kw})


def _act(c, i, action, **kw):
    return c.post(f"/api/racks/diff/{i}/act", json={"action": action, **kw})


def _tile(db_path, rack="B8"):
    conn = db.get_connection(db_path)
    try:
        dc = rack_layout.datacenter(conn)
        room = next(r for r in dc["rooms"] if r["room"] == ROOM)
        return next(t for t in room["racks"] if t["rack"] == rack), room
    finally:
        conn.close()


def _verify(db_path):
    import verify_numbers as vn
    conn = db.get_connection(db_path)
    try:
        vn.RESULTS.clear()
        vn.check_rack_diff(conn)
        return [r for r in vn.RESULTS if r["page"] == "差異待辦" or r["name"] == "這段檢查自己出錯"]
    finally:
        conn.close()


def test_回報不改正式資料_viewer擋_重複擋_原因要對(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        alice, vera = _as("alice"), _as("vera")
        before = _m(db_path, "S2")
        assert _sys(vera, db_path, "S2").status_code == 403, "viewer 不能回報（R4）"
        r = _sys(alice, db_path, "S2", note="現場看到沒插電", serial="SNX-1")
        assert r.status_code == 200, r.text
        it = r.json()
        assert it["status"] == "pending" and it["created_by"].startswith("alice") and it["note"] == "現場看到沒插電" and it["serial"] == "SNX-1"
        assert it["log"][0]["action"] == "report" and it["log"][0]["by"].startswith("alice")
        after = _m(db_path, "S2")
        assert (after["state"], after["u_start"], after["rack"]) == (before["state"], before["u_start"], before["rack"]), "回報不改正式資料"
        assert _sys(alice, db_path, "S2").status_code == 409, "同一台已有未處理的待辦"
        assert _sys(alice, db_path, "S3", reason="unsure").status_code == 400, "不確定要寫備註"
        assert _sys(alice, db_path, "S3", reason="absent").status_code == 400, "系統設備不能用 Excel 格的原因"
        assert it["reason_label"] == "還在，但關機或拔電", "現場用語白話（GPT B4）"
        assert rack_diff.REASONS_SYS["removed"].startswith("這台不在這格") and rack_diff.REASONS_XL == {
            "absent": "這格其實是空的", "wrongpos": "位置寫錯了", "bind": "這格就是系統裡的某一台"}
        assert _act(alice, it["id"], "confirm_off").status_code == 403, "一般帳號不能結案"
        assert _act(vera, it["id"], "confirm_off").status_code == 403
        lst = vera.get("/api/racks/diff").json()
        assert lst["can_report"] is False and lst["can_edit"] is False and len(lst["items"]) == 1
        assert alice.get("/api/racks/diff").json()["can_report"] is True


def test_關機結案_保留U_用電0_不算台數_可還原(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        k = _key(db_path, "S2")
        t0, room0 = _tile(db_path)
        assert k in t0["keys"]
        it = _sys(c, db_path, "S2").json()
        r = _act(c, it["id"], "confirm_off", note="現場確認")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "closed" and r.json()["close_action"] == "confirm_off"
        m = _m(db_path, "S2")
        assert m["state"] == "off" and m["u_start"] == 5 and m["rack"] == "B8", "關機保留櫃位與 U"
        t1, room1 = _tile(db_path)
        assert k not in t1["keys"] and t1["device_count"] == t0["device_count"] - 1, "關機不算在用台數（同現有口徑）"
        assert k in t1["power"]["off_keys"] and t1["power"]["used_u"] == t0["power"]["used_u"], "關機仍佔 U"
        assert t1["power"]["w"] <= t0["power"]["w"], "用電不增加（關機算 0）"
        conn = db.get_connection(db_path)
        try:
            det = rack_layout.rack_detail(conn, ROOM, "B8")
            d = next(x for x in det["devices"] if x["key"] == k)
            assert d["state"] == "off" and k in det["power"]["off_keys"], "機櫃立面下鑽同一個口徑"
        finally:
            conn.close()
        assert all(x["ok"] for x in _verify(db_path) if x["ok"] is not None), _verify(db_path)
        r = _act(c, it["id"], "undo", note="按錯")
        assert r.status_code == 200 and r.json()["status"] == "pending"
        assert _m(db_path, "S2")["state"] == "active"
        t2, _ = _tile(db_path)
        assert t2["device_count"] == t0["device_count"] and k in t2["keys"], "還原後台數復原（R8）"
        acts = [x["action"] for x in c.get(f"/api/racks/diff/{it['id']}").json()["log"]]
        assert acts == ["report", "confirm_off", "undo"]
        conn = db.get_connection(db_path)
        try:
            aud = [r["new_state"] for r in conn.execute("SELECT new_state FROM rack_state_audit WHERE key = ? ORDER BY id", (k,))]
            assert aud == ["off", None], "機櫃狀態稽核留兩筆（主機履歷看得到）"
        finally:
            conn.close()


def test_非法轉換被拒_還原前被別人改過就擋(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        it = _sys(c, db_path, "S3", reason="removed").json()
        assert _act(c, it["id"], "done").status_code == 409, "待確認不能直接「外部已處理」"
        assert _act(c, it["id"], "bind").status_code == 409, "系統設備不能綁定"
        assert _act(c, it["id"], "reject").status_code == 400, "駁回要寫原因"
        assert _act(c, it["id"], "confirm_removed").status_code == 200
        assert _act(c, it["id"], "confirm_off").status_code == 409, "已結案不能再結案"
        k = _key(db_path, "S3")
        t, _ = _tile(db_path)
        assert k not in t["keys"]
        assert all(x["ok"] for x in _verify(db_path) if x["ok"] is not None)
        conn = db.get_connection(db_path)
        try:
            rack_layout.set_state_override(conn, k, "active", "別人改回在用", "bob")
        finally:
            conn.close()
        assert any(x["ok"] is None for x in _verify(db_path)), "結案後被改過＝警告"
        r = _act(c, it["id"], "undo")
        assert r.status_code == 409 and "又被改過" in r.text, "不蓋掉別人的修改"
        assert _m(db_path, "S3")["state"] == "active"


def test_搬到別櫃_位置更正_還原(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        r = _sys(c, db_path, "S9", reason="moved", target_rack="B8", target_u_start=33, target_u_end=34)
        assert r.status_code == 200, r.text
        i = r.json()["id"]
        assert _act(c, i, "move").status_code == 200
        m = _m(db_path, "S9")
        assert (m["rack"], m["u_start"], m["u_end"], m["position_corrected"]) == ("B8", 33, 34, True)
        assert _act(c, i, "undo").status_code == 200
        m = _m(db_path, "S9")
        assert (m["u_start"], m["position_corrected"]) == (18, False)


def test_退役要核准人與依據_只寫CIA待異動_可還原(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        i = _sys(c, db_path, "S8", reason="removed").json()["id"]
        assert _act(c, i, "retire").json()["status"] == "retire_wait"
        assert _act(c, i, "approve_retire", approver="資產負責人甲").status_code == 400, "要有依據"
        r = _act(c, i, "approve_retire", approver="資產負責人甲", basis="簽呈 1130-01")
        assert r.status_code == 200 and r.json()["status"] == "closed" and r.json()["approver"] == "資產負責人甲"
        conn = db.get_connection(db_path)
        try:
            row = conn.execute("SELECT asset_status, pending_status FROM hardware WHERE asset_serial = 'S8'").fetchone()
            assert row["asset_status"] == "使用中" and row["pending_status"] == "報廢", "不直接改 CIA 資產狀態，只記待異動"
            ch = conn.execute("SELECT new_value, reason FROM cia_pending_change WHERE asset_serial = 'S8'").fetchall()
            assert [x["new_value"] for x in ch] == ["報廢"] and "簽呈 1130-01" in ch[0]["reason"]
        finally:
            conn.close()
        assert _m(db_path, "S8")["kind"] == "removed", "退役＝已不在櫃上"
        assert _act(c, i, "undo").status_code == 200
        conn = db.get_connection(db_path)
        try:
            assert conn.execute("SELECT pending_status FROM hardware WHERE asset_serial = 'S8'").fetchone()[0] is None
            vals = [x[0] for x in conn.execute("SELECT new_value FROM cia_pending_change WHERE asset_serial = 'S8' ORDER BY id")]
            assert vals == ["報廢", "（取消待異動）"], "給 CIA 窗口的清單看得到取消"
        finally:
            conn.close()
        assert _m(db_path, "S8")["state"] == "active"
        assert c.get(f"/api/racks/diff/{i}").json()["status"] == "retire_wait", "還原回到上一個狀態"


def _xl_preview(c, racks=None):
    r = c.post("/api/racks/excel-import/preview", params={"room": ROOM},
               files={"file": ("x.xlsx", make_xlsx(racks or XL, height=42), "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()


def _cells(c):
    return c.get("/api/racks/detail", params={"room": ROOM, "rack": "B8"}).json()["excel"]["cells"]


def test_Excel格_退回Excel_重匯不改舊快照_相同不重開_不同列待確認(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        pv1 = _xl_preview(c)
        ghost = next(x for x in _cells(c) if "Ghost" in x["label"])
        assert ghost["status"] == "MISSING"
        r = c.post("/api/racks/diff", json={"subject": "xl", "reason": "absent", "room": ROOM, "rack": "B8",
                                            "xl_run_id": pv1["run_id"], "xl_row_id": ghost["id"]})
        assert r.status_code == 200, r.text
        i = r.json()["id"]
        assert next(x for x in _cells(c) if x["id"] == ghost["id"])["diff"]["id"] == i, "格子上標出待辦"
        assert _act(c, i, "confirm_off").status_code == 409, "Excel 格不能用系統設備的動作"
        assert _act(c, i, "to_excel").json()["status"] == "excel"
        conn = db.get_connection(db_path)
        try:
            snap1 = conn.execute("SELECT rows_json, summary_json FROM rack_excel_run WHERE id = ?", (pv1["run_id"],)).fetchone()
        finally:
            conn.close()
        pv2 = _xl_preview(c)                                              # 下一版 Excel 還寫著同一格
        g2 = next(x for x in _cells(c) if "Ghost" in x["label"])
        assert g2["diff"] and g2["diff"]["id"] == i, "還在「待退回 Excel」：指到同一筆，不是新待辦"
        r = c.post("/api/racks/diff", json={"subject": "xl", "reason": "absent", "room": ROOM, "rack": "B8",
                                            "xl_run_id": pv2["run_id"], "xl_row_id": g2["id"]})
        assert r.status_code == 409, "相同差異不重開待辦（R7）"
        assert _act(c, i, "done", note="已通知維護者").json()["status"] == "closed"
        pv3 = _xl_preview(c)
        g3 = next(x for x in _cells(c) if "Ghost" in x["label"])
        assert g3["status"] == "STALE" and g3["stale"]["id"] == i, "處理完、下一版仍寫著＝Excel 尚未更新"
        r = c.post("/api/racks/diff", json={"subject": "xl", "reason": "absent", "room": ROOM, "rack": "B8",
                                            "xl_run_id": pv3["run_id"], "xl_row_id": g3["id"]})
        assert r.status_code == 409 and "已處理過" in r.text, "結案過的同一差異直接打 API 也擋（GPT A5）"
        pv4 = _xl_preview(c, {"B8": [(5, 5, "moved2 10.99.7.2"), (30, 30, "Another Box 10.99.7.60")]})
        g4 = next(x for x in _cells(c) if x["u_start"] == 30)
        assert g4["status"] == "MISSING" and g4["recheck"] and g4["recheck"][0]["id"] == i, "同一段 U 但文字不同：待確認，不沿用、不猜"
        r = c.post("/api/racks/diff", json={"subject": "xl", "reason": "absent", "room": ROOM, "rack": "B8",
                                            "xl_run_id": pv4["run_id"], "xl_row_id": g4["id"]})
        assert r.status_code == 200, "Excel 文字變了＝新的差異，可以開"
        conn = db.get_connection(db_path)
        try:
            again = conn.execute("SELECT rows_json, summary_json FROM rack_excel_run WHERE id = ?", (pv1["run_id"],)).fetchone()
            assert tuple(again) == tuple(snap1), "重匯不改舊快照"
        finally:
            conn.close()


def test_Excel格_綁定既有資產_畫到這格_還原(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        pv = _xl_preview(c)
        ghost = next(x for x in _cells(c) if "Ghost" in x["label"])
        body = {"subject": "xl", "reason": "bind", "room": ROOM, "rack": "B8", "xl_run_id": pv["run_id"], "xl_row_id": ghost["id"]}
        assert c.post("/api/racks/diff", json={**body, "bind_q": "沒有這台"}).status_code == 400, "對不到不猜"
        i = c.post("/api/racks/diff", json={**body, "bind_q": "other9"}).json()["id"]
        r = _act(c, i, "bind")
        assert r.status_code == 200 and r.json()["status"] == "matched"
        m = _m(db_path, "S9")
        assert (m["rack"], m["u_start"]) == ("B8", 30)
        ex = c.get("/api/racks/detail", params={"room": ROOM, "rack": "B8"}).json()["excel"]
        assert not any("Ghost" in x["label"] for x in ex["cells"]), "綁定後兩邊一致：不再畫 Excel 框、不標 STALE（GPT A4）"
        assert ex["by_key"][_key_ro(db_path, "S9")] in ("OK", "POSONLY"), "那台的對照結果＝一致"
        assert c.post("/api/racks/diff", json={**body, "bind_q": "other9"}).status_code == 409, "已匹配的同一格不再開"
        assert _act(c, i, "undo").status_code == 200 and _m(db_path, "S9")["u_start"] == 18
        g = next(x for x in _cells(c) if "Ghost" in x["label"])
        assert g["status"] == "MISSING", "還原後回到原本的差異"


def test_Excel回報_櫃位以Excel那一列為準(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        pv = _xl_preview(c, {"B8": [(30, 30, "Ghost HPE 10.99.7.50")], "C2": [(7, 7, "Other Box 10.99.7.70")]})
        row = next(x for x in pv["rows"] if x["rack"] == "C2")
        r = c.post("/api/racks/diff", json={"subject": "xl", "reason": "bind", "room": ROOM, "rack": "B8", "bind_q": "other9",
                                            "xl_run_id": pv["run_id"], "xl_row_id": row["id"]})
        assert r.status_code == 400 and "C2" in r.text, "C2 那一列不能報成 B8（GPT A3），綁定才不會寫錯櫃"
        r = c.post("/api/racks/diff", json={"subject": "xl", "reason": "bind", "room": ROOM, "rack": "C2", "bind_q": "other9",
                                            "xl_run_id": pv["run_id"], "xl_row_id": row["id"]})
        assert r.status_code == 200 and r.json()["rack"] == "C2", r.text
        assert _act(c, r.json()["id"], "bind").status_code == 200
        m = _m(db_path, "S9")
        assert (m["rack"], m["u_start"]) == ("C2", 7)


def test_綁定過且那台就在這格_不畫Excel框_標一致不標STALE(monkeypatch):
    """GPT A4：系統比對沒把那台認成這一格（例：Excel 寫的名字跟系統差很多）時，綁定後也要算一致，不可以變成「Excel 尚未更新」。"""
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        pv = _xl_preview(c)
        ghost = next(x for x in _cells(c) if "Ghost" in x["label"])
        i = c.post("/api/racks/diff", json={"subject": "xl", "reason": "bind", "room": ROOM, "rack": "B8", "bind_q": "other9",
                                            "xl_run_id": pv["run_id"], "xl_row_id": ghost["id"]}).json()["id"]
        assert _act(c, i, "bind").status_code == 200
        conn = db.get_connection(db_path)
        try:
            k9 = _key_ro(db_path, "S9")
            ms = rack_layout.machines_readonly_full(conn)
            excel = {"run": {"run_id": pv["run_id"]}, "by_key": {}, "cells": [{**ghost, "status": "MISSING"}]}
            rack_diff.annotate_cells(conn, ROOM, "B8", excel, ms)
            assert excel["cells"] == [] and excel["by_key"][k9] == "OK", excel
            moved = [{**m, "u_start": 3, "u_end": 3} if m["key"] == k9 else m for m in ms]      # 那台後來又搬走了
            excel = {"run": {"run_id": pv["run_id"]}, "by_key": {}, "cells": [{**ghost, "status": "MISSING"}]}
            rack_diff.annotate_cells(conn, ROOM, "B8", excel, moved)
            assert excel["cells"][0]["status"] == "MISSING" and k9 not in excel["by_key"], "不在這格了：照現在比對，不標 STALE"
        finally:
            conn.close()


def test_差異待辦寫入端點都明確分級():
    import access_policy as ap
    assert ap.classification("POST", "/api/racks/diff") == "user"
    for path in ("/api/racks/diff/{item_id}/act", "/api/racks/diff/batch-preview", "/api/racks/diff/batch-apply"):
        assert ap.classification("POST", path) == "admin", path


def test_verify獨立重算_U佔用與用電0(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        i = _sys(c, db_path, "S2").json()["id"]
        assert _act(c, i, "confirm_off").status_code == 200
        assert all(x["ok"] for x in _verify(db_path) if x["ok"] is not None)
        real = rack_layout.rack_power

        def lies_u(devs, *a, **kw):           # 關機的不算佔 U（壞掉的情況）
            return real([{**d, "u_start": None, "u_end": None} if d["state"] == "off" else d for d in devs], *a, **kw)
        monkeypatch.setattr(rack_layout, "rack_power", lies_u)
        bad = [x["name"] for x in _verify(db_path) if x["ok"] is False]
        assert any("U 仍佔用" in n for n in bad), bad

        def lies_w(devs, *a, **kw):           # 關機的也算用電（壞掉的情況）
            out = real(devs, *a, **kw)
            out["w"] = out["w"] + sum((d.get("w") or 100) for d in devs if d["state"] == "off")
            return out
        monkeypatch.setattr(rack_layout, "rack_power", lies_w)
        bad = [x["name"] for x in _verify(db_path) if x["ok"] is False]
        assert any("用電 0" in n for n in bad), bad


def test_批次先預覽_只認伺服器token_資料變了整批不做_中途失敗整批退回(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        a = _sys(c, db_path, "S2").json()["id"]
        b = _sys(c, db_path, "S3").json()["id"]
        x = _sys(c, db_path, "S9", reason="moved").json()["id"]
        assert _act(c, x, "reject", note="現場其實在").status_code == 200
        pv = c.post("/api/racks/diff/batch-preview", json={"ids": [a, b, x], "action": "confirm_off"}).json()
        assert pv["count"] == 2 and pv["ok_ids"] == [a, b] and pv["skip"][0]["id"] == x and pv["token"]
        assert c.post("/api/racks/diff/batch-preview", json={"ids": [a], "action": "move"}).status_code == 400, "要逐筆填的不能批次"
        assert c.post("/api/racks/diff/batch-preview", json={"ids": [a, b], "action": "reject"}).status_code == 400, "駁回要原因"
        # 不信任客戶端的 ids（GPT A2）：沒有 token／亂給 token 都不做
        assert c.post("/api/racks/diff/batch-apply", json={"ids": [a, b], "action": "confirm_off"}).status_code == 422
        assert c.post("/api/racks/diff/batch-apply", json={"token": "亂給的"}).status_code == 409
        assert _m(db_path, "S2")["state"] == "active"
        # 別的帳號（就算也是管理員）拿我的 token 不行
        conn = db.get_connection(db_path)
        try:
            db.create_user(conn, "adm2", auth.hash_password(PW))
            conn.execute("UPDATE users SET role = 'admin' WHERE username = 'adm2'")
            conn.commit()
        finally:
            conn.close()
        r = _as("adm2").post("/api/racks/diff/batch-apply", json={"token": pv["token"]})
        assert r.status_code == 409 and "不是你做的" in r.text, r.text
        assert _as("alice").post("/api/racks/diff/batch-apply", json={"token": pv["token"]}).status_code == 403
        # 預覽之後其中一筆被改過 → 指紋不同 → 整批不做
        assert _act(c, a, "to_excel").status_code == 200 and _act(c, a, "undo").status_code == 200
        r = c.post("/api/racks/diff/batch-apply", json={"token": pv["token"]})
        assert r.status_code == 409 and "被改過" in r.text and _m(db_path, "S3")["state"] == "active", "資料變了：整批沒做"
        pv = c.post("/api/racks/diff/batch-preview", json={"ids": [a, b, x], "action": "confirm_off"}).json()
        real = rack_layout.set_state_override
        calls = {"n": 0}

        def boom(*args, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("第二筆寫入失敗")
            return real(*args, **kw)
        monkeypatch.setattr(rack_layout, "set_state_override", boom)
        c2 = TestClient(api.app, raise_server_exceptions=False, cookies=c.cookies, headers=c.headers)
        assert c2.post("/api/racks/diff/batch-apply", json={"token": pv["token"]}).status_code == 500
        monkeypatch.setattr(rack_layout, "set_state_override", real)
        assert _m(db_path, "S2")["state"] == "active" and c.get(f"/api/racks/diff/{a}").json()["status"] == "pending", "中途失敗整批退回"
        r = c.post("/api/racks/diff/batch-apply", json={"token": pv["token"]})
        assert r.status_code == 200 and r.json()["count"] == 2
        assert _m(db_path, "S2")["state"] == "off" and _m(db_path, "S3")["state"] == "off"
        r = c.post("/api/racks/diff/batch-apply", json={"token": pv["token"]})
        assert r.status_code == 409 and "送出過" in r.text, "token 只能用一次"
        conn = db.get_connection(db_path)
        try:
            pv2 = rack_diff.batch_preview(conn, [x], "done", "admin")
            conn.execute("UPDATE rack_diff_batch SET expires_at = '2000-01-01 00:00:00' WHERE token = ?", (pv2["token"],))
            conn.commit()
            with pytest.raises(rack_diff.BatchStale):
                rack_diff.batch_apply(conn, pv2["token"], "admin")
        finally:
            conn.close()


def test_回報只寫待辦表_其他表一筆都不動(monkeypatch):
    """GPT A1（P0）：一般帳號回報前後，除了待辦表，全庫每一張表的內容都不能變（回報路徑用唯讀版 machines）。"""
    import hashlib
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        # 先造一個「可寫的 machines() 會順手寫庫」的狀況：位置更正跟 CIA 一樣（可寫版會標 CIA 已同步）
        conn = db.get_connection(db_path)
        try:
            k = next(m["key"] for m in rack_layout._machines(conn, read_only=True) if m["asset_serial"] == "S3")
            rack_layout.ensure_position_table(conn)
            conn.execute("INSERT INTO rack_position_override (key, asset_serial, room, rack, u_start, u_end, note, status) "
                         "VALUES (?, 'S3', ?, 'B8', 10, 11, '跟 CIA 一樣', 'active')", (k, ROOM))
            conn.commit()
        finally:
            conn.close()
        alice = _as("alice")
        skip = {"rack_diff_item", "rack_diff_log", "sessions", "activity_log", "users", "sqlite_sequence", "login_attempts"}

        def digest():
            cn = db.get_connection(db_path)
            try:
                out = {}
                for (t,) in cn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                    if t in skip:
                        continue
                    rows = cn.execute(f'SELECT * FROM "{t}"').fetchall()
                    out[t] = hashlib.sha256(json.dumps(sorted(map(tuple, rows), key=repr), default=str, ensure_ascii=False).encode()).hexdigest()
                return out
            finally:
                cn.close()
        before = digest()
        r = alice.post("/api/racks/diff", json={"subject": "sys", "reason": "off", "room": ROOM, "rack": "B8", "sys_key": _key_ro(db_path, "S2")})
        assert r.status_code == 200, r.text
        r = alice.post("/api/racks/diff", json={"subject": "sys", "reason": "removed", "room": ROOM, "rack": "B8", "sys_key": _key_ro(db_path, "S9")})
        assert r.status_code == 200, r.text
        after = digest()
        changed = sorted(t for t in set(before) | set(after) if before.get(t) != after.get(t))
        assert changed == [], f"回報動到待辦表以外的表：{changed}"


def test_清單筆數等於下鑽_verify全綠(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        assert all(x["ok"] for x in _verify(db_path)), "沒有表也不會出錯"
        a = _sys(c, db_path, "S2").json()["id"]
        _sys(c, db_path, "S3")
        assert _act(c, a, "to_excel").status_code == 200
        j = c.get("/api/racks/diff").json()
        for st, n in j["counts"].items():
            assert n == len(c.get("/api/racks/diff", params={"status": st}).json()["items"]) == sum(1 for x in j["items"] if x["status"] == st)
        assert j["counts"]["pending"] == 1 and j["counts"]["excel"] == 1
        rs = _verify(db_path)
        assert rs and all(x["ok"] for x in rs if x["ok"] is not None), rs
        conn = db.get_connection(db_path)
        try:
            rows = rack_diff.export_rows(conn)
        finally:
            conn.close()
        assert [r[0] for r in rows] == [f"D{a}", f"D{a + 1}"]


def test_verify抓得到_結案關機的那台還算在用(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        i = _sys(c, db_path, "S2").json()["id"]
        k = _key(db_path, "S2")
        conn = db.get_connection(db_path)
        try:
            # 造一筆「結案關機」但那台實際還在用、結案當下的快照也是在用（不算「被別人改過」）→ 數字對不上要紅
            conn.execute("UPDATE rack_diff_item SET status='closed', close_action='confirm_off', undo_json=? WHERE id=?",
                         (json.dumps({"after": {"key": k, "state": None, "pos": None}}), i))
            conn.commit()
        finally:
            conn.close()
        bad = [x for x in _verify(db_path) if x["ok"] is False]
        assert bad and "關機未下架" in bad[0]["name"], _verify(db_path)


def test_verify抓得到_筆數對不上與不認得的狀態(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        _sys(c, db_path, "S2")
        real = rack_diff.counts
        monkeypatch.setattr(rack_diff, "counts", lambda conn, room=None: {**real(conn, room), "pending": 9})
        bad = [x["name"] for x in _verify(db_path) if x["ok"] is False]
        assert any("「待確認」筆數" in n for n in bad), bad
        monkeypatch.setattr(rack_diff, "counts", real)
        conn = db.get_connection(db_path)
        try:
            conn.execute("UPDATE rack_diff_item SET status = 'weird'")
            conn.commit()
        finally:
            conn.close()
        bad = [x["name"] for x in _verify(db_path) if x["ok"] is False]
        assert any("不認得的狀態" in n for n in bad), bad


def test_畫面入口_每格回報與差異待辦頁籤():
    fe = ROOT / "APP" / "asset-module" / "frontend"
    el = (fe / "components" / "RackElevation.vue").read_text(encoding="utf-8")
    assert 'data-testid="diff-report"' in el and 'data-testid="diff-report-xl"' in el and "V2RackDiffReport" in el
    assert "canReport" in el and "!props.compact" in el, "viewer／事件應變小圖不顯示回報入口"
    dc = (fe / "pages" / "v2" / "datacenter.vue").read_text(encoding="utf-8")
    assert "{ k: 'diff', label: '差異待辦' }" in dc and "V2RackDiffList" in dc
    # 2.043.18 GPT B1：入口至少 44px，另有「回報模式」整格可點（拖拉、調高度在回報模式關掉）
    assert 'data-testid="diff-report-mode"' in el and "min-width: 44px" in el and "reportMode.value && canReport.value" in el
    assert "!reportMode && startDrag" in el
    lst = (fe / "components" / "v2" / "RackDiffList.vue").read_text(encoding="utf-8")
    assert 'class="drawer"' in lst, "B2：處理表單是側邊抽屜，不在表格最下面"
    assert 'data-testid="diff-confirm"' in lst and "actSummary" in lst, "B3：正式資料動作送出前有確認摘要"
    assert "guardedFetch('/api/racks/diff/batch-preview'" in lst, "A8：預覽也走寫入守門（428 會問管理密碼）"
    assert "body: { token: pv.value.token, note: bNote.value || null }" in lst, "A2：送出只帶 token（＋當下備註給後端比對）"
    assert "watch([selected, bAction, bNote], () => { pv.value = null })" in lst, "備註改了預覽作廢（GPT 複核 P1）"
    assert lst.count("下一步：") >= 3, "B5：失敗訊息寫下一步"


def test_R20_R21_R22_機櫃格子重排_格子說明_盤點檢視(monkeypatch):
    import re
    fe = ROOT / "APP" / "asset-module" / "frontend"
    el = (fe / "components" / "RackElevation.vue").read_text(encoding="utf-8")
    sys_face = el[el.index('<span class="xrows">'):el.index('<span class="go" role="link"')]
    xl_face = el[el.index('<div v-for="c in xlDrawn"'):el.index('<div v-for="r in det.reservations')]
    for face in (sys_face, xl_face):
        assert re.findall(r'data-cell="(\d)"', face) == ["1", "2", "3", "5", "6", "7"], "第 1 列 1 2 3、第 2 列 5 6 7（狀態在第 7 格）"
    assert "firstCellText(d)" in sys_face and "sysText(d)" in sys_face, "第 1 格主機名稱／廠牌、第 6 格系統｜管理口"
    assert ".xmodel { grid-column: span 2; white-space: normal" in el and ".xmgmt { grid-column: span 2; white-space: normal" in el, "機型兩格寬、放不下換行"
    assert 'data-testid="cell-legend"' in el and "CELL_LEGEND" in el, "R21 格子說明直接放在畫面上"
    assert 'data-testid="audit-view"' in el and 'data-testid="audit-list"' in el and "window.print()" in el, "R22 盤點檢視可切換、可列印"
    assert 'data-testid="audit-report"' in el and ".pbtn.abtn { min-height: 44px; min-width: 44px; }" in el, "R22 盤點檢視直接回報，入口 ≥44px"
    for f in ("序號 {{ serialOf(it.d!)", "機型 {{ up(it.d!.model)", "IP {{ it.d!.ip", "狀態 {{", "管理口 {{ mgmtShort(g)", "系統 {{"):
        assert f in el, f"盤點檢視每台資料都要在：{f}"
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        det = c.get("/api/racks/detail", params={"room": ROOM, "rack": "B8"}).json()
        d = next(x for x in det["devices"] if x["asset_serial"] == "S2")
        assert d["vendor"] == "Dell", "後端給廠牌（rack_layout.vendor_of），第 1 格沒有真主機名稱時用"


def test_2044_6_批次預覽後改備註_token作廢(monkeypatch):
    """GPT 複核 P1：畫面顯示的備註跟稽核寫進去的不可以不同；預覽之後改了備註，送出就拒絕。"""
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        a = _sys(c, db_path, "S2").json()["id"]
        b = _sys(c, db_path, "S3").json()["id"]
        pv = c.post("/api/racks/diff/batch-preview", json={"ids": [a, b], "action": "reject", "note": "現場其實在"}).json()
        assert pv["count"] == 2
        r = c.post("/api/racks/diff/batch-apply", json={"token": pv["token"], "note": "改過的原因"})
        assert r.status_code == 409 and "備註改過" in r.text
        assert c.post("/api/racks/diff/batch-apply", json={"token": pv["token"]}).status_code == 409, "沒帶備註也不行"
        assert c.get(f"/api/racks/diff/{a}").json()["status"] == "pending", "整批沒做"
        r = c.post("/api/racks/diff/batch-apply", json={"token": pv["token"], "note": " 現場其實在 "})
        assert r.status_code == 200 and r.json()["count"] == 2
        log = c.get(f"/api/racks/diff/{a}").json()["log"]
        assert log[-1]["note"] == "現場其實在"


def test_2044_6_回報是哪一台_候選清單_點選帶key(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, db_path = _env(tmp, monkeypatch)
        vera = _as("vera")
        j = vera.get("/api/racks/diff/candidates", params={"room": ROOM, "rack": "B8", "q": "OTHER"}).json()
        assert [x["name"] for x in j["items"]][:1] == ["other9"], "主機名模糊比對（不分大小寫）"
        j = c.get("/api/racks/diff/candidates", params={"room": ROOM, "rack": "B8", "q": "7.8"}).json()
        assert any(x["name"] == "occupant8" for x in j["items"]), "IP 部分比對"
        j = c.get("/api/racks/diff/candidates", params={"room": ROOM, "rack": "B8", "q": ""}).json()
        names = [x["name"] for x in j["items"]]
        assert "moved2" in names and all(x["same_rack"] for x in j["items"]), "沒打字＝列這一櫃的設備"
        j = c.get("/api/racks/diff/candidates", params={"room": ROOM, "rack": "B8", "q": "nopos"}).json()
        assert j["items"] and j["items"][0]["name"] == "nopos1" and not j["items"][0]["same_rack"], "同機房、沒定位的也列"
        items = c.get("/api/racks/diff/candidates", params={"room": ROOM, "rack": "B8", "q": "o"}).json()["items"]
        flags = [x["same_rack"] for x in items]
        assert True in flags and False in flags and flags == sorted(flags, reverse=True), f"同一櫃排前面：{[(x['name'], x['same_rack']) for x in items]}"
        assert not any(x["name"] == "vm5" for x in c.get("/api/racks/diff/candidates", params={"room": ROOM, "q": "vm"}).json()["items"]), "虛擬機不列"
        pv = _xl_preview(c)
        ghost = next(x for x in _cells(c) if "Ghost" in x["label"])
        body = {"subject": "xl", "reason": "bind", "room": ROOM, "rack": "B8", "xl_run_id": pv["run_id"], "xl_row_id": ghost["id"]}
        assert c.post("/api/racks/diff", json={**body, "bind_key": "沒有這個key"}).status_code == 400
        k9 = _key_ro(db_path, "S9")
        r = c.post("/api/racks/diff", json={**body, "bind_q": "moved2", "bind_key": k9})
        assert r.status_code == 400 and "對不上" in r.text, "選定的那台要符合欄位上的字（舊搜尋結果晚到也不會記錯台，GPT 複核 P1）"
        assert c.post("/api/racks/diff", json={**body, "bind_key": k9}).status_code == 400, "只帶 key 沒有字也不行"
        r = c.post("/api/racks/diff", json={**body, "bind_q": "oth", "bind_key": k9})
        assert r.status_code == 200 and r.json()["bind_key"] == k9, "從清單選定＝直接用那一台，不用打到完全相符"


def test_2044_6_掃條碼只在支援的裝置顯示():
    fe = ROOT / "APP" / "asset-module" / "frontend"
    rep = (fe / "components" / "v2" / "RackDiffReport.vue").read_text(encoding="utf-8")
    assert 'v-if="scanOk"' in rep and "canScan()" in rep and "V2BarcodeScan" in rep
    assert "/api/racks/diff/candidates" in rep and 'data-testid="diff-cands"' in rep and "bind_key: f.bind_key" in rep
    assert "if (!seq.isLatest(n)) return\n    cands.value = items" in rep, "只採用最新一次搜尋的結果（GPT 複核 P1）"
    sc = (fe / "components" / "v2" / "BarcodeScan.vue").read_text(encoding="utf-8")
    assert "BarcodeDetector" in sc and "getTracks().forEach(t => t.stop())" in sc, "掃完要關相機"
