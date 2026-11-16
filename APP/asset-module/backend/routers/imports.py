from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

import datetime as _dt
from contextlib import contextmanager
import re

router = _RouteCollector()



@router.post("/api/documents/import")
def documents_import(
    files: list[UploadFile] = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """批次歸檔既有的 Word 單據（.doc／.docx 都吃）。

    只抽識別欄位建索引、把原檔存起來，**不解析表格內容**（見 doc_import.py 檔頭）。
    一份檔案失敗不能讓整批中斷——那會逼使用者一次次試錯找出是哪一份有問題。
    """
    import doc_import

    ok, failed = [], []
    for f in files:
        name = Path(f.filename or "doc").name
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(name).suffix) as tmp:
                tmp.write(f.file.read())
                tmp_path = Path(tmp.name)
            try:
                ok.append(doc_import.import_document(
                    conn, tmp_path, _doc_archive_dir(), session["username"],
                    original_name=name, refresh=False,
                ))
            finally:
                tmp_path.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001 - 逐份回報，不讓一顆壞蘋果毀掉整批
            failed.append({"file_name": name, "error": str(e)})
    # 整批做完才算一次「每個 IP 現行的是哪一張」——逐份跑是 O(N²) 的全表掃描。
    doc_import.refresh_current_flags(conn)
    return {
        "imported": len(ok), "failed": len(failed), "results": ok, "errors": failed,
        "auto_bound": sum(1 for r in ok if r["bind_confidence"] == "auto"),
        "need_review": sum(1 for r in ok if r["bind_confidence"] == "review"),
    }


@router.get("/api/documents")
def documents_list(
    confidence: str | None = Query(None),
    q: str | None = Query(None),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """單據清單／搜尋。q 連 Word 內文一起搜——使用者以前要找一筆資料得把每份檔案
    打開來看，只搜檔名和單號等於沒解決那個問題。命中會附上片段。"""
    import doc_import

    return {
        "documents": doc_import.list_documents(conn, confidence, q),
        "summary": doc_import.summary(conn),
    }


@router.get("/api/documents/summary")
def documents_summary(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """單據份數與最後匯入時間（給資料匯入頁那一列用；不撈整份清單）。唯讀。"""
    import doc_import

    return doc_import.summary(conn)


@router.get("/api/documents/{doc_id}/download")
def documents_download(
    doc_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """下載原始 Word。索引查到了卻打不開原檔，等於沒歸檔。"""
    row = conn.execute("SELECT * FROM doc_archive WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "找不到這份單據")
    path = Path(row["file_path"])
    if not path.exists():
        raise HTTPException(410, f"索引還在，但原始檔不見了：{row['file_name']}")
    # 全文檢索那份已遮掉帳密，但原始 Word 沒有（那是稽核證據，不能改）。
    # 既然一定要能下載，至少留下「誰在什麼時候看了哪一份」。
    conn.execute(
        "INSERT INTO doc_download_audit (doc_id, file_name, username, downloaded_at) "
        "VALUES (?, ?, ?, ?)",
        (doc_id, row["file_name"], session["username"],
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    return FileResponse(path, filename=row["file_name"])


@router.post("/api/documents/{doc_id}/review")
def documents_review(
    doc_id: int,
    body: DocReviewBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """人工審核：確認或修正抽出來的規格值（CPU／記憶體／硬碟／OS 版本）。

    使用者 2026-08-15 要求的關卡。自由填寫欄抓歪不會報錯，唯一有效的防線就是有人看過；
    沒審過的值標成 pending，不參與任何比對，避免「沒人看過的數字被當成事實」。
    """
    import doc_import

    try:
        return doc_import.review_document(conn, doc_id, body.values, session["username"])
    except doc_import.ReviewConflict as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/api/documents/{doc_id}/bind")
def documents_bind(
    doc_id: int,
    body: DocBindBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import doc_import

    try:
        doc_import.bind_document(conn, doc_id, body.asset_serial)
    except LookupError as e:          # Codex 1137：單據不存在＝404（跟審核一致），不是回 ok
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.get("/api/import/last")
def import_last(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    row = get_latest_import_log(conn)
    return dict(row) if row else None


def _field_mapping_view(raw: bytes) -> dict:
    """GET／PUT 回給畫面的形狀：各分頁＝啟用中的對應（跟匯入用的同一份），
    有停用中的列才多一個 `_disabled`：{分頁: {Excel 標題: 原本對應的欄位}}（Codex 1193：停用後還要能選回）。"""
    import hashlib

    import excel_import as _ei
    out: dict = _ei.parse_mapping(raw)
    disabled = {sh: cols for sh, cols in _ei.parse_disabled(raw).items() if cols}
    if disabled:
        out[_ei.DISABLED_KEY] = disabled
    # Codex 1185：設定檔的版本（內容雜湊）。儲存時帶回 base_version，跟現況不同＝別人先存過 → 409，
    # 不讓拿舊畫面存檔的人把別人剛停用的欄位又恢復成匯入。
    out[MAPPING_VERSION_KEY] = hashlib.sha256(raw).hexdigest()
    return out


MAPPING_VERSION_KEY = "_version"

# 審查 P3（2.065.1）：base_version 比對到寫檔之間要有鎖，不然兩人毫秒內同時存，兩個都比對通過、後寫的蓋掉先寫的。
# 用旁邊的 .lock 檔（os.open O_CREAT|O_EXCL，Windows／Linux 都是原子的、跨執行緒也跨行程），不靠 fcntl／msvcrt。
MAPPING_LOCK_WAIT_S = 5.0     # 等別人存完最多等幾秒；等不到回 409 請他重試
MAPPING_LOCK_STALE_S = 30.0   # 鎖檔超過這麼久還在＝上一個行程死在半路沒清，視為過期清掉


def _clear_stale_lock(lock: Path) -> None:
    """清掉過期鎖（審查 P3，2.065.2）。不能直接 unlink：B 看到過期、A 同時清掉舊鎖又建了新鎖，
    B 的 unlink 會刪到 A 的新鎖（兩人同時進臨界區）。改成先 rename 到唯一檔名（rename 是原子的，
    同一個檔只有一個人搬得走），搬到手再確認搬到的真的是過期的那個：
    - rename 失敗（FileNotFoundError 等）＝別人已處理，呼叫端回頭重搶。
    - 搬到的其實是剛建的新鎖（mtime 不過期）＝搬錯了，原子地放回去（os.link 目的地存在會失敗，不會蓋掉別人）。"""
    import os
    import time

    tmp = lock.with_name(f"{lock.name}.stale.{os.getpid()}.{time.time_ns()}")
    try:
        os.rename(str(lock), str(tmp))
    except OSError:
        return                                    # 別人已清掉／已釋放：回頭重搶
    try:
        if time.time() - tmp.stat().st_mtime > MAPPING_LOCK_STALE_S:
            return                                # 確實過期：finally 刪掉
        try:
            os.link(str(tmp), str(lock))          # 搬錯：放回去（原子，lock 已存在就失敗）
        except OSError:
            # 放不回去（第三人已搶到新鎖，或檔案系統不支援 hard link）：那個新鎖的主人已在臨界區外被插隊，
            # 只能讓它照舊跑完；機率極低（要在數十毫秒內三方交錯），記一筆給事後查。
            import logging
            logging.getLogger(__name__).warning("mapping lock: 誤搬了未過期的鎖且放不回去 %s", lock)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:                 # Windows 上檔案仍被占用時刪不掉：留著暫存檔不影響存檔，不可變成 500（審查 P3）
            pass


@contextmanager
def _mapping_file_lock(path: Path):
    import os
    import time

    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + MAPPING_LOCK_WAIT_S
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > MAPPING_LOCK_STALE_S:
                    _clear_stale_lock(lock)       # 過期鎖：搬走再刪，然後重搶（搶輸的照樣再等）
                    continue
            except FileNotFoundError:
                continue                          # 剛好被釋放：馬上再搶
            if time.monotonic() >= deadline:
                raise HTTPException(409, "欄位對應正在被別人儲存，請稍後重新整理再改") from None
            time.sleep(0.05)
    try:
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)                          # 審查 P3：os.write 拋錯也要關 fd（不然 Windows 上鎖檔刪不掉）
        yield
    finally:
        lock.unlink(missing_ok=True)


@router.get("/api/import/field-mapping")
def import_field_mapping(session: sqlite3.Row = Depends(require_auth)):
    """D14精神：欄位對應可調整、不寫死。回傳目前的field_mapping.json內容（含停用中的列，見 _field_mapping_view）。"""
    return _field_mapping_view(MAPPING_PATH.read_bytes())


@router.put("/api/import/field-mapping")
def update_import_field_mapping(
    body: FieldMappingBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """value為「不匯入」的項目不寫進分頁對應（匯入照舊看不到它），改記在 `_disabled`
    （記住原本對應的欄位），畫面才列得出來、改回「匯入」（Codex 1193）。其餘value要對得上目標資料表
    的實際欄位名稱（PRAGMA table_info動態查，不寫死欄位清單，schema改了這裡自動跟上）。

    body 裡底線開頭的鍵（GET 回去的 `_disabled`，畫面原封送回）忽略，以檔案現況為準。
    停用中的列：這次送「匯入＋欄位」→ 移回分頁對應；送「不匯入」或沒送 → 維持停用。

    _comment說明欄位：load_mapping()讀取時會把它拿掉（不是真的分頁對應資料），這裡存檔
    要把原本檔案裡的_comment原封放回去，不然存一次設定檔裡給後人看的說明文字就消失了。
    """
    with _mapping_file_lock(MAPPING_PATH):
        return _update_field_mapping_locked(body, conn)


def _update_field_mapping_locked(body: FieldMappingBody, conn: sqlite3.Connection) -> dict:
    """update_import_field_mapping 的本體，一定在 _mapping_file_lock 裡跑：讀檔、比 base_version、寫檔是同一段，
    第二個人要等第一個人寫完才讀得到檔，讀到的已是新版 → 版本不同 → 409（不會互蓋）。"""
    import hashlib

    import excel_import as _ei
    existing_bytes = MAPPING_PATH.read_bytes()
    if body.base_version is not None and body.base_version != hashlib.sha256(existing_bytes).hexdigest():
        raise HTTPException(409, "欄位對應剛被別人改過，請重新整理後再改（避免蓋掉別人的設定）")
    old_active = _ei.parse_mapping(existing_bytes)
    old_disabled = _ei.parse_disabled(existing_bytes)

    cleaned: dict[str, dict[str, str]] = {}
    newly_disabled: dict[str, dict[str, str]] = {}
    for sheet, columns in body.mapping.items():
        if sheet.startswith("_"):
            continue
        if sheet not in SHEET_CONFIG:
            raise HTTPException(400, f"不支援的分頁：{sheet}")
        if not isinstance(columns, dict):
            raise HTTPException(400, f"{sheet}分頁的格式不對")
        valid_columns = _table_columns(conn, SHEET_CONFIG[sheet]["table"])
        cleaned[sheet] = {}
        for excel_header, system_field in columns.items():
            if not isinstance(system_field, str):
                raise HTTPException(400, f"{sheet}分頁「{excel_header}」的值格式不對")
            if system_field == NO_IMPORT_SENTINEL:
                was = old_active.get(sheet, {}).get(excel_header) or old_disabled.get(sheet, {}).get(excel_header)
                if was:
                    newly_disabled.setdefault(sheet, {})[excel_header] = was
                continue
            if system_field not in valid_columns:
                raise HTTPException(
                    400, f"{sheet}分頁「{excel_header}」對應到不存在的欄位：{system_field}"
                )
            cleaned[sheet][excel_header] = system_field
        # Codex 1187：資產序號是三個分頁對回資產的鑰匙。設成「不匯入」的話，硬體整列被略過、
        # 人員／軟體被默默略過且錯誤數 0——直接擋下，不給停用。
        had_key = "asset_serial" in old_active.get(sheet, {}).values()
        if had_key and "asset_serial" not in cleaned[sheet].values():
            raise HTTPException(400, f"{sheet}分頁的「資產序號」是必要欄位（用來對回資產），不能設為不匯入")

    # 停用清單：舊的（這次沒提到的照留）＋這次新停用的，扣掉這次已啟用的
    disabled: dict[str, dict[str, str]] = {}
    for sheet in list(old_disabled) + [s for s in newly_disabled if s not in old_disabled]:
        cols = {**old_disabled.get(sheet, {}), **newly_disabled.get(sheet, {})}
        cols = {h: f for h, f in cols.items() if h not in cleaned.get(sheet, {})}
        if cols:
            disabled[sheet] = cols

    existing_raw = json.loads(existing_bytes.decode("utf-8"))
    to_write = {}
    if "_comment" in existing_raw:
        to_write["_comment"] = existing_raw["_comment"]
    to_write.update(cleaned)
    if disabled:
        to_write[_ei.DISABLED_KEY] = disabled

    text = json.dumps(to_write, ensure_ascii=False, indent=2) + "\n"
    # 寫 bytes 不寫 text：Windows 的 write_text 會把換行換成 CRLF，回給畫面的 _version（text 的雜湊）就跟檔案對不上，
    # 下一次存檔必 409（Linux 正式機不受影響，但開發機／測試會踩到）
    data = text.encode("utf-8")
    MAPPING_PATH.write_bytes(data)
    return _field_mapping_view(data)


def _cia_commit(conn: sqlite3.Connection, username: str, filename: str, contents: bytes,
                mapping: dict | None = None) -> dict:
    """CIA 清冊真正寫入的那一段。一步式與兩段式的確認寫入共用這一支，所以兩條路寫進去的東西一模一樣
    （test_cia_import_two_stage 用同一份檔比對兩條路的資料庫結果）。

    2.055.4（第 20 批審查 P2-1）：資料與匯入紀錄**同一筆交易**——import_excel(commit=False) → 匯入紀錄(commit=False)
    → 一次 commit；任何一步失敗整筆 rollback（以前紀錄寫失敗時資料已經進去、卻沒有匯入紀錄）。
    原始檔在 commit 成功之後才存（失敗的匯入不會蓋掉上一份存檔）。mapping：呼叫端已讀好並雜湊過的欄位對應。
    """
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        try:
            summary = import_excel(tmp_path, conn, commit=False, mapping=mapping)
        except Exception as exc:  # noqa: BLE001 - openpyxl對壞檔案的錯誤型態不一，統一攔截如實回報
            conn.rollback()
            raise HTTPException(400, f"匯入失敗，請確認檔案格式：{exc}") from exc
        try:
            create_import_log(
                conn,
                imported_by=username,
                hardware_count=summary["sheets"].get("硬體", {}).get("inserted", 0)
                + summary["sheets"].get("硬體", {}).get("updated", 0),
                personnel_count=summary["sheets"].get("人員", {}).get("inserted", 0)
                + summary["sheets"].get("人員", {}).get("updated", 0),
                software_count=summary["sheets"].get("軟體", {}).get("inserted", 0)
                + summary["sheets"].get("軟體", {}).get("updated", 0),
                error_count=len(summary["errors"]),
                source="cia_excel", file_name=filename, commit=False,
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        tmp_path.unlink(missing_ok=True)

    # 9.3 CIA 修正包（2.060.0）：匯入已 commit 之後，自動核對未結案的修正（已套用／CIA 已另改／資產已消失），記下是哪一份檔。
    # 刻意放在匯入交易之外：核對出錯不能讓 CIA 匯入失敗；錯誤照實回在 summary["cia_fix"] 並印出（不吞），下次匯入會再核對一次。
    try:
        import hashlib

        import cia_fix

        summary["cia_fix"] = cia_fix.reconcile_after_import(conn, filename, hashlib.sha256(contents).hexdigest(), username)
    except Exception as exc:  # noqa: BLE001 - 見上：不影響匯入，錯誤回給畫面
        if conn.in_transaction:
            conn.rollback()
        print(f"[import] CIA 修正包自動核對失敗（不影響匯入）：{exc}")
        summary["cia_fix"] = {"error": f"CIA 修正包自動核對失敗：{exc}"}
    # 9.4 CIA 補登（P2）：同樣在匯入 commit 之後、自己的交易；補登過的 VM 現在 CIA 有登記 → 已登記。出錯不影響匯入、照實回報。
    try:
        import hashlib

        import cia_reg

        summary["cia_reg"] = cia_reg.reconcile_after_import(conn, filename, hashlib.sha256(contents).hexdigest(), username)
    except Exception as exc:  # noqa: BLE001 - 不影響匯入，錯誤回給畫面
        if conn.in_transaction:
            conn.rollback()
        print(f"[import] CIA 補登自動核對失敗（不影響匯入）：{exc}")
        summary["cia_reg"] = {"error": f"CIA 補登自動核對失敗：{exc}"}

    # 原始檔留一份（只留最新的），之後「匯出原始檔」才有東西可以給。資料已經 commit 了才存；
    # 存檔失敗不該讓匯入整個失敗——少一份存檔是小事，但要留下痕跡，不能讓人以為有存到。
    try:
        import import_export

        import_export.save_original("cia_excel", filename or "upload", contents)
    except Exception as exc:  # noqa: BLE001
        print(f"[import] 原始檔保存失敗（cia_excel，不影響匯入）：{exc}")
    return summary


@router.post("/api/import/excel")
def import_excel_upload(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """一步式（上傳就寫入）。2.055.0 起畫面改走兩段式（/preview → /confirm）。
    2.056.0（兩段式定案，一步式不可留成後門）：缺省關閉回 410；部署明確設 WEBIT_ENABLE_ONESTEP_CIA_IMPORT=1 才開
    （查過：畫面、.project、relay、patch／離線工具都沒有呼叫，只有測試；同 /api/admin/reset-inventory 的做法）。

    故意用sync def（不是async def）：UploadFile.file是同步可讀的SpooledTemporaryFile，
    sync路由可以直接讀，不需要await。

    ⚠️ 原本這裡寫著「FastAPI 會把 get_db 依賴跟 sync 路由排進同一個 threadpool worker
    執行緒」——那個假設是錯的，並行時兩者可能落在不同 worker，會炸
    sqlite3「不同執行緒不能共用連線」。真正的修正在 db.get_connection()
    （check_same_thread=False），不是靠 sync/async 的寫法去賭執行緒。
    """
    import os
    if os.environ.get("WEBIT_ENABLE_ONESTEP_CIA_IMPORT") != "1":
        raise HTTPException(410, "CIA 資產清冊改成兩段式：請在「資料匯入」先預覽，確認後才寫入（POST /api/import/excel/preview → /confirm）。")
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 檔案")
    return _cia_commit(conn, session["username"], file.filename, file.file.read())


@router.post("/api/import/excel/preview")
def import_excel_preview(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """兩段式第一段（2.055.0，待決定 5）：上傳 → 在正式庫的記憶體副本上跑同一支 import_excel，
    回新增／更新／沒變／拒收與逐筆差異，**不寫正式庫**。回 token＋file_sha256，確認寫入要帶回來。"""
    import cia_import_preview

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 檔案")
    data = file.file.read(cia_import_preview.MAX_BYTES + 1)
    try:
        return cia_import_preview.preview(conn, data, file.filename, session["username"])
    except cia_import_preview.TooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except cia_import_preview.TooMany as exc:
        raise HTTPException(429, str(exc)) from exc
    except cia_import_preview.StorageError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - openpyxl對壞檔案的錯誤型態不一，統一攔截如實回報（同一步式）
        raise HTTPException(400, f"預覽失敗，請確認檔案格式：{exc}") from exc


class CiaConfirmBody(BaseModel):
    token: str
    file_sha256: str


@router.post("/api/import/excel/confirm")
def import_excel_confirm(
    body: CiaConfirmBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """兩段式第二段（cia_import_preview.confirm）：只有建立預覽的人能確認（403）；原子地搶下代號（同時兩個確認只有一個寫，
    另一個 404）；檔案、欄位對應、資料起點的檢查跟寫入在同一個 BEGIN IMMEDIATE 交易裡，對不上 409；
    寫入走一步式同一支 _cia_commit。代號用一次就作廢。"""
    import cia_import_preview

    username = session["username"]
    try:
        return cia_import_preview.confirm(conn, body.token, body.file_sha256, username,
                                          lambda c, fn, data, mapping: _cia_commit(c, username, fn, data, mapping))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except cia_import_preview.NotOwner as exc:
        raise HTTPException(403, str(exc)) from exc
    except cia_import_preview.StorageError as exc:
        raise HTTPException(503, str(exc)) from exc
    except cia_import_preview.StalePreview as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/import/rvtools")
def import_rvtools_upload(
    file: UploadFile = File(...),
    data_at: str | None = Form(None),
    force: bool = Form(False),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """S19 VC 採集器：吃一份 RVTools 匯出的 vCenter 盤點（vInfo 分頁）。

    每台 VM 走身分解析：對到既有資產就更新機器事實（不碰業務欄位），新的建成 VC- 資產，
    判不準的進人工審核佇列（不自動合併）。真實 vCenter 資料，非假資料。

    ## 時序保護（data_at / force）

    匯入是「後蓋前」，沒有時間概念——不小心傳了一份舊的匯出檔，裡面的 os／is_vm
    就會直接蓋掉比較新的值，而且系統沒有欄位變更歷史，蓋掉就救不回來
    （只能翻每日備份，超過保留天數就沒了）。

    所以這裡收 data_at（前端帶上傳檔案的最後修改時間），跟上次成功匯入的時間比：
    比上次舊就先擋下來、回報兩邊時間讓人確認，確定要覆蓋才帶 force=true 再送一次。
    沒帶 data_at 就照舊直接匯（例如 curl 手動呼叫），不強制。
    """
    import rvtools_import
    from db import get_setting, set_setting

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 RVTools 匯出的 .xlsx 檔案")

    prev_at = get_setting(conn, RVTOOLS_LAST_DATA_AT, "") or ""
    if data_at and prev_at and not force and data_at < prev_at:
        # 用 409 而不是 400：這不是「檔案有問題」，是「請你確認一下」，
        # 前端要據此跳確認框而不是當成錯誤紅字。
        raise HTTPException(
            409,
            {
                "code": "stale_import",
                "message": "這份檔案的資料時間比系統現有的舊，直接匯入會用舊資料覆蓋新的。",
                "incoming_data_at": data_at,
                "current_data_at": prev_at,
                "hint": "確定要覆蓋就再送一次並帶 force=true。",
            },
        )

    contents = file.file.read()
    # 原始檔留一份（只留最新的），之後「匯出原始檔」才有東西可以給。
    # 存檔失敗不該讓匯入整個失敗——資料已經進去了，少一份存檔是小事，
    # 但要留下痕跡，不能讓人以為有存到。
    try:
        import import_export

        import_export.save_original("rvtools", file.filename or "upload", contents)
    except Exception as exc:  # noqa: BLE001
        print(f"[import] 原始檔保存失敗（rvtools，不影響匯入）：{exc}")

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)
    try:
        try:
            summary = rvtools_import.import_rvtools(tmp_path, conn)
        except ValueError as exc:
            # 「這份檔不對」——使用者自己就能判斷、能修（例如根本沒有 vInfo 分頁）
            raise HTTPException(400, f"匯入失敗，這份檔看起來不是 RVTools 匯出：{exc}") from exc
        except Exception as exc:  # noqa: BLE001
            # 其他例外是**我們自己的 bug**，不是使用者的檔案有問題。
            # 2026-08-20 踩過：五個貨真價實的 RVTools 匯出檔全部收到
            # 「請確認是 RVTools 匯出的檔：Object of type datetime is not JSON serializable」
            # ——訊息把責任推給使用者，害人去反覆檢查根本沒問題的檔案。
            # 分不出來就不要亂猜原因，如實說是系統的錯，並把錯誤原文給出來。
            raise HTTPException(
                500,
                f"匯入時系統發生錯誤（不是你的檔案有問題，請把這段訊息回報）："
                f"{type(exc).__name__}: {exc}",
            ) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    create_import_log(
        conn, imported_by=session["username"],
        hardware_count=summary["inserted"] + summary["updated"],
        personnel_count=0, software_count=0, error_count=len(summary["errors"]),
        source="rvtools", file_name=file.filename,
        # 這份是「哪天從 vCenter 匯出的」，不是「哪天匯進系統」。取不到就存 None，
        # 不拿匯入時間頂替——「不知道多舊」跟「是今天的」是完全不同的兩句話。
        exported_at=rvtools_import.export_time_from_filename(file.filename),
    )

    # 記下這份的資料時間，供下次比對。強制覆蓋舊資料時不要往回退，
    # 否則之後每一份新檔都會被拿來跟這個舊時間比，保護就失效了。
    if data_at and (not prev_at or data_at > prev_at):
        set_setting(conn, RVTOOLS_LAST_DATA_AT, data_at)
    summary["data_at"] = data_at or None
    summary["previous_data_at"] = prev_at or None
    return summary


@router.get("/api/import/log")
def list_import_log_endpoint(
    source: str | None = None, limit: int = 20,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """最近的匯入紀錄——2026-08-19 使用者原話「要有紀錄表讓我查」：一次要匯好幾份
    RVTools檔案時，需要看得出哪些已經匯過（檔名+時間），不用憑記憶判斷。
    source 篩選：rvtools/cia_excel/dynassets/scan_import；不帶就全部混著回。
    """
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit 只接受 1–100")
    return [dict(r) for r in list_import_log(conn, source=source, limit=limit)]


@router.get("/api/import/dynassets/export-template")
def dynassets_export_template(session: sqlite3.Row = Depends(require_auth)):
    """空白範本——同 segments 那支的理由。查證時發現的落差：畫面文案只講
    「需含 IP 欄」，但實際上還吃主機名／MAC／OS，範本要把全部欄位列出來，
    不能讓人以為只填 IP 就夠。
    """
    import dynassets_import

    headers, example = dynassets_import.export_template_rows()
    wb = Workbook()
    ws = wb.active
    ws.title = "存活清單範本"
    ws.append(headers)
    ws.append(example)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from urllib.parse import quote

    fname = "存活清單_空白範本.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/api/import/{source}/export-template")
def import_export_template(source: str, session: sqlite3.Row = Depends(require_auth)):
    """其餘匯入的範例檔（系統類別／業務系統對照表／RVTools／CIA）。
    2026-09-10 使用者：「每一個都要範例檔案，不然 USER 不會知道要匯入什麼資料」。
    範例列以「範例」開頭，原封不動匯回來會被略過，見 import_templates。"""
    import import_templates
    from urllib.parse import quote

    try:
        fname, data = import_templates.build(source)
    except KeyError:
        raise HTTPException(404, f"沒有這種匯入的範例檔：{source}") from None
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


# ===== 每一種匯入都配一個匯出（2026-09-09）=====
# 使用者：「Dynaset 有匯入但沒有匯出的功能……我要選原始檔、Excel 的匯出，
# 跟 dump 格式的匯出。也要有 dump 格式的匯入」，並補「rvtools、CIA 資產清冊都要」。
#
# 三種格式回答的是**不同的問題**，不是同一份東西的三種包裝：
#   原始檔 = 當初送進來的那個檔（可能有系統看不懂而丟掉的欄位）
#   Excel  = 現在系統裡是什麼（正規化之後的）
#   dump   = 整包搬到另一台
# 混為一談會讓人以為匯出的 Excel 可以拿去跟來源系統對帳。
#
# ⚠️ dump 目前**不加密**（使用者 2026-09-09：「先不加密」）。裡面是完整的
# 資產／存活清單，畫面上要明講這件事，不要讓人當備份寄出去。


@router.get("/api/import/{source}/export/original")
def export_original(
    source: str,
    session: sqlite3.Row = Depends(require_auth),
):
    """把當初上傳的那個檔原樣送回去。

    **沒有就回 404 並說清楚為什麼**——這個功能上線之前的匯入沒有留原始檔
    （舊流程處理完就把暫存檔刪了）。這種時候不可以拿重新產生的檔頂替：
    那會讓人拿它去跟來源系統對帳，對不起來卻找不到原因。
    """
    import import_export

    try:
        path = import_export.original_for(source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if path is None:
        raise HTTPException(404, "這個來源沒有留原始檔——可能是還沒匯入過，"
                                 "或那次匯入早於「保留原始檔」這個功能上線之前")
    from urllib.parse import quote

    return StreamingResponse(
        io.BytesIO(path.read_bytes()),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name)}"},
    )


@router.get("/api/import/{source}/export/excel")
def export_excel(
    source: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """現在資料庫裡這個來源的內容，做成 Excel。"""
    import import_export

    try:
        headers, rows = import_export.export_rows(conn, source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    def _xl(v):
        # Excel 不收控制字元（\x00-\x08、\x0b…），openpyxl 遇到直接丟例外→整份匯出 500。
        # 從別的系統複製貼上的資料常夾帶，拿掉不影響可讀性（原始檔匯出不經過這裡，照原樣）。
        if v is None:
            return ""
        return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v

    wb = Workbook()
    ws = wb.active
    ws.title = import_export.SOURCES[source].label[:31]
    ws.append([_xl(h) for h in headers])
    for r in rows:
        ws.append([_xl(v) for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from urllib.parse import quote

    fname = f"{import_export.SOURCES[source].label}_{_now_local()[:10]}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/api/import/{source}/export/dump")
def export_dump(
    source: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """整包搬家用的格式（gzip 過的 JSON）。**目前不加密。**"""
    import import_export

    try:
        data = import_export.build_dump(conn, source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    from urllib.parse import quote

    fname = f"{source}_{_now_local()[:10].replace('-', '')}.webit3dump"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/gzip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


def data_date_of(name: str | None) -> str | None:
    """檔名裡的資料日期（匯出日期）：2026-08-28、2026_08_28、20260828 都認；找不到回 None（畫面寫「—」，不猜）。"""
    m = re.search(r"(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])", name or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _last_import(conn: sqlite3.Connection, key: str, orig) -> dict:
    """上次匯入：import_log 這個來源最新一筆（2.007.15：原始檔留存功能上線前匯入的批次沒有原始檔，改看匯入紀錄）；
    沒紀錄才用留存原始檔的時間。資料日期：紀錄裡的匯出時間，沒有就從檔名抓。"""
    at, date = None, None
    try:
        r = conn.execute("SELECT imported_at, exported_at, file_name FROM import_log WHERE source = ? "
                         "ORDER BY imported_at DESC, id DESC LIMIT 1", (key,)).fetchone()
    except sqlite3.OperationalError:   # 舊庫沒有 source／exported_at 欄：只看原始檔
        r = None
    if r:
        at = (r["imported_at"] or "")[:16] or None
        date = (r["exported_at"] or "")[:10] or data_date_of(r["file_name"])
    # Codex 1189：有成功匯入紀錄就以紀錄為準——留存檔比紀錄新（例如紀錄沒寫成、或殘留的較新檔）時，
    # 不能讓來源列的「上次匯入／資料日期」跟頁首「上次匯入」（看紀錄）講兩個不同的時間。沒紀錄才看留存檔。
    if orig is not None and not at:
        mt = _dt.datetime.fromtimestamp(orig.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        at, date = mt, data_date_of(orig.name) or date
    return {"last_imported_at": at, "data_date": date}


@router.get("/api/import/sources")
def list_import_sources(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """每個來源現在有什麼可以匯出——**畫面要靠這個決定按鈕能不能按**。

    「按了才發現沒有」跟「一開始就看得出沒有」差很多：前者會讓人以為系統壞了。
    """
    import import_export

    out = []
    for key, src in import_export.SOURCES.items():
        try:
            orig = import_export.original_for(key)
        except ValueError:
            orig = None
        try:
            _, rows = import_export.export_rows(conn, key)
            n = len(rows)
        except Exception:  # noqa: BLE001 - 單一來源壞掉不該讓整頁掛掉
            n = 0
        out.append({
            "key": key, "label": src.label, "row_count": n,
            "origin": src.origin,
            "has_original": orig is not None,
            "original_name": orig.name if orig else None,
            "original_size": orig.stat().st_size if orig else None,
            # 上次匯入＝留存原始檔的時間；資料日期＝檔名裡的日期（例：RVTools_export_all_2026-08-28 → 2026-08-28），
            # 2.007.14 使用者：「沒有上次的匯入日期」「上次的匯入版本」
            **_last_import(conn, key, orig),
        })
    return {"sources": out}


@router.post("/api/import/dump/preview")
def import_dump_preview(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
):
    """先看這個 dump 是什麼，**不寫入任何東西**。

    為什麼一定要有這一步：dump 匯入會覆蓋現有資料，而檔名看不出裡面是哪個
    來源、幾筆、什麼時候匯出的。讓人先看一眼再按，比事後還原便宜得多。
    """
    import import_export

    try:
        body = import_export.read_dump(file.file.read())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"source": body.get("source"), "row_count": body.get("row_count"),
            "headers": body.get("headers", [])[:50],
            "label": import_export.SOURCES.get(body.get("source"), None)
            and import_export.SOURCES[body["source"]].label}


@router.post("/api/import/dynassets")
def import_dynassets_upload(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """dynassets 匯入：吃「存活掃描＋CMDB」清單（.csv／.xlsx），跟 CIA 登記對帳。

    每列走身分解析：對到既有資產就更新機器事實（不碰業務欄位）、掃到但沒登記的建成
    DYN- 資產（＝漏登記／帳外資產）、判不準的進人工審核佇列（不自動合併）。
    dynassets 是每次重新產生的存活快照，不做 RVTools 那種時序保護。
    """
    import dynassets_import

    fn = (file.filename or "").lower()
    if not (fn.endswith(".csv") or fn.endswith(".xlsx")):
        raise HTTPException(400, "只接受 dynassets 的 .csv 或 .xlsx 檔案")
    suffix = ".xlsx" if fn.endswith(".xlsx") else ".csv"

    contents = file.file.read()
    # 原始檔留一份（只留最新的），之後「匯出原始檔」才有東西可以給。
    # 存檔失敗不該讓匯入整個失敗——資料已經進去了，少一份存檔是小事，
    # 但要留下痕跡，不能讓人以為有存到。
    try:
        import import_export

        import_export.save_original("dynassets", file.filename or "upload", contents)
    except Exception as exc:  # noqa: BLE001
        print(f"[import] 原始檔保存失敗（dynassets，不影響匯入）：{exc}")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)
    try:
        try:
            summary = dynassets_import.import_dynassets(tmp_path, conn)
        except Exception as exc:  # noqa: BLE001 - 壞檔/非dynassets格式統一如實回報
            raise HTTPException(400, f"匯入失敗，請確認是 dynassets 存活清單：{exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    create_import_log(
        conn, imported_by=session["username"],
        hardware_count=summary["inserted"] + summary["updated"],
        personnel_count=0, software_count=0, error_count=len(summary["errors"]),
        source="dynassets", file_name=file.filename,
    )
    return summary


__all__ = [
    'documents_import',
    'documents_list',
    'documents_summary',
    'documents_download',
    'documents_review',
    'documents_bind',
    'import_last',
    'import_field_mapping',
    'update_import_field_mapping',
    'import_excel_upload',
    'import_excel_preview',
    'import_excel_confirm',
    'CiaConfirmBody',
    '_cia_commit',
    'import_rvtools_upload',
    'list_import_log_endpoint',
    'dynassets_export_template',
    'import_export_template',
    'export_original',
    'export_excel',
    'export_dump',
    'list_import_sources',
    'import_dump_preview',
    'import_dynassets_upload',
]
