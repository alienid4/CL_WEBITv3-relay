"""夜間速查改版（2026-11-15 夜間衝刺）的 API。邏輯在 night_desk.py／night_baseline.py，這裡只接線。

  GET  /api/v2/night/notified?hours=12           今晚 Teams 真的通知過的（登入即可看）
  GET  /api/v2/night/host?key=<machine_key 或 IP>  緊急檢查彙整（只讀、不連主機；登入即可看）
  POST /api/v2/night/check-snapshot               即時檢測後存快照並回跟平常比（管理員，跟即時檢測同級；登入停用 403）
  POST /api/v2/night/handle                       我在處理（一般帳號以上；登入停用 403）
  POST /api/v2/night/handle/{handling_id}/result  寫處理結果＝結案（只能一次；一般帳號以上；登入停用 403）

即時檢測本身沿用既有 POST /api/health-check/run（管理員），這裡不連任何主機。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["v2_night_notified_endpoint", "v2_night_host_endpoint", "v2_night_snapshot_endpoint",
           "v2_night_handle_endpoint", "v2_night_handle_result_endpoint"]


def _who_writes(conn, session, request: Request, admin: bool = False) -> str:
    """寫入要真實身分：登入停用＝403（要記是誰做的），只能看的帳號＝403；再過 write_guard。"""
    import auth
    from routers.overlaps import _guard
    if auth_disabled(conn) or session is None:
        raise HTTPException(403, "登入停用時不能留紀錄（要記是誰做的）")
    if auth.session_level(session) not in ("user", "admin"):
        raise HTTPException(403, "只能看的帳號不能做這個動作")
    if admin and auth.session_level(session) != "admin":
        raise HTTPException(403, "這個動作要管理員（跟即時檢測同級）")
    _guard(conn, request)
    return session["username"]


@router.get("/api/v2/night/notified")
def v2_night_notified_endpoint(hours: int | None = None, session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    import night_desk
    if hours is not None and not 1 <= hours <= night_desk.MAX_HOURS:
        raise HTTPException(400, f"hours 要是 1～{night_desk.MAX_HOURS}")
    return night_desk.notified(conn, hours)


@router.get("/api/v2/night/host")
def v2_night_host_endpoint(key: str = "", hours: int | None = None, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    import night_desk
    k = (key or "").strip()
    if not k:
        raise HTTPException(400, "要給 key（machine_key 或 IP）")
    if hours is not None and not 1 <= hours <= night_desk.MAX_HOURS:
        raise HTTPException(400, f"hours 要是 1～{night_desk.MAX_HOURS}")
    return night_desk.host(conn, k, hours=hours)


class NightSnapshotBody(BaseModel):
    key: str
    result: dict


@router.post("/api/v2/night/check-snapshot")
def v2_night_snapshot_endpoint(body: NightSnapshotBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    import night_desk
    who = _who_writes(conn, session, request, admin=True)
    try:
        out = night_desk.save_snapshot(conn, body.key, body.result, who)
    except LookupError as exc:
        conn.rollback()
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc))
    activity.log(conn, username=who, ip=activity.client_ip(request), method="NIGHT", status=200, action="night_check_snapshot",
                 path=f"夜間緊急檢查快照：{body.key}"[:280], commit=False)
    conn.commit()
    return out


class NightHandleBody(BaseModel):
    item_id: str
    key: str | None = None


class NightResultBody(BaseModel):
    result: str


@router.post("/api/v2/night/handle")
def v2_night_handle_endpoint(body: NightHandleBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    import night_desk
    who = _who_writes(conn, session, request)
    try:
        row = night_desk.start_handling(conn, body.item_id, body.key or "", who)
    except LookupError as exc:
        conn.rollback()
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc))
    if not row.get("existing"):
        activity.log(conn, username=who, ip=activity.client_ip(request), method="NIGHT", status=200, action="night_handle",
                     path=f"夜間我在處理：{body.item_id}"[:280], commit=False)
    conn.commit()
    return {"id": row["id"], "by": row["by"], "at": row["at"], "existing": row["existing"], "item_id": row["item_id"]}


@router.post("/api/v2/night/handle/{handling_id}/result")
def v2_night_handle_result_endpoint(handling_id: int, body: NightResultBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                    conn: sqlite3.Connection = Depends(get_db)):
    import night_desk
    who = _who_writes(conn, session, request)
    try:
        row = night_desk.finish_handling(conn, handling_id, body.result, who)
    except LookupError as exc:
        conn.rollback()
        raise HTTPException(404, str(exc))
    except PermissionError as exc:
        conn.rollback()
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc))
    activity.log(conn, username=who, ip=activity.client_ip(request), method="NIGHT", status=200, action="night_handle_result",
                 path=f"夜間處理結案：#{handling_id}"[:280], commit=False)
    conn.commit()
    return {"id": row["id"], "result": row["result"], "done_by": row["done_by"], "done_at": row["done_at"]}
