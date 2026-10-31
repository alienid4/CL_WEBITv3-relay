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
           "night_settings_endpoint", "night_settings_save_endpoint", "roster_template_endpoint",
           "roster_preview_endpoint", "roster_import_endpoint",
           "data_fix_endpoint", "data_fix_required_endpoint", "lifecycle_endpoint",
           "hk_overview_endpoint", "hk_scan_endpoint", "hk_result_endpoint"]


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


# ---------------------------------------------------------------- 夜間速查設定（W221-N1）
class NightSettingsBody(BaseModel):
    auto_live: bool


@router.get("/api/night/settings")
def night_settings_endpoint(session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    """夜間速查設定：所有登入者可讀（頁面要知道要不要自動即時檢測）。"""
    import daily_risk
    out = daily_risk.night_settings(conn)
    out["can_edit"] = _can_edit_nav(session, conn)
    return out


@router.put("/api/night/settings")
def night_settings_save_endpoint(body: NightSettingsBody, session: sqlite3.Row = Depends(require_auth),
                                 conn: sqlite3.Connection = Depends(get_db)):
    import daily_risk
    _need_admin(session, conn)
    return daily_risk.save_night_auto_live(conn, body.auto_live, _user(session))


# ---------------------------------------------------------------- 值班電話名冊匯入（#11，只有管理員）
@router.get("/api/contacts/roster/template")
def roster_template_endpoint(fmt: str = "csv", session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    """名冊範本（csv 或 xlsx）。"""
    import daily_risk
    _need_admin(session, conn)
    rows = daily_risk.roster_template_rows()
    if fmt == "xlsx":
        wb = Workbook()
        ws = wb.active
        ws.title = "值班電話名冊"
        for r in rows:
            ws.append(r)
        for cell in ws["C"][1:]:
            cell.number_format = "@"        # 電話欄存成文字，開頭的 0 才不會被 Excel 吃掉
        buf = io.BytesIO()
        wb.save(buf)
        return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": "attachment; filename=oncall_roster_template.xlsx"})
    if fmt != "csv":
        raise HTTPException(400, "fmt 只能是 csv 或 xlsx")
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return Response(("\ufeff" + buf.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=oncall_roster_template.csv"})


@router.post("/api/contacts/roster/preview")
def roster_preview_endpoint(file: UploadFile = File(...), session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    """dry-run：逐列列出 新增／更新／不變／衝突／格式錯誤，不寫資料庫。"""
    import daily_risk
    _need_admin(session, conn)
    try:
        return daily_risk.roster_plan(conn, file.filename or "", daily_risk.read_limited(file.file))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/contacts/roster/import")
def roster_import_endpoint(file: UploadFile = File(...), file_sha256: str = Form(""), plan_digest: str = Form(""),
                           session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    """確認匯入：重新解析同一份檔，只寫新增／更新；衝突與格式錯誤的列不寫。"""
    import daily_risk
    _need_admin(session, conn)
    try:
        return daily_risk.roster_apply(conn, file.filename or "", daily_risk.read_limited(file.file), _user(session),
                                       file_sha256, plan_digest)
    except daily_risk.RosterChanged as exc:
        raise HTTPException(409, str(exc))
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


# ---------------------------------------------------------------- 目錄清理掃描（1.3「目錄清理」頁籤，2.007.17）
class HkScanBody(BaseModel):
    serials: list[str]


@router.get("/api/housekeeping/overview")
def hk_overview_endpoint(session: sqlite3.Row = Depends(require_auth),
                         conn: sqlite3.Connection = Depends(get_db)):
    """可掃描的主機（已納管、非 VMware）＋每台最近一次掃描狀態＋現在能不能掃（08:30～15:00 不能）。"""
    import dir_housekeeping
    out = dir_housekeeping.overview(conn)
    out["can_edit"] = _can_edit_nav(session, conn)
    return out


@router.post("/api/housekeeping/scan")
def hk_scan_endpoint(body: HkScanBody, request: Request,
                     session: sqlite3.Row = Depends(require_auth),
                     conn: sqlite3.Connection = Depends(get_db)):
    """手動開始掃描（只有管理員）。08:30～15:00 伺服器直接拒絕；一次一批、一次一台、最多 50 台。"""
    import dir_housekeeping
    import write_guard
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以開始目錄清理掃描")
    try:
        write_guard.check(conn, request)
    except write_guard.TooManyFails as exc:
        raise HTTPException(429, str(exc))
    except write_guard.NeedPassword as exc:
        raise HTTPException(428, str(exc))
    try:
        return dir_housekeeping.queue_scan(conn, body.serials, write_guard.actor(session, request))
    except PermissionError as exc:
        raise HTTPException(423, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/housekeeping/result/{scan_id}")
def hk_result_endpoint(scan_id: int, session: sqlite3.Row = Depends(require_auth),
                       conn: sqlite3.Connection = Depends(get_db)):
    import dir_housekeeping
    if not _can_edit_nav(session, conn):     # 結果含目錄與檔名（可能有人名、客戶名），只給管理員
        raise HTTPException(403, "只有管理員可以看掃描結果")
    r = dir_housekeeping.result(conn, scan_id)
    if not r:
        raise HTTPException(404, "找不到這次掃描")
    return r
