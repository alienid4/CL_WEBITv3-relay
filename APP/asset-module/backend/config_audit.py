"""組態檢核（選單 9-2）：吃各平台檢核腳本產出的 .txt，解析、存放、統計。

上游腳本：
  * Linux   `DATA/fcbrhelsh`      61 條，TWGCB-01-012 / FCB-01-002
  * AIX     `DATA/fcbaixsh`       98 條，CIS AIX 7.2 Benchmark，ID 為 FCB-AIX-xxxx
  * Windows 檢核表 v4.0           CSCB-01-2025-xxxx

三件在寫之前先查過、跟直覺不一樣的事（不查就會整批算錯）：

1. **欄數各平台不同。** Linux／AIX 是 6 欄，**Windows 是 7 欄**——多一個「角色」夾在
   ID 與檢查結果中間。照位置取第 2 欄當結果，Windows 每一列都會讀成 `Common`。
   所以一律**照欄位標題取值**，不照位置。
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
}

#: 欄位標題 → 內部欄名。各平台欄數不同，靠這張表對，不靠位置。
_COLUMN = {
    "twgcb-id": "item_id", "id": "item_id",
    "角色": "role",
    "檢查結果": "result",
    "類別": "category",
    "原則設定名稱": "name",
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


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(RUN_SQL)
    conn.execute(ITEM_SQL)
    # 既有資料庫補欄位：CREATE TABLE IF NOT EXISTS 不會幫已存在的表加欄，
    # 漏掉這段的話已部署的機器會在 INSERT 時炸「no such column」。
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
                "ref_id": extract_ref(row.get("standard", "")),
                "desc": split_fields(row.get("standard", ""))[0],
                "expected": split_fields(row.get("standard", ""))[1] or None,
                "note": split_fields(row.get("standard", ""))[2] or None,
                "role": row.get("role") or None,
                "result": row.get("result", ""),
                "category": row.get("category", ""),
                "name": row.get("name", ""),
                "standard": row.get("standard", ""),
                "current": row.get("current", ""),
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
        # fcbaixsh 的檔頭自己保證「收尾一定有這兩行，沒有就是中途死掉」，
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
            "ended_at=?, completeness=?, warning=?, asset_serial=?, match_by=?, total=?, "
            "compliant=?, non_compliant=?, not_applicable=?, error=?, raw=? WHERE id=?",
            (h.get("platform"), h.get("hostname"), h.get("ip"), h.get("os_version"),
             h.get("kernel"), h.get("script_version"), h.get("baseline"), h.get("runas"),
             h.get("checked_at"), h.get("ended_at"), d["completeness"], h.get("warning"),
             serial, how, len(items), counts[COMPLIANT], counts[NON_COMPLIANT],
             counts[NOT_APPLICABLE], counts[ERROR], raw_text, run_id))
        conn.commit()
        return {"run_id": run_id, "platform": h.get("platform"),
                "hostname": h.get("hostname"), "ip": h.get("ip"),
                "total": len(items), "counts": counts,
                "coverage": coverage(counts), "formula": coverage_formula(counts),
                "completeness": d["completeness"], "asset_serial": serial,
                "match_by": how, "warning": h.get("warning")}
    except Exception as exc:  # noqa: BLE001 - 失敗要留痕，不可以裝作沒發生
        conn.execute("UPDATE config_audit_run SET status='failed', note=? WHERE id=?",
                     (str(exc)[:500], run_id))
        conn.execute("DELETE FROM config_audit_item WHERE run_id=?", (run_id,))
        conn.commit()
        raise


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
