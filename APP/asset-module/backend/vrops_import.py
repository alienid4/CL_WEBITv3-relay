"""Aria Operations API 匯入（Q92）：取代「RVTools 資料夾監看」的新來源。

使用者決定（2026-10-29）：一個 Aria Operations API 涵蓋所有 vCenter，不直連個別 vCenter。
舊的資料夾監看（vcenter_autoimport）原樣保留，這裡是新增的、建議使用的來源。

## 這支負責什麼

* 設定（網址、驗證來源、CA 憑證路徑、排程）存 app_settings；帳密存加密憑證庫（credential_store，kind='vrops'），
  任何 API 都不回傳密碼，每次取用留稽核。
* 連線測試：只做「登入＋讀第一頁 VM」，不寫任何資料。
* 匯入：抓 VM／主機／叢集／datastore 與關聯 → 轉成 RVTools 同形狀 → 交給 rvtools_import.import_records（同一個寫入器）。
* 執行紀錄（vrops_runs）：每次的筆數、錯誤、結果，可追溯。

## 部分失敗的處理

VM 清單抓不到＝整次失敗（沒東西可匯，不動資料）。主機／叢集／datastore／關聯／屬性其中一段失敗＝
繼續匯入但狀態記為「部分成功」並列出哪一段失敗，不假裝完整。
"""
from __future__ import annotations

import json
import threading
from urllib.parse import urlsplit

import credential_store
import integration_http as ih
import vrops_constants as C
import vrops_mapper as mp
from db import get_setting, set_setting
from vrops_client import VropsClient, VropsError

MODULE_KEY = "vrops_api"
CRED_NAME = "integration:vrops"
KEY_PATH = credential_store.KEY_PATH_DEFAULT      # 加密金鑰檔位置（測試會換成暫存路徑）
CRED_KIND = "vrops"

K_CRED = "vrops_credential"        # 從「收集憑證」挑的那一筆名稱（空＝舊固定名稱／同類型唯一一筆）
K_URL = "vrops_url"
K_SOURCE = "vrops_auth_source"
K_CA = "vrops_ca_bundle"
K_ENABLED = "vrops_enabled"
K_INTERVAL = "vrops_interval_hours"
DEFAULT_INTERVAL_HOURS = 24
MIN_INTERVAL_HOURS = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS vrops_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    trigger TEXT NOT NULL,                   -- manual / schedule
    started_by TEXT,
    status TEXT NOT NULL,                    -- running / ok / partial / failed
    vms INTEGER, hosts INTEGER, clusters INTEGER, datastores INTEGER,
    inserted INTEGER, updated INTEGER, pending_review INTEGER,
    error_count INTEGER NOT NULL DEFAULT 0,
    message TEXT,                            -- 人看的一句話
    detail TEXT                              -- JSON：錯誤清單、各分頁筆數
);
CREATE INDEX IF NOT EXISTS idx_vrops_runs_started ON vrops_runs(started_at);
"""

_run_lock = threading.Lock()


def ensure_tables(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def module_enabled(conn) -> bool:
    row = conn.execute("SELECT enabled FROM feature_flags WHERE module_key = ?", (MODULE_KEY,)).fetchone()
    return bool(row and row["enabled"])


# ---------------------------------------------------------------- 設定
def credential_name(conn, override: str | None = None) -> str | None:
    """目前取用哪一筆憑證（收集憑證庫，kind='vrops'）。找不到＝None。"""
    return credential_store.resolve_for_kind(conn, CRED_KIND, override or get_setting(conn, K_CRED, "") or None, CRED_NAME)


def get_config(conn) -> dict:
    name = credential_name(conn)
    cred = next((c for c in credential_store.list_public(conn) if c["name"] == name), None) if name else None
    return {
        "url": get_setting(conn, K_URL, "") or "",
        "auth_source": get_setting(conn, K_SOURCE, "") or "",
        "ca_bundle": get_setting(conn, K_CA, "") or "",
        "enabled": get_setting(conn, K_ENABLED, "0") == "1",
        "interval_hours": int(get_setting(conn, K_INTERVAL, str(DEFAULT_INTERVAL_HOURS)) or DEFAULT_INTERVAL_HOURS),
        "credential": name or "",
        "username": cred["username"] if cred else "",
        "has_password": bool(cred and cred["has_secret"]),       # 只告知「有沒有設」，永不回傳內容
    }


def save_config(conn, *, url: str, auth_source: str, ca_bundle: str, enabled: bool, interval_hours: int,
                credential: str | None = None) -> None:
    """驗證通過才存。帳密不在這裡存——請到「系統設定 → 收集憑證」新增 kind=vrops 的憑證，這裡只挑名稱。
    credential：None＝沿用目前設定；空字串＝清掉選擇（退回自動）。"""
    if url.strip():
        ih.normalize_base_url(url)
    ih.check_ca_bundle(ca_bundle)
    if interval_hours < MIN_INTERVAL_HOURS:
        raise ih.ConfigError(f"匯入間隔至少 {MIN_INTERVAL_HOURS} 小時")
    chosen = None if credential is None else (credential or "").strip()
    if chosen and not credential_store.valid_choice(conn, chosen, CRED_KIND):
        raise ih.ConfigError(f"收集憑證「{chosen}」不存在或不是 Aria Operations 類型，請到系統設定 → 收集憑證設定")
    if enabled and chosen == "":
        raise ih.ConfigError("啟用中不能清掉所選憑證；請改選另一筆，或先關閉再清除")
    if enabled and credential_name(conn, chosen) is None:
        raise ih.ConfigError("要啟用排程匯入，請先到系統設定 → 收集憑證新增 Aria Operations 類型的憑證，再回來挑選")
    if enabled and not url.strip():
        raise ih.ConfigError("要啟用排程匯入，請先填 API 網址")
    if chosen is not None:
        set_setting(conn, K_CRED, chosen)
    set_setting(conn, K_URL, url.strip().rstrip("/"))
    set_setting(conn, K_SOURCE, (auth_source or "").strip())
    set_setting(conn, K_CA, (ca_bundle or "").strip())
    set_setting(conn, K_ENABLED, "1" if enabled else "0")
    set_setting(conn, K_INTERVAL, str(int(interval_hours)))


# ---------------------------------------------------------------- 用戶端（測試可換）
def default_client_factory(cfg: dict, username: str, password: str) -> VropsClient:
    return VropsClient(cfg["url"], username, password, cfg["auth_source"], ih.make_transport(cfg["ca_bundle"] or None))


#: 測試把這個換成回傳假用戶端的函式：factory(cfg, username, password) -> 有 VropsClient 介面的物件
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
        raise ih.ConfigError("尚未選定收集憑證，或憑證解不開。請到系統設定 → 收集憑證設定 Aria Operations 類型的憑證")
    return CLIENT_FACTORY(cfg, got[0], got[1]), cfg, host


def _safe(exc: Exception, client=None) -> str:
    return ih.scrub(exc, [getattr(client, "_pw", ""), getattr(client, "_token", "") or ""])


# ---------------------------------------------------------------- 連線測試
def check_connection(conn, credential: str | None = None) -> dict:
    """登入＋讀第一頁 VM。不寫任何資料。"""
    try:
        client, cfg, host = _open_client(conn, credential)
    except ih.ConfigError as exc:
        return {"ok": False, "message": str(exc)}
    try:
        client.acquire_token()
        vms = client.list_resources(C.KIND_VM)
        credential_store.audit_use(conn, credential_name(conn, credential) or CRED_NAME, host, True, "連線測試")
        return {"ok": True, "message": f"連線成功，Aria Operations 回報 {len(vms)} 台虛擬機器", "vm_count": len(vms)}
    except (VropsError, ih.TransportError, ih.ConfigError) as exc:
        msg = _safe(exc, client)
        credential_store.audit_use(conn, credential_name(conn, credential) or CRED_NAME, host, False, "連線測試失敗：" + msg)
        return {"ok": False, "message": msg}
    finally:
        _release(client)


def _release(client) -> None:
    try:
        client.release_token()
    except (VropsError, ih.TransportError):
        pass  # 釋放權杖只是收尾，失敗不影響這次結果


# ---------------------------------------------------------------- 取回快照
def fetch_snapshot(client) -> tuple[mp.Snapshot, list[str]]:
    """抓全部資料。回 (快照, 警告清單)。VM 清單失敗直接丟錯；其他段落失敗記進警告。"""
    warnings: list[str] = []
    snap = mp.Snapshot()
    snap.vms = client.list_resources(C.KIND_VM)                      # 失敗＝整次失敗
    for attr, kind, label in (("hosts", C.KIND_HOST, "ESXi 主機"), ("clusters", C.KIND_CLUSTER, "叢集"),
                              ("datastores", C.KIND_DATASTORE, "Datastore")):
        try:
            setattr(snap, attr, client.list_resources(kind))
        except (VropsError, ih.TransportError) as exc:
            warnings.append(f"{label}清單讀取失敗：{_safe(exc, client)}")

    for group, keys, label in ((snap.vms, C.ALL_VM_KEYS, "虛擬機器"), (snap.hosts, C.ALL_HOST_KEYS, "ESXi 主機"),
                               (snap.datastores, C.ALL_DS_KEYS, "Datastore")):
        ids = [mp.rid(r) for r in group if mp.rid(r)]
        if not ids:
            continue
        try:
            snap.props.update(client.latest_properties(ids, keys))
        except (VropsError, ih.TransportError) as exc:
            warnings.append(f"{label}屬性讀取失敗（欄位會偏少）：{_safe(exc, client)}")

    def children(res_list, target: dict, kind: str, label: str):
        for res in res_list:
            try:
                rels = client.relationships(mp.rid(res), "CHILD")
            except (VropsError, ih.TransportError) as exc:
                warnings.append(f"{label} {mp.rname(res)} 的關聯讀取失敗：{_safe(exc, client)}")
                continue
            target[mp.rid(res)] = [mp.rid(r) for r in rels
                                   if ((r.get("resourceKey") or {}).get("resourceKindKey")) == kind]

    children(snap.clusters, snap.cluster_hosts, C.KIND_HOST, "叢集")
    children(snap.hosts, snap.host_vms, C.KIND_VM, "主機")
    children(snap.datastores, snap.ds_vms, C.KIND_VM, "Datastore")
    for ds in snap.datastores:
        try:
            parents = client.relationships(mp.rid(ds), "PARENT")
        except (VropsError, ih.TransportError) as exc:
            warnings.append(f"Datastore {mp.rname(ds)} 的上層關聯讀取失敗：{_safe(exc, client)}")
            continue
        snap.ds_hosts[mp.rid(ds)] = [mp.rid(r) for r in parents
                                     if ((r.get("resourceKey") or {}).get("resourceKindKey")) == C.KIND_HOST]
    return snap, warnings


# ---------------------------------------------------------------- 匯入
def _start_run(conn, trigger: str, user: str | None) -> int:
    from db import _now_local
    cur = conn.execute("INSERT INTO vrops_runs (started_at, trigger, started_by, status) VALUES (?,?,?, 'running')",
                       (_now_local(), trigger, user))
    conn.commit()
    return cur.lastrowid


def _finish_run(conn, run_id: int, status: str, message: str, *, stats: dict | None = None,
                result: dict | None = None, errors: list[str] | None = None) -> None:
    from db import _now_local
    stats, result, errors = stats or {}, result or {}, errors or []
    detail = {"errors": errors[:200], "extra_sheets": result.get("extra_sheets", {}),
              "vc_moved": result.get("vc_moved", []), "write_errors": result.get("errors", [])[:200]}
    conn.execute(
        "UPDATE vrops_runs SET finished_at=?, status=?, vms=?, hosts=?, clusters=?, datastores=?, inserted=?, updated=?, "
        "pending_review=?, error_count=?, message=?, detail=? WHERE id=?",
        (_now_local(), status, stats.get("vms"), stats.get("hosts"), stats.get("clusters"), stats.get("datastores"),
         result.get("inserted"), result.get("updated"), result.get("pending_review"),
         len(errors) + len(result.get("errors", [])), message[:500], json.dumps(detail, ensure_ascii=False), run_id))
    conn.commit()


def run_import(conn, trigger: str = "manual", user: str | None = None) -> dict:
    """匯入一次。回 {"run_id", "status", "message", "summary"}。同一時間只跑一次。"""
    import rvtools_import
    ensure_tables(conn)
    if not _run_lock.acquire(blocking=False):
        return {"run_id": None, "status": "busy", "message": "已有一次匯入正在進行，請稍後再試"}
    run_id = None
    client = None
    try:
        run_id = _start_run(conn, trigger, user)
        try:
            client, cfg, host = _open_client(conn)
        except ih.ConfigError as exc:
            _finish_run(conn, run_id, "failed", str(exc), errors=[str(exc)])
            return {"run_id": run_id, "status": "failed", "message": str(exc)}
        try:
            snap, warnings = fetch_snapshot(client)
        except (VropsError, ih.TransportError, ih.ConfigError) as exc:
            msg = _safe(exc, client)
            credential_store.audit_use(conn, credential_name(conn) or CRED_NAME, host, False, "匯入失敗：" + msg)
            _finish_run(conn, run_id, "failed", f"匯入失敗，資料沒有變動：{msg}", errors=[msg])
            return {"run_id": run_id, "status": "failed", "message": msg}
        credential_store.audit_use(conn, credential_name(conn) or CRED_NAME, host, True, f"匯入（{trigger}）")
        mapped = mp.map_snapshot(snap)
        result = rvtools_import.import_records(conn, mapped["records"], mapped["extra_sheets"],
                                               source="vcenter/aria-operations", origin="vCenter/Aria Operations")
        status = "partial" if (warnings or result.get("errors")) else "ok"
        stats = mapped["stats"]
        msg = (f"讀到 {stats['vms']} 台（新增 {result['inserted']}／更新 {result['updated']}／待確認 {result['pending_review']}）"
               f"，主機 {stats['hosts']}、叢集 {stats['clusters']}、Datastore {stats['datastores']}")
        if status == "partial":
            msg += f"；有 {len(warnings) + len(result.get('errors', []))} 項部分失敗，詳見紀錄"
        _finish_run(conn, run_id, status, msg, stats=stats, result=result, errors=warnings)
        return {"run_id": run_id, "status": status, "message": msg, "summary": {**stats, **{k: result[k] for k in ("inserted", "updated", "pending_review")}},
                "warnings": warnings, "write_errors": result.get("errors", [])}
    except Exception as exc:  # noqa: BLE001 - 沒預期到的錯：記進紀錄再丟，絕不讓 running 卡死、也不外洩機密
        msg = _safe(exc, client)
        if run_id is not None:
            _finish_run(conn, run_id, "failed", f"匯入中斷：{type(exc).__name__}：{msg}", errors=[msg])
        raise
    finally:
        if client is not None:
            _release(client)
        _run_lock.release()


def list_runs(conn, limit: int = 100) -> list[dict]:
    ensure_tables(conn)
    out = []
    for r in conn.execute("SELECT * FROM vrops_runs ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(r)
        try:
            d["detail"] = json.loads(d["detail"]) if d.get("detail") else {}
        except ValueError:
            d["detail"] = {}
        out.append(d)
    return out


def tick(conn) -> dict:
    """排程器每輪呼叫：模組開著＋已啟用＋距離上次嘗試超過間隔才跑。失敗也算一次嘗試（不每分鐘猛打）。"""
    from datetime import datetime, timedelta
    if not module_enabled(conn) or get_setting(conn, K_ENABLED, "0") != "1":
        return {"status": "disabled"}
    ensure_tables(conn)
    interval = max(MIN_INTERVAL_HOURS, int(get_setting(conn, K_INTERVAL, str(DEFAULT_INTERVAL_HOURS)) or DEFAULT_INTERVAL_HOURS))
    last = conn.execute("SELECT started_at FROM vrops_runs ORDER BY id DESC LIMIT 1").fetchone()
    if last:
        try:
            if datetime.now() - datetime.strptime(last["started_at"][:19], "%Y-%m-%d %H:%M:%S") < timedelta(hours=interval):
                return {"status": "not_due"}
        except ValueError:
            pass  # 時間欄壞掉就當作該跑了
    return run_import(conn, "schedule", None)
