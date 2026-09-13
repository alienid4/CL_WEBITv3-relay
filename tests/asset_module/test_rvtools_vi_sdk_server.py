"""「這台在哪個 vCenter 上」要查得出來。

使用者 2026-08-28：「這很常追」。原本系統把來源一律寫死成 `source='vcenter'`，
多座 VC 的 RVTools 匯出全部混成一池，這個問題查不出答案。

守三件事：
1. `VI SDK Server` 欄要真的被讀進來、寫進 hardware（不是只留在 payload 裡）
2. **不可以抓成 `VI SDK UUID`** —— 那是伺服器的 UUID 不是位址，只差一個字，
   抓錯會存進一串沒人看得懂的 UUID 而且看起來「有值」
3. NULL 的三種原因要分得開。全部長一樣的話，人只能猜——而猜錯的方向
   （以為「查過沒有」）會讓他不去重匯，那批資料就永遠補不上
"""
import sys
import tempfile
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import rvtools_import as rv  # noqa: E402

HEADERS = ["VM", "DNS Name", "Powerstate", "Primary IP Address",
           "OS according to the VMware Tools", "Host", "VM UUID", "VM ID",
           "VI SDK Server", "VI SDK UUID"]

VC_A = "vcenter-a.corp.local"
VC_B = "vcenter-b.corp.local"
#: 這串刻意跟 VC 位址完全不同，抓錯欄位一眼就看得出來
SDK_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _make_xlsx(tmp: str, rows: list[dict], name="rvtools.xlsx") -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "vInfo"
    ws.append(HEADERS)
    for r in rows:
        ws.append([r.get(h, "") for h in HEADERS])
    p = Path(tmp) / name
    wb.save(p)
    return p


def _vm(uuid="42301111-2222-3333-4444-555566667777", vc=VC_A, name="web01"):
    return {
        "VM": name, "DNS Name": f"{name}.corp.local", "Powerstate": "poweredOn",
        "Primary IP Address": "10.1.1.10",
        "OS according to the VMware Tools": "Ubuntu Linux (64-bit)",
        "Host": "esxi-a.corp.local", "VM UUID": uuid, "VM ID": "vm-101",
        "VI SDK Server": vc, "VI SDK UUID": SDK_UUID,
    }


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _row(conn, uuid):
    return conn.execute(
        "SELECT * FROM hardware WHERE vm_uuid = ?", (uuid,)).fetchone()


def test_匯入會記下來源管理端():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            result = rv.import_rvtools(_make_xlsx(tmp, [_vm()]), conn)
            assert result["vi_sdk_servers"] == [VC_A]
            row = _row(conn, "42301111-2222-3333-4444-555566667777")
            assert row["vi_sdk_server"] == VC_A
        finally:
            conn.close()


def test_抓的是_Server_不是_UUID():
    """兩欄只差一個字。抓錯的話欄位「有值」，但那個值回答不了任何人的問題，
    而且因為看起來有值，沒有人會發現它是錯的。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            rv.import_rvtools(_make_xlsx(tmp, [_vm()]), conn)
            row = _row(conn, "42301111-2222-3333-4444-555566667777")
            assert row["vi_sdk_server"] == VC_A
            assert SDK_UUID not in (row["vi_sdk_server"] or "")
        finally:
            conn.close()


def test_換了一座VC要留痕不是靜默覆蓋():
    """跨 vCenter vMotion 是真的會發生。值該更新（新的比較新），但
    「什麼時候搬的」正是事故調查最想知道的事，不能安靜地換掉。"""
    uuid = "42301111-2222-3333-4444-555566667777"
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            rv.import_rvtools(_make_xlsx(tmp, [_vm(uuid, VC_A)], "a.xlsx"), conn)
            result = rv.import_rvtools(_make_xlsx(tmp, [_vm(uuid, VC_B)], "b.xlsx"), conn)

            assert len(result["vc_moved"]) == 1, "換了 VC 卻沒有留下任何紀錄"
            moved = result["vc_moved"][0]
            assert moved["from"] == VC_A and moved["to"] == VC_B
            assert _row(conn, uuid)["vi_sdk_server"] == VC_B, "值本身還是要更新成新的"
        finally:
            conn.close()


def test_沒換VC就不要吵():
    """同一份重匯是日常操作。每次都報「換了 VC」會讓真的搬遷被淹掉。"""
    uuid = "42301111-2222-3333-4444-555566667777"
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            rv.import_rvtools(_make_xlsx(tmp, [_vm(uuid, VC_A)], "a.xlsx"), conn)
            result = rv.import_rvtools(_make_xlsx(tmp, [_vm(uuid, VC_A)], "b.xlsx"), conn)
            assert result["vc_moved"] == []
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 空白的三種原因要分得開
# ---------------------------------------------------------------------------

def test_非RVTools來源的機器_說明是這台不是匯進來的():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            db.insert_hardware(conn, asset_serial="MANUAL-1", ip="10.9.9.9", is_vm=1)
            hw = dict(conn.execute(
                "SELECT * FROM hardware WHERE asset_serial = 'MANUAL-1'").fetchone())
            note = api._vi_sdk_note(conn, hw)
            assert note and "不是從 RVTools" in note
        finally:
            conn.close()


def test_匯出檔沒那一欄_說明是檔案沒有不是沒查():
    """舊版 RVTools 沒有 VI SDK Server 欄。這種要講「拿新版重匯」，
    跟「這台不在 vCenter 上」是完全不同的下一步。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "vInfo"
            old_headers = [h for h in HEADERS if not h.startswith("VI SDK")]
            ws.append(old_headers)
            v = _vm()
            ws.append([v.get(h, "") for h in old_headers])
            p = Path(tmp) / "old.xlsx"
            wb.save(p)

            result = rv.import_rvtools(p, conn)
            assert result["vi_sdk_servers"] == []
            hw = dict(_row(conn, "42301111-2222-3333-4444-555566667777"))
            assert hw["vi_sdk_server"] is None
            note = api._vi_sdk_note(conn, hw)
            assert note is not None
            # 不可以講成「這台不是匯進來的」——它就是匯進來的
            assert "不是從 RVTools" not in note
        finally:
            conn.close()


def test_有值就不要再解釋為什麼沒有值():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            rv.import_rvtools(_make_xlsx(tmp, [_vm()]), conn)
            hw = dict(_row(conn, "42301111-2222-3333-4444-555566667777"))
            assert api._vi_sdk_note(conn, hw) is None
        finally:
            conn.close()


def test_來源管理端要能被全文搜尋找到():
    """「這很常追」的東西，如果只能在單一資產頁看到，那還是要先知道是哪一台。
    反過來查（給我這座 VC 上的所有機器）才是實際用法。

    全文搜尋是通用掃表，新欄位理論上自動就被涵蓋——但「理論上」不算，
    這裡實測一次。哪天有人把它加進 EXCLUDE_COLUMNS 就會紅。
    """
    import search_terms

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            rv.import_rvtools(_make_xlsx(tmp, [
                _vm("42301111-2222-3333-4444-555566667777", VC_A, "web01"),
                _vm("42302222-2222-3333-4444-555566667777", VC_B, "web02"),
            ]), conn)
            tables = search_terms.searchable_tables(conn)
            assert any(t == "hardware" and "vi_sdk_server" in cols for t, cols in tables),                 "vi_sdk_server 沒有被列入可搜尋欄位（是不是被加進 EXCLUDE_COLUMNS 了？）"

            parsed = search_terms.parse_query(VC_A)
            hit = search_terms.scan(conn, parsed, tables, per_table=50)
            assert hit["total"] >= 1, f"搜尋 {VC_A} 找不到任何東西"
            # VC_B 上那台不該被 VC_A 撈到，不然這個查詢等於沒過濾
            hit_b = search_terms.scan(conn, parsed, [("hardware", ["vi_sdk_server"])],
                                      per_table=50)
            assert hit_b["total"] == 1, f"應該只有一台在 {VC_A}，實際 {hit_b['total']}"
        finally:
            conn.close()
