"""中繼匯入（方案 A）的 API。邏輯都在 relay_import.py，這裡只接線。

  GET  /api/v2/relay-import            設定＋各資料來源的鮮度燈（登入即可看）
  GET  /api/v2/relay-import/batches    匯入紀錄（誰／何時／批次／筆數／結果／拒收原因）
  GET  /api/v2/relay-import/unmatched  對不到資產的清單（不丟棄，等人處理）
  GET  /api/v2/relay-import/unity      Dell Unity 匯入結果（陣列／Pool／LUN／主機↔LUN／對不到／每日容量；5.2 儲存容量的 Unity 頁籤）
  PUT  /api/v2/relay-import/settings   共用資料夾、啟用、過期天數、檢查間隔（管理員）
  POST /api/v2/relay-import/run        立即匯入（管理員；盤中靜默窗內要二次確認）

這裡只讀共用資料夾的檔案，不連任何外部系統。沒有任何端點收或回傳密碼。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

import quiet_window

router = _RouteCollector()

__all__ = ["relay_import_view_endpoint", "relay_import_batches_endpoint", "relay_import_unmatched_endpoint", "relay_import_unity_endpoint",
           "relay_import_unity_accept_empty_endpoint",
           "relay_import_settings_endpoint", "relay_import_run_endpoint"]


class RelaySettingsBody(BaseModel):
    enabled: bool = False
    dir: str = ""
    stale_days: int = 2
    interval_min: int = 15


def _relay_admin(conn, session) -> str:
    """設定與立即匯入只有管理員群組；登入停用時 fail-closed（沒有真實身分就留不了稽核）。"""
    import auth
    if auth_disabled(conn):
        raise HTTPException(403, "登入停用時不允許設定或執行中繼匯入（需要真實身分才能留下稽核）")
    if session is None or auth.session_level(session) != "admin":
        raise HTTPException(403, "只有管理員群組可以設定或執行中繼匯入")
    return session["username"]


@router.get("/api/v2/relay-import")
def relay_import_view_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import relay_import as ri
    return ri.view(conn)


@router.get("/api/v2/relay-import/batches")
def relay_import_batches_endpoint(source: str | None = None, limit: int = 200, session: sqlite3.Row = Depends(require_auth),
                                  conn: sqlite3.Connection = Depends(get_db)):
    import relay_import as ri
    return {"rows": ri.list_batches(conn, source, limit)}


@router.get("/api/v2/relay-import/unmatched")
def relay_import_unmatched_endpoint(source: str | None = None, session: sqlite3.Row = Depends(require_auth),
                                    conn: sqlite3.Connection = Depends(get_db)):
    import relay_import as ri
    return {"rows": ri.list_unmatched(conn, source)}


@router.get("/api/v2/relay-import/unity")
def relay_import_unity_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import storage_relay
    return storage_relay.overview(conn)


class UnityAcceptEmptyBody(BaseModel):
    serial: str


@router.post("/api/v2/relay-import/unity/accept-empty")
def relay_import_unity_accept_empty_endpoint(body: UnityAcceptEmptyBody, session: sqlite3.Row = Depends(require_auth),
                                             conn: sqlite3.Connection = Depends(get_db)):
    """管理員：Unity 回報 0 個 Pool 被擋（empty_snapshot）且確定真的清空了 → 下一次匯入接受這台的空快照（只一次）。"""
    import logging

    import storage_relay
    actor = _relay_admin(conn, session)
    try:
        res = storage_relay.accept_empty(conn, body.serial)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    logging.getLogger(__name__).warning("Unity 接受空快照：%s 由 %s 設定（可重跑批次 %s）", res["serial"], actor, res["retry_batches"])
    return res


@router.put("/api/v2/relay-import/settings")
def relay_import_settings_endpoint(body: RelaySettingsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                   conn: sqlite3.Connection = Depends(get_db)):
    import relay_import as ri
    from routers.overlaps import _guard
    who = _relay_admin(conn, session)
    _guard(conn, request)
    try:
        cfg = ri.set_config(conn, body.enabled, body.dir, body.stale_days, body.interval_min)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    activity.log(conn, username=who, ip=activity.client_ip(request), method="RELAY",
                 path="中繼匯入：改設定（" + ("啟用" if cfg["enabled"] else "停用") + "）", status=200, action="relay_import_settings")
    return ri.view(conn)


@router.post("/api/v2/relay-import/run")
def relay_import_run_endpoint(request: Request, confirm_in_window: bool = False, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    """人為「立即匯入」。純讀檔，但窗內（平日 08:30–15:00）仍需二次確認，比照全站靜默窗
    （前端 useApi 收到 409 quiet_window_confirm 會跳確認，再帶 ?confirm_in_window=true 重送）。"""
    import relay_import as ri
    from routers.overlaps import _guard
    who = _relay_admin(conn, session)
    _guard(conn, request)
    quiet_window.confirm_from_request(conn, "relay_import_tick", confirm_in_window, who, request)
    out = ri.run_scan(conn, "manual", who)
    activity.log(conn, username=who, ip=activity.client_ip(request), method="RELAY",
                 path="中繼匯入：立即匯入（" + str(out.get("message", out.get("status")))[:120] + "）", status=200, action="relay_import_run")
    return out


# ---------------------------------------------------------------- WhatsUp 告警事件（2.043.0，wug_alert.py）
#   GET  /api/v2/wug-alerts              狀態（啟用、最後收到、心跳、最近一次掃描、開著幾筆、對不到幾筆）＋設定
#   GET  /api/v2/wug-alerts/events       事件清單（state／tier／month／q／machine_key／ip 可組合）
#   GET  /api/v2/wug-alerts/machine      一台機器的目前告警＋最近幾次（夜間速查、主機履歷用；key＝machine key／IP／主機名）
#   GET  /api/v2/wug-alerts/month        月報彙總（每台次數、總時長；點下去＝events?month=）
#   GET  /api/v2/wug-alerts/notices      通知紀錄（發生／升級／恢復／摘要／監控中斷，含 Teams 投遞狀態）
#   PUT  /api/v2/wug-alerts/settings     啟用、心跳、升級、摘要間隔、對不到資產的分級、保存天數（管理員）
#   PUT  /api/v2/wug-alerts/events/{id}/verdict  回報這則告警是真的／假的（一般使用者以上）
#   GET  /api/v2/wug-alerts/mutes        不通知清單（2.060.18；哪台、哪個監控項目、星期＋時段）
#   POST /api/v2/wug-alerts/mutes        從一筆事件加一條不通知規則（管理員）
#   DELETE /api/v2/wug-alerts/mutes/{id} 刪掉一條（管理員）
__all__ += ["wug_alerts_view_endpoint", "wug_alerts_events_endpoint", "wug_alerts_machine_endpoint", "wug_alerts_month_endpoint",
            "wug_alerts_notices_endpoint", "wug_alerts_settings_endpoint", "wug_alerts_verdict_endpoint"]


class WugAlertSettingsBody(BaseModel):
    enabled: bool = False
    heartbeat_min: int = 10
    escalate_min: int = 60
    digest_min: int = 60
    unmatched_tier: str = "red"
    retention_days: int = 730
    notify_policies: str = ""
    confirm_min: int = 10


class WugVerdictBody(BaseModel):
    verdict: str = ""


class WugMuteBody(BaseModel):
    event_id: int
    whole_device: bool = False
    days: list[str] = []
    time_from: str = ""
    time_to: str = ""
    note: str = ""


def _wug_can_edit(conn, session) -> bool:
    import auth
    return (not auth_disabled(conn)) and session is not None and auth.session_level(session) == "admin"


def _can_notify(conn, session) -> bool:
    """R16 一鍵通知：一般帳號以上、登入啟用中（要真實身分留紀錄）。只能看的帳號沒有按鈕，後端也擋。"""
    import auth
    return (not auth_disabled(conn)) and session is not None and auth.session_level(session) in ("user", "admin")


@router.get("/api/v2/wug-alerts")
def wug_alerts_view_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import wug_alert as wa
    out = wa.status(conn)
    out["can_edit"] = _wug_can_edit(conn, session)
    return out


@router.get("/api/v2/wug-alerts/events")
def wug_alerts_events_endpoint(state: str = "", tier: str = "", month: str = "", q: str = "", machine_key: str = "", ip: str = "",
                               wug_device_id: str = "", no_machine: bool = False, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import alert_contact
    import alert_notify
    import wug_alert as wa
    try:
        rows = wa.events(conn, state=state, tier=tier, month=month, q=q, machine_key=machine_key, ip=ip,
                         wug_device_id=wug_device_id, no_machine=no_machine)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    alert_contact.attach(conn, rows)          # 2.044.2（R15）：告警聯絡五欄
    alert_notify.attach(conn, rows)           # 2.044.2（R16）：已人工通知幾次、最後結果
    return {"rows": rows, "can_notify": _can_notify(conn, session)}


@router.get("/api/v2/wug-alerts/machine")
def wug_alerts_machine_endpoint(key: str = "", ip: str = "", session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    """key 對得到唯一一台 → 用它的 machine key＋所有 IP；對不到 → 只用 ip（對不到資產的告警仍查得到，不猜）。"""
    import wug_alert as wa
    mk, ips = None, ([ip.strip()] if ip.strip() else [])
    if key.strip():
        import v2_sections
        try:
            ctx = v2_sections.Ctx(conn, key.strip())
            mk, ips = ctx.mkey, list(dict.fromkeys(ctx.ips + ips))
        except LookupError:
            ips = list(dict.fromkeys(ips + [key.strip()]))
    out = wa.for_machine(conn, mk, ips)
    import alert_contact
    import alert_notify
    for k in ("current", "recent"):
        alert_contact.attach(conn, out.get(k) or [])
        alert_notify.attach(conn, out.get(k) or [])
    out["can_notify"] = _can_notify(conn, session)
    return out


@router.get("/api/v2/wug-alerts/month")
def wug_alerts_month_endpoint(ym: str = "", session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import wug_alert as wa
    ym = ym or wa._now().strftime("%Y-%m")
    try:
        return wa.month_summary(conn, ym)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/v2/wug-alerts/notices")
def wug_alerts_notices_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import wug_alert as wa
    return {"rows": wa.notices(conn)}


@router.put("/api/v2/wug-alerts/settings")
def wug_alerts_settings_endpoint(body: WugAlertSettingsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                 conn: sqlite3.Connection = Depends(get_db)):
    import wug_alert as wa
    from routers.overlaps import _guard
    if not _wug_can_edit(conn, session):
        raise HTTPException(403, "只有管理員群組可以改告警設定（登入停用時也不行：需要真實身分才能留下稽核）")
    _guard(conn, request)
    try:
        cfg = wa.save_settings(conn, body.model_dump() if hasattr(body, "model_dump") else body.dict())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    activity.log(conn, username=session["username"], ip=activity.client_ip(request), method="WUGALERT",
                 path=f"WhatsUp 告警：改設定（{'啟用' if cfg['enabled'] else '停用'}・心跳 {cfg['heartbeat_min']} 分・升級 {cfg['escalate_min']} 分・"
                      f"摘要 {cfg['digest_min']} 分・對不到資產 {cfg['unmatched_tier']}・保存 {cfg['retention_days']} 天）",
                 status=200, action="wug_alert_settings")
    conn.commit()
    out = wa.status(conn)
    out["can_edit"] = True
    return out


@router.put("/api/v2/wug-alerts/events/{event_id}/verdict")
def wug_alerts_verdict_endpoint(event_id: int, body: WugVerdictBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    import auth
    import wug_alert as wa
    from routers.overlaps import _guard
    if auth_disabled(conn):
        raise HTTPException(403, "登入停用時不允許回報真假（需要真實身分）")
    if session is None or auth.session_level(session) == "viewer":
        raise HTTPException(403, "只能看的帳號不能回報真假")
    _guard(conn, request)
    try:
        row = wa.set_verdict(conn, event_id, body.verdict, session["username"])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    activity.log(conn, username=session["username"], ip=activity.client_ip(request), method="WUGALERT",
                 path=f"WhatsUp 告警：事件 #{event_id} 回報 {body.verdict or '清除'}", status=200, action="wug_alert_verdict")
    conn.commit()
    return row


@router.get("/api/v2/wug-alerts/mutes")
def wug_alerts_mutes_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import wug_alert as wa
    return {"items": wa.list_mutes(conn), "can_edit": _wug_can_edit(conn, session)}


@router.post("/api/v2/wug-alerts/mutes")
def wug_alerts_mute_add_endpoint(body: WugMuteBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                 conn: sqlite3.Connection = Depends(get_db)):
    import wug_alert as wa
    from routers.overlaps import _guard
    if not _wug_can_edit(conn, session):
        raise HTTPException(403, "只有管理員可以改不通知清單（登入停用時也不行：需要真實身分才能留下稽核）")
    _guard(conn, request)
    try:
        m = wa.add_mute(conn, body.event_id, body.whole_device, body.days, body.time_from, body.time_to, body.note, session["username"])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "已經有一樣的規則")
    activity.log(conn, username=session["username"], ip=activity.client_ip(request), method="WUGALERT",
                 path=f"WhatsUp 告警：加不通知 {m['device_label']} {'全部監控項目' if m['monitor'] == '*' else m['monitor']}・{m['when_text']}",
                 status=200, action="wug_alert_mute")
    conn.commit()
    return m


@router.delete("/api/v2/wug-alerts/mutes/{mute_id}")
def wug_alerts_mute_del_endpoint(mute_id: int, request: Request, session: sqlite3.Row = Depends(require_auth),
                                 conn: sqlite3.Connection = Depends(get_db)):
    import wug_alert as wa
    from routers.overlaps import _guard
    if not _wug_can_edit(conn, session):
        raise HTTPException(403, "只有管理員可以改不通知清單")
    _guard(conn, request)
    try:
        m = wa.del_mute(conn, mute_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    activity.log(conn, username=session["username"], ip=activity.client_ip(request), method="WUGALERT",
                 path=f"WhatsUp 告警：刪不通知 {m['device_label']} {'全部監控項目' if m['monitor'] == '*' else m['monitor']}・{m['when_text']}",
                 status=200, action="wug_alert_mute_del")
    conn.commit()
    return {"ok": True}
