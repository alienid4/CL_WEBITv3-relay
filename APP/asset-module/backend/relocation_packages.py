"""搬遷包 v1（2026-10-31）：機房搬遷時「這些機器必須同一批搬」的分組。**只讀既有資料，GET 路徑完全不寫庫。**

## 成包規則（兩層，設計稿 docs/ui-v2/搬遷包_設計稿_草案.md）

第一層：同一個業務系統（api_id）在所選機房的所有現役機器為一包。
  · 「現役機器」＝rack_layout.machines_readonly（machine_key 去重、退役不列）。機器是全站唯一一把尺，不另寫去重。
  · 一台機器掛兩個以上系統（api_ids 多值）＝這兩個系統共用一台機器，不可能分兩批搬 → 併包，原因「共用同一台機器」。
  · 沒有 api_id 的機器歸單獨一包「未歸屬系統」。
  · 跨機房：同系統在別的機房還有機器，標出別的機房名與台數（成員仍只含所選機房，台數加總 ＝ 該機房現役台數）。
第二層：用 observed_deps 的觀察連線（infer 已套用過期門檻與排除／樞紐規則）把「必須同批搬」的包合併。
  · 證據列的兩端都必須是所選機房的成員機器（不是只有系統層級的邊）。
  · 樞紐系統（監控、備份這種跟所有人都有連線）的邊不拿來合併；跳過幾條會回在 meta。
  · **強證據才併包**（Codex 審查 2026-10-31）：一對系統之間未過期證據 ≥ relocation_pkg_merge_min（預設 3）條
    而且發起端 ≥ 2 台不同機器，或兩個方向都觀察到。沒達標的只列「弱證據（建議人工確認是否同批）」，不影響成包。
  · 忽略埠：app_settings relocation_pkg_ignore_ports（逗號分隔，預設空）；另疊 observed_deps 既有噪音過濾。
  · 每個合併都附證據：哪兩台、方向（A→B／B→A／雙向）、埠、最後看到時間、條數。連線資料不全時合併是推論，不是真相。
  · 沒有任何連線證據的包標「僅依系統登記」。
**不自動拆包**；超過門檻（app_settings relocation_pkg_max，預設 40）只標記。

## 完全不寫庫
用 rack_layout.machines_readonly（不建表、不塞型號規格種子、不標「已同步」），唯讀連線（mode=ro）也能呼叫。
結果有 60 秒 TTL 記憶體快取（指紋＝hardware／連線／人工更正／相關設定的版本），清單與明細共用同一次計算。

## 天條
VMware 設備（ESXi／vCenter）不納入成員、也不拿它的連線當合併證據。判定偏保守：任何登記或機器層面
（名稱、IP、OS、型號）被判成 VMware、或判定本身出錯，都排除（不做 DNS）。
"""
from __future__ import annotations

import hashlib
import time

MAX_KEY = "relocation_pkg_max"
DEFAULT_MAX = 40
MERGE_MIN_KEY = "relocation_pkg_merge_min"
DEFAULT_MERGE_MIN = 3
IGNORE_PORTS_KEY = "relocation_pkg_ignore_ports"
UNASSIGNED = "（未歸屬系統）"
BASIS_EDGE = "連線證據"
BASIS_REG = "僅依系統登記"
BASIS_NONE = "未歸屬系統"
EVIDENCE_SAMPLES = 5
CACHE_TTL_S = 60


class UnknownRoom(KeyError):
    """room 不是既有機房。"""


def _int_setting(conn, key: str, default: int) -> int:
    import db

    try:
        v = int(str(db.get_setting(conn, key, str(default)) or default).strip())
    except (TypeError, ValueError):
        return default
    return v if v >= 1 else default


def threshold(conn) -> int:
    return _int_setting(conn, MAX_KEY, DEFAULT_MAX)


def merge_min(conn) -> int:
    return _int_setting(conn, MERGE_MIN_KEY, DEFAULT_MERGE_MIN)


def ignore_ports(conn) -> set[int]:
    import db

    out: set[int] = set()
    for x in str(db.get_setting(conn, IGNORE_PORTS_KEY, "") or "").replace("，", ",").split(","):
        x = x.strip()
        if x.isdigit():
            out.add(int(x))
    return out


def _vmware_keys(conn, machines: list[dict]) -> set[str]:
    """machine_key 集合：疑似 VMware 設備（ESXi／vCenter）。**偏保守**：任何一筆登記、或機器本身的
    名稱／IP／OS／型號被判成 VMware 就排除；判定自己出錯也排除（寧可漏包，不可把 VMware 放進搬遷包）。
    不做 DNS。"""
    import onboard_eligibility

    esxi = onboard_eligibility.esxi_host_names(conn)
    by_serial: dict[str, str] = {}
    for m in machines:
        for s in m["serials"]:
            by_serial[s] = m["key"]
    out: set[str] = set()

    def judge(asset) -> bool:
        try:
            got = onboard_eligibility.judge_for_asset(conn, asset, esxi_names=esxi)
        except Exception:  # noqa: BLE001 - 判不出來就當 VMware 排除（偏保守）
            return True
        return bool(got and got[0] == "esxi")

    for r in conn.execute("SELECT * FROM hardware"):
        k = by_serial.get(r["asset_serial"])
        if k is not None and k not in out and judge(r):
            out.add(k)
    for m in machines:                      # 回連不到序號、或登記沒帶 VMware 字樣但機器層看得出來的
        if m["key"] in out:
            continue
        if judge({"os": " ".join(m.get("_oses") or []) or None, "hostname": m["name"], "ip": m["ip"],
                  "asset_name": " ".join(m.get("asset_names") or []) or None,
                  "asset_purpose": m.get("model")}):
            out.add(m["key"])
    return out


def _api_names(conn) -> dict[str, str]:
    try:
        return {(r[0] or "").strip(): (r[1] or "").strip()
                for r in conn.execute("SELECT api_id, name FROM business_system")}
    except Exception:  # noqa: BLE001 - 舊庫沒有系統表：只顯示 APID
        return {}


class _UF:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _pid(room: str, nodes: list[str]) -> str:
    return "PK-" + hashlib.sha1((room + "|" + "|".join(sorted(nodes))).encode("utf-8")).hexdigest()[:10]


# ── 快取 ──────────────────────────────────────────────────────────────

_CACHE: dict[str, dict] = {}
STATS = {"world": 0, "hit": 0}


def _db_id(conn) -> str:
    try:
        return str(conn.execute("PRAGMA database_list").fetchone()[2] or f"mem:{id(conn)}")
    except Exception:  # noqa: BLE001
        return f"conn:{id(conn)}"


def _q1(conn, sql: str):
    try:
        return tuple(conn.execute(sql).fetchone())
    except Exception:  # noqa: BLE001 - 表還沒建：指紋用空值
        return None


def _fingerprint(conn) -> tuple:
    """資料版本：hardware／連線／人工更正／相關設定。任何一個變了就重算。"""
    pats = ("relocation_pkg_%", "observed_deps_%", "machine_key_mode", "collector_ip")
    st = tuple(tuple(r) for r in conn.execute(
        "SELECT key, value FROM app_settings WHERE " + " OR ".join("key LIKE ?" for _ in pats) + " ORDER BY key",
        pats))
    return (_q1(conn, "SELECT COUNT(*), MAX(updated_at), MAX(rowid) FROM hardware"),
            _q1(conn, "SELECT COUNT(*), MAX(last_seen), MAX(id) FROM host_connection"),
            _q1(conn, "SELECT COUNT(*), MAX(updated_at) FROM rack_position_override"),
            _q1(conn, "SELECT COUNT(*), MAX(updated_at) FROM rack_state_override"),
            _q1(conn, "SELECT COUNT(*), MAX(rowid) FROM asset_nic"),
            _q1(conn, "SELECT COUNT(*), MAX(rowid) FROM personnel"), st)


def invalidate() -> None:
    _CACHE.clear()


def _world(conn) -> dict:
    """機隊層的一次計算（機器、VMware、連線推論、IP→機器鍵），60 秒內資料沒變就共用。"""
    import observed_deps
    import rack_layout

    key, now, fp = _db_id(conn), time.monotonic(), _fingerprint(conn)
    w = _CACHE.get(key)
    if w and w["fp"] == fp and now - w["t"] < CACHE_TTL_S:
        STATS["hit"] += 1
        return w
    STATS["world"] += 1
    ms = [m for m in rack_layout.machines_readonly(conn) if not m["retired"]]
    vm = _vmware_keys(conn, ms)
    w = {"fp": fp, "t": now, "all_rooms": sorted({m["room"] for m in ms}),
         "active": [m for m in ms if m["key"] not in vm], "vm_keys": vm,
         "inferred": observed_deps.infer(conn), "ident": observed_deps._identity(conn),  # noqa: SLF001
         "names": _api_names(conn), "limit": threshold(conn), "min": merge_min(conn),
         "ignore": ignore_ports(conn), "stale": observed_deps.stale_days(conn), "built": {}}
    _CACHE[key] = w
    return w


def rooms(conn) -> list[str]:
    """既有機房（有現役機器的機房）。"""
    return list(_world(conn)["all_rooms"])


def active_machine_keys(conn, room: str) -> set[str]:
    """該機房「非 VMware 現役」machine key 集合（verify_numbers 比對用）。"""
    return {m["key"] for m in _world(conn)["active"] if m["room"] == room}


def build(conn, room: str) -> dict:
    """回 {room, threshold, total, packages:[(pk, members)], meta}。唯讀、有快取。room 不存在丟 UnknownRoom。"""
    room = (room or "").strip()
    w = _world(conn)
    if room not in w["all_rooms"]:
        raise UnknownRoom(room)
    if room not in w["built"]:
        w["built"][room] = _build_room(conn, w, room)
    return w["built"][room]


def _build_room(conn, w: dict, room: str) -> dict:
    import blast_radius
    import observed_deps

    active = w["active"]
    here = [m for m in active if m["room"] == room]
    by_key = {m["key"]: m for m in here}
    names, limit, need, ign = w["names"], w["limit"], w["min"], w["ignore"]

    other_rooms_of: dict[str, dict[str, int]] = {}
    for m in active:
        if m["room"] == room:
            continue
        for a in m["api_ids"]:
            other_rooms_of.setdefault(a, {})
            other_rooms_of[a][m["room"]] = other_rooms_of[a].get(m["room"], 0) + 1

    uf = _UF()
    reasons: list[dict] = []
    weak: list[dict] = []
    apis_here: set[str] = set()
    for m in here:
        for a in m["api_ids"]:
            apis_here.add(a)
            uf.find("S:" + a)
        if len(m["api_ids"]) > 1:
            first = m["api_ids"][0]
            for a in m["api_ids"][1:]:
                uf.union("S:" + first, "S:" + a)
            reasons.append({"type": "shared_machine", "label": "共用同一台機器", "apis": list(m["api_ids"]),
                            "direction": None, "machine": m["name"], "ip": m["ip"], "evidence": [],
                            "last_seen": None, "evidence_count": 0})

    inferred, ident = w["inferred"], w["ident"]
    hubs = observed_deps.hub_ids(inferred)
    keys_of_ip, key_of_serial = ident["keys_of_ip"], ident["key_of_serial"]
    hub_skipped = 0
    pairs: dict[tuple[str, str], dict] = {}
    for e in inferred["edges"]:
        x, y = e["caller"], e["callee"]
        if x not in apis_here or y not in apis_here:
            continue
        if x in hubs or y in hubs:
            hub_skipped += 1
            continue
        for r in observed_deps.edge_rows(conn, x, y, inferred):
            inbound = r["direction"] == "inbound"
            port = r["local_port"] if inbound else r["peer_port"]
            if port in ign:
                continue
            hk = key_of_serial.get(r["asset_serial"] or "")
            if not hk or hk.startswith("ret:"):
                ks = keys_of_ip.get(r["host_ip"], set())
                hk = next(iter(ks)) if len(ks) == 1 else None
            pks = keys_of_ip.get(r["peer_ip"], set())
            pk = next(iter(pks)) if len(pks) == 1 else None
            if hk not in by_key or pk not in by_key or hk == pk:
                continue
            ha, pa = set(by_key[hk]["api_ids"]), set(by_key[pk]["api_ids"])
            if not ((x in ha and y in pa) or (y in ha and x in pa)):
                continue
            caller_k, callee_k = (pk, hk) if inbound else (hk, pk)       # x（caller）依賴 y（callee）
            ev = {"from": by_key[caller_k]["name"], "from_ip": r["peer_ip"] if inbound else r["host_ip"],
                  "to": by_key[callee_k]["name"], "to_ip": r["host_ip"] if inbound else r["peer_ip"],
                  "flow": f"{x}→{y}", "port": port, "protocol": observed_deps.port_label(port),
                  "count": r["count"], "last_seen": r["last_seen"]}
            slot = pairs.setdefault(tuple(sorted((x, y))), {"rows": {}, "callers": set()})
            slot["rows"].setdefault((x, y), []).append(ev)
            slot["callers"].add(caller_k)
    for (a, b), slot in sorted(pairs.items()):
        evs = [ev for rows in slot["rows"].values() for ev in rows]
        dirs = sorted(slot["rows"])
        direction = "雙向" if len(dirs) == 2 else f"{dirs[0][0]}→{dirs[0][1]}"
        strong = len(dirs) == 2 or (len(evs) >= need and len(slot["callers"]) >= 2)
        evs.sort(key=lambda t: t["last_seen"] or "", reverse=True)
        item = {"type": "observed_conn" if strong else "weak_conn",
                "label": "觀察到連線（強證據）" if strong else "弱證據（建議人工確認是否同批）",
                "apis": [a, b], "direction": direction, "machine": None, "ip": None,
                "evidence": evs[:EVIDENCE_SAMPLES], "evidence_count": len(evs), "callers": len(slot["callers"]),
                "last_seen": max((t["last_seen"] or "") for t in evs) or None,
                "rule": f"併包條件：連線 ≥ {need} 條且發起端 ≥ 2 台，或兩個方向都看到"}
        if strong:
            uf.union("S:" + a, "S:" + b)
            reasons.append(item)
        else:
            weak.append(item)

    comps: dict[str, dict] = {}
    for m in here:
        root = uf.find("S:" + m["api_ids"][0]) if m["api_ids"] else "UNASSIGNED"
        c = comps.setdefault(root, {"members": [], "apis": set()})
        c["members"].append(m)
        c["apis"].update(m["api_ids"])
    packages = []
    contact_cache: dict[str, list] = {}
    for c in comps.values():
        apis = sorted(c["apis"])
        mem = sorted(c["members"], key=lambda m: (m["rack"] or "", m["u_start"] or 0, m["name"].lower()))
        merges = [r for r in reasons if r["apis"][0] in c["apis"]] if apis else []
        wk = [r for r in weak if r["apis"][0] in c["apis"] or r["apis"][1] in c["apis"]] if apis else []
        has_edge = any(r["type"] == "observed_conn" for r in merges)
        basis = BASIS_NONE if not apis else (BASIS_EDGE if has_edge else BASIS_REG)
        other: dict[str, int] = {}
        for a in apis:
            for rm, n in other_rooms_of.get(a, {}).items():
                other[rm] = other.get(rm, 0) + n
        contacts: dict[str, dict] = {}
        for a in apis:
            if a not in contact_cache:
                contact_cache[a] = blast_radius._system_contacts(conn, a)     # noqa: SLF001
            for p in contact_cache[a]:
                contacts.setdefault(p["name"], p)
        if not contacts:
            for m in mem:
                if m.get("owner"):
                    contacts.setdefault(m["owner"], {"name": m["owner"], "role": "機器擁有者", "department": None, "phone": None})
        sys_txt = [f"{a} {names.get(a, '')}".strip() for a in apis] or [UNASSIGNED]
        title = ("、".join(names.get(a) or a for a in apis[:3]) + (f" 等 {len(apis)} 個系統" if len(apis) > 3 else "")) \
            if apis else UNASSIGNED
        summ = "；".join(_summ(r) for r in merges) if merges else ("沒有連線證據，只依系統登記" if apis else "沒有登記系統")
        if wk:
            summ += f"；另有 {len(wk)} 組弱證據待人工確認"
        packages.append(({
            "id": _pid(room, apis or ["UNASSIGNED"]), "name": title, "count": len(mem),
            "systems": sys_txt, "api_ids": apis, "system_count": len(apis),
            "cross_room": len(other), "other_rooms": [{"room": rm, "count": n} for rm, n in sorted(other.items())],
            "basis": basis, "over_threshold": len(mem) > limit, "threshold": limit,
            "merge_summary": summ, "merge_count": len(merges), "weak_count": len(wk),
            "contacts": list(contacts.values()),
            "contact_text": "、".join(f"{p['name']}（{p['role']}）" for p in contacts.values()) or None,
            "member_keys": [m["key"] for m in mem], "merges": merges, "weak": wk,
        }, mem))
    packages.sort(key=lambda t: (-t[0]["count"], t[0]["name"]))
    return {"room": room, "threshold": limit, "total": len(here), "packages": packages,
            "meta": {"hub_edges_skipped": hub_skipped, "hubs": sorted(hubs), "vmware_excluded": len(w["vm_keys"]),
                     "stale_days": w["stale"], "merge_min": need, "ignore_ports": sorted(ign)}}


def _summ(r: dict) -> str:
    if r["type"] == "shared_machine":
        return f"共用同一台機器（{r['machine']}）"
    return f"{r['apis'][0]}↔{r['apis'][1]} {r['direction']} 連線 {r['evidence_count']} 條（最後 {r['last_seen'] or '—'}）"


def member_row(m: dict) -> dict:
    return {"key": m["key"], "hostname": m["name"], "ip": m["ip"], "room": m["room"], "rack": m["rack"],
            "u": (None if not m["u_start"] else (str(m["u_start"]) if m["u_end"] in (None, m["u_start"])
                                                  else f"{m['u_start']}-{m['u_end']}")),
            "systems": m["api_ids"], "environment": "、".join(m.get("_envs") or []) or None,
            "owner": m.get("owner")}


def list_packages(conn, room: str) -> dict:
    r = build(conn, room)
    return {"room": r["room"], "threshold": r["threshold"], "total": r["total"],
            "packages": [{k: v for k, v in pk.items() if k not in ("merges", "member_keys", "weak")} for pk, _ in r["packages"]],
            "meta": r["meta"]}


def package_detail(conn, room: str, package_id: str) -> dict | None:
    r = build(conn, room)
    for pk, mem in r["packages"]:
        if pk["id"] == package_id:
            d = {k: v for k, v in pk.items() if k != "member_keys"}
            d["members"] = [member_row(m) for m in mem]
            d["room"] = r["room"]
            return d
    return None
