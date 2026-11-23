"""CIA 第三批共用案件（2.061.x）：同機多筆（S3）、實體機補登（S1）、機櫃編號（S2）、未結案清單（S4）的 API。
邏輯在 cia_case.py（共用案件表）與各自的模組，這裡只接線。

權限（access_policy）：讀＝登入即可；確認、撤回、標例外、重新比對、交出＝管理員（ADMIN_WRITE，GPT 第 1 點）。
寫入都過 write_guard（登入停用時可要求管理密碼）並記「誰」。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()


class CiaCaseActBody(BaseModel):
    kind: str
    ids: list[int]
    action: str
    note: str | None = None
    decision: dict | None = None
    until: str | None = None


def _w(conn, request, session) -> str:
    from routers.overlaps import _actor, _guard

    _guard(conn, request)
    return _actor(session, request)


def _kind(kind: str) -> str:
    import cia_case

    if kind not in cia_case.KINDS:
        raise HTTPException(400, f"不認得的類別：{kind}")
    return kind


def _settle(conn) -> None:
    """讀取前先結算（例外到期回待確認、修正包已結案的案件結案；審查 P2-3、P2-4）。沒有要結算的不拿寫入鎖。"""
    import cia_case

    try:
        cia_case.settle(conn, "系統")
    except sqlite3.OperationalError as exc:      # 資料庫忙：這次照現況顯示，下次讀取再結算
        if conn.in_transaction:
            conn.rollback()
        print(f"[cia-case] 結算略過（資料庫忙）：{exc}")


@router.get("/api/cia-case/summary")
def cia_case_summary_endpoint(kind: str, room: str | None = None, category: str | None = None,
                              session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """卡片數字＋上次比對（room、category 跟下鑽同一組條件）。數字＝/api/cia-case/items 同一份（verify_numbers 釘住）。"""
    import cia_case

    _kind(kind)
    _settle(conn)
    return {"counts": cia_case.counts(conn, kind, room or None, category or None), "last_scan": cia_case.last_scan(conn, kind),
            "status_labels": cia_case.STATUS, "kind_label": cia_case.KINDS[kind], "categories": cia_case.categories(conn, kind)}


@router.get("/api/cia-case/items")
def cia_case_items_endpoint(kind: str, status: str | None = None, room: str | None = None, category: str | None = None,
                            session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """下鑽：status＝狀態代號／open_all（未結案）；room＝機房（機櫃編號用）；category＝分類（同機多筆）。"""
    import cia_case

    _kind(kind)
    _settle(conn)
    try:
        items = cia_case.list_cases(conn, kind, status or None, room or None, category=category or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"total": len(items), "items": items}


@router.get("/api/cia-case/items/{case_id}/events")
def cia_case_events_endpoint(case_id: int, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import cia_case

    return {"items": cia_case.case_events(conn, case_id)}


@router.post("/api/cia-case/act")
def cia_case_act_endpoint(body: CiaCaseActBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                          conn: sqlite3.Connection = Depends(get_db)):
    """確認（confirm）／撤回（withdraw，反向事件）／標例外（except，要原因與到期日）／取消例外（unexcept）。"""
    import cia_case

    by = _w(conn, request, session)
    _kind(body.kind)
    if body.kind == "dup" and body.action == "confirm":
        raise HTTPException(400, "同機多筆要逐案確認（/api/cia-case/dup/confirm，每個不同欄位選要取哪一筆）")
    try:
        # 確認內容由系統依類別存（default_decision，審查 P1／P3），不收前端傳來的 decision
        return cia_case.act(conn, body.ids, body.action, by, body.note, None, body.until, kind=body.kind)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------- S3 同機多筆

class CiaDupConfirmBody(BaseModel):
    id: int
    keep: str
    fields: dict[str, str] | None = None
    note: str | None = None
    seen_last: str | None = None          # 畫面載入時這一案的 last_seen（審查 P3：比對過就擋）


@router.post("/api/cia-case/dup/scan")
def cia_dup_scan_endpoint(request: Request, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """重新比對：找出同一台有 2 筆以上 CIA 登記的，寫進案件表（新案／再次出現／已不存在）。"""
    import cia_dup

    by = _w(conn, request, session)
    return cia_dup.scan(conn, by)


@router.post("/api/cia-case/dup/confirm")
def cia_dup_confirm_endpoint(body: CiaDupConfirmBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    """逐案確認：保留哪一筆＋每個不同欄位取哪一筆的值（不批次自動）。"""
    import cia_dup

    by = _w(conn, request, session)
    try:
        return cia_dup.confirm(conn, body.id, body.keep, body.fields, body.note, by, body.seen_last)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/cia-case/dup/handoff")
def cia_dup_handoff_endpoint(request: Request, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """已確認的同機多筆交出成一個 9.3 修正包批次（WEBIT3 不刪任何一筆，CIA 由管理人改）。"""
    import cia_dup

    by = _w(conn, request, session)
    try:
        return cia_dup.handoff(conn, by)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------- S1 實體機補登

@router.get("/api/cia-case/physical/overview")
def cia_physical_overview_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """三態各幾台＋排除＋APID 回測。數字＝/api/cia-case/physical/list 同一份計算（verify_numbers 釘住）。"""
    import cia_physical

    return cia_physical.overview(conn)


@router.get("/api/cia-case/physical/list")
def cia_physical_list_endpoint(state: str, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """下鑽：state＝physical（實體）／unknown（無法判定）／virtual（非實體）。"""
    import cia_physical

    p = cia_physical.plan(conn)          # 一個請求只算一次（審查 P3-9）：清單與上方數字同一份
    try:
        items = cia_physical.listing(conn, state, p)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"total": len(items), "items": items, "overview": cia_physical.overview(conn, p)}


@router.post("/api/cia-case/physical/scan")
def cia_physical_scan_endpoint(request: Request, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """重新比對：實體的一台一案寫進案件表；無法判定的不進。"""
    import cia_physical

    by = _w(conn, request, session)
    return cia_physical.scan(conn, by)


# ---------------------------------------------------------------- S4 未結案清單

class CiaOpenSettingsBody(BaseModel):
    overdue_days: int


@router.get("/api/cia-open/summary")
def cia_open_summary_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """新案／逾期／例外到期／全部未結案的數字（＝/api/cia-open/items 同一份計算）＋各來源未結案數。"""
    import cia_open

    _settle(conn)
    return cia_open.summary(conn)


@router.get("/api/cia-open/items")
def cia_open_items_endpoint(view: str = "all", session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """下鑽：view＝all（全部未結案）／new（新案）／overdue（逾期）／exc_due（例外到期）。含事件紀錄（稽核用）。"""
    import cia_open

    _settle(conn)
    rs, params = cia_open.rows(conn)
    try:
        items = cia_open.pick(rs, view)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    # 卡片數字跟清單同一次計算一起回（畫面不用再打一次 summary 重算，審查 P3-9）
    return {"total": len(items), "items": items, "summary": cia_open.summary(conn, computed=(rs, params)), **params}


@router.get("/api/cia-open/coverage")
def cia_open_coverage_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """掃描涵蓋範圍：分母＝2.1 全部登記台數；每個來源最後資料日期、涵蓋／未涵蓋／無法比對。"""
    import cia_open

    return cia_open.coverage(conn)


@router.put("/api/cia-open/settings")
def cia_open_settings_endpoint(body: CiaOpenSettingsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    """逾期天數（交出超過幾天未回算逾期）。"""
    import cia_open

    _w(conn, request, session)
    try:
        return {"overdue_days": cia_open.save_overdue_days(conn, body.overdue_days)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------- S2 機櫃編號統一

class CiaRackRosterBody(BaseModel):
    room: str
    extra: list[str] | None = None
    effective_date: str | None = None


class CiaRackApproveBody(BaseModel):
    room: str
    version: int


class CiaRackAliasBody(BaseModel):
    room: str
    raw: str
    start_rack: str | None = None
    end_rack: str | None = None
    note: str | None = None


class CiaRackHandoffBody(BaseModel):
    room: str | None = None


@router.get("/api/cia-rack/rooms")
def cia_rack_rooms_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """機房清單：機櫃圖 Excel 最近一次、生效中的清冊版本與核准、草稿。"""
    import cia_rack

    return {"items": cia_rack.rooms(conn)}


@router.get("/api/cia-rack/roster")
def cia_rack_roster_endpoint(room: str, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import cia_rack

    return {"roster": cia_rack.roster(conn, room), "versions": cia_rack.roster_versions(conn, room)}


@router.get("/api/cia-rack/mapping")
def cia_rack_mapping_endpoint(room: str, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """這個機房 CIA 的每一種機櫃寫法：筆數、對到哪（自動／人選／對不到）、標準寫法。數字＝列數（verify_numbers 釘住）。"""
    import cia_rack

    return cia_rack.mapping(conn, room)


@router.post("/api/cia-rack/roster/load")
def cia_rack_load_endpoint(body: CiaRackRosterBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    """從機櫃圖 Excel（＋人工補的櫃）建新版本清冊草稿；要核准才生效。"""
    import cia_rack

    by = _w(conn, request, session)
    try:
        return cia_rack.load_roster(conn, body.room, by, body.extra, body.effective_date)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/cia-rack/roster/approve")
def cia_rack_approve_endpoint(body: CiaRackApproveBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import cia_rack

    by = _w(conn, request, session)
    try:
        return cia_rack.approve_roster(conn, body.room, body.version, by)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/cia-rack/alias")
def cia_rack_alias_endpoint(body: CiaRackAliasBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    """人選對照：CIA 寫法 → 清冊的櫃（起訖）；空＝標成不處理（要原因）。只增不改。"""
    import cia_rack

    by = _w(conn, request, session)
    try:
        return cia_rack.add_alias(conn, body.room, body.raw, body.start_rack, body.end_rack, body.note, by)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/cia-case/rack/scan")
def cia_rack_scan_endpoint(request: Request, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import cia_rack

    by = _w(conn, request, session)
    return cia_rack.scan(conn, by)


@router.post("/api/cia-case/rack/handoff")
def cia_rack_handoff_endpoint(body: CiaRackHandoffBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    """這個機房已確認的交出成一個 9.3 修正包批次（每批限一個機房）。"""
    import cia_rack

    by = _w(conn, request, session)
    try:
        return cia_rack.handoff(conn, body.room or "", by)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
