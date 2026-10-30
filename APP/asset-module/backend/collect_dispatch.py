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

import fleet_cache as _fleet_cache
import ipaddress
import json
import re
from concurrent.futures import ThreadPoolExecutor

import win_onboard

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
# 2026-10-19：活著但 22／445／5985 都不通的機器，以前一律「待裝 Agent」。但 Push Agent 只支援
# Linux／Unix——Windows 與 AIX 裝不上。先判斷是什麼機器（host_kind），再給對的下一步：
STATUS_NEEDS_WINRM_PORT = "needs_winrm_port"  # 判斷是 Windows → 請開 WinRM（5985）給收集器
STATUS_NEEDS_SSH_PORT = "needs_ssh_port"      # 判斷是 AIX → 請開 SSH（22）給收集器
# 2026-10-22：Windows 勾選才收。445／5985 開著但這台還沒被人「納管」→ 這一輪**不登入**，
# 只記探測結果（走哪條路照樣是 WinRM）。人在 2-1 WinRM 頁簽勾選按「納管」才登入收集（見 win_onboard）。
STATUS_WIN_PENDING = "win_pending"
#: 由「是什麼機器」決定的那幾個狀態（同一台在讀取時會依現在的證據重判）
AGENT_FAMILY = (STATUS_NEEDS_AGENT, STATUS_NEEDS_WINRM_PORT, STATUS_NEEDS_SSH_PORT)

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


# ===== 收集節奏設定（v1.468.4，2026-10-19 使用者：「收集一次要很久，有什麼改善方式」）=====
# 都存 app_settings，6 系統設定 → 掃描排程頁看得到、改得了（現值照實顯示）。
# ⚠️ 批次間隔是刻意的 SOC 節奏（一次對上千台送封包像掃描），只開放調整、不拿掉。
#: 「只收新的」：上次結果在這幾小時內、而且是定案狀態（已納管／空位址）的位址這次不重探
DEFAULT_SKIP_FRESH_HOURS = 24
#: 合併檢視：結果超過這幾天的列標「舊」
DEFAULT_STALE_DAYS = 30
#: 「只收新的」裡**空位址**（沒回應也沒登記）另用較長的時間窗（v1.468.5）。
#: 9900 個位址多數是空的，它們每天重探就是每天重付探測＋批次間隔；新機器上線另有
#: 排程存活掃描會發現——掃描看到它活著（比上次收集新）就照樣重探，不會被這個窗蓋掉。
DEFAULT_SKIP_EMPTY_HOURS = 168
#: 探測的並行數與每埠逾時（v1.468.6，221 實測見 fast_probe）。並行數維持以前的 64：
#: 四個埠改成同時連之後，同時在途的連線已經從 64 變 256（SOC 不申請，不再往上加；
#: 要更快可在 6 系統設定調到 128 上限）。逾時 0.4 秒對內網綽綽有餘（同機房 RTT 是毫秒級），
#: 以前「有活過」的位址這次沒回應會用 1 秒再試一次，慢但活著的機器不會被誤判成空位址。
DEFAULT_PROBE_WORKERS = 64
DEFAULT_PROBE_TIMEOUT_MS = 400
# 心跳逾時（2026-10-20 公司機：2-1 頁簽「收集中…」亮很久，看不出是真的在跑還是早就死了）：
# running 的列超過這幾分鐘沒有任何進度 → 畫面講「可能已中斷」，不再講「收集中」。
DEFAULT_HEARTBEAT_STALE_MINUTES = 5
#: 曾經活過、這次沒回應 → 用這個逾時再試一次
RETRY_TIMEOUT_S = 1.0
_DISPATCH_SETTING_RANGE = {
    # key: (預設, 最小, 最大)
    "dispatch_batch_size": (BATCH_SIZE, 16, MAX_TARGETS),
    "dispatch_batch_gap_seconds": (DEFAULT_BATCH_GAP_SECONDS, 0, 3600),
    "dispatch_skip_fresh_hours": (DEFAULT_SKIP_FRESH_HOURS, 0, 24 * 30),
    "dispatch_stale_days": (DEFAULT_STALE_DAYS, 1, 3650),
    "dispatch_skip_empty_hours": (DEFAULT_SKIP_EMPTY_HOURS, 0, 24 * 90),
    "dispatch_probe_workers": (DEFAULT_PROBE_WORKERS, 16, 128),
    "dispatch_probe_timeout_ms": (DEFAULT_PROBE_TIMEOUT_MS, 200, 2000),
    "dispatch_heartbeat_stale_minutes": (DEFAULT_HEARTBEAT_STALE_MINUTES, 3, 120),
    # 0／1 開關，見 OCP_PROBE_PORT。**預設關**：正式區收集端只申請開通 DISPATCH_PORTS 那四個埠
    "dispatch_ocp_port_probe": (0, 0, 1),
}
_SETTING_FIELD = {"batch_size": "dispatch_batch_size", "gap_seconds": "dispatch_batch_gap_seconds",
                  "skip_fresh_hours": "dispatch_skip_fresh_hours", "stale_days": "dispatch_stale_days",
                  "skip_empty_hours": "dispatch_skip_empty_hours",
                  "probe_workers": "dispatch_probe_workers",
                  "probe_timeout_ms": "dispatch_probe_timeout_ms",
                  "heartbeat_stale_minutes": "dispatch_heartbeat_stale_minutes",
                  "ocp_port_probe": "dispatch_ocp_port_probe"}


def _int_setting(conn, key: str) -> int:
    from db import get_setting

    default, lo, hi = _DISPATCH_SETTING_RANGE[key]
    try:
        return min(hi, max(lo, int(get_setting(conn, key, str(default)))))
    except (TypeError, ValueError):
        return default


def batch_size_setting(conn) -> int:
    """一批幾台（預設 1024）。"""
    return _int_setting(conn, "dispatch_batch_size")


def skip_fresh_hours(conn) -> int:
    """「只收新的」的時間窗（小時）。0＝關掉（每次都全部重探）。"""
    return _int_setting(conn, "dispatch_skip_fresh_hours")


def skip_empty_hours(conn) -> int:
    """空位址的「只收新的」時間窗（小時）。0＝空位址每次都重探。"""
    return _int_setting(conn, "dispatch_skip_empty_hours")


def registered_ips(conn) -> list[str]:
    """「只掃已登記的 IP」（v1.468.5）：資產庫裡現役（非退役）資產的 IP，去重、照 IP 排序。

    公司掃整片網段 9906 個位址、約 8954 個是空的——九成時間花在沒人的位址上。
    白天要看「我登記的這些機器現在收不收得到」，只掃登記的就夠；整片網段的新機器發現
    照樣可以用輸入網段的方式跑。一格寫了多個 IP（逗號、分號、空白分隔）的都拆開。
    0.0.0.0／127.x／格式不對的不算。
    """
    import ipaddress
    import re as _re

    import manage_state

    out: set[str] = set()
    for r in conn.execute("SELECT ip, asset_status FROM hardware WHERE ip IS NOT NULL AND ip != ''"):
        if (r["asset_status"] or "").strip() in manage_state.RETIRED_STATUS:
            continue
        for tok in _re.split(r"[\s,;／/、]+", r["ip"] or ""):
            try:
                a = ipaddress.IPv4Address(tok.strip())
            except ValueError:
                continue
            if a.is_unspecified or a.is_loopback:
                continue
            out.add(str(a))
    return sorted(out, key=lambda x: tuple(int(p) for p in x.split(".")))


class NoRegisteredInRange(ValueError):
    """勾了「只掃已登記」、範圍內卻一台登記的都沒有。這不是收集失敗，是篩選把範圍篩空了
    ——畫面要講原因＋給「取消只掃已登記再收一次」，不要紅字「收集失敗」。"""


def _split_tokens(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").replace(",", "\n").replace(";", "\n").split() if t.strip()]


def registered_targets(conn, targets_raw: str | None) -> tuple[str, str]:
    """只掃已登記：回 (給 run_dispatch 的目標文字, 記在 run 上的說明)。

    2026-10-20 公司機兩次踩到，規則改成：
      1. **空白絕不代表全部**（勾了網段沒按「加入範圍」→ 框是空的 → 以前跑了全部 4565 個登記 IP，
         停不下來）。沒輸入一律 ValueError。
      2. 篩選**只套在網段／範圍**；直接打的單一 IP 一律照收（人指名要收這台，例如沒登記的測試機）。
      3. 範圍篩完一台都沒有 → NoRegisteredInRange（畫面當提示，不當失敗）。
    """
    raw = (targets_raw or "").strip()
    if not raw:
        raise ValueError("沒有輸入範圍——空白不代表全部。請輸入網段或 IP，"
                         "或在「從網段配置表挑」勾好後按「加入範圍」")
    singles: list[str] = []
    ranges: list[str] = []
    for tok in _split_tokens(raw):
        # x.x.x.x/32 也是指名一台（公司機使用者就是打 142/32），算單一 IP
        is_single = tok.endswith("/32") or ("/" not in tok and "-" not in tok)
        (singles if is_single else ranges).append(tok)
    single_ips = parse_targets("\n".join(singles)) if singles else []
    want = set(parse_targets("\n".join(ranges))) if ranges else set()
    reg_in = [ip for ip in registered_ips(conn) if ip in want]
    out = list(dict.fromkeys(single_ips + reg_in))
    if not out:
        raise NoRegisteredInRange(
            "這個範圍內沒有已登記的 IP（勾了「只掃已登記的 IP」，網段裡只收有登記的）")
    extra = f"＋直接指定 {len(single_ips)} 個" if single_ips else ""
    label = f"只掃已登記的 IP（{len(reg_in)} 個{extra}，範圍：{raw[:300]}）"
    return "\n".join(out), label


def ocp_port_probe_on(conn) -> bool:
    """「探 10250 認 OCP」開關（預設關）。"""
    return _int_setting(conn, "dispatch_ocp_port_probe") == 1


def probe_settings(conn) -> tuple[int, float]:
    """(並行數, 每埠逾時秒數)。"""
    return (_int_setting(conn, "dispatch_probe_workers"),
            _int_setting(conn, "dispatch_probe_timeout_ms") / 1000.0)


def _ever_alive(conn, ips) -> set[str]:
    """這些位址以前有沒有活過（收集結果活著，或排程存活掃描看過它）。給「沒回應再試一次」用。"""
    out: set[str] = set()
    ips = list(dict.fromkeys(ips))
    for i in range(0, len(ips), 400):
        chunk = ips[i:i + 400]
        qs = ",".join("?" * len(chunk))
        for sql in (f"SELECT DISTINCT ip FROM collect_dispatch_result WHERE alive = 1 AND ip IN ({qs})",
                    f"SELECT DISTINCT ip FROM scan_history WHERE scan_ok = 1 AND ip IN ({qs})"):
            try:
                out |= {r["ip"] for r in conn.execute(sql, chunk)}
            except Exception:  # noqa: BLE001 - 舊庫少表：沒有證據就不重試
                pass
    return out


def heartbeat_stale_minutes(conn) -> int:
    return _int_setting(conn, "dispatch_heartbeat_stale_minutes")


# ---- 這一輪還活著嗎／手動停止（2026-10-20）----
# 背景收集是 API 行程裡的一條執行緒。服務一重啟（裝 patch、systemctl restart）執行緒就沒了，
# 但 collect_dispatch_run 那一列還寫著 running——以前畫面只看這個字，於是「收集中…」永遠亮著，
# 每週排程也被它擋 6 小時。現在兩道：
#   1. 服務啟動時把所有 running 收尾成「已中斷（服務重啟）」（單一 worker：啟動當下不可能有活的執行緒）
#   2. 讀取時看心跳：超過 N 分鐘沒進度（批次間隔中則以「下一批預計時間＋N 分」為準）→ 回 stale，
#      畫面講「可能已中斷：最後進度 HH:MM，第 x/y 批」並給管理員「標記為已中斷」——不默默藏掉。
STATUS_INTERRUPTED = "interrupted"
STATUS_STOPPED = "stopped"
INTERRUPTED_BY_RESTART = "已中斷（服務重啟）"
INTERRUPTED_BY_HAND = "已中斷（人工標記）"

# 「停止收集」旗標：每一輪一個 Event（背景那輪跑的同時，小批次的同步收集也可能在跑，不能共用）。
# 批與批之間、以及批次間隔的等待中都會看它——間隔用 Event.wait，按停止馬上醒來。
_stop_events: dict = {}
_tl = __import__("threading").local()


def request_stop(run_id: int) -> bool:
    """要求停止正在這個行程裡跑的那一輪。回 True＝送到了（會在這一批做完後停）。"""
    ev = _stop_events.get(run_id)
    if ev is None:
        return False
    ev.set()
    return True


def _run_cols_of(conn) -> set:
    return {c[1] for c in conn.execute("PRAGMA table_info(collect_dispatch_run)")}


def _heartbeat(conn, run_id: int, next_batch_at=None, cols=None) -> None:
    """記一次心跳（不 commit，跟呼叫端的 commit 一起進）。舊庫沒這欄就不記。"""
    cols = cols if cols is not None else _run_cols_of(conn)
    if "heartbeat_at" not in cols:
        return
    conn.execute("UPDATE collect_dispatch_run SET heartbeat_at=datetime('now','localtime'), "
                 "next_batch_at=? WHERE id=?", (next_batch_at, run_id))


def _parse_ts(v):
    from datetime import datetime

    try:
        return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S") if v else None
    except ValueError:
        return None


def run_liveness(conn, run: dict, now=None) -> dict:
    """running 的列：live（真的在跑）或 stale（心跳逾時，可能已中斷）。其餘狀態照 status 回。

    回 {"liveness", "last_progress_at", "next_batch_at", "stale_minutes", "stop_requested"}。
    沒有心跳欄的舊列用 started_at 當最後進度。
    """
    from datetime import datetime, timedelta

    n_min = heartbeat_stale_minutes(conn)
    last = run.get("heartbeat_at") or run.get("started_at")
    nxt = run.get("next_batch_at")
    out = {"liveness": run.get("status"), "last_progress_at": last, "next_batch_at": None,
           "stale_minutes": n_min,
           "stop_requested": bool(_stop_events.get(run.get("id")) and _stop_events[run.get("id")].is_set())}
    if run.get("status") != "running":
        return out
    now = now or datetime.now()
    deadlines = [t + timedelta(minutes=n_min) for t in (_parse_ts(last), _parse_ts(nxt)) if t]
    live = bool(deadlines) and now <= max(deadlines)
    out["liveness"] = "live" if live else "stale"
    if live and _parse_ts(nxt) and _parse_ts(nxt) > now:
        out["next_batch_at"] = nxt
    return out


def finalize_orphans(conn) -> int:
    """服務啟動時呼叫：所有還寫著 running 的列＝上一個行程留下的孤兒，收尾成已中斷。回幾列。"""
    if not _run_cols_of(conn):
        return 0
    n = conn.execute(
        "UPDATE collect_dispatch_run SET status=?, error=?, "
        "finished_at=datetime('now','localtime') WHERE status='running'",
        (STATUS_INTERRUPTED, INTERRUPTED_BY_RESTART)).rowcount
    conn.commit()
    return n or 0


def mark_interrupted(conn, run_id: int, by=None) -> dict:
    """管理員按「標記為已中斷」：只收 running、而且心跳已逾時的列（活著的請用「停止收集」）。"""
    row = conn.execute("SELECT * FROM collect_dispatch_run WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise LookupError("找不到這一輪收集")
    run = dict(row)
    if run["status"] != "running":
        raise ValueError("這一輪已經結束了，不用標")
    if run_id in _stop_events:
        raise ValueError("這一輪的執行緒還活著（只是暫時沒進度）——要停請用「停止收集」")
    if run_liveness(conn, run)["liveness"] == "live":
        raise ValueError("這一輪還有進度（心跳沒有逾時）——要停請用「停止收集」")
    conn.execute("UPDATE collect_dispatch_run SET status=?, error=?, "
                 "finished_at=datetime('now','localtime') WHERE id=? AND status='running'",
                 (STATUS_INTERRUPTED, INTERRUPTED_BY_HAND + (f"：{by}" if by else ""), run_id))
    conn.commit()
    return dict(conn.execute("SELECT * FROM collect_dispatch_run WHERE id=?", (run_id,)).fetchone())


#: 收完的結果卡（2026-10-20 使用者：「寫收集完了，我也不知道有沒有成功還是失敗」）——
#: 這一輪自己的結果分五堆，順序固定。每一列只落在一堆（加總＝這一輪的位址數）。
RESULT_BUCKETS = (
    ("collected", "✓ 已納管"),
    ("needs_credential", "待佈身分"),
    ("failed", "✕ 失敗"),
    ("no_response", "沒回應"),
    ("other", "其他（Windows 待納管／另有納管方式／待開埠／待裝 Agent）"),
)


def _bucket_of(row: dict) -> str:
    st = row.get("status")
    if st == STATUS_COLLECTED:
        return "collected"
    if st == STATUS_NEEDS_CREDENTIAL:
        return "needs_credential"
    if st in (STATUS_FAILED, STATUS_ONBOARD_FAILED):
        return "failed"
    if not row.get("alive"):
        return "no_response"
    return "other"


def run_result_summary(conn, run_id: int) -> dict:
    """某一輪自己的結果（不是合併檢視）：五堆的台數＋逐列（IP、主機名、狀態、原因）。"""
    run = conn.execute("SELECT * FROM collect_dispatch_run WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise LookupError("找不到這一輪收集")
    rows = [dict(r) for r in conn.execute(
        "SELECT ip, hostname, status, alive, message FROM collect_dispatch_result "
        "WHERE run_id=? ORDER BY id", (run_id,))]
    counts = {k: 0 for k, _ in RESULT_BUCKETS}
    for i, r in enumerate(rows, 1):
        r["no"] = i
        r["bucket"] = _bucket_of(r)
        r["status_label"] = STATUS_LABEL.get(r["status"], r["status"])
        counts[r["bucket"]] += 1
    return {"run": dict(run), "total": len(rows), "counts": counts,
            "buckets": [{"key": k, "label": lab} for k, lab in RESULT_BUCKETS], "rows": rows}


def stale_days(conn) -> int:
    return _int_setting(conn, "dispatch_stale_days")


def dispatch_settings(conn) -> dict:
    """收集節奏的現值＋可設範圍＋預設（給 6 系統設定與 2-1 頁顯示）。"""
    return {
        **{f: _int_setting(conn, k) for f, k in _SETTING_FIELD.items()},
        "hard_max": HARD_MAX_TARGETS,
        "range": {f: list(_DISPATCH_SETTING_RANGE[k][1:]) for f, k in _SETTING_FIELD.items()},
        "default": {f: _DISPATCH_SETTING_RANGE[k][0] for f, k in _SETTING_FIELD.items()},
    }


def save_dispatch_settings(conn, values: dict) -> dict:
    """存設定；超出範圍丟 ValueError（呼叫端轉 400）。只存有給的欄位。"""
    from db import set_setting

    for f, v in values.items():
        if v is None or f not in _SETTING_FIELD:
            continue
        _d, lo, hi = _DISPATCH_SETTING_RANGE[_SETTING_FIELD[f]]
        if not (lo <= int(v) <= hi):
            raise ValueError(f"{f} 要在 {lo}～{hi} 之間")
        set_setting(conn, _SETTING_FIELD[f], str(int(v)))
    return dispatch_settings(conn)


def fresh_ips(conn, ips: list[str], hours: int, empty_hours: int | None = None) -> set[str]:
    """這批位址裡，上次結果在 `hours` 小時內、而且是定案狀態的（這次可以不重探）。

    定案＝已納管（collected）或空位址（import_only 且沒登記）。空位址還要**現在**也沒登記
    ——中間有人把它登記成資產，就要重探（資料變了）。其餘狀態（待佈身分、納管失敗、
    進不去、已登記卻沒回應…）都是待辦，一律重探，才看得到處理後的變化。

    `empty_hours`（v1.468.5）：空位址另用的時間窗（None＝跟 hours 一樣）。hours<=0＝整個功能關掉。
    空位址在那之後被排程存活掃描看到活著（scan_history 比上次收集新）→ 重探。
    """
    if hours <= 0 or not ips:
        return set()
    from datetime import datetime, timedelta

    empty_hours = hours if empty_hours is None else max(0, int(empty_hours))
    _now = datetime.now()
    cutoff_hit = (_now - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_empty = (_now - timedelta(hours=empty_hours)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff = min(cutoff_hit, cutoff_empty)
    want = set(ips)
    out: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT ip, status, registered, created_at FROM collect_dispatch_result "
            "WHERE id IN (SELECT MAX(id) FROM collect_dispatch_result GROUP BY ip) "
            "AND created_at >= ?", (cutoff,)).fetchall()
    except Exception:  # noqa: BLE001 - 舊庫沒有結果表：沒有可跳過的
        return set()
    empties: dict[str, str] = {}
    for r in rows:
        if r["ip"] not in want:
            continue
        if r["status"] == STATUS_COLLECTED:
            if r["created_at"] >= cutoff_hit:
                out.add(r["ip"])
        elif (r["status"] == STATUS_IMPORT_ONLY and not r["registered"]
              and empty_hours > 0 and r["created_at"] >= cutoff_empty):
            empties[r["ip"]] = r["created_at"]
    if empties:
        now_reg = _known_assets(conn, list(empties))
        seen = _scan_alive_since(conn, empties)
        out |= {ip for ip in empties if ip not in now_reg and ip not in seen}
    return out


def _scan_alive_since(conn, since_by_ip: dict[str, str]) -> set[str]:
    """排程存活掃描在「上次收集」之後看到活著的位址（空位址有新機器上線了）。"""
    out: set[str] = set()
    ips = list(since_by_ip)
    try:
        for i in range(0, len(ips), 400):
            chunk = ips[i:i + 400]
            qs = ",".join("?" * len(chunk))
            for r in conn.execute(
                    f"SELECT ip, MAX(scan_time) AS t FROM scan_history WHERE scan_ok = 1 "
                    f"AND ip IN ({qs}) GROUP BY ip", chunk):
                if r["t"] and r["t"] > since_by_ip.get(r["ip"], ""):
                    out.add(r["ip"])
    except Exception:  # noqa: BLE001 - 舊庫沒有 scan_history：沒有新證據，照時間窗
        return set()
    return out


def plan_batches(ips: list[str], size: int = BATCH_SIZE) -> list[list[str]]:
    """切批。保留輸入順序——使用者貼的順序通常有意義（同機房排在一起）。"""
    size = max(1, int(size))
    return [ips[i:i + size] for i in range(0, len(ips), size)] or [[]]


#: 只有 Windows 會開的埠。Linux 不會有這兩個服務。
#: - 3389 RDP、5985 WinRM
#: ⚠️ **445 不算**：Linux 裝 Samba 一樣會開 445，拿它當 Windows 證據會誤判檔案伺服器。
WINDOWS_ONLY_PORTS = (3389, 5985)

# ===== 掃描就認出 OCP（2026-10-22，第一層；第二層是連上去讀到 rhcos 被拒，見 _credential_outcome）=====
# 不登 CIA 的新主機以前要等到有人試納管、連上去才認得出是 OCP。每台 OCP 節點的 kubelet
# 都開 10250（6443／22623 只有 master 開，不需要），一般 RHEL 極少開。
# ⚠️ 只在開關打開時探：正式區收集端只申請開通 22／445／3389／5985，探 10250 要先向網路單位
# 加申請「收集端 → 目標網段 TCP 10250」，不然可能觸發 SOC／IDS。只做 TCP connect，不送 HTTP。
# fail-closed：有回應就擋——不試連、不納管、不給重試；誤判的人可以「搬移分類」搬回 SSH（人工指定優先）。
OCP_PROBE_PORT = 10250
OCP_PROBE_LABEL = "疑似 OCP 節點"
OCP_PROBE_WHY = f"依據：{OCP_PROBE_PORT}（kubelet）有回應"


def _last_onboard_failure(conn, ip: str) -> dict | None:
    """這台最後一次 **SSH 那套**納管是不是失敗的。回失敗摘要，成功或沒試過回 None。

    只看 Linux／AIX 那套的紀錄（2026-10-22 審查）：同一個 IP 的 Windows 納管失敗（WinRM 帳密錯）
    不可以把 SSH 那一列講成「納管失敗」——那是另一條路、另一組帳號。"""
    try:
        import onboard_failures
    except ImportError:      # noqa: F401 - 模組不在（精簡佈署）就當沒有紀錄
        return None
    try:
        row = conn.execute(
            "SELECT ok, stage, message, created_at, "
            "(SELECT COUNT(*) FROM onboard_audit x WHERE x.target_ip = ? AND x.ok = 0 "
            " AND COALESCE(x.platform, '') <> 'windows') AS fail_count "
            "FROM onboard_audit WHERE target_ip = ? AND COALESCE(platform, '') <> 'windows' "
            "ORDER BY id DESC LIMIT 1", (ip, ip)).fetchone()
    except Exception:  # noqa: BLE001 - 舊庫沒這張表就當沒試過
        return None
    if row is None or row["ok"]:
        return None
    reason = onboard_failures.classify_reason(row["stage"], row["message"])
    return {"reason": reason, "stage": row["stage"], "message": (row["message"] or "")[:200],
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
    STATUS_NEEDS_WINRM_PORT: "待開 WinRM",
    STATUS_NEEDS_SSH_PORT: "待開 SSH",
    STATUS_IMPORT_ONLY: "只能匯入",
    STATUS_FAILED: "收集失敗",
    STATUS_WIN_PENDING: "待納管",
}


# ===== 探測 =====

def _default_prober(ip: str, timeout: float = 0.6, ports=DISPATCH_PORTS):
    """回 open port 清單；主機完全沒回應回 None。判定規則跟 net_scan._probe_host 一致
    （存活判定要跟掃描一致，不要兩套結論打架），只是四個埠**同時**連、不排隊等
    （v1.468.5：空位址 3.4 秒 → 1.6 秒，送出的封包一樣，見 fast_probe）。"""
    import fast_probe

    return fast_probe.probe_host(ip, ports, timeout=timeout)


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
    # 每台一個收集帳號（遷移過的那台是新帳號，舊的已刪）
    import collect_account_migration as cam

    account = cam.account_for_host(conn, (asset or {}).get("asset_serial"), platform)
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


def _do_winrm(conn, ip: str, asset, winrm_runner, cred_key_path, credential: str | None = None) -> tuple[str, str]:
    """走 WinRM：從加密憑證庫挑一組 winrm 憑證去收 facts。憑證明文用完即丟。"""
    import credential_store
    import winrm_collector

    if credential == credential_store.AUTO:                 # 納管時選「改回依網段自動」：這次登入不看指定
        name = credential_store.pick_for_host(conn, ip, kind="winrm", use_pref=False)
    elif credential:                                         # 納管時人挑的那一組（只能是已設定的 winrm 憑證）
        if not credential_store.valid_choice(conn, credential, "winrm"):
            return STATUS_NEEDS_CREDENTIAL, f"指定的憑證「{credential}」不存在，或不是 WinRM 憑證"
        name = credential
    else:
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
        # 收到的 facts 寫回資產（2026-10-20）：以前沒寫，OS 欄一直空的，納管後六樣判不出
        # 這台是 Windows、拿 SSH 去收 → 軟體 0 服務 0 帳號 0（公司機實測）
        manage_state.apply_collected_facts(conn, asset["asset_serial"], facts or {})
        winrm_collector.record_session(conn, ip, asset["asset_serial"], facts or {})
        conn.commit()
        got_host = facts.get("hostname") or ip
        return STATUS_COLLECTED, f"WinRM 收集成功（{got_host}）{_admin_text(facts)}"
    return STATUS_COLLECTED, (
        f"WinRM 收集成功{_admin_text(facts)}——但這台還沒登記成資產，請在下方「納入管理」建成資產")


def _admin_text(facts) -> str:
    """WinRM 結論帶上「是否本機管理員」（決策 WINRM-ADMIN1）。舊腳本沒回報就不講。"""
    v = (facts or {}).get("is_admin") if isinstance(facts, dict) else None
    if v is None:
        return ""
    return "；本機管理員：是" if v == 1 else "；本機管理員：否（服務／IIS／排程可能讀不完整，見 AD 申請單）"


def _agent_kind_outcome(ports, guess: dict) -> tuple[str, str, str | None]:
    """「活著但進不去」的機器，依判斷出來是什麼機器給狀態與下一步。回 (status, message, nok_kind)。

    Push Agent 只支援 Linux／Unix（2026-10-19）：Windows 叫人開 WinRM、AIX 叫人開 SSH、
    設備／VMware 走它自己的納管方式（VMware fail-closed：推測是 VMware 也擋）。
    判斷不出來的照舊留在 Push Agent，但下一步先講「先確認是什麼機器」。
    """
    import host_kind as hk

    seen = f"活著（開放埠 {','.join(map(str, ports)) or '無'}）但 22／445／5985 都不通"
    why = (f"判斷：{guess['label']}（{guess['confidence_label']}）" if guess["kind"] != hk.KIND_UNKNOWN
           else "判斷不出是什麼機器") + f"。依據：{'；'.join(guess['evidence'])}"
    act = guess["action"]
    if act == hk.ACTION_WINRM_PORT:
        return STATUS_NEEDS_WINRM_PORT, (
            f"{seen}。{why}——Push Agent 只支援 Linux／Unix，Windows 裝不上。"
            f"請開 WinRM（5985）給收集器，開了重新收集就會走 WinRM 收集"), None
    if act == hk.ACTION_SSH_PORT:
        return STATUS_NEEDS_SSH_PORT, (
            f"{seen}。{why}——Push Agent 安裝腳本在 AIX 上跑不起來。"
            f"請開 SSH（22）給收集器，開了再一鍵納管"), None
    if act == hk.ACTION_NOK:
        # 閘道位址（v1.468.3）：設定檔帶了下一步就講它（「多半是閘道…確認後可標為非納管」）
        nxt = f"{guess['next_step']}；{NOT_ONBOARDABLE_TEXT}" if guess.get("next_step")             else NOT_ONBOARDABLE_SENTENCE
        return STATUS_NOT_ONBOARDABLE, (
            f"{guess['label']}——{nxt}。{why}"), guess.get("nok_kind")
    if act == hk.ACTION_AGENT:
        return STATUS_NEEDS_AGENT, (
            f"{seen}，系統進不去。{why}——請按「取得安裝包」，把 Push Agent 交給該機管理者安裝"), None
    return STATUS_NEEDS_AGENT, (
        f"{seen}。{why}——先確認是什麼機器：Linux／Unix 才裝 Push Agent（取得安裝包）；"
        f"Windows 請開 WinRM（5985）；設備請按「改分類」"), None


def _override_kind(ov: dict | None) -> tuple[str | None, str | None]:
    """人工指定 → host_kind 的 kind（人已經看過這台了，是最強的證據）。"""
    if not ov:
        return None, None
    import pipeline as p
    tab = ov.get("tab")
    kind = {TAB_WINRM: "windows", TAB_SSH: "linux", TAB_STORAGE: "storage",
            TAB_NETWORK: "network", TAB_ESXI: "esxi", TAB_OCP: "ocp",
            TAB_NOK_OTHER: "device"}.get(tab)
    if tab == TAB_MAINFRAME:
        kind = "aix" if ov.get("os_type") == p.OS_AIX else "ibmi"
    why = f"{ov.get('by') or '不詳'} 於 {ov.get('at')}" + (f"：{ov['reason']}" if ov.get("reason") else "")
    return kind, why


def _apply_agent_kind(row: dict, inputs: dict | None, ov: dict | None = None) -> None:
    """就地寫回判斷（kind_guess）與依判斷而定的狀態／訊息。只給 route=agent、活著的列用。"""
    import host_kind as hk

    okind, why = _override_kind(ov)
    ports = [int(p) for p in str(row.get("open_ports") or "").split(",") if p.strip().isdigit()]
    guess = hk.infer_from(inputs, open_ports=ports, ttl=row.get("ttl"),
                          override_kind=okind, override_why=why,
                          ip=row.get("ip"), registered=row.get("registered"))
    status, message, nok = _agent_kind_outcome(ports, guess)
    row["kind_guess"] = guess
    row["status"] = status
    row["message"] = message
    row["not_onboardable_kind"] = nok


#: 平台白名單拒絕訊息裡認得出「ostree 系統（CoreOS／RHCOS）」的字樣（onboard_engine.platform_refusal：
#: os-release 的 PRETTY_NAME，或 appliance_markers 的 coreos 標記名稱）
_OSTREE_RE = re.compile(r"coreos|rhcos|ostree", re.I)


def _credential_outcome(conn, ip: str, message: str) -> tuple[str, str, str | None]:
    """SSH 路通、收集帳號進不去（待佈身分）時，看上次納管結果改講法。回 (status, message, nok_kind)。

    - 上次被 VMware 天條擋（onboard_engine.ESXI_REFUSED_STAGE）→ 另有納管方式（不給重試入口）
    - 上次被平台白名單擋、而且是 RHCOS／ostree → OCP（另有納管方式，不給重試入口）
    - 上次納管失敗 → 納管失敗（2026-09-17 使用者：「待佈身分」會讓人以為還沒動過）
    - 都沒有 → 維持待佈身分
    """
    prev = _last_onboard_failure(conn, ip)
    if prev and prev.get("stage") == "refused":
        return STATUS_NOT_ONBOARDABLE, (
            f"VMware——{NOT_ONBOARDABLE_SENTENCE}。依據：上次納管時系統探測到 "
            f"VMware 並拒絕（{prev['last_tried_at']}）：{prev['message']}"), "esxi"
    # 2026-10-21：連上去才認出是 OCP 節點（os-release 是 RHCOS，或有 ostree 標記）——登記沒寫 OCP、
    # 不登 CIA 的新主機就是靠這一關認出來的。歸 OCP 頁簽、不給重試入口（跟 VMware 同一個做法）
    if prev and prev.get("stage") == "refused_platform" and _OSTREE_RE.search(prev.get("message") or ""):
        return STATUS_NOT_ONBOARDABLE, (
            f"OCP 節點（RHCOS／ostree）——{NOT_ONBOARDABLE_SENTENCE}。依據：上次納管時系統探測到 "
            f"並拒絕（{prev['last_tried_at']}）：{prev['message']}"), "immutable"
    if prev:
        return STATUS_ONBOARD_FAILED, (
            f"納管失敗（{prev['reason']}）：{prev['message']}"
            f"　── {prev['next_step']}"
            f"（第 {prev['fail_count']} 次，最後一次 {prev['last_tried_at']}）"), None
    return STATUS_NEEDS_CREDENTIAL, message, None


# ===== 大型主機：頁簽、擋不擋、走哪條路用同一個判斷（2026-10-19）=====
# 使用者在公司機 v1.468.1 看到：大型主機頁簽裡的 test1T（AIX、開 22、9/22 用 root 納管過）
# 寫「目前不收——認不出是什麼設備」；FUTAS21（AS400，登記 V7R3）也寫「認不出」。
#
# 根因：頁簽看的是 OS 大類（os_class，全站同一份；認得 `oslevel -s` 的 `7200-05-09-2446`、
# 認得 `V7R3`、也採收集到的 OS），但「擋不擋」看的是 onboard_eligibility.not_onboardable
# （只讀作業系統欄、白名單；`7200-05-09-2446`／`V7R3` 都不在名單 → appliance＝認不出的設備），
# 指路看的又是第三條規則（_IBMI_RE，不認 V7R3）。三套對同一台給三個答案。
#
# AIX 納管後，facts 收集把 hardware.os 改寫成 oslevel 輸出（見 collected_os），所以**每一台
# 納管過的 AIX** 都會在下一輪收集被擋成「認不出的設備」——test1T 就是這樣。
#
# 改法：not_onboardable 回 appliance（語意是「作業系統欄認不出」，不是「確定是設備」）而大類
# 判得出是大型主機時，以大類為準：
#   AIX   → 不擋，照 route 走（22 開 → SSH 收集；22 沒開 → 待開 SSH）
#   IBM i → 照擋（不在可納管白名單），但講清楚是 AS400／IBM i、指路 HMC 收集
# ⚠️ 只在**收集分派**這一層。批次納管白名單（batch_onboard_service.classify_targets 讀
# classify_os，只放行 linux）不變；分派試連只是用收集金鑰唯讀登入，不建帳號。


def _mainframe_class(os_cls: str | None, ov: dict | None = None) -> str | None:
    """這台是大型主機的哪一類（AIX／IBM i）；不是回 None。人工指定優先——跟 dispatch_tab 同一個判斷。"""
    cls = ov.get("os_type") if ov else os_cls
    return cls if cls in mainframe_group()["classes"] else None


def _mainframe_block(block, os_cls: str | None, os_text: str | None = None):
    """not_onboardable 的結論，依大類（大型主機頁簽的依據）修正。只動 appliance（認不出）那一桶；
    ESXi／OpenShift／儲存／網路這些正面認出來的結論一律不動。"""
    if not block or block[0] != "appliance":
        return block
    mf = mainframe_group()
    if os_cls in mf["onboardable_classes"]:
        return None
    if os_cls in mf["classes"]:
        label = _MAINFRAME_CHOICE_LABEL.get(os_cls, os_cls)
        return ("appliance", label,
                f"依據：作業系統大類為 {os_cls}（資產登記「{os_text or '—'}」）——"
                f"不在可納管白名單（不 SSH 建本機帳號），走 HMC 收集")
    return block


#: 要寫廠牌的「另有納管方式」類別（2026-10-19 使用者：「網路設備要寫廠牌」）
VENDOR_KINDS = ("network", "storage", "appliance", "banner")


def _attach_vendor(conn, results: list[dict], ov_map: dict | None = None) -> None:
    """另有納管方式裡的網路設備／儲存設備／設備列補上廠牌（`vendor`，只在讀取／回傳時算，不存 DB）。

    判斷在 host_kind.vendor_judge（規則在 host_kind_rules.json），這裡只負責挑列、餵證據。
    ⚠️ 只對**已經是另有納管方式**的列加註——廠牌（尤其 MAC 廠商）不會讓任何一台被擋或被放行，
    納管白名單完全不看這裡。唯一會動的是頁簽：認不出的「設備」若廠牌是網通廠牌，改放
    「網路設備」頁簽（推測的照樣標推測，畫面分得出來；錯了按「改分類」）。人工指定過的不動。
    """
    import host_kind
    import onboard_eligibility as oe

    rows = [r for r in results if r.get("status") == STATUS_NOT_ONBOARDABLE
            and r.get("not_onboardable_kind") in VENDOR_KINDS
            and not _mainframe_class(r.get("os_class"), override_of(ov_map, r))]
    if not rows:
        return
    try:
        inputs = host_kind.vendor_inputs(conn, [r["ip"] for r in rows])
    except Exception:  # noqa: BLE001 - 證據撈不到就當沒有線索（顯示「廠牌不明」），不擋整張表
        inputs = {}
    cfg = host_kind.load_rules()
    cat_label = cfg.get("vendor_category_label") or {}
    for r in rows:
        d = inputs.get(r["ip"]) or {}
        msg = r.get("message") or ""
        product = (oe.product_from_banner(msg)
                   if r.get("not_onboardable_kind") == oe.KIND_BANNER or "SSH banner" in msg else None)
        v = host_kind.vendor_judge(banner_product=product, registry=d.get("registry"),
                                   mac_vendor=d.get("mac_vendor"))
        if (v["category"] == "network" and r["not_onboardable_kind"] in ("appliance", "banner")
                and not override_of(ov_map, r)):
            v["moved_from"] = r["not_onboardable_kind"]
            r["not_onboardable_kind"] = "network"
        v["text"] = (f"{cat_label.get(r['not_onboardable_kind'], '設備')}・"
                     f"{v['vendor'] or cfg.get('vendor_unknown_text') or '廠牌不明'}")
        r["vendor"] = v


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


def start_background(db_path, targets_raw: str, *, triggered_by: str = "(未知)",
                     full: bool = True, batch_size: int | None = None,
                     targets_label: str | None = None, trigger: str = "manual") -> dict:
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
            run_dispatch(conn, targets_raw, triggered_by=triggered_by, full=full,
                         targets_label=targets_label, trigger=trigger)
        except Exception:  # noqa: BLE001 - run_dispatch 自己會把 run 標成 failed 並留錯誤
            pass
        finally:
            conn.close()
            _bg_running = False

    threading.Thread(target=worker, daemon=True).start()
    return {"started": True, "total": len(ips),
            "batch_total": len(plan_batches(ips, batch_size or BATCH_SIZE))}


def _sleep(seconds: int) -> None:
    """抽出來讓測試不用真的等兩分鐘。等待中按「停止收集」會馬上醒來（Event.wait）。"""
    ev = getattr(_tl, "stop", None)
    if ev is not None:
        ev.wait(seconds)
    else:
        import time

        time.sleep(seconds)


def _db_path_of(conn):
    """這條連線開的是哪個庫檔（背景隊伍要自己開連線）。記憶體庫／查不到 → None＝預設庫。"""
    try:
        for row in conn.execute("PRAGMA database_list"):
            if row[1] == "main":
                return row[2] or None
    except Exception:  # noqa: BLE001 - 查不到就用預設庫（跟 API 同一個）
        return None
    return None


def _queue_first_collects(conn, batch_rows: list[dict], by: str | None, enqueue=None) -> list[str]:
    """2-1「開始收集」第一次把一台收成功 → 排進背景隊伍收一輪六樣（2026-10-20）。

    為什麼：其他納管入口（單台、批次、排程自動納管、貼指令）納管後都會自動收六樣
    （使用者規則 2026-09-16／10-18：納管後一律自動收一輪、沒有開關），唯獨「開始收集」
    只把台標成已納管、六樣一樣都沒收——公司機一堆台卡在「有主機事實，還沒收服務」。

    規則（**只有第一次**）：這次收成功（collected）、有資產序號、而且這台從來沒有
    post_onboard 紀錄的才排。重跑收集不會每次重收六樣；要重收用「收集全部」。
    走背景隊伍（post_onboard.enqueue）：一條執行緒逐台、跟單台納管共用同時上限，
    不擋這次收集的回應；任何一步出錯只記紀錄，**不會**讓這次收集失敗。
    """
    import post_onboard

    serials = []
    try:
        for r in batch_rows:
            sn = r.get("asset_serial")
            if r.get("status") != STATUS_COLLECTED or not sn or sn in serials:
                continue
            if post_onboard.never_ran(conn, sn, r.get("ip")):
                serials.append(sn)
        if serials:
            (enqueue or post_onboard.enqueue)(_db_path_of(conn), serials, by=by, source="onboard")
    except Exception as exc:  # noqa: BLE001 - 排不進隊伍不影響收集本身，但不可靜默
        try:
            import collect_log

            collect_log.record(conn, "post_onboard", None, False,
                               f"開始收集後自動收六樣沒有排進去：{type(exc).__name__}: {exc}",
                               actor=by)
        except Exception:  # noqa: BLE001 - 連紀錄都寫不進去就放棄，收集本身已經存好
            pass
    return serials


def run_dispatch(conn, targets_raw: str, *, triggered_by: str = "(未知)",
                 prober=None, ssh_prober=None, winrm_runner=None, ttl_prober=None,
                 key_path: str | None = None, cred_key_path: str | None = None,
                 workers: int | None = None, batch_size: int | None = None,
                 gap_seconds: int | None = None, full: bool = True,
                 skip_hours: int | None = None, targets_label: str | None = None,
                 retry_prober=None, trigger: str = "manual",
                 post_onboard_enqueue=None) -> dict:
    """一個入口跑完整流程：解析目標 → 探測 → 選路 → 各路各自執行 → 一張結果表。

    每一台的結果都寫進 collect_dispatch_result（run 存 collect_dispatch_run），
    讓使用者重新整理頁面還看得到上一次的結果——這種要等數十秒的動作，
    做完卻只活在瀏覽器記憶體裡，等於做白工。

    prober／ssh_prober／winrm_runner／ttl_prober 全部可注入，測試不碰真網路、不碰真憑證。

    `full=False`＝「只收新的」（v1.468.4）：上次結果在 skip_hours 小時內、而且是定案狀態
    （已納管／空位址）的位址不重探；它們在合併檢視裡照樣看得到（顯示的是上次的結果與時間）。
    只輸入一個位址時一律重探——那是人指名要收這台。
    各階段花的秒數寫進 run.timing（JSON），量得出時間花在哪。
    """
    import time as _time
    import host_kind
    import manage_state
    import onboard_eligibility
    import os_override

    ips = parse_targets(targets_raw)
    if not ips:
        raise ValueError("沒有可處理的目標——請輸入網段（10.99.1.0/24）或 IP 清單")
    t_start = _time.monotonic()
    timing = {"probe": 0.0, "ttl": 0.0, "ssh": 0.0, "winrm": 0.0, "gap": 0.0, "other": 0.0}
    skipped: set[str] = set()
    if not full and len(ips) > 1:
        skipped = fresh_ips(conn, ips, skip_fresh_hours(conn) if skip_hours is None else skip_hours,
                            empty_hours=skip_empty_hours(conn))
        ips = [ip for ip in ips if ip not in skipped]
    key_path = key_path or manage_state.COLLECTOR_KEY_DEFAULT
    # 探測並行數／逾時（v1.468.5，6 系統設定）。預設探測器才套逾時與「曾活過再試一次」；
    # 注入的探測器（測試）不重試，除非也注入 retry_prober。
    _cfg_workers, _cfg_timeout = probe_settings(conn)
    workers = workers or _cfg_workers
    # 開關整輪只讀一次；關著時探的埠、判定都跟以前一模一樣
    _ocp_probe = ocp_port_probe_on(conn)
    _ports = DISPATCH_PORTS + ((OCP_PROBE_PORT,) if _ocp_probe else ())
    _default_ttl = prober is None
    if prober is None:
        prober = lambda _ip: _default_prober(_ip, timeout=_cfg_timeout, ports=_ports)  # noqa: E731
        if retry_prober is None:
            retry_prober = lambda _ip: _default_prober(_ip, timeout=RETRY_TIMEOUT_S, ports=_ports)  # noqa: E731
    # TTL 只對「活著但進不去」的那幾台取（判斷是什麼機器的線索之一，ping 一次）。
    # 探測器被注入（測試）時預設不 ping——測試不碰真網路。
    if ttl_prober is None:
        if _default_ttl:
            import fingerprint
            ttl_prober = fingerprint.probe_ttl
        else:
            ttl_prober = lambda _ip: None  # noqa: E731

    # 自動分批（2026-09-16）：以前超過 1024 台直接拒絕、叫使用者自己切；
    # 現在系統自己切，並把「第幾批、做完幾台」寫進 run，畫面才看得出進度。
    batches = plan_batches(ips, batch_size or batch_size_setting(conn))
    gap = batch_gap_seconds(conn) if gap_seconds is None else max(0, int(gap_seconds))

    cur = conn.execute(
        "INSERT INTO collect_dispatch_run (trigger, triggered_by, targets_raw, "
        "target_count, status, started_at, batch_total, batch_done, done_count) "
        "VALUES (?,?,?,?,'running', datetime('now','localtime'), ?, 0, 0)",
        (trigger, triggered_by, (targets_label or targets_raw or "")[:2000], len(ips),
         len(batches) if ips else 0),
    )
    run_id = cur.lastrowid
    # 新欄（skipped_count／timing）在 db.init_db 補；舊庫（還沒重啟補欄）就不記，不擋收集
    _run_cols = _run_cols_of(conn)
    _stop = __import__("threading").Event()
    _stop_events[run_id] = _stop
    _tl.stop = _stop
    _heartbeat(conn, run_id, cols=_run_cols)
    _hb_last = [_time.monotonic()]

    def _beat(next_at=None, force: bool = False) -> None:
        # 一批裡逐台 SSH／WinRM 可能跑很久：至少每 30 秒記一次，不然會被誤判成中斷
        if force or _time.monotonic() - _hb_last[0] >= 30:
            _heartbeat(conn, run_id, next_at, cols=_run_cols)
            conn.commit()
            _hb_last[0] = _time.monotonic()

    stopped_after = None      # 手動停止：做完第幾批停的
    stopped_partial = None    # 手動停止發生在批次中間：(第幾批, 收了幾台, 這批幾台)
    if "skipped_count" in _run_cols:
        conn.execute("UPDATE collect_dispatch_run SET skipped_count=? WHERE id=?",
                     (len(skipped), run_id))
    conn.commit()

    def _save_timing(final: bool = False) -> None:
        if "timing" not in _run_cols:
            return
        tot = _time.monotonic() - t_start
        timing["other"] = max(0.0, tot - sum(v for k, v in timing.items() if k != "other"))
        conn.execute("UPDATE collect_dispatch_run SET timing=? WHERE id=?",
                     (json.dumps({**{k: round(v, 1) for k, v in timing.items()},
                                  "total": round(tot, 1), "final": final}), run_id))

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
          if not batch:
              continue
          # 「停止收集」：在批與批之間看旗標——正在跑的那一批做完才停，已收的結果都留著
          if bi and _stop.is_set():
              stopped_after = bi
              break
          if bi and gap:
              _t = _time.monotonic()
              # 間隔中沒有進度：先記「下一批預計幾點開始」，判斷活著與否以它為準（畫面也顯示它）
              from datetime import datetime as _dt, timedelta as _td
              _beat((_dt.now() + _td(seconds=gap)).strftime("%Y-%m-%d %H:%M:%S"), force=True)
              _sleep(gap)
              if _stop.is_set():
                  timing["gap"] += _time.monotonic() - _t
                  stopped_after = bi
                  break
              _beat(force=True)
              timing["gap"] += _time.monotonic() - _t
          _t = _time.monotonic()
          probed = _probe_all(batch, prober, workers=workers)
          # 沒回應、但以前活過的：用較長逾時再試一次（慢但活著的不要被判成空位址）
          if retry_prober is not None:
              _dead = [ip for ip in batch if probed.get(ip) is None]
              _seen = _ever_alive(conn, _dead) if _dead else set()
              _again = [ip for ip in _dead if ip in _seen]
              if _again:
                  probed.update({ip: p for ip, p in
                                 _probe_all(_again, retry_prober, workers=workers).items()
                                 if p is not None})
          timing["probe"] += _time.monotonic() - _t
          # 「活著但進不去」的那幾台：一次撈齊判斷用的證據（登記／收集／上次掃描）＋ TTL
          agent_ips = [ip for ip in batch if probed.get(ip) is not None
                       and choose_route(True, probed.get(ip)) == ROUTE_AGENT]
          kind_inputs = host_kind.evidence_inputs(conn, agent_ips) if agent_ips else {}
          # OS 大類（大型主機頁簽就是看它）：擋不擋、走哪條路也要看**同一個**判斷，
          # 不然頁簽說「大型主機」、下一步卻說「認不出是什麼設備」（2026-10-19 公司機 v1.468.1）
          cls_map = os_class_by_ip(conn, batch)
          _t = _time.monotonic()
          ttls = _probe_all(agent_ips, ttl_prober, workers=workers) if agent_ips else {}
          timing["ttl"] += _time.monotonic() - _t
          _beat(force=True)
          _batch_start = len(results)
          _cut = False
          for ip in batch:
            _beat()
            # 「停止收集」也在逐台之間看（v1.471.1）：一批 1024 台逐台收可能很久，不能等整批做完。
            # 每台最多卡多久有上限（SSH 探測整支 subprocess 有逾時、WinRM 腳本 SCRIPT_DEADLINE_S＝180 秒，
            # 加上收尾的一兩次 HTTP 來回），
            # 所以按下停止，最多等目前那一台收完。
            if _stop.is_set():
                _cut = True
                break
            ports = probed.get(ip)
            alive = ports is not None
            ports = ports or []
            route = choose_route(alive, ports)
            asset = _known_asset(conn, ip)
            # 22／445／5985 都開的 Windows（裝了 OpenSSH）路由判 SSH；人在 2-1 對它做過 Windows 納管
            # （WinRM 登入成功過、現在仍納管中）就照它被納管的那條路收——不然每一輪都拿 SSH 去試、
            # 永遠是「待佈身分」。只看 5985（WinRM 本尊），445 不算（Samba 也開 445）。
            if route == ROUTE_SSH and 5985 in ports and win_onboard.is_opted_in(conn, ip):
                route = ROUTE_WINRM

            # 有開 22 不代表可以納管：ESXi／OpenShift 節點／設備**先擋、連試連都不做**，
            # 並寫明依據（2026-09-14 使用者：有 SSH 但要標出來，我才不會去納管它）
            block = None
            kind_guess = None
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
            # 人工指定成 IBM i（落在大型主機頁簽）：IBM i 不在可納管白名單，照樣擋、不試連
            if _ov and _ov.get("os_type") == _ibmi_os_type():
                _ov_nok_kind = "appliance"
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
                block = _mainframe_block(block, cls_map.get(ip), (asset or {}).get("os"))
                # 10250 有回應 → 疑似 OCP。放在上面那些判定**之後**、只在它們沒結論時才用：
                # VMware（登記／vCenter 名單）等正面認出來的類別不可以被蓋掉（天條順序）
                if not block and _ocp_probe and OCP_PROBE_PORT in ports:
                    block = ("immutable", OCP_PROBE_LABEL, OCP_PROBE_WHY)
            # 大型主機頁簽靠的大類（人工指定優先）——下面「走哪條路」也看同一個
            _mf_cls = _mainframe_class(cls_map.get(ip), _ov)
            if block:
                nok_kind = block[0]
                status = STATUS_NOT_ONBOARDABLE
                message = f"{block[1]}——{NOT_ONBOARDABLE_SENTENCE}。{block[2]}"
            elif route == ROUTE_SSH:
                _t = _time.monotonic()
                status, message = _do_ssh(conn, ip, asset, ssh_prober, key_path)
                timing["ssh"] += _time.monotonic() - _t
                # 收集帳號進不去時，連線訊息裡常帶著 SSH banner——產品自報身分就採信它。
                # 資產庫沒登記／os 欄空白的機器，光看 os 判不出來，但 banner 已經講了
                # （2026-09-15：10.99.198.21 自報 VMware Avi Load Balancer，被列在待佈身分）。
                # 已經試過納管而且失敗的，講「納管失敗」不要講「待佈身分」
                # （2026-09-17 使用者）——後者會讓人以為還沒動過。
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
                # 「納管失敗」放在 banner 判定**之後**（2026-10-18）：上次失敗的機器現在可以從
                # 這一列直接重試（一鍵納管／勾選），所以 banner 自報是 VMware／儲存的必須先被擋成
                # 「另有納管方式」，不可以因為「上次失敗過」就跳過這道判定而變成可重試（fail-closed）
                if status == STATUS_NEEDS_CREDENTIAL:
                    status, message, nok_kind = _credential_outcome(conn, ip, message)
            elif route == ROUTE_WINRM and _mf_cls in mainframe_group()["onboardable_classes"]:
                # 大類是 AIX 卻只開 445／5985（沒開 22）：AIX 沒有 WinRM，去試 WinRM 只會失敗。
                # 跟 route=agent 的 AIX 同一個結論——請開 SSH（22）。
                status = STATUS_NEEDS_SSH_PORT
                message = (f"活著（開放埠 {','.join(map(str, ports))}）但 22 沒開。判斷：AIX"
                           f"（{'人工指定' if _ov else '作業系統大類'}）——AIX 走 SSH 收集，"
                           f"請開 SSH（22）給收集器，開了再一鍵納管")
            elif route == ROUTE_WINRM and not win_onboard.is_opted_in(conn, ip):
                # Windows 勾選才收（2026-10-22）：還沒納管的**不登入**——連憑證都不取。
                # 探測結果照記（走哪條路＝WinRM），畫面列成「待納管」，人勾選按「納管」才登入。
                status, message = STATUS_WIN_PENDING, win_onboard.pending_reason(conn, ip)
            elif route == ROUTE_WINRM:
                _t = _time.monotonic()
                status, message = _do_winrm(conn, ip, asset, winrm_runner, cred_key_path)
                timing["winrm"] += _time.monotonic() - _t
            elif route == ROUTE_AGENT:
                _r = {"open_ports": ",".join(map(str, ports)), "ttl": ttls.get(ip),
                      "ip": ip, "registered": 1 if asset else 0}
                _apply_agent_kind(_r, kind_inputs.get(ip), _ov)
                status, message, nok_kind = _r["status"], _r["message"], _r["not_onboardable_kind"]
                kind_guess = _r["kind_guess"]
            else:
                status, message = _do_import(conn, ip, asset)
            # 進不去、又已被資產登記／人工指定擋成另有納管方式的：判斷照樣給（畫面「判斷」欄），不改狀態
            if kind_guess is None and route == ROUTE_AGENT and status == STATUS_NOT_ONBOARDABLE:
                _ok, _why = _override_kind(_ov)
                kind_guess = host_kind.infer_from(kind_inputs.get(ip), open_ports=ports,
                                                  ttl=ttls.get(ip), override_kind=_ok,
                                                  override_why=_why, ip=ip,
                                                  registered=1 if asset else 0)

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
                # 判斷是什麼機器（只有 route=agent 的列有；畫面「判斷」欄）
                "kind_guess": kind_guess,
                "ttl": ttls.get(ip),
                # 只給 summarize 指路用（appliance 桶裡再認 IBM i／Power）。
                # 不進 collect_dispatch_result——那張表存的是「這次探測的事實」，
                # 資產登記會變，存一份快照只會跟資產庫漂走。latest_run 讀時現查。
                "asset_os": (asset or {}).get("os"),
            }
            results.append(row)
            conn.execute(
                "INSERT INTO collect_dispatch_result (run_id, ip, alive, open_ports, route, "
                "status, asset_serial, hostname, registered, message, not_onboardable_kind, ttl) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, ip, row["alive"], row["open_ports"], route, status,
                 row["asset_serial"], row["hostname"], row["registered"], message, nok_kind,
                 row["ttl"]),
            )
            # 逐台更新「做完幾台」（v1.473.2，2026-10-20 使用者：「應該顯示『收集中：幾台』」）：
            # 單批的小收集以前要整批做完才看得到數字，畫面只能顯示秒數
            conn.execute("UPDATE collect_dispatch_run SET done_count=? WHERE id=?",
                         (len(results), run_id))
            conn.commit()
          # 每批做完就存檔並更新進度：跑到第 7 批才壞掉時，前 6 批的結果不可以跟著不見
          conn.execute(
              "UPDATE collect_dispatch_run SET batch_done=?, done_count=? WHERE id=?",
              (bi if _cut else bi + 1, len(results), run_id))
          _heartbeat(conn, run_id, cols=_run_cols)
          _save_timing()
          conn.commit()
          # 這一批第一次收成功的台 → 排進「納管後自動收集」（六樣）。見 _queue_first_collects
          # 探測器／收集器被注入（測試的假網路）時預設不排：後面那六樣會是真連線，
          # 測試要驗這段就明確注入 post_onboard_enqueue。正式呼叫端（start_background、
          # /api/collect/dispatch）從不注入。_default_ttl＝呼叫端沒給 prober。
          if post_onboard_enqueue is not None or (
                  _default_ttl and ssh_prober is None and winrm_runner is None):
              _queue_first_collects(conn, results[_batch_start:], triggered_by,
                                    post_onboard_enqueue)
          if _cut:
              stopped_after = bi
              stopped_partial = (bi + 1, len(results) - _batch_start, len(batch))
              break
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - 失敗也要把 run 收尾，不要留一筆永遠 running
        _stop_events.pop(run_id, None)
        _tl.stop = None
        conn.execute(
            "UPDATE collect_dispatch_run SET status='failed', error=?, "
            "finished_at=datetime('now','localtime') WHERE id=?", (str(exc)[:300], run_id))
        conn.commit()
        raise

    _stop_events.pop(run_id, None)
    _tl.stop = None
    if stopped_after is not None:
        conn.execute(
            "UPDATE collect_dispatch_run SET status=?, error=?, finished_at=datetime('now','localtime'), "
            "next_batch_at=NULL WHERE id=?",
            (STATUS_STOPPED, f"已手動停止（完成 {stopped_after}/{len(batches)} 批"
             + (f"；第 {stopped_partial[0]} 批收了 {stopped_partial[1]}/{stopped_partial[2]} 台"
                if stopped_partial else "") + "）", run_id))
    else:
        conn.execute(
            "UPDATE collect_dispatch_run SET status='ok', finished_at=datetime('now','localtime') "
            "WHERE id=?", (run_id,))
    _save_timing(final=True)
    conn.commit()
    _attach_os_class(conn, results)
    _attach_vendor(conn, results, ov_map)
    _attach_win(conn, results)
    return {"run_id": run_id, **summarize(results, ov_map), "results": results,
            "skipped": len(skipped), "stopped_after": stopped_after}


def _ibmi_os_type() -> str:
    import pipeline
    return pipeline.OS_IBMI


def _attach_os_class(conn, results: list[dict]) -> None:
    """每列補上 OS 大類（大型主機頁簽靠它）。只在讀取／回傳時算，不存進 collect_dispatch_result
    ——資產登記與收集結果會變，存快照只會漂走（同 asset_name 的理由）。"""
    try:
        cls = os_class_by_ip(conn, [r["ip"] for r in results if r.get("registered")])
    except Exception:  # noqa: BLE001 - 大類算不出來就不分大型主機（退回舊頁簽），不擋整張表
        cls = {}
    for r in results:
        r["os_class"] = cls.get(r["ip"])


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
#: OCP（OpenShift 節點）（2026-10-21 使用者：「這裡怎麼沒有 OCP 分類」）：以前併在「其他」。
#: 另有納管方式（帳號由 OCP 平台管、本機帳號會在節點重佈時消失），自成一類才看得出有幾台、
#: 也才能把登記只寫 RHEL 的 OCP 節點用「搬移分類」標回來（寫 os_override＝OpenShift 節點）。
TAB_OCP = "ocp"
#: 其他不納管：認不出廠牌的設備（iDRAC、SecureKi…）、banner 自報但
#: 認不出歸屬的、以及舊 run 沒留類別而且現在也重判不出來的。
TAB_NOK_OTHER = "nok_other"
#: 大型主機（AIX／AS400）（2026-10-18 使用者）：歸屬看全站同一份 OS 大類（os_class），
#: 哪些大類算、哪些可佈帳號放 os_class_groups.json 的 adopt_groups.mainframe，不寫死。
#: AIX 照舊走 SSH（ksh 腳本、8 字元帳號、root 金鑰邏輯都不變），只是換一個頁簽放；
#: IBM i／AS400 照舊不在可納管白名單（status 仍是另有納管方式，指路 HMC）。
TAB_MAINFRAME = "mainframe"
#: 空位址（2026-10-18 使用者：「8954+9652 這兩筆對我來說沒意義」）：沒回應**而且**資產庫
#: 沒登記＝那個位址上沒有機器。自成一個頁簽只為了「各頁簽加總＝探測位址數」對得起來；
#: 畫面上**不列**這個頁簽、也不把它算進「全部」（全部＝真的有機器的列）。
TAB_EMPTY = "empty"

#: 顯示順序固定（不跟台數跳動）。可以動的三條在前，大型主機，不納管三類＋其他，只能匯入，空位址最後。
TAB_ORDER = (TAB_SSH, TAB_WINRM, TAB_AGENT, TAB_MAINFRAME, TAB_STORAGE, TAB_NETWORK, TAB_ESXI,
             TAB_OCP, TAB_NOK_OTHER, TAB_IMPORT, TAB_EMPTY)
#: 「動得了的」＝真的有下一步的頁簽（不納管的、只能匯入的都沒有）。大型主機頁簽裡
#: 只有可佈帳號的那幾類（AIX）算動得了——逐列看 `mainframe_onboardable`。
ACTIONABLE_TABS = (TAB_SSH, TAB_WINRM, TAB_AGENT)
TAB_LABEL = {
    TAB_SSH: "SSH 收集", TAB_WINRM: "WinRM 收集", TAB_AGENT: "Push Agent",
    TAB_MAINFRAME: "大型主機（AIX／AS400）",
    TAB_STORAGE: "儲存設備", TAB_NETWORK: "網路設備", TAB_ESXI: "ESXi", TAB_OCP: "OCP",
    TAB_NOK_OTHER: "其他（另有方式）", TAB_IMPORT: "只能匯入", TAB_EMPTY: "空位址",
}


def mainframe_group() -> dict:
    """os_class_groups.json 的 adopt_groups.mainframe（大類清單＋可佈帳號的大類）。"""
    import os_class
    g = (os_class.load_rules().get("adopt_groups") or {}).get("mainframe") or {}
    return {"label": g.get("label") or TAB_LABEL[TAB_MAINFRAME],
            "classes": tuple(g.get("classes") or ()),
            "onboardable_classes": tuple(g.get("onboardable_classes") or ())}


def os_class_by_ip(conn, ips) -> dict[str, str]:
    """這批 IP 的 OS 大類（全站同一支 os_class ＋ 已納管主機以收集到的 OS 為準）。

    同一個 IP 有多筆登記（DYN- 掃描殼＋CIA 正本最常見）時，取第一個**講得出類別**的
    （不是「其他／未知」），順序照 manage_state.serial_for_ip 的挑法（現役、已納管、正式登記優先）。
    沒登記的 IP 不在回傳裡。
    """
    import collected_os
    import manage_state
    import os_class

    uniq = [ip for ip in dict.fromkeys(ips) if ip]
    if not uniq:
        return {}
    collected = collected_os.platform_map(conn)
    by_ip: dict[str, list] = {}
    for i in range(0, len(uniq), 400):
        chunk = uniq[i:i + 400]
        qs = ",".join("?" * len(chunk))
        for r in conn.execute(
                "SELECT ip, asset_serial, os, device_model, group_name, asset_purpose, "
                "asset_status, collect_ok, rowid AS rid "
                f"FROM hardware WHERE ip IN ({qs})", chunk):
            by_ip.setdefault(r["ip"], []).append(r)
    out: dict[str, str] = {}
    for ip, rows in by_ip.items():
        def rank(r):
            retired = (r["asset_status"] or "").strip() in manage_state.RETIRED_STATUS
            return (1 if retired else 0, 0 if (r["collect_ok"] or 0) == 1 else 1,
                    1 if str(r["asset_serial"]).startswith("DYN-") else 0, r["rid"])
        # 退役的舊登記（IP 回收前那台）不拿來講現在這台是什麼——除非整個 IP 只剩退役的
        live = [r for r in rows
                if (r["asset_status"] or "").strip() not in manage_state.RETIRED_STATUS] or rows
        classes = [collected_os.apply_os_class(os_class.classify_row(r),
                                               collected.get(r["asset_serial"]))
                   for r in sorted(live, key=rank)]
        out[ip] = next((c for c in classes if c not in ("其他", "未知")), classes[0])
    return out
_NOK_KIND_TAB = {"storage": TAB_STORAGE, "network": TAB_NETWORK, "esxi": TAB_ESXI,
                 "immutable": TAB_OCP}
#: 反向：頁簽 → 不納管類別（人工指定搬到不納管頁簽時要寫回這一欄）
_TAB_NOK_KIND = {TAB_STORAGE: "storage", TAB_NETWORK: "network", TAB_ESXI: "esxi",
                 TAB_OCP: "immutable", TAB_NOK_OTHER: "appliance"}

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
        TAB_OCP: p.OS_OPENSHIFT,
        TAB_NOK_OTHER: p.OS_APPLIANCE,
        # 搬到大型主機＝指定成 AIX（可佈帳號的那一類）。AS400／IBM i 是登記上就認得出來的，
        # 要人工指定成 IBM i 走資產明細頁的「OS 類型」（同一張 os_type_override 表）。
        TAB_MAINFRAME: p.OS_AIX,
    })
    return _TAB_OS_CACHE


def _os_tab() -> dict[str, str]:
    """OS 大類 → 頁簽。比 _tab_os() 多的那幾類（AIX／IBM i／BMC）
    是漏斗有、頁簽沒有的，各自歸到意思最近的頁簽；它們不是搬移的目的地，
    只有在資產明細頁搬過、再回到 /adopt 看時才會走到這裡。"""
    if _OS_TAB_CACHE:
        return _OS_TAB_CACHE
    import pipeline as p
    _OS_TAB_CACHE.update({v: k for k, v in _tab_os().items()})
    # 2026-10-18：AIX／IBM i 都歸「大型主機」頁簽（AIX 仍走 SSH 收集、IBM i 仍不納管）
    _OS_TAB_CACHE[p.OS_AIX] = TAB_MAINFRAME
    _OS_TAB_CACHE[p.OS_IBMI] = TAB_MAINFRAME
    _OS_TAB_CACHE[p.OS_BMC] = TAB_NOK_OTHER
    return _OS_TAB_CACHE


#: 可以當搬移目的地的頁簽（順序＝畫面上選單的順序）
MOVABLE_TABS = (TAB_SSH, TAB_WINRM, TAB_MAINFRAME, TAB_STORAGE, TAB_NETWORK, TAB_ESXI,
                TAB_OCP, TAB_NOK_OTHER)


def os_type_for_tab(tab: str) -> str | None:
    """搬到這個頁簽要寫進 os_override 的類別；不能搬的頁簽回 None。"""
    return _tab_os().get(tab)


def tab_for_os_type(os_type: str | None) -> str | None:
    return _os_tab().get(os_type or "")


#: 2-1 待納管的**分類頁簽**——全站唯一一份定義（順序＝畫面順序）。
#: 頁簽列（chips）與「改分類／搬移分類」選單（待納管列、批次、已納管列、資產明細頁）全部讀這一份
#: （GET /api/adopt/move-choices 的 `tabs`）。2026-10-18 使用者：「這裡有幾個選項，那裡分類就應該有
#: 幾個選項」——以前頁簽寫在前端、選單另寫一份、資產頁又讀漏斗大類，三份各長各的。
#: 「動得了的」「全部」是彙總頁簽，不是類別，不在這裡。
CATEGORY_TABS = (TAB_SSH, TAB_WINRM, TAB_AGENT, TAB_MAINFRAME, TAB_STORAGE, TAB_NETWORK, TAB_ESXI,
                 TAB_OCP, TAB_NOK_OTHER, TAB_IMPORT)

#: 列在選單裡但**不能選**的頁簽與原因（不給選，但要看得到、講得出為什麼——不靜默少一個）
_NOT_MOVABLE_WHY = {
    TAB_AGENT: "這是連線狀態（活著但 22／445 都進不去），由每次收集的探測決定，不是機器的類別",
    TAB_IMPORT: "這是連線狀態（完全沒回應），由每次收集的探測決定，不是機器的類別",
}

#: 大型主機頁簽底下的子選項標籤（大類名 → 畫面字）
_MAINFRAME_CHOICE_LABEL = {"AIX": "AIX", "IBM i": "AS400／IBM i"}


def tab_label(tab: str) -> str:
    if tab == TAB_MAINFRAME:
        return mainframe_group()["label"]
    return TAB_LABEL.get(tab, tab)


def tab_os_choices(tab: str) -> list[dict]:
    """搬到這個頁簽可以寫進 os_override 的類別（一般一個；大型主機分 AIX／AS400 兩個）。

    大型主機的子選項讀 os_class_groups.json 的 adopt_groups.mainframe（classes／onboardable_classes），
    跟頁簽歸屬同一份設定。`onboardable`＝搬過去會不會去試連（AIX 會；AS400／IBM i 不在可納管
    白名單，不會）。⚠️ 這只影響顯示與試連，**不改納管白名單**（test_adopt_move.py 釘住）。
    """
    if tab == TAB_MAINFRAME:
        mf = mainframe_group()
        return [{"os_type": c, "label": _MAINFRAME_CHOICE_LABEL.get(c, c),
                 "onboardable": c in mf["onboardable_classes"]} for c in mf["classes"]]
    os_type = _tab_os().get(tab)
    if not os_type:
        return []
    return [{"os_type": os_type, "label": tab_label(tab), "onboardable": tab in ACTIONABLE_TABS}]


def category_tabs() -> list[dict]:
    """分類頁簽（含不能搬的那兩個），給頁簽列與改分類選單共用。"""
    out = []
    for t in CATEGORY_TABS:
        ch = tab_os_choices(t)
        out.append({"tab": t, "label": tab_label(t), "movable": bool(ch),
                    "why_not": _NOT_MOVABLE_WHY.get(t), "choices": ch,
                    # 舊欄位（相容）：第一個子選項
                    "os_type": ch[0]["os_type"] if ch else None,
                    "onboardable": any(c["onboardable"] for c in ch)})
    return out


def os_type_for_tab_choice(tab: str, os_type: str | None) -> str | None:
    """搬到 tab，指定子類別 os_type（大型主機要分 AIX／AS400）。os_type 不在這個頁簽的選項裡回 None。"""
    ch = tab_os_choices(tab)
    if not ch:
        return None
    if not os_type:
        return ch[0]["os_type"]
    return os_type if any(c["os_type"] == os_type for c in ch) else None


def move_choices() -> list[dict]:
    """給搬移選單：只回能搬的頁簽（舊介面；新畫面讀 category_tabs()）。"""
    return [c for c in category_tabs() if c["movable"]]


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


def nok_guidance(kind: str | None, os_text: str | None = None,
                 os_class: str | None = None) -> dict | None:
    """這一類不納管的機器**該走哪條收集路徑**。回 None ＝這一列不需要指路。

    `os_text`／`os_class` 只用在 appliance 這一桶的再細分（IBM i／Power → HMC 收集）。
    `os_class`＝大型主機頁簽用的同一個大類（2026-10-19：`V7R3` 這種寫法 _IBMI_RE 不認、
    大類認得，於是 AS400 在大型主機頁簽卻被指成「認不出是什麼設備」）。
    """
    if not kind:
        return None
    if kind == "appliance" and (
            (os_text and _IBMI_RE.search(str(os_text)))
            or (os_class in mainframe_group()["classes"]
                and os_class not in mainframe_group()["onboardable_classes"])):
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
    # 空位址：沒回應又沒登記＝那裡沒有機器（跟 summarize 的 empty_addresses 同一個條件）
    if row.get("status") == STATUS_IMPORT_ONLY and not row.get("registered"):
        return TAB_EMPTY
    # 大型主機：照 OS 大類（呼叫端用 os_class_by_ip 帶進 row["os_class"]）。
    # VMware／儲存／網路的不納管判定優先——那是天條與實際類別，不讓大類蓋過去。
    if (row.get("os_class") in mainframe_group()["classes"]
            and row.get("not_onboardable_kind") not in ("esxi", "storage", "network")):
        return TAB_MAINFRAME
    if row.get("status") == STATUS_NOT_ONBOARDABLE:
        return _NOK_KIND_TAB.get(row.get("not_onboardable_kind") or "", TAB_NOK_OTHER)
    # 2026-10-19：判斷是 Windows 的進不去的機器，下一步是開 WinRM——放 WinRM 收集頁簽，
    # 不留在 Push Agent（Push Agent 只支援 Linux／Unix）。AIX 一律在大型主機頁簽。
    if row.get("status") == STATUS_NEEDS_WINRM_PORT:
        return TAB_WINRM
    if row.get("status") == STATUS_NEEDS_SSH_PORT:
        return TAB_MAINFRAME
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


def _reopen_aix(conn, row: dict) -> None:
    """舊 run 裡被擋成「認不出的設備」、但大類判得出是 AIX 的列（只在讀取時，不改 DB）。

    修正前跑的那一輪，這台根本沒試連（被擋了），所以**不能假造「已納管」**：
      - 22 開著 → 待佈身分（上次納管失敗過就寫納管失敗，同 run_dispatch），並講明要重新收集才會真的試連
      - 22 沒開 → 交給下面「進不去的機器」重判（route=agent → 待開 SSH）；其餘 route 直接待開 SSH
    """
    note = ("依現在的判斷重判：AIX（作業系統大類）——這一輪收集是在修正前跑的，當時被誤判成"
            "「認不出的設備」、沒有試連；重新收集就會走 SSH 收集真的試連一次。")
    row["not_onboardable_kind"] = None
    if row.get("route") == ROUTE_SSH:
        row["status"], row["message"], row["not_onboardable_kind"] = _credential_outcome(
            conn, row["ip"], note)
        if row["status"] != STATUS_NEEDS_CREDENTIAL:
            row["message"] = f"{note}{row['message']}"
    elif row.get("route") == ROUTE_AGENT:
        row["status"] = STATUS_NEEDS_AGENT
        row["message"] = note
    else:
        row["status"] = STATUS_NEEDS_SSH_PORT
        row["message"] = f"{note}22 沒開——請開 SSH（22）給收集器，開了再一鍵納管"


def _rejudge_block(conn, row: dict, esxi_names, os_cls: str | None = None) -> None:
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
    blk = _mainframe_block(blk, os_cls, (asset or {}).get("os"))
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
        # 大型主機頁簽裡這台能不能佈收集帳號（AIX 可以、AS400／IBM i 不行）。
        # 「能不能」最後仍看 status（另有納管方式的一律不行），這裡只是讓前端不必自己背大類清單。
        if r["tab"] == TAB_MAINFRAME:
            _mf = mainframe_group()
            _cls = (ov or {}).get("os_type") if ov else r.get("os_class")
            # 完全沒回應（route=import）的也不算——跟「動得了的」另外三個頁簽同一個意思：
            # 現在就有下一步可做。AIX 本身可佈帳號，但連不上就還不是現在能做的事。
            r["mainframe_onboardable"] = bool(
                r.get("status") != STATUS_NOT_ONBOARDABLE
                and r.get("route") != ROUTE_IMPORT
                and (_cls in _mf["onboardable_classes"] or (ov and _cls == "AIX")))
        # 不納管的要指路（見 _NOK_GUIDE）：「不納管」不是結論，「它走哪條路」才是。
        # `asset_os` 由呼叫端帶進來（run_dispatch 手上就有、latest_run 另外補），
        # 只用在 appliance 這一桶再認 IBM i／Power。
        if r["status"] == STATUS_NOT_ONBOARDABLE:
            g = nok_guidance(r.get("not_onboardable_kind"), r.get("asset_os"),
                             _mainframe_class(r.get("os_class"), ov))
            # 閘道位址（v1.468.3）：判斷帶了下一步就用它（人工指定過的照指定，不加）
            _nx = (r.get("kind_guess") or {}).get("next_step")
            if g and _nx and not ov:
                g = {**g, "note": _nx}
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


def _merged_results(conn, run_id: int | None) -> list[dict]:
    """逐台結果。run_id=None＝合併檢視：每個位址取**最新一次**的結果（id 最大的那筆，
    跨所有收集；還在跑的那一輪已存檔的批也算）。給 run_id＝只看那一次。"""
    # ttl 欄 1.467.0 才有。補欄在 db.init_db（服務啟動時）——但 deploy.sh [2d] 的 verify_numbers
    # 是在**重啟之前**拿正式庫直接跑的，那時還沒補欄。沒有就當 NULL（＝當時沒取 TTL），不要整支炸掉。
    _cols = {c[1] for c in conn.execute("PRAGMA table_info(collect_dispatch_result)")}
    _ttl = "r.ttl" if "ttl" in _cols else "NULL AS ttl"
    sel = ("SELECT r.ip, r.alive, r.open_ports, r.route, r.status, r.asset_serial, r.hostname, "
           f"r.registered, r.message, r.not_onboardable_kind, {_ttl}, r.run_id, "
           "COALESCE(r.created_at, u.finished_at, u.started_at) AS collected_at "
           "FROM collect_dispatch_result r LEFT JOIN collect_dispatch_run u ON u.id = r.run_id ")
    if run_id is not None:
        return [dict(r) for r in conn.execute(sel + "WHERE r.run_id = ? ORDER BY r.id", (run_id,))]
    return [dict(r) for r in conn.execute(
        sel + "WHERE r.id IN (SELECT MAX(id) FROM collect_dispatch_result GROUP BY ip) "
        "ORDER BY r.id")]


#: 讀取時可以被「之後納管成功」蓋過去的狀態：那一輪連得到路、但身分不通／納管失敗／收集失敗
OVERLAY_STATUSES = (STATUS_NEEDS_CREDENTIAL, STATUS_ONBOARD_FAILED, STATUS_FAILED, STATUS_WIN_PENDING)


def _overlay_onboarded(conn, results: list[dict]) -> int:
    """收集結果存的是「那一輪」的事實；之後才納管成功的台，讀取時疊上現況（2026-10-20）。

    使用者批次納管 18 台全部成功，重整後待納管表卻還是「待佈身分／請執行納管」——
    因為畫面只改了記憶體裡那份，存著的收集結果（重整時讀的）沒人回頭對。
    **不改寫歷史**（collect_dispatch_result 不動），只在讀的時候疊：

    這一列是 SSH／WinRM 路、狀態是待佈身分／納管失敗／收集失敗，而且**在這列的收集時間之後**：
      1. 這個 IP 最後一次納管動作（含取消納管）是成功的納管，或
      2. 掛這個 IP 的資產，收集帳號最後確認收得到（hardware.collect_ok=1）
    → 改報「已收集」，訊息講明是那輪之後才納管成功的、何時。納管失敗或之後又取消納管的不疊。
    回疊了幾列。頁簽數字跟著變（summarize 吃同一份 results），加總＝下鑽不受影響。
    """
    cand = [r for r in results if r.get("status") in OVERLAY_STATUSES
            and r.get("route") in (ROUTE_SSH, ROUTE_WINRM) and r.get("ip")]
    if not cand:
        return 0
    ips = list(dict.fromkeys(r["ip"] for r in cand))
    onb: dict[str, tuple[str, str]] = {}
    chk: dict[str, str] = {}
    revoked: dict[str, str] = {}      # 最後一個納管動作是「取消納管」的 IP → 何時
    for i in range(0, len(ips), 400):
        chunk = ips[i:i + 400]
        ph = ",".join("?" * len(chunk))
        try:
            for row in conn.execute(
                    "SELECT a.target_ip, a.ok, a.trigger, a.created_at FROM onboard_audit a "
                    f"JOIN (SELECT target_ip, MAX(id) AS mid FROM onboard_audit WHERE target_ip IN ({ph}) "
                    "GROUP BY target_ip) m ON m.mid = a.id", chunk):
                if (row["trigger"] or "") == "revoke":
                    revoked[row["target_ip"]] = row["created_at"] or ""
                elif row["ok"] and row["created_at"]:
                    onb[row["target_ip"]] = (row["created_at"], "納管")
        except Exception:  # noqa: BLE001 - 舊庫沒有納管紀錄表：只看收集狀態
            pass
        try:
            for row in conn.execute(
                    f"SELECT ip, MAX(collect_checked_at) AS at FROM hardware WHERE ip IN ({ph}) "
                    "AND collect_ok = 1 AND collect_checked_at IS NOT NULL GROUP BY ip", chunk):
                chk[row["ip"]] = row["at"]
        except Exception:  # noqa: BLE001 - 舊庫沒有收集狀態欄：只看納管紀錄
            pass
    n = 0
    for r in cand:
        since = r.get("collected_at") or ""
        if r["ip"] in revoked:
            continue          # 最後一個納管動作是取消納管：不管收集狀態怎麼寫都不疊（fail-closed）
        ev = []
        if r["ip"] in onb and onb[r["ip"]][0] > since:
            ev.append((onb[r["ip"]][0], "納管成功"))
        if r["ip"] in chk and (chk[r["ip"]] or "") > since:
            ev.append((chk[r["ip"]], "收集帳號確認收得到"))
        if not ev:
            continue
        at, what = max(ev)
        r["overlay_from"] = r["status"]
        r["overlay_at"] = at
        r["status"] = STATUS_COLLECTED
        r["message"] = (f"這次收集（{since or '時間不詳'}）之後已{what}（{at}）——"
                        f"現在是已納管；下次收集會更新這一列。當時的結果：{r.get('message') or ''}")
        n += 1
    return n


#: 讀成「待納管」時保留原狀態的那幾種（WinRM 這條路、人還沒說要收的）
_WIN_PENDING_FROM = (STATUS_COLLECTED, STATUS_NEEDS_CREDENTIAL, STATUS_FAILED, STATUS_ONBOARD_FAILED)


def _overlay_win_optin(conn, results: list[dict]) -> int:
    """Windows 勾選才收（2026-10-22）：走 WinRM、現在**沒納管**的列，讀取時一律報「待納管」。

    兩種舊列要改讀：
      1. 上線前那幾輪自動登入留下的結果（收集失敗／待佈身分）——現在那台不會再被登入，
         照舊報「收集失敗」會叫人去修一台沒人要收的機器。
      2. 收成功過、但之後被人取消納管的——不可以還掛著「已納管」（fail-closed）。
    不改寫存著的結果（同 _overlay_onboarded），只在讀的時候疊。上一次的結果接在訊息後面，查得到。
    放在 _overlay_onboarded **之後**：那支靠納管紀錄把列疊成「已收集」，這支再把沒納管的拉回待納管。
    回改了幾列。
    """
    n = 0
    cand = [r for r in results if r.get("route") == ROUTE_WINRM
            and r.get("status") in _WIN_PENDING_FROM and r.get("ip")]
    if not cand:
        return 0
    res = win_onboard.resolve_batch(conn, [r["ip"] for r in cand])   # 一次認完（上千列不逐列查全套）
    for r in cand:
        _a, st, reason = res.get(r["ip"]) or (None, None, win_onboard.PENDING_MESSAGE)
        if st and st.get("opted_in") == 1:
            continue
        why = (f"已由 {st.get('revoked_by') or '不詳'} 於 {st.get('revoked_at') or '不詳'} 取消納管"
               if st and st.get("revoked_at") else "")
        r["status"] = STATUS_WIN_PENDING
        r["message"] = (reason + (f"（{why}）" if why else "")
                        + f"——上一次的結果：{r.get('message') or ''}")
        n += 1
    return n


def _attach_win(conn, results: list[dict]) -> None:
    """WinRM 那幾列帶上 Windows 納管狀態（opted_in／本機管理員／誰何時納管），畫面據此給按鈕。

    只看走 WinRM 的、或開了 5985 的（裝了 OpenSSH、被人搬到 WinRM 頁簽的那種）。"""
    ips = [r["ip"] for r in results if r.get("ip") and (
        r.get("route") == ROUTE_WINRM or "5985" in (r.get("open_ports") or "").split(","))]
    if not ips:
        return
    st = win_onboard.state_map(conn, ips)
    for r in results:
        if r.get("ip") in st:
            r["win"] = st[r["ip"]]


def _merge_info(conn, results: list[dict], run_id: int | None) -> dict:
    """合併檢視的表頭：合併了幾次收集、最早／最新、幾列算舊（超過 stale_days 天）。每列補 stale。"""
    from datetime import datetime, timedelta

    days = stale_days(conn)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    times = [r["collected_at"] for r in results if r.get("collected_at")]
    for r in results:
        r["stale"] = bool(r.get("collected_at")) and r["collected_at"] < cutoff
    try:
        run_total = conn.execute("SELECT COUNT(*) FROM collect_dispatch_run").fetchone()[0]
    except Exception:  # noqa: BLE001
        run_total = None
    return {
        "mode": "run" if run_id is not None else "merged",
        "run_id": run_id,
        "run_count": len({r["run_id"] for r in results}),
        "run_total": run_total,
        "earliest": min(times) if times else None,
        "latest": max(times) if times else None,
        "stale_days": days,
        "stale_count": sum(1 for r in results if r["stale"]),
    }


def _a_run_is_running(conn) -> bool:
    """有收集正在跑：進度與「還活著嗎」看現在時間與記憶體停止旗標，不能用快取（獨立審查 #2）。"""
    return conn.execute(
        "SELECT 1 FROM collect_dispatch_run WHERE status = 'running' LIMIT 1").fetchone() is not None


@_fleet_cache.cached(skip_if=_a_run_is_running)   # 跨請求快取：資料庫沒變、也沒有收集在跑就不重算
def latest_run(conn, run_id: int | None = None) -> dict | None:
    """收集結果（含逐台）。頁面重新整理後靠這支還原。

    v1.468.4（2026-10-19 使用者：「用單一主機收集完之後，發現資料全部不見了，我得全部重新收集一次」）：
    以前只回**最近一次**收集——收一台就把前一次 9000 個位址的結果藏起來（資料沒刪，只是不顯示）。
    現在預設是**合併檢視**：每個位址取它最新一次的結果，跨所有收集；每列帶自己的收集時間
    （collected_at）與「舊」（stale）。頁簽數字／下鑽／verify_numbers 都算這一份（加總＝下鑽）。
    `run` 仍是最近一次收集（畫面的進度與「還在跑」看它）；`run_id` 給了就只看那一次。
    """
    run = conn.execute(
        "SELECT * FROM collect_dispatch_run ORDER BY id DESC LIMIT 1").fetchone()
    if run is None:
        return None
    # 「還在跑嗎」看心跳，不只看 status 字（2026-10-20，見 run_liveness）
    run = {**dict(run), **run_liveness(conn, dict(run))}
    if run_id is not None and conn.execute(
            "SELECT 1 FROM collect_dispatch_run WHERE id = ?", (run_id,)).fetchone() is None:
        return None
    results = _merged_results(conn, run_id)
    merged = _merge_info(conn, results, run_id)
    # 那一輪之後才納管成功的台：讀取時疊上現況（不改寫存著的結果），見 _overlay_onboarded
    merged["overlaid"] = _overlay_onboarded(conn, results)
    # Windows 勾選才收：現在沒納管（從沒納管、或已取消納管）的 WinRM 列一律讀成「待納管」，見 _overlay_win_optin
    merged["win_pending_overlaid"] = _overlay_win_optin(conn, results)
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
        # 大類（大型主機頁簽的依據）整批一次問——擋不擋要跟頁簽看同一個判斷（見 _mainframe_block）
        cls_map = os_class_by_ip(conn, [r["ip"] for r in results if r.get("registered")])
        for r in results:
            if id(r) in overridden:
                continue
            if _rejudge_block_candidate(r):
                _rejudge_block(conn, r, esxi_names, cls_map.get(r["ip"]))
            if r["status"] == STATUS_NOT_ONBOARDABLE:
                r["not_onboardable_kind"] = _rejudge_kind(conn, r, esxi_names)
                if (r["not_onboardable_kind"] == "appliance"
                        and _mainframe_block(("appliance", "", ""), cls_map.get(r["ip"])) is None):
                    _reopen_aix(conn, r)

    # 「活著但進不去」的列依**現在的**證據重判是什麼機器（2026-10-19）。舊 run（那時一律
    # 待裝 Agent）讀出來也要分得出 Windows／AIX／設備——使用者升版後不必重跑收集就看得到。
    # 只動 AGENT_FAMILY 那幾個狀態；已被判成另有納管方式的不動（fail-closed，不往回翻）。
    # 判斷不存進 DB（證據會變），所以已被判成另有納管方式的列也在這裡補上判斷（只給畫面看，不改狀態）。
    agent_rows = [r for r in results if r.get("route") == ROUTE_AGENT and r.get("alive")]
    if agent_rows:
        import host_kind
        kin = host_kind.evidence_inputs(conn, [r["ip"] for r in agent_rows])
        for r in agent_rows:
            if r.get("status") in AGENT_FAMILY:
                _apply_agent_kind(r, kin.get(r["ip"]), override_of(ov_map, r))
            elif r.get("status") == STATUS_NOT_ONBOARDABLE:
                okind, why = _override_kind(override_of(ov_map, r))
                r["kind_guess"] = host_kind.infer_from(
                    kin.get(r["ip"]), open_ports=r.get("open_ports"), ttl=r.get("ttl"),
                    override_kind=okind, override_why=why,
                    ip=r["ip"], registered=r.get("registered"))

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
    #
    # 2026-10-22 使用者改規則：「結果」也要跟著人工分類走——
    #   「19.210 是 storage，我歸到 storage，結果應該是『另有納管方式』」
    #   「反之從 storage 拉回 Linux，就應該要有一鍵納管」
    # 所以搬到另有納管方式的 → 狀態改另有納管方式；搬回可收集的 → 狀態改成依路線的待辦
    #（待佈身分／待裝 Agent／只能匯入），message 講明「還沒試連」。一鍵納管走納管引擎，
    # 引擎連上去仍會驗平台白名單與 VMware 天條（fail-closed），所以搬錯也建不了帳號。
    for r in results:
        ov = override_of(ov_map, r)
        if not ov:
            continue
        who = f"人工指定為「{TAB_LABEL[ov['tab']]}」（{ov['by'] or '不詳'} 於 {ov['at']}）"
        nok = _TAB_NOK_KIND.get(ov["tab"])
        if ov["tab"] == TAB_MAINFRAME and ov.get("os_type") == _ibmi_os_type():
            nok = "appliance"
        if nok:
            if r["status"] not in (STATUS_NOT_ONBOARDABLE,) + SUCCESS_STATUSES:
                r["status"] = STATUS_NOT_ONBOARDABLE
                r["not_onboardable_kind"] = nok
                r["message"] = f"{who}——{NOT_ONBOARDABLE_SENTENCE}。原本的結果：{r['message']}"
        elif r["status"] == STATUS_NOT_ONBOARDABLE and r.get("not_onboardable_kind") == "esxi":
            # VMware 天條：判成 ESXi／vCenter 的，人工搬回可收集也**不**轉成待辦、不給一鍵納管
            #（獨立審查 2026-10-22：連線時的遠端防線不能是唯一一道）
            r["message"] = (f"{who}——但這台被判定為 VMware（{r['message']}），"
                            f"VMware 一律不建帳號；確定不是 VMware 請先修正資產登記")
        elif r["status"] == STATUS_NOT_ONBOARDABLE:
            r["status"] = {ROUTE_SSH: STATUS_NEEDS_CREDENTIAL, ROUTE_WINRM: STATUS_NEEDS_CREDENTIAL,
                           ROUTE_AGENT: STATUS_NEEDS_AGENT}.get(r.get("route"), STATUS_IMPORT_ONLY)
            r["not_onboardable_kind"] = None
            r["message"] = (f"{who}——這一輪收集是在指定之前跑的，還沒試連過；"
                            f"可一鍵納管，或下次收集時會真的試連。原本的判定：{r['message']}")
    _attach_os_class(conn, results)
    _attach_vendor(conn, results, ov_map)
    _attach_win(conn, results)
    return {"run": run, "merged": merged, **summarize(results, ov_map), "results": results}


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
