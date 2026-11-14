"""ACT-03 Teams 單向通知（重大告警／恢復／定期摘要），走 Teams「Workflows」的 webhook。

官方格式與來源見 docs/codex-review/2026-10-30/105_ACT03_Teams官方格式與設計.md。
Office 365 Connector／舊 incoming webhook 已退役，**不使用**；只送 Adaptive Card（type=message + attachments）。

沿用既有做法（eos_notify／vuln_notify／daily_digest）：
  * 設定存 app_settings（key=teams_notify）；壞掉丟 SettingsCorrupt，不吞。
  * 預覽（dry_run）永遠不送、不寫任何表。
  * 投遞紀錄表 teams_notify_delivery：每則一列，含事件ID、嘗試次數、HTTP 狀態、錯誤摘要（**不含 URL**）。
  * 去重：event_key UNIQUE（同一事件輪詢／重送不重發）＋游標只往前；重試重用同一列。
  * 只有送成功才算送過（sent）；恢復／結案只在「對應的告警／開立那一則真的 sent」之後才送。
資料來源不另建：告警／恢復＝whatsup_alert_history（new/changed/cleared）；事件＝incident_event；
摘要數字＝daily_digest.diff_for 與上述兩者。

投遞語意是「至少一次」（Teams 沒有冪等鍵）：
  * 每張卡片尾端有穩定「事件ID」，萬一重複收到可辨識。
  * 請求已送出但沒收到回應（讀取逾時／連線中斷）＝「不確定」：對方可能已收件，**不自動重送**，管理員可手動重送；
    只有連線建立前失敗、429、5xx 才自動退避重試。
  * 多行程／多執行緒以 DB 原子 claim（status='sending' + lease）互斥，同一列同時只有一個送件者；
    lease 過期而沒有結果的列也視為「不確定」。

Workflow URL 視同密鑰：Fernet 加密（沿用 credential_store 的金鑰檔）存 app_settings，
畫面／API／稽核／匯出一律只給「已設定」旗標，永不回傳。**每次實際投遞前都重新驗證 URL 與當下環境閘門**，
不跟隨重導、對外只允許 https:443。

安全閘門（本批：不對外送任何訊息）：
  * 預設關閉；URL 必須 https 且主機屬 Microsoft 的 Workflows 網域，防 SSRF；
    本機模擬接收端（127.0.0.1／localhost）只在環境變數 WEBIT_TEAMS_ALLOW_LOOPBACK=1 時接受（存入與每次投遞都檢查）。
  * 對外（非 loopback）要再勾 live_confirmed 才會真的送；沒勾就留在 pending。
  * 「測試送出」只對 loopback 模擬接收端可用。
  * 登入停用時，路由層一律拒絕所有 Teams 設定／測試／重送寫入（fail-closed）。
時間一律 Asia/Taipei（UTC+8，無日光節約，以固定位移實作，避免 Windows 缺 tzdata）。

資料函式（設定／網址／重送）**不自己 commit**，由路由與稽核同一交易提交；排程引擎（tick）自己 commit。
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import os
import re
import socket
import sqlite3
import ssl
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlparse

SETTING_KEY = "teams_notify"
SECRET_KEY = "teams_notify_secret"
TPE = timezone(timedelta(hours=8), "Asia/Taipei")

SEVERITIES = ("info", "warning", "critical")
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
SEV_LABEL = {"info": "輕微", "warning": "警告", "critical": "嚴重"}
#: whatsup_constants.STATE_LABEL 的中文狀態 → 嚴重度
STATE_SEVERITY = {"當機": "critical", "嚴重": "critical", "警告": "warning", "無回應": "warning", "錯誤": "warning", "輕微": "info"}

ALLOWED_HOST_SUFFIXES = (".logic.azure.com", ".powerplatform.com", ".logic.azure.cn")
CARD_LIMIT_BYTES = 27000        # 官方上限 28 KB，留餘裕
EXPIRE_HOURS = 6                # 即時告警自建立起的絕對有效期；超過沒送出就過期，不被重試／靜默延長
SUMMARY_EXPIRE_HOURS = 18
MAX_BACKOFF_SEC = 3600
TICK_LIMIT = 5                  # 單次 tick 最多送幾則（序列送件，逾時 8 秒：最壞約 80 秒，不長時間卡住共用排程執行緒）
LEASE_SEC = 120                 # 送件者 claim 一列的租約

DEFAULTS = {
    "enabled": False,
    "min_severity": "warning",
    "send_recovery": True,
    "send_incidents": True,
    "summary_daily": False,
    "summary_weekly": False,
    "summary_time": "08:00",
    "weekly_day": 0,             # 0=週一
    "quiet_start": "",           # 例 22:00；空＝不靜音
    "quiet_end": "",             # 例 07:00
    "max_attempts": 5,
    "base_backoff_sec": 60,
    "live_confirmed": False,
    "dns_guard": True,           # 送出前解析 DNS，解析到內部／保留位址就拒絕（公司走內部 proxy 時 admin 可關，需稽核）
    "base_url": "",
    "channel": "webhook",        # webhook＝Workflow 網址直送；email＝經內部 SMTP 寄信，由收件人的 Power Automate 流程轉成 Teams 私訊（正式區不能連外網時用）
    "mail_to": "",               # channel=email 的收件信箱（逗號分隔）
}

STATUS_LABEL = {"pending": "待送", "sending": "送出中", "sent": "已送出", "failed": "重試中", "dead": "放棄",
                "expired": "過期未送", "uncertain": "不確定（可能已送達）", "skipped": "不需送", "cancelled": "已取消"}
CHANNELS = ("webhook", "email")
MAIL_SUBJECT_TAG = "WEBIT3-ALERT"                # Power Automate 流程用主旨篩選這個字串，改了流程就收不到
MAIL_PREFIX = "mailto:"
# 2.060.x（使用者 11-05「看不懂重點」）：信尾的維護說明整段拿掉；排查方法寫在手冊「通知」一節。
_EMAIL_RE = re.compile(r"[^@\s,;<>]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
LIVE_TEST_WINDOW_SEC = 60                       # 「送測試訊息到已設定的網址」伺服器端速率限制：每 60 秒最多 1 次
LIVE_STATUSES = ("pending", "failed")           # 尚未送出、可被取消的狀態
log = logging.getLogger("teams_notify")
MANUAL_RETRY = ("dead", "expired", "uncertain")


class SettingsCorrupt(ValueError):
    pass


class WebhookError(ValueError):
    pass


SQL = [
    """CREATE TABLE IF NOT EXISTS teams_notify_delivery (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_key TEXT NOT NULL UNIQUE,      -- 去重鍵：來源+事件ID+種類
        kind TEXT NOT NULL,                  -- alert / recovery / incident / incident_closed / summary_daily / summary_weekly / test
        source_ref TEXT,                     -- 例 wug:<裝置ID>、inc:7
        severity TEXT,
        title TEXT,
        payload_json TEXT NOT NULL,          -- Adaptive Card（不含 URL）
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_try_at TEXT,
        lease_until TEXT,                    -- status='sending' 時的租約到期
        claim_token TEXT,                    -- fencing：完成更新必須帶同一個 token
        depends_on TEXT,                     -- 恢復／結案：對應告警／開立那一列的 event_key，它 sent 了才送
        expires_at TEXT,                     -- 絕對有效期（建立時固定）
        last_http INTEGER,
        last_error TEXT,
        created_at TEXT NOT NULL,
        sent_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_teams_deliv_status ON teams_notify_delivery(status, next_try_at)",
    """CREATE TABLE IF NOT EXISTS teams_notify_state (
        ref TEXT PRIMARY KEY,                -- 裝置ID，或 __cursor_wug__／__cursor_inc__
        last_sev TEXT,
        notified INTEGER NOT NULL DEFAULT 0, -- 已為這個裝置排過告警（恢復是否值得排的依據；是否真送達另看 depends_on）
        cursor_id INTEGER)""",
]


def ensure(conn: sqlite3.Connection) -> None:
    for stmt in SQL:
        conn.execute(stmt)


def now_taipei() -> datetime:
    """Asia/Taipei 現在時間（naive，供字串比較與存檔）。"""
    return datetime.now(TPE).replace(tzinfo=None)


def _ts(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


# ── 設定 ───────────────────────────────────────────────────────────────
def load_settings(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    cfg = dict(DEFAULTS)
    if row and row[0]:
        try:
            saved = json.loads(row[0]) or {}
        except (ValueError, TypeError) as exc:
            raise SettingsCorrupt(f"Teams 通知設定讀不出來（內容壞掉，不是沒設定）：{type(exc).__name__}") from exc
        cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
    return cfg


def hhmm(raw, default: str = "") -> tuple[int, int] | None:
    t = (raw or "").strip() or default
    if not t:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"時間格式要 HH:MM（收到「{t}」）")
    return int(m.group(1)), int(m.group(2))


def normalize_origin(raw: str) -> str:
    """base_url 只收「可信 origin」：http(s)://主機[:埠]，不得有路徑、查詢、片段、帳密。空字串＝不放連結。"""
    t = (raw or "").strip()
    if not t:
        return ""
    try:
        p = urlparse(t)
        port = p.port
    except ValueError as exc:
        raise ValueError("系統網址格式不正確") from exc
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("系統網址要是 http:// 或 https:// 開頭的主機位址")
    if p.username or p.password or p.query or p.fragment or p.path not in ("", "/") or p.params:
        raise ValueError("系統網址只能填到主機與埠（例 http://10.99.0.10:3000），不可有路徑、查詢或帳密")
    host = p.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"{p.scheme}://{host}" + (f":{port}" if port else "")


def validate(cfg: dict) -> dict:
    """驗證並正規化；不合法丟 ValueError（API 轉 400，不默默改值）。"""
    out = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    if out["min_severity"] not in SEVERITIES:
        raise ValueError("嚴重度門檻只能是 info／warning／critical")
    hhmm(out["summary_time"], "08:00")
    qs, qe = hhmm(out["quiet_start"]), hhmm(out["quiet_end"])
    if bool(qs) != bool(qe):
        raise ValueError("靜默時段要同時填開始與結束，或兩個都留空")
    try:
        out["max_attempts"] = int(out["max_attempts"])
        out["base_backoff_sec"] = int(out["base_backoff_sec"])
        out["weekly_day"] = int(out["weekly_day"])
    except (TypeError, ValueError) as exc:
        raise ValueError("重試次數、退避秒數、每週摘要星期都要是整數") from exc
    if not 1 <= out["max_attempts"] <= 10:
        raise ValueError("重試上限要在 1～10 之間")
    if not 5 <= out["base_backoff_sec"] <= 3600:
        raise ValueError("退避起始秒數要在 5～3600 之間")
    if not 0 <= out["weekly_day"] <= 6:
        raise ValueError("每週摘要星期要在 0（週一）～6（週日）")
    for k in ("enabled", "send_recovery", "send_incidents", "summary_daily", "summary_weekly", "live_confirmed", "dns_guard"):
        out[k] = bool(out[k])
    out["base_url"] = normalize_origin(out["base_url"])
    if out["channel"] not in CHANNELS:
        raise ValueError("投遞方式只能是 webhook（Workflow 網址）或 email（內部寄信）")
    addrs = [a.strip() for a in re.split(r"[,;\s]+", str(out["mail_to"] or "")) if a.strip()]
    bad = [a for a in addrs if not _EMAIL_RE.fullmatch(a)]
    if bad:
        raise ValueError(f"收件信箱格式不正確：{bad[0]}")
    if len(addrs) > 10:
        raise ValueError("收件信箱最多 10 個")
    out["mail_to"] = ",".join(addrs)
    out["summary_time"] = (out["summary_time"] or "08:00").strip()
    return out


def reset_tracking(conn: sqlite3.Connection, wug: bool = True, inc: bool = True) -> None:
    """停用（或關掉事件通知）時清游標與裝置狀態：下次啟用當成第一次，只記目前位置，**不補發停用期間的歷史**。
    裝置狀態（last_sev／notified）一併清，否則停用期間已恢復的裝置會讓之後的同級告警被誤判成重複。"""
    if not _has(conn, "teams_notify_state"):
        return
    if wug:
        conn.execute("DELETE FROM teams_notify_state WHERE ref != '__cursor_inc__'")
    if inc:
        conn.execute("DELETE FROM teams_notify_state WHERE ref = '__cursor_inc__'")


#: 種類 → 對應的旗標（旗標關掉＝這種通知不該再送）
KIND_FLAG = {"recovery": "send_recovery", "incident": "send_incidents", "incident_closed": "send_incidents",
             "summary_daily": "summary_daily", "summary_weekly": "summary_weekly"}


def cancel_pending(conn: sqlite3.Connection, kinds: tuple[str, ...] | None = None, reason: str = "已停用，取消未送出的通知") -> int:
    """把尚未送出的列（pending／failed）標為 cancelled（畫面看得到）；重新啟用不會補送。kinds=None＝全部（不含測試列）。"""
    if not _has(conn, "teams_notify_delivery"):
        return 0
    q = "UPDATE teams_notify_delivery SET status='cancelled', next_try_at=NULL, last_error=? WHERE status IN ('pending','failed') AND kind NOT IN ('test','live_test')"
    args: list = [reason]
    if kinds:
        q += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += list(kinds)
    return conn.execute(q, args).rowcount


def save_settings(conn: sqlite3.Connection, cfg: dict) -> dict:
    """存設定。**不 commit**（路由與稽核同一交易提交）。
    停用或關掉某類別時：清游標／狀態（重啟不補發歷史），並把該類殘留的 pending／failed 標為 cancelled（重啟不補送佇列）。"""
    out = validate(cfg)
    try:
        old = load_settings(conn)
    except SettingsCorrupt:
        old = dict(DEFAULTS)
    if not out["enabled"]:
        reset_tracking(conn)
        cancel_pending(conn)
    else:
        if not out["send_incidents"]:
            reset_tracking(conn, wug=False)
        for flag in ("send_recovery", "send_incidents", "summary_daily", "summary_weekly"):
            if old.get(flag) and not out[flag]:
                cancel_pending(conn, tuple(k for k, f in KIND_FLAG.items() if f == flag), "已關閉此類通知，取消未送出的通知")
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (SETTING_KEY, json.dumps(out, ensure_ascii=False)))
    return out


# ── Workflow URL（密鑰）──────────────────────────────────────────────
_CTRL_RE = re.compile(r"[\s\x00-\x1f\x7f]")
_HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")   # 無連續點、label 前後不是連字號


def loopback_allowed() -> bool:
    return os.environ.get("WEBIT_TEAMS_ALLOW_LOOPBACK") == "1"


def is_loopback_url(url: str) -> bool:
    try:
        return (urlparse(url).hostname or "").lower() in ("127.0.0.1", "localhost", "::1")
    except ValueError:
        return False


def validate_webhook_url(url: str) -> str:
    """只接受 Teams Workflows 的 https:443 網址（防 SSRF）；本機模擬接收端要環境變數明開。
    **存入與每次實際投遞都呼叫**，所以環境變數移除後既存的 loopback 網址不會再被送。"""
    url = url or ""
    if not url.isascii() or _CTRL_RE.search(url) or "\\" in url:
        raise WebhookError("網址不可含空白、控制字元、反斜線或非 ASCII 字元")
    try:
        p = urlparse(url)
        port = p.port
    except ValueError as exc:
        raise WebhookError("網址格式不正確（埠必須是 1～65535 的數字）") from exc
    host = (p.hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        if loopback_allowed() and p.scheme in ("http", "https"):
            return url
        raise WebhookError("本機模擬接收端未開放（WEBIT_TEAMS_ALLOW_LOOPBACK 未設定）")
    if p.scheme != "https" or not host:
        raise WebhookError("Workflow 網址必須是 https")
    if not _HOST_RE.fullmatch(host):
        raise WebhookError("網址的主機名稱格式不正確")
    if port not in (None, 443):
        raise WebhookError("Workflow 網址的埠只能是 443")
    if p.username or p.password:
        raise WebhookError("網址不可夾帶帳密")
    if not any(host.endswith(s) for s in ALLOWED_HOST_SUFFIXES):
        raise WebhookError("網址不是 Teams Workflows 的網域（logic.azure.com／powerplatform.com）")
    return url


def _bad_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%")[0])
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return (a.is_loopback or a.is_private or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified or not a.is_global)


def resolve_checked(host: str, port: int, guard: bool = True) -> list[str]:
    """解析 A／AAAA；guard 時只要任一結果是 loopback／private／link-local／multicast／reserved／unspecified 就拒絕。
    回「已驗證的 IP 清單」，連線時直接用這些 IP（不再解析第二次），避免驗證後被 DNS rebinding 換掉。
    解析失敗丟 socket.gaierror（連線建立前的失敗，可安全重試）。"""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips = []
    for fam, _t, _p, _c, sa in infos:
        ip = sa[0]
        if guard and _bad_ip(ip):
            raise WebhookError("網址解析到內部／保留位址，已拒絕（DNS 內部位址檢查）")
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise socket.gaierror("no address")
    return ips


def _fernet(key_path: str | None):
    import credential_store
    return credential_store._fernet(key_path or credential_store.KEY_PATH_DEFAULT)


def set_webhook(conn: sqlite3.Connection, url: str, key_path: str | None = None) -> None:
    """設定 Workflow 網址（加密）。**不 commit**。"""
    url = validate_webhook_url(url)
    token = _fernet(key_path).encrypt(url.encode()).decode()
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (SECRET_KEY, token))


def clear_webhook(conn: sqlite3.Connection) -> None:
    """清除網址。**不 commit**。"""
    conn.execute("DELETE FROM app_settings WHERE key = ?", (SECRET_KEY,))


def webhook_is_set(conn: sqlite3.Connection) -> bool:
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SECRET_KEY,)).fetchone()
    return bool(r and r[0])


def get_webhook(conn: sqlite3.Connection, key_path: str | None = None) -> str | None:
    """**唯一會回明文網址的入口**，只給 transport 用，不寫入任何地方。解不開回 None。"""
    from cryptography.fernet import InvalidToken
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SECRET_KEY,)).fetchone()
    if not r or not r[0]:
        return None
    try:
        return _fernet(key_path).decrypt(r[0].encode()).decode()
    except (InvalidToken, ValueError):
        return None


def public_settings(conn: sqlite3.Connection) -> dict:
    """給畫面的設定：網址只回「已設定」旗標。"""
    cfg = load_settings(conn)
    return {**cfg, "webhook_set": webhook_is_set(conn)}


def smtp_settings(conn: sqlite3.Connection) -> dict:
    """沿用「寄信設定（SMTP）」那一份（vuln_notify），不另存一套主機。讀不出來當成沒設定（留 log，畫面會顯示缺 SMTP 主機）。"""
    import vuln_notify
    try:
        return vuln_notify.load_settings(conn)
    except vuln_notify.SettingsCorrupt as exc:
        log.error("Teams 通知：寄信設定讀不出來（%s），寄信模式視為未設定、不寄", type(exc).__name__)
        return {}


def smtp_missing(conn: sqlite3.Connection) -> list[str]:
    import vuln_notify
    return vuln_notify.check_settings(smtp_settings(conn))


def target(conn: sqlite3.Connection, cfg: dict, key_path: str | None = None) -> str | None:
    """投遞目的地：webhook＝Workflow 網址（明文，只給 transport）；email＝mailto:收件者。沒設定回 None。"""
    if cfg.get("channel") == "email":
        return (MAIL_PREFIX + cfg["mail_to"]) if cfg.get("mail_to") else None
    return get_webhook(conn, key_path)


def is_mail(url: str | None) -> bool:
    return bool(url) and url.startswith(MAIL_PREFIX)


def missing(conn: sqlite3.Connection, cfg: dict | None = None) -> list[str]:
    if cfg is None:
        try:
            cfg = load_settings(conn)
        except SettingsCorrupt:
            cfg = dict(DEFAULTS)
    out = []
    if cfg.get("channel") == "email":
        if not cfg.get("mail_to"):
            out.append("收件信箱")
        out += [f"寄信設定的{m}" for m in smtp_missing(conn)]
    elif not webhook_is_set(conn):
        out.append("Workflow 網址")
    return out


_URL_RE = re.compile(r"https?://\S+", re.I)
_TOKEN_RE = re.compile(r"(?i)(sig|token|key|secret|code)=[^\s&;,]+|[A-Za-z0-9_\-%]{24,}")


def scrub(text: str | None, url: str | None = None) -> str:
    """錯誤摘要：把任何網址（含實際 Workflow URL 與其片段）抹掉再存。"""
    t = str(text or "")
    if url:
        t = t.replace(url, "[webhook]")
        try:
            pu = urlparse(url)
            bits = [pu.hostname or ""] + [seg for seg in pu.path.split("/") if seg] + [v for _, v in parse_qsl(pu.query)]
        except ValueError:
            bits = []
        for b in sorted({b for b in bits if len(b) >= 3}, key=len, reverse=True):
            t = t.replace(b, "[x]")
    t = _URL_RE.sub("[url]", t)
    t = _TOKEN_RE.sub("[token]", t)
    return t[:300]


# ── 卡片 ───────────────────────────────────────────────────────────────
_MD_SPECIAL = re.compile(r"([\\`*_\[\]()<>~#|!{}])")


def md_escape(text) -> str:
    """Teams 卡片文字支援基本 Markdown：可控文字（裝置名稱、問題、群組、事件標題…）送入前轉義，
    避免被當成連結／粗斜體／標籤；@ 後插零寬字元避免 @mention。換行壓成空白。"""
    t = re.sub(r"\s+", " ", str(text if text is not None else "")).strip()
    t = _MD_SPECIAL.sub(r"\\\1", t)
    t = re.sub(r"^([-+])", r"\\\1", t)                  # 行首 - / + 會變清單
    t = re.sub(r"^(\d+)([.])", r"\1\\\2", t)             # 行首「1.」會變編號清單
    return t.replace("@", "@\u200b")


def _card(title: str, color: str, facts: list[tuple[str, str]], text: str | None = None, link: str | None = None) -> dict:
    e = md_escape
    body: list[dict] = [{"type": "TextBlock", "text": e(title)[:240], "weight": "Bolder", "size": "Medium", "color": color, "wrap": True}]
    if text:
        body.append({"type": "TextBlock", "text": e(text)[:600], "wrap": True})
    if facts:
        body.append({"type": "FactSet", "facts": [{"title": e(k)[:60], "value": e(v)[:400]} for k, v in facts[:25]]})
    content: dict = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.2", "body": body}
    if link:
        content["actions"] = [{"type": "Action.OpenUrl", "title": "開啟系統", "url": link}]
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": content}]}


def _with_event_id(card: dict, event_key: str) -> dict:
    """卡片尾端放穩定事件ID（至少一次投遞下，讓人辨識重複）。"""
    card["attachments"][0]["content"]["body"].append(
        {"type": "TextBlock", "text": md_escape(f"事件ID：{event_key}"), "isSubtle": True, "size": "Small", "wrap": True})
    return card


_HAS_STAMP = re.compile(r"^(?:\S+ )?(?:\S+ )?(?:\d{4}-)?\d{2}-\d{2} \d{2}:\d{2}")   # 開頭（或燈號＋標題之後）已帶日期時間才不補


def _stamp(card: dict, title: str, now: datetime) -> tuple[dict, str]:
    """2.060.19（S6，使用者 11-11）：每則第一行開頭帶日期時間（MM-DD HH:MM），已經有日期時間的不重複加。
    只改卡片文字；佇列的 title 欄不動（畫面與去重沿用）。"""
    st = f"{now:%m-%d %H:%M} "
    try:
        body = card["attachments"][0]["content"]["body"]
        b0 = next(b for b in body if b.get("type") == "TextBlock")
        if not _HAS_STAMP.search(b0.get("text") or ""):
            b0["text"] = st + (b0.get("text") or "")
    except (KeyError, IndexError, TypeError, StopIteration):
        pass                                      # 不是 Adaptive Card 格式（理論上不會）＝不加，照原樣送
    return card, title


def _fit(card: dict) -> dict:
    """卡片 >27KB 就砍 FactSet 到塞得下（官方 28KB 上限）。"""
    while len(json.dumps(card, ensure_ascii=False).encode()) > CARD_LIMIT_BYTES:
        body = card["attachments"][0]["content"]["body"]
        fs = [b for b in body if b["type"] == "FactSet"]
        if not fs or len(fs[0]["facts"]) <= 1:
            break
        fs[0]["facts"] = fs[0]["facts"][: len(fs[0]["facts"]) // 2]
    return card


def _link(cfg: dict, path: str) -> str | None:
    """連結只用設定裡驗證過的可信 origin + 程式內固定路徑。"""
    try:
        base = normalize_origin(cfg.get("base_url") or "")
    except ValueError:
        return None
    return f"{base}{path}" if base else None


def severity_of_state(state: str | None) -> str:
    return STATE_SEVERITY.get((state or "").strip(), "warning")


def _outage(down_at: str | None, up_at: str | None) -> str:
    """斷線時長（「1 小時 5 分」）；算不出來回空字串。"""
    try:
        a, b = datetime.fromisoformat(str(down_at)), datetime.fromisoformat(str(up_at))
    except (TypeError, ValueError):
        return ""
    m = max(0, int((b - a).total_seconds() // 60))
    return f"{m // 60} 小時 {m % 60} 分" if m >= 60 else f"{m} 分"


def alert_card(cfg: dict, ev: dict, recovery: bool = False, demo: bool = False) -> dict:
    """WhatsUp 斷線／恢復（新格式，見 notice_fmt）：第一行燈號＋級別＋什麼事＋台數，一台一行。
    恢復的那行帶斷線時長（ev['down_at']：斷線開始時間，由 enqueue_alerts 帶入；查不到就不寫）。"""
    import notice_fmt as nf
    sev = severity_of_state(ev.get("new_state") or ev.get("old_state"))
    prefix = "【示範】" if demo else ""
    name = ev.get("name") or ev.get("device_id")
    if recovery:
        first = nf.head(nf.EMOJI["ok"], f"{prefix}恢復", "WhatsUp 斷線已恢復", 1)
        dur = _outage(ev.get("down_at"), ev.get("at"))
        line = f"{name}｜{ev.get('ip') or '—'}｜原狀態 {ev.get('old_state') or '—'}｜{ev.get('at') or '—'} 恢復" + (f"｜斷線 {dur}" if dur else "")
        color = "Good"
    else:
        first = nf.head(nf.EMOJI["critical" if sev == "critical" else "warning"], f"{prefix}{SEV_LABEL[sev]}", "WhatsUp 斷線", 1)
        line = f"{name}｜{ev.get('ip') or '—'}｜{ev.get('new_state') or '—'}｜{ev.get('problem') or '—'}｜{ev.get('at') or '—'} 開始"
        color = "Attention" if sev == "critical" else "Warning"
    return _fit(nf.card(first, [line], link=_link(cfg, "/v2/incident"), color=color))


# ── 資料來源 → 佇列 ────────────────────────────────────────────────────
def _enqueue(conn, event_key, kind, source_ref, sev, title, card, now: datetime, depends_on: str | None = None) -> bool:
    """去重寫入：同 event_key 已存在就不寫。回是否新增。卡片尾端加事件ID；有效期在此固定。"""
    hours = SUMMARY_EXPIRE_HOURS if kind.startswith("summary") else EXPIRE_HOURS
    card, title = _stamp(card, title, now)
    card = _fit(_with_event_id(card, event_key))
    cur = conn.execute(
        "INSERT OR IGNORE INTO teams_notify_delivery (event_key, kind, source_ref, severity, title, payload_json, status, attempts, next_try_at, "
        "depends_on, expires_at, created_at) VALUES (?,?,?,?,?,?, 'pending', 0, ?, ?, ?, ?)",
        (event_key, kind, source_ref, sev, (title or "")[:200], json.dumps(card, ensure_ascii=False), _ts(now), depends_on,
         _ts(now + timedelta(hours=hours)), _ts(now)))
    return cur.rowcount > 0


def _cursor(conn, ref: str) -> int | None:
    r = conn.execute("SELECT cursor_id FROM teams_notify_state WHERE ref = ?", (ref,)).fetchone()
    return r[0] if r else None


def _set_cursor(conn, ref: str, v: int) -> None:
    conn.execute("INSERT INTO teams_notify_state (ref, cursor_id) VALUES (?, ?) ON CONFLICT(ref) DO UPDATE SET cursor_id = excluded.cursor_id", (ref, v))


def enqueue_alerts(conn: sqlite3.Connection, cfg: dict, now: datetime) -> int:
    """把 whatsup_alert_history 新增的變化轉成待送通知。第一次啟用只記下目前位置，不補發歷史。
    恢復通知綁定「該裝置最近一則告警」（depends_on）：那則真的 sent 才會送恢復。"""
    if not _has(conn, "whatsup_alert_history"):
        return 0
    ensure(conn)
    top = conn.execute("SELECT COALESCE(MAX(id),0) FROM whatsup_alert_history").fetchone()[0]
    cur = _cursor(conn, "__cursor_wug__")
    if cur is None:
        _set_cursor(conn, "__cursor_wug__", top)
        conn.commit()
        return 0
    n = 0
    thr = SEV_RANK[cfg["min_severity"]]
    for h in conn.execute("SELECT * FROM whatsup_alert_history WHERE id > ? ORDER BY id", (cur,)).fetchall():
        ev = dict(h)
        did = ev["device_id"]
        st = conn.execute("SELECT last_sev, notified FROM teams_notify_state WHERE ref = ?", (did,)).fetchone()
        last_sev, queued = (st[0], bool(st[1])) if st else (None, False)
        if ev["event"] in ("new", "changed"):
            sev = severity_of_state(ev.get("new_state"))
            if SEV_RANK[sev] >= thr and sev != last_sev:
                if _enqueue(conn, f"wug:{ev['id']}:{ev['event']}", "alert", f"wug:{did}", sev, f"{SEV_LABEL[sev]}告警 {ev.get('name')}",
                            alert_card(cfg, ev), now):
                    n += 1
                conn.execute("INSERT INTO teams_notify_state (ref, last_sev, notified) VALUES (?, ?, 1) "
                             "ON CONFLICT(ref) DO UPDATE SET last_sev = excluded.last_sev, notified = 1", (did, sev))
        elif ev["event"] == "cleared":
            down = conn.execute("SELECT at FROM whatsup_alert_history WHERE device_id = ? AND id < ? AND event IN ('new','changed') "
                                "ORDER BY id DESC LIMIT 1", (did, ev["id"])).fetchone()
            ev["down_at"] = down[0] if down else None
            if queued and cfg["send_recovery"]:
                dep = conn.execute("SELECT event_key FROM teams_notify_delivery WHERE kind='alert' AND source_ref=? ORDER BY id DESC LIMIT 1",
                                   (f"wug:{did}",)).fetchone()
                if dep and _enqueue(conn, f"wug:{ev['id']}:cleared", "recovery", f"wug:{did}", "info", f"已恢復 {ev.get('name')}",
                                    alert_card(cfg, ev, recovery=True), now, depends_on=dep[0]):
                    n += 1
            conn.execute("INSERT INTO teams_notify_state (ref, last_sev, notified) VALUES (?, NULL, 0) "
                         "ON CONFLICT(ref) DO UPDATE SET last_sev = NULL, notified = 0", (did,))
        _set_cursor(conn, "__cursor_wug__", ev["id"])
    conn.commit()
    return n


def enqueue_incidents(conn: sqlite3.Connection, cfg: dict, now: datetime) -> int:
    """正式（非演練）異常事件：開立與結案各通知一次；結案要等開立那一則 sent 才送。"""
    if not cfg["send_incidents"] or not _has(conn, "incident_event"):
        return 0
    cols = {r[1] for r in conn.execute("PRAGMA table_info(incident_event)")}
    if "mode" not in cols:
        return 0
    ensure(conn)
    top = conn.execute("SELECT COALESCE(MAX(id),0) FROM incident_event").fetchone()[0]
    cur = _cursor(conn, "__cursor_inc__")
    if cur is None:
        _set_cursor(conn, "__cursor_inc__", top)
        conn.commit()
        return 0
    n = 0
    sel = "SELECT id, type, title, started_at, status, closed_at, mode FROM incident_event"
    extra = " AND COALESCE(category,'incident') = 'incident'" if "category" in cols else ""
    for e in conn.execute(sel + " WHERE id > ? AND mode = 'formal'" + extra + " ORDER BY id", (cur,)).fetchall():
        facts = [("類型", e["type"]), ("開始時間", e["started_at"])]
        if _enqueue(conn, f"inc:{e['id']}:open", "incident", f"inc:{e['id']}", "critical", f"事件開立 {e['title']}",
                    _card(f"事件開立：{e['title']}", "Attention", facts, link=_link(cfg, f"/v2/incident/{e['id']}")), now):
            n += 1
        _set_cursor(conn, "__cursor_inc__", e["id"])
    # 已排過開立、現在已結案、還沒排結案
    for e in conn.execute(sel + " WHERE status = 'closed' AND mode = 'formal' AND id IN "
                          "(SELECT CAST(substr(event_key, 5, length(event_key)-9) AS INTEGER) FROM teams_notify_delivery WHERE event_key LIKE 'inc:%:open')").fetchall():
        if _enqueue(conn, f"inc:{e['id']}:closed", "incident_closed", f"inc:{e['id']}", "info", f"事件結案 {e['title']}",
                    _card(f"事件結案：{e['title']}", "Good", [("類型", e["type"]), ("結案時間", e["closed_at"] or "")],
                          link=_link(cfg, f"/v2/incident/{e['id']}")), now, depends_on=f"inc:{e['id']}:open"):
            n += 1
    conn.commit()
    return n


# ── 摘要 ───────────────────────────────────────────────────────────────
def _relay_alert_facts(conn, now: datetime, since_s: str, label: str, nums: dict, facts: list) -> None:
    """2.066.x（.14 每日摘要寫「目前告警 0」實際有告警）：4.1 告警事件（中繼）有啟用時，跟 1.1 今日概況同一份
    （v2_today._alerts_now，同一個 Q9 範圍）：需要注意 N｜仍在告警 M；今日新增／恢復也讀 alert_event 同範圍。
    心跳中斷或從沒收到資料＝監控盲區，不給數字（給了會被讀成沒事）。只讀。"""
    import v2_today
    import wug_alert as wa
    a = v2_today._alerts_now(conn, now)
    att = a.get("attention") or {}
    st = a.get("status")
    if st == "error" or st == "disabled":
        facts.append(("目前告警", a.get("label") or "無法計算"))
        nums["alerts_unavailable"] = True
        return
    blind = st == "uncollected" or bool(att.get("blind"))
    if blind:
        nums["alerts_blind"] = True
        facts.append(("目前告警", "監控盲區，數字不可信（" + ("還沒收到任何 WhatsUp 告警資料" if st == "uncollected"
                                                          else f"告警資料中斷，最後收到 {a.get('as_of') or '不明'}") + "）"))
    else:
        nums["alerts_attention"], nums["alerts_now"] = att.get("n", 0), a["n"]
        facts.append(("目前告警", f"需要注意 {nums['alerts_attention']}｜仍在告警 {nums['alerts_now']}"))
    if wa._has(conn, "alert_event"):
        cfg = wa.load_settings(conn)
        new = [wa._row(r) for r in conn.execute("SELECT * FROM alert_event WHERE tier != 'maint' AND started_at >= ?", (since_s,))]
        rec = [wa._row(r) for r in conn.execute("SELECT * FROM alert_event WHERE tier != 'maint' AND ended_at IS NOT NULL AND ended_at >= ?", (since_s,))]
        nums["alerts_new"] = len(v2_today.scope_filter(new, cfg)[0])
        nums["alerts_cleared"] = len(v2_today.scope_filter(rec, cfg)[0])
        # 審查 P3：恢復＝這段期間恢復的（含之前就開始的），跟首頁「今天發生過」（今天開始的）口徑不同，標題寫清楚；
        # 監控盲區時這兩個數字只到中斷那一刻，標「監控中斷前」，不能當成今天的全貌
        rec_lab = "今日恢復（含昨日起）" if label == "今日" else f"{label}恢復（含更早開始的）"
        facts.append((f"{label}新增／{rec_lab}" + ("（監控中斷前）" if blind else ""), f"{nums['alerts_new']}／{nums['alerts_cleared']}"))


def build_summary(conn: sqlite3.Connection, cfg: dict, period: str, now: datetime | None = None, demo: bool = False) -> tuple[str, dict, dict]:
    """period=daily|weekly。回 (標題, 卡片, 原始數字)。只讀；表不存在的來源就略過並在卡片寫「無資料」。"""
    now = now or now_taipei()
    since = (now.replace(hour=0, minute=0, second=0, microsecond=0) if period == "daily"
             else now - timedelta(days=7))
    since_s = _ts(since)
    label = "今日" if period == "daily" else "本週（近 7 天）"
    nums: dict = {"period": period, "since": since_s}
    facts: list[tuple[str, str]] = []
    import night_alerts
    if night_alerts.relay_enabled(conn) is not False:
        try:
            _relay_alert_facts(conn, now, since_s, label, nums, facts)
        except sqlite3.Error as exc:     # 告警那一段讀不到：在摘要裡寫出原因（不吞掉），其他段照樣組
            nums["alerts_error"] = f"{type(exc).__name__}: {exc}"
            facts.append(("目前告警", f"讀取失敗：{type(exc).__name__}: {exc}"[:200]))
    elif _has(conn, "whatsup_alerts"):
        cur = {r[0]: r[1] for r in conn.execute("SELECT state, COUNT(*) FROM whatsup_alerts GROUP BY state")}
        crit = sum(c for s, c in cur.items() if severity_of_state(s) == "critical")
        nums["alerts_now"] = sum(cur.values())
        nums["alerts_now_critical"] = crit
        facts.append(("目前告警中", f"{nums['alerts_now']}（嚴重 {crit}）"))
        ev = {r[0]: r[1] for r in conn.execute(
            "SELECT event, COUNT(*) FROM whatsup_alert_history WHERE at >= ? GROUP BY event", (since_s,))}
        nums["alerts_new"], nums["alerts_cleared"] = ev.get("new", 0), ev.get("cleared", 0)
        facts.append((f"{label}新增／恢復", f"{nums['alerts_new']}／{nums['alerts_cleared']}"))
    else:
        facts.append(("告警", "無資料（WhatsUp 尚未啟用）"))
    if _has(conn, "incident_event"):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(incident_event)")}
        cond = " AND mode = 'formal'" if "mode" in cols else ""
        nums["incidents_opened"] = conn.execute(f"SELECT COUNT(*) FROM incident_event WHERE started_at >= ?{cond}", (since_s,)).fetchone()[0]
        nums["incidents_open"] = conn.execute(f"SELECT COUNT(*) FROM incident_event WHERE status = 'open'{cond}").fetchone()[0]
        facts.append((f"{label}新開事件／未結案", f"{nums['incidents_opened']}／{nums['incidents_open']}"))
    try:
        import daily_digest
        if daily_digest.tables_exist(conn):
            d = daily_digest.diff_for(conn, None, daily_digest.load_settings(conn))
            if d.get("date"):
                nums["changes_date"] = d["date"]
                nums["changes"] = d["counts"]
                # 審查 P2-1：總數＝change_total（收集中斷不算異動），收集中斷另列一行
                tot = daily_digest.change_total(d["counts"])
                nums["changes_total"] = tot
                facts.append((f"資產異動（{d['date']}）", f"{tot} 項" + ("：" + "、".join(
                    f"{lab} {d['counts'][k]}" for k, lab in daily_digest.CATEGORIES
                    if d["counts"].get(k) and k not in daily_digest.STATE_KEYS) if tot else "")))
                facts.append(("收集中斷", f"{int(d['counts'].get('collect_gap', 0) or 0)} 台"))
    except (ValueError, sqlite3.Error) as exc:
        facts.append(("資產異動", f"讀不到（{type(exc).__name__}）"))
    if _has(conn, "teams_notify_delivery"):
        bad = conn.execute("SELECT COUNT(*) FROM teams_notify_delivery WHERE status IN ('dead','expired','uncertain') AND kind NOT IN ('test','live_test') AND created_at >= ?", (since_s,)).fetchone()[0]
        nums["delivery_bad"] = bad
        facts.append((f"{label}通知送不出去／不確定", str(bad)))
    title = f"{'【示範】' if demo else ''}{'每日' if period == 'daily' else '每週'}摘要 {now.strftime('%Y-%m-%d')}"
    return title, _fit(_card(title, "Accent", facts, link=_link(cfg, "/v2"))), nums


class read_only:
    """預覽／GET 路徑用：這個區塊內這條連線 query_only，任何寫入都會被 SQLite 擋下（OperationalError），而不是只靠不寫的約定。"""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("PRAGMA query_only = ON")
        return self.conn

    def __exit__(self, *exc):
        self.conn.execute("PRAGMA query_only = OFF")
        return False


def week_key(now: datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def enqueue_summaries(conn: sqlite3.Connection, cfg: dict, now: datetime) -> list[str]:
    """到點、且這一天／這一週還沒排過才排。key 唯一＝重送不重發；送失敗由佇列重試，不重排新的。"""
    ensure(conn)
    t = hhmm(cfg["summary_time"], "08:00")
    if (now.hour, now.minute) < t:
        return []
    out = []
    plans = []
    if cfg["summary_daily"]:
        plans.append(("daily", f"sum:daily:{now.strftime('%Y-%m-%d')}", "summary_daily"))
    if cfg["summary_weekly"] and now.weekday() == cfg["weekly_day"]:
        plans.append(("weekly", f"sum:weekly:{week_key(now)}", "summary_weekly"))
    for period, key, kind in plans:
        title, card, _ = build_summary(conn, cfg, period, now)
        if _enqueue(conn, key, kind, key, "info", title, card, now):
            out.append(key)
    conn.commit()
    return out


# ── 投遞 ───────────────────────────────────────────────────────────────
def http_transport(url: str, payload: bytes, timeout: int = 8, dns_guard: bool = True) -> tuple:
    """真實 transport（POST JSON）。回 (HTTP 狀態或 None, 錯誤摘要, phase)。
    phase='connect'＝**能證明請求尚未送出**（DNS 失敗、拒絕連線、連線建立／TLS 握手階段失敗）→ 可安全重試；
    phase='uncertain'＝其餘沒拿到回應的情形（送出後逾時、中斷…）→ 對方可能已收件，不自動重送；
    phase='refused'＝網址或 DNS 解析結果未通過驗證（不送、不計次）；phase=''＝有 HTTP 回應（含 3xx，不跟隨重導）。
    連線階段自己做：先解析並驗證 IP，再直接連驗證過的 IP（TLS 仍以原主機名驗憑證），之後的收發錯誤一律視為不確定。
    每次都重新驗證網址（含當下環境閘門）；錯誤文字不含 URL。"""
    try:
        validate_webhook_url(url)
    except WebhookError as exc:
        return None, f"網址驗證未通過：{exc}", "refused"
    pu = urlparse(url)
    host = pu.hostname or ""
    https = pu.scheme == "https"
    port = pu.port or (443 if https else 80)
    loop = host.lower() in ("127.0.0.1", "localhost", "::1")
    # ── 連線建立階段（失敗＝尚未送出）──
    sock = None
    try:
        ips = [host] if loop else resolve_checked(host, port, dns_guard)
        last: Exception | None = None
        for ip in ips:
            try:
                sock = socket.create_connection((ip, port), timeout=timeout)
                break
            except OSError as exc:
                last = exc
        if sock is None:
            raise last or OSError("connect failed")
        if https:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
    except WebhookError as exc:
        return None, str(exc), "refused"
    except (OSError, ssl.SSLError) as exc:      # DNS／拒絕連線／連線逾時／TLS 握手：請求還沒送出
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        return None, type(exc).__name__, "connect"
    # ── 收送階段（之後任何無回應＝不確定）──
    try:
        conn = (http.client.HTTPSConnection if https else http.client.HTTPConnection)(host, port, timeout=timeout)
        conn.sock = sock
        path = (pu.path or "/") + (f"?{pu.query}" if pu.query else "")
        conn.request("POST", path, body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read(512)
        code = resp.status
        conn.close()
        return code, ("" if 200 <= code < 300 else f"HTTP {code}"), ""
    except Exception as exc:  # noqa: BLE001 - 送出後的任何失敗都保守當「不確定」，不自動重送
        try:
            sock.close()
        except OSError:
            pass
        return None, type(exc).__name__, "uncertain"


def _unmd(text) -> str:
    return re.sub(r"\\(.)", r"\1", str(text or ""))


def card_to_mail(payload: dict) -> tuple[str, list[str], str | None]:
    """卡片 → (標題, 內文各行, 連結)。只讀 TextBlock／FactSet／OpenUrl，其餘忽略。"""
    try:
        content = payload["attachments"][0]["content"]
    except (KeyError, IndexError, TypeError):
        return "通知", [], None
    title, lines = "", []
    for b in content.get("body") or []:
        if b.get("type") == "TextBlock":
            t = _unmd(b.get("text"))
            if not title:
                title = t
            elif not t.startswith("事件ID："):          # 信件不放事件ID（2.060.x 新格式；Teams 卡片照舊有）
                lines.append(t)
        elif b.get("type") == "FactSet":
            lines += [f"{_unmd(f.get('title'))}：{_unmd(f.get('value'))}" for f in b.get("facts") or []]
    link = next((a.get("url") for a in content.get("actions") or [] if a.get("type") == "Action.OpenUrl"), None)
    return title or "通知", lines, link


def mail_transport(smtp: dict, url: str, payload: bytes, timeout: int = 15) -> tuple:
    """內部 SMTP 寄信（免認證 relay）。回傳格式同 http_transport：(狀態碼或 None, 錯誤摘要, phase)。
    連線／EHLO 失敗＝還沒送（connect，可重試）；SMTP 4xx＝暫時失敗（當 503 重試）；5xx／收件者被拒＝不重試（當 400）；
    送信途中斷線＝不確定（可能已收）。成功回 200。"""
    import html as _html
    import smtplib
    from email.message import EmailMessage
    to = [a for a in url[len(MAIL_PREFIX):].split(",") if a] if is_mail(url) else []
    if not to or not smtp.get("smtp_host") or not smtp.get("mail_from"):
        return None, "寄信設定或收件信箱不完整", "refused"
    try:
        card = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        card = {}
    title, lines, link = card_to_mail(card)
    msg = EmailMessage()
    msg["Subject"] = re.sub(r"[\r\n]+", " ", f"{MAIL_SUBJECT_TAG} {title}")[:200]
    msg["From"] = smtp["mail_from"]
    msg["To"] = ", ".join(to)
    # 內文不重複主旨那一行（Power Automate 流程貼的是 主旨＋內文），也不放維護說明（2.060.x）
    msg.set_content("\n".join(list(lines) + ([f"開啟系統：{link}"] if link else [])) or title, cte="base64")   # 2.060.20（S9）：不指定會走 8bit，公司 relay／Power Automate 把內文當 Big5 解成亂碼
    body = "<br>".join(_html.escape(x) for x in lines)
    if link:
        body += ("<br>" if body else "") + f'<a href="{_html.escape(link, quote=True)}">開啟系統</a>'
    body = body or _html.escape(title)
    msg.add_alternative(body, subtype="html", cte="base64")
    try:
        s = smtplib.SMTP(smtp["smtp_host"], int(smtp.get("smtp_port") or 25), timeout=timeout)
        s.ehlo()
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        return None, type(exc).__name__, "connect"
    try:
        s.send_message(msg)
    except smtplib.SMTPRecipientsRefused:
        return 400, "收件信箱被郵件主機拒收", ""
    except smtplib.SMTPResponseException as exc:
        code = exc.smtp_code
        return (503 if 400 <= code < 500 else 400), f"SMTP {code}", ""
    except (OSError, smtplib.SMTPException) as exc:
        return None, type(exc).__name__, "uncertain"
    finally:
        try:
            s.quit()
        except (OSError, smtplib.SMTPException):
            pass
    return 200, "", ""


def default_transport(conn: sqlite3.Connection, cfg: dict, url: str | None):
    """依投遞方式挑 transport；寄信設定在挑的當下讀一次。"""
    if is_mail(url):
        smtp = smtp_settings(conn)
        return lambda u, b: mail_transport(smtp, u, b)
    return lambda u, b, _g=cfg.get("dns_guard", True): http_transport(u, b, dns_guard=_g)


def in_quiet(cfg: dict, now: datetime) -> bool:
    qs, qe = hhmm(cfg.get("quiet_start")), hhmm(cfg.get("quiet_end"))
    if not qs or not qe:
        return False
    cur = (now.hour, now.minute)
    return (qs <= cur < qe) if qs <= qe else (cur >= qs or cur < qe)


def quiet_end_at(cfg: dict, now: datetime) -> datetime:
    qe = hhmm(cfg.get("quiet_end"))
    end = now.replace(hour=qe[0], minute=qe[1], second=0, microsecond=0)
    return end if end > now else end + timedelta(days=1)


def backoff_seconds(cfg: dict, attempts: int) -> int:
    return min(int(cfg["base_backoff_sec"]) * (2 ** max(0, attempts - 1)), MAX_BACKOFF_SEC)


def _retriable(http: int | None) -> bool:
    """可自動重試的 HTTP：429 與 5xx。408 請求逾時語意上對方可能已收，列為不確定不自動重送。"""
    return http is None or http == 429 or http >= 500


def _norm(res) -> tuple:
    http, err = res[0], res[1]
    phase = res[2] if len(res) > 2 else ("connect" if http is None else "")
    return http, err, phase


def _finish(conn, row_id: int, token: str, sets: str, args: tuple) -> bool:
    """fencing：完成更新一定帶 claim_token 且列仍是 sending；舊送件者晚回來（租約已過期被別人處理）就更新 0 列，不得覆寫新狀態。"""
    cur = conn.execute(f"UPDATE teams_notify_delivery SET {sets} WHERE id=? AND status='sending' AND claim_token=?", (*args, row_id, token))
    return cur.rowcount == 1


def deliver_one(conn, row, cfg: dict, url: str, transport, now: datetime, token: str | None = None) -> str:
    """送一列（呼叫端已 claim 成 sending 並給 token）。結果寫回該列；回 sent／failed／dead／uncertain／held／stale。"""
    token = token or row["claim_token"]
    rid = row["id"]
    try:
        if not is_mail(url):
            validate_webhook_url(url)        # 每次實際投遞前再驗一次（含環境閘門）
    except WebhookError as exc:
        _finish(conn, rid, token, "status='pending', lease_until=NULL, claim_token=NULL, last_error=?", (scrub(f"網址驗證未通過：{exc}"),))
        return "held"
    http, err, phase = _norm(transport(url, row["payload_json"].encode()))
    attempts = row["attempts"] + 1
    if http is not None and 200 <= http < 300:
        ok = _finish(conn, rid, token, "status='sent', attempts=?, last_http=?, last_error=NULL, sent_at=?, next_try_at=NULL, lease_until=NULL", (attempts, http, _ts(now)))
        return "sent" if ok else "stale"
    err = scrub(err or (f"HTTP {http}" if http else "未知錯誤"), url)
    if phase == "refused":
        ok = _finish(conn, rid, token, "status='pending', lease_until=NULL, claim_token=NULL, last_error=?", (err,))
        return "held" if ok else "stale"
    if (http is None and phase == "uncertain") or http == 408:
        ok = _finish(conn, rid, token, "status='uncertain', attempts=?, last_http=?, last_error=?, next_try_at=NULL, lease_until=NULL",
                     (attempts, http, err + "（請求可能已送達，不自動重送）"))
        return "uncertain" if ok else "stale"
    if not _retriable(http) or attempts >= cfg["max_attempts"]:
        ok = _finish(conn, rid, token, "status='dead', attempts=?, last_http=?, last_error=?, next_try_at=NULL, lease_until=NULL", (attempts, http, err))
        return "dead" if ok else "stale"
    nxt = now + timedelta(seconds=backoff_seconds(cfg, attempts))
    ok = _finish(conn, rid, token, "status='failed', attempts=?, last_http=?, last_error=?, next_try_at=?, lease_until=NULL", (attempts, http, err, _ts(nxt)))
    return "failed" if ok else "stale"


def _claim(conn, row_id: int, now: datetime) -> str | None:
    """DB 層原子 claim：只有 UPDATE 命中 1 列的那一方可以送，回 claim_token（失敗回 None）。
    claim 條件內含「目前設定仍是啟用」：停用提交之後，舊 tick 再也 claim 不到任何列。"""
    token = uuid.uuid4().hex
    cur = conn.execute(
        "UPDATE teams_notify_delivery SET status='sending', lease_until=?, claim_token=? WHERE id=? AND status IN ('pending','failed') "
        "AND (next_try_at IS NULL OR next_try_at <= ?) "
        "AND (kind = 'test' OR COALESCE((SELECT json_extract(value,'$.enabled') FROM app_settings WHERE key=?), 0) = 1)",
        (_ts(now + timedelta(seconds=LEASE_SEC)), token, row_id, _ts(now), SETTING_KEY))
    conn.commit()
    return token if cur.rowcount == 1 else None


def _auth_off(conn) -> bool:
    """登入停用（環境變數或系統設定）。排程沒有真實身分可核對 → 不對外送出（fail-closed）。"""
    if os.environ.get("WEBIT3_DISABLE_AUTH", "").strip() in ("1", "true", "TRUE", "yes"):
        return True
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key='auth_disabled'").fetchone()
    except sqlite3.Error as exc:
        # 查不出登入是否停用 → 一律當成停用（fail-closed），不對外送；日誌只記例外型別
        log.error("Teams 通知：無法確認登入是否停用（%s），保守視為停用、不對外送出", type(exc).__name__)
        return True
    return bool(r) and str(r[0]).strip() in ("1", "true", "yes")


def hold_reason(conn, cfg: dict, key_path: str | None = None) -> str | None:
    """目前為什麼不會對外送出（給畫面與 process_queue 共用）。None＝可以送。"""
    url = target(conn, cfg, key_path)
    if is_mail(url):
        miss = smtp_missing(conn)
        if miss:
            return f"寄信設定還缺：{'、'.join(miss)}"
    elif not url:
        return "沒有收件信箱" if cfg.get("channel") == "email" else "沒有 Workflow 網址（或解不開）"
    else:
        try:
            validate_webhook_url(url)
        except WebhookError as exc:
            return f"網址驗證未通過：{exc}"
    if is_mail(url) or not is_loopback_url(url):
        if _auth_off(conn):
            return "登入停用中：不對外送出"
        if not cfg["live_confirmed"]:
            return "尚未確認對外送出（「對外送出已確認」未勾）"
    return None


#: 依賴的那一則已經有定論（不管成敗）才輪到子通知；還在 pending／failed／sending／uncertain 就先不碰，也不占 limit
_DEP_DONE = "('sent','dead','expired','skipped','cancelled')"


def process_queue(conn: sqlite3.Connection, cfg: dict, now: datetime, transport=None, key_path: str | None = None, limit: int = TICK_LIMIT) -> dict:
    """送出到期的待送／重試列。沒設網址、網址不合法、登入停用、沒到對外許可時一律不送，列留在 pending。
    每一列送出前都重讀「目前」設定（舊 tick 不會在停用提交後繼續送），並依目前旗標過濾種類。"""
    ensure(conn)
    out = {"sent": 0, "failed": 0, "dead": 0, "expired": 0, "uncertain": 0, "skipped": 0, "cancelled": 0, "stale": 0, "held": None}
    # 租約過期還沒有結果＝送件者可能中途死掉，對方可能已收件 → 不確定（不自動重送）。舊送件者之後回來會被 fencing 擋下
    out["uncertain"] += conn.execute(
        "UPDATE teams_notify_delivery SET status='uncertain', lease_until=NULL, next_try_at=NULL, "
        "last_error=CASE WHEN kind='live_test' THEN '結果未記錄（可能已送達）' ELSE '送件者中斷，結果不明（可能已送達，不自動重送）' END "
        "WHERE status='sending' AND lease_until < ?", (_ts(now),)).rowcount
    # 父通知「不確定」期間子通知有效期不倒數（滑動延後），避免畫面先顯示子通知過期；父通知有定論後才開始倒數
    conn.execute(
        "UPDATE teams_notify_delivery SET expires_at=? WHERE status IN ('pending','failed') AND depends_on IS NOT NULL AND EXISTS "
        "(SELECT 1 FROM teams_notify_delivery p WHERE p.event_key = teams_notify_delivery.depends_on AND p.status = 'uncertain')",
        (_ts(now + timedelta(hours=EXPIRE_HOURS)),))
    out["expired"] = conn.execute(
        "UPDATE teams_notify_delivery SET status='expired', next_try_at=NULL, last_error='超過有效期仍未送出，已過期' "
        "WHERE status IN ('pending','failed') AND expires_at IS NOT NULL AND expires_at < ?", (_ts(now),)).rowcount
    conn.commit()
    out["held"] = hold_reason(conn, cfg, key_path)
    if out["held"]:
        return out
    quiet = in_quiet(cfg, now)
    rows = conn.execute(
        "SELECT r.* FROM teams_notify_delivery r LEFT JOIN teams_notify_delivery d ON d.event_key = r.depends_on "
        "WHERE r.status IN ('pending','failed') AND r.kind NOT IN ('test','live_test') AND (r.next_try_at IS NULL OR r.next_try_at <= ?) "
        f"AND (r.depends_on IS NULL OR d.id IS NULL OR d.status IN {_DEP_DONE}) ORDER BY r.id LIMIT ?", (_ts(now), limit)).fetchall()
    for r in rows:
        try:
            cur = load_settings(conn)               # 重讀目前設定（可能剛被停用／改旗標）
        except SettingsCorrupt:
            break
        if not cur["enabled"]:
            break
        flag = KIND_FLAG.get(r["kind"])
        if flag and not cur[flag]:
            conn.execute("UPDATE teams_notify_delivery SET status='cancelled', last_error='此類通知已關閉' WHERE id=? AND status IN ('pending','failed')", (r["id"],))
            conn.commit()
            out["cancelled"] += 1
            continue
        if hold_reason(conn, cur, key_path):
            break
        if quiet and r["severity"] != "critical":
            # 靜默時段：非嚴重的留著，時段結束再送（絕對有效期不延長）
            conn.execute("UPDATE teams_notify_delivery SET next_try_at=? WHERE id=? AND status IN ('pending','failed')",
                         (_ts(quiet_end_at(cur, now)), r["id"]))
            conn.commit()
            continue
        if r["depends_on"]:
            dep = conn.execute("SELECT status FROM teams_notify_delivery WHERE event_key=?", (r["depends_on"],)).fetchone()
            if dep is None or dep[0] != "sent":
                conn.execute("UPDATE teams_notify_delivery SET status='skipped', last_error='對應的告警／開立通知沒有送達，不單獨送恢復／結案（對方被人工重送成功後會一併重新排隊）' "
                             "WHERE id=? AND status IN ('pending','failed')", (r["id"],))
                conn.commit()
                out["skipped"] += 1
                continue
        token = _claim(conn, r["id"], now)
        if not token:
            continue           # 別的送件者搶先了，或剛被停用
        # claim 之後、實際送出之前：重讀 URL 與設定（enabled、各類旗標、live_confirmed、dns_guard），
        # 並用「同一次重讀」的結果送；中途被停用／輪替網址／改 dns_guard，後面的列都套用新的
        try:
            fresh = load_settings(conn)
        except SettingsCorrupt:
            fresh = None
        flag2 = KIND_FLAG.get(r["kind"])
        if fresh is None or not fresh["enabled"] or (flag2 and not fresh[flag2]):
            _finish(conn, r["id"], token, "status='cancelled', lease_until=NULL, claim_token=NULL, next_try_at=NULL, last_error='送出前確認已停用或此類通知已關閉，取消'", ())
            conn.commit()
            out["cancelled"] += 1
            if fresh is None or not fresh["enabled"]:
                break
            continue
        if hold_reason(conn, fresh, key_path):
            _finish(conn, r["id"], token, "status='pending', lease_until=NULL, claim_token=NULL", ())      # 放回待送
            conn.commit()
            break
        url = target(conn, fresh, key_path)
        row_transport = transport or default_transport(conn, fresh, url)
        res = deliver_one(conn, r, fresh, url, row_transport, now, token)
        conn.commit()
        if res in out and res != "held":
            out[res] += 1
    return out


def tick(conn: sqlite3.Connection, now: datetime | None = None, transport=None, key_path: str | None = None) -> dict:
    """排程器每分鐘叫一次。預設關閉；設定壞掉安靜略過（畫面讀設定時會講出原因）。"""
    now = now or now_taipei()
    try:
        cfg = load_settings(conn)
    except SettingsCorrupt:
        return {"skipped": "設定壞掉"}
    if not cfg["enabled"]:
        reset_tracking(conn)       # 直接改 DB 關掉的也一樣：下次啟用不補發、不補送
        cancel_pending(conn)
        conn.commit()
        return {"skipped": "未啟用"}
    ensure(conn)
    if not cfg["send_incidents"]:
        reset_tracking(conn, wug=False)
        conn.commit()
    out: dict = {"queued": enqueue_alerts(conn, cfg, now) + enqueue_incidents(conn, cfg, now)}
    out["summaries"] = enqueue_summaries(conn, cfg, now)
    out["delivery"] = process_queue(conn, cfg, now, transport, key_path)
    return out


# ── 預覽／測試／紀錄 ───────────────────────────────────────────────────
def preview(conn: sqlite3.Connection, kind: str) -> dict:
    """dry_run：只組內容回傳，**不送、不寫任何表**（連 ensure 都不呼叫）。"""
    cfg = load_settings(conn)
    if kind in ("daily", "weekly"):
        title, card, nums = build_summary(conn, cfg, kind)
        return {"title": title, "card": _with_event_id(card, f"sum:{kind}:預覽"), "numbers": nums, "demo": False}
    demo = {"device_id": "demo-1", "name": "示範設備 demo-host-01", "ip": "192.0.2.10", "old_state": "", "new_state": "當機",
            "problem": "Ping 無回應（示範）", "group_name": "示範群組", "at": _ts(now_taipei())}
    if kind == "recovery":
        demo = {**demo, "old_state": "當機", "new_state": ""}
        return {"title": "【示範】告警已恢復", "card": _with_event_id(alert_card(cfg, demo, recovery=True, demo=True), "wug:示範:cleared"), "demo": True}
    return {"title": "【示範】嚴重告警", "card": _with_event_id(alert_card(cfg, demo, demo=True), "wug:示範:new"), "demo": True}


def send_test(conn: sqlite3.Connection, transport=None, key_path: str | None = None, now: datetime | None = None) -> dict:
    """測試送出：只對本機模擬接收端（loopback）可用，且每次都重驗網址與環境閘門；真實網址一律拒絕（本批不對外送）。
    **不 commit**：投遞紀錄與稽核由路由同一交易提交。回傳 sent_out＝外部「可能已收到」（拿到回應，或送出後讀取逾時），供路由在提交失敗時如實說明。"""
    now = now or now_taipei()
    url = get_webhook(conn, key_path)
    if not url:
        return {"ok": False, "reason": "還沒設定 Workflow 網址", "sent_out": False}
    if not is_loopback_url(url):
        return {"ok": False, "reason": "測試送出目前只開放本機模擬接收端；對外送出請等確認公司頻道後再開放", "sent_out": False}
    try:
        validate_webhook_url(url)
    except WebhookError as exc:
        return {"ok": False, "reason": f"網址驗證未通過：{exc}", "sent_out": False}
    ensure(conn)
    cfg = load_settings(conn)
    import notice_fmt as nf
    card = nf.card(f"{nf.EMOJI['test']} 測試｜Teams 通知測試（不是告警）{_ts(now)[11:16]}", [], color="Accent")
    key = f"test:{now.strftime('%Y%m%d%H%M%S%f')}"
    _enqueue(conn, key, "test", "test", "info", "Teams 通知測試", card, now)
    token = uuid.uuid4().hex
    conn.execute("UPDATE teams_notify_delivery SET status='sending', lease_until=?, claim_token=? WHERE event_key=?",
                 (_ts(now + timedelta(seconds=LEASE_SEC)), token, key))
    row = conn.execute("SELECT * FROM teams_notify_delivery WHERE event_key = ?", (key,)).fetchone()
    seen = {}

    def spy(u, b):
        res = (transport or (lambda uu, bb: http_transport(uu, bb, dns_guard=cfg.get("dns_guard", True))))(u, b)
        # 有 HTTP 回應，或送出後沒回應（可能已送達）都算「外部可能已收到」
        seen["maybe"] = res[0] is not None or (len(res) > 2 and res[2] == "uncertain")
        return res

    res = deliver_one(conn, row, {**cfg, "max_attempts": 1}, url, spy, now, token)
    r2 = conn.execute("SELECT last_http, last_error FROM teams_notify_delivery WHERE event_key = ?", (key,)).fetchone()
    return {"ok": res == "sent", "http": r2[0], "reason": r2[1], "sent_out": bool(seen.get("maybe"))}


def webhook_host(conn: sqlite3.Connection, key_path: str | None = None) -> str | None:
    """只回網域（給確認對話框顯示「將送到哪裡」），絕不回完整網址。寄信模式回收件信箱。"""
    try:
        cfg = load_settings(conn)
    except SettingsCorrupt:
        cfg = dict(DEFAULTS)
    if cfg.get("channel") == "email":
        return cfg.get("mail_to") or None
    url = get_webhook(conn, key_path)
    try:
        return (urlparse(url).hostname or None) if url else None
    except ValueError:
        return None


_LIVE_LAST_MONO: float | None = None


def _mono() -> float:
    return time.monotonic()


def _live_refuse(code, reason, **kw):
    return {"ok": False, "outcome": "refused", "code": code, "reason": reason, "http": None, "sent_out": False, **kw}


def live_test_begin(conn: sqlite3.Connection, key_path: str | None = None, now: datetime | None = None,
                    card: dict | None = None, title: str | None = None) -> dict:
    """第一段（短交易，**不 commit**；路由在同一交易寫稽核「開始」後先 commit）：驗證條件並插入 live_test 列（status=sending）。
    這一列就是 60 秒限流的依據，所以必須在送出前先提交；之後即使結果紀錄寫失敗，它也不會消失。
    限流雙重判斷：DB 列的 created_at（伺服器時間窗，多行程共用）＋本行程的單調時鐘（伺服器時間被往前撥時仍擋得住）。
    兩者都依賴伺服器；多行程部署時單調時鐘只保護同一行程。"""
    global _LIVE_LAST_MONO
    now = now or now_taipei()
    cfg = load_settings(conn)
    url = target(conn, cfg, key_path)
    if not url:
        return _live_refuse("no_url", "還沒設定收件信箱" if cfg.get("channel") == "email" else "還沒設定 Workflow 網址")
    if _auth_off(conn):
        return _live_refuse("auth_disabled", "登入停用中：不能對外送出")
    if not cfg["live_confirmed"]:
        return _live_refuse("not_confirmed", "尚未勾選「對外送出已確認」並儲存")
    if is_mail(url):
        miss = smtp_missing(conn)
        if miss:
            return _live_refuse("no_smtp", f"寄信設定還缺：{'、'.join(miss)}")
    elif is_loopback_url(url):
        return _live_refuse("invalid_url", "目前設定的是本機模擬網址，請用「測試送出（僅本機模擬）」")
    else:
        try:
            validate_webhook_url(url)
        except WebhookError as exc:
            return _live_refuse("invalid_url", f"網址驗證未通過：{exc}")
    mono = _mono()
    if _LIVE_LAST_MONO is not None and 0 <= mono - _LIVE_LAST_MONO < LIVE_TEST_WINDOW_SEC:
        wait = max(1, LIVE_TEST_WINDOW_SEC - int(mono - _LIVE_LAST_MONO))
        return _live_refuse("rate_limited", f"每 {LIVE_TEST_WINDOW_SEC} 秒最多送一次測試訊息，請 {wait} 秒後再試", retry_after=wait)
    ensure(conn)
    title = title or "測試訊息（IT 戰情室）"
    import notice_fmt as nf
    card = card or nf.card(f"{nf.EMOJI['test']} 測試｜IT 戰情室連線測試（不是告警）{_ts(now)[11:16]}", [], color="Accent")
    key = f"live_test:{now.strftime('%Y%m%d%H%M%S%f')}"
    card = _fit(_with_event_id(card, key))
    token = uuid.uuid4().hex
    since = _ts(now - timedelta(seconds=LIVE_TEST_WINDOW_SEC))
    cur = conn.execute(
        "INSERT INTO teams_notify_delivery (event_key, kind, source_ref, severity, title, payload_json, status, attempts, next_try_at, "
        "lease_until, claim_token, expires_at, created_at) "
        "SELECT ?, 'live_test', 'live_test', 'info', ?, ?, 'sending', 0, NULL, ?, ?, NULL, ? "
        "WHERE NOT EXISTS (SELECT 1 FROM teams_notify_delivery WHERE kind='live_test' AND created_at >= ?)",
        (key, title[:200], json.dumps(card, ensure_ascii=False), _ts(now + timedelta(seconds=LEASE_SEC)), token, _ts(now), since))
    if cur.rowcount != 1:
        last = conn.execute("SELECT MAX(created_at) FROM teams_notify_delivery WHERE kind='live_test'").fetchone()[0]
        try:
            wait = max(1, LIVE_TEST_WINDOW_SEC - int((now - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()))
        except (TypeError, ValueError):
            wait = LIVE_TEST_WINDOW_SEC
        return _live_refuse("rate_limited", f"每 {LIVE_TEST_WINDOW_SEC} 秒最多送一次測試訊息，請 {wait} 秒後再試", retry_after=wait)
    _LIVE_LAST_MONO = mono
    return {"ok": True, "outcome": "begun", "key": key, "token": token}


def live_test_execute(conn: sqlite3.Connection, key: str, transport=None, key_path: str | None = None) -> tuple:
    """第二段（**交易外**，只讀取、不寫入、不持有 SQLite 寫入鎖）：送出網路請求，回 transport 的 (http, err, phase)。
    送出前再驗一次網址（含 DNS 內部位址檢查）。任何例外都轉成「不確定」，不往上丟。"""
    row = conn.execute("SELECT payload_json FROM teams_notify_delivery WHERE event_key=?", (key,)).fetchone()
    cfg = load_settings(conn)
    url = target(conn, cfg, key_path)
    if row is None or not url:
        return (None, "網址或測試列不見了", "refused")
    try:
        if not is_mail(url):
            validate_webhook_url(url)
    except WebhookError as exc:
        return (None, f"網址驗證未通過：{exc}", "refused")
    try:
        send = transport or default_transport(conn, cfg, url)
        return tuple(send(url, row[0].encode()))
    except Exception as exc:  # noqa: BLE001 - 送出後的任何例外都保守當「不確定」
        return (None, type(exc).__name__, "uncertain")


def live_test_finish(conn: sqlite3.Connection, key: str, token: str, result: tuple, key_path: str | None = None, now: datetime | None = None) -> dict:
    """第三段（短交易，**不 commit**；路由同一交易寫稽核「結果」後 commit）：把結果寫回那一列（fencing）。"""
    now = now or now_taipei()
    cfg = load_settings(conn)
    url = target(conn, cfg, key_path) or ""
    row = conn.execute("SELECT * FROM teams_notify_delivery WHERE event_key = ?", (key,)).fetchone()
    res = deliver_one(conn, row, {**cfg, "max_attempts": 1}, url, lambda u, b: result, now, token)
    if res == "held":          # 送出前網址／環境驗證沒過：不是排程列，直接收尾
        conn.execute("UPDATE teams_notify_delivery SET status='dead', claim_token=NULL, lease_until=NULL WHERE event_key=?", (key,))
    r2 = conn.execute("SELECT last_http, last_error FROM teams_notify_delivery WHERE event_key = ?", (key,)).fetchone()
    outcome = {"sent": "sent", "uncertain": "uncertain"}.get(res, "failed")
    maybe = result[0] is not None or (len(result) > 2 and result[2] == "uncertain")
    return {"ok": res == "sent", "outcome": outcome, "code": "", "http": r2[0], "reason": r2[1] or "", "sent_out": bool(maybe)}


def send_live_test(conn: sqlite3.Connection, transport=None, key_path: str | None = None, now: datetime | None = None) -> dict:
    """三段合一（單元測試與程式內呼叫用，**不 commit**）。路由不用這個，而是分三段各自短交易，網路呼叫在交易外。"""
    b = live_test_begin(conn, key_path, now)
    if b["outcome"] == "refused":
        return b
    result = live_test_execute(conn, b["key"], transport, key_path)
    return live_test_finish(conn, b["key"], b["token"], result, key_path, now)


def delivery_log(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    if not _has(conn, "teams_notify_delivery"):
        return []
    rows = conn.execute("SELECT id, event_key, kind, source_ref, severity, title, status, attempts, last_http, last_error, created_at, sent_at, next_try_at, expires_at "
                        "FROM teams_notify_delivery ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = {**dict(r), "__no": r["id"], "status_label": STATUS_LABEL.get(r["status"], r["status"])}
        if r["kind"] == "live_test" and r["status"] == "sending":
            d["status_label"] = "結果未記錄（可能已送達）"        # 送出了但結果沒寫回（或仍在送）：不讓這筆消失、也不假裝成功
        out.append(d)
    return out


def retry_dead(conn: sqlite3.Connection, delivery_id: int, now: datetime | None = None) -> dict | None:
    """管理員把放棄／過期／不確定的一則重新排隊（嘗試次數歸零、有效期重算，仍受同一套退避與上限約束）。
    依賴它的子通知（恢復／結案）若因它沒送達而被略過或過期，一併重新排隊（等它真的 sent 才會送），不會無聲漏送。
    回 {event_key, status, dependents}（供稽核）；不符資格回 None。**不 commit**。"""
    now = now or now_taipei()
    ensure(conn)
    row = conn.execute("SELECT event_key, status, kind FROM teams_notify_delivery WHERE id=?", (delivery_id,)).fetchone()
    if not row or row["status"] not in MANUAL_RETRY or row["kind"] in ("test", "live_test"):
        return None
    hours = SUMMARY_EXPIRE_HOURS if row["kind"].startswith("summary") else EXPIRE_HOURS
    cur = conn.execute("UPDATE teams_notify_delivery SET status='pending', attempts=0, next_try_at=?, expires_at=?, lease_until=NULL, claim_token=NULL, last_error=NULL "
                       "WHERE id=? AND status IN ('dead','expired','uncertain') AND kind NOT IN ('test','live_test')", (_ts(now), _ts(now + timedelta(hours=hours)), delivery_id))
    if not cur.rowcount:
        return None
    kids = conn.execute("UPDATE teams_notify_delivery SET status='pending', attempts=0, next_try_at=?, expires_at=?, lease_until=NULL, claim_token=NULL, last_error=NULL "
                        "WHERE depends_on=? AND status IN ('skipped','expired')", (_ts(now), _ts(now + timedelta(hours=EXPIRE_HOURS)), row["event_key"])).rowcount
    return {"event_key": row["event_key"], "status": row["status"], "dependents": kids}
