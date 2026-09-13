#!/usr/bin/env python3
"""清掉測試期間累積的業務資料，把系統回到「乾淨可正式使用」的狀態。

會清的是「跑出來的資料」：資產、掃描紀錄、比對結果、帳號／服務盤點、稽核發現。
不會碰「設定與帳號」：登入帳號、系統設定、功能開關、連線與收集憑證、授權網段、別名字典。
—— 清完你還是用原本的 admin 密碼登入，排程與憑證都還在，不必重設。

用法：
    python clean_data.py --dry-run      # 只列出會刪什麼，完全不動資料（建議先跑這個）
    python clean_data.py                # 實際清除，會先備份、並要你打字確認
    python clean_data.py --yes          # 不互動確認（給腳本用，仍然會備份）
    python clean_data.py --keep-topology  # 連戰情室系統圖(systems/system_deps)一起保留

實際執行時：
    cd /opt/webit3/app/backend
    sudo -u sysinfra ASSET_DB_PATH=/opt/webit3/data/asset.db \
        /opt/webit3/venv/bin/python clean_data.py --dry-run

刪除前一定會先備份（VACUUM INTO，依專案決策 T3），備份路徑會印在畫面上；
真的刪錯了，把備份檔複製回原位就還原了。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

# 「跑出來的資料」——測試期間會被灌髒的就是這些。
BUSINESS_TABLES = [
    # 資產本體與來源
    "hardware", "software", "personnel", "source_record",
    # 掃描與比對
    "scan_history", "scan_runs", "comparison_result",
    # 帳號盤點
    "host_account", "account_finding", "account_collect_runs", "finding_disposition",
    # 服務盤點
    "host_service", "service_collect_runs",
    # 作業紀錄
    "import_log", "merge_review", "onboard_audit", "credential_use_audit",
    # 戰情室系統圖（人工維護，但測試期間多半也是亂填的）
    "systems", "system_deps",
]

# 保留這些的理由，會印給使用者看，避免「以為被清掉了」而重設一輪。
KEEP_TABLES = {
    "users": "登入帳號（清掉就進不去了）",
    "sessions": "登入狀態",
    "app_settings": "系統設定（排程頻率、開關等）",
    "feature_flags": "功能開關",
    "connections": "連線設定（含加密後的密碼）",
    "collect_credential": "收集用憑證（含加密後的密碼）",
    "auto_onboard_segment": "自動納管的授權網段",
    "normalize_alias": "人工維護的別名字典",
}

# --keep-topology 時，這兩張改為保留
TOPOLOGY_TABLES = {"systems", "system_deps"}


def existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.Error:
        return -1


def main() -> None:
    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args
    assume_yes = "--yes" in args
    keep_topology = "--keep-topology" in args

    db_path = Path(os.environ.get("ASSET_DB_PATH", "/opt/webit3/data/asset.db"))
    if not db_path.exists():
        print(f"!! 找不到資料庫：{db_path}")
        print("   用 ASSET_DB_PATH 指定，或確認這台是否真的裝好了。")
        raise SystemExit(1)

    conn = sqlite3.connect(db_path)
    have = existing_tables(conn)

    targets = [t for t in BUSINESS_TABLES if t in have]
    if keep_topology:
        targets = [t for t in targets if t not in TOPOLOGY_TABLES]

    print(f"資料庫：{db_path}")
    print(f"大小　：{db_path.stat().st_size / 1024:.0f} KB\n")

    print("=== 會清空（業務資料）===")
    total = 0
    for t in targets:
        n = count(conn, t)
        total += max(n, 0)
        mark = "" if n else "   （本來就是空的）"
        print(f"  {t:26} {n:>7}{mark}")
    print(f"  {'合計':26} {total:>7} 筆")

    print("\n=== 保留（設定與帳號，完全不動）===")
    for t, why in KEEP_TABLES.items():
        if t in have:
            print(f"  {t:26} {count(conn, t):>7}   {why}")
    if keep_topology:
        for t in sorted(TOPOLOGY_TABLES & have):
            print(f"  {t:26} {count(conn, t):>7}   （--keep-topology 指定保留）")

    # 提醒沒被分類到的表，免得預期外的資料被留下或被清掉
    unknown = have - set(BUSINESS_TABLES) - set(KEEP_TABLES)
    if unknown:
        print("\n=== 未分類（保守起見不動它）===")
        for t in sorted(unknown):
            print(f"  {t:26} {count(conn, t):>7}")

    if total == 0:
        print("\n沒有東西要清，資料庫已經是乾淨的。")
        conn.close()
        return

    if dry_run:
        print("\n--dry-run：以上只是預覽，沒有動任何資料。")
        print("確認無誤後把 --dry-run 拿掉再跑一次。")
        conn.close()
        return

    # 先算好備份路徑但先不建立：確認之後才真的備份，這樣取消時不會留下垃圾檔。
    # 檔名帶到秒，取消後馬上重跑會撞名（VACUUM INTO 拒絕覆蓋既有檔案），所以往後挪。
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.parent / f"asset_before_clean_{stamp}.db"
    seq = 2
    while backup.exists():
        backup = db_path.parent / f"asset_before_clean_{stamp}_{seq}.db"
        seq += 1

    # ===== 確認 =====
    if not assume_yes:
        print(f"\n即將永久刪除 {total} 筆業務資料。帳號與設定不受影響。")
        print(f"動手前會先備份到：{backup}")
        try:
            ans = input("確定要清除請輸入大寫 CLEAN，其他任何輸入都會取消： ").strip()
        except EOFError:
            print("\n!! 非互動環境卻沒有給 --yes，為安全起見中止。")
            raise SystemExit(1)
        if ans != "CLEAN":
            print("已取消，沒有刪除任何資料。")
            conn.close()
            return

    # ===== 備份（依決策 T3：VACUUM INTO，完成後做 integrity_check）=====
    print(f"\n[1/4] 備份 → {backup}")
    conn.execute("VACUUM INTO ?", (str(backup),))
    chk = sqlite3.connect(backup).execute("PRAGMA integrity_check").fetchone()[0]
    if chk != "ok":
        print(f"!! 備份檔完整性檢查沒過（{chk}），為安全起見中止，沒有刪除任何資料。")
        raise SystemExit(1)
    print(f"  ✓ 備份完成（{backup.stat().st_size / 1024:.0f} KB，integrity_check: ok）")

    # ===== 清除 =====
    print("\n[2/4] 清除業務資料")
    deleted = 0
    for t in targets:
        n = count(conn, t)
        if n > 0:
            conn.execute(f"DELETE FROM {t}")
            deleted += n
            print(f"  ✓ {t:26} 刪除 {n} 筆")
    conn.commit()

    # AUTOINCREMENT 的計數器歸零，之後新資料的 id 從 1 開始，看起來才像新系統
    if "sqlite_sequence" in have:
        qs = ",".join("?" * len(targets))
        conn.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({qs})", targets)
        conn.commit()

    print("\n[3/4] 回收空間（VACUUM）")
    conn.execute("VACUUM")
    conn.commit()

    print("[4/4] 完整性檢查")
    chk = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"  integrity_check: {chk}")
    conn.close()

    size = db_path.stat().st_size / 1024
    print("\n" + "=" * 52)
    print(f"✅ 已清除 {deleted} 筆業務資料，資料庫縮到 {size:.0f} KB")
    print(f"   備份留在： {backup}")
    print("   帳號與設定沒有變動，用原本的密碼登入即可。")
    print("\n   要還原成清除前的狀態：")
    print(f"     sudo systemctl stop webit3-api")
    print(f"     sudo -u $(stat -c %U {db_path}) cp -p {backup} {db_path}")
    print(f"     sudo systemctl start webit3-api")
    print("=" * 52)


if __name__ == "__main__":
    main()
