"""WhatsUp 記憶體／SWAP → Teams（2.060.x，使用者 11-04 新增；預設關閉，Teams 磁碟告警設定頁旁邊的開關打開才送）。

- 記憶體 ≥ 門檻（預設 90%）：capacity_whatsup_perf 的 mem_used，**前一日**（台北時間）每台每個時間桶最高值（同容量分析／每日檢查的口徑）。
- SWAP ≥ 門檻（預設 50%）：relay_disk_util 裡名稱像 swap 的那幾顆（daily_check._is_swap，同一份判斷），前一日最高值；同一行帶同一台的記憶體最高值。
- 掛在 WhatsUp 效能／磁碟中繼匯入成功之後（relay_import，跟 disk_alert.after_import 同一個 try/except）。
- 去重：同一天、同一種只送一次（mem_alert_sent，PRIMARY KEY (day, kind)）。
- 格式：notice_fmt（第一行燈號＋級別＋什麼事＋台數，一台一行最多 5 行＋「另有 N 台」，結尾「開啟系統」）。
讀取函式不建表；寫入路徑才 ensure。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta

log = logging.getLogger("mem_alert")
SETTING_KEY = "teams_mem_alert"
DEFAULTS = {"mem_enabled": False, "mem_threshold": 90.0, "swap_enabled": False, "swap_threshold": 50.0}
SQL = ["""CREATE TABLE IF NOT EXISTS mem_alert_sent (
    day TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL, batch_id TEXT, threshold REAL, hosts INTEGER NOT NULL,
    status TEXT NOT NULL, delivery_key TEXT, summary TEXT, items_json TEXT, PRIMARY KEY (day, kind))"""]


def ensure(conn) -> None:
    for s in SQL:
        conn.execute(s)


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def load_settings(conn) -> dict:
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    cur = {}
    if r and r[0]:
        try:
            cur = json.loads(r[0])
        except ValueError:
            cur = {}                       # 壞掉＝照預設（預設是關閉，不會亂送）
    return {**DEFAULTS, **{k: v for k, v in cur.items() if k in DEFAULTS}}


def save_settings(conn, cfg: dict) -> dict:
    out = load_settings(conn)
    for k, v in (cfg or {}).items():
        if k not in DEFAULTS or v is None:
            continue
        if k.endswith("_enabled"):
            out[k] = bool(v)
        else:
            v = float(v)
            if not 1 <= v <= 100:
                raise ValueError(f"{'記憶體' if k.startswith('mem') else 'SWAP'}門檻要在 1～100")
            out[k] = v
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (SETTING_KEY, json.dumps(out, ensure_ascii=False)))
    return out


def _mem_peaks(conn, day: str) -> dict[str, float]:
    """前一日每台記憶體最高值（capacity_whatsup_perf，容量分析同一份序列）。"""
    import capacity_analysis as ca
    import daily_check as dc
    if not _has(conn, "capacity_whatsup_perf"):
        return {}
    out = {}
    for r in conn.execute("SELECT host, data FROM capacity_whatsup_perf WHERE day = ?", (day,)):
        try:
            pk = dc._peak(ca._decode(r["data"]), "mem_used")
        except ValueError:
            continue                        # 壞掉的資料列：當這台沒資料（不猜）
        if pk is not None:
            out[r["host"]] = pk
    return out


def _swap_peaks(conn, day: str) -> dict[str, tuple[str, float]]:
    """前一日每台 SWAP 最高值（relay_disk_util 名稱像 swap 的那顆；同一台多顆取最高）。"""
    import daily_check as dc
    import disk_alert
    if not _has(conn, "relay_disk_util"):
        return {}
    out: dict[str, tuple[str, float]] = {}
    for r in conn.execute("SELECT host, disk, data FROM relay_disk_util WHERE day = ?", (day,)):
        if not dc._is_swap(r["disk"]):
            continue
        pk = disk_alert.day_peak(r["data"])
        if pk is not None and (r["host"] not in out or pk > out[r["host"]][1]):
            out[r["host"]] = (r["disk"], round(pk, 1))
    return out


def build(conn, day: str, cfg: dict) -> list[tuple[str, str, float, list[str], int, list[dict]]]:
    """回 [(kind, 第一行, 門檻, 行（已截 5 行）, 台數, 明細)]。沒超過的種類不回。明細每筆帶 line（未截的那一行），補充通知用。"""
    import disk_alert
    import notice_fmt as nf
    mem = _mem_peaks(conn, day)
    out = []
    if cfg["mem_enabled"]:
        th = float(cfg["mem_threshold"])
        hot = sorted(((h, v) for h, v in mem.items() if v >= th), key=lambda x: (-x[1], x[0]))
        if hot:
            full = [f"{disk_alert.host_label(conn, h)}｜記憶體最高 {v:g}%｜{day}｜{disk_alert.contact_text(conn, h)}" for h, v in hot]
            items = [{"host": h, "mem": v, "line": ln} for (h, v), ln in zip(hot, full)]
            lines = nf.cap(full)
            sev = "critical" if any(v >= 95 for _, v in hot) else "warning"
            out.append(("mem", nf.head(nf.EMOJI[sev], "記憶體", f"記憶體使用率 ≥{th:g}%（{day}）", len(hot)), th, lines, len(hot), items))
    if cfg["swap_enabled"]:
        th = float(cfg["swap_threshold"])
        sw = _swap_peaks(conn, day)
        hot = sorted(((h, d, v) for h, (d, v) in sw.items() if v >= th), key=lambda x: (-x[2], x[0]))
        if hot:
            full = [f"{disk_alert.host_label(conn, h)}｜SWAP {v:g}%（{d}）｜記憶體最高 "
                    f"{(str(mem[h]) + '%') if h in mem else '無資料'}｜{disk_alert.contact_text(conn, h)}" for h, d, v in hot]
            items = [{"host": h, "disk": d, "swap": v, "mem": mem.get(h), "line": ln} for (h, d, v), ln in zip(hot, full)]
            lines = nf.cap(full)
            out.append(("swap", nf.head(nf.EMOJI["warning"], "SWAP", f"SWAP 使用率 ≥{th:g}%（{day}）", len(hot)), th, lines, len(hot), items))
    return out


def evaluate(conn, batch_id: str, now: datetime | None = None) -> dict:
    """前一日（台北）的記憶體／SWAP。兩個開關都關＝不動作。同一天同一種只送一次。自己 commit。"""
    import notice_fmt as nf
    import teams_notify as tn
    cfg = load_settings(conn)
    if not (cfg["mem_enabled"] or cfg["swap_enabled"]):
        return {"status": "disabled"}
    now = now or tn.now_taipei()
    day = (now.date() - timedelta(days=1)).isoformat()
    ensure(conn)
    try:
        teams_on = tn.load_settings(conn)["enabled"]
    except tn.SettingsCorrupt:
        teams_on = False
    link = nf.link_of(conn, "/v2/hosts")
    sent = []
    for kind, first, th, lines, hosts, items in build(conn, day, cfg):
        prev = conn.execute("SELECT items_json FROM mem_alert_sent WHERE day = ? AND kind = ?", (day, kind)).fetchone()
        if prev:
            # 2.060.16（審查 P2）：同一天第一批效能資料不完整、後到的批次才出現超標主機時，補發一則「補充」只列新增的主機；
            # 每天每種最多補一次（kind 加「+」記一列，主鍵仍是 (day, kind)），不會一直洗版。
            fkind = kind + "+"
            if conn.execute("SELECT 1 FROM mem_alert_sent WHERE day = ? AND kind = ?", (day, fkind)).fetchone():
                continue
            try:
                seen = {it.get("host") for it in json.loads(prev["items_json"] or "[]")}
            except ValueError:      # 舊紀錄壞掉＝不補發（寧可少發一則，也不重發整批）
                continue
            new = [it for it in items if it["host"] not in seen]
            if not new:
                continue
            first = first.rsplit("：", 1)[0] + f"（補充：後到的資料）：{len(new)} 台"
            kind, items, hosts, lines = fkind, new, len(new), nf.cap([it["line"] for it in new])
        key = None
        if teams_on:
            tn.ensure(conn)
            key = f"mem:{day}:{kind}"
            tn._enqueue(conn, key, f"{kind.rstrip('+')}_alert", kind, "warning", first, nf.card(first, lines, link), now)
        conn.execute("INSERT INTO mem_alert_sent (day, kind, at, batch_id, threshold, hosts, status, delivery_key, summary, items_json) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)", (day, kind, tn._ts(now), batch_id, th, hosts, "queued" if teams_on else "recorded",
                                                      key, "\n".join([first, *lines]), json.dumps(items, ensure_ascii=False)))
        sent.append({"kind": kind, "hosts": hosts, "key": key})
    conn.commit()
    return {"status": "ok", "day": day, "sent": sent}


def after_import(conn, result: dict) -> None:
    """relay_import 匯入成功（已 commit）後的掛勾：只處理 WhatsUp。錯誤只記日誌，不影響匯入結果。"""
    if result.get("status") != "imported" or result.get("source") != "whatsup":
        return
    try:
        evaluate(conn, result["batch_id"])
    except Exception as exc:  # noqa: BLE001 - 告警評估失敗不可以讓匯入變失敗；記日誌（不含資料內容）
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        log.error("WhatsUp 記憶體／SWAP 告警評估失敗（批次 %s）：%s", result.get("batch_id"), type(exc).__name__)


def history(conn, limit: int = 100) -> list[dict]:
    if not _has(conn, "mem_alert_sent"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM mem_alert_sent ORDER BY at DESC LIMIT ?", (limit,))]
