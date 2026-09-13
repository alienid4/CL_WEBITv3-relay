"""一鍵批次自動納管——背景執行版（500 台不能卡畫面）。

模式沿用 scan_service：模組級 _running 旗標防並發、背景執行緒做事、結果寫進 DB
持久化、畫面輪詢 status()。

⚠️ 密碼只活在這次 start() 傳進來的參數與背景執行緒的閉包裡，**絕不寫進 DB**——
batch_onboard_result 只存「用第幾組（password_index）」與結果，沒有任何密碼字串。
（設計理由見 AI/計畫_一鍵批次自動納管.md）
"""
from __future__ import annotations

import ipaddress

import onboard_eligibility
import threading
import time

import manage_state
import onboard_engine
from db import _now_local, get_connection

_lock = threading.Lock()
_running = False
_HOST_PAUSE_SEC = 0.5   # 每台之間停一下——別瞬間對很多台開連線（也讓失敗不會擠成噴灑的形狀）


def is_running() -> bool:
    return _running


def _ip_int(ip: str) -> int | None:
    try:
        return int(ipaddress.ip_address(ip.strip()))
    except ValueError:
        return None


def _segment_env(conn, ip: str) -> str | None:
    """這個 IP 落在哪個網段、那個網段是測試還是正式。

    未登記的機器沒有自己的環境別，但網段配置表（network_segment）有——一個
    測試網段裡掃到的機器，就算還沒登記，也能靠網段判定是測試。挑最小（最精確）
    的那一段。"""
    v = _ip_int(ip)
    if v is None:
        return None
    row = conn.execute(
        "SELECT environment FROM network_segment WHERE net_start IS NOT NULL "
        "AND net_start <= ? AND net_end >= ? ORDER BY (net_end - net_start) ASC LIMIT 1",
        (v, v)).fetchone()
    return manage_state.group_environment(row["environment"]) if row and row["environment"] else None


def _env_of(conn, ip: str) -> tuple[str | None, bool, str | None, str | None]:
    """回 (環境別, 是否已登記, 作業系統, 矛盾說明)。

    作業系統一起撈回來，是因為 2026-09-08 之後它是**能不能納管的門檻**
    （見 onboard_eligibility）。分成兩次查詢只會讓同一台機器被查兩遍。
    沒登記的機器沒有 os 可言，回 None——那本身就是「不確認就不碰」的理由。

    ## 為什麼不能用 `LIMIT 1`（2026-09-08 查 221 真實資料才發現）

    資產表裡同一個 IP 有多筆的，共 410 組、933 筆。

    ⚠️ **多筆不等於重複登記**（2026-09-09 更正，原本判斷是錯的）：CIA 清冊的單位
    其實是「**每個服務／VIP 一筆**」，所以同一台實體機跑三個服務就會有三筆，
    `asset_name`／`asset_purpose`／`big_ip_vip` 各不相同——那是正常資料，不能刪。
    真正「連名稱、用途、VIP 都一樣」的重複只有約 145 組。

    **但不論是不是重複，這裡的問題都一樣**：同一台機器只會有一個環境別、一種
    作業系統。這兩項在多筆之間對不起來，就是資料有問題：

        環境別互相矛盾 20 組（15 組 備援/正式、**5 組 正式/測試**）
        作業系統不一致 58 組

    原本這裡是 `WHERE ip = ? LIMIT 1` **而且沒有 ORDER BY**——SQLite 回哪一列
    不保證，等於隨機挑。挑到「測試」那一列，**正式機就會被自動納管**；換個時間
    又變成不能跑。兩種都錯，而且是隨機的，最難查。

    改成：多筆而且互相矛盾時回一個矛盾說明，呼叫端丟進「要你確認」。
    使用者 2026-09-08 拍板：「第一個答案就是人要確認」。
    **資料自己在打架的時候，系統挑一個等於幫忙猜。**

    註：欄位一致的多筆（同一台的多個服務）**照樣可以納管**——那不是矛盾。
    """
    rows = conn.execute(
        "SELECT environment, os FROM hardware WHERE ip = ?", (ip,)).fetchall()
    if not rows:
        return _segment_env(conn, ip), False, None, None

    envs = {manage_state.group_environment(r["environment"]) or "" for r in rows}
    # os 比對走「分類後」的結果：同一台機器寫成 "RedHat 8.5" 與
    # "Red Hat Enterprise Linux 8 (64-bit)" 不算矛盾，那只是填法不同。
    kinds = {onboard_eligibility.classify_os(r["os"])[0] for r in rows}

    if len(rows) > 1 and len(envs) > 1:
        return None, True, None, (
            f"資產表裡這個 IP 有 {len(rows)} 筆登記，**環境別不一致**"
            f"（{'／'.join(sorted(x or '空白' for x in envs))}）——"
            "無法確認是不是測試機，不自動納管")
    if len(rows) > 1 and len(kinds) > 1:
        return None, True, None, (
            f"資產表裡這個 IP 有 {len(rows)} 筆登記，**作業系統不一致**"
            f"（{'／'.join(sorted(kinds))}）——無法確認這台是什麼，不自動納管")

    r = rows[0]
    return manage_state.group_environment(r["environment"]) or None, True, r["os"], None


def expand_targets(conn, tokens: list[str]) -> list[dict]:
    """把使用者輸入的一批 token（IP 或網段 CIDR）攤成一台一台，帶上開放埠。

    網段（如 10.99.1.0/24）**不是硬展開成 254 個 IP 去盲試**——那會對一堆空 IP
    連線、又像密碼噴灑（使用者 2026-09-06 選的方案 B）。改成只取「最近一次掃描
    有掃到、活著」而且落在這個網段內的機器。純 IP 就直接用（也帶上掃到的埠）。
    """
    latest = conn.execute("SELECT MAX(scan_time) AS t FROM scan_history").fetchone()
    scan_time = latest["t"] if latest else None
    alive = {}
    if scan_time:
        for r in conn.execute(
            "SELECT ip, open_ports FROM scan_history WHERE scan_time = ? AND scan_ok = 1",
                (scan_time,)):
            alive[r["ip"]] = r["open_ports"]

    out: list[dict] = []
    seen: set[str] = set()
    for tok in tokens:
        tok = (tok or "").strip()
        if not tok:
            continue
        if "/" in tok:   # 網段：取這段裡「掃到活著」的機器
            try:
                net = ipaddress.ip_network(tok, strict=False)
            except ValueError:
                continue
            lo, hi = int(net.network_address), int(net.broadcast_address)
            for ip, ports in alive.items():
                v = _ip_int(ip)
                if v is not None and lo <= v <= hi and ip not in seen:
                    seen.add(ip)
                    out.append({"ip": ip, "open_ports": ports, "from_segment": tok})
        else:            # 純 IP：直接用，順帶帶上掃到的埠（有的話）
            if tok not in seen:
                seen.add(tok)
                out.append({"ip": tok, "open_ports": alive.get(tok), "from_segment": None})
    return out


def _os_route(open_ports) -> str:
    """從開放埠判 OS 路線：ssh(Linux)／winrm(Windows)／other。沿用 collect_dispatch 的判法。"""
    from collect_dispatch import choose_route
    ports = []
    for p in str(open_ports or "").replace(" ", "").split(","):
        if p.isdigit():
            ports.append(int(p))
    return choose_route(alive=True, open_ports=ports)


def classify_targets(conn, ips: list[str]) -> dict:
    """把目標分堆——只有「Linux＋測試」能自動跑，其餘各自歸類講清楚為什麼。

    使用者 2026-09-06 定案（方案 B）：
    - 網段只取掃到活著的機器（expand_targets），不盲試整段
    - 先分 OS：Windows 另外歸一堆（走 WinRM，不是這支能做的），不硬拿 Linux 的
      方式去打它、也不會被誤判成「密碼不對」
    - 再看環境別（自己的或網段的）：正式/備援擋掉；判不出來的當未知
    """
    hosts = expand_targets(conn, ips)
    run, windows, other, blocked, unknown, excluded = [], [], [], [], [], []
    for h in hosts:
        ip = h["ip"]
        route = _os_route(h.get("open_ports"))
        env, registered, os_text, conflict = _env_of(conn, ip)

        if route == "winrm":
            windows.append({"ip": ip, "environment": env,
                            "reason": "Windows（開 445/5985）——要走 WinRM 流程，這支不做"})
            continue
        if route != "ssh":
            other.append({"ip": ip, "environment": env,
                          "reason": "沒開 22（SSH）——這支只能納管走 SSH 的機器"})
            continue
        # 走到這＝開著 22。但**開 22 不等於是伺服器**——交換器、儲存設備、F5、
        # iDRAC 管理卡都吃 SSH，banner 還常自報 Linux。所以先問資產庫登記的
        # 作業系統是什麼，白名單擋一層（2026-09-08 使用者拍板，見 onboard_eligibility）。
        if not registered:
            unknown.append({"ip": ip, "environment": env or "（不明）",
                            "reason": "資產庫查無這個 IP——無從確認它是伺服器還是網通設備，不自動納管"})
            continue
        if conflict:
            # 資產表自己在打架。挑一列等於幫忙猜，猜錯就是在正式機上建帳號。
            unknown.append({"ip": ip, "environment": "（資料矛盾）", "reason": conflict})
            continue
        kind, why = onboard_eligibility.classify_os(os_text)
        if kind == "windows":
            windows.append({"ip": ip, "environment": env, "reason": why})
            continue
        if kind == "unknown":
            unknown.append({"ip": ip, "environment": env or "（不明）", "reason": why})
            continue
        if kind != "linux":
            excluded.append({"ip": ip, "environment": env, "kind": kind,
                             "os": os_text, "reason": why})
            continue

        # 走到這＝已登記、而且確定是伺服器 Linux。最後才看環境別。
        if env == "測試":
            run.append({"ip": ip, "environment": env, "registered": registered})
        elif env in ("正式", "備援"):
            blocked.append({"ip": ip, "environment": env,
                            "reason": f"環境別＝{env}，不自動納管（正式/備援要走 PAM＋金鑰）"})
        else:
            unknown.append({"ip": ip, "environment": env or "（不明）",
                            "reason": "查不到環境別（資產沒登記、網段表也沒對到）——無法確認是測試機"})
    return {"run": run, "windows": windows, "other": other,
            "blocked_production": blocked, "unknown": unknown,
            "excluded": excluded}


def start(conn, ips: list[str], username: str, passwords: list[str],
          unify_password: bool, triggered_by: str,
          include_ips: list[str] | None = None) -> dict:
    """啟動一次批次自動納管。回 {run_id, will_run, blocked_production, unknown}。

    只跑「環境別＝測試」的（classify_targets）。include_ips 是使用者在畫面上
    明確勾選要納入的未登記/未知機器（他自己確認過是測試機）——沒有就不跑那些。

    ⚠️ passwords 只往背景執行緒的閉包傳，不寫 DB、不寫 log、不回傳。
    """
    global _running
    if not username:
        raise ValueError("缺少登入帳號")
    if not passwords or not any(passwords):
        raise ValueError("至少要給一組密碼")
    passwords = [p for p in passwords if p]

    buckets = classify_targets(conn, ips)
    run_targets = list(buckets["run"])
    # 使用者明確勾選要納入的未知機器（他確認過是測試）才加進來
    if include_ips:
        inc = set(include_ips)
        for u in buckets["unknown"]:
            if u["ip"] in inc:
                run_targets.append({"ip": u["ip"], "environment": u.get("environment") or "（人工確認）"})

    if not run_targets:
        raise ValueError("沒有可自動納管的目標——都是正式/備援或未確認的機器")

    with _lock:
        if _running:
            raise ValueError("已經有一批在跑，等它跑完再啟動下一批")
        _running = True

    now = _now_local()
    cur = conn.execute(
        "INSERT INTO batch_onboard_run (triggered_by, target_count, unify, status, started_at) "
        "VALUES (?,?,?,?,?)",
        (triggered_by, len(run_targets), 1 if unify_password else 0, "running", now))
    run_id = cur.lastrowid
    conn.commit()

    from db import get_db_path
    db_path = get_db_path()
    threading.Thread(
        target=_do_batch,
        args=(db_path, run_id, run_targets, username, passwords, unify_password),
        daemon=True).start()

    return {"run_id": run_id, "will_run": [t["ip"] for t in run_targets],
            "blocked_production": buckets["blocked_production"], "unknown": buckets["unknown"]}


def _do_batch(db_path, run_id: int, targets: list[dict], username: str,
              passwords: list[str], unify_password: bool) -> None:
    """背景執行緒：逐台納管、每台做完就寫一筆結果（畫面才有真進度）。

    ⚠️ 用自己的連線（背景執行緒不能用請求那條）。密碼只在這個函式的參數裡，
    寫 DB 時**只寫 result 的欄位，沒有密碼**。
    """
    global _running
    conn = get_connection(db_path)
    try:
        collector_ip = onboard_engine.resolve_collector_ip(conn)
        pubkey = onboard_engine.collector_pubkey()
        # 管理者在「盤點作業」頁設定的帳號備註（員工編號-姓名_部門_系統）。
        # 沒設就是中性預設值；實際值存 DB 不進版控（含個資）。
        comment = onboard_engine.resolve_account_comment(conn)

        def account_of(platform):
            return manage_state.get_collect_account(conn, platform)

        for t in targets:
            # 一台一台跑（不是並發轟）——安全考量：慢一點、不像攻擊、不會瞬間鎖一堆帳號
            rows = onboard_engine.batch_auto_onboard(
                [{"ip": t["ip"]}], username=username, passwords=passwords,
                collector_ip=collector_ip, account_of=account_of, pubkey=pubkey,
                unify_password=unify_password, comment=comment)
            r = rows[0]
            conn.execute(
                "INSERT INTO batch_onboard_result (run_id, ip, environment, login_ok, "
                "password_index, platform, onboarded, password_unified, fail_stage, fail_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, t["ip"], t.get("environment"),
                 1 if r["login_ok"] else 0, r["password_index"], r["platform"],
                 1 if r["onboarded"] else 0, 1 if r["password_unified"] else 0,
                 r["fail_stage"], r["fail_reason"]))
            conn.commit()

            # 納管成功的：補最小資產（未登記的才有）＋只刷這一台的收集狀態
            if r["onboarded"]:
                try:
                    _post_onboard(conn, t["ip"])
                except Exception:  # noqa: BLE001 - 收尾失敗不影響「已納管」的事實
                    pass
            time.sleep(_HOST_PAUSE_SEC)

        conn.execute(
            "UPDATE batch_onboard_run SET status = 'ok', finished_at = ? WHERE id = ?",
            (_now_local(), run_id))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.execute(
            "UPDATE batch_onboard_run SET status = 'failed', error = ?, finished_at = ? "
            "WHERE id = ?", (str(exc)[:500], _now_local(), run_id))
        conn.commit()
    finally:
        conn.close()
        _running = False


def _post_onboard(conn, ip: str) -> None:
    """納管成功後：未登記的補一筆最小資產，並只刷這一台的收集狀態。"""
    from api import insert_hardware
    row = conn.execute("SELECT asset_serial FROM hardware WHERE ip = ?", (ip,)).fetchone()
    if row is None:
        scan_hn = conn.execute(
            "SELECT hostname FROM scan_history WHERE ip = ? AND scan_ok = 1 "
            "ORDER BY scan_time DESC LIMIT 1", (ip,)).fetchone()
        insert_hardware(
            conn, asset_serial=f"AUTO-{ip}", ip=ip,
            hostname=(scan_hn["hostname"] if scan_hn and scan_hn["hostname"] else None),
            environment="測試", asset_status="使用中")
    manage_state.refresh_collect_status(conn, only_ip=ip)


def status(conn) -> dict:
    """最近一批的狀態＋逐台結果。畫面輪詢這個；隔天回來也看得到上次跑到哪。"""
    run = conn.execute(
        "SELECT * FROM batch_onboard_run ORDER BY id DESC LIMIT 1").fetchone()
    if run is None:
        return {"running": False, "run": None, "results": [], "done": 0, "total": 0}
    results = conn.execute(
        "SELECT ip, environment, login_ok, password_index, platform, onboarded, "
        "password_unified, fail_stage, fail_reason FROM batch_onboard_result "
        "WHERE run_id = ? ORDER BY id", (run["id"],)).fetchall()
    return {
        "running": is_running(),
        "run": {"id": run["id"], "status": run["status"], "unify": run["unify"],
                "target_count": run["target_count"], "triggered_by": run["triggered_by"],
                "started_at": run["started_at"], "finished_at": run["finished_at"],
                "error": run["error"]},
        "total": run["target_count"],
        "done": len(results),
        "results": [dict(r) for r in results],
    }
