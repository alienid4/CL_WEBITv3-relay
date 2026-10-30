"""搬遷包 v1（2026-10-31）：機房搬遷時「這些機器必須同一批搬」的分組。**只讀既有資料，不寫任何表。**

## 成包規則（兩層，設計稿 docs/ui-v2/搬遷包_設計稿_草案.md）

第一層：同一個業務系統（api_id）在所選機房的所有現役機器為一包。
  · 「現役機器」＝rack_layout.machines（machine_key 去重、退役不列）。機器是全站唯一一把尺，不另寫去重。
  · 一台機器掛兩個以上系統（api_ids 多值）＝這兩個系統共用一台機器，不可能分兩批搬 → 併包，原因「共用同一台機器」。
  · 沒有 api_id 的機器歸單獨一包「未歸屬系統」。
  · 跨機房：同系統在別的機房還有機器，標出別的機房名與台數（成員仍只含所選機房，台數加總 ＝ 該機房現役台數）。
第二層：用 observed_deps 的觀察連線（infer 已套用過期門檻與排除／樞紐規則）把「必須同批搬」的包合併。
  · 合併條件：系統 X↔Y 有連線邊，**而且證據列的兩端都是所選機房的成員機器**（不是只有系統層級的邊）。
  · 樞紐系統（監控、備份這種跟所有人都有連線）的邊不拿來合併——否則整個機房串成一包；跳過幾條會回在 meta。
  · 每個合併都附證據：哪兩台、什麼埠、最後看到時間、共幾條。連線資料不全時合併是推論，不是真相。
  · 沒有任何連線證據的包標「僅依系統登記」。
**不自動拆包**；超過門檻（app_settings relocation_pkg_max，預設 40）只標記。

## 天條
VMware 設備（ESXi／vCenter）不納入成員、也不拿它的連線當合併證據（判定同 manage_state.drop_vmware_targets：
onboard_eligibility.judge_for_asset，不做 DNS）。
"""
from __future__ import annotations

import hashlib

MAX_KEY = "relocation_pkg_max"
DEFAULT_MAX = 40
UNASSIGNED = "（未歸屬系統）"
BASIS_EDGE = "連線證據"
BASIS_REG = "僅依系統登記"
BASIS_NONE = "未歸屬系統"
EVIDENCE_SAMPLES = 5


def threshold(conn) -> int:
    import db

    try:
        v = int(str(db.get_setting(conn, MAX_KEY, str(DEFAULT_MAX)) or DEFAULT_MAX).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX
    return v if v >= 1 else DEFAULT_MAX


def _vmware_keys(conn, machines: list[dict]) -> set[str]:
    """machine_key 集合：這台機器任一筆登記是 VMware 設備（ESXi／vCenter）。"""
    import onboard_eligibility

    serial_key: dict[str, str] = {}
    for m in machines:
        for s in m["serials"]:
            serial_key[s] = m["key"]
    if not serial_key:
        return set()
    esxi = onboard_eligibility.esxi_host_names(conn)
    out: set[str] = set()
    for r in conn.execute("SELECT * FROM hardware"):
        k = serial_key.get(r["asset_serial"])
        if k is None or k in out:
            continue
        got = onboard_eligibility.judge_for_asset(conn, r, esxi_names=esxi)
        if got and got[0] == "esxi":
            out.add(k)
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


def build(conn, room: str) -> dict:
    """回 {room, threshold, total, packages:[...含 members 與 merges], meta}。唯讀。"""
    import manage_state
    import observed_deps
    import rack_layout

    room = (room or "").strip()
    all_active = [m for m in rack_layout.machines(conn) if not m["retired"]]
    vm_keys = _vmware_keys(conn, all_active)
    active = [m for m in all_active if m["key"] not in vm_keys]
    here = [m for m in active if m["room"] == room]
    by_key = {m["key"]: m for m in here}
    names = _api_names(conn)
    limit = threshold(conn)

    # 別的機房的同系統機器（跨機房標示）
    other_rooms_of: dict[str, dict[str, int]] = {}
    for m in active:
        if m["room"] == room:
            continue
        for a in m["api_ids"]:
            other_rooms_of.setdefault(a, {})
            other_rooms_of[a][m["room"]] = other_rooms_of[a].get(m["room"], 0) + 1

    uf = _UF()
    reasons: list[dict] = []          # 每個合併一筆；最後依所屬元件分配
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
                            "machine": m["name"], "ip": m["ip"], "evidence": [], "last_seen": None,
                            "evidence_count": 0})

    # 第二層：連線
    inferred = observed_deps.infer(conn)
    hubs = observed_deps.hub_ids(inferred)
    ident = observed_deps._identity(conn)           # noqa: SLF001 - 同一把 IP→機器鍵的尺，不另寫
    keys_of_ip, key_of_serial = ident["keys_of_ip"], ident["key_of_serial"]
    ip_name: dict[str, str] = {}
    for m in here:
        for ip in [m["ip"]] + list(m.get("_hw_ips") or []):
            for x in manage_state.ips_of(ip or ""):
                ip_name.setdefault(x, m["name"])
    hub_skipped = 0
    for e in inferred["edges"]:
        x, y = e["caller"], e["callee"]
        if x not in apis_here or y not in apis_here:
            continue
        if x in hubs or y in hubs:
            hub_skipped += 1
            continue
        rows = observed_deps.edge_rows(conn, x, y, inferred)
        good = []
        for r in rows:
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
            good.append((r, hk, pk))
        if not good:
            continue
        uf.union("S:" + x, "S:" + y)
        port_of = lambda r: r["local_port"] if r["direction"] == "inbound" else r["peer_port"]  # noqa: E731
        ev = [{"host": by_key[hk]["name"], "host_ip": r["host_ip"], "peer": by_key[pk]["name"],
               "peer_ip": r["peer_ip"], "direction": r["direction"], "port": port_of(r),
               "protocol": observed_deps.port_label(port_of(r)), "count": r["count"],
               "last_seen": r["last_seen"]}
              for (r, hk, pk) in sorted(good, key=lambda t: t[0]["last_seen"] or "", reverse=True)]
        reasons.append({"type": "observed_conn", "label": "觀察到連線", "apis": [x, y], "machine": None,
                        "ip": None, "evidence": ev[:EVIDENCE_SAMPLES], "evidence_count": len(ev),
                        "last_seen": max((t[0]["last_seen"] or "") for t in good) or None})

    # 元件 → 包
    comps: dict[str, dict] = {}
    for m in here:
        root = uf.find("S:" + m["api_ids"][0]) if m["api_ids"] else "UNASSIGNED"
        c = comps.setdefault(root, {"members": [], "apis": set()})
        c["members"].append(m)
        c["apis"].update(m["api_ids"])
    packages = []
    contact_cache: dict[str, list] = {}
    import blast_radius

    for root, c in comps.items():
        apis = sorted(c["apis"])
        mem = sorted(c["members"], key=lambda m: (m["rack"] or "", m["u_start"] or 0, m["name"].lower()))
        merges = [r for r in reasons if r["apis"] and r["apis"][0] in c["apis"]] if apis else []
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
        title = "、".join(names.get(a) or a for a in apis[:3]) + (f" 等 {len(apis)} 個系統" if len(apis) > 3 else "") \
            if apis else UNASSIGNED
        pk = {
            "id": _pid(room, apis or ["UNASSIGNED"]), "name": title, "count": len(mem),
            "systems": sys_txt, "api_ids": apis, "system_count": len(apis),
            "cross_room": len(other), "other_rooms": [{"room": rm, "count": n} for rm, n in sorted(other.items())],
            "basis": basis, "over_threshold": len(mem) > limit, "threshold": limit,
            "merge_summary": ("；".join(_summ(r) for r in merges) if merges else
                              ("沒有連線證據，只依系統登記" if apis else "沒有登記系統")),
            "merge_count": len(merges),
            "contacts": list(contacts.values()),
            "contact_text": "、".join(f"{p['name']}（{p['role']}）" for p in contacts.values()) or None,
            "member_keys": [m["key"] for m in mem],
            "merges": merges,
        }
        packages.append((pk, mem))
    packages.sort(key=lambda t: (-t[0]["count"], t[0]["name"]))
    return {"room": room, "threshold": limit, "total": len(here),
            "packages": packages,
            "meta": {"hub_edges_skipped": hub_skipped, "hubs": sorted(hubs),
                     "vmware_excluded": len([k for k in vm_keys]),
                     "stale_days": observed_deps.stale_days(conn)}}


def _summ(r: dict) -> str:
    if r["type"] == "shared_machine":
        return f"共用同一台機器（{r['machine']}）"
    return f"{r['apis'][0]}↔{r['apis'][1]} 連線 {r['evidence_count']} 條（最後 {r['last_seen'] or '—'}）"


def member_row(m: dict) -> dict:
    return {"key": m["key"], "hostname": m["name"], "ip": m["ip"], "room": m["room"], "rack": m["rack"],
            "u": (None if not m["u_start"] else (str(m["u_start"]) if m["u_end"] in (None, m["u_start"])
                                                  else f"{m['u_start']}-{m['u_end']}")),
            "systems": m["api_ids"], "environment": "、".join(m.get("_envs") or []) or None,
            "owner": m.get("owner")}


def list_packages(conn, room: str) -> dict:
    r = build(conn, room)
    return {"room": r["room"], "threshold": r["threshold"], "total": r["total"],
            "packages": [{k: v for k, v in pk.items() if k not in ("merges", "member_keys")} for pk, _ in r["packages"]],
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
