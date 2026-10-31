"""每張表格／清單都能匯出目前畫面的結果（2.007.7）。

使用者 2026-10-27：「查詢後想匯出，系統無此功能；我要說一次你才幫我加一次」。所以匯出是全站預設，不是逐頁加：
  * 共用表格 V2Table 一律有「匯出」（目前篩選、排序後的全部列；伺服器端分頁的要給 export-all 拿全部）；
  * v2 頁面／元件裡自己畫的 <table> 只能是下面白名單裡的，而且要講清楚怎麼匯出。新頁面自己畫表又沒匯出 → 紅。
"""
import re
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"

#: 不是 V2Table 的表：檔案 → 為什麼、怎麼匯出（required＝檔案裡一定要出現的字）
RAW_TABLES = {
    "components/v2/InventoryFlow.vue": ("差異表每列有狀態按鈕（可編輯），匯出另做：exportDiffs", "exportDiffs"),
    "pages/v2/incident/[id].vue": ("檢核清單每列可編輯，匯出另做：exportTasks", "exportTasks"),
    "components/v2/HostMonthly.vue": ("編輯管理判斷時的輸入表（每格是輸入框）；閱讀模式同一份資料用 V2Table（自帶匯出）", None),
    "components/v2/RackFillSheet.vue": ("機櫃盤點網頁填寫：每格是輸入框（填結果／更正）；填完的結果在 7.1「盤點結果」用 V2Table（自帶匯出）、離線盤點檔另有下載", None),
    "components/v2/Rebuild.vue": ("系統重建資料分組折疊顯示；匯出走後端 xlsx（HostGroup「匯出系統重建資料」）", None),
}


def _files():
    yield from (FE / "pages" / "v2").rglob("*.vue")
    yield from (FE / "components" / "v2").rglob("*.vue")


def _func_body(src: str, head: str) -> str:
    i = src.index(head)
    j = src.index("\n}", i)
    return src[i:j]


def test_共用表格一律有匯出_匯出全部列():
    src = (FE / "components" / "v2" / "Table.vue").read_text(encoding="utf-8")
    assert "匯出" in src and "@click=\"doExport\"" in src
    body = _func_body(src, "async function doExport")
    # 全部列＝排序／篩選後的整份 sorted（不是只有這一頁的 shown）；伺服器端分頁的向後端拿全部
    assert "sorted.value" in body and "shown" not in body, body
    assert "props.exportAll" in body
    assert "props.cols.map(c => c.label)" in body               # 欄名＝畫面欄名


def test_v2自己畫的表格都在白名單_而且有匯出():
    bad = []
    for f in _files():
        rel = f.relative_to(FE).as_posix()
        if rel == "components/v2/Table.vue":
            continue
        s = f.read_text(encoding="utf-8")
        if "<table" not in s:
            continue
        if rel not in RAW_TABLES:
            bad.append(f"{rel}：自己畫 <table>，請改用 V2Table（自帶匯出）或加進白名單並做匯出")
            continue
        need = RAW_TABLES[rel][1]
        if need and need not in s:
            bad.append(f"{rel}：白名單要求有 {need}，找不到")
    assert not bad, "\n".join(bad)


def _v2table_tags(src: str):
    """完整的 <V2Table ...> 開頭標籤：屬性值裡會有「=>」「Record<string, string>」，要跳過雙引號裡的 >。"""
    i = 0
    while True:
        i = src.find("<V2Table", i)
        if i < 0:
            return
        j, q = i, False
        while j < len(src):
            ch = src[j]
            if ch == '"':
                q = not q
            elif ch == ">" and not q:
                break
            j += 1
        yield src[i:j + 1]
        i = j + 1


def test_伺服器端分頁的表格要給export_all():
    bad, seen = [], 0
    for f in _files():
        s = f.read_text(encoding="utf-8")
        for tag in _v2table_tags(s):
            if ":server=" in tag:
                seen += 1
                if ":export-all=" not in tag:
                    bad.append(f.relative_to(FE).as_posix())
    assert seen >= 1, "掃描器沒有抓到任何伺服器端分頁的表（2.1 主機查詢應該有）"
    assert not bad, f"伺服器端分頁沒給 export-all（只會匯出這一頁）：{bad}"
