"""Codex 派給 CC 的缺陷工單（docs/codex-review/2026-10-30/745／843／975／1193／1223／1293／1297），
在 2.063.0 現行程式上重現與守門。每張一組，正反兩面都釘（修好的行為＋不該被誤傷的行為）。"""
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
FRONT = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import capacity_archive as ar  # noqa: E402
import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import rack_layout as rl  # noqa: E402
from test_capacity_archive import D, _api, put  # noqa: E402


# ---------------------------------------------------------------- 745：父層 ref 拿得到 loadTasks
def _script_setup(src: str) -> str:
    m = re.search(r"<script setup[^>]*>(.*?)</script>", src, re.S)
    return m.group(1) if m else ""


def _expose_after_top_await(src: str) -> bool:
    """<script setup> 裡 defineExpose 出現在第一個 top-level await（行首 await／const x = await）之後。"""
    body = _script_setup(src)
    ex = re.search(r"^defineExpose\(", body, re.M)
    aw = re.search(r"^(?:await |(?:const|let) [^=\n]+= await )", body, re.M)
    return bool(ex and aw and aw.start() < ex.start())


def test_745_defineExpose_都在第一個top_level_await之前():
    bad = [str(p.relative_to(FRONT)) for p in FRONT.rglob("*.vue")
           if "node_modules" not in p.parts and ".nuxt" not in p.parts and ".output" not in p.parts
           and _expose_after_top_await(p.read_text(encoding="utf-8"))]
    assert bad == [], f"defineExpose 在 top-level await 之後，正式 build 父層 ref 會拿不到方法：{bad}"


def test_745_偵測本身會抓到錯誤順序():
    wrong = "<script setup lang=\"ts\">\nasync function f() {}\nawait f()\ndefineExpose({ f })\n</script>"
    right = "<script setup lang=\"ts\">\nasync function f() {}\ndefineExpose({ f })\nawait f()\n</script>"
    assert _expose_after_top_await(wrong) and not _expose_after_top_await(right)


def test_745_機櫃盤點上傳後刷新走InventoryFlow的expose():
    flow = (FRONT / "components" / "v2" / "InventoryFlow.vue").read_text(encoding="utf-8")
    assert re.search(r"defineExpose\(\{[^}]*\bloadTasks\b", flow)
    rack = (FRONT / "pages" / "v2" / "inventory" / "rack.vue").read_text(encoding="utf-8")
    assert "await flow.value?.loadTasks()" in rack


# ---------------------------------------------------------------- 843：整個機房印標籤不含沒機櫃的 VM
def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def test_843_機房標籤_排除型號VM但沒機櫃的_實體沒機櫃的照印(tmp_path):
    conn = _conn(tmp_path)
    try:
        def hw(serial, host, ip, rack, model, is_vm=None):
            hid = db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location="01_甲機房",
                                     rack_no=rack, device_model=model)
            if is_vm is not None:
                conn.execute("UPDATE hardware SET is_vm = ? WHERE id = ?", (is_vm, hid))
        hw("A-1", "phyA", "10.99.7.1", "M10/10-11U", "PowerEdge R750")
        hw("B-1", "vmB", "10.99.7.2", None, "VM", is_vm=1)            # Codex 843 的 B：型號 VM、沒機櫃
        hw("B-2", "vmC", "10.99.7.3", None, "(VM)")
        hw("C-1", "phyC", "10.99.7.4", None, "PowerEdge R650")        # 實體但沒寫機櫃：照印（不能誤傷）
        conn.commit()
        got = {x["asset_serial"] for x in rl.label_items(conn, room="甲機房")["items"]}
        assert got == {"A-1", "C-1"}
        assert {x["asset_serial"] for x in rl.label_items(conn, room="甲機房", rack="M10")["items"]} == {"A-1"}
    finally:
        conn.close()


# ---------------------------------------------------------------- 975：靜默窗確認帶在 query 也要認
def test_975_人工封存_窗內取消只409_確認走query會封存並留兩種稽核():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, p, api = _api(tmp, [("admin", "admin")])
        cn = db.get_connection(p)
        put(cn, day=D)
        cn.close()
        admin = client("admin")
        qw.set_clock(lambda: datetime(2026, 9, 14, 14, 0))           # 台北平日 14:00（窗內）
        try:
            r = admin.post("/api/v2/capacity/archive/run", json={"day": D})          # 按「取消」＝只有這一次
            assert r.status_code == 409 and r.json()["detail"]["code"] == qw.CONFIRM_CODE
            r = admin.post("/api/v2/capacity/archive/run?confirm_in_window=false", json={"day": D})
            assert r.status_code == 409                                              # 明講 false 不算確認
            cn = db.get_connection(p)
            ar.ensure(cn)
            assert cn.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 0
            cn.close()
            r = admin.post("/api/v2/capacity/archive/run?confirm_in_window=true", json={"day": D})   # useApi 的重送方式
            assert r.status_code == 200 and r.json()["results"][0]["result"] == "written"
            cn = db.get_connection(p)
            assert cn.execute("SELECT COUNT(*) FROM capacity_day_archive").fetchone()[0] == 1
            acts = [x[0] for x in cn.execute("SELECT action FROM activity_log")]
            assert "quiet_window_manual" in acts and "capacity_archive_run" in acts
            assert cn.execute("SELECT COUNT(*) FROM quiet_window_log WHERE entry = 'capacity_archive_tick' "
                              "AND kind = 'manual_confirmed'").fetchone()[0] == 1
            cn.close()
        finally:
            qw.set_clock(None)
            api.app.dependency_overrides.clear()


def test_975_前端重送方式就是query():
    src = (FRONT / "composables" / "useApi.ts").read_text(encoding="utf-8")
    assert "query: { ...(opts.query || {}), confirm_in_window: true }" in src


# ---------------------------------------------------------------- 1193：CIA 欄位對應停用後要能選回（⚠D4，2.064.0 已修：停用列記在 _disabled）
def test_1193_停用硬體主機名稱後_欄位對應仍看得到這列(monkeypatch, tmp_path):
    import excel_import
    import routers.imports as ri
    mp = tmp_path / "field_mapping.json"
    mp.write_bytes(excel_import.MAPPING_PATH.read_bytes())
    monkeypatch.setattr(excel_import, "MAPPING_PATH", mp)
    monkeypatch.setattr(ri, "MAPPING_PATH", mp)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client, _p, api = _api(tmp, [("admin", "admin")])
        try:
            admin = client("admin")
            fm = admin.get("/api/import/field-mapping").json()
            header = next(h for h, f in fm["硬體"].items() if f == "hostname")
            fm["硬體"][header] = "不匯入"
            assert admin.put("/api/import/field-mapping", json={"mapping": fm}).status_code == 200
            after = admin.get("/api/import/field-mapping").json()
            disabled = json.dumps(after.get("_disabled", {}).get("硬體", {}), ensure_ascii=False)
            assert header in after["硬體"] or header in disabled
        finally:
            api.app.dependency_overrides.clear()


# ---------------------------------------------------------------- 1293：A4 盤點表右邊兩欄表頭不擠
def _col_widths(src: str) -> dict[str, float]:
    return {m.group(1): float(m.group(2)) for m in re.finditer(r"col\.(c-[\w-]+)\s*\{\s*width:\s*([\d.]+)mm", src)}


def test_1293_盤點表固定欄寬_備註欄至少留40mm_不再有資產序號欄():
    src = (FRONT / "pages" / "v2" / "print" / "rack-sheet.vue").read_text(encoding="utf-8")
    cols = re.findall(r'<col class="(c-[\w-]+)">', src)
    widths = _col_widths(src)
    assert cols[-1] == "c-note" and "c-note" not in widths                      # 備註吃剩下的寬
    fixed = sum(widths[c] for c in cols[:-1])
    assert 190 - fixed >= 40, f"固定欄合計 {fixed}mm，備註只剩 {190 - fixed}mm"
    assert len(cols) == len(re.findall(r"<th[ >]", src[src.index("<thead>"):src.index("</thead>")]))   # 每欄都有表頭
    assert "資產序號" not in src[src.index("<thead>"):src.index("</thead>")]
    assert "資料<br>不符" in src                                                # 窄欄兩行，不硬擠一行


def test_1293_盤點表樣式_容器限定蓋過全站th_td的important():
    """Codex 1383：main.css 全站 `th, td { padding: 9px 12px !important }`、`th { font-size: 12px !important; color/background }`、
    `td { border-left/right/top: none !important }` 會蓋掉盤點表的密度設定（42 台 1U 縮到最小級仍印 2 頁、表身沒有直格線）。
    盤點表必須用 .sheet table.sh 限定＋!important 拿回自己的內距、表頭字級（隨密度縮）、黑字、四邊格線；全站 main.css 不動。"""
    src = (FRONT / "pages" / "v2" / "print" / "rack-sheet.vue").read_text(encoding="utf-8")
    style = re.sub(r"/\*.*?\*/", "", src[src.index("<style scoped>"):], flags=re.S)

    def rule(sel_re: str) -> str:
        m = re.search(sel_re + r"\s*\{([^}]*)\}", style)
        assert m, f"缺少容器限定規則：{sel_re}"
        return m.group(1)

    cells = rule(r"\.sheet table\.sh th, \.sheet table\.sh td")
    assert re.search(r"padding:\s*var\(--pad\)[^;]*!important", cells)            # 內距隨密度
    assert re.search(r"border:\s*1px solid currentColor\s*!important", cells)      # 表身四邊格線
    head = rule(r"\.sheet table\.sh th")
    assert re.search(r"font-size:\s*min\(var\(--fs\),\s*9pt\)\s*!important", head)  # 表頭字級隨密度縮
    assert re.search(r"color:\s*inherit\s*!important", head)                        # 黑字，不吃全站灰字
    assert re.search(r"padding-left:\s*0\s*!important", rule(r"\.sheet table\.sh \.c-box"))   # 勾選欄仍不留左右內距
    assert re.search(r"border-top:\s*2px[^;]*!important", rule(r"\.sheet table\.sh tr\.sub td"))
    # 不准用沒有容器限定的 th/td !important（那會變成在 scoped 裡改全站行為的假象），也不改 main.css 那幾行
    assert not re.search(r"(^|\})\s*(th|td)\s*[,{][^}]*!important", style)
    main = (FRONT / "assets" / "css" / "main.css").read_text(encoding="utf-8")
    assert "th, td { padding: 9px 12px !important;" in main


# ---------------------------------------------------------------- 1297：舊明細頁搬移分類不撐破窄卡片
def test_1297_搬移分類框限寬_選單與輸入框可縮():
    src = (FRONT / "pages" / "assets" / "[serial].vue").read_text(encoding="utf-8")
    style = src[src.index("<style"):]
    assert re.search(r"\.clsbox, \.movebox \{ min-width: 0; max-width: 100%; \}", style)
    assert re.search(r"\.movebox \.selin, \.movebox \.txtin \{ min-width: 0; max-width: 100%; \}", style)
    assert not re.search(r"\.movebox \.txtin \{ flex: 1 1 2\d\dpx", style)
    # 主要入口已是 V2 原生（資產明細「標籤與分類」頁籤），整列寬、不在窄卡片裡
    v2 = (FRONT / "components" / "v2" / "AssetTagsOs.vue").read_text(encoding="utf-8")
    assert 'data-test="move-submit"' in v2 and ".row { display: flex;" in v2 and "flex-wrap: wrap" in v2


# ---------------------------------------------------------------- 1223：我的最愛拖拉用路徑，不用畫面索引
def test_1223_最愛拖拉傳路徑_不傳畫面第幾個():
    lay = (FRONT / "layouts" / "default.vue").read_text(encoding="utf-8")
    assert '@dragstart="favDrag = c.to"' in lay and '@drop="favDrop(c.to)"' in lay
    assert "favDrop(i)" not in lay and "favDrag = i" not in lay
    fav = (FRONT / "composables" / "useFavorites.ts").read_text(encoding="utf-8")
    assert "function moveFav(fromTo: string, toTo: string)" in fav and "moveFavByRoute(" in fav
    # 純邏輯（隱藏路徑在前、往前往後、拖到自己）在 frontend/tests/favOrder.spec.ts（vitest）
