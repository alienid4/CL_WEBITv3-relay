"""存在可信度：這台機器「真的存在嗎」——跟「業務資料完整度（CIA 有沒有登記）」是兩件事。

決策 EXIST1（使用者 2026-10-18，原話）：「CIA 表是最致命的缺點，完整率可能只有六、七成；
DY 可能九成；如果我真的能納管，就代表 100% 沒問題。CIA 漏登是 CIA 表不正確，事實上就是有這幾台。」

所以一台機器拆成兩個分數，**不可以混成一個**：

1. 存在可信度（本檔）：看證據＋時效，取這台所有證據裡最高的一項。配分／天數在
   `existence_rules.json`，不寫死：
     - 已納管：收集帳號 7 天內連得上 ............................... 100
     - 在 DY／vCenter（RVTools）**最新一次匯入**裡 ................... 90
     - 收集帳號 8–30 天內連上過／在所屬網段**最近一次掃描**活著 ....... 70
     - 只有 CIA（任何來源的最新快照都沒有它、30 天內也沒收集到）..... 60
     - 什麼都沒有 .................................................. 0
   匯入／掃描類證據看「最新快照有沒有它」，不看日曆天數（2026-10-18 修正：日曆天數會把
   「來源資料太舊」誤當成「機器不見了」）。來源本身太久沒更新（> source_stale_days）只出
   「來源過期」警示、不降級。收集帳號證據仍按時間（7 天／30 天）。
   分數 ≥ count_threshold（70）＝**計入台數**；沒達到＝**疑似下線**（不計入、另列清單）。
   退役（停用／報廢／閒置）＝備查：自成一組、不計入、不算疑似下線。
   ⇒ 計入 ＋ 疑似下線 ＋ 退役 ＝ 全部機器（verify_numbers 釘住）。

2. 業務資料完整度：有沒有 CIA 登記（序號不是 DYN-/VC-/AUTO- 帳外前綴，正典
   `system_report.is_off_book`）。業務欄位（負責人／AP ID／用途／環境）**只從 CIA 來**，
   規則不變（Q77：CIA 正本優先於 DY 空殼）。
   CIA 完整率 ＝ 計入的機器裡有 CIA 登記的 ÷ 計入的機器。

「已納管」全站只有一個定義（首頁／2-1／3-8 都讀這裡）：收集帳號 7 天內連得上，
**跟 CIA 有沒有登記無關**（推翻 1.450.0「CIA 已登記＋連得上」的交叉引用折衷）。
超過 7 天＝「已納管但失聯」。證據來源：
  - hardware.collect_ok=1 且 collect_checked_at 在 7 天內（整台：同一台任一筆成立就算）
  - host_account.last_seen（帳號收集）／host_service（ssh_ss、winrm 服務收集）在 7 天內
collect_ok=1 但沒有時間（collect_checked_at 空白）＝不知道什麼時候連上的 → 不算 7 天內。

DY-only（沒有 CIA、沒有掃到、沒有納管，只有 DY 存活清單說它在）照決策 (1) **計入但標待確認**。
vCenter 不在待確認之列：那是 hypervisor 自己的清單，不是匯入的存活推測。

同一台＝正典 `system_stats.machine_key`（全站唯一一把尺），不自己另寫去重。
"""
from __future__ import annotations

import ipaddress
import json
from datetime import datetime, timedelta
from pathlib import Path

RULES_PATH = Path(__file__).with_name("existence_rules.json")

L_ONBOARDED = "onboarded"
L_INVENTORY = "inventory"
L_COLLECT_STALE = "collect_stale"
L_SCAN = "scan"
L_CIA_ONLY = "cia_only"
L_NONE = "none"
LEVEL_ORDER = (L_ONBOARDED, L_INVENTORY, L_COLLECT_STALE, L_SCAN, L_CIA_ONLY, L_NONE)

#: 下鑽清單的分桶。每一桶都是 compute() 同一份 machines 篩出來的（加總＝下鑽）。
BUCKETS = ("all", "counted", "suspect_offline", "retired", "cia_registered", "cia_pending",
           "pending_confirm", "onboarded", "onboarded_stale")
BUCKET_LABEL = {
    "all": "全部機器", "counted": "計入台數", "suspect_offline": "疑似下線",
    "retired": "已退役（備查）", "cia_registered": "計入且 CIA 有登記",
    "cia_pending": "CIA 待補登", "pending_confirm": "只有 DY 看得到（待確認）",
    "onboarded": "已納管", "onboarded_stale": "已納管但失聯",
}


def load_rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def _cut(now: datetime, days: int) -> str:
    return (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _ts(v) -> str | None:
    s = str(v or "").strip()
    return s or None


def _max(*vals):
    xs = [v for v in vals if v]
    return max(xs) if xs else None


def _mkey(hostname, ip, serial) -> str:
    import system_stats

    return system_stats.machine_key(hostname, ip, serial)


def extra_key(hostname, ip) -> str:
    """資產庫沒有的機器（只有掃描／收集看到）的機器鍵。

    ⚠️ 不可以直接 machine_key(hostname, ip, None)：沒有主機名時舊口徑會退回序號，
    None 序號全部變成同一個 'sn:None'，幾十台被併成一台。用 IP 當序號佔位。"""
    return _mkey(hostname, ip, f"ip:{(ip or '').strip()}")


def item_key(hostname, ip, serial) -> str:
    """一列（有序號＝資產庫的登記；沒序號＝只有掃描／收集看到）屬於哪一台。
    manage_state 首頁四態、pipeline 漏斗、existence 都用這一支，三邊的「台」才是同一把尺。"""
    return _mkey(hostname, ip, serial) if serial else extra_key(hostname, ip)


class CollectIndex:
    """「收集帳號連得上」的證據索引：整台（machine_key）＋逐 IP。

    manage_state.summarize（首頁已納管）跟 existence.compute（存在可信度）共用這一份，
    「已納管」才只有一個定義、兩邊數字對得起來。"""

    def __init__(self, conn, now: datetime | None = None, rules: dict | None = None):
        import manage_state as ms

        self.rules = rules or load_rules()
        self.now = now or datetime.now()
        self.cut_fresh = _cut(self.now, int(self.rules["onboarded_days"]))
        self.cut_evidence = _cut(self.now, int(self.rules["collect_evidence_days"]))
        # 整台：任一筆 collect_ok=1 → 這台「連上過」；時間取最新
        self.key_ok: dict[str, str | None] = {}
        for r in conn.execute(
                "SELECT hostname, ip, asset_serial, collect_ok, collect_checked_at FROM hardware"):
            if r["collect_ok"] != 1:
                continue
            k = _mkey(r["hostname"], r["ip"], r["asset_serial"])
            self.key_ok[k] = _max(self.key_ok.get(k), _ts(r["collect_checked_at"]))
        # 逐 IP：帳號收集、服務收集（走收集帳號的那幾種來源）
        self.ip_ts: dict[str, str] = {}
        self._add_ip(conn, "SELECT ip, MAX(last_seen) FROM host_account GROUP BY ip", ())
        srcs = tuple(self.rules.get("collect_service_sources") or ())
        if srcs:
            ph = ",".join("?" for _ in srcs)
            self._add_ip(conn, f"SELECT ip, MAX(last_seen) FROM host_service WHERE source IN ({ph}) "
                         "GROUP BY ip", srcs)
        self._ips_of = ms.ips_of

    def _add_ip(self, conn, sql: str, params: tuple) -> None:
        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception:  # noqa: BLE001 - 舊 DB 沒這張表＝沒有這種證據，不是錯
            return
        for ip, ts in rows:
            ip, ts = (ip or "").strip(), _ts(ts)
            if ip and ts:
                self.ip_ts[ip] = _max(self.ip_ts.get(ip), ts)

    def last_collect(self, keys, ips) -> tuple[bool, str | None]:
        """(連上過嗎, 最近一次連上的時間)。collect_ok=1 但沒有時間＝連上過、時間不明。"""
        ever, last = False, None
        for k in keys:
            if k in self.key_ok:
                ever = True
                last = _max(last, self.key_ok[k])
        for ip in ips:
            if ip in self.ip_ts:
                ever = True
                last = _max(last, self.ip_ts[ip])
        return ever, last

    def status(self, keys, ips) -> str | None:
        """'fresh'（7 天內連上）／'stale'（連上過但超過 7 天或時間不明）／None（沒連上過）。"""
        ever, last = self.last_collect(keys, ips)
        if not ever:
            return None
        return "fresh" if (last and last >= self.cut_fresh) else "stale"

    def row_status(self, hostname, ip, serial) -> str | None:
        return self.status([_mkey(hostname, ip, serial)], self._ips_of(ip))


def compute(conn, now: datetime | None = None, rules: dict | None = None) -> dict:
    """全部機器的存在可信度＋彙總。回 {"machines": [...], "summary": {...}, "rules": ...}。

    一次查完，逐台不打 SQL。summary 的每個數字都是 machines 篩出來的筆數（加總＝下鑽）。"""
    import manage_state as ms
    import system_report as sr

    rules = rules or load_rules()
    now = now or datetime.now()
    idx = CollectIndex(conn, now, rules)
    cut30 = idx.cut_evidence          # 只用在「收集帳號 8–30 天」那一級
    levels = rules["levels"]
    threshold = int(rules["count_threshold"])
    inv_sources = tuple(rules.get("inventory_sources") or ())
    pending_srcs = set(rules.get("pending_confirm_sources") or ())
    stale_days = int(rules.get("source_stale_days", 60))
    sources = source_snapshots(conn, now, rules)
    stale_src = {s["source"] for s in sources if s["stale"]}

    groups: dict[str, dict] = {}
    by_id: dict[int, str] = {}
    ip_index: dict[str, set] = {}
    host_index: dict[str, set] = {}
    for r in conn.execute(
            "SELECT id, asset_serial, hostname, ip, os, mac, hw_serial, vi_sdk_server, asset_status, "
            "owner, custodian, user_name, usage_unit FROM hardware"):
        retired = (r["asset_status"] or "").strip() in ms.RETIRED_STATUS
        base = _mkey(r["hostname"], r["ip"], r["asset_serial"])
        k = ("ret:" if retired else "") + base   # 退役＝備查，不跟現役合併（跟 manage_state 同一條）
        g = groups.setdefault(k, {"key": k, "base_keys": set(), "rows": [], "retired": retired,
                                  "ips": set(), "ev": {}, "inv_src": set(), "scan": False, "extra": False})
        g["rows"].append(dict(r))
        g["base_keys"].add(base)
        by_id[r["id"]] = k
        for ip in ms.ips_of(r["ip"]):
            g["ips"].add(ip)
            ip_index.setdefault(ip, set()).add(k)
        if r["ip"]:
            ip_index.setdefault(r["ip"], set()).add(k)
        if r["hostname"]:
            host_index.setdefault(r["hostname"], set()).add(k)

    def _hits(ip, hostname) -> set:
        """跟 manage_state 同一條比對規則（IP 或主機名任一對得上＝同一台，_row_in_keys）。
        現役優先：同時對到現役與退役，證據只給現役那台。"""
        h = set(ip_index.get(ip or "", ())) | set(host_index.get(hostname or "", ()))
        act = {k for k in h if not groups[k]["retired"]}
        return act or h

    def _ev(g, code, ts):
        g["ev"][code] = _max(g["ev"].get(code), ts)

    # ── DY／vCenter（RVTools）：這台在不在那個來源的**最新一次匯入**裡（不是「N 天內」——
    #    來源本身多久沒更新是另一件事，用來源過期警示講，不拿來把機器降級）。
    #    最新一次匯入＝collected_at 在該來源最新時間往前 snapshot_window_hours 內（重匯會更新
    #    collected_at，沒出現在新檔裡的列會留著舊時間）。ambiguous（待人工比對）不算新機器。
    snap_from = {s["source"]: s["snapshot_from"] for s in sources if s.get("snapshot_from")}
    dy_hint: dict[str, list] = {}
    if inv_sources:
        ph = ",".join("?" for _ in inv_sources)
        for r in conn.execute(
                f"SELECT source, resolved_hardware_id, collected_at, payload FROM source_record "
                f"WHERE source IN ({ph}) AND resolved_hardware_id IS NOT NULL", inv_sources):
            k = by_id.get(r["resolved_hardware_id"])
            if not k:
                continue
            g = groups[k]
            ts = _ts(r["collected_at"])
            _ev(g, f"inv:{r['source']}", ts)
            if ts and ts >= snap_from.get(r["source"], "9999"):
                g["inv_src"].add(r["source"])
            if r["source"] == "dynassets" and r["payload"]:
                try:
                    p = json.loads(r["payload"])
                except (ValueError, TypeError):
                    p = {}
                for f in ("department", "ap"):
                    if p.get(f):
                        dy_hint.setdefault(k, []).append(f"DY {f}：{p[f]}")

    # ── 網路掃描：這台 IP 所在網段**最近一次掃完**的那次掃描裡有沒有活著
    #    （有涵蓋紀錄就逐網段看；那段沒有涵蓋紀錄就看最新一批掃描）
    latest_scan, cover = _scan_cover(conn)

    def _scan_time_for(ip) -> str | None:
        best = None
        if ip:
            try:
                a = ipaddress.ip_address(ip)
            except ValueError:
                a = None
            if a is not None:
                for net, t in cover:
                    if a in net and (best is None or t > best):
                        best = t
        return best or latest_scan

    scan_extra: dict[str, dict] = {}
    need = sorted({t for _, t in cover} | ({latest_scan} if latest_scan else set()))
    alive = []
    for i in range(0, len(need), 400):
        chunk = need[i:i + 400]
        ph = ",".join("?" for _ in chunk)
        alive += conn.execute(
            f"SELECT ip, hostname, scan_time AS t, mac FROM scan_history "
            f"WHERE scan_ok = 1 AND scan_time IN ({ph})", chunk).fetchall()
    for r in alive:
        if not r["ip"] and not r["hostname"]:
            continue
        if r["t"] != _scan_time_for(r["ip"]):
            continue                      # 不是這段最近一次的掃描結果
        hits = _hits(r["ip"], r["hostname"])
        if hits:
            for k in hits:
                groups[k]["scan"] = True
                _ev(groups[k], "scan", _ts(r["t"]))
            continue
        k = extra_key(r["hostname"], r["ip"])
        g = groups.get(k) or scan_extra.get(k)
        if g is None:
            g = {"key": k, "base_keys": {k}, "rows": [], "retired": False, "ips": set(), "ev": {},
                 "inv_src": set(), "scan": False, "extra": True,
                 "seen": {"hostname": r["hostname"], "ip": r["ip"], "mac": r["mac"]}}
            scan_extra[k] = g
        if r["ip"]:
            g["ips"].add(r["ip"])
        g["scan"] = True
        _ev(g, "scan", _ts(r["t"]))
    groups.update(scan_extra)
    extra_ips = {ip for g in scan_extra.values() for ip in g["ips"]}

    # ── 2-1 開始收集的探測（2026-10-20）：每個位址最新一次、N 天內有回應＝跟網路掃描活著同一級證據。
    #    跟首頁四態的「掃到」同一份（scan_scope.recent_coverage）。只替資產庫裡的機器加證據，
    #    不另生新機器（整片網段掃到、CIA 沒登記的位址，留給 2-1 自己的清單處理）。
    import scan_scope

    for ip, t in scan_scope.recent_coverage(conn, now=now)["alive"].items():
        for k in _hits(ip, None):
            if k in groups:
                groups[k]["scan"] = True
                _ev(groups[k], "scan", _ts(t))

    # ── 收集帳號收得到、但資產庫跟掃描都沒有的 IP（CIA 無關：收得到就是有這台）
    for ip in sorted(idx.ip_ts):
        if ip in ip_index or ip in extra_ips:
            continue
        k = extra_key(None, ip)
        if k in groups:
            groups[k]["ips"].add(ip)
            continue
        groups[k] = {"key": k, "base_keys": {k}, "rows": [], "retired": False, "ips": {ip},
                     "ev": {}, "inv_src": set(), "scan": False, "extra": True,
                     "seen": {"hostname": None, "ip": ip, "mac": None}}

    machines = []
    for g in groups.values():
        rows = sorted(g["rows"], key=lambda x: (sr.is_off_book(x["asset_serial"]), x["asset_serial"] or ""))
        has_cia = any(not sr.is_off_book(x["asset_serial"]) for x in rows)
        cstat = idx.status(g["base_keys"], g["ips"])
        _, last_c = idx.last_collect(g["base_keys"], g["ips"])
        if last_c:
            _ev(g, "collect", last_c)
        inv_fresh = bool(g["inv_src"])
        scan_fresh = g["scan"]
        collect_recent = cstat is not None and (last_c or "") >= cut30
        if cstat == "fresh":
            level = L_ONBOARDED
        elif inv_fresh:
            level = L_INVENTORY
        elif collect_recent:
            level = L_COLLECT_STALE
        elif scan_fresh:
            level = L_SCAN
        elif has_cia:
            level = L_CIA_ONLY
        else:
            level = L_NONE
        score = int(levels[level]["score"])
        retired = g["retired"]
        counted = (not retired) and score >= threshold
        evidence = []
        if cstat == "fresh":
            evidence.append("onboarded")
        evidence += sorted(f"inv:{s}" for s in g["inv_src"])
        if collect_recent and cstat != "fresh":
            evidence.append("collect_stale")
        if scan_fresh:
            evidence.append("scan")
        pending = (counted and not has_cia and cstat is None and not scan_fresh
                   and inv_fresh and g["inv_src"] <= pending_srcs)

        def first(f):
            for x in rows:
                if x.get(f):
                    return x[f]
            return (g.get("seen") or {}).get(f)

        hints = []
        for x in rows:
            for f, lab in (("owner", "擁有者"), ("custodian", "保管者"), ("user_name", "使用者"),
                           ("usage_unit", "使用單位")):
                if x.get(f) and f"{lab}：{x[f]}" not in hints:
                    hints.append(f"{lab}：{x[f]}")
        hints += [h for h in dy_hint.get(g["key"], []) if h not in hints]
        rep = rows[0] if rows else None
        machines.append({
            "key": g["key"],
            "asset_serial": rep["asset_serial"] if rep else None,
            "serials": [x["asset_serial"] for x in rows],
            "hostname": first("hostname"), "ip": first("ip"), "os": first("os"),
            "mac": first("mac"), "hw_serial": first("hw_serial"), "vcenter": first("vi_sdk_server"),
            "has_cia": has_cia,
            "level": level, "level_label": levels[level]["label"], "score": score,
            "evidence": evidence, "last_seen": g["ev"],
            "last_seen_any": _max(*g["ev"].values()),
            "collect": cstat,   # fresh／stale／None
            "onboarded": (not retired) and cstat == "fresh",
            "onboarded_stale": (not retired) and cstat == "stale",
            "counted": counted, "suspect_offline": (not retired) and not counted,
            "retired": retired, "pending_confirm": pending,
            "in_asset_db": bool(rows),
            "suggested_owner": "；".join(hints[:4]) or None,
            # 這台的證據裡，哪些來源本身已經太久沒更新（不降級，只標「來源過期」）
            "stale_evidence": sorted(
                ({s for s in g["inv_src"]} | ({"scan"} if scan_fresh else set())) & stale_src),
        })
    machines.sort(key=lambda m: (m["retired"], -m["score"], (m["hostname"] or "").lower(), m["ip"] or "", m["key"]))
    summary = summarize_machines(machines, rules)
    summary["sources"] = sources
    summary["stale_sources"] = [s for s in sources if s["stale"]]
    summary["source_stale_days"] = stale_days
    return {"machines": machines, "summary": summary,
            "rules": rules, "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
            "cutoff_onboarded": idx.cut_fresh, "cutoff_evidence": cut30}


SOURCE_LABEL = {"dynassets": "DY", "vcenter": "vCenter／RVTools", "scan": "網路掃描"}


def _scan_cover(conn) -> tuple[str | None, list]:
    """(最新一批掃描時間, [(網段, 這段最近一次掃完的時間)])。沒涵蓋紀錄（舊 DB）→ 空清單。"""
    latest = conn.execute("SELECT MAX(scan_time) FROM scan_history").fetchone()[0]
    cover = []
    try:
        for cidr, t in conn.execute(
                "SELECT cidr, MAX(scan_time) FROM scan_coverage WHERE ok = 1 GROUP BY cidr"):
            try:
                cover.append((ipaddress.ip_network(cidr, strict=False), t))
            except ValueError:
                continue
    except Exception:  # noqa: BLE001 - 舊 DB 沒有 scan_coverage
        pass
    return latest, cover


def source_snapshots(conn, now: datetime | None = None, rules: dict | None = None) -> list[dict]:
    """每個來源最新一次快照的時間、年齡、是不是過期（> source_stale_days）。

    過期只拿來**警示**（「DY 資料已 N 天沒更新，請重匯」），不拿來把機器降級——
    來源沒更新不代表機器不在了（EXIST1 修正，2026-10-18）。"""
    rules = rules or load_rules()
    now = now or datetime.now()
    win = timedelta(hours=float(rules.get("snapshot_window_hours", 6)))
    stale_days = int(rules.get("source_stale_days", 60))
    out = []

    def _one(src, latest):
        if not latest:
            out.append({"source": src, "label": SOURCE_LABEL.get(src, src), "latest": None,
                        "age_days": None, "stale": False, "snapshot_from": None})
            return
        try:
            lt = datetime.strptime(str(latest)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            lt = None
        age = (now - lt).days if lt else None
        out.append({"source": src, "label": SOURCE_LABEL.get(src, src), "latest": latest,
                    "age_days": age, "stale": age is not None and age > stale_days,
                    "snapshot_from": (lt - win).strftime("%Y-%m-%d %H:%M:%S") if lt else latest})

    for src in rules.get("inventory_sources") or ():
        _one(src, conn.execute("SELECT MAX(collected_at) FROM source_record WHERE source = ?",
                               (src,)).fetchone()[0])
    _one("scan", conn.execute("SELECT MAX(scan_time) FROM scan_history").fetchone()[0])
    return out


def in_bucket(m: dict, bucket: str) -> bool:
    if bucket == "all":
        return True
    if bucket == "cia_registered":
        return m["counted"] and m["has_cia"]
    if bucket == "cia_pending":
        return m["counted"] and not m["has_cia"]
    return bool(m.get(bucket))


def summarize_machines(machines: list[dict], rules: dict | None = None) -> dict:
    """彙總：每個數字＝in_bucket 篩出來的筆數，跟下鑽同一份判斷。"""
    rules = rules or load_rules()
    cnt = {b: sum(1 for m in machines if in_bucket(m, b)) for b in BUCKETS}
    by_level = {lv: sum(1 for m in machines if not m["retired"] and m["level"] == lv)
                for lv in LEVEL_ORDER}
    counted = cnt["counted"]
    return {
        "total_machines": cnt["all"],
        "counted": counted,
        "suspect_offline": cnt["suspect_offline"],
        "retired": cnt["retired"],
        "cia_registered": cnt["cia_registered"],
        "cia_pending": cnt["cia_pending"],
        # CIA 完整率：沒有計入的機器時回 None（不是 0%——0 是「真的一台都沒登記」）
        "cia_complete_pct": round(cnt["cia_registered"] * 100.0 / counted, 1) if counted else None,
        "pending_confirm": cnt["pending_confirm"],
        "onboarded": cnt["onboarded"],
        "onboarded_stale": cnt["onboarded_stale"],
        "by_level": by_level,
        "level_score": {lv: int(rules["levels"][lv]["score"]) for lv in LEVEL_ORDER},
        "level_label": {lv: rules["levels"][lv]["label"] for lv in LEVEL_ORDER},
        "count_threshold": int(rules["count_threshold"]),
        "onboarded_days": int(rules["onboarded_days"]),
        "collect_evidence_days": int(rules["collect_evidence_days"]),
    }


def machines_in(conn, bucket: str = "all", level: str | None = None,
                now: datetime | None = None) -> dict:
    if bucket not in BUCKETS:
        raise ValueError(f"不合法的分桶：{bucket}；合法值：{'、'.join(BUCKETS)}")
    if level is not None and level not in LEVEL_ORDER:
        raise ValueError(f"不合法的等級：{level}；合法值：{'、'.join(LEVEL_ORDER)}")
    out = compute(conn, now=now)
    items = [m for m in out["machines"] if in_bucket(m, bucket)
             and (level is None or (m["level"] == level and not m["retired"]))]
    return {"bucket": bucket, "bucket_label": BUCKET_LABEL[bucket], "level": level,
            "total": len(items), "items": items, "as_of": out["as_of"]}


def for_serial(conn, asset_serial: str, now: datetime | None = None) -> dict | None:
    """單台（資產詳細頁的「存在可信度」徽章）。"""
    for m in compute(conn, now=now)["machines"]:
        if asset_serial in m["serials"]:
            return m
    return None


CIA_PENDING_HEADERS = ["項次", "主機名稱", "IP", "作業系統", "MAC", "硬體序號", "vCenter",
                       "存在可信度", "依據", "最後看到", "建議窗口（線索）", "待確認", "系統內序號"]

_EV_LABEL = {"onboarded": "已納管", "inv:dynassets": "DY", "inv:vcenter": "vCenter／RVTools",
             "collect_stale": "收集帳號（超過 7 天）", "scan": "網路掃描"}


def cia_pending_rows(conn, now: datetime | None = None) -> list[list]:
    """「CIA 補登清單」：計入台數、但 CIA 沒登記的機器，預填我們收到的事實給 CIA 窗口。"""
    out = []
    items = machines_in(conn, "cia_pending", now=now)["items"]
    for i, m in enumerate(items, 1):
        out.append([i, m["hostname"], m["ip"], m["os"], m["mac"], m["hw_serial"], m["vcenter"],
                    m["score"], "、".join(_EV_LABEL.get(e, e) for e in m["evidence"]),
                    m["last_seen_any"], m["suggested_owner"], "是" if m["pending_confirm"] else "",
                    "、".join(s for s in m["serials"] if s)])
    return out
