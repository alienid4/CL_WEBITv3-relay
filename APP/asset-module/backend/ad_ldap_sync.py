"""AD 人員名單自動同步（LDAPS 直連網域控制站）。

使用者 2026-11-19：正式機碰得到 DC 的 389／636，不必再每天人工匯出 CSV 或靠 9.26 中繼——系統自己每天唯讀拉一次。
2.068.2：照 GPT 審查＋獨立裁判的 21 點修正（分頁完整性、UAC 未知、TLS 名稱、持久化鎖帳節流、補跑、時區、單一來源、稽核）。

## 寫入路徑跟 CSV／中繼完全同一條

讀到的每個人轉成跟 `AI/tools_relay/ad_export.ps1` 一樣的欄位（SamAccountName、employeeID、DisplayName、
UserPrincipalName、mail、department、manager〔DN〕、Enabled），交給 `ad_import.import_rows`（整份取代），
套同一道「有效人數比現有少超過兩成整批不收」（`guard_shrink=True`）；名單與「成功」紀錄同一個交易提交。
電話欄 ad_import 用不到，這裡**不讀**（資料最小化）。

## 完整性（寧可不寫，不寫半份）

* 分頁：`paged_criticality=True`；每頁驗結果碼、分頁控制項存在、cookie 是 bytes；只有「明確空 cookie」算讀完，
  缺控制項或型別不對＝整批失敗不寫入。
* userAccountControl 缺或解不開＝狀態未知（不是啟用）；未知超過 2% 整批拒收。
* 查詢途中斷線／逾時：換下一台 DC 從第一頁重抓；整次上限 10 分鐘。

## 安全（寫死，沒有設定可以關）

* **只走 LDAPS 636**；`ldap://`、`:389`、其他連接埠一律拒收。沒有 StartTLS、沒有明文。
* **一律驗證憑證**，CA 檔必填（不退回系統信任清單：有公開 CA 憑證的外部主機不可以通過）。
  用 IP 連 DC 時憑證主機名必填；**bind 之前自己再驗一次「憑證上的 DNS 名稱＝設定的主機名」**，
  不接受 ldap3 那種「IP 對得上也算」的替代。`*` 一律拒收。
* **密碼不可空白**（AD 會把空密碼當匿名繫結成功）。
* **鎖帳節流持久化**（ad_ldap_lockout）：鍵＝正規化帳號（domain\\user／user@domain／DN 對到同一人、不分大小寫），
  同步與兩個「測試」入口共用額度，原子遞增；30 分鐘內 3 次就停；帳號鎖定／停用／過期／需改密碼＝直接停到憑證被改過。
* **換台只限 bind 還沒送出**（連不上、TLS／憑證名稱不符）。bind 送出但結果不明＝停止、扣額度；
  非 49 的拒絕依原因停止，都不換台。
* **錯誤一律白名單原因碼＋固定訊息**，原始例外文字不進紀錄、不回前端、不寫 log（log 只寫原因碼／例外類別名）。
* **全體員工個資**：LDAP 原始回應只在記憶體轉成列，不寫檔、不寫 log、不進執行紀錄。

## 排程

每天一次（預設 07:00，Asia/Taipei 牆上時間，不看主機時區），盤中靜默窗（入口 `ad_ldap_tick`）內不動；
bind 前與每一頁前再查一次靜默窗，被擋記「延後」（不算失敗）。失敗隔 60 分鐘、一天最多 3 次。
補跑走同一個判斷（模組、開關、今天是否已成功、重試額度、鎖帳），只有真的同步成功才算補跑成功。
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta

import credential_store
import integration_http as ih
from db import get_setting, set_setting

LOG = logging.getLogger("ad_ldap_sync")

MODULE_KEY = "ad_ldap"
CRED_KIND = "ldap"
CRED_NAME = "integration:ad"          # 保留名稱（舊版相容用，不再自動選）
KEY_PATH = credential_store.KEY_PATH_DEFAULT      # 測試會換成暫存路徑

PORT = 636                            # 寫死：只走 LDAPS
FILTER = "(&(objectCategory=person)(objectClass=user))"
ATTRS = ("sAMAccountName", "employeeID", "displayName", "userPrincipalName", "mail", "department", "manager",
         "userAccountControl")
#: 交給 ad_import 的欄名＝ad_export.ps1 的欄名（同一份別名表解析）
COLUMNS = ["SamAccountName", "employeeID", "DisplayName", "UserPrincipalName", "mail", "department", "manager", "Enabled"]
PAGE_SIZE = 1000
MAX_PAGES = 200                       # 20 萬人；超過＝分頁資訊異常，中止
TEST_LIMIT = 20                       # 測試連線只抽查一頁、最多這麼多筆
CONNECT_TIMEOUT = 8
RECEIVE_TIMEOUT = 60
MAX_RUN_S = 600                       # 整次同步上限 10 分鐘
STALE_RUNNING_MIN = 30                # running 超過這麼久＝中斷（服務重啟等），可回收
UAC_UNKNOWN_MAX = 0.02                # 帳號狀態未知超過 2% 整批拒收
PAGED_OID = "1.2.840.113556.1.4.319"

K_CRED = "ad_ldap_credential"
K_HOSTS = "ad_ldap_hosts"             # JSON 清單：[{"host": "...", "cert_name": "..."}]
K_BASE = "ad_ldap_base_dn"
K_CA = "ad_ldap_ca_bundle"
K_ENABLED = "ad_ldap_enabled"
K_RUN_AT = "ad_ldap_run_at"
DEFAULT_RUN_AT = "07:00"
STALE_DAYS = 2
MAX_SCHEDULE_TRIES = 3
RETRY_GAP_MIN = 60
BIND_FAIL_MAX = 3
BIND_FAIL_WINDOW_S = 1800

SCHEMA = """
CREATE TABLE IF NOT EXISTS ad_ldap_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    trigger TEXT NOT NULL,                   -- manual / schedule
    started_by TEXT,
    status TEXT NOT NULL,                    -- running / ok / refused / failed / deferred
    dc TEXT,                                 -- 實際讀到資料的那台（位址，不含帳密）
    entries INTEGER,                         -- AD 回了幾筆人員
    rows INTEGER, enabled_n INTEGER, disabled_n INTEGER,
    mgr_ok INTEGER, mgr_fail INTEGER, mgr_dupname INTEGER,
    prev_total INTEGER,                      -- 同步前名單人數
    message TEXT,                            -- 固定訊息（白名單原因碼），不含個資、不含密碼、不含原始例外文字
    detail TEXT                              -- JSON：{"codes": [...], "errors": [各台 DC 的固定訊息]}
);
CREATE INDEX IF NOT EXISTS idx_ad_ldap_runs_started ON ad_ldap_runs(started_at);
CREATE TABLE IF NOT EXISTS ad_ldap_lockout (
    account TEXT PRIMARY KEY,                -- 正規化後的 AD 帳號（小寫、去網域）
    fails INTEGER NOT NULL DEFAULT 0,
    window_start REAL NOT NULL DEFAULT 0,    -- epoch 秒
    hard_reason TEXT,                        -- 鎖定／停用／過期／需改密碼：停到憑證被改過
    cred_stamp TEXT,                         -- 憑證名稱＋更新時間；改過密碼＝不同 stamp＝自動解除
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS ad_ldap_config_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL, username TEXT, before TEXT, after TEXT     -- JSON：DC／憑證主機名、Base DN、CA、憑證、排程（無密碼）
);
"""

#: 白名單原因碼 → 固定訊息。紀錄、API、畫面只出現這些字（加上設定裡的 DC 位址與整數代碼）。
REASONS = {
    "config": "設定不完整或不合規",
    "no_ldap3": "伺服器缺少 ldap3 套件（離線安裝包要附 ldap3／pyasn1 的 wheel）",
    "connect": "連不上（連線失敗或逾時）",
    "tls": "TLS 憑證驗證失敗（不是指定 CA 簽發、已過期或格式不符）",
    "cert_name": "憑證上的主機名與設定不符，沒有送出帳密",
    "bind_invalid": "帳號或密碼錯誤",
    "bind_locked": "帳號已鎖定",
    "bind_disabled": "帳號已停用",
    "bind_expired": "帳號或密碼已過期",
    "bind_must_change": "帳號需要變更密碼",
    "bind_restricted": "此時段或這台主機不允許此帳號登入",
    "bind_uncertain": "帳密已送出但結果不明（連線中斷或逾時），為避免鎖帳先停止",
    "bind_refused": "AD 拒絕繫結",
    "search_transport": "查詢途中連線中斷或逾時",
    "search_failed": "查詢失敗",
    "paging": "分頁控制項缺少或格式不符，整批不寫入",
    "too_many_pages": "分頁超過上限，疑似分頁資訊異常",
    "total_timeout": "整次同步超過 10 分鐘，已中止",
    "all_down": "每台 DC 都連不上",
    "uac_unknown": "帳號啟用狀態（userAccountControl）缺值比例過高，整批不收",
    "no_rows": "AD 回的人員都沒有員編（sAMAccountName／employeeID），名單沒有變動",
    "parse": "名單解析失敗，沒有寫入",
    "shrink": "新名單比現有少超過兩成，整批不收",
    "blocked": "服務帳號被暫停使用（避免鎖帳）",
    "deferred": "盤中靜默，延後到窗外補跑",
    "internal": "內部錯誤，已中止（名單沒有變動）",
}
HARD_BIND = {"bind_locked", "bind_disabled", "bind_expired", "bind_must_change", "bind_restricted"}
COUNT_BIND = {"bind_invalid", "bind_uncertain"}

_run_lock = threading.Lock()
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9\-\.]{0,251}[A-Za-z0-9])?$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def reason(code: str, host: str | None = None, n: int | None = None) -> str:
    """固定訊息。只會帶設定裡的 DC 位址與整數代碼，不帶任何外來文字。"""
    t = REASONS.get(code, REASONS["internal"])
    if n is not None:
        t += f"（代碼 {int(n)}）"
    return f"{host}：{t}" if host else t


class SyncError(Exception):
    """同步失敗。code 是白名單原因碼；str() 只會是固定訊息。"""

    def __init__(self, code: str, host: str | None = None, n: int | None = None, errors: list[str] | None = None):
        self.code, self.host, self.n, self.errors = code, host, n, errors or []
        super().__init__(reason(code, host, n))


class Unreachable(SyncError):
    """bind 還沒送出的失敗（連不上、TLS、憑證名稱）：可以換下一台。"""


class BindStop(SyncError):
    """bind 已送出後的任何非成功（拒絕、結果不明）：停止、不換台。"""


class SearchTransport(SyncError):
    """查詢途中斷線／逾時：可以換下一台從第一頁重抓。"""


def ensure_tables(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def module_enabled(conn) -> bool:
    row = conn.execute("SELECT enabled FROM feature_flags WHERE module_key = ?", (MODULE_KEY,)).fetchone()
    return bool(row and row["enabled"])


def _now() -> datetime:
    """Asia/Taipei 牆上時間（不看主機時區）。"""
    import quiet_window as qw
    return qw.local_now()


def _stamp(t: datetime | None = None) -> str:
    return (t or _now()).strftime("%Y-%m-%d %H:%M:%S")


def _epoch() -> float:
    return time.time()


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- 設定驗證
def parse_hosts(text_or_list) -> list[dict]:
    """DC 清單：一行一台，`位址` 或 `位址 憑證上的主機名`。回 [{"host", "cert_name"}]。不合規丟 ConfigError。

    明文或 389 一律拒收；`*` 拒收；用 IP 連時憑證主機名必填（且必須是 DNS 名稱，不可以又是 IP）。"""
    if isinstance(text_or_list, list):
        lines = []
        for x in text_or_list:
            if isinstance(x, dict):
                lines.append(f"{x.get('host', '')} {x.get('cert_name', '') or ''}".strip())
            elif isinstance(x, str):
                lines.append(x)
            else:
                raise ih.ConfigError("DC 清單格式不對")
    elif isinstance(text_or_list, str) or text_or_list is None:
        lines = re.split(r"[\r\n,;]+", str(text_or_list or ""))
    else:
        raise ih.ConfigError("DC 清單格式不對")
    out: list[dict] = []
    seen = set()
    for raw in lines:
        t = raw.strip()
        if not t:
            continue
        low = t.lower()
        if low.startswith("ldap://") or "starttls" in low:
            raise ih.ConfigError("只接受 LDAPS（636）；ldap:// 明文連線不提供")
        if "://" in t and not low.startswith("ldaps://"):
            raise ih.ConfigError("DC 只能寫主機名或 IP（可加 ldaps:// 與 :636）")
        if low.startswith("ldaps://"):
            t = t[8:]
        parts = t.split()
        if not parts:
            raise ih.ConfigError("DC 位址是空的")
        if len(parts) > 2:
            raise ih.ConfigError("一行只能是「位址」或「位址 憑證上的主機名」")
        host = parts[0].rstrip("/")
        if ":" in host:
            h, _, port = host.rpartition(":")
            if port != str(PORT):
                raise ih.ConfigError("只走 LDAPS 636，不接受其他連接埠（389 是明文）")
            host = h
        if not host:
            raise ih.ConfigError("DC 位址是空的")
        cert = parts[1] if len(parts) == 2 else ""
        for v, what in ((host, "位址"), (cert, "憑證主機名")):
            if v and ("*" in v or not _HOST_RE.match(v)):
                raise ih.ConfigError(f"DC {what}不合規（只能是主機名或 IP，不可含 *）")
        if cert and _is_ip(cert):
            raise ih.ConfigError("憑證主機名要寫 DC 憑證上的 DNS 名稱，不能寫 IP")
        if _is_ip(host) and not cert:
            raise ih.ConfigError(f"用 IP（{host}）連 DC 時，同一行要寫憑證上的主機名（例：{host} dc01.corp.example）")
        if host.lower() in seen:
            continue
        seen.add(host.lower())
        out.append({"host": host, "cert_name": cert})
    if len(out) > 10:
        raise ih.ConfigError("DC 最多 10 台")
    return out


def check_base_dn(base) -> str:
    """用 ldap3 的 DN 解析器驗證；至少要有一段 DC=。"""
    if not isinstance(base, str):
        raise ih.ConfigError("Base DN 格式不對")
    b = base.strip()
    if not b:
        return ""
    try:
        from ldap3.utils.dn import parse_dn
        parts = parse_dn(b)
    except ImportError:
        raise ih.ConfigError(REASONS["no_ldap3"]) from None
    except Exception:  # noqa: BLE001 - LDAPInvalidDnError 等：一律回固定訊息（不回顯輸入）
        raise ih.ConfigError("Base DN 格式不對（例：DC=corp,DC=example,DC=test）") from None
    if not parts or not any(str(p[0]).upper() == "DC" for p in parts):
        raise ih.ConfigError("Base DN 至少要有一段 DC=（例：DC=corp,DC=example,DC=test）")
    return ",".join(f"{p[0]}={p[1]}" for p in parts)


def check_bind_user(user: str) -> str:
    """服務帳號三種寫法都收：完整 DN、user@domain（UPN）、DOMAIN\\user。只有帳號名（沒網域）不收。"""
    u = (user or "").strip()
    if not u:
        raise ih.ConfigError("收集憑證的帳號是空的")
    if "=" in u and "," in u:
        return u
    if re.match(r"^[^@\s\\]+@[^@\s\\]+\.[^@\s\\]+$", u):
        return u
    if re.match(r"^[^\\\s@]+\\[^\\\s@]+$", u):
        return u
    raise ih.ConfigError("帳號要寫成完整 DN（CN=…,DC=…）、帳號@網域，或 網域\\帳號 其中一種")


def account_key(user: str) -> str:
    """鎖帳節流的鍵：domain\\user、user@domain、DN 對到同一個人（小寫、去網域；DN 取第一段的值）。"""
    u = (user or "").strip()
    if "\\" in u:
        u = u.split("\\", 1)[1]
    elif "=" in u:
        m = re.match(r"^\s*[A-Za-z]+\s*=\s*((?:\\.|[^,\\])+)", u)
        u = re.sub(r"\\(.)", r"\1", m.group(1)) if m else u
    elif "@" in u:
        u = u.split("@", 1)[0]
    return u.strip().lower()


def credential_name(conn, override: str | None = None) -> str | None:
    """只用人明確挑的那一筆（kind=ldap）；不自動挑（畫面不再有存不進去的「自動」）。"""
    name = (override or get_setting(conn, K_CRED, "") or "").strip()
    return name if name and credential_store.valid_choice(conn, name, CRED_KIND) else None


def _hosts_setting(conn) -> list[dict]:
    try:
        v = json.loads(get_setting(conn, K_HOSTS, "[]") or "[]")
        return parse_hosts(v if isinstance(v, list) else [])
    except (ValueError, ih.ConfigError):
        return []        # 存的值壞掉（只可能是手改資料庫）＝當作沒設定：燈號顯示「待設定」、同步回「尚未設定 DC」，不是靜默當成正常


def get_config(conn) -> dict:
    name = credential_name(conn)
    cred = next((c for c in credential_store.list_public(conn) if c["name"] == name), None) if name else None
    return {
        "hosts": _hosts_setting(conn),
        "port": PORT,
        "base_dn": get_setting(conn, K_BASE, "") or "",
        "ca_bundle": get_setting(conn, K_CA, "") or "",
        "enabled": get_setting(conn, K_ENABLED, "0") == "1",
        "run_at": get_setting(conn, K_RUN_AT, DEFAULT_RUN_AT) or DEFAULT_RUN_AT,
        "credential": name or "",
        "username": cred["username"] if cred else "",
        "has_password": bool(cred and cred["has_secret"]),      # 只告知有沒有設，永不回內容
        "stale_days": STALE_DAYS,
    }


def _in_quiet(conn, hhmm: str) -> bool:
    import quiet_window as qw
    cfg = qw.get_config(conn)
    t = datetime.strptime(hhmm, "%H:%M").time()
    return bool(cfg["enabled"]) and cfg["start"] <= t < cfg["end"]


def _cred_row(conn, name: str):
    return conn.execute("SELECT username, scope, updated_at FROM collect_credential WHERE name = ?", (name,)).fetchone()


def _check_scope(conn, name: str | None, hosts: list[dict]) -> None:
    """收集憑證有填「適用範圍」時，每台 DC 都要在範圍內（比照 SSH／儲存設備），否則不用這組憑證。"""
    if not name:
        return
    r = _cred_row(conn, name)
    scope = ((r["scope"] if r else "") or "").strip()
    if not scope:
        return
    bad = [h["host"] for h in hosts if not credential_store.scope_allows(scope, h["host"])]
    if bad:
        raise ih.ConfigError(f"DC {'、'.join(bad)} 不在收集憑證「{name}」的適用範圍內，不使用這組憑證")


def _as_bool(v, what: str) -> bool:
    if isinstance(v, bool):
        return v
    raise ih.ConfigError(f"{what}要是 true／false")


def _as_str(v, what: str) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    raise ih.ConfigError(f"{what}要是文字")


def save_config(conn, *, hosts, base_dn, ca_bundle, enabled, run_at, credential=None, port=None,
                actor: str | None = None) -> None:
    """驗證通過才存，同一個交易寫設定變更稽核（改前改後，無密碼）。帳密不在這裡（收集憑證 kind=ldap）。
    credential：None＝沿用；空字串＝清掉選擇。"""
    if port is not None:
        if isinstance(port, bool) or not isinstance(port, int) or port != PORT:
            raise ih.ConfigError("只走 LDAPS 636，連接埠不能改")
    hs = parse_hosts(hosts)
    base = check_base_dn(base_dn)
    ca = _as_str(ca_bundle, "CA 憑證檔").strip()
    if hs and not ca:
        raise ih.ConfigError("CA 憑證檔必填（只信任公司 CA 簽發的 DC 憑證，不用系統預設信任清單）")
    ih.check_ca_bundle(ca)
    en = _as_bool(enabled, "每日同步")
    ra = _as_str(run_at, "同步時間").strip() or DEFAULT_RUN_AT
    if not _HHMM.match(ra):
        raise ih.ConfigError("同步時間格式要是 HH:MM（例：07:00）")
    if _in_quiet(conn, ra):
        raise ih.ConfigError("同步時間落在盤中靜默時段內，請改到開盤前或收盤後")
    chosen = None if credential is None else _as_str(credential, "收集憑證").strip()
    if chosen and not credential_store.valid_choice(conn, chosen, CRED_KIND):
        raise ih.ConfigError("所選的收集憑證不存在或不是 AD（ldap）類型，請到系統設定 → 收集憑證設定")
    eff_cred = credential_name(conn) if chosen is None else (chosen or None)
    if en:
        if not eff_cred:
            raise ih.ConfigError("要啟用每日同步，請先挑一筆 AD（ldap）類型的收集憑證")
        if not hs:
            raise ih.ConfigError("要啟用每日同步，請先填網域控制站（DC）")
        if not base:
            raise ih.ConfigError("要啟用每日同步，請先填 Base DN")
    _check_scope(conn, eff_cred, hs)
    before = get_config(conn)
    after = {"hosts": hs, "base_dn": base, "ca_bundle": ca, "enabled": en, "run_at": ra, "credential": eff_cred or ""}
    keys = ("hosts", "base_dn", "ca_bundle", "enabled", "run_at", "credential")
    b = {k: before[k] for k in keys}
    try:
        ensure_tables(conn)
        if b != after:
            conn.execute("INSERT INTO ad_ldap_config_audit (at, username, before, after) VALUES (?,?,?,?)",
                         (_stamp(), actor, json.dumps(b, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))
            import activity
            changed = [k for k in keys if b[k] != after[k]]
            activity.log(conn, username=actor, ip=None, method="PUT", path="/api/integrations/ad", status=200,
                         action=_audit_text(b, after, changed), commit=False)
        if chosen is not None:
            set_setting(conn, K_CRED, chosen, commit=False)
        set_setting(conn, K_HOSTS, json.dumps(hs, ensure_ascii=False), commit=False)
        set_setting(conn, K_BASE, base, commit=False)
        set_setting(conn, K_CA, ca, commit=False)
        set_setting(conn, K_ENABLED, "1" if en else "0", commit=False)
        set_setting(conn, K_RUN_AT, ra, commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _audit_text(b: dict, a: dict, changed: list[str]) -> str:
    label = {"hosts": "DC／憑證主機名", "base_dn": "Base DN", "ca_bundle": "CA 憑證檔", "enabled": "每日同步",
             "run_at": "同步時間", "credential": "收集憑證"}

    def fmt(k, v):
        if k == "hosts":
            return "、".join(f"{h['host']}" + (f"（{h['cert_name']}）" if h["cert_name"] else "") for h in v) or "（空）"
        if k == "enabled":
            return "開" if v else "關"
        return str(v) or "（空）"
    parts = [f"{label[k]}：{fmt(k, b[k])} → {fmt(k, a[k])}" for k in changed]
    return ("AD 同步設定變更｜" + "；".join(parts))[:900]


def config_audit(conn, limit: int = 50) -> list[dict]:
    ensure_tables(conn)
    out = []
    for r in conn.execute("SELECT * FROM ad_ldap_config_audit ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(r)
        for k in ("before", "after"):
            try:
                d[k] = json.loads(d[k] or "{}")
            except ValueError:
                d[k] = {}
        out.append(d)
    return out


# ---------------------------------------------------------------- 連線（ldap3；測試換 CONNECT）
def build_tls(ca_bundle: str | None, cert_name: str = ""):
    """一律 CERT_REQUIRED、TLS 1.2 以上、CA 檔必填（不退回系統信任清單）。沒有參數可以關掉驗證。"""
    import ssl

    from ldap3 import Tls
    ca = ih.check_ca_bundle(ca_bundle)
    if not ca:
        raise ih.ConfigError("CA 憑證檔必填（只信任公司 CA 簽發的 DC 憑證）")
    kw = {"validate": ssl.CERT_REQUIRED, "version": ssl.PROTOCOL_TLS_CLIENT, "ca_certs_file": ca,
          "ssl_options": [getattr(ssl, "OP_NO_SSLv2", 0), getattr(ssl, "OP_NO_SSLv3", 0),
                          getattr(ssl, "OP_NO_TLSv1", 0), getattr(ssl, "OP_NO_TLSv1_1", 0)]}
    if cert_name:
        kw["valid_names"] = [cert_name]
        kw["sni"] = cert_name
    return Tls(**kw)


def _dns_match(pattern: str, name: str) -> bool:
    p, n = pattern.lower().rstrip("."), name.lower().rstrip(".")
    if p == n:
        return True
    if p.startswith("*.") and "*" not in p[2:]:
        return "." in n and n.split(".", 1)[1] == p[2:] and n.split(".", 1)[0] != ""
    return False


def cert_name_ok(cert: dict | None, expected: str) -> bool:
    """bind 前自己比對：只看憑證 SAN 的 DNS 名稱（沒有 DNS SAN 才看 CN），**不接受 IP SAN 替代**。"""
    if not cert or not expected or _is_ip(expected):
        return False
    dns = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
    if not dns:
        dns = [v for rdn in cert.get("subject", ()) for k, v in rdn if k == "commonName"]
    return any(_dns_match(d, expected) for d in dns)


def _hard_close(c) -> None:
    """不送 unbind（還沒 bind 或 bind 結果不明時，不再對 DC 送任何東西）。"""
    try:
        c.strategy.close()
    except Exception:  # noqa: BLE001 - 收尾而已，關不掉也不影響結果
        try:
            c.socket.close()
        except Exception:  # noqa: BLE001 - 同上
            pass


def default_connect(host: dict, user: str, password: str, ca_bundle: str):
    """開一條 LDAPS 連線、驗完憑證與憑證主機名，**還沒 bind**。失敗一律 Unreachable（可以換台）。"""
    try:
        import ldap3
        from ldap3.core import exceptions as lx
    except ImportError:
        raise Unreachable("no_ldap3", host["host"]) from None
    expected = host.get("cert_name") or host["host"]
    if _is_ip(expected):
        raise ih.ConfigError(f"用 IP（{host['host']}）連 DC 時，憑證主機名必填")
    server = ldap3.Server(host["host"], port=PORT, use_ssl=True, tls=build_tls(ca_bundle, host.get("cert_name", "")),
                          get_info=ldap3.NONE, connect_timeout=CONNECT_TIMEOUT)
    c = ldap3.Connection(server, user=user, password=password, authentication=ldap3.SIMPLE, auto_bind=False,
                         read_only=True, receive_timeout=RECEIVE_TIMEOUT, auto_referrals=False, raise_exceptions=False)
    try:
        c.open()
    except (lx.LDAPException, OSError) as exc:
        code = "tls" if ("ssl" in type(exc).__name__.lower() or "certificate" in type(exc).__name__.lower()
                         or "certificate" in str(exc).lower() or "ssl" in str(exc).lower()) else "connect"
        raise Unreachable(code, host["host"]) from None
    try:
        peer = c.socket.getpeercert()
    except Exception:  # noqa: BLE001 - 拿不到對方憑證＝當作驗證失敗
        peer = None
    if not cert_name_ok(peer, expected):
        _hard_close(c)
        raise Unreachable("cert_name", host["host"])
    return c


#: 測試換成假連線：CONNECT(host_dict, user, password, ca_bundle) -> 已開好、未 bind 的連線（bind()/search()/result/response）
CONNECT = default_connect

_AD_DATA = {"525": "bind_invalid", "52e": "bind_invalid", "530": "bind_restricted", "531": "bind_restricted",
            "532": "bind_expired", "533": "bind_disabled", "701": "bind_expired", "773": "bind_must_change",
            "775": "bind_locked"}


def _bind(c, host: str) -> None:
    """送 bind。成功回 None；其他一律 BindStop（不換台）。"""
    try:
        ok = c.bind()
    except Exception:  # noqa: BLE001 - 已送出、結果不明（斷線／逾時）：停、扣額度
        _hard_close(c)
        raise BindStop("bind_uncertain", host) from None
    if ok:
        return
    res = c.result or {}
    rc = res.get("result")
    _hard_close(c)
    if rc == 49:
        m = re.search(r"data ([0-9a-f]{3})", str(res.get("message", "")), re.IGNORECASE)
        raise BindStop(_AD_DATA.get(m.group(1).lower() if m else "", "bind_invalid"), host)
    try:
        n = int(rc)
    except (TypeError, ValueError):
        n = None
    raise BindStop("bind_refused", host, n)


def _safe_unbind(c) -> None:
    try:
        c.unbind()
    except Exception:  # noqa: BLE001 - 收尾而已，失敗不影響結果
        _hard_close(c)


# ---------------------------------------------------------------- 轉換
def _first(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    if isinstance(v, bytes):
        try:
            v = v.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    return str(v).strip()


def _get(attrs: dict, name: str) -> str:
    low = name.lower()
    for k, v in attrs.items():
        if k.lower() == low:
            return _first(v)
    return ""


def map_entry(attrs: dict) -> list[str] | None:
    """一筆 LDAP 人員 → ad_import 的一列（欄位順序＝COLUMNS）。沒有 sAMAccountName 也沒有 employeeID 的回 None。

    Enabled：userAccountControl 第 2 位元（ACCOUNTDISABLE）沒設＝"True"、有設＝"False"；
    **缺值或解不開＝""（狀態未知，不是啟用）**。"""
    sam, emp = _get(attrs, "sAMAccountName"), _get(attrs, "employeeID")
    if not sam and not emp:
        return None
    uac_t = _get(attrs, "userAccountControl")
    try:
        enabled = ("False" if int(uac_t) & 2 else "True") if uac_t else ""
    except ValueError:
        enabled = ""
    return [sam, emp, _get(attrs, "displayName"), _get(attrs, "userPrincipalName"), _get(attrs, "mail"),
            _get(attrs, "department"), _get(attrs, "manager"), enabled]


def _deadline_check(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise SyncError("total_timeout")


def fetch_rows(c, base_dn: str, host: str = "", deadline: float | None = None,
               limit: int | None = None) -> tuple[list[list[str]], int]:
    """分頁讀完整份（limit＝只抽查第一頁）。回 (列, AD 回的人員筆數)。
    每頁驗：結果碼 0、分頁控制項存在、cookie 是 bytes；只有明確空 cookie 算讀完。"""
    import quiet_window as qw

    rows: list[list[str]] = []
    seen = 0
    cookie = None
    for page in range(MAX_PAGES):
        _deadline_check(deadline)
        qw.network_gate()                       # 排程任務進窗後不送下一頁（QuietWindowBlocked 往上丟＝延後）
        try:
            c.search(base_dn, FILTER, "SUBTREE", attributes=list(ATTRS), paged_size=limit or PAGE_SIZE,
                     paged_criticality=True, paged_cookie=cookie)
        except Exception:  # noqa: BLE001 - 斷線／逾時：換台重抓（原始例外文字不往外帶）
            raise SearchTransport("search_transport", host) from None
        res = c.result or {}
        rc = res.get("result")
        if rc != 0:
            try:
                n = int(rc)
            except (TypeError, ValueError):
                n = None
            raise SyncError("search_failed", host, n)
        for e in c.response or []:
            if not isinstance(e, dict) or e.get("type") != "searchResEntry":
                continue                        # 參照（searchResRef）不跟
            seen += 1
            r = map_entry(e.get("attributes") or {})
            if r is not None:
                rows.append(r)
        ctl = (res.get("controls") or {}).get(PAGED_OID) if isinstance(res.get("controls"), dict) else None
        val = ctl.get("value") if isinstance(ctl, dict) else None
        ck = val.get("cookie") if isinstance(val, dict) else None
        if not isinstance(ck, (bytes, bytearray)):
            raise SyncError("paging", host)
        if limit or len(ck) == 0:
            return rows, seen
        cookie = bytes(ck)
    raise SyncError("too_many_pages", host)


# ---------------------------------------------------------------- 鎖帳節流（持久化）
def _cred_stamp(conn, name: str) -> str:
    r = _cred_row(conn, name)
    return f"{name}|{(r['updated_at'] if r else '') or ''}"


def blocked_reason(conn, name: str | None, user: str | None) -> str | None:
    """這組帳號現在能不能用。回固定訊息或 None。改過密碼（憑證更新時間變了）自動解除。"""
    if not name or not user:
        return None
    ensure_tables(conn)
    r = conn.execute("SELECT * FROM ad_ldap_lockout WHERE account = ?", (account_key(user),)).fetchone()
    if not r or r["cred_stamp"] != _cred_stamp(conn, name):
        return None
    if r["hard_reason"]:
        return f"{REASONS['blocked']}：{REASONS.get(r['hard_reason'], '')}，請 AD 管理員處理後到收集憑證更新密碼才會恢復"
    if r["fails"] >= BIND_FAIL_MAX and r["window_start"] >= _epoch() - BIND_FAIL_WINDOW_S:
        return (f"{REASONS['blocked']}：30 分鐘內已被 AD 拒絕 {r['fails']} 次（再試可能鎖帳），"
                "請確認帳號密碼，到收集憑證改過密碼會立即恢復")
    return None


def _quota_hit(conn, name: str, user: str, code: str) -> None:
    """原子遞增（一條 UPSERT）；硬性原因直接標停。同步與兩個測試入口共用。"""
    ensure_tables(conn)
    now = _epoch()
    stamp = _cred_stamp(conn, name)
    conn.execute(
        "INSERT INTO ad_ldap_lockout (account, fails, window_start, hard_reason, cred_stamp, updated_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(account) DO UPDATE SET "
        " fails = CASE WHEN ad_ldap_lockout.cred_stamp IS NOT excluded.cred_stamp OR ad_ldap_lockout.window_start < ? "
        "              THEN excluded.fails ELSE ad_ldap_lockout.fails + excluded.fails END,"
        " window_start = CASE WHEN ad_ldap_lockout.cred_stamp IS NOT excluded.cred_stamp OR ad_ldap_lockout.window_start < ? "
        "              THEN excluded.window_start ELSE ad_ldap_lockout.window_start END,"
        " hard_reason = CASE WHEN excluded.hard_reason IS NOT NULL THEN excluded.hard_reason "
        "              WHEN ad_ldap_lockout.cred_stamp IS NOT excluded.cred_stamp THEN NULL ELSE ad_ldap_lockout.hard_reason END,"
        " cred_stamp = excluded.cred_stamp, updated_at = excluded.updated_at",
        (account_key(user), 1 if code in COUNT_BIND else 0, now, code if code in HARD_BIND else None, stamp, _stamp(),
         now - BIND_FAIL_WINDOW_S, now - BIND_FAIL_WINDOW_S))
    conn.commit()


def _quota_ok(conn, user: str) -> None:
    ensure_tables(conn)
    conn.execute("UPDATE ad_ldap_lockout SET fails = 0, hard_reason = NULL, updated_at = ? WHERE account = ?",
                 (_stamp(), account_key(user)))
    conn.commit()


# ---------------------------------------------------------------- 共用：準備＋逐台讀
def _prepare(conn, credential: str | None = None) -> tuple[dict, str, str, str]:
    cfg = get_config(conn)
    if not cfg["hosts"]:
        raise ih.ConfigError("尚未設定網域控制站（DC）")
    if not cfg["base_dn"]:
        raise ih.ConfigError("尚未設定 Base DN")
    if not cfg["ca_bundle"]:
        raise ih.ConfigError("尚未設定 CA 憑證檔")
    name = credential_name(conn, credential)
    if not name:
        raise ih.ConfigError("尚未挑選 AD（ldap）類型的收集憑證")
    _check_scope(conn, name, cfg["hosts"])
    got = credential_store.get_for_use(conn, name, key_path=KEY_PATH)
    if got is None:
        credential_store.audit_use(conn, name, cfg["hosts"][0]["host"], False, "取不到憑證（未設定或金鑰檔已更換）")
        raise ih.ConfigError("收集憑證解不開（加密金鑰可能已更換），請到收集憑證重新輸入密碼")
    user, pw = got
    check_bind_user(user)
    if not pw:
        raise ih.ConfigError("收集憑證的密碼是空的（AD 會把空密碼當匿名登入成功），請到收集憑證補上密碼")
    blocked = blocked_reason(conn, name, user)
    if blocked:
        raise ih.ConfigError(blocked)
    return cfg, name, user, pw


def _read_all(conn, cfg: dict, name: str, user: str, pw: str, deadline: float | None,
              limit: int | None = None) -> tuple[list[list[str]], int, str, list[str]]:
    """逐台：bind 還沒送出的失敗、查詢途中斷線 → 換下一台（從第一頁重抓）；bind 已送出的任何失敗 → 停。"""
    import quiet_window as qw

    errs: list[str] = []
    for h in cfg["hosts"]:
        _deadline_check(deadline)
        qw.network_gate()
        try:
            c = CONNECT(h, user, pw, cfg["ca_bundle"])
        except Unreachable as exc:
            errs.append(str(exc))
            continue
        try:
            try:
                qw.network_gate()                 # bind 前再查一次靜默窗
            except qw.QuietWindowBlocked:
                _hard_close(c)
                raise
            try:
                _bind(c, h["host"])
            except BindStop as exc:
                _quota_hit(conn, name, user, exc.code)
                credential_store.audit_use(conn, name, h["host"], False, str(exc))
                raise
            _quota_ok(conn, user)
            try:
                rows, seen = fetch_rows(c, cfg["base_dn"], h["host"], deadline, limit)
            except SearchTransport as exc:
                errs.append(str(exc))
                continue
            return rows, seen, h["host"], errs
        finally:
            _safe_unbind(c)
    raise SyncError("all_down", errors=errs)


# ---------------------------------------------------------------- 連線測試（不寫名單、不寫執行紀錄）
def check_connection(conn, credential: str | None = None) -> dict:
    """bind＋抽查一頁（最多 TEST_LIMIT 筆）。0 筆回警告。只在憑證稽核留一筆。"""
    import quiet_window as qw

    try:
        cfg, name, user, pw = _prepare(conn, credential)
    except ih.ConfigError as exc:
        return {"ok": False, "message": str(exc)}
    try:
        rows, seen, dc, errs = _read_all(conn, cfg, name, user, pw, time.monotonic() + 120, limit=TEST_LIMIT)
    except qw.QuietWindowBlocked:
        return {"ok": False, "message": REASONS["deferred"]}
    except SyncError as exc:
        msg = str(exc) + ("：" + "；".join(exc.errors) if exc.errors else "")
        if not isinstance(exc, BindStop):
            credential_store.audit_use(conn, name, cfg["hosts"][0]["host"], False, "AD 連線測試失敗：" + str(exc))
        return {"ok": False, "message": msg, "code": exc.code}
    except Exception as exc:  # noqa: BLE001 - 不回原始例外文字；log 只寫類別名
        LOG.error("AD 連線測試內部錯誤：%s", type(exc).__name__)
        return {"ok": False, "message": REASONS["internal"], "code": "internal"}
    credential_store.audit_use(conn, name, dc, True, "AD 連線測試")
    out = {"ok": True, "dc": dc, "count": seen, "sample_limit": TEST_LIMIT,
           "message": f"連線成功（{dc}），抽查讀到 {seen} 筆人員帳號（只讀第一頁、最多 {TEST_LIMIT} 筆）"}
    if errs:
        out["message"] += f"；前面 {len(errs)} 台連不上：" + "；".join(errs)
    if seen == 0:
        out["warning"] = "Base DN 底下查不到任何人員帳號，請確認 Base DN 與服務帳號的讀取權限"
    return out


# ---------------------------------------------------------------- 同步
def _start_run(conn, trigger: str, user: str | None) -> int:
    cur = conn.execute("INSERT INTO ad_ldap_runs (started_at, trigger, started_by, status) VALUES (?,?,?, 'running')",
                       (_stamp(), trigger, user))
    conn.commit()
    return cur.lastrowid


def _finish_run(conn, run_id: int, status: str, message: str, *, dc: str | None = None, entries: int | None = None,
                counts: dict | None = None, prev: int | None = None, errors: list[str] | None = None,
                codes: list[str] | None = None, commit: bool = True) -> None:
    c = counts or {}
    conn.execute(
        "UPDATE ad_ldap_runs SET finished_at=?, status=?, dc=?, entries=?, rows=?, enabled_n=?, disabled_n=?, mgr_ok=?, "
        "mgr_fail=?, mgr_dupname=?, prev_total=?, message=?, detail=? WHERE id=?",
        (_stamp(), status, dc, entries, c.get("rows"), c.get("enabled_n"), c.get("disabled_n"), c.get("mgr_ok"),
         c.get("mgr_fail"), c.get("mgr_dupname"), prev, message[:500],
         json.dumps({"codes": codes or [], "errors": (errors or [])[:20]}, ensure_ascii=False), run_id))
    if commit:
        conn.commit()


def _reap_stale(conn) -> None:
    """running 超過 STALE_RUNNING_MIN 分鐘＝中斷（服務重啟、行程被殺），標失敗才不會擋住今天的排程。"""
    cut = _stamp(_now() - timedelta(minutes=STALE_RUNNING_MIN))
    conn.execute("UPDATE ad_ldap_runs SET status='failed', finished_at=?, message=? WHERE status='running' AND started_at < ?",
                 (_stamp(), f"中斷：超過 {STALE_RUNNING_MIN} 分鐘沒有結束（服務重啟或行程中止），名單沒有變動", cut))
    conn.commit()


@__import__("quiet_window").guarded("ad_ldap_tick")
def run_sync(conn, trigger: str = "manual", user: str | None = None) -> dict:
    """同步一次。回 {"run_id", "status", "message"}；status：ok／refused／failed／deferred／busy。永不往外丟原始例外。"""
    import ad_import
    import quiet_window as qw

    ensure_tables(conn)
    if not _run_lock.acquire(blocking=False):
        return {"run_id": None, "status": "busy", "message": "已有一次同步正在進行，請稍後再試"}
    run_id = None
    try:
        _reap_stale(conn)
        run_id = _start_run(conn, trigger, user)
        try:
            cfg, name, buser, pw = _prepare(conn)
        except ih.ConfigError as exc:
            _finish_run(conn, run_id, "failed", str(exc), codes=["config"])
            return {"run_id": run_id, "status": "failed", "message": str(exc)}
        try:
            rows, seen, dc, errs = _read_all(conn, cfg, name, buser, pw, time.monotonic() + MAX_RUN_S)
        except qw.QuietWindowBlocked:
            _finish_run(conn, run_id, "deferred", REASONS["deferred"], codes=["deferred"])
            qw.pending_mark(conn, "ad_ldap_tick", "AD 名單同步途中進入盤中靜默，延後")
            return {"run_id": run_id, "status": "deferred", "message": REASONS["deferred"]}
        except SyncError as exc:
            msg = f"同步失敗，名單沒有變動：{exc}"
            _finish_run(conn, run_id, "failed", msg, errors=exc.errors, codes=[exc.code])
            return {"run_id": run_id, "status": "failed", "message": msg, "code": exc.code}
        credential_store.audit_use(conn, name, dc, True, f"AD 同步（{trigger}）")
        prev = ad_import.current_total(conn)
        if not rows:
            _finish_run(conn, run_id, "failed", REASONS["no_rows"], dc=dc, entries=seen, prev=prev, errors=errs, codes=["no_rows"])
            return {"run_id": run_id, "status": "failed", "message": REASONS["no_rows"]}
        unknown = sum(1 for r in rows if r[7] == "")
        if unknown / len(rows) > UAC_UNKNOWN_MAX:
            msg = f"{REASONS['uac_unknown']}（{unknown}／{len(rows)}，上限 {UAC_UNKNOWN_MAX:.0%}）"
            _finish_run(conn, run_id, "refused", msg, dc=dc, entries=seen, prev=prev, errors=errs, codes=["uac_unknown"])
            return {"run_id": run_id, "status": "refused", "message": msg}
        try:
            counts = ad_import.import_rows(conn, [COLUMNS] + rows, file_name=f"LDAPS {dc}", by=f"ad-sync:{trigger}",
                                           guard_shrink=True, commit=False)
            msg = (f"同步 {counts['rows']} 人（原 {prev} 人；啟用 {counts['enabled_n']}、停用 {counts['disabled_n']}"
                   f"、狀態未知 {unknown}），主管對上 {counts['mgr_ok']}、對不上 {counts['mgr_fail']}、"
                   f"同名疑慮 {counts['mgr_dupname']}（來源 {dc}）")
            if errs:
                msg += f"；前面 {len(errs)} 台 DC 連不上"
            _finish_run(conn, run_id, "ok", msg, dc=dc, entries=seen, counts=counts, prev=prev, errors=errs, commit=False)
            conn.commit()                         # 名單＋批次＋成功紀錄同一個交易
        except ad_import.ShrinkRefused as exc:
            conn.rollback()
            msg = str(exc)                        # 固定格式：只有人數
            _finish_run(conn, run_id, "refused", msg, dc=dc, entries=seen, prev=prev, errors=errs, codes=["shrink"])
            return {"run_id": run_id, "status": "refused", "message": msg}
        except ValueError:
            conn.rollback()
            _finish_run(conn, run_id, "failed", REASONS["parse"], dc=dc, entries=seen, prev=prev, errors=errs, codes=["parse"])
            return {"run_id": run_id, "status": "failed", "message": REASONS["parse"]}
        return {"run_id": run_id, "status": "ok", "message": msg, "summary": {**counts, "prev_total": prev, "dc": dc}}
    except Exception as exc:  # noqa: BLE001 - 沒預期到的錯：回滾、記固定訊息；不往外丟原始例外（可能帶機密）
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 - 回滾失敗也要繼續把紀錄寫成失敗
            pass
        LOG.error("AD 同步內部錯誤：%s", type(exc).__name__)
        msg = f"{REASONS['internal']}（{type(exc).__name__}）"
        if run_id is not None:
            try:
                _finish_run(conn, run_id, "failed", msg, codes=["internal"])
            except Exception:  # noqa: BLE001 - 紀錄寫不進去：running 會被 _reap_stale 回收
                LOG.error("AD 同步：執行紀錄寫入失敗")
        return {"run_id": run_id, "status": "failed", "message": msg, "code": "internal"}
    finally:
        _run_lock.release()


def list_runs(conn, limit: int = 100) -> list[dict]:
    ensure_tables(conn)
    out = []
    for r in conn.execute("SELECT * FROM ad_ldap_runs ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(r)
        try:
            d["detail"] = json.loads(d["detail"]) if d.get("detail") else {}
        except ValueError:
            d["detail"] = {}
        out.append(d)
    return out


# ---------------------------------------------------------------- 新鮮度（狀態用語固定：正常／過期／失敗／沒收到／待設定）
def _current_blocked(conn) -> str | None:
    name = credential_name(conn)
    r = _cred_row(conn, name) if name else None
    return blocked_reason(conn, name, r["username"] if r else None) if r else None


def freshness(conn) -> dict:
    ensure_tables(conn)
    cfg = get_config(conn)
    last_ok = conn.execute("SELECT finished_at FROM ad_ldap_runs WHERE status = 'ok' ORDER BY id DESC LIMIT 1").fetchone()
    last = conn.execute("SELECT status, finished_at, message FROM ad_ldap_runs WHERE status NOT IN ('running', 'deferred') "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    age_h = None
    if last_ok and last_ok["finished_at"]:
        try:
            age_h = (_now() - datetime.strptime(last_ok["finished_at"][:19], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
        except ValueError:
            age_h = None
    blocked = _current_blocked(conn)
    if not (cfg["hosts"] and cfg["base_dn"] and cfg["credential"] and cfg["ca_bundle"]):
        st, label = "unset", "待設定"
    elif blocked:
        st, label = "failed", "失敗"
    elif not last_ok:
        st, label = "never", "沒收到"
    elif age_h is None or age_h > STALE_DAYS * 24:
        st, label = "stale", "過期"
    elif last and last["status"] in ("failed", "refused"):
        st, label = "failed", "失敗"
    else:
        st, label = "ok", "正常"
    return {"status": st, "label": label, "last_ok_at": last_ok["finished_at"] if last_ok else None,
            "age_hours": None if age_h is None else round(age_h, 1),
            "stale_days": STALE_DAYS, "last_status": last["status"] if last else None,
            "last_message": last["message"] if last else None, "blocked": blocked}


# ---------------------------------------------------------------- 排程（正常 tick 與補跑走同一個判斷）
def _due(conn) -> tuple[bool, str]:
    now = _now()
    ra = get_setting(conn, K_RUN_AT, DEFAULT_RUN_AT) or DEFAULT_RUN_AT
    if not _HHMM.match(ra):
        ra = DEFAULT_RUN_AT
    at = now.replace(hour=int(ra[:2]), minute=int(ra[3:]), second=0, microsecond=0)
    if now < at:
        return False, "not_due"
    rows = conn.execute("SELECT status, trigger, started_at FROM ad_ldap_runs WHERE started_at >= ? ORDER BY id",
                        (_stamp(at),)).fetchall()
    if any(r["status"] in ("ok", "refused", "running") for r in rows):
        return False, "done_today"
    fails = [r for r in rows if r["trigger"] == "schedule" and r["status"] == "failed"]
    if len(fails) >= MAX_SCHEDULE_TRIES:
        return False, "gave_up_today"
    if fails:
        try:
            last = datetime.strptime(fails[-1]["started_at"][:19], "%Y-%m-%d %H:%M:%S")
            if now - last < timedelta(minutes=RETRY_GAP_MIN):
                return False, "retry_later"
        except ValueError:
            pass  # 時間欄壞掉就當作可以重試
    return True, "due"


def _should_run(conn) -> tuple[bool, str]:
    if not module_enabled(conn) or get_setting(conn, K_ENABLED, "0") != "1":
        return False, "disabled"
    ensure_tables(conn)
    if not _run_lock.locked():
        _reap_stale(conn)
    if _current_blocked(conn):
        return False, "blocked"
    return _due(conn)


def tick(conn) -> dict:
    """排程器每輪呼叫：模組＋開關＋今天到點還沒成功＋沒被鎖帳節流擋才跑；盤中靜默窗內延後。"""
    ok, why = _should_run(conn)
    if not ok:
        return {"status": why}
    import quiet_window as qw
    if qw.defer_if_quiet(conn, "ad_ldap_tick"):            # 確實到期才守門；盤中不自動連 DC，窗外補跑
        qw.pending_mark(conn, "ad_ldap_tick", "AD 名單同步到期時在盤中靜默，延後")
        return {"status": "deferred_quiet_window"}
    out = run_sync(conn, "schedule", None)
    if isinstance(out, dict) and out.get("status") == "ok":
        qw.pending_finish(conn, "ad_ldap_tick", True, detail="已依排程執行")
    return out


def catchup(conn) -> dict:
    """盤中被延後的補跑：跟 tick 同一個判斷（模組、開關、今天是否已成功、重試額度、鎖帳）。
    回給 scan_service._catchup_result：成功＝{"status": "ok"}；busy／拒收／延後＝{"refused": True}（回 pending、不扣重試）；
    失敗＝{"status": "failed"}（扣重試）；已經不需要補跑（關了、今天已成功、額度用完、被鎖帳擋）＝{"status": "not_needed"}。"""
    ok, why = _should_run(conn)
    if not ok:
        return {"status": "not_needed", "message": why}
    out = run_sync(conn, "schedule", None)
    st = out.get("status") if isinstance(out, dict) else None
    if st == "ok":
        return out
    if st == "failed":
        return {"status": "failed", "message": out.get("message", "")}
    return {"refused": True, "status": st}
