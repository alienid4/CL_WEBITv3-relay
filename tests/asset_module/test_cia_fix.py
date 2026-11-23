"""9.3 CIA 修正包（2.060.0，P1 寫法統一）。全部假資料（IP 一律 10.99.x、機房名稱虛構）。

規則判定／切包／產包重驗與唯一性／Excel 六張工作表與鎖定／交回檔差異報告／狀態機（撤回、重開、事件只增）／
CIA 匯入後自動核對（⚠D4：接在 _cia_commit 之後，沒產過包時完全不動資料庫）／verify_numbers 不變式（含突變抽查）。
"""
import io
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import cia_fix  # noqa: E402
import cia_import_preview as cp  # noqa: E402
import db  # noqa: E402
import verify_numbers as vn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"
ROOM = {"甲 機房": "01_甲機房", "甲機房(舊)": "01_甲機房"}


def _seed(conn):
    hw = [
        dict(asset_serial="S1", hostname="h1", ip="10.99.1.1", physical_location="甲 機房", hardware_no="無", big_ip_vip="N/A",
             request_no="n/a", usage_unit="請選擇使用單位", inventory_department="部A", group_name="G1"),
        dict(asset_serial="S2", hostname="h2", ip="10.99.1", physical_location="01_甲機房", hardware_no="0", big_ip_vip="無",
             request_no="NA", inventory_department="部A", group_name="G1"),
        dict(asset_serial="S3", hostname="h3", ip="10.99.1.3, 10.99.1.4", physical_location="甲機房(舊)", inventory_department="部B",
             group_name="G2"),
        dict(asset_serial="DYN-1", hostname="d1", ip="bad", physical_location="甲 機房"),       # 帳外：不列
        dict(asset_serial="ADOPT-10.99.1.9", hostname="a1", ip="bad", physical_location="甲 機房"),
    ]
    for h in hw:
        db.insert_hardware(conn, **h)
    conn.commit()
    cia_fix.save_settings(conn, ROOM, ["範本文字XYZ"])


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    yield c
    c.close()


def _keys(rows):
    return {(r["asset_serial"], r["field"], r["old_value"], r["new_value"]) for r in rows}


# ---------------------------------------------------------------- 規則
def test_規則判定_帳外不列_標準寫法不列_一格只給一條規則(conn):
    pv = cia_fix.preview(conn)
    assert _keys(pv["rows"]) == {
        ("S1", "physical_location", "甲 機房", "01_甲機房"),
        ("S1", "hardware_no", "無", "0"),
        ("S1", "big_ip_vip", "N/A", "無"),
        ("S1", "request_no", "n/a", "NA"),
        ("S1", "usage_unit", "請選擇使用單位", None),      # 範本提示文字：新值留空請人填
        ("S2", "ip", "10.99.1", None),                    # IP 格式錯：只列出
        ("S3", "physical_location", "甲機房(舊)", "01_甲機房"),
    }
    assert pv["assets"] == 3 and pv["total"] == 7
    assert {r["rule_id"]: r["count"] for r in pv["rules"]}["room_alias"] == 2
    assert not pv["room_alias_empty"]


def test_設定裡的範本文字也會抓到_沒設機房對照時提示(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="S9", hostname="h9", ip="10.99.9.9", usage_unit="xx 範本文字XYZ yy", physical_location="甲 機房")
    c.commit()
    pv = cia_fix.preview(c)
    assert pv["room_alias_empty"] and pv["total"] == 0
    cia_fix.save_settings(c, {}, ["範本文字XYZ"])
    assert _keys(cia_fix.preview(c)["rows"]) == {("S9", "usage_unit", "xx 範本文字XYZ yy", None)}


def test_規則檔沒有真實機房名與真實IP():
    raw = cia_fix.RULES_PATH.read_text(encoding="utf-8")
    assert "10.93." not in raw and "10.92." not in raw
    assert "內湖" not in raw and "板橋" not in raw and "敦南" not in raw


# ---------------------------------------------------------------- 切包
def test_切包_依單位分_同一台不拆兩包_每包不超過上限():
    cands = []
    for unit, n_assets, per in (("U1", 5, 3), ("U2", 1, 7)):
        for a in range(n_assets):
            for f in range(per):
                cands.append({"unit": unit, "asset_group": f"G{a % 2}", "asset_serial": f"{unit}-A{a}", "field": f"f{f}"})
    pk = cia_fix.pack(cands, 7)
    assert all(len({c["unit"] for c in p["rows"]}) == 1 for p in pk)
    owner = {}
    for p in pk:
        assert len(p["rows"]) <= 7
        for c in p["rows"]:
            assert owner.setdefault(c["asset_serial"], p["pkg_no"]) == p["pkg_no"]     # 同一台只在一包
    assert [p["pkg_no"] for p in pk] == list(range(1, len(pk) + 1))
    assert sum(len(p["rows"]) for p in pk) == len(cands)


def test_一台本身超過上限_自己一包():
    cands = [{"unit": "U", "asset_group": "G", "asset_serial": "BIG", "field": f"f{i}"} for i in range(5)]
    cands += [{"unit": "U", "asset_group": "G", "asset_serial": "SMALL", "field": "f"}]
    pk = cia_fix.pack(cands, 3)
    assert [len(p["rows"]) for p in pk] == [5, 1]


# ---------------------------------------------------------------- 產包
def test_產包_指紋不符就擋_未結案唯一_第二次預覽排除處理中的(conn):
    pv = cia_fix.preview(conn)
    with pytest.raises(cia_fix.Stale):
        cia_fix.create_batch(conn, "tester", "wrong")
    out = cia_fix.create_batch(conn, "tester", pv["fingerprint"])
    assert out == {"batch_id": 1, "items": 7, "packages": 2}       # 部A、部B 各一包
    items = cia_fix.list_items(conn)
    assert all(i["track_code"] == f"CF1-{i['id']}" for i in items)
    assert {i["status"] for i in items} == {"pending"}
    pv2 = cia_fix.preview(conn)
    assert pv2["total"] == 0 and pv2["in_open"] == 7
    with pytest.raises(ValueError):
        cia_fix.create_batch(conn, "tester", pv2["fingerprint"])
    # 部分唯一索引兜底：硬塞同一格的未結案列會被資料庫擋
    i = items[0]
    with pytest.raises(Exception):
        conn.execute("INSERT INTO cia_fix_item (batch_id, pkg_no, asset_serial, field, old_value, rule_id, status) "
                     "VALUES (1, 9, ?, ?, ?, 'x', 'pending')", (i["asset_serial"], i["field"], i["old_value"]))
    conn.rollback()
    b = cia_fix.list_batches(conn)[0]
    assert b["created_by"] == "tester" and b["rules_sha256"] and b["settings_sha256"]
    snap = conn.execute("SELECT snapshot_json FROM cia_fix_batch").fetchone()[0]
    assert "01_甲機房" in snap


def test_脫敏快照遮掉IP字面值():
    s = cia_fix._snapshot({"rules": []}, {"x": {"10.99.1.1 機房": "a"}})
    assert "10.99.1.1" not in s and "x.x.x.x" in s


def test_預覽之後資料變了_產包被擋(conn):
    pv = cia_fix.preview(conn)
    conn.execute("UPDATE hardware SET hardware_no = '0' WHERE asset_serial = 'S1'")
    conn.commit()
    with pytest.raises(cia_fix.Stale):
        cia_fix.create_batch(conn, "tester", pv["fingerprint"])
    assert not cia_fix.list_batches(conn)


# ---------------------------------------------------------------- Excel
def _make(conn):
    pv = cia_fix.preview(conn)
    cia_fix.create_batch(conn, "tester", pv["fingerprint"])
    buf, n, name = cia_fix.package_xlsx(conn, 1, 1)
    return buf.getvalue(), n, name


def test_匯出一包_六張工作表_決定欄以外鎖定_追蹤碼隱藏(conn):
    data, n, name = _make(conn)
    assert n == 6 and name.startswith("CIA修正包_第1批_第1包_部A")
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["1 說明", "2 修正明細", "3 補登清單", "4 待填檢查", "5 選項清單", "6 修正後全表（參考）"]
    ws = wb["2 修正明細"]
    head = [c.value for c in ws[1]]
    assert head == cia_fix.DETAIL_COLS
    assert ws.protection.sheet
    col = {h: i + 1 for i, h in enumerate(head)}
    assert not ws.cell(2, col["同意"]).protection.locked and not ws.cell(2, col["不同意原因"]).protection.locked
    assert ws.cell(2, col["原值"]).protection.locked and ws.cell(2, col["新值"]).protection.locked
    assert ws.column_dimensions[openpyxl.utils.get_column_letter(col["追蹤碼"])].hidden
    assert ws.max_row == 7 and all(str(ws.cell(r, col["追蹤碼"]).value).startswith("CF1-") for r in range(2, 8))
    assert len(ws.data_validations.dataValidation) == 2
    assert wb["3 補登清單"].max_row == 1 and wb["4 待填檢查"].max_row == 1          # P1 只有表頭
    opts = [r[1] for r in wb["5 選項清單"].iter_rows(min_row=2, values_only=True) if r[1]]
    assert opts == cia_fix.load_rules()["reject_codes"]
    full = wb["6 修正後全表（參考）"]
    heads = [c.value for c in full[2]]
    rows = {r[heads.index("資產序號")]: r for r in full.iter_rows(min_row=3, values_only=True)}
    assert set(rows) == {"S1", "S2", "S3"}                                           # 只有 CIA 正本
    assert rows["S1"][heads.index("資產實體位置")] == "01_甲機房"                      # 套用了修正
    assert rows["S2"][heads.index("IP")] == "10.99.1"                                 # 新值留空的不套


def _fill(data: bytes, edits: dict[int, dict]) -> bytes:
    """edits：{Excel 列號: {欄名: 值}}"""
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb["2 修正明細"]
    col = {c.value: i + 1 for i, c in enumerate(ws[1])}
    for r, kv in edits.items():
        for k, v in kv.items():
            ws.cell(r, col[k]).value = v
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_交回檔_記錄決定_被改過的列不記_差異報告_不同意要原因(conn):
    data, _, _ = _make(conn)
    code = cia_fix.load_rules()["reject_codes"][0]
    back = _fill(data, {2: {"同意": "同意"}, 3: {"同意": "不同意", "不同意原因": code, "備註": "原本就對"},
                        4: {"同意": "不同意"},                          # 沒選原因 → 不合格
                        5: {"同意": "同意", "新值": "被偷改"},          # 鎖定欄被改 → 差異報告
                        6: {"同意": "好"}})                             # 不是同意／不同意
    pv = cia_fix.upload_preview(conn, back, "back.xlsx")
    assert pv["agree"] == 1 and pv["reject"] == 1 and [x["row"] for x in pv["blank"]] == [7]
    assert [x["row"] for x in pv["tampered"]] == [5] and pv["tampered"][0]["diffs"][0]["col"] == "新值"
    assert sorted(x["row"] for x in pv["invalid"]) == [4, 6]
    assert cia_fix.counts(conn)["pending"] == 7                          # 預覽不寫庫
    with pytest.raises(cia_fix.Stale):
        cia_fix.upload_confirm(conn, back, "back.xlsx", "x", "tester")
    res = cia_fix.upload_confirm(conn, back, "back.xlsx", pv["file_sha256"], "mgr")
    assert res["recorded"] == 2
    c = cia_fix.counts(conn)
    assert (c["agreed"], c["rejected"], c["pending"]) == (1, 1, 5)
    rej = cia_fix.list_items(conn, "rejected")[0]
    assert rej["reject_code"] == code and rej["decision_note"] == "原本就對" and rej["decided_by"] == "mgr"
    ev = cia_fix.item_events(conn, rej["id"])
    assert [e["action"] for e in ev] == ["create", "decide"] and ev[-1]["source_sha256"] == pv["file_sha256"]
    # 同一份再交一次：沒變的不重記
    again = cia_fix.upload_preview(conn, back, "back.xlsx")
    assert again["record"] == [] and sorted(x["row"] for x in again["same"]) == [2, 3]


def test_交回檔不是修正包_回錯誤(conn):
    wb = openpyxl.Workbook()
    out = io.BytesIO()
    wb.save(out)
    with pytest.raises(ValueError, match="不是 CIA 修正包"):
        cia_fix.upload_preview(conn, out.getvalue(), "x.xlsx")
    with pytest.raises(ValueError):
        cia_fix.upload_preview(conn, b"not a zip", "x.xlsx")


# ---------------------------------------------------------------- 狀態機
def test_撤回_重開_事件只增不改(conn):
    _make(conn)
    items = cia_fix.list_items(conn)
    a, b = items[0]["id"], items[1]["id"]
    with pytest.raises(ValueError):
        cia_fix.withdraw(conn, [a], "", "t")                                  # 原因必填
    assert cia_fix.withdraw(conn, [a], "這格先不送", "t")["updated"] == 1
    assert cia_fix.list_items(conn, "withdrawn")[0]["id"] == a
    # 撤回後同一格可以再產包（不在未結案裡）
    pv = cia_fix.preview(conn)
    assert pv["total"] == 1
    # 重開：原值仍是 CIA 現值 → 回待決定
    assert cia_fix.reopen(conn, [a], "改主意", "t")["updated"] == 1
    assert [e["action"] for e in cia_fix.item_events(conn, a)] == ["create", "withdraw", "reopen"]
    # 待決定不能重開；不同意後 CIA 現值變了也不能重開
    assert cia_fix.reopen(conn, [b], "x", "t")["skipped"]
    code = cia_fix.load_rules()["reject_codes"][1]
    cia_fix.decide(conn, [b], "不同意", code, None, "t")
    it = cia_fix.list_items(conn, "rejected")[0]
    conn.execute(f"UPDATE hardware SET {it['field']} = 'changed' WHERE asset_serial = ?", (it["asset_serial"],))
    conn.commit()
    sk = cia_fix.reopen(conn, [b], "x", "t")["skipped"]
    assert sk and "不是原值" in sk[0]["why"]
    with pytest.raises(ValueError):
        cia_fix.decide(conn, [a], "不同意", None, None, "t")                 # 不同意要原因


def test_曾被不同意的_預設不再送_勾選才重送(conn):
    _make(conn)
    code = cia_fix.load_rules()["reject_codes"][0]
    ids = [i["id"] for i in cia_fix.list_items(conn)]
    cia_fix.decide(conn, ids, "不同意", code, None, "t")
    pv = cia_fix.preview(conn)
    assert pv["total"] == 0 and pv["was_rejected"] == 7
    assert cia_fix.preview(conn, resend_rejected=True)["total"] == 7


# ---------------------------------------------------------------- 自動核對（直接呼叫）
def test_自動核對_已套用_另改_消失_請人填(conn):
    _make(conn)
    cia_fix.decide(conn, [i["id"] for i in cia_fix.list_items(conn) if i["field"] == "physical_location"], "同意", None, None, "t")
    conn.execute("UPDATE hardware SET physical_location = '01_甲機房', hardware_no = '9', ip = '10.99.1.2' WHERE asset_serial IN ('S1','S2')")
    conn.execute("UPDATE hardware SET usage_unit = '' WHERE asset_serial = 'S1'")      # 範本文字改成空白 → 還是沒填
    conn.execute("DELETE FROM hardware WHERE asset_serial = 'S3'")
    conn.commit()
    res = cia_fix.reconcile_after_import(conn, "cia_20261110.xlsx", "abc", "tester")
    by = {(i["asset_serial"], i["field"]): i for i in cia_fix.list_items(conn)}
    assert by[("S1", "physical_location")]["status"] == "applied"
    assert by[("S1", "physical_location")]["closed_sha256"] == "abc"
    assert by[("S1", "hardware_no")]["status"] == "cia_other" and by[("S1", "hardware_no")]["cia_value_at_close"] == "9"
    assert by[("S2", "ip")]["status"] == "applied"                                    # 請人填：改成合法 IP
    assert by[("S1", "usage_unit")]["status"] == "cia_other"                          # 請人填：改成空白不算
    assert by[("S3", "physical_location")]["status"] == "gone"
    assert by[("S1", "big_ip_vip")]["status"] == "pending"                            # 沒動
    assert {k: res[k] for k in ("checked", "applied", "cia_other", "gone")} == {"checked": 7, "applied": 2, "cia_other": 2, "gone": 1}
    assert cia_fix.last_reconcile(conn)["file"] == "cia_20261110.xlsx"
    ev = cia_fix.item_events(conn, by[("S1", "physical_location")]["id"])
    assert [e["action"] for e in ev] == ["create", "decide", "applied"] and ev[-1]["source_file"] == "cia_20261110.xlsx"
    # 已套用不能撤回、不能改決定
    assert cia_fix.withdraw(conn, [by[("S1", "physical_location")]["id"]], "x", "t")["skipped"]


def test_沒產過包_核對什麼都不做_也不建表(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    assert cia_fix.reconcile_after_import(c, "f.xlsx", "x", "t") is None
    assert not cia_fix._ready(c)
    assert cia_fix.counts(c)["total"] == 0 and cia_fix.list_items(c) == [] and cia_fix.list_batches(c) == []


# ---------------------------------------------------------------- verify_numbers
def test_verify_numbers_不變式通過_改壞會紅(conn):
    _make(conn)
    cia_fix.decide(conn, [cia_fix.list_items(conn)[0]["id"]], "同意", None, None, "t")
    before = vn.FAILS
    vn.check_cia_fix(conn)
    assert vn.FAILS == before
    # 突變：直接改狀態不寫事件 → 「最後一筆事件＝目前狀態」紅
    conn.execute("UPDATE cia_fix_item SET status = 'rejected' WHERE id = (SELECT MAX(id) FROM cia_fix_item)")
    conn.commit()
    vn.check_cia_fix(conn)
    assert vn.FAILS == before + 1
    vn.FAILS = before


def test_verify_numbers_卡片跟下鑽對不上會紅(conn, monkeypatch):
    _make(conn)
    before = vn.FAILS
    real = cia_fix.list_items
    monkeypatch.setattr(cia_fix, "list_items", lambda c, s=None, *a, **k: real(c, s, *a, **k)[1:] if s == "pending" else real(c, s, *a, **k))
    vn.check_cia_fix(conn)
    assert vn.FAILS == before + 1
    vn.FAILS = before


# ---------------------------------------------------------------- API＋CIA 匯入接線（⚠D4）
def _cia_file(path: Path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "硬體"
    ws.append(["資產序號", "主機名稱", "IP", "資產實體位置", "硬體編號", "BIG IP/VIP", "申請單編號", "使用單位", "盤點單位-部門", "群組名稱"])
    for r in rows:
        ws.append(list(r))
    wb.create_sheet("人員").append(["資產序號", "人員姓名"])
    wb.create_sheet("軟體").append(["資產序號", "資料庫/軟體"])
    wb.save(path)
    return path.read_bytes()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "PREVIEW_DIR", tmp_path / "preview")
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.create_user(c, "tester", auth.hash_password(_PW))
    c.close()

    def _get_db():
        cc = db.get_connection(p)
        try:
            yield cc
        finally:
            cc.close()
    api.app.dependency_overrides[api.get_db] = _get_db
    cl = TestClient(api.app)
    assert cl.post("/api/auth/login", json={"username": "tester", "password": _PW}).status_code == 200
    yield cl, p, tmp_path
    api.app.dependency_overrides.clear()


def _import(cl, data, name):
    pv = cl.post("/api/import/excel/preview", files={"file": (name, data, "application/octet-stream")})
    assert pv.status_code == 200, pv.text
    r = cl.post("/api/import/excel/confirm", json={"token": pv.json()["token"], "file_sha256": pv.json()["file_sha256"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_API全流程_產包_下載_交回_CIA匯入後自動標已套用(client):
    cl, p, tmp = client
    f1 = _cia_file(tmp / "cia_20261101.xlsx", [("S1", "h1", "10.99.1.1", "甲 機房", "無", "N/A", "NA", "部A", "部A", "G1"),
                                              ("S2", "h2", "10.99.1.2", "01_甲機房", "0", "無", "NA", "部A", "部A", "G1")])
    r1 = _import(cl, f1, "cia_20261101.xlsx")
    assert r1["cia_fix"] is None                                                 # 沒產過包：什麼都不做
    c = db.get_connection(p)
    assert not cia_fix._ready(c)
    c.close()
    assert cl.put("/api/cia-fix/settings", json={"room_alias": ROOM, "template_texts": []}).status_code == 200
    pv = cl.get("/api/cia-fix/preview").json()
    assert pv["total"] == 3 and pv["cia_import"]["data_date"] == "2026-11-01"
    assert cl.post("/api/cia-fix/batches", json={"fingerprint": "bad"}).status_code == 409
    mk = cl.post("/api/cia-fix/batches", json={"fingerprint": pv["fingerprint"]})
    assert mk.status_code == 200 and mk.json()["items"] == 3
    x = cl.get("/api/cia-fix/batches/1/packages/1.xlsx")
    assert x.status_code == 200 and x.headers["X-Row-Count"] == "3"
    assert cl.get("/api/cia-fix/batches/1/packages/9.xlsx").status_code == 404
    back = _fill(x.content, {2: {"同意": "同意"}, 3: {"同意": "同意"}, 4: {"同意": "同意"}})
    up = cl.post("/api/cia-fix/upload/preview", files={"file": ("back.xlsx", back, "application/octet-stream")})
    assert up.status_code == 200 and up.json()["agree"] == 3
    ok = cl.post("/api/cia-fix/upload/confirm", files={"file": ("back.xlsx", back, "application/octet-stream")},
                 data={"file_sha256": up.json()["file_sha256"]})
    assert ok.status_code == 200 and ok.json()["recorded"] == 3
    s = cl.get("/api/cia-fix/summary").json()
    assert s["counts"]["agreed"] == 3 and s["counts"]["open_batches"] == 1
    # CIA 管理人改好兩格（機房、硬體編號），BIG IP 改成別的寫法
    f2 = _cia_file(tmp / "cia_20261115.xlsx", [("S1", "h1", "10.99.1.1", "01_甲機房", "0", "NONE", "NA", "部A", "部A", "G1"),
                                              ("S2", "h2", "10.99.1.2", "01_甲機房", "0", "無", "NA", "部A", "部A", "G1")])
    r2 = _import(cl, f2, "cia_20261115.xlsx")
    assert {k: r2["cia_fix"][k] for k in ("checked", "applied", "cia_other", "gone")} == {"checked": 3, "applied": 2, "cia_other": 1, "gone": 0}
    assert r2["cia_fix"]["data_date"] == "2026-11-15" and r2["cia_fix"]["rows"] == 2 and r2["cia_fix"]["warn"] is None
    s = cl.get("/api/cia-fix/summary").json()
    assert s["counts"]["applied"] == 2 and s["counts"]["cia_other"] == 1 and s["counts"]["open_batches"] == 0
    assert s["last_reconcile"]["file"] == "cia_20261115.xlsx" and len(s["last_reconcile"]["sha256"]) == 64
    it = cl.get("/api/cia-fix/items", params={"status": "applied"}).json()["items"]
    ev = cl.get(f"/api/cia-fix/items/{it[0]['id']}/events").json()["items"]
    assert [e["action"] for e in ev] == ["create", "decide", "applied"]
    assert cl.get("/api/cia-fix/items", params={"status": "nope"}).status_code == 400
    assert cl.get("/api/cia-fix/rules").json()["locations"]


def test_自動核對出錯不影響CIA匯入_錯誤照實回(client, monkeypatch):
    cl, p, tmp = client
    f1 = _cia_file(tmp / "a.xlsx", [("S1", "h1", "10.99.1.1", "甲 機房", "無", "N/A", "NA", "部A", "部A", "G1")])

    def boom(*a, **k):
        raise RuntimeError("測試用故障")
    monkeypatch.setattr(cia_fix, "reconcile_after_import", boom)
    r = _import(cl, f1, "a.xlsx")
    assert "測試用故障" in r["cia_fix"]["error"]
    c = db.get_connection(p)
    assert c.execute("SELECT hostname FROM hardware WHERE asset_serial='S1'").fetchone()[0] == "h1"     # 匯入照樣寫進去
    c.close()


# ---------------------------------------------------------------- 2.060.x 挑剔使用者審查＋審查 P2
def test_P2a_沒有未結案_核對不拿寫入鎖也不寫上次核對(conn):
    _make(conn)
    cia_fix.withdraw(conn, [i["id"] for i in cia_fix.list_items(conn)], "全部撤回", "t")
    assert cia_fix.last_reconcile(conn) is None
    total = conn.total_changes
    res = cia_fix.reconcile_after_import(conn, "f.xlsx", "x", "t")
    assert res["checked"] == 0 and conn.total_changes == total and not conn.in_transaction
    assert cia_fix.last_reconcile(conn) is None


def test_P2b_Excel數字格不算被改過_明細格存文字(tmp_path):
    p = tmp_path / "n.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="007", hostname="h7", ip="10.99.7.7", hardware_no="無", inventory_department="部A")
    c.commit()
    pv = cia_fix.preview(c)
    cia_fix.create_batch(c, "t", pv["fingerprint"])
    buf, _, _ = cia_fix.package_xlsx(c, 1, 1)
    wb = openpyxl.load_workbook(io.BytesIO(buf.getvalue()))
    ws = wb["2 修正明細"]
    col = {x.value: i + 1 for i, x in enumerate(ws[1])}
    assert ws.cell(2, col["資產序號"]).value == "007" and ws.cell(2, col["資產序號"]).number_format == "@"
    back = _fill(buf.getvalue(), {2: {"資產序號": 7, "新值": 0.0, "同意": "同意"}})       # Excel 把 007 變 7、0 變 0.0
    res = cia_fix.upload_preview(c, back, "b.xlsx")
    assert res["tampered"] == [] and res["agree"] == 1
    back2 = _fill(buf.getvalue(), {2: {"資產序號": 8, "同意": "同意"}})                   # 真的改掉才算
    assert [d["col"] for d in cia_fix.upload_preview(c, back2, "b.xlsx")["tampered"][0]["diffs"]] == ["資產序號"]
    assert cia_fix._same("001", "1") and cia_fix._same("1.0", "1") and not cia_fix._same("1a", "1")


def test_3_交回預覽顯示是哪一包_不是本系統的檔拒收(conn):
    data, _, _ = _make(conn)
    pv = cia_fix.upload_preview(conn, data, "b.xlsx")
    (pk,) = pv["packages"]
    assert (pk["batch_id"], pk["pkg_no"], pk["unit"], pk["rows"]) == (1, 1, "部A", 6) and pk["created_at"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "2 修正明細"
    ws.append(cia_fix.DETAIL_COLS)
    ws.append([1, "S1", "h1", "x", "a", "b", "", "", "同意", "", "", "XX-1"])
    out = io.BytesIO()
    wb.save(out)
    with pytest.raises(ValueError, match="不是本系統產生"):
        cia_fix.upload_preview(conn, out.getvalue(), "fake.xlsx")


def test_4_正常列照記_明列被擋的列與剩下的待決定(conn):
    data, _, _ = _make(conn)
    back = _fill(data, {2: {"同意": "同意"}, 3: {"同意": "同意", "原值": "被改"}})
    pv = cia_fix.upload_preview(conn, back, "b.xlsx")
    assert len(pv["record"]) == 1 and len(pv["tampered"]) == 1
    assert len(pv["still_pending"]) == 5                                   # 6 列：1 記錄、1 被擋、4 沒填 → 剩 5 待決定
    res = cia_fix.upload_confirm(conn, back, "b.xlsx", pv["file_sha256"], "mgr")
    assert res["recorded"] == 1 and len(res["tampered"]) == 1 and len(res["still_pending"]) == 5
    assert res["packages"][0]["pending"] == 5


def test_12_明細篩選_單位_規則_某次交回(conn):
    data, _, _ = _make(conn)
    assert {i["asset_serial"] for i in cia_fix.list_items(conn, unit="部B")} == {"S3"}
    assert {i["asset_serial"] for i in cia_fix.list_items(conn, rule_id="room_alias")} == {"S1", "S3"}
    back = _fill(data, {2: {"同意": "同意"}, 3: {"同意": "同意"}})
    pv = cia_fix.upload_preview(conn, back, "b.xlsx")
    cia_fix.upload_confirm(conn, back, "b.xlsx", pv["file_sha256"], "mgr")
    assert len(cia_fix.list_items(conn, upload_sha=pv["file_sha256"])) == 2
    f = cia_fix.facets(conn)
    assert f["units"] == ["部A", "部B"] and {r["id"] for r in f["rules"]} >= {"room_alias", "ip_format"} and f["batches"] == [1]


def test_1_待人確認合計_卡片與下鑽(conn):
    _make(conn)
    conn.execute("DELETE FROM hardware WHERE asset_serial = 'S3'")
    conn.execute("UPDATE hardware SET hardware_no = '9' WHERE asset_serial = 'S1'")
    conn.commit()
    cia_fix.reconcile_after_import(conn, "f.xlsx", "x", "t")
    c = cia_fix.counts(conn)
    assert (c["cia_other"], c["gone"], c["needs_confirm"]) == (1, 1, 2)
    assert {i["status"] for i in cia_fix.list_items(conn, "confirm")} == {"cia_other", "gone"}
    with pytest.raises(ValueError):
        cia_fix.list_items(conn, "other")


def test_2_CIA筆數驟減會警示_不擋(client):
    cl, p, tmp = client
    rows = [(f"S{i}", f"h{i}", f"10.99.1.{i}", "甲 機房", "0", "無", "NA", "部A", "部A", "G1") for i in range(1, 11)]
    _import(cl, _cia_file(tmp / "cia_20261101.xlsx", rows), "cia_20261101.xlsx")
    assert cl.get("/api/cia-fix/preview").json()["cia_import"]["warn"] is None
    _import(cl, _cia_file(tmp / "cia_20261102.xlsx", rows[:5]), "cia_20261102.xlsx")
    ci = cl.get("/api/cia-fix/preview").json()["cia_import"]
    assert (ci["rows"], ci["prev_rows"], ci["data_date"]) == (5, 10, "2026-11-02") and "少了 5 筆" in ci["warn"]
