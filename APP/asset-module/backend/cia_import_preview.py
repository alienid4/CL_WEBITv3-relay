"""CIA 資產清冊匯入兩段式（2.055.0，V36，待決定 5：先預覽影響筆數，確認後才寫入）。⚠D4。

**寫入邏輯不動**：預覽與確認都是呼叫同一支 `excel_import.import_excel`——
  · 預覽：把正式庫整份複製到記憶體（sqlite backup），**先在副本上**算起點指紋與「before」，再在副本上跑
    import_excel、逐筆比對 → 新增／更新／沒變／拒收與每一筆改了哪些欄位。正式庫一個字都沒動（副本用完就丟）。
    欄位對應只讀一次：同一份 bytes 拿去雜湊、也拿去解析給 import_excel 用。
  · 確認（`confirm`）：
      1. 代號、檔案 sha256、建立者（只有預覽的那個人能確認，否則 403）都對；
      2. **原子地搶下代號**（O_CREAT|O_EXCL 建認領檔；只有一個請求建得成，另一個 404）。認領檔放超過 10 分鐘
         ＝上次寫入中途掛掉：清掉並回 409「上次寫入中斷，請重新預覽」；
      3. 在**同一個 BEGIN IMMEDIATE 交易**裡讀一次欄位對應（雜湊＝預覽時）與重算資料起點指紋，對不上 rollback 回 409；
         對得上就在同一個交易裡寫（write(conn, 檔名, 檔案, 對應表)：資料＋匯入紀錄一次 commit，見 routers.imports._cia_commit）。
      4. 不管成功失敗，預覽的檔案都刪掉（代號只能用一次）。
起點指紋＝import_excel 判斷時會讀到的全部輸入：hardware／personnel／software 三張表全部內容（含 collect_ok），
＋ CIA 作業系統是否「已實測」會讀的 source_record（實測來源、已對到哪一台；dyn_shell.cia_measured 同一條 SQL）。

預覽檔放在資料目錄底下的私有資料夾（0700，檔案 0600），建不起來就拒絕預覽（不退回共用暫存）；30 分鐘過期；
每次預覽與服務啟動時清掉過期的；同一人最多 3 份、全系統最多 20 份活著的預覽（計數＋佔位在同一把鎖裡）。
**前提：單一行程**（正式機 uvicorn 單一 worker）。鎖是 threading.Lock，多 worker 時份數上限不準——啟動時偵測到 WEB_CONCURRENCY>1 會印警告；要多 worker 得改成檔案鎖。
上傳上限 30 MB：Starlette 在進到路由之前就把整份上傳收進暫存檔，所以這個上限是「收完才擋」，不是邊收邊擋（已知限制）。

一步式 POST /api/import/excel 自 2.055.2 起缺省關閉（410），只有部署明確設 WEBIT_ENABLE_ONESTEP_CIA_IMPORT=1 才開。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

import excel_import

PREVIEW_DIR: Path | None = None    # None＝資料目錄底下的 cia_preview（測試可以改）
DIRNAME = "cia_preview"
TTL_SECONDS = 30 * 60
CLAIM_STALE_SECONDS = 10 * 60
MAX_BYTES = 30 * 1024 * 1024
MAX_PER_USER = 3
MAX_TOTAL = 20
DETAIL_LIMIT = 2000          # 每類明細最多回幾筆（數字一律是全部）
IGNORE_COLS = {"updated_at"}  # 比對「有沒有改」時不看的欄位
_LOCK = threading.Lock()      # 活著的預覽計數＋佔位（同一個行程內原子；正式機是單一行程）

# 每張表用哪些欄位讓人認得是哪一筆（畫面用；比對一律用 id）
_TABLES = {
    "hardware": ("硬體", ("asset_serial", "hostname", "ip")),
    "personnel": ("人員", ("asset_serial", "person_name")),
    "software": ("軟體", ("asset_serial", "asset_name", "db_software")),
}


class StalePreview(Exception):
    """預覽之後起點變了（檔案、欄位對應或資料），確認寫入會跟預覽不同——要重新預覽（409）。"""


class NotOwner(Exception):
    """不是建立這次預覽的人（403）。"""


class TooMany(Exception):
    """活著的預覽太多（429）。"""


class TooLarge(Exception):
    """檔案太大（413）。"""


class StorageError(Exception):
    """私有預覽資料夾建不起來／權限設不了（拒絕預覽，503）。"""


def preview_dir(create: bool = True) -> Path:
    if PREVIEW_DIR is not None:
        d = Path(PREVIEW_DIR)
    else:
        import db
        d = db.get_db_path().parent / DIRNAME
    if not create:
        return d
    try:
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    except OSError as exc:
        raise StorageError(f"預覽資料夾建立失敗，已拒絕預覽：{exc}") from exc
    return d


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def read_mapping() -> tuple[bytes, dict]:
    """讀 field_mapping.json 一次：回（原始 bytes，解析好的對應表）。雜湊跟寫入用的是同一份。"""
    raw = excel_import.MAPPING_PATH.read_bytes()
    return raw, excel_import.parse_mapping(raw)


def mapping_hash() -> str:
    return _sha(read_mapping()[0])


def db_fingerprint(conn: sqlite3.Connection) -> str:
    """import_excel 會讀到的全部輸入（見檔頭）。"""
    import dyn_shell

    h = hashlib.sha256()
    for t in _TABLES:
        for r in conn.execute(f"SELECT * FROM {t} ORDER BY id"):
            h.update(json.dumps(list(r), ensure_ascii=False, default=str).encode("utf-8"))
        h.update(b"|")
    ph = ",".join("?" for _ in dyn_shell.MEASURED_SOURCES)
    try:
        for r in conn.execute(f"SELECT DISTINCT resolved_hardware_id FROM source_record WHERE source IN ({ph}) "
                              "AND resolved_hardware_id IS NOT NULL ORDER BY 1", dyn_shell.MEASURED_SOURCES):
            h.update(f"{r[0]},".encode())
    except sqlite3.Error:
        h.update(b"no-source_record")
    return h.hexdigest()


def _rows(conn: sqlite3.Connection, table: str) -> dict[int, dict]:
    return {r["id"]: dict(r) for r in conn.execute(f"SELECT * FROM {table}")}


def _label(table: str, row: dict) -> dict:
    return {k: row.get(k) for k in _TABLES[table][1]}


def dry_run(conn: sqlite3.Connection, xlsx_path: Path, mapping: dict | None = None) -> dict[str, Any]:
    """在正式庫的記憶體副本上跑同一支 import_excel。起點指紋與 before 都從**同一份副本**取（不再讀正式庫）。"""
    mem = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        conn.backup(mem)
        mem.row_factory = sqlite3.Row
        mem.execute("PRAGMA foreign_keys = ON")
        fingerprint = db_fingerprint(mem)
        before = {t: _rows(mem, t) for t in _TABLES}
        summary = excel_import.import_excel(xlsx_path, mem, mapping=mapping)   # 跟確認寫入同一支，寫的是副本
        after = {t: _rows(mem, t) for t in _TABLES}
    finally:
        mem.close()
    tables: dict[str, Any] = {}
    for t, (sheet, _) in _TABLES.items():
        b, a = before[t], after[t]
        new_ids = [i for i in a if i not in b]
        changed = []
        for i, row in a.items():
            if i in b:
                diff = {k: {"before": b[i].get(k), "after": v} for k, v in row.items()
                        if k not in IGNORE_COLS and b[i].get(k) != v}
                if diff:
                    changed.append({**_label(t, row), "fields": diff})
        removed = [i for i in b if i not in a]          # import_excel 不刪資料；有就是異常，照實回報
        s = summary["sheets"].get(sheet, {"inserted": 0, "updated": 0, "skipped": 0})
        touched = s["inserted"] + s["updated"]
        tables[t] = {
            "sheet": sheet,
            "new": len(new_ids), "updated": len(changed),
            "unchanged": max(0, touched - len(new_ids) - len(changed)),
            "rejected": s["skipped"], "removed": len(removed),
            "new_rows": [_label(t, a[i]) for i in new_ids[:DETAIL_LIMIT]],
            "updated_rows": changed[:DETAIL_LIMIT],
        }
    return {"summary": summary, "tables": tables, "fingerprint": fingerprint}


def _meta_path(token: str) -> Path:
    return preview_dir() / f"{token}.json"


def _file_path(token: str) -> Path:
    return preview_dir() / f"{token}.xlsx"


def _claim_path(token: str) -> Path:
    return preview_dir() / f"{token}.claim"


def cleanup(now: float | None = None) -> int:
    """刪掉過期的預覽（meta＋檔案）與殘檔。回刪了幾份。資料夾不存在就什麼都不做（不建）。"""
    now = time.time() if now is None else now
    d = preview_dir(create=False)
    if not d.exists():
        return 0
    n = 0
    for p in list(d.iterdir()):
        try:
            if p.suffix == ".json":
                if now - json.loads(p.read_text(encoding="utf-8")).get("created", 0) > TTL_SECONDS:
                    p.unlink(missing_ok=True)
                    (d / (p.stem + ".xlsx")).unlink(missing_ok=True)
                    n += 1
            elif p.suffix in (".xlsx", ".claim") and not (d / (p.stem + ".json")).exists() and now - p.stat().st_mtime > TTL_SECONDS:
                p.unlink(missing_ok=True)
        except (OSError, ValueError):
            continue
    return n


def _live(by: str | None = None) -> int:
    n = 0
    for p in preview_dir().glob("*.json"):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if by is None or m.get("by") == by:
            n += 1
    return n


def preview(conn: sqlite3.Connection, data: bytes, filename: str, by: str) -> dict[str, Any]:
    """存下檔案、在副本上試跑、回預覽與代號。檔案打不開／不是 CIA 清冊會丟 ValueError 或 openpyxl 的錯，呼叫端轉 400。"""
    if len(data) > MAX_BYTES:
        raise TooLarge(f"檔案超過 {MAX_BYTES // (1024 * 1024)} MB")
    now = time.time()
    preview_dir()                                   # 私有資料夾建不起來就在這裡拒絕（StorageError）
    cleanup(now)
    token = secrets.token_hex(16)
    mp = _meta_path(token)
    # 計數＋佔位在同一把鎖裡：兩個請求同時進來不會都看到「還有名額」
    with _LOCK:
        if _live(by) >= MAX_PER_USER:
            raise TooMany(f"你已經有 {MAX_PER_USER} 份還沒確認的預覽，請先確認或等 30 分鐘過期")
        if _live() >= MAX_TOTAL:
            raise TooMany("系統上還沒確認的預覽太多，請稍後再試")
        _write_private(mp, json.dumps({"token": token, "by": by, "created": now, "pending": True}).encode("utf-8"))
    fpath = _file_path(token)
    try:
        _write_private(fpath, data)
        raw_map, mapping = read_mapping()
        res = dry_run(conn, fpath, mapping)
    except Exception:
        fpath.unlink(missing_ok=True)
        mp.unlink(missing_ok=True)
        raise
    meta = {"token": token, "filename": filename, "by": by, "created": now,
            "file_sha256": _sha(data), "mapping_sha256": _sha(raw_map), "db_fingerprint": res.pop("fingerprint")}
    _write_private(mp, json.dumps(meta, ensure_ascii=False).encode("utf-8"))
    return {"token": token, "filename": filename, "file_sha256": meta["file_sha256"],
            "expires_in": TTL_SECONDS, **res}


def _valid_token(token: str) -> bool:
    return bool(token) and len(token) == 32 and all(c in "0123456789abcdef" for c in token)


def confirm(conn: sqlite3.Connection, token: str, file_sha256: str, by: str,
            write: Callable[[sqlite3.Connection, str, bytes, dict], dict]) -> dict:
    """確認寫入（見檔頭 1～4）。write(conn, 檔名, 檔案內容, 欄位對應) 在交易裡呼叫，回寫入結果。
    丟 LookupError（404）、NotOwner（403）、StalePreview（409）。"""
    if not _valid_token(token):
        raise LookupError("預覽代號不對")
    mp, fp, claimed = _meta_path(token), _file_path(token), _claim_path(token)
    try:
        meta = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise LookupError("找不到這次預覽（可能已經寫入過、或超過 30 分鐘），請重新上傳預覽") from None
    if meta.get("pending") or "file_sha256" not in meta:
        raise LookupError("這次預覽還沒完成，請稍候再確認")
    if meta.get("by") != by:
        raise NotOwner("只有做這次預覽的人可以確認寫入")
    if time.time() - meta["created"] > TTL_SECONDS:
        discard(token)
        raise LookupError("預覽已超過 30 分鐘，請重新上傳預覽")
    if file_sha256 != meta["file_sha256"]:
        raise StalePreview("檔案跟預覽時不同，請重新預覽")
    # 2. 原子地搶下代號：O_CREAT|O_EXCL 建一個認領檔，只會有一個請求建成功，另一個在這裡拿到 404。
    #    （不用 rename：實測 Windows 上兩條執行緒同時 rename 同一個檔可以兩個都「成功」。）
    try:
        os.close(os.open(claimed, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except OSError:
        try:
            stale = time.time() - claimed.stat().st_mtime > CLAIM_STALE_SECONDS
        except OSError:
            stale = False
        if stale:      # 認領檔放太久＝上次寫入中途掛掉：清乾淨，講清楚要重新預覽
            discard(token)
            claimed.unlink(missing_ok=True)
            raise StalePreview("上次寫入中斷，請重新預覽") from None
        raise LookupError("這次預覽已經在寫入或寫入過了，請重新上傳預覽") from None
    try:
        try:
            contents = fp.read_bytes()
        except OSError:
            raise LookupError("預覽的檔案已經不在了（可能已經寫入過、或超過 30 分鐘被清掉），請重新上傳預覽") from None
        if _sha(contents) != meta["file_sha256"]:
            raise StalePreview("檔案跟預覽時不同，請重新預覽")
        # 3. 檢查與寫入在同一個交易：別人在檢查之後、寫入之前改資料，會被這把寫入鎖擋在外面
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            raw_map, mapping = read_mapping()       # 只讀一次：雜湊的跟拿去寫的是同一份
            if _sha(raw_map) != meta["mapping_sha256"]:
                raise StalePreview("預覽之後欄位對應改過，寫入結果會跟預覽不同，請重新預覽")
            if db_fingerprint(conn) != meta["db_fingerprint"]:
                raise StalePreview("預覽之後資產資料有變動（例如別人又匯入、改過資產或收集結果），寫入結果會跟預覽不同，請重新預覽")
            return write(conn, meta["filename"], contents, mapping)
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
    finally:
        mp.unlink(missing_ok=True)      # 先刪 meta：之後再來的請求在讀 meta 那一步就是 404
        fp.unlink(missing_ok=True)
        claimed.unlink(missing_ok=True)


def discard(token: str) -> None:
    if not _valid_token(token):
        return
    _meta_path(token).unlink(missing_ok=True)
    _file_path(token).unlink(missing_ok=True)
