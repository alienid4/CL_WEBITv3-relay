"""排程盤點（2026-10-20 使用者：資產頁要有「排程」分頁，Linux／Windows 都要）。

## 資料從哪來

- **Linux**：不另外連線、不加任何 sudo 規則——直接讀「OS 重建資料」已經收到的原文
  （os_rebuild_collector 的 cron.system／cron.user／cron.periodic／cron.timers，加上
  boot.systemd_custom 裡的自訂 *.timer）。沒收過 OS 重建 → 畫面講「先收 OS 重建」。
- **Windows**：WinRM／PSRP 在記憶體跑 Get-ScheduledTask＋Get-ScheduledTaskInfo（唯讀），
  結果存 host_schedule。拿不到的項目印 ERR 行，不靜默變成 0 個排程。

## 排序（使用者：「從時間最短，到 00 到 23 去排序」）

1. 週期性（每分鐘、每 5 分…每小時、每 N 小時），間隔短的在前
2. 定時（每天／每週／每月／每年／一次），依「幾點幾分」00:00→23:59；同時間時每天在前
3. 時間不明（定義沒收到）
4. 事件／開機／登入觸發、沒有觸發條件的，排最後

編號（no）照這個預設排序給、固定不變；畫面重排後同一筆仍是同一號。

SOC：Windows 腳本只走 PSRP 記憶體執行，不落地、不改執行原則、不編碼、不另起行程
（tests/asset_module/test_agent_scripts_security.py 會自動掃本模組所有 *_PS 常數）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime

# =====================================================================
# 共用：排程 → 白話＋排序鍵
# =====================================================================
#: 排序大類（越小越前面）
B_INTERVAL, B_TIMED, B_UNKNOWN, B_EVENT = 0, 1, 2, 3
_FREQ_RANK = {"daily": 0, "weekly": 1, "monthly": 2, "yearly": 3, "once": 4}
_WD = ["日", "一", "二", "三", "四", "五", "六"]
_MON_NAMES = {n: i for i, n in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DOW_NAMES = {n: i for i, n in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
KIND_LABEL = {"interval": "週期性", "daily": "每天", "weekly": "每週", "monthly": "每月",
              "yearly": "每年", "once": "一次", "boot": "開機", "event": "事件", "logon": "登入",
              "manual": "手動", "unknown": "不明"}


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _expand(field: str, lo: int, hi: int, names: dict | None = None) -> list[int] | None:
    """cron 單一欄位 → 值清單；`*`（不限）回 None。看不懂丟 ValueError。"""
    f = field.strip().lower()
    if f in ("*", "?"):
        return None
    out: set[int] = set()
    for part in f.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step <= 0:
                raise ValueError(field)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a, b = _num(x, names), _num(y, names)
        else:
            a = _num(part, names)
            b = hi if "/" in field else a
        if a < lo or b > hi or a > b:
            raise ValueError(field)
        out.update(range(a, b + 1, step))
    return sorted(out)


def _num(s: str, names: dict | None) -> int:
    if names and s[:3] in names:
        return names[s[:3]]
    return int(s)


def _min_gap(vals: list[int], period: int) -> int:
    if len(vals) < 2:
        return period
    gaps = [b - a for a, b in zip(vals, vals[1:])] + [vals[0] + period - vals[-1]]
    return min(gaps)


def _even(vals: list[int], period: int) -> bool:
    gaps = [b - a for a, b in zip(vals, vals[1:])] + [vals[0] + period - vals[-1]]
    return len(set(gaps)) == 1


def _range_text(vals: list[int], fmt=str, sep="、") -> str:
    """[1,2,3,5] → 1–3、5"""
    out, i = [], 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[j + 1] == vals[j] + 1:
            j += 1
        out.append(fmt(vals[i]) if i == j else
                   (f"{fmt(vals[i])}{sep}{fmt(vals[j])}" if j == i + 1 else f"{fmt(vals[i])}–{fmt(vals[j])}"))
        i = j + 1
    return sep.join(out)


def _dow_text(dows: list[int]) -> str:
    if dows == [1, 2, 3, 4, 5]:
        return "週一至週五"
    if dows == [0, 6]:
        return "週六、週日"
    return "每週" + "、".join(_WD[d] for d in dows)


def _result(kind: str, text: str, interval: int | None = None, tod: int | None = None,
            hours: list[int] | None = None) -> dict:
    if kind == "interval":
        key = [B_INTERVAL, interval or 0, tod or 0]
    elif kind in _FREQ_RANK:
        key = [B_TIMED, tod if tod is not None else 1440, _FREQ_RANK[kind]]
    elif kind == "unknown":
        key = [B_UNKNOWN, tod if tod is not None else 1440, 0]
    else:
        key = [B_EVENT, 0, 0]
    return {"kind": kind, "kind_label": KIND_LABEL.get(kind, kind), "text": text,
            "interval_min": interval, "tod_min": tod,
            "hours": sorted(set(hours or [])), "sort_key": key}


def unknown(text: str, tod: int | None = None) -> dict:
    return _result("unknown", text, tod=tod, hours=[tod // 60] if tod is not None else [])


CRON_SHORTHAND = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
                  "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *",
                  "@annually": "0 0 1 1 *"}


def describe_cron(spec: str) -> dict:
    """cron 時間欄（5 欄或 @xxx）→ {kind, text, interval_min, tod_min, hours, sort_key}。"""
    spec = (spec or "").strip()
    if spec.lower() == "@reboot":
        return _result("boot", "開機時")
    spec = CRON_SHORTHAND.get(spec.lower(), spec)
    parts = spec.split()
    if len(parts) != 5:
        return unknown(f"看不懂：{spec}")
    m_f, h_f, dom_f, mon_f, dow_f = parts
    try:
        mins = _expand(m_f, 0, 59)
        hrs = _expand(h_f, 0, 23)
        doms = _expand(dom_f, 1, 31)
        mons = _expand(mon_f, 1, 12, _MON_NAMES)
        dows = _expand(dow_f, 0, 7, _DOW_NAMES)
    except (ValueError, IndexError):
        return unknown(f"看不懂：{spec}")
    if dows is not None:
        dows = sorted({0 if d == 7 else d for d in dows})
        if len(dows) == 7:
            dows = None

    # 日期限制的前綴（每天／每週一／每月 1 日／每年 1 月 1 日）
    if mons is not None and doms is not None:
        kind, day = "yearly", f"每年{_range_text(mons)}月{_range_text(doms)}日"
    elif doms is not None and dows is not None:
        kind, day = "monthly", f"每月{_range_text(doms)}日或{_dow_text(dows)}"
    elif doms is not None:
        kind, day = "monthly", f"每月{_range_text(doms)}日"
    elif dows is not None:
        kind, day = "weekly", _dow_text(dows)
    elif mons is not None:
        kind, day = "yearly", f"每年{_range_text(mons)}月每天"
    else:
        kind, day = "daily", "每天"
    all_days = kind == "daily"

    hour_wild = h_f.strip() == "*" or h_f.strip().startswith("*/")
    # ---- 週期性：分鐘不只一個值，或小時是 * ／ */n ----
    if mins is None or len(mins) > 1 or hour_wild:
        if mins is None:
            step, mtxt = 1, "每分鐘"
        elif len(mins) > 1:
            step = _min_gap(mins, 60)
            mtxt = f"每 {step} 分" if _even(mins, 60) else \
                "每小時第 " + "、".join(f"{m:02d}" for m in mins) + " 分"
        else:
            step = None
            mtxt = f"第 {mins[0]:02d} 分"
        if hrs is None:
            if step is None:
                step, txt = 60, f"每小時（{mtxt}）"
            else:
                txt = mtxt
        elif hour_wild and step is None:
            hstep = _min_gap(hrs, 24)
            step = 60 * hstep
            txt = f"每 {hstep} 小時（{mtxt}，自 {_hm(hrs[0] * 60 + mins[0])} 起）"
        else:
            if step is None:
                step = 60 * _min_gap(hrs, 24)
            txt = f"{_range_text(hrs, lambda h: f'{h:02d}')} 點 {mtxt}"
        if not all_days:
            txt = f"{day} {txt}"
        return _result("interval", txt, interval=step,
                       tod=(hrs[0] * 60 if hrs else 0), hours=[])
    # ---- 定時：單一分鐘＋明確小時 ----
    times = [h * 60 + mins[0] for h in hrs]
    shown = "、".join(_hm(t) for t in times[:6]) + ("…" if len(times) > 6 else "")
    return _result(kind, f"{day} {shown}", tod=times[0], hours=hrs)


# ---- systemd：OnCalendar／時間長度 ----
_TS_UNITS = {"us": 0, "ms": 0, "s": 1 / 60, "sec": 1 / 60, "second": 1 / 60, "seconds": 1 / 60,
             "m": 1, "min": 1, "minute": 1, "minutes": 1, "h": 60, "hr": 60, "hour": 60,
             "hours": 60, "d": 1440, "day": 1440, "days": 1440, "w": 10080, "week": 10080,
             "weeks": 10080}


def parse_timespan(s: str) -> float | None:
    """systemd 時間長度（1h、15min、1d 2h、90）→ 分鐘。看不懂回 None。"""
    s = (s or "").strip().lower()
    if not s:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s) / 60          # 沒單位＝秒
    total, pos = 0.0, 0
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([a-z]+)", s):
        if s[pos:m.start()].strip():
            return None
        if m.group(2) not in _TS_UNITS:
            return None
        total += float(m.group(1)) * _TS_UNITS[m.group(2)]
        pos = m.end()
    return total if pos and not s[pos:].strip() else None


def _span_text(minutes: float) -> str:
    if minutes < 1:
        return f"{round(minutes * 60)} 秒"
    if minutes % 1440 == 0:
        return f"{int(minutes // 1440)} 天"
    if minutes % 60 == 0:
        return f"{int(minutes // 60)} 小時"
    return f"{round(minutes)} 分"


_CAL_SHORT = {"minutely": "* * * * *", "hourly": "0 * * * *", "daily": "0 0 * * *",
              "weekly": "0 0 * * 1", "monthly": "0 0 1 * *", "yearly": "0 0 1 1 *",
              "annually": "0 0 1 1 *", "quarterly": "0 0 1 1,4,7,10 *",
              "semiannually": "0 0 1 1,7 *"}
_SD_DOW = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0}


def _sd_field(v: str) -> str:
    """systemd 時間欄 → cron 欄：`*`、`0/10`（自 0 起每 10）、`6,18`、`02`。"""
    v = v.strip()
    if v == "*":
        return "*"
    if "/" in v:
        a, b = v.split("/", 1)
        return f"*/{int(b)}" if a in ("*", "0", "00") else f"{int(a)}-59/{int(b)}"
    return ",".join(str(int(x)) if x.isdigit() else x for x in v.split(","))


def calendar_to_cron(expr: str) -> str | None:
    """OnCalendar 常見寫法 → 5 欄 cron（只為了白話與排序；看不懂回 None）。"""
    e = (expr or "").strip()
    if e.lower() in _CAL_SHORT:
        return _CAL_SHORT[e.lower()]
    toks = e.split()
    dow, date, time = "*", None, None
    for t in toks:
        if re.match(r"^[A-Za-z]", t):
            days: set[int] = set()
            for seg in t.split(","):
                if ".." in seg:
                    a, b = seg.split("..", 1)
                    x, y = _SD_DOW.get(a[:3].lower()), _SD_DOW.get(b[:3].lower())
                    if x is None or y is None:
                        return None
                    days.update(range(x, (y if y >= x else y + 7) + 1))
                else:
                    x = _SD_DOW.get(seg[:3].lower())
                    if x is None:
                        return None
                    days.add(x)
            dow = ",".join(str(d % 7) for d in sorted(days))
        elif ":" in t:
            time = t
        elif "-" in t:
            date = t
        else:
            return None
    if time is None:
        time = "00:00:00"
    tp = time.split(":")
    if len(tp) < 2:
        return None
    try:
        hour, minute = _sd_field(tp[0]), _sd_field(tp[1])
        dom, mon = "*", "*"
        if date:
            dp = date.split("-")
            if len(dp) == 3:
                mon, dom = _sd_field(dp[1]), _sd_field(dp[2])
            elif len(dp) == 2:
                mon, dom = _sd_field(dp[0]), _sd_field(dp[1])
    except ValueError:
        return None
    return f"{minute} {hour} {dom} {mon} {dow}"


def describe_timer_unit(directives: dict) -> dict | None:
    """*.timer 的 [Timer] 段 → 白話。directives: {OnCalendar: [...], OnBootSec: [...]…}"""
    for cal in directives.get("OnCalendar", []):
        cron = calendar_to_cron(cal)
        if cron:
            d = describe_cron(cron)
            if d["kind"] != "unknown":
                return d
        return unknown(f"OnCalendar={cal}")
    for key, label in (("OnUnitActiveSec", "上次開始後"), ("OnUnitInactiveSec", "上次結束後"),
                       ("OnActiveSec", "啟用後")):
        for v in directives.get(key, []):
            m = parse_timespan(v)
            if m:
                return _result("interval", f"每 {_span_text(m)}（{label}）", interval=max(1, round(m)))
    for key, label in (("OnBootSec", "開機後"), ("OnStartupSec", "systemd 啟動後")):
        for v in directives.get(key, []):
            m = parse_timespan(v)
            return _result("boot", f"{label} {_span_text(m)}" if m else f"{label}（{v}）")
    return None


# =====================================================================
# Linux：從 OS 重建資料組出排程
# =====================================================================
_ENV_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")
#: 常見的系統預設 timer（unit 在 /usr/lib/systemd/system，我們沒收它的內容）。值＝上游預設，
#: 畫面標「推定（系統預設值）」。不在表裡的就用 下次－上次 推估，再不行就講「定義未收」。
KNOWN_TIMERS = {
    "dnf-makecache.timer": {"OnUnitInactiveSec": ["1h"]},
    "logrotate.timer": {"OnCalendar": ["daily"]},
    "fstrim.timer": {"OnCalendar": ["weekly"]},
    "systemd-tmpfiles-clean.timer": {"OnUnitActiveSec": ["1d"]},
    "unbound-anchor.timer": {"OnCalendar": ["daily"]},
    "mlocate-updatedb.timer": {"OnCalendar": ["daily"]},
    "plocate-updatedb.timer": {"OnCalendar": ["daily"]},
    "sysstat-collect.timer": {"OnCalendar": ["*:00/10"]},
    "sysstat-summary.timer": {"OnCalendar": ["00:07:00"]},
    "apt-daily.timer": {"OnCalendar": ["*-*-* 6,18:00"]},
    "apt-daily-upgrade.timer": {"OnCalendar": ["*-*-* 6:00"]},
    "e2scrub_all.timer": {"OnCalendar": ["Sun *-*-* 03:10:00"]},
    "man-db.timer": {"OnCalendar": ["daily"]},
    "dpkg-db-backup.timer": {"OnCalendar": ["daily"]},
    "motd-news.timer": {"OnCalendar": ["*-*-* 6,18:00"]},
}


def parse_crontab(text: str, *, source: str, has_user: bool, user: str = "") -> list[dict]:
    """crontab 原文 → 列。has_user＝/etc/crontab、/etc/cron.d 那種第 6 欄是帳號。"""
    rows = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or _ENV_LINE.match(s):
            continue
        if s.startswith("@"):
            parts = s.split(None, 2 if has_user else 1)
            spec = parts[0]
            rest = parts[1:]
        else:
            parts = s.split(None, 6 if has_user else 5)
            if len(parts) < (7 if has_user else 6):
                continue
            spec = " ".join(parts[:5])
            rest = parts[5:]
        if has_user:
            if len(rest) < 2:
                continue
            acct, cmd = rest[0], rest[1]
        else:
            if not rest:
                continue
            acct, cmd = user, rest[0]
        rows.append({"source": source, "account": acct, "raw": spec, "command": cmd,
                     "sched": describe_cron(spec)})
    return rows


def parse_timer_units(files: list[dict]) -> dict[str, dict]:
    """/etc/systemd/system 收到的檔 → {xxx.timer: {OnCalendar: [...], …, Unit: [...]}}。"""
    out: dict[str, dict] = {}
    for f in files or []:
        path = f.get("path") or ""
        if not path.endswith(".timer") and ".timer.d/" not in path:
            continue
        name = path.rsplit("/", 1)[-1] if path.endswith(".timer") else \
            path.split(".timer.d/")[0].rsplit("/", 1)[-1] + ".timer"
        d = out.setdefault(name, {"_path": path})
        in_timer = False
        for ln in (f.get("content") or "").splitlines():
            s = ln.strip()
            if s.startswith("["):
                in_timer = s.lower() == "[timer]"
                continue
            if not in_timer or "=" not in s or s.startswith(("#", ";")):
                continue
            k, v = (x.strip() for x in s.split("=", 1))
            if v == "":
                d.pop(k, None)              # drop-in 的「清空」寫法
                continue
            d.setdefault(k, []).append(v)
    return out


_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def parse_list_timers(text: str) -> list[dict]:
    """`systemctl list-timers --all --no-legend` → [{unit, activates, next, last}]。"""
    out = []
    for ln in (text or "").splitlines():
        toks = ln.split()
        idx = next((i for i, t in enumerate(toks) if t.endswith(".timer")), None)
        if idx is None:
            continue
        head = " ".join(toks[:idx])
        stamps = _TS_RE.findall(head)
        never_next = head.strip().startswith(("n/a", "-"))
        nxt = None if never_next else (stamps[0] if stamps else None)
        last = (stamps[0] if never_next else (stamps[1] if len(stamps) > 1 else None)) if stamps else None
        out.append({"unit": toks[idx], "activates": toks[idx + 1] if idx + 1 < len(toks) else "",
                    "next": nxt, "last": last})
    return out


def _guess_from_delta(nxt: str | None, last: str | None) -> dict | None:
    if not (nxt and last):
        return None
    try:
        a = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
        b = datetime.strptime(nxt, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    secs = (b - a).total_seconds()
    if secs <= 0:
        return None
    mins = round(secs / 60)
    tod = b.hour * 60 + b.minute
    if secs < 60:
        d = _result("interval", f"約每 {int(secs)} 秒（推估：下次－上次）", interval=1)
    elif mins < 23 * 60:
        d = _result("interval", f"約每 {_span_text(mins)}（推估：下次－上次）", interval=mins)
    elif mins <= 25 * 60:
        d = _result("daily", f"約每天 {_hm(tod)}（推估：下次－上次）", tod=tod, hours=[b.hour])
    elif 6 * 1440 <= mins <= 8 * 1440:
        d = _result("weekly", f"約每週{_WD[(b.weekday() + 1) % 7]} {_hm(tod)}（推估）", tod=tod, hours=[b.hour])
    else:
        return None
    return d


def _item(data: dict, iid: str) -> dict | None:
    for g in (data or {}).get("groups") or []:
        for it in g.get("items") or []:
            if it.get("id") == iid:
                return it
    return None


_STATE_NOTE = {"denied": "權限不足", "no_tool": "此機沒有這個指令", "unknown": "不明",
               "not_run": "沒收到（舊版收集）"}


def linux_rows(data: dict) -> tuple[list[dict], list[str]]:
    """OS 重建資料（build_rebuild_data 的輸出）→ (排程列, 備註)。"""
    rows: list[dict] = []
    notes: list[str] = []
    for iid in ("cron.system", "cron.user", "cron.periodic", "cron.timers"):
        it = _item(data, iid)
        if it is None:
            notes.append(f"{iid}：OS 重建資料裡沒有這一項（舊版收集，重收一次 OS 重建）")
        elif it.get("state") in _STATE_NOTE:
            paths = it.get("denied_paths") or it.get("unreadable_paths") or []
            notes.append(f"{it.get('title', iid)}：{it.get('label') or _STATE_NOTE[it['state']]}"
                         + (f"（{'、'.join(paths[:5])}）" if paths else ""))
        elif it.get("note"):
            notes.append(f"{it.get('title', iid)}：{it['note']}")

    sys_it = _item(data, "cron.system") or {}
    for f in sys_it.get("files") or []:
        rows += parse_crontab(f.get("content", ""), source=f.get("path", "/etc/crontab"), has_user=True)
    usr_it = _item(data, "cron.user") or {}
    for f in usr_it.get("files") or []:
        u = (f.get("path") or "").rsplit("/", 1)[-1]
        rows += parse_crontab(f.get("content", ""), source=f"使用者 crontab（{u}）",
                              has_user=False, user=u)
    for r in rows:
        r["builtin"] = False

    # cron.{hourly,daily,weekly,monthly}：每支腳本一列；時間取 run-parts 那一行（Debian 在 /etc/crontab，
    # RHEL hourly 在 /etc/cron.d/0hourly）。RHEL 的 daily/weekly/monthly 由 anacron 排（/etc/anacrontab
    # 沒收），講清楚是 anacron、時間不寫死。
    per_it = _item(data, "cron.periodic") or {}
    runparts = {}
    for r in rows:
        for p in ("hourly", "daily", "weekly", "monthly"):
            if f"cron.{p}" in r["command"]:
                runparts.setdefault(p, r)
    default = {"hourly": _result("interval", "每小時", interval=60),
               "daily": unknown("每天（由 anacron 排，預設 03:00–22:00 間隨機延遲）"),
               "weekly": unknown("每週（由 anacron 排）"),
               "monthly": unknown("每月（由 anacron 排）")}
    for f in per_it.get("files") or []:
        path = f.get("path") or ""
        m = re.search(r"/etc/cron\.(hourly|daily|weekly|monthly)/", path)
        if not m:
            continue
        p = m.group(1)
        rp = runparts.get(p)
        rows.append({"source": f"cron.{p}", "account": "root",
                     "raw": rp["raw"] if rp else f"cron.{p}",
                     "command": path, "sched": rp["sched"] if rp else default[p], "builtin": False})

    # systemd timers
    tim_it = _item(data, "cron.timers") or {}
    sd_it = _item(data, "boot.systemd_custom") or {}
    units = parse_timer_units(sd_it.get("files") or [])
    # /etc/systemd/system 底下有、但只給 root 讀的 *.timer：是自訂的（不是系統內建），只是定義沒讀到
    etc_timers = {p.rsplit("/", 1)[-1] for p in (sd_it.get("unreadable_paths") or []) + (sd_it.get("denied_paths") or [])
                  if p.endswith(".timer")}
    for t in parse_list_timers(tim_it.get("text") or ""):
        custom = units.get(t["unit"])
        sched = describe_timer_unit(custom) if custom else None
        raw = ""
        if custom:
            raw = "; ".join(f"{k}={v}" for k, vs in custom.items() if not k.startswith("_")
                            and k.startswith("On") for v in vs)
        if sched is None and t["unit"] in KNOWN_TIMERS:
            sched = describe_timer_unit(KNOWN_TIMERS[t["unit"]])
            if sched:
                sched["text"] += "（推定：系統預設值）"
                raw = "; ".join(f"{k}={v}" for k, vs in KNOWN_TIMERS[t["unit"]].items() for v in vs)
        if sched is None:
            sched = _guess_from_delta(t["next"], t["last"])
            if sched and t["unit"] in etc_timers:
                sched["text"] = sched["text"].replace("（推估", "（定義檔只給 root 讀；推估")
        if sched is None:
            tod = None
            if t["next"]:
                tod = int(t["next"][11:13]) * 60 + int(t["next"][14:16])
            sched = unknown("systemd timer（定義檔只給 root 讀，未收）" if t["unit"] in etc_timers
                            else "systemd timer（定義在系統預設 unit，未收）", tod=tod)
        rows.append({"source": "systemd timer", "account": "root（systemd）", "raw": raw or t["unit"],
                     "command": t["activates"] or t["unit"], "name": t["unit"],
                     "next_run": t["next"], "last_run": t["last"], "sched": sched,
                     # 系統內建＝不是 /etc 自訂、而且是已知的發行版預設 timer；認不出來的一律顯示，不藏
                     "builtin": custom is None and t["unit"] not in etc_timers and t["unit"] in KNOWN_TIMERS})
    return rows, notes


# =====================================================================
# Windows：Get-ScheduledTask over PSRP
# =====================================================================
from winrm_collector import _PS_ERR_FN  # noqa: E402 - 共用 ERR 行格式（W3Err）

#: 唯讀：只列舉工作排程器。每項各自 try／catch，拿不到印 ERR，不靜默變 0。
#: 2026-10-21 使用者（工作排程器內容視窗截圖）「這些都是 WINDOWS 排程的重點」：
#: 一般（名稱／位置／作者／描述）、觸發程序（每個觸發的類型／重複／到期／狀態）、動作（程式／引數／起始位置）、
#: 安全性選項（執行帳戶／登入方式／最高權限／隱藏／設定相容性）。兩條路要給出同一組欄位：
#:   ・Get-ScheduledTask（CIM）：TASK／ACT／TRG 行直接帶這些欄
#:   ・COM 退路（Schedule.Service）：TASK 行只帶執行狀態，定義整份走 XML 行（工作定義 XML，攤成一行），
#:     由 Python 解析成同一組欄位（parse_task_xml）
SCHEDULE_PS = r"""
$ErrorActionPreference='SilentlyContinue'
""" + _PS_ERR_FN + r"""
function W3T($d) { if ($d) { ([datetime]$d).ToString('yyyy-MM-dd HH:mm:ss') } else { '' } }
function W3S($s, $n) { $v = ("" + $s) -replace '[\t\r\n]+', ' '; if ($n -and $v.Length -gt $n) { $v = $v.Substring(0, $n) }; $v }
"SRC=schtasks"
$tasks = @()
$taskErr = $false
try { $tasks = @(Get-ScheduledTask -ErrorAction Stop) } catch { W3Err 'tasks' $_; $taskErr = $true }
if ($tasks.Count -eq 0 -and -not $taskErr) { "ERR`ttasks`tEMPTY`tGet-ScheduledTask returned no rows" }
$infoErr = 0
$infoMsg = ''
foreach ($t in $tasks) {
  $id = "" + $t.TaskPath + $t.TaskName
  $info = $null
  try { $info = Get-ScheduledTaskInfo -InputObject $t -ErrorAction Stop } catch { $infoErr++; $infoMsg = "" + $_.Exception.Message }
  $res = ''
  if ($info) { $res = "" + $info.LastTaskResult }
  $s = $t.Settings
  "TASK`t$id`t$($t.TaskPath)`t$(W3S $t.TaskName 200)`t$($t.State)`t$(W3S $t.Principal.UserId 120)`t$($t.Principal.LogonType)`t$($t.Principal.RunLevel)`t$(W3S $t.Author 120)`t$(W3T $info.LastRunTime)`t$res`t$(W3T $info.NextRunTime)`t$(W3S $t.Description 400)`t$($s.Hidden)`t$($s.Enabled)`t$($s.Compatibility)`t$(W3S $t.Principal.GroupId 120)`tcim"
  foreach ($a in @($t.Actions)) {
    "ACT`t$id`t$(W3S $a.Execute 300)`t$(W3S $a.Arguments 300)`t$(W3S $a.WorkingDirectory 200)`t$($a.ClassId)"
  }
  foreach ($g in @($t.Triggers)) {
    if (-not $g) { continue }
    "TRG`t$id`t$($g.CimClass.CimClassName)`t$($g.Enabled)`t$($g.StartBoundary)`t$($g.DaysInterval)`t$($g.WeeksInterval)`t$($g.DaysOfWeek)`t$($g.Repetition.Interval)`t$($g.Repetition.Duration)`t$($g.Delay)`t$(W3S $g.UserId 120)`t$($g.EndBoundary)`t$($g.Repetition.StopAtDurationEnd)`t$($g.RandomDelay)`t$(W3S $g.Subscription 300)`t$($g.StateChange)"
  }
}
if ($infoErr -gt 0) { "ERR`ttaskinfo`tDENIED`t$infoErr tasks: $(W3S $infoMsg 160)" }
# 退路（2026-10-21 公司機「✕ 排程」）：非管理員經 PSRP 跑 Get-ScheduledTask 常被 CIM 拒（0x80070005）
# 或回空。改走工作排程器的 COM 介面（Schedule.Service，同一個 svchost 服務，程序內、不起任何執行檔），
# 唯讀列舉；非管理員看得到的工作會比管理員少——那一條 ERR 仍保留，畫面照講「權限不足」。
# 定義（觸發／動作／安全性／設定）讀工作的定義 XML（IRegisteredTask.Xml，唯讀屬性），攤成一行送回。
if ($tasks.Count -eq 0) {
  $comN = 0
  try {
    $sch = New-Object -ComObject Schedule.Service
    $sch.Connect()
    $stack = New-Object System.Collections.Stack
    $stack.Push($sch.GetFolder('\'))
    $fErr = 0
    while ($stack.Count -gt 0) {
      $f = $stack.Pop()
      try { foreach ($sf in @($f.GetFolders(0))) { $stack.Push($sf) } } catch { $fErr++ }
      $ts = @()
      try { $ts = @($f.GetTasks(1)) } catch { $fErr++ }
      $fp = "" + $f.Path
      if (-not $fp.EndsWith('\')) { $fp = $fp + '\' }
      foreach ($t in $ts) {
        try {
          $id = $fp + $t.Name
          $st = switch ([int]$t.State) { 1 {'Disabled'} 2 {'Queued'} 3 {'Ready'} 4 {'Running'} default {'Unknown'} }
          "TASK`t$id`t$fp`t$(W3S $t.Name 200)`t$st`t`t`t`t`t$(W3T $t.LastRunTime)`t$($t.LastTaskResult)`t$(W3T $t.NextRunTime)`t`t`t`t`t`tcom"
          $x = ''
          try { $x = ("" + $t.Xml) -replace '[\t\r\n]+', ' ' } catch { "XERR`t$id`t$(W3S $_.Exception.Message 160)" }
          if ($x) { "XML`t$id`t$x" }
          $comN++
        } catch { $fErr++ }
      }
    }
    "SRC2=com`t$comN"
    if ($fErr -gt 0) { "ERR`ttasks_com`tDENIED`t$fErr folders/tasks unreadable via Schedule.Service" }
  } catch { W3Err 'tasks_com' $_ }
}
"""

#: Windows 項目中文名／需要的權限（給 winrm_collector.explain_error 風格的說明）
WIN_ITEM_LABEL = {"tasks": "排程工作清單", "taskinfo": "排程的上次／下次執行時間",
                  "tasks_com": "排程工作清單（COM 退路）", "taskxml": "排程的工作定義"}
WIN_PERM_NEED = {"tasks": "本機 Administrators（非管理員只看得到自己有權限的工作）",
                 "taskinfo": "本機 Administrators",
                 "tasks_com": "本機 Administrators（非管理員只看得到自己有權限的工作）",
                 "taskxml": "本機 Administrators"}

#: LastTaskResult 常見值
TASK_RESULT = {0: "成功", 0x41300: "就緒", 0x41301: "執行中", 0x41302: "已停用",
               0x41303: "尚未執行過", 0x41304: "沒有其他排程", 0x41306: "被使用者終止",
               0x41325: "排隊中", 0x8004131F: "已有執行個體在跑", 0x800710E0: "被拒（權限／條件）",
               0x80070002: "找不到檔案", 0x80070005: "存取被拒", 0x8007010B: "起始位置的目錄不存在",
               0x800704DD: "使用者沒有登入（只在登入時執行的工作）", 0xC000013A: "被中止（關機或 Ctrl+C）",
               1: "失敗（錯誤碼 1）", 2: "失敗（找不到檔案）"}
STATE_LABEL = {"Ready": "就緒", "Running": "執行中", "Disabled": "停用", "Queued": "排隊中",
               "Unknown": "不明"}


def _iso_minutes(v: str) -> float | None:
    """ISO 8601 期間（PT5M、PT1H、P1D、P1DT12H）→ 分鐘。"""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", (v or "").strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 1440 + h * 60 + mi + s / 60


def _start_tod(start: str) -> int | None:
    m = re.search(r"T(\d{2}):(\d{2})", start or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _days_mask(mask: int) -> list[int]:
    return [d for d in range(7) if mask & (1 << d)]


def _monthly_text(trg: dict, tod: int | None) -> str:
    """每月觸發 → 「每月 1、15 日 23:00」／「每月第 1 個週一 03:00」（＋限定月份）。CIM 拿不到細節就照講。"""
    hm = f" {_hm(tod)}" if tod is not None else ""
    doms = trg.get("days_of_month") or []
    wks = trg.get("weeks_of_month") or []
    dow = int(trg.get("days_of_week") or 0)
    if doms:
        day = "每月 " + "、".join("最後一天" if d == "Last" else str(d) for d in doms) + " 日"
    elif wks and dow:
        day = ("每月第 " + "、".join("最後" if w == "Last" else str(w) for w in wks) + " 個"
               + "、".join("週" + _WD[d] for d in _days_mask(dow)))
    else:
        return "每月" + hm + "（日期細節 Windows 未提供）"
    mons = trg.get("months") or []
    if mons and len(mons) < 12:
        day += "（" + "、".join(str(m) for m in mons) + " 月）"
    return day + hm


def describe_win_trigger(trg: dict) -> dict:
    """一個 TRG → 排序用的白話（sort_key／hours）。trg: {cls, enabled, start, days_interval, weeks_interval,
    days_of_week, rep_interval, rep_duration, delay}。給人看的完整句子見 trigger_text。"""
    cls = (trg.get("cls") or "").replace("MSFT_Task", "").replace("Trigger", "")
    tod = _start_tod(trg.get("start"))
    rep = _iso_minutes(trg.get("rep_interval") or "")
    delay = _iso_minutes(trg.get("delay") or "")
    dtxt = f"（延遲 {_span_text(delay)}）" if delay else ""
    if cls == "Boot":
        return _result("boot", "開機時" + dtxt)
    if cls == "Logon":
        u = trg.get("user")
        return _result("logon", f"{u} 登入時" if u else "任何人登入時")
    if cls == "Idle":
        return _result("event", "電腦閒置時")
    if cls == "Event":
        return _result("event", "事件記錄觸發")
    if cls == "SessionStateChange":
        return _result("event", "工作階段狀態變更時（連線／鎖定）")
    if cls == "Registration":
        return _result("event", "建立或修改工作時")
    if rep:
        base = {"Daily": "每天", "Weekly": "每週", "Time": ""}.get(cls, "")
        frm = f"自 {_hm(tod)} 起" if tod is not None else ""
        return _result("interval", f"{base}{frm}每 {_span_text(rep)}".strip(),
                       interval=max(1, round(rep)), tod=tod or 0)
    if cls == "Time":
        start = (trg.get("start") or "")[:16].replace("T", " ")
        return _result("once", f"一次：{start}" if start else "一次",
                       tod=tod, hours=[tod // 60] if tod is not None else [])
    if cls == "Daily":
        n = int(trg.get("days_interval") or 1)
        day = "每天" if n <= 1 else f"每 {n} 天"
        if tod is None:
            return unknown(day)
        return _result("daily", f"{day} {_hm(tod)}", tod=tod, hours=[tod // 60])
    if cls == "Weekly":
        days = _days_mask(int(trg.get("days_of_week") or 0))
        n = int(trg.get("weeks_interval") or 1)
        dt = _dow_text(days) if days else "每週"
        if n > 1:
            dt = dt.replace("每週", f"每 {n} 週", 1) if dt.startswith("每週") else f"每 {n} 週 {dt}"
        if tod is None:
            return unknown(dt)
        return _result("weekly", f"{dt} {_hm(tod)}", tod=tod, hours=[tod // 60])
    if cls in ("", "Monthly", "MonthlyDOW"):
        # ScheduledTasks 模組把「每月」觸發回成泛用 MSFT_TaskTrigger，細節（哪幾日）拿不到；COM 退路讀 XML 拿得到
        return _result("monthly", _monthly_text(trg, tod),
                       tod=tod, hours=[tod // 60] if tod is not None else [])
    return unknown(f"{trg.get('cls')}")


# ---------------------------------------------------------------------
# 工作定義 XML（COM 退路）→ 跟 Get-ScheduledTask 同一組欄位
# ---------------------------------------------------------------------
_XML_TRG_CLS = {"LogonTrigger": "MSFT_TaskLogonTrigger", "BootTrigger": "MSFT_TaskBootTrigger",
                "IdleTrigger": "MSFT_TaskIdleTrigger", "TimeTrigger": "MSFT_TaskTimeTrigger",
                "EventTrigger": "MSFT_TaskEventTrigger", "RegistrationTrigger": "MSFT_TaskRegistrationTrigger",
                "SessionStateChangeTrigger": "MSFT_TaskSessionStateChangeTrigger"}
_XML_DOW = {n: i for i, n in enumerate(["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
                                        "Friday", "Saturday"])}
_XML_MON = {n: i for i, n in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                        "August", "September", "October", "November", "December"], 1)}
_SESSION_CHANGE = {"ConsoleConnect": 1, "ConsoleDisconnect": 2, "RemoteConnect": 3,
                   "RemoteDisconnect": 4, "SessionLock": 7, "SessionUnlock": 8}


def _strip_ns(root) -> None:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _txt(el, path: str, default: str = "") -> str:
    x = el.find(path) if el is not None else None
    return (x.text or "").strip() if x is not None and x.text else default


def _children(el, path: str) -> list:
    x = el.find(path) if el is not None else None
    return list(x) if x is not None else []


def _list_vals(el, path: str) -> list:
    return [int(x.text) if (x.text or "").strip().isdigit() else (x.text or "").strip()
            for x in _children(el, path)]


def _dow_mask_xml(el, path: str) -> str:
    return str(sum(1 << _XML_DOW[x.tag] for x in _children(el, path) if x.tag in _XML_DOW))


def _months_xml(el, path: str) -> list[int]:
    return sorted(_XML_MON[x.tag] for x in _children(el, path) if x.tag in _XML_MON)


def parse_task_xml(xml: str) -> dict:
    """工作排程器的定義 XML → {author, description, user, group, logon_type, run_level, hidden,
    settings_enabled, compat, actions, triggers}。看不懂丟 ValueError（呼叫端標「不知道」）。"""
    import xml.etree.ElementTree as ET

    s = (xml or "").strip()
    if not s:
        raise ValueError("空的 XML")
    if re.search(r"<!DOCTYPE|<!ENTITY", s, re.IGNORECASE):
        raise ValueError("XML 含 DOCTYPE／ENTITY，不解析")       # 防實體展開；工作定義不會有這個
    s = re.sub(r"^<\?xml[^>]*\?>", "", s).strip()
    root = ET.fromstring(s)
    _strip_ns(root)
    if root.tag != "Task":
        raise ValueError(f"不是工作定義（根節點 {root.tag}）")
    reg = root.find("RegistrationInfo")
    out = {"author": _txt(reg, "Author"), "description": _txt(reg, "Description"),
           "actions": [], "triggers": []}

    # 安全性：Actions 的 Context 指到哪個 Principal 就用哪個；沒寫就第一個
    acts = root.find("Actions")
    ctx = acts.get("Context") if acts is not None else None
    pr = None
    for p in root.findall("Principals/Principal"):
        if pr is None or (ctx and p.get("id") == ctx):
            pr = p
    out["user"] = _txt(pr, "UserId")
    out["group"] = _txt(pr, "GroupId")
    lt = _txt(pr, "LogonType")
    if not lt and out["group"] and not out["user"]:
        lt = "Group"
    out["logon_type"] = lt
    out["run_level"] = _txt(pr, "RunLevel", "LeastPrivilege" if pr is not None else "")

    st = root.find("Settings")
    out["hidden"] = _txt(st, "Hidden", "false").lower() == "true"
    out["settings_enabled"] = _txt(st, "Enabled", "true").lower() == "true"
    out["compat"] = _txt(st, "Compatibility") or (f"version {root.get('version')}" if root.get("version") else "")

    for a in list(acts) if acts is not None else []:
        if a.tag == "Exec":
            out["actions"].append({"exe": _txt(a, "Command"), "args": _txt(a, "Arguments"),
                                   "workdir": _txt(a, "WorkingDirectory"), "class_id": ""})
        elif a.tag == "ComHandler":
            out["actions"].append({"exe": "", "args": _txt(a, "Data"), "workdir": "",
                                   "class_id": _txt(a, "ClassId")})
        else:                                   # SendEmail／ShowMessage（已淘汰）照實列出類型
            out["actions"].append({"exe": "", "args": "", "workdir": "", "class_id": "", "other": a.tag})

    trgs = root.find("Triggers")
    for g in list(trgs) if trgs is not None else []:
        rep = g.find("Repetition")
        sc = _txt(g, "StateChange")
        d = {"cls": _XML_TRG_CLS.get(g.tag, g.tag), "enabled": _txt(g, "Enabled", "true"),
             "start": _txt(g, "StartBoundary"), "end": _txt(g, "EndBoundary"),
             "days_interval": None, "weeks_interval": None, "days_of_week": None,
             "rep_interval": _txt(rep, "Interval"), "rep_duration": _txt(rep, "Duration"),
             "rep_stop": _txt(rep, "StopAtDurationEnd"),
             "delay": _txt(g, "Delay"), "random_delay": _txt(g, "RandomDelay"),
             "user": _txt(g, "UserId"), "subscription": _txt(g, "Subscription")[:300],
             "state_change": str(_SESSION_CHANGE.get(sc, sc)) if sc else ""}
        if g.tag == "CalendarTrigger":
            bd, bw = g.find("ScheduleByDay"), g.find("ScheduleByWeek")
            bm, bmd = g.find("ScheduleByMonth"), g.find("ScheduleByMonthDayOfWeek")
            if bd is not None:
                d["cls"], d["days_interval"] = "MSFT_TaskDailyTrigger", _txt(bd, "DaysInterval", "1")
            elif bw is not None:
                d["cls"], d["weeks_interval"] = "MSFT_TaskWeeklyTrigger", _txt(bw, "WeeksInterval", "1")
                d["days_of_week"] = _dow_mask_xml(bw, "DaysOfWeek")
            elif bm is not None:
                d["cls"] = "MSFT_TaskMonthlyTrigger"
                d["days_of_month"] = _list_vals(bm, "DaysOfMonth")
                d["months"] = _months_xml(bm, "Months")
            elif bmd is not None:
                d["cls"] = "MSFT_TaskMonthlyDOWTrigger"
                d["weeks_of_month"] = _list_vals(bmd, "Weeks")
                d["days_of_week"] = _dow_mask_xml(bmd, "DaysOfWeek")
                d["months"] = _months_xml(bmd, "Months")
            else:
                d["cls"] = "MSFT_TaskTimeTrigger"   # 沒有 ScheduleBy＝只跑一次
        out["triggers"].append(d)
    return out


def _bool(v) -> bool | None:
    s = str(v if v is not None else "").strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def parse_windows_schedule(text: str) -> dict:
    """SCHEDULE_PS 輸出 → {tasks: [...], errors: [...]}。

    CIM 路徑：TASK＋ACT＋TRG 行。COM 退路：TASK（執行狀態）＋XML（定義）行。舊版收集只有前 13 欄。"""
    import winrm_collector

    tasks: dict[str, dict] = {}
    xml_bad = 0
    for raw in (text or "").splitlines():
        ln = raw.rstrip("\r\n")
        p = ln.split("\t")
        if p[0] == "TASK" and len(p) >= 13:
            def _t(v):
                # 1999-／0001-／1899-12-30＝從沒跑過（COM 介面的「空日期」是 1899-12-30）
                return v if v and not v.startswith(("1999-", "0001-", "1899-")) else None
            res = None
            if p[10].strip():
                try:
                    res = int(p[10]) & 0xFFFFFFFF
                except ValueError:
                    res = None
            ext = p[13:] + [""] * 5
            src = ext[4].strip() or None
            tasks[p[1]] = {"id": p[1], "path": p[2], "name": p[3], "state": p[4],
                           "user": p[5], "group": ext[3], "logon_type": p[6], "run_level": p[7],
                           "author": p[8], "last_run": _t(p[9]), "last_result": res, "next_run": _t(p[11]),
                           "info_ok": bool(p[10].strip()), "description": p[12],
                           # 舊版收集（只有 13 欄）沒有這三樣 → None＝不知道，不是「否」
                           "hidden": _bool(ext[0]) if src else None,
                           "settings_enabled": _bool(ext[1]) if src else None,
                           "compat": ext[2] if src else None,
                           "src": src, "def_error": None, "actions": [], "triggers": []}
        elif p[0] == "ACT" and len(p) >= 6 and p[1] in tasks:
            tasks[p[1]]["actions"].append({"exe": p[2], "args": p[3], "workdir": p[4], "class_id": p[5]})
        elif p[0] == "TRG" and len(p) >= 12 and p[1] in tasks:
            ext = p[12:] + [""] * 5
            tasks[p[1]]["triggers"].append({
                "cls": p[2], "enabled": p[3], "start": p[4], "days_interval": p[5] or None,
                "weeks_interval": p[6] or None, "days_of_week": p[7] or None,
                "rep_interval": p[8], "rep_duration": p[9], "delay": p[10], "user": p[11],
                "end": ext[0], "rep_stop": ext[1], "random_delay": ext[2], "subscription": ext[3],
                "state_change": ext[4]})
        elif p[0] == "XML" and len(p) >= 3 and p[1] in tasks:
            t = tasks[p[1]]
            try:
                d = parse_task_xml("\t".join(p[2:]))
            except Exception as exc:  # noqa: BLE001 - 單一工作定義壞掉不影響其他筆，原因標在該筆
                t["def_error"] = f"工作定義 XML 解析失敗：{str(exc)[:120]}"
                xml_bad += 1
                continue
            t.update(d)
        elif p[0] == "XERR" and len(p) >= 2 and p[1] in tasks:
            tasks[p[1]]["def_error"] = ("權限不足：讀不到工作定義"
                                        + (f"（{p[2][:120]}）" if len(p) > 2 and p[2] else ""))
            xml_bad += 1
    # COM 路徑沒拿到定義的工作：欄位維持 None（不知道），不要被當成「否／沒有」
    for t in tasks.values():
        if t["src"] == "com" and t["def_error"] is None and t["hidden"] is None:
            t["def_error"] = "沒收到工作定義"
    errs = list(winrm_collector.parse_errors(text))
    if xml_bad:
        errs.append({"item": "taskxml", "kind": "DENIED", "message": f"{xml_bad} tasks"})
    return {"tasks": list(tasks.values()), "errors": errs}


def explain_win_error(e: dict) -> str:
    label = WIN_ITEM_LABEL.get(e.get("item"), e.get("item") or "")
    if e.get("kind") in ("DENIED", "EMPTY"):
        extra = "（沒有報錯，但一筆都沒回——非管理員常見）" if e.get("kind") == "EMPTY" else ""
        cnt = f"（{e['message']}）" if e.get("item") == "taskxml" and e.get("message") else ""
        return f"{label}：權限不足（需 {WIN_PERM_NEED.get(e.get('item'), '系統管理員權限')}）{extra}{cnt}"
    return f"{label}：收集失敗（{(e.get('message') or '沒有訊息')[:160]}）"


def _result_text(code: int | None) -> str:
    if code is None:
        return ""
    if code in TASK_RESULT:
        return TASK_RESULT[code]
    return f"失敗（0x{code:X}）"


# ---------------------------------------------------------------------
# 給人看的：觸發程序／動作／安全性選項（工作排程器內容視窗那幾頁）
# ---------------------------------------------------------------------
#: 常見的知名 SID → 名稱（XML 寫 SID，Get-ScheduledTask 寫名稱；統一成名稱）
_WELL_KNOWN_SID = {"s-1-5-18": "SYSTEM", "s-1-5-19": "LOCAL SERVICE", "s-1-5-20": "NETWORK SERVICE",
                   "s-1-5-4": "INTERACTIVE", "s-1-5-11": "Authenticated Users", "s-1-1-0": "Everyone",
                   "s-1-5-32-544": "Administrators", "s-1-5-32-545": "Users", "s-1-5-6": "SERVICE",
                   "s-1-5-32-555": "Remote Desktop Users"}
_ACCOUNT_ALIAS = {"nt authority\\system": "SYSTEM", "nt authority\\local service": "LOCAL SERVICE",
                  "nt authority\\network service": "NETWORK SERVICE", "nt authority\\interactive": "INTERACTIVE",
                  "localsystem": "SYSTEM", "system": "SYSTEM", "interactive": "INTERACTIVE",
                  "builtin\\users": "Users", "builtin\\administrators": "Administrators"}
#: 登入方式：CIM 列舉名／XML 值／COM 數字 → 一種寫法
_LOGON_NORM = {"interactive": "interactive", "interactivetoken": "interactive", "group": "interactive",
               "password": "password", "s4u": "s4u", "serviceaccount": "service",
               "interactiveorpassword": "interactive_or_password",
               "interactivetokenorpassword": "interactive_or_password", "none": "none",
               "0": "none", "1": "password", "2": "s4u", "3": "interactive", "4": "interactive",
               "5": "service", "6": "interactive_or_password"}
LOGON_LABEL = {"interactive": "只有使用者登入時才執行",
               "password": "不論使用者登入與否均執行（已儲存密碼）",
               "s4u": "不論使用者登入與否均執行（不要儲存密碼）",
               "service": "不論使用者登入與否均執行（服務帳戶）",
               "interactive_or_password": "使用者登入時執行，否則用已儲存密碼",
               "none": "不需登入"}
_COMPAT_NORM = {"at": "at", "v1": "v1", "version 1.0": "v1", "version 1.1": "v1",
                "v2": "vista", "vista": "vista", "version 1.2": "vista",
                "v2_1": "win7", "win7": "win7", "version 1.3": "win7",
                "v2_2": "win8", "win8": "win8", "version 1.4": "win8",
                "v2_3": "win10", "win10": "win10", "version 1.5": "win10", "version 1.6": "win10"}
COMPAT_LABEL = {"at": "AT 指令相容", "v1": "Windows Server 2003、Windows XP 或 Windows 2000",
                "vista": "Windows Vista、Windows Server 2008", "win7": "Windows 7、Windows Server 2008 R2",
                "win8": "Windows 8、Windows Server 2012", "win10": "Windows 10、Windows Server 2016 以後"}
TRIGGER_LABEL = {"Logon": "登入時", "Boot": "開機時", "Idle": "閒置時", "Time": "一次", "Daily": "每天",
                 "Weekly": "每週", "Monthly": "每月", "MonthlyDOW": "每月", "": "每月", "Event": "事件",
                 "Registration": "建立／修改工作時", "SessionStateChange": "工作階段狀態變更"}
_SESSION_LABEL = {"1": "本機連線", "2": "本機中斷連線", "3": "遠端連線", "4": "遠端中斷連線",
                  "7": "鎖定工作站", "8": "解除鎖定工作站"}
#: 一般使用者可寫入的目錄（執行檔放這裡＝誰都能換掉它，排程以高權限跑就變提權）
_USER_WRITABLE = re.compile(
    r"^(%(userprofile|appdata|localappdata|temp|tmp|public|programdata)%|[a-z]:\\users\\|[a-z]:\\programdata\\|"
    r"[a-z]:\\windows\\temp\\|[a-z]:\\temp\\|[a-z]:\\tmp\\)|\\appdata\\", re.IGNORECASE)


def _dur_text(minutes: float | None) -> str:
    """分鐘 → 「3 小時 30 分」「1 天」「30 秒」。"""
    if minutes is None:
        return ""
    if minutes < 1:
        return f"{round(minutes * 60)} 秒"
    m = int(round(minutes))
    d, rem = divmod(m, 1440)
    h, mi = divmod(rem, 60)
    parts = [f"{d} 天" if d else "", f"{h} 小時" if h else "", f"{mi} 分" if mi else ""]
    return " ".join(x for x in parts if x)


def _date_text(v: str) -> str:
    """2032-05-02T12:05:00(+08:00) → 2032/5/2 12:05（照工作排程器的寫法）。"""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?", v or "")
    if not m:
        return v or ""
    y, mo, d, hh, mm = m.groups()
    return f"{int(y)}/{int(mo)}/{int(d)}" + (f" {hh}:{mm}" if hh else "")


def _event_text(sub: str) -> str:
    """事件訂閱 XML（已攤平）→ 「System 記錄，事件 1074」；看不懂就說「自訂事件篩選」。"""
    if not sub:
        return ""
    path = re.search(r"Path=['\"]([^'\"]+)['\"]", sub)
    ids = re.findall(r"EventID\s*=\s*(\d+)", sub)
    src = re.search(r"Provider\[@Name=['\"]([^'\"]+)['\"]", sub)
    bits = []
    if path:
        bits.append(f"{path.group(1)} 記錄")
    if src:
        bits.append(f"來源 {src.group(1)}")
    if ids:
        bits.append("事件 " + "、".join(dict.fromkeys(ids)))
    return "，".join(bits) or "自訂事件篩選"


def _short_cls(g: dict) -> str:
    return (g.get("cls") or "").replace("MSFT_Task", "").replace("Trigger", "")


def trigger_text(g: dict) -> str:
    """一個觸發 → 工作排程器「觸發程序」頁的詳細資料（白話）。
    例：「每天 23:00，到期 2032/5/2 12:05」「任何使用者登入時，之後每 3 小時 30 分重複」。"""
    cls = _short_cls(g)
    tod = _start_tod(g.get("start") or "")
    if cls == "Logon":
        head = f"{g['user']} 登入時" if g.get("user") else "任何使用者登入時"
    elif cls == "Boot":
        head = "開機時"
    elif cls == "Idle":
        head = "電腦閒置時"
    elif cls == "Registration":
        head = "建立或修改工作時"
    elif cls == "Event":
        ev = _event_text(g.get("subscription") or "")
        head = "發生事件時" + (f"（{ev}）" if ev else "")
    elif cls == "SessionStateChange":
        sc = _SESSION_LABEL.get(str(g.get("state_change") or ""), "工作階段狀態變更")
        head = f"{g['user']} {sc}時" if g.get("user") else f"任何使用者{sc}時"
    elif cls == "Time":
        head = f"一次：{_date_text(g.get('start') or '')}" if g.get("start") else "一次"
    elif cls == "Daily":
        n = int(g.get("days_interval") or 1)
        head = ("每天" if n <= 1 else f"每 {n} 天") + (f" {_hm(tod)}" if tod is not None else "")
    elif cls == "Weekly":
        days = _days_mask(int(g.get("days_of_week") or 0))
        n = int(g.get("weeks_interval") or 1)
        dd = "、".join(_WD[d] for d in days)
        head = (f"每 {n} 週的週{dd}" if n > 1 else f"每週{dd}") if dd else ("每週" if n <= 1 else f"每 {n} 週")
        head += f" {_hm(tod)}" if tod is not None else ""
    elif cls in ("", "Monthly", "MonthlyDOW"):
        head = _monthly_text(g, tod)
    else:
        head = g.get("cls") or "不明的觸發"
    tail = []
    delay = _iso_minutes(g.get("delay") or "")
    if delay:
        tail.append(f"延遲 {_dur_text(delay)}")
    rnd = _iso_minutes(g.get("random_delay") or "")
    if rnd:
        tail.append(f"隨機延遲最多 {_dur_text(rnd)}")
    rep = _iso_minutes(g.get("rep_interval") or "")
    if rep:
        dur = _iso_minutes(g.get("rep_duration") or "")
        tail.append(f"之後每 {_dur_text(rep)}重複" + (f"，持續 {_dur_text(dur)}" if dur else ""))
    if g.get("end"):
        tail.append(f"到期 {_date_text(g['end'])}")
    return "，".join([head] + tail)


def _account_name(v: str) -> str:
    s = (v or "").strip()
    if not s:
        return ""
    low = s.lower()
    return _WELL_KNOWN_SID.get(low) or _ACCOUNT_ALIAS.get(low) or s


def _highest(run_level: str | None) -> bool | None:
    s = (run_level or "").strip().lower()
    if s in ("highest", "highestavailable", "1"):
        return True
    if s in ("limited", "leastprivilege", "0"):
        return False
    return None


def action_view(a: dict) -> dict:
    exe = (a.get("exe") or "").strip()
    if exe:
        from service_inventory import unquoted_path
        return {"kind": "exec", "kind_label": "啟動程式", "exe": exe, "args": a.get("args") or "",
                "workdir": a.get("workdir") or "", "class_id": "",
                "unquoted": unquoted_path(exe), "user_writable": bool(_USER_WRITABLE.search(exe.strip('"')))}
    if a.get("class_id"):
        return {"kind": "com", "kind_label": "COM 處理常式", "exe": "", "args": a.get("args") or "",
                "workdir": "", "class_id": a["class_id"], "unquoted": False, "user_writable": False}
    return {"kind": "other", "kind_label": a.get("other") or "其他動作", "exe": "", "args": "",
            "workdir": "", "class_id": "", "unquoted": False, "user_writable": False}


def task_view(t: dict) -> dict:
    """一個工作（不論 CIM 或 COM 路徑）→ 畫面要的完整內容。兩條路徑給同樣的輸入要得出同樣的結果。
    值是 None＝不知道（舊版收集、或讀不到定義），畫面要講原因，不可以當成「否」。"""
    known = t.get("def_error") is None
    why = t.get("def_error") or ("舊版收集沒有這一項，請重收一次" if t.get("src") is None else "")
    trg = []
    for i, g in enumerate(t.get("triggers") or [], 1):
        c = _short_cls(g)
        on = _bool(g.get("enabled", "true")) is not False
        trg.append({"no": i, "type": c, "type_label": TRIGGER_LABEL.get(c, g.get("cls") or "不明"),
                    "text": trigger_text(g), "enabled": on, "enabled_label": "已啟用" if on else "已停用"})
    acts = [{"no": i, **action_view(a)} for i, a in enumerate(t.get("actions") or [], 1)]
    acct = _account_name(t.get("user") or "") or _account_name(t.get("group") or "")
    raw_lt = (t.get("logon_type") or "").strip()
    lt = _LOGON_NORM.get(raw_lt.lower().replace("_", "")) if raw_lt else None
    if lt is None and not raw_lt and known and acct in ("SYSTEM", "LOCAL SERVICE", "NETWORK SERVICE"):
        lt = "service"
    compat_raw = t.get("compat")
    cp = _COMPAT_NORM.get(compat_raw.strip().lower()) if compat_raw else None
    code = t.get("last_result")
    info_ok = t.get("info_ok", code is not None)
    return {
        "general": {"name": t.get("name") or "", "path": t.get("path") or "\\",
                    "author": t.get("author") or "", "description": t.get("description") or ""},
        "triggers": trg, "actions": acts,
        "security": {"account": acct or None, "logon": lt,
                     "logon_label": LOGON_LABEL.get(lt) if lt else (raw_lt or None),
                     "highest": _highest(t.get("run_level")) if known else None,
                     "hidden": t.get("hidden") if known else None,
                     "compat": cp, "compat_label": COMPAT_LABEL.get(cp) if cp else (compat_raw or None)},
        "unknown_reason": why or None,
        "settings_enabled": t.get("settings_enabled"),
        "info_ok": bool(info_ok),
        "last_result_code": f"0x{code:X}" if code is not None else None,
        "last_result_text": _result_text(code) or None,
    }


def _action_summary(a: dict) -> str:
    if a["kind"] == "exec":
        return a["exe"] + (" " + a["args"] if a["args"] else "")
    if a["kind"] == "com":
        return f"COM 處理常式 {a['class_id']}"
    return a["kind_label"]


def windows_rows(parsed: dict) -> list[dict]:
    rows = []
    for t in parsed.get("tasks") or []:
        scheds = [describe_win_trigger(g) for g in t["triggers"]
                  if (g.get("enabled") or "True").lower() != "false"]
        disabled_trg = len(t["triggers"]) - len(scheds)
        if not scheds:
            if t.get("def_error"):
                sched = unknown(f"不知道（{t['def_error']}）")
            else:
                sched = _result("manual", "沒有啟用的觸發條件（只能手動執行）" if not disabled_trg
                                else f"觸發條件都已停用（{disabled_trg} 個）")
        else:
            best = min(scheds, key=lambda s: s["sort_key"])
            sched = dict(best)
            if len(scheds) > 1:
                sched["text"] = "；".join(s["text"] for s in scheds)
            sched["hours"] = sorted({h for s in scheds for h in s["hours"]})
            if best["kind"] == "interval":
                sched["hours"] = []
        raw = "；".join(f"{g['cls'].replace('MSFT_Task', '')} {g['start'] or ''}"
                       + (f" 每{g['rep_interval']}" if g.get("rep_interval") else "")
                       for g in t["triggers"]).strip()
        v = task_view(t)
        sec = v["security"]
        flags = []
        if any(a["unquoted"] for a in v["actions"]):
            flags.append("未加引號")
        if any(a["user_writable"] for a in v["actions"]):
            flags.append("使用者可寫目錄")
        rows.append({"source": t["path"] or "\\", "name": t["name"], "account": sec["account"] or "",
                     "run_level": t["run_level"], "state": STATE_LABEL.get(t["state"], t["state"]),
                     "raw": raw, "command": "；".join(_action_summary(a) for a in v["actions"]),
                     "sched": sched, "next_run": t["next_run"], "last_run": t["last_run"],
                     "last_result": _result_text(t["last_result"]),
                     "last_result_ok": t["last_result"] in (0, 0x41301, 0x41303, None),
                     "builtin": (t["path"] or "").lower().startswith("\\microsoft\\"),
                     "description": t["description"],
                     # 2026-10-21：Windows 主表（觸發摘要／動作摘要／最高權限）＋點列下鑽的完整內容
                     "trigger_summary": "；".join(x["text"] + ("（已停用）" if not x["enabled"] else "")
                                                 for x in v["triggers"]) or None,
                     "action_summary": "；".join(_action_summary(a) for a in v["actions"]) or None,
                     "highest": sec["highest"], "hidden": sec["hidden"],
                     "info_ok": v["info_ok"], "unknown_reason": v["unknown_reason"],
                     "flags": flags, "task": v})
    return rows


# =====================================================================
# 排序＋編號＋時段分布
# =====================================================================
def finalize(rows: list[dict]) -> list[dict]:
    """依預設規則排序、給固定編號，並把 sched 攤平成畫面用的欄位。"""
    rows = sorted(rows, key=lambda r: (r["sched"]["sort_key"], (r.get("name") or r.get("command") or "").lower()))
    out = []
    for i, r in enumerate(rows, 1):
        s = r.pop("sched")
        out.append({"no": i, **r, "kind": s["kind"], "kind_label": s["kind_label"],
                    "schedule": s["text"], "interval_min": s["interval_min"], "tod_min": s["tod_min"],
                    "hours": s["hours"], "sort_key": s["sort_key"]})
    return out


def hour_histogram(rows: list[dict]) -> list[int]:
    h = [0] * 24
    for r in rows:
        for x in r.get("hours") or []:
            if 0 <= x < 24:
                h[x] += 1
    return h


# =====================================================================
# 落庫、收集、讀取
# =====================================================================
def _ensure(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS host_schedule (
            asset_serial TEXT PRIMARY KEY, ip TEXT, platform TEXT,
            data_json TEXT, collected_at TEXT)""")


def collect_windows(conn, only_serial: str | None = None, runner=None,
                    trigger: str = "manual") -> dict:
    """Windows 排程（WinRM／PSRP）。回 post_onboard 看得懂的格式：ok／fail_detail／unsupported／skipped。"""
    import credential_store
    import manage_state
    import onboard_eligibility
    import winrm_collector

    q, prm = manage_state.collect_targets_sql(
        conn, only_serial,
        "SELECT asset_serial, ip, os, is_vm, device_model, hostname, asset_name, asset_purpose "
        "FROM hardware WHERE collect_ok = 1 AND ip IS NOT NULL AND ip != ''")
    targets = conn.execute(q, list(prm)).fetchall()
    ok, failed, unsupported, skipped = [], [], [], []
    if only_serial and not targets:
        why = manage_state.why_not_collectable(conn, only_serial)
        if why:
            skipped.append({"asset_serial": only_serial, "reason": why})
    _ensure(conn)
    for t in targets:
        ip, serial = t["ip"], t["asset_serial"]
        got = onboard_eligibility.judge_for_asset(conn, dict(t))
        if got and got[0] == "esxi":
            unsupported.append({"asset_serial": serial, "ip": ip,
                                "reason": "VMware 設備（ESXi／vCenter）不經 SSH／WinRM 收集"})
            continue
        platform = manage_state.collect_platform_of(conn, ip, t["os"], asset_serial=serial,
                                                    probe=lambda _h: None)
        if platform != "windows":
            unsupported.append({"asset_serial": serial, "ip": ip,
                                "reason": "Linux 排程讀 OS 重建資料（不另收）；AIX 尚未支援"})
            continue
        cred_name = None
        try:
            if runner is not None:
                raw = runner(ip, SCHEDULE_PS)
            else:
                import win_onboard

                # Windows 勾選才收（2026-10-22）：沒納管的不登入（第二道；第一道是只挑 collect_ok=1）
                blocked = win_onboard.collect_block(conn, ip, serial)
                if blocked:
                    failed.append({"asset_serial": serial, "ip": ip, "error": blocked})
                    continue
                cred_name = credential_store.pick_for_host(conn, ip, kind="winrm")
                if not cred_name:
                    failed.append({"asset_serial": serial, "ip": ip,
                                   "error": "沒有可用的收集憑證——請先在系統設定新增 WinRM 服務帳號"})
                    continue
                cred = credential_store.get_for_use(conn, cred_name)
                if cred is None:
                    failed.append({"asset_serial": serial, "ip": ip,
                                   "error": f"憑證「{cred_name}」解不開（加密金鑰可能已更換），請重新設定"})
                    continue
                username, password = cred
                try:
                    raw = winrm_collector._run(ip, username, password, SCHEDULE_PS, 120)
                finally:
                    password = None  # noqa: F841 - 明確結束密碼生命週期
        except Exception as exc:  # noqa: BLE001 - 原因如實回報（含開通指引），單台失敗不中斷
            msg = str(exc)[:300]
            if "actively refused" in msg or "timed out" in msg or "Max retries" in msg:
                msg = f"{msg}｜{winrm_collector.enable_hint()}"
            if cred_name:
                credential_store.audit_use(conn, cred_name, ip, False, msg)
            failed.append({"asset_serial": serial, "ip": ip, "error": msg})
            continue
        if cred_name:
            credential_store.audit_use(conn, cred_name, ip, True, "排程盤點成功")
        if "SRC=schtasks" not in (raw or ""):
            failed.append({"asset_serial": serial, "ip": ip,
                           "error": "收不到排程輸出（WinRM 連上了但腳本沒有回資料）"})
            continue
        parsed = parse_windows_schedule(raw)
        notes = [explain_win_error(e) for e in parsed["errors"]]
        if not parsed["tasks"]:
            # Windows 一定有內建排程（\Microsoft\Windows\…）；一筆都沒有＝沒收到，不是沒有
            failed.append({"asset_serial": serial, "ip": ip,
                           "error": ("；".join(notes) or "排程清單是空的（Windows 一定有內建排程，這是收集失敗）")[:400]})
            continue
        conn.execute(
            "INSERT INTO host_schedule (asset_serial, ip, platform, data_json, collected_at) "
            "VALUES (?,?,?,?, datetime('now','localtime')) ON CONFLICT(asset_serial) DO UPDATE SET "
            "ip=excluded.ip, platform=excluded.platform, data_json=excluded.data_json, "
            "collected_at=excluded.collected_at",
            (serial, ip, "windows", json.dumps({"tasks": parsed["tasks"], "notes": notes},
                                                ensure_ascii=False)))
        conn.commit()
        ok.append({"asset_serial": serial, "ip": ip, "tasks": len(parsed["tasks"]), "notes": notes})
    return {"total": len(targets), "ok": len(ok), "failed": len(failed),
            "unsupported": len(unsupported), "ok_detail": ok[:50],
            "hosts": [{"asset_serial": o["asset_serial"], "notes": o["notes"]} for o in ok],
            "fail_detail": failed[:20], "unsupported_detail": unsupported[:20], "skipped": skipped}


def _row_for(conn, table: str, cols: str, asset_serial: str):
    """先找這筆；沒有就找同一台（machine_key）的其他登記（收集器存在 collect_ok=1 那筆底下）。"""
    import sqlite3

    try:
        row = conn.execute(f"SELECT {cols} FROM {table} WHERE asset_serial = ?",
                           (asset_serial,)).fetchone()
        if row:
            return row
        import manage_state
        sibs = [x for x in manage_state.expand_to_machines(conn, [asset_serial]) if x != asset_serial]
        if not sibs:
            return None
        ph = ",".join("?" for _ in sibs)
        return conn.execute(f"SELECT {cols} FROM {table} WHERE asset_serial IN ({ph}) "
                            "ORDER BY collected_at DESC LIMIT 1", sibs).fetchone()
    except sqlite3.Error:
        return None


def detail_for(conn, asset_serial: str) -> dict:
    """資產頁「排程」分頁的資料。"""
    import manage_state

    hw = conn.execute("SELECT ip, os FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
    platform = None
    if hw is not None:
        try:
            platform = manage_state.collect_platform_of(conn, hw["ip"] or "", hw["os"],
                                                        asset_serial=asset_serial, probe=lambda _h: None)
        except Exception:  # noqa: BLE001 - 判不出平台就走「兩種資料都找」，畫面照講缺什麼
            platform = None
    base = {"asset_serial": asset_serial, "platform": platform, "rows": [], "notes": [],
            "hour_hist": [0] * 24, "collected_at": None, "source": None, "empty_reason": None}

    if platform != "windows":
        rb = _row_for(conn, "host_rebuild", "data_json, collected_at, platform", asset_serial)
        if rb and rb["platform"] == "windows":
            rb, platform = None, "windows"      # Windows 的 OS 重建資料（WinRM 版）不是 crontab，排程另收
        if rb:
            try:
                data = json.loads(rb["data_json"] or "{}")
            except (ValueError, TypeError):
                data = {}
            rows, notes = linux_rows(data)
            rows = finalize(rows)
            base.update(platform=platform or rb["platform"], rows=rows, notes=notes,
                        hour_hist=hour_histogram(rows), collected_at=rb["collected_at"],
                        source="OS 重建資料")
            if not rows:
                base["empty_reason"] = "OS 重建資料裡沒有任何排程（crontab／timer 都是空的）"
            return base
        if platform == "windows":
            pass                                # 往下走 Windows 的排程資料
        elif platform in ("linux", None):
            base["empty_reason"] = "先收 OS 重建：排程讀 OS 重建資料，這台還沒收過（到「OS 重建」分頁按收集，或按「收集全部」）"
            if platform == "linux":
                return base
        else:
            base["empty_reason"] = f"{platform} 尚未支援排程盤點（目前 Linux、Windows）"
            return base

    _ensure(conn)
    ws = _row_for(conn, "host_schedule", "data_json, collected_at", asset_serial)
    if not ws:
        if platform == "windows":
            base["empty_reason"] = "這台還沒收過排程：按「收集排程」或「收集全部」"
        return base
    try:
        data = json.loads(ws["data_json"] or "{}")
    except (ValueError, TypeError):
        data = {}
    rows = finalize(windows_rows(data))
    notes = list(data.get("notes") or [])
    if any(t.get("src") is None for t in data.get("tasks") or []):
        notes.append("這份排程是舊版收集的：觸發的到期／重複細節、隱藏、設定相容性不知道——按「收集排程」重收一次")
    base.update(platform="windows", rows=rows, notes=notes,
                hour_hist=hour_histogram(rows), collected_at=ws["collected_at"], source="WinRM 工作排程器")
    return base
