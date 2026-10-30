"""10.1 主機組月報（2.007.13）。

使用者 2026-10-27：「這些月報都是我們主機組的月報，也都是我想要在系統上產生的月報」「月報放在網頁上看就好，
最好直接剪下、複製我們的畫面就可以當月報」「效能資料我可以拿得到 NMON」。

一個月一份，畫面分成固定 16:9 的幾頁（截圖即可貼進月報）。資料來源：
  · 生命週期：8.3 生命週期同一份（routers.vulns.eos_summary 的作業系統分組），逐台；
  · 規模：2.1 主機查詢同一份（v2_hosts.machines），作業系統大類「虛擬化平台」依機房分；
  · 事件：4.1 事件應變（incident_event），當月開始的事件；
  · 效能：使用者上傳的 NMON 檔（AIX／Linux），CPU 忙碌％＝CPU_ALL 的 User%＋Sys%；
  · 管理判斷（風險追蹤、未來 3–6 個月、關注事項、整體判定）：人填，存在 host_monthly_note，
    當月還沒填就先帶上個月的內容（畫面標「沿用上月」），只改有變動的。
VM（ESXi）CPU 趨勢沒有資料來源（NMON 只有 AIX／Linux），畫面寫明「待 vROps 匯出檔」，不編數字。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

THRESHOLD = 80.0      # CPU 警戒（使用者月報沿用的既有基準）

DDL = [
    """CREATE TABLE IF NOT EXISTS host_monthly_note (
        month TEXT PRIMARY KEY, data TEXT NOT NULL, updated_by TEXT, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS nmon_file (
        id INTEGER PRIMARY KEY AUTOINCREMENT, host TEXT NOT NULL, filename TEXT, first_ts TEXT, last_ts TEXT,
        samples INTEGER NOT NULL, uploaded_by TEXT, uploaded_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS nmon_sample (
        host TEXT NOT NULL, ts TEXT NOT NULL, cpu REAL NOT NULL, PRIMARY KEY (host, ts))""",
]

#: 管理判斷欄位（畫面可編輯）；表格類是 list of dict
NOTE_DEFAULT: dict[str, Any] = {
    "verdict": "", "verdict_note": "",
    "attention": [{"k": "風險 vs 上月", "v": ""}, {"k": "最擔心三件事", "v": ""}, {"k": "交易量 1.5～2 倍", "v": ""},
                  {"k": "上月改善", "v": ""}, {"k": "需主管協助", "v": ""}],
    "actions": [],       # {risk, platform, status, due, next}
    "upcoming": [],      # {item, when, purpose, status}
    "vm_perf": [],       # {platform, env, usage, over80, headroom, risk}（VM 沒有資料來源，人填）
    "priorities": [],    # 下月資料補強優先順序（字串）
}
STATUS = ("Completed", "On Track", "Delay")


def ensure(conn: sqlite3.Connection) -> None:
    for s in DDL:
        conn.execute(s)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _month(m: str | None) -> str:
    if m and re.fullmatch(r"\d{4}-\d{2}", m):
        return m
    t = datetime.now()
    y, mo = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)   # 預設上個月（月報寫的是上個月）
    return f"{y}-{mo:02d}"


def _prev(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:])
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


# ===== NMON =====

_MON = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def parse_nmon(text: str) -> dict:
    """NMON 檔 → {host, samples: [(ts, cpu)]}。只讀 AAA,host、ZZZZ（時間）、CPU_ALL（User%＋Sys%）。
    格式不對（不是 NMON、沒有 CPU_ALL）丟 ValueError，整份不存。"""
    host, zz, cpu = None, {}, {}
    for line in text.splitlines():
        p = line.strip().split(",")
        if len(p) < 3:
            continue
        if p[0] == "AAA" and p[1] == "host":
            host = p[2].strip()
        elif p[0] == "ZZZZ" and len(p) >= 4:
            d = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", p[3].strip())
            if d and p[2].strip():
                mo = _MON.get(d.group(2).upper())
                if mo:
                    zz[p[1]] = f"{d.group(3)}-{mo:02d}-{int(d.group(1)):02d} {p[2].strip()[:8]}"
        elif p[0] == "CPU_ALL" and p[1].startswith("T") and len(p) >= 4:
            try:
                cpu[p[1]] = round(float(p[2]) + float(p[3]), 1)
            except ValueError:   # 表頭列（User%、Sys%）不是數字
                continue
    if not host:
        raise ValueError("不是 NMON 檔：找不到 AAA,host（主機名稱）")
    samples = sorted((zz[t], c) for t, c in cpu.items() if t in zz)
    if not samples:
        raise ValueError(f"{host}：找不到 CPU_ALL 與時間（ZZZZ）資料，無法算 CPU")
    return {"host": host, "samples": samples}


def import_nmon(conn: sqlite3.Connection, data: bytes, filename: str, by: str) -> dict:
    """匯入一個 NMON 檔（純文字／.gz／AIX .Z）。2.007.19 起整份指標都讀（nmon_series），
    CPU（user＋sys）另外照舊存 nmon_sample（月報 CPU 表用）；原始檔另存 nmon_raw/ 給下載。"""
    import nmon_series

    ensure(conn)
    nmon_series.ensure(conn)
    p = nmon_series.parse(nmon_series.to_text(data))
    cpu = [(f"{day} {t}", r["cpu_busy"]) for day, d in p["days"].items() for t, r in d["rows"].items() if "cpu_busy" in r]
    if not cpu:
        raise ValueError(f"{p['host']}：找不到 CPU_ALL 與時間（ZZZZ）資料，無法算 CPU")
    cpu.sort()
    raw_path = nmon_series.save_raw(conn, p["host"], filename, data)        # 先寫原始檔：寫不進去就整份不收，不會圖有了、月報表沒有
    try:
        st = nmon_series.store(conn, p)
        conn.executemany("INSERT OR REPLACE INTO nmon_sample (host, ts, cpu) VALUES (?,?,?)", [(p["host"], ts, c) for ts, c in cpu])
        conn.execute("INSERT INTO nmon_file (host, filename, first_ts, last_ts, samples, uploaded_by, uploaded_at, raw_path) VALUES (?,?,?,?,?,?,?,?)",
                     (p["host"], filename, cpu[0][0], cpu[-1][0], len(cpu), by, _now(), raw_path))
        conn.commit()
    except Exception:
        (nmon_series.raw_dir(conn) / raw_path).unlink(missing_ok=True)
        raise
    return {"host": p["host"], "samples": len(cpu), "from": cpu[0][0], "to": cpu[-1][0],
            "metrics": p["found"], "interval_s": p["interval_s"], "notes": p["notes"], "days": st["days"]}


def _gap_minutes(a: str, b: str) -> float:
    return (datetime.strptime(b, "%Y-%m-%d %H:%M:%S") - datetime.strptime(a, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60


def nmon_month(conn: sqlite3.Connection, month: str) -> list[dict]:
    """每台：樣本數、平均、尖峰、超過 80% 的樣本數、最長連續超標（分鐘）、每天的平均與尖峰（畫趨勢用）。"""
    ensure(conn)
    out = []
    by: dict[str, list] = {}
    for r in conn.execute("SELECT host, ts, cpu FROM nmon_sample WHERE ts LIKE ? ORDER BY host, ts", (f"{month}%",)):
        by.setdefault(r[0], []).append((r[1], r[2]))
    for host, ss in sorted(by.items()):
        cpus = [c for _, c in ss]
        longest, run_start, prev = 0.0, None, None
        step = _gap_minutes(ss[0][0], ss[1][0]) if len(ss) > 1 else 0
        for ts, c in ss:
            if c > THRESHOLD:
                if run_start is None or (prev and _gap_minutes(prev, ts) > step * 2 + 1):   # 中間缺樣本超過兩格＝斷開
                    run_start = ts
                longest = max(longest, _gap_minutes(run_start, ts) + step)
                prev = ts
            else:
                run_start, prev = None, None
        days: dict[str, list] = {}
        for ts, c in ss:
            days.setdefault(ts[:10], []).append(c)
        out.append({"host": host, "samples": len(ss), "from": ss[0][0], "to": ss[-1][0],
                    "avg": round(sum(cpus) / len(cpus), 1), "peak": max(cpus),
                    "over": sum(1 for c in cpus if c > THRESHOLD), "longest_over_min": round(longest),
                    "days": [{"date": d, "avg": round(sum(v) / len(v), 1), "peak": max(v)} for d, v in sorted(days.items())]})
    return out


# ===== 管理判斷（人填）=====

def get_note(conn: sqlite3.Connection, month: str) -> dict:
    ensure(conn)
    r = conn.execute("SELECT data, updated_by, updated_at FROM host_monthly_note WHERE month = ?", (month,)).fetchone()
    if r:
        return {**NOTE_DEFAULT, **json.loads(r[0]), "_inherited": None, "_by": r[1], "_at": r[2]}
    p = conn.execute("SELECT month, data FROM host_monthly_note WHERE month < ? ORDER BY month DESC LIMIT 1", (month,)).fetchone()
    if p:
        return {**NOTE_DEFAULT, **json.loads(p[1]), "_inherited": p[0], "_by": None, "_at": None}
    return {**NOTE_DEFAULT, "_inherited": None, "_by": None, "_at": None}


def save_note(conn: sqlite3.Connection, month: str, data: dict, by: str) -> dict:
    ensure(conn)
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        raise ValueError("月份格式要是 YYYY-MM")
    clean = {k: data.get(k, v) for k, v in NOTE_DEFAULT.items()}
    for k in ("actions", "upcoming", "vm_perf", "attention", "priorities"):
        if not isinstance(clean[k], list):
            raise ValueError(f"{k} 要是清單")
    conn.execute("INSERT OR REPLACE INTO host_monthly_note (month, data, updated_by, updated_at) VALUES (?,?,?,?)",
                 (month, json.dumps(clean, ensure_ascii=False), by, _now()))
    conn.commit()
    return get_note(conn, month)


# ===== 自動算的部分 =====

def lifecycle(conn: sqlite3.Connection) -> dict:
    """8.3 生命週期「作業系統」同一份：依平台（family）逐台加總 已 EOS／一年內／需確認（查不到日期）／支援中。"""
    from routers.vulns import eos_summary

    s = eos_summary(session=None, conn=conn)
    fam: dict[str, dict] = {}
    for it in (s.get("host_os") or {}).get("items", []):
        f = it.get("family") or "其他"
        b = fam.setdefault(f, {"platform": f, "total": 0, "expired": 0, "upcoming": 0, "unknown": 0, "ok": 0})
        b["total"] += it["count"]
        b[it["status"] if it["status"] in ("expired", "upcoming", "unknown", "ok") else "unknown"] += it["count"]
    rows = sorted(fam.values(), key=lambda b: -b["total"])
    # 合計用 8.3 磚塊同一份（逐台）：同一台登記了兩種版本時，平台列會各算一次，合計不重複算
    bs = (s.get("host_os") or {}).get("by_status") or {}
    tot = {k: int(bs.get(k, 0)) for k in ("expired", "upcoming", "unknown", "ok")}
    tot["total"] = sum(tot.values())
    return {"rows": rows, "total": tot, "row_dup": sum(r["total"] for r in rows) - tot["total"]}


def scale(conn: sqlite3.Connection) -> dict:
    """虛擬化平台（ESXi／vCenter）依機房，2.1 主機查詢同一份（逐台）。"""
    import v2_hosts

    rooms: dict[str, int] = {}
    for m in v2_hosts.machines(conn):
        if (m["badge"] or {}).get("os_class") == "虛擬化平台":
            k = m["room"] or m["site"] or "未填"
            rooms[k] = rooms.get(k, 0) + 1
    return {"virt_total": sum(rooms.values()), "virt_by_room": [{"room": k, "n": v} for k, v in sorted(rooms.items(), key=lambda x: -x[1])]}


def incidents(conn: sqlite3.Connection, month: str) -> list[dict]:
    try:
        import incident
        mcol = "mode" if incident._has_mode(conn) else "NULL AS mode"   # 舊庫沒有 mode 欄位也要能讀
        rows = conn.execute(f"SELECT id, type, title, started_at, status, closed_at, summary, {mcol} FROM incident_event "
                            "WHERE started_at LIKE ? ORDER BY started_at", (f"{month}%",)).fetchall()
    except sqlite3.OperationalError:   # 還沒開過事件應變（表不存在）＝當月沒有事件紀錄
        return []
    out = []
    for r in rows:
        dur = None
        if r["closed_at"]:
            try:
                dur = round(_gap_minutes(r["started_at"][:19].replace("T", " "), r["closed_at"][:19].replace("T", " ")) / 60, 1)
            except ValueError:   # 時間格式不是 YYYY-MM-DD HH:MM:SS：不算處理時間
                dur = None
        out.append({"id": r["id"], "type": r["type"], "title": r["title"], "started_at": r["started_at"][:16],
                    "status": "已結案" if r["status"] == "closed" else "處理中", "closed_at": (r["closed_at"] or "")[:16],
                    "hours": dur, "summary": r["summary"] or "", "mode": r["mode"], "mode_label": incident.mode_label(r["mode"])})
    return out


def report(conn: sqlite3.Connection, month: str | None = None) -> dict:
    import nmon_series

    m = _month(month)
    ensure(conn)
    nmon_series.ensure(conn)
    months = [r[0] for r in conn.execute(
        "SELECT month FROM host_monthly_note UNION SELECT DISTINCT substr(ts, 1, 7) FROM nmon_sample ORDER BY 1 DESC")]
    return {"month": m, "prev": _prev(m), "months": months, "threshold": THRESHOLD, "status_options": list(STATUS),
            "lifecycle": lifecycle(conn), "scale": scale(conn), "incidents": incidents(conn, m),
            "nmon": nmon_month(conn, m), "nmon_perf": nmon_series.hosts(conn, m), "note": get_note(conn, m),
            "nmon_files": [dict(r) for r in conn.execute(
                "SELECT host, filename, first_ts, last_ts, samples, uploaded_by, uploaded_at, (raw_path IS NOT NULL) AS has_raw FROM nmon_file "
                "WHERE substr(first_ts, 1, 7) <= ? AND substr(last_ts, 1, 7) >= ? ORDER BY uploaded_at DESC", (m, m))],
            "generated_at": _now()}
