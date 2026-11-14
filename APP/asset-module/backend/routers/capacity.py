"""證券業分時段容量分析（ACT-04）的 API。邏輯都在 capacity_analysis.py，這裡只接線。

只讀分析（不自動調資源、不寫任何資產資料）：
  GET  /api/v2/capacity/overview  主機 × 時段 × 日型 × 指標（?from=&to=&host=）
  GET  /api/v2/capacity/demo      示範資料（記憶體內模擬，不碰真實資料庫）
  GET  /api/v2/capacity/config    時段／大量交易日／休市日／參數
  PUT  /api/v2/capacity/config    改上面這些（管理員；寫入留操作紀錄，只記做了什麼）
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["capacity_overview_endpoint", "capacity_demo_endpoint", "capacity_config_endpoint", "capacity_config_put_endpoint", "capacity_mappings_endpoint", "capacity_mapping_put_endpoint", "capacity_mapping_end_endpoint",
           "capacity_markers_endpoint", "capacity_marker_options_endpoint", "capacity_marker_audit_endpoint", "capacity_marker_create_endpoint",
           "capacity_marker_update_endpoint", "capacity_marker_revise_endpoint", "capacity_marker_void_endpoint", "capacity_marker_events_endpoint",
           "capacity_archive_list_endpoint", "capacity_archive_settings_endpoint", "capacity_archive_settings_put_endpoint", "capacity_archive_get_endpoint",
           "capacity_archive_run_endpoint", "capacity_archive_purge_preview_endpoint", "capacity_archive_purge_endpoint",
           "capacity_citations_endpoint", "capacity_citation_get_endpoint", "capacity_citation_create_endpoint", "capacity_citation_revise_endpoint",
           "capacity_citation_void_endpoint"]


class CapacityMapBody(BaseModel):
    source: str
    device: str
    asset_serial: str
    valid_from: str
    valid_to: str | None = None
    note: str = ""


class MarkerBody(BaseModel):
    """來源（事前預告／盤中標記／事後補標）由伺服器時間判定，這裡刻意沒有這個欄位。"""
    trade_date: str | None = None
    ranges: list[dict] | None = None
    mtype: str | None = None
    reason_cat: str | None = None
    expected_level: str | None = None
    basis: str | None = None
    note: str | None = None
    market_note: str | None = None
    market_source: str | None = None
    service_group: str | None = None


class MarkerVoidBody(BaseModel):
    reason: str


class MarkerEventsBody(BaseModel):
    event_ids: list[int] = []


class CapacityMapEndBody(BaseModel):
    valid_to: str


class CapacityConfigBody(BaseModel):
    slots: list[dict] | None = None
    heavy_days: list[dict] | None = None
    holidays: list[dict] | None = None
    params: dict | None = None


@router.get("/api/v2/capacity/overview")
def capacity_overview_endpoint(frm: str | None = Query(None, alias="from"), to: str | None = None, host: str | None = None,
                               limit: int | None = None, offset: int = 0,
                               session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import capacity_analysis
    try:
        return capacity_analysis.overview(conn, frm, to, host, limit, offset)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/v2/capacity/demo")
def capacity_demo_endpoint(session: sqlite3.Row = Depends(require_auth)):
    import capacity_analysis
    return capacity_analysis.demo_overview()


@router.get("/api/v2/capacity/config")
def capacity_config_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import capacity_analysis
    return capacity_analysis.get_config(conn)


@router.put("/api/v2/capacity/config")
def capacity_config_put_endpoint(body: CapacityConfigBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                 conn: sqlite3.Connection = Depends(get_db)):
    """整份驗證通過才存。資料與操作紀錄在同一個交易：紀錄寫不進去，設定一併回滾。"""
    import capacity_analysis
    from routers.overlaps import _guard
    _guard(conn, request)
    try:
        name = session["username"]
    except (KeyError, IndexError, TypeError):
        name = None
    try:
        cfg = capacity_analysis.save_config(conn, body.model_dump(exclude_none=True), commit=False)
        activity.log(conn, username=name, ip=activity.client_ip(request), method="CAPACITY",
                     path=f"容量分析設定更新：{len(cfg['slots'])} 個時段、大量交易日 {len(cfg['heavy_days'])} 天、休市日 {len(cfg['holidays'])} 天",
                     status=200, action="capacity_settings", commit=False)
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc))
    except Exception:
        conn.rollback()
        raise
    return cfg


@router.get("/api/v2/capacity/mappings")
def capacity_mappings_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """device→資產 的人工對應（帶生效起訖日），以及尚未對應的 device 與名稱／IP 候選（候選只是參考，不自動綁定）。"""
    import capacity_analysis as ca
    maps = ca.mappings(conn)
    pending = []
    for src in ca.SOURCES:
        for h in src.hosts(conn):
            last = src.latest_day(conn, h)      # 對應要在『資料日期』當天有效才算已對應；已過期的對應不算
            if not ca.mappings(conn, src.name, h, last):
                pending.append({"source": src.name, "device": h, "latest_data_day": last, "candidates": ca.candidates(conn, h)})
    return {"mappings": maps, "unmapped": pending}


@router.put("/api/v2/capacity/mappings")
def capacity_mapping_put_endpoint(body: CapacityMapBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                  conn: sqlite3.Connection = Depends(get_db)):
    """新增一筆 device→資產 對應（管理員）。期間不可與既有重疊；資料與操作紀錄同一個交易。"""
    import capacity_analysis as ca
    from routers.overlaps import _guard
    _guard(conn, request)
    try:
        name = session["username"]
    except (KeyError, IndexError, TypeError):
        name = None
    try:
        ca.save_mapping(conn, body.source, body.device, body.asset_serial, body.valid_from, body.valid_to, body.note, name, commit=False)
        activity.log(conn, username=name, ip=activity.client_ip(request), method="CAPACITY", path="容量分析：新增一筆裝置與資產的對應",
                     status=200, action="capacity_mapping", commit=False)
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc))
    except Exception:
        conn.rollback()
        raise
    return {"ok": True}


@router.put("/api/v2/capacity/mappings/{map_id}/end")
def capacity_mapping_end_endpoint(map_id: int, body: CapacityMapEndBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                  conn: sqlite3.Connection = Depends(get_db)):
    """結束（或更正結束日）一筆既有對應，讓開放式對應可以換任（管理員；資料與操作紀錄同一個交易）。"""
    import capacity_analysis as ca
    from routers.overlaps import _guard
    _guard(conn, request)
    try:
        name = session["username"]
    except (KeyError, IndexError, TypeError):
        name = None
    try:
        r = ca.end_mapping(conn, map_id, body.valid_to, name, commit=False)
        activity.log(conn, username=name, ip=activity.client_ip(request), method="CAPACITY",
                     path=f"容量分析：結束對應 #{map_id}，結束日 {r['valid_to']}（原 {r['was'] or '迄今'}）", status=200, action="capacity_mapping_end", commit=False)
        conn.commit()
    except LookupError as exc:
        conn.rollback()
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc))
    except Exception:
        conn.rollback()
        raise
    return {"ok": True, "id": map_id, "valid_to": r["valid_to"]}




# ---------------------------------------------------------------- 交易日情境標記（與取數解耦：這裡完全不碰 WhatsUp）
def _name(session):
    try:
        return session["username"]
    except (KeyError, IndexError, TypeError):
        return None


def _marker_write(conn, request, session, action: str, text: str, fn):
    """資料變更與操作紀錄同一個交易：任何一步失敗整筆回滾。"""
    import auth
    import capacity_markers as mk
    from routers.overlaps import _guard
    # 只有管理員群組可寫；登入停用時（假 session 是 admin）比照 fail-closed：不得寫入——標記是日後回測的證據，沒有真實身分就留不了稽核
    if auth_disabled(conn):
        raise HTTPException(403, "登入停用時不允許新增或修改交易日情境標記（需要真實身分才能留下稽核）")
    if auth.session_level(session) != "admin":
        raise HTTPException(403, "只有管理員群組可以新增或修改交易日情境標記")
    _guard(conn, request)
    try:
        out = fn(mk, _name(session))
        activity.log(conn, username=_name(session), ip=activity.client_ip(request), method="CAPACITY", path=text, status=200, action=action, commit=False)
        conn.commit()
        return out
    except LookupError as exc:
        conn.rollback()
        raise HTTPException(404, str(exc))
    except (PermissionError, mk.MarkerConflict) as exc:
        conn.rollback()
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc))
    except Exception:
        conn.rollback()
        raise


@router.get("/api/v2/capacity/markers/options")
def capacity_marker_options_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import capacity_markers as mk
    return mk.options(conn)


@router.get("/api/v2/capacity/markers")
def capacity_markers_endpoint(frm: str | None = Query(None, alias="from"), to: str | None = None, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import capacity_markers as mk
    return {"markers": mk.listing(conn, frm, to), "options": mk.options(conn)}


@router.get("/api/v2/capacity/markers/{marker_id}/audit")
def capacity_marker_audit_endpoint(marker_id: int, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import capacity_markers as mk
    if not mk.get(conn, marker_id):
        raise HTTPException(404, "找不到這筆標記")
    return {"audit": mk.audit_trail(conn, marker_id)}


@router.post("/api/v2/capacity/markers")
def capacity_marker_create_endpoint(body: MarkerBody, request: Request, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    return _marker_write(conn, request, session, "capacity_marker", "容量分析：新增交易日情境標記",
                         lambda mk, who: mk.create(conn, body.model_dump(exclude_none=True), who))


@router.put("/api/v2/capacity/markers/{marker_id}")
def capacity_marker_update_endpoint(marker_id: int, body: MarkerBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                    conn: sqlite3.Connection = Depends(get_db)):
    """開盤（交易日 09:00）前才能直接改預告欄位；之後回 409，請改用『新增修訂版』。"""
    return _marker_write(conn, request, session, "capacity_marker_update", f"容量分析：修改交易日情境標記 #{marker_id}",
                         lambda mk, who: mk.update_direct(conn, marker_id, body.model_dump(exclude_none=True), who))


@router.post("/api/v2/capacity/markers/{marker_id}/revise")
def capacity_marker_revise_endpoint(marker_id: int, body: MarkerBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                    conn: sqlite3.Connection = Depends(get_db)):
    return _marker_write(conn, request, session, "capacity_marker_revise", f"容量分析：新增交易日情境標記 #{marker_id} 的修訂版",
                         lambda mk, who: mk.revise(conn, marker_id, body.model_dump(exclude_none=True), who))


@router.post("/api/v2/capacity/markers/{marker_id}/void")
def capacity_marker_void_endpoint(marker_id: int, body: MarkerVoidBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                  conn: sqlite3.Connection = Depends(get_db)):
    """作廢（管理員）：不刪除，保留原文，要寫原因。"""
    return _marker_write(conn, request, session, "capacity_marker_void", f"容量分析：作廢交易日情境標記 #{marker_id}",
                         lambda mk, who: mk.void(conn, marker_id, body.reason, who))


@router.put("/api/v2/capacity/markers/{marker_id}/events")
def capacity_marker_events_endpoint(marker_id: int, body: MarkerEventsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                    conn: sqlite3.Connection = Depends(get_db)):
    return _marker_write(conn, request, session, "capacity_marker_events", f"容量分析：交易日情境標記 #{marker_id} 連結事件應變",
                         lambda mk, who: mk.set_events(conn, marker_id, body.event_ids, who))


# ---------------------------------------------------------------- 日摘要封存與保存（2 年）。讀：登入即可；寫／清理：只有 admin，登入停用 fail-closed
class ArchiveRunBody(BaseModel):
    day: str
    host: str | None = None
    reason: str = ""
    confirm_in_window: bool = False


class ArchiveRetentionBody(BaseModel):
    retention_months: int


class ArchivePurgeBody(BaseModel):
    expected_rows: int
    cutoff: str
    confirm: bool = False


def _archive_admin(conn, session) -> str | None:
    """封存的寫入／清理：只有管理員群組；登入停用時假 session 是 admin，但這裡 fail-closed（沒有真實身分就留不了稽核）。"""
    import auth
    if auth_disabled(conn):
        raise HTTPException(403, "登入停用時不允許封存、設定保存期限或清理（需要真實身分才能留下稽核）")
    if auth.session_level(session) != "admin":
        raise HTTPException(403, "只有管理員群組可以操作日摘要封存與清理")
    return _name(session)


def _archive_write(conn, request, session, action: str, text: str, fn):
    """資料變更與操作紀錄同一個交易：任何一步失敗整筆回滾。"""
    import capacity_archive as ar
    from routers.overlaps import _guard
    who = _archive_admin(conn, session)
    _guard(conn, request)
    try:
        ar.ensure(conn)
        out = fn(ar, who)
        activity.log(conn, username=who, ip=activity.client_ip(request), method="CAPACITY", path=text, status=200, action=action, commit=False)
        conn.commit()
        return out
    except ar.ArchiveConflict as exc:
        conn.rollback()
        raise HTTPException(409, str(exc))
    except LookupError as exc:
        conn.rollback()
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc))
    except Exception:
        conn.rollback()
        raise


@router.get("/api/v2/capacity/archive")
def capacity_archive_list_endpoint(frm: str | None = Query(None, alias="from"), to: str | None = None, host: str | None = None, stage: str | None = None,
                                   latest_only: bool = False, limit: int = 500, offset: int = 0,
                                   session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import capacity_archive as ar
    try:
        return {"settings": ar.settings_view(conn), **ar.listing(conn, frm, to, host, stage, latest_only, limit, offset)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/v2/capacity/archive/settings")
def capacity_archive_settings_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import capacity_archive as ar
    return ar.settings_view(conn)


@router.put("/api/v2/capacity/archive/settings")
def capacity_archive_settings_put_endpoint(body: ArchiveRetentionBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                           conn: sqlite3.Connection = Depends(get_db)):
    return _archive_write(conn, request, session, "capacity_archive_retention", f"容量分析封存：保存期限改為 {body.retention_months} 個月",
                          lambda ar, who: ar.set_retention(conn, body.retention_months, who))


@router.get("/api/v2/capacity/archive/{archive_id}")
def capacity_archive_get_endpoint(archive_id: int, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import capacity_archive as ar
    d = ar.get_one(conn, archive_id)
    if not d:
        raise HTTPException(404, "找不到這筆封存")
    return d


@router.post("/api/v2/capacity/archive/run")
def capacity_archive_run_endpoint(body: ArchiveRunBody, request: Request, confirm_in_window: bool = False,
                                  session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """人工補封存／修訂（admin）。純本機計算，但窗內（平日 08:30–15:00）仍需二次確認，比照全站靜默窗。
    確認值 JSON 欄位或 ?confirm_in_window=true 都認：全站 useApi 收到 409 跳確認後是帶在 query 重送（Codex 975：
    以前只讀 JSON，畫面按「確定」仍一直 409）。"""
    import quiet_window
    who = _archive_admin(conn, session)
    quiet_window.confirm_from_request(conn, "capacity_archive_tick", body.confirm_in_window or confirm_in_window, who, request)
    return _archive_write(conn, request, session, "capacity_archive_run", f"容量分析封存：人工封存 {body.day}" + (f"（{body.host}）" if body.host else "（全部主機）"),
                          lambda ar, w: ar.run_manual(conn, body.day, body.host, w, body.reason))


@router.post("/api/v2/capacity/archive/purge/preview")
def capacity_archive_purge_preview_endpoint(request: Request, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """清理預覽（admin）：只算『會刪幾筆、哪些被標記保護』，不刪任何東西；預覽本身留稽核，清理必須對得上這次預覽。"""
    return _archive_write(conn, request, session, "capacity_archive_purge_preview", "容量分析封存：預覽過期日摘要清理",
                          lambda ar, who: ar.purge_preview(conn, who))


@router.post("/api/v2/capacity/archive/purge")
def capacity_archive_purge_endpoint(body: ArchivePurgeBody, request: Request, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """清理過期日摘要（admin，手動）：要帶預覽的筆數與截止日；排程永遠不會呼叫這個。"""
    return _archive_write(conn, request, session, "capacity_archive_purge", f"容量分析封存：清理過期日摘要（截止 {body.cutoff}，預期 {body.expected_rows} 筆）",
                          lambda ar, who: ar.purge(conn, who, body.expected_rows, body.cutoff, body.confirm))


# ---- 封存引用（2.042.06）：報告／簽呈引用哪幾筆日摘要。只增不改：更正＝新增一版、作廢＝另記作廢；寫入只有管理員，稽核同交易
class CitationBody(BaseModel):
    title: str | None = None
    purpose: str | None = None
    note: str | None = None
    refs: list[str] | None = None


class CitationVoidBody(BaseModel):
    reason: str


@router.get("/api/v2/capacity/archive-citations")
def capacity_citations_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import capacity_archive as ar
    try:
        return ar.citations(conn)
    except ar.ArchiveConflict as exc:
        raise HTTPException(409, str(exc))


@router.get("/api/v2/capacity/archive-citations/{cid}")
def capacity_citation_get_endpoint(cid: int, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import capacity_archive as ar
    try:
        return ar.citation_get(conn, cid)
    except ar.ArchiveConflict as exc:
        raise HTTPException(409, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/api/v2/capacity/archive-citations")
def capacity_citation_create_endpoint(body: CitationBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                      conn: sqlite3.Connection = Depends(get_db)):
    return _archive_write(conn, request, session, "capacity_citation_create", f"容量分析封存：新增引用「{(body.title or '')[:40]}」（{len(body.refs or [])} 筆）",
                          lambda ar, who: ar.citation_create(conn, body.model_dump(), who))


@router.post("/api/v2/capacity/archive-citations/{cid}/revise")
def capacity_citation_revise_endpoint(cid: int, body: CitationBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                      conn: sqlite3.Connection = Depends(get_db)):
    return _archive_write(conn, request, session, "capacity_citation_revise", f"容量分析封存：更正引用 #{cid}（新增一版）",
                          lambda ar, who: ar.citation_revise(conn, cid, body.model_dump(), who))


@router.post("/api/v2/capacity/archive-citations/{cid}/void")
def capacity_citation_void_endpoint(cid: int, body: CitationVoidBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                    conn: sqlite3.Connection = Depends(get_db)):
    return _archive_write(conn, request, session, "capacity_citation_void", f"容量分析封存：作廢引用 #{cid}",
                          lambda ar, who: ar.citation_void(conn, cid, body.reason, who))
