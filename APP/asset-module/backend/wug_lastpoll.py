"""WhatsUp「上次輪循」群組快照（S13，2.062.0）：9.26 的 wug_lastpoll_collect.ps1 每 30 分鐘一批 → 這裡存表 → 1.5 每日檢查讀。

設計：docs/設計_每日檢查改抓上次輪循_2026-11-12.md（§10 修正後的階段為準）；人工檢查表正本 1-3～1-7。

## 資料怎麼進來
收集端對幾個 WhatsUp 群組（含子群組）各問一次成員、cpu／memory／disk-utilization（range=lastPolled），打成一包
（資料夾 wuglp_<時間>：snap.json＋manifest.json，source=whatsup_lastpoll）。relay_import 的 WugLastpollAdapter 驗證後呼叫 parse／apply。

## 快速通道（盤中也匯入）
每批只寫幾千列、不重算全站，跟告警快照同級：scan_service 每分鐘呼叫 tick（quiet_window C 類例外 wug_lastpoll_tick），
只撿 wuglp_ 開頭的批次；一般 relay_import 排程跳過它們（relay_import.FAST_PREFIXES）。只讀檔案、寫本機表，不連任何主機、不連 WhatsUp。

## 完整性（防假正常）
manifest 帶每個群組成員數與各指標筆數；對不上、群組 ok=false、指標 ok=false 或缺指標 → 該批照存但 complete=0、problems 記原因。
每日檢查只拿「最新且完整」的一批（latest_complete）。
收集端 v2：報表讀到頁數上限／預算＝partial（已讀的列照收、不算不完整），每日檢查那列標「只讀到部分」、不判新鮮、沒讀到的設備逐台無資料。
avgPercent 是 0～1 小數（NB 11-13 實跑）→ _scale_batch 用記憶體／磁碟的 used/size 判單位、套到整批（含 CPU）；判不出或 >100% → unit_err，該列無資料。

## 表
- wug_lp_batch：一批一列（收集時間＝台北、完整與否、問題、各群組摘要）。
- wug_lp_value：每批每群組每台每個項目一列（cpu／mem 一台一列或多列、disk 逐顆）。保留 VALUE_KEEP_DAYS 天。
- wug_lp_member：成員，以批次為鍵（判定用的成員與數值一定同一批）。只有「完整、而且比已存的更新」的批次寫成員；
  30 天內每批保留，超過 30 天每天只留最後一批（永久，稽核回看範圍）。
- wug_lp_approval：已核准的成員快照（第一次匯入自動核准一筆；成員跳動超過門檻要管理員在設定頁核准）。

讀取函式不建表；寫入路徑（apply、approve）才 ensure。
"""
from __future__ import annotations

import hashlib
import json
import math
import logging
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone

log = logging.getLogger("wug_lastpoll")

SOURCE = "whatsup_lastpoll"
BATCH_PREFIX = "wuglp_"
FMT = "%Y-%m-%d %H:%M:%S"
METRICS = ("cpu", "mem", "disk")
METRIC_LABEL = {"cpu": "CPU", "mem": "記憶體", "disk": "磁碟"}
VALUE_KEEP_DAYS = 30
MAX_GROUPS = 50
MAX_ROWS = 300_000
AUTO_BY = "（系統：第一次自動核准）"

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS wug_lp_batch (
        batch_id TEXT PRIMARY KEY, collected_at TEXT NOT NULL, imported_at TEXT NOT NULL, collector_version TEXT,
        complete INTEGER NOT NULL, problems_json TEXT, groups_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_wug_lp_batch_at ON wug_lp_batch(collected_at)",
    """CREATE TABLE IF NOT EXISTS wug_lp_value (
        batch_id TEXT NOT NULL, group_path TEXT NOT NULL, device_id TEXT NOT NULL, ip TEXT, name TEXT,
        metric TEXT NOT NULL, item TEXT, pct REAL, poll_at TEXT, age_s INTEGER)""",
    "CREATE INDEX IF NOT EXISTS ix_wug_lp_value_b ON wug_lp_value(batch_id, group_path, metric)",
    """CREATE TABLE IF NOT EXISTS wug_lp_member (
        batch_id TEXT NOT NULL, day TEXT NOT NULL, collected_at TEXT NOT NULL, group_path TEXT NOT NULL, group_id TEXT, device_id TEXT NOT NULL,
        ip TEXT, name TEXT, PRIMARY KEY (batch_id, group_path, device_id))""",
    "CREATE INDEX IF NOT EXISTS ix_wug_lp_member_day ON wug_lp_member(day, collected_at, batch_id)",
    """CREATE TABLE IF NOT EXISTS wug_lp_approval (
        id INTEGER PRIMARY KEY AUTOINCREMENT, group_path TEXT NOT NULL, group_id TEXT, at TEXT NOT NULL, by TEXT, auto INTEGER NOT NULL,
        count INTEGER NOT NULL, device_ids TEXT NOT NULL, batch_id TEXT, note TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_wug_lp_approval_p ON wug_lp_approval(group_path, id)",
    # 收集端 v4：列在 wug_lp_devices.txt 的幾台設備（1.5 板橋交易所列）的 WhatsUp 狀態與最後輪循（devices.json）
    """CREATE TABLE IF NOT EXISTS wug_lp_device (
        batch_id TEXT NOT NULL, query TEXT NOT NULL, ip TEXT, device_id TEXT, name TEXT, ok INTEGER NOT NULL, error TEXT,
        worst_state TEXT, best_state TEXT, monitors_down INTEGER, down_monitors TEXT, last_change TEXT, poll_at TEXT, age_s INTEGER,
        collected_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_wug_lp_device_b ON wug_lp_device(batch_id, ip)",
]


def ensure(conn: sqlite3.Connection) -> None:
    for s in SCHEMA:
        conn.execute(s)


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _tpe(d: datetime) -> datetime:
    return d.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)


# ---------------------------------------------------------------- parse（壞資料丟 ValueError → relay 拒收）
def _num(v, what: str):
    """WhatsUp 的數字欄：None＝沒有值（照收）；不是數字＝壞資料。"""
    if v is None or v == "":
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{what} 不是數字：{str(v)[:20]}")
    if not math.isfinite(v):                    # Infinity／1e400：int() 會丟 OverflowError 卡住整個中繼匯入（審查 P2）→ 當壞資料整批拒收
        raise ValueError(f"{what} 不是有限數字")
    return v


def _text(v, n: int = 300) -> str:
    return "" if v is None else str(v)[:n]


def parse(data, manifest: dict | None = None) -> dict:
    """snap.json → {"groups": [...], "complete": bool, "problems": [...]}。結構不對丟 ValueError（整批拒收）；
    結構對但內容缺（群組失敗、缺指標、筆數對不上）＝照收、標不完整。"""
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        raise ValueError("snap.json 要有 groups 清單")
    gs = data["groups"]
    if not gs:
        raise ValueError("groups 是空的（收集端沒有任何群組，不收）")
    if len(gs) > MAX_GROUPS:
        raise ValueError(f"群組超過 {MAX_GROUPS} 個")
    out, problems, warnings, seen, n_rows = [], [], [], set(), 0
    for i, g in enumerate(gs, 1):
        if not isinstance(g, dict):
            raise ValueError(f"第 {i} 個群組格式不對")
        path = _text(g.get("path")).strip()
        if not path:
            raise ValueError(f"第 {i} 個群組沒有 path")
        if not isinstance(g.get("ok"), bool):
            raise ValueError(f"{path}：ok 要是 true/false")
        if path in seen:
            raise ValueError(f"群組重複：{path}")
        seen.add(path)
        members = g.get("members")
        if members is None:
            members = []
        if not isinstance(members, list):
            raise ValueError(f"{path}：members 要是清單")
        mem_out, mids = [], set()
        for m in members:
            if not isinstance(m, dict) or not _text(m.get("id")).strip():
                raise ValueError(f"{path}：成員沒有 id")
            did = _text(m.get("id"), 64).strip()
            if did in mids:
                continue                       # 同一台在子群組重複列出：成員以 deviceId 去重
            mids.add(did)
            mem_out.append({"id": did, "name": _text(m.get("name"), 255), "ip": _text(m.get("ip"), 64).strip()})
        grp = {"query": _text(g.get("query")) or path, "path": path, "id": _text(g.get("id"), 64) or None, "ok": g["ok"],
               "error": _text(g.get("error")), "members": mem_out if g["ok"] else [], "metrics": {}}
        if not g["ok"]:
            problems.append(f"{path}：{grp['error'] or '收集端標失敗'}")
        mets = g.get("metrics")
        if mets is not None and not isinstance(mets, dict):
            raise ValueError(f"{path}：metrics 要是物件")
        mets = mets or {}
        for k in METRICS:
            m = mets.get(k)
            if m is None:
                grp["metrics"][k] = {"ok": False, "error": "缺這個指標", "rows": []}
                if g["ok"]:
                    problems.append(f"{path}：缺 {METRIC_LABEL[k]} 指標")
                continue
            if not isinstance(m, dict) or not isinstance(m.get("ok"), bool) or not isinstance(m.get("rows", []), list):
                raise ValueError(f"{path}／{k}：格式不對（要有 ok 與 rows 清單）")
            # partial（收集端 v2）：報表讀到頁數上限／呼叫預算／時間盒就停，已讀的頁照收（WhatsUp 預設依使用率由高到低排，讀到的是最高的那些）。
            # 不算「讀取失敗」：批次照樣完整；沒讀到的設備在每日檢查逐台顯示無資料，該列標「只讀到部分」不能判新鮮。
            partial = (not m["ok"]) and m.get("partial") is True
            pages = m.get("pages")
            pages = pages if isinstance(pages, int) and not isinstance(pages, bool) and pages >= 0 else None
            rows = []
            for r in m.get("rows") or []:
                if not isinstance(r, dict) or not _text(r.get("id")).strip():
                    raise ValueError(f"{path}／{k}：有一筆沒有設備 id")
                avg = _num(r.get("avg"), f"{path}／{k} avg")
                if avg is not None and avg < 0:
                    raise ValueError(f"{path}／{k}：使用率是負的")
                age = _num(r.get("age"), f"{path}／{k} age")
                used, size = _num(r.get("used"), f"{path}／{k} used"), _num(r.get("size"), f"{path}／{k} size")
                rid = _text(r.get("device_id"), 64).strip() or _text(r.get("id"), 64).strip()   # 有 deviceId 就用它（9.26 實跑待確認 id 是不是 deviceId）
                rows.append({"id": rid, "name": _text(r.get("name"), 255), "item": _text(r.get("item"), 255),
                             "pct": float(avg) if avg is not None else None, "poll": _text(r.get("poll"), 40),
                             "age": int(age) if age is not None else None, "used": used, "size": size})
            n_rows += len(rows)
            if n_rows > MAX_ROWS:
                raise ValueError(f"一批超過 {MAX_ROWS} 筆")
            keep = m["ok"] or partial
            grp["metrics"][k] = {"ok": keep and g["ok"], "partial": partial and g["ok"], "pages": pages, "error": _text(m.get("error")),
                                 "rows": rows if keep else [], "note": _text(m.get("note"))}
            if g["ok"] and keep and grp["metrics"][k]["note"]:      # 收集端 v3：涵蓋率低（設備數 < 成員一半、沒有下一頁）→ 提示，不擋
                warnings.append(f"{path}：{METRIC_LABEL[k]} {grp['metrics'][k]['note']}")
            if g["ok"] and not keep:
                problems.append(f"{path}：{METRIC_LABEL[k]} 讀取失敗：{_text(m.get('error')) or '—'}")
            if g["ok"] and partial:
                warnings.append(f"{path}：{METRIC_LABEL[k]} 只讀到部分（{pages if pages is not None else '?'} 頁、{len(rows)} 筆）")
        out.append(grp)
    problems += _check_counts(out, (manifest or {}).get("counts"))
    units = _scale_batch(out, warnings)
    return {"groups": out, "complete": not problems, "problems": problems, "warnings": warnings, "units": units}


UNIT_VOTERS = ("mem", "disk")      # 有 used／size 才能判單位的指標
OVER_100 = 100.01                  # 換算後超過＝單位判錯或資料壞（小數點誤差容忍 0.01）


def _vote(rows: list[dict]) -> float | None:
    """一個指標的列：avg 比較接近 used/size（小數，×100）還是 used/size×100（已是百分比，×1），多數決；沒得比或平手＝None。"""
    frac = pct = 0
    for r in rows:
        if r["pct"] is None or not r.get("used") or not r.get("size") or r["size"] <= 0:
            continue
        ratio = r["used"] / r["size"]
        if ratio <= 0:
            continue
        if abs(r["pct"] - ratio) <= abs(r["pct"] - ratio * 100):
            frac += 1
        else:
            pct += 1
    if frac == pct:
        return None
    return 100.0 if frac > pct else 1.0


def _scale_batch(groups: list[dict], warnings: list[str]) -> dict:
    """WhatsUp 的 avgPercent 是 0～1 小數（Swagger 寫 decimal number；NB 11-13 實跑磁碟 0.1003＝畫面 10%）→ 換成 0～100。
    審查 P2：不看數值大小猜（整批 CPU ≤1% 會被誤乘 100；小數裡混一個 1.02 會整批不換、全部變 0.x% 假正常）。
    單位一批只判一次：記憶體、磁碟有 used/size 的列各自多數決，判得出的要一致，再套到同一批所有指標（含 CPU）。
    判不出（沒有 used/size、平手、兩邊不一致）→ 不猜：每個有值的指標標 unit_err，每日檢查那列無資料並寫原因，絕不判正常。
    換算後還有 > 100% 的列 → 那個指標也標 unit_err。回傳 {指標: 乘數或 None}；pct 就地改。"""
    def rows_of(k):
        return [r for g in groups for r in g["metrics"][k]["rows"]]
    votes = {k: _vote(rows_of(k)) for k in UNIT_VOTERS}
    decided = {v for v in votes.values() if v is not None}
    factor, why = None, None
    if len(decided) == 1:
        factor = decided.pop()
    elif not decided:
        why = "單位判斷不出（記憶體／磁碟沒有可比對的已用量／容量），不猜是小數還是百分比"
    else:
        why = "單位判斷不一致（記憶體判成" + ("小數" if votes["mem"] == 100.0 else "百分比") + "、磁碟判成" + \
              ("小數" if votes["disk"] == 100.0 else "百分比") + "），不猜"
    if factor == 1.0:
        # 複審 P3（11-14）：判成「已是百分比」但記憶體＋磁碟每一筆都 ≤1，多半是 used/size 單位不一致（MB 對 bytes）
        # 讓投票誤判；照這樣顯示會全部 0.x%＝假正常。筆數夠多（≥10）才判，避免小樣本誤擋。
        vals = [r["pct"] for k in UNIT_VOTERS for r in rows_of(k) if r["pct"] is not None]
        if len(vals) >= 10 and max(vals) <= 1.0:
            factor, why = None, "判成百分比，但記憶體／磁碟全部 ≤1%，疑似已用量與容量單位不一致，不猜"
    units = {}
    for k in METRICS:
        rows = [r for r in rows_of(k) if r["pct"] is not None]
        err = None
        if rows and factor is None:
            err = why
        elif rows:
            if factor != 1.0:
                for r in rows:
                    r["pct"] = round(r["pct"] * factor, 4)
            over = [r["pct"] for r in rows if r["pct"] > OVER_100]
            if over:
                err = f"有 {len(over)} 筆換算後超過 100%（最高 {max(over):g}%），單位或資料有問題"
        for g in groups:
            g["metrics"][k]["unit_err"] = err
        if err:
            warnings.append(f"{METRIC_LABEL[k]}：{err}")
        units[k] = None if err else factor
    return units


def _check_counts(groups: list[dict], counts) -> list[str]:
    """manifest 的筆數（收集端寫檔前數的）對 snap.json 實際內容；對不上＝不完整（檔案被截斷、半寫入）。"""
    if not isinstance(counts, list) or len(counts) != len(groups):
        return ["manifest 沒有各群組筆數（counts），無法確認這批是完整的"]
    bad = []
    for g, c in zip(groups, counts):
        if not isinstance(c, dict):
            bad.append(f"{g['path']}：manifest 筆數格式不對")
            continue
        want = {"members": c.get("members"), **{k: c.get(k) for k in METRICS}}
        got = {"members": len(g["members"]), **{k: len(g["metrics"][k]["rows"]) for k in METRICS}}
        if not g["ok"]:
            continue                            # 已經記成失敗，不重複
        # 成員：收集端數的是原始筆數（可能含重複），這裡去重後 ≤ 原始筆數才算合理
        if not isinstance(want["members"], int) or got["members"] > want["members"] or (want["members"] > 0 and got["members"] == 0):
            bad.append(f"{g['path']}：成員筆數對不上（manifest {want['members']}、檔案 {got['members']}）")
        for k in METRICS:
            if g["metrics"][k]["ok"] and want[k] != got[k]:
                bad.append(f"{g['path']}：{METRIC_LABEL[k]} 筆數對不上（manifest {want[k]}、檔案 {got[k]}）")
    return bad


# ---------------------------------------------------------------- apply（在 relay 匯入交易裡；呼叫端提交）
def _poll_text(s: str) -> str | None:
    import relay_import as ri
    d = ri.parse_time(s, "UTC")
    return _tpe(d).strftime(FMT) if d else None


def _ids_sha(ids) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()[:16]


MAX_DEVICES = 200


def parse_devices(data) -> list[dict]:
    """devices.json（收集端 v4）→ 每台一列。結構不對丟 ValueError（整批拒收）；單台找不到／讀不到＝ok=false 照收（每日檢查那列無資料）。"""
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        raise ValueError("devices.json 要有 devices 清單")
    if len(data["devices"]) > MAX_DEVICES:
        raise ValueError(f"devices 超過 {MAX_DEVICES} 台")
    out = []
    for i, d in enumerate(data["devices"], 1):
        if not isinstance(d, dict) or not _text(d.get("query")).strip() or not isinstance(d.get("ok"), bool):
            raise ValueError(f"devices 第 {i} 筆格式不對（要有 query 與 ok）")
        age = _num(d.get("age"), f"devices 第 {i} 筆 age")
        md = _num(d.get("monitors_down"), f"devices 第 {i} 筆 monitors_down")
        if (age is not None and age < 0) or (md is not None and md < 0):   # 負的輪循秒數會讓舊資料看起來很新（審查 P3）
            raise ValueError(f"devices 第 {i} 筆 age／monitors_down 不可為負數")
        dm = d.get("down_monitors") or []
        if not isinstance(dm, list):
            raise ValueError(f"devices 第 {i} 筆 down_monitors 要是清單")
        out.append({"query": _text(d.get("query"), 64).strip(), "ip": _text(d.get("ip"), 64).strip(), "device_id": _text(d.get("id"), 64).strip(),
                    "name": _text(d.get("name"), 255), "ok": d["ok"], "error": _text(d.get("error")),
                    "worst_state": _text(d.get("worst_state"), 40).strip(), "best_state": _text(d.get("best_state"), 40).strip(),
                    "monitors_down": int(md) if md is not None else None, "down_monitors": [_text(x, 100) for x in dm][:10],
                    "last_change": _text(d.get("last_change"), 40), "poll": _text(d.get("poll"), 40),
                    "age": int(age) if age is not None else None, "collected_at": _text(d.get("collected_at"), 40)})
    return out


def apply(conn, batch_id: str, collected_at: str, collector_version: str, parsed: dict, now_text: str, devices: list[dict] | None = None) -> dict:
    import relay_import as ri
    ensure(conn)
    col = ri.parse_time(collected_at, "Asia/Taipei")
    col_t = _tpe(col)
    col_s = col_t.strftime(FMT)
    day = col_t.date().isoformat()
    conn.execute("DELETE FROM wug_lp_value WHERE batch_id = ?", (batch_id,))
    # 跟 latest_complete 同一個排序（收集時間、再 batch_id）：只有排在最前面的完整批次寫成員。晚到的舊批、不完整的批：不寫成員、不自動核准
    newest = conn.execute("SELECT collected_at, batch_id FROM wug_lp_batch WHERE complete = 1 AND batch_id <> ? "
                          "ORDER BY collected_at DESC, batch_id DESC LIMIT 1", (batch_id,)).fetchone()
    write_members = parsed["complete"] and (newest is None or (col_s, batch_id) > (newest[0], newest[1]))
    summary, vals = [], []
    for g in parsed["groups"]:
        ipof = {m["id"]: m["ip"] for m in g["members"]}
        s = {"query": g["query"], "path": g["path"], "id": g["id"], "ok": g["ok"], "error": g["error"],
             "members": len(g["members"]), "ips": len({m["ip"] for m in g["members"] if m["ip"]}), "member_sha": _ids_sha(ipof)}
        for k in METRICS:
            m = g["metrics"][k]
            s[k] = {"ok": m["ok"], "error": m["error"], "rows": len(m["rows"]), "devices": len({r["id"] for r in m["rows"]}),
                    "partial": bool(m.get("partial")), "pages": m.get("pages"), "unit_err": m.get("unit_err"), "note": m.get("note") or ""}
            for r in m["rows"]:
                vals.append((batch_id, g["path"], r["id"], ipof.get(r["id"]) or None, r["name"], k, r["item"], r["pct"],
                             _poll_text(r["poll"]), r["age"]))
        summary.append(s)
        if g["ok"] and write_members:
            conn.execute("DELETE FROM wug_lp_member WHERE batch_id = ? AND group_path = ?", (batch_id, g["path"]))
            conn.executemany("INSERT INTO wug_lp_member (batch_id, day, collected_at, group_path, group_id, device_id, ip, name) VALUES (?,?,?,?,?,?,?,?)",
                             [(batch_id, day, col_s, g["path"], g["id"], m["id"], m["ip"] or None, m["name"]) for m in g["members"]])
            if g["members"] and _last_approval(conn, g["path"]) is None:
                _insert_approval(conn, g["path"], g["id"], col_s, AUTO_BY, True, [m["id"] for m in g["members"]], batch_id,
                                 "第一次收到這個群組，自動核准目前成員")
    conn.execute("INSERT OR REPLACE INTO wug_lp_batch (batch_id, collected_at, imported_at, collector_version, complete, problems_json, groups_json) "
                 "VALUES (?,?,?,?,?,?,?)", (batch_id, col_s, now_text, collector_version, 1 if parsed["complete"] else 0,
                                           json.dumps(parsed["problems"], ensure_ascii=False), json.dumps(summary, ensure_ascii=False)))
    conn.executemany("INSERT INTO wug_lp_value (batch_id, group_path, device_id, ip, name, metric, item, pct, poll_at, age_s) VALUES (?,?,?,?,?,?,?,?,?,?)", vals)
    conn.execute("DELETE FROM wug_lp_device WHERE batch_id = ?", (batch_id,))
    if devices:
        conn.executemany("INSERT INTO wug_lp_device (batch_id, query, ip, device_id, name, ok, error, worst_state, best_state, monitors_down, down_monitors, "
                         "last_change, poll_at, age_s, collected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         [(batch_id, d["query"], d["ip"] or None, d["device_id"] or None, d["name"], 1 if d["ok"] else 0, d["error"], d["worst_state"],
                           d["best_state"], d["monitors_down"], json.dumps(d["down_monitors"], ensure_ascii=False), d["last_change"],
                           _poll_text(d["poll"]) if d["poll"] else None, d["age"], d["collected_at"]) for d in devices])
    purged = purge(conn, col_t)
    devs = {v[2] for v in vals}
    note = f"上次輪循：{len(summary)} 個群組、{len(devs)} 台、{len(vals)} 筆" + ("" if parsed["complete"] else f"；不完整：{'；'.join(parsed['problems'][:3])}")
    if parsed.get("warnings"):
        note += f"；部分：{'；'.join(parsed['warnings'][:3])}"
    if parsed["complete"] and not write_members:
        note += "；比已收到的完整批次舊，不更新成員"
    return {"devices": len(devs), "points": len(vals), "note": note[:480], "purged": purged}


def purge(conn, now: datetime) -> int:
    """值與批次保留 VALUE_KEEP_DAYS 天；成員超過這個天數每天只留最後一批（永久）；核准紀錄永久保存。"""
    edge = (now - timedelta(days=VALUE_KEEP_DAYS)).strftime(FMT)
    # 每天最後一批＝跟 latest_complete 同一個排序（收集時間、再 batch_id），不看 batch_id 字串大小
    conn.execute("DELETE FROM wug_lp_member AS m WHERE m.day < ? AND EXISTS (SELECT 1 FROM wug_lp_member o WHERE o.day = m.day AND "
                 "(o.collected_at > m.collected_at OR (o.collected_at = m.collected_at AND o.batch_id > m.batch_id)))", (edge[:10],))
    n = conn.execute("DELETE FROM wug_lp_value WHERE batch_id IN (SELECT batch_id FROM wug_lp_batch WHERE collected_at < ?)", (edge,)).rowcount
    conn.execute("DELETE FROM wug_lp_device WHERE batch_id IN (SELECT batch_id FROM wug_lp_batch WHERE collected_at < ?)", (edge,))
    conn.execute("DELETE FROM wug_lp_batch WHERE collected_at < ?", (edge,))
    return n


# ---------------------------------------------------------------- 核准成員
def _insert_approval(conn, path, gid, at, by, auto, ids, batch_id, note) -> None:
    ids = sorted(set(ids))
    conn.execute("INSERT INTO wug_lp_approval (group_path, group_id, at, by, auto, count, device_ids, batch_id, note) VALUES (?,?,?,?,?,?,?,?,?)",
                 (path, gid, at, by, 1 if auto else 0, len(ids), json.dumps(ids), batch_id, note))


def _last_approval(conn, path: str) -> dict | None:
    if not _has(conn, "wug_lp_approval"):
        return None
    r = conn.execute("SELECT * FROM wug_lp_approval WHERE group_path = ? ORDER BY id DESC LIMIT 1", (path,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["ids"] = set(json.loads(d.pop("device_ids") or "[]"))
    return d


def members_of(conn, batch: dict, path: str) -> list[dict]:
    """這一批這個群組的成員（跟數值同一批）。"""
    if not _has(conn, "wug_lp_member"):
        return []
    return [dict(r) for r in conn.execute("SELECT device_id, ip, name, batch_id FROM wug_lp_member WHERE batch_id = ? AND group_path = ? ORDER BY device_id",
                                          (batch["batch_id"], path))]


def scope_check(conn, batch: dict, path: str, jump_pct: float) -> dict:
    """成員跟上一個已核准快照比：0 台、或增減（新增＋移除）超過 jump_pct% → 範圍未確認（ok=False）。"""
    cur = {m["device_id"] for m in members_of(conn, batch, path)}
    appr = _last_approval(conn, path)
    base = {"members": len(cur), "approved": appr and {k: appr[k] for k in ("at", "by", "auto", "count")}}
    if not cur:
        return {**base, "ok": False, "reason": f"範圍未確認：群組「{path}」成員 0 台"}
    if appr is None:
        return {**base, "ok": False, "reason": f"範圍未確認：群組「{path}」還沒有已核准的成員快照（設定頁按「核准目前成員」）"}
    added, removed = cur - appr["ids"], appr["ids"] - cur
    pct = (len(added) + len(removed)) * 100.0 / max(len(appr["ids"]), 1)
    base.update({"added": len(added), "removed": len(removed), "change_pct": round(pct, 1)})
    if pct > jump_pct:
        return {**base, "ok": False, "reason": (f"範圍未確認：群組「{path}」成員比已核准（{appr['at'][:16]}，{appr['count']} 台）"
                                                f"新增 {len(added)}、移除 {len(removed)} 台（{pct:.1f}% > {jump_pct:g}%），請管理員確認後在設定頁核准")}
    return {**base, "ok": True, "reason": None}


def member_diff(conn, path: str) -> dict:
    """核准前看：最新完整批次這個群組的成員 vs 上一次核准，新增／移除哪幾台（給設定頁下鑽）。"""
    b = latest_complete(conn)
    if b is None:
        raise LookupError("還沒有完整的上次輪循批次")
    cur = {m["device_id"]: m for m in members_of(conn, b, path)}
    if not cur and not any(g["path"] == path for g in b["groups"]):
        raise LookupError(f"最新完整批次裡沒有群組「{path}」")
    appr = _last_approval(conn, path)
    old = appr["ids"] if appr else set()
    rows = [{"device_id": k, "ip": cur[k]["ip"], "name": cur[k]["name"], "change": "新增"} for k in sorted(set(cur) - old)]
    rows += [{"device_id": k, "ip": None, "name": None, "change": "移除"} for k in sorted(old - set(cur))]
    return {"path": path, "batch": b["batch_id"], "members": len(cur), "approved": appr and {k: appr[k] for k in ("at", "by", "count")},
            "added": sum(1 for r in rows if r["change"] == "新增"), "removed": sum(1 for r in rows if r["change"] == "移除"), "rows": rows}


def approve(conn, path: str, by: str, now: datetime) -> dict:
    """管理員核准「最新完整批次」裡這個群組的目前成員（寫一筆核准紀錄；舊紀錄保留）。"""
    b = latest_complete(conn)
    if b is None:
        raise LookupError("還沒有完整的上次輪循批次，沒有成員可核准")
    g = next((x for x in b["groups"] if x["path"] == path and x["ok"]), None)
    if g is None:
        raise LookupError(f"最新完整批次裡沒有群組「{path}」")
    ids = [m["device_id"] for m in members_of(conn, b, path)]
    if not ids:
        raise ValueError(f"群組「{path}」成員 0 台，不能核准")
    ensure(conn)
    prev = _last_approval(conn, path)
    _insert_approval(conn, path, g.get("id"), now.strftime(FMT), by, False, ids, b["batch_id"], "管理員核准目前成員")
    conn.commit()
    return {"path": path, "count": len(set(ids)), "previous": prev["count"] if prev else None, "batch": b["batch_id"]}


# ---------------------------------------------------------------- 讀取（不建表）
def _batch_dict(r) -> dict:
    d = dict(r)
    d["problems"] = json.loads(d.pop("problems_json") or "[]")
    d["groups"] = json.loads(d.pop("groups_json") or "[]")
    return d


def latest_complete(conn) -> dict | None:
    if not _has(conn, "wug_lp_batch"):
        return None
    r = conn.execute("SELECT * FROM wug_lp_batch WHERE complete = 1 ORDER BY collected_at DESC, batch_id DESC LIMIT 1").fetchone()
    return _batch_dict(r) if r else None


def latest_any(conn) -> dict | None:
    if not _has(conn, "wug_lp_batch"):
        return None
    r = conn.execute("SELECT * FROM wug_lp_batch ORDER BY collected_at DESC, batch_id DESC LIMIT 1").fetchone()
    return _batch_dict(r) if r else None


def device_rows(conn, batch_id: str, ip: str) -> list[dict]:
    """這一批裡這個 IP 的設備狀態列（列在 wug_lp_devices.txt 才有；用 IP 或設備 ID 查到的都算）。讀取不建表。"""
    if not _has(conn, "wug_lp_device"):
        return []
    out = []
    for r in conn.execute("SELECT * FROM wug_lp_device WHERE batch_id = ? AND (ip = ? OR query = ?) ORDER BY rowid", (batch_id, ip, ip)):
        d = dict(r)
        d["down_monitors"] = json.loads(d["down_monitors"] or "[]")
        out.append(d)
    return out


def values(conn, batch_id: str, path: str, metric: str) -> list[dict]:
    if not _has(conn, "wug_lp_value"):
        return []
    return [dict(r) for r in conn.execute("SELECT device_id, ip, name, item, pct, poll_at, age_s FROM wug_lp_value "
                                          "WHERE batch_id = ? AND group_path = ? AND metric = ?", (batch_id, path, metric))]


def norm_name(s: str) -> str:
    """群組名比對鍵（收集端 Norm 同一套）：NFKC（全形括號／斜線／底線／空白轉半形）→ 去掉所有空白 → 小寫。"""
    return "".join(unicodedata.normalize("NFKC", s or "").split()).lower()


def match_group(batch: dict, want: dict) -> tuple[dict | None, str | None]:
    """設定的群組（完整路徑＋可選 ID）對最新批次：路徑（或收集端查詢名稱）一樣、有填 ID 就 ID 也要一樣；對不上＝停判。"""
    path = str((want or {}).get("path") or "").strip()
    gid = str((want or {}).get("id") or "").strip()
    if not path:
        return None, "群組未設定，停判"
    hits = [g for g in batch["groups"] if g["path"] == path or g.get("query") == path]
    if not hits:
        # 名稱寬鬆比對（NB 11-13：WhatsUp 實名「1-xServer (重要系統/交易)」括號前有半形空白，設定寫的沒有）：去空白、全形轉半形再比；
        # 設定只寫群組名（沒有「 > 」）時也比完整路徑的最後一層。比出不只一個＝停判，不猜。
        n = norm_name(path)
        hits = [g for g in batch["groups"] if n in {norm_name(g["path"]), norm_name(g.get("query") or "")}
                or (">" not in path and n == norm_name(str(g["path"]).rsplit(">", 1)[-1]))]
        if len(hits) > 1:
            return None, f"群組對不上，停判：設定「{path}」對到 {len(hits)} 個群組（{'、'.join(g['path'] for g in hits)[:200]}），請改成完整路徑"
    if not hits:
        return None, f"群組對不上，停判：設定「{path}」，最新批次沒有這個群組（回報的是：{'、'.join(g['path'] for g in batch['groups'])[:200]}）"
    g = hits[0]
    if not g["ok"]:
        return None, f"群組對不上，停判：「{path}」收集失敗：{g.get('error') or '—'}"
    if gid and str(g.get("id") or "") != gid:
        return None, f"群組對不上，停判：「{path}」設定 ID {gid}，收集端回報 ID {g.get('id') or '（沒有）'}（群組可能被搬移或重建）"
    return g, None


def status_view(conn, cfg: dict, now: datetime) -> dict:
    """設定頁：最新批次、最新完整批次、每個群組台數／IP 數／各指標筆數、核准狀態（含目前跳動比例）。"""
    any_b, comp = latest_any(conn), latest_complete(conn)
    jump = float(cfg.get("wug_member_jump_pct") or 20)
    groups = []
    if comp:
        for i, g in enumerate(comp["groups"], 1):
            sc = scope_check(conn, comp, g["path"], jump) if g["ok"] else {"ok": False, "reason": g.get("error"), "members": 0}
            appr = _last_approval(conn, g["path"])
            groups.append({"__no": i, "path": g["path"], "query": g.get("query"), "id": g.get("id"), "ok": g["ok"], "error": g.get("error"),
                           "members": g.get("members"), "ips": g.get("ips"), "cpu": (g.get("cpu") or {}).get("rows"), "mem": (g.get("mem") or {}).get("rows"),
                           "disk": (g.get("disk") or {}).get("rows"), "partial": [k for k in METRICS if (g.get(k) or {}).get("partial")], "scope_ok": sc["ok"], "scope_reason": sc.get("reason"),
                           "change_pct": sc.get("change_pct"), "approved_at": appr["at"] if appr else None, "approved_by": appr["by"] if appr else None,
                           "approved_count": appr["count"] if appr else None})

    def brief(b):
        if not b:
            return None
        at = datetime.strptime(b["collected_at"], FMT)
        return {"batch_id": b["batch_id"], "collected_at": b["collected_at"], "age_min": round((now - at).total_seconds() / 60),
                "complete": bool(b["complete"]), "problems": b["problems"]}
    return {"latest": brief(any_b), "latest_complete": brief(comp), "groups": groups, "fresh_min": cfg.get("lp_fresh_min"), "jump_pct": jump}


def approvals(conn, limit: int = 200) -> list[dict]:
    if not _has(conn, "wug_lp_approval"):
        return []
    return [{k: r[k] for k in ("id", "group_path", "group_id", "at", "by", "auto", "count", "batch_id", "note")}
            for r in conn.execute("SELECT * FROM wug_lp_approval ORDER BY id DESC LIMIT ?", (limit,))]


# ---------------------------------------------------------------- 心跳（S13 GPT 8／審查 P2-6）
HB_KEY = "wug_lp_heartbeat"           # app_settings：{"down": bool, "since", "down_day", "notified_down", "notified_up"}
HB_FROM, HB_TO, HB_STALE_MIN = "05:30", "18:00", 60


def heartbeat_view(conn, now: datetime) -> dict:
    """平日（每日檢查的不上班日除外）05:30～18:00：超過 60 分鐘沒有新的「完整」批次＝中斷（紅字原因）。從沒收到過批次時不判斷。讀取不建表。"""
    import daily_check as dc
    last_any = latest_any(conn)
    if last_any is None:
        return {"down": False, "checked": False, "reason": None}
    cfg = dc.settings(conn)
    if cfg.get("daily_check_perf_source") == "w3" or not dc.is_working_day(cfg, now.date()):
        return {"down": False, "checked": False, "reason": None}
    t = now.strftime("%H:%M")
    if not (HB_FROM <= t <= HB_TO):
        return {"down": False, "checked": False, "reason": None}
    comp = latest_complete(conn)
    at = datetime.strptime(comp["collected_at"], FMT) if comp else None
    if at is not None and (now - at) <= timedelta(minutes=HB_STALE_MIN):
        return {"down": False, "checked": True, "reason": None, "last_complete": comp["collected_at"]}
    why = (f"WhatsUp 上次輪循收集中斷：最新完整批次 {comp['collected_at']}（{int((now - at).total_seconds() // 60)} 分鐘前）"
           if comp else "WhatsUp 上次輪循收集中斷：還沒有任何完整批次")
    if last_any and (comp is None or last_any["batch_id"] != comp["batch_id"]) and last_any["problems"]:
        why += f"；最新一批 {last_any['collected_at']} 不完整：{'；'.join(last_any['problems'][:2])}"
    return {"down": True, "checked": True, "reason": why + "（9.26 排程或中繼上傳要查）", "last_complete": comp and comp["collected_at"]}


def _hb_state(conn) -> dict:
    from db import get_setting
    try:
        d = json.loads(get_setting(conn, HB_KEY, "") or "{}")
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def heartbeat_tick(conn, now: datetime) -> str:
    """中斷 → Teams 推一則（同一天只推一次）；恢復 → 再推一次。走現有 teams_notify 佇列；Teams 沒開＝只記狀態。"""
    from db import set_setting
    hb = heartbeat_view(conn, now)
    if not hb["checked"]:
        return "skip"
    st = _hb_state(conn)
    day = now.date().isoformat()
    if hb["down"] and not st.get("down"):
        if st.get("down_day") == day:
            st["down"] = True                     # 同一天第二次中斷：不再推
            set_setting(conn, HB_KEY, json.dumps(st, ensure_ascii=False))
            return "down_again"
        st = {"down": True, "since": now.strftime(FMT), "down_day": day}
        st["notified_down"] = _push(conn, now, "中斷", hb["reason"], day)
        set_setting(conn, HB_KEY, json.dumps(st, ensure_ascii=False))
        return "down"
    if not hb["down"] and st.get("down"):
        st["down"] = False
        if not st.get("notified_up_day") == day:
            st["notified_up"] = _push(conn, now, "恢復", f"已收到新的完整批次（{hb.get('last_complete')}）；中斷自 {st.get('since')}", day)
            st["notified_up_day"] = day
        set_setting(conn, HB_KEY, json.dumps(st, ensure_ascii=False))
        return "up"
    return "same"


def _push(conn, now: datetime, what: str, text: str, day: str) -> str:
    import teams_notify as tn
    try:
        tcfg = tn.load_settings(conn)
        on = tcfg["enabled"]
    except Exception:  # noqa: BLE001 - Teams 設定讀不到：只記狀態（同每日檢查的做法），畫面的紅字照樣顯示
        return "recorded"
    if not on:
        return "recorded"
    tn.ensure(conn)
    title = f"WhatsUp 上次輪循收集{what}（1.5 每日檢查）"
    tn._enqueue(conn, f"wuglp_hb:{day}:{what}", "wuglp_heartbeat", "wug_lastpoll", "warning" if what == "中斷" else "info", title,
                tn._card(title, "Attention" if what == "中斷" else "Good", [("說明", text)], link=tn._link(tcfg, "/v2/dailycheck?tab=settings")), now)
    return "queued"


# ---------------------------------------------------------------- 排程入口（每分鐘；C 類例外，見檔頭）
def import_pending(conn) -> dict:
    """只匯 wuglp_ 開頭的批次（一般中繼匯入的 15 分鐘間隔與盤中靜默窗不套用在這裡）。中繼匯入沒啟用就什麼都不做。"""
    import relay_import as ri
    from db import get_setting
    if get_setting(conn, ri.ENABLED_KEY, "0") != "1":
        return {"status": "disabled"}
    ri.ensure(conn)
    d = ri.get_config(conn)["dir"]
    if not d:
        return {"status": "no_dir"}
    folders, problem = ri.list_batch_folders(d)
    if problem:
        return {"status": "no_dir", "message": problem}
    done = {r[0] for r in conn.execute("SELECT batch_id FROM relay_import_batches WHERE source = ? AND status = 'imported'", (SOURCE,))}
    todo = [f for f in folders if f.name.startswith(BATCH_PREFIX) and f.name not in done]
    todo = [f for f in todo if ri.is_pending(conn, f)][:ri.MAX_BATCHES_PER_RUN]
    if not todo:
        return {"status": "idle"}
    if not ri._run_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        now_text = ri._now_text(None)
        res = [ri.import_batch(conn, f, "lastpoll", None, now_text, ri.get_config(conn)["max_file_mb"] * 1024 * 1024) for f in todo]
    finally:
        ri._run_lock.release()
    return {"status": "ok", "imported": sum(1 for r in res if r["status"] == "imported"), "rejected": sum(1 for r in res if r["status"] != "imported")}


def tick(conn, now: datetime | None = None) -> dict:
    """排程器每分鐘叫一次：匯入新到的上次輪循批次 → 心跳（中斷／恢復各推一則 Teams）。只讀檔案、寫本機表、排通知（C 類例外）。"""
    out = {"import": import_pending(conn)}
    try:
        import daily_check as dc
        out["heartbeat"] = heartbeat_tick(conn, now or dc.now_local())
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - 心跳是附加功能，失敗只記日誌，不影響匯入
        log.error("上次輪循心跳失敗：%s", type(exc).__name__)
        if conn.in_transaction:
            conn.rollback()
    return out
