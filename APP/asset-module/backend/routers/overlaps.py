"""機櫃「位置重疊待處理」API（2026-10-24）。邏輯在 overlap_queue.py。
讀：登入即可。寫（處理／復原）：管理員（_can_edit_nav，跟機櫃狀態、位置更正同一把尺）。"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["rack_overlaps_endpoint", "rack_overlap_decide_endpoint", "rack_overlap_undo_endpoint",
           "rack_overlap_history_endpoint", "rack_move_endpoint", "rack_merge_confirm_endpoint", "rack_diff_list_endpoint", "rack_diff_candidates_endpoint", "rack_diff_get_endpoint", "rack_diff_report_endpoint", "rack_diff_act_endpoint",
           "rack_diff_batch_preview_endpoint", "rack_diff_batch_apply_endpoint", "write_guard_get_endpoint", "write_guard_put_endpoint",
           "rack_field_edit_endpoint", "rack_field_edit_clear_endpoint", "rack_field_edits_endpoint",
           "rack_field_edits_xlsx_endpoint",
           "rack_move_check_endpoint", "rack_moves_endpoint", "rack_move_undo_endpoint",
           "rack_excel_preview_endpoint", "rack_excel_runs_endpoint", "rack_excel_run_endpoint", "rack_excel_apply_endpoint",
           "ip_relations_endpoint", "ip_relation_review_endpoint", "ip_relation_undo_endpoint",
           "ip_relation_batch_preview_endpoint", "ip_relation_batch_apply_endpoint", "ip_relation_batch_undo_endpoint",
           "ip_relation_batches_endpoint"]


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


class RackNameMapBody(BaseModel):
    items: dict[str, str]           # {「機房/系統櫃名」: Excel 櫃名}；值留空＝不換


@router.get("/api/racks/name-map")
def rack_name_map_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """同一實體櫃合一的對照（2.043.13）：stored＝管理員設定／套用 Excel 寫入的，derived＝由 CIA 機櫃欄括號代碼推得的，effective＝實際套用。"""
    import rack_layout
    stored = rack_layout._setting_json(conn, rack_layout.RACK_NAME_MAP_SETTING)
    return {"stored": stored if isinstance(stored, dict) else {}, "derived": rack_layout.derived_rack_names(conn),
            "effective": rack_layout.rack_name_map(conn), "can_edit": _can_edit_nav(session, conn),
            "merge_changes": rack_layout.merge_count_changes(conn)}


class RackMergeConfirmBody(BaseModel):
    room: str
    rack: str
    keys: list[str]


@router.put("/api/racks/merge-confirm")
def rack_merge_confirm_endpoint(body: RackMergeConfirmBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    """管理員確認「合櫃後這幾台算一台是對的」（2.043.15，GPT 複核 D）。沒確認的台數變動，部署時 verify_numbers 會擋。"""
    import rack_layout
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以確認合櫃台數")
    _guard(conn, request)
    try:
        return rack_layout.confirm_merge(conn, body.room, body.rack, body.keys, _actor(session, request))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/api/racks/name-map")
def rack_name_map_save_endpoint(body: RackNameMapBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    """管理員改對照（整份取代 stored）。只是改名，不動 CIA 資料、不增減任何一台。"""
    import rack_layout
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以改機櫃名稱對照")
    _guard(conn, request)
    try:
        out = rack_layout.save_rack_name_map(conn, body.items, _actor(session, request), merge=False)
        conn.commit()
        return {"stored": out, "effective": rack_layout.rack_name_map(conn)}
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


# ===== 共用 IP／VIP 待核清單（ip_relation.py）：讀＝登入即可、純讀；寫（判定／復原）＝管理員 =====
class IpReviewBody(BaseModel):
    item_key: str
    verdict: str
    relation_type: str | None = None
    note: str | None = None


class IpUndoBody(BaseModel):
    note: str | None = None


@router.get("/api/ip-relations")
def ip_relations_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import ip_relation
    return {**ip_relation.listing(conn), "can_edit": _can_edit_nav(session, conn)}


@router.post("/api/ip-relations/review")
def ip_relation_review_endpoint(body: IpReviewBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    import ip_relation
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以判定")
    _guard(conn, request)
    try:
        return ip_relation.review(conn, body.item_key, body.verdict, body.relation_type, body.note, _actor(session, request))
    except LookupError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/ip-relations/{review_id}/undo")
def ip_relation_undo_endpoint(review_id: int, body: IpUndoBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import ip_relation
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以復原")
    _guard(conn, request)
    try:
        return ip_relation.undo(conn, review_id, _actor(session, request), body.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class IpBatchBody(BaseModel):
    item_keys: list[str]
    verdict: str
    relation_type: str | None = None
    note: str | None = None
    overwrite: bool = False
    digest: str | None = None
    filter_text: str | None = None


class IpBatchUndoBody(BaseModel):
    note: str | None = None
    skip_conflicts: bool = False


@router.get("/api/ip-relations/batches")
def ip_relation_batches_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import ip_relation
    return {"batches": ip_relation.list_batches(conn), "limit": ip_relation.BATCH_MAX}


@router.post("/api/ip-relations/batch/preview")
def ip_relation_batch_preview_endpoint(body: IpBatchBody, session: sqlite3.Row = Depends(require_auth),
                                       conn: sqlite3.Connection = Depends(get_db)):
    import ip_relation
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以批次判定")
    try:
        return ip_relation.batch_preview(conn, body.item_keys, body.verdict, body.relation_type, body.note, body.overwrite)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/ip-relations/batch/apply")
def ip_relation_batch_apply_endpoint(body: IpBatchBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                     conn: sqlite3.Connection = Depends(get_db)):
    import ip_relation
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以批次判定")
    _guard(conn, request)
    try:
        return ip_relation.batch_apply(conn, body.item_keys, body.verdict, body.relation_type, body.note, body.overwrite,
                                       body.digest or "", body.filter_text, _actor(session, request))
    except ip_relation.StalePlan as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/ip-relations/batch/{batch_id}/undo")
def ip_relation_batch_undo_endpoint(batch_id: str, body: IpBatchUndoBody, request: Request,
                                    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import ip_relation
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以復原")
    _guard(conn, request)
    try:
        return ip_relation.batch_undo(conn, batch_id, _actor(session, request), body.note, body.skip_conflicts)
    except ip_relation.BatchConflict as exc:
        raise HTTPException(409, {"message": str(exc), "conflicts": exc.conflicts})
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ===== 機櫃差異待辦（2.043.17，R4–R8；邏輯在 rack_diff.py）=====
# 讀：登入即可。回報：一般帳號以上（viewer 403；登入停用＝單人測試，放行）。結案／批次：管理員（_can_edit_nav）。
class RackDiffReportBody(BaseModel):
    subject: str
    reason: str
    room: str
    rack: str
    sys_key: str | None = None
    xl_run_id: int | None = None
    xl_row_id: int | None = None
    note: str | None = None
    serial: str | None = None
    target_room: str | None = None
    target_rack: str | None = None
    target_u_start: int | None = None
    target_u_end: int | None = None
    bind_q: str | None = None
    bind_key: str | None = None      # 從候選清單選定的那一台（2.044.6，GPT B4）


class RackDiffActBody(BaseModel):
    action: str
    note: str | None = None
    target_room: str | None = None
    target_rack: str | None = None
    target_u_start: int | None = None
    target_u_end: int | None = None
    bind_q: str | None = None
    approver: str | None = None
    basis: str | None = None


class RackDiffBatchBody(BaseModel):
    ids: list[int]
    action: str
    note: str | None = None


class RackDiffBatchApplyBody(BaseModel):
    token: str                       # 預覽給的；送出只認這個（GPT A2：不信任客戶端給的 ids）
    note: str | None = None          # 送出當下的備註，要跟預覽時一樣（GPT 複核 P1）


def _can_report(session, conn) -> bool:
    import auth
    if auth_disabled(conn):
        return True
    return session is not None and auth.session_level(session) != "viewer"


@router.get("/api/racks/diff")
def rack_diff_list_endpoint(status: str | None = None, room: str | None = None, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    import rack_diff
    return {"items": rack_diff.list_items(conn, status or None, room or None), "counts": rack_diff.counts(conn, room or None),
            "status": rack_diff.STATUS, "actions": rack_diff.ACTIONS, "reasons_sys": rack_diff.REASONS_SYS,
            "reasons_xl": rack_diff.REASONS_XL, "batchable": sorted(rack_diff.BATCHABLE),
            "can_edit": _can_edit_nav(session, conn), "can_report": _can_report(session, conn)}


@router.get("/api/racks/diff/candidates")
def rack_diff_candidates_endpoint(room: str, q: str = "", rack: str | None = None, session: sqlite3.Row = Depends(require_auth),
                                  conn: sqlite3.Connection = Depends(get_db)):
    """回報「是哪一台」打字時的候選清單（同機房；同一櫃排前面）。唯讀。"""
    import rack_diff
    return {"items": rack_diff.candidates(conn, room, rack, q)}


@router.get("/api/racks/diff/{item_id}")
def rack_diff_get_endpoint(item_id: int, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import rack_diff
    try:
        return {**rack_diff.get(conn, item_id), "can_edit": _can_edit_nav(session, conn)}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/racks/diff")
def rack_diff_report_endpoint(body: RackDiffReportBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    """現場回報一格不符（不改正式資料，只開一筆待辦）。"""
    import rack_diff
    if not _can_report(session, conn):
        raise HTTPException(403, "只能看的帳號不能回報")
    _guard(conn, request)
    try:
        return rack_diff.report(conn, body.model_dump(), _actor(session, request))
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/racks/diff/batch-preview")
def rack_diff_batch_preview_endpoint(body: RackDiffBatchBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                     conn: sqlite3.Connection = Depends(get_db)):
    """批次預覽（不寫庫）：哪幾筆可以做、哪幾筆不行、為什麼。跟其他機櫃寫入的前一步一樣先過寫入守門。"""
    import rack_diff
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以處理差異待辦")
    _guard(conn, request)
    try:
        return rack_diff.batch_preview(conn, body.ids, body.action, _actor(session, request), body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/racks/diff/batch-apply")
def rack_diff_batch_apply_endpoint(body: RackDiffBatchApplyBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                   conn: sqlite3.Connection = Depends(get_db)):
    import rack_diff
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以處理差異待辦")
    _guard(conn, request)
    try:
        return rack_diff.batch_apply(conn, body.token, _actor(session, request), body.note)
    except rack_diff.BatchStale as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/racks/diff/{item_id}/act")
def rack_diff_act_endpoint(item_id: int, body: RackDiffActBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    """SP（管理員）處理一筆待辦：結案動作才寫正式資料（機櫃狀態／位置更正／CIA 待異動），可還原。"""
    import rack_diff
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以處理差異待辦")
    _guard(conn, request)
    extra = body.model_dump(exclude={"action", "note"})
    try:
        return rack_diff.act(conn, item_id, body.action, _actor(session, request), body.note, extra)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
