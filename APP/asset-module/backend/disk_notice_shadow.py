"""硬碟通知「影子模式」（2.069.5）：只記錄「如果正式開通，今天會通知誰」，**完全不寄信、不送 Teams、不連任何主機**。

背景：使用者想要「硬碟使用率 ≥90% 自動通知負責人」，還沒拍板，正式機寄信也還沒通。NB＋GPT 拷問後的建議（ai_handoff NB留言 2026-11-20 C2）：
先影子模式一週，看「預計通知誰、幾封」合不合理，再決定要不要正式開。

判定（重用既有，不另寫第二套）：
  · 使用率分級：`disk_level.level`（≥90 ＝「高」；一位小數四捨五入，全站同一把尺）。
  · 資料：`relay_disk_util`（WhatsUp 每日中繼匯入）當天最高值，用 `disk_alert.day_peak`；假磁碟排除用 `disk_alert.exclude_list`／`excluded`，
    SWAP 用 `daily_check._is_swap`；同一台身分用 `disk_alert.ident_of`（machine_key）。
  · 負責人：資產 `hardware.owner`（CIA 負責人）→ `hardware.custodian`（保管者）→ 對不到就算 SP（系統管理者）；
    人名能不能查到信箱用 `eos_notify.effective_mail`（跟 EOS 通知同一套；只查本機資料）。
規則（rule_ver）：
  · cross90：今天最高值 ≥90%，而前一天（同台同碟）< 90% 或沒有前次資料 → 「跨過 90%」，一天一筆。
  · ge95：今天最高值 ≥95% → 即時類（正式開通時要即時通知 SP）。
  · full3d：最近 7 天每日最高值（至少 3 天、跨 2 天以上）線性外推，3 天內會滿（≥80% 才算，避免小碟雜訊）→ 即時類。
  同日同機同碟只留一筆（即時類蓋過一般跨線）。
預計通知對象：即時類一律 SP；一般跨線＝負責人（對不到 → SP）。
安全：這個模組**不呼叫任何送出函式**（寄信、Teams、SMTP、事件發布都沒有）。它會間接載入 eos_notify／disk_alert 取「負責人信箱／資料判定」，
這些模組有送出能力，所以硬保證不靠 import 位置，而靠測試最底層的 fail-fast（smtplib／socket／Teams 佇列寫入全部攔截，跑完整流程仍零新增）。
設定關閉時連表都不建、不寫。
盤中靜默窗：純本機計算（B 類），登記為 `disk_notice_shadow_tick`。
資料函式不自行 commit 的：save_enabled（路由與稽核同一交易）；run／tick 自己 commit。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone

import disk_level

KEY_ENABLED = "disk_notice_shadow_enabled"
KEY_LAST_RUN = "disk_notice_shadow_last_run"
RULE_VER = "shadow-1"
CROSS, GE95, FULL3D = "cross90", "ge95", "full3d"
REASON_LABEL = {CROSS: "跨過 90%", GE95: "≥95%（即時）", FULL3D: "預估 3 天內滿（即時）"}
SRC_OWNER, SRC_CUSTODIAN, SRC_SP = "CIA負責人", "保管者", "對不到→SP"
SP_NAME = "SP（系統管理者）"
CRITICAL_PCT = 95.0
FULL_DAYS = 3.0
FULL_FLOOR = 80.0
TREND_DAYS = 7
STALE_DAYS = 2
RUN_AFTER_HOUR = 7                  # 台北時間 07:00 之後每天跑一次（WhatsUp 中繼約 05:00 匯入）
KEEP_DAYS = 90
TPE = timezone(timedelta(hours=8))
log = logging.getLogger("disk_notice_shadow")

SQL = [
    """CREATE TABLE IF NOT EXISTS disk_notice_shadow (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL,                  -- 資料日（relay_disk_util.day）
        machine TEXT NOT NULL,              -- 同一台身分：'mk:<machine_key>' 或 'ip:<IP>'
        ip TEXT NOT NULL, host TEXT, disk TEXT NOT NULL,
        pct REAL NOT NULL, prev_pct REAL,
        reason TEXT NOT NULL,               -- cross90／ge95／full3d
        immediate INTEGER NOT NULL DEFAULT 0,
        target TEXT NOT NULL,               -- 預計通知對象（人名、或 SP）
        target_src TEXT NOT NULL,           -- CIA負責人／保管者／對不到→SP
        note TEXT,                          -- 診斷：例如「預估 N 天滿」「負責人 X 查不到信箱」
        rule_ver TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE (day, machine, disk))""",
    "CREATE INDEX IF NOT EXISTS ix_disk_notice_shadow_day ON disk_notice_shadow(day)",
]


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def ensure(conn: sqlite3.Connection) -> None:
    for s in SQL:
        conn.execute(s)


# ── 設定 ───────────────────────────────────────────────────────────────
def is_enabled(conn) -> bool:
    """預設關。讀不到、值壞掉＝關（影子模式不該在不明狀態下寫表）。"""
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (KEY_ENABLED,)).fetchone()
    except sqlite3.Error:
        return False
    return bool(r and str(r[0]).strip() in ("1", "true", "True"))


def save_enabled(conn, on: bool) -> bool:
    """存開關（不 commit）。"""
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (KEY_ENABLED, "1" if on else "0"))
    return bool(on)


# ── 負責人對應 ──────────────────────────────────────────────────────────
def resolve_target(conn, ip: str, memo: dict | None = None) -> tuple[str, str, str | None]:
    """IP → (預計通知對象, 來源, 診斷備註)。
    順序：CIA 負責人（hardware.owner）→ 保管者（hardware.custodian）→ 對不到＝SP。
    同一台（machine_key）每一筆登記都看；人名要查得到信箱才算對得到（跟 EOS 通知同一套 eos_notify.effective_mail），
    查不到就試下一個來源，最後落到 SP；備註寫明為什麼。"""
    import eos_notify as en
    import manage_state
    import relay_import
    memo = memo if memo is not None else {}
    notes: list[str] = []
    try:
        res, _n, serial = relay_import.match_asset(conn, ip)
    except sqlite3.Error:
        return SP_NAME, SRC_SP, "資產表讀不到"
    if res != "matched" or not serial:
        return SP_NAME, SRC_SP, "IP 對不到資產" if res == "no_asset" else "IP 對到多台資產（不猜）"
    try:
        sers = manage_state.same_machine_serials(conn, serial) or [serial]
        ph = ",".join("?" for _ in sers)
        rows = conn.execute(f"SELECT owner, custodian FROM hardware WHERE asset_serial IN ({ph}) "
                            "ORDER BY asset_serial = ? DESC", [*sers, serial]).fetchall()
    except sqlite3.Error:
        return SP_NAME, SRC_SP, "資產表讀不到"
    for col, src in (("owner", SRC_OWNER), ("custodian", SRC_CUSTODIAN)):
        names: list[str] = []
        for r in rows:
            for n in en.split_owners(r[col]):
                if n not in names:
                    names.append(n)
        if not names:
            notes.append(f"{src}沒登記")
            continue
        try:
            ok = [n for n in names if en.effective_mail(conn, n, None, memo)]
        except sqlite3.Error:
            ok = []                     # AD 人員表還沒匯入（沒有 ad_person）＝查不到信箱，往下一個來源／SP；備註會寫明
            notes.append("AD 人員表讀不到")
        if ok:
            return "、".join(ok[:3]), src, ("；".join(notes) or None)
        notes.append(f"{src}「{'、'.join(names[:3])}」查不到信箱")
    return SP_NAME, SRC_SP, "；".join(notes) or "沒有登記負責人"


# ── 判定 ───────────────────────────────────────────────────────────────
def _peaks(conn, host: str, disk: str, days: list[str]) -> dict[str, float]:
    import disk_alert
    ph = ",".join("?" for _ in days)
    out = {}
    for r in conn.execute(f"SELECT day, data FROM relay_disk_util WHERE host = ? AND disk = ? AND day IN ({ph})", [host, disk, *days]):
        p = disk_alert.day_peak(r[1])
        if p is not None:
            out[r[0]] = p
    return out


def days_to_full(points: list[tuple[date, float]]) -> float | None:
    """每日最高值的線性外推（端點斜率：最早→最晚）。少於 3 天、跨不到 2 天、沒在上升 → None（不猜）。"""
    pts = sorted(points)
    if len(pts) < 3:
        return None
    span = (pts[-1][0] - pts[0][0]).days
    if span < 2:
        return None
    slope = (pts[-1][1] - pts[0][1]) / span
    if slope <= 0:
        return None
    return max(0.0, (100.0 - pts[-1][1]) / slope)


def judge(today_pct, prev_pct, hist: list[tuple[date, float]]) -> tuple[str, float | None] | None:
    """一顆碟 → (reason, 預估幾天滿) 或 None。門檻比較先經 disk_level.norm（一位小數，全站同一把尺）。"""
    t = disk_level.norm(today_pct)
    if t is None:
        return None
    if t >= CRITICAL_PCT:
        return GE95, None
    d = days_to_full(hist)
    if d is not None and t >= FULL_FLOOR and d <= FULL_DAYS:
        return FULL3D, round(d, 1)
    if disk_level.level(t) == disk_level.HIGH:
        p = disk_level.norm(prev_pct)
        if p is None or disk_level.level(p) != disk_level.HIGH:
            return CROSS, None
    return None


def run(conn, now: datetime | None = None) -> dict:
    """算最近一個資料日的「預計通知」寫進 disk_notice_shadow。關閉＝什麼都不做（不建表）。自己 commit。
    回 {"status": disabled／no_data／stale／ok, "day", "added"}（added＝新增或升級為即時類的筆數）。"""
    if not is_enabled(conn):
        return {"status": "disabled"}
    now = now or datetime.now(TPE)
    if not _has(conn, "relay_disk_util"):
        return {"status": "no_data"}
    import disk_alert
    day = conn.execute("SELECT MAX(day) FROM relay_disk_util").fetchone()[0]
    if not day:
        return {"status": "no_data"}
    try:
        d0 = date.fromisoformat(day)
    except ValueError:
        return {"status": "no_data"}
    if d0 < now.date() - timedelta(days=STALE_DAYS):
        return {"status": "stale", "day": day}       # 太舊的資料不當「今天」算（補匯入的舊批次）
    ensure(conn)
    pats = disk_alert.exclude_list(conn)
    hist_days = [(d0 - timedelta(days=i)).isoformat() for i in range(TREND_DAYS)]
    now_s = now.strftime("%Y-%m-%d %H:%M:%S")
    memo: dict = {}
    added = 0
    # 2.070.1（審查 p2#4）：同一天重跑＝以來源日期完整重建（先刪該日舊列再寫），不然稍後匯入修正版資料、容量下降、
    # 負責人改變後，影子表會留著舊百分比／舊收件人的假陽性。刪除與重寫同一個 savepoint，中途出錯整段退回、不留半份。
    rows = conn.execute("SELECT host, disk, data FROM relay_disk_util WHERE day = ? ORDER BY host, disk", (day,)).fetchall()
    conn.execute("SAVEPOINT disk_shadow_run")
    try:
        conn.execute("DELETE FROM disk_notice_shadow WHERE day = ?", (day,))
        added = _write_rows(conn, rows, day, pats, hist_days, now_s, memo)
        conn.execute("DELETE FROM disk_notice_shadow WHERE day < ?", ((d0 - timedelta(days=KEEP_DAYS)).isoformat(),))
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (KEY_LAST_RUN, json.dumps({"at": now_s, "day": day, "added": added})))
    except BaseException:
        conn.execute("ROLLBACK TO disk_shadow_run")
        conn.execute("RELEASE disk_shadow_run")
        raise
    conn.execute("RELEASE disk_shadow_run")
    conn.commit()
    return {"status": "ok", "day": day, "added": added}


def _write_rows(conn, rows, day, pats, hist_days, now_s, memo) -> int:
    import daily_check
    import disk_alert
    added = 0
    for r in rows:
        host, disk = r[0], r[1]
        if disk_alert.excluded(disk, pats) or daily_check._is_swap(disk):
            continue
        pct = disk_alert.day_peak(r[2])
        if pct is None:
            continue
        pk = _peaks(conn, host, disk, hist_days)
        prev = pk.get(hist_days[1])
        hist = [(date.fromisoformat(k), v) for k, v in pk.items()]
        v = judge(pct, prev, hist)
        if v is None:
            continue
        reason, dtf = v
        immediate = reason in (GE95, FULL3D)
        if immediate:
            target, src, note = SP_NAME, SRC_SP, None
            _t, _s, why = resolve_target(conn, host, memo)
            note = f"負責人對應：{_t}（{_s}）" + (f"；{why}" if why else "")
        else:
            target, src, note = resolve_target(conn, host, memo)
        if dtf is not None:
            note = f"預估 {dtf:g} 天滿" + (f"；{note}" if note else "")
        machine = disk_alert.ident_of(conn, host)
        label = disk_alert.host_label(conn, host)
        hostname = label.split("（", 1)[0] if label != host else None
        cur = conn.execute(
            "INSERT INTO disk_notice_shadow (day, machine, ip, host, disk, pct, prev_pct, reason, immediate, target, target_src, note, rule_ver, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(day, machine, disk) DO UPDATE SET pct = excluded.pct, prev_pct = excluded.prev_pct, reason = excluded.reason, "
            "immediate = excluded.immediate, target = excluded.target, target_src = excluded.target_src, note = excluded.note, "
            "rule_ver = excluded.rule_ver WHERE excluded.immediate = 1 AND disk_notice_shadow.immediate = 0",
            (day, machine, host, hostname, disk, pct, prev, reason, int(immediate), target, src, note, RULE_VER, now_s))
        if cur.rowcount:
            added += 1
    return added


def tick(conn, now: datetime | None = None) -> dict:
    """排程器每分鐘呼叫：關閉不動；台北 07:00 後、當天還沒跑過才算一次。純本機（B 類）。"""
    if not is_enabled(conn):
        return {"status": "disabled"}
    now = now or datetime.now(TPE)
    if now.hour < RUN_AFTER_HOUR:
        return {"status": "early"}
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (KEY_LAST_RUN,)).fetchone()
    try:
        last = json.loads(r[0]).get("at", "")[:10] if r and r[0] else ""
    except (ValueError, TypeError, AttributeError):
        last = ""                       # 標記壞掉＝當作沒跑過，重算一次（寫入是冪等的）
    if last == now.date().isoformat():
        return {"status": "done_today"}
    return run(conn, now)


# ── 畫面用 ─────────────────────────────────────────────────────────────
def view(conn, days: int = 7, today: date | None = None) -> dict:
    """最近 N 天的預計通知彙總＋明細（唯讀；表不存在回空）。每列加項次 __no（預設排序＝日期新→舊、即時類在前）。"""
    today = today or datetime.now(TPE).date()
    since = (today - timedelta(days=days)).isoformat()
    last = None
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (KEY_LAST_RUN,)).fetchone()
        last = json.loads(r[0]) if r and r[0] else None
    except (sqlite3.Error, ValueError, TypeError):
        last = None                     # 讀不到上次執行紀錄只是少一行「上次算的時間」，不影響明細
    rows: list[dict] = []
    if _has(conn, "disk_notice_shadow"):
        for x in conn.execute("SELECT * FROM disk_notice_shadow WHERE day >= ? ORDER BY day DESC, immediate DESC, pct DESC, ip, disk", (since,)):
            d = dict(x)
            d["reason_text"] = REASON_LABEL.get(d["reason"], d["reason"])
            d["immediate"] = bool(d["immediate"])
            rows.append(d)
    for i, d in enumerate(rows, 1):
        d["__no"] = i
    per: dict[str, int] = {}
    for d in rows:
        if d["target_src"] != SRC_SP:
            per[d["target"]] = per.get(d["target"], 0) + 1
    summary = {"total": len(rows), "owner_disks": sum(per.values()), "no_owner": sum(1 for d in rows if d["target_src"] == SRC_SP and not d["immediate"]),
               "immediate": sum(1 for d in rows if d["immediate"]),
               "per_person": [{"target": k, "disks": v} for k, v in sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))]}
    return {"enabled": is_enabled(conn), "days": days, "since": since, "summary": summary, "rows": rows, "last_run": last, "rule_ver": RULE_VER}
