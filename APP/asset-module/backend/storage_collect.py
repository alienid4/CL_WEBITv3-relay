"""儲存設備（Dell Unity）資料收集（批 S2）：人按按鈕才收，GET only，結果存本機。

## 範圍

收：設備身份（名稱／型號／序號／OE 版本）、Storage Pool 容量、LUN 容量、host、host initiator、host↔LUN 映射。
**不做**：排程（S3 每日 07:00 與告警是另一批）、不讀告警、不改 Storage 任何設定、不確認／清除告警、
不自動建立／合併／刪除資產（歧義只標「待核」，由人決定）。

## 鐵則（沿用 S1，不退步）

- 連線全部走 `storage_unity.UnitySession`（SSRF 位址限制、next 只跟同集合、嚴格 UTF-8、重新導向不跟隨、401 不重試）。
- **一次收集一批**：每台每次存一筆 run（含資料時間、狀態、失敗原因、各區段結果）；舊的 run 不被覆蓋，只保留最近 KEEP_RUNS 次。
- **失敗不是零筆**：某區段失敗／逾時／被中斷，該區段標「失敗」或「不完整」，已讀到的頁保留為部分證據（`incomplete=1`），
  run 狀態是 partial／failed；畫面不得把它顯示成「0 筆」。
- **兩台隔離**：每次新建 session；資料以 run_id（綁 device_id）分開，不同台的同一個物件 id 不會互蓋。
- 收集進行中設備設定被改（IP／埠／憑證…）：這份結果屬於舊端點，不寫入（回 stale）。
- 欄位名稱依 Dell Unity REST 公開文件推定，**未實機驗證**；容量原始值照回傳存放（文件寫單位是 bytes），不猜任何轉換常數、
  不把健康碼等列舉值硬翻成文字。
"""
from __future__ import annotations

import json
import time
from datetime import datetime

import credential_store
import integration_http as ih
import storage_unity as su
from storage_unity import UnityError, UnitySession

KEEP_RUNS = 10               # 每台保留最近幾次收集
RUN_BUDGET_S = 240.0         # 一次收集的總時間上限（同步請求）；超過就把剩下的區段標「略過」
#: 這些錯誤代表連線／驗證層出問題：繼續打下一個區段只會重複失敗（甚至鎖帳號），直接中止其餘區段。
ABORT_CODES = {"unauthorized", "tls", "redirect", "unreachable", "html", "timeout", "run_timeout"}

PATH_BASIC_FIELDS = "name,model,softwareVersion,apiVersion"
SYSTEM_FIELDS = "id,name,model,serialNumber"

#: (key, 顯示名, Unity 型別名, fields)。欄位名稱依公開文件推定，未實機驗證。
SECTIONS = [
    ("pool", "Storage Pool", "pool", "id,name,sizeTotal,sizeUsed,sizeFree,sizeSubscribed"),
    ("lun", "LUN", "lun", "id,name,wwn,sizeTotal,sizeUsed,sizeAllocated,pool"),
    ("host", "Host", "host", "id,name,description,osType,type"),
    ("initiator", "Host Initiator", "hostInitiator", "id,initiatorId,type,parentHost"),
    ("host_lun", "Host↔LUN 映射", "hostLUN", "id,host,lun,hlu,type"),
]
SECTION_LABEL = {"system": "設備身份", **{k: lab for k, lab, _t, _f in SECTIONS}}
KIND_LABEL = {"system": "設備", "pool": "Pool", "lun": "LUN", "host": "Host", "initiator": "Initiator", "host_lun": "映射"}


# ---------------------------------------------------------------- 小工具
def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ref_id(v):
    """Unity 的關聯欄位是 {"id": "..."}；容錯字串。取不到回 None。"""
    if isinstance(v, dict):
        v = v.get("id")
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    s = str(v).strip()
    return s or None


def _size(v):
    """容量原始值：只收整數（bool 不算）。其他型別回 None，並由呼叫端標待核。"""
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return None
    return v


def _s(v) -> str:
    return "" if v is None else str(v).strip()


class _Item(dict):
    """一筆待寫入的收集項目。"""

    def __init__(self, kind, obj_id, name, parent="", total=None, used=None, free=None, extra=None):
        super().__init__(kind=kind, obj_id=obj_id, name=name, parent=parent, size_total=total, size_used=used, size_free=free,
                         extra=extra or {}, review=[], incomplete=0)

    def flag(self, text: str) -> None:
        if text not in self["review"]:
            self["review"].append(text)


def _dups(items, key):
    seen: dict = {}
    for it in items:
        k = key(it)
        if k:
            seen.setdefault(k, []).append(it)
    return {k: v for k, v in seen.items() if len(v) > 1}


# ---------------------------------------------------------------- 各區段轉換
def _pool_items(rows):
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        it = _Item("pool", _s(r.get("id")), _s(r.get("name")), total=_size(r.get("sizeTotal")), used=_size(r.get("sizeUsed")),
                   free=_size(r.get("sizeFree")), extra={"subscribed": _size(r.get("sizeSubscribed"))})
        if not it["obj_id"]:
            it.flag("缺少物件 id")
        if any(r.get(k) is not None and _size(r.get(k)) is None for k in ("sizeTotal", "sizeUsed", "sizeFree")):
            it.flag("容量欄位不是非負整數，未採用")
        if it["size_total"] is not None and it["size_used"] is not None and it["size_used"] > it["size_total"]:
            it.flag("已用容量大於總容量")
        out.append(it)
    return out


def _lun_items(rows):
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        it = _Item("lun", _s(r.get("id")), _s(r.get("name")), parent=_ref_id(r.get("pool")) or "", total=_size(r.get("sizeTotal")),
                   used=_size(r.get("sizeUsed")), extra={"wwn": _s(r.get("wwn")), "allocated": _size(r.get("sizeAllocated")),
                                                         "pool_id": _ref_id(r.get("pool"))})
        if not it["obj_id"]:
            it.flag("缺少物件 id")
        if any(r.get(k) is not None and _size(r.get(k)) is None for k in ("sizeTotal", "sizeUsed")):
            it.flag("容量欄位不是非負整數，未採用")
        if it["size_total"] is not None and it["size_used"] is not None and it["size_used"] > it["size_total"]:
            it.flag("已用容量大於總容量")
        out.append(it)
    return out


def _host_items(rows):
    return [_Item("host", _s(r.get("id")), _s(r.get("name")), extra={"os": _s(r.get("osType")), "type": _s(r.get("type")),
                                                                        "description": _s(r.get("description"))})
            for r in rows if isinstance(r, dict)]


def _init_items(rows):
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append(_Item("initiator", _s(r.get("id")), _s(r.get("initiatorId")), parent=_ref_id(r.get("parentHost")) or "",
                         extra={"type": _s(r.get("type")), "host_id": _ref_id(r.get("parentHost"))}))
    return out


def _map_items(rows):
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append(_Item("host_lun", _s(r.get("id")), "", extra={"host_id": _ref_id(r.get("host")), "lun_id": _ref_id(r.get("lun")),
                                                                  "hlu": r.get("hlu") if isinstance(r.get("hlu"), int) and not isinstance(r.get("hlu"), bool) else None,
                                                                  "type": _s(r.get("type"))}))
    return out


_BUILD = {"pool": _pool_items, "lun": _lun_items, "host": _host_items, "initiator": _init_items, "host_lun": _map_items}


def _annotate(sec: dict[str, list[_Item]], state: dict[str, str]) -> None:
    """跨區段對照與歧義標記。只標「待核」，不改資料、不合併。state[key]＝ok／partial／failed／skipped。"""
    ok = lambda k: state.get(k) == "ok"      # noqa: E731
    for k, items in sec.items():
        for ids in _dups(items, lambda i: i["obj_id"]).values():
            for it in ids:
                it.flag("同一份清單中物件 id 重複出現")
    dup_ids = {k: set(_dups(items, lambda i: i["obj_id"]).keys()) for k, items in sec.items()}
    pools = {i["obj_id"]: i for i in sec.get("pool", []) if i["obj_id"] and i["obj_id"] not in dup_ids.get("pool", set())}
    luns = {i["obj_id"]: i for i in sec.get("lun", []) if i["obj_id"] and i["obj_id"] not in dup_ids.get("lun", set())}
    hosts = {i["obj_id"]: i for i in sec.get("host", []) if i["obj_id"] and i["obj_id"] not in dup_ids.get("host", set())}
    for g in _dups(sec.get("lun", []), lambda i: i["name"]).values():
        for it in g:
            it.flag("LUN 名稱重複（不同 id 同名）")
    for g in _dups(sec.get("host", []), lambda i: i["name"]).values():
        for it in g:
            it.flag("Host 名稱重複（不同 id 同名）")
    for it in sec.get("lun", []):
        pid = it["extra"].get("pool_id")
        if not pid:
            it.flag("沒有所屬 Pool")
        elif pid in dup_ids.get("pool", set()):
            it.flag("所屬 Pool 的 id 在來源中重複，關聯不確定")
        elif pid in pools:
            it["extra"]["pool_name"] = pools[pid]["name"]
        else:
            it.flag("所屬 Pool 在已收 Pool 清單中找不到" if ok("pool") else "Pool 清單不完整，無法確認所屬 Pool")
    wwn = _dups([i for i in sec.get("lun", []) if i["extra"].get("wwn")], lambda i: i["extra"]["wwn"])
    for g in wwn.values():
        for it in g:
            it.flag("WWN 重複")
    for it in sec.get("initiator", []):
        hid = it["extra"].get("host_id")
        if not hid:
            it.flag("沒有所屬 Host")
        elif hid in dup_ids.get("host", set()):
            it.flag("所屬 Host 的 id 在來源中重複，關聯不確定")
        elif hid in hosts:
            it["extra"]["host_name"] = hosts[hid]["name"]
        else:
            it.flag("所屬 Host 在已收 Host 清單中找不到" if ok("host") else "Host 清單不完整，無法確認所屬 Host")
    for g in _dups(sec.get("initiator", []), lambda i: i["name"]).values():
        if len({i["extra"].get("host_id") for i in g}) > 1:
            for it in g:
                it.flag("同一個 initiator 掛在多個 Host 底下")
    pairs: dict = {}
    for it in sec.get("host_lun", []):
        hid, lid = it["extra"].get("host_id"), it["extra"].get("lun_id")
        if not hid or not lid:
            it.flag("映射缺少 host 或 LUN 的關聯")
            continue
        h, lu = hosts.get(hid), luns.get(lid)
        if hid in dup_ids.get("host", set()):
            it.flag("映射的 Host id 在來源中重複，關聯不確定")
        if lid in dup_ids.get("lun", set()):
            it.flag("映射的 LUN id 在來源中重複，關聯不確定")
        if h:
            it["extra"]["host_name"] = h["name"]
        else:
            it.flag("映射的 Host 在已收 Host 清單中找不到" if ok("host") else "Host 清單不完整，無法確認映射的 Host")
        if lu:
            it["extra"]["lun_name"] = lu["name"]
        else:
            it.flag("映射的 LUN 在已收 LUN 清單中找不到" if ok("lun") else "LUN 清單不完整，無法確認映射的 LUN")
        it["parent"] = (h or {}).get("name", "") if h else ""
        it["name"] = lu["name"] if lu else ""
        pairs.setdefault((hid, lid), []).append(it)
    for g in pairs.values():
        if len(g) > 1:
            for it in g:
                it.flag("同一組 Host／LUN 出現多筆映射")


# ---------------------------------------------------------------- 收集
def _same_endpoint(a: dict, b: dict | None) -> bool:
    if b is None:
        return False
    for k in ("host", "port", "credential", "ca_bundle", "vendor"):
        if a[k] != b[k]:
            return False
    return bool(a["trust_self_signed"]) == bool(b["trust_self_signed"])


def collect_device(conn, device_id: int, user: str | None = None) -> dict:
    """人為收集一台。永遠回一份摘要（失敗也寫一筆 run，保留失敗原因）；設備不存在丟 LookupError。"""
    dev = su.get_device(conn, device_id)
    if dev is None:
        raise LookupError("查無這台設備")
    started = _ts()
    t0 = time.monotonic()
    items: list[_Item] = []
    sections: list[dict] = []
    identity: dict = {}
    tls = None

    def finish(code: str, message: str, status: str) -> dict:
        return _store(conn, dev, user, started, status, code, message, identity, sections, items, tls)

    def sec_row(key, status, rows=0, code=None, message=""):
        sections.append({"key": key, "label": SECTION_LABEL[key], "status": status, "rows": rows, "code": code, "message": message[:200]})

    if dev["vendor"] not in su.SUPPORTED_VENDORS:
        return finish("unsupported", f"品牌「{dev['vendor']}」目前不支援", "failed")
    if not dev["credential"]:
        return finish("credential", "尚未選定收集憑證；請到系統設定 → 收集憑證新增 storage 類型的憑證，再回來挑選", "failed")
    try:
        username, password = credential_store.resolve_storage(conn, dev["credential"], dev["host"], key_path=su.KEY_PATH)
    except credential_store.CredentialRefused as exc:
        credential_store.audit_use(conn, dev["credential"], dev["host"], False, "Storage 資料收集被拒：" + str(exc))
        return finish("credential", str(exc), "failed")

    sess = None
    try:
        try:
            sess = UnitySession(dev["host"], dev["port"], username, password, ca_bundle=dev["ca_bundle"],
                                trust_self_signed=dev["trust_self_signed"])
        except ih.ConfigError as exc:
            return finish("config", str(exc), "failed")
        tls = sess.tls_verified
        # 1. 位址探測（不送帳密）＋ OE 版本；2. 受保護的 system GET 證明帳密有效並取身分
        try:
            _st, basic = sess.get(su.PATH_BASIC, {"fields": PATH_BASIC_FIELDS}, authed=False)
            if not (isinstance(basic, dict) and isinstance(basic.get("entries"), list)):
                raise UnityError("bad_shape", "位址有回應，但不像 Dell Unity 的 API（格式不符）")
            bc = basic["entries"][0].get("content", {}) if basic["entries"] and isinstance(basic["entries"][0], dict) else {}
            rows = sess.get_pages(su.PATH_SYSTEM, {"fields": SYSTEM_FIELDS})
            if not rows or not isinstance(rows[0], dict):
                raise UnityError("bad_shape", "帳號已通過驗證，但讀不到系統資訊（回傳為空）")
            c = rows[0]
            identity = {"name": c.get("name"), "model": c.get("model") or bc.get("model"), "serial": c.get("serialNumber"),
                        "os_version": bc.get("softwareVersion"), "api_version": bc.get("apiVersion")}
            si = _Item("system", _s(c.get("id")), _s(c.get("name")), extra={k: v for k, v in identity.items() if k != "name"})
            if not identity["serial"]:
                si.flag("讀不到序號")
            items.append(si)
            sec_row("system", "ok", 1)
        except UnityError as exc:
            msg = sess._scrub(exc.message)
            credential_store.audit_use(conn, dev["credential"], dev["host"], False, f"Storage 資料收集失敗（{exc.code}）：{msg}")
            sec_row("system", "failed", 0, exc.code, msg)
            return finish(exc.code, msg, "failed")

        # 3. 各區段：失敗不影響已收到的；連線／驗證層錯誤則中止其餘區段
        collected: dict[str, list[_Item]] = {}
        state: dict[str, str] = {}
        aborted = ""
        for key, _lab, utype, fields in SECTIONS:
            if aborted:
                state[key] = "skipped"
                sec_row(key, "skipped", 0, "skipped", f"因前一步失敗（{aborted}）而略過")
                continue
            if time.monotonic() - t0 > RUN_BUDGET_S:
                aborted = "run_timeout"
                state[key] = "skipped"
                sec_row(key, "skipped", 0, "run_timeout", "整次收集超過時間上限，略過")
                continue
            try:
                got = sess.get_pages(f"/api/types/{utype}/instances", {"fields": fields}, deadline=t0 + RUN_BUDGET_S)
                malformed = sum(1 for g in got if not isinstance(g, dict))
                collected[key] = _BUILD[key](got)
                if malformed:      # 格式異常的項目不可靜默丟棄後仍標完整
                    for it in collected[key]:
                        it["incomplete"] = 1
                    state[key] = "partial"
                    sec_row(key, "partial", len(collected[key]), "bad_item", f"{malformed} 筆項目格式異常，已略過（結果不完整）")
                else:
                    state[key] = "ok"
                    sec_row(key, "ok", len(collected[key]))
            except UnityError as exc:
                msg = sess._scrub(exc.message)
                part = _BUILD[key](list(getattr(exc, "partial", []) or []))
                for it in part:
                    it["incomplete"] = 1
                collected[key] = part
                state[key] = "partial" if part else "failed"
                sec_row(key, state[key], len(part), exc.code, msg)
                if exc.code in ABORT_CODES:
                    aborted = exc.code
        _annotate(collected, state)
        for key, _l, _t, _f in SECTIONS:
            items.extend(collected.get(key, []))
    finally:
        if sess is not None:
            sess.close()

    bad = [s for s in sections if s["status"] != "ok"]
    got_any = any(s["status"] in ("ok", "partial") and s["key"] != "system" for s in sections)
    if not bad:
        ok_cnt = len(items)
        credential_store.audit_use(conn, dev["credential"], dev["host"], True, "Storage 資料收集")
        return finish("ok", f"收集完成：{ok_cnt} 筆（{su.SUPPORTED_VENDORS[dev['vendor']]}）" + ("" if tls else "（⚠ 未驗證 TLS 憑證）"), "ok")
    detail = "；".join(f"{s['label']}：{'不完整' if s['status'] == 'partial' else '失敗' if s['status'] == 'failed' else '略過'}"
                       + (f"（{s['message']}）" if s["message"] and s["status"] != "skipped" else "") for s in bad)
    credential_store.audit_use(conn, dev["credential"], dev["host"], got_any, "Storage 資料收集" + ("（部分成功）" if got_any else "（區段全部失敗）"))
    return finish("partial" if got_any else "sections_failed", f"部分資料未收齊，結果不完整：{detail}", "partial" if got_any else "failed")


def _store(conn, dev, user, started, status, code, message, identity, sections, items, tls) -> dict:
    finished = _ts()
    review_n = sum(1 for i in items if i["review"])
    summary = {
        "ok": status == "ok", "status": status, "complete": status == "ok", "code": code, "message": message,
        "device_id": dev["id"], "name": dev["name"], "started_at": started, "finished_at": finished,
        "identity": identity or None, "sections": sections, "item_count": len(items), "review_count": review_n,
        "tls_verified": tls,
    }
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")      # 先取寫入鎖再檢查設定：避免檢查後、寫入前設定被別的連線改掉（TOCTOU）
    now = su.get_device(conn, dev["id"])
    if not _same_endpoint(dev, now):
        conn.rollback()
        summary.update(stale=True, ok=False, run_id=None,
                       message="（設定在收集進行中被修改，此結果屬於舊設定，已不採用）" + message)
        return summary
    cur = conn.execute(
        "INSERT INTO storage_collect_runs (device_id, device_name, endpoint, started_at, finished_at, status, complete, code, message, "
        "collected_by, tls_verified, identity, sections, item_count, review_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (dev["id"], dev["name"], f"{dev['host']}:{dev['port']}", started, finished, status, 1 if status == "ok" else 0, code,
         message[:500], user, None if tls is None else (1 if tls else 0),
         json.dumps(identity, ensure_ascii=False) if identity else None, json.dumps(sections, ensure_ascii=False),
         len(items), review_n))
    rid = cur.lastrowid
    conn.executemany(
        "INSERT INTO storage_collect_items (run_id, kind, obj_id, name, parent, size_total, size_used, size_free, extra, review, incomplete) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(rid, i["kind"], i["obj_id"], i["name"], i["parent"], i["size_total"], i["size_used"], i["size_free"],
          json.dumps(i["extra"], ensure_ascii=False), "；".join(i["review"]), i["incomplete"]) for i in items])
    # 只保留最近 KEEP_RUNS 次（舊的整批刪；本機表，不碰 Storage）
    old = [r["id"] for r in conn.execute("SELECT id FROM storage_collect_runs WHERE device_id = ? ORDER BY id DESC LIMIT -1 OFFSET ?",
                                         (dev["id"], KEEP_RUNS))]
    for oid in old:
        conn.execute("DELETE FROM storage_collect_items WHERE run_id = ?", (oid,))
        conn.execute("DELETE FROM storage_collect_runs WHERE id = ?", (oid,))
    conn.commit()
    summary["run_id"] = rid
    return summary


# ---------------------------------------------------------------- 讀取
def _run_public(r) -> dict:
    d = dict(r)
    d["complete"] = bool(d["complete"])
    d["tls_verified"] = None if d["tls_verified"] is None else bool(d["tls_verified"])
    for k in ("identity", "sections"):
        try:
            d[k] = json.loads(d[k]) if d[k] else None
        except ValueError:
            d[k] = None
    return d


def latest_runs(conn) -> dict[int, dict]:
    """每台設備最近一次收集的摘要（含失敗的），給設備列表顯示。"""
    out: dict[int, dict] = {}
    for r in conn.execute("SELECT * FROM storage_collect_runs WHERE id IN (SELECT MAX(id) FROM storage_collect_runs GROUP BY device_id)"):
        d = _run_public(r)
        out[d["device_id"]] = d
    return out


def get_detail(conn, device_id: int | None = None, run_id: int | None = None) -> dict:
    """唯讀明細。預設：該台最近一次「有資料」的 run（沒有就最近一次）；回傳同時附最近一次 run，讓畫面能標出『最近一次其實失敗了』。"""
    if run_id is not None:
        r = conn.execute("SELECT * FROM storage_collect_runs WHERE id = ?", (run_id,)).fetchone()
        if r is None or (device_id is not None and r["device_id"] != device_id):
            raise LookupError("查無這次收集")
        device_id = r["device_id"]
        shown = _run_public(r)
    else:
        if device_id is None or su.get_device(conn, device_id) is None:
            raise LookupError("查無這台設備")
        r = conn.execute("SELECT * FROM storage_collect_runs WHERE device_id = ? AND item_count > 0 ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
        r = r or conn.execute("SELECT * FROM storage_collect_runs WHERE device_id = ? ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
        shown = _run_public(r) if r else None
    latest = conn.execute("SELECT * FROM storage_collect_runs WHERE device_id = ? ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
    rows: list[dict] = []
    if shown:
        for n, it in enumerate(conn.execute("SELECT * FROM storage_collect_items WHERE run_id = ? ORDER BY id", (shown["id"],)), 1):
            d = dict(it)
            try:
                ex = json.loads(d.pop("extra") or "{}")
            except ValueError:
                ex = {}
            d["extra"] = ex
            d["__no"] = n
            d["kind_label"] = KIND_LABEL.get(d["kind"], d["kind"])
            d["incomplete"] = bool(d["incomplete"])
            rows.append(d)
    return {"device_id": device_id, "run": shown, "latest": _run_public(latest) if latest else None, "rows": rows}
