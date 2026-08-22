"""網段配置表：匯入、查詢、IP 配置輔助。

來源是公司的「總分公司網段配置表」Excel（2026-08-15 使用者提供，183 段）。
欄位：使用狀況／使用位置／用途說明／使用類別／使用目的／網段／弱掃說明。

匯入的立場是**寬鬆解析、明確回報**：真實檔案裡本來就有解析不掉的寫法
（一格塞兩段、寫成 IP 範圍），這種列不能靜默丟掉——丟掉的網段之後不會有人發現，
而「系統裡沒有這段」跟「這段不存在」在盤點上是完全不同的兩件事。
所以：解析得出來的存 cidr，解析不出來的照樣入庫（cidr=NULL）並列進匯入警告。
"""
from __future__ import annotations

import ipaddress
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

# Excel 表頭 → 欄位。用「包含」比對，不是逐字相等：表頭常有全形括號、尾端空白、
# 或多一個「（本欄位…）」註記，逐字比對會整批匯不進來（同 excel_import 踩過的坑）。
HEADER_MAP = {
    "使用狀況": "usage_status",
    "使用位置": "location",
    "用途說明": "purpose_desc",
    "使用類別": "category",
    "使用目的": "usage",
    "網段": "raw_cidr",
    "弱掃說明": "scan_note",
}
REQUIRED = ("raw_cidr",)

# 「建議排除掃描」是資安人員寫在弱掃說明裡的判斷（員工電腦、UAT、重複 IP 網段）。
# 這是主動掃描要不要打進去的唯一依據，不能靠我們自己猜。
_EXCLUDE_RE = re.compile(r"排除掃描|勿掃|不要掃|不得掃")


def _norm_header(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).replace("（", "(").replace("）", ")")


def _derive_environment(category: str) -> str:
    """UAT- 前綴＝測試環境，其餘視為正式。

    刻意用 category 而不是另外要一欄：原檔就是用 UAT-SERVER／UAT-NETWORK 表達這件事，
    多要一欄等於要維護兩份同義資料，遲早不同步。
    """
    c = (category or "").upper()
    if c.startswith("UAT"):
        return "測試"
    return "正式"


def parse_cidr(raw: str) -> tuple[str | None, int | None, int | None]:
    """回傳 (正規化 CIDR, 起始整數, 結束整數)；解析不出來回 (None, None, None)。

    只認單一標準 CIDR。原檔有這幾種解析不掉的寫法，一律當「無法解析」不要硬猜：
      · 一格兩段：`10.99.255.23/28\\n10.99.255.17/28`
      · IP 範圍：`172.16.156.0/24~172.16.157.230`
    硬猜的後果是把錯的範圍寫進系統，之後「這個 IP 屬於哪段」全錯——寧可留白等人修。
    """
    s = str(raw or "").strip()
    if not s or "\n" in s or "~" in s or "," in s:
        return None, None, None
    try:
        net = ipaddress.ip_network(s, strict=False)
    except ValueError:
        return None, None, None
    return str(net), int(net.network_address), int(net.broadcast_address)


def _rows_from_xlsx(path: Path) -> list[dict]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []
    header = [_norm_header(c) for c in rows[0]]
    out = []
    for i, r in enumerate(rows[1:], start=2):
        d = {"_row_no": i}
        for h, v in zip(header, r):
            for key, field in HEADER_MAP.items():
                if key in h:
                    d[field] = None if v is None else str(v).strip()
        out.append(d)
    return out


def _rows_from_text(path: Path) -> list[dict]:
    """Tab 分隔的文字檔（使用者先給的過渡格式，欄位跟 Excel 一樣）。"""
    import csv
    import io

    with io.open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        out = []
        for i, r in enumerate(reader, start=2):
            d = {"_row_no": i}
            for k, v in r.items():
                h = _norm_header(k)
                for key, field in HEADER_MAP.items():
                    if key in h:
                        d[field] = (v or "").strip() or None
            out.append(d)
        return out


def read_rows(path: Path) -> list[dict]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        return _rows_from_xlsx(path)
    return _rows_from_text(path)


def import_segments(conn: sqlite3.Connection, path: Path) -> dict:
    """整批取代匯入。回傳摘要與警告清單。

    **整批取代不是 upsert**：Excel 是這份清單的唯一真相，段被刪掉就該從系統消失。
    upsert 會讓已作廢的網段永遠留著，越用越髒，而且沒有人會發現。
    先解析成功才刪舊資料——檔案格式不對時不能把現有清單清空。
    """
    rows = read_rows(path)
    if not rows:
        raise ValueError("檔案裡沒有資料列")

    header_ok = any(r.get("raw_cidr") for r in rows)
    if not header_ok:
        raise ValueError("找不到「網段」欄位，請確認是不是網段配置表（表頭要有：使用位置／使用類別／網段）")

    parsed: list[dict] = []
    warnings: list[dict] = []
    for r in rows:
        raw = (r.get("raw_cidr") or "").strip()
        if not raw:
            continue  # 整列空白（Excel 常見的尾巴空列）不算警告
        cidr, start, end = parse_cidr(raw)
        if cidr is None:
            warnings.append({
                "row_no": r["_row_no"], "raw_cidr": raw,
                "reason": "網段寫法無法解析成單一 CIDR（一格多段或 IP 範圍），已保留但不能用於 IP 配置與掃描",
            })
        parsed.append({
            "cidr": cidr, "raw_cidr": raw, "net_start": start, "net_end": end,
            "usage_status": r.get("usage_status"), "location": r.get("location"),
            "purpose_desc": r.get("purpose_desc"), "category": r.get("category"),
            "usage": r.get("usage"),
            "environment": _derive_environment(r.get("category") or ""),
            "scan_excluded": 1 if _EXCLUDE_RE.search(r.get("scan_note") or "") else 0,
            "scan_note": r.get("scan_note"), "row_no": r["_row_no"],
        })

    seen: dict[str, int] = {}
    for p in parsed:
        if p["cidr"]:
            if p["cidr"] in seen:
                warnings.append({
                    "row_no": p["row_no"], "raw_cidr": p["raw_cidr"],
                    "reason": f"這個網段在第 {seen[p['cidr']]} 列已經出現過，兩列都保留，請確認是不是重複登記",
                })
            else:
                seen[p["cidr"]] = p["row_no"]

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM network_segment")
    conn.executemany(
        "INSERT INTO network_segment "
        "(cidr, raw_cidr, net_start, net_end, usage_status, location, purpose_desc, "
        " category, usage, environment, scan_excluded, scan_note, row_no, imported_at) "
        "VALUES (:cidr, :raw_cidr, :net_start, :net_end, :usage_status, :location, "
        " :purpose_desc, :category, :usage, :environment, :scan_excluded, :scan_note, "
        " :row_no, :imported_at)",
        [{**p, "imported_at": now} for p in parsed],
    )
    conn.commit()
    return {
        "imported": len(parsed),
        "parsed_cidr": sum(1 for p in parsed if p["cidr"]),
        "scan_excluded": sum(1 for p in parsed if p["scan_excluded"]),
        "locations": len({p["location"] for p in parsed if p["location"]}),
        "warnings": warnings,
        "imported_at": now,
    }


def list_segments(conn: sqlite3.Connection) -> list[dict]:
    """全部網段＋每段已登記幾台資產（要看得出哪段快滿了、哪段根本沒東西）。"""
    rows = conn.execute(
        "SELECT * FROM network_segment ORDER BY location, cidr, raw_cidr"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["asset_count"] = _count_assets(conn, r)
        d["capacity"] = (r["net_end"] - r["net_start"] - 1) if r["net_start"] else None
        out.append(d)
    return out


def _count_assets(conn: sqlite3.Connection, seg: sqlite3.Row) -> int:
    if seg["net_start"] is None:
        return 0
    r = conn.execute(
        "SELECT COUNT(*) n FROM hardware WHERE ip IS NOT NULL AND TRIM(ip) != '' "
        "AND _ip_int(ip) BETWEEN ? AND ?",
        (seg["net_start"], seg["net_end"]),
    ).fetchone()
    return r["n"]


def tree(conn: sqlite3.Connection) -> list[dict]:
    """機房 → 環境 → 網段 三層，給新增資產的 IP 選單用。

    使用者指定的順序（2026-08-15）：先選機房、再選環境、最後才選網段——
    填表的人記得住「這台在板橋、是正式機」，記不住 10.99.163 是哪一段。
    """
    rows = conn.execute(
        "SELECT * FROM network_segment WHERE cidr IS NOT NULL "
        "ORDER BY location, environment, cidr"
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        loc = r["location"] or "（未填機房）"
        env = r["environment"] or "正式"
        node = next((x for x in out if x["location"] == loc), None)
        if node is None:
            node = {"location": loc, "environments": []}
            out.append(node)
        enode = next((x for x in node["environments"] if x["environment"] == env), None)
        if enode is None:
            enode = {"environment": env, "segments": []}
            node["environments"].append(enode)
        enode["segments"].append({
            "cidr": r["cidr"],
            "label": f"{r['cidr']}　{r['purpose_desc'] or ''}".strip(),
            "category": r["category"],
            "usage": r["usage"],
            "scan_excluded": bool(r["scan_excluded"]),
            "scan_note": r["scan_note"],
        })
    return out


def segment_ips(conn: sqlite3.Connection, cidr: str) -> dict:
    """這段裡哪些 IP 已被登記、下一個沒被用的是哪個。

    只講「這份清單裡有沒有登記」，不宣稱「這個 IP 實際上沒人在用」——
    清單本來就不完整（這正是資料品質那頁在量的事），把「沒登記」講成「可用」
    會讓使用者拿去配一個實際上已經有人在用的 IP。
    """
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        raise ValueError(f"網段格式不正確：{cidr}")

    seg = conn.execute("SELECT * FROM network_segment WHERE cidr = ? LIMIT 1", (cidr,)).fetchone()
    rows = conn.execute(
        "SELECT asset_serial, hostname, ip, asset_status FROM hardware "
        "WHERE ip IS NOT NULL AND TRIM(ip) != '' AND _ip_int(ip) BETWEEN ? AND ? "
        "ORDER BY _ip_int(ip)",
        (int(net.network_address), int(net.broadcast_address)),
    ).fetchall()
    used = {r["ip"].strip(): dict(r) for r in rows}

    suggestion = None
    for host in net.hosts():
        if str(host) not in used:
            suggestion = str(host)
            break

    return {
        "cidr": cidr,
        "purpose_desc": seg["purpose_desc"] if seg else None,
        "scan_note": seg["scan_note"] if seg else None,
        "capacity": net.num_addresses - 2 if net.num_addresses > 2 else net.num_addresses,
        "used": list(used.values()),
        "used_count": len(used),
        "suggestion": suggestion,
        "suggestion_caveat": "只代表「這份清單裡沒登記」，不代表實際上沒人在用",
    }


def scan_candidates(conn: sqlite3.Connection) -> dict:
    """掃描範圍建議：哪些網段該掃、哪些被資安註記為排除。

    這支存在的理由是資料品質那頁量出來的問題——「涵蓋率 0%」的下一步是
    「那該掃哪些」，不該讓使用者自己回去翻 Excel。
    """
    rows = conn.execute(
        "SELECT * FROM network_segment WHERE cidr IS NOT NULL ORDER BY cidr"
    ).fetchall()
    include = [dict(r) for r in rows if not r["scan_excluded"]]
    exclude = [dict(r) for r in rows if r["scan_excluded"]]
    return {
        "include": include,
        "exclude": exclude,
        "include_count": len(include),
        "exclude_count": len(exclude),
    }


def find_segment_for_ip(conn: sqlite3.Connection, ip: str) -> dict | None:
    """這個 IP 屬於哪一段。資產詳細頁要講得出「這台在板橋 DMZ」而不只是一串數字。

    只認 IPv4——network_segment.net_start/net_end 是用 int(IPv4位址) 灌的，範圍在
    SQLite INTEGER（64位元有號）之內；IPv6 的整數值可以到 2^128，直接綁進查詢會炸
    `OverflowError: Python int too large to convert to SQLite INTEGER`。2026-08-19
    正式機真的踩到：vCenter 收到兩台 VM 的 IPv6 link-local 位址（fe80::...）當
    hardware.ip，讓整個 ci_graph.rebuild() 在跑到那筆資料時整批中斷——不是「這個 IP
    沒有段」，是「這個 IP 根本問錯問題」，兩者都回 None，呼叫端不用分辨。
    """
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return None
    if addr.version != 4:
        return None
    n = int(addr)
    r = conn.execute(
        "SELECT * FROM network_segment WHERE net_start IS NOT NULL "
        "AND ? BETWEEN net_start AND net_end ORDER BY (net_end - net_start) LIMIT 1",
        (n,),
    ).fetchone()
    return dict(r) if r else None
