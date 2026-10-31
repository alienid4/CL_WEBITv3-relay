"""WhatsUp Gold 目前告警（Q93）：定期把 WhatsUp 的告警抓回來，值班不必再手動複製 IP 去查。

## 定位（使用者 2026-10-29 決定）

硬體故障由 WhatsUp 自己處理，WhatsUp 就是硬體告警的唯一來源，本系統不另外掃硬體。
這裡只做兩件事：把「現在有哪些告警」存成清單（附變化歷史），並讓每個 IP 一鍵跳到查詢頁。
這份資料**不**餵進存活判定或健檢（理由見 docs/ui-v2/Q93設計_2026-10-29.md）。

## 資料

* whatsup_alerts：現在這一刻的告警（每台裝置一列）。
* whatsup_alert_history：每次變化（新增／狀態改變／解除）一列，只增不改。
* whatsup_runs：每次輪詢的結果。

## 安全閥

整份裝置清單沒抓完整（連線失敗、翻頁中斷）就**不更新**現況清單，也不會把舊告警當成「已解除」——
抓不到不等於沒事，畫面會標示資料已過期。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import credential_store
import integration_http as ih
import whatsup_constants as C
from db import get_setting, set_setting
from whatsup_client import WhatsupClient, WhatsupError, pick

MODULE_KEY = "whatsup_api"
CRED_NAME = "integration:whatsup"
KEY_PATH = credential_store.KEY_PATH_DEFAULT      # 加密金鑰檔位置（測試會換成暫存路徑）
CRED_KIND = "whatsup"

K_CRED = "whatsup_credential"      # 從「收集憑證」挑的那一筆名稱（空＝舊固定名稱／同類型唯一一筆）
K_URL = "whatsup_url"
K_CA = "whatsup_ca_bundle"
K_ENABLED = "whatsup_enabled"
K_INTERVAL = "whatsup_interval_min"
DEFAULT_INTERVAL_MIN = 5
MIN_INTERVAL_MIN = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS whatsup_alerts (
    device_id TEXT PRIMARY KEY,
    name TEXT, ip TEXT, state TEXT, problem TEXT, since TEXT, group_name TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS whatsup_alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    device_id TEXT NOT NULL, name TEXT, ip TEXT,
    event TEXT NOT NULL,                      -- new / changed / cleared
    old_state TEXT, new_state TEXT, problem TEXT, group_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_wug_hist_at ON whatsup_alert_history(at);
CREATE TABLE IF NOT EXISTS whatsup_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    trigger TEXT NOT NULL,                    -- manual / schedule
    started_by TEXT,
    status TEXT NOT NULL,                     -- ok / partial / failed
    devices INTEGER, alerts INTEGER, new_count INTEGER, cleared_count INTEGER,
    message TEXT
);
"""

_lock = threading.Lock()


def ensure_tables(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def module_enabled(conn) -> bool:
    row = conn.execute("SELECT enabled FROM feature_flags WHERE module_key = ?", (MODULE_KEY,)).fetchone()
    return bool(row and row["enabled"])


# ---------------------------------------------------------------- 設定
def credential_name(conn, override: str | None = None) -> str | None:
    """目前取用哪一筆憑證（收集憑證庫，kind='whatsup'）。找不到＝None。"""
    return credential_store.resolve_for_kind(conn, CRED_KIND, override or get_setting(conn, K_CRED, "") or None, CRED_NAME)


def get_config(conn) -> dict:
    name = credential_name(conn)
    cred = next((c for c in credential_store.list_public(conn) if c["name"] == name), None) if name else None
    return {
        "url": get_setting(conn, K_URL, "") or "",
        "ca_bundle": get_setting(conn, K_CA, "") or "",
        "enabled": get_setting(conn, K_ENABLED, "0") == "1",
        "interval_min": int(get_setting(conn, K_INTERVAL, str(DEFAULT_INTERVAL_MIN)) or DEFAULT_INTERVAL_MIN),
        "credential": name or "",
        "username": cred["username"] if cred else "",
        "has_password": bool(cred and cred["has_secret"]),
    }


def save_config(conn, *, url: str, ca_bundle: str, enabled: bool, interval_min: int, credential: str | None = None) -> None:
    """帳密不在這裡存——請到「系統設定 → 收集憑證」新增 kind=whatsup 的憑證，這裡只挑名稱。
    credential：None＝沿用目前設定；空字串＝清掉選擇（退回自動）。"""
    if url.strip():
        ih.normalize_base_url(url)
    ih.check_ca_bundle(ca_bundle)
    if interval_min < MIN_INTERVAL_MIN:
        raise ih.ConfigError(f"輪詢間隔至少 {MIN_INTERVAL_MIN} 分鐘")
    chosen = None if credential is None else (credential or "").strip()
    if chosen and not credential_store.valid_choice(conn, chosen, CRED_KIND):
        raise ih.ConfigError(f"收集憑證「{chosen}」不存在或不是 WhatsUp 類型，請到系統設定 → 收集憑證設定")
    if enabled and chosen == "":
        raise ih.ConfigError("啟用中不能清掉所選憑證；請改選另一筆，或先關閉再清除")
    if enabled and credential_name(conn, chosen) is None:
        raise ih.ConfigError("要啟用定期輪詢，請先到系統設定 → 收集憑證新增 WhatsUp 類型的憑證，再回來挑選")
    if enabled and not url.strip():
        raise ih.ConfigError("要啟用定期輪詢，請先填 API 網址")
    if chosen is not None:
        set_setting(conn, K_CRED, chosen)
    set_setting(conn, K_URL, url.strip().rstrip("/"))
    set_setting(conn, K_CA, (ca_bundle or "").strip())
    set_setting(conn, K_ENABLED, "1" if enabled else "0")
    set_setting(conn, K_INTERVAL, str(int(interval_min)))


# ---------------------------------------------------------------- 用戶端（測試可換）
def default_client_factory(cfg: dict, username: str, password: str) -> WhatsupClient:
    return WhatsupClient(cfg["url"], username, password, ih.make_transport(cfg["ca_bundle"] or None))


#: 測試換成回傳假用戶端的函式
CLIENT_FACTORY = default_client_factory


def _open_client(conn, credential: str | None = None):
    cfg = get_config(conn)
    if not cfg["url"]:
        raise ih.ConfigError("尚未設定 API 網址")
    name = credential_name(conn, credential)
    got = credential_store.get_for_use(conn, name, key_path=KEY_PATH) if name else None
    host = urlsplit(cfg["url"]).hostname or ""
    if got is None:
        credential_store.audit_use(conn, name or CRED_NAME, host, False, "取不到憑證（未設定或金鑰檔已更換）")
        raise ih.ConfigError("尚未選定收集憑證，或憑證解不開。請到系統設定 → 收集憑證設定 WhatsUp 類型的憑證")
    return CLIENT_FACTORY(cfg, got[0], got[1]), host


def _safe(exc: Exception, client=None) -> str:
    return ih.scrub(exc, [getattr(client, "_pw", ""), getattr(client, "_token", "") or ""])


def check_connection(conn, credential: str | None = None) -> dict:
    """登入＋讀一頁裝置。不寫任何告警資料。"""
    try:
        client, host = _open_client(conn, credential)
    except ih.ConfigError as exc:
        return {"ok": False, "message": str(exc)}
    try:
        client.acquire_token()
        devs = client.devices_overview()
        credential_store.audit_use(conn, credential_name(conn, credential) or CRED_NAME, host, True, "連線測試")
        return {"ok": True, "message": f"連線成功，WhatsUp Gold 回報 {len(devs)} 台裝置", "device_count": len(devs)}
    except (WhatsupError, ih.TransportError) as exc:
        msg = _safe(exc, client)
        credential_store.audit_use(conn, credential_name(conn, credential) or CRED_NAME, host, False, "連線測試失敗：" + msg)
        return {"ok": False, "message": msg}


# ---------------------------------------------------------------- 輪詢
def _since_text(v) -> str | None:
    if v in (None, ""):
        return None
    return str(v).replace("T", " ")[:19]


def _problem_text(status: dict) -> str:
    for k in C.F_PROBLEM_LIST:
        items = status.get(k)
        if isinstance(items, list) and items:
            names = [str(pick(i, C.F_PROBLEM_NAME) or "") for i in items if isinstance(i, dict)]
            names = [n for n in names if n]
            if names:
                return "、".join(names[:5]) + (f" 等 {len(names)} 項" if len(names) > 5 else "")
    return ""


def collect_alerts(client) -> tuple[list[dict], int, list[str]]:
    """抓裝置清單，挑出告警中的，補群組與問題說明。回 (告警清單, 裝置總數, 警告)。裝置清單失敗直接丟錯。"""
    warnings: list[str] = []
    devices = client.devices_overview()
    groups: dict[str, str] = {}
    try:
        for g in client.group_devices():
            gid = pick(g, C.F_ID)
            gname = pick(g, C.F_GROUP)
            if gid is not None and gname:
                groups.setdefault(str(gid), str(gname))
    except (WhatsupError, ih.TransportError) as exc:
        warnings.append(f"群組資訊讀取失敗（群組欄會空白）：{_safe(exc, client)}")
    alerts: list[dict] = []
    for d in devices:
        did = pick(d, C.F_ID)
        state = str(pick(d, C.F_STATE) or "")
        if did is None or state.lower() not in C.ALERT_STATES:
            continue
        problem, since = "", _since_text(pick(d, C.F_SINCE))
        try:
            st = client.device_status(str(did))
            problem = _problem_text(st)
            since = _since_text(pick(st, C.F_SINCE)) or since
        except (WhatsupError, ih.TransportError) as exc:
            warnings.append(f"裝置 {pick(d, C.F_NAME) or did} 的狀態讀取失敗（問題說明會空白）：{_safe(exc, client)}")
        alerts.append({"device_id": str(did), "name": str(pick(d, C.F_NAME) or ""), "ip": str(pick(d, C.F_IP) or "").strip(),
                       "state": C.STATE_LABEL.get(state.lower(), state), "problem": problem, "since": since,
                       "group_name": groups.get(str(did), str(pick(d, C.F_GROUP) or ""))})
    return alerts, len(devices), warnings


def apply_alerts(conn, alerts: list[dict], now: str) -> tuple[int, int]:
    """把這一輪的完整告警清單套用到現況表，並寫變化歷史。回 (新增數, 解除數)。"""
    cur = {r["device_id"]: dict(r) for r in conn.execute("SELECT * FROM whatsup_alerts")}
    seen = set()
    new = 0
    for a in alerts:
        seen.add(a["device_id"])
        old = cur.get(a["device_id"])
        if old is None:
            conn.execute("INSERT INTO whatsup_alerts (device_id, name, ip, state, problem, since, group_name, first_seen, last_seen) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", (a["device_id"], a["name"], a["ip"], a["state"], a["problem"], a["since"], a["group_name"], now, now))
            conn.execute("INSERT INTO whatsup_alert_history (at, device_id, name, ip, event, new_state, problem, group_name) VALUES (?,?,?,?,?,?,?,?)",
                         (now, a["device_id"], a["name"], a["ip"], "new", a["state"], a["problem"], a["group_name"]))
            new += 1
            continue
        if old["state"] != a["state"] or (old["problem"] or "") != (a["problem"] or ""):
            conn.execute("INSERT INTO whatsup_alert_history (at, device_id, name, ip, event, old_state, new_state, problem, group_name) VALUES (?,?,?,?,?,?,?,?,?)",
                         (now, a["device_id"], a["name"], a["ip"], "changed", old["state"], a["state"], a["problem"], a["group_name"]))
        conn.execute("UPDATE whatsup_alerts SET name=?, ip=?, state=?, problem=?, since=?, group_name=?, last_seen=? WHERE device_id=?",
                     (a["name"], a["ip"], a["state"], a["problem"], a["since"], a["group_name"], now, a["device_id"]))
    cleared = 0
    for did, old in cur.items():
        if did in seen:
            continue
        conn.execute("INSERT INTO whatsup_alert_history (at, device_id, name, ip, event, old_state, problem, group_name) VALUES (?,?,?,?,?,?,?,?)",
                     (now, did, old["name"], old["ip"], "cleared", old["state"], old["problem"], old["group_name"]))
        conn.execute("DELETE FROM whatsup_alerts WHERE device_id=?", (did,))
        cleared += 1
    conn.commit()
    return new, cleared


def _record_run(conn, trigger: str, user, status: str, message: str, devices=None, alerts=None, new=None, cleared=None) -> int:
    from db import _now_local
    cur = conn.execute("INSERT INTO whatsup_runs (at, trigger, started_by, status, devices, alerts, new_count, cleared_count, message) VALUES (?,?,?,?,?,?,?,?,?)",
                       (_now_local(), trigger, user, status, devices, alerts, new, cleared, message[:500]))
    conn.commit()
    return cur.lastrowid


def poll(conn, trigger: str = "manual", user: str | None = None) -> dict:
    from db import _now_local
    ensure_tables(conn)
    if not _lock.acquire(blocking=False):
        return {"status": "busy", "message": "已有一次輪詢正在進行，請稍後再試"}
    client = None
    try:
        try:
            client, host = _open_client(conn)
        except ih.ConfigError as exc:
            rid = _record_run(conn, trigger, user, "failed", str(exc))
            return {"run_id": rid, "status": "failed", "message": str(exc)}
        try:
            alerts, n_dev, warnings = collect_alerts(client)
        except (WhatsupError, ih.TransportError) as exc:
            msg = _safe(exc, client)
            credential_store.audit_use(conn, credential_name(conn) or CRED_NAME, host, False, "輪詢失敗：" + msg)
            rid = _record_run(conn, trigger, user, "failed", f"輪詢失敗，現況清單沒有更新（可能已過期）：{msg}")
            return {"run_id": rid, "status": "failed", "message": msg}
        credential_store.audit_use(conn, credential_name(conn) or CRED_NAME, host, True, f"輪詢（{trigger}）")
        new, cleared = apply_alerts(conn, alerts, _now_local())
        status = "partial" if warnings else "ok"
        msg = f"裝置 {n_dev} 台，告警中 {len(alerts)} 台（新增 {new}／解除 {cleared}）"
        if warnings:
            msg += f"；{len(warnings)} 項部分失敗"
        rid = _record_run(conn, trigger, user, status, msg, n_dev, len(alerts), new, cleared)
        return {"run_id": rid, "status": status, "message": msg, "warnings": warnings,
                "devices": n_dev, "alerts": len(alerts), "new": new, "cleared": cleared}
    finally:
        _lock.release()


# ---------------------------------------------------------------- 讀取
def last_run(conn) -> dict | None:
    ensure_tables(conn)
    r = conn.execute("SELECT * FROM whatsup_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def last_success(conn) -> dict | None:
    r = conn.execute("SELECT * FROM whatsup_runs WHERE status IN ('ok','partial') ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def current_alerts(conn) -> dict:
    """現況告警＋資料新鮮度。最後一次成功輪詢超過 3 倍間隔（或最近一次失敗）就標 stale。"""
    ensure_tables(conn)
    rows = [dict(r) for r in conn.execute("SELECT * FROM whatsup_alerts ORDER BY COALESCE(since, first_seen), name, device_id")]
    for i, r in enumerate(rows, 1):
        r["__no"] = i
    lr, ok = last_run(conn), last_success(conn)
    interval = int(get_setting(conn, K_INTERVAL, str(DEFAULT_INTERVAL_MIN)) or DEFAULT_INTERVAL_MIN)
    stale, why = False, ""
    if ok is None:
        stale, why = True, "尚未成功輪詢過"
    else:
        try:
            age = datetime.now() - datetime.strptime(ok["at"][:19], "%Y-%m-%d %H:%M:%S")
            if age > timedelta(minutes=interval * 3):
                stale, why = True, f"最後一次成功是 {int(age.total_seconds() // 60)} 分鐘前"
        except ValueError:
            stale, why = True, "最後成功時間無法判讀"
        if lr and lr["status"] == "failed":
            stale, why = True, "最近一次輪詢失敗：" + (lr["message"] or "")
    return {"rows": rows, "last_run": lr, "last_success_at": ok["at"] if ok else None, "stale": stale, "stale_reason": why}


def history(conn, limit: int = 500) -> list[dict]:
    ensure_tables(conn)
    return [{**dict(r), "__no": r["id"]} for r in conn.execute("SELECT * FROM whatsup_alert_history ORDER BY id DESC LIMIT ?", (limit,))]


def runs(conn, limit: int = 100) -> list[dict]:
    ensure_tables(conn)
    return [{**dict(r), "__no": r["id"]} for r in conn.execute("SELECT * FROM whatsup_runs ORDER BY id DESC LIMIT ?", (limit,))]


def tick(conn) -> dict:
    if not module_enabled(conn) or get_setting(conn, K_ENABLED, "0") != "1":
        return {"status": "disabled"}
    ensure_tables(conn)
    interval = max(MIN_INTERVAL_MIN, int(get_setting(conn, K_INTERVAL, str(DEFAULT_INTERVAL_MIN)) or DEFAULT_INTERVAL_MIN))
    lr = last_run(conn)
    if lr:
        try:
            if datetime.now() - datetime.strptime(lr["at"][:19], "%Y-%m-%d %H:%M:%S") < timedelta(minutes=interval):
                return {"status": "not_due"}
        except ValueError:
            pass  # 時間欄壞掉就當作該跑了
    return poll(conn, "schedule", None)
