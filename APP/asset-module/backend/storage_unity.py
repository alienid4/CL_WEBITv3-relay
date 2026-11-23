"""儲存設備（Storage）API：多台設定＋連線測試（批 S1，目前只支援 Dell Unity）。

## 範圍（S1）

S1 只做「設定」與「人為連線測試」；S2 的資料收集在 storage_collect.py（同樣人為、GET only，結果表也定義在這裡）。
S3 每日唯讀健康與告警檢查在 storage_health.py（排程入口 storage_health_tick；結果表也定義在這裡）。本檔自己沒有任何排程。

## 鐵則

- **唯讀**：HTTP 只發 GET。不改 Storage 設定、不確認／清除告警、不呼叫任何 action 端點。
- **帳密不在這裡**：設定只存「收集憑證的名稱」（kind=storage，9-3 收集憑證）；連線當下才解密，
  明文只在記憶體，錯誤訊息一律先 scrub。`credential_store.resolve_storage` 會要求憑證有填適用範圍且含這台 IP。
- **帳密有效必須由「受保護的 GET」證明**（AUTH-1）：`basicSystemInfo` 不用登入也會回 200，
  所以它只代表「這個位址是 Unity、看得到」，**不算**帳密有效。帳密只用一次，401 不重試（避免鎖帳號）。
- **只跟隨「同源」重新導向**（真機 Unity 只帶 Basic 會被 302 到 /cas/login，要帶 X-EMC-REST-CLIENT＋Accept 並跟轉址換 session cookie）：
  相同 scheme＋host＋port、最多 3 次、目標路徑須在 /api/ 或 /cas/ 下；跨主機／跨協定／帶帳密網址／//host 一律拒絕，
  Authorization 與 cookie 絕不會送到別的來源。第一次請求用 Basic 換 cookie，成功後只用 cookie。
  **HTML 登入頁拒收**、JSON 才收；回應大小有上限。結束時丟棄 session（不送 logout：logout 是 POST，本模組只發 GET）。
- **TLS 預設驗證**；每台可設 CA 憑證檔，或明示「信任自簽憑證」（預設關，畫面註明風險，測試結果會標「未驗證 TLS」）。
- **分頁 next 只能是同主機同協定**（PAGE-3），重複的 next（循環）與超過頁數上限就停止並回錯（PAGE-2）。
- **每台獨立 session／Cookie**（ISOLATE-1）：每次測試新建一個 client，Cookie 只存在這個 session 內，用完即關並清空；不同台的資料用設備 id 分開，不會互蓋。
- 不吃環境變數的 proxy（trust_env=False）：帳密不經過不明代理。
"""
from __future__ import annotations

import base64
import ipaddress
import json
import re
import ssl
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import credential_store
import integration_http as ih

MODULE_KEY = "storage_api"
KEY_PATH = credential_store.KEY_PATH_DEFAULT      # 加密金鑰檔位置（測試會換成暫存路徑）
CRED_KIND = "storage"

#: 支援的品牌（key → 顯示名）。其他品牌一律明示不支援，不默默當成 Unity 處理。
SUPPORTED_VENDORS = {"dell_unity": "Dell Unity"}
#: 畫面上列出「尚未支援」的品牌，讓人知道不是漏做。僅供顯示，後端一律拒絕。
UNSUPPORTED_HINT = ("NetApp", "HPE (3PAR／Primera／Nimble)", "Pure Storage", "Hitachi", "IBM", "Huawei", "其他")

DEFAULT_PORT = 443
TIMEOUT_S = 15.0
MAX_BODY = 5 * 1024 * 1024      # 單次回應上限（超過視為異常，不吃爆記憶體）
MAX_PAGES = 50                  # 分頁上限（PAGE-2：next 無限延伸也會停）
PER_PAGE = 100
MAX_REDIRECTS = 3               # 同源重新導向（/cas/login 換 session）上限
REDIRECT_PREFIXES = ("/api/", "/cas/")
#: 真機 Unity：缺這兩個 header 會被導到 /cas/login 的網頁登入流程；每個請求都帶
REST_HEADERS = {"Accept": "application/json", "X-EMC-REST-CLIENT": "true"}

#: 受保護的 GET（AUTH-1）：沒登入會 401。basicSystemInfo 不用登入，只當「看得到、是 Unity」的探測。
PATH_BASIC = "/api/types/basicSystemInfo/instances"
PATH_SYSTEM = "/api/types/system/instances"
SYSTEM_FIELDS = "name,model,serialNumber"

#: 只給測試用：測試用 127.0.0.1 的假 HTTP 伺服器，正式環境一律 https。
ALLOW_PLAINTEXT_FOR_TEST = False
ALLOW_LOOPBACK_FOR_TEST = False      # 只給測試：正式一律不收本機／連結本地／保留位址（SSRF 防線）

SCHEMA = """
CREATE TABLE IF NOT EXISTS storage_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    vendor TEXT NOT NULL DEFAULT 'dell_unity',
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 443,
    credential TEXT NOT NULL DEFAULT '',
    ca_bundle TEXT NOT NULL DEFAULT '',
    trust_self_signed INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    created_by TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    last_test_at TEXT, last_test_ok INTEGER, last_test_code TEXT, last_test_msg TEXT,
    last_test_by TEXT, last_identity TEXT, last_tls_verified INTEGER
);
CREATE TABLE IF NOT EXISTS storage_collect_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    device_name TEXT NOT NULL DEFAULT '',
    endpoint TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
    status TEXT NOT NULL,
    complete INTEGER NOT NULL DEFAULT 0,
    code TEXT, message TEXT, collected_by TEXT, tls_verified INTEGER,
    identity TEXT, sections TEXT,
    item_count INTEGER NOT NULL DEFAULT 0, review_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_storage_collect_runs_dev ON storage_collect_runs (device_id, id);
CREATE TABLE IF NOT EXISTS storage_collect_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    kind TEXT NOT NULL, obj_id TEXT, name TEXT, parent TEXT,
    size_total INTEGER, size_used INTEGER, size_free INTEGER,
    extra TEXT, review TEXT NOT NULL DEFAULT '', incomplete INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_storage_collect_items_run ON storage_collect_items (run_id, kind);
CREATE TABLE IF NOT EXISTS storage_health_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL, device_name TEXT NOT NULL DEFAULT '', endpoint TEXT NOT NULL DEFAULT '',
    day TEXT NOT NULL, trigger TEXT NOT NULL, attempt_no INTEGER NOT NULL DEFAULT 1,
    started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
    status TEXT NOT NULL, code TEXT, message TEXT, checked_by TEXT, tls_verified INTEGER, os_version TEXT,
    health_status TEXT NOT NULL DEFAULT 'skipped', health_at TEXT, health_value TEXT, health_raw TEXT, health_code TEXT, health_msg TEXT,
    alert_status TEXT NOT NULL DEFAULT 'skipped', alert_at TEXT, alert_code TEXT, alert_msg TEXT,
    n_total INTEGER, n_effective INTEGER, n_resolved INTEGER, n_uncertain INTEGER, n_acked INTEGER, n_manual INTEGER
);
CREATE INDEX IF NOT EXISTS ix_storage_health_batches_dev ON storage_health_batches (device_id, id);
CREATE INDEX IF NOT EXISTS ix_storage_health_batches_day ON storage_health_batches (device_id, day);
CREATE TABLE IF NOT EXISTS storage_health_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL, alert_id TEXT, state_raw TEXT, state_class TEXT NOT NULL, state_note TEXT NOT NULL DEFAULT '',
    manual INTEGER NOT NULL DEFAULT 0, severity_raw TEXT, alert_time TEXT, acked INTEGER, component TEXT, message TEXT,
    incomplete INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_storage_health_alerts_batch ON storage_health_alerts (batch_id, state_class);
"""


class UnityError(Exception):
    """連線／協定層錯誤。code 給程式判斷、訊息已清掉機密，可直接給人看。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ---------------------------------------------------------------- 設定（資料表）
def ensure_tables(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def module_enabled(conn) -> bool:
    row = conn.execute("SELECT enabled FROM feature_flags WHERE module_key = ?", (MODULE_KEY,)).fetchone()
    return bool(row and row["enabled"])


_NAME_RE = re.compile(r"^[^\s/\\][^/\\]{0,63}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-\.]{0,251}[A-Za-z0-9])?$")


def normalize_host(host: str) -> str:
    """只收 IP（v4／v6）或主機名。不收網址、路徑、帳密、埠（埠另填）。"""
    h = (host or "").strip()
    if not h:
        raise ih.ConfigError("請填 Storage 的 IP 或主機名")
    if "://" in h or "/" in h or "@" in h or "?" in h or " " in h:
        raise ih.ConfigError("位址只填 IP 或主機名（不含 https://、路徑、帳號密碼）")
    try:
        ip = ipaddress.ip_address(h.strip("[]"))
    except ValueError:
        ip = None
    if ip is not None:
        if not ALLOW_LOOPBACK_FOR_TEST and (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved):
            raise ih.ConfigError("不收本機／連結本地／保留位址（避免後端被當跳板去打自己或雲端中繼資料服務）")
        return str(ip)
    if ":" in h or not _HOST_RE.match(h):
        raise ih.ConfigError("位址格式不正確（只收 IP 或主機名）")
    if not ALLOW_LOOPBACK_FOR_TEST and h.lower() in ("localhost", "localhost.localdomain"):
        raise ih.ConfigError("不收本機位址")
    return h.lower()


def _clean_vendor(vendor: str) -> str:
    v = (vendor or "").strip().lower().replace(" ", "_")
    if v not in SUPPORTED_VENDORS:
        raise ih.ConfigError(f"品牌「{(vendor or '').strip() or '（空白）'}」目前不支援；現在只支援 Dell Unity，其他品牌之後再加")
    return v


def _clean_port(port) -> int:
    try:
        p = int(port if port not in (None, "") else DEFAULT_PORT)
    except (TypeError, ValueError):
        raise ih.ConfigError("埠必須是數字") from None
    if not 1 <= p <= 65535:
        raise ih.ConfigError("埠必須介於 1 到 65535")
    return p


def _validate(conn, *, name, vendor, host, port, credential, ca_bundle, trust_self_signed, note, exclude_id=None) -> dict:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise ih.ConfigError("請填設備名稱（64 字內，不含斜線）")
    dup = conn.execute("SELECT id FROM storage_devices WHERE name = ?", (name,)).fetchone()
    if dup and dup["id"] != exclude_id:
        raise ih.ConfigError(f"已有同名的設備「{name}」")
    cred = (credential or "").strip()
    if cred and not credential_store.valid_choice(conn, cred, CRED_KIND):
        raise ih.ConfigError(f"收集憑證「{cred}」不存在或不是儲存設備（storage）類型，請到系統設定 → 收集憑證設定")
    ca = (ca_bundle or "").strip()
    ih.check_ca_bundle(ca)
    return {"name": name, "vendor": _clean_vendor(vendor), "host": normalize_host(host), "port": _clean_port(port),
            "credential": cred, "ca_bundle": ca, "trust_self_signed": 1 if trust_self_signed else 0,
            "note": (note or "").strip()[:300]}


def _public(r) -> dict:
    d = dict(r)
    d["trust_self_signed"] = bool(d["trust_self_signed"])
    d["enabled"] = bool(d["enabled"])
    d["vendor_label"] = SUPPORTED_VENDORS.get(d["vendor"], d["vendor"])
    d["last_test_ok"] = None if d["last_test_ok"] is None else bool(d["last_test_ok"])
    d["last_tls_verified"] = None if d["last_tls_verified"] is None else bool(d["last_tls_verified"])
    try:
        d["last_identity"] = json.loads(d["last_identity"]) if d["last_identity"] else None
    except ValueError:
        d["last_identity"] = None
    d["__no"] = d["id"]          # 項次固定＝設備 id，排序後同一台仍是同一個號碼
    return d


def list_devices(conn) -> list[dict]:
    return [_public(r) for r in conn.execute("SELECT * FROM storage_devices ORDER BY id")]


def get_device(conn, device_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM storage_devices WHERE id = ?", (device_id,)).fetchone()
    return _public(r) if r else None


def create_device(conn, user: str | None, *, name, vendor="dell_unity", host, port=DEFAULT_PORT, credential="", ca_bundle="",
                  trust_self_signed=False, enabled=True, note="") -> dict:
    v = _validate(conn, name=name, vendor=vendor, host=host, port=port, credential=credential, ca_bundle=ca_bundle,
                  trust_self_signed=trust_self_signed, note=note)
    cur = conn.execute(
        "INSERT INTO storage_devices (name, vendor, host, port, credential, ca_bundle, trust_self_signed, enabled, note, created_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (v["name"], v["vendor"], v["host"], v["port"], v["credential"], v["ca_bundle"], v["trust_self_signed"],
         1 if enabled else 0, v["note"], user))
    conn.commit()
    return get_device(conn, cur.lastrowid)


def update_device(conn, device_id: int, *, name, vendor="dell_unity", host, port=DEFAULT_PORT, credential=None, ca_bundle="",
                  trust_self_signed=False, enabled=True, note="") -> dict:
    old = get_device(conn, device_id)
    if old is None:
        raise LookupError("查無這台設備")
    cred = old["credential"] if credential is None else credential       # None＝沿用目前選擇
    v = _validate(conn, name=name, vendor=vendor, host=host, port=port, credential=cred, ca_bundle=ca_bundle,
                  trust_self_signed=trust_self_signed, note=note, exclude_id=device_id)
    # 位址／憑證／TLS 設定一改，上次的測試結果就不再代表現況：清掉，避免畫面還掛著舊的「成功」
    changed = any(old[k] != v[k] for k in ("host", "port", "credential", "ca_bundle", "vendor")) or bool(old["trust_self_signed"]) != bool(v["trust_self_signed"])
    conn.execute(
        "UPDATE storage_devices SET name=?, vendor=?, host=?, port=?, credential=?, ca_bundle=?, trust_self_signed=?, enabled=?, note=?, "
        "updated_at=datetime('now','localtime') WHERE id=?",
        (v["name"], v["vendor"], v["host"], v["port"], v["credential"], v["ca_bundle"], v["trust_self_signed"],
         1 if enabled else 0, v["note"], device_id))
    if any(old[k] != v[k] for k in ("host", "port", "vendor")):
        purge_collected(conn, device_id)       # 端點換了：舊收集資料屬於別的設備，不再顯示成這台的現況
    if changed:
        conn.execute("UPDATE storage_devices SET last_test_at=NULL, last_test_ok=NULL, last_test_code=NULL, last_test_msg=NULL, "
                     "last_test_by=NULL, last_identity=NULL, last_tls_verified=NULL WHERE id=?", (device_id,))
    conn.commit()
    return get_device(conn, device_id)


def purge_collected(conn, device_id: int) -> None:
    """清掉這台設備在本系統裡的收集結果（只動本機表，不碰 Storage）。"""
    conn.execute("DELETE FROM storage_collect_items WHERE run_id IN (SELECT id FROM storage_collect_runs WHERE device_id = ?)", (device_id,))
    conn.execute("DELETE FROM storage_collect_runs WHERE device_id = ?", (device_id,))
    conn.execute("DELETE FROM storage_health_alerts WHERE batch_id IN (SELECT id FROM storage_health_batches WHERE device_id = ?)", (device_id,))
    conn.execute("DELETE FROM storage_health_batches WHERE device_id = ?", (device_id,))      # S3 每日檢查結果同樣屬於舊端點


def delete_device(conn, device_id: int) -> bool:
    """只刪「這個系統裡的設定」，不碰 Storage 本身。"""
    purge_collected(conn, device_id)
    cur = conn.execute("DELETE FROM storage_devices WHERE id = ?", (device_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------- HTTP（唯讀 GET）
def _base_url(host: str, port: int) -> str:
    h = f"[{host}]" if ":" in host else host
    scheme = "http" if ALLOW_PLAINTEXT_FOR_TEST else "https"
    return f"{scheme}://{h}:{port}"


def _insecure_context() -> ssl.SSLContext:
    """「信任自簽」旗標才會用：不驗憑證與主機名。連線有被中間人冒充的風險，所以每台預設關、結果會標「未驗證 TLS」。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class UnitySession:
    """一台 Unity 的一次連線。**每次都新建、用完即關**：Cookie 只存在這個物件的 client 裡，不會跟別台共用。"""

    def __init__(self, host: str, port: int, username: str, password: str, *, ca_bundle: str = "",
                 trust_self_signed: bool = False, timeout: float | None = None):
        import httpx
        self.base = _base_url(host, port)
        self._parts = urlsplit(self.base)
        self._user, self._pw = username, password
        self._established = False      # 受保護的 GET 成功且拿到 session cookie 後為 True：之後只靠 cookie，不再送密碼
        if trust_self_signed:
            verify = _insecure_context()
        else:
            ca = ih.check_ca_bundle(ca_bundle)
            verify = ssl.create_default_context(cafile=ca) if ca else True      # True＝驗證且用系統信任清單
        self.tls_verified = not trust_self_signed
        self._client = httpx.Client(verify=verify, follow_redirects=False, trust_env=False,
                                     timeout=TIMEOUT_S if timeout is None else timeout)

    def close(self) -> None:
        try:
            self._client.cookies.clear()       # 丟棄 session（不送 logout：那是 POST）
        finally:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _scrub(self, text) -> str:
        return ih.scrub(text, [self._pw, base64.b64encode(f"{self._user}:{self._pw}".encode()).decode()])

    # -- 只發 GET ----------------------------------------------------------
    @staticmethod
    def _origin(parts) -> tuple:
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
        return (parts.scheme.lower(), (parts.hostname or "").lower(), port)

    def _redirect_target(self, current: str, location: str | None, status: int) -> str:
        """驗證重新導向目標。只放行同源（scheme＋host＋port）且路徑在 /api/ 或 /cas/ 之下；其餘一律拒絕（訊息不含目標位址）。"""
        refuse = UnityError("redirect", f"收到重新導向（HTTP {status}）但目標不是同一台 Storage 的 API／登入路徑，已拒絕；請確認位址與埠")
        loc = (location or "").strip()
        if not loc or loc.startswith("//") or "\\" in loc or any(ord(c) < 32 for c in loc):
            raise refuse
        target = urljoin(current, loc)
        parts = urlsplit(target)
        if parts.username or parts.password or "@" in parts.netloc:
            raise refuse
        try:
            same = self._origin(parts) == self._origin(self._parts)
        except ValueError:
            raise refuse from None
        if not same:
            raise refuse
        if "%" in parts.path:
            raise refuse                  # 不收百分比編碼的路徑（避免 %2e%2e／雙重編碼繞過路徑限制）
        segs = parts.path.split("/")
        if ".." in segs or "." in segs or not parts.path.startswith(REDIRECT_PREFIXES):
            raise refuse
        return self.base + parts.path + (f"?{parts.query}" if parts.query else "")

    def _once(self, url: str, params: dict | None, auth) -> tuple[int, dict, bytes]:
        """送一次 GET（不自動跟轉址）。回 (status, 小寫 header, body bytes)。傳輸層錯誤轉成 UnityError。"""
        import httpx
        try:
            with self._client.stream("GET", url, params=params, headers=dict(REST_HEADERS), auth=auth) as r:
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_BODY:
                        raise UnityError("too_big", "回應超過大小上限，已中止（不是預期的 Storage API 回應）")
                return r.status_code, {k.lower(): v for k, v in r.headers.items()}, bytes(buf)
        except UnityError:
            raise
        except httpx.TimeoutException:
            raise UnityError("timeout", "連線逾時，請確認網路、埠與防火牆") from None
        except httpx.ConnectError as exc:
            msg = str(exc).lower()
            if "certificate" in msg or "ssl" in msg or "tls" in msg:
                raise UnityError("tls", "TLS 憑證驗證失敗：請設定 CA 憑證檔；自簽憑證可勾「信任自簽憑證」（有風險，見說明）") from None
            raise UnityError("unreachable", "連不上目標主機，請確認位址、埠與防火牆") from None
        except httpx.HTTPError as exc:
            raise UnityError("http_error", f"連線失敗（{type(exc).__name__}）") from None

    def get(self, path_or_url: str, params: dict | None = None, *, authed: bool = True) -> tuple[int, object]:
        """GET 一次。回 (status, 已解析的 JSON)。任何不是「乾淨 JSON 且 2xx」的情況都丟 UnityError。
        同源重新導向（/api/、/cas/）最多跟 3 次；帳密只在還沒建立 session 時、且只送往同源。"""
        import quiet_window as qw
        qw.network_gate()       # AUD-02：真正送出前的最後一道（有任務脈絡才會擋；人為診斷端點已在路由確認過）
        url = self.abs_url(path_or_url)
        cur_params = params
        hops = 0
        while True:
            # 帳密：探測（authed=False）永遠不帶；已建立 session 的第一跳只用 cookie；轉址跳（可能是重新登入）才帶
            login_hop = hops > 0 and urlsplit(url).path.startswith("/cas/")      # 帳密只送給第一個請求（尚無 cookie）與 /cas/ 登入跳；轉回 /api/ 一律只用 cookie
            auth = (self._user, self._pw) if authed and ((hops == 0 and not self._established) or login_hop) else None
            status, hdrs, raw = self._once(url, cur_params, auth)
            if 300 <= status < 400:
                if hops >= MAX_REDIRECTS:
                    raise UnityError("redirect", f"重新導向超過 {MAX_REDIRECTS} 次（HTTP {status}），已停止")
                url = self._redirect_target(url, hdrs.get("location"), status)
                cur_params = None           # Location 已含完整查詢字串
                hops += 1
                continue
            break
        ctype = (hdrs.get("content-type") or "").lower()
        if status == 401:
            raise UnityError("unauthorized", "帳號或密碼被拒絕（HTTP 401）；為避免鎖定帳號，不會自動重試")
        if status == 403:
            raise UnityError("forbidden", "帳號已通過驗證但沒有權限讀取（HTTP 403）；請確認該帳號的角色可唯讀 Storage 資訊")
        text = raw.decode("utf-8", errors="replace")      # 僅供錯誤訊息／HTML 判斷；成功路徑下面另用嚴格解碼
        if status >= 400:
            raise UnityError("http_error", f"Storage 回應錯誤（HTTP {status}）：{self._scrub(text)[:120]}")
        if "html" in ctype or text.lstrip()[:1] == "<":
            raise UnityError("html", "收到 HTML 頁面（可能是登入頁或代理畫面），不是 Storage API 的 JSON 回應")
        try:
            strict = raw.decode("utf-8")          # 成功資料不可被靜默換字：無效 UTF-8＝格式錯誤（不記原 bytes）
        except UnicodeDecodeError:
            raise UnityError("bad_encoding", "回應含無效的 UTF-8 位元組，資料可能已損壞，不當成功資料使用") from None
        try:
            body = json.loads(strict)
        except ValueError:
            raise UnityError("bad_json", "回應不是有效的 JSON") from None
        if authed and 200 <= status < 300 and len(self._client.cookies) > 0:
            self._established = True
        return status, body

    # -- 分頁 ------------------------------------------------------------
    def abs_url(self, path_or_url: str) -> str:
        """只允許同主機、同協定。相對路徑一律補成 base 下的 /api/ 路徑；絕對網址必須與 base 的 scheme＋host＋port 相同。"""
        s = (path_or_url or "").strip()
        parts = urlsplit(s)
        if parts.scheme or parts.netloc:
            if (parts.scheme.lower() != self._parts.scheme or parts.netloc.lower() != self._parts.netloc.lower()
                    or parts.username or parts.password):
                raise UnityError("bad_next", "分頁連結指向別的主機或協定，已拒絕（只跟隨同一台 Storage）")
            path = parts.path + (f"?{parts.query}" if parts.query else "")
        else:
            if s.startswith("//") or not s.startswith("/api/"):
                raise UnityError("bad_next", "分頁連結格式不正確，已拒絕")
            path = s
        return self.base + path

    def get_pages(self, path: str, params: dict | None = None, *, max_pages: int = MAX_PAGES, deadline: float | None = None) -> list[dict]:
        """讀完整份清單：回所有頁的 entries（每筆取 content）。next 只跟隨同主機；重複的 next（循環）或超過頁數上限＝丟錯。"""
        q = dict(params or {})
        q.setdefault("per_page", PER_PAGE)
        q.setdefault("page", 1)
        url = path
        seen: set[str] = set()
        out: list[dict] = []
        try:
            return self._pages(path, q, url, seen, out, max_pages, deadline)
        except UnityError as exc:
            exc.partial = out          # 已讀到的頁：呼叫端可保留為「部分證據」並標不完整，不可當成零筆或全部
            raise

    def _pages(self, path, q, url, seen, out, max_pages, deadline=None):
        base_path = urlsplit(path).path
        for _ in range(max_pages):
            if deadline is not None and time.monotonic() > deadline:
                raise UnityError("run_timeout", "整次收集超過時間上限，已停止讀取下一頁")
            full = self.abs_url(url)
            if urlsplit(full).path != base_path:        # next 必須仍是同一個集合端點，不跟隨到 action 或別的資源
                raise UnityError("bad_next", "分頁連結指向別的資源路徑，已拒絕（只跟隨同一個集合）")
            key = _canon(full)
            if key in seen:
                raise UnityError("page_loop", "分頁連結重複出現（循環），已停止")
            seen.add(key)
            _st, body = self.get(url, q if url == path else None)
            if not isinstance(body, dict) or not isinstance(body.get("entries"), list):
                raise UnityError("bad_shape", "回應格式不是預期的清單（缺少 entries）")
            for e in body["entries"]:
                out.append(e.get("content", e) if isinstance(e, dict) else e)
            nxt = next((lk.get("href") for lk in (body.get("links") or []) if isinstance(lk, dict) and lk.get("rel") == "next" and lk.get("href")), None)
            if not nxt:
                return out
            if nxt.startswith("&"):
                # 真機 Unity 的 next 是相對於 @base 的查詢片段（如 "&page=2"）：併回目前的查詢參數，仍只打同一個集合端點
                merged = dict(q)
                merged.update(dict(parse_qsl(nxt[1:])))
                nxt = path + "?" + urlencode(merged)
            url = nxt
        raise UnityError("page_limit", f"分頁超過上限（{max_pages} 頁），已停止；清單可能異常龐大或 next 無限延伸")


def _canon(full_url: str) -> str:
    p = urlsplit(full_url)
    return f"{p.scheme}://{p.netloc.lower()}{p.path}?{urlencode(sorted(parse_qsl(p.query)))}"


# ---------------------------------------------------------------- 連線測試（人為）
def _record(conn, device_id: int, user: str | None, res: dict) -> None:
    conn.execute(
        "UPDATE storage_devices SET last_test_at=datetime('now','localtime'), last_test_ok=?, last_test_code=?, last_test_msg=?, "
        "last_test_by=?, last_identity=?, last_tls_verified=? WHERE id=?",
        (1 if res["ok"] else 0, res["code"], res["message"][:300], user,
         json.dumps(res["identity"], ensure_ascii=False) if res.get("identity") else None,
         None if res.get("tls_verified") is None else (1 if res["tls_verified"] else 0), device_id))
    conn.commit()


def check_connection(conn, device_id: int, user: str | None = None) -> dict:
    """連線測試。步驟：
      1. 憑證（kind=storage、適用範圍含這台）→ 取不到就停，不連線。
      2. 不需登入的 basicSystemInfo：只確認位址有 Unity 在回應（**不算帳密有效**）。
      3. 受保護的 system GET（帶 Basic 帳密）：成功才算帳密有效（AUTH-1）；順便讀回名稱／型號／序號當身分。
    只發 GET；回傳與寫入都不含密碼。"""
    dev = get_device(conn, device_id)
    if dev is None:
        raise LookupError("查無這台設備")
    t0 = time.monotonic()

    def done(ok, code, message, **kw) -> dict:
        res = {"ok": ok, "code": code, "message": message, "reachable": kw.get("reachable", False),
               "authenticated": kw.get("authenticated", False), "identity": kw.get("identity"),
               "tls_verified": kw.get("tls_verified"), "elapsed_ms": int((time.monotonic() - t0) * 1000),
               "device_id": device_id, "name": dev["name"]}
        now = get_device(conn, device_id)
        keys = ("host", "port", "credential", "ca_bundle", "trust_self_signed", "vendor")
        if now is None or any(bool(now[k]) != bool(dev[k]) if k == "trust_self_signed" else now[k] != dev[k] for k in keys):
            res["stale"] = True            # 測試進行中設定被改過：這份結果屬於舊端點，不寫入最近測試欄位、前端也不得當成新設定的結果
            res["message"] = "（設定在測試進行中被修改，此結果屬於舊設定，已不採用）" + res["message"]
            return res
        _record(conn, device_id, user, res)
        return res

    if dev["vendor"] not in SUPPORTED_VENDORS:
        return done(False, "unsupported", f"品牌「{dev['vendor']}」目前不支援")
    if not dev["credential"]:
        return done(False, "credential", "尚未選定收集憑證；請到系統設定 → 收集憑證新增 storage 類型的憑證，再回來挑選")
    try:
        username, password = credential_store.resolve_storage(conn, dev["credential"], dev["host"], key_path=KEY_PATH)
    except credential_store.CredentialRefused as exc:
        credential_store.audit_use(conn, dev["credential"], dev["host"], False, "Storage 連線測試被拒：" + str(exc))
        return done(False, "credential", str(exc))
    reachable = False
    sess = None
    try:
        try:
            sess = UnitySession(dev["host"], dev["port"], username, password, ca_bundle=dev["ca_bundle"],
                                trust_self_signed=dev["trust_self_signed"])
        except ih.ConfigError as exc:
            return done(False, "config", str(exc))
        tls = sess.tls_verified
        try:
            # 位址探測（不用登入）。失敗就不要再拿帳密去試，避免把密碼送給不對的對象。
            _st, basic = sess.get(PATH_BASIC, {"compact": "true"}, authed=False)
            reachable = True
            if not (isinstance(basic, dict) and isinstance(basic.get("entries"), list)):
                raise UnityError("bad_shape", "位址有回應，但不像 Dell Unity 的 API（格式不符）")
            # 受保護的 GET：這一步成功才算帳密有效
            rows = sess.get_pages(PATH_SYSTEM, {"fields": SYSTEM_FIELDS})
            if not rows or not isinstance(rows[0], dict):
                raise UnityError("bad_shape", "帳號已通過驗證，但讀不到系統資訊（回傳為空）")
            c = rows[0]
            identity = {"name": c.get("name"), "model": c.get("model"), "serial": c.get("serialNumber")}
        except UnityError as exc:
            msg = sess._scrub(exc.message)
            credential_store.audit_use(conn, dev["credential"], dev["host"], False, f"Storage 連線測試失敗（{exc.code}）：{msg}")
            return done(False, exc.code, msg, reachable=reachable, authenticated=False, tls_verified=tls)
    finally:
        if sess is not None:
            sess.close()
    credential_store.audit_use(conn, dev["credential"], dev["host"], True, "Storage 連線測試")
    label = " ".join(x for x in (identity.get("model"), identity.get("name")) if x) or "Dell Unity"
    note = "" if tls else "（⚠ 未驗證 TLS 憑證）"
    return done(True, "ok", f"連線成功：{label}，帳密已由受保護的 API 驗證{note}", reachable=True, authenticated=True,
                identity=identity, tls_verified=tls)
