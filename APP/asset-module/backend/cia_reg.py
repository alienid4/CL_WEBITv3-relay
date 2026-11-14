"""9.4 CIA 補登（P2：VM 補登，分派給多人；試辦）。

設計：docs/設計_CIA補登P2_2026-11-11.md（含 GPT 12 點接受／不接受）；規格：AI_note 規格_CIA修正包與補登_2026-11-10.md 五／五之一／六／九。

## 誰、怎麼用
SP 在 9.4 設好各類負責人 → 「預覽分派」（母體＝2.1「未登錄 CIA」裡的 VM，排除清單附原因）→ 確認 → 每人下載一個 Excel 寄出
→ 負責人在 Excel 確認 APID（附建議、信心、依據）、補幾格 → SP 在 ⚙ 資料匯入上傳交回檔（兩段式）→ 逐列：拒收／退回／通過／說不登記
→ 「彙整」把通過的切包交 CIA 管理人 → 之後每次 CIA 匯入，系統自動核對：那台變成 CIA 有登記 → 已登記。

## 不做的事（D4）
不寫 hardware、不改 CIA；只記「建議、分派、交回、彙整、核對」。CIA 正本只由 CIA 匯入寫入。

## 同一台（D3）
母體＝v2_hosts.machines() 的「未登錄 CIA」頁籤（＝2.1 同一份計算）∩ 虛擬機（vm_keys，全站一個定義）；一台＝一個 machine_key。
另存 RVTools VM UUID：同一個 machine_key 有兩個 UUID、或一個 UUID 掛在兩個 machine_key → 待確認，不分派（GPT 第 6 點）。

## APID 建議（GPT 第 3、8 點）
依據（命名前綴、同網段、同 cluster、同 ESXi、vCenter 資料夾…）彼此相關，不能當獨立證據相乘；所以「高信心」是**量出來的**：
拿 CIA 已登記、APID 已知的 VM 逐台留一（hold-out）回測，每個依據的準確率 ≥ precision_min 才准預填。回測數字在 9.4 看得到、可下鑽。

讀取函式不建表；寫入前 ensure()。每次異動寫 cia_reg_event（只增不改）。
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import re
import sqlite3
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import cia_fix
import fleet_cache as _fleet_cache

RULES_PATH = Path(__file__).parent / "cia_reg_rules.json"
SET_ASSIGNEES = "cia_reg_assignees"
SET_PILOT = "cia_reg_pilot"
SET_LAST_RECON = "cia_reg_last_reconcile"

STATUS = {
    "review": "待確認（身分）", "unassigned": "待分派", "assigned": "已分派", "bounced": "退回重填", "passed": "通過",
    "claim": "說不登記（待確認）", "merged": "已彙整", "registered": "已登記", "excluded": "撤出", "gone": "資產已消失",
    "missing": "待確認（找不到）",
}
CLOSED = ("registered", "excluded", "gone")
OPEN = tuple(k for k in STATUS if k not in CLOSED)
REASSIGNABLE = ("review", "unassigned", "assigned", "bounced", "claim")
WITHDRAWABLE = ("review", "unassigned", "assigned", "bounced", "claim", "passed")
#: 連續幾次 CIA 匯入後核對都找不到（所有來源都沒有這台）才自動結案成「資產已消失」（審查 P2-1：一次找不到可能只是改名／鍵變）
GONE_AFTER_MISSES = 2
#: 一次匯入讓超過這個比例的未結案項目「找不到」＝可疑的匯入（匯錯檔、匯出不完整）：這次不計次、只在核對結果寫警示（審查 P1-1c）。
#: 未結案少於 SUSPICIOUS_MIN_OPEN 台時不套這條（3 台掉 1 台就 33%，小試辦會永遠被當可疑）。
SUSPICIOUS_MISS_RATIO = 0.2
SUSPICIOUS_MIN_OPEN = 10
CONF_LABEL = {"high": "高", "mid": "中", "low": "低"}
UNDETERMINED = "無法判定"
SHEET_INFO, SHEET_FILL, SHEET_CHECK, SHEET_DERIVED, SHEET_OPTS = "1 說明", "2 補登填寫", "3 待填檢查", "4 APID 帶出", "5 選項清單"
PERSON_SHEETS = (SHEET_INFO, SHEET_FILL, SHEET_CHECK, SHEET_DERIVED, SHEET_OPTS)
TRACK, VER, KEYH = "追蹤碼", "分派版本", "機器鍵"
EXTRA_COLS = ["APID 確認", "不登記原因", "建議 APID", "信心", "依據", "觀測狀態（參考）", "觀測作業系統（參考）", TRACK, VER, KEYH]
MAX_UPLOAD_BYTES = cia_fix.MAX_UPLOAD_BYTES

SCHEMA = """
CREATE TABLE IF NOT EXISTS cia_reg_round (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, created_by TEXT, categories_json TEXT,
    counts_json TEXT, backtest_json TEXT, rules_sha256 TEXT, settings_sha256 TEXT, snapshot_json TEXT, cia_data_date TEXT
);
CREATE TABLE IF NOT EXISTS cia_reg_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT, round_id INTEGER NOT NULL, no INTEGER, track_code TEXT,
    machine_key TEXT NOT NULL, key_hash TEXT, vm_uuid TEXT, hostname TEXT, ip TEXT, category TEXT, group_key TEXT,
    assignee TEXT, assign_ver INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL,
    apid_suggest TEXT, apid_conf TEXT, basis_text TEXT, basis_json TEXT, fill_json TEXT, observed TEXT, observed_os TEXT,
    filled_json TEXT, check_json TEXT, returned_at TEXT, reg_batch_id INTEGER, pkg_no INTEGER, cia_serial TEXT,
    created_at TEXT NOT NULL, updated_at TEXT, closed_at TEXT, closed_file TEXT, closed_sha256 TEXT, note TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cia_reg_item_open ON cia_reg_item(machine_key)
    WHERE status NOT IN ('registered', 'excluded', 'gone');
CREATE TABLE IF NOT EXISTS cia_reg_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL, round_id INTEGER, at TEXT NOT NULL, by TEXT,
    action TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL, assignee TEXT, assign_ver INTEGER,
    source_file TEXT, source_sha256 TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS ix_cia_reg_event_item ON cia_reg_event(item_id);
CREATE TABLE IF NOT EXISTS cia_reg_upload (
    sha256 TEXT PRIMARY KEY, file_name TEXT, assignee TEXT, at TEXT NOT NULL, by TEXT, result_json TEXT
);
CREATE TABLE IF NOT EXISTS cia_reg_file (
    id INTEGER PRIMARY KEY AUTOINCREMENT, assignee TEXT NOT NULL, part INTEGER, parts INTEGER, bounced INTEGER DEFAULT 0,
    file_name TEXT, sha256 TEXT, items_json TEXT, rounds_json TEXT, machines INTEGER, max_ver INTEGER,
    generated_at TEXT NOT NULL, generated_by TEXT, via TEXT, sent_at TEXT, sent_by TEXT
);
CREATE TABLE IF NOT EXISTS cia_reg_batch (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, created_by TEXT, item_count INTEGER NOT NULL,
    pkg_count INTEGER NOT NULL, max_rows INTEGER, cia_import_at TEXT, cia_data_date TEXT
);
"""

ACTION_LABEL = {"create": "建立", "assign": "分派", "reassign": "改派", "pass": "交回：通過", "bounce": "交回：退回",
                "claim": "交回：說不登記", "claim_accept": "確認不登記", "claim_reject": "不接受不登記（退回）",
                "withdraw": "撤出", "review": "轉待確認（身分）", "merge": "彙整", "registered": "核對：已登記", "gone": "核對：資產已消失",
                "missing": "核對：找不到（待確認）", "found": "核對：又找到了（回原狀態）", "confirm_gone": "確認資產已消失",
                "reopen_missing": "找不到→恢復原狀態", "restore_gone": "資產已消失→恢復", "miss_same_file": "核對：同一份檔重匯，不重複計次"}


class Stale(Exception):
    """預覽之後資料變了（409）。"""


# ---------------------------------------------------------------- 共用

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_ADD_COLS = {"cia_reg_round": [("deadline", "TEXT")],
             "cia_reg_item": [("miss_count", "INTEGER DEFAULT 0"), ("prev_status", "TEXT"), ("last_miss_sha", "TEXT")]}


def ensure(conn) -> None:
    conn.executescript(SCHEMA)
    for t, cols in _ADD_COLS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
        for c, typ in cols:
            if c not in have:
                conn.execute(f"ALTER TABLE {t} ADD COLUMN {c} {typ}")
    conn.commit()


def _ready(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cia_reg_item'").fetchone() is not None


#: 測試用：放寬門檻（正式一律讀規則檔）
_RULES_OVERRIDE: dict | None = None


def load_rules() -> dict:
    if _RULES_OVERRIDE is not None:
        return json.loads(json.dumps(_RULES_OVERRIDE))
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def _begin(conn):
    if conn.in_transaction:
        conn.commit()
    ensure(conn)
    conn.execute("BEGIN IMMEDIATE")


def _rollback(conn):
    if conn.in_transaction:
        conn.rollback()


_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def safe(v) -> str:
    """寫進 Excel 的文字（GPT 第 8 點）：去控制字元；= + - @ 開頭（或 Tab／CR 開頭）前面加 ' 讓它不會被當公式。"""
    s = _ILLEGAL.sub("", "" if v is None else str(v))
    if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s
    return s


def fname_safe(s: str) -> str:
    """檔名用：去控制字元與 Windows 不允許的字元。"""
    return re.sub(r'[\x00-\x1f\x7f\\/:*?"<>|]', "_", str(s or "")).strip() or "_"


def _person_label(names: dict, key: str) -> str:
    """檔名裡的負責人：顯示名稱＋員編（同名的人才分得開）；名稱就是代號時只寫一次。"""
    nm = names.get(key, key)
    return fname_safe(nm if nm == key else f"{nm}_{key}")


def _key_hash(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()[:16]


def _event(conn, item_id, round_id, by, action, frm, to, *, assignee=None, ver=None, now=None, source_file=None,
           source_sha=None, note=None):
    conn.execute("INSERT INTO cia_reg_event (item_id, round_id, at, by, action, from_status, to_status, assignee, assign_ver, "
                 "source_file, source_sha256, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (item_id, round_id, now or _now(), by, action, frm, to, assignee, ver, source_file, source_sha, note))


def _ad_rows(conn) -> dict:
    """AD 人員名單（ad_person）：員編 → 列。沒有匯過 AD 回 {}（那時允許只填姓名，畫面標「未對 AD」）。"""
    try:
        return {r["employee_id"]: dict(r) for r in conn.execute(
            "SELECT employee_id, display_name, mail, dept_name, enabled FROM ad_person")}
    except sqlite3.OperationalError:   # 還沒匯過 AD 名單：沒有表就是沒有名單（不是錯）
        return {}


def _person_warn(p: dict, ad: dict) -> str | None:
    if not ad:
        return "還沒匯入 AD 名單，無法核對"
    a = ad.get(p.get("key") or "")
    if a is None:
        return "AD 名單裡找不到（可能已離職）"
    if not a.get("enabled"):
        return "AD 帳號已停用（可能已離職）"
    if not (a.get("mail") or "").strip():
        return "AD 沒有信箱"
    return None


def _norm_person(p: dict, ad: dict) -> dict:
    key = str(p.get("key") or p.get("employee_id") or "").strip()
    a = ad.get(key) or {}
    name = str(a.get("display_name") or p.get("name") or key).strip()
    cap = p.get("cap")
    try:
        cap = int(cap) if cap not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError(f"{name} 的上限要是數字")
    if cap is not None and cap < 0:
        raise ValueError(f"{name} 的上限不能是負數")
    return {"key": key or name, "name": name, "email": str(a.get("mail") or p.get("email") or "").strip()[:120],
            "dept": a.get("dept_name") or "", "cap": cap, "paused": bool(p.get("paused"))}


def get_settings(conn) -> dict:
    rules = load_rules()
    raw = cia_fix._setting(conn, SET_ASSIGNEES, {}) or {}
    pilot = cia_fix._setting(conn, SET_PILOT, None)
    ad = _ad_rows(conn)
    out = {}
    for c in rules["categories"]:
        lst = []
        for p in raw.get(c, []):
            q = _norm_person({**p, "key": p.get("key") or p.get("name")}, ad)
            q["warn"] = _person_warn(q, ad)
            lst.append(q)
        out[c] = lst
    return {"assignees": out, "ad_loaded": bool(ad),
            "pilot": pilot if isinstance(pilot, list) and pilot else list(rules.get("pilot_default") or rules["categories"]),
            "categories": rules["categories"], "per_person_cap": rules["per_person_cap"]}


def people_names(conn) -> dict:
    """負責人代號（員編）→ 顯示名稱（設定裡的＋AD 名單）。"""
    ad = _ad_rows(conn)
    out = {k: (v.get("display_name") or k) for k, v in ad.items()}
    for lst in (cia_fix._setting(conn, SET_ASSIGNEES, {}) or {}).values():
        for p in lst:
            k = p.get("key") or p.get("name")
            out.setdefault(k, p.get("name") or k)
    return out


def people_search(conn, q: str, limit: int = 30) -> list[dict]:
    """分派設定挑人：AD 名單裡啟用、有信箱的人（員編唯一）。"""
    q = (q or "").strip().lower()
    out = []
    for a in _ad_rows(conn).values():
        if not a.get("enabled") or not (a.get("mail") or "").strip():
            continue
        hay = f"{a['employee_id']} {a.get('display_name') or ''} {a.get('mail') or ''} {a.get('dept_name') or ''}".lower()
        if q and q not in hay:
            continue
        out.append({"key": a["employee_id"], "name": a.get("display_name") or a["employee_id"], "email": a.get("mail"), "dept": a.get("dept_name") or ""})
        if len(out) >= limit:
            break
    return out


def save_settings(conn, assignees: dict | None, pilot: list | None) -> dict:
    """負責人一律從 AD 名單挑（員編、要啟用、要有信箱）；還沒匯過 AD 才允許只填姓名。"""
    cats = load_rules()["categories"]
    ad = _ad_rows(conn)
    if assignees is not None:
        clean = {}
        for c, people in assignees.items():
            if c not in cats:
                raise ValueError(f"不認得的類別：{c}")
            seen, lst = set(), []
            for p in people or []:
                p = p or {}
                key = str(p.get("key") or p.get("employee_id") or (p.get("name") if not ad else "") or "").strip()
                if not key:
                    if ad:
                        raise ValueError(f"{c}：負責人要從 AD 名單挑（員編）")
                    continue
                if key in seen:
                    continue
                if len(key) > 60:
                    raise ValueError("代號太長")
                if ad:
                    a = ad.get(key)
                    if a is None:
                        raise ValueError(f"{c}：AD 名單裡沒有 {key}")
                    if not a.get("enabled"):
                        raise ValueError(f"{c}：{a.get('display_name') or key} 的 AD 帳號已停用")
                    if not (a.get("mail") or "").strip():
                        raise ValueError(f"{c}：{a.get('display_name') or key} 在 AD 沒有信箱")
                q = _norm_person({**p, "key": key}, ad)
                seen.add(key)
                lst.append({"key": q["key"], "name": q["name"], "email": q["email"], "cap": q["cap"], "paused": q["paused"]})
            clean[c] = lst
        cia_fix._put_setting(conn, SET_ASSIGNEES, clean)
    if pilot is not None:
        bad = [c for c in pilot if c not in cats]
        if bad or not pilot:
            raise ValueError("試辦類別至少選一類，而且要是清單裡的類別")
        cia_fix._put_setting(conn, SET_PILOT, list(dict.fromkeys(pilot)))
    conn.commit()
    return get_settings(conn)


# ---------------------------------------------------------------- 機器事實

def _short(h: str | None) -> str:
    import system_stats

    return system_stats.strip_domain(h or "").strip().lower()


def _prefix(h: str | None, rules: dict) -> str | None:
    cfg = rules["name_prefix"]
    s = _short(h)
    if cfg.get("split_on") and cfg["split_on"] in s:
        s = s.split(cfg["split_on"])[0]
    if cfg.get("strip_trailing_digits"):
        s = re.sub(r"\d+$", "", s)
    return s if len(s) >= int(cfg.get("min_len", 3)) else None


def _subnet(ip: str | None) -> str | None:
    import manage_state

    for x in manage_state.ips_of(ip):
        try:
            a = ipaddress.ip_address(x)
        except ValueError:
            continue
        if a.version == 4 and str(a) != "0.0.0.0":
            return ".".join(str(a).split(".")[:3])
    return None


def _vc_recs(conn) -> dict[int, dict]:
    """hardware.id → 最新一筆 RVTools vInfo 紀錄（cluster、ESXi、電源、UUID、Folder、Template）。"""
    out: dict[int, tuple] = {}
    try:
        rows = conn.execute("SELECT resolved_hardware_id, collected_at, payload FROM source_record "
                            "WHERE source = 'vcenter' AND resolved_hardware_id IS NOT NULL").fetchall()
    except sqlite3.OperationalError:   # 舊庫沒有 source_record 表＝沒有 RVTools 資料（不是錯）：依據少了 cluster／ESXi／資料夾
        return {}
    for r in rows:
        try:
            p = json.loads(r["payload"] or "{}")
        except ValueError:
            continue
        raw = p.get("raw") or {}
        rec = {"cluster": p.get("cluster"), "esxi": p.get("esxi_host"), "power": p.get("powerstate"), "uuid": p.get("vm_uuid"),
               "folder": raw.get("Folder") or raw.get("Folder Path"), "template": str(raw.get("Template") or "").strip().lower() in ("true", "1", "yes"),
               "vi": p.get("vi_sdk_server"), "seen": r["collected_at"]}
        prev = out.get(r["resolved_hardware_id"])
        if prev is None or (r["collected_at"] or "") >= (prev[0] or ""):
            out[r["resolved_hardware_id"]] = (r["collected_at"], rec)
    return {k: v[1] for k, v in out.items()}


def _first(rows, f):
    for r in rows:
        v = r.get(f)
        if v not in (None, ""):
            return v
    return None


def _fleet(conn) -> dict:
    """一台一個 info（machine_key → 事實）。只讀；跨請求快取（資料庫沒變不重算）。"""
    return _fleet_cached(conn)


@_fleet_cache.cached
def _fleet_cached(conn) -> dict:
    import oncall
    import system_report as sr
    import v2_hosts

    rules = load_rules()
    groups, of = oncall._groups(conn)
    vc = _vc_recs(conn)
    mrows = {r["key"]: r for r in v2_hosts.machines(conn)}
    out = {}
    for k, g in groups.items():
        if k.startswith("ret:"):
            continue
        g = [dict(r) for r in g]
        recs = [vc[r["id"]] for r in g if r["id"] in vc]
        uuids = {str(u).strip().lower() for u in ([r.get("vm_uuid") for r in g] + [x["uuid"] for x in recs]) if u}
        cia_rows = [r for r in g if not sr.is_off_book(r["asset_serial"])]
        apis = sorted({(r["api_id"] or "").strip() for r in cia_rows if (r["api_id"] or "").strip()})
        m = mrows.get(k) or {}
        badge = m.get("badge") or {}
        out[k] = {
            "key": k, "rows": g, "rep": g[0], "hostname": _first(g, "hostname"), "ip": _first(g, "ip"), "os": _first(g, "os"),
            "uuids": uuids, "has_cia": bool(m.get("has_cia")) if m else bool(cia_rows), "cia_rows": cia_rows, "apis": apis,
            "vm": bool(badge.get("vm")), "vmware": badge.get("code") == "ESX" or badge.get("os_class") == "虛擬化平台",
            "in_nocia": bool(m) and v2_hosts.in_tab(m, "no_cia"), "status_label": m.get("status_label"), "last_seen": m.get("last_seen"),
            "cluster": next((x["cluster"] for x in recs if x.get("cluster")), None),
            "esxi": next((x["esxi"] for x in recs if x.get("esxi")), None),
            "folder": next((x["folder"] for x in recs if x.get("folder")), None),
            "power": next((x["power"] for x in recs if x.get("power")), None),
            "template": any(x["template"] for x in recs),
            "vi": next((x["vi"] for x in recs if x.get("vi")), None) or _first(g, "vi_sdk_server"),
            "prefix": _prefix(_first(g, "hostname"), rules), "subnet": _subnet(_first(g, "ip")),
        }
    return out


def _base_key(info: dict, base: str):
    v = {"name_prefix": info["prefix"], "subnet": info["subnet"], "cluster": info["cluster"], "esxi_host": info["esxi"],
         "vc_folder": info["folder"]}.get(base)
    return str(v).strip().lower() if v else None


BASE_IDS = ("vc_folder", "name_prefix", "subnet", "cluster", "esxi_host")


class Suggester:
    """APID 建議：每個依據一張「鄰居鍵 → APID 計數」，鄰居＝CIA 已登記、有 APID 的機器（同一台算一次）。"""

    def __init__(self, fleet: dict, rules: dict, known_apis: set[str]):
        self.rules = rules
        self.cfg = rules["suggest"]
        self.known = known_apis
        self.enabled = [b["id"] for b in rules["bases"] if b.get("enabled") and b["id"] in BASE_IDS]
        self.labels = {b["id"]: b["label"] for b in rules["bases"]}
        self.idx: dict[str, dict[str, Counter]] = {b: {} for b in self.enabled}
        for info in fleet.values():
            if not info["apis"]:
                continue
            for b in self.enabled:
                k = _base_key(info, b)
                if k:
                    c = self.idx[b].setdefault(k, Counter())
                    for a in info["apis"]:
                        c[a] += 1
        self.code_re = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(a) for a in sorted(known_apis, key=len, reverse=True)) + r")(?![A-Za-z0-9])") \
            if known_apis else None

    def per_base(self, info: dict, holdout: list[str] | None = None) -> dict:
        out = {}
        for b in self.enabled:
            k = _base_key(info, b)
            if not k:
                continue
            if b == "vc_folder" and self.code_re:
                hit = self.code_re.search(str(info["folder"]))
                if hit:
                    out[b] = {"apid": hit.group(1), "n": 1, "total": 1, "share": 1.0, "key": info["folder"], "direct": True}
                    continue
            c = Counter(self.idx[b].get(k, Counter()))
            for a in holdout or []:
                c[a] -= 1
            c = +c
            n = sum(c.values())
            if n < int(self.cfg["min_samples"]):
                continue
            a, cnt = c.most_common(1)[0]
            out[b] = {"apid": a, "n": cnt, "total": n, "share": cnt / n, "key": k, "direct": False}
        return out

    def combine(self, pb: dict, eligible: set[str]) -> dict:
        smin, cshare = float(self.cfg["share_min"]), float(self.cfg["conflict_share"])
        strong = {b: x for b, x in pb.items() if x["share"] >= smin}
        votes = Counter(x["apid"] for x in strong.values())
        agree = [a for a, n in votes.items() if n >= 2]
        cands = []          # (依據, apid)
        for b, x in strong.items():
            cands.append((b, x["apid"]))
        if agree:
            cands.append(("agree2", agree[0]))
        chosen, basis = None, None
        for b, a in cands:
            if b in eligible:
                chosen, basis = a, b
                break
        if chosen is None and cands:
            b, a = max(((b, a) for b, a in cands if b != "agree2"), key=lambda t: strong[t[0]]["share"], default=cands[0])
            chosen, basis = a, b
        conflicts = [b for b, x in pb.items() if chosen and x["apid"] != chosen and x["share"] >= cshare]
        if chosen and basis in eligible and not conflicts:
            conf = "high"
        elif chosen and not conflicts:
            conf = "mid"
        else:
            conf = "low"
        top = Counter()
        for x in pb.values():
            top[x["apid"]] += x["share"]
        return {"apid": chosen if conf != "low" else None, "conf": conf, "basis": basis, "conflicts": conflicts,
                "top": [a for a, _ in top.most_common(3)]}

    def text(self, pb: dict, res: dict) -> str:
        bits = []
        for b, x in pb.items():
            lab = self.labels.get(b, b)
            if x.get("direct"):
                bits.append(f"{lab}「{x['key']}」名稱含 {x['apid']}")
            else:
                bits.append(f"{lab} {x['key']}：CIA {x['total']} 台中 {x['n']} 台 {x['apid']}")
        if res["conflicts"]:
            bits.append("衝突：" + "、".join(f"{self.labels.get(b, b)}指向 {pb[b]['apid']}" for b in res["conflicts"]))
        return "｜".join(bits) or "沒有可用的依據（附近沒有 CIA 已登記的同類機器）"


def backtest(conn, fleet: dict | None = None, rules: dict | None = None, with_samples: bool = False) -> dict:
    """逐台留一回測（GPT 第 3 點）：對象＝CIA 已登記、只有一個 APID 的 VM。每個依據：有預測幾台、對幾台、準確率、涵蓋率；
    準確率 ≥ precision_min 且預測數 ≥ backtest_min_predictions 的依據才「可預填」。另算「最後真的會預填的那一套」的準確率。"""
    rules = rules or load_rules()
    fleet = fleet if fleet is not None else _fleet(conn)
    known = _known_apis(conn)
    sg = Suggester(fleet, rules, known)
    cfg = rules["suggest"]
    ids = list(sg.enabled) + ["agree2"]
    stat = {b: {"predicted": 0, "correct": 0} for b in ids}
    samples = []
    pop = [i for i in fleet.values() if i["vm"] and i["has_cia"] and len(i["apis"]) == 1]
    per = []
    for info in pop:
        truth = info["apis"][0]
        pb = sg.per_base(info, holdout=[truth])
        strong = {b: x for b, x in pb.items() if x["share"] >= float(cfg["share_min"])}
        votes = Counter(x["apid"] for x in strong.values())
        agree = next((a for a, n in votes.items() if n >= 2), None)
        row = {"key": info["key"], "hostname": info["hostname"], "truth": truth, "pred": {}}
        for b, x in strong.items():
            stat[b]["predicted"] += 1
            stat[b]["correct"] += x["apid"] == truth
            row["pred"][b] = x["apid"]
        if agree:
            stat["agree2"]["predicted"] += 1
            stat["agree2"]["correct"] += agree == truth
            row["pred"]["agree2"] = agree
        per.append((info, pb, row))
    pmin, nmin = float(cfg["precision_min"]), int(cfg["backtest_min_predictions"])
    bases = []
    for b in ids:
        s = stat[b]
        prec = s["correct"] / s["predicted"] if s["predicted"] else None
        bases.append({"id": b, "label": sg.labels.get(b, b), "predicted": s["predicted"], "correct": s["correct"],
                      "wrong": s["predicted"] - s["correct"], "precision": prec,
                      "coverage": s["predicted"] / len(pop) if pop else None,
                      "eligible": bool(prec is not None and prec >= pmin and s["predicted"] >= nmin)})
    eligible = {b["id"] for b in bases if b["eligible"]}
    hi_n = hi_ok = 0
    for info, pb, row in per:
        res = sg.combine(pb, eligible)
        row["result"] = res["apid"] if res["conf"] == "high" else None
        if res["conf"] == "high":
            hi_n += 1
            hi_ok += res["apid"] == row["truth"]
        if with_samples:
            samples.append(row)
    out = {"population": len(pop), "bases": bases, "eligible": sorted(eligible), "precision_min": pmin, "min_predictions": nmin,
           "prefill": {"predicted": hi_n, "correct": hi_ok, "precision": hi_ok / hi_n if hi_n else None}}
    if with_samples:
        out["samples"] = samples
    return out


def backtest_samples(conn, basis: str | None = None, outcome: str | None = None) -> list[dict]:
    """回測下鑽：某個依據（或 prefill＝最後會預填的）× 結果（correct／wrong／none＝沒預測）。"""
    bt = backtest(conn, with_samples=True)
    out = []
    for i, s in enumerate(bt["samples"], 1):
        pred = s["result"] if basis == "prefill" else (s["pred"].get(basis) if basis else None)
        oc = "none" if pred is None else ("correct" if pred == s["truth"] else "wrong")
        if outcome and oc != outcome:
            continue
        if basis and outcome is None and pred is None:
            continue
        out.append({"no": i, "hostname": s["hostname"], "truth": s["truth"], "pred": pred, "outcome": oc,
                    "all_pred": "、".join(f"{k}:{v}" for k, v in s["pred"].items())})
    return out


def _known_apis(conn) -> set[str]:
    try:
        return {r[0] for r in conn.execute("SELECT api_id FROM business_system WHERE api_id IS NOT NULL AND api_id <> ''")}
    except sqlite3.OperationalError:   # 還沒匯過業務系統對照表：沒有已知 APID（交回檢查改成只要求非空白）
        return set()


def _sysnames(conn) -> dict:
    try:
        return {r[0]: r[1] or "" for r in conn.execute("SELECT api_id, name FROM business_system")}
    except sqlite3.OperationalError:   # 同上：沒有對照表就只顯示代號
        return {}


# ---------------------------------------------------------------- 30 欄

def columns(rules: dict | None = None) -> list[tuple[str, str]]:
    """CIA 硬體頁的欄（欄名, 欄位代號）：匯入用同一份 field_mapping 的 29 欄＋設備廠牌（插在設備機型後）。"""
    import excel_import

    rules = rules or load_rules()
    out = []
    for head, col in excel_import.load_mapping().get("硬體", {}).items():
        out.append((head, col))
        if col == rules["vendor_after"]:
            out.append((rules["vendor_header"], "device_vendor"))
    if not any(c == "device_vendor" for _, c in out):
        out.append((rules["vendor_header"], "device_vendor"))
    return out


def _mode(values, min_share: float):
    vals = [str(v).strip() for v in values if v is not None and str(v).strip() != ""]
    if not vals:
        return None, 0, 0
    c = Counter(vals)
    v, n = c.most_common(1)[0]
    return (v if n / len(vals) >= min_share else None), n, len(vals)


def derived_table(fleet: dict, rules: dict) -> dict:
    """APID → 各帶出欄的 (值, 命中, 樣本)。樣本＝CIA 現役列（同一台多筆各算一筆，跟 CIA 硬體頁的筆數一致）。"""
    by: dict[str, list] = {}
    for info in fleet.values():
        for r in info["cia_rows"]:
            a = (r["api_id"] or "").strip()
            if a:
                by.setdefault(a, []).append(r)
    out = {}
    ms = float(rules["derived_min_share"])
    for a, rows in by.items():
        out[a] = {f: _mode([r.get(f) for r in rows], ms) for f in rules["derived_fields"]}
    return out


def _cia_vocab(fleet: dict, col: str, min_count: int, vm_only: bool = True) -> list[str]:
    c = Counter()
    for info in fleet.values():
        if vm_only and not info["vm"]:
            continue
        for r in info["cia_rows"]:
            v = str(r.get(col) or "").strip()
            if v:
                c[str(v)] += 1
    return [v for v, n in c.most_common() if n >= min_count]


def options(conn, fleet: dict, rules: dict) -> dict:
    """下拉選項（WEBIT3 產生）：固定清單在規則檔；作業系統、機房、使用單位取 CIA 現有寫法（VM、出現 ≥ N 次）；
    機房若 9.3 已設標準名稱，就用標準名稱。APID＝業務系統對照表。"""
    o = {k: list(v) for k, v in rules["options"].items()}
    n = int(rules["os_option_min_count"])
    o["os"] = _cia_vocab(fleet, "os", n)
    stds = sorted(set((cia_fix._setting(conn, "cia_fix_room_alias", {}) or {}).values()))
    o["physical_location"] = stds or _cia_vocab(fleet, "physical_location", n, vm_only=False)
    o["usage_unit"] = _cia_vocab(fleet, "usage_unit", n, vm_only=False)
    for k in ("integrity", "confidentiality", "availability"):
        o[k] = sorted(set(o.get(k, [])) | set(_cia_vocab(fleet, k, n, vm_only=False)))
    o["api_id"] = sorted(_known_apis(conn))
    other = rules["other_option"]
    for k in ("os", "physical_location", "usage_unit", "environment", "asset_status", "owning_company", "device_vendor"):
        if other not in o.setdefault(k, []):
            o[k].append(other)
    return o


def _room_suggest(info: dict, fleet: dict, rules: dict, cache: dict):
    """機房建議（GPT 第 4 點：只當建議）：同 cluster 的 CIA VM 機房眾數 ≥ share_min；不行再看同一座 vCenter。"""
    smin = float(rules["suggest"]["share_min"])
    for what, key in (("cluster", info["cluster"]), ("vi", info["vi"])):
        if not key:
            continue
        ck = (what, key)
        if ck not in cache:
            vals = [r["physical_location"] for i in fleet.values() if i["vm"] and i[what] == key for r in i["cia_rows"]]
            cache[ck] = _mode(vals, smin)
        v, n, t = cache[ck]
        if v and t >= int(rules["suggest"]["min_samples"]):
            return v, f"同{'cluster' if what == 'cluster' else ' vCenter'} {key} 的 CIA VM {t} 筆中 {n} 筆"
    return None, None


def _observed(info: dict, rules: dict) -> str:
    bits = []
    if info["power"]:
        bits.append(f"RVTools 電源：{info['power']}")
    if info["last_seen"]:
        bits.append(f"最後看到 {str(info['last_seen'])[:10]}")
    if info["status_label"]:
        bits.append(f"偵測：{info['status_label']}")
    return "；".join(bits) or "無觀測資料"


def fill(info: dict, sug: dict, dtab: dict, opts: dict, rules: dict, fleet: dict, room_cache: dict, data_date: str | None) -> dict:
    """一台的 30 欄：{欄位代號: {v, kind, src}}。kind：fixed／system／derived／suggest／blank。
    資產狀態一律不預填（GPT 第 5 點）；APID 只有高信心才預填；帶出欄附來源（樣本數＋CIA 資料日）。"""
    out = {}
    for col, v in rules["fixed_vm"].items():
        out[col] = {"v": v, "kind": "fixed", "src": "VM 固定值（規則檔）"}
    for col in rules["fixed_from_cia_vm_mode"]:
        if col not in room_cache.setdefault("_vm_mode", {}):
            room_cache["_vm_mode"][col] = _mode([r.get(col) for i in fleet.values() if i["vm"] for r in i["cia_rows"]], 0.5)
        v, n, t = room_cache["_vm_mode"][col]
        out[col] = {"v": v or "", "kind": "fixed" if v else "blank", "src": f"CIA 現有 VM {t} 筆中 {n} 筆" if v else "CIA 現有 VM 沒有一致寫法"}
    out["asset_serial"] = {"v": "", "kind": "blank", "src": "CIA 給號"}
    out["hostname"] = {"v": info["hostname"] or "", "kind": "system", "src": "WEBIT3 主機名"}
    out["ip"] = {"v": info["ip"] or "", "kind": "system", "src": "WEBIT3 IP"}
    os_v = None
    seen_os = (info["os"] or "").strip()
    for o in opts.get("os", []):
        if seen_os and cia_fix._nk(o) == cia_fix._nk(seen_os):
            os_v = o
    out["os"] = {"v": os_v or "", "kind": "system" if os_v else "blank",
                 "src": "觀測值對到 CIA 現有寫法" if os_v else "觀測值對不到 CIA 現有寫法，請選"}
    out["asset_status"] = {"v": "", "kind": "blank", "src": "不預填（請依實際狀態選）"}
    room, rsrc = _room_suggest(info, fleet, rules, room_cache)
    out["physical_location"] = {"v": room or "", "kind": "suggest" if room else "blank",
                                "src": (f"建議：{rsrc}（CIA 資料日 {data_date or '不明'}）") if room else "沒有足夠的同 cluster／vCenter 樣本"}
    a = sug["apid"] if sug["conf"] == "high" else (UNDETERMINED if sug["conf"] == "low" else "")
    out["api_id"] = {"v": a, "kind": "suggest" if sug["conf"] == "high" else "blank", "src": sug.get("basis_text") or ""}
    d = dtab.get(sug["apid"] or "", {}) if sug["conf"] == "high" else {}
    for f in rules["derived_fields"]:
        v, n, t = d.get(f, (None, 0, 0))
        out[f] = {"v": v or "", "kind": "derived" if v else "blank",
                  "src": f"同 APID 的 CIA {t} 筆中 {n} 筆（CIA 資料日 {data_date or '不明'}）" if t else "選 APID 後帶出"}
    out["environment"] = {"v": "", "kind": "blank", "src": "請選"}
    out["remark"] = {"v": "", "kind": "blank", "src": "選填"}
    return out


# ---------------------------------------------------------------- 分類、綁組、母體

def _db_signals(conn) -> tuple[dict, dict]:
    ports: dict[str, set] = {}
    try:
        for r in conn.execute("SELECT asset_serial, port FROM host_service WHERE gone_at IS NULL"):
            ports.setdefault(r[0], set()).add(int(r[1] or 0))
    except (sqlite3.OperationalError, ValueError):
        pass
    scan: dict[str, set] = {}
    try:
        for r in conn.execute("SELECT ip, open_ports FROM scan_history WHERE open_ports IS NOT NULL AND open_ports <> ''"):
            for p in str(r[1]).split(","):
                if p.strip().isdigit():
                    scan.setdefault(r[0], set()).add(int(p))
    except sqlite3.OperationalError:
        pass
    return ports, scan


def category_of(info: dict, rules: dict, sig: tuple[dict, dict]) -> tuple[str, str]:
    """依序判斷，先符合先分走：DB → AS400 → AIX → NETWORK → WIN → LINUX → 無法判斷。回 (類別, 為什麼)。"""
    import manage_state
    import os_class

    dbp = set(rules["db_ports"])
    ports = set()
    for r in info["rows"]:
        ports |= sig[0].get(r["asset_serial"], set())
    for ip in manage_state.ips_of(info["ip"]):
        ports |= sig[1].get(ip, set())
    hit = sorted(ports & dbp)
    if hit:
        return "DB", f"資料庫埠 {','.join(map(str, hit))}"
    if re.search(rules["db_name_regex"], _short(info["hostname"])):
        return "DB", "主機名像資料庫"
    oc = os_class.classify_row(info["rep"])
    cat = rules["category_of_os_class"].get(oc)
    return (cat, f"作業系統類別 {oc}") if cat else ("無法判斷", f"作業系統類別 {oc or '不明'}")


def _group_key(info: dict, sug: dict) -> str:
    if sug["conf"] in ("high", "mid") and sug.get("apid"):
        return f"APID {sug['apid']}"
    if info["prefix"]:
        return f"前綴 {info['prefix']}"
    if info["subnet"]:
        return f"網段 {info['subnet']}.0/24"
    return f"單台 {info['key']}"


def _open_keys(conn) -> set:
    if not _ready(conn):
        return set()
    return {r[0] for r in conn.execute(f"SELECT machine_key FROM cia_reg_item WHERE status IN ({','.join('?' * len(OPEN))})", OPEN)}


def plan(conn) -> dict:
    """母體與這輪會分派的（不寫庫）。"""
    import existence

    rules = load_rules()
    st = get_settings(conn)
    fleet = _fleet(conn)
    bt = backtest(conn, fleet, rules)
    eligible = set(bt["eligible"])
    sg = Suggester(fleet, rules, _known_apis(conn))
    names = _sysnames(conn)
    dtab = derived_table(fleet, rules)
    opts = options(conn, fleet, rules)
    data_date = cia_fix._last_cia_import(conn)["data_date"]
    pending_dy = {s for m in existence.compute(conn)["machines"] if m.get("pending_confirm") for s in m["serials"] if s}
    sig = _db_signals(conn)
    in_open = _open_keys(conn)
    # UUID → 掛在哪幾台
    uuid_keys: dict[str, set] = {}
    for info in fleet.values():
        for u in info["uuids"]:
            uuid_keys.setdefault(u, set()).add(info["key"])
    nocia = [i for i in fleet.values() if i["in_nocia"]]
    excluded, cands, room_cache = [], [], {}
    nonvm = 0
    for info in sorted(nocia, key=lambda x: ((x["hostname"] or "").lower(), x["key"])):
        if not info["vm"]:
            nonvm += 1
            continue
        why = None
        if info["vmware"]:
            why = "VMware 設備本身（天條：只從 vCenter／vROps 取資料）"
        elif info["template"]:
            why = "RVTools 範本（Template）"
        elif any(r["asset_serial"] in pending_dy for r in info["rows"]):
            why = "只有 DY 看得到（待確認是否真的存在）"
        elif info["key"] in in_open:
            why = "已在進行中的補登裡"
        if why:
            excluded.append({"key": info["key"], "hostname": info["hostname"], "ip": info["ip"], "reason": why})
            continue
        pb = sg.per_base(info)
        sug = sg.combine(pb, eligible)
        sug["basis_text"] = sg.text(pb, sug)
        cat, cat_why = category_of(info, rules, sig)
        review = None
        if len(info["uuids"]) > 1:
            review = "同一台有兩個以上 VM UUID"
        elif any(len(uuid_keys.get(u, ())) > 1 for u in info["uuids"]):
            review = "VM UUID 同時掛在兩台機器上"
        cands.append({
            "key": info["key"], "hostname": info["hostname"], "ip": info["ip"], "uuid": sorted(info["uuids"])[0] if info["uuids"] else None,
            "category": cat, "category_why": cat_why, "review": review,
            "apid": sug["apid"], "apid_name": names.get(sug["apid"] or "", ""), "conf": sug["conf"], "conf_label": CONF_LABEL[sug["conf"]],
            "basis_text": sug["basis_text"], "basis": sug["basis"], "top": sug["top"], "per_base": pb,
            "group_key": _group_key(info, sug), "observed": _observed(info, rules), "observed_os": info["os"] or "",
            "fill": fill(info, {**sug}, dtab, opts, rules, fleet, room_cache, data_date),
        })
    pilot = set(st["pilot"])
    in_round = [c for c in cands if c["category"] in pilot]
    _assign(in_round, st["assignees"], _open_load(conn))
    fp = hashlib.sha256(json.dumps([(c["key"], c["category"], c.get("assignee"), c["apid"], c["conf"], bool(c["review"])) for c in in_round]
                                   + [st, sorted(eligible)], ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return {"rules": rules, "settings": st, "fleet_n": len(fleet), "nocia": len(nocia), "nonvm": nonvm, "vm": len(nocia) - nonvm,
            "excluded": excluded, "cands": cands, "in_round": in_round, "not_pilot": [c for c in cands if c["category"] not in pilot],
            "backtest": bt, "fingerprint": fp, "options": opts, "derived": dtab, "data_date": data_date}


def _open_load(conn) -> Counter:
    """每人手上還沒交完的台數（已分派＋退回），分派新一輪時算進上限。"""
    if not _ready(conn):
        return Counter()
    return Counter(r[0] for r in conn.execute("SELECT assignee FROM cia_reg_item WHERE status IN ('assigned','bounced') AND assignee IS NOT NULL"))


def _assign(cands: list[dict], assignees: dict, existing: Counter | None = None) -> None:
    """同組不拆；各類的組依台數由大到小，給「加上去不超過上限」裡目前台數最少的人（LPT）。
    暫停的人不分；放不下的組留在待分派並寫原因（不拆組、不硬塞）。待確認（身分）的不分派。"""
    existing = existing or Counter()
    load: Counter = Counter(existing)
    by_cat: dict[str, dict[str, list]] = {}
    for c in cands:
        c["assignee"] = None
        c["unassigned_why"] = None
        if c["review"]:
            continue
        by_cat.setdefault(c["category"], {}).setdefault(c["group_key"], []).append(c)
    for cat, groups in by_cat.items():
        everyone = assignees.get(cat, [])
        people = [p for p in everyone if not p.get("paused")]
        cap = {p["key"]: p.get("cap") for p in people}
        order = [p["key"] for p in people]
        for gk in sorted(groups, key=lambda g: (-len(groups[g]), g)):
            n = len(groups[gk])
            fit = [k for k in order if cap[k] is None or load[k] + n <= cap[k]]
            if not fit:
                why = "這一類沒有負責人" if not everyone else ("負責人都暫停" if not people else f"同組 {n} 台，放不進任何人的上限（組不拆）")
                for c in groups[gk]:
                    c["unassigned_why"] = why
                continue
            p = min(fit, key=lambda x: (load[x], order.index(x)))
            for c in groups[gk]:
                c["assignee"] = p
            load[p] += n


def _slim_cand(c: dict) -> dict:
    return {k: c[k] for k in ("key", "hostname", "ip", "category", "category_why", "review", "apid", "apid_name", "conf", "conf_label",
                              "basis_text", "group_key", "observed", "assignee", "unassigned_why") if k in c}


def preview(conn) -> dict:
    p = plan(conn)
    names = people_names(conn)
    st = p["settings"]
    existing = _open_load(conn)
    per = Counter((c["category"], c.get("assignee") or "") for c in p["in_round"] if not c["review"])
    load = Counter(c.get("assignee") for c in p["in_round"] if c.get("assignee"))
    pmeta = {}
    for cat, lst in st["assignees"].items():
        for q in lst:
            pmeta.setdefault(q["key"], q)
    spread = []
    for k in sorted(set(load) | set(pmeta)):
        m = pmeta.get(k, {})
        new, old = load.get(k, 0), existing.get(k, 0)
        cap = m.get("cap")
        spread.append({"key": k, "name": names.get(k, k), "new": new, "existing": old, "total": new + old, "cap": cap,
                       "paused": bool(m.get("paused")), "warn": m.get("warn"),
                       "over": cap is not None and new + old > cap})
    warns = [f"{x['name']}：{x['warn']}" for x in spread if x["warn"] and x["new"]]
    unassigned = Counter(c["unassigned_why"] for c in p["in_round"] if not c["review"] and not c.get("assignee"))
    warns += [f"{n} 台沒分出去：{w}" for w, n in unassigned.items() if w]
    conf = Counter(c["conf"] for c in p["in_round"])
    no_owner = sorted({c["category"] for c in p["in_round"] if not c["review"] and not c.get("assignee")})
    rows = [{**_slim_cand(c), "assignee_name": names.get(c.get("assignee") or "", c.get("assignee"))} for c in p["in_round"]]
    return {"nocia": p["nocia"], "vm": p["vm"], "nonvm": p["nonvm"], "excluded": p["excluded"],
            "excluded_by_reason": dict(Counter(x["reason"] for x in p["excluded"])),
            "assignable": len(p["cands"]), "in_round": len(p["in_round"]), "not_pilot": len(p["not_pilot"]),
            "review": sum(1 for c in p["in_round"] if c["review"]),
            "by_category": dict(Counter(c["category"] for c in p["cands"])), "pilot": st["pilot"],
            "per_person": [{"category": k[0], "assignee": k[1], "assignee_name": names.get(k[1], k[1]) if k[1] else "（沒分出去）", "count": n}
                           for k, n in sorted(per.items())],
            "spread": spread, "warnings": warns,
            "conf": {CONF_LABEL[k]: conf.get(k, 0) for k in CONF_LABEL}, "no_owner_categories": no_owner,
            "rows": rows, "not_pilot_rows": [_slim_cand(c) for c in p["not_pilot"]],
            "backtest": p["backtest"], "fingerprint": p["fingerprint"], "cia_import": cia_fix._last_cia_import(conn)}


def _check_date(d: str | None) -> str | None:
    if d in (None, ""):
        return None
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError("截止日格式要是 YYYY-MM-DD")


def create_round(conn, by: str, expect: str, deadline: str | None = None) -> dict:
    """在 BEGIN IMMEDIATE 裡重算、比指紋（GPT 第 11 點）；部分唯一索引兜底同一台不會同時在兩個未結案項目。"""
    _begin(conn)
    try:
        p = plan(conn)
        if p["fingerprint"] != expect:
            raise Stale("預覽之後資料或設定變了（有人匯入、改了負責人、或別人先分派了），請重新預覽")
        if not p["in_round"]:
            raise ValueError("這次沒有可分派的機器（試辦類別裡沒有）")
        now = _now()
        st = p["settings"]
        deadline = _check_date(deadline)
        rid = conn.execute(
            "INSERT INTO cia_reg_round (created_at, created_by, categories_json, counts_json, backtest_json, rules_sha256, settings_sha256, "
            "snapshot_json, cia_data_date) VALUES (?,?,?,?,?,?,?,?,?)",
            (now, by, json.dumps(st["pilot"], ensure_ascii=False),
             json.dumps({"nocia": p["nocia"], "vm": p["vm"], "excluded": len(p["excluded"]), "assignable": len(p["cands"]),
                         "in_round": len(p["in_round"])}, ensure_ascii=False),
             json.dumps({k: v for k, v in p["backtest"].items() if k != "samples"}, ensure_ascii=False),
             hashlib.sha256(RULES_PATH.read_bytes()).hexdigest(),
             hashlib.sha256(json.dumps(st, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
             cia_fix._snapshot(p["rules"], {"pilot": st["pilot"], "categories_with_people": sorted(c for c, v in st["assignees"].items() if v)}),
             p["data_date"])).lastrowid
        conn.execute("UPDATE cia_reg_round SET deadline = ? WHERE id = ?", (deadline, rid))
        for n, c in enumerate(p["in_round"], 1):
            status = "review" if c["review"] else ("assigned" if c.get("assignee") else "unassigned")
            iid = conn.execute(
                "INSERT INTO cia_reg_item (round_id, no, machine_key, key_hash, vm_uuid, hostname, ip, category, group_key, assignee, "
                "assign_ver, status, apid_suggest, apid_conf, basis_text, basis_json, fill_json, observed, observed_os, created_at, "
                "updated_at, note) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, n, c["key"], _key_hash(c["key"]), c["uuid"], c["hostname"], c["ip"], c["category"], c["group_key"],
                 c.get("assignee") if status == "assigned" else None, status, c["apid"], c["conf"], c["basis_text"],
                 json.dumps({"per_base": c["per_base"], "top": c["top"], "basis": c["basis"]}, ensure_ascii=False),
                 json.dumps(c["fill"], ensure_ascii=False), c["observed"], c["observed_os"], now, now,
                 c["review"] or c.get("unassigned_why"))).lastrowid
            conn.execute("UPDATE cia_reg_item SET track_code = ? WHERE id = ?", (f"CR{rid}-{iid}", iid))
            _event(conn, iid, rid, by, "create", None, status, assignee=c.get("assignee"), ver=1, now=now,
                   note=c["review"] or c.get("unassigned_why"))
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return {"round_id": rid, "items": len(p["in_round"])}


# ---------------------------------------------------------------- 查詢、進度

METRICS = {
    "total": "分到", "returned": "交回", "passed": "通過", "bounced": "退回", "remaining": "還差",
}


def _metric(r, m: str) -> bool:
    """進度矩陣與下鑽共用的同一個判斷（加總＝下鑽）。"""
    s = r["status"]
    return {"total": True, "due": s not in ("excluded", "gone"), "returned": r["returned_at"] is not None,
            "passed": s in ("passed", "merged", "registered"), "bounced": s == "bounced",
            "remaining": s not in ("passed", "merged", "registered", "excluded", "gone")}[m]


def _completion(rows) -> float | None:
    """完成率＝通過（含已彙整、已登記）÷（全部 − 撤出 − 資產已消失）。"""
    base = [r for r in rows if r["status"] not in ("excluded", "gone")]
    return (sum(1 for r in base if _metric(r, "passed")) / len(base)) if base else None


def _item_dict(r, names: dict | None = None) -> dict:
    d = dict(r)
    d["status_label"] = STATUS.get(r["status"], r["status"])
    d["conf_label"] = CONF_LABEL.get(r["apid_conf"] or "", "")
    for k in ("fill_json", "filled_json", "check_json", "basis_json"):
        d.pop(k, None)
    d["check"] = json.loads(r["check_json"]) if r["check_json"] else []
    d["assignee_name"] = (names or {}).get(r["assignee"] or "", r["assignee"])
    return d


def list_items(conn, status: str | None = None, category: str | None = None, assignee: str | None = None,
               metric: str | None = None, round_id: int | None = None, batch_id: int | None = None, conf: str | None = None) -> list[dict]:
    if not _ready(conn):
        return []
    if status and status not in STATUS and status not in ("open",):
        raise ValueError(f"不認得的狀態：{status}")
    if metric and metric not in METRICS and metric not in ("resend", "due"):
        raise ValueError(f"不認得的指標：{metric}")
    resend = _resend_ids(conn) if metric == "resend" else set()
    names = people_names(conn)
    out = []
    for r in conn.execute("SELECT * FROM cia_reg_item ORDER BY round_id, no, id"):
        if status == "open" and r["status"] not in OPEN or status and status != "open" and r["status"] != status:
            continue
        if category and r["category"] != category:
            continue
        if assignee is not None and (r["assignee"] or "") != assignee:
            continue
        if metric == "resend":
            if r["id"] not in resend:
                continue
        elif metric and not _metric(r, metric):
            continue
        if round_id and r["round_id"] != round_id or batch_id and r["reg_batch_id"] != batch_id:
            continue
        if conf and r["apid_conf"] != conf:
            continue
        out.append(_item_dict(r, names))
    return out


def counts(conn, round_id: int | None = None) -> dict:
    out = {k: 0 for k in STATUS}
    out.update({"total": 0, "rounds": 0, "batches": 0})
    if not _ready(conn):
        return out
    for st, n in conn.execute("SELECT status, COUNT(*) FROM cia_reg_item WHERE (? IS NULL OR round_id = ?) GROUP BY status",
                              (round_id, round_id)):
        out[st] = n
        out["total"] += n
    out["rounds"] = conn.execute("SELECT COUNT(*) FROM cia_reg_round").fetchone()[0]
    out["batches"] = conn.execute("SELECT COUNT(*) FROM cia_reg_batch").fetchone()[0]
    return out


def progress(conn, round_id: int | None = None) -> list[dict]:
    """類別 × 負責人（沒分派的寫「（未分派）」）：每個指標的台數；每格下鑽＝list_items(category, assignee, metric, round_id)。"""
    if not _ready(conn):
        return []
    names = people_names(conn)
    cells: dict[tuple, dict] = {}
    for r in conn.execute("SELECT * FROM cia_reg_item WHERE (? IS NULL OR round_id = ?)", (round_id, round_id)):
        k = (r["category"], r["assignee"] or "")
        c = cells.setdefault(k, {m: 0 for m in METRICS})
        for m in METRICS:
            c[m] += _metric(r, m)
    cats = load_rules()["categories"]
    return [{"category": k[0], "assignee": k[1], "assignee_label": names.get(k[1], k[1]) if k[1] else "（未分派）", **v}
            for k, v in sorted(cells.items(), key=lambda kv: (cats.index(kv[0][0]) if kv[0][0] in cats else 99, kv[0][1]))]


def item_events(conn, item_id: int) -> list[dict]:
    if not _ready(conn):
        return []
    return [{**dict(r), "action_label": ACTION_LABEL.get(r["action"], r["action"]), "to_label": STATUS.get(r["to_status"], r["to_status"])}
            for r in conn.execute("SELECT * FROM cia_reg_event WHERE item_id = ? ORDER BY id", (item_id,))]


def rounds(conn) -> list[dict]:
    """輪次選單：建立時間、設定快照（試辦類別、哪幾類有人）、截止日、完成率。"""
    if not _ready(conn):
        return []
    out = []
    for r in conn.execute("SELECT * FROM cia_reg_round ORDER BY id DESC"):
        rows = conn.execute("SELECT status, returned_at FROM cia_reg_item WHERE round_id = ?", (r["id"],)).fetchall()
        snap = {}
        try:
            snap = json.loads(r["snapshot_json"] or "{}").get("settings", {})
        except ValueError:   # 快照壞掉不影響輪次清單，只是看不到快照
            snap = {}
        out.append({"id": r["id"], "created_at": r["created_at"], "created_by": r["created_by"],
                    "categories": json.loads(r["categories_json"] or "[]"), "counts": json.loads(r["counts_json"] or "{}"),
                    "items": len(rows), "cia_data_date": r["cia_data_date"], "deadline": r["deadline"],
                    "completion": _completion(rows), "snapshot": snap})
    return out


def set_deadline(conn, round_id: int, deadline: str | None, by: str) -> dict:
    d = _check_date(deadline)
    _begin(conn)
    try:
        if not conn.execute("SELECT 1 FROM cia_reg_round WHERE id = ?", (round_id,)).fetchone():
            raise LookupError(f"找不到第 {round_id} 輪")
        conn.execute("UPDATE cia_reg_round SET deadline = ? WHERE id = ?", (d, round_id))
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return {"round_id": round_id, "deadline": d}


def batches(conn) -> list[dict]:
    if not _ready(conn):
        return []
    out = []
    for b in conn.execute("SELECT * FROM cia_reg_batch ORDER BY id DESC"):
        by: dict[int, list] = {}
        for r in conn.execute("SELECT pkg_no, filled_json FROM cia_reg_item WHERE reg_batch_id = ? ORDER BY pkg_no", (b["id"],)):
            v = json.loads(r["filled_json"] or "{}")
            by.setdefault(r["pkg_no"], []).append((v.get("inventory_department") or v.get("inventory_division") or "").strip() or "（未填單位）")
        pk = [{"pkg_no": k, "unit": us[0], "rows": len(us)} for k, us in sorted(by.items())]
        reg = conn.execute("SELECT COUNT(*) FROM cia_reg_item WHERE reg_batch_id = ? AND status = 'registered'", (b["id"],)).fetchone()[0]
        out.append({**dict(b), "packages": pk, "registered": reg})
    return out


# ---------------------------------------------------------------- 改派、撤出、不登記確認

def _items(conn, ids):
    ids = [int(i) for i in dict.fromkeys(ids or [])]
    if not ids:
        raise ValueError("沒有選任何一台")
    if len(ids) > 2000:
        raise ValueError("一次最多 2000 台")
    rows = conn.execute(f"SELECT * FROM cia_reg_item WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    if len(rows) != len(ids):
        raise LookupError("有幾台找不到")
    return rows


def reassign(conn, ids: list[int], assignee: str, note: str | None, by: str) -> dict:
    """改派（GPT 第 9 點）：分派版本 +1，舊版 Excel 交回時這些列拒收。已通過以後的不能改派。"""
    assignee = (assignee or "").strip()
    if not assignee:
        raise ValueError("要選改派給誰")
    if not _ready(conn):
        raise LookupError("還沒有任何補登")
    warn = reassign_warning(conn, assignee, len(ids))
    _begin(conn)
    try:
        now, done, skipped = _now(), 0, []
        for r in _items(conn, ids):
            if r["status"] not in REASSIGNABLE:
                skipped.append({"id": r["id"], "track_code": r["track_code"], "why": f"目前是{STATUS[r['status']]}，不能改派"})
                continue
            ver = r["assign_ver"] + 1
            conn.execute("UPDATE cia_reg_item SET assignee = ?, assign_ver = ?, status = 'assigned', updated_at = ? WHERE id = ?",
                         (assignee, ver, now, r["id"]))
            _event(conn, r["id"], r["round_id"], by, "reassign" if r["assignee"] else "assign", r["status"], "assigned",
                   assignee=assignee, ver=ver, now=now, note="；".join(x for x in (note, f"警示：{warn}" if warn else None) if x) or None)
            done += 1
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return {"done": done, "skipped": skipped, "warning": warn}


def reassign_warning(conn, assignee: str, n: int) -> str | None:
    """改派對象是暫停中、或加上這 n 台會超過上限 → 回一句警示（不擋；人工改派以人的判斷為準）。"""
    people = {q["key"]: q for lst in get_settings(conn)["assignees"].values() for q in lst}
    p = people.get(assignee)
    if not p:
        return None
    bits = []
    if p.get("paused"):
        bits.append(f"{p['name']} 目前暫停分派")
    load = _open_load(conn).get(assignee, 0)
    if p.get("cap") is not None and load + n > p["cap"]:
        bits.append(f"{p['name']} 手上 {load} 台＋這次 {n} 台，超過上限 {p['cap']}")
    return "；".join(bits) or None


def withdraw(conn, ids: list[int], note: str | None, by: str) -> dict:
    if not (note or "").strip():
        raise ValueError("撤出要寫原因")
    return _close(conn, ids, "excluded", "withdraw", WITHDRAWABLE, note, by)


def claim_decide(conn, ids: list[int], accept: bool, note: str | None, by: str) -> dict:
    """負責人說「不登記」：SP 確認（→ 撤出）或不接受（→ 退回重填，同一版本）。"""
    if accept:
        return _close(conn, ids, "excluded", "claim_accept", ("claim",), note, by)
    return _close(conn, ids, "bounced", "claim_reject", ("claim",), note, by)


def _close(conn, ids, to, action, allowed, note, by) -> dict:
    if not _ready(conn):
        raise LookupError("還沒有任何補登")
    _begin(conn)
    try:
        now, done, skipped = _now(), 0, []
        for r in _items(conn, ids):
            if r["status"] not in allowed:
                skipped.append({"id": r["id"], "track_code": r["track_code"], "why": f"目前是{STATUS[r['status']]}，不能這樣處理"})
                continue
            conn.execute("UPDATE cia_reg_item SET status = ?, updated_at = ?, closed_at = ? WHERE id = ?",
                         (to, now, now if to in CLOSED else None, r["id"]))
            _event(conn, r["id"], r["round_id"], by, action, r["status"], to, assignee=r["assignee"], ver=r["assign_ver"], now=now, note=note)
            done += 1
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return {"done": done, "skipped": skipped}


# ---------------------------------------------------------------- 每人一個 Excel

def _person_items(conn, assignee: str, bounced_only: bool = False) -> list:
    sts = ("bounced",) if bounced_only else ("assigned", "bounced")
    return conn.execute(f"SELECT * FROM cia_reg_item WHERE assignee = ? AND status IN ({','.join('?' * len(sts))}) "
                        "ORDER BY group_key, round_id, no, id", (assignee, *sts)).fetchall()


def parts(items: list, cap: int) -> list[list]:
    """每人檔上限 cap 台；同一組（group_key）不拆到兩個檔（一組本身超過上限就自己一個檔）。"""
    out, cur, by = [], [], {}
    for r in items:
        by.setdefault(r["group_key"], []).append(r)
    for gk in by:
        g = by[gk]
        if cur and len(cur) + len(g) > cap:
            out.append(cur)
            cur = []
        cur = cur + g
    if cur:
        out.append(cur)
    return out


def person_files(conn) -> list[dict]:
    """每個負責人目前有幾台待填、切成幾個檔、幾台退回。"""
    if not _ready(conn):
        return []
    cap = int(load_rules()["per_person_cap"])
    names = people_names(conn)
    out = []
    for (a,) in conn.execute("SELECT DISTINCT assignee FROM cia_reg_item WHERE assignee IS NOT NULL AND status IN ('assigned','bounced') ORDER BY assignee"):
        items = _person_items(conn, a)
        out.append({"assignee": a, "assignee_name": names.get(a, a), "items": len(items), "parts": len(parts(items, cap)),
                    "bounced": sum(1 for r in items if r["status"] == "bounced")})
    return out


def person_xlsx(conn, assignee: str, part: int = 1, bounced_only: bool = False, by: str | None = None,
                via: str = "單檔") -> tuple[io.BytesIO, int, str]:
    buf, items, fname, nparts = _person_xlsx(conn, assignee, part, bounced_only)
    data = buf.getvalue()
    _record_file(conn, assignee, part, nparts, bounced_only, fname, data, items, by, via)
    return io.BytesIO(data), len(items), fname


def _person_xlsx(conn, assignee: str, part: int = 1, bounced_only: bool = False):
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Font, PatternFill, Protection
    from openpyxl.utils import get_column_letter as L
    from openpyxl.worksheet.datavalidation import DataValidation

    if not _ready(conn):
        raise LookupError("還沒有任何補登")
    rules = load_rules()
    allp = parts(_person_items(conn, assignee, bounced_only), int(rules["per_person_cap"]))
    if not allp:
        raise LookupError(f"{assignee} 目前沒有待填的機器")
    if part < 1 or part > len(allp):
        raise LookupError(f"{assignee} 只有 {len(allp)} 個檔")
    items = allp[part - 1]
    fleet_opts = _options_snapshot(conn)
    cols = columns(rules)
    heads = ["項次"] + [h for h, _ in cols] + EXTRA_COLS
    ci = {c: i + 2 for i, (_, c) in enumerate(cols)}          # 欄位代號 → Excel 欄號（第 1 欄是項次）
    xi = {h: len(cols) + 2 + i for i, h in enumerate(EXTRA_COLS)}
    bold, yellow, grey = Font(bold=True), PatternFill("solid", fgColor="FFF4C2"), PatternFill("solid", fgColor="EEEEEE")
    unlocked = Protection(locked=False)
    wb = Workbook()

    ws = wb.active
    ws.title = SHEET_INFO
    pname = people_names(conn).get(assignee, assignee)
    lines = [("CIA 補登", f"負責人：{pname}" + ("（退回重填）" if bounced_only else "") + f"・第 {part}/{len(allp)} 個檔・{len(items)} 台"),
             ("負責人", pname), ("負責人代號", assignee), ("檔案", f"{part}/{len(allp)}"), ("產生時間", _now()),
             ("CIA 資料日", cia_fix._last_cia_import(conn)["data_date"] or "（還沒有 CIA 匯入紀錄）"),
             ("怎麼填", "到「2 補登填寫」逐列：確認或選 APID → 選「APID 確認」（是／改了／交統籌）→ 補白色空格（環境別、資產狀態等）。"),
             ("", "黃底＝系統建議（APID、機房），請確認；灰底＝固定值或系統值，已鎖定。改 APID 後，帶出欄會自動跟著變；要改帶出欄可直接蓋掉。"),
             ("", "這台不該登記 CIA：在「不登記原因」選原因（其他欄不用填）。"),
             ("檢查", "「3 待填檢查」每列顯示還缺幾格；交回時系統會再檢查一次，不合格的列會退回給你。"),
             ("注意", "請勿新增或刪除工作表、勿另存成其他格式；這份檔交回後請刪除寄件備份（含個人資料）。")]
    for k, v in lines:
        ws.append([k, safe(v)])
        ws.cell(ws.max_row, 1).font = bold
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 110

    # 4 APID 帶出（隱藏）：A＝APID，之後每個帶出欄一欄值、再一欄樣本說明
    wd = wb.create_sheet(SHEET_DERIVED)
    df = rules["derived_fields"]
    wd.append(["APID"] + df + [f"{f}（樣本）" for f in df])
    dtab = fleet_opts["derived"]
    for a in sorted(dtab):
        wd.append([safe(a)] + [safe(dtab[a][f][0] or "") for f in df] + [f"{dtab[a][f][1]}/{dtab[a][f][2]}" for f in df])
    nd = max(wd.max_row, 2)
    wd.sheet_state = "hidden"
    wd.protection.sheet = True

    # 5 選項清單
    wo = wb.create_sheet(SHEET_OPTS)
    opt_keys = ["api_id", "environment", "asset_status", "physical_location", "os", "usage_unit", "owning_company",
                "integrity", "confidentiality", "availability", "apid_confirm", "no_register"]
    opts = {**fleet_opts["options"], "apid_confirm": rules["apid_confirm_options"], "no_register": rules["no_register_reasons"]}
    names = _sysnames(conn)
    wo.append(opt_keys + ["APID 名稱"])
    longest = max(len(opts.get(k, [])) for k in opt_keys)
    for i in range(longest):
        wo.append([safe(opts[k][i]) if i < len(opts.get(k, [])) else None for k in opt_keys]
                  + [safe(names.get(opts["api_id"][i], "")) if i < len(opts["api_id"]) else None])
    wo.protection.sheet = True

    # 2 補登填寫
    ws = wb.create_sheet(SHEET_FILL, 1)
    ws.append(heads)
    for c in ws[1]:
        c.font = bold
    editable = {"api_id", "environment", "asset_status", "physical_location", "os", "remark", *df}
    api_col = L(ci["api_id"])
    for n, r in enumerate(items, 2):
        fl = json.loads(r["fill_json"] or "{}")
        prev = json.loads(r["filled_json"]) if r["filled_json"] else {}
        row = [n - 1]
        for _, c in cols:
            row.append(safe(prev.get(c, (fl.get(c) or {}).get("v", ""))))
        row += ["", "", safe(r["apid_suggest"] or ""), CONF_LABEL.get(r["apid_conf"] or "", ""), safe(r["basis_text"] or ""),
                safe(r["observed"] or ""), safe(r["observed_os"] or ""), r["track_code"], r["assign_ver"], r["key_hash"]]
        ws.append(row)
        papi = prev.get("api_id") or ""
        for i, f in enumerate(df):
            # 帶出欄放公式（APID 改了會跟著變）；退回重填的列，上次交回值若是人手動蓋的（跟帶出值不同）就保留字面值
            if (prev.get(f, "") == ((dtab.get(papi) or {}).get(f, (None,))[0] or "")) if f in prev \
                    else (fl.get(f) or {}).get("kind") in ("derived", "blank"):
                k = 2 + i
                ws.cell(n, ci[f]).value = f'=IFERROR(""&VLOOKUP(${api_col}{n},\'{SHEET_DERIVED}\'!$A$2:${L(len(df) * 2 + 1)}${nd},{k},FALSE),"")'
        for c, meta in fl.items():
            if c not in ci:
                continue
            cell = ws.cell(n, ci[c])
            if meta.get("kind") == "suggest":
                cell.fill = yellow
            if meta.get("src") and meta.get("kind") in ("suggest", "derived", "fixed", "system"):
                cell.comment = Comment(safe(meta["src"])[:300], "WEBIT3")
            if c in editable:
                cell.protection = unlocked
            else:
                cell.fill = grey
        for h in ("APID 確認", "不登記原因"):
            ws.cell(n, xi[h]).protection = unlocked
        if r["status"] == "bounced" and r["check_json"]:
            ws.cell(n, 1).comment = Comment("上次退回：" + "；".join(json.loads(r["check_json"]))[:300], "WEBIT3")
    last = ws.max_row
    for row in ws.iter_rows(min_row=2, max_row=last, min_col=2, max_col=len(heads)):
        for cell in row:
            if not (isinstance(cell.value, str) and cell.value.startswith("=")):
                cell.number_format = "@"

    def dv(key: str, col_no: int):
        n_opts = len(opts.get(key, []))
        if not n_opts or last < 2:
            return
        k = opt_keys.index(key) + 1
        v = DataValidation(type="list", formula1=f"='{SHEET_OPTS}'!${L(k)}$2:${L(k)}${n_opts + 1}", allow_blank=True)
        ws.add_data_validation(v)
        v.add(f"{L(col_no)}2:{L(col_no)}{last}")
    for key in ("api_id", "environment", "asset_status", "physical_location", "os", "usage_unit", "owning_company",
                "integrity", "confidentiality", "availability"):
        if key in ci:
            dv(key, ci[key])
    dv("apid_confirm", xi["APID 確認"])
    dv("no_register", xi["不登記原因"])
    req_cells = [ci[c] for c in rules["required_fields"] + df if c in ci]
    for c in req_cells:
        rng = f"{L(c)}2:{L(c)}{max(last, 2)}"
        ws.conditional_formatting.add(rng, FormulaRule(formula=[f'AND(LEN({L(c)}2)=0,LEN(${L(xi["不登記原因"])}2)=0)'],
                                                       fill=PatternFill("solid", fgColor="F8D7DA")))
    for h in (TRACK, VER, KEYH):
        ws.column_dimensions[L(xi[h])].hidden = True
    ws.freeze_panes = "B2"
    ws.protection.sheet = True
    ws.protection.formatColumns = False
    ws.protection.formatRows = False
    ws.protection.autoFilter = False

    # 3 待填檢查（即時公式）
    wc = wb.create_sheet(SHEET_CHECK, 2)
    wc.append(["項次", "主機名稱", "還缺幾格", "不登記"])
    for n in range(2, last + 1):
        miss = "+".join(f"(LEN('{SHEET_FILL}'!{L(c)}{n})=0)" for c in req_cells) or "0"
        apid_bad = f"('{SHEET_FILL}'!{api_col}{n}=\"{UNDETERMINED}\")+(LEN('{SHEET_FILL}'!{L(xi['APID 確認'])}{n})=0)"
        wc.append([n - 1, f"='{SHEET_FILL}'!{L(ci['hostname'])}{n}",
                   f"=IF(LEN('{SHEET_FILL}'!{L(xi['不登記原因'])}{n})>0,0,{miss}+{apid_bad})",
                   f"=IF(LEN('{SHEET_FILL}'!{L(xi['不登記原因'])}{n})>0,\"是\",\"\")"])
    wc.protection.sheet = True

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"CIA補登_{_person_label(people_names(conn), assignee)}_{'退回_' if bounced_only else ''}{part}of{len(allp)}_{datetime.now():%Y%m%d%H%M}.xlsx"
    return buf, items, fname_safe(fname), len(allp)


def _record_file(conn, assignee, part, nparts, bounced, fname, data: bytes, items, by, via) -> int:
    """檔案登記（GPT 2）：每產一個檔記一筆（誰、哪些台＋當時版本、雜湊）；現行／作廢由 files() 依目前分派算。"""
    _begin(conn)
    try:
        fid = conn.execute(
            "INSERT INTO cia_reg_file (assignee, part, parts, bounced, file_name, sha256, items_json, rounds_json, machines, max_ver, "
            "generated_at, generated_by, via) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (assignee, part, nparts, int(bool(bounced)), fname, hashlib.sha256(data).hexdigest(),
             json.dumps([[r["id"], r["assign_ver"]] for r in items]), json.dumps(sorted({r["round_id"] for r in items})),
             len(items), max(r["assign_ver"] for r in items), _now(), by, via)).lastrowid
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return fid


def _options_snapshot(conn) -> dict:
    rules = load_rules()
    fleet = _fleet(conn)
    return {"options": options(conn, fleet, rules), "derived": derived_table(fleet, rules)}


# ---------------------------------------------------------------- 交回

def _check_package(data: bytes) -> None:
    """不收巨集、外部連結、多出來的工作表（GPT 第 8 點）。"""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("檔案超過 10MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
    except zipfile.BadZipFile as exc:
        raise ValueError("讀不開這個檔（不是 .xlsx？）") from exc
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise ValueError("檔案含巨集，拒收（請用系統產生的 .xlsx，不要另存成 .xlsm）")
    if any(n.lower().startswith("xl/externallinks/") for n in names):
        raise ValueError("檔案含外部連結，拒收")


def _cell_txt(v) -> str:
    s = cia_fix._txt(v)
    return s


def read_return(conn, data: bytes) -> dict:
    """逐列分類（不寫庫）：refused 拒收、bounce 退回、pass 通過、claim 說不登記、same 跟上次一樣。"""
    from openpyxl import load_workbook

    _check_package(data)
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    except Exception as exc:  # noqa: BLE001 - openpyxl 對壞檔錯誤型態不一
        raise ValueError(f"讀不開這個檔：{exc}") from exc
    extra = [s for s in wb.sheetnames if s not in PERSON_SHEETS]
    if extra or SHEET_FILL not in wb.sheetnames:
        raise ValueError(f"這不是 CIA 補登檔，或工作表被改過（多出：{extra or '無'}；應有：{list(PERSON_SHEETS)}）")
    info = {str(r[0]): r[1] for r in wb[SHEET_INFO].iter_rows(values_only=True) if r and r[0]}
    owner = cia_fix._txt(info.get("負責人代號") or info.get("負責人"))
    rules = load_rules()
    cols = columns(rules)
    it = wb[SHEET_FILL].iter_rows(values_only=True)
    head = [cia_fix._txt(h) for h in next(it, [])]
    need = [h for h, _ in cols] + EXTRA_COLS
    miss = [h for h in need if h not in head]
    if miss:
        raise ValueError(f"補登填寫少了欄位：{'、'.join(miss)}（請用系統產生的檔案）")
    ix = {h: head.index(h) for h in need}
    h_of = {c: h for h, c in cols}
    opts_all = _options_snapshot(conn)
    opts, dtab = opts_all["options"], opts_all["derived"]
    known = _known_apis(conn)
    fleet = _fleet(conn)
    other = rules["other_option"]
    df = rules["derived_fields"]
    out = {"owner": owner, "owner_name": people_names(conn).get(owner, owner), "rows": 0, "refused": [], "bounce": [], "pass": [], "claim": [], "same": []}
    seen = set()
    for n, row in enumerate(it, 2):
        if row is None or all(v in (None, "") for v in row):
            continue
        out["rows"] += 1
        raw = lambda h: row[ix[h]] if ix[h] < len(row) else None  # noqa: E731
        g = lambda h: _cell_txt(raw(h))  # noqa: E731
        code = g(TRACK)
        m = re.fullmatch(r"CR(\d+)-(\d+)", code)
        r = conn.execute("SELECT * FROM cia_reg_item WHERE id = ? AND track_code = ?", (int(m.group(2)), code)).fetchone() \
            if m and _ready(conn) else None
        base = {"row": n, "track_code": code or "（空白）", "hostname": g(h_of["hostname"])}
        if r is None:
            out["refused"].append({**base, "why": "追蹤碼對不到（不是本系統產的列）"})
            continue
        base.update(item_id=r["id"], hostname=r["hostname"])
        if code in seen:
            out["refused"].append({**base, "why": "同一台出現兩次，只看第一次"})
            continue
        seen.add(code)
        if r["status"] not in ("assigned", "bounced"):
            out["refused"].append({**base, "why": f"目前是{STATUS[r['status']]}，不收交回"})
            continue
        if (r["assignee"] or "") != owner:
            out["refused"].append({**base, "why": f"已改派給 {r['assignee'] or '（未分派）'}，這份是 {owner or '（不明）'} 的檔"})
            continue
        if not cia_fix._same(g(VER), str(r["assign_ver"])):
            out["refused"].append({**base, "why": f"已改派（現在是第 {r['assign_ver']} 版，這份是第 {g(VER) or '?'} 版），請用新檔"})
            continue
        fl = json.loads(r["fill_json"] or "{}")
        locked = {h_of["hostname"]: r["hostname"] or "", h_of["ip"]: r["ip"] or "", KEYH: r["key_hash"], h_of["asset_serial"]: ""}
        for c, v in rules["fixed_vm"].items():
            if c in h_of:
                locked[h_of[c]] = v
        for c in rules["fixed_from_cia_vm_mode"]:
            locked[h_of[c]] = (fl.get(c) or {}).get("v", "")
        diffs = [{"col": h, "file": g(h), "system": safe(v)} for h, v in locked.items() if not cia_fix._same(g(h), safe(v))]
        if diffs:
            out["refused"].append({**base, "why": "鎖定欄被改過：" + "、".join(f"{d['col']}（檔案「{d['file']}」≠ 系統「{d['system']}」）" for d in diffs)})
            continue
        ident = _identity_problem(r, fleet)
        if ident:
            out["refused"].append({**base, "why": ident, "to_review": True})
            continue
        claim = g("不登記原因")
        if claim:
            if claim not in rules["no_register_reasons"]:
                out["bounce"].append({**base, "why": [f"不登記原因不在選項裡（{claim}）"], "values": {}})
            else:
                out["claim"].append({**base, "reason": claim, "note": g(h_of["remark"])})
            continue
        vals, why = {}, []
        api = g(h_of["api_id"]).split(" ")[0].strip()
        for h, c in cols:
            v = raw(h)
            if c in df and isinstance(v, str) and v.startswith("="):
                d = dtab.get(api, {}).get(c, (None, 0, 0))[0]       # 公式一律不信，伺服器端重算（GPT 第 7 點）
                vals[c] = d or ""
            elif c == "api_id":
                vals[c] = api
            else:
                vals[c] = g(h)
            if vals[c].startswith("'") and safe(vals[c][1:]) == vals[c]:
                vals[c] = vals[c][1:]
        if not api or api == UNDETERMINED:
            why.append("APID 沒填或仍是「無法判定」")
        elif known and api not in known:
            why.append(f"APID {api} 不在業務系統對照表")
        conf = g("APID 確認")
        if conf == "交統籌":
            why.append("負責人選「交統籌」")
        elif conf not in ("是", "改了"):
            why.append("「APID 確認」沒選")
        for c in rules["required_fields"] + df:
            if not vals.get(c):
                why.append(f"{h_of[c]} 沒填")
        for c in ("environment", "asset_status", "physical_location", "os", "usage_unit", "owning_company", "integrity",
                  "confidentiality", "availability"):
            v = vals.get(c)
            if not v or c not in h_of:
                continue
            if v == other:
                if not vals.get("remark"):
                    why.append(f"{h_of[c]} 選「其他」但附加說明沒寫")
            elif c in df and v == (dtab.get(api, {}).get(c, (None,))[0] or ""):
                continue          # 跟同 APID 的 CIA 寫法一樣：照收（那就是 CIA 現有的值）
            elif opts.get(c) and v not in opts[c]:
                why.append(f"{h_of[c]}「{v}」不在選項裡")
        if why:
            out["bounce"].append({**base, "why": why, "values": vals})
        else:
            prev = json.loads(r["filled_json"]) if r["filled_json"] else None
            (out["same"] if prev == vals and r["status"] == "passed" else out["pass"]).append({**base, "values": vals, "apid": api})
    wb.close()
    if not out["rows"]:
        raise ValueError("補登填寫沒有任何資料列")
    if not any(out[k] for k in ("bounce", "pass", "claim", "same")) and all(x["why"].startswith("追蹤碼") for x in out["refused"]):
        raise ValueError("這不是本系統產生的補登檔：沒有任何一列的追蹤碼對得上")
    return out


def _identity_problem(r, fleet: dict) -> str | None:
    """GPT 第 6 點：分派後這台的身分變了（machine_key 不見、或 UUID 換了）→ 轉待確認，不收。"""
    info = fleet.get(r["machine_key"])
    if info is None:
        return "這台的機器鍵已不存在（可能改名或已被併），轉待確認"
    if r["vm_uuid"] and info["uuids"] and r["vm_uuid"] not in info["uuids"]:
        return "VM UUID 跟分派時不同，轉待確認"
    return None


def upload_preview(conn, data: bytes, file_name: str | None) -> dict:
    sha = hashlib.sha256(data).hexdigest()
    done = conn.execute("SELECT at, by FROM cia_reg_upload WHERE sha256 = ?", (sha,)).fetchone() if _ready(conn) else None
    res = read_return(conn, data)
    return {**_summary(res), "file_name": file_name, "file_sha256": sha,
            "already": {"at": done["at"], "by": done["by"]} if done else None}


def _summary(res: dict) -> dict:
    return {"owner": res["owner"], "owner_name": res.get("owner_name") or res["owner"], "rows": res["rows"], "refused": res["refused"], "bounce": res["bounce"],
            "pass": [{k: v for k, v in x.items() if k != "values"} for x in res["pass"]],
            "claim": res["claim"], "same": [{k: v for k, v in x.items() if k != "values"} for x in res["same"]],
            "n": {k: len(res[k]) for k in ("refused", "bounce", "pass", "claim", "same")}}


def upload_confirm(conn, data: bytes, file_name: str | None, expect_sha: str, by: str) -> dict:
    """兩段式第二段。同一份檔（sha256）記過就直接回當時結果、不再寫（冪等，GPT 第 11 點）。"""
    sha = hashlib.sha256(data).hexdigest()
    if sha != expect_sha:
        raise Stale("檔案跟預覽時不同，請重新預覽")
    if not _ready(conn):
        raise LookupError("還沒有任何補登")
    _begin(conn)
    try:
        old = conn.execute("SELECT result_json FROM cia_reg_upload WHERE sha256 = ?", (sha,)).fetchone()
        if old:
            conn.commit()
            return {**json.loads(old[0]), "already": True}
        res = read_return(conn, data)
        now = _now()
        for x in res["refused"]:
            if x.get("to_review"):
                r = conn.execute("SELECT * FROM cia_reg_item WHERE id = ?", (x["item_id"],)).fetchone()
                conn.execute("UPDATE cia_reg_item SET status = 'review', note = ?, updated_at = ? WHERE id = ?", (x["why"], now, r["id"]))
                _event(conn, r["id"], r["round_id"], by, "review", r["status"], "review", assignee=r["assignee"], ver=r["assign_ver"],
                       now=now, source_file=file_name, source_sha=sha, note=x["why"])
        for kind, to in (("pass", "passed"), ("bounce", "bounced"), ("claim", "claim")):
            for x in res[kind]:
                r = conn.execute("SELECT * FROM cia_reg_item WHERE id = ?", (x["item_id"],)).fetchone()
                vals = x.get("values") or {}
                chk = x.get("why") if kind == "bounce" else ([f"不登記：{x['reason']}"] if kind == "claim" else [])
                conn.execute("UPDATE cia_reg_item SET status = ?, filled_json = ?, check_json = ?, returned_at = ?, updated_at = ? WHERE id = ?",
                             (to, json.dumps(vals, ensure_ascii=False) if vals else r["filled_json"], json.dumps(chk, ensure_ascii=False),
                              now, now, r["id"]))
                _event(conn, r["id"], r["round_id"], by, kind, r["status"], to, assignee=r["assignee"], ver=r["assign_ver"], now=now,
                       source_file=file_name, source_sha=sha, note="；".join(chk) or None)
        summ = {**_summary(res), "file_name": file_name, "file_sha256": sha, "recorded_at": now}
        conn.execute("INSERT INTO cia_reg_upload (sha256, file_name, assignee, at, by, result_json) VALUES (?,?,?,?,?,?)",
                     (sha, file_name, res["owner"], now, by, json.dumps(summ, ensure_ascii=False)))
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return summ


def uploads(conn) -> list[dict]:
    if not _ready(conn):
        return []
    out = []
    for r in conn.execute("SELECT sha256, file_name, assignee, at, by, result_json FROM cia_reg_upload ORDER BY at DESC"):
        n = (json.loads(r["result_json"] or "{}").get("n")) or {}
        out.append({"sha256": r["sha256"], "file_name": r["file_name"], "assignee": r["assignee"], "at": r["at"], "by": r["by"], **n})
    return out


# ---------------------------------------------------------------- 彙整交 CIA 管理人

def _merge_plan(conn) -> dict:
    rules = load_rules()
    rows = conn.execute("SELECT * FROM cia_reg_item WHERE status = 'passed' ORDER BY id").fetchall() if _ready(conn) else []
    cands = []
    for r in rows:
        v = json.loads(r["filled_json"] or "{}")
        cands.append({"item_id": r["id"], "asset_serial": r["track_code"], "unit": (v.get("inventory_department") or v.get("inventory_division") or "").strip() or "（未填單位）",
                      "asset_group": v.get("group_name") or "", "hostname": r["hostname"]})
    pk = cia_fix.pack(cands, int(rules["package_max_rows"]))
    fp = hashlib.sha256(json.dumps([c["item_id"] for c in cands]).encode()).hexdigest()
    return {"cands": cands, "packages": pk, "fingerprint": fp}


def merge_preview(conn) -> dict:
    p = _merge_plan(conn)
    return {"items": len(p["cands"]), "fingerprint": p["fingerprint"],
            "packages": [{"pkg_no": x["pkg_no"], "unit": x["unit"], "rows": len(x["rows"])} for x in p["packages"]]}


def merge(conn, by: str, expect: str) -> dict:
    _begin(conn)
    try:
        p = _merge_plan(conn)
        if p["fingerprint"] != expect:
            raise Stale("預覽之後通過的台數變了，請重新預覽")
        if not p["cands"]:
            raise ValueError("目前沒有通過、還沒彙整的機器")
        now = _now()
        cia = cia_fix._last_cia_import(conn)
        bid = conn.execute("INSERT INTO cia_reg_batch (created_at, created_by, item_count, pkg_count, max_rows, cia_import_at, cia_data_date) "
                           "VALUES (?,?,?,?,?,?,?)", (now, by, len(p["cands"]), len(p["packages"]), int(load_rules()["package_max_rows"]),
                                                      cia["at"], cia["data_date"])).lastrowid
        for c in p["cands"]:
            r = conn.execute("SELECT * FROM cia_reg_item WHERE id = ?", (c["item_id"],)).fetchone()
            conn.execute("UPDATE cia_reg_item SET status = 'merged', reg_batch_id = ?, pkg_no = ?, updated_at = ? WHERE id = ?",
                         (bid, c["pkg_no"], now, r["id"]))
            _event(conn, r["id"], r["round_id"], by, "merge", "passed", "merged", assignee=r["assignee"], ver=r["assign_ver"], now=now,
                   note=f"補登第 {bid} 批第 {c['pkg_no']} 包")
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return {"batch_id": bid, "items": len(p["cands"]), "packages": len(p["packages"])}


def batch_xlsx(conn, batch_id: int, pkg_no: int) -> tuple[io.BytesIO, int, str]:
    """交 CIA 管理人的補登包：照規格三的六張工作表（2、6 這一批只有表頭）。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    if not _ready(conn):
        raise LookupError("還沒有任何補登")
    b = conn.execute("SELECT * FROM cia_reg_batch WHERE id = ?", (batch_id,)).fetchone()
    if b is None:
        raise LookupError(f"找不到補登第 {batch_id} 批")
    rows = conn.execute("SELECT * FROM cia_reg_item WHERE reg_batch_id = ? AND pkg_no = ? ORDER BY id", (batch_id, pkg_no)).fetchall()
    if not rows:
        raise LookupError(f"補登第 {batch_id} 批沒有第 {pkg_no} 包")
    rules = load_rules()
    cols = columns(rules)
    bold = Font(bold=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "1 說明"
    for k, v in [("CIA 補登", f"補登第 {batch_id} 批・第 {pkg_no} 包（共 {b['pkg_count']} 包）・{len(rows)} 台"),
                 ("CIA 資料日", b["cia_data_date"] or "（不明）"), ("產生時間", b["created_at"]),
                 ("怎麼用", "「3 補登清單」照 CIA 硬體頁欄位排好，資產序號請由 CIA 給號；各列已由負責人確認 APID 並通過系統檢查。"),
                 ("對回", "CIA 登好後照平常匯出、在 WEBIT3 匯入，系統會自動標「已登記」。")]:
        ws.append([k, safe(v)])
        ws.cell(ws.max_row, 1).font = bold
    ws.column_dimensions["B"].width = 100
    wb.create_sheet("2 修正明細").append(["（補登批沒有修正明細）"])
    w3 = wb.create_sheet("3 補登清單")
    w3.append([h for h, _ in cols])
    w4 = wb.create_sheet("4 待填檢查")
    w4.append(["項次", "主機名稱", "還缺哪幾格"])
    for i, r in enumerate(rows, 1):
        v = json.loads(r["filled_json"] or "{}")
        w3.append([safe(v.get(c, "")) for _, c in cols])
        miss = [h for h, c in cols if c in rules["required_fields"] + rules["derived_fields"] and not v.get(c)]
        w4.append([i, safe(r["hostname"]), "、".join(miss) or "（齊全）"])
    w5 = wb.create_sheet("5 選項清單")
    opts = _options_snapshot(conn)["options"]
    keys = ["environment", "asset_status", "physical_location", "os", "owning_company", "device_vendor"]
    w5.append(keys)
    for i in range(max(len(opts.get(k, [])) for k in keys)):
        w5.append([safe(opts[k][i]) if i < len(opts.get(k, [])) else None for k in keys])
    wb.create_sheet("6 修正後全表（參考）").append(["（補登批沒有修正後全表）"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf, len(rows), f"CIA補登包_第{batch_id}批_第{pkg_no}包_CIA{b['cia_data_date'] or '未匯入'}.xlsx"


# ---------------------------------------------------------------- CIA 匯入後核對

def reconcile_after_import(conn, file_name: str | None, file_sha256: str | None, by: str | None) -> dict | None:
    """CIA 清冊匯入完成後呼叫（routers.imports._cia_commit，在 P1 核對之後、自己的交易）。
    未結案的每一台，用機器鍵（或分派時的 VM UUID）找：
      找得到、而且現在 CIA 有登記 → 已登記（記 CIA 序號）；找得到、CIA 還沒登 → 不動（之前是「找不到」就回原狀態）；
      找不到（所有來源都沒有這台）→ 先轉「待確認（找不到）」（非終態，管理員可確認消失或恢復）；
      連續 GONE_AFTER_MISSES 次核對都找不到 → 自動結案「資產已消失」（審查 P2-1：改名／鍵變不會一次就被關掉）。
    沒建過表就什麼都不做、回 None。"""
    import system_report as sr

    if not _ready(conn):
        return None
    if not conn.execute(f"SELECT 1 FROM cia_reg_item WHERE status IN ({','.join('?' * len(OPEN))}) LIMIT 1", OPEN).fetchone():
        return {"checked": 0, "registered": 0, "missing": 0, "found": 0, "gone": 0, "same_file": 0, "suspicious": False, "warning": None}
    fleet = _fleet(conn)
    by_uuid = {}
    for info in fleet.values():
        for u in info["uuids"]:
            by_uuid.setdefault(u, info)
    _begin(conn)
    try:
        now = _now()
        res = {"checked": 0, "registered": 0, "missing": 0, "found": 0, "gone": 0, "same_file": 0, "suspicious": False, "warning": None}
        src = {"source_file": file_name, "source_sha": file_sha256}
        rows = conn.execute(f"SELECT * FROM cia_reg_item WHERE status IN ({','.join('?' * len(OPEN))}) ORDER BY id", OPEN).fetchall()
        locate = lambda r: fleet.get(r["machine_key"]) or (by_uuid.get(r["vm_uuid"]) if r["vm_uuid"] else None)  # noqa: E731
        n_miss = sum(1 for r in rows if locate(r) is None)
        if len(rows) >= SUSPICIOUS_MIN_OPEN and n_miss > len(rows) * SUSPICIOUS_MISS_RATIO:
            res["suspicious"] = True
            res["warning"] = (f"這次匯入後有 {n_miss}／{len(rows)} 台（超過 {round(SUSPICIOUS_MISS_RATIO * 100)}%）同時找不到，"
                              "疑似匯錯檔或匯出不完整：這次不計「找不到」次數，請確認匯入的檔")
        for r in rows:
            res["checked"] += 1
            info = locate(r)
            if info is None:
                if res["suspicious"]:
                    continue
                if file_sha256 and r["last_miss_sha"] == file_sha256:
                    res["same_file"] += 1           # 同一份 CIA 檔重匯：不算第二次（審查 P1-1a）
                    continue
                misses = (r["miss_count"] or 0) + 1
                if misses >= GONE_AFTER_MISSES:
                    prev = r["prev_status"] if r["status"] == "missing" else r["status"]
                    conn.execute("UPDATE cia_reg_item SET status = 'gone', prev_status = ?, miss_count = ?, last_miss_sha = ?, closed_at = ?, "
                                 "closed_file = ?, closed_sha256 = ?, updated_at = ? WHERE id = ?",
                                 (prev, misses, file_sha256, now, file_name, file_sha256, now, r["id"]))
                    _event(conn, r["id"], r["round_id"], by, "gone", r["status"], "gone", assignee=r["assignee"], ver=r["assign_ver"],
                           now=now, note=f"連續 {misses} 次核對都找不到", **src)
                    res["gone"] += 1
                else:
                    prev = r["prev_status"] if r["status"] == "missing" else r["status"]
                    conn.execute("UPDATE cia_reg_item SET status = 'missing', prev_status = ?, miss_count = ?, last_miss_sha = ?, updated_at = ? "
                                 "WHERE id = ?", (prev, misses, file_sha256, now, r["id"]))
                    if r["status"] != "missing":
                        _event(conn, r["id"], r["round_id"], by, "missing", r["status"], "missing", assignee=r["assignee"],
                               ver=r["assign_ver"], now=now, note=f"第 {misses} 次找不到（機器鍵與 VM UUID 都對不到）", **src)
                    res["missing"] += 1
                continue
            cia_serials = sorted(x["asset_serial"] for x in info["cia_rows"] if not sr.is_off_book(x["asset_serial"]))
            if cia_serials:
                serial = cia_serials[0]
                conn.execute("UPDATE cia_reg_item SET status = 'registered', cia_serial = ?, miss_count = 0, closed_at = ?, closed_file = ?, "
                             "closed_sha256 = ?, updated_at = ? WHERE id = ?", (serial, now, file_name, file_sha256, now, r["id"]))
                _event(conn, r["id"], r["round_id"], by, "registered", r["status"], "registered", assignee=r["assignee"],
                       ver=r["assign_ver"], now=now, note=f"CIA 序號 {serial}", **src)
                res["registered"] += 1
            elif r["status"] == "missing" or (r["miss_count"] or 0):
                back = r["prev_status"] or "unassigned" if r["status"] == "missing" else r["status"]
                conn.execute("UPDATE cia_reg_item SET status = ?, miss_count = 0, last_miss_sha = NULL, prev_status = NULL, updated_at = ? "
                             "WHERE id = ?", (back, now, r["id"]))
                if r["status"] == "missing":
                    _event(conn, r["id"], r["round_id"], by, "found", "missing", back, assignee=r["assignee"], ver=r["assign_ver"],
                           now=now, **src)
                    res["found"] += 1
        cia_fix._put_setting(conn, SET_LAST_RECON, {"at": now, "file": file_name, "sha256": file_sha256, "by": by, **res})
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return res


def missing_decide(conn, ids: list[int], gone: bool, note: str | None, by: str) -> dict:
    """「待確認（找不到）」：管理員確認消失（→ 資產已消失，結案）或恢復（→ 找不到之前的狀態）。"""
    if not _ready(conn):
        raise LookupError("還沒有任何補登")
    _begin(conn)
    try:
        now, done, skipped = _now(), 0, []
        for r in _items(conn, ids):
            if r["status"] != "missing":
                skipped.append({"id": r["id"], "track_code": r["track_code"], "why": f"目前是{STATUS[r['status']]}，不是找不到"})
                continue
            to = "gone" if gone else (r["prev_status"] or "unassigned")
            conn.execute("UPDATE cia_reg_item SET status = ?, miss_count = 0, last_miss_sha = NULL, prev_status = ?, closed_at = ?, "
                         "updated_at = ? WHERE id = ?", (to, r["prev_status"] if gone else None, now if to == "gone" else None, now, r["id"]))
            _event(conn, r["id"], r["round_id"], by, "confirm_gone" if gone else "reopen_missing", "missing", to,
                   assignee=r["assignee"], ver=r["assign_ver"], now=now, note=note)
            done += 1
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return {"done": done, "skipped": skipped}


def restore_gone(conn, ids: list[int], note: str | None, by: str) -> dict:
    """「資產已消失」（自動或人工結案）恢復成消失前的狀態（審查 P1-1b）；寫事件。同一台已有別的未結案項目就不能恢復。"""
    if not (note or "").strip():
        raise ValueError("恢復要寫原因")
    if not _ready(conn):
        raise LookupError("還沒有任何補登")
    _begin(conn)
    try:
        now, done, skipped = _now(), 0, []
        for r in _items(conn, ids):
            if r["status"] != "gone":
                skipped.append({"id": r["id"], "track_code": r["track_code"], "why": f"目前是{STATUS[r['status']]}，不是資產已消失"})
                continue
            if conn.execute(f"SELECT 1 FROM cia_reg_item WHERE machine_key = ? AND id <> ? AND status IN ({','.join('?' * len(OPEN))})",
                            (r["machine_key"], r["id"], *OPEN)).fetchone():
                skipped.append({"id": r["id"], "track_code": r["track_code"], "why": "同一台已有進行中的補登"})
                continue
            to = r["prev_status"] or "unassigned"
            conn.execute("UPDATE cia_reg_item SET status = ?, prev_status = NULL, miss_count = 0, last_miss_sha = NULL, closed_at = NULL, "
                         "closed_file = NULL, closed_sha256 = NULL, updated_at = ? WHERE id = ?", (to, now, r["id"]))
            _event(conn, r["id"], r["round_id"], by, "restore_gone", "gone", to, assignee=r["assignee"], ver=r["assign_ver"], now=now, note=note)
            done += 1
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return {"done": done, "skipped": skipped}


# ---------------------------------------------------------------- 檔案、待重寄、催收、ZIP

def _file_rows(conn) -> list:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cia_reg_file'").fetchone():
        return []
    return conn.execute("SELECT * FROM cia_reg_file ORDER BY id").fetchall()


def files(conn, assignee: str | None = None) -> list[dict]:
    """產過的每一個檔（GPT 2）：現行＝檔裡每一台現在仍分給這個人、版本相同，而且沒有被同一人較新的檔包含；否則作廢（寫原因）。"""
    if not _ready(conn):
        return []
    cur = {r["id"]: r for r in conn.execute("SELECT id, assignee, assign_ver, status FROM cia_reg_item")}
    rows = _file_rows(conn)
    newest: dict[tuple, int] = {}
    for f in rows:
        for iid, _v in json.loads(f["items_json"] or "[]"):
            newest[(f["assignee"], iid)] = f["id"]
    names = people_names(conn)
    out = []
    for f in rows:
        if assignee is not None and f["assignee"] != assignee:
            continue
        why = None
        for iid, v in json.loads(f["items_json"] or "[]"):
            it = cur.get(iid)
            if it is None or it["assignee"] != f["assignee"]:
                why = "裡面有台已改派給別人"
            elif it["assign_ver"] != v:
                why = "裡面有台的分派版本變了"
            elif newest.get((f["assignee"], iid)) != f["id"]:
                why = "已有較新的檔"
            if why:
                break
        out.append({**dict(f), "assignee_name": names.get(f["assignee"], f["assignee"]), "state": "作廢" if why else "現行", "void_why": why,
                    "rounds": json.loads(f["rounds_json"] or "[]")})
    return out[::-1]


def mark_sent(conn, file_id: int, by: str) -> dict:
    _begin(conn)
    try:
        f = conn.execute("SELECT * FROM cia_reg_file WHERE id = ?", (file_id,)).fetchone()
        if f is None:
            raise LookupError(f"找不到檔案 {file_id}")
        now = _now()
        conn.execute("UPDATE cia_reg_file SET sent_at = ?, sent_by = ? WHERE id = ?", (now, by, file_id))
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return {"id": file_id, "sent_at": now}


def _resend_ids(conn) -> set[int]:
    """待重寄：已分派／退回重填的台，沒有被任何「現行而且標了已寄」的檔涵蓋（退回的要在退回之後寄出的檔才算）。"""
    if not _ready(conn):
        return set()
    sent_cover: dict[int, str] = {}
    for f in files(conn):
        if f["state"] == "現行" and f["sent_at"]:
            for iid, _v in json.loads(f["items_json"] or "[]"):
                sent_cover[iid] = max(sent_cover.get(iid, ""), f["generated_at"])
    out = set()
    for r in conn.execute("SELECT id, status, returned_at FROM cia_reg_item WHERE status IN ('assigned','bounced')"):
        g = sent_cover.get(r["id"])
        if g is None or (r["status"] == "bounced" and r["returned_at"] and g < r["returned_at"]):
            out.add(r["id"])
    return out


def chase(conn, round_id: int | None = None, today: str | None = None) -> list[dict]:
    """催收（GPT 1）：每位負責人一列。數字＝list_items(assignee, metric[, round_id]) 同一份判斷。"""
    if not _ready(conn):
        return []
    today = today or datetime.now().strftime("%Y-%m-%d")
    names = people_names(conn)
    dl = {r[0]: r[1] for r in conn.execute("SELECT id, deadline FROM cia_reg_round")}
    resend = _resend_ids(conn)
    fl = files(conn)
    per: dict[str, dict] = {}
    for r in conn.execute("SELECT * FROM cia_reg_item WHERE assignee IS NOT NULL AND (? IS NULL OR round_id = ?)", (round_id, round_id)):
        p = per.setdefault(r["assignee"], {"assignee": r["assignee"], "assignee_name": names.get(r["assignee"], r["assignee"]),
                                           "due": 0, "returned": 0, "bounced": 0, "passed": 0, "remaining": 0, "resend": 0, "deadlines": set()})
        for m in ("due", "returned", "bounced", "passed", "remaining"):
            p[m] += _metric(r, m)
        p["resend"] += r["id"] in resend
        if _metric(r, "remaining") and dl.get(r["round_id"]):
            p["deadlines"].add(dl[r["round_id"]])
    out = []
    for k, p in sorted(per.items(), key=lambda kv: kv[1]["assignee_name"]):
        mine = [f for f in fl if f["assignee"] == k]
        deadline = min(p.pop("deadlines")) if p["deadlines"] else None
        overdue = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(deadline, "%Y-%m-%d")).days if deadline and p["remaining"] else 0
        overdue = max(0, overdue)
        if not p["remaining"]:
            nxt = "完成"
        elif p["resend"] and not any(f["sent_at"] for f in mine):
            nxt = "下載檔案並寄出，寄完按「標記已寄」"
        elif p["resend"]:
            nxt = "重寄（有改派或退回的台還沒寄出新檔）"
        elif p["bounced"]:
            nxt = "等退回的台重填交回"
        elif overdue:
            nxt = f"催交（已逾期 {overdue} 天）"
        else:
            nxt = "等交回"
        out.append({**p, "deadline": deadline, "overdue_days": overdue, "next": nxt,
                    "last_generated": max((f["generated_at"] for f in mine), default=None),
                    "last_sent": max((f["sent_at"] for f in mine if f["sent_at"]), default=None),
                    "files_current": sum(1 for f in mine if f["state"] == "現行")})
    return out


def zip_files(conn, by: str | None, assignee: str | None = None) -> tuple[bytes, str, int]:
    """整批下載（GPT 4）：每位（或指定一位）負責人目前待填的每一個檔＋「派送清冊.xlsx」（負責人、檔名、台數、sha256）。"""
    from openpyxl import Workbook

    if not _ready(conn):
        raise LookupError("還沒有任何補登")
    people = [a for (a,) in conn.execute("SELECT DISTINCT assignee FROM cia_reg_item WHERE assignee IS NOT NULL "
                                         "AND status IN ('assigned','bounced') ORDER BY assignee")]
    if assignee is not None:
        people = [a for a in people if a == assignee]
    if not people:
        raise LookupError("目前沒有待填的檔")
    names = people_names(conn)
    cap = int(load_rules()["per_person_cap"])
    st = get_settings(conn)
    mail = {q["key"]: q["email"] for lst in st["assignees"].values() for q in lst}
    roster = Workbook()
    ws = roster.active
    ws.title = "派送清冊"
    ws.append(["項次", "負責人", "代號", "信箱", "檔名", "台數", "sha256"])
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for a in people:
            nparts = len(parts(_person_items(conn, a), cap))
            for part in range(1, nparts + 1):
                x, cnt, fname = person_xlsx(conn, a, part, False, by, "ZIP")
                data = x.getvalue()
                z.writestr(fname, data)
                n += 1
                ws.append([n, safe(names.get(a, a)), safe(a), safe(mail.get(a, "")), safe(fname), cnt, hashlib.sha256(data).hexdigest()])
        rb = io.BytesIO()
        roster.save(rb)
        z.writestr("派送清冊.xlsx", rb.getvalue())
    label = _person_label(names, assignee) if assignee else "全部"
    return buf.getvalue(), fname_safe(f"CIA補登_{label}_{datetime.now():%Y%m%d%H%M}.zip"), n


def last_reconcile(conn) -> dict | None:
    return cia_fix._setting(conn, SET_LAST_RECON, None)


def summary(conn, round_id: int | None = None) -> dict:
    """卡片＋進度矩陣＋回測摘要＋最後一次核對＋輪次。卡片數字＝list_items 同一份（verify_numbers 釘住）。round_id＝只看那一輪。"""
    bt = backtest(conn)
    return {"counts": counts(conn, round_id), "status_labels": STATUS, "progress": progress(conn, round_id), "metrics": METRICS,
            "backtest": bt, "settings": get_settings(conn), "last_reconcile": last_reconcile(conn),
            "cia_import": cia_fix._last_cia_import(conn), "files": person_files(conn), "rounds": rounds(conn),
            "resend": len(_resend_ids(conn))}
