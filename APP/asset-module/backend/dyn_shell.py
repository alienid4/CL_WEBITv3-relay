"""掃描空殼（DYN-）與 CIA 正本：比對、併回預覽、併回執行、還原（2026-10-16）。

## 為什麼要有

存活掃描（dynassets）的角色是替既有的 CIA 正本**加分**——證明這台還活著、
提高可信度（trust_score 的 dynassets 25 分）。它不應該另外生一筆資產；
`DYN-` 只該用在「掃到了、但 CIA 完全沒登記」的機器。

2026-10-16 公司機實例：同一台 VM 有 `DYN-<MAC>` 與 CIA 的 `HW-…` 兩筆，
主機名＋IP 完全相同（全站正典 system_stats.machine_key 認得是同一台）。
掃描證據全掛在 DYN- 那筆，正本沒拿到「存活」的分數，收集資料也散在兩筆。

## 這個空殼怎麼來的（根因）

1. dynassets 匯入（dynassets_import）走 identity.resolve：強識別碼 → MAC → 主機名＋IP。
   匯入當下 CIA 還沒有這台（或主機名／IP 對不上）→ 判成 NEW → 建 `DYN-<MAC>`。
2. CIA 清冊匯入（excel_import._upsert）只用 asset_serial 當鍵，**不跟既有的 DYN- 比對**，
   於是正本 HW- 進來時跟 DYN- 並存。
3. 之後每次重匯 dynassets，identity 的 MAC 那一關（排在主機名＋IP 之前）只對得到
   帶 MAC 的 DYN-（CIA 沒有 MAC 欄）→ 永遠判給空殼，空殼自己延續下去。

## 這裡做什麼

- `cia_twins`：用 machine_key（主機名＋IP 都相同、同為非退役）找同一台的 CIA 正本。
  dynassets 匯入用它把證據改掛到正本（只有恰好一筆、MAC 不矛盾時）。
- `preview`：把現有的 DYN- 分成三類——可併回／需人工／真的未登記。
- `apply`：使用者在畫面上看過預覽、按確認才跑。先留快照、只做軟退役、可還原。
- `restore`：照快照還原。

## 欄位優先序（使用者 2026-10-16：「DYN 比較準，要掃過；CIA 比較不準，但資訊比較多」）

- 實測類（IP、MAC、主機名、偵測到的 OS、最後看到時間）→ 以掃描為準；
- 業務類（名稱、用途、AP ID、負責人、環境、狀態…）→ 以 CIA 為準。
**掃描值不寫進 CIA 欄位**（excel_import 重匯會把非鍵欄位整個蓋回去，寫了也會來回跳）。
掃描證據本來就存在 source_record（source='dynassets'，payload 原樣），併回只是把
它改指向正本；畫面用 `observed_facts` 並列「實測值／CIA 登記值」，對不上就標出來。
唯一的例外是 MAC：CIA 清冊沒有這一欄（不會被重匯蓋掉），而且 identity 靠它比對，
正本缺 MAC 時補上，下一次掃描才對得到正本。

⚠️ 只處理「DYN- 空殼 vs CIA 正本」。CIA 自己同一台多筆（一台 VM 多個業務系統）不碰。
"""
from __future__ import annotations

import json

from db import _now_local

SHELL_PREFIX = "DYN-"
RETIRE_STATUS = "停用"           # 軟退役：manage_state.RETIRED_STATUS 之一，全站自動排除
#: 收集資料表：跟著併回改指向正本（快照記下每一列的 id，還原時照改回）
INVENTORY_TABLES = ("host_package", "host_service", "host_account")
#: 併回時正本空白才補的機器欄位（CIA 清冊不管這幾欄，重匯不會蓋掉）。
#: is_vm 預設 0（0＝實體或沒填），所以也用「空白／0 才補」。
FILL_IF_EMPTY = ("mac", "vm_uuid", "vi_sdk_server", "is_vm")

AUDIT_SQL = """
CREATE TABLE IF NOT EXISTS shell_merge_audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    merged_at     TEXT NOT NULL,
    actor         TEXT,
    shell_serial  TEXT NOT NULL,
    target_serial TEXT NOT NULL,
    snapshot      TEXT NOT NULL,     -- 併回前：空殼整列、正本整列、改指向的每一列 id（JSON）
    restored_at   TEXT,
    restored_by   TEXT
)
"""


def _blank(v) -> bool:
    return str(v or "").strip() in ("", "0")


def _ensure(conn) -> None:
    conn.execute(AUDIT_SQL)


def _norm_mac(v) -> str:
    return str(v or "").strip().lower().replace("-", ":").replace(".", ":")


def _retired():
    import manage_state

    return manage_state.RETIRED_STATUS


def _is_off_book(serial) -> bool:
    import system_report

    return system_report.is_off_book(serial)


def _key(hostname, ip, serial) -> str:
    import system_stats

    return system_stats.machine_key(hostname, ip, serial)


def cia_twins(conn, hostname, ip, mac=None) -> dict:
    """同一台（machine_key）的 CIA 正本。回 {twins, mac_conflict, near}。

    twins＝machine_key 相同「而且 IP 一樣」（主機名＋IP 都相同）、非退役、非帳外的登記；
    mac_conflict＝machine_key 相同但 MAC 跟這筆不同的（明確是兩台機器共用名稱／IP，不併）；
    near＝只有 IP 或只有主機名相同的 CIA 登記（不夠斷定，留給人判斷）。
    """
    import system_stats

    ip = (ip or "").strip()
    key = _key(hostname, ip, None)
    out: dict = {"twins": [], "mac_conflict": [], "near": []}
    if key.startswith("sn:"):
        return out
    short = system_stats.strip_domain(hostname).lower()
    ret = _retired()
    m = _norm_mac(mac)
    for r in conn.execute(
            "SELECT id, asset_serial, hostname, ip, mac, asset_status FROM hardware "
            "WHERE trim(ip) = ? OR lower(trim(hostname)) LIKE ?", (ip, short + "%")):
        if _is_off_book(r["asset_serial"]) or (r["asset_status"] or "").strip() in ret:
            continue
        # 審查（2026-10-17）：自動掛正本要「主機名＋IP 都相同」。主機名口徑下 machine_key 同名就相同，
        # 但 IP 不同時（另一張網卡？名稱重用？）不自動掛，送需人工。0.0.0.0 佔位不算 IP 相符。
        # 兩邊都空白或都 0.0.0.0 不算 IP 相同（審查 L6）
        same_ip = bool(ip) and ip != "0.0.0.0" and (r["ip"] or "").strip() == ip
        if _key(r["hostname"], r["ip"], r["asset_serial"]) == key and same_ip:
            other = _norm_mac(r["mac"])
            (out["mac_conflict"] if (m and other and other != m) else out["twins"]).append(dict(r))
        elif same_ip or \
                system_stats.strip_domain(r["hostname"]).lower() == short:
            out["near"].append(dict(r))
    return out


CIA_RULE = "machine_key:cia"
#: CIA 清冊管的身分欄位：掃描／vCenter 寫到 CIA 正本時一律不寫——實測值留在 source_record，
#: 畫面並列（observed_facts）。os 不在這裡：hardware.os 是「實測優先」的顯示值，CIA 原值另存
#: os_cia，CIA 匯入有實測證據時不蓋 os（見 cia_measured / excel_import，決策 Q80）。
CIA_OWNED_FACTS = ("hostname", "ip")


def prefer_cia(conn, rec: dict, match):
    """掃描／vCenter 證據要替 CIA 正本加分，不是另開一筆（2026-10-16 使用者：
    「這是讓你判斷加分的，不是這樣是兩筆」）。dynassets 與 RVTools 匯入共用。

    identity.resolve 的 MAC／vm_uuid 那幾關排在主機名＋IP 前面，而 CIA 清冊沒有這些欄——
    一旦曾經建過帳外那筆，之後每次重匯都只對得到它，正本永遠拿不到證據。
    所以 identity 判成「對到帳外那筆」「判不準」或「新的」時，再用全站正典
    machine_key（主機名＋IP 都相同、非退役）找 CIA 正本；**恰好一筆、MAC 不矛盾**才改掛正本。
    多筆（CIA 一台多筆登記）、MAC 不同（IP 被回收）、只對到 IP 或主機名其中一個——
    一律維持原判（寧可多一筆給人判斷，也不要把兩台誤併成一台）。
    """
    import identity

    if match.status == identity.MATCHED:
        row = conn.execute("SELECT asset_serial FROM hardware WHERE id = ?",
                           (match.hardware_id,)).fetchone()
        if row is None or not _is_off_book(row["asset_serial"]):
            return match
    t = cia_twins(conn, rec.get("hostname"), rec.get("ip"), rec.get("mac"))
    if len(t["twins"]) == 1 and not t["mac_conflict"]:
        return identity.MatchResult(
            identity.MATCHED, t["twins"][0]["id"], CIA_RULE, 0.8,
            reason="主機名＋IP 與 CIA 正本相同（machine_key），證據掛到正本")
    return match


def is_cia_target(conn, hardware_id) -> bool:
    """對到的這筆是 CIA 正本（不是 DYN-/VC-/AUTO- 帳外那筆）。決策 Q80：寫到 CIA 正本一律過 cia_safe_setters。"""
    row = conn.execute("SELECT asset_serial FROM hardware WHERE id = ?", (hardware_id,)).fetchone()
    return row is not None and not _is_off_book(row["asset_serial"])


#: 「有實測證據」的來源（source_record.source）。有證據的列，CIA 重匯不蓋 hardware.os。
MEASURED_SOURCES = ("dynassets", "vcenter", "rvtools")


def cia_measured(conn, hardware_id) -> bool:
    """這筆有沒有實測值（掃描／vCenter 來源紀錄，或 SSH／WinRM 收集成功過）。決策 Q80。"""
    ph = ",".join("?" for _ in MEASURED_SOURCES)
    try:
        if conn.execute(f"SELECT 1 FROM source_record WHERE resolved_hardware_id = ? AND source IN ({ph}) "
                        "LIMIT 1", (hardware_id, *MEASURED_SOURCES)).fetchone():
            return True
    except Exception:  # noqa: BLE001 - 舊庫沒有 source_record：當沒有實測證據（照舊讓 CIA 寫 os）
        pass
    row = conn.execute("SELECT collect_ok FROM hardware WHERE id = ?", (hardware_id,)).fetchone()
    return row is not None and row["collect_ok"] == 1


def cia_safe_setters(conn, hardware_id: int, setters: dict) -> dict:
    """寫到 CIA 正本時能寫哪些機器欄位（決策 Q80）：身分欄（主機名／IP）不寫；MAC 只在正本空白時補；
    os 寫進顯示值，但 CIA 原值還沒搬到 os_cia 的，先把目前值存進 os_cia 再蓋（不讓 CIA 值消失）。"""
    out = {k: v for k, v in setters.items() if k not in CIA_OWNED_FACTS}
    cur = conn.execute("SELECT * FROM hardware WHERE id = ?", (hardware_id,)).fetchone()
    if cur is None:
        return out
    if "mac" in out and (cur["mac"] or "").strip():
        out.pop("mac")
    # 設備型號是 CIA 清冊管的欄（field_mapping.json）：CIA 有值就不蓋，空白才補（審查 #7）
    if "device_model" in out and (cur["device_model"] or "").strip():
        out.pop("device_model")
    if "os" in out and "os_cia" in cur.keys():
        old = (cur["os"] or "").strip()
        if old and not (cur["os_cia"] or "").strip() and old != str(out["os"]).strip():
            out["os_cia"] = old
    return out


def cia_guard_collected(conn, asset_serial: str, setters: dict) -> dict:
    """SSH／WinRM 收集寫到 CIA 正本前過這一關（決策 Q80 補洞，2026-10-16）。

    主機名是 CIA 管的身分欄：收集到的主機名另存 hostname_observed，不蓋 hostname；
    os 照寫（顯示值實測優先），但 os_cia 還空著就先把目前值存進去，CIA 原值不消失。
    帳外那筆（DYN-/VC-/AUTO-）沒有 CIA 值要保護，原樣寫。
    """
    if _is_off_book(asset_serial):
        return setters
    out = dict(setters)
    cur = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
    if cur is None:
        return out
    keys = cur.keys()
    if "hostname" in out:
        h = out.pop("hostname")
        if "hostname_observed" in keys:
            out["hostname_observed"] = h
    # 設備型號是 CIA 清冊管的欄：CIA 有值就不蓋，空白才補（審查 #7；CIA 匯入只寫有值的格子，補了不會被洗回空白）
    if "device_model" in out and (cur["device_model"] or "").strip():
        out.pop("device_model")
    if "os" in out and "os_cia" in keys:
        old = (cur["os"] or "").strip()
        if old and not (cur["os_cia"] or "").strip() and old != str(out["os"]).strip():
            out["os_cia"] = old
    return out


#: CIA 業務欄位：全空＝這筆沒有業務資料（掃描自動建的空殼）
BUSINESS = ("asset_purpose", "api_id", "environment", "asset_status", "user_name",
            "custodian", "usage_unit", "big_ip_vip", "request_no")


def no_business(row: dict) -> bool:
    return not any(str(row.get(c) or "").strip() for c in BUSINESS)


def classify(conn, shell: dict) -> dict:
    """單一筆 DYN- 的歸類：mergeable／review／unregistered，附理由與對象。"""
    t = cia_twins(conn, shell.get("hostname"), shell.get("ip"), shell.get("mac"))
    base = {"shell_serial": shell["asset_serial"], "hostname": shell.get("hostname"),
            "ip": shell.get("ip"), "mac": shell.get("mac"), "asset_name": shell.get("asset_name")}
    if t["mac_conflict"]:
        return {**base, "kind": "review", "target_serial": None,
                "candidates": [r["asset_serial"] for r in t["mac_conflict"] + t["twins"]],
                "reason": "主機名＋IP 相同，但 CIA 那筆的 MAC 不一樣——可能是 IP 被回收給別台"}
    if len(t["twins"]) == 1:
        if not no_business(shell):
            return {**base, "kind": "review", "target_serial": None,
                    "candidates": [t["twins"][0]["asset_serial"]],
                    "reason": "這筆 DYN- 有人填過業務欄位，不是空殼，併之前要人看"}
        return {**base, "kind": "mergeable", "target_serial": t["twins"][0]["asset_serial"],
                "candidates": [t["twins"][0]["asset_serial"]],
                "reason": "主機名＋IP 與 CIA 正本完全相同（同一台），這筆沒有業務資料"}
    if len(t["twins"]) > 1:
        return {**base, "kind": "review", "target_serial": None,
                "candidates": [r["asset_serial"] for r in t["twins"]],
                "reason": f"同一台在 CIA 有 {len(t['twins'])} 筆登記，掃描證據要掛哪一筆待人工決定"}
    if t["near"]:
        return {**base, "kind": "review", "target_serial": None,
                "candidates": [r["asset_serial"] for r in t["near"]],
                "reason": "只有 IP 或只有主機名跟 CIA 相符（或同名但 IP 不同），不足以自動斷定，要人看"}
    return {**base, "kind": "unregistered", "target_serial": None, "candidates": [],
            "reason": "CIA 沒有任何主機名或 IP 相符的登記——真的未登記（帳外資產）"}


def preview(conn) -> dict:
    """全部非退役 DYN- 的歸類。不寫任何東西。"""
    ret = _retired()
    items = []
    for r in conn.execute(
            "SELECT * FROM hardware WHERE asset_serial GLOB 'DYN-*' ORDER BY asset_serial"):
        d = dict(r)
        if (d.get("asset_status") or "").strip() in ret:
            continue
        items.append(classify(conn, d))
    counts = {k: sum(1 for i in items if i["kind"] == k)
              for k in ("mergeable", "review", "unregistered")}
    return {"items": items, "counts": counts}


def _ids_by_serial(conn, table: str, serial: str) -> list[int]:
    try:
        return [r[0] for r in conn.execute(
            f"SELECT id FROM {table} WHERE asset_serial = ?", (serial,))]
    except Exception:  # noqa: BLE001 - 舊 DB 沒這張表
        return []


def _set_in(conn, table: str, col: str, value, ids: list[int]) -> None:
    if ids:
        ph = ",".join("?" for _ in ids)
        conn.execute(f"UPDATE {table} SET {col} = ? WHERE id IN ({ph})", (value, *ids))


def apply(conn, pairs: list[dict], actor: str | None) -> dict:
    """把使用者確認過的 {shell_serial, target_serial} 併回。

    **不信任畫面傳來的配對**：每一組都重算一次，仍是 mergeable 而且對象一樣才做；
    否則略過並講理由（預覽到確認之間資料可能變了）。
    """
    _ensure(conn)
    done, skipped = [], []
    now = _now_local()
    for p in pairs or []:
        sserial = str(p.get("shell_serial") or "").strip()
        tserial = str(p.get("target_serial") or "").strip()
        row = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (sserial,)).fetchone()
        if row is None or not sserial.startswith(SHELL_PREFIX):
            skipped.append({"shell_serial": sserial, "reason": "找不到這筆 DYN-"})
            continue
        shell = dict(row)
        if (shell.get("asset_status") or "").strip() in _retired():
            skipped.append({"shell_serial": sserial, "reason": "這筆已經退役（可能已併過）"})
            continue
        c = classify(conn, shell)
        if c["kind"] != "mergeable" or c["target_serial"] != tserial:
            skipped.append({"shell_serial": sserial,
                            "reason": f"重新比對後不能併到 {tserial}：{c['reason']}"})
            continue
        target = dict(conn.execute("SELECT * FROM hardware WHERE asset_serial = ?",
                                   (tserial,)).fetchone())
        snap = {"shell": shell, "target": target,
                "source_record": [r[0] for r in conn.execute(
                    "SELECT id FROM source_record WHERE resolved_hardware_id = ?", (shell["id"],))],
                "tables": {t: _ids_by_serial(conn, t, sserial) for t in INVENTORY_TABLES}}
        # 快照先寫，後面任何一步出錯都還有底可以還原
        conn.execute(
            "INSERT INTO shell_merge_audit (merged_at, actor, shell_serial, target_serial, snapshot) "
            "VALUES (?,?,?,?,?)", (now, actor, sserial, tserial,
                                   json.dumps(snap, ensure_ascii=False, default=str)))
        # 1) 掃描證據改指向正本（trust_score 的 dynassets／RVTools 分數跟著過去）
        _set_in(conn, "source_record", "resolved_hardware_id", target["id"], snap["source_record"])
        # 2) 收集資料改掛正本
        for t, ids in snap["tables"].items():
            _set_in(conn, t, "asset_serial", tserial, ids)
        # 3) 正本缺的「CIA 不管的機器欄位」才補（MAC、納管狀態）；業務欄位一律不動
        sets: dict = {}
        for k in FILL_IF_EMPTY:
            if k in shell and _blank(target.get(k)) and not _blank(shell.get(k)):
                sets[k] = shell[k]
        if target.get("collect_ok") != 1 and shell.get("collect_ok") == 1:
            sets["collect_ok"] = 1
            sets["collect_checked_at"] = shell.get("collect_checked_at")
        if sets:
            assigns = ", ".join(f"{k} = ?" for k in sets)
            conn.execute(f"UPDATE hardware SET {assigns} WHERE asset_serial = ?",
                         (*sets.values(), tserial))
        # 4) 空殼軟退役（不 DELETE），備註寫明併到哪裡
        note = f"已併入 {tserial}（掃描自動建立的空殼，{now[:10]}）"
        remark = (shell.get("remark") or "").strip()
        conn.execute("UPDATE hardware SET asset_status = ?, remark = ?, updated_at = ? "
                     "WHERE asset_serial = ?",
                     (RETIRE_STATUS, f"{remark}；{note}" if remark else note, now, sserial))
        done.append({"shell_serial": sserial, "target_serial": tserial,
                     "moved": {"source_record": len(snap["source_record"]),
                               **{t: len(v) for t, v in snap["tables"].items()}},
                     "filled": sorted(sets)})
    conn.commit()
    if done:
        _rebuild_ci(conn)
    return {"merged": done, "skipped": skipped}


def _rebuild_ci(conn) -> None:
    """vCenter 的「跑在哪台 ESXi／哪個叢集」是 CI 圖依 source_record 算出來的；
    證據改指向後重建一次，正本那頁才看得到虛擬化資訊。失敗不影響併回本身。"""
    try:
        import ci_graph

        ci_graph.rebuild(conn)
        conn.commit()
    except Exception:  # noqa: BLE001
        pass


def history(conn, limit: int = 200) -> list[dict]:
    _ensure(conn)
    return [dict(r) for r in conn.execute(
        "SELECT id, merged_at, actor, shell_serial, target_serial, restored_at, restored_by "
        "FROM shell_merge_audit ORDER BY id DESC LIMIT ?", (min(max(int(limit), 1), 1000),))]


def restore(conn, audit_id: int, actor: str | None) -> dict:
    """照快照把一次併回整個退回去：空殼回原狀、正本補過的欄位回原值、改指向的列改回。"""
    _ensure(conn)
    a = conn.execute("SELECT * FROM shell_merge_audit WHERE id = ?", (audit_id,)).fetchone()
    if a is None:
        raise ValueError("查無這筆併回紀錄")
    if a["restored_at"]:
        raise ValueError(f"這筆已在 {a['restored_at']} 還原過")
    snap = json.loads(a["snapshot"])
    shell, target = snap["shell"], snap["target"]
    conn.execute("UPDATE hardware SET asset_status = ?, remark = ?, updated_at = ? "
                 "WHERE asset_serial = ?",
                 (shell.get("asset_status"), shell.get("remark"), shell.get("updated_at"),
                  shell["asset_serial"]))
    cols = [k for k in (*FILL_IF_EMPTY, "collect_ok", "collect_checked_at") if k in target]
    conn.execute(f"UPDATE hardware SET {', '.join(f'{k} = ?' for k in cols)} WHERE asset_serial = ?",
                 (*[target.get(k) for k in cols], target["asset_serial"]))
    _set_in(conn, "source_record", "resolved_hardware_id", shell["id"], snap["source_record"])
    for t, ids in snap["tables"].items():
        _set_in(conn, t, "asset_serial", shell["asset_serial"], ids)
    conn.execute("UPDATE shell_merge_audit SET restored_at = ?, restored_by = ? WHERE id = ?",
                 (_now_local(), actor, audit_id))
    conn.commit()
    _rebuild_ci(conn)
    return {"restored": audit_id, "shell_serial": shell["asset_serial"],
            "target_serial": target["asset_serial"]}


# ---- 實測 vs 登記（明細頁並列顯示，對不上要標出來）----

def _os_mismatch(o, g) -> bool:
    """兩邊都認得出來才比產品與主版本；認不出來的不下「不符」的結論（不知道就是不知道）。"""
    import normalize

    no, ng = normalize.normalize_os(o), normalize.normalize_os(g)
    if not (no["matched"] and ng["matched"]):
        return False
    if no["product"] != ng["product"]:
        return True
    if no["version"] and ng["version"]:
        return no["version"].split(".")[0] != ng["version"].split(".")[0]
    return False


def observed_facts(conn, serials: list[str], hardware) -> dict | None:
    """這台（serials＝同一台的全部登記）最近一份 dynassets 掃描證據，跟 CIA 登記並列。

    回 {seen_at, via_serial, fields: [{key, label, observed, registered, mismatch}]}；
    沒有掃描證據回 None。實測欄位畫面以 observed 為準，registered 只在不符時提示。
    """
    import system_stats

    if not serials:
        return None
    ph = ",".join("?" for _ in serials)
    try:
        r = conn.execute(
            "SELECT sr.payload, sr.collected_at, h.asset_serial FROM source_record sr "
            "JOIN hardware h ON h.id = sr.resolved_hardware_id "
            f"WHERE sr.source = 'dynassets' AND h.asset_serial IN ({ph}) "
            "ORDER BY sr.collected_at DESC LIMIT 1", list(serials)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    hw = dict(hardware)
    collected_host = str(hw.get("hostname_observed") or "").strip()
    if r is None and not collected_host:
        return None
    try:
        obs = json.loads(r["payload"] or "{}") if r is not None else {}
    except (TypeError, ValueError):
        obs = {}
    if collected_host:
        obs["hostname"] = collected_host   # SSH／WinRM 從機器本身收到的主機名最可信（Q80）
    out = []
    for key, label in (("hostname", "主機名"), ("ip", "IP"), ("mac", "MAC"), ("os", "作業系統")):
        o, g = obs.get(key), hw.get(key)
        if key == "os" and str(hw.get("os_cia") or "").strip():
            g = hw.get("os_cia")   # 決策 Q80：os 是實測優先的顯示值，CIA 登記原值在 os_cia
        if o in (None, "") and g in (None, ""):
            continue
        mismatch = False
        if o not in (None, "") and g not in (None, ""):
            if key == "hostname":
                mismatch = (system_stats.strip_domain(o).lower()
                            != system_stats.strip_domain(g).lower())
            elif key == "mac":
                mismatch = _norm_mac(o) != _norm_mac(g)
            elif key == "os":
                mismatch = _os_mismatch(o, g)
            else:
                mismatch = str(o).strip() != str(g).strip()
        out.append({"key": key, "label": label, "observed": o, "registered": g,
                    "mismatch": mismatch})
    return {"seen_at": r["collected_at"] if r is not None else hw.get("collect_checked_at"),
            "via_serial": r["asset_serial"] if r is not None else hw.get("asset_serial"), "fields": out}
