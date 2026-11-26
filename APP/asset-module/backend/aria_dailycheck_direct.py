"""VMware 每日檢查快照：系統自己用既有的 vROps 連線每天直抓（2.069.0）。

以前：人在跳板機點 run1 bat 跑 `AI/tools_relay/aria_daily_snap.ps1`、再複製到中繼機點 run2 bat 送中繼，`aria_dailycheck` 再撿進來。
現在：排程每天自己抓，做出跟 `snap.json` 同形狀的資料，走**同一條** `aria_dailycheck.parse` → `apply`，1.5 頁面不用改版面。
跳板機腳本不刪，當備援（切換流程見 docs/設計_每日檢查VMware直抓與備援切換SOP_2026-11-19.md）。

## 天條
只對 vROps suite-api 做 GET（外加登入取權杖、用完釋放權杖）。不連 ESXi／vCenter、不建任何帳號、不寫入 Aria。
網址、驗證來源、CA 憑證、帳密全部沿用「外部整合 › Aria Operations」已設的，這裡不另存一份密碼。

## 時間契約（單一來源）
開始窗／採用截止／「今天是否已完成」全部讀 daily_check 的 vm_batch_from／vm_batch_cutoff，由 `daily_check.vm_window` 解析（1.5 的 vm_pick 用同一個函式）；
空字串或壞值＝預設 06:00／08:00（畫面標「用預設」）。最晚啟動＝採用截止－20 分鐘（秒級：07:40:00 之後不得啟動）；採用截止（08:00:00）是排程執行的**絕對硬截止**：
每個請求的 HTTP timeout 取「剩餘秒數」上限，釋放權杖也受同一個截止約束。過了最晚啟動時間不再啟動，並寫一筆「今日自動抓取結束，請走備援」；沒有「窗外補跑」。
每 20 分鐘試一次（以「開始時間」的固定時槽算）。只在 daily_check 認定的工作日跑。

## 呼叫預算（下沉到 transport，每一次實際 request）
呼叫間隔 DELAY_S＝1.5 秒；MAX_CALLS＝TIME_BOX_S／DELAY_S＝800；TIME_BOX_S＝1200。登入、重新登入、401 後重送、釋放權杖，每一個真的送出的 HTTP 請求都經過
`_GuardedTransport`：檢查開關（被關掉＝人為中止）、呼叫數上限、截止、盤中靜默窗，睡 1.5 秒，才送。

## 共用鎖
跟 vROps 資產匯入共用 vrops_import._run_lock（非阻塞）。**鎖在主執行緒（排程 tick 內）先取得**，再啟動背景工作並把鎖交給 worker，worker 結束才釋放：
資產匯入若在直抓預約鎖之後才輪到，會拿到 busy，不會搶在直抓前面。

## 錯誤分類
只有明確的帳密被拒（登入 401／403）才計入登入保護（一天最多 3 次、最後一次被拒後 60 分鐘不再試）；連線、TLS、逾時、5xx、格式錯誤各自記錄類別、
下一個時槽再試、不計入鎖帳額度。對使用者只回固定文字、類別與 HTTP status，不回 vROps 原始訊息本文。

## 執行紀錄
aria_dc_direct_runs（append-only；保留 ≥400 天，不受批次 30 天清理影響）；開始前先寫 running，每次 tick 最前面無條件把孤兒 running 標 interrupted。
結束寫入失敗：rollback＋health 警告＋回傳註明「稽核紀錄未落盤」，並暫存在記憶體下一輪補寫（補寫前不會被誤標 interrupted）。
aria_dc_direct_audit：開關變更（誰、何時、舊值→新值）。

## 警示等級（刻意設計）
收集層保存「預設三級 ∪ 每日檢查設定的 vm_alert_levels」的超集合；下游（daily_check）會依設定過濾，多存無害。
設定讀不到或 JSON 損毀時**不退回預設**：警示這一類標不完整並寫出原因。
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import aria_dailycheck as ad
import credential_store
import integration_http as ih
import quiet_window as qw
import vrops_import as vi
from db import get_connection, get_setting, set_setting
from vrops_client import VropsError

LOG = logging.getLogger("aria_dailycheck_direct")

ENTRY = "aria_dailycheck_direct_tick"           # quiet_window.ENTRIES 的代號（A 類）
COLLECTOR_VERSION = ad.DIRECT_PREFIX + " 2"
BATCH_PREFIX = ad.BATCH_PREFIX + "direct_"       # ariadc_direct_<時間>

K_ENABLED = ad.K_DIRECT_ENABLED
K_MOUNT = "aria_dc_direct_mount_rels"           # 選用：用關聯算資料存放區掛載主機數（預設關，同腳本的 -MountFromRels）

RETRY_GAP_MIN = 20
AUTH_BACKOFF_MIN = 60
MAX_AUTH_REJECTS_PER_DAY = 3
RUNS_KEEP_DAYS = 400

# ---- 查詢參數（跟腳本預設值一樣）
ADAPTER_KIND = "VMWARE"
CRITICALITY = ("CRITICAL", "IMMEDIATE", "WARNING")      # 預設三級；實際等級 = 這三級 ∪ daily_check 設定的 vm_alert_levels（超集合，下游再過濾）
VCID_NAME = "VMEntityVCID"
HOST_CONN_KEY = "runtime|connectionState"
HOST_MAINT_KEY = "runtime|maintenanceState"
HOST_TIME_KEY = "cpu|usage_average"
DS_TYPE_KEY = "summary|type"
DS_CAP_KEY = "capacity|total_capacity"
DS_FREE_KEY = "capacity|available_space"
DS_HOSTS_KEY = "summary|total_number_hosts"
DS_LOCAL_KEY = "summary|isLocal"
PAGE_SIZE = 500
CHUNK = 50
DELAY_S = 1.5
TIME_BOX_S = 1200
MAX_CALLS = int(TIME_BOX_S / DELAY_S)            # 800：光是間隔就要 1200 秒，兩個上限互相一致
MAX_PAGES = 200
MAX_PAGE_ITEMS = 2000                            # 單頁最多幾筆（伺服器不理 pageSize 時擋下，不讓記憶體爆掉）
MAX_TOTAL_ITEMS = 50000                          # 單一類別累計上限
MIN_HTTP_TIMEOUT_S = 0.5

# 測試可換（不真的睡覺、不真的開執行緒）
SLEEP = time.sleep
CLOCK = time.monotonic
RUN_IN_BACKGROUND = True

_run_lock = vi._run_lock                          # 跟 vROps 資產匯入共用同一把非阻塞鎖：同一時間只有一個在打 vROps
_mutex = threading.Lock()
_state = {"running": False}
_PENDING_END: dict[int, tuple] = {}               # 結束紀錄寫入失敗、等下一輪補寫的列：{run id: (status, error_class, message, calls, batch_id, complete, elapsed)}

RUNS_SQL = """CREATE TABLE IF NOT EXISTS aria_dc_direct_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, trigger TEXT NOT NULL, started_by TEXT, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, error_class TEXT, message TEXT, calls INTEGER, batch_id TEXT, complete INTEGER, gave_up INTEGER NOT NULL DEFAULT 0,
    elapsed_s INTEGER)"""
AUDIT_SQL = """CREATE TABLE IF NOT EXISTS aria_dc_direct_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, by TEXT, key TEXT NOT NULL, before TEXT, after TEXT)"""

CAT_LABEL = {"auth": "帳密被拒", "timeout": "逾時", "tls": "TLS 憑證", "connect": "連不上", "server": "伺服器錯誤", "client": "請求被拒",
             "format": "回應格式不對", "budget": "超過呼叫預算或截止時間", "other": "其他錯誤"}


class _DataError(ValueError):
    """vROps 回的資料形狀不對（缺 ID、非有限數值、批次漏回…）。訊息是固定文字，不含原始資料。"""


class _AuthStop(Exception):
    """登入（含權杖過期後重新登入）被拒：立刻停止其餘類別。"""


class _Aborted(Exception):
    """人為中止（開關被關掉）：不再送任何請求。"""


# ---------------------------------------------------------------- 時間契約
def _window(conn) -> tuple[str, str, bool]:
    """(開始窗, 採用截止, 是否用了預設)：跟 1.5 的 vm_pick 同一個解析函式（daily_check.vm_window）。設定讀不到＝預設。"""
    import daily_check as dc
    try:
        cfg = dc.settings(conn)
    except Exception as exc:  # noqa: BLE001 - 設定讀不到就用預設窗，但標「用預設」並留日誌
        LOG.error("讀每日檢查設定失敗，採用預設時間窗：%s", type(exc).__name__)
        return dc.vm_window({})
    return dc.vm_window(cfg)


def last_start_hms(cut: str) -> str:
    """最晚啟動＝採用截止－單次時間箱（20 分鐘）。回 HH:MM:SS。"""
    secs = int(cut[:2]) * 3600 + int(cut[3:]) * 60 - TIME_BOX_S
    secs = max(0, secs)                                  # 不跨日回繞（vm_window 已拒絕太短的窗口，這裡再保險）
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


# ---------------------------------------------------------------- 設定、稽核
def ensure(conn) -> None:
    """建表（IF NOT EXISTS）。aria_dc_direct_runs／aria_dc_direct_audit 是 2.069.0 才首次出貨的新表，沒有舊 schema，所以不需要欄位遷移。"""
    conn.execute(RUNS_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_aria_dc_direct_runs_day ON aria_dc_direct_runs(day, trigger)")
    conn.execute(AUDIT_SQL)


def module_enabled(conn) -> bool:
    return vi.module_enabled(conn)


def enabled(conn) -> bool:
    return get_setting(conn, K_ENABLED, "0") == "1"


def save_enabled(conn, on: bool, by: str | None = None) -> None:
    """改開關並留稽核（誰、何時、舊值→新值）。"""
    ensure(conn)
    conn.commit()                                         # 先結束任何已開的交易，才能 BEGIN IMMEDIATE
    conn.execute("BEGIN IMMEDIATE")                       # 取得寫鎖後才讀舊值：兩個管理員同時改時，稽核一定接得上（開→關→開…），不會漏記
    try:
        old = get_setting(conn, K_ENABLED, "0") == "1"
        set_setting(conn, K_ENABLED, "1" if on else "0", commit=False)
        if old != on:
            conn.execute("INSERT INTO aria_dc_direct_audit (at, by, key, before, after) VALUES (?,?,?,?,?)",
                         (qw.local_now().strftime("%Y-%m-%d %H:%M:%S"), by, "每日自動抓取開關", "開" if old else "關", "開" if on else "關"))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def audit_list(conn, limit: int = 20) -> list[dict]:
    if not ad._has(conn, "aria_dc_direct_audit"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM aria_dc_direct_audit ORDER BY id DESC LIMIT ?", (limit,))]


# ---------------------------------------------------------------- 執行紀錄
def _now_s() -> str:
    return qw.local_now().strftime("%Y-%m-%d %H:%M:%S")


def _run_start(conn, trigger: str, user, started: datetime) -> int:
    ensure(conn)
    cur = conn.execute("INSERT INTO aria_dc_direct_runs (day, trigger, started_by, started_at, status) VALUES (?,?,?,?,'running')",
                       (started.strftime("%Y-%m-%d"), trigger, user, started.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    return cur.lastrowid


def _run_end_write(conn, rid: int, status, error_class, message, calls, batch_id, complete, elapsed) -> None:
    conn.execute("UPDATE aria_dc_direct_runs SET finished_at=?, status=?, error_class=?, message=?, calls=?, batch_id=?, complete=?, elapsed_s=? WHERE id=?",
                 (_now_s(), status, error_class, message[:500], calls, batch_id, None if complete is None else int(bool(complete)), elapsed, rid))
    edge = (qw.local_now() - timedelta(days=RUNS_KEEP_DAYS)).strftime("%Y-%m-%d")
    conn.execute("DELETE FROM aria_dc_direct_runs WHERE day < ?", (edge,))
    conn.commit()


def _run_end(conn, rid: int | None, status: str, error_class: str | None, message: str, calls: int = 0, batch_id=None, complete=None,
             elapsed: int | None = None) -> bool:
    """寫結束紀錄。失敗：rollback、記 health、暫存等下一輪補寫，回 False（呼叫端要在回傳裡註明「稽核紀錄未落盤」）。"""
    if rid is None:
        return True
    try:
        _run_end_write(conn, rid, status, error_class, message, calls, batch_id, complete, elapsed)
        return True
    except Exception as exc:  # noqa: BLE001 - 紀錄寫不進去不影響抓取結果，但要 rollback、留日誌與 health，下一輪補寫
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 - 回滾失敗也只能留日誌
            LOG.error("每日 VMware 快照：執行紀錄 rollback 失敗")
        LOG.error("每日 VMware 快照：執行紀錄寫入失敗：%s", type(exc).__name__)
        qw._HEALTH["aria_dc_direct_runlog"] = time.monotonic()
        _PENDING_END[rid] = (status, error_class, message, calls, batch_id, complete, elapsed)
        return False


def flush_pending_end(conn) -> int:
    """補寫上一輪沒寫進去的結束紀錄（成功才從暫存移除）。"""
    n = 0
    for rid, args in list(_PENDING_END.items()):
        try:
            _run_end_write(conn, rid, *args)
            _PENDING_END.pop(rid, None)
            n += 1
        except Exception:  # noqa: BLE001 - 下一輪再試；health 已有警告
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001 - 同上
                pass
    return n


def _batch_id_of(started_at: str) -> str:
    """批次 ID 由開始時間決定（run_snapshot 與這裡同一個算法），所以孤兒 running 紀錄可以反查它有沒有已提交的批次。"""
    return BATCH_PREFIX + datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S").strftime("%Y%m%dT%H%M%S")


def mark_interrupted(conn) -> int:
    """目前沒有執行緒在跑，資料庫裡還是 running 的列＝孤兒（服務重啟、或結束紀錄沒寫進去）。
    先用「開始時間算出的批次 ID」反查：批次已提交（成功寫入）就把紀錄補成 ok／incomplete（跨重啟也恢復得了）；找不到批次才標 interrupted。
    等記憶體補寫的列除外。每次 tick 最前面無條件執行（不論開關）；不論有沒有動到列，都結束交易（不留下寫入交易）。"""
    if _state["running"] or not ad._has(conn, "aria_dc_direct_runs"):
        return 0
    n = 0
    try:
        keep = set(_PENDING_END)
        for r in conn.execute("SELECT id, started_at FROM aria_dc_direct_runs WHERE status='running'").fetchall():
            if r["id"] in keep:
                continue
            b = None
            if ad._has(conn, "aria_dc_batch"):
                b = conn.execute("SELECT batch_id, complete, imported_at FROM aria_dc_batch WHERE batch_id=? AND collector_version LIKE ?",
                                 (_batch_id_of(r["started_at"]), ad.DIRECT_PREFIX + "%")).fetchone()
            if b:
                conn.execute("UPDATE aria_dc_direct_runs SET status=?, finished_at=?, batch_id=?, complete=?, message=? WHERE id=?",
                             ("ok" if b["complete"] else "incomplete", b["imported_at"], b["batch_id"], b["complete"],
                              "批次已寫入；結束紀錄當時沒寫進去，由已提交的批次補回", r["id"]))
            else:
                conn.execute("UPDATE aria_dc_direct_runs SET status='interrupted', finished_at=?, error_class='interrupted', "
                             "message='服務重啟或執行緒中斷，這次沒有完成' WHERE id=?", (_now_s(), r["id"]))
            n += 1
    finally:
        conn.commit()                                      # 沒有孤兒時也要結束交易（避免留著寫入交易擋住其他連線）
    return n


def _record_gave_up(conn, now: datetime, reason: str, message: str) -> None:
    """今天放棄自動重試：每個原因每天只寫一筆（1.5 顯示「今天不會再自動重試」）。"""
    ensure(conn)
    day = now.strftime("%Y-%m-%d")
    if conn.execute("SELECT 1 FROM aria_dc_direct_runs WHERE day=? AND gave_up=1 AND error_class=?", (day, reason)).fetchone():
        return
    conn.execute("INSERT INTO aria_dc_direct_runs (day, trigger, started_at, finished_at, status, error_class, message, gave_up) VALUES (?,?,?,?,?,?,?,1)",
                 (day, "schedule", now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S"), "gave_up", reason, message))
    conn.commit()


def _rows_today(conn, day: str) -> list[dict]:
    if not ad._has(conn, "aria_dc_direct_runs"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM aria_dc_direct_runs WHERE day=? ORDER BY id", (day,))]


def _auth_rejects(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["error_class"] == "auth" and r["status"] in ("failed", "incomplete")]


def _adopted_today(conn, now: datetime):
    """今天「窗內」已完成的批次：跟 1.5 採用窗用同一個解析（daily_check.vm_window）與同一個選批（vm_pick_in）。"""
    import daily_check as dc
    frm, cut, _d = _window(conn)
    return dc.vm_pick_in(conn, now.strftime("%Y-%m-%d"), frm, cut)


def public_state(conn, now: datetime | None = None) -> dict:
    """給設定頁與 1.5 資料來源：最近一次執行、今天登入被拒次數、下次可試時間、今天是否放棄、實際時間窗。只讀。"""
    now = now or qw.local_now()
    day = now.strftime("%Y-%m-%d")
    frm, cut, used_default = _window(conn)
    last_start = last_start_hms(cut)
    rows = _rows_today(conn, day)
    real = [r for r in rows if r["status"] != "gave_up"]
    last = None
    if ad._has(conn, "aria_dc_direct_runs"):
        r = conn.execute("SELECT * FROM aria_dc_direct_runs WHERE status != 'gave_up' ORDER BY id DESC LIMIT 1").fetchone()
        last = dict(r) if r else None
    gave = next((r for r in reversed(rows) if r["gave_up"]), None)
    rej = _auth_rejects(rows)
    nxt = None
    if enabled(conn) and not gave:
        cands = [datetime.strptime(f"{day} {frm}", "%Y-%m-%d %H:%M")]
        sched = [r for r in real if r["trigger"] == "schedule"]
        if sched:
            cands.append(datetime.strptime(sched[-1]["started_at"], "%Y-%m-%d %H:%M:%S") + timedelta(minutes=RETRY_GAP_MIN))
        if rej:
            cands.append(datetime.strptime(rej[-1]["finished_at"] or rej[-1]["started_at"], "%Y-%m-%d %H:%M:%S") + timedelta(minutes=AUTH_BACKOFF_MIN))
        t = max(cands)
        if t.strftime("%H:%M:%S") <= last_start and now.strftime("%H:%M:%S") <= last_start:
            nxt = t.strftime("%Y-%m-%d %H:%M")
    return {"last_run": last, "auth_rejects_today": len(rej), "auth_limit": MAX_AUTH_REJECTS_PER_DAY, "next_try": nxt, "running": bool(_state["running"]),
            "gave_up_today": ({"reason": gave["error_class"], "message": gave["message"]} if gave else None),
            "window": {"from": frm, "cutoff": cut, "last_start": last_start[:5], "used_default": used_default}}


def status(conn) -> dict:
    """設定頁用：開關、有沒有設好 vROps、執行狀態、最近一次、系統直抓最後一次成功、今天有沒有 1.5 會採用的完整批次。只讀（不連 vROps）。"""
    now = qw.local_now()
    sv = ad.status_view(conn, now)
    cfg = vi.get_config(conn)
    ps = public_state(conn, now)
    w = ps["window"]
    return {"module_enabled": module_enabled(conn), "enabled": enabled(conn), "configured": bool(cfg["url"] and cfg["credential"]),
            "running": bool(_state["running"]),
            "window_text": f"{w['from']}～{w['cutoff']}（最晚 {w['last_start']} 開始）" + ("，用預設" if w["used_default"] else ""), "day": now.strftime("%Y-%m-%d"),
            "last": _last_compat(conn), "last_ok": sv["direct"]["last_ok"], "today": sv["today"], **ps, "audit": audit_list(conn, 5)}


def _last_compat(conn) -> dict:
    """設定頁「最近一次」：欄位名維持 at／status／message／problems／calls／elapsed_s／batch_id。"""
    if not ad._has(conn, "aria_dc_direct_runs"):
        return {}
    r = conn.execute("SELECT * FROM aria_dc_direct_runs WHERE status != 'gave_up' ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return {}
    st = {"ok": "ok", "incomplete": "incomplete", "failed": "failed", "deferred": "deferred", "interrupted": "failed", "aborted": "failed", "running": "busy"}.get(r["status"], r["status"])
    probs = []
    if r["batch_id"]:
        b = conn.execute("SELECT problems_json FROM aria_dc_batch WHERE batch_id=?", (r["batch_id"],)).fetchone() if ad._has(conn, "aria_dc_batch") else None
        probs = json.loads(b["problems_json"] or "[]") if b else []
    return {"at": r["finished_at"] or r["started_at"], "trigger": r["trigger"], "by": r["started_by"], "status": st, "run_status": r["status"],
            "error_class": r["error_class"], "message": r["message"] or "", "batch_id": r["batch_id"], "complete": bool(r["complete"]),
            "problems": probs[:10], "calls": r["calls"], "elapsed_s": r["elapsed_s"]}


# ---------------------------------------------------------------- 取用層：預算在 transport，每一次實際 request
def _cat(exc) -> str:
    return exc.category if isinstance(exc, VropsError) else "other"


def describe(exc) -> str:
    """對使用者只給固定文字、類別與 HTTP status，不回 vROps 原始訊息本文。"""
    if isinstance(exc, VropsError):
        what = exc.what or "讀取"
        extra = f"，HTTP {exc.status}" if exc.status else ""
        return f"{what}失敗（{CAT_LABEL.get(exc.category, '其他錯誤')}{extra}）"
    if isinstance(exc, _DataError):
        return str(exc)
    return f"內部錯誤（{type(exc).__name__}）"


class _Budget:
    """一次執行的預算：呼叫數、時間箱、（排程）絕對硬截止。calls 是實際送出的 HTTP 請求數（含登入、重新登入、重送、釋放權杖）。"""

    def __init__(self, conn, hard_deadline: datetime | None):
        self.conn, self.hard = conn, hard_deadline
        self.calls, self.first_fail = 0, None
        self.t_deadline = CLOCK() + TIME_BOX_S

    def remaining(self) -> float:
        r = self.t_deadline - CLOCK()
        if self.hard is not None:
            r = min(r, (self.hard - qw.local_now()).total_seconds())
        return r

    def expired(self) -> bool:
        return self.remaining() <= 0

    def gate(self) -> None:
        if not enabled(self.conn):
            raise _Aborted()
        if self.calls >= MAX_CALLS:
            raise VropsError(f"call cap {MAX_CALLS} reached", category="budget", what="讀取")
        if self.remaining() <= 0:
            raise VropsError("deadline reached", category="budget", what="讀取")
        if not qw.checkpoint(self.conn, ENTRY, require_context=False):
            raise qw.QuietWindowBlocked("盤中靜默：不派送新連線，已標待補跑")

    def sleep(self, s: float) -> None:
        SLEEP(min(s, max(0.0, self.remaining())))


class _GuardedTransport:
    """包住真正的 transport：每一個實際送出的 HTTP 請求（登入、重新登入、401 後重送、釋放權杖都算）先過 gate、睡間隔、再過 gate、計數，
    HTTP timeout 取「剩餘秒數」上限。"""

    def __init__(self, inner, budget: _Budget):
        self._inner, self._b = inner, budget

    def request(self, method, url, *, headers=None, json_body=None, form=None, params=None, timeout=ih.DEFAULT_TIMEOUT):
        b = self._b
        b.gate()
        b.sleep(DELAY_S)
        b.gate()                                          # 睡完再看一次：這段時間可能超過截止、被關掉或進了靜默窗
        rem = b.remaining()
        if rem < MIN_HTTP_TIMEOUT_S:                      # 剩餘時間比最低逾時還短：不送（不可把逾時放大成超過截止）
            raise VropsError("deadline reached", category="budget", what="讀取")
        b.calls += 1
        return self._inner.request(method, url, headers=headers, json_body=json_body, form=form, params=params, timeout=min(float(timeout), rem))

    def close(self) -> None:
        self._inner.close()


class _Fetch:
    """單一查詢的重試：失敗最多重試 3 次（3／6／9 秒，睡眠不超過剩餘時間）。預算與間隔由 transport 管。"""

    def __init__(self, conn, client, budget: _Budget):
        self.conn, self.client, self.b = conn, client, budget

    @property
    def calls(self) -> int:
        return self.b.calls

    def call(self, fn, *a):
        for t in range(4):
            try:
                return fn(*a)
            except VropsError as exc:
                if exc.category == "auth":
                    raise _AuthStop() from exc            # 重新登入被拒：不重試、不再問其他類別
                if exc.category == "budget":
                    raise
                if self.b.first_fail is None:
                    self.b.first_fail = self.b.calls
                if t == 3:
                    raise
                self.b.sleep(3 * (t + 1))
            except (ih.TransportError, ih.ConfigError):
                if t == 3:
                    raise VropsError("連線失敗", category="connect", what="讀取") from None
                self.b.sleep(3 * (t + 1))
        raise VropsError("unreachable")

    def paged(self, fn, prop: str, label: str) -> tuple[list, int | None]:
        """一個清單讀到底；回 (項目, vROps 自己回報的 pageInfo.totalCount)。單頁與累計都有筆數上限。"""
        out: list = []
        pg, total = 0, None
        while True:
            rr = self.call(fn, pg)
            chunk = rr.get(prop) or []
            if not isinstance(chunk, list):
                raise _DataError(f"{label}：回應格式不對")
            if len(chunk) > MAX_PAGE_ITEMS:
                raise _DataError(f"{label}：單頁筆數超過上限（{MAX_PAGE_ITEMS}）")
            out.extend(x for x in chunk if x)
            if len(out) > MAX_TOTAL_ITEMS:
                raise _DataError(f"{label}：累計筆數超過上限（{MAX_TOTAL_ITEMS}）")
            info = rr.get("pageInfo")
            if isinstance(info, dict) and isinstance(info.get("totalCount"), (int, float)) and not isinstance(info.get("totalCount"), bool):
                total = int(info["totalCount"])
            pg += 1
            if len(chunk) < PAGE_SIZE:
                break
            if total is not None and len(out) >= total:
                break
            if pg >= MAX_PAGES:
                raise _DataError(f"{label}：分頁超過 {MAX_PAGES} 頁")
        return out, total


# ---------------------------------------------------------------- 小工具：嚴格轉換
def _rid(x, what: str = "資源") -> str:
    """ID 一定要是非空字串或整數；None／空白／其他型別一律這一類失敗（絕不出現 "None"）。回正規化（去前後空白）後的字串。"""
    if isinstance(x, bool):
        raise _DataError(f"{what} ID 格式不對")
    if isinstance(x, int):
        return str(x)
    if isinstance(x, str) and x.strip():
        return x.strip()
    raise _DataError(f"{what} ID 格式不對")


def _fnum(x, what: str):
    """數值：沒有（None／空字串）→ None；有值就要是有限數字，NaN／Inf／無法轉換→這一類失敗。"""
    if x is None or (isinstance(x, str) and not x.strip()):
        return None
    if isinstance(x, bool):
        raise _DataError(f"{what}：不是數字")
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise _DataError(f"{what}：不是數字") from None
    if not math.isfinite(v):
        raise _DataError(f"{what}：不是有限數字")
    return v


def _ms(x, what: str):
    v = _fnum(x, what)
    return None if v is None else int(v)


def _vcid(r: dict) -> str:
    for x in ((r.get("resourceKey") or {}).get("resourceIdentifiers") or []):
        if isinstance(x, dict) and ((x.get("identifierType") or {}).get("name")) == VCID_NAME:
            return str(x.get("value") if x.get("value") is not None else "")
    return ""


def _chunks(ids: list[str]) -> list[list[str]]:
    return [ids[i:i + CHUNK] for i in range(0, len(ids), CHUNK)]


def _res_name(r: dict) -> str:
    return str((r.get("resourceKey") or {}).get("name") or "")


def _short(s) -> str:
    return ("" if s is None else str(s))[:300]


def _block() -> dict:
    return {"ok": False, "error": "not read", "total": None, "rows": []}


def _check_ids(req: list[str], got: list[str], label: str) -> None:
    """回傳的 ID 集合必須與要求的完全相等，且每個只出現一次；重複、額外、缺少任一種都這一類失敗。"""
    miss, extra = set(req) - set(got), set(got) - set(req)
    dup = len(got) - len(set(got))
    if miss or extra or dup:
        raise _DataError(f"{label}回傳的資源跟要求的對不上（要求 {len(req)} 個：缺 {len(miss)}、額外 {len(extra)}、重複 {dup}）")


def _props(fx: _Fetch, ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for c in _chunks(ids):
        res = fx.call(fx.client.properties_get, c)
        lst = res.get("resourcePropertiesList")
        if not lst or not isinstance(lst, list):
            raise _DataError("屬性回應沒有 resourcePropertiesList")
        got = [_rid(item.get("resourceId"), "屬性") for item in lst]
        _check_ids(c, got, "屬性")
        for rid, item in zip(got, lst):
            m = {}
            for x in (item.get("property") or []):
                if isinstance(x, dict) and x.get("name"):
                    m[str(x["name"])] = x.get("value")
            out[rid] = m
    return out


def _latest(fx: _Fetch, ids: list[str], keys: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for c in _chunks(ids):
        res = fx.call(fx.client.stats_latest, c, keys)
        vals = res.get("values")
        if not isinstance(vals, list):
            raise _DataError("最新指標回應沒有 values")
        got = [_rid(v.get("resourceId"), "指標") for v in vals]
        _check_ids(c, got, "最新指標")
        for rid, v in zip(got, vals):
            m = {}
            for s in ((v.get("stat-list") or {}).get("stat") or []):
                if isinstance(s, dict) and s.get("statKey"):
                    d, ts = s.get("data") or [], s.get("timestamps") or []
                    if d:
                        m[str((s["statKey"] or {}).get("key"))] = {"v": d[-1], "t": ts[-1] if ts else None}
            out[rid] = m
    return out


def alert_levels(conn) -> list[str]:
    """警示等級 = 預設三級 ∪ 每日檢查設定的 vm_alert_levels。**超集合是刻意設計**：收集層多存無害，下游 daily_check 依設定再過濾。
    設定讀不到、JSON 損毀、格式不對一律丟 _DataError（警示這一類標不完整並寫原因），不退回預設。"""
    import daily_check as dc
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (dc.SETTING,)).fetchone()
        cfg = json.loads(r[0]) if r and r[0] else {}
    except Exception:  # noqa: BLE001 - 轉成固定訊息的資料錯誤（這一類不完整）
        raise _DataError("警示等級設定讀取失敗（每日檢查設定讀不到或損毀），警示這一類不完整") from None
    lv = cfg.get("vm_alert_levels") if isinstance(cfg, dict) else None
    if lv is None:
        lv = dc.DEFAULTS["vm_alert_levels"]
    if not isinstance(lv, list) or not lv or any(str(x).upper() not in dc.VM_LEVELS for x in lv):
        raise _DataError("警示等級設定格式不對，警示這一類不完整")
    return sorted(set(CRITICALITY) | {str(x).upper() for x in lv})


# ---------------------------------------------------------------- 收集：產出跟 snap.json 同形狀的 dict
def collect(conn, client, fx: _Fetch, mount_rels: bool = False, levels=None) -> dict:
    """levels：等級清單；傳 Exception（_DataError）＝設定讀取失敗，警示那一類標不完整。"""
    aria = {"ok": False, "error": ""}
    login_err = None
    try:
        client.acquire_token()
        aria["ok"] = True
    except VropsError as exc:
        login_err = exc
        aria["error"] = "login failed: " + describe(exc)
    except (ih.TransportError, ih.ConfigError):
        aria["error"] = "login failed: 連線失敗"
    adapters, hosts, dss, alerts = _block(), _block(), _block(), _block()
    alerts["fetched"] = None
    extra: dict = {}
    if not aria["ok"]:
        for b in (adapters, hosts, dss, alerts):
            b["error"] = "not read: " + aria["error"]
    else:
        try:
            _read_adapters(fx, adapters)
            _read_hosts(fx, hosts, extra)
            _read_datastores(fx, dss, mount_rels)
            _read_alerts(fx, alerts, levels)
        except _AuthStop:
            aria["ok"], aria["error"] = False, "login failed: 登入失敗（帳密被拒）"
            login_err = VropsError("", category="auth", what="登入")
            for b in (adapters, hosts, dss, alerts):
                b.update(ok=False, error="not read: " + aria["error"], rows=[])
    lv = list(levels) if isinstance(levels, (list, tuple)) else []
    params = {"criticality": lv, "vcid": VCID_NAME, "mount_from_rels": bool(mount_rels), "source": "direct", "host_conn": HOST_CONN_KEY,
              "host_maint": HOST_MAINT_KEY, "host_time": HOST_TIME_KEY, "ds_type": DS_TYPE_KEY, "ds_cap": DS_CAP_KEY, "ds_free": DS_FREE_KEY,
              "ds_hosts": DS_HOSTS_KEY, "ds_local": DS_LOCAL_KEY, "page_size": PAGE_SIZE, "chunk": CHUNK, **extra}
    return {"aria": aria, "adapters": adapters, "hosts": hosts, "datastores": dss, "alerts": alerts, "params": params, "_login_err": login_err}


def _fail_block(b: dict, exc, client) -> None:
    b["error"] = ih.scrub(describe(exc), [getattr(client, "_pw", ""), getattr(client, "_token", "") or ""], limit=300)
    b["_exc"] = exc


def _read_adapters(fx, b: dict) -> None:
    try:
        res = fx.call(fx.client.adapters, ADAPTER_KIND)
        rows = []
        for a in (res.get("adapterInstancesInfoDto") or []):
            if not isinstance(a, dict) or not a:
                continue
            rows.append({"id": _rid(a.get("id"), "收集器"), "name": _res_name(a), "vcid": _vcid(a),
                         "last_collected": _ms(a.get("lastCollected"), "收集器最後收集時間"),
                         "last_heartbeat": _ms(a.get("lastHeartbeat"), "收集器心跳時間"), "message": _short(a.get("messageFromAdapterInstance"))})
        info = res.get("pageInfo")
        total, total_from = len(rows), "count"
        if isinstance(info, dict) and isinstance(info.get("totalCount"), int) and not isinstance(info.get("totalCount"), bool):
            total, total_from = info["totalCount"], "pageInfo"
        b.update(rows=rows, total=total, total_from=total_from, ok=True, error="")
    except (qw.QuietWindowBlocked, _AuthStop, _Aborted):
        raise
    except Exception as exc:  # noqa: BLE001 - 這一類失敗＝ok=false 並寫原因，其他類照跑（同腳本）
        _fail_block(b, exc, fx.client)


def _read_hosts(fx, b: dict, extra: dict) -> None:
    try:
        lst, total = fx.paged(lambda pg: fx.client.resources_page(ADAPTER_KIND, "HostSystem", pg, PAGE_SIZE), "resourceList", "ESXi 主機")
        ids = [_rid(r.get("identifier"), "主機") for r in lst]
        pp = _props(fx, ids)
        st = _latest(fx, ids, [HOST_TIME_KEY])
        no_conn = [i for i in ids if not (pp.get(i) or {}).get(HOST_CONN_KEY)]
        if no_conn:
            raise _DataError(f"{len(no_conn)} 台主機沒有連線狀態屬性（{HOST_CONN_KEY}）")
        extra["host_time_missing"] = sum(1 for i in ids if not (st.get(i) or {}).get(HOST_TIME_KEY))      # 沒有取樣時間的台數（記錄，不擋）
        rows = []
        for r, rid in zip(lst, ids):
            p, s = pp.get(rid), st.get(rid)
            at = s[HOST_TIME_KEY]["t"] if s and s.get(HOST_TIME_KEY) else None
            rows.append({"id": rid, "name": _res_name(r), "vcid": _vcid(r), "conn": p.get(HOST_CONN_KEY), "maint": p.get(HOST_MAINT_KEY),
                         "at": _ms(at, "主機取樣時間")})
        b.update(rows=rows, total=total, ok=True, error="")
    except (qw.QuietWindowBlocked, _AuthStop, _Aborted):
        raise
    except Exception as exc:  # noqa: BLE001
        _fail_block(b, exc, fx.client)


def _read_datastores(fx, b: dict, mount_rels: bool) -> None:
    try:
        lst, total = fx.paged(lambda pg: fx.client.resources_page(ADAPTER_KIND, "Datastore", pg, PAGE_SIZE), "resourceList", "資料存放區")
        ids = [_rid(r.get("identifier"), "資料存放區") for r in lst]
        pp = _props(fx, ids)
        st = _latest(fx, ids, [DS_CAP_KEY, DS_FREE_KEY, DS_HOSTS_KEY])
        miss_cap = [i for i in ids if not (st.get(i) or {}).get(DS_CAP_KEY)]
        miss_free = [i for i in ids if not (st.get(i) or {}).get(DS_FREE_KEY)]
        if miss_cap or miss_free:
            raise _DataError(f"資料存放區缺容量指標：總容量缺 {len(miss_cap)} 個、剩餘缺 {len(miss_free)} 個（共 {len(ids)} 個）")
        rows, mount_fail = [], 0
        for r, rid in zip(lst, ids):
            p, s = pp.get(rid), st.get(rid)
            cap = _fnum(s[DS_CAP_KEY]["v"], "資料存放區總容量")
            free = _fnum(s[DS_FREE_KEY]["v"], "資料存放區剩餘")
            at = s[DS_FREE_KEY]["t"]
            nh = None
            if s.get(DS_HOSTS_KEY):
                x = _fnum(s[DS_HOSTS_KEY]["v"], "掛載主機數")
                if x is not None and x >= 1:
                    nh = int(x)
            if mount_rels:
                # 選用：數這個資料存放區的上層 HostSystem。失敗只讓掛載主機數留空（系統會列出來），不讓整類失敗
                try:
                    rel = fx.call(fx.client.relationships, rid, "PARENT")
                    n = sum(1 for x in rel if ((x.get("resourceKey") or {}).get("resourceKindKey")) == "HostSystem")
                    if n >= 1:
                        nh = n
                except (qw.QuietWindowBlocked, _AuthStop, _Aborted):
                    raise
                except Exception:  # noqa: BLE001
                    mount_fail += 1
            if free is not None and free < 0:
                free = None
            if cap is not None and cap < 0:
                cap = None
            rows.append({"id": rid, "name": _res_name(r), "vcid": _vcid(r), "type": p.get(DS_TYPE_KEY) if p else None,
                         "cap_gb": cap, "free_gb": free, "hosts": nh, "local": p.get(DS_LOCAL_KEY) if p else None, "at": _ms(at, "資料存放區取樣時間")})
        if mount_fail:
            LOG.info("掛載主機數讀取失敗 %s 個資料存放區（留空）", mount_fail)
        b.update(rows=rows, total=total, ok=True, error="")
    except (qw.QuietWindowBlocked, _AuthStop, _Aborted):
        raise
    except Exception as exc:  # noqa: BLE001
        _fail_block(b, exc, fx.client)


def _read_alerts(fx, b: dict, levels) -> None:
    try:
        if not isinstance(levels, (list, tuple)):
            raise levels if isinstance(levels, Exception) else _DataError("警示等級設定讀取失敗，警示這一類不完整")
        lst, total = fx.paged(lambda pg: fx.client.alerts_page(pg, PAGE_SIZE, True), "alerts", "警示")
        b["total"], b["fetched"] = total, len(lst)
        want = {c.upper() for c in levels}
        keep = []
        for a in lst:
            if str(a.get("alertLevel") or "").upper() in want:
                # 警示的 resourceId：鍵不存在＝這一筆不合法（這一類不完整）；明確 null＝沒有資源，存成 NULL（不是空字串）；有值就走同一套嚴格 _rid，之後全程重用正規化結果
                if "resourceId" not in a:
                    raise _DataError("警示缺少 resourceId 欄位")
                keep.append((a, None if a.get("resourceId") is None else _rid(a.get("resourceId"), "警示資源")))
        rids = sorted({r for _a, r in keep if r is not None})
        res: dict[str, dict] = {}
        for c in _chunks(rids):
            rr = fx.call(fx.client.resources_by_ids, c, PAGE_SIZE)
            lst2 = [r for r in (rr.get("resourceList") or []) if isinstance(r, dict)]
            got = [_rid(r.get("identifier"), "警示資源") for r in lst2]
            _check_ids(c, got, "警示資源")                   # 漏回、額外、重複都讓這一類不完整（不產生名稱／種類／vCenter 全空的警示列）
            for rid2, r in zip(got, lst2):
                res[rid2] = r
        rows = []
        for a, rid in keep:
            r = res.get(rid) if rid is not None else None
            rows.append({"id": _rid(a.get("alertId"), "警示"), "res_id": rid,
                         "res_kind": str((r.get("resourceKey") or {}).get("resourceKindKey") or "") if r else "",
                         "res_name": _res_name(r) if r else "", "vcid": _vcid(r) if r else "",
                         "name": str(a.get("alertDefinitionName") or ""), "level": str(a.get("alertLevel") or ""),
                         "status": str(a.get("status") or ""), "control": str(a.get("controlState") or ""), "owner": str(a.get("ownerName") or ""),
                         "start": _ms(a.get("startTimeUTC"), "警示開始時間"), "updated": _ms(a.get("updateTimeUTC"), "警示更新時間")})
        b.update(rows=rows, ok=True, error="")
    except (qw.QuietWindowBlocked, _AuthStop, _Aborted):
        raise
    except Exception as exc:  # noqa: BLE001
        _fail_block(b, exc, fx.client)


# ---------------------------------------------------------------- 執行一次（抓 → parse → apply；同一條完整性檢查）
def _release_close(client) -> None:
    """盡力釋放權杖（也受預算與截止約束，超過就只記錄）再無條件關連線；釋放遇到靜默窗只記錄、不改寫已完成的結果。"""
    try:
        client.release_token()
    except qw.QuietWindowBlocked:
        LOG.info("盤中靜默：權杖沒有釋放（會自己逾時）")
    except Exception as exc:  # noqa: BLE001 - 釋放權杖只是收尾（含超過截止／開關被關），失敗不影響這次結果
        LOG.info("權杖沒有釋放：%s", type(exc).__name__)
    close = getattr(client, "close", None)
    if close:
        close()


def _out(status: str, message: str, error_class: str | None = None, **kw) -> dict:
    return {"status": status, "message": message, "error_class": error_class, **kw}


def _first_block_class(snap: dict) -> str | None:
    for k in ad.KINDS:
        e = snap[k].get("_exc")
        if e is not None:
            return _cat(e) if isinstance(e, VropsError) else ("format" if isinstance(e, _DataError) else "other")
    return None


@qw.guarded("aria_dailycheck_direct_tick")
def run_snapshot(conn, trigger: str = "schedule", user: str | None = None, lock_held: bool = False) -> dict:
    """抓一次並存成一批。回 {"status": ok／incomplete／failed／deferred／aborted／busy, "message", "error_class", "batch_id", "complete", "problems"}。
    ok＝存了完整一批；incomplete＝存了但不完整（1.5 會顯示原因、不會當成正常）；永不往外丟原始例外（可能帶機密）。每次都寫一筆執行紀錄。
    lock_held：呼叫端（_spawn，主執行緒）已先取得共用鎖，由呼叫端的 worker 結束時釋放；沒給就自己取、自己放。"""
    if not lock_held and not _run_lock.acquire(blocking=False):
        return _out("busy", "另一個 vROps 工作（資產匯入或上一次快照）正在進行，這次跳過")
    client = None
    started = qw.local_now()
    t0 = CLOCK()
    rid = None
    fx = None
    budget = None

    def elapsed() -> int:
        return int(CLOCK() - t0)

    def finish(out: dict, status, err, calls=0, batch_id=None, complete=None) -> dict:
        if not _run_end(conn, rid, status, err, out["message"], calls, batch_id, complete, elapsed()):
            out["audit_note"] = "稽核紀錄未落盤（下一輪會補寫）"
            out["message"] += "；稽核紀錄未落盤"
        return out
    try:
        if trigger == "schedule":
            # 主執行緒通過 _due 到 worker 真的開始之間可能隔了一段時間（排隊、CPU）：開 client／登入之前，用「現在」重新核對最晚啟動時間
            _f0, cut0, _d0 = _window(conn)
            if started.strftime("%H:%M:%S") > last_start_hms(cut0):
                _record_gave_up(conn, started, "past_last_start", f"今日自動抓取結束（已過 {last_start_hms(cut0)[:5]}，來不及在 {cut0} 前收尾），請走備援")
                return _out("failed", "已過最晚啟動時間，這次不啟動", "past_last_start")
        try:
            rid = _run_start(conn, trigger, user, started)
        except Exception as exc:  # noqa: BLE001 - 紀錄表寫不進去：不抓（沒有紀錄的連線不可接受）
            LOG.error("每日 VMware 快照：開始紀錄寫入失敗：%s", type(exc).__name__)
            return _out("failed", "內部錯誤（執行紀錄寫入失敗），這次沒有抓", "internal")
        # 開 client、首次登入之前先看開關與靜默窗（登入 POST 也是外連）
        if not enabled(conn):
            return finish(_out("aborted", "人為中止（每日自動抓取開關已關閉）", "aborted"), "aborted", "aborted")
        if not qw.checkpoint(conn, ENTRY, require_context=False):
            return finish(_out("deferred", "進入盤中靜默，這次沒有抓", "quiet_window"), "deferred", "quiet_window")
        try:
            client, cfg, host = vi._open_client(conn)
        except ih.ConfigError as exc:
            return finish(_out("failed", str(exc), "config"), "failed", "config")
        hard = None
        if trigger == "schedule":                          # 排程：採用截止（例如 08:00:00）是絕對硬截止；人工抓不受窗口限制，只受時間箱
            _f, cut, _d = _window(conn)
            hard = datetime.strptime(f"{started.strftime('%Y-%m-%d')} {cut}", "%Y-%m-%d %H:%M")
        budget = _Budget(conn, hard)
        client._t = _GuardedTransport(client._t, budget)    # 之後這個 client 的每一個 HTTP 請求（含登入、重新登入、重送、釋放權杖）都受預算約束
        fx = _Fetch(conn, client, budget)
        mount = get_setting(conn, K_MOUNT, "0") == "1"
        try:
            levels = alert_levels(conn)
        except _DataError as exc:
            levels = exc                                   # 設定讀不到：警示那一類標不完整並寫原因，不退回預設
        try:
            snap = collect(conn, client, fx, mount, levels)
        except qw.QuietWindowBlocked:
            return finish(_out("deferred", "進入盤中靜默，這次沒有存", "quiet_window"), "deferred", "quiet_window", budget.calls)
        except _Aborted:
            return finish(_out("aborted", "人為中止（每日自動抓取開關已關閉）", "aborted"), "aborted", "aborted", budget.calls)
        if budget.expired():                                # 收集完已經超過截止（或時間箱）：不解析、不寫批次
            return finish(_out("failed", "超過採用截止時間，這次沒有存", "budget"), "failed", "budget", budget.calls)
        login_err = snap.pop("_login_err", None)
        blk_class = _first_block_class(snap)
        for k in ad.KINDS:
            snap[k].pop("_exc", None)
        err_class = None
        if not snap["aria"]["ok"]:
            err_class = _cat(login_err) if login_err is not None else "connect"
        counts = {k: len(snap[k]["rows"]) for k in ad.KINDS}
        try:
            parsed = ad.parse(snap, {"counts": counts})
        except ValueError as exc:
            credential_store.audit_use(conn, vi.credential_name(conn) or vi.CRED_NAME, host, False, "每日 VMware 快照：資料格式不對")
            conn.commit()
            return finish(_out("failed", f"收到的資料格式不對，這次沒有存：{ih.scrub(exc, [], 200)}", "format"), "failed", "format", budget.calls)
        batch_id = BATCH_PREFIX + started.strftime("%Y%m%dT%H%M%S")
        collected_at = started.strftime("%Y-%m-%dT%H:%M:%S") + "+08:00"
        import relay_import as ri
        try:
            credential_store.audit_use(conn, vi.credential_name(conn) or vi.CRED_NAME, host, snap["aria"]["ok"],
                                       f"每日 VMware 快照（{trigger}）" + ("" if snap["aria"]["ok"] else "：登入失敗"))
            res = ad.apply(conn, batch_id, collected_at, COLLECTOR_VERSION, parsed, ri._now_text(None), raw_sha=[{"params": snap["params"]}])
            if budget.expired():                            # 寫入途中（例如 SQLite 鎖等待）超過截止：不提交，批次不落地、不標完整
                conn.rollback()
                return finish(_out("failed", "超過採用截止時間，這次沒有存", "budget"), "failed", "budget", budget.calls)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        done = _out("ok" if parsed["complete"] else "incomplete", res["note"], err_class, batch_id=batch_id, complete=parsed["complete"],
                    problems=parsed["problems"][:10], summary=counts)
        if parsed["complete"] and not budget.expired():     # 已超過截止就不再刷新每日檢查（批次本身是截止前寫好的）
            try:
                import daily_check as dc
                done["refresh"] = dc.vm_refresh(conn, dc.now_local())
            except Exception as exc:  # noqa: BLE001 - 重新產生是附加功能；失敗只記日誌（畫面會提示有較新的 VMware 資料，人可手動重新產生）
                LOG.error("每日 VMware 快照後重新產生每日檢查失敗：%s", type(exc).__name__)
                if conn.in_transaction:
                    conn.rollback()
        if not parsed["complete"] and err_class is None:
            err_class = blk_class
            done["error_class"] = err_class
        return finish(done, done["status"], err_class, budget.calls, batch_id, parsed["complete"])
    except _AuthStop:
        return finish(_out("failed", "登入失敗（帳密被拒）", "auth"), "failed", "auth", budget.calls if budget else 0)
    except Exception as exc:  # noqa: BLE001 - 沒預期到的錯：記固定訊息，不往外丟原始例外（可能帶機密）
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 - 回滾失敗也要繼續把紀錄寫成失敗
            pass
        LOG.error("每日 VMware 快照內部錯誤：%s", type(exc).__name__)
        return finish(_out("failed", f"內部錯誤（{type(exc).__name__}），這次沒有存", "internal"), "failed", "internal", budget.calls if budget else 0)
    finally:
        if client is not None:
            _release_close(client)
        if not lock_held:                                  # lock_held＝鎖是 _spawn 取的，由 worker 結束時釋放
            _run_lock.release()


# ---------------------------------------------------------------- 排程（每分鐘；時間契約、重試時槽、已完成判斷都在這裡）
def _is_workday(conn, now: datetime) -> bool:
    import daily_check as dc
    return dc.is_working_day(dc.settings(conn), now.date())


def _due(conn, now: datetime | None = None) -> tuple[bool, str]:
    if not module_enabled(conn) or not enabled(conn):
        return False, "disabled"
    now = now or qw.local_now()
    if not _is_workday(conn, now):
        return False, "not_working_day"
    frm, cut, _d = _window(conn)
    hms = now.strftime("%H:%M:%S")
    if hms < frm + ":00":
        return False, "out_of_window"
    day = now.strftime("%Y-%m-%d")
    if _adopted_today(conn, now) is not None:
        return False, "done_today"
    last_start = last_start_hms(cut)
    if frm + ":00" > last_start:                           # 窗口太短（不該發生，vm_window 已拒絕）：寧可不跑
        return False, "window_too_short"
    if hms > last_start:                                   # 秒級：07:40:00 之後不得啟動（08:00:00 前一定收尾）
        _record_gave_up(conn, now, "past_last_start", f"今日自動抓取結束（已過 {last_start[:5]}，來不及在 {cut} 前收尾），請走備援")
        return False, "past_last_start"
    if _state["running"] or _run_lock.locked():
        return False, "busy"
    cfg = vi.get_config(conn)
    if not (cfg["url"] and cfg["credential"]):
        _record_gave_up(conn, now, "not_configured", "還沒設定 vROps 網址與收集憑證，今天不會自動抓取，請走備援")
        return False, "not_configured"
    rows = _rows_today(conn, day)
    rej = _auth_rejects(rows)
    if len(rej) >= MAX_AUTH_REJECTS_PER_DAY:
        _record_gave_up(conn, now, "login_limit", f"今天登入被拒已達 {MAX_AUTH_REJECTS_PER_DAY} 次，為保護帳號今天不會再自動重試，請走備援")
        return False, "gave_up_today"
    if rej:
        t = datetime.strptime(rej[-1]["finished_at"] or rej[-1]["started_at"], "%Y-%m-%d %H:%M:%S")
        if now - t < timedelta(minutes=AUTH_BACKOFF_MIN):
            return False, "auth_backoff"
    sched = [r for r in rows if r["trigger"] == "schedule" and r["status"] != "gave_up"]
    if sched and now - datetime.strptime(sched[-1]["started_at"], "%Y-%m-%d %H:%M:%S") < timedelta(minutes=RETRY_GAP_MIN):
        return False, "retry_later"
    return True, "due"


def _connect(path):
    """worker 自己的連線（路徑由主執行緒事先固定，worker 絕不碰主執行緒的連線）。"""
    return get_connection(Path(path)) if path else get_connection()


def _worker_start_failed(path, trigger: str, user, exc: Exception) -> dict:
    """worker 連資料庫都開不了：留日誌與 health，再試著用新連線寫一筆 failed 紀錄（寫不進去就只剩日誌與 health）。"""
    LOG.error("每日 VMware 快照：背景工作無法開始（%s）", type(exc).__name__)
    qw._HEALTH["aria_dc_direct_thread"] = time.monotonic()
    msg = f"背景工作無法開始（{type(exc).__name__}）"
    try:
        c2 = _connect(path)
        try:
            rid = _run_start(c2, trigger, user, qw.local_now())
            _run_end(c2, rid, "failed", "worker_start", msg)
        finally:
            c2.close()
    except Exception as exc2:  # noqa: BLE001 - 紀錄也寫不進去：已有日誌與 health
        LOG.error("每日 VMware 快照：無法開始的紀錄也寫不進去（%s）", type(exc2).__name__)
    return {"status": "failed", "message": msg, "error_class": "worker_start"}


def _spawn(conn, trigger: str, user: str | None, ticket=None) -> dict:
    """**主執行緒先取得共用鎖**再啟動背景工作並把鎖交給 worker（worker 結束才釋放）：資產匯入不可能搶在直抓前面。
    資料庫路徑也在主執行緒先固定；worker 只用自己開的連線。執行緒建立失敗要復原狀態、釋放鎖並留紀錄。"""
    with _mutex:
        if _state["running"]:
            return {"status": "busy", "message": "已有一次每日 VMware 快照正在進行，請稍後再試"}
        if not _run_lock.acquire(blocking=False):
            return {"status": "busy", "message": "另一個 vROps 工作（資產匯入）正在進行，這次跳過"}
        _state["running"] = True
    box: dict = {}
    try:
        run = qw.capture(ENTRY, ticket)                 # 把窗內人為授權／非人為狀態帶進執行緒
        path = qw._path_of(conn) if RUN_IN_BACKGROUND else None     # 主執行緒固定路徑（worker 不碰主連線）

        def work():
            c = conn if not RUN_IN_BACKGROUND else None
            try:
                if c is None:
                    try:
                        c = _connect(path)
                    except Exception as exc:  # noqa: BLE001 - 連線開不了：留紀錄與 health，不讓 API 的 started 變成沒下文
                        box["out"] = _worker_start_failed(path, trigger, user, exc)
                        return
                with qw.use(run):
                    box["out"] = run_snapshot(c, trigger, user, lock_held=True)
                if isinstance(box["out"], dict) and box["out"].get("status") in ("ok", "incomplete"):
                    qw.pending_finish(c, ENTRY, True, detail="已依排程執行")
            except Exception as exc:  # noqa: BLE001 - 執行緒最外層：不讓例外跑掉；run_snapshot 內已把錯誤寫進執行紀錄
                LOG.error("每日 VMware 快照執行緒中斷：%s", type(exc).__name__)
                box["out"] = {"status": "failed", "message": f"執行中斷（{type(exc).__name__}）"}
            finally:
                if c is not None and c is not conn:
                    try:
                        c.close()
                    except Exception:  # noqa: BLE001 - 關連線失敗不影響結果
                        pass
                with _mutex:
                    _state["running"] = False
                try:
                    _run_lock.release()                       # 鎖是 _spawn 在主執行緒取的，worker 結束才還
                except RuntimeError:
                    pass  # 鎖已經不在了（不該發生）；不讓它蓋掉結果

        if not RUN_IN_BACKGROUND:
            work()
            return box.get("out") or {"status": "failed", "message": "沒有結果"}
        threading.Thread(target=work, name="aria-dc-direct", daemon=True).start()
        return {"status": "started", "message": "已開始抓取，幾分鐘後完成；請看最近一次狀態"}
    except Exception as exc:  # noqa: BLE001 - 執行緒建立失敗：復原狀態、釋放鎖、留紀錄（不讓 running 永久卡住）
        with _mutex:
            _state["running"] = False
        try:
            _run_lock.release()
        except RuntimeError:
            pass  # 鎖已經不在了（不該發生）
        LOG.error("每日 VMware 快照：背景執行緒建立失敗：%s", type(exc).__name__)
        qw._HEALTH["aria_dc_direct_thread"] = time.monotonic()
        try:
            rid = _run_start(conn, trigger, user, qw.local_now())
            _run_end(conn, rid, "failed", "thread", f"背景執行緒建立失敗（{type(exc).__name__}）")
        except Exception:  # noqa: BLE001 - 紀錄也寫不進去：已有日誌與 health
            pass
        return {"status": "failed", "message": f"背景執行緒建立失敗（{type(exc).__name__}）"}


def tick(conn) -> dict:
    """排程器每輪呼叫（放在整合任務最前面）：先無條件回收孤兒 running、補寫沒寫進去的結束紀錄；
    再看開關＋工作日＋窗口＋今天（窗內）還沒有批次＋時槽到了才抓；盤中靜默窗內延後。立刻返回。"""
    try:
        mark_interrupted(conn)
        flush_pending_end(conn)
    except Exception as exc:  # noqa: BLE001 - 回收失敗不擋排程；留日誌與 health
        LOG.error("每日 VMware 快照：孤兒紀錄回收失敗：%s", type(exc).__name__)
        qw._HEALTH["aria_dc_direct_runlog"] = time.monotonic()
    ok, why = _due(conn)
    if not ok:
        return {"status": why}
    if qw.defer_if_quiet(conn, "aria_dailycheck_direct_tick"):      # 確實到期才守門；盤中不自動連 vROps
        qw.pending_mark(conn, ENTRY, "每日 VMware 快照到期時在盤中靜默，延後")
        return {"status": "deferred_quiet_window"}
    return _spawn(conn, "schedule", None)


def catchup(conn) -> dict:
    """盤中被延後的補跑登記：直抓過了最晚啟動時間就不再啟動（不補跑、不假承諾）；只有仍在時槽內且到期才會開跑。"""
    ok, why = _due(conn)
    if not ok:
        return {"status": "busy", "refused": True} if why == "busy" else {"status": "not_needed", "message": why}
    out = _spawn(conn, "schedule", None)
    return {"status": "ok", "message": out.get("status", "")}


def start_manual(conn, user: str, ticket=None) -> dict:
    """管理員按「立即抓一次」：不看窗口與今天有沒有批次；但開關關著就拒絕，登入被拒達保護門檻時也拒絕。"""
    if not module_enabled(conn):
        return {"status": "failed", "message": "Aria Operations 匯入模組未啟用"}
    if not enabled(conn):
        return {"status": "failed", "message": "每日自動抓取開關是關閉的，請先開啟（建議在採用窗與最晚啟動時間以外的時段試抓）"}
    cfg = vi.get_config(conn)
    if not (cfg["url"] and cfg["credential"]):
        return {"status": "failed", "message": "還沒設定 vROps 網址與收集憑證（系統設定 → 外部整合 → Aria Operations）"}
    now = qw.local_now()
    rows = _rows_today(conn, now.strftime("%Y-%m-%d"))
    rej = _auth_rejects(rows)
    if len(rej) >= MAX_AUTH_REJECTS_PER_DAY:
        return {"status": "failed", "message": f"今天登入被拒已達 {MAX_AUTH_REJECTS_PER_DAY} 次，為保護帳號今天不再嘗試；請先確認收集憑證，明天再試"}
    if rej:
        t = datetime.strptime(rej[-1]["finished_at"] or rej[-1]["started_at"], "%Y-%m-%d %H:%M:%S")
        if now - t < timedelta(minutes=AUTH_BACKOFF_MIN):
            return {"status": "failed", "message": f"剛剛登入被拒，為保護帳號 {t + timedelta(minutes=AUTH_BACKOFF_MIN):%H:%M} 以後才能再試"}
    return _spawn(conn, "manual", user, ticket)
