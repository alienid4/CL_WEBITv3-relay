"""今日開盤尖峰（09:00～09:30，收盤後回補）（2.045.0／2.045.1）。

資料來源＝跳板機收盤後從 Aria Operations（vCOps）匯出的 ESXi 主機效能檔，走中繼匯入（source=aria_host）。

## 為什麼是檔案、不是直接連 Aria（2026-11-04 NB 實測）
  · 公司裡只有跳板機連得到 vCOps；本系統（.14）和中繼 PC 都連不到，所以本系統不連 Aria、不連任何主機，只讀中繼資料夾。
  · vCOps 保存 5 分鐘一點的歷史 28 天 → 收盤後回補就夠（使用者定的做法；這不是盤中監控），盤中不做任何事。

## 報表規格（使用者定）
  · 對象＝ESXi 主機（範圍在跳板機腳本 aria_probe.ps1 的 -NameLike，manifest 的 scope 照實記下來）。
  · 時段＝09:00～09:30（台北），取 09:00 起、09:30 以前的點（跟跳板機上已和 vCOps 畫面對過的報表同一個口徑）。
  · 三欄 Max／Avg／Min（只算時段內，Aria 缺值的點跳過、不補 0），以 CPU usage 的 Max 排序；首頁列 Top10，可下鑽看全部。
    （2.045.1 GPT 審 B2：原本的 Current 是「檔案裡最新一點」，可能是下午的值，放在開盤表會被誤讀，拿掉。）
  · 指標：cpu＝cpu|usage_average（%）、mem＝mem|usage_average（%）、swap＝mem|swapped_average（KB）。三個都存，畫面目前只列 CPU。
  · 尖峰口徑（2.058.23，使用者 11-09 定「主機組人工報告才是標準」）：人工報告的「CPU Usage (Max)」＝vCOps rollUpType=MAX。
    5 分鐘 AVG 會把尖峰磨平（11-09 esxi169-214：AVG 80.66%、MAX 87.48%＝人工報告）。跳板機 aria_probe v6 起另外帶
    `cpu|usage_average:max`／`mem|usage_average:max`（5 分鐘 MAX）→ 存成 cpu_peak／mem_peak；畫面與 Teams 的「最高」
    有 cpu_peak 就用它（max_at 也跟著它），沒有（舊批次）才退回 AVG 序列的最高。Avg／Min／點數一律仍用 AVG 序列。

## 寫入（relay_import.AriaHostAdapter → apply_batch，同一個交易，不 commit）
  · 一批＝那一天跳板機看到的全部範圍內主機（一台一檔）。同一天收到較新的批次（collected_at 較晚）就整天換掉；較舊的不蓋新的；
    collected_at 相同時以 batch_id 較大的為準（決定性），並在批次紀錄留一句警告（GPT 審 A4）。
  · opening_point：原始點照收到的存（含缺值 null），verify_numbers 拿它獨立重算（GPT 審 A2）；保留 45 天。
  · opening_peak：一天 × 一台 × 一個指標一列（Max／Avg／Min／點數／最高點時間）；範圍內但 Aria 沒有回值的主機也記一列（點數 0）。
  · 主機對資產：Aria 主機名去網域、小寫，跟資產（現役）的**有效主機名**比（machine_identity.effective_hostname，跟 machine_key
    同一個名字來源，GPT 審 A3），對到的登記用 system_stats.machine_key 收成台；唯一一台才算對到，對到多台或沒有就列未匹配，不猜。

## 應有台數（GPT 審 B3）
  · 獨立基準＝資產庫裡同一個範圍的 ESXi：vCenter／RVTools 匯入裡 VM 的「Host」欄出現過的 ESXi，加上資產登記被判成 VMware 平台的主機，
    名字符合 scope 的那些。基準是 0 台（或批次沒帶 scope）就照實寫「應有＝本批列到 N 台（未核對資產）」。

## 影響（GPT 審 B4）
  · 每台 ESXi 帶出上面跑的 VM（最近一次 vCenter／RVTools 匯入的 Host 欄）和那些 VM 掛的業務系統（APID），AP 聯絡五欄照這些 APID 查。

## 讀取（畫面）只讀，不建表、不寫任何東西。
"""
from __future__ import annotations

import fnmatch
import json
import logging
import math
import re
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

_log = logging.getLogger(__name__)

SOURCE = "aria_host"
TPE = timezone(timedelta(hours=8))
WINDOW_START = (9, 0)
WINDOW_MINUTES = 30
TOP_N = 10
BASELINE_DAYS = 20
RAW_KEEP_DAYS = 45
STAT_KEYS = {"cpu|usage_average": "cpu", "mem|usage_average": "mem", "mem|swapped_average": "swap",
             "cpu|usage_average:max": "cpu_peak", "mem|usage_average:max": "mem_peak"}
METRICS = ("cpu", "mem", "swap", "cpu_peak", "mem_peak")
MAX_POINTS = 5000                       # 一台一個指標最多幾點（一天 5 分鐘一點才 288 點，多的就是壞資料）
TITLE = "今日開盤尖峰（09:00～09:30，收盤後回補）"
PENDING_LABEL = "今日資料尚未匯入（收盤後由跳板機送來）"
HOLIDAY_LABEL = "今天休市，不收開盤資料"

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS opening_peak (
        day TEXT NOT NULL, host TEXT NOT NULL, metric TEXT NOT NULL,
        max REAL, avg REAL, min REAL, points INTEGER NOT NULL DEFAULT 0, max_at TEXT,
        resource_id TEXT, machine_key TEXT, match TEXT NOT NULL DEFAULT 'no_asset', candidates INTEGER NOT NULL DEFAULT 0,
        batch_id TEXT NOT NULL, collected_at TEXT NOT NULL, imported_at TEXT NOT NULL,
        PRIMARY KEY (day, host, metric))""",
    """CREATE TABLE IF NOT EXISTS opening_point (
        day TEXT NOT NULL, host TEXT NOT NULL, metric TEXT NOT NULL, ts INTEGER NOT NULL, value REAL,
        PRIMARY KEY (day, host, metric, ts))""",
    """CREATE TABLE IF NOT EXISTS opening_peak_batch (
        day TEXT PRIMARY KEY, batch_id TEXT NOT NULL, collected_at TEXT NOT NULL, data_until TEXT, scope TEXT,
        hosts INTEGER NOT NULL, listed_total INTEGER, note TEXT, imported_at TEXT NOT NULL)""",
    # 開盤摘要第二段（2.059.10，使用者 11-10「做第二段」）：主機組人工報告「VM 使用超過主機實體 CPU 10%」。
    # 一天 × 一台 ESXi × 一台 VM：VM 的 cpu|usagemhz_average（vCOps rollUpType=MAX、5 分鐘）時段內最高 ÷ 主機 cpu|totalCapacity。
    """CREATE TABLE IF NOT EXISTS opening_vm (
        day TEXT NOT NULL, host TEXT NOT NULL, vm TEXT NOT NULL, resource_id TEXT,
        max_mhz REAL, max_at TEXT, points INTEGER NOT NULL DEFAULT 0, cap_mhz REAL, pct REAL,
        machine_key TEXT, match TEXT NOT NULL DEFAULT 'no_asset', batch_id TEXT NOT NULL,
        PRIMARY KEY (day, host, vm))""",
    # 每台 ESXi 的 VM 有沒有讀到：vms NULL＝沒讀到（舊收集程式／讀失敗）——是「不知道」，不是 0 台。
    """CREATE TABLE IF NOT EXISTS opening_vm_host (
        day TEXT NOT NULL, host TEXT NOT NULL, vms INTEGER, cap_mhz REAL, error TEXT, batch_id TEXT NOT NULL,
        PRIMARY KEY (day, host))""",
]
VM_THRESHOLD = 10.0                     # 人工報告：VM 使用超過主機實體 CPU 10%
VM_MAX = 3000                           # 一台 ESXi 最多幾台 VM（多的就是壞資料）
CAP_KEY = "cpu|totalCapacity_average"   # 主機 CPU 總容量（MHz）；aria_probe 早就有抓這個鍵


def ensure(conn) -> None:
    for s in SCHEMA:
        conn.execute(s)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(opening_peak_batch)")}
    for c, t in (("listed_total", "INTEGER"), ("note", "TEXT")):   # 2.045.0 建的表沒有這兩欄（CREATE IF NOT EXISTS 不補欄）
        if c not in cols:
            conn.execute(f"ALTER TABLE opening_peak_batch ADD COLUMN {c} {t}")


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _now() -> datetime:
    import quiet_window as qw
    return qw.local_now()


def window_ms(day: str) -> tuple[int, int]:
    """那天 09:00（台北）到 09:30 的毫秒區間 [起, 迄)。"""
    d = datetime.strptime(day, "%Y-%m-%d").replace(hour=WINDOW_START[0], minute=WINDOW_START[1], tzinfo=TPE)
    a = int(d.timestamp() * 1000)
    return a, a + WINDOW_MINUTES * 60_000


def _hhmm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TPE).strftime("%H:%M")


def _day_of(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TPE).strftime("%Y-%m-%d")


# ===================================================================== 解析（relay_import 呼叫；壞資料丟 ValueError）
_HOST_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,252}$")


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def aggregate(points: list[tuple[int, float | None]], day: str) -> dict:
    """時段內（09:00 起、09:30 以前）非缺值的點：Max／Avg／Min／點數／最高點時間。"""
    a, b = window_ms(day)
    win = sorted((ts, x) for ts, x in points if x is not None and a <= ts < b)
    r = {"max": None, "avg": None, "min": None, "points": len(win), "max_at": None}
    if win:
        vals = [x for _ts, x in win]
        mx = max(vals)
        r.update(max=round(mx, 2), avg=round(sum(vals) / len(vals), 2), min=round(min(vals), 2),
                 max_at=_hhmm(next(ts for ts, x in win if x == mx)))
    return r


def parse_host(data: Any, day: str) -> dict:
    """一台一檔：{"name", "resource_id"?, "metrics": {statKey: {"t": [毫秒…], "v": [值或 null…]}}}。
    回 {"name", "resource_id", "raw": {cpu|mem|swap: [(ts, 值或 None)]}, "metrics": {cpu|mem|swap: 三欄＋點數}}；不認得的 statKey 忽略。
    raw 只留那一天（台北）的點，缺值保留成 None（不補 0）。"""
    if not isinstance(data, dict):
        raise ValueError("最外層要是物件")
    name = data.get("name")
    if not isinstance(name, str) or not _HOST_OK.match(name.strip()):
        raise ValueError("name（ESXi 主機名）缺少或格式不對")
    rid = data.get("resource_id")
    if rid is not None and (not isinstance(rid, str) or len(rid) > 128):
        raise ValueError("resource_id 要是 128 字以內的文字")
    metrics = data.get("metrics")
    if metrics is None:
        metrics = {}
    if not isinstance(metrics, dict):
        raise ValueError("metrics 要是物件")
    raw: dict[str, list] = {}
    out: dict[str, dict] = {}
    for key, m in STAT_KEYS.items():
        s = metrics.get(key)
        if s is None:
            continue
        raw[m] = _series(s, key, day)
        out[m] = aggregate(raw[m], day)
    cap = None
    if metrics.get(CAP_KEY) is not None:
        a, b = window_ms(day)
        cv = [x for ts, x in _series(metrics[CAP_KEY], CAP_KEY, day) if x is not None and x > 0 and a <= ts < b]
        cap = max(cv) if cv else None
    vms = _parse_vms(data.get("vms"), cap, day)
    err = data.get("vms_error")
    err = err[:200] if isinstance(err, str) and err.strip() else None
    return {"name": name.strip(), "resource_id": rid, "raw": raw, "metrics": out, "cap_mhz": cap,
            "vms": vms, "vms_error": err}


def _series(s: Any, key: str, day: str) -> list[tuple[int, float | None]]:
    """{"t": [毫秒…], "v": [值或 null…]} → 那一天（台北）的 [(ts, 值或 None)]，依時間排序。缺值保留 None（不補 0）。"""
    if not isinstance(s, dict):
        raise ValueError(f"{key} 要是物件")
    t, v = s.get("t"), s.get("v")
    if isinstance(t, (int, float)) and not isinstance(t, bool) and not isinstance(v, list):
        t, v = [t], [v]                                    # PowerShell 單一元素陣列可能被攤平
    if not isinstance(t, list) or not isinstance(v, list) or len(t) != len(v):
        raise ValueError(f"{key} 的 t、v 要是一樣長的清單")
    if len(t) > MAX_POINTS:
        raise ValueError(f"{key} 點數超過 {MAX_POINTS}")
    pts: dict[int, float | None] = {}
    for ts, val in zip(t, v):
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(float(ts)):
            raise ValueError(f"{key} 的時間不是毫秒數字")
        if val is not None and _num(val) is None:
            raise ValueError(f"{key} 的值要是數字或 null")
        ts = int(ts)
        if _day_of(ts) != day:
            continue                                       # 不是這一天的點不收
        pts[ts] = _num(val)
    return sorted(pts.items())


def _parse_vms(vms: Any, cap: float | None, day: str) -> list[dict] | None:
    """主機檔裡的 "vms"：[{"name", "resource_id"?, "t", "v"}]，t／v＝VM 的 cpu|usagemhz_average（rollUpType=MAX，MHz）。
    回 None＝這台沒帶 VM（舊收集程式或讀失敗：**不知道**，不是 0 台）。佔主機％＝時段內最高 MHz ÷ 主機容量；沒有容量就是 None。"""
    if vms is None:
        return None
    if isinstance(vms, dict):
        vms = [vms]                                        # PowerShell 單一元素陣列可能被攤平
    if not isinstance(vms, list) or len(vms) > VM_MAX:
        raise ValueError(f"vms 要是 {VM_MAX} 筆以內的清單")
    out, seen = [], set()
    for x in vms:
        if not isinstance(x, dict):
            raise ValueError("vms 每一筆要是物件")
        n = x.get("name")
        if not isinstance(n, str) or not n.strip() or len(n) > 200:
            raise ValueError("vms 的 name 缺少或超過 200 字")
        rid = x.get("resource_id")
        if rid is not None and (not isinstance(rid, str) or len(rid) > 128):
            raise ValueError("vms 的 resource_id 要是 128 字以內的文字")
        if n.strip().lower() in seen:
            continue                                       # 同一台主機同名 VM 只算一次
        seen.add(n.strip().lower())
        ag = aggregate(_series(x, "vms." + n.strip()[:40], day), day)
        pct = round(ag["max"] / cap * 100, 4) if (ag["max"] is not None and cap) else None
        out.append({"name": n.strip(), "resource_id": rid, "max_mhz": ag["max"], "max_at": ag["max_at"],
                    "points": ag["points"], "pct": pct})
    return out


# ===================================================================== 資產對應（不猜）
def _short(h: Any) -> str:
    import system_stats
    return system_stats.strip_domain(str(h or "").strip()).lower()


def asset_index(conn) -> dict[str, set]:
    """現役資產：有效主機名（實測校正後，跟 machine_key 同一個名字來源）的短名 → machine_key 集合。"""
    import machine_identity
    import system_stats
    idx: dict[str, set] = {}
    try:
        rows = conn.execute("SELECT asset_serial, hostname, ip, asset_status FROM hardware").fetchall()
    except sqlite3.OperationalError:
        return idx                                       # 沒有資產表＝沒有可對應的
    for r in rows:
        if system_stats.is_retired_row(r):
            continue
        name = _short(machine_identity.effective_hostname(r["hostname"], r["asset_serial"]))
        if not name:
            continue
        idx.setdefault(name, set()).add(system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"]))
    return idx


def match_host(idx: dict[str, set], name: str) -> tuple[str, str | None, int]:
    keys = idx.get(_short(name)) or set()
    if len(keys) == 1:
        return "matched", next(iter(keys)), 1
    if keys:
        return "ambiguous", None, len(keys)
    return "no_asset", None, 0


_VM_NOTE_PAREN = re.compile(r"\s*[（(][^()（）]*[)）]\s*$")


def match_vm(idx: dict[str, set], name: str) -> tuple[str, str | None, int]:
    """vCOps 的 VM 名稱常帶註記（11-10 跳板機 實測：「EXSVR348-219_AP04」「EXSVR334-013(eGW-004)」），
    資產登記的是主機名 EXSVR348-219。依序試：原名 → 去掉結尾括號註記 → 去掉第一個底線之後；
    第一個「唯一一台」就用；都沒有唯一就照原名的結果（對到多台／沒有），不猜。"""
    first = match_host(idx, name)
    if first[0] == "matched":
        return first
    base = _VM_NOTE_PAREN.sub("", name).strip()
    tries = [base, base.split("_", 1)[0].strip()]
    for t in tries:
        if t and t != name:
            r = match_host(idx, t)
            if r[0] == "matched":
                return r
    return first


def in_scope(name: str, scope: str | None) -> bool:
    if not scope:
        return False
    sc = scope.strip().lower()
    n = str(name or "").strip().lower()
    return fnmatch.fnmatchcase(n, sc) or fnmatch.fnmatchcase(_short(n), sc)


def _vcenter_payloads(conn):
    try:
        # 只取最近一次匯入：RVTools 匯入只 upsert 本次出現的 VM（collected_at 刷新），本次沒出現的舊列不會刪，
        # 用「最新 collected_at 往前 1 天內」當作最近一輪，避免已消失的 ESXi／VM 一直算進應有台數與影響範圍。
        # 11-10：**每座 vCenter 各算各的最近一輪**——vCOps 清單每週一批、RVTools 每月一批，兩個來源時間不同；
        # 用全庫最新時間當基準，只在 RVTools 裡的那座 vCenter 會整座掉出去（應有台數悄悄變少）。
        rows = conn.execute("SELECT payload, resolved_hardware_id, collected_at FROM source_record "
                            "WHERE source = 'vcenter'").fetchall()
    except sqlite3.OperationalError:
        return []                                         # 沒有 vCenter 匯入表＝沒有證據
    groups: dict[str, list] = {}
    for r in rows:
        try:
            vc = str((json.loads(r[0] or "{}") or {}).get("vi_sdk_server") or "").strip().lower()
        except (ValueError, TypeError, AttributeError):
            vc = ""
        # 2.059.15（審查 P3）：同一座 vCenter 有時記 FQDN、有時記短名 → 統一成短名，免得分成兩組各留各的舊資料（IP 不切）
        if vc and not vc.replace(".", "").isdigit():
            vc = vc.split(".")[0]
        groups.setdefault(vc, []).append(r)
    out = []
    for grp in groups.values():
        latest = max((r[2] or "") for r in grp)
        try:
            floor = (datetime.strptime(latest[:19], "%Y-%m-%d %H:%M:%S") - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            floor = ""
        out += [(r[0], r[1]) for r in grp if (r[2] or "") >= floor]
    return out


def expected_hosts(conn, scope: str | None) -> set[str]:
    """應有的獨立基準：資產庫（vCenter／RVTools 匯入的 Host 欄＋登記成 VMware 平台的資產）裡名字符合 scope 的 ESXi 短名。"""
    out: set[str] = set()
    if not scope:
        return out
    for r in _vcenter_payloads(conn):
        try:
            d = json.loads(r[0] or "{}")
        except (ValueError, TypeError):
            continue
        h = d.get("esxi_host") if isinstance(d, dict) else None
        if h and in_scope(str(h), scope):
            out.add(_short(h))
    try:
        import onboard_eligibility as oe
        import system_stats
        for r in conn.execute("SELECT * FROM hardware").fetchall():
            if r["hostname"] and not system_stats.is_retired_row(r) and oe.is_vmware_platform(r) and in_scope(r["hostname"], scope):
                out.add(_short(r["hostname"]))
    except sqlite3.OperationalError:
        pass                                              # 沒有資產表：只用 vCenter 匯入
    return out


def vms_by_esxi(conn) -> dict[str, list[dict]]:
    """ESXi 短名 → 上面跑的 VM（最近一次 vCenter／RVTools 匯入的 Host 欄）：名稱、IP、APID、machine_key。退役的不列。"""
    import system_stats
    hw: dict[int, Any] = {}
    try:
        for r in conn.execute("SELECT id, asset_serial, hostname, ip, api_id, asset_status FROM hardware"):
            hw[r["id"]] = r
    except sqlite3.OperationalError:
        pass
    out: dict[str, list[dict]] = {}
    seen: dict[str, set] = {}
    for r in _vcenter_payloads(conn):
        try:
            d = json.loads(r[0] or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(d, dict) or not d.get("esxi_host"):
            continue
        e = _short(d["esxi_host"])
        a = hw.get(r[1])
        if a is not None and system_stats.is_retired_row(a):
            continue
        name = (a["hostname"] if a is not None and a["hostname"] else None) or d.get("hostname") or d.get("vm_name") or "—"
        ip = (a["ip"] if a is not None and a["ip"] else None) or d.get("ip")
        mk = system_stats.machine_key(a["hostname"], a["ip"], a["asset_serial"]) if a is not None else None
        k = mk or f"n:{name}"
        if k in seen.setdefault(e, set()):
            continue
        seen[e].add(k)
        out.setdefault(e, []).append({"vm": name, "ip": ip, "api_id": ((a["api_id"] or "").strip() if a is not None else "") or None,
                                      "machine_key": mk, "link": ("/v2/machine/" + quote(mk, safe="")) if mk else None})
    for e, lst in out.items():
        lst.sort(key=lambda x: str(x["vm"]).lower())
        for i, x in enumerate(lst, 1):
            x["__no"] = i
    return out


# ===================================================================== 寫入（relay_import 呼叫；不 commit）
def apply_batch(conn, batch_id: str, collected_at: str, day: str, scope: str, data_until: str, hosts: list[dict], now_text: str,
                listed_total: int | None = None) -> dict:
    import relay_import as ri
    ensure(conn)
    pts = sum(((h["metrics"] or {}).get("cpu") or {}).get("points") or 0 for h in hosts)
    stats = {"files": len(hosts), "devices": len(hosts), "points": pts, "matched": 0, "unmatched": 0, "skipped_existing": 0}
    note = None
    cur = conn.execute("SELECT batch_id, collected_at FROM opening_peak_batch WHERE day = ?", (day,)).fetchone()
    if cur is not None:
        old = ri.parse_time(cur[1], "Asia/Taipei")
        new = ri.parse_time(collected_at, "Asia/Taipei")
        if old is not None and new is not None:
            if new < old or (new == old and batch_id <= cur[0]):
                if new == old:
                    note = f"同一個收集時間有兩批（{cur[0]}、{batch_id}），以 batch_id 較大的 {cur[0]} 為準"
                    conn.execute("UPDATE opening_peak_batch SET note = ? WHERE day = ?", (note, day))
                    _log.warning("開盤尖峰 %s：%s", day, note)
                stats.update(skipped_existing=len(hosts), points=0)     # 同一天已經有較新的批次：舊的不蓋新的
                return stats
            if new == old:
                note = f"同一個收集時間有兩批（{cur[0]}、{batch_id}），以 batch_id 較大的 {batch_id} 為準"
                _log.warning("開盤尖峰 %s：%s", day, note)
    conn.execute("DELETE FROM opening_peak WHERE day = ?", (day,))
    conn.execute("DELETE FROM opening_point WHERE day = ?", (day,))
    conn.execute("DELETE FROM opening_vm WHERE day = ?", (day,))
    conn.execute("DELETE FROM opening_vm_host WHERE day = ?", (day,))
    cutoff = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=RAW_KEEP_DAYS)).strftime("%Y-%m-%d")
    conn.execute("DELETE FROM opening_point WHERE day < ?", (cutoff,))
    idx = asset_index(conn)
    for h in hosts:
        res, mk, n = match_host(idx, h["name"])
        if res == "matched":
            stats["matched"] += 1
            conn.execute("DELETE FROM relay_import_unmatched WHERE source = ? AND ident = ?", (SOURCE, h["name"]))
        else:
            stats["unmatched"] += 1
            conn.execute("INSERT INTO relay_import_unmatched (source, ident, name, reason, candidates, last_batch, first_seen, last_seen) "
                         "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source, ident) DO UPDATE SET name = excluded.name, reason = excluded.reason, "
                         "candidates = excluded.candidates, last_batch = excluded.last_batch, last_seen = excluded.last_seen",
                         (SOURCE, h["name"], h["name"][:80], res, n, batch_id, now_text, now_text))
        for m, plist in (h.get("raw") or {}).items():
            conn.executemany("INSERT OR REPLACE INTO opening_point (day, host, metric, ts, value) VALUES (?,?,?,?,?)",
                             [(day, h["name"], m, ts, v) for ts, v in plist])
        ms = h["metrics"] or {}
        for m in METRICS:
            r = ms.get(m)
            if r is None and m != "cpu":
                continue                                 # 沒回這個指標就不記；CPU 一定記一列（算本批列到幾台用）
            r = r or {"max": None, "avg": None, "min": None, "points": 0, "max_at": None}
            conn.execute("INSERT INTO opening_peak (day, host, metric, max, avg, min, points, max_at, resource_id, machine_key, match, "
                         "candidates, batch_id, collected_at, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (day, h["name"], m, r["max"], r["avg"], r["min"], r["points"], r["max_at"], h["resource_id"],
                          mk, res, n, batch_id, collected_at, now_text))
        vl = h.get("vms")
        conn.execute("INSERT OR REPLACE INTO opening_vm_host (day, host, vms, cap_mhz, error, batch_id) VALUES (?,?,?,?,?,?)",
                     (day, h["name"], None if vl is None else len(vl), h.get("cap_mhz"), h.get("vms_error"), batch_id))
        for v in vl or []:
            vres, vmk, _vn = match_vm(idx, v["name"])
            conn.execute("INSERT OR REPLACE INTO opening_vm (day, host, vm, resource_id, max_mhz, max_at, points, cap_mhz, pct, "
                         "machine_key, match, batch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (day, h["name"], v["name"], v["resource_id"], v["max_mhz"], v["max_at"], v["points"],
                          h.get("cap_mhz"), v["pct"], vmk, vres, batch_id))
    conn.execute("INSERT INTO opening_peak_batch (day, batch_id, collected_at, data_until, scope, hosts, listed_total, note, imported_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET batch_id = excluded.batch_id, collected_at = excluded.collected_at, "
                 "data_until = excluded.data_until, scope = excluded.scope, hosts = excluded.hosts, listed_total = excluded.listed_total, "
                 "note = excluded.note, imported_at = excluded.imported_at",
                 (day, batch_id, collected_at, data_until or None, scope or None, len(hosts), listed_total, note, now_text))
    if note:
        stats["warning"] = note
    return stats


# ===================================================================== 讀取（畫面；只讀）
def baseline_days(conn, day: str) -> list[str]:
    """基準用的日子：這天以前、有收到批次的最近 20 天（有批次＝那天是有開盤的工作日）。"""
    if not _has(conn, "opening_peak_batch"):
        return []
    return [r[0] for r in conn.execute("SELECT day FROM opening_peak_batch WHERE day < ? ORDER BY day DESC LIMIT ?", (day, BASELINE_DAYS))]


def _baseline(conn, days: list[str]) -> dict[str, tuple[float, int]]:
    """每台：基準日子裡每天 CPU 最高（有 cpu_peak 用它，否則 AVG 序列的最高；同畫面口徑）的中位數與實際天數。"""
    if not days:
        return {}
    daily: dict[tuple[str, str], float] = {}
    q = (f"SELECT day, host, metric, max FROM opening_peak WHERE metric IN ('cpu', 'cpu_peak') AND points > 0 AND max IS NOT NULL "
         f"AND day IN ({','.join('?' for _ in days)}) ORDER BY metric")          # 'cpu' 先、'cpu_peak' 後 → 後者蓋前者
    for d, host, _m, v in conn.execute(q, days):
        daily[(d, host.lower())] = v
    per: dict[str, list[float]] = {}
    for (_d, h), v in daily.items():
        per.setdefault(h, []).append(v)
    return {h: (round(statistics.median(vs), 2), len(vs)) for h, vs in per.items()}


def _peaks(conn, day: str, metric: str) -> dict[str, tuple]:
    """那天每台的 MAX 序列尖峰（cpu_peak／mem_peak）：host → (max, max_at)。沒點的不列（＝退回 AVG 序列）。"""
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT host, max, max_at FROM opening_peak WHERE day = ? AND metric = ? AND points > 0 AND max IS NOT NULL", (day, metric))}


MATCH_TEXT = {"matched": "已對到", "ambiguous": "對到多台（不猜）", "no_asset": "資產庫沒有這台"}


def sort_key(x: dict):
    """排序口徑：有資料的在前，CPU Max 由大到小，同值依主機名。verify_numbers 另寫一份獨立重算，不呼叫這支。"""
    return (0 if x["has_data"] else 1, -(x["max"] or 0), x["host"].lower())


def rows(conn, day: str, with_vms: bool = True) -> list[dict]:
    """某天範圍內每台一列（CPU 三欄＋點數＋近 20 日基準＋資產對應＋上面跑的 VM）。依 CPU Max 由大到小（沒資料的排最後），項次固定。"""
    if not _has(conn, "opening_peak"):
        return []
    base = _baseline(conn, baseline_days(conn, day))
    mem = {r[0]: r[1] for r in conn.execute("SELECT host, max FROM opening_peak WHERE day = ? AND metric = 'mem'", (day,))}
    mem.update({h: v for h, (v, _at) in _peaks(conn, day, "mem_peak").items()})
    cpk = _peaks(conn, day, "cpu_peak")
    vms = vms_by_esxi(conn) if with_vms else {}
    out = []
    for r in conn.execute("SELECT * FROM opening_peak WHERE day = ? AND metric = 'cpu'", (day,)):
        med, nd = base.get(r["host"].lower(), (None, 0))
        mx, mx_at = cpk.get(r["host"], (r["max"], r["max_at"]))
        has = (bool(r["points"]) and r["max"] is not None) or r["host"] in cpk
        vl = vms.get(_short(r["host"]), [])
        apis = sorted({v["api_id"] for v in vl if v["api_id"]})
        out.append({"host": r["host"], "max": mx, "avg": r["avg"], "min": r["min"],
                    "points": r["points"], "max_at": mx_at, "has_data": has, "mem_max": mem.get(r["host"]),
                    "peak_basis": "max" if r["host"] in cpk else "avg5",
                    "machine_key": r["machine_key"], "match": r["match"], "match_text": MATCH_TEXT.get(r["match"], r["match"]),
                    "baseline": med, "baseline_days": nd,
                    "baseline_text": ("資料不足" if med is None else f"{med:g}（{nd} 日{'，不足 20 日' if nd < BASELINE_DAYS else ''}）"),
                    "delta": round(mx - med, 2) if (has and mx is not None and med is not None) else None,
                    "vms": vl, "vm_count": len(vl), "vm_api_ids": apis,
                    "link": ("/v2/machine/" + quote(r["machine_key"], safe="")) if r["machine_key"] else None})
    out.sort(key=sort_key)
    for i, x in enumerate(out, 1):
        x["__no"] = i
    return out


def attach_contacts(conn, rs: list[dict]) -> list[dict]:
    """AP 聯絡五欄：查這台 ESXi 自己掛的 APID，加上它上面跑的 VM 掛的 APID（影響哪些系統、找誰）。都沒有就照實「未提供」。"""
    import alert_contact
    by_mk = alert_contact.api_ids_by_machine(conn)
    for r in rs:
        own = by_mk.get(r["machine_key"] or "", [])
        r["api_ids"] = list(dict.fromkeys(list(own) + list(r.get("vm_api_ids") or [])))
    alert_contact.attach(conn, rs)
    for r in rs:
        if not r["api_ids"]:
            r["c_note"] = ("這台 ESXi 上面沒有查到掛業務系統（APID）的 VM，聯絡資訊未提供" if r.get("vm_count")
                           else "vCenter 匯入裡沒有這台 ESXi 上的 VM，聯絡資訊未提供")
    return rs


def _asset_info(conn, keys: set[str]) -> dict[str, dict]:
    """machine_key → 用途／使用單位／保管人／APID（同一台多筆登記：每欄取第一個有值的）。"""
    import system_stats
    out: dict[str, dict] = {}
    if not keys:
        return out
    try:
        rs = conn.execute("SELECT asset_serial, hostname, ip, asset_purpose, asset_name, usage_unit, custodian, user_name, api_id "
                          "FROM hardware").fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rs:
        k = system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        if k not in keys:
            continue
        d = out.setdefault(k, {"purpose": None, "unit": None, "keeper": None, "api_ids": []})
        d["purpose"] = d["purpose"] or (r["asset_purpose"] or "").strip() or (r["asset_name"] or "").strip() or None
        d["unit"] = d["unit"] or (r["usage_unit"] or "").strip() or None
        d["keeper"] = d["keeper"] or (r["custodian"] or "").strip() or (r["user_name"] or "").strip() or None
        a = (r["api_id"] or "").strip()
        if a and a not in d["api_ids"]:
            d["api_ids"].append(a)
    return out


def vm_rows(conn, day: str, threshold: float = VM_THRESHOLD) -> dict:
    """開盤摘要第二段：佔主機實體 CPU ≥ 門檻的 VM（由高到低），帶用途／部門／負責人。

    部門＝業務系統聯絡的「單位」，沒有才用資產的使用單位；負責人＝業務系統的 AP 團隊，沒有才用資產的保管人；都沒有寫「未提供」。
    hosts_with_vms＝有讀到 VM 的 ESXi 台數；0 台＝這批沒帶 VM（不知道），呼叫端不可以寫成「沒有 VM 超過」。
    """
    import alert_contact
    empty = {"rows": [], "threshold": threshold, "hosts_total": 0, "hosts_with_vms": 0, "missing_hosts": [], "no_cap_hosts": []}
    if not _has(conn, "opening_vm_host"):
        return empty
    hs = conn.execute("SELECT host, vms, cap_mhz FROM opening_vm_host WHERE day = ? ORDER BY host", (day,)).fetchall()
    if not hs:
        return empty
    got = [h for h in hs if h["vms"] is not None]
    rs = [dict(r) for r in conn.execute("SELECT * FROM opening_vm WHERE day = ? AND pct IS NOT NULL AND pct >= ? "
                                        "ORDER BY pct DESC, vm", (day, threshold))]
    info = _asset_info(conn, {r["machine_key"] for r in rs if r["machine_key"]})
    for r in rs:
        a = info.get(r["machine_key"] or "", {})
        r["api_ids"] = a.get("api_ids") or []
    alert_contact.attach(conn, rs)
    np = alert_contact.NOT_PROVIDED
    out = []
    for i, r in enumerate(rs, 1):
        a = info.get(r["machine_key"] or "", {})
        out.append({"__no": i, "vm": r["vm"], "host": r["host"], "pct": round(r["pct"], 2), "max_mhz": r["max_mhz"], "max_at": r["max_at"],
                    "machine_key": r["machine_key"], "match": r["match"], "match_text": MATCH_TEXT.get(r["match"], r["match"]),
                    "link": ("/v2/machine/" + quote(r["machine_key"], safe="")) if r["machine_key"] else None,
                    "system": r["c_system_name"] if r["c_system_name"] != np else None,
                    "purpose": a.get("purpose") or np,
                    "dept": r["c_unit"] if r["c_unit"] != np else (a.get("unit") or np),
                    "owner": r["c_ap_team"] if r["c_ap_team"] != np else (a.get("keeper") or np)})
    return {"rows": out, "threshold": threshold, "hosts_total": len(hs), "hosts_with_vms": len(got),
            "missing_hosts": [h["host"] for h in hs if h["vms"] is None],
            "no_cap_hosts": [h["host"] for h in got if not h["cap_mhz"]]}


def _is_trading_day(conn, d) -> bool:
    import quiet_window as qw
    if d.weekday() >= 5:
        return False
    try:
        return d.isoformat() not in qw.get_config(conn)["holidays"]
    except Exception:  # noqa: BLE001 - 休市日設定讀不到：當工作日（只影響「尚未匯入／休市」哪一句）
        return True


def coverage(conn, day: str, rs: list[dict], scope: str | None) -> dict:
    """應有＝資產庫同範圍 ESXi（獨立基準）；涵蓋＝基準裡有 09:00～09:30 資料的台數。基準 0 台就照實寫「未核對資產」。"""
    exp = expected_hosts(conn, scope)
    listed = {_short(r["host"]) for r in rs}
    with_data = {_short(r["host"]) for r in rs if r["has_data"]}
    if exp:
        return {"expected": len(exp), "expected_basis": "asset", "listed": len(listed),
                "covered": len(exp & with_data), "missing": sorted(exp - listed), "extra": sorted(listed - exp),
                "expected_label": f"應有 {len(exp)} 台（資產庫／vCenter 匯入同範圍 ESXi）"}
    return {"expected": len(listed), "expected_basis": "batch", "listed": len(listed), "covered": len(with_data),
            "missing": [], "extra": [], "expected_label": f"應有＝本批列到 {len(listed)} 台（未核對資產）"}


HOT_PCT = 80          # 首頁「超過 80%：N 台」（使用者 11-10 卡片改版）；比較不四捨五入
SCOPE_LABELS_KEY = "opening_scope_labels"          # app_settings：{"esxi169-*": "板橋 vCenter"}，沒設就用下面預設
_DEFAULT_SCOPE_LABELS = {"esxi169-*": "板橋 vCenter"}


def scope_label(conn, scope: str | None) -> str:
    """範圍的白話名稱（使用者 11-10：要標明是哪一座 vCenter，不要讓人以為是全公司）。對不到就照原樣寫範圍字串。"""
    labels = dict(_DEFAULT_SCOPE_LABELS)
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SCOPE_LABELS_KEY,)).fetchone()
        if r and r[0]:
            labels.update(json.loads(r[0]) or {})
    except (sqlite3.Error, ValueError, TypeError):
        pass          # 對照表讀不到＝用預設名稱；只影響範圍的顯示文字，不影響任何數字
    sc = (scope or "").strip()
    return f"{labels[sc]}（{sc}）" if sc in labels else (sc or "—")


def summary(conn, now: datetime | None = None) -> dict:
    """首頁右欄。今天的批次還沒到：寫「尚未匯入」，不放數字。"""
    now = now or _now()
    day = now.strftime("%Y-%m-%d")
    last = None
    b = None
    if _has(conn, "opening_peak_batch"):
        b = conn.execute("SELECT * FROM opening_peak_batch WHERE day = ?", (day,)).fetchone()
        r = conn.execute("SELECT MAX(day) FROM opening_peak_batch WHERE day < ?", (day,)).fetchone()
        last = r[0] if r else None
    if b is None:
        trading = _is_trading_day(conn, now.date())
        out = {"status": "pending" if trading else "holiday", "label": PENDING_LABEL if trading else HOLIDAY_LABEL,
               "as_of": None, "day": day, "last_day": last, "title": TITLE}
        # 2.060.19（S5）：今天還沒到時附上「上一筆」整份（同一份計算），首頁灰字標「MM-DD 的資料，今日尚未匯入」；
        # status 仍是 pending／holiday，今天的批次一到就換成今天的。
        if last:
            pb = conn.execute("SELECT * FROM opening_peak_batch WHERE day = ?", (last,)).fetchone()
            if pb is not None:
                out["prev"] = _day_summary(conn, last, last_before(conn, last), pb)
        return out
    return _day_summary(conn, day, last, b)


def last_before(conn, day: str) -> str | None:
    r = conn.execute("SELECT MAX(day) FROM opening_peak_batch WHERE day < ?", (day,)).fetchone()
    return r[0] if r else None


def _day_summary(conn, day: str, last: str | None, b) -> dict:
    rs = rows(conn, day)
    attach_contacts(conn, rs[:TOP_N])
    nd = len(baseline_days(conn, day))
    until = b["data_until"] or b["collected_at"]
    t = (str(until)[11:16] if until and len(str(until)) >= 16 else None)
    cov = coverage(conn, day, rs, b["scope"])
    return {"status": "ok", "label": None, "title": TITLE, "day": day, "last_day": last, "as_of": t, "collected_at": b["collected_at"],
            "batch_id": b["batch_id"], "scope": b["scope"], "note": b["note"], "listed_total": b["listed_total"],
            "resolution": "5 分鐘一點", "window": "09:00～09:30", **cov,
            "peak_basis": ("最高＝vCOps 最高值（同主機組報告）" if any(x.get("peak_basis") == "max" for x in rs)
                           else "最高＝5 分鐘平均的最高（這批沒有帶 vCOps 最高值）"),
            "unmatched": sum(1 for r in rs if r["match"] != "matched"),
            "scope_label": scope_label(conn, b["scope"]), "hot_pct": HOT_PCT,
            "hot": sum(1 for r in rs if r["has_data"] and r["max"] is not None and r["max"] > HOT_PCT),
            "baseline_days": nd, "baseline_label": (f"近 {BASELINE_DAYS} 個工作日" if nd >= BASELINE_DAYS else f"目前只有 {nd} 個工作日（不足 {BASELINE_DAYS} 日）"),
            "top": {"rows": rs[:TOP_N], "total": len(rs)}, "vm": vm_rows(conn, day)}
