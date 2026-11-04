"""EOS 批三（2.041.83）：維護合約（contract_row）與官方 EOS 的「影子比對」與差異報告。

## 這個模組是什麼、不是什麼
只**比較、不更動**：把目前生效的合約匯入（contract_import.is_current=1）每一列，和資產的硬體型號、官方 EOS 表放在一起看，
列出哪裡對不起來，讓人去查。它：

* 不改 EOS 表、不改任何可信度（已確認／推定／未知）、不改風險等級、不降風險、不改通知與紅燈行為；
* 不寄任何通知；不寫任何資料表（唯讀，合約表不存在＝尚無合約資料）；
* 缺資料不補：合約沒有型號／到期日、資產沒有型號，都只是「無法比對」，不猜。

## 差異類型（每列恰好一類，依下列順序判定；一列合約對到多台時每台各一列）
1. unlinked          合約列沒有對到任何資產（未對到／對到多台無法判定）。
2. model_mismatch    合約型號與資產型號正規化後的標準名稱不同（以序號或主機名對到時才比；以型號對到的本來就同型號）。
3. contract_after_eos 合約到期日晚於官方 EOS 日期（月精度的 EOS 以該月最後一天為準，只在確定晚於時才標）。
                     意義：合約還在、官方已停止支援，可能是延伸支援，也可能是 EOS 表或合約日期有誤，需要人查。
4. eos_only_in_contract 合約型號查得到官方 EOS 日期，但資產的型號查不到（沒填、或 EOS 表查無）。
資產與合約都查得到、日期也不衝突的列＝一致，不列入差異。

說明一律只是「提示人去看」，不構成任何判定。
"""
from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date

import eos

TYPES = ("unlinked", "model_mismatch", "contract_after_eos", "eos_only_in_contract")
TYPE_LABEL = {
    "unlinked": "合約列未對到資產",
    "model_mismatch": "合約型號與資產型號不一致",
    "contract_after_eos": "合約到期日晚於官方 EOS 日期",
    "eos_only_in_contract": "合約型號有 EOS 日期、資產型號查不到",
}
TYPE_HINT = {
    "unlinked": "這一列合約沒有對到資產（未對到，或同時對到多台無法判定），無法與 EOS 比較。",
    "model_mismatch": "兩邊的型號不是同一個標準名稱，請確認是合約或資產其中一邊登記有誤。",
    "contract_after_eos": "合約還在、官方已停止支援：可能是延伸支援，也可能是 EOS 表或合約日期有誤，請人工查證。",
    "eos_only_in_contract": "以合約上的型號可以查到官方 EOS 日期，但資產登記的型號沒有，請確認資產型號是否漏填或登記不同。",
}
EFFECT_NOTE = "影子比對只做比較與記錄：不會改任何 EOS 日期、可信度或風險，也不會寄任何通知。"


def _tbl(conn: sqlite3.Connection, name: str) -> bool:
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
    except sqlite3.Error:
        return False


def _current_rows(conn: sqlite3.Connection) -> tuple[dict | None, list[dict]]:
    if not (_tbl(conn, "contract_import") and _tbl(conn, "contract_row")):
        return None, []
    imp = conn.execute("SELECT * FROM contract_import WHERE is_current = 1 ORDER BY id DESC LIMIT 1").fetchone()
    if not imp:
        return None, []
    rows = [dict(r) for r in conn.execute("SELECT * FROM contract_row WHERE import_id = ? ORDER BY row_no, id", (imp["id"],))]
    return dict(imp), rows


def _parse_day(text) -> date | None:
    t = (str(text or "")).strip()[:10]
    try:
        return date.fromisoformat(t)
    except ValueError:
        return None


def _eos_latest(text) -> date | None:
    """官方 EOS 日期的「最晚可能日」：日精度＝該日；月精度（YYYY-MM）＝該月最後一天（只在確定晚於時才標差異）。"""
    t = (str(text or "")).strip()
    prec = eos.date_precision(t)
    if prec == "day":
        return date.fromisoformat(t)
    if prec == "month":
        y, m = int(t[:4]), int(t[5:7])
        return date(y, m, calendar.monthrange(y, m)[1])
    return None


def _serials(row: dict) -> list[str]:
    try:
        v = json.loads(row.get("matched_serials") or "[]")
    except (ValueError, TypeError):
        # 匯入時寫入的 JSON 壞掉：這一列當作「沒對到任何資產」，會以 unlinked 差異列出來（不是靜默消失）
        return []
    return [str(s) for s in v] if isinstance(v, list) else []


def _hw_lookup(conn, model: str | None, hint: str | None = None) -> tuple[str | None, str | None, dict | None]:
    """(標準名稱, 硬體家族, EOS 表項目)。沒有型號＝(None, None, None)；虛擬機型號不查硬體 EOS。"""
    import normalize

    if not (model or "").strip():
        return None, None, None
    info = normalize.normalize_model(model, conn, hint)
    fam, _v = normalize.hardware_family(info["vendor"], info["canonical"])
    hit = None if fam == "虛擬化" else eos.lookup_hardware_eos(info["canonical"])
    return info["canonical"], fam, hit


def shadow_rows(conn: sqlite3.Connection) -> dict:
    """{"import": 目前生效匯入 | None, "rows": [...差異列...], "compared": 比對的（合約列×資產）組數, "contract_rows": 合約列數}。唯讀。"""
    import eos_notify as en

    imp, crows = _current_rows(conn)
    if imp is None:
        return {"import": None, "rows": [], "compared": 0, "contract_rows": 0}
    by_serial: dict[str, dict] = {}
    for m in en.resolve_machines(conn):
        for g in m["group"]:
            by_serial[g["asset_serial"]] = m
    out: list[dict] = []
    compared = 0
    memo: dict[tuple, tuple] = {}

    def contract_side(model, vendor=None):
        key = ((vendor or "").strip().lower(), model)           # 同型號文字、不同廠商不共用結果
        if key not in memo:
            memo[key] = _hw_lookup(conn, model, (vendor or "").strip() or None)
        return memo[key]

    for c in crows:
        base = {"row_no": c.get("row_no"), "contract_no": c.get("contract_no"), "vendor": c.get("vendor"),
                "contract_model": c.get("model"), "contract_end": c.get("end_date"), "match_by": c.get("match_by"),
                "match_status": c.get("match_status")}
        c_canon, _cfam, c_hit = contract_side(c.get("model"), c.get("vendor"))
        if c.get("match_status") != "matched":
            out.append({**base, "type": "unlinked", "mk": None, "hostname": None, "ip": None, "environment": None,
                        "asset_model": None, "asset_canonical": None, "contract_canonical": c_canon,
                        "eos_name": (c_hit or {}).get("name"), "eos_date": (c_hit or {}).get("eos_date"), "trust_label": None,
                        "detail": f"比對狀態：{ {'unmatched': '未對到', 'ambiguous': '對到多台無法判定'}.get(c.get('match_status'), c.get('match_status') or '—') }"})
            continue
        seen: set[str] = set()
        before = compared
        for s in _serials(c):
            m = by_serial.get(s)
            if m is None or m["mk"] in seen:
                continue
            seen.add(m["mk"])
            compared += 1
            rep = m["rep"]
            hw = m["hw"]
            a_canon = hw["canonical"] if hw else None
            a_hit = hw["hit"] if hw else None
            row = {**base, "mk": m["mk"], "hostname": rep["hostname"], "ip": rep["ip"], "environment": rep["environment"],
                   "asset_model": hw["raw"] if hw else None, "asset_canonical": a_canon, "contract_canonical": c_canon,
                   "eos_name": (a_hit or c_hit or {}).get("name"), "eos_date": (a_hit or c_hit or {}).get("eos_date"),
                   "trust_label": eos.TRUST_LABEL[eos.trust_level(a_hit or c_hit)] if (a_hit or c_hit) else None}
            if (c.get("match_by") or "") != "model" and c_canon and a_canon and c_canon != a_canon:
                out.append({**row, "type": "model_mismatch", "detail": f"合約「{c_canon}」≠ 資產「{a_canon}」"})
                continue
            end = _parse_day(c.get("end_date"))
            latest = _eos_latest((a_hit or {}).get("eos_date")) if a_hit else None
            if end is not None and latest is not None and end > latest:
                out.append({**row, "type": "contract_after_eos",
                            "detail": f"合約到期 {end.isoformat()}，官方 EOS {(a_hit or {}).get('eos_date')}"})
                continue
            if c_hit and eos.date_precision(c_hit.get("eos_date")) != "none" and not (a_hit and a_hit.get("eos_date")):
                out.append({**row, "type": "eos_only_in_contract", "eos_name": c_hit.get("name"), "eos_date": c_hit.get("eos_date"),
                            "trust_label": eos.TRUST_LABEL[eos.trust_level(c_hit)],
                            "detail": "資產型號" + ("未填" if not a_canon else f"「{a_canon}」在 EOS 表查無日期")})
        if compared == before:           # 標為「已對到」卻找不到任何現役資產（資料壞掉、資產已退役或刪除）：列為未對到，不靜默消失
            out.append({**base, "type": "unlinked", "mk": None, "hostname": None, "ip": None, "environment": None,
                        "asset_model": None, "asset_canonical": None, "contract_canonical": c_canon,
                        "eos_name": (c_hit or {}).get("name"), "eos_date": (c_hit or {}).get("eos_date"), "trust_label": None,
                        "detail": "合約標示已對到，但找不到對應的現役資產"})
    order = {t: i for i, t in enumerate(TYPES)}
    out.sort(key=lambda r: (order[r["type"]], r["row_no"] if r["row_no"] is not None else 10 ** 9, r["hostname"] or "", r["ip"] or ""))
    for i, r in enumerate(out, 1):
        r["no"] = i
        r["type_label"] = TYPE_LABEL[r["type"]]
    return {"import": imp, "rows": out, "compared": compared, "contract_rows": len(crows)}


def shadow_overview(conn: sqlite3.Connection, flag: str | None = None) -> dict:
    """tiles 與 rows 同一份計算（加總＝下鑽）；flag 指定類型時 rows 只回該類。"""
    res = shadow_rows(conn)
    rows = res["rows"]
    tiles = [{"key": "all", "label": "全部差異", "count": len(rows)}] + [
        {"key": t, "label": TYPE_LABEL[t], "count": sum(1 for r in rows if r["type"] == t)} for t in TYPES]
    imp = res["import"]
    return {"has_import": imp is not None,
            "import": ({"id": imp["id"], "filename": imp.get("filename"), "imported_at": imp.get("imported_at"),
                        "row_count": imp.get("row_count")} if imp else None),
            "contract_rows": res["contract_rows"], "compared": res["compared"], "tiles": tiles,
            "rows": [r for r in rows if flag in (None, "", "all") or r["type"] == flag], "flag": flag or "all",
            "effect_note": EFFECT_NOTE, "hints": TYPE_HINT}
