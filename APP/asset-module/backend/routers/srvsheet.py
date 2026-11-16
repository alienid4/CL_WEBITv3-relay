"""伺服器資料彙整表（參考來源，2.060.20）的 API。邏輯都在 srv_sheet.py，這裡只接線。

權限（access_policy）：讀＝登入即可；上傳預覽／確認＝管理員。寫入過 write_guard 並記「誰」。
只寫 srv_sheet_* 三張表（參考資料快照），不寫 hardware、不改 CIA。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()


def _read(file: UploadFile) -> bytes:
    import srv_sheet

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 檔案（有密碼的請先在 Excel 另存不加密的）")
    data = file.file.read(srv_sheet.MAX_UPLOAD_BYTES + 1)
    if len(data) > srv_sheet.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "檔案超過 20MB")
    return data


def _call(fn, *a):
    import srv_sheet

    try:
        return fn(*a)
    except srv_sheet.Rejected as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/srv-sheet/preview")
def srv_sheet_preview_endpoint(file: UploadFile = File(...), session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    """讀檔、回列數與分頁名稱；不寫庫。"""
    import srv_sheet

    return _call(srv_sheet.preview, conn, _read(file), file.filename)


@router.post("/api/srv-sheet/confirm")
def srv_sheet_confirm_endpoint(request: Request, file: UploadFile = File(...), file_sha256: str = Form(...),
                               session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """整份取代上一份（快照）；sha256 要跟預覽時一樣。"""
    import srv_sheet
    from routers.overlaps import _actor, _guard

    _guard(conn, request)
    by = _actor(session, request)
    return _call(srv_sheet.confirm, conn, _read(file), file.filename, file_sha256, by)


@router.get("/api/srv-sheet/summary")
def srv_sheet_summary_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """最近一份＋各類數字（＝/api/srv-sheet/rows 同一份計算）＋匯入紀錄。"""
    import srv_sheet

    a = srv_sheet.analyze(conn)
    return {k: v for k, v in a.items() if k != "rows"} | {"history": srv_sheet.batches(conn)}


@router.get("/api/srv-sheet/rows")
def srv_sheet_rows_endpoint(kind: str = "all", field: str | None = None, q: str | None = None,
                            session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import srv_sheet

    return _call(srv_sheet.listing, conn, kind, field, q)


@router.get("/api/v2/machine/sources")
def machine_sources_endpoint(key: str, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """2.060.21（S11）主機頁「來源對照表」：每個欄位四種來源的值、採用哪個、為什麼；對不上又分不出的標 conflict。只讀。"""
    import field_sources

    try:
        return field_sources.compare(conn, key)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
