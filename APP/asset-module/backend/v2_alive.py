"""2.1 主機查詢「批次偵測存活」（2.041.97；舊版缺口 230 第 1 項）。

只做連線嘗試（ICMP＋TCP 22／445／3389／5985），不登入、不送指令、不驗證帳密——探測本體沿用 host_ping。
這一層負責「誰可以被偵測」，而且全部在伺服器端強制：

  · 只收 serials（資產序號），不收 IP、不收網段。序號對不回資產庫的、非現役（停用／報廢／閒置＝備查）的，
    列進「略過」並講原因；沒登記 IP、IP 格式不對、同 IP 已在本次對象的，也列進「略過」。
  · VMware 設備（ESXi 與 vCenter／VCSA）一律排除，沿用 manage_state.drop_vmware_targets（納管、收集同一個判定入口），
    列進「已排除」；跑在 VMware 上的客體 VM 不在此列。
  · 一次最多 MAX_SERIALS 個序號；整批有時間上限 BATCH_DEADLINE 秒，來不及的那幾台記「無法判斷」並說明，不假裝有結果。

報告沿用舊版 alive_check_run／alive_check_item（同一份歷史，舊頁也看得到）；略過與已排除另存 alive_run_extra。
保留策略：最近 host_ping.MAX_RUNS（200）份，寫新報告的同一交易內刪掉最舊的整份（三張表一起）；每份最多 MAX_SERIALS 列；清單預設顯示最近 20 份。

安全邊界（審查必修）：
  · 可探測位址白名單＝私有位址 10/8、172.16/12、192.168/16；loopback、link-local、multicast、unspecified、保留位址與公網一律略過。
  · 只偵測「現役」：資產狀態為使用中或待上線（明確白名單 ACTIVE_STATUS）。空白狀態只放行非 CIA 來源序號
    （lifecycle.NON_CIA_PREFIXES：帳外／掃描納入，本來就沒有 CIA 狀態）；CIA 序號空白、未知值、停用／報廢／閒置一律略過。
  · 全域互斥：同時只允許一批在跑（含上一批超過時間上限後仍在背景結束中的探測），否則 409。
  · 同一個 IP 在 REUSE_SECONDS（5 分鐘）內不重複探測（不論結果，含無法判斷），沿用上次結果並標「沿用」；逾時沒跑完的不算探測過。
"""
from __future__ import annotations

import ipaddress
import json
import threading
import time
from datetime import datetime

MAX_SERIALS = 200
#: 整批時間上限（秒）。200 台、20 條併行、每台最久約 10 秒 ≈ 100 秒；超過就不再等，剩下的記無法判斷
BATCH_DEADLINE = 150.0
#: 同一個 IP 這段時間內不重複探測（秒），沿用上次結果
REUSE_SECONDS = 300
_ALLOWED_NETS = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"), ipaddress.ip_network("192.168.0.0/16"))  # webit3:allow-private-ip（RFC1918 私有網段定義，不是實際位址）
#: 現役白名單（明確列出，不由其他集合推導：新增狀態值時預設不偵測）
ACTIVE_STATUS = frozenset({"使用中", "待上線"})
_RUN_LOCK = threading.Lock()
_RECENT: dict[str, tuple[float, dict]] = {}
_RECENT_LOCK = threading.Lock()


class Busy(Exception):
    """已有批次偵測在進行（或上一批的探測還在背景結束）。"""


def reset_cache() -> None:
    with _RECENT_LOCK:
        _RECENT.clear()


def ip_allowed(ip: str) -> tuple[bool, str]:
    """可不可以被探測：只允許私有位址 10/8、172.16/12、192.168/16。"""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False, "IP 格式不對"
    if a.version != 4 or not any(a in n for n in _ALLOWED_NETS):
        return False, "IP 不在可偵測範圍（只偵測內部私有位址 10.x、172.16–31.x、192.168.x；公網、本機、link-local、保留位址一律不探測）"
    return True, ""


def status_ok(serial: str, status: str | None) -> tuple[bool, str]:
    """現役白名單：使用中、待上線（沿用 lifecycle 的合法值，去掉退役值）。非 CIA 來源序號的空白狀態放行。"""
    import lifecycle

    st = (status or "").strip()
    if st in ACTIVE_STATUS:
        return True, ""
    if not st:
        if (serial or "").startswith(lifecycle.NON_CIA_PREFIXES):
            return True, ""
        return False, "資產狀態空白（CIA 登記沒有狀態，無法確認是現役）"
    if st in lifecycle.RETIRED_STATUS:
        return False, f"非現役（資產狀態：{st}），退役機器不偵測"
    return False, f"資產狀態「{st}」不是已知的現役狀態，不偵測"
SCOPE = "2.1 主機查詢・批次偵測存活"

STATE_ALIVE = "alive"
STATE_NO_RESPONSE = "no_response"
STATE_UNKNOWN = "unknown"
STATE_EXCLUDED = "excluded"
STATE_SKIPPED = "skipped"
STATE_LABEL = {STATE_ALIVE: "存活", STATE_NO_RESPONSE: "連不上", STATE_UNKNOWN: "無法判斷",
               STATE_EXCLUDED: "已排除（VMware 設備）", STATE_SKIPPED: "略過"}

EXTRA_SQL = """
CREATE TABLE IF NOT EXISTS alive_run_extra (
    run_id       INTEGER PRIMARY KEY,
    requested    INTEGER NOT NULL,
    skipped      TEXT NOT NULL,
    excluded     TEXT NOT NULL,
    deadline_hit INTEGER NOT NULL DEFAULT 0,
    ports        TEXT,
    still_running INTEGER NOT NULL DEFAULT 0,
    reused       INTEGER NOT NULL DEFAULT 0
)
"""


def plan(conn, serials: list[str]) -> dict:
    """把勾選的序號對回資產庫，分成「可偵測」「略過」「已排除（VMware）」。純讀，不連線。"""
    import manage_state
    import net_scan
    import ssh_probe

    seen_serial: set[str] = set()
    uniq: list[str] = []
    for s in serials:
        s = (s or "").strip()
        if s and s not in seen_serial:
            seen_serial.add(s)
            uniq.append(s)
    rows: dict[str, object] = {}
    for i in range(0, len(uniq), 500):
        part = uniq[i:i + 500]
        ph = ",".join("?" for _ in part)
        for r in conn.execute(f"SELECT asset_serial, hostname, ip, asset_status FROM hardware WHERE asset_serial IN ({ph})", tuple(part)):
            rows[r["asset_serial"]] = r

    skipped: list[dict] = []
    cand: list[dict] = []
    seen_ip: set[str] = set()

    def skip(sn, host, ip, why):
        skipped.append({"asset_serial": sn, "hostname": host, "ip": ip, "reason": why})

    for sn in uniq:
        r = rows.get(sn)
        if r is None:
            skip(sn, None, None, "查無此資產序號（只偵測資產庫已登記的主機）")
            continue
        host, raw_ip = r["hostname"], (r["ip"] or "").strip()
        ok, why = status_ok(sn, r["asset_status"])
        if not ok:
            skip(sn, host, raw_ip or None, why)
            continue
        if not raw_ip or raw_ip == "0.0.0.0":
            skip(sn, host, raw_ip or None, "沒有登記 IP")
            continue
        ips = manage_state.ips_of(raw_ip)
        if len(ips) != 1:
            skip(sn, host, raw_ip, "IP 欄不是單一有效位址（空白、格式不對或登記了多個），請先修正登記")
            continue
        try:
            ip, _ = ssh_probe.validate(ips[0], 22)
        except ValueError:
            skip(sn, host, raw_ip, "IP 格式不對")
            continue
        ok, why = ip_allowed(ip)
        if not ok:
            skip(sn, host, ip, why)
            continue
        if ip in seen_ip:
            skip(sn, host, ip, "同一個 IP 已在本次對象中（不重複偵測）")
            continue
        seen_ip.add(ip)
        cand.append({"asset_serial": sn, "hostname": host, "ip": ip})

    kept, excl = manage_state.drop_vmware_targets(conn, cand) if cand else ([], [])
    by_serial = {c["asset_serial"]: c for c in cand}
    excluded = [{"asset_serial": e["asset_serial"], "hostname": by_serial[e["asset_serial"]]["hostname"], "ip": e["ip"],
                 "reason": e["reason"]} for e in excl]
    return {"requested": len(uniq), "targets": kept, "skipped": skipped, "excluded": excluded}


def _ensure(conn) -> None:
    import host_ping

    host_ping._ensure_run_tables(conn)
    conn.execute(EXTRA_SQL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(alive_run_extra)")}
    for c in ("still_running", "reused"):
        if c not in cols:
            conn.execute(f"ALTER TABLE alive_run_extra ADD COLUMN {c} INTEGER NOT NULL DEFAULT 0")


def run(conn, serials: list[str], actor: str | None) -> dict:
    """偵測並存成報告。回傳報告（同 get_report）。呼叫端負責盤中確認票證與稽核入口。同時只允許一批在跑，否則丟 Busy。"""
    import host_ping

    if not _RUN_LOCK.acquire(blocking=False):
        raise Busy("已有偵測在進行，請等它結束後再試")
    try:
        if host_ping.inflight() > 0:
            raise Busy(f"上一批還有 {host_ping.inflight()} 個探測在背景結束中，請稍後再試")
        return _run_locked(conn, serials, actor)
    finally:
        _RUN_LOCK.release()


def _run_locked(conn, serials: list[str], actor: str | None) -> dict:
    import collect_log
    import host_ping
    import net_scan

    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    p = plan(conn, serials)
    if not p["targets"] and not p["excluded"] and not p["skipped"]:
        raise ValueError("沒有選任何主機")
    # 5 分鐘內探測過的 IP 不重複探測，沿用上次結果
    now = time.monotonic()
    fresh, reused = [], {}
    with _RECENT_LOCK:
        for t in p["targets"]:
            hit = _RECENT.get(t["ip"])
            if hit and now - hit[0] < REUSE_SECONDS:
                reused[t["ip"]] = (now - hit[0], hit[1])
            else:
                fresh.append(t)
    got, deadline_hit, running = ([], False, 0)
    if fresh:
        got, deadline_hit, running = host_ping.check_many(fresh, deadline=BATCH_DEADLINE, with_flag=True)
    by_ip = {r["ip"]: r for r in got}
    with _RECENT_LOCK:
        for r in got:
            if r.get("probed", True):   # 真的探測過的一律記下（含無法判斷）；逾時沒跑完的不記
                _RECENT[r["ip"]] = (time.monotonic(), {k: v for k, v in r.items() if k not in ("asset_serial", "hostname")})
        for k in [k for k, v in _RECENT.items() if time.monotonic() - v[0] >= REUSE_SECONDS]:
            del _RECENT[k]
    results: list[dict] = []
    for t in p["targets"]:
        if t["ip"] in reused:
            age, old = reused[t["ip"]]
            r = dict(old)
            r["verdict"] = f"沿用 {int(age // 60)} 分鐘前的偵測結果（5 分鐘內不重複探測）：{old['verdict']}"
            r["asset_serial"], r["hostname"] = t["asset_serial"], t["hostname"]
            results.append(r)
        else:
            results.append(by_ip[t["ip"]])
    summary = host_ping.summarize_batch(results)
    run_id = host_ping.save_run(conn, results, SCOPE, actor, started, commit=False)
    _ensure(conn)
    conn.execute("INSERT INTO alive_run_extra (run_id, requested, skipped, excluded, deadline_hit, ports, still_running, reused) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 (run_id, p["requested"], json.dumps(p["skipped"], ensure_ascii=False),
                  json.dumps(p["excluded"], ensure_ascii=False), 1 if deadline_hit else 0,
                  ",".join(str(x) for x in net_scan.PROBE_PORTS), running, len(reused)))
    host_ping.prune_runs(conn)
    conn.commit()
    collect_log.record(
        conn, "alive_check", None, summary["no_response"] == 0,
        f"2.1 批次偵測存活 要求 {p['requested']} 台：偵測 {summary['total']}（存活 {summary['alive']}、連不上 {summary['no_response']}、"
        f"無法判斷 {summary['unknown']}、沿用 {len(reused)}）、已排除 VMware {len(p['excluded'])}、略過 {len(p['skipped'])}",
        actor=actor, extra={"run_id": run_id, "scope": SCOPE, "summary": summary, "still_running": running,
                            "excluded": len(p["excluded"]), "skipped": len(p["skipped"])})
    return get_report(conn, run_id)


def _rows(run: dict, extra: dict) -> list[dict]:
    out: list[dict] = []
    for it in run["items"]:
        out.append({"asset_serial": it["asset_serial"], "hostname": it["hostname"], "ip": it["ip"], "state": it["result"],
                    "state_label": STATE_LABEL.get(it["result"], it["result_label"]), "evidence": it["evidence"],
                    "verdict": it["verdict"], "icmp_sent": it["icmp_sent"], "icmp_recv": it["icmp_recv"],
                    "avg_ms": it["avg_ms"], "open_ports": it["open_ports"] or ""})
    for key, st in (("excluded", STATE_EXCLUDED), ("skipped", STATE_SKIPPED)):
        for x in extra[key]:
            out.append({"asset_serial": x["asset_serial"], "hostname": x["hostname"], "ip": x["ip"], "state": st,
                        "state_label": STATE_LABEL[st], "evidence": "", "verdict": x["reason"], "icmp_sent": None,
                        "icmp_recv": None, "avg_ms": None, "open_ports": ""})
    return out


def get_report(conn, run_id: int) -> dict | None:
    import host_ping

    run = host_ping.get_run(conn, run_id)
    if run is None:
        return None
    _ensure(conn)
    r = conn.execute("SELECT * FROM alive_run_extra WHERE run_id = ?", (run_id,)).fetchone()
    extra = ({"requested": r["requested"], "skipped": json.loads(r["skipped"]), "excluded": json.loads(r["excluded"]),
              "deadline_hit": bool(r["deadline_hit"]), "ports": r["ports"] or "",
              "still_running": r["still_running"], "reused": r["reused"]}
             if r else {"requested": run["total"], "skipped": [], "excluded": [], "deadline_hit": False, "ports": "",
                        "still_running": 0, "reused": 0})
    rows = _rows(run, extra)
    counts = {k: 0 for k in STATE_LABEL}
    for x in rows:
        counts[x["state"]] += 1
    return {"run_id": run_id, "scope": run["scope"], "started_at": run["started_at"], "finished_at": run["finished_at"],
            "created_by": run["created_by"], "ports": extra["ports"], "requested": extra["requested"],
            "deadline_hit": extra["deadline_hit"], "still_running": extra["still_running"], "reused": extra["reused"], "counts": counts, "rows": rows}


def list_reports(conn, limit: int = 20) -> list[dict]:
    import host_ping

    _ensure(conn)
    out = []
    for r in host_ping.list_runs(conn, max(1, min(int(limit), 200))):
        e = conn.execute("SELECT requested, skipped, excluded FROM alive_run_extra WHERE run_id = ?", (r["id"],)).fetchone()
        out.append({"run_id": r["id"], "started_at": r["started_at"], "created_by": r["created_by"], "scope": r["scope"],
                    "total": r["total"], "alive": r["alive"], "no_response": r["no_response"], "unknown": r["unknown"],
                    "excluded": len(json.loads(e["excluded"])) if e else 0,
                    "skipped": len(json.loads(e["skipped"])) if e else 0,
                    "from_v2": bool(e)})
    return out
