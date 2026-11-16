"""告警聯絡資訊（2.044.2，需求 R15，使用者 2026-11-04）：每則告警都要帶「AP 團隊、單位、系統名稱、分級、email」。

## 鍵與欄位
以業務系統代號（APID，＝hardware.api_id＝business_system.api_id）為鍵，五欄：
  ap_team（AP 團隊）、unit（單位）、system_name（系統名稱）、sys_class（分級：第一類／第二類／第三類）、email。

## 為什麼另開兩張小表、不加在 business_system
既有 business_system 已經有系統名稱（name）、AP 部門（ap_department）、分級（sys_class＋sys_class_override），
這三欄**直接沿用當退路**；缺的只有 AP 團隊與 email。business_system 由「業務系統對照表」整列覆寫匯入、
分級表另匯，再加欄位會被那兩條匯入互相沖掉（D4），所以聯絡資訊放自己的表：
  · alert_contact          匯入的（整份取代；從「資料匯入」頁匯）
  · alert_contact_override 管理員手動改的（匯入不碰；有值就蓋過匯入）

## 每一欄的取值順序（固定，畫面每格都標來源，可追溯）
  1. 人工覆寫（alert_contact_override，非 NULL）
  2. 匯入（alert_contact，非空）
  3. 業務系統對照表（只有系統名稱←name、單位←ap_department、分級←sys_class_override／sys_class）
  4. 都沒有＝「未提供」（不猜、不拿 AP 負責人當 AP 團隊）

## 寫入
資料函式不 commit，由路由與稽核（contact_audit kind='alert_contact'、activity）同一交易提交。
匯入兩段式：先預覽（新增／修改／移除／錯誤筆數），確認再寫；範例檔的「範例」列一律略過。
"""
from __future__ import annotations

import json
import re
import sqlite3

FIELDS = ("ap_team", "unit", "system_name", "sys_class", "email")
LABEL = {"ap_team": "AP 團隊", "unit": "單位", "system_name": "系統名稱", "sys_class": "分級", "email": "email"}
NOT_PROVIDED = "未提供"
CLASSES = ("第一類", "第二類", "第三類")
SRC_OVERRIDE, SRC_IMPORT, SRC_BIZ = "人工", "匯入", "業務系統對照表"
MAX_ROWS = 20000
MAX_LEN = 200

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS alert_contact (
        api_id TEXT PRIMARY KEY, ap_team TEXT, unit TEXT, system_name TEXT, sys_class TEXT, email TEXT,
        source_file TEXT, imported_at TEXT, imported_by TEXT)""",
    """CREATE TABLE IF NOT EXISTS alert_contact_override (
        api_id TEXT PRIMARY KEY, ap_team TEXT, unit TEXT, system_name TEXT, sys_class TEXT, email TEXT,
        updated_by TEXT, updated_at TEXT)""",
]

#: 匯入表頭（第一個是範例檔用的正式名稱；其餘是常見寫法）
HEADERS = {
    "api_id": ["APID", "AP_ID", "api_id", "系統代號", "業務系統代號", "system_id"],
    "ap_team": ["AP團隊", "AP 團隊", "ap_team", "負責團隊", "團隊"],
    "unit": ["單位", "AP單位", "AP部門", "部門", "ap_department"],
    "system_name": ["系統名稱", "業務系統名稱", "系統別", "system", "system_name"],
    "sys_class": ["分級", "系統分級", "系統類別", "sys_class"],
    "email": ["email", "Email", "E-mail", "EMAIL", "信箱", "電子郵件"],
}
_EMAIL = re.compile(r"^[^@\s;,]+@[^@\s;,]+\.[^@\s;,]+$")


def ensure(conn: sqlite3.Connection) -> None:
    for s in SCHEMA:
        conn.execute(s)


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


# ── 正規化與驗證 ──────────────────────────────────────────────────────
def norm_class(v) -> str | None:
    """分級 → 第一類／第二類／第三類；空白回 None；認不出丟 ValueError（不猜）。"""
    s = str(v or "").strip()
    if not s:
        return None
    import system_stats
    m = {"1": "第一類", "2": "第二類", "3": "第三類", "一": "第一類", "二": "第二類", "三": "第三類"}
    if s in m:
        return m[s]
    cls, _core = system_stats.class_from_label(s)
    if cls in CLASSES:
        return cls
    raise ValueError(f"分級「{s[:30]}」認不出來（要寫第一類、第二類或第三類）")


def norm_email(v) -> str | None:
    """一格可以有多個信箱（用 ; 或 , 或空白隔開），存成「a; b」。格式不對丟 ValueError。"""
    s = str(v or "").strip()
    if not s:
        return None
    parts = [p for p in re.split(r"[;,\s]+", s) if p]
    bad = [p for p in parts if not _EMAIL.match(p)]
    if bad:
        raise ValueError(f"email「{bad[0][:60]}」格式不對")
    return "; ".join(parts)[:MAX_LEN * 2]


def norm_text(v) -> str | None:
    s = " ".join(str(v or "").split())
    if s[:1] in ("=", "+", "-", "@"):           # 試算表公式開頭：存成文字也不行（匯出時會被當公式）
        s = "'" + s
    return s[:MAX_LEN] or None


def norm_field(f: str, v):
    if f == "sys_class":
        return norm_class(v)
    if f == "email":
        return norm_email(v)
    return norm_text(v)


# ── 讀取：每一欄的值＋來源 ──────────────────────────────────────────
def _biz(conn) -> dict[str, dict]:
    import system_stats
    out: dict[str, dict] = {}
    try:
        rows = conn.execute("SELECT api_id, name, ap_department, sys_class, sys_class_override FROM business_system").fetchall()
    except sqlite3.OperationalError:
        return out            # 沒有業務系統表：只少退路那三欄
    for r in rows:
        a = (r["api_id"] or "").strip()
        if not a:
            continue
        cls = (r["sys_class_override"] or "").strip() or system_stats.class_from_label(r["sys_class"])[0]
        out[a] = {"system_name": (r["name"] or "").strip() or None, "unit": (r["ap_department"] or "").strip() or None,
                  "sys_class": cls if cls in CLASSES else None}
    return out


def _table(conn, t: str) -> dict[str, dict]:
    if not _has(conn, t):
        return {}
    return {r["api_id"]: dict(r) for r in conn.execute(f"SELECT * FROM {t}")}


class Resolver:
    """一次讀三個來源，之後每個 APID 查五欄（給大量告警列用，不逐列查庫）。"""

    def __init__(self, conn):
        self.ovr = _table(conn, "alert_contact_override")
        self.imp = _table(conn, "alert_contact")
        self.biz = _biz(conn)

    def get(self, api_id: str | None) -> dict:
        a = (api_id or "").strip()
        out = {"api_id": a or None}
        for f in FIELDS:
            val, src = None, None
            if a:
                o, i, b = self.ovr.get(a) or {}, self.imp.get(a) or {}, self.biz.get(a) or {}
                if o.get(f) is not None:
                    val, src = o[f], SRC_OVERRIDE
                elif i.get(f):
                    val, src = i[f], f"{SRC_IMPORT}（{i.get('source_file') or '檔名不明'}）"
                elif b.get(f):
                    val, src = b[f], SRC_BIZ
            out[f] = val
            out[f + "_src"] = src
            out[f + "_text"] = val if val else NOT_PROVIDED
        return out

    def api_ids(self) -> list[str]:
        return sorted(set(self.ovr) | set(self.imp) | set(self.biz))


def attach(conn, rows: list[dict], key: str = "api_id") -> list[dict]:
    """告警列加上五欄（c_ap_team…，值或「未提供」）＋來源。一列掛多個 APID（api_ids）時每欄用「／」接起來。
    沒有 APID 的列五欄都寫「未提供」，c_note 寫原因。"""
    if not rows:
        return rows
    rv = Resolver(conn)
    for r in rows:
        ids = r.get("api_ids") if isinstance(r.get("api_ids"), list) else ([r.get(key)] if r.get(key) else [])
        ids = [x for x in dict.fromkeys((x or "").strip() for x in ids) if x]
        cs = [rv.get(a) for a in ids]
        for f in FIELDS:
            vals = [c[f] for c in cs if c[f]]
            r["c_" + f] = "／".join(dict.fromkeys(vals)) if vals else NOT_PROVIDED
            r["c_" + f + "_src"] = "／".join(dict.fromkeys(c[f + "_src"] for c in cs if c[f + "_src"])) or None
        r["c_api_ids"] = ids
        r["c_note"] = None if ids else "這筆告警沒有對到業務系統（APID），聯絡資訊無從查起"
    return rows


def api_ids_by_machine(conn) -> dict[str, list[str]]:
    """machine_key → 這台掛的 APID（夜間速查的 WhatsUp 現況列只有 machine_key 時用；同一台＝system_stats.machine_key）。"""
    import system_stats
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT asset_serial, hostname, ip, api_id FROM hardware WHERE TRIM(COALESCE(api_id,'')) <> ''"):
        k = system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        a = r["api_id"].strip()
        if a not in out.setdefault(k, []):
            out[k].append(a)
    return out


def listing(conn) -> dict:
    """管理畫面：每個 APID 一列（業務系統對照表＋匯入＋人工，三者聯集），五欄＋來源＋是否人工覆寫。"""
    rv = Resolver(conn)
    rows = []
    for i, a in enumerate(rv.api_ids(), 1):
        c = rv.get(a)
        c["__no"] = i
        c["overridden"] = [LABEL[f] for f in FIELDS if (rv.ovr.get(a) or {}).get(f) is not None]
        c["missing"] = [LABEL[f] for f in FIELDS if not c[f]]
        c["in_import"] = a in rv.imp
        c["in_biz"] = a in rv.biz
        rows.append(c)
    last = None
    if rv.imp:
        x = max(rv.imp.values(), key=lambda r: r.get("imported_at") or "")
        last = {"file": x.get("source_file"), "at": x.get("imported_at"), "by": x.get("imported_by"), "rows": len(rv.imp)}
    return {"rows": rows, "total": len(rows), "import": last, "snapshots": snapshots(conn),
            "missing": {LABEL[f]: sum(1 for r in rows if not r[f]) for f in FIELDS},
            "overridden": sum(1 for r in rows if r["overridden"])}


# ── 人工覆寫（管理員）────────────────────────────────────────────────
def save_override(conn, api_id: str, fields: dict, user: str) -> dict:
    """fields：五欄任意幾欄；值是 None 或空白＝清掉這欄的覆寫（回到匯入／業務系統對照表）。**不 commit**。"""
    from db import _now_local
    a = (api_id or "").strip()
    if not a or len(a) > 64:
        raise ValueError("要有業務系統代號（APID）")
    ensure(conn)
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise ValueError(f"不認得的欄位：{'、'.join(sorted(unknown))}")
    before = conn.execute("SELECT * FROM alert_contact_override WHERE api_id = ?", (a,)).fetchone()
    cur = {f: (before[f] if before else None) for f in FIELDS}
    for f, v in fields.items():
        cur[f] = norm_field(f, v) if (v is not None and str(v).strip()) else None
    if all(cur[f] is None for f in FIELDS):
        conn.execute("DELETE FROM alert_contact_override WHERE api_id = ?", (a,))
    else:
        conn.execute("INSERT INTO alert_contact_override (api_id, ap_team, unit, system_name, sys_class, email, updated_by, updated_at) "
                     "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(api_id) DO UPDATE SET ap_team=excluded.ap_team, unit=excluded.unit, "
                     "system_name=excluded.system_name, sys_class=excluded.sys_class, email=excluded.email, "
                     "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                     (a, *[cur[f] for f in FIELDS], user, _now_local()))
    conn.execute("INSERT INTO contact_audit (kind, level, key, before_json, after_json, changed_by, changed_at) "
                 "VALUES ('alert_contact', 'override', ?, ?, ?, ?, datetime('now','localtime'))",
                 (a, json.dumps({f: before[f] for f in FIELDS}, ensure_ascii=False) if before else None,
                  json.dumps(cur, ensure_ascii=False), user))
    return Resolver(conn).get(a)


# ── 匯入（整份取代 alert_contact；人工覆寫不碰）──────────────────────
def _map_headers(headers: list[str]) -> dict[str, int]:
    norm = {re.sub(r"\s+", "", h).lower(): i for i, h in enumerate(headers)}
    out = {}
    for f, cands in HEADERS.items():
        for c in cands:
            k = re.sub(r"\s+", "", c).lower()
            if k in norm:
                out[f] = norm[k]
                break
    return out


def parse(filename: str, data: bytes) -> dict:
    """檔案 → {rows: {api_id: {五欄}}, errors, skipped_example, duplicates, columns}。不碰資料庫。"""
    import daily_risk
    import import_templates
    if (filename or "").lower().endswith((".xlsx", ".xlsm")):
        daily_risk._check_xlsx_zip(data)
    headers, grid = daily_risk.read_table(filename, data, max_rows=MAX_ROWS)
    cols = _map_headers(headers)
    if "api_id" not in cols:
        raise ValueError("找不到 APID 欄（表頭要有「APID」；請用範例檔）")
    missing_cols = [LABEL[f] for f in FIELDS if f not in cols]
    rows: dict[str, dict] = {}
    errors, dup, examples = [], set(), 0
    for n, r in enumerate(grid, 2):
        def cell(f):
            i = cols.get(f)
            return r[i] if i is not None and i < len(r) else None
        a = str(cell("api_id") or "").strip()
        if not a:
            if any(str(cell(f) or "").strip() for f in FIELDS):     # 有內容卻沒 APID＝缺必填（GPT A1），不是空白列
                errors.append({"row": n, "api_id": "", "message": "這一列有資料但缺 APID（必填）"})
            continue
        if import_templates.is_example(a):
            examples += 1
            continue
        if len(a) > 64:
            errors.append({"row": n, "api_id": a[:64], "message": "APID 太長"})
            continue
        rec = {}
        for f in FIELDS:
            try:
                rec[f] = norm_field(f, cell(f))
            except ValueError as exc:
                rec[f] = None
                errors.append({"row": n, "api_id": a, "message": str(exc)})
        if a in rows or a in dup:
            dup.add(a)
            rows.pop(a, None)
            continue
        rows[a] = rec
    for a in sorted(dup):
        errors.append({"row": None, "api_id": a, "message": "同一個 APID 出現不只一列，這個 APID 整個不匯（不猜用哪一列）"})
    return {"rows": rows, "errors": errors, "skipped_example": examples, "duplicates": sorted(dup), "missing_columns": missing_cols}


def plan(conn, filename: str, data: bytes) -> dict:
    """預覽：跟現在的匯入資料比，新增／修改／不變／移除各幾筆。"""
    p = parse(filename, data)
    cur = _table(conn, "alert_contact")
    biz = _biz(conn)
    new = [a for a in p["rows"] if a not in cur]
    changed = [a for a in p["rows"] if a in cur and any((cur[a].get(f) or None) != p["rows"][a][f] for f in FIELDS)]
    removed = [a for a in cur if a not in p["rows"]]
    blockers = []
    if p["errors"]:
        blockers.append(f"有 {len(p['errors'])} 個問題（格式錯、缺必填或 APID 重複）")
    if p["missing_columns"]:
        blockers.append(f"檔案缺欄位：{'、'.join(p['missing_columns'])}（整份取代會把這幾欄清空）")
    if not p["rows"]:
        blockers.append("沒有任何可匯入的列")
    # 預覽與確認綁在一起（GPT A1）：檔案內容＋要寫入的列＋目前資料庫裡的匯入資料，任何一個變了，確認就作廢、要重新預覽
    digest = _digest(p["rows"], cur)
    return {"file": filename, "total": len(p["rows"]), "new": len(new), "changed": len(changed),
            "unchanged": len(p["rows"]) - len(new) - len(changed), "removed": len(removed), "removed_ids": removed[:200],
            "errors": p["errors"][:500], "error_count": len(p["errors"]), "skipped_example": p["skipped_example"],
            "duplicates": p["duplicates"], "missing_columns": p["missing_columns"],
            "not_in_biz": sorted(a for a in p["rows"] if a not in biz)[:200],
            "not_in_biz_count": sum(1 for a in p["rows"] if a not in biz),
            "can_confirm": not blockers, "blockers": blockers, "plan_digest": digest, "_rows": p["rows"]}


def _digest(rows: dict, cur: dict) -> str:
    import hashlib
    body = json.dumps({"rows": rows, "cur": {a: {f: (r.get(f) or None) for f in FIELDS} for a, r in cur.items()}},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


class ImportBlocked(ValueError):
    """有錯誤或預覽已過期：整份不匯（一筆都不動）。"""


SNAP_KEEP = 50


def _snapshot(conn, user: str, reason: str, source: str | None) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS alert_contact_snapshot (id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT NOT NULL, "
                 "by TEXT, reason TEXT, source_file TEXT, n INTEGER, rows_json TEXT NOT NULL)")
    from db import _now_local
    rows = [dict(r) for r in conn.execute("SELECT * FROM alert_contact ORDER BY api_id")]
    cur = conn.execute("INSERT INTO alert_contact_snapshot (taken_at, by, reason, source_file, n, rows_json) VALUES (?,?,?,?,?,?)",
                       (_now_local(), user, reason, source, len(rows), json.dumps(rows, ensure_ascii=False)))
    conn.execute(f"DELETE FROM alert_contact_snapshot WHERE id NOT IN (SELECT id FROM alert_contact_snapshot ORDER BY id DESC LIMIT {SNAP_KEEP})")
    return cur.lastrowid


def snapshots(conn) -> list[dict]:
    if not _has(conn, "alert_contact_snapshot"):
        return []
    return [{**dict(r), "__no": r["id"]} for r in conn.execute(
        "SELECT id, taken_at, by, reason, source_file, n FROM alert_contact_snapshot ORDER BY id DESC")]


def apply(conn, filename: str, data: bytes, user: str, plan_digest: str = "") -> dict:
    """整份取代 alert_contact（人工覆寫不碰）。**不 commit**（路由同一交易提交；中途失敗整筆回滾）。

    GPT A1：任何錯誤（格式錯、缺必填、APID 重複、缺欄位）＝整份不能確認，一筆都不動；確認要帶預覽的 plan_digest，
    重算不同（檔案換了或資料庫在預覽後被改過）就拒絕。寫入前先存一份匯入前快照，可以還原。"""
    from db import _now_local
    ensure(conn)
    pl = plan(conn, filename, data)
    if not pl["can_confirm"]:
        raise ImportBlocked("整份不能匯入：" + "；".join(pl["blockers"]))
    if not plan_digest or plan_digest != pl["plan_digest"]:
        raise ImportBlocked("預覽已過期（檔案或目前資料在預覽後變了），請重新預覽再確認")
    rows = pl.pop("_rows")
    now = _now_local()
    pl["snapshot_id"] = _snapshot(conn, user, "匯入前", filename)
    conn.execute("DELETE FROM alert_contact")
    conn.executemany("INSERT INTO alert_contact (api_id, ap_team, unit, system_name, sys_class, email, source_file, imported_at, imported_by) "
                     "VALUES (?,?,?,?,?,?,?,?,?)", [(a, *[r[f] for f in FIELDS], filename, now, user) for a, r in rows.items()])
    written = conn.execute("SELECT COUNT(*) FROM alert_contact").fetchone()[0]
    if written != pl["total"]:                      # 預覽筆數＝寫入筆數；不等就整筆不算（路由會回滾）
        raise ImportBlocked(f"寫入筆數 {written} 跟預覽 {pl['total']} 不一致，已取消")
    pl["written"] = written
    conn.execute("INSERT INTO contact_audit (kind, level, key, before_json, after_json, changed_by, changed_at) "
                 "VALUES ('alert_contact', 'import', ?, ?, ?, ?, datetime('now','localtime'))",
                 (filename, json.dumps({"snapshot_id": pl["snapshot_id"]}),
                  json.dumps({k: pl[k] for k in ("total", "new", "changed", "removed", "written")}, ensure_ascii=False), user))
    return pl


def restore(conn, snapshot_id: int, user: str) -> dict:
    """把匯入資料還原成某一份快照（還原前也先存一份，可以再還原回來）。人工覆寫不碰。**不 commit**。"""
    ensure(conn)
    if not _has(conn, "alert_contact_snapshot"):
        raise LookupError("沒有這份快照")
    s = conn.execute("SELECT * FROM alert_contact_snapshot WHERE id = ?", (int(snapshot_id),)).fetchone()
    if s is None:
        raise LookupError("沒有這份快照")
    rows = json.loads(s["rows_json"])
    before = _snapshot(conn, user, f"還原快照 #{snapshot_id} 前", None)
    conn.execute("DELETE FROM alert_contact")
    cols = ("api_id", *FIELDS, "source_file", "imported_at", "imported_by")
    conn.executemany(f"INSERT INTO alert_contact ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                     [tuple(r.get(c) for c in cols) for r in rows])
    conn.execute("INSERT INTO contact_audit (kind, level, key, before_json, after_json, changed_by, changed_at) "
                 "VALUES ('alert_contact', 'restore', ?, ?, ?, ?, datetime('now','localtime'))",
                 (str(snapshot_id), json.dumps({"snapshot_id": before}), json.dumps({"rows": len(rows)}), user))
    return {"restored": len(rows), "from_snapshot": int(snapshot_id), "before_snapshot": before}


def template_rows() -> tuple[list[str], list[list]]:
    """範例檔（import_templates 用）：表頭＋兩列「範例」開頭的列（原封匯回會被略過）。"""
    head = [HEADERS[f][0] for f in ("api_id", "ap_team", "unit", "system_name", "sys_class", "email")]
    return head, [["範例-001", "範例AP一組", "範例部門", "範例系統A", "第一類", "ap-team-a@example.test"],
                  ["範例-002", "範例AP二組", "範例部門", "範例系統B", "第二類", "ap-team-b@example.test; oncall@example.test"]]
