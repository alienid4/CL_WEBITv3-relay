"""資產分類：平台（Windows/Linux/AIX…）與角色（DB／Web／主機…）。

兩個軸刻意分開，因為它們回答的是不同問題：
  平台 = 這台是什麼作業系統 → 決定「怎麼收它、誰維護它」
  角色 = 這台在做什麼         → 決定「它掛了誰受影響」

## 角色從「服務」推導，不是人填的欄位

一台機器是不是資料庫，看它有沒有在聽 3306/5432/1521——這是機器自己講的事實，
比人填的「資產用途」可靠得多（那欄常年沒人更新）。這也是 M2 服務盤點的複利：
先收到服務，資產分類就自動有了。

⚠️ 沒收過服務的機器角色是「未知」，不是「主機」——把「還沒查」講成「查過了是普通主機」
是在製造假確定性。畫面必須把這兩者分開顯示。
"""
from __future__ import annotations

# 角色判定用的監聽埠。一台機器可以同時是多個角色（跑 nginx 又跑 MySQL 很常見）。
ROLE_PORTS = {
    "db": {1433, 1521, 3306, 5432, 6379, 9200, 11211, 27017},
    "web": {80, 443, 3000, 8000, 8080, 8443, 9000},
    "middleware": {2181, 4369, 5672, 9092},          # 訊息佇列／協調服務
    "infra": {53, 123, 389, 636, 3268, 2049, 514},   # DNS/NTP/LDAP/NFS/Syslog
    "mgmt": {22, 3389, 5985, 5986, 623},             # 遠端管理通道
}

ROLE_LABELS = {
    "db": "資料庫",
    "web": "Web／應用",
    "middleware": "中介服務",
    "infra": "基礎服務",
    "mgmt": "僅管理通道",
    "unknown": "未知（未收過服務）",
}

# 只有管理通道（22/3389）的機器不該被叫成「Web／應用」也不該叫「普通主機」——
# 它就是「有開機、但沒看到在提供什麼服務」。這個區別在盤點時很有用：
# 那可能是閒置機器，也可能是收集權限不足，兩種都值得有人看一眼。
_SUBSTANTIVE = ("db", "web", "middleware", "infra")


def roles_by_ip(conn) -> dict[str, list[str]]:
    """每個 IP 目前在聽的服務 → 角色清單。只看還活著的服務（gone_at 為空）。"""
    out: dict[str, set] = {}
    try:
        rows = conn.execute(
            "SELECT ip, port FROM host_service WHERE gone_at IS NULL"
        ).fetchall()
    except Exception:  # noqa: BLE001 - 還沒建表（舊 DB）就是全部未知
        return {}
    for r in rows:
        ip, port = r["ip"], r["port"]
        bucket = out.setdefault(ip, set())
        for role, ports in ROLE_PORTS.items():
            if port in ports:
                bucket.add(role)
    result: dict[str, list[str]] = {}
    for ip, roles in out.items():
        substantive = [r for r in _SUBSTANTIVE if r in roles]
        # 有實質服務就不再標 mgmt——每台都有 SSH，標了等於沒標
        result[ip] = substantive or (["mgmt"] if "mgmt" in roles else [])
    return result


def role_of(conn_roles: dict[str, list[str]], ip: str | None) -> list[str]:
    """單台的角色。沒收過服務回 ['unknown']——不預設成「主機」，不製造假確定性。"""
    if not ip:
        return ["unknown"]
    return conn_roles.get(ip) or ["unknown"]


def platform_of_row(row, guesses: dict) -> str:
    """單列資產的平台大類。沿用 manage_state 的判準，避免兩邊分岔成兩套規則。

    優先序：hardware.os（收到的真 OS）> 掃描 os_guess（推測）> device_model。
    """
    import manage_state

    guess_os = guesses.get(row["ip"], (None, None))[0] if row["ip"] else None
    return manage_state.platform_of(row["os"], guess_os, row["device_model"])


def scan_os_guesses(conn) -> dict:
    """最近一次掃描的 os_guess／mac_vendor，供平台判定退回使用。"""
    latest = conn.execute("SELECT MAX(scan_time) AS t FROM scan_history").fetchone()
    if not latest or not latest["t"]:
        return {}
    return {
        r["ip"]: (r["os_guess"], r["mac_vendor"])
        for r in conn.execute(
            "SELECT ip, os_guess, mac_vendor FROM scan_history "
            "WHERE scan_time = ? AND scan_ok = 1", (latest["t"],)
        )
    }


def classify_all(conn) -> dict[str, dict]:
    """全部資產的平台＋角色，key 是 asset_serial。給清單頁與統計共用同一份判定。"""
    guesses = scan_os_guesses(conn)
    roles = roles_by_ip(conn)
    out = {}
    for r in conn.execute(
        "SELECT asset_serial, ip, os, device_model FROM hardware"
    ).fetchall():
        out[r["asset_serial"]] = {
            "platform": platform_of_row(r, guesses),
            "roles": role_of(roles, r["ip"]),
        }
    return out
