"""憑證管理（2026-11-01 第一階段，CC 設計、GPT 審查；2.042.02 第二階段：經中繼 PC 的到期探測）：CA 簽發 TLS 憑證的「清單＋到期查詢」。

範圍與界線（使用者決定，不可改）：
· 只做管理與查詢、**不做更換**；更換一律人工，`replace_method`／`replace_status` 只存不驅動任何動作。系統永遠不接觸私鑰、不收 CSR。
· **本系統自己不做任何對外連線（TLS 握手）**。第二階段（使用者 2026-11-03 核准）的自動到期檢查，跟 WhatsUp 同一做法：
  公司中繼 Windows PC 跑只讀腳本（AI/tools_relay/cert_collect.ps1）對清單上的端點做 TLS 握手、只讀憑證公開資訊、輸出檔案；
  本系統只讀檔（relay_import 的 `cert` 來源），寫進**獨立的** cert_probe_observation（探測歷史）與 cert_probe_status（最後一次結果）。
  不寫進 cert_observation：人工／匯入觀測的唯一性（簽發者＋序號＋指紋＋到期）與作廢／更換作業都不受探測影響。探測清單由「匯出探測清單」產生。
· **探測值不覆蓋人工值**：到期卡片、狀態、風險仍只看人工登錄／匯入的觀測；探測結果另列「最後探測時間」，
  與人工到期日不同時只標「探測不一致」，由人決定要不要改。
· 本系統不寄送通知、不建立待辦、不保證即時探測——畫面頁首固定標示（前端），這裡不做任何提醒動作。

資料模型兩層（GPT 審查結論 1）：
· cert_item（受管項目）：內部 UUID 主鍵；自然唯一條件＝環境＋端點(endpoint/VIP)＋埠＋SNI 主機名＋掛載型態。
  **不用憑證名稱當鍵**——同名正／測、同 VIP 不同 SNI 都要能並存。
· cert_observation（觀測憑證）：一個項目可有多筆歷史；以 issuer DN＋serial＋SHA-256 指紋識別；
  來源 manual（人工）／import（調查表）。中繼探測的結果另存 cert_probe_observation，不在這張表、不進到期判斷。

時間（結論 2）：資料庫存 `not_after_utc`（UTC ISO，結尾 Z）；畫面以 UTC+8 顯示；
狀態以「距到期瞬間剩餘秒數」判斷，**不是本地日期相減**（到期當天 00:10 UTC 的憑證，00:11 UTC 就是已過期）。

隱私（結論 5）：窗口／Infra／驗測人員是可識別資訊。一般清單不含人名；詳情對非管理員遮罩（洪○）；
完整聯絡資訊與含人名匯出僅管理員，匯出另需用途註記並寫稽核。

讀取路徑不建表、不 ALTER、不寫庫（建表只在 db.init_db 與寫入入口；測試以 mode=ro＋query_only 釘住）。
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import io
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

TZ8 = timezone(timedelta(hours=8))
UTC = timezone.utc

SETTING_THRESHOLDS = "cert_risk_thresholds"
DEFAULT_THRESHOLDS = (45, 30, 14)

#: 卡片（互斥、加總＝受管且啟用的項目數）
STATUS_ORDER = ("expired", "d7", "d30", "d60", "ok", "none")
STATUS_LABEL = {
    "expired": "已過期", "d7": "7 天內", "d30": "8–30 天", "d60": "31–60 天", "ok": "正常（60 天以上）", "none": "無到期資料",
}
MOUNT_TYPES = ("F5", "IIS", "Nginx", "APP", "其他")      # 預設（內建）型態；管理員可再增加（存設定 cert_mount_types），內建不可刪
SETTING_MOUNT_TYPES = "cert_mount_types"
SETTING_IMPORT_LAST = "cert_import_last"
ENVIRONMENTS = ("正式", "測試")
#: 更換進度（使用者定：待排程／已排程／已更換／已驗測）。只存、不驅動任何自動動作。
REPLACE_STATUSES = ("待排程", "已排程", "已更換", "已驗測")
#: 停用原因（下拉）；另有選填文字，不寫入稽核紀錄
DEACT_REASONS = {"retired": "已汰換／不再使用", "duplicate": "重複登錄", "mistake": "誤建", "merged": "併入其他項目", "other": "其他"}
BATCH_MAX = 500
#: 批次只能改「更換相關欄位」，不含身分鍵欄位（環境／端點／埠／SNI／掛載型態）
BATCH_FIELDS = ("replace_status", "planned_date", "announce_needed", "infra_owner")
BATCH_LABEL = {"replace_status": "更換進度", "planned_date": "預排日期", "announce_needed": "需公告", "infra_owner": "執行人", "new_expiry": "新到期日"}
RISK_CODES = {"high": "高", "mid": "中", "low": "低", "expired": "已過期", "pending": "待補到期日", "none": ""}
BLANK = "_none"          # 篩選用：該欄位為空白
CONTACT_FIELDS = ("req_contact", "ap_contact", "infra_owner", "ap_tester")

#: 可由人手編輯／匯入更新的非鍵欄位
DATA_FIELDS = ("name", "host_ips", "f5_bigip", "f5_mounted", "os_type", "req_unit", *CONTACT_FIELDS,
               "planned_text", "planned_at_utc", "announce_needed", "replace_method", "replace_status", "note")
KEY_FIELDS = ("environment", "endpoint", "port", "sni", "mount_type")
TEXT_MAX = 500

SQL = [
    """CREATE TABLE IF NOT EXISTS cert_item (
        id TEXT PRIMARY KEY,
        seq INTEGER NOT NULL,                              -- 固定項次（建立順序，不因排序改變）
        name TEXT NOT NULL,                                -- 憑證名稱（顯示用，不是鍵）
        environment TEXT NOT NULL DEFAULT '',              -- 正式／測試／空白
        endpoint TEXT NOT NULL DEFAULT '',                 -- F5 VIP 或主機位址（小寫、第一個）
        port INTEGER NOT NULL DEFAULT 443,
        sni TEXT NOT NULL DEFAULT '',                      -- SNI 主機名（小寫）
        mount_type TEXT NOT NULL DEFAULT '',               -- F5／IIS／Nginx／APP／其他
        host_ips TEXT, f5_bigip TEXT, f5_mounted INTEGER, os_type TEXT,
        req_unit TEXT, req_contact TEXT, ap_contact TEXT, infra_owner TEXT, ap_tester TEXT,
        planned_text TEXT, planned_at_utc TEXT, announce_needed TEXT,
        replaced_pending INTEGER NOT NULL DEFAULT 0,       -- 1＝標記已更換時選了「稍後補登」，新到期日還沒登錄（舊觀測不再用來判斷到期）
        planned_ignored INTEGER NOT NULL DEFAULT 0,        -- 1＝不再採用匯入的預排文字當排程（清空預排日期、或按「忽略匯入值」）
        planned_date TEXT,                                 -- 預排日期（YYYY-MM-DD，UTC+8 日期；可空，可排序／篩選）
        replace_method TEXT NOT NULL DEFAULT 'manual',     -- 只存不驅動任何動作
        replace_status TEXT NOT NULL DEFAULT '',           -- 只存不驅動任何動作
        note TEXT,
        active INTEGER NOT NULL DEFAULT 1,                 -- 刪除＝停用（保留歷史）
        manual_fields TEXT NOT NULL DEFAULT '[]',          -- 人工改過的欄位；重匯不覆蓋
        source TEXT NOT NULL DEFAULT 'manual',             -- manual／import
        created_by TEXT, created_at TEXT NOT NULL, updated_by TEXT, updated_at TEXT,
        deactivated_at TEXT, deactivate_reason TEXT, deactivate_note TEXT,
        rev INTEGER NOT NULL DEFAULT 0)                      -- 每次寫入 +1；批次 digest／復原衝突比 rev，不比秒級時間""",
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_cert_item_key
        ON cert_item(environment, endpoint, port, sni, mount_type)""",
    """CREATE TABLE IF NOT EXISTS cert_observation (
        id TEXT PRIMARY KEY,
        item_id TEXT NOT NULL,
        issuer_dn TEXT NOT NULL DEFAULT '', serial TEXT NOT NULL DEFAULT '',
        fingerprint_sha256 TEXT NOT NULL DEFAULT '',
        subject_cn TEXT, san_json TEXT,
        not_before_utc TEXT, not_after_utc TEXT NOT NULL,
        source TEXT NOT NULL,                              -- manual／import（中繼探測另存 cert_probe_observation）
        observed_at_utc TEXT NOT NULL, note TEXT, created_by TEXT,
        voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT, voided_at TEXT, voided_by TEXT)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_cert_obs_ident
        ON cert_observation(item_id, issuer_dn, serial, fingerprint_sha256, not_after_utc)""",
    "CREATE INDEX IF NOT EXISTS ix_cert_obs_item ON cert_observation(item_id, observed_at_utc)",
    # 中繼探測（2.042.02）：每個項目最後一次探測的結果（成功或失敗都記；失敗沒有憑證可存成觀測）。只由 relay_import 的 cert 來源寫入。
    """CREATE TABLE IF NOT EXISTS cert_probe_status (
        item_id TEXT PRIMARY KEY, probed_at_utc TEXT NOT NULL, ok INTEGER NOT NULL, error TEXT,
        not_after_utc TEXT, fingerprint_sha256 TEXT, batch_id TEXT, updated_at TEXT)""",
    # 中繼探測的歷史（2.042.03）：跟人工觀測分開放——同一張憑證先被探測到，之後人工登錄同一組簽發者＋序號＋指紋＋到期時間也不會撞唯一鍵。
    """CREATE TABLE IF NOT EXISTS cert_probe_observation (
        id TEXT PRIMARY KEY, item_id TEXT NOT NULL,
        issuer_dn TEXT NOT NULL DEFAULT '', serial TEXT NOT NULL DEFAULT '', fingerprint_sha256 TEXT NOT NULL DEFAULT '',
        subject_cn TEXT, san_json TEXT, not_before_utc TEXT, not_after_utc TEXT NOT NULL,
        first_seen_utc TEXT NOT NULL, last_seen_utc TEXT NOT NULL, batch_id TEXT)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_cert_probe_obs ON cert_probe_observation(item_id, fingerprint_sha256, not_after_utc)""",
    # 批次更新紀錄（2026-11-01 第二批）：一批一列＋逐項前後值，供稽核與整批復原。人名（執行人）只存在逐項前後值，不進任何列表／稽核文字。
    """CREATE TABLE IF NOT EXISTS cert_batch (
        id TEXT PRIMARY KEY, created_by TEXT, created_at TEXT NOT NULL,
        item_count INTEGER NOT NULL, fields_json TEXT NOT NULL, summary_json TEXT NOT NULL DEFAULT '{}',
        undone_at TEXT, undone_by TEXT, undone_count INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS cert_batch_item (
        batch_id TEXT NOT NULL, item_id TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
        after_updated_at TEXT, after_rev INTEGER, undone INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (batch_id, item_id))""",
]

#: 第一批上線的庫要補的欄位（CREATE TABLE IF NOT EXISTS 不會幫舊表加欄）
_ADD_COLS = {
    "cert_batch_item": [("after_rev", "INTEGER")],
    "cert_item": [("rev", "INTEGER NOT NULL DEFAULT 0"), ("planned_ignored", "INTEGER NOT NULL DEFAULT 0"), ("replaced_pending", "INTEGER NOT NULL DEFAULT 0"), ("planned_date", "TEXT"), ("deactivated_at", "TEXT"), ("deactivate_reason", "TEXT"), ("deactivate_note", "TEXT")],
    "cert_observation": [("voided", "INTEGER NOT NULL DEFAULT 0"), ("void_reason", "TEXT"), ("voided_at", "TEXT"), ("voided_by", "TEXT")],
}


def _has_col(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))
    except sqlite3.Error:
        return False


_AUDIT = contextvars.ContextVar("cert_audit_hook", default=None)


@contextlib.contextmanager
def audit_scope(fn):
    """在這個範圍內，所有寫入函式會在 commit 之前呼叫 fn(conn, info) 寫稽核（同一個交易）：稽核寫失敗＝資料一併回滾，
    不會留下『資料已生效、沒有紀錄』。fn 不可自己 commit。"""
    tok = _AUDIT.set(fn)
    try:
        yield
    finally:
        _AUDIT.reset(tok)


def _commit(conn: sqlite3.Connection, info: dict | None = None) -> None:
    fn = _AUDIT.get()
    try:
        if fn is not None:
            fn(conn, info or {})
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def ensure_tables(conn: sqlite3.Connection) -> None:
    """只在 db.init_db 與寫入入口呼叫；讀取路徑絕不呼叫。"""
    for s in SQL:
        conn.execute(s)
    for table, cols in _ADD_COLS.items():
        for col, ddl in cols:
            if not _has_col(conn, table, col):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def tables_ready(conn: sqlite3.Connection) -> bool:
    try:
        n = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('cert_item','cert_observation')").fetchone()[0]
        return n == 2
    except sqlite3.Error:
        return False


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    try:
        u = dt.astimezone(UTC)
    except (OverflowError, ValueError) as exc:      # 0001-01-01、9999-12-31 帶時區轉 UTC 會溢位
        raise ValueError("時間超出可處理的範圍") from exc
    if not 2000 <= u.year <= 2100:
        raise ValueError("時間超出可處理的範圍（2000～2100 年）")
    return u.strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ 時間
def parse_utc(s: str | None) -> datetime | None:
    if not s:
        return None
    t = str(s).strip().replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


def to_utc_iso(value, *, assume=TZ8, date_end: bool = True) -> str:
    """把輸入轉成 UTC ISO。沒帶時區視為 UTC+8；只有日期者視為當日 23:59:59（date_end）。解析不了拋 ValueError。"""
    if isinstance(value, datetime):
        d = value
        if d.tzinfo is None:
            d = d.replace(tzinfo=assume)
        # Excel 日期儲存格讀進來是 00:00:00 的 datetime：視為只有日期，跟文字 "2026-11-30" 同一規則（當日 23:59:59）
        if date_end and (d.hour, d.minute, d.second, d.microsecond) == (0, 0, 0, 0):
            d = d.replace(hour=23, minute=59, second=59)
        return _iso(d)
    s = str(value or "").strip()
    if not s:
        raise ValueError("時間不可空白")
    s = s.replace("/", "-").replace("Z", "+00:00")
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", s):
        y, m, dd = (int(x) for x in s.split("-"))
        try:
            d = datetime(y, m, dd, 23, 59, 59 if date_end else 0, tzinfo=assume)
        except ValueError as exc:
            raise ValueError(f"日期不正確：{value}") from exc
        return _iso(d)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?(.*)", s)
    if not m:
        raise ValueError(f"時間格式看不懂：{value}（請用 2026-11-30 或 2026-11-30 23:59）")
    y, mo, dd, hh, mi, ss, tail = m.groups()
    tail = tail.strip()
    tz = assume
    if tail:
        tm = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", tail)
        if not tm:
            raise ValueError(f"時區格式看不懂：{tail}")
        sign = 1 if tm.group(1) == "+" else -1
        tz = timezone(sign * timedelta(hours=int(tm.group(2)), minutes=int(tm.group(3))))
    try:
        d = datetime(int(y), int(mo), int(dd), int(hh), int(mi), int(ss or 0), tzinfo=tz)
    except ValueError as exc:
        raise ValueError(f"時間不正確：{value}") from exc
    return _iso(d)


def fmt_local(utc_iso: str | None) -> str | None:
    """畫面顯示：UTC+8，明示時區。"""
    d = parse_utc(utc_iso)
    try:
        return d.astimezone(TZ8).strftime("%Y-%m-%d %H:%M (UTC+8)") if d else None
    except (OverflowError, ValueError):
        return None


def remaining_seconds(not_after_utc: str | None, now: datetime | None = None) -> float | None:
    d = parse_utc(not_after_utc)
    if d is None:
        return None
    return (d - (now or _now())).total_seconds()


def status_of(not_after_utc: str | None, now: datetime | None = None) -> str:
    """距到期『瞬間』的剩餘秒數決定狀態（不是本地日期相減）。到期那一刻起即已過期。"""
    rem = remaining_seconds(not_after_utc, now)
    if rem is None:
        return "none"
    if rem <= 0:
        return "expired"
    day = 86400
    if rem <= 7 * day:
        return "d7"
    if rem <= 30 * day:
        return "d30"
    if rem <= 60 * day:
        return "d60"
    return "ok"


def remain_text(not_after_utc: str | None, now: datetime | None = None) -> str:
    rem = remaining_seconds(not_after_utc, now)
    if rem is None:
        return "無到期資料"
    past = rem <= 0
    s = int(abs(rem))
    days, hours, minutes = s // 86400, (s % 86400) // 3600, (s % 3600) // 60
    if days:
        body = f"{days} 天 {hours} 小時" if hours else f"{days} 天"
    elif hours:
        body = f"{hours} 小時 {minutes} 分"
    else:
        body = f"{minutes} 分鐘" if minutes else "不到 1 分鐘"
    return f"已過期 {body}" if past else f"剩 {body}"


def risk_label(not_after_utc: str | None, thresholds, now: datetime | None = None) -> str:
    """到期風險等級（門檻可調；只標示，不通知）。回 ''／低／中／高／已過期。"""
    rem = remaining_seconds(not_after_utc, now)
    if rem is None:
        return ""
    if rem <= 0:
        return "已過期"
    t1, t2, t3 = sorted(thresholds, reverse=True)
    for limit, lab in ((t3, "高"), (t2, "中"), (t1, "低")):
        if rem <= limit * 86400:
            return lab
    return ""


# ------------------------------------------------------------------ 設定
def get_thresholds(conn: sqlite3.Connection) -> list[int]:
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (SETTING_THRESHOLDS,)).fetchone()
    except sqlite3.Error:
        row = None
    if row:
        try:
            vals = [int(x) for x in str(row[0]).split(",")]
            if len(vals) == 3 and all(1 <= v <= 3650 for v in vals) and len(set(vals)) == 3:
                return sorted(vals, reverse=True)
        except ValueError:
            pass
    return list(DEFAULT_THRESHOLDS)


def validate_thresholds(vals) -> list[int]:
    try:
        v = [int(x) for x in vals]
    except (TypeError, ValueError) as exc:
        raise ValueError("門檻必須是整數") from exc
    if len(v) != 3:
        raise ValueError("到期風險等級門檻需要 3 個數字（高／中／低）")
    if any(x < 1 or x > 3650 for x in v):
        raise ValueError("門檻範圍 1～3650 天")
    if len(set(v)) != 3:
        raise ValueError("3 個門檻不可重複")
    return sorted(v, reverse=True)


def save_thresholds(conn: sqlite3.Connection, vals, user: str | None) -> list[int]:
    v = validate_thresholds(vals)
    conn.execute("INSERT INTO app_settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (SETTING_THRESHOLDS, ",".join(str(x) for x in v)))
    _commit(conn, {"v": v})
    return v


# ------------------------------------------------------------------ 遮罩
_SEP = re.compile(r"[、,，/／;；\s]+")


def mask_person(v) -> str:
    """洪小明 → 洪○；多人以「、」分開；電話／email 類整個蓋掉。"""
    if v is None or str(v).strip() == "":
        return ""
    out = []
    for t in _SEP.split(str(v).strip()):
        if not t:
            continue
        if "@" in t or sum(c.isdigit() for c in t) >= 5:
            out.append("○○○")
        else:
            out.append(t[0] + "○")
    return "、".join(out)


# ------------------------------------------------------------------ 正規化
def _clean(v, n: int = TEXT_MAX) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, datetime):
        v = v.strftime("%Y-%m-%d %H:%M")
    return re.sub(r"[ \t\r\f\v]+", " ", str(v)).strip()[:n]


def norm_name(v) -> str:
    return re.sub(r"\s+", " ", _clean(v)).lower()


def norm_env(v) -> str:
    t = _clean(v).lower()
    if t in ("正", "正式", "prod", "production", "正式區", "正式環境"):
        return "正式"
    if t in ("測", "測試", "test", "uat", "測試區", "測試環境"):
        return "測試"
    return _clean(v, 20)


def norm_mount(v, names=None) -> str:
    """掛載型態正規化：常見寫法（f5/iis/nginx/app）歸到內建型態，其餘與已登記型態不分大小寫比對；
    都對不上就**原樣回傳**（不再默默歸「其他」）——手動輸入會被擋、匯入會在預覽提示『發現未登記的型態』。"""
    t = _clean(v)
    if not t:
        return ""
    low = t.lower()
    if "f5" in low:
        return "F5"
    if "iis" in low:
        return "IIS"
    if "nginx" in low:
        return "Nginx"
    if low in ("app", "ap", "應用程式"):
        return "APP"
    for n in (names if names is not None else MOUNT_TYPES):
        if n.lower() == low:
            return n
    return t


def first_endpoint(v) -> str:
    for t in re.split(r"[\s、,，;；/／]+", _clean(v)):
        if t:
            return t.lower()
    return ""


def derive_sni(name: str) -> str:
    t = _clean(name).lower()
    return t if "." in t and re.fullmatch(r"(\*\.)?[a-z0-9]([a-z0-9.\-]*[a-z0-9])?", t) else ""


def yes_no(v) -> int | None:
    t = _clean(v).lower()
    if t in ("是", "y", "yes", "true", "1", "有", "v", "o", "○"):
        return 1
    if t in ("否", "n", "no", "false", "0", "無", "x", "×"):
        return 0
    return None


def parse_planned(text: str) -> str | None:
    """預排更換時間能解析才存結構化；解析不了回 None（不報錯，原文照存）。需年月日，否則不猜年份。"""
    m = re.match(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T]+(\d{1,2}):(\d{2}))?", text or "")
    if not m:
        return None
    try:
        d = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4] or 0), int(m[5] or 0), tzinfo=TZ8)
        return _iso(d)
    except ValueError:        # 日期不存在或超出範圍：原文照存、不存結構化
        return None


def norm_date(v) -> str | None:
    """預排日期：'' → None；'2026-12-05'（也收 2026/12/5）→ '2026-12-05'；日期不存在或超出 2000～2100 拋 ValueError。"""
    t = _clean(v)
    if not t:
        return None
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", t)
    if not m:
        raise ValueError(f"預排日期格式看不懂：{v}（請用 2026-12-05）")
    try:
        d = datetime(int(m[1]), int(m[2]), int(m[3]))
    except ValueError as exc:
        raise ValueError(f"預排日期不存在：{v}") from exc
    if not 2000 <= d.year <= 2100:
        raise ValueError("預排日期超出可處理的範圍（2000～2100 年）")
    return d.strftime("%Y-%m-%d")


def key_of(f: dict) -> tuple:
    return (f["environment"], f["endpoint"], int(f["port"]), f["sni"], f["mount_type"])


def key_text(k: tuple) -> str:
    env, ep, port, sni, mt = k
    return f"{env or '(環境空白)'}｜{ep or '(無端點)'}:{port}｜SNI {sni or '(無)'}｜{mt or '(掛載未填)'}"


# ------------------------------------------------------------------ 設定：掛載型態（管理員可增減）
def _mount_cfg(conn: sqlite3.Connection) -> list[dict]:
    """存設定的型態清單 [{name, active}]。內建型態永遠在最前面且存在；讀取路徑只讀 app_settings，不建表。"""
    saved: list[dict] = []
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (SETTING_MOUNT_TYPES,)).fetchone()
    except sqlite3.Error:
        row = None
    if row:
        try:
            v = json.loads(row[0])
            if isinstance(v, list):
                saved = [x for x in v if isinstance(x, dict) and isinstance(x.get("name"), str) and x["name"].strip()]
        except ValueError:
            saved = []          # 設定值壞掉：退回預設清單（內建型態），不讓整頁失敗
    by = {x["name"]: bool(x.get("active", True)) for x in saved}
    out = [{"name": n, "active": by.get(n, True) if n != "其他" else True, "builtin": True} for n in MOUNT_TYPES]
    out += [{"name": x["name"], "active": bool(x.get("active", True)), "builtin": False} for x in saved if x["name"] not in MOUNT_TYPES]
    return out


def mount_types(conn: sqlite3.Connection) -> list[dict]:
    """型態清單＋各型態被幾個項目使用（含停用項目；使用中的不可刪）。"""
    cfg = _mount_cfg(conn)
    used: dict[str, int] = {}
    if tables_ready(conn):
        used = {r[0]: r[1] for r in conn.execute("SELECT mount_type, count(*) FROM cert_item GROUP BY mount_type")}
    return [{**c, "in_use": used.get(c["name"], 0)} for c in cfg]


def _save_mount_cfg(conn: sqlite3.Connection, cfg: list[dict]) -> None:
    val = json.dumps([{"name": c["name"], "active": bool(c["active"])} for c in cfg], ensure_ascii=False)
    conn.execute("INSERT INTO app_settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (SETTING_MOUNT_TYPES, val))


def add_mount_type(conn: sqlite3.Connection, name: str, user: str | None, commit: bool = True) -> str:
    n = _clean(name, 10000)
    if len(n) > 30:
        raise ValueError(f"型態名稱最多 30 個字（目前 {len(n)} 個）")
    if not n:
        raise ValueError("型態名稱不可空白")
    cfg = _mount_cfg(conn)
    hit = next((c for c in cfg if c["name"].lower() == n.lower()), None)
    if hit:
        if hit["active"]:
            raise KeyConflict(f"已有「{hit['name']}」這個型態")
        hit["active"] = True               # 曾停用過：重新啟用
        n = hit["name"]
    else:
        cfg.append({"name": n, "active": True, "builtin": False})
    _save_mount_cfg(conn, cfg)
    if commit:
        _commit(conn, {"name": n})
    return n


def set_mount_active(conn: sqlite3.Connection, name: str, active: bool, user: str | None) -> None:
    cfg = _mount_cfg(conn)
    hit = next((c for c in cfg if c["name"] == name), None)
    if hit is None:
        raise LookupError("找不到這個型態")
    if name == "其他" and not active:
        raise ValueError("「其他」是保底型態，不能停用")
    hit["active"] = bool(active)
    _save_mount_cfg(conn, cfg)
    _commit(conn, {"name": name, "active": bool(active)})


def remove_mount_type(conn: sqlite3.Connection, name: str, user: str | None) -> None:
    cfg = _mount_cfg(conn)
    hit = next((c for c in cfg if c["name"] == name), None)
    if hit is None:
        raise LookupError("找不到這個型態")
    if hit["builtin"]:
        raise ValueError("內建型態不能刪除（可以停用）")
    used = conn.execute("SELECT count(*) FROM cert_item WHERE mount_type=?", (name,)).fetchone()[0] if tables_ready(conn) else 0
    if used:
        raise ValueError(f"「{name}」被 {used} 個項目使用中，不能刪除；可以改成停用")
    _save_mount_cfg(conn, [c for c in cfg if c["name"] != name])
    _commit(conn, {"name": name})


def _check_announce(value: str, current: str | None) -> None:
    """需公告只收 是／否／空白；匯入進來的舊自由文字原樣保留，沒改它就放行。"""
    if value in ("", "是", "否") or (current is not None and value == current):
        return
    raise ValueError("「需公告」只能選 是／否（或留空）")


def check_mount_allowed(conn: sqlite3.Connection, mount: str, current: str | None = None) -> None:
    """手動新增／編輯時，掛載型態必須是『已登記且啟用』（沒改動的舊值除外）。空白放行。"""
    if not mount or (current is not None and mount == current):
        return
    cfg = _mount_cfg(conn)
    if any(c["name"] == mount and c["active"] for c in cfg):
        return
    if any(c["name"] == mount for c in cfg):
        raise ValueError(f"掛載型態「{mount}」已停用，不能選用；請管理員到「設定」重新啟用，或改選其他型態")
    raise ValueError(f"掛載型態「{mount}」尚未登記；請管理員到「設定」新增這個型態")


# ------------------------------------------------------------------ 讀取
def _next_seq(conn) -> int:
    return (conn.execute("SELECT COALESCE(MAX(seq),0) FROM cert_item").fetchone()[0] or 0) + 1


def _list_sql(conn: sqlite3.Connection) -> str:
    # 作廢的觀測不計入到期判斷；舊庫（升級前、還沒補 voided 欄）退回不篩，讓 verify_numbers 之類的唯讀檢查不因欄位不存在而失敗
    v = " AND voided=0" if _has_col(conn, "cert_observation", "voided") else ""
    # 中繼探測（probe）的觀測不進到期判斷：探測值不覆蓋人工值，只另外比對（_row_view 的 probe_*）
    if _probe_ready(conn):
        psel = (", ps.probed_at_utc AS probe_at_utc, ps.ok AS probe_ok, ps.error AS probe_error, ps.not_after_utc AS probe_not_after_utc")
        pjoin = "\nLEFT JOIN cert_probe_status ps ON ps.item_id = i.id"
    else:
        psel, pjoin = "", ""
    return f"""
SELECT i.*, o.id AS obs_id, o.not_after_utc AS not_after_utc, o.issuer_dn AS issuer_dn, o.serial AS obs_serial,
       o.source AS obs_source, o.observed_at_utc AS observed_at_utc{psel}
FROM cert_item i
LEFT JOIN cert_observation o ON o.id = (
    SELECT id FROM cert_observation WHERE item_id = i.id AND source <> 'probe'{v} ORDER BY observed_at_utc DESC, rowid DESC LIMIT 1){pjoin}
ORDER BY i.seq"""


def _probe_ready(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cert_probe_status'").fetchone() is not None
    except sqlite3.Error:
        return False


PROBE_DIFF_LABEL = {"same": "一致", "differs": "探測不一致", "probe_only": "僅探測有值", "failed": "探測失敗", "": "未探測"}


def probe_compare(manual_utc: str | None, probe_utc: str | None, ok, probed: bool) -> str:
    """人工到期日 vs 探測到期日。以 UTC+8 日期比（人工常只填日期）；不同就標 differs，不改任何值。"""
    if not probed:
        return ""
    if not ok:
        return "failed"
    if not probe_utc:
        return "failed"
    if not manual_utc:
        return "probe_only"
    return "same" if _local_date(manual_utc) == _local_date(probe_utc) else "differs"


def _g(r, k, default=None):
    try:
        return r[k]
    except (IndexError, KeyError):
        return default


def _planned_eff(r) -> str:
    """有效的預排日期：人工填的預排日期優先，否則取『預排更換時間』文字能解析出的日期。"""
    pd = _g(r, "planned_date")
    if pd:
        return pd
    if _g(r, "planned_ignored"):            # 清空過預排日期／選了「忽略匯入值」：不再退回採用匯入原文字
        return ""
    d = parse_utc(_g(r, "planned_at_utc"))
    try:
        return d.astimezone(TZ8).strftime("%Y-%m-%d") if d else ""
    except (OverflowError, ValueError):
        return ""


def _row_view(r: sqlite3.Row, thresholds, now: datetime) -> dict:
    pend = bool(_g(r, "replaced_pending")) and (r["replace_status"] or "") == "已更換"
    # 已更換且「稍後補登」：舊到期日已經不是這張憑證的到期日，不能再拿它判斷風險；算「無到期資料」（待補清單），風險等級顯示「待補到期日」
    na = None if pend else r["not_after_utc"]
    st = status_of(na, now)
    rem = remaining_seconds(na, now)
    risk = "待補到期日" if pend else risk_label(na, thresholds, now)
    upd = max([x for x in (_g(r, "updated_at"), r["created_at"], r["observed_at_utc"]) if x] or [""])
    return {
        "id": r["id"], "seq": r["seq"], "name": r["name"], "environment": r["environment"], "endpoint": r["endpoint"],
        "port": r["port"], "sni": r["sni"], "mount_type": r["mount_type"], "req_unit": r["req_unit"] or "",
        "f5_bigip": r["f5_bigip"] or "", "host_ips": r["host_ips"] or "",
        "planned_text": r["planned_text"] or "", "planned_date": _planned_eff(r), "announce_needed": r["announce_needed"] or "",
        "replace_method": r["replace_method"], "replace_status": r["replace_status"] or "",
        "active": bool(r["active"]), "status": st, "status_label": "待補新到期日" if pend else STATUS_LABEL[st], "pending_expiry": pend,
        "not_after_utc": na, "not_after_local": fmt_local(na),
        "remain_text": "待補新到期日" if pend else remain_text(na, now),
        "remain_hours": None if rem is None else round(rem / 3600, 1),
        "risk": risk, "risk_code": next((k for k, v in RISK_CODES.items() if v == risk and k != "none"), "none"),
        "issuer_dn": r["issuer_dn"] or "", "obs_source": r["obs_source"] or "", "observed_at_utc": r["observed_at_utc"],
        "source": r["source"] or "manual", "source_label": "匯入" if r["source"] == "import" else "手動",
        "data_updated": upd, "data_updated_local": fmt_local(upd),
        "deactivate_reason": _g(r, "deactivate_reason") or "", "deactivate_note": _g(r, "deactivate_note") or "",
        "deactivate_reason_label": DEACT_REASONS.get(_g(r, "deactivate_reason") or "", ""),
        "deactivated_at": _g(r, "deactivated_at"),
        **_probe_view(r, na),
    }


def _probe_view(r, manual_na: str | None) -> dict:
    at = _g(r, "probe_at_utc")
    pna = _g(r, "probe_not_after_utc")
    code = probe_compare(manual_na, pna, _g(r, "probe_ok"), bool(at))
    return {"probe_at_utc": at, "probe_at_local": fmt_local(at), "probe_ok": None if not at else bool(_g(r, "probe_ok")),
            "probe_error": _g(r, "probe_error") or "", "probe_not_after_utc": pna, "probe_not_after_local": fmt_local(pna),
            "probe_diff": code, "probe_diff_label": PROBE_DIFF_LABEL[code], "probe_differs": code == "differs"}


def all_rows(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    """唯一的計算來源：卡片、清單、下鑽都吃這一份（加總＝下鑽）。"""
    if not tables_ready(conn):
        return []
    now = now or _now()
    th = get_thresholds(conn)
    return [_row_view(r, th, now) for r in conn.execute(_list_sql(conn))]


def import_info(conn: sqlite3.Connection) -> dict:
    """畫面顯示『資料來源、最後匯入時間』用。"""
    info = {"import_items": 0, "manual_items": 0, "last_import_at": None, "last_import_local": None, "last_import_file": None}
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (SETTING_IMPORT_LAST,)).fetchone()
    except sqlite3.Error:
        row = None
    if row:
        try:
            v = json.loads(row[0])
            info.update(last_import_at=v.get("at"), last_import_local=fmt_local(v.get("at")), last_import_file=v.get("file"))
        except (ValueError, AttributeError):
            info["last_import_at"] = None      # 設定值壞掉：當作沒有匯入紀錄
    if tables_ready(conn):
        for src, n in conn.execute("SELECT source, count(*) FROM cert_item WHERE active=1 GROUP BY source"):
            info["import_items" if src == "import" else "manual_items"] += n
    return info


def summary(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    now = now or _now()
    rows = all_rows(conn, now)
    act = [r for r in rows if r["active"]]
    cards = [{"key": k, "label": STATUS_LABEL[k], "count": sum(1 for r in act if r["status"] == k)} for k in STATUS_ORDER]
    return {"cards": cards, "total_active": len(act), "inactive": len(rows) - len(act),
            "thresholds": get_thresholds(conn) if tables_ready(conn) else list(DEFAULT_THRESHOLDS),
            "now_utc": _iso(now), "ready": tables_ready(conn), "import_info": import_info(conn)}


def options(conn: sqlite3.Connection) -> dict:
    """篩選／表單下拉用的選項（掛載型態來自設定、申請單位來自現有資料）。"""
    units = sorted({r["req_unit"] for r in all_rows(conn) if r["req_unit"]})
    return {"mount_types": mount_types(conn), "replace_statuses": list(REPLACE_STATUSES), "environments": list(ENVIRONMENTS),
            "deactivate_reasons": [{"code": k, "label": v} for k, v in DEACT_REASONS.items()], "units": units,
            "batch_max": BATCH_MAX, "batch_fields": [{"field": k, "label": BATCH_LABEL[k]} for k in BATCH_FIELDS],
            "search_fields": list(SEARCH_FIELDS)}


# ------------------------------------------------------------------ 條件過濾（可組合；清單、卡片下鑽、批次『全選目前篩選結果』、匯出都走這一份）
FILTER_KEYS = ("pending", "q", "status", "env", "mount", "unit", "risk", "within", "due_from", "due_to", "rs", "announce", "has_expiry",
               "active", "plan_from", "plan_to", "has_plan")
#: 搜尋框支援的欄位（畫面提示文字照這份寫，改這裡畫面跟著變）
SEARCH_FIELDS = ("憑證名稱", "端點", "SNI", "申請單位", "F5 BIG-IP", "主機 IP", "SAN", "序號")


def _date_arg(v, label: str) -> str | None:
    if v in (None, ""):
        return None
    try:
        return norm_date(v)
    except ValueError as exc:
        raise ValueError(f"{label}：{exc}") from exc


def normalize_filters(raw: dict | None) -> dict:
    """把網址／API 參數整理成乾淨的篩選條件；看不懂的值拋 ValueError（不默默當成沒篩）。"""
    raw = {k: v for k, v in (raw or {}).items() if k in FILTER_KEYS and v not in (None, "")}
    f: dict = {}
    if "q" in raw and str(raw["q"]).strip():
        f["q"] = str(raw["q"]).strip()
    if "status" in raw:
        if raw["status"] not in STATUS_ORDER:
            raise ValueError("不認得的狀態")
        f["status"] = raw["status"]
    for k in ("env", "mount", "unit"):
        if k in raw:
            f[k] = _clean(raw[k])
    if "risk" in raw:
        if raw["risk"] not in RISK_CODES:
            raise ValueError("不認得的風險等級")
        f["risk"] = raw["risk"]
    if "within" in raw:
        try:
            n = int(raw["within"])
        except (TypeError, ValueError) as exc:
            raise ValueError("「N 天內」必須是整數") from exc
        if not 1 <= n <= 3650:
            raise ValueError("「N 天內」範圍 1～3650")
        f["within"] = n
    for k, lab in (("due_from", "到期日起"), ("due_to", "到期日迄"), ("plan_from", "預排日起"), ("plan_to", "預排日迄")):
        if k in raw:
            f[k] = _date_arg(raw[k], lab)
    if f.get("due_from") and f.get("due_to") and f["due_from"] > f["due_to"]:
        raise ValueError("到期日區間的起日不能晚於迄日")
    if f.get("plan_from") and f.get("plan_to") and f["plan_from"] > f["plan_to"]:
        raise ValueError("預排日區間的起日不能晚於迄日")
    if "rs" in raw:
        if raw["rs"] != BLANK and raw["rs"] not in REPLACE_STATUSES:
            raise ValueError("不認得的更換進度")
        f["rs"] = raw["rs"]
    if "announce" in raw:
        if raw["announce"] not in ("yes", "no", "blank"):
            raise ValueError("「需公告」只能是 yes／no／blank")
        f["announce"] = raw["announce"]
    for k in ("has_expiry", "has_plan", "pending"):
        if k in raw:
            if str(raw[k]) not in ("1", "0"):
                raise ValueError(f"{k} 只能是 1 或 0")
            f[k] = str(raw[k])
    f["active"] = raw.get("active", "on")
    if f["active"] not in ("on", "off", "all"):
        raise ValueError("「是否停用」只能是 on／off／all")
    return f


def _local_date(utc_iso: str | None) -> str:
    d = parse_utc(utc_iso)
    try:
        return d.astimezone(TZ8).strftime("%Y-%m-%d") if d else ""
    except (OverflowError, ValueError):
        return ""


def _search_extra(conn: sqlite3.Connection) -> dict[str, str]:
    """搜尋才用得到：每個項目的 序號／SAN／主體 CN（含歷史觀測，作廢的不算）。"""
    v = " WHERE voided=0" if _has_col(conn, "cert_observation", "voided") else ""
    out: dict[str, list[str]] = {}
    for r in conn.execute(f"SELECT item_id, serial, subject_cn, san_json FROM cert_observation{v}"):
        out.setdefault(r[0], []).extend([r[1] or "", r[2] or "", " ".join(_san_list(r[3]))])
    return {k: " ".join(x for x in v if x) for k, v in out.items()}


def apply_filters(conn: sqlite3.Connection, rows: list[dict], f: dict) -> list[dict]:
    out = rows
    act = f.get("active", "on")
    if act == "on":
        out = [r for r in out if r["active"]]
    elif act == "off":
        out = [r for r in out if not r["active"]]
    if "status" in f:
        out = [r for r in out if r["status"] == f["status"]]
    for key, col in (("env", "environment"), ("mount", "mount_type"), ("unit", "req_unit"), ("rs", "replace_status")):
        if key in f:
            want = "" if f[key] == BLANK else f[key]
            out = [r for r in out if (r[col] or "") == want]
    if "risk" in f:
        out = [r for r in out if r["risk_code"] == f["risk"]]
    if "within" in f:                       # N 天內到期：尚未過期且剩餘 ≤ N 天（已過期的不算）
        out = [r for r in out if r["remain_hours"] is not None and 0 < r["remain_hours"] <= f["within"] * 24]
    if "due_from" in f or "due_to" in f:    # 到期日區間：以 UTC+8 日期、含起迄兩天
        lo, hi = f.get("due_from"), f.get("due_to")
        out = [r for r in out if r["not_after_utc"] and (not lo or _local_date(r["not_after_utc"]) >= lo)
               and (not hi or _local_date(r["not_after_utc"]) <= hi)]
    if "plan_from" in f or "plan_to" in f:
        lo, hi = f.get("plan_from"), f.get("plan_to")
        out = [r for r in out if r["planned_date"] and (not lo or r["planned_date"] >= lo) and (not hi or r["planned_date"] <= hi)]
    if "announce" in f:
        def _ann(r):
            y = yes_no(r["announce_needed"])
            return "yes" if y == 1 else "no" if y == 0 else "blank"
        out = [r for r in out if _ann(r) == f["announce"]]
    if "has_expiry" in f:
        out = [r for r in out if bool(r["not_after_utc"]) == (f["has_expiry"] == "1")]
    if "has_plan" in f:
        out = [r for r in out if bool(r["planned_date"]) == (f["has_plan"] == "1")]
    if "pending" in f:                      # 已更換、但選了「稍後補登」而新到期日還沒登錄
        out = [r for r in out if r["pending_expiry"] == (f["pending"] == "1")]
    if "q" in f:
        needle = f["q"].lower()
        extra = _search_extra(conn) if tables_ready(conn) else {}
        bare = re.sub(r"[:\s]", "", needle)           # 憑證視窗複製出來的序號／指紋常帶冒號；庫裡存的是去冒號的，兩種寫法都要找得到

        def _hit(r) -> bool:
            ex = extra.get(r["id"], "").lower()
            hay = " ".join(str(x) for x in (r["name"], r["endpoint"], r["sni"], r["req_unit"], r["f5_bigip"], r["host_ips"],
                                            r["mount_type"], r["environment"], ex)).lower()
            return needle in hay or (":" in needle and bool(bare) and bare in ex)
        out = [r for r in out if _hit(r)]
    return out


def list_items(conn: sqlite3.Connection, status: str | None = None, q: str | None = None,
               include_inactive: bool = False, now: datetime | None = None, **filters) -> list[dict]:
    """舊介面（verify_numbers、卡片下鑽共用）：回傳符合的列清單。新增的條件用關鍵字參數傳入（見 FILTER_KEYS）。"""
    act = "all" if include_inactive else filters.pop("active", "on")
    return apply_filters(conn, all_rows(conn, now), normalize_filters({"status": status, "q": q, "active": act, **filters}))


def _rank(r: dict) -> int:
    return STATUS_ORDER.index(r["status"])


SORTERS = {
    "__no": lambda r: r["seq"], "seq": lambda r: r["seq"], "name": lambda r: r["name"].lower(),
    "environment": lambda r: r["environment"], "ep": lambda r: (r["endpoint"], r["port"]), "sni": lambda r: r["sni"],
    "mount_type": lambda r: r["mount_type"], "req_unit": lambda r: r["req_unit"],
    "not_after_local": lambda r: r["not_after_utc"] or None, "not_after_utc": lambda r: r["not_after_utc"] or None,
    "remain_hours": lambda r: r["remain_hours"], "risk": lambda r: ["", "低", "中", "高", "已過期", "待補到期日"].index(r["risk"]),
    "status_rank": _rank, "planned_date": lambda r: r["planned_date"] or None, "announce_needed": lambda r: r["announce_needed"],
    "replace_status": lambda r: (REPLACE_STATUSES.index(r["replace_status"]) if r["replace_status"] in REPLACE_STATUSES else -1),
    "data_updated": lambda r: r["data_updated"] or None, "src": lambda r: r["source"], "act": lambda r: not r["active"],
    "deact": lambda r: r["deactivate_reason"],
    "probe_at": lambda r: r["probe_at_utc"] or None,
    "probe_diff": lambda r: ["differs", "failed", "probe_only", "same"].index(r["probe_diff"]) if r["probe_diff"] else None,
}


def query_items(conn: sqlite3.Connection, raw_filters: dict | None = None, sort: str = "seq", desc: bool = False,
                page: int = 1, size: int = 100, now: datetime | None = None) -> dict:
    """伺服器端篩選＋排序＋分頁（size=0＝全部，匯出用）。total＝同一個『是否停用』範圍內的總筆數、matched＝符合篩選的筆數。"""
    f = normalize_filters(raw_filters)
    if sort not in SORTERS:
        raise ValueError("不認得的排序欄位")
    rows = all_rows(conn, now)
    scope = apply_filters(conn, rows, {"active": f["active"]})
    hit = apply_filters(conn, rows, f)
    key = SORTERS[sort]
    full = [r for r in hit if key(r) is not None]
    nulls = [r for r in hit if key(r) is None]          # 沒有值的永遠排最後，不論升降
    full.sort(key=lambda r: (key(r), r["seq"]), reverse=desc)
    hit = full + sorted(nulls, key=lambda r: r["seq"])
    if size and size > 0:
        page = max(1, int(page or 1))
        hit_page = hit[(page - 1) * size: page * size]
    else:
        page, hit_page = 1, hit
    return {"items": hit_page, "total": len(scope), "matched": len(hit), "page": page, "size": size}


def _san_list(v) -> list[str]:
    try:
        return json.loads(v) if v else []
    except ValueError:
        # SAN 欄位存的 JSON 壞掉：此欄只是顯示用的輔助資訊（到期判斷不靠它），壞值當作沒有 SAN，不讓整個詳情頁失敗
        return []


def item_detail(conn: sqlite3.Connection, item_id: str, full_contacts: bool, now: datetime | None = None) -> dict:
    if not tables_ready(conn):
        raise LookupError("找不到這筆憑證項目")
    r = conn.execute("SELECT * FROM cert_item WHERE id=?", (item_id,)).fetchone()
    if r is None:
        raise LookupError("找不到這筆憑證項目")
    now = now or _now()
    th = get_thresholds(conn)
    view = next((x for x in all_rows(conn, now) if x["id"] == item_id), None) or {}
    d = {k: r[k] for k in r.keys() if k not in ("manual_fields",)}
    d["manual_fields"] = json.loads(r["manual_fields"] or "[]")
    for f in CONTACT_FIELDS:
        d[f] = r[f] or "" if full_contacts else mask_person(r[f])
    d["contacts_masked"] = not full_contacts
    if not full_contacts:                    # 建立者／更新者是帳號名稱，一併不給非管理員
        d.pop("created_by", None)
        d.pop("updated_by", None)
    d["active"] = bool(r["active"])
    d["planned_local"] = fmt_local(r["planned_at_utc"])
    d["planned_date"] = _g(r, "planned_date") or ""
    d["planned_ignored"] = bool(_g(r, "planned_ignored"))
    d["planned_text_date"] = None if d["planned_ignored"] else (_local_date(r["planned_at_utc"]) if (r["planned_text"] and not d["planned_date"]) else "")
    d["deactivate_reason_label"] = DEACT_REASONS.get(_g(r, "deactivate_reason") or "", "")
    d["has_deactivate_note"] = bool(d.get("deactivate_note"))
    if not full_contacts:                    # 停用說明、作廢原因是自由文字、可能含人名：跟人名欄同待遇，只有管理員看得到
        d["deactivate_note"] = ""
    d["deactivated_local"] = fmt_local(_g(r, "deactivated_at"))
    d["source_label"] = "匯入" if r["source"] == "import" else "手動"
    d["key_text"] = key_text(key_of(dict(r)))
    if not full_contacts and view:
        view = {**view, "deactivate_note": ""}
    d["view"] = view
    d["thresholds"] = th
    obs = []
    for o in conn.execute("SELECT * FROM cert_observation WHERE item_id=? ORDER BY observed_at_utc DESC, rowid DESC", (item_id,)):
        voided = bool(_g(o, "voided", 0))
        obs.append({"id": o["id"], "issuer_dn": o["issuer_dn"], "serial": o["serial"], "fingerprint_sha256": o["fingerprint_sha256"],
                    "subject_cn": o["subject_cn"] or "", "san": _san_list(o["san_json"]),
                    "not_before_utc": o["not_before_utc"], "not_before_local": fmt_local(o["not_before_utc"]),
                    "not_after_utc": o["not_after_utc"], "not_after_local": fmt_local(o["not_after_utc"]),
                    "status_label": "已作廢" if voided else STATUS_LABEL[status_of(o["not_after_utc"], now)],
                    "source": o["source"], "observed_at_utc": o["observed_at_utc"],
                    "observed_local": fmt_local(o["observed_at_utc"]), "note": o["note"] or "",
                    "voided": voided, "void_reason": (_g(o, "void_reason") or "") if full_contacts else "", "has_void_reason": bool(_g(o, "void_reason")), "voided_local": fmt_local(_g(o, "voided_at"))})
        if full_contacts:
            obs[-1]["created_by"] = o["created_by"]
            obs[-1]["voided_by"] = _g(o, "voided_by")
    d["observations"] = obs
    d["probe_observations"] = probe_history(conn, item_id)
    return d


def probe_history(conn: sqlite3.Connection, item_id: str) -> list[dict]:
    """中繼探測看到過的憑證（每張一列：第一次／最後一次看到）。只讀；表不存在＝從沒探測過。"""
    if not _probe_ready(conn) or conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cert_probe_observation'").fetchone() is None:
        return []
    out = []
    for o in conn.execute("SELECT * FROM cert_probe_observation WHERE item_id=? ORDER BY last_seen_utc DESC, rowid DESC", (item_id,)):
        out.append({"id": o["id"], "issuer_dn": o["issuer_dn"], "serial": o["serial"], "fingerprint_sha256": o["fingerprint_sha256"],
                    "subject_cn": o["subject_cn"] or "", "san": _san_list(o["san_json"]),
                    "not_after_utc": o["not_after_utc"], "not_after_local": fmt_local(o["not_after_utc"]),
                    "first_seen_local": fmt_local(o["first_seen_utc"]), "last_seen_utc": o["last_seen_utc"],
                    "last_seen_local": fmt_local(o["last_seen_utc"]), "batch_id": o["batch_id"] or ""})
    return out


# ------------------------------------------------------------------ 手動寫入
def _norm_input(body: dict, mount_names=None) -> dict:
    """手動新增／編輯的欄位清理（鍵欄位一律正規化後才比對唯一）。"""
    out: dict = {}
    if "name" in body:
        out["name"] = _clean(body["name"])
    if "environment" in body:
        out["environment"] = norm_env(body["environment"])
    if "endpoint" in body:
        out["endpoint"] = first_endpoint(body["endpoint"])
    if "port" in body:
        try:
            p = int(body["port"] or 443)
        except (TypeError, ValueError) as exc:
            raise ValueError("埠號必須是整數") from exc
        if not 1 <= p <= 65535:
            raise ValueError("埠號範圍 1～65535")
        out["port"] = p
    if "sni" in body:
        out["sni"] = _clean(body["sni"]).lower()
    if "mount_type" in body:
        out["mount_type"] = norm_mount(body["mount_type"], mount_names)
    for f in ("host_ips", "f5_bigip", "os_type", "req_unit", *CONTACT_FIELDS, "announce_needed", "note"):
        if f in body:
            out[f] = _clean(body[f])
    if "f5_mounted" in body:
        v = body["f5_mounted"]
        out["f5_mounted"] = None if v in (None, "") else (int(v) if isinstance(v, (bool, int)) else yes_no(v))
    if "replace_status" in body:
        rs = _clean(body["replace_status"])
        if rs and rs not in REPLACE_STATUSES:
            raise ValueError(f"更換進度只能是：{'、'.join(REPLACE_STATUSES)}（或留空）")
        out["replace_status"] = rs
    if "planned_date" in body:
        out["planned_date"] = norm_date(body["planned_date"])
    if "replace_method" in body:
        out["replace_method"] = _clean(body["replace_method"]) or "manual"
    if "planned_text" in body:
        out["planned_text"] = _clean(body["planned_text"])
        out["planned_at_utc"] = parse_planned(out["planned_text"])
    return out


def _same(a, b) -> bool:
    """None 與 '' 視為相同（舊資料欄位是 NULL，表單送回來是 ''）；0 與 None 不同（f5_mounted）。"""
    if a in (None, "") and b in (None, ""):
        return True
    return a == b


class KeyConflict(Exception):
    pass


def _find_by_key(conn, k: tuple, exclude_id: str | None = None):
    r = conn.execute("SELECT id, name, active FROM cert_item WHERE environment=? AND endpoint=? AND port=? AND sni=? AND mount_type=?", k).fetchone()
    if r is not None and r["id"] != exclude_id:
        return r
    return None


def create_item(conn: sqlite3.Connection, body: dict, user: str | None, is_admin: bool) -> dict:
    ensure_tables(conn)
    f = _norm_input(body, [m["name"] for m in _mount_cfg(conn)])
    if not f.get("name"):
        raise ValueError("憑證名稱必填")
    check_mount_allowed(conn, f.get("mount_type", ""))
    _check_announce(f.get("announce_needed", ""), None)
    if f.get("environment") not in ENVIRONMENTS:       # 最低識別資料：名稱＋環境＋（端點或 SNI）——同名的正式／測試是不同項目，沒有這幾樣無法判斷是不是重複
        raise ValueError("環境必填（正式／測試）：同名的正式與測試憑證是不同項目")
    if not f.get("endpoint") and not f.get("sni"):
        raise ValueError("端點（F5 VIP 或主機位址）與 SNI 至少要填一個，否則無法辨識是哪一張憑證")
    if not is_admin:                      # 非管理員看不到完整人名，也不能寫（避免遮罩值被存回去）
        for c in CONTACT_FIELDS:
            f.pop(c, None)
    for k, dv in (("environment", ""), ("endpoint", ""), ("port", 443), ("sni", ""), ("mount_type", "")):
        f.setdefault(k, dv)
    hit = _find_by_key(conn, key_of(f))
    if hit:
        raise KeyConflict(f"已有相同的項目：環境、端點、埠、SNI、掛載型態這五欄都相同（{key_text(key_of(f))}）：{hit['name']}" + ("（已停用，請在清單重新啟用）" if not hit["active"] else ""))
    iid = uuid.uuid4().hex
    cols = ["id", "seq", "source", "created_by", "created_at", "manual_fields", *f.keys()]
    vals = [iid, _next_seq(conn), "manual", user, _iso(_now()), "[]", *f.values()]
    try:
        conn.execute(f"INSERT INTO cert_item ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
    except sqlite3.IntegrityError as exc:           # 兩人同時新增同一把鍵：事前檢查之後才被別人搶先
        conn.rollback()
        raise KeyConflict(f"已有相同的項目（{key_text(key_of(f))}）") from exc
    _commit(conn, {"id": iid})
    return {"id": iid}


def update_item(conn: sqlite3.Connection, item_id: str, body: dict, user: str | None, is_admin: bool) -> dict:
    ensure_tables(conn)
    r = conn.execute("SELECT * FROM cert_item WHERE id=?", (item_id,)).fetchone()
    if r is None:
        raise LookupError("找不到這筆憑證項目")
    f = _norm_input(body, [m["name"] for m in _mount_cfg(conn)])
    if not is_admin:
        for c in CONTACT_FIELDS:
            f.pop(c, None)
    if "name" in f and not f["name"]:
        raise ValueError("憑證名稱不可空白")
    cur = dict(r)
    if "announce_needed" in f:
        _check_announce(f["announce_needed"], cur.get("announce_needed"))
    if "mount_type" in f:
        check_mount_allowed(conn, f["mount_type"], cur["mount_type"])
    changed = {k: v for k, v in f.items() if not _same(cur.get(k), v) and not (k == "planned_at_utc")}
    if "planned_text" in changed:
        changed["planned_at_utc"] = f["planned_at_utc"]
    if not changed:
        return {"id": item_id, "changed": []}
    merged = {**cur, **changed}
    if "replace_status" in changed or "planned_date" in changed:          # 只檢查這次動到的；舊資料本來就矛盾的不動
        msg = check_schedule(merged.get("replace_status"), merged.get("planned_date"))
        if msg:
            raise ValueError(msg)
    if "planned_date" in changed and changed["planned_date"] is None:      # 清空預排日期＝不再採用匯入原文字當排程
        changed["planned_ignored"] = 1
    if any(k in changed for k in KEY_FIELDS):
        if not merged.get("endpoint") and not merged.get("sni"):        # 比照新增：端點與 SNI 至少要有一個，否則無法辨識是哪一張憑證
            raise ValueError("端點（F5 VIP 或主機位址）與 SNI 至少要留一個，否則無法辨識是哪一張憑證")
        hit = _find_by_key(conn, key_of(merged), exclude_id=item_id)
        if hit:
            raise KeyConflict(f"改完會跟另一筆重複（{key_text(key_of(merged))}）：{hit['name']}")
    man = set(json.loads(r["manual_fields"] or "[]")) | {k for k in changed if k not in ("planned_at_utc", "planned_ignored")}
    sets = ", ".join(f"{k}=?" for k in changed) + ", manual_fields=?, updated_by=?, updated_at=?, rev=rev+1"
    try:
        conn.execute(f"UPDATE cert_item SET {sets} WHERE id=?",
                     [*changed.values(), json.dumps(sorted(man), ensure_ascii=False), user, _iso(_now()), item_id])
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise KeyConflict("改完會跟另一筆重複（同時被別人改了）") from exc
    out = {"id": item_id, "changed": sorted(k for k in changed if k not in ("planned_at_utc", "planned_ignored"))}
    _commit(conn, out)
    return out


def set_active(conn: sqlite3.Connection, item_id: str, active: bool, user: str | None,
               reason_code: str | None = None, reason_note: str | None = None) -> dict:
    """停用必須選原因（下拉）＋選填說明（不寫入稽核紀錄、請勿填人名），並記停用日期；重新啟用會清掉這些。"""
    ensure_tables(conn)
    now = _iso(_now())
    if active:
        cur = conn.execute("UPDATE cert_item SET active=1, deactivated_at=NULL, deactivate_reason=NULL, deactivate_note=NULL, updated_by=?, updated_at=?, rev=rev+1 WHERE id=?",
                           (user, now, item_id))
    else:
        if reason_code not in DEACT_REASONS:
            raise ValueError("請選停用原因")
        cur = conn.execute("UPDATE cert_item SET active=0, deactivated_at=?, deactivate_reason=?, deactivate_note=?, updated_by=?, updated_at=?, rev=rev+1 WHERE id=?",
                           (now, reason_code, _clean(reason_note, 200) or None, user, now, item_id))
    if cur.rowcount == 0:
        raise LookupError("找不到這筆憑證項目")
    _commit(conn, {"id": item_id, "active": active})
    return {"id": item_id, "active": active}


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def add_observation(conn: sqlite3.Connection, item_id: str, body: dict, user: str | None, source: str = "manual") -> dict:
    ensure_tables(conn)
    if conn.execute("SELECT 1 FROM cert_item WHERE id=?", (item_id,)).fetchone() is None:
        raise LookupError("找不到這筆憑證項目")
    na = to_utc_iso(body.get("not_after"))
    nb = to_utc_iso(body["not_before"], date_end=False) if _clean(body.get("not_before")) else None
    fp = re.sub(r"[\s:]", "", _clean(body.get("fingerprint_sha256"))).lower()
    if fp and not _HEX64.match(fp):
        raise ValueError("SHA-256 指紋必須是 64 個十六進位字元（可含冒號）")
    san = body.get("san") or []
    if isinstance(san, str):
        san = [x for x in re.split(r"[\s,，、;；]+", san) if x]
    san = [_clean(x, 253) for x in san][:200]
    observed = to_utc_iso(body["observed_at"]) if _clean(body.get("observed_at")) else _iso(_now())
    oid = uuid.uuid4().hex
    try:
        conn.execute(
            "INSERT INTO cert_observation (id,item_id,issuer_dn,serial,fingerprint_sha256,subject_cn,san_json,not_before_utc,not_after_utc,"
            "source,observed_at_utc,note,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, item_id, _clean(body.get("issuer_dn")), _clean(body.get("serial")).replace(":", "").upper(), fp,
             _clean(body.get("subject_cn")), json.dumps(san, ensure_ascii=False), nb, na, source, observed,
             _clean(body.get("note")), user))
    except sqlite3.IntegrityError as exc:
        raise KeyConflict("已有相同的觀測紀錄（簽發者＋序號＋指紋＋到期時間都一樣）") from exc
    conn.execute("UPDATE cert_item SET rev=rev+1, replaced_pending=0 WHERE id=?", (item_id,))      # 觀測變動也算寫入：批次復原才看得出來；補登了就不再待補
    _commit(conn, {"id": oid, "not_after_utc": na})
    return {"id": oid, "not_after_utc": na}


def void_observation(conn: sqlite3.Connection, item_id: str, obs_id: str, reason: str, user: str | None) -> dict:
    """作廢一筆觀測：保留紀錄、標記作廢、不計入到期判斷；必須寫原因；可還原。"""
    ensure_tables(conn)
    why = _clean(reason, 200)
    if not why:
        raise ValueError("作廢請寫原因")
    o = conn.execute("SELECT voided FROM cert_observation WHERE id=? AND item_id=?", (obs_id, item_id)).fetchone()
    if o is None:
        raise LookupError("找不到這筆觀測")
    if o["voided"]:
        raise ValueError("這筆觀測已經是作廢狀態")
    conn.execute("UPDATE cert_observation SET voided=1, void_reason=?, voided_at=?, voided_by=? WHERE id=?", (why, _iso(_now()), user, obs_id))
    conn.execute("UPDATE cert_item SET rev=rev+1 WHERE id=?", (item_id,))
    _commit(conn, {"id": obs_id, "item_id": item_id})
    return {"id": obs_id, "voided": True}


def restore_observation(conn: sqlite3.Connection, item_id: str, obs_id: str, user: str | None) -> dict:
    ensure_tables(conn)
    o = conn.execute("SELECT voided FROM cert_observation WHERE id=? AND item_id=?", (obs_id, item_id)).fetchone()
    if o is None:
        raise LookupError("找不到這筆觀測")
    if not o["voided"]:
        raise ValueError("這筆觀測沒有作廢")
    conn.execute("UPDATE cert_observation SET voided=0, void_reason=NULL, voided_at=NULL, voided_by=NULL WHERE id=?", (obs_id,))
    conn.execute("UPDATE cert_item SET rev=rev+1 WHERE id=?", (item_id,))
    _commit(conn, {"id": obs_id, "item_id": item_id})
    return {"id": obs_id, "voided": False}


# ------------------------------------------------------------------ 匯入
IMPORT_HEADERS = {
    "憑證名稱": "name", "F5BIGIP": "f5_bigip", "F5VIP": "f5_bigip", "主機IP": "host_ips", "F5是否掛憑證": "f5_mounted",
    "OS/站台類型": "os_type", "正/測": "environment", "申請單位": "req_unit", "申請窗口": "req_contact",
    "AP窗口": "ap_contact", "INFRA執行人員": "infra_owner", "預排更換時間": "planned_text", "AP驗測人員": "ap_tester",
    "憑證更換是否需要公告": "announce_needed", "憑證到期日": "expiry", "到期日": "expiry", "憑證到期時間": "expiry",
}
MAX_IMPORT_ROWS = 5000


def _norm_header(h) -> str:
    t = re.sub(r"\s+", "", _clean(h, 100)).replace("／", "/").upper()
    return t


def parse_xlsx(data: bytes) -> list[dict]:
    """xlsx → 列清單 [{row_no, fields}]。表頭換行／空白容忍；找不到「憑證名稱」表頭拋 ValueError。"""
    import daily_risk
    from openpyxl import load_workbook

    daily_risk._check_xlsx_zip(data)
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl 對壞檔拋各種例外，統一成白話
        raise ValueError(f"讀不了這個 xlsx：{type(exc).__name__}") from exc
    hmap = {_norm_header(k): v for k, v in IMPORT_HEADERS.items()}
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        header_idx, header = None, None
        buf = []
        for i, row in enumerate(rows, start=1):
            buf.append(row)
            if i <= 15 and any(_norm_header(c) == "憑證名稱" for c in row):
                header_idx, header = i, row
                break
            if i > 15:
                break
        if header is None:
            continue
        cols = {j: hmap[_norm_header(c)] for j, c in enumerate(header) if _norm_header(c) in hmap}
        out = []
        for n, row in enumerate(ws.iter_rows(min_row=header_idx + 1, values_only=True), start=header_idx + 1):
            raw = {fld: row[j] for j, fld in cols.items() if j < len(row)}
            if not any(_clean(v) for v in raw.values()):
                continue
            if len(out) >= MAX_IMPORT_ROWS:
                raise ValueError(f"列數超過上限 {MAX_IMPORT_ROWS}")
            out.append({"row_no": n, "raw": raw})
        wb.close()
        return out
    wb.close()
    raise ValueError("找不到表頭「憑證名稱」；請確認第一個工作表前 15 列內有表頭列")


def _row_fields(raw: dict, names=None, active=None) -> dict:
    f_mounted = yes_no(raw.get("f5_mounted"))
    bigip = first_endpoint(raw.get("f5_bigip"))
    host = first_endpoint(raw.get("host_ips"))
    name = _clean(raw.get("name"))
    planned = _clean(raw.get("planned_text"))
    mount = "F5" if f_mounted == 1 else norm_mount(raw.get("os_type"), names)
    f = {
        "name": name, "environment": norm_env(raw.get("environment")),
        "endpoint": bigip if (f_mounted == 1 and bigip) else (host or bigip),
        "port": 443, "sni": derive_sni(name), "mount_type": mount,
        "host_ips": _clean(raw.get("host_ips")), "f5_bigip": _clean(raw.get("f5_bigip")), "f5_mounted": f_mounted,
        "os_type": _clean(raw.get("os_type")), "req_unit": _clean(raw.get("req_unit")),
        "req_contact": _clean(raw.get("req_contact")), "ap_contact": _clean(raw.get("ap_contact")),
        "infra_owner": _clean(raw.get("infra_owner")), "ap_tester": _clean(raw.get("ap_tester")),
        "planned_text": planned, "planned_at_utc": parse_planned(planned),
        "announce_needed": _clean(raw.get("announce_needed")),
        # 型態不在已登記清單：不默默歸「其他」，預覽明講、由人決定（新增型態／歸其他／略過）
        "unknown_mount": mount if (mount and names is not None and mount not in (active if active is not None else names)) else "",
    }
    exp = raw.get("expiry")
    f["expiry_utc"] = None
    f["expiry_error"] = None
    if _clean(exp):
        try:
            f["expiry_utc"] = to_utc_iso(exp)
        except ValueError as exc:
            f["expiry_error"] = str(exc)
    return f


def _item_cands(existing: list[dict], f: dict) -> list[dict]:
    nm = norm_name(f["name"])
    return [{"id": e["id"], "name": e["name"], "environment": e["environment"], "endpoint": e["endpoint"], "port": e["port"],
             "sni": e["sni"], "mount_type": e["mount_type"], "active": bool(e["active"])}
            for e in existing if norm_name(e["name"]) == nm and e["environment"] == f["environment"]]


def _diff(e: dict, f: dict, manual: set[str], with_keys: bool = False) -> tuple[list[dict], int]:
    """匯入值（非空）與現有值的差異；人工改過的欄位不動（回傳略過數）。"""
    changes, protected = [], 0
    fields = list(DATA_FIELDS) + (list(KEY_FIELDS) if with_keys else [])
    for k in fields:
        if k in ("replace_method", "replace_status", "note", "planned_at_utc"):
            continue
        new = f.get(k)
        if new in (None, ""):
            continue
        if e.get(k) == new:
            continue
        if k in manual:
            protected += 1
            continue
        changes.append({"field": k, "old": e.get(k), "new": new})
        if k == "planned_text":
            changes.append({"field": "planned_at_utc", "old": e.get("planned_at_utc"), "new": f.get("planned_at_utc")})
    return changes, protected


def import_plan(conn: sqlite3.Connection, filename: str, data: bytes) -> dict:
    """預覽（唯讀，不寫庫）：每列 new／update／skip／conflict／error，附判斷原因。"""
    parsed = parse_xlsx(data)
    cfg = _mount_cfg(conn)
    names = [m["name"] for m in cfg]
    active_names = [m["name"] for m in cfg if m["active"]]        # 已停用的型態視同未登記（跟手動新增同一條規則）
    existing = [dict(r) for r in conn.execute("SELECT * FROM cert_item")] if tables_ready(conn) else []
    by_key = {key_of(e): e for e in existing}
    has_obs = {r[0] for r in conn.execute("SELECT DISTINCT item_id FROM cert_observation WHERE source <> 'probe'")} if tables_ready(conn) else set()
    seen: dict[tuple, int] = {}
    rows = []
    for p in parsed:
        f = _row_fields(p["raw"], names, active_names)
        row = {"row_no": p["row_no"], "name": f["name"], "environment": f["environment"], "key_text": "", "status": "", "reason": "",
               "changes": [], "candidates": [], "item_id": None, "expiry_utc": f["expiry_utc"], "unknown_mount": f["unknown_mount"]}
        rows.append(row)
        if not f["name"]:
            row.update(status="error", reason="缺憑證名稱")
            continue
        if not f["endpoint"] and not f["sni"]:
            row.update(status="error", reason="缺 F5/主機 IP，且憑證名稱不是主機名稱，無法判定唯一項目；請改用手動新增")
            continue
        k = key_of(f)
        row["key_text"] = key_text(k)
        if f["expiry_error"]:
            row["reason"] = f"到期日欄位看不懂（{f['expiry_error']}），到期日略過；"
        if f["unknown_mount"]:
            row["reason"] += f"掛載型態「{f['unknown_mount']}」尚未登記（需決定：新增型態／歸「其他」／略過）；"
        if k in seen:
            row.update(status="conflict", reason=f"檔內重複：與第 {seen[k]} 列的環境／端點／埠／SNI／掛載型態相同；請選擇略過或當作新項目")
            continue
        seen[k] = p["row_no"]
        e = by_key.get(k)
        if e is not None:
            row["item_id"] = e["id"]
            if not e["active"]:
                row.update(status="skip", reason=row["reason"] + "對到的項目已停用，未變更；如要恢復請在清單重新啟用")
                continue
            manual = set(json.loads(e["manual_fields"] or "[]"))
            ch, prot = _diff(e, f, manual)
            if f["expiry_utc"] and e["id"] not in has_obs:
                ch.append({"field": "到期日（新增觀測）", "old": None, "new": fmt_local(f["expiry_utc"])})
            row["changes"] = ch
            if ch:
                row.update(status="update", reason=row["reason"] + f"以 環境+端點+埠+SNI+掛載型態 對到既有項目；{len([c for c in ch if c['field'] != 'planned_at_utc'])} 個欄位有差異"
                           + (f"；{prot} 個欄位為人工修改過，不覆蓋" if prot else ""))
            else:
                row.update(status="skip", reason=row["reason"] + "與現有資料相同" + (f"（{prot} 個欄位為人工修改過，不覆蓋）" if prot else ""))
            continue
        cands = _item_cands(existing, f)
        if cands:
            row.update(status="conflict", candidates=cands,
                       reason=row["reason"] + f"同名同環境已有 {len(cands)} 筆項目但端點／SNI／掛載不同，無法判斷是同一個還是新的；請選擇")
            continue
        same_name_other_env = any(norm_name(x["name"]) == norm_name(f["name"]) for x in existing)
        row.update(status="new", reason=row["reason"] + ("同名但環境不同，視為不同項目" if same_name_other_env else "沒有對到任何既有項目"))
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("new", "update", "skip", "conflict", "error")}
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    um: dict[str, int] = {}
    for r in rows:
        if r["unknown_mount"] and r["status"] not in ("error",):
            um[r["unknown_mount"]] = um.get(r["unknown_mount"], 0) + 1
    return {"filename": _clean(filename, 100), "row_count": len(rows), "counts": counts, "rows": rows,
            "unknown_mounts": [{"name": k, "rows": v} for k, v in sorted(um.items())],
            "file_sha256": hashlib.sha256(data).hexdigest(), "plan_digest": digest}


class PlanChanged(Exception):
    pass


def import_apply(conn: sqlite3.Connection, filename: str, data: bytes, user: str | None,
                 decisions: dict | None, file_sha256: str | None = None, plan_digest: str | None = None,
                 mount_decisions: dict | None = None) -> dict:
    """確認匯入：重新規劃＋比對 digest＋寫入在同一個交易。衝突列要有 decisions[row_no] 才寫
    （"new"＝當新項目／"skip"／候選項目 id＝視為該項目）。不覆蓋人工修改欄位，不碰既有觀測。"""
    ensure_tables(conn)
    decisions = {str(k): v for k, v in (decisions or {}).items()}
    mount_decisions = {str(k): v for k, v in (mount_decisions or {}).items()}
    if any(v not in ("add", "other", "skip") for v in mount_decisions.values()):
        raise ValueError("掛載型態的處理方式只能是：新增型態／歸「其他」／略過")
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        plan = import_plan(conn, filename, data)
        if file_sha256 is not None or plan_digest is not None:
            if plan["file_sha256"] != file_sha256 or plan["plan_digest"] != plan_digest:
                raise PlanChanged("資料或檔案已變動，請重新預覽")
        cfg = _mount_cfg(conn)
        names = [m["name"] for m in cfg]
        active_names = [m["name"] for m in cfg if m["active"]]
        parsed = {p["row_no"]: _row_fields(p["raw"], names, active_names) for p in parse_xlsx(data)}
        mount_map: dict[str, str] = {}
        now = _iso(_now())
        res = {"created": 0, "updated": 0, "observations": 0, "skipped": 0, "unresolved": 0, "notes": []}
        for r in plan["rows"]:
            f = parsed[r["row_no"]]
            st = r["status"]
            if st == "conflict":
                d = decisions.get(str(r["row_no"]))
                if d is None:
                    res["unresolved"] += 1
                    continue
                if d == "skip":
                    res["skipped"] += 1
                    continue
                if d == "new":
                    st = "new"
                elif d in {c["id"] for c in r["candidates"]}:
                    st, r["item_id"] = "update_chosen", d
                else:
                    raise ValueError(f"第 {r['row_no']} 列的選擇不正確")
            if st in ("skip", "error"):
                res["skipped"] += 1
                continue
            um = f.get("unknown_mount")
            if um:
                md = mount_decisions.get(um)
                if md is None:
                    res["unresolved"] += 1
                    res["notes"].append(f"第 {r['row_no']} 列：掛載型態「{um}」未登記且沒有選處理方式，未寫入")
                    continue
                if md == "skip":
                    res["skipped"] += 1
                    continue
                if md == "other":
                    f["mount_type"] = "其他"
                else:
                    if um not in mount_map:
                        mount_map[um] = add_mount_type(conn, um, user, commit=False)
                        res.setdefault("mounts_added", []).append(mount_map[um])
                    f["mount_type"] = mount_map[um]
            if st == "new":
                k = key_of(f)
                if _find_by_key(conn, k):
                    res["notes"].append(f"第 {r['row_no']} 列：同樣的項目已存在，未新增")
                    res["skipped"] += 1
                    continue
                iid = uuid.uuid4().hex
                cols = ["id", "seq", "source", "created_by", "created_at", "manual_fields", *KEY_FIELDS,
                        *[c for c in DATA_FIELDS if c not in ("replace_method", "replace_status", "note")]]
                vals = [iid, _next_seq(conn), "import", user, now, "[]", *[f[c] for c in KEY_FIELDS],
                        *[f[c] for c in DATA_FIELDS if c not in ("replace_method", "replace_status", "note")]]
                conn.execute(f"INSERT INTO cert_item ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
                res["created"] += 1
                _import_obs(conn, iid, f, user, res)
                continue
            iid = r["item_id"]
            e = dict(conn.execute("SELECT * FROM cert_item WHERE id=?", (iid,)).fetchone())
            manual = set(json.loads(e["manual_fields"] or "[]"))
            ch, _ = _diff(e, f, manual, with_keys=(st == "update_chosen"))
            if any(c["field"] == "planned_text" for c in ch) and e.get("planned_ignored"):
                ch.append({"field": "planned_ignored", "old": 1, "new": 0})        # 匯入文字換了：新值要重新讓人決定；文字相同則維持忽略
            if st == "update_chosen":                  # 人選了候選：鍵欄位若會撞到別筆就不改鍵
                keych = {c["field"]: c["new"] for c in ch if c["field"] in KEY_FIELDS}
                if keych and _find_by_key(conn, key_of({**e, **keych}), exclude_id=iid):
                    ch = [c for c in ch if c["field"] not in KEY_FIELDS]
                    res["notes"].append(f"第 {r['row_no']} 列：改端點會跟另一筆重複，只更新其他欄位")
            if ch:
                sets = ", ".join(f"{c['field']}=?" for c in ch) + ", updated_by=?, updated_at=?, rev=rev+1"
                conn.execute(f"UPDATE cert_item SET {sets} WHERE id=?", [*[c["new"] for c in ch], user, now, iid])
                res["updated"] += 1
            else:
                res["skipped"] += 1
            _import_obs(conn, iid, f, user, res)
        conn.execute("INSERT INTO app_settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (SETTING_IMPORT_LAST, json.dumps({"at": now, "file": plan["filename"], "created": res["created"], "updated": res["updated"]},
                                                      ensure_ascii=False)))
        _commit(conn, {**res, "filename": plan["filename"]})
    except Exception:
        conn.rollback()
        raise
    res["filename"] = plan["filename"]
    res["counts"] = plan["counts"]
    return res


def _import_obs(conn, iid: str, f: dict, user: str | None, res: dict) -> None:
    """匯入檔的到期日只當『第一筆』觀測：項目已有任何觀測就不再加（避免舊檔蓋過人工登錄的較新到期日）。"""
    if not f.get("expiry_utc"):
        return
    if conn.execute("SELECT 1 FROM cert_observation WHERE item_id=? AND source <> 'probe' LIMIT 1", (iid,)).fetchone():
        return
    conn.execute(
        "INSERT INTO cert_observation (id,item_id,not_after_utc,source,observed_at_utc,note,created_by) VALUES (?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, iid, f["expiry_utc"], "import", _iso(_now()), "匯入檔的到期日欄位", user))
    conn.execute("UPDATE cert_item SET rev=rev+1, replaced_pending=0 WHERE id=?", (iid,))        # 觀測變動也算寫入：標記已更換之後被匯入加了觀測，批次復原要判衝突
    res["observations"] += 1


# ------------------------------------------------------------------ 匯出
EXPORT_HEAD_DEID = ["項次", "憑證名稱", "環境", "端點", "埠", "SNI", "掛載型態", "申請單位", "到期時間(UTC+8)", "剩餘", "風險等級",
                    "到期分段", "預排更換時間", "預排日期", "需公告", "更換方式", "更換進度", "資料來源", "最後更新(UTC+8)", "停用", "停用原因"]


def export_rows(conn: sqlite3.Connection, with_contacts: bool, now: datetime | None = None,
                flt: dict | None = None) -> tuple[list[str], list[list]]:
    """匯出資料。預設去識別（不含人名）；with_contacts 只有管理員端點才會傳 True。flt＝清單的篩選條件（None＝全部）。"""
    rows = all_rows(conn, now)
    if flt is not None:
        rows = apply_filters(conn, rows, normalize_filters(flt))
    head = list(EXPORT_HEAD_DEID)
    extra = []
    if with_contacts:
        head += ["申請窗口", "AP窗口", "Infra執行人員", "AP驗測人員"]
        extra = {r["id"]: r for r in (conn.execute("SELECT id, req_contact, ap_contact, infra_owner, ap_tester FROM cert_item") if tables_ready(conn) else [])}
    body = []
    for r in rows:
        line = [f"C{r['seq']}", r["name"], r["environment"], r["endpoint"], r["port"], r["sni"], r["mount_type"], r["req_unit"],
                r["not_after_local"] or "", r["remain_text"], r["risk"], r["status_label"], r["planned_text"], r["planned_date"],
                r["announce_needed"], r["replace_method"], r["replace_status"], r["source_label"], r["data_updated_local"] or "",
                "" if r["active"] else "已停用", r["deactivate_reason_label"]]
        if with_contacts:
            c = extra.get(r["id"])
            line += [c["req_contact"] or "", c["ap_contact"] or "", c["infra_owner"] or "", c["ap_tester"] or ""] if c else ["", "", "", ""]
        body.append(line)
    return head, body


# ------------------------------------------------------------------ 批次更新（兩段式：預覽→確認；單一交易；稽核；可整批復原）
class Forbidden(Exception):
    pass


def _norm_batch_changes(changes: dict | None, is_admin: bool, allow_empty: bool = False) -> dict:
    """批次只能改『更換相關欄位』（BATCH_FIELDS）；身分鍵欄位一律拒絕。執行人是人名欄，只有管理員能批次設。"""
    if not isinstance(changes, dict):
        changes = {}
    if not changes and not allow_empty:
        raise ValueError("請至少勾選一個要修改的欄位")
    bad = [k for k in changes if k not in BATCH_FIELDS]
    if bad:
        raise ValueError(f"批次不能修改「{'、'.join(bad)}」；只有 {'、'.join(BATCH_LABEL[k] for k in BATCH_FIELDS)} 可以批次修改")
    if "infra_owner" in changes and not is_admin:
        raise Forbidden("執行人是人名欄位，只有管理員可以批次設定")
    out = _norm_input({k: v for k, v in changes.items()})
    out.pop("planned_at_utc", None)
    if "announce_needed" in out and out["announce_needed"] not in ("", "是", "否"):
        raise ValueError("「需公告」只能是 是／否／（清空）")
    return out


def _norm_replace_op(op) -> dict | None:
    """『標記已更換』的附加動作：登錄新到期日（必填，或明確「稍後補登」）、必要時作廢舊觀測。"""
    if not op:
        return None
    if not isinstance(op, dict):
        raise ValueError("標記已更換的資料格式不對")
    na = _clean(op.get("not_after"))
    iso = to_utc_iso(na) if na else None
    if iso is None and not op.get("later"):
        raise ValueError("標記已更換要填新的到期時間，或明確勾選「稍後補登」")
    void_old = bool(op.get("void_old"))
    if void_old and iso is None:
        raise ValueError("要作廢舊觀測，必須同時登錄新到期日（否則這筆會變成「無到期資料」）")
    return {"not_after_utc": iso, "later": iso is None, "void_old": void_old}


def check_schedule(status: str | None, planned_date: str | None) -> str | None:
    """預排日期與更換進度要一致：已排程必須有預排日期；有預排日期不能停在待排程／未設定。回傳矛盾說明或 None。"""
    st = status or ""
    if st == "已排程" and not planned_date:
        return "「已排程」必須有預排日期（請同時設定預排日期，或用「排定更換」）"
    if planned_date and st in ("", "待排程"):
        return "有預排日期時，更換進度不能是「待排程」或未設定（請改成「已排程」，或用「排定更換」一起設定）"
    return None


def _obs_ops(conn: sqlite3.Connection, iid: str, rop: dict) -> dict:
    """這個項目要加哪筆新觀測、要作廢哪些舊觀測（只算、不寫）。"""
    rows = list(conn.execute("SELECT id, not_after_utc, voided, issuer_dn, serial, fingerprint_sha256 FROM cert_observation "
                             "WHERE item_id=? AND source <> 'probe'", (iid,)))        # 探測紀錄是歷史參考，更換作業不動它
    add, keep = rop["not_after_utc"], None
    if add:
        for o in rows:
            if o["not_after_utc"] == add and not (o["issuer_dn"] or o["serial"] or o["fingerprint_sha256"]):
                if o["voided"]:
                    raise ValueError("這筆已有同一個到期時間但被作廢的觀測，請先到詳情頁「還原」它，或換一個到期時間")
                add, keep = None, o["id"]          # 已有一模一樣的有效觀測：不重複新增
                break
    void = [o["id"] for o in rows if not o["voided"] and o["id"] != keep] if rop["void_old"] else []
    return {"add": add, "void": void}


def _batch_plan(conn: sqlite3.Connection, ids: list[str] | None, flt: dict | None, changes: dict, is_admin: bool,
                now: datetime | None = None, replace_op: dict | None = None) -> dict:
    rop = _norm_replace_op(replace_op)
    norm = _norm_batch_changes(changes, is_admin, allow_empty=bool(rop))
    if rop:
        if norm.get("replace_status") not in (None, "已更換"):
            raise ValueError("標記已更換時，更換進度只能是「已更換」")
        norm["replace_status"] = "已更換"
    rows = all_rows(conn, now)
    if flt is not None:                                  # 『全選目前篩選結果』：後端用同一份篩選算出清單
        picked = [r["id"] for r in apply_filters(conn, rows, normalize_filters(flt))]
    else:
        picked = list(dict.fromkeys(ids or []))
    if not picked:
        raise ValueError("沒有選到任何憑證項目")
    if len(picked) > BATCH_MAX:
        raise ValueError(f"一次最多批次更新 {BATCH_MAX} 筆，目前選了 {len(picked)} 筆；請縮小篩選條件或分批")
    q = ",".join("?" * len(picked))
    raw = {r["id"]: dict(r) for r in conn.execute(f"SELECT * FROM cert_item WHERE id IN ({q})", picked)}
    missing = [i for i in picked if i not in raw]
    if missing:
        raise LookupError(f"有 {len(missing)} 筆項目找不到（可能已被刪除）")
    inactive = [i for i in picked if not raw[i]["active"]]
    changed, unchanged, ops, bad = [], 0, {}, []
    for iid in picked:
        cur = raw[iid]
        if not cur["active"]:
            continue
        diff = {k: (cur.get(k), v) for k, v in norm.items() if not _same(cur.get(k), v)}
        op = _obs_ops(conn, iid, rop) if rop else {"add": None, "void": []}
        if rop:                                                     # 稍後補登＝待補旗標 1；填了新到期日＝0
            want = 1 if rop["later"] else 0
            op["pend"] = None if int(cur.get("replaced_pending") or 0) == want else [int(cur.get("replaced_pending") or 0), want]
        if diff or op["add"] or op["void"] or op.get("pend"):
            changed.append((iid, cur, diff))
            ops[iid] = op
            if "replace_status" in diff or "planned_date" in diff:       # 只檢查這次動到的項目；舊資料本來就矛盾的不動
                new = {**cur, **{k: n for k, (_, n) in diff.items()}}
                msg = check_schedule(new.get("replace_status"), new.get("planned_date"))
                if msg:
                    bad.append((cur["name"], msg))
        else:
            unchanged += 1
    if bad:
        raise ValueError(f"有 {len(bad)} 筆會讓預排日期與更換進度矛盾，例如「{bad[0][0]}」：{bad[0][1]}")
    sig = [[iid, int(cur.get("rev") or 0), cur.get("updated_at") or "", {k: "" if o is None else str(o) for k, (o, _) in diff.items()}, ops[iid]]
           for iid, cur, diff in changed]
    digest = hashlib.sha256(json.dumps({"ids": sorted(picked), "norm": norm, "rop": rop, "sig": sig}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {"ids": picked, "norm": norm, "changed": changed, "unchanged": unchanged, "inactive": len(inactive), "digest": digest, "rop": rop, "ops": ops,
            "views": {r["id"]: r for r in rows}}


def _field_show(k: str, v) -> str:
    if k == "infra_owner":
        return "（已設定，人名不顯示）" if v else "（清空）"
    return "（清空）" if v in (None, "") else str(v)


def batch_preview(conn: sqlite3.Connection, ids: list[str] | None, flt: dict | None, changes: dict, is_admin: bool,
                  now: datetime | None = None, replace_op: dict | None = None) -> dict:
    """第一段：只算、不寫庫。回傳影響筆數、每個欄位『原本分布→新值』、前 20 筆前後差異，以及確認時要帶回來的 digest。"""
    if not tables_ready(conn):
        raise ValueError("憑證表尚未建立")
    pl = _batch_plan(conn, ids, flt, changes, is_admin, now, replace_op)
    fields = []
    for k, new in pl["norm"].items():
        dist: dict[str, int] = {}
        for _iid, cur, diff in pl["changed"]:
            if k in diff:
                lab = "（人名，不顯示）" if k == "infra_owner" else ("（空）" if diff[k][0] in (None, "") else str(diff[k][0]))
                dist[lab] = dist.get(lab, 0) + 1
        fields.append({"field": k, "label": BATCH_LABEL[k], "new": _field_show(k, new), "from": [{"value": a, "count": b} for a, b in sorted(dist.items())],
                       "will_change": sum(dist.values())})
    sample = []
    for iid, cur, diff in pl["changed"][:20]:
        v = pl["views"].get(iid, {})
        ch = [{"label": BATCH_LABEL[k], "old": _field_show(k, o), "new": _field_show(k, n)} for k, (o, n) in diff.items()]
        op = pl["ops"][iid]
        if op["add"]:
            ch.append({"label": BATCH_LABEL["new_expiry"], "old": "—", "new": fmt_local(op["add"]) or ""})
        if op["void"]:
            ch.append({"label": "舊觀測", "old": f"{len(op['void'])} 筆", "new": "作廢"})
        sample.append({"id": iid, "seq": cur["seq"], "name": cur["name"], "environment": cur["environment"], "endpoint": v.get("endpoint", ""), "changes": ch})
    out = {"selected": len(pl["ids"]), "will_change": len(pl["changed"]), "unchanged": pl["unchanged"], "inactive_skipped": pl["inactive"],
           "fields": fields, "sample": sample, "ids": pl["ids"], "digest": pl["digest"], "max": BATCH_MAX}
    if pl["rop"]:
        out["replace_op"] = {"later": pl["rop"]["later"], "new_expiry": fmt_local(pl["rop"]["not_after_utc"]), "void_old": pl["rop"]["void_old"],
                             "obs_added": sum(1 for o in pl["ops"].values() if o["add"]), "obs_voided": sum(len(o["void"]) for o in pl["ops"].values())}
    return out


def batch_apply(conn: sqlite3.Connection, ids: list[str], changes: dict, user: str | None, is_admin: bool, digest: str | None,
                now: datetime | None = None, replace_op: dict | None = None) -> dict:
    """第二段：重新計算並比對 digest（預覽後資料有變就拒絕；單筆『標記已更換』不經預覽，digest 為 None），單一交易寫入＋留逐項前後值。"""
    ensure_tables(conn)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        pl = _batch_plan(conn, ids, None, changes, is_admin, now, replace_op)
        if digest is not None and pl["digest"] != digest:
            raise PlanChanged("預覽之後資料有變動，請重新預覽再確認")
        if not pl["changed"]:
            raise ValueError("沒有任何一筆會變更，不需要套用")
        bid, ts = uuid.uuid4().hex, _iso(_now())
        shown = {k: v for k, v in pl["norm"].items() if k != "infra_owner"}        # 批次紀錄的摘要不含人名
        fields = list(pl["norm"])
        rop = pl["rop"]
        if rop:
            fields.append("new_expiry")
            shown["new_expiry"] = (fmt_local(rop["not_after_utc"]) or "")[:10] if rop["not_after_utc"] else "稍後補登"
        conn.execute("INSERT INTO cert_batch (id, created_by, created_at, item_count, fields_json, summary_json) VALUES (?,?,?,?,?,?)",
                     (bid, user, ts, len(pl["changed"]), json.dumps(fields, ensure_ascii=False), json.dumps(shown, ensure_ascii=False)))
        for iid, cur, diff in pl["changed"]:
            op = pl["ops"][iid]
            man = sorted(set(json.loads(cur["manual_fields"] or "[]")) | set(diff))
            extra = {}
            if op.get("pend"):
                extra["replaced_pending"] = tuple(op["pend"])
            if "planned_date" in diff and diff["planned_date"][1] is None:        # 清空預排日期＝不再採用匯入原文字當排程
                extra["planned_ignored"] = (cur.get("planned_ignored") or 0, 1)
            allset = {**{k: n for k, (_, n) in diff.items()}, **{k: n for k, (_, n) in extra.items()}}
            sets = "".join(f"{k}=?, " for k in allset) + "manual_fields=?, updated_by=?, updated_at=?, rev=rev+1"
            conn.execute(f"UPDATE cert_item SET {sets} WHERE id=?", [*allset.values(), json.dumps(man, ensure_ascii=False), user, ts, iid])
            before = {k: o for k, (o, _) in diff.items()}
            before.update({k: o for k, (o, _) in extra.items()})
            before["__manual_fields"] = cur["manual_fields"] or "[]"
            if op["add"] or op["void"]:
                oid = None
                if op["add"]:
                    oid = uuid.uuid4().hex
                    conn.execute("INSERT INTO cert_observation (id,item_id,not_after_utc,source,observed_at_utc,note,created_by) VALUES (?,?,?,?,?,?,?)",
                                 (oid, iid, op["add"], "manual", ts, "標記已更換時登錄", user))
                for vid in op["void"]:
                    conn.execute("UPDATE cert_observation SET voided=1, void_reason=?, voided_at=?, voided_by=? WHERE id=?",
                                 (f"已更換，舊憑證觀測作廢（批次 {bid[:8]}）", ts, user, vid))
                before["__obs"] = {"added": oid, "voided": op["void"]}
            after_rev = conn.execute("SELECT rev FROM cert_item WHERE id=?", (iid,)).fetchone()[0]
            conn.execute("INSERT INTO cert_batch_item (batch_id, item_id, before_json, after_json, after_updated_at, after_rev) VALUES (?,?,?,?,?,?)",
                         (bid, iid, json.dumps(before, ensure_ascii=False), json.dumps(allset, ensure_ascii=False), ts, after_rev))
        _commit(conn, {"batch_id": bid, "changed": len(pl["changed"]), "fields": fields, "item_ids": [x[0] for x in pl["changed"]]})
    except Exception:
        conn.rollback()
        raise
    return {"batch_id": bid, "changed": len(pl["changed"]), "unchanged": pl["unchanged"], "inactive_skipped": pl["inactive"], "fields": fields}


def mark_replaced(conn: sqlite3.Connection, item_id: str, op: dict, user: str | None, is_admin: bool) -> dict:
    """單一操作：更換進度＝已更換＋登錄新到期日（必填或明確『稍後補登』）＋必要時作廢舊觀測。
    走批次同一條路徑（單一交易、留批次紀錄、可在「批次紀錄」復原）。"""
    return batch_apply(conn, [item_id], {}, user, is_admin, None, replace_op=op)


def schedule_item(conn: sqlite3.Connection, item_id: str, planned_date: str, user: str | None, is_admin: bool) -> dict:
    """『排定更換』組合動作：預排日期＋更換進度＝已排程一次設定（兩欄一致）。"""
    d = norm_date(planned_date)
    if not d:
        raise ValueError("請選預排日期")
    return update_item(conn, item_id, {"planned_date": d, "replace_status": "已排程"}, user, is_admin)


def planned_import_action(conn: sqlite3.Connection, item_id: str, action: str, user: str | None) -> dict:
    """處理『匯入的預排更換時間文字』：convert＝轉成正式預排日期（文字要能解析出日期；進度還在待排程／未設定時一併改為已排程）；
    ignore＝忽略匯入值（不再當排程）。"""
    ensure_tables(conn)
    r = conn.execute("SELECT * FROM cert_item WHERE id=?", (item_id,)).fetchone()
    if r is None:
        raise LookupError("找不到這筆憑證項目")
    if action == "ignore":
        if not (r["planned_text"] or "").strip():
            raise ValueError("這筆沒有匯入的預排文字，不需要忽略")
        if r["planned_ignored"]:
            return {"id": item_id, "action": "ignore", "noop": True}      # 已經忽略過：不寫入、不加版本、不留稽核
        conn.execute("UPDATE cert_item SET planned_ignored=1, updated_by=?, updated_at=?, rev=rev+1 WHERE id=?", (user, _iso(_now()), item_id))
        _commit(conn, {"id": item_id, "action": "ignore"})
        return {"id": item_id, "action": "ignore"}
    if action != "convert":
        raise ValueError("動作只能是 convert／ignore")
    if r["planned_date"]:
        raise ValueError("這筆已經有正式的預排日期")
    d = _local_date(r["planned_at_utc"])
    if not d:
        raise ValueError("匯入的預排文字無法轉成日期，請改用「忽略匯入值」，再自行填預排日期")
    st = r["replace_status"] or ""
    new_st = "已排程" if st in ("", "待排程") else st
    man = sorted(set(json.loads(r["manual_fields"] or "[]")) | {"planned_date", "replace_status"})
    conn.execute("UPDATE cert_item SET planned_date=?, planned_ignored=0, replace_status=?, manual_fields=?, updated_by=?, updated_at=?, rev=rev+1 WHERE id=?",
                 (d, new_st, json.dumps(man, ensure_ascii=False), user, _iso(_now()), item_id))
    _commit(conn, {"id": item_id, "action": "convert"})
    return {"id": item_id, "action": "convert", "planned_date": d, "replace_status": new_st}


def _batch_tables_ready(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('cert_batch','cert_batch_item')").fetchone()[0] == 2
    except sqlite3.Error:
        return False


def list_batches(conn: sqlite3.Connection, full: bool, limit: int = 50) -> list[dict]:
    if not _batch_tables_ready(conn):
        return []
    out = []
    for b in conn.execute("SELECT * FROM cert_batch ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)):
        left = conn.execute("SELECT count(*) FROM cert_batch_item WHERE batch_id=? AND undone=0", (b["id"],)).fetchone()[0]
        try:
            summ = json.loads(b["summary_json"] or "{}")
        except ValueError:
            summ = {}        # 摘要欄壞掉只影響這一列的說明文字，不影響復原（復原讀逐項前後值）
        fields = json.loads(b["fields_json"] or "[]")
        d = {"id": b["id"], "short": b["id"][:8], "created_at": b["created_at"], "created_local": fmt_local(b["created_at"]),
             "item_count": b["item_count"], "fields": [BATCH_LABEL.get(k, k) for k in fields],
             "summary": "；".join(f"{BATCH_LABEL.get(k, k)}→{_field_show(k, v)}" for k, v in summ.items()),
             "item_seqs": [r[0] for r in conn.execute(
                 "SELECT i.seq FROM cert_batch_item bi JOIN cert_item i ON i.id = bi.item_id WHERE bi.batch_id=? ORDER BY i.seq", (b["id"],))],
             "undone_count": b["undone_count"], "remaining": left,
             "state": "undone" if left == 0 else ("partial" if b["undone_count"] else "active")}
        if full:
            d["created_by"] = b["created_by"]
        out.append(d)
    return out


def batch_undo(conn: sqlite3.Connection, batch_id: str, user: str | None, is_admin: bool = False) -> dict:
    """整批復原：項目自批次後被寫入過（rev 不等於批次套用後的 rev，或批次改的欄位值不同）→ 列為衝突、不覆蓋；其餘還原。可重複執行處理剩下的。
    人名欄位（執行人）：非管理員復原時一律略過、不回寫（跟批次套用同一條權限）；留給管理員之後復原。回應只列欄位名稱，不含人名。"""
    ensure_tables(conn)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        b = conn.execute("SELECT * FROM cert_batch WHERE id=?", (batch_id,)).fetchone()
        if b is None:
            raise LookupError("找不到這個批次")
        todo = list(conn.execute("SELECT * FROM cert_batch_item WHERE batch_id=? AND undone=0", (batch_id,)))
        if not todo:
            raise ValueError("這個批次已經全部復原了")
        ts, restored, conflicts, skipped_person = _iso(_now()), 0, [], 0
        for bi in todo:
            cur = conn.execute("SELECT * FROM cert_item WHERE id=?", (bi["item_id"],)).fetchone()
            after = json.loads(bi["after_json"])
            before = json.loads(bi["before_json"])
            man_before = before.pop("__manual_fields", "[]")
            ob = before.pop("__obs", None)
            if cur is None:
                conflicts.append({"id": bi["item_id"], "seq": None, "name": "（項目已不存在）", "fields": list(after)})
                continue
            drift = [k for k, v in after.items() if not _same(cur[k], v)]
            ar = bi["after_rev"]
            moved = (int(cur["rev"] or 0) != int(ar)) if ar is not None else ((cur["updated_at"] or "") != (bi["after_updated_at"] or ""))
            if drift or moved:
                conflicts.append({"id": cur["id"], "seq": cur["seq"], "name": cur["name"], "fields": drift or list(after)})
                continue
            keep_person = (not is_admin) and "infra_owner" in before
            if keep_person:
                before.pop("infra_owner")
                man_before = json.dumps(sorted(set(json.loads(man_before or "[]")) | {"infra_owner"}), ensure_ascii=False)
                skipped_person += 1
            added = set(after) - set(json.loads(man_before or "[]"))        # 批次自己加進 manual_fields 的欄位；復原只拿掉這些
            man_before = json.dumps(sorted(set(json.loads(cur["manual_fields"] or "[]")) - added | ({"infra_owner"} if keep_person else set())), ensure_ascii=False)
            if before or ob:
                sets = "".join(f"{k}=?, " for k in before) + "manual_fields=?, updated_by=?, updated_at=?, rev=rev+1"
                conn.execute(f"UPDATE cert_item SET {sets} WHERE id=?", [*before.values(), man_before, user, ts, cur["id"]])
                if ob:                      # 標記已更換的觀測動作：新加的那筆作廢（保留紀錄）、被作廢的舊觀測還原
                    for vid in ob.get("voided", []):
                        conn.execute("UPDATE cert_observation SET voided=0, void_reason=NULL, voided_at=NULL, voided_by=NULL WHERE id=?", (vid,))
                    if ob.get("added"):
                        conn.execute("UPDATE cert_observation SET voided=1, void_reason=?, voided_at=?, voided_by=? WHERE id=?",
                                     (f"復原批次 {batch_id[:8]}", ts, user, ob["added"]))
                restored += 1
            if keep_person:       # 批次紀錄只留人名欄位，等管理員復原；其他欄位已還原
                new_rev = conn.execute("SELECT rev FROM cert_item WHERE id=?", (cur["id"],)).fetchone()[0]
                person_after = {"infra_owner": after["infra_owner"]}
                person_before = {"infra_owner": json.loads(bi["before_json"])["infra_owner"], "__manual_fields": json.loads(bi["before_json"]).get("__manual_fields", "[]")}
                conn.execute("UPDATE cert_batch_item SET before_json=?, after_json=?, after_updated_at=?, after_rev=? WHERE batch_id=? AND item_id=?",
                             (json.dumps(person_before, ensure_ascii=False), json.dumps(person_after, ensure_ascii=False), ts, new_rev, batch_id, cur["id"]))
            else:
                conn.execute("UPDATE cert_batch_item SET undone=1 WHERE batch_id=? AND item_id=?", (batch_id, cur["id"]))
        conn.execute("UPDATE cert_batch SET undone_count=undone_count+?, undone_at=?, undone_by=? WHERE id=?",
                     (restored, ts if restored else b["undone_at"], user if restored else b["undone_by"], batch_id))
        _commit(conn, {"batch_id": batch_id, "restored": restored, "conflicts": conflicts})
    except Exception:
        conn.rollback()
        raise
    out = {"batch_id": batch_id, "restored": restored, "conflicts": conflicts}
    if skipped_person:
        out["skipped_fields"] = [BATCH_LABEL["infra_owner"]]
        out["skipped_items"] = skipped_person
    return out


# ------------------------------------------------------------------ 中繼探測（2.042.02）：探測清單匯出、寫入探測結果
_PROBE_HOST = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,252}$")


def _probe_host_ok(h: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return bool(_PROBE_HOST.match(h)) and ".." not in h


def probe_list(conn: sqlite3.Connection) -> dict:
    """啟用中項目的探測清單：每行 `端點:埠,SNI`（IPv6 用 [位址]:埠）。端點不是合法主機名／IP 的略過並回報。同端點同埠同 SNI 只列一次。"""
    import ipaddress
    lines, skipped, seen = [], [], set()
    if not tables_ready(conn):
        return {"text": "", "lines": 0, "skipped": []}
    for r in conn.execute("SELECT seq, endpoint, port, sni FROM cert_item WHERE active=1 ORDER BY seq"):
        h = (r["endpoint"] or "").strip().lower()
        sni = (r["sni"] or "").strip().lower()
        port = int(r["port"] or 443)
        if not h or not _probe_host_ok(h) or not 1 <= port <= 65535 or (sni and not _PROBE_HOST.match(sni)):
            skipped.append(f"C{r['seq']}")
            continue
        k = (h, port, sni)
        if k in seen:
            continue
        seen.add(k)
        try:
            hp = f"[{h}]:{port}" if ipaddress.ip_address(h).version == 6 else f"{h}:{port}"
        except ValueError:
            hp = f"{h}:{port}"
        lines.append(hp + (f",{sni}" if sni else ""))
    return {"text": "\n".join(lines) + ("\n" if lines else ""), "lines": len(lines), "skipped": skipped}


def match_probe_item(conn: sqlite3.Connection, endpoint: str, port: int, sni: str) -> tuple[str, list[str]]:
    """探測結果對到哪個項目：啟用中、端點＋埠＋SNI 相同（不分大小寫）。回 (matched／no_item／ambiguous, 項目 id 清單)。不自動建項目。"""
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM cert_item WHERE active=1 AND lower(endpoint)=? AND port=? AND lower(sni)=?",
        (endpoint.lower(), int(port), (sni or "").lower()))]
    if len(ids) == 1:
        return "matched", ids
    return ("no_item" if not ids else "ambiguous"), ids


def record_probe(conn: sqlite3.Connection, item_id: str, res: dict, probed_at_utc: str, batch_id: str) -> bool:
    """寫一筆探測結果（不 commit；由 relay_import 同一交易提交）。成功且有到期時間才記進 cert_probe_observation（同一張憑證只一列，
    再看到只更新最後看到時間）。不碰 cert_observation 與 cert_item（不改 rev、不清待補）：探測不是人工作業，
    不能擋人工登錄、也不能讓批次復原誤判衝突。回 True＝第一次看到這張憑證。"""
    ensure_tables(conn)
    added = False
    if res["ok"] and res.get("not_after_utc"):
        cur = conn.execute(
            "INSERT INTO cert_probe_observation (id,item_id,issuer_dn,serial,fingerprint_sha256,subject_cn,san_json,not_before_utc,"
            "not_after_utc,first_seen_utc,last_seen_utc,batch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(item_id, fingerprint_sha256, not_after_utc) DO NOTHING",
            (uuid.uuid4().hex, item_id, res.get("issuer") or "", res.get("serial") or "", res.get("sha256") or "",
             res.get("subject") or "", json.dumps(res.get("san") or [], ensure_ascii=False), res.get("not_before_utc"),
             res["not_after_utc"], probed_at_utc, probed_at_utc, batch_id))
        added = cur.rowcount == 1
        if not added:
            conn.execute("UPDATE cert_probe_observation SET last_seen_utc=?, batch_id=? WHERE item_id=? AND fingerprint_sha256=? "
                         "AND not_after_utc=? AND last_seen_utc < ?",
                         (probed_at_utc, batch_id, item_id, res.get("sha256") or "", res["not_after_utc"], probed_at_utc))
    conn.execute(
        "INSERT INTO cert_probe_status (item_id, probed_at_utc, ok, error, not_after_utc, fingerprint_sha256, batch_id, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET probed_at_utc=excluded.probed_at_utc, ok=excluded.ok, "
        "error=excluded.error, not_after_utc=excluded.not_after_utc, fingerprint_sha256=excluded.fingerprint_sha256, "
        "batch_id=excluded.batch_id, updated_at=excluded.updated_at WHERE excluded.probed_at_utc >= cert_probe_status.probed_at_utc",
        (item_id, probed_at_utc, 1 if res["ok"] else 0, (res.get("error") or "")[:TEXT_MAX] or None,
         res.get("not_after_utc") if res["ok"] else None, res.get("sha256") or None, batch_id, _iso(_now())))
    return added
