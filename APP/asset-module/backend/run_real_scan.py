"""真實掃描 job：讀 connections 表的網段目標 → 跑 NetworkSweepSource → 寫 scan_history。

跟 backup.py / cleanup.py 同性質——可排程/CLI 觸發的獨立 job，不是 API 端點。
在 .221 上以服務帳號(sysctl)跑；TCP sweep 免金鑰免 root。

掃描目標來源：connections 表裡 target 是 CIDR（含 "/"）的那幾筆，
或 connection_type 標成「網路掃描」。都沒有時 fallback 掃本機所在 /24。
用法： ASSET_DB_PATH=/opt/webit3/data/asset.db python run_real_scan.py
"""
from __future__ import annotations

import socket

from db import get_connection, init_db, list_connections
from net_scan import NetworkSweepSource
from scanner import run_scan


def _local_subnet() -> str:
    """推本機所在 /24（fallback 用）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    octets = ip.split(".")
    return ".".join(octets[:3]) + ".0/24"


def _is_cidr(target: str) -> bool:
    """target 真的是網段才算數。

    ⚠️ 原本判斷是「target 含有 '/'」——但 CMDB Gateway 的 target 是
    `http://10.93.18.35:3001`，裡面也有 '/'，於是一個 HTTP 網址被當成 CIDR
    餵給網段掃描器，每次掃描都失敗、還被算進「掃描不完整」，把真正的掃描問題淹掉。
    改成實際解析，解得開才是網段。
    """
    import ipaddress

    try:
        ipaddress.ip_network(target, strict=False)
        return True
    except ValueError:
        return False


def scan_targets(conn) -> list[NetworkSweepSource]:
    sources: list[NetworkSweepSource] = []
    for c in list_connections(conn):
        target = (c["target"] or "").strip()
        ctype = (c["connection_type"] or "")
        # 停用的來源直接跳過：使用者刻意關掉的東西不該被掃、更不該被算成「掃描失敗」
        # （既有 DB 可能還沒有這個欄位，取不到就當啟用）
        try:
            if c["enabled"] is not None and not c["enabled"]:
                continue
        except (IndexError, KeyError):
            pass
        if _is_cidr(target) or "網路掃描" in ctype:
            sources.append(NetworkSweepSource(target, name=c["name"] or f"網段掃描 {target}"))
    if not sources:
        sub = _local_subnet()
        sources.append(NetworkSweepSource(sub, name=f"本機網段(fallback) {sub}"))
    return sources


def main() -> dict:
    init_db()
    conn = get_connection()
    try:
        sources = scan_targets(conn)
        summary = run_scan(sources, conn)
        print(
            f"真實掃描完成：找到 {summary['total_found']} 台活著的主機；"
            f"來源 {[s.name for s in sources]}"
        )
        if summary["failed_segments"]:
            print(f"失敗網段：{summary['failed_segments']}")
        return summary
    finally:
        conn.close()


if __name__ == "__main__":
    main()
