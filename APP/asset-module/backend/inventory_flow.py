"""7 盤點作業（2.x 第二批，2026-10-28）：四種盤點共用一條流程「盤點任務 → 盤點結果 → 差異處理 → 盤點報告」。

  rack     機櫃盤點：沿用 rack_inventory（任務＝凍結今天的機櫃帳，現場離線填、上傳，跟基準比出差異）。
  account  帳號盤點 ┐ 這三種原本沒有「任務」：系統收集是持續的（每台主機每輪收集都更新 host_account／
  software 軟體盤點 ├ host_package／host_service）。這裡的任務＝把「當下已收集的結果」凍結成一份快照
  service  服務盤點 ┘ （日期、誰做的），差異＝跟上一次同類任務的快照比。

為什麼這樣設計（報告裡有完整說明）：
  * 盤點要回答「某一天的狀態是什麼、跟上一次比變了什麼、變的有沒有人看過」——收集表本身只有「現在」，
    舊值會被覆蓋或只標 gone_at，事後要說「三月那次盤點時 root 有沒有 sudo」說不出來。快照把那一刻留下來。
  * 只比「兩次都有收到的主機」：這一次沒收到的主機不能把它的每個帳號都當成「被刪除」——那是收集問題，
    列成一筆「主機未收集」差異就好；新出現的主機同理，一筆「新收集主機」。
  * 差異每筆有狀態（待處理／已處理／不處理＋原因）、留人與時間，跟機櫃盤點同一套。
  * 快照只存需要比對的欄位（帳號：種類、UID、管理員權限、能否登入、密碼狀態；軟體：版本；服務：程式、對外），
    一列一筆，不存 JSON 大物件以外的東西；軟體一次約 20 萬列。
"""
from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime
from typing import Any

KINDS = {"rack": "機櫃盤點", "account": "帳號盤點", "software": "軟體盤點", "service": "服務盤點"}
SNAP_KINDS = ("account", "software", "service")
DIFF_STATUS = ("待處理", "已處理", "不處理")
DIFF_LABEL = {"added": "新增", "removed": "移除", "changed": "變更", "host_new": "新收集主機", "host_missing": "主機未收集"}
#: 每種快照：比對時看哪些欄位（變了＝「變更」）、畫面欄名
FIELDS = {
    "account": [("kind", "種類"), ("uid", "UID"), ("is_sudoer", "管理員權限"), ("can_login", "可登入"), ("pw_status", "密碼狀態")],
    "software": [("version", "版本"), ("vendor", "廠商")],
    "service": [("process", "程式"), ("exposure", "對外"), ("service_guess", "服務")],
}
ITEM_LABEL = {"account": "帳號", "software": "軟體", "service": "服務埠"}


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS inv_task (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT,"
                 " created_by TEXT, created_at TEXT NOT NULL, hosts INTEGER, rows INTEGER, prev_id INTEGER, diff_count INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS inv_task_row (task_id INTEGER NOT NULL, host TEXT NOT NULL, asset_serial TEXT,"
                 " item TEXT NOT NULL, data TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inv_task_row ON inv_task_row(task_id, host)")
    conn.execute("CREATE TABLE IF NOT EXISTS inv_task_diff_state (task_id INTEGER NOT NULL, diff_key TEXT NOT NULL, status TEXT NOT NULL,"
                 " reason TEXT, updated_by TEXT, updated_at TEXT, PRIMARY KEY (task_id, diff_key))")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _current(conn: sqlite3.Connection, kind: str) -> list[tuple[str, str | None, str, dict]]:
    """當下已收集的結果（gone_at IS NULL）：(主機 IP, 資產序號, 項目, 比對欄位)。"""
    out = []
    try:
        if kind == "account":
            cols = {r[1] for r in conn.execute("PRAGMA table_info(host_account)")}
            kexpr = "COALESCE(kind_override, kind)" if "kind_override" in cols else "kind"
            for r in conn.execute(f"SELECT ip, asset_serial, username, uid, {kexpr} AS kind, is_sudoer, can_login, pw_status "
                                  "FROM host_account WHERE gone_at IS NULL"):
                out.append((r["ip"], r["asset_serial"], r["username"],
                            {"kind": r["kind"], "uid": r["uid"], "is_sudoer": "是" if r["is_sudoer"] else "否",
                             "can_login": "是" if r["can_login"] else "否", "pw_status": r["pw_status"]}))
        elif kind == "software":
            for r in conn.execute("SELECT ip, asset_serial, name, arch, version, vendor FROM host_package WHERE gone_at IS NULL"):
                out.append((r["ip"], r["asset_serial"], f"{r['name']}（{r['arch']}）" if r["arch"] else r["name"],
                            {"version": r["version"], "vendor": r["vendor"]}))
        elif kind == "service":
            for r in conn.execute("SELECT ip, asset_serial, proto, port, bind_addr, exposure, process, service_guess "
                                  "FROM host_service WHERE gone_at IS NULL"):
                out.append((r["ip"], r["asset_serial"], f"{r['proto']}/{r['port']}" + (f"@{r['bind_addr']}" if r["bind_addr"] else ""),
                            {"process": r["process"], "exposure": r["exposure"], "service_guess": r["service_guess"]}))
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
    return out


def _names(conn) -> dict[str, str]:
    import manage_state as ms
    import v2_views

    out = {}
    for r in conn.execute("SELECT * FROM hardware"):
        for ip in ms.ips_of(r["ip"]):
            out.setdefault(ip, v2_views.name_of(r))
    return out


# ===== 任務 =====

def create_task(conn: sqlite3.Connection, kind: str, by: str, title: str | None = None) -> dict:
    """凍結當下已收集的結果。上一份同類任務＝比對對象。"""
    if kind not in SNAP_KINDS:
        raise ValueError(f"不認得的盤點：{kind}")
    ensure_tables(conn)
    rows = _current(conn, kind)
    if not rows:
        raise ValueError(f"目前沒有任何已收集的{ITEM_LABEL[kind]}資料，無法建立盤點任務（請先收集）")
    prev = conn.execute("SELECT id FROM inv_task WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
    now = _now()
    hosts = len({r[0] for r in rows})
    cur = conn.execute("INSERT INTO inv_task (kind, title, created_by, created_at, hosts, rows, prev_id) VALUES (?,?,?,?,?,?,?)",
                       (kind, (title or "").strip() or f"{KINDS[kind]} {now[:10]}", by, now, hosts, len(rows),
                        prev["id"] if prev else None))
    tid = cur.lastrowid
    conn.executemany("INSERT INTO inv_task_row (task_id, host, asset_serial, item, data) VALUES (?,?,?,?,?)",
                     [(tid, h, s, it, json.dumps(d, ensure_ascii=False)) for h, s, it, d in rows])
    n = len(diffs(conn, kind, tid)["rows"])          # 差異數在建立時算一次存起來（清單頁不必每次重比 20 萬列）
    conn.execute("UPDATE inv_task SET diff_count = ? WHERE id = ?", (n, tid))
    conn.commit()
    return {"id": tid, "kind": kind, "hosts": hosts, "rows": len(rows), "prev_id": prev["id"] if prev else None, "diff_count": n}


def _task(conn, kind: str, tid: int) -> sqlite3.Row:
    ensure_tables(conn)
    t = conn.execute("SELECT * FROM inv_task WHERE id = ? AND kind = ?", (tid, kind)).fetchone()
    if not t:
        raise LookupError("查無這次盤點")
    return t


def _rows(conn, tid: int) -> dict[tuple[str, str], dict]:
    return {(r["host"], r["item"]): {"serial": r["asset_serial"], **json.loads(r["data"] or "{}")}
            for r in conn.execute("SELECT host, asset_serial, item, data FROM inv_task_row WHERE task_id = ?", (tid,))}


def list_tasks(conn: sqlite3.Connection, kind: str) -> list[dict]:
    if kind == "rack":
        import rack_inventory
        return [{"id": a["id"], "title": a["title"], "created_at": a["baseline_at"], "created_by": a["created_by"],
                 "status": "進行中" if a["status"] == "open" else "已結案", "scope": f"{a['rack_done']}／{a['rack_total']} 櫃已盤",
                 "size": a["book_count"], "diff_count": a["diff_count"], "diff_open": a["diff_open"]}
                for a in rack_inventory.list_audits(conn)]
    ensure_tables(conn)
    out = []
    for t in conn.execute("SELECT * FROM inv_task WHERE kind = ? ORDER BY id DESC", (kind,)):
        n = t["diff_count"] or 0
        done = conn.execute("SELECT COUNT(*) FROM inv_task_diff_state WHERE task_id = ? AND status != '待處理'", (t["id"],)).fetchone()[0]
        out.append({"id": t["id"], "title": t["title"], "created_at": t["created_at"], "created_by": t["created_by"],
                    "status": "已完成", "scope": f"{t['hosts']} 台主機", "size": t["rows"],
                    "diff_count": n, "diff_open": n - done, "prev_id": t["prev_id"]})
    return out


# ===== 盤點結果 =====

PRIV = "*priv"      # result(host=PRIV)：帳號盤點的特權帳號清單（跨主機）


def _is_priv(d: dict) -> bool:
    return d.get("is_sudoer") == "是" or str(d.get("uid")) == "0"

def result(conn: sqlite3.Connection, kind: str, tid: int, host: str | None = None) -> dict:
    """每台主機一列（項目數）；給 host 就列那台的每一個項目。"""
    if kind == "rack":
        import rack_inventory
        if host:
            # 點一櫃（2.007.13 使用者：「盤點出來我看不到裡面的台數和狀況」）：那一櫃帳上的每一箱＋現場結果（沒交過＝未盤）
            room, _, rack = host.rpartition(":")
            sh = rack_inventory.rack_sheet(conn, tid, room, rack)
            rows = [{"item": r["u"] or "（無 U 位）", "name": r["name_text"], "model": r["model"] or "", "serial": r["asset_serial"] or "",
                     "hw_serial": r["hw_serial"], "result": r["result"] if sh["done"] else "未盤",
                     "fix": "、".join(x for x in (r["correct_u"] and f"U {r['correct_u']}", r["correct_model"] and f"型號 {r['correct_model']}",
                                                  r["correct_serial"] and f"序號 {r['correct_serial']}") if x),
                     "note": r["note"]} for r in sh["rows"]]
            rows += [{"item": e.get("u") or "（現場多出）", "name": e.get("name") or "", "model": e.get("model") or "", "serial": "",
                      "hw_serial": e.get("serial") or "", "result": "帳外（現場多出）", "fix": "", "note": e.get("note") or ""} for e in sh["extras"]]
            return {"level": "item", "host": host, "name": f"{room} {rack}", "item_label": "U 位",
                    "fields": [{"k": k, "label": lab} for k, lab in (("name", "設備"), ("model", "型號"), ("serial", "資產編號"), ("hw_serial", "硬體序號"),
                                                                       ("result", "現場結果"), ("fix", "現場更正"), ("note", "備註"))],
                    "rows": rows, "total": len(rows), "done": sh["done"], "counted_by": sh["counted_by"], "counted_on": sh["counted_on"]}
        det = rack_inventory.audit_detail(conn, tid)
        rows = [{"key": f"{r['room']}:{r['rack']}", "host": f"{r['room']} {r['rack']}", "name": f"{r['room']} {r['rack']}",
                 "count": r["book_count"], "done": "已盤" if r["done"] else "未盤", "counted_by": r["counted_by"] or "",
                 "counted_on": r["counted_on"] or "", "diff_open": r["diff_open"]} for r in det["racks"]]
        return {"level": "unit", "unit_label": "機櫃", "count_label": "帳上台數", "rows": rows,
                "total": sum(r["count"] for r in rows), "task": {"id": tid, "title": det["title"], "created_at": det["baseline_at"]}}
    t = _task(conn, kind, tid)
    names = _names(conn)
    if host == PRIV and kind == "account":
        # 特權帳號（這次盤點凍結的資料）：有管理員權限或 UID 0，跨主機一份（每季特權帳號清單用）
        rows = [{"host": h, "name": names.get(h, h), "item": it, **{k: d.get(k) for k, _ in FIELDS[kind]}}
                for (h, it), d in sorted(_rows(conn, tid).items()) if _is_priv(d)]
        return {"level": "item", "multi_host": True, "host": "", "name": "特權帳號（管理員權限或 UID 0）", "item_label": ITEM_LABEL[kind],
                "fields": [{"k": k, "label": lab} for k, lab in FIELDS[kind]], "rows": rows, "total": len(rows)}
    if host:
        rows = [{"item": it, **{k: d.get(k) for k, _ in FIELDS[kind]}} for (h, it), d in sorted(_rows(conn, tid).items()) if h == host]
        return {"level": "item", "host": host, "name": names.get(host, host), "item_label": ITEM_LABEL[kind],
                "fields": [{"k": k, "label": lab} for k, lab in FIELDS[kind]], "rows": rows, "total": len(rows)}
    agg: dict[str, dict] = {}
    for r in conn.execute("SELECT host, MAX(asset_serial) s, COUNT(*) n FROM inv_task_row WHERE task_id = ? GROUP BY host", (tid,)):
        agg[r["host"]] = {"key": r["host"], "host": r["host"], "name": names.get(r["host"], r["host"]), "serial": r["s"], "count": r["n"]}
    rows = sorted(agg.values(), key=lambda x: (x["name"] or "").lower())
    src = {"software": "host_package", "account": "host_account", "service": "host_service"}.get(kind)
    if src:
        # 每台的資料是什麼時候收到的（2.007.13 使用者：「沒有時間標記，不知道看的是什麼時候的資料」）
        try:
            seen = {r[0]: r[1] for r in conn.execute(f"SELECT ip, MAX(last_seen) FROM {src} GROUP BY ip")}
        except sqlite3.OperationalError:   # 還沒收集過（表不存在）：不顯示收集時間，不是錯
            seen = {}
        for r in rows:
            r["collected_at"] = (seen.get(r["host"]) or "")[:16] or None
    return {"level": "unit", "unit_label": "主機", "count_label": f"{ITEM_LABEL[kind]}數", "rows": rows,
            "total": sum(r["count"] for r in rows), "task": {"id": tid, "title": t["title"], "created_at": t["created_at"]}}


# ===== 差異處理 =====

def diffs(conn: sqlite3.Connection, kind: str, tid: int) -> dict:
    if kind == "rack":
        import rack_inventory
        rows = [{"diff_key": d["diff_key"], "host": f"{d['room']} {d['rack']}", "name": d["name"], "kind": d["label"],
                 "before": d["book"], "after": d["found"], "next_step": d["next_step"], "action": d["action"],
                 "status": d["status"], "reason": d["reason"], "status_by": d["status_by"], "status_at": d["status_at"],
                 "key": d["key"]} for d in rack_inventory.diffs(conn, tid)]
        return {"rows": rows, "prev": None}
    t = _task(conn, kind, tid)
    states = {r["diff_key"]: dict(r) for r in conn.execute("SELECT * FROM inv_task_diff_state WHERE task_id = ?", (tid,))}
    if not t["prev_id"]:
        return {"rows": [], "prev": None, "note": "這是第一次盤點，沒有上一次可比較；下一次盤點起會列出差異。"}
    cur, old = _rows(conn, tid), _rows(conn, t["prev_id"])
    names = _names(conn)
    hosts_now, hosts_old = {h for h, _ in cur}, {h for h, _ in old}
    out = []

    def add(host, item, kind_, before, after):
        dk = "::".join((host, item or '', kind_))   # 差異識別（不是同一台的鍵）
        st = states.get(dk) or {}
        out.append({"diff_key": dk, "host": host, "name": names.get(host, host), "item": item or "", "kind": DIFF_LABEL[kind_],
                    "before": before, "after": after, "next_step": NEXT[kind_], "action": None,
                    "status": st.get("status") or "待處理", "reason": st.get("reason"), "status_by": st.get("updated_by"),
                    "status_at": st.get("updated_at")})
    for h in sorted(hosts_old - hosts_now):
        add(h, None, "host_missing", f"上次 {sum(1 for x, _ in old if x == h)} 筆", "這次沒有收集到")
    for h in sorted(hosts_now - hosts_old):
        add(h, None, "host_new", "上次沒有", f"這次 {sum(1 for x, _ in cur if x == h)} 筆")
    both = hosts_now & hosts_old
    fields = [k for k, _ in FIELDS[kind]]
    for (h, it), d in sorted(cur.items()):
        if h not in both:
            continue
        o = old.get((h, it))
        if o is None:
            add(h, it, "added", "—", _fmt(d, fields))
        elif any(o.get(k) != d.get(k) for k in fields):
            ch = [k for k in fields if o.get(k) != d.get(k)]
            add(h, it, "changed", _fmt(o, ch), _fmt(d, ch))
    for (h, it), o in sorted(old.items()):
        if h in both and (h, it) not in cur:
            add(h, it, "removed", _fmt(o, fields), "—")
    return {"rows": out, "prev": {"id": t["prev_id"]}}


NEXT = {"added": "確認是否為核准的新增", "removed": "確認是否為核准的移除", "changed": "確認變更是否核准",
        "host_new": "確認主機已納入盤點範圍", "host_missing": "確認主機是否下線或收集失敗"}


def _fmt(d: dict, keys: list[str]) -> str:
    lab = {k: v for f in FIELDS.values() for k, v in f}      # 畫面用欄位中文名，不出現程式欄位名
    return "；".join(f"{lab.get(k, k)}：{d.get(k)}" for k in keys if d.get(k) not in (None, ""))


def set_status(conn: sqlite3.Connection, kind: str, tid: int, diff_key: str, status: str, reason: str | None, by: str) -> dict:
    if kind == "rack":
        import rack_inventory
        return rack_inventory.set_diff_status(conn, tid, diff_key, status, reason, by)
    _task(conn, kind, tid)
    if status not in DIFF_STATUS:
        raise ValueError("狀態只能是 待處理／已處理／不處理")
    if status == "不處理" and not (reason or "").strip():
        raise ValueError("選「不處理」要寫原因")
    if not any(d["diff_key"] == diff_key for d in diffs(conn, kind, tid)["rows"]):
        raise LookupError("查無這筆差異")
    conn.execute("INSERT INTO inv_task_diff_state (task_id, diff_key, status, reason, updated_by, updated_at) VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(task_id, diff_key) DO UPDATE SET status=excluded.status, reason=excluded.reason, "
                 "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                 (tid, diff_key, status, (reason or "").strip() or None, by, _now()))
    conn.commit()
    return {"diff_key": diff_key, "status": status}


# ===== 盤點報告 =====

def report(conn: sqlite3.Connection, kind: str, tid: int) -> dict:
    if kind == "rack":
        import rack_inventory
        s = rack_inventory.summary(conn, tid)
        lines = [("盤點", f"#{tid} {s['title']}"), ("基準日", s["baseline_at"]), ("盤點人", "、".join(s["people"]) or "—"),
                 ("進度", f"{s['rack_done']}／{s['rack_total']} 櫃"), ("帳上台數", s["book_count"]), ("已盤台數", s["counted"]),
                 ("差異", s["diff_total"]), ("差異待處理", s["diff_open"]), ("序號待補", len(s["serial_missing"]))]
        by_kind = s["by_kind"]
    else:
        t = _task(conn, kind, tid)
        d = diffs(conn, kind, tid)["rows"]
        by_kind: dict[str, dict] = {}
        for x in d:
            b = by_kind.setdefault(x["kind"], {"total": 0, "待處理": 0, "已處理": 0, "不處理": 0})
            b["total"] += 1
            b[x["status"]] += 1
        lines = [("盤點", f"#{tid} {t['title']}"), ("盤點時間", t["created_at"]), ("建立者", t["created_by"] or "—"),
                 ("主機數", t["hosts"]), (f"{ITEM_LABEL[kind]}數", t["rows"]),
                 ("比較對象", f"#{t['prev_id']}" if t["prev_id"] else "（第一次盤點）"),
                 ("差異", len(d)), ("差異待處理", sum(1 for x in d if x["status"] == "待處理"))]
        if kind == "account":
            lines.append(("特權帳號（管理員權限或 UID 0）", result(conn, kind, tid, host=PRIV)["total"]))
    return {"kind": kind, "kind_label": KINDS[kind], "lines": [{"k": k, "v": v} for k, v in lines], "by_kind": by_kind}


def report_xlsx(conn: sqlite3.Connection, kind: str, tid: int) -> tuple[io.BytesIO, str]:
    if kind == "rack":
        import rack_inventory
        return rack_inventory.report_xlsx(conn, tid)
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    r = report(conn, kind, tid)
    wb = Workbook()
    ws = wb.active
    ws.title = "摘要"
    for x in r["lines"]:
        ws.append([x["k"], x["v"]])
    ws.append([])
    ws.append(["差異種類", "總數", "待處理", "已處理", "不處理"])
    for k, v in r["by_kind"].items():
        ws.append([k, v["total"], v["待處理"], v["已處理"], v["不處理"]])
    hdr = PatternFill("solid", fgColor="00806A")

    def table(title, cols, data):
        w = wb.create_sheet(title)
        w.append(cols)
        for c in w[1]:
            c.fill, c.font = hdr, Font(color="FFFFFF", bold=True)
        for row in data:
            w.append(row)
        w.freeze_panes = "A2"
    res = result(conn, kind, tid)
    table("主機", ["項次", "主機", "IP", f"{ITEM_LABEL[kind]}數"], [[i, x["name"], x["host"], x["count"]] for i, x in enumerate(res["rows"], 1)])
    table("差異", ["項次", "主機", "IP", ITEM_LABEL[kind], "種類", "上次", "這次", "狀態", "原因", "處理人", "處理時間"],
          [[i, x["name"], x["host"], x["item"], x["kind"], x["before"], x["after"], x["status"], x["reason"] or "",
            x["status_by"] or "", x["status_at"] or ""] for i, x in enumerate(diffs(conn, kind, tid)["rows"], 1)])
    if kind == "account":
        pv = result(conn, kind, tid, host=PRIV)["rows"]
        table("特權帳號", ["項次", "主機", "IP", "帳號"] + [lab for _, lab in FIELDS[kind]],
              [[i, x["name"], x["host"], x["item"]] + [x.get(k) for k, _ in FIELDS[kind]] for i, x in enumerate(pv, 1)])
    buf = io.BytesIO()
    import export_safe
    export_safe.neutralize_workbook(wb)     # 防公式注入（審查 B1）
    wb.save(buf)
    buf.seek(0)
    return buf, f"{KINDS[kind]}報告_{tid}_{datetime.now().strftime('%Y%m%d')}.xlsx"
