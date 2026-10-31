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
- `apply`：使用者在畫面上看過預覽、按確認才跑。先留快照、空殼搬進合併紀錄（hardware_merged）、可還原。
- `auto_merge`：證據確定是同一台的，部署／patch 時自動併（決策 DYN2，2026-10-18，見下）。
- `restore`：照快照還原。

## 自動併回（決策 DYN2：使用者方向＋小總管定條件）

使用者 2026-10-17：「確定是同一台就直接合併，畫面只留一台，不要那段黃色說明。」
取代「清理空殼要使用者點頭才執行」，**只限證據確定者**（`auto_check` 全部成立）：
1. 可併回（classify＝mergeable：恰好一筆 CIA 正本、主機名＋IP 都相同、MAC 不矛盾、空殼沒業務資料）；
2. 正規化主機名（同 machine_key 的 norm_name）相同、而且不是通用名（localhost、template…）；
3. IP 相同、非空白、非 0.0.0.0；
4. CIA 那筆非退役、不是帳外；
5. 兩邊都有填的 MAC／硬體序號／vm_uuid 不衝突（MAC 不是必要條件：CIA 常沒填）；
6. 這個主機名或 IP 在 CIA 的候選登記全部屬同一台（machine_key 相同）；
7. 這筆沒有被人「還原」過（還原＝有人判斷不該併，之後不再自動併，留給人工）。
不成立的一律留在 3-13 讓人判斷。每筆照樣先留快照、寫進併回紀錄（理由「自動：主機名＋IP 相符」）、
可逐筆還原。掃描證據（source_record）跟著改掛正本，實測與 CIA 登記不符照樣標示——
防「私接機器沿用舊主機名＋IP」被併進正本後藏起來。
何時跑：deploy.sh／patch.sh 的 schema 遷移與台數口徑判定之後（`python dyn_shell.py auto-merge`）。
先在記憶體副本上模擬＋核對（帳外筆數差＝併回筆數、在管台數不變、沒有退役被併），過了才寫正式庫；
冪等——已併過的空殼已搬離 hardware（在合併紀錄裡），下一次不會再出現。

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

import lifecycle
from db import _now_local

SHELL_PREFIX = "DYN-"
# 2026-10-20 使用者：資產狀態只有 CIA 可以寫——併回不再把空殼標「停用」（那會讓「已退役」多算一台），
# 改成明確的合併關係：空殼整列搬進 hardware_merged（記 merged_into），見 lifecycle.archive_merged。
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
    restored_by   TEXT,
    reason        TEXT               -- 人工確認／自動：主機名＋IP 相符（決策 DYN2）
)
"""
MANUAL_REASON = "人工確認"
AUTO_REASON = "自動：主機名＋IP 相符"
AUTO_ACTOR = "系統（自動併回）"
#: 兩邊都有填才比的機器身分欄（任一邊空白不算衝突；MAC 不是必要條件，CIA 常沒填）
ID_FIELDS = (("mac", "MAC"), ("hw_serial", "硬體序號"), ("vm_uuid", "VM UUID"))


def _blank(v) -> bool:
    return str(v or "").strip() in ("", "0")


def _ensure(conn) -> None:
    conn.execute(AUDIT_SQL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(shell_merge_audit)")}
    if "reason" not in cols:          # v1.451.0 之前建的表沒有 reason 欄
        conn.execute("ALTER TABLE shell_merge_audit ADD COLUMN reason TEXT")


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
    # MAC 是 CIA 登記的身分欄（2026-10-21）：CIA 有值就不蓋——實測 MAC 全部在 asset_nic，
    # 對不上由系統校正（measured_hw.mac_view）標「⟲ 系統校正」；洗掉 CIA 值就沒得比了
    if "mac" in out and (cur["mac"] or "").strip():
        out.pop("mac")
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


def apply(conn, pairs: list[dict], actor: str | None, reason: str = MANUAL_REASON,
          rebuild: bool = True) -> dict:
    """把使用者確認過的 {shell_serial, target_serial} 併回（auto_merge 也走這一支，reason 不同）。

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
        if row is None and lifecycle.merged_into_of(conn, sserial):
            skipped.append({"shell_serial": sserial,
                            "reason": f"已經併到 {lifecycle.merged_into_of(conn, sserial)}"})
            continue
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
        # 有別的資料用外鍵掛在空殼上（人員／軟體／agent 金鑰）＝不是單純的空殼，不併、留給人看
        why_refs = shell_refs_reason(conn, sserial)
        if why_refs:
            skipped.append({"shell_serial": sserial, "reason": why_refs})
            continue
        # 快照先寫，後面任何一步出錯都還有底可以還原
        cur = conn.execute(
            "INSERT INTO shell_merge_audit (merged_at, actor, shell_serial, target_serial, snapshot, "
            "reason) VALUES (?,?,?,?,?,?)", (now, actor, sserial, tserial,
                                            json.dumps(snap, ensure_ascii=False, default=str), reason))
        audit_id = cur.lastrowid
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
        # 實測主機名校正（PRECEDENCE1）：空殼是登入收集過的，它的主機名就是機器自己講的——
        # 併回後留在正本的 hostname_observed，校正證據才不會跟著空殼一起消失
        if shell.get("collect_ok") == 1 and "hostname_observed" in target                 and _blank(target.get("hostname_observed")) and not _blank(shell.get("hostname")):
            sets["hostname_observed"] = shell["hostname"]
            sets["collect_checked_at"] = shell.get("collect_checked_at")
        if sets:
            assigns = ", ".join(f"{k} = ?" for k in sets)
            conn.execute(f"UPDATE hardware SET {assigns} WHERE asset_serial = ?",
                         (*sets.values(), tserial))
        # 4) 空殼搬進合併紀錄（記 merged_into＝正本），從 hardware 移除——**不改資產狀態**
        #    （資產狀態只有 CIA 可以寫；以前標「停用」會讓已退役多算一台，2026-10-20）
        ok, why = lifecycle.archive_merged(conn, sserial, tserial, audit_id, reason, now)
        if not ok:
            raise RuntimeError(f"{sserial} 搬進合併紀錄失敗：{why}")
        done.append({"shell_serial": sserial, "target_serial": tserial,
                     "moved": {"source_record": len(snap["source_record"]),
                               **{t: len(v) for t, v in snap["tables"].items()}},
                     "filled": sorted(sets)})
    conn.commit()
    if done and rebuild:
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
        "SELECT id, merged_at, actor, shell_serial, target_serial, restored_at, restored_by, "
        "coalesce(reason, ?) AS reason FROM shell_merge_audit ORDER BY id DESC LIMIT ?",
        (MANUAL_REASON, min(max(int(limit), 1), 1000)))]


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
    # 空殼從合併紀錄搬回 hardware（新做法）；舊做法（v1.471 前）空殼還在 hardware、被標停用 → 放回原值
    if lifecycle.unarchive_merged(conn, shell["asset_serial"]) is None:
        conn.execute("UPDATE hardware SET asset_status = ?, remark = ?, updated_at = ? "
                     "WHERE asset_serial = ?",
                     (shell.get("asset_status"), shell.get("remark"), shell.get("updated_at"),
                      shell["asset_serial"]))
    cols = [k for k in (*FILL_IF_EMPTY, "collect_ok", "collect_checked_at", "hostname_observed")
            if k in target]
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


# ---- 自動併回（決策 DYN2，2026-10-18）：條件見檔頭「自動併回」----

def _norm_id(field: str, v) -> str:
    return _norm_mac(v) if field == "mac" else str(v or "").strip().lower()


def _was_restored(conn, shell_serial: str) -> bool:
    _ensure(conn)
    return conn.execute("SELECT 1 FROM shell_merge_audit WHERE shell_serial = ? "
                        "AND restored_at IS NOT NULL LIMIT 1", (shell_serial,)).fetchone() is not None


def shell_refs_reason(conn, shell_serial: str) -> str | None:
    """有別的資料用外鍵掛在空殼上（人員／軟體／agent 金鑰…）＝不是單純空殼，不併。apply 與 auto_check 共用
    （2026-10-27：只有 apply 判、auto_check 沒判，預覽／模擬說「可自動併 1」、實際併 0，部署核對報「不冪等」）。"""
    refs = lifecycle._fk_refs(conn, shell_serial)
    return f"這筆 DYN- 還有資料掛著（{'、'.join(refs)}），不是單純空殼，要人看" if refs else None


def auto_check(conn, shell: dict, c: dict | None = None) -> tuple[bool, str]:
    """這筆 DYN- 能不能自動併回。回 (可以嗎, 理由)；不行的理由給 3-13 需人工看。"""
    import machine_identity

    c = c or classify(conn, shell)
    if c["kind"] != "mergeable" or not c.get("target_serial"):
        return False, c["reason"]
    hn = machine_identity.norm_name(shell.get("hostname"))
    if not hn or machine_identity.is_generic(hn):
        return False, "主機名空白或是通用名（localhost、template…），不能當同一台的證據"
    ip = str(shell.get("ip") or "").strip()
    if not ip or ip == "0.0.0.0":
        return False, "IP 空白或是 0.0.0.0 佔位"
    trow = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?",
                        (c["target_serial"],)).fetchone()
    if trow is None:
        return False, "找不到 CIA 那筆"
    t = dict(trow)
    if (t.get("asset_status") or "").strip() in _retired() or _is_off_book(t["asset_serial"]):
        return False, "CIA 那筆已退役或不是 CIA 正本"
    if machine_identity.norm_name(t.get("hostname")) != hn:
        return False, "主機名正規化後不相同"
    if str(t.get("ip") or "").strip() != ip:
        return False, "IP 不相同"
    for f, label in ID_FIELDS:
        a, b = _norm_id(f, shell.get(f)), _norm_id(f, t.get(f))
        if a and b and a != b:
            return False, f"兩邊都有填 {label} 但不一樣，可能是兩台機器"
    key = _key(shell.get("hostname"), ip, shell["asset_serial"])
    tw = cia_twins(conn, shell.get("hostname"), ip, shell.get("mac"))
    others = [r for r in tw["twins"] + tw["mac_conflict"] + tw["near"]
              if _key(r["hostname"], r["ip"], r["asset_serial"]) != key]
    if others:
        return False, (f"同一個 IP 或主機名在 CIA 另有別台的登記（{others[0]['asset_serial']} 等 "
                       f"{len(others)} 筆），要人看")
    if _was_restored(conn, shell["asset_serial"]):
        return False, "這筆曾經併回又被人還原，不再自動併"
    why_refs = shell_refs_reason(conn, shell["asset_serial"])
    if why_refs:
        return False, why_refs
    return True, AUTO_REASON


def auto_plan(conn) -> dict:
    """全部非退役 DYN- 分成「會自動併」與「不會」。不寫任何東西。"""
    auto, manual = [], []
    for it in preview(conn)["items"]:
        shell = dict(conn.execute("SELECT * FROM hardware WHERE asset_serial = ?",
                                  (it["shell_serial"],)).fetchone())
        ok, why = auto_check(conn, shell, it)
        row = {"shell_serial": it["shell_serial"], "target_serial": it["target_serial"],
               "kind": it["kind"], "hostname": it["hostname"], "ip": it["ip"], "reason": why}
        (auto if ok else manual).append(row)
    return {"auto": auto, "manual": manual}


def auto_merge(conn, actor: str = AUTO_ACTOR) -> dict:
    """自動併回全部符合條件的空殼。每一筆併前**再判一次**（前一筆併完可能改變後一筆的條件，
    例如正本補了 MAC 之後，同一台的第二個空殼 MAC 不同 → 變成需人工）。"""
    merged, skipped = [], []
    for p in auto_plan(conn)["auto"]:
        row = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (p["shell_serial"],)).fetchone()
        ok, why = auto_check(conn, dict(row)) if row is not None else (False, "找不到這筆")
        if not ok:
            skipped.append({"shell_serial": p["shell_serial"], "reason": why})
            continue
        r = apply(conn, [p], actor, reason=AUTO_REASON, rebuild=False)
        merged += r["merged"]
        skipped += r["skipped"]
    if merged:
        _rebuild_ci(conn)
    return {"merged": merged, "skipped": skipped}


def _offbook_numbers(conn) -> dict:
    """帳外筆數／台數、在管筆數／台數（首頁同一支 composition），加上獨立算的帳外 machine_key 集合。"""
    import manage_state

    s = manage_state.composition(conn)
    ret = _retired()
    rows = conn.execute("SELECT asset_serial, hostname, ip, asset_status FROM hardware").fetchall()
    active = [r for r in rows if (r["asset_status"] or "").strip() not in ret]
    return {"off_book_total": s["off_book_total"], "off_book_machines": s["off_book_machines"],
            "registered_total": s["registered_total"], "registered_hosts": s["registered_hosts"],
            "off_keys": {_key(r["hostname"], r["ip"], r["asset_serial"]) for r in active
                         if _is_off_book(r["asset_serial"])},
            "dyn_active": sum(1 for r in active if str(r["asset_serial"] or "").startswith(SHELL_PREFIX))}


def simulate(conn) -> dict:
    """在記憶體副本上跑一次 auto_merge，回前後數字。**不碰原庫**。
    verify_numbers（部署 [2d]）與 `dyn_shell.py auto-merge` 寫正式庫前都用這支。"""
    import sqlite3

    import db
    import machine_identity

    mem = sqlite3.connect(":memory:", check_same_thread=False)
    conn.backup(mem)
    mem.row_factory = sqlite3.Row
    db._register_ip_int(mem)
    try:
        _ensure(mem)
        machine_identity.refresh(mem)
        before = _offbook_numbers(mem)
        plan = auto_plan(mem)
        res = auto_merge(mem)
        after = _offbook_numbers(mem)
        pv = preview(mem)
        left = auto_plan(mem)["auto"]
        done = {d["shell_serial"] for d in res["merged"]}
        snaps = [json.loads(a["snapshot"]) for a in mem.execute(
            "SELECT shell_serial, snapshot FROM shell_merge_audit WHERE reason = ? "
            "AND restored_at IS NULL", (AUTO_REASON,)) if a["shell_serial"] in done]
    finally:
        mem.close()
        machine_identity.refresh(conn)
    ret = _retired()
    m_keys = {_key(x["shell"]["hostname"], x["shell"]["ip"], x["shell"]["asset_serial"]) for x in snaps}
    bad = [x["shell"]["asset_serial"] for x in snaps
           if (x["target"].get("asset_status") or "").strip() in ret
           or (x["shell"].get("asset_status") or "").strip() in ret
           or _is_off_book(x["target"]["asset_serial"])]
    return {"before": {k: v for k, v in before.items() if k != "off_keys"},
            "after": {k: v for k, v in after.items() if k != "off_keys"},
            "planned": len(plan["auto"]), "merged": len(res["merged"]), "skipped": res["skipped"],
            "auto": plan["auto"], "manual": plan["manual"],
            "expected_machine_drop": len(m_keys - after["off_keys"]),
            "bad_retired": bad, "left_auto": [x["shell_serial"] for x in left],
            "preview_total": sum(pv["counts"].values())}


def summary_line(sim: dict) -> str:
    """部署／patch 印的一行分類：跟 3-13 同一份歸類（preview 的 kind）。
    CIA 沒登記的＝真的存在、CIA 清冊漏登，不是待人工併回的工作，分開講（2026-10-18 審查：
    曾印成「留給人工 696 筆」）。EXIST1 起叫「CIA 待補登」——不是假資料、也不是「不用處理」，
    是要交給 CIA 窗口補登（3-1／首頁可匯出「CIA 補登清單」）。"""
    unreg = sum(1 for m in sim["manual"] if m["kind"] == "unregistered")
    need = len(sim["manual"]) - unreg
    return f"可自動併 {sim['planned']}／需人工 {need}／CIA 待補登 {unreg}"


def simulate_problems(sim: dict) -> list[str]:
    """simulate() 的不變式，任何一條不成立就不寫正式庫（verify_numbers 也會紅）。"""
    b, a, n = sim["before"], sim["after"], sim["merged"]
    out = []
    if b["off_book_total"] - a["off_book_total"] != n:
        out.append(f"帳外筆數 {b['off_book_total']}→{a['off_book_total']}，差不等於併回 {n} 筆")
    if b["off_book_machines"] - a["off_book_machines"] != sim["expected_machine_drop"]:
        out.append(f"帳外台數 {b['off_book_machines']}→{a['off_book_machines']}，"
                   f"差不等於併回後不再有帳外紀錄的 {sim['expected_machine_drop']} 台")
    if (b["registered_total"], b["registered_hosts"]) != (a["registered_total"], a["registered_hosts"]):
        out.append(f"在管筆數／台數變了：{b['registered_total']}/{b['registered_hosts']} → "
                   f"{a['registered_total']}/{a['registered_hosts']}")
    if sim["bad_retired"]:
        out.append(f"有退役或帳外的被併：{sim['bad_retired'][:5]}")
    if sim["left_auto"]:
        out.append(f"跑完還有符合條件沒併的（不冪等）：{sim['left_auto'][:5]}")
    if sim["preview_total"] != a["dyn_active"]:
        out.append(f"3-13 三類加總 {sim['preview_total']} ≠ 剩下的非退役 DYN- {a['dyn_active']}")
    if sim["merged"] != sim["planned"]:
        out.append(f"預定併 {sim['planned']} 筆、實際併 {sim['merged']} 筆")
    return out


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


def _cli(argv: list[str]) -> int:
    """deploy.sh／patch.sh：`python dyn_shell.py auto-merge [--dry-run]`。
    rc 0＝正常（併了 N 筆或沒有可併的）；3＝副本上不變式不成立，**正式庫沒動**（警告、部署照常）。"""
    import os
    from pathlib import Path

    import db

    if not argv or argv[0] != "auto-merge":
        print("用法：python dyn_shell.py auto-merge [--dry-run]")
        return 2
    dry = "--dry-run" in argv
    p = os.environ.get("ASSET_DB_PATH")
    conn = db.get_connection(Path(p) if p else None)
    try:
        if not dry:
            _ensure(conn)
            conn.commit()
        sim = simulate(conn)
        probs = simulate_problems(sim)
        b, a = sim["before"], sim["after"]
        print(f"掃描空殼自動併回（DYN2）：{summary_line(sim)}；"
              f"CIA 待補登（DYN-/VC-/AUTO- 紀錄） {b['off_book_total']}→{a['off_book_total']} 筆／"
              f"{b['off_book_machines']}→{a['off_book_machines']} 台；CIA 登記 {b['registered_hosts']} 台不變")
        if probs:
            print("!! 副本上核對沒過，正式庫不動：")
            for x in probs:
                print("   - " + x)
            return 3
        if dry or not sim["planned"]:
            return 0
        res = auto_merge(conn)
        print(f"   已併回 {len(res['merged'])} 筆（3-13 併回紀錄可逐筆還原）；略過 {len(res['skipped'])} 筆")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    # 交給 import 進來的那一份模組跑（machine_identity 的 _STATE 才是同一份，見 machine_identity.py）
    import dyn_shell as _mod

    sys.exit(_mod._cli(sys.argv[1:]))
