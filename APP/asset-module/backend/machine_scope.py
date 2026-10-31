"""收集到的資料屬於「機器」，不屬於某一筆登記（2026-10-16）。

## 為什麼要有

同一台機器在 hardware 表常有好幾筆登記：CIA 清冊一台多筆（一個服務／VIP 一筆），
或存活掃描（dynassets）對不上 CIA 正本時自己開的 `DYN-` 空殼。
「同一台」的判斷全站只有一把尺：`system_stats.machine_key`（主機名＋IP），
展開成序號走 `manage_state.same_machine_serials`（同生命週期才算）。

以前收集與顯示**各用各的鍵**，於是 2026-10-16 公司機實際看到：

- 收集器指定單台時會展開成同一台的每一筆序號，**同一個 IP 被 SSH 收了好幾次**，
  回報的「收到 N 筆」是 N × 登記筆數（軟體 1054＝527×2、服務 34＝17×2、帳號 48＝24×2）；
- 每收一次就把資料列的 asset_serial 改寫成「這一次的序號」，最後一次贏——
  服務／帳號分頁只認 asset_serial，輸掉的那一筆登記就顯示 0。

這支把兩件事收斂成一套：

1. `dedupe_targets`：收集時同一個 IP 只收一次（一台機器一次 SSH）。
2. `scope` / `scope_where`：顯示時「這台」＝同一台的全部序號，加上這個 IP 收到的資料。
   上方「本次收到」與下方分頁「目前在庫」都用這一套，不再各算各的。

⚠️ 只統一「看的口徑」，**不合併、不刪任何一筆登記**（合併是人工決定，見
AI/方案_重複資產合併_2026-09-24.md）。
"""
from __future__ import annotations


def _ip_of(hardware) -> str:
    try:
        return (hardware["ip"] or "").strip()
    except (IndexError, KeyError, TypeError):
        return ""


def scope(conn, asset_serial: str, hardware=None) -> dict:
    """這筆登記所屬「這台」的範圍：{serials: [...], ip: str|None, ips: [...]}。

    · serials：同一台（machine_key、同生命週期）的全部登記
    · ips：這些登記的**全部** IP（2026-10-28）。以前只放這一筆自己的 IP：資料掛在另一筆的 IP 上
      （例如序號是已併回的 DYN- 空殼、只剩 IP 對得到），同一台的另一筆登記就看到 0
      （verify_numbers「同一台多筆登記每筆數字一樣」在 221 抓到 SECSVR018-145TT）。
    · ip：這一筆自己的第一個 IP（相容舊呼叫端）。
    IP 只有在這筆不是退役登記時才拿來放寬——IP 會回收給新機器，報廢那筆不該看到現在這台收到的東西
    （同一台的其他登記一定同生命週期，見 manage_state.same_machine_serials）。
    """
    import manage_state

    if hardware is None:
        hardware = conn.execute(
            "SELECT ip, asset_status FROM hardware WHERE asset_serial = ?",
            (asset_serial,)).fetchone()
    serials = manage_state.same_machine_serials(conn, asset_serial) or [asset_serial]
    if asset_serial not in serials:
        serials = [asset_serial, *serials]
    try:
        status = (hardware["asset_status"] or "").strip() if hardware is not None else ""
    except (IndexError, KeyError):
        status = ""
    own = manage_state.ips_of(_ip_of(hardware)) if hardware is not None else []
    ips: list[str] = []
    if status not in manage_state.RETIRED_STATUS:
        ips = list(own)
        others = [x for x in serials if x != asset_serial]
        if others:
            ph = ",".join("?" for _ in others)
            for (raw,) in conn.execute(f"SELECT ip FROM hardware WHERE asset_serial IN ({ph})", others):
                for ip in manage_state.ips_of(raw):
                    if ip not in ips:
                        ips.append(ip)
    return {"serials": serials, "ip": own[0] if own and ips else None, "ips": ips}


def scope_where(sc: dict, alias: str = "") -> tuple[str, list]:
    """把 scope 變成 SQL 條件（含括號），給收集資料表（host_package／host_service／host_account）用。"""
    p = f"{alias}." if alias else ""
    ph = ",".join("?" for _ in sc["serials"])
    sql = f"{p}asset_serial IN ({ph})"
    params: list = list(sc["serials"])
    ips = sc.get("ips")
    if ips is None:                      # 舊形狀（只有 ip）
        ips = [sc["ip"]] if sc.get("ip") else []
    if ips:
        sql += f" OR {p}ip IN ({','.join('?' for _ in ips)})"
        params.extend(ips)
    return f"({sql})", params


#: 同一台的「同一個東西」：同一台用兩個 IP 各收一次時，每樣東西會存兩列（一個 IP 一列）。
#: 這台的數字與清單一律依這把鍵去重——一台機器的套件／服務／帳號不會因為有兩個 IP 就變兩倍
#: （verify_numbers 在 221 抓到 SECSVR018-064T：606 個套件被算成 1212）。
ITEM_KEYS = {
    "host_package": ("name", "version", "arch"),
    "host_service": ("proto", "port", "bind_addr"),
    "host_account": ("username",),
}


def distinct_count(conn, table: str, where: str, params: list) -> int:
    """這台目前在庫（gone_at IS NULL）的不重複筆數（ITEM_KEYS）。"""
    cols = ", ".join(ITEM_KEYS[table])
    r = conn.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM {table} WHERE gone_at IS NULL AND {where} "
                     f"GROUP BY {cols})", params).fetchone()
    return r[0] if r else 0


def dedupe_rows(rows: list[dict], table: str, prefer_ip: str | None = None) -> list[dict]:
    """清單依 ITEM_KEYS 去重：留這一筆自己 IP 收到的那列，沒有就留最近看到的；順序照原本。
    另外帶 seen_ips（這樣東西在這台的哪幾個 IP 上看到），不把資訊丟掉。"""
    keys = ITEM_KEYS[table]
    best: dict[tuple, dict] = {}
    ips: dict[tuple, list] = {}
    order: list[tuple] = []
    for r in rows:
        k = tuple(r.get(c) for c in keys)
        ips.setdefault(k, [])
        if r.get("ip") and r["ip"] not in ips[k]:
            ips[k].append(r["ip"])
        cur = best.get(k)
        if cur is None:
            order.append(k)
            best[k] = r
            continue
        rank = (r.get("ip") == prefer_ip, r.get("last_seen") or "")
        if rank > (cur.get("ip") == prefer_ip, cur.get("last_seen") or ""):
            best[k] = r
    out = []
    for k in order:
        d = dict(best[k])
        d["seen_ips"] = sorted(ips[k])
        out.append(d)
    return out


def machine_packages(conn, asset_serial: str, hardware) -> list:
    """資產明細「軟體」分頁：這台目前在庫的套件（host_package，未消失），依 ITEM_KEYS 去重。"""
    w, params = scope_where(scope(conn, asset_serial, hardware))
    return conn.execute(
        "SELECT name, version, arch, MIN(vendor) AS vendor, MIN(source) AS source, "
        "MIN(first_seen) AS first_seen, MAX(last_seen) AS last_seen "
        f"FROM host_package WHERE gone_at IS NULL AND {w} "
        "GROUP BY name, version, arch "
        "ORDER BY name COLLATE NOCASE, version", params).fetchall()


def _rep_rank(serial: str, only_serial: str | None) -> tuple:
    import system_report

    return (serial != only_serial, system_report.is_off_book(serial), serial or "")


def dedupe_targets(rows: list[dict], only_serial: str | None = None) -> list[dict]:
    """收集目標同一個 IP 只留一筆（一台機器只 SSH 一次）。

    代表序號：指定的那一筆優先（使用者在哪一頁按收集，診斷紀錄就落在哪一頁）；
    否則 CIA 正本優先於 DYN-/VC-/AUTO-；再取最小序號——每次挑的一樣。
    顯示端用 scope 看整台，所以代表是誰不影響數字。保留原本的先後順序。
    """
    best: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        ip = (r.get("ip") or "").strip()
        if ip not in best:
            order.append(ip)
            best[ip] = r
        elif _rep_rank(r["asset_serial"], only_serial) < _rep_rank(best[ip]["asset_serial"], only_serial):
            best[ip] = r
    return [best[ip] for ip in order]


def same_ip_records(conn, asset_serial: str, hardware) -> list[dict]:
    """同一個 IP 的其他登記（不含自己）。給明細頁提示「這個 IP 另有一筆資產紀錄」。

    same_machine＝跟這筆是不是同一台（machine_key 相同且同生命週期）；
    off_book＝掃描／vCenter／系統自動建立的（不是 CIA 正本）；
    no_business＝業務欄位全空（定義同 dyn_shell.no_business）。
    """
    import dyn_shell
    import manage_state
    import system_report

    ip = _ip_of(hardware)
    if not ip or ip == "0.0.0.0":
        return []
    siblings = set(manage_state.same_machine_serials(conn, asset_serial) or [])
    cols = ", ".join(dyn_shell.BUSINESS)
    out = []
    for r in conn.execute(
            f"SELECT asset_serial, hostname, asset_name, {cols} FROM hardware "
            "WHERE trim(ip) = ? AND asset_serial != ? ORDER BY asset_serial", (ip, asset_serial)):
        d = dict(r)
        d["same_machine"] = d["asset_serial"] in siblings
        d["off_book"] = system_report.is_off_book(d["asset_serial"])
        d["no_business"] = dyn_shell.no_business(d)
        out.append(d)
    return out


def self_flags(hardware) -> dict:
    """目前這筆自己是不是掃描建的空殼（給明細頁標示，同 same_ip_records 的定義）。"""
    import dyn_shell
    import system_report

    d = dict(hardware)
    return {"off_book": system_report.is_off_book(d.get("asset_serial")),
            "no_business": dyn_shell.no_business(d)}
