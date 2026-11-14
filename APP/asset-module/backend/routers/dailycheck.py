"""1.5 每日檢查（試辦，2.060.x）的 API。邏輯都在 daily_check.py，這裡只接線。

權限（access_policy）：讀＝登入即可；產生快照、確認、批次確認、送出、更正＝一般帳號以上；設定＝管理員；匯出 Excel＝一般以上。
寫入都過 write_guard（登入停用時可要求管理密碼）並記「誰」。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()


class DcRunBody(BaseModel):
    run_id: int


class DcConfirmBody(BaseModel):
    row_ids: list[int]
    result: str
    note: str | None = None
    extra: dict | None = None


class DcCorrectBody(BaseModel):
    row_id: int
    result: str
    note: str
    extra: dict | None = None


class DcSettingsBody(BaseModel):
    patch: dict


class DcApproveBody(BaseModel):
    path: str


def _w(conn, request, session) -> str:
    from routers.overlaps import _actor, _guard

    _guard(conn, request)
    return _actor(session, request)


def _call(fn, *a):
    try:
        return fn(*a)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/daily-check")
def daily_check_endpoint(day: str | None = None, run_id: int | None = None, session: sqlite3.Row = Depends(require_auth),
                         conn: sqlite3.Connection = Depends(get_db)):
    """某一天最新一次（或指定 run_id）的快照、逐列、卡片數字；還沒有就回 run=None 與原因。"""
    import daily_check as dc

    d = day or dc.now_local().date().isoformat()
    cfg = dc.settings(conn)
    info = {"day": d, "enabled": cfg["enabled"], "signoff": cfg["signoff"], "times": {k: cfg[k] for k in ("snapshot_time", "remind_time", "escalate_time")},
            "thresholds": {k: cfg[k] for k in ("cpu", "mem", "disk", "swap", "poll_min", "alert_fresh_min", "perf_fresh_h", "disk_list",
                                               "disk_alert", "lp_fresh_min", "wug_member_jump_pct", "vm_fresh_min")},
            "perf_source": cfg["daily_check_perf_source"], "lp_alert": dc.today_status(conn).get("lp_alert"),
            "trial_day": dc.trial_day(conn, dc.now_local().date()), "now": dc._s(dc.now_local()),
            "phrases": dc.load_items().get("phrases", []), "warnings_now": dc.setting_warnings(dc.effective(conn, cfg), dc.now_local().date())}
    info["vm_pending"] = {k: cfg[k] for k in dc.VM_PENDING} if cfg.get("vm_vcenters") else None
    rid = run_id or dc.latest_run_id(conn, d)
    if rid is None:
        return {**info, "run": None, "rows": [], "counts": dc.counts_of([]), "runs": [], "vm_newer": None}
    v = _call(dc.run_view, conn, rid)
    return {**info, **v, "vm_newer": dc.vm_newer(conn, v["run"])}


@router.get("/api/daily-check/today")
def daily_check_today_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """1.1 首頁一行：今天的每日檢查狀態。唯讀。"""
    import daily_check as dc
    return dc.today_status(conn)


@router.get("/api/daily-check/mail-log")
def daily_check_mail_log_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """升級信寄送紀錄（每一次成功或失敗都有一筆）。唯讀。"""
    import daily_check as dc
    return {"items": dc.mail_log(conn)}


@router.get("/api/daily-check/events")
def daily_check_events_endpoint(run_id: int, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import daily_check as dc
    return {"items": dc.events(conn, run_id)}


@router.get("/api/daily-check/settings")
def daily_check_settings_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """管理員看完整設定；其他人只看門檻與時間（不含 IP 清單、主機清單、收件人、例外）。"""
    import auth as _auth
    import daily_check as dc
    s = dc.settings(conn)
    if not (_auth.is_admin(session) or auth_disabled(conn)):
        s = {k: s[k] for k in ("enabled", "signoff", "snapshot_time", "remind_time", "escalate_time", "notify_stop_time", "cpu", "mem", "disk",
                               "poll_min", "alert_fresh_min", "perf_fresh_h", "daily_check_perf_source", "lp_fresh_min", "disk_list",
                               "disk_alert", "wug_member_jump_pct", "vm_lun_threshold", "vm_lun_unit", "vm_lun_op", "vm_alert_levels",
                               "vm_fresh_min", "vm_adapter_stale_min", "vm_record_stale_min", "vm_mute_max_days")} | {"limited": True}
    return {"settings": s, "items": dc.load_items()["items"]}


@router.get("/api/daily-check/lastpoll")
def daily_check_lastpoll_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """2.062.0（S13）：WhatsUp 上次輪循的最新批次、最新完整批次、各群組台數／IP 數／各指標筆數與成員核准狀態；核准紀錄。唯讀。"""
    import daily_check as dc
    import wug_lastpoll as lp
    now = dc.now_local()
    return {**lp.status_view(conn, dc.settings(conn), now), "approvals": lp.approvals(conn, 50)}


@router.get("/api/daily-check/vmware")
def daily_check_vmware_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """2.064.0：VMware（vROps）最新批次、最新完整批次、看到的 vCenter（vcid）與收集器狀態；VMware 設定的改動紀錄。唯讀。
    非管理員只看批次狀態（不含 vcid、收集器清單、設定紀錄）。"""
    import aria_dailycheck as ad
    import auth as _auth
    import daily_check as dc
    v = ad.status_view(conn, dc.now_local())
    if not (_auth.is_admin(session) or auth_disabled(conn)):
        return {"latest": v["latest"], "latest_complete": v["latest_complete"], "vcenters": [], "adapters": [], "log": [], "limited": True}
    log = [{**x, "__no": i} for i, x in enumerate(dc.setting_log(conn, 100), 1)]
    return {**v, "log": log}


@router.get("/api/daily-check/lastpoll/diff")
def daily_check_lastpoll_diff_endpoint(path: str, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """核准前看：最新完整批次這個群組的成員 vs 上一次核准，新增／移除哪幾台。唯讀。"""
    import wug_lastpoll as lp
    return _call(lp.member_diff, conn, path)


@router.post("/api/daily-check/approve-members")
def daily_check_approve_members(body: DcApproveBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    """管理員核准某個 WhatsUp 群組「最新完整批次」的目前成員（範圍跳動後才恢復判定）。寫核准紀錄＋操作紀錄。"""
    import activity
    import daily_check as dc
    import wug_lastpoll as lp
    by = _w(conn, request, session)
    out = _call(lp.approve, conn, body.path, by, dc.now_local())
    try:
        activity.log(conn, username=by, ip=activity.client_ip(request), method="POST", path=f"/api/daily-check/approve-members {body.path[:200]}",
                     status=200, action="change")
    except Exception as exc:  # noqa: BLE001 - 核准已寫進核准紀錄表（那張就是稽核紀錄）；操作紀錄失敗只記日誌
        import logging
        logging.getLogger("daily_check").error("核准成員的操作紀錄寫入失敗：%s", type(exc).__name__)
    return out


@router.put("/api/daily-check/settings")
def daily_check_settings_put(body: DcSettingsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    import daily_check as dc
    by = _w(conn, request, session)
    try:
        return {"settings": dc.save_settings(conn, body.patch, by=by)}
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/daily-check/generate")
def daily_check_generate(request: Request, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """人工產生（或重新產生）今天的快照；舊的保留。"""
    import daily_check as dc
    by = _w(conn, request, session)
    return {"run_id": dc.generate(conn, by, "manual")}


@router.post("/api/daily-check/confirm")
def daily_check_confirm(body: DcConfirmBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                        conn: sqlite3.Connection = Depends(get_db)):
    import daily_check as dc
    by = _w(conn, request, session)
    return _call(dc.confirm, conn, body.row_ids, body.result, body.note, by, body.extra)


@router.post("/api/daily-check/confirm-fresh-ok")
def daily_check_confirm_fresh(body: DcRunBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import daily_check as dc
    by = _w(conn, request, session)
    return _call(dc.confirm_fresh_ok, conn, body.run_id, by)


@router.post("/api/daily-check/submit")
def daily_check_submit(body: DcRunBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                       conn: sqlite3.Connection = Depends(get_db)):
    import daily_check as dc
    by = _w(conn, request, session)
    return _call(dc.submit, conn, body.run_id, by)


@router.post("/api/daily-check/correct")
def daily_check_correct(body: DcCorrectBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                        conn: sqlite3.Connection = Depends(get_db)):
    import daily_check as dc
    by = _w(conn, request, session)
    return _call(dc.correct, conn, body.row_id, body.result, body.note, by, body.extra)


@router.get("/api/daily-check/{run_id}/export.xlsx")
def daily_check_export(run_id: int, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import daily_check as dc
    buf, name = _call(dc.export_xlsx, conn, run_id)
    return _xlsx_response(buf, name)
