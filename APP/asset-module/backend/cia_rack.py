"""S2 機櫃編號統一（2.061.4）：有效機櫃清冊＋舊寫法對照 → 一格一案 → 交 9.3 修正包（每批限一個機房）。

設計：docs/設計_CIA第三批_S1toS4_2026-11-11.md「S2」＋ GPT 第 6 點（清冊獨立表：版本、生效日、誰改、核准；「~」跨櫃存起訖兩櫃）。
**標準櫃名（使用者 2026-11-11 定）＝機櫃圖 Excel 上的櫃名**（rack_layout／rack_excel_import 對照時用的那份，機櫃以 Excel 為準）。
CIA 舊寫法對到這些名字；格式照 CIA 原本的「櫃/起-迄U」，只換櫃名。

## 有效機櫃清冊（cia_rack_roster）
一個機房一份、有版本：「從機櫃圖 Excel 載入」（該機房最近一次機櫃圖對照的櫃名）或人工補櫃 → 產生新版本（草稿）→ 管理員核准才生效。
同一機房只認「最新一個已核准版本」。不存 app_settings（GPT 第 6 點），不進版控（櫃名含現場真實值，存在資料庫）。

## 舊寫法對照（cia_rack_alias，只增不改）
系統先自動對：CIA 機櫃欄解析（rack_layout.parse_rack：全半形、空白、-／_、樓層前綴、括號代碼）→ 套全站櫃名對照（rack_name_map，CIA 名 → Excel 名）
→ 在清冊裡就對到。「7F-M11~M12」這種跨櫃拆成起訖兩櫃，兩個都在清冊才算對到。
對不到的列「CIA 有、清冊沒有」給人選（下拉＝該機房清冊的櫃）。人選的對照一列一筆，改了就新增一筆（最新的生效），可追溯。

## 案件與交出
需要改的（CIA 現值≠標準寫法）＝一台（machine_key）＋原值一案（共用案件表 kind=rack，room＝機房）。人確認後「交出」＝9.3 修正包一個批次，
**每批只收一個機房**（避免一次丟幾百格）；一列一格：資產序號、機櫃編號、原值→新值、原因（對照表第幾列／自動規則）。
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime

import cia_case

KIND = "rack"
SCHEMA = """
CREATE TABLE IF NOT EXISTS cia_rack_roster (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    room           TEXT NOT NULL,
    version        INTEGER NOT NULL,
    rack           TEXT NOT NULL,
    source         TEXT,                 -- excel:<機櫃圖對照編號>／manual
    effective_date TEXT,
    created_by     TEXT,
    created_at     TEXT NOT NULL,
    approved_by    TEXT,
    approved_at    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cia_rack_roster ON cia_rack_roster(room, version, rack);
CREATE TABLE IF NOT EXISTS cia_rack_alias (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room        TEXT NOT NULL,
    raw         TEXT NOT NULL,           -- CIA 原寫法（完整機櫃欄值）
    start_rack  TEXT,                    -- 清冊櫃名；NULL＝標成「不是機櫃／不處理」
    end_rack    TEXT,                    -- 跨櫃的迄櫃（單櫃＝NULL）
    note        TEXT,
    by          TEXT,
    at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cia_rack_alias ON cia_rack_alias(room, raw);
"""
_RANGE = re.compile(r"^\s*(?:\d{1,2}\s*F\s*[-_ ]?\s*)?(?P<a>[A-Za-z]*\d{1,3})\s*[~～]\s*(?:\d{1,2}\s*F\s*[-_ ]?\s*)?(?P<b>[A-Za-z]*\d{1,3})\s*(?P<rest>.*)$", re.I)
_FLOOR = re.compile(r"^\s*\d{1,2}\s*F\s*[-_ ]\s*", re.I)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure(conn) -> None:
    conn.executescript(SCHEMA)


def _ready(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cia_rack_roster'").fetchone() is not None


def _norm(r: str | None) -> str:
    import rack_layout
    return rack_layout._norm_rack(_FLOOR.sub("", r or "")) if (r or "").strip() else ""


# ---------------------------------------------------------------- 清冊

def excel_rooms(conn) -> dict[str, dict]:
    """有機櫃圖 Excel 對照的機房 → 最近一次對照（編號、時間、櫃名）。"""
    import json
    try:
        runs = conn.execute("SELECT r.id, r.room, r.created_at, r.rows_json FROM rack_excel_run r JOIN "
                            "(SELECT room, MAX(id) AS mid FROM rack_excel_run GROUP BY room) x ON x.mid = r.id").fetchall()
    except sqlite3.OperationalError:     # 還沒匯過任何機櫃圖 Excel
        return {}
    out = {}
    for r in runs:
        try:
            rows = json.loads(r["rows_json"] or "[]")
        except ValueError:
            rows = []            # 壞掉的那次對照：當這個機房沒有櫃名
        out[r["room"]] = {"run_id": r["id"], "at": r["created_at"], "racks": sorted({x["rack"] for x in rows if x.get("rack")})}
    return out


def roster_versions(conn, room: str) -> list[dict]:
    if not _ready(conn):
        return []
    return [dict(r) for r in conn.execute(
        "SELECT version, MIN(source) AS source, MIN(effective_date) AS effective_date, MIN(created_by) AS created_by, MIN(created_at) AS created_at, "
        "MIN(approved_by) AS approved_by, MIN(approved_at) AS approved_at, COUNT(*) AS racks FROM cia_rack_roster WHERE room = ? "
        "GROUP BY version ORDER BY version DESC", (room,))]


def roster(conn, room: str) -> dict | None:
    """這個機房生效中的清冊＝最新一個已核准版本。"""
    if not _ready(conn):
        return None
    v = conn.execute("SELECT MAX(version) FROM cia_rack_roster WHERE room = ? AND approved_at IS NOT NULL", (room,)).fetchone()[0]
    if v is None:
        return None
    racks = [r[0] for r in conn.execute("SELECT rack FROM cia_rack_roster WHERE room = ? AND version = ? ORDER BY rack", (room, v))]
    meta = next(x for x in roster_versions(conn, room) if x["version"] == v)
    return {**meta, "room": room, "racks": racks}


def load_roster(conn, room: str, by: str, extra: list[str] | None = None, effective_date: str | None = None) -> dict:
    """從機櫃圖 Excel 載入（＋人工補的櫃）→ 新版本草稿（要核准才生效）。"""
    room = (room or "").strip()
    ex = excel_rooms(conn).get(room)
    racks = set(ex["racks"]) if ex else set()
    racks |= {x.strip() for x in (extra or []) if x and x.strip()}
    if not racks:
        raise ValueError("這個機房沒有機櫃圖 Excel 的櫃名，也沒有補任何櫃")
    if effective_date and not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", effective_date):
        raise ValueError("生效日格式要是 YYYY-MM-DD")
    if conn.in_transaction:
        conn.commit()
    ensure(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        v = (conn.execute("SELECT MAX(version) FROM cia_rack_roster WHERE room = ?", (room,)).fetchone()[0] or 0) + 1
        now = _now()
        src = f"excel:{ex['run_id']}" if ex else "manual"
        if extra and ex:
            src += "+manual"
        for r in sorted(racks):
            conn.execute("INSERT INTO cia_rack_roster (room, version, rack, source, effective_date, created_by, created_at) VALUES (?,?,?,?,?,?,?)",
                         (room, v, r, src, effective_date or now[:10], by, now))
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {"room": room, "version": v, "racks": len(racks)}


def approve_roster(conn, room: str, version: int, by: str) -> dict:
    if not _ready(conn):
        raise LookupError("還沒有任何清冊")
    n = conn.execute("UPDATE cia_rack_roster SET approved_by = ?, approved_at = ? WHERE room = ? AND version = ? AND approved_at IS NULL",
                     (by, _now(), room, int(version))).rowcount
    conn.commit()
    if not n:
        raise ValueError("找不到這個草稿版本（可能已核准）")
    return {"room": room, "version": int(version), "racks": n}


# ---------------------------------------------------------------- 對照

def add_alias(conn, room: str, raw: str, start_rack: str | None, end_rack: str | None, note: str | None, by: str) -> dict:
    """人選對照（CIA 寫法 → 清冊櫃；start_rack 空＝標成不是機櫃／不處理）。只增不改：最新一筆生效。"""
    ro = roster(conn, room)
    if not ro:
        raise ValueError("這個機房還沒有核准的清冊")
    for x in (start_rack, end_rack):
        if x and x not in ro["racks"]:
            raise ValueError(f"「{x}」不在這個機房的清冊裡")
    if end_rack and not start_rack:
        raise ValueError("有迄櫃就要有起櫃")
    if not start_rack and not (note or "").strip():
        raise ValueError("標成不處理要寫原因")
    aid = conn.execute("INSERT INTO cia_rack_alias (room, raw, start_rack, end_rack, note, by, at) VALUES (?,?,?,?,?,?,?)",
                       (room, raw, start_rack or None, end_rack or None, (note or "").strip() or None, by, _now())).lastrowid
    conn.commit()
    return {"id": aid}


def _aliases(conn, room: str) -> dict[str, dict]:
    if not _ready(conn):
        return {}
    out = {}
    for r in conn.execute("SELECT * FROM cia_rack_alias WHERE room = ? ORDER BY id", (room,)):
        out[r["raw"]] = dict(r)          # 後面的蓋前面的＝最新一筆生效
    return out


def _u_text(lo, hi) -> str:
    return "" if not lo else (f"/{lo}U" if lo == hi else f"/{lo}-{hi}U")


def parse(raw: str) -> dict:
    """CIA 機櫃欄 → {kind: rack／range／skip／unparsed, a, b（正規化櫃名）, u}。"""
    import rack_layout
    v = (raw or "").strip()
    m = _RANGE.match(v)
    if m:
        rest = m.group("rest").strip()
        mu = re.match(r"^/\s*(\d{1,2})\s*(?:-\s*(\d{1,2}))?\s*U\s*$", rest, re.I)
        if rest and not mu:
            return {"kind": "unparsed"}
        lo, hi = (int(mu.group(1)), int(mu.group(2) or mu.group(1))) if mu else (None, None)
        return {"kind": "range", "a": _norm(m.group("a")), "b": _norm(m.group("b")), "u": _u_text(lo, hi)}
    pr = rack_layout.parse_rack(_FLOOR.sub("", v))
    if pr["kind"] in ("empty", "vm"):
        return {"kind": "skip"}
    if pr["kind"] != "rack" or pr.get("extra_ranges"):
        return {"kind": "unparsed"}
    return {"kind": "rack", "a": pr["rack"], "b": None, "u": _u_text(pr.get("u_start"), pr.get("u_end"))}


def mapping(conn, room: str) -> dict:
    """這個機房 CIA 出現過的每一種機櫃寫法：筆數、對到哪（自動／人選／對不到）、標準寫法。"""
    import blast_radius
    import cia_fix
    import rack_layout

    ro = roster(conn, room)
    std = {_norm(r): r for r in (ro["racks"] if ro else [])}
    names = rack_layout.rack_name_map(conn)
    al = _aliases(conn, room)
    rows, _cols = cia_fix._cia_rows(conn)
    by_raw: dict[str, list] = {}
    import system_stats as ss
    for r in rows:
        if blast_radius.rack_room(r["physical_location"]) != room or ss.is_retired_row(r):     # 退役＝備查，不統一、不計數（審查 P3）
            continue
        raw = r["rack_no"] if r["rack_no"] is not None else ""
        if not str(raw).strip():
            continue
        by_raw.setdefault(str(raw), []).append(r)

    def to_std(x: str | None) -> str | None:
        if not x:
            return None
        shown = rack_layout.display_rack(names, room, x)      # 全站櫃名對照（CIA 名 → Excel 名）
        return std.get(_norm(shown)) or std.get(_norm(x))

    out = []
    for i, (raw, rs) in enumerate(sorted(by_raw.items(), key=lambda kv: (-len(kv[1]), kv[0])), 1):
        p = parse(raw)
        a = al.get(raw)
        if a:
            how = "人選（對照表第 %d 列）" % a["id"]
            if not a["start_rack"]:
                st, new, why = "ignored", None, f"標成不處理：{a['note'] or ''}"
            else:
                body = a["start_rack"] + (f"~{a['end_rack']}" if a["end_rack"] else "")
                new, st, why = body + (p.get("u") or ""), "manual", how
        elif p["kind"] == "skip":
            continue
        elif p["kind"] == "unparsed":
            st, new, why = "unmatched", None, "寫法認不出來"
        else:
            sa, sb = to_std(p["a"]), to_std(p["b"]) if p["kind"] == "range" else None
            if not sa or (p["kind"] == "range" and not sb):
                st, new, why = "unmatched", None, "CIA 有、清冊沒有"
            else:
                new, st, why = sa + (f"~{sb}" if sb else "") + (p.get("u") or ""), "auto", "自動對照"
        if new is not None and new == raw:
            st = "ok"
        out.append({"__no": i, "raw": raw, "count": len(rs), "serials": [r["asset_serial"] for r in rs], "status": st, "new": new, "why": why,
                    "alias_id": a["id"] if a else None, "start_rack": (a or {}).get("start_rack"), "end_rack": (a or {}).get("end_rack")})
    return {"room": room, "roster": ro, "items": out,
            "counts": {k: sum(1 for x in out if x["status"] == k) for k in ("ok", "auto", "manual", "unmatched", "ignored")}}


# ---------------------------------------------------------------- 案件

def candidates(conn, room: str) -> list[dict]:
    import system_stats as ss
    m = mapping(conn, room)
    if not m["roster"]:
        return []
    serial_row = {r["asset_serial"]: dict(r) for r in conn.execute("SELECT asset_serial, hostname, ip, asset_status FROM hardware")}
    out = []
    for x in m["items"]:
        if x["status"] not in ("auto", "manual"):
            continue
        groups: dict[str, list] = {}
        for s in x["serials"]:
            r = serial_row.get(s) or {}
            # 退役自成一組（全站同一條：lifecycle_prefix）；mapping 已排除退役，這裡再守一次
            groups.setdefault(ss.lifecycle_prefix(r) + ss.machine_key(r.get("hostname"), r.get("ip"), s), []).append(s)
        for k, ss_ in groups.items():
            h = (serial_row.get(ss_[0]) or {}).get("hostname")
            out.append({"machine_key": k, "field": "rack_no", "old_value": x["raw"], "title": h or ss_[0], "room": room,
                        "match_basis": {"machine_key": k, "serials": ss_},
                        "detail": {"serials": ss_, "new": x["new"], "why": x["why"], "alias_id": x["alias_id"], "room": room}})
    return out


def scan(conn, by: str) -> dict:
    """所有有核准清冊的機房一起比對（一次 sync，才能正確標出已不存在的）。"""
    rooms = [r for r in excel_rooms(conn)] + ([x[0] for x in conn.execute("SELECT DISTINCT room FROM cia_rack_roster")] if _ready(conn) else [])
    cands = []
    for room in sorted(set(rooms)):
        cands += candidates(conn, room)
    return cia_case.sync(conn, KIND, cands, by)


def handoff(conn, room: str, by: str) -> dict:
    """這個機房已確認的交出成一個 9.3 修正包批次（每批限一個機房）。對照改過（新值跟確認時不同）的不交。"""
    import cia_fix

    room = (room or "").strip()
    if not room:
        raise ValueError("要選一個機房（每批只收一個機房）")
    m = {x["raw"]: x for x in mapping(conn, room)["items"]}
    hw = {r["asset_serial"]: dict(r) for r in conn.execute("SELECT * FROM hardware")}
    items, skipped = [], []
    for c in cia_case.list_cases(conn, KIND, "confirmed", room=room):
        d = c["decision"] or {}           # 只用人確認的內容（審查 P1），不用重新比對後的 detail
        cur = m.get(c["old_value"])
        if not d.get("new") or d.get("old") != c["old_value"]:
            skipped.append({"id": c["id"], "why": "確認內容不完整，請撤回後重新確認"})
            continue
        if not cur or cur["new"] != d["new"] or cur["status"] not in ("auto", "manual"):
            skipped.append({"id": c["id"], "why": "對照已改、請重新確認"})
            continue
        rows = []
        for s in d.get("serials") or []:
            r = hw.get(s)
            if not r or str(r.get("rack_no") or "") != c["old_value"]:
                continue
            rows.append({"asset_serial": s, "hostname": r.get("hostname"), "unit": cia_fix._unit_of(r),
                         "asset_group": (r.get("group_name") or "").strip() or "（未填群組）", "field": "rack_no",
                         "old_value": c["old_value"], "new_value": d["new"], "rule_id": "rack_name",
                         "reason": f"機櫃編號統一（{room}）：{d.get('why')}"})
        if not rows:
            skipped.append({"id": c["id"], "why": "CIA 現值已經不是原值"})
            continue
        items.append((c["id"], rows))
    if not items:
        return {"batch_id": None, "items": 0, "cases": 0, "skipped": skipped}
    r = cia_case.hand_to_fix(conn, KIND, items, f"第三批：機櫃編號（{room}）", by)
    r["skipped"] = skipped + r["skipped"]
    return r


def rooms(conn) -> list[dict]:
    """機房清單：有機櫃圖 Excel 的、或有清冊的；各自的清冊版本與核准狀態。"""
    ex = excel_rooms(conn)
    names = set(ex) | ({x[0] for x in conn.execute("SELECT DISTINCT room FROM cia_rack_roster")} if _ready(conn) else set())
    out = []
    for i, room in enumerate(sorted(names), 1):
        vs = roster_versions(conn, room)
        ro = roster(conn, room)
        out.append({"__no": i, "room": room, "excel_at": (ex.get(room) or {}).get("at"), "excel_racks": len((ex.get(room) or {}).get("racks") or []),
                    "version": ro["version"] if ro else None, "racks": len(ro["racks"]) if ro else 0,
                    "approved_by": ro["approved_by"] if ro else None, "approved_at": ro["approved_at"] if ro else None,
                    "draft": next((v["version"] for v in vs if not v["approved_at"]), None)})
    return out
