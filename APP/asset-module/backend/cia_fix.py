"""CIA 修正包（P1：第一批「寫法統一」，2.060.0）。

規格：AI_note ai_handoff/CL_WEBITv3/規格_CIA修正包與補登_2026-11-10.md；設計與 GPT 12 點：docs/設計_CIA修正包與補登_2026-11-10.md。

## 誰、怎麼用
SP 在 9.3「CIA 修正包」按「產生修正包」→ 先預覽各規則幾筆 → 確認才產生一個批次（依負責單位＋資產群組切成多包，
每包最多 300 列、同一台不拆兩包）→ 下載每一包的 Excel 交 CIA 管理人 → 管理人在「修正明細」選同意／不同意（不同意選原因碼）
→ SP 在「資料匯入」上傳勾好的檔，先看差異報告再確認記錄 → 之後每次 CIA 清冊匯入完成，系統自動核對：
CIA 現值＝新值 → 已套用；既不是原值也不是新值 → CIA 已另改；資產不見了 → 資產已消失。

## 不做的事
- **不改 CIA 資料、不改 hardware 表**：修正包只記錄「建議、決定、核對結果」，CIA 正本只由 CIA 匯入寫入。
- 補登（P2）、機櫃編號統一與 CIA 同機多筆（P3）不在這裡。

## 逐筆狀態機（GPT 第 3 點）
待決定(pending) ─┬→ 同意(agreed) ─┐
                 ├→ 不同意(rejected)│  （回檔或手動；未結案前可改決定）
                 └→ 已撤回(withdrawn)
pending／agreed ──CIA 匯入核對──→ 已套用(applied)／CIA已另改(cia_other)／資產已消失(gone)
不同意／已撤回／CIA已另改 ──重開──→ 待決定（要原值仍是 CIA 現值、而且沒有別包正在處理同一格）
**每一次異動都寫一筆 cia_fix_event，只增不改**；cia_fix_item 上的狀態是「目前」值，歷程看事件表。

## 唯一性（GPT 第 5 點）
（資產序號、欄位、原值）在「未結案」（pending／agreed）裡唯一：部分唯一索引兜底，產包在 BEGIN IMMEDIATE 裡重算、重驗。

## 規則與設定（專案硬規則：分類規則放設定檔、含真實值的對照放 app_settings）
cia_fix_rules.json 只放結構與虛構例子；真實機房寫法對照（cia_fix_room_alias）與範本提示文字（cia_fix_template_texts）在 app_settings。
批次存規則檔與設定的雜湊＋脫敏快照（GPT 第 6 點），日後查得到當時用哪一份規則判的。

讀取函式不建表（跟 rack_diff 一樣，沒產過包的庫一律回空的）。
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path

RULES_PATH = Path(__file__).parent / "cia_fix_rules.json"
SETTING_LAST_RECON = "cia_fix_last_reconcile"

STATUS = {
    "pending": "待決定", "agreed": "同意", "rejected": "不同意", "applied": "已套用",
    "cia_other": "CIA已另改", "gone": "資產已消失", "withdrawn": "已撤回",
}
OPEN = ("pending", "agreed")
#: 系統核對自動結案、但要有人確認的兩種（CIA 改成別的值、資產不見了）——卡片標「待人確認」
NEEDS_CONFIRM = ("cia_other", "gone")
REOPENABLE = ("rejected", "withdrawn", "cia_other")
DECISION_OF = {"同意": "agreed", "不同意": "rejected"}
ACTION_LABEL = {
    "create": "產生", "decide": "記錄決定", "withdraw": "撤回", "reopen": "重開",
    "applied": "核對：已套用", "cia_other": "核對：CIA 已另改", "gone": "核對：資產已消失",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
#: 第三批（2.061.x）由案件交出的規則與特殊欄（不在 cia_fix_rules.json：那份只管第一批寫法統一的判定）
EXTRA_RULE_LABELS = {"dup_field": "同機多筆：欄位取值", "dup_merge": "同機多筆：停用或併入", "rack_name": "機櫃編號統一"}
#: 欄位名以底線開頭＝不是 CIA 的一格，是「整筆」的動作（例：請停用或併入）
EXTRA_FIELD_LABELS = {"_dup_merge": "（整筆）停用或併入"}
TRACK_HEADER = "追蹤碼"
DETAIL_SHEET = "2 修正明細"
#: 修正明細的欄（順序就是 Excel 的欄序；最後一欄追蹤碼隱藏）
DETAIL_COLS = ["項次", "資產序號", "主機名稱", "欄位", "原值", "新值", "原因", "目前狀態", "同意", "不同意原因", "備註", TRACK_HEADER]
#: 回檔時拿來比對有沒有被改過的欄（決定欄以外、跟系統紀錄比）
_LOCKED_COMPARE = ("資產序號", "欄位", "原值", "新值")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cia_fix_batch (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,          -- normalize＝第一批寫法統一（P1 只有這種）
    title           TEXT,
    cia_import_at   TEXT,                   -- 產包當下最後一次 CIA 清冊匯入的時間
    cia_import_file TEXT,                   -- 同上，那份檔名（「CIA 匯出日期」由檔名或匯入日推得）
    cia_data_date   TEXT,
    cia_rows        INTEGER,                -- 那份 CIA 檔的硬體筆數（匯入紀錄）
    created_by      TEXT,
    created_at      TEXT NOT NULL,
    item_count      INTEGER NOT NULL DEFAULT 0,
    pkg_count       INTEGER NOT NULL DEFAULT 0,
    max_rows        INTEGER,
    rules_sha256    TEXT,
    settings_sha256 TEXT,
    snapshot_json   TEXT                    -- 規則＋設定的脫敏快照
);
CREATE TABLE IF NOT EXISTS cia_fix_item (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id         INTEGER NOT NULL REFERENCES cia_fix_batch(id),
    pkg_no           INTEGER NOT NULL,
    track_code       TEXT,
    asset_serial     TEXT NOT NULL,
    hostname         TEXT,
    unit             TEXT,                  -- 負責單位（盤點單位-部門，沒有就處別）
    asset_group      TEXT,                  -- 資產群組（群組名稱）
    field            TEXT NOT NULL,
    old_value        TEXT NOT NULL,
    new_value        TEXT,                  -- NULL＝請人填（IP 格式錯、範本提示文字）
    rule_id          TEXT NOT NULL,
    reason           TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    reject_code      TEXT,
    decision_note    TEXT,
    decided_at       TEXT,
    decided_by       TEXT,
    closed_at        TEXT,
    closed_file      TEXT,                  -- 判已套用／另改的那份 CIA 匯入檔
    closed_sha256    TEXT,
    cia_value_at_close TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cia_fix_item_open
    ON cia_fix_item(asset_serial, field, old_value) WHERE status IN ('pending', 'agreed');
CREATE INDEX IF NOT EXISTS ix_cia_fix_item_batch ON cia_fix_item(batch_id, pkg_no);
CREATE TABLE IF NOT EXISTS cia_fix_event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id      INTEGER NOT NULL REFERENCES cia_fix_item(id),
    batch_id     INTEGER NOT NULL,
    at           TEXT NOT NULL,
    by           TEXT,
    action       TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    reject_code  TEXT,
    note         TEXT,
    source_file  TEXT,
    source_sha256 TEXT,
    cia_value    TEXT
);
CREATE INDEX IF NOT EXISTS ix_cia_fix_event_item ON cia_fix_event(item_id);
"""


class Stale(Exception):
    """預覽之後資料變了（409）。"""


# ---------------------------------------------------------------- 共用

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def ensure(conn: sqlite3.Connection) -> None:
    """建表（只在寫入路徑呼叫；executescript 會先 commit，所以呼叫端要在開交易之前叫）。"""
    conn.executescript(SCHEMA)
    if "cia_rows" not in {r[1] for r in conn.execute("PRAGMA table_info(cia_fix_batch)")}:     # 2.060.x 補欄
        conn.execute("ALTER TABLE cia_fix_batch ADD COLUMN cia_rows INTEGER")
        conn.commit()


def _ready(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cia_fix_item'").fetchone() is not None


def load_rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def _setting(conn, key: str, default):
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if not r or r[0] in (None, ""):
        return default
    try:
        return json.loads(r[0])
    except ValueError:
        return default


def _put_setting(conn, key: str, value) -> None:
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, json.dumps(value, ensure_ascii=False)))


def _nk(v) -> str:
    """比對用鍵：全半形統一（NFKC）、去掉所有空白、不分大小寫。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(v or ""))).lower()


def field_labels() -> dict[str, str]:
    """db 欄位 → CIA 硬體頁的欄名（跟匯入用同一份 field_mapping.json）。"""
    import excel_import

    return {**{col: head for head, col in excel_import.load_mapping().get("硬體", {}).items()}, **EXTRA_FIELD_LABELS}


def _non_cia(serial: str | None) -> bool:
    import lifecycle

    return not serial or str(serial).startswith(lifecycle.NON_CIA_PREFIXES)


def _unit_of(r) -> str:
    return (r["inventory_department"] or r["inventory_division"] or "").strip() or "（未填單位）"


def _settings(conn) -> dict:
    """這次判定用到的系統設定（真實機房寫法、範本提示文字）。"""
    rules = load_rules()
    out = {}
    for r in rules["rules"]:
        if r.get("setting"):
            out[r["setting"]] = _setting(conn, r["setting"], {} if r["kind"] == "setting_map" else [])
    return out


def get_settings(conn) -> dict:
    s = _settings(conn)
    return {"room_alias": s.get("cia_fix_room_alias") or {}, "template_texts": s.get("cia_fix_template_texts") or []}


def save_settings(conn, room_alias: dict | None, template_texts: list | None) -> dict:
    """管理員維護真實對照（寫法 → 標準名稱；範本提示文字清單）。空的鍵／值不收。"""
    if room_alias is not None:
        clean = {}
        for k, v in room_alias.items():
            k2, v2 = str(k or "").strip(), str(v or "").strip()
            if k2 and v2:
                clean[k2] = v2
        _put_setting(conn, "cia_fix_room_alias", clean)
    if template_texts is not None:
        _put_setting(conn, "cia_fix_template_texts", [t.strip() for t in template_texts if t and str(t).strip()])
    conn.commit()
    return get_settings(conn)


# ---------------------------------------------------------------- 規則判定

def _ip_ok(v: str) -> bool:
    toks = [t for t in re.split(r"[,;、/\s]+", v.strip()) if t]
    if not toks:
        return False
    try:
        for t in toks:
            ipaddress.ip_address(t)
    except ValueError:
        return False
    return True


class _Matcher:
    def __init__(self, rules: dict, settings: dict):
        self.rules = []
        for r in rules["rules"]:
            m = dict(r)
            if r["kind"] == "setting_map":
                table = settings.get(r["setting"]) or {}
                m["_map"] = {_nk(k): v for k, v in table.items()}
                for std in table.values():          # 標準名稱本身多了空白／全形 → 也統一
                    m["_map"].setdefault(_nk(std), std)
            elif r["kind"] == "value_map":
                m["_map"] = {_nk(k): v for k, v in r.get("map", {}).items()}
            elif r["kind"] == "blank_text":
                m["_re"] = [re.compile(p) for p in r.get("patterns", [])]
                m["_texts"] = [_nk(t) for t in (settings.get(r.get("setting")) or []) if _nk(t)]
            elif r["kind"] == "ip_format":
                m["_ignore"] = {_nk(x) for x in r.get("ignore", [])}
            self.rules.append(m)

    def judge(self, rule: dict, value) -> tuple[bool, str | None]:
        """(要不要列, 新值)。新值 None＝請人填。"""
        v = "" if value is None else str(value)
        k = rule["kind"]
        if k in ("setting_map", "value_map"):
            if not v.strip():
                return False, None
            t = rule["_map"].get(_nk(v))
            return (t is not None and t != v), t
        if k == "blank_text":
            if not v.strip():
                return False, None
            hit = any(p.search(v) for p in rule["_re"]) or any(t in _nk(v) for t in rule["_texts"])
            return hit, None
        if k == "ip_format":
            if _nk(v) in rule["_ignore"]:
                return False, None
            return (not _ip_ok(v)), None
        return False, None

    def candidates(self, rows) -> list[dict]:
        out = []
        for r in rows:
            for rule in self.rules:
                for f in rule["fields"]:
                    if f not in r.keys():
                        continue
                    hit, new = self.judge(rule, r[f])
                    if hit:
                        out.append({"asset_serial": r["asset_serial"], "hostname": r["hostname"], "unit": _unit_of(r),
                                    "asset_group": (r["group_name"] or "").strip() or "（未填群組）",
                                    "field": f, "old_value": str(r[f]), "new_value": new,
                                    "rule_id": rule["id"], "rule_label": rule["label"], "reason": rule["reason"]})
        # 同一格（資產＋欄位）只給第一條規則
        seen, uniq = set(), []
        for c in out:
            k = (c["asset_serial"], c["field"])
            if k not in seen:
                seen.add(k)
                uniq.append(c)
        return uniq


def _rule_by_id(rules: dict) -> dict:
    return {r["id"]: r for r in rules["rules"]}


def _hw_cols(conn) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}


def _cia_rows(conn):
    """CIA 正本（有資產序號、不是系統自己建的 DYN-／VC-／AUTO-／ADOPT-）。"""
    cols = _hw_cols(conn)
    rows = conn.execute("SELECT * FROM hardware WHERE asset_serial IS NOT NULL AND TRIM(asset_serial) <> '' "
                        "ORDER BY asset_serial").fetchall()
    return [r for r in rows if not _non_cia(r["asset_serial"])], cols


def pack(cands: list[dict], max_rows: int) -> list[dict]:
    """切包（GPT 第 10 點）：依負責單位分、單位內依資產群組排；同一台的列一定在同一包；每包最多 max_rows 列
    （一台本身就超過上限時，那一台自己一包）。回 [{pkg_no, unit, rows:[cand…]}]，候選會被補上 pkg_no。"""
    by_unit: dict[str, dict[str, dict[str, list]]] = {}
    for c in cands:
        by_unit.setdefault(c["unit"], {}).setdefault(c["asset_group"], {}).setdefault(c["asset_serial"], []).append(c)
    pkgs: list[dict] = []
    for unit in sorted(by_unit):
        cur: list = []
        for grp in sorted(by_unit[unit]):
            for serial in sorted(by_unit[unit][grp]):
                rows = by_unit[unit][grp][serial]
                if cur and len(cur) + len(rows) > max_rows:
                    pkgs.append({"unit": unit, "rows": cur})
                    cur = []
                cur = cur + rows
        if cur:
            pkgs.append({"unit": unit, "rows": cur})
    for i, p in enumerate(pkgs, 1):
        p["pkg_no"] = i
        for c in p["rows"]:
            c["pkg_no"] = i
    return pkgs


def _open_keys(conn) -> set:
    if not _ready(conn):
        return set()
    return {(r[0], r[1], r[2]) for r in conn.execute(
        "SELECT asset_serial, field, old_value FROM cia_fix_item WHERE status IN ('pending','agreed')")}


def _rejected_keys(conn) -> set:
    if not _ready(conn):
        return set()
    return {(r[0], r[1], r[2], r[3] or "") for r in conn.execute(
        "SELECT asset_serial, field, old_value, new_value FROM cia_fix_item WHERE status = 'rejected'")}


def _plan(conn, resend_rejected: bool = False) -> dict:
    rules = load_rules()
    settings = _settings(conn)
    rows, _cols = _cia_rows(conn)
    cands = _Matcher(rules, settings).candidates(rows)
    open_k, rej_k = _open_keys(conn), _rejected_keys(conn)
    keep, in_open, was_rejected = [], [], []
    for c in cands:
        if (c["asset_serial"], c["field"], c["old_value"]) in open_k:
            in_open.append(c)
        elif not resend_rejected and (c["asset_serial"], c["field"], c["old_value"], c["new_value"] or "") in rej_k:
            was_rejected.append(c)
        else:
            keep.append(c)
    max_rows = int(rules.get("max_rows_per_package") or 300)
    pkgs = pack(keep, max_rows)
    fp = _sha(json.dumps([(c["asset_serial"], c["field"], c["old_value"], c["new_value"], c["rule_id"], c["pkg_no"])
                          for c in sorted(keep, key=lambda x: (x["pkg_no"], x["asset_serial"], x["field"]))],
                         ensure_ascii=False).encode())
    return {"rules": rules, "settings": settings, "keep": keep, "in_open": in_open, "was_rejected": was_rejected,
            "pkgs": pkgs, "max_rows": max_rows, "fingerprint": fp}


def _snapshot(rules: dict, settings: dict) -> str:
    """脫敏快照：規則結構＋設定（遮掉任何 IPv4 字面值）。"""
    raw = json.dumps({"rules": rules, "settings": settings}, ensure_ascii=False, sort_keys=True)
    return re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "x.x.x.x", raw)


def _data_date(file_name, at) -> str | None:
    m = re.search(r"(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])", file_name or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ((at or "")[:10] or None)


def _last_cia_import(conn) -> dict:
    """最後一次 CIA 清冊匯入：時間、檔名、資料日期（檔名裡的日期，沒有就用匯入日）、硬體筆數，
    以及上一次的筆數；這次比上一次少超過 drop_warn_ratio 就給一句警示（只警示、不擋——正常改版也可能大量刪減）。"""
    try:
        rs = conn.execute("SELECT imported_at, file_name, hardware_count FROM import_log WHERE source = 'cia_excel' "
                          "ORDER BY imported_at DESC, id DESC LIMIT 2").fetchall()
    except sqlite3.OperationalError:     # 舊庫 import_log 還沒有 source 欄：當沒有 CIA 匯入紀錄
        rs = []
    if not rs:
        return {"at": None, "file": None, "data_date": None, "rows": None, "prev_rows": None, "prev_at": None, "warn": None}
    r, prev = rs[0], (rs[1] if len(rs) > 1 else None)
    out = {"at": r["imported_at"], "file": r["file_name"], "data_date": _data_date(r["file_name"], r["imported_at"]),
           "rows": r["hardware_count"], "prev_rows": prev["hardware_count"] if prev else None,
           "prev_at": prev["imported_at"] if prev else None, "warn": None}
    ratio = float(load_rules().get("drop_warn_ratio") or 0.8)
    if prev and prev["hardware_count"] and r["hardware_count"] is not None and r["hardware_count"] < prev["hardware_count"] * ratio:
        out["warn"] = (f"這份 CIA 檔的硬體筆數 {r['hardware_count']} 比上一次 {prev['hardware_count']} 少了 "
                       f"{prev['hardware_count'] - r['hardware_count']} 筆（超過 {round((1 - ratio) * 100)}%），請確認是不是匯錯檔或匯出不完整")
    return out


# ---------------------------------------------------------------- 預覽、產包

def preview(conn, resend_rejected: bool = False) -> dict:
    p = _plan(conn, resend_rejected)
    labels = field_labels()
    per_rule = []
    for r in p["rules"]["rules"]:
        per_rule.append({"rule_id": r["id"], "label": r["label"], "fields": [labels.get(f, f) for f in r["fields"]],
                         "count": sum(1 for c in p["keep"] if c["rule_id"] == r["id"]),
                         "in_open": sum(1 for c in p["in_open"] if c["rule_id"] == r["id"]),
                         "was_rejected": sum(1 for c in p["was_rejected"] if c["rule_id"] == r["id"])})
    rows = [{**c, "field_label": labels.get(c["field"], c["field"])} for c in p["keep"]]
    return {"total": len(p["keep"]), "assets": len({c["asset_serial"] for c in p["keep"]}),
            "in_open": len(p["in_open"]), "was_rejected": len(p["was_rejected"]),
            "max_rows": p["max_rows"], "rules": per_rule,
            "packages": [{"pkg_no": x["pkg_no"], "unit": x["unit"], "rows": len(x["rows"]),
                          "assets": len({c["asset_serial"] for c in x["rows"]})} for x in p["pkgs"]],
            "rows": rows, "fingerprint": p["fingerprint"], "cia_import": _last_cia_import(conn),
            "room_alias_empty": not (p["settings"].get("cia_fix_room_alias") or {})}


def create_batch(conn, by: str, expect: str, resend_rejected: bool = False) -> dict:
    """在 BEGIN IMMEDIATE 裡重算、重驗（跟預覽的指紋不同就 Stale）再寫入。"""
    if conn.in_transaction:
        conn.commit()
    ensure(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        p = _plan(conn, resend_rejected)
        if p["fingerprint"] != expect:
            raise Stale("預覽之後資料或規則變了（有人匯入 CIA、改了設定、或別人先產了包），請重新預覽")
        if not p["keep"]:
            raise ValueError("目前沒有要修正的項目")
        rules_raw = RULES_PATH.read_bytes()
        cia = _last_cia_import(conn)
        now = _now()
        cur = conn.execute(
            "INSERT INTO cia_fix_batch (kind, title, cia_import_at, cia_import_file, cia_data_date, cia_rows, created_by, created_at, "
            "item_count, pkg_count, max_rows, rules_sha256, settings_sha256, snapshot_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("normalize", "第一批：寫法統一", cia["at"], cia["file"], cia["data_date"], cia["rows"], by, now, len(p["keep"]), len(p["pkgs"]),
             p["max_rows"], _sha(rules_raw), _sha(json.dumps(p["settings"], ensure_ascii=False, sort_keys=True).encode()),
             _snapshot(p["rules"], p["settings"])))
        bid = cur.lastrowid
        for pk in p["pkgs"]:
            for c in pk["rows"]:
                iid = conn.execute(
                    "INSERT INTO cia_fix_item (batch_id, pkg_no, asset_serial, hostname, unit, asset_group, field, old_value, "
                    "new_value, rule_id, reason, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending')",
                    (bid, c["pkg_no"], c["asset_serial"], c["hostname"], c["unit"], c["asset_group"], c["field"],
                     c["old_value"], c["new_value"], c["rule_id"], c["reason"])).lastrowid
                conn.execute("UPDATE cia_fix_item SET track_code = ? WHERE id = ?", (f"CF{bid}-{iid}", iid))
                _event(conn, iid, bid, by, "create", None, "pending", now=now)
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {"batch_id": bid, "items": len(p["keep"]), "packages": len(p["pkgs"])}


def _event(conn, item_id: int, batch_id: int, by, action: str, frm, to: str, *, now: str | None = None,
           reject_code=None, note=None, source_file=None, source_sha=None, cia_value=None) -> None:
    conn.execute(
        "INSERT INTO cia_fix_event (item_id, batch_id, at, by, action, from_status, to_status, reject_code, note, "
        "source_file, source_sha256, cia_value) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (item_id, batch_id, now or _now(), by, action, frm, to, reject_code, note, source_file, source_sha, cia_value))


# ---------------------------------------------------------------- 讀取（不建表）

def counts(conn) -> dict:
    """卡片：各狀態筆數＋未結案批次數。加總＝下鑽由 verify_numbers 釘住。"""
    out = {k: 0 for k in STATUS}
    out.update({"total": 0, "open_batches": 0, "batches": 0, "needs_confirm": 0})
    if not _ready(conn):
        return out
    for st, n in conn.execute("SELECT status, COUNT(*) FROM cia_fix_item GROUP BY status"):
        out[st] = n
        out["total"] += n
    out["needs_confirm"] = sum(out[k] for k in NEEDS_CONFIRM)
    out["batches"] = conn.execute("SELECT COUNT(*) FROM cia_fix_batch").fetchone()[0]
    out["open_batches"] = len(list_batches(conn, open_only=True))
    return out


def _item_dict(r, labels: dict, rule_labels: dict) -> dict:
    d = dict(r)
    d["field_label"] = labels.get(d["field"], d["field"])
    d["status_label"] = STATUS.get(d["status"], d["status"])
    d["rule_label"] = rule_labels.get(d["rule_id"], d["rule_id"])
    return d


def list_items(conn, status: str | None = None, batch_id: int | None = None, pkg_no: int | None = None,
               unit: str | None = None, rule_id: str | None = None, upload_sha: str | None = None) -> list[dict]:
    """status：狀態代號、open（待決定＋同意）、confirm（待人確認＝CIA已另改＋資產已消失）、None＝全部。
    unit＝負責單位、rule_id＝規則、upload_sha＝某一份交回檔記錄過決定的那幾筆（交回後直連本次結果）。"""
    if not _ready(conn):
        return []
    where, args = [], []
    if status == "open":
        where.append("status IN ('pending','agreed')")
    elif status == "confirm":
        where.append(f"status IN ({','.join('?' * len(NEEDS_CONFIRM))})")
        args += list(NEEDS_CONFIRM)
    elif status:
        if status not in STATUS:
            raise ValueError(f"不認得的狀態：{status}")
        where.append("status = ?")
        args.append(status)
    if batch_id is not None:
        where.append("batch_id = ?")
        args.append(batch_id)
    if pkg_no is not None:
        where.append("pkg_no = ?")
        args.append(pkg_no)
    if unit:
        where.append("unit = ?")
        args.append(unit)
    if rule_id:
        where.append("rule_id = ?")
        args.append(rule_id)
    if upload_sha:
        where.append("id IN (SELECT item_id FROM cia_fix_event WHERE action = 'decide' AND source_sha256 = ?)")
        args.append(upload_sha)
    sql = "SELECT * FROM cia_fix_item" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    labels = field_labels()
    rl = {**EXTRA_RULE_LABELS, **{r["id"]: r["label"] for r in load_rules()["rules"]}}
    return [_item_dict(r, labels, rl) for r in conn.execute(sql, args)]


def list_batches(conn, open_only: bool = False) -> list[dict]:
    if not _ready(conn):
        return []
    out = []
    for b in conn.execute("SELECT * FROM cia_fix_batch ORDER BY id DESC"):
        st = {k: 0 for k in STATUS}
        for s, n in conn.execute("SELECT status, COUNT(*) FROM cia_fix_item WHERE batch_id = ? GROUP BY status", (b["id"],)):
            st[s] = n
        n_open = st["pending"] + st["agreed"]
        if open_only and not n_open:
            continue
        pk = [dict(r) for r in conn.execute(
            "SELECT pkg_no, MIN(unit) AS unit, COUNT(*) AS rows, COUNT(DISTINCT asset_serial) AS assets, "
            "SUM(status IN ('pending','agreed')) AS open_rows FROM cia_fix_item WHERE batch_id = ? GROUP BY pkg_no ORDER BY pkg_no",
            (b["id"],))]
        d = {k: b[k] for k in b.keys() if k != "snapshot_json"}
        d.update({"counts": st, "open": n_open, "is_open": n_open > 0, "packages": pk})
        out.append(d)
    return out


def item_events(conn, item_id: int) -> list[dict]:
    if not _ready(conn):
        return []
    return [{**dict(r), "action_label": ACTION_LABEL.get(r["action"], r["action"]),
             "to_label": STATUS.get(r["to_status"], r["to_status"]),
             "from_label": STATUS.get(r["from_status"], r["from_status"]) if r["from_status"] else None}
            for r in conn.execute("SELECT * FROM cia_fix_event WHERE item_id = ? ORDER BY id", (item_id,))]


def last_reconcile(conn) -> dict | None:
    return _setting(conn, SETTING_LAST_RECON, None)


def rules_view(conn) -> dict:
    """規則設定頁：規則清單＋目前設定＋CIA 現有的機房寫法（含筆數），讓管理員對照著填。"""
    rules = load_rules()
    labels = field_labels()
    rows, _ = _cia_rows(conn)
    loc: dict[str, int] = {}
    for r in rows:
        v = (r["physical_location"] or "")
        if v.strip():
            loc[v] = loc.get(v, 0) + 1
    s = get_settings(conn)
    return {"rules": [{"id": r["id"], "label": r["label"], "kind": r["kind"], "fields": [labels.get(f, f) for f in r["fields"]],
                       "reason": r["reason"], "map": r.get("map"), "patterns": r.get("patterns"),
                       "uses_setting": bool(r.get("setting"))} for r in rules["rules"]],
            "max_rows": rules.get("max_rows_per_package"), "reject_codes": rules.get("reject_codes", []),
            "settings": s, "locations": [{"value": k, "count": n, "to": s["room_alias"].get(k, "")}
                                         for k, n in sorted(loc.items(), key=lambda x: (-x[1], x[0]))]}


# ---------------------------------------------------------------- 手動異動

def _items_by_ids(conn, ids: list[int]):
    ids = [int(i) for i in dict.fromkeys(ids or [])]
    if not ids:
        raise ValueError("沒有選任何一筆")
    if len(ids) > 2000:
        raise ValueError("一次最多 2000 筆")
    ph = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT * FROM cia_fix_item WHERE id IN ({ph})", ids).fetchall()
    if len(rows) != len(ids):
        raise LookupError("有幾筆找不到（可能已被刪除或代號不對）")
    return rows


def _begin(conn):
    if not _ready(conn):
        raise LookupError("還沒有任何修正包")
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")


def _open_conflict(conn, r) -> bool:
    return conn.execute("SELECT 1 FROM cia_fix_item WHERE asset_serial = ? AND field = ? AND old_value = ? "
                        "AND status IN ('pending','agreed') AND id <> ?",
                        (r["asset_serial"], r["field"], r["old_value"], r["id"])).fetchone() is not None


def _set_decision(conn, r, to: str, code, note, by, now, *, source_file=None, source_sha=None) -> str | None:
    """改決定（pending／agreed／rejected 之間）。回 None＝成功，否則回不能改的原因。"""
    if r["status"] not in ("pending", "agreed", "rejected"):
        return f"已結案（{STATUS[r['status']]}），不能改決定"
    if to == "rejected" and not code:
        return "不同意要選原因"
    if r["status"] == "rejected" and to != "rejected" and _open_conflict(conn, r):
        return "同一格已在別包處理中，不能改回未結案"
    if r["status"] == to and (r["reject_code"] or None) == (code if to == "rejected" else None) \
            and (r["decision_note"] or None) == (note or None):
        return ""      # 沒變，不記事件
    conn.execute("UPDATE cia_fix_item SET status = ?, reject_code = ?, decision_note = ?, decided_at = ?, decided_by = ? "
                 "WHERE id = ?", (to, code if to == "rejected" else None, note or None, now, by, r["id"]))
    _event(conn, r["id"], r["batch_id"], by, "decide", r["status"], to, now=now, reject_code=code if to == "rejected" else None,
           note=note, source_file=source_file, source_sha=source_sha)
    return None


def decide(conn, ids: list[int], decision: str, reject_code: str | None, note: str | None, by: str) -> dict:
    to = DECISION_OF.get(decision) or (decision if decision in ("pending", "agreed", "rejected") else None)
    if not to:
        raise ValueError("決定只能是 同意／不同意")
    codes = load_rules().get("reject_codes", [])
    if to == "rejected" and reject_code not in codes:
        raise ValueError("不同意要選原因（下拉清單裡的其中一個）")
    _begin(conn)
    try:
        now, done, skipped = _now(), 0, []
        for r in _items_by_ids(conn, ids):
            why = _set_decision(conn, r, to, reject_code, (note or "").strip() or None, by, now)
            if why is None:
                done += 1
            elif why:
                skipped.append({"id": r["id"], "track_code": r["track_code"], "why": why})
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {"updated": done, "skipped": skipped}


def withdraw(conn, ids: list[int], note: str | None, by: str) -> dict:
    note = (note or "").strip()
    if not note:
        raise ValueError("撤回要填原因")
    _begin(conn)
    try:
        now, done, skipped = _now(), 0, []
        for r in _items_by_ids(conn, ids):
            if r["status"] not in OPEN:
                skipped.append({"id": r["id"], "track_code": r["track_code"], "why": f"目前是{STATUS[r['status']]}，不能撤回"})
                continue
            conn.execute("UPDATE cia_fix_item SET status = 'withdrawn', closed_at = ? WHERE id = ?", (now, r["id"]))
            _event(conn, r["id"], r["batch_id"], by, "withdraw", r["status"], "withdrawn", now=now, note=note)
            done += 1
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {"updated": done, "skipped": skipped}


def reopen(conn, ids: list[int], note: str | None, by: str) -> dict:
    """不同意／已撤回／CIA已另改 → 待決定。原值要仍是 CIA 現值（不然重開沒意義），且同一格沒有別包正在處理。"""
    note = (note or "").strip()
    if not note:
        raise ValueError("重開要填原因")
    _begin(conn)
    try:
        now, done, skipped = _now(), 0, []
        for r in _items_by_ids(conn, ids):
            why = None
            if r["status"] not in REOPENABLE:
                why = f"目前是{STATUS[r['status']]}，不能重開"
            elif r["field"] not in _hw_cols(conn):
                why = "欄位已不在資產表"
            else:
                hw = conn.execute(f"SELECT {r['field']} FROM hardware WHERE asset_serial = ?", (r["asset_serial"],)).fetchone()
                if hw is None:
                    why = "這台已不在資產清單"
                elif str(hw[0] if hw[0] is not None else "") != r["old_value"]:
                    why = f"CIA 現值已經不是原值（現在是「{hw[0] if hw[0] is not None else ''}」），請重新產包"
                elif _open_conflict(conn, r):
                    why = "同一格已在別包處理中"
            if why:
                skipped.append({"id": r["id"], "track_code": r["track_code"], "why": why})
                continue
            conn.execute("UPDATE cia_fix_item SET status = 'pending', reject_code = NULL, closed_at = NULL, closed_file = NULL, "
                         "closed_sha256 = NULL, cia_value_at_close = NULL WHERE id = ?", (r["id"],))
            _event(conn, r["id"], r["batch_id"], by, "reopen", r["status"], "pending", now=now, note=note)
            done += 1
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {"updated": done, "skipped": skipped}


# ---------------------------------------------------------------- CIA 匯入後自動核對

def reconcile_after_import(conn, file_name: str | None, file_sha256: str | None, by: str | None) -> dict | None:
    """CIA 清冊匯入完成後呼叫（routers.imports._cia_commit）。只看未結案（待決定／同意）的列：
    CIA 現值＝新值 → 已套用；新值留空（請人填）的：現值≠原值且規則不再抓到 → 已套用，否則仍是錯的 → CIA已另改；
    現值既不是原值也不是新值 → CIA已另改；資產不見 → 資產已消失。沒產過包（表不存在）就什麼都不做、回 None。
    記下是哪一份 CIA 匯入檔（檔名＋雜湊）看到它生效（GPT 第 1 點）。"""
    if not _ready(conn):
        return None
    src = _last_cia_import(conn)          # 呼叫時這次匯入的紀錄已經 commit：就是這一份
    cia = {"data_date": src["data_date"], "rows": src["rows"], "prev_rows": src["prev_rows"], "warn": src["warn"]}
    # 審查 P2(a)：沒有未結案的列就不拿寫入鎖、不寫「上次核對」（不要每次匯入都動一下資料庫）
    if not conn.execute("SELECT 1 FROM cia_fix_item WHERE status IN ('pending','agreed') LIMIT 1").fetchone():
        return {"checked": 0, "applied": 0, "cia_other": 0, "gone": 0, **cia}
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        rules = load_rules()
        m = _Matcher(rules, _settings(conn))
        rmap = {r["id"]: r for r in m.rules}
        now = _now()
        res = {"checked": 0, "applied": 0, "cia_other": 0, "gone": 0}
        cols = _hw_cols(conn)
        for r in conn.execute("SELECT * FROM cia_fix_item WHERE status IN ('pending','agreed') ORDER BY id").fetchall():
            res["checked"] += 1
            if r["field"].startswith("_"):
                # 整筆的動作（同機多筆「請停用或併入」）：這筆從 CIA 消失、或變成退役 → 已套用；還在而且現役 → 不動
                import manage_state
                hw = conn.execute("SELECT asset_status FROM hardware WHERE asset_serial = ?", (r["asset_serial"],)).fetchone()
                st = None if hw is None else ("" if hw[0] is None else str(hw[0]).strip())
                if hw is not None and st not in manage_state.RETIRED_STATUS:
                    continue
                conn.execute("UPDATE cia_fix_item SET status = 'applied', closed_at = ?, closed_file = ?, closed_sha256 = ?, "
                             "cia_value_at_close = ? WHERE id = ?", (now, file_name, file_sha256, st or "（已不在 CIA）", r["id"]))
                _event(conn, r["id"], r["batch_id"], by, "applied", r["status"], "applied", now=now, source_file=file_name,
                       source_sha=file_sha256, cia_value=st or "（已不在 CIA）")
                res["applied"] += 1
                continue
            if r["field"] not in cols:
                continue
            hw = conn.execute(f"SELECT {r['field']} FROM hardware WHERE asset_serial = ?", (r["asset_serial"],)).fetchone()
            cur = None if hw is None else ("" if hw[0] is None else str(hw[0]))
            if hw is None:
                to = "gone"
            elif r["new_value"] is not None:
                to = "applied" if cur == r["new_value"] else (None if cur == r["old_value"] else "cia_other")
            elif cur == r["old_value"]:
                to = None
            else:
                rule = rmap.get(r["rule_id"])
                still_bad = bool(rule) and m.judge(rule, cur)[0]
                to = "cia_other" if (still_bad or not cur.strip()) else "applied"
            if not to:
                continue
            conn.execute("UPDATE cia_fix_item SET status = ?, closed_at = ?, closed_file = ?, closed_sha256 = ?, "
                         "cia_value_at_close = ? WHERE id = ?", (to, now, file_name, file_sha256, cur, r["id"]))
            _event(conn, r["id"], r["batch_id"], by, to, r["status"], to, now=now, source_file=file_name,
                   source_sha=file_sha256, cia_value=cur)
            res[to] += 1
        _put_setting(conn, SETTING_LAST_RECON, {"at": now, "file": file_name, "sha256": file_sha256, "by": by, **res, **cia})
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    _settle_cases(conn, by)
    return {**res, **cia}


def _settle_cases(conn, by) -> None:
    """修正包列狀態變了（CIA 匯入核對、管理人交回）→ 第三批共用案件跟著結案／退回已確認（審查 P2-3）。
    案件結算出錯不影響修正包本身（已 commit），印出來、不往上丟。"""
    try:
        import cia_case
        cia_case.settle(conn, by)
    except Exception as exc:  # noqa: BLE001 - 見 docstring：修正包已寫好，案件下次讀取時會再結算一次
        if conn.in_transaction:
            conn.rollback()
        print(f"[cia_fix] 第三批案件結算失敗（下次讀取會重試）：{exc}")


# ---------------------------------------------------------------- Excel：匯出一包

def _cia_export_text(b) -> str:
    return b["cia_data_date"] or "（產包時還沒有 CIA 匯入紀錄）"


def package_xlsx(conn, batch_id: int, pkg_no: int) -> tuple[io.BytesIO, int, str]:
    """一包一個 Excel（規格六張工作表；P1 填 1／2／5／6，3、4 只有表頭）。回 (檔案, 列數, 檔名)。"""
    import excel_import
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Protection
    from openpyxl.utils import get_column_letter as col_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    if not _ready(conn):
        raise LookupError("還沒有任何修正包")
    b = conn.execute("SELECT * FROM cia_fix_batch WHERE id = ?", (batch_id,)).fetchone()
    if b is None:
        raise LookupError(f"找不到第 {batch_id} 批")
    items = list_items(conn, batch_id=batch_id, pkg_no=pkg_no)
    if not items:
        raise LookupError(f"第 {batch_id} 批沒有第 {pkg_no} 包")
    rules = load_rules()
    codes = rules.get("reject_codes", [])
    mapping = excel_import.load_mapping().get("硬體", {})
    bold = Font(bold=True)
    hl = PatternFill("solid", fgColor="DFF2EC")
    wb = Workbook()

    # 1 說明
    ws = wb.active
    ws.title = "1 說明"
    unit = items[0]["unit"]
    lines = [
        ("CIA 修正包", f"第 {batch_id} 批・第 {pkg_no} 包（共 {b['pkg_count']} 包）"),
        ("內容", b["title"] or ""),
        ("負責單位", unit),
        ("CIA 匯出日期", _cia_export_text(b)),
        ("產生時間", b["created_at"]),
        ("這包筆數", f"{len(items)} 格（{len({i['asset_serial'] for i in items})} 台）"),
        ("怎麼審", "到「2 修正明細」逐列在「同意」欄選 同意／不同意；不同意請在「不同意原因」欄選原因，需要說明寫在「備註」。"),
        ("", "其他欄位已鎖定，請勿修改；交回時系統會逐列比對，被改過的列不會記錄、會列在差異報告裡。"),
        ("套用", "同意的請在 CIA 依「資產序號＋欄位」逐格修改。「6 修正後全表」只當參考，請勿整份覆蓋，以免蓋掉這段期間別人的改動。"),
        ("新值空白", "新值空白的列＝系統只能指出有錯、不知道正確值，請在 CIA 填正確值。"),
        ("3、4 工作表", "補登清單與待填檢查是下一批（補登）用的，這一批沒有內容。"),
    ]
    for k, v in lines:
        ws.append([k, v])
        ws.cell(ws.max_row, 1).font = bold
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 100

    # 2 修正明細
    ws = wb.create_sheet(DETAIL_SHEET)
    ws.append(DETAIL_COLS)
    for c in ws[1]:
        c.font = bold
    dec_text = {"agreed": "同意", "rejected": "不同意"}
    for n, it in enumerate(items, 1):
        ws.append([n, it["asset_serial"], it["hostname"] or "", it["field_label"], it["old_value"],
                   it["new_value"] if it["new_value"] is not None else "", it["reason"] or "", it["status_label"],
                   dec_text.get(it["status"], ""), it["reject_code"] or "", it["decision_note"] or "", it["track_code"]])
    last = ws.max_row
    # 審查 P2(b)：明細格一律存文字、格式設「文字」，管理人在 Excel 裡打 001 不會被轉成 1（讀回來另有數字正規化兜底）
    for row in ws.iter_rows(min_row=2, max_row=last, min_col=2, max_col=len(DETAIL_COLS)):
        for cell in row:
            cell.number_format = "@"
    unlocked = Protection(locked=False)
    dec_col, why_col, note_col = (DETAIL_COLS.index(x) + 1 for x in ("同意", "不同意原因", "備註"))
    for row in range(2, last + 1):
        for col in (dec_col, why_col, note_col):
            ws.cell(row, col).protection = unlocked
    dv1 = DataValidation(type="list", formula1='"' + ",".join(rules.get("decision_options", ["同意", "不同意"])) + '"', allow_blank=True)
    dv2 = DataValidation(type="list", formula1=f"='5 選項清單'!$B$2:$B${len(codes) + 1}", allow_blank=True)
    ws.add_data_validation(dv1)
    ws.add_data_validation(dv2)
    if last >= 2:
        dv1.add(f"{col_letter(dec_col)}2:{col_letter(dec_col)}{last}")
        dv2.add(f"{col_letter(why_col)}2:{col_letter(why_col)}{last}")
    ws.column_dimensions[col_letter(len(DETAIL_COLS))].hidden = True
    for col, w in zip("ABCDEFGHIJK", (6, 16, 18, 14, 22, 22, 36, 10, 8, 26, 24)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "B2"
    ws.protection.sheet = True          # 決定欄以外鎖定（不設密碼：防手滑，竄改另由回檔比對擋）
    ws.protection.formatRows = False
    ws.protection.formatColumns = False

    # 3 補登清單／4 待填檢查：下一批才有內容，這一批只有表頭
    ws = wb.create_sheet("3 補登清單")
    ws.append(list(mapping.keys()))
    ws = wb.create_sheet("4 待填檢查")
    ws.append(["項次", "資產序號", "主機名稱", "還缺哪幾格"])

    # 5 選項清單
    ws = wb.create_sheet("5 選項清單")
    ws.append(["同意", "不同意原因", "機房標準名稱", "「沒有」的寫法"])
    stds = sorted(set((_setting(conn, "cia_fix_room_alias", {}) or {}).values()))
    nones = ["NA（一般欄位）", "0（硬體編號）", "無（BIG IP/VIP）"]
    opts = rules.get("decision_options", ["同意", "不同意"])
    for i in range(max(len(opts), len(codes), len(stds), len(nones))):
        ws.append([opts[i] if i < len(opts) else None, codes[i] if i < len(codes) else None,
                   stds[i] if i < len(stds) else None, nones[i] if i < len(nones) else None])

    # 6 修正後全表（參考）：CIA 硬體頁全部正本列，套用這一批未結案（待決定＋同意）的修正，改到的格子標色
    ws = wb.create_sheet("6 修正後全表（參考）")
    heads = list(mapping.keys())
    cols = list(mapping.values())
    ws.append([f"CIA 匯出日期 {_cia_export_text(b)}・套用第 {batch_id} 批未結案的修正（標色的格子）・只當參考，請依「2 修正明細」逐格套用"])
    ws.append(heads)
    fixes = {(r["asset_serial"], r["field"]): r["new_value"] for r in conn.execute(
        "SELECT asset_serial, field, new_value FROM cia_fix_item WHERE batch_id = ? AND status IN ('pending','agreed') "
        "AND new_value IS NOT NULL", (batch_id,))}
    rows, hw_cols = _cia_rows(conn)
    for r in rows:
        vals = []
        marks = []
        for i, c in enumerate(cols, 1):
            v = r[c] if c in hw_cols else None
            if (r["asset_serial"], c) in fixes:
                v = fixes[(r["asset_serial"], c)]
                marks.append(i)
            vals.append(v)
        ws.append(vals)
        for i in marks:
            ws.cell(ws.max_row, i).fill = hl

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"CIA修正包_第{batch_id}批_第{pkg_no}包_{unit}_CIA{b['cia_data_date'] or '未匯入'}.xlsx"
    return buf, len(items), re.sub(r'[\\/:*?"<>|]', "_", fname)


# ---------------------------------------------------------------- Excel：管理人交回的檔

def _txt(v) -> str:
    """Excel 格子 → 文字。數字格（管理人或 Excel 自己把 001 轉成 1、0 轉成 0.0）照數字寫回文字，不留 .0。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v).strip()


def _same(file_v: str, sys_v: str) -> bool:
    """回檔比對用：文字相同；或系統值是純數字、檔案值只是被 Excel 去掉前導零（007 → 7、00 → 0）。
    不用 float 比：長序號會失真（20 位數尾數不同也相等），'1_0' 也會被當成 10（11-10 審查 P2）。
    0 vs 0.0 已由 _txt 把數字格轉回 "0"，這裡不用再寬鬆。"""
    a, b = file_v.strip(), str(sys_v or "").strip()
    if a == b:
        return True
    na, nb = _digits_norm(a), _digits_norm(b)
    return na is not None and na == nb


def _digits_norm(s: str):
    """純數字（可帶 .0）→ 去前導零、去 .0 的字串；不是純數字回 None。只做字串處理，不轉 float。"""
    m = re.fullmatch(r"([0-9]+)(?:\.0+)?", s)
    return (m.group(1).lstrip("0") or "0") if m else None


def _row_brief(n: int, code: str, r, labels: dict) -> dict:
    return {"row": n, "track_code": code, "item_id": r["id"], "asset_serial": r["asset_serial"], "hostname": r["hostname"],
            "field_label": labels.get(r["field"], r["field"]), "old_value": r["old_value"], "new_value": r["new_value"],
            "status_label": STATUS.get(r["status"], r["status"])}


def _read_return(conn, data: bytes) -> dict:
    """讀交回的 Excel，逐列分類（不寫庫）。沒有任何一列的追蹤碼對得上＝不是本系統產的包，整份拒收。"""
    from openpyxl import load_workbook

    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("檔案超過 10MB")
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl 對壞檔的錯誤型態不一，統一回「不是修正包」
        raise ValueError(f"讀不開這個檔（不是 .xlsx？）：{exc}") from exc
    if DETAIL_SHEET not in wb.sheetnames:
        raise ValueError(f"這不是 CIA 修正包：找不到「{DETAIL_SHEET}」工作表（這份檔案的工作表：{wb.sheetnames}）")
    ws = wb[DETAIL_SHEET]
    it = ws.iter_rows(values_only=True)
    head = [_txt(h) for h in next(it, [])]
    miss = [h for h in ("同意", "不同意原因", "備註", TRACK_HEADER, *_LOCKED_COMPARE) if h not in head]
    if miss:
        raise ValueError(f"修正明細少了欄位：{'、'.join(miss)}（請用系統產生的檔案）")
    ix = {h: head.index(h) for h in head if h}
    codes = load_rules().get("reject_codes", [])
    labels = field_labels()
    out = {"record": [], "same": [], "blank": [], "tampered": [], "unknown": [], "invalid": [], "closed": [], "rows": 0}
    cache: dict[str, sqlite3.Row | None] = {}
    seen: set[str] = set()
    pk_keys: set[tuple[int, int]] = set()
    for n, row in enumerate(it, 2):
        if row is None or all(v in (None, "") for v in row):
            continue
        out["rows"] += 1
        g = lambda h: _txt(row[ix[h]]) if ix[h] < len(row) else ""  # noqa: E731
        code = g(TRACK_HEADER)
        m = re.fullmatch(r"CF(\d+)-(\d+)", code)
        r = None
        if m and _ready(conn):
            if code not in cache:
                cache[code] = conn.execute("SELECT * FROM cia_fix_item WHERE id = ? AND track_code = ?",
                                           (int(m.group(2)), code)).fetchone()
            r = cache[code]
        if r is None:
            out["unknown"].append({"row": n, "track_code": code or "（空白）", "asset_serial": g("資產序號")})
            continue
        pk_keys.add((r["batch_id"], r["pkg_no"]))
        if code in seen:
            out["invalid"].append({"row": n, "track_code": code, "asset_serial": r["asset_serial"], "why": "同一筆出現兩次，只看第一次"})
            continue
        seen.add(code)
        sysv = {"資產序號": r["asset_serial"], "欄位": labels.get(r["field"], r["field"]), "原值": r["old_value"],
                "新值": r["new_value"] or ""}
        diffs = [{"col": h, "file": g(h), "system": sysv[h]} for h in _LOCKED_COMPARE if not _same(g(h), sysv[h])]
        if diffs:
            out["tampered"].append({"row": n, "track_code": code, "item_id": r["id"], "asset_serial": r["asset_serial"], "diffs": diffs})
            continue
        dec, why, note = g("同意"), g("不同意原因"), g("備註")
        if not dec:
            out["blank"].append(_row_brief(n, code, r, labels))
            continue
        to = DECISION_OF.get(dec)
        if not to:
            out["invalid"].append({"row": n, "track_code": code, "asset_serial": r["asset_serial"], "why": f"「同意」欄只能選 同意／不同意（寫的是「{dec}」）"})
            continue
        if to == "rejected" and why not in codes:
            out["invalid"].append({"row": n, "track_code": code, "asset_serial": r["asset_serial"],
                                   "why": "不同意沒有選原因" if not why else f"不同意原因不在選項裡（寫的是「{why}」）"})
            continue
        if r["status"] not in ("pending", "agreed", "rejected"):
            out["closed"].append({"row": n, "track_code": code, "asset_serial": r["asset_serial"], "status": STATUS[r["status"]]})
            continue
        if r["status"] == to and (r["reject_code"] or "") == (why if to == "rejected" else "") and (r["decision_note"] or "") == note:
            out["same"].append({**_row_brief(n, code, r, labels), "decision": dec, "reject_code": r["reject_code"]})
            continue
        out["record"].append({"row": n, "item_id": r["id"], "track_code": code, "asset_serial": r["asset_serial"],
                              "hostname": r["hostname"], "field_label": sysv["欄位"], "old_value": r["old_value"],
                              "new_value": r["new_value"], "from": STATUS[r["status"]], "to": to, "to_label": STATUS[to],
                              "reject_code": why if to == "rejected" else None, "note": note or None})
    wb.close()
    if not out["rows"]:
        raise ValueError("修正明細沒有任何資料列")
    if not pk_keys:
        raise ValueError("這不是本系統產生的修正包：沒有任何一列的追蹤碼對得上（請用 9.3 下載的檔案，不要另存成新表）")
    out["packages"] = _packages_info(conn, pk_keys)
    return out


def _packages_info(conn, keys: set) -> list[dict]:
    """交回檔裡出現的每一包：批次、包號、負責單位、產生時間、這包現在幾筆待決定。"""
    out = []
    for bid, pno in sorted(keys):
        b = conn.execute("SELECT title, created_at, created_by, cia_data_date FROM cia_fix_batch WHERE id = ?", (bid,)).fetchone()
        st = conn.execute("SELECT MIN(unit), COUNT(*), SUM(status = 'pending') FROM cia_fix_item WHERE batch_id = ? AND pkg_no = ?",
                          (bid, pno)).fetchone()
        out.append({"batch_id": bid, "pkg_no": pno, "title": b["title"] if b else None, "created_at": b["created_at"] if b else None,
                    "created_by": b["created_by"] if b else None, "cia_data_date": b["cia_data_date"] if b else None,
                    "unit": st[0], "rows": st[1], "pending": st[2] or 0})
    return out


def _still_pending(conn, keys: set, will_decide: set[int]) -> list[dict]:
    """這幾包裡（記錄完之後）仍是「待決定」的列——沒填、被擋、或不在這份檔裡的，都還要再追。"""
    labels = field_labels()
    out = []
    for bid, pno in sorted(keys):
        for r in conn.execute("SELECT * FROM cia_fix_item WHERE batch_id = ? AND pkg_no = ? AND status = 'pending' ORDER BY id",
                              (bid, pno)):
            if r["id"] not in will_decide:
                out.append(_row_brief(0, r["track_code"], r, labels) | {"pkg": f"第 {bid} 批第 {pno} 包"})
    return out


def upload_preview(conn, data: bytes, file_name: str | None) -> dict:
    res = _read_return(conn, data)
    keys = {(p["batch_id"], p["pkg_no"]) for p in res["packages"]}
    return {**res, "file_name": file_name, "file_sha256": _sha(data),
            "agree": sum(1 for x in res["record"] if x["to"] == "agreed"),
            "reject": sum(1 for x in res["record"] if x["to"] == "rejected"),
            "still_pending": _still_pending(conn, keys, {x["item_id"] for x in res["record"]})}


def upload_confirm(conn, data: bytes, file_name: str | None, expect_sha: str, by: str) -> dict:
    """被改過／不合格的列不記，其餘正常列照記（審查第 4 點：管理人改錯一格不該整包重來）；結果明列被擋的列與這幾包剩下的待決定。"""
    if _sha(data) != expect_sha:
        raise Stale("檔案跟預覽時不同，請重新預覽")
    _begin(conn)
    try:
        res = _read_return(conn, data)          # 在寫入鎖裡重讀一次：預覽之後有人改過決定，以現在為準
        now, done, skipped = _now(), 0, []
        sha = _sha(data)
        for x in res["record"]:
            r = conn.execute("SELECT * FROM cia_fix_item WHERE id = ?", (x["item_id"],)).fetchone()
            why = _set_decision(conn, r, x["to"], x["reject_code"], x["note"], by, now, source_file=file_name, source_sha=sha)
            if why is None:
                done += 1
            elif why:
                skipped.append({"row": x["row"], "track_code": x["track_code"], "why": why})
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    keys = {(p["batch_id"], p["pkg_no"]) for p in res["packages"]}
    _settle_cases(conn, by)
    return {"recorded": done, "file_sha256": sha, "skipped": skipped, "tampered": res["tampered"], "unknown": res["unknown"],
            "invalid": res["invalid"], "closed": res["closed"], "blank": res["blank"], "same": res["same"],
            "packages": _packages_info(conn, keys), "still_pending": _still_pending(conn, keys, set())}


def facets(conn) -> dict:
    """逐筆明細的篩選選項（批次、包、負責單位、規則），從現有資料來，不寫死。"""
    if not _ready(conn):
        return {"batches": [], "packages": [], "units": [], "rules": []}
    rl = {**EXTRA_RULE_LABELS, **{r["id"]: r["label"] for r in load_rules()["rules"]}}
    return {"batches": [r[0] for r in conn.execute("SELECT id FROM cia_fix_batch ORDER BY id DESC")],
            "packages": [{"batch_id": r[0], "pkg_no": r[1], "unit": r[2]} for r in conn.execute(
                "SELECT batch_id, pkg_no, MIN(unit) FROM cia_fix_item GROUP BY batch_id, pkg_no ORDER BY batch_id DESC, pkg_no")],
            "units": [r[0] for r in conn.execute("SELECT DISTINCT unit FROM cia_fix_item ORDER BY unit")],
            "rules": [{"id": r[0], "label": rl.get(r[0], r[0])} for r in conn.execute("SELECT DISTINCT rule_id FROM cia_fix_item ORDER BY rule_id")]}
