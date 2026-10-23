"""每日變化報告、早上摘要信、值班一頁卡的 API（v1.500.0）。

照 routers/ 的拆法用 _RouteCollector：api.py 只多一個模組名。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["changes_get", "changes_snapshot_now", "digest_settings_get", "digest_settings_put",
           "digest_preview", "digest_test_mail", "digest_mail_log", "oncall_get"]


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
        return daily_digest.diff_for(conn, date, cfg)
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
