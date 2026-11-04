"""機櫃圖對帳 API（2026-10-23）。邏輯在 rack_diagram.py。讀寫：登入即可；只寫自己的 rack_diagram_* 表，不改 CIA／位置。"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["rack_diagram_imports_endpoint", "rack_diagram_import_endpoint", "rack_diagram_reconcile_endpoint",
           "rack_diagram_diff_endpoint", "rack_diagram_fix_xlsx_endpoint", "rack_diagram_export_endpoint",
           "rack_diagram_rules_endpoint", "rack_diagram_review_endpoint", "rack_diagram_reviews_endpoint"]


class ReviewBody(BaseModel):
    action: str            # confirm（確認（照片對））／misread（判讀錯了）
    note: str | None = None

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _user(session) -> str | None:
    try:
        return session["username"]
    except (KeyError, IndexError, TypeError):
        return None


def _xlsx(buf, fname: str, n: int):
    from urllib.parse import quote
    return StreamingResponse(buf, media_type=_XLSX, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}", "X-Row-Count": str(n)})


@router.get("/api/rack-diagram/imports")
def rack_diagram_imports_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import rack_diagram
    return rack_diagram.list_imports(conn)


@router.get("/api/rack-diagram/rules")
def rack_diagram_rules_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import rack_diagram
    return {"sites": list(rack_diagram.SITES), "alias": rack_diagram.alias_rules(conn), "results": list(rack_diagram.RESULTS)}


@router.post("/api/rack-diagram/import")
def rack_diagram_import_endpoint(file: UploadFile = File(...), site: str = Form(...),
                                 default_source: str | None = Form(None), default_shot_date: str | None = Form(None),
                                 default_confidence: str | None = Form(None),
                                 session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import rack_diagram
    try:
        return rack_diagram.import_diagram(conn, site, file.filename or "", file.file.read(), _user(session),
                                           {"source": default_source, "shot_date": default_shot_date,
                                            "confidence": default_confidence})
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/rack-diagram/imports/{iid}/reconcile")
def rack_diagram_reconcile_endpoint(iid: int, session: sqlite3.Row = Depends(require_auth),
                                    conn: sqlite3.Connection = Depends(get_db)):
    import rack_diagram
    try:
        return rack_diagram.reconcile(conn, iid)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/api/rack-diagram/imports/{iid}/diff")
def rack_diagram_diff_endpoint(iid: int, session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    import rack_diagram
    try:
        return rack_diagram.diff_previous(conn, iid)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/api/rack-diagram/imports/{iid}/cia-fix.xlsx")
def rack_diagram_fix_xlsx_endpoint(iid: int, session: sqlite3.Row = Depends(require_auth),
                                   conn: sqlite3.Connection = Depends(get_db)):
    import rack_diagram
    try:
        buf, n = rack_diagram.fix_xlsx(conn, iid)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return _xlsx(buf, f"CIA待更正_機櫃圖對帳_{datetime.now():%Y%m%d}.xlsx", n)


@router.get("/api/rack-diagram/export.xlsx")
def rack_diagram_export_endpoint(room: str | None = None, session: sqlite3.Row = Depends(require_auth),
                                 conn: sqlite3.Connection = Depends(get_db)):
    import rack_diagram
    buf, n = rack_diagram.export_xlsx(conn, room or None)
    return _xlsx(buf, f"系統產生_機櫃圖_{room or '全部機房'}_{datetime.now():%Y%m%d}.xlsx", n)


@router.post("/api/rack-diagram/rows/{row_id}/review")
def rack_diagram_review_endpoint(row_id: int, body: ReviewBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                 conn: sqlite3.Connection = Depends(get_db)):
    """一筆差異的判定：只新增紀錄（誰／何時／備註／來源照片），不寫 CIA。"""
    import rack_diagram
    import write_guard
    try:
        write_guard.check(conn, request)
    except write_guard.TooManyFails as exc:
        raise HTTPException(429, str(exc))
    except write_guard.NeedPassword as exc:
        raise HTTPException(428, str(exc))
    try:
        return rack_diagram.review_row(conn, row_id, body.action, body.note, write_guard.actor(session, request))
    except PermissionError as exc:
        raise HTTPException(401, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/rack-diagram/imports/{iid}/reviews")
def rack_diagram_reviews_endpoint(iid: int, session: sqlite3.Row = Depends(require_auth),
                                  conn: sqlite3.Connection = Depends(get_db)):
    import rack_diagram
    try:
        return rack_diagram.review_history(conn, iid)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
