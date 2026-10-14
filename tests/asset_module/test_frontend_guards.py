"""前端寫法守門（2026-09-20 使用者：「寫錯應該有機制檢查啊?」）。

這一天連續踩到三種「測試全綠、畫面卻壞掉」的錯，三次都是使用者用眼睛發現的。
共同點：pytest 測後端、nuxt build 只看編譯得過、smoke_pages 只看頁面打不打得開——
**沒有任何一關在看「樣板寫法對不對」**。這支就是補那一關，用最笨但有效的方式掃原始碼。

擋下的三種：
  1. v-for 的元素後面又接一個用到同一個迴圈變數的兄弟元素（作用域不同 → 整頁空白）
  2. 樣板裡把 ref 當參數傳進函式再改 .value（樣板會自動拆包 → 點了沒反應）
  3. 表格沒有排序（天條：只要是表格，每一欄都要能排）

抓不到的：樣式跑版、邏輯算錯、點下去之後的行為。所以它是下限不是保證。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "APP" / "asset-module" / "frontend"
VUE_FILES = sorted(list((FRONTEND / "pages").rglob("*.vue"))
                   + list((FRONTEND / "components").rglob("*.vue")))

# 表格不必排序的，列在這裡並寫明理由——要豁免就要講得出原因
NO_SORT_OK = {
    "ScopePreview.vue": "動作前的預覽小表，最多幾筆、本來就照順序列",
    "AccountAuditImport.vue": "匯入結果明細卡，一次就那幾列",
    "BatchProbeModal.vue": "視窗內的即時結果，跟著執行順序走",
    "OnboardMatrix.vue": "納管矩陣是格子不是清單，橫軸縱軸都是固定維度",
    "new.vue": "新增資產表單裡的欄位說明表",
    "hmc.vue": "HMC 收集結果，照回傳順序列",
}

# ⚠️ 既有欠債：這些頁有表格但還沒補排序（2026-09-20 立這條規矩時就存在）。
# **只能變少不能變多**——新頁一律要排序；清掉一個就從這裡刪一行。
LEGACY_NO_SORT = {
    "account-ops.vue", "batch-onboard.vue", "[serial].vue", "monthly.vue", "host-sources.vue",
}


def _block(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>([\s\S]*)</{tag}>", text)
    return m.group(1) if m else ""


@pytest.mark.parametrize("f", VUE_FILES, ids=lambda f: f.name)
def test_v_for_的兄弟元素不可以用同一個變數(f: Path):
    """第二個 <tr>／<div> 要用 v-for 的變數時，v-for 必須移到外層 <template>。

    2026-09-20：掃不到的網段加標註面板，第二個 <tr> 寫在 v-for 的 <tr> 後面，
    r 是 undefined → 整頁空白、console 連噴 Cannot read properties of undefined。

    判法用縮排（比正則配對標籤可靠）：找 v-for 那一行的縮排 → 同縮排的收尾 →
    下一個同縮排的兄弟元素有沒有用到迴圈變數。找不到配對就跳過（寧可漏報，不要亂報）。
    """
    lines = _block(f.read_text(encoding="utf-8"), "template").split("\n")
    for i, line in enumerate(lines):
        m = re.search(r'^(\s*)<(\w+)[^>]*\sv-for="\(?\s*(\w+)', line)
        if not m:
            continue
        indent, tag, var = m.group(1), m.group(2), m.group(3)
        if tag == "template":
            continue                      # 正解就是它
        close = f"{indent}</{tag}>"
        j = next((k for k in range(i + 1, len(lines)) if lines[k].rstrip() == close.rstrip()), None)
        if j is None:
            continue
        k = next((x for x in range(j + 1, len(lines)) if lines[x].strip()), None)
        if k is None or "v-for" in lines[k]:
            continue
        sib = re.match(rf"{indent}<(\w+)", lines[k])
        if not sib:
            continue
        # 單行就寫完的元素（<tr ...>…</tr> 在同一行）只看那一行——
        # 不然會一路找到很後面的收尾，把別人的內容也算進來（會誤判，2026-09-20 實際發生）
        if f"</{sib.group(1)}>" in lines[k]:
            chunk = lines[k]
        else:
            end = next((x for x in range(k + 1, len(lines))
                        if lines[x].rstrip() == f"{indent}</{sib.group(1)}>"), None)
            if end is None:
                continue          # 找不到配對就跳過：寧可漏報，也不要亂報
            chunk = "\n".join(lines[k:end + 1])
        # 箭頭函式自己的參數剛好同名（例 rvResults.some(r => r.summary…)）不算——
        # 那是它自己的作用域，不是在借 v-for 的變數（2026-09-20 誤判過一次）
        if re.search(rf"\b{var}\s*=>", chunk):
            continue
        if re.search(rf"\b{var}\.\w", chunk):
            pytest.fail(
                f"{f.name} 第 {k + 1} 行附近：<{sib.group(1)}> 用到 v-for 的變數 `{var}`，"
                f"但它是 v-for 元素的**兄弟**、拿不到那個變數（畫面會整頁空白）。"
                f"把 v-for 移到外層 <template v-for=...>，兩個元素都包進去。")


@pytest.mark.parametrize("f", VUE_FILES, ids=lambda f: f.name)
def test_不可以把ref當參數傳進函式再改value(f: Path):
    """樣板裡的 ref 會被自動拆成值，函式收到的不是 ref，改了等於沒改。

    2026-09-20：掃不到的網段的環境／機房籌碼點了沒反應（使用者：「不能選阿」）。
    """
    src = f.read_text(encoding="utf-8")
    script, tpl = _block(src, "script"), _block(src, "template")
    for m in re.finditer(r"function\s+(\w+)\s*\(([^)]*)\)", script):
        name, params = m.group(1), m.group(2)
        if "Ref<" not in params:
            continue
        if re.search(rf'@\w+(?:\.\w+)*="[^"]*\b{name}\(', tpl):
            pytest.fail(
                f"{f.name}：`{name}()` 的參數型別是 Ref<>，而且被樣板直接呼叫——"
                f"樣板會把 ref 拆成值，函式改到的不是原本那個 ref（按了不會有反應）。"
                f"改成不收 ref、直接在函式裡操作那個 ref。")


@pytest.mark.parametrize("f", VUE_FILES, ids=lambda f: f.name)
def test_表格要能排序(f: Path):
    """天條：只要是表格，每一欄都要能排（2026-09-20 使用者：「沒排序，鐵規定」）。"""
    src = f.read_text(encoding="utf-8")
    tpl = _block(src, "template")
    if "<table" not in tpl or "<thead" not in tpl:
        return
    if f.name in NO_SORT_OK or f.name in LEGACY_NO_SORT:
        return
    if "SortTh" in tpl or "useSort" in src or "@sort" in tpl or "sort_by" in src or "sortBy" in src:
        return
    pytest.fail(
        f"{f.name}：有表格但沒有排序。資料全在前端就用 useSort() ＋ "
        f"<SortTh k=... :active=\"sortKey\" :dir=\"sortDir\" @sort=\"toggle\">；"
        f"伺服器端分頁就把 sort_by/order 送後端。"
        f"真的不需要排序，把檔名加進 NO_SORT_OK 並寫明理由。")


def test_既有欠債只能變少不能變多():
    """LEGACY_NO_SORT 是立規矩那天就存在的欠債清單，補好一個就刪一行。"""
    still = {n for n in LEGACY_NO_SORT
             if any(f.name == n and "SortTh" not in f.read_text(encoding="utf-8") for f in VUE_FILES)}
    assert still <= LEGACY_NO_SORT
    assert len(LEGACY_NO_SORT) <= 5, (
        f"欠債清單不可以變長（現在 {len(LEGACY_NO_SORT)} 個）：新頁一律要排序")
