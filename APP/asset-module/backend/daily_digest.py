"""每日變化報告＋早上摘要信（v1.500.0，2026-10-23）。

使用者要的是「系統每天主動來找他」，不是等他打開：
  * 每晚存一份「每台機器的關鍵事實」快照（daily_snapshot），以 machine_key 為鍵。
  * 今天的快照對前一份快照做差異 → 分類（新增／消失／位置／IP／主機名／系統歸屬／OS／
    存在可信度下降／收集中斷／軟體服務帳號數大幅變化／開機過久／儲存空間跌破門檻）。
  * 早上寄一封短信：每類幾筆（對比昨天）、新風險、收集中斷的網段，每行帶連結。

設計要點：
1. **同一台＝system_stats.machine_key**（透過 existence.compute 的分組），不另寫去重。
   退役的機器（ret: 前綴）存成同一把鍵、retired=1：現役→退役會被分到「消失（退役）」，
   不會變成「一台消失＋一台新增」。
2. **差異是純函式**（diff_rows）：同一份計算給頁面、給信、給 verify_numbers，
   所以「分頁上的數字＝點進去的筆數」「N 日新增＝反向比對的消失」都可以機械驗證。
3. **寄信沿用 vuln_notify 的 SMTP 設定與寄送函式**（免認證內部 relay），不另做一套。
   照它的鐵律：每一次嘗試都記（含 SMTP 回應）；**只有寄成功才算這期寄過**，
   失敗一小時後自己補——寧可重複寄也不可以假裝寄過。
4. 寄信預設是關的；收件人沒填、網址沒填、SMTP 沒填都不寄，並指名缺哪一項。
"""
from __future__ import annotations

import ipaddress
import json
import re
import sqlite3
from datetime import date, datetime, timedelta

SETTING_KEY = "daily_digest"

DEFAULTS = {
    "snapshot_time": "05:30",     # 夜間掃描（01:00）、CI 圖譜（03:00）之後
    "retain_days": 90,
    "gap_days": 7,                # 收集中斷：N 天沒有成功收集
    "count_change_pct": 30,       # 軟體／服務／帳號數變化 ≥ 這個百分比
    "count_change_min": 10,       # …而且變化筆數 ≥ 這個數（小機器 2→4 不算大幅）
    "uptime_days": 365,           # 開機超過 N 天＝風險
    "mail_enabled": False,
    "mail_time": "08:00",
    "recipients": "",
    "base_url": "",
    "subject_prefix": "",
}

#: 分頁順序＝信裡的順序。key 不可以改（網址 ?tab= 用它）。
CATEGORIES = (
    ("added", "新增"),
    ("missing", "消失"),
    ("location", "位置改變"),
    ("ip", "IP 改變"),
    ("hostname", "主機名改變"),
    ("system", "系統歸屬改變"),
    ("os", "OS 改變"),
    ("exist_down", "存在可信度下降"),
    ("collect_gap", "收集中斷"),
    ("count_change", "軟體／服務／帳號數大幅變化"),
    ("uptime_risk", "開機過久（本次新增）"),
    ("storage_low", "儲存空間跌破門檻（本次新增）"),
)
CATEGORY_LABEL = dict(CATEGORIES)

#: 快照欄位（不含 snap_date／mkey）
SNAP_FIELDS = ("retired", "hostname", "ips", "serials", "room", "rack", "u", "asset_status",
               "exist_level", "exist_score", "os", "api_ids", "collect_status", "last_collect",
               "sw_count", "svc_count", "acct_count", "segment", "uptime_days")

SNAP_SQL = """
CREATE TABLE IF NOT EXISTS daily_snapshot (
    snap_date      TEXT NOT NULL,         -- YYYY-MM-DD
    mkey           TEXT NOT NULL,         -- system_stats.machine_key（退役不帶 ret:，用 retired 標）
    retired        INTEGER DEFAULT 0,
    hostname       TEXT,
    ips            TEXT,                  -- 逗號分隔、排序過
    serials        TEXT,                  -- 這台的資產序號（逗號分隔）
    room           TEXT,
    rack           TEXT,
    u              TEXT,
    asset_status   TEXT,
    exist_level    TEXT,
    exist_score    INTEGER,
    os             TEXT,
    api_ids        TEXT,                  -- 逗號分隔、排序過
    collect_status TEXT,                  -- fresh／stale／NULL（從沒連上過）
    last_collect   TEXT,
    sw_count       INTEGER,
    svc_count      INTEGER,
    acct_count     INTEGER,
    segment        TEXT,                  -- 所屬網段（網段配置表；沒登記用 /24）
    uptime_days    INTEGER,               -- 收集當時的開機天數（host_spec）
    PRIMARY KEY (snap_date, mkey)
)
"""
RUN_SQL = """
CREATE TABLE IF NOT EXISTS daily_snapshot_run (
    snap_date         TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    trigger           TEXT,               -- schedule／manual
    machines          INTEGER,
    storage_threshold REAL,
    storage_under     TEXT                -- JSON：[{id,name,free_pct}] 低於門檻的 datastore
)
"""
MAIL_LOG_SQL = """
CREATE TABLE IF NOT EXISTS digest_mail_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date   TEXT NOT NULL,
    kind       TEXT NOT NULL,             -- scheduled／test
    sent_at    TEXT NOT NULL,
    recipients TEXT,
    subject    TEXT,
    status     TEXT,                      -- sent／failed
    detail     TEXT,                      -- SMTP 回應或錯誤原文
    smtp_host  TEXT,
    mail_from  TEXT
)
"""


def ensure(conn: sqlite3.Connection) -> None:
    conn.execute(SNAP_SQL)
    conn.execute(RUN_SQL)
    conn.execute(MAIL_LOG_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dsnap_key ON daily_snapshot(mkey, snap_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dmail_date ON digest_mail_log(run_date, kind)")


def tables_exist(conn: sqlite3.Connection) -> bool:
    """唯讀檢查（verify_numbers 用：不可以在正式庫上建表）。"""
    r = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                     "AND name IN ('daily_snapshot','daily_snapshot_run')").fetchone()
    return bool(r) and r[0] == 2


# ── 設定 ───────────────────────────────────────────────────────────────
def load_settings(conn: sqlite3.Connection) -> dict:
    """讀設定＋預設值。設定壞掉（不是 JSON）要講出來，不可以靜靜退回預設——
    那會讓「設定被寫壞」看起來像「從沒設定過」。"""
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    cfg = dict(DEFAULTS)
    if row and row[0]:
        try:
            saved = json.loads(row[0]) or {}
        except (ValueError, TypeError) as exc:
            raise ValueError(f"每日摘要設定讀不出來（內容壞掉，不是沒設定）：{exc}") from exc
        cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
    return cfg


def save_settings(conn: sqlite3.Connection, cfg: dict) -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULTS and v is not None})
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (SETTING_KEY, json.dumps(out, ensure_ascii=False)))
    conn.commit()
    return out


def _int(cfg: dict, k: str) -> int:
    try:
        return int(cfg.get(k))
    except (TypeError, ValueError):
        return int(DEFAULTS[k])


def hhmm(raw, default: str) -> tuple[int, int]:
    """'HH:MM' → (h, m)。打錯就用預設——不可以因為時間格式打錯就整個不跑。"""
    for s in (raw, default):
        try:
            hh, mm = str(s).strip().split(":")
            h, m = int(hh), int(mm)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h, m
        except (ValueError, AttributeError):
            continue
    return 0, 0


def recipients_of(cfg: dict) -> list[str]:
    raw = cfg.get("recipients") or ""
    parts = raw if isinstance(raw, (list, tuple)) else \
        str(raw).replace("；", ",").replace(";", ",").replace("、", ",").replace("\n", ",").split(",")
    out, seen = [], set()
    for x in parts:
        a = str(x).strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


def mail_missing(conn: sqlite3.Connection, cfg: dict) -> list[str]:
    """寄信還缺什麼（指名）。SMTP 主機／埠／寄件者沿用 vuln_notify 那份設定。"""
    import vuln_notify

    miss = []
    try:
        smtp = vuln_notify.load_settings(conn)
        miss += [f"SMTP：{x}" for x in vuln_notify.check_settings(smtp)]
    except vuln_notify.SettingsCorrupt as exc:
        miss.append(f"SMTP 設定壞掉：{exc}")
    if not recipients_of(cfg):
        miss.append("收件人")
    if not str(cfg.get("base_url") or "").strip():
        miss.append("系統網址（信裡的連結用）")
    return miss


# ── 小工具 ─────────────────────────────────────────────────────────────
_UP_UNIT = re.compile(r"(\d+)\s*(years?|weeks?|days?)", re.I)


def parse_uptime_days(text) -> int | None:
    """host_spec 收到的開機時間文字 → 天數。看不懂回 None（不知道≠0 天）。

    認得：`up 2 years, 3 weeks, 1 day`（uptime -p）、`up 120 days, 3:45`（AIX／舊 uptime）、
    `/proc/uptime` 的秒數、Windows 收集器的 `up 120 days, 5 hours（開機 …）`。"""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s+\d+(?:\.\d+)?\s*$", s)
    if m:
        return int(float(m.group(1)) // 86400)
    total, found = 0, False
    for n, unit in _UP_UNIT.findall(s):
        found = True
        u = unit.lower()
        total += int(n) * (365 if u.startswith("year") else 7 if u.startswith("week") else 1)
    if found:
        return total
    if re.search(r"\bup\b", s, re.I):
        return 0                        # up 3:45／up 5 hours／up 10 min
    return None


class SegmentIndex:
    """IP → 所屬網段（網段配置表最小的那段；沒登記就 x.x.x.0/24（未登記））。"""

    def __init__(self, conn: sqlite3.Connection):
        self.segs: list[tuple[int, int, str]] = []
        try:
            for r in conn.execute("SELECT net_start, net_end, cidr, raw_cidr FROM network_segment "
                                  "WHERE net_start IS NOT NULL AND net_end IS NOT NULL"):
                self.segs.append((int(r[0]), int(r[1]), r[2] or r[3]))
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            # 舊庫沒有網段表：全部退回 /24，畫面標「未登記」
        self.segs.sort(key=lambda x: x[1] - x[0])

    def of(self, ip: str | None) -> str | None:
        try:
            a = ipaddress.ip_address(str(ip or "").strip())
        except ValueError:
            return None
        if a.version != 4:
            return None
        n = int(a)
        for lo, hi, name in self.segs:
            if lo <= n <= hi:
                return name
        return str(ipaddress.ip_network(f"{a}/24", strict=False)) + "（未登記）"


def _ip_counts(conn: sqlite3.Connection, table: str) -> dict[str, int]:
    try:
        return {r[0]: r[1] for r in conn.execute(
            f"SELECT ip, COUNT(*) FROM {table} WHERE gone_at IS NULL GROUP BY ip")}
    except sqlite3.OperationalError as exc:
        if "no such" not in str(exc):
            raise
        return {}          # 舊庫沒有這張表＝沒收過這種資料；快照記 NULL 不記 0（見 build_rows）


# ── 快照 ───────────────────────────────────────────────────────────────
def build_rows(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    """算出「現在每台機器的關鍵事實」。台＝existence.compute 的分組（machine_key）。"""
    import existence
    import manage_state as ms
    import rack_layout
    import system_stats

    now = now or datetime.now()
    ex = existence.compute(conn, now=now)
    hw = {r["asset_serial"]: dict(r) for r in conn.execute(
        "SELECT asset_serial, hostname, ip, physical_location, rack_no, asset_status, api_id, os, "
        "is_vm, device_model FROM hardware")}
    have = {t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    sw = _ip_counts(conn, "host_package") if "host_package" in have else None
    svc = _ip_counts(conn, "host_service") if "host_service" in have else None
    acct = _ip_counts(conn, "host_account") if "host_account" in have else None
    collected_ips = set()
    for d in (sw, svc, acct):
        collected_ips |= set(d or {})
    uptime: dict[str, tuple[int | None, str | None]] = {}
    if "host_spec" in have:
        for r in conn.execute("SELECT asset_serial, spec_json, collected_at FROM host_spec"):
            try:
                spec = json.loads(r[1]) if r[1] else {}
            except (ValueError, TypeError):
                spec = {}        # 規格 JSON 壞掉＝開機天數不知道（記 NULL），不擋整份快照
            uptime[r[0]] = (parse_uptime_days(spec.get("uptime")), r[2])
    segs = SegmentIndex(conn)

    out: dict[str, dict] = {}
    for m in ex["machines"]:
        key = m["key"]
        retired = key.startswith("ret:")
        base = key[4:] if retired else key
        if retired and base in out and not out[base]["retired"]:
            continue                       # 同一把鍵現役優先；退役那組是備查
        rows = [hw[s] for s in m["serials"] if s in hw]
        ips: list[str] = []
        for r in rows:
            for ip in ms.ips_of(r["ip"]):
                if ip not in ips:
                    ips.append(ip)
        for ip in ms.ips_of(m.get("ip")):
            if ip not in ips:
                ips.append(ip)
        rep = rows[0] if rows else {}
        room = rack = u = None
        if rep:
            room = system_stats.normalize_location(rep.get("physical_location"))
            pr = rack_layout.parse_rack(rep.get("rack_no"))
            if pr["kind"] == "vm":
                rack = "VM"
            elif pr["kind"] == "rack":
                rack = pr["rack"]
                if pr["u_start"]:
                    u = (f"{pr['u_start']}" if pr["u_start"] == pr["u_end"]
                         else f"{pr['u_start']}-{pr['u_end']}")
            elif pr["kind"] == "unparsed":
                rack = pr["raw"]
        apis = sorted({(r.get("api_id") or "").strip() for r in rows if (r.get("api_id") or "").strip()})

        def _sum(d):
            if d is None or not any(ip in collected_ips for ip in ips):
                return None                # 沒收過＝不知道，不是 0
            return sum(d.get(ip, 0) for ip in ips)

        ups = [uptime[s] for s in m["serials"] if s in uptime and uptime[s][0] is not None]
        up = max(ups, key=lambda x: x[1] or "")[0] if ups else None
        out[base] = {
            "mkey": base, "retired": 1 if retired else 0,
            "hostname": m.get("hostname"),
            "ips": ",".join(sorted(ips, key=_ip_sort)),
            "serials": ",".join(s for s in m["serials"] if s),
            "room": room, "rack": rack, "u": u,
            "asset_status": rep.get("asset_status") if rep else None,
            "exist_level": m["level"], "exist_score": int(m["score"]),
            "os": m.get("os"),
            "api_ids": ",".join(apis),
            "collect_status": m.get("collect"),
            "last_collect": (m.get("last_seen") or {}).get("collect"),
            "sw_count": _sum(sw), "svc_count": _sum(svc), "acct_count": _sum(acct),
            "segment": segs.of(ips[0]) if ips else None,
            "uptime_days": up,
        }
    return list(out.values())


def _ip_sort(ip: str):
    try:
        return (0, int(ipaddress.ip_address(ip)))
    except ValueError:
        return (1, 0)


def storage_under(conn: sqlite3.Connection) -> tuple[float | None, list[dict]]:
    """低於剩餘門檻的 datastore（儲存容量模組在才有）。回 (門檻, 清單)。"""
    try:
        import storage_capacity
    except ImportError:
        return None, []        # 沒有儲存容量模組的版本：這一行信不出現
    ds = storage_capacity.datastores(conn)
    th = ds["threshold_pct"]
    return th, [{"id": v["id"], "name": v["name"], "free_pct": v["free_pct"]}
                for v in ds["volumes"] if v["free_pct"] is not None and v["free_pct"] < th]


def write_snapshot(conn: sqlite3.Connection, now: datetime | None = None,
                   trigger: str = "manual", cfg: dict | None = None) -> dict:
    """寫今天這份快照（同一天重寫＝覆蓋），順便清掉超過保留天數的舊快照。"""
    ensure(conn)
    now = now or datetime.now()
    cfg = cfg or load_settings(conn)
    day = now.strftime("%Y-%m-%d")
    rows = build_rows(conn, now)
    th, under = storage_under(conn)
    conn.execute("DELETE FROM daily_snapshot WHERE snap_date = ?", (day,))
    cols = ("snap_date", "mkey") + SNAP_FIELDS
    conn.executemany(
        f"INSERT INTO daily_snapshot ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [(day, r["mkey"], *[r[f] for f in SNAP_FIELDS]) for r in rows])
    conn.execute(
        "INSERT INTO daily_snapshot_run (snap_date, created_at, trigger, machines, storage_threshold, "
        "storage_under) VALUES (?,?,?,?,?,?) ON CONFLICT(snap_date) DO UPDATE SET "
        "created_at=excluded.created_at, trigger=excluded.trigger, machines=excluded.machines, "
        "storage_threshold=excluded.storage_threshold, storage_under=excluded.storage_under",
        (day, now.strftime("%Y-%m-%d %H:%M:%S"), trigger, len(rows), th,
         json.dumps(under, ensure_ascii=False)))
    purged = purge(conn, now.date(), _int(cfg, "retain_days"))
    conn.commit()
    return {"snap_date": day, "machines": len(rows),
            "active": sum(1 for r in rows if not r["retired"]), "purged_days": purged}


def purge(conn: sqlite3.Connection, today: date, retain_days: int) -> int:
    """刪掉超過保留天數的快照。回刪掉幾天。保留天數 < 2 當 2（差異至少要兩天）。"""
    ensure(conn)
    cut = (today - timedelta(days=max(int(retain_days), 2))).isoformat()
    n = conn.execute("SELECT COUNT(*) FROM daily_snapshot_run WHERE snap_date < ?", (cut,)).fetchone()[0]
    conn.execute("DELETE FROM daily_snapshot WHERE snap_date < ?", (cut,))
    conn.execute("DELETE FROM daily_snapshot_run WHERE snap_date < ?", (cut,))
    return int(n)


def dates(conn: sqlite3.Connection) -> list[str]:
    if not tables_exist(conn):
        return []
    return [r[0] for r in conn.execute("SELECT snap_date FROM daily_snapshot_run ORDER BY snap_date DESC")]


def load_snapshot(conn: sqlite3.Connection, day: str) -> dict[str, dict]:
    cols = ("mkey",) + SNAP_FIELDS
    return {r[0]: dict(zip(cols, r)) for r in conn.execute(
        f"SELECT {','.join(cols)} FROM daily_snapshot WHERE snap_date = ?", (day,))}


def load_run(conn: sqlite3.Connection, day: str | None) -> dict | None:
    if not day:
        return None
    r = conn.execute("SELECT * FROM daily_snapshot_run WHERE snap_date = ?", (day,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["storage_under"] = json.loads(d.get("storage_under") or "[]")
    except (ValueError, TypeError):
        d["storage_under"] = []     # 舊格式壞掉：當作沒有低於門檻的（畫面另顯示快照時間可追）
    return d


# ── 差異（純函式）──────────────────────────────────────────────────────
def _split(v) -> list[str]:
    return [x for x in str(v or "").split(",") if x]


def _brief(r: dict | None) -> dict:
    if not r:
        return {}
    return {"hostname": r.get("hostname"), "ips": r.get("ips"), "serials": r.get("serials"),
            "serial": (_split(r.get("serials")) or [None])[0], "api_ids": r.get("api_ids"),
            "segment": r.get("segment")}


def _loc(r: dict) -> str:
    parts = [r.get("room") or "未填", r.get("rack") or "—"]
    if r.get("u"):
        parts.append(f"{r['u']}U")
    return " / ".join(parts)


def is_gap(r: dict, cutoff: str) -> bool:
    """收集中斷：連上過（collect_status 有值）但最後一次成功收集早於 cutoff（或時間不明）。"""
    if r.get("retired") or not r.get("collect_status"):
        return False
    lc = (r.get("last_collect") or "")[:10]
    return (not lc) or lc < cutoff


def big_change(a, b, pct: int, min_abs: int) -> bool:
    if a is None or b is None:
        return False
    d = abs(int(b) - int(a))
    if d < min_abs:
        return False
    return int(a) == 0 or d * 100.0 / int(a) >= pct


def diff_rows(prev: dict[str, dict] | None, cur: dict[str, dict], cfg: dict, day: str,
              level_label: dict | None = None, prev_storage: list | None = None,
              cur_storage: list | None = None) -> dict:
    """prev／cur：{mkey: 快照列}。prev=None＝沒有前一份（只算狀態類：收集中斷）。

    回 {"items": {cat: [列]}, "segments": [...]}。每類的筆數＝len(items[cat])（加總＝下鑽）。"""
    level_label = level_label or {}
    items: dict[str, list] = {k: [] for k, _ in CATEGORIES}
    pct, mn = _int(cfg, "count_change_pct"), _int(cfg, "count_change_min")
    up_th = _int(cfg, "uptime_days")
    cutoff = (date.fromisoformat(day) - timedelta(days=_int(cfg, "gap_days"))).isoformat()
    act_cur = {k for k, r in cur.items() if not r.get("retired")}

    if prev is not None:
        act_prev = {k for k, r in prev.items() if not r.get("retired")}
        new_keys, gone_keys = act_cur - act_prev, act_prev - act_cur
        # 改主機名／改 IP 會讓 machine_key 變掉（鍵本身就含主機名／IP）。同一筆資產序號
        # 一邊「新增」一邊「消失」＝同一台換了名字，配成一對、歸到主機名／IP 改變，
        # 不然改一個名字會變成「新增 1＋消失 1」。配對順序跟方向無關（排序過的鍵對），
        # 反向比對配出來的是同一批對，「N 日新增＝反向消失」的對稱性不會被破壞。
        cands = []
        for a in new_keys:
            sa = set(_split(cur[a].get("serials")))
            for g in gone_keys:
                if sa & set(_split(prev[g].get("serials"))):
                    cands.append(tuple(sorted((a, g))) + (a, g))
        pairs: list[tuple[str, str]] = []
        used: set[str] = set()
        for _x, _y, a, g in sorted(cands):
            if a in used or g in used:
                continue
            used |= {a, g}
            pairs.append((g, a))
        for k in sorted(new_keys - used):
            r = cur[k]
            items["added"].append({"key": k, **_brief(r), "before": None,
                                   "after": f"{r.get('os') or '—'}｜{_loc(r)}",
                                   "detail": "前一份是退役" if k in prev else "前一份沒有這台"})
        for k in sorted(gone_keys - used):
            p = prev[k]
            why = "退役" if (k in cur and cur[k].get("retired")) else "不見"
            items["missing"].append({"key": k, **_brief(p), "before": f"{p.get('os') or '—'}｜{_loc(p)}",
                                     "after": None, "detail": why,
                                     "asset_status": cur[k].get("asset_status") if k in cur else None})
        same = [(k, k) for k in sorted(act_cur & act_prev)] + sorted(pairs, key=lambda x: x[1])
        for pk, k in same:
            p, r = prev[pk], cur[k]
            b = _brief(r)
            if _loc(p) != _loc(r):
                items["location"].append({"key": k, **b, "before": _loc(p), "after": _loc(r)})
            if set(_split(p.get("ips"))) != set(_split(r.get("ips"))):
                items["ip"].append({"key": k, **b, "before": p.get("ips") or "—", "after": r.get("ips") or "—"})
            if (p.get("hostname") or "").lower() != (r.get("hostname") or "").lower():
                items["hostname"].append({"key": k, **b, "before": p.get("hostname") or "—",
                                          "after": r.get("hostname") or "—"})
            if set(_split(p.get("api_ids"))) != set(_split(r.get("api_ids"))):
                items["system"].append({"key": k, **b, "before": p.get("api_ids") or "（無）",
                                        "after": r.get("api_ids") or "（無）"})
            if (p.get("os") or "") != (r.get("os") or ""):
                items["os"].append({"key": k, **b, "before": p.get("os") or "—", "after": r.get("os") or "—"})
            if (r.get("exist_score") or 0) < (p.get("exist_score") or 0):
                items["exist_down"].append({
                    "key": k, **b,
                    "before": level_label.get(p.get("exist_level"), p.get("exist_level")),
                    "after": level_label.get(r.get("exist_level"), r.get("exist_level")),
                    "detail": f"{p.get('exist_score')} → {r.get('exist_score')}"})
            changed = []
            for f, lab in (("sw_count", "軟體"), ("svc_count", "服務"), ("acct_count", "帳號")):
                if big_change(p.get(f), r.get(f), pct, mn):
                    changed.append(f"{lab} {p.get(f)}→{r.get(f)}")
            if changed:
                items["count_change"].append({"key": k, **b, "before": None, "after": None,
                                              "detail": "、".join(changed)})
            pu, ru = p.get("uptime_days"), r.get("uptime_days")
            if ru is not None and ru > up_th and not (pu is not None and pu > up_th):
                items["uptime_risk"].append({"key": k, **b, "before": pu, "after": ru,
                                             "detail": f"開機 {ru} 天（門檻 {up_th}）"})
        prev_ids = {s["id"] for s in (prev_storage or [])}
        for s in cur_storage or []:
            if s["id"] not in prev_ids:
                items["storage_low"].append({"key": s["id"], "hostname": s["name"], "ips": None,
                                             "serial": None, "before": None,
                                             "after": f"剩 {s['free_pct']}%", "detail": "新跌破門檻"})

    # 收集中斷（狀態類：今天的快照就算得出來）＋依網段分組
    segs: dict[str, dict] = {}
    for k in sorted(act_cur):
        r = cur[k]
        if not r.get("collect_status"):
            continue                       # 從沒納管過的不算「中斷」
        seg = r.get("segment") or "（沒有 IP）"
        g = segs.setdefault(seg, {"segment": seg, "collected": 0, "gap": 0, "items": []})
        g["collected"] += 1
        if is_gap(r, cutoff):
            p = (prev or {}).get(k)
            it = {"key": k, **_brief(r), "before": None, "after": r.get("last_collect") or "時間不明",
                  "detail": seg, "new": bool(p) and not is_gap(p, cutoff)}
            items["collect_gap"].append(it)
            g["gap"] += 1
            g["items"].append(k)
    seg_out = []
    for g in segs.values():
        if not g["gap"]:
            continue
        g["whole"] = g["gap"] == g["collected"]
        seg_out.append(g)
    seg_out.sort(key=lambda g: (not g["whole"], -g["gap"], g["segment"]))
    return {"items": items, "segments": seg_out, "cutoff": cutoff}


def counts_of(items: dict) -> dict[str, int]:
    return {k: len(items.get(k) or []) for k, _ in CATEGORIES}


def _level_labels() -> dict:
    import existence

    return {k: v["label"] for k, v in existence.load_rules()["levels"].items()}


def diff_for(conn: sqlite3.Connection, day: str | None = None, cfg: dict | None = None) -> dict:
    """某一天的變化報告（沒給日期＝最新一份）。prev＝早於這天的最近一份快照。"""
    cfg = cfg or load_settings(conn)
    ds = dates(conn)
    base = {"dates": ds, "categories": [{"key": k, "label": lab} for k, lab in CATEGORIES],
            "settings": {k: cfg[k] for k in ("gap_days", "count_change_pct", "count_change_min",
                                              "uptime_days", "retain_days")}}
    if not ds:
        return {**base, "date": None, "prev_date": None, "items": {k: [] for k, _ in CATEGORIES},
                "counts": {k: 0 for k, _ in CATEGORIES}, "segments": [], "reason": "還沒有任何快照"}
    day = day or ds[0]
    if day not in ds:
        raise ValueError(f"{day} 沒有快照；有快照的日期：{'、'.join(ds[:10])}")
    older = [d for d in ds if d < day]
    prev_day = older[0] if older else None
    cur = load_snapshot(conn, day)
    prev = load_snapshot(conn, prev_day) if prev_day else None
    run_c, run_p = load_run(conn, day), load_run(conn, prev_day)
    out = diff_rows(prev, cur, cfg, day, _level_labels(),
                    (run_p or {}).get("storage_under"), (run_c or {}).get("storage_under"))
    return {**base, "date": day, "prev_date": prev_day, "items": out["items"],
            "counts": counts_of(out["items"]), "segments": out["segments"], "cutoff": out["cutoff"],
            "snapshot": run_c, "machines": sum(1 for r in cur.values() if not r.get("retired")),
            "reason": None if prev_day else "只有一份快照，新增／消失等差異要等明天第二份"}


def machine_history(conn: sqlite3.Connection, mkey: str, days: int = 14,
                    today: date | None = None) -> list[dict]:
    """一台機器最近 N 天的變化（值班卡用）：逐對相鄰快照，只看這一台。"""
    if not tables_exist(conn):
        return []
    today = today or date.today()
    since = (today - timedelta(days=days)).isoformat()
    ds = sorted(d for d in dates(conn) if d >= since)
    older = [d for d in dates(conn) if d < since]
    if older:
        ds = [older[0]] + ds
    cols = ("mkey",) + SNAP_FIELDS
    rows = {r[0]: dict(zip(("snap_date",) + cols, r)) for r in conn.execute(
        f"SELECT snap_date, {','.join(cols)} FROM daily_snapshot WHERE mkey = ? AND snap_date >= ?",
        (mkey, ds[0] if ds else since))}
    cfg = load_settings(conn)
    labels = _level_labels()
    out = []
    for a, b in zip(ds, ds[1:]):
        p = {mkey: rows[a]} if a in rows else {}
        c = {mkey: rows[b]} if b in rows else {}
        d = diff_rows(p, c, cfg, b, labels)
        for cat, lab in CATEGORIES:
            for it in d["items"][cat]:
                out.append({"date": b, "category": cat, "label": lab, "before": it.get("before"),
                            "after": it.get("after"), "detail": it.get("detail")})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


# ── 摘要信 ─────────────────────────────────────────────────────────────
def _url(cfg: dict, path: str) -> str:
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    return f"{base}{path}" if base else path


def compose(cfg: dict, today: dict, yesterday: dict | None) -> tuple[str, str]:
    """組信（純函式，測試直接餵 diff_for 的結果）。短、白話、每行一個連結。"""
    day = today.get("date") or "—"
    prefix = str(cfg.get("subject_prefix") or "").strip()
    c = today.get("counts") or {}
    yc = (yesterday or {}).get("counts") or {}
    total = sum(c.get(k, 0) for k, _ in CATEGORIES)
    subject = f"{prefix}[每日摘要] {day} 變化 {total} 筆"
    lines = [f"資訊戰情室每日摘要 {day}（對比 {today.get('prev_date') or '—'}）", ""]
    if today.get("reason"):
        lines += [f"※ {today['reason']}", ""]
    lines.append("一、今天的變化（括號＝昨天）")
    for k, lab in CATEGORIES:
        if k in ("uptime_risk", "storage_low", "collect_gap"):
            continue
        lines.append(f"  {lab}：{c.get(k, 0)}（{yc.get(k, 0) if yesterday else '—'}）"
                     f"  {_url(cfg, f'/changes?date={day}&tab={k}')}")
    lines += ["", "二、新風險"]
    lines.append(f"  開機超過 {_int(cfg, 'uptime_days')} 天（新）：{c.get('uptime_risk', 0)} 台"
                 f"  {_url(cfg, f'/changes?date={day}&tab=uptime_risk')}")
    th = (today.get("snapshot") or {}).get("storage_threshold")
    if th is not None:
        lines.append(f"  儲存空間新跌破 {th:g}%：{c.get('storage_low', 0)} 個"
                     f"  {_url(cfg, f'/changes?date={day}&tab=storage_low')}")
    segs = today.get("segments") or []
    whole = [s for s in segs if s.get("whole")]
    lines += ["", f"三、收集中斷（{_int(cfg, 'gap_days')} 天以上沒收到）"]
    lines.append(f"  共 {c.get('collect_gap', 0)} 台、{len(segs)} 個網段，其中整段中斷 {len(whole)} 段"
                 f"  {_url(cfg, f'/changes?date={day}&tab=collect_gap')}")
    for s in segs[:5]:
        lines.append(f"  · {s['segment']}：{s['gap']}／{s['collected']} 台" + ("（整段）" if s["whole"] else ""))
    if len(segs) > 5:
        lines.append(f"  · …其餘 {len(segs) - 5} 段請看網頁")
    lines += ["", "—— 系統自動寄出。數字＝網頁上同一份計算，點連結可看明細。"]
    return subject, "\n".join(lines)


def build_mail(conn: sqlite3.Connection, cfg: dict | None = None, day: str | None = None) -> tuple[str, str]:
    cfg = cfg or load_settings(conn)
    t = diff_for(conn, day, cfg)
    y = diff_for(conn, t["prev_date"], cfg) if t.get("prev_date") else None
    return compose(cfg, t, y)


def _log(conn, run_date, kind, recips, subject, ok, detail, smtp, at: datetime):
    ensure(conn)
    conn.execute(
        "INSERT INTO digest_mail_log (run_date, kind, sent_at, recipients, subject, status, detail, "
        "smtp_host, mail_from) VALUES (?,?,?,?,?,?,?,?,?)",
        (run_date, kind, at.strftime("%Y-%m-%d %H:%M:%S"), ", ".join(recips), subject, "sent" if ok else "failed", detail,
         smtp.get("smtp_host"), smtp.get("mail_from")))
    conn.commit()


def send(conn: sqlite3.Connection, kind: str = "scheduled", now: datetime | None = None,
         sender=None, to_override: list[str] | None = None) -> dict:
    """寄一封。sender(cfg, to, cc, subject, body) -> (ok, detail)，預設 vuln_notify._send_smtp。

    **每一次嘗試都記**（成功失敗都記、含 SMTP 回應）；設定不齊就不寄並指名缺哪一項
    （也記一筆 failed，排程那邊才不會以為今天寄過）。"""
    import vuln_notify

    ensure(conn)
    now = now or datetime.now()
    run_date = now.strftime("%Y-%m-%d")
    cfg = load_settings(conn)
    recips = to_override or recipients_of(cfg)
    missing = mail_missing(conn, {**cfg, "recipients": recips})
    try:
        smtp = vuln_notify.load_settings(conn)
    except vuln_notify.SettingsCorrupt:
        smtp = {}         # 壞掉的原因已在 missing 裡講了，下面照樣記一筆 failed
    if missing:
        detail = f"沒有寄：還缺 {'、'.join(missing)}"
        _log(conn, run_date, kind, recips, None, False, detail, smtp, now)
        return {"ok": False, "reason": detail}
    if kind == "scheduled" and run_date not in dates(conn):
        write_snapshot(conn, now, "schedule", cfg)       # 早上寄之前今天還沒快照就補一份
    subject, body = build_mail(conn, cfg)
    if kind == "test":
        subject = f"[測試] {subject}"
    sender = sender or vuln_notify._send_smtp
    try:
        ok, detail = sender(smtp, ", ".join(recips), [], subject, body)
    except Exception as exc:  # noqa: BLE001 - 寄送失敗要記下來，不可以讓它往上炸掉紀錄
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    _log(conn, run_date, kind, recips, subject, ok, detail, smtp, now)
    return {"ok": ok, "detail": detail, "subject": subject, "recipients": recips}


def mail_log(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    ensure(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM digest_mail_log ORDER BY id DESC LIMIT ?", (limit,))]


# ── 排程 ───────────────────────────────────────────────────────────────
RETRY_MINUTES = 60
RETRY_UNTIL_HOUR = 18


def snapshot_due(conn: sqlite3.Connection, now: datetime, cfg: dict) -> bool:
    """今天到點了、而且今天還沒有「排程」寫的快照（手動寫的不算：可能是夜間掃描前寫的）。"""
    if (now.hour, now.minute) < hhmm(cfg.get("snapshot_time"), DEFAULTS["snapshot_time"]):
        return False
    ensure(conn)
    r = conn.execute("SELECT trigger FROM daily_snapshot_run WHERE snap_date = ?",
                     (now.strftime("%Y-%m-%d"),)).fetchone()
    return not (r and r[0] == "schedule")


def mail_due(conn: sqlite3.Connection, now: datetime, cfg: dict) -> bool:
    """**只有寄成功才算今天寄過**；失敗了一小時後再試，到 18:00 為止。預設關著。"""
    if not cfg.get("mail_enabled"):
        return False
    if (now.hour, now.minute) < hhmm(cfg.get("mail_time"), DEFAULTS["mail_time"]):
        return False
    if now.hour >= RETRY_UNTIL_HOUR:
        return False
    ensure(conn)
    tries = conn.execute("SELECT status, sent_at FROM digest_mail_log WHERE run_date = ? "
                         "AND kind = 'scheduled' ORDER BY id", (now.strftime("%Y-%m-%d"),)).fetchall()
    if any(t[0] == "sent" for t in tries):
        return False
    if tries:
        last = datetime.strptime(tries[-1][1], "%Y-%m-%d %H:%M:%S")
        if now - last < timedelta(minutes=RETRY_MINUTES):
            return False
    return True


def tick(conn: sqlite3.Connection, now: datetime | None = None, sender=None) -> dict:
    """排程器每分鐘叫一次。"""
    now = now or datetime.now()
    try:
        cfg = load_settings(conn)
    except ValueError:
        return {"skipped": "設定壞掉"}   # 畫面讀設定時會把原因講出來；排程不能因此炸掉
    out: dict = {}
    if snapshot_due(conn, now, cfg):
        out["snapshot"] = write_snapshot(conn, now, "schedule", cfg)
    if mail_due(conn, now, cfg):
        out["mail"] = send(conn, "scheduled", now, sender)
    return out
