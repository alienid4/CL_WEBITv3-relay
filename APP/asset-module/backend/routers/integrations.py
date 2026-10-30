"""外部系統整合的 API：Aria Operations（Q92）與 WhatsUp Gold（Q93）。

## 閘門（兩個模組各一組，順序固定）

  讀取：登入（require_auth，沒登入 401）→ 模組開關（預設關，關著＝403，連網址直接打都擋）
  寫入／測試／執行：再加「登入必須是啟用的」與「必須是 admin」。登入停用時一律拒絕（fail-closed）——
  這兩個整合會用到外部系統的帳密，不能在沒有身分的狀態下被操作。

## 機密

沒有任何端點會回傳密碼。整合設定**不再收帳密**（2026-10-30）：帳密統一在「系統設定 → 收集憑證」設定
（kind=whatsup／vrops），這裡只挑「用哪一筆」的名稱。
"""
from __future__ import annotations

from pydantic import ConfigDict

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

import integration_http as ih
import vrops_import as vi
import whatsup_service as wu

router = _RouteCollector()

__all__ = [
    "vrops_read_gate", "vrops_write_gate", "vrops_config_get", "vrops_config_put", "vrops_test", "vrops_run", "vrops_runs",
    "whatsup_read_gate", "whatsup_write_gate", "whatsup_config_get", "whatsup_config_put", "whatsup_test", "whatsup_poll",
    "whatsup_alerts", "whatsup_history", "whatsup_runs",
]


def _read_gate(conn, enabled: bool, label: str) -> None:
    if not enabled:
        raise HTTPException(403, f"{label}模組未啟用")


def _write_gate(session, conn, enabled: bool, label: str) -> str:
    _read_gate(conn, enabled, label)
    if auth_disabled(conn):
        raise HTTPException(403, f"登入停用中，{label}不能設定或執行。請先啟用登入，再以 admin 帳號操作")
    if session is None or not auth.is_admin(session):
        raise HTTPException(403, f"只有 admin 帳號能設定或執行{label}")
    return session["username"]


# ---------------------------------------------------------------- Aria Operations（Q92）
def vrops_read_gate(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)) -> None:
    _read_gate(conn, vi.module_enabled(conn), "Aria Operations 匯入")
    vi.ensure_tables(conn)


def vrops_write_gate(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)) -> str:
    user = _write_gate(session, conn, vi.module_enabled(conn), "Aria Operations 匯入")
    vi.ensure_tables(conn)
    return user



def _reject_legacy_secret_fields(body: BaseModel) -> None:
    """舊 client 還在送 username／password：明確 400（不是靜默 200 而密碼其實沒更新），而且訊息不回顯內容。"""
    extra = body.model_extra or {}
    if any(k in extra for k in ("username", "password", "token", "secret")):
        raise HTTPException(400, "這裡不再收帳號密碼；請到 系統設定 → 收集憑證 設定，再回來挑選憑證名稱")


class VropsConfigBody(BaseModel):
    model_config = ConfigDict(extra="allow")      # 舊版會多送 username/password：下面明確拒絕（不能用 422，它會把送來的密碼回顯）
    url: str = ""
    auth_source: str = ""
    ca_bundle: str = ""
    enabled: bool = False
    interval_hours: int = vi.DEFAULT_INTERVAL_HOURS
    credential: str | None = None     # 收集憑證的名稱；None＝沿用目前選擇


@router.get("/api/integrations/vrops")
def vrops_config_get(_g: None = Depends(vrops_read_gate), conn: sqlite3.Connection = Depends(get_db)):
    runs = vi.list_runs(conn, 1)
    return {**vi.get_config(conn), "last_run": runs[0] if runs else None}


@router.put("/api/integrations/vrops")
def vrops_config_put(body: VropsConfigBody, user: str = Depends(vrops_write_gate), conn: sqlite3.Connection = Depends(get_db)):
    _reject_legacy_secret_fields(body)
    try:
        vi.save_config(conn, url=body.url, auth_source=body.auth_source, ca_bundle=body.ca_bundle, enabled=body.enabled,
                       interval_hours=body.interval_hours, credential=body.credential)
    except ih.ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    return vi.get_config(conn)


@router.post("/api/integrations/vrops/test")
def vrops_test(user: str = Depends(vrops_write_gate), conn: sqlite3.Connection = Depends(get_db)):
    return vi.check_connection(conn)


@router.post("/api/integrations/vrops/run")
def vrops_run(user: str = Depends(vrops_write_gate), conn: sqlite3.Connection = Depends(get_db)):
    return vi.run_import(conn, "manual", user)


@router.get("/api/integrations/vrops/runs")
def vrops_runs(_g: None = Depends(vrops_read_gate), conn: sqlite3.Connection = Depends(get_db)):
    return {"runs": [{**r, "__no": r["id"]} for r in vi.list_runs(conn)]}


# ---------------------------------------------------------------- WhatsUp Gold（Q93）
def whatsup_read_gate(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)) -> None:
    _read_gate(conn, wu.module_enabled(conn), "WhatsUp Gold 告警")
    wu.ensure_tables(conn)


def whatsup_write_gate(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)) -> str:
    user = _write_gate(session, conn, wu.module_enabled(conn), "WhatsUp Gold 告警")
    wu.ensure_tables(conn)
    return user


class WhatsupConfigBody(BaseModel):
    model_config = ConfigDict(extra="allow")      # 舊版會多送 username/password：下面明確拒絕（不能用 422，它會把送來的密碼回顯）
    url: str = ""
    ca_bundle: str = ""
    enabled: bool = False
    interval_min: int = wu.DEFAULT_INTERVAL_MIN
    credential: str | None = None     # 收集憑證的名稱；None＝沿用目前選擇


@router.get("/api/integrations/whatsup")
def whatsup_config_get(_g: None = Depends(whatsup_read_gate), conn: sqlite3.Connection = Depends(get_db)):
    return {**wu.get_config(conn), "last_run": wu.last_run(conn)}


@router.put("/api/integrations/whatsup")
def whatsup_config_put(body: WhatsupConfigBody, user: str = Depends(whatsup_write_gate), conn: sqlite3.Connection = Depends(get_db)):
    _reject_legacy_secret_fields(body)
    try:
        wu.save_config(conn, url=body.url, ca_bundle=body.ca_bundle, enabled=body.enabled, interval_min=body.interval_min,
                       credential=body.credential)
    except ih.ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    return wu.get_config(conn)


@router.post("/api/integrations/whatsup/test")
def whatsup_test(user: str = Depends(whatsup_write_gate), conn: sqlite3.Connection = Depends(get_db)):
    return wu.check_connection(conn)


@router.post("/api/integrations/whatsup/poll")
def whatsup_poll(user: str = Depends(whatsup_write_gate), conn: sqlite3.Connection = Depends(get_db)):
    return wu.poll(conn, "manual", user)


@router.get("/api/integrations/whatsup/alerts")
def whatsup_alerts(_g: None = Depends(whatsup_read_gate), conn: sqlite3.Connection = Depends(get_db)):
    return wu.current_alerts(conn)


@router.get("/api/integrations/whatsup/history")
def whatsup_history(_g: None = Depends(whatsup_read_gate), conn: sqlite3.Connection = Depends(get_db)):
    return {"rows": wu.history(conn)}


@router.get("/api/integrations/whatsup/runs")
def whatsup_runs(_g: None = Depends(whatsup_read_gate), conn: sqlite3.Connection = Depends(get_db)):
    return {"rows": wu.runs(conn)}
