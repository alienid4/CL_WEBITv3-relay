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
    #: 這個來源支援哪幾種匯出（2.068.0）：
    #:   original —— 匯入時留原始檔
    #:   excel    —— 做得出「原封匯回得進去」的 Excel；做不到的不給，免得人拿去匯回卻匯不進去
    #:   dump     —— 整包搬家用的 JSON 快照
    original: bool = True
    excel: bool = True
    dump: bool = True
    #: Excel 鈕的提示（匯出的內容跟「匯入層」不一樣時要講清楚）
    excel_note: str = ""


SOURCES: dict[str, Source] = {
    # dump（2.068.2 起）：**只有匯入端吃得回去的來源才給**——目前只有帳號盤點。其餘來源的 dump 只是快照、
    # 匯不回去，給了會讓人以為能拿去另一台還原；要搬家請用原始檔（或可匯回的 Excel）。
    "dynassets": Source("dynassets", "存活清單（dynassets）", "source_record", "dynassets", dump=False),
    "rvtools": Source("rvtools", "RVTools（vCenter）", "source_record", "vcenter", dump=False),
    "cia_excel": Source("cia_excel", "CIA 資產清冊", "hardware", dump=False),
    # 業務系統對照表有自己的表（api_id 是主鍵），整張進整張出。
    "business_system": Source("business_system", "業務系統對照表", "table",
                              table_name="business_system", dump=False),
    # 帳號盤點（2026-09-18）：原始檔＝上次盤點表原檔；dump＝目前的比較基準（表頭＋每列），
    # 帳號盤點的匯入吃得回這個 dump——搬到另一台還原得回來。
    "account_audit": Source("account_audit", "帳號盤點（上次盤點）", "account_audit"),
    # ---- 2.068.0：匯入頁其餘來源也都要能匯出（使用者要把測試機的資料搬到正式機）----
    # origin='custom'：列由下方 _CUSTOM[key] 產生，表頭刻意跟該來源匯入端認得的欄名一樣，匯出檔原封匯回得進去。
    "sys_class": Source("sys_class", "系統類別對照表", "custom", dump=False,
                        excel_note="匯出的是畫面上生效的分級（分級表＋人工改過的）"),
    "syscat": Source("syscat", "業務系統分類對照表", "custom", dump=False),
    "segments": Source("segments", "網段配置表", "custom", dump=False),
    # AD 人員名單（全體員工個資）刻意**不在這裡**：之後改 LDAPS 直連同步，不需要「下載全員名單」的按鈕（2.068.2）。
    # 憑證調查表的匯入格式（多欄、鍵由 F5／主機 IP 推導）重建不回去 → 只給原始檔。
    "certs": Source("certs", "憑證調查表", "custom", excel=False, dump=False),
    "alert_contact": Source("alert_contact", "告警聯絡資訊（APID）", "custom", dump=False,
                            excel_note="匯出的是畫面上生效的值（匯入資料＋管理員手動改過的）；沒填的欄留空，匯回後一樣退回業務系統對照表"),
    # 伺服器資料彙整表是兩個分頁的自訂格式 → 只給原始檔。
    "srv_sheet": Source("srv_sheet", "伺服器資料彙整表", "custom", excel=False, dump=False),
}


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _rows_sys_class(conn):
    """表頭＝匯入端（business_system.import_class_file）認得的「APID／系統類別」，第三欄系統別只給人看。
    類別是**畫面上生效的值**（人工覆寫優先，否則分級表）——匯回另一台後那邊看到的分級跟這邊一樣。"""
    head = ["APID", "系統類別", "系統別"]
    if not _has_table(conn, "business_system"):
        return head, []
    cols = {r[1] for r in conn.execute("PRAGMA table_info(business_system)")}
    eff = "COALESCE(sys_class_override, sys_class)" if "sys_class_override" in cols else "sys_class"
    rows = conn.execute(
        f"SELECT api_id, {eff} AS cls, name FROM business_system "
        f"WHERE {eff} IS NOT NULL AND length(trim({eff})) > 0 ORDER BY api_id").fetchall()
    return head, [[r["api_id"], r["cls"], r["name"] or ""] for r in rows]


def _rows_syscat(conn):
    """跟「下載範本」同一份（依台數排序、填好目前的分類），再補上已設分類但目前沒有機器的代碼——
    匯入端只看第 1 欄 api_id、第 4 欄分類（位置固定），所以欄位順序不能動。"""
    import system_report

    head = ["api_id", "系統名稱", "目前台數", "分類"]
    tpl = system_report.system_category_template(conn)
    cat = system_report._system_category_map(conn)
    rows = [[r["api_id"], r["name"], r["count"], r["category"]] for r in tpl]
    seen = {r["api_id"] for r in tpl}
    rows += [[a, "", 0, cat[a]] for a in sorted(cat) if a not in seen and cat[a]]
    return head, rows


def _rows_segments(conn):
    import segments

    return segments.export_current_rows(conn)


def _rows_certs(conn):
    import certs

    return certs.export_rows(conn, with_contacts=True)


def _rows_alert_contact(conn):
    """**畫面上生效的值**：人工覆寫優先、否則匯入的那份（不含業務系統對照表的退回值——那份兩邊各自有，
    匯回後空欄一樣會退回去）。列＝有匯入或有覆寫的 APID。匯回另一台後，那邊看到的值跟這邊一樣。"""
    import alert_contact

    fields = ("api_id", "ap_team", "unit", "system_name", "sys_class", "email")
    head = [alert_contact.HEADERS[f][0] for f in fields]
    imp = alert_contact._table(conn, "alert_contact")
    ovr = alert_contact._table(conn, "alert_contact_override")
    out = []
    for a in sorted(set(imp) | set(ovr)):
        o, i = ovr.get(a) or {}, imp.get(a) or {}
        out.append([a] + [(o[f] if o.get(f) is not None else i.get(f)) or "" for f in fields[1:]])
    return head, out


def _rows_srv_sheet(conn):
    """最近一份彙整表的主分頁列（跟畫面上看到的是同一份）。"""
    if not _has_table(conn, "srv_sheet_row"):
        return [], []
    cols = [r[1] for r in conn.execute("PRAGMA table_info(srv_sheet_row)") if r[1] not in ("id", "batch_id")]
    last = conn.execute("SELECT MAX(id) FROM srv_sheet_batch").fetchone()[0] if _has_table(conn, "srv_sheet_batch") else None
    if last is None:
        return cols, []
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM srv_sheet_row WHERE batch_id = ? ORDER BY row_no, id",
                        (last,)).fetchall()
    return cols, [[r[c] if r[c] is not None else "" for c in cols] for r in rows]


_CUSTOM = {
    "sys_class": _rows_sys_class, "syscat": _rows_syscat, "segments": _rows_segments,
    "certs": _rows_certs, "alert_contact": _rows_alert_contact, "srv_sheet": _rows_srv_sheet,
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
    # Codex 1167：以前是「寫暫存 → 刪舊檔 → 改名」，改名那步失敗就兩頭空（舊的刪了、目錄只剩 .part）。
    # 改成「寫暫存 → 改名成正式檔 → 才刪其他舊檔」：改名失敗時清掉暫存、舊檔原封不動，上一份仍可下載。
    try:
        tmp.write_bytes(content)
        tmp.replace(target)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    for old in list(d.iterdir()):
        if old != target:
            try:
                old.unlink()
            except OSError:
                pass
    return target


def keep_original(source: str, filename: str | None, content: bytes) -> None:
    """匯入**成功之後**留原始檔（2.068.0 起新增的來源都走這支）。
    存檔失敗不讓匯入失敗——資料已經進去了，少一份存檔是小事，但要留痕跡。"""
    try:
        save_original(source, filename or "upload.dat", content)
    except Exception as exc:  # noqa: BLE001
        # 存不下來就清掉這個來源的舊原始檔：寧可「沒有原始檔」，也不能讓上一批頂替這一批（裁判判定）。
        # 訊息只印例外種類，不印路徑（OSError 的字串會帶絕對路徑）。
        print(f"[import] 原始檔保存失敗（{source}，不影響匯入）：{type(exc).__name__}")
        try:
            clear_original(source)
        except Exception as exc2:  # noqa: BLE001
            print(f"[import] 清除舊原始檔也失敗（{source}）：{type(exc2).__name__}")


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

    if src.origin == "account_audit":
        import account_audit
        return account_audit.baseline_rows(conn)

    if src.origin == "custom":
        return _CUSTOM[source](conn)

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
