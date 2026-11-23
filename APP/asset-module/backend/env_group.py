"""環境三分類：正式／非正式／OA（2026-09-16 使用者：「環境 選正式 非正式 oa 3總」）。

## 為什麼要分類而不是直接用原值

CIA 清冊的 environment 欄實際有 7 種值（2026-09-16 於 221 正式庫查證）：
正式 3273、(空) 696、測試 695、備援 111、使用者測試(UAT) 5、OA 3、開發環境(DEV) 2。

篩選下拉列 7 個選項，人要自己記得「UAT 也是非正式」「備援也是正式」——
所以收成三類。但**原值仍然顯示在欄位上**，分類只影響篩選，不改資料。

## 歸類依據

- **正式**：`正式`、`備援`。備援是正式環境的待命機，出事一樣要處理，不是測試機
- **OA**：`OA`。辦公自動化環境，跟機房主機的作業方式不同，使用者要求單獨一類
- **非正式**：`測試`、`使用者測試(UAT)`、`開發環境(DEV)` 等

⚠️ **認不出的值也不歸進任何一類**，獨立成「未知環境」（[B-06] 2026-09-18）。

⚠️ **空白不歸進任何一類**，獨立成「未填環境」。696 台沒填環境，
把它們塞進「非正式」會讓人以為那些機器不重要；塞進「正式」又會灌水。
沒填就是沒填，要看得見才有人會去補（CLAUDE.md：預設值會漏掉誰）。
"""
from __future__ import annotations

PROD = "prod"
NONPROD = "nonprod"
OA = "oa"
UNSET = "unset"
#: [B-06] 認不出來的值獨立一類，逼人去看，不替他猜
UNKNOWN = "unknown"

LABEL = {PROD: "正式", NONPROD: "非正式", OA: "OA", UNKNOWN: "未知環境", UNSET: "未填環境"}

#: 精確比對（不做模糊比對，免得「非正式測試」這種字串被歸錯）
_PROD_VALUES = {"正式", "備援"}
_OA_VALUES = {"OA", "oa", "Oa"}

#: 這些字樣出現就算非正式。UAT／DEV 的原值帶括號，所以用包含比對
_NONPROD_HINTS = ("測試", "UAT", "DEV", "開發", "驗證", "POC", "poc")

#: 下拉選單順序。未填放最後，但一定要在（不能靜默消失）
CHOICES = (PROD, NONPROD, OA, UNKNOWN, UNSET)


def classify(environment) -> str:
    """把 environment 原值收成三類＋未知＋未填。原值仍顯示在欄位上，分類只影響篩選。"""
    v = (environment or "").strip()
    if not v:
        return UNSET
    if v in _PROD_VALUES:
        return PROD
    if v in _OA_VALUES:
        return OA
    if any(h in v for h in _NONPROD_HINTS):
        return NONPROD
    # [B-06] 認不出來的新值 → 「未知環境」。
    # 以前這裡 `return NONPROD`，註解卻寫「寧可多看一眼，不要漏掉正式機出事」——行為跟註解
    # 相反：「正式二區」「PROD-DR」這種新值不含測試字樣，會被歸成非正式，使用者篩「正式」
    # 時那批正式機就不在清單裡。歸哪一類都是猜，所以不猜：獨立一類，要人看得到才會去補。
    return UNKNOWN
