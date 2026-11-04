"""EOS 通知與回覆追蹤（批次一，2.041.51／審查修正 2.041.52）。

## 這個模組是什麼、不是什麼（業務規則以 docs/codex-review/2026-10-30/110 第八節為準）

管理人員的工作：**提出** OS／硬體到期風險 → **通知** SP／AP 負責人 → **追蹤**回覆。是否換、怎麼換由負責人決定；
要保留就必須留一份**依據**（公司目前沒有對應的審查規範，所以本模組沒有簽核引擎、不要求任何簽署人；
使用者原話「是你不讓我升級，不是我不升級」，至少要有依據）。管理人員不代各單位規劃汰換。

* 已通知 ≠ 已回覆；已回覆 ≠ 已完成；擬保留 ≠ 有依據；保留不改 EOS 日期、不隱藏過期設備、不代表風險消失；不發明風險分數。
* 「已通知」**不存欄位**：由既有 eos_notify_state／eos_notify_log 算出（單一真相）。本模組完全不寄信、不呼叫 SMTP／Teams。
* 追蹤資料只有一張表 eos_follow（一列＝EOS 項目 item_key × 負責人 owner_ref；缺負責人 owner_ref＝「待指派」）
  ＋ eos_follow_history（每次變更的前後值、操作者、時間、理由）。**本模組從不寫 EOS 資料、不改 EOS 日期**。
* 衍生狀態（不存、由查詢算；加總＝下鑽走同一份計算 classify()）：見 FLAGS。
* 讀取（overview／detail／reminders）絕不寫庫、不建表；表不存在＝當作還沒有追蹤資料。寫入入口才 ensure_tables。
* 時間一律可注入；「今天」一律是 Asia/Taipei（固定 +8，無日光節約），不看伺服器時區（TPE／today_tpe）。

## 保留（hold）的硬規則（後端強制，不只靠畫面）
reply_state='擬保留' 時，必須**同時**有：阻礙來源類別（已登記於類別清單；需說明的類別要有說明）＋依據說明＋證據文字參照＋
記錄人／日期＋重新檢視日，才算「保留（已附依據）」；缺任一項只能是「擬保留（尚無依據）」並列待辦。
重新檢視日**當日或之前**＝「保留已到期」，自動成為待辦，永不永久綠燈；重新檢視日與承諾日不得晚於「今天起 N 個月」
（N 存設定，預設 24），避免用 9999-12-31 讓一筆永遠看起來沒事。證據只存文字參照，不存附件。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

import eos_notify as en
import vuln_notify as vn

SETTING_KEY = "eos_follow"
UNASSIGNED = "待指派"
ORPHAN_LOG_OWNER = "(沒人收)"
REPLY_STATES = ("待回覆", "規劃更換", "擬保留")
DONE_STATES = ("未完成", "更換完成")
DEMO_PREFIX = "demo|"
DISCLAIMER = "依據由負責人自行提供，公司目前沒有對應的審查規範；「已附依據」只代表有記錄，不代表風險消失。"
TZ_LABEL = "Asia/Taipei（UTC+8）"
TPE = en.TPE
LOG_WINDOW_DAYS = 35          # 通知紀錄沒有逐筆項目：寄送失敗只算「最近一個月批次」內、且晚於該列最後一次成功的那一封
MAX_CLASSES = 50
MAX_KEY = 300
MAX_OWNER = 200

#: 阻礙來源類別（預設值；實際清單存 app_settings，admin 可增減）。need_note＝該類別必須另填說明（單位名／原因）。
DEFAULT_CLASSES = [
    {"name": "原廠或廠商相容性", "need_note": False},
    {"name": "業務單位不同意", "need_note": True},       # 需註明單位
    {"name": "專案或變更凍結", "need_note": False},
    {"name": "預算", "need_note": False},
    {"name": "法規或監管", "need_note": False},
    {"name": "技術不相容", "need_note": False},
    {"name": "其他", "need_note": True},                 # 必填說明
]
DEFAULTS = {
    "reason_classes": DEFAULT_CLASSES,
    "review_default_months": 6,      # 只是表單預填的「建議值」，不是系統規則
    "max_horizon_months": 24,        # 重新檢視日／承諾日最晚只能設到「今天起」幾個月（防永久綠燈）
    "soon_days": 30,                 # 重新檢視日前幾天算「即將到期」
    "reminder": {"enabled": False, "unreplied_days": None, "plan_overdue_days": None,
                 "hold_expired_days": None, "escalate_to": ""},      # 預設關閉；不擅定層級與期限
}

EDIT_FIELDS = ("reply_state", "committed_date", "reply_source", "hold_reason_class", "hold_reason_note",
               "hold_basis_text", "hold_evidence_ref", "hold_review_date", "blocker_confirm",
               "done_state", "done_evidence_ref", "succession_note")
HOLD_BASIS_FIELDS = ("hold_reason_class", "hold_reason_note", "hold_basis_text", "hold_evidence_ref",
                     "hold_review_date", "blocker_confirm")
FIELD_LABEL = {
    "reply_state": "回覆狀態", "committed_date": "承諾日", "reply_source": "回覆來源（誰說的、何時、經何管道）",
    "hold_reason_class": "阻礙來源類別", "hold_reason_note": "類別說明（單位／原因）", "hold_basis_text": "依據說明",
    "hold_evidence_ref": "證據參照", "hold_review_date": "重新檢視日", "blocker_confirm": "阻礙方確認",
    "done_state": "完成狀態", "done_evidence_ref": "完成證據參照", "succession_note": "接替關係註記",
}
MAXLEN = {"reply_source": 300, "hold_reason_note": 200, "hold_basis_text": 500, "hold_evidence_ref": 300,
          "blocker_confirm": 300, "done_evidence_ref": 300, "succession_note": 200}
REASON_MAX = 300
MAXLEN["reason"] = REASON_MAX

#: 衍生旗標（一列可有多個；done／voided 的列只有自己）。TODO_FLAGS＝需要管理人員處理的。
FLAGS = {
    "unnotified": "已確認到期尚未通知",
    "unassigned": "缺負責人（待指派）",
    "delivery_failed": "最近一次寄送失敗",
    "unreplied": "已通知尚未回覆",
    "plan_overdue": "規劃更換逾承諾日",
    "hold_no_basis": "擬保留但尚無依據",
    "hold_expired": "保留已到期（待重新檢視）",
    "hold_expiring": "保留即將到期",
    "done": "更換完成",
    "voided": "已作廢追蹤",
}
TODO_FLAGS = ("unnotified", "unassigned", "delivery_failed", "unreplied", "plan_overdue", "hold_no_basis", "hold_expired")
TILE_ORDER = ("unnotified", "unassigned", "delivery_failed", "unreplied", "plan_overdue", "hold_no_basis",
              "hold_expired", "hold_expiring", "done", "voided", "todo", "all")
TILE_LABEL = {**FLAGS, "todo": "全部待辦", "all": "全部追蹤項目"}

SQL = [
    """CREATE TABLE IF NOT EXISTS eos_follow (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_key TEXT NOT NULL, owner_ref TEXT NOT NULL,
        track TEXT, hostname TEXT, ip TEXT, item_name TEXT, eos_date TEXT,          -- 建立當下的項目快照（項目日後消失仍看得出是哪一件）
        reply_state TEXT NOT NULL DEFAULT '待回覆' CHECK (reply_state IN ('待回覆','規劃更換','擬保留')),
        committed_date TEXT, reply_source TEXT,
        hold_reason_class TEXT, hold_reason_note TEXT, hold_basis_text TEXT, hold_evidence_ref TEXT,
        hold_recorded_by TEXT, hold_recorded_at TEXT, hold_review_date TEXT, blocker_confirm TEXT,
        done_state TEXT NOT NULL DEFAULT '未完成' CHECK (done_state IN ('未完成','更換完成')), done_evidence_ref TEXT,
        succession_note TEXT, voided_at TEXT, voided_by TEXT, void_reason TEXT,
        version INTEGER NOT NULL DEFAULT 1,
        is_demo INTEGER NOT NULL DEFAULT 0, demo_notified_at TEXT, demo_notify_status TEXT,
        created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_eos_follow_key ON eos_follow(item_key, owner_ref)",
    """CREATE TABLE IF NOT EXISTS eos_follow_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, follow_id INTEGER NOT NULL, at TEXT NOT NULL, by TEXT,
        op TEXT NOT NULL, reason TEXT, before_json TEXT, after_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_eos_follow_hist ON eos_follow_history(follow_id)",
]
#: 舊庫 ensure_tables 會補上缺的欄位（2.041.52 新增這幾個）。
_ADD_COLUMNS: list[tuple[str, str]] = [
    ("succession_note", "TEXT"), ("voided_at", "TEXT"), ("voided_by", "TEXT"), ("void_reason", "TEXT"),
    ("version", "INTEGER NOT NULL DEFAULT 1"),
]


class Conflict(ValueError):
    """同時有人改了同一筆／同一筆已存在（API 回 409）。"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    """只在寫入入口呼叫；讀取路徑絕不呼叫。"""
    for s in SQL:
        conn.execute(s)
    have = {r[1] for r in conn.execute("PRAGMA table_info(eos_follow)")}
    for col, ddl in _ADD_COLUMNS:
        if col not in have:
            conn.execute(f"ALTER TABLE eos_follow ADD COLUMN {col} {ddl}")


def _tbl(conn: sqlite3.Connection, name: str) -> bool:
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
    except sqlite3.Error:
        return False


# ===================================================================== 時間（一律 Asia/Taipei）
now_tpe = en.now_tpe          # 與通知引擎共用同一個 Asia/Taipei 今日 helper
today_tpe = en.today_tpe


def _ts(d: datetime) -> str:
    """稽核時間統一格式：YYYY-MM-DD HH:MM:SS+08:00（台北）。"""
    return d.strftime("%Y-%m-%d %H:%M:%S") + "+08:00"


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + (d.month - 1) + n, 12)
    last = (date(y + (m + 1) // 12, (m + 1) % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m + 1, min(d.day, last))


# ===================================================================== 設定
class SettingsCorrupt(ValueError):
    pass


def _merge_settings(raw: dict | None) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))        # 深拷貝
    raw = raw or {}
    if isinstance(raw.get("reason_classes"), list) and raw["reason_classes"]:
        cfg["reason_classes"] = [{"name": str(c.get("name", "")).strip(), "need_note": bool(c.get("need_note"))}
                                 for c in raw["reason_classes"] if isinstance(c, dict) and str(c.get("name", "")).strip()]
    for k in ("review_default_months", "soon_days", "max_horizon_months"):
        if isinstance(raw.get(k), int) and not isinstance(raw.get(k), bool):
            cfg[k] = raw[k]
    if isinstance(raw.get("reminder"), dict):
        cfg["reminder"].update({k: raw["reminder"][k] for k in cfg["reminder"] if k in raw["reminder"]})
    return cfg


def load_settings(conn: sqlite3.Connection) -> dict:
    """設定（沒存過＝預設值）。內容壞掉丟 SettingsCorrupt；只容忍「表不存在」，其他資料庫錯誤照樣往上丟（不吞成預設）。"""
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        row = None
    if not row:
        return _merge_settings(None)
    try:
        return _merge_settings(json.loads(row[0]))
    except (ValueError, TypeError) as exc:
        raise SettingsCorrupt(f"EOS 追蹤設定讀不出來（內容壞掉，不是沒設定）：{type(exc).__name__}: {exc}") from exc


def _safe_settings(conn: sqlite3.Connection) -> tuple[dict, str | None]:
    try:
        return load_settings(conn), None
    except SettingsCorrupt as exc:
        return _merge_settings(None), str(exc)


def _opt_days(v, what: str, hi: int = 3650) -> int | None:
    if v in (None, ""):
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= hi:
        raise ValueError(f"{what}要是 0～{hi} 的整數天數，或留空（不啟用這條規則）")
    return v


def validate_settings(raw: dict) -> dict:
    """PUT 設定的驗證。不合法一律丟 ValueError（API 轉 400），不默默改成別的值。"""
    if not isinstance(raw, dict):
        raise ValueError("設定格式不對")
    classes = raw.get("reason_classes")
    if not isinstance(classes, list) or not classes:
        raise ValueError("阻礙來源類別清單不可為空")
    if len(classes) > MAX_CLASSES:
        raise ValueError(f"阻礙來源類別最多 {MAX_CLASSES} 個")
    seen, out = set(), []
    for c in classes:
        if not isinstance(c, dict):
            raise ValueError("阻礙來源類別格式不對")
        name = _strip_zw(str(c.get("name", ""))).strip()
        if not name or len(name) > 40:
            raise ValueError("類別名稱不可為空，且最多 40 字")
        if name in seen:
            raise ValueError(f"類別「{name}」重複")
        seen.add(name)
        out.append({"name": name, "need_note": bool(c.get("need_note"))})
    months, soon = raw.get("review_default_months", 6), raw.get("soon_days", 30)
    horizon = raw.get("max_horizon_months", 24)
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 60:
        raise ValueError("重新檢視預設期間要是 1～60 個月（只是表單預填的建議值）")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= 60:
        raise ValueError("重新檢視日／承諾日的最遠期限要是 1～60 個月")
    if isinstance(soon, bool) or not isinstance(soon, int) or not 1 <= soon <= 365:
        raise ValueError("「即將到期」天數要是 1～365")
    rem = raw.get("reminder") or {}
    esc = str(rem.get("escalate_to") or "").strip()
    if len(esc) > 200:
        raise ValueError("催辦對象最多 200 字")
    return {"reason_classes": out, "review_default_months": months, "max_horizon_months": horizon, "soon_days": soon,
            "reminder": {"enabled": bool(rem.get("enabled")),
                         "unreplied_days": _opt_days(rem.get("unreplied_days"), "未回覆催辦天數"),
                         "plan_overdue_days": _opt_days(rem.get("plan_overdue_days"), "逾承諾日催辦天數"),
                         "hold_expired_days": _opt_days(rem.get("hold_expired_days"), "保留到期催辦天數"),
                         "escalate_to": esc}}


def save_settings(conn: sqlite3.Connection, raw: dict, by: str | None, audit_hook=None) -> dict:
    cfg = validate_settings(raw)
    ensure_tables(conn)
    try:
        before = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)).fetchone()
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (SETTING_KEY, json.dumps(cfg, ensure_ascii=False)))
        _finish(conn, audit_hook, {"op": "settings", "had_before": before is not None})
    except Exception:
        conn.rollback()
        raise
    used = {r[0] for r in conn.execute("SELECT DISTINCT hold_reason_class FROM eos_follow WHERE is_demo=0 AND hold_reason_class IS NOT NULL")}
    return {"settings": cfg, "unregistered_in_use": sorted(used - {c["name"] for c in cfg["reason_classes"]})}


def _finish(conn, audit_hook, info: dict) -> None:
    """稽核（activity_log）與資料寫進同一個交易：稽核失敗＝整筆回滾，例外往上丟（不吞）。"""
    try:
        if audit_hook:
            audit_hook(conn, info)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ===================================================================== 驗證小工具
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
#: 零寬／隱形字元（判空前先移除，否則「​」也能當依據說明）
_ZW = re.compile("[​-‏‪-‮⁠-⁯﻿­᠎ㅤﾠ]")


def _strip_zw(s: str) -> str:
    return _ZW.sub("", s or "")


def _blank(v) -> bool:
    return not _strip_zw(str(v if v is not None else "")).strip()


def _date_ok(s: str | None) -> date | None:
    """真的日期解析（YYYY-MM-DD；0000-00-00、2026-02-30 都是無效）。"""
    if not s or not _DATE.fullmatch(s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _clean_text(v, field: str) -> str | None:
    if v is None:
        return None
    t = re.sub(r"[\r\n\t]+", " ", _strip_zw(str(v))).strip()
    if not t:
        return None
    cap = MAXLEN.get(field, 200)
    if len(t) > cap:
        raise ValueError(f"「{FIELD_LABEL.get(field, field)}」最多 {cap} 字")
    return t


def hold_missing(r: dict, classes: dict[str, dict]) -> list[str]:
    """擬保留缺哪些依據（空清單＝依據齊全）。類別沒登記在清單裡要指名，不默默歸成「其他」。"""
    miss: list[str] = []
    cls = _strip_zw(r.get("hold_reason_class") or "").strip()
    if not cls:
        miss.append("阻礙來源類別")
    elif cls not in classes:
        miss.append(f"阻礙來源類別「{cls}」未登記於類別清單（請管理員先加入，或改選已登記的類別）")
    elif classes[cls]["need_note"] and _blank(r.get("hold_reason_note")):
        miss.append(f"「{cls}」需填說明（單位／原因）")
    if _blank(r.get("hold_basis_text")):
        miss.append("依據說明")
    if _blank(r.get("hold_evidence_ref")):
        miss.append("證據參照")
    if _blank(r.get("hold_recorded_by")) or _blank(r.get("hold_recorded_at")):
        miss.append("記錄人／日期")
    if _date_ok(r.get("hold_review_date")) is None:
        miss.append("重新檢視日")
    return miss


# ===================================================================== 衍生狀態（單一份計算）
def classify(r: dict, today: date, cfg: dict) -> dict:
    """在一列上算出 flags／status／天數。r 需有基本欄位（in_window、owner_ref、notified…）與追蹤欄位。
    **只讀 r，不寫任何東西；不碰 EOS 日期。** 加總＝下鑽：tiles 與下鑽清單都用這個函式的結果。
    項目或負責人已不在目前清單（stale）的列照樣算：沿用最後已知的通知狀態與已存的回覆／承諾日／保留日。"""
    classes = {c["name"]: c for c in cfg["reason_classes"]}
    flags: list[str] = []
    r["unreplied_days"] = r["overdue_days"] = r["review_in_days"] = None
    r["hold_missing"] = []
    unassigned = r["owner_ref"] == UNASSIGNED
    if r.get("voided_at"):
        flags = ["voided"]
        status = "已作廢追蹤"
    elif r.get("done_state") == "更換完成":
        flags = ["done"]
        status = "更換完成"
    else:
        if unassigned:
            flags.append("unassigned")
        if r["in_window"] and not r["notified"]:
            flags.append("unnotified")
        if r.get("last_send_failed"):                      # 最新一封寄送失敗：與「已通知」可並存，不被舊成功蓋掉
            flags.append("delivery_failed")
        rs = r.get("reply_state") or "待回覆"
        status = ""
        if rs == "待回覆":
            if r["notified"]:
                flags.append("unreplied")
                d = _date_ok((r.get("notified_at") or "")[:10])
                r["unreplied_days"] = (today - d).days if d else None
                status = "已通知，待回覆"
            elif unassigned:
                status = "待指派"
            elif r.get("last_send_failed"):
                status = "通知投遞失敗"
            elif r.get("notify_map") == "recipient_unverified":
                status = "收件地址無法確認（待核對）"
            elif r.get("notify_map") == "unmapped":
                status = "無法對應通知紀錄"
            else:
                status = "尚未通知"
        elif rs == "規劃更換":
            c = _date_ok(r.get("committed_date"))
            if c is not None and today > c:                      # 承諾日當天還不算逾期，隔天才算
                flags.append("plan_overdue")
                r["overdue_days"] = (today - c).days
                status = "規劃更換・逾承諾日"
            else:
                status = "規劃更換（承諾日內）"
        else:                                                     # 擬保留
            miss = hold_missing(r, classes)
            r["hold_missing"] = miss
            if miss:
                flags.append("hold_no_basis")
                status = "擬保留（尚無依據）"
            else:
                rv = _date_ok(r["hold_review_date"])
                r["review_in_days"] = (rv - today).days
                if today >= rv:                                   # 重新檢視日當天（含）就算到期
                    flags.append("hold_expired")
                    status = "保留已到期（待重新檢視）"
                else:
                    status = "保留（已附依據）"
                    if (rv - today).days <= int(cfg.get("soon_days") or 30):
                        flags.append("hold_expiring")
                        status += "・即將到期"
        if "delivery_failed" in flags and "寄送失敗" not in status and "投遞失敗" not in status:
            status += "・最近一次寄送失敗"
    if r.get("stale") and "voided" not in flags:
        status = (status + "・") if status else ""
        status += "項目或負責人已不在目前清單"
    r["flags"] = flags
    r["status"] = status
    r["todo"] = any(f in TODO_FLAGS for f in flags)
    return r


def match_flag(r: dict, flag: str | None) -> bool:
    if not flag or flag == "all":
        return True
    if flag == "todo":
        return bool(r["todo"])
    return flag in r["flags"]


def tiles_of(rows: list[dict]) -> list[dict]:
    return [{"key": k, "label": TILE_LABEL[k], "count": sum(1 for r in rows if match_flag(r, k))} for k in TILE_ORDER]


# ===================================================================== 通知事實（讀既有 state／log，不寫）
def norm_name(s: str | None) -> str:
    """AD 顯示名與登記名比對用：去括號內容、去空白、轉小寫（「王小明 (Ming)」＝「王小明」）。"""
    t = re.sub(r"[（(\[【][^）)\]】]*[）)\]】]", "", s or "")
    return re.sub(r"\s+", "", t).lower()


class _Notify:
    """一次總覽用的通知事實索引。state 與 log 都只讀。"""

    def __init__(self, conn: sqlite3.Connection, today: date, warnings: list[str]):
        self.today, self.conn = today, conn
        self.state: dict[str, tuple] = {}
        if _tbl(conn, "eos_notify_state"):
            for r in conn.execute("SELECT * FROM eos_notify_state"):
                self.state[r["state_key"]] = (r["last_bucket"], r["last_sent_at"], r["last_recipient"] if "last_recipient" in r.keys() else None)
        self.by_mail: dict[str, tuple] = {}
        self.by_name: dict[str, tuple] = {}
        self.orphan: tuple | None = None
        if _tbl(conn, "eos_notify_log"):
            for r in conn.execute("SELECT owner_name, recipient, status, sent_at FROM eos_notify_log ORDER BY id"):
                rec = (r["status"], r["sent_at"])
                self.by_mail[(r["recipient"] or "").strip().lower()] = rec
                on = (r["owner_name"] or "").strip()
                if on == ORPHAN_LOG_OWNER:
                    self.orphan = rec
                elif on and not on.startswith("(本期"):
                    self.by_name[norm_name(on)] = rec
        self.has_history = bool(self.state or self.by_mail)
        self.ad = vn.ad_ready(conn)
        if not self.ad:
            warnings.append("AD 名單未匯入：無法核對收件地址是否變更，也無法把登記名對到信箱（通知紀錄改用姓名比對）。")
        self._memo: dict[str, tuple] = {}

    def person(self, name: str):
        if name not in self._memo:
            self._memo[name] = vn.resolve_recipient(self.conn, name) if self.ad else (None, "AD 未匯入")
        return self._memo[name]

    def facts(self, track: str, mk: str, item: str, name: str | None, b: int | None) -> dict:
        out = {"notified": False, "notified_at": None, "notified_bucket": None, "last_log_status": None, "last_log_at": None,
               "last_send_failed": False, "notify_map": "ok", "recipient": None, "recipient_unrecorded": False}
        st = self.state.get(f"{track}|{mk}|{name or ''}|{item}")
        mail = None
        if name is None:
            rec = self.orphan
        else:
            person, _why = self.person(name)
            mail = (person.get("mail") or "").strip().lower() if person else None
            rec = self.by_mail.get(mail) if mail else self.by_name.get(norm_name(name))
        out["recipient"] = mail
        if st is not None:
            level_ok = b is None or (st[0] is not None and b >= st[0])
            moved = bool(mail and st[2] and mail != str(st[2]).strip().lower())      # 收件地址換了：舊地址的成功不算
            # 現在解析不出收件人（查無此人／信箱被清空）、但 state 記著舊地址：舊成功不能沿用，要人核對
            unverified = bool(name is not None and self.ad and not mail and st[2])
            out["notified"] = level_ok and not moved and not unverified
            if unverified:
                out["notify_map"] = "recipient_unverified"
            out["recipient_unrecorded"] = bool(out["notified"] and st[2] is None)      # 舊資料沒記收件地址：相容算已通知，只提示
            out["notified_at"] = st[1] if out["notified"] else None
            out["notified_bucket"] = st[0]
        if rec:
            out["last_log_status"], out["last_log_at"] = rec
            at = _date_ok((rec[1] or "")[:10])
            recent = at is not None and (self.today - at).days <= LOG_WINDOW_DAYS
            newer = st is None or (rec[1] or "") > (st[1] or "")
            out["last_send_failed"] = rec[0] == "failed" and recent and newer
        if name is not None and mail is None and st is None and rec is None and self.has_history and out["notify_map"] == "ok":
            out["notify_map"] = "unmapped"                  # 有負責人但對不上任何通知紀錄（查無信箱／名稱不一致）
        return out


# ===================================================================== 讀取（不寫庫）
FOLLOW_COLS = ("id", "version", "voided_at", "voided_by", "void_reason") + EDIT_FIELDS + (
    "hold_recorded_by", "hold_recorded_at", "created_by", "created_at", "updated_by", "updated_at")


def _follow_rows(conn: sqlite3.Connection, demo: bool) -> dict[tuple[str, str], dict]:
    if not _tbl(conn, "eos_follow"):
        return {}
    return {(r["item_key"], r["owner_ref"]): dict(r)
            for r in conn.execute("SELECT * FROM eos_follow WHERE is_demo = ?", (1 if demo else 0,))}


def item_key_of(c: dict) -> str:
    return f"{c['track']}|{c['mk']}|{c['item']}"


def split_item_key(ik: str) -> tuple[str, str, str]:
    p = ik.split("|")
    return p[0], "|".join(p[1:-1]), p[-1]


def _follow_fields(f: dict | None) -> dict:
    """追蹤欄位（沒有追蹤紀錄＝預設：待回覆／未完成）。"""
    f = f or {}
    out = {k: f.get(k) for k in FOLLOW_COLS}
    out["reply_state"] = f.get("reply_state") or "待回覆"
    out["done_state"] = f.get("done_state") or "未完成"
    out["has_follow"] = bool(f)
    return out


def _real_base(conn: sqlite3.Connection, today: date, warnings: list[str]) -> tuple[list[dict], dict]:
    """目前 EOS 候選（重用 eos_notify.eos_candidates，**不重算 EOS**）× 負責人，附上通知事實（讀 eos_notify_state／log）。"""
    cfg_n = {}
    try:
        cfg_n = en.load_settings(conn)
    except en.SettingsCorrupt as exc:
        warnings.append(f"EOS 通知設定壞掉，門檻改用預設值：{exc}")
    buckets = en.buckets_of(cfg_n)
    cands, stats = en.eos_candidates(conn, today)
    nf = _Notify(conn, today, warnings)
    follows = _follow_rows(conn, demo=False)
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for c in cands:
        b = en.bucket_for(c["days_left"], buckets)
        ik = item_key_of(c)
        for name in (c["owners"] or [None]):
            ref = name or UNASSIGNED
            f = follows.get((ik, ref))
            if b is None and f is None:
                continue                                           # 還很遠、也沒人追蹤：不列
            seen.add((ik, ref))
            fa = nf.facts(c["track"], c["mk"], c["item"], name, b)
            out.append({
                **_follow_fields(f), "item_key": ik, "owner_ref": ref, "track": c["track"],
                "track_label": en.TRACK_LABEL[c["track"]], "owner_role": en.OWNER_LABEL[c["track"]],
                "hostname": c.get("hostname"), "ip": c.get("ip"), "environment": c.get("environment"), "mk": c["mk"],
                "item": c["item"], "eos_date": c["eos_date"], "eos_date_text": c.get("eos_date_text"),
                "date_precision": c.get("date_precision"), "days_left": c["days_left"], "bucket": b,
                "in_window": b is not None, **fa, "stale": False, "is_demo": False,
            })
    for (ik, ref), f in follows.items():                           # 追蹤紀錄還在、但項目或負責人已不在目前清單：照樣列出並沿用最後已知狀態
        if (ik, ref) in seen:
            continue
        d = _date_ok(f.get("eos_date"))
        left = (d - today).days if d else None
        track, mk, item = split_item_key(ik)
        fa = nf.facts(track, mk, item, None if ref == UNASSIGNED else ref, en.bucket_for(left, buckets))
        out.append({
            **_follow_fields(f), "item_key": ik, "owner_ref": ref, "track": f.get("track"),
            "track_label": en.TRACK_LABEL.get(f.get("track"), f.get("track")), "owner_role": en.OWNER_LABEL.get(f.get("track"), ""),
            "hostname": f.get("hostname"), "ip": f.get("ip"), "environment": None, "mk": mk or None, "item": f.get("item_name"),
            "eos_date": f.get("eos_date"), "eos_date_text": f.get("eos_date"), "date_precision": None,
            "days_left": left, "bucket": en.bucket_for(left, buckets), "in_window": en.bucket_for(left, buckets) is not None,
            **fa, "stale": True, "is_demo": False,
        })
    return out, {"machines": stats.get("machines"), "buckets": list(buckets), "candidates": len(cands)}


def _demo_base(conn: sqlite3.Connection, today: date) -> tuple[list[dict], dict]:
    out = []
    for (ik, ref), f in _follow_rows(conn, demo=True).items():
        d = _date_ok(f.get("eos_date"))
        out.append({
            **_follow_fields(f), "item_key": ik, "owner_ref": ref, "track": f.get("track"),
            "track_label": en.TRACK_LABEL.get(f.get("track"), f.get("track")), "owner_role": en.OWNER_LABEL.get(f.get("track"), ""),
            "hostname": f.get("hostname"), "ip": f.get("ip"), "environment": "示範", "item": f.get("item_name"),
            "eos_date": f.get("eos_date"), "eos_date_text": f.get("eos_date"), "date_precision": "day",
            "days_left": (d - today).days if d else None, "bucket": 0, "in_window": True,
            "notified": bool(f.get("demo_notified_at")), "notified_at": f.get("demo_notified_at"), "notified_bucket": 0,
            "last_log_status": f.get("demo_notify_status"), "last_log_at": f.get("demo_notified_at"),
            "last_send_failed": f.get("demo_notify_status") == "failed", "notify_map": "ok", "recipient": None,
            "stale": False, "is_demo": True,
        })
    return out, {"machines": None, "buckets": [], "candidates": len(out)}


def overview(conn: sqlite3.Connection, today: date | None = None, demo: bool = False, now_utc: datetime | None = None) -> dict:
    """追蹤總覽：tiles（管理摘要磚）＋全部列（每列已編固定項次、已算好 flags）。**只讀。**
    今天＝台北日期（today 可注入，否則由 now_utc／系統時間換算）。demo=True 只回示範資料（獨立標記，永遠不混進真實資料）。"""
    today = today or today_tpe(now_utc)
    warnings: list[str] = []
    cfg, corrupt = _safe_settings(conn)
    if corrupt:
        warnings.append(corrupt + "（改用預設值顯示）")
    rows, meta = _demo_base(conn, today) if demo else _real_base(conn, today, warnings)
    rows.sort(key=lambda r: ((r.get("hostname") or "").lower(), r.get("track") or "", r.get("item") or "", r["owner_ref"], r["item_key"]))
    for i, r in enumerate(rows, 1):
        r["no"] = i                                                 # 項次：以此排序為準、與狀態無關，篩選／重排後同一筆仍同一號
        classify(r, today, cfg)
    demo_n = 0
    if _tbl(conn, "eos_follow"):
        demo_n = conn.execute("SELECT COUNT(*) FROM eos_follow WHERE is_demo=1").fetchone()[0]
    return {"today": today.isoformat(), "tz": TZ_LABEL, "mode": "demo" if demo else "real", "demo_count": demo_n,
            "tiles": tiles_of(rows), "rows": rows, "settings": cfg, "meta": meta, "warnings": warnings,
            "disclaimer": DISCLAIMER, "flag_labels": TILE_LABEL}


def drill(ov: dict, flag: str | None) -> list[dict]:
    """下鑽清單＝同一份 rows 依旗標過濾（加總＝下鑽的保證就在這裡）。"""
    return [r for r in ov["rows"] if match_flag(r, flag)]


def detail(conn: sqlite3.Connection, item_key: str, owner_ref: str) -> dict:
    """一列展開：通知紀錄（對得上這位負責人信箱／姓名的寄送）＋變更歷史。只讀。"""
    if len(item_key or "") > MAX_KEY or len(owner_ref or "") > MAX_OWNER:
        raise ValueError("參數太長")
    logs: list[dict] = []
    if _tbl(conn, "eos_notify_log"):
        mail = None
        if owner_ref != UNASSIGNED and vn.ad_ready(conn):
            p, _w = vn.resolve_recipient(conn, owner_ref)
            mail = (p.get("mail") or "").strip().lower() if p else None
        for r in conn.execute("SELECT id, sent_at, schedule, recipient, owner_name, status, detail, subject, item_count "
                              "FROM eos_notify_log ORDER BY id DESC LIMIT 400"):
            on = (r["owner_name"] or "").strip()
            if owner_ref == UNASSIGNED:
                hit = on == ORPHAN_LOG_OWNER
            elif mail:
                hit = (r["recipient"] or "").strip().lower() == mail
            else:
                hit = norm_name(on) == norm_name(owner_ref)
            if hit:
                logs.append({k: r[k] for k in r.keys()})
            if len(logs) >= 20:
                break
    hist: list[dict] = []
    if _tbl(conn, "eos_follow_history") and _tbl(conn, "eos_follow"):
        hist = [dict(r) for r in conn.execute(
            "SELECT h.id, h.at, h.by, h.op, h.reason, h.before_json, h.after_json FROM eos_follow_history h "
            "JOIN eos_follow f ON f.id = h.follow_id WHERE f.item_key = ? AND f.owner_ref = ? ORDER BY h.id DESC",
            (item_key, owner_ref))]
    return {"logs": logs, "history": hist, "tz": TZ_LABEL,
            "note": "通知紀錄以收件人為單位（信裡是一批項目），沒有逐筆項目；寄送失敗＝該收件地址最近一個月內最新一封失敗且晚於這一列最後一次成功。"}


def reminders(ov: dict) -> dict:
    """應催辦清單（**唯讀預覽，不發送**）。規則預設關閉／未設天數；每條規則只有填了天數才會列。"""
    rem = ov["settings"]["reminder"]
    items: list[dict] = []
    for r in ov["rows"]:
        for rule, flag, days_key, label in (
                ("unreplied_days", "unreplied", "unreplied_days", "已通知尚未回覆"),
                ("plan_overdue_days", "plan_overdue", "overdue_days", "規劃更換逾承諾日")):
            n = rem.get(rule)
            if n is not None and flag in r["flags"] and (r.get(days_key) or 0) >= n:
                items.append({"no": r["no"], "item_key": r["item_key"], "owner_ref": r["owner_ref"], "hostname": r["hostname"],
                              "rule": label, "days": r.get(days_key), "threshold": n, "stale": r["stale"]})
        n = rem.get("hold_expired_days")
        if n is not None and "hold_expired" in r["flags"] and r["review_in_days"] is not None and -r["review_in_days"] >= n:
            items.append({"no": r["no"], "item_key": r["item_key"], "owner_ref": r["owner_ref"], "hostname": r["hostname"],
                          "rule": "保留已到期", "days": -r["review_in_days"], "threshold": n, "stale": r["stale"]})
    return {"enabled": bool(rem.get("enabled")), "escalate_to": rem.get("escalate_to") or "", "settings": rem,
            "items": items, "sent": False,
            "note": "本批只做設定與預覽，不會發送任何催辦或升級通知；預設關閉，層級與期限由使用者設定。"}


# ===================================================================== 寫入（USER_WRITE；稽核同交易）
def _norm_fields(fields: dict) -> dict:
    unknown = set(fields) - set(EDIT_FIELDS)
    if unknown:
        raise ValueError(f"不能修改的欄位：{'、'.join(sorted(unknown))}")
    out = {}
    for k, v in fields.items():
        if k in ("reply_state", "done_state", "committed_date", "hold_reason_class", "hold_review_date"):
            out[k] = (_strip_zw(str(v)).strip() or None) if v is not None else None
        else:
            out[k] = _clean_text(v, k)
    return out


def _validate(m: dict, classes: dict[str, dict], today: date, horizon: int) -> None:
    if m["reply_state"] not in REPLY_STATES:
        raise ValueError("回覆狀態要是 待回覆／規劃更換／擬保留")
    if m["done_state"] not in DONE_STATES:
        raise ValueError("完成狀態要是 未完成／更換完成")
    limit = add_months(today, horizon)
    for f in ("committed_date", "hold_review_date"):
        if not m.get(f):
            continue
        d = _date_ok(m[f])
        if d is None:
            raise ValueError(f"「{FIELD_LABEL[f]}」要是 YYYY-MM-DD 的有效日期")
        if d > limit:
            raise ValueError(f"「{FIELD_LABEL[f]}」不能晚於 {limit.isoformat()}（今天起 {horizon} 個月內）。"
                             "日期設得太遠，這筆就會長期看起來沒事；請設近一點的日期，到時再重新檢視。")
    cls = m.get("hold_reason_class")
    if cls and cls not in classes:
        raise ValueError(f"阻礙來源類別「{cls}」未登記於類別清單；請管理員先到設定新增，或改選已登記的類別（不會默默歸成「其他」）")
    if m["reply_state"] == "規劃更換":
        if not m.get("committed_date"):
            raise ValueError("「規劃更換」必須有承諾日（承諾日是負責人的承諾，管理人員只轉錄並註明來源）")
        if not m.get("reply_source"):
            raise ValueError("請註明回覆來源（誰說的、何時、經何管道：email／會議／單號）")
    if m["reply_state"] == "擬保留" and not m.get("reply_source"):
        raise ValueError("請註明回覆來源（誰說的、何時、經何管道：email／會議／單號）")
    if m["done_state"] == "更換完成" and not m.get("done_evidence_ref"):
        raise ValueError("「更換完成」必須附完成證據參照（單號／驗證紀錄／會議紀錄等文字參照）")


def _audit_row(conn, fid: int, op: str, by: str | None, reason: str | None, before, after, now: datetime) -> None:
    conn.execute("INSERT INTO eos_follow_history (follow_id, at, by, op, reason, before_json, after_json) VALUES (?,?,?,?,?,?,?)",
                 (fid, _ts(now), by, op, reason, json.dumps(before, ensure_ascii=False) if before is not None else None,
                  json.dumps(after, ensure_ascii=False) if after is not None else None))


def get_follow(conn: sqlite3.Connection, item_key: str, owner_ref: str) -> dict | None:
    if not _tbl(conn, "eos_follow"):
        return None
    r = conn.execute("SELECT * FROM eos_follow WHERE item_key=? AND owner_ref=?", (item_key, owner_ref)).fetchone()
    return dict(r) if r else None


def _check_keys(item_key, owner_ref, expected_version) -> tuple[str, str, int | None]:
    ik, ref = (item_key or "").strip(), (owner_ref or "").strip()
    if not ik or not ref:
        raise ValueError("缺項目或負責人")
    if len(ik) > MAX_KEY or len(ref) > MAX_OWNER:
        raise ValueError("項目或負責人參數太長")
    if expected_version is not None and (isinstance(expected_version, bool) or not isinstance(expected_version, int)
                                         or not 0 <= expected_version <= 2_000_000_000):
        raise ValueError("版本號格式不對")
    return ik, ref, expected_version


def _live_keys(conn, now: datetime, candidates: list[dict] | None) -> set[tuple[str, str]]:
    cands = candidates if candidates is not None else en.eos_candidates(conn, now.date())[0]
    return {(item_key_of(c), n or UNASSIGNED) for c in cands for n in (c["owners"] or [None])}


def upsert(conn: sqlite3.Connection, item_key: str, owner_ref: str, fields: dict, reason: str | None, by: str | None,
           now: datetime | None = None, audit_hook=None, expected_version: int | None = None,
           candidates: list[dict] | None = None) -> dict:
    """新增或更正一筆追蹤。只動 fields 裡有給的欄位；其他維持原值（回覆不會改 done_state、完成不會改 reply_state）。
    新增必須是目前清單裡真的有的（項目×負責人）；已存在的更正不需要（項目可能因換機已從清單消失）。
    更正必須附理由；已作廢的不能再編輯。樂觀鎖用整數 version（每次寫入 +1），不符回 Conflict（409）。
    now＝台北時間（naive）。回傳 {"changed": bool, "row": ...}。"""
    now = now or now_tpe()
    ik, ref, expected_version = _check_keys(item_key, owner_ref, expected_version)
    if ik.startswith(DEMO_PREFIX):
        raise ValueError("示範資料不能用一般入口修改（請到「示範資料」清除後重新產生）")
    why = _clean_text(reason, "reason") if reason is not None else None
    upd = _norm_fields(fields)
    cfg = load_settings(conn)
    classes = {c["name"]: c for c in cfg["reason_classes"]}
    ensure_tables(conn)
    old = get_follow(conn, ik, ref)
    if old is not None and old["is_demo"]:
        raise ValueError("示範資料不能用一般入口修改")
    snap: dict = {}
    if old is None:
        cands = candidates if candidates is not None else en.eos_candidates(conn, now.date())[0]
        cand = next((c for c in cands if item_key_of(c) == ik), None)
        names = (cand or {}).get("owners") or []
        if cand is None or (ref not in names and not (ref == UNASSIGNED and not names)):
            raise LookupError("目前 EOS 清單裡沒有這個項目×負責人（可能已更新，請重新整理後再試）")
        snap = {"track": cand["track"], "hostname": cand.get("hostname"), "ip": cand.get("ip"),
                "item_name": cand["item"], "eos_date": cand["eos_date"]}
        merged = {"reply_state": "待回覆", "done_state": "未完成", **{f: None for f in EDIT_FIELDS if f not in ("reply_state", "done_state")}}
    else:
        if old.get("voided_at"):
            raise ValueError("這筆追蹤已作廢，不能再編輯（作廢的列仍可在「全部追蹤項目」看到；請改在接替的新列上記錄）")
        if expected_version is not None and int(old.get("version") or 1) != expected_version:
            raise Conflict("這一筆剛被別人更新過（版本已變），請重新整理後再更正")
        if not why:
            raise ValueError("更正既有紀錄必須填更正理由")
        merged = {f: old.get(f) for f in EDIT_FIELDS}
    merged.update(upd)
    _validate(merged, classes, now.date(), int(cfg["max_horizon_months"]))
    base = {f: (old.get(f) if old else None) for f in EDIT_FIELDS}
    if old is None:
        base["reply_state"], base["done_state"] = "待回覆", "未完成"
    diff_b = {f: base[f] for f in EDIT_FIELDS if merged[f] != base[f]}
    diff_a = {f: merged[f] for f in diff_b}
    if old is not None and not diff_a:
        return {"changed": False, "row": old}
    # 依據欄位有變、或剛切回「擬保留」：記錄人／日期更新成本次操作者與時間
    hold_touched = any(f in diff_a for f in HOLD_BASIS_FIELDS) or (merged["reply_state"] == "擬保留" and base["reply_state"] != "擬保留")
    rec_by = rec_at = None
    if hold_touched:
        rec_by, rec_at = by or "(未知)", _ts(now)
        diff_b["hold_recorded_by"], diff_b["hold_recorded_at"] = (old or {}).get("hold_recorded_by"), (old or {}).get("hold_recorded_at")
        diff_a["hold_recorded_by"], diff_a["hold_recorded_at"] = rec_by, rec_at
    try:
        if old is None:
            cols = ["item_key", "owner_ref", *snap, *EDIT_FIELDS, "version", "created_by", "created_at", "updated_by", "updated_at"]
            vals = [ik, ref, *snap.values(), *(merged[f] for f in EDIT_FIELDS), 1, by, _ts(now), by, _ts(now)]
            if hold_touched:
                cols += ["hold_recorded_by", "hold_recorded_at"]
                vals += [rec_by, rec_at]
            try:
                cur = conn.execute(f"INSERT INTO eos_follow ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
            except sqlite3.IntegrityError as exc:
                raise Conflict("這個項目×負責人剛被別人建立了追蹤紀錄，請重新整理後改用更正") from exc
            fid, op = cur.lastrowid, "create"
        else:
            keys = [k for k in diff_a if k in EDIT_FIELDS or k in ("hold_recorded_by", "hold_recorded_at")]
            sets = [f"{k}=?" for k in keys] + ["version=version+1", "updated_by=?", "updated_at=?"]
            vals = [diff_a[k] for k in keys] + [by, _ts(now)]
            cur = conn.execute(f"UPDATE eos_follow SET {','.join(sets)} WHERE id=? AND version=? AND voided_at IS NULL",
                               vals + [old["id"], old["version"]])
            if cur.rowcount != 1:
                raise Conflict("這一筆剛被別人更新過（版本已變），請重新整理後再更正")
            fid, op = old["id"], "update"
        _audit_row(conn, fid, op, by, why, diff_b, diff_a, now)
        _finish(conn, audit_hook, {"op": op, "item_key": ik, "owner_ref": ref, "fields": sorted(k for k in diff_a if k in EDIT_FIELDS)})
    except Exception:
        conn.rollback()
        raise
    return {"changed": True, "row": get_follow(conn, ik, ref)}


def void(conn: sqlite3.Connection, item_key: str, owner_ref: str, reason: str | None, by: str | None, now: datetime | None = None,
         audit_hook=None, expected_version: int | None = None, succession_note: str | None = None,
         candidates: list[dict] | None = None) -> dict:
    """作廢追蹤（接替列時結案舊列）：必須附理由、留歷史、之後不能再編輯、不進待辦磚，但「全部追蹤項目」仍看得到。
    只能作廢「項目或負責人已不在目前清單」的列（仍在清單的請直接更正）；更換完成的列已結案，不需作廢。
    succession_note（選填，文字）註記接替關係，例如「由 srv-x / 王小明 接替」。"""
    now = now or now_tpe()
    ik, ref, expected_version = _check_keys(item_key, owner_ref, expected_version)
    why = _clean_text(reason, "reason")
    if not why:
        raise ValueError("作廢必須填理由")
    note = _clean_text(succession_note, "succession_note") if succession_note is not None else None
    ensure_tables(conn)
    old = get_follow(conn, ik, ref)
    if old is None or old["is_demo"]:
        raise LookupError("找不到這筆追蹤紀錄")
    if old.get("voided_at"):
        raise ValueError("這筆追蹤已經作廢")
    if expected_version is not None and int(old.get("version") or 1) != expected_version:
        raise Conflict("這一筆剛被別人更新過（版本已變），請重新整理後再操作")
    if old["done_state"] == "更換完成":
        raise ValueError("這筆已是「更換完成」，已結案，不需要作廢")
    if (ik, ref) in _live_keys(conn, now, candidates):
        raise ValueError("這個項目×負責人仍在目前 EOS 清單內，請直接更正，不要作廢（作廢是給項目消失或負責人更換後接替用）")
    try:
        sets, vals = ["voided_at=?", "voided_by=?", "void_reason=?", "version=version+1", "updated_by=?", "updated_at=?"], [_ts(now), by, why, by, _ts(now)]
        if note is not None:
            sets.insert(0, "succession_note=?")
            vals.insert(0, note)
        cur = conn.execute(f"UPDATE eos_follow SET {','.join(sets)} WHERE id=? AND version=? AND voided_at IS NULL", vals + [old["id"], old["version"]])
        if cur.rowcount != 1:
            raise Conflict("這一筆剛被別人更新過（版本已變），請重新整理後再操作")
        _audit_row(conn, old["id"], "void", by, why, {"voided_at": None, "succession_note": old.get("succession_note")},
                   {"voided_at": _ts(now), "succession_note": note if note is not None else old.get("succession_note")}, now)
        _finish(conn, audit_hook, {"op": "void", "item_key": ik, "owner_ref": ref, "fields": ["voided"]})
    except Exception:
        conn.rollback()
        raise
    return {"row": get_follow(conn, ik, ref)}


# ---- 示範資料（獨立標記 is_demo=1；預設不顯示；一鍵清除；絕不混進真實資料）
def _delete_demo(conn: sqlite3.Connection) -> int:
    """刪示範列與其歷史（不 commit；由呼叫端的交易一起提交）。"""
    ids = [r[0] for r in conn.execute("SELECT id FROM eos_follow WHERE is_demo=1")]
    for i in ids:
        conn.execute("DELETE FROM eos_follow_history WHERE follow_id=?", (i,))
    conn.execute("DELETE FROM eos_follow WHERE is_demo=1")
    return len(ids)


def clear_demo(conn: sqlite3.Connection, by: str | None, audit_hook=None) -> dict:
    ensure_tables(conn)
    try:
        n = _delete_demo(conn)
        _finish(conn, audit_hook, {"op": "demo_clear", "n": n})
    except Exception:
        conn.rollback()
        raise
    return {"cleared": n}


def seed_demo(conn: sqlite3.Connection, by: str | None, today: date | None = None, audit_hook=None) -> dict:
    """產生示範資料（先清掉舊的示範資料；主機名／負責人都標「示範」，IP 用保留網段 10.99.x）。涵蓋每一種狀態。"""
    today = today or today_tpe()
    ensure_tables(conn)
    now = now_tpe()

    def D(n: int) -> str:
        return (today + timedelta(days=n)).isoformat()

    P = {"reply_source": "示範：甲君 email 回覆"}
    spec = [
        # (hostname, track, item, eos 剩餘天數, owner, notified_days_ago|None, status, fields)
        ("示範-主機01", "os", "示範作業系統 A", -200, UNASSIGNED, None, None, {}),
        ("示範-主機02", "os", "示範作業系統 A", -100, "示範負責人乙", None, "failed", {}),
        ("示範-主機03", "hw", "示範伺服器 X", -30, "示範負責人丙", 20, "sent", {}),
        ("示範-主機04", "os", "示範作業系統 B", 90, "示範負責人丁", 40, "sent",
         {**P, "reply_state": "規劃更換", "committed_date": D(-5)}),
        ("示範-主機05", "os", "示範作業系統 B", 120, "示範負責人丁", 40, "sent",
         {**P, "reply_state": "規劃更換", "committed_date": D(60)}),
        ("示範-主機06", "hw", "示範伺服器 X", -50, "示範負責人戊", 35, "sent", {**P, "reply_state": "擬保留"}),
        ("示範-主機07", "os", "示範作業系統 C", -10, "示範負責人己", 35, "sent",
         {**P, "reply_state": "擬保留", "hold_reason_class": "原廠或廠商相容性", "hold_basis_text": "示範：廠商應用僅支援此版本",
          "hold_evidence_ref": "示範：廠商信件 2026-10-01", "hold_review_date": D(15)}),
        ("示範-主機08", "os", "示範作業系統 C", -10, "示範負責人己", 35, "sent",
         {**P, "reply_state": "擬保留", "hold_reason_class": "專案或變更凍結", "hold_basis_text": "示範：年底變更凍結",
          "hold_evidence_ref": "示範：變更公告單 CHG-DEMO-1", "hold_review_date": D(120)}),
        ("示範-主機09", "os", "示範作業系統 C", -10, "示範負責人己", 35, "sent",
         {**P, "reply_state": "擬保留", "hold_reason_class": "預算", "hold_basis_text": "示範：預算尚未核定",
          "hold_evidence_ref": "示範：會議紀錄 2026-04-01", "hold_review_date": D(-3)}),
        ("示範-主機10", "hw", "示範伺服器 Y", -300, "示範負責人庚", 60, "sent",
         {**P, "reply_state": "規劃更換", "committed_date": D(-40), "done_state": "更換完成", "done_evidence_ref": "示範：驗收單 ACC-DEMO-1"}),
    ]
    try:
        _delete_demo(conn)
        n = 0
        for i, (host, track, item, left, owner, ago, st, fv) in enumerate(spec, 1):
            ik = f"{DEMO_PREFIX}{track}|{host}|{item}"
            hold = any(k in fv for k in HOLD_BASIS_FIELDS)
            cols = ["item_key", "owner_ref", "track", "hostname", "ip", "item_name", "eos_date", "is_demo", "demo_notified_at",
                    "demo_notify_status", "created_by", "created_at", "updated_by", "updated_at", *fv]
            vals = [ik, owner, track, host, f"10.99.0.{i}", item, D(left), 1,
                    _ts(now - timedelta(days=ago)) if ago is not None and st == "sent" else None, st, by, _ts(now), by, _ts(now), *fv.values()]
            if hold:
                cols += ["hold_recorded_by", "hold_recorded_at"]
                vals += ["示範記錄人", _ts(now)]
            cur = conn.execute(f"INSERT INTO eos_follow ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
            _audit_row(conn, cur.lastrowid, "create", by, "示範資料", None, fv or None, now)
            n += 1
        _finish(conn, audit_hook, {"op": "demo_seed", "n": n})
    except Exception:
        conn.rollback()
        raise
    return {"created": n}
