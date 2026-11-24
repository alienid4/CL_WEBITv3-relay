"""外部系統整合的 API：Aria Operations（Q92）與 WhatsUp Gold（Q93）。

## 閘門（兩個模組各一組，順序固定）

  讀取：登入（require_auth，沒登入 401）→ 模組開關（預設關，關著＝403，連網址直接打都擋）
  寫入／測試／執行：再加「登入必須是啟用的」與「必須是 admin」。登入停用時一律拒絕（fail-closed）——
  這兩個整合會用到外部系統的帳密，不能在沒有身分的狀態下被操作。

AD 人員名單（LDAPS）：讀取也要 admin（設定含 DC 與服務帳號、紀錄是全體員工名單的統計）。

## 機密

沒有任何端點會回傳密碼。整合設定**不再收帳密**（2026-10-30）：帳密統一在「系統設定 → 收集憑證」設定
（kind=whatsup／vrops），這裡只挑「用哪一筆」的名稱。
"""
from __future__ import annotations

from typing import Any

from fastapi import Body
from pydantic import ConfigDict

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

import ad_ldap_sync as ads
import aria_dailycheck_direct as add
import integration_http as ih
import vrops_import as vi
import whatsup_service as wu

router = _RouteCollector()

__all__ = [
    "vrops_read_gate", "vrops_write_gate", "vrops_config_get", "vrops_config_put", "vrops_test", "vrops_run", "vrops_runs",
    "vrops_dc_get", "vrops_dc_put", "vrops_dc_run",
    "whatsup_read_gate", "whatsup_write_gate", "whatsup_config_get", "whatsup_config_put", "whatsup_test", "whatsup_poll",
    "whatsup_alerts", "whatsup_history", "whatsup_runs", "night_alerts_endpoint", "night_alerts_settings_endpoint",
    "ad_gate", "ad_config_get", "ad_config_put", "ad_test", "ad_run", "ad_runs",
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


@router.post("/api/integrations/vrops/test", dependencies=[Depends(__import__("quiet_deps").gate("manual_diag"))])
def vrops_test(user: str = Depends(vrops_write_gate), conn: sqlite3.Connection = Depends(get_db)):
    return vi.check_connection(conn)


@router.post("/api/integrations/vrops/run")
def vrops_run(request: Request, confirm_in_window: bool = False, user: str = Depends(vrops_write_gate),
              conn: sqlite3.Connection = Depends(get_db)):
    import quiet_window

    tk = quiet_window.confirm_from_request(conn, "vrops_import_tick", confirm_in_window, user, request)   # AUD-02
    return vi.run_import(conn, "manual", user, quiet_ticket=tk)


@router.get("/api/integrations/vrops/runs")
def vrops_runs(_g: None = Depends(vrops_read_gate), conn: sqlite3.Connection = Depends(get_db)):
    return {"runs": [{**r, "__no": r["id"]} for r in vi.list_runs(conn)]}


class VropsDcBody(BaseModel):
    enabled: bool = False


@router.get("/api/integrations/vrops/dailycheck")
def vrops_dc_get(_g: None = Depends(vrops_read_gate), conn: sqlite3.Connection = Depends(get_db)):
    """每日 VMware 快照（系統直抓）：開關、最近一次嘗試、最後一次成功、今天有沒有完整批次。只讀本機資料，不連 vROps。"""
    return add.status(conn)


@router.put("/api/integrations/vrops/dailycheck")
def vrops_dc_put(body: VropsDcBody, user: str = Depends(vrops_write_gate), conn: sqlite3.Connection = Depends(get_db)):
    if body.enabled and not add.status(conn)["configured"]:
        raise HTTPException(400, "要開啟每日自動抓取，請先在上面填 API 網址並選好收集憑證")
    add.save_enabled(conn, body.enabled, user)
    return add.status(conn)


@router.post("/api/integrations/vrops/dailycheck/run")
def vrops_dc_run(request: Request, confirm_in_window: bool = False, user: str = Depends(vrops_write_gate),
                 conn: sqlite3.Connection = Depends(get_db)):
    import quiet_window

    tk = quiet_window.confirm_from_request(conn, "aria_dailycheck_direct_tick", confirm_in_window, user, request)   # AUD-02 盤中二次確認
    return add.start_manual(conn, user, tk)


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


@router.post("/api/integrations/whatsup/test", dependencies=[Depends(__import__("quiet_deps").gate("manual_diag"))])
def whatsup_test(user: str = Depends(whatsup_write_gate), conn: sqlite3.Connection = Depends(get_db)):
    return wu.check_connection(conn)


@router.post("/api/integrations/whatsup/poll")
def whatsup_poll(request: Request, confirm_in_window: bool = False, user: str = Depends(whatsup_write_gate),
                 conn: sqlite3.Connection = Depends(get_db)):
    import quiet_window

    tk = quiet_window.confirm_from_request(conn, "whatsup_poll_tick", confirm_in_window, user, request)   # AUD-02
    return wu.poll(conn, "manual", user, quiet_ticket=tk)


@router.get("/api/integrations/whatsup/alerts")
def whatsup_alerts(_g: None = Depends(whatsup_read_gate), conn: sqlite3.Connection = Depends(get_db)):
    return wu.current_alerts(conn)


@router.get("/api/integrations/whatsup/history")
def whatsup_history(_g: None = Depends(whatsup_read_gate), conn: sqlite3.Connection = Depends(get_db)):
    return {"rows": wu.history(conn)}


@router.get("/api/integrations/whatsup/runs")
def whatsup_runs(_g: None = Depends(whatsup_read_gate), conn: sqlite3.Connection = Depends(get_db)):
    return {"rows": wu.runs(conn)}


# ---------------------------------------------------------------- AD 人員名單（LDAPS 每日同步，2.068.0）
# 讀取也只給 admin：設定裡有 DC 位址與服務帳號，執行紀錄是全體員工名單的筆數與主管對照統計。
def ad_gate(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)) -> str:
    user = _write_gate(session, conn, ads.module_enabled(conn), "AD 人員名單同步")
    ads.ensure_tables(conn)
    return user


_AD_FIELDS = {"hosts", "base_dn", "ca_bundle", "enabled", "run_at", "credential", "port"}


def _ad_view(conn) -> dict:
    import ad_import
    runs = ads.list_runs(conn, 1)
    return {**ads.get_config(conn), "freshness": ads.freshness(conn), "last_run": runs[0] if runs else None,
            "list": ad_import.summary(conn), "config_audit": ads.config_audit(conn, 20)}


@router.get("/api/integrations/ad")
def ad_config_get(user: str = Depends(ad_gate), conn: sqlite3.Connection = Depends(get_db)):
    return _ad_view(conn)


@router.put("/api/integrations/ad")
def ad_config_put(body: Any = Body(default=None), user: str = Depends(ad_gate), conn: sqlite3.Connection = Depends(get_db)):
    """body 刻意收 Any、自己驗：型別錯時不能讓 FastAPI 回 422（422 會把送來的原始值回顯）。錯誤訊息一律固定文字。"""
    if not isinstance(body, dict):
        raise HTTPException(400, "內容要是 JSON 物件")
    if any(k in body for k in ("username", "password", "token", "secret")):
        raise HTTPException(400, "這裡不收帳號密碼；請到 系統設定 → 收集憑證 設定，再回來挑選憑證名稱")
    unknown = sorted(k for k in body if k not in _AD_FIELDS)
    if unknown:
        raise HTTPException(400, "有不認得的欄位")
    try:
        ads.save_config(conn, hosts=body.get("hosts", ""), base_dn=body.get("base_dn", ""), ca_bundle=body.get("ca_bundle", ""),
                        enabled=body.get("enabled", False), run_at=body.get("run_at", ads.DEFAULT_RUN_AT),
                        credential=body.get("credential"), port=body.get("port"), actor=user)
    except ih.ConfigError as exc:
        raise HTTPException(400, str(exc)) from None
    return _ad_view(conn)


@router.post("/api/integrations/ad/test", dependencies=[Depends(__import__("quiet_deps").gate("manual_diag"))])
def ad_test(user: str = Depends(ad_gate), conn: sqlite3.Connection = Depends(get_db)):
    """繫結＋數人數，不寫任何名單。"""
    return ads.check_connection(conn)


@router.post("/api/integrations/ad/run")
def ad_run(request: Request, confirm_in_window: bool = False, user: str = Depends(ad_gate),
           conn: sqlite3.Connection = Depends(get_db)):
    import quiet_window

    tk = quiet_window.confirm_from_request(conn, "ad_ldap_tick", confirm_in_window, user, request)   # AUD-02 盤中二次確認
    return ads.run_sync(conn, "manual", user, quiet_ticket=tk)


@router.get("/api/integrations/ad/runs")
def ad_runs(user: str = Depends(ad_gate), conn: sqlite3.Connection = Depends(get_db)):
    return {"runs": [{**r, "__no": r["id"]} for r in ads.list_runs(conn)]}


# ---------------------------------------------------------------- 夜間速查「目前告警中」（AUD-03）
@router.get("/api/night/alerts")
def night_alerts_endpoint(scope: str = "policy", session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """純讀本機快取，不寫庫、不對外連線。登入即可看。模組沒啟用不是 403——要回「未取得」，畫面才講得出來。
    4.1 告警事件（中繼）有啟用時＝跟 1.1 今日概況同一份（scope 同首頁：policy＝Teams 通知範圍，all＝全部）。
    can_refresh：這個帳號按「更新」會不會成功（只有舊的 WhatsUp API 輪詢來源才有；既有輪詢端點只收 admin，這裡不放寬）。"""
    import night_alerts as na
    out = na.build(conn, scope="all" if scope == "all" else "policy")
    relay = out["source"].get("kind") == "relay"
    if not relay:
        # 2.044.2（R15）：每列帶告警聯絡五欄（對到資產才查得到 APID；對不到的五欄寫「未提供」）。中繼那一份首頁已經掛好，不重掛
        import alert_contact
        by_mk = alert_contact.api_ids_by_machine(conn) if out["rows"] else {}
        for r in out["rows"]:
            r["api_ids"] = by_mk.get(r.get("machine_key") or "", [])
        alert_contact.attach(conn, out["rows"])
    out["can_refresh"] = bool(not relay and out["source"]["enabled"] and not auth_disabled(conn) and session is not None and auth.is_admin(session))
    out["can_edit_settings"] = bool(not relay and not auth_disabled(conn) and session is not None and auth.is_admin(session))  # 中繼那一份的新鮮度＝4.1 心跳設定
    return out


class NightAlertSettingsBody(BaseModel):
    fresh_min: int


@router.put("/api/night/alerts/settings")
def night_alerts_settings_endpoint(body: NightAlertSettingsBody, session: sqlite3.Row = Depends(require_auth),
                                   conn: sqlite3.Connection = Depends(get_db)):
    import daily_risk
    import night_alerts as na
    if auth_disabled(conn) or session is None or not auth.is_admin(session):
        raise HTTPException(403, "只有 admin 帳號能修改")
    before = {"fresh_min": na.fresh_minutes(conn)}
    try:
        na.save_fresh_minutes(conn, body.fresh_min)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    daily_risk._audit(conn, "night_setting", "setting", na.K_FRESH, before, {"fresh_min": body.fresh_min}, session["username"])
    conn.commit()
    return {"fresh_min": na.fresh_minutes(conn)}
