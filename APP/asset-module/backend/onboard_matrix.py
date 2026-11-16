"""OS × 環境 的納管狀態交叉表（2026-09-16 使用者：「OS 為列、環境為欄，格子放納管」）。

## 為什麼要有

「還有 5244 台要處理」是一個數字，但它不告訴人**該從哪裡下手**。
補佈納管是按 OS 分批做的（Linux 一套腳本、Windows 一套、AIX 又一套），
而能不能動又要看環境（正式要排維護時間、測試可以隨時來）。
所以「Linux × 測試」這一格有多少台還沒納管，才是可以直接排進行程的單位。

## 資料從哪來

直接吃 `pipeline.summarize()` 的逐台結果，理由有二：

1. 那份**已經照主機算**（同主機名＋IP 的重複登記收成一台），
   數字才不會跟漏斗頁對不起來——同一個系統裡兩個地方講不同的數字，人就不信了
2. 狀態判定（已納管／已收集／非納管設備／已退役／未涵蓋／失聯）也在那裡，
   不要在這裡再判一次，判準漂走比數字錯更難查

## 每一格放什麼

不是只放一個數字。一格裡要分得出：

- `onboarded` 已納管、`collected` 已收集（設備）——這兩種都算「顧得到」
- `needs_action` 還要處理的（未登記／未納管／失聯／未涵蓋）
- `excluded` 不需處理的（已退役／非納管設備）——**不算分母裡的問題**

只給「已納管 12」看不出 12 是多還是少；給「12/45」才知道還有 33 台要做。
而把退役與豁免算進分母，會讓永遠達不到 100%，那個比率就沒人看了。
"""
from __future__ import annotations

import env_group
import manage_state

#: 「顧得到」的狀態：已納管、已收集（設備）
OK_STATES = (manage_state.ONBOARDED, manage_state.COLLECTED)
#: 不需處理、也不該算進分母的狀態
EXCLUDED_STATES = (manage_state.RETIRED, manage_state.EXEMPT)

#: OS 列的顯示順序。未知放最後——它是「還不知道」，不是一種 OS
from pipeline import OS_ORDER  # [B-02] 列順序跟漏斗同一份，不各寫一套


def _blank_cell() -> dict:
    return {"total": 0, "onboarded": 0, "collected": 0, "needs_action": 0,
            "excluded": 0, "by_stage": {}}


def build(conn) -> dict:
    """回 {rows, cols, cells, row_totals, col_totals, grand}。

    cells 是 {os_type: {env_key: cell}}；cell 見模組說明。
    """
    import pipeline

    data = pipeline.summarize(conn)
    items = data.get("items") or []

    cols = [{"key": k, "label": env_group.LABEL[k]} for k in env_group.CHOICES]
    col_keys = [c["key"] for c in cols]

    cells: dict[str, dict[str, dict]] = {}
    seen_os: list[str] = []
    for it in items:
        os_type = it.get("os_type") or "未填"
        env_key = it.get("env_group") or env_group.UNSET
        if env_key not in col_keys:
            env_key = env_group.UNSET
        if os_type not in cells:
            cells[os_type] = {k: _blank_cell() for k in col_keys}
            seen_os.append(os_type)
        cell = cells[os_type][env_key]
        cell["total"] += 1
        stage = it.get("stage_label") or it.get("stage") or "?"
        cell["by_stage"][stage] = cell["by_stage"].get(stage, 0) + 1

        state = _state_of(it)
        if state in OK_STATES:
            cell["onboarded" if state == manage_state.ONBOARDED else "collected"] += 1
        elif state in EXCLUDED_STATES:
            cell["excluded"] += 1
        else:
            cell["needs_action"] += 1

    rows = [o for o in OS_ORDER if o in cells] + [o for o in seen_os if o not in OS_ORDER]

    row_totals = {o: _sum_cells([cells[o][k] for k in col_keys]) for o in rows}
    col_totals = {k: _sum_cells([cells[o][k] for o in rows]) for k in col_keys}
    grand = _sum_cells([row_totals[o] for o in rows])
    return {
        "rows": rows,
        "cols": cols,
        "cells": cells,
        "row_totals": row_totals,
        "col_totals": col_totals,
        "grand": grand,
        "scan_time": data.get("scan_time"),
        # 照主機算：跟漏斗頁同一份資料，收起了幾筆重複登記也一併講清楚
        "duplicates_merged": data.get("duplicates_merged", 0),
    }


def _state_of(item: dict) -> str:
    """把漏斗的關卡對回納管狀態。關卡是狀態的細分，這裡只要粗分四類。"""
    stage = item.get("stage")
    if stage == "retired":
        return manage_state.RETIRED
    if stage == "exempt":
        return manage_state.EXEMPT
    if stage == "collected":
        return manage_state.COLLECTED
    # onboarded_stale／no_facts／no_services／no_accounts／complete 都代表「收得到」
    if stage in ("complete", "no_facts", "no_services", "no_accounts", "onboarded_stale"):
        return manage_state.ONBOARDED
    return manage_state.NOT_ONBOARDED      # 其餘都算還要處理


def _sum_cells(cells: list[dict]) -> dict:
    out = _blank_cell()
    for c in cells:
        for k in ("total", "onboarded", "collected", "needs_action", "excluded"):
            out[k] += c[k]
        for s, n in c["by_stage"].items():
            out["by_stage"][s] = out["by_stage"].get(s, 0) + n
    return out


def coverage(cell: dict) -> float | None:
    """顧得到的比率。分母不含退役與豁免——把它們算進去會讓比率永遠到不了 100%。

    分母是 0（這一格根本沒有機器）回 None，不要回 0%——
    「沒有機器」跟「有機器但一台都沒納管」是完全不同的兩件事。
    """
    base = cell["total"] - cell["excluded"]
    if base <= 0:
        return None
    return round((cell["onboarded"] + cell["collected"]) / base * 100, 1)


EXPORT_HEADERS = ["OS", "環境", "總台數", "已納管", "已收集（設備）", "還要處理",
                  "不需處理（退役／非納管）", "納管率%"]


def export_rows(conn) -> list[list]:
    m = build(conn)
    out = []
    for os_type in m["rows"]:
        for col in m["cols"]:
            c = m["cells"][os_type][col["key"]]
            if not c["total"]:
                continue
            out.append([os_type, col["label"], c["total"], c["onboarded"], c["collected"],
                        c["needs_action"], c["excluded"], coverage(c)])
    return out
