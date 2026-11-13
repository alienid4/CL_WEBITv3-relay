"""AUD-02 全站「盤中靜默窗」：平日開盤時間內，系統自己發動的探測／掃描／收集一律不動。

使用者 2026-10-30 原話：「開盤時間別去偵測什麼。如果只是做報告，可以等開盤之後再來，或夜間再收集。
開盤時間除非是人為主動去做收集的動作，不然都不要有自動的排查。我不想因為我們做了什麼事情，影響到線上服務。」

## 規則（單一真相來源在這個檔；其他地方不准再寫第二份時間定義）

1. 窗口預設＝transfer_policy 的 MARKET_OPEN～MARKET_CLOSE（平日 08:30–15:00，伺服器時間 Asia/Taipei）。
   **左閉右開**：08:30:00 起在窗內、14:59:59 仍在窗內、15:00:00 解除。週六日不在窗內。
   休市日沿用容量分析頁已有的設定 `capacity_holidays`（同一份，不另存），預設空。
2. 設定（app_settings）：quiet_window_enabled（預設 1）、quiet_window_start／quiet_window_end（預設 08:30／15:00）。
   關閉要寫稽核（由設定 API 寫），畫面醒目標示。
   **設定壞掉、讀不出來＝視同在窗內（fail-closed）**：寧可多等，不可在盤中亂動。
3. 「自動」入口（背景排程、啟動時、被動觸發）在最前面呼叫 `defer_if_quiet(conn, <入口代號>)`：
   回 True＝現在在窗內，呼叫端必須**立刻返回、不呼叫任何對外連線函式**。這支函式**沒有任何參數可以繞過**。
   延後會記一筆 quiet_window_log（同一入口同一天只一列，次數累加，避免每分鐘洗版）。
4. 窗外第一個 tick 的補跑：各排程原本的「到期判斷」（今天還沒跑過／距離上次超過間隔）在窗內被跳過後仍然成立，
   所以窗外第一個 tick 自然補跑一次，不會跑兩次；有時間窗限制的（每週收集的 CATCH_UP_HOURS）另外看
   `was_deferred_on`，不會因為整個補跑窗都落在盤中而整週不跑。
5. 「人工」入口（使用者按按鈕／打 API）不被擋，但窗內要二次確認：呼叫 `require_manual_confirm`，
   沒帶 confirm_in_window=true 回 409（code=quiet_window_confirm），帶了才放行並寫稽核（誰、何時、哪個入口）。
   **只有人工路徑有這個參數**；自動路徑沒有，也不接受。
6. 對外通知（Email／Teams，C 類）與純本機計算（B 類）不受限；分類與理由見 docs/codex-review/2026-10-30/115。

## 入口登記（ENTRIES）

每個背景入口都要登記在 ENTRIES：分類 A（對線上主機／平台連線→必須守門）、B（純本機）、C（對外通知）、
H（使用者自己排入的檔案傳送，由 Q94 傳送規則自管）、M（人工入口開的背景執行緒，自動呼叫者在上游已守門）。
判不出就當 A。tests/asset_module/test_quiet_window_entries.py 會掃 backend 內所有 Thread／tick／排程呼叫，
沒登記的就紅，避免日後新增入口漏守門。
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import functools
import json
import logging
import re
import secrets
import sqlite3
import threading
import time as _time

import transfer_policy as tp

KEY_ENABLED = "quiet_window_enabled"
KEY_START = "quiet_window_start"
KEY_END = "quiet_window_end"
KEY_HOLIDAYS = "capacity_holidays"           # 與容量分析頁同一份（capacity_analysis.HOLIDAY_KEY）
KEY_POST_SCAN_PENDING = "quiet_window_post_scan_pending"

CONFIRM_CODE = "quiet_window_confirm"
LOG_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS quiet_window_log ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, entry TEXT NOT NULL, kind TEXT NOT NULL, "
    "first_at TEXT NOT NULL, last_at TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 1, "
    "actor TEXT, detail TEXT)"
)
LOG_INDEX_SQL = ("CREATE UNIQUE INDEX IF NOT EXISTS ux_quiet_window_deferred ON quiet_window_log(day, entry) "
                 "WHERE kind = 'deferred'")

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


# ============================================================ 入口登記
def _e(cls, name, where, trigger, touches, manual="", why=""):
    return {"cls": cls, "name": name, "where": where, "trigger": trigger, "touches": touches,
            "manual": manual, "why": why}


#: key＝入口代號（defer_if_quiet 的第二個參數；A 類一定要有）。where＝守門所在「檔案:函式」。
ENTRIES: dict[str, dict] = {
    # ---- A：會對線上主機／平台連線、掃描、收集 → 盤中自動必須跳過
    "scan_schedule": _e("A", "排程網段存活掃描（全域＋各網段自訂時間）", "scan_service.py:_run_due_scans", "背景排程（每分鐘檢查）",
                        "網段 ICMP／TCP 探測", "POST /api/scan/run"),
    "scan_post_auto_onboard": _e("A", "掃描後自動納管", "scan_service.py:_post_scan_auto_onboard", "排程掃描後接續",
                                 "SSH／WinRM 登入目標機建帳號", "POST /api/auto-onboard/run"),
    "scan_post_services": _e("A", "掃描後服務盤點", "scan_service.py:_post_scan_collect_services", "排程掃描後接續",
                             "SSH／WinRM 唯讀收集", "POST /api/services/collect"),
    "scan_post_accounts": _e("A", "掃描後帳號盤點", "scan_service.py:_post_scan_collect_accounts", "排程掃描後接續",
                             "SSH／WinRM 唯讀收集", "POST /api/accounts/collect"),
    "scan_post_software": _e("A", "掃描後軟體盤點", "scan_service.py:_post_scan_collect_software", "排程掃描後接續",
                             "SSH／WinRM 唯讀收集", "POST /api/software/collect"),
    "scan_post_spec": _e("A", "掃描後主機規格更新（每台每 N 天一次）", "scan_service.py:_post_scan_collect_spec", "排程掃描後接續",
                         "SSH／WinRM 唯讀收集", "主機規格「收集」按鈕（/api/host-spec/collect，人工入口走 manual_diag 守門）"),
    "vrops_import_tick": _e("A", "Aria Operations（vROps）自動匯入", "vrops_import.py:tick", "背景排程（間隔制）",
                            "vROps API", "POST /api/integrations/vrops/run"),
    "whatsup_poll_tick": _e("A", "WhatsUp Gold 告警輪詢", "whatsup_service.py:tick", "背景排程（間隔制）",
                            "WhatsUp Gold API", "POST /api/integrations/whatsup/poll"),
    "health_baseline_weekly": _e("A", "每週健康基準收集", "scan_service.py:_health_baseline_tick", "背景排程（週六）",
                                 "SSH／WinRM 對已納管主機", "POST /api/health-check/baseline/run"),
    "paste_watch_tick": _e("A", "貼指令納管後自動偵測", "paste_watch.py:tick_in_background", "背景排程（每分鐘）",
                           "SSH 試連等待中主機", "（無按鈕；畫面輪詢只讀）"),
    "dispatch_weekly_tick": _e("A", "2-1 每週整片網段收集", "dispatch_weekly.py:tick", "背景排程（每週）",
                               "網段探測＋SSH／WinRM 收集", "POST /api/collect/dispatch"),
    "capacity_wu_refresh": _e("A", "容量分析 WhatsUp 效能讀取", "capacity_analysis.py:wu_refresh", "排程函式（目前未接排程）",
                              "WhatsUp Gold API", "（尚無路由；函式 manual=True 需確認）"),
    "capacity_archive_tick": _e("A", "容量分析日摘要封存（純本機計算）", "capacity_archive.py:tick", "背景排程（每分鐘檢查，有新的交易日才動）",
                                "只讀本機資料庫、寫本機封存表（不連線）；使用者要求盤中不要有任何自動的重運算", "POST /api/v2/capacity/archive/run",
                                "即使純本機也放窗外"),
    "storage_health_tick": _e("A", "儲存設備（Dell Unity）每日唯讀健康與告警檢查", "storage_health.py:tick", "背景排程（每分鐘檢查；每台每日 07:00 起一次，失敗最多重試 3 次）",
                              "Storage 管理介面 REST（僅 GET）；不送任何通知、不確認／清除告警", "POST /api/storage/devices/{id}/health-check",
                              "07:00 在窗外，週末也跑"),
    "hk_night_tick": _e("A", "目錄清理夜間自動掃描（預設關閉；19:00～07:00 開新的一台、08:30 前結束）", "dir_housekeeping.py:night_tick",
                        "背景排程（每分鐘檢查，一次一台、每台每晚最多一次）", "SSH／WinRM 唯讀盤點（收集帳號）", "（沒有獨立的人工路徑；人工掃描是 POST /api/housekeeping/scan，走 manual_diag）",
                        "時段本來就在窗外；仍守門，設定若改了也不會在盤中動"),
    "relay_import_tick": _e("A", "中繼檔案匯入（WhatsUp／Unity／vCenter 的收集結果檔）", "relay_import.py:tick", "背景排程（依設定間隔，預設每 15 分鐘看一次共用資料夾）",
                            "只讀本機（或掛載）共用資料夾的檔案、寫本機資料表（不連線）；使用者要求盤中不要有任何自動的重運算", "POST /api/v2/relay-import/run",
                            "即使純讀檔也放窗外"),
    # ---- B：純本機計算／備份／重建 → 不受限
    "vcenter_autoimport_tick": _e("B", "RVTools 匯出檔自動匯入", "scan_service.py:_vcenter_autoimport_tick", "背景排程", "只讀本機監看資料夾的檔案",
                                  why="不連任何線上系統；只讀檔案"),
    "ci_graph_tick": _e("B", "CI 圖譜每日重建", "scan_service.py:_ci_graph_tick", "背景排程", "只用本機資料庫", why="純本機重建"),
    "track_snapshot_tick": _e("B", "1.4 追蹤處理快照（每小時＋15:05 盤後一次）", "scan_service.py:_track_snapshot_tick", "背景排程（每小時）",
                              "只用本機資料庫計算、寫本機快照表", why="純本機計算，不連任何主機（2.044.3，算今日新增／解除用）"),
    "post_scan_drift_check": _e("B", "上線基線回檢", "scan_service.py:_post_scan_drift_check", "排程掃描後接續", "只比對資料庫既有資料",
                                why="不連線，golive.run_drift_check 只比對 DB"),
    "audit_digest_scheduled": _e("B", "稽核表外部摘要留存", "transfer_service.py:start_resumer", "每小時", "本機資料庫＋本機檔案", why="純本機"),
    "startup_housekeeping": _e("B", "服務啟動時收尾／清舊紀錄", "api.py:_start_scheduler", "啟動時", "本機資料庫", why="純本機，不對外連線"),
    # 2.055.2 加的啟動事件（CIA 匯入兩段式），當時漏登記（2.058.18 補）：只刪本機私有資料夾裡過期的預覽檔
    "startup_cia_preview_cleanup": _e("B", "服務啟動時清過期的 CIA 匯入預覽檔", "api.py:_cleanup_cia_previews", "啟動時",
                                      "本機私有資料夾的預覽檔", why="純本機檔案清理，不對外連線"),
    "backup_cleanup_timers": _e("B", "systemd 備份／清理計時器", "deploy.sh", "systemd timer（02:00／03:00）", "本機檔案", why="夜間、純本機"),
    # ---- C：對外通知（不探測目標系統）→ 不受限（重大告警本來就要在盤中送）
    "vuln_notify_tick": _e("C", "弱點到期寄信", "scan_service.py:_vuln_notify_tick", "背景排程", "SMTP 寄信", why="只寄信，不探測目標系統"),
    "eos_notify_tick": _e("C", "EOS 到期寄信", "scan_service.py:_eos_notify_tick", "背景排程", "SMTP 寄信", why="只寄信，不探測目標系統"),
    "daily_digest_tick": _e("C", "每日異動摘要（快照＋寄信）", "scan_service.py:_daily_digest_tick", "背景排程", "本機快照＋SMTP", why="只讀本機資料並寄信"),
    "teams_notify_tick": _e("C", "Teams 單向通知", "scan_service.py:_teams_notify_tick", "背景排程", "本機佇列＋Teams webhook", why="只送通知，不探測目標系統"),
    "notice_digest_tick": _e("C", "非緊急通知每日彙整（🟡 告警、磁碟準備級；預設每天 08:00 一則）", "scan_service.py:_notice_digest_tick",
                             "背景排程（每分鐘檢查）", "只讀本機彙整表、寫本機、排 Teams 佇列", why="只送通知，不連任何主機"),
    "daily_check_tick": _e("C", "1.5 每日檢查（06:30 產生當天快照、07:45 提醒、08:15 升級；預設關閉）", "scan_service.py:_daily_check_tick",
                           "背景排程（每分鐘檢查）", "只讀本機已匯入的資料（WhatsUp 告警快照、效能／磁碟中繼）、寫本機每日檢查表、排 Teams 佇列",
                           why="只讀本機資料並送通知，不連任何主機、不連 WhatsUp"),
    "wug_lastpoll_tick": _e("C", "WhatsUp 上次輪循群組快照匯入（每分鐘撿 wuglp_ 批次，給 1.5 每日檢查）", "scan_service.py:_wug_lastpoll_tick",
                            "背景排程（每分鐘，24 小時）", "只讀中繼資料夾裡 wuglp_ 開頭的批次檔、寫本機上次輪循資料表；心跳中斷／恢復各排一則 Teams 佇列",
                            why="資料小（一批幾千列）、只寫表不重算全站，跟告警快照同級（S13 設計 §5）；不連任何主機、也不連 WhatsUp，"
                                "9.26 收集腳本只對 WhatsUp 做 GET"),
    "aria_dailycheck_tick": _e("C", "VMware 每日檢查快照匯入（每分鐘撿 ariadc_ 批次，給 1.5 每日檢查 VC 列；新的完整批次到了且還沒人確認就重新產生）",
                               "scan_service.py:_aria_dailycheck_tick", "背景排程（每分鐘，24 小時）",
                               "只讀中繼資料夾裡 ariadc_ 開頭的批次檔、寫本機 VMware 快照表與每日檢查表",
                               why="資料小（一天三批、幾百列）、只寫表不重算全站，跟上次輪循同級；不連 vROps、不連 vCenter／ESXi（天條），"
                                   "跳板機 收集腳本只對 vROps suite-api 做 GET＋登入"),
    "wug_alert_tick": _e("C", "WhatsUp 告警事件（每分鐘匯入告警快照、心跳、排 Teams 通知）", "scan_service.py:_wug_alert_tick", "背景排程（每分鐘，24 小時）",
                         "只讀中繼資料夾裡 wugalert_ 開頭的批次檔、寫本機告警事件表、排 Teams 佇列",
                         why="唯讀監控例外（使用者 2026-11-03 決定：監控要 24 小時、含開盤時間）。只讀 WhatsUp 收集端放進來的檔案，"
                             "不連任何主機、也不連 WhatsUp；中繼 PC 端的收集腳本也只對 WhatsUp 做 GET"),
    # ---- 檔案傳送：自動續傳／到期自動開始屬 A；人在窗內按「開始傳送」才是人為（需確認票證）
    "transfer_resumer": _e("A", "檔案傳送：自動續傳／到期自動開始", "transfer_service.py:start_job", "背景（每分鐘）＋人工開始傳送",
                           "SSH／WinRM 傳檔", "POST /api/transfer/jobs"),
    "post_onboard_queue": _e("A", "納管後收集隊伍（逐台）", "post_onboard.py:_queue_worker", "納管／收集後自動排隊＋人工按鈕",
                             "SSH／WinRM 唯讀收集", "POST /api/onboard/post-collect-batch"),
    # ---- M：人工入口開的背景執行緒（自動呼叫者在上游已守門）
    "thread_scan_start": _e("M", "start_scan 背景執行緒", "scan_service.py:start_scan", "人工按鈕／排程呼叫（schedule* 於此再守一層）", "同 scan_schedule"),
    "thread_catchup": _e("M", "補跑背景執行緒（pending 表）", "scan_service.py:_quiet_catchup_tick", "窗外且有待補跑", "只呼叫已守門的入口（@guarded／checkpoint）"),
    "thread_scheduler": _e("M", "排程迴圈本體", "scan_service.py:start_scheduler", "服務啟動", "只呼叫各 tick（各 tick 自己登記）"),
    "thread_collect_dispatch": _e("M", "start_background（2-1 收集）", "collect_dispatch.py:start_background", "人工按鈕；每週排程經 dispatch_weekly_tick",
                                  "同 dispatch_weekly_tick"),
    "manual_diag": _e("A", "人為診斷／連線測試／單台與批次納管／貼指令納管（router 端點）", "routers/*（dependencies=gate）", "人工按鈕",
                      "對指定主機的探測、SSH／WinRM 測試、納管", "各端點（見 test_quiet_window_entries 的清單）"),
    "cmd_local_b": _e("B", "命令列工具（本機資料庫維護／匯入／種子資料／驗證）", "各檔 __main__", "人工命令列", "只用本機資料庫與檔案", why="不連任何線上系統"),
    "external_scan_segments": _e("B", "外部掃描機的 scan_segments.py（mmap.sh 寫 /etc/cron.d/webit3-scan）", "scan_segments.py:main", "外部 cron，預設 22:00–06:00",
                                 "外部掃描機對網段探測", why="不在本 app；預設夜間時段，且 scan_segments 自帶 08:30–15:00（台北，平日）拒絕，--now 也一樣，人為要 --confirm-in-window"),
    "thread_post_onboard_start": _e("M", "post_onboard.start", "post_onboard.py:start", "納管後；呼叫者為人工 API 或已守門的自動入口", "SSH／WinRM 唯讀收集"),
    "thread_batch_onboard": _e("M", "批次自動納管", "batch_onboard_service.py:start", "人工按鈕", "SSH 納管"),
    "thread_dir_housekeeping": _e("M", "目錄清理掃描排隊", "dir_housekeeping.py:queue_scan", "人工按鈕；夜間自動經 hk_night_tick 守門後", "SSH 唯讀盤點"),
    "thread_paste_watch_worker": _e("M", "paste_watch 背景工人", "paste_watch.py:tick_in_background", "由 paste_watch_tick 守門後才建立", "同 paste_watch_tick"),
    "thread_transfer_spawn": _e("M", "檔案傳送工作執行緒", "transfer_service.py:spawn", "start_job 守門後才建立", "同 transfer_resumer"),
    "thread_transfer_reader": _e("M", "傳送讀取泵", "transfer_runner.py:_capped_get", "傳送工作內部", "同 transfer_resumer"),
}

#: Thread／Timer 建立點（檔案:函式）→ 對應的 ENTRIES 代號。入口完整性測試用。
THREAD_SITES: dict[str, str] = {
    "batch_onboard_service.py:start": "thread_batch_onboard",
    "collect_dispatch.py:start_background": "thread_collect_dispatch",
    "dir_housekeeping.py:queue_scan": "thread_dir_housekeeping",
    "paste_watch.py:tick_in_background": "thread_paste_watch_worker",
    "post_onboard.py:start": "thread_post_onboard_start",
    "post_onboard.py:enqueue": "post_onboard_queue",
    "scan_service.py:start_scan": "thread_scan_start",
    "scan_service.py:start_scheduler": "thread_scheduler",
    "scan_service.py:_quiet_catchup_tick": "thread_catchup",
    "transfer_runner.py:_capped_get": "thread_transfer_reader",
    "transfer_service.py:spawn": "thread_transfer_spawn",
    "transfer_service.py:start_resumer": "transfer_resumer",
}

#: 所有叫 tick／*_tick 的函式（檔案:函式）→ ENTRIES 代號。
TICK_SITES: dict[str, str] = {
    "daily_digest.py:tick": "daily_digest_tick",
    "capacity_archive.py:tick": "capacity_archive_tick",
    "dispatch_weekly.py:tick": "dispatch_weekly_tick",
    "eos_notify.py:tick": "eos_notify_tick",
    "scan_service.py:_vcenter_autoimport_tick": "vcenter_autoimport_tick",
    "scan_service.py:_integrations_tick": "vrops_import_tick",      # 同時涵蓋 whatsup（見 SCHEDULER_CALLS）
    "scan_service.py:_capacity_archive_tick": "capacity_archive_tick",
    "storage_health.py:tick": "storage_health_tick",
    "relay_import.py:tick": "relay_import_tick",
    "dir_housekeeping.py:night_tick": "hk_night_tick",
    "scan_service.py:_hk_night_tick": "hk_night_tick",
    "scan_service.py:_relay_import_tick": "relay_import_tick",
    "scan_service.py:_storage_health_tick": "storage_health_tick",
    "scan_service.py:_ci_graph_tick": "ci_graph_tick",
    "scan_service.py:_track_snapshot_tick": "track_snapshot_tick",
    "v2_today.py:tick": "track_snapshot_tick",
    "scan_service.py:_paste_watch_tick": "paste_watch_tick",
    "scan_service.py:_dispatch_weekly_tick": "dispatch_weekly_tick",
    "scan_service.py:_vuln_notify_tick": "vuln_notify_tick",
    "scan_service.py:_eos_notify_tick": "eos_notify_tick",
    "scan_service.py:_daily_digest_tick": "daily_digest_tick",
    "scan_service.py:_teams_notify_tick": "teams_notify_tick",
    "scan_service.py:_health_baseline_tick": "health_baseline_weekly",
    "scan_service.py:_quiet_catchup_tick": "scan_post_services",    # 窗外補跑被延後的掃描後處理（本身不連線，呼叫的各步驟各自守門）
    "teams_notify.py:tick": "teams_notify_tick",
    "wug_alert.py:tick": "wug_alert_tick",
    "scan_service.py:_wug_alert_tick": "wug_alert_tick",
    "wug_lastpoll.py:tick": "wug_lastpoll_tick",
    "wug_lastpoll.py:heartbeat_tick": "wug_lastpoll_tick",      # 同一個入口裡的心跳（只讀本機表、排 Teams 佇列）
    "scan_service.py:_wug_lastpoll_tick": "wug_lastpoll_tick",
    "aria_dailycheck.py:tick": "aria_dailycheck_tick",
    "scan_service.py:_aria_dailycheck_tick": "aria_dailycheck_tick",
    "scan_service.py:_daily_check_tick": "daily_check_tick",
    "scan_service.py:_notice_digest_tick": "notice_digest_tick",
    "notice_digest.py:tick": "notice_digest_tick",
    "daily_check.py:tick": "daily_check_tick",
    "vcenter_autoimport.py:tick": "vcenter_autoimport_tick",
    "vrops_import.py:tick": "vrops_import_tick",
    "vuln_notify.py:tick": "vuln_notify_tick",
    "whatsup_service.py:tick": "whatsup_poll_tick",
}

#: _scheduler_loop 裡呼叫的每一個函式 → ENTRIES 代號（A 類：scan_schedule 由 _run_due_scans 守門）。
SCHEDULER_CALLS: dict[str, str] = {
    "_run_due_scans": "scan_schedule",
    "_quiet_catchup_tick": "scan_post_services",
    "_vcenter_autoimport_tick": "vcenter_autoimport_tick",
    "_integrations_tick": "vrops_import_tick",
    "_capacity_archive_tick": "capacity_archive_tick",
    "_storage_health_tick": "storage_health_tick",
    "_hk_night_tick": "hk_night_tick",
    "_relay_import_tick": "relay_import_tick",
    "_ci_graph_tick": "ci_graph_tick",
    "_track_snapshot_tick": "track_snapshot_tick",
    "_health_baseline_tick": "health_baseline_weekly",
    "_vuln_notify_tick": "vuln_notify_tick",
    "_eos_notify_tick": "eos_notify_tick",
    "_daily_digest_tick": "daily_digest_tick",
    "_teams_notify_tick": "teams_notify_tick",
    "_wug_alert_tick": "wug_alert_tick",
    "_wug_lastpoll_tick": "wug_lastpoll_tick",
    "_aria_dailycheck_tick": "aria_dailycheck_tick",
    "_daily_check_tick": "daily_check_tick",
    "_notice_digest_tick": "notice_digest_tick",
    "_paste_watch_tick": "paste_watch_tick",
    "_dispatch_weekly_tick": "dispatch_weekly_tick",
}

#: api.py 的 startup 事件（@app.on_event）→ ENTRIES 代號。
STARTUP_SITES: dict[str, str] = {"api.py:_start_scheduler": "startup_housekeeping",
                                  "api.py:_cleanup_cia_previews": "startup_cia_preview_cleanup"}


# ============================================================ 純函式：時間判斷
def parse_hhmm(s) -> _dt.time | None:
    m = _HHMM.match(str(s or "").strip())
    return _dt.time(int(m.group(1)), int(m.group(2))) if m else None


def is_quiet_time(t: _dt.datetime, start: _dt.time, end: _dt.time, holidays=()) -> bool:
    """純函式：t 在不在 [start, end) 的平日窗內。左閉右開；週六日、休市日不在窗內。
    t 帶不帶時區都行（不帶就當伺服器本地＝Asia/Taipei 的牆上時間）。"""
    t = _naive(t)          # 單一 helper：aware 先轉台北（naive 視為台北牆上時間）；公開函式也一樣，不信任呼叫端
    if t.weekday() >= 5 or t.date().isoformat() in set(holidays or ()):
        return False
    return start <= t.time() < end


_clock = None        # 測試用：set_clock(fn) 換掉；None＝用 transfer_policy 的時鐘（Asia/Taipei）


def set_clock(fn) -> None:
    """測試用：注入時鐘（回 datetime）。傳 None 還原。"""
    global _clock
    _clock = fn


def now() -> _dt.datetime:
    return (_clock or tp.now)()


def _naive(n: _dt.datetime | None) -> _dt.datetime:
    """一律回『Asia/Taipei 牆上時間』（naive）。aware 的先轉到台北（固定 UTC+8、無 DST），所以 UTC 01:00 ＝ 台北 09:00。
    naive 的視為已經是台北牆上時間（只有測試注入會這樣給）；系統自己的呼叫一律不傳 now，由 now() 給 aware 的台北時間，
    所以伺服器 TZ 是 UTC 也不會算錯（不要再傳 datetime.now()）。"""
    n = n or now()
    if n.tzinfo is not None:
        n = n.astimezone(tp.TZ).replace(tzinfo=None)
    return n


def local_now() -> _dt.datetime:
    """台北牆上時間（naive）。全站『現在幾點』只有這一個來源。"""
    return _naive(None)


def today() -> str:
    return _naive(None).strftime("%Y-%m-%d")


#: 窗口結束後各類入口錯開啟動（分鐘），避免 15:00:00 一到掃描、vROps、WhatsUp、每週收集、補跑同一分鐘全部啟動。
STAGGER = {"integrations": 1, "paste": 1, "dispatch": 2, "health": 3, "catchup": 4, "archive": 5, "storage": 6, "relay": 7}


def minutes_since_release(conn) -> int:
    """距離今天靜默窗結束過了幾分鐘。窗內回 -1；今天沒有窗（週末／休市／關閉）或已過很久回 9999（不需錯開）。"""
    try:
        cfg = get_config(conn)
        if cfg["broken"] or not cfg["enabled"]:
            return -1 if cfg["broken"] else 9999
        n = _naive(None)
        if n.weekday() >= 5 or n.date().isoformat() in cfg["holidays"]:
            return 9999
        if cfg["start"] <= n.time() < cfg["end"]:
            return -1
        if n.time() < cfg["end"]:
            return 9999
        return int((n - _dt.datetime.combine(n.date(), cfg["end"])).total_seconds() // 60)
    except Exception:  # noqa: BLE001 - fail-closed
        return -1


def cli_gate(entry: str, confirm: bool = False, label: str = "") -> bool:
    """命令列入口（__main__）用：窗內預設拒絕；人為手動要帶 --confirm-in-window 才放行並寫稽核。回 True＝可以繼續。"""
    try:
        from db import get_connection
        c = get_connection()
    except Exception:  # noqa: BLE001 - 連設定庫都開不了：fail-closed
        print("盤中靜默：無法確認靜默窗設定，不執行。")
        return False
    try:
        if not in_quiet_window(c):
            return True
        if not confirm:
            print(f"現在是盤中靜默時段（平日 {fmt_window(c)}），{label or entry} 不自動執行。"
                  f"人為手動請加 --confirm-in-window。")
            defer_if_quiet(c, entry)
            return False
        try:
            _ensure_log(c)
            _log_event(c, entry, "manual_confirmed", "cli", f"命令列窗內人為確認執行 {label}", commit=True)
        except Exception:  # noqa: BLE001 - 稽核寫不進去：不執行
            print("稽核紀錄寫入失敗，為安全起見未執行。")
            return False
        return True
    finally:
        c.close()


# ============================================================ 設定
def _raw(conn, key: str):
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else row[0]


def _holidays(conn) -> set[str]:
    """沿用容量分析的休市日（[{date: 'YYYY-MM-DD', ...}] 的 JSON）。嚴格驗證：每一筆都要是完整 YYYY-MM-DD 且可解析；
    任何一筆不合就整份視為損毀（呼叫端 get_config 會標 broken＝視同在窗內、狀態條告警），不再『猜』。"""
    raw = _raw(conn, KEY_HOLIDAYS)
    if not raw:
        return set()
    out = set()
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("休市日不是清單")
    for h in data:
        d = h.get("date") if isinstance(h, dict) else None
        if not isinstance(d, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise ValueError(f"休市日格式不是完整 YYYY-MM-DD：{d!r}")
        _dt.date.fromisoformat(d)
        out.add(d)
    return out


def get_config(conn) -> dict:
    """讀設定。broken=True 代表設定壞了或讀不出來（呼叫端必須視同在窗內）。"""
    cfg = {"enabled": True, "start": tp.MARKET_OPEN, "end": tp.MARKET_CLOSE, "holidays": set(), "broken": False, "problem": ""}
    try:
        en = _raw(conn, KEY_ENABLED)
        if en is None or en == "1":
            cfg["enabled"] = True
        elif en == "0":
            cfg["enabled"] = False
        else:
            cfg["broken"], cfg["problem"] = True, f"{KEY_ENABLED} 不是 0/1"
        s, e = _raw(conn, KEY_START), _raw(conn, KEY_END)
        if s is not None:
            ps = parse_hhmm(s)
            if ps is None:
                cfg["broken"], cfg["problem"] = True, f"{KEY_START} 格式不是 HH:MM"
            else:
                cfg["start"] = ps
        if e is not None:
            pe = parse_hhmm(e)
            if pe is None:
                cfg["broken"], cfg["problem"] = True, f"{KEY_END} 格式不是 HH:MM"
            else:
                cfg["end"] = pe
        if not cfg["broken"] and not cfg["start"] < cfg["end"]:
            cfg["broken"], cfg["problem"] = True, "起訖時間不合理（起必須早於迄）"
        try:
            cfg["holidays"] = _holidays(conn)
        except Exception as exc:  # noqa: BLE001 - 休市日損毀：fail-closed 並告警
            cfg["broken"], cfg["problem"] = True, f"休市日設定損毀（{exc}）"
    except Exception as exc:  # noqa: BLE001 - 讀不出設定＝fail-closed（視同在窗內）
        cfg["broken"], cfg["problem"] = True, f"讀取設定失敗：{type(exc).__name__}"
    if cfg["broken"]:
        _alert_broken(cfg["problem"])
    return cfg


_ALERT_AT = [0.0]
LOG = logging.getLogger("quiet_window")
#: 偵測到『自動排程可能因設定／資料庫異常而停擺』的時間點（monotonic）。狀態條顯示警告（一小時內）。
_HEALTH: dict[str, float] = {}
HEALTH_WINDOW_S = 3600


def health_warnings() -> list[str]:
    now_ = _time.monotonic()
    msgs = {"log_write_failed": "延後紀錄寫不進資料庫", "pending_write_failed": "補跑待辦寫不進資料庫", "pending_read_failed": "補跑待辦讀不出來",
            "catchup_worker_error": "背景補跑發生未預期的錯誤", "config_unreadable": "讀不到靜默窗設定（資料庫異常），自動連線一律暫停"}
    return [m for k, m in msgs.items() if k in _HEALTH and now_ - _HEALTH[k] < HEALTH_WINDOW_S]


def _alert_broken(problem: str) -> None:
    """設定異常寫系統日誌（journal 看得到）；一分鐘最多一次，免得排程每個 tick 洗版。"""
    t = _time.monotonic()
    if t - _ALERT_AT[0] > 60:
        _ALERT_AT[0] = t
        LOG.error("盤中靜默窗設定異常，自動排程全部暫停（fail-closed）：%s", problem)


def in_quiet_window(conn, now: _dt.datetime | None = None) -> bool:
    """現在是不是盤中靜默窗。now 可注入（測試）；沒給就用 transfer_policy 的時鐘。
    設定壞掉／讀不出來／任何例外＝True（fail-closed）。管理員關閉靜默窗＝False（由設定 API 稽核＋畫面醒目標示）。"""
    try:
        cfg = get_config(conn)
        if cfg["broken"]:
            return True
        if not cfg["enabled"]:
            return False
        return is_quiet_time(_naive(now), cfg["start"], cfg["end"], cfg["holidays"])
    except Exception:  # noqa: BLE001 - fail-closed
        return True


def is_market_hours(conn, at: _dt.datetime | None = None) -> bool:
    """是不是開盤時段（用靜默窗的起訖與休市日，不管靜默窗開關）。只給訊息標示用，不拿來擋任何動作。
    at 一樣經 _naive 轉台北牆上時間，例外照常丟出，由呼叫端決定怎麼標示。"""
    cfg = get_config(conn)
    return is_quiet_time(_naive(at), cfg["start"], cfg["end"], cfg["holidays"])


# ============================================================ 紀錄（只在「延後／人工確認／設定變更」時寫；GET 不寫）
def _ensure_log(conn) -> None:
    conn.execute(LOG_TABLE_SQL)
    conn.execute(LOG_INDEX_SQL)


def _stamp(now) -> str:
    return _naive(now).strftime("%Y-%m-%d %H:%M:%S")


def _log_deferred(conn, entry: str, now) -> None:
    _ensure_log(conn)
    st = _stamp(now)
    day = st[:10]
    cur = conn.execute("UPDATE quiet_window_log SET count = count + 1, last_at = ? "
                       "WHERE day = ? AND entry = ? AND kind = 'deferred'", (st, day, entry))
    if cur.rowcount == 0:
        conn.execute("INSERT INTO quiet_window_log (day, entry, kind, first_at, last_at, count, detail) "
                     "VALUES (?,?,?,?,?,1,?)", (day, entry, "deferred", st, st, "盤中靜默，延後（窗外第一個 tick 補跑）"))
    conn.commit()


def _log_event(conn, entry: str, kind: str, actor: str | None, detail: str, now=None, commit: bool = True) -> None:
    _ensure_log(conn)
    st = _stamp(now)
    conn.execute("INSERT INTO quiet_window_log (day, entry, kind, first_at, last_at, count, actor, detail) "
                 "VALUES (?,?,?,?,?,1,?,?)", (st[:10], entry, kind, st, st, actor, detail))
    if commit:
        conn.commit()


def was_deferred_on(conn, entry: str, day: str) -> bool:
    """這個入口在這天（YYYY-MM-DD）有沒有因盤中靜默被延後過（補跑窗判斷用；只讀）。"""
    try:
        return conn.execute("SELECT 1 FROM quiet_window_log WHERE day = ? AND entry = ? AND kind = 'deferred'",
                            (day, entry)).fetchone() is not None
    except Exception:  # noqa: BLE001 - 表還不存在＝沒延後過
        return False


# ============================================================ 自動路徑：守門（沒有任何繞過參數）
def defer_if_quiet(conn, entry: str, now: _dt.datetime | None = None) -> bool:
    """自動入口的第一行。True＝在盤中靜默窗內，呼叫端必須立刻返回、不呼叫任何對外連線函式。
    entry 必須是 ENTRIES 裡 A（或 H）類的代號，打錯字直接 KeyError（寧可炸測試也不要悄悄失效）。
    ⚠️ 刻意沒有 confirm／force 之類的參數：自動路徑不可被任何參數繞過。"""
    meta = ENTRIES[entry]
    if meta["cls"] not in ("A", "H"):
        raise ValueError(f"{entry} 不是需要守門的入口（分類 {meta['cls']}）")
    if not in_quiet_window(conn, now):
        return False
    try:
        _log_deferred(conn, entry, now)
    except Exception as exc:  # noqa: BLE001 - 紀錄寫不進去也要照樣延後（fail-closed），不可因此放行；但要讓人看得到
        LOG.error("盤中靜默：延後紀錄寫入失敗（%s）；自動排程仍照常延後，但可能因資料庫異常而停擺", exc)
        _HEALTH["log_write_failed"] = _time.monotonic()
    return True


# ============================================================ 票證（人工路徑簽發、底層函式驗證；排程路徑拿不到）
TICKET_TTL = 60
_TICKETS: dict[str, dict] = {}
_TLOCK = threading.Lock()


class Ticket(str):
    """票證：字串內容是隨機權杖，另帶簽發時的使用者。只能原物件往下傳；轉成純字串再用會因 actor 對不上而失效。"""
    actor: str | None = None


def issue_ticket(entry: str, actor: str | None) -> "Ticket":
    """只有人工 API 在窗內收到 confirm_in_window=true 且稽核成功後才會呼叫。一次性、約 60 秒、綁入口與使用者。"""
    tok = Ticket(secrets.token_urlsafe(24))
    tok.actor = actor
    with _TLOCK:
        for k in [k for k, v in _TICKETS.items() if v["exp"] < _time.monotonic()]:
            _TICKETS.pop(k, None)
        _TICKETS[tok] = {"entry": entry, "actor": actor, "exp": _time.monotonic() + TICKET_TTL}
    return tok


def consume_ticket(token, entry: str, actor="__from_ticket__") -> bool:
    """一次性。同時核對入口與簽發時的使用者：actor 預設取自票證物件本身；明確傳 actor 則以傳入者為準（跨使用者不得使用）。"""
    if not token or not isinstance(token, str):
        return False
    who = getattr(token, "actor", None) if actor == "__from_ticket__" else actor
    with _TLOCK:
        t = _TICKETS.pop(str(token), None)
    return bool(t and t["entry"] == entry and t["exp"] >= _time.monotonic() and t["actor"] == who)


# ============================================================ Run（一次任務的授權狀態）與 checkpoint（每次新外連前重查）
class Run:
    """human＝這個任務是人在窗內以有效票證啟動的（可跨過 08:30）。否則進窗後 ok() 就是 False。paused＝曾因進窗而停止派送。"""

    def __init__(self, entry: str, human: bool = False, db_path: str | None = None):
        self.entry, self.human, self.paused = entry, human, False
        self.db_path = db_path          # 這個任務讀設定／寫待補跑用的資料庫檔（conn-less 檢查用同一個庫，不碰別的庫）

    def ok(self, conn) -> bool:
        return self.human or not in_quiet_window(conn)


_local = threading.local()


def _path_of(conn) -> str | None:
    try:
        r = conn.execute("PRAGMA database_list").fetchone()
        return (r[2] or None) if r else None
    except Exception:  # noqa: BLE001
        return None


def current() -> Run | None:
    return getattr(_local, "run", None)


@contextlib.contextmanager
def use(run: Run | None):
    prev = getattr(_local, "run", None)
    _local.run = run
    try:
        yield run
    finally:
        _local.run = prev


def begin(conn, entry: str, ticket: str | None = None, actor="__from_ticket__") -> Run | None:
    """任務開頭：窗外 → 非人為 Run（之後每次新外連前 checkpoint 會重查）；窗內有有效票證 → 人為 Run；窗內沒票證 → None（拒絕＋記延後）。
    已在別的 Run 底下（巢狀）就沿用外層的，不重新授權。"""
    cur = current()
    if cur is not None:
        return cur
    if ticket is not None and consume_ticket(ticket, entry, actor):
        return Run(entry, human=True, db_path=_path_of(conn))
    if defer_if_quiet(conn, entry):
        return None
    return Run(entry, human=False, db_path=_path_of(conn))


def checkpoint(conn, entry: str, require_context: bool = True) -> bool:
    """『下一次外連（下一台／下一頁／下一步）之前』呼叫。True＝可以連；False＝呼叫端立刻停止派送新連線（已送出的單次請求可完成）。
    在窗內被擋時，這個任務標記『盤中暫停，待補跑』（pending 表），窗外由補跑 tick 重跑。"""
    run = current()
    if run is None and not require_context:
        return True
    if run is not None and run.ok(conn):
        return True
    if run is None and not in_quiet_window(conn):
        return True
    if not defer_if_quiet(conn, entry):       # 不在窗內（run 非人為但窗已過）→ 放行
        return True
    if run is not None:
        run.paused = True
        pending_mark(conn, entry, "盤中暫停（進窗時尚有未處理的項目）")
    return False


def guarded(entry: str):
    """裝飾器：長任務入口。吃掉 quiet_ticket= 參數；窗內沒有有效票證也不在 Run 底下 → 拒絕（回 refused 字典），
    不信任任何 manual／trigger／auto 旗標。通過後在 Run 底下執行，函式內各迴圈用 checkpoint 重查。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            ticket = k.pop("quiet_ticket", None)
            conn = next((x for x in (*a, *k.values()) if hasattr(x, "execute")), None)
            own = None
            if conn is None:                     # 例如 scheduled_cycle(conn=None) 之後才自己開連線：守門用自己的短連線
                from db import get_connection
                own = conn = get_connection()
            try:
                run = begin(conn, entry, ticket)
            finally:
                if own is not None:
                    own.close()
            if run is None:
                return {"refused": True, "quiet_window": True, "reason": "盤中靜默，未執行（人為執行請在窗內確認）"}
            with use(run):
                return fn(*a, **k)
        return wrapper
    return deco


def may_connect(entry: str, run: Run | None = None, mark_pending: bool = True) -> bool:
    """沒有 conn 的『下一次外連之前』檢查（底層迴圈、執行緒池工人用）；可明確傳 run（池裡的執行緒沒有 thread-local）。
    人為 Run 放行；否則窗內 → False，並把該 Run 標 paused（mark_pending 時寫 pending）。讀不到設定＝擋（fail-closed）。"""
    run = run or current()
    if run is None or run.human:
        return True          # 沒有任務授權脈絡（人為診斷、測試直接呼叫）：不在這裡擋，由路由 dependency／任務入口負責
    try:
        from pathlib import Path

        from db import get_connection
        c = get_connection(Path(run.db_path) if run.db_path else None)
    except Exception as exc:  # noqa: BLE001 - fail-closed 擋下，但停止原因要看得到（日誌＋狀態條）
        LOG.error("盤中靜默：開不了設定資料庫，%s 一律視同窗內而暫停（%s）", run.entry, exc)
        _HEALTH["config_unreadable"] = _time.monotonic()
        run.paused = True
        return False
    try:
        if not in_quiet_window(c):
            return True
        if run is not None:
            run.paused = True
            if mark_pending:
                pending_mark(c, run.entry, "盤中暫停（進窗時尚有未處理的項目）")
        return False
    finally:
        c.close()


class QuietWindowBlocked(Exception):
    """底層外連被靜默窗擋下（呼叫端把它當成連線失敗即可；任務已標待補跑）。"""


def network_gate() -> None:
    """底層外連函式（真正送出請求之前）的最後一道：沒有人為授權的 Run 而現在在窗內 → 丟 QuietWindowBlocked，並把該任務標待補跑。"""
    if current() is None:
        return           # 沒有任務授權脈絡（人為診斷端點的連線測試等）：不在最底層擋，窗內確認已由路由 dependency 做過
    if not may_connect(current().entry):
        raise QuietWindowBlocked("盤中靜默：不派送新連線，已標待補跑")


def capture(entry: str, ticket: str | None = None) -> Run:
    """開背景執行緒之前呼叫：把目前的授權狀態帶進執行緒（沿用外層 Run、或消耗票證成人為 Run、否則非人為）。執行緒內 `with use(run)`。"""
    cur = current()
    if cur is not None:
        return cur
    return Run(entry, human=bool(ticket and consume_ticket(ticket, entry)))


# ============================================================ pending（補跑狀態；獨立於 quiet_window_log）
PENDING_SQL = ("CREATE TABLE IF NOT EXISTS quiet_window_pending (entry TEXT PRIMARY KEY, state TEXT NOT NULL, "
               "attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL, detail TEXT)")
MAX_ATTEMPTS = 3
BACKOFF_MIN = (5, 15, 45)
STALE_RUNNING_MIN = 30
_MEM_PENDING: dict[str, dict] = {}      # DB 寫不進去時的後備：{entry: {"detail":…, "claimed": bool}}；成功完成才移除


def _p_now() -> str:
    return _stamp(None)


def pending_mark(conn, entry: str, detail: str = "") -> None:
    """標記『待補跑』。新的合法延後事件會重設 attempts／next_at（不會因舊失敗永久卡死）；running 中的不打斷。
    寫入失敗不吞：記系統日誌、並保留在記憶體後備，補跑 tick 仍會處理。"""
    try:
        conn.execute(PENDING_SQL)
        conn.execute("INSERT INTO quiet_window_pending (entry, state, attempts, next_at, updated_at, detail) VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(entry) DO UPDATE SET state = CASE WHEN state = 'running' THEN state ELSE 'pending' END, "
                     "attempts = CASE WHEN state = 'running' THEN attempts ELSE 0 END, "
                     "next_at = CASE WHEN state = 'running' THEN next_at ELSE '' END, "
                     "updated_at = excluded.updated_at, detail = excluded.detail",
                     (entry, "pending", 0, "", _p_now(), detail))
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - 不可靜默：告警＋記憶體後備
        LOG.error("盤中靜默：無法寫入補跑待辦 %s（%s）；改以記憶體保留，請檢查資料庫", entry, exc)
        _HEALTH["pending_write_failed"] = _time.monotonic()
        _MEM_PENDING.setdefault(entry, {"detail": detail, "claimed": False})


def pending_claim(conn) -> list[str]:
    """取走『到期、可重試』的待辦並原子標成 running。超過重試次數的不取（狀態停在 failed）。記憶體後備的先標 claimed，成功完成才移除。"""
    out = []
    try:
        conn.execute(PENDING_SQL)
        now_s = _p_now()
        stale = _stamp(_naive(None) - _dt.timedelta(minutes=STALE_RUNNING_MIN))
        conn.execute("UPDATE quiet_window_pending SET state = 'pending' WHERE state = 'running' AND updated_at < ?", (stale,))
        for (entry,) in conn.execute("SELECT entry FROM quiet_window_pending WHERE state IN ('pending','failed') AND attempts < ? "
                                     "AND (next_at = '' OR next_at <= ?)", (MAX_ATTEMPTS, now_s)).fetchall():
            cur = conn.execute("UPDATE quiet_window_pending SET state = 'running', updated_at = ? "
                               "WHERE entry = ? AND state IN ('pending','failed')", (now_s, entry))
            if cur.rowcount == 1:
                out.append(entry)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        LOG.error("盤中靜默：讀取補跑待辦失敗（%s）", exc)
        _HEALTH["pending_read_failed"] = _time.monotonic()
    for e, v in _MEM_PENDING.items():
        if not v["claimed"]:
            v["claimed"] = True
            out.append(e)
    return list(dict.fromkeys(out))


def pending_finish(conn, entry: str, ok: bool, paused: bool = False, detail: str = "") -> None:
    """補跑結束：成功才標 done；又被擋回窗內（paused）回 pending 不算失敗；失敗 attempts+1、遞增間隔，滿 3 次停在 failed。
    DB 原本沒有該列（只在記憶體後備）就補寫（upsert）。寫入失敗不吞：告警，記憶體後備還原成可再領。"""
    try:
        conn.execute(PENDING_SQL)
        if ok and not paused:
            conn.execute("INSERT INTO quiet_window_pending (entry, state, attempts, next_at, updated_at, detail) VALUES (?,?,?,?,?,?) "
                         "ON CONFLICT(entry) DO UPDATE SET state='done', attempts=0, next_at='', updated_at=excluded.updated_at, detail=excluded.detail",
                         (entry, "done", 0, "", _p_now(), detail))
            _MEM_PENDING.pop(entry, None)
        elif paused:
            conn.execute("INSERT INTO quiet_window_pending (entry, state, attempts, next_at, updated_at, detail) VALUES (?,?,?,?,?,?) "
                         "ON CONFLICT(entry) DO UPDATE SET state='pending', updated_at=excluded.updated_at",
                         (entry, "pending", 0, "", _p_now(), detail))
            if entry in _MEM_PENDING:
                _MEM_PENDING[entry]["claimed"] = False
        else:
            row = conn.execute("SELECT attempts FROM quiet_window_pending WHERE entry = ?", (entry,)).fetchone()
            n = (row[0] if row else 0) + 1
            nxt = "" if n >= MAX_ATTEMPTS else _stamp(_naive(None) + _dt.timedelta(minutes=BACKOFF_MIN[min(n - 1, len(BACKOFF_MIN) - 1)]))
            conn.execute("INSERT INTO quiet_window_pending (entry, state, attempts, next_at, updated_at, detail) VALUES (?,?,?,?,?,?) "
                         "ON CONFLICT(entry) DO UPDATE SET state='failed', attempts=excluded.attempts, next_at=excluded.next_at, "
                         "updated_at=excluded.updated_at, detail=excluded.detail",
                         (entry, "failed", n, nxt, _p_now(), detail[:300]))
            _MEM_PENDING.pop(entry, None)               # 已轉成 DB 的 failed 列（有重試計數）
            if n >= MAX_ATTEMPTS:
                LOG.error("盤中靜默：%s 補跑連續失敗 %d 次，已停止自動重試，請人工處理：%s", entry, n, detail)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - 標記失敗不吞：保持 running→過期後自動回 pending，並告警
        LOG.error("盤中靜默：無法更新 %s 補跑狀態（%s）；該步會在 %d 分鐘後被重新視為待補跑", entry, exc, STALE_RUNNING_MIN)
        _HEALTH["pending_write_failed"] = _time.monotonic()
        if entry in _MEM_PENDING:
            _MEM_PENDING[entry]["claimed"] = False


def pending_reset(conn, entry: str) -> bool:
    """admin 手動重試／重置：把 failed／pending 重設為可立即補跑（attempts=0）。回有沒有這筆。"""
    cur = conn.execute("UPDATE quiet_window_pending SET state='pending', attempts=0, next_at='', updated_at=? WHERE entry=? AND state != 'running'",
                       (_p_now(), entry))
    conn.commit()
    return cur.rowcount == 1


def pending_is(conn, entry: str) -> bool:
    if entry in _MEM_PENDING:
        return True
    try:
        r = conn.execute("SELECT state, attempts FROM quiet_window_pending WHERE entry = ?", (entry,)).fetchone()
        return bool(r and r[0] in ("pending", "failed", "running") and r[1] < MAX_ATTEMPTS)
    except Exception:  # noqa: BLE001 - 表不存在＝沒有待辦
        return False


def pending_list(conn) -> list[dict]:
    try:
        return [dict(zip(("entry", "state", "attempts", "next_at", "updated_at", "detail"), r)) for r in conn.execute(
            "SELECT entry, state, attempts, next_at, updated_at, detail FROM quiet_window_pending WHERE state != 'done' ORDER BY updated_at")]
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []                                  # 從沒有補跑待辦＝表還沒建，不是異常
        LOG.error("盤中靜默：讀取補跑待辦失敗（%s）", exc)
        _HEALTH["pending_read_failed"] = _time.monotonic()
        return []
    except Exception as exc:  # noqa: BLE001
        LOG.error("盤中靜默：讀取補跑待辦失敗（%s）", exc)
        _HEALTH["pending_read_failed"] = _time.monotonic()
        return []


# ============================================================ 人工路徑：窗內要二次確認＋稽核
def require_manual_confirm(conn, entry: str, confirm: bool, actor: str | None = None, ip: str | None = None,
                           now: _dt.datetime | None = None) -> str | None:
    """人工 API 的第一行。不在窗內：回 None（不需要票證）。在窗內：沒帶 confirm → HTTP 409；
    帶了 → quiet_window_log 與操作紀錄在『同一個交易』提交（失敗整筆回滾，不留假的 manual_confirmed），成功後簽發一次性票證回傳。
    呼叫端必須把票證傳給底層函式（begin／guarded 的 quiet_ticket=）；底層在窗內沒有有效票證一律拒絕。"""
    if ENTRIES[entry]["cls"] != "A":
        raise ValueError(f"{entry} 不是 A 類入口")
    if not in_quiet_window(conn, now):
        return None
    if not confirm:
        from fastapi import HTTPException
        raise HTTPException(409, {
            "code": CONFIRM_CODE, "entry": entry,
            "message": f"現在是盤中靜默時段（平日 {fmt_window(conn)}），系統不會自動掃描或收集。"
                       f"確認要人為執行「{ENTRIES[entry]['name']}」嗎？會對線上主機或平台送出連線。"})
    try:
        _ensure_log(conn)
        _log_event(conn, entry, "manual_confirmed", actor, f"窗內人為確認執行 · 來源 {ip or '-'}", now, commit=False)
        import activity
        activity.log(conn, username=actor, ip=ip, method="CONFIRM", path=f"quiet-window/{entry}", status=200,
                     action="quiet_window_manual", commit=False)
        conn.commit()
    except Exception:  # noqa: BLE001 - 稽核寫不進去：整筆回滾、不簽票證、不執行
        with contextlib.suppress(Exception):
            conn.rollback()
        from fastapi import HTTPException
        raise HTTPException(500, "盤中人為執行的稽核紀錄寫入失敗，為安全起見未執行；請稍後再試或聯絡管理員") from None
    return issue_ticket(entry, actor)


def confirm_from_request(conn, entry: str, confirm: bool, username: str | None, request=None) -> str | None:
    """路由用的薄包裝：從 request 取來源 IP，轉給 require_manual_confirm。"""
    ip = None
    if request is not None:
        try:
            import activity
            ip = activity.client_ip(request)
        except Exception:  # noqa: BLE001 - 取不到 IP 不影響確認流程
            ip = None
    return require_manual_confirm(conn, entry, confirm, username, ip)


def fmt_window(conn) -> str:
    cfg = get_config(conn)
    return f"{cfg['start'].strftime('%H:%M')}–{cfg['end'].strftime('%H:%M')}"


# ============================================================ 設定寫入（admin；由路由呼叫）
def save_config(conn, enabled: bool, start: str, end: str, actor: str | None, ip: str | None = None) -> dict:
    ps, pe = parse_hhmm(start), parse_hhmm(end)
    if ps is None or pe is None:
        raise ValueError("時間格式要是 HH:MM（例：08:30）")
    if not ps < pe:
        raise ValueError("起始時間必須早於結束時間")
    before = get_config(conn)
    for k, v in ((KEY_ENABLED, "1" if enabled else "0"), (KEY_START, ps.strftime("%H:%M")), (KEY_END, pe.strftime("%H:%M"))):
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, v))
    detail = (f"靜默窗設定：{'啟用' if enabled else '【關閉】'} {ps.strftime('%H:%M')}–{pe.strftime('%H:%M')}"
              f"（原 {'啟用' if before['enabled'] else '關閉'} {before['start'].strftime('%H:%M')}–{before['end'].strftime('%H:%M')}）")
    _log_event(conn, "settings", "setting_changed", actor, detail)
    try:
        import activity
        activity.log(conn, username=actor, ip=ip, method="PUT", path="/api/quiet-window", status=200,
                     action="quiet_window_setting" if enabled else "quiet_window_DISABLED")
    except Exception:  # noqa: BLE001 - 稽核另有 quiet_window_log 一份
        pass
    return status(conn)


def export_config(conn) -> dict:
    """唯讀：匯出目前靜默窗設定的檔案格式，給外部掃描機（scan_segments.py --quiet-config）同步。設定異常時 broken=true（外部腳本視同窗內）。"""
    cfg = get_config(conn)
    return {"format": "webit3-quiet-window", "version": 1, "tz": "Asia/Taipei", "weekdays_only": True,
            "enabled": cfg["enabled"], "broken": cfg["broken"],
            "start": cfg["start"].strftime("%H:%M"), "end": cfg["end"].strftime("%H:%M"),
            "holidays": sorted(cfg["holidays"]), "generated_at": _stamp(None)}


# ============================================================ 狀態（GET；只讀，不寫庫）
def status(conn, now: _dt.datetime | None = None, recent: int = 20) -> dict:
    cfg = get_config(conn)
    quiet = in_quiet_window(conn, now)
    end_txt = cfg["end"].strftime("%H:%M")
    if cfg["broken"]:
        msg = f"設定異常，自動排程全部暫停（{cfg['problem']}）：為安全起見視同盤中靜默；請管理員修正設定"
    elif not cfg["enabled"]:
        msg = "盤中靜默窗已被管理員關閉：開盤時間系統仍會自動掃描與收集，可能影響線上服務"
    elif quiet:
        msg = f"盤中靜默中：自動掃描與收集暫停至 {end_txt}（人為手動仍可執行，需再確認）"
    else:
        msg = "目前不在靜默時段"
    rows: list[dict] = []
    try:
        rows = [dict(zip(("day", "entry", "kind", "first_at", "last_at", "count", "actor", "detail"), r)) for r in conn.execute(
            "SELECT day, entry, kind, first_at, last_at, count, actor, detail FROM quiet_window_log ORDER BY id DESC LIMIT ?", (recent,))]
    except Exception:  # noqa: BLE001 - 表還沒建（從沒延後過）
        rows = []
    for r in rows:
        r["entry_name"] = (ENTRIES.get(r["entry"]) or {}).get("name", r["entry"])
    warns = health_warnings()
    return {"warning": ("自動排程可能因設定／資料庫異常而停擺：" + "、".join(warns)) if warns else "",
            "pending": pending_list(conn), "in_quiet_window": quiet, "enabled": cfg["enabled"], "broken": cfg["broken"], "problem": cfg["problem"],
            "start": cfg["start"].strftime("%H:%M"), "end": end_txt, "holidays": sorted(cfg["holidays"]),
            "message": msg, "recent": rows}


#: backend 內有 `if __name__ == "__main__"` 的檔案 → 登記代號（命令列入口；A 類另在 main 內呼叫 cli_gate）。
MAIN_SITES: dict[str, str] = {
    "auto_onboard.py": "scan_post_auto_onboard", "run_real_scan.py": "scan_schedule",
    "scanner.py": "cmd_local_b", "backup.py": "cmd_local_b", "clean_data.py": "cmd_local_b", "cleanup.py": "cmd_local_b", "db.py": "cmd_local_b",
    "dyn_shell.py": "cmd_local_b", "dynassets_import.py": "cmd_local_b", "excel_import.py": "cmd_local_b", "lifecycle.py": "cmd_local_b",
    "machine_identity.py": "cmd_local_b", "rvtools_import.py": "cmd_local_b", "seed_admin.py": "cmd_local_b", "seed_demo.py": "cmd_local_b",
    "vcenter_autoimport.py": "vcenter_autoimport_tick", "verify_numbers.py": "cmd_local_b", "rack_name_migrate.py": "cmd_local_b",
}

#: 非本 app 的外部排程（deploy.sh／mmap.sh 寫出的 systemd timer、cron）。key＝檔案，value＝(要在檔案裡出現幾次的樣式, 次數, 登記代號)。
EXTERNAL_SCHEDULES: list[tuple[str, str, int, str]] = [
    ("deploy.sh", r"OnCalendar=", 2, "backup_cleanup_timers"),
    ("mmap.sh", r"CRON_FILE=/etc/cron\.d/", 1, "external_scan_segments"),
]
