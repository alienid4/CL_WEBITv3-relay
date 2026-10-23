"""值班一頁卡（v1.500.0，2026-10-23）。

使用者：半夜值班時打一個 IP 或主機名，一頁看完這台的一切（手機上看）。
輸入可以是 IP、主機名、資產序號、APID：
  * 對到「一台」（system_stats.machine_key 同一組）→ 回整張卡
  * APID → 回這個系統底下的機器清單，點一台再看卡
  * 對到多台／只有模糊比對 → 回候選清單，不自動挑（挑錯台比沒結果更糟）

卡的每一段都從既有的正典來源取，不另算：
  存在可信度＝existence.compute、影響範圍＝blast_radius.impact、
  最近變化＝daily_digest.machine_history、同一台＝system_stats.collapse_machines。
"""
from __future__ import annotations

import sqlite3

#: 半夜要聯絡人時，「沒資料」跟「沒有這個人」要分得出來
NOT_REGISTERED = "未登錄"


def _norm_host(h: str | None) -> str:
    import system_stats

    return system_stats.strip_domain(h or "").strip().lower()


def _groups(conn: sqlite3.Connection) -> tuple[dict[str, list], dict[str, str]]:
    """全部硬體列依 machine_key 分組（退役自成一組）。回 (groups, serial→key)。"""
    import system_stats

    rows = conn.execute("SELECT * FROM hardware").fetchall()
    groups = system_stats.collapse_machines(rows)
    of = {}
    for k, g in groups.items():
        for r in g:
            of[r["asset_serial"]] = k
    return groups, of


def _cand(k: str, g: list) -> dict:
    r = g[0]
    return {"key": k, "asset_serial": r["asset_serial"], "hostname": r["hostname"], "ip": r["ip"],
            "api_id": (r["api_id"] or "").strip() or None, "os": r["os"],
            "asset_status": r["asset_status"], "records": len(g), "retired": k.startswith("ret:")}


def lookup(conn: sqlite3.Connection, q: str) -> dict:
    """解析輸入。回 {"mode": machine|system|candidates|none, ...}。"""
    import manage_state as ms

    kw = (q or "").strip()
    if not kw:
        return {"mode": "none", "q": kw, "reason": "請輸入 IP、主機名、資產序號或 APID"}
    groups, of = _groups(conn)

    def hit_keys(pred) -> list[str]:
        ks = []
        for k, g in groups.items():
            if any(pred(r) for r in g) and k not in ks:
                ks.append(k)
        # 現役優先：同一個 IP／名字同時對到現役與退役，值班要的是現役那台
        act = [k for k in ks if not k.startswith("ret:")]
        return act or ks

    low = kw.lower()
    # 1. 資產序號（精確）
    keys = hit_keys(lambda r: (r["asset_serial"] or "").lower() == low)
    how = "資產序號"
    # 2. IP（精確，一格填兩個 IP 也認）
    if not keys:
        keys = hit_keys(lambda r: kw in ms.ips_of(r["ip"]))
        how = "IP"
    # 3. 主機名（去網域、不分大小寫）
    if not keys:
        nk = _norm_host(kw)
        keys = hit_keys(lambda r: nk and _norm_host(r["hostname"]) == nk)
        how = "主機名"
    if len(keys) == 1:
        return {"mode": "machine", "q": kw, "matched_by": how, "card": card(conn, keys[0], groups[keys[0]])}
    if keys:
        return {"mode": "candidates", "q": kw, "matched_by": how,
                "candidates": [_cand(k, groups[k]) for k in sorted(keys)]}
    # 4. APID → 這個系統的機器
    sys_keys = hit_keys(lambda r: (r["api_id"] or "").strip().lower() == low)
    if sys_keys:
        b = conn.execute("SELECT * FROM business_system WHERE lower(api_id) = ?", (low,)).fetchone()
        return {"mode": "system", "q": kw, "matched_by": "APID",
                "system": _system_info(conn, (b["api_id"] if b else kw), [g for k in sys_keys for g in groups[k]]),
                "candidates": [_cand(k, groups[k]) for k in sorted(
                    sys_keys, key=lambda k: (k.startswith("ret:"), (groups[k][0]["hostname"] or "").lower()))]}
    # 5. 模糊（主機名／IP／序號含這串字）
    fz = hit_keys(lambda r: low in (r["hostname"] or "").lower() or kw in (r["ip"] or "")
                  or low in (r["asset_serial"] or "").lower())
    if len(fz) == 1:
        return {"mode": "machine", "q": kw, "matched_by": "部分比對", "card": card(conn, fz[0], groups[fz[0]])}
    if fz:
        return {"mode": "candidates", "q": kw, "matched_by": "部分比對",
                "candidates": [_cand(k, groups[k]) for k in sorted(fz)[:50]], "total": len(fz)}
    return {"mode": "none", "q": kw, "reason": "資產庫裡找不到（IP／主機名／資產序號／APID 都比對過）"}


def _system_info(conn: sqlite3.Connection, api_id: str, rows: list) -> dict:
    b = conn.execute("SELECT * FROM business_system WHERE api_id = ?", (api_id,)).fetchone()
    avail = max((r["availability"] or 0 for r in rows), default=0)
    cls = None
    if b:
        cls = (b["sys_class_override"] or b["sys_class"]) if "sys_class_override" in b.keys() else b["sys_class"]
    return {"api_id": api_id, "name": (b["name"] if b else None),
            "ap_department": b["ap_department"] if b else None,
            "ap_owner": b["ap_owner"] if b else None,
            "sys_class": cls, "availability": avail or None,
            # 跟 4-2 影響範圍同一條：可用性 A ≥ 3＝重大
            "severity": "重大" if avail >= 3 else "一般",
            "machines": len({r["asset_serial"] for r in rows})}


def _esxi_of(conn: sqlite3.Connection, serials: list[str]) -> list[dict]:
    out = []
    try:
        for s in serials:
            for r in conn.execute(
                    "SELECT e.dst_node_id, n.label, n.asset_serial FROM ci_edge e "
                    "LEFT JOIN ci_node n ON n.node_id = e.dst_node_id "
                    "WHERE e.src_node_id = ? AND e.edge_type = 'runs_on' AND e.gone_at IS NULL "
                    "AND e.dst_node_id LIKE 'esxi:%'", (f"hw:{s}",)):
                if not any(x["node_id"] == r[0] for x in out):
                    out.append({"node_id": r[0], "label": r[1] or r[0], "asset_serial": r[2]})
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        return out          # 還沒建過 CI 圖譜：不知道跑在哪台 ESXi（畫面顯示「—」）
    return out


def _impact(conn: sqlite3.Connection, serial: str) -> dict:
    """呼叫 4-2 影響範圍的同一支計算，只取摘要（受影響系統數）。算不出來要講原因。"""
    import blast_radius

    try:
        res = blast_radius.resolve(conn, serial)
        if res.get("status") != "resolved":
            return {"ok": False, "reason": "CI 圖譜裡沒有這台（還沒重建圖譜或不在圖譜範圍）",
                    "link": f"/blast?q={serial}&mode=incident"}
        imp = blast_radius.impact(conn, res["node_id"])
        systems = imp["summary"]["by_biz_system"]
        return {"ok": True, "node_id": res["node_id"], "systems": len(systems),
                "major": sum(1 for s in systems if s.get("severity") == "重大"),
                "hosts": len(imp["dependents"]),
                "system_list": [{"api_id": s["api_id"], "name": s.get("name"), "severity": s.get("severity")}
                                for s in systems],
                "deps_available": imp.get("deps_available"),
                "link": f"/blast?q={serial}&mode=incident"}
    except Exception as exc:  # noqa: BLE001 - 影響範圍算不出來要講原因，不可以讓整張卡掛掉
        return {"ok": False, "reason": f"影響範圍算不出來：{type(exc).__name__}: {exc}",
                "link": f"/blast?q={serial}&mode=incident"}


def card(conn: sqlite3.Connection, key: str, g: list) -> dict:
    import daily_digest
    import existence
    import manage_state as ms
    import rack_layout
    import system_stats

    rows = [dict(r) for r in g]
    rep = rows[0]
    serials = [r["asset_serial"] for r in rows]
    ips: list[str] = []
    for r in rows:
        for ip in ms.ips_of(r["ip"]):
            if ip not in ips:
                ips.append(ip)

    # 是什麼
    ex = None
    for m in existence.compute(conn)["machines"]:
        if set(serials) & set(m["serials"]):
            ex = m
            break
    what = {"hostname": rep["hostname"], "ips": ips, "serials": serials,
            "model": rep["device_model"], "os": rep["os"],
            "environment": system_stats.normalize_env(rep["environment"]),
            "is_vm": bool(ms.is_vm_value(rep.get("is_vm"), rep.get("device_model"))),
            "asset_status": rep["asset_status"],
            "exist_level": ex["level"] if ex else None, "exist_label": ex["level_label"] if ex else None,
            "exist_score": ex["score"] if ex else None, "retired": key.startswith("ret:")}

    # 哪些系統
    apis = sorted({(r["api_id"] or "").strip() for r in rows if (r["api_id"] or "").strip()})
    systems = []
    for a in apis:
        srows = conn.execute("SELECT availability, asset_serial FROM hardware WHERE TRIM(api_id) = ?",
                             (a,)).fetchall()
        systems.append(_system_info(conn, a, srows))

    # 在哪
    pr = rack_layout.parse_rack(rep["rack_no"])
    where = {"room": system_stats.normalize_location(rep["physical_location"]),
             "rack": pr["rack"] if pr["kind"] == "rack" else ("VM" if pr["kind"] == "vm" else pr["raw"]),
             "u_start": pr["u_start"], "u_end": pr["u_end"], "rack_raw": rep["rack_no"],
             "esxi": _esxi_of(conn, serials)}

    # 找誰
    contacts = []
    for s in serials:
        for p in conn.execute("SELECT person_name, phone, proxy1, proxy1_phone, belong_division, "
                              "belong_department, job_desc FROM personnel WHERE asset_serial = ?", (s,)):
            if p["person_name"] and not any(c["name"] == p["person_name"] for c in contacts):
                contacts.append({"name": p["person_name"], "phone": p["phone"] or NOT_REGISTERED,
                                 "proxy": p["proxy1"], "proxy_phone": p["proxy1_phone"],
                                 "dept": " ".join(x for x in (p["belong_division"], p["belong_department"]) if x)
                                 or None, "job": p["job_desc"]})
    who = {"owner": rep["owner"] or NOT_REGISTERED, "custodian": rep["custodian"] or NOT_REGISTERED,
           "ap_user": rep["user_name"] or NOT_REGISTERED, "usage_unit": rep["usage_unit"] or NOT_REGISTERED,
           "ap_owner": next((s["ap_owner"] for s in systems if s.get("ap_owner")), None) or NOT_REGISTERED,
           "ap_department": next((s["ap_department"] for s in systems if s.get("ap_department")), None)
           or NOT_REGISTERED,
           "contacts": contacts, "contacts_note": None if contacts else NOT_REGISTERED}

    # 最近收集／健康
    health = None
    try:
        for ip in ips:
            h = conn.execute("SELECT taken_at, overall, reachable, mem_pct, load_per, source FROM health_snapshot "
                             "WHERE ip = ? ORDER BY taken_at DESC LIMIT 1", (ip,)).fetchone()
            if h and (health is None or h["taken_at"] > health["taken_at"]):
                health = {**dict(h), "ip": ip}
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        health = None          # 舊庫沒有健檢表：畫面顯示「沒有健檢紀錄」
    collect = {"status": ex["collect"] if ex else None,
               "last_collect": (ex["last_seen"] or {}).get("collect") if ex else None,
               "last_seen_any": ex["last_seen_any"] if ex else None,
               "health": health}

    mkey = key[4:] if key.startswith("ret:") else key
    return {"key": key, "what": what, "systems": systems, "where": where, "who": who,
            "impact": _impact(conn, rep["asset_serial"]),
            "changes": daily_digest.machine_history(conn, mkey, 14),
            "collect": collect}
