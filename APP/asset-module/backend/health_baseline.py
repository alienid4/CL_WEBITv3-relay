"""主機自我檢查——健康基準（批次 2，2026-09-22）。

grill 定案：
- **純排程、每週六**（離峰）對已納管主機收一份健康基準，存進 health_snapshot。
- 保留最近 N 份，看**序列變化點**（解掉「週六那份剛好已經半殘」的問題）。
- 事發時的即時健檢跟**最近一份 baseline** 比，做**方向性 diff**：
  baseline 有、現在**消失**的監聽 port＝紅（服務掉了）；**新增**＝只提示（多半合法上線）。
  絕對閾值（磁碟/記憶體/load…）獨立於 baseline，已在批次 1 的 health_probe 判過。
- 報告標明「基準取自 上週六 HH:MM」（新鮮度）。

刻意獨立成一支模組，不塞進 health_probe.py（那支批次 1/3 已很滿、且多人在動）。
"""
from __future__ import annotations

import json

import db

SNAP_KEEP = 8                      # 每台留最近幾份（≈ 兩個月的週快照）
SAT_HOUR = 3                       # 週六幾點跑（離峰）
DISK_JUMP = 10                     # 磁碟使用率跨基準漲多少個百分點才標出來
_LAST_RUN_KEY = "health_baseline_last_run"     # app_settings：上次週基準跑完的日期


def _ports_of(result: dict) -> list[int]:
    """健檢結果裡的監聽 port 清單（去重排序）。這是穩態、適合跟基準硬比。"""
    ports = set()
    for p in result.get("ports") or []:
        try:
            ports.add(int(p.get("port")))
        except (TypeError, ValueError):
            continue
    return sorted(ports)


def _disks_of(result: dict) -> dict:
    """{掛載點: 使用率%}，看跨基準的漲幅（絕對閾值批次 1 已判，這裡只看趨勢）。"""
    return {d["mount"]: d["use_pct"] for d in (result.get("disks") or []) if "mount" in d}


def save_snapshot(conn, result: dict, source: str = "weekly") -> None:
    """存一筆快照並修剪到每台最多 SNAP_KEEP 份。只存穩態＋數值。"""
    ip = result.get("ip")
    if not ip:
        return
    conn.execute(
        "INSERT INTO health_snapshot (ip, taken_at, source, overall, reachable, "
        "ports_json, disks_json, mem_pct, load_per) VALUES (?,?,?,?,?,?,?,?,?)",
        (ip, db._now_local(), source, result.get("overall"),
         1 if result.get("reachable") else 0,
         json.dumps(_ports_of(result)), json.dumps(_disks_of(result)),
         (result.get("mem") or {}).get("used_pct"),
         (result.get("load") or {}).get("per_cpu")),
    )
    # 修剪：留最新的 SNAP_KEEP 份，其餘刪掉（用 id 由大到小，跨天也穩）
    conn.execute(
        "DELETE FROM health_snapshot WHERE ip = ? AND id NOT IN ("
        "SELECT id FROM health_snapshot WHERE ip = ? ORDER BY id DESC LIMIT ?)",
        (ip, ip, SNAP_KEEP),
    )
    conn.commit()


def latest_baseline(conn, ip: str) -> dict | None:
    """某台最近一份快照（不分 source；週基準與手動釘的都算）。"""
    r = conn.execute(
        "SELECT taken_at, source, overall, reachable, ports_json, disks_json, mem_pct, load_per "
        "FROM health_snapshot WHERE ip = ? ORDER BY id DESC LIMIT 1", (ip,)
    ).fetchone()
    return dict(r) if r else None


def history(conn, ip: str, limit: int = SNAP_KEEP) -> list[dict]:
    """某台的快照序列（舊→新），給趨勢/變化點用。"""
    rows = conn.execute(
        "SELECT taken_at, source, overall, reachable, mem_pct, load_per, ports_json "
        "FROM health_snapshot WHERE ip = ? ORDER BY id DESC LIMIT ?", (ip, limit)
    ).fetchall()
    out = []
    for r in reversed(rows):
        d = dict(r)
        try:
            d["port_count"] = len(json.loads(d.pop("ports_json") or "[]"))
        except (ValueError, TypeError):
            d["port_count"] = None
        out.append(d)
    return out


def diff(baseline: dict | None, current: dict) -> dict | None:
    """把即時健檢跟最近基準比，回方向性 diff。沒有基準回 None（畫面顯示「尚無基準」）。

    **方向性**：baseline 有、現在消失的 port＝紅（服務可能掉了）；新增＝只提示。
    磁碟只回漲幅（絕對紅黃在批次 1 已判，這裡不重複判燈，避免基準太舊生假紅）。
    """
    if not baseline:
        return None
    try:
        base_ports = set(json.loads(baseline.get("ports_json") or "[]"))
        base_disks = json.loads(baseline.get("disks_json") or "{}")
    except (ValueError, TypeError):
        base_ports, base_disks = set(), {}
    cur_ports = set(_ports_of(current))
    cur_disks = _disks_of(current)
    missing = sorted(base_ports - cur_ports)      # 消失＝可疑（紅）
    added = sorted(cur_ports - base_ports)         # 新增＝提示
    disk_jumps = []
    for mount, base_pct in base_disks.items():
        cur_pct = cur_disks.get(mount)
        if cur_pct is not None and cur_pct - base_pct >= DISK_JUMP:
            disk_jumps.append({"mount": mount, "from": base_pct, "to": cur_pct,
                               "delta": cur_pct - base_pct})
    # 方向性判燈：只有「消失的 port」才升級成紅（服務掉了）；新增/漲幅不升級
    level = "red" if missing else "green"
    return {
        "baseline_at": baseline.get("taken_at"),
        "baseline_source": baseline.get("source"),
        "missing_ports": missing, "new_ports": added,
        "disk_jumps": disk_jumps, "level": level,
    }


# ---------- 週六排程 ----------
def _target_ips(conn) -> list[str]:
    """已納管、可收集的主機 IP（單一來源：account_inventory.list_collectable_hosts）。"""
    import account_inventory
    return [h["ip"] for h in account_inventory.list_collectable_hosts(conn)
            if h.get("ip") and not h.get("excluded")]


def run_weekly_baseline(conn, key_path: str | None = None, _check_batch=None) -> dict:
    """對已納管主機收一輪、每台存一份 source='weekly'。回摘要。"""
    import health_probe
    import manage_state

    check_batch = _check_batch or health_probe.check_batch
    ips = _target_ips(conn)
    if not ips:
        db.set_setting(conn, _LAST_RUN_KEY, db._now_local()[:10])
        return {"targets": 0, "saved": 0}

    def _platform_of(ip: str) -> str:
        try:
            return manage_state.collect_platform_of(conn, ip)
        except Exception:  # noqa: BLE001
            return "linux"

    results = check_batch(ips, platform_of=_platform_of, key_path=key_path)
    saved = 0
    for r in results:
        save_snapshot(conn, r, source="weekly")
        saved += 1
    db.set_setting(conn, _LAST_RUN_KEY, db._now_local()[:10])
    return {"targets": len(ips), "saved": saved}


def baseline_due(conn, now) -> bool:
    """週六、過了 SAT_HOUR、且本 ISO 週還沒跑過 → 該跑了。"""
    if now.weekday() != 5 or now.hour < SAT_HOUR:      # 5 = 週六
        return False
    last = db.get_setting(conn, _LAST_RUN_KEY, "") or ""
    if not last:
        return True
    try:
        import datetime as _dt
        last_d = _dt.date.fromisoformat(last[:10])
    except ValueError:
        return True
    # 同一 ISO 週（年＋週號）就算跑過了，不重跑
    return last_d.isocalendar()[:2] != now.date().isocalendar()[:2]
