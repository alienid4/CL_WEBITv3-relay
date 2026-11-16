"""開始收集的涵蓋率：哪些網段／位址收集過、哪些完全沒收過（2026-10-20 使用者：
「我要怎麼知道哪些已經收集過、哪些完全沒收集過」）。

單一來源：collect_dispatch._merged_results(conn, None)——跟 2-1 待納管表、頁簽數字同一份
「每個位址最新一次結果」。這裡只按網段分組，不另寫一套「收過沒」的判斷。

網段來源：網段配置表（network_segment，有 CIDR 的）＋收集過、但不在任何配置表網段裡的位址
所屬的 /24（標「配置表沒有」）——不然收過的位址會在這張表裡憑空消失。

「已收集」＝這個位址在任何一輪收集裡有結果（不論活著與否：空位址也是「收過、沒東西」）。
「從未收集」＝沒有任何一輪探測過它。
"""
from __future__ import annotations

import ipaddress

#: 網段大於這個位址數就不逐址列（/20＝4096）：只列收集過的那些，並明講沒列全
DETAIL_MAX = 4096


def _hosts_range(start: int, end: int) -> tuple[int, int]:
    """可用主機位址範圍（扣網路位址與廣播；/31、/32 不扣）。跟 parse_targets 展開網段同一個口徑。"""
    if end - start >= 2:
        return start + 1, end - 1
    return start, end


def _latest_by_ip(conn) -> dict[int, dict]:
    import collect_dispatch

    out: dict[int, dict] = {}
    for r in collect_dispatch._merged_results(conn, None):
        try:
            out[int(ipaddress.IPv4Address(r["ip"]))] = r
        except ValueError:
            continue
    return out


def _segments(conn) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT cidr, net_start, net_end, location, environment, purpose_desc, scan_excluded "
            "FROM network_segment WHERE cidr IS NOT NULL AND net_start IS NOT NULL "
            "ORDER BY net_start").fetchall()
    except Exception:  # noqa: BLE001 - 還沒匯網段配置表（新庫沒有這張表）＝沒有配置表網段
        rows = []
    seen: set[str] = set()
    out = []
    for r in rows:
        if r["cidr"] in seen:
            continue
        seen.add(r["cidr"])
        out.append({"cidr": r["cidr"], "start": r["net_start"], "end": r["net_end"],
                    "location": r["location"], "environment": r["environment"],
                    "purpose": r["purpose_desc"], "scan_excluded": bool(r["scan_excluded"]),
                    "in_table": True})
    return out


def coverage(conn) -> dict:
    """每個網段：可用位址數、已收集位址數、最近收集時間、從未收集與否。"""
    latest = _latest_by_ip(conn)
    segs = _segments(conn)
    import bisect

    keys = sorted(latest)
    covered: set[int] = set()
    rows = []
    for s in segs:
        lo, hi = _hosts_range(s["start"], s["end"])
        ks = keys[bisect.bisect_left(keys, lo):bisect.bisect_right(keys, hi)]
        hit = [latest[k] for k in ks]
        covered.update(ks)
        rows.append({**s, "total": hi - lo + 1, "collected": len(hit),
                     "alive": sum(1 for v in hit if v.get("alive")),
                     "last_at": max((v.get("collected_at") or "" for v in hit), default="") or None})
    # 收過、但不在任何配置表網段裡的位址：依 /24 分組補上
    extra: dict[str, list] = {}
    for k, v in latest.items():
        if k in covered:
            continue
        net = ipaddress.ip_network(f"{ipaddress.IPv4Address(k)}/24", strict=False)
        extra.setdefault(str(net), []).append(v)
    for cidr, hit in extra.items():
        net = ipaddress.ip_network(cidr)
        lo, hi = _hosts_range(int(net.network_address), int(net.broadcast_address))
        rows.append({"cidr": cidr, "start": int(net.network_address), "end": int(net.broadcast_address),
                     "location": None, "environment": None, "purpose": None, "scan_excluded": False,
                     "in_table": False, "total": hi - lo + 1, "collected": len(hit),
                     "alive": sum(1 for v in hit if v.get("alive")),
                     "last_at": max((v.get("collected_at") or "" for v in hit), default="") or None})
    rows.sort(key=lambda r: r["start"])
    for i, r in enumerate(rows, 1):
        r["no"] = i
        r["never"] = r["collected"] == 0
    return {"segments": rows,
            "summary": {"segments": len(rows),
                        "never": sum(1 for r in rows if r["never"]),
                        "partial": sum(1 for r in rows if 0 < r["collected"] < r["total"]),
                        "full": sum(1 for r in rows if r["total"] and r["collected"] >= r["total"]),
                        "addresses": sum(r["total"] for r in rows),
                        "collected": sum(r["collected"] for r in rows)}}


def segment_detail(conn, cidr: str) -> dict:
    """點一個網段：逐位址列最近收集時間／狀態，或「從未收集」。"""
    try:
        net = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError as exc:
        raise ValueError(f"網段格式錯誤：{cidr}") from exc
    lo, hi = _hosts_range(int(net.network_address), int(net.broadcast_address))
    latest = _latest_by_ip(conn)
    total = hi - lo + 1
    full_list = total <= DETAIL_MAX
    keys = range(lo, hi + 1) if full_list else sorted(k for k in latest if lo <= k <= hi)
    items = []
    for i, k in enumerate(keys, 1):
        v = latest.get(k)
        items.append({"no": i, "ip": str(ipaddress.IPv4Address(k)),
                      "collected_at": (v or {}).get("collected_at"),
                      "status": (v or {}).get("status"), "alive": (v or {}).get("alive"),
                      "hostname": (v or {}).get("hostname"), "never": v is None})
    return {"cidr": str(net), "total": total, "collected": sum(1 for x in items if not x["never"]),
            "listed_all": full_list, "items": items}
