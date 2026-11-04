"""共用 IP／VIP 待核清單（2026-10-31，Codex 與 CC 共同設計）。

問題：共用資料庫（RAC SCAN、DB VIP、HA 浮動 IP）、F5／負載平衡器（VIP）會讓「多個 IP／多筆資料」看起來像多台，
或「多台共用一個 IP」。原則（Codex 定）：
  * 台數**只由主機身份（machine_key）決定**；IP／VIP 關係只是「提示＋人工確認的關聯」，**不自動併機、不改任何台數**。
  * 訊號只用來**排序待核清單**，不下「這是端點」的結論；每筆列出命中規則與來源。
  * 人工可標：共用服務 IP（要選關係類型）／不是服務 IP／資料錯誤。標過就不再出現；
    之後「涉及的機器組合」變了（指紋不同）才會重新出現並註明。
  * 關係類型分開命名（SCAN、DB VIP、HA 浮動 IP、LB VIP…），不用泛稱。
本模組：讀取路徑只 SELECT（不建表、不寫庫）；寫入只有 ip_relation_review 這一張新表，不碰 hardware 與任何計數。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime

VERDICTS = {
    "shared_service": "共用服務 IP",
    "not_service": "不是服務 IP",
    "data_error": "資料錯誤（待補）",
}
RELATION_TYPES = ("RAC SCAN", "資料庫 VIP", "HA 浮動 IP", "負載平衡 VIP", "其他服務 IP")

SIGNAL_LABEL = {
    "vip_field": "「BIG IP/VIP」欄有值",
    "vip_shared": "同一個 VIP 登記在多台主機，或 VIP 與另一台的 IP 相同",
    "shared_ip": "同一個 IP 出現在多台不同主機",
    "lb_model": "型號／系統是 F5 或負載平衡器",
    "name_keyword": "主機名或用途含 vip／scan／floating／lb",
}
_NO_VALUE = {"", "無", "无", "none", "n/a", "na", "-", "--", "否", "null", "nil", "false", "0"}
_LB_RX = re.compile(r"\bf5\b|big-?ip|\bltm\b|\bgtm\b|netscaler|\ba10\b|\badc\b|load\s*balanc|負載平衡", re.I)
_NAME_RX = re.compile(r"(?<![a-z])(vip|scan|floating|lb)(?![a-z])|浮動|虛擬\s*ip", re.I)
_IP_RX = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_IGNORE_IPS = {"0.0.0.0", "127.0.0.1"}
_ALL_DIGIT_PLACEHOLDER = re.compile(r"^[\d\W_]+$")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def meaningful_vip(v) -> bool:
    return str(v or "").strip().lower() not in _NO_VALUE


def ensure_table(conn: sqlite3.Connection) -> None:
    _ddl(conn)
    conn.commit()


def _schema_ok(conn: sqlite3.Connection) -> bool:
    """唯讀檢查：表與欄位是否齊（批次套用在交易外先看，不齊才在交易內補）。"""
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "ip_relation_review" not in names or "ip_relation_batch" not in names:
        return False
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ip_relation_review)")}
    return {"batch_id", "rev"} <= cols


def _ddl(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ip_relation_review ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, item_key TEXT NOT NULL, fingerprint TEXT NOT NULL,"
        " verdict TEXT NOT NULL, relation_type TEXT, machine_keys TEXT, note TEXT,"
        " decided_by TEXT, decided_at TEXT NOT NULL, undone_at TEXT, undone_by TEXT, undo_note TEXT)")
    # 批次判定（2026-11-01）：舊庫補 batch_id 欄；批次本身一張表（稽核＋整批復原用）。只在寫入路徑呼叫，讀取不建表
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ip_relation_review)")}
    if "batch_id" not in cols:
        conn.execute("ALTER TABLE ip_relation_review ADD COLUMN batch_id TEXT")
    if "rev" not in cols:       # 每次被改動（取代／復原／還原）加 1，批次復原靠它判斷「自批次後有沒有被動過」
        conn.execute("ALTER TABLE ip_relation_review ADD COLUMN rev INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ip_relation_batch ("
        " batch_id TEXT PRIMARY KEY, verdict TEXT NOT NULL, relation_type TEXT, note TEXT, overwrite INTEGER NOT NULL DEFAULT 0,"
        " filter_text TEXT, item_count INTEGER NOT NULL, items_json TEXT NOT NULL,"
        " created_by TEXT, created_at TEXT NOT NULL, undone_at TEXT, undone_by TEXT, undo_note TEXT)")


def _has_table(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ip_relation_review'").fetchone() is not None


def _fp(keys) -> str:
    return hashlib.sha1(json.dumps(sorted(set(keys)), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def candidates(conn: sqlite3.Connection) -> list[dict]:
    """全部「值得人看一眼」的項目（只 SELECT）。item_key：ip:<IP>（共用 IP）或 mk:<machine_key>（單機訊號）。"""
    import machine_identity
    import manage_state
    import system_stats

    rows = conn.execute(
        "SELECT asset_serial, hostname, ip, big_ip_vip, device_model, os, asset_name, asset_purpose, asset_status, "
        "physical_location FROM hardware").fetchall()
    machines: dict[str, dict] = {}
    ip_owners: dict[str, dict[str, str]] = {}      # ip -> {machine_key: 顯示名}
    vip_owners: dict[str, dict[str, str]] = {}     # VIP 欄裡的 IP -> {machine_key: 顯示名}
    for r in rows:
        if (r["asset_status"] or "").strip() in manage_state.RETIRED_STATUS:
            continue                                # 退役＝備查，不跟現役混
        mk = system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        name = (r["hostname"] or "").strip() or r["asset_name"] or r["asset_serial"]
        m = machines.setdefault(mk, {"key": mk, "name": name, "serials": [], "models": set(), "ips": set(), "room": None,
                                     "vip": set(), "text": []})
        m["serials"].append(r["asset_serial"])
        if r["device_model"]:
            m["models"].add(r["device_model"])
        if not m["room"] and r["physical_location"]:
            m["room"] = r["physical_location"]
        if meaningful_vip(r["big_ip_vip"]):
            m["vip"].add(str(r["big_ip_vip"]).strip())
            for vip in _IP_RX.findall(str(r["big_ip_vip"])):
                if vip not in _IGNORE_IPS:
                    vip_owners.setdefault(vip, {})[mk] = name
        m["text"].append(" ".join(str(r[c] or "") for c in ("hostname", "asset_purpose", "device_model", "os")))
        generic = machine_identity.is_generic(machine_identity.norm_name(r["hostname"]))
        for ip in _IP_RX.findall(str(r["ip"] or "")):
            if ip in _IGNORE_IPS:
                continue
            m["ips"].add(ip)
            if not generic:                         # 沒主機名／通用名的列不是「另一台」，不拿來當共用證據
                ip_owners.setdefault(ip, {})[mk] = name
    out: dict[str, dict] = {}

    def _item(key: str, kind: str, ip: str | None, keys: list[str]) -> dict:
        it = out.get(key)
        if it is None:
            it = out[key] = {"item_key": key, "kind": kind, "ip": ip, "signals": [], "keys": sorted(set(keys))}
        return it

    for ip, owners in ip_owners.items():
        if len(owners) >= 2:
            it = _item(f"ip:{ip}", "ip", ip, list(owners))
            it["signals"].append({"code": "shared_ip", "label": SIGNAL_LABEL["shared_ip"],
                                  "detail": f"{len(owners)} 台：{'、'.join(sorted(owners.values())[:6])}"})
    # VIP 欄只是「這台登記了一個 VIP」，單獨出現不需要人看（已是登記資料）；真正有疑義的是「同一個 VIP 掛在多台」
    # 或「VIP 的 IP 同時是另一台的 IP」——這種才列入待核。
    registered_vip = 0
    for vip, owners in vip_owners.items():
        others = {k: n for k, n in ip_owners.get(vip, {}).items() if k not in owners}
        allo = {**owners, **others}
        if len(allo) >= 2:
            it = _item(f"ip:{vip}", "ip", vip, list(allo))
            it["signals"].append({"code": "vip_shared", "label": SIGNAL_LABEL["vip_shared"],
                                  "detail": f"{len(allo)} 台：{'、'.join(sorted(allo.values())[:6])}"})
    for mk, m in machines.items():
        sigs = []
        if m["vip"]:
            registered_vip += 1                     # 只計數；是否列入待核看上面的 vip_shared 與下面的 LB／關鍵字
        mtxt = " ".join(m["text"])
        lbm = _LB_RX.search(mtxt)
        if lbm:
            sigs.append({"code": "lb_model", "label": SIGNAL_LABEL["lb_model"], "detail": ", ".join(sorted(m["models"])[:2]) or lbm.group(0)})
        nk = _NAME_RX.search(str(m["name"]) + " " + mtxt)
        if nk and not lbm:
            sigs.append({"code": "name_keyword", "label": SIGNAL_LABEL["name_keyword"], "detail": nk.group(0)})
        if sigs:
            it = _item(f"mk:{mk}", "machine", None, [mk])
            it["signals"].extend(sigs)
            it["ips"] = sorted(m["ips"])
    for it in out.values():
        ks = it["keys"]
        it["machines"] = [{"key": k, "name": machines[k]["name"], "serial": machines[k]["serials"][0],
                           "model": "、".join(sorted(machines[k]["models"])[:2]) or None, "room": machines[k]["room"],
                           "ips": sorted(machines[k]["ips"])[:4]} for k in ks if k in machines]
        it.setdefault("ips", [it["ip"]] if it.get("ip") else [])
        it["score"] = len(it["signals"])
        it["fingerprint"] = _fp(ks)
    candidates.registered_vip = registered_vip      # type: ignore[attr-defined]  給 listing 顯示「已登記 VIP 的機器數」
    return sorted(out.values(), key=lambda x: (-x["score"], x["item_key"]))


def _active_reviews(conn: sqlite3.Connection) -> dict[str, dict]:
    if not _has_table(conn):
        return {}
    return {r["item_key"]: dict(r) for r in conn.execute(
        "SELECT * FROM ip_relation_review WHERE undone_at IS NULL ORDER BY id")}


def listing(conn: sqlite3.Connection) -> dict:
    """待核／已確認關係／已排除。純讀。"""
    cands = candidates(conn)
    rv = _active_reviews(conn)
    pending, confirmed, dismissed = [], [], []
    for it in cands:
        r = rv.get(it["item_key"])
        if r and r["fingerprint"] == it["fingerprint"]:
            row = {**it, "review": {k: r[k] for k in ("id", "verdict", "relation_type", "note", "decided_by", "decided_at")},
                   "verdict_label": VERDICTS.get(r["verdict"], r["verdict"])}
            (confirmed if r["verdict"] == "shared_service" else dismissed).append(row)
        else:
            row = dict(it)
            if r:                                   # 核定過，但涉及的機器變了：重新出現
                row["reopened"] = True
                row["reopened_note"] = f"曾核定為「{VERDICTS.get(r['verdict'], r['verdict'])}」，之後涉及的機器有變動，請再看一次"
            pending.append(row)
    return {"pending": pending, "confirmed": confirmed, "dismissed": dismissed,
            "counts": {"pending": len(pending), "confirmed": len(confirmed), "dismissed": len(dismissed)},
            "registered_vip": getattr(candidates, "registered_vip", 0),
            "relation_types": list(RELATION_TYPES), "verdicts": VERDICTS,
            "note": "只是提示與人工確認的關聯：不併機、不改任何台數"}


def review(conn: sqlite3.Connection, item_key: str, verdict: str, relation_type: str | None, note: str | None,
           by: str | None) -> dict:
    if verdict not in VERDICTS:
        raise ValueError("判定只能是：共用服務 IP／不是服務 IP／資料錯誤")
    if verdict == "shared_service" and relation_type not in RELATION_TYPES:
        raise ValueError(f"共用服務 IP 要選關係類型：{'／'.join(RELATION_TYPES)}")
    note = (note or "").strip()
    if verdict == "data_error" and not note:
        raise ValueError("資料錯誤要寫一句說明（錯在哪、要誰補）")
    cur = next((c for c in candidates(conn) if c["item_key"] == item_key), None)
    if cur is None:
        raise LookupError("這一項已不在待核範圍（資料可能剛變動），請重新整理")
    ensure_table(conn)
    now = _now()
    conn.execute("UPDATE ip_relation_review SET undone_at = ?, undone_by = ?, undo_note = '被新的判定取代', rev = rev + 1 "
                 "WHERE item_key = ? AND undone_at IS NULL", (now, by, item_key))
    c = conn.execute(
        "INSERT INTO ip_relation_review (item_key, fingerprint, verdict, relation_type, machine_keys, note, decided_by, decided_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (item_key, cur["fingerprint"], verdict, relation_type if verdict == "shared_service" else None,
         json.dumps(cur["keys"], ensure_ascii=False), note or None, by, now))
    conn.commit()
    return {"id": c.lastrowid, "item_key": item_key, "verdict": verdict}


def undo(conn: sqlite3.Connection, review_id: int, by: str | None, note: str | None) -> dict:
    if not _has_table(conn):
        raise LookupError("沒有這筆判定")
    ensure_table(conn)              # 舊庫補 rev 欄（寫入路徑）
    r = conn.execute("SELECT id, undone_at FROM ip_relation_review WHERE id = ?", (review_id,)).fetchone()
    if r is None:
        raise LookupError("沒有這筆判定")
    if r["undone_at"]:
        raise ValueError("這筆判定已經復原過")
    conn.execute("UPDATE ip_relation_review SET undone_at = ?, undone_by = ?, undo_note = ?, rev = rev + 1 WHERE id = ?",
                 (_now(), by, (note or "").strip() or None, review_id))
    conn.commit()
    return {"id": review_id, "undone": True}


# ===================== 批次判定（2026-11-01）=====================
# 判定只是這份清單的標記：只寫 ip_relation_review／ip_relation_batch，不碰 hardware 與任何台數。
# 兩段式：preview（純讀，回 digest）→ apply（重算並比對 digest，單一交易）。可整批復原。
BATCH_MAX = 500


class StalePlan(Exception):
    """預覽之後清單有變動（或判定被改過）→ 要重新預覽。"""


class BatchConflict(Exception):
    def __init__(self, conflicts: list[dict]):
        super().__init__("有項目在批次之後被改過，未復原")
        self.conflicts = conflicts


def _validate_batch(item_keys, verdict, relation_type, note) -> tuple[list[str], str | None, str | None]:
    if verdict not in VERDICTS:
        raise ValueError("判定只能是：共用服務 IP／不是服務 IP／資料錯誤")
    if verdict == "shared_service" and relation_type not in RELATION_TYPES:
        raise ValueError(f"共用服務 IP 要選關係類型：{'／'.join(RELATION_TYPES)}")
    note = (note or "").strip() or None
    if verdict == "data_error" and not note:
        raise ValueError("資料錯誤要寫一句說明（錯在哪、要誰補）")
    keys = list(dict.fromkeys(str(k) for k in (item_keys or [])))
    if not keys:
        raise ValueError("沒有勾選任何項目")
    if len(keys) > BATCH_MAX:
        raise ValueError(f"單次批次最多 {BATCH_MAX} 筆（這次 {len(keys)} 筆），請縮小篩選再試")
    return keys, (relation_type if verdict == "shared_service" else None), note


def _batch_plan(conn: sqlite3.Connection, keys: list[str], verdict: str, relation_type: str | None, note: str | None,
                overwrite: bool) -> dict:
    cands = {c["item_key"]: c for c in candidates(conn)}
    rv = _active_reviews(conn)
    items, skipped = [], []
    for k in keys:
        cur = cands.get(k)
        if cur is None:
            skipped.append({"item_key": k, "reason": "not_in_scope"})
            continue
        prev = rv.get(k)
        judged = bool(prev and prev["fingerprint"] == cur["fingerprint"])
        if judged and not overwrite:
            skipped.append({"item_key": k, "reason": "already_judged", "verdict": prev["verdict"]})
            continue
        content = {"kind": cur["kind"], "ip": cur.get("ip"), "ips": cur.get("ips"), "signals": cur["signals"],
                   "machines": cur.get("machines")}          # 畫面上看到的內容；預覽後這些變了就要重新預覽
        items.append({"item_key": k, "fingerprint": cur["fingerprint"], "keys": cur["keys"], "content": content,
                      "prev_id": prev["id"] if prev else None,
                      "prev_verdict": prev["verdict"] if judged else None,
                      "reopened": bool(prev and not judged)})
    body = {"verdict": verdict, "relation_type": relation_type, "note": note, "overwrite": bool(overwrite),
            "items": [[i["item_key"], i["fingerprint"], i["prev_id"], i["content"]] for i in items],
            "skipped": [[s["item_key"], s["reason"]] for s in skipped]}
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {"items": items, "skipped": skipped, "digest": digest, "cands": cands}


def batch_preview(conn: sqlite3.Connection, item_keys, verdict: str, relation_type: str | None = None,
                  note: str | None = None, overwrite: bool = False) -> dict:
    """純讀：不建表、不寫庫。"""
    keys, relation_type, note = _validate_batch(item_keys, verdict, relation_type, note)
    plan = _batch_plan(conn, keys, verdict, relation_type, note, bool(overwrite))
    cands = plan["cands"]
    by_prev: dict[str, int] = {}
    for i in plan["items"]:
        if i["prev_verdict"]:
            lab = VERDICTS.get(i["prev_verdict"], i["prev_verdict"])
            by_prev[lab] = by_prev.get(lab, 0) + 1
    skip_by: dict[str, int] = {}
    for s in plan["skipped"]:
        skip_by[s["reason"]] = skip_by.get(s["reason"], 0) + 1

    def _sample(key: str) -> dict:
        c = cands.get(key) or {}
        return {"item_key": key, "who": c.get("ip") or "、".join(c.get("ips") or []),
                "machines": [m["name"] for m in (c.get("machines") or [])][:4]}

    return {"will_apply": len(plan["items"]), "overwrite": bool(overwrite),
            "will_overwrite": sum(1 for i in plan["items"] if i["prev_verdict"]),
            "will_overwrite_by_verdict": by_prev,
            "reopened": sum(1 for i in plan["items"] if i["reopened"]),
            "skipped": len(plan["skipped"]), "skipped_by_reason": skip_by,
            "skipped_reason_label": {"not_in_scope": "已不在待核範圍", "already_judged": "已判定過（未勾覆蓋）"},
            "verdict": verdict, "verdict_label": VERDICTS[verdict], "relation_type": relation_type,
            "sample": [_sample(i["item_key"]) for i in plan["items"][:20]],
            "digest": plan["digest"], "limit": BATCH_MAX}


def _insert_review(conn, item: dict, verdict, relation_type, note, by, now, batch_id) -> int:
    if item["prev_id"] is not None:
        conn.execute("UPDATE ip_relation_review SET undone_at = ?, undone_by = ?, undo_note = ?, rev = rev + 1 WHERE id = ?",
                     (now, by, f"被批次 {batch_id} 取代", item["prev_id"]))
    c = conn.execute(
        "INSERT INTO ip_relation_review (item_key, fingerprint, verdict, relation_type, machine_keys, note, decided_by,"
        " decided_at, batch_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (item["item_key"], item["fingerprint"], verdict, relation_type, json.dumps(item["keys"], ensure_ascii=False),
         note, by, now, batch_id))
    return c.lastrowid


def batch_apply(conn: sqlite3.Connection, item_keys, verdict: str, relation_type: str | None, note: str | None,
                overwrite: bool, digest: str, filter_text: str | None, by: str | None) -> dict:
    keys, relation_type, note = _validate_batch(item_keys, verdict, relation_type, note)
    if not digest:
        raise ValueError("缺少預覽的 digest，請先預覽")
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not _schema_ok(conn):
            _ddl(conn)              # 建表／補欄位在同一個交易內；digest 不符會連同 rollback，不留 schema 變動
        plan = _batch_plan(conn, keys, verdict, relation_type, note, bool(overwrite))
        if plan["digest"] != digest:
            raise StalePlan("預覽之後清單有變動，請重新預覽")
        if not plan["items"]:
            raise ValueError("沒有可套用的項目")
        now = _now()
        batch_id = "IPB-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha1(
            (digest + now).encode("utf-8")).hexdigest()[:6]
        done = []
        for i in plan["items"]:
            new_id = _insert_review(conn, i, verdict, relation_type, note, by, now, batch_id)
            done.append({"item_key": i["item_key"], "new_id": new_id, "prev_id": i["prev_id"]})
        conn.execute(
            "INSERT INTO ip_relation_batch (batch_id, verdict, relation_type, note, overwrite, filter_text, item_count,"
            " items_json, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (batch_id, verdict, relation_type, note, 1 if overwrite else 0, (filter_text or "")[:200] or None,
             len(done), json.dumps(done, ensure_ascii=False), by, now))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"batch_id": batch_id, "applied": len(done), "skipped": len(plan["skipped"]), "verdict": verdict}


def batch_undo(conn: sqlite3.Connection, batch_id: str, by: str | None, note: str | None,
               skip_conflicts: bool = False) -> dict:
    """整批復原：只有「批次寫的那筆判定仍是現行、沒被改過」才復原；有衝突預設整批不動、列出衝突。"""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ip_relation_batch'").fetchone():
        raise LookupError("沒有這個批次")
    b = conn.execute("SELECT * FROM ip_relation_batch WHERE batch_id = ?", (batch_id,)).fetchone()
    if b is None:
        raise LookupError("沒有這個批次")
    if b["undone_at"]:
        raise ValueError("這個批次已經整批復原過")
    done = json.loads(b["items_json"])
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not _schema_ok(conn):
            _ddl(conn)
        ok, conflicts = [], []
        for d in done:
            r = conn.execute("SELECT undone_at, rev FROM ip_relation_review WHERE id = ?", (d["new_id"],)).fetchone()
            if r is None or r["undone_at"] or (r["rev"] or 0) != 0:     # rev≠0＝批次之後被取代／復原／還原過（含 A→B→復原 B 的情形）
                conflicts.append({"item_key": d["item_key"], "reason": "批次之後這筆判定已被單筆改過或復原"})
            else:
                ok.append(d)
        if conflicts and not skip_conflicts:
            raise BatchConflict(conflicts)
        now = _now()
        why = (note or "").strip() or f"整批復原 {batch_id}"
        for d in ok:
            conn.execute("UPDATE ip_relation_review SET undone_at = ?, undone_by = ?, undo_note = ?, rev = rev + 1 WHERE id = ?",
                         (now, by, why, d["new_id"]))
            if d["prev_id"] is not None:
                conn.execute("UPDATE ip_relation_review SET undone_at = NULL, undone_by = NULL, undo_note = NULL, rev = rev + 1 "
                             "WHERE id = ? AND undo_note = ?", (d["prev_id"], f"被批次 {batch_id} 取代"))
        conn.execute("UPDATE ip_relation_batch SET undone_at = ?, undone_by = ?, undo_note = ? WHERE batch_id = ?",
                     (now, by, why if not conflicts else f"{why}（略過 {len(conflicts)} 筆衝突）", batch_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"batch_id": batch_id, "restored": len(ok), "conflicts": conflicts}


def list_batches(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """純讀；表不存在回空清單。"""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ip_relation_batch'").fetchone():
        return []
    out = []
    for r in conn.execute(
            "SELECT batch_id, verdict, relation_type, note, overwrite, filter_text, item_count, created_by, created_at,"
            " undone_at, undone_by FROM ip_relation_batch ORDER BY created_at DESC, batch_id DESC LIMIT ?", (limit,)):
        d = dict(r)
        d["verdict_label"] = VERDICTS.get(d["verdict"], d["verdict"])
        out.append(d)
    return out
