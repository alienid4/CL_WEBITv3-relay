"""納管狀態：系統技術上收不收得到這台機器。

⚠️ 這跟 `asset_status`（資產狀態）是**兩條各自獨立的軸**，不可混用：
  - asset_status = 業務生命週期（使用中／閒置／維修中／停用／已汰換），人維護
  - 納管狀態     = 系統收不收得到它，系統自己試連後算出來

一台機器可以同時是「使用中」而且「連不進去」——兩句都對、都有用。
混成同一欄就會丟掉其中一個資訊（使用者 2026-07-19 明確指出這個區別）。

四態互斥且窮盡：你知道的每一台機器都剛好落在一格，加起來就是全部。
每一格都對應一個明確動作，這是它比「資產數／異常數」更有用的原因。
"""
from __future__ import annotations

import subprocess

# 四態。值刻意用中文：這是要直接顯示在畫面上的，不需要再翻一層。
UNREGISTERED = "未登記"   # 掃到了，但 ICA 完全沒這台      → 去「納入管理」
NOT_ONBOARDED = "未納管"  # 已登記，但收集帳號連不進去      → 貼 bootstrap 納管腳本
ONBOARDED = "已納管"      # 收集帳號 OK，拿得到主機名/OS/序號 → 完成
LOST = "失聯"             # 以前掃得到，這次掃不到           → 關機？換 IP？下線？

ALL_STATES = (UNREGISTERED, NOT_ONBOARDED, ONBOARDED, LOST)

# 每一態要人做什麼——畫面直接用這個，不要各頁自己編一套說法
NEXT_ACTION = {
    UNREGISTERED: "掃到了但沒登記，去「納入管理」把它建成資產",
    NOT_ONBOARDED: "已登記但收集帳號連不進去，在該機器執行納管腳本",
    ONBOARDED: "收集正常，無需處理",
    LOST: "登記在案但這次掃不到——確認是否關機、換 IP 或已下線",
}


def classify(registered: bool, seen_in_scan: bool, collect_ok: int | None) -> str:
    """把「有沒有登記／這次有沒有掃到／收集連不連得上」三個事實變成一個狀態。

    刻意寫成純函式（不碰 DB、不碰網路）——這是整個功能的判定核心，
    必須能被直接測到，不能藏在 SQL 或 API 裡。
    """
    if not registered:
        return UNREGISTERED
    if not seen_in_scan:
        # 登記了卻掃不到＝失聯。就算它曾經收得到，現在人不在也是失聯，
        # 這比「已納管」更重要——顯示成已納管會讓人以為一切正常。
        return LOST
    if collect_ok == 1:
        return ONBOARDED
    # collect_ok 是 0（試過連不上）或 None（還沒試過）都算未納管：
    # 對使用者來說「還收不到」跟「還沒試」要做的事一樣——去把它納管起來。
    return NOT_ONBOARDED


def probe_collect(host: str, key_path: str, account: str = "webit3scan",
                  timeout: int = 8, runner=None) -> tuple[bool, str | None]:
    """試連一台機器，看收集帳號通不通。回 (成功?, 失敗原因)。

    只跑一個無害的 `hostname`，不改目標機器任何東西。
    runner 可注入，測試不打真網路。
    """
    if runner is not None:
        return runner(host)
    cmd = [
        "ssh", "-i", key_path, "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no", "-o", f"ConnectTimeout={timeout}",
        f"{account}@{host}", "hostname",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.SubprocessError as exc:
        return False, f"執行失敗：{exc}"
    if r.returncode == 0 and r.stdout.strip():
        return True, None
    return False, _clean_ssh_error(r.stderr or r.stdout or "")


# SSH 會把這些無害訊息也寫進 stderr，它們不是失敗原因。
# 實測踩到：.221/.224 的失敗原因顯示成「Warning: Permanently added ...」——
# 那只是首次連線加 host key 的提示，真正的原因（帳號不存在）反而被蓋掉，
# 等於白費了「保留原始錯誤」的用意。
_SSH_NOISE = (
    "Warning: Permanently added",
    "warning: connection is not using a post-quantum",
    "This session may be vulnerable",
    "The server may need to be upgraded",
)


def _clean_ssh_error(raw: str) -> str:
    """濾掉 SSH 的雜訊行，只留真正的失敗原因。

    「Permission denied」（帳號/金鑰問題，去佈納管腳本）跟「Connection timed out」
    （機器不在或防火牆，去查機器）要做的事完全不同——把原因吞成一句「連線失敗」
    或顯示成無關的警告，都等於叫使用者自己猜。
    """
    lines = [
        ln.strip() for ln in raw.splitlines()
        if ln.strip() and not any(n.lower() in ln.lower() for n in _SSH_NOISE)
    ]
    return ("；".join(lines))[:300] if lines else "無回應（連得上但沒有輸出）"


COLLECTOR_KEY_DEFAULT = "/opt/webit3/.collector_key"


def refresh_collect_status(conn, key_path: str = COLLECTOR_KEY_DEFAULT,
                           runner=None, workers: int = 8) -> dict:
    """對所有「有 IP 的已登記資產」試連一次，把結果寫回 hardware。

    回 {"checked": n, "ok": n, "failed": n}。

    為什麼要存而不是每次現算：試連一台要好幾秒，8 台就十幾秒——
    畫面不能每次載入都等這個。存下來、由排程定期更新，畫面讀快取。
    所以每筆都帶 collect_checked_at，讓人知道這個結論是什麼時候的。
    """
    from concurrent.futures import ThreadPoolExecutor
    from db import _now_local

    rows = conn.execute(
        "SELECT asset_serial, ip FROM hardware WHERE ip IS NOT NULL AND ip != ''"
    ).fetchall()
    targets = [(r["asset_serial"], r["ip"]) for r in rows]

    locals_ = local_ips()

    def work(item):
        serial, ip = item
        if runner is None and ip in locals_:
            # 本機不需要 SSH 帳號——直接跑一個指令確認收得到就好
            try:
                out = _local_runner()(ip, "hostname")
                return serial, bool(out.strip()), None
            except Exception as exc:  # noqa: BLE001
                return serial, False, f"本機收集失敗：{exc}"
        ok, err = probe_collect(ip, key_path, runner=runner)
        return serial, ok, err

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(work, targets))

    now = _now_local()
    for serial, ok, err in results:
        conn.execute(
            "UPDATE hardware SET collect_ok = ?, collect_checked_at = ?, collect_error = ? "
            "WHERE asset_serial = ?",
            (1 if ok else 0, now, None if ok else err, serial),
        )
    conn.commit()
    ok_n = sum(1 for _, ok, _ in results if ok)
    return {"checked": len(results), "ok": ok_n, "failed": len(results) - ok_n}


def summarize(conn) -> dict:
    """全站四態統計＋每一台的狀態。畫面（儀表板四格、資產清單狀態欄）共用這一份，
    數字才不會跟清單對不上。"""
    from api import _latest_scan_time, _row_in_keys, _scan_keys, _scanned_alive_rows

    scan_time = _latest_scan_time(conn)
    scanned = _scanned_alive_rows(conn, scan_time)
    scan_ips, scan_hostnames = _scan_keys(scanned)

    hw = conn.execute(
        "SELECT asset_serial, hostname, ip, collect_ok, collect_checked_at, collect_error "
        "FROM hardware"
    ).fetchall()
    hw_ips = {r["ip"] for r in hw if r["ip"]}
    hw_hostnames = {r["hostname"] for r in hw if r["hostname"]}

    items = []
    counts = {s: 0 for s in ALL_STATES}

    for r in hw:
        seen = _row_in_keys(r, scan_ips, scan_hostnames)
        state = classify(True, seen, r["collect_ok"])
        counts[state] += 1
        items.append({
            "asset_serial": r["asset_serial"], "hostname": r["hostname"], "ip": r["ip"],
            "state": state, "collect_checked_at": r["collect_checked_at"],
            "collect_error": r["collect_error"],
        })

    # 掃到但沒登記的＝未登記，它們還不在 hardware 裡，要從掃描側補進來
    for r in scanned:
        if not r["ip"] and not r["hostname"]:
            continue
        if _row_in_keys(r, hw_ips, hw_hostnames):
            continue
        counts[UNREGISTERED] += 1
        items.append({
            "asset_serial": None, "hostname": r["hostname"], "ip": r["ip"],
            "state": UNREGISTERED, "collect_checked_at": None, "collect_error": None,
        })

    return {
        "scan_time": scan_time,
        "counts": counts,
        "total_known": sum(counts.values()),
        "next_action": NEXT_ACTION,
        "items": items,
    }


def local_ips() -> set[str]:
    """本機自己的 IP。收集器跑在哪台，那台就不需要 SSH。"""
    ips = set()
    try:
        r = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        ips.update(x for x in r.stdout.split() if x)
    except (OSError, subprocess.SubprocessError):
        pass
    ips.update({"127.0.0.1", "localhost"})
    return ips


def _local_runner(timeout: int = 10):
    """本機收集：直接執行指令，不繞 SSH。

    收集器就跑在這台機器上，要它「SSH 自己」才收得到資料是多此一舉——
    還得替自己建收集帳號、佈自己的公鑰，平白多一份維護與失敗點。
    """
    def run(host: str, cmd: str) -> str:
        r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True,
                           timeout=timeout + 10)
        return r.stdout
    return run


# 收集身分預設值。webit3scan＝唯讀最小權限；設成 sysinfra 等有 NOPASSWD:ALL 的
# 標準管理帳號時，sudo -n 那些指令會通，能拿到密碼/sudo/authorized_keys 明細。
DEFAULT_COLLECT_ACCOUNT = "webit3scan"


def get_collect_account(conn) -> str:
    """讀目前設定的遠端收集身分。沒設就回 webit3scan（最小權限預設）。"""
    from db import get_setting

    return get_setting(conn, "collect_ssh_account", DEFAULT_COLLECT_ACCOUNT) \
        or DEFAULT_COLLECT_ACCOUNT


def _runner_for(ip: str, key_path: str, account: str = DEFAULT_COLLECT_ACCOUNT):
    """本機走本機、遠端走 SSH。呼叫端不用自己判斷。

    account＝SSH 登入身分。本機捷徑(_local_runner)**只在用預設唯讀帳號時才走**——
    一旦設了 sysinfra 這種管理身分，本機也要以那個身分 SSH（SSH 到自己）。
    否則會出現「其他台都靠 sysinfra 收到完整資料、唯獨收集器 221 自己走非 root 的
    服務帳號、需 root 的欄位全查不到」這種只有一台缺一半的怪象（實際踩到）。
    前提：收集公鑰要授權進本機該管理帳號的 authorized_keys（與遠端同一套佈署）。
    """
    if account == DEFAULT_COLLECT_ACCOUNT and ip in local_ips():
        return _local_runner()
    return _ssh_runner(key_path, account=account)


def _ssh_runner(key_path: str, account: str = "webit3scan", timeout: int = 10):
    """給 facts_collector 用的 runner：runner(host, cmd) -> str。"""
    def run(host: str, cmd: str) -> str:
        r = subprocess.run(
            ["ssh", "-i", key_path, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "-o", f"ConnectTimeout={timeout}", f"{account}@{host}", cmd],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        return r.stdout
    return run


# facts 收到的欄位 → hardware 的欄位。只寫「機器裡的事實」，不碰業務欄位
# （資產用途、保管者那些是人填的，收集不該覆蓋掉人的輸入）。
FACT_FIELDS = ("hostname", "os", "device_model", "hw_serial", "mac", "is_vm")


def collect_facts_into_assets(conn, key_path: str = COLLECTOR_KEY_DEFAULT,
                              runner=None, only_serial: str | None = None) -> dict:
    """對「已納管」的機器收 facts，寫回 hardware。

    為什麼要有這步：先前「已納管」只代表**連得上**，但收到的東西從來沒寫回資產——
    3 台連得上的機器 OS 欄位全是空的。連得上卻不拿資料，等於納管了個寂寞。

    只覆蓋「機器裡的事實」欄位（主機名/OS/機型/序號/MAC/虛實），
    **不碰人填的業務欄位**；收不到的欄位保持原值，不用 None 洗掉既有資料。
    """
    import facts_collector
    from db import _now_local

    sql = ("SELECT asset_serial, ip FROM hardware "
           "WHERE ip IS NOT NULL AND ip != '' AND collect_ok = 1")
    if only_serial:
        sql += " AND asset_serial = ?"
        rows = conn.execute(sql, (only_serial,)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()

    updated, failed = 0, []
    for r in rows:
        # 平台從掃描指紋猜（3389→windows），facts_collector 各平台指令集不同
        guess = conn.execute(
            "SELECT open_ports FROM scan_history WHERE ip = ? AND scan_ok = 1 "
            "ORDER BY scan_time DESC LIMIT 1", (r["ip"],)
        ).fetchone()
        ports = [int(p) for p in (guess["open_ports"] or "").split(",") if p.strip().isdigit()] \
            if guess else []
        platform = facts_collector.detect_platform(ports)
        # Windows 走原生 WinRM/CIM，不走 SSH——收集不必在目標機建帳號、佈金鑰、改設定。
        # （使用者定案：Windows 應該靠 Windows 方式，硬套 SSH 撞的每一面牆都是典範錯置。）
        if platform == "windows" and runner is None:
            facts = _collect_windows(conn, r["ip"], r["asset_serial"], failed)
            if facts is None:
                continue
            setters = {k: v for k, v in facts.items()
                       if k in FACT_FIELDS and v not in (None, "")}
            if setters:
                assigns = ", ".join(f"{k} = ?" for k in setters)
                conn.execute(
                    f"UPDATE hardware SET {assigns}, collect_checked_at = ? "
                    "WHERE asset_serial = ?",
                    (*setters.values(), _now_local(), r["asset_serial"]),
                )
                updated += 1
            continue
        run = runner or _runner_for(r["ip"], key_path)
        try:
            facts = facts_collector.collect(run, r["ip"], platform)
        except Exception as exc:  # noqa: BLE001
            failed.append({"asset_serial": r["asset_serial"], "error": str(exc)[:200]})
            continue
        setters = {k: v for k, v in facts.items() if k in FACT_FIELDS and v not in (None, "")}
        if not setters:
            failed.append({"asset_serial": r["asset_serial"], "error": "收不到任何欄位"})
            continue
        assigns = ", ".join(f"{k} = ?" for k in setters)
        conn.execute(
            f"UPDATE hardware SET {assigns}, collect_checked_at = ? WHERE asset_serial = ?",
            (*setters.values(), _now_local(), r["asset_serial"]),
        )
        updated += 1
    conn.commit()
    return {"updated": updated, "failed": failed, "candidates": len(rows)}


# OS 字串 → 平台大類。同一個平台在資料裡有各種寫法
# （"Rocky Linux 9.7"／"Ubuntu 22.04"／"Linux/Unix（TTL≈64）"），
# 不歸類就統計不出「我有幾台 Windows」這種真正有用的問題。
_PLATFORM_RULES = (
    ("Windows", ("windows", "win server", "microsoft")),
    ("Linux", ("linux", "rocky", "ubuntu", "debian", "centos", "rhel", "red hat", "suse", "fedora")),
    ("AIX/Unix", ("aix", "solaris", "hp-ux", "unix")),
    ("網路設備", ("網路設備", "switch", "router", "ios", "junos")),
)


def platform_of(*candidates: str | None) -> str:
    """依序看幾個線索字串，回平台大類。都認不出來回「未知」。

    刻意接受多個候選：優先用 facts 收到的真 OS，收不到才退回掃描的 os_guess——
    真資料永遠優先於推測，但推測比空白有用。
    """
    for text in candidates:
        if not text:
            continue
        low = str(text).lower()
        for label, keys in _PLATFORM_RULES:
            if any(k in low for k in keys):
                return label
    return "未知"


def composition(conn) -> dict:
    """全站組成統計：我的機器長什麼樣子。

    這是儀表板該回答的問題（使用者 2026-07-19）——「有幾台 Windows／各平台各幾台」
    是統計，而「兩邊相符／登記卻掃不到」是對帳細節，屬於小功能不該當頭條。

    OS 來源優先序：hardware.os（facts 收到的真 OS）> 掃描的 os_guess（推測）。
    每一類都附 source 說明資料是真的還是猜的，畫面才能誠實標示。
    """
    latest = conn.execute("SELECT MAX(scan_time) AS t FROM scan_history").fetchone()
    scan_time = latest["t"] if latest else None
    guesses = {}
    if scan_time:
        for r in conn.execute(
            "SELECT ip, os_guess, mac_vendor FROM scan_history WHERE scan_time = ? AND scan_ok = 1",
            (scan_time,),
        ):
            guesses[r["ip"]] = (r["os_guess"], r["mac_vendor"])

    rows = conn.execute(
        "SELECT ip, os, device_model, is_vm, environment, asset_status FROM hardware"
    ).fetchall()

    def bump(d, k):
        d[k] = d.get(k, 0) + 1

    import normalize

    by_platform, by_env, by_status, by_virt = {}, {}, {}, {}
    by_os, by_model = {}, {}
    real_os = 0
    for r in rows:
        guess_os = guesses.get(r["ip"], (None, None))[0]
        bump(by_platform, platform_of(r["os"], guess_os, r["device_model"]))
        # 正規化後才統計：同一個 OS 在資料裡有多種寫法
        # （Rocky Linux 9.7 vs Rocky Linux 9.7 (Blue Onyx)），不收斂就會被算成兩種。
        if r["os"]:
            real_os += 1
            bump(by_os, normalize.normalize_os(r["os"], conn)["canonical"])
        if r["device_model"]:
            bump(by_model, normalize.normalize_model(r["device_model"], conn)["canonical"])
        bump(by_env, r["environment"] or "未填")
        bump(by_status, r["asset_status"] or "未填")
        # is_vm 在資料裡混了 0/1 與 'VM' 字串（納管表單存字串），這裡統一判定
        v = r["is_vm"]
        is_vm = str(v).strip().upper() in ("1", "VM", "TRUE", "是")
        bump(by_virt, "虛擬機" if is_vm else "實體機")

    return {
        "total": len(rows),
        "by_platform": by_platform,
        "by_environment": by_env,
        "by_status": by_status,
        "by_virtualization": by_virt,
        # 正規化後的明細：這才答得出「我有幾台 Rocky 9.7」
        "by_os": by_os,
        "by_model": by_model,
        # 待人工對應的髒資料（規則與字典都認不出來的原值）
        "pending_normalize": normalize.pending_values(conn),
        # 誠實揭露：多少台的 OS 是真的收到的、多少是靠掃描推測的
        "os_from_facts": real_os,
        "os_guessed": len(rows) - real_os,
    }


# 系統健康度：由關聯主機的納管四態推導，取代人手動標。
# 對應關係刻意保守——「我看不到它」不等於「它壞了」，但也絕不能說 ok。
_STATE_TO_HEALTH = {
    LOST: "err",            # 主機失聯＝系統確實有問題
    NOT_ONBOARDED: "warn",  # 收不到資料＝我不知道它好不好，不能說 ok
    ONBOARDED: "ok",
    UNREGISTERED: "warn",   # 理論上不會發生（已關聯代表已登記），保守起見算 warn
}
_HEALTH_RANK = {"ok": 0, "warn": 1, "err": 2}   # 取最差的那台當系統健康度


def system_health(conn) -> dict:
    """每個系統的健康度與關聯主機。

    關聯用 hardware.api_id → systems.id（那個欄位語意上本來就是系統代碼，
    資料也已經在用，不另開關聯表；日後真的出現「一台主機服務多個系統」再加）。

    ⚠️ 誠實揭露 health_source：
      derived = 由關聯主機的實際狀態推導出來的
      manual  = 沒有任何關聯主機，只能沿用人手動標的值
    畫面必須分得出來——把「某人半年前填的 ok」跟「系統剛剛確認過是 ok」混在一起，
    等於讓人相信一個沒有根據的綠燈。
    """
    states = {i["ip"]: i["state"] for i in summarize(conn)["items"] if i["ip"]}

    hosts_by_system: dict[str, list] = {}
    for r in conn.execute(
        "SELECT api_id, asset_serial, hostname, ip FROM hardware "
        "WHERE api_id IS NOT NULL AND api_id != ''"
    ):
        hosts_by_system.setdefault(r["api_id"], []).append({
            "asset_serial": r["asset_serial"], "hostname": r["hostname"],
            "ip": r["ip"], "state": states.get(r["ip"], NOT_ONBOARDED),
        })

    out = {}
    for s in conn.execute("SELECT id, health FROM systems"):
        hosts = hosts_by_system.get(s["id"], [])
        if not hosts:
            out[s["id"]] = {"health": s["health"], "health_source": "manual", "hosts": []}
            continue
        worst = max((_STATE_TO_HEALTH.get(h["state"], "warn") for h in hosts),
                    key=lambda h: _HEALTH_RANK[h])
        out[s["id"]] = {"health": worst, "health_source": "derived", "hosts": hosts}
    return out


# ---- 診斷外掛 ----
try:
    import diagnostics

    @diagnostics.register("manage_state")
    def _diag(conn) -> dict:
        """四態統計＋每台的判定結果與失敗原因（失敗原因是最有用的一段）。"""
        s = summarize(conn)
        return {
            "counts": s["counts"],
            "total_known": s["total_known"],
            "scan_time": s["scan_time"],
            "items": s["items"],
            "system_health": system_health(conn),
        }
except ImportError:
    pass


def _collect_windows(conn, ip: str, asset_serial: str, failed: list) -> dict | None:
    """Windows 走 WinRM/CIM 收集。憑證從憑證庫取、用完即丟，每次留稽核。

    回 facts dict；失敗回 None 並把原因放進 failed（原因要能行動：
    「沒有可用憑證」跟「WinRM 沒開」跟「帳密錯」要做的事完全不同）。
    """
    import credential_store
    import winrm_collector

    cred_name = credential_store.pick_for_host(conn, ip, kind="winrm")
    if not cred_name:
        failed.append({"asset_serial": asset_serial,
                       "error": "沒有可用的收集憑證——請先在系統設定新增 WinRM 服務帳號"})
        return None
    got = credential_store.get_for_use(conn, cred_name)
    if got is None:
        failed.append({"asset_serial": asset_serial,
                       "error": f"憑證「{cred_name}」解不開（加密金鑰可能已更換），請重新設定"})
        return None
    username, password = got
    try:
        facts = winrm_collector.collect(ip, username, password)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)[:200]
        # WinRM 沒開是最常見的情況，給 Windows 原生的解法而不是一句「連線失敗」
        if "actively refused" in msg or "timed out" in msg or "Max retries" in msg:
            msg = f"{msg}｜{winrm_collector.ENABLE_HINT}"
        credential_store.audit_use(conn, cred_name, ip, False, msg)
        failed.append({"asset_serial": asset_serial, "error": msg})
        return None
    credential_store.audit_use(conn, cred_name, ip, True, "收集成功")
    return facts
