"""Q91：權限等級管理與下載紀錄（系統設定頁用）。全部限管理員（等級在 access_policy 登記）。"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

import access_control as ac

router = _RouteCollector()

__all__ = ["access_users_get", "access_user_level_put", "access_user_create", "access_export_log_get", "access_level_audit_get"]


@router.get("/api/access/users")
def access_users_get(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """帳號與等級清單（管理員）。levels＝可選的等級。"""
    return {"items": ac.list_users(conn),
            "levels": [{"value": v, "label": auth.LEVEL_LABEL[v]} for v in reversed(auth.LEVELS)]}


class LevelBody(BaseModel):
    level: str


@router.put("/api/access/users/{username}/level")
def access_user_level_put(username: str, body: LevelBody, request: Request,
                          session: sqlite3.Row = Depends(require_auth),
                          conn: sqlite3.Connection = Depends(get_db)):
    """改某帳號的等級（管理員）。每次異動寫 access_level_audit。"""
    try:
        return ac.set_level(conn, username, body.level, session["username"], activity.client_ip(request))
    except ac.LevelError as exc:
        raise HTTPException(400, str(exc)) from exc


class CreateUserBody(BaseModel):
    username: str
    display_name: str | None = None
    level: str = "user"
    password: str


@router.post("/api/access/users")
def access_user_create(body: CreateUserBody, request: Request,
                       session: sqlite3.Row = Depends(require_auth),
                       conn: sqlite3.Connection = Depends(get_db)):
    """新增帳號（管理員；只能建 user／viewer）。回應與紀錄不含密碼。"""
    ip = activity.client_ip(request)
    try:
        res = ac.create_account(conn, body.username, body.display_name, body.level, body.password,
                                session["username"], ip)
    except ac.LevelError as exc:
        raise HTTPException(400, str(exc)) from exc
    activity.log(conn, username=session["username"], ip=ip, method="POST",
                 path=f"/api/access/users 建立 {res['username']}（{res['level']}）", status=200,
                 action="create_user")
    return res


@router.get("/api/access/export-log")
def access_export_log_get(limit: int = 1000, session: sqlite3.Row = Depends(require_auth),
                          conn: sqlite3.Connection = Depends(get_db)):
    """下載／匯出紀錄（唯讀、只增不改）。"""
    return {"items": ac.list_export_log(conn, limit)}


@router.get("/api/access/level-audit")
def access_level_audit_get(limit: int = 500, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    """權限等級異動紀錄（唯讀、只增不改）。"""
    return {"items": ac.list_level_audit(conn, limit)}
