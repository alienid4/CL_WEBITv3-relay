"""掃描服務層：手動重掃 + DB 驅動的排程器。

- 手動重掃：`start_scan("manual")` 開背景執行緒跑掃描，記錄 scan_runs 狀態，防並發。
- 排程：設定存 app_settings（UI 可改、馬上生效），`_scheduler_loop` 每分鐘檢查到點沒。
  改成 DB 驅動而非寫死 systemd timer，就是為了「以後改時間/頻率點一下就好、不用碰主機」。

執行緒安全：每個背景執行緒開自己的 sqlite 連線（sqlite3 預設 check_same_thread=True）。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from db import (
    create_scan_run,
    finish_scan_run,
    get_connection,
    get_last_schedule_run_time,
    get_latest_scan_run,
    get_setting,
    init_db,
    list_connections,
    mark_connection_scanned,
    set_setting,
)
from comparison_engine import run_comparison
from run_real_scan import scan_targets  # 重用「從 connections 讀網段目標」的邏輯
from scanner import run_scan

_lock = threading.Lock()
_running = False

# 2026-09-16 使用者要「每週一次的存活掃描，時間我再指定」→ 多一個 weekly 模式。
# scan_weekday：0=週一 … 6=週日（照 datetime.weekday()）。
_DEFAULTS = {"scan_enabled": "1", "scan_mode": "daily", "scan_time": "01:00",
             "scan_interval_hours": "6", "scan_weekday": "6"}

#: 排程模式。weekly 是加法，daily／interval 的行為完全沒動
MODES = ("daily", "interval", "weekly")
WEEKDAY_LABEL = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")


# ---- 排程設定（存 app_settings）----
def get_schedule(conn) -> dict:
    return {
        "enabled": get_setting(conn, "scan_enabled", _DEFAULTS["scan_enabled"]) == "1",
        "mode": get_setting(conn, "scan_mode", _DEFAULTS["scan_mode"]),
        "time": get_setting(conn, "scan_time", _DEFAULTS["scan_time"]),
        "interval_hours": int(get_setting(conn, "scan_interval_hours", _DEFAULTS["scan_interval_hours"])),
        "weekday": int(get_setting(conn, "scan_weekday", _DEFAULTS["scan_weekday"])),
        "weekday_label": WEEKDAY_LABEL[int(get_setting(conn, "scan_weekday", _DEFAULTS["scan_weekday"])) % 7],
        "modes": list(MODES),
    }


def set_schedule(conn, enabled: bool, mode: str, time_str: str, interval_hours: int,
                 weekday: int | None = None) -> None:
    if mode not in MODES:
        raise ValueError(f"mode 只能是：{'、'.join(MODES)}")
    if mode in ("daily", "weekly"):
        try:
            hh, mm = time_str.split(":")
            if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError("時間要是 HH:MM（例：01:00）") from None
    if mode == "weekly":
        if weekday is None or not (0 <= int(weekday) <= 6):
            raise ValueError("每週排程要指定星期幾（0=週一 … 6=週日）")
        set_setting(conn, "scan_weekday", str(int(weekday)))
    set_setting(conn, "scan_enabled", "1" if enabled else "0")
    set_setting(conn, "scan_mode", mode)
    set_setting(conn, "scan_time", time_str)
    set_setting(conn, "scan_interval_hours", str(int(interval_hours)))


# ---- 手動/背景掃描 ----
def is_running() -> bool:
    return _running


def _scan_pending_settle(conn, trigger: str, paused: bool, ok: bool) -> None:
    """排程掃描『跑完』才結算補跑待辦（啟動≠完成）：中途進窗＝重新標待補跑；正常跑完＝done；其他失敗交給下個排程週期。
    補跑路徑（schedule-catchup）由 _quiet_catchup_run 自己結算，這裡不碰。"""
    if trigger not in ("schedule", "schedule-seg"):
        return
    import quiet_window as qw

    try:
        if paused:
            qw.pending_mark(conn, "scan_schedule", "排程掃描中途進入盤中靜默，待窗外補跑")
        elif ok:
            qw.pending_finish(conn, "scan_schedule", True, detail="掃描已跑完")
    except Exception as exc:  # noqa: BLE001 - 結算失敗不影響掃描結果，但要留日誌
        qw.LOG.error("盤中靜默：掃描補跑待辦結算失敗：%s", exc)


def _do_scan(trigger: str, only_ids: set[int] | None = None, light: bool = False, run=None) -> bool:
    """掃描。only_ids 給定＝只掃那些網段（各網段自訂排程用）。
    light=True（單一網段的排程掃描）跳過納管/採集/回檢等全域後處理——那些是整輪掃完才做一次，
    不需要每個網段各觸發一次。手動掃與全域排程 light=False，行為與以往完全相同。"""
    global _running
    import quiet_window as qw

    conn = get_connection()
    run_id = create_scan_run(conn, trigger)
    try:
      with qw.use(run if run is not None else qw.Run("scan_schedule", human=False, db_path=qw._path_of(conn))):
        sources = scan_targets(conn, only_ids)
        summary = run_scan(sources, conn)
        _recompare(conn)  # ② 掃完自動重比對，讓問題清單跟最新掃描一致
        # 好幾個網段加起來 0 台＝掃描器連不出去，不是網段都空了：記 failed＋開異常（見 scan_health）
        import scan_health

        empty_msg = scan_health.empty_failure(len(sources), summary["total_found"])
        if empty_msg:
            finish_scan_run(conn, run_id, "failed", 0, empty_msg)
            scan_health.raise_alert(conn, run_id, len(sources), empty_msg)
        else:
            finish_scan_run(conn, run_id, "ok", summary["total_found"], None)
        if not light and not (qw.current() and qw.current().paused):
            # AUD-02：後處理（SSH／WinRM 連目標機）的每一步各自在連線前重查靜默窗；掃描本身已因進窗而暫停的，
            # 整輪（含後處理）交給補跑，不在這裡接。人為（持票證）啟動的掃描 Run.human=True，不受影響。
            _post_scan_chain(conn)
        paused = bool(qw.current() and qw.current().paused)
        _scan_pending_settle(conn, trigger, paused, ok=not paused and not empty_msg)
        return not paused and not empty_msg
    except Exception as exc:  # noqa: BLE001 - 掃描失敗要如實記錄，不吞
        finish_scan_run(conn, run_id, "failed", 0, str(exc))
        return False
    finally:
        conn.close()
        with _lock:
            _running = False


def _post_scan_chain(conn) -> None:
    """掃描後處理：四個對目標機連線的步驟（每步各自在連線前 checkpoint）＋基線回檢（純比對 DB）。
    不信任任何 auto／manual 旗標：授權狀態只看目前執行緒的 Run（人為持票證啟動＝human，其餘進窗就停）。"""
    for step, fn in _POST_SCAN_STEPS:
        fn(conn)
    _post_scan_drift_check(conn)  # ⑥ 純比對 DB；要排在服務採集之後，用當天最新資料


def _post_scan_drift_check(conn) -> None:
    """上線基線回檢：已通過上線檢查的主機，auto 項跟當初宣告的基線比一次。

    **順序有意義**：排在服務採集之後，比對用的是今天剛收到的監聽埠，不是昨天的。
    排在前面的話，「Telnet 今天被打開」要等到隔天才會亮燈。

    失敗吞掉不外拋，理由同 auto_onboard：掃描本身已經成功，回檢是附加步驟，
    不該讓它把整次掃描記成 failed。
    """
    try:
        import golive

        golive.run_drift_check(conn)
    except Exception:  # noqa: BLE001 - 附加步驟失敗不影響掃描已完成的事實
        pass


def _post_scan_auto_onboard(conn) -> None:
    """B：排程掃描完成後，若總開關開著就跑一輪自動納管。

    只在「排程掃描成功」後接——這時才有新鮮的存活/指紋資料判斷誰未納管、是什麼平台。
    總開關預設關閉；沒開、沒授權網段、沒憑證都只是安靜不動作，不讓它拖累掃描本身。
    失敗吞掉不外拋：掃描已成功記錄在案，自動納管是附加步驟，不該讓它把整次掃描拉成 failed。
    """
    import quiet_window as qw

    if not qw.checkpoint(conn, "scan_post_auto_onboard"):   # AUD-02：每次對目標機連線前重查；進窗就不做，標 pending 由補跑 tick 重跑
        return
    try:
        import auto_onboard

        if not auto_onboard.is_enabled(conn):
            return
        auto_onboard.scheduled_cycle(conn=conn)
    except Exception:  # noqa: BLE001 - 附加步驟失敗不影響掃描已完成的事實
        pass


def _post_scan_collect_services(conn) -> None:
    """M2：掃描＋自動納管跑完後，對已納管主機收一輪服務。

    **順序有意義**：排在自動納管之後，這一輪剛納管進來的機器才會被收到，
    否則新機器要等到隔天才看得到服務。

    預設開啟（跟自動納管的預設關不同）：收服務是唯讀查詢，不像納管會在目標機
    建帳號改設定，沒有「無人在場自動改動主機」的風險，所以不需要那道授權閘門。
    要關就把 service_collect_enabled 設 0。

    失敗吞掉不外拋：掃描本身已經成功，服務採集是附加步驟，
    不該讓某台主機連不上就把整次掃描記成 failed。
    """
    import quiet_window as qw

    if not qw.checkpoint(conn, "scan_post_services"):   # AUD-02：每次對目標機連線前重查；進窗就不做，標 pending 由補跑 tick 重跑
        return
    try:
        if get_setting(conn, "service_collect_enabled", "1") != "1":
            return
        import service_inventory

        service_inventory.collect_services(conn, trigger="schedule")
    except Exception:  # noqa: BLE001 - 附加步驟失敗不影響掃描已完成的事實
        pass


def _post_scan_collect_accounts(conn) -> None:
    """M3：每晚掃描週期也收一輪帳號並跑稽核規則。

    稽核工具的價值在於資料新鮮——「上週的帳號稽核」對今天的稽核沒意義。
    跟服務盤點同樣預設開啟（唯讀查詢），要關把 account_collect_enabled 設 0。
    失敗吞掉不外拋：掃描已成功，帳號盤點是附加步驟。
    """
    import quiet_window as qw

    if not qw.checkpoint(conn, "scan_post_accounts"):   # AUD-02：每次對目標機連線前重查；進窗就不做，標 pending 由補跑 tick 重跑
        return
    try:
        if get_setting(conn, "account_collect_enabled", "1") != "1":
            return
        import account_inventory

        account_inventory.collect_accounts(conn, trigger="schedule")
    except Exception:  # noqa: BLE001 - 附加步驟失敗不影響掃描已完成的事實
        pass


def _post_scan_collect_software(conn) -> None:
    """軟體盤點也每晚收一輪：「哪台還在跑舊版 OpenSSL」要看今天的，不是上週的。

    唯讀查詢（rpm -qa／讀登錄檔），預設開啟；要關把 software_collect_enabled 設 0。
    失敗吞掉不外拋：掃描已成功，軟體盤點是附加步驟。
    """
    import quiet_window as qw

    if not qw.checkpoint(conn, "scan_post_software"):   # AUD-02：每次對目標機連線前重查；進窗就不做，標 pending 由補跑 tick 重跑
        return
    try:
        if get_setting(conn, "software_collect_enabled", "1") != "1":
            return
        import software_inventory

        software_inventory.collect_software(conn, trigger="schedule")
    except Exception:  # noqa: BLE001 - 附加步驟失敗不影響掃描已完成的事實
        pass


def _recompare(conn) -> None:
    """掃完自動重比對：取最新兩次掃描時間跑 run_comparison，更新問題清單。"""
    times = conn.execute(
        "SELECT DISTINCT scan_time FROM scan_history WHERE scan_ok = 1 ORDER BY scan_time DESC LIMIT 2"
    ).fetchall()
    if not times:
        return
    current = times[0]["scan_time"]
    previous = times[1]["scan_time"] if len(times) > 1 else None
    run_comparison(conn, current, previous)


def start_scan(trigger: str = "manual", only_ids: set[int] | None = None, light: bool = False,
               quiet_ticket: str | None = None) -> bool:
    """啟動一次掃描。回 True=已啟動；False=已有掃描在跑（防並發）或盤中靜默未啟動。
    only_ids／light 見 _do_scan；預設（手動掃）＝全部、完整後處理，跟以往一樣。
    AUD-02 硬守門：不信任 trigger（"manual" 也一樣）——窗內只有持有人工 API 簽發的有效票證（quiet_ticket）才放行；
    排程路徑沒有票證。"""
    global _running
    import quiet_window as qw

    gc = get_connection_for_guard()
    try:
        run = qw.begin(gc, "scan_schedule", quiet_ticket)
    finally:
        gc.close()
    if run is None:
        return False
    with _lock:
        if _running:
            return False
        _running = True
    threading.Thread(target=_do_scan, args=(trigger, only_ids, light, run), daemon=True).start()
    return True


def latest_status(conn) -> dict:
    row = get_latest_scan_run(conn)
    return {
        "running": is_running(),
        "status": row["status"] if row else "none",
        "found_count": row["found_count"] if row else None,
        "started_at": row["started_at"] if row else None,
        "finished_at": row["finished_at"] if row else None,
        "trigger": row["trigger"] if row else None,
        "error": row["error"] if row else None,
    }


#: 掃描後處理的步驟（順序有意義：納管 → 服務 → 帳號 → 軟體）。(step, fn(conn, auto))
_POST_SCAN_STEPS = (
    ("auto_onboard", lambda c: _post_scan_auto_onboard(c)),
    ("services", lambda c: _post_scan_collect_services(c)),
    ("accounts", lambda c: _post_scan_collect_accounts(c)),
    ("software", lambda c: _post_scan_collect_software(c)),
)


# AUD-02 補跑：入口代號 → 補跑函式（由 _quiet_catchup_tick 在窗外、依 pending 表執行；失敗有限次重試、遞增間隔）
def _catchup_scan(conn):
    """同步跑完整一輪掃描（在補跑背景執行緒裡），完成才回；別的掃描在跑＝忙碌，回 pending（不算失敗）。"""
    global _running
    with _lock:
        if _running:
            qw_run = __import__("quiet_window").current()
            if qw_run is not None:
                qw_run.paused = True
            return None
        _running = True
    return _do_scan("schedule-catchup", None, False, __import__("quiet_window").current())


def _catchup_dispatch_weekly(conn):
    import dispatch_weekly
    return dispatch_weekly.run_catchup(conn)


def _catchup_health(conn):
    import health_baseline
    health_baseline.run_weekly_baseline(conn)


def _catchup_vrops(conn):
    import vrops_import
    vrops_import.run_import(conn, "schedule", None)


def _catchup_whatsup(conn):
    import whatsup_service
    whatsup_service.poll(conn, "schedule", None)


def _catchup_post_onboard(conn):
    import post_onboard
    post_onboard.requeue_paused(conn)


def _catchup_storage_health(conn):
    """儲存設備每日檢查：執行中被盤中靜默窗打斷後的補跑。tick 本身依「今天哪台到期」判斷，所以重跑是冪等的。"""
    import storage_health

    return storage_health.tick(conn)


CATCHUP = {
    "storage_health_tick": _catchup_storage_health,
    "scan_schedule": _catchup_scan,
    "scan_post_auto_onboard": lambda c: _post_scan_auto_onboard(c),
    "scan_post_services": lambda c: _post_scan_collect_services(c),
    "scan_post_accounts": lambda c: _post_scan_collect_accounts(c),
    "scan_post_software": lambda c: _post_scan_collect_software(c),
    "vrops_import_tick": _catchup_vrops,
    "whatsup_poll_tick": _catchup_whatsup,
    "health_baseline_weekly": _catchup_health,
    "dispatch_weekly_tick": _catchup_dispatch_weekly,
    "post_onboard_queue": _catchup_post_onboard,
}


_catchup_lock = threading.Lock()          # 補跑背景執行緒的重入保護（鎖；worker 結束才放）


def _quiet_catchup_tick(conn, sync: bool = False) -> None:
    """排程器每分鐘呼叫：窗外且有待補跑的才啟動『背景執行緒』處理（防重入旗標），不在排程器主執行緒逐台 SSH。
    sync=True 只給測試（在目前執行緒同步跑）。"""
    import quiet_window as qw

    if qw.in_quiet_window(conn) or is_running():
        return
    if sync:
        _quiet_catchup_run(conn)
        return
    if _catchup_lock.locked():
        return
    if not qw.pending_list(conn) and not qw._MEM_PENDING:
        return
    if not _catchup_lock.acquire(blocking=False):
        return

    def worker():
        c = None
        try:
            c = get_connection()
            _quiet_catchup_run(c)
        except Exception as exc:  # noqa: BLE001 - 不吞：系統日誌＋狀態條警示
            qw.LOG.exception("盤中靜默：背景補跑發生未預期的錯誤：%s", exc)
            qw._HEALTH["catchup_worker_error"] = time.monotonic()
        finally:
            if c is not None:
                c.close()
            _catchup_lock.release()
    try:
        threading.Thread(target=worker, daemon=True, name="quiet-catchup").start()
    except Exception as exc:  # noqa: BLE001 - 啟動不了執行緒：放鎖、留警示，下一輪再試
        _catchup_lock.release()
        qw.LOG.exception("盤中靜默：背景補跑執行緒啟動失敗：%s", exc)
        qw._HEALTH["catchup_worker_error"] = time.monotonic()


def _catchup_result(res) -> tuple[bool, str]:
    """補跑函式回傳值 → (成功?, 說明)。False／失敗字典＝失敗（會重試，上限 3 次）；refused＝窗內被拒（回 pending 不算失敗）。"""
    if res is False:
        return False, "補跑函式回傳 False"
    if isinstance(res, dict):
        if res.get("refused"):
            return True, "refused"
        if res.get("status") in ("failed", "error") or res.get("ok") is False or res.get("error"):
            return False, str(res.get("error") or res.get("message") or res.get("status"))[:200]
    return True, ""


def _quiet_catchup_run(conn) -> None:
    """窗外把『盤中被延後／暫停』的任務補跑（pending 表；獨立於稽核表）。每項成功才標 done；失敗有限次（3）重試、間隔遞增，
    不會每分鐘重跑；補跑途中又進窗＝回 pending（不算失敗）。"""
    import quiet_window as qw

    if qw.in_quiet_window(conn) or is_running():
        return
    for entry in qw.pending_claim(conn):
        fn = CATCHUP.get(entry)
        run = qw.Run(entry, human=False, db_path=qw._path_of(conn))
        ok, detail = True, ""
        try:
            if fn is None:
                raise RuntimeError("沒有登記補跑函式")
            with qw.use(run):
                res = fn(conn)
            ok, detail = _catchup_result(res)
            if detail == "refused":                       # 窗內被拒（例如補跑時剛好又進窗）：不算失敗，回 pending
                run.paused, ok, detail = True, True, ""
        except Exception as exc:  # noqa: BLE001 - 失敗要記進 pending 表（有限次重試），不吞
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        qw.pending_finish(conn, entry, ok, paused=run.paused, detail=detail)


# ---- 排程判定 + 迴圈 ----
def _due(conn, now: datetime) -> bool:
    sched = get_schedule(conn)
    if not sched["enabled"]:
        return False
    last = get_last_schedule_run_time(conn)
    if sched["mode"] == "interval":
        if last is None:
            return True
        return (now - last).total_seconds() >= sched["interval_hours"] * 3600
    hh, mm = sched["time"].split(":")
    target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if now < target:
        return False
    if sched["mode"] == "weekly":
        # 每週：只有在指定的星期幾、過了設定時間、而且這一週還沒跑過才觸發。
        # 用「距今 7 天內有沒有跑過」判斷，比算週數簡單也不會在跨年時出錯。
        if now.weekday() != sched["weekday"]:
            return False
        if last is None:
            return True
        return (now - last).total_seconds() >= 6 * 24 * 3600
    # daily：到設定時間、且今天還沒被排程觸發過
    if last is None:
        return True
    return last.date() < now.date()


def _parse_dt(s: str | None):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[:len(fmt) + 2], fmt)
        except (ValueError, TypeError):
            continue
    return None


def _seg_due(row, now: datetime) -> bool:
    """這個網段自訂排程今天到點沒（daily）。沒設 scan_time／停用一律否。"""
    st = (row["scan_time"] or "").strip()
    if not st:
        return False
    if row["enabled"] is not None and not row["enabled"]:
        return False
    try:
        hh, mm = st.split(":")
        target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except (ValueError, AttributeError):
        return False
    if now < target:
        return False
    last = _parse_dt(row["last_scan_at"])
    return last is None or last.date() < now.date()


def _run_due_scans(conn, now: datetime) -> None:
    """先看全域排程、再看各網段自訂排程。一輪只啟動一個掃描（_running 互斥），
    其餘到點的下一輪（每分鐘）接著跑。

    ⚠️ 相容保證：**沒有任何網段設自訂時間時，走的是跟以往完全相同的 `start_scan("schedule")`**
    （掃全部、含 fallback 到本機網段）。自訂時間是加法，不改預設行為。"""
    import quiet_window as qw

    conns = list_connections(conn)
    custom = [c for c in conns if (c["scan_time"] or "").strip()]
    if _due(conn, now):
        if qw.defer_if_quiet(conn, "scan_schedule"):
            qw.pending_mark(conn, "scan_schedule", "排程掃描到期時在盤中靜默，延後")      # 確保窗外補跑會被叫到
            return      # AUD-02：盤中靜默（確實到期才記延後）。到期判斷不變，窗外第一個 tick 自然補跑一次
        if not custom:
            start_scan("schedule")                     # ← 與現況完全相同（待辦由 _do_scan 跑完才結算）
            return
        rest = {c["id"] for c in conns if not (c["scan_time"] or "").strip()}
        if rest:
            start_scan("schedule", only_ids=rest)      # 有自訂時間的各自跑，這裡掃其餘（待辦由 _do_scan 跑完才結算）
            return
        # 全部網段都有自訂時間 → 全域沒東西可掃，往下讓各網段自訂排程接手
    for c in custom:
        if _seg_due(c, now):
            if qw.defer_if_quiet(conn, "scan_schedule"):
                qw.pending_mark(conn, "scan_schedule", "網段自訂排程到期時在盤中靜默，延後")
                return
            if start_scan("schedule-seg", only_ids={c["id"]}, light=True):
                mark_connection_scanned(conn, c["id"], now.strftime("%Y-%m-%d %H:%M:%S"))
            return


def get_connection_for_guard():
    """start_scan 沒有 conn 參數：守門用自己的短連線（用完即關）。"""
    return get_connection()


def _scheduler_once(conn) -> None:
    """排程器一輪。AUD-02：窗外釋放後各類 A 入口錯開 1–4 分鐘啟動（STAGGER），不在 15:00:00 同一分鐘全部啟動；
    窗內（since<0）照常呼叫各 tick，讓它們自己守門並記延後。補跑改丟背景執行緒，不卡住後面的 tick（Teams 告警等）。"""
    import quiet_window as qw

    since = qw.minutes_since_release(conn)

    def ready(key: str) -> bool:
        return since < 0 or since >= qw.STAGGER[key]
    if not is_running():
        _run_due_scans(conn, qw.local_now())            # 全站一律台北時鐘（不用 datetime.now()）
    _vcenter_autoimport_tick(conn)
    if ready("integrations"):
        _integrations_tick(conn)
    if ready("catchup"):
        _quiet_catchup_tick(conn)
    _ci_graph_tick(conn)
    _track_snapshot_tick(conn)          # 2.044.3：1.4 追蹤處理每小時快照＋盤後一次（純本機計算，B 類）
    if ready("archive"):
        _capacity_archive_tick(conn)
    if ready("health"):
        _health_baseline_tick(conn)
    if ready("storage"):
        _storage_health_tick(conn)
    _hk_night_tick(conn)                # 2.046.0（R19）：目錄清理夜間自動，預設關閉；19:00～07:00 才動，自己守 quiet_window
    if ready("relay"):
        _relay_import_tick(conn)
    _vuln_notify_tick(conn)
    _eos_notify_tick(conn)
    _daily_digest_tick(conn)
    _wug_alert_tick(conn)               # 每分鐘、不錯開、不受盤中靜默窗限制（C 類例外，見 wug_alert.py）；排在 Teams 前面，同一分鐘就送出
    _teams_notify_tick(conn)
    if ready("paste"):
        _paste_watch_tick(conn)
    if ready("dispatch"):
        _dispatch_weekly_tick(conn)


def _scheduler_loop() -> None:
    while True:
        try:
            conn = get_connection()
            try:
                _scheduler_once(conn)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - 排程器不能因單次錯誤整個掛掉
            pass
        time.sleep(60)


def _vcenter_autoimport_tick(conn) -> None:
    """VC 自動匯入（方案 B）：每輪看監看資料夾有沒有新的 RVTools 匯出，有才抓。

    刻意跟掃描分開判定——vCenter 匯出的節奏跟網段掃描無關。反應式（有新檔才動作、
    同一份不重匯），關著或沒設資料夾就安靜略過，不拖累排程器。
    """
    try:
        import vcenter_autoimport

        vcenter_autoimport.tick(conn)
    except Exception:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體
        pass


def _integrations_tick(conn) -> None:
    """外部系統整合（Aria Operations 匯入、WhatsUp Gold 告警輪詢）：模組關著或沒啟用就安靜略過；
    各自失敗都只記進自己的執行紀錄，不拖累排程器。"""
    for mod in ("vrops_import", "whatsup_service"):
        try:
            __import__(mod).tick(conn)
        except Exception:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體（錯誤已寫進該整合的 runs 表）
            pass


def _ci_graph_tick(conn) -> None:
    """CI 圖譜每日重建：到設定時間（預設 03:00）且今天還沒排程跑過就重建一次。

    為什麼排在掃描之後而不是 00:00：圖譜的原料是 hardware 與 source_record，這兩者
    由上面的夜間掃描（預設 01:00）刷新，掃完後面還串著自動納管、服務採集、帳號盤點。
    排 00:00 會拿前一天的舊資料重建——看起來有跑、內容卻是舊的，比沒跑更難察覺。

    已有重建在跑就安靜跳過等下一輪（每分鐘一輪，不會漏），不像 HTTP 端點要回 409：
    排程沒有人在等回應，重試比報錯有意義。
    """
    try:
        import ci_graph

        if ci_graph.is_due(conn, datetime.now()):
            ci_graph.run_rebuild(conn, "schedule", "scheduler")
    except Exception:  # noqa: BLE001 - 失敗已寫進 ci_graph_runs，不能拖垮排程器本體
        pass


def _capacity_archive_tick(conn) -> None:
    """容量分析日摘要封存：收盤後把當天（T+1 再升級成完成版）的每台主機日摘要凍結。純本機計算，但使用者要求盤中不要有任何自動重運算，
    所以走 quiet_window 入口 capacity_archive_tick（窗內不動並記延後，窗外第一個 tick 補）。**這裡只封存，從不清理**（清理只有 admin 手動）。"""
    try:
        import capacity_archive

        capacity_archive.tick(conn)
    except Exception as exc:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體；錯誤寫系統日誌，下一輪會再試
        import logging

        logging.getLogger("capacity_archive").error("日摘要封存 tick 失敗：%s", exc)


def _relay_import_tick(conn) -> None:
    """中繼檔案匯入：依設定的間隔看共用資料夾有沒有新批次，有才匯（冪等）。純讀檔，但屬於重運算，
    所以走 quiet_window 入口 relay_import_tick（窗內不動並記延後，窗外下一輪補）。關著或沒設資料夾就安靜略過。"""
    try:
        import relay_import

        relay_import.tick(conn)
    except Exception as exc:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體；錯誤寫系統日誌，下一輪會再試
        import logging

        logging.getLogger("relay_import").error("中繼匯入 tick 失敗：%s", exc)


def _track_snapshot_tick(conn) -> None:
    """1.4 追蹤處理快照（2.044.3，GPT A3）：每小時一份＋15:05 後當天一份「盤後」，給首頁／追蹤處理算今日新增與解除
    （GET 只讀，不再由開頁的人寫入）。只用本機資料庫計算，不連任何主機。"""
    try:
        import v2_today

        v2_today.tick(conn, __import__("quiet_window").local_now())
    except Exception as exc:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體；錯誤寫系統日誌，下一輪會再試
        import logging

        logging.getLogger("v2_today").error("追蹤處理快照失敗：%s", type(exc).__name__)


def _wug_alert_tick(conn) -> None:
    """WhatsUp 告警（2.043.0）：每分鐘匯入中繼 PC 新放進來的告警快照、檢查心跳、排 Teams 通知。只讀檔案、寫本機、送通知，
    不連任何主機也不連 WhatsUp；使用者 2026-11-03 決定監控 24 小時，所以登記為 C 類例外（wug_alert_tick）。未啟用就安靜略過。"""
    try:
        import wug_alert

        wug_alert.tick(conn)
    except Exception as exc:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體；錯誤寫系統日誌，下一輪會再試
        import logging

        logging.getLogger("wug_alert").error("告警 tick 失敗：%s", type(exc).__name__)


def _storage_health_tick(conn) -> None:
    """儲存設備每日唯讀健康與告警檢查（批 S3）：每台每日 07:00（台北）起一次，只發 GET。模組 storage_api 關著就安靜略過。
    走 quiet_window 入口 storage_health_tick（窗內不連線並記延後，窗外第一個 tick 補）；一個 tick 最多一台。**不送任何通知。**"""
    try:
        import storage_health

        storage_health.tick(conn)
    except Exception as exc:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體；錯誤寫系統日誌，下一輪依到期判斷再試
        import logging

        logging.getLogger("storage_health").error("儲存設備每日檢查 tick 失敗：%s", type(exc).__name__)


def _hk_night_tick(conn) -> None:
    """目錄清理夜間自動（R19）：預設關閉；一次一台、每台每晚最多一次；quiet_window 入口 hk_night_tick。"""
    try:
        import dir_housekeeping

        dir_housekeeping.night_tick(conn)
    except Exception as exc:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體；錯誤寫系統日誌，下一輪再試
        import logging

        logging.getLogger("dir_housekeeping").error("目錄清理夜間自動 tick 失敗：%s", type(exc).__name__)


def _paste_watch_tick(conn) -> None:
    """ONBOARD-FLOW1：主機上跑完 webit3_onboard.sh 的，自動偵測、自動收（每台 2 分鐘最多試一次）。"""
    try:
        import paste_watch

        paste_watch.tick_in_background()      # 背景跑，不卡排程器本體
    except Exception:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體
        pass


def _dispatch_weekly_tick(conn) -> None:
    """2-1 開始收集每週一次整片網段（使用者 2026-10-19：「每週一次在晚上自動跑（六晚上）」）。
    到點且今天還沒跑過才在背景開跑；已有一輪收集在跑就等下一分鐘。"""
    try:
        import dispatch_weekly

        dispatch_weekly.tick(conn, datetime.now())
    except Exception:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體
        pass


def _vuln_notify_tick(conn) -> None:
    """B-19 弱點到期寄信：每週一／每月 1 號到點寄。仿上面幾顆——到點且這期還沒
    寄成才寄，寄不成一小時後自己補（不是整週不寄），每一次嘗試都留在 vuln_notify_run。

    **預設是關的**：設定沒填完、AD 沒匯入的系統不可以自己開始對全公司寄信。
    """
    try:
        import vuln_notify

        vuln_notify.tick(conn, datetime.now())
    except Exception:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體
        pass


def _eos_notify_tick(conn) -> None:
    """Q95 EOS 到期寄信：每月 1 號到點寄（硬體→SP、作業系統→AP）。仿 _vuln_notify_tick：
    到點且這期還沒寄成才寄，寄不成一小時後自己補，每一次嘗試都留在 eos_notify_run。
    預設關閉：沒在設定頁明確啟用的系統不會自己寄。"""
    try:
        import eos_notify

        eos_notify.tick(conn, datetime.now())
    except Exception:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體
        pass


def _daily_digest_tick(conn) -> None:
    """每日變化報告＋早上摘要信（v1.500.0）：到點寫今天的快照；寄信預設關著，
    開了才到點寄，寄不成一小時後自己補，每一次嘗試都記在 digest_mail_log。"""
    try:
        import daily_digest

        daily_digest.tick(conn, datetime.now())
    except Exception:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體
        pass


def _teams_notify_tick(conn) -> None:
    """ACT-03 Teams 單向通知：預設關著；開了才把告警／事件／摘要排進佇列並送出（失敗退避重試）。"""
    try:
        import teams_notify

        teams_notify.tick(conn)
    except Exception:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體
        pass


def _health_baseline_tick(conn) -> None:
    """主機自我檢查的健康基準：每週六離峰對已納管主機收一份，事發時可比對（批次 2）。
    仿 _ci_graph_tick——到點且本週還沒跑過才跑，已跑過安靜跳過等下一輪。"""
    try:
        import health_baseline
        import quiet_window as qw

        if health_baseline.baseline_due(conn, qw.local_now()):
            if qw.defer_if_quiet(conn, "health_baseline_weekly"):
                qw.pending_mark(conn, "health_baseline_weekly", "每週健康基準到期時在盤中靜默，延後")
                return      # AUD-02：盤中靜默；週六本來不在窗內，這裡是休市日設定／窗設定被改時的保險
            out = health_baseline.run_weekly_baseline(conn)
            if not (isinstance(out, dict) and (out.get("refused") or out.get("paused"))):
                qw.pending_finish(conn, "health_baseline_weekly", True, detail="已依排程執行")
    except Exception:  # noqa: BLE001 - 附加步驟失敗不影響排程器本體
        pass


def start_scheduler() -> None:
    """在 app 啟動時呼叫（由 webit3-api.service 設 ASSET_SCHEDULER=1 觸發）。"""
    init_db()
    # 上一輪程序若在重建半途被砍（部署重啟/OOM/機器重開），ci_graph_runs 會留下
    # 永遠不會結束的 running 列，之後每一次重建都被它擋掉。啟動時先收乾淨。
    try:
        import ci_graph

        conn = get_connection()
        try:
            ci_graph.reclaim_stale_runs(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 回收失敗不該擋住服務啟動
        pass
    threading.Thread(target=_scheduler_loop, daemon=True).start()
