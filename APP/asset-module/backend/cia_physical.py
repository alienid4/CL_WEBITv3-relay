"""S1 實體機補登（2.061.2）：CIA 沒登記的實體機，帶證據（含日期）與預填，人確認後交 CIA 補登。

設計：docs/設計_CIA第三批_S1toS4_2026-11-11.md「S1」＋ GPT 第 3（三態）、4（機櫃圖 Excel 勝但並列兩邊）、5（證據帶日期）點。

## 母體
2.1「未登錄 CIA」（cia_reg._fleet 的 in_nocia，跟 9.4 VM 補登同一份）∩ 不是虛擬機（vm_keys，全站一個定義）
∩ 不是 VMware 設備（天條：ESXi／vCenter 只從 vCenter／vROps 取資料，不當補登候選）。

## 三態（GPT 第 3 點：排除法不可靠）
實體＝有硬體序號而且型號像伺服器，或機櫃圖 Excel 有這台；非實體＝型號／作業系統／主機名像虛擬或容器；其餘＝無法判定（另列，不進補登）。
規則在 cia_physical_rules.json（不寫死）。

## 證據（依強弱，每個都帶觀測日期；GPT 第 5 點）
① 伺服器資料彙整表有這台（2.060.20「表有、CIA 無」）＝最強；② 機櫃圖 Excel（rack_excel_run 最近一次）有這台；③ 存活掃描／收集最後看到。
兩個證據的日期相差超過 evidence_max_gap_days（預設 30）不算互相佐證 → 標「單一來源」。

## 預填
機房／機櫃＝機櫃圖 Excel（人工對過、衝突時 Excel 勝；使用者 2026-11 定），並列「彙整表位置」；兩邊對不上標黃、不預填。
廠牌／型號／序號＝系統收集，沒有才用彙整表。APID＝彙整表「資訊資產名稱」完全等於某業務系統名稱 → 建議；
用 CIA 已登記、也在彙整表的機器回測這條規則，準確率 ≥ apid_precision_min 才預填，否則「無法判定」（不硬猜）。

不寫 hardware、不改 CIA；確認後的清單匯出交 CIA 管理人補登。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import cia_case

KIND = "physical"
RULES_PATH = Path(__file__).parent / "cia_physical_rules.json"
STATE_LABEL = {"physical": "實體", "virtual": "非實體", "unknown": "無法判定"}


def load_rules() -> dict:
    r = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    r["_srv"] = [re.compile(p, re.I) for p in r.get("server_model_patterns", [])]
    r["_virt"] = [re.compile(p, re.I) for p in r.get("virtual_patterns", [])]
    return r


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _d(v) -> str | None:
    """日期字串 → YYYY-MM-DD（認不出就 None）。"""
    m = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", _s(v))
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def _first(rows, f) -> str:
    return next((_s(r.get(f)) for r in rows if _s(r.get(f))), "")


def _sheet_rows(conn) -> tuple[dict, str | None]:
    """machine_key → 彙整表那一列（原始欄位＋比對結果），以及彙整表的資料日期。"""
    import srv_sheet

    a = srv_sheet.analyze(conn)
    b = a.get("batch") or {}
    date = _d(b.get("data_date")) or _d(b.get("imported_at"))
    raw = {}
    if a["rows"]:
        raw = {r["row_no"]: dict(r) for r in conn.execute("SELECT * FROM srv_sheet_row")}
    out = {}
    for r in a["rows"]:
        if r.get("machine_key") and not r.get("off"):
            out.setdefault(r["machine_key"], {**raw.get(r["row_no"], {}), "kinds": r["kinds"]})
    return out, date


def _rack_rows(conn) -> dict:
    """machine_key → 機櫃圖 Excel（每個機房最近一次對照）裡指到這台的那一格：機房、櫃、U、上傳日期。"""
    try:
        runs = conn.execute("SELECT r.room, r.created_at, r.rows_json FROM rack_excel_run r JOIN "
                            "(SELECT room, MAX(id) AS mid FROM rack_excel_run GROUP BY room) x ON x.mid = r.id").fetchall()
    except Exception:  # noqa: BLE001 - 還沒匯過任何機櫃圖 Excel（表不存在）：當沒有這個證據
        return {}
    out = {}
    for run in runs:
        try:
            rows = json.loads(run["rows_json"] or "[]")
        except ValueError:
            continue          # 壞掉的那次對照：這個機房當沒有 Excel 證據
        for r in rows:
            if not r.get("key") or r.get("status") in ("BLOCKED", "POSONLY", "MISSING"):
                continue
            out.setdefault(r["key"], {"room": run["room"], "rack": r.get("to_rack") or r.get("rack"), "u_start": r.get("u_start"),
                                      "u_end": r.get("u_end"), "label": r.get("label"), "date": _d(run["created_at"])})
    return out


def classify(info: dict, rules: dict, in_rack: bool) -> tuple[str, str]:
    """三態＋理由。"""
    rows = info["rows"]
    model = _first(rows, "device_model")
    hw = _first(rows, "hw_serial") or _first(rows, "hardware_no")
    text = " ".join([model, _first(rows, "os"), _s(info.get("hostname"))])
    if any(p.search(text) for p in rules["_virt"]):
        return "virtual", "型號／作業系統／主機名像虛擬或容器"
    if in_rack:
        return "physical", "機櫃圖 Excel 有這台"
    # 2.061.10：作業系統大類是網路設備／儲存設備的不會是虛擬機（221 實測 86 台卡在「無法判定」）
    dev_cls = _s(info.get("os"))
    if dev_cls and dev_cls in rules.get("physical_os_classes", []):
        return "physical", f"設備類型是{dev_cls}（不會是虛擬機）"
    if hw and hw not in ("0", "無", "NA", "N/A") and any(p.search(model) for p in rules["_srv"]):
        return "physical", "有硬體序號，型號像伺服器"
    return "unknown", ("沒有硬體序號" if not hw else "型號認不出是伺服器") + "，機櫃圖也沒有"


def _apid_backtest(conn, fleet: dict, sheet: dict, rules: dict) -> dict:
    """彙整表「資訊資產名稱」＝業務系統名稱 → APID 這條建議的準確率（拿 CIA 已登記、也在彙整表的機器回測）。"""
    names = {}
    for r in conn.execute("SELECT api_id, name FROM business_system WHERE name IS NOT NULL"):
        names.setdefault(_s(r["name"]).lower(), set()).add(_s(r["api_id"]))
    n = hit = 0
    for k, sh in sheet.items():
        info = fleet.get(k)
        if not info or not info.get("apis"):
            continue
        cand = names.get(_s(sh.get("asset_name")).lower())
        if not cand or len(cand) != 1:
            continue
        n += 1
        hit += next(iter(cand)) in info["apis"]
    prec = (hit / n) if n else None
    ok = bool(n >= int(rules.get("backtest_min_samples", 20)) and prec is not None and prec >= float(rules.get("apid_precision_min", 0.95)))
    return {"samples": n, "hits": hit, "precision": prec, "ok": ok, "names": names}


def _evidence(sh: dict | None, sh_date, rk: dict | None, last_seen, gap: int) -> tuple[list[dict], str]:
    ev = []
    if sh:
        ev.append({"source": "彙整表", "date": sh_date, "what": "表有、CIA 無" if "no_cia" in (sh.get("kinds") or []) else "表有這台"})
    if rk:
        ev.append({"source": "機櫃圖 Excel", "date": rk.get("date"), "what": f"{rk['room']} {rk['rack']}"})
    if _d(last_seen):
        ev.append({"source": "存活掃描／收集", "date": _d(last_seen), "what": "最後看到"})
    dates = sorted(datetime.strptime(e["date"], "%Y-%m-%d") for e in ev if e.get("date"))
    if len(ev) >= 2 and len(dates) >= 2 and (dates[-1] - dates[0]).days <= gap:
        level = "互相佐證"
    elif len(ev) >= 2:
        level = f"單一來源（日期相差超過 {gap} 天）" if len(dates) >= 2 else "單一來源（有證據沒有日期）"
    elif ev:
        level = "單一來源"
    else:
        level = "沒有證據"
    return ev, level


def _same_place(a_rack: str | None, b_rack: str | None) -> bool:
    """機櫃圖的櫃跟彙整表「機櫃位置」是不是同一櫃：兩邊都正規化後完全相等（A1 ≠ A10，審查 P3）。彙整表沒寫櫃＝沒有衝突。"""
    import rack_layout

    b = rack_layout.parse_rack(_s(b_rack)) if _s(b_rack) else None
    if not b or not a_rack:
        return True
    bn = b["rack"] if b["kind"] == "rack" else rack_layout._norm_rack(_s(b_rack))
    return rack_layout._norm_rack(a_rack) == bn


def plan(conn) -> dict:
    """母體與三態（不寫庫）。回 {physical:[…], unknown:[…], virtual:[…], excluded:{vm, vmware}, backtest}。"""
    import cia_reg

    rules = load_rules()
    gap = int(rules.get("evidence_max_gap_days", 30))
    fleet = cia_reg._fleet(conn)
    sheet, sh_date = _sheet_rows(conn)
    racks = _rack_rows(conn)
    bt = _apid_backtest(conn, fleet, sheet, rules)
    sysname = {r["api_id"]: r["name"] for r in conn.execute("SELECT api_id, name FROM business_system")}
    out = {"physical": [], "unknown": [], "virtual": [], "excluded": {"vm": 0, "vmware": 0}}
    for k, info in sorted(fleet.items(), key=lambda kv: (_s(kv[1].get("hostname")).lower(), kv[0])):
        if not info["in_nocia"]:
            continue
        if info["vmware"]:
            out["excluded"]["vmware"] += 1       # 天條：VMware 設備不當補登候選
            continue
        if info["vm"]:
            out["excluded"]["vm"] += 1           # 虛擬機走 9.4 VM 補登
            continue
        sh, rk = sheet.get(k), racks.get(k)
        state, why = classify(info, rules, rk is not None)
        rows = info["rows"]
        ev, level = _evidence(sh, sh_date, rk, info.get("last_seen"), gap)
        sh_loc = f"{_s((sh or {}).get('location'))} {_s((sh or {}).get('rack'))}".strip()
        rk_loc = f"{rk['room']} {rk['rack']}" + (f"・{rk['u_start']}U" if rk and rk.get("u_start") else "") if rk else ""
        conflict = bool(rk and sh and not _same_place(rk["rack"], (sh or {}).get("rack")))
        apid, apid_why = None, "無法判定"
        if sh:
            cand = bt["names"].get(_s(sh.get("asset_name")).lower())
            if cand and len(cand) == 1:
                apid = next(iter(cand))
                apid_why = "彙整表資訊資產名稱＝業務系統名稱" + ("" if bt["ok"] else "（回測沒過門檻，只當參考）")
        fill = {
            "room": None if conflict else (rk["room"] if rk else None),
            "rack": None if conflict else (rk["rack"] if rk else None),
            "vendor": _first(rows, "device_model").split(" ")[0] if _first(rows, "device_model") else _s((sh or {}).get("vendor")) or None,
            "model": _first(rows, "device_model") or None,
            "serial": _first(rows, "hw_serial") or _s((sh or {}).get("serial")) or None,
            "api_id": apid if (apid and bt["ok"]) else None,
        }
        out[state].append({
            "machine_key": k, "serial": rows[0].get("asset_serial") if rows else None, "hostname": info.get("hostname"),
            "ip": info.get("ip"), "os": info.get("os"), "state": state,
            "state_label": STATE_LABEL[state], "state_why": why, "evidence": ev, "evidence_level": level,
            "rack_loc": rk_loc, "sheet_loc": sh_loc, "conflict": conflict, "fill": fill,
            "apid_suggest": apid, "apid_name": sysname.get(apid or ""), "apid_why": apid_why, "last_seen": info.get("last_seen"),
            "status_label": info.get("status_label"),
        })
    bt_out = {k: v for k, v in bt.items() if k != "names"}
    return {**out, "backtest": bt_out, "sheet_date": sh_date}


def scan(conn, by: str) -> dict:
    """實體的寫進案件表（一台一案；指紋＝類別＋machine_key＋register）。無法判定的不進案件、另列。"""
    p = plan(conn)
    cands = [{"machine_key": c["machine_key"], "field": "register", "old_value": "", "title": c["hostname"] or c["ip"] or c["machine_key"],
              "room": c["fill"]["room"],
              "match_basis": {"machine_key": c["machine_key"], "hostname": c["hostname"], "ip": c["ip"]},
              "detail": c} for c in p["physical"]]
    res = cia_case.sync(conn, KIND, cands, by)
    return {**res, "unknown": len(p["unknown"]), "virtual": len(p["virtual"]), **{f"excluded_{k}": v for k, v in p["excluded"].items()}}


def overview(conn, p: dict | None = None) -> dict:
    """畫面上方：三態各幾台（＝清單列數，同一份計算）＋排除＋回測。p＝已算好的 plan（同一個請求不重算，審查 P3-9）。"""
    p = p or plan(conn)
    return {"physical": len(p["physical"]), "unknown": len(p["unknown"]), "virtual": len(p["virtual"]), "excluded": p["excluded"],
            "backtest": p["backtest"], "sheet_date": p["sheet_date"]}


def listing(conn, state: str, p: dict | None = None) -> list[dict]:
    if state not in STATE_LABEL:
        raise ValueError(f"不認得的分類：{state}")
    return (p or plan(conn))[state]
