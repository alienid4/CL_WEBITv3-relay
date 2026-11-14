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

推導（2026-11-14 使用者）：一台 ESXi 都對不到（機房不明）的卷宗，用名稱前綴（第一個「_」之前，例 DN_F_xxx → DN）
推導：已知機房（由 ESXi 對到、非跨機房、非推導）的卷宗中，同前綴至少 2 個、而且全部在同一間 → 套用並標 room_derived；
前綴的已知卷宗分在不同機房、或只有 1 個樣本 → 維持機房不明。前綴對照不寫死，每次從資料學。

## 處理（快滿）＝跟 1.5 每日檢核 VMware 段同一把尺（2026-11-14 使用者）

Datastore 剩餘 GB（Free MiB ÷ 1024）低於 daily_check.lun_rule（預設 < 600 GB，等於 600 不算）
或 用量（In Use ÷ Capacity，一位小數）≥ 90%（11-14 安全網，90.0 算）＝「處理」，lun_why 寫觸發哪一條；
門檻從每日檢核設定讀，不另存一份。實體主機磁碟仍用 disk_level 使用率 80／90。

## 資料時間

每個卷宗帶 collected_at＝RVTools 匯入時間（source_record，同一卷宗多列取最新），超過 storage_stale_days
（預設 2 天）標 stale（畫面「過期」）。

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
from datetime import datetime, timedelta
from typing import Any

MIB_PER_TIB = 1024 * 1024
DEFAULT_THRESHOLD_PCT = 15
DERIVE_MIN_SAMPLES = 2


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


def lun_rule(conn: sqlite3.Connection) -> dict:
    """Datastore「處理」門檻＝1.5 每日檢核 VMware 段那一份（daily_check.lun_rule），不另存。"""
    import daily_check
    try:
        cfg = daily_check.settings(conn)
    except sqlite3.OperationalError:          # 沒有 app_settings 表（空庫）：用每日檢核預設
        cfg = daily_check.DEFAULTS
    return daily_check.lun_rule(cfg)


def rule_text(rule: dict) -> str:
    """門檻的白話：「剩餘 < 600 GB 或用量 ≥ 90%」（照每日檢核設定）。首頁、1.4、每日異動都用這一句。"""
    return rule["text"]


def lun_hits(v: dict, rule: dict) -> list[str]:
    """這個卷宗觸發了哪幾條（daily_check.lun_hits，同一支判斷）：剩餘 GB＝Free MiB ÷ 1024；用量＝In Use ÷ Capacity。"""
    import daily_check
    used = round(v["in_use_mib"] * 100 / v["capacity_mib"], 1) if v["capacity_mib"] else None
    return daily_check.lun_hits(v["free_mib"] / 1024, used, rule)


def stale_days(conn: sqlite3.Connection) -> int:
    try:
        return int(_setting(conn, "storage_stale_days", DEFAULT_STALE_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_STALE_DAYS


def is_stale(at, days: int, now: datetime | None = None) -> bool:
    """資料時間比現在早超過 days 天（剛好 days 天不算）；沒有時間或解析不了＝不判。"""
    if not at:
        return False
    try:
        t = datetime.fromisoformat(str(at)[:19])
    except ValueError:
        return False
    return (now or datetime.now()) - t > timedelta(days=days)


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


def ds_prefix(name: str | None) -> str | None:
    """名稱第一個「_」之前（DN_F_xxx → DN）；沒有「_」或前綴空白＝沒有前綴。"""
    n = (name or "").strip()
    if "_" not in n:
        return None
    return n.split("_", 1)[0].strip() or None


def _derive_rooms(vols: list[dict]) -> None:
    """機房不明的卷宗用名稱前綴推導（規則見模組開頭）。只從 ESXi 對到的單一機房學，不從推導結果再學。"""
    learn: dict[str, list[str]] = {}
    for v in vols:
        p = ds_prefix(v["name"])
        if p and v["room"] not in (ROOM_CROSS, ROOM_UNKNOWN):
            learn.setdefault(p, []).append(v["room"])
    for v in vols:
        v["room_derived"], v["room_reason"] = False, None
        if v["room"] != ROOM_UNKNOWN:
            continue
        p = ds_prefix(v["name"])
        seen = learn.get(p or "", [])
        if not p:
            v["room_reason"] = "名稱沒有「_」前綴，無法推導"
        elif len(set(seen)) > 1:
            v["room_reason"] = f"前綴 {p} 的已知 Datastore 分在不同機房（{'、'.join(sorted(set(seen)))}），不推導"
        elif len(seen) < DERIVE_MIN_SAMPLES:
            v["room_reason"] = f"前綴 {p} 的已知 Datastore 只有 {len(seen)} 個（至少 {DERIVE_MIN_SAMPLES} 個才推導）"
        else:
            room = seen[0]
            v.update(room=room, rooms=[room], room_derived=True,
                     room_reason=f"推導：名稱前綴 {p} 的 {len(seen)} 個已知 Datastore 都在 {room}（掛載的 ESXi 對不到機房）")


def datastores(conn: sqlite3.Connection, exempt_state=None) -> dict[str, Any]:
    """去重後的 datastore 卷宗＋總量。totals 就是 volumes（未排除者）的加總。
    已知例外（capacity_exempt.check，kind=ds）：生效中的「處理」不列（lun_low=False、exempt_on=True），仍算在總量與各清單；
    exempt_state=False＝不看例外（capacity_exempt 自己取樣本用）。"""
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
                "vms": 0, "hosts": 0, "vcenters": [], "vc_uuids": [], "rows": 0, "collected_at": at, "_hosts": [],
            }
        v["rows"] += 1
        if at and (not v["collected_at"] or str(at) > str(v["collected_at"])):
            v["collected_at"] = at          # 同一卷宗多份匯入：取最新那次
        v["vms"] += int(_num(rec.get("# VMs")))
        v["hosts"] += int(_num(rec.get("# Hosts")))
        vc = (rec.get("VI SDK Server") or "").strip()
        if vc and vc not in v["vcenters"]:
            v["vcenters"].append(vc)
        vu = (rec.get("VI SDK UUID") or "").strip()          # vCenter instance UUID（已知例外對 vROps vcid 用）
        if vu and vu not in v["vc_uuids"]:
            v["vc_uuids"].append(vu)
        for h in (rec.get("Hosts") or "").split(","):
            if h.strip() and h.strip() not in v["_hosts"]:
                v["_hosts"].append(h.strip())
    by_name, by_ip = _esxi_rooms(conn)
    rule, sd, now = lun_rule(conn), stale_days(conn), datetime.now()
    import capacity_exempt as ce
    est = None if exempt_state is False else (exempt_state or ce.load(conn))
    volumes, excluded = [], []
    for v in vols.values():
        v.update(_assign_room(v.pop("_hosts"), by_name, by_ip))
    _derive_rooms(list(vols.values()))
    for v in vols.values():
        v["free_gb"] = v["free_mib"] / 1024
        hits = lun_hits(v, rule)
        v["lun_low"], v["lun_why"] = bool(hits), "／".join(hits) or None
        ex = ce.check(est, ce.KIND_DS, v["id"], round(v["in_use_mib"] * 100 / v["capacity_mib"], 1) if v["capacity_mib"] else None,
                      v["free_gb"], v["capacity_mib"] / 1024, "rvtools", v["collected_at"], v["id"]) if est else None
        v["exempt_on"] = bool(ex and ex["exempt"])
        v["exempt_suppress"] = v["exempt_on"] and bool(hits)   # 例外正在擋下一個「處理」（跟 1.5 ds_exempt 同一個定義，審查 P3-2）
        v["exempt_id"], v["exempt_text"] = (ex["id"], ex["text"]) if ex else (None, None)
        if v["exempt_on"]:
            v["lun_low"] = False                    # 已知例外：不列處理（總量、清單照算）
        v["stale"] = is_stale(v["collected_at"], sd, now)
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
        "exclude_prefixes": prefixes, "threshold_pct": threshold_pct(conn), "lun_rule": rule,
        "as_of": max((at for _, at in raw if at), default=None), "stale_days": sd,
        "derived_count": sum(1 for v in volumes + excluded if v["room_derived"]),
        "exempt_count": sum(1 for v in volumes if v["exempt_suppress"]),        # 擋下處理的例外（＝1.5 ds_exempt 定義）
        "exempt_active_count": sum(1 for v in volumes if v["exempt_on"]),      # 生效中例外（不論現在有沒有超標）
        "exempt_error": est.error if est else None,
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


def _age_days(ts) -> int | None:
    import v2_collect_summary
    d = v2_collect_summary._parse(ts)
    return (datetime.now() - d).days if d else None


def physical(conn: sqlite3.Connection, exempt_state=None) -> dict[str, Any]:
    """已收集實體／一般主機的磁碟（host_spec.storage_json：VG 總量／剩餘，GB）。
    已知例外（kind=disk，machine_key＋掛載點）：檔案系統（by=mount）的每個掛載點各自判斷；容量預警用
    alert_free_pct＝排除生效中例外掛載點後重算的剩餘 %（全部掛載點都是例外＝None、all_exempt）。free_pct 仍是整台原值。"""
    import rack_layout

    import blast_radius
    import capacity_exempt as ce
    est = None if exempt_state is False else (exempt_state or ce.load(conn))
    mkc: dict = {}

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
        # 剩餘查不到（free_gb=None）≠ 剩 0（Codex 429）：
        #   free_gb＝查得到的那幾個加總（一個都查不到＝None）；free_unknown＝查不到的掛載數。
        #   free_pct 只在「全部查得到」時才有值——有任何一個查不到就不判這台（不告警、不算健康）；
        #   查得到那部分的比例另放 free_pct_known 給畫面參考，告警／計數一律不用它。
        known = [i for i in items if i.get("free_gb") is not None]
        free_unknown = len(items) - len(known)
        free = sum(_num(i.get("free_gb")) for i in known) if known or not items else None
        if not free_unknown:
            pct = round(free * 100 / total, 1) if total else None
            pct_known = pct
        else:
            pct = None
            denom = sum(_num(i.get("size_gb")) for i in known)
            pct_known = round(free * 100 / denom, 1) if known and denom else None
        h, an, api, loc = names.get(r["asset_serial"], (None, None, None, None))
        groups = [{"name": i.get("name"), "size_gb": _num(i.get("size_gb")),
                   "free_gb": None if i.get("free_gb") is None else _num(i.get("free_gb"))} for i in items]
        alert_pct, all_exempt, n_ex = pct, False, 0
        if est and s.get("by") == "mount" and est.active:
            mk = ce.mk_of_serial(conn, r["asset_serial"], mkc)
            ok = ce.unique_keys((g["name"], ce.disk_key(mk, g["name"])) for g in groups)   # 同一台兩個掛載點正規化成同一鍵＝不套
            for g in groups:
                used_g = round((g["size_gb"] - g["free_gb"]) * 100 / g["size_gb"], 1) if g["size_gb"] and g["free_gb"] is not None else None
                k = ce.disk_key(mk, g["name"])
                k = k if k and ok.get(k) == str(g["name"]) else None
                ex = ce.check(est, ce.KIND_DISK, k, used_g, g["free_gb"], g["size_gb"] or None, "spec", r["collected_at"], g["name"])
                g["exempt_on"] = bool(ex and ex["exempt"])
                g["exempt_id"], g["exempt_text"] = (ex["id"], ex["text"]) if ex else (None, None)
            rest = [g for g in groups if not g.get("exempt_on")]
            n_ex = len(groups) - len(rest)
            if n_ex:
                denom = sum(g["size_gb"] for g in rest)
                all_exempt = not rest
                alert_pct = (round(sum(g["free_gb"] for g in rest) * 100 / denom, 1)
                             if rest and denom and all(g["free_gb"] is not None for g in rest) else None)
        out.append({"asset_serial": r["asset_serial"], "name": rack_layout.display_name(h, an, r["asset_serial"]),
                    "ip": r["ip"], "api_id": api, "by": s.get("by"), "total_gb": total, "free_gb": free,
                    "room": blast_radius.map_room(loc) if (loc or "").strip() else ROOM_UNKNOWN,
                    "free_pct": pct, "free_pct_known": pct_known, "free_unknown": free_unknown,
                    "alert_free_pct": alert_pct, "all_exempt": all_exempt, "exempt_mounts": n_ex,
                    "groups": groups, "collected_at": r["collected_at"],
                    # 逐筆可追資料時間（ESJE 第 2 點）：超過 data_coverage 期限的列畫面標「資料過期」
                    "data_age_days": _age_days(r["collected_at"])})
    out.sort(key=lambda x: (x["free_pct"] if x["free_pct"] is not None else 999))
    # 2.006.3（找碴 C12）：三種收法的「剩餘」意思不同，不可以加在一起講——
    #   mount＝檔案系統剩餘（真的快滿）；vg＝VG 尚未配置的空間（配置完是常態）；disk＝只有磁碟大小。
    # 221 實測 402 台是 vg，加總出「100744 GB 剩 1344 GB」看起來像快滿，其實是未配置空間。
    by_kind: dict[str, dict] = {}
    for x in out:
        b = by_kind.setdefault(x["by"] or "unknown", {"hosts": 0, "total_gb": 0.0, "free_gb": None,
                                                      "free_unknown_hosts": 0})
        b["hosts"] += 1
        b["total_gb"] += x["total_gb"]
        b["free_gb"] = add_known(b["free_gb"], x["free_gb"])
        b["free_unknown_hosts"] += 1 if x["free_unknown"] else 0
    free_total = None
    for x in out:
        free_total = add_known(free_total, x["free_gb"])
    return {"rows": out, "total_gb": sum(x["total_gb"] for x in out), "free_gb": free_total,
            "free_unknown_hosts": sum(1 for x in out if x["free_unknown"]), "by_kind": by_kind,
            "data_stale_days": __import__("data_coverage").stale_days_for(conn, "storage"),
            "exempt_error": est.error if est else None}


def mount_samples(conn: sqlite3.Connection) -> list[dict]:
    """已收集檔案系統（by=mount）每個掛載點的現值（capacity_exempt 取樣本、設定基準用；不看例外）。"""
    out = []
    for p in physical(conn, exempt_state=False)["rows"]:
        if p["by"] != "mount":
            continue
        for g in p["groups"]:
            if g["free_gb"] is None or not g["size_gb"]:
                continue
            out.append({"asset_serial": p["asset_serial"], "host": p["name"], "ip": p["ip"], "mount": g["name"],
                        "used_pct": round((g["size_gb"] - g["free_gb"]) * 100 / g["size_gb"], 1), "free_gb": g["free_gb"],
                        "cap_gb": g["size_gb"], "collected_at": p["collected_at"]})
    return out


def add_known(acc: float | None, v: float | None) -> float | None:
    """查得到的才加；全部查不到就維持 None（未知），不變成 0（Codex 429）。
    加總＝逐台 free_gb 非 None 者之和（verify_numbers 釘住）。"""
    if v is None:
        return acc
    return (acc or 0.0) + v


def alerts(ds: dict, phys: dict) -> dict[str, Any]:
    """容量預警（2.006.3，首頁待處理事項與 5.2「容量預警」頁籤同一份）：剩餘 % < 門檻的
    datastore（跟 rooms() 的 near_full 同一條件）＋只看檔案系統剩餘（by=mount）的實體主機。
    VG／磁碟的剩餘是未配置空間，不列入（配置完是常態，不代表容量不足）。

    2.044.0（R17）：實體主機檔案系統改用全站磁碟一把尺 disk_level——使用率 ≥80% 列「處理」、≥90% 列「高」
    （使用率＝100−剩餘 %，一位小數比較）；剩餘查不到的不列、另計 physical_uncollected（未收集，不是 0）。
    2026-11-14（使用者）：Datastore 改用 1.5 每日檢核 VMware 段同一把尺（剩餘 < 600 GB 或用量 ≥ 90%，lun_rule），一律「處理」。"""
    import disk_level
    th = ds["threshold_pct"]
    vols = []
    for v in ds["volumes"]:
        if v["lun_low"]:
            vols.append({**v, "used_pct": disk_level.used_from_free(v["free_pct"]), "level": disk_level.HANDLE,
                         "level_label": disk_level.LABEL[disk_level.HANDLE]})
    hosts, uncollected, ex_hosts = [], [], []
    for p in phys["rows"]:
        if p["by"] != "mount":
            continue
        if p["free_pct"] is None:
            uncollected.append(p)
            continue
        if p.get("exempt_mounts"):
            # 有生效中的例外掛載點：用排除後重算的剩餘 % 判；原本會列、排除後不列＝例外
            eff = disk_level.used_from_free(p["alert_free_pct"]) if p.get("alert_free_pct") is not None else None
            if eff is not None and disk_level.listed(eff):
                lv = disk_level.level(eff)
                hosts.append({**p, "used_pct": eff, "level": lv, "level_label": disk_level.LABEL[lv]})
            elif disk_level.listed(disk_level.used_from_free(p["free_pct"])) or any(
                    g.get("exempt_on") and g["size_gb"] and g["free_gb"] is not None
                    and disk_level.listed(disk_level.used_from_free(round(g["free_gb"] * 100 / g["size_gb"], 1))) for g in p["groups"]):
                ex_hosts.append(p)
            continue
        used = disk_level.used_from_free(p["free_pct"])
        if disk_level.listed(used):
            lv = disk_level.level(used)
            hosts.append({**p, "used_pct": used, "level": lv, "level_label": disk_level.LABEL[lv]})
    return {"threshold_pct": th, "lun_rule": ds["lun_rule"], "disk_handle_pct": disk_level.HANDLE_PCT, "disk_high_pct": disk_level.HIGH_PCT,
            "volumes": vols, "physical": hosts, "physical_uncollected": len(uncollected), "count": len(vols) + len(hosts),
            "volumes_exempt": [v for v in ds["volumes"] if v.get("exempt_on")], "physical_exempt": ex_hosts}


DEFAULT_STALE_DAYS = 2      # 使用者 11-14：RVTools 資料超過 2 天＝過期


def freshness(conn: sqlite3.Connection, ds: dict, now: datetime | None = None) -> dict[str, Any]:
    """RVTools 資料多久沒更新（2.006.3，找碴 C12）。門檻＝app_settings storage_stale_days（預設 2 天）。"""
    sd = stale_days(conn)
    as_of = ds.get("as_of")
    age = None
    if as_of:
        try:
            age = ((now or datetime.now()) - datetime.fromisoformat(str(as_of)[:19])).days
        except ValueError:
            age = None
    return {"as_of": as_of, "age_days": age, "stale_days": sd,
            "stale": is_stale(as_of, sd, now), "has_data": bool(as_of)}


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
    """依機房：容量／已用／剩餘／卷宗數／快滿數（lun_low，跟容量預警同一把尺）／快照數，外加實體主機磁碟。
    每個卷宗只在一個桶（跨機房、機房不明各自一桶），所以各桶加總＝總量。每格都帶下鑽用的 id。"""
    out: dict[str, dict] = {}

    def b(room: str) -> dict:
        return out.setdefault(room, {"room": room, "capacity_mib": 0.0, "in_use_mib": 0.0, "free_mib": 0.0,
                                     "provisioned_mib": 0.0, "volume_ids": [], "near_full_ids": [],
                                     "snapshot_count": 0, "phys_count": 0, "phys_total_gb": 0.0,
                                     "phys_free_gb": None, "phys_free_unknown": 0, "member_rooms": set()})
    for v in ds["volumes"]:
        x = b(v["room"])
        for k in ("capacity_mib", "in_use_mib", "free_mib", "provisioned_mib"):
            x[k] += v[k]
        x["volume_ids"].append(v["id"])
        x["member_rooms"].update(v["rooms"])
        if v["lun_low"]:
            x["near_full_ids"].append(v["id"])
    for sn in (snaps or {}).get("rows", []):
        b(sn["room"])["snapshot_count"] += 1
    for p in (phys or {}).get("rows", []):
        x = b(p["room"])
        x["phys_count"] += 1
        x["phys_total_gb"] += p["total_gb"]
        x["phys_free_gb"] = add_known(x["phys_free_gb"], p["free_gb"])
        x["phys_free_unknown"] += 1 if p.get("free_unknown") else 0
    res = []
    for x in out.values():
        x["volume_count"] = len(x["volume_ids"])
        x["near_full_count"] = len(x["near_full_ids"])
        x["free_pct"] = round(x["free_mib"] * 100 / x["capacity_mib"], 1) if x["capacity_mib"] else None
        x["member_rooms"] = sorted(x["member_rooms"])
        res.append(x)
    res.sort(key=lambda x: (x["room"] in (ROOM_CROSS, ROOM_UNKNOWN), -x["capacity_mib"], x["room"]))
    return res


# ===== VMware Disk（資料存放區）：全部卷宗＋到 90% 的預估（2026-11-14 使用者）=====

FORECAST_WINDOW_DAYS = 30      # 用最後一個點往前 30 天內的點
FORECAST_MIN_DAYS = 7          # 不同日期少於 7 天＝資料不足
FORECAST_MAX_GAP_DAYS = 7      # 最後一個點比現在早超過 7 天＝資料過期，不預估
FORECAST_TARGET_PCT = 90.0
SOON_DAYS = 90                 # 預估 90 天內到 90%＝標「快到」


def lsq_slope(xs: list[float], ys: list[float]) -> float | None:
    """最小平方法斜率（y 對 x）；點少於 2 個或 x 全相同回 None。"""
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if not sxx:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def _history(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """storage_daily：卷宗 → 每天一點（日期由舊到新）。"""
    ensure_table(conn)
    out: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT volume_id, snap_date, capacity_mib, in_use_mib FROM storage_daily ORDER BY snap_date"):
        cap, used = _num(r["capacity_mib"]), _num(r["in_use_mib"])
        if cap <= 0:
            continue                                    # 容量 0 的點算不出使用率，不當資料點
        out.setdefault(r["volume_id"], []).append({"date": r["snap_date"], "cap_gb": cap / 1024, "used_gb": used / 1024,
                                                   "used_pct": round(used * 100 / cap, 2)})
    return out


def forecast(points: list[dict], last_at, now: datetime | None = None) -> dict[str, Any]:
    """到 90% 使用率的預估。points＝每天一點（date, used_pct, used_gb），由舊到新。
    順序：已 ≥90% → 已超過；最後資料比現在早超過 7 天 → 資料過期，不預估；近 30 天不同日期 < 7 → 資料不足（N 天）；
    使用率斜率 ≤0 → 不會到；否則 天數＝(90 − 目前使用率) ÷ 每天增加的使用率（無條件捨去），日期＝最後一點日期＋天數。"""
    now = now or datetime.now()
    res: dict[str, Any] = {"status": None, "days_to_90": None, "eta_date": None, "slope_pct_day": None,
                           "gb_per_day": None, "data_days": 0, "window": []}
    if not points:
        res["status"] = "資料不足（0 天）"
        return res
    last = datetime.fromisoformat(points[-1]["date"][:10])
    win = [p for p in points if (last - datetime.fromisoformat(p["date"][:10])).days < FORECAST_WINDOW_DAYS]
    res["window"], res["data_days"] = win, len({p["date"][:10] for p in win})
    cur = win[-1]["used_pct"]
    xs = [float((datetime.fromisoformat(p["date"][:10]) - last).days) for p in win]
    sp, sg = lsq_slope(xs, [p["used_pct"] for p in win]), lsq_slope(xs, [p["used_gb"] for p in win])
    if res["data_days"] >= FORECAST_MIN_DAYS:
        res["slope_pct_day"] = None if sp is None else round(sp, 4)
        res["gb_per_day"] = None if sg is None else round(sg, 2)
    if cur >= FORECAST_TARGET_PCT:
        res["status"] = "已超過"
    elif last_at is None or is_stale(last_at, FORECAST_MAX_GAP_DAYS, now):
        res["status"] = "資料過期，不預估"
    elif res["data_days"] < FORECAST_MIN_DAYS:
        res["status"] = f"資料不足（{res['data_days']} 天）"
    elif sp is None or sp <= 0:
        res["status"] = "不會到"
    else:
        days = int((FORECAST_TARGET_PCT - cur) / sp)
        res["days_to_90"] = days
        res["eta_date"] = (last + timedelta(days=days)).strftime("%Y-%m-%d")
        res["status"] = f"{days} 天"
    return res


def vmdisk(conn: sqlite3.Connection, ds: dict, now: datetime | None = None) -> dict[str, Any]:
    """VMware Disk 頁籤：全部（未排除）卷宗一列，帶剩餘 GB、使用率、近 30 天每日增加、到 90% 預估。
    low＝1.5 每日檢核門檻（剩餘 < 600 GB 或用量 ≥ 90%，lun_low，跟容量預警同一把尺）；soon＝預估 90 天內到 90%。數字＝清單筆數（verify_numbers 釘住）。"""
    hist = _history(conn)
    rows = []
    for v in ds["volumes"]:
        f = forecast(hist.get(v["id"], []), v.get("collected_at"), now)
        cap = v["capacity_mib"]
        rows.append({"id": v["id"], "name": v["name"], "vcenters": v["vcenters"], "room": v["room"], "rooms": v["rooms"],
                     "room_derived": v.get("room_derived"), "room_reason": v.get("room_reason"),
                     "capacity_gb": cap / 1024, "used_gb": v["in_use_mib"] / 1024, "free_gb": v["free_gb"],
                     "used_pct": round(v["in_use_mib"] * 100 / cap, 1) if cap else None,
                     "collected_at": v.get("collected_at"), "stale": v.get("stale"),
                     "low": bool(v["lun_low"]), "lun_why": v.get("lun_why"), "exempt_on": v.get("exempt_on"),
                     "exempt_suppress": v.get("exempt_suppress"),
                     "exempt_id": v.get("exempt_id"), "exempt_text": v.get("exempt_text"), "soon": f["days_to_90"] is not None and f["days_to_90"] < SOON_DAYS,
                     "forecast_status": f["status"], "days_to_90": f["days_to_90"], "eta_date": f["eta_date"],
                     "gb_per_day": f["gb_per_day"], "slope_pct_day": f["slope_pct_day"], "data_days": f["data_days"],
                     "points": f["window"]})
    rows.sort(key=lambda r: (r["free_gb"] if r["free_gb"] is not None else 1e18, r["name"]))
    return {"rows": rows, "low_count": sum(1 for r in rows if r["low"]), "soon_count": sum(1 for r in rows if r["soon"]),
            "exempt_count": sum(1 for r in rows if r["exempt_suppress"]), "exempt_active_count": sum(1 for r in rows if r["exempt_on"]),
            "lun_rule": ds["lun_rule"], "window_days": FORECAST_WINDOW_DAYS, "min_days": FORECAST_MIN_DAYS,
            "max_gap_days": FORECAST_MAX_GAP_DAYS, "target_pct": FORECAST_TARGET_PCT, "soon_days": SOON_DAYS,
            "history_days": len({p["date"] for ps in hist.values() for p in ps})}


def overview(conn: sqlite3.Connection) -> dict[str, Any]:
    """畫面一次拿齊：去重後卷宗、依系統、快照、實體主機磁碟、趨勢、依機房。順便補存今天這份匯入的快照。"""
    ds = datastores(conn)
    record_snapshot(conn, ds)
    rbn = _ds_room_by_name(ds)
    snaps = snapshots(conn, room_by_name=rbn)
    phys = physical(conn)
    return {"datastores": ds, "by_system": by_system(conn, room_by_name=rbn), "snapshots": snaps,
            "physical": phys, "trend": trend(conn), "rooms": rooms(ds, snaps, phys),
            "alerts": alerts(ds, phys), "freshness": freshness(conn, ds), "vmdisk": vmdisk(conn, ds)}


def room_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """機房架構圖那一行用的精簡版（只有 datastore 容量／已用／卷宗數）。"""
    ds = datastores(conn)
    return [{k: x[k] for k in ("room", "capacity_mib", "in_use_mib", "free_mib", "free_pct", "volume_count",
                               "near_full_count")} for x in rooms(ds)]
