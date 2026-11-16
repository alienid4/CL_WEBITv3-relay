"""2.1 主機查詢的兩個唯讀功能（2.041.84；舊版缺口 230 的第 2、3 項「看」的部分）。

* batch：貼多筆（主機名、IP、資產編號、APID）一次查。**不寫庫、不連目標機、不留查詢紀錄。**
  比對規則只有一份：oncall.resolve（值班卡、2.x 病歷表、首頁搜尋共用）；本檔不寫新規則。
  - 預設「精確」：呼叫 resolve(exact=True)，不退到部分字串比對。
  - 使用者勾選「含部分比對」才用 resolve 原本的部分比對，結果一律標 status=partial／matched_by=部分比對，
    不會被算成「找到」。
  - 整批共用一次建好的群組索引（resolve 的 groups 參數），不是每行重建全庫。
  - 查不到的每一筆都有一列（status=none），不會安靜少一列；重複輸入合併成一列並記次數。
  - 不支援直接輸入機器代號（machine_key，如 ret:…、sn:…）：resolve 沒有這個分支，該輸入回 none 並附說明。
* duplicates：「重複登記」唯讀清單。分組函式只有一份：_app_shared._duplicate_groups
  （內部呼叫 vip_view.duplicate_candidate_groups＝machine_key 全站同一把尺）；本檔不自寫去重。
  只顯示，不提供釋放（刪除）；標「不是重複」也不在這一版。
"""
from __future__ import annotations

import sqlite3

MAX_RAW_LINES = 2000        # 原始行數上限（含空白行與重複行）
MAX_TERM_LEN = 128          # 每一項字元數上限
MAX_TOTAL_CHARS = 60000     # 全部輸入字元總量上限
MAX_UNIQUE = 500            # 去空白去重後的項數上限

STATUS_LABEL = {"one": "找到", "many": "多筆", "partial": "部分命中", "none": "查無"}
MODE_LABEL = {"exact": "精確比對", "partial": "含部分比對"}
_CAND_SHOWN = 20


def _check_limits(terms) -> None:
    if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
        raise ValueError("terms 必須是文字清單")
    if len(terms) > MAX_RAW_LINES:
        raise ValueError(f"一次最多 {MAX_RAW_LINES} 行（含空白與重複行），目前 {len(terms)} 行")
    total = 0
    for i, t in enumerate(terms, 1):
        if len(t) > MAX_TERM_LEN:
            raise ValueError(f"第 {i} 行超過 {MAX_TERM_LEN} 個字元")
        total += len(t)
    if total > MAX_TOTAL_CHARS:
        raise ValueError(f"輸入總量超過 {MAX_TOTAL_CHARS} 個字元")


def batch(conn: sqlite3.Connection, terms: list[str], partial: bool = False) -> dict:
    """批次查詢。回 {mode, rows, summary, limits, notes}；rows 每個去重後的輸入一列，順序＝首次出現順序。"""
    import oncall
    import v2_hosts

    _check_limits(terms)
    seen: dict[str, dict] = {}
    order: list[str] = []
    blank = 0
    for t in terms:
        s = t.strip()
        if not s:
            blank += 1
            continue
        k = s.lower()
        if k in seen:
            seen[k]["input_count"] += 1
        else:
            seen[k] = {"term": s, "input_count": 1}
            order.append(k)
    if not order:
        raise ValueError("請貼上至少一筆主機名、IP、資產編號或 APID")
    if len(order) > MAX_UNIQUE:
        raise ValueError(f"去除重複後最多 {MAX_UNIQUE} 筆，目前 {len(order)} 筆")

    groups, _ = oncall._groups(conn)                       # 整批共用同一份群組索引
    by_key = {m["key"]: m for m in v2_hosts.machines(conn, include_retired=True)}
    rows: list[dict] = []
    for n, k in enumerate(order, 1):
        e = seen[k]
        term = e["term"]
        how, keys = oncall.resolve(conn, term, groups, exact=not partial)
        hint = None
        if not keys and term in groups:
            hint = "不支援以機器代號查詢，請改用主機名、IP、資產編號或 APID"
        status = "none" if not keys else ("partial" if how == "部分比對" else ("one" if len(keys) == 1 else "many"))
        cands = []
        for ck in keys[:_CAND_SHOWN]:
            m = by_key.get(ck)
            g = groups.get(ck)
            cands.append({"key": ck, "name": (m or {}).get("name") or (g[0]["hostname"] if g else ck),
                          "ip": (m or {}).get("ip") or (g[0]["ip"] if g else None), "retired": ck.startswith("ret:")})
        row = {"no": n, "term": term, "input_count": e["input_count"], "status": status, "status_label": STATUS_LABEL[status],
               "matched_by": how, "partial": status == "partial", "count": len(keys), "hint": hint,
               "key": None, "name": None, "ip": None, "type": None, "system": None, "where": None,
               "detect": None, "records": None, "retired": None, "candidates": cands,
               "candidates_text": ""}
        if status == "one":
            m = by_key.get(keys[0]) or {}
            row.update({"key": keys[0], "name": m.get("name") or cands[0]["name"], "ip": m.get("ip") or cands[0]["ip"],
                        "type": m.get("type"), "system": m.get("system"), "where": m.get("where"),
                        "detect": m.get("status_label"), "records": m.get("records"), "retired": keys[0].startswith("ret:")})
        elif keys:
            more = f"…共 {len(keys)} 台" if len(keys) > len(cands) else ""
            row["candidates_text"] = "、".join(f"{c['name']}（{c['ip'] or '無 IP'}）" for c in cands) + more
        rows.append(row)

    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUS_LABEL}
    return {
        "mode": "partial" if partial else "exact", "mode_label": MODE_LABEL["partial" if partial else "exact"],
        "rows": rows,
        "summary": {"raw_lines": len(terms), "blank": blank, "unique": len(rows),
                    "duplicates_merged": len(terms) - blank - len(rows), **counts},
        "limits": {"max_raw_lines": MAX_RAW_LINES, "max_term_len": MAX_TERM_LEN,
                   "max_total_chars": MAX_TOTAL_CHARS, "max_unique": MAX_UNIQUE},
        "notes": ["比對順序：資產編號、IP、主機名（不分大小寫、FQDN 與短名視為同名）、APID；與值班卡、首頁搜尋同一套規則。",
                  "不支援以機器代號（machine_key）直接查詢。",
                  "同一個輸入重複貼上只列一列，並記錄輸入次數。"],
    }


def duplicates(conn: sqlite3.Connection) -> dict:
    """重複登記唯讀清單。分組＝_app_shared._duplicate_groups（machine_key 一把尺）；這裡只整理成畫面用的欄位。"""
    import _app_shared as shared
    import vip_view

    groups = shared._duplicate_groups(conn)
    out = []
    members = []
    for i, g in enumerate(groups, 1):
        mem = g["members"]
        hows: dict[str, int] = {}
        for m in mem:
            h = (m.get("origin") or {}).get("how") or "—"
            hows[h] = hows.get(h, 0) + 1
        out.append({
            "no": i, "key": g["key"], "hostname": g["hostname"], "ip": g["ip"], "count": g["count"],
            "extra_rows": g["extra_rows"], "kind": g["kind"], "kind_label": g["kind_label"],
            "identical": g["identical"], "diff_fields": g["diff_fields"],
            "serials": [m["asset_serial"] for m in mem], "systems": g["systems"],
            "origin_text": "、".join(f"{h} {n} 筆" for h, n in hows.items()),
        })
        for m in mem:
            o = m.get("origin") or {}
            fi = o.get("from_import")
            members.append({
                "group_no": i, "group_key": g["key"], "kind": g["kind"], "kind_label": g["kind_label"],
                "serial": m["asset_serial"], "hostname": m.get("hostname"), "ip": m.get("ip"), "api_id": m.get("api_id"),
                "how": o.get("how"), "created_at": o.get("created_at"), "updated_at": o.get("updated_at"),
                # None＝查不到（沒有來源紀錄表）≠ 否：兩者不混
                "from_import": "未取得" if fi is None else ("是" if fi else "否"),
                "strong_ids": "、".join(o.get("strong_ids") or []) or "無",
                "diff": {f: ("" if m.get(f) is None else str(m.get(f)).strip()) for f in g["diff_fields"]},
            })
    try:
        dismissed = conn.execute("SELECT COUNT(*) FROM duplicate_dismiss").fetchone()[0]
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        dismissed = None
    return {
        "groups": out, "members": members,
        "by_kind": {k: sum(1 for g in out if g["kind"] == k) for k in vip_view.DUP_KIND_ORDER},
        "kind_labels": vip_view.DUP_KIND_LABEL, "kind_order": list(vip_view.DUP_KIND_ORDER),
        "extra_rows": sum(g["extra_rows"] for g in out),
        "dismissed_groups": dismissed,
    }
