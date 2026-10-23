"""設備風險清單＋出事聯絡資訊的 API（v1.499.0）。邏輯都在 daily_risk.py，這裡只接線。

讀：登入即可。寫（聯絡資訊、值班電話、合約匯入存檔、門檻設定）：管理員（_can_edit_nav，跟選單編輯同一把尺）。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["risk_overview_endpoint", "risk_settings_endpoint", "contacts_todo_endpoint",
           "contacts_entries_endpoint", "contacts_entry_save_endpoint", "contacts_oncall_endpoint",
           "contacts_oncall_save_endpoint", "contacts_audit_endpoint", "contacts_card_endpoint",
           "contracts_current_endpoint", "contracts_preview_endpoint", "contracts_import_endpoint",
           "data_fix_endpoint", "data_fix_required_endpoint", "lifecycle_endpoint"]


def _user(session) -> str | None:
    try:
        return session["username"]
    except (KeyError, IndexError, TypeError):
        return None


def _need_admin(session, conn) -> None:
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以修改")


class RiskSettingsBody(BaseModel):
    uptime_days: int | None = None
    disk_pct: int | None = None


class ContactEntryBody(BaseModel):
    level: str
    key: str
    vendor: str | None = None
    phone: str | None = None
    contact: str | None = None
    contract_no: str | None = None
    contract_end: str | None = None
    note: str | None = None


class OncallBody(BaseModel):
    api_id: str
    phone: str | None = None
    note: str | None = None


@router.get("/api/risk")
def risk_overview_endpoint(session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    """設備風險清單四個分頁的資料（一次給齊，卡片數＝分頁列數）。"""
    import daily_risk
    out = daily_risk.risk_overview(conn)
    out["can_edit"] = _can_edit_nav(session, conn)
    return out


@router.put("/api/risk/settings")
def risk_settings_endpoint(body: RiskSettingsBody, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    import daily_risk
    _need_admin(session, conn)
    try:
        return daily_risk.save_settings(conn, body.uptime_days, body.disk_pct)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/contacts/todo")
def contacts_todo_endpoint(session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    """待補：沒有任何聯絡／合約資料的機器，依系統、依型號分組（台數多的在前）。"""
    import daily_risk
    return daily_risk.todo(conn)


@router.get("/api/contacts/entries")
def contacts_entries_endpoint(session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import daily_risk
    return {"items": daily_risk.list_entries(conn), "can_edit": _can_edit_nav(session, conn)}


@router.put("/api/contacts/entry")
def contacts_entry_save_endpoint(body: ContactEntryBody, session: sqlite3.Row = Depends(require_auth),
                                 conn: sqlite3.Connection = Depends(get_db)):
    """新增／修改一筆（欄位全空＝刪除）。"""
    import daily_risk
    _need_admin(session, conn)
    try:
        saved = daily_risk.save_entry(conn, body.level, body.key, body.model_dump(), _user(session))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"saved": saved, "deleted": saved is None}


@router.get("/api/contacts/oncall")
def contacts_oncall_endpoint(session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    import daily_risk
    return {"items": daily_risk.list_oncall(conn), "can_edit": _can_edit_nav(session, conn)}


@router.put("/api/contacts/oncall")
def contacts_oncall_save_endpoint(body: OncallBody, session: sqlite3.Row = Depends(require_auth),
                                  conn: sqlite3.Connection = Depends(get_db)):
    import daily_risk
    _need_admin(session, conn)
    try:
        saved = daily_risk.save_oncall(conn, body.api_id, body.phone, body.note, _user(session))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"saved": saved, "deleted": saved is None}


@router.get("/api/contacts/audit")
def contacts_audit_endpoint(limit: int = 500, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    import daily_risk
    return {"items": daily_risk.audit_log(conn, max(1, min(limit, 5000)))}


@router.get("/api/contacts/card")
def contacts_card_endpoint(asset_serial: str | None = None, api_id: str | None = None,
                           session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    """出事聯絡卡：資產頁、4-1 右側、4-2 檢查清單共用。"""
    import daily_risk
    try:
        out = daily_risk.contact_card(conn, asset_serial=asset_serial, api_id=api_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    out["can_edit"] = _can_edit_nav(session, conn)
    return out


@router.get("/api/contracts")
def contracts_current_endpoint(session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    """目前生效的合約匯入（含對不上的列）＋歷次匯入。"""
    import daily_risk
    out = daily_risk.contract_current(conn)
    out["can_edit"] = _can_edit_nav(session, conn)
    return out


def _mapping_of(mapping: str | None) -> dict | None:
    if not mapping:
        return None
    try:
        m = json.loads(mapping)
    except ValueError:
        raise HTTPException(400, "欄位對應格式錯誤")
    return m if isinstance(m, dict) else None


@router.post("/api/contracts/preview")
def contracts_preview_endpoint(file: UploadFile = File(...), mapping: str | None = Form(None),
                               session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    """上傳檔 → 表頭、建議／記住的欄位對應、逐列比對結果（不存檔）。"""
    import daily_risk
    try:
        return daily_risk.preview(conn, file.filename or "", file.file.read(), _mapping_of(mapping))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/contracts/import")
def contracts_import_endpoint(file: UploadFile = File(...), mapping: str = Form(...),
                              session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    """存檔：取代上一份（舊的留作歷史），記住這次的欄位對應，留稽核。"""
    import daily_risk
    _need_admin(session, conn)
    try:
        return daily_risk.save_import(conn, file.filename or "", file.file.read(),
                                      _mapping_of(mapping) or {}, _user(session))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class RequiredBody(BaseModel):
    items: list[str]


@router.get("/api/data-fix")
def data_fix_endpoint(session: sqlite3.Row = Depends(require_auth),
                      conn: sqlite3.Connection = Depends(get_db)):
    """補登分派（v1.499.1）：每台缺哪些、依使用單位／系統分組、每週快照趨勢。本週還沒有快照會順手存一份。"""
    import data_fix
    out = data_fix.overview(conn)
    out["can_edit"] = _can_edit_nav(session, conn)
    return out


@router.put("/api/data-fix/required")
def data_fix_required_endpoint(body: RequiredBody, session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    import data_fix
    _need_admin(session, conn)
    try:
        return {"required": data_fix.save_required(conn, body.items)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/lifecycle")
def lifecycle_endpoint(session: sqlite3.Row = Depends(require_auth),
                       conn: sqlite3.Connection = Depends(get_db)):
    """新設備生命週期（v1.499.1）：每台停在哪一步、卡多久；第 4、5 步直接讀 existence／pipeline。"""
    import device_lifecycle
    return device_lifecycle.compute(conn)
