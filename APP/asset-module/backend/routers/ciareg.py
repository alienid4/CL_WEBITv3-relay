"""9.4 CIA 補登（P2 VM 補登，試辦）的 API。邏輯都在 cia_reg.py，這裡只接線。

權限（access_policy）：讀＝登入即可；分派、設定、改派、撤出、不登記確認、交回檔、彙整、下載（含個資欄）＝管理員。
寫入都過 write_guard 並記「誰」（事件表另記每一台的來龍去脈）。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()


class CiaRegFpBody(BaseModel):
    fingerprint: str
    deadline: str | None = None


class CiaRegDeadlineBody(BaseModel):
    deadline: str | None = None


class CiaRegSettingsBody(BaseModel):
    assignees: dict[str, list[dict]] | None = None
    pilot: list[str] | None = None


class CiaRegIdsBody(BaseModel):
    ids: list[int]
    note: str | None = None
    assignee: str | None = None
    accept: bool | None = None
    gone: bool | None = None


def _w(conn, request, session) -> str:
    from routers.overlaps import _actor, _guard

    _guard(conn, request)
    return _actor(session, request)


def _call(fn, *a):
    import cia_reg

    try:
        return fn(*a)
    except cia_reg.Stale as exc:
        raise HTTPException(409, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/cia-reg/summary")
def cia_reg_summary_endpoint(round_id: int | None = None, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    """卡片＋進度矩陣（類別×負責人）＋回測摘要＋每人檔案＋輪次。round_id＝只看那一輪。數字＝/api/cia-reg/items 同一份。"""
    import cia_reg

    return cia_reg.summary(conn, round_id)


@router.get("/api/cia-reg/chase")
def cia_reg_chase_endpoint(round_id: int | None = None, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    """催收：每位負責人應交、檔案產生／寄出時間、截止日、已收、退回、通過、逾期天數、下一步。"""
    import cia_reg

    return {"items": cia_reg.chase(conn, round_id)}


@router.get("/api/cia-reg/people")
def cia_reg_people_endpoint(q: str = "", session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """分派設定挑人：AD 名單裡啟用、有信箱的人。"""
    import cia_reg

    return {"items": cia_reg.people_search(conn, q), "ad_loaded": bool(cia_reg._ad_rows(conn))}


@router.get("/api/cia-reg/files")
def cia_reg_files_endpoint(assignee: str | None = None, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    """產過的每個檔（現行／作廢＋原因）與待重寄台數。"""
    import cia_reg

    return {"items": cia_reg.files(conn, assignee), "resend": len(cia_reg._resend_ids(conn))}


@router.post("/api/cia-reg/files/{file_id}/sent")
def cia_reg_file_sent_endpoint(file_id: int, request: Request, session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    return _call(cia_reg.mark_sent, conn, file_id, _w(conn, request, session))


@router.get("/api/cia-reg/files.zip")
def cia_reg_zip_endpoint(request: Request, assignee: str | None = None, session: sqlite3.Row = Depends(require_auth),
                         conn: sqlite3.Connection = Depends(get_db)):
    """整批下載：每人目前待填的檔＋派送清冊（負責人、檔名、台數、sha256）。"""
    import cia_reg
    from routers.overlaps import _actor

    data, fname, n = _call(cia_reg.zip_files, conn, _actor(session, request), assignee or None)
    from urllib.parse import quote
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}", "X-File-Count": str(n)})


@router.put("/api/cia-reg/rounds/{round_id}/deadline")
def cia_reg_deadline_endpoint(round_id: int, body: CiaRegDeadlineBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    return _call(cia_reg.set_deadline, conn, round_id, body.deadline, _w(conn, request, session))


@router.post("/api/cia-reg/items/restore")
def cia_reg_restore_endpoint(body: CiaRegIdsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    """「資產已消失」恢復成消失前的狀態（要寫原因、留事件）。"""
    import cia_reg

    return _call(cia_reg.restore_gone, conn, body.ids, body.note, _w(conn, request, session))


@router.get("/api/cia-reg/reassign-check")
def cia_reg_reassign_check_endpoint(assignee: str, n: int = 1, session: sqlite3.Row = Depends(require_auth),
                                    conn: sqlite3.Connection = Depends(get_db)):
    """改派前檢查：對象暫停中或會超過上限 → 回警示（不擋）。"""
    import cia_reg

    return {"warning": cia_reg.reassign_warning(conn, assignee, n)}


@router.post("/api/cia-reg/items/missing")
def cia_reg_missing_endpoint(body: CiaRegIdsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    """「待確認（找不到）」：gone=true 確認資產已消失；false 恢復原狀態。"""
    import cia_reg

    return _call(cia_reg.missing_decide, conn, body.ids, bool(body.gone), body.note, _w(conn, request, session))


@router.get("/api/cia-reg/items")
def cia_reg_items_endpoint(status: str | None = None, category: str | None = None, assignee: str | None = None,
                           metric: str | None = None, round_id: int | None = None, batch_id: int | None = None,
                           conf: str | None = None, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    """下鑽：status（含 open）、category、assignee（空字串＝未分派）、metric（total／returned／passed／bounced／remaining）、批次、信心。"""
    import cia_reg

    items = _call(cia_reg.list_items, conn, status or None, category or None, assignee, metric or None, round_id, batch_id, conf or None)
    return {"total": len(items), "items": items}


@router.get("/api/cia-reg/items/{item_id}/events")
def cia_reg_events_endpoint(item_id: int, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    return {"items": cia_reg.item_events(conn, item_id)}


@router.get("/api/cia-reg/backtest")
def cia_reg_backtest_endpoint(basis: str | None = None, outcome: str | None = None, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    """留一回測：不帶參數＝各依據的準確率；basis（依據代號或 prefill）＋outcome（correct／wrong／none）＝下鑽到哪幾台。"""
    import cia_reg

    if basis:
        rows = cia_reg.backtest_samples(conn, basis, outcome or None)
        return {"total": len(rows), "items": rows}
    return cia_reg.backtest(conn)


@router.get("/api/cia-reg/settings")
def cia_reg_settings_get_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    return cia_reg.get_settings(conn)


@router.put("/api/cia-reg/settings")
def cia_reg_settings_endpoint(body: CiaRegSettingsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    """負責人名單與試辦類別存系統設定（含真實姓名，不進版控）。"""
    import cia_reg

    _w(conn, request, session)
    return _call(cia_reg.save_settings, conn, body.assignees, body.pilot)


@router.get("/api/cia-reg/preview")
def cia_reg_preview_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """分派前預覽（不寫庫）：母體 → VM → 排除（附原因）→ 可分派 → 這輪（試辦類別）；各人台數、信心分布。fingerprint 要帶回。"""
    import cia_reg

    return cia_reg.preview(conn)


@router.post("/api/cia-reg/rounds")
def cia_reg_create_endpoint(body: CiaRegFpBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    return _call(cia_reg.create_round, conn, _w(conn, request, session), body.fingerprint, body.deadline)


@router.get("/api/cia-reg/rounds")
def cia_reg_rounds_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    return {"items": cia_reg.rounds(conn)}


@router.post("/api/cia-reg/items/reassign")
def cia_reg_reassign_endpoint(body: CiaRegIdsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    return _call(cia_reg.reassign, conn, body.ids, body.assignee or "", body.note, _w(conn, request, session))


@router.post("/api/cia-reg/items/withdraw")
def cia_reg_withdraw_endpoint(body: CiaRegIdsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                              conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    return _call(cia_reg.withdraw, conn, body.ids, body.note, _w(conn, request, session))


@router.post("/api/cia-reg/items/claim")
def cia_reg_claim_endpoint(body: CiaRegIdsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    """負責人說「不登記」：accept=true → 撤出；false → 退回重填。"""
    import cia_reg

    return _call(cia_reg.claim_decide, conn, body.ids, bool(body.accept), body.note, _w(conn, request, session))


@router.get("/api/cia-reg/files/{assignee}.xlsx")
def cia_reg_person_xlsx_endpoint(assignee: str, request: Request, part: int = 1, bounced: bool = False, session: sqlite3.Row = Depends(require_auth),
                                 conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    from routers.overlaps import _actor

    buf, n, fname = _call(cia_reg.person_xlsx, conn, assignee, part, bounced, _actor(session, request))
    resp = _xlsx_response(buf, fname)
    resp.headers["X-Row-Count"] = str(n)
    return resp


def _read_upload(file: UploadFile) -> bytes:
    import cia_reg

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 檔案")
    data = file.file.read(cia_reg.MAX_UPLOAD_BYTES + 1)
    if len(data) > cia_reg.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "檔案超過 10MB")
    return data


@router.post("/api/cia-reg/upload/preview")
def cia_reg_upload_preview_endpoint(file: UploadFile = File(...), session: sqlite3.Row = Depends(require_auth),
                                    conn: sqlite3.Connection = Depends(get_db)):
    """負責人交回的補登檔：逐列 拒收／退回／通過／說不登記。不寫庫。"""
    import cia_reg

    return _call(cia_reg.upload_preview, conn, _read_upload(file), file.filename)


@router.post("/api/cia-reg/upload/confirm")
def cia_reg_upload_confirm_endpoint(request: Request, file: UploadFile = File(...), file_sha256: str = Form(...),
                                    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    by = _w(conn, request, session)
    return _call(cia_reg.upload_confirm, conn, _read_upload(file), file.filename, file_sha256, by)


@router.get("/api/cia-reg/uploads")
def cia_reg_uploads_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    return {"items": cia_reg.uploads(conn)}


@router.get("/api/cia-reg/merge/preview")
def cia_reg_merge_preview_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    return cia_reg.merge_preview(conn)


@router.post("/api/cia-reg/merge")
def cia_reg_merge_endpoint(body: CiaRegFpBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    return _call(cia_reg.merge, conn, _w(conn, request, session), body.fingerprint)


@router.get("/api/cia-reg/batches")
def cia_reg_batches_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    return {"items": cia_reg.batches(conn)}


@router.get("/api/cia-reg/batches/{batch_id}/packages/{pkg_no}.xlsx")
def cia_reg_batch_xlsx_endpoint(batch_id: int, pkg_no: int, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    import cia_reg

    buf, n, fname = _call(cia_reg.batch_xlsx, conn, batch_id, pkg_no)
    resp = _xlsx_response(buf, fname)
    resp.headers["X-Row-Count"] = str(n)
    return resp
