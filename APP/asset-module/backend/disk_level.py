"""磁碟使用率分級（2.044.0，需求 R17，使用者 2026-11-04 定）：全站「磁碟快滿」只有這一把尺。

* 使用率 ≥ 80%  → 「處理」（開始處理，列進清單）
* 使用率 ≥ 90%  → 「高」
* 使用率 < 80%  → 不列
* 查不到使用率   → 「未收集」（不是 0、不是不列；畫面要分得出來）

用的地方（加總＝下鑽，都呼叫這裡，不各自寫門檻）：
  · storage_capacity.alerts 的實體主機檔案系統（by=mount，SSH 收集；5.2 容量預警、1.4 追蹤處理、首頁）
  · daily_risk.hardware_tab 的健康基準磁碟空間（1.3 硬體異常）
  · disk_alert（Teams 磁碟告警）的預設門檻＝「高」那一級

邊界：比較前先四捨五入到小數一位（畫面顯示的就是一位小數），所以 79.95 → 80.0 列「處理」、79.94 → 79.9 不列；
不會出現畫面寫 80.0% 卻沒列、或寫 79.9% 卻列進來的情形。

不包含：Datastore（RVTools 的儲存池）仍用 5.2 的「剩餘 % 門檻」（storage_free_threshold_pct，管理員可調），
那是儲存池容量、不是主機磁碟，口徑另外講（見 docs 交接）。
"""
from __future__ import annotations

HANDLE_PCT = 80.0
HIGH_PCT = 90.0

HANDLE = "handle"
HIGH = "high"
UNCOLLECTED = "uncollected"
LABEL = {HANDLE: "處理", HIGH: "高", UNCOLLECTED: "未收集"}


def norm(pct) -> float | None:
    """使用率 → 一位小數；不是數字回 None（未收集）。"""
    if pct is None or isinstance(pct, bool):
        return None
    try:
        return round(float(pct), 1)
    except (TypeError, ValueError):
        return None


def level(used_pct) -> str | None:
    """使用率（%）→ high／handle／None（不列）；查不到 → uncollected。"""
    p = norm(used_pct)
    if p is None:
        return UNCOLLECTED
    if p >= HIGH_PCT:
        return HIGH
    if p >= HANDLE_PCT:
        return HANDLE
    return None


def listed(used_pct) -> bool:
    """要不要列進清單（處理或高）。未收集不列進「快滿」清單，另外計「未收集」。"""
    return level(used_pct) in (HANDLE, HIGH)


def used_from_free(free_pct) -> float | None:
    """剩餘 % → 使用率 %（一位小數）；查不到回 None。"""
    f = norm(free_pct)
    return None if f is None else round(100.0 - f, 1)
