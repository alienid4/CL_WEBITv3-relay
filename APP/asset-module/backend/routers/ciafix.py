"""9.3 CIA 修正包（2.060.0，P1 寫法統一）的 API。邏輯都在 cia_fix.py，這裡只接線。

權限（access_policy）：讀＝登入即可；下載修正包（含整份 CIA 硬體頁）＝管理員；產包、規則設定、交回檔預覽／記錄＝管理員
（跟其他匯入同級）；逐筆改決定／撤回／重開＝一般帳號以上。寫入都過 write_guard（登入停用時可要求管理密碼）並記「誰」。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()


class CiaFixCreateBody(BaseModel):
    fingerprint: str
    resend_rejected: bool = False


class CiaFixIdsBody(BaseModel):
    ids: list[int]
    note: str | None = None


class CiaFixDecideBody(BaseModel):
    ids: list[int]
    decision: str
    reject_code: str | None = None
    note: str | None = None


class CiaFixSettingsBody(BaseModel):
    room_alias: dict[str, str] | None = None
    template_texts: list[str] | None = None


def _w(conn, request, session) -> str:
    from routers.overlaps import _actor, _guard

    _guard(conn, request)
    return _actor(session, request)


@router.get("/api/cia-fix/summary")
def cia_fix_summary_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """卡片數字＋上次自動核對＋最後一次 CIA 匯入。數字＝/api/cia-fix/items 同一份（verify_numbers 釘住）。"""
    import cia_fix

    return {"counts": cia_fix.counts(conn), "last_reconcile": cia_fix.last_reconcile(conn),
            "cia_import": cia_fix._last_cia_import(conn), "status_labels": cia_fix.STATUS}


@router.get("/api/cia-fix/batches")
def cia_fix_batches_endpoint(open_only: bool = False, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    import cia_fix

    return {"items": cia_fix.list_batches(conn, open_only=open_only)}


@router.get("/api/cia-fix/items")
def cia_fix_items_endpoint(status: str | None = None, batch_id: int | None = None, pkg_no: int | None = None,
                           unit: str | None = None, rule_id: str | None = None, upload: str | None = None,
                           session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """下鑽：status＝狀態代號／open／confirm（待人確認）；batch_id、pkg_no＝某一批、某一包；unit＝負責單位；rule_id＝規則；
    upload＝交回檔的雜湊（那一次交回記錄了決定的列）。facets＝篩選下拉的選項。"""
    import cia_fix

    try:
        items = cia_fix.list_items(conn, status or None, batch_id, pkg_no, unit or None, rule_id or None, upload or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"total": len(items), "items": items, "facets": cia_fix.facets(conn)}


@router.get("/api/cia-fix/items/{item_id}/events")
def cia_fix_events_endpoint(item_id: int, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    import cia_fix

    return {"items": cia_fix.item_events(conn, item_id)}


@router.get("/api/cia-fix/rules")
def cia_fix_rules_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import cia_fix

    return cia_fix.rules_view(conn)


@router.put("/api/cia-fix/settings")
def cia_fix_settings_endpoint(body: CiaFixSettingsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    """真實對照（機房寫法 → 標準名稱、範本提示文字）存系統設定，不進版控。"""
    import cia_fix

    _w(conn, request, session)        # 寫入關卡；誰改的由操作紀錄中介層記（非 GET 一律記）
    return cia_fix.save_settings(conn, body.room_alias, body.template_texts)


@router.get("/api/cia-fix/preview")
def cia_fix_preview_endpoint(resend_rejected: bool = False, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    """產包前預覽：各規則幾筆、會切成幾包、逐筆明細；不寫庫。fingerprint 要帶回產包。"""
    import cia_fix

    return cia_fix.preview(conn, resend_rejected)


@router.post("/api/cia-fix/batches")
def cia_fix_create_endpoint(body: CiaFixCreateBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    import cia_fix

    by = _w(conn, request, session)
    try:
        return cia_fix.create_batch(conn, by, body.fingerprint, body.resend_rejected)
    except cia_fix.Stale as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/cia-fix/batches/{batch_id}/packages/{pkg_no}.xlsx")
def cia_fix_package_xlsx_endpoint(batch_id: int, pkg_no: int, session: sqlite3.Row = Depends(require_auth),
                                  conn: sqlite3.Connection = Depends(get_db)):
    import cia_fix

    try:
        buf, n, fname = cia_fix.package_xlsx(conn, batch_id, pkg_no)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    resp = _xlsx_response(buf, fname)
    resp.headers["X-Row-Count"] = str(n)
    return resp


def _ids_call(fn, body: CiaFixIdsBody, conn, by):
    try:
        return fn(conn, body.ids, body.note, by)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/cia-fix/items/decide")
def cia_fix_decide_endpoint(body: CiaFixDecideBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    import cia_fix

    by = _w(conn, request, session)
    try:
        return cia_fix.decide(conn, body.ids, body.decision, body.reject_code, body.note, by)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/cia-fix/items/withdraw")
def cia_fix_withdraw_endpoint(body: CiaFixIdsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import cia_fix

    return _ids_call(cia_fix.withdraw, body, conn, _w(conn, request, session))


@router.post("/api/cia-fix/items/reopen")
def cia_fix_reopen_endpoint(body: CiaFixIdsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    import cia_fix

    return _ids_call(cia_fix.reopen, body, conn, _w(conn, request, session))


def _read_upload(file: UploadFile) -> bytes:
    import cia_fix

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 檔案")
    data = file.file.read(cia_fix.MAX_UPLOAD_BYTES + 1)
    if len(data) > cia_fix.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "檔案超過 10MB")
    return data


@router.post("/api/cia-fix/upload/preview")
def cia_fix_upload_preview_endpoint(file: UploadFile = File(...), session: sqlite3.Row = Depends(require_auth),
                                    conn: sqlite3.Connection = Depends(get_db)):
    """CIA 管理人勾好的修正包交回：先看會記哪些決定、哪些列被改過（差異報告）。不寫庫。"""
    import cia_fix

    data = _read_upload(file)
    try:
        return cia_fix.upload_preview(conn, data, file.filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/cia-fix/upload/confirm")
def cia_fix_upload_confirm_endpoint(request: Request, file: UploadFile = File(...), file_sha256: str = Form(...),
                                    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import cia_fix

    by = _w(conn, request, session)
    data = _read_upload(file)
    try:
        return cia_fix.upload_confirm(conn, data, file.filename, file_sha256, by)
    except cia_fix.Stale as exc:
        raise HTTPException(409, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
