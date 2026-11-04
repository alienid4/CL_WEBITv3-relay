"""6.1 納管作業（2.x 第三批）的唯讀彙整：新設備上線（逐台 5 步）、納管進度（覆蓋率對目標）。

只讀不寫，不碰納管／收集邏輯（onboard_engine、win_onboard、collect_dispatch 一律不動）：
· 新設備上線＝device_lifecycle.compute（舊 4-10 同一份）的前 4 步＋上線前檢查表（golive_check）是否已通過。
  藍圖第 5 步是「上線檢查」，所以第 5 步看 golive，不看 lifecycle 的「收集正常」。
· 納管進度＝2.1 主機查詢同一份 v2_hosts.machines（「已納管」與 2.1 頁籤同一個數字），依作業系統大類分；
  分母＝運作中（偵測狀態，existence 計入台數）的伺服器作業系統主機。目標百分比在系統設定 onboard_target_pct（預設 80）。
· 連線涵蓋率＝observed_deps.coverage（連線推論頁同一支），依作業系統大類分。
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any

#: 可以納管的作業系統大類（網路設備、儲存、虛擬化平台、管理介面各有自己的收集方式，不算進納管覆蓋率）
SERVER_FAMILIES = ("Linux", "Windows", "AIX", "IBM i")
STEPS = [("registered", "CIA 登記"), ("racked", "上架"), ("scanned", "網路偵測"), ("onboarded", "納管"), ("golive", "上線檢查")]
DEFAULT_TARGET = 80.0
NEW_DAYS = 90


def target_pct(conn: sqlite3.Connection) -> float:
    import db
    try:
        v = float(db.get_setting(conn, "onboard_target_pct", str(DEFAULT_TARGET)) or DEFAULT_TARGET)
    except (TypeError, ValueError):     # 設定值不是數字：用預設，畫面會寫出目前用的值
        v = DEFAULT_TARGET
    return v if 0 < v <= 100 else DEFAULT_TARGET


def _golive(conn) -> dict[str, str]:
    try:
        return {r["asset_serial"]: r["status"] for r in conn.execute("SELECT asset_serial, status FROM golive_check")}
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        return {}


def new_devices(conn: sqlite3.Connection, now: _dt.datetime | None = None) -> dict[str, Any]:
    """逐台 5 步（CIA 登記 → 上架 → 網路偵測 → 納管 → 上線檢查）。退役／報廢／豁免不列。
    is_new＝登記或第一次被掃到在 NEW_DAYS 天內。"""
    import device_lifecycle

    now = now or _dt.datetime.now()
    lc = device_lifecycle.compute(conn, now)
    gl = _golive(conn)
    exits = {k for k, _ in device_lifecycle.EXITS}
    cut = (now - _dt.timedelta(days=NEW_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for r in lc["rows"]:
        if r["stage"] in exits:
            continue
        g = gl.get(r["serial"])
        steps = {k: bool(r["done"].get(k)) for k, _ in STEPS[:4]}
        steps["golive"] = g == "passed"
        first = min([x for x in (r.get("registered_at"), r.get("first_scan_at")) if x] or [""]) or None
        nxt = next((k for k, _ in STEPS if not steps[k]), None)
        rows.append({"key": r["key"], "serial": r["serial"], "hostname": r["hostname"], "ip": r["ip"], "is_vm": r["is_vm"],
                     "room": r["room"], "systems": r["systems"], "unit": r["unit"], "steps": steps,
                     "golive_status": g or "", "steps_done": sum(steps.values()), "next": nxt,
                     "next_label": dict(STEPS).get(nxt, "五步都完成"), "first_seen": first,
                     "is_new": bool(first and first >= cut), "stuck_days": r.get("stuck_days")})
    counts = {k: sum(1 for x in rows if x["next"] == k) for k, _ in STEPS}
    counts["done"] = sum(1 for x in rows if x["next"] is None)
    return {"steps": [{"key": k, "label": v} for k, v in STEPS], "new_days": NEW_DAYS, "rows": rows,
            "total": len(rows), "new_total": sum(1 for x in rows if x["is_new"]), "counts": counts}


def progress(conn: sqlite3.Connection) -> dict[str, Any]:
    """納管覆蓋率（依作業系統大類）＋連線涵蓋率。每格帶下鑽清單（keys），數字＝清單長度。"""
    import observed_deps
    import v2_hosts

    ms = v2_hosts.machines(conn)
    fam: dict[str, dict] = {}
    for r in ms:
        f = (r["badge"] or {}).get("os_class") or "未知"
        b = fam.setdefault(f, {"family": f, "server": f in SERVER_FAMILIES, "all": [], "online": [], "onboarded": [], "online_not_onboarded": []})
        b["all"].append(r["key"])
        on = r["status"]["key"] == "online"
        if on:
            b["online"].append(r["key"])
        if r["onboarded"]:
            b["onboarded"].append(r["key"])
        if on and not r["onboarded"]:
            b["online_not_onboarded"].append(r["key"])

    def pct(a, b):
        return round(a * 100.0 / b, 1) if b else 0.0
    rows = []
    for f in sorted(fam, key=lambda x: (not fam[x]["server"], x)):
        b = fam[f]
        # 已納管但偵測狀態不是運作中的（少見，例如剛納管、存在可信度還沒到）：分子只算運作中的，分子 ≤ 分母
        on_onb = len(b["online"]) - len(b["online_not_onboarded"])
        rows.append({**b, "n_all": len(b["all"]), "n_online": len(b["online"]), "n_onboarded": len(b["onboarded"]),
                     "n_online_onboarded": on_onb, "pct": pct(on_onb, len(b["online"]))})
    srv = [r for r in rows if r["server"]]
    num, den = sum(r["n_online_onboarded"] for r in srv), sum(r["n_online"] for r in srv)
    tgt = target_pct(conn)
    cov = observed_deps.coverage(conn)
    return {"target_pct": tgt, "server_families": list(SERVER_FAMILIES), "rows": rows,
            "server": {"online": den, "onboarded": num, "pct": pct(num, den),
                       "gap_to_target": max(0, -(-int(den * tgt) // 100) - num)},
            "onboarded_total": sum(r["n_onboarded"] for r in rows),
            "observed": {"days": cov["days"], "total": cov["total"], "observed_count": cov["observed_count"], "pct": cov["pct"],
                         "by_family": [{"family": x["family"], "total": x["total"], "observed_count": x["observed_count"], "pct": x["pct"],
                                        "missing": x["missing"]} for x in cov["by_family"]],
                         "cumulative_days": cov["cumulative_days"], "month_end_crossed": cov["month_end_crossed"]}}


DRILL_KINDS = {"all": "全部登記", "online": "運作中", "onboarded": "已納管", "online_not_onboarded": "運作中、未納管"}


def drill(conn: sqlite3.Connection, family: str, kind: str) -> dict[str, Any]:
    """納管進度某一格的清單（同一份 progress 計算，列數＝格子數字）。"""
    import v2_hosts

    if kind not in DRILL_KINDS:
        raise ValueError(f"不認得的清單：{kind}")
    row = next((r for r in progress(conn)["rows"] if r["family"] == family), None)
    if row is None:
        raise LookupError(f"沒有這個作業系統大類：{family}")
    keys = set(row[kind])
    ms = [r for r in v2_hosts.machines(conn) if r["key"] in keys]
    return {"family": family, "kind": kind, "label": f"{family} {DRILL_KINDS[kind]}",
            "rows": [{"key": r["key"], "name": r["name"], "ip": r["ip"], "serial": r["serial"], "type": r["type"],
                      "status_label": r["status_label"], "onboarded": "是" if r["onboarded"] else "否",
                      "last_seen": r["last_seen"], "system": r["system"]} for r in ms]}


# ===== 6.1 待納管的「存活」欄（2.007.13）=====
# 使用者：「本來預設你就要先測過有沒有通；偵測存活應該是我想重新再偵測才會按」。
# 不另外對機器發連線（不多踩 SOC），只整理系統已經有的三種證據，取時間最新的一筆：
#   · 偵測存活（人按的）：alive_check_item；
#   · 網路掃描：scan_history 有這個 IP＝那次掃描看到它活著；這個 IP 所在網段之後又有成功的掃描、卻沒看到它＝不通；
#   · 收集（開始收集）：有開放埠＝那時候是通的。
def _ts(v: Any) -> str:
    return str(v or "")[:19].replace("T", " ")


def _place(conn: sqlite3.Connection, rows: list[dict]) -> dict[str, tuple[str | None, str | None]]:
    """每個位址的 (環境原值, 機房原值)：已登記的資產用它登記的，沒登記的看它落在網段配置表哪一段（取最小的那一段）。
    兩個值各自找（資產只登記了環境、沒登記機房，機房還是可以看網段）。找不到就是 None。"""
    import ipaddress

    ips = sorted({r["ip"] for r in rows if r.get("ip")})
    if not ips:
        return {}
    segs: list[tuple[int, int, str | None, str | None]] = []
    try:
        for s in conn.execute("SELECT net_start, net_end, environment, location FROM network_segment "
                              "WHERE net_start IS NOT NULL AND net_end IS NOT NULL"):
            segs.append((int(s[0]), int(s[1]), (s[2] or "").strip() or None, (s[3] or "").strip() or None))
    except sqlite3.OperationalError:     # 還沒匯網段配置表：沒有網段這項依據，不是錯
        pass
    segs.sort(key=lambda x: x[1] - x[0])     # 小段優先（/24 比 /16 準）
    asset: dict[str, tuple[str | None, str | None]] = {}
    for i in range(0, len(ips), 400):
        part = ips[i:i + 400]
        ph = ",".join("?" * len(part))
        for ip, env, loc in conn.execute(
                f"SELECT ip, environment, physical_location FROM hardware WHERE ip IN ({ph}) ORDER BY rowid", part):
            e0, l0 = asset.get(ip, (None, None))
            asset[ip] = (e0 or (env or "").strip() or None, l0 or (loc or "").strip() or None)
    out: dict[str, tuple[str | None, str | None]] = {}
    for ip in ips:
        env, loc = asset.get(ip, (None, None))
        if not (env and loc):
            try:
                n = int(ipaddress.IPv4Address(ip))
            except ValueError:
                n = None
            if n is not None:
                # 環境與機房各自取「第一個有填的最小段」：小段沒填環境就往上一層找，不因為小段缺一個值就兩個都放棄
                for a, b, e, l in segs:
                    if a <= n <= b:
                        env = env or e
                        loc = loc or l
                        if env and loc:
                            break
        out[ip] = (env, loc)
    return out


def environment_map(conn: sqlite3.Connection, rows: list[dict], place: dict | None = None) -> dict[str, str]:
    """待納管每個位址的環境別（2.007.48，使用者：「環境可選 正式 測試」）。分組走 manage_state.group_environment
    （environment_groups.json，UAT／DEV／OA 併進測試）——跟全站同一把尺；找不到＝「未分類」。"""
    import manage_state

    return {ip: (manage_state.group_environment(env) if env else "未分類") for ip, (env, _loc) in (place if place is not None else _place(conn, rows)).items()}


def room_map(conn: sqlite3.Connection, rows: list[dict], place: dict | None = None) -> dict[str, str]:
    """待納管每個位址的機房（板橋／敦南…，2.007.48，使用者：「想辦法找出機房位置」）：跟機櫃、系統地圖同一個機房寫法
    （blast_radius.map_room，去掉 01_ 編號、分公司併成一個）；找不到＝「未知」。"""
    import blast_radius

    return {ip: (blast_radius.map_room(loc) if loc else "未知") for ip, (_env, loc) in (place if place is not None else _place(conn, rows)).items()}


def alive_map(conn: sqlite3.Connection, rows: list[dict]) -> dict[str, dict]:
    import ipaddress

    ips = sorted({r["ip"] for r in rows if r.get("ip")})
    if not ips:
        return {}
    out: dict[str, dict] = {}

    def put(ip: str, state: str, at: str, by: str) -> None:
        if at and (ip not in out or at > out[ip]["at"]):
            out[ip] = {"state": state, "at": at, "by": by}

    for r in rows:   # 收集時有開放埠＝那時候通
        if r.get("ip") and r.get("open_ports"):
            put(r["ip"], "通", _ts(r.get("collected_at")), "收集")
    seen: dict[str, str] = {}
    for i in range(0, len(ips), 500):
        part = ips[i:i + 500]
        ph = ",".join("?" * len(part))
        try:
            for ip, t in conn.execute(f"SELECT ip, MAX(scan_time) FROM scan_history WHERE ip IN ({ph}) GROUP BY ip", part):
                seen[ip] = _ts(t)
                put(ip, "通", _ts(t), "網路掃描")
        except sqlite3.OperationalError:    # 沒有掃描紀錄表（新裝機）：沒有這項證據，不是錯；偵測存活照樣看
            pass
        try:
            for x in conn.execute(
                    f"SELECT ip, result, r.finished_at, r.started_at FROM alive_check_item i JOIN alive_check_run r ON r.id = i.run_id "
                    f"WHERE ip IN ({ph})", part):
                st = {"alive": "通", "no_response": "不通"}.get(x[1], "無法判斷")
                put(x[0], st, _ts(x[2] or x[3]), "偵測存活")
        except sqlite3.OperationalError:    # 還沒人按過偵測存活：表不存在，沒有這項證據
            pass
    # 網段之後有成功掃描卻沒看到＝不通
    cov = []
    try:
        for c, t in conn.execute("SELECT cidr, MAX(scan_time) FROM scan_coverage WHERE ok = 1 GROUP BY cidr"):
            try:
                cov.append((ipaddress.ip_network(c, strict=False), _ts(t)))
            except ValueError:   # 這一段網段格式壞：只略過這一段，不影響其他網段
                continue
    except sqlite3.OperationalError:   # 沒有涵蓋紀錄表：不推論「不通」
        pass
    for ip in ips:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        last = max((t for n, t in cov if a in n), default="")
        if last and last > seen.get(ip, ""):
            put(ip, "不通", last, "網路掃描")
    return out
