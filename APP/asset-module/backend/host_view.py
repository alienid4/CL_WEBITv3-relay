"""以「台」為單位的主機清單（2026-09-18 使用者：一台像一個人、可以有多個外號）。

資產查詢頁是逐「筆」列（同一台被多系統／多 VIP 各登記一筆會出現多列）。這一頁改以
machine_key 去重、一台一列，把同一台的多筆登記（不同 asset_serial／資產名稱／AP ID）
收成該台的「多重登記（別名）」清單。

口徑跟全站一致：
- 去重鍵＝正典 system_stats.machine_key（主機名不分大小寫＋IP；缺任一或 0.0.0.0 各算一台）
- 排除退役（報廢／停用／閒置）與帳外（DYN-/VC-/AUTO-），跟 composition.registered_hosts
  同一個母體 → 台數對得上首頁「在管 N 台」。
- 代表列固定取最小序號（跟 system_stats.collapse_machines 同規則），每次算出來一樣。
"""
from __future__ import annotations

import system_stats
import manage_state as ms
import system_report as sr


def list_hosts(conn) -> dict:
    rows = conn.execute(
        "SELECT asset_serial, hostname, ip, environment, physical_location, os, device_model, "
        "is_vm, asset_status, asset_name, asset_purpose, api_id, big_ip_vip FROM hardware"
    ).fetchall()
    # 服務／帳號數依 IP 一次撈完（不在每台迴圈裡逐台查）。表可能還沒建就當空。
    def _count_by_ip(table: str) -> dict:
        try:
            return {r[0]: r[1] for r in conn.execute(
                f"SELECT ip, COUNT(*) FROM {table} WHERE gone_at IS NULL "
                "AND ip IS NOT NULL AND TRIM(ip) <> '' GROUP BY ip")}
        except Exception:  # noqa: BLE001 - 舊庫沒這張表／沒 gone_at 欄就當沒有，不擋整頁
            return {}
    svc_by_ip = _count_by_ip("host_service")
    acct_by_ip = _count_by_ip("host_account")
    import vip_view
    groups: dict[str, list] = {}
    for r in rows:
        status = (r["asset_status"] or "").strip()
        if status in ms.RETIRED_STATUS:
            continue                                   # 退役不列（跟在管台同母體）
        if str(r["asset_serial"] or "").startswith(sr.OFF_BOOK_PREFIXES):
            continue                                   # 帳外另計，不進在管台
        groups.setdefault(
            system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"]), []
        ).append(r)

    hosts = []
    for key, g in groups.items():
        g.sort(key=lambda x: x["asset_serial"] or "")
        rep = g[0]                                     # 代表列＝最小序號
        regs = [{
            "asset_serial": r["asset_serial"],
            "asset_name": r["asset_name"],
            "asset_purpose": r["asset_purpose"],
            "api_id": r["api_id"],
            "environment": r["environment"],
        } for r in g]
        # 這台掛的系統（AP ID 去重、保序），給畫面一眼看出「一台上有幾個系統」
        systems, seen = [], set()
        for r in g:
            code = (r["api_id"] or "").strip()
            if code and code not in seen:
                seen.add(code)
                systems.append(code)
        # VIP 數：這台各筆登記的 big_ip_vip 去重（用 vip_view 同一套正規化濾掉「無/N/A/空」）。
        vips = set()
        for r in g:
            v = vip_view.normalize_vip(r["big_ip_vip"])
            if v and v not in ("無",):
                vips.add(v)
        # 服務／帳號數：這台所有 IP 的加總（同一台可能多 IP，但 machine_key 下多為同一個）
        ips = {(r["ip"] or "").strip() for r in g if (r["ip"] or "").strip()}
        svc_count = sum(svc_by_ip.get(ip, 0) for ip in ips)
        acct_count = sum(acct_by_ip.get(ip, 0) for ip in ips)
        hosts.append({
            "machine_key": key,
            "asset_serial": rep["asset_serial"],       # 代表序號（點進去看的那筆）
            "hostname": rep["hostname"],
            "ip": rep["ip"],
            "location": rep["physical_location"],
            "environment": rep["environment"],
            "os": rep["os"],
            "device_model": rep["device_model"],
            "is_vm": bool(ms.is_vm_value(rep["is_vm"], rep["device_model"])),
            "asset_status": rep["asset_status"],
            "reg_count": len(g),                       # 這台登記幾筆（>1＝有多重登記）
            "system_count": len(systems),
            "systems": systems,
            "vip_count": len(vips),                    # 這台掛幾個 VIP（去重、濾無/N/A）
            "service_count": svc_count,                # 收到的服務數（依 IP）
            "account_count": acct_count,               # 盤到的帳號數（依 IP）
            "registrations": regs,                     # 多重登記（別名）明細
        })
    hosts.sort(key=lambda h: (-h["reg_count"], (h["hostname"] or "").lower()))
    # 「疑似同台待確認」入口用的數字：缺主機名或 IP、但放寬比對（vm_uuid／MAC／單欄）
    # 找得到候選的殘缺列。**沿用 host_sources 這唯一一份放寬比對邏輯**，不在這裡重寫一把尺
    # （全站去重只能有一把）。系統不自動合併，交給人去 /reports/host-sources 那欄認定。
    import host_sources
    loose_candidate_hosts = host_sources.summary(host_sources.rows(conn))["loose_candidate_hosts"]
    return {
        "hosts": hosts,
        "total_hosts": len(groups),                    # 台數（去重）＝首頁在管台
        "total_rows": sum(len(g) for g in groups.values()),  # 登記筆數（同母體）
        "multi_reg_hosts": sum(1 for h in hosts if h["reg_count"] > 1),  # 有多重登記的台數
        "loose_candidate_hosts": loose_candidate_hosts,  # 疑似同一台待人確認（放寬比對）
    }
