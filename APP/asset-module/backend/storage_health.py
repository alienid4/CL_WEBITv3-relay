"""儲存設備（Dell Unity）每日唯讀健康與有效告警檢查（批 S3）。

## 範圍

每台（啟用、已選收集憑證）每日一次（台北時間 07:00 起），只發 GET：系統健康（/api/types/system 的 health）與告警
（/api/types/alert/instances，含分頁）。結果存本機新表（每台每次一批）。S1 設定／連線測試、S2 人為收集不變。
**不做**：任何通知（Teams／Email 都不碰）、高頻輪詢、自動建立／合併資產、確認／清除告警、寫入類 HTTP。

## 排程與去重（TIME-1）

- 週末也跑（07:00 不在盤中靜默窗內）。窗＝平日 08:30–15:00（左閉右開）；窗內不連線，窗外第一個 tick 補（當天內）。
- 每台每個台北日期只成功一批：當天已有 status=ok 的批次（含人為「立即檢查」成功的）就不再跑。
- 失敗／不完整重試：首次 + 最多 MAX_RETRIES 次重試，間隔遞增（5／15／45 分鐘）。批次的 trigger 分 auto（當天第一次）／retry（重試）／
  manual（人為），attempt_no 記第幾次，所以「失敗」與「重試」查得出來。帳密被拒、權限不足、憑證／設定問題、TLS 失敗這類
  **不重試**（重試只會多鎖一次帳號），隔天才再試一次。
- 一個 tick 最多處理一台（同步執行，避免卡住排程器太久，也是限流）；每台新建 session，Cookie 不共用。
- 逐台前重查靜默窗；執行中進窗（底層 network_gate）→ 中止、不寫批次、不算失敗，窗外下一個 tick 補。

## 結果語意（絕不「零＝正常」）

- 健康：只存設備回的原始 health 值與說明文字，**不翻譯數值常數、不憑說明文字判故障**。讀不到／缺 health 欄位＝「健康未知」。
- 告警 state（OE 5.0 起官方語義）：Inactive＝已解除；Active_Auto／Active_Manual＝有效（Manual 只讀，不代處置）；
  Updating、缺 state、未知枚舉、數值（常數未驗證不猜）＝**不確定**。已讀（isAcknowledged）≠ 解除，另外計數。
- 告警區段失敗時各計數是 NULL（未知），不是 0；部分成功（分頁中斷、格式異常）計數只是下限，標「不完整」。
- 部分成功 ≠ 全部正常；「告警空、健康未知」並存，不升格成全正常。資料超過 STALE_HOURS 小時標過期。
- 欄位名稱依 Dell Unity 公開文件推定，**未實機驗證**。
"""
from __future__ import annotations

import contextlib
import json
import re
import threading
import time
from datetime import datetime, timedelta

import credential_store
import integration_http as ih
import quiet_window as qw
import storage_collect as sc
import storage_unity as su
from storage_unity import UnityError, UnitySession

ENTRY = "storage_health_tick"
DAILY_AT_H, DAILY_AT_M = 7, 0           # 台北時間 07:00 起算「今天到期」
MAX_RETRIES = 3                          # 首次之外最多重試幾次
BACKOFF_MIN = (5, 15, 45)                # 第 n 次嘗試失敗後，等這麼多分鐘才重試
KEEP_BATCHES = 60                        # 每台保留最近幾批（摘要）
KEEP_ALERT_BATCHES = 14                  # 其中最近幾批保留告警明細
RUN_BUDGET_S = 90.0
STALE_HOURS = 30                         # 最近一批成功資料超過這麼多小時＝過期（每日一次，容忍 6 小時延遲）
#: 這些失敗代碼不在同一天重試（重試無助益，且可能多鎖一次帳號）。
NON_RETRYABLE = {"unauthorized", "forbidden", "credential", "config", "unsupported", "tls"}

HEALTH_FIELDS = "id,name,model,serialNumber,health"
ALERT_FIELDS = "id,state,severity,timestamp,message,isAcknowledged,component"
PATH_ALERT = "/api/types/alert/instances"

#: 官方文字枚舉名（正規化後：小寫、去非英數）→ 分類。數值常數沒有實機／apidocs 驗證，**不猜**：STATE_NUMERIC 留空，
#: 日後核對 /apidocs 再填；在那之前整數 state 一律「不確定」。
STATE_NAMES = {"inactive": "resolved", "activeauto": "effective", "activemanual": "effective"}
STATE_NUMERIC: dict[int, str] = {}



def ensure_tables(conn) -> None:
    su.ensure_tables(conn)          # 每日檢查的表定義在 storage_unity.SCHEMA（刪除設備／端點變更時要一起清）


# ---------------------------------------------------------------- 小工具
def _ts(t: datetime | None = None) -> str:
    return (t or qw.local_now()).strftime("%Y-%m-%d %H:%M:%S")


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _raw_text(v) -> str:
    """原始值照存（字串原樣、其他型別 JSON 化），讓人與稽核看得到設備實際回了什麼。"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()[:100]
    try:
        return json.dumps(v, ensure_ascii=False)[:100]
    except (TypeError, ValueError):
        return repr(v)[:100]


def classify_state(raw) -> tuple[str, bool, str]:
    """回 (分類, 是否 Manual, 說明)。分類：effective（有效）／resolved（已解除）／uncertain（不確定）。
    Updating、缺 state、未知枚舉、數值（常數未驗證）一律 uncertain，絕不當成零有效或已解除。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "uncertain", False, "缺少 state 欄位"
    if isinstance(raw, bool):
        return "uncertain", False, "state 型別異常"
    if isinstance(raw, int):
        cls = STATE_NUMERIC.get(raw)
        if cls:
            return cls, False, ""
        return "uncertain", False, "state 是數值枚舉；對照常數尚未驗證，不猜"
    if not isinstance(raw, str):
        return "uncertain", False, "state 型別異常"
    key = re.sub(r"[^a-z0-9]", "", raw.lower())
    if key in STATE_NAMES:
        return STATE_NAMES[key], key == "activemanual", ""
    if key == "updating":
        return "uncertain", False, "狀態更新中（Updating），尚未確定"
    return "uncertain", False, "未知的 state 枚舉"


def _ack(v):
    return None if not isinstance(v, bool) else (1 if v else 0)


def _health_block(row: dict) -> tuple[str, str, str, str]:
    """回 (health_status, value文字, raw JSON, 說明)。read＝有 health.value；missing＝回應 200 但沒有可用的 health。"""
    h = row.get("health") if isinstance(row, dict) else None
    if not isinstance(h, dict) or "value" not in h or h.get("value") is None or isinstance(h.get("value"), bool):
        return "missing", "", "", "系統回應沒有可用的 health 欄位（健康未知）"
    desc = h.get("descriptions")
    shown = {"value": h.get("value"), "descriptions": [str(x)[:200] for x in desc][:5] if isinstance(desc, list) else None}
    return "read", _raw_text(h.get("value")), json.dumps(shown, ensure_ascii=False)[:1500], ""


# ---------------------------------------------------------------- 單台檢查
def check_device(conn, device_id: int, *, trigger: str = "manual", user: str | None = None, attempt_no: int = 1,
                 now: datetime | None = None, auto: bool = False) -> dict:
    """檢查一台並存一批。永遠回摘要（失敗也寫一批、保留原因）。設備不存在丟 LookupError。
    auto=True（排程）：在 Run 底下執行，每次外連前由 network_gate 重查靜默窗；進窗就中止，回 {"paused": True}，不寫批次。"""
    dev = su.get_device(conn, device_id)
    if dev is None:
        raise LookupError("查無這台設備")
    started_dt = qw._naive(now) if now is not None else qw.local_now()
    started = _ts(started_dt)
    day = started[:10]
    t0 = time.monotonic()
    res: dict = {"health_status": "skipped", "alert_status": "skipped", "os_version": None, "tls": None}
    rows: list[dict] = []

    def finish(status: str, code: str, message: str) -> dict:
        return _store(conn, dev, trigger, user, attempt_no, day, started, status, code, message, res, rows)

    if dev["vendor"] not in su.SUPPORTED_VENDORS:
        return finish("failed", "unsupported", f"品牌「{dev['vendor']}」目前不支援")
    if not dev["credential"]:
        return finish("failed", "credential", "尚未選定收集憑證；請到系統設定 → 收集憑證新增 storage 類型的憑證，再回來挑選")
    try:
        username, password = credential_store.resolve_storage(conn, dev["credential"], dev["host"], key_path=su.KEY_PATH)
    except credential_store.CredentialRefused as exc:
        credential_store.audit_use(conn, dev["credential"], dev["host"], False, "Storage 每日檢查被拒：" + str(exc))
        return finish("failed", "credential", str(exc))

    run_ctx = qw.use(qw.Run(ENTRY, human=False, db_path=qw._path_of(conn))) if auto else contextlib.nullcontext()
    sess = None
    try:
        with run_ctx:
            try:
                sess = UnitySession(dev["host"], dev["port"], username, password, ca_bundle=dev["ca_bundle"],
                                    trust_self_signed=dev["trust_self_signed"])
            except ih.ConfigError as exc:
                return finish("failed", "config", str(exc))
            res["tls"] = sess.tls_verified
            deadline = t0 + RUN_BUDGET_S
            # 1. 位址探測（不送帳密）：不像 Unity 就不要再把密碼送過去
            try:
                _st, basic = sess.get(su.PATH_BASIC, {"fields": sc.PATH_BASIC_FIELDS}, authed=False)
                if not (isinstance(basic, dict) and isinstance(basic.get("entries"), list)):
                    raise UnityError("bad_shape", "位址有回應，但不像 Dell Unity 的 API（格式不符）")
                bc = basic["entries"][0].get("content", {}) if basic["entries"] and isinstance(basic["entries"][0], dict) else {}
                res["os_version"] = bc.get("softwareVersion") if isinstance(bc, dict) else None
            except UnityError as exc:
                msg = sess._scrub(exc.message)
                credential_store.audit_use(conn, dev["credential"], dev["host"], False, f"Storage 每日檢查失敗（{exc.code}）：{msg}")
                return finish("failed", exc.code, msg)
            aborted = ""
            # 2. 健康（受保護 GET；401 在這裡就停，不再往下打）
            try:
                got = sess.get_pages(su.PATH_SYSTEM, {"fields": HEALTH_FIELDS}, deadline=deadline)
                row0 = got[0] if got and isinstance(got[0], dict) else None
                if row0 is None:
                    res.update(health_status="missing", health_msg="系統回應為空（健康未知）")
                else:
                    hs, hv, hraw, hmsg = _health_block(row0)
                    res.update(health_status=hs, health_value=hv, health_raw=hraw, health_msg=hmsg or None, health_at=_ts())
            except UnityError as exc:
                res.update(health_status="failed", health_code=exc.code, health_msg=sess._scrub(exc.message)[:300])
                if exc.code in sc.ABORT_CODES:
                    aborted = exc.code
            # 3. 告警（含分頁）。健康失敗（非連線／驗證層）不影響告警照讀 → HEALTH-1：告警空但健康未知並存
            if aborted:
                res.update(alert_status="skipped", alert_code="skipped", alert_msg=f"因前一步失敗（{aborted}）而略過")
            elif time.monotonic() > deadline:
                res.update(alert_status="skipped", alert_code="run_timeout", alert_msg="整次檢查超過時間上限，略過")
            else:
                try:
                    got = sess.get_pages(PATH_ALERT, {"fields": ALERT_FIELDS}, deadline=deadline)
                    bad = sum(1 for g in got if not isinstance(g, dict))
                    rows.extend(_alert_rows(got))
                    if bad:
                        for r in rows:
                            r["incomplete"] = 1
                        res.update(alert_status="partial", alert_code="bad_item", alert_msg=f"{bad} 筆告警項目格式異常，已略過（結果不完整）", alert_at=_ts())
                    else:
                        res.update(alert_status="ok", alert_at=_ts())
                except UnityError as exc:
                    part = _alert_rows(list(getattr(exc, "partial", []) or []))
                    for r in part:
                        r["incomplete"] = 1
                    rows.extend(part)
                    res.update(alert_status="partial" if part else "failed", alert_code=exc.code, alert_msg=sess._scrub(exc.message)[:300],
                               alert_at=_ts() if part else None)
    except qw.QuietWindowBlocked:
        return {"paused": True, "stored": False, "ok": False, "device_id": dev["id"], "name": dev["name"],
                "message": "執行中進入盤中靜默窗，已中止；不算失敗，窗外下一個 tick 補"}
    finally:
        if sess is not None:
            sess.close()

    h_ok = res["health_status"] == "read"
    a_ok = res["alert_status"] == "ok"
    got_any = h_ok or res["alert_status"] in ("ok", "partial")
    if h_ok and a_ok:
        credential_store.audit_use(conn, dev["credential"], dev["host"], True, "Storage 每日檢查")
        return finish("ok", "ok", "檢查完成" + ("" if res["tls"] else "（⚠ 未驗證 TLS 憑證）"))
    parts = []
    if not h_ok:
        parts.append("健康：" + {"missing": "讀不到可用欄位", "failed": "失敗", "skipped": "略過"}[res["health_status"]] + (f"（{res['health_msg']}）" if res.get("health_msg") else ""))
    if not a_ok:
        parts.append("告警：" + {"partial": "不完整", "failed": "失敗", "skipped": "略過"}[res["alert_status"]] + (f"（{res['alert_msg']}）" if res.get("alert_msg") else ""))
    code = res.get("health_code") if res["health_status"] == "failed" else None
    code = code or res.get("alert_code") or ("health_missing" if res["health_status"] == "missing" else "partial")
    credential_store.audit_use(conn, dev["credential"], dev["host"], got_any, "Storage 每日檢查" + ("（部分成功）" if got_any else "（全部失敗）"))
    return finish("partial" if got_any else "failed", code, "結果不完整：" + "；".join(parts))


def _alert_rows(got) -> list[dict]:
    out = []
    for r in got:
        if not isinstance(r, dict):
            continue
        cls, manual, note = classify_state(r.get("state"))
        out.append({"alert_id": _s(r.get("id")), "state_raw": _raw_text(r.get("state")), "state_class": cls, "state_note": note,
                    "manual": 1 if manual else 0, "severity_raw": _raw_text(r.get("severity")), "alert_time": _s(r.get("timestamp"))[:40],
                    "acked": _ack(r.get("isAcknowledged")), "component": _raw_text(r.get("component")), "message": _s(r.get("message"))[:300],
                    "incomplete": 0})
    return out


def _counts(res: dict, rows: list[dict]) -> dict:
    if res["alert_status"] in ("failed", "skipped"):
        return {k: None for k in ("n_total", "n_effective", "n_resolved", "n_uncertain", "n_acked", "n_manual")}
    return {"n_total": len(rows), "n_effective": sum(r["state_class"] == "effective" for r in rows),
            "n_resolved": sum(r["state_class"] == "resolved" for r in rows), "n_uncertain": sum(r["state_class"] == "uncertain" for r in rows),
            "n_acked": sum(r["acked"] == 1 for r in rows), "n_manual": sum(bool(r["manual"]) and r["state_class"] == "effective" for r in rows)}


def _store(conn, dev, trigger, user, attempt_no, day, started, status, code, message, res, rows) -> dict:
    finished = _ts()
    cnt = _counts(res, rows)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")         # 先取寫入鎖再檢查：避免檢查後、寫入前設定或別的批次被改（TOCTOU）
    now_dev = su.get_device(conn, dev["id"])
    summary = {"ok": status == "ok", "status": status, "complete": status == "ok", "code": code, "message": message, "device_id": dev["id"],
               "name": dev["name"], "started_at": started, "finished_at": finished, "trigger": trigger, "attempt_no": attempt_no,
               "health_status": res["health_status"], "alert_status": res["alert_status"], **cnt}
    if not sc._same_endpoint(dev, now_dev):
        conn.rollback()
        summary.update(stale=True, ok=False, batch_id=None, message="（設定在檢查進行中被修改，此結果屬於舊設定，已不採用）" + message)
        return summary
    if trigger != "manual" and conn.execute("SELECT 1 FROM storage_health_batches WHERE device_id=? AND day=? AND status='ok'", (dev["id"], day)).fetchone():
        conn.rollback()           # 當天已有成功批次（例如人為「立即檢查」剛成功）：這次自動結果不再另存
        summary.update(duplicate=True, batch_id=None, message="今天已有成功批次，這次自動結果不另存")
        return summary
    cur = conn.execute(
        "INSERT INTO storage_health_batches (device_id, device_name, endpoint, day, trigger, attempt_no, started_at, finished_at, status, code, message, "
        "checked_by, tls_verified, os_version, health_status, health_at, health_value, health_raw, health_code, health_msg, alert_status, alert_at, "
        "alert_code, alert_msg, n_total, n_effective, n_resolved, n_uncertain, n_acked, n_manual) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (dev["id"], dev["name"], f"{dev['host']}:{dev['port']}", day, trigger, attempt_no, started, finished, status, code, message[:500], user,
         None if res["tls"] is None else (1 if res["tls"] else 0), _s(res.get("os_version")) or None, res["health_status"], res.get("health_at"),
         res.get("health_value"), res.get("health_raw"), res.get("health_code"), res.get("health_msg"), res["alert_status"], res.get("alert_at"),
         res.get("alert_code"), res.get("alert_msg"), cnt["n_total"], cnt["n_effective"], cnt["n_resolved"], cnt["n_uncertain"], cnt["n_acked"], cnt["n_manual"]))
    bid = cur.lastrowid
    conn.executemany(
        "INSERT INTO storage_health_alerts (batch_id, alert_id, state_raw, state_class, state_note, manual, severity_raw, alert_time, acked, component, message, incomplete) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(bid, r["alert_id"], r["state_raw"], r["state_class"], r["state_note"], r["manual"], r["severity_raw"], r["alert_time"], r["acked"],
          r["component"], r["message"], r["incomplete"]) for r in rows])
    ids = [r[0] for r in conn.execute("SELECT id FROM storage_health_batches WHERE device_id=? ORDER BY id DESC", (dev["id"],))]
    for oid in ids[KEEP_ALERT_BATCHES:]:
        conn.execute("DELETE FROM storage_health_alerts WHERE batch_id = ?", (oid,))
    for oid in ids[KEEP_BATCHES:]:
        conn.execute("DELETE FROM storage_health_batches WHERE id = ?", (oid,))
    conn.commit()
    summary["batch_id"] = bid
    return summary


# ---------------------------------------------------------------- 排程：到期判斷
def eligible(conn, dev: dict) -> str | None:
    """None＝納入每日檢查；否則回不納入的原因（畫面顯示用，不是失敗）。"""
    if not dev["enabled"]:
        return "設備已停用"
    if dev["vendor"] not in su.SUPPORTED_VENDORS:
        return "品牌不支援"
    if not dev["credential"]:
        return "尚未選收集憑證"
    if not credential_store.valid_choice(conn, dev["credential"], su.CRED_KIND):
        return "所選收集憑證不存在或不是 storage 類型"
    return None


def due_info(conn, dev: dict, t: datetime) -> dict:
    """這台在時間 t 要不要跑自動檢查。回 {due, trigger, attempt_no, reason}。純讀。"""
    day = t.strftime("%Y-%m-%d")
    why = eligible(conn, dev)
    if why:
        return {"due": False, "reason": why}
    if (t.hour, t.minute) < (DAILY_AT_H, DAILY_AT_M):
        return {"due": False, "reason": "未到每日檢查時間"}
    rows = conn.execute("SELECT * FROM storage_health_batches WHERE device_id=? AND day=? ORDER BY id", (dev["id"], day)).fetchall()
    if any(r["status"] == "ok" for r in rows):
        return {"due": False, "reason": "今天已成功"}
    auto = [r for r in rows if r["trigger"] in ("auto", "retry")]
    n = len(auto)
    if n == 0:
        return {"due": True, "trigger": "auto", "attempt_no": 1, "reason": "每日首次"}
    last = auto[-1]
    if last["code"] in NON_RETRYABLE:
        return {"due": False, "reason": "失敗原因不會因重試而好轉，今天不再試（" + str(last["code"]) + "）"}
    if n >= 1 + MAX_RETRIES:
        return {"due": False, "reason": "今天重試次數已用完"}
    try:
        nxt = datetime.strptime(last["finished_at"], "%Y-%m-%d %H:%M:%S") + timedelta(minutes=BACKOFF_MIN[min(n - 1, len(BACKOFF_MIN) - 1)])
    except ValueError:
        return {"due": False, "reason": "上次失敗時間格式異常，今天不再自動重試"}
    if t < nxt:
        return {"due": False, "reason": f"等待重試（{nxt.strftime('%H:%M')} 之後）"}
    return {"due": True, "trigger": "retry", "attempt_no": n + 1, "reason": f"第 {n} 次重試"}


_TICK_LOCK = threading.Lock()        # 排程 tick 與窗外補跑執行緒不會同時檢查同一台


_EXC_UNTIL: dict = {}
EXC_COOLDOWN_MIN = 60       # 未預期例外沒有留下批次，改以記憶體冷卻：同一台一小時內不再自動連線


def tick(conn, now: datetime | None = None) -> int:
    """排程入口。模組未啟用、沒有設備、沒有到期的：什麼都不做（也不記延後）。有到期的：窗內一律不連線並記延後，
    窗外第一個 tick 補（當天內）。一個 tick 最多處理一台。回處理台數。"""
    try:
        if not su.module_enabled(conn):
            return 0
    except Exception:  # noqa: BLE001 - feature_flags 讀不到＝視同未啟用
        return 0
    if not _TICK_LOCK.acquire(blocking=False):
        return 0
    try:
        ensure_tables(conn)
        t = qw._naive(now)
        pick = None
        for dev in su.list_devices(conn):
            if _EXC_UNTIL.get(dev["id"], datetime.min) > t:
                continue                    # 上次發生未預期例外：冷卻中，不每分鐘反覆連線（避免鎖帳號）
            info = due_info(conn, dev, t)
            if info["due"]:
                pick = (dev, info)
                break
        if pick is None:
            return 0
        if qw.defer_if_quiet(conn, "storage_health_tick", t if now is not None else None):
            return 0
        dev, info = pick
        try:
            r = check_device(conn, dev["id"], trigger=info["trigger"], attempt_no=info["attempt_no"], now=now, auto=True)
        except Exception as exc:  # noqa: BLE001 - 單台例外不擋排程器；寫系統日誌，下一輪依到期判斷再試
            import logging

            logging.getLogger("storage_health").error("儲存設備每日檢查例外（%s）：%s", dev["name"], type(exc).__name__)
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            _EXC_UNTIL[dev["id"]] = t + timedelta(minutes=EXC_COOLDOWN_MIN)
            return 0
        return 0 if r.get("paused") else 1
    finally:
        _TICK_LOCK.release()


# ---------------------------------------------------------------- 讀取
def _public(r) -> dict:
    d = dict(r)
    d["complete"] = d["status"] == "ok"
    d["tls_verified"] = None if d["tls_verified"] is None else bool(d["tls_verified"])
    try:
        d["health_detail"] = json.loads(d["health_raw"]) if d.get("health_raw") else None
    except ValueError:
        d["health_detail"] = None
    d.pop("health_raw", None)
    return d


def verdict(b: dict | None, t: datetime | None = None) -> dict:
    """畫面用的一句話與層級：絕不輸出「全部正常」。level：complete｜incomplete｜failed｜none。"""
    if b is None:
        return {"level": "none", "text": "尚未檢查", "stale": False}
    t = t or qw.local_now()
    stale = False
    try:
        stale = t - datetime.strptime(b["finished_at"], "%Y-%m-%d %H:%M:%S") > timedelta(hours=STALE_HOURS)
    except (ValueError, KeyError):
        stale = True
    h, a = b["health_status"], b["alert_status"]
    if b["status"] == "failed":
        text = "檢查失敗，沒有取得資料"
    else:
        hp = {"read": f"健康原始值 {b.get('health_value')}", "missing": "健康未知（讀不到欄位）", "failed": "健康未知（讀取失敗）", "skipped": "健康未知（略過）"}[h]
        if a == "ok":
            ap = f"有效告警 {b['n_effective']}、已解除 {b['n_resolved']}、不確定 {b['n_uncertain']}"
            if not b["n_total"] and h != "read":
                ap += "（告警空不代表正常：健康未知）"
        elif a == "partial":
            ap = f"告警不完整（至少有效 {b['n_effective']}、不確定 {b['n_uncertain']}）"
        else:
            ap = "告警未取得"
        text = f"{hp}；{ap}"
    if stale:
        text += "　⚠ 資料已過期"
    return {"level": "complete" if b["status"] == "ok" else ("failed" if b["status"] == "failed" else "incomplete"), "text": text, "stale": stale}


def latest_batches(conn) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for r in conn.execute("SELECT * FROM storage_health_batches WHERE id IN (SELECT MAX(id) FROM storage_health_batches GROUP BY device_id)"):
        d = _public(r)
        d["verdict"] = verdict(d)
        out[d["device_id"]] = d
    return out


def schedule_info(conn, dev: dict, t: datetime | None = None) -> dict:
    t = t or qw.local_now()
    info = due_info(conn, dev, t)
    return {"included": eligible(conn, dev) is None, "reason": info["reason"], "due": info["due"]}


def get_detail(conn, device_id: int, batch_id: int | None = None) -> dict:
    """唯讀明細：預設最近一批「有告警明細或成功」的；同時附最近一批（可能失敗），讓畫面標出『最近一次其實失敗了』。"""
    dev = su.get_device(conn, device_id)
    if dev is None:
        raise LookupError("查無這台設備")
    if batch_id is not None:
        r = conn.execute("SELECT * FROM storage_health_batches WHERE id=? AND device_id=?", (batch_id, device_id)).fetchone()
        if r is None:
            raise LookupError("查無這批檢查")
    else:
        r = conn.execute("SELECT * FROM storage_health_batches WHERE device_id=? AND status IN ('ok','partial') ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
        r = r or conn.execute("SELECT * FROM storage_health_batches WHERE device_id=? ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
    latest = conn.execute("SELECT * FROM storage_health_batches WHERE device_id=? ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
    hist = [{k: x[k] for k in ("id", "day", "trigger", "attempt_no", "started_at", "finished_at", "status", "code", "message")}
            for x in conn.execute("SELECT * FROM storage_health_batches WHERE device_id=? ORDER BY id DESC LIMIT 30", (device_id,))]
    shown = _public(r) if r else None
    if shown:
        shown["verdict"] = verdict(shown)
    alerts: list[dict] = []
    if shown:
        for n, a in enumerate(conn.execute("SELECT * FROM storage_health_alerts WHERE batch_id=? ORDER BY id", (shown["id"],)), 1):
            d = dict(a)
            d["__no"] = n
            d["manual"] = bool(d["manual"])
            d["acked"] = None if d["acked"] is None else bool(d["acked"])
            d["incomplete"] = bool(d["incomplete"])
            alerts.append(d)
    lat = _public(latest) if latest else None
    if lat:
        lat["verdict"] = verdict(lat)
    return {"device_id": device_id, "batch": shown, "latest": lat, "alerts": alerts, "history": hist, "schedule": schedule_info(conn, dev),
            "daily_at": f"{DAILY_AT_H:02d}:{DAILY_AT_M:02d}", "alerts_pruned": shown is not None and not alerts and (shown.get("n_total") or 0) > 0}
