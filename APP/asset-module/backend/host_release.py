"""重複登記管理 階段二：釋放序號（硬刪除，危險區 D4＋D3）與還原（2.041.97 起；2.042.00 改為允許清單制、2.042.01 逐欄審核）。

只做一件事：同一台（machine_key，全站同一把尺）被登記成多筆時，管理員確認後把「多出來的序號」整個刪掉，
刪之前先把這個序號在可快照的表裡的列完整存成 JSON 快照（merge_audit），之後可以原樣還原。

設計重點（對照 docs/codex-review/2026-10-30/230、233、235 與 GPT 三輪審查）：
* 成員一律在後端用 vip_view.duplicate_candidate_groups（＝machine_key）重算，不信前端；不自寫去重。
* **允許清單制**：只有 SNAPSHOT_TABLES 列出的業務表會被快照與刪除。其他任何以序號為鍵／指向 hardware／欄名含 serial 的表，
  只要該序號在其中仍有列，就擋下釋放（未列入可快照清單），不靠名稱猜「是不是祕密」決定要不要放進快照。
  名稱判斷（_sensitive_name）只當額外護欄：清單內的表若出現未經審核、看起來像祕密的欄位，也一樣擋下。
* 「刪什麼」與「存什麼」只有一份定義（_catalog／_gather）：快照範圍＝刪除範圍＝預覽範圍。
* 歷史／稽核／日誌類（KEEP_TABLES）刻意保留不動（刪了會丟證據），預覽會列出保留了多少筆。
* 刪除、快照、稽核在同一個交易（BEGIN IMMEDIATE），任何一步失敗全部回滾；交易內再核一次：
  預覽指紋（含列內容）一致、釋放後該序號在所有非保留表都是 0 筆（孤兒掃描）、現役台數（machine_key 去重）不變、被保留的序號還在。
* 還原：把快照寫回；格式不符、序號已被占用、內部編號被占用、任何唯一性衝突、接回的關聯列不在或已被改，一律拒絕並說明，不覆蓋、不做一半。
* 管理員限定（路由層 ADMIN_WRITE）；登入停用時一律拒絕（沒有操作人可稽核＝fail-closed）。

不處理：以「文字清單」引用序號的欄位（contract_row.matched_serials、daily_snapshot.serials、rack_diagram.serials 等）
——那是歷史／彙總文字，不是以序號為鍵的資料列，釋放不改它們（值剛好等於序號時會被當成未列入清單而擋下）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from datetime import datetime

#: 3＝links 以主鍵（pk／keys）記錄；2 是審查前的暫行格式（links 用 rowids），不再支援還原。
SNAPSHOT_FORMAT = 3
OLD_FORMAT_MSG = "此紀錄是舊格式，無法自動還原"
CONFIRM_WORD = "釋放"
LINK_SUFFIX = "（接回用）"
#: 單一序號快照上限；超過就拒絕釋放（避免把大量資料塞進 merge_audit）。
MAX_SNAPSHOT_BYTES = 20 * 1024 * 1024

#: **可快照（＝可刪除、可還原）的業務表，以及每張表逐欄審核過的完整欄位集合**（2.042.01 起）。
#: 來源：schema.sql（db.init_db 後的實際欄位，含 db._migrate 補的欄位）中以 asset_serial 為鍵或外鍵指向 hardware 的業務表，
#: 加上各模組懶建的序號覆寫／對應表（cia_pending、os_override、onboard_exempt、ocp_cluster、count_bridge、rack_layout、capacity_analysis），
#: 由 tests 對照 schema 產生後逐欄人工審核（例：doc_archive.has_secrets＝旗標、host_account.authorized_keys＝收集到的公開金鑰清單、
#: hardware.root_key_at＝收集金鑰佈署時間，都不是祕密本身）。
#: **正式表出現這裡沒有的欄位（不管叫什麼名字）→ 該序號在該表有列就擋下釋放（欄位未審核）**，所以 value／payload 之類新增欄位也進不了快照。
#: 新增表或欄位要進來：先審核那個欄位不是密碼／金鑰等祕密，再加進這裡並補測試。
#: source_record 只做「斷開／接回」resolved_hardware_id，不快照內容，但欄位一樣要審核。
REVIEWED_COLUMNS_SRC = {
    "hardware": frozenset(['api_id', 'asset_name', 'asset_purpose', 'asset_serial', 'asset_status', 'availability', 'big_ip_vip', 'collect_account', 'collect_checked_at', 'collect_error', 'collect_ok', 'confidentiality', 'created_at', 'custodian', 'device_model', 'environment', 'group_name', 'hardware_no', 'hostname', 'hostname_observed', 'hw_serial', 'id', 'infra_type', 'integrity', 'inventory_department', 'inventory_division', 'ip', 'is_vm', 'mac', 'manual_updated_at', 'os', 'os_cia', 'owner', 'owning_company', 'pending_at', 'pending_by', 'pending_note', 'pending_status', 'physical_location', 'quantity', 'rack_no', 'remark', 'request_no', 'root_key_at', 'subnet', 'system_category', 'updated_at', 'usage_unit', 'user_name', 'vi_sdk_server', 'vm_uuid']),
    "account_finding": frozenset(['asset_serial', 'created_at', 'detail', 'exempt_by', 'exempt_reason', 'exempt_until', 'id', 'ip', 'label', 'law', 'rule_id', 'run_id', 'severity', 'username', 'verdict']),
    "asset_hba": frozenset(['asset_serial', 'collected_at', 'downgraded', 'fabric', 'hba_name', 'id', 'port_state', 'san_port', 'san_switch', 'sources', 'speed_cur', 'speed_max', 'target_wwpn', 'wwnn', 'wwpn', 'zone']),
    "asset_nic": frozenset(['asset_serial', 'bond', 'bond_mode', 'collected_at', 'downgraded', 'driver', 'gateway', 'id', 'ip', 'ips', 'mac', 'model', 'nic_name', 'nic_type', 'sources', 'speed_cur', 'speed_max', 'state', 'subnet', 'vlan']),
    "baseline_drift": frozenset(['asset_serial', 'baseline', 'current', 'decided_at', 'decided_by', 'first_detected_at', 'id', 'item_key', 'last_detected_at', 'note', 'resolved_at', 'status']),
    "capacity_device_map": frozenset(['asset_serial', 'created_at', 'created_by', 'device', 'id', 'note', 'source', 'valid_from', 'valid_to']),
    "checklist_item": frozenset(['asset_serial', 'biz_system', 'contact_department', 'contact_name', 'contact_phone', 'contact_role', 'environment', 'hostname', 'id', 'ip', 'note', 'physical_location', 'severity', 'snapshot_id', 'sort_depth', 'status', 'updated_at', 'updated_by']),
    "ci_node": frozenset(['asset_serial', 'attrs', 'created_at', 'first_seen_at', 'gone_at', 'health', 'is_spof', 'label', 'last_seen_at', 'manual_locked', 'node_id', 'node_type', 'source', 'source_key', 'updated_at']),
    "cia_pending_change": frozenset(['asset_serial', 'changed_at', 'changed_by', 'field', 'field_label', 'hostname', 'id', 'ip', 'new_value', 'old_value', 'reason', 'synced_at', 'synced_by']),
    "count_role_override": frozenset(['asset_serial', 'note', 'role', 'updated_at', 'updated_by']),
    "doc_archive": frozenset(['applicant', 'applicant_unit', 'asset_serial', 'bind_confidence', 'checkboxes', 'checklist_json', 'doc_type', 'extracted', 'file_ext', 'file_name', 'file_path', 'form_date', 'full_text', 'has_secrets', 'hostname', 'id', 'imported_at', 'imported_by', 'ip', 'is_current', 'is_decommission', 'ref_request_no', 'request_no', 'review_status', 'reviewed_at', 'reviewed_by', 'sections_json', 'system_name', 'unit_manager', 'values_json', 'warnings']),
    "golive_check": frozenset(['asset_serial', 'id', 'passed_at', 'passed_by', 'started_at', 'status']),
    "host_account": frozenset(['acct_expires', 'asset_serial', 'authorized_keys', 'can_login', 'first_seen', 'gecos', 'gid', 'gone_at', 'home', 'id', 'ip', 'is_sudoer', 'kind', 'kind_override', 'last_login', 'last_seen', 'local_groups', 'login_source', 'never_logged_in', 'note', 'os_family', 'os_id', 'os_version', 'principal_class', 'principal_source', 'priv_groups', 'pw_expires', 'pw_last_change', 'pw_max_days', 'pw_status', 'shell', 'sid', 'source', 'sudo_nopasswd', 'uid', 'username']),
    "host_connection": frozenset(['asset_serial', 'count', 'direction', 'first_seen', 'host_ip', 'id', 'last_seen', 'local_port', 'peer_ip', 'peer_port']),
    "host_iis": frozenset(['asset_serial', 'checked_at', 'collected_at', 'ip', 'last_note', 'last_state', 'payload', 'state']),
    "host_metric_latest": frozenset(['asset_serial', 'collected_at', 'metric_key', 'received_at', 'unit', 'value']),
    "host_package": frozenset(['arch', 'asset_serial', 'first_seen', 'gone_at', 'id', 'installed_at', 'ip', 'last_seen', 'name', 'source', 'vendor', 'version']),
    "host_rebuild": frozenset(['asset_serial', 'collected_at', 'data_json', 'ip', 'platform', 'raw_json']),
    "host_service": frozenset(['asset_serial', 'bind_addr', 'exposure', 'first_seen', 'gone_at', 'guess_source', 'id', 'ip', 'is_infra', 'last_seen', 'port', 'process', 'proto', 'service_guess', 'source']),
    "host_spec": frozenset(['asset_serial', 'collected_at', 'cores', 'cpu', 'ip', 'mem_mb', 'model', 'platform', 'psu_json', 'raw_json', 'serial', 'spec_json', 'storage', 'storage_json', 'vendor']),
    "host_winservice": frozenset(['account', 'asset_serial', 'delayed', 'description', 'display_name', 'first_seen', 'gone_at', 'id', 'ip', 'last_seen', 'name', 'path_name', 'pid', 'source', 'start_mode', 'state', 'vendor']),
    "ocp_cluster_override": frozenset(['asset_serial', 'cluster', 'reason', 'role', 'updated_at', 'updated_by']),
    "onboard_exempt": frozenset(['asset_serial', 'created_at', 'created_by', 'id', 'kind', 'reason', 'removed_at', 'removed_by']),
    "os_type_override": frozenset(['asset_serial', 'auto_os_type', 'created_at', 'created_by', 'hostname', 'id', 'ip', 'os_type', 'reason', 'removed_at', 'removed_by']),
    "personnel": frozenset(['asset_serial', 'asset_status', 'availability', 'belong_department', 'belong_division', 'confidentiality', 'created_at', 'group_name', 'id', 'infra_type', 'integrity', 'inventory_department', 'inventory_division', 'job_desc', 'person_name', 'phone', 'proxy1', 'proxy1_phone', 'remark', 'request_no']),
    "provision_request": frozenset(['applicant', 'applicant_unit', 'asset_serial', 'attachment_name', 'attachment_path', 'change_kind', 'created_at', 'created_by', 'form_date', 'id', 'raw_fields', 'request_no', 'source', 'unit_manager']),
    "rack_position_override": frozenset(['asset_serial', 'cia_rack', 'cia_raw', 'cia_room', 'cia_u_end', 'cia_u_start', 'key', 'note', 'rack', 'room', 'status', 'synced_at', 'u_end', 'u_start', 'updated_at', 'updated_by']),
    "software": frozenset(['api_id', 'asset_name', 'asset_purpose', 'asset_serial', 'asset_status', 'availability', 'backup_frequency', 'cloud', 'cloud_service_type', 'collect_checked_at', 'collect_error', 'collect_ok', 'confidentiality', 'created_at', 'custodian', 'db_software', 'group_name', 'handles_pii', 'hostname', 'id', 'infra_type', 'integrity', 'inventory_department', 'inventory_division', 'ip', 'os', 'outsourced_maintenance', 'owner', 'owning_company', 'project_zone', 'remark', 'request_no', 'usage_unit']),
    "source_record": frozenset(['collected_at', 'id', 'payload', 'resolved_confidence', 'resolved_hardware_id', 'resolved_rule', 'resolved_status', 'source', 'source_key']),
    "win_onboard_state": frozenset(['asset_serial', 'ip', 'is_admin', 'last_at', 'last_message', 'last_ok', 'machine_key', 'onboarded_at', 'onboarded_by', 'opted_in', 'revoked_at', 'revoked_by']),
}
HARDWARE_COLUMNS: frozenset = REVIEWED_COLUMNS_SRC.pop("hardware")
SNAPSHOT_TABLES: dict[str, frozenset] = REVIEWED_COLUMNS_SRC

#: 名稱護欄（只用於說明理由，不決定放不放行）：名稱（去底線、小寫）含這些字＝看起來像祕密；'key' 任何位置都算，除非整個名稱是業務鍵。
SENSITIVE_RE = re.compile(r"password|passwd|secret|token|credential|passphrase|keymaterial|key|pem")
#: 業務鍵（整個名稱去底線、小寫後完全相同才算）：schema.sql 與各模組實際出現的 machine_key、item_key、source_key…
BUSINESS_KEYS = frozenset({
    "key", "machinekey", "legacymachinekey", "itemkey", "sourcekey", "modulekey", "metrickey", "rowkey", "eventkey",
    "syskey", "groupkey", "statekey", "rulekey", "envkey", "fieldkey", "dimkey", "diffkey", "targetkey", "naturalkey",
    "hostmatchkey", "sortkey", "checkkey", "namekey", "serialkey",
})

#: 歷史／稽核／日誌類：有 asset_serial 欄位但刻意不刪、不快照（刪了會丟證據；序號字串留在裡面不影響運作）。
KEEP_TABLES = frozenset({
    "hardware_merged", "collect_dispatch_result", "package_change", "host_ops_note", "inv_task_row",
    "inv_task_diff_state", "alive_check_item", "ft_job_host", "ft_cmd_result", "onboard_paste_pending",
    "win_collect_session", "merge_audit", "activity_log",
})
KEEP_SUFFIXES = ("_audit", "_log", "_trace")


class ReleaseError(Exception):
    """可預期的拒絕（帶 HTTP 狀態碼與給人看的說明）。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def is_kept(table: str) -> bool:
    t = table.lower()
    return t in KEEP_TABLES or t.endswith(KEEP_SUFFIXES)


def _norm(name: str) -> str:
    return name.lower().replace("_", "")


def _sensitive_name(name: str) -> bool:
    """名稱看起來像祕密嗎（寧可多報）。只用來在擋下理由裡加註；放不放行由逐欄審核清單決定。"""
    nn = _norm(name)
    if nn in BUSINESS_KEYS:
        return False
    return bool(SENSITIVE_RE.search(nn))


def _unreviewed(cols, reviewed: frozenset) -> list[str]:
    """實際欄位中不在審核清單的（比對不分大小寫——SQLite 識別字本來就不分）。"""
    rv = {c.lower() for c in reviewed}
    return [c for c in cols if c.lower() not in rv]


def _bad_label(bad: list[str]) -> str:
    return "欄位未審核：" + "、".join(c + ("（名稱疑似祕密）" if _sensitive_name(c) else "") for c in bad)


# ====================================================================== 表清單（允許清單＋逐欄審核＋執行時掃描）
def _catalog(conn: sqlite3.Connection) -> dict:
    """回 {"serial", "link", "kept", "unlisted", "guarded", "suspect", "hardware_bad"}。識別字比對一律不分大小寫。

    serial：清單內且欄位全部審核過的表，以序號值為鍵的欄位（asset_serial，或外鍵指向 hardware(asset_serial) 的欄）→ 快照＋刪除。
    link：同上，以 hardware.id 為外鍵的欄。可為空且有主鍵 → 記主鍵後設 NULL（還原依主鍵接回並驗筆數）；否則快照＋刪除。
    kept：歷史／稽核／日誌，保留不動。
    unlisted：不在清單、也不是保留表，但有序號欄位／指向 hardware 的外鍵／欄名含 serial → 該序號在其中有列就擋下（未列入可快照清單）。
    guarded：在清單內，但實際有未審核的欄位 → 該序號在其中有列就擋下（欄位未審核：x）。
    suspect：清單內的表裡，欄名含 serial 但不是序號鍵的欄（不論該表是否 guarded）→ 值等於該序號就擋下。
    hardware_bad：hardware 有未審核的欄位 → 任何釋放都擋下（快照一定含 hardware 整列）。
    """
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    serial: list[tuple[str, str]] = []
    link: list[tuple[str, str, list]] = []
    kept: list[str] = []
    unlisted: list[tuple[str, str, str]] = []
    guarded: list[tuple[str, str, str, list]] = []
    suspect: list[tuple[str, str]] = []
    hw_cols = [r[1] for r in conn.execute("PRAGMA table_info(hardware)")]
    hardware_bad = _unreviewed(hw_cols, HARDWARE_COLUMNS)
    for t in names:
        tl = t.lower()
        if tl == "hardware":
            continue
        rows = conn.execute(f"PRAGMA table_info({_q(t)})").fetchall()
        info = {r[1]: r for r in rows}
        by_lower = {c.lower(): c for c in info}
        pk = [r[1] for r in sorted((r for r in rows if r[5]), key=lambda r: r[5])]
        cands: dict[str, tuple[str, bool]] = {}          # 欄（實際名稱） -> (kind, 可為空)
        if "asset_serial" in by_lower:
            cands[by_lower["asset_serial"]] = ("serial", False)
        for fk in conn.execute(f"PRAGMA foreign_key_list({_q(t)})"):
            if str(fk[2]).lower() != "hardware":
                continue
            frm = by_lower.get(str(fk[3]).lower(), fk[3])
            to = str(fk[4] or "id").lower()
            if to == "asset_serial":
                cands[frm] = ("serial", False)
            elif to == "id":
                cands[frm] = ("id", not info[frm][3])
        extra = [c for c in info if "serial" in c.lower() and c not in cands]
        if not cands and not extra:
            continue
        if is_kept(t):
            if "asset_serial" in by_lower:
                kept.append(t)
            continue
        if tl not in SNAPSHOT_TABLES:
            for col, (kind, _n) in cands.items():
                unlisted.append((t, col, kind))
            for c in extra:
                unlisted.append((t, c, "serial"))
            continue
        for c in extra:                                  # 不論這張表有沒有未審核欄位，都要先登記（不能因為 continue 漏掉）
            suspect.append((t, c))
        bad = _unreviewed(list(info), SNAPSHOT_TABLES[tl])
        if bad:
            for col, (kind, _n) in cands.items():
                guarded.append((t, col, kind, bad))
            continue
        for col, (kind, nullable) in cands.items():
            if kind == "serial":
                serial.append((t, col))
            else:
                link.append((t, col, pk if (nullable and pk) else []))
    return {"serial": serial, "link": link, "kept": kept, "unlisted": unlisted, "guarded": guarded,
            "suspect": suspect, "hardware_bad": hardware_bad}


def _enc(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"__b64__": base64.b64encode(bytes(v)).decode("ascii")}
    return v


def _dec(v):
    if isinstance(v, dict) and "__b64__" in v:
        return base64.b64decode(v["__b64__"])
    return v


def _row_dict(row: sqlite3.Row) -> dict:
    return {k: _enc(row[k]) for k in row.keys()}


def _select(conn, table: str, col: str, val):
    return conn.execute(f"SELECT * FROM {_q(table)} WHERE {_q(col)} = ?", (val,)).fetchall()


def _sig(table: str, rd: dict) -> tuple:
    return (table, json.dumps(rd, sort_keys=True, default=str))


def _gather(conn: sqlite3.Connection, serial: str, cat: dict | None = None) -> dict:
    """這個序號在所有納入的表裡的列。快照、預覽數字、刪除對象都從這裡來（唯一一份）。
    同一張表的同一列若同時被兩個欄位（例如 asset_serial 與另一個指向 hardware 的外鍵欄）選到，只快照一次。"""
    cat = cat or _catalog(conn)
    hw = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
    if hw is None:
        raise ReleaseError(409, f"序號 {serial} 已不在資產清冊內")
    out = {"hardware": {"table": "hardware", "column": "asset_serial", "kind": "serial", "rows": [_row_dict(hw)]},
           "tables": {}, "links": {}}
    seen: set = set()
    for t, c in cat["serial"]:
        rows = [_row_dict(r) for r in _select(conn, t, c, serial)]
        fresh = [r for r in rows if _sig(t, r) not in seen]
        seen.update(_sig(t, r) for r in rows)
        if fresh:
            out["tables"][f"{t}.{c}"] = {"table": t, "column": c, "kind": "serial", "rows": fresh}
    for t, c, pk in cat["link"]:
        if pk:
            sel = ", ".join(_q(x) for x in pk)
            keys = [[_enc(r[x]) for x in pk] for r in conn.execute(
                f"SELECT {sel} FROM {_q(t)} WHERE {_q(c)} = ?", (hw["id"],)).fetchall()]
            if keys:
                out["links"][f"{t}.{c}"] = {"table": t, "column": c, "kind": "relink", "hardware_id": hw["id"], "pk": pk, "keys": keys}
        else:
            rows = [_row_dict(r) for r in _select(conn, t, c, hw["id"])]
            fresh = [r for r in rows if _sig(t, r) not in seen]
            seen.update(_sig(t, r) for r in rows)
            if fresh:
                out["tables"][f"{t}.{c}"] = {"table": t, "column": c, "kind": "id", "hardware_id": hw["id"], "rows": fresh}
    return out


def _counts(g: dict) -> dict:
    c = {"hardware": len(g["hardware"]["rows"])}
    for k, v in g["tables"].items():
        c[v["table"] if v["column"] == "asset_serial" else k] = len(v["rows"])
    for k, v in g["links"].items():
        c[k + LINK_SUFFIX] = len(v["keys"])
    return c


def _row_total(cnt: dict) -> int:
    return sum(v for k, v in cnt.items() if not k.endswith(LINK_SUFFIX))


def _content_hash(g: dict) -> str:
    """要刪（與要接回）的列『內容』的雜湊：預覽後任何一格內容變了（筆數不變也一樣），指紋就會不同。"""
    def rows_of(v):
        return sorted(json.dumps(r, sort_keys=True, default=str) for r in v["rows"])
    blob = {"hardware": rows_of(g["hardware"]),
            "tables": {k: rows_of(v) for k, v in sorted(g["tables"].items())},
            "links": {k: sorted(json.dumps(x, default=str) for x in v["keys"]) for k, v in sorted(g["links"].items())}}
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _make_snapshot(conn, serial: str, g: dict, cat: dict) -> dict:
    return {"format": SNAPSHOT_FORMAT, "serial": serial, "hardware": g["hardware"], "tables": g["tables"],
            "links": g["links"], "kept_refs": _kept_counts(conn, serial, cat)}


def _orphan_tables(conn: sqlite3.Connection, serial: str) -> list[str]:
    """孤兒掃描：**不經過 _catalog**、直接掃每張有 asset_serial 欄位的非保留表，回仍有這個序號的表。
    清單外的表在釋放前就會擋下，所以釋放後這裡只要有任何一張表還留著該序號，就是漏刪 → 整筆回滾。"""
    left = []
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        if is_kept(t) or t == "hardware":
            continue
        cols = [r[1].lower() for r in conn.execute(f"PRAGMA table_info({_q(t)})")]
        if "asset_serial" not in cols:
            continue
        if conn.execute(f"SELECT 1 FROM {_q(t)} WHERE asset_serial = ? LIMIT 1", (serial,)).fetchone():
            left.append(t)
    return left


def _suspects(conn, serial: str, cat: dict) -> list[dict]:
    """清單內的表裡，欄名含 serial 但不是序號鍵的欄位，值等於這個序號的列（有就必須先處理）。"""
    out = []
    for t, c in cat["suspect"]:
        n = conn.execute(f"SELECT COUNT(*) FROM {_q(t)} WHERE {_q(c)} = ?", (serial,)).fetchone()[0]
        if n:
            out.append({"table": t, "column": c, "rows": n})
    return out


def _blocked_rows(conn, serial: str, hw_id, cat: dict) -> list[dict]:
    """清單外的表（unlisted）與有未審核疑似祕密欄位的清單內表（guarded）裡，該序號仍有的列。有就擋下釋放。"""
    out = []
    for t, c, kind in cat["unlisted"]:
        n = conn.execute(f"SELECT COUNT(*) FROM {_q(t)} WHERE {_q(c)} = ?", (serial if kind == "serial" else hw_id,)).fetchone()[0]
        if n:
            out.append({"table": t, "column": c, "rows": n, "blocking": True, "reason": "未列入可快照清單"})
    for t, c, kind, bad in cat["guarded"]:
        n = conn.execute(f"SELECT COUNT(*) FROM {_q(t)} WHERE {_q(c)} = ?", (serial if kind == "serial" else hw_id,)).fetchone()[0]
        if n:
            out.append({"table": t, "column": c, "rows": n, "blocking": True,
                        "reason": _bad_label(bad)})
    return out


def _blockers(conn, serial: str, g: dict, cat: dict) -> tuple[list[str], dict]:
    """釋放前必須先處理的問題（預覽會列出，實刪會拒絕）。回 (訊息清單, 附帶資訊)。"""
    msgs: list[str] = []
    if cat["hardware_bad"]:
        msgs.append("資產主檔" + _bad_label(cat["hardware_bad"]) + "；快照前需先完成欄位審核，暫不能釋放")
    sus = _suspects(conn, serial, cat)
    for s in sus:
        msgs.append(f"序號 {serial} 在 {s['table']}.{s['column']} 還有 {s['rows']} 筆，這個欄位未納入自動處理（疑似關聯資料），請先人工處理後再釋放")
    blk = _blocked_rows(conn, serial, g["hardware"]["rows"][0].get("id"), cat)
    for b in blk:
        msgs.append(f"序號 {serial} 在 {b['table']}.{b['column']} 還有 {b['rows']} 筆（{b['reason']}），不會進快照也不會刪；"
                    "釋放後會留下無法還原的資料，請先到原功能處理後再釋放")
    size = len(json.dumps(_make_snapshot(conn, serial, g, cat), ensure_ascii=False, default=str).encode("utf-8"))
    if size > MAX_SNAPSHOT_BYTES:
        msgs.append(f"序號 {serial} 的快照約 {size // 1024 // 1024} MB，超過上限 {MAX_SNAPSHOT_BYTES // 1024 // 1024} MB，不能釋放")
    return msgs, {"suspects": sus, "protected": blk, "snapshot_bytes": size}


def _kept_counts(conn, serial: str, cat: dict) -> dict:
    out = {}
    for t in cat["kept"]:
        n = conn.execute(f"SELECT COUNT(*) FROM {_q(t)} WHERE asset_serial = ?", (serial,)).fetchone()[0]
        if n:
            out[t] = n
    return out


# ====================================================================== 成員（用 machine_key 重算）
def _group(conn: sqlite3.Connection, key: str) -> list[dict]:
    import vip_view
    for k, members in vip_view.duplicate_candidate_groups(conn):
        if k == key:
            return members
    raise ReleaseError(409, "這一組現在已經不是重複登記（可能已被處理、標為不是重複，或成員已變動），請重新整理")


def _machine_count(conn: sqlite3.Connection) -> int:
    """現役台數（machine_key 去重，退役不算）；釋放前後必須相同。"""
    import manage_state as ms
    import system_stats as ss
    keys = set()
    for r in conn.execute("SELECT hostname, ip, asset_serial, asset_status FROM hardware"):
        if (r["asset_status"] or "").strip() in ms.RETIRED_STATUS:
            continue
        keys.add(ss.machine_key(r["hostname"], r["ip"], r["asset_serial"]))
    return len(keys)


def _validate(conn, key: str, serials) -> tuple[list[dict], list[str]]:
    release: list[str] = []
    for s in serials or []:
        s = str(s).strip()
        if s and s not in release:
            release.append(s)
    if not release:
        raise ReleaseError(400, "沒有要釋放的序號")
    members = _group(conn, key)
    cur = [m["asset_serial"] for m in members]
    missing = [s for s in release if s not in cur]
    if missing:
        raise ReleaseError(409, "這幾個序號已經不在這組裡了，資料可能已變更，請重新整理：" + "、".join(sorted(missing)))
    if len(release) >= len(cur):
        raise ReleaseError(400, "釋放後這組至少要保留一筆，不能全部釋放")
    return members, release


def _fingerprint(key: str, cur: list[str], release: list[str], per: dict) -> str:
    blob = json.dumps({"key": key, "cur": sorted(cur), "rel": sorted(release), "per": per},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


# ====================================================================== 預覽（只讀）
def preview(conn: sqlite3.Connection, key: str, serials) -> dict:
    import lifecycle
    import vip_view
    members, release_list = _validate(conn, key, serials)
    cur = [m["asset_serial"] for m in members]
    cat = _catalog(conn)
    per: dict = {}
    items = []
    blockers: list[str] = []
    by_table: dict[str, int] = {}
    for s in release_list:
        g = _gather(conn, s, cat)
        cnt = _counts(g)
        per[s] = {"c": cnt, "h": _content_hash(g)}
        msgs, extra = _blockers(conn, s, g, cat)
        blockers += msgs
        for t, n in cnt.items():
            by_table[t] = by_table.get(t, 0) + n
        m = next(x for x in members if x["asset_serial"] == s)
        items.append({"serial": s, "hostname": m.get("hostname"), "ip": m.get("ip"), "api_id": m.get("api_id"),
                      "asset_status": m.get("asset_status"), "from_cia": bool(lifecycle.is_from_cia(s)),
                      "counts": cnt, "total_rows": _row_total(cnt), "kept_refs": _kept_counts(conn, s, cat),
                      "suspects": extra["suspects"], "protected": extra["protected"], "snapshot_bytes": extra["snapshot_bytes"]})
    cls = vip_view.classify_duplicate_group([dict(m) for m in members])
    keep = [s for s in cur if s not in release_list]
    warnings = []
    if cls["kind"] != "suspect":
        warnings.append(f"這一組分類是「{cls['kind_label']}」，不是疑似真重複：各筆可能是同一台機器上真實的服務登記，"
                        "釋放會一併刪掉該筆的人員、軟體與關聯資料。")
    cia = [i["serial"] for i in items if i["from_cia"]]
    if cia:
        warnings.append("以下序號來自 CIA 清冊，釋放後下次匯入 CIA 會再出現，請同步修改 CIA 清冊：" + "、".join(cia))
    return {
        "key": key, "hostname": members[0].get("hostname"), "ip": members[0].get("ip"),
        "kind": cls["kind"], "kind_label": cls["kind_label"],
        "current": cur, "keep": keep, "release": items,
        "totals": {"serials": len(items), "rows": sum(i["total_rows"] for i in items), "by_table": by_table},
        "cia_serials": cia, "warnings": warnings, "blockers": blockers, "blocked": bool(blockers),
        "count_before": len(cur), "count_after": len(keep),
        "confirm_word": CONFIRM_WORD, "fingerprint": _fingerprint(key, cur, release_list, per),
    }


# ====================================================================== 稽核表
def _ensure_audit(conn: sqlite3.Connection) -> None:
    from _app_shared import _ensure_merge_audit
    _ensure_merge_audit(conn)


# ====================================================================== 閘門
def gate(conn, session) -> str:
    """管理員＋登入啟用。登入停用時假 session 是 admin，但沒有真實操作人可稽核，一律拒絕。"""
    import auth
    from _app_shared import auth_disabled
    if auth_disabled(conn):
        raise ReleaseError(403, "登入已停用：釋放與還原需要登入的管理員帳號才能操作（無法記錄是誰做的）")
    if not auth.is_admin(session):
        raise ReleaseError(403, "只有管理員可以釋放或還原序號")
    try:
        return session["username"]
    except (KeyError, IndexError, TypeError):
        raise ReleaseError(403, "無法辨識操作人")


def _begin(conn) -> None:
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")


def _delete_rows(conn, table: str, col: str, val) -> int:
    return conn.execute(f"DELETE FROM {_q(table)} WHERE {_q(col)} = ?", (val,)).rowcount


# ====================================================================== 釋放
def release(conn: sqlite3.Connection, key: str, serials, *, actor: str, confirm: str | None, fingerprint: str | None,
            client_ip: str | None = None) -> dict:
    """釋放。一律要預覽指紋（涵蓋要刪的列內容）與確認文字；沒有繞過用的模式。"""
    import activity
    from lifecycle import is_from_cia
    batch = datetime.now().strftime("%Y%m%d%H%M%S%f")
    _ensure_audit(conn)
    conn.commit()
    _begin(conn)
    try:
        members, release_list = _validate(conn, key, serials)
        cur = [m["asset_serial"] for m in members]
        word = (confirm or "").strip()
        if word != CONFIRM_WORD and word not in release_list:
            raise ReleaseError(400, f"請輸入「{CONFIRM_WORD}」或要釋放的序號來確認")
        if not fingerprint:
            raise ReleaseError(400, "缺少預覽結果，請先按「預覽」確認要刪的內容")
        cat = _catalog(conn)
        gathered = {s: _gather(conn, s, cat) for s in release_list}
        per = {s: {"c": _counts(g), "h": _content_hash(g)} for s, g in gathered.items()}
        if fingerprint != _fingerprint(key, cur, release_list, per):
            raise ReleaseError(409, "預覽之後資料已有變動，請重新預覽再執行")
        problems: list[str] = []
        for s, g in gathered.items():
            problems += _blockers(conn, s, g, cat)[0]
        if problems:
            raise ReleaseError(409, "；".join(problems))
        machines_before = _machine_count(conn)
        keep = [s for s in cur if s not in release_list]
        audit_ids = []
        for s in release_list:
            g = gathered[s]
            snap = _make_snapshot(conn, s, g, cat)
            hw = g["hardware"]["rows"][0]
            c = conn.execute(
                "INSERT INTO merge_audit (merged_at, actor, hostname, ip, released_serial, kept_serials, snapshot, "
                "batch_id, group_key, snapshot_format, counts_json, from_cia) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), actor, hw.get("hostname"), hw.get("ip"), s, ", ".join(sorted(keep)),
                 json.dumps(snap, ensure_ascii=False, default=str), batch, key, SNAPSHOT_FORMAT,
                 json.dumps(per[s]["c"], ensure_ascii=False), 1 if is_from_cia(s) else 0))
            audit_ids.append(c.lastrowid)
            # 先處理指到這台的外鍵，再刪從屬表，最後刪 hardware
            for v in g["links"].values():
                where = " AND ".join(f"{_q(x)} = ?" for x in v["pk"])
                for kv in v["keys"]:
                    cnt = conn.execute(f"UPDATE {_q(v['table'])} SET {_q(v['column'])} = NULL WHERE {where}",
                                       [_dec(x) for x in kv]).rowcount
                    if cnt != 1:
                        raise ReleaseError(500, f"斷開 {v['table']} 的關聯時筆數不符（預期 1 筆，實際 {cnt} 筆），已全部回滾")
            for v in g["tables"].values():
                _delete_rows(conn, v["table"], v["column"], s if v["kind"] == "serial" else v["hardware_id"])
            _delete_rows(conn, "hardware", "asset_serial", s)
        # —— 交易內驗證（任何一項不符就整筆回滾）——
        for s in release_list:
            left = []
            if conn.execute("SELECT 1 FROM hardware WHERE asset_serial = ?", (s,)).fetchone():
                left.append("hardware")
            left += _orphan_tables(conn, s)
            left += [f"{x['table']}.{x['column']}" for x in _suspects(conn, s, cat)]
            if left:
                raise ReleaseError(500, f"序號 {s} 刪除後仍有殘留資料（{'、'.join(left)}），已全部回滾")
        for s in keep:
            if not conn.execute("SELECT 1 FROM hardware WHERE asset_serial = ?", (s,)).fetchone():
                raise ReleaseError(500, f"應保留的序號 {s} 不見了，已全部回滾")
        if _machine_count(conn) != machines_before:
            raise ReleaseError(500, "釋放後現役台數與釋放前不同（應該相同），已全部回滾")
        activity.log(conn, username=actor, ip=client_ip, method="POST", status=200, action="host_release", commit=False,
                     path=f"重複登記釋放序號：{'、'.join(release_list)}（保留 {'、'.join(keep)}）"[:280])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"ok": True, "released": release_list, "kept": keep, "remaining": len(keep), "batch_id": batch,
            "audit_ids": audit_ids, "rows": sum(_row_total(per[s]["c"]) for s in release_list)}


# ====================================================================== 還原
def _load_audit(conn, audit_id: int) -> tuple[sqlite3.Row, dict]:
    r = conn.execute("SELECT * FROM merge_audit WHERE id = ?", (audit_id,)).fetchone()
    if r is None:
        raise ReleaseError(404, "找不到這筆釋放紀錄")
    fmt = r["snapshot_format"] or 0
    if fmt == 2:
        raise ReleaseError(409, OLD_FORMAT_MSG)
    if fmt != SNAPSHOT_FORMAT:
        raise ReleaseError(409, "這是舊版留下的紀錄，快照只有資產主檔一列、缺人員／軟體等關聯資料，無法完整還原")
    try:
        snap = json.loads(r["snapshot"])
        ok = (isinstance(snap, dict) and snap.get("format") == SNAPSHOT_FORMAT and snap.get("serial") and isinstance(snap.get("hardware", {}).get("rows"), list)
              and isinstance(snap.get("tables"), dict) and isinstance(snap.get("links"), dict)
              and all(isinstance(v, dict) and isinstance(v.get("pk"), list) and isinstance(v.get("keys"), list)
                      and v.get("table") and v.get("column") and "hardware_id" in v for v in snap["links"].values())
              and all(isinstance(v, dict) and isinstance(v.get("rows"), list) and v.get("table") for v in snap["tables"].values()))
    except (TypeError, ValueError, AttributeError):
        raise ReleaseError(409, "快照內容損毀，無法還原")
    if not ok:
        raise ReleaseError(409, OLD_FORMAT_MSG)
    return r, snap


def _insert_row(conn, table: str, row: dict) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({_q(table)})")}
    if not cols:
        raise ReleaseError(409, f"資料表 {table} 已不存在，無法還原")
    unknown = [c for c in row if c not in cols]
    if unknown:
        raise ReleaseError(409, f"資料表 {table} 的欄位已變更（{'、'.join(unknown)}），無法還原")
    names = list(row)
    conn.execute(f"INSERT INTO {_q(table)} ({', '.join(_q(c) for c in names)}) VALUES ({', '.join('?' * len(names))})",
                 [_dec(row[c]) for c in names])


def restore(conn: sqlite3.Connection, audit_id: int, *, actor: str, client_ip: str | None = None) -> dict:
    import activity
    _ensure_audit(conn)
    conn.commit()
    _begin(conn)
    try:
        a, snap = _load_audit(conn, audit_id)
        if a["restored_at"]:
            raise ReleaseError(409, f"這筆已在 {a['restored_at']} 由 {a['restored_by'] or '—'} 還原過了")
        serial = snap["serial"]
        if conn.execute("SELECT 1 FROM hardware WHERE asset_serial = ?", (serial,)).fetchone():
            raise ReleaseError(409, f"序號 {serial} 已被現有資產使用，無法還原（請先處理佔用的那一筆）")
        n = 0
        want = len(snap["hardware"]["rows"]) + sum(len(v["rows"]) for v in snap["tables"].values())
        try:
            for row in snap["hardware"]["rows"]:
                _insert_row(conn, "hardware", row)
                n += 1
            for v in snap["tables"].values():
                for row in v["rows"]:
                    _insert_row(conn, v["table"], row)
                    n += 1
            for v in snap["links"].values():
                where = " AND ".join(f"{_q(x)} = ?" for x in v["pk"])
                for kv in v["keys"]:
                    cnt = conn.execute(
                        f"UPDATE {_q(v['table'])} SET {_q(v['column'])} = ? WHERE {where} AND {_q(v['column'])} IS NULL",
                        [v["hardware_id"]] + [_dec(x) for x in kv]).rowcount
                    if cnt != 1:
                        raise ReleaseError(409, f"還原時 {v['table']} 有一筆關聯資料已不存在或已被改過（{kv}），無法接回，整筆未動")
        except sqlite3.IntegrityError as exc:
            raise ReleaseError(409, f"還原會與現有資料衝突（{exc}），整筆未動；請先處理衝突的那筆資料")
        if n != want:
            raise ReleaseError(500, "還原筆數與快照不符，已全部回滾")
        conn.execute("UPDATE merge_audit SET restored_at = ?, restored_by = ? WHERE id = ?", (_now(), actor, audit_id))
        activity.log(conn, username=actor, ip=client_ip, method="POST", status=200, action="host_release_restore", commit=False,
                     path=f"重複登記還原序號：{serial}（紀錄 {audit_id}，{n} 筆）"[:280])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"ok": True, "serial": serial, "restored_rows": n, "audit_id": audit_id}


# ====================================================================== 釋放紀錄（可還原清單）
def list_releases(conn: sqlite3.Connection, limit: int = 500) -> dict:
    _ensure_audit(conn)
    rows = conn.execute(
        "SELECT id, merged_at, actor, hostname, ip, released_serial, kept_serials, batch_id, snapshot_format, counts_json, "
        "from_cia, restored_at, restored_by FROM merge_audit ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 5000),)).fetchall()
    items = []
    for r in rows:
        try:
            counts = json.loads(r["counts_json"]) if r["counts_json"] else {}
        except ValueError:
            counts = {}
        taken = bool(conn.execute("SELECT 1 FROM hardware WHERE asset_serial = ?", (r["released_serial"],)).fetchone())
        fmt = r["snapshot_format"] or 0
        v2 = fmt >= 2
        if r["restored_at"]:
            state, reason = "已還原", None
        elif fmt == 2:
            state, reason = "無法還原", OLD_FORMAT_MSG
        elif fmt != SNAPSHOT_FORMAT:
            state, reason = "無法還原", "舊版紀錄，快照不完整（只有資產主檔一列）"
        elif taken:
            state, reason = "序號已占用", "這個序號目前已被其他資產使用，需先處理才能還原"
        else:
            state, reason = "可還原", None
        items.append({
            "id": r["id"], "merged_at": r["merged_at"], "actor": r["actor"], "hostname": r["hostname"], "ip": r["ip"],
            "serial": r["released_serial"], "kept_serials": r["kept_serials"], "batch_id": r["batch_id"],
            "from_cia": "是" if r["from_cia"] else ("否" if v2 else "—"), "state": state, "reason": reason,
            "restorable": state == "可還原", "restored_at": r["restored_at"], "restored_by": r["restored_by"],
            "rows": _row_total(counts) if counts else None,
            "counts": counts,
        })
    return {"items": items, "restorable": sum(1 for i in items if i["restorable"])}
