"""資產生命週期的口徑與「誰可以寫資產狀態」（2026-10-20 使用者拍板）。

## 一、退役口徑全站只有這一份

「哪些資產狀態算退役」只在這裡定義。number_proof.independent() 依規定**不准 import
manage_state**（獨立重算的前提），以前只好自己再寫一份；monthly_report／asset_tags 也各寫死一份。
口徑一改（例如使用者決定「閒置」不算退役——目前**待使用者決定**，維持算退役）改這一行就全站生效。
manage_state.RETIRED_STATUS 指向這裡，既有呼叫端不用動。

## 二、資產狀態只有 CIA 匯入可以寫

使用者原話（2026-10-20）：「停用、報廢、閒置，如果是從原來的 CIA 異動，該是什麼就是什麼，
我們自己不能變成這種狀態」「我沒有權限去改成停用、報廢或閒置，至少目前階段不應該改」。

所以：
- `hardware.asset_status` **只有 CIA 清冊匯入（excel_import）寫**。
- 人知道某台下線了 → 記在「待 CIA 異動」欄位（pending_status／pending_note／pending_by／pending_at），
  畫面標徽章、可匯出給 CIA 窗口改清冊；**不改任何台數**。CIA 改好、重匯進來才是真的退役。
- 掃描空殼（DYN-）併回 CIA 正本：以前把空殼標「停用」當作「全站排除」的手段——那是系統自己
  捏造了一個退役狀態，還讓「已退役」多算一台（同一台被算兩次）。現在改成**明確的合併關係**：
  空殼整列搬到 `hardware_merged`（記 merged_into＝正本序號），從 hardware 移除，全站自然不再算它；
  還原時原樣搬回來（dyn_shell.restore）。
- 不是從 CIA 來的資產（DYN-／VC-／AUTO-／ADOPT-）：資產狀態留空（畫面顯示「CIA 未登記」）、
  環境留空（畫面顯示「不明」）。以前系統替它們填「使用中／正式／測試」，那是捏造的值，
  而且環境會影響「同一台」判定（machine_identity 的「同名但環境不同不併」）。

⚠️ 目前保留的例外（待使用者決定，2026-10-20 審查 #9）：「上線前檢查」流程——手動新增資產預設
「待上線」、通過檢查表改「使用中」（golive.py）。那兩個值不是退役狀態、只用在人工新增的機器上；
退役狀態（停用／報廢／閒置）手動新增一律拒收。

## 三、一次性資料遷移（migrate_v1）

把過去系統自己寫進去的值改回來，每一格都記在 lifecycle_migration_log（舊值／新值／原因），
可以照紀錄還原（revert_v1）。冪等：用 app_settings 記已完成，跑第二次不會再動。
"""
from __future__ import annotations

import json
from datetime import datetime

#: 資產狀態＝這幾個值 → 退役（備查：不計入任何台數、不跟現役合併）。
RETIRED_STATUS = {"停用", "報廢", "閒置"}

#: CIA 清冊「資產狀態」欄的合法值（2026-10-21）。不在這裡面的值（例：「1」「0」「A02櫃」——
#: 欄位錯位的典型樣子）匯入時**不寫入**、列進匯入警告與資料品質頁，不會被當成使用中默默算進台數。
CIA_STATUS_VALUES = {"使用中"} | RETIRED_STATUS
#: 系統裡合法存在的資產狀態：CIA 值＋上線前檢查流程（golive.py）用的「待上線」
VALID_STATUS_VALUES = CIA_STATUS_VALUES | {"待上線"}
#: 上次 CIA 匯入被擋下的資產狀態（JSON list of {asset_serial, value}），資料品質頁讀它
INVALID_STATUS_SETTING = "cia_import_invalid_status"

#: 不是 CIA 清冊來的序號前綴（帳外三種＋掃描頁「納入管理」暫用序號）
NON_CIA_PREFIXES = ("DYN-", "VC-", "AUTO-", "ADOPT-")
#: 畫面上空白資產狀態／環境的顯示字（不存進資料庫）
LABEL_NOT_IN_CIA = "CIA 未登記"
LABEL_ENV_UNKNOWN = "不明"

PENDING_COLS = ("pending_status", "pending_note", "pending_by", "pending_at")

MIGRATION_KEY = "lifecycle_v1_migrated"

MERGED_SQL = """
CREATE TABLE IF NOT EXISTS hardware_merged (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_serial TEXT NOT NULL,          -- 空殼原本的序號
    merged_into  TEXT NOT NULL,          -- 併到哪一筆（CIA 正本序號）
    merged_at    TEXT NOT NULL,
    audit_id     INTEGER,                -- shell_merge_audit.id（快照、還原都靠它）
    reason       TEXT,
    row_json     TEXT NOT NULL           -- hardware 整列原樣（還原時照搬回去，含 id）
)
"""
LOG_SQL = """
CREATE TABLE IF NOT EXISTS lifecycle_migration_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at       TEXT NOT NULL,
    asset_serial TEXT NOT NULL,
    field        TEXT NOT NULL,          -- asset_status / environment / pending_status / merged
    old_value    TEXT,
    new_value    TEXT,
    reason       TEXT,
    reverted_at  TEXT
)
"""


def retired_sql_list() -> str:
    """SQL 用的 IN 清單字面值，例：('停用','報廢','閒置')。值是上面的常數，不吃使用者輸入。"""
    return "(" + ",".join("'" + s.replace("'", "''") + "'" for s in sorted(RETIRED_STATUS)) + ")"


def is_retired_value(v) -> bool:
    return (v or "").strip() in RETIRED_STATUS


def is_from_cia(serial) -> bool:
    """這一筆是 CIA 清冊（或人手動照 CIA 欄位新增）來的嗎？帳外／暫用序號不是。"""
    s = str(serial or "")
    return bool(s) and not s.startswith(NON_CIA_PREFIXES)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure(conn) -> None:
    """建表＋補欄位（db._migrate 會叫；各函式自己也叫一次當保底，舊庫直接用也不會炸）。"""
    conn.execute(MERGED_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hardware_merged_serial ON hardware_merged(asset_serial)")
    conn.execute(LOG_SQL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
    for c in PENDING_COLS:
        if cols and c not in cols:
            conn.execute(f"ALTER TABLE hardware ADD COLUMN {c} TEXT")


def _log(conn, serial, field, old, new, reason, at) -> None:
    conn.execute(
        "INSERT INTO lifecycle_migration_log (run_at, asset_serial, field, old_value, new_value, reason) "
        "VALUES (?,?,?,?,?,?)",
        (at, serial, field, None if old is None else str(old), None if new is None else str(new), reason))


# ===== 待 CIA 異動（人知道的下線／狀態變更，不動台數）=====

def set_pending(conn, serial: str, status: str | None, note: str | None, by: str | None,
                at: str | None = None) -> bool:
    """記「待 CIA 異動」。status=None 或跟 CIA 目前的值一樣＝取消。回有沒有變。不寫 asset_status。"""
    ensure(conn)
    row = conn.execute("SELECT asset_status, pending_status FROM hardware WHERE asset_serial = ?",
                       (serial,)).fetchone()
    if row is None:
        return False
    cia = (row[0] or "").strip()
    want = (status or "").strip() or None
    if want is not None and want == cia:
        want = None                       # 要的狀態 CIA 已經是了：沒有待異動
    if (row[1] or None) == want:
        return False
    if want is None:
        conn.execute("UPDATE hardware SET pending_status = NULL, pending_note = NULL, pending_by = NULL, "
                     "pending_at = NULL WHERE asset_serial = ?", (serial,))
    else:
        conn.execute("UPDATE hardware SET pending_status = ?, pending_note = ?, pending_by = ?, pending_at = ? "
                     "WHERE asset_serial = ?", (want, note, by, at or _now(), serial))
    return True


# ===== 合併關係（掃描空殼併回 CIA 正本）=====

def _fk_refs(conn, serial: str) -> list[str]:
    """還有哪些表用外鍵指著這個序號（ci_node 是推算出來的，不算——搬走前直接清掉、之後重建）。"""
    out = []
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        if t in ("hardware", "ci_node"):
            continue
        for fk in conn.execute(f"PRAGMA foreign_key_list({t})").fetchall():
            if fk[2] == "hardware" and fk[4] == "asset_serial":
                if conn.execute(f"SELECT 1 FROM {t} WHERE {fk[3]} = ? LIMIT 1", (serial,)).fetchone():
                    out.append(t)
    return out


def archive_merged(conn, serial: str, merged_into: str, audit_id: int | None, reason: str,
                   at: str | None = None) -> tuple[bool, str]:
    """把空殼整列搬到 hardware_merged（記合併關係），從 hardware 移除。回 (成功嗎, 原因)。

    還有別的表用外鍵指著它（人員／軟體／agent 金鑰…）就不搬——那代表它不是單純的空殼，
    要人看；呼叫端照原狀保留（不會再替它捏造退役狀態）。"""
    ensure(conn)
    row = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
    if row is None:
        return False, "找不到這筆"
    refs = _fk_refs(conn, serial)
    if refs:
        return False, f"還有資料掛在這筆（{'、'.join(refs)}），不自動搬"
    # CI 圖是從 hardware 推算出來的（ci_graph.rebuild）：這台的節點與連到它的邊先清掉，之後重建。
    # ⚠️ 要先刪邊再刪點（ci_edge 外鍵指 ci_node），而且不吞錯——吞掉會讓後面刪 hardware 時
    # 撞外鍵（2026-10-20 審查抓到：幾乎每個空殼都有 located_in 邊）。
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "ci_node" in tables:
        nodes = [r[0] for r in conn.execute("SELECT node_id FROM ci_node WHERE asset_serial = ?", (serial,))]
        if nodes and "ci_edge" in tables:
            ph = ",".join("?" for _ in nodes)
            conn.execute(f"DELETE FROM ci_edge WHERE src_node_id IN ({ph}) OR dst_node_id IN ({ph})",
                         (*nodes, *nodes))
        conn.execute("DELETE FROM ci_node WHERE asset_serial = ?", (serial,))
    conn.execute(
        "INSERT INTO hardware_merged (asset_serial, merged_into, merged_at, audit_id, reason, row_json) "
        "VALUES (?,?,?,?,?,?)",
        (serial, merged_into, at or _now(), audit_id, reason,
         json.dumps(dict(row), ensure_ascii=False, default=str)))
    conn.execute("DELETE FROM hardware WHERE asset_serial = ?", (serial,))
    return True, "已搬到合併紀錄"


def unarchive_merged(conn, serial: str) -> dict | None:
    """合併還原：把空殼原樣搬回 hardware（含原本的 id，source_record 才指得回去）。沒有就回 None。"""
    ensure(conn)
    m = conn.execute("SELECT * FROM hardware_merged WHERE asset_serial = ? ORDER BY id DESC LIMIT 1",
                     (serial,)).fetchone()
    if m is None:
        return None
    row = json.loads(m["row_json"])
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
    row = {k: v for k, v in row.items() if k in cols}
    ks = list(row)
    conn.execute(f"INSERT INTO hardware ({','.join(ks)}) VALUES ({','.join('?' for _ in ks)})",
                 tuple(row[k] for k in ks))
    conn.execute("DELETE FROM hardware_merged WHERE id = ?", (m["id"],))
    return row


def merged_rows(conn) -> list[dict]:
    """目前的合併關係（每個空殼一列）。舊庫沒這張表＝沒有。"""
    try:
        return [dict(r) for r in conn.execute(
            "SELECT asset_serial, merged_into, merged_at, reason FROM hardware_merged ORDER BY id")]
    except Exception:  # noqa: BLE001 - 舊庫沒有 hardware_merged：沒有合併過
        return []


def merged_into_of(conn, serial: str) -> str | None:
    try:
        r = conn.execute("SELECT merged_into FROM hardware_merged WHERE asset_serial = ? "
                         "ORDER BY id DESC LIMIT 1", (serial,)).fetchone()
    except Exception:  # noqa: BLE001 - 舊庫沒有 hardware_merged
        return None
    return r[0] if r else None


# ===== 一次性遷移 =====

def plan_v1(conn) -> dict:
    """遷移要動哪些格子（不寫）。部署／patch 印這份給人看，verify_numbers 也拿它核對。"""
    ensure(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
    has_manual = "manual_updated_at" in cols
    rows = {r["asset_serial"]: dict(r) for r in conn.execute("SELECT * FROM hardware")}
    merged, status_fix, env_fix, pending, offbook_ret = [], [], [], [], []
    # 1) 併回時被系統標成停用的空殼 → 合併關係（原本的狀態照快照放回）
    try:
        audits = conn.execute("SELECT id, shell_serial, target_serial, snapshot FROM shell_merge_audit "
                              "WHERE restored_at IS NULL ORDER BY id").fetchall()
    except Exception:  # noqa: BLE001 - 舊庫沒有併回紀錄
        audits = []
    seen = set()
    for a in audits:
        s = a["shell_serial"]
        r = rows.get(s)
        if r is None or s in seen or not is_retired_value(r.get("asset_status")):
            continue
        seen.add(s)
        try:
            orig = (json.loads(a["snapshot"]).get("shell") or {})
        except (TypeError, ValueError):
            orig = {}
        merged.append({"asset_serial": s, "merged_into": a["target_serial"], "audit_id": a["id"],
                       "old_status": r.get("asset_status"), "orig_status": orig.get("asset_status"),
                       "orig_remark": orig.get("remark")})
    # 2) 不是 CIA 來的資產：系統填的「使用中」、VC-／AUTO- 系統填的環境 → 留空
    #    人改過（manual_updated_at 有值）的不動——那是人填的，不是系統捏造的
    for s, r in rows.items():
        if s in seen or is_from_cia(s):
            continue
        # 2b) 非 CIA 的登記卻是退役狀態：一定是系統裡標的（舊的「整台下線」連帳外一起改、或人在
        #     資產頁改的）——CIA 清冊上根本沒有這筆。改記待 CIA 異動、狀態留空（審查 #2）
        if is_retired_value(r.get("asset_status")):
            offbook_ret.append({"asset_serial": s, "old": r.get("asset_status")})
            continue
        if has_manual and r.get("manual_updated_at"):
            continue
        if (r.get("asset_status") or "").strip() == "使用中":
            status_fix.append({"asset_serial": s, "old": r.get("asset_status")})
        if s.startswith(("VC-", "AUTO-")) and (r.get("environment") or "").strip():
            env_fix.append({"asset_serial": s, "old": r.get("environment")})
    # 3) 系統裡人改過的資產狀態（CIA 待異動還沒同步的）→ 改記成待 CIA 異動，資產狀態回 CIA 的值
    try:
        ch = conn.execute("SELECT * FROM cia_pending_change WHERE field = 'asset_status' "
                          "AND synced_at IS NULL ORDER BY id").fetchall()
    except Exception:  # noqa: BLE001 - 舊庫沒有 CIA 待異動
        ch = []
    by: dict[str, list] = {}
    for c in ch:
        by.setdefault(c["asset_serial"], []).append(dict(c))
    for s, lst in by.items():
        r = rows.get(s)
        if r is None or s in seen:
            continue
        last, first = lst[-1], lst[0]
        if (r.get("asset_status") or "") != (last["new_value"] or ""):
            continue                      # 之後 CIA 重匯已經蓋過：現在的值就是 CIA 的，不動
        pending.append({"asset_serial": s, "cia_value": first["old_value"], "pending": last["new_value"],
                        "note": last.get("reason"), "by": last.get("changed_by"), "at": last.get("changed_at")})
    return {"merged": merged, "status_fix": status_fix, "env_fix": env_fix, "pending": pending,
            "offbook_retired": offbook_ret}


def migrate_v1(conn, force: bool = False) -> dict:
    """照 plan_v1 改，逐格記 lifecycle_migration_log。跑過就不再跑（force 給測試用）。"""
    ensure(conn)
    try:
        done = conn.execute("SELECT value FROM app_settings WHERE key = ?", (MIGRATION_KEY,)).fetchone()
    except Exception:  # noqa: BLE001 - 還沒有 app_settings（全新庫初始化途中）：下次再跑
        return {"skipped": "no app_settings"}
    if done and not force:
        # 值可能是「reverted:<時間>」：有人還原過就不再自動重跑（審查 #4），要重跑用 force
        return {"skipped": f"已在 {done[0]} 跑過"}
    p = plan_v1(conn)
    at = _now()
    out = {"merged": 0, "merged_blocked": [], "status_fix": 0, "env_fix": 0, "pending": 0,
           "offbook_retired": 0}
    for m in p["merged"]:
        s = m["asset_serial"]
        cur_remark = conn.execute("SELECT remark FROM hardware WHERE asset_serial = ?", (s,)).fetchone()[0]
        # 先記下現在的備註（還原時照放回），再把系統寫的「停用」放回併回前的原值（通常是空白）
        _log(conn, s, "remark", cur_remark, m["orig_remark"], "併回空殼：備註回併回前原值", at)
        conn.execute("UPDATE hardware SET asset_status = ?, remark = ? WHERE asset_serial = ?",
                     (m["orig_status"], m["orig_remark"], s))
        ok, why = archive_merged(conn, s, m["merged_into"], m["audit_id"], "遷移：併回時被標停用的空殼", at)
        if ok:
            _log(conn, s, "merged", m["old_status"], m["merged_into"], "併回空殼：停用 → 合併關係", at)
            out["merged"] += 1
        else:
            # 搬不走（有別的資料用外鍵掛著）：不再算退役（主機名＋IP 跟正本相同＝同一台，台數不會多）；
            # 合併關係寫在備註留給人看（審查 #5）
            note = f"已併入 {m['merged_into']}（合併紀錄搬不走：{why}）"
            new_remark = f"{m['orig_remark']}；{note}" if m["orig_remark"] else note
            conn.execute("UPDATE hardware SET remark = ? WHERE asset_serial = ?", (new_remark, s))
            _log(conn, s, "asset_status", m["old_status"], m["orig_status"], f"併回空殼搬不走：{why}", at)
            out["merged_blocked"].append({"asset_serial": s, "reason": why})
    for f in p["offbook_retired"]:
        s = f["asset_serial"]
        conn.execute("UPDATE hardware SET asset_status = NULL, pending_status = ?, pending_note = ?, "
                     "pending_by = ?, pending_at = ? WHERE asset_serial = ?",
                     (f["old"], "遷移：系統裡標的下線（CIA 清冊沒有這筆）", "遷移", at, s))
        _log(conn, s, "asset_status", f["old"], None, "非 CIA 登記被系統標退役 → 留空", at)
        _log(conn, s, "pending_status", None, f["old"], "非 CIA 登記被系統標退役 → 待 CIA 異動", at)
        out["offbook_retired"] += 1
    for f in p["status_fix"]:
        conn.execute("UPDATE hardware SET asset_status = NULL WHERE asset_serial = ?", (f["asset_serial"],))
        _log(conn, f["asset_serial"], "asset_status", f["old"], None, "系統替非 CIA 資產填的狀態 → 留空（CIA 未登記）", at)
        out["status_fix"] += 1
    for f in p["env_fix"]:
        conn.execute("UPDATE hardware SET environment = NULL WHERE asset_serial = ?", (f["asset_serial"],))
        _log(conn, f["asset_serial"], "environment", f["old"], None, "系統替非 CIA 資產填的環境 → 留空（不明）", at)
        out["env_fix"] += 1
    for f in p["pending"]:
        s = f["asset_serial"]
        cur = conn.execute("SELECT asset_status FROM hardware WHERE asset_serial = ?", (s,)).fetchone()[0]
        conn.execute("UPDATE hardware SET asset_status = ?, pending_status = ?, pending_note = ?, "
                     "pending_by = ?, pending_at = ? WHERE asset_serial = ?",
                     (f["cia_value"], f["pending"], f["note"], f["by"], f["at"], s))
        _log(conn, s, "asset_status", cur, f["cia_value"], "人改的狀態 → 改記待 CIA 異動，狀態回 CIA 原值", at)
        _log(conn, s, "pending_status", None, f["pending"], "人改的狀態 → 待 CIA 異動", at)
        out["pending"] += 1
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (MIGRATION_KEY, at))
    conn.commit()
    if out["merged"]:
        try:
            import ci_graph

            ci_graph.rebuild(conn)
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - CI 圖重建失敗不影響遷移本身；印出來讓部署看得到
            print(f"⚠️ 生命週期遷移後重建 CI 圖失敗：{exc}")
    return out


def revert_v1(conn) -> dict:
    """照 lifecycle_migration_log 把 migrate_v1 的每一格改回舊值（合併的搬回 hardware）。"""
    ensure(conn)
    n = 0
    at = _now()
    logs = conn.execute("SELECT * FROM lifecycle_migration_log WHERE reverted_at IS NULL "
                        "ORDER BY id DESC").fetchall()
    for g in logs:
        s, f = g["asset_serial"], g["field"]
        if f == "merged":
            row = unarchive_merged(conn, s)
            if row is not None:
                conn.execute("UPDATE hardware SET asset_status = ? WHERE asset_serial = ?", (g["old_value"], s))
        elif f in ("asset_status", "environment", "pending_status", "remark"):
            conn.execute(f"UPDATE hardware SET {f} = ? WHERE asset_serial = ?", (g["old_value"], s))
            if f == "pending_status":
                conn.execute("UPDATE hardware SET pending_note = NULL, pending_by = NULL, pending_at = NULL "
                             "WHERE asset_serial = ?", (s,))
        conn.execute("UPDATE lifecycle_migration_log SET reverted_at = ? WHERE id = ?", (at, g["id"]))
        n += 1
    # 不刪標記、改成「已還原」：不然下一次 init_db（啟動、匯入…）會立刻再遷移一次（審查 #4）
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                 (MIGRATION_KEY, f"reverted:{at}"))
    conn.commit()
    return {"reverted": n}


def _cli(argv: list[str]) -> int:
    """python lifecycle.py plan|migrate|revert（部署／patch 印給人看用）。"""
    import db

    conn = db.get_connection()
    ensure(conn)
    cmd = argv[1] if len(argv) > 1 else "plan"
    if cmd == "plan":
        p = plan_v1(conn)
        print(f"生命週期遷移（預覽）：併回空殼 停用→合併 {len(p['merged'])}／非 CIA 狀態留空 {len(p['status_fix'])}"
              f"／非 CIA 環境留空 {len(p['env_fix'])}／人改狀態→待 CIA 異動 {len(p['pending'])}"
              f"／非 CIA 標退役→待 CIA 異動 {len(p['offbook_retired'])}")
    elif cmd == "migrate":
        print(json.dumps(migrate_v1(conn), ensure_ascii=False))
    elif cmd == "changed-today":
        # 部署用：今天的遷移有沒有動到任何一格（rc 0＝有、1＝沒有）
        n = conn.execute("SELECT COUNT(*) FROM lifecycle_migration_log WHERE run_at >= ? AND reverted_at IS NULL",
                         (_now()[:10],)).fetchone()[0]
        print(f"生命週期遷移今天動到 {n} 格")
        return 0 if n else 1
    elif cmd == "revert":
        print(json.dumps(revert_v1(conn), ensure_ascii=False))
    else:
        print("用法：python lifecycle.py plan|migrate|changed-today|revert")
        return 2
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_cli(sys.argv))
