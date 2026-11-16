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
    # 同一實體櫃合一（2.043.14，GPT 審查 7）：快照的櫃名也用畫面櫃名（rack_name_map），跟機櫃頁、主機頁一致。
    # 位置更正（rack_position_override）這裡刻意不套：快照記的是 CIA 登記的位置，現場更正另有稽核紀錄與「CIA 待更正」。
    rack_names = rack_layout.rack_name_map(conn)
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
                rack = rack_layout.display_rack(rack_names, room, pr["rack"])
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
    """低於剩餘門檻的 datastore（儲存容量模組在才有）。回 (門檻 GB, 清單)。
    2026-11-14：跟 5.2 容量預警、1.5 每日檢核同一把尺（daily_check.lun_rule／lun_low：剩餘 < 600 GB 或用量 ≥ 90%）。
    清單每筆帶 free_gb 與 rule（白話門檻）；舊快照只有 free_pct，畫面照舊顯示 %。"""
    try:
        import storage_capacity
    except ImportError:
        return None, []        # 沒有儲存容量模組的版本：這一行信不出現
    ds = storage_capacity.datastores(conn)
    rule = ds["lun_rule"]
    txt = storage_capacity.rule_text(rule)
    return rule["threshold_gb"], [{"id": v["id"], "name": v["name"], "free_pct": v["free_pct"],
                                   "free_gb": round(v["free_mib"] / 1024, 1), "rule": txt, "why": v.get("lun_why")}
                                  for v in ds["volumes"] if v["lun_low"]]


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


def _loc_n(r: dict, rack_names: dict | None) -> str:
    """比對用的位置：櫃名換成畫面櫃名（同一實體櫃合一，2.043.14）；沒給對照＝照原值。"""
    if not rack_names:
        return _loc(r)
    import rack_layout
    return _loc({**r, "rack": rack_layout.display_rack(rack_names, r.get("room"), r.get("rack"))})


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
              cur_storage: list | None = None, rack_names: dict | None = None) -> dict:
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
            # 位置比對前，兩邊的櫃名都換成現在的畫面櫃名：舊快照記 1-1、新快照記 A1（同一實體櫃）不算位置改變
            if _loc_n(p, rack_names) != _loc_n(r, rack_names):
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
                                             "after": f"剩 {s['free_gb']:g} GB" if s.get("free_gb") is not None else f"剩 {s['free_pct']}%",
                                             "detail": f"新跌破門檻（{s['rule']}）" if s.get("rule") else "新跌破門檻"})

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
    import daily_risk
    up_on = daily_risk.uptime_enabled(conn)          # 使用者 11-14：開機過久預設不列（類別整個拿掉，加總＝下鑽仍成立）
    base = {"dates": ds, "categories": [{"key": k, "label": lab} for k, lab in CATEGORIES if up_on or k != "uptime_risk"],
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
    import rack_layout
    out = diff_rows(prev, cur, cfg, day, _level_labels(),
                    (run_p or {}).get("storage_under"), (run_c or {}).get("storage_under"), rack_layout.rack_name_map(conn))
    if not up_on:
        out["items"]["uptime_risk"] = []
    return {**base, "date": day, "prev_date": prev_day, "items": out["items"],
            "counts": counts_of(out["items"]), "segments": out["segments"], "cutoff": out["cutoff"],
            "snapshot": run_c, **machine_counts(cur), "change_total": change_total(counts_of(out["items"])),
            "reason": None if prev_day else "只有一份快照，新增／消失等差異要等明天第二份"}


def machine_counts(snap: dict[str, dict]) -> dict:
    """兩個「台數」分開講（2.065.3，驗收疑點 4）：machines＝現役台數；snapshot.machines（快照紀錄）＝快照列數＝現役＋退役。
    退役的留在快照裡是為了比出「消失（退役）」，不是算錯。verify_numbers 驗「現役＋退役＝快照紀錄台數」。"""
    active = sum(1 for r in snap.values() if not r.get("retired"))
    retired = sum(1 for r in snap.values() if r.get("retired"))
    return {"machines": active, "machines_retired": retired, "machines_rows": len(snap),
            "machines_note": "machines＝現役台數（不含退役）；machines_rows＝快照列數（現役＋退役，"
                             "與 snapshot.machines 相同）。退役留在快照裡是為了比出「消失（退役）」。"}


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
    import rack_layout
    rack_names = rack_layout.rack_name_map(conn)     # 同一實體櫃合一：換名不算位置改變
    out = []
    for a, b in zip(ds, ds[1:]):
        p = {mkey: rows[a]} if a in rows else {}
        c = {mkey: rows[b]} if b in rows else {}
        d = diff_rows(p, c, cfg, b, labels, rack_names=rack_names)
        for cat, lab in CATEGORIES:
            for it in d["items"][cat]:
                out.append({"date": b, "category": cat, "label": lab, "before": it.get("before"),
                            "after": it.get("after"), "detail": it.get("detail")})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


# ── 從沒收集過（2.066.2，驗收疑點 3）────────────────────────────────────
#: 口徑＝資料覆蓋率共用判定 data_coverage（主機收集類「spec」：未納管＋已納管尚無資料），不另寫一套。
#: 是「目前」的狀態（不是那天快照的值）：不算變化、不算收集中斷，單獨一列。
NEVER_KIND = "spec"


def never_collected(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    """1.2 每日異動／摘要信／整份日報的「從沒收集過」那一列。下鑽＝/api/v2/coverage?kind=spec&state=never（同一份 rows）。"""
    import data_coverage as dc

    r = dc.coverage(conn, NEVER_KIND, now)
    c = r["counts"]
    return {"kind": NEVER_KIND, "label": "從沒收集過", "count": sum(c[s] for s in dc.NEVER),
            "unmanaged": c[dc.UNMANAGED], "pending": c[dc.PENDING], "scope_total": r["scope_total"],
            "out_of_scope": r["out_of_scope"], "as_of": r["generated_at"], "error": None}


def never_collected_or_error(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    """同上，但算不出來時回 error（畫面／信寫「讀取失敗」），不讓整份日報跟著掛掉，也不寫成 0。"""
    try:
        return never_collected(conn, now)
    except Exception as exc:  # noqa: BLE001 - 錯誤原文回給畫面與信顯示，不吞掉
        return {"kind": NEVER_KIND, "label": "從沒收集過", "count": None, "error": f"{type(exc).__name__}: {exc}"[:200]}


def _never_line(cfg: dict, today: dict) -> list[str]:
    nc = today.get("never_collected")
    if not nc:
        return []
    head = "四、從沒收集過（目前狀態；不算變化、不算收集中斷）"
    link = _url(cfg, f"/v2/changes?tab=daily&date={today.get('date') or ''}&cat=never")
    if nc.get("error") or nc.get("count") is None:
        return ["", head, f"  讀取失敗（{nc.get('error') or '原因未記錄'}），不是 0  {link}"]
    return ["", head, f"  共 {nc['count']} 台（未納管 {nc.get('unmanaged', 0)}、已納管尚無資料 {nc.get('pending', 0)}）  {link}"]


# ── 摘要信 ─────────────────────────────────────────────────────────────
def _url(cfg: dict, path: str) -> str:
    # path 一律 V2 網址（2.042.20：信裡的連結不可以把人帶回舊版；test_v2_no_v1_links 守門）
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    return f"{base}{path}" if base else path


#: 狀態類（常態、天天都有）：不算「變化」，主旨分開寫（2.065.3，驗收疑點 1：主旨「變化 257 筆」其實全是收集中斷）
STATE_KEYS = ("collect_gap",)


def change_total(counts: dict) -> int:
    """「變化」筆數＝12 類扣掉狀態類（收集中斷）。主旨、匯出摘要頁共用這一份。"""
    return sum(int(counts.get(k, 0) or 0) for k, _ in CATEGORIES if k not in STATE_KEYS)


def subject_counts(today: dict) -> str:
    """主旨的數字段：「變化 N 筆、收集中斷 M 台」。沒有前一份快照＝比不出變化，不寫「變化 0 筆」。"""
    c = today.get("counts") or {}
    gap = f"收集中斷 {int(c.get('collect_gap', 0) or 0)} 台"
    if not today.get("prev_date"):
        return f"無前一份快照可比、{gap}"
    return f"變化 {change_total(c)} 筆、{gap}"


def compose(cfg: dict, today: dict, yesterday: dict | None) -> tuple[str, str]:
    """組信（純函式，測試直接餵 diff_for 的結果）。短、白話、每行一個連結。"""
    day = today.get("date") or "—"
    prefix = str(cfg.get("subject_prefix") or "").strip()
    c = today.get("counts") or {}
    yc = (yesterday or {}).get("counts") or {}
    subject = f"{prefix}[每日摘要] {day} {subject_counts(today)}"
    lines = [f"資訊戰情室每日摘要 {day}（對比 {today.get('prev_date') or '—'}）", ""]
    if today.get("reason"):
        lines += [f"※ {today['reason']}", ""]
    lines.append("一、今天的變化（括號＝昨天）")
    for k, lab in CATEGORIES:
        if k in ("uptime_risk", "storage_low", "collect_gap"):
            continue
        lines.append(f"  {lab}：{c.get(k, 0)}（{yc.get(k, 0) if yesterday else '—'}）"
                     f"  {_url(cfg, f'/v2/changes?tab=daily&date={day}&cat={k}')}")
    lines += ["", "二、新風險"]
    if any(x.get("key") == "uptime_risk" for x in (today.get("categories") or [{"key": "uptime_risk"}])):   # 開機過久關掉時不寫
        lines.append(f"  開機超過 {_int(cfg, 'uptime_days')} 天（新）：{c.get('uptime_risk', 0)} 台"
                     f"  {_url(cfg, f'/v2/changes?tab=daily&date={day}&cat=uptime_risk')}")
    snap = today.get("snapshot") or {}
    th = snap.get("storage_threshold")
    if th is not None:
        # 2026-11-14 起門檻是 GB（daily_check.lun_rule）；快照清單帶白話門檻就用它，舊快照（只有 %）照舊寫 %
        rule = next((x.get("rule") for x in (snap.get("storage_under") or []) if x.get("rule")), None)
        rule = rule or (f"剩餘 < {th:g} GB" if any("free_gb" in x for x in (snap.get("storage_under") or [])) or th > 100 else None)
        lines.append(f"  儲存空間新跌破門檻（{rule}）：{c.get('storage_low', 0)} 個" if rule else f"  儲存空間新跌破 {th:g}%：{c.get('storage_low', 0)} 個"
                     f"  {_url(cfg, f'/v2/changes?tab=daily&date={day}&cat=storage_low')}")
    segs = today.get("segments") or []
    whole = [s for s in segs if s.get("whole")]
    lines += ["", f"三、收集中斷（{_int(cfg, 'gap_days')} 天以上沒收到）"]
    lines.append(f"  共 {c.get('collect_gap', 0)} 台、{len(segs)} 個網段，其中整段中斷 {len(whole)} 段"
                 f"  {_url(cfg, f'/v2/changes?tab=daily&date={day}&cat=collect_gap')}")
    for s in segs[:5]:
        lines.append(f"  · {s['segment']}：{s['gap']}／{s['collected']} 台" + ("（整段）" if s["whole"] else ""))
    if len(segs) > 5:
        lines.append(f"  · …其餘 {len(segs) - 5} 段請看網頁")
    lines += _never_line(cfg, today)
    lines += ["", "—— 系統自動寄出。數字＝網頁上同一份計算，點連結可看明細。"]
    return subject, "\n".join(lines)


def build_mail(conn: sqlite3.Connection, cfg: dict | None = None, day: str | None = None) -> tuple[str, str]:
    cfg = cfg or load_settings(conn)
    t = diff_for(conn, day, cfg)
    t["never_collected"] = never_collected_or_error(conn)
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


# ── 整份日報匯出（2.065.3，驗收疑點 8）──────────────────────────────────
EXPORT_COLS = ("項次", "主機名稱", "IP", "資產序號", "異動前", "異動後", "說明", "網段", "machine_key")


def _cell(v):
    if isinstance(v, (list, tuple)):
        return "、".join(str(x) for x in v)
    return "" if v is None else v


def export_workbook(conn: sqlite3.Connection, day: str | None = None, cfg: dict | None = None):
    """某一天的完整日報 → (Workbook, 日期, 明細總列數)。摘要頁＋收集中斷網段＋12 類各一頁＋信件內容。
    每類的列＝diff_for 的 items（跟畫面分頁、信同一份計算：摘要頁筆數＝該類頁的列數）。"""
    from openpyxl import Workbook

    import export_safe

    cfg = cfg or load_settings(conn)
    t = diff_for(conn, day, cfg)
    t["never_collected"] = nc = never_collected_or_error(conn)
    y = diff_for(conn, t["prev_date"], cfg) if t.get("prev_date") else None
    subject, body = compose(cfg, t, y)
    snap = t.get("snapshot") or {}
    c = t.get("counts") or {}
    wb = Workbook()
    ws = wb.active
    ws.title = "摘要"
    ws.append(["項目", "值", "說明"])
    for row in (
        ("日期", t.get("date") or "—", "這份日報的快照日期"),
        ("對比", t.get("prev_date") or "—", "前一份快照日期"),
        ("快照時間", snap.get("created_at") or "—", f"寫入方式：{snap.get('trigger') or '—'}"),
        ("現役台數", t.get("machines"), "不含退役"),
        ("快照列數", t.get("machines_rows"), f"現役＋退役 {t.get('machines_retired')} 台；退役留著是為了比出「消失（退役）」"),
        ("變化合計", change_total(c) if t.get("prev_date") else "無前一份快照可比", "12 類扣掉收集中斷（常態類）"),
        ("從沒收集過", nc["count"] if nc.get("count") is not None else f"讀取失敗：{nc.get('error')}",
         f"目前狀態（匯出時 {nc.get('as_of') or '—'}），不算變化、不算收集中斷；未納管 {nc.get('unmanaged', '—')}、"
         f"已納管尚無資料 {nc.get('pending', '—')}（口徑＝資料覆蓋率・主機規格收集）"),
        ("信件主旨", subject, ""),
    ):
        ws.append(export_safe.row(list(row)))
    ws.append([])
    ws.append(["項次", "分類", "筆數"])
    for i, (k, lab) in enumerate(CATEGORIES, 1):
        ws.append(export_safe.row([i, lab, c.get(k, 0)]))
    sg = wb.create_sheet("收集中斷網段")
    sg.append(["項次", "網段", "中斷台數", "有收集過的台數", "整段中斷"])
    for i, s in enumerate(t.get("segments") or [], 1):
        sg.append(export_safe.row([i, s["segment"], s["gap"], s["collected"], "是" if s.get("whole") else ""]))
    total = 0
    for n, (k, lab) in enumerate(CATEGORIES, 1):
        sh = wb.create_sheet(export_safe.sheet_title(f"{n:02d}{lab}"))
        sh.append(list(EXPORT_COLS))
        for i, it in enumerate(t.get("items", {}).get(k) or [], 1):
            sh.append(export_safe.row([i, it.get("hostname") or it.get("serial") or it.get("key"), _cell(it.get("ips")),
                                       _cell(it.get("serials")), _cell(it.get("before")), _cell(it.get("after")),
                                       _cell(it.get("detail")), _cell(it.get("segment")), it.get("key")]))
            total += 1
    ml = wb.create_sheet("信件內容")
    ml.append(["主旨", subject])
    for line in body.split("\n"):
        ml.append(export_safe.row(["", line]))
    export_safe.neutralize_workbook(wb)
    return wb, t.get("date") or "none", total


# ── 排程失敗紀錄（2.065.3，驗收疑點 6：原本例外整個吞掉）────────────────
TICK_ERROR_KEY = "daily_digest_tick_error"
#: 同一個錯誤每小時最多記一筆操作紀錄（排程每分鐘重試，不可以把 activity_log 灌爆）
TICK_ERROR_LOG_EVERY_MIN = 60


def tick_error(conn: sqlite3.Connection) -> dict | None:
    """最近一次排程失敗（還沒被下一次成功清掉）。{at, first_at, count, error}；沒有＝None。"""
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (TICK_ERROR_KEY,)).fetchone()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0]) or None
    except (ValueError, TypeError):
        return {"at": None, "first_at": None, "count": None, "error": "排程失敗紀錄內容壞掉（讀不出來）"}


def tick_error_sig(exc: BaseException) -> str:
    """「同一個錯誤」＝例外類型＋訊息第一行（訊息後面帶的時間、筆數變了也算同一個，不重記 activity_log）。"""
    first = (str(exc).strip().splitlines() or [""])[0]
    return f"{type(exc).__name__}: {first}"[:300]


def record_tick_error(conn: sqlite3.Connection, exc: BaseException, now: datetime | None = None) -> dict:
    """排程失敗：寫 app_settings（1.1 首頁待處理、1.2 每日異動看得到）＋activity_log（同一錯誤每小時一筆）。"""
    now = now or datetime.now()
    at = now.strftime("%Y-%m-%d %H:%M:%S")
    err = f"{type(exc).__name__}: {exc}"[:500]
    # 失敗那一步自己的寫入已由 tick() 的 SAVEPOINT 退回；這裡不 rollback 共用連線（會連帶丟掉別的 tick 的寫入）
    sig = tick_error_sig(exc)
    prev = tick_error(conn) or {}
    same = prev.get("sig") == sig
    rec = {"at": at, "first_at": prev.get("first_at") if same and prev.get("first_at") else at,
           "count": (int(prev.get("count") or 0) + 1) if same else 1, "error": err, "sig": sig,
           "logged_at": prev.get("logged_at") if same else None}
    last = rec["logged_at"]
    due = (not last) or (now - datetime.strptime(last, "%Y-%m-%d %H:%M:%S") >= timedelta(minutes=TICK_ERROR_LOG_EVERY_MIN))
    if due:
        import activity
        activity.log(conn, username="system", ip=None, method="TICK", path=f"排程：每日異動快照／摘要信失敗 {err}",
                     status=500, action="error", commit=False)
        rec["logged_at"] = at
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (TICK_ERROR_KEY, json.dumps(rec, ensure_ascii=False)))
    conn.commit()
    return rec


def clear_tick_error(conn: sqlite3.Connection) -> None:
    """排程成功跑完一輪：清掉失敗紀錄（只有存在時才寫，平常每分鐘不動資料庫）。"""
    if conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (TICK_ERROR_KEY,)).fetchone():
        conn.execute("DELETE FROM app_settings WHERE key = ?", (TICK_ERROR_KEY,))
        conn.commit()


def tick_error_label(rec: dict | None) -> str | None:
    if not rec:
        return None
    n = rec.get("count")
    times = f"，連續 {n} 次" if n and n > 1 else ""
    return f"排程失敗（最近 {rec.get('at') or '時間不明'}{times}）：{rec.get('error')}"


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


def is_trading_day(conn: sqlite3.Connection, now: datetime) -> bool:
    """開盤日＝平日且不在休市日（跟靜默時段、容量分析同一份休市日設定）。2026-11-09 使用者：沒開盤的日子不寄每日摘要
    （週末 11/07、11/08 照寄而且全 0）。休市日讀不到就只看週末（寧可多寄一封，不要平日漏寄）。只擋排程，手動寄照常。"""
    if now.weekday() >= 5:
        return False
    try:
        import quiet_window
        return now.strftime("%Y-%m-%d") not in set(quiet_window.get_config(conn)["holidays"] or ())
    except Exception:  # noqa: BLE001 - 休市日設定讀不到：當平日（見上）
        return True


def mail_due(conn: sqlite3.Connection, now: datetime, cfg: dict) -> bool:
    """**只有寄成功才算今天寄過**；失敗了一小時後再試，到 18:00 為止。預設關著。"""
    if not cfg.get("mail_enabled"):
        return False
    if (now.hour, now.minute) < hhmm(cfg.get("mail_time"), DEFAULTS["mail_time"]):
        return False
    if now.hour >= RETRY_UNTIL_HOUR:
        return False
    if not is_trading_day(conn, now):
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
    # 只退回自己的寫入（審查 P3）：失敗時 ROLLBACK TO 這個 savepoint，不 rollback 整條共用連線
    conn.execute("SAVEPOINT daily_digest_tick")
    try:
        if snapshot_due(conn, now, cfg):
            out["snapshot"] = write_snapshot(conn, now, "schedule", cfg)
        if mail_due(conn, now, cfg):
            out["mail"] = send(conn, "scheduled", now, sender)
    except BaseException:
        _savepoint_end(conn, rollback=True)
        raise
    _savepoint_end(conn, rollback=False)
    return out


def _savepoint_end(conn: sqlite3.Connection, rollback: bool) -> None:
    """結束 tick 的 savepoint。中途 write_snapshot／send 已 commit 時 savepoint 已跟著結束，
    再 ROLLBACK TO／RELEASE 會回 no such savepoint——那代表已經沒有屬於這一輪、尚未 commit 的寫入。"""
    try:
        if rollback:
            conn.execute("ROLLBACK TO daily_digest_tick")
        conn.execute("RELEASE daily_digest_tick")
    except sqlite3.OperationalError as exc:
        if "no such savepoint" not in str(exc):
            raise
