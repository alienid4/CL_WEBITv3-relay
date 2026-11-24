"""交易日情境標記（ACT-04 第一階段）——「大量交易日」不是事先定義的日型，而是人在事前或事後留下的標記，日後回測才有依據。

核心規則（使用者／GPT 5.6 討論結論）：
  1. 來源由**伺服器時間**自動判定，使用者不能選：交易日 09:00 前建立＝事前預告；09:00–13:30＝盤中標記；13:30 之後（或隔天以後）＝事後補標。
     事前預告與事後補標在任何統計都分開；連結事件應變不會改變來源。
  2. 開盤（交易日 09:00）後，預告欄位（原因類別、預期程度、依據說明、時段、類型）鎖定：要更正只能新增修訂版（supersedes_id），
     原始標記永遠保留；刪除只能「作廢」（保留原文與原因）。開盤前建立者可直接修改（有稽核，前後值）。
  3. 所有寫入都有稽核（建立人、伺服器時間、前後值），並與資料同一個交易（由路由一起 commit，這裡不自己 commit）。
  4. 與取數解耦：新增／修改標記不觸發任何 WhatsUp 查詢（本模組不 import 取數程式，也不呼叫它）。
  5. 市場背景（大盤漲跌幅、成交量）只收使用者自行輸入的文字＋來源說明，系統不捏造、不自動串接。
權限（路由層）：USER 可新增、修訂、連結事件；作廢限 ADMIN 且要寫原因（作廢會讓標記不再參與分析，影響回測，所以收斂到管理員）；viewer 唯讀。
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time as _time, timedelta
from typing import Any

HEAVY_TYPE = "大量交易日"               # 分析端只認這個類型當『大量交易日標註』
DEFAULT_TYPES = ["大量交易日", "其他情境"]
DEFAULT_REASONS = ["預期大漲", "預期大跌", "重大事件", "其他"]
LEVELS = ["", "低", "中", "高"]
DEFAULT_RANGES = [{"start": "09:00", "end": "09:30"}, {"start": "09:30", "end": "13:30"}]
OPEN_TIME = _time(9, 0)
CLOSE_TIME = _time(13, 30)
class MarkerConflict(ValueError):
    """競態或狀態衝突（已被修訂、已作廢）：路由回 409。"""


KINDS = {"pre_announce": "事前預告", "intraday": "盤中標記", "post_fact": "事後補標"}
LOCKED_FIELDS = ("mtype", "reason_cat", "expected_level", "basis", "ranges")      # 開盤後鎖定的預告欄位
EDITABLE_ALWAYS = ("note", "market_note", "market_source", "service_group")        # 備註類欄位；仍有稽核
DDL = [
    """CREATE TABLE IF NOT EXISTS capacity_day_marker (
        id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL, ranges_json TEXT NOT NULL,
        mtype TEXT NOT NULL, reason_cat TEXT NOT NULL, expected_level TEXT NOT NULL DEFAULT '', basis TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '', market_note TEXT NOT NULL DEFAULT '', market_source TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT '全系統', service_group TEXT NOT NULL DEFAULT '',
        source_kind TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL,
        supersedes_id INTEGER, voided_at TEXT, voided_by TEXT, void_reason TEXT,
        archive_ref TEXT, origin_source_kind TEXT)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_capmk_supersedes ON capacity_day_marker(supersedes_id) WHERE supersedes_id IS NOT NULL""",                      # archive_ref＝第二階段「日摘要封存」的參考鍵，現在不用；封存清理不得刪除仍被標記引用的日摘要
    """CREATE TABLE IF NOT EXISTS capacity_day_marker_event (
        marker_id INTEGER NOT NULL, event_id INTEGER NOT NULL, linked_by TEXT, linked_at TEXT NOT NULL, PRIMARY KEY (marker_id, event_id))""",
    """CREATE TABLE IF NOT EXISTS capacity_day_marker_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, marker_id INTEGER NOT NULL, at TEXT NOT NULL, actor TEXT, action TEXT NOT NULL,
        before_json TEXT, after_json TEXT)""",
]


def ensure(conn: sqlite3.Connection) -> None:
    for s in DDL:
        conn.execute(s)


def _now(now: datetime | None) -> datetime:
    import transfer_policy as tp
    if now is not None:
        # aware datetime 先轉成 Asia/Taipei 再去掉 tzinfo（UTC 00:59:59 ＝ 台北 08:59:59）；沒有 tzinfo 的視為已是台北時間
        return now.astimezone(tp.TZ).replace(tzinfo=None) if now.tzinfo else now                      # 伺服器時間（Asia/Taipei），測試可用 tp.set_clock 或直接傳 now
    return tp.now().replace(tzinfo=None)


@contextmanager
def _lock(conn: sqlite3.Connection):
    """明確鎖定：沒有交易就 BEGIN IMMEDIATE（寫鎖，兩個人同時改會排隊）；失敗只回滾自己開的交易。成功時不 commit，由路由與稽核一起 commit。"""
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if own:
            conn.rollback()
        raise


def _ts(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def source_kind(trade_date: str, now: datetime | None = None) -> str:
    """由伺服器時間決定，使用者不能選。"""
    t = _now(now)
    d = date.fromisoformat(trade_date)
    if t.date() < d:
        return "pre_announce"
    if t.date() > d:
        return "post_fact"
    tt = t.time()
    return "pre_announce" if tt < OPEN_TIME else "intraday" if tt < CLOSE_TIME else "post_fact"


def is_locked(trade_date: str, now: datetime | None = None) -> bool:
    """交易日 09:00（含）之後，預告欄位鎖定。"""
    t = _now(now)
    return t >= datetime.combine(date.fromisoformat(trade_date), OPEN_TIME)


# ---------------------------------------------------------------- 選項（類型、原因類別可設定）
def _setting(conn: sqlite3.Connection, key: str, default: list) -> list:
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return list(default)      # 沒有設定表＝用預設
    if not r or not r[0]:
        return list(default)
    try:
        v = json.loads(r[0])
        return v if isinstance(v, list) and v else list(default)
    except ValueError:
        return list(default)      # 設定壞掉＝退回預設（畫面另有設定損壞警示在 capacity_analysis.get_config）


def options(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    t = _now(now)
    return {"types": _setting(conn, "capacity_marker_types", DEFAULT_TYPES), "reasons": _setting(conn, "capacity_marker_reasons", DEFAULT_REASONS),
            "levels": LEVELS[1:], "default_ranges": DEFAULT_RANGES, "kinds": KINDS, "server_now": _ts(t),
            "today_kind": source_kind(t.date().isoformat(), t), "open": "09:00", "close": "13:30"}


def validate_options(types: Any, reasons: Any) -> tuple[list, list]:
    def one(v, name, must=None):
        if not isinstance(v, list) or not v or len(v) > 12:
            raise ValueError(f"{name}要是 1～12 個項目")
        out = []
        for x in v:
            s = str(x).strip()
            if not s or len(s) > 20 or s in out:
                raise ValueError(f"{name}每項 1～20 字且不可重複")
            out.append(s)
        if must and must not in out:
            raise ValueError(f"{name}必須包含「{must}」")
        return out
    return one(types, "類型清單", HEAVY_TYPE), one(reasons, "原因類別清單")


# ---------------------------------------------------------------- 驗證
def _hhmm(s: str) -> int:
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(s or "").strip())
    if not m:
        raise ValueError(f"時間格式要是 HH:MM（收到 {s!r}）")
    return int(m.group(1)) * 60 + int(m.group(2))


def _norm_ranges(r: Any) -> list[dict]:
    if r is None or r == []:
        return [dict(x) for x in DEFAULT_RANGES]
    if not isinstance(r, list) or len(r) > 6:
        raise ValueError("時段範圍最多 6 段")
    out, spans = [], []
    for x in r:
        a, b = _hhmm(x.get("start") if isinstance(x, dict) else None), _hhmm(x.get("end") if isinstance(x, dict) else None)
        if a >= b:
            raise ValueError("時段範圍要是 起點 < 終點（左閉右開，不跨午夜）")
        for (c, d) in spans:
            if a < d and c < b:
                raise ValueError("時段範圍不可重疊")
        spans.append((a, b))
        out.append({"start": f"{a // 60:02d}:{a % 60:02d}", "end": f"{b // 60:02d}:{b % 60:02d}"})
    return sorted(out, key=lambda z: z["start"])


def _clean(conn: sqlite3.Connection, body: dict, base: dict | None = None, now: datetime | None = None) -> dict:
    """驗證並正規化欄位（base＝修訂／修改時的原值）。來源類欄位（source_kind／created_*）一律不收。"""
    b = {**(base or {}), **{k: v for k, v in body.items() if k not in ("source_kind", "created_by", "created_at", "id", "supersedes_id")}}
    opt = options(conn)
    td = b.get("trade_date")
    if not isinstance(td, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", td):
        raise ValueError("交易日期要是 YYYY-MM-DD")
    d = date.fromisoformat(td)
    if d > _now(now).date() + timedelta(days=366):
        raise ValueError("交易日期太遠（最多預告一年內）")
    mtype, reason = str(b.get("mtype") or "").strip(), str(b.get("reason_cat") or "").strip()
    if mtype not in opt["types"]:
        raise ValueError("類型不在清單內")
    if reason not in opt["reasons"]:
        raise ValueError("原因類別不在清單內")
    level = str(b.get("expected_level") or "").strip()
    if level not in LEVELS:
        raise ValueError("預期程度只能是 低／中／高 或不填")
    basis = str(b.get("basis") or "").strip()
    if not basis:
        raise ValueError("依據說明必填")
    if len(basis) > 500:
        raise ValueError("依據說明最長 500 字")
    return {"trade_date": td, "ranges": _norm_ranges(b.get("ranges") if "ranges" in b else b.get("ranges_json") and json.loads(b["ranges_json"])),
            "mtype": mtype, "reason_cat": reason, "expected_level": level, "basis": basis,
            "note": str(b.get("note") or "")[:500], "market_note": str(b.get("market_note") or "")[:500], "market_source": str(b.get("market_source") or "")[:200],
            "scope": "全系統", "service_group": str(b.get("service_group") or "")[:60]}


_COLS = ("id", "trade_date", "ranges_json", "mtype", "reason_cat", "expected_level", "basis", "note", "market_note", "market_source", "scope",
         "service_group", "source_kind", "created_by", "created_at", "supersedes_id", "voided_at", "voided_by", "void_reason", "origin_source_kind")


def _row(r) -> dict:
    d = dict(zip(_COLS, r))
    d["ranges"] = json.loads(d.pop("ranges_json"))
    return d


def get(conn: sqlite3.Connection, marker_id: int) -> dict | None:
    ensure(conn)
    r = conn.execute(f"SELECT {','.join(_COLS)} FROM capacity_day_marker WHERE id = ?", (int(marker_id),)).fetchone()
    return _row(r) if r else None


def _audit(conn: sqlite3.Connection, marker_id: int, actor: str | None, action: str, before: Any, after: Any, t: datetime) -> None:
    conn.execute("INSERT INTO capacity_day_marker_audit (marker_id, at, actor, action, before_json, after_json) VALUES (?,?,?,?,?,?)",
                 (marker_id, _ts(t), actor, action, json.dumps(before, ensure_ascii=False) if before is not None else None,
                  json.dumps(after, ensure_ascii=False) if after is not None else None))


def _insert(conn: sqlite3.Connection, c: dict, kind: str, actor: str | None, t: datetime, supersedes: int | None, origin: str | None = None) -> int:
    """source_kind＝這一筆自己的建立來源（修訂版就是 revision_source_kind）；origin_source_kind＝整條修訂鏈『首次標記』的來源，分析一律用它。"""
    cur = conn.execute(
        "INSERT INTO capacity_day_marker (trade_date, ranges_json, mtype, reason_cat, expected_level, basis, note, market_note, market_source, scope, service_group, "
        "source_kind, created_by, created_at, supersedes_id, origin_source_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (c["trade_date"], json.dumps(c["ranges"]), c["mtype"], c["reason_cat"], c["expected_level"], c["basis"], c["note"], c["market_note"], c["market_source"],
         c["scope"], c["service_group"], kind, actor, _ts(t), supersedes, origin or kind))
    return int(cur.lastrowid)


def create(conn: sqlite3.Connection, body: dict, actor: str | None, now: datetime | None = None) -> dict:
    """新增。來源由伺服器時間判定（body 裡就算帶了 source_kind 也會被丟掉）。不 commit，由路由與稽核一起 commit。"""
    ensure(conn)
    t = _now(now)
    c = _clean(conn, body, now=t)
    with _lock(conn):
        mid = _insert(conn, c, source_kind(c["trade_date"], t), actor, t, None)
        _audit(conn, mid, actor, "create", None, c, t)
    return get(conn, mid)


def update_direct(conn: sqlite3.Connection, marker_id: int, body: dict, actor: str | None, now: datetime | None = None) -> dict:
    """開盤（交易日 09:00）前才可直接修改預告欄位；之後鎖定，只能新增修訂版。備註類欄位隨時可補，一樣有稽核。已作廢或已被修訂取代的不能改。"""
    ensure(conn)
    t = _now(now)
    with _lock(conn):
        return _update_direct(conn, marker_id, body, actor, t)


def _update_direct(conn: sqlite3.Connection, marker_id: int, body: dict, actor: str | None, t: datetime) -> dict:
    cur = get(conn, marker_id)
    if not cur:
        raise LookupError("找不到這筆標記")
    if cur["voided_at"]:
        raise MarkerConflict("已作廢的標記不能修改")
    if _superseded(conn, marker_id):
        raise MarkerConflict("這筆已被修訂版取代，請修改最新的修訂版")
    changed_locked = [k for k in LOCKED_FIELDS if k in body and (_norm_cmp(k, body[k]) != _norm_cmp(k, cur.get(k)))]
    if changed_locked and is_locked(cur["trade_date"], t):
        raise PermissionError("交易日 09:00 開盤後預告欄位已鎖定：請新增修訂版（原始標記會保留）")
    if "trade_date" in body and body["trade_date"] != cur["trade_date"]:
        raise ValueError("交易日期不能改：請作廢後重建，或新增修訂版")
    c = _clean(conn, body, base=cur, now=t)
    before = {k: cur[k] for k in ("mtype", "reason_cat", "expected_level", "basis", "ranges", "note", "market_note", "market_source", "service_group")}
    conn.execute("UPDATE capacity_day_marker SET ranges_json=?, mtype=?, reason_cat=?, expected_level=?, basis=?, note=?, market_note=?, market_source=?, service_group=? WHERE id=?",
                 (json.dumps(c["ranges"]), c["mtype"], c["reason_cat"], c["expected_level"], c["basis"], c["note"], c["market_note"], c["market_source"], c["service_group"], int(marker_id)))
    after = {k: c[k] for k in before}
    _audit(conn, int(marker_id), actor, "update", before, after, t)
    return get(conn, marker_id)


def _norm_cmp(k: str, v: Any) -> Any:
    if k == "ranges":
        return json.dumps(_norm_ranges(v), sort_keys=True)
    return (v or "") if isinstance(v, str) or v is None else v


def _superseded(conn: sqlite3.Connection, marker_id: int) -> bool:
    return conn.execute("SELECT 1 FROM capacity_day_marker WHERE supersedes_id = ?", (int(marker_id),)).fetchone() is not None


def revise(conn: sqlite3.Connection, marker_id: int, body: dict, actor: str | None, now: datetime | None = None) -> dict:
    """新增修訂版（supersedes_id 指向被修訂者，且有唯一約束：同一原版只能有一個修訂版）。原始標記與它的來源判定永遠保留；
    修訂版的 source_kind（＝revision_source_kind）依它自己的建立時間判定，但 origin_source_kind 沿用鏈首——分析看的是首次標記的來源。
    在 BEGIN IMMEDIATE 內以條件式 INSERT 完成並檢查受影響列數；競態失敗回 MarkerConflict（路由 409）。"""
    ensure(conn)
    t = _now(now)
    with _lock(conn):
        cur = get(conn, marker_id)
        if not cur:
            raise LookupError("找不到這筆標記")
        if cur["voided_at"]:
            raise MarkerConflict("已作廢的標記不能修訂")
        if _superseded(conn, marker_id):
            raise MarkerConflict("這筆已有更新的修訂版，請修訂最新的那一版")
        if "trade_date" in body and body["trade_date"] != cur["trade_date"]:
            raise ValueError("修訂版的交易日期要與原標記相同")
        c = _clean(conn, body, base=cur, now=t)
        kind = source_kind(c["trade_date"], t)
        origin = cur.get("origin_source_kind") or cur["source_kind"]
        try:
            r = conn.execute(
                "INSERT INTO capacity_day_marker (trade_date, ranges_json, mtype, reason_cat, expected_level, basis, note, market_note, market_source, scope, service_group, "
                "source_kind, created_by, created_at, supersedes_id, origin_source_kind) "
                "SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM capacity_day_marker WHERE supersedes_id = ?) "
                "AND EXISTS (SELECT 1 FROM capacity_day_marker WHERE id = ? AND voided_at IS NULL)",
                (c["trade_date"], json.dumps(c["ranges"]), c["mtype"], c["reason_cat"], c["expected_level"], c["basis"], c["note"], c["market_note"], c["market_source"],
                 c["scope"], c["service_group"], kind, actor, _ts(t), int(marker_id), origin, int(marker_id), int(marker_id)))
        except sqlite3.IntegrityError as exc:
            raise MarkerConflict("這筆已有更新的修訂版（同時有人修訂）") from exc
        if r.rowcount != 1:
            raise MarkerConflict("這筆剛被修訂或作廢，請重新整理後再試")
        new_id = int(r.lastrowid)
        conn.execute("INSERT INTO capacity_day_marker_event (marker_id, event_id, linked_by, linked_at) SELECT ?, event_id, ?, ? FROM capacity_day_marker_event WHERE marker_id = ?",
                     (new_id, actor, _ts(t), int(marker_id)))                 # 修訂版沿用事件連結
        _audit(conn, new_id, actor, "revise", {"supersedes_id": int(marker_id)}, c, t)
        _audit(conn, int(marker_id), actor, "revised_by", None, {"new_id": new_id}, t)
    return get(conn, new_id)


def void(conn: sqlite3.Connection, marker_id: int, reason: str, actor: str | None, now: datetime | None = None) -> dict:
    """作廢（不刪除）：保留原文，加上作廢者、時間、原因。作廢後不參與分析。條件式 UPDATE（只有尚未作廢的才會動）並檢查受影響列數，稽核只在真的作廢時寫一條。"""
    ensure(conn)
    t = _now(now)
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("作廢要寫原因")
    with _lock(conn):
        cur = get(conn, marker_id)
        if not cur:
            raise LookupError("找不到這筆標記")
        if cur["voided_at"]:
            raise MarkerConflict("這筆已作廢")
        r = conn.execute("UPDATE capacity_day_marker SET voided_at=?, voided_by=?, void_reason=? WHERE id=? AND voided_at IS NULL", (_ts(t), actor, reason[:300], int(marker_id)))
        if r.rowcount != 1:
            raise MarkerConflict("這筆已被別人作廢")
        _audit(conn, int(marker_id), actor, "void", {"voided_at": None}, {"voided_at": _ts(t), "reason": reason[:300]}, t)
    return get(conn, marker_id)


def set_events(conn: sqlite3.Connection, marker_id: int, event_ids: list, actor: str | None, now: datetime | None = None) -> dict:
    """連結事件應變（多對多）。連結不改變標記來源判定，也不另建檢查清單。"""
    ensure(conn)
    t = _now(now)
    ids = sorted({int(x) for x in event_ids or []})
    if len(ids) > 20:
        raise ValueError("最多連結 20 筆事件")
    try:
        have = {r[0] for r in conn.execute("SELECT id FROM incident_event")}
    except sqlite3.OperationalError:
        have = set()           # 沒有事件應變表（測試／舊庫）：無法驗證，視為沒有任何事件
    bad = [i for i in ids if i not in have]
    if bad:
        raise ValueError(f"找不到事件應變編號：{bad[0]}")
    with _lock(conn):
        cur = get(conn, marker_id)
        if not cur:
            raise LookupError("找不到這筆標記")
        if cur["voided_at"]:
            raise MarkerConflict("已作廢的標記不能再連結事件")
        before = sorted(r[0] for r in conn.execute("SELECT event_id FROM capacity_day_marker_event WHERE marker_id = ?", (int(marker_id),)))
        conn.execute("DELETE FROM capacity_day_marker_event WHERE marker_id = ?", (int(marker_id),))
        for i in ids:
            conn.execute("INSERT INTO capacity_day_marker_event (marker_id, event_id, linked_by, linked_at) VALUES (?,?,?,?)", (int(marker_id), i, actor, _ts(t)))
        _audit(conn, int(marker_id), actor, "link_events", before, ids, t)
    return get(conn, marker_id)


# ---------------------------------------------------------------- 查詢
def listing(conn: sqlite3.Connection, frm: str | None = None, to: str | None = None, include_void: bool = True, now: datetime | None = None) -> list[dict]:
    ensure(conn)
    t = _now(now)
    q, a = f"SELECT {','.join(_COLS)} FROM capacity_day_marker WHERE 1=1", []
    if frm:
        q += " AND trade_date >= ?"
        a.append(frm)
    if to:
        q += " AND trade_date <= ?"
        a.append(to)
    rows = [_row(r) for r in conn.execute(q + " ORDER BY trade_date DESC, id DESC", a)]
    sup = {r["supersedes_id"] for r in rows if r["supersedes_id"]} | {r[0] for r in conn.execute("SELECT supersedes_id FROM capacity_day_marker WHERE supersedes_id IS NOT NULL")}
    ev: dict[int, list] = {}
    for mid, eid in conn.execute("SELECT marker_id, event_id FROM capacity_day_marker_event ORDER BY event_id"):
        ev.setdefault(mid, []).append(eid)
    out = []
    for r in rows:
        r["origin_source_kind"] = r.get("origin_source_kind") or r["source_kind"]
        r["revision_source_kind"] = r["source_kind"]
        r["kind_label"] = KINDS[r["source_kind"]]
        r["origin_kind_label"] = KINDS[r["origin_source_kind"]]
        r["status"] = "voided" if r["voided_at"] else "superseded" if r["id"] in sup else "active"
        r["status_label"] = {"voided": "已作廢", "superseded": "已被修訂取代", "active": "有效"}[r["status"]]
        r["locked"] = is_locked(r["trade_date"], t)
        r["event_ids"] = ev.get(r["id"], [])
        out.append(r)
    return out if include_void else [r for r in out if r["status"] != "voided"]


def audit_trail(conn: sqlite3.Connection, marker_id: int) -> list[dict]:
    ensure(conn)
    return [{"at": r[0], "actor": r[1], "action": r[2], "before": json.loads(r[3]) if r[3] else None, "after": json.loads(r[4]) if r[4] else None}
            for r in conn.execute("SELECT at, actor, action, before_json, after_json FROM capacity_day_marker_audit WHERE marker_id = ? ORDER BY id", (int(marker_id),))]


def annotated_days(conn: sqlite3.Connection) -> dict[str, str]:
    """分析端用：{日期: 'pre'|'post'|'pre+post'}——只讀『有效（未作廢、未被取代）』、類型為大量交易日、來源為事前預告或事後補標的標記；
    盤中標記不納入，事前預告與事後補標分開標示（不混成一類）。"""
    try:
        rows = listing(conn, include_void=False)
    except sqlite3.OperationalError:
        return {}      # 沒有標記表（舊庫）＝沒有標註
    kinds: dict[str, set] = {}
    for r in rows:
        if r["status"] != "active" or r["mtype"] != HEAVY_TYPE or r["origin_source_kind"] not in ("pre_announce", "post_fact"):
            continue          # 一律用鏈首（首次標記）的來源：事前預告被盤後修訂，仍然是事前預告
        kinds.setdefault(r["trade_date"], set()).add("pre" if r["origin_source_kind"] == "pre_announce" else "post")
    return {d: "+".join(sorted(k, key=lambda x: ("pre", "post").index(x))) for d, k in kinds.items()}
