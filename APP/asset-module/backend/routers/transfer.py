"""檔案傳送／批次查詢（Q94）的 API。

## 全部端點共用同一道閘門 transfer_gate（順序固定，任何一關不過都是 403）

  1. 模組開關 `file_transfer` 必須是開的（預設關；關著時連網址直接打都是 403——藏起畫面不是安全）
  2. 登入者必須是 admin（登入停用時是假 admin，2026-10-30 使用者決定取消「登入停用即拒絕」，方便測試）

沒登入（沒有 session）由 require_auth 先回 401。

## 沒有任何刪除／修改「紀錄」的端點

傳送紀錄、稽核、指令紀錄只有 GET。檔案庫的檔案可以刪（那是檔案，不是紀錄），而且刪除本身會寫稽核。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

import transfer_policy as pol
import transfer_service as svc
import transfer_store as st

router = _RouteCollector()

__all__ = [
    "transfer_gate", "transfer_status", "transfer_settings_get", "transfer_settings_put", "transfer_hosts",
    "transfer_host_files", "transfer_library_list", "transfer_library_upload", "transfer_library_delete",
    "transfer_preflight", "transfer_job_create", "transfer_jobs", "transfer_job_get", "transfer_job_stop",
    "transfer_job_retry", "transfer_job_reverify", "transfer_pull", "transfer_command_run", "transfer_commands",
    "transfer_command_get", "transfer_residue", "transfer_audit",
    "transfer_audit_verify", "transfer_audit_digest",
]


def check_access(session, conn: sqlite3.Connection) -> str:
    """模組開關→登入啟用→admin。回使用者名稱；不過就 HTTPException 403。toggle 端點也用這個。"""
    if not svc.module_enabled(conn):
        raise HTTPException(403, "檔案傳送模組未啟用")
    # 登入停用（測試階段）時不再拒絕：此時 session 是假 admin（2026-10-30 使用者決定，測試用）。
    if not st.is_admin(session):
        raise HTTPException(403, "只有 admin 帳號能使用檔案傳送")
    return session["username"]


def transfer_gate(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)) -> str:
    st.ensure_tables(conn)
    return check_access(session, conn)


def _err(exc: Exception):
    if isinstance(exc, svc.TransferError):
        raise HTTPException(exc.status, str(exc)) from exc
    if isinstance(exc, st.LibraryError):
        raise HTTPException(exc.status, str(exc)) from exc
    if isinstance(exc, pol.PolicyError):
        raise HTTPException(400, str(exc)) from exc
    raise exc


# ---------------------------------------------------------------- 狀態與設定
@router.get("/api/transfer/status")
def transfer_status(user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    now = pol.now()
    return {"user": user, "library": st.library_info(conn), "settings": st.get_settings(conn),
            "folders": {"windows": list(pol.WINDOWS_FOLDERS), "linux": list(pol.LINUX_FOLDERS)},
            "rules": {"small_mb": pol.SMALL_MAX // pol.MB, "mid_mb": pol.MID_MAX // pol.MB,
                      "wave_size": pol.WAVE_SIZE, "default_concurrency": pol.DEFAULT_CONCURRENCY,
                      "default_rate_mbps": pol.DEFAULT_RATE_MBPS, "rates": list(pol.ALLOWED_RATES_MBPS)},
            "now": st.now_str(now), "weekday": pol.is_weekday(now)}


class SettingsBody(BaseModel):
    ft_library_cap_gb: float | None = None
    ft_file_cap_gb: float | None = None
    ft_retention_days: float | None = None
    ft_wave_pause_s: float | None = None
    ft_max_active_jobs: float | None = None
    ft_max_hosts_job: float | None = None
    ft_max_hosts_cmd: float | None = None
    ft_max_waiting_jobs: float | None = None


@router.get("/api/transfer/settings")
def transfer_settings_get(user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    return st.get_settings(conn)


@router.put("/api/transfer/settings")
def transfer_settings_put(body: SettingsBody, user: str = Depends(transfer_gate),
                          conn: sqlite3.Connection = Depends(get_db)):
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        out = st.set_settings(conn, changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    st.audit(conn, user, "settings", ", ".join(f"{k}={v}" for k, v in changes.items()))
    return out


# ---------------------------------------------------------------- 主機
@router.get("/api/transfer/hosts")
def transfer_hosts(user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    return svc.list_hosts(conn)


@router.get("/api/transfer/host-files")
def transfer_host_files(ip: str, folder: str | None = None, user: str = Depends(transfer_gate),
                        conn: sqlite3.Connection = Depends(get_db)):
    """右邊窗格：某台主機白名單資料夾裡有什麼（只列檔案，不能瀏覽其他目錄）。"""
    import transfer_runner as rn
    try:
        h = svc.resolve_hosts(conn, [ip])[0]
        fol = pol.check_folder("windows" if h["box"] == "windows" else "linux", folder or pol.default_folder(h["platform"]))
        files = rn.get_runner().list_dir(svc.make_target(conn, h), fol)
    except rn.RunnerError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 轉成對應的 HTTP 錯誤；沒預期到的再往上丟
        _err(exc)
    return {"ip": ip, "folder": fol, "files": [{**f, "__no": i} for i, f in enumerate(files, 1)]}


# ---------------------------------------------------------------- 檔案庫
@router.get("/api/transfer/library")
def transfer_library_list(user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    now = pol.now()
    files = []
    for i, f in enumerate(st.list_files(conn), 1):
        v = pol.verdict(f["size"], now)          # 每個檔案「現在可不可以傳」（畫面標籤；真正擋的是建立傳送時的後端檢查）
        files.append({**f, "__no": i, "now_ok": v.ok, "why": v.why, "next_at": st.now_str(v.next_at) if v.next_at else None})
    return {"files": files, "info": st.library_info(conn)}


@router.post("/api/transfer/library")
async def transfer_library_upload(request: Request, user: str = Depends(transfer_gate),
                                  conn: sqlite3.Connection = Depends(get_db)):
    """上傳到檔案庫（審查 HIGH 4）：**不是 multipart**——Starlette 的 multipart 解析會先把整個檔案暫存到磁碟才交給我們，
    上限擋不住。改成原始位元組串流：檔名放標頭 X-Filename（網址編碼，過 check_name）、內容放 body。
    閘門先過；宣告的 Content-Length 超過上限（單檔／總量／磁碟剩餘取最小）在讀任何內容之前就 413；
    沒宣告長度（chunked）也在串流途中一超過就中止、刪暫存。"""
    import re as _re
    from urllib.parse import unquote
    from starlette.concurrency import run_in_threadpool
    name = unquote(request.headers.get("x-filename", ""))
    clen = request.headers.get("content-length", "")
    declared = int(clen) if (_re.fullmatch(r"[0-9]{1,18}", clen) and clen.isascii()) else None      # 只收 ASCII 數字
    try:
        pol.check_name("linux", name)
        sink = await run_in_threadpool(st.UploadSink, conn, name, declared)
    except Exception as exc:  # noqa: BLE001
        _err(exc)
    try:
        async for chunk in request.stream():
            await run_in_threadpool(sink.write, chunk)               # 寫檔與雜湊不佔住事件迴圈
    except st.LibraryError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except BaseException:
        await run_in_threadpool(sink.abort)
        raise
    try:
        row = await run_in_threadpool(sink.commit, user)
    except st.LibraryError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    await run_in_threadpool(st.audit, conn, user, "library_upload", f"{row['name']}（{row['size']} 位元組，SHA256 {row['sha256'][:12]}…）")
    return row


@router.delete("/api/transfer/library/{file_id}")
def transfer_library_delete(file_id: int, user: str = Depends(transfer_gate),
                            conn: sqlite3.Connection = Depends(get_db)):
    try:
        ok = st.delete_file(conn, file_id, user)
    except st.LibraryError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not ok:
        raise HTTPException(404, "查無此檔案")
    return {"ok": True}


# ---------------------------------------------------------------- 傳送
class PreflightBody(BaseModel):
    file_id: int
    ips: list[str]
    folder: str | None = None
    concurrency: int | None = None
    rate_mbps: int | None = None


class JobBody(PreflightBody):
    overwrite: bool = False
    confirm_count: int | None = None
    confirm_big: bool = False


@router.post("/api/transfer/preflight")
def transfer_preflight(body: PreflightBody, user: str = Depends(transfer_gate),
                       conn: sqlite3.Connection = Depends(get_db)):
    try:
        return svc.preflight(conn, body.file_id, body.ips, body.folder, body.concurrency, body.rate_mbps)
    except Exception as exc:  # noqa: BLE001
        _err(exc)


@router.post("/api/transfer/jobs")
def transfer_job_create(body: JobBody, user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    try:
        return svc.create_upload_job(conn, user, body.file_id, body.ips, body.folder, body.concurrency,
                                     body.rate_mbps, body.overwrite, body.confirm_count, body.confirm_big)
    except Exception as exc:  # noqa: BLE001
        _err(exc)


@router.get("/api/transfer/jobs")
def transfer_jobs(user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    rows = svc.job_summary_rows(conn)
    return {"jobs": [{**r, "__no": r["id"]} for r in rows]}


@router.get("/api/transfer/jobs/{job_id}")
def transfer_job_get(job_id: int, user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    try:
        return svc.job_detail(conn, job_id)
    except Exception as exc:  # noqa: BLE001
        _err(exc)


@router.post("/api/transfer/jobs/{job_id}/stop")
def transfer_job_stop(job_id: int, user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    try:
        return svc.stop_job(conn, user, job_id)
    except Exception as exc:  # noqa: BLE001
        _err(exc)


@router.post("/api/transfer/jobs/{job_id}/retry-failed")
def transfer_job_retry(job_id: int, user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    try:
        return svc.retry_failed(conn, user, job_id)
    except Exception as exc:  # noqa: BLE001
        _err(exc)


class ReverifyBody(BaseModel):
    with_hash: bool = False


@router.post("/api/transfer/jobs/{job_id}/reverify")
def transfer_job_reverify(job_id: int, body: ReverifyBody, user: str = Depends(transfer_gate),
                          conn: sqlite3.Connection = Depends(get_db)):
    try:
        return svc.start_reverify(conn, user, job_id, body.with_hash)
    except Exception as exc:  # noqa: BLE001
        _err(exc)


class PullBody(BaseModel):
    ip: str
    folder: str | None = None
    name: str


@router.post("/api/transfer/pull")
def transfer_pull(body: PullBody, user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    try:
        return svc.create_pull(conn, user, body.ip, body.folder, body.name)
    except Exception as exc:  # noqa: BLE001
        _err(exc)


# ---------------------------------------------------------------- 指令
class CommandBody(BaseModel):
    box: str
    ips: list[str]
    command: str
    folder: str | None = None


@router.post("/api/transfer/commands")
def transfer_command_run(body: CommandBody, user: str = Depends(transfer_gate),
                         conn: sqlite3.Connection = Depends(get_db)):
    try:
        return svc.start_command(conn, user, body.box, body.ips, body.command, body.folder)
    except Exception as exc:  # noqa: BLE001
        _err(exc)


@router.get("/api/transfer/commands")
def transfer_commands(user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    return {"logs": [{**r, "__no": r["id"]} for r in st.list_cmd_logs(conn)]}


@router.get("/api/transfer/commands/{log_id}")
def transfer_command_get(log_id: int, user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    d = st.get_cmd_log(conn, log_id)
    if d is None:
        raise HTTPException(404, "查無此指令紀錄")
    return d


# ---------------------------------------------------------------- 紀錄
@router.get("/api/transfer/residue")
def transfer_residue(user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    return {"rows": [{**r, "__no": i} for i, r in enumerate(svc.residue(conn), 1)]}


@router.get("/api/transfer/audit")
def transfer_audit(user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    return {"rows": [{**r, "__no": r["id"]} for r in st.list_audit(conn)]}


@router.get("/api/transfer/audit-verify")
def transfer_audit_verify(user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    """稽核完整性：純讀，只對照「外部摘要」(audit_digest.log) 驗證各稽核表，不寫 log、不留存。"""
    import audit_digest
    try:
        return {"verify": audit_digest.verify(conn), "retained": None}
    except Exception as exc:  # noqa: BLE001 - 驗證本身壞了要讓畫面看得到，不是 500 空白
        return {"verify": {"status": "error", "ok": False, "issues": [],
                           "message": f"驗證失敗：{type(exc).__name__}: {exc}"}, "retained": None}


@router.post("/api/transfer/audit-digest")
def transfer_audit_digest(user: str = Depends(transfer_gate), conn: sqlite3.Connection = Depends(get_db)):
    """立即驗證並留存一輪新摘要（admin；寫入只在這支 POST 與背景排程、服務啟動）。每小時最多留存一次。"""
    import audit_digest
    return audit_digest.verify_then_retain(conn)
