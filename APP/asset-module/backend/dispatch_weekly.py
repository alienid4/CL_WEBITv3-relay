"""每週六晚上自動跑一次整片網段的「開始收集」（v1.468.6）。

使用者 2026-10-19 拍板（原話）：「每週一次在晚上自動跑（六晚上）」。
白天預設「只掃已登記的 IP」（快、只看登記的機器）；整片網段的新機器發現交給這個每週一次。

- 範圍＝使用者上一次手動跑「整片網段」用的那份清單（存在 app_settings，6 系統設定看得到、改得了）。
  什麼叫整片：沒勾「只掃已登記」、而且展開後至少 254 個位址（一個 /24）——單台、幾台的補收不算，
  不然收一台就把每週範圍蓋成一台。
- 跑法＝全部重收（full=True）：每週這一次就是要把空位址也重探一遍，才看得到新上線的機器。
- 批次間隔（SOC 節奏）照設定，一樣適用（使用者：「SOC 不申請」→ 節奏不拿掉、不調低）。
- 走既有排程器（scan_service._scheduler_loop 每分鐘一輪），不另開 cron。
- 只有這一個例行排程：使用者先前說過不要例行排程，這是他明講的唯一例外（decisions.json DISPATCH-WEEKLY1）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

WEEKDAY_LABEL = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")

DEFAULTS = {
    "dispatch_weekly_enabled": "1",
    "dispatch_weekly_weekday": "5",        # Python weekday：5＝週六
    "dispatch_weekly_time": "22:00",
    "dispatch_weekly_targets": "",
    "dispatch_weekly_last_at": "",
}
#: 手動跑到這麼多位址以上才算「整片網段」，才記成每週範圍
FULL_SCAN_MIN = 254
#: 到點後多久內還算「該跑」（服務剛好重啟、前一輪還在跑…）；超過就等下週，
#: 不要週六半夜開了開關、一分鐘後就冒出一輪沒人預期的整片重探
CATCH_UP_HOURS = 3
TRIGGER = "weekly"


def _get(conn, key: str) -> str:
    from db import get_setting

    return (get_setting(conn, key, DEFAULTS[key]) or "").strip()


def _hhmm(s: str) -> tuple[int, int]:
    hh, mm = s.split(":")
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("時間格式要是 HH:MM")
    return h, m


def settings(conn) -> dict:
    try:
        wd = min(6, max(0, int(_get(conn, "dispatch_weekly_weekday"))))
    except ValueError:
        wd = 5
    t = _get(conn, "dispatch_weekly_time") or "22:00"
    try:
        _hhmm(t)
    except ValueError:
        t = "22:00"
    return {"enabled": _get(conn, "dispatch_weekly_enabled") == "1", "weekday": wd, "time": t,
            "targets": _get(conn, "dispatch_weekly_targets"),
            "last_at": _get(conn, "dispatch_weekly_last_at") or None}


def save(conn, *, enabled=None, weekday=None, time=None, targets=None) -> dict:
    """存設定；格式不對丟 ValueError（呼叫端轉 400）。只動有給的欄位。"""
    from db import set_setting

    import collect_dispatch

    if weekday is not None and not 0 <= int(weekday) <= 6:
        raise ValueError("星期要是 0（週一）～6（週日）")
    if time is not None:
        _hhmm(time)
    if targets is not None and targets.strip():
        collect_dispatch.parse_targets(targets)         # 格式不對／超過上限就擋
    if enabled is not None:
        set_setting(conn, "dispatch_weekly_enabled", "1" if enabled else "0")
    if weekday is not None:
        set_setting(conn, "dispatch_weekly_weekday", str(int(weekday)))
    if time is not None:
        set_setting(conn, "dispatch_weekly_time", time)
    if targets is not None:
        set_setting(conn, "dispatch_weekly_targets", targets.strip())
    return status(conn)


def remember_full_targets(conn, targets_raw: str, count: int) -> bool:
    """手動跑整片網段時，把範圍記成每週範圍。回 True＝有記。

    只在「還沒有範圍」或「新範圍涵蓋舊範圍」時才記——全公司範圍設好之後，手動補跑其中一個
    /24 不可以把每週範圍悄悄縮成那一段（要縮就到 6 系統設定直接改）。
    """
    from db import set_setting

    import collect_dispatch

    if count < FULL_SCAN_MIN or not (targets_raw or "").strip():
        return False
    old = _get(conn, "dispatch_weekly_targets")
    if old:
        try:
            if not set(collect_dispatch.parse_targets(old)) <= set(collect_dispatch.parse_targets(targets_raw)):
                return False
        except ValueError:
            pass        # 舊範圍壞了：用新的取代
    set_setting(conn, "dispatch_weekly_targets", targets_raw.strip())
    return True


def next_run(conn, now: datetime | None = None) -> str | None:
    """下一次會跑的時間（關著或沒有範圍回 None）。"""
    s = settings(conn)
    if not s["enabled"] or not s["targets"]:
        return None
    now = now or datetime.now()
    h, m = _hhmm(s["time"])
    cand = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(
        days=(s["weekday"] - now.weekday()) % 7)
    last = s["last_at"]
    if cand < now or (last and last[:10] == cand.strftime("%Y-%m-%d")):
        cand += timedelta(days=7)
    return cand.strftime("%Y-%m-%d %H:%M")


def is_due(conn, now: datetime) -> bool:
    s = settings(conn)
    if not s["enabled"] or not s["targets"] or now.weekday() != s["weekday"]:
        return False
    h, m = _hhmm(s["time"])
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if not target <= now < target + timedelta(hours=CATCH_UP_HOURS):
        return False
    return not (s["last_at"] and s["last_at"][:10] == now.strftime("%Y-%m-%d"))


def last_run(conn) -> dict | None:
    try:
        r = conn.execute(
            "SELECT id, status, started_at, finished_at, target_count, error FROM collect_dispatch_run "
            "WHERE trigger = ? ORDER BY id DESC LIMIT 1", (TRIGGER,)).fetchone()
    except Exception:  # noqa: BLE001 - 舊庫：當作沒跑過
        return None
    if r is None:
        return None
    out = dict(r)
    agg = conn.execute(
        "SELECT SUM(alive) AS alive, SUM(status = 'collected') AS collected, COUNT(*) AS done "
        "FROM collect_dispatch_result WHERE run_id = ?", (r["id"],)).fetchone()
    out.update(alive=agg["alive"] or 0, collected=agg["collected"] or 0, done=agg["done"] or 0)
    return out


def status(conn) -> dict:
    s = settings(conn)
    import collect_dispatch

    try:
        n = len(collect_dispatch.parse_targets(s["targets"])) if s["targets"] else 0
    except ValueError:
        n = 0
    # 決策 WEEKLY-RANGE1（使用者 2026-10-20）：開著卻沒有範圍 → 畫面亮紅提醒就好，**不讓系統自己猜範圍**
    # （6 系統設定 → 掃描排程、2-1 開始收集各一處，讀的都是這個值）。
    return {**s, "weekday_label": WEEKDAY_LABEL[s["weekday"]], "target_count": n,
            "range_missing": bool(s["enabled"] and n == 0),
            "next_run": next_run(conn), "last_run": last_run(conn)}


def _run_in_progress(conn) -> bool:
    """有沒有一輪收集正在跑（背景或同步都算；6 小時前就開始的視為殘留，不擋）。"""
    import collect_dispatch

    if collect_dispatch.background_running():
        return True
    try:
        # 2026-10-20：以前是「6 小時內開始的 running 都擋」——服務重啟留下的孤兒列會擋掉每週那一輪。
        # 現在看心跳（collect_dispatch.run_liveness），心跳逾時的不擋。
        rows = conn.execute(
            "SELECT * FROM collect_dispatch_run WHERE status = 'running' "
            "AND started_at >= datetime('now','localtime','-6 hours')").fetchall()
        return any(collect_dispatch.run_liveness(conn, dict(r))["liveness"] == "live" for r in rows)
    except Exception:  # noqa: BLE001 - 舊庫：當作沒有
        return False


def tick(conn, now: datetime | None = None, starter=None) -> bool:
    """排程器每分鐘呼叫。到點就在背景開跑一次；已有一輪在跑就等下一分鐘。回 True＝這次有開跑。"""
    from db import set_setting

    import collect_dispatch

    now = now or datetime.now()
    if not is_due(conn, now) or _run_in_progress(conn):
        return False
    s = settings(conn)
    # 先記「今天跑過了」再開跑：開跑失敗（例如範圍格式壞掉）也不要每分鐘重試洗版
    set_setting(conn, "dispatch_weekly_last_at", now.strftime("%Y-%m-%d %H:%M:%S"))
    (starter or collect_dispatch.start_background)(
        None, s["targets"], triggered_by="(每週排程)", full=True, trigger=TRIGGER)
    return True
