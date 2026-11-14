"""CIA 第三批共用案件表（2.061.0）：同機多筆（S3）、實體機補登（S1）、機櫃編號（S2）共用一套「案件＋事件＋問題指紋」。

設計：docs/設計_CIA第三批_S1toS4_2026-11-11.md（GPT 質疑第 1、2、8、10 點）。

## 為什麼要有
S1～S3 都是「系統找出一個問題 → 人確認 → 交 9.3 修正包／9.4 補登給 CIA 管理人 → CIA 匯入後結案」。
各寫一套狀態機會分岔（S4 未結案清單就對不起來），所以先做這張共用表，三種問題都放進來。

## 問題指紋（GPT 第 10 點）
指紋＝類別＋machine_key＋欄位＋原值（sha256）。同一個問題不管掃幾次都是同一案：已送過的不重送、新出現的才是新案。
每案另存「當時用哪些欄位配對到這台」與配對時間（GPT 第 2 點），machine_key 規則日後變了看得出來。

## 狀態（GPT 第 8 點：S4 只是這些狀態上的視圖）
待確認(open) ─確認→ 已確認(confirmed) ─交出→ 已交出(handed) ─修正包／補登結案→ 已結案(closed)
已確認 ─撤回→ 待確認（撤回＝新增一筆反向事件，不改舊事件；GPT 第 1 點）
待確認／已確認 ─標例外（不是同一台／不處理，要到期日）→ 例外(exception) ─取消→ 待確認；**到期 → 自動回待確認（事件「例外到期」，S4 列要重審）**
重新比對時問題不見了（待確認／已確認／例外）→ 已不存在(resolved)；之後又出現 → 待確認（同一案，事件記「再次出現」）
**確認時把人確認的內容（原值、新值、各欄取的值）存進 decision_json**；之後重新比對只更新 detail（看到的現況），
交出一律用 decision，現況跟確認時不一樣就不交（要重新確認）——不會把沒人確認過的值送出去（審查 P1）。
確認、撤回、例外、交出、重新比對＝管理員（access_policy ADMIN_WRITE）。

## 不做的事（D4）
不寫 hardware、不改 CIA。交出＝產生 9.3 修正包的一個批次（cia_fix 的表），CIA 正本只由 CIA 匯入寫入。
讀取函式不建表（沒用過的庫一律回空）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta

KINDS = {"dup": "同機多筆", "physical": "實體機補登", "rack": "機櫃編號"}
STATUS = {"open": "待確認", "confirmed": "已確認", "handed": "已交出", "closed": "已結案",
          "exception": "例外（不處理）", "resolved": "已不存在"}
#: 未結案（S4 會列）
OPEN = ("open", "confirmed", "handed")
ACTION_LABEL = {"found": "系統找到", "reappear": "再次出現", "gone": "重新比對：已不存在", "confirm": "確認", "withdraw": "撤回確認",
                "except": "標例外", "unexcept": "取消例外", "handed": "交出", "closed": "結案", "expire": "例外到期（要重審）"}
#: 例外最長多久要重審（天）；沒給到期日就用預設
EXCEPTION_DEFAULT_DAYS = 180
EXCEPTION_MAX_DAYS = 730

SCHEMA = """
CREATE TABLE IF NOT EXISTS cia_case (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    kind             TEXT NOT NULL,          -- dup／physical／rack
    fingerprint      TEXT NOT NULL,          -- sha256(類別｜machine_key｜欄位｜原值)
    machine_key      TEXT,
    field            TEXT,
    old_value        TEXT,
    title            TEXT,                   -- 畫面上的名字（主機名或機房／櫃）
    category         TEXT,                   -- 類別內的分類（同機多筆＝2.060.7 分類），畫面篩選用
    room             TEXT,                   -- S2 每批限一個機房用
    match_basis      TEXT,                   -- 當時用哪些欄位配對到這台（JSON）
    matched_at       TEXT,
    detail_json      TEXT,                   -- 最近一次比對看到的內容（各筆值、證據…）
    decision_json    TEXT,                   -- 人確認的內容（S3 逐欄取哪一筆、S2 對到哪個櫃…）
    status           TEXT NOT NULL DEFAULT 'open',
    first_seen       TEXT NOT NULL,
    last_seen        TEXT,
    exception_until  TEXT,
    exception_note   TEXT,
    handed_ref       TEXT,                   -- 交出到哪：cia_fix:<批次>
    handed_at        TEXT,
    closed_at        TEXT,
    updated_at       TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cia_case_fp ON cia_case(fingerprint);
CREATE INDEX IF NOT EXISTS ix_cia_case_kind ON cia_case(kind, status);
CREATE TABLE IF NOT EXISTS cia_case_event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id      INTEGER NOT NULL REFERENCES cia_case(id),
    at           TEXT NOT NULL,
    by           TEXT,
    action       TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    note         TEXT,
    data_json    TEXT
);
CREATE INDEX IF NOT EXISTS ix_cia_case_event_case ON cia_case_event(case_id);
CREATE TABLE IF NOT EXISTS cia_case_link (
    case_id      INTEGER NOT NULL,
    ref_kind     TEXT NOT NULL,              -- cia_fix_item
    ref_id       INTEGER NOT NULL,
    PRIMARY KEY (case_id, ref_kind, ref_id)
);
CREATE TABLE IF NOT EXISTS cia_case_scan (
    kind         TEXT PRIMARY KEY,
    at           TEXT NOT NULL,
    by           TEXT,
    result_json  TEXT
);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure(conn: sqlite3.Connection) -> None:
    """建表（寫入路徑才呼叫；executescript 會先 commit，呼叫端要在開交易之前叫）。"""
    conn.executescript(SCHEMA)
    if "category" not in {r[1] for r in conn.execute("PRAGMA table_info(cia_case)")}:     # 2.061.0 建的表補欄
        conn.execute("ALTER TABLE cia_case ADD COLUMN category TEXT")
        conn.commit()


def _ready(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cia_case'").fetchone() is not None


def fingerprint(kind: str, machine_key: str | None, field: str | None, old_value: str | None) -> str:
    """問題指紋：類別＋machine_key＋欄位＋原值。"""
    raw = "\x1f".join([kind or "", machine_key or "", field or "", old_value or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event(conn, case_id: int, by, action: str, frm, to: str, *, now=None, note=None, data=None) -> None:
    conn.execute("INSERT INTO cia_case_event (case_id, at, by, action, from_status, to_status, note, data_json) VALUES (?,?,?,?,?,?,?,?)",
                 (case_id, now or _now(), by, action, frm, to, note,
                  json.dumps(data, ensure_ascii=False, default=str) if data is not None else None))


def _begin(conn) -> None:
    if conn.in_transaction:
        conn.commit()
    ensure(conn)
    conn.execute("BEGIN IMMEDIATE")


def _rollback(conn) -> None:
    if conn.in_transaction:
        conn.rollback()


def _j(v):
    try:
        return json.loads(v) if v else None
    except ValueError:
        return None       # 壞掉的 JSON 當沒有（只影響顯示，不影響狀態）


# ---------------------------------------------------------------- 比對結果寫進案件表

def sync(conn, kind: str, cands: list[dict], by: str | None) -> dict:
    """把這次比對找到的問題寫進案件表。cands：[{machine_key, field, old_value, title, room, match_basis, detail}]。
    新指紋 → 待確認（系統找到）；已不存在的又出現 → 待確認（再次出現）；待確認／已確認這次沒找到 → 已不存在。
    例外的這次沒找到也 → 已不存在（問題不在了，不用再重審）。已交出／已結案的不動（交出的由修正包結案）。"""
    if kind not in KINDS:
        raise ValueError(f"不認得的類別：{kind}")
    _begin(conn)
    try:
        now = _now()
        res = {"found": 0, "reappear": 0, "gone": 0, "same": 0}
        seen = set()
        for c in cands:
            fp = fingerprint(kind, c.get("machine_key"), c.get("field"), c.get("old_value"))
            if fp in seen:
                continue
            seen.add(fp)
            basis = json.dumps(c.get("match_basis"), ensure_ascii=False, default=str) if c.get("match_basis") is not None else None
            detail = json.dumps(c.get("detail"), ensure_ascii=False, default=str) if c.get("detail") is not None else None
            r = conn.execute("SELECT id, status FROM cia_case WHERE fingerprint = ?", (fp,)).fetchone()
            if r is None:
                cid = conn.execute(
                    "INSERT INTO cia_case (kind, fingerprint, machine_key, field, old_value, title, category, room, match_basis, matched_at, "
                    "detail_json, status, first_seen, last_seen, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'open',?,?,?)",
                    (kind, fp, c.get("machine_key"), c.get("field"), c.get("old_value"), c.get("title"), c.get("category"), c.get("room"),
                     basis, now, detail, now, now, now)).lastrowid
                _event(conn, cid, by, "found", None, "open", now=now)
                res["found"] += 1
                continue
            conn.execute("UPDATE cia_case SET title = ?, category = ?, room = ?, match_basis = ?, matched_at = ?, detail_json = ?, last_seen = ? "
                         "WHERE id = ?", (c.get("title"), c.get("category"), c.get("room"), basis, now, detail, now, r["id"]))
            if r["status"] == "resolved":
                conn.execute("UPDATE cia_case SET status = 'open', updated_at = ? WHERE id = ?", (now, r["id"]))
                _event(conn, r["id"], by, "reappear", "resolved", "open", now=now)
                res["reappear"] += 1
            else:
                res["same"] += 1
        for r in conn.execute("SELECT id, status, fingerprint FROM cia_case WHERE kind = ? AND status IN ('open','confirmed','exception')",
                              (kind,)).fetchall():
            if r["fingerprint"] not in seen:
                conn.execute("UPDATE cia_case SET status = 'resolved', updated_at = ? WHERE id = ?", (now, r["id"]))
                _event(conn, r["id"], by, "gone", r["status"], "resolved", now=now)
                res["gone"] += 1
        conn.execute("INSERT INTO cia_case_scan (kind, at, by, result_json) VALUES (?,?,?,?) "
                     "ON CONFLICT(kind) DO UPDATE SET at = excluded.at, by = excluded.by, result_json = excluded.result_json",
                     (kind, now, by, json.dumps(res)))
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    settle(conn, by)
    return res


def last_scan(conn, kind: str) -> dict | None:
    if not _ready(conn):
        return None
    r = conn.execute("SELECT * FROM cia_case_scan WHERE kind = ?", (kind,)).fetchone()
    return {"at": r["at"], "by": r["by"], **(_j(r["result_json"]) or {})} if r else None


# ---------------------------------------------------------------- 人的動作

def _cases(conn, ids: list[int]):
    ids = [int(i) for i in dict.fromkeys(ids or [])]
    if not ids:
        raise ValueError("沒有選任何一案")
    if len(ids) > 2000:
        raise ValueError("一次最多 2000 案")
    rows = conn.execute(f"SELECT * FROM cia_case WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    if len(rows) != len(ids):
        raise LookupError("有幾案找不到")
    return rows


def _check_date(d: str | None) -> str:
    if not d:
        return (datetime.now() + timedelta(days=EXCEPTION_DEFAULT_DAYS)).strftime("%Y-%m-%d")
    try:
        dt = datetime.strptime(d.strip()[:10], "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("到期日格式要是 YYYY-MM-DD") from exc
    if dt.date() <= datetime.now().date():
        raise ValueError("到期日要在今天之後")
    if dt > datetime.now() + timedelta(days=EXCEPTION_MAX_DAYS):
        raise ValueError(f"到期日最多 {EXCEPTION_MAX_DAYS} 天後（例外要定期重審）")
    return dt.strftime("%Y-%m-%d")


def default_decision(r) -> dict:
    """批次確認（機櫃編號、實體機補登）存的確認內容：原值＋這次看到的新值／預填。同機多筆不走這裡（要逐欄選，cia_dup.confirm）。"""
    d = _j(r["detail_json"]) or {}
    if r["kind"] == "rack":
        return {"old": r["old_value"], "new": d.get("new"), "why": d.get("why"), "serials": d.get("serials") or []}
    if r["kind"] == "physical":
        return {"fill": d.get("fill"), "apid": d.get("apid_suggest"), "evidence_level": d.get("evidence_level")}
    return {}


def act(conn, ids: list[int], action: str, by: str, note: str | None = None, decision: dict | None = None,
        until: str | None = None, kind: str | None = None) -> dict:
    """confirm（待確認→已確認，存 decision）／withdraw（已確認→待確認，反向事件，要原因）／
    except（待確認或已確認→例外，要原因、到期日）／unexcept（例外→待確認）。kind 給了就只收那一類。"""
    note = (note or "").strip() or None
    if action in ("withdraw", "except", "unexcept") and not note:
        raise ValueError("要填原因")
    allowed = {"confirm": ("open",), "withdraw": ("confirmed",), "except": ("open", "confirmed"), "unexcept": ("exception",)}
    to_of = {"confirm": "confirmed", "withdraw": "open", "except": "exception", "unexcept": "open"}
    if action not in allowed:
        raise ValueError(f"不認得的動作：{action}")
    exc_until = _check_date(until) if action == "except" else None
    if not _ready(conn):
        raise LookupError("還沒有任何案件（先按「重新比對」）")
    _begin(conn)
    try:
        now, done, skipped = _now(), 0, []
        for r in _cases(conn, ids):
            if kind and r["kind"] != kind:
                skipped.append({"id": r["id"], "why": f"不是{KINDS.get(kind, kind)}的案件"})
                continue
            if action == "confirm" and r["kind"] == "dup" and decision is None:
                skipped.append({"id": r["id"], "why": "同機多筆要逐案確認（每個不同欄位選要取哪一筆）"})
                continue
            if r["status"] not in allowed[action]:
                skipped.append({"id": r["id"], "why": f"目前是{STATUS.get(r['status'], r['status'])}，不能{ACTION_LABEL[action]}"})
                continue
            to = to_of[action]
            dec = decision
            if action == "confirm":
                # 確認的內容存進 decision（審查 P1）：沒給的就把「現在看到、這次確認的」那份存起來，交出時只用它
                if dec is None:
                    dec = default_decision(r)
                conn.execute("UPDATE cia_case SET status = ?, decision_json = ?, updated_at = ? WHERE id = ?",
                             (to, json.dumps(dec, ensure_ascii=False, default=str), now, r["id"]))
            elif action == "withdraw":
                # 反向事件：舊的「確認」事件留著，決定內容清掉（下次確認重填）
                conn.execute("UPDATE cia_case SET status = ?, decision_json = NULL, updated_at = ? WHERE id = ?", (to, now, r["id"]))
            elif action == "except":
                conn.execute("UPDATE cia_case SET status = ?, exception_until = ?, exception_note = ?, updated_at = ? WHERE id = ?",
                             (to, exc_until, note, now, r["id"]))
            else:
                conn.execute("UPDATE cia_case SET status = ?, exception_until = NULL, exception_note = NULL, updated_at = ? WHERE id = ?",
                             (to, now, r["id"]))
            _event(conn, r["id"], by, action, r["status"], to, now=now, note=note,
                   data=dec if action == "confirm" else ({"until": exc_until} if exc_until else None))
            done += 1
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return {"updated": done, "skipped": skipped}


def hand_to_fix(conn, kind: str, case_items: list[tuple[int, list[dict]]], title: str, by: str) -> dict:
    """已確認的案件交出成 9.3 修正包的一個批次（共用 cia_fix 的表、狀態機、Excel、CIA 匯入後自動核對）。
    case_items：[(case_id, [cia_fix 候選列…])]。一案的列一定同一包（cia_fix.pack 依資產序號不拆）。
    同一格（資產＋欄位＋原值）已在別包未結案的，那一案整案不交（留在已確認），回 skipped。"""
    import cia_fix

    if not case_items:
        raise ValueError("沒有已確認、可以交出的案件")
    cia_fix.ensure(conn)
    _begin(conn)
    try:
        open_k = cia_fix._open_keys(conn)
        keep, skipped = [], []
        for cid, rows in case_items:
            r = conn.execute("SELECT status FROM cia_case WHERE id = ?", (cid,)).fetchone()
            if not r or r["status"] != "confirmed":
                skipped.append({"id": cid, "why": "不是已確認"})
                continue
            if any((x["asset_serial"], x["field"], x["old_value"]) in open_k for x in rows):
                skipped.append({"id": cid, "why": "同一格已在別包處理中"})
                continue
            if not rows:
                skipped.append({"id": cid, "why": "確認的內容不需要改任何一格"})
                continue
            for x in rows:
                x["_case"] = cid
                open_k.add((x["asset_serial"], x["field"], x["old_value"]))
            keep.extend(rows)
        if not keep:
            conn.commit()
            return {"batch_id": None, "items": 0, "cases": 0, "skipped": skipped}
        max_rows = int(cia_fix.load_rules().get("max_rows_per_package") or 300)
        pkgs = cia_fix.pack(keep, max_rows)
        cia = cia_fix._last_cia_import(conn)
        now = _now()
        bid = conn.execute(
            "INSERT INTO cia_fix_batch (kind, title, cia_import_at, cia_import_file, cia_data_date, cia_rows, created_by, created_at, "
            "item_count, pkg_count, max_rows, rules_sha256, settings_sha256, snapshot_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (kind, title, cia["at"], cia["file"], cia["data_date"], cia["rows"], by, now, len(keep), len(pkgs), max_rows,
             None, None, json.dumps({"kind": kind, "cases": sorted({x["_case"] for x in keep})}))).lastrowid
        cases = set()
        for pk in pkgs:
            for c in pk["rows"]:
                iid = conn.execute(
                    "INSERT INTO cia_fix_item (batch_id, pkg_no, asset_serial, hostname, unit, asset_group, field, old_value, "
                    "new_value, rule_id, reason, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending')",
                    (bid, c["pkg_no"], c["asset_serial"], c.get("hostname"), c["unit"], c["asset_group"], c["field"],
                     c["old_value"], c.get("new_value"), c["rule_id"], c.get("reason"))).lastrowid
                conn.execute("UPDATE cia_fix_item SET track_code = ? WHERE id = ?", (f"CF{bid}-{iid}", iid))
                cia_fix._event(conn, iid, bid, by, "create", None, "pending", now=now, note=f"案件 {c['_case']}")
                conn.execute("INSERT OR IGNORE INTO cia_case_link (case_id, ref_kind, ref_id) VALUES (?, 'cia_fix_item', ?)", (c["_case"], iid))
                cases.add(c["_case"])
        for cid in cases:
            conn.execute("UPDATE cia_case SET status = 'handed', handed_ref = ?, handed_at = ?, updated_at = ? WHERE id = ?",
                         (f"cia_fix:{bid}", now, now, cid))
            _event(conn, cid, by, "handed", "confirmed", "handed", now=now, data={"cia_fix_batch": bid})
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return {"batch_id": bid, "items": len(keep), "cases": len(cases), "packages": len(pkgs), "skipped": skipped}


def expire_exceptions(conn, by: str | None = None, today: str | None = None) -> int:
    """例外到期（到期日已過）→ 自動回待確認，記一筆「例外到期」事件（S4 的「例外到期」列要重審）。沒有到期的就不拿寫入鎖。"""
    if not _ready(conn):
        return 0
    today = today or datetime.now().strftime("%Y-%m-%d")
    ids = [r[0] for r in conn.execute("SELECT id FROM cia_case WHERE status = 'exception' AND exception_until IS NOT NULL "
                                      "AND exception_until < ?", (today,))]
    if not ids:
        return 0
    _begin(conn)
    try:
        now = _now()
        for cid in ids:
            u = conn.execute("SELECT exception_until FROM cia_case WHERE id = ?", (cid,)).fetchone()[0]
            conn.execute("UPDATE cia_case SET status = 'open', updated_at = ? WHERE id = ? AND status = 'exception'", (now, cid))
            _event(conn, cid, by or "系統", "expire", "exception", "open", now=now, note=f"例外到期日 {u} 已過", data={"until": u})
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return len(ids)


def settle(conn, by: str | None) -> int:
    """先處理例外到期；再看已交出的案件：連到的修正包列全部結案（已套用／CIA已另改／資產已消失／不同意／已撤回）→ 案件結案。
    修正包的列被撤回或不同意＝這案沒做成，案件回已確認（可重交或撤回）。回結案件數。"""
    if not _ready(conn):
        return 0
    expire_exceptions(conn, by)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='cia_fix_item'").fetchone():
        return 0
    rows = conn.execute("SELECT c.id, GROUP_CONCAT(i.status) AS sts FROM cia_case c JOIN cia_case_link l ON l.case_id = c.id "
                        "AND l.ref_kind = 'cia_fix_item' JOIN cia_fix_item i ON i.id = l.ref_id "
                        "AND c.handed_ref = 'cia_fix:' || i.batch_id WHERE c.status = 'handed' "   # 只看這一次交出的那批（重交過的舊列不算）
                        "GROUP BY c.id").fetchall()
    todo = []
    for r in rows:
        sts = set((r["sts"] or "").split(","))
        if sts & {"pending", "agreed"}:
            continue
        back = bool(sts & {"rejected", "withdrawn"})
        todo.append((r["id"], back, sorted(sts)))
    if not todo:
        return 0
    _begin(conn)
    try:
        now, n = _now(), 0
        for cid, back, sts in todo:
            if back:
                conn.execute("UPDATE cia_case SET status = 'confirmed', handed_ref = NULL, handed_at = NULL, updated_at = ? WHERE id = ?",
                             (now, cid))
                _event(conn, cid, by, "withdraw", "handed", "confirmed", now=now, note="修正包被不同意或撤回", data={"fix_status": sts})
            else:
                conn.execute("UPDATE cia_case SET status = 'closed', closed_at = ?, updated_at = ? WHERE id = ?", (now, now, cid))
                _event(conn, cid, by, "closed", "handed", "closed", now=now, data={"fix_status": sts})
                n += 1
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return n


# ---------------------------------------------------------------- 讀取（不建表）

def _case_dict(r) -> dict:
    d = dict(r)
    d["status_label"] = STATUS.get(d["status"], d["status"])
    d["kind_label"] = KINDS.get(d["kind"], d["kind"])
    d["detail"] = _j(d.pop("detail_json"))
    d["decision"] = _j(d.pop("decision_json"))
    d["match_basis"] = _j(d["match_basis"])
    return d


def list_cases(conn, kind: str | None = None, status: str | None = None, room: str | None = None,
               ids: list[int] | None = None, category: str | None = None) -> list[dict]:
    """status：狀態代號、open_all（未結案＝待確認＋已確認＋已交出）、None＝全部。"""
    if not _ready(conn):
        return []
    where, args = [], []
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if status == "open_all":
        where.append(f"status IN ({','.join('?' * len(OPEN))})")
        args += list(OPEN)
    elif status:
        if status not in STATUS:
            raise ValueError(f"不認得的狀態：{status}")
        where.append("status = ?")
        args.append(status)
    if room:
        where.append("room = ?")
        args.append(room)
    if category:
        where.append("category = ?")
        args.append(category)
    if ids is not None:
        if not ids:
            return []
        where.append(f"id IN ({','.join('?' * len(ids))})")
        args += [int(i) for i in ids]
    sql = "SELECT * FROM cia_case" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    return [_case_dict(r) for r in conn.execute(sql, args)]


def counts(conn, kind: str, room: str | None = None, category: str | None = None) -> dict:
    """卡片：各狀態案數＋未結案（可限機房、分類，跟下鑽同一組條件）。卡片＝list_cases（verify_numbers 用獨立 SQL 釘住）。"""
    out = {k: 0 for k in STATUS}
    out.update({"total": 0, "open_all": 0})
    if not _ready(conn):
        return out
    where, args = ["kind = ?"], [kind]
    if room:
        where.append("room = ?")
        args.append(room)
    if category:
        where.append("category = ?")
        args.append(category)
    for st, n in conn.execute(f"SELECT status, COUNT(*) FROM cia_case WHERE {' AND '.join(where)} GROUP BY status", args):
        out[st] = n
        out["total"] += n
    out["open_all"] = sum(out[k] for k in OPEN)
    return out


def categories(conn, kind: str) -> list[dict]:
    """這一類有哪些分類、各幾案（篩選下拉用，從資料來）。"""
    if not _ready(conn):
        return []
    return [{"category": r[0], "n": r[1]} for r in conn.execute(
        "SELECT category, COUNT(*) FROM cia_case WHERE kind = ? AND category IS NOT NULL GROUP BY category ORDER BY category", (kind,))]


def case_events(conn, case_id: int) -> list[dict]:
    if not _ready(conn):
        return []
    return [{**dict(r), "action_label": ACTION_LABEL.get(r["action"], r["action"]),
             "to_label": STATUS.get(r["to_status"], r["to_status"]), "data": _j(r["data_json"])}
            for r in conn.execute("SELECT * FROM cia_case_event WHERE case_id = ? ORDER BY id", (case_id,))]
