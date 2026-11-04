"""每日變化報告、早上摘要信、值班一頁卡的 API（v1.500.0）。

照 routers/ 的拆法用 _RouteCollector：api.py 只多一個模組名。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["changes_get", "changes_snapshot_now", "digest_settings_get", "digest_settings_put",
           "digest_preview", "digest_test_mail", "digest_mail_log", "oncall_get",
           "teams_settings_get", "teams_settings_put", "teams_webhook_put", "teams_webhook_delete", "teams_preview",
           "teams_test_send", "teams_live_test", "teams_log", "teams_retry",
           "disk_alert_get", "disk_alert_put", "disk_alert_sample"]


class DigestSettingsBody(BaseModel):
    snapshot_time: str | None = None
    retain_days: int | None = None
    gap_days: int | None = None
    count_change_pct: int | None = None
    count_change_min: int | None = None
    uptime_days: int | None = None
    mail_enabled: bool | None = None
    mail_time: str | None = None
    recipients: str | None = None
    base_url: str | None = None
    subject_prefix: str | None = None


def _admin_only(session, conn) -> None:
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員（admin 帳號）能改每日摘要的設定或寄測試信")


@router.get("/api/changes")
def changes_get(
    date: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """某一天的變化報告（沒給＝最新一份）。分頁數字＝items 的筆數（同一份計算）。"""
    import daily_digest

    try:
        cfg = daily_digest.load_settings(conn)
        out = daily_digest.diff_for(conn, date, cfg)
        import v2_views
        out["freshness"] = v2_views.changes_freshness(conn, out)   # 2.006.6：1.2 顯示「今日未收集／資料來源未更新」
        return out
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/changes/snapshot")
def changes_snapshot_now(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """立刻寫今天的快照（同一天重寫＝覆蓋）。排程每晚也會自己寫一次。"""
    import daily_digest

    return daily_digest.write_snapshot(conn, trigger="manual")


@router.get("/api/digest/settings")
def digest_settings_get(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import daily_digest

    try:
        cfg = daily_digest.load_settings(conn)
        corrupt = None
    except ValueError as exc:
        cfg, corrupt = dict(daily_digest.DEFAULTS), str(exc)
    return {"settings": cfg, "corrupt": corrupt, "missing": daily_digest.mail_missing(conn, cfg),
            "can_edit": _can_edit_nav(session, conn)}


@router.put("/api/digest/settings")
def digest_settings_put(
    body: DigestSettingsBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """存設定（限管理員）。缺必填卻要啟用 → 擋下並指名缺哪一項，
    不可以存成「開著但一封都不會寄」。"""
    import daily_digest

    _admin_only(session, conn)
    try:
        cur = daily_digest.load_settings(conn)
    except ValueError:
        cur = dict(daily_digest.DEFAULTS)   # 舊設定壞掉：這次存檔就是在修它
    cfg = {**cur, **{k: v for k, v in body.model_dump().items() if v is not None}}
    missing = daily_digest.mail_missing(conn, cfg)
    if cfg.get("mail_enabled") and missing:
        raise HTTPException(400, f"要啟用寄信就得先填：{'、'.join(missing)}")
    return {"ok": True, "settings": daily_digest.save_settings(conn, cfg), "missing": missing}


@router.get("/api/digest/preview")
def digest_preview(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """信的內容預覽（不寄）。"""
    import daily_digest

    try:
        subject, body = daily_digest.build_mail(conn)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"subject": subject, "body": body}


@router.post("/api/digest/test-mail")
def digest_test_mail(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """立即寄一封測試信給設定的收件人（限管理員）。記在寄送紀錄，kind=test，
    不佔掉當天排程那一封的位置。"""
    import daily_digest

    _admin_only(session, conn)
    return daily_digest.send(conn, "test")


@router.get("/api/digest/mail-log")
def digest_mail_log(
    limit: int = 100,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import daily_digest

    return {"items": daily_digest.mail_log(conn, min(max(limit, 1), 1000))}


@router.get("/api/oncall")
def oncall_get(
    q: str = "",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """值班一頁卡：IP／主機名／資產序號／APID。"""
    import oncall

    return oncall.lookup(conn, q)


# ===== ACT-03 Teams 單向通知（teams_notify.py）=====
# Workflow URL 視同密鑰：任何回應都不含它；稽核（activity）不記 request body，這裡也只記「改了/清了」。

class TeamsSettingsBody(BaseModel):
    enabled: bool | None = None
    min_severity: str | None = None
    send_recovery: bool | None = None
    send_incidents: bool | None = None
    summary_daily: bool | None = None
    summary_weekly: bool | None = None
    summary_time: str | None = None
    weekly_day: int | None = None
    quiet_start: str | None = None
    quiet_end: str | None = None
    max_attempts: int | None = None
    base_backoff_sec: int | None = None
    live_confirmed: bool | None = None
    dns_guard: bool | None = None
    base_url: str | None = None
    channel: str | None = None
    mail_to: str | None = None


class TeamsWebhookBody(BaseModel):
    url: str


class TeamsLiveTestBody(BaseModel):
    confirm: bool = False


def _teams_admin(session, conn) -> None:
    """Teams 通知的所有寫入：只有 admin；登入停用時 fail-closed（沒有真實身分就不准設網址／啟用對外／測試／重送）。"""
    import auth

    if auth_disabled(conn):
        raise HTTPException(403, "登入停用時不能設定或操作 Teams 通知（沒有真實身分可核對）；請先啟用登入")
    if not auth.is_admin(session):
        raise HTTPException(403, "只有管理員（admin 帳號）能改 Teams 通知設定")


def _teams_audit(conn, session, method: str, path: str, action: str, commit: bool = False) -> None:
    """稽核寫進同一交易（commit=False 由呼叫端一起提交）；寫不進去就丟例外讓整體回滾，不吞。內容不含網址。"""
    import activity

    activity.log(conn, username=session["username"] if session is not None else None, ip=None, method=method,
                 path=path, status=200, duration_ms=0, action=action, commit=commit)


class _teams_txn:
    """資料異動＋稽核同一交易：成功才 commit，任何例外（含 HTTPException）一律 rollback 再往上丟。"""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, et, ev, tb):
        if et is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        return False


@router.get("/api/teams-notify/settings")
def teams_settings_get(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import auth
    import teams_notify

    with teams_notify.read_only(conn):      # GET 路徑強制唯讀
        try:
            cfg, corrupt = teams_notify.public_settings(conn), None
        except teams_notify.SettingsCorrupt as exc:
            cfg, corrupt = {**teams_notify.DEFAULTS, "webhook_set": teams_notify.webhook_is_set(conn)}, str(exc)
        hold, queue = None, {}
        if corrupt is None:
            hold = teams_notify.hold_reason(conn, cfg) if cfg.get("enabled") else None
            if teams_notify._has(conn, "teams_notify_delivery"):
                queue = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM teams_notify_delivery WHERE kind NOT IN ('test','live_test') GROUP BY status")}
        host = teams_notify.webhook_host(conn) if (not auth_disabled(conn)) and auth.is_admin(session) else None     # 只回網域、只給 admin
        return {"settings": cfg, "corrupt": corrupt, "missing": teams_notify.missing(conn), "hold": hold, "queue": queue, "webhook_host": host,
                "can_edit": (not auth_disabled(conn)) and auth.is_admin(session), "auth_disabled": auth_disabled(conn)}


@router.put("/api/teams-notify/settings")
def teams_settings_put(
    body: TeamsSettingsBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """存設定（限管理員）。缺網址卻要啟用 → 擋下並指名，不存成「開著但一則都不會送」。"""
    import teams_notify

    _teams_admin(session, conn)
    try:
        cur = teams_notify.load_settings(conn)
    except teams_notify.SettingsCorrupt:
        cur = dict(teams_notify.DEFAULTS)
    cfg = {**cur, **{k: v for k, v in body.model_dump().items() if v is not None}}
    miss = teams_notify.missing(conn, cfg)
    if cfg.get("enabled") and miss:
        raise HTTPException(400, f"要啟用就得先填：{'、'.join(miss)}")
    with _teams_txn(conn):
        try:
            saved = teams_notify.save_settings(conn, cfg)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        _teams_audit(conn, session, "PUT", "/api/teams-notify/settings",
                     f"改 Teams 通知設定（啟用={saved['enabled']}、門檻={saved['min_severity']}、對外確認={saved['live_confirmed']}、DNS內部位址檢查={saved['dns_guard']}）")
        if cur.get("dns_guard", True) != saved["dns_guard"]:
            _teams_audit(conn, session, "PUT", "/api/teams-notify/settings",
                         f"{'開啟' if saved['dns_guard'] else '關閉'} Teams 通知的 DNS 內部位址檢查（SSRF 防線）")
    return {"ok": True, "settings": teams_notify.public_settings(conn), "missing": miss}


@router.put("/api/teams-notify/webhook")
def teams_webhook_put(
    body: TeamsWebhookBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """設定 Workflow 網址（密鑰，加密存放，之後任何地方都不會再顯示）。"""
    import teams_notify

    _teams_admin(session, conn)
    with _teams_txn(conn):
        try:
            teams_notify.set_webhook(conn, body.url.strip(" "))
        except teams_notify.WebhookError as exc:
            raise HTTPException(400, str(exc))
        _teams_audit(conn, session, "PUT", "/api/teams-notify/webhook", "設定 Teams Workflow 網址（內容不記錄）")
    return {"ok": True, "webhook_set": True}


@router.delete("/api/teams-notify/webhook")
def teams_webhook_delete(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import teams_notify

    _teams_admin(session, conn)
    with _teams_txn(conn):
        teams_notify.clear_webhook(conn)
        _teams_audit(conn, session, "DELETE", "/api/teams-notify/webhook", "清除 Teams Workflow 網址")
    return {"ok": True, "webhook_set": False}


@router.get("/api/teams-notify/preview")
def teams_preview(
    kind: str = "alert",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """預覽卡片內容（dry_run）：不送、不寫任何表。kind=alert|recovery|daily|weekly。"""
    import teams_notify

    if kind not in ("alert", "recovery", "daily", "weekly"):
        raise HTTPException(400, "kind 只能是 alert／recovery／daily／weekly")
    with teams_notify.read_only(conn):      # dry_run：連線層就是唯讀，任何寫入都會被 SQLite 擋下
        try:
            return teams_notify.preview(conn, kind)
        except teams_notify.SettingsCorrupt as exc:
            raise HTTPException(400, str(exc))


@router.post("/api/teams-notify/test")
def teams_test_send(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """測試送出（限管理員）：只對本機模擬接收端可用；真實 Teams 網址一律拒絕。"""
    import teams_notify

    _teams_admin(session, conn)
    r = None
    try:
        r = teams_notify.send_test(conn)           # 不 commit
        _teams_audit(conn, session, "POST", "/api/teams-notify/test",
                     f"Teams 測試送出（本機模擬）：{'成功' if r.get('ok') else '未成功'}")
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if r is not None and r.get("sent_out"):
            teams_notify.log.error("Teams 測試訊息已送到接收端，但投遞紀錄／稽核寫入失敗並已回滾：%s", type(exc).__name__)
            raise HTTPException(500, f"測試訊息已經送出（接收端可能已收到），但投遞紀錄與稽核寫入失敗（已回滾）：{type(exc).__name__}。請勿以為沒送。")
        raise
    return r


def _live_send(conn, session, path: str, what: str, card=None, title=None, on_begun=None) -> dict:
    """人為主動真實送出的共用三段式（送測試訊息、磁碟告警範例）：
    (a) 短交易：驗證＋插入 live_test 列（60 秒限流依據）＋稽核「開始」→ 先 commit；
    (b) 交易外送出網路請求（不持有 SQLite 寫入鎖）；
    (c) 短交易：寫回結果＋稽核「結果」→ commit。(c) 失敗時該列仍留在紀錄（顯示「結果未記錄（可能已送達）」），並如實回報。
    on_begun(conn, key)：(a) 同一交易內額外要記的東西（例如磁碟告警歷史）。"""
    import teams_notify

    try:
        b1 = teams_notify.live_test_begin(conn, card=card, title=title)
        if b1["outcome"] != "refused":
            _teams_audit(conn, session, "POST", path, f"開始送{what}（限流計時起點）")
            if on_begun:
                on_begun(conn, b1["key"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if b1["outcome"] == "refused":
        if b1["code"] == "rate_limited":
            raise HTTPException(429, b1["reason"], headers={"Retry-After": str(b1.get("retry_after", 60))})
        raise HTTPException(409, b1["reason"])
    result = teams_notify.live_test_execute(conn, b1["key"])          # (b) 交易外
    try:
        r = teams_notify.live_test_finish(conn, b1["key"], b1["token"], result)
        label = {"sent": "成功", "uncertain": "不確定（可能已送達）", "failed": "失敗"}[r["outcome"]]
        _teams_audit(conn, session, "POST", path, f"送{what}結果：{label}（HTTP {r['http'] if r['http'] is not None else '無回應'}）")
        conn.commit()
    except Exception as exc:
        conn.rollback()
        teams_notify.log.error("Teams 真實測試訊息已送出，但結果紀錄／稽核寫入失敗並已回滾（%s）；該列保留為「結果未記錄」", type(exc).__name__)
        raise HTTPException(500, f"測試訊息已經送出（接收端可能已收到），但結果紀錄與稽核寫入失敗（已回滾）：{type(exc).__name__}。"
                                 "投遞紀錄會把這筆顯示為「結果未記錄（可能已送達）」，請到 Teams 頻道確認。")
    return {k: r[k] for k in ("ok", "outcome", "http", "reason")}


@router.post("/api/teams-notify/test-live")
def teams_live_test(
    body: TeamsLiveTestBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """人為主動「送測試訊息到已設定的網址」（限管理員，真的會送到 Teams 頻道）。三段式見 _live_send。"""
    _teams_admin(session, conn)
    if not body.confirm:
        raise HTTPException(400, "需要明確確認（confirm=true）：這會真的送一則測試訊息到 Teams 頻道")
    return _live_send(conn, session, "/api/teams-notify/test-live", "真實 Teams 測試訊息")


# ===== WhatsUp 磁碟使用率 → Teams（disk_alert.py）=====
class DiskAlertSettingsBody(BaseModel):
    enabled: bool | None = None
    threshold: float | None = None


@router.get("/api/teams-notify/disk-alert")
def disk_alert_get(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """設定＋最近 200 筆告警歷史（唯讀）。"""
    import auth
    import disk_alert
    import teams_notify

    with teams_notify.read_only(conn):
        try:
            cfg, corrupt = disk_alert.load_settings(conn), None
        except ValueError as exc:
            cfg, corrupt = dict(disk_alert.DEFAULTS), str(exc)
        try:
            teams_on = teams_notify.load_settings(conn)["enabled"]
        except teams_notify.SettingsCorrupt:
            teams_on = False
        has_data = disk_alert._has(conn, "relay_disk_util") and conn.execute("SELECT 1 FROM relay_disk_util LIMIT 1").fetchone() is not None
        latest = conn.execute("SELECT MAX(day) FROM relay_disk_util").fetchone()[0] if has_data else None
        return {"settings": cfg, "corrupt": corrupt, "teams_enabled": teams_on, "latest_day": latest,
                "history": disk_alert.history(conn), "can_edit": (not auth_disabled(conn)) and auth.is_admin(session)}


@router.put("/api/teams-notify/disk-alert/settings")
def disk_alert_put(
    body: DiskAlertSettingsBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import disk_alert

    _teams_admin(session, conn)
    try:
        cur = disk_alert.load_settings(conn)
    except ValueError:
        cur = dict(disk_alert.DEFAULTS)
    cfg = {**cur, **{k: v for k, v in body.model_dump().items() if v is not None}}
    with _teams_txn(conn):
        try:
            saved = disk_alert.save_settings(conn, cfg)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        _teams_audit(conn, session, "PUT", "/api/teams-notify/disk-alert/settings",
                     f"改 WhatsUp 磁碟告警設定（啟用={saved['enabled']}、門檻={saved['threshold']}%）")
    return {"ok": True, "settings": saved}


@router.post("/api/teams-notify/disk-alert/sample")
def disk_alert_sample(
    body: TeamsLiveTestBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """「用最近一筆資料送範例」（限管理員，真的會送到 Teams 頻道）：用最近一天匯入的 WhatsUp 磁碟資料組出真實格式的訊息，
    標題明確寫【測試】、不套門檻、不記去重。送出條件與限流同「送測試訊息」（共用 60 秒限流）。"""
    import disk_alert
    import teams_notify

    _teams_admin(session, conn)
    if not body.confirm:
        raise HTTPException(400, "需要明確確認（confirm=true）：這會真的送一則訊息到 Teams 頻道")
    built = disk_alert.sample_card(conn)
    if built is None:
        raise HTTPException(409, "還沒有匯入過 WhatsUp 磁碟資料，無法組範例")
    card, title = built

    def remember(c, key):
        disk_alert.ensure(c)
        lines = [b["text"] for b in card["attachments"][0]["content"]["body"][2:]]
        disk_alert._history(c, teams_notify._ts(teams_notify.now_taipei()), "sample", None, None,
                            len(lines), len(lines), "sample", key, "\n".join(lines))

    return _live_send(conn, session, "/api/teams-notify/disk-alert/sample", "WhatsUp 磁碟告警範例", card=card, title=title, on_begun=remember)


@router.get("/api/teams-notify/log")
def teams_log(
    limit: int = 200,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import teams_notify

    with teams_notify.read_only(conn):
        return {"items": teams_notify.delivery_log(conn, min(max(limit, 1), 1000))}


@router.post("/api/teams-notify/log/{delivery_id}/retry")
def teams_retry(
    delivery_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import teams_notify

    _teams_admin(session, conn)
    with _teams_txn(conn):
        info = teams_notify.retry_dead(conn, delivery_id)
        if info is None:
            raise HTTPException(409, "只有「放棄」「過期未送」「不確定」的列能重送")
        _teams_audit(conn, session, "POST", f"/api/teams-notify/log/{delivery_id}/retry",
                     f"Teams 通知重送（{info['event_key']}，原狀態 {info['status']}，連帶重新排隊 {info['dependents']} 則恢復／結案）")
    return {"ok": True}
