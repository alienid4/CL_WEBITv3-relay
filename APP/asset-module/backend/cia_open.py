"""S4 未結案清單（2.061.3）：給稽核一頁看完所有 CIA 治理的未結案＋新案／逾期／例外到期＋掃描涵蓋範圍。

設計：docs/設計_CIA第三批_S1toS4_2026-11-11.md「S4」＋ GPT 第 8（只是視圖，不是新的生命週期）、9（涵蓋率固定分母）點。

## 一頁看完（只讀，不寫庫）
來源：9.3 修正包（待決定、同意等套用）、9.4 補登（未結案的各狀態）、共用案件（同機多筆、實體機補登、機櫃編號：待確認、已確認、已交出）、
彙整表比對（表有 CIA 無、欄位不一致）。每一列有「從哪天起算」。
- 新案＝第一次出現在最近 new_days（預設 7）天內。
- 逾期＝已交出去等人回（修正包已產包、補登已分派或退回、案件已交出）超過 overdue_days（預設 14，可設）；**退回時重算**（補登看最後一次分派／退回的時間）。
- 例外到期＝標了例外（確認不是／不處理）而到期日已過：案件自動回「待確認」並記「例外到期」事件（cia_case.expire_exceptions），
  在它被重新處理之前（最後一筆事件仍是「例外到期」）列在這張卡，要重審。
卡片＝清單篩選後的列數，同一份計算（verify_numbers 釘住）。

## 掃描涵蓋範圍（GPT 第 9 點）
分母固定＝2.1「全部登記台數」（machine_key、現役）；截點＝頁面計算時間。每個來源列：最後資料日期、涵蓋台數、未涵蓋台數、無法比對（來源有、對不到任何一台）。
讓稽核看得出「沒有差異」是真的沒有，還是沒掃到。WhatsUp 沒有逐台清單，只列最後一次輪詢時間，不算涵蓋。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

SET_OVERDUE = "cia_open_overdue_days"
NEW_DAYS = 7
DEFAULT_OVERDUE = 14
VIEWS = {"all": "全部未結案", "new": "新案", "overdue": "逾期", "exc_due": "例外到期"}


def _setting_int(conn, key: str, default: int) -> int:
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return max(1, int(str(r[0]).strip('"'))) if r and r[0] else default
    except (sqlite3.Error, ValueError):
        return default       # 設定壞掉或還沒建：用預設


def save_overdue_days(conn, days: int) -> int:
    d = int(days)
    if not 1 <= d <= 365:
        raise ValueError("逾期天數要在 1～365 之間")
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (SET_OVERDUE, str(d)))
    conn.commit()
    return d


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _days(since: str | None, now: datetime) -> int | None:
    try:
        return (now - datetime.strptime(str(since)[:19], "%Y-%m-%d %H:%M:%S")).days
    except (TypeError, ValueError):
        try:
            return (now - datetime.strptime(str(since)[:10], "%Y-%m-%d")).days
        except (TypeError, ValueError):
            return None


def _trail(conn, sql: str, labels: dict) -> dict[int, list[str]]:
    """事件紀錄（稽核第九節：誰、哪天、做了什麼）→ 每筆一串「MM-DD 動作（誰）」。"""
    out: dict[int, list[str]] = {}
    for r in conn.execute(sql):
        out.setdefault(r[0], []).append(f"{str(r[1])[:10]} {labels.get(r[3], r[3])}（{r[2] or '系統'}）")
    return out


def rows(conn, now: datetime | None = None) -> tuple[list[dict], dict]:
    """全部未結案一列一筆＋例外（到期的）。回 (rows, params)。"""
    now = now or datetime.now()
    overdue = _setting_int(conn, SET_OVERDUE, DEFAULT_OVERDUE)
    out: list[dict] = []

    def add(src, ref, name, status, first_seen, waiting_since, *, link, exc_until=None, exc_due=False, trail=None):
        age_first = _days(first_seen, now)
        wait = _days(waiting_since, now) if waiting_since else None
        out.append({"source": src, "ref": ref, "name": name or "—", "status": status, "first_seen": (first_seen or "")[:16],
                    "waiting_since": (waiting_since or "")[:16] or None, "wait_days": wait, "link": link,
                    "is_open": True, "new": bool(age_first is not None and age_first < NEW_DAYS),
                    "overdue": bool(wait is not None and wait > overdue), "exc_due": exc_due, "exception_until": exc_until if exc_due else None,
                    "trail": "；".join(trail or []) or "—"})

    if _has(conn, "cia_fix_item"):
        import cia_fix
        # 只讀未結案那幾筆的事件（審查 P3-9：不整張事件表讀進來）
        tr_fix = _trail(conn, "SELECT item_id, at, by, action FROM cia_fix_event WHERE item_id IN "
                              "(SELECT id FROM cia_fix_item WHERE status IN ('pending','agreed')) ORDER BY id", cia_fix.ACTION_LABEL)
        for r in conn.execute("SELECT i.id, i.track_code, i.asset_serial, i.hostname, i.field, i.status, i.rule_id, b.created_at "
                              "FROM cia_fix_item i JOIN cia_fix_batch b ON b.id = i.batch_id WHERE i.status IN ('pending','agreed') ORDER BY i.id"):
            add("9.3 修正包", r["track_code"], f"{r['hostname'] or r['asset_serial']}・{r['field']}", cia_fix.STATUS[r["status"]],
                r["created_at"], r["created_at"], link=f"/v2/ciafix?tab=items&st={r['status']}", trail=tr_fix.get(r["id"]))
    if _has(conn, "cia_reg_item"):
        import cia_reg
        ph = ",".join("?" * len(cia_reg.OPEN))
        tr_reg = _trail(conn, f"SELECT item_id, at, by, action FROM cia_reg_event WHERE item_id IN "
                              f"(SELECT id FROM cia_reg_item WHERE status IN ({','.join(repr(s) for s in cia_reg.OPEN)})) ORDER BY id",
                        cia_reg.ACTION_LABEL)
        last = {r[0]: r[1] for r in conn.execute("SELECT item_id, MAX(at) FROM cia_reg_event WHERE to_status IN ('assigned','bounced') GROUP BY item_id")}
        for r in conn.execute(f"SELECT id, track_code, hostname, ip, status, created_at FROM cia_reg_item WHERE status IN ({ph}) ORDER BY id", cia_reg.OPEN):
            waiting = last.get(r["id"]) if r["status"] in ("assigned", "bounced") else None
            add("9.4 補登", r["track_code"], r["hostname"] or r["ip"], cia_reg.STATUS[r["status"]], r["created_at"], waiting,
                link=f"/v2/ciareg?tab=items&st={r['status']}", trail=tr_reg.get(r["id"]))
    if _has(conn, "cia_case"):
        import cia_case
        tr_case = _trail(conn, "SELECT case_id, at, by, action FROM cia_case_event WHERE case_id IN "
                               "(SELECT id FROM cia_case WHERE status IN ('open','confirmed','handed')) ORDER BY id", cia_case.ACTION_LABEL)
        # 每案最後一筆事件（例外到期判斷）：只查未結案的
        last_ev = {r[0]: r[1] for r in conn.execute(
            "SELECT e.case_id, e.action FROM cia_case_event e JOIN (SELECT case_id, MAX(id) AS mid FROM cia_case_event GROUP BY case_id) x "
            "ON x.mid = e.id JOIN cia_case c ON c.id = e.case_id WHERE c.status IN ('open','confirmed','handed')")}
        page = {"dup": "/v2/ciafix?tab=dup", "rack": "/v2/ciafix?tab=rack", "physical": "/v2/ciareg?tab=phys"}
        for r in conn.execute("SELECT * FROM cia_case WHERE status IN ('open','confirmed','handed') ORDER BY id"):
            add(cia_case.KINDS.get(r["kind"], r["kind"]), f"{r['kind'][0].upper()}{r['id']}", r["title"], cia_case.STATUS[r["status"]],
                r["first_seen"], r["handed_at"] if r["status"] == "handed" else None,
                link=f"{page.get(r['kind'], '/v2/ciafix')}&cst={r['status']}",
                exc_due=last_ev.get(r["id"]) == "expire", exc_until=r["exception_until"], trail=tr_case.get(r["id"]))
    if _has(conn, "srv_sheet_batch"):
        import srv_sheet
        a = srv_sheet.analyze(conn)
        b = a.get("batch") or {}
        for r in a["rows"]:
            ks = [k for k in r["kinds"] if k in ("no_cia", "diff")]
            if ks:
                add("彙整表比對", f"列 {r['row_no']}", r["host"] or r["ip"], "、".join(srv_sheet.KINDS[k] for k in ks),
                    b.get("imported_at"), None, link=f"/v2/ciareg?tab=srv&sk={ks[0]}")
    for i, r in enumerate(out, 1):
        r["__no"] = i
    return out, {"overdue_days": overdue, "new_days": NEW_DAYS, "as_of": now.strftime("%Y-%m-%d %H:%M:%S")}


def pick(rs: list[dict], view: str) -> list[dict]:
    if view not in VIEWS:
        raise ValueError(f"不認得的清單：{view}")
    if view == "all":
        return [r for r in rs if r["is_open"]]
    return [r for r in rs if r[view]]


def summary(conn, now: datetime | None = None, computed: tuple | None = None) -> dict:
    """computed＝已算好的 rows() 結果（同一個請求裡不重算，審查 P3-9）。"""
    rs, params = computed or rows(conn, now)
    by_src: dict[str, int] = {}
    for r in pick(rs, "all"):
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
    return {"counts": {v: len(pick(rs, v)) for v in VIEWS}, "by_source": by_src, **params}


def coverage(conn, now: datetime | None = None) -> dict:
    """掃描涵蓋範圍：分母＝2.1 全部登記台數（現役、machine_key）。"""
    import existence
    import v2_hosts

    now = now or datetime.now()
    ms = v2_hosts.machines(conn)
    keys = {m["key"] for m in ms}
    total = len(keys)
    out = []
    import cia_fix
    ci = cia_fix._last_cia_import(conn)
    cov = sum(1 for m in ms if m["has_cia"])
    out.append({"source": "CIA 資產清冊", "date": ci["data_date"], "covered": cov, "uncovered": total - cov, "unmatched": None})
    if _has(conn, "srv_sheet_batch"):
        import srv_sheet
        a = srv_sheet.analyze(conn)
        hit = {r["machine_key"] for r in a["rows"] if r.get("machine_key")} & keys
        um = sum(1 for r in a["rows"] if not r.get("machine_key"))
        b = a.get("batch") or {}
        out.append({"source": "伺服器資料彙整表", "date": (b.get("data_date") or b.get("imported_at") or "")[:10] or None,
                    "covered": len(hit), "uncovered": total - len(hit), "unmatched": um})
    else:
        out.append({"source": "伺服器資料彙整表", "date": None, "covered": 0, "uncovered": total, "unmatched": None})
    ex = existence.compute(conn)
    snaps = {s["source"]: s for s in ex["summary"].get("sources", [])} if isinstance(ex.get("summary"), dict) else {}
    if not snaps:
        snaps = {s["source"]: s for s in existence.source_snapshots(conn)}
    for code, src, label in (("inv:vcenter", "vcenter", "vCenter／RVTools"), ("inv:dynassets", "dynassets", "DY"), ("scan", "scan", "存活掃描")):
        hit = {m["key"] for m in ex["machines"] if (m.get("last_seen") or {}).get(code)} & keys
        s = snaps.get(src) or {}
        out.append({"source": label, "date": (s.get("latest") or "")[:10] or None, "covered": len(hit), "uncovered": total - len(hit), "unmatched": None})
    wr = None
    if _has(conn, "whatsup_runs"):
        wr = conn.execute("SELECT MAX(at) FROM whatsup_runs WHERE status IN ('ok','partial')").fetchone()[0]
    out.append({"source": "WhatsUp", "date": (wr or "")[:10] or None, "covered": None, "uncovered": None, "unmatched": None,
                "note": "沒有逐台清單，只列最後一次輪詢"})
    for i, r in enumerate(out, 1):
        r["__no"] = i
    return {"total": total, "as_of": now.strftime("%Y-%m-%d %H:%M:%S"), "sources": out}
