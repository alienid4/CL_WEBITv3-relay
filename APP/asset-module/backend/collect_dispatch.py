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
STATUS_NOT_ONBOARDABLE = "not_onboardable"  # ESXi／OpenShift 節點／設備：有 SSH 也不納管、不去試連
# 2026-09-17 使用者：「結果應該寫 納管失敗」。
# 「待佈身分」是「還沒佈過收集帳號」；這台已經**試過納管而且失敗**，是不同的事。
# 混成同一個字會把「已經試過了」藏起來，害人再試一次同樣的密碼。
STATUS_ONBOARD_FAILED = "onboard_failed"

# 成功＝真的收到東西；其餘都要人做點什麼。畫面靠這個分兩堆，不要各頁自己編一套。
SUCCESS_STATUSES = (STATUS_COLLECTED,)

# 探測埠：只放「決定走哪條路」需要的，加上幾個純粹用來證明「這台活著」的。
# 跟 net_scan.PROBE_PORTS 不同份是刻意的——那份是掃描存活用，這份多了 5985（WinRM 本尊）。
# 跟正式區開通申請一致（2026-09-15）：只探收集與判平台真正用到的埠
DISPATCH_PORTS = (22, 445, 3389, 5985)

# 一批最多幾台。超過就**自動切成多批依序跑**，不再叫使用者自己分
# （2026-09-16 使用者：「你應該幫我自動分」「你有分批嗎?」）。
# 分批不是把上限拿掉：一次探測上千台會吃滿連線數，切開跑對網路也比較客氣。
MAX_TARGETS = 1024
BATCH_SIZE = MAX_TARGETS

# 總量的硬上限。分批之後仍然要有天花板——有人貼一個 /8 進來就是 1600 萬個位址，
# 自動分批只會讓它從「馬上失敗」變成「跑到天荒地老」，那更糟。
HARD_MAX_TARGETS = 20000

# 批與批之間停幾秒（預設值；實際值由設定決定，見 batch_gap_seconds）。
# 使用者 2026-09-16：「預設每個網段間距2分鐘」「可以設定改幾分鐘」。
DEFAULT_BATCH_GAP_SECONDS = 120


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
            if net.num_addresses > HARD_MAX_TARGETS + 2:
                raise ValueError(
                    f"網段 {tok} 有 {net.num_addresses} 個位址，超過一次 {HARD_MAX_TARGETS} 台的上限"
                    f"——這個大小請改用掃描排程處理整個網段，不要用「開始收集」")
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

    if len(out) > HARD_MAX_TARGETS:
        raise ValueError(
            f"這次展開成 {len(out)} 台，超過 {HARD_MAX_TARGETS} 台的上限——"
            f"這個規模請改用掃描排程處理整個網段，不要用「開始收集」")
    return out


def batch_gap_seconds(conn) -> int:
    """批與批之間停幾秒。存在 app_settings，畫面可改（使用者：「可以設定改幾分鐘」）。"""
    from db import get_setting

    try:
        return max(0, int(get_setting(conn, "dispatch_batch_gap_seconds",
                                      str(DEFAULT_BATCH_GAP_SECONDS))))
    except (TypeError, ValueError):
        return DEFAULT_BATCH_GAP_SECONDS


def plan_batches(ips: list[str], size: int = BATCH_SIZE) -> list[list[str]]:
    """切批。保留輸入順序——使用者貼的順序通常有意義（同機房排在一起）。"""
    size = max(1, int(size))
    return [ips[i:i + size] for i in range(0, len(ips), size)] or [[]]


#: 只有 Windows 會開的埠。Linux 不會有這兩個服務。
#: - 3389 RDP、5985 WinRM
#: ⚠️ **445 不算**：Linux 裝 Samba 一樣會開 445，拿它當 Windows 證據會誤判檔案伺服器。
WINDOWS_ONLY_PORTS = (3389, 5985)


def _last_onboard_failure(conn, ip: str) -> dict | None:
    """這台最後一次納管是不是失敗的。回失敗摘要，成功或沒試過回 None。"""
    try:
        import onboard_failures
    except ImportError:      # noqa: F401 - 模組不在（精簡佈署）就當沒有紀錄
        return None
    try:
        row = conn.execute(
            "SELECT ok, stage, message, created_at, "
            "(SELECT COUNT(*) FROM onboard_audit x WHERE x.target_ip = ? AND x.ok = 0) AS fail_count "
            "FROM onboard_audit WHERE target_ip = ? ORDER BY id DESC LIMIT 1", (ip, ip)).fetchone()
    except Exception:  # noqa: BLE001 - 舊庫沒這張表就當沒試過
        return None
    if row is None or row["ok"]:
        return None
    reason = onboard_failures.classify_reason(row["stage"], row["message"])
    return {"reason": reason, "message": (row["message"] or "")[:200],
            "next_step": onboard_failures.NEXT_STEP.get(reason, ""),
            "fail_count": row["fail_count"] or 1, "last_tried_at": row["created_at"]}


def choose_route(alive: bool, open_ports=None) -> str:
    """純函式：由探測結果決定走哪條路。整個功能的判定核心，必須能直接測到。

    ⚠️ **22 開著就走 SSH，即使那是 Windows**——這是 2026 年既有的設計決定，理由寫在
    test_兩個都開時走SSH_因為收集鏈主線是SSH：WinRM 只收得到 facts，
    服務盤點與帳號盤點都只有 SSH 這條路。Windows 裝了 OpenSSH 反而是好事。

    2026-09-17 使用者指出 10.99.18.39（22/445/3389/5985）被當成 Linux——
    那個問題**不在這支**，在平台判定（fingerprint.onboard_method 把通用 OpenSSH banner
    當成「確定是 Linux」）。路徑選 SSH 沒錯，錯的是拿 Linux 的腳本去打它。
    """
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
    STATUS_COLLECTED: "已納管",   # 收集帳號連得上＝納管完成（2026-09-14 使用者要的字）
    STATUS_NOT_ONBOARDABLE: "不納管",
    STATUS_NEEDS_CREDENTIAL: "待佈身分",
    STATUS_ONBOARD_FAILED: "納管失敗",
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

        import manage_state
        # [B-05] 成功寫整台（同一台的多筆登記一起），跟失敗時寫同 IP 全部筆對稱
        manage_state.mark_collect_ok(conn, asset["asset_serial"], _now_local())
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

        import manage_state
        # [B-05] 成功寫整台（同一台的多筆登記一起），跟失敗時寫同 IP 全部筆對稱
        manage_state.mark_collect_ok(conn, asset["asset_serial"], _now_local())
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

# ===== 背景執行（2026-09-16）=====
# 1270 台探測要跑很久，加上批與批之間的間隔更久——同步的 HTTP 請求一定會被逾時切斷，
# 使用者看到的就是「收集沒有完成（跑了 0 秒）」。所以大批次改在背景跑，畫面輪詢進度。
# 小批次維持同步（行為與以往完全相同），避免為了幾十台也要輪詢。

_bg_lock = __import__("threading").Lock()
_bg_running = False


def background_running() -> bool:
    return _bg_running


def start_background(db_path, targets_raw: str, *, triggered_by: str = "(未知)") -> dict:
    # db_path=None → 用 db.get_connection() 的預設（ASSET_DB_PATH 環境變數），跟 API 走同一個庫
    """在背景執行緒跑一次分派。回 {run_id, total, batch_total}。

    ⚠️ 執行緒自己開一條 sqlite 連線（sqlite3 預設 check_same_thread=True）。
    """
    global _bg_running
    import threading

    from db import get_connection

    ips = parse_targets(targets_raw)
    if not ips:
        raise ValueError("沒有可處理的目標——請輸入網段（10.99.1.0/24）或 IP 清單")
    with _bg_lock:
        if _bg_running:
            raise RuntimeError("已經有一輪收集在跑了——等它跑完，或看下方「開始收集紀錄」")
        _bg_running = True

    def worker():
        global _bg_running
        conn = get_connection(db_path)
        try:
            run_dispatch(conn, targets_raw, triggered_by=triggered_by)
        except Exception:  # noqa: BLE001 - run_dispatch 自己會把 run 標成 failed 並留錯誤
            pass
        finally:
            conn.close()
            _bg_running = False

    threading.Thread(target=worker, daemon=True).start()
    return {"started": True, "total": len(ips), "batch_total": len(plan_batches(ips))}


def _sleep(seconds: int) -> None:
    """抽出來讓測試不用真的等兩分鐘。"""
    import time

    time.sleep(seconds)


def run_dispatch(conn, targets_raw: str, *, triggered_by: str = "(未知)",
                 prober=None, ssh_prober=None, winrm_runner=None,
                 key_path: str | None = None, cred_key_path: str | None = None,
                 workers: int = 64, batch_size: int | None = None,
                 gap_seconds: int | None = None) -> dict:
    """一個入口跑完整流程：解析目標 → 探測 → 選路 → 各路各自執行 → 一張結果表。

    每一台的結果都寫進 collect_dispatch_result（run 存 collect_dispatch_run），
    讓使用者重新整理頁面還看得到上一次的結果——這種要等數十秒的動作，
    做完卻只活在瀏覽器記憶體裡，等於做白工。

    prober／ssh_prober／winrm_runner 全部可注入，測試不碰真網路、不碰真憑證。
    """
    import manage_state
    import onboard_eligibility

    ips = parse_targets(targets_raw)
    if not ips:
        raise ValueError("沒有可處理的目標——請輸入網段（10.99.1.0/24）或 IP 清單")
    key_path = key_path or manage_state.COLLECTOR_KEY_DEFAULT

    # 自動分批（2026-09-16）：以前超過 1024 台直接拒絕、叫使用者自己切；
    # 現在系統自己切，並把「第幾批、做完幾台」寫進 run，畫面才看得出進度。
    batches = plan_batches(ips, batch_size or BATCH_SIZE)
    gap = batch_gap_seconds(conn) if gap_seconds is None else max(0, int(gap_seconds))

    cur = conn.execute(
        "INSERT INTO collect_dispatch_run (trigger, triggered_by, targets_raw, "
        "target_count, status, started_at, batch_total, batch_done, done_count) "
        "VALUES (?,?,?,?,'running', datetime('now','localtime'), ?, 0, 0)",
        ("manual", triggered_by, (targets_raw or "")[:2000], len(ips), len(batches)),
    )
    run_id = cur.lastrowid
    conn.commit()

    try:
        # vCenter 列為 ESXi 主機的名單，整輪只查一次
        esxi_names = onboard_eligibility.esxi_host_names(conn)
        results = []
        for bi, batch in enumerate(batches):
          # 批與批之間停一下：一次對上千台送封包對網路不客氣，也容易被 SOC 當成掃描行為。
          # 第一批不等（不然按下去要先發呆兩分鐘）。
          if bi and gap:
              _sleep(gap)
          probed = _probe_all(batch, prober or _default_prober, workers=workers)
          for ip in batch:
            ports = probed.get(ip)
            alive = ports is not None
            ports = ports or []
            route = choose_route(alive, ports)
            asset = _known_asset(conn, ip)

            # 有開 22 不代表可以納管：ESXi／OpenShift 節點／設備**先擋、連試連都不做**，
            # 並寫明依據（2026-09-14 使用者：有 SSH 但要標出來，我才不會去納管它）
            block = None
            if route == ROUTE_SSH:
                block = onboard_eligibility.not_onboardable(
                    (asset or {}).get("os"), (asset or {}).get("hostname"), ip, esxi_names)
            if block:
                status = STATUS_NOT_ONBOARDABLE
                message = f"{block[1]}——不納管。{block[2]}"
            elif route == ROUTE_SSH:
                status, message = _do_ssh(conn, ip, asset, ssh_prober, key_path)
                # 收集帳號進不去時，連線訊息裡常帶著 SSH banner——產品自報身分就採信它。
                # 資產庫沒登記／os 欄空白的機器，光看 os 判不出來，但 banner 已經講了
                # （2026-09-15：10.92.198.21 自報 VMware Avi Load Balancer，被列在待佈身分）。
                # 已經試過納管而且失敗的，講「納管失敗」不要講「待佈身分」
                # （2026-09-17 使用者）——後者會讓人以為還沒動過。
                if status == STATUS_NEEDS_CREDENTIAL:
                    prev = _last_onboard_failure(conn, ip)
                    if prev:
                        status = STATUS_ONBOARD_FAILED
                        message = (f"納管失敗（{prev['reason']}）：{prev['message']}"
                                   f"　── {prev['next_step']}"
                                   f"（第 {prev['fail_count']} 次，最後一次 {prev['last_tried_at']}）")
                if status == STATUS_NEEDS_CREDENTIAL:
                    product = onboard_eligibility.product_from_banner(message)
                    if product:
                        status = STATUS_NOT_ONBOARDABLE
                        message = (f"{product}——不納管。依據：SSH banner 自報。"
                                   f"原始訊息：{message}")
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
          # 每批做完就存檔並更新進度：跑到第 7 批才壞掉時，前 6 批的結果不可以跟著不見
          conn.execute(
              "UPDATE collect_dispatch_run SET batch_done=?, done_count=? WHERE id=?",
              (bi + 1, len(results), run_id))
          conn.commit()
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
    # 不納管的不算「要人工處理」——那些本來就不該動，算進去會讓人以為還有事沒做
    not_onboardable = by_status.get(STATUS_NOT_ONBOARDABLE, 0)

    # 2026-09-16 使用者：「為什麼是 508，還要人工處理?」
    # 他貼了兩個 /24（254×2＝508 個**位址**，不是 508 台機器），全部沒有回應，
    # 畫面卻寫「要人工處理 508、沒有回應（只能匯入）508」——同一批東西講成兩個數字，
    # 而且把 508 個空位址說成「要人工處理」。掃一個網段本來就大部分是空的。
    #
    # 所以拆開：完全沒回應**而且資產庫也沒登記**＝那個位址上根本沒有機器，不需要處理。
    # 已登記卻完全沒回應的**仍然要處理**（機器在案卻叫不動，那是真的待辦）。
    empty_addresses = sum(1 for r in results
                          if r.get("status") == STATUS_IMPORT_ONLY and not r.get("registered"))
    return {
        "total": len(results),
        "collected": collected,
        "not_onboardable": not_onboardable,
        # 空位址不算待辦，但要看得見——不然使用者會以為系統漏掉了什麼
        "empty_addresses": empty_addresses,
        "needs_action": len(results) - collected - not_onboardable - empty_addresses,
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
