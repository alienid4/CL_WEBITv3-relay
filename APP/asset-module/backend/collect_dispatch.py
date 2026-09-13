"""收集入口收斂：一個動作，系統自己選路（決策 C4，2026-08-16 拍板）。

## 這片在解什麼問題

在這之前，「把一台機器納進來」散在四個入口：一鍵納管、本機執行指令、WinRM 憑證設定、
Push Agent 安裝包。使用者要先自己判斷「這台該走哪一條」才知道該點哪裡——但那個判斷
（22 通不通、445 通不通、活著沒）正是系統自己就能做的事。

所以收斂成一個入口：**輸入網段或 IP 清單，按一下，系統自己選路**，最後給一張表，
上面分得出「收到了」跟「要人工處理」，以及人工處理的各是哪幾台、要做什麼。

## 四條路（C4 定案）

| 探測結果 | 路 | 意義 |
|---|---|---|
| 22 開 | `ssh` | 走 pull 收集（webit3scan 唯讀帳號） |
| 445／5985 開、22 不開 | `winrm` | Windows 走 WinRM/CIM，不動目標機 |
| 活著、但 22/445 都不通 | `agent` | 進不去 → 產 Push Agent 安裝包，請人裝 |
| 完全沒回應 | `import` | 連活著都證明不了 → 只能靠檔案匯入 |

22 優先於 445：Windows 裝了 OpenSSH Server 時兩個都會開，而 SSH 是收集鏈的主線
（facts/服務/帳號盤點都走它），能走 SSH 就別退回只收得到 facts 的 WinRM。

## 檔案匯入為什麼不在這裡

匯入是「既有資料進場」，不是「收集」——它不需要連得到那台機器，也不產生任何
「此刻為真」的事實。混進來會讓這張表的語意變成「資料有沒有進系統」，而不是
「機器收不收得到」。維持獨立入口（/import）。

## 執行器全部可注入

探測、SSH 試連、WinRM 收集都碰真網路，家裡驗不了；抽成可注入介面，
測試把四條路各挑一台走一遍，驗的是「分派邏輯」本身，不是網路。
"""
from __future__ import annotations

import ipaddress
from concurrent.futures import ThreadPoolExecutor

ROUTE_SSH = "ssh"
ROUTE_WINRM = "winrm"
ROUTE_AGENT = "agent"
ROUTE_IMPORT = "import"

STATUS_COLLECTED = "collected"              # 成功：這台現在收得到
STATUS_NEEDS_CREDENTIAL = "needs_credential"  # 路通、身分不通 → 要佈帳號或設憑證
STATUS_NEEDS_AGENT = "needs_agent"          # 進不去 → 要請人裝 agent
STATUS_IMPORT_ONLY = "import_only"          # 完全不通 → 只能匯入
STATUS_FAILED = "failed"                    # 身分有、但收集當下失敗

# 成功＝真的收到東西；其餘都要人做點什麼。畫面靠這個分兩堆，不要各頁自己編一套。
SUCCESS_STATUSES = (STATUS_COLLECTED,)

# 探測埠：只放「決定走哪條路」需要的，加上幾個純粹用來證明「這台活著」的。
# 跟 net_scan.PROBE_PORTS 不同份是刻意的——那份是掃描存活用，這份多了 5985（WinRM 本尊）。
DISPATCH_PORTS = (22, 445, 5985, 3389, 80, 443, 8000, 161)

# 一次最多處理幾台。防的是有人貼一個 /8 進來，讓這支端點跑到天荒地老。
MAX_TARGETS = 1024


def parse_targets(text: str) -> list[str]:
    """把使用者貼進來的自由文字變成 IP 清單。

    接受三種寫法混用，用換行／逗號／空白隔開：
        10.99.1.0/24      網段（展開成可用主機位址）
        10.99.1.10-20     同網段的範圍簡寫
        10.99.1.5         單一 IP

    去重但保留輸入順序——使用者貼的順序通常有意義（同機房排在一起），
    重排成數字序反而讓他對不回自己的清單。
    """
    tokens: list[str] = []
    for chunk in (text or "").replace(",", "\n").replace(";", "\n").split():
        chunk = chunk.strip()
        if chunk:
            tokens.append(chunk)

    out: list[str] = []
    seen: set[str] = set()

    def add(ip: str) -> None:
        if ip not in seen:
            seen.add(ip)
            out.append(ip)

    for tok in tokens:
        if "/" in tok:
            try:
                net = ipaddress.ip_network(tok, strict=False)
            except ValueError as exc:
                raise ValueError(f"網段格式錯誤：{tok}（{exc}）") from exc
            # ⚠️ 先看大小再展開，不要先展開才發現太大——IPv6 的 /64 有 2^64 個位址，
            # 真的跑 list(net.hosts()) 會把記憶體吃光，整個服務跟著倒（不是慢，是死）。
            if net.num_addresses > MAX_TARGETS + 2:
                raise ValueError(
                    f"網段 {tok} 有 {net.num_addresses} 個位址，超過一次 {MAX_TARGETS} 台的上限"
                    f"——請分批，或改用掃描排程處理整個網段")
            hosts = [str(h) for h in net.hosts()] or [str(net.network_address)]
            for ip in hosts:
                add(ip)
            continue
        if "-" in tok:
            base, _, tail = tok.rpartition("-")
            try:
                start = ipaddress.ip_address(base)
                # 範圍簡寫是「最後一段」的語意，只對 IPv4 成立（IPv6 沒有「最後一段」
                # 這種寫法，硬套會在 rsplit('.') 炸成 500）
                if start.version != 4:
                    raise ValueError("範圍簡寫只支援 IPv4，IPv6 請逐一列出")
                # 「10.99.1.10-20」的 20 是最後一段，不是完整 IP
                end_last = int(tail)
                first_last = int(str(start).rsplit(".", 1)[1])
                if end_last < first_last:
                    raise ValueError("結束值小於起始值")
                prefix = str(start).rsplit(".", 1)[0]
                for last in range(first_last, end_last + 1):
                    add(f"{prefix}.{last}")
            except ValueError as exc:
                raise ValueError(f"IP 範圍格式錯誤：{tok}（{exc}）") from exc
            continue
        try:
            ipaddress.ip_address(tok)
        except ValueError as exc:
            raise ValueError(f"IP 格式錯誤：{tok}（{exc}）") from exc
        add(tok)

    if len(out) > MAX_TARGETS:
        raise ValueError(
            f"一次最多 {MAX_TARGETS} 台，這次展開成 {len(out)} 台——請分批，"
            f"或改用掃描排程處理整個網段")
    return out


def choose_route(alive: bool, open_ports=None) -> str:
    """純函式：由探測結果決定走哪條路。整個功能的判定核心，必須能直接測到。"""
    ports = set(open_ports or [])
    if not alive:
        return ROUTE_IMPORT
    if 22 in ports:
        return ROUTE_SSH
    if 5985 in ports or 445 in ports:
        return ROUTE_WINRM
    return ROUTE_AGENT


ROUTE_LABEL = {
    ROUTE_SSH: "SSH 收集",
    ROUTE_WINRM: "WinRM 收集",
    ROUTE_AGENT: "Push Agent",
    ROUTE_IMPORT: "只能匯入",
}

STATUS_LABEL = {
    STATUS_COLLECTED: "已收到",
    STATUS_NEEDS_CREDENTIAL: "待佈身分",
    STATUS_NEEDS_AGENT: "待裝 Agent",
    STATUS_IMPORT_ONLY: "只能匯入",
    STATUS_FAILED: "收集失敗",
}


# ===== 探測 =====

def _default_prober(ip: str, timeout: float = 0.6):
    """回 open port 清單；主機完全沒回應回 None。沿用 net_scan 那顆探測器，
    只是換一組埠——存活判定的行為要跟掃描一致，不要兩套結論打架。"""
    import net_scan

    return net_scan._probe_host(ip, ports=DISPATCH_PORTS, timeout=timeout)


def _probe_all(ips: list[str], prober, workers: int = 64) -> dict[str, list[int] | None]:
    with ThreadPoolExecutor(max_workers=min(workers, max(len(ips), 1))) as ex:
        return dict(zip(ips, ex.map(prober, ips)))


# ===== 各路的執行 =====

def _known_asset(conn, ip: str) -> dict | None:
    row = conn.execute(
        "SELECT asset_serial, hostname, os FROM hardware WHERE ip = ? LIMIT 1", (ip,)
    ).fetchone()
    return dict(row) if row else None


def _do_ssh(conn, ip: str, asset, ssh_prober, key_path: str) -> tuple[str, str]:
    """走 SSH：用唯讀收集帳號試連。通＝這台現在收得到；不通＝身分還沒佈。"""
    import manage_state

    # 收集身分要跟平台走：AIX 上是 8 字元的短名（max_logname 限制）。拿錯名字去試連，
    # 那批 AIX 會全部落在「待佈身分」，而它們其實只是名字對不上。
    platform = manage_state.collect_platform_of(
        conn, ip, (asset or {}).get("os"))
    account = manage_state.get_collect_account(conn, platform)
    via = f"以 {account} 連得上"
    if ssh_prober is not None:
        ok, err = ssh_prober(ip)
    elif ip in manage_state.local_ips():
        # 收集器自己那台不需要 SSH 帳號（C2：系統自己就是 ansible 主機）。
        # 少了這個分支，把自己的網段貼進來會把 collector 本機報成「待佈身分」——
        # 一個永遠修不好的假紅燈，因為那台根本不需要 webit3scan。
        # 這裡跟 manage_state.refresh_collect_status 用同一套判斷，兩邊結論才不會打架。
        via = "收集器本機，不需 SSH 帳號"
        try:
            ok, err = bool(manage_state._local_runner()(ip, "hostname").strip()), None
        except Exception as exc:  # noqa: BLE001
            ok, err = False, f"本機收集失敗：{exc}"
    else:
        ok, err = manage_state.probe_collect(ip, key_path, account=account)

    if not ok:
        return STATUS_NEEDS_CREDENTIAL, (
            f"22 通、但收集帳號 {account} 進不去（{err or '原因不明'}）——"
            f"請對這台執行納管（一鍵納管或在該機貼一行指令建帳號佈金鑰）")

    # 通了就把「已納管」寫回資產，讓四態畫面立刻反映實況，不用等下一輪排程試連。
    # 未登記的不寫（沒有資產可寫），也刻意不自動建資產——收不收它是人的決定。
    if asset:
        from db import _now_local

        conn.execute(
            "UPDATE hardware SET collect_ok = 1, collect_checked_at = ?, collect_error = NULL "
            "WHERE asset_serial = ?", (_now_local(), asset["asset_serial"]))
        conn.commit()
        return STATUS_COLLECTED, f"{via}，收集正常"
    return STATUS_COLLECTED, (
        f"{via}——但這台還沒登記成資產，"
        f"請在下方「納入管理」把它建成資產，收到的資料才有地方落")


def _do_winrm(conn, ip: str, asset, winrm_runner, cred_key_path) -> tuple[str, str]:
    """走 WinRM：從加密憑證庫挑一組 winrm 憑證去收 facts。憑證明文用完即丟。"""
    import credential_store
    import winrm_collector

    name = credential_store.pick_for_host(conn, ip, kind="winrm")
    if not name and winrm_runner is None:
        return STATUS_NEEDS_CREDENTIAL, (
            "445/5985 通，但沒有適用的 WinRM 憑證——"
            "請到系統設定→收集憑證新增一組（kind=winrm）並設定適用網段")

    username = password = None
    if name:
        got = credential_store.get_for_use(conn, name, key_path=cred_key_path) \
            if cred_key_path else credential_store.get_for_use(conn, name)
        if got is None:
            return STATUS_NEEDS_CREDENTIAL, (
                f"WinRM 憑證「{name}」解不開（加密金鑰可能已更換），請重設")
        username, password = got

    try:
        facts = winrm_collector.collect(ip, username or "", password or "",
                                        runner=winrm_runner)
    except Exception as exc:  # noqa: BLE001 - 收集失敗要如實回報原因，不吞
        if name:
            credential_store.audit_use(conn, name, ip, False, str(exc)[:200])
        return STATUS_FAILED, f"WinRM 連得到但收集失敗：{str(exc)[:200]}"
    finally:
        password = None   # 明確結束密碼生命週期

    if name:
        credential_store.audit_use(conn, name, ip, True)
    if asset:
        from db import _now_local

        conn.execute(
            "UPDATE hardware SET collect_ok = 1, collect_checked_at = ?, collect_error = NULL "
            "WHERE asset_serial = ?", (_now_local(), asset["asset_serial"]))
        conn.commit()
        got_host = facts.get("hostname") or ip
        return STATUS_COLLECTED, f"WinRM 收集成功（{got_host}）"
    return STATUS_COLLECTED, (
        "WinRM 收集成功——但這台還沒登記成資產，請在下方「納入管理」建成資產")


def _do_agent(conn, ip: str, asset, ports) -> tuple[str, str]:
    return STATUS_NEEDS_AGENT, (
        f"活著（開放埠 {','.join(map(str, ports)) or '無'}）但 22／445 都不通，"
        f"系統進不去——請按「取得安裝包」，把 Push Agent 交給該機管理者安裝")


def _do_import(conn, ip: str, asset) -> tuple[str, str]:
    if asset:
        return STATUS_IMPORT_ONLY, (
            "登記在案但完全沒回應——確認是否關機、換 IP、已下線，或被防火牆整段擋住")
    return STATUS_IMPORT_ONLY, (
        "完全沒回應，連活著都證明不了——這台的資料只能靠檔案匯入（/import）")


# ===== 主流程 =====

def run_dispatch(conn, targets_raw: str, *, triggered_by: str = "(未知)",
                 prober=None, ssh_prober=None, winrm_runner=None,
                 key_path: str | None = None, cred_key_path: str | None = None,
                 workers: int = 64) -> dict:
    """一個入口跑完整流程：解析目標 → 探測 → 選路 → 各路各自執行 → 一張結果表。

    每一台的結果都寫進 collect_dispatch_result（run 存 collect_dispatch_run），
    讓使用者重新整理頁面還看得到上一次的結果——這種要等數十秒的動作，
    做完卻只活在瀏覽器記憶體裡，等於做白工。

    prober／ssh_prober／winrm_runner 全部可注入，測試不碰真網路、不碰真憑證。
    """
    import manage_state

    ips = parse_targets(targets_raw)
    if not ips:
        raise ValueError("沒有可處理的目標——請輸入網段（10.99.1.0/24）或 IP 清單")
    key_path = key_path or manage_state.COLLECTOR_KEY_DEFAULT

    cur = conn.execute(
        "INSERT INTO collect_dispatch_run (trigger, triggered_by, targets_raw, "
        "target_count, status, started_at) VALUES (?,?,?,?,'running', "
        "datetime('now','localtime'))",
        ("manual", triggered_by, (targets_raw or "")[:2000], len(ips)),
    )
    run_id = cur.lastrowid
    conn.commit()

    try:
        probed = _probe_all(ips, prober or _default_prober, workers=workers)
        results = []
        for ip in ips:
            ports = probed.get(ip)
            alive = ports is not None
            ports = ports or []
            route = choose_route(alive, ports)
            asset = _known_asset(conn, ip)

            if route == ROUTE_SSH:
                status, message = _do_ssh(conn, ip, asset, ssh_prober, key_path)
            elif route == ROUTE_WINRM:
                status, message = _do_winrm(conn, ip, asset, winrm_runner, cred_key_path)
            elif route == ROUTE_AGENT:
                status, message = _do_agent(conn, ip, asset, ports)
            else:
                status, message = _do_import(conn, ip, asset)

            row = {
                "ip": ip,
                "alive": 1 if alive else 0,
                "open_ports": ",".join(map(str, ports)) or None,
                "route": route,
                "status": status,
                "asset_serial": asset["asset_serial"] if asset else None,
                "hostname": asset["hostname"] if asset else None,
                "registered": 1 if asset else 0,
                "message": message,
            }
            results.append(row)
            conn.execute(
                "INSERT INTO collect_dispatch_result (run_id, ip, alive, open_ports, route, "
                "status, asset_serial, hostname, registered, message) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, ip, row["alive"], row["open_ports"], route, status,
                 row["asset_serial"], row["hostname"], row["registered"], message),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - 失敗也要把 run 收尾，不要留一筆永遠 running
        conn.execute(
            "UPDATE collect_dispatch_run SET status='failed', error=?, "
            "finished_at=datetime('now','localtime') WHERE id=?", (str(exc)[:300], run_id))
        conn.commit()
        raise

    conn.execute(
        "UPDATE collect_dispatch_run SET status='ok', finished_at=datetime('now','localtime') "
        "WHERE id=?", (run_id,))
    conn.commit()
    return {"run_id": run_id, **summarize(results), "results": results}


def summarize(results: list[dict]) -> dict:
    """把逐台結果收成畫面上那幾個數字。成功／待人工是主軸，路由分佈是次要。"""
    by_status: dict[str, int] = {}
    by_route: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_route[r["route"]] = by_route.get(r["route"], 0) + 1
    collected = sum(by_status.get(s, 0) for s in SUCCESS_STATUSES)
    return {
        "total": len(results),
        "collected": collected,
        "needs_action": len(results) - collected,
        "by_status": by_status,
        "by_route": by_route,
    }


def latest_run(conn) -> dict | None:
    """最近一次分派的完整結果（含逐台）。頁面重新整理後靠這支還原。"""
    run = conn.execute(
        "SELECT * FROM collect_dispatch_run ORDER BY id DESC LIMIT 1").fetchone()
    if run is None:
        return None
    results = [dict(r) for r in conn.execute(
        "SELECT ip, alive, open_ports, route, status, asset_serial, hostname, "
        "registered, message FROM collect_dispatch_result WHERE run_id = ? ORDER BY id",
        (run["id"],))]
    return {"run": dict(run), **summarize(results), "results": results}


# ---- 診斷外掛：只給分派結果，永不含憑證 ----
try:
    import diagnostics

    @diagnostics.register("collect_dispatch")
    def _diag(conn) -> dict:
        try:
            runs = [dict(r) for r in conn.execute(
                "SELECT id, trigger, triggered_by, target_count, status, error, "
                "started_at, finished_at FROM collect_dispatch_run ORDER BY id DESC LIMIT 10")]
        except Exception:  # noqa: BLE001
            runs = []
        return {"recent_runs": runs}
except ImportError:
    pass
