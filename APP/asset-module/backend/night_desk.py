"""夜間速查（改版，2026-11-15 夜間衝刺 N3～N6 後端；介面約定 docs/codex-review/2026-11-15/06）。

半夜被 Teams 叫醒 → 這頁最上面就是剛剛通知你的那台 → 點進去一頁看完（活著嗎、跟平常比、影響多大、找誰、最近 7 天）。

* 今晚通知清單（notified）：**只列 Teams 真的送出去的**——teams_notify_delivery status='sent'，
  再對回是哪一台：WhatsUp 告警（alert_event 已推 open／escalate＋同時段那則通知送出）、硬碟告警
  （disk_alert_history 送出 → 同一批 disk_alert_seen）、記憶體／SWAP（mem_alert_sent 送出 → items_json）、
  WhatsUp 告警資料中斷（alert_notice heartbeat_down 送出）。排進佇列沒送出的不列（沒通知到人）。
* 緊急檢查彙整（host）：只讀、不連主機。活著與否用最近 30 分鐘內存過的即時檢測快照＋監控（4.1 告警事件）。
* 寫入（D4）：night_check_snapshot（緊急檢查當下結果）、night_handling（我在處理／處理結果）。
  只新增、不刪；結果只能寫一次（結案後不可改，要補充請到主機履歷加處理紀錄）。讀取路徑不建表。
* 主機履歷 host_ops 與 10.1 月報 host_monthly 從這裡讀（events_for_host、month_summary）。

時間一律台北時間字串 YYYY-MM-DD HH:MM:SS；查不到回 None 並附原因，[]／0 只代表查過、真的沒有。
"""
from __future__ import annotations

import bisect
import json
import re
import sqlite3
from datetime import datetime, timedelta

FMT = "%Y-%m-%d %H:%M:%S"
K_HOURS = "night_notified_hours"
DEFAULT_HOURS = 12
MAX_HOURS = 72
LIVE_FRESH_MIN = 30          # 即時檢測快照多新才拿來判斷活著
REBOOT_MIN = 30              # 開機不到這麼久＝剛重開
BIG_HOSTS = 3                # 受影響（開機中、machine_key 去重）超過這麼多台＝影響範圍大
RESULT_MAX = 500
OLDER_DAYS = 1
KIND_LABEL = {"wug": "WhatsUp 告警", "disk": "硬碟告警", "mem": "記憶體／SWAP 告警", "heartbeat": "告警資料中斷"}
POWER_ON = {"poweredon", "on", "powered on", "running"}

SQL = [
    """CREATE TABLE IF NOT EXISTS night_check_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        machine_key TEXT NOT NULL, ip TEXT, at TEXT NOT NULL, by TEXT,
        alive_state TEXT, conclusion TEXT,
        result_json TEXT NOT NULL,            -- 即時檢測結果（只留比對要用的欄位，不留原始輸出）
        baseline_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_night_snap_mk ON night_check_snapshot(machine_key, at)",
    """CREATE TABLE IF NOT EXISTS night_handling (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id TEXT NOT NULL, machine_key TEXT NOT NULL, kind TEXT, title TEXT, notified_at TEXT,
        by TEXT NOT NULL, at TEXT NOT NULL,
        result TEXT, done_by TEXT, done_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_night_handling_item ON night_handling(item_id, id)",
    "CREATE INDEX IF NOT EXISTS ix_night_handling_mk ON night_handling(machine_key, at)",
]
# 同一項目同時只能有一筆未結案（部分唯一索引）。既有資料若已有重複就建不起來，略過（程式碼那邊仍會先查再寫）
SQL_OPEN_UNIQUE = "CREATE UNIQUE INDEX IF NOT EXISTS ux_night_handling_open ON night_handling(item_id) WHERE done_at IS NULL"


def ensure(conn: sqlite3.Connection) -> None:
    """只在寫入入口呼叫（CREATE IF NOT EXISTS，只增不改既有表）。"""
    for s in SQL:
        conn.execute(s)
    try:
        conn.execute(SQL_OPEN_UNIQUE)
    except sqlite3.IntegrityError:
        pass


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _now() -> datetime:
    import teams_notify as tn
    return tn.now_taipei()


def _p(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s).strip()[:19].replace("T", " "), FMT)
    except ValueError:
        return None


def _f(d: datetime | None) -> str | None:
    return d.strftime(FMT) if d else None


def hours_setting(conn) -> int:
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (K_HOURS,)).fetchone()
        v = int(r[0]) if r and r[0] else DEFAULT_HOURS
    except (sqlite3.Error, ValueError, TypeError):
        v = DEFAULT_HOURS
    return min(max(v, 1), MAX_HOURS)


# ===================================================================== 對資產
class _World:
    """一次讀全站資產，之後每台查名字、IP、系統、環境、是不是 VMware（清單用，不逐列查庫）。"""

    def __init__(self, conn):
        import host_ops
        self.conn = conn
        self.idx = host_ops._Idx(conn)
        self._vm: dict[str, object] = {}
        self._esxi_names = None
        self._vc_rows = None          # 同一個請求內只解析一次 vCenter 匯入資料（P2-8）

    def key_of(self, ip: str | None = None, mk: str | None = None) -> str | None:
        if mk and mk in self.idx.groups:
            return mk
        if ip:
            ks = self.idx.ip.get(ip) or set()
            if len(ks) == 1:
                return next(iter(ks))
        return None

    def info(self, k: str | None) -> dict:
        import manage_state as ms
        import system_stats
        import v2_views
        g = self.idx.groups.get(k) if k else None
        if not g:
            return {"key": k, "name": None, "ip": None, "ips": [], "serials": [], "api_ids": [], "env": None, "os": None}
        ips: list[str] = []
        for r in g:
            for ip in ms.ips_of(r["ip"]):
                if ip not in ips:
                    ips.append(ip)
        apis = sorted({(r["api_id"] or "").strip() for r in g if (r["api_id"] or "").strip()})
        return {"key": k, "name": v2_views.name_of(g[0]), "hostname": g[0]["hostname"], "ip": ips[0] if ips else None, "ips": ips,
                "serials": [r["asset_serial"] for r in g], "api_ids": apis, "env": system_stats.normalize_env(g[0]["environment"]),
                "os": g[0]["os"], "rows": g}

    def vmware(self, k: str | None):
        """VMware 設備（ESXi／vCenter）判定（納管同一個入口）。回依據字串或 None；判定壞掉＝當作是（fail-closed）。"""
        if not k or k not in self.idx.groups:
            return None
        if k not in self._vm:
            try:
                import onboard_eligibility as oe
                if self._esxi_names is None:
                    self._esxi_names = oe.esxi_host_names(self.conn)
                got = [oe.judge_for_asset(self.conn, r, esxi_names=self._esxi_names) for r in self.idx.groups[k]]
                self._vm[k] = next((x[2] for x in got if x and x[0] == "esxi"), None)
            except Exception as exc:  # noqa: BLE001 - 判定壞掉保守當 VMware（前端就不會發即時檢測）
                self._vm[k] = f"VMware 判定失敗（{type(exc).__name__}）"
        return self._vm[k]

    def platform(self, k: str | None) -> str | None:
        inf = self.info(k)
        if self.vmware(k):
            return "esxi"
        o = (inf.get("os") or "").lower()
        for w, lab in (("windows", "windows"), ("aix", "aix"), ("linux", "linux"), ("red hat", "linux"), ("rhel", "linux"),
                       ("centos", "linux"), ("ubuntu", "linux"), ("suse", "linux"), ("oracle", "linux")):
            if w in o:
                return lab
        return o or None


def _systems(conn, api_ids: list[str], env: str | None) -> list[dict]:
    import alert_contact
    rv = alert_contact.Resolver(conn)
    out = []
    for a in api_ids:
        c = rv.get(a)
        out.append({"api_id": a, "name": c.get("system_name"), "tier": c.get("sys_class"), "env": env})
    return out


def _pick_system(systems: list[dict]) -> dict | None:
    if not systems:
        return None
    return sorted(systems, key=lambda s: ({"第一類": 0, "第二類": 1, "第三類": 2}.get(s.get("tier") or "", 9), s["api_id"]))[0]


# ===================================================================== 影響範圍
def _vcenter_rows(conn) -> tuple[list[tuple], str | None]:
    """vCenter／RVTools 匯入資料解析成 [(payload, collected_at, hw_id)] ＋ 最新匯入時間。"""
    rows, newest = [], None
    for r in conn.execute("SELECT payload, collected_at, resolved_hardware_id FROM source_record WHERE source = 'vcenter'"):
        try:
            pl = json.loads(r["payload"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(pl, dict):
            continue
        newest = max(newest or "", r["collected_at"] or "") or newest
        rows.append((pl, r["collected_at"], r["resolved_hardware_id"]))
    return rows, newest


def _vcenter_vms(conn, esxi_names: set[str], w: "_World | None" = None) -> tuple[list[dict], str | None, str | None]:
    """最後一次 vCenter／RVTools 匯入裡，跑在這台 ESXi 上的 VM（每台 VM 取最新一筆）。回 (vms, 資料時間, 讀不到的原因)。
    給 w 時同一個請求內只解析一次（清單有很多台 ESXi 時不重複掃整張表）。"""
    import machine_identity as mi
    if not _has(conn, "source_record"):
        return [], None, "沒有 vCenter／RVTools 匯入資料"
    best: dict[str, dict] = {}
    if w is not None:
        if w._vc_rows is None:
            w._vc_rows = _vcenter_rows(conn)
        rows, newest = w._vc_rows
    else:
        rows, newest = _vcenter_rows(conn)
    for pl, collected_at, hw in rows:
        if mi.norm_name(pl.get("esxi_host")) not in esxi_names:
            continue
        nm = str(pl.get("vm_name") or pl.get("hostname") or "").strip()
        if not nm:
            continue
        if nm not in best or str(collected_at or "") >= str(best[nm]["at"] or ""):
            best[nm] = {"name": nm, "hostname": pl.get("hostname"), "power": pl.get("powerstate"), "at": collected_at,
                        "hw_id": hw}
    if newest is None:
        return [], None, "沒有 vCenter／RVTools 匯入資料"
    return sorted(best.values(), key=lambda v: v["name"]), newest, None


def impact(conn, w: _World, k: str | None, platform: str | None = None) -> dict:
    """受影響的開機中主機台數（machine_key 去重）與業務系統。一般主機＝1（自己）；ESXi 本身不算，算它上面開機中的 VM。"""
    import machine_identity as mi
    import system_stats
    inf = w.info(k)
    if not k or not inf["serials"]:
        return {"hosts": None, "systems": [], "big": False, "vms": [], "vm_data_at": None, "reason": "資產庫對不到這台，影響範圍算不出來"}
    if (platform or w.platform(k)) == "esxi":
        names = {mi.norm_name(r["hostname"]) for r in inf["rows"] if r["hostname"]} | set(inf["ips"])
        names.discard("")
        vms, at, why = _vcenter_vms(conn, names, w)
        if why:
            return {"hosts": None, "systems": [], "big": False, "vms": [], "vm_data_at": None, "reason": why}
        keys, apis, out_vms = set(), {}, []
        for v in vms:
            on = str(v["power"] or "").strip().lower() in POWER_ON
            vk, api = None, None
            if v["hw_id"] is not None:
                hr = conn.execute("SELECT hostname, ip, asset_serial, api_id, environment FROM hardware WHERE id = ?", (v["hw_id"],)).fetchone()
                if hr:
                    vk = system_stats.machine_key(hr["hostname"], hr["ip"], hr["asset_serial"])
                    api = (hr["api_id"] or "").strip() or None
                    if api:
                        apis.setdefault(api, system_stats.normalize_env(hr["environment"]))
            out_vms.append({"name": v["name"], "api_id": api, "power": "on" if on else (v["power"] or "不知道")})
            if on:
                keys.add(vk or "vm:" + v["name"].lower())
        n = len(keys)
        systems = []
        for a, env in sorted(apis.items()):
            systems += _systems(conn, [a], env)
        return {"hosts": n, "systems": systems, "big": n > BIG_HOSTS, "vms": out_vms, "vm_data_at": at,
                "reason": None if vms else "vCenter 資料裡這台 ESXi 上沒有 VM"}
    systems = _systems(conn, inf["api_ids"], inf["env"])
    return {"hosts": 1, "systems": systems, "big": False, "vms": [], "vm_data_at": None, "reason": None}


# ===================================================================== 今晚通知清單
def _sent(conn, keys: list[str]) -> dict[str, str]:
    """event_key → sent_at（只回真的送出的）。"""
    keys = [k for k in keys if k]
    if not keys or not _has(conn, "teams_notify_delivery"):
        return {}
    out = {}
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        for r in conn.execute("SELECT event_key, sent_at FROM teams_notify_delivery WHERE status = 'sent' AND event_key IN (%s)"
                              % ",".join("?" * len(part)), part):
            out[r["event_key"]] = r["sent_at"]
    return out


def _mentions(text: str, needle: str) -> bool:
    """needle 在 text 裡是「整個詞」：前後不可接英數、底線、減號、點（IP 10.99.1.1 不配 10.99.1.10、主機名 app01 不配 app010／xapp01）。
    句尾的點（後面不是英數）不算接上。"""
    if not needle:
        return False
    tail = r"(?![A-Za-z0-9_\-]|\.[0-9])" if re.fullmatch(r"[0-9.]+", needle) else r"(?![A-Za-z0-9_\-])"     # IP 後面不可再接「.數字」；主機名後面可接 .網域
    return re.search(r"(?<![A-Za-z0-9_.\-])" + re.escape(needle) + tail, text) is not None


def _wug_items(conn, start: datetime, now: datetime) -> list[dict]:
    if not _has(conn, "alert_event") or not _has(conn, "alert_notice"):
        return []
    notices = [dict(r) for r in conn.execute(
        "SELECT id, at, kind, delivery_key, summary FROM alert_notice WHERE kind IN ('open','escalate') AND status = 'queued' "
        "AND at >= ? AND at <= ?", (_f(start - timedelta(days=OLDER_DAYS + 1)), _f(now)))]
    sent = _sent(conn, [n["delivery_key"] for n in notices])
    notices = [n for n in notices if n["delivery_key"] in sent]
    if not notices:
        return []
    notices = [dict(n, _t=_p(n["at"])) for n in notices]
    notices = sorted((n for n in notices if n["_t"] is not None), key=lambda x: (x["_t"], x["id"]))      # 排序只做一次
    times = [n["_t"] for n in notices]
    out = []
    for r in conn.execute("SELECT * FROM alert_event WHERE (notified LIKE '%,open,%' OR notified LIKE '%,escalate,%') AND tier != 'maint' "
                          "AND started_at <= ? AND (ended_at IS NULL OR ended_at >= ?)", (_f(now), _f(start - timedelta(days=OLDER_DAYS)))):
        r = dict(r)
        s = _p(r["started_at"]) or now
        e = _p(r["ended_at"]) or now
        who = [x for x in (r.get("hostname"), r.get("device_name"), r.get("wug_host"), r.get("ip")) if x]
        hit = None
        lo = bisect.bisect_left(times, s - timedelta(minutes=5))        # 只掃事件時段 ±5 分鐘內的通知
        hi = bisect.bisect_right(times, e + timedelta(minutes=5))
        for n in notices[lo:hi]:
            sm = n["summary"] or ""
            if re.search(r"#%d(?!\d)" % int(r["id"]), sm) or any(_mentions(sm, x) for x in who):
                hit = n
                break
        if hit is None:
            continue
        sent_at = sent[hit["delivery_key"]] or hit["at"]
        out.append({"id": f"wug:{r['id']}", "kind": "wug", "notified_at": sent_at,
                    "title": f"{r['monitor'] if r['monitor'] != '(設備)' else '設備'} {r['wug_state'] or 'Down'}".strip(),
                    "detail": r.get("status_text") or r.get("tier_reason") or "",
                    "machine_key": r.get("machine_key"), "ip": r.get("ip"), "name": who[0] if who else r.get("wug_device_id"),
                    "open": r["ended_at"] is None, "recovered_at": r["ended_at"],
                    "_api": (r.get("api_id") or "").strip() or None, "_sys": {"name": r.get("system_name"), "tier": r.get("sys_class"),
                                                                             "env": r.get("environment")}})
    return out


def _disk_items(conn, w: _World, start: datetime, now: datetime | None = None) -> list[dict]:
    if not _has(conn, "disk_alert_history") or not _has(conn, "disk_alert_seen"):
        return []
    import disk_alert
    hist = [dict(r) for r in conn.execute("SELECT batch_id, tier, delivery_key, status, threshold FROM disk_alert_history "
                                          "WHERE kind = 'alert' AND at >= ?" + (" AND at <= ?" if now else ""),
                                          (_f(start - timedelta(days=1)),) + ((_f(now),) if now else ()))]
    sent = _sent(conn, [h["delivery_key"] for h in hist if h["status"] == "queued"])
    by_bt: dict[tuple, list[dict]] = {}
    for h in hist:
        by_bt.setdefault((h["batch_id"], h["tier"]), []).append(h)
    out = []
    for (bid, tier), hs in by_bt.items():
        ok = [h for h in hs if h["delivery_key"] in sent and (sent[h["delivery_key"]] or "") >= _f(start)]
        if not ok:
            continue
        split = len(hs) > 1                       # 同一批同一級拆成正式（立即送）＋非正式（進 08:00 彙整）
        sent_at = min(sent[h["delivery_key"]] for h in ok)
        for s in conn.execute("SELECT host, disk, day, tier, pct, first_at FROM disk_alert_seen WHERE batch_id = ? AND tier = ?", (bid, tier)):
            host = s["host"]
            mk = host[3:] if host.startswith("mk:") else None
            ip = host[3:] if host.startswith("ip:") else None
            k = w.key_of(ip=ip, mk=mk)
            inf = w.info(k)
            ip = ip or inf.get("ip")
            if split and ip:
                try:
                    if not disk_alert.is_prod(conn, ip):
                        continue
                except Exception:  # noqa: BLE001 - 判不出正式與否：照列（寧可多列，不藏）
                    pass
            lab = {"critical": "硬碟緊急", "warn": "硬碟準備", "surge": "硬碟暴增"}.get(tier, "硬碟告警")
            out.append({"id": f"disk:{host}:{s['disk']}:{s['day']}:{tier}", "kind": "disk", "notified_at": sent_at,
                        "title": f"{lab} {s['disk']} {s['pct']:g}%" if isinstance(s["pct"], (int, float)) else f"{lab} {s['disk']}",
                        "detail": f"{s['day']} 資料；第一次看到 {s['first_at']}", "machine_key": k, "ip": ip, "name": inf.get("name") or ip or host,
                        "open": True, "recovered_at": None})
    return out


def _mem_items(conn, w: _World, start: datetime, now: datetime | None = None) -> list[dict]:
    if not _has(conn, "mem_alert_sent"):
        return []
    rows = [dict(r) for r in conn.execute("SELECT day, kind, delivery_key, items_json FROM mem_alert_sent WHERE status = 'queued' AND at >= ?"
                                          + (" AND at <= ?" if now else ""), (_f(start - timedelta(days=1)),) + ((_f(now),) if now else ()))]
    sent = _sent(conn, [r["delivery_key"] for r in rows])
    out = []
    for r in rows:
        at = sent.get(r["delivery_key"])
        if not at or at < _f(start):
            continue
        try:
            items = json.loads(r["items_json"] or "[]")
        except ValueError:
            items = []
        for it in items if isinstance(items, list) else []:
            ip = str((it or {}).get("host") or "")
            if not ip:
                continue
            k = w.key_of(ip=ip)
            inf = w.info(k)
            swap = r["kind"].startswith("swap")
            what = f"SWAP {it.get('swap')}%" if swap else f"記憶體最高 {it.get('mem')}%"
            out.append({"id": f"mem:{r['day']}:{r['kind'].rstrip('+')}:{ip}", "kind": "mem", "notified_at": at, "title": what,
                        "detail": f"{r['day']} 資料", "machine_key": k, "ip": ip, "name": inf.get("name") or ip, "open": True, "recovered_at": None})
    return out


def _hb_items(conn, start: datetime, now: datetime | None = None) -> list[dict]:
    if not _has(conn, "alert_notice"):
        return []
    rows = [dict(r) for r in conn.execute("SELECT id, at, kind, delivery_key, title FROM alert_notice WHERE kind IN ('heartbeat_down','heartbeat_up') "
                                          "AND at >= ?" + (" AND at <= ?" if now else "") + " ORDER BY at",
                                          (_f(start),) + ((_f(now),) if now else ()))]
    sent = _sent(conn, [r["delivery_key"] for r in rows])
    out = []
    for r in rows:
        if r["kind"] != "heartbeat_down" or r["delivery_key"] not in sent:
            continue
        up = next((x for x in rows if x["kind"] == "heartbeat_up" and x["at"] > r["at"]), None)
        out.append({"id": f"hb:{r['id']}", "kind": "heartbeat", "notified_at": sent[r["delivery_key"]] or r["at"], "title": r["title"] or "告警資料中斷",
                    "detail": "WhatsUp 告警資料沒有進來（不是某一台主機的問題）", "machine_key": None, "ip": None, "name": "WhatsUp 告警資料",
                    "open": up is None, "recovered_at": up["at"] if up else None})
    return out


def _handling_map(conn, items: list[dict]) -> dict[str, dict]:
    """item id → 最新一筆處理紀錄。只配「處理時間不早於這則通知時間」的：同一個項目編號隔天再通知，不會配到上一次已結案的。"""
    nat = {x["id"]: x.get("notified_at") for x in items}
    ids = list(nat)
    if not ids or not _has(conn, "night_handling"):
        return {}
    out = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in conn.execute("SELECT * FROM night_handling WHERE item_id IN (%s) ORDER BY id" % ",".join("?" * len(part)), part):
            n = nat.get(r["item_id"])
            if n and (r["at"] or "") < n:
                continue
            out[r["item_id"]] = {"id": r["id"], "by": r["by"], "at": r["at"], "result": r["result"], "done_at": r["done_at"]}
    return out


def collect(conn, start: datetime, now: datetime, w: _World | None = None) -> list[dict]:
    """[start, now] 之間 Teams 真的送出的通知，一則對到一台一筆（還沒合併）。"""
    w = w or _World(conn)
    items = _wug_items(conn, start, now) + _disk_items(conn, w, start, now) + _mem_items(conn, w, start, now) + _hb_items(conn, start, now)
    for it in items:
        if it["kind"] != "heartbeat":
            it["machine_key"] = w.key_of(ip=it.get("ip"), mk=it.get("machine_key"))
    return items


def notified(conn, hours: int | None = None, now: datetime | None = None) -> dict:
    now = now or _now()
    hours = min(max(int(hours or hours_setting(conn)), 1), MAX_HOURS)
    start = now - timedelta(hours=hours)
    w = _World(conn)
    raw = collect(conn, start - timedelta(days=OLDER_DAYS), now, w)
    tonight = [x for x in raw if (x["notified_at"] or "") >= _f(start)]
    older_open = {x["id"] for x in raw if (x["notified_at"] or "") < _f(start) and x["open"]}
    groups: dict[str, list[dict]] = {}
    for it in tonight:
        gk = it["machine_key"] or (f"ip:{it['ip']}" if it.get("ip") else it["id"])
        groups.setdefault(gk, []).append(it)
    hmap = _handling_map(conn, tonight)
    out = []
    for gk, its in groups.items():
        its.sort(key=lambda x: (not x["open"], x["notified_at"] or ""), reverse=False)
        opens = [x for x in its if x["open"]]
        rep = sorted(opens or its, key=lambda x: x["notified_at"] or "", reverse=True)[0]
        k = rep["machine_key"]
        inf = w.info(k)
        plat = w.platform(k) if k else None
        if k:
            systems = _systems(conn, inf["api_ids"], inf["env"])
        elif rep.get("_api"):
            systems = [{"api_id": rep["_api"], **{kk: rep["_sys"].get(kk) for kk in ("name", "tier", "env")}}]
        else:
            systems = []
        try:
            imp = impact(conn, w, k, plat) if k else {"hosts": None, "systems": [], "big": False, "reason": "資產庫對不到這台"}
        except sqlite3.Error as exc:
            imp = {"hosts": None, "systems": [], "big": False, "reason": f"影響範圍讀不到（{type(exc).__name__}）"}
        hd = next((hmap[x["id"]] for x in sorted(its, key=lambda x: x["notified_at"] or "", reverse=True) if x["id"] in hmap), None)
        out.append({"id": rep["id"], "kind": rep["kind"], "notified_at": rep["notified_at"], "title": rep["title"], "detail": rep["detail"],
                    "machine_key": k, "name": inf.get("name") or rep.get("name"), "ip": rep.get("ip") or inf.get("ip"), "platform": plat,
                    "open": bool(opens), "recovered_at": None if opens else max((x["recovered_at"] or "") for x in its) or None,
                    "times_tonight": len(its), "system": _pick_system(systems),
                    "impact": {"hosts": imp.get("hosts"), "systems": len(imp.get("systems") or systems), "big": bool(imp.get("big")),
                               "reason": imp.get("reason")},
                    "handling": hd, "handled": hd is not None,
                    "parts": [{"id": x["id"], "kind": x["kind"], "kind_label": KIND_LABEL.get(x["kind"]), "title": x["title"],
                               "notified_at": x["notified_at"], "open": x["open"], "recovered_at": x["recovered_at"]} for x in its]})
    out.sort(key=lambda x: x["notified_at"] or "", reverse=True)          # 先新到舊，再穩定排序：未恢復、第一類、影響大在前
    out.sort(key=lambda x: (not x["open"], (x["system"] or {}).get("tier") != "第一類", not x["impact"]["big"]))
    for n, x in enumerate(out, 1):
        x["no"] = n
    data_at = None
    for t, col in (("alert_scan", "received_at"), ("relay_import_batches", "imported_at"), ("teams_notify_delivery", "sent_at")):
        if _has(conn, t):
            v = conn.execute(f"SELECT MAX({col}) FROM {t}").fetchone()[0]
            data_at = max(data_at or "", v or "") or data_at
    return {"now": _f(now), "hours": hours, "data_at": data_at,
            "summary": {"total": len(out), "open": sum(1 for x in out if x["open"]),
                        "tier1": sum(1 for x in out if (x["system"] or {}).get("tier") == "第一類"),
                        "big_impact": sum(1 for x in out if x["impact"]["big"])},
            "items": out, "older": {"count": len(older_open), "link": "/v2/night?view=older"}}


# ===================================================================== 緊急檢查彙整
def _bare(k: str) -> str:
    return k[4:] if k.startswith("ret:") else k


def _latest_snapshot(conn, mk: str) -> dict | None:
    if not _has(conn, "night_check_snapshot"):
        return None
    b = _bare(mk)
    r = conn.execute("SELECT * FROM night_check_snapshot WHERE machine_key IN (?, ?) ORDER BY at DESC, id DESC LIMIT 1", (b, "ret:" + b)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["result"] = json.loads(d.pop("result_json") or "{}")
    except ValueError:
        d["result"] = {}
    return d


def alive_of(conn, mk: str, ips: list[str], now: datetime, live: dict | None = None, live_at: datetime | None = None) -> dict:
    """活著／剛重開／連不上／不知道。即時檢測（30 分鐘內）優先；監控（4.1 告警事件）只用來佐證「連不上」。不猜。"""
    import wug_alert as wa
    mon_down, mon_at = None, None
    try:
        got = wa.for_machine(conn, mk, ips, recent=0)
        dn = [r for r in got.get("current") or [] if r.get("monitor") == wa.DEVICE_MONITOR and r.get("tier") != "maint"]
        if got.get("known"):
            mon_down = bool(dn)
            mon_at = dn[0]["started_at"] if dn else None
    except (sqlite3.Error, KeyError):
        mon_down = None
    snap_at = live_at
    if live is None:
        s = _latest_snapshot(conn, mk)
        if s and (_p(s["at"]) or datetime.min) >= now - timedelta(minutes=LIVE_FRESH_MIN):
            live, snap_at = s["result"], _p(s["at"])
    if live is not None:
        at = _f(snap_at) or live.get("checked_at")
        up = live.get("uptime") if isinstance(live.get("uptime"), dict) else None
        if live.get("reachable") and up and isinstance(up.get("uptime_sec"), (int, float)):
            secs = float(up["uptime_sec"])
            booted = (snap_at or now) - timedelta(seconds=secs)
            days = round(secs / 86400, 1)
            if secs < REBOOT_MIN * 60:
                return {"state": "rebooted", "uptime_days": days, "booted_at": _f(booted), "source": "即時檢測", "at": at,
                        "text": f"剛重開過：{_f(booted)[11:16]} 開機（{int(secs // 60)} 分鐘前）"}
            return {"state": "alive", "uptime_days": days, "booted_at": _f(booted), "source": "即時檢測", "at": at,
                    "text": f"活著・開機 {days:g} 天，沒有重開過" + ("；斷線可能是網路或監控誤判" if mon_down else "")}
        if live.get("reachable"):
            return {"state": "alive", "uptime_days": None, "booted_at": None, "source": "即時檢測", "at": at,
                    "text": "連得上，但這次沒讀到開機時間"}
        if mon_down:
            return {"state": "unreachable", "uptime_days": None, "booted_at": None, "source": "即時檢測", "at": at,
                    "text": f"連不上：即時檢測連不上，監控也從 {mon_at} 起斷線"}
        return {"state": "unknown", "uptime_days": None, "booted_at": None, "source": "即時檢測", "at": at,
                "text": "即時檢測連不上，但監控沒有斷線紀錄；不判斷（可能是收集帳號或網路路徑問題）"}
    if mon_down:
        return {"state": "unknown", "uptime_days": None, "booted_at": None, "source": "監控", "at": mon_at,
                "text": f"監控從 {mon_at} 起斷線；還沒做即時檢測，不判斷"}
    return {"state": "unknown", "uptime_days": None, "booted_at": None, "source": "監控" if mon_down is not None else None, "at": None,
            "text": "最近 30 分鐘沒有即時檢測" + ("，監控沒有斷線紀錄" if mon_down is False else "，也沒有監控資料")}


def contacts_of(conn, k: str, w: _World) -> list[dict]:
    """AP 負責人／SP 負責人／值班／代理／主管。no_source＝沒有資料來源；blank＝有登記但沒填。"""
    import daily_risk
    import oncall
    g = w.idx.groups.get(k) or []
    rows = [dict(r) for r in g]
    inf = w.info(k)
    people: dict[str, dict] = {}
    for s in inf["serials"]:
        for p in conn.execute("SELECT person_name, phone, proxy1, proxy1_phone FROM personnel WHERE asset_serial = ?", (s,)):
            if p["person_name"]:
                people.setdefault(p["person_name"].strip(), dict(p))

    def _ph(name):
        p = people.get((name or "").strip())
        return (p or {}).get("phone") or None

    out = []
    apis = inf["api_ids"]
    if not apis:
        out.append({"role": "AP 負責人", "name": None, "phone": None, "state": "no_source", "note": "這台沒有掛業務系統（APID）"})
    for a in apis:
        b = conn.execute("SELECT ap_owner FROM business_system WHERE api_id = ?", (a,)).fetchone()
        r = conn.execute("SELECT phone, roles_json FROM system_oncall WHERE api_id = ?", (a,)).fetchone() if _has(conn, "system_oncall") else None
        roles = daily_risk._roles_of(r)
        own = (roles.get("負責人") or {})
        nm = (own.get("name") or (b["ap_owner"] if b else None) or "").strip() or None
        out.append({"role": "AP 負責人", "api_id": a, "name": nm, "phone": own.get("phone") or _ph(nm),
                    "state": "ok" if nm else ("blank" if b or r else "no_source")})
        duty = roles.get("值班") or {}
        if r is not None:
            out.append({"role": "值班", "api_id": a, "name": duty.get("name"), "phone": r["phone"] or duty.get("phone"),
                        "state": "ok" if (r["phone"] or duty.get("phone")) else "blank"})
        bak = roles.get("備援") or {}
        if bak:
            out.append({"role": "代理", "api_id": a, "name": bak.get("name"), "phone": bak.get("phone"),
                        "state": "ok" if bak.get("name") or bak.get("phone") else "blank"})
    cust = next((str(r.get("custodian") or "").strip() for r in rows if str(r.get("custodian") or "").strip()), "")
    out.append({"role": "SP 負責人", "name": cust or None, "phone": _ph(cust), "state": "ok" if cust else "blank"})
    for nm, p in people.items():
        if p.get("proxy1"):
            out.append({"role": "代理", "name": p["proxy1"], "phone": p.get("proxy1_phone") or None, "state": "ok", "of": nm})
    out.append({"role": "主管", "name": None, "phone": None, "state": "no_source", "note": "系統沒有主管聯絡資料來源"})
    for c in out:
        if c["name"] == oncall.NOT_REGISTERED:
            c["name"], c["state"] = None, "blank"
    return out


def _events_tonight(conn, k: str, inf: dict, start: datetime, now: datetime) -> list[dict]:
    import wug_alert as wa
    out = []
    try:
        got = wa.for_machine(conn, k, inf["ips"], recent=1000)
        for r in got.get("current", []) + got.get("recent", []):
            if (r.get("ended_at") or _f(now)) >= _f(start):
                out.append({"at": r["started_at"], "what": f"WhatsUp：{r.get('monitor')} {r.get('wug_state') or ''}".strip(),
                            "recovered_at": r.get("ended_at")})
    except sqlite3.Error:
        pass
    if _has(conn, "disk_alert_seen"):
        hk = [f"mk:{k}"] + [f"ip:{ip}" for ip in inf["ips"]]
        for r in conn.execute("SELECT disk, tier, pct, first_at FROM disk_alert_seen WHERE host IN (%s) AND first_at >= ?" % ",".join("?" * len(hk)),
                              (*hk, _f(start))):
            out.append({"at": r["first_at"], "what": f"硬碟 {r['disk']} {r['pct']}%（{r['tier']}）", "recovered_at": None})
    out.sort(key=lambda x: x["at"] or "", reverse=True)
    return out


_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _events_unregistered(conn, key: str, now: datetime, hours: int | None) -> list[dict]:
    """資產庫找不到的主機（只有 IP）：今晚事件改用 IP 對告警與硬碟通知（2.066.28，.14 實機發現：通知過的主機「今晚事件」卻是空的）。
    不是 IP 的 key（主機名）查不到就是空，不猜。"""
    if not _IP_RE.match(str(key or "").strip()):
        return []
    h = min(max(int(hours or hours_setting(conn)), 1), MAX_HOURS)
    return _events_tonight(conn, "", {"ips": [key.strip()]}, now - timedelta(hours=h), now)


def host(conn, key: str, now: datetime | None = None, hours: int | None = None) -> dict:
    import daily_check
    import host_ops
    import night_baseline
    now = now or _now()
    w = _World(conn)
    try:
        info = host_ops.machine_info(conn, key, w.idx)
    except LookupError as exc:
        return {"key": key, "found": False, "reason": str(exc), "name": None, "ip": None, "platform": None,
                "alive": {"state": "unknown", "uptime_days": None, "booted_at": None, "source": None, "at": None, "text": "資產庫找不到這台"},
                "baseline": {"status": "none", "days": night_baseline.DAYS, "reason": str(exc), "changes": [], "same_count": 0, "same_brief": ""},
                "impact": {"hosts": None, "systems": [], "big": False, "vms": [], "vm_data_at": None, "reason": str(exc)},
                "contacts": [], "history": daily_check.host_history(conn, key, None, 7, now.date()),
                "events_tonight": _events_unregistered(conn, key, now, hours)}
    k = info["key"]
    mk = _bare(k)
    inf = w.info(k)
    plat = w.platform(k)
    snap = _latest_snapshot(conn, mk)
    live = snap["result"] if snap and (_p(snap["at"]) or datetime.min) >= now - timedelta(minutes=LIVE_FRESH_MIN) else None
    hours = min(max(int(hours or hours_setting(conn)), 1), MAX_HOURS)
    return {"key": k, "found": True, "name": info["name"], "ip": info["ips"][0] if info["ips"] else None, "ips": info["ips"],
            "platform": plat, "vmware": w.vmware(k),
            "alive": alive_of(conn, mk, info["ips"], now),
            "baseline": night_baseline.diff(conn, k, now, live),
            "impact": impact(conn, w, k, plat),
            "contacts": contacts_of(conn, k, w),
            "history": daily_check.host_history(conn, ",".join(info["ips"]), ",".join(info["serials"]), 7, now.date()),
            "events_tonight": _events_tonight(conn, mk, inf, now - timedelta(hours=hours), now),
            "last_snapshot": {k2: snap[k2] for k2 in ("id", "at", "by", "alive_state", "conclusion")} if snap else None,
            "generated_at": _f(now)}


# ===================================================================== 寫入（D4）
_KEEP = ("ip", "platform", "reachable", "checked_at", "uptime", "disks", "ports", "failed_units", "overall", "error", "os")


def _slim(result: dict) -> dict:
    out = {k: result.get(k) for k in _KEEP if k in result}
    out["disks"] = [{"mount": d.get("mount"), "use_pct": d.get("use_pct")} for d in (result.get("disks") or []) if isinstance(d, dict)][:200]
    out["ports"] = [{"port": p.get("port")} for p in (result.get("ports") or []) if isinstance(p, dict)][:500] if result.get("ports") is not None else None
    out["failed_units"] = [str(x)[:200] for x in (result.get("failed_units") or [])][:200]
    if isinstance(out.get("uptime"), dict):
        out["uptime"] = {k: out["uptime"].get(k) for k in ("uptime_sec", "days", "just_rebooted")}
    if isinstance(out.get("error"), str):
        import host_ops
        out["error"] = host_ops.redact(out["error"])[:300]            # 錯誤訊息可能帶帳號／路徑／token：先遮蔽再截
    return out


SNAP_STR_MAX = 200


def _chk_str(v, what: str) -> None:
    if not isinstance(v, str):
        raise ValueError(f"result.{what} 要是文字")
    if len(v) > SNAP_STR_MAX:
        raise ValueError(f"result.{what} 太長（最多 {SNAP_STR_MAX} 字）")


def _chk_num(v, what: str) -> None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"result.{what} 要是數字")


def _validate_result(r: dict) -> None:
    """快照只收固定欄位、固定型別：字串最長 200、不收巢狀物件。只檢查會存的欄位，其餘欄位 _slim 本來就丟掉。"""
    ip = r.get("ip")
    if not isinstance(ip, str) or not ip.strip():
        raise ValueError("result 沒帶 ip（要是這台即時檢測的結果）")
    _chk_str(ip, "ip")
    for f in ("platform", "checked_at", "os", "overall"):
        if r.get(f) is not None:
            _chk_str(r[f], f)
    if r.get("reachable") is not None and not isinstance(r["reachable"], bool):
        raise ValueError("result.reachable 要是 true／false")
    if r.get("error") is not None and not isinstance(r["error"], str):
        raise ValueError("result.error 要是文字")
    up = r.get("uptime")
    if up is not None:
        if not isinstance(up, dict):
            raise ValueError("result.uptime 要是物件")
        for f in ("uptime_sec", "days"):
            if up.get(f) is not None:
                _chk_num(up[f], f"uptime.{f}")
        if up.get("just_rebooted") is not None and not isinstance(up["just_rebooted"], bool):
            raise ValueError("result.uptime.just_rebooted 要是 true／false")
    for f, sub in (("disks", "mount"), ("ports", "port")):
        v = r.get(f)
        if v is None:
            continue
        if not isinstance(v, list):
            raise ValueError(f"result.{f} 要是清單")
        for x in v:
            if not isinstance(x, dict):
                raise ValueError(f"result.{f} 每一項要是物件")
            if f == "disks":
                if x.get("mount") is not None:
                    _chk_str(x["mount"], "disks.mount")
                if x.get("use_pct") is not None:
                    _chk_num(x["use_pct"], "disks.use_pct")
            elif x.get("port") is not None:
                if isinstance(x["port"], str):
                    _chk_str(x["port"], "ports.port")
                else:
                    _chk_num(x["port"], "ports.port")
    fu = r.get("failed_units")
    if fu is not None:
        if not isinstance(fu, list):
            raise ValueError("result.failed_units 要是清單")
        for x in fu:
            _chk_str(x, "failed_units")


def save_snapshot(conn, key: str, result: dict, by: str, now: datetime | None = None) -> dict:
    """緊急檢查當下結果存一份（誰、何時、結論、與基準差異）。result＝/api/health-check/run 回來的單台 item。不 commit。"""
    import host_ops
    import night_baseline
    if not isinstance(result, dict):
        raise ValueError("result 要是即時檢測的單台結果")
    _validate_result(result)
    now = now or _now()
    info = host_ops.machine_info(conn, key)
    mk = _bare(info["key"])
    ip = str(result.get("ip") or "").strip()
    if ip and info["ips"] and ip not in info["ips"]:
        raise ValueError(f"這份即時檢測是 {ip} 的，不是這台（{'、'.join(info['ips'])}）")
    slim = _slim(result)
    alive = alive_of(conn, mk, info["ips"], now, live=slim, live_at=now)
    base = night_baseline.diff(conn, info["key"], now, slim)
    reds = sum(1 for c in base["changes"] if c["level"] == "red")
    concl = alive["text"] + (f"；跟平常比有 {len(base['changes'])} 項不同（紅 {reds}）" if base["changes"] else
                             ("；跟平常比沒有變化" if base["status"] == "ok" else "；沒有基準可比"))
    ensure(conn)
    cur = conn.execute("INSERT INTO night_check_snapshot (machine_key, ip, at, by, alive_state, conclusion, result_json, baseline_json) "
                       "VALUES (?,?,?,?,?,?,?,?)", (info["key"], ip or (info["ips"][0] if info["ips"] else None), _f(now), by, alive["state"], concl,
                                                    json.dumps(slim, ensure_ascii=False), json.dumps(base, ensure_ascii=False)))
    return {"id": cur.lastrowid, "at": _f(now), "alive": alive, "conclusion": concl, "baseline": base}


def _clean(text: str | None, limit: int) -> str:
    import host_ops
    t = host_ops.redact(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text or ""))).strip()
    if not t:
        raise ValueError("處理結果不可以空白")
    if len(t) > limit:
        raise ValueError(f"處理結果最多 {limit} 字")
    return t


def start_handling(conn, item_id: str, key: str, by: str, now: datetime | None = None) -> dict:
    """我在處理：只能標「今晚通知清單（含舊告警）裡真的有」的那一項，主機用伺服器對到的（不採信前端）。同一項已有人在處理就回那一筆。不 commit。"""
    now = now or _now()
    iid = (item_id or "").strip()
    if not iid or len(iid) > 300:
        raise ValueError("item_id 不對")
    start = now - timedelta(hours=hours_setting(conn)) - timedelta(days=OLDER_DAYS)
    cur = next((x for x in collect(conn, start, now) if x["id"] == iid), None)
    if cur is None:
        raise LookupError("這一項不在通知清單上（可能已超出時間範圍），重新整理後再看")
    mk = cur["machine_key"] or (f"ip:{cur['ip']}" if cur.get("ip") else iid)
    k = (key or "").strip()
    if cur["machine_key"] and k and k != cur["machine_key"]:
        raise ValueError("這一項不是這台主機的通知")
    ensure(conn)
    nat = cur["notified_at"]
    old = conn.execute("SELECT * FROM night_handling WHERE item_id = ? AND done_at IS NULL AND (? IS NULL OR at >= ?) ORDER BY id DESC LIMIT 1",
                       (iid, nat, nat)).fetchone()
    if old:
        return {**dict(old), "existing": True}
    try:
        c = conn.execute("INSERT INTO night_handling (item_id, machine_key, kind, title, notified_at, by, at) VALUES (?,?,?,?,?,?,?)",
                         (iid, mk, cur["kind"], cur["title"][:300], nat, by, _f(now)))
    except sqlite3.IntegrityError:        # 同一項目已有一筆未結案（兩人同時按）：回那一筆
        old = conn.execute("SELECT * FROM night_handling WHERE item_id = ? AND done_at IS NULL ORDER BY id DESC LIMIT 1", (iid,)).fetchone()
        if old is None:
            raise
        return {**dict(old), "existing": True}
    return {**dict(conn.execute("SELECT * FROM night_handling WHERE id = ?", (c.lastrowid,)).fetchone()), "existing": False}


def finish_handling(conn, hid: int, result: str, by: str, now: datetime | None = None) -> dict:
    """寫處理結果＝結案（只能一次；不可改、不可刪）。不 commit。"""
    now = now or _now()
    t = _clean(result, RESULT_MAX)
    if not _has(conn, "night_handling"):
        raise LookupError("找不到這筆處理紀錄")
    r = conn.execute("SELECT * FROM night_handling WHERE id = ?", (hid,)).fetchone()
    if not r:
        raise LookupError("找不到這筆處理紀錄")
    if r["done_at"]:
        raise PermissionError("這筆已經結案（處理結果不能改；要補充請到主機履歷加處理紀錄）")
    cur = conn.execute("UPDATE night_handling SET result = ?, done_by = ?, done_at = ? WHERE id = ? AND done_at IS NULL", (t, by, _f(now), hid))
    if cur.rowcount != 1:                 # 剛好被別人先結案：不能讓兩邊都以為自己寫成功
        raise PermissionError("這筆剛剛已被結案（處理結果不能改；要補充請到主機履歷加處理紀錄）")
    return dict(conn.execute("SELECT * FROM night_handling WHERE id = ?", (hid,)).fetchone())


# ===================================================================== 主機履歷「事件與維運」來源
def events_for_host(conn, info: dict, start: str, end: str) -> tuple[list[dict], list[dict]]:
    """host_ops.build 用：硬碟告警（disk_alert_seen，算事件）、緊急檢查快照、夜間處理紀錄（紀錄，不計次）。回 (events, coverage)。"""
    import host_ops as ho
    mk = _bare(info["key"])
    mks = (mk, "ret:" + mk)               # 停用主機的快照／處理紀錄可能存完整 key（含 ret:），兩種都查
    events, cov = [], []
    # 硬碟告警
    c = {"source": "disk_alert", "label": "硬碟告警（Teams）", "kind": "event_history", "state": "no_data", "rows": 0,
         "covers_from": None, "covers_to": None, "note": ""}
    if _has(conn, "disk_alert_seen"):
        hk = [f"mk:{mk}"] + ([] if info.get("retired") else [f"ip:{ip}" for ip in info["ips"]])
        for r in conn.execute("SELECT host, disk, day, tier, pct, batch_id, first_at FROM disk_alert_seen WHERE host IN (%s) "
                              "AND first_at >= ? AND first_at <= ? ORDER BY first_at" % ",".join("?" * len(hk)), (*hk, start, end)):
            lab = {"critical": "緊急", "warn": "準備", "surge": "暴增"}.get(r["tier"], r["tier"])
            events.append({"id": f"DISKA-{r['host']}-{r['disk']}-{r['day']}-{r['tier']}", "source": "disk_alert", "source_label": "硬碟告警（Teams）",
                           "category": "disk", "category_label": ho.CAT_LABEL["disk"], "partition": r["disk"],
                           "title": f"磁碟 {r['disk']} {lab}", "first_at": r["first_at"], "last_at": r["first_at"], "recovered_at": None,
                           "status": "unknown", "partial_recover": False, "match": "relay", "match_label": ho.MATCH_LABEL["relay"],
                           "countable": True, "metric": r["pct"], "metric_text": f"{r['pct']}%（{r['day']} 資料）",
                           "evidence": [{"k": "磁碟", "v": r["disk"]}, {"k": "使用率", "v": f"{r['pct']}%"}, {"k": "級別", "v": lab},
                                        {"k": "批次", "v": r["batch_id"] or ho.NO_DATA}],
                           "known_start": False, "recovered_label": "硬碟告警沒有恢復紀錄（只記第一次看到）"})
        n = sum(1 for e in events if e["source"] == "disk_alert")
        c.update(state="has_data" if n else "no_machine_data", rows=n,
                 note="" if n else "這段期間沒有這台的硬碟告警（只代表沒超過門檻被通知，不代表一定正常）")
    else:
        c["note"] = "還沒有任何硬碟告警紀錄"
    cov.append(c)
    # 緊急檢查快照
    c2 = {"source": "night_check", "label": "夜間緊急檢查快照", "kind": "event_record", "state": "no_data", "rows": 0,
          "covers_from": None, "covers_to": None, "note": "值班當下存的即時檢測結果，是紀錄不是故障，不計次"}
    n2 = 0
    if _has(conn, "night_check_snapshot"):
        for r in conn.execute("SELECT id, at, by, alive_state, conclusion FROM night_check_snapshot WHERE machine_key IN (?, ?) AND at >= ? AND at <= ? ORDER BY at",
                              (*mks, start, end)):
            n2 += 1
            events.append({"id": f"NIGHTCHK-{r['id']}", "source": "night_check", "source_label": "夜間緊急檢查", "category": "night",
                           "category_label": ho.CAT_LABEL["night"], "partition": "", "title": f"緊急檢查：{r['conclusion'] or ''}"[:200],
                           "first_at": r["at"], "last_at": r["at"], "recovered_at": None, "status": "closed", "partial_recover": False,
                           "match": "relay", "match_label": "值班從這台的緊急檢查頁存的", "countable": False, "metric": None,
                           "metric_text": r["alive_state"] or ho.NO_DATA,
                           "evidence": [{"k": "誰", "v": r["by"] or ho.NO_DATA}, {"k": "結論", "v": r["conclusion"] or ho.NO_DATA}],
                           "known_start": True, "recovered_label": None})
    c2.update(state="has_data" if n2 else "none_recorded", rows=n2)
    cov.append(c2)
    # 我在處理／處理結果
    c3 = {"source": "night_handling", "label": "夜間處理紀錄", "kind": "event_record", "state": "no_data", "rows": 0,
          "covers_from": None, "covers_to": None, "note": "「我在處理」與處理結果，留紀錄不可刪"}
    n3 = 0
    if _has(conn, "night_handling"):
        for r in conn.execute("SELECT * FROM night_handling WHERE machine_key IN (?, ?) AND at >= ? AND at <= ? ORDER BY at", (*mks, start, end)):
            n3 += 1
            mins = _gap(r["notified_at"], r["at"])
            events.append({"id": f"NIGHTH-{r['id']}", "source": "night_handling", "source_label": "夜間處理", "category": "night",
                           "category_label": ho.CAT_LABEL["night"], "partition": "", "title": f"夜間處理：{r['title'] or r['item_id']}"[:200],
                           "first_at": r["at"], "last_at": r["done_at"] or r["at"], "recovered_at": None,
                           "status": "closed" if r["done_at"] else "ongoing", "partial_recover": False, "match": "relay",
                           "match_label": "值班從夜間速查標的", "countable": False, "metric": None,
                           "metric_text": r["result"] or "處理中（還沒寫結果）",
                           "evidence": [{"k": "通知時間", "v": r["notified_at"] or ho.NO_DATA}, {"k": "誰接手", "v": f"{r['by']}（{r['at']}）"},
                                        {"k": "通知到接手", "v": f"{mins} 分鐘" if mins is not None else ho.NO_DATA},
                                        {"k": "處理結果", "v": (f"{r['result']}（{r['done_by']}，{r['done_at']}）" if r["done_at"] else "還沒寫")}],
                           "known_start": True, "recovered_label": None})
    c3.update(state="has_data" if n3 else "none_recorded", rows=n3)
    cov.append(c3)
    return events, cov


def _gap(a, b) -> int | None:
    x, y = _p(a), _p(b)
    if not x or not y:
        return None
    return max(0, int((y - x).total_seconds() // 60))


# ===================================================================== 10.1 月報
def month_summary(conn, month: str) -> dict:
    """本月夜間通知與處理：哪幾台、各幾次、通知到第一次有人處理多久（分）、處理結果；沒人處理的台數。
    「通知」＝Teams 真的送出的（同今晚通知清單同一份規則，同一台合併）。"""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise ValueError("month 要是 YYYY-MM")
    y, m = int(month[:4]), int(month[5:])
    start = datetime(y, m, 1)
    end = datetime(y + (m == 12), (m % 12) + 1, 1) - timedelta(seconds=1)
    try:
        items = collect(conn, start, end)
    except sqlite3.Error as exc:
        return {"count": None, "hosts": [], "unhandled": None, "reason": f"讀不到通知紀錄（{type(exc).__name__}）"}
    items = [x for x in items if start <= (_p(x["notified_at"]) or datetime.min) <= end]
    w = None
    hmap = _handling_map(conn, items)
    g: dict[str, list[dict]] = {}
    for x in items:
        g.setdefault(x["machine_key"] or (f"ip:{x['ip']}" if x.get("ip") else x["id"]), []).append(x)
    hosts = []
    for k, its in g.items():
        if w is None:
            w = _World(conn)
        inf = w.info(k if k in w.idx.groups else None)
        hs = [(x, hmap[x["id"]]) for x in its if x["id"] in hmap]
        first = min((_gap(x["notified_at"], h["at"]) for x, h in hs if _gap(x["notified_at"], h["at"]) is not None), default=None)
        res = [h["result"] for _x, h in sorted(hs, key=lambda t: t[1]["at"]) if h.get("result")]
        hosts.append({"name": inf.get("name") or its[0].get("name"), "ip": its[0].get("ip") or inf.get("ip"), "machine_key": k,
                      "times": len(its), "first_handle_min": first, "result": "；".join(res) if res else None,
                      "handled": bool(hs), "item_ids": [x["id"] for x in its]})
    hosts.sort(key=lambda h: (-h["times"], h["name"] or ""))
    for n, h in enumerate(hosts, 1):
        h["no"] = n
    return {"count": len(items), "hosts": hosts, "unhandled": sum(1 for h in hosts if not h["handled"]), "reason": None}
