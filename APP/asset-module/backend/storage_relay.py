"""Dell Unity 中繼匯入（devkit D2/D3 的 WEBIT3 端）：陣列／Pool／LUN／主機／主機↔LUN 的快照，與每日 Pool 容量。

資料來源：中繼 PC 跑 `AI/tools_relay/unity_collect.ps1`（只發 GET），每台陣列一個 `unity_<管理位址>.json`，
經共用資料夾由 `relay_import.UnityAdapter` 驗證後呼叫這裡寫入。本模組不連任何設備。

## 寫入規則

* **快照、以陣列為單位**：一個檔＝一台陣列（以系統序號 serialNumber 識別）在那個時間點的完整清單。
  新批次匯入時，該陣列的 Pool／LUN／主機／對應**整份換掉**（Unity 上刪掉的 LUN 不會殘留）；別台陣列不動。
  比目前資料更舊的批次（collected_at 較早）不蓋掉較新的，記「較舊略過」。
* **每日 Pool 容量**（storage_pool_daily）：每個 Pool 每天一列（台北日期），同一天後到的蓋先到的；不刪舊日，做趨勢用。
* **主機對應到 WEBIT3 機器**：腳本沒有收 initiator（IQN／WWN），只能用主機名對。主機名用 machine_identity 的
  正規化（小寫、去網域留短名），通用名（localhost、template…）不對；候選資產用 `system_stats.machine_key` 判斷是不是
  同一台：剛好一台＝對到，多台＝對到多台不同機器（不猜），沒有＝找不到。**不自寫 (host, ip) 去重、不自動建資產。**
  對不到的同時寫進中繼匯入的「未匹配」清單（source='unity'，識別＝<陣列序號>/<主機 id>）。
* Unity 的 host.type 2（Subnet）、3（NetGroup）是網段／網路群組，不是一台主機，標「非主機」不列對不到。
  （列舉值依 Unity REST 公開文件，**未實機驗證**。）
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS storage_array (
        serial TEXT PRIMARY KEY, name TEXT, model TEXT, management_host TEXT,
        collected_at TEXT, batch_id TEXT, imported_at TEXT,
        pool_count INTEGER, lun_count INTEGER, host_count INTEGER, map_count INTEGER,
        size_total INTEGER, size_used INTEGER, size_free INTEGER)""",
    """CREATE TABLE IF NOT EXISTS storage_pool (
        serial TEXT NOT NULL, pool_id TEXT NOT NULL, name TEXT,
        size_total INTEGER, size_used INTEGER, size_free INTEGER,
        PRIMARY KEY (serial, pool_id))""",
    """CREATE TABLE IF NOT EXISTS storage_lun (
        serial TEXT NOT NULL, lun_id TEXT NOT NULL, name TEXT, pool_id TEXT,
        size_total INTEGER, size_used INTEGER, size_allocated INTEGER,
        PRIMARY KEY (serial, lun_id))""",
    """CREATE TABLE IF NOT EXISTS storage_host (
        serial TEXT NOT NULL, host_id TEXT NOT NULL, name TEXT, host_type INTEGER,
        match_status TEXT, asset_serial TEXT, candidates INTEGER,
        PRIMARY KEY (serial, host_id))""",
    """CREATE TABLE IF NOT EXISTS storage_host_map (
        serial TEXT NOT NULL, lun_id TEXT NOT NULL, host_id TEXT NOT NULL, access_mask INTEGER,
        PRIMARY KEY (serial, lun_id, host_id))""",
    """CREATE TABLE IF NOT EXISTS storage_pool_daily (
        serial TEXT NOT NULL, pool_id TEXT NOT NULL, day TEXT NOT NULL, name TEXT,
        size_total INTEGER, size_used INTEGER, size_free INTEGER, batch_id TEXT,
        PRIMARY KEY (serial, pool_id, day))""",
]
TABLES = ("storage_array", "storage_pool", "storage_lun", "storage_host", "storage_host_map", "storage_pool_daily")
SOURCE = "unity"
_log = logging.getLogger(__name__)

MAX_ROWS = 20_000
MAX_INT = 2 ** 63 - 1
_HOST_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,251}$")      # 與腳本 -ManagementHost 檢查同一條
_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")         # Unity 物件 id：pool_1、sv_12、Host_3
_SERIAL_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
TEXT_MAX = 200

#: Unity HostTypeEnum（公開文件；未實機驗證）。2／3 不是一台主機。
HOST_TYPE = {0: "未知", 1: "手動建立", 2: "子網路", 3: "網路群組", 4: "RPA", 5: "自動註冊"}
NOT_HOST_TYPES = {2, 3}
#: Unity BlockHostAccessEnum（公開文件；未實機驗證）。0＝沒有存取權，匯入時略過。
ACCESS = {1: "正式", 2: "快照", 3: "正式＋快照"}
#: Pool 使用率預警門檻（%）：≥ 這個值列進「Pool 使用率 ≥80%」卡片。畫面 InfoNote 說明；verify_numbers 用同一個常數獨立重算。
POOL_WARN_PCT = 80
#: 趨勢：最近幾天的每日容量算成長；少於 TREND_MIN_DAYS 天不估「幾天後滿」（資料不足）。
TREND_WINDOW_DAYS = 30
TREND_MIN_DAYS = 7
#: 管理員接受空快照：app_settings 這個鍵寫陣列序號，下一次匯入該陣列的空快照照收、用掉即清空（只生效一次），批次備註記一筆。
ACCEPT_EMPTY_KEY = "storage_unity_accept_empty"
EMPTY_ALERT = "Unity 回報 0 個 {what}，已保留舊資料"
MATCH_LABEL = {"matched": "已對到", "no_asset": "找不到資產", "ambiguous": "對到多台不同機器",
               "generic_name": "主機名太通用", "not_host": "非主機"}


def ensure(conn) -> None:
    for s in SCHEMA:
        conn.execute(s)


def tables_ready(conn) -> bool:
    """Unity 表與中繼匯入表都在（verify_numbers 用：唯讀，不建表）。"""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return all(t in have for t in (*TABLES, "relay_import_batches", "relay_import_unmatched"))


# ---------------------------------------------------------------- 驗證（單檔）
def _as_list(v: Any, what: str, allow_null: bool = True) -> list:
    """腳本是 Windows PowerShell 5.1 的 ConvertTo-Json：陣列偶爾被包成 {"value": [...], "Count": n}、單筆可能變成物件。
    這兩種寫法照原意展開；其他形狀一律不收。"""
    if v is None:
        if allow_null:
            return []
        raise ValueError(f"缺少 {what}")
    if isinstance(v, dict) and "value" in v and set(v) <= {"value", "Count"}:
        v = v["value"]
        if v is None:
            return []
    if isinstance(v, dict):
        v = [v]
    if not isinstance(v, list):
        raise ValueError(f"{what} 要是清單")
    if len(v) > MAX_ROWS:
        raise ValueError(f"{what} 超過 {MAX_ROWS} 筆")
    for r in v:
        if not isinstance(r, dict):
            raise ValueError(f"{what} 的每一筆要是物件")
    return v


def _text(v: Any, what: str) -> str:
    if v is None:
        return ""
    if not isinstance(v, str) or len(v) > TEXT_MAX:
        raise ValueError(f"{what} 要是 {TEXT_MAX} 字以內的文字")
    return re.sub(r"[\x00-\x1f\x7f]", "", v).strip()


def _id(v: Any, what: str) -> str:
    if isinstance(v, int) and not isinstance(v, bool):
        v = str(v)
    if not isinstance(v, str) or not _ID_OK.match(v.strip()):
        raise ValueError(f"{what} 的 id 不合法")
    return v.strip()


def _ref(v: Any, what: str) -> str:
    """Unity 的關聯欄位：{"id": "..."}。"""
    if isinstance(v, dict):
        v = v.get("id")
    return _id(v, what)


def _size(v: Any, what: str, required: bool = True) -> int | None:
    if v is None and not required:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > MAX_INT:
        raise ValueError(f"{what} 要是 0 以上的整數（位元組）")
    return v


def _unique(rows: list[dict], what: str) -> None:
    seen: set[str] = set()
    for r in rows:
        if r["id"] in seen:
            raise ValueError(f"{what} 的 id {r['id']} 重複")
        seen.add(r["id"])


def parse_file(data: Any) -> dict:
    """驗證一個 unity_<位址>.json，回整理好的 dict；格式不對丟 ValueError（呼叫端轉成整批拒收）。"""
    if not isinstance(data, dict):
        raise ValueError("最外層要是物件")
    if data.get("source", SOURCE) != SOURCE:
        raise ValueError("source 不是 unity")
    if "schema_version" not in data or str(data.get("schema_version")) != "1":
        raise ValueError("schema_version 缺少或不是 1")
    mh = data.get("management_host")
    if not isinstance(mh, str) or not _HOST_OK.match(mh):
        raise ValueError("management_host 要是單純的主機名或 IPv4")
    if data.get("complete") is not True:
        raise ValueError("complete 缺少或不是 true（不完整的快照不收，免得把沒收到的當成已刪除）")
    systems = _as_list(data.get("system"), "system", allow_null=False)
    if len(systems) != 1:
        raise ValueError(f"system 要剛好一筆（收到 {len(systems)} 筆）")
    s = systems[0]
    serial = s.get("serialNumber")
    if not isinstance(serial, str) or not _SERIAL_OK.match(serial.strip()):
        raise ValueError("system.serialNumber 缺少或不合法")
    out: dict[str, Any] = {"serial": serial.strip(), "name": _text(s.get("name"), "system.name"),
                           "model": _text(s.get("model"), "system.model"), "management_host": mh}
    pools = []
    for r in _as_list(data.get("pools"), "pools"):
        pools.append({"id": _id(r.get("id"), "pool"), "name": _text(r.get("name"), "pool.name"),
                      "total": _size(r.get("sizeTotal"), "pool.sizeTotal"), "used": _size(r.get("sizeUsed"), "pool.sizeUsed"),
                      "free": _size(r.get("sizeFree"), "pool.sizeFree")})
    _unique(pools, "pools")
    hosts = []
    for r in _as_list(data.get("hosts"), "hosts"):
        t = r.get("type")
        if t is not None and (isinstance(t, bool) or not isinstance(t, int)):
            raise ValueError("host.type 要是整數")
        hosts.append({"id": _id(r.get("id"), "host"), "name": _text(r.get("name"), "host.name"), "type": t})
    _unique(hosts, "hosts")
    host_ids = {h["id"] for h in hosts}
    luns = []
    for r in _as_list(data.get("luns"), "luns"):
        lid = _id(r.get("id"), "lun")
        access: dict[str, int | None] = {}
        for a in _as_list(r.get("hostAccess"), f"lun {lid} 的 hostAccess"):
            hid = _ref(a.get("host"), f"lun {lid} 的 hostAccess.host")
            mask = a.get("accessMask")
            if mask is not None and (isinstance(mask, bool) or not isinstance(mask, int) or not 0 <= mask <= 255):
                raise ValueError(f"lun {lid} 的 accessMask 要是整數")
            if mask == 0:
                continue                           # 沒有存取權（NoAccess）＝沒在用
            if hid not in host_ids:
                raise ValueError(f"lun {lid} 指到主機清單沒有的 {hid}（收集途中清單變動？下一次收集再匯）")
            access[hid] = mask
        pool = r.get("pool")
        luns.append({"id": lid, "name": _text(r.get("name"), "lun.name"), "total": _size(r.get("sizeTotal"), "lun.sizeTotal"),
                     "used": _size(r.get("sizeUsed"), "lun.sizeUsed", required=False),
                     "allocated": _size(r.get("sizeAllocated"), "lun.sizeAllocated", required=False),
                     "pool_id": _ref(pool, f"lun {lid} 的 pool") if pool is not None else None, "access": access})
    _unique(luns, "luns")
    pool_ids = {p["id"] for p in pools}
    for lun in luns:
        if lun["pool_id"] is not None and lun["pool_id"] not in pool_ids:
            raise ValueError(f"lun {lun['id']} 指到 Pool 清單沒有的 {lun['pool_id']}")
    out.update(pools=pools, luns=luns, hosts=hosts)
    return out


# ---------------------------------------------------------------- 主機 → WEBIT3 機器
def host_index(conn) -> dict[str, list[tuple]]:
    """現役資產依「正規化主機名」分組：{短名: [(hostname, ip, asset_serial), ...]}。退役不參與（備查不是資產）。"""
    import machine_identity as mi
    import system_stats

    try:
        rows = conn.execute("SELECT * FROM hardware").fetchall()
    except sqlite3.OperationalError as exc:
        # 只有「沒有資產表」（舊庫）當成沒有可以對的；其他錯誤往上丟，整批 failed、下一輪重試，不讓未匹配清單被灌爆
        if "no such table" not in str(exc).lower():
            raise
        _log.warning("Unity 匯入：資產表不存在，主機一律列找不到資產（%s）", exc)
        return {}
    idx: dict[str, list[tuple]] = {}
    for r in rows:
        if system_stats.is_retired_row(r):
            continue
        h, ip, sn = r["hostname"], r["ip"], r["asset_serial"]
        names = {mi.norm_name(h), mi.norm_name(mi.effective_hostname(h, sn))}
        for n in names - {""}:
            idx.setdefault(n, []).append((h, ip, sn))
    return idx


def match_host(name: str, idx: dict[str, list[tuple]]) -> tuple[str, str | None, int]:
    """回 (結果, 資產序號, 候選機器數)。同一台的判斷只用 machine_key。"""
    import machine_identity as mi
    import system_stats

    n = mi.norm_name(name)
    if mi.is_generic(n):
        return "generic_name", None, 0
    rows = idx.get(n) or []
    if not rows:
        return "no_asset", None, 0
    groups: dict[str, list[str]] = {}
    for h, ip, sn in rows:
        groups.setdefault(system_stats.machine_key(h, ip, sn), []).append(sn or "")
    if len(groups) == 1:
        return "matched", min(next(iter(groups.values()))) or None, 1
    return "ambiguous", None, len(groups)


# ---------------------------------------------------------------- 寫入（呼叫端負責交易）
def _taipei_day(collected_at: str) -> str:
    import relay_import as ri

    t = ri.parse_time(collected_at, "Asia/Taipei")
    if t is None:
        raise ri.RelayReject("manifest_invalid", "collected_at 不是合法的時間，無法決定每日容量是哪一天")
    return t.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def apply_arrays(conn, arrays: list[dict], collected_at: str, batch_id: str, now_text: str) -> dict:
    """把一批（每項＝parse_file 的結果）寫進去。同一交易由 relay_import.import_batch 提交／回滾。"""
    import relay_import as ri

    ensure(conn)
    new_t = ri.parse_time(collected_at, "Asia/Taipei")
    day = _taipei_day(collected_at)
    idx = host_index(conn)
    from db import get_setting, set_setting

    st = {"devices": 0, "pools": 0, "luns": 0, "hosts": 0, "maps": 0, "matched": 0, "unmatched": 0, "older_skipped": 0,
          "accepted_empty": []}
    for a in arrays:
        sn = a["serial"]
        old = conn.execute("SELECT collected_at, pool_count, lun_count FROM storage_array WHERE serial = ?", (sn,)).fetchone()
        old_t = ri.parse_time(old[0], "Asia/Taipei") if old and old[0] else None
        if old_t and new_t and old_t > new_t:
            st["older_skipped"] += 1                 # 較舊的批次不蓋掉較新的快照
            continue
        # 原本有資料、這次 REST 回空清單：多半是暫時性的空回應，不可以把整台陣列的快照清空（整批拒收，下一次再匯）
        empty = None
        if old is not None and (old[1] or 0) > 0 and not a["pools"]:
            empty = ("Pool", old[1])
        elif old is not None and (old[2] or 0) > 0 and a["pools"] and not a["luns"]:
            empty = ("LUN", old[2])
        if empty:
            if (get_setting(conn, ACCEPT_EMPTY_KEY, "") or "").strip() == sn:
                set_setting(conn, ACCEPT_EMPTY_KEY, "", commit=False)       # 用掉即清空；跟這批同一個交易
                st["accepted_empty"].append(sn)
            else:
                raise ri.RelayReject("empty_snapshot", EMPTY_ALERT.format(what=empty[0]) +
                                     f"（陣列 {sn} 原本有 {empty[1]} 個 {empty[0]}；疑似暫時性的空回應，整批不收。"
                                     f"確定真的清空了，由管理員在 5.2 Unity 陣列按「接受空快照」）")
        for t in ("storage_pool", "storage_lun", "storage_host", "storage_host_map"):
            conn.execute(f"DELETE FROM {t} WHERE serial = ?", (sn,))
        for p in a["pools"]:
            conn.execute("INSERT INTO storage_pool VALUES (?,?,?,?,?,?)", (sn, p["id"], p["name"], p["total"], p["used"], p["free"]))
            conn.execute("INSERT INTO storage_pool_daily VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(serial, pool_id, day) DO UPDATE SET "
                         "name = excluded.name, size_total = excluded.size_total, size_used = excluded.size_used, "
                         "size_free = excluded.size_free, batch_id = excluded.batch_id",
                         (sn, p["id"], day, p["name"], p["total"], p["used"], p["free"], batch_id))
        maps = 0
        keep: set[str] = set()
        for lun in a["luns"]:
            conn.execute("INSERT INTO storage_lun VALUES (?,?,?,?,?,?,?)",
                         (sn, lun["id"], lun["name"], lun["pool_id"], lun["total"], lun["used"], lun["allocated"]))
            for hid, mask in lun["access"].items():
                conn.execute("INSERT INTO storage_host_map VALUES (?,?,?,?)", (sn, lun["id"], hid, mask))
                maps += 1
        for h in a["hosts"]:
            if h["type"] in NOT_HOST_TYPES:
                res, asset, cand = "not_host", None, 0
            else:
                res, asset, cand = match_host(h["name"], idx)
            conn.execute("INSERT INTO storage_host VALUES (?,?,?,?,?,?,?)", (sn, h["id"], h["name"], h["type"], res, asset, cand))
            if res == "matched":
                st["matched"] += 1
            elif res != "not_host":
                st["unmatched"] += 1
                keep.add(f"{sn}/{h['id']}")
                conn.execute("INSERT INTO relay_import_unmatched (source, ident, name, reason, candidates, last_batch, first_seen, last_seen) "
                             "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source, ident) DO UPDATE SET name = excluded.name, reason = excluded.reason, "
                             "candidates = excluded.candidates, last_batch = excluded.last_batch, last_seen = excluded.last_seen",
                             (SOURCE, f"{sn}/{h['id']}", (h["name"] or h["id"])[:80], res, cand, batch_id, now_text, now_text))
        # 這台陣列在未匹配清單裡、這次已對到或已不在的移除（保留仍對不到的 first_seen）
        for (ident,) in conn.execute("SELECT ident FROM relay_import_unmatched WHERE source = ? AND substr(ident, 1, ?) = ?",
                                     (SOURCE, len(sn) + 1, sn + "/")).fetchall():
            if ident not in keep:
                conn.execute("DELETE FROM relay_import_unmatched WHERE source = ? AND ident = ?", (SOURCE, ident))
        tot = [sum(p[k] for p in a["pools"]) for k in ("total", "used", "free")]
        conn.execute("INSERT INTO storage_array VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(serial) DO UPDATE SET "
                     "name = excluded.name, model = excluded.model, management_host = excluded.management_host, "
                     "collected_at = excluded.collected_at, batch_id = excluded.batch_id, imported_at = excluded.imported_at, "
                     "pool_count = excluded.pool_count, lun_count = excluded.lun_count, host_count = excluded.host_count, "
                     "map_count = excluded.map_count, size_total = excluded.size_total, size_used = excluded.size_used, "
                     "size_free = excluded.size_free",
                     (sn, a["name"], a["model"], a["management_host"], collected_at, batch_id, now_text,
                      len(a["pools"]), len(a["luns"]), len(a["hosts"]), maps, *tot))
        st["devices"] += 1
        st["pools"] += len(a["pools"])
        st["luns"] += len(a["luns"])
        st["hosts"] += len(a["hosts"])
        st["maps"] += maps
    return st


# ---------------------------------------------------------------- 讀取（畫面用；加總與下鑽同一份）
def _pct(used, total):
    return round(used * 100 / total, 1) if isinstance(used, int) and isinstance(total, int) and total > 0 else None


def pool_trend(rows: list[dict], today: str, free_now=None, stale: bool = False) -> dict:
    """一個 Pool 最近 TREND_WINDOW_DAYS 天的每日列（day 遞增）→ 成長與預估幾天後滿。
    少於 TREND_MIN_DAYS 天：不估，寫「資料不足（N 天）」。剩餘用**目前**的 Pool 剩餘（free_now），不是最後一筆每日列。
    陣列資料過期：不估，寫「資料過期」。剩餘 ≤ 0：0 天「已滿」。成長 ≤ 0：不會滿（「未增加」）。
    growth_days＝實際跨幾天（第一筆到最後一筆），畫面寫「近 N 天增加」。"""
    start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=TREND_WINDOW_DAYS)).strftime("%Y-%m-%d")
    win = [r for r in rows if start <= r["day"] <= today and isinstance(r["size_used"], int)]
    n = len(win)
    out = {"trend_days": n, "growth_30d": None, "growth_days": None, "growth_per_day": None, "days_to_full": None}
    if n < TREND_MIN_DAYS:
        return {**out, "trend_note": f"資料不足（{n} 天）"}
    first, last = win[0], win[-1]
    span = (datetime.strptime(last["day"], "%Y-%m-%d") - datetime.strptime(first["day"], "%Y-%m-%d")).days
    growth = last["size_used"] - first["size_used"]
    rate = growth / span if span > 0 else 0.0
    out.update(growth_30d=growth, growth_days=span, growth_per_day=round(rate))
    free = free_now if isinstance(free_now, int) else last["size_free"]
    if stale:
        return {**out, "trend_note": "資料過期"}
    if isinstance(free, int) and free <= 0:
        return {**out, "days_to_full": 0, "trend_note": "已滿"}
    if rate <= 0 or not isinstance(free, int):
        return {**out, "trend_note": "未增加"}
    dtf = int(-(-free // rate))                         # 無條件進位
    return {**out, "days_to_full": dtf, "trend_note": f"約 {dtf} 天"}


def accept_empty(conn, serial: str) -> dict:
    """管理員：下一次匯入接受這台陣列的空快照（只生效一次）。並讓被 empty_snapshot 拒收的 Unity 批次可以重跑（清掉批次簽章）。"""
    from db import set_setting

    sn = (serial or "").strip()
    if not _SERIAL_OK.match(sn):
        raise ValueError("陣列序號不合法")
    if conn.execute("SELECT 1 FROM storage_array WHERE serial = ?", (sn,)).fetchone() is None:
        raise ValueError("沒有這台陣列")
    set_setting(conn, ACCEPT_EMPTY_KEY, sn, commit=False)
    n = conn.execute("UPDATE relay_import_batches SET sig = NULL WHERE source = ? AND status = 'rejected' AND reason_code = 'empty_snapshot'",
                     (SOURCE,)).rowcount
    conn.commit()
    return {"serial": sn, "retry_batches": n}


def is_hot(used, total) -> bool:
    """已用 ÷ 總量 ≥ 門檻；用整數比（已用×100 ≥ 門檻×總量），不受顯示用的四捨五入影響。"""
    return isinstance(used, int) and isinstance(total, int) and total > 0 and used * 100 >= POOL_WARN_PCT * total


def overview(conn, now: datetime | None = None, days: int = 90, readonly: bool = False) -> dict:
    """畫面一次拿全部：陣列、Pool、LUN、主機、主機↔LUN、對不到、每日容量、鮮度。卡片數字＝各清單筆數（同一份）。
    readonly=True（verify_numbers 用）：不建表、不提交，鮮度燈不算（sources_view 會建表）；呼叫端先確認 tables_ready。"""
    import relay_import as ri

    if not readonly:
        ri.ensure(conn)
        ensure(conn)
    arr = [dict(r) for r in conn.execute("SELECT * FROM storage_array ORDER BY name, serial")]
    aname = {a["serial"]: (a["name"] or a["serial"]) for a in arr}
    stale_days = getattr(ri.ADAPTERS.get(SOURCE), "stale_days", None) or ri.DEFAULT_STALE_DAYS
    now_utc = (now.replace(tzinfo=timezone(timedelta(hours=8))) if now is not None and now.tzinfo is None else now or datetime.now(timezone.utc))
    for i, a in enumerate(arr, 1):
        got = ri.parse_time(a["collected_at"], "Asia/Taipei")
        age = round((now_utc - got).total_seconds() / 3600, 1) if got else None
        a.update(__no=i, used_pct=_pct(a["size_used"], a["size_total"]), array=aname[a["serial"]],
                 collected_text=ri._taipei_text(got) if got else None, age_hours=age,
                 stale=age is None or age > stale_days * 24)
    pools = [dict(r) for r in conn.execute("SELECT * FROM storage_pool ORDER BY serial, name, pool_id")]
    pname = {(p["serial"], p["pool_id"]): p["name"] or p["pool_id"] for p in pools}
    astale = {a["serial"]: a["stale"] for a in arr}
    today = now_utc.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    hist: dict[tuple, list[dict]] = {}
    for r in conn.execute("SELECT * FROM storage_pool_daily WHERE day >= ? ORDER BY serial, pool_id, day",
                          ((datetime.strptime(today, "%Y-%m-%d") - timedelta(days=max(days, TREND_WINDOW_DAYS))).strftime("%Y-%m-%d"),)):
        hist.setdefault((r["serial"], r["pool_id"]), []).append(dict(r))
    for i, p in enumerate(pools, 1):
        p.update(__no=i, array=aname.get(p["serial"], p["serial"]), used_pct=_pct(p["size_used"], p["size_total"]),
                 **pool_trend(hist.get((p["serial"], p["pool_id"]), []), today, p["size_free"], astale.get(p["serial"], True)))
        p["hot"] = is_hot(p["size_used"], p["size_total"])
    hosts = [dict(r) for r in conn.execute("SELECT * FROM storage_host ORDER BY serial, name, host_id")]
    hname = {(h["serial"], h["host_id"]): h for h in hosts}
    maps = [dict(r) for r in conn.execute("SELECT * FROM storage_host_map ORDER BY serial, lun_id, host_id")]
    luns = [dict(r) for r in conn.execute("SELECT * FROM storage_lun ORDER BY serial, name, lun_id")]
    lname = {(x["serial"], x["lun_id"]): x["name"] or x["lun_id"] for x in luns}
    per_lun: dict[tuple, list[str]] = {}
    per_host: dict[tuple, int] = {}
    for i, m in enumerate(maps, 1):
        h = hname.get((m["serial"], m["host_id"])) or {}
        m.update(__no=i, array=aname.get(m["serial"], m["serial"]), lun=lname.get((m["serial"], m["lun_id"]), m["lun_id"]),
                 host=h.get("name") or m["host_id"], match_status=h.get("match_status"), asset_serial=h.get("asset_serial"),
                 access=ACCESS.get(m["access_mask"], "未註明" if m["access_mask"] is None else str(m["access_mask"])))
        per_lun.setdefault((m["serial"], m["lun_id"]), []).append(m["host"])
        per_host[(m["serial"], m["host_id"])] = per_host.get((m["serial"], m["host_id"]), 0) + 1
    for i, x in enumerate(luns, 1):
        used = x["size_used"] if x["size_used"] is not None else x["size_allocated"]
        hs = per_lun.get((x["serial"], x["lun_id"]), [])
        x.update(__no=i, array=aname.get(x["serial"], x["serial"]),
                 pool=pname.get((x["serial"], x["pool_id"])) if x["pool_id"] else None,
                 used=used, free=(x["size_total"] - used) if used is not None and x["size_total"] is not None else None,
                 used_pct=_pct(used, x["size_total"]), host_count=len(hs), hosts=sorted(hs))
    for i, h in enumerate(hosts, 1):
        h.update(__no=i, array=aname.get(h["serial"], h["serial"]), lun_count=per_host.get((h["serial"], h["host_id"]), 0),
                 type_label=HOST_TYPE.get(h["host_type"], "—" if h["host_type"] is None else str(h["host_type"])),
                 match_label=MATCH_LABEL.get(h["match_status"], h["match_status"] or "—"))
    unmatched = [h for h in hosts if h["match_status"] not in ("matched", "not_host")]
    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    daily = [dict(r) for r in conn.execute(
        "SELECT serial, day, COUNT(*) AS pools, SUM(size_total) AS size_total, SUM(size_used) AS size_used, SUM(size_free) AS size_free "
        "FROM storage_pool_daily WHERE day >= ? GROUP BY serial, day ORDER BY serial, day DESC", (since,))]
    for i, d in enumerate(daily, 1):
        d.update(__no=i, array=aname.get(d["serial"], d["serial"]), used_pct=_pct(d["size_used"], d["size_total"]))
    pool_daily = [dict(r) for rs in hist.values() for r in rs if r["day"] >= since]
    pool_daily.sort(key=lambda r: r["day"], reverse=True)                      # 新到舊…
    pool_daily.sort(key=lambda r: (r["serial"], r["pool_id"]))                 # …再依陣列、Pool（穩定排序保留日期順序）
    for i, d in enumerate(pool_daily, 1):
        d.update(__no=i, array=aname.get(d["serial"], d["serial"]), pool=pname.get((d["serial"], d["pool_id"]), d["name"] or d["pool_id"]),
                 used_pct=_pct(d["size_used"], d["size_total"]))
    hot = [p for p in pools if p["hot"]]
    last = conn.execute("SELECT batch_id, reason_code, reason FROM relay_import_batches WHERE source = ? ORDER BY id DESC LIMIT 1",
                        (SOURCE,)).fetchone()
    alert = None
    if last is not None and last["reason_code"] == "empty_snapshot":
        m = re.search(r"陣列 ([A-Za-z0-9_.-]+) 原本", last["reason"] or "")
        alert = {"batch_id": last["batch_id"], "text": (last["reason"] or "").split("（", 1)[0], "serial": m.group(1) if m else None}
    src = None if readonly else next((s for s in ri.sources_view(conn, now) if s["source"] == SOURCE), None)
    cards = {"arrays": len(arr), "pools": len(pools), "luns": len(luns), "maps": len(maps), "hosts": len(hosts),
             "matched": sum(1 for h in hosts if h["match_status"] == "matched"), "unmatched": len(unmatched),
             "not_host": sum(1 for h in hosts if h["match_status"] == "not_host"), "pools_hot": len(hot),
             "stale_arrays": sum(1 for a in arr if a["stale"]),
             "size_total": sum(a["size_total"] or 0 for a in arr), "size_used": sum(a["size_used"] or 0 for a in arr),
             "size_free": sum(a["size_free"] or 0 for a in arr)}
    cards["used_pct"] = _pct(cards["size_used"], cards["size_total"])
    return {"arrays": arr, "pools": pools, "pools_hot": hot, "luns": luns, "hosts": hosts, "maps": maps, "unmatched": unmatched,
            "daily": daily, "pool_daily": pool_daily, "cards": cards, "freshness": src, "empty_alert": alert,
            "settings": {"pool_warn_pct": POOL_WARN_PCT, "trend_window_days": TREND_WINDOW_DAYS, "trend_min_days": TREND_MIN_DAYS,
                         "stale_days": stale_days}}
