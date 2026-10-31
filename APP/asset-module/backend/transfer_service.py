"""檔案傳送（Q94）的流程：選主機、預檢、背景傳送、抓回、重新驗證、下指令。

規則（時段、大小、白名單）在 transfer_policy；資料表與檔案庫在 transfer_store；
碰目標主機的動作都在 transfer_runner 的介面後面。這裡只負責把它們串起來。

## 背景執行

每個傳送是一條背景執行緒（`spawn`）：進度寫進 SQLite，關掉瀏覽器也不會斷。
服務重啟時還在跑的那幾台標「服務重啟中斷」（recover_after_restart），可以用「重送失敗的」補。
等待允許時段的傳送由 resumer 每分鐘看一次，到時間自動開始。

## 失敗要講原因，不可以悄悄吞掉

每一台的結果都帶一句人看得懂的原因；沒預期到的例外也會被接住、寫成「未預期的錯誤：…」，
絕不會讓一台的錯誤把整批弄成「沒有結果」。
"""
from __future__ import annotations

import concurrent.futures as _cf
import contextlib
import datetime as _dt
import hashlib
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import transfer_policy as pol
import transfer_runner as rn
import transfer_store as st


class TransferError(Exception):
    """要回給使用者的錯誤（訊息直接給人看）。status＝HTTP 狀態碼。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


#: 測試設 True：背景工作改成同步執行（不開執行緒）
SYNC = False
_sleep = time.sleep
PREFLIGHT_MAX_HOSTS = 60           # 預檢逐台探測的上限（超過只給估算，逐台檢查在傳送時做）
COMMAND_POOL = 5
COMMAND_TIMEOUT_S = 120
COMMAND_MAX_LEN = 4000

CMD_PENDING_MAX = 300              # 全系統排隊中的「指令台數」上限
_cmd_lock = threading.Lock()
_cmd_pending = [0]
_cmd_pool_obj: list = [None]


def _command_pool() -> _cf.ThreadPoolExecutor:
    """全系統唯一的指令執行緒池（審查 HIGH 3）。"""
    with _cmd_lock:
        if _cmd_pool_obj[0] is None:
            _cmd_pool_obj[0] = _cf.ThreadPoolExecutor(max_workers=COMMAND_POOL, thread_name_prefix="ft-cmd")
        return _cmd_pool_obj[0]


_active_lock = threading.Lock()
_active_jobs: set[int] = set()


def spawn(fn, *args) -> None:
    if SYNC:
        fn(*args)
        return
    threading.Thread(target=fn, args=args, daemon=True, name="ft-worker").start()


def _db_path(conn: sqlite3.Connection) -> Path:
    return st._db_file(conn)


def _open(db_path: Path) -> sqlite3.Connection:
    import db as _db
    conn = _db.get_connection(db_path)
    st.ensure_tables(conn)
    return conn


# ====================================================================== 閘門（背景工作用）
def module_enabled(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT enabled FROM feature_flags WHERE module_key = ?", (st.MODULE_KEY,)).fetchone()
    return bool(row and row["enabled"])


def block_reason(conn: sqlite3.Connection, created_by: str | None = "admin") -> str | None:
    """背景工作能不能繼續：模組開著、登入啟用、而且建立者是 admin。不行就回原因（要寫進「已停止」的說明）。
    每一波、每一台開始前都問一次——中途關掉模組或停用登入，剩下的就不再動（審查 HIGH 2）。"""
    from _app_shared import auth_disabled
    if not module_enabled(conn):
        return "功能模組已停用"
    if auth_disabled(conn):
        # 2026-10-30 使用者決定：登入停用（測試階段）時也可用——此時沒有「建立者帳號」可重查，視同 admin。
        return None
    if not _creator_is_active_admin(conn, created_by):
        return "建立者已不是有效的 admin（帳號被刪除或降級）"
    return None


def _creator_is_active_admin(conn: sqlite3.Connection, name: str | None) -> bool:
    """排隊中的工作每一步都重讀建立者「現在」的帳號：還在 users 表、而且現在的等級是 admin。
    查不到、查詢出錯一律當不是（fail-closed）。"""
    import auth

    if not name:
        return False
    try:
        row = conn.execute("SELECT username, role FROM users WHERE username = ?", (name,)).fetchone()
    except sqlite3.Error:
        return False
    return row is not None and auth.is_admin(row)


def background_allowed(conn: sqlite3.Connection) -> bool:
    """背景工作開始前再問一次：模組還開著（登入停用不再擋，見 block_reason）。不成立就不動（fail-closed）。"""
    return module_enabled(conn)


# ====================================================================== 主機
def _label(platform: str) -> str:
    return {"windows": "Windows", "linux": "Linux", "aix": "AIX"}.get(platform, platform)


def list_hosts(conn: sqlite3.Connection) -> dict:
    """可以當傳檔／下指令對象的主機：已納管、現役、有明確平台（Windows／Linux／AIX），而且**不是 VMware 設備**。
    VMware（ESXi、vCenter）在這裡就被濾掉——天條；實際動作時各腳本的守門句還會再擋一次。"""
    import manage_state
    import system_stats
    import win_onboard

    rows = [r for r in conn.execute(
        "SELECT * FROM hardware WHERE collect_ok = 1 AND ip IS NOT NULL AND trim(ip) != ''")
        if not system_stats.is_retired_row(r)]
    kept, skipped = manage_state.drop_vmware_targets(conn, rows)
    out, seen, excluded = [], set(), {"vmware": len(skipped), "platform_unknown": 0, "not_opted_in": 0, "other": 0}
    for r in kept:
        ips = manage_state.ips_of(r["ip"])
        if not ips:
            excluded["other"] += 1
            continue
        ip = ips[0]
        key = system_stats.machine_key(r["hostname"], ip, r["asset_serial"])
        if key in seen or ip in seen:
            continue
        ex = manage_state.collect_platform_explain(conn, ip, r["os"], asset_serial=r["asset_serial"],
                                                   probe=lambda host: None)
        if ex["platform"] not in ("windows", "linux", "aix") or ex["basis"] == manage_state.BASIS_GUESS:
            excluded["platform_unknown"] += 1
            continue
        if ex["platform"] == "windows" and win_onboard.collect_block(conn, ip, r["asset_serial"]):
            excluded["not_opted_in"] += 1
            continue
        seen.add(key)
        seen.add(ip)
        out.append({
            "ip": ip, "serial": r["asset_serial"], "hostname": r["hostname"] or "", "platform": ex["platform"],
            "os_label": _label(ex["platform"]), "box": "windows" if ex["platform"] == "windows" else "linux",
            "env": (r["environment"] or "").strip() or "未填",
            "room": system_stats.normalize_location(r["physical_location"]),
        })
    out.sort(key=lambda h: ((h["hostname"] or "").lower(), h["ip"]))
    for i, h in enumerate(out, 1):
        h["__no"] = i
    return {"hosts": out, "excluded": excluded}


def resolve_hosts(conn: sqlite3.Connection, ips: list[str], box: str | None = None) -> list[dict]:
    """把使用者勾的 IP 對回合格主機清單。有任何一台不在清單裡（未納管、VMware、平台不明）就整批拒絕。"""
    ips = [str(x).strip() for x in ips if str(x).strip()]
    if not ips:
        raise TransferError("沒有選任何主機")
    if len(set(ips)) != len(ips):
        raise TransferError("主機清單有重複的 IP")
    by_ip = {h["ip"]: h for h in list_hosts(conn)["hosts"]}
    bad = [ip for ip in ips if ip not in by_ip]
    if bad:
        raise TransferError(f"這些不在可選主機內（未納管、已退役、VMware 設備或平台不明）：{'、'.join(bad[:5])}"
                            + ("…" if len(bad) > 5 else ""), 400)
    hosts = [by_ip[ip] for ip in ips]
    boxes = {h["box"] for h in hosts}
    if len(boxes) > 1:
        raise TransferError("一次只能選同一個箱子的主機（Windows 或 Linux／AIX），不能混著選")
    if box and boxes != {box}:
        raise TransferError("選的主機和箱子不一致")
    return hosts


def make_target(conn: sqlite3.Connection, h: dict) -> rn.Target:
    """憑證：Windows 用收集憑證庫裡的 WinRM 服務帳號；Linux／AIX 用既有的收集身分（不建任何帳號）。"""
    import collect_account_migration as cam
    import credential_store
    import manage_state

    if h["platform"] == "windows":
        name = credential_store.pick_for_host(conn, h["ip"], kind="winrm")
        if not name:
            raise TransferError(f"{h['ip']} 沒有可用的 WinRM 收集憑證（請先到「收集身分」設定）")
        cred = credential_store.get_for_use(conn, name)
        if cred is None:
            raise TransferError(f"憑證「{name}」解不開，請重新設定")
        return rn.Target(ip=h["ip"], platform="windows", hostname=h["hostname"], username=cred[0],
                         password=cred[1], cred_name=name)
    acct = cam.account_for_host(conn, h["serial"], h["platform"])
    if acct == "root":
        raise TransferError(f"{h['ip']} 是 root 金鑰主機，不做檔案傳送")
    return rn.Target(ip=h["ip"], platform=h["platform"], hostname=h["hostname"], username=acct,
                     key_path=manage_state.COLLECTOR_KEY_DEFAULT)


def _cred_audit(conn: sqlite3.Connection, t: rn.Target, ok: bool, what: str) -> None:
    if t.cred_name:
        try:
            import credential_store
            credential_store.audit_use(conn, t.cred_name, t.ip, ok, what)
        except Exception:  # noqa: BLE001 - 稽核寫不進去不可以反過來讓傳送失敗；傳送本身另有 ft_job_host 紀錄
            pass


# ====================================================================== 預檢與估算
def _verdict_dict(size: int) -> dict:
    now = pol.now()
    v = pol.verdict(size, now)
    nxt = pol.next_start(size, now) if (not v.ok or not pol.may_start_new_wave(size, now)) else None
    if v.ok and nxt is not None:
        v = pol.Verdict(False, "平日 08:15 之後不再開新的一波（已在傳的會傳完）", nxt)
    return {"ok": v.ok, "why": v.why, "next_at": st.now_str(nxt) if nxt else None, "now": st.now_str(now)}


def preflight(conn: sqlite3.Connection, file_id: int, ips: list[str], folder: str | None,
              concurrency=None, rate_mbps=None, runner: rn.TransferRunner | None = None) -> dict:
    f = st.get_file(conn, file_id)
    if f is None:
        raise TransferError("檔案庫裡找不到這個檔案", 404)
    hosts = resolve_hosts(conn, ips)
    box = hosts[0]["box"]
    folder = pol.check_folder(hosts[0]["platform"], folder or pol.default_folder(hosts[0]["platform"])) \
        if box == "windows" else pol.check_folder("linux", folder or "/tmp")
    s = st.get_settings(conn)
    est = pol.estimate(f["size"], len(hosts), len({h["room"] for h in hosts}), pol.default_concurrency(box, concurrency),
                       rate_mbps or pol.DEFAULT_RATE_MBPS, int(s["ft_wave_pause_s"]))
    out = {"file": {"id": f["id"], "name": f["name"], "size": f["size"]}, "box": box, "folder": folder,
           "hosts": len(hosts), "estimate": est, "verdict": _verdict_dict(f["size"]),
           "problems": [], "checked": False, "windows_unlimited": box == "windows"}
    if len(hosts) > PREFLIGHT_MAX_HOSTS:
        return out
    runner = runner or rn.get_runner()
    # 憑證在主執行緒先解好（SQLite 連線不給執行緒池同時用）；執行緒池裡只做遠端探測
    jobs, problems = [], []
    for h in hosts:
        try:
            jobs.append((h, make_target(conn, h)))
        except TransferError as exc:
            problems.append({"ip": h["ip"], "hostname": h["hostname"], "message": str(exc)})

    def one(pair):
        h, t = pair
        try:
            p = runner.probe(t, folder, f["name"])
        except rn.RunnerError as exc:
            return {"ip": h["ip"], "hostname": h["hostname"], "message": str(exc)}
        msg = _precheck_message(p, folder, f["size"])
        return {"ip": h["ip"], "hostname": h["hostname"], "message": msg} if msg else None

    with _cf.ThreadPoolExecutor(max_workers=6) as pool:
        problems += [x for x in pool.map(one, jobs) if x]
    out["problems"] = problems
    out["checked"] = True
    return out


def _precheck_message(p: dict, folder: str, size: int) -> str | None:
    if not p.get("dir_exists"):
        return f"沒有 {folder} 資料夾（不會自動建立）"
    fb = p.get("free_bytes")
    need = int(size * pol.SPACE_FACTOR)
    if fb is not None and fb < need:
        return f"{folder} 可用空間不足（剩 {pol.fmt_size(fb)}，需 {pol.fmt_size(need)}）"
    return None


# ====================================================================== 建立傳送
def active_job_count(conn: sqlite3.Connection) -> int:
    """正在占用頻寬的傳送數（排隊中＋執行中；等待時段的還沒開始，不算）。"""
    return conn.execute("SELECT COUNT(*) FROM ft_job WHERE status IN ('queued', 'running')").fetchone()[0]


def waiting_job_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM ft_job WHERE status = 'waiting_window'").fetchone()[0]


def check_capacity(conn: sqlite3.Connection, n_hosts: int, settings: dict | None = None, will_wait: bool = False) -> None:
    """全域上限（審查 HIGH 3／M2）：同時進行的傳送數、等待時段的傳送數、一次傳送的台數。超過回 429＋清楚的訊息。
    會排進「等待允許時段」的傳送算等待那一格（預設 20），其餘算進行中那一格（預設 2）。"""
    s = settings or st.get_settings(conn)
    if n_hosts > int(s["ft_max_hosts_job"]):
        raise TransferError(f"一次傳送最多 {int(s['ft_max_hosts_job'])} 台（你選了 {n_hosts} 台），請分批", 429)
    if will_wait:
        if waiting_job_count(conn) >= int(s["ft_max_waiting_jobs"]):
            raise TransferError(f"等待允許時段的傳送最多 {int(s['ft_max_waiting_jobs'])} 個，請先處理或停止現有的", 429)
    elif active_job_count(conn) >= int(s["ft_max_active_jobs"]):
        raise TransferError(f"同時最多 {int(s['ft_max_active_jobs'])} 個傳送在進行，請等目前的傳送結束再送", 429)


_create_lock = threading.Lock()


@contextlib.contextmanager
def capacity_txn(conn: sqlite3.Connection, n_hosts: int, settings: dict | None = None, will_wait: bool = False):
    """「檢查名額＋寫入傳送」在同一把行程鎖與同一個 BEGIN IMMEDIATE 交易裡做（審查 M2）：
    多個請求同時進來時不會各自看到「還有名額」而一起超過上限（IMMEDIATE 連多個服務行程也序列化）。"""
    with _create_lock:
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            check_capacity(conn, n_hosts, settings, will_wait)
            yield
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def create_upload_job(conn: sqlite3.Connection, user: str, file_id: int, ips: list[str], folder: str | None,
                      concurrency=None, rate_mbps=None, overwrite: bool = False,
                      confirm_count: int | None = None, confirm_big: bool = False) -> dict:
    f = st.get_file(conn, file_id)
    if f is None:
        raise TransferError("檔案庫裡找不到這個檔案", 404)
    hosts = resolve_hosts(conn, ips)
    if confirm_count != len(hosts):
        raise TransferError(f"請輸入台數 {len(hosts)} 以確認")
    platform0 = hosts[0]["platform"]
    box = hosts[0]["box"]
    folder = pol.check_folder("windows" if box == "windows" else "linux", folder or pol.default_folder(platform0))
    pol.check_name(platform0, f["name"])
    s = st.get_settings(conn)
    conc, rate = pol.default_concurrency(box, concurrency), pol.clamp_rate(rate_mbps)
    est = pol.estimate(f["size"], len(hosts), len({h["room"] for h in hosts}), conc, rate, int(s["ft_wave_pause_s"]))
    if est["needs_second_confirm"] and not confirm_big:
        raise TransferError(f"總量 {pol.fmt_size(est['total_bytes'])} 超過 20 GB，需要二次確認", 409)
    now = pol.now()
    ok_now = pol.allowed_at(f["size"], now) and pol.may_start_new_wave(f["size"], now)
    nxt = None if ok_now else pol.next_start(f["size"], now)
    with capacity_txn(conn, len(hosts), s, will_wait=not ok_now):
        cur = conn.execute(
            "INSERT INTO ft_job (kind, box, file_id, file_name, size, dest_folder, status, created_by, created_at, "
            "not_before, concurrency, rate_mbps, overwrite, rooms, est_minutes, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("upload", box, f["id"], f["name"], f["size"], folder, "queued" if ok_now else "waiting_window", user,
             st.now_str(now), st.now_str(nxt) if nxt else None, conc, rate, 1 if overwrite else 0,
             len({h["room"] for h in hosts}), est["minutes"],
             None if ok_now else f"等待允許時段，{st.now_str(nxt)} 起自動開始"))
        job_id = cur.lastrowid
        for i, h in enumerate(hosts, 1):
            conn.execute("INSERT INTO ft_job_host (job_id, seq, ip, asset_serial, hostname, platform, room, status) "
                         "VALUES (?,?,?,?,?,?,?,'queued')",
                         (job_id, i, h["ip"], h["serial"], h["hostname"], h["platform"], h["room"]))
    st.audit(conn, user, "job_create",
             f"傳送 #{job_id}：{f['name']}（{f['size']} 位元組）→ {len(hosts)} 台 {box}，{folder}"
             + ("" if ok_now else f"；排到 {st.now_str(nxt)}"))
    if ok_now:
        start_job(_db_path(conn), job_id)
    return job_detail(conn, job_id)


def start_job(db_path: Path, job_id: int) -> bool:
    """開一條背景執行緒做這個傳送；已經在做就不重複開。"""
    with _active_lock:
        if job_id in _active_jobs:
            return False
        _active_jobs.add(job_id)

    def wrap():
        try:
            run_job(db_path, job_id)
        finally:
            with _active_lock:
                _active_jobs.discard(job_id)

    spawn(wrap)
    return True


# ====================================================================== 背景：傳送
def _get_job(conn, job_id):
    return conn.execute("SELECT * FROM ft_job WHERE id = ?", (job_id,)).fetchone()


def _stop_requested(conn, job_id) -> bool:
    r = conn.execute("SELECT stop_requested FROM ft_job WHERE id = ?", (job_id,)).fetchone()
    return bool(r and r["stop_requested"])


def _set_host(conn, host_id: int, **cols) -> None:
    sets = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(f"UPDATE ft_job_host SET {sets} WHERE id = ?", (*cols.values(), host_id))
    conn.commit()


def run_job(db_path: Path, job_id: int, runner: rn.TransferRunner | None = None) -> None:
    conn = _open(db_path)
    try:
        _run_job(conn, db_path, job_id, runner or rn.get_runner())
    except Exception as exc:  # noqa: BLE001 - 背景執行緒最後一道網：整個傳送出事要留下原因，不能讓畫面永遠停在「進行中」
        conn.execute("UPDATE ft_job SET status = 'failed', finished_at = ?, note = ? WHERE id = ?",
                     (st.now_str(), f"傳送程序發生未預期的錯誤：{type(exc).__name__}: {exc}"[:500], job_id))
        conn.execute("UPDATE ft_job_host SET status = 'failed', message = '傳送程序中斷', finished_at = ? "
                     "WHERE job_id = ? AND status IN ('queued', 'running')", (st.now_str(), job_id))
        conn.commit()
    finally:
        conn.close()


def _run_job(conn: sqlite3.Connection, db_path: Path, job_id: int, runner: rn.TransferRunner) -> None:
    job = _get_job(conn, job_id)
    if job is None or job["status"] not in ("queued", "waiting_window", "running"):
        return
    if block_reason(conn, job["created_by"]):
        conn.execute("UPDATE ft_job SET status = 'waiting_window', note = ? WHERE id = ?",
                     ("功能模組停用或登入停用中，暫不執行（重新啟用後會自動繼續）", job_id))
        conn.commit()
        return
    conn.execute("UPDATE ft_job SET status = 'running', started_at = COALESCE(started_at, ?), note = NULL WHERE id = ?",
                 (st.now_str(), job_id))
    conn.commit()
    size = job["size"]
    kind = job["kind"]
    pending = [r["id"] for r in conn.execute(
        "SELECT id FROM ft_job_host WHERE job_id = ? AND status = 'queued' ORDER BY seq", (job_id,))]
    s = st.get_settings(conn)
    waves = pol.waves(pending) if kind == "upload" else [pending]
    for wi, wave in enumerate(waves):
        if _stop_requested(conn, job_id):
            break
        why = block_reason(conn, job["created_by"])                       # 每一波開始前再問一次
        if why:
            return _stop_all(conn, job_id, why)
        now = pol.now()
        if kind == "upload" and not (pol.allowed_at(size, now) and pol.may_start_new_wave(size, now)):
            return _park(conn, job_id, size)
        wave = _drop_ineligible(conn, wave)                               # 排隊很久才開始的主機：VMware／未納管等資格重驗
        if kind == "upload":
            with _cf.ThreadPoolExecutor(max_workers=pol.clamp_concurrency(job["concurrency"])) as pool:
                list(pool.map(lambda hid: _host_task(db_path, job_id, hid, runner), wave))
        else:
            for hid in wave:
                _host_task(db_path, job_id, hid, runner)
        if wi < len(waves) - 1:
            _pause(conn, job_id, int(s["ft_wave_pause_s"]))
    _finish(conn, job_id)


def _stop_all(conn: sqlite3.Connection, job_id: int, why: str) -> None:
    """中途不允許再動（模組停用、登入停用…）：還沒開始的台全標「已停止」並寫原因，傳送收尾成 stopped。"""
    conn.execute("UPDATE ft_job_host SET status = 'stopped', message = ?, finished_at = ? WHERE job_id = ? AND status = 'queued'",
                 (f"已停止：{why}", st.now_str(), job_id))
    c = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM ft_job_host WHERE job_id = ? GROUP BY status", (job_id,))}
    conn.execute("UPDATE ft_job SET status = 'stopped', finished_at = ?, note = ? WHERE id = ?",
                 (st.now_str(), f"已停止：{why}（已完成 {c.get('ok', 0)} 台，未開始的台不再送）", job_id))
    conn.commit()


def _drop_ineligible(conn: sqlite3.Connection, host_ids: list[int]) -> list[int]:
    """這一波的主機再驗一次資格（和選主機時同一套：已納管、現役、不是 VMware…）。不合格的標失敗（不傳）。"""
    if not host_ids:
        return host_ids
    ok_ips = {h["ip"] for h in list_hosts(conn)["hosts"]}
    keep = []
    for hid in host_ids:
        r = conn.execute("SELECT ip FROM ft_job_host WHERE id = ?", (hid,)).fetchone()
        if r and r["ip"] in ok_ips:
            keep.append(hid)
        else:
            _set_host(conn, hid, status="failed", finished_at=st.now_str(),
                      message="開始前重新檢查：不在可選主機內（VMware 設備、已取消納管、已退役或平台不明），沒有傳")
    return keep


def _pause(conn, job_id: int, seconds: int) -> None:
    for _ in range(max(0, seconds)):
        if _stop_requested(conn, job_id):
            return
        _sleep(1)


def _park(conn, job_id: int, size: int) -> None:
    nxt = pol.next_start(size, pol.now())
    conn.execute("UPDATE ft_job SET status = 'waiting_window', not_before = ?, note = ? WHERE id = ?",
                 (st.now_str(nxt), f"等待允許時段，{st.now_str(nxt)} 起自動繼續", job_id))
    conn.commit()


def _finish(conn: sqlite3.Connection, job_id: int) -> None:
    job = _get_job(conn, job_id)
    stop = bool(job["stop_requested"])
    if stop:
        conn.execute("UPDATE ft_job_host SET status = 'stopped', message = '已停止（未開始）', finished_at = ? "
                     "WHERE job_id = ? AND status = 'queued'", (st.now_str(), job_id))
        conn.commit()
    c = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM ft_job_host WHERE job_id = ? GROUP BY status", (job_id,))}
    if not stop and c.get("queued"):
        return _park(conn, job_id, job["size"])
    ok, bad = c.get("ok", 0), c.get("failed", 0)
    status = "stopped" if (stop or c.get("stopped")) else ("done" if bad == 0 else ("failed" if ok == 0 else "partial"))
    note = None
    if status == "stopped" and not stop:                       # 被閘門停掉（不是人按的停止）：把原因帶到傳送上
        r = conn.execute("SELECT message FROM ft_job_host WHERE job_id = ? AND status = 'stopped' LIMIT 1", (job_id,)).fetchone()
        note = (r["message"] if r else None) or "已停止"
    conn.execute("UPDATE ft_job SET status = ?, finished_at = ?, note = COALESCE(?, note) WHERE id = ?",
                 (status, st.now_str(), note, job_id))
    conn.commit()


def _host_task(db_path: Path, job_id: int, host_id: int, runner: rn.TransferRunner) -> None:
    """一台一條連線（SQLite 連線不跨執行緒共用寫入）。任何例外都接住寫成這台的失敗原因。"""
    conn = _open(db_path)
    try:
        job = _get_job(conn, job_id)
        h = conn.execute("SELECT * FROM ft_job_host WHERE id = ?", (host_id,)).fetchone()
        if h is None or h["status"] != "queued":
            return
        if _stop_requested(conn, job_id):
            return                                                    # 留在 queued，_finish 會標成已停止
        why = block_reason(conn, job["created_by"])                   # 每一台開始前再問一次
        if why:
            _set_host(conn, host_id, status="stopped", message=f"已停止：{why}", finished_at=st.now_str())
            return
        if not pol.allowed_at(job["size"], pol.now()) and job["kind"] == "upload":
            return                                                    # 時段中途關了：這台留在 queued，等下一個時段
        _set_host(conn, host_id, status="running", started_at=st.now_str(), pct=3, message=None)
        target = None
        try:
            info = {"ip": h["ip"], "serial": h["asset_serial"], "hostname": h["hostname"], "platform": h["platform"]}
            target = make_target(conn, info)
            if job["kind"] == "upload":
                _upload_one(conn, runner, target, job, h)
            else:
                _download_one(conn, runner, target, job, h)
            _cred_audit(conn, target, True, "檔案傳送")
        except rn.GateStopped as exc:                                 # 傳到一半被叫停：標已停止（暫存檔已清），不是失敗
            _set_host(conn, host_id, status="stopped", message=str(exc)[:400], finished_at=st.now_str())
        except (rn.RunnerError, TransferError, st.LibraryError, pol.PolicyError) as exc:
            _set_host(conn, host_id, status="failed", message=str(exc)[:400], finished_at=st.now_str())
            if target is not None:
                _cred_audit(conn, target, False, "檔案傳送失敗")
        except Exception as exc:  # noqa: BLE001
            _set_host(conn, host_id, status="failed", finished_at=st.now_str(),
                      message=f"未預期的錯誤：{type(exc).__name__}: {exc}"[:400])
    finally:
        conn.close()


def _upload_one(conn, runner: rn.TransferRunner, t: rn.Target, job, h) -> None:
    f = st.get_file(conn, job["file_id"])
    if f is None:
        raise TransferError("檔案庫裡的檔案已被刪除")
    folder, name, size = job["dest_folder"], job["file_name"], job["size"]
    pol.check_folder("windows" if t.is_windows else "linux", folder)
    pol.check_name(t.platform, name)
    local = st.stored_path(conn, f)
    if not local.exists() or local.stat().st_size != size:
        raise TransferError("檔案庫裡的檔案已遺失或被改變")
    hid = h["id"]
    remote = pol.remote_path(t.platform if t.is_windows else "linux", folder, name)
    p = runner.probe(t, folder, name)                      # Linux／AIX：VMware 守門句在腳本裡，ESXi／vCenter 在這裡就被擋下
    _set_host(conn, hid, pct=10)
    bad = _precheck_message(p, folder, size)
    if bad:
        raise TransferError(bad)
    if p.get("exists"):
        if p.get("symlink"):
            raise TransferError("目的地同名項目是符號連結，不處理")
        if p.get("size") == size and runner.sha256(t, folder, name) == f["sha256"]:
            _set_host(conn, hid, status="ok", pct=100, remote_path=remote, remote_sha256=f["sha256"],
                      message="已存在且內容相同，略過", finished_at=st.now_str())
            return
        if not job["overwrite"]:
            raise TransferError(f"目的地已有同名檔案（{p.get('size')} 位元組）；要覆蓋請勾選「覆蓋同名檔案」")
    part = rn.part_name(job["id"], h["seq"])
    if not runner.part_free(t, folder, part):
        raise TransferError("暫存檔名被佔用，請重試")
    _set_host(conn, hid, pct=20)
    if _stop_requested(conn, job["id"]):
        raise TransferError("已停止，這台沒有開始傳")
    def gate():
        return block_reason(conn, job["created_by"])          # 傳送途中（Windows 每約 1 MB、scp 開始前與傳完後）再問閘門

    try:
        runner.upload(t, local, folder, part, None if t.is_windows else job["rate_mbps"], gate)
        _set_host(conn, hid, pct=80)
        why = gate()
        if why:
            raise rn.GateStopped(f"已停止：{why}")
        res = runner.finalize(t, folder, part, name, f["sha256"], bool(job["overwrite"]))
    except rn.RunnerError:
        _best_effort_remove(runner, t, folder, part)
        raise
    r = res.get("result")
    if r == "ok" and res.get("sha256") == f["sha256"]:
        _set_host(conn, hid, status="ok", pct=100, remote_path=remote, remote_sha256=res["sha256"],
                  message="雜湊相符", finished_at=st.now_str())
        return
    why = {"mismatch": "SHA256 不符（傳輸過程檔案被改變），已刪掉暫存檔，目的地沒有留下檔案",
           "exists": "目的地已有同名檔案，沒有覆蓋", "isdir": "目的地同名項目是資料夾，不處理", "symlink": "目的地是符號連結，不處理",
           "missing": "傳完卻找不到暫存檔", "mvfail": "雜湊相符但改名失敗（暫存檔留在目的地，名稱結尾 .webit3part）"}
    raise TransferError(why.get(r, f"收尾失敗（{r}）"))


def _best_effort_remove(runner, t, folder, part) -> None:
    try:
        runner.remove_part(t, folder, part)
    except Exception:  # noqa: BLE001 - 清暫存檔是補救；清不掉不蓋過原本的失敗原因（暫存檔名稱固定、殘留清單可看）
        pass


def _download_one(conn, runner: rn.TransferRunner, t: rn.Target, job, h) -> None:
    folder, name = job["dest_folder"], job["file_name"]
    hid = h["id"]
    tmp_dir = st.library_dir(conn) / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / uuid.uuid4().hex
    try:
        why = block_reason(conn, job["created_by"])
        if why:
            raise TransferError(f"已停止：{why}")
        p = runner.probe(t, folder, name)
        if not p.get("exists") or p.get("symlink") or p.get("size") is None:
            raise TransferError("主機上找不到這個檔案（或它是符號連結）")
        size = p["size"]
        v = pol.verdict(size, pol.now())
        if not v.ok:
            raise TransferError(f"現在不能抓：{v.why}（下一個允許時間 {st.now_str(v.next_at)}）")
        room, info = st.room_for_new_file(conn)
        if size > room:
            raise TransferError("檔案庫空間不足、超過單檔上限或磁碟剩餘不足，抓回已取消")
        _set_host(conn, hid, pct=20)
        # 拉之前再看一次遠端大小（探測到現在可能被改過），並問一次閘門；接收途中硬性上限＝room
        p2 = runner.probe(t, folder, name)
        if p2.get("size") != size or p2.get("symlink"):
            raise TransferError("遠端檔案在抓回前被改動（大小或型態變了），已取消")
        why = block_reason(conn, job["created_by"])
        if why:
            raise TransferError(f"已停止：{why}")
        runner.download(t, folder, name, tmp, None if t.is_windows else job["rate_mbps"], room,
                        check=lambda: block_reason(conn, job["created_by"]))      # 接收途中每約 1 秒再問閘門
        if tmp.stat().st_size > room or tmp.stat().st_size != size:
            raise TransferError("抓回的檔案大小和預期不符（或超過上限），已丟棄")
        _set_host(conn, hid, pct=70)
        local_sha = st.sha256_file(tmp)
        remote_sha = runner.sha256(t, folder, name)
        if local_sha != remote_sha:
            raise TransferError("SHA256 不符（抓回的檔案和主機上的不一樣），已丟棄")
        row = st.adopt_local_file(conn, name, tmp, local_sha, tmp.stat().st_size, job["created_by"], t.ip,
                                  pol.remote_path(t.platform if t.is_windows else "linux", folder, name))
        conn.execute("UPDATE ft_job SET size = ?, file_id = ? WHERE id = ?", (row["size"], row["id"], job["id"]))
        conn.commit()
        _set_host(conn, hid, status="ok", pct=100, remote_sha256=local_sha, remote_path=pol.remote_path(
            t.platform if t.is_windows else "linux", folder, name), message="已抓回檔案庫（雜湊相符）",
            finished_at=st.now_str())
    finally:
        tmp.unlink(missing_ok=True)


# ====================================================================== 抓回
def create_pull(conn: sqlite3.Connection, user: str, ip: str, folder: str | None, name: str,
                runner: rn.TransferRunner | None = None) -> dict:
    h = resolve_hosts(conn, [ip])[0]
    platform = "windows" if h["box"] == "windows" else "linux"
    folder = pol.check_folder(platform, folder or pol.default_folder(h["platform"]))
    pol.check_name(h["platform"], name)
    runner = runner or rn.get_runner()
    try:
        t = make_target(conn, h)
        p = runner.probe(t, folder, name)
    except rn.RunnerError as exc:
        raise TransferError(str(exc), 502) from exc
    if not p.get("exists") or p.get("symlink") or p.get("size") is None:
        raise TransferError("主機上找不到這個檔案（或它是符號連結）", 404)
    size = p["size"]
    v = pol.verdict(size, pol.now())
    if not v.ok:
        raise TransferError(f"現在不能抓：{v.why}。下一個允許時間 {st.now_str(v.next_at)}", 409)
    room, info = st.room_for_new_file(conn)
    if size > info["file_cap"]:
        raise TransferError(f"超過單一檔案上限 {pol.fmt_size(info['file_cap'])}")
    if size > room:
        raise TransferError(f"檔案庫空間不足（總量上限 {pol.fmt_size(info['cap'])}，已用 {pol.fmt_size(info['used'])}，或磁碟剩餘不足）")
    if conn.execute("SELECT 1 FROM ft_file WHERE name = ? AND deleted_at IS NULL", (name,)).fetchone():
        raise TransferError(f"檔案庫已經有「{name}」，請先刪除舊的或改名", 409)
    with capacity_txn(conn, 1):
        cur = conn.execute(
            "INSERT INTO ft_job (kind, box, file_name, size, dest_folder, status, created_by, created_at, concurrency, rate_mbps, rooms) "
            "VALUES ('download', ?, ?, ?, ?, 'queued', ?, ?, 1, ?, 1)",
            (h["box"], name, size, folder, user, st.now_str(), pol.DEFAULT_RATE_MBPS))
        job_id = cur.lastrowid
        conn.execute("INSERT INTO ft_job_host (job_id, seq, ip, asset_serial, hostname, platform, room, status) "
                     "VALUES (?,1,?,?,?,?,?,'queued')", (job_id, h["ip"], h["serial"], h["hostname"], h["platform"], h["room"]))
    st.audit(conn, user, "pull_create", f"抓回 #{job_id}：{h['ip']} {folder} {name}（{size} 位元組）")
    start_job(_db_path(conn), job_id)
    return job_detail(conn, job_id)


# ====================================================================== 停止／重送／重新驗證
def stop_job(conn: sqlite3.Connection, user: str, job_id: int) -> dict:
    j = _get_job(conn, job_id)
    if j is None:
        raise TransferError("查無此傳送", 404)
    if j["status"] in ("done", "failed", "partial", "stopped"):
        raise TransferError("這個傳送已經結束了")
    conn.execute("UPDATE ft_job SET stop_requested = 1 WHERE id = ?", (job_id,))
    conn.commit()
    st.audit(conn, user, "job_stop", f"停止傳送 #{job_id}（未開始的台不再送；已在傳的會傳完）")
    if j["status"] == "waiting_window":
        _finish(conn, job_id)                                  # 沒有人在跑，直接收尾
    return job_detail(conn, job_id)


def retry_failed(conn: sqlite3.Connection, user: str, job_id: int) -> dict:
    j = _get_job(conn, job_id)
    if j is None or j["kind"] != "upload":
        raise TransferError("查無此傳送", 404)
    if j["status"] in ("queued", "running", "waiting_window"):
        raise TransferError("這個傳送還沒結束")
    ips = [r["ip"] for r in conn.execute(
        "SELECT ip FROM ft_job_host WHERE job_id = ? AND status IN ('failed', 'stopped') ORDER BY seq", (job_id,))]
    if not ips:
        raise TransferError("沒有失敗或停止的台可以重送")
    out = create_upload_job(conn, user, j["file_id"], ips, j["dest_folder"], j["concurrency"], j["rate_mbps"],
                            bool(j["overwrite"]), confirm_count=len(ips), confirm_big=True)
    conn.execute("UPDATE ft_job SET note = COALESCE(note, '') || ? WHERE id = ?", (f"（重送 #{job_id} 失敗的 {len(ips)} 台）", out["id"]))
    conn.commit()
    return job_detail(conn, out["id"])


def start_reverify(conn: sqlite3.Connection, user: str, job_id: int, with_hash: bool) -> dict:
    j = _get_job(conn, job_id)
    if j is None or j["kind"] != "upload":
        raise TransferError("查無此傳送", 404)
    if with_hash:
        v = pol.verdict(j["size"], pol.now())
        if not v.ok:
            raise TransferError(f"現在不能重算雜湊：{v.why}（雜湊要讀完整個檔案，遵守同樣的時段規則）。"
                                f"下一個允許時間 {st.now_str(v.next_at)}", 409)
    st.audit(conn, user, "reverify", f"重新驗證 #{job_id}" + ("（含雜湊）" if with_hash else "（只查在不在、大小、時間）"))
    spawn(run_reverify, _db_path(conn), job_id, with_hash)
    return job_detail(conn, job_id)


def run_reverify(db_path: Path, job_id: int, with_hash: bool, runner: rn.TransferRunner | None = None) -> None:
    runner = runner or rn.get_runner()
    conn = _open(db_path)
    try:
        if not background_allowed(conn):
            return
        j = _get_job(conn, job_id)
        hosts = conn.execute("SELECT * FROM ft_job_host WHERE job_id = ? AND status = 'ok' ORDER BY seq", (job_id,)).fetchall()
        f = st.get_file(conn, j["file_id"]) if j and j["file_id"] else None
        want_sha = f["sha256"] if f else None

        def one(h):
            c = _open(db_path)
            try:
                try:
                    t = make_target(c, {"ip": h["ip"], "serial": h["asset_serial"], "hostname": h["hostname"],
                                        "platform": h["platform"]})
                    p = runner.probe(t, j["dest_folder"], j["file_name"])
                    if not p.get("exists"):
                        state, detail = "gone", "檔案已不在"
                    else:
                        state, detail = "exists", f"還在・{pol.fmt_size(p.get('size') or 0)}・{p.get('mtime') or ''}".strip("・")
                        if p.get("size") != j["size"]:
                            state, detail = "changed", f"大小變了（現在 {p.get('size')} 位元組，原本 {j['size']}）"
                        elif with_hash and want_sha:
                            if runner.sha256(t, j["dest_folder"], j["file_name"]) != want_sha:
                                state, detail = "changed", "內容雜湊和原本不同"
                            else:
                                detail += "・雜湊相符"
                except (rn.RunnerError, TransferError) as exc:
                    state, detail = "error", str(exc)[:200]
                except Exception as exc:  # noqa: BLE001
                    state, detail = "error", f"未預期的錯誤：{type(exc).__name__}: {exc}"[:200]
                c.execute("UPDATE ft_job_host SET verify_state = ?, verify_detail = ?, verified_at = ? WHERE id = ?",
                          (state, detail, st.now_str(), h["id"]))
                c.commit()
            finally:
                c.close()

        with _cf.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one, hosts))
    finally:
        conn.close()


# ====================================================================== 指令
def start_command(conn: sqlite3.Connection, user: str, box: str, ips: list[str], command: str,
                  folder: str | None = None) -> dict:
    cmd = (command or "").strip("\r\n")
    if not cmd.strip():
        raise TransferError("沒有輸入指令")
    if len(cmd) > COMMAND_MAX_LEN or "\x00" in cmd:
        raise TransferError("指令太長或含不合法字元")
    if box not in ("windows", "linux"):
        raise TransferError("箱子只能是 windows 或 linux")
    hosts = resolve_hosts(conn, ips, box)
    s = st.get_settings(conn)
    if len(hosts) > int(s["ft_max_hosts_cmd"]):
        raise TransferError(f"一次指令最多 {int(s['ft_max_hosts_cmd'])} 台（你選了 {len(hosts)} 台），請分批", 429)
    fol = pol.check_folder("windows" if box == "windows" else "linux", folder or pol.default_folder(hosts[0]["platform"]))
    with _cmd_lock:
        if _cmd_pending[0] + len(hosts) > CMD_PENDING_MAX:
            raise TransferError("目前還有太多台的指令在排隊執行，請稍後再下", 429)
        _cmd_pending[0] += len(hosts)
    log_id = None
    try:
        log_hosts = [{"ip": h["ip"], "hostname": h["hostname"], "folder": fol} for h in hosts]
        log_id = st.cmd_log_start(conn, user, box, log_hosts, cmd)       # 先記下「要執行」，再動手：跑到一半掛掉也有紀錄
        st.audit(conn, user, "command", f"指令 #{log_id}：{box} {len(hosts)} 台")
        spawn(_command_worker, _db_path(conn), log_id, hosts, fol, cmd, None, user)
    except BaseException:
        # 任何一步失敗（記錄、稽核、開執行緒）：這批排隊台數要還回去。worker 一旦接手（登記在 _cmd_taken）就由它自己還。
        with _cmd_lock:
            if log_id is None or log_id not in _cmd_taken:
                _cmd_pending[0] = max(0, _cmd_pending[0] - len(hosts))
        raise
    return {"log_id": log_id, "hosts": len(hosts)}


_cmd_taken: set = set()          # worker 已接手、自己負責還排隊台數的指令（log_id）


def _command_worker(db_path: Path, log_id: int, hosts: list[dict], folder: str, command: str,
                    runner: rn.TransferRunner | None = None, by_user: str | None = None) -> None:
    with _cmd_lock:
        _cmd_taken.add(log_id)
    state = [0]                                         # 已交給執行緒池的台數（沒交出去的，計數由這裡還）
    try:
        _command_worker_run(db_path, log_id, hosts, folder, command, runner, state, by_user)
    finally:
        with _cmd_lock:
            _cmd_pending[0] = max(0, _cmd_pending[0] - (len(hosts) - state[0]))
            _cmd_taken.discard(log_id)


def _command_worker_run(db_path, log_id, hosts, folder, command, runner, state, by_user=None) -> None:
    runner = runner or rn.get_runner()
    try:
        c0 = _open(db_path)
        try:
            if not by_user:                                                 # 沒帶建立者就從不可改的指令紀錄讀（不再寫死 admin）
                r0 = c0.execute("SELECT by_user FROM ft_cmd_log WHERE id = ?", (log_id,)).fetchone()
                by_user = r0["by_user"] if r0 else None
            eligible = {x["ip"] for x in list_hosts(c0)["hosts"]}          # 排隊後才開始：資格（VMware 等）重驗
        finally:
            c0.close()
    except Exception:  # noqa: BLE001 - 查不出來就當全部不合格（fail-closed），每台的結果列會講原因
        eligible = set()

    def one(h):
        c = None
        host = {"ip": h["ip"], "hostname": h["hostname"], "folder": folder}
        try:
            c = _open(db_path)                                              # 開連線也在 try 裡：失敗時計數照樣會還
            ok, rc, out = False, None, ""
            try:
                why = block_reason(c, by_user)                              # 建立者「現在」還是不是有效 admin，每一台都重讀
                if why:
                    raise TransferError(f"已停止：{why}，沒有執行")
                if h["ip"] not in eligible:
                    raise TransferError("開始前重新檢查：不在可選主機內（VMware 設備、已取消納管、已退役或平台不明），沒有執行")
                t = make_target(c, h)
                r = runner.run_command(t, folder, command, COMMAND_TIMEOUT_S)
                rc, out = r.get("rc"), r.get("output") or ""
                ok = rc in (0, None)
                _cred_audit(c, t, ok, "檔案傳送：指令")
            except rn.VmwareRefused as exc:
                out = f"已拒絕（天條）：{exc}"
            except (rn.RunnerError, TransferError, pol.PolicyError) as exc:
                out = f"執行失敗：{exc}"
            except Exception as exc:  # noqa: BLE001
                out = f"執行失敗（未預期的錯誤）：{type(exc).__name__}: {exc}"
            st.cmd_log_result(c, log_id, host, ok, rc, out)             # 成功或失敗都記，不例外
        finally:
            if c is not None:
                c.close()
            with _cmd_lock:
                _cmd_pending[0] = max(0, _cmd_pending[0] - 1)

    # 全系統共用一個執行緒池（不是每個請求各開一個），同時最多 COMMAND_POOL 台在遠端執行
    futures = []
    for h in hosts:
        futures.append(_command_pool().submit(one, h))
        state[0] += 1
    for f in futures:
        f.result()


# ====================================================================== 讀取
def job_summary_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT j.*, "
        "(SELECT COUNT(*) FROM ft_job_host h WHERE h.job_id = j.id) AS n, "
        "(SELECT COUNT(*) FROM ft_job_host h WHERE h.job_id = j.id AND h.status = 'ok') AS ok_count, "
        "(SELECT COUNT(*) FROM ft_job_host h WHERE h.job_id = j.id AND h.status = 'failed') AS fail_count, "
        "(SELECT COUNT(*) FROM ft_job_host h WHERE h.job_id = j.id AND h.status = 'running') AS running_count, "
        "(SELECT COUNT(*) FROM ft_job_host h WHERE h.job_id = j.id AND h.status = 'queued') AS queued_count "
        "FROM ft_job j ORDER BY j.id DESC LIMIT 500").fetchall()
    return [dict(r) for r in rows]


def job_detail(conn: sqlite3.Connection, job_id: int) -> dict:
    j = _get_job(conn, job_id)
    if j is None:
        raise TransferError("查無此傳送", 404)
    d = dict(j)
    hosts = [dict(r) for r in conn.execute("SELECT * FROM ft_job_host WHERE job_id = ? ORDER BY seq", (job_id,))]
    d["hosts"] = hosts
    c = {}
    for h in hosts:
        c[h["status"]] = c.get(h["status"], 0) + 1
    d.update(n=len(hosts), ok_count=c.get("ok", 0), fail_count=c.get("failed", 0),
             running_count=c.get("running", 0), queued_count=c.get("queued", 0))
    return d


def residue(conn: sqlite3.Connection) -> list[dict]:
    """我們留在主機上的檔案：傳送成功、而且還沒被「重新驗證」確認已刪除。同一台同一路徑只留最新一筆。
    「已存在且內容相同，略過」的不是我們放的，不列。"""
    seen, out = set(), []
    for r in conn.execute(
            "SELECT h.id, h.job_id, h.ip, h.hostname, h.platform, h.remote_path, h.finished_at, h.verify_state, h.verified_at, "
            "j.file_name, j.size FROM ft_job_host h JOIN ft_job j ON j.id = h.job_id "
            "WHERE j.kind = 'upload' AND h.status = 'ok' AND COALESCE(h.message, '') NOT LIKE '已存在%' "
            "ORDER BY h.id DESC"):
        k = (r["ip"], r["remote_path"])
        if k in seen:
            continue
        seen.add(k)
        if r["verify_state"] == "gone":
            continue
        out.append(dict(r))
    return out


# ====================================================================== 重啟後收尾／排程
def recover_after_restart(conn: sqlite3.Connection) -> int:
    """服務重啟時還在跑的台：標「服務重啟中斷」，傳送收尾（還有排隊的台就轉成等待，讓 resumer 接手）。"""
    st.ensure_tables(conn)
    jobs = [r["id"] for r in conn.execute("SELECT id FROM ft_job WHERE status = 'running'")]
    for jid in jobs:
        conn.execute("UPDATE ft_job_host SET status = 'failed', message = '服務重啟中斷（可用「重送失敗的」補）', finished_at = ? "
                     "WHERE job_id = ? AND status = 'running'", (st.now_str(), jid))
        conn.commit()
        _finish(conn, jid)
    return len(jobs)


def resume_waiting(db_path: Path) -> int:
    """到了時間的等待傳送自動開始。模組停用或登入停用時不動（留著等重新啟用）。"""
    conn = _open(db_path)
    try:
        if not background_allowed(conn):
            return 0
        n = 0
        cap = int(st.get_settings(conn)["ft_max_active_jobs"])
        for j in conn.execute("SELECT id, size, not_before, status, created_at, created_by FROM ft_job "
                              "WHERE status IN ('waiting_window', 'queued') AND stop_requested = 0 ORDER BY id").fetchall():
            nb = st.parse_ts(j["not_before"])
            if j["status"] == "waiting_window" and nb is not None and nb > pol.now():
                continue
            if block_reason(conn, j["created_by"]):
                continue
            if j["status"] == "waiting_window" and active_job_count(conn) + n >= cap:
                continue                                        # 已有上限數量的傳送在跑：這個再等下一輪
            if j["status"] == "queued":
                created = st.parse_ts(j["created_at"])
                if created is not None and (pol.now() - created) < _dt.timedelta(minutes=2):
                    continue                                    # 剛建立的，執行緒正在起
            if start_job(db_path, j["id"]):
                n += 1
        return n
    finally:
        conn.close()


_resumer_started = False


def start_resumer(db_path_fn) -> None:
    """服務啟動時開一條背景執行緒：每分鐘看一次等待中的傳送；每小時清一次過期的檔案庫檔案。"""
    global _resumer_started
    if _resumer_started:
        return
    _resumer_started = True

    def loop():
        last_purge = 0.0
        while True:
            try:
                p = db_path_fn()
                resume_waiting(p)
                try:                                            # 稽核表外部摘要：到了每小時才驗證＋留存，失敗只記 log
                    import audit_digest
                    c = _open(p)
                    try:
                        audit_digest.scheduled(c)
                    finally:
                        c.close()
                except Exception as exc:  # noqa: BLE001 - 不可拖垮傳送排程迴圈
                    print(f"[audit_digest] 排程呼叫失敗（不影響傳送）：{type(exc).__name__}: {exc}")
                if time.time() - last_purge > 3600:
                    last_purge = time.time()
                    c = _open(p)
                    try:
                        if background_allowed(c):
                            st.purge_expired(c)
                    finally:
                        c.close()
            except Exception as exc:  # noqa: BLE001 - 排程執行緒不可因一次錯誤死掉；錯誤印出來讓 journal 看得到
                print(f"[transfer] resumer 錯誤（下一分鐘重試）：{type(exc).__name__}: {exc}")
            time.sleep(60)

    threading.Thread(target=loop, daemon=True, name="ft-resumer").start()
