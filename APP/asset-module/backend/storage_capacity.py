"""儲存容量（2026-10-22）：只用已經匯進來的資料，不連任何設備。

來源：
  - RVTools 匯出的 vDatastore／vDisk／vSnapshot 分頁（source_record source='vcenter_extra:*'）
  - 已收集主機的 host_spec.storage_json（VG 總量／剩餘）

## 同一個 datastore 只算一次（去重規則）

同一個 NFS／共用 LUN 會被好幾個 vCenter 各自看到（221 實測：同一個 NFS 在 3 個 vCenter 各一列），
直接加總會重複計算。身分鍵依序取：
  1. URL（`ds:///vmfs/volumes/<uuid>/`，同一個卷宗在每個 vCenter 都一樣）
  2. 沒有 URL：vCenter（VI SDK Server）＋ Object ID
  3. 都沒有：名稱＋類型
同一卷宗的容量／已用取第一列（各 vCenter 報的是同一個卷宗，數字相同或只差即時誤差）；
VM 數、主機數、看到它的 vCenter 則合併列出。

## 排除：宣告容量不可信的卷宗

名稱符合 app_settings `storage_exclude_name_prefixes`（JSON 陣列，預設 ["ma-ds-"]）**而且**
已用＝0、VM 數＝0 的卷宗不算進總量，另列「已排除」清單（221 實測：6 個各宣告 500 TiB、完全沒用，
一加進去總量就從 1.2 PiB 變成 4.2 PiB）。規則只看名稱前綴＋確實沒在用，不猜它是什麼。

## 分機房（2026-10-23「Storage 要分機房」）

datastore 的機房＝掛載它的 ESXi 主機（vDatastore 的 Hosts 欄）各自登記的機房（hardware.physical_location，
跟機房架構圖同一套正規化 blast_radius.map_room）：
  - 全部 ESXi 在同一個機房 → 那個機房
  - ESXi 分在好幾個機房 → 「跨機房」（列出是哪幾間）
  - 一台都對不到機房 → 「機房不明」（對不到的 ESXi 列出來）
ESXi 對法：主機名去網域、轉大寫比 hardware.hostname；Hosts 寫的是 IP 就比 hardware.ip。
同一台 ESXi 登記在兩個機房（資料矛盾）＝這台對不到。部分 ESXi 對不到、對到的都在同一間 → 算那一間，
對不到的列在 hosts_unresolved。快照、VM 磁碟跟著它所在的 datastore 走；實體主機磁碟用主機自己的機房。
每個卷宗只歸一個桶，所以各機房（含跨機房、不明）加總＝總量（verify_numbers 釘住）。

## 每日快照

每次 RVTools 匯入（以 vDatastore 的匯入日期為準）存一份各卷宗容量／已用到 storage_daily，
有 14 天以上才畫趨勢；之前畫面顯示「累積中 N 天」。

## 掛鉤：儲存設備匯入（未做）

日後陣列匯出檔（CSV／XLSX）匯進來時，存成 source_record source='storage_array:<型號>'，
在 datastores() 之外另開一組；這一版不讀。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

MIB_PER_TIB = 1024 * 1024
DEFAULT_THRESHOLD_PCT = 15


def _setting(conn: sqlite3.Connection, key: str, default: Any) -> Any:
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return json.loads(r[0]) if r and r[0] else default
    except (sqlite3.OperationalError, json.JSONDecodeError, TypeError):  # 沒設定／值不是 JSON：用預設
        return default


def threshold_pct(conn: sqlite3.Connection) -> float:
    try:
        return float(_setting(conn, "storage_free_threshold_pct", DEFAULT_THRESHOLD_PCT))
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD_PCT


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _extra(conn: sqlite3.Connection, sheet: str) -> list[tuple[dict, str | None]]:
    out = []
    for r in conn.execute("SELECT payload, collected_at FROM source_record WHERE source = ?",
                          (f"vcenter_extra:{sheet}",)):
        try:
            out.append((json.loads(r["payload"]), r["collected_at"]))
        except (json.JSONDecodeError, TypeError):  # 壞掉的列不算，筆數會在 raw_rows 對不上時看得出來
            continue
    return out


def identity(rec: dict) -> str:
    """同一個 datastore 卷宗的身分鍵（規則見模組開頭）。"""
    url = (rec.get("URL") or "").strip()
    if url:
        return f"url:{url}"
    oid = (rec.get("Object ID") or "").strip()
    if oid:
        return f"oid:{(rec.get('VI SDK Server') or '').strip()}|{oid}"
    return f"name:{(rec.get('Name') or '').strip()}|{(rec.get('Type') or '').strip()}"


ROOM_CROSS = "跨機房"
ROOM_UNKNOWN = "機房不明"


def _esxi_rooms(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """ESXi 主機（短名大寫／IP）→ 機房集合（只看有填機房的現役登記）。"""
    import blast_radius
    import system_stats

    by_name: dict[str, set] = {}
    by_ip: dict[str, set] = {}
    for r in conn.execute("SELECT hostname, ip, physical_location, asset_status FROM hardware"):
        if system_stats.is_retired_row(r) or not (r["physical_location"] or "").strip():
            continue
        room = blast_radius.map_room(r["physical_location"])
        if (r["hostname"] or "").strip():
            by_name.setdefault(r["hostname"].strip().split(".")[0].upper(), set()).add(room)
        for ip in (r["ip"] or "").replace(";", ",").split(","):
            if ip.strip():
                by_ip.setdefault(ip.strip(), set()).add(room)
    return by_name, by_ip


_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _host_room(name: str, by_name: dict, by_ip: dict) -> str | None:
    h = name.strip()
    rooms = by_ip.get(h) if _IP_RE.match(h) else by_name.get(h.split(".")[0].upper())
    return next(iter(rooms)) if rooms and len(rooms) == 1 else None


def _assign_room(hosts: list[str], by_name: dict, by_ip: dict) -> dict:
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for h in hosts:
        r = _host_room(h, by_name, by_ip)
        if r:
            resolved[h] = r
        else:
            unresolved.append(h)
    rooms = sorted(set(resolved.values()))
    room = rooms[0] if len(rooms) == 1 else (ROOM_CROSS if rooms else ROOM_UNKNOWN)
    return {"room": room, "rooms": rooms, "hosts_unresolved": unresolved, "host_names": hosts}


def datastores(conn: sqlite3.Connection) -> dict[str, Any]:
    """去重後的 datastore 卷宗＋總量。totals 就是 volumes（未排除者）的加總。"""
    prefixes = [str(p) for p in _setting(conn, "storage_exclude_name_prefixes", ["ma-ds-"]) or []]
    raw = _extra(conn, "vDatastore")
    vols: dict[str, dict] = {}
    for rec, at in raw:
        k = identity(rec)
        v = vols.get(k)
        if v is None:
            cap = _num(rec.get("Capacity MiB"))
            used = _num(rec.get("In Use MiB"))
            prov = _num(rec.get("Provisioned MiB"))
            free = _num(rec.get("Free MiB")) if rec.get("Free MiB") is not None else max(cap - used, 0)
            v = vols[k] = {
                "id": k, "name": rec.get("Name") or k, "type": rec.get("Type"),
                "capacity_mib": cap, "in_use_mib": used, "provisioned_mib": prov, "free_mib": free,
                "free_pct": round(free * 100 / cap, 1) if cap else None,
                "over_ratio": round(prov / cap, 2) if cap else None,
                "vms": 0, "hosts": 0, "vcenters": [], "rows": 0, "collected_at": at, "_hosts": [],
            }
        v["rows"] += 1
        v["vms"] += int(_num(rec.get("# VMs")))
        v["hosts"] += int(_num(rec.get("# Hosts")))
        vc = (rec.get("VI SDK Server") or "").strip()
        if vc and vc not in v["vcenters"]:
            v["vcenters"].append(vc)
        for h in (rec.get("Hosts") or "").split(","):
            if h.strip() and h.strip() not in v["_hosts"]:
                v["_hosts"].append(h.strip())
    by_name, by_ip = _esxi_rooms(conn)
    volumes, excluded = [], []
    for v in vols.values():
        v.update(_assign_room(v.pop("_hosts"), by_name, by_ip))
        bad = any(v["name"].startswith(p) for p in prefixes) and v["in_use_mib"] == 0 and v["vms"] == 0
        v["excluded"] = bad
        (excluded if bad else volumes).append(v)
    volumes.sort(key=lambda v: (v["free_pct"] if v["free_pct"] is not None else 999, v["name"]))
    excluded.sort(key=lambda v: v["name"])
    tot = {k: sum(v[k] for v in volumes) for k in ("capacity_mib", "in_use_mib", "provisioned_mib", "free_mib")}
    return {
        "raw_rows": len(raw), "volume_count": len(volumes), "excluded_count": len(excluded),
        "dup_rows": len(raw) - len(vols),
        "naive_capacity_mib": sum(_num(r.get("Capacity MiB")) for r, _ in raw),
        "excluded_capacity_mib": sum(v["capacity_mib"] for v in excluded),
        "totals": tot, "volumes": volumes, "excluded": excluded,
        "exclude_prefixes": prefixes, "threshold_pct": threshold_pct(conn),
        "as_of": max((at for _, at in raw if at), default=None),
    }


def _ds_room_by_name(ds: dict) -> dict[str, str]:
    """datastore 名稱 → 機房（vDisk Path／快照 Filename 開頭的「[名稱]」用）。同名不同機房 → 機房不明。"""
    out: dict[str, str] = {}
    for v in ds["volumes"] + ds["excluded"]:
        n = v["name"]
        out[n] = v["room"] if out.get(n, v["room"]) == v["room"] else ROOM_UNKNOWN
    return out


_BRACKET = re.compile(r"^\s*\[([^\]]+)\]")


def _room_of_path(path: str | None, room_by_name: dict) -> str:
    m = _BRACKET.match(path or "")
    return room_by_name.get(m.group(1), ROOM_UNKNOWN) if m else ROOM_UNKNOWN


def _vm_systems(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """VM UUID → 它那台機器（machine_key）登記的 APID。先用匯入時對到的資產；
    對不到（ambiguous／new）再用 VM 的主機名＋IP 算 machine_key 找。"""
    import existence
    import system_stats

    api_by_key: dict[str, set] = {}
    key_by_id: dict[int, str] = {}
    for r in conn.execute("SELECT id, asset_serial, hostname, ip, api_id, asset_status FROM hardware"):
        if system_stats.is_retired_row(r):
            continue
        k = system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        key_by_id[r["id"]] = k
        if (r["api_id"] or "").strip():
            api_by_key.setdefault(k, set()).add(r["api_id"].strip())
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT source_key, payload, resolved_hardware_id FROM source_record WHERE source = 'vcenter'"):
        try:
            p = json.loads(r["payload"])
        except (json.JSONDecodeError, TypeError):  # 壞列：這台 VM 就算「未對應系統」
            continue
        uuid = p.get("vm_uuid") or r["source_key"]
        k = key_by_id.get(r["resolved_hardware_id"]) if r["resolved_hardware_id"] else None
        apis = api_by_key.get(k or "", set())
        if not apis:
            k2 = existence.extra_key(p.get("hostname") or p.get("vm_name"), p.get("ip"))
            apis = api_by_key.get(k2, set())
        out[uuid] = sorted(apis)
    return out


def by_system(conn: sqlite3.Connection, room_by_name: dict | None = None) -> dict[str, Any]:
    """各系統的 VM 磁碟配置量（vDisk Capacity）。一台 VM 只算進一個系統（APID 排序第一個），
    其他 APID 列在 also；對不到系統的 VM 自成一列「未對應系統」。列加總＝vDisk 總量。
    另外依磁碟所在 datastore 的機房分桶（rows_by_room）：同一台 VM 的磁碟分在不同機房時各算各的。"""
    import blast_radius

    vm_api = _vm_systems(conn)
    room_by_name = room_by_name if room_by_name is not None else _ds_room_by_name(datastores(conn))
    rows: dict[str, dict] = {}
    per_room: dict[str, dict] = {}
    total = 0.0
    for rec, _ in _extra(conn, "vDisk"):
        cap = _num(rec.get("Capacity MiB"))
        total += cap
        uuid = rec.get("VM UUID") or rec.get("VM")
        apis = vm_api.get(uuid, [])
        key = apis[0] if apis else ""
        room = _room_of_path(rec.get("Path"), room_by_name)
        for bucket in (rows, per_room.setdefault(room, {})):
            row = bucket.setdefault(key, {"api_id": key or None, "capacity_mib": 0.0, "vms": set(),
                                          "disks": 0, "also": set(), "names": set()})
            row["capacity_mib"] += cap
            row["vms"].add(uuid)
            row["names"].add(rec.get("VM") or str(uuid))
            row["disks"] += 1
            row["also"].update(apis[1:])
    names: dict[str, str] = {}

    def fin(bucket: dict) -> list:
        out = []
        for key, r in bucket.items():
            if key not in names:
                names[key] = blast_radius._system_name(conn, key) if key else "未對應系統"
            out.append({"api_id": r["api_id"], "name": names[key], "capacity_mib": r["capacity_mib"],
                        "vm_count": len(r["vms"]), "disk_count": r["disks"], "vms": sorted(r["names"]),
                        "also": sorted(r["also"])})
        out.sort(key=lambda r: -r["capacity_mib"])
        return out
    return {"rows": fin(rows), "total_mib": total,
            "rows_by_room": {rm: fin(bk) for rm, bk in per_room.items()},
            "total_by_room": {rm: sum(r["capacity_mib"] for r in bk.values()) for rm, bk in per_room.items()}}


def snapshots(conn: sqlite3.Connection, now: datetime | None = None, room_by_name: dict | None = None) -> dict[str, Any]:
    """vSnapshot：每張快照的 VM、名稱、日期、天數、大小、所屬系統。"""
    now = now or datetime.now()
    vm_api = _vm_systems(conn)
    room_by_name = room_by_name if room_by_name is not None else _ds_room_by_name(datastores(conn))
    out = []
    for rec, _ in _extra(conn, "vSnapshot"):
        dt = str(rec.get("Date / time") or "")
        try:
            age = (now - datetime.strptime(dt[:19], "%Y-%m-%d %H:%M:%S")).days
        except ValueError:
            age = None
        out.append({"vm": rec.get("VM"), "name": rec.get("Name"), "date": dt or None, "age_days": age,
                    "size_mib": _num(rec.get("Size MiB (total)")) or None, "vcenter": rec.get("VI SDK Server"),
                    "api_ids": vm_api.get(rec.get("VM UUID") or "", []),
                    "room": _room_of_path(rec.get("Filename"), room_by_name)})
    out.sort(key=lambda s: -(s["age_days"] or 0))
    return {"rows": out, "total_mib": sum(s["size_mib"] or 0 for s in out)}


def physical(conn: sqlite3.Connection) -> dict[str, Any]:
    """已收集實體／一般主機的磁碟（host_spec.storage_json：VG 總量／剩餘，GB）。"""
    import rack_layout

    import blast_radius

    names = {r["asset_serial"]: (r["hostname"], r["asset_name"], r["api_id"], r["physical_location"])
             for r in conn.execute("SELECT asset_serial, hostname, asset_name, api_id, physical_location FROM hardware")}
    out = []
    for r in conn.execute("SELECT asset_serial, ip, storage_json, collected_at FROM host_spec "
                          "WHERE storage_json IS NOT NULL AND storage_json != ''"):
        try:
            s = json.loads(r["storage_json"])
        except (json.JSONDecodeError, TypeError):  # 收集回來的 JSON 壞了：跳過這台
            continue
        items = s.get("items") or []
        if s.get("by") == "mount":      # DVD／ISO 掛載不算（optical_media）：以前收集到的資料讀出來時也排除，總量跟著重算
            import optical_media
            kept = [i for i in items if not optical_media.is_optical(i.get("name"))]
            if len(kept) != len(items):
                items = kept
                s = {**s, "total_gb": None}
        total = _num(s.get("total_gb")) or sum(_num(i.get("size_gb")) for i in items)
        free = sum(_num(i.get("free_gb")) for i in items)
        h, an, api, loc = names.get(r["asset_serial"], (None, None, None, None))
        out.append({"asset_serial": r["asset_serial"], "name": rack_layout.display_name(h, an, r["asset_serial"]),
                    "ip": r["ip"], "api_id": api, "by": s.get("by"), "total_gb": total, "free_gb": free,
                    "room": blast_radius.map_room(loc) if (loc or "").strip() else ROOM_UNKNOWN,
                    "free_pct": round(free * 100 / total, 1) if total else None,
                    "groups": [{"name": i.get("name"), "size_gb": _num(i.get("size_gb")), "free_gb": _num(i.get("free_gb"))}
                               for i in items], "collected_at": r["collected_at"]})
    out.sort(key=lambda x: (x["free_pct"] if x["free_pct"] is not None else 999))
    # 2.006.3（找碴 C12）：三種收法的「剩餘」意思不同，不可以加在一起講——
    #   mount＝檔案系統剩餘（真的快滿）；vg＝VG 尚未配置的空間（配置完是常態）；disk＝只有磁碟大小。
    # 221 實測 402 台是 vg，加總出「100744 GB 剩 1344 GB」看起來像快滿，其實是未配置空間。
    by_kind: dict[str, dict] = {}
    for x in out:
        b = by_kind.setdefault(x["by"] or "unknown", {"hosts": 0, "total_gb": 0.0, "free_gb": 0.0})
        b["hosts"] += 1
        b["total_gb"] += x["total_gb"]
        b["free_gb"] += x["free_gb"]
    return {"rows": out, "total_gb": sum(x["total_gb"] for x in out), "free_gb": sum(x["free_gb"] for x in out),
            "by_kind": by_kind}


def alerts(ds: dict, phys: dict) -> dict[str, Any]:
    """容量預警（2.006.3，首頁待處理事項與 5.2「容量預警」頁籤同一份）：剩餘 % < 門檻的
    datastore（跟 rooms() 的 near_full 同一條件）＋只看檔案系統剩餘（by=mount）的實體主機。
    VG／磁碟的剩餘是未配置空間，不列入（配置完是常態，不代表容量不足）。"""
    th = ds["threshold_pct"]
    vols = [v for v in ds["volumes"] if v["free_pct"] is not None and v["free_pct"] < th]
    hosts = [p for p in phys["rows"] if p["by"] == "mount" and p["free_pct"] is not None and p["free_pct"] < th]
    return {"threshold_pct": th, "volumes": vols, "physical": hosts, "count": len(vols) + len(hosts)}


DEFAULT_STALE_DAYS = 14


def freshness(conn: sqlite3.Connection, ds: dict, now: datetime | None = None) -> dict[str, Any]:
    """RVTools 資料多久沒更新（2.006.3，找碴 C12）。門檻＝app_settings storage_stale_days（預設 14 天）。"""
    try:
        stale_days = int(_setting(conn, "storage_stale_days", DEFAULT_STALE_DAYS))
    except (TypeError, ValueError):
        stale_days = DEFAULT_STALE_DAYS
    as_of = ds.get("as_of")
    age = None
    if as_of:
        try:
            age = ((now or datetime.now()) - datetime.fromisoformat(str(as_of)[:19])).days
        except ValueError:
            age = None
    return {"as_of": as_of, "age_days": age, "stale_days": stale_days,
            "stale": bool(as_of) and age is not None and age > stale_days, "has_data": bool(as_of)}


# ===== 每日快照（趨勢）=====

def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS storage_daily ("
        " snap_date TEXT NOT NULL, volume_id TEXT NOT NULL, name TEXT, type TEXT,"
        " capacity_mib REAL, in_use_mib REAL, free_mib REAL, provisioned_mib REAL,"
        " recorded_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),"
        " PRIMARY KEY (snap_date, volume_id))")


def record_snapshot(conn: sqlite3.Connection, ds: dict | None = None) -> int:
    """以這份 RVTools 的匯入日期存一份各卷宗容量（同一天只存一次，已存在不覆蓋）。回新寫入的筆數。"""
    ensure_table(conn)
    ds = ds or datastores(conn)
    if not ds["as_of"]:
        return 0
    day = str(ds["as_of"])[:10]
    n = 0
    for v in ds["volumes"]:
        cur = conn.execute(
            "INSERT OR IGNORE INTO storage_daily (snap_date, volume_id, name, type, capacity_mib, in_use_mib, "
            "free_mib, provisioned_mib) VALUES (?,?,?,?,?,?,?,?)",
            (day, v["id"], v["name"], v["type"], v["capacity_mib"], v["in_use_mib"], v["free_mib"],
             v["provisioned_mib"]))
        n += cur.rowcount
    conn.commit()
    return n


def trend(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_table(conn)
    rows = conn.execute(
        "SELECT snap_date, SUM(capacity_mib) AS cap, SUM(in_use_mib) AS used FROM storage_daily "
        "GROUP BY snap_date ORDER BY snap_date").fetchall()
    return {"days": len(rows), "min_days": 14,
            "points": [{"date": r["snap_date"], "capacity_mib": r["cap"], "in_use_mib": r["used"]} for r in rows]}


def rooms(ds: dict, snaps: dict | None = None, phys: dict | None = None) -> list[dict[str, Any]]:
    """依機房：容量／已用／剩餘／卷宗數／快滿數（剩餘 % < 門檻）／快照數，外加實體主機磁碟。
    每個卷宗只在一個桶（跨機房、機房不明各自一桶），所以各桶加總＝總量。每格都帶下鑽用的 id。"""
    th = ds["threshold_pct"]
    out: dict[str, dict] = {}

    def b(room: str) -> dict:
        return out.setdefault(room, {"room": room, "capacity_mib": 0.0, "in_use_mib": 0.0, "free_mib": 0.0,
                                     "provisioned_mib": 0.0, "volume_ids": [], "near_full_ids": [],
                                     "snapshot_count": 0, "phys_count": 0, "phys_total_gb": 0.0,
                                     "phys_free_gb": 0.0, "member_rooms": set()})
    for v in ds["volumes"]:
        x = b(v["room"])
        for k in ("capacity_mib", "in_use_mib", "free_mib", "provisioned_mib"):
            x[k] += v[k]
        x["volume_ids"].append(v["id"])
        x["member_rooms"].update(v["rooms"])
        if v["free_pct"] is not None and v["free_pct"] < th:
            x["near_full_ids"].append(v["id"])
    for sn in (snaps or {}).get("rows", []):
        b(sn["room"])["snapshot_count"] += 1
    for p in (phys or {}).get("rows", []):
        x = b(p["room"])
        x["phys_count"] += 1
        x["phys_total_gb"] += p["total_gb"]
        x["phys_free_gb"] += p["free_gb"]
    res = []
    for x in out.values():
        x["volume_count"] = len(x["volume_ids"])
        x["near_full_count"] = len(x["near_full_ids"])
        x["free_pct"] = round(x["free_mib"] * 100 / x["capacity_mib"], 1) if x["capacity_mib"] else None
        x["member_rooms"] = sorted(x["member_rooms"])
        res.append(x)
    res.sort(key=lambda x: (x["room"] in (ROOM_CROSS, ROOM_UNKNOWN), -x["capacity_mib"], x["room"]))
    return res


def overview(conn: sqlite3.Connection) -> dict[str, Any]:
    """畫面一次拿齊：去重後卷宗、依系統、快照、實體主機磁碟、趨勢、依機房。順便補存今天這份匯入的快照。"""
    ds = datastores(conn)
    record_snapshot(conn, ds)
    rbn = _ds_room_by_name(ds)
    snaps = snapshots(conn, room_by_name=rbn)
    phys = physical(conn)
    return {"datastores": ds, "by_system": by_system(conn, room_by_name=rbn), "snapshots": snaps,
            "physical": phys, "trend": trend(conn), "rooms": rooms(ds, snaps, phys),
            "alerts": alerts(ds, phys), "freshness": freshness(conn, ds)}


def room_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """機房架構圖那一行用的精簡版（只有 datastore 容量／已用／卷宗數）。"""
    ds = datastores(conn)
    return [{k: x[k] for k in ("room", "capacity_mib", "in_use_mib", "free_mib", "free_pct", "volume_count",
                               "near_full_count")} for x in rooms(ds)]
