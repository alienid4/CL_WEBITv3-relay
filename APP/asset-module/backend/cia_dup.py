"""S3 同機多筆合併（2.061.1）：同一台（machine_key）有 2 筆以上 CIA 登記 → 逐案、逐欄建議 → 人確認 → 交 9.3 修正包。

設計：docs/設計_CIA第三批_S1toS4_2026-11-11.md「S3」＋ GPT 第 7 點（逐欄建議，平手不建議）。

## 母體
vip_view.duplicate_candidate_groups（全站重複登記的唯一來源：machine_key、排除退役、排除人工標「不是重複」、判不出是哪一台的不成組）
∩ 只算 CIA 正本（資產序號不是 DYN-／VC-／AUTO-／ADOPT-）還剩 2 筆以上的。
排除：堆疊交換器／機箱元件（2.060.7 已歸「堆疊／機箱元件」，不是重複）。DYN 空殼不在此（已有「重複登記合併」一鍵併回）。
每組附 2.060.7 的分類（疑似真重複／一機多系統／VIP／用途分列），畫面可篩；一機多系統的也列，因為 CIA 是否要一台一筆由人判斷。

## 逐欄建議（GPT 第 7 點）
只看各筆不一樣的欄（「不同欄位」）。每欄依序：① 只有一個非空值 → 取它（其他筆空白）；② 有一個值出現次數最多 → 取它（多數筆）；
③ 欄位最齊的那筆是唯一的 → 取它那筆的值；④ 其他＝平手，不建議，留給人選。
保留哪一筆：欄位最齊；一樣齊取資產序號最早（只是建議，人可改）。

## 交出（WEBIT3 不刪任何一筆）
確認後交 9.3 修正包「同機多筆」：保留那筆要改的格（原值→人選的值，規則 dup_field）＋其他每一筆一列「請 CIA 停用或併入保留筆」
（欄位 _dup_merge，規則 dup_merge）。CIA 匯入後：保留筆的格＝新值 → 已套用；其他筆從 CIA 消失或變退役 → 已套用。
"""
from __future__ import annotations

import cia_case
import cia_fix

KIND = "dup"
MERGE_FIELD = "_dup_merge"
#: 不拿來比「不同欄位」的欄（身分本身、系統自己的欄）
SKIP_FIELDS = {"asset_serial", "id", "created_at", "updated_at", "imported_at", "source", "vm_uuid"}


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _fields() -> dict[str, str]:
    return {f: lab for f, lab in cia_fix.field_labels().items() if f not in SKIP_FIELDS}


def _filled(r: dict, fields) -> int:
    return sum(1 for f in fields if _s(r.get(f)))


def analyze_group(rows: list[dict], labels: dict[str, str]) -> dict:
    """一組（同一台的多筆 CIA 登記）→ 不同欄位、逐欄建議、建議保留哪一筆。"""
    rows = sorted(rows, key=lambda r: _s(r.get("asset_serial")))
    fields = [f for f in labels if any(f in r for r in rows)]
    filled = {r["asset_serial"]: _filled(r, fields) for r in rows}
    top = max(filled.values())
    best = [s for s, n in filled.items() if n == top]
    keep = best[0]
    keep_why = "欄位最齊" if len(best) == 1 else "欄位一樣齊，取資產序號最早"
    diffs = []
    for f in fields:
        vals = {r["asset_serial"]: _s(r.get(f)) for r in rows}
        if len(set(vals.values())) <= 1:
            continue
        nonblank = {s: v for s, v in vals.items() if v}
        distinct = sorted(set(nonblank.values()))
        pick, why = None, "平手：請人選"
        if len(distinct) == 1:
            pick, why = next(s for s, v in nonblank.items() if v == distinct[0]), "只有這筆有值"
        else:
            cnt = {v: sum(1 for x in nonblank.values() if x == v) for v in distinct}
            hi = max(cnt.values())
            tops = [v for v, n in cnt.items() if n == hi]
            if len(tops) == 1:
                pick, why = next(s for s, v in nonblank.items() if v == tops[0]), "多數筆是這個值"
            elif len(best) == 1 and nonblank.get(best[0]):
                pick, why = best[0], "欄位最齊那筆的值"
        diffs.append({"field": f, "label": labels.get(f, f), "values": vals, "suggest": pick,
                      "suggest_value": vals.get(pick) if pick else None, "why": why})
    return {"serials": [r["asset_serial"] for r in rows], "keep": keep, "keep_why": keep_why, "filled": filled, "diffs": diffs,
            "rows": [{"asset_serial": r["asset_serial"], "hostname": r.get("hostname"), "ip": r.get("ip"), "api_id": r.get("api_id"),
                      "asset_name": r.get("asset_name"), "asset_status": r.get("asset_status")} for r in rows]}


def candidates(conn) -> list[dict]:
    """這次比對找到的同機多筆（還沒寫庫）。"""
    import machine_identity
    import vip_view

    labels = _fields()
    mode = machine_identity.mode()       # 當時用哪一種「同一台」口徑（GPT 第 2 點：之後鍵變了看得出來）
    out = []
    for k, rows in vip_view.duplicate_candidate_groups(conn):
        cia_rows = [dict(r) for r in rows if not cia_fix._non_cia(r.get("asset_serial"))]
        if len(cia_rows) < 2:
            continue
        cls = vip_view.classify_duplicate_group(rows)
        if cls["kind"] == vip_view.DUP_STACK:
            continue
        a = analyze_group(cia_rows, labels)
        out.append({
            "machine_key": k, "field": "*", "old_value": ",".join(a["serials"]),
            "title": next((r["hostname"] for r in cia_rows if _s(r.get("hostname"))), k), "category": cls["kind"],
            "match_basis": {"machine_key": k, "mode": mode, "hostnames": sorted({_s(r.get("hostname")) for r in cia_rows} - {""}),
                            "ips": sorted({_s(r.get("ip")) for r in cia_rows} - {""})},
            "detail": {**a, "dup_kind": cls["kind"], "dup_kind_label": cls["kind_label"]},
        })
    return out


def scan(conn, by: str) -> dict:
    return cia_case.sync(conn, KIND, candidates(conn), by)


def _hw(conn, serials: list[str]) -> dict[str, dict]:
    ph = ",".join("?" * len(serials))
    return {r["asset_serial"]: dict(r) for r in conn.execute(f"SELECT * FROM hardware WHERE asset_serial IN ({ph})", serials)} if serials else {}


def _raw(v) -> str:
    return "" if v is None else str(v)


def confirm(conn, case_id: int, keep: str, fields: dict[str, str] | None, note: str | None, by: str,
            seen_last: str | None = None) -> dict:
    """人逐案確認：保留哪一筆＋每個不同欄位取哪一筆的值（值＝那筆的資產序號）。每個不同欄位都要選（平手的不能跳過）。
    確認當下把「人看到、選定的值」存進 decision（審查 P2）：保留筆每欄的原值、選的那筆的值、各筆序號。
    CIA 現況已經跟畫面上的比對結果不一樣（確認前有人匯入過 CIA）就擋下，請先重新比對。"""
    cs = cia_case.list_cases(conn, KIND, ids=[case_id])
    if not cs:
        raise LookupError("找不到這一案")
    # 畫面載入後又重新比對過（審查 P3）：人看到的不是最新的比對結果 → 擋下，請重新整理。前端沒帶就照舊（相容舊頁面）
    if seen_last and cs[0].get("last_seen") and seen_last != cs[0]["last_seen"]:
        raise ValueError("這一案在你開啟畫面後重新比對過，請重新整理後再確認")
    d = cs[0]["detail"] or {}
    serials = d.get("serials") or []
    if keep not in serials:
        raise ValueError("保留的那筆不在這一組裡")
    fields = {k: v for k, v in (fields or {}).items() if v}
    diffs = d.get("diffs") or []
    diff_fields = [x["field"] for x in diffs]
    miss = [x["label"] for x in diffs if x["field"] not in fields]
    if miss:
        raise ValueError(f"還有欄位沒選要取哪一筆：{'、'.join(miss[:5])}")
    bad = [f for f, s in fields.items() if f not in diff_fields or s not in serials]
    if bad:
        raise ValueError(f"選擇不對：{'、'.join(bad[:5])}")
    cur = _hw(conn, serials)
    shown = {x["field"]: x["values"] for x in diffs}
    if len(cur) != len(serials) or any(_s(cur[s].get(f)) != (shown[f].get(s) or "") for f in diff_fields for s in serials):
        raise ValueError("CIA 登記跟畫面上的比對結果不一樣了（可能剛匯入過 CIA），請先按「重新比對」")
    dec = {"keep": keep, "serials": serials, "fields": fields,
           "values": {f: _raw(cur[s].get(f)) for f, s in fields.items()},          # 人選定的新值
           "keep_values": {f: _raw(cur[keep].get(f)) for f in fields}}             # 保留筆確認當下的原值
    return cia_case.act(conn, [case_id], "confirm", by, note, dec, kind=KIND)


def fix_rows(conn, case: dict) -> tuple[list[dict] | None, str | None]:
    """一案確認的內容 → 修正包的列。只用 decision 裡人確認的值（審查 P2），不讀來源筆的「現值」當新值；
    CIA 現況跟確認時不一樣（保留筆原值變了、來源筆的值變了、有筆不見了）→ (None, 原因)，這一案不交、請重新確認。"""
    dec = case["decision"] or {}
    keep = dec.get("keep")
    serials = dec.get("serials") or []
    if not keep or not serials or "values" not in dec:
        return None, "確認內容不完整（舊版確認），請撤回後重新確認"
    cur = _hw(conn, serials)
    if len(cur) != len(serials):
        return None, "這一組有登記不見了，請重新比對"
    k = cur[keep]
    for f, src in (dec.get("fields") or {}).items():
        if _raw(k.get(f)) != dec["keep_values"].get(f) or _raw(cur[src].get(f)) != dec["values"].get(f):
            return None, "CIA 登記跟確認時不一樣了，請撤回後重新確認"
    labels = cia_fix.field_labels()
    out = []
    base = {"hostname": k.get("hostname"), "unit": cia_fix._unit_of(k), "asset_group": _s(k.get("group_name")) or "（未填群組）"}
    for f, src in (dec.get("fields") or {}).items():
        new, old = dec["values"][f], dec["keep_values"][f]
        if _s(new) != _s(old):
            out.append({**base, "asset_serial": keep, "field": f, "old_value": old, "new_value": new, "rule_id": "dup_field",
                        "reason": f"同機多筆合併：{labels.get(f, f)} 取 {src} 的值"})
    for s in serials:
        if s != keep:
            r = cur[s]
            out.append({"hostname": r.get("hostname"), "unit": base["unit"], "asset_group": base["asset_group"],
                        "asset_serial": s, "field": MERGE_FIELD, "old_value": s, "new_value": f"停用或併入 {keep}",
                        "rule_id": "dup_merge", "reason": f"同機多筆：跟 {keep} 是同一台，請停用這筆或併入 {keep}"})
    return out, None


def handoff(conn, by: str) -> dict:
    """把所有已確認的同機多筆交出成一個 9.3 修正包批次。登記已經變了的案件不交（回 skipped，請重新比對）。"""
    items, skipped = [], []
    for c in cia_case.list_cases(conn, KIND, "confirmed"):
        rows, why = fix_rows(conn, c)
        if rows is None:
            skipped.append({"id": c["id"], "why": why})
            continue
        items.append((c["id"], rows))
    if not items:
        return {"batch_id": None, "items": 0, "cases": 0, "skipped": skipped}
    r = cia_case.hand_to_fix(conn, KIND, items, "第三批：同機多筆", by)
    r["skipped"] = skipped + r["skipped"]
    return r
