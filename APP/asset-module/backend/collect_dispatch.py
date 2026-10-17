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
import re
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

# ===== 全站不再出現「不納管」這個字（2026-10-16 使用者：「這個沒改到」「每一頁都要」）=====
# 使用者原話：「網路設備／ESXi／儲存 這些都有它獨立納管方式」「**不是靠 Linux 方式納管**」。
# 「不納管」讀起來是「這台不用管了」——錯的訊息。實際上是**不走 Linux 那套**
#（SSH 進去建本機帳號），各自另有收集路徑（見 _NOK_GUIDE）。
#
# 只換**給人看的文字**。內部識別字（STATUS_NOT_ONBOARDABLE 的值 "not_onboardable"、
# not_onboardable_kind、API／DB 欄位名、測試常數名）一律不動——動了連環影響，不值得。
#: 給人看的短標籤（狀態 pill、統計方塊、各頁清單都讀它，不要各頁自己編一套）
NOT_ONBOARDABLE_TEXT = "另有納管方式"
#: 逐列訊息裡的那一句（比 pill 多講一句「不走哪條」，因為那一格塞得下）
NOT_ONBOARDABLE_SENTENCE = f"{NOT_ONBOARDABLE_TEXT}，不走 Linux 那套（SSH 進去建本機帳號）"

STATUS_LABEL = {
    STATUS_COLLECTED: "已納管",   # 收集帳號連得上＝納管完成（2026-09-14 使用者要的字）
    STATUS_NOT_ONBOARDABLE: NOT_ONBOARDABLE_TEXT,
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
        "SELECT asset_serial, hostname, os, asset_name, asset_purpose "
        "FROM hardware WHERE ip = ? LIMIT 1", (ip,)
    ).fetchone()
    return dict(row) if row else None


def _known_assets(conn, ips) -> dict[str, dict]:
    """一次把這批 IP 的資產登記查回來，`ip → 第一筆`。

    跟 `_known_asset` 同語意（同一個 IP 有多筆登記就取 rowid 最小的那一筆，
    等同單筆版的 `LIMIT 1`），只是掃一個 /24 時不要發 254 次查詢。
    """
    out: dict[str, dict] = {}
    uniq = [ip for ip in dict.fromkeys(ips) if ip]
    for i in range(0, len(uniq), 400):          # SQLite 參數上限，分批問
        chunk = uniq[i:i + 400]
        qs = ",".join("?" * len(chunk))
        for r in conn.execute(
                "SELECT ip, asset_serial, hostname, os, asset_name, asset_purpose "
                f"FROM hardware WHERE ip IN ({qs}) ORDER BY rowid", chunk):
            out.setdefault(r["ip"], dict(r))
    return out


def _do_ssh(conn, ip: str, asset, ssh_prober, key_path: str) -> tuple[str, str]:
    """走 SSH：用唯讀收集帳號試連。通＝這台現在收得到；不通＝身分還沒佈。"""
    import manage_state

    # 收集身分要跟平台走：AIX 上是 8 字元的短名（max_logname 限制）。拿錯名字去試連，
    # 那批 AIX 會全部落在「待佈身分」，而它們其實只是名字對不上。
    platform = manage_state.collect_platform_of(
        conn, ip, (asset or {}).get("os"),
        asset_serial=(asset or {}).get("asset_serial"))
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
    import os_override

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
        # 人工指定整輪只查一次。**重新收集不可以蓋回人工指定**——使用者改過的類別
        # 在這裡就先採用，而不是等畫面顯示時才蓋，否則同一輪的 status／message
        # 會跟頁簽講不同的話（「不納管」卻躺在 SSH 收集頁）。
        ov_map = os_override.active_map(conn)
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
            # 擋下來的**類別**要留著，不是只寫進 message（2026-10-13）：
            # 畫面上的「不納管」是一個合計數字，使用者要看得出其中幾台是儲存設備。
            # 只寫進 message 就只能靠關鍵字去猜，而 message 是給人看的自由文字。
            nok_kind = None
            # ⚠️ 判定**不綁 route**（2026-10-16 修）：以前這段關在 `if route == ROUTE_SSH` 裡，
            # 於是「不納管」只有開著 22 的機器判得到。使用者在公司機看到三台
            # `Esxi019-56/57/58`（os 欄登記 `VMware ESXi, 8.0.3`）被列成「待裝 Agent」、
            # ESXi 頁簽是 0——ESXi 預設不開 SSH，只開 443/902，探測結果是「活著、開放埠無」，
            # choose_route 回 agent，判定根本沒跑。而「請人去 ESXi 上裝 Push Agent」是錯的建議。
            #
            # 「可不可以納管」是這台機器的屬性，跟「連得進去的是哪條路」無關，
            # 所以**活著的一律判一次**，判出來就蓋掉 route 的結論（ssh／winrm／agent 都一樣）。
            #
            # 完全沒回應的（alive=False，route=import）刻意**不判**：
            #   1. 掃一個 /24 大部分是空位址，它們沒有資產登記 → not_onboardable 回 None，
            #      判了也沒事；真正會被改到的是「登記在案但完全沒回應」那種。
            #   2. 那種機器現在算在「要人工處理」裡（機器在案卻叫不動，是真的待辦，
            #      summarize 的 empty_addresses 刻意只扣沒登記的）。標成「不納管」會把
            #      這筆待辦從數字上抹掉，也把「完全沒回應」這個真正的狀況換成一句不納管——
            #      少講了它現在連不上。失敗方向不對，所以不動。
            # 人工指定優先（2026-10-16）。兩個方向都在這裡定案：
            #   → 不納管（儲存／網路／ESXi／其他）：直接擋，連試連都不做
            #   → 可收集（SSH／WinRM）：不擋，讓它照正常流程**真的去試連**一次
            #     （使用者要的「只影響顯示與試連」就是這個意思）。試連只是唯讀登入，
            #     **不會建帳號**——建帳號只有人按「⚡ 一鍵納管」才會發生，
            #     而那條路的白名單（batch_onboard_service.classify_targets）讀的是資產
            #     登記的作業系統，完全不看這張表，所以搬移永遠搬不出一個自動建帳號。
            _ov = override_of(ov_map, {"hostname": (asset or {}).get("hostname"), "ip": ip,
                                       "asset_serial": (asset or {}).get("asset_serial")})
            _ov_nok_kind = _TAB_NOK_KIND.get(_ov["tab"]) if _ov else None
            if _ov and _ov_nok_kind:
                block = (_ov_nok_kind, TAB_LABEL[_ov["tab"]],
                         f"依據：人工指定（{_ov['by'] or '不詳'} 於 {_ov['at']}）"
                         + (f"：{_ov['reason']}" if _ov.get("reason") else ""))
            elif _ov:
                pass        # 指定成可收集的那幾類 → 不擋，往下照 route 真的試連一次
            elif alive:
                # 作業系統欄空白時，資產名稱／用途常常就寫著身分（EMC UnityXT480 NAS）——
                # 證據一直都在，只是以前沒傳進去（2026-10-13）。
                _hint = " ".join(x for x in ((asset or {}).get("asset_name"),
                                             (asset or {}).get("asset_purpose")) if x)
                block = onboard_eligibility.not_onboardable(
                    (asset or {}).get("os"), (asset or {}).get("hostname"), ip, esxi_names,
                    extra_text=_hint or None)
            if block:
                nok_kind = block[0]
                status = STATUS_NOT_ONBOARDABLE
                message = f"{block[1]}——{NOT_ONBOARDABLE_SENTENCE}。{block[2]}"
            elif route == ROUTE_SSH:
                status, message = _do_ssh(conn, ip, asset, ssh_prober, key_path)
                # 收集帳號進不去時，連線訊息裡常帶著 SSH banner——產品自報身分就採信它。
                # 資產庫沒登記／os 欄空白的機器，光看 os 判不出來，但 banner 已經講了
                # （2026-09-15：10.99.198.21 自報 VMware Avi Load Balancer，被列在待佈身分）。
                # 已經試過納管而且失敗的，講「納管失敗」不要講「待佈身分」
                # （2026-09-17 使用者）——後者會讓人以為還沒動過。
                if status == STATUS_NEEDS_CREDENTIAL:
                    prev = _last_onboard_failure(conn, ip)
                    if prev:
                        status = STATUS_ONBOARD_FAILED
                        message = (f"納管失敗（{prev['reason']}）：{prev['message']}"
                                   f"　── {prev['next_step']}"
                                   f"（第 {prev['fail_count']} 次，最後一次 {prev['last_tried_at']}）")
                if status == STATUS_NEEDS_CREDENTIAL and not _ov:
                    # 人工指定過的不再用 banner 反判：人已經看過這台了，
                    # banner 自報只是證據之一，不能拿它推翻人的結論
                    product = onboard_eligibility.product_from_banner(message)
                    if product:
                        status = STATUS_NOT_ONBOARDABLE
                        # 這一條不是 classify_os 的結論，自成一類（KIND_BANNER）；
                        # 不給類別，各類別小計就加不回「不納管」的總數。
                        # 2026-10-13：banner 認得出歸屬的（NetApp→儲存、Cisco→網路設備、
                        # ESXi→ESXi）直接歸那一類，/adopt 的頁簽才不會漏算；
                        # 認不出歸屬的維持 KIND_BANNER。
                        nok_kind = onboard_eligibility.kind_from_banner(product)
                        message = (f"{product}——{NOT_ONBOARDABLE_SENTENCE}。依據：SSH banner 自報。"
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
                # 資產名稱（2026-10-16 使用者：「加入資產名稱欄位」）。有些列主機名稱是空的，
                # 光看主機名稱認不出是哪一台，但資產名稱（例如「技術中台」）常常有值。
                # ⚠️ 跟 asset_os 一樣**不寫進 collect_dispatch_result**：那張表存的是
                # 「這次探測的事實」，資產名稱會改，存快照只會跟資產庫漂走。重讀時現查。
                "asset_name": asset["asset_name"] if asset else None,
                "registered": 1 if asset else 0,
                "message": message,
                "not_onboardable_kind": nok_kind,
                # 只給 summarize 指路用（appliance 桶裡再認 IBM i／Power）。
                # 不進 collect_dispatch_result——那張表存的是「這次探測的事實」，
                # 資產登記會變，存一份快照只會跟資產庫漂走。latest_run 讀時現查。
                "asset_os": (asset or {}).get("os"),
            }
            results.append(row)
            conn.execute(
                "INSERT INTO collect_dispatch_result (run_id, ip, alive, open_ports, route, "
                "status, asset_serial, hostname, registered, message, not_onboardable_kind) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, ip, row["alive"], row["open_ports"], route, status,
                 row["asset_serial"], row["hostname"], row["registered"], message, nok_kind),
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
    return {"run_id": run_id, **summarize(results, ov_map), "results": results}


# ===== /adopt 收集結果的頁簽（2026-10-13）=====
# 使用者截圖：「SSH 收集（Linux）40」裡混著儲存設備（NAS）、Aruba 等網路設備、
# 一台 VMware Photon OS（虛擬化平台）——它們都開 22 所以 route 是 ssh，但根本不納管。
# 頁簽是用 route 分的，於是「能走 SSH 收集的」跟「有 SSH 但不納管的」混在一起。
#
# 所以頁簽改成看「這台最後落在哪」：不納管的依類別拆出去，其餘照 route。
# **每台只會落在一個頁簽**（互斥），全部頁簽加起來＝這輪探測的位址數（窮盡）。
#
# 頁簽在**後端**算、前端照抄 `tab` 與 `by_tab`——數字跟點下去的列走同一份計算，
# 不會出現「頁簽寫 3、點進去 4 台」（加總＝下鑽）。
TAB_SSH = ROUTE_SSH
TAB_WINRM = ROUTE_WINRM
TAB_AGENT = ROUTE_AGENT
TAB_IMPORT = ROUTE_IMPORT
TAB_STORAGE = "storage"
TAB_NETWORK = "network"
TAB_ESXI = "esxi"
#: 其他不納管：OpenShift 節點、認不出廠牌的設備（iDRAC、SecureKi…）、banner 自報但
#: 認不出歸屬的、以及舊 run 沒留類別而且現在也重判不出來的。
TAB_NOK_OTHER = "nok_other"

#: 顯示順序固定（不跟台數跳動）。可以動的三條在前，不納管三類＋其他，只能匯入最後。
TAB_ORDER = (TAB_SSH, TAB_WINRM, TAB_AGENT, TAB_STORAGE, TAB_NETWORK, TAB_ESXI,
             TAB_NOK_OTHER, TAB_IMPORT)
#: 「動得了的」＝真的有下一步的這三個頁簽（不納管的、只能匯入的都沒有）
ACTIONABLE_TABS = (TAB_SSH, TAB_WINRM, TAB_AGENT)
TAB_LABEL = {
    TAB_SSH: "SSH 收集", TAB_WINRM: "WinRM 收集", TAB_AGENT: "Push Agent",
    TAB_STORAGE: "儲存設備", TAB_NETWORK: "網路設備", TAB_ESXI: "ESXi",
    TAB_NOK_OTHER: "其他（另有方式）", TAB_IMPORT: "只能匯入",
}
_NOK_KIND_TAB = {"storage": TAB_STORAGE, "network": TAB_NETWORK, "esxi": TAB_ESXI}
#: 反向：頁簽 → 不納管類別（人工指定搬到不納管頁簽時要寫回這一欄）
_TAB_NOK_KIND = {TAB_STORAGE: "storage", TAB_NETWORK: "network", TAB_ESXI: "esxi",
                 TAB_NOK_OTHER: "appliance"}

# ===== 人工指定類別 → 頁簽（2026-10-16）=====
# 使用者在公司機 1.428.0：「我還是沒看到可以移動到其他組別的方式」。
# 實例：`SVR019-203`（主機名去掉公司字首）是一般伺服器，但資產登記的「作業系統」欄
# 寫「儲存設備」，於是被歸到儲存設備頁簽。自動分類一定有錯的時候，人要能自己改。
#
# **不另做第二套儲存**：沿用 2026-09-18 就做好的 `os_override`（os_type_override 表）——
# 重匯 CIA 不沖掉、主機名＋IP 都對才套用、留誰／何時／原因／機器原本判什麼、取消是軟刪除。
# 那張表存的是**漏斗的 OS 大類**（pipeline.OS_*），/adopt 的頁簽是另一套字彙，
# 所以要一組對照。對照是**雙向且一致**的：搬到頁簽 X 寫進 `_tab_os()[X]`，
# 讀回來用 `_os_tab()` 一定回到 X（test_adopt_move.py 釘住往返）。
#
# ⚠️ `TAB_AGENT`（待裝 Agent）**刻意不給搬**：那是「活著但進不去」的連線狀態，
# 不是機器的類別；給它一個 OS 大類等於用類別去描述連不連得上。
# `TAB_IMPORT`（只能匯入）同理，是「完全不通」。


#: 對照只算一次。`dispatch_tab` 會被每一列各呼叫一次（掃一個 /24 就是兩百多次），
#: 每次重建 dict 是白花的。pipeline 的那些常數是模組級字串，不會中途變。
_TAB_OS_CACHE: dict[str, str] = {}
_OS_TAB_CACHE: dict[str, str] = {}


def _tab_os() -> dict[str, str]:
    """頁簽 → 要寫進 os_override 的 OS 大類。"""
    if _TAB_OS_CACHE:
        return _TAB_OS_CACHE
    import pipeline as p
    _TAB_OS_CACHE.update({
        TAB_SSH: p.OS_LINUX,
        TAB_WINRM: p.OS_WINDOWS,
        TAB_STORAGE: p.OS_STORAGE,
        TAB_NETWORK: p.OS_NETWORK,
        TAB_ESXI: p.OS_ESXI,
        TAB_NOK_OTHER: p.OS_APPLIANCE,
    })
    return _TAB_OS_CACHE


def _os_tab() -> dict[str, str]:
    """OS 大類 → 頁簽。比 _tab_os() 多的那幾類（AIX／IBM i／OpenShift／BMC）
    是漏斗有、頁簽沒有的，各自歸到意思最近的頁簽；它們不是搬移的目的地，
    只有在資產明細頁搬過、再回到 /adopt 看時才會走到這裡。"""
    if _OS_TAB_CACHE:
        return _OS_TAB_CACHE
    import pipeline as p
    _OS_TAB_CACHE.update({v: k for k, v in _tab_os().items()})
    _OS_TAB_CACHE[p.OS_AIX] = TAB_SSH            # AIX 也走 SSH 收集
    _OS_TAB_CACHE[p.OS_IBMI] = TAB_NOK_OTHER     # IBM i 不在可納管白名單
    _OS_TAB_CACHE[p.OS_OPENSHIFT] = TAB_NOK_OTHER
    _OS_TAB_CACHE[p.OS_BMC] = TAB_NOK_OTHER
    return _OS_TAB_CACHE


#: 可以當搬移目的地的頁簽（順序＝畫面上選單的順序）
MOVABLE_TABS = (TAB_SSH, TAB_WINRM, TAB_STORAGE, TAB_NETWORK, TAB_ESXI, TAB_NOK_OTHER)


def os_type_for_tab(tab: str) -> str | None:
    """搬到這個頁簽要寫進 os_override 的類別；不能搬的頁簽回 None。"""
    return _tab_os().get(tab)


def tab_for_os_type(os_type: str | None) -> str | None:
    return _os_tab().get(os_type or "")


def move_choices() -> list[dict]:
    """給 /adopt 的搬移選單：頁簽、畫面標籤、會寫進 os_override 的類別、搬過去納不納管。"""
    t2o = _tab_os()
    return [{"tab": t, "label": TAB_LABEL[t], "os_type": t2o[t],
             "onboardable": t in ACTIONABLE_TABS} for t in MOVABLE_TABS]


# ===== 「那它走哪條路」：不納管 ≠ 不管（2026-10-16）=====
# 使用者：「網路設備／ESXi／儲存 這些都有它獨立納管方式」「不是靠 Linux 方式納管」。
#
# 修完不納管判定之後，那三台 ESXi 落在 ESXi 頁簽了，但「走哪條路」欄還寫 **Push Agent**
#（route 是探測事實：ESXi 只開 443/902，choose_route 回 agent），字面讀起來還像在叫人
# 去裝 agent；「下一步」寫「不納管」也是錯的訊息——它們不是不管，是**不走 Linux 那套**
#（SSH 進去建本機帳號）。
#
# ⚠️ **不竄改 route**：`by_route` 統計、前端的 `isWindowsOnSsh` 都依賴它是探測事實。
# 改的是**顯示**：多給一欄「該走哪條收集路徑」，畫面拿它蓋掉那一格的文字，
# route 原值留在 tooltip 裡。
#
# 一張表，之後新增類別只改表，不要長成一串 if。
_NOK_GUIDE = {
    "esxi":      {"how": "vCenter／RVTools 匯入", "page": "/import", "page_label": "資料匯入"},
    "storage":   {"how": "SAN 收集", "page": "/san", "page_label": "SAN 收集"},
    # IBM i／Power 走 HMC 拿 LPAR 與實體機資訊，不是 SSH 建本機帳號
    "ibmi":      {"how": "HMC 收集", "page": "/hmc", "page_label": "HMC 收集"},
    # OpenShift 節點歸 OCP 管，本機帳號會在節點重佈時消失（見 onboard_eligibility）
    "immutable": {"how": "OCP 叢集明細", "page": "/ocp", "page_label": "OCP 叢集明細"},
    # 2026-10-16 使用者：「暫時無打算」——**不要假裝有路**
    "network":   {"how": None, "page": None, "page_label": None,
                  "note": "目前不收（2026-10-16 使用者：暫時無打算）"},
    "appliance": {"how": None, "page": None, "page_label": None,
                  "note": "沒有既有收集路徑——認不出是什麼設備"},
    "banner":    {"how": None, "page": None, "page_label": None,
                  "note": "沒有既有收集路徑——只有 SSH banner 自報身分"},
}
#: IBM i／Power 現在會被 `classify_os` 歸進 appliance（它不在可納管白名單裡），
#: 所以在 appliance 這一桶裡再認一次，才指得到 HMC 收集。只認作業系統欄的明確字樣，
#: 不從自由文字猜（跟 onboard_eligibility 的 `_FROM_FREE_TEXT` 同一個原則）。
#: ⚠️ `ibm\s*i` 后面一定要有詞界，不然 `IBM ServeRAID`、`IBM Informix` 這種
#: 「IBM 開頭的別的東西」也會被當成 IBM i。
_IBMI_RE = re.compile(r"\b(ibm\s*i|i5/?os|os/?400|as/?400|power\s*vm)\b", re.I)


def nok_guidance(kind: str | None, os_text: str | None = None) -> dict | None:
    """這一類不納管的機器**該走哪條收集路徑**。回 None ＝這一列不需要指路。

    `os_text` 只用在 appliance 這一桶的再細分（IBM i／Power → HMC 收集）。
    """
    if not kind:
        return None
    if kind == "appliance" and os_text and _IBMI_RE.search(str(os_text)):
        kind = "ibmi"
    g = _NOK_GUIDE.get(kind)
    if g is None:
        return None
    return {"kind": kind, **g}


def override_of(ov_map: dict | None, row: dict) -> dict | None:
    """這一列目前有沒有有效的人工指定。回 os_override 的留痕 ＋ 算出來的頁簽。

    認台方式完全交給 `os_override.resolve`（主機名＋IP 都對才套用），這裡不自己比。
    """
    if not ov_map:
        return None
    import os_override
    os_type, info = os_override.resolve(
        ov_map, row.get("hostname"), row.get("ip"), row.get("asset_serial"), "")
    if not info:
        return None
    tab = tab_for_os_type(os_type)
    if not tab:
        return None
    return {**info, "os_type": os_type, "tab": tab}


def dispatch_tab(row: dict, ov_map: dict | None = None) -> str:
    """這一列落在 /adopt 哪個頁簽。純函式；互斥由「先看不納管、再看 route」保證。

    `ov_map`（os_override.active_map）有這台的人工指定時**優先採用**——自動分類
    一定有錯的時候，人改過的不可以被機器判回去（重新收集也一樣，run_dispatch
    同樣吃這張表）。沒給 ov_map 就是純自動判定，行為跟以前一模一樣。
    """
    ov = override_of(ov_map, row)
    if ov:
        return ov["tab"]
    if row.get("status") == STATUS_NOT_ONBOARDABLE:
        return _NOK_KIND_TAB.get(row.get("not_onboardable_kind") or "", TAB_NOK_OTHER)
    route = row.get("route")
    return route if route in (TAB_SSH, TAB_WINRM, TAB_AGENT, TAB_IMPORT) else TAB_IMPORT


def _rejudge_kind(conn, row: dict, esxi_names) -> str | None:
    """舊 run 的不納管列重新歸類（只在讀取時，不改 DB）。

    兩種舊資料要補：
      - 類別欄是 NULL（1.422.0 之前跑的 run，當時沒存類別）
      - 類別是 appliance／banner，但其實是網路設備／儲存（network 類別 1.427.0 才有）
    不補的話，使用者升版後不重跑收集，畫面上的儲存／網路／ESXi 頁簽會是 0，
    那些機器全擠在「其他不納管」——等於這次修正在舊資料上看不出來。

    ⚠️ 重判用的是**同一條規則**（`onboard_eligibility.not_onboardable`／
    `kind_from_banner`），餵的是資產庫現在的登記資料，不是拿 message 自由文字另猜。
    banner 那一類例外：當初就是從連線訊息裡的 banner 判的，重判也只能讀同一段訊息，
    而且讀法跟當初一模一樣（`product_from_banner`）。
    """
    import onboard_eligibility as oe

    kind = row.get("not_onboardable_kind")
    if kind in (None, "appliance"):
        asset = _known_asset(conn, row["ip"]) if conn is not None else None
        if asset:
            hint = " ".join(x for x in (asset.get("asset_name"), asset.get("asset_purpose")) if x)
            blk = oe.not_onboardable(asset.get("os"), asset.get("hostname"), row["ip"],
                                     esxi_names, extra_text=hint or None)
            if blk:
                return blk[0]
    if kind in (None, oe.KIND_BANNER):
        product = oe.product_from_banner(row.get("message"))
        if product and "SSH banner" in (row.get("message") or ""):
            return oe.kind_from_banner(product)
    return kind


def _rejudge_block_candidate(row: dict) -> bool:
    """這一列值得在讀取時重跑一次「不納管」判定嗎？（2026-10-16）

    只收「活著、當初沒被判成不納管、而且也沒收到東西」的列。排除的三種：
      - `alive=0`：完全沒回應的不判（理由見 run_dispatch 裡那段）
      - 已經是不納管：那條走 `_rejudge_kind`（只補類別，不動結論）
      - **已經納管成功（collected）**：收得到就是收得到，不可以因為 os 欄寫著設備
        就把它改成不納管——那是用登記資料去否認一個已經成立的事實。
    """
    return bool(row.get("alive")) and row.get("status") not in (
        (STATUS_NOT_ONBOARDABLE,) + SUCCESS_STATUSES)


def _rejudge_block(conn, row: dict, esxi_names) -> None:
    """就地把「其實不納管」的舊列改寫成不納管（只在讀取時，不改 DB）。

    為什麼要有：1.427.x 之前「不納管」只在 `route == ssh` 時才判，所以 ESXi
    （預設不開 SSH，探測看到的是「活著、開放埠無」→ route=agent）全被寫成
    「待裝 Agent」。使用者升版後若不重跑收集，畫面上 ESXi 頁簽還是 0、
    還是叫他去 ESXi 上裝 Push Agent——這次修正在舊資料上看不出來。

    ⚠️ 用的是**同一條規則**（`onboard_eligibility.not_onboardable`），餵資產庫
    現在的登記資料；判不出來就原封不動（失敗方向維持「漏標」）。
    """
    if conn is None:
        return
    import onboard_eligibility as oe

    asset = _known_asset(conn, row["ip"])
    hint = " ".join(x for x in ((asset or {}).get("asset_name"),
                                (asset or {}).get("asset_purpose")) if x)
    blk = oe.not_onboardable((asset or {}).get("os"), (asset or {}).get("hostname"),
                             row["ip"], esxi_names, extra_text=hint or None)
    if not blk:
        return
    row["status"] = STATUS_NOT_ONBOARDABLE
    row["not_onboardable_kind"] = blk[0]
    row["message"] = f"{blk[1]}——{NOT_ONBOARDABLE_SENTENCE}。{blk[2]}（依現在的資產登記重判）"


def summarize(results: list[dict], ov_map: dict | None = None) -> dict:
    """把逐台結果收成畫面上那幾個數字。成功／待人工是主軸，路由分佈是次要。

    `ov_map` 給了就把人工指定寫回每一列（`os_override`）並據它算頁簽——
    頁簽數字與點下去的列仍是同一份計算（加總＝下鑽的鐵律不變）。
    """
    by_status: dict[str, int] = {}
    by_route: dict[str, int] = {}
    # 不納管的再依類別拆開（2026-10-13 使用者：「不納管（ESXi／設備）11」是合計的，
    # 看不出其中幾台是儲存設備——他要看得到）。
    #
    # ⚠️ 這裡只統計、**不重新判定**：類別是當初擋下來時就記在那一列上的，
    # 不是現在拿 message 反推。反推等於同一件事有第二套規則。
    # （舊 run 缺類別的補判在 latest_run 呼叫 _rejudge_kind，用的是同一條規則，
    #   不在這裡做——summarize 是純統計，給什麼算什麼。）
    #
    # 沒有類別的列不計（`if kind`）：既有 DB 在這一欄出現前跑的那些 run，
    # 欄位是 NULL。把它們歸進某一類會憑空多出數字，寧可讓小計加不回總數——
    # 畫面上那是「類別未留存」，不是「沒有這一類」。
    not_onboardable_by_kind: dict[str, int] = {}
    # 頁簽：每列寫回 `tab`，同一個迴圈算 `by_tab`——頁簽數字跟點下去的列是同一份計算
    by_tab: dict[str, int] = {t: 0 for t in TAB_ORDER}
    for r in results:
        # 人工指定寫回這一列：畫面要標得出「這是人改的」，滑過看得到誰／何時／從哪類改到哪類
        ov = override_of(ov_map, r)
        if ov:
            r["os_override"] = ov
        r["tab"] = dispatch_tab(r, ov_map)
        # 不納管的要指路（見 _NOK_GUIDE）：「不納管」不是結論，「它走哪條路」才是。
        # `asset_os` 由呼叫端帶進來（run_dispatch 手上就有、latest_run 另外補），
        # 只用在 appliance 這一桶再認 IBM i／Power。
        if r["status"] == STATUS_NOT_ONBOARDABLE:
            g = nok_guidance(r.get("not_onboardable_kind"), r.get("asset_os"))
            if g:
                r["guide"] = g
        by_tab[r["tab"]] = by_tab.get(r["tab"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_route[r["route"]] = by_route.get(r["route"], 0) + 1
        if r["status"] == STATUS_NOT_ONBOARDABLE:
            kind = r.get("not_onboardable_kind")
            if kind:
                not_onboardable_by_kind[kind] = not_onboardable_by_kind.get(kind, 0) + 1
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
        # 類別→台數。數字 0 的類別不會出現在這裡（沒有就是沒有，不要補 0）
        "not_onboardable_by_kind": not_onboardable_by_kind,
        # 空位址不算待辦，但要看得見——不然使用者會以為系統漏掉了什麼
        "empty_addresses": empty_addresses,
        "needs_action": len(results) - collected - not_onboardable - empty_addresses,
        "by_status": by_status,
        "by_route": by_route,
        # 頁簽→台數。**互斥且窮盡**：加起來一定等於 total（test 釘住）。
        # 跟 not_onboardable_by_kind 不同，0 也列出來——頁簽位置固定，不因 0 消失。
        "by_tab": by_tab,
        "tab_order": list(TAB_ORDER),
        "tab_label": dict(TAB_LABEL),
        "actionable_tabs": list(ACTIONABLE_TABS),
    }


def latest_run(conn) -> dict | None:
    """最近一次分派的完整結果（含逐台）。頁面重新整理後靠這支還原。"""
    run = conn.execute(
        "SELECT * FROM collect_dispatch_run ORDER BY id DESC LIMIT 1").fetchone()
    if run is None:
        return None
    results = [dict(r) for r in conn.execute(
        "SELECT ip, alive, open_ports, route, status, asset_serial, hostname, "
        "registered, message, not_onboardable_kind FROM collect_dispatch_result "
        "WHERE run_id = ? ORDER BY id",
        (run["id"],))]
    # 人工指定（os_override）：整次只查一次。**人改過的列不重判**——
    # `_rejudge_*` 是拿「現在的資產登記」重跑機器判定，而人工指定存在的理由
    # 正是那份登記判錯了（`SVR019-203` 的作業系統欄寫著「儲存設備」）。
    # 重判會把人改的結論蓋回去，等於搬移不算數。
    import os_override
    ov_map = os_override.active_map(conn)
    overridden = {id(r) for r in results if override_of(ov_map, r)}

    # 舊 run 的不納管列補歸類（見 _rejudge_kind）。esxi 名單整次只查一次。
    need_kind = any(r["status"] == STATUS_NOT_ONBOARDABLE
                    and r.get("not_onboardable_kind") in (None, "appliance", "banner")
                    for r in results if id(r) not in overridden)
    # 舊 run 的「不納管判定沒跑到」那些列也要補（見 _rejudge_block）
    need_block = any(_rejudge_block_candidate(r) for r in results if id(r) not in overridden)
    if need_kind or need_block:
        import onboard_eligibility
        esxi_names = onboard_eligibility.esxi_host_names(conn)
        for r in results:
            if id(r) in overridden:
                continue
            if _rejudge_block_candidate(r):
                _rejudge_block(conn, r, esxi_names)
            if r["status"] == STATUS_NOT_ONBOARDABLE:
                r["not_onboardable_kind"] = _rejudge_kind(conn, r, esxi_names)

    # 資產登記現查（不存快照，見 run_dispatch 裡 asset_name 的註解）。整批一次問，
    # 掃一個 /24 不要發 254 次查詢。兩個用途共用這一份：
    #   1. 畫面上的「資產名稱」欄（2026-10-16 使用者：「加入資產名稱欄位」）
    #   2. 指路時 appliance 那一桶再細分 IBM i／Power（只有那幾列要 asset_os）
    assets = _known_assets(conn, [r["ip"] for r in results])
    for r in results:
        a = assets.get(r["ip"])
        r["asset_name"] = (a or {}).get("asset_name")
        if (r["status"] == STATUS_NOT_ONBOARDABLE
                and r.get("not_onboardable_kind") == "appliance"):
            r["asset_os"] = (a or {}).get("os")

    # 在人工指定之前跑的那一輪，status 還是舊結論（例如還寫著「不納管」），
    # 但頁簽已經搬走了。畫面上不可以只搬位置不講話——那會變成「躺在 SSH 收集頁的
    # 不納管」，看起來像系統算錯。這裡只改 message（**不假造 status**：
    # 我們確實還沒去試連過，要等下一次收集才知道連不連得上）。
    for r in results:
        ov = override_of(ov_map, r)
        if not ov:
            continue
        if ov["tab"] in ACTIONABLE_TABS and r["status"] == STATUS_NOT_ONBOARDABLE:
            r["message"] = (f"人工指定為「{TAB_LABEL[ov['tab']]}」"
                            f"（{ov['by'] or '不詳'} 於 {ov['at']}）——"
                            f"這一輪收集是在指定之前跑的，下次收集才會真的試連。"
                            f"原本的判定：{r['message']}")
    return {"run": dict(run), **summarize(results, ov_map), "results": results}


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
