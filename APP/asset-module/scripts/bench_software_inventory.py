#!/usr/bin/env python3
"""7.3 軟體盤點各 API 的耗時（2.043.9，GPT 審查 6）——唯讀、只印秒數與筆數，不寫任何東西。

用法（在 221 用正式資料跑；要裝了 2.043.9 以上的程式）：
    cd /opt/webit3/src/APP/asset-module/backend
    python3 ../scripts/bench_software_inventory.py [asset.db 路徑]
    預設路徑：環境變數 ASSET_DB_PATH，否則 /opt/webit3/data/asset.db

量的是跟 API 一樣的呼叫組合：
  /api/software/packages   ＝ _live_rows 讀一次 → list_software＋software_summary 共用
  /api/software/by-machine ＝ machines_overview
  /api/software/hosts      ＝ software_hosts（台數最多的那個軟體）
另外印「沒共用 live、各算各的」的時間給對照（2.043.9 之前的寫法）。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))


def _open(path: str) -> sqlite3.Connection:
    # 唯讀開檔：mode=ro，不會改到正式資料
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _t(label: str, fn):
    t0 = time.perf_counter()
    out = fn()
    dt = time.perf_counter() - t0
    n = len(out) if hasattr(out, "__len__") else "-"
    print(f"{label:<48} {dt:7.2f} 秒   （{n} 筆）")
    return out


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ASSET_DB_PATH", "/opt/webit3/data/asset.db")
    if not Path(path).exists():
        print(f"找不到 {path}")
        return 2
    conn = _open(path)
    import machine_identity
    import software_inventory as si

    machine_identity.refresh(conn)      # 口徑開關（machine_key_mode）跟正式服務一樣從 app_settings 讀
    n_rows = conn.execute("SELECT COUNT(*) FROM host_package WHERE gone_at IS NULL").fetchone()[0]
    print(f"資料庫：{path}\n現行套件列：{n_rows:,}　口徑：{machine_identity.mode()}\n")

    t0 = time.perf_counter()
    live = _t("_live_rows（讀一次表＋machine_key）", lambda: si._live_rows(conn))
    _t("list_software（共用 live）", lambda: si.list_software(conn, live=live))
    _t("software_summary（共用 live）", lambda: si.software_summary(conn, live=live))
    print(f"{'＝ /api/software/packages 合計':<48} {time.perf_counter() - t0:7.2f} 秒\n")

    _t("對照：list_software（自己讀）", lambda: si.list_software(conn))
    _t("對照：software_summary（自己讀）", lambda: si.software_summary(conn))
    _t("/api/software/by-machine（machines_overview）", lambda: si.machines_overview(conn))
    ls = si.list_software(conn, live=live)
    if ls:
        top = max(ls, key=lambda r: r["host_count"])
        _t(f"/api/software/hosts（{top['name']}，{top['host_count']} 台）", lambda: si.software_hosts(conn, top["name"]))
        _t(f"/api/software/versions（{top['name']}）", lambda: si.software_versions(conn, top["name"]))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
