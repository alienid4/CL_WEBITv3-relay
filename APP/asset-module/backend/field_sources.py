"""主機頁「來源對照表」（2.060.21，S11）：同一台的用途／主機名／作業系統／環境別／負責人／業務系統／機房機櫃，
四種來源各寫什麼、各是哪天的、該信哪一個。

來源：CIA（hardware 非 DYN-/VC-/AUTO- 的登記列）、伺服器資料彙整表（srv_sheet，依這台任一 IP 對）、
系統實掃（vCenter／RVTools source_record 的 hostname、os）、納管回報（collected_os 實測 OS、hostname_observed）。
信任規則在 field_trust.json（不寫死）：
  machine  → 依 machine_order 取第一個有值的（預設 納管 > 實掃 > 彙整表 > CIA）；
  human    → 彙整表「標記時間」與 CIA 更新時間比，取較新那筆；
  location → 機櫃圖（rack_layout 的 Excel 櫃名）> 較新那筆。
有兩個以上來源、值彼此對不上、又依規則分不出（例如 human 兩邊都沒有日期）＝不猜：status=conflict，畫面兩值並列標黃。
只讀，不寫任何資產資料；確認後要改，走 CIA 修正（只寫回 CIA）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

RULES_PATH = Path(__file__).parent / "field_trust.json"


def rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def _norm(field: str, v: str | None) -> str:
    """比對用的正規化（不改顯示值）：主機名去網域小寫、作業系統取大類＋Windows 年份、環境別取正式／測試／DR、其他去空白。"""
    import srv_sheet as ss
    t = (v or "").strip()
    if not t:
        return ""
    if field == "host":
        return t.lower().split(".")[0]
    if field == "os":
        fam = ss.os_family(t) or t.lower()
        y = ss._win_year(t)
        return f"{fam} {y or ''}".strip()
    if field == "env":
        return ss.env_of(t) or t
    if field == "where":
        import system_stats
        return system_stats.normalize_location(t.split(" ")[0])
    return re.sub(r"\s+", "", t).lower()


def _same(field: str, a: str, b: str) -> bool:
    x, y = _norm(field, a), _norm(field, b)
    if field in ("owner", "system"):
        sa, sb = set(re.split(r"[、,，/;；\s]+", x)) - {""}, set(re.split(r"[、,，/;；\s]+", y)) - {""}
        return bool(sa & sb)
    return x == y


def collect(conn, key: str) -> dict:
    """這台每個欄位四種來源的值與日期。"""
    import oncall
    import srv_sheet as ss
    import system_report as sr
    groups, _of = oncall._groups(conn)
    g = groups.get(key) or []
    if not g:
        raise LookupError("找不到這台機器")
    src: dict[str, dict] = {"cia": {}, "sheet": {}, "scan": {}, "onboard": {}}
    dates: dict[str, str | None] = {}
    cia = [r for r in g if not sr.is_off_book(r["asset_serial"])]

    def first(rows, f):
        return next(((r[f] or "").strip() for r in rows if (r[f] or "").strip()), "")
    if cia:
        names = {r["api_id"]: r["name"] for r in conn.execute("SELECT api_id, name FROM business_system")}
        apis = sorted({(r["api_id"] or "").strip() for r in cia if (r["api_id"] or "").strip()})
        src["cia"] = {"purpose": first(cia, "asset_purpose"), "host": first(cia, "hostname"), "os": first(cia, "os_cia") or first(cia, "os"),
                      "env": first(cia, "environment"), "owner": "、".join(dict.fromkeys(x for r in cia for x in ((r["owner"] or "").strip(),) if x)),
                      "system": "、".join(f"{a} {names.get(a) or ''}".strip() for a in apis),
                      "where": f"{first(cia, 'physical_location')} {first(cia, 'rack_no')}".strip()}
        dates["cia"] = max((str(r["manual_updated_at"] or r["updated_at"] or "") for r in cia), default="") or None
    ips = {ip for r in g for ip in ss.ips_of(r["ip"])}
    if ips and ss._has(conn, "srv_sheet_row"):
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM srv_sheet_row WHERE ip IN ({','.join('?' * len(ips))}) ORDER BY marked_at DESC",
                                              tuple(ips))]
        if rows:
            s = rows[0]
            src["sheet"] = {"purpose": s["purpose"] or "", "host": s["host"] or "", "os": f"{s['os'] or ''} {s['os_ver'] or ''}".strip(),
                            "env": s["env"] or "", "owner": "、".join(x for x in (s["ap_owner"], s["infra_owner"]) if x),
                            "system": s["asset_name"] or "", "where": f"{s['location'] or ''} {s['rack'] or ''}".strip()}
            dates["sheet"] = s["marked_at"] or None
    hids = [r["id"] for r in g]
    if hids:
        sr_rows = conn.execute(f"SELECT payload, rowid FROM source_record WHERE resolved_hardware_id IN ({','.join('?' * len(hids))}) "
                               "ORDER BY rowid DESC", tuple(hids)).fetchall() if _has(conn, "source_record") else []
        for p in sr_rows:
            try:
                d = json.loads(p["payload"] or "{}")
            except ValueError:
                continue                       # 單筆壞掉的原始資料跳過（其餘來源照常）；不當成「沒有」以外的意思
            if d.get("hostname") or d.get("os"):
                src["scan"] = {"host": d.get("hostname") or "", "os": d.get("os") or ""}
                break
    import collected_os
    mm = collected_os.measured_map(conn)
    meas = next((mm[r["asset_serial"]] for r in g if r["asset_serial"] in mm), None)
    obs = first(g, "hostname_observed")
    if meas or obs:
        src["onboard"] = {"os": collected_os.display_text(meas["platform"], meas.get("os")) if meas else "", "host": obs}
        dates["onboard"] = (meas or {}).get("collected_at")
    return {"src": src, "dates": dates, "rack_excel": _rack_excel(conn, g)}


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _rack_excel(conn, g: list) -> str:
    """機櫃圖（人工對過的 Excel，約 95% 正確）認得的櫃名；認不得回空。"""
    try:
        import blast_radius
        import rack_layout
        names = rack_layout.rack_name_map(conn)
        for r in g:
            pr = rack_layout.parse_rack(r["rack_no"])
            if pr["kind"] == "rack":
                shown = rack_layout.display_rack(names, blast_radius.rack_room(r["physical_location"]), pr["rack"])
                if shown and shown != pr["rack"]:
                    return f"{r['physical_location'] or ''} {shown}".strip()
    except Exception:  # noqa: BLE001 - 機櫃圖讀不到＝這一條規則不適用，退回「較新那筆」；不影響其他欄
        return ""
    return ""


def compare(conn, key: str) -> dict:
    """來源對照表：每個欄位一列，四種來源的值、採用哪個、為什麼；對不上又分不出的標 conflict。"""
    cfg = rules()
    c = collect(conn, key)
    src, dates = c["src"], c["dates"]
    out = []
    for f in cfg["fields"]:
        k, rule = f["key"], f["rule"]
        vals = {s: (src[s].get(k) or "").strip() for s in cfg["sources"]}
        have = {s: v for s, v in vals.items() if v}
        agree = len({_norm(k, v) for v in have.values()}) <= 1 if k not in ("owner", "system") else all(
            _same(k, a, b) for a in have.values() for b in have.values())
        pick, why, status = None, "", "ok"
        if not have:
            status, why = "none", "四個來源都沒有"
        elif agree:
            pick = next(s for s in cfg["machine_order"] + list(cfg["sources"]) if s in have)
            why = "各來源一致" if len(have) > 1 else "只有這個來源有"
        elif rule == "machine":
            pick = next(s for s in cfg["machine_order"] if s in have)
            why = "機器自己知道的，信實掃／納管"
        elif rule == "location" and c["rack_excel"]:
            pick, why = None, "機櫃圖（人工對過的 Excel）"
            vals = {**vals, "rack": c["rack_excel"]}
        else:
            ds, dc = dates.get("sheet") or "", dates.get("cia") or ""
            cand = [s for s in ("sheet", "cia") if s in have]
            if len(cand) == 2 and ds and dc and ds[:10] != dc[:10]:
                pick = "sheet" if ds > dc else "cia"
                why = f"人決定的，信較新那筆（彙整表 {ds[:10]}／CIA {dc[:10]}）"
            elif len(cand) == 1:
                pick, why = cand[0], "只有這個來源有（其他來源是機器資訊，不代表人的決定）"
            else:
                status, why = "conflict", "對不上、分不出哪個新——不猜，請到 9.4 彙整表比對批次確認"
        by_rack = rule == "location" and "rack" in vals
        out.append({"key": k, "label": f["label"], "rule": rule, "values": vals, "pick": "rack" if by_rack else pick,
                    "pick_value": vals["rack"] if by_rack else (vals.get(pick) if pick else None),
                    "why": why, "status": status, "disagree": bool(have) and not agree})
    return {"key": key, "rows": out, "dates": dates, "sources": cfg["sources"],
            "conflicts": sum(1 for r in out if r["status"] == "conflict"), "disagree": sum(1 for r in out if r["disagree"])}
