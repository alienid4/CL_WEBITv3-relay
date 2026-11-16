"""容量分析「日摘要封存」（ACT-04 第二階段，使用者決定：保存 2 年）。

目的：NMON／WhatsUp 的原始取樣會被覆蓋或過期，但容量判斷要回看兩年。收盤後把「每台主機 × 每個交易日」的分析結果凍結成一份日摘要，
日後不管來源怎麼變，這份證據仍在。

規則（107 第二階段設計、本檔逐條落實）：
  1. 暫定 → T+1 完成 → 修訂：同一個（主機, 日期）每次寫入都是**新的一版**（version 1、2、3…），列一旦寫入就不可改（資料庫 trigger 擋 UPDATE），
     不覆寫。階段 provisional（收盤後第一次）／final（T+1 起）／revision（完成後來源資料變了、內容也不同）。內容沒變不新增版本。
  2. 每版記：來源與粒度、演算法版本、當時的資產身分（資產序號、環境、用途、是否備援）、當時的時段設定、各格統計。
     **缺資料不補 0**：沒有樣本的格子 avg／p95／max 是 null、狀態寫「無資料／未收集／覆蓋不足」，另記缺值率。
  3. 保存期限預設 24 個月（日曆月份計算，月底自動夾到該月最後一天），admin 可設。
  4. 清理**只有 admin 手動**：先預覽影響筆數（預覽會寫稽核，清理必須對得上最近一次預覽的筆數與截止日），清理寫稽核；
     **排程不可清理**（本檔的 tick 不 import、不呼叫任何清理函式，有測試掃描）。
  5. 被引用的不可刪：標記表（capacity_day_marker）**所有列**（含被修訂取代的舊版、已作廢的）只要 archive_ref 指到該日摘要就整條版本鏈保護；
     標記的交易日期本身也保護（保守：寧可多留）。archive_ref 看不懂的格式 → 清理 fail-closed（拒絕），不猜。
     2.042.06 起另有「封存引用」（capacity_archive_citation：報告／簽呈引用哪幾筆日摘要）：只增不改——更正＝新增一版（supersedes_id），
     作廢＝另記一筆作廢紀錄；兩張表都有 trigger 擋 UPDATE／DELETE。保護一樣讀**所有版本**（舊版、作廢的都算）。
  7. 日摘要每格另記「中位數」與「超標分鐘」（capacity_analysis.dist_stats）：只用真實取樣、依來源自己的取樣間隔換算，
     中位數只在 raw 取樣且覆蓋足夠（ok）時給，其餘寫「不知道」；超標分鐘在覆蓋不足時仍給範圍（下限＝確定超標、上限含缺口），
     間隔推算／無資料／未收集／間隔太粗才寫「不知道」；彙整桶的桶內 min／max 能確定時給精確值、否則給上下限。不由平均／P95 推、不補 0。
  6. 自動封存是排程入口 capacity_archive_tick：走 quiet_window（靜默窗內一律不動，起訖讀 quiet_window 設定，預設平日 08:30–15:00，記延後，窗外第一個 tick 補），
     即使純本機計算也一樣（使用者：盤中不要有任何自動的重運算）。人工入口窗內需二次確認。
"""
from __future__ import annotations

import calendar
import hashlib
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any

import capacity_analysis as ca
import quiet_window as qw

_log = logging.getLogger("capacity_archive")

ENTRY = "capacity_archive_tick"            # quiet_window.ENTRIES 的代號
ALGO_VERSION = "capacity-day-summary/2"      # 2：每格加中位數與超標分鐘（2.042.06）（舊版 1 的封存保持原樣，不重算）
SYSTEM_ACTOR = "system:capacity_archive"
RETENTION_KEY = "capacity_archive_retention_months"
DEFAULT_RETENTION_MONTHS = 24
MIN_RETENTION_MONTHS, MAX_RETENTION_MONTHS = 1, 120
LOOKBACK_DAYS = 45            # 自動封存只回看這麼多天（更早的缺口走人工補封存）
MAX_PER_TICK = 40             # 一次 tick 最多處理幾個（主機,日期），其餘下一輪
PREVIEW_VALID_MIN = 30        # 清理必須對得上 30 分鐘內同一位管理員的預覽
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAGES = ("provisional", "final", "revision")
STAGE_LABEL = {"provisional": "暫定", "final": "完成（T+1）", "revision": "修訂版"}

DDL = [
    """CREATE TABLE IF NOT EXISTS capacity_day_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT, host TEXT NOT NULL, day TEXT NOT NULL, version INTEGER NOT NULL,
        stage TEXT NOT NULL CHECK (stage IN ('provisional','final','revision')),
        algo_version TEXT NOT NULL, sources_json TEXT NOT NULL, granularity_json TEXT NOT NULL, identity_json TEXT NOT NULL,
        summary_json TEXT NOT NULL, content_hash TEXT NOT NULL, source_fp TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, created_by TEXT NOT NULL,
        UNIQUE (host, day, version))""",
    "CREATE INDEX IF NOT EXISTS idx_capdayarch_day ON capacity_day_archive(day)",
    # 列一旦寫入就不可修改（不覆寫）；刪除只在清理流程把 guard 打開的同一個交易內才放行
    """CREATE TRIGGER IF NOT EXISTS trg_capdayarch_no_update BEFORE UPDATE ON capacity_day_archive
        BEGIN SELECT RAISE(ABORT, '日摘要封存列不可修改（要更正請新增修訂版）'); END""",
    """CREATE TABLE IF NOT EXISTS capacity_day_archive_guard (id INTEGER PRIMARY KEY CHECK (id = 1), allow_delete INTEGER NOT NULL DEFAULT 0)""",
    "INSERT OR IGNORE INTO capacity_day_archive_guard (id, allow_delete) VALUES (1, 0)",
    """CREATE TRIGGER IF NOT EXISTS trg_capdayarch_no_delete BEFORE DELETE ON capacity_day_archive
        WHEN (SELECT allow_delete FROM capacity_day_archive_guard WHERE id = 1) IS NOT 1
        BEGIN SELECT RAISE(ABORT, '日摘要封存列只能由管理員手動清理（先預覽）刪除'); END""",
    # 看過的來源指紋（可更新）：內容沒變就不新增版本，但要記住「這個指紋已經比對過」，避免每分鐘重算
    """CREATE TABLE IF NOT EXISTS capacity_day_archive_seen (host TEXT NOT NULL, day TEXT NOT NULL, source_fp TEXT NOT NULL, checked_at TEXT NOT NULL,
        PRIMARY KEY (host, day))""",
    """CREATE TABLE IF NOT EXISTS capacity_day_archive_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT, action TEXT NOT NULL, detail_json TEXT)""",
    # 封存引用（2.042.06）：只增不改。root_id＝這條版本鏈第一版的 id（第一版自己是 NULL）；supersedes_id 唯一＝一版只能被更正一次
    """CREATE TABLE IF NOT EXISTS capacity_archive_citation (
        id INTEGER PRIMARY KEY AUTOINCREMENT, root_id INTEGER, version INTEGER NOT NULL, supersedes_id INTEGER UNIQUE,
        title TEXT NOT NULL, purpose TEXT NOT NULL DEFAULT '', refs_json TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TRIGGER IF NOT EXISTS trg_capcite_no_update BEFORE UPDATE ON capacity_archive_citation
        BEGIN SELECT RAISE(ABORT, '封存引用不可修改（要更正請新增一版）'); END""",
    """CREATE TRIGGER IF NOT EXISTS trg_capcite_no_delete BEFORE DELETE ON capacity_archive_citation
        BEGIN SELECT RAISE(ABORT, '封存引用不可刪除（要撤回請作廢）'); END""",
    """CREATE TABLE IF NOT EXISTS capacity_archive_citation_void (
        citation_id INTEGER PRIMARY KEY, voided_at TEXT NOT NULL, voided_by TEXT NOT NULL, reason TEXT NOT NULL)""",
    """CREATE TRIGGER IF NOT EXISTS trg_capcitev_no_update BEFORE UPDATE ON capacity_archive_citation_void
        BEGIN SELECT RAISE(ABORT, '作廢紀錄不可修改'); END""",
    """CREATE TRIGGER IF NOT EXISTS trg_capcitev_no_delete BEFORE DELETE ON capacity_archive_citation_void
        BEGIN SELECT RAISE(ABORT, '作廢紀錄不可刪除'); END""",
]


class ArchiveConflict(ValueError):
    """狀態衝突（預覽已過期／資料已變動／設定損壞）：路由回 409。"""


def ensure(conn: sqlite3.Connection) -> None:
    """建表（IF NOT EXISTS，重複啟動不會重複建立或清掉資料）。舊版標記表若缺 archive_ref 欄位就補上（只加欄位，不動資料）。"""
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN")              # 全部 DDL 一個交易：引用表與作廢表一起建好或一起沒建（SQLite 的 DDL 可回滾）
    try:
        cite_n = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('capacity_archive_citation','capacity_archive_citation_void')").fetchone()[0]
    except sqlite3.Error as exc:
        if own and conn.in_transaction:
            conn.rollback()
        raise ArchiveConflict(f"封存引用的資料表狀態無法確認（{exc}），已暫停寫入") from exc
    try:
        for s in DDL:
            if cite_n == 1 and "capacity_archive_citation" in s:
                continue       # 引用表只剩一張＝狀態不明：不自動補建另一張（補建會把『不知道』變成『沒有』），讓清理 fail-closed、寫入拒絕
            if s.startswith("INSERT OR IGNORE"):
                if conn.execute("SELECT 1 FROM capacity_day_archive_guard WHERE id = 1").fetchone() is None:
                    conn.execute(s)
                continue
            conn.execute(s)
        import capacity_markers as mk
        mk.ensure(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(capacity_day_marker)")}
        if "archive_ref" not in cols:
            conn.execute("ALTER TABLE capacity_day_marker ADD COLUMN archive_ref TEXT")
    except BaseException:
        if own:
            conn.rollback()
        raise
    if own and conn.in_transaction:
        conn.commit()          # 只有第一次建 guard 列時才會走到；之後的寫入才能用 BEGIN IMMEDIATE 排隊


def _ensure_if_missing(conn: sqlite3.Connection) -> None:
    """讀取路徑用：表格已存在就完全不碰資料庫（唯讀帳號／登入停用下的 GET 不會因此建表或補欄位）；缺表才補（正常情況 init_db 啟動時已建）。"""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('capacity_day_archive','capacity_day_archive_guard','capacity_day_archive_audit')")}
    if len(have) < 3:
        ensure(conn)


@contextmanager
def _lock(conn: sqlite3.Connection):
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if own:
            conn.rollback()
        raise


def _now(now: datetime | None = None) -> datetime:
    """台北牆上時間（naive）。沒給就用靜默窗的時鐘（全站同一個）。"""
    if now is None:
        return qw.local_now()
    return qw._naive(now)


def _ts(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _audit(conn: sqlite3.Connection, actor: str | None, action: str, detail: dict, now: datetime | None = None) -> None:
    conn.execute("INSERT INTO capacity_day_archive_audit (at, actor, action, detail_json) VALUES (?,?,?,?)",
                 (_ts(_now(now)), actor, action, json.dumps(detail, ensure_ascii=False, default=str)))


# ---------------------------------------------------------------- 保存期限
def add_months(d: date, n: int) -> date:
    """日曆月份加減；目標月份沒有那一天就夾到該月最後一天（2024-02-29 − 24 個月 → 2022-02-28；03-31 − 1 個月 → 02-28／29）。"""
    total = d.year * 12 + (d.month - 1) + n
    y, m = divmod(total, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def cutoff_day(today: date, months: int) -> date:
    """日期『早於』截止日的日摘要才算過期（剛好滿 N 個日曆月的那天還保留）。"""
    return add_months(today, -months)


def get_retention(conn: sqlite3.Connection) -> dict:
    """{months, broken, default}。設定壞掉（非整數、超出範圍）→ broken＝True：畫面照預設顯示，但清理 fail-closed 拒絕。"""
    raw = None
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (RETENTION_KEY,)).fetchone()
        raw = r[0] if r else None
    except sqlite3.OperationalError:
        raw = None
    if raw is None or str(raw).strip() == "":
        return {"months": DEFAULT_RETENTION_MONTHS, "broken": False, "default": DEFAULT_RETENTION_MONTHS, "raw": None}
    try:
        v = int(str(raw).strip())
        if not MIN_RETENTION_MONTHS <= v <= MAX_RETENTION_MONTHS or str(v) != str(raw).strip():
            raise ValueError
    except ValueError:
        return {"months": DEFAULT_RETENTION_MONTHS, "broken": True, "default": DEFAULT_RETENTION_MONTHS, "raw": str(raw)}
    return {"months": v, "broken": False, "default": DEFAULT_RETENTION_MONTHS, "raw": str(raw)}


def set_retention(conn: sqlite3.Connection, months: Any, actor: str | None, now: datetime | None = None, commit: bool = False) -> dict:
    if isinstance(months, bool) or not isinstance(months, int) or not MIN_RETENTION_MONTHS <= months <= MAX_RETENTION_MONTHS:
        raise ValueError(f"保存月數要是 {MIN_RETENTION_MONTHS}～{MAX_RETENTION_MONTHS} 的整數")
    ensure(conn)
    before = get_retention(conn)
    with _lock(conn):
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (RETENTION_KEY, str(months)))
        _audit(conn, actor, "retention_set", {"from": before["months"], "to": months, "was_broken": before["broken"]}, now)
    if commit:
        conn.commit()
    return get_retention(conn)


# ---------------------------------------------------------------- 產生日摘要
def _cfg(conn: sqlite3.Connection) -> dict:
    cfg = dict(ca.get_config(conn))
    cfg["params"] = {**cfg["params"], "min_days": 1}      # 單日摘要：『有資料天數不足』不適用（那是跨日統計的門檻），否則單日永遠只剩最大值
    return cfg


_CELL_KEYS = ("source", "kind", "segment", "asset_serial", "slot", "slot_label", "metric", "samples", "expected", "coverage_pct", "status", "avg", "p95", "max", "max_at",
              "p95_note", "max_gap_min", "bucket", "partial") + ca.DIST_KEYS


def _slim(c: dict) -> dict:
    out = {k: c.get(k) for k in _CELL_KEYS}
    cov = c.get("coverage_pct")
    out["missing_pct"] = None if cov is None else round(max(0.0, 100.0 - float(cov)), 1)       # 缺值率；沒有期望樣本數＝不知道，不是 0
    return out


def is_trade_day(day: str, cfg: dict) -> bool:
    heavy = {h["date"] for h in cfg["heavy_days"]} | set((cfg.get("marker_days") or {}))
    return ca.day_type(day, heavy, {h["date"] for h in cfg["holidays"]}) != "off"


def summarize(conn: sqlite3.Connection, host: str, day: str, cfg: dict | None = None) -> dict | None:
    """該主機該日的日摘要內容（不含時間戳）；沒有任何樣本（或非交易日）回 None——不補零、不造空列。"""
    cfg = cfg or _cfg(conn)
    if not is_trade_day(day, cfg):
        return None
    res = ca.analyze_host(conn, host, cfg, day, day)
    cells = [_slim(c) for c in res["cells"] if c["day_type"] == "normal"]
    if not any((c["samples"] or 0) > 0 for c in cells):
        return None
    cells.sort(key=lambda c: (str(c["source"]), str(c["kind"]), str(c["segment"]), str(c["slot"]), str(c["metric"])))
    segs = [s for s in (res.get("context") or {}).get("segments", []) if s.get("from") <= day <= s.get("to")]
    seg = segs[0] if segs else {}
    identity = {"mapped": bool(seg.get("mapped")), "asset_serial": seg.get("asset_serial"), "environment": seg.get("environment"), "purpose": seg.get("purpose"),
                "standby": seg.get("standby"), "segment": seg.get("segment"), "conflict": bool(seg.get("conflict")), "missing_asset": bool(seg.get("missing_asset")),
                "device": host}
    gran = {"interval_s": res.get("interval_s"), "kinds": sorted({str(c["kind"]) for c in cells}),
            "per_source": {s: sorted({str(c["kind"]) for c in cells if c["source"] == s}) for s in res.get("sources", [])}}
    return {"sources": list(res.get("sources", [])), "granularity": gran, "identity": identity, "cells": cells,
            "config": {"slots": [{k: s.get(k) for k in ("key", "label", "start", "end", "priority")} for s in cfg["slots"]],
                       "min_coverage_pct": cfg["params"]["min_coverage_pct"]},
            "evidence": res.get("evidence"), "limit_codes": sorted({str(l["code"]) for l in res.get("limits", [])})}


def content_hash(body: dict) -> str:
    return hashlib.sha256(json.dumps({"algo": ALGO_VERSION, **body}, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 來源指紋（便宜：只看列數／長度，不解壓）
def _utc_spills_into_next(conn: sqlite3.Connection, host: str, prev: str, per: dict) -> bool:
    """前一天的來源列是否有標 UTC、且時刻 ≥ 16:00（換算台北＝隔天）。只在『這天自己沒資料、前一天有』時才解壓那幾列，量很小。"""
    if not per.get((host, prev)):
        return False
    for src in ca.SOURCES:
        try:
            rows = conn.execute(f"SELECT data FROM {src.table} WHERE host = ? AND day = ?", (host, prev)).fetchall()
        except sqlite3.OperationalError:
            continue
        for (blob,) in rows:
            try:
                d = ca._decode(blob)
            except ValueError:
                continue      # 壞列由 summarize 的限制欄說明，這裡不當候選依據
            if str(d.get("tz") or "").strip().upper() == "UTC" and any(str(t) >= "16:00" for t in (d.get("ts") or [])):
                return True
    return False


def source_fingerprints(conn: sqlite3.Connection, frm: str, to: str) -> dict[tuple[str, str], str]:
    """{(host, 日期): 指紋}。日期前後各含一天（UTC 來源轉台北日可能跨日）。一個來源表一次查詢。"""
    lo = (date.fromisoformat(frm) - timedelta(days=1)).isoformat()
    hi = (date.fromisoformat(to) + timedelta(days=1)).isoformat()
    per: dict[tuple[str, str], list] = {}
    for src in ca.SOURCES:
        try:
            rows = conn.execute(f"SELECT host, day, interval_s, length(data), hex(substr(data, -8)) FROM {src.table} WHERE day >= ? AND day <= ?", (lo, hi)).fetchall()
        except sqlite3.OperationalError:
            continue      # 來源表不存在＝這個來源沒資料
        for h, d, itv, ln, tail in rows:
            per.setdefault((h, d), []).append((src.name, itv, ln, tail))
    out: dict[tuple[str, str], str] = {}
    hosts = {h for h, _ in per}
    d0, d1 = date.fromisoformat(frm), date.fromisoformat(to)
    day = d0
    while day <= d1:
        ds = day.isoformat()
        for h in hosts:
            if not per.get((h, ds)) and not _utc_spills_into_next(conn, h, (day - timedelta(days=1)).isoformat(), per):
                continue      # 候選日＝這一天自己有來源資料，或前一天有標 UTC、且 UTC 16:00 後（＝台北隔天）的樣本（Codex 1025）。
                #               後一天不算——UTC 只會往後推 8 小時。前一天沒跨日的資料不會把沒資料的日子列進來。
            parts = []
            for off in (-1, 0, 1):
                parts.append((off, sorted(per.get((h, (day + timedelta(days=off)).isoformat()), []))))
            if any(p for _, p in parts):
                out[(h, ds)] = hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()
        day += timedelta(days=1)
    return out


def fingerprint_one(conn: sqlite3.Connection, host: str, day: str) -> str:
    fps = source_fingerprints(conn, day, day)
    return fps.get((host, day), "")


# ---------------------------------------------------------------- 寫入一版
def _latest(conn: sqlite3.Connection, host: str, day: str):
    return conn.execute("SELECT id, version, stage, content_hash FROM capacity_day_archive WHERE host = ? AND day = ? ORDER BY version DESC LIMIT 1", (host, day)).fetchone()


def write_version(conn: sqlite3.Connection, host: str, day: str, stage: str, body: dict, fp: str, actor: str, reason: str = "",
                  now: datetime | None = None, commit: bool = True) -> dict:
    """新增一版（絕不改舊版）。回 {result: written|unchanged, id, version, stage}。
    階段在鎖內重新判斷：已有 final／revision 的一律寫 revision（不降級回暫定）；暫定只在尚無任何版本、或同日仍是暫定時寫；
    內容雜湊跟最新一版相同就不新增（暫定→完成的升級除外，那是階段改變）。"""
    if stage not in STAGES:
        raise ValueError(f"階段不合法：{stage}")
    h = content_hash(body)
    t = _now(now)
    with _lock(conn):
        lat = _latest(conn, host, day)
        lat_ver, lat_stage, lat_hash = (lat[1], lat[2], lat[3]) if lat is not None else (0, None, None)
        if lat is None:
            st = "final" if stage == "revision" else stage
        elif lat_stage in ("final", "revision"):
            st = "revision"
        else:
            st = "final" if stage in ("final", "revision") else "provisional"
        conn.execute("INSERT INTO capacity_day_archive_seen (host, day, source_fp, checked_at) VALUES (?,?,?,?) "
                     "ON CONFLICT(host, day) DO UPDATE SET source_fp = excluded.source_fp, checked_at = excluded.checked_at", (host, day, fp, _ts(t)))
        promote = lat_stage == "provisional" and st == "final"
        if lat is not None and lat_hash == h and not promote:
            if commit:
                conn.commit()
            return {"result": "unchanged", "version": lat_ver, "stage": lat_stage}
        ver = lat_ver + 1
        cur = conn.execute(
            "INSERT INTO capacity_day_archive (host, day, version, stage, algo_version, sources_json, granularity_json, identity_json, summary_json, content_hash, "
            "source_fp, reason, created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (host, day, ver, st, ALGO_VERSION, json.dumps(body["sources"], ensure_ascii=False), json.dumps(body["granularity"], ensure_ascii=False),
             json.dumps(body["identity"], ensure_ascii=False),
             json.dumps({k: body[k] for k in ("cells", "config", "evidence", "limit_codes")}, ensure_ascii=False, default=str), h, fp, reason or "", _ts(t), actor))
    if commit:
        conn.commit()
    return {"result": "written", "id": cur.lastrowid, "version": ver, "stage": st}


# ---------------------------------------------------------------- 該封存什麼
def _closed(day: str, now: datetime) -> bool:
    import transfer_policy as tp
    d = date.fromisoformat(day)
    return d < now.date() or (d == now.date() and now.time() >= tp.MARKET_CLOSE)


def planned_stage(day: str, now: datetime, lat_stage: str | None) -> str:
    """這個（主機,日）此刻該寫哪一階段。已有 final／revision → revision；日期已過（T+1 起）→ final；當天收盤後 → provisional。"""
    if lat_stage in ("final", "revision"):
        return "revision"
    return "final" if date.fromisoformat(day) < now.date() else "provisional"


def due_items(conn: sqlite3.Connection, now: datetime | None = None, lookback_days: int = LOOKBACK_DAYS) -> list[dict]:
    """哪些（主機,日）需要寫新的一版。便宜：一個來源表一次查詢＋一次最新版查詢，不解壓資料。"""
    ensure(conn)
    t = _now(now)
    today = t.date()
    frm = (today - timedelta(days=lookback_days)).isoformat()
    fps = source_fingerprints(conn, frm, today.isoformat())
    if not fps:
        return []
    cfg = _cfg(conn)
    latest = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(
        "SELECT a.host, a.day, a.stage, a.source_fp FROM capacity_day_archive a WHERE a.day >= ? AND a.version = "
        "(SELECT MAX(version) FROM capacity_day_archive b WHERE b.host = a.host AND b.day = a.day)", (frm,))}
    seen = {(r[0], r[1]): r[2] for r in conn.execute("SELECT host, day, source_fp FROM capacity_day_archive_seen WHERE day >= ?", (frm,))}
    out = []
    for (host, day), fp in sorted(fps.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if day > today.isoformat() or not is_trade_day(day, cfg) or not _closed(day, t):
            continue
        lat = latest.get((host, day))
        lat_stage = lat[0] if lat else None
        known_fp = seen.get((host, day), lat[1] if lat else None)
        if lat_stage is None:
            if known_fp == fp:
                continue      # 看過、當時沒有可封存的內容（例如全是無資料）且來源沒變
            out.append({"host": host, "day": day, "stage": planned_stage(day, t, None), "fp": fp, "why": "尚未封存"})
        elif lat_stage == "provisional" and date.fromisoformat(day) < today:
            out.append({"host": host, "day": day, "stage": "final", "fp": fp, "why": "T+1 完成"})
        elif known_fp != fp:
            out.append({"host": host, "day": day, "stage": planned_stage(day, t, lat_stage), "fp": fp, "why": "來源資料有變動"})
    return out


def archive_one(conn: sqlite3.Connection, host: str, day: str, stage: str, fp: str, actor: str, reason: str = "", now: datetime | None = None,
                cfg: dict | None = None, commit: bool = True) -> dict:
    body = summarize(conn, host, day, cfg)
    if body is None:
        t = _now(now)
        with _lock(conn):
            conn.execute("INSERT INTO capacity_day_archive_seen (host, day, source_fp, checked_at) VALUES (?,?,?,?) "
                         "ON CONFLICT(host, day) DO UPDATE SET source_fp = excluded.source_fp, checked_at = excluded.checked_at", (host, day, fp, _ts(t)))
        if commit:
            conn.commit()
        return {"result": "skipped", "reason": "沒有可封存的樣本（或非交易日）；不補 0、不建空列"}
    return write_version(conn, host, day, stage, body, fp, actor, reason, now, commit)


# ---------------------------------------------------------------- 排程入口（只在窗外動）
def tick(conn: sqlite3.Connection, now: datetime | None = None, limit: int = MAX_PER_TICK) -> int:
    """自動封存。盤中靜默窗（quiet_window 設定，預設平日 08:30–15:00）內一律不動、記延後，窗外第一個 tick 補。
    只在『確實有東西要封存』時才記延後（沒東西不必擾動延後紀錄）。**這裡沒有任何清理：排程不可清理。**"""
    t = _now(now)
    items = due_items(conn, t)
    if not items:
        return 0
    if qw.defer_if_quiet(conn, "capacity_archive_tick", t):
        return 0
    cfg = _cfg(conn)
    done = failed = 0
    for it in items[:limit]:
        if qw.defer_if_quiet(conn, "capacity_archive_tick", t if now is not None else None):       # 長任務：逐項重查（沒指定 now 就用即時時鐘），進窗就停，剩下的下一個窗外 tick 補
            break
        try:
            r = archive_one(conn, it["host"], it["day"], it["stage"], it["fp"], SYSTEM_ACTOR, it["why"], t, cfg)
            done += 1 if r.get("result") == "written" else 0
        except Exception as exc:  # noqa: BLE001 - 單台失敗不擋其他台；錯誤寫系統日誌＋稽核，下一輪會再試
            failed += 1
            _log.error("日摘要封存失敗 %s %s：%s", it["host"], it["day"], exc)
            try:
                conn.rollback()
            except sqlite3.Error:
                _log.error("日摘要封存：rollback 失敗")
    if done or failed:
        try:
            _audit(conn, SYSTEM_ACTOR, "auto_run", {"written": done, "failed": failed, "due": len(items)}, t)
            conn.commit()
        except sqlite3.Error as exc:
            _log.error("日摘要封存：稽核寫入失敗 %s", exc)
    return done


# ---------------------------------------------------------------- 人工封存（admin；路由負責二次確認與稽核）
def run_manual(conn: sqlite3.Connection, day: str, host: str | None, actor: str, reason: str = "", now: datetime | None = None, commit: bool = False) -> dict:
    ensure(conn)
    if not DATE_RE.match(day or ""):
        raise ValueError("日期格式要 YYYY-MM-DD")
    t = _now(now)
    if date.fromisoformat(day) > t.date():
        raise ValueError("還沒到的日期不能封存")
    if not _closed(day, t):
        import transfer_policy as tp
        raise ValueError(f"這一天還沒收盤（{tp.MARKET_CLOSE.strftime('%H:%M')}），不能封存")      # 文字與 _closed 同一個截止時間來源（Codex 1023）
    cfg = _cfg(conn)
    if not is_trade_day(day, cfg):
        raise ValueError("這一天是非交易日（週末或休市日），不封存")
    fps = {h: fp for (h, d), fp in source_fingerprints(conn, day, day).items() if d == day}
    hosts = [host] if host else sorted(fps)
    results = []
    for h in hosts:
        if h not in fps:
            results.append({"host": h, "result": "skipped", "reason": "這台這天沒有任何來源資料"})
            continue
        lat = _latest(conn, h, day)
        lat_stage = lat[2] if lat is not None else None
        r = archive_one(conn, h, day, planned_stage(day, t, lat_stage), fps[h], actor, reason or "人工封存", t, cfg, commit=False)
        results.append({"host": h, **r})
    _audit(conn, actor, "run_manual", {"day": day, "host": host, "reason": reason, "written": sum(1 for r in results if r["result"] == "written"), "hosts": len(results)}, t)
    if commit:
        conn.commit()
    return {"day": day, "results": results}


# ---------------------------------------------------------------- 標記引用保護
_REF_SPLIT = re.compile(r"[,;\n]+")


def _parse_refs(raw: str) -> tuple[list[tuple], list[str]]:
    """archive_ref 可以是單一值、逗號／分號／換行分隔、或 JSON 陣列。格式：YYYY-MM-DD（當天全部主機）、YYYY-MM-DD|主機、archive:<id>。
    回 (看得懂的, 看不懂的原文)。"""
    s = (raw or "").strip()
    if not s:
        return [], []
    parts: list[str]
    if s.startswith("["):
        try:
            v = json.loads(s)
            parts = [str(x) for x in v] if isinstance(v, list) else [s]
        except ValueError:
            parts = [s]
    else:
        parts = _REF_SPLIT.split(s)
    ok, bad = [], []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})", p)
        m2 = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\|(.+)", p)
        m3 = re.fullmatch(r"archive:(\d+)", p, re.I)
        try:
            if m:
                date.fromisoformat(m.group(1))
                ok.append(("day", m.group(1)))
            elif m2:
                date.fromisoformat(m2.group(1))
                ok.append(("hostday", m2.group(1), m2.group(2).strip().lower()))
            elif m3:
                ok.append(("id", int(m3.group(1))))
            else:
                bad.append(p)
        except ValueError:
            bad.append(p)
    return ok, bad


def protection(conn: sqlite3.Connection) -> dict:
    """{'days': {日期: [原因]}, 'hostdays': {(日期,主機小寫): [原因]}, 'ids': {id: [原因]}, 'unparsed': [原文…]}。
    讀的是標記表『所有列』：被修訂取代的舊版、已作廢的也算（歷史修訂鏈與作廢紀錄的 archive_ref 一樣保護）。標記交易日期本身也保護（保守）。
    讀不了標記表（非『表不存在』）就丟例外——清理 fail-closed。"""
    days: dict[str, list] = {}
    hostdays: dict[tuple, list] = {}
    ids: dict[int, list] = {}
    unparsed: list[str] = []
    cite_rows, cite_problems = _citation_rows_for_protection(conn)
    unparsed += cite_problems                  # 讀不到／表不完整：當成看不懂的引用 → 清理 fail-closed
    for cid, ver, refs_json, kind in cite_rows:
        try:
            refs = json.loads(refs_json)
            raw = "\n".join(str(x) for x in refs) if isinstance(refs, list) else str(refs_json)
        except ValueError:
            raw = str(refs_json)
        ok, bad = _parse_refs(raw)
        unparsed += [f"引用 #{cid}：{b}" for b in bad]
        for item in ok:
            why = f"{kind} #{cid}（第 {ver} 版）"
            if item[0] == "day":
                days.setdefault(item[1], []).append(why)
            elif item[0] == "hostday":
                hostdays.setdefault((item[1], item[2]), []).append(why)
            else:
                ids.setdefault(item[1], []).append(why)
    try:
        rows = conn.execute("SELECT id, trade_date, archive_ref, supersedes_id, voided_at FROM capacity_day_marker").fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return {"days": days, "hostdays": hostdays, "ids": ids, "unparsed": unparsed}
        raise
    superseded = {r[3] for r in rows if r[3] is not None}
    for r in rows:
        mid, td, ref, sup, voided = r[0], r[1], r[2], r[3], r[4]
        kind = "已作廢的標記" if voided else ("被修訂取代的舊標記" if mid in superseded else "標記")
        if td and DATE_RE.match(str(td)):
            days.setdefault(str(td), []).append(f"{kind} #{mid} 的交易日期")
        ok, bad = _parse_refs(ref or "")
        unparsed += [f"標記 #{mid}：{b}" for b in bad]
        for item in ok:
            why = f"{kind} #{mid} 的 archive_ref"
            if item[0] == "day":
                days.setdefault(item[1], []).append(why)
            elif item[0] == "hostday":
                hostdays.setdefault((item[1], item[2]), []).append(why)
            else:
                ids.setdefault(item[1], []).append(why)
    return {"days": days, "hostdays": hostdays, "ids": ids, "unparsed": unparsed}


def _chains(conn: sqlite3.Connection) -> dict[tuple[str, str], list[tuple]]:
    out: dict[tuple[str, str], list[tuple]] = {}
    for r in conn.execute("SELECT id, host, day, version, stage FROM capacity_day_archive ORDER BY day, host, version"):
        out.setdefault((r[1], r[2]), []).append((r[0], r[3], r[4]))
    return out


def protected_reasons(prot: dict, host: str, day: str, ids: list[int]) -> list[str]:
    why = list(prot["days"].get(day, [])) + list(prot["hostdays"].get((day, host.lower()), []))
    for i in ids:
        why += prot["ids"].get(i, [])
    return why


# ---------------------------------------------------------------- 清理（只有 admin 手動；排程不可呼叫）
def purge_candidates(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    ensure(conn)
    ret = get_retention(conn)
    t = _now(now)
    cut = cutoff_day(t.date(), ret["months"])
    prot = protection(conn)
    expired, protected = [], []
    for (host, day), vers in _chains(conn).items():
        if day >= cut.isoformat():
            continue
        why = protected_reasons(prot, host, day, [v[0] for v in vers])
        (protected if why else expired).append({"host": host, "day": day, "rows": len(vers), "ids": [v[0] for v in vers], "reasons": why[:5]})
    return {"retention_months": ret["months"], "retention_broken": ret["broken"], "today": t.date().isoformat(), "cutoff": cut.isoformat(),
            "host_days": len(expired), "rows": sum(x["rows"] for x in expired), "protected_host_days": len(protected),
            "protected_rows": sum(x["rows"] for x in protected), "unparsed_refs": prot["unparsed"],
            "oldest": min((x["day"] for x in expired), default=None), "newest": max((x["day"] for x in expired), default=None),
            "sample": [{"host": x["host"], "day": x["day"], "rows": x["rows"]} for x in expired[:20]],
            "protected_sample": [{"host": x["host"], "day": x["day"], "rows": x["rows"], "reasons": x["reasons"]} for x in protected[:20]],
            "_expired": expired}


def _refuse_if_unsafe(c: dict) -> None:
    if c["retention_broken"]:
        raise ArchiveConflict("保存月數設定損壞，已暫停清理；請先在『封存與保存』重新設定保存月數")
    if c["unparsed_refs"]:
        raise ArchiveConflict(f"有 {len(c['unparsed_refs'])} 筆引用看不懂或讀不到（{c['unparsed_refs'][0]}…），無法確認是否有被引用，已暫停清理")


def _ids_hash(c: dict) -> str:
    """候選刪除列的 id 集合雜湊：預覽與實刪必須是同一批（只比筆數會被『一進一出』騙過）。"""
    ids = sorted(i for x in c["_expired"] for i in x["ids"])
    return hashlib.sha256(",".join(map(str, ids)).encode()).hexdigest()


def purge_preview(conn: sqlite3.Connection, actor: str, now: datetime | None = None) -> dict:
    """預覽（寫稽核；清理必須對得上這次預覽）。呼叫端 commit。"""
    c = purge_candidates(conn, now)
    out = {k: v for k, v in c.items() if k != "_expired"}
    out["blocked"] = None
    try:
        _refuse_if_unsafe(c)
    except ArchiveConflict as exc:
        out["blocked"] = str(exc)
    _audit(conn, actor, "purge_preview", {"cutoff": c["cutoff"], "rows": c["rows"], "host_days": c["host_days"], "protected_rows": c["protected_rows"], "blocked": out["blocked"],
                                          "ids_hash": _ids_hash(c)}, now)
    return out


def purge(conn: sqlite3.Connection, actor: str, expected_rows: int, cutoff: str, confirm: bool, now: datetime | None = None) -> dict:
    """真的刪。必須：confirm＝True；最近 30 分鐘內同一位管理員的預覽、截止日與筆數都對得上；鎖內重算後筆數仍相同（預覽後資料變了就 409）。
    刪除與稽核同一個交易，由呼叫端 commit；任何一步失敗呼叫端 rollback。"""
    if confirm is not True:
        raise ValueError("清理需要明確確認（confirm=true）")
    ensure(conn)
    t = _now(now)
    with _lock(conn):
        pv = None
        since = _ts(t - timedelta(minutes=PREVIEW_VALID_MIN))
        for r in conn.execute("SELECT detail_json FROM capacity_day_archive_audit WHERE action = 'purge_preview' AND actor = ? AND at >= ? ORDER BY id DESC LIMIT 1", (actor, since)):
            pv = json.loads(r[0])
        if pv is None:
            raise ArchiveConflict("請先預覽（30 分鐘內）再清理：清理必須對得上預覽的筆數與截止日")
        c = purge_candidates(conn, t)
        _refuse_if_unsafe(c)
        if pv.get("cutoff") != cutoff or c["cutoff"] != cutoff or pv.get("rows") != expected_rows or c["rows"] != expected_rows or pv.get("ids_hash") != _ids_hash(c):
            raise ArchiveConflict(f"預覽後資料或截止日已變動（預覽 {pv.get('rows')} 筆、現在 {c['rows']} 筆），請重新預覽")
        ids = [i for x in c["_expired"] for i in x["ids"]]
        conn.execute("UPDATE capacity_day_archive_guard SET allow_delete = 1 WHERE id = 1")
        deleted = 0
        try:
            for k in range(0, len(ids), 500):
                chunk = ids[k:k + 500]
                deleted += conn.execute(f"DELETE FROM capacity_day_archive WHERE id IN ({','.join('?' * len(chunk))})", chunk).rowcount
        finally:
            conn.execute("UPDATE capacity_day_archive_guard SET allow_delete = 0 WHERE id = 1")      # 任何例外都關回去（不殘留開啟）
        if deleted != len(ids):
            raise ArchiveConflict("刪除筆數與預覽不一致，已回滾")
        _audit(conn, actor, "purge", {"cutoff": cutoff, "retention_months": c["retention_months"], "rows": deleted, "host_days": c["host_days"],
                                      "protected_rows_kept": c["protected_rows"], "oldest": c["oldest"], "newest": c["newest"]}, t)
    return {"deleted_rows": deleted, "host_days": c["host_days"], "cutoff": cutoff, "protected_rows_kept": c["protected_rows"]}


# ---------------------------------------------------------------- 讀
def _row_dict(r, protected: bool = False, latest_ver: int | None = None, detail: bool = False) -> dict:
    d = {"id": r["id"], "host": r["host"], "day": r["day"], "version": r["version"], "stage": r["stage"], "stage_label": STAGE_LABEL.get(r["stage"], r["stage"]),
         "algo_version": r["algo_version"], "sources": json.loads(r["sources_json"]), "granularity": json.loads(r["granularity_json"]),
         "identity": json.loads(r["identity_json"]), "content_hash": r["content_hash"], "reason": r["reason"], "created_at": r["created_at"],
         "created_by": r["created_by"], "protected": protected, "is_latest": latest_ver is not None and r["version"] == latest_ver}
    s = json.loads(r["summary_json"])
    cells = s.get("cells", [])
    d["cells_n"] = len(cells)
    d["ok_cells"] = sum(1 for c in cells if c.get("status") == "ok")
    d["no_data_cells"] = sum(1 for c in cells if c.get("status") in ("no_data", "not_collected"))
    d["low_coverage_cells"] = sum(1 for c in cells if c.get("status") == "low_coverage")
    d["evidence"] = s.get("evidence")
    if detail:
        d["summary"] = s
    return d


def _rows(conn: sqlite3.Connection, q: str, a: list | tuple = ()):
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return cur.execute(q, a).fetchall()


def listing(conn: sqlite3.Connection, frm: str | None = None, to: str | None = None, host: str | None = None, stage: str | None = None,
            latest_only: bool = False, limit: int = 500, offset: int = 0) -> dict:
    _ensure_if_missing(conn)
    q, a = "SELECT * FROM capacity_day_archive WHERE 1=1", []
    if frm:
        q += " AND day >= ?"
        a.append(frm)
    if to:
        q += " AND day <= ?"
        a.append(to)
    if host:
        q += " AND host = ?"
        a.append(host)
    if stage:
        if stage not in STAGES:
            raise ValueError("階段不合法")
        q += " AND stage = ?"
        a.append(stage)
    mx = {(r[0], r[1]): r[2] for r in conn.execute("SELECT host, day, MAX(version) FROM capacity_day_archive GROUP BY host, day")}
    prot = protection(conn)
    chains = _chains(conn)
    rows = _rows(conn, q + " ORDER BY day DESC, host, version DESC", a)
    out = []
    for r in rows:
        if latest_only and r["version"] != mx.get((r["host"], r["day"])):
            continue
        ids = [v[0] for v in chains.get((r["host"], r["day"]), [])]
        out.append(_row_dict(r, bool(protected_reasons(prot, r["host"], r["day"], ids)), mx.get((r["host"], r["day"]))))
    limit = max(1, min(int(limit), 2000))
    return {"total": len(out), "rows": out[offset:offset + limit]}


def get_one(conn: sqlite3.Connection, archive_id: int) -> dict | None:
    _ensure_if_missing(conn)
    found = _rows(conn, "SELECT * FROM capacity_day_archive WHERE id = ?", (archive_id,))
    if not found:
        return None
    r = found[0]
    mx = conn.execute("SELECT MAX(version) FROM capacity_day_archive WHERE host = ? AND day = ?", (r["host"], r["day"])).fetchone()[0]
    prot = protection(conn)
    ids = [v[0] for v in _chains(conn).get((r["host"], r["day"]), [])]
    why = protected_reasons(prot, r["host"], r["day"], ids)
    d = _row_dict(r, bool(why), mx, detail=True)
    d["protected_reasons"] = why
    return d


def _quiet_text(conn: sqlite3.Connection) -> str:
    """畫面顯示用：靜默窗起訖讀 quiet_window 設定（管理員可改），收盤時間讀 transfer_policy；不寫死時間字串。"""
    import transfer_policy as tp
    cfg = qw.get_config(conn)
    close = f"收盤 {tp.MARKET_CLOSE.strftime('%H:%M')} 後才能封存當天"
    if cfg["broken"]:
        return f"靜默窗設定有問題（{cfg['problem']}），自動封存暫停；{close}"
    if not cfg["enabled"]:
        return f"靜默窗已關閉；{close}"
    return f"平日 {cfg['start'].strftime('%H:%M')}–{cfg['end'].strftime('%H:%M')} 不自動封存；{close}"


def settings_view(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    _ensure_if_missing(conn)
    ret = get_retention(conn)
    t = _now(now)
    n = conn.execute("SELECT COUNT(*), COUNT(DISTINCT host || '|' || day), MIN(day), MAX(day) FROM capacity_day_archive").fetchone()
    last = conn.execute("SELECT at, actor, action, detail_json FROM capacity_day_archive_audit ORDER BY id DESC LIMIT 5").fetchall()
    return {"retention_months": ret["months"], "retention_broken": ret["broken"], "default_months": DEFAULT_RETENTION_MONTHS,
            "min_months": MIN_RETENTION_MONTHS, "max_months": MAX_RETENTION_MONTHS, "cutoff": cutoff_day(t.date(), ret["months"]).isoformat(),
            "rows": n[0], "host_days": n[1], "oldest": n[2], "newest": n[3], "algo_version": ALGO_VERSION, "quiet_window": _quiet_text(conn),
            "audit": [{"at": r[0], "actor": r[1], "action": r[2], "detail": json.loads(r[3] or "{}")} for r in last]}


# ---------------------------------------------------------------- 封存引用（2.042.06；只增不改，管理員寫入，稽核同交易）
CITE_TITLE_MAX, CITE_PURPOSE_MAX, CITE_NOTE_MAX, CITE_REFS_MAX = 120, 200, 500, 200
CITE_VOID_REASON_MAX = 300          # 作廢原因上限：超過回 400 講上限，不靜默截斷（1388 疑點 10）
CITE_STATUS_LABEL = {"active": "有效", "superseded": "已被更正取代", "voided": "已作廢"}


def _cite_tables(conn: sqlite3.Connection) -> bool:
    """兩張引用表是否都在。查不了（資料庫錯誤）→ ArchiveConflict（路由回 409「資料表狀態無法確認」），不回原始 500、也不當成「沒有」。"""
    try:
        n = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('capacity_archive_citation','capacity_archive_citation_void')").fetchone()[0]
    except sqlite3.Error as exc:
        raise ArchiveConflict(f"封存引用的資料表狀態無法確認（{exc}）") from exc
    return n == 2


def _citation_rows_for_protection(conn: sqlite3.Connection) -> tuple[list[tuple], list[str]]:
    """(引用列, 讀取問題)。引用列＝(id, 版本, refs_json, 說明)，所有版本都算（舊版、作廢的也保護）。
    兩張表都不存在＝從沒用過引用 → ([], [])。其他任何狀況（只剩一張表、讀取出錯）都是「不能確定有沒有被引用」：
    回讀取問題（呼叫端把它當看不懂的引用 → 清理 fail-closed）；讀得到的引用列照樣回傳、照樣保護。"""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('capacity_archive_citation','capacity_archive_citation_void')")}
    if not have:
        return [], []
    problems: list[str] = []
    if len(have) != 2:
        problems.append("封存引用的資料表不完整（" + "、".join(sorted({"capacity_archive_citation", "capacity_archive_citation_void"} - have)) + " 不存在），無法確認是否被引用")
    rows: list[tuple] = []
    if "capacity_archive_citation" in have:
        try:
            rows = conn.execute("SELECT c.id, c.version, c.refs_json, (SELECT 1 FROM capacity_archive_citation x WHERE x.supersedes_id = c.id) "
                                "FROM capacity_archive_citation c").fetchall()
        except sqlite3.Error as exc:
            return [], problems + [f"封存引用讀取失敗（{exc}），無法確認是否被引用"]
    voided: set = set()
    if "capacity_archive_citation_void" in have:
        try:
            voided = {r[0] for r in conn.execute("SELECT citation_id FROM capacity_archive_citation_void")}
        except sqlite3.Error as exc:
            problems.append(f"封存引用的作廢紀錄讀取失敗（{exc}）")
    out = [(r[0], r[1], r[2], "已作廢的引用" if r[0] in voided else ("被更正取代的舊版引用" if r[3] else "封存引用")) for r in rows]
    return out, problems


def _resolve_ref(conn: sqlite3.Connection, item: tuple) -> list[dict]:
    if item[0] == "id":
        q, a = "SELECT id, host, day, version, stage FROM capacity_day_archive WHERE id = ?", (item[1],)
    elif item[0] == "hostday":
        q, a = "SELECT id, host, day, version, stage FROM capacity_day_archive WHERE day = ? AND lower(host) = ? ORDER BY version", (item[1], item[2])
    else:
        q, a = "SELECT id, host, day, version, stage FROM capacity_day_archive WHERE day = ? ORDER BY host, version", (item[1],)
    return [{"id": r[0], "host": r[1], "day": r[2], "version": r[3], "stage": r[4]} for r in conn.execute(q, a)]


def _ref_text(item: tuple) -> str:
    return f"archive:{item[1]}" if item[0] == "id" else (f"{item[1]}|{item[2]}" if item[0] == "hostday" else item[1])


def _norm_refs(conn: sqlite3.Connection, refs: Any) -> list[str]:
    """引用的格式：archive:<封存編號>（指定某一版）、YYYY-MM-DD|主機（該主機該日整條版本鏈）、YYYY-MM-DD（該日全部主機）。
    看不懂的、或指到不存在的日摘要一律拒絕（不猜、不先存起來）。去重後保持輸入順序。"""
    if isinstance(refs, str):
        raw = refs
    elif isinstance(refs, list) and all(isinstance(x, str) for x in refs):
        raw = "\n".join(refs)
    else:
        raise ValueError("引用要是文字清單")
    ok, bad = _parse_refs(raw)
    if bad:
        raise ValueError(f"看不懂的引用：{bad[0][:60]}（格式：archive:編號、YYYY-MM-DD|主機、YYYY-MM-DD）")
    out: list[str] = []
    for item in ok:
        t = _ref_text(item)
        if t in out:
            continue
        if not _resolve_ref(conn, item):
            raise ValueError(f"找不到被引用的日摘要：{t}")
        out.append(t)
    if not out:
        raise ValueError("至少要引用一筆日摘要")
    if len(out) > CITE_REFS_MAX:
        raise ValueError(f"一次最多引用 {CITE_REFS_MAX} 筆")
    return out


def _cite_fields(conn: sqlite3.Connection, body: dict) -> dict:
    title = str(body.get("title") or "").strip()
    if not title or len(title) > CITE_TITLE_MAX:
        raise ValueError(f"引用名稱必填（例如報告或簽呈名稱），最多 {CITE_TITLE_MAX} 字")
    purpose = str(body.get("purpose") or "").strip()
    note = str(body.get("note") or "").strip()
    if len(purpose) > CITE_PURPOSE_MAX or len(note) > CITE_NOTE_MAX:
        raise ValueError(f"用途最多 {CITE_PURPOSE_MAX} 字、備註最多 {CITE_NOTE_MAX} 字")
    return {"title": title, "purpose": purpose, "note": note, "refs": _norm_refs(conn, body.get("refs"))}


def _cite_row(conn: sqlite3.Connection, cid: int):
    return conn.execute("SELECT id, root_id, version, supersedes_id, title, purpose, refs_json, note, created_by, created_at FROM capacity_archive_citation WHERE id = ?",
                        (int(cid),)).fetchone()


def citation_create(conn: sqlite3.Connection, body: dict, actor: str, now: datetime | None = None) -> dict:
    """新增一筆引用（第 1 版）。不 commit：由路由與操作紀錄一起 commit。"""
    ensure(conn)
    t = _now(now)
    if not _cite_tables(conn):
        raise ArchiveConflict("封存引用的資料表不完整，已暫停引用的寫入；請聯絡管理員檢查資料庫")
    with _lock(conn):
        f = _cite_fields(conn, body)
        cur = conn.execute("INSERT INTO capacity_archive_citation (root_id, version, supersedes_id, title, purpose, refs_json, note, created_by, created_at) "
                           "VALUES (NULL, 1, NULL, ?, ?, ?, ?, ?, ?)", (f["title"], f["purpose"], json.dumps(f["refs"], ensure_ascii=False), f["note"], actor, _ts(t)))
        cid = int(cur.lastrowid)
        _audit(conn, actor, "citation_create", {"id": cid, "version": 1, "title": f["title"], "refs": f["refs"]}, t)
    return citation_get(conn, cid)


def citation_revise(conn: sqlite3.Connection, cid: int, body: dict, actor: str, now: datetime | None = None) -> dict:
    """更正＝新增下一版（舊版原封不動）。只能更正最新且未作廢的那一版；同時有人更正時只會成功一個（supersedes_id 唯一）。"""
    ensure(conn)
    t = _now(now)
    if not _cite_tables(conn):
        raise ArchiveConflict("封存引用的資料表不完整，已暫停引用的寫入；請聯絡管理員檢查資料庫")
    with _lock(conn):
        r = _cite_row(conn, cid)
        if r is None:
            raise LookupError("找不到這筆引用")
        if conn.execute("SELECT 1 FROM capacity_archive_citation_void WHERE citation_id = ?", (int(cid),)).fetchone():
            raise ArchiveConflict("這一版已作廢，不能更正")
        if conn.execute("SELECT 1 FROM capacity_archive_citation WHERE supersedes_id = ?", (int(cid),)).fetchone():
            raise ArchiveConflict("這一版已被更正過，請更正最新的那一版")
        f = _cite_fields(conn, {**{"title": r[4], "purpose": r[5], "note": r[7], "refs": json.loads(r[6])}, **{k: v for k, v in body.items() if v is not None}})
        root = r[1] or r[0]
        try:
            cur = conn.execute("INSERT INTO capacity_archive_citation (root_id, version, supersedes_id, title, purpose, refs_json, note, created_by, created_at) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (root, r[2] + 1, int(cid), f["title"], f["purpose"], json.dumps(f["refs"], ensure_ascii=False),
                                                                     f["note"], actor, _ts(t)))
        except sqlite3.IntegrityError as exc:
            raise ArchiveConflict("這一版剛被別人更正，請重新整理") from exc
        new_id = int(cur.lastrowid)
        _audit(conn, actor, "citation_revise", {"id": new_id, "supersedes_id": int(cid), "root_id": root, "version": r[2] + 1,
                                                "before_refs": json.loads(r[6]), "after_refs": f["refs"], "title": f["title"]}, t)
    return citation_get(conn, new_id)


def citation_void(conn: sqlite3.Connection, cid: int, reason: str, actor: str, now: datetime | None = None) -> dict:
    """作廢最新那一版（另記一筆作廢紀錄，引用內容保留、繼續保護被引用的日摘要）。"""
    ensure(conn)
    t = _now(now)
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("作廢要寫原因")
    if len(reason) > CITE_VOID_REASON_MAX:
        raise ValueError(f"作廢原因最多 {CITE_VOID_REASON_MAX} 字（目前 {len(reason)} 字）")
    if not _cite_tables(conn):
        raise ArchiveConflict("封存引用的資料表不完整，已暫停引用的寫入；請聯絡管理員檢查資料庫")
    with _lock(conn):
        r = _cite_row(conn, cid)
        if r is None:
            raise LookupError("找不到這筆引用")
        if conn.execute("SELECT 1 FROM capacity_archive_citation WHERE supersedes_id = ?", (int(cid),)).fetchone():
            raise ArchiveConflict("這一版已被更正取代，請作廢最新的那一版")
        try:
            conn.execute("INSERT INTO capacity_archive_citation_void (citation_id, voided_at, voided_by, reason) VALUES (?,?,?,?)",
                         (int(cid), _ts(t), actor, reason))
        except sqlite3.IntegrityError as exc:
            raise ArchiveConflict("這一版已作廢") from exc
        _audit(conn, actor, "citation_void", {"id": int(cid), "root_id": r[1] or r[0], "version": r[2], "reason": reason}, t)
    return citation_get(conn, cid)


def _cite_dict(conn: sqlite3.Connection, r, superseded: set, voids: dict, latest: dict) -> dict:
    cid, root = r[0], r[1] or r[0]
    refs = json.loads(r[6])
    resolved = []
    for ref in refs:
        ok, _bad = _parse_refs(ref)
        rows = _resolve_ref(conn, ok[0]) if ok else []
        resolved.append({"ref": ref, "rows": len(rows), "archive_ids": [x["id"] for x in rows],
                         "label": "、".join(f"{x['host']} {x['day']} v{x['version']}" for x in rows[:5]) + ("…" if len(rows) > 5 else "")})
    v = voids.get(cid)
    status = "voided" if v else ("superseded" if cid in superseded else "active")
    return {"id": cid, "root_id": root, "chain": f"C{root}", "version": r[2], "supersedes_id": r[3], "title": r[4], "purpose": r[5], "note": r[7],
            # Codex 1007：ref_rows＝聯集（同一筆被兩個 selector 命中只算一次），跟清理保護同一個口徑
            "refs": refs, "resolved": resolved, "ref_rows": len({i for x in resolved for i in x["archive_ids"]}), "created_by": r[8], "created_at": r[9],
            "status": status, "status_label": CITE_STATUS_LABEL[status], "is_latest": latest.get(root) == cid,
            "voided_at": v[0] if v else None, "voided_by": v[1] if v else None, "void_reason": v[2] if v else None}


def citations(conn: sqlite3.Connection) -> dict:
    """全部引用（含舊版與作廢的）。唯讀：表不存在就回空（不建表）。"""
    if not _cite_tables(conn):
        return {"rows": [], "counts": {k: 0 for k in CITE_STATUS_LABEL}, "chains": 0}
    rows = conn.execute("SELECT id, root_id, version, supersedes_id, title, purpose, refs_json, note, created_by, created_at FROM capacity_archive_citation "
                        "ORDER BY COALESCE(root_id, id) DESC, version DESC").fetchall()
    superseded = {r[3] for r in rows if r[3] is not None}
    voids = {r[0]: (r[1], r[2], r[3]) for r in conn.execute("SELECT citation_id, voided_at, voided_by, reason FROM capacity_archive_citation_void")}
    ver = {r[0]: r[2] for r in rows}
    latest: dict[int, int] = {}
    for r in rows:
        root = r[1] or r[0]
        if root not in latest or r[2] > ver[latest[root]]:
            latest[root] = r[0]
    out = [_cite_dict(conn, r, superseded, voids, latest) for r in rows]
    counts = {k: sum(1 for x in out if x["status"] == k) for k in CITE_STATUS_LABEL}
    return {"rows": out, "counts": counts, "chains": len(latest)}


def citation_get(conn: sqlite3.Connection, cid: int) -> dict:
    data = citations(conn)
    one = next((x for x in data["rows"] if x["id"] == int(cid)), None)
    if one is None:
        raise LookupError("找不到這筆引用")
    chain = [dict(x) for x in data["rows"] if x["root_id"] == one["root_id"]]      # 複本：避免 one 自己出現在自己的 chain_versions 裡（循環參照）
    one = dict(one)
    one["chain_versions"] = chain
    return one
