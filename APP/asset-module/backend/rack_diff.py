"""機櫃差異待辦（2.043.17，需求表 R4–R8；規格 docs/ui-v2/機櫃差異待辦_規格_2026-11-04.md 第二階段與附錄 A）。D3／D4。

現場回報一格「不在這格／現場沒有／位置不對／其實是系統某台」→ 一筆待辦（不直接改正式資料）→ SP（admin）結案。
結案動作才會寫資料，而且全部走既有、有稽核的寫入口，跟待辦本身同一個交易：
  · 實體移除／關機：rack_layout.set_state_override（removed／off）。off＝「關機未下架」：保留 U、用電 0、不算台數，
    跟現有機櫃狀態標記同一個口徑（所有下鑽都走同一份 machines()／rack_power，不另算）
  · 搬到別櫃／綁定既有資產：rack_layout.set_position（位置更正，進 CIA 待更正、可清除）
  · 退役：先轉「待資產負責人核准退役」，admin 代為登錄核准人與依據後才寫「CIA 待異動：報廢」（lifecycle.set_pending＋
    cia_pending.record）＋已不在櫃上；不直接改 CIA 的資產狀態（只有 CIA 匯入能改）
  · 補登 CIA／退回 Excel：只改待辦狀態，清單可匯出給 CIA 窗口／Excel 維護者
每一步寫 rack_diff_log（前後值、操作者、時間）；結案可「還原」回到結案前——但結案之後別人又改過那台（位置／機櫃狀態／
待異動跟結案當下不同）就拒絕還原，不蓋掉別人的修改。

狀態（六種）：pending 待確認／matched 已匹配／cia 待補登 CIA／excel 待退回 Excel／retire_wait 待資產負責人核准退役／closed 已結案。
重匯 Excel（R7）：Excel 每次匯入是不可改的版本快照（rack_excel_run 的 rows_json 寫入後不再改）；處理過的 Excel 格以
（機房、實體櫃鍵、U 起訖、正規化文字）完全相同才延續（下一版仍寫著＝「Excel 尚未更新」，不再開新待辦）；
同一段 U 但文字不同＝「待確認」，不沿用、不猜。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

STATUS = {"pending": "待確認", "matched": "已匹配", "cia": "待補登 CIA", "excel": "待退回 Excel",
          "retire_wait": "待資產負責人核准退役", "closed": "已結案"}
OPEN = ("pending", "cia", "excel", "retire_wait")          # 還沒處理完（同一台／同一格不准再開一筆）
REASONS_SYS = {"moved": "搬到別的位置了", "removed": "這台不在這格（整台拿走了）", "off": "還在，但關機或拔電",
               "dup": "看起來重複登記", "unsure": "不確定，請管理員看"}
REASONS_XL = {"absent": "這格其實是空的", "wrongpos": "位置寫錯了", "bind": "這格就是系統裡的某一台"}
ACTIONS = {
    "confirm_removed": "確認：實體移除（仍是資產）", "confirm_off": "確認：關機未下架（保留 U）", "move": "位置更正（搬到別櫃／別 U）",
    "bind": "綁定既有資產（畫到這格）", "to_cia": "轉：待補登 CIA", "to_excel": "轉：退回 Excel 修正", "retire": "轉：待資產負責人核准退役",
    "approve_retire": "資產負責人核准退役（登錄核准人與依據）", "done": "外部已處理，結案", "reject": "駁回（現場沒問題）", "undo": "還原",
}
# 狀態轉換表：(目前狀態, 動作) → 新狀態。不在表上的一律拒絕（R5 非法轉換被拒絕）
TRANSITIONS = {
    ("pending", "confirm_removed"): "closed", ("pending", "confirm_off"): "closed", ("pending", "move"): "closed",
    ("pending", "bind"): "matched", ("pending", "to_cia"): "cia", ("pending", "to_excel"): "excel",
    ("pending", "retire"): "retire_wait", ("pending", "reject"): "closed",
    ("cia", "done"): "closed", ("cia", "reject"): "closed",
    ("excel", "done"): "closed",
    ("retire_wait", "approve_retire"): "closed", ("retire_wait", "reject"): "closed",
}
SYS_ONLY = {"confirm_removed", "confirm_off", "move", "retire", "approve_retire"}
XL_ONLY = {"bind", "to_cia"}
BATCHABLE = {"confirm_removed", "confirm_off", "to_cia", "to_excel", "retire", "reject", "done"}   # 不需要逐筆填資料的
NEED_NOTE = {"reject"}

SQL = ["""CREATE TABLE IF NOT EXISTS rack_diff_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL, rack TEXT NOT NULL, rack_stored TEXT,
    u_start INTEGER, u_end INTEGER, subject TEXT NOT NULL, sys_key TEXT, sys_name TEXT,
    xl_run_id INTEGER, xl_row_id INTEGER, xl_label TEXT, xl_match TEXT,
    reason TEXT NOT NULL, note TEXT, serial TEXT,
    target_room TEXT, target_rack TEXT, target_u_start INTEGER, target_u_end INTEGER, bind_key TEXT,
    status TEXT NOT NULL DEFAULT 'pending', close_action TEXT, approver TEXT, basis TEXT, undo_json TEXT,
    created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT)""",
       """CREATE TABLE IF NOT EXISTS rack_diff_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL, at TEXT NOT NULL, by TEXT, action TEXT NOT NULL,
    status_before TEXT, status_after TEXT, before_json TEXT, after_json TEXT, note TEXT)"""]


def ensure(conn: sqlite3.Connection) -> None:
    for q in SQL:
        conn.execute(q)


def _ready(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rack_diff_item'").fetchone() is not None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def norm_text(t: str | None) -> str:
    """Excel 格文字的比對鍵：去空白、轉大寫、全形斜線統一（只拿來判斷「完全相同」，不做模糊比對）。"""
    return re.sub(r"\s+", "", (t or "").replace("／", "/")).upper()


def xl_match_key(room: str, rack_stored: str, u_start, u_end, label: str | None) -> str:
    """跨版本延續用的穩定鍵：機房＋實體櫃鍵（系統櫃名，改對照不會變）＋U 起訖＋正規化文字。"""
    return f"{room}|{rack_stored}|{u_start}|{u_end}|{norm_text(label)}"


def _row(r) -> dict:
    d = dict(r)
    d["status_label"] = STATUS.get(d["status"], d["status"])
    d["reason_label"] = (REASONS_SYS if d["subject"] == "sys" else REASONS_XL).get(d["reason"], d["reason"])
    d["subject_label"] = "系統設備" if d["subject"] == "sys" else "Excel 格"
    d["open"] = d["status"] in OPEN
    d["who"] = d["sys_name"] if d["subject"] == "sys" else (d["xl_label"] or "").replace("\n", " ")
    d["u_text"] = "" if not d["u_start"] else (f"{d['u_start']}U" if d["u_start"] == d["u_end"] else f"{d['u_start']}-{d['u_end']}U")
    d["close_label"] = ACTIONS.get(d["close_action"] or "", "")
    return d


def get(conn, item_id: int) -> dict:
    if not _ready(conn):
        raise LookupError("查無這筆待辦")
    r = conn.execute("SELECT * FROM rack_diff_item WHERE id = ?", (item_id,)).fetchone()
    if r is None:
        raise LookupError("查無這筆待辦")
    d = _row(r)
    d["log"] = [dict(x) for x in conn.execute("SELECT * FROM rack_diff_log WHERE item_id = ? ORDER BY id", (item_id,))]
    d["actions"] = allowed_actions(d)
    return d


def list_items(conn, status: str | None = None, room: str | None = None) -> list[dict]:
    """待辦清單（讀取不建表）。status 給了只列那一種；項次照 id 固定（D 前綴＋id）。"""
    if not _ready(conn):
        return []
    q, args = "SELECT * FROM rack_diff_item WHERE 1=1", []
    if status:
        q += " AND status = ?"
        args.append(status)
    if room:
        q += " AND room = ?"
        args.append(room)
    out = [_row(r) for r in conn.execute(q + " ORDER BY id", args)]
    for d in out:
        d["actions"] = allowed_actions(d)
    return out


def counts(conn, room: str | None = None) -> dict[str, int]:
    """各狀態筆數＝list_items(status=…) 的列數（同一份查詢；verify_numbers 釘住）。"""
    c = {k: 0 for k in STATUS}
    for it in list_items(conn, room=room):
        c[it["status"]] = c.get(it["status"], 0) + 1      # 不認得的狀態也數進來（verify 會列出）
    return c


def allowed_actions(item: dict) -> list[str]:
    out = []
    for (st, act), _ in TRANSITIONS.items():
        if st != item["status"]:
            continue
        if act in SYS_ONLY and item["subject"] != "sys":
            continue
        if act in XL_ONLY and item["subject"] != "xl":
            continue
        out.append(act)
    if item["status"] != "pending" and item.get("undo_json"):
        out.append("undo")
    return out


# ------------------------------------------------------------------ 回報（R4：一般帳號以上；viewer 由路由擋 403）
def report(conn, body: dict, by: str) -> dict:
    """送一筆回報。不改任何正式資料。同一台／同一個 Excel 格（含前一版 Excel 的同一格）已有未處理的待辦就拒絕（指到那一筆）。"""
    import rack_layout

    subject = body.get("subject")
    reason = (body.get("reason") or "").strip()
    room = (body.get("room") or "").strip()
    rack = rack_layout._norm_rack((body.get("rack") or "").strip())
    if subject not in ("sys", "xl"):
        raise ValueError("要指定是系統設備還是 Excel 格")
    reasons = REASONS_SYS if subject == "sys" else REASONS_XL
    if reason not in reasons:
        raise ValueError("原因要選一個：" + "／".join(reasons.values()))
    if not room or not rack:
        raise ValueError("要有機房與機櫃")
    names = rack_layout.rack_name_map(conn)
    rack = rack_layout.display_rack(names, room, rack)
    stored = rack_layout.stored_rack(names, room, rack)
    note = (body.get("note") or "").strip() or None
    serial = (body.get("serial") or "").strip() or None
    if reason == "unsure" and not note:
        raise ValueError("選「不確定」要寫備註（看到什麼）")
    ensure(conn)
    rec: dict[str, Any] = {"room": room, "rack": rack, "rack_stored": stored,
                           "subject": subject, "reason": reason, "note": note, "serial": serial}
    ph = ",".join("?" * len(OPEN))
    if subject == "sys":
        key = (body.get("sys_key") or "").strip()
        m = next((x for x in rack_layout.machines_readonly_full(conn) if x["key"] == key), None)
        if m is None:
            raise ValueError("查無這台設備")
        if m["kind"] != "rack" or m["room"] != room or m.get("rack") != rack:
            raise ValueError("這台不在這一櫃（畫面可能過時，請重新整理）")
        dup = conn.execute(f"SELECT id FROM rack_diff_item WHERE sys_key = ? AND status IN ({ph})", (key, *OPEN)).fetchone()
        if dup:
            raise FileExistsError(f"這台已經有一筆還沒處理的待辦（D{dup[0]}）")
        rec.update(sys_key=key, sys_name=m["name"], u_start=m.get("u_start"), u_end=m.get("u_end"))
        if reason == "moved":
            rec.update(_target(rack_layout, names, body, room, need=False))
    else:
        try:
            run_id, row_id = int(body.get("xl_run_id")), int(body.get("xl_row_id"))
        except (TypeError, ValueError):
            raise ValueError("要指定是哪一次 Excel 對照的哪一格") from None
        import rack_excel_import
        try:
            run = rack_excel_import.get_run(conn, run_id)
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        if run["room"] != room:
            raise ValueError("這次 Excel 對照不是這個機房的")
        cell = next((x for x in run["rows"] if x["id"] == row_id), None)
        if cell is None:
            raise ValueError("這次 Excel 對照沒有這一格")
        # 這一格屬於哪一櫃以 Excel 那一列為準（GPT A3）：用實體櫃鍵比，不信任請求帶來的櫃名
        row_racks = [r for r in (cell.get("to_rack"), cell.get("rack")) if r]
        hit = next((r for r in row_racks if rack_layout.same_rack(names, room, r, rack)), None)
        if hit is None:
            raise ValueError(f"這一格在 Excel 寫的是 {row_racks[-1] if row_racks else '（沒有櫃名）'} 櫃，不是 {rack}（畫面可能過時，請重新整理）")
        rack = rack_layout.display_rack(names, room, hit)
        stored = rack_layout.stored_rack(names, room, rack)
        rec.update(rack=rack, rack_stored=stored)
        match = xl_match_key(room, stored, cell["u_start"], cell["u_end"], cell["label"])
        dup = conn.execute(f"SELECT id FROM rack_diff_item WHERE subject = 'xl' AND status IN ({ph}) AND "
                           "((xl_run_id = ? AND xl_row_id = ?) OR xl_match = ?)", (*OPEN, run_id, row_id, match)).fetchone()
        if dup:
            raise FileExistsError(f"這一格已經有一筆還沒處理的待辦（D{dup[0]}）")
        # 同一個差異已經處理完（已匹配／已結案）＝不再開（GPT A5）；Excel 文字變了才是新的差異（match 鍵不同）
        done = conn.execute("SELECT id, status FROM rack_diff_item WHERE subject = 'xl' AND status IN ('matched', 'closed') "
                            "AND xl_match = ? ORDER BY id DESC", (match,)).fetchone()
        if done:
            raise FileExistsError(f"這一格的同一個差異已處理過（D{done[0]}，{STATUS[done[1]]}）；Excel 寫的內容改了才算新的差異")
        rec.update(xl_run_id=run_id, xl_row_id=row_id, xl_label=cell["label"], u_start=cell["u_start"], u_end=cell["u_end"],
                   xl_match=match)
        if reason == "wrongpos":
            rec.update(_target(rack_layout, names, body, room, need=True))
        if reason == "bind":
            bk = (body.get("bind_key") or "").strip()
            q = (body.get("bind_q") or "").strip()
            if bk:
                m = next((x for x in rack_layout.machines_readonly_full(conn) if x["key"] == bk), None)
                if m is None:
                    raise ValueError("選的那一台查不到了（畫面可能過時，請重新搜尋）")
                # 選定的那台要符合畫面上的字（GPT 複核 P1）：舊的搜尋結果晚到、或選了之後又改字，不可以記成另一台
                if not q or not _matches(m, q):
                    raise ValueError(f"選的那一台（{m['name']}）跟「是哪一台」欄寫的「{q}」對不上，請重新選")
                rec["bind_key"] = bk
            elif not q:
                raise ValueError("要寫是系統哪一台（主機名、IP 或資產編號），或從清單選一台")
            else:
                rec["bind_key"] = _resolve_machine(conn, q)
    now = _now()
    cols = list(rec) + ["status", "created_by", "created_at", "updated_by", "updated_at"]
    vals = list(rec.values()) + ["pending", by, now, by, now]
    try:
        cur = conn.execute(f"INSERT INTO rack_diff_item ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        _log(conn, cur.lastrowid, by, "report", None, "pending", None, rec, note)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get(conn, cur.lastrowid)


def _target(rack_layout, names, body, room0: str, need: bool) -> dict:
    tr = (body.get("target_rack") or "").strip()
    if not tr:
        if need:
            raise ValueError("要填正確的位置（櫃、U）")
        return {}
    room = (body.get("target_room") or room0 or "").strip()
    try:
        u1 = int(body.get("target_u_start"))
        u2 = int(body.get("target_u_end") or u1)
    except (TypeError, ValueError):
        raise ValueError("新位置的 U 要是整數") from None
    if u1 < 1 or u2 < u1 or u2 > 60:
        raise ValueError("新位置的 U 不對")
    return {"target_room": room, "target_rack": rack_layout.display_rack(names, room, rack_layout._norm_rack(tr)),
            "target_u_start": u1, "target_u_end": u2}


def _resolve_machine(conn, q: str) -> str:
    """主機名／IP／資產編號 → 唯一一台的 machine key；對到好幾台或對不到就拒絕（不猜）。"""
    import rack_layout
    ql = q.strip().lower()
    hits = [m for m in rack_layout.machines_readonly_full(conn)      # 唯讀：回報路徑不可以寫到任何正式資料（GPT A1）
            if ql in {(m.get("name") or "").lower(), (m.get("ip") or "").lower(), (m.get("asset_serial") or "").lower()}
            or ql in [s.lower() for s in m.get("serials") or []]]
    if not hits:
        raise ValueError(f"系統裡找不到「{q}」")
    if len(hits) > 1:
        raise ValueError(f"「{q}」對到 {len(hits)} 台，請寫更完整的主機名或資產編號")
    return hits[0]["key"]


def _norm_q(t) -> str:
    return re.sub(r"[\s\-_]+", "", (t or "")).lower()


def _hay(m: dict) -> list:
    return [m.get("name"), m.get("ip"), m.get("asset_serial"), *(m.get("serials") or []), *(m.get("hw_serials") or [])]


def _matches(m: dict, q: str) -> bool:
    """這台的主機名／IP／資產編號／硬體序號有沒有包含 q（候選清單與送出時的檢查用同一把尺）。"""
    nq = _norm_q(q)
    return bool(nq) and any(nq in _norm_q(h) for h in _hay(m) if h)


def candidates(conn, room: str, rack: str | None, q: str, limit: int = 20) -> list[dict]:
    """回報時「是哪一台」的候選（GPT B4）：同機房的設備，主機名／IP／序號（硬體序號、資產編號）包含打的字（不分大小寫、
    忽略空白與 -）。同一櫃的排前面，其次同機房。唯讀（不寫庫）；q 空白＝列同一櫃的設備。"""
    import rack_layout
    names = rack_layout.rack_name_map(conn)
    norm = _norm_q
    nq = norm(q)
    out = []
    for m in rack_layout.machines_readonly_full(conn):
        if m.get("room") != room or m["kind"] in ("vm", "vm_model")                 or (m.get("model") or "").strip().upper() in ("(VM)", "VM", "（VM）"):      # 虛擬機不會在機櫃上
            continue
        here = bool(rack and m.get("rack") and rack_layout.same_rack(names, room, m["rack"], rack))
        hay = _hay(m)
        if nq and not _matches(m, q):
            continue
        if not nq and not here:
            continue
        out.append({"key": m["key"], "name": m["name"], "ip": m.get("ip"), "serial": ((m.get("hw_serials") or [None])[0]) or m.get("asset_serial"),
                    "rack": m.get("rack"), "u": rack_layout._u_text(m) if m.get("u_start") else "", "same_rack": here,
                    "exact": bool(nq) and any(norm(h) == nq for h in hay if h)})
    out.sort(key=lambda x: (not x["exact"], not x["same_rack"], (x["name"] or "").lower()))
    return out[:limit]


def _log(conn, item_id, by, action, st_before, st_after, before, after, note) -> None:
    conn.execute("INSERT INTO rack_diff_log (item_id, at, by, action, status_before, status_after, before_json, after_json, note) "
                 "VALUES (?,?,?,?,?,?,?,?,?)",
                 (item_id, _now(), by, action, st_before, st_after,
                  json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
                  json.dumps(after, ensure_ascii=False, default=str) if after is not None else None, note))


# ------------------------------------------------------------------ 結案（R6；admin）
def _snapshot(conn, key: str, serials: list[str] | None = None) -> dict:
    """這台的機櫃狀態標記、位置更正、（退役用）CIA 待異動欄位。還原與「還原前有沒有人改過」都比這份。"""
    import rack_layout
    rack_layout.ensure_state_table(conn)
    rack_layout.ensure_position_table(conn)
    st = conn.execute("SELECT state FROM rack_state_override WHERE key = ?", (key,)).fetchone()
    pos = conn.execute("SELECT room, rack, u_start, u_end FROM rack_position_override WHERE key = ? AND status = 'active'",
                       (key,)).fetchone()
    out = {"key": key, "state": st["state"] if st else None, "pos": dict(pos) if pos else None}
    if serials:
        out["pending"] = {s: (dict(r) if r else None) for s in serials
                          for r in [conn.execute("SELECT pending_status, pending_note, pending_by, pending_at FROM hardware "
                                                 "WHERE asset_serial = ?", (s,)).fetchone()]}
    return out


def _snapshot_ro(conn, key: str) -> dict:
    """同 _snapshot 的機櫃狀態＋位置，但不建表（verify_numbers 用）。"""
    def has(t):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None
    st = conn.execute("SELECT state FROM rack_state_override WHERE key = ?", (key,)).fetchone() if has("rack_state_override") else None
    pos = (conn.execute("SELECT room, rack, u_start, u_end FROM rack_position_override WHERE key = ? AND status = 'active'",
                        (key,)).fetchone() if has("rack_position_override") else None)
    return {"key": key, "state": st["state"] if st else None, "pos": dict(pos) if pos else None}


def _restore(conn, snap: dict, by: str, note: str) -> None:
    import cia_pending
    import rack_layout
    key = snap["key"]
    cur = _snapshot(conn, key, list((snap.get("pending") or {}).keys()))
    if cur["state"] != snap["state"]:
        rack_layout.set_state_override(conn, key, snap["state"], note, by, commit=False)
    if cur["pos"] != snap["pos"]:
        if snap["pos"]:
            p = snap["pos"]
            rack_layout.set_position(conn, key, p["room"], p["rack"], p["u_start"], p["u_end"] - p["u_start"] + 1, note, by, commit=False)
        elif cur["pos"]:
            rack_layout.clear_position(conn, key, note, by, commit=False)
    for s, p in (snap.get("pending") or {}).items():
        now_p = (cur.get("pending") or {}).get(s)
        if now_p == p:
            continue
        p = p or {}
        conn.execute("UPDATE hardware SET pending_status = ?, pending_note = ?, pending_by = ?, pending_at = ? WHERE asset_serial = ?",
                     (p.get("pending_status"), p.get("pending_note"), p.get("pending_by"), p.get("pending_at"), s))
        import system_report
        if not system_report.is_off_book(s):
            # 給 CIA 窗口的清單也要看得到「這筆報廢取消了」，不默默消失
            cia_pending.record(conn, s, "asset_status", (now_p or {}).get("pending_status"), p.get("pending_status") or "（取消待異動）",
                               note, by, commit=False)


def act(conn, item_id: int, action: str, by: str, note: str | None = None, extra: dict | None = None,
        commit: bool = True) -> dict:
    """對一筆待辦做一個動作。非法轉換拒絕；寫資料的動作先記前值（還原用），跟待辦狀態同一個交易。"""
    import rack_layout

    extra = extra or {}
    it = get(conn, item_id)
    if action not in it["actions"]:
        raise PermissionError(f"「{it['status_label']}」的待辦不能做「{ACTIONS.get(action, action)}」")
    note = (note or "").strip() or None
    if action == "undo":
        return _undo(conn, it, by, note)
    if action in NEED_NOTE and not note:
        raise ValueError("駁回要寫原因")
    new_status = TRANSITIONS[(it["status"], action)]
    tag = f"機櫃差異待辦 D{item_id}：{ACTIONS[action]}" + (f"（{note}）" if note else "")
    undo: dict[str, Any] = {"status_before": it["status"]}
    upd: dict[str, Any] = {}
    key = it["sys_key"]
    try:
        if action in ("confirm_removed", "confirm_off"):
            undo["snap"] = _snapshot(conn, key)
            rack_layout.set_state_override(conn, key, "removed" if action == "confirm_removed" else "off", tag, by, commit=False)
        elif action == "move":
            room = (extra.get("target_room") or it["target_room"] or it["room"] or "").strip()
            rk = (extra.get("target_rack") or it["target_rack"] or "").strip()
            u1 = extra.get("target_u_start") or it["target_u_start"]
            u2 = extra.get("target_u_end") or it["target_u_end"] or u1
            if not rk or not u1:
                raise ValueError("要填新位置（櫃、U）")
            undo["snap"] = _snapshot(conn, key)
            rack_layout.set_position(conn, key, room, rk, int(u1), int(u2) - int(u1) + 1, tag, by, commit=False)
            upd.update(target_room=room, target_rack=rk, target_u_start=int(u1), target_u_end=int(u2))
        elif action == "bind":
            key = it["bind_key"] or (_resolve_machine(conn, extra["bind_q"]) if extra.get("bind_q") else None)
            if not key:
                raise ValueError("要指定綁到系統哪一台")
            undo["snap"] = _snapshot(conn, key)
            rack_layout.set_position(conn, key, it["room"], it["rack"], it["u_start"], it["u_end"] - it["u_start"] + 1, tag, by,
                                     commit=False)
            upd["bind_key"] = key
        elif action == "approve_retire":
            approver = (extra.get("approver") or "").strip()
            basis = (extra.get("basis") or "").strip()
            if not approver or not basis:
                raise ValueError("退役要登錄資產負責人（核准人）與依據")
            m = next((x for x in rack_layout.machines(conn) if x["key"] == key), None)
            if m is None:
                raise ValueError("查無這台設備")
            undo["snap"] = _snapshot(conn, key, m["serials"])
            _mark_retire(conn, m["serials"], f"{tag}；核准人 {approver}；依據 {basis}", by)
            rack_layout.set_state_override(conn, key, "removed", tag, by, commit=False)
            upd.update(approver=approver, basis=basis)
        if undo.get("snap"):
            snap = undo["snap"]
            undo["after"] = _snapshot(conn, snap["key"], list((snap.get("pending") or {}).keys()))
        upd.update(status=new_status, close_action=action, updated_by=by, updated_at=_now(),
                   undo_json=json.dumps(undo, ensure_ascii=False, default=str))
        cur = conn.execute(f"UPDATE rack_diff_item SET {', '.join(k + ' = ?' for k in upd)} WHERE id = ? AND status = ?",
                           (*upd.values(), item_id, it["status"]))
        if cur.rowcount != 1:
            raise PermissionError("這筆待辦剛被別人處理過，請重新整理")
        _log(conn, item_id, by, action, it["status"], new_status, undo, {k: v for k, v in upd.items() if k != "undo_json"}, note)
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get(conn, item_id)


def _mark_retire(conn, serials: list[str], reason: str, by: str) -> None:
    """「CIA 待異動：報廢」——跟 cia_pending.batch_set_status 同一套寫法（不改 asset_status），但不 commit，跟待辦同一個交易。"""
    import cia_pending
    import lifecycle
    import system_report
    lifecycle.ensure(conn)
    for s in serials:
        row = conn.execute("SELECT asset_status, pending_status FROM hardware WHERE asset_serial = ?", (s,)).fetchone()
        if row is None:
            continue
        if lifecycle.set_pending(conn, s, "報廢", reason, by) and not system_report.is_off_book(s):
            cia_pending.record(conn, s, "asset_status", row[1] or row[0], "報廢", reason, by, commit=False)


def _undo(conn, it: dict, by: str, note: str | None) -> dict:
    undo = json.loads(it["undo_json"] or "{}")
    back = undo.get("status_before") or "pending"
    try:
        if undo.get("snap"):
            snap = undo["snap"]
            now = _snapshot(conn, snap["key"], list((snap.get("pending") or {}).keys()))
            if undo.get("after") is not None and now != undo["after"]:
                raise PermissionError("結案之後這台又被改過（位置、機櫃狀態或待異動），不能直接還原；請到那台確認後再處理")
            _restore(conn, snap, by, f"機櫃差異待辦 D{it['id']}：還原" + (f"（{note}）" if note else ""))
        cur = conn.execute("UPDATE rack_diff_item SET status = ?, close_action = NULL, approver = NULL, basis = NULL, undo_json = NULL, "
                           "updated_by = ?, updated_at = ? WHERE id = ? AND status = ?", (back, by, _now(), it["id"], it["status"]))
        if cur.rowcount != 1:
            raise PermissionError("這筆待辦剛被別人處理過，請重新整理")
        _log(conn, it["id"], by, "undo", it["status"], back, {"status": it["status"], "close_action": it["close_action"]}, undo, note)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get(conn, it["id"])


# ------------------------------------------------------------------ 批次（R8：先預覽，送出的筆要等於預覽的筆）
BATCH_TTL_MIN = 10
BATCH_SQL = ("CREATE TABLE IF NOT EXISTS rack_diff_batch (token TEXT PRIMARY KEY, action TEXT NOT NULL, ids_json TEXT NOT NULL,"
             " digest TEXT NOT NULL, note TEXT, created_by TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,"
             " used_at TEXT, used_by TEXT)")


def _digest(conn, action: str, ok_ids: list[int], note: str | None) -> str:
    """這一批的內容指紋：動作＋每一筆的 (id, 狀態, 最後一筆紀錄編號)＋備註。任何一筆被改過，指紋就不同。"""
    import hashlib
    # 每一筆的「最後一筆紀錄編號」：改過一次就變（updated_at 只到秒，同一秒內改了又改會看不出來）
    rows = [tuple(r) for r in conn.execute(
        f"SELECT i.id, i.status, (SELECT MAX(l.id) FROM rack_diff_log l WHERE l.item_id = i.id) FROM rack_diff_item i "
        f"WHERE i.id IN ({','.join('?' * len(ok_ids))}) ORDER BY i.id", ok_ids)] if ok_ids else []
    return hashlib.sha256(json.dumps([action, rows, note or ""], ensure_ascii=False).encode()).hexdigest()


def batch_preview(conn, ids: list[int], action: str, by: str | None = None, note: str | None = None) -> dict:
    """預覽（伺服器記下這一批）：回 token，送出只認 token（GPT A2：不信任客戶端給的 ids）。token 10 分鐘內有效、只能用一次、限同一人。"""
    pv = _preview_rows(conn, ids, action)
    note = (note or "").strip() or None
    if action in NEED_NOTE and not note and pv["count"]:
        raise ValueError("駁回要寫原因")
    import secrets
    from datetime import timedelta
    conn.execute(BATCH_SQL)
    tok = secrets.token_urlsafe(18)
    now = datetime.now()
    conn.execute("INSERT INTO rack_diff_batch (token, action, ids_json, digest, note, created_by, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                 (tok, action, json.dumps(pv["ok_ids"]), _digest(conn, action, pv["ok_ids"], note), note, by,
                  now.strftime("%Y-%m-%d %H:%M:%S"), (now + timedelta(minutes=BATCH_TTL_MIN)).strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    return {**pv, "token": tok, "note": note, "expires_min": BATCH_TTL_MIN}


def _preview_rows(conn, ids: list[int], action: str) -> dict:
    if action not in BATCHABLE:
        raise ValueError(f"「{ACTIONS.get(action, action)}」要逐筆填資料，不能批次")
    ok, skip = [], []
    for i in dict.fromkeys(int(x) for x in ids):
        try:
            it = get(conn, i)
        except LookupError:
            skip.append({"id": i, "why": "查無這筆"})
            continue
        if action in it["actions"]:
            ok.append({"id": i, "room": it["room"], "rack": it["rack"], "u_text": it["u_text"], "who": it["who"], "status": it["status_label"]})
        else:
            skip.append({"id": i, "who": it["who"], "why": f"「{it['status_label']}」不能做「{ACTIONS.get(action, action)}」"})
    return {"action": action, "action_label": ACTIONS[action], "ok": ok, "skip": skip, "count": len(ok),
            "ok_ids": [x["id"] for x in ok]}


class BatchStale(ValueError):
    """預覽過期／用過／不是同一人／資料變了：整批沒做，要重新預覽。"""


def batch_apply(conn, token: str, by: str, note: str | None = None) -> dict:
    """只收預覽給的 token：做的就是預覽時那一批（動作、哪幾筆、備註都從伺服器記的拿）。
    token 不存在、過期、用過、不是同一人、或那幾筆在預覽之後被改過（指紋不同）＝整批不做。一個交易，中途一筆失敗整批退回。
    note＝送出當下畫面上的備註：跟預覽時記下的不一樣＝拒絕（GPT 複核 P1：不可以畫面一套、稽核寫的是另一套）。"""
    conn.execute(BATCH_SQL)
    b = conn.execute("SELECT * FROM rack_diff_batch WHERE token = ?", (token or "",)).fetchone()
    if b is None:
        raise BatchStale("找不到這次預覽，請重新按「預覽」")
    if b["used_at"]:
        raise BatchStale("這次預覽已經送出過了，請重新按「預覽」")
    if b["expires_at"] < _now():
        raise BatchStale(f"預覽已超過 {BATCH_TTL_MIN} 分鐘，請重新按「預覽」")
    if (b["created_by"] or "") != (by or ""):
        raise BatchStale("這次預覽不是你做的，請自己按「預覽」")
    action, ok_ids, stored_note = b["action"], json.loads(b["ids_json"]), b["note"]
    if ((note or "").strip() or None) != stored_note:
        raise BatchStale("預覽之後備註改過了，整批沒做，請重新按「預覽」（送出的備註要跟預覽時一樣）")
    note = stored_note
    pv = _preview_rows(conn, ok_ids, action)
    if pv["ok_ids"] != ok_ids or _digest(conn, action, ok_ids, note) != b["digest"]:
        raise BatchStale("預覽之後有待辦被改過，整批沒做，請重新按「預覽」看最新的清單")
    done = []
    try:
        conn.execute("UPDATE rack_diff_batch SET used_at = ?, used_by = ? WHERE token = ? AND used_at IS NULL", (_now(), by, token))
        for i in ok_ids:
            act(conn, i, action, by, note, commit=False)       # 整批做完才 commit；任一筆失敗 act 會 rollback 整個交易
            done.append(i)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"done": done, "count": len(done)}


# ------------------------------------------------------------------ 給機櫃圖用（R7）
def rack_marks(conn, room: str, rack: str) -> dict:
    """{sys: {key: 摘要}, xl_open: {"run:row": 摘要}, xl_open_match: {match: 摘要}, xl_done: {match: 摘要}, xl_done_u: {"u1-u2": [摘要]}}。讀取不建表。"""
    out: dict[str, dict] = {"sys": {}, "xl_open": {}, "xl_open_match": {}, "xl_done": {}, "xl_done_u": {}}
    if not _ready(conn):
        return out
    import rack_layout
    names = rack_layout.rack_name_map(conn)
    stored = rack_layout.stored_rack(names, room, rack)
    for r in conn.execute("SELECT * FROM rack_diff_item WHERE room = ? AND (rack = ? OR rack_stored = ?)", (room, rack, stored)):
        d = _row(r)
        if not rack_layout.same_rack(names, room, d["rack_stored"] or d["rack"], rack):
            continue
        brief = {"id": d["id"], "status": d["status"], "status_label": d["status_label"], "reason_label": d["reason_label"],
                 "bind_key": d["bind_key"] if d["status"] == "matched" else None}
        if d["subject"] == "sys" and d["open"]:
            out["sys"][d["sys_key"]] = brief
        if d["subject"] == "xl":
            if d["open"]:
                out["xl_open"][f"{d['xl_run_id']}:{d['xl_row_id']}"] = brief
                out["xl_open_match"][d["xl_match"]] = brief
            # 處理過（退回 Excel 中、或結案但不是駁回）：同一格下一版 Excel 還寫著 → 「Excel 尚未更新」
            if d["status"] == "excel" or (d["status"] in ("closed", "matched") and d.get("close_action") != "reject"):
                out["xl_done"][d["xl_match"]] = brief
                out["xl_done_u"].setdefault(f"{d['u_start']}-{d['u_end']}", []).append(brief)
    return out


def annotate_cells(conn, room: str, rack: str, excel: dict, ms: list[dict] | None = None) -> dict:
    """把差異待辦標到機櫃圖的 Excel 層格子上（rack_detail 用；不寫庫）：
    · 這一格（或前一版同一格）有未處理待辦 → cell.diff
    · 前一版處理過、這一版文字完全一樣 → status＝STALE「Excel 尚未更新」（不再開新待辦）
    · 前一版同一段 U 處理過、但文字不同 → cell.recheck「待確認」（不沿用前一次的處理、不猜）
    · 綁定過（已匹配）而且那台現在就在這一格 → 兩邊一致：這格不再畫成 Excel 框，那台的對照結果記「一致」（GPT A4，不是 STALE）；
      那台後來又搬走了 → 照現在的比對結果顯示，不標 STALE"""
    marks = rack_marks(conn, room, rack)
    if not excel or not excel.get("cells"):
        return marks
    import rack_layout
    stored = rack_layout.stored_rack(rack_layout.rack_name_map(conn), room, rack)
    run_id = (excel.get("run") or {}).get("run_id")
    names = rack_layout.rack_name_map(conn)
    pos = {m["key"]: m for m in (ms or [])}
    keep = []
    for c in excel["cells"]:
        mk = xl_match_key(room, stored, c["u_start"], c["u_end"], c["label"])
        c["match"] = mk
        c["diff"] = marks["xl_open"].get(f"{run_id}:{c['id']}") or marks["xl_open_match"].get(mk)
        done = marks["xl_done"].get(mk)
        if done and not c["diff"] and done.get("bind_key"):
            m = pos.get(done["bind_key"])
            if m and m.get("kind") == "rack" and m.get("room") == room and rack_layout.same_rack(names, room, m.get("rack"), rack) \
                    and (m.get("u_start"), m.get("u_end")) == (c["u_start"], c["u_end"]):
                excel.setdefault("by_key", {})[done["bind_key"]] = "OK"
                continue                         # 一致：不畫 Excel 框（系統那台就在這格）
            keep.append(c)
            continue
        if done and not c["diff"]:
            c["status_raw"] = c["status"]
            c["status"] = "STALE"
            c["stale"] = done
        elif not c["diff"]:
            prev = marks["xl_done_u"].get(f"{c['u_start']}-{c['u_end']}")
            if prev:
                c["recheck"] = prev
        keep.append(c)
    excel["cells"] = keep
    return marks


# ------------------------------------------------------------------ 匯出給 CIA 窗口／Excel 維護者
EXPORT_HEADERS = ["項次", "狀態", "機房", "機櫃", "U", "對象", "設備／Excel 文字", "原因", "回報人", "回報時間", "備註", "序號",
                  "新位置", "處理", "處理人", "處理時間", "核准人", "依據"]


def export_rows(conn, status: str | None = None, room: str | None = None) -> list[list]:
    out = []
    for d in list_items(conn, status, room):
        tgt = f"{d['target_room'] or ''} {d['target_rack'] or ''} {d['target_u_start'] or ''}-{d['target_u_end'] or ''}U" if d["target_rack"] else ""
        out.append([f"D{d['id']}", d["status_label"], d["room"], d["rack"], d["u_text"], d["subject_label"], d["who"], d["reason_label"],
                    d["created_by"], d["created_at"], d["note"], d["serial"], tgt.strip(), d["close_label"],
                    d["updated_by"] if d["status"] != "pending" else "", d["updated_at"] if d["status"] != "pending" else "",
                    d["approver"], d["basis"]])
    return out
