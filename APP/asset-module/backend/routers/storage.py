"""儲存設備（Storage）API 設定、連線測試（批 S1）與人為資料收集（批 S2）（目前只支援 Dell Unity）。

## 閘門（與 integrations.py 同一套，順序固定）

  讀取：登入（require_auth）→ 模組開關 storage_api（預設關，關著＝403）
  寫入／測試：再加「登入必須是啟用的」與「必須是 admin」；登入停用時一律拒絕（fail-closed）。

連線測試是**人為按鈕**：盤中靜默窗（平日 08:30–15:00）內要先確認（manual_diag 入口，回 409 → 畫面跳確認 → 帶 confirm_in_window=true）。
資料收集（S2）同樣只由人按按鈕觸發、同一道窗內確認；這裡沒有任何排程或自動呼叫。
每日健康／告警檢查（S3）的排程在 storage_health.tick（scan_service 呼叫、走靜默窗入口 storage_health_tick）；這裡只有唯讀明細，
與 admin 的「立即檢查」人為按鈕（窗內同樣要二次確認，只發 GET，不送任何通知）。

## 機密

沒有任何端點收或回傳密碼。設定只存收集憑證（kind=storage）的「名稱」。
"""
from __future__ import annotations

from pydantic import ConfigDict

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

import integration_http as ih
import quiet_window
import storage_collect as sc
import storage_health as sh
import storage_unity as su

router = _RouteCollector()

__all__ = ["storage_read_gate", "storage_write_gate", "storage_devices_list", "storage_device_create", "storage_device_update",
           "storage_device_delete", "storage_device_test", "storage_device_collect", "storage_device_collected",
           "storage_device_health", "storage_device_health_check"]


def storage_read_gate(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)) -> None:
    if not su.module_enabled(conn):
        raise HTTPException(403, "儲存設備 API 模組未啟用")
    su.ensure_tables(conn)


def storage_write_gate(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)) -> str:
    if not su.module_enabled(conn):
        raise HTTPException(403, "儲存設備 API 模組未啟用")
    if auth_disabled(conn):
        raise HTTPException(403, "登入停用中，儲存設備 API 不能設定或測試。請先啟用登入，再以 admin 帳號操作")
    if session is None or not auth.is_admin(session):
        raise HTTPException(403, "只有 admin 帳號能設定或測試儲存設備 API")
    su.ensure_tables(conn)
    return session["username"]


class StorageDeviceBody(BaseModel):
    model_config = ConfigDict(extra="allow")      # 舊／錯的 client 若多送 username／password：下面明確 400，不能用 422（會回顯密碼）
    name: str = ""
    vendor: str = "dell_unity"
    host: str = ""
    port: int | None = su.DEFAULT_PORT
    credential: str | None = None       # 收集憑證名稱；None＝沿用目前選擇（新增時＝未選）
    ca_bundle: str = ""
    trust_self_signed: bool = False
    enabled: bool = True
    note: str = ""


def _reject_secret_fields(body: BaseModel) -> None:
    extra = body.model_extra or {}
    if any(k in extra for k in ("username", "password", "token", "secret")):
        raise HTTPException(400, "這裡不收帳號密碼；請到 系統設定 → 收集憑證 設定（類型選 storage），再回來挑選憑證名稱")


def _fields(body: StorageDeviceBody) -> dict:
    return dict(name=body.name, vendor=body.vendor, host=body.host, port=body.port, ca_bundle=body.ca_bundle,
                trust_self_signed=body.trust_self_signed, enabled=body.enabled, note=body.note)


@router.get("/api/storage/devices")
def storage_devices_list(_g: None = Depends(storage_read_gate), conn: sqlite3.Connection = Depends(get_db)):
    rows = su.list_devices(conn)
    last = sc.latest_runs(conn)
    health = sh.latest_batches(conn)
    for r in rows:
        lr = last.get(r["id"])
        r["last_collect"] = None if lr is None else {k: lr[k] for k in ("id", "status", "complete", "code", "message", "finished_at", "item_count", "review_count", "sections")}
        hb = health.get(r["id"])
        r["last_health"] = None if hb is None else {k: hb[k] for k in (
            "id", "status", "complete", "code", "message", "finished_at", "trigger", "attempt_no", "health_status", "health_value", "alert_status",
            "n_total", "n_effective", "n_resolved", "n_uncertain", "n_acked", "n_manual", "verdict")}
        r["health_schedule"] = sh.schedule_info(conn, r)
    return {"rows": rows,
            "vendors": [{"key": k, "label": v} for k, v in su.SUPPORTED_VENDORS.items()],
            "unsupported_vendors": list(su.UNSUPPORTED_HINT)}


@router.post("/api/storage/devices")
def storage_device_create(body: StorageDeviceBody, user: str = Depends(storage_write_gate), conn: sqlite3.Connection = Depends(get_db)):
    _reject_secret_fields(body)
    try:
        return su.create_device(conn, user, credential=body.credential or "", **_fields(body))
    except ih.ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/api/storage/devices/{device_id}")
def storage_device_update(device_id: int, body: StorageDeviceBody, user: str = Depends(storage_write_gate),
                          conn: sqlite3.Connection = Depends(get_db)):
    _reject_secret_fields(body)
    try:
        return su.update_device(conn, device_id, credential=body.credential, **_fields(body))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ih.ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/api/storage/devices/{device_id}")
def storage_device_delete(device_id: int, user: str = Depends(storage_write_gate), conn: sqlite3.Connection = Depends(get_db)):
    if not su.delete_device(conn, device_id):
        raise HTTPException(404, "查無這台設備")
    return {"deleted": True}


@router.post("/api/storage/devices/{device_id}/test", dependencies=[Depends(__import__("quiet_deps").gate("manual_diag"))])
def storage_device_test(device_id: int, user: str = Depends(storage_write_gate), conn: sqlite3.Connection = Depends(get_db)):
    try:
        return su.check_connection(conn, device_id, user)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/storage/devices/{device_id}/collect", dependencies=[Depends(__import__("quiet_deps").gate("manual_diag"))])
def storage_device_collect(device_id: int, user: str = Depends(storage_write_gate), conn: sqlite3.Connection = Depends(get_db)):
    """批 S2：人為按鈕才收（同步，GET only）。窗內要二次確認（同連線測試）；沒有任何排程會呼叫它。"""
    try:
        return sc.collect_device(conn, device_id, user)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/storage/devices/{device_id}/collected")
def storage_device_collected(device_id: int, run_id: int | None = None, _g: None = Depends(storage_read_gate),
                             conn: sqlite3.Connection = Depends(get_db)):
    """唯讀明細：預設該台最近一次有資料的收集；附最近一次（可能失敗）的摘要。"""
    try:
        return sc.get_detail(conn, device_id, run_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/storage/devices/{device_id}/health")
def storage_device_health(device_id: int, batch_id: int | None = None, _g: None = Depends(storage_read_gate),
                          conn: sqlite3.Connection = Depends(get_db)):
    """唯讀明細（S3）：預設該台最近一批成功／部分成功的每日檢查；附最近一批（可能失敗）、近 30 批歷史與排程狀態。不連線。"""
    try:
        return sh.get_detail(conn, device_id, batch_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/storage/devices/{device_id}/health-check")
def storage_device_health_check(device_id: int, request: Request, confirm_in_window: bool = False, user: str = Depends(storage_write_gate),
                                conn: sqlite3.Connection = Depends(get_db)):
    """批 S3：admin 的「立即檢查」（人為，同步，只發 GET）。窗內（平日 08:30–15:00）要二次確認；存成一批 trigger=manual。
    當天已成功的自動批次不受影響；人為成功的批次也會讓當天不再重複自動檢查。"""
    quiet_window.confirm_from_request(conn, "storage_health_tick", confirm_in_window, user, request)
    try:
        return sh.check_device(conn, device_id, trigger="manual", user=user)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
