"""S19 VC 採集器：把 RVTools 匯出的 vCenter 盤點吃進資產清單。

## 為什麼走 RVTools 而不是先接 vCenter API

RVTools 是業界收 vCenter 盤點最常見的工具：連上 vCenter 按一下就匯出一份 Excel，
`vInfo` 分頁一列一台 VM，欄位版本間幾乎沒變（公開且穩定）。而我們系統本來就吃 Excel，
所以「先用 RVTools 拿到真實盤點」是最短路徑。等這條走順，再往「直接連 vCenter API、
每晚自己收」推（那才是不用人點的版本）——同一套身分解析與寫入，不會白做。

## 三條原則（沿用既有地基）

1. **不亂合併**：每一列都走 identity.resolve（強識別碼 vm_uuid 相符才定案）。判不準的
   一律進 merge_review 交人工，**寧留兩筆也不自動合併錯**（合併錯不噴錯、很難救）。
2. **只覆蓋機器事實、不碰人填的業務欄位**：vCenter 對 VM 的主機名/IP/OS/UUID 是權威，
   但資產用途、保管者那些是人維護的，收集不覆蓋（沿用 manage_state 的 FACT_FIELDS 精神）。
3. **每一列留來源紀錄**（source_record，source='vcenter'）：判錯時能回溯是哪一步、哪條規則。

## VM 是真實資料，不是假資料

RVTools 匯出的是 vCenter 裡真的 VM——這正是我們要盤的東西，跟「demo 假資料」是兩回事。
新 VM 會建成資產，序號用 `VC-<vm_uuid>` 前綴標明來源（像納管的 AUTO-），一眼看得出這台
是從 vCenter 收進來的、還沒對到公司資產序號。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import openpyxl

VINFO_SHEET_CANDIDATES = ("vInfo", "vinfo", "tabvInfo")

# RVTools vInfo 標準表頭 → 我們的欄位。用「正規化後比對」容忍大小寫/空白/版本差異。
# 同一個目標欄位可有多個來源候選，依序取第一個「有值」的（真值優先於次選）。
# 這些是 RVTools 各版本都在的欄位，刻意只挑「盤點真的需要」的，不貪多。
_COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    # 最強識別碼：VM 的 BIOS/SMBIOS UUID，換 IP 換名都不變（identity.py 的最強階）。
    # ⚠️ 不可用 "VI SDK UUID"——那是 vCenter 伺服器的 UUID，不是這台 VM 的。
    "vm_uuid": ("VM UUID", "SMBIOS UUID", "BIOS UUID"),
    # 主機名：優先 guest 的 DNS Name（真主機名），退回 VM 顯示名稱
    "hostname": ("DNS Name", "VM"),
    # IP：優先 Primary IP Address，退回 IP Address
    "ip": ("Primary IP Address", "IP Address"),
    # OS：優先 VMware Tools 回報的（實際跑的），退回設定檔宣告的
    "os": ("OS according to the VMware Tools", "OS according to the configuration file",
           "OS according to the VMware tools"),
    # VM 顯示名稱（進 asset_name，也當 hostname 的退路）
    "vm_name": ("VM",),
    # 這台 VM 跑在哪台實體 ESXi 主機上（進 remark，沒有對應欄位）
    "esxi_host": ("Host",),
    # 開機狀態（進 remark；不映射到 asset_status——那是業務生命週期、人維護的另一條軸）
    "powerstate": ("Powerstate", "Power State", "powerstate"),
    # vCenter 內部 moref（vm-123），當 source_key 退路（vm_uuid 缺時）
    "moref": ("VM ID", "MoRef", "Object ID"),
}

# 只覆蓋這些「機器事實」欄位，不碰人填的業務欄位（用途/保管者/環境/資產狀態…）。
FACT_FIELDS = ("hostname", "ip", "os", "vm_uuid", "is_vm")


def _norm(header: Any) -> str:
    """表頭比對用正規化：全形轉半形、collapse 空白、小寫。只用於比對，不改原值。"""
    if not isinstance(header, str):
        return ""
    text = header.replace("　", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def _find_vinfo_sheet(wb) -> Any:
    for name in wb.sheetnames:
        if _norm(name) in {_norm(c) for c in VINFO_SHEET_CANDIDATES}:
            return wb[name]
    # 有些匯出把 VM 清單放在唯一/第一個分頁——退而求其次用第一個含 "VM UUID" 表頭的分頁
    for ws in wb.worksheets:
        header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
        norms = {_norm(h) for h in header}
        if _norm("VM UUID") in norms or _norm("VM") in norms:
            return ws
    return None


def parse_rvtools(xlsx_path: Path) -> list[dict[str, Any]]:
    """讀 RVTools 匯出的 vInfo 分頁，一列 VM 轉一筆記錄。

    回傳的每筆含：vm_uuid/hostname/ip/os/is_vm，以及供 remark 與 source_key 用的
    vm_name/esxi_host/powerstate/moref。欄位用標題文字比對，不靠固定欄位位置——
    RVTools 換版本或欄位順序不會壞。
    """
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    try:
        ws = _find_vinfo_sheet(wb)
        if ws is None:
            raise ValueError("找不到 RVTools 的 vInfo 分頁（也沒有含 VM/VM UUID 表頭的分頁）")

        header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
        # 正規化表頭 → 欄索引
        norm_to_idx: dict[str, int] = {}
        for idx, h in enumerate(header_row):
            n = _norm(h)
            if n:
                norm_to_idx.setdefault(n, idx)

        # 目標欄位 → 所有存在的候選欄索引（依候選順序）。
        # ⚠️ 逐「列」取值而非只選一欄：RVTools 可能同時有 Primary IP Address 與 IP Address
        # 兩欄，但某台 VM 的 Primary 為空、IP Address 才有值——只鎖第一個存在的欄會漏掉。
        field_indices: dict[str, list[int]] = {}
        for field, candidates in _COLUMN_CANDIDATES.items():
            idxs = [norm_to_idx[_norm(c)] for c in candidates if _norm(c) in norm_to_idx]
            if idxs:
                field_indices[field] = idxs

        records = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not any(v not in (None, "") for v in row):
                continue  # 全空列跳過

            def get(field: str):
                for idx in field_indices.get(field, ()):
                    if idx >= len(row):
                        continue
                    v = row[idx]
                    if isinstance(v, str):
                        v = v.strip()
                    if v not in (None, ""):
                        return v      # 候選欄裡第一個有值的
                return None

            hostname = get("hostname")
            rec = {
                "vm_uuid": get("vm_uuid"),
                "hostname": hostname,
                "ip": get("ip"),
                "os": get("os"),
                "is_vm": 1,
                "vm_name": get("vm_name"),
                "esxi_host": get("esxi_host"),
                "powerstate": get("powerstate"),
                "moref": get("moref"),
            }
            # 整列連個名字都沒有就不算一台
            if not (rec["vm_uuid"] or rec["hostname"] or rec["vm_name"]):
                continue
            records.append(rec)
        return records
    finally:
        wb.close()


def _make_remark(rec: dict) -> str | None:
    """把沒有對應欄位、但盤點有用的資訊收進 remark（實體主機、開機狀態）。"""
    bits = []
    if rec.get("esxi_host"):
        bits.append(f"vHost={rec['esxi_host']}")
    if rec.get("powerstate"):
        bits.append(f"powerState={rec['powerstate']}")
    bits.append("來源=vCenter/RVTools")
    return "；".join(bits)


def _synth_serial(rec: dict) -> str:
    """新 VM 的資產序號：VC- 前綴標明來源（像納管的 AUTO-）。

    優先用 vm_uuid（穩定、換 IP 換名都不變），沒有才退到顯示名稱——退路會比較弱，
    但至少能建起來讓人看到、之後補真序號。"""
    base = rec.get("vm_uuid") or rec.get("moref") or rec.get("vm_name") or rec.get("hostname")
    return f"VC-{base}"


def _stage_source_record(conn: sqlite3.Connection, rec: dict, match) -> int:
    """留一筆來源紀錄（source='vcenter'）。判錯時能回溯是哪一步、哪條規則。

    source_key 用 vm_uuid（沒有退到 moref／顯示名），UNIQUE(source, source_key) 讓
    同一台 VM 重複匯入是更新同一筆、不會累積。"""
    source_key = rec.get("vm_uuid") or rec.get("moref") or rec.get("vm_name") or rec.get("hostname")
    payload = json.dumps(rec, ensure_ascii=False)
    cur = conn.execute(
        "INSERT INTO source_record (source, source_key, payload, resolved_status, "
        "resolved_hardware_id, resolved_rule, resolved_confidence) VALUES ('vcenter',?,?,?,?,?,?) "
        "ON CONFLICT(source, source_key) DO UPDATE SET payload=excluded.payload, "
        "resolved_status=excluded.resolved_status, resolved_hardware_id=excluded.resolved_hardware_id, "
        "resolved_rule=excluded.resolved_rule, resolved_confidence=excluded.resolved_confidence, "
        "collected_at=datetime('now','localtime')",
        (source_key, payload, match.status, match.hardware_id, match.rule, match.confidence),
    )
    # ON CONFLICT 更新時 lastrowid 不可靠，回查一次拿 id
    row = conn.execute(
        "SELECT id FROM source_record WHERE source='vcenter' AND source_key=?", (source_key,)
    ).fetchone()
    return row["id"] if row else cur.lastrowid


def import_rvtools(xlsx_path: Path, conn: sqlite3.Connection) -> dict[str, Any]:
    """把一份 RVTools 匯出吃進資產清單。回統計＋逐項結果。

    每列走 identity.resolve：
      matched   → 更新既有資產的機器事實欄位（不碰業務欄位）
      new       → 建一筆 VC- 前綴的新資產（is_vm=1）
      ambiguous → 進 merge_review 交人工，**不寫 hardware**（不自動合併錯）
    """
    import identity
    from db import _now_local

    records = parse_rvtools(xlsx_path)
    inserted = updated = pending = 0
    errors: list[str] = []

    for rec in records:
        try:
            match = identity.resolve(conn, rec)
            sr_id = _stage_source_record(conn, rec, match)

            if match.status == identity.MATCHED:
                existing = conn.execute(
                    "SELECT asset_serial FROM hardware WHERE id = ?", (match.hardware_id,)
                ).fetchone()
                if existing is None:
                    errors.append(f"{rec.get('vm_name') or rec.get('hostname')}：對到的資產已不存在，略過")
                    continue
                setters = {k: rec[k] for k in FACT_FIELDS if rec.get(k) not in (None, "")}
                if setters:
                    assigns = ", ".join(f"{k} = ?" for k in setters)
                    conn.execute(
                        f"UPDATE hardware SET {assigns}, updated_at = ? WHERE id = ?",
                        (*setters.values(), _now_local(), match.hardware_id),
                    )
                updated += 1

            elif match.status == identity.NEW:
                serial = _synth_serial(rec)
                # 序號可能已存在（同一份重匯、或退路序號撞名）→ 當更新處理，不硬塞出 IntegrityError
                exists = conn.execute(
                    "SELECT id FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
                fields = {k: rec[k] for k in FACT_FIELDS if rec.get(k) not in (None, "")}
                if exists:
                    if fields:
                        assigns = ", ".join(f"{k} = ?" for k in fields)
                        conn.execute(
                            f"UPDATE hardware SET {assigns}, updated_at = ? WHERE id = ?",
                            (*fields.values(), _now_local(), exists["id"]),
                        )
                    updated += 1
                    continue
                fields.update({
                    "asset_serial": serial,
                    "asset_name": rec.get("vm_name"),
                    "is_vm": 1,
                    "environment": "正式",       # 預設，人可改；不猜業務欄位
                    "remark": _make_remark(rec),
                })
                cols = ", ".join(fields.keys())
                ph = ", ".join("?" for _ in fields)
                cur = conn.execute(
                    f"INSERT INTO hardware ({cols}) VALUES ({ph})", tuple(fields.values()))
                conn.execute(
                    "UPDATE source_record SET resolved_hardware_id = ? WHERE id = ?",
                    (cur.lastrowid, sr_id))
                inserted += 1

            else:  # AMBIGUOUS —— 不自動合併，進人工佇列
                conn.execute(
                    "INSERT INTO merge_review (source_record_id, reason, candidates, status) "
                    "VALUES (?,?,?, 'open')",
                    (sr_id, match.reason, json.dumps(match.candidates, ensure_ascii=False)))
                pending += 1

        except sqlite3.IntegrityError as exc:
            errors.append(f"{rec.get('vm_name') or rec.get('hostname')}：寫入失敗（{exc}）")

    conn.commit()
    return {
        "source": "vcenter/rvtools",
        "total_vms": len(records),
        "inserted": inserted,
        "updated": updated,
        "pending_review": pending,   # 判不準、等人工決定的
        "errors": errors,
    }


if __name__ == "__main__":
    import sys

    from db import get_connection, init_db

    if len(sys.argv) < 2:
        print("用法：python rvtools_import.py <RVTools匯出.xlsx>")
        raise SystemExit(2)

    init_db()
    connection = get_connection()
    try:
        result = import_rvtools(Path(sys.argv[1]), connection)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        connection.close()
