"""EOS 批二（2.041.73）：官方證據欄位、舊 confirmed 待重查、資產覆蓋率與未匹配、對照規則修改歷史。

## 這個模組是什麼、不是什麼
EOS 日期的**唯一事實來源**仍是 eos_data/os_eos.json、hardware_eos.json（`eos.lookup_*`）。本模組**不改它們、
不覆寫日期或 confidence**，只在旁邊補三樣東西：

1. **官方證據**（eos_evidence／eos_evidence_history）：某個 EOS 表項目是誰、哪天查證的、官方原文怎麼寫、適用範圍、
   日期精度。證據不齊只是「證據不完整」，不會讓既有日期消失，也不會自動升降可信度（缺資料不補）。
2. **待重查清單**：表內 confidence 為 confirmed／confirmed_via_attachment、但沒有完整證據記錄的項目（舊資料）。
   只列出來讓人補，不降級、不改通知與紅燈行為。
3. **資產覆蓋率與未匹配清單**：逐台（machine_key 一把尺）看 OS／硬體各落在「已確認／推定／未知／未匹配／未填」哪一格，
   加總＝下鑽走同一份 rows。
4. **對照規則修改歷史**（eos_mapping_history）：別名（normalize_alias）、正規名稱覆寫（normalize_canonical_override）、
   EOS 分類覆寫（eos_category_override）每次新增／更正／刪除的前後值與操作者，與資料變更同一個交易。

## 可信度語意（UI／Excel／通知／ICS 共用，定義在 eos.py）
已確認／推定／未知，見 `eos.trust_level`。本模組只引用，不另造詞。

## 硬規則
* 讀取絕不寫庫、不建表；表不存在＝當作還沒有記錄。寫入入口才 CREATE TABLE IF NOT EXISTS／ALTER（舊庫相容，冪等）。
* 稽核（activity_log）與資料同一個交易；稽核失敗整筆回滾、例外往上丟。
* 沒有簽核流程：證據是「記錄」，不是「簽核」。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from datetime import date, datetime

import eos
import eos_follow as ef

KINDS = ("os", "hw")
KIND_LABEL = {"os": "作業系統", "hw": "硬體型號"}
PRECISIONS = ("day", "month", "none")
PRECISION_LABEL = {"day": "日（完整日期）", "month": "月（官方只到年月）", "none": "無日期"}
CONFIRMED_CONF = ("confirmed", "confirmed_via_attachment")
EVIDENCE_FIELDS = ("verified_by", "verified_on", "quote_text", "scope_text", "date_precision", "source_url", "source_status")
FIELD_LABEL = {"verified_by": "查證人", "verified_on": "查證日期", "quote_text": "官方原文引用", "scope_text": "適用範圍",
               "date_precision": "日期精度", "source_url": "來源網址", "source_status": "來源狀態"}
# #131（2026-11-12）：來源是否還有效。source_status＝查證人實際開過網址後記錄的結果（ok 可開啟／broken 打不開、404、
# 內容已不是這個產品）；沒記錄＝未檢查。過期＝查證日超過 SOURCE_RECHECK_DAYS 天沒複查。全站不自動上網抓網址（公司機連不出去）。
SOURCE_STATUSES = ("ok", "broken")
SOURCE_RECHECK_DAYS = 365
SOURCE_STATES = ("src_ok", "src_missing", "src_unverified", "src_stale", "src_broken")
SOURCE_STATE_LABEL = {"src_ok": "來源有效", "src_missing": "缺來源網址", "src_unverified": "有網址、未查證或來源未檢查",
                      "src_stale": f"過期未複查（>{SOURCE_RECHECK_DAYS} 天）", "src_broken": "來源網址失效"}
MAXLEN = {"verified_by": 100, "quote_text": 2000, "scope_text": 500, "source_url": 500}
REQUIRED_TO_SAVE = ("verified_on", "quote_text")

SQL = [
    """CREATE TABLE IF NOT EXISTS eos_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_kind TEXT NOT NULL CHECK (item_kind IN ('os','hw')),
        item_name TEXT NOT NULL,
        verified_by TEXT, verified_on TEXT, quote_text TEXT, scope_text TEXT,
        date_precision TEXT CHECK (date_precision IS NULL OR date_precision IN ('day','month','none')),
        source_url TEXT,
        version INTEGER NOT NULL DEFAULT 1,
        created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_eos_evidence_item ON eos_evidence(item_kind, item_name)",
    """CREATE TABLE IF NOT EXISTS eos_evidence_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id INTEGER NOT NULL, at TEXT NOT NULL, by TEXT,
        op TEXT NOT NULL, reason TEXT, before_json TEXT, after_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_eos_evidence_hist ON eos_evidence_history(evidence_id)",
]
# 舊庫補欄（冪等；只在寫入入口跑）
ADD_COLUMNS = (("eos_evidence", "source_status",
                "ALTER TABLE eos_evidence ADD COLUMN source_status TEXT CHECK (source_status IS NULL OR source_status IN ('ok','broken'))"),)
MAPPING_SQL = [
    """CREATE TABLE IF NOT EXISTS eos_mapping_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, by TEXT,
        rule_table TEXT NOT NULL, op TEXT NOT NULL, rule_key TEXT NOT NULL, before_json TEXT, after_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_eos_mapping_hist_at ON eos_mapping_history(at)",
]
MAPPING_TABLE_LABEL = {
    "normalize_alias": "別名（原始值→標準名）",
    "normalize_canonical_override": "標準名稱覆寫",
    "eos_category_override": "EOS 分類覆寫",
}
MAPPING_OP_LABEL = {"create": "新增", "update": "更正", "delete": "刪除"}

BUCKET_LABEL = {
    "confirmed": "已確認", "presumed": "推定", "unknown": "未知（表內有、無可用日期）",
    "unmatched": "未匹配（EOS 表查無）", "no_value": "未填（資產沒有這個欄位）", "not_applicable": "不適用（虛擬機）",
}
BUCKET_ORDER = ("confirmed", "presumed", "unknown", "unmatched", "no_value", "not_applicable")


class Conflict(ValueError):
    """同時有人改了同一筆（API 回 409）。"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    """只在寫入入口呼叫；讀取路徑絕不呼叫。"""
    for s in SQL:
        conn.execute(s)
    for s in MAPPING_SQL:
        conn.execute(s)
    for tbl, col, ddl in ADD_COLUMNS:
        if col not in {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")}:
            conn.execute(ddl)


def _tbl(conn: sqlite3.Connection, name: str) -> bool:
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
    except sqlite3.Error:
        return False


def _strip(v) -> str:
    return re.sub(r"[\r\n\t]+", " ", ef._strip_zw(str(v if v is not None else ""))).strip()


def _blank(v) -> bool:
    return ef._blank(v)


def _entries(kind: str) -> list[dict]:
    return eos._os_entries() if kind == "os" else eos._hw_entries()


def entry_of(kind: str, name: str) -> dict | None:
    return next((e for e in _entries(kind) if e.get("name") == name), None)


def all_entries() -> list[tuple[str, dict]]:
    return [(k, e) for k in KINDS for e in _entries(k)]


# ===================================================================== 證據讀取與完整度
def evidence_map(conn: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    if not _tbl(conn, "eos_evidence"):
        return {}
    return {(r["item_kind"], r["item_name"]): dict(r) for r in conn.execute("SELECT * FROM eos_evidence")}


def evidence_state(entry: dict, ev: dict | None, today: date | None = None) -> tuple[str, list[str]]:
    """(complete|partial|none, 缺什麼[])。完整＝查證人／日期（真實日期、不在未來）／原文引用／適用範圍／日期精度（且與表內
    日期一致）／來源網址（表內或證據自帶）都有。零寬字元視為空。"""
    if not ev:
        return "none", []
    today = today or ef.today_tpe()
    miss: list[str] = []
    if _blank(ev.get("verified_by")):
        miss.append(FIELD_LABEL["verified_by"])
    d = ef._date_ok(_strip(ev.get("verified_on")))
    if d is None:
        miss.append(FIELD_LABEL["verified_on"])
    elif d > today:
        miss.append("查證日期（不能在未來）")
    if _blank(ev.get("quote_text")):
        miss.append(FIELD_LABEL["quote_text"])
    if _blank(ev.get("scope_text")):
        miss.append(FIELD_LABEL["scope_text"])
    prec = ev.get("date_precision")
    derived = eos.date_precision(entry.get("eos_date"))
    if prec not in PRECISIONS:
        miss.append(FIELD_LABEL["date_precision"])
    elif prec != derived:
        miss.append(f"日期精度（證據寫「{PRECISION_LABEL[prec]}」，表內日期是「{PRECISION_LABEL[derived]}」）")
    if _blank(ev.get("source_url")) and _blank(entry.get("source_url")):
        miss.append(FIELD_LABEL["source_url"])
    elif ev.get("source_status") == "broken":
        miss.append("來源網址（已失效，請換有效網址）")
    return ("complete" if not miss else "partial"), miss


def source_url_of(entry: dict, ev: dict | None) -> str | None:
    """實際採用的來源網址：證據自帶的優先，沒有才用 EOS 表內的。"""
    for v in ((ev or {}).get("source_url"), entry.get("source_url")):
        if not _blank(v):
            return _strip(v)
    return None


def source_state(entry: dict, ev: dict | None, today: date | None = None) -> tuple[str, int | None]:
    """(src_ok|src_missing|src_unverified|src_stale|src_broken, 距查證日天數)。五格互斥，加總＝全部項目。
    順序：沒網址 → 缺；記錄過打不開 → 失效；沒有查證日、或來源狀態沒記錄「可開啟」（未檢查）→ 未查證；
    查證超過 SOURCE_RECHECK_DAYS → 過期；其餘（可開啟且查證日在期限內）才是有效。
    審查 P3（2026-11-12）：未檢查不能算有效——「有網址、來源未檢查」併進「未查證」格（五格互斥、加總不變）。"""
    today = today or ef.today_tpe()
    if source_url_of(entry, ev) is None:
        return "src_missing", None
    if (ev or {}).get("source_status") == "broken":
        d = ef._date_ok(_strip((ev or {}).get("verified_on")))
        return "src_broken", ((today - d).days if d else None)
    d = ef._date_ok(_strip((ev or {}).get("verified_on")))
    if d is None or d > today:
        return "src_unverified", None
    age = (today - d).days
    if (ev or {}).get("source_status") != "ok":
        return "src_unverified", age
    return ("src_stale" if age > SOURCE_RECHECK_DAYS else "src_ok"), age


STATE_LABEL = {"complete": "證據完整", "partial": "證據不完整", "none": "尚無證據記錄"}


def entry_rows(conn: sqlite3.Connection, today: date | None = None, usage: dict | None = None) -> list[dict]:
    """EOS 表每個項目一列（含證據完整度、是否待重查、影響台數）。項次固定＝表內順序（os 先、hw 後），與排序無關。"""
    today = today or ef.today_tpe()
    evs = evidence_map(conn)
    usage = usage or {}
    out: list[dict] = []
    for i, (kind, e) in enumerate(all_entries(), 1):
        ev = evs.get((kind, e["name"]))
        st, miss = evidence_state(e, ev, today)
        conf = e.get("confidence")
        legacy = conf in CONFIRMED_CONF and st != "complete"
        lvl = eos.trust_level(e)
        src, age = source_state(e, ev, today)
        out.append({
            "no": i, "kind": kind, "kind_label": KIND_LABEL[kind], "name": e["name"], "item_key": f"{kind}|{e['name']}",
            "eos_date": e.get("eos_date"), "eos_kind": e.get("eos_kind"), "confidence": conf,
            "trust": lvl, "trust_label": eos.TRUST_LABEL[lvl], "date_precision": eos.date_precision(e.get("eos_date")),
            "source_url": e.get("source_url"), "evidence_state": st, "evidence_label": STATE_LABEL[st], "evidence_missing": miss,
            "verified_by": (ev or {}).get("verified_by"), "verified_on": (ev or {}).get("verified_on"),
            "version": (ev or {}).get("version"), "recheck": legacy,
            "recheck_reason": ("沒有證據記錄" if st == "none" else "、".join(["缺" + m for m in miss])) if legacy else "",
            "machines": usage.get((kind, e["name"]), 0),
            "source_url_effective": source_url_of(e, ev), "scope_text": (ev or {}).get("scope_text"),
            "source_status": (ev or {}).get("source_status"), "source_state": src,
            "source_state_label": SOURCE_STATE_LABEL[src], "source_age_days": age,
        })
    return out


EVIDENCE_TILES = (
    ("all", "全部 EOS 表項目"), ("recheck", "待重查（舊 confirmed 無完整證據）"), ("complete", "證據完整"),
    ("partial", "證據不完整"), ("none", "尚無證據記錄"), ("presumed", "推定（來源未核對）"), ("unknown", "未知（無可用日期）"),
    *((k, SOURCE_STATE_LABEL[k]) for k in SOURCE_STATES),
)


def evidence_flag(r: dict, key: str) -> bool:
    if key == "all":
        return True
    if key == "recheck":
        return bool(r["recheck"])
    if key in ("complete", "partial", "none"):
        return r["evidence_state"] == key
    if key in ("presumed", "unknown"):
        return r["trust"] == key
    if key in SOURCE_STATES:
        return r["source_state"] == key
    return False


def evidence_overview(conn: sqlite3.Connection, today: date | None = None, usage: dict | None = None) -> dict:
    """tiles 與 drill 同一份 rows（加總＝下鑽）。"""
    rows = entry_rows(conn, today, usage)
    tiles = [{"key": k, "label": lb, "count": sum(1 for r in rows if evidence_flag(r, k))} for k, lb in EVIDENCE_TILES]
    return {"rows": rows, "tiles": tiles, "required_to_save": [FIELD_LABEL[f] for f in REQUIRED_TO_SAVE]}


def evidence_drill(ov: dict, key: str | None) -> list[dict]:
    return [r for r in ov["rows"] if evidence_flag(r, key or "all")]


def evidence_detail(conn: sqlite3.Connection, kind: str, name: str, today: date | None = None) -> dict:
    e = entry_of(kind, name)
    if e is None:
        raise LookupError("EOS 表查無這個項目")
    evs = evidence_map(conn)
    ev = evs.get((kind, name))
    st, miss = evidence_state(e, ev, today)
    hist: list[dict] = []
    if ev and _tbl(conn, "eos_evidence_history"):
        hist = [dict(r) for r in conn.execute("SELECT * FROM eos_evidence_history WHERE evidence_id=? ORDER BY id DESC", (ev["id"],))]
    lvl = eos.trust_level(e)
    return {"entry": {**e, "kind": kind, "trust": lvl, "trust_label": eos.TRUST_LABEL[lvl],
                      "date_precision": eos.date_precision(e.get("eos_date"))},
            "evidence": ev, "evidence_state": st, "evidence_label": STATE_LABEL[st], "evidence_missing": miss, "history": hist}


# ===================================================================== 證據寫入
def _norm_fields(fields: dict) -> dict:
    unknown = sorted(set(fields) - set(EVIDENCE_FIELDS))
    if unknown:
        raise ValueError(f"不認得的欄位：{'、'.join(unknown)}；EOS 日期與可信度不能從證據修改")
    out: dict = {}
    for k, v in fields.items():
        if v is None:
            out[k] = None
            continue
        t = _strip(v)
        if k == "date_precision":
            if t not in PRECISIONS:
                raise ValueError("日期精度只能是 day／month／none")
        elif k == "verified_on":
            d = ef._date_ok(t)
            if d is None:
                raise ValueError("查證日期不是有效日期（YYYY-MM-DD）")
        elif k == "source_url":
            if t and not re.match(r"^https?://\S+$", t):
                raise ValueError("來源網址必須是 http(s):// 開頭的網址")
        elif k == "source_status":
            if t and t not in SOURCE_STATUSES:
                raise ValueError("來源狀態只能是 ok（可開啟）或 broken（打不開／失效）")
        if k in MAXLEN and len(t) > MAXLEN[k]:
            raise ValueError(f"「{FIELD_LABEL[k]}」最多 {MAXLEN[k]} 字")
        out[k] = t or None
    return out


def _finish(conn, audit_hook, info: dict) -> None:
    ef._finish(conn, audit_hook, info)


def save_evidence(conn: sqlite3.Connection, kind: str, name: str, fields: dict, reason: str | None, by: str | None,
                  now: datetime | None = None, audit_hook=None, expected_version: int | None = None) -> dict:
    """新增或更正一筆官方證據。新增至少要有查證日期與官方原文引用；更正必須附理由並帶 expected_version（樂觀鎖）。
    查證日期不能在未來。回傳 {"changed", "evidence", "state", "missing"}。不改 EOS 表、不改可信度。"""
    now = now or ef.now_tpe()
    if kind not in KINDS:
        raise ValueError("kind 只能是 os 或 hw")
    entry = entry_of(kind, name)
    if entry is None:
        raise LookupError("EOS 表查無這個項目（名稱要與對照表完全相同）")
    if expected_version is not None and (isinstance(expected_version, bool) or not isinstance(expected_version, int)
                                        or not 0 <= expected_version <= 2_000_000_000):
        raise ValueError("版本號格式不對")
    upd = _norm_fields(fields)
    upd.pop("verified_by", None)          # 查證人一律＝實際操作的登入者，不接受呼叫端自填（避免冒填他人）
    if "verified_on" in upd and upd["verified_on"] and ef._date_ok(upd["verified_on"]) > now.date():
        raise ValueError("查證日期不能在未來")
    why = _strip(reason) or None
    if why and len(why) > 1000:
        raise ValueError("理由最多 1000 字")
    ensure_tables(conn)
    r = conn.execute("SELECT * FROM eos_evidence WHERE item_kind=? AND item_name=?", (kind, name)).fetchone()
    old = dict(r) if r else None
    if old is None:
        merged = {f: None for f in EVIDENCE_FIELDS}
        merged.update(upd)
        merged["verified_by"] = by or None
        for f in REQUIRED_TO_SAVE:
            if _blank(merged.get(f)):
                raise ValueError(f"請填「{FIELD_LABEL[f]}」（沒有官方原文引用與查證日期就不是證據）")
        diff_b, diff_a = {}, {f: merged[f] for f in EVIDENCE_FIELDS if merged[f] is not None}
    else:
        if expected_version is None:
            raise ValueError("更正既有證據必須帶 expected_version（先讀取目前版本再更正）")
        if int(old["version"]) != expected_version:
            raise Conflict("這筆證據剛被別人更新過（版本已變），請重新整理後再更正")
        if not why:
            raise ValueError("更正既有證據必須填更正理由")
        merged = {f: old.get(f) for f in EVIDENCE_FIELDS}
        merged.update(upd)
        # 審查 P3：換了來源網址、又沒有同時講新網址開不開得了 → 來源狀態回到「未檢查」（舊的可開啟／失效不適用新網址）
        if "source_url" in upd and upd["source_url"] != old.get("source_url") and "source_status" not in upd:
            merged["source_status"] = None
        merged["verified_by"] = by or old.get("verified_by")      # 更正者＝新的查證人，前後值進歷史
        for f in REQUIRED_TO_SAVE:
            if _blank(merged.get(f)):
                raise ValueError(f"「{FIELD_LABEL[f]}」不能清空")
        diff_b = {f: old.get(f) for f in EVIDENCE_FIELDS if merged[f] != old.get(f)}
        diff_a = {f: merged[f] for f in diff_b}
        if not diff_a:
            st, miss = evidence_state(entry, old, now.date())
            return {"changed": False, "evidence": old, "state": st, "missing": miss}
    try:
        if old is None:
            cols = ["item_kind", "item_name", *EVIDENCE_FIELDS, "version", "created_by", "created_at", "updated_by", "updated_at"]
            vals = [kind, name, *(merged[f] for f in EVIDENCE_FIELDS), 1, by, ef._ts(now), by, ef._ts(now)]
            try:
                cur = conn.execute(f"INSERT INTO eos_evidence ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
            except sqlite3.IntegrityError as exc:
                raise Conflict("這個項目剛被別人建立了證據記錄，請重新整理後改用更正") from exc
            eid, op = cur.lastrowid, "create"
        else:
            sets = [f"{k}=?" for k in diff_a] + ["version=version+1", "updated_by=?", "updated_at=?"]
            cur = conn.execute(f"UPDATE eos_evidence SET {','.join(sets)} WHERE id=? AND version=?",
                               [*diff_a.values(), by, ef._ts(now), old["id"], old["version"]])
            if cur.rowcount != 1:
                raise Conflict("這筆證據剛被別人更新過（版本已變），請重新整理後再更正")
            eid, op = old["id"], "update"
        conn.execute("INSERT INTO eos_evidence_history (evidence_id, at, by, op, reason, before_json, after_json) VALUES (?,?,?,?,?,?,?)",
                     (eid, ef._ts(now), by, op, why, json.dumps(diff_b, ensure_ascii=False) if diff_b else None,
                      json.dumps(diff_a, ensure_ascii=False)))
        _finish(conn, audit_hook, {"op": op, "item_key": "|".join((kind, name)), "fields": sorted(diff_a)})
    except Exception:
        conn.rollback()
        raise
    ev = dict(conn.execute("SELECT * FROM eos_evidence WHERE id=?", (eid,)).fetchone())
    st, miss = evidence_state(entry, ev, now.date())
    return {"changed": True, "evidence": ev, "state": st, "missing": miss}


# ===================================================================== 資產覆蓋率與未匹配
def _bucket_of(hit: dict | None) -> str:
    if hit is None:
        return "unmatched"
    return {"confirmed": "confirmed", "presumed": "presumed", "unknown": "unknown"}[eos.trust_level(hit)]


def coverage_rows(conn: sqlite3.Connection) -> list[dict]:
    """逐台×兩軌（作業系統／硬體）各一列，每列恰好落在一個 bucket。
    母體與取值**直接呼叫通知引擎的 eos_notify.resolve_machines**（同一個函式，不各自查詢），所以覆蓋率與通知看到的是同一批機器、
    同一列的同一個值。同一台有多筆不同的非空 OS／型號時，取值有確定規則（序號、再依值排序，只取被選中那一列，不拼接），
    並標 needs_review＝待核、列出各個不同的值。唯讀。"""
    import eos_notify as en

    out: list[dict] = []
    for m in en.resolve_machines(conn):
        rep = m["rep"]
        base = {"mk": m["mk"], "hostname": rep["hostname"], "ip": rep["ip"], "environment": rep["environment"]}
        for track in KINDS:
            pick = m[track]
            if pick is None:
                out.append({**base, "track": track, "raw_value": None, "canonical": None, "matched_name": None,
                            "bucket": "no_value", "trust": None, "needs_review": False, "conflict_values": ""})
                continue
            conflict = bool(pick["conflict"])
            extra = {"needs_review": conflict, "conflict_values": "；".join(pick["values"]) if conflict else ""}
            if track == "hw" and pick.get("family") == "虛擬化":
                out.append({**base, "track": track, "raw_value": pick["raw"], "canonical": pick["canonical"],
                            "matched_name": None, "bucket": "not_applicable", "trust": None, **extra})
                continue
            hit = pick["hit"]
            out.append({**base, "track": track, "raw_value": pick["raw"], "canonical": pick["canonical"],
                        "matched_name": (hit or {}).get("name"), "bucket": _bucket_of(hit),
                        "trust": eos.trust_level(hit) if hit else None, **extra})
    return out


def usage_of(rows: list[dict]) -> dict[tuple[str, str], int]:
    """EOS 表每個項目被幾台機器命中（逐台）。"""
    u: dict[tuple[str, str], int] = {}
    for r in rows:
        if r["matched_name"]:
            k = (r["track"], r["matched_name"])
            u[k] = u.get(k, 0) + 1
    return u


def coverage_overview(conn: sqlite3.Connection, rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else coverage_rows(conn)
    machines = len({r["mk"] for r in rows})
    tracks: dict[str, dict] = {}
    tiles: list[dict] = []
    for t in KINDS:
        sub = [r for r in rows if r["track"] == t]
        b = {k: sum(1 for r in sub if r["bucket"] == k) for k in BUCKET_ORDER}
        valid = len(sub) - b["no_value"] - b["not_applicable"]
        tracks[t] = {"label": KIND_LABEL[t], "total": len(sub), "buckets": b, "applicable": valid,
                     "confirmed_rate": round(b["confirmed"] / valid * 100, 1) if valid else None}
        for k in BUCKET_ORDER:
            tiles.append({"key": "|".join((t, k)), "track": t, "bucket": k, "label": f"{KIND_LABEL[t]}・{BUCKET_LABEL[k]}", "count": b[k]})
    # 未匹配清單（逐「標準名稱」彙總；每個項目附原始值樣本與台數，下鑽看逐台）
    grp: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r["bucket"] != "unmatched":
            continue
        g = grp.setdefault((r["track"], r["canonical"] or ""), {"track": r["track"], "track_label": KIND_LABEL[r["track"]],
                                                              "canonical": r["canonical"], "machines": 0, "raw_values": []})
        g["machines"] += 1
        if r["raw_value"] and r["raw_value"] not in g["raw_values"] and len(g["raw_values"]) < 5:
            g["raw_values"].append(r["raw_value"])
    unmatched = sorted(grp.values(), key=lambda g: (-g["machines"], g["track"], g["canonical"] or ""))
    for i, g in enumerate(unmatched, 1):
        g["no"] = i
        g["raw_text"] = "；".join(g["raw_values"])
    review = sum(1 for r in rows if r.get("needs_review"))
    return {"machines": machines, "tracks": tracks, "tiles": tiles, "unmatched": unmatched, "needs_review": review,
            "scope_note": "母體＝非退役資產逐台（machine_key）；與 EOS 通知引擎同一套正規化與查表。"}


def coverage_drill(rows: list[dict], track: str | None, bucket: str | None, item: str | None = None,
                   review: bool = False) -> list[dict]:
    out = [r for r in rows if (not track or r["track"] == track) and (not bucket or r["bucket"] == bucket)
           and (item is None or (r["canonical"] or "") == item) and (not review or r.get("needs_review"))]
    return [{**r, "no": i} for i, r in enumerate(out, 1)]


SORT_KEYS = ("no", "hostname", "ip", "environment", "raw_value", "canonical", "matched_name", "trust", "bucket", "track")


def sort_rows(rows: list[dict], key: str | None, direction: str = "asc") -> list[dict]:
    """伺服器端排序（項次 no 是固定編號，不因排序改變）。空值一律排最後；key 不在白名單就維持預設順序。"""
    if not key or key not in SORT_KEYS:
        return rows
    full = [r for r in rows if r.get(key) not in (None, "")]
    blank = [r for r in rows if r.get(key) in (None, "")]
    full.sort(key=lambda r: (str(r[key]).lower() if not isinstance(r[key], (int, float)) else r[key], r["no"]),
              reverse=(direction == "desc"))
    return full + blank


def page_rows(rows: list[dict], page: int = 1, size: int = 0) -> list[dict]:
    """伺服器端分頁；size=0＝全部（匯出用）。page 從 1 起算。"""
    if not size or size <= 0:
        return rows
    p = max(1, int(page or 1))
    return rows[(p - 1) * size: p * size]


# ===================================================================== 覆蓋率快照（磚與下鑽共用同一份計算）
SNAPSHOT_TTL = 30.0
_snap_lock = threading.Lock()
_snap: dict = {"key": None, "at": 0.0, "val": None}


def _data_fingerprint(conn: sqlite3.Connection) -> str:
    """影響覆蓋率結果的資料指紋：資產列（含取值欄位）、別名／覆寫表、EOS 對照表內容。資料有變，快照立刻作廢（不等 TTL）。"""
    h = hashlib.sha1()
    h.update(repr([tuple(r) for r in conn.execute(
        "SELECT asset_serial, hostname, ip, environment, os, device_model, asset_name, asset_purpose, asset_status, owner "
        "FROM hardware ORDER BY asset_serial")]).encode("utf-8"))
    for t in ("normalize_alias", "normalize_canonical_override", "eos_category_override"):
        if _tbl(conn, t):
            h.update(repr([tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]).encode("utf-8"))
    h.update(json.dumps([eos._os_entries(), eos._hw_entries()], ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()[:16]


def _db_identity(conn: sqlite3.Connection) -> str:
    try:
        return "|".join(str(r[2]) for r in conn.execute("PRAGMA database_list"))
    except sqlite3.Error:
        return ""


def clear_snapshot() -> None:
    with _snap_lock:
        _snap.update(key=None, at=0.0, val=None)


def coverage_snapshot(conn: sqlite3.Connection, now_mono: float | None = None) -> dict:
    """{"rows","overview","id","computed_at"}。磚（/api/eos/coverage）與下鑽（/api/eos/coverage/rows）都從這裡取同一份結果：
    行程內快取，鍵＝資料庫檔案＋資料指紋；30 秒內且指紋沒變就沿用，過期或資料變了就重算。唯讀。"""
    now = time.monotonic() if now_mono is None else now_mono
    key = (_db_identity(conn), _data_fingerprint(conn))
    with _snap_lock:
        if _snap["key"] == key and _snap["val"] is not None and 0 <= now - _snap["at"] < SNAPSHOT_TTL:
            return _snap["val"]
    rows = coverage_rows(conn)
    val = {"rows": rows, "overview": coverage_overview(conn, rows), "id": key[1],
           "computed_at": ef._ts(ef.now_tpe())}
    with _snap_lock:
        _snap.update(key=key, at=now, val=val)
    return val


# ===================================================================== 對照規則修改歷史
def log_mapping_change(conn: sqlite3.Connection, rule_table: str, op: str, rule_key, before, after, by: str | None,
                       now: datetime | None = None) -> None:
    """與規則變更**同一個交易**寫入一列歷史（呼叫端負責 commit；失敗就整筆回滾，例外不吞）。"""
    if rule_table not in MAPPING_TABLE_LABEL or op not in MAPPING_OP_LABEL:
        raise ValueError("未知的規則表或操作")
    for s in MAPPING_SQL:
        conn.execute(s)
    conn.execute("INSERT INTO eos_mapping_history (at, by, rule_table, op, rule_key, before_json, after_json) VALUES (?,?,?,?,?,?,?)",
                 (ef._ts(now or ef.now_tpe()), by, rule_table, op, json.dumps(rule_key, ensure_ascii=False),
                  json.dumps(before, ensure_ascii=False) if before is not None else None,
                  json.dumps(after, ensure_ascii=False) if after is not None else None))


def mapping_history(conn: sqlite3.Connection) -> list[dict]:
    """對照規則修改歷史（新→舊，固定項次＝由新到舊的順序）。唯讀；表不存在＝尚無歷史。"""
    if not _tbl(conn, "eos_mapping_history"):
        return []
    out = []
    for i, r in enumerate(conn.execute("SELECT * FROM eos_mapping_history ORDER BY id DESC"), 1):
        d = dict(r)
        d.update(no=i, rule_label=MAPPING_TABLE_LABEL.get(d["rule_table"], d["rule_table"]), op_label=MAPPING_OP_LABEL.get(d["op"], d["op"]))
        out.append(d)
    return out
