"""機櫃名稱一次性遷移（2.043.15，GPT 複核 A）：把存成「畫面櫃名／Excel 櫃名」的舊紀錄改存回實體櫃鍵（系統櫃名）。

## 為什麼要有
2.043.13 起同一個實體櫃（CIA「1-1」＝Excel「A1」）畫面上一律顯示 Excel 櫃名；2.043.14 起寫入一律存系統櫃名
（`rack_layout.stored_rack`）。但在那之前寫進去的位置更正、預留、重疊決定，有的是用畫面上看到的「A1」存的
（例：早期套用 Excel 對照時寫的位置更正）。讀取時系統把 A1 當成另一個櫃，那幾筆就跟這一櫃失聯。

## 做什麼
- 只看「還在生效」的紀錄：rack_position_override（status=active）、rack_reservation（status=active）、
  rack_overlap_decision（undone_at IS NULL）。歷史紀錄（搬移紀錄、稽核）不改，那是當時的事實。
- 一筆要改（2.043.16，GPT 第三次複核 P1：只改確定的）＝三個條件都成立：
  1. 存的櫃名是這個機房某個對照的「畫面櫃名」（例 A1）；
  2. 這個機房的 CIA 沒有真的叫這個名字的櫃（CIA 機櫃欄解析出來的櫃名，不含位置更正）——有的話它本身就是實體櫃，不動；
  3. 只有一個系統櫃名對到這個畫面櫃名——好幾個（1-1、1-0 都叫 A1）就不知道原本是哪一櫃，列「待確認」不自動改。
- 每筆改動寫一列 rack_name_migration_log（表、列 id、機房、舊值、新值、時間、誰）；UPDATE 沒真的改到一列（並行改動）就不記、不算。
- 建表、改資料、寫紀錄在同一個交易裡：中途失敗整個退回，連 log 表都不會留下。
- 可重跑：改過的那一筆已是系統櫃名，第二次跑不會再動；沒東西可改＝什麼都不做。
- 只改櫃名字串，不改 U 位、不改 CIA 資料、不增減任何一台（畫面櫃名改前改後一樣）。

## 怎麼跑
- deploy.sh `[3a3/7]` 自動跑 `python rack_name_migrate.py apply`：有東西要改才先做一份備份（backup.run_backup），
  備份失敗就不改。`preview` 只列出會改哪幾筆與待確認。`revert` 把最近一次改的退回。
- apply 另外補記 2.043.15 以前寫的對照的「生效時間」（只寫 app_settings；合櫃台數確認的 7 天寬限從這裡起算）。
- 結束碼：0＝資料沒改；5＝改了 N 筆（deploy 會重跑數字核對）；1＝備份失敗、沒改；4＝改的時候出錯、已整個退回。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import rack_layout

LOG_SQL = ("CREATE TABLE IF NOT EXISTS rack_name_migration_log ("
           " id INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT NOT NULL, row_id TEXT NOT NULL, room TEXT,"
           " old_rack TEXT NOT NULL, new_rack TEXT NOT NULL, run_by TEXT, run_at TEXT NOT NULL)")

# (表, 主鍵欄, 生效條件)
TARGETS = [
    ("rack_position_override", "key", "status = 'active'"),
    ("rack_reservation", "id", "status = 'active'"),
    ("rack_overlap_decision", "id", "undone_at IS NULL"),
]


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _cia_racks(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """CIA 機櫃欄解析出來的（機房, 櫃名）——真正存在的實體櫃名，不含位置更正。"""
    import blast_radius
    out = set()
    for loc, raw in conn.execute("SELECT physical_location, rack_no FROM hardware WHERE rack_no IS NOT NULL AND rack_no != ''"):
        pr = rack_layout.parse_rack(raw)
        if pr["kind"] == "rack" and pr["rack"]:
            out.add((blast_radius.rack_room(loc), pr["rack"]))
    return out


def scan(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """回 (要改的, 待確認的)。各是 [{tbl, row_id, room, old_rack, new_rack|candidates, why}]。不寫任何東西。"""
    stored = rack_layout._setting_json(conn, rack_layout.RACK_NAME_MAP_SETTING)
    if not isinstance(stored, dict) or not stored:
        return [], []
    names = rack_layout.rack_name_map(conn)
    real = _cia_racks(conn)
    plan, pending = [], []
    for tbl, pk, cond in TARGETS:
        if not _has_table(conn, tbl):
            continue
        for row_id, room, rack in conn.execute(f"SELECT {pk}, room, rack FROM {tbl} WHERE {cond}").fetchall():
            if not rack or (room, rack) in real:
                continue                     # CIA 真的有這個櫃名＝它本身就是實體櫃
            src = sorted((k.split("/", 1)[1] for k, v in names.items() if k.split("/", 1)[0] == room and v == rack),
                         key=rack_layout._natural)
            if not src:
                continue                     # 不是任何對照的畫面櫃名：不在這次遷移的範圍
            item = {"tbl": tbl, "row_id": str(row_id), "room": room, "old_rack": rack}
            if len(src) == 1:
                plan.append({**item, "new_rack": src[0]})
            else:
                pending.append({**item, "candidates": src,
                                "why": f"{len(src)} 個系統櫃名（{'、'.join(src)}）都叫 {rack}，不知道原本是哪一櫃"})
    return plan, pending


def preview(conn: sqlite3.Connection) -> list[dict]:
    """會改哪幾筆：[{tbl, row_id, room, old_rack, new_rack}]。不寫任何東西。待確認的看 scan()。"""
    return scan(conn)[0]


def apply(conn: sqlite3.Connection, by: str = "system") -> list[dict]:
    """照 preview 改，一個交易（含建 log 表）；每筆寫 rack_name_migration_log。回真的改了哪幾筆。"""
    plan = preview(conn)
    if not plan:
        return []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pks = {t: pk for t, pk, _ in TARGETS}
    done = []
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN")
    try:
        conn.execute(LOG_SQL)                # DDL 也在交易裡：失敗退回時不留下空表
        for x in plan:
            cur = conn.execute(f"UPDATE {x['tbl']} SET rack = ? WHERE {pks[x['tbl']]} = ? AND rack = ?",
                               (x["new_rack"], x["row_id"], x["old_rack"]))
            if cur.rowcount != 1:
                continue                     # 並行改掉了：不記、不算
            conn.execute("INSERT INTO rack_name_migration_log (tbl, row_id, room, old_rack, new_rack, run_by, run_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (x["tbl"], x["row_id"], x["room"], x["old_rack"], x["new_rack"], by, now))
            done.append(x)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return done


def revert(conn: sqlite3.Connection, by: str = "system") -> int:
    """把最近一次遷移改的退回原值（只退「現在還是遷移後的值」的那幾筆）。回退了幾筆。"""
    if not _has_table(conn, "rack_name_migration_log"):
        return 0
    last = conn.execute("SELECT MAX(run_at) FROM rack_name_migration_log WHERE run_by NOT LIKE 'revert:%'").fetchone()[0]
    if not last:
        return 0
    pks = {t: pk for t, pk, _ in TARGETS}
    n = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        for r in conn.execute("SELECT tbl, row_id, room, old_rack, new_rack FROM rack_name_migration_log "
                              "WHERE run_at = ? AND run_by NOT LIKE 'revert:%'", (last,)).fetchall():
            cur = conn.execute(f"UPDATE {r[0]} SET rack = ? WHERE {pks[r[0]]} = ? AND rack = ?", (r[3], r[1], r[4]))
            if cur.rowcount == 1:
                conn.execute("INSERT INTO rack_name_migration_log (tbl, row_id, room, old_rack, new_rack, run_by, run_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)", (r[0], r[1], r[2], r[4], r[3], f"revert:{by}", now))
                n += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return n


def _cli(argv: list[str]) -> int:
    """`python rack_name_migrate.py preview|apply|revert`。結束碼見模組說明（0 沒改／5 改了／1 備份失敗沒改／4 出錯已退回）。"""
    import os
    from pathlib import Path

    import backup
    import db

    if not argv or argv[0] not in ("preview", "apply", "revert"):
        print("用法：python rack_name_migrate.py preview|apply|revert")
        return 2
    p = os.environ.get("ASSET_DB_PATH")
    db_path = Path(p) if p else db.get_db_path()
    conn = db.get_connection(db_path)
    try:
        if argv[0] == "revert":
            print(f"機櫃名稱遷移退回：{revert(conn, 'cli')} 筆")
            return 0
        plan, pending = scan(conn)
        print(f"機櫃名稱遷移：要改 {len(plan)} 筆（存成畫面櫃名的紀錄改存系統櫃名）、待確認 {len(pending)} 筆（不自動改）")
        for x in plan[:50]:
            print(f"   - {x['tbl']}#{x['row_id']}：{x['room']} {x['old_rack']} → {x['new_rack']}")
        for x in pending[:50]:
            print(f"   ? {x['tbl']}#{x['row_id']}：{x['room']} {x['old_rack']}——{x['why']}")
        if argv[0] == "preview":
            return 0
        n_since = rack_layout.backfill_name_map_since(conn)
        conn.commit()
        if n_since:
            print(f"   補記 {n_since} 筆對照的生效時間（只寫系統設定，不動任何一台；合櫃台數確認的 7 天寬限從今天起算）")
        if not plan:
            print("   資料沒有改（沒有要改的紀錄）")
            return 0
        res = backup.run_backup(db_path, backup.get_backup_dir(db_path), datetime.now())
        if not res.ok:
            print(f"!! 改之前的備份失敗，資料沒有改：{res.error}")
            return 1
        print(f"   改之前已備份：{res.path}")
        try:
            done = apply(conn, "deploy")
        except Exception as exc:  # noqa: BLE001 - 交易已整個退回（含 log 表），講清楚後讓 deploy 決定
            print(f"!! 改的時候出錯，已整個退回（資料、log 表都沒動）：{type(exc).__name__}: {exc}")
            return 4
        print(f"   ✓ 已改 {len(done)} 筆（紀錄在 rack_name_migration_log；退回：python rack_name_migrate.py revert）")
        print(json.dumps({"changed": len(done), "pending": len(pending)}, ensure_ascii=False))
        return 5 if done else 0
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    sys.exit(_cli(sys.argv[1:]))
