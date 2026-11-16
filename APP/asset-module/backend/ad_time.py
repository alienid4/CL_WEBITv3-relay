"""AD 對時（2.060.x，使用者 11-10：「AD 自動」）：9.26 每天 w32tm /monitor 量每台 DC 的時間差，走中繼送來（source ad_time）。

只存觀測值、不判定好壞以外的事：每批一份（批次＋每台 DC 一列），保留歷史（稽核要能回看哪天哪台差多少）。
判定（給每日檢查用）：
  · 最新一批太舊（> STALE_HOURS）→ 整列「無資料」，不可以拿舊的當正常
  · 某台沒回應 → 那台「無資料」（原因照錄：多半是 UDP 123 被擋）
  · |offset| > WARN_SECONDS → 異常；否則正常
門檻放系統設定 ad_time_warn_seconds（預設 1 秒；Kerberos 容忍 5 分鐘，1 秒是提早發現漂移）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS ad_time_batch (
        batch_id TEXT PRIMARY KEY, collected_at TEXT NOT NULL, imported_at TEXT NOT NULL, dc_count INTEGER NOT NULL, ok_count INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS ad_time_dc (
        batch_id TEXT NOT NULL, name TEXT NOT NULL, ip TEXT, ok INTEGER NOT NULL, offset_s REAL, note TEXT,
        PRIMARY KEY (batch_id, name))""",
]
WARN_KEY = "ad_time_warn_seconds"
DEFAULT_WARN = 1.0
STALE_HOURS = 26


def ensure(conn: sqlite3.Connection) -> None:
    for s in SCHEMA:
        conn.execute(s)


def parse(data) -> list[dict]:
    """dcs.json → [{name, ip, ok, offset_s, note}]；格式不對丟 ValueError。"""
    if not isinstance(data, dict) or not isinstance(data.get("dcs"), list):
        raise ValueError("dcs.json 要有 dcs 清單")
    rows = data["dcs"]
    if not rows:
        raise ValueError("dcs 是空的（w32tm 沒列出任何 DC，不收）")
    if len(rows) > 200:
        raise ValueError("dcs 超過 200 台")
    out, seen = [], set()
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            raise ValueError(f"第 {i} 台格式不對")
        name = str(r.get("name") or "").strip()
        if not name or len(name) > 255:
            raise ValueError(f"第 {i} 台沒有名稱")
        if name.lower() in seen:
            raise ValueError(f"DC 名稱重複：{name}")
        seen.add(name.lower())
        ok = r.get("ok")
        if not isinstance(ok, bool):
            raise ValueError(f"{name}：ok 要是 true/false")
        off = r.get("offset_s")
        if ok:
            if isinstance(off, bool) or not isinstance(off, (int, float)):
                raise ValueError(f"{name}：有回應卻沒有 offset_s 數字")
            off = float(off)
        else:
            off = None
        out.append({"name": name, "ip": str(r.get("ip") or "")[:64], "ok": ok, "offset_s": off,
                    "note": str(r.get("note") or "")[:200]})
    return out


def apply(conn: sqlite3.Connection, batch_id: str, collected_at: str, dcs: list[dict], now_text: str) -> dict:
    """寫一批（呼叫端負責交易）。同一 batch_id 重送＝取代該批。"""
    ensure(conn)
    conn.execute("DELETE FROM ad_time_dc WHERE batch_id = ?", (batch_id,))
    conn.execute("INSERT OR REPLACE INTO ad_time_batch (batch_id, collected_at, imported_at, dc_count, ok_count) VALUES (?,?,?,?,?)",
                 (batch_id, collected_at, now_text, len(dcs), sum(1 for d in dcs if d["ok"])))
    conn.executemany("INSERT INTO ad_time_dc (batch_id, name, ip, ok, offset_s, note) VALUES (?,?,?,?,?,?)",
                     [(batch_id, d["name"], d["ip"], 1 if d["ok"] else 0, d["offset_s"], d["note"]) for d in dcs])
    return {"dcs": len(dcs), "answered": sum(1 for d in dcs if d["ok"]),
            "note": f"AD 對時：{len(dcs)} 台 DC，{sum(1 for d in dcs if d['ok'])} 台有回應"}


def _warn_seconds(conn) -> float:
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (WARN_KEY,)).fetchone()
        return float(r[0]) if r and r[0] not in (None, "") else DEFAULT_WARN
    except (sqlite3.OperationalError, ValueError):     # 沒有設定表或值不是數字＝用預設門檻（設定頁會顯示目前用的值）
        return DEFAULT_WARN


def latest(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    """給每日檢查與頁面：最新一批＋逐台判定。沒有資料表／沒有批次 → status=no_data（不建表、不寫入）。

    回 {"status": ok|warn|no_data, "reason", "collected_at", "warn_seconds", "dcs": [{name, ip, ok, offset_s, note, verdict}]}
    verdict：normal｜warn｜no_data。整列 status：任何一台 warn → warn；全部 normal → ok；其他（含任何一台 no_data、資料太舊）→ no_data。
    """
    warn_s = _warn_seconds(conn)
    base = {"warn_seconds": warn_s, "dcs": [], "collected_at": None}
    try:
        b = conn.execute("SELECT batch_id, collected_at FROM ad_time_batch ORDER BY collected_at DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:   # 表還不存在＝從未收到；照實回「無資料」，不建表
        return {**base, "status": "no_data", "reason": "從未收到 AD 對時資料"}
    if not b:
        return {**base, "status": "no_data", "reason": "從未收到 AD 對時資料"}
    now = now or datetime.now()
    col = _parse(b[1])
    rows = conn.execute("SELECT name, ip, ok, offset_s, note FROM ad_time_dc WHERE batch_id = ? ORDER BY name", (b[0],)).fetchall()
    dcs = []
    for name, ip, ok, off, note in rows:
        if not ok:
            v = "no_data"
        elif abs(off) > warn_s:
            v = "warn"
        else:
            v = "normal"
        dcs.append({"name": name, "ip": ip, "ok": bool(ok), "offset_s": off, "note": note, "verdict": v})
    res = {**base, "collected_at": b[1], "dcs": dcs}
    if col is None or now - col > timedelta(hours=STALE_HOURS):
        return {**res, "status": "no_data", "reason": f"最新一次是 {b[1]}，超過 {STALE_HOURS} 小時（太舊不算數）"}
    if any(d["verdict"] == "warn" for d in dcs):
        return {**res, "status": "warn", "reason": f"有 DC 時間差超過 {warn_s:g} 秒"}
    if any(d["verdict"] == "no_data" for d in dcs):
        return {**res, "status": "no_data", "reason": "有 DC 沒回應（多半是 UDP 123 被擋）"}
    return {**res, "status": "ok", "reason": ""}


def _parse(s: str) -> datetime | None:
    """collected_at（帶時區的 ISO）→ 台北當地的 naive datetime；解析不了回 None（呼叫端當太舊處理）。"""
    try:
        d = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    if d.tzinfo is not None:
        from datetime import timezone
        d = d.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    return d
