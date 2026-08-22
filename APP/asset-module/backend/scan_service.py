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
    set_setting,
)
from comparison_engine import run_comparison
from run_real_scan import scan_targets  # 重用「從 connections 讀網段目標」的邏輯
from scanner import run_scan

_lock = threading.Lock()
_running = False

_DEFAULTS = {"scan_enabled": "1", "scan_mode": "daily", "scan_time": "01:00", "scan_interval_hours": "6"}


# ---- 排程設定（存 app_settings）----
def get_schedule(conn) -> dict:
    return {
        "enabled": get_setting(conn, "scan_enabled", _DEFAULTS["scan_enabled"]) == "1",
        "mode": get_setting(conn, "scan_mode", _DEFAULTS["scan_mode"]),
        "time": get_setting(conn, "scan_time", _DEFAULTS["scan_time"]),
        "interval_hours": int(get_setting(conn, "scan_interval_hours", _DEFAULTS["scan_interval_hours"])),
    }


def set_schedule(conn, enabled: bool, mode: str, time_str: str, interval_hours: int) -> None:
    if mode not in ("daily", "interval"):
        raise ValueError("mode 只能是 daily 或 interval")
    set_setting(conn, "scan_enabled", "1" if enabled else "0")
    set_setting(conn, "scan_mode", mode)
    set_setting(conn, "scan_time", time_str)
    set_setting(conn, "scan_interval_hours", str(int(interval_hours)))


# ---- 手動/背景掃描 ----
def is_running() -> bool:
    return _running


def _do_scan(trigger: str) -> None:
    global _running
    conn = get_connection()
    run_id = create_scan_run(conn, trigger)
    try:
        sources = scan_targets(conn)
        summary = run_scan(sources, conn)
        _recompare(conn)  # ② 掃完自動重比對，讓問題清單跟最新掃描一致
        finish_scan_run(conn, run_id, "ok", summary["total_found"], None)
        _post_scan_auto_onboard(conn)  # ③ B：掃完接自動納管（要靠新鮮掃描資料判未納管＋指紋）
        _post_scan_collect_services(conn)  # ④ M2：納管完再收一輪服務（順序不能顛倒）
        _post_scan_collect_accounts(conn)  # ⑤ M3：帳號盤點也每晚刷新（稽核資料要新鮮）
        _post_scan_drift_check(conn)  # ⑥ 上線基線回檢（要排在服務採集之後，用當天最新資料）
    except Exception as exc:  # noqa: BLE001 - 掃描失敗要如實記錄，不吞
        finish_scan_run(conn, run_id, "failed", 0, str(exc))
    finally:
        conn.close()
        with _lock:
            _running = False


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
    try:
        if get_setting(conn, "account_collect_enabled", "1") != "1":
            return
        import account_inventory

        account_inventory.collect_accounts(conn, trigger="schedule")
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


def start_scan(trigger: str = "manual") -> bool:
    """啟動一次掃描。回 True=已啟動；False=已有掃描在跑（防並發）。"""
    global _running
    with _lock:
        if _running:
            return False
        _running = True
    threading.Thread(target=_do_scan, args=(trigger,), daemon=True).start()
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
    # daily：到設定時間、且今天還沒被排程觸發過
    hh, mm = sched["time"].split(":")
    target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if now < target:
        return False
    if last is None:
        return True
    return last.date() < now.date()


def _scheduler_loop() -> None:
    while True:
        try:
            conn = get_connection()
            try:
                if not is_running() and _due(conn, datetime.now()):
                    start_scan("schedule")
                _vcenter_autoimport_tick(conn)
                _ci_graph_tick(conn)
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
