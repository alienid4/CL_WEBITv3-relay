"""VMware 每日檢查快照（2.064.0）：跳板機的 aria_daily_snap.ps1 讀 Aria Operations（vROps）→ 這裡存表 → 1.5 每日檢查 VC 列預填。

設計：docs/設計_每日檢查全表範圍_含VMware_2026-11-12.md（§3 做法、§5 GPT 30 問的接受項）；人工檢查表正本 2-1～2-4。

## 天條
ESXi／vCenter 不可納管、不可建帳號。這支只讀中繼資料夾裡收集端放進來的檔案、寫本機表；不連 vROps、不連 vCenter、不連任何 ESXi。
收集端（跳板機）也只對 vROps suite-api 做 GET（外加一次登入取權杖），不碰 ESXi／vCenter 本身。

## 資料怎麼進來
資料夾 ariadc_<時間>：snap.json＋manifest.json（source=aria_dailycheck，最後寫）。snap.json 五類，各自 {ok, error, total, rows}：
adapters（vROps 的 vCenter 收集器狀態與最後收集時間）、hosts（HostSystem：連線、維護）、datastores（類型、容量、剩餘、掛載主機數）、
alerts（目前未解警示；total＝vROps 回報的 active 總數、fetched＝實際讀到的筆數、rows＝依等級清單留下的）、aria（登入）。
每筆都帶 vROps 資源 ID、vcid（vCenter 識別，VMEntityVCID）與逐筆採集時間（毫秒）。

## 完整性（防假正常）
任何一類 ok=false、分頁沒讀完（total≠筆數）、manifest 筆數對不上、ESXi／資料存放區／收集器／警示（vROps 全部 active）零筆 → 照存但 complete=0、problems 記原因；
每日檢查只拿最新且完整的一批。

## 快速通道
跟上次輪循同級（C 類例外 aria_dailycheck_tick）：scan_service 每分鐘呼叫 tick，只撿 ariadc_ 開頭的批次；一般 relay_import 排程跳過。
匯入新的完整批次後，若今天的每日檢查還沒人確認任何一列，就重新產生一次（新版本，舊版標作廢；daily_check.vm_refresh）。

## 表（保留 KEEP_DAYS 天）
aria_dc_batch／aria_dc_adapter／aria_dc_host／aria_dc_ds／aria_dc_alert。讀取函式不建表；寫入路徑才 ensure。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

log = logging.getLogger("aria_dailycheck")

SOURCE = "aria_dailycheck"
BATCH_PREFIX = "ariadc_"
FMT = "%Y-%m-%d %H:%M:%S"
KEEP_DAYS = 30
RULE_VERSION = "aria_dailycheck 1"
KINDS = ("adapters", "hosts", "datastores", "alerts")
KIND_LABEL = {"adapters": "vROps 收集器", "hosts": "ESXi 主機", "datastores": "資料存放區", "alerts": "警示", "aria": "vROps 登入"}
MAX_ROWS = 200_000

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS aria_dc_batch (
        batch_id TEXT PRIMARY KEY, collected_at TEXT NOT NULL, imported_at TEXT NOT NULL, collector_version TEXT,
        complete INTEGER NOT NULL, problems_json TEXT, summary_json TEXT, file_sha TEXT, raw_sha_json TEXT, rule_version TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_aria_dc_batch_at ON aria_dc_batch(collected_at)",
    """CREATE TABLE IF NOT EXISTS aria_dc_adapter (
        batch_id TEXT NOT NULL, adapter_id TEXT NOT NULL, name TEXT, vcid TEXT, last_collected TEXT, last_heartbeat TEXT, message TEXT,
        PRIMARY KEY (batch_id, adapter_id))""",
    """CREATE TABLE IF NOT EXISTS aria_dc_host (
        batch_id TEXT NOT NULL, res_id TEXT NOT NULL, name TEXT, vcid TEXT, conn TEXT, maint TEXT, collected_at TEXT,
        PRIMARY KEY (batch_id, res_id))""",
    """CREATE TABLE IF NOT EXISTS aria_dc_ds (
        batch_id TEXT NOT NULL, res_id TEXT NOT NULL, name TEXT, vcid TEXT, ds_type TEXT, cap_gb REAL, free_gb REAL, hosts INTEGER, local INTEGER, collected_at TEXT,
        PRIMARY KEY (batch_id, res_id))""",
    """CREATE TABLE IF NOT EXISTS aria_dc_alert (
        batch_id TEXT NOT NULL, alert_id TEXT NOT NULL, res_id TEXT, res_kind TEXT, res_name TEXT, vcid TEXT, name TEXT, level TEXT,
        status TEXT, control TEXT, owner TEXT, start_at TEXT, updated_at TEXT, PRIMARY KEY (batch_id, alert_id))""",
]


def ensure(conn: sqlite3.Connection) -> None:
    for s in SCHEMA:
        conn.execute(s)


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _tpe(d: datetime) -> datetime:
    return d.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)


def ms_text(v) -> str | None:
    """vROps 的毫秒時間 → 台北時間文字；沒有就 None。"""
    if v is None:
        return None
    return _tpe(datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc)).strftime(FMT)


# ---------------------------------------------------------------- parse（結構壞＝ValueError 整批拒收；內容缺＝照收、標不完整）
def _text(v, n: int = 300) -> str:
    return "" if v is None else str(v)[:n]


def _num(v, what: str, allow_neg: bool = False):
    if v is None or v == "":
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{what} 不是數字：{str(v)[:20]}")
    if not allow_neg and v < 0:
        raise ValueError(f"{what} 是負的")
    return v


def _ms(v, what: str):
    v = _num(v, what)
    return None if v is None else int(v)


def _flag(v):
    """vROps 的「本機」屬性：true／false（布林或文字）；其他＝不知道（None）。"""
    if isinstance(v, bool):
        return v
    t = str(v or "").strip().lower()
    return True if t == "true" else False if t == "false" else None


def _block(data: dict, k: str) -> dict:
    b = data.get(k)
    if not isinstance(b, dict) or not isinstance(b.get("ok"), bool):
        raise ValueError(f"{k}：要是 {{ok, error, total, rows}}")
    rows = b.get("rows")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ValueError(f"{k}：rows 要是清單")
    return {"ok": b["ok"], "error": _text(b.get("error")), "total": b.get("total"), "fetched": b.get("fetched"), "rows": rows if b["ok"] else []}


def _row_id(r, k: str, i: int) -> str:
    if not isinstance(r, dict) or not _text(r.get("id")).strip():
        raise ValueError(f"{k} 第 {i} 筆沒有 id")
    return _text(r.get("id"), 80).strip()


def parse(data, manifest: dict | None = None) -> dict:
    if not isinstance(data, dict):
        raise ValueError("snap.json 最外層要是物件")
    aria = data.get("aria")
    if not isinstance(aria, dict) or not isinstance(aria.get("ok"), bool):
        raise ValueError("aria：要是 {ok, error}")
    out = {"aria": {"ok": aria["ok"], "error": _text(aria.get("error"))}, "dups": {}}
    problems: list[str] = []
    if not aria["ok"]:
        problems.append(f"vROps 登入失敗：{out['aria']['error'] or '—'}")
    n = 0
    for k in KINDS:
        b = _block(data, k)
        seen, rows, dup = set(), [], 0
        for i, r in enumerate(b["rows"], 1):
            rid = _row_id(r, k, i)
            if rid in seen:
                dup += 1                                  # 同一個 vROps 資源 ID 出現兩次：只算一次（GPT 17）
                continue
            seen.add(rid)
            vcid = _text(r.get("vcid"), 120).strip()
            if k == "adapters":
                rows.append({"id": rid, "name": _text(r.get("name"), 255), "vcid": vcid,
                             "last_collected": ms_text(_ms(r.get("last_collected"), f"{k} last_collected")),
                             "last_heartbeat": ms_text(_ms(r.get("last_heartbeat"), f"{k} last_heartbeat")), "message": _text(r.get("message"))})
            elif k == "hosts":
                rows.append({"id": rid, "name": _text(r.get("name"), 255), "vcid": vcid, "conn": _text(r.get("conn"), 60),
                             "maint": _text(r.get("maint"), 60), "at": ms_text(_ms(r.get("at"), f"{k} at"))})
            elif k == "datastores":
                h = r.get("hosts")
                if h is not None and (isinstance(h, bool) or not isinstance(h, int) or h < 0):
                    raise ValueError(f"datastores {rid}：hosts（掛載主機數）要是 0 以上的整數")
                rows.append({"id": rid, "name": _text(r.get("name"), 255), "vcid": vcid, "type": _text(r.get("type"), 40),
                             "cap_gb": _num(r.get("cap_gb"), f"{rid} cap_gb"), "free_gb": _num(r.get("free_gb"), f"{rid} free_gb"),
                             "hosts": h, "local": _flag(r.get("local")), "at": ms_text(_ms(r.get("at"), f"{k} at"))})
            else:
                rows.append({"id": rid, "res_id": _text(r.get("res_id"), 80), "res_kind": _text(r.get("res_kind"), 60),
                             "res_name": _text(r.get("res_name"), 255), "vcid": vcid, "name": _text(r.get("name"), 300),
                             "level": _text(r.get("level"), 20).upper(), "status": _text(r.get("status"), 20).upper(),
                             "control": _text(r.get("control"), 20).upper(), "owner": _text(r.get("owner"), 120),
                             "start": ms_text(_ms(r.get("start"), "alerts start")), "updated": ms_text(_ms(r.get("updated"), "alerts updated"))})
        n += len(rows)
        if n > MAX_ROWS:
            raise ValueError(f"一批超過 {MAX_ROWS} 筆")
        b["rows"] = rows
        out[k] = b
        out["dups"][k] = dup
        lab = KIND_LABEL[k]
        if not b["ok"]:
            if aria["ok"]:
                problems.append(f"{lab}讀取失敗：{b['error'] or '—'}")
            continue
        # 分頁讀到底＋總數核對（GPT 5）：收集端回報的總數（vROps pageInfo.totalCount）≠ 讀到的筆數 → 不完整
        got = (b["fetched"] if k == "alerts" else len(rows) + dup)
        if not isinstance(b["total"], int) or isinstance(b["total"], bool) or (k == "alerts" and not isinstance(got, int)):
            problems.append(f"{lab}：沒有總數，無法確認分頁讀完")
        elif got != b["total"]:
            problems.append(f"{lab}：分頁不完整（vROps 說 {b['total']} 筆，讀到 {got} 筆）")
        if k == "alerts" and isinstance(got, int) and len(rows) > got:
            problems.append("警示：留下的筆數比讀到的還多")
        # 零筆阻擋（GPT 6）：警示看的是 vROps 全部 active 筆數（等級篩選前），篩完 0 筆是正常的
        zero = (b["total"] == 0) if k == "alerts" else not rows
        if zero:
            problems.append(f"{lab} 0 筆（不當作「沒有問題」）")
    problems += _check_counts(out, (manifest or {}).get("counts"))
    out["complete"] = not problems
    out["problems"] = problems
    return out


def _check_counts(p: dict, counts) -> list[str]:
    """manifest 的筆數（收集端寫檔前數的，含重複）對 snap.json 實際內容（去重前）；對不上＝檔案被截斷或半寫入。"""
    if not isinstance(counts, dict):
        return ["manifest 沒有各類筆數（counts），無法確認這批是完整的"]
    bad = []
    for k in KINDS:
        if not p[k]["ok"]:
            continue
        want = counts.get(k)
        got = len(p[k]["rows"]) + p["dups"].get(k, 0)
        if want != got:
            bad.append(f"{KIND_LABEL[k]}：manifest {want} 筆、檔案 {got} 筆，對不上")
    return bad


# ---------------------------------------------------------------- apply（relay 匯入交易內；呼叫端提交）
def apply(conn, batch_id: str, collected_at: str, collector_version: str, parsed: dict, now_text: str, file_sha: str | None = None,
          raw_sha=None) -> dict:
    import relay_import as ri
    ensure(conn)
    col_t = _tpe(ri.parse_time(collected_at, "Asia/Taipei"))
    col_s = col_t.strftime(FMT)
    for t in ("aria_dc_adapter", "aria_dc_host", "aria_dc_ds", "aria_dc_alert"):
        conn.execute(f"DELETE FROM {t} WHERE batch_id = ?", (batch_id,))  # noqa: S608 - 表名是上面固定清單
    conn.executemany("INSERT INTO aria_dc_adapter (batch_id, adapter_id, name, vcid, last_collected, last_heartbeat, message) VALUES (?,?,?,?,?,?,?)",
                     [(batch_id, r["id"], r["name"], r["vcid"] or None, r["last_collected"], r["last_heartbeat"], r["message"] or None)
                      for r in parsed["adapters"]["rows"]])
    conn.executemany("INSERT INTO aria_dc_host (batch_id, res_id, name, vcid, conn, maint, collected_at) VALUES (?,?,?,?,?,?,?)",
                     [(batch_id, r["id"], r["name"], r["vcid"] or None, r["conn"], r["maint"], r["at"]) for r in parsed["hosts"]["rows"]])
    conn.executemany("INSERT INTO aria_dc_ds (batch_id, res_id, name, vcid, ds_type, cap_gb, free_gb, hosts, local, collected_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     [(batch_id, r["id"], r["name"], r["vcid"] or None, r["type"], r["cap_gb"], r["free_gb"], r["hosts"],
                       None if r["local"] is None else int(r["local"]), r["at"])
                      for r in parsed["datastores"]["rows"]])
    conn.executemany("INSERT INTO aria_dc_alert (batch_id, alert_id, res_id, res_kind, res_name, vcid, name, level, status, control, owner, start_at, updated_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     [(batch_id, r["id"], r["res_id"], r["res_kind"], r["res_name"], r["vcid"] or None, r["name"], r["level"], r["status"],
                       r["control"], r["owner"], r["start"], r["updated"]) for r in parsed["alerts"]["rows"]])
    summary = {"aria": parsed["aria"], **{k: {"ok": parsed[k]["ok"], "error": parsed[k]["error"], "total": parsed[k]["total"],
                                             "fetched": parsed[k]["fetched"], "rows": len(parsed[k]["rows"]), "dups": parsed["dups"].get(k, 0)} for k in KINDS}}
    conn.execute("INSERT OR REPLACE INTO aria_dc_batch (batch_id, collected_at, imported_at, collector_version, complete, problems_json, summary_json, "
                 "file_sha, raw_sha_json, rule_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (batch_id, col_s, now_text, collector_version, 1 if parsed["complete"] else 0, json.dumps(parsed["problems"], ensure_ascii=False),
                  json.dumps(summary, ensure_ascii=False), file_sha, json.dumps(raw_sha or [], ensure_ascii=False), RULE_VERSION))
    purged = purge(conn, col_t)
    n = {k: len(parsed[k]["rows"]) for k in KINDS}
    note = (f"VMware 每日檢查：收集器 {n['adapters']}、ESXi {n['hosts']}、資料存放區 {n['datastores']}、警示 {n['alerts']}"
            + ("" if parsed["complete"] else f"；不完整：{'；'.join(parsed['problems'][:3])}"))
    return {"devices": n["hosts"], "points": sum(n.values()), "note": note[:480], "purged": purged}


def purge(conn, now: datetime) -> int:
    edge = (now - timedelta(days=KEEP_DAYS)).strftime(FMT)
    n = 0
    for t in ("aria_dc_adapter", "aria_dc_host", "aria_dc_ds", "aria_dc_alert"):
        n += conn.execute(f"DELETE FROM {t} WHERE batch_id IN (SELECT batch_id FROM aria_dc_batch WHERE collected_at < ?)", (edge,)).rowcount  # noqa: S608
    conn.execute("DELETE FROM aria_dc_batch WHERE collected_at < ?", (edge,))
    return n


# ---------------------------------------------------------------- 讀取（不建表）
def _batch_dict(r) -> dict:
    d = dict(r)
    d["problems"] = json.loads(d.pop("problems_json") or "[]")
    d["summary"] = json.loads(d.pop("summary_json") or "{}")
    d["raw_sha"] = json.loads(d.pop("raw_sha_json") or "[]")
    return d


def latest_complete(conn) -> dict | None:
    if not _has(conn, "aria_dc_batch"):
        return None
    r = conn.execute("SELECT * FROM aria_dc_batch WHERE complete = 1 ORDER BY collected_at DESC, batch_id DESC LIMIT 1").fetchone()
    return _batch_dict(r) if r else None


def latest_any(conn) -> dict | None:
    if not _has(conn, "aria_dc_batch"):
        return None
    r = conn.execute("SELECT * FROM aria_dc_batch ORDER BY collected_at DESC, batch_id DESC LIMIT 1").fetchone()
    return _batch_dict(r) if r else None


def adapters(conn, batch_id: str) -> list[dict]:
    if not _has(conn, "aria_dc_adapter"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM aria_dc_adapter WHERE batch_id = ? ORDER BY name, adapter_id", (batch_id,))]


def hosts(conn, batch_id: str, vcid: str) -> list[dict]:
    if not _has(conn, "aria_dc_host"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM aria_dc_host WHERE batch_id = ? AND vcid = ? ORDER BY name, res_id", (batch_id, vcid))]


def datastores(conn, batch_id: str, vcid: str) -> list[dict]:
    if not _has(conn, "aria_dc_ds"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM aria_dc_ds WHERE batch_id = ? AND vcid = ? ORDER BY name, res_id", (batch_id, vcid))]


def unassigned(conn, batch_id: str, known: set) -> dict:
    """無法歸屬 vCenter（P1）：vcid 空、或不是任何已知 vCenter（設定的＋收集器回報的）的警示與資料存放區。不可以默默消失。"""
    out = {"alerts": [], "datastores": []}
    if _has(conn, "aria_dc_alert"):
        out["alerts"] = [dict(r) for r in conn.execute("SELECT * FROM aria_dc_alert WHERE batch_id = ? ORDER BY level, name, alert_id", (batch_id,))
                         if (r["vcid"] or "") not in known]
    if _has(conn, "aria_dc_ds"):
        out["datastores"] = [dict(r) for r in conn.execute("SELECT * FROM aria_dc_ds WHERE batch_id = ? ORDER BY name, res_id", (batch_id,))
                             if (r["vcid"] or "") not in known]
    return out


def alerts(conn, batch_id: str, vcid: str) -> list[dict]:
    if not _has(conn, "aria_dc_alert"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM aria_dc_alert WHERE batch_id = ? AND vcid = ? ORDER BY level, name, alert_id", (batch_id, vcid))]


def vcid_overview(conn, batch_id: str) -> list[dict]:
    """設定頁用：這批看到哪些 vCenter（vcid）、各幾台 ESXi／資料存放區、例子、對到的收集器。給管理員抄進設定。"""
    if not _has(conn, "aria_dc_host"):
        return []
    out: dict = {}
    for r in conn.execute("SELECT vcid, COUNT(*) n, MIN(name) ex FROM aria_dc_host WHERE batch_id = ? GROUP BY vcid", (batch_id,)):
        out[r["vcid"] or ""] = {"vcid": r["vcid"] or "", "hosts": r["n"], "example": r["ex"], "datastores": 0, "adapter": None}
    for r in conn.execute("SELECT vcid, COUNT(*) n FROM aria_dc_ds WHERE batch_id = ? GROUP BY vcid", (batch_id,)):
        out.setdefault(r["vcid"] or "", {"vcid": r["vcid"] or "", "hosts": 0, "example": None, "datastores": 0, "adapter": None})["datastores"] = r["n"]
    for a in adapters(conn, batch_id):
        if a["vcid"] in out:
            out[a["vcid"]]["adapter"] = a["name"]
    return [dict(v, __no=i) for i, v in enumerate(sorted(out.values(), key=lambda x: x["vcid"]), 1)]


def status_view(conn, now: datetime) -> dict:
    any_b, comp = latest_any(conn), latest_complete(conn)

    def brief(b):
        if not b:
            return None
        at = datetime.strptime(b["collected_at"], FMT)
        return {"batch_id": b["batch_id"], "collected_at": b["collected_at"], "age_min": round((now - at).total_seconds() / 60),
                "complete": bool(b["complete"]), "problems": b["problems"], "summary": b["summary"], "file_sha": b.get("file_sha")}
    ads = [dict(a, __no=i) for i, a in enumerate(adapters(conn, comp["batch_id"]), 1)] if comp else []
    return {"latest": brief(any_b), "latest_complete": brief(comp), "vcenters": vcid_overview(conn, comp["batch_id"]) if comp else [], "adapters": ads}


# ---------------------------------------------------------------- 排程入口（每分鐘；C 類例外 aria_dailycheck_tick）
def import_pending(conn) -> dict:
    """只匯 ariadc_ 開頭的批次。中繼匯入沒啟用就什麼都不做。"""
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
        res = [ri.import_batch(conn, f, "aria_dailycheck", None, now_text, ri.get_config(conn)["max_file_mb"] * 1024 * 1024) for f in todo]
    finally:
        ri._run_lock.release()
    return {"status": "ok", "imported": sum(1 for r in res if r["status"] == "imported"), "rejected": sum(1 for r in res if r["status"] != "imported")}


def tick(conn, now: datetime | None = None) -> dict:
    """排程器每分鐘叫一次：匯入新到的 VMware 批次 → 有新的完整批次就讓每日檢查判斷要不要重新產生（還沒人確認才重產）。
    只讀檔案、寫本機表（C 類例外）；不連 vROps、不連 vCenter／ESXi。"""
    out = {"import": import_pending(conn)}
    if out["import"].get("imported"):
        try:
            import daily_check as dc
            out["refresh"] = dc.vm_refresh(conn, now or dc.now_local())
        except Exception as exc:  # noqa: BLE001 - 重新產生是附加功能，失敗只記日誌（畫面會顯示「有較新的 VMware 資料」，人可以手動重新產生）
            log.error("VMware 補抓後重新產生每日檢查失敗：%s", type(exc).__name__)
            if conn.in_transaction:
                conn.rollback()
    return out
