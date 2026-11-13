"""1.1 今日（R9）、1.4 追蹤處理（R10）、告警聯絡資訊（R15）、告警一鍵通知（R16）的 API（2.044.x）。

  GET  /api/v2/today                               今日：現在的告警、今天的事件、今天的資產異動、開盤尖峰（未啟用）、追蹤處理一行、各來源資料時間
  GET  /api/v2/opening                             今日開盤尖峰下鑽：某天範圍內全部 ESXi 主機（CPU 四欄、近 20 日基準、資產對應、AP 聯絡五欄）
  GET  /api/v2/track                               追蹤處理：每一類的摘要（筆數＝下鑽列數）＋全部列（篩選／排序／匯出在畫面做）
  GET  /api/v2/alert-contacts                      告警聯絡資訊：每個 APID 五欄＋來源（登入即可看）
  PUT  /api/v2/alert-contacts/{api_id}             管理員手動覆寫（值空白＝清掉覆寫）
  POST /api/v2/alert-contacts/import               從「資料匯入」頁匯入（confirm=0 預覽、1 寫入；管理員）
  GET  /api/v2/wug-alerts/events/{id}/notify       一鍵通知：送前預覽（收件人、內容、之前通知過幾次）（一般帳號以上）
  POST /api/v2/wug-alerts/events/{id}/notify       一鍵通知：確認送出（排進 Teams 佇列＋留紀錄；重送要 resend=true）
  GET  /api/v2/alert-notices                       人工通知紀錄（成功／失敗／不確定／處理中，帶事件 ID）
  GET  /api/v2/routine?key=                        一台的例行時段、異動紀錄、建議例行、需要注意門檻（2.066.x）
  GET  /api/v2/routine/suggestions                 全部的建議例行（管理員確認才生效）
  POST /api/v2/routine                             新增例行時段（管理員）；PUT／DELETE /api/v2/routine/{id} 修改／刪除（留紀錄）
  POST /api/v2/routine/decide                      建議例行確認或拒絕（管理員）
  PUT  /api/v2/routine-settings                    需要注意門檻分鐘數（管理員）
範例檔：GET /api/import/alert_contact/export-template（既有範例檔端點）。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["v2_today_endpoint", "v2_opening_endpoint", "v2_track_endpoint", "alert_contacts_endpoint", "alert_contact_save_endpoint",
           "alert_contacts_import_endpoint", "alert_contacts_restore_endpoint", "alert_notify_preview_endpoint", "alert_notify_send_endpoint", "alert_notices_endpoint",
           "routine_get_endpoint", "routine_suggestions_endpoint", "routine_add_endpoint", "routine_update_endpoint",
           "routine_delete_endpoint", "routine_decide_endpoint", "routine_settings_endpoint"]


def _level(session) -> str:
    import auth
    return auth.session_level(session) if session is not None else ""


def _real_user(conn, session, need: str) -> str:
    """寫入要真實身分（登入停用時拒絕）；need＝user（一般以上）或 admin。回帳號名。"""
    if auth_disabled(conn) or session is None:
        raise HTTPException(403, "登入停用時不允許（需要真實身分才能留下紀錄）")
    lv = _level(session)
    if need == "admin" and lv != "admin":
        raise HTTPException(403, "只有管理員可以改")
    if lv not in ("user", "admin"):
        raise HTTPException(403, "只能看的帳號不能做這個動作")
    return session["username"]


@router.get("/api/v2/today")
def v2_today_endpoint(scope: str = "policy", session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """scope：policy（預設）＝告警只列跟 Teams 同一個主機組政策範圍；all＝看全部。"""
    import v2_today
    out = v2_today.today(conn, scope="all" if scope == "all" else "policy")
    out["can_notify"] = (not auth_disabled(conn)) and _level(session) in ("user", "admin")
    return out


@router.get("/api/v2/opening")
def v2_opening_endpoint(day: str = "", session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """今日開盤尖峰「看全部」：那天範圍內每台 ESXi 一列（跟首頁 Top10 同一份計算）。唯讀；day 空白＝今天。"""
    import opening_peak as op
    import re as _re
    d = (day or op._now().strftime("%Y-%m-%d")).strip()
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        raise HTTPException(400, "day 要是 YYYY-MM-DD")
    rs = op.attach_contacts(conn, op.rows(conn, d))
    return {"day": d, "rows": rs, "total": len(rs)}


@router.get("/api/v2/track")
def v2_track_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import v2_today
    return v2_today.track(conn)


# ── R15 告警聯絡資訊 ───────────────────────────────────────────────────
class AlertContactBody(BaseModel):
    ap_team: str | None = None
    unit: str | None = None
    system_name: str | None = None
    sys_class: str | None = None
    email: str | None = None


@router.get("/api/v2/alert-contacts")
def alert_contacts_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import alert_contact
    out = alert_contact.listing(conn)
    out["can_edit"] = (not auth_disabled(conn)) and _level(session) == "admin"
    return out


@router.put("/api/v2/alert-contacts/{api_id}")
def alert_contact_save_endpoint(api_id: str, body: AlertContactBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    import alert_contact
    from routers.overlaps import _guard
    user = _real_user(conn, session, "admin")
    _guard(conn, request)
    data = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    try:
        row = alert_contact.save_override(conn, api_id, data, user)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    activity.log(conn, username=user, ip=activity.client_ip(request), method="ALERTCONTACT",
                 path=f"告警聯絡資訊：{api_id} 人工覆寫", status=200, action="alert_contact_override")
    conn.commit()
    return row


@router.post("/api/v2/alert-contacts/import")
def alert_contacts_import_endpoint(request: Request, file: UploadFile = File(...), confirm: bool = Form(False), plan_digest: str = Form(""),
                                   session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """confirm=false：只預覽（不寫）；true：整份取代匯入資料（人工覆寫不碰）。
    確認要帶預覽回的 plan_digest；有任何錯誤（can_confirm=false）或預覽過期一律 409、一筆都不動。"""
    import alert_contact
    import daily_risk
    user = _real_user(conn, session, "admin")
    name = daily_risk.clean_filename(file.filename)
    try:
        data = daily_risk.read_limited(file.file)
        if not confirm:
            out = alert_contact.plan(conn, name, data)
            out.pop("_rows", None)
            return out
        from routers.overlaps import _guard
        _guard(conn, request)
        out = alert_contact.apply(conn, name, data, user, plan_digest)
    except alert_contact.ImportBlocked as exc:
        conn.rollback()
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc))
    activity.log(conn, username=user, ip=activity.client_ip(request), method="ALERTCONTACT",
                 path=f"告警聯絡資訊：匯入 {name}（{out['total']} 筆，新增 {out['new']}、修改 {out['changed']}、移除 {out['removed']}）",
                 status=200, action="alert_contact_import")
    conn.commit()
    return out


@router.post("/api/v2/alert-contacts/restore/{snapshot_id}")
def alert_contacts_restore_endpoint(snapshot_id: int, request: Request, session: sqlite3.Row = Depends(require_auth),
                                    conn: sqlite3.Connection = Depends(get_db)):
    """把匯入資料還原成某一份匯入前快照（還原前也存一份）。管理員。"""
    import alert_contact
    from routers.overlaps import _guard
    user = _real_user(conn, session, "admin")
    _guard(conn, request)
    try:
        out = alert_contact.restore(conn, snapshot_id, user)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    activity.log(conn, username=user, ip=activity.client_ip(request), method="ALERTCONTACT",
                 path=f"告警聯絡資訊：還原快照 #{snapshot_id}（{out['restored']} 筆）", status=200, action="alert_contact_restore")
    conn.commit()
    return out


# ── R16 一鍵通知 ─────────────────────────────────────────────────────
class AlertNotifyBody(BaseModel):
    resend: bool = False
    preview_hash: str = ""


@router.get("/api/v2/wug-alerts/events/{event_id}/notify")
def alert_notify_preview_endpoint(event_id: int, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import alert_notify
    user = _real_user(conn, session, "user")
    try:
        return alert_notify.preview(conn, event_id, user)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/api/v2/wug-alerts/events/{event_id}/notify")
def alert_notify_send_endpoint(event_id: int, body: AlertNotifyBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    import alert_notify
    from routers.overlaps import _guard
    user = _real_user(conn, session, "user")
    _guard(conn, request)
    try:
        out = alert_notify.send(conn, event_id, user, resend=body.resend, preview_hash=body.preview_hash)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except alert_notify.NotifyRefused as exc:
        raise HTTPException(409, str(exc))
    activity.log(conn, username=user, ip=activity.client_ip(request), method="ALERTNOTIFY",
                 path=f"告警一鍵通知：事件 #{event_id} 第 {out['seq']} 次{'（重送）' if out['is_resend'] else ''} → {out['recipient']}",
                 status=200, action="alert_notify")
    conn.commit()
    return out


@router.get("/api/v2/alert-notices")
def alert_notices_endpoint(event_id: int | None = None, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import alert_notify
    return {"rows": alert_notify.history(conn, event_id)}


# ── 2.066.x 需要注意／例行時段 ───────────────────────────────────────────
class RoutineBody(BaseModel):
    key: str = ""
    start: str = ""
    end: str = ""
    expires_on: str | None = None
    note: str | None = None


class RoutineDecideBody(BaseModel):
    key: str = ""
    start: str = ""
    end: str = ""
    decision: str = ""


class RoutineSettingsBody(BaseModel):
    attention_min: int
    attention_open_min: int | None = None
    longterm_hours: int | None = None


def _routine_key(conn, key: str) -> str:
    """主機頁的 key（machine key／IP／主機名）→ 例行時段用的同一把鍵（alert_attention.host_key：machine key；對不到資產用 ip:<IP>）。"""
    k = (key or "").strip()
    if not k:
        raise HTTPException(400, "缺主機")
    if k.startswith(("ip:", "wug:")):
        return k
    import v2_sections
    try:
        return v2_sections.Ctx(conn, k).mkey
    except LookupError:
        raise HTTPException(404, "找不到這台主機")


def _now_local():
    import v2_today
    return v2_today._now()


@router.get("/api/v2/routine")
def routine_get_endpoint(key: str = "", session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import alert_attention as aa
    hk = _routine_key(conn, key)
    now = _now_local()
    ws = aa.windows(conn, hk)
    today = now.strftime("%Y-%m-%d")
    for i, w in enumerate(ws, 1):
        w["__no"] = i
        w["expired"] = bool(w["expires_on"] and w["expires_on"] < today)
    return {"host_key": hk, "windows": ws, "log": aa.window_log(conn, hk), "suggestions": aa.suggestions(conn, now, hk),
            "attention_min": aa.threshold(conn), "attention_open_min": aa.thresholds(conn)["open"],
            "longterm_hours": aa.thresholds(conn)["long_h"], "can_edit": (not auth_disabled(conn)) and _level(session) == "admin"}


@router.get("/api/v2/routine/suggestions")
def routine_suggestions_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import alert_attention as aa
    return {"rows": aa.suggestions(conn, _now_local()), "can_edit": (not auth_disabled(conn)) and _level(session) == "admin"}


def _routine_write(conn, request, session):
    from routers.overlaps import _guard
    user = _real_user(conn, session, "admin")
    _guard(conn, request)
    return user


@router.post("/api/v2/routine")
def routine_add_endpoint(body: RoutineBody, request: Request, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import alert_attention as aa
    user = _routine_write(conn, request, session)
    try:
        row = aa.add_window(conn, _routine_key(conn, body.key), body.start, body.end, body.expires_on, body.note or "", user, _now_local())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    conn.commit()
    return row


@router.put("/api/v2/routine/{window_id}")
def routine_update_endpoint(window_id: int, body: RoutineBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    import alert_attention as aa
    user = _routine_write(conn, request, session)
    try:
        row = aa.update_window(conn, window_id, body.start, body.end, body.expires_on, body.note or "", user, _now_local())
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    conn.commit()
    return row


@router.delete("/api/v2/routine/{window_id}")
def routine_delete_endpoint(window_id: int, request: Request, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import alert_attention as aa
    user = _routine_write(conn, request, session)
    try:
        aa.delete_window(conn, window_id, user, _now_local())
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    conn.commit()
    return {"ok": True}


@router.post("/api/v2/routine/decide")
def routine_decide_endpoint(body: RoutineDecideBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    import alert_attention as aa
    user = _routine_write(conn, request, session)
    try:
        out = aa.decide(conn, _routine_key(conn, body.key), body.start, body.end, body.decision, user, _now_local())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    conn.commit()
    return out


@router.put("/api/v2/routine-settings")
def routine_settings_endpoint(body: RoutineSettingsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import alert_attention as aa
    import daily_risk
    user = _routine_write(conn, request, session)
    th = aa.thresholds(conn)
    before = {"attention_min": th["min"], "attention_open_min": th["open"], "longterm_hours": th["long_h"]}
    try:
        th = aa.save_threshold(conn, body.attention_min, body.attention_open_min, body.longterm_hours)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    daily_risk._audit(conn, "alert_attention", "setting", aa.K_MIN, before, {"attention_min": th["min"], "attention_open_min": th["open"], "longterm_hours": th["long_h"]}, user)
    conn.commit()
    return {"attention_min": th["min"], "attention_open_min": th["open"], "longterm_hours": th["long_h"]}
