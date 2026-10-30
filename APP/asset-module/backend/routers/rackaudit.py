"""機櫃實體盤點（4 機房與容量，2026-10-27）的 API。邏輯都在 rack_inventory.py，這裡只接線。

讀：登入即可。寫（開始盤點、交結果、上傳、差異處理、結案）：管理員（_can_edit_nav，跟機櫃位置更正同一把尺）
＋寫入守門（登入停用時可要求管理密碼）。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["rack_audits_endpoint", "rack_audit_start_endpoint", "rack_audit_detail_endpoint", "rack_audit_xlsx_endpoint",
           "rack_audit_upload_endpoint", "rack_audit_rack_endpoint", "rack_audit_rack_save_endpoint",
           "rack_audit_diffs_endpoint", "rack_audit_diff_status_endpoint", "rack_audit_diff_act_endpoint",
           "rack_audit_summary_endpoint", "rack_audit_report_endpoint", "rack_audit_close_endpoint"]

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _guard(conn, request):
    import write_guard
    try:
        write_guard.check(conn, request)
    except write_guard.TooManyFails as exc:
        raise HTTPException(429, str(exc))
    except write_guard.NeedPassword as exc:
        raise HTTPException(428, str(exc))


def _actor(session, request) -> str:
    import write_guard
    return write_guard.actor(session, request)


def _writer(session, conn, request) -> str:
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以做機櫃盤點的寫入動作")
    _guard(conn, request)
    return _actor(session, request)


def _call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _file(buf, fname):
    from urllib.parse import quote
    return StreamingResponse(buf, media_type=_XLSX,
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


class AuditStartBody(BaseModel):
    room: str | None = None
    racks: list[dict] | None = None
    title: str | None = None


class AuditRackBody(BaseModel):
    room: str
    rack: str
    rows: dict = {}
    extras: list[dict] = []
    counted_by: str
    counted_on: str | None = None


class AuditDiffStatusBody(BaseModel):
    diff_key: str
    status: str
    reason: str | None = None


class AuditDiffActBody(BaseModel):
    diff_key: str
    action: str


@router.get("/api/rack-audits")
def rack_audits_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import rack_inventory
    return {"items": rack_inventory.list_audits(conn), "can_edit": _can_edit_nav(session, conn)}


@router.post("/api/rack-audits")
def rack_audit_start_endpoint(body: AuditStartBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    """開始盤點：凍結選定機房／機櫃今天的帳當基準。"""
    import rack_inventory
    by = _writer(session, conn, request)
    return _call(rack_inventory.start_audit, conn, body.room, body.racks, by, body.title)


@router.get("/api/rack-audits/{audit_id}")
def rack_audit_detail_endpoint(audit_id: int, session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    import rack_inventory
    return {**_call(rack_inventory.audit_detail, conn, audit_id), "can_edit": _can_edit_nav(session, conn)}


@router.get("/api/rack-audits/{audit_id}/xlsx")
def rack_audit_xlsx_endpoint(audit_id: int, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    """離線盤點檔：一櫃一張表、結果預設在、key 欄鎖住、隱藏表記盤點編號。"""
    import rack_inventory
    buf, fname = _call(rack_inventory.build_xlsx, conn, audit_id)
    return _file(buf, fname)


@router.post("/api/rack-audits/{audit_id}/upload")
async def rack_audit_upload_endpoint(audit_id: int, request: Request, file: UploadFile = File(...),
                                     session: sqlite3.Row = Depends(require_auth),
                                     conn: sqlite3.Connection = Depends(get_db)):
    """上傳填好的盤點檔：有錯整份不存、回每一格錯在哪（200＋ok=false）；沒填盤點人的櫃略過。"""
    import rack_inventory
    by = _writer(session, conn, request)
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "檔案太大（上限 20MB）")
    return _call(rack_inventory.parse_upload, conn, audit_id, data, by, file.filename)


@router.get("/api/rack-audits/{audit_id}/rack")
def rack_audit_rack_endpoint(audit_id: int, room: str, rack: str, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    import rack_inventory
    return _call(rack_inventory.rack_sheet, conn, audit_id, room, rack)


@router.put("/api/rack-audits/{audit_id}/rack")
def rack_audit_rack_save_endpoint(audit_id: int, body: AuditRackBody, request: Request,
                                  session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """網頁直接填一櫃（同一張表，預設在，只點例外）；再交＝取代，舊的留歷史。"""
    import rack_inventory
    by = _writer(session, conn, request)
    return _call(rack_inventory.submit_rack, conn, audit_id, body.room, body.rack, body.rows, body.extras,
                 body.counted_by, body.counted_on, by, "web")


@router.get("/api/rack-audits/{audit_id}/diffs")
def rack_audit_diffs_endpoint(audit_id: int, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import rack_inventory
    return {"items": _call(rack_inventory.diffs, conn, audit_id), "can_edit": _can_edit_nav(session, conn)}


@router.post("/api/rack-audits/{audit_id}/diffs/status")
def rack_audit_diff_status_endpoint(audit_id: int, body: AuditDiffStatusBody, request: Request,
                                    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import rack_inventory
    by = _writer(session, conn, request)
    return _call(rack_inventory.set_diff_status, conn, audit_id, body.diff_key, body.status, body.reason, by)


@router.post("/api/rack-audits/{audit_id}/diffs/act")
def rack_audit_diff_act_endpoint(audit_id: int, body: AuditDiffActBody, request: Request,
                                 session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """一鍵處理：不在 → 標記已不在櫃上；U 不符 → 位置更正（目標有別台會 409）。"""
    import rack_inventory
    import rack_layout
    by = _writer(session, conn, request)
    try:
        return _call(rack_inventory.act, conn, audit_id, body.diff_key, body.action, by)
    except rack_layout.Occupied as exc:
        raise HTTPException(409, str(exc))


@router.get("/api/rack-audits/{audit_id}/summary")
def rack_audit_summary_endpoint(audit_id: int, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    import rack_inventory
    return _call(rack_inventory.summary, conn, audit_id)


@router.get("/api/rack-audits/{audit_id}/report.xlsx")
def rack_audit_report_endpoint(audit_id: int, session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    import rack_inventory
    buf, fname = _call(rack_inventory.report_xlsx, conn, audit_id)
    return _file(buf, fname)


@router.post("/api/rack-audits/{audit_id}/close")
def rack_audit_close_endpoint(audit_id: int, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import rack_inventory
    by = _writer(session, conn, request)
    return _call(rack_inventory.close_audit, conn, audit_id, by)
