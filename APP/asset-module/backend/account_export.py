"""帳號盤點匯出：標準格式與全匯出。

使用者 2026-08-28 給了公司現行的 Excel 欄位（18 欄），要兩種匯出：
  1. **標準帳號盤點** —— 就是那 18 欄，可以直接交出去
  2. **全匯出** —— 系統知道的全部欄位

## type_id 只自動填「確定的」

公司的分類是**用途**（程式運行／資料庫運行／自動化盤點／Anchor·APPM／人員使用），
而 `/etc/passwd` 只看得到「shell 是什麼、uid 多少、叫什麼名字」。3、4、6 三類
從系統面看起來一模一樣：都是 nologin、低 uid、叫不出名字。

使用者 2026-08-28 明講：「目前還沒討論出一個邏輯，等到有邏輯之後我們再修改程式。
在還沒有修改程式之前，都要人工判斷。」

所以只自動填四種確定的，其餘留空由人在 Excel 裡填。**留空不好看，但填錯的代價是
稽核文件上的假資料**——而且沒有人會發現，因為那一欄看起來有值。

## 欄位對照的依據

`department` 與 `ap_department` 一度搞混。2026-08-28 用真實資料查證：

    inventory_division    3643 筆全部 = 資訊管理處    <- 單一值
    inventory_department  3643 筆全部 = 資訊架構部    <- 單一值
    usage_unit            資訊架構部 1418／數位平台部 471／專案開發部 420…

對照使用者的範例（department=資訊管理處資訊XX部、ap_department=金融XX資訊部）：
`department` 是「處＋部」串起來的**我們自己部門**；`ap_department` 是**每台不同**的
AP 單位。所以 ap_department 不能用 inventory_department——那一欄 3643 筆全一樣，
用它的話所有機器的 AP 部門會變成同一個值，通知會全部寄回我們自己這裡。
"""
from __future__ import annotations

import sqlite3

#: 公司代碼表（使用者 2026-08-28 提供）。3／4／6 需要人工判斷，這裡不猜。
TYPE_INFO = {
    1: "最高權限帳號",
    2: "系統預設",
    3: "程式運行帳號",
    4: "資料庫運行帳號",
    5: "自動化盤點帳號",
    6: "Anchor和APPM納管帳號",
    7: "人員使用帳號",
}

#: 我們自己佈的收集帳號——這個確定是 5（自動化盤點）。
#: 名稱跟著設定走：管理者可能把收集身分換成別的帳號。
_OUR_SCAN_ACCOUNTS = {"webit3scan", "webit3sc"}


def classify_type(acc: dict, scan_accounts: set[str] | None = None) -> int | None:
    """回公司代碼表的 type_id；判不出來回 None（**不猜**）。

    只認四種確定的：
      1 uid=0、2 已知內建、5 我們自己的收集帳號、7 有 shell 的一般帳號。
    3（程式運行）／4（資料庫運行）／6（Anchor·APPM）從 /etc/passwd 分不出來，
    使用者也明講目前沒有邏輯、要人工判斷——留空讓人填，不要填一個看起來對的值。
    """
    scan = scan_accounts or _OUR_SCAN_ACCOUNTS
    if acc.get("uid") == 0:
        return 1
    if (acc.get("username") or "") in scan:
        return 5
    kind = acc.get("kind")
    if kind in ("default", "builtin"):
        return 2
    if kind == "human":
        return 7
    # mgmt（sysinfra 這類標準管理帳號）與 service（叫不出名字的）：
    # 可能是 3／4／5／6，分不出來。留空。
    return None


#: 標準帳號盤點的欄位順序（使用者提供的公司現行格式）
STANDARD_COLUMNS = [
    "system_id", "system", "ap_department", "ap_owner",
    "hostname", "ip_addr", "username", "password", "uid", "gid",
    "gecos", "home", "shell", "type_id", "type_info",
    "department", "owner", "login_status",
]


def _department(hw: dict) -> str:
    """處＋部串起來，就是使用者範例的「資訊管理處資訊XX部」。"""
    return f"{hw.get('inventory_division') or ''}{hw.get('inventory_department') or ''}"


def standard_rows(conn: sqlite3.Connection,
                  ap_department_fallback: str = "usage_unit") -> tuple[list[dict], dict]:
    """組出標準格式的每一列，並回一份對帳摘要。

    `ap_department_fallback`：對照表沒給 AP 部門時退回哪個機器欄位。預設
    `usage_unit`——那是唯一每台不同的部門欄（見模組 docstring 的查證）。
    做成參數是因為這個對照使用者還沒最終拍板，改一個字就能換，不用動程式。
    """
    import business_system

    rows = conn.execute(
        "SELECT a.*, h.hostname, h.api_id, h.usage_unit, h.user_name, h.custodian, "
        "       h.inventory_division, h.inventory_department "
        "FROM host_account a LEFT JOIN hardware h ON h.ip = a.ip "
        "WHERE a.gone_at IS NULL "
        "ORDER BY h.api_id, a.ip, a.uid").fetchall()

    out: list[dict] = []
    unclassified = 0
    no_system = 0
    for r in rows:
        acc = dict(r)
        biz = business_system.lookup(conn, acc.get("api_id"))
        if not biz["found"]:
            no_system += 1
        tid = classify_type(acc)
        if tid is None:
            unclassified += 1
        out.append({
            "system_id": acc.get("api_id") or "",
            "system": biz["name"] or "",
            "ap_department": biz["ap_department"] or acc.get(ap_department_fallback) or "",
            "ap_owner": biz["ap_owner"] or acc.get("user_name") or "",
            "hostname": acc.get("hostname") or "",
            "ip_addr": acc.get("ip") or "",
            "username": acc.get("username") or "",
            # /etc/passwd 第 2 欄固定是 x（真值在 shadow）。照抄公司格式。
            "password": "x",
            "uid": acc.get("uid"),
            "gid": acc.get("gid"),
            "gecos": acc.get("gecos") or "",
            "home": acc.get("home") or "",
            "shell": acc.get("shell") or "",
            "type_id": tid if tid is not None else "",
            "type_info": TYPE_INFO.get(tid, "") if tid is not None else "",
            "department": _department(acc),
            "owner": acc.get("custodian") or "",
            # can_login 是 NULL 代表沒採集到，不是「無法登入」——三態要分開
            "login_status": ("可登入" if acc.get("can_login") == 1
                             else ("無法登入" if acc.get("can_login") == 0 else "未採集")),
        })

    return out, {
        "rows": len(out),
        # 這兩個數字是這份匯出「還差多少才完整」的答案。只給列數等於沒回答。
        "unclassified_type": unclassified,      # 要人工填 type_id 的
        "rows_without_system_name": no_system,  # 對照表查不到系統名稱的
    }


def full_rows(conn: sqlite3.Connection) -> tuple[list[dict], list[str]]:
    """全匯出：`host_account` 全欄位 ＋ 機器與業務系統的脈絡欄位。

    這個是給自己人查的，不是給稽核的——所以不做欄位最小化，把知道的都吐出來。
    """
    import business_system

    rows = conn.execute(
        "SELECT a.*, h.hostname, h.api_id, h.environment, h.usage_unit, h.user_name, "
        "       h.custodian, h.inventory_division, h.inventory_department, "
        "       h.asset_serial AS hw_asset_serial, h.os AS hw_os "
        "FROM host_account a LEFT JOIN hardware h ON h.ip = a.ip "
        "ORDER BY a.ip, a.uid").fetchall()

    out = []
    for r in rows:
        acc = dict(r)
        biz = business_system.lookup(conn, acc.get("api_id"))
        acc["system_name"] = biz["name"] or ""
        acc["system_lookup"] = biz["reason"] or "對得到"
        tid = classify_type(acc)
        acc["type_id"] = tid if tid is not None else ""
        acc["type_info"] = TYPE_INFO.get(tid, "") if tid is not None else "待人工判斷"
        out.append(acc)

    cols = list(out[0].keys()) if out else []
    return out, cols
