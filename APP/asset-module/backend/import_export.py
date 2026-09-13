"""每一種匯入都配一個匯出（原始檔／Excel／dump）。

2026-09-09 使用者：「Dynaset 有匯入但沒有匯出的功能。我如果匯入，要怎麼匯出？
當我匯出的時候，我要選原始檔、Excel 的匯出，跟 dump 格式的匯出。也要有 dump
格式的匯入」，接著補「每個匯入匯出都要有，rvtools 的匯入、CIA 資產清冊都要」。

## 三種格式各自回答不同的問題

| 格式 | 回答的問題 | 從哪來 |
|---|---|---|
| 原始檔 | 「當初送進來的那個檔長什麼樣」 | 存下來的上傳檔本身 |
| Excel | 「現在系統裡是什麼」，人要看、要改、要給別人 | 資料庫重建 |
| dump | 「整包搬到另一台」 | 資料庫重建，gzip＋加密 |

**它們不是同一份東西的三種包裝**——原始檔可能有系統看不懂而丟掉的欄位，
Excel 是正規化之後的結果。混為一談會讓人以為匯出的原始檔可以拿去對帳。

## 口徑：只有「現在」，沒有歷史（使用者 2026-09-09 選 A）

匯出的是**現在資料庫裡的內容**，不是「第 3 次匯入的那一批」。
所以原始檔也**只留最新一份**，新的蓋掉舊的——留舊檔跟這個口徑不一致，
而且 RVTools 匯出檔動輒幾十 MB。

## 以前匯入的沒有原始檔，這件事要講出來

原本的匯入流程是寫暫存檔、處理完 `unlink` 刪掉（`api.py`），所以
**這個功能上線之前的匯入，原始檔是真的不存在**。畫面要誠實說「這批沒有留
原始檔」，**不可以拿重新產生的檔冒充**——那會讓人拿它去跟來源系統對帳，
對不起來卻找不到原因。

## 三種來源的資料在不同地方（查證過，不是猜的）

- `dynassets`：`source_record`（source='dynassets'，2984 筆）——payload 就是原始那一列
- `rvtools`：`source_record`（source='vcenter'，2334 筆）＋ `vcenter_extra:*` 各分頁
- `cia_excel`：**沒有寫 source_record**，資料直接進 `hardware`——所以它的 Excel
  只能從 `hardware` 產，欄位是系統的欄位，不是原始 Excel 的欄位
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import db

#: 原始檔放這裡。跟 DB 同一個 data/ 目錄——那個目錄本來就不進版控、
#: 部署時權限只開給服務帳號，真實資料放這裡的規則已經成立，不要另立門戶。
ARCHIVE_DIRNAME = "imports"


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    #: Excel 匯出的資料從哪來：
    #:   'source_record' —— 用 payload 重建，欄位就是當初匯入的欄位
    #:   'hardware'      —— 沒留原始列，只能匯系統欄位
    #:   'table'         —— 這個來源有自己的表，整張匯出（欄位就是它的欄位）
    origin: str
    #: origin='source_record' 時要撈的 source 值
    record_source: str | None = None
    #: origin='table' 時要匯的表名
    table_name: str | None = None


SOURCES: dict[str, Source] = {
    "dynassets": Source("dynassets", "存活清單（dynassets）", "source_record", "dynassets"),
    "rvtools": Source("rvtools", "RVTools（vCenter）", "source_record", "vcenter"),
    "cia_excel": Source("cia_excel", "CIA 資產清冊", "hardware"),
    # 業務系統對照表有自己的表（api_id 是主鍵），整張進整張出——
    # 這是四個來源裡唯一「匯出的東西跟匯入的東西欄位完全一樣」的，
    # 所以它的 dump 拿去別台匯入是真的可以還原。
    "business_system": Source("business_system", "業務系統對照表", "table",
                              table_name="business_system"),
}


def archive_root() -> Path:
    """原始檔的根目錄，跟著 DB 走（測試與正式各自獨立）。"""
    return db.get_db_path().parent / ARCHIVE_DIRNAME


def _dir_for(source: str) -> Path:
    if source not in SOURCES:
        raise ValueError(f"不認得的匯入來源：{source}")
    return archive_root() / source


def save_original(source: str, filename: str, content: bytes) -> Path:
    """存下這次上傳的原始檔，**並刪掉這個來源之前那一份**。

    只留最新一份是使用者 2026-09-09 選的。實作上先寫新檔再刪舊檔——
    倒過來做的話，寫檔失敗就會變成「舊的也沒了、新的也沒有」。
    """
    d = _dir_for(source)
    d.mkdir(parents=True, exist_ok=True)
    safe = os.path.basename(filename or "upload.dat").strip() or "upload.dat"
    target = d / safe
    tmp = d / (safe + ".part")
    tmp.write_bytes(content)
    for old in list(d.iterdir()):
        if old != tmp:
            try:
                old.unlink()
            except OSError:
                pass
    tmp.replace(target)
    return target


def original_for(source: str) -> Path | None:
    """回這個來源留著的原始檔；**沒有就回 None，不要生一個假的出來**。"""
    d = _dir_for(source)
    if not d.is_dir():
        return None
    files = [p for p in d.iterdir() if p.is_file() and not p.name.endswith(".part")]
    return files[0] if len(files) == 1 else (sorted(files)[-1] if files else None)


def clear_original(source: str) -> None:
    d = _dir_for(source)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


# ---------- Excel／表格資料 ----------

def export_rows(conn, source: str) -> tuple[list[str], list[list]]:
    """回 (欄位名, 每列的值)，給 Excel／CSV 用。

    `source_record` 那兩種是用 payload 重建——**欄位就是當初匯入的欄位**。
    欄位順序取所有列的鍵聯集並排序：不同列可能鍵不一樣（有些機器沒填某欄），
    只看第一列會漏掉後面才出現的欄位。
    """
    src = SOURCES.get(source)
    if src is None:
        raise ValueError(f"不認得的匯入來源：{source}")

    if src.origin == "source_record":
        rows = conn.execute(
            "SELECT payload FROM source_record WHERE source = ? ORDER BY id",
            (src.record_source,)).fetchall()
        payloads = []
        keys: set[str] = set()
        for r in rows:
            try:
                d = json.loads(r["payload"] or "{}")
            except (ValueError, TypeError):
                continue
            if isinstance(d, dict):
                payloads.append(d)
                keys |= set(d.keys())
        headers = sorted(keys)
        return headers, [[p.get(h, "") for h in headers] for p in payloads]

    if src.origin == "table":
        t = src.table_name
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        if not cols:
            return [], []
        rows = conn.execute(f"SELECT {', '.join(cols)} FROM {t}").fetchall()
        return cols, [[r[c] if r[c] is not None else "" for c in cols] for r in rows]

    # cia_excel：沒有原始列可還原，只能匯系統欄位。
    cols = [r[1] for r in conn.execute("PRAGMA table_info(hardware)")]
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM hardware ORDER BY id").fetchall()
    return cols, [[r[c] if r[c] is not None else "" for c in cols] for r in rows]


# ---------- dump ----------

DUMP_MAGIC = "WEBIT3-IMPORT-DUMP"
DUMP_VERSION = 1


def build_dump(conn, source: str) -> bytes:
    """整包搬家用的格式：gzip 過的 JSON。

    刻意**不是** SQL——SQL dump 匯進不同版本的 schema 會炸，而這個檔的用途
    正是「跨機器、跨版本搬」。JSON 帶欄位名，缺欄位補空、多欄位忽略，
    對得起來的部分就進得去。
    """
    headers, rows = export_rows(conn, source)
    body = {
        "magic": DUMP_MAGIC,
        "version": DUMP_VERSION,
        "source": source,
        "headers": headers,
        "rows": rows,
        "row_count": len(rows),
    }
    return gzip.compress(json.dumps(body, ensure_ascii=False).encode("utf-8"), compresslevel=6)


def read_dump(data: bytes) -> dict:
    """讀 dump，**每一種壞法都要講清楚是哪一種**。

    「檔案讀不到」對使用者沒有用——他要知道的是「拿錯檔」還是「檔壞了」。
    """
    try:
        raw = gzip.decompress(data)
    except OSError as exc:
        raise ValueError("這不是 dump 檔（解壓縮失敗），請確認選到的是匯出的 dump") from exc
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("dump 內容壞了（不是有效的 JSON），檔案可能在傳輸中損毀") from exc
    if not isinstance(body, dict) or body.get("magic") != DUMP_MAGIC:
        raise ValueError("這不是本系統的 dump 檔")
    if body.get("version") != DUMP_VERSION:
        raise ValueError(
            f"dump 版本是 {body.get('version')}，這套系統只認得 {DUMP_VERSION}——"
            "請用同一版的系統匯出")
    if not isinstance(body.get("headers"), list) or not isinstance(body.get("rows"), list):
        raise ValueError("dump 缺少欄位或資料")
    return body
