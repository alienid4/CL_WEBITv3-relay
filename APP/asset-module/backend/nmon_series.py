"""NMON 完整時間序列（10.1 月報「效能圖表」，2.007.19）。

使用者 2026-10-28（NB 交辦）：「這個顆粒太粗了吧」「nmon 只有 cpu 不夠」「要給連結」。
host_monthly 原本只存 CPU（user＋sys）一條線、畫面只畫每天一點。這裡把 NMON 檔裡的各項指標整份讀進來：

  CPU（user／sys／wait／idle／busy）、排隊數（PROC Runnable）、記憶體使用率、換頁（pgin／pgout／pgsin／pgsout）、
  每張網卡 KB/s 與錯誤數、每顆磁碟 busy％／讀寫 KB/s、各檔案系統使用率、每個時間點最吃 CPU 的程式（TOP）。

格式（只有 AIX 用 NMON，真檔驗證；Linux 不用 NMON，下面 Linux 欄位判斷只是順手容錯，不對外宣稱支援）：
  區段表頭列：`SECTION,說明,欄名1,欄名2…`；資料列：`SECTION,T0001,值1,值2…`；`ZZZZ,T0001,HH:MM:SS,DD-MON-YYYY` 給時間。
  欄位靠表頭的名字對，不靠位置（AIX 與 Linux 的欄位順序不同）。

儲存：每台每天一列（nmon_series），內容是 gzip 的 JSON（時間軸＋每個指標一條陣列）。
  使用者實際只有 2 台 AIX、各 30 天，每分鐘一點全部留（一台一天壓縮後約 0.1～0.2 MB）。同一台同一天再匯入＝依時間點覆蓋、不重複。
  原始 NMON 檔另存在資料庫旁的 nmon_raw/（不進資料庫，備份才不會膨脹），給「下載原始 NMON」。
上傳可以是純文字、.gz、AIX 的 .Z（compress）。

沒收集的指標**不能顯示 0**：整個檔裡沒有這個區段／欄位，summary 就標「未收集」。
"""
from __future__ import annotations

import gzip
import json
import math
import re
import sqlite3
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 門檻（畫面也會寫出來）
THRESHOLDS = {
    "cpu_busy": 80,      # CPU 忙碌 ＝ user＋sys，跟月報原本的 80% 同一把尺
    "mem_used": 90,
    "swap_used": 50,     # 換頁空間使用率
    "fs": 80,            # 檔案系統使用率（另外標 90）
    "fs_crit": 90,
    "disk_busy": 80,
}
MAX_DISKS = 24
MAX_TEXT_BYTES = 64 * 1024 * 1024      # 解壓後上限（防壓縮炸彈）；一天一台的 NMON 約 4 MB，一個月合併檔也遠小於這個數
TOP_PER_SNAPSHOT = 8
TOP_MIN_CPU = 0.0          # NMON 本身只記「比較吃 CPU 的幾個程式」，閒置主機也常常都 < 1%，不再自己篩

_MON = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}

SERIES_SQL = """
CREATE TABLE IF NOT EXISTS nmon_series (
    host TEXT NOT NULL,
    day TEXT NOT NULL,             -- YYYY-MM-DD
    interval_s INTEGER,
    samples INTEGER,
    data BLOB NOT NULL,            -- gzip JSON：{"ts":[...], "cols":{key:[...]}, "top":{ts:[[pid,cpu,cmd],...]}}
    updated_at TEXT,
    PRIMARY KEY (host, day)
)
"""


def ensure(conn: sqlite3.Connection) -> None:
    conn.execute(SERIES_SQL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(nmon_file)")}
    if cols and "raw_path" not in cols:
        conn.execute("ALTER TABLE nmon_file ADD COLUMN raw_path TEXT")
    conn.commit()


# ---------------------------------------------------------------- 解壓（.gz／AIX .Z／純文字）
def lzw_decompress(data: bytes) -> bytes:
    """UNIX `compress` 的 .Z（AIX 的 nmon 常這樣壓）。標準 LZW：9～maxbits 位元可變寬度、block mode 的 CLEAR(256)。
    寫碼是「一組 8 個碼＝n 位元組」；碼寬改變或 CLEAR 時，目前這組補滿再開新的一組——
    所以對齊是相對於「上一次換寬度／CLEAR 的位置」，不是相對於檔頭（用真檔逐位元組比對驗證過）。"""
    if data[:2] != bytes([0x1F, 0x9D]):
        raise ValueError("不是 .Z 檔")
    flags = data[2]
    maxbits = flags & 0x1F
    block = bool(flags & 0x80)
    if not 9 <= maxbits <= 16:
        raise ValueError("不支援的 .Z 位元寬度")
    buf = data[3:]
    nbits_total = len(buf) * 8
    n = 9
    maxcode = (1 << n) - 1
    free = 257 if block else 256
    table: list[bytes] = [bytes([i]) for i in range(256)] + [b""] * ((1 << maxbits) - 256)
    out = bytearray()
    pos = 0
    base = 0                      # 目前這一段（同一個碼寬）的起點；8 個碼一組從這裡算
    prev = b""
    first = True

    def realign(p: int, width: int) -> int:
        blk = width * 8
        return base + ((p - base + blk - 1) // blk) * blk

    while pos + n <= nbits_total:
        byte = pos >> 3
        chunk = int.from_bytes(buf[byte:byte + 4], "little")
        code = (chunk >> (pos & 7)) & maxcode
        pos += n
        if first:
            out += table[code]
            prev = table[code]
            first = False
            continue
        if block and code == 256:        # CLEAR：丟掉字典，碼寬回 9，這一組補滿後從新的一組開始
            pos = realign(pos, n)
            base = pos
            n = 9
            maxcode = (1 << n) - 1
            free = 257
            first = True
            continue
        if code < free:
            entry = table[code]
        elif code == free:
            entry = prev + prev[:1]
        else:
            raise ValueError("壞掉的 .Z 檔")
        out += entry
        if len(out) > MAX_TEXT_BYTES:
            raise ValueError("解壓後超過大小上限，不處理")
        if free <= (1 << maxbits) - 1:
            table[free] = prev + entry[:1]
            free += 1
            if free > maxcode and n < maxbits:
                pos = realign(pos, n)
                base = pos
                n += 1
                maxcode = (1 << n) - 1
        prev = entry
    return bytes(out)


def to_text(data: bytes) -> str:
    """純文字／.gz／.Z 都收；回解碼後的文字。"""
    if data[:2] == b"\x1f\x8b":
        import zlib
        out, rest = [], data
        total = 0
        try:
            while rest[:2] == b"\x1f\x8b":                 # gzip 可以是好幾段接起來，每段都要解
                d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                chunk = d.decompress(rest, MAX_TEXT_BYTES + 1 - total)
                total += len(chunk)
                if total > MAX_TEXT_BYTES:
                    raise ValueError("解壓後超過大小上限，不處理")
                out.append(chunk)
                rest = d.unused_data
        except zlib.error as exc:
            raise ValueError(f"gzip 檔案損壞：{exc}") from exc
        data = b"".join(out)
    elif data[:2] == b"\x1f\x9d":
        data = lzw_decompress(data)
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 解析
def _num(s: str):
    try:
        x = float(s)
    except ValueError:
        return None
    return round(x, 2) if math.isfinite(x) else None          # nan／inf 不收（會毒掉平均與尖峰，也讓 JSON 輸出壞掉）


def _ts_of(zz: dict, t: str) -> tuple[str, str] | None:
    return zz.get(t)


def parse(text: str) -> dict:
    """NMON 文字 → {host, interval_s, days: {day: {"ts": [...], "cols": {key: [...]}, "top": {ts: [...]}}}, found: [區段名]}。
    格式不對（不是 NMON、沒有任何時間）丟 ValueError，整份不存。"""
    host, interval = None, None
    zz: dict[str, tuple[str, str]] = {}           # T0001 → (day, HH:MM:SS)
    lines = text.splitlines()
    for line in lines:
        if line.startswith("AAA,host,"):
            host = line.split(",")[2].strip()
        elif line.startswith("AAA,interval,"):
            try:
                interval = int(line.split(",")[2])
            except ValueError:   # 沒寫間隔就不寫（畫面不顯示間隔，不猜）
                interval = None
        elif line.startswith("ZZZZ,"):
            p = line.strip().split(",")
            if len(p) >= 4:
                d = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", p[3].strip())
                if d and _MON.get(d.group(2).upper()) and p[2].strip():
                    zz[p[1]] = (f"{d.group(3)}-{_MON[d.group(2).upper()]:02d}-{int(d.group(1)):02d}", p[2].strip()[:8])
    if not host:
        raise ValueError("不是 NMON 檔：找不到 AAA,host（主機名稱）")
    if not zz:
        raise ValueError(f"{host}：找不到時間資料（ZZZZ），無法讀")

    headers: dict[str, list[str]] = {}
    rows: dict[tuple[str, str], dict[str, float]] = {}      # (day, hh:mm:ss) → {key: value}
    top: dict[tuple[str, str], list] = {}
    found: set[str] = set()

    def put(t: str, key: str, v):
        z = zz.get(t)
        if z is None or v is None:
            return
        rows.setdefault(z, {})[key] = v

    def col(sec: str, vals: list[str], name: str):
        h = [x.lower() for x in (headers.get(sec) or [])]      # 欄名大小寫在 AIX／Linux 不一致（Real Free %／Real free %）
        try:
            return _num(vals[h.index(name.lower())])
        except (ValueError, IndexError):
            return None

    for line in lines:
        if not line or line[0] in "AB" and (line.startswith("AAA,") or line.startswith("BBB")):
            continue
        p = line.rstrip("\r").split(",")
        sec = p[0]
        if len(p) < 3 or sec in ("ZZZZ", "UARG"):
            continue
        if sec == "TOP":
            # 表頭：TOP,+PID,Time,%CPU,…；資料：TOP,<pid>,<Txxxx>,<%CPU>,…,<Command>
            if p[1] == "+PID":
                headers["TOP"] = p[1:]
            elif re.fullmatch(r"T\d+", p[2] if len(p) > 2 else "") and "TOP" in headers:
                h = headers["TOP"]
                try:
                    cpu = float(p[2 + h.index("%CPU") - 1])
                    cmd = p[2 + h.index("Command") - 1]
                    z = zz.get(p[2])
                    if z and cpu >= TOP_MIN_CPU:
                        top.setdefault(z, []).append([p[1], round(cpu, 1), cmd])
                        found.add("TOP")
                except (ValueError, IndexError):
                    pass
            continue
        if not re.fullmatch(r"T\d+", p[1]):
            headers[sec] = [c.strip() for c in p[2:]]       # 表頭列
            continue
        v = p[2:]
        t = p[1]
        if sec == "CPU_ALL":
            u, s_, w, i = (col(sec, v, "User%"), col(sec, v, "Sys%"), col(sec, v, "Wait%"), col(sec, v, "Idle%"))
            if u is not None and s_ is not None:
                put(t, "cpu_user", u); put(t, "cpu_sys", s_); put(t, "cpu_busy", round(u + s_, 2))
                if w is not None: put(t, "cpu_wait", w)
                if i is not None: put(t, "cpu_idle", i)
                found.add("CPU_ALL")
        elif sec == "PROC":
            r = col(sec, v, "Runnable")
            if r is not None:
                put(t, "runq", r); found.add("PROC")
        elif sec == "MEM":
            h = [x.lower() for x in (headers.get("MEM") or [])]
            if "real free %" in h:                                  # AIX
                rf, vf = col(sec, v, "Real free %"), col(sec, v, "Virtual free %")
                if rf is not None: put(t, "mem_used", round(100 - rf, 2))
                if vf is not None: put(t, "swap_used", round(100 - vf, 2))
                found.add("MEM")
            elif "memtotal" in h:                                   # Linux（MB）
                tot, fr = col(sec, v, "memtotal"), col(sec, v, "memfree")
                ca, bu = col(sec, v, "cached") or 0, col(sec, v, "buffers") or 0
                st, sf = col(sec, v, "swaptotal"), col(sec, v, "swapfree")
                if tot and fr is not None: put(t, "mem_used", round(100 * (tot - fr - ca - bu) / tot, 2))
                if st and sf is not None: put(t, "swap_used", round(100 * (st - sf) / st, 2))
                found.add("MEM")
        elif sec in ("PAGE", "VM"):
            for key, names in (("pgin", ("pgin", "pgpgin")), ("pgout", ("pgout", "pgpgout")),
                               ("pgsin", ("pgsin", "pswpin")), ("pgsout", ("pgsout", "pswpout"))):
                for nm in names:
                    x = col(sec, v, nm)
                    if x is not None:
                        put(t, key, x); found.add("PAGE")
                        break
        elif sec == "NET":
            for name, val in zip(headers.get("NET") or [], v):
                m = re.fullmatch(r"(.+)-(read|write)-KB/s", name)
                if m and not m.group(1).startswith("lo"):
                    put(t, f"nic:{m.group(1)}:{'rx' if m.group(2) == 'read' else 'tx'}", _num(val)); found.add("NET")
        elif sec == "NETERROR":
            acc: dict[str, float] = {}
            for name, val in zip(headers.get("NETERROR") or [], v):
                m = re.fullmatch(r"(.+)-(ierrs|oerrs)", name)
                x = _num(val)
                if m and x is not None and not m.group(1).startswith("lo"):
                    acc[m.group(1)] = acc.get(m.group(1), 0) + x
            for nic, x in acc.items():
                put(t, f"nic:{nic}:err", round(x, 2)); found.add("NETERROR")
        elif sec in ("DISKBUSY", "DISKREAD", "DISKWRITE"):
            k = {"DISKBUSY": "busy", "DISKREAD": "rd", "DISKWRITE": "wr"}[sec]
            for name, val in zip(headers.get(sec) or [], v):
                put(t, f"disk:{name}:{k}", _num(val)); found.add(sec)
        elif sec == "JFSFILE":
            for name, val in zip(headers.get("JFSFILE") or [], v):
                put(t, f"fs:{name}", _num(val)); found.add("JFSFILE")

    if not rows:
        raise ValueError(f"{host}：找不到任何可用的指標（CPU_ALL 等區段）")

    # 磁碟太多只留最忙的 MAX_DISKS 顆（avg busy），其餘丟掉（寫進 notes，不假裝全收）
    notes: list[str] = []
    disks: dict[str, list[float]] = {}
    for r in rows.values():
        for k, x in r.items():
            if k.startswith("disk:") and k.endswith(":busy"):
                disks.setdefault(_name_kind(k)[0], []).append(x)
    if len(disks) > MAX_DISKS:
        keep = {d for d, _ in sorted(disks.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))[:MAX_DISKS]}
        for r in rows.values():
            for k in [k for k in r if k.startswith("disk:") and _name_kind(k)[0] not in keep]:
                del r[k]
        notes.append(f"磁碟共 {len(disks)} 顆，只留平均忙碌最高的 {MAX_DISKS} 顆")

    days: dict[str, dict] = {}
    for (day, hhmmss) in sorted(rows):
        d = days.setdefault(day, {"rows": {}, "top": {}})
        d["rows"][hhmmss] = rows[(day, hhmmss)]
    for (day, hhmmss), lst in top.items():
        if day in days:
            days[day]["top"][hhmmss] = sorted(lst, key=lambda x: -x[1])[:TOP_PER_SNAPSHOT]
    return {"host": host, "interval_s": interval, "days": days, "found": sorted(found), "notes": notes}


# ---------------------------------------------------------------- 存取
def _encode(rows: dict[str, dict], top: dict) -> bytes:
    ts = sorted(rows)
    keys = sorted({k for r in rows.values() for k in r})
    cols = {k: [rows[t].get(k) for t in ts] for k in keys}
    return gzip.compress(json.dumps({"ts": ts, "cols": cols, "top": top}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _decode(blob: bytes) -> dict:
    return json.loads(gzip.decompress(blob).decode("utf-8"))


def store(conn: sqlite3.Connection, p: dict) -> dict:
    """把 parse() 的結果寫進 nmon_series（同台同天依時間點覆蓋）。回 {days, samples}。"""
    ensure(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = 0
    for day, d in sorted(p["days"].items()):
        rows = {t: dict(r) for t, r in d["rows"].items()}
        top = dict(d["top"])
        old = conn.execute("SELECT data FROM nmon_series WHERE host = ? AND day = ?", (p["host"], day)).fetchone()
        if old:
            o = _decode(old[0])
            merged: dict[str, dict] = {}
            for i, t in enumerate(o["ts"]):
                merged[t] = {k: c[i] for k, c in o["cols"].items() if c[i] is not None}
            for t, r in rows.items():
                merged.setdefault(t, {}).update(r)
            rows = merged
            o_top = o.get("top") or {}
            o_top.update(top)
            top = o_top
        conn.execute("INSERT OR REPLACE INTO nmon_series (host, day, interval_s, samples, data, updated_at) VALUES (?,?,?,?,?,?)",
                     (p["host"], day, p["interval_s"], len(rows), _encode(rows, top), now))
        total += len(rows)
    conn.commit()
    return {"days": len(p["days"]), "samples": total}


def _load(conn: sqlite3.Connection, host: str, month: str, day: str | None = None) -> list[tuple[str, dict]]:
    ensure(conn)
    if day:
        q, a = "SELECT day, data FROM nmon_series WHERE host = ? AND day = ? ORDER BY day", (host, day)
    else:
        q, a = "SELECT day, data FROM nmon_series WHERE host = ? AND day LIKE ? ORDER BY day", (host, f"{month}%")
    return [(r[0], _decode(r[1])) for r in conn.execute(q, a)]


def hosts(conn: sqlite3.Connection, month: str | None = None) -> list[dict]:
    """有逐分鐘資料的主機：每台的天數、樣本數、間隔、期間。"""
    ensure(conn)
    q = "SELECT host, COUNT(*) d, SUM(samples) s, MIN(day) a, MAX(day) b, MAX(interval_s) i FROM nmon_series "
    a: tuple = ()
    if month:
        q += "WHERE day LIKE ? "
        a = (f"{month}%",)
    q += "GROUP BY host ORDER BY host"
    return [{"host": r[0], "days": r[1], "samples": r[2], "from": r[3], "to": r[4], "interval_s": r[5]} for r in conn.execute(q, a)]


def months(conn: sqlite3.Connection) -> list[str]:
    ensure(conn)
    return [r[0] for r in conn.execute("SELECT DISTINCT substr(day, 1, 7) FROM nmon_series ORDER BY 1 DESC")]


def files(conn: sqlite3.Connection, month: str | None = None) -> list[dict]:
    """有存原始檔的 NMON 檔（給「下載原始 NMON」）。"""
    ensure(conn)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'nmon_file'").fetchone():
        return []                                   # 還沒有人上傳過（月報那張表是第一次上傳才建）
    q = "SELECT host, filename, first_ts, last_ts, uploaded_at FROM nmon_file WHERE raw_path IS NOT NULL"
    a: tuple = ()
    if month:
        q += " AND substr(first_ts, 1, 7) <= ? AND substr(last_ts, 1, 7) >= ?"
        a = (month, month)
    return [dict(r) for r in conn.execute(q + " ORDER BY host, first_ts", a)]


# ---------------------------------------------------------------- 指標目錄（畫面照這個分組畫圖）
GROUPS = [
    {"key": "cpu", "label": "CPU", "items": [("cpu_busy", "忙碌（user＋sys）", "%"), ("cpu_user", "User", "%"), ("cpu_sys", "Sys", "%"),
                                           ("cpu_wait", "Wait", "%"), ("cpu_idle", "Idle", "%"), ("runq", "排隊數（Runnable）", "")]},
    {"key": "mem", "label": "記憶體與換頁", "items": [("mem_used", "記憶體使用率", "%"), ("swap_used", "換頁空間使用率", "%"),
                                                     ("pgsin", "換頁 in（pgsin）", "/s"), ("pgsout", "換頁 out（pgsout）", "/s"),
                                                     ("pgin", "pgin", "/s"), ("pgout", "pgout", "/s")]},
    {"key": "disk", "label": "磁碟", "dynamic": "disk"},
    {"key": "net", "label": "網路", "dynamic": "nic"},
    {"key": "fs", "label": "檔案系統", "dynamic": "fs"},
]
THRESHOLD_OF = {"cpu_busy": THRESHOLDS["cpu_busy"], "mem_used": THRESHOLDS["mem_used"], "swap_used": THRESHOLDS["swap_used"]}


def _name_kind(key: str) -> tuple[str, str]:
    """「disk:hdisk0:busy」→（hdisk0, busy）；名稱裡有冒號也不會拆壞（從最後一個冒號切）。"""
    rest = key.split(":", 1)[1]
    name, _, kind = rest.rpartition(":")
    return name, kind


def describe(key: str) -> dict:
    """一個指標的標籤、單位、門檻。"""
    for g in GROUPS:
        for k, label, unit in g.get("items", []):
            if k == key:
                return {"key": key, "label": label, "unit": unit, "threshold": THRESHOLD_OF.get(key), "group": g["key"]}
    if key.startswith("disk:"):
        name, kind = _name_kind(key)
        lab = {"busy": "忙碌", "rd": "讀", "wr": "寫"}[kind]
        return {"key": key, "label": f"{name} {lab}", "unit": "%" if kind == "busy" else "KB/s",
                "threshold": THRESHOLDS["disk_busy"] if kind == "busy" else None, "group": "disk"}
    if key.startswith("nic:"):
        name, kind = _name_kind(key)
        lab = {"rx": "收", "tx": "送", "err": "錯誤"}[kind]
        return {"key": key, "label": f"{name} {lab}", "unit": "" if kind == "err" else "KB/s", "threshold": None, "group": "net"}
    if key.startswith("fs:"):
        return {"key": key, "label": key[3:], "unit": "%", "threshold": THRESHOLDS["fs"], "group": "fs"}
    return {"key": key, "label": key, "unit": "", "threshold": None, "group": "other"}


def _agg(vals: list, mode: str) -> float | None:
    v = [x for x in vals if x is not None]
    if not v:
        return None
    return round(max(v) if mode == "max" else sum(v) / len(v), 2)


def series(conn: sqlite3.Connection, host: str, month: str, grain: str = "hour", day: str | None = None,
           keys: list[str] | None = None) -> dict:
    """畫圖用。grain：day（每天一點）／hour（每小時一點）／raw（原始取樣，只能看某一天）。
    每點 [x, 平均, 尖峰, 尖峰時間]；raw 的平均＝尖峰＝原始值。沒有任何資料的指標不出現（畫面寫「未收集」，不補 0）。"""
    if grain not in ("day", "hour", "raw"):
        raise ValueError("grain 要是 day／hour／raw")
    if grain == "raw" and not day:
        raise ValueError("原始顆粒要指定哪一天（day=YYYY-MM-DD）")
    data = _load(conn, host, month, day if grain == "raw" else None)
    if not data:
        return {"host": host, "month": month, "grain": grain, "day": day, "interval_s": None, "days": [], "series": {}}
    allkeys = sorted({k for _, d in data for k in d["cols"]})
    if keys:
        allkeys = [k for k in allkeys if k in set(keys)]
    out: dict[str, list] = {}
    for key in allkeys:
        pts: list[list] = []
        for dy, d in data:
            ts, col = d["ts"], d["cols"].get(key)
            if col is None:
                continue
            if grain == "raw":
                pts += [[f"{dy} {t}", c, c, f"{dy} {t}"] for t, c in zip(ts, col) if c is not None]
                continue
            buckets: dict[str, list] = {}
            for t, c in zip(ts, col):
                if c is None:
                    continue
                buckets.setdefault(dy if grain == "day" else f"{dy} {t[:2]}", []).append((t, c))
            for b, lst in buckets.items():
                cs = [c for _, c in lst]
                mx = max(cs)
                at = next(t for t, c in lst if c == mx)
                pts.append([b, _agg(cs, "avg"), mx, f"{dy} {at}"])
        if pts:
            out[key] = pts
    return {"host": host, "month": month, "grain": grain, "day": day,
            "interval_s": next((d.get("interval_s") for _, d in data if d.get("interval_s")), None),
            "days": [dy for dy, _ in data], "series": out,
            "meta": {k: describe(k) for k in out}}


def _runs(ts: list[str], vals: list, day: str, thr: float, step_s: int) -> list[dict]:
    """超過門檻的連續時段（相鄰取樣算連續）。每段：開始、結束、分鐘數、尖峰與尖峰時間。"""
    out, cur = [], None
    for t, v in zip(ts, vals):
        if v is not None and v > thr:
            if cur is None:
                cur = {"from": f"{day} {t}", "to": f"{day} {t}", "peak": v, "peak_at": f"{day} {t}", "n": 1}
            else:
                cur["to"] = f"{day} {t}"
                cur["n"] += 1
                if v > cur["peak"]:
                    cur["peak"], cur["peak_at"] = v, f"{day} {t}"
        elif cur is not None:
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    for r in out:
        r["minutes"] = round(r.pop("n") * step_s / 60)
    return out


def summary(conn: sqlite3.Connection, host: str, month: str) -> dict:
    """每個指標一張卡：平均、尖峰（含時間）、超過門檻的樣本數與時段清單（卡片數字點開看是哪些時間點）。
    沒收集（整個月沒有這個欄位）的指標不在 items 裡；畫面對應的組別寫「未收集」。"""
    data = _load(conn, host, month)
    items: list[dict] = []
    if not data:
        return {"host": host, "month": month, "items": [], "collected_groups": []}
    step = next((d.get("interval_s") for _, d in data if d.get("interval_s")), None) or 60
    keys = sorted({k for _, d in data for k in d["cols"]})
    for key in keys:
        vals_all, peak, peak_at = [], None, None
        runs: list[dict] = []
        over = 0
        meta = describe(key)
        thr = meta["threshold"]
        for dy, d in data:
            col = d["cols"].get(key)
            if col is None:
                continue
            for t, v in zip(d["ts"], col):
                if v is None:
                    continue
                vals_all.append(v)
                if peak is None or v > peak:
                    peak, peak_at = v, f"{dy} {t}"
            if thr is not None:
                runs += _runs(d["ts"], col, dy, thr, step)
                over += sum(1 for v in col if v is not None and v > thr)
        if not vals_all:
            continue
        item = {**meta, "avg": round(sum(vals_all) / len(vals_all), 2), "peak": peak, "peak_at": peak_at,
                "samples": len(vals_all), "over": over if thr is not None else None,
                "over_runs": runs[:300]}
        if meta["group"] == "fs":
            item["over_crit"] = sum(1 for dy, d in data for v in (d["cols"].get(key) or []) if v is not None and v > THRESHOLDS["fs_crit"])
        items.append(item)
    present = {i["group"] for i in items}
    return {"host": host, "month": month, "interval_s": step, "items": items,
            "collected_groups": [g["key"] for g in GROUPS if g["key"] in present],
            "all_groups": [{"key": g["key"], "label": g["label"]} for g in GROUPS],
            "thresholds": THRESHOLDS}


def top_at(conn: sqlite3.Connection, host: str, ts: str) -> dict:
    """某個時間點最吃 CPU 的程式：取當天**最接近**的那個 TOP 快照（NMON 只在有程式夠忙時才記，不是每個取樣點都有）。
    exact＝剛好同一個時間點；不是的話畫面要寫出實際是哪個時間。沒收 TOP 就回空清單。"""
    day, t = ts[:10], ts[11:19]
    data = _load(conn, host, day[:7], day)
    top = (data[0][1].get("top") or {}) if data else {}
    if not top:
        return {"host": host, "ts": ts, "at": None, "procs": []}

    def secs(x: str) -> int:
        h, m, s_ = (int(v) for v in x.split(":"))
        return h * 3600 + m * 60 + s_
    try:
        at = min(top, key=lambda k: abs(secs(k) - secs(t)))
    except ValueError:   # 時間格式不對：回空，不猜
        return {"host": host, "ts": ts, "at": None, "procs": []}
    return {"host": host, "ts": ts, "at": f"{day} {at}", "exact": at == t,
            "procs": [{"pid": p, "cpu": c, "cmd": m} for p, c, m in top[at]]}


# ---------------------------------------------------------------- 原始檔
def raw_dir(conn: sqlite3.Connection) -> Path:
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    return Path(path).parent / "nmon_raw"


def save_raw(conn: sqlite3.Connection, host: str, filename: str, data: bytes) -> str:
    """原始上傳檔（原樣，不解壓）另存在資料庫旁的 nmon_raw/，回檔名。
    檔名一律是系統產生的（uuid），**不用主機名或上傳檔名**——那兩個都是上傳者能控制的字串，
    曾經 `..` 當主機名＋ asset.db 當檔名就能蓋掉資料庫（2026-10-28 獨立審查）。原始檔名只存資料庫、下載時才拿來當檔名。"""
    import uuid
    d = raw_dir(conn)
    d.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.nmon"
    (d / name).write_bytes(data)
    return name


def raw_file(conn: sqlite3.Connection, host: str, filename: str) -> Path | None:
    """下載原始檔用：只回 nmon_raw 底下、且這台確實有登記的檔（不接受路徑跳脫）。"""
    r = conn.execute("SELECT raw_path FROM nmon_file WHERE host = ? AND filename = ? AND raw_path IS NOT NULL "
                     "ORDER BY uploaded_at DESC LIMIT 1", (host, filename)).fetchone()
    if not r or not r[0]:
        return None
    base = raw_dir(conn).resolve()
    p = (base / r[0]).resolve()
    return p if base in p.parents and p.is_file() else None
