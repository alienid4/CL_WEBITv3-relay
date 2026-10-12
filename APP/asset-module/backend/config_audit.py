"""組態檢核（選單 9-2）：吃各平台檢核腳本產出的 .txt，解析、存放、統計。

上游腳本：
  * Linux   `DATA/fcbrhelsh`      61 條，TWGCB-01-012 / FCB-01-002
  * AIX     `DATA/fcb_aixV###.ksh` 98 條，CIS AIX 7.2 Benchmark，ID 為 FCB-AIX-xxxx
                                  （`fcbaixsh` -> `fcb_aix.ksh` -> `fcb_aixV001.ksh`；
                                   檔名帶流水號，腳本內容一改就遞增，所以這裡寫 V###）
  * Windows 檢核表 v4.0           CSCB-01-2025-xxxx

三件在寫之前先查過、跟直覺不一樣的事（不查就會整批算錯）：

1. **欄數各平台不同。** Linux 6 欄、**Windows 7 欄**（多一個「角色」夾在 ID 與
   檢查結果中間）、**AIX 自 2026-09-30 起 7 欄**（多一個「參考來源」在原則設定名稱
   與標準設定值中間）。照位置取第 2 欄當結果，Windows 每一列都會讀成 `Common`。
   所以一律**照欄位標題取值**，不照位置——新增欄位才不會把既有平台弄壞。
2. **Windows 檔有 UTF-8 BOM。** 不剝掉的話第一行變成 `﻿系統組態檢查`，
   平台判不出來，會被當成格式錯誤整份退掉。
3. **只有 AIX 那支腳本會寫 `Check summary:` 收尾。** Linux／Windows 兩支都沒有。
   所以「沒有結尾就拒收」這條規則**只能對有完成標記的平台用**；沒有標記的平台
   要如實標成「無法確認是否截斷」，不可以假裝檔案一定完整，也不可以整份退掉。

其餘守則（B-15）：

* `Error`（沒查到）是**獨立第三態**，不准併進 Compliant，也不准併進 Non-Compliant
* 合規率分母 = 總數 − Not-Applicable − Error，**算式要跟著數字一起給畫面**
* 平台看表頭的 `平台:`，**不看檔名**——檔名可以被改
* 對不回資產的主機要列得出來，不可以靜默丟掉
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime

#: 四種結果。Error 是「沒查到」——指令不存在、檔案讀不到、權限不足。
#: 它既不是合規也不是不合規，混進任何一邊都會讓報表說謊。
COMPLIANT = "Compliant"
NON_COMPLIANT = "Non-Compliant"
NOT_APPLICABLE = "Not-Applicable"
ERROR = "Error"
RESULTS = (COMPLIANT, NON_COMPLIANT, NOT_APPLICABLE, ERROR)

RUN_SQL = """
CREATE TABLE IF NOT EXISTS config_audit_run (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name     TEXT,
    imported_by   TEXT,
    imported_at   TEXT NOT NULL,
    status        TEXT NOT NULL,      -- importing / ready / failed
    platform      TEXT,               -- Linux / AIX / Windows（讀表頭，不看檔名）
    hostname      TEXT,
    ip            TEXT,
    os_version    TEXT,
    kernel        TEXT,
    script_version TEXT,
    baseline      TEXT,               -- 檢核基準（Windows／AIX 有寫，Linux 沒有）
    runas         TEXT,               -- 執行身分；非 root 會有大量 Error
    checked_at    TEXT,               -- 表頭的 Check stated at（不是匯入時間）
    ended_at      TEXT,               -- Check ended at；沒有就是 NULL
    completeness  TEXT,               -- 完整 / 截斷 / 無完成標記
    warning       TEXT,               -- 表頭的 Check warning（例如「本機不是 AIX」）
    probes        TEXT,               -- 表頭的 Check probes（腳本當下量到的事實）
    asset_serial  TEXT,               -- 對回資產清冊；對不到為 NULL
    match_by      TEXT,
    total         INTEGER DEFAULT 0,
    compliant     INTEGER DEFAULT 0,
    non_compliant INTEGER DEFAULT 0,
    not_applicable INTEGER DEFAULT 0,
    error         INTEGER DEFAULT 0,
    note          TEXT,
    raw           TEXT                -- 原始檔全文，匯出給 DYN 時逐字吐回
)
"""

ITEM_SQL = """
CREATE TABLE IF NOT EXISTS config_audit_item (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    INTEGER NOT NULL,
    seq       INTEGER,
    item_id   TEXT,                   -- TWGCB-01-012-0066 / FCB-AIX-0001 / CSCB-...
    ref_id    TEXT,                   -- 上游基準的條號，例如 CIS 的 4.1.1.1
    note      TEXT,                   -- 備註：控制項對應、TWGCB 交叉參照、經核准之例外
    expected  TEXT,                   -- 建議值（該設成什麼），與「說明」分開
    role      TEXT,                   -- Windows 專有；其他平台為 NULL
    result    TEXT,
    category  TEXT,
    name      TEXT,
    standard  TEXT,
    current   TEXT
)
"""

IDX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_cai_run ON config_audit_item(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_car_host ON config_audit_run(hostname, ip)",
)

#: 表頭鍵 → 欄位。`Check stated at` 的拼字是上游腳本原本就這樣寫，
#: 不要「順手改成 started」——改了就對不上既有檔案。

#: 這些平台的腳本**保證**跑完會寫 `Check summary:` 收尾（腳本檔頭自己寫的保證）。
#: 所以對它們來說「沒有收尾」＝腳本中途死掉，不是「這個平台沒有完成標記」。
#: Linux／Windows 兩支腳本目前不寫這一行，不能用同一條規則擋，只能如實標明。
_GUARANTEES_SUMMARY = {"AIX"}

_HEADER = {
    "平台": "platform",
    "主機名": "hostname",
    "IP地址": "ip",
    "作業系統版本": "os_version",
    "核心版本": "kernel",
    "腳本版本": "script_version",
    "檢核基準": "baseline",
    "Check stated at": "checked_at",
    "Check runas": "runas",
    "Check ended at": "ended_at",
    "Check warning": "warning",
    # 腳本自己量到的事實（2026-09-24）。用來回答「這份報表的檢查有沒有在工作」，
    # 例如 showmount 的離開碼、ps -ef 實際幾欄。
    # ⚠️ 現行 fcb_aixV###.ksh **還沒有**這一行——那是刻意的：那支腳本至今零真機執行，
    # 在拿到第一份真機輸出之前凍結，不再動它。所以現在匯入 AIX 檔一律會顯示
    # 「沒有自我檢查資訊」，那正是現況的實話。
    "Check probes": "probes",
}

#: 哪些平台的腳本**應該**提供 `Check probes:`。沒提供就要講出來。
#: Linux／Windows 兩支腳本沒有這個機制，對它們不適用——
#: 對不適用的平台顯示「缺少自我檢查資訊」只是雜訊。
_EXPECTS_PROBES = {"AIX"}

#: 欄位標題 → 內部欄名。各平台欄數不同，靠這張表對，不靠位置。
_COLUMN = {
    "twgcb-id": "item_id", "id": "item_id",
    "角色": "role",
    "檢查結果": "result",
    "類別": "category",
    "原則設定名稱": "name",
    # 2026-09-30 起 AIX 那支腳本把原本埋在「標準設定值」裡的三種參考各自抽成
    # 獨立欄。三條軸線不一樣，不可以合併看：
    #   CIS基準條號  = CIS AIX 7 Benchmark（這個參數該設什麼），查對用的鍵
    #   CIS控制項    = CIS Critical Security Controls v8（這件事屬於哪一類控制），
    #                  值少、很多條共用 -> 這才是能拿來篩選的維度
    #   本行對應條號 = 本行自己的 FCB-01-002-xxxx / TWGCB-01-012-xxxx
    # 舊報告沒有這些欄，條號仍埋在「標準設定值」裡——兩種都要吃得下，
    # 見 parse() 的 ref_id 與 note。
    "參考來源": "ref_src",
    "cis基準條號": "ref_src",
    "cis控制項": "ctl_src",
    "本行對應條號": "org_src",
    # 給操作者的三欄（2026-09-30）：怎麼確認、怎麼改、什麼時候生效。
    # 「生效方式」是受控詞彙（立即生效／下次登入生效／需重啟服務：x／需重開機／
    # 需 bosboot + 重開機／未確認），因為「要重開機的有幾條」是排維護窗口要的數字，
    # 自由文字問不出來。
    "查核指令": "verify_cmd",
    "修正指令": "fix_cmd",
    "生效方式": "apply_effect",
    # 2026-10-01：修正指令是「貼上就能跑」還是「給人看的步驟」。
    # 畫面上的複製鈕只該出現在前者——按鈕做不到它承諾的事，比沒有按鈕更糟。
    "修正指令類型": "fix_kind",
    "標準設定值": "standard",
    "目前值": "current",
}

_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

#: 基準條號。AIX 那支腳本把 CIS 條號寫在「標準設定值」欄末，形如
#: `[CIS AIX 7.2 v1.0.0 §4.1.1.1]`。
#:
#: 2026-09-21 使用者看畫面後問「這不是採用 CIS?」——因為第一欄顯示的是我們自己的
#: FCB-AIX-0001，CIS 條號埋在長長的標準設定值裡，等於藏起來了。基準是 CIS 就該讓
#: CIS 條號看得到，否則稽核要一條一條翻。所以拆成獨立欄位。
_REF = re.compile(r"§\s*([^\]）)（(]+)")


def split_fields(standard: str) -> tuple[str, str, str]:
    """把「標準設定值」一欄拆成（說明, 建議值, 備註）。

    腳本組出來的字串長這樣（順序固定）：

        <說明句子>，( 建議值 ) [CIS AIX 7.2 v1.0.0 §4.2.5] [Controls v8 #4 …] (對應 TWGCB-…)

    所以**從右邊往左剝**：

      * `[...]` 一律是備註（條號、控制項對應）
      * `(...)` 要看它前面是什麼——
        前面是逗號 → 那是句子把值交出來，**這組就是建議值，剝到這裡停**；
        否則是補充語（例如「(對應 TWGCB-…)」「(包含 5)」），歸備註、繼續往左剝

    2026-09-21 先前的寫法是把**所有**括號都當備註抽走，結果建議值在
    split_expected 跑到之前就被拿光了，98 條全部沒有建議值，說明還留一個孤兒逗號。
    從右往左剝才對得起這個字串的實際結構。

    判不出建議值就留白——**給錯的建議值比沒有更糟**，人會照著錯的去改設定。
    """
    text = (standard or "").strip()
    notes: list[str] = []
    expected = ""
    while True:
        m = re.search(r"\[([^\[\]]*)\]\s*$", text)
        if m:
            notes.append(m.group(1).strip())
            text = text[: m.start()].rstrip()
            continue
        m = re.search(r"[（(]([^（()）]*)[)）]\s*$", text)
        if not m:
            break
        head = text[: m.start()].rstrip()
        if head.endswith(("，", ",", "、", "：", ":")):
            expected = m.group(1).strip()
            text = head.rstrip("，,、：: 　")
            break
        notes.append(m.group(1).strip())
        text = head
    # 條號那段已經有獨立欄位，不重複放進備註
    notes = [n for n in notes if n and chr(167) not in n]
    return text.strip(), expected, "；".join(reversed(notes)).strip()


def split_standard(standard: str) -> tuple[str, str]:
    """相容舊呼叫端：回（說明, 備註）。"""
    d, _e, n = split_fields(standard)
    return d, n


def split_expected(desc: str) -> tuple[str, str]:
    """相容舊呼叫端與測試：回（建議值, 說明）。"""
    d, e, _n = split_fields(desc)
    return e, d


def extract_ref(standard: str) -> str | None:
    """從標準設定值取出基準條號；取不到回 None（不可以塞空字串假裝有）。"""
    m = _REF.search(standard or "")
    if not m:
        return None
    return m.group(1).strip() or None


def _join_note(*parts: str) -> str:
    """把新格式那幾個獨立欄拼成備註。空的、以及腳本填的「查無對應」字樣都不收——
    「查無對應」在它自己那一欄講清楚就好，塞進備註只是雜訊。"""
    out = [p.strip() for p in parts if p and p.strip()]
    out = [p for p in out if "查無對應" not in p]
    return "；".join(out)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(RUN_SQL)
    conn.execute(ITEM_SQL)
    # 既有資料庫補欄位：CREATE TABLE IF NOT EXISTS 不會幫已存在的表加欄，
    # 漏掉這段的話已部署的機器會在 INSERT 時炸「no such column」。
    run_cols = {r[1] for r in conn.execute("PRAGMA table_info(config_audit_run)")}
    if "probes" not in run_cols:
        conn.execute("ALTER TABLE config_audit_run ADD COLUMN probes TEXT")
    have = {r[1] for r in conn.execute("PRAGMA table_info(config_audit_item)")}
    if "ref_id" not in have:
        conn.execute("ALTER TABLE config_audit_item ADD COLUMN ref_id TEXT")
    if "note" not in have:
        conn.execute("ALTER TABLE config_audit_item ADD COLUMN note TEXT")
    if "expected" not in have:
        conn.execute("ALTER TABLE config_audit_item ADD COLUMN expected TEXT")
    rhave = {r[1] for r in conn.execute("PRAGMA table_info(config_audit_run)")}
    if "raw" not in rhave:
        conn.execute("ALTER TABLE config_audit_run ADD COLUMN raw TEXT")
    for sql in IDX_SQL:
        conn.execute(sql)


def _decode(blob: bytes) -> str:
    """UTF-8 優先，再退 cp950（Windows 那支腳本可能用 ANSI 存）。BOM 一定要剝。"""
    for enc in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            return blob.decode(enc)
        except UnicodeDecodeError:
            continue
    return blob.decode("utf-8", errors="replace")


def _is_column_header(line: str) -> bool:
    """欄位標題列：含分號、而且有「檢查結果」。不靠「開頭是 TWGCB-ID」——
    AIX 那份第一欄放的是 FCB-AIX，未來平台也可能不一樣。"""
    return ";" in line and "檢查結果" in line


def parse(blob: bytes) -> dict:
    """解析成 {header, columns, items, completeness}。解析不出來就丟例外，不回半套。"""
    text = _decode(blob).replace("\r\n", "\n").replace("\r", "\n")
    header: dict[str, str] = {}
    columns: list[str] = []
    items: list[dict] = []
    summary_line = ""
    seq = 0

    for raw in text.split("\n"):
        line = raw.rstrip()
        if not line:
            continue
        # 註解行（測資檔開頭會有）直接略過
        if line.lstrip().startswith("#"):
            continue

        if not columns:
            if _is_column_header(line):
                for part in line.split(";"):
                    columns.append(_COLUMN.get(part.strip().lower(), part.strip()))
                # 第一欄一律是編號，不管它叫什麼。
                # 現行三支腳本都寫 `TWGCB-ID`，但 AIX 那份的 ID 其實是 FCB-AIX-xxxx，
                # 哪天有人把標題改成 FCB-AIX-ID 就對不到了——那時每一列都會被當成
                # 「沒有編號」而整份跳過，畫面顯示「這台 0 條」而沒有任何錯誤訊息。
                if "item_id" not in columns and columns:
                    columns[0] = "item_id"
                continue
            # 表頭 key: value
            for key, field in _HEADER.items():
                if line.startswith(key + ":") or line.startswith(key + "："):
                    header[field] = line.split(":", 1)[1].strip() if ":" in line \
                        else line.split("：", 1)[1].strip()
                    break
            continue

        # 欄位標題之後
        if line.startswith("Check summary"):
            summary_line = line
            continue
        for key, field in _HEADER.items():
            if line.startswith(key + ":"):
                header[field] = line.split(":", 1)[1].strip()
                break
        else:
            if ";" not in line:
                continue
            # 最後一欄（目前值）可能含分號，所以只切 len(columns)-1 刀
            parts = line.split(";", len(columns) - 1)
            if len(parts) < len(columns):
                parts += [""] * (len(columns) - len(parts))
            row = dict(zip(columns, [p.strip() for p in parts]))
            if not row.get("item_id"):
                continue
            seq += 1
            items.append({
                "seq": seq,
                "item_id": row.get("item_id", ""),
                # 新格式：獨立的條號欄；舊格式：條號還埋在標準設定值裡。
                # 先看獨立欄，取不到才回頭挖舊欄位——兩種報告都要讀得出來。
                "ref_id": (extract_ref(row.get("ref_src", ""))
                           or extract_ref(row.get("standard", ""))),
                "desc": split_fields(row.get("standard", ""))[0],
                "expected": split_fields(row.get("standard", ""))[1] or None,
                # 備註：新格式拿獨立的「CIS控制項」與「本行對應條號」兩欄拼；
                # 舊格式還是從標準設定值裡剝。兩邊都吐同一個欄位給畫面，
                # 畫面不必知道這份是新版還是舊版產的。
                "note": (_join_note(row.get("ctl_src", ""), row.get("org_src", ""))
                         or split_fields(row.get("standard", ""))[2] or None),
                "role": row.get("role") or None,
                "result": row.get("result", ""),
                "category": row.get("category", ""),
                "name": row.get("name", ""),
                "standard": row.get("standard", ""),
                "current": row.get("current", ""),
                # 舊格式沒有這三欄 -> None，畫面顯示「—」。
                # 不可以填空字串假裝有，也不可以自己生一個指令出來。
                "verify_cmd": row.get("verify_cmd") or None,
                "fix_cmd": row.get("fix_cmd") or None,
                "apply_effect": row.get("apply_effect") or None,
                "fix_kind": row.get("fix_kind") or None,
            })

    if not columns:
        raise ValueError("找不到欄位標題列（要有『檢查結果』那一行），這不是檢核結果檔")
    if not items:
        raise ValueError("檔案裡沒有任何檢核項目")

    # 完整性：只有會寫 Check summary 的腳本判得出來。
    # 沒有完成標記的平台**如實標明**，不可以假裝完整，也不可以整份退掉。
    if summary_line:
        declared = _declared_total(summary_line)
        completeness = "完整" if declared in (None, len(items)) else "截斷"
    elif (header.get("platform") or "").strip().upper() in _GUARANTEES_SUMMARY:
        # ⚠️ 2026-09-23 於 221 實測抓到：跑到第 57 條死掉的 AIX 檔被收下來，
        # 而且因為前 57 條剛好都合規，畫面顯示**100% 合規**。
        #
        # 原本的截斷判定要「有 Check summary 可以比對」才成立
        # （宣稱 98、實際 90 -> 擋下來）。但腳本中途死掉時本來就寫不到結尾，
        # 於是沒有東西可比 -> 放行。**越嚴重的截斷越擋不住。**
        #
        # fcb_aixV###.ksh 的檔頭自己保證「收尾一定有這兩行，沒有就是中途死掉」，
        # 所以對這些平台，缺結尾＝截斷，不是「這個平台沒有完成標記」。
        completeness = "截斷"
    else:
        completeness = "無完成標記"

    return {"header": header, "columns": columns, "items": items,
            "completeness": completeness, "summary": summary_line}


def _declared_total(summary_line: str) -> int | None:
    """從 `Check summary: 合計 98 項；...` 取出腳本自己宣稱的條數。
    宣稱 98 但只解析到 60 → 檔案被截斷，要擋下來。"""
    m = re.search(r"合計\s*(\d+)", summary_line)
    return int(m.group(1)) if m else None


def parse_probes(text: str) -> dict:
    """`Check probes: showmount_rc=0 ps_fields=8 ps_rows=412` -> dict。

    只拆成鍵值，不解讀。解讀在 `integrity_notes`。
    """
    out: dict[str, str] = {}
    for tok in (text or "").split():
        k, sep, v = tok.partition("=")
        if sep and k:
            out[k.strip()] = v.strip()
    return out


def integrity_notes(header: dict, counts: dict, completeness: str) -> list[dict]:
    """這份報表的「檢查本身有沒有在工作」。回 [{level, text}]。

    ⚠️ 「這裡回的是提示，不改任何數字。」 合規率是使用者要交出去的東西，
    提示歸提示，不可以動它。

    分兩種來源，強度完全不同：

    * **事實**：腳本在當下量到的（`Check probes:`）。有就轉述，沒有就說沒有。
    * **提示**：一條統計觀察。它質疑的**不是結果**，是檢查有沒有在工作——
      見下面那條的說明。
    """
    notes: list[dict] = []
    plat = (header.get("platform") or "").strip().upper()
    probes = parse_probes(header.get("probes") or "")

    # ── 事實：腳本量到的 ──────────────────────────────────────────
    if probes:
        rc = probes.get("showmount_rc")
        if rc is not None and rc != "0":
            why = "查詢逾時" if rc == "124" else f"查詢失敗（離開碼 {rc}）"
            notes.append({"level": "fact", "text": (
                f"NFS 那條（FCB-AIX-0020）「沒有查到」：`showmount` {why}。"
                "報表上該條應為 Error；若顯示 Compliant，是腳本的修正沒有生效，請回報。")})
        nf = probes.get("ps_fields")
        if nf is not None and nf.isdigit() and int(nf) != 8:
            notes.append({"level": "fact", "text": (
                f"這台的 `ps -ef` 是 「{nf} 欄」，不是預期的 8 欄——"
                "inetd 那 13 條（0023~0045）的「行程比對不會生效，已退回只看設定」。"
                "也就是說「設定關了但服務還在跑」這種情況，這份報表抓不到。")})
        lr = probes.get("lsdev_rows")
        if lr is not None and lr.isdigit() and int(lr) == 0:
            notes.append({"level": "fact", "text": (
                "`lsdev -Cc adapter` 一列都沒回——網路／HBA 相關的條目"
                "「無法判斷」，不是這台沒有介面卡。")})
    if plat in _EXPECTS_PROBES and not (header.get("script_version") or "").strip():
        # 沒有腳本版本戳 -> 這份結果查不出是哪一版跑的。
        # 09-23 那五條判定邏輯改過，修正前後的輸出長得一樣——
        # 沒有版號的話，第一份真機輸出出問題時查不出是哪一版產的。
        notes.append({"level": "unknown", "text": (
            "這份沒有「腳本版本」——查不出是哪一版的 fcb_aixV###.ksh 跑出來的。"
            "2026-09-23 改過五條判定邏輯（NFS、nodev、兩處讀檔前置、inetd），"
            "修正前後的輸出長得一樣，所以沒有版號就分不出這份是修正前還是修正後的。"
            "請改用有版號的腳本重跑一次。")})

    if not probes and plat in _EXPECTS_PROBES:
        # ⚠️ 沉默等於預設它沒問題。這一條一定要講。
        notes.append({"level": "unknown", "text": (
            "這份是「舊版腳本」產出的，沒有自我檢查資訊（`Check probes:`）。"
            "所以「無法確認」 NFS 那條（0020）與 inetd 那 13 條（0023~0045）"
            "的檢查有沒有真的生效——那兩處若沒生效，結果會偏向「看起來合規」。"
            "拿到新版腳本的輸出後這一行就會換成實際量到的數字。")})

    # ── 提示：唯一一條統計觀察 ────────────────────────────────────
    #
    # ⚠️ 這條質疑的**不是結果**，是檢查有沒有在工作。
    # 「全數合規很可疑」是在質疑結果（而一台真的很乾淨的機器會被冤枉）；
    # 「一條查不到的都沒有很可疑」是在質疑檢查——兩者不一樣，只有後者
    # 對得上我們要防的東西：檢查沒跑起來時，它不會產生 Error，只會安靜地過。
    na = counts.get(NOT_APPLICABLE, 0)
    err = counts.get(ERROR, 0)
    total = sum(counts.values())
    if total >= 20 and na == 0 and err == 0:
        notes.append({"level": "hint", "text": (
            f"這份 {total} 條裡，「沒有任何一條回報「不適用」或「查不到」」。"
            "一台機器上 98 條檢核全部都問得到、而且每一條都適用，在實務上少見——"
            "「這比較像是有檢查沒跑起來」（沒跑起來的檢查不會產生 Error，只會安靜地過）。"
            "建議先確認 `Check summary:` 的條數對得上，再抽看 0020 與 0023~0045。"
            "（這只是提示，「沒有改動上面的合規率」。）")})

    if completeness == "無完成標記" and plat not in _GUARANTEES_SUMMARY:
        notes.append({"level": "unknown", "text": (
            "這個平台的腳本不寫收尾標記，所以「無法確認檔案有沒有被截斷」。"
            "截斷的檔案會讓少查的條目變成「沒有問題」。")})
    return notes


def tally(items: list[dict]) -> dict:
    """四態各自計數。**認不得的結果值不可以吞掉**——歸進 error 並如實顯示，
    否則加總對不起來而沒人知道。"""
    out = {COMPLIANT: 0, NON_COMPLIANT: 0, NOT_APPLICABLE: 0, ERROR: 0}
    for it in items:
        r = (it.get("result") or "").strip()
        out[r if r in out else ERROR] += 1
    return out


def coverage(counts: dict) -> float | None:
    """合規率。分母 = 總數 − Not-Applicable − Error。

    分母 0 回 None，不可以回 0%——「沒有可判定的項目」跟「一條都不合規」
    是完全不同的兩件事。
    """
    denom = counts.get(COMPLIANT, 0) + counts.get(NON_COMPLIANT, 0)
    if denom <= 0:
        return None
    return round(counts.get(COMPLIANT, 0) * 100.0 / denom, 1)


def coverage_formula(counts: dict) -> str:
    """畫面要顯示算式，不可以只給一個百分比讓人猜分母是什麼。"""
    total = sum(counts.values())
    na = counts.get(NOT_APPLICABLE, 0)
    err = counts.get(ERROR, 0)
    return (f"合規 {counts.get(COMPLIANT, 0)} ÷（總數 {total} − 不適用 {na} − "
            f"未查到 {err} ＝ {total - na - err}）")


def observed_value(current: str) -> str:
    """從「無異常: ( minlen = 8 )」取出實際值 `minlen = 8`。

    取不到就原樣回傳——很多條的目前值本來就是整段 `ls -l` 輸出，
    那本身就是實際值，不需要再拆。**不可以因為拆不出來就回空字串**，
    那會讓畫面看起來像「沒量到」。
    """
    text = (current or "").strip()
    m = re.search(r"[（(]\s*(.+?)\s*[)）]\s*$", text)
    return m.group(1).strip() if m else text


def _asset_index(conn: sqlite3.Connection) -> tuple[dict, dict]:
    import manage_state
    by_ip: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for r in conn.execute("SELECT asset_serial, hostname, ip FROM hardware"):
        for ip in manage_state.ips_of(r["ip"]):
            by_ip.setdefault(ip, r["asset_serial"])
        h = (r["hostname"] or "").strip()
        if h:
            by_name.setdefault(h.upper(), r["asset_serial"])
            by_name.setdefault(h.split(".")[0].upper(), r["asset_serial"])
    return by_ip, by_name


def _match(hostname: str, ip: str, by_ip: dict, by_name: dict) -> tuple[str | None, str]:
    """先比 IP 再比主機名。對不到就是對不到，不可以硬湊一台。"""
    i = (ip or "").strip()
    if i and _IPV4.match(i) and i != "0.0.0.0":
        s = by_ip.get(i)
        if s:
            return s, "ip"
    h = (hostname or "").strip()
    if h:
        s = by_name.get(h.upper()) or by_name.get(h.split(".")[0].upper())
        if s:
            return s, "hostname"
    return None, "對不到"


def import_report(conn: sqlite3.Connection, blob: bytes,
                  file_name: str | None = None, by: str | None = None) -> dict:
    """匯入一份檢核結果。

    原子化：先 status='importing'，全部寫完才 'ready'。匯到一半掛掉的那半份
    不可以被當成檢核結果拿去算合規率。
    """
    _ensure(conn)
    cur = conn.execute(
        "INSERT INTO config_audit_run (file_name, imported_by, imported_at, status) "
        "VALUES (?,?,?,?)", (file_name, by, _now(), "importing"))
    run_id = cur.lastrowid
    conn.commit()
    try:
        d = parse(blob)
        raw_text = _decode(blob).replace("\r\n", "\n")
        if d["completeness"] == "截斷":
            if d.get("summary"):
                why = ("腳本宣稱的條數跟實際解析到的對不起來"
                       f"（實際 {len(d['items'])} 條）")
            else:
                # 腳本中途死掉時本來就寫不到結尾——**越嚴重的截斷越沒有東西可比對**。
                # 2026-09-23 於 221 實測：跑到第 57 條死掉的 AIX 檔原本會被收下來，
                # 而且前 57 條剛好都合規，畫面顯示 100% 合規。
                why = (f"沒有收尾的 `Check summary:` 行，而 {d['header'].get('platform')} "
                       f"的腳本保證跑完一定會寫這一行——所以它是中途死掉的，"
                       f"檔案只有前面 {len(d['items'])} 條")
            raise ValueError(
                f"這份檔案是截斷的：{why}。"
                "少查的條目會被當成『沒有問題』，收下來會算出一個假的合規率，所以不收。"
                "請重新執行檢核腳本。")
        h = d["header"]
        items = d["items"]
        counts = tally(items)
        by_ip, by_name = _asset_index(conn)
        serial, how = _match(h.get("hostname", ""), h.get("ip", ""), by_ip, by_name)
        conn.executemany(
            "INSERT INTO config_audit_item (run_id, seq, item_id, ref_id, note, expected, "
            "role, result, category, name, standard, current) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(run_id, it["seq"], it["item_id"], it["ref_id"], it["note"], it["expected"],
              it["role"], it["result"], it["category"], it["name"], it["desc"],
              it["current"]) for it in items])
        conn.execute(
            "UPDATE config_audit_run SET status='ready', platform=?, hostname=?, ip=?, "
            "os_version=?, kernel=?, script_version=?, baseline=?, runas=?, checked_at=?, "
            "ended_at=?, completeness=?, warning=?, probes=?, asset_serial=?, match_by=?, total=?, "
            "compliant=?, non_compliant=?, not_applicable=?, error=?, raw=? WHERE id=?",
            (h.get("platform"), h.get("hostname"), h.get("ip"), h.get("os_version"),
             h.get("kernel"), h.get("script_version"), h.get("baseline"), h.get("runas"),
             h.get("checked_at"), h.get("ended_at"), d["completeness"], h.get("warning"),
             h.get("probes"), serial, how, len(items), counts[COMPLIANT], counts[NON_COMPLIANT],
             counts[NOT_APPLICABLE], counts[ERROR], raw_text, run_id))
        conn.commit()
        return {"run_id": run_id, "platform": h.get("platform"),
                "hostname": h.get("hostname"), "ip": h.get("ip"),
                "total": len(items), "counts": counts,
                "coverage": coverage(counts), "formula": coverage_formula(counts),
                "completeness": d["completeness"], "asset_serial": serial,
                "match_by": how, "warning": h.get("warning"),
                # 「這份報表的檢查本身有沒有在工作」。提示歸提示，不動合規率。
                "integrity": integrity_notes(h, counts, d["completeness"])}
    except Exception as exc:  # noqa: BLE001 - 失敗要留痕，不可以裝作沒發生
        conn.execute("UPDATE config_audit_run SET status='failed', note=? WHERE id=?",
                     (str(exc)[:500], run_id))
        conn.execute("DELETE FROM config_audit_item WHERE run_id=?", (run_id,))
        conn.commit()
        raise


# 收集器要跑的 ksh 放這裡（取檔名流水號最大的那支）
_SCRIPTS_DIR = __import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"


def _latest_fcb_aix() -> "object":
    """找 scripts/ 下流水號最大的 fcb_aixV###.ksh。"""
    import pathlib
    cands = sorted(_SCRIPTS_DIR.glob("fcb_aixV*.ksh"))
    if not cands:
        raise FileNotFoundError("找不到 fcb_aixV###.ksh（scripts/ 下沒有檢核腳本）")
    return cands[-1]


def collect_from_host(conn, asset_serial: str, runner=None, by: str | None = None) -> dict:
    """手動一鍵：SSH 到主機跑組態檢核腳本、把產出的 .txt 收回來匯入（2026-10-01）。

    MVP 只做 AIX、且**非 root**（fcb_aix 非 root 仍可跑，root-only 的項目會標 Error）——
    先用現成唯讀收集帳號 webit3sc 驗穩，之後要拿滿分再加 sudo。

    做法：把 ksh 用 heredoc 送上去寫成暫存檔、設 FCBAIX_OUTDIR 到暫存目錄跑、
    把 FCB_*.txt cat 回來、刪暫存。腳本是唯讀的、對目標機不做任何變更。
    """
    import manage_state

    row = conn.execute(
        "SELECT asset_serial, hostname, ip FROM hardware WHERE asset_serial = ?",
        (asset_serial,)).fetchone()
    if not row or not (row["ip"] or "").strip():
        raise ValueError("這台沒有可連線的 IP")
    ip = row["ip"].strip()
    platform = manage_state.collect_platform_of(conn, ip)
    if platform != "aix":
        raise ValueError(f"目前自動收集只支援 AIX（這台判為 {platform}）；"
                         "其他平台請用『匯入』上傳檢核 .txt")

    script = _latest_fcb_aix().read_text(encoding="utf-8")
    # heredoc 用引號標記＝內容原樣不展開，腳本裡的 $、引號都安全。不 set -e：
    # 腳本可能以非 0 收尾（有不合規項），用 set -e 會在 cat 之前就中斷。
    cmd = (
        'd="${TMPDIR:-/tmp}/fcbaix.$$"; mkdir -p "$d"; '
        "cat > \"$d/a.ksh\" <<'__FCBAIX_EOF__'\n"
        + script
        + "\n__FCBAIX_EOF__\n"
        'FCBAIX_OUTDIR="$d" ksh "$d/a.ksh" >/dev/null 2>&1; '
        'cat "$d"/FCB_*.txt 2>/dev/null; rm -rf "$d"'
    )

    if runner is None:
        acct = manage_state.get_collect_account(conn, "aix")
        # 腳本 98 條要跑一陣子，給長一點的逾時（預設 10 秒不夠）
        runner = manage_state._ssh_runner(manage_state.COLLECTOR_KEY_DEFAULT,
                                          account=acct, timeout=180)
    out = runner(ip, cmd) or ""
    if not out.strip():
        raise ValueError(
            "收不到檢核輸出——SSH 可能沒連上（帳號/金鑰/防火牆），或腳本在目標機沒跑成。"
            "先確認這台 AIX 的收集帳號（webit3sc）連得上。")
    return import_report(conn, out.encode("utf-8"),
                         file_name=f"fcb_aix_{row['hostname'] or ip}.txt", by=by)


def _decorate(r: sqlite3.Row) -> dict:
    d = dict(r)
    counts = {COMPLIANT: d.get("compliant", 0), NON_COMPLIANT: d.get("non_compliant", 0),
              NOT_APPLICABLE: d.get("not_applicable", 0), ERROR: d.get("error", 0)}
    d["coverage"] = coverage(counts)
    d["coverage_formula"] = coverage_formula(counts)
    # Error 多就代表這份不可信，畫面要看得到理由而不是自己去推
    d["trustworthy"] = d.get("error", 0) == 0
    if d.get("error", 0):
        d["untrust_reason"] = (f"有 {d['error']} 條沒查到（指令不存在／檔案讀不到／權限不足）"
                               "，合規率不含這些項目")
    # 「這份報表的檢查本身有沒有在工作」。**匯入那一秒講過還不夠**——
    # 使用者是回頭看詳細頁的時候才在判斷數字可不可信。
    d["integrity"] = integrity_notes(
        {"platform": d.get("platform"), "probes": d.get("probes")},
        counts, d.get("completeness") or "")
    return d


def runs(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """清單頁：每台一列，取每台最新一份。"""
    _ensure(conn)
    rows = conn.execute(
        "SELECT * FROM config_audit_run WHERE status='ready' ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    seen: set[tuple] = set()
    out = []
    for r in rows:
        key = ((r["hostname"] or "").strip().lower(), (r["ip"] or "").strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(_decorate(r))
    return out


def history(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """所有匯入紀錄（含失敗的）。失敗的要看得到，不然沒人知道匯入過但壞了。"""
    _ensure(conn)
    return [dict(r) for r in conn.execute(
        "SELECT id, file_name, imported_by, imported_at, status, platform, hostname, ip, "
        "total, note FROM config_audit_run ORDER BY id DESC LIMIT ?", (limit,))]


def detail(conn: sqlite3.Connection, run_id: int) -> dict | None:
    _ensure(conn)
    r = conn.execute("SELECT * FROM config_audit_run WHERE id=?", (run_id,)).fetchone()
    if not r:
        return None
    d = _decorate(r)
    d["items"] = [dict(x) for x in conn.execute(
        "SELECT * FROM config_audit_item WHERE run_id=? ORDER BY seq", (run_id,))]
    return d


def summary(conn: sqlite3.Connection) -> dict:
    """總覽數字。每個數字都要能點進去看是哪幾台，所以這裡同時給分組的 key。"""
    _ensure(conn)
    rs = runs(conn)
    by_platform: dict[str, dict] = {}
    for r in rs:
        p = r.get("platform") or "未知"
        b = by_platform.setdefault(p, {"platform": p, "hosts": 0, COMPLIANT: 0,
                                       NON_COMPLIANT: 0, NOT_APPLICABLE: 0, ERROR: 0})
        b["hosts"] += 1
        b[COMPLIANT] += r.get("compliant", 0)
        b[NON_COMPLIANT] += r.get("non_compliant", 0)
        b[NOT_APPLICABLE] += r.get("not_applicable", 0)
        b[ERROR] += r.get("error", 0)
    for b in by_platform.values():
        c = {k: b[k] for k in RESULTS}
        b["coverage"] = coverage(c)
        b["coverage_formula"] = coverage_formula(c)
    return {
        "hosts": len(rs),
        "unmatched": sum(1 for r in rs if not r.get("asset_serial")),
        "untrustworthy": sum(1 for r in rs if not r.get("trustworthy")),
        "no_end_marker": sum(1 for r in rs if r.get("completeness") == "無完成標記"),
        "by_platform": sorted(by_platform.values(), key=lambda x: -x["hosts"]),
    }


def unmatched(conn: sqlite3.Connection) -> list[dict]:
    """有檢核結果、卻對不回資產清冊的主機。

    這份清單本身就是發現：不是清冊少一台，就是檢核腳本跑在不該跑的機器上。
    """
    return [r for r in runs(conn) if not r.get("asset_serial")]


def by_asset(conn: sqlite3.Connection, asset_serial: str) -> dict:
    """某一台的組態檢核（資產查詢的分頁用）。

    三種狀況要分得出來，不可以都顯示成「沒問題」：
      1. 全站還沒匯入過任何檢核結果
      2. 有結果、但**這一台沒有**（沒跑過檢核，不是「檢核都過了」）
      3. 這一台有結果
    第 2 種最容易誤讀——沒有資料被當成合格，是這個系統最該避免的事。
    """
    _ensure(conn)
    any_run = conn.execute(
        "SELECT COUNT(*) FROM config_audit_run WHERE status='ready'").fetchone()[0]
    r = conn.execute(
        "SELECT * FROM config_audit_run WHERE status='ready' AND asset_serial=? "
        "ORDER BY id DESC LIMIT 1", (asset_serial,)).fetchone()
    if not r:
        return {"state": "站上還沒有任何檢核結果" if not any_run else "這一台沒有檢核結果",
                "run": None, "items": [], "history": []}
    d = _decorate(r)
    d["items"] = [dict(x) for x in conn.execute(
        "SELECT * FROM config_audit_item WHERE run_id=? ORDER BY seq", (r["id"],))]
    # 歷次：同一台跑過幾輪、合規率有沒有進步
    d["history"] = [dict(x) for x in conn.execute(
        "SELECT id, checked_at, imported_at, total, compliant, non_compliant, "
        "not_applicable, error FROM config_audit_run "
        "WHERE status='ready' AND asset_serial=? ORDER BY id DESC LIMIT 20",
        (asset_serial,))]
    return {"state": "有檢核結果", "run": d, "items": d["items"],
            "history": d["history"]}


def export_status(conn: sqlite3.Connection) -> tuple[str, int, int]:
    """(原文, 有原文的份數, 沒有原文的份數)。

    「沒有東西可以匯出」有兩種意思，不可以講同一句話：
      1. 站上真的一份檢核結果都沒有
      2. **有結果，但那幾份是在系統加上「保存原始檔」之前匯入的**
    第 2 種如果也說「還沒有任何檢核結果」，看的人會以為資料不見了，
    然後去重新跑檢核腳本——其實只要重新匯入那個檔就好。
    """
    _ensure(conn)
    parts, missing = [], 0
    for r in runs(conn):
        row = conn.execute("SELECT raw FROM config_audit_run WHERE id=?", (r["id"],)).fetchone()
        if row and row["raw"]:
            parts.append(row["raw"].rstrip("\n"))
        else:
            missing += 1
    text = "\n".join(parts) + "\n" if parts else ""
    return text, len(parts), missing
    return text, len(parts), missing


def export_raw(conn: sqlite3.Connection) -> str:
    """把所有機器最新一份的**原始檔全文**串起來，給 DYN 分析。

    刻意吐原文而不是用拆過的欄位重組——重組就不保證逐字相同，
    DYN 那邊是照字串比對的，差一個空白就可能整份解析不出來。
    """
    _ensure(conn)
    parts = []
    for r in runs(conn):
        row = conn.execute("SELECT raw FROM config_audit_run WHERE id=?", (r["id"],)).fetchone()
        if row and row["raw"]:
            parts.append(row["raw"].rstrip("\n"))
    return "\n".join(parts) + "\n" if parts else ""
