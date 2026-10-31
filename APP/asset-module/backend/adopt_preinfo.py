"""2-1「納管前資訊」：一台機器我們**已經知道**的全部線索，攤在一個面板上（v1.468.7）。

2026-10-19 使用者（公司機）：怕把網路設備、或不是 RHEL／CentOS／Debian 的機器納管進來，
想在按「一鍵納管」之前先看清楚這台是什麼。

只讀資料庫，**不碰網路、不登入、不改任何東西**（要登入看的是另一個功能「預檢」）。
查不到的欄位回 None，畫面寫「沒有」——不要猜。

線索來源：
  - 網段掃描（scan_history 最新一筆）：反解主機名、MAC／廠商、開放埠、TTL、研判
  - 2-1 收集結果（collect_dispatch_result 最新一筆）：狀態、訊息、SSH banner（連線訊息裡帶的）
  - 資產登記（hardware，現役優先）：資產名稱、設備機型、作業系統、業務系統（API ID）、擁有者／保管者／使用者、環境
  - 納管紀錄（onboard_audit 最後一次）、機器標籤（asset_tags）
  - 判斷（host_kind，跟 2-1 表格同一份）、廠牌（host_kind.vendor_judge）
banner 代表什麼：規則在 host_kind_rules.json 的 banner_hints（不寫死在這裡）。
"""
from __future__ import annotations

import re

_SSH_BANNER_RE = re.compile(r"SSH-\d\.\d-[^\s，。、）)\]'\"]+", re.I)


def banner_raw(text) -> str | None:
    """訊息裡帶的原始 SSH banner（SSH-2.0-OpenSSH_8.7）；沒有回 None。"""
    m = _SSH_BANNER_RE.search(str(text or ""))
    return m.group(0) if m else None


def banner_hint(raw: str | None) -> dict | None:
    """banner 代表什麼（規則在 host_kind_rules.json 的 banner_hints，由上往下先命中先算）。"""
    if not raw:
        return None
    import host_kind

    for h in host_kind.load_rules().get("banner_hints", []):
        if re.search(h["regex"], raw, re.I):
            return {"kind": h["kind"], "text": h["text"]}
    return {"kind": "unknown", "text": "認不出這個 banner 是什麼軟體——不能拿它判斷是不是設備"}


def gather(conn, ip: str) -> dict:
    import asset_tags
    import collect_dispatch as cd
    import host_kind
    import manage_state
    import onboard_eligibility as oe
    import onboard_failures

    ip = (ip or "").strip()
    out: dict = {"ip": ip}

    # --- 網段掃描（最新一筆成功的）
    scan = None
    try:
        r = conn.execute(
            "SELECT scan_time, hostname, mac, mac_vendor, open_ports, ttl, os_guess, segment "
            "FROM scan_history WHERE ip = ? AND scan_ok = 1 ORDER BY scan_time DESC, id DESC LIMIT 1",
            (ip,)).fetchone()
        scan = dict(r) if r else None
    except Exception:  # noqa: BLE001 - 舊庫沒有指紋欄：這一段寫「沒有」，不擋整個面板
        scan = None

    # --- 資產登記（現役優先，跟 host_kind.evidence_inputs 同一個挑法）
    rows = [dict(x) for x in conn.execute(
        "SELECT asset_serial, asset_name, device_model, os, api_id, group_name, asset_purpose, owner, "
        "custodian, user_name, environment, asset_status, hostname FROM hardware WHERE ip = ? ORDER BY rowid",
        (ip,))]
    live = [x for x in rows if (x["asset_status"] or "").strip() not in manage_state.RETIRED_STATUS] or rows
    reg = live[0] if live else None
    out["registry"] = reg
    out["registry_count"] = len(rows)

    if scan:
        scan["os_guess"] = host_kind.display_guess(
            scan.get("os_guess"), ip=ip, registered=1 if reg else 0,
            ttl=scan.get("ttl"), open_ports=scan.get("open_ports"))
    out["scan"] = scan

    # --- 2-1 收集結果（最新一筆）
    disp = None
    try:
        r = conn.execute(
            "SELECT r.status, r.route, r.open_ports, r.message, r.not_onboardable_kind, r.created_at, r.run_id "
            "FROM collect_dispatch_result r WHERE r.ip = ? ORDER BY r.id DESC LIMIT 1", (ip,)).fetchone()
        disp = dict(r) if r else None
    except Exception:  # noqa: BLE001 - 沒跑過收集：這一段寫「沒有」
        disp = None
    if disp:
        disp["status_label"] = cd.STATUS_LABEL.get(disp["status"], disp["status"])
        disp["route_label"] = cd.ROUTE_LABEL.get(disp["route"], disp["route"])
    out["dispatch"] = disp

    # --- SSH banner：收集時連線訊息帶的（沒開 22 或沒連過就沒有）
    raw = banner_raw((disp or {}).get("message"))
    product = oe.product_from_banner((disp or {}).get("message")) if disp else None
    out["banner"] = {"raw": raw, "product": product, "hint": banner_hint(raw)} if (raw or product) else None

    # --- 判斷（跟 2-1 表格同一份）＋廠牌
    ttl = (scan or {}).get("ttl")
    ports = sorted(set(host_kind._ports((disp or {}).get("open_ports")))
                   | set(host_kind._ports((scan or {}).get("open_ports"))))
    inputs = host_kind.evidence_inputs(conn, [ip]).get(ip)
    out["judgement"] = host_kind.infer_from(inputs, open_ports=ports, ttl=ttl, ip=ip,
                                            registered=1 if reg else 0)
    vin = host_kind.vendor_inputs(conn, [ip]).get(ip) or {}
    out["vendor"] = host_kind.vendor_judge(banner_product=product, registry=vin.get("registry"),
                                           mac_vendor=vin.get("mac_vendor"))

    # --- 納管紀錄、標籤
    out["last_attempt"] = onboard_failures.last_attempts(conn, [ip]).get(ip)
    out["tags"] = asset_tags.tags_by_serial(conn, [reg["asset_serial"]]).get(reg["asset_serial"], []) \
        if reg else []
    return out
