"""憑證管理 第一階段的 API（2026-11-01）。邏輯都在 certs.py，這裡只接線。

只做管理與查詢：本系統不對外探測（到期探測由中繼 PC 做、系統只讀檔，見 relay_import 的 cert 來源）、沒有更換、不碰私鑰。
讀：登入即可（viewer 以上）；人名一律遮罩，只有管理員（_can_edit_nav，跟選單編輯同一把尺）看得到完整聯絡資訊。
寫：新增／編輯／停用／登錄觀測＝一般帳號（user）；匯入、門檻設定、含人名匯出＝管理員。
"""
from __future__ import annotations

import json

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["certs_summary_endpoint", "certs_options_endpoint", "certs_list_endpoint", "certs_detail_endpoint", "certs_create_endpoint",
           "certs_update_endpoint", "certs_deactivate_endpoint", "certs_reactivate_endpoint", "certs_obs_endpoint",
           "certs_obs_void_endpoint", "certs_obs_restore_endpoint", "certs_import_preview_endpoint", "certs_import_apply_endpoint",
           "certs_settings_put_endpoint", "certs_export_identified_endpoint", "certs_batch_preview_endpoint",
           "certs_batch_apply_endpoint", "certs_batches_endpoint", "certs_batch_undo_endpoint", "certs_mount_add_endpoint",
           "certs_mount_active_endpoint", "certs_mount_remove_endpoint", "certs_mark_replaced_endpoint", "certs_planned_import_endpoint",
           "certs_probe_list_endpoint"]


def _user(session) -> str | None:
    try:
        return session["username"]
    except (KeyError, IndexError, TypeError):
        return None


def _admin(session, conn) -> bool:
    return bool(_can_edit_nav(session, conn))


def _need_admin(session, conn) -> None:
    if not _admin(session, conn):
        raise HTTPException(403, "只有管理員可以做這件事")


def _call(fn, *a, **kw):
    import certs
    try:
        return fn(*a, **kw)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except certs.KeyConflict as exc:
        raise HTTPException(409, str(exc))
    except certs.PlanChanged as exc:
        raise HTTPException(409, str(exc))
    except certs.Forbidden as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _audit(conn, request, session, text: str, action: str) -> None:
    """操作紀錄只寫『做了什麼』，不寫人名內容。"""
    activity.log(conn, username=_user(session), ip=activity.client_ip(request), method="CERT", path=text[:280],
                 status=200, action=action)


def _audited(conn, request, session, action: str, text_fn):
    """資料異動與稽核寫進同一個交易（certs._commit 在 commit 前呼叫）：稽核失敗＝資料一併回滾，不會『已生效沒紀錄』。
    text_fn(info)＝稽核文字（只寫做了什麼，不寫人名內容）。"""
    import certs
    uname, ip = _user(session), activity.client_ip(request)

    def hook(c, info):
        activity.log(c, username=uname, ip=ip, method="CERT", path=text_fn(info)[:280], status=200, action=action, commit=False)
    return certs.audit_scope(hook)


class ItemBody(BaseModel):
    planned_date: str | None = None
    name: str | None = None
    environment: str | None = None
    endpoint: str | None = None
    port: int | None = None
    sni: str | None = None
    mount_type: str | None = None
    host_ips: str | None = None
    f5_bigip: str | None = None
    f5_mounted: int | None = None
    os_type: str | None = None
    req_unit: str | None = None
    req_contact: str | None = None
    ap_contact: str | None = None
    infra_owner: str | None = None
    ap_tester: str | None = None
    planned_text: str | None = None
    announce_needed: str | None = None
    replace_method: str | None = None
    replace_status: str | None = None
    note: str | None = None


class ObsBody(BaseModel):
    not_after: str
    not_before: str | None = None
    issuer_dn: str | None = None
    serial: str | None = None
    fingerprint_sha256: str | None = None
    subject_cn: str | None = None
    san: str | list[str] | None = None
    observed_at: str | None = None
    note: str | None = None


class SettingsBody(BaseModel):
    thresholds: list[int]


class DeactivateBody(BaseModel):
    reason_code: str
    reason_note: str | None = None


class VoidBody(BaseModel):
    reason: str


class BatchBody(BaseModel):
    ids: list[str] | None = None           # 勾選的項目
    filter: dict | None = None             # 或：『全選目前篩選結果』直接帶篩選條件（後端用同一份篩選算清單）
    changes: dict = {}
    replace_op: dict | None = None         # 標記已更換：{not_after | later, void_old}


class BatchApplyBody(BaseModel):
    ids: list[str]                          # 預覽時回傳的確定清單（不再重新用篩選算，才不會預覽後偷偷變）
    changes: dict = {}
    digest: str
    replace_op: dict | None = None


class MarkReplacedBody(BaseModel):
    not_after: str | None = None            # 新到期時間（UTC+8）
    later: bool = False                     # 明確勾「稍後補登」
    void_old: bool = False                  # 同時作廢舊觀測


class PlannedImportBody(BaseModel):
    action: str                             # convert＝轉成正式預排日期／ignore＝忽略匯入值


class MountBody(BaseModel):
    name: str
    active: bool | None = None


#: 含人名匯出的用途：受控分類（稽核只記代號與列數，不記自由文字——避免人名被寫進操作紀錄）
EXPORT_PURPOSES = {"audit": "稽核查核", "replace": "更換作業", "inventory": "內部盤點", "other": "其他"}


class ExportBody(BaseModel):
    purpose_code: str
    other_note: str | None = None      # 選「其他」時必填，但只用來擋空白，不寫入任何紀錄
    filter: dict | None = None         # 清單目前的篩選條件（含人名匯出沿用它；None＝全部）


@router.get("/api/certs/summary")
def certs_summary_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import certs
    out = certs.summary(conn)
    out["can_see_contacts"] = _admin(session, conn)
    return out


@router.get("/api/certs/options")
def certs_options_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """篩選與表單的下拉選項（掛載型態來自設定；申請單位來自現有資料）。"""
    import certs
    return certs.options(conn)


@router.get("/api/certs/items")
def certs_list_endpoint(status: str | None = None, q: str | None = None, include_inactive: bool = False,
                        env: str | None = None, mount: str | None = None, unit: str | None = None, risk: str | None = None,
                        within: str | None = None, due_from: str | None = None, due_to: str | None = None,
                        rs: str | None = None, announce: str | None = None, has_expiry: str | None = None,
                        active: str | None = None, plan_from: str | None = None, plan_to: str | None = None,
                        has_plan: str | None = None, sort: str = "seq", desc: bool = False, page: int = 1, size: int = 100,
                        session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """伺服器端篩選（可組合）＋排序＋分頁；size=0 回全部（匯出目前篩選結果用）。
    回傳 total＝同一個『是否停用』範圍的總筆數、matched＝符合篩選的筆數。"""
    import certs
    flt = {"status": status, "q": q, "env": env, "mount": mount, "unit": unit, "risk": risk, "within": within, "due_from": due_from,
           "due_to": due_to, "rs": rs, "announce": announce, "has_expiry": has_expiry, "plan_from": plan_from, "plan_to": plan_to,
           "has_plan": has_plan, "active": active or ("all" if include_inactive else "on")}
    out = _call(certs.query_items, conn, flt, sort, desc, page, max(0, min(size, 5000)))
    if not _admin(session, conn):          # 停用說明是自由文字、可能含人名：非管理員不給
        for it in out["items"]:
            it["deactivate_note"] = ""
    return out


@router.get("/api/certs/items/{item_id}")
def certs_detail_endpoint(item_id: str, session: sqlite3.Row = Depends(require_auth),
                          conn: sqlite3.Connection = Depends(get_db)):
    import certs
    return _call(certs.item_detail, conn, item_id, _admin(session, conn))


@router.post("/api/certs/items")
def certs_create_endpoint(body: ItemBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                          conn: sqlite3.Connection = Depends(get_db)):
    import certs
    with _audited(conn, request, session, "cert_create", lambda i: f"憑證管理：新增項目 {i['id']}"):
        out = _call(certs.create_item, conn, body.model_dump(exclude_unset=True), _user(session), _admin(session, conn))
    return out


@router.put("/api/certs/items/{item_id}")
def certs_update_endpoint(item_id: str, body: ItemBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                          conn: sqlite3.Connection = Depends(get_db)):
    import certs
    with _audited(conn, request, session, "cert_update", lambda i: f"憑證管理：編輯項目 {item_id}（欄位：{','.join(i['changed'])}）"):
        out = _call(certs.update_item, conn, item_id, body.model_dump(exclude_unset=True), _user(session), _admin(session, conn))
    return out


@router.post("/api/certs/items/{item_id}/deactivate")
def certs_deactivate_endpoint(item_id: str, body: DeactivateBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    """停用＝保留項目與觀測歷史；必須選原因（選填說明不寫入稽核紀錄）。"""
    import certs
    with _audited(conn, request, session, "cert_deactivate", lambda i: f"憑證管理：停用項目 {item_id}（原因代號：{body.reason_code}）"):
        out = _call(certs.set_active, conn, item_id, False, _user(session), body.reason_code, body.reason_note)
    return out


@router.post("/api/certs/items/{item_id}/reactivate")
def certs_reactivate_endpoint(item_id: str, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import certs
    with _audited(conn, request, session, "cert_reactivate", lambda i: f"憑證管理：重新啟用項目 {item_id}"):
        out = _call(certs.set_active, conn, item_id, True, _user(session))
    return out


@router.post("/api/certs/items/{item_id}/observations")
def certs_obs_endpoint(item_id: str, body: ObsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                       conn: sqlite3.Connection = Depends(get_db)):
    """手動登錄一筆觀測（到期時間、簽發者、序號、指紋、SAN）。來源＝manual。"""
    import certs
    with _audited(conn, request, session, "cert_observe", lambda i: f"憑證管理：登錄觀測 {item_id}（到期 {i['not_after_utc']}）"):
        out = _call(certs.add_observation, conn, item_id, body.model_dump(exclude_unset=True), _user(session))
    return out


@router.post("/api/certs/items/{item_id}/observations/{obs_id}/void")
def certs_obs_void_endpoint(item_id: str, obs_id: str, body: VoidBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    """作廢一筆觀測（保留紀錄、不計入到期判斷、可還原；原因必填，不寫入稽核文字）。"""
    import certs
    with _audited(conn, request, session, "cert_obs_void", lambda i: f"憑證管理：作廢觀測 {obs_id}（項目 {item_id}）"):
        out = _call(certs.void_observation, conn, item_id, obs_id, body.reason, _user(session))
    return out


@router.post("/api/certs/items/{item_id}/observations/{obs_id}/restore")
def certs_obs_restore_endpoint(item_id: str, obs_id: str, request: Request, session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    import certs
    with _audited(conn, request, session, "cert_obs_restore", lambda i: f"憑證管理：還原作廢的觀測 {obs_id}（項目 {item_id}）"):
        out = _call(certs.restore_observation, conn, item_id, obs_id, _user(session))
    return out


@router.post("/api/certs/batch/preview")
def certs_batch_preview_endpoint(body: BatchBody, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """批次更新第一段：只計算、不寫庫。回影響筆數、前後差異與確認用的 digest。"""
    import certs
    return _call(certs.batch_preview, conn, body.ids, body.filter, body.changes, _admin(session, conn), None, body.replace_op)


@router.post("/api/certs/batch/apply")
def certs_batch_apply_endpoint(body: BatchApplyBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    """批次更新第二段：digest 對不上（預覽後資料有變）就 409；單一交易；稽核只記批次代號、筆數、欄位名（不含人名內容）。"""
    import certs
    with _audited(conn, request, session, "cert_batch", lambda i: f"憑證管理：批次更新 {i['batch_id'][:8]}，{i['changed']} 筆，欄位：{','.join(i['fields'])}"):
        out = _call(certs.batch_apply, conn, body.ids, body.changes, _user(session), _admin(session, conn), body.digest, None, body.replace_op)
    return out


@router.post("/api/certs/items/{item_id}/mark-replaced")
def certs_mark_replaced_endpoint(item_id: str, body: MarkReplacedBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                 conn: sqlite3.Connection = Depends(get_db)):
    """標記已更換（單一操作）：更換進度＝已更換＋登錄新到期日（必填或勾稍後補登）＋可作廢舊觀測。走批次路徑：單一交易、留批次紀錄、可復原。"""
    import certs
    with _audited(conn, request, session, "cert_mark_replaced", lambda i: f"憑證管理：標記已更換 {item_id}（批次 {i['batch_id'][:8]}）"):
        out = _call(certs.mark_replaced, conn, item_id, {"not_after": body.not_after, "later": body.later, "void_old": body.void_old},
                    _user(session), _admin(session, conn))
    return out


@router.post("/api/certs/items/{item_id}/planned-import")
def certs_planned_import_endpoint(item_id: str, body: PlannedImportBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                  conn: sqlite3.Connection = Depends(get_db)):
    """處理匯入的預排更換時間文字：轉成正式預排日期，或忽略匯入值。"""
    import certs
    with _audited(conn, request, session, "cert_planned_import", lambda i: f"憑證管理：預排文字{'轉成正式日期' if i['action'] == 'convert' else '忽略匯入值'} {item_id}"):
        out = _call(certs.planned_import_action, conn, item_id, body.action, _user(session))
    return out


@router.get("/api/certs/batches")
def certs_batches_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import certs
    return {"batches": certs.list_batches(conn, _admin(session, conn))}


@router.post("/api/certs/batch/{batch_id}/undo")
def certs_batch_undo_endpoint(batch_id: str, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    """整批復原；項目自批次後被改過的列為衝突、不覆蓋（回傳清單）。"""
    import certs
    with _audited(conn, request, session, "cert_batch_undo", lambda i: f"憑證管理：復原批次 {batch_id[:8]}（還原 {i['restored']} 筆，衝突 {len(i['conflicts'])} 筆）"):
        out = _call(certs.batch_undo, conn, batch_id, _user(session), _admin(session, conn))
    return out


@router.post("/api/certs/mount-types/add")
def certs_mount_add_endpoint(body: MountBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    import certs
    _need_admin(session, conn)
    with _audited(conn, request, session, "cert_mount_add", lambda i: f"憑證管理：新增掛載型態「{i['name']}」"):
        name = _call(certs.add_mount_type, conn, body.name, _user(session))
    return {"name": name, "mount_types": certs.mount_types(conn)}


@router.post("/api/certs/mount-types/set-active")
def certs_mount_active_endpoint(body: MountBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    import certs
    _need_admin(session, conn)
    if body.active is None:
        raise HTTPException(400, "請指定啟用或停用")
    with _audited(conn, request, session, "cert_mount_active", lambda i: f"憑證管理：{'啟用' if body.active else '停用'}掛載型態「{body.name}」"):
        _call(certs.set_mount_active, conn, body.name, body.active, _user(session))
    return {"mount_types": certs.mount_types(conn)}


@router.post("/api/certs/mount-types/remove")
def certs_mount_remove_endpoint(body: MountBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    import certs
    _need_admin(session, conn)
    with _audited(conn, request, session, "cert_mount_remove", lambda i: f"憑證管理：刪除掛載型態「{body.name}」"):
        _call(certs.remove_mount_type, conn, body.name, _user(session))
    return {"mount_types": certs.mount_types(conn)}


@router.post("/api/certs/import/preview")
def certs_import_preview_endpoint(file: UploadFile = File(...), session: sqlite3.Row = Depends(require_auth),
                                  conn: sqlite3.Connection = Depends(get_db)):
    """預覽：逐列 新增／更新／略過／衝突，附判斷原因；不寫資料庫。"""
    import certs
    import daily_risk
    _need_admin(session, conn)
    try:
        data = daily_risk.read_limited(file.file)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _call(certs.import_plan, conn, file.filename or "", data)


@router.post("/api/certs/import/apply")
def certs_import_apply_endpoint(request: Request, file: UploadFile = File(...), file_sha256: str = Form(""),
                                plan_digest: str = Form(""), decisions: str = Form("{}"), mount_decisions: str = Form("{}"),
                                session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    """確認寫入。衝突列沒有選擇就不寫；人工改過的欄位與既有觀測不覆蓋。"""
    import certs
    import daily_risk
    _need_admin(session, conn)
    try:
        dec = json.loads(decisions or "{}")
        if not isinstance(dec, dict) or not all(isinstance(v, str) for v in dec.values()):
            raise ValueError("decisions 格式不對（值必須是文字）")
        md = json.loads(mount_decisions or "{}")
        if not isinstance(md, dict) or not all(isinstance(v, str) for v in md.values()):
            raise ValueError("mount_decisions 格式不對（值必須是文字）")
        data = daily_risk.read_limited(file.file)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    with _audited(conn, request, session, "cert_import",
                  lambda i: f"憑證管理：匯入 {i['filename']}（新增 {i['created']}、更新 {i['updated']}、觀測 {i['observations']}、未決 {i['unresolved']}）"):
        out = _call(certs.import_apply, conn, file.filename or "", data, _user(session), dec, file_sha256, plan_digest, md)
    return out


@router.put("/api/certs/settings")
def certs_settings_put_endpoint(body: SettingsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    """到期風險等級門檻（高／中／低，天）。只影響畫面標示，不通知、不建待辦。"""
    import certs
    _need_admin(session, conn)
    try:
        with _audited(conn, request, session, "cert_settings", lambda i: f"憑證管理：到期風險等級門檻改為 {i['v']}"):
            v = certs.save_thresholds(conn, body.thresholds, _user(session))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"thresholds": v}


@router.post("/api/certs/export-identified")
def certs_export_identified_endpoint(body: ExportBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                                     conn: sqlite3.Connection = Depends(get_db)):
    """含人名的匯出：管理員＋必填用途；寫稽核（用途、列數）與匯出紀錄。一般匯出走清單表格的『匯出』（去識別）。"""
    import io
    from urllib.parse import quote

    import access_control
    import certs
    import export_safe
    from fastapi import Response

    _need_admin(session, conn)
    if body.purpose_code not in EXPORT_PURPOSES:
        raise HTTPException(400, "請選匯出用途")
    if body.purpose_code == "other" and len((body.other_note or "").strip()) < 4:
        raise HTTPException(400, "選「其他」時請另外填用途說明（至少 4 個字；說明不會寫入稽核紀錄）")
    head, rows = _call(certs.export_rows, conn, True, None, body.filter)
    access_control.note_export(request, name="憑證管理（含人名）", rows=len(rows), summary=f"含人名；用途代號：{body.purpose_code}")
    fkeys = ",".join(sorted(k for k, v in (body.filter or {}).items() if v not in (None, "")))       # 只記有哪些篩選條件，不記值
    _audit(conn, request, session, f"憑證管理：含人名匯出 {len(rows)} 列；用途代號：{body.purpose_code}；篩選鍵：{fkeys or '無'}", "cert_export_identified")
    wb = Workbook()
    ws = wb.active
    ws.title = export_safe.sheet_title("憑證管理含人名")
    ws.append(export_safe.row(head))
    for r in rows:
        ws.append(export_safe.row(["" if v is None else v for v in r]))
    export_safe.neutralize_workbook(wb)
    buf = io.BytesIO()
    wb.save(buf)
    name = f"憑證管理_含人名_{datetime.now():%Y%m%d_%H%M}.xlsx"
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.get("/api/certs/probe-list.txt")
def certs_probe_list_endpoint(request: Request, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """匯出探測清單（管理員）：給中繼 PC 的 cert_collect.ps1 讀，每行 `端點:埠,SNI`，只含啟用中項目。不含人名。"""
    from urllib.parse import quote

    import access_control
    import certs
    from fastapi import Response

    _need_admin(session, conn)
    out = certs.probe_list(conn)
    access_control.note_export(request, name="憑證探測清單", rows=out["lines"], summary=f"略過 {len(out['skipped'])} 筆（端點不是合法主機名或 IP）")
    _audit(conn, request, session, f"憑證管理：匯出探測清單 {out['lines']} 行；略過 {len(out['skipped'])} 筆", "cert_probe_list")
    name = f"cert_probe_list_{datetime.now():%Y%m%d_%H%M}.txt"
    return Response(out["text"].encode("ascii", "ignore"), media_type="text/plain; charset=us-ascii",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
                             "X-Probe-Lines": str(out["lines"]), "X-Probe-Skipped": ",".join(out["skipped"])[:2000]})
