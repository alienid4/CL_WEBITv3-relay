"""PowerVM 可觀測架構圖（3.2 架構圖「PowerVM」頁籤，第一版）。

定位：只畫「HMC 確實回報過的事」——HMC → 實體機（managed system）→ LPAR，加上 HMC 回報的 CPU／記憶體。
VIOS／網路／SAN／儲存四層**保留分層框、標「未取得（尚未收集）」**：不由名稱、CPU、記憶體猜 VIOS 或連線；
SAN zone 不等於實際存取，不畫成連線。

純讀本機 hmc_console 表（HMC 收集頁收集後留下的結果）：**不連 HMC、不排程、不寫任何表**。
未知／零值／查無資料要分得出來：欄位沒回報＝None（畫面寫「未取得」），HMC 回報 0＝0。
示範資料（demo_overview）是記憶體內獨立資料集，名稱 DEMO-、IP 10.99.x，不碰資料庫。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

DEFAULT_STALE_DAYS = 14

#: 分層（由上而下）。available＝第一版有資料可畫；其餘一律「未取得」，不得推測。
LAYERS = [
    {"key": "hmc", "label": "HMC", "available": True},
    {"key": "frame", "label": "實體機", "available": True},
    {"key": "vios", "label": "VIOS", "available": False, "reason": "尚未收集（HMC 的 LPAR 類型欄不等於 VIOS 對應關係，不據此推測）"},
    {"key": "lpar", "label": "LPAR", "available": True},
    {"key": "network", "label": "網路", "available": False, "reason": "尚未收集（不由名稱或 IP 猜連線）"},
    {"key": "san", "label": "SAN", "available": False, "reason": "尚未收集（SAN zone 不等於實際存取，不畫成連線）"},
    {"key": "storage", "label": "儲存", "available": False, "reason": "尚未收集"},
]


def _num(v):
    """HMC 回報的字串數字 → float；沒回報／空字串／不是數字 → None（未取得）。0 保持 0。"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int_or_none(x):
    return None if x is None else int(round(x))


def _age_days(collected_at: str | None, now: datetime) -> float | None:
    if not collected_at:
        return None
    try:
        t = datetime.strptime(collected_at[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return max(0.0, (now - t).total_seconds() / 86400)


def _status(collected_at: str | None, now: datetime, stale_days: int, parse_error: bool = False) -> dict:
    """狀態只講資料新舊：fresh（綠）／stale（過期）／unknown（未取得，琥珀）／error（資料壞）。"""
    if parse_error:
        return {"code": "error", "label": "資料無法解析", "age_days": None}
    age = _age_days(collected_at, now)
    if age is None:
        return {"code": "unknown", "label": "未取得收集時間", "age_days": None}
    if age > stale_days:
        return {"code": "stale", "label": f"資料已 {int(age)} 天未更新", "age_days": round(age, 1)}
    return {"code": "fresh", "label": "有資料", "age_days": round(age, 1)}


def _build_lpars(raw_lpars, console_ip: str, sys_key: str) -> list[dict]:
    out, seen = [], set()
    for lp in raw_lpars or []:
        if not isinstance(lp, dict):
            continue
        ident = lp.get("lpar_id") or lp.get("name")
        if ident is None or str(ident) == "":
            continue
        if str(ident) in seen:          # 同一台同一個 LPAR 只畫一次
            continue
        seen.add(str(ident))
        pu, pc, mm = _num(lp.get("proc_units")), _num(lp.get("procs")), _num(lp.get("mem_mb"))
        out.append({
            "key": f"{console_ip}|{sys_key}|{ident}",
            "lpar_id": lp.get("lpar_id"), "name": lp.get("name"),
            "env": lp.get("env"),                 # HMC 回報的 lpar_env（aixlinux／vioserver／os400），原樣顯示、不據此畫 VIOS 關係
            "state": lp.get("state"), "os_version": lp.get("os_version"),
            "proc_units": pu, "procs": _int_or_none(pc), "mem_mb": _int_or_none(mm),
        })
    return out


def _build_systems(data: dict, console_ip: str) -> list[dict]:
    systems, seen = [], set()
    for s in data.get("systems") or []:
        if not isinstance(s, dict) or not s.get("name"):
            continue
        sys_key = s.get("serial") or s["name"]
        if sys_key in seen:                  # 同一個 HMC 內重複的實體機只畫一次
            continue
        seen.add(sys_key)
        lpars = _build_lpars(s.get("lpars"), console_ip, sys_key)
        known_cpu = [l["proc_units"] for l in lpars if l["proc_units"] is not None]
        known_mem = [l["mem_mb"] for l in lpars if l["mem_mb"] is not None]
        systems.append({
            "key": "|".join((console_ip, sys_key)), "frame_key": sys_key,
            "name": s["name"], "type_model": s.get("type_model"), "serial": s.get("serial"),
            "state": s.get("state"), "firmware": s.get("firmware"),
            "lpars": lpars, "lpar_count": len(lpars),
            # 只加總「有取得」的；沒取得的台數另列，畫面不可把合計當完整值
            "cpu_sum": round(sum(known_cpu), 2) if known_cpu else None,
            "cpu_unknown": sum(1 for l in lpars if l["proc_units"] is None),
            "mem_sum_mb": sum(known_mem) if known_mem else None,
            "mem_unknown": sum(1 for l in lpars if l["mem_mb"] is None),
        })
    return systems


def _assemble(console_rows: list[dict], now: datetime, stale_days: int, mode: str,
              serial_links: dict[str, str] | None = None) -> dict:
    serial_links = serial_links or {}
    consoles = []
    frames_seen, lpars_seen = set(), set()
    for r in console_rows:
        ip = r["ip"]
        parse_error, data = False, {}
        try:
            data = json.loads(r.get("data_json") or "{}")
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (ValueError, TypeError):
            parse_error, data = True, {}
        systems = []
        if not parse_error:
            try:
                systems = _build_systems(data, ip)
            except Exception:  # noqa: BLE001 - 合法 JSON 但欄位型別損毀：只標這台 HMC「資料無法解析」，不讓整頁 500
                parse_error, systems = True, []
        for s in systems:
            s["machine_link"] = serial_links.get(s["serial"]) if s["serial"] else None
            frames_seen.add(s["frame_key"])
            for l in s["lpars"]:
                lpars_seen.add((s["frame_key"], l["lpar_id"] or l["name"]))
        consoles.append({
            "ip": ip, "hostname": r.get("hostname"), "hmc_version": r.get("hmc_version"),
            "collected_at": r.get("collected_at"),
            "status": _status(r.get("collected_at"), now, stale_days, parse_error),
            "source": "HMC 唯讀指令（lssyscfg／lshwres）" if mode == "real" else "示範資料（模擬）",
            "systems": systems,
        })
    uniq: dict = {}
    for c in consoles:                      # 兩個 HMC 管同一台時，未取得統計只算一次（圖上兩邊都畫，數字不重複）
        for s in c["systems"]:
            for l in s["lpars"]:
                uniq.setdefault((s["frame_key"], l["lpar_id"] or l["name"]), l)
    lp_all = list(uniq.values())
    summary = {
        "consoles": len(consoles), "frames": len(frames_seen), "lpars": len(lpars_seen),
        "stale_consoles": sum(1 for c in consoles if c["status"]["code"] == "stale"),
        "error_consoles": sum(1 for c in consoles if c["status"]["code"] == "error"),
        "unknown_cpu_lpars": sum(1 for l in lp_all if l["proc_units"] is None),
        "unknown_mem_lpars": sum(1 for l in lp_all if l["mem_mb"] is None),
    }
    return {
        "mode": mode, "empty": not consoles, "stale_days": stale_days,
        "layers": LAYERS, "summary": summary, "consoles": consoles,
        "next_step": "到 6.2 設備收集的「HMC」收集一次，這裡才會有資料" if not consoles and mode == "real" else None,
    }


def overview(conn, now: datetime | None = None) -> dict:
    """正式模式：只讀 hmc_console。0 筆＝空狀態，不用示範補畫。"""
    from db import get_setting
    now = now or datetime.now()
    try:
        stale_days = int(get_setting(conn, "powervm_stale_days", str(DEFAULT_STALE_DAYS)) or DEFAULT_STALE_DAYS)
    except (TypeError, ValueError):
        stale_days = DEFAULT_STALE_DAYS
    rows = [dict(r) for r in conn.execute(
        "SELECT ip, hostname, hmc_version, data_json, collected_at FROM hmc_console ORDER BY ip")]
    links: dict[str, str] = {}
    for r in conn.execute("SELECT asset_serial FROM hardware WHERE asset_serial IS NOT NULL AND asset_serial != ''"):
        links[r[0]] = f"/v2/machine/{r[0]}"
    return _assemble(rows, now, stale_days, "real", links)


def demo_overview(now: datetime | None = None) -> dict:
    """示範資料：記憶體內獨立資料集，不碰資料庫，不進任何統計／下鑽／匯出。"""
    now = now or datetime.now()
    fresh = now.strftime("%Y-%m-%d %H:%M:%S")
    old = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    def lp(i, name, env, state, pu, procs, mem):
        d = {"name": name, "lpar_id": str(i), "env": env, "state": state, "os_version": "AIX 7.3"}
        if pu is not None:
            d["proc_units"] = pu
        if procs is not None:
            d["procs"] = procs
        if mem is not None:
            d["mem_mb"] = mem
        return d

    a = {"hmc_version": "V10 R1", "systems": [
        {"name": "DEMO-FRAME-01", "type_model": "9080-HEX", "serial": "DEMO0001", "state": "Operating", "firmware": "FW1010",
         "lpars": [lp(1, "DEMO-AIX-01", "aixlinux", "Running", "2.0", "4", "32768"),
                   lp(2, "DEMO-AIX-02", "aixlinux", "Running", "0", "0", "0"),               # 回報 0（不是未取得）
                   lp(3, "DEMO-AIX-03", "aixlinux", "Not Activated", None, None, None)]},    # 沒回報＝未取得
        {"name": "DEMO-FRAME-02", "type_model": "9080-HEX", "serial": "DEMO0002", "state": "Operating", "firmware": "FW1010",
         "lpars": [lp(1, "DEMO-AIX-11", "aixlinux", "Running", "1.5", "3", "16384")]}]}
    b = {"hmc_version": "V9 R2", "systems": [
        {"name": "DEMO-FRAME-03", "type_model": "9009-42A", "serial": "DEMO0003", "state": "Operating", "firmware": "FW950",
         "lpars": [lp(1, "DEMO-IBMI-01", "os400", "Running", "1.0", "2", "8192")]}]}
    rows = [
        {"ip": "10.99.0.11", "hostname": "DEMO-HMC-01", "hmc_version": a["hmc_version"], "data_json": json.dumps(a), "collected_at": fresh},
        {"ip": "10.99.0.12", "hostname": "DEMO-HMC-02", "hmc_version": b["hmc_version"], "data_json": json.dumps(b), "collected_at": old},
    ]
    return _assemble(rows, now, DEFAULT_STALE_DAYS, "demo")
