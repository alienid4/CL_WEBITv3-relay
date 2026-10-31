"""2.x 新版畫面（A1 首頁、A2 病歷表）的 API（2026-10-27）。只讀；邏輯都在 v2_views.py（組裝既有計算）。"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["v2_search_endpoint", "v2_machine_endpoint", "v2_connections_endpoint", "v2_registrations_endpoint",
           "v2_history_endpoint", "v2_home_endpoint", "v2_home_week_endpoint", "v2_summary_endpoint", "v2_section_endpoint",
           "v2_sections_endpoint", "v2_export_endpoint", "v2_hosts_endpoint", "v2_incident_notify_endpoint",
           "v2_incident_contacts_csv_endpoint", "v2_inv_tasks_endpoint", "v2_inv_create_endpoint", "v2_inv_result_endpoint",
           "v2_inv_diffs_endpoint", "v2_inv_status_endpoint", "v2_inv_report_endpoint", "v2_inv_report_xlsx_endpoint",
           "v2_onboard_new_devices", "v2_onboard_pending", "v2_onboard_progress", "v2_onboard_progress_drill",
           "v2_systems_endpoint", "v2_dist_endpoint", "v2_dist_drill_endpoint",
           "v2_host_monthly_endpoint", "v2_host_monthly_note_endpoint", "v2_host_monthly_nmon_endpoint"]


def _call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/v2/search")
def v2_search_endpoint(q: str = "", session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """首頁搜尋框：打字就出（機器／系統分組＋最可能那一筆）。比對規則＝值班卡同一套。"""
    import v2_views
    return v2_views.search(conn, q)


@router.get("/api/v2/machine")
def v2_machine_endpoint(q: str, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """病歷表：q＝代號／資產序號／IP／主機名。"""
    import v2_views
    return _call(v2_views.machine, conn, q)


@router.get("/api/v2/machine/connections")
def v2_connections_endpoint(key: str, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import v2_views
    return _call(v2_views.connections, conn, key)


@router.get("/api/v2/machine/registrations")
def v2_registrations_endpoint(key: str, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import v2_views
    return _call(v2_views.registrations, conn, key)


@router.get("/api/v2/machine/history")
def v2_history_endpoint(key: str, days: int = 90, session: sqlite3.Row = Depends(require_auth),
                        conn: sqlite3.Connection = Depends(get_db)):
    import v2_views
    return _call(v2_views.history, conn, key, max(1, min(days, 365)))


@router.get("/api/v2/home")
def v2_home_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """首頁：今天要處理＋實體設備／主機兩個數字。"""
    import v2_views
    return v2_views.home(conn)


@router.get("/api/v2/home/week")
def v2_home_week_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """1.1「本週異動」：最近 7 天每天的每日異動筆數（1.2 同一份計算）。唯讀。"""
    import v2_views
    return v2_views.week_changes(conn)


@router.get("/api/v2/machine/summary")
def v2_summary_endpoint(key: str, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """病歷表下方五組頁簽＋重建用資料：每一段的筆數或「不知道（原因）」。"""
    import v2_sections
    return _call(v2_sections.summary, conn, key)


@router.get("/api/v2/machine/section")
def v2_section_endpoint(key: str, name: str, session: sqlite3.Row = Depends(require_auth),
                        conn: sqlite3.Connection = Depends(get_db)):
    """一段的資料（點了才載）。資料來源＝舊資產頁同一支計算。"""
    import v2_sections
    return _call(v2_sections.section, conn, key, name)


@router.get("/api/v2/machine/sections")
def v2_sections_endpoint(key: str, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """完整病歷（全部展開）：每一段一次給齊。"""
    import v2_sections
    return {"sections": _call(v2_sections.all_sections, conn, key)}


@router.get("/api/v2/machine/export.xlsx")
def v2_export_endpoint(key: str, only: str | None = None, session: sqlite3.Row = Depends(require_auth),
                       conn: sqlite3.Connection = Depends(get_db)):
    """匯出完整病歷（一段一張工作表）；only=rebuild 只匯重建用資料。"""
    import v2_sections
    from urllib.parse import quote
    buf, fname = _call(v2_sections.export_xlsx, conn, key, only or None)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


@router.get("/api/v2/hosts")
def v2_hosts_endpoint(tab: str = "all", per_record: bool = False, q: str | None = None, page: int = 1, size: int = 100,
                      sort: str = "no", desc: bool = False,
                      os_source: str | None = None, os_class: str | None = None,
                      session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """2.1 主機查詢：頁籤筆數＋目前頁籤這一頁的列（一台一列；per_record＝顯示每筆登記）。
    伺服器端分頁排序（size=0＝全部，匯出用）。"""
    import v2_hosts
    return _call(v2_hosts.listing, conn, tab, per_record, q, page, max(0, min(size, 100000)), sort, desc,
                 os_source, os_class)


@router.get("/api/v2/incident/{eid}/notify")
def v2_incident_notify_endpoint(eid: int, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    """4.1 單一事件「通報聯絡」：聯絡名單＋通知信草稿（只產生文字，系統不寄信）。"""
    import v2_incident
    return _call(v2_incident.notify, conn, eid)


@router.get("/api/v2/incident/{eid}/contacts.csv")
def v2_incident_contacts_csv_endpoint(eid: int, session: sqlite3.Row = Depends(require_auth),
                                      conn: sqlite3.Connection = Depends(get_db)):
    import v2_incident
    from urllib.parse import quote
    from fastapi.responses import Response
    text = _call(v2_incident.contacts_csv, conn, eid)
    return Response(text.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'事件{eid}_聯絡名單.csv')}"})


# ===== 7 盤點作業（四種盤點共用一條流程，inventory_flow.py）=====

class InvCreateBody(BaseModel):
    title: str | None = None
    room: str | None = None
    racks: list[dict] | None = None


class InvStatusBody(BaseModel):
    diff_key: str
    status: str
    reason: str | None = None


def _kind(kind: str) -> str:
    import inventory_flow
    if kind not in inventory_flow.KINDS:
        raise HTTPException(404, f"不認得的盤點：{kind}")
    return kind


@router.get("/api/v2/onboard/new-devices")
def v2_onboard_new_devices(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """6.1 新設備上線：逐台 5 步（CIA 登記 → 上架 → 網路偵測 → 納管 → 上線檢查）。唯讀。"""
    import v2_onboard
    return v2_onboard.new_devices(conn)


#: 6.1 待納管畫面用到的欄位（2.006.8，找碴 C8：/api/collect/dispatch/latest 一次 4.4 MB，畫面只用其中幾欄）
_PENDING_FIELDS = ("ip", "hostname", "asset_name", "asset_serial", "tab", "status", "collected_at", "open_ports", "message", "win")


@router.get("/api/v2/onboard/pending")
def v2_onboard_pending(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """6.1 待納管：跟舊版「納入管理」同一份 collect_dispatch.latest_run（每個位址最新一次結果），
    只留可處理路線（SSH／WinRM／Agent）且還沒收到資料的列、只回畫面用到的欄位。唯讀。"""
    import collect_dispatch
    d = collect_dispatch.latest_run(conn) or {"results": [], "actionable_tabs": [], "tab_label": {}, "merged": None, "run": None}
    act = set(d.get("actionable_tabs") or [])
    rows = [{k: r[k] for k in _PENDING_FIELDS if k in r} for r in d.get("results") or []
            if r.get("tab") in act and r.get("status") != "collected"]
    import v2_onboard
    alive = v2_onboard.alive_map(conn, rows)
    place = v2_onboard._place(conn, rows)        # 環境與機房同一次查
    envs = v2_onboard.environment_map(conn, rows, place)
    rooms = v2_onboard.room_map(conn, rows, place)
    for r in rows:
        r["alive"] = alive.get(r.get("ip"))
        r["environment"] = envs.get(r.get("ip"), "未分類")
        r["room"] = rooms.get(r.get("ip"), "未知")
    run = d.get("run") or {}
    return {"results": rows, "actionable_tabs": d.get("actionable_tabs") or [], "tab_label": d.get("tab_label") or {},
            "merged": d.get("merged"), "run": {"finished_at": run.get("finished_at")} if run else None}


@router.get("/api/v2/onboard/progress")
def v2_onboard_progress(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """6.1 納管進度：納管覆蓋率（依作業系統大類，對目標百分比）＋連線涵蓋率。唯讀。"""
    import v2_onboard
    return v2_onboard.progress(conn)


@router.get("/api/v2/onboard/progress/drill")
def v2_onboard_progress_drill(family: str, kind: str, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import v2_onboard
    return _call(v2_onboard.drill, conn, family, kind)


@router.get("/api/v2/systems")
def v2_systems_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """3.1 業務系統清單（台數＝系統履歷同一套）。唯讀。"""
    import v2_systems
    return v2_systems.listing(conn)


@router.get("/api/v2/dist")
def v2_dist_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """10.2 分布統計：只放台數（所在地／作業系統／環境別＝2.1 同一份；業務系統排行＝3.1 同一份）。唯讀。"""
    import v2_dist
    return v2_dist.summary(conn)


@router.get("/api/v2/dist/drill")
def v2_dist_drill_endpoint(dim: str, value: str, kind: str = "all", session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    import v2_dist
    return _call(v2_dist.drill, conn, dim, value, kind)


@router.get("/api/v2/inv/{kind}/tasks")
def v2_inv_tasks_endpoint(kind: str, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import inventory_flow
    return {"items": inventory_flow.list_tasks(conn, _kind(kind)), "kind_label": inventory_flow.KINDS[kind],
            "can_edit": _can_edit_nav(session, conn)}


@router.post("/api/v2/inv/{kind}/tasks")
def v2_inv_create_endpoint(kind: str, body: InvCreateBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    """建立盤點任務。機櫃＝開始盤點（凍結機櫃帳）；帳號／軟體／服務＝凍結當下已收集的結果。限管理員＋寫入守門。"""
    import inventory_flow
    from routers.rackaudit import _writer
    by = _writer(session, conn, request)
    if _kind(kind) == "rack":
        import rack_inventory
        return _call(rack_inventory.start_audit, conn, body.room, body.racks, by, body.title)
    return _call(inventory_flow.create_task, conn, kind, by, body.title)


@router.get("/api/v2/inv/{kind}/tasks/{tid}/result")
def v2_inv_result_endpoint(kind: str, tid: int, host: str | None = None, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    import inventory_flow
    return _call(inventory_flow.result, conn, _kind(kind), tid, host)


@router.get("/api/v2/inv/{kind}/tasks/{tid}/diffs")
def v2_inv_diffs_endpoint(kind: str, tid: int, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import inventory_flow
    return {**_call(inventory_flow.diffs, conn, _kind(kind), tid), "can_edit": _can_edit_nav(session, conn)}


@router.post("/api/v2/inv/{kind}/tasks/{tid}/diffs/status")
def v2_inv_status_endpoint(kind: str, tid: int, body: InvStatusBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    import inventory_flow
    from routers.rackaudit import _writer
    by = _writer(session, conn, request)
    return _call(inventory_flow.set_status, conn, _kind(kind), tid, body.diff_key, body.status, body.reason, by)


@router.get("/api/v2/inv/{kind}/tasks/{tid}/report")
def v2_inv_report_endpoint(kind: str, tid: int, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import inventory_flow
    return _call(inventory_flow.report, conn, _kind(kind), tid)


@router.get("/api/v2/inv/{kind}/tasks/{tid}/report.xlsx")
def v2_inv_report_xlsx_endpoint(kind: str, tid: int, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    import inventory_flow
    from urllib.parse import quote
    buf, fname = _call(inventory_flow.report_xlsx, conn, _kind(kind), tid)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


# ===== 10.1 主機組月報（2.007.13）=====
class HostMonthlyNoteBody(BaseModel):
    month: str
    data: dict


@router.get("/api/v2/host-monthly")
def v2_host_monthly_endpoint(month: str | None = None, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    """主機組月報：生命週期、規模、事件、NMON 效能（自動算）＋管理判斷（人填）。"""
    import host_monthly
    return host_monthly.report(conn, month)


@router.put("/api/v2/host-monthly/note")
def v2_host_monthly_note_endpoint(body: HostMonthlyNoteBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                  conn: sqlite3.Connection = Depends(get_db)):
    """存當月的管理判斷（風險追蹤、未來 3–6 個月、關注事項、整體判定）。"""
    import host_monthly
    from routers.overlaps import _actor, _guard
    _guard(conn, request)
    try:
        return host_monthly.save_note(conn, body.month, body.data, _actor(session, request))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/v2/nmon/hosts")
def v2_nmon_hosts_endpoint(month: str | None = None, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    """有逐分鐘資料的主機（10.1 效能圖表的主機清單）。"""
    import nmon_series
    return {"hosts": nmon_series.hosts(conn, month), "months": nmon_series.months(conn),
            "files": nmon_series.files(conn, month), "thresholds": nmon_series.THRESHOLDS}


@router.get("/api/v2/nmon/series")
def v2_nmon_series_endpoint(host: str, month: str, grain: str = "hour", day: str | None = None, keys: str | None = None,
                            session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """畫圖資料。grain：day／hour／raw（raw 要帶 day）。每點 [時間, 平均, 尖峰, 尖峰時間]。"""
    import nmon_series
    try:
        return nmon_series.series(conn, host, month, grain, day, [k for k in (keys or "").split(",") if k] or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/v2/nmon/summary")
def v2_nmon_summary_endpoint(host: str, month: str, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    """每個指標的平均／尖峰（含時間）／超過門檻次數與時段；沒收集的指標不出現。"""
    import nmon_series
    return nmon_series.summary(conn, host, month)


@router.get("/api/v2/nmon/top")
def v2_nmon_top_endpoint(host: str, ts: str, session: sqlite3.Row = Depends(require_auth),
                         conn: sqlite3.Connection = Depends(get_db)):
    """某個時間點最吃 CPU 的程式（取最接近的 TOP 快照；有收才有）。"""
    import nmon_series
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", ts or ""):
        raise HTTPException(400, "ts 格式要是 YYYY-MM-DD HH:MM:SS")
    return nmon_series.top_at(conn, host, ts)


@router.get("/api/v2/nmon/raw")
def v2_nmon_raw_endpoint(host: str, filename: str, session: sqlite3.Row = Depends(require_auth),
                         conn: sqlite3.Connection = Depends(get_db)):
    """下載原始 NMON 檔（只有管理員；檔案存在資料庫旁的 nmon_raw/，不接受路徑跳脫）。"""
    import nmon_series
    from fastapi.responses import FileResponse
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以下載原始 NMON 檔")
    p = nmon_series.raw_file(conn, host, filename)
    if p is None:
        raise HTTPException(404, "沒有這個原始檔（上傳時間早於 2.007.19，或已被清掉）")
    return FileResponse(str(p), filename=filename)


@router.post("/api/v2/host-monthly/nmon")
def v2_host_monthly_nmon_endpoint(request: Request, file: UploadFile = File(...),
                                  session: sqlite3.Row = Depends(require_auth),
                                  conn: sqlite3.Connection = Depends(get_db)):
    """上傳一個 NMON 檔（一台一段時間）；同一台同一時間點再傳一次＝覆蓋。"""
    import host_monthly
    from routers.overlaps import _actor, _guard
    _guard(conn, request)
    data = file.file.read(50 * 1024 * 1024 + 1)         # 同步函式＋限量讀：解壓與解析是純 Python 的 CPU 工作，不能占住事件迴圈
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(400, "檔案超過 50 MB")
    try:
        return host_monthly.import_nmon(conn, data, file.filename or "upload.nmon", _actor(session, request))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
