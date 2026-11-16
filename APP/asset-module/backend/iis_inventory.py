"""IIS 盤點（2026-10-20，使用者：「IIS 底下可能有不同的子服務」）。

一台 IIS 主機底下有多個站台、每個站台有多個應用程式／虛擬目錄、各自掛在不同的應用程式集區——
「這台在跑 IIS」只是一句話，要列到「哪個站台、哪個路徑、哪個集區、用什麼身分跑、綁哪個埠／憑證」
才看得出依賴與風險。

## 怎麼收（WinRM／PSRP，記憶體內，不起子行程）

* 先看有沒有 IIS：登錄 `HKLM\\SYSTEM\\CurrentControlSet\\Services\\W3SVC` 存在才算有；沒有＝「此機未使用」。
* 結構：讀 `applicationHost.config`（Get-Content＋[xml]，唯讀）——站台、綁定、應用程式、虛擬目錄、集區。
  不用 appcmd.exe（會起子行程，SOC）；不依賴 WebAdministration 模組（很多機器沒裝管理工具）。
  這個檔預設只有系統管理員讀得到；讀不到 → 「權限不足（需讀 applicationHost.config 或 WMI root\\WebAdministration
  遠端啟用）」（決策 WINRM-ADMIN1：收集帳號應為本機管理員）。
* 執行狀態：有 WebAdministration 模組才查（Get-WebsiteState／Get-WebAppPoolState）；沒有就留空（不知道，不是停止）。
* https 憑證：登錄 `HTTP\\Parameters\\SslBindingInfo`（與 SslSniBindingInfo）的 SslCertHash →
  `Cert:\\LocalMachine\\My` 取主旨與到期日。**只取指紋／主旨／到期日，永不碰私鑰。**
* 集區身分：只收 identityType 與 userName；**密碼屬性一律不讀出**。

## 存放

一台一份快照（host_iis，JSON）：結構是樹狀的，拆成五張表只會讓「整台現況」變難讀。
綁定的埠回填服務盤點（host_service.service_guess＝「IIS：站台名」），服務分頁看得出 80／443 是哪個站台。
"""
from __future__ import annotations

import json

import winrm_collector as _wc

IIS_PS = _wc.IIS_PS     # 腳本本體放在 winrm_collector（附在 SERVICES_PS 後面一次往返）


def parse_iis(text: str) -> dict | None:
    """SERVICES_PS 輸出裡的 @@IIS 段 → 樹狀 dict。沒有 @@IIS 段（舊腳本）回 None＝這次沒查。"""
    if "@@IIS" not in (text or ""):
        return None
    seg = text.split("@@IIS", 1)[1]
    present = None
    sites: dict[str, dict] = {}
    pools: list[dict] = []
    for raw in seg.splitlines():
        line = raw.rstrip("\r\n")
        if line.startswith("IIS="):
            present = line[4:].strip() == "1"
            continue
        p = line.split("\t")
        tag = p[0]
        p = p + [""] * 8
        if tag == "POOL":
            pools.append({"name": p[1], "state": p[2] or None, "clr": p[3] or None,
                          "pipeline": p[4] or None, "identity_type": p[5] or None,
                          "user": p[6] or None})
        elif tag == "SITE":
            sites[p[1]] = {"id": p[1], "name": p[2], "state": p[3] or None, "path": p[4] or None,
                           "auto_start": p[5] or None, "bindings": [], "apps": []}
        elif tag == "BIND" and p[1] in sites:
            info = p[3]
            parts = info.split(":")
            port = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            sites[p[1]]["bindings"].append({
                "protocol": p[2], "info": info, "ip": parts[0] or "*", "port": port,
                "host": parts[2] if len(parts) > 2 else "",
                "cert_thumbprint": p[4] or None, "cert_expires": p[5] or None,
                "cert_subject": p[6] or None})
        elif tag == "APP" and p[1] in sites:
            sites[p[1]]["apps"].append({"path": p[2], "pool": p[3] or None,
                                        "physical_path": p[4] or None, "vdirs": []})
        elif tag == "VDIR" and p[1] in sites:
            for a in sites[p[1]]["apps"]:
                if a["path"] == p[2]:
                    a["vdirs"].append({"path": p[3], "physical_path": p[4] or None})
                    break
    errors = [e for e in _wc.parse_errors(seg) if e["item"].startswith("iis")]
    return {"present": present, "sites": list(sites.values()), "pools": pools, "errors": errors}


def store(conn, ip: str, asset_serial: str | None, iis: dict | None) -> str | None:
    """存快照並回填服務盤點的服務名。回給人看的一句話（沒查就 None）。"""
    if iis is None:
        return None
    from db import _now_local

    if iis["present"] is False:
        state, note = "unused", "此機未使用 IIS"
    elif iis["errors"] and not iis["sites"]:
        state = "denied" if any(e["kind"] in ("DENIED", "EMPTY") for e in iis["errors"]) else "failed"
        note = ("IIS：權限不足（需讀 applicationHost.config 或 WMI root\\WebAdministration 遠端啟用）"
                if state == "denied" else "IIS：" + _wc.explain_errors(iis["errors"])[0])
    else:
        state, note = "ok", None
    if state in ("denied", "failed"):
        # 讀不到不覆蓋上一份好的快照，只記這次的狀態
        prev = conn.execute("SELECT 1 FROM host_iis WHERE ip = ?", (ip,)).fetchone()
        if prev:
            conn.execute("UPDATE host_iis SET last_state = ?, last_note = ?, checked_at = ? WHERE ip = ?",
                         (state, note, _now_local(), ip))
            conn.commit()
            return note
    payload = json.dumps({"sites": iis["sites"], "pools": iis["pools"]}, ensure_ascii=False)
    conn.execute(
        "INSERT INTO host_iis (ip, asset_serial, state, payload, last_state, last_note, collected_at, checked_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(ip) DO UPDATE SET asset_serial=excluded.asset_serial, "
        "state=excluded.state, payload=excluded.payload, last_state=excluded.last_state, "
        "last_note=excluded.last_note, collected_at=excluded.collected_at, checked_at=excluded.checked_at",
        (ip, asset_serial, state, payload, state, note, _now_local(), _now_local()))
    # 綁定的埠回填服務盤點：80／443 是哪幾個站台（http.sys 的行程名只會是 System）
    by_port: dict[int, list[str]] = {}
    for s in iis["sites"]:
        for b in s["bindings"]:
            if b.get("port") and b["protocol"] in ("http", "https"):
                by_port.setdefault(b["port"], [])
                if s["name"] not in by_port[b["port"]]:
                    by_port[b["port"]].append(s["name"])
    for port, names in by_port.items():
        conn.execute(
            "UPDATE host_service SET service_guess = ?, guess_source = 'process' "
            "WHERE ip = ? AND port = ? AND gone_at IS NULL "
            "AND (process IS NULL OR process = 'System' OR process LIKE 'System %')",
            (f"IIS：{'、'.join(names)[:200]}", ip, port))
    conn.commit()
    return note


def for_asset(conn, asset_serial: str) -> dict:
    """資產頁 IIS 區塊：{state, collected_at, sites, pools, note}。state=none 表示從沒查過。"""
    import machine_scope

    w, params = machine_scope.scope_where(machine_scope.scope(conn, asset_serial))
    try:
        row = conn.execute(f"SELECT * FROM host_iis WHERE {w} ORDER BY checked_at DESC LIMIT 1",
                           params).fetchone()
    except Exception:  # noqa: BLE001 - 舊庫沒有這張表：當作沒查過
        row = None
    if not row:
        return {"state": "none", "sites": [], "pools": []}
    data = json.loads(row["payload"] or "{}") if row["payload"] else {}
    return {"state": row["state"], "last_state": row["last_state"], "note": row["last_note"],
            "collected_at": row["collected_at"], "checked_at": row["checked_at"],
            "sites": data.get("sites") or [], "pools": data.get("pools") or []}
