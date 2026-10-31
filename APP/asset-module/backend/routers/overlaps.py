"""機櫃「位置重疊待處理」API（2026-10-24）。邏輯在 overlap_queue.py。
讀：登入即可。寫（處理／復原）：管理員（_can_edit_nav，跟機櫃狀態、位置更正同一把尺）。"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["rack_overlaps_endpoint", "rack_overlap_decide_endpoint", "rack_overlap_undo_endpoint",
           "rack_overlap_history_endpoint", "rack_move_endpoint", "write_guard_get_endpoint", "write_guard_put_endpoint",
           "rack_field_edit_endpoint", "rack_field_edit_clear_endpoint", "rack_field_edits_endpoint",
           "rack_field_edits_xlsx_endpoint",
           "rack_move_check_endpoint", "rack_moves_endpoint", "rack_move_undo_endpoint",
           "rack_excel_preview_endpoint", "rack_excel_runs_endpoint", "rack_excel_run_endpoint", "rack_excel_apply_endpoint"]


def _guard(conn, request):
    """登入停用＋設定打開時要管理密碼（428）；錯太多次 429。"""
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


class MoveBody(BaseModel):
    key: str
    room: str | None = None
    rack: str
    u_start: int
    u_height: int
    note: str


class MoveUndoBody(BaseModel):
    force: bool = False
    note: str | None = None


class GuardBody(BaseModel):
    enabled: bool
    password: str | None = None


def _user(session) -> str | None:
    try:
        return session["username"]
    except (KeyError, IndexError, TypeError):
        return None


class DecideBody(BaseModel):
    room: str
    rack: str
    keys: list[str]
    action: str
    target_key: str | None = None
    relocate: dict | None = None
    note: str | None = None


class UndoBody(BaseModel):
    note: str | None = None


@router.get("/api/racks/overlaps")
def rack_overlaps_endpoint(room: str | None = None, rack: str | None = None,
                           session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import overlap_queue
    return {**overlap_queue.groups(conn, room or None, rack or None), "can_edit": _can_edit_nav(session, conn)}


@router.post("/api/racks/overlaps/decide")
def rack_overlap_decide_endpoint(body: DecideBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                 conn: sqlite3.Connection = Depends(get_db)):
    import overlap_queue
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以處理位置重疊")
    _guard(conn, request)
    try:
        return overlap_queue.decide(conn, body.room, body.rack, body.keys, body.action, _actor(session, request),
                                    body.note, body.target_key, body.relocate)
    except PermissionError as exc:
        raise HTTPException(401, str(exc))
    except LookupError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/racks/overlaps/{decision_id}/undo")
def rack_overlap_undo_endpoint(decision_id: int, body: UndoBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    import overlap_queue
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以復原")
    _guard(conn, request)
    try:
        return overlap_queue.undo(conn, decision_id, _actor(session, request), body.note)
    except PermissionError as exc:
        raise HTTPException(401, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/racks/overlaps/history")
def rack_overlap_history_endpoint(room: str | None = None, session: sqlite3.Row = Depends(require_auth),
                                  conn: sqlite3.Connection = Depends(get_db)):
    import overlap_queue
    return overlap_queue.history(conn, room or None)


@router.put("/api/racks/position/move")
def rack_move_endpoint(body: MoveBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                       conn: sqlite3.Connection = Depends(get_db)):
    """拖拉搬移／調高度：整個箱子（含畫成同一台的、管理卡）一起改位置；目標有別台就 409。走既有位置更正。"""
    import rack_layout
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以搬移")
    _guard(conn, request)
    try:
        return rack_layout.move_box(conn, body.key, body.room, body.rack, body.u_start, body.u_height, body.note,
                                    _actor(session, request))
    except rack_layout.Occupied as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class FieldEditBody(BaseModel):
    key: str
    field: str
    value: str
    note: str | None = None


@router.put("/api/racks/field-edit")
def rack_field_edit_endpoint(body: FieldEditBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    """面板雙擊改機型／IP／序號／備註（現場更正）：存自己的表、不動 CIA；留稽核（誰改的），進 CIA 待更正。"""
    import rack_field_edit
    import rack_layout
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以修改")
    _guard(conn, request)
    try:
        return rack_field_edit.set_edit(conn, rack_layout.machines(conn), body.key, body.field, body.value, body.note,
                                        _actor(session, request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/api/racks/field-edit")
def rack_field_edit_clear_endpoint(key: str, field: str, request: Request, session: sqlite3.Row = Depends(require_auth),
                                   conn: sqlite3.Connection = Depends(get_db)):
    """還原一欄現場更正：回到 CIA 的值。"""
    import rack_field_edit
    import rack_layout
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以還原")
    _guard(conn, request)
    try:
        return rack_field_edit.clear_edit(conn, rack_layout.machines(conn), key, field, _actor(session, request))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/racks/field-edits")
def rack_field_edits_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """CIA 待更正清單（現場更正還沒被 CIA 跟上的）＋最近異動紀錄。"""
    import rack_field_edit
    import rack_layout
    return {"items": rack_field_edit.pending_cia(conn, rack_layout.machines(conn)),
            "audit": rack_field_edit.audit(conn, None, 60), "can_edit": _can_edit_nav(session, conn)}


@router.get("/api/racks/field-edits.xlsx")
def rack_field_edits_xlsx_endpoint(session: sqlite3.Row = Depends(require_auth),
                                   conn: sqlite3.Connection = Depends(get_db)):
    """CIA 待更正（機型／IP／序號／備註）xlsx：給 CIA 負責人一次照改。"""
    import rack_field_edit
    import rack_layout
    from datetime import datetime
    from urllib.parse import quote

    buf, n = rack_field_edit.pending_xlsx(conn, rack_layout.machines(conn))
    fname = f"CIA待更正_欄位_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}", "X-Row-Count": str(n)})


@router.get("/api/racks/position/move-check")
def rack_move_check_endpoint(key: str, rack: str, room: str | None = None, u_start: int | None = None,
                             u_height: int | None = None, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    """拖拉確認框邊打邊驗：不寫入（所以是 GET），回 ok／錯誤種類（unknown_rack／out_of_range／collision…）／撞到誰。"""
    import rack_layout
    return rack_layout.move_check(conn, key, room or None, rack, u_start, u_height)


@router.get("/api/racks/position/moves")
def rack_moves_endpoint(room: str | None = None, rack: str | None = None, limit: int = 30,
                        session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """最近異動（拖拉搬移），新到舊；每列可復原。"""
    import rack_layout
    return {"items": rack_layout.recent_moves(conn, room or None, rack or None, limit), "can_edit": _can_edit_nav(session, conn)}


@router.post("/api/racks/position/moves/{move_id}/undo")
def rack_move_undo_endpoint(move_id: int, body: MoveUndoBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    """復原一次拖拉搬移（回到搬之前：之前的位置更正或 CIA 位置）。之後又被改過 → 409＋changes，要 force 才做。"""
    import rack_layout
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以復原搬移")
    _guard(conn, request)
    try:
        return rack_layout.undo_move(conn, move_id, _actor(session, request), body.force, body.note)
    except rack_layout.MoveChanged as exc:
        raise HTTPException(409, {"message": str(exc), "changes": exc.changes})
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/admin/write-guard")
def write_guard_get_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import write_guard
    return {"enabled": write_guard.enabled(conn), "auth_disabled": auth_disabled(conn)}


@router.put("/api/admin/write-guard")
def write_guard_put_endpoint(body: GuardBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    """開關「登入停用時，寫入動作要輸入管理密碼」。關掉時（如果現在是開的）也要管理密碼，不然等於沒有。"""
    import write_guard
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以改")
    if body.enabled and not write_guard.admin_exists(conn):
        raise HTTPException(400, "系統裡沒有 admin 帳號（或沒設密碼），打開會讓所有寫入都卡住，已取消")
    if (body.enabled and auth_disabled(conn)) or (not body.enabled and write_guard.enabled(conn)):
        # 打開（登入停用中）要先驗一次密碼，確定你知道它；關掉也要（不然等於沒有）
        try:
            write_guard.check(conn, request, body.password or "")
        except write_guard.TooManyFails as exc:
            raise HTTPException(429, str(exc))
        except write_guard.NeedPassword as exc:
            raise HTTPException(428, str(exc))
    write_guard.set_enabled(conn, body.enabled)
    try:
        activity.log(conn, username=_actor(session, request), ip=None, method="PUT", path="/api/admin/write-guard",
                     status=200, duration_ms=0, action=("寫入要管理密碼：開" if body.enabled else "寫入要管理密碼：關"))
    except Exception:  # noqa: BLE001 - 記錄失敗不該讓設定失敗
        pass
    return {"enabled": write_guard.enabled(conn)}


# ---------------------------------------------------------------- Excel 機櫃圖對照（2.007.20）
class ExcelApplyBody(BaseModel):
    run_id: int
    row_ids: list[int]


@router.post("/api/racks/excel-import/preview")
async def rack_excel_preview_endpoint(request: Request, room: str, file: UploadFile = File(...),
                                      session: sqlite3.Row = Depends(require_auth),
                                      conn: sqlite3.Connection = Depends(get_db)):
    """上傳機房的 Excel 機櫃圖，逐格比對系統（只讀，不改任何設備；結果存成一次對照）。限管理員。"""
    import rack_excel_import
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以做機櫃圖對照")
    data = await file.read()
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(400, "檔案超過 30 MB")
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx")
    try:
        return rack_excel_import.preview(conn, room, data, file.filename or "upload.xlsx", _actor(session, request))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/racks/excel-import/runs")
def rack_excel_runs_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import rack_excel_import
    return {"items": rack_excel_import.list_runs(conn), "can_edit": _can_edit_nav(session, conn)}


@router.get("/api/racks/excel-import/{run_id}")
def rack_excel_run_endpoint(run_id: int, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    import rack_excel_import
    try:
        return {**rack_excel_import.get_run(conn, run_id), "can_edit": _can_edit_nav(session, conn)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/racks/excel-import/apply")
def rack_excel_apply_endpoint(body: ExcelApplyBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    """套用勾選的格：先做資料庫快照，再逐台走位置更正（有稽核紀錄、可還原）。硬性不安全的格伺服器端會跳過。限管理員。"""
    import backup
    import rack_excel_import
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以套用")
    _guard(conn, request)
    try:
        return rack_excel_import.apply(conn, body.run_id, body.row_ids, _actor(session, request),
                                       backup_to=backup.get_backup_dir())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
