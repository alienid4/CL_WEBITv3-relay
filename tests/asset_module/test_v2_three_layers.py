"""每一頁分三層：操作／設定／診斷不可混在一起（2026-11-14 使用者鐵律，見 CLAUDE.md「每一頁分三層」）。

使用者原話：「B1 版好不容易改善，B2 版又開始有這個問題」「這改幾回合又歪了」。光寫規則擋不住，所以釘成測試：
v2 的操作頁（不是系統設定、使用手冊、開發協作、列印）不可以新出現：
  - 設定表單（v-model 綁設定物件、「儲存設定」）
  - 示範資料按鈕／切換
  - 開發筆記（「使用者 YYYY-MM-DD…」「拍板」「第二期」這類開發歷史）
現況已有的違規列在 KNOWN（來自 AI/畫面盤點_三層分離_2026-11-14 清單），**只能減不能加**：
  - 新出現的違規 → 紅
  - KNOWN 裡某項已經改掉了卻沒從 KNOWN 刪 → 也紅（逼名單縮小，避免名單變成永久豁免）
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"

# 不算操作頁：系統設定本身、手冊、開發協作、列印頁。
NOT_OPERATE_PAGES = {"pages/v2/settings.vue", "pages/v2/manual.vue", "pages/v2/devkit.vue"}
NOT_OPERATE_PREFIX = ("pages/v2/print/",)

RULES = {
    "settings_form": [
        # 綁設定物件的欄位（建立事件、新增憑證這類「新增表單」用 form.，不算設定）
        re.compile(r'v-model(?:\.number|\.trim)?="(?:cfg|settings|setting|conf|thr|thresholds)\.'),
        re.compile(r"儲存設定|門檻設定|設定已儲存"),
    ],
    "demo_data": [
        re.compile(r"示範資料"),
    ],
    "dev_note": [
        re.compile(r"使用者\s*20\d\d-\d\d-\d\d"),
        re.compile(r"使用者\s*\d{1,2}-\d{1,2}\s*(確認|拍板|說)"),
        re.compile(r"拍板"),
        re.compile(r"第二期"),
    ],
}


def _settings_components() -> set[str]:
    """系統設定頁用到的元件＝設定層，不算操作頁。"""
    s = (FE / "pages" / "v2" / "settings.vue").read_text(encoding="utf-8")
    names = set(re.findall(r"<(V2[A-Z][A-Za-z0-9]*)", s)) | set(re.findall(r"<([A-Z][A-Za-z0-9]*(?:Settings|Notify[A-Za-z]*|Bar))\b", s))
    out = set()
    for n in names:
        stem = n[2:] if n.startswith("V2") else n
        for cand in (FE / "components" / "v2" / f"{stem}.vue", FE / "components" / f"{n}.vue", FE / "components" / f"{stem}.vue"):
            if cand.exists():
                out.add(cand.relative_to(FE).as_posix())
    return out


def _template(text: str) -> str:
    """只看 <template> 區：script 裡的變數名、註解不算畫面上的字。HTML 註解也拿掉。"""
    m = re.search(r"<template>(.*)</template>", text, re.S)
    t = m.group(1) if m else ""
    return re.sub(r"<!--.*?-->", "", t, flags=re.S)


def _operate_files() -> list[str]:
    exempt = _settings_components()
    files = []
    for p in sorted((FE / "pages" / "v2").rglob("*.vue")) + sorted((FE / "components" / "v2").glob("*.vue")):
        rel = p.relative_to(FE).as_posix()
        if rel in NOT_OPERATE_PAGES or rel.startswith(NOT_OPERATE_PREFIX) or rel in exempt:
            continue
        files.append(rel)
    return files


def scan() -> dict[tuple[str, str], int]:
    found: dict[tuple[str, str], int] = {}
    for rel in _operate_files():
        t = _template((FE / rel).read_text(encoding="utf-8"))
        for kind, pats in RULES.items():
            n = sum(len(p.findall(t)) for p in pats)
            if n:
                found[(rel, kind)] = n
    return found


# 現況違規（2026-11-14 夜盤點時的數量）。改掉一處就把數字降下來；降到 0 就刪掉那一行。只能減不能加。
KNOWN: dict[tuple[str, str], int] = {
    ('components/v2/EosFollow.vue', 'demo_data'): 1,     # 只剩看示範資料時的警示橫幅（入口與產生／清除已搬到 ⚙ 系統設定）
    ('components/v2/HostOps.vue', 'demo_data'): 3,
    ('components/v2/NightAlerts.vue', 'demo_data'): 1,
    ('components/v2/PowerVm.vue', 'demo_data'): 2,
    ('pages/v2/capacity.vue', 'demo_data'): 1,
    ('pages/v2/capacity.vue', 'settings_form'): 1,
    ('pages/v2/ciafix.vue', 'settings_form'): 1,
    ('pages/v2/ciareg.vue', 'settings_form'): 1,
}


def test_沒有新的混層():
    found = scan()
    new = {k: v for k, v in found.items() if v > KNOWN.get(k, 0)}
    assert not new, (
        "操作頁出現了設定表單／示範資料／開發筆記（每一頁分三層，見 CLAUDE.md）。"
        "設定搬到 ⚙ 系統設定、示範資料搬到使用手冊、開發筆記刪掉：\n"
        + "\n".join(f"  {f} [{k}] {v} 處（名單允許 {KNOWN.get((f, k), 0)}）" for (f, k), v in sorted(new.items()))
    )


def test_名單只能縮小():
    found = scan()
    stale = {k: v for k, v in KNOWN.items() if found.get(k, 0) < v}
    assert not stale, (
        "這些已經改好了，請把 KNOWN 的數字降下來（或刪掉那一行），名單只能縮小：\n"
        + "\n".join(f"  {f} [{k}] 名單 {v} → 現在 {found.get((f, k), 0)}" for (f, k), v in sorted(stale.items()))
    )


def test_每日檢查是樣板_零違規():
    found = scan()
    bad = {k: v for k, v in found.items() if k[0] == "pages/v2/dailycheck.vue"}
    assert not bad, f"1.5 每日檢查是三層分離的樣板，不可有任何混層：{bad}"


def test_掃描本身有效():
    """防止掃描範圍被改壞而永遠綠：至少要掃到 dailycheck、hosts，而且不掃 settings。"""
    files = set(_operate_files())
    assert "pages/v2/dailycheck.vue" in files and "pages/v2/hosts.vue" in files
    assert "pages/v2/settings.vue" not in files
    assert len(files) > 40
