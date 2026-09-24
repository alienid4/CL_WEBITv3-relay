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

import re
import subprocess

# 四態。值刻意用中文：這是要直接顯示在畫面上的，不需要再翻一層。
UNREGISTERED = "未登記"   # 掃到了，但 CIA 完全沒這台      → 去「納入管理」
NOT_ONBOARDED = "未納管"  # 已登記，但收集帳號連不進去      → 貼 bootstrap 納管腳本
ONBOARDED = "已納管"      # 收集帳號 OK，拿得到主機名/OS/序號 → 完成
LOST = "失聯"             # 以前掃得到，這次掃不到           → 關機？換 IP？下線？
# 2026-09-15 使用者定：SAN switch 等設備「收集」跟「納管」不一樣——納管是我能控制它
# （帳號佈好、系統自己定期收），收集是純收集（每次人給帳密或離線匯入）。所以獨立一態。
# ⚠️ 它跟已納管一樣算「有人顧著」：100 台裡 20 已納管、10 已收集 → 有問題的是 70 台不是 80 台。
COLLECTED = "已收集"      # 設備資料收集過（SAN 收集／離線匯入），非納管 → 定期再收保持最新

# 2026-09-15 使用者抓到：設備下線改成停用／報廢後，納管統計還是算它「失聯」、計入有問題。
# 退役是資產生命週期的歷史，不是要處理的問題——獨立一態、不算有問題。
RETIRED = "已退役"        # 資產狀態＝停用／報廢／閒置 → 不需處理（CIA 清冊記得同步）

# 2026-09-16 使用者：「如果我發現這個沒辦法納管、也不是下線，譬如客製化系統或者是 Oracle」——
# 自動判不納管（onboard_eligibility）只認得 ESXi／OpenShift／設備，客製系統只有人知道。
# 沒有人工入口，那些機器會永遠掛在「要處理」裡，把真正要處理的淹掉。見 onboard_exempt。
EXEMPT = "非納管設備"     # 人工標記豁免（客製化系統／Oracle／廠商維護…）→ 不需處理

# 2026-09-16 使用者：「怎麼會判失聯?」——他 ping 得通的機器被判失聯。
# 查證：失聯的定義是「不在最新一次掃描的存活清單裡」，但掃描範圍讀的是 connections（空的），
# 只 fallback 掃本機一段 → 其餘全部「不在清單裡」→ 全判失聯。
# 「沒掃過」跟「掃了沒回應」是兩件完全不同的事，要做的處置也不同，不能混成一個狀態。
NOT_COVERED = "未涵蓋"    # 這台的 IP 不在最近一次掃描涵蓋的網段內 → 去把那段加進掃描範圍
CONFLICT = "登記矛盾"     # [B-04] 同一台有的登記標退役、有的仍使用中 → 確認 CIA 哪筆對（逐台才有）
RETIRED_ALIVE = "退役但仍在線"  # [B-07] 標停用／報廢／閒置，卻還掃得到或收得到 → 稽核發現，要查為什麼沒關
# [B-09] 2026-09-18 數字健檢抓到：221「失聯」316 台全部是沒有 IP（305）或 IP 欄填兩個（11）——
# 掃描從來不可能找到它們，卻被算成失聯。沒有 IP＝沒查，不是失聯；要做的事是到 CIA 補 IP
NO_IP = "沒有 IP"         # 登記了但沒有可掃描的 IP → 掃描無從判斷在不在，去 CIA 補 IP

ALL_STATES = (UNREGISTERED, NOT_ONBOARDED, ONBOARDED, COLLECTED, LOST, RETIRED, EXEMPT, NOT_COVERED, CONFLICT,
              RETIRED_ALIVE, NO_IP)
#: 「要人處理」的狀態。已納管、已收集都不算——統計「有問題幾台」一律用這份，不要各處自己寫「!= 已納管」
NEEDS_ACTION_STATES = (UNREGISTERED, NOT_ONBOARDED, LOST, NOT_COVERED, CONFLICT, RETIRED_ALIVE, NO_IP)

# 停用/報廢/閒置＝退役資產，是資產生命週期的歷史，不算進「有效盤點」。
# 全站要排除退役的地方（composition 統計、重複偵測…）都共用這個常數，避免各處各自定義漏同步。
RETIRED_STATUS = {"停用", "報廢", "閒置"}


def is_vm_value(v, device_model=None) -> bool:
    """is_vm 欄位在資料裡混了 0/1 與 'VM' 字串（納管表單存字串），統一判定寫這裡
    一次，composition() 與 system_report.py 都吃這支——各自寫一份遲早會漂走
    （例如某處漏了 'TRUE' 這個變體，兩邊虛實拆分數字就對不起來）。

    2026-08-25 查證發現：is_vm 欄位本身漏填的情況很多——2536 筆 device_model
    寫著「(VM)」，is_vm 欄位卻是空/0，害「運算平台概況」的實體機數字灌水到
    1,464（該有的是 265 上下）。device_model 是輔助信號，不是唯一依據：
    只認「(VM)」「VM-」「VM(」開頭或整格剛好是「VM」——不能整串找「VM」子字串，
    那樣「ATEN…KVM」「Dell R330 Server KVM」這種實體 KVM 切換器會被誤判成虛擬機
    （KVM 在這裡是鍵盤/螢幕/滑鼠切換器，字尾剛好帶 VM 三個字母純屬巧合）。"""
    if str(v).strip().upper() in ("1", "VM", "TRUE", "是"):
        return True
    dm = str(device_model or "").strip().upper()
    return dm == "VM" or dm.startswith(("(VM)", "VM-", "VM("))


def is_vm_sql(is_vm_col: str = "is_vm", model_col: str = "device_model") -> str:
    """SQL 版的虛擬機判定，跟 is_vm_value **完全同義**（含 device_model「(VM)」等標記）。

    為什麼要有：以前資產查詢的虛實篩選（api）與 blast_radius 各寫一段 SQL，都只看
    is_vm 欄、漏了 device_model 標記 → 跟儀表板（走 is_vm_value）差 1,188 台
    （2026-09-18 實測 221：正典 3,308 VM、舊 SQL 只算 2,120）。要在 SQL 裡篩虛實就呼叫這支，
    不要再自己拼，才不會又漂走。col 參數給有表別名時用（如 h.is_vm）。"""
    # COALESCE 成 ''：欄位是 NULL 時，若不轉成空字串，整段 OR 會算成 NULL，外層 NOT(...) 也變
    # NULL → 實體機那一批（is_vm=0、device_model 空）會被三值邏輯整批濾掉（實測 P-1 消失）。
    v = f"UPPER(TRIM(CAST(COALESCE({is_vm_col}, '') AS TEXT)))"
    m = f"UPPER(TRIM(CAST(COALESCE({model_col}, '') AS TEXT)))"
    return (f"({v} IN ('1','VM','TRUE','是') OR {m} = 'VM' "
            f"OR {m} LIKE '(VM)%' OR {m} LIKE 'VM-%' OR {m} LIKE 'VM(%')")

# 唯讀最小權限帳號。這個常數只用來判斷「能不能走本機捷徑」，不是預設值——
# 兩者混用會出事，見 _runner_for 的說明。
#: 收集身分的名字。**所有平台同一個**（使用者 2026-09-23：「還是統一都叫 webit3sc」）。
#:
#: 為什麼是 8 個字元：AIX 的 sys0 `max_logname` 預設只給 8 個字元，`mkuser` 會直接
#: 拒絕更長的名字；放寬要 `chdev` **並重開機**，為了帳號名重開正式 AIX 不划算。
#: Linux 沒有這個限制，所以兩邊能共用的只有短的那個。
#:
#: ⚠️ 為什麼要統一成一個名字（2026-09-23 的教訓）：
#: 以前 Linux 用 webit3scan、AIX 用 webit3sc，於是每個呼叫端都必須記得帶 platform。
#: 四個收集器忘了帶 -> 全部拿 webit3scan 去登 AIX -> 那個帳號在 AIX 上不存在 ->
#: 每條指令都回空字串。而空字串的症狀跟「指令有問題」一模一樣，**查了一整天**。
#: 一個名字就沒有「忘了帶 platform」這回事——**把出錯的可能性拿掉，比記得帶參數可靠**。
READONLY_ACCOUNT = "webit3sc"
#: 保留這個名字是為了相容既有呼叫端，值跟上面相同（不再是另一個帳號）。
AIX_COLLECT_ACCOUNT = READONLY_ACCOUNT
#: 舊名。**只用在遷移提示**，不可以拿來當預設值——
#: 已納管的 Linux 主機上存在的是這個帳號，要一台一台重新納管才會換過來。
LEGACY_LINUX_ACCOUNT = "webit3scan"

# 收集身分預設值＝專用唯讀帳號（2026-08-16 定案，中間一度改成 sysinfra 又改回來）。
#
# 為什麼不用 sysinfra 這個現成的管理帳號（討論後定案的理由，記下來免得又繞回去）：
#   1. 稽核汙染：系統每天登入三千台，sysinfra 的登入紀錄全被機器流量塞滿，
#      真出事要查「誰凌晨三點登入」時，人跟系統分不開。
#   2. 汙染我們自己的資料：account_collector 會看管理帳號的最後登入，
#      我們天天登入會讓它永遠顯示活躍，那個稽核欄位就廢了。
#   3. 無法單獨撤銷：要停掉系統存取就得動 sysinfra，會影響真人作業。
# 專用帳號則是「刪一行 authorized_keys 就斷」，而且權限被綁死在唯讀白名單。
#
# 要改用 sysinfra 仍然可以（設定值換掉即可），唯一合理的情況是公司政策不准新增帳號。
DEFAULT_COLLECT_ACCOUNT = READONLY_ACCOUNT


# 每一態要人做什麼——畫面直接用這個，不要各頁自己編一套說法
NEXT_ACTION = {
    UNREGISTERED: "掃到了但沒登記，去「納入管理」把它建成資產",
    NOT_ONBOARDED: "已登記但收集帳號連不進去，在該機器執行納管腳本",
    ONBOARDED: "收集正常，無需處理",
    COLLECTED: "設備資料已收集（SAN 收集／離線匯入，非納管），定期再收一次保持最新即可",
    RETIRED: "資產狀態是停用／報廢／閒置，不需處理；記得 CIA 清冊也要同步，不然重匯會被蓋回去",
    EXEMPT: "人工標記為非納管設備（客製化系統／Oracle／廠商維護等），不需處理；情況改變可取消豁免",
    NOT_COVERED: "最近一次掃描沒有涵蓋這台所在的網段——它不是失聯，是沒掃過。去「納入管理」把那段加進掃描範圍",
    LOST: "登記在案但這次掃不到——確認是否關機、換 IP 或已下線",
    CONFLICT: "同一台有的登記標退役、有的仍使用中——到 CIA 確認哪一筆才對，系統不替人挑邊",
    RETIRED_ALIVE: "清冊標停用／報廢／閒置，這台卻還掃得到或收得到——確認是該關沒關（資安風險），還是清冊標錯",
    NO_IP: "登記了但沒有可掃描的 IP——掃描無從判斷它在不在。到 CIA 補 IP；是範本／KVM 等非主機就標非納管",
}


def ips_of(raw) -> list[str]:
    """IP 欄拆成可掃描的 IP 清單。CIA 常見一格填兩個（「a,b」「a／b」）；0.0.0.0 是佔位不算。"""
    import ipaddress
    import re as _re
    out = []
    for part in _re.split(r"[,;／/、\s]+", str(raw or "")):
        try:
            a = ipaddress.ip_address(part.strip())
        except ValueError:
            continue
        if not a.is_unspecified and str(a) not in out:
            out.append(str(a))
    return out


def classify(registered: bool, seen_in_scan: bool, collect_ok: int | None,
             collected: bool = False, retired: bool = False, exempt: bool = False,
             covered: bool = True, has_ip: bool = True) -> str:
    """把「有沒有登記／這次有沒有掃到／收集連不連得上」三個事實變成一個狀態。

    刻意寫成純函式（不碰 DB、不碰網路）——這是整個功能的判定核心，
    必須能被直接測到，不能藏在 SQL 或 API 裡。
    """
    if not registered:
        return UNREGISTERED
    # 退役優先：下線的機器掃不到、收不到都是正常的，不能再算成失聯或未納管
    if retired:
        # [B-07] 但標了退役卻還掃得到／收得到，不可以直接結案——「清冊寫報廢、實際還在跑」
        # 本身就是稽核發現（該關沒關，或清冊標錯），要列待辦
        if seen_in_scan or collect_ok == 1:
            return RETIRED_ALIVE
        return RETIRED
    # 已經收得到就顯示已納管——豁免是「不去納管它」，不是「不准收」。
    # 真的收到了卻還顯示「非納管設備」會讓人以為資料是假的。
    if exempt and collect_ok != 1:
        return EXEMPT
    # 已收集優先於失聯：SAN switch 多半在 OOB 網段、我們的掃描本來就看不到，
    # 但收集／匯入到它的資料本身就證明它在。已納管的（收得到）照舊走下面。
    if collected and collect_ok != 1:
        return COLLECTED
    if not seen_in_scan:
        # [B-09] 沒有 IP 的機器掃描本來就找不到它——那是「沒查」，不可以說成失聯
        if not has_ip:
            return NO_IP
        # 「沒掃過」不可以說成「失聯」（2026-09-16）：掃描範圍沒涵蓋到它，
        # 我們根本沒有證據說它在不在。要做的事也不同——是去把網段加進掃描範圍，
        # 不是去查機房有沒有關機。
        if not covered:
            return NOT_COVERED
        # 登記了卻掃不到＝失聯。就算它曾經收得到，現在人不在也是失聯，
        # 這比「已納管」更重要——顯示成已納管會讓人以為一切正常。
        return LOST
    if collect_ok == 1:
        return ONBOARDED
    # collect_ok 是 0（試過連不上）或 None（還沒試過）都算未納管：
    # 對使用者來說「還收不到」跟「還沒試」要做的事一樣——去把它納管起來。
    return NOT_ONBOARDED


def probe_uname(host: str, key_path: str | None = None,
                account: str | None = None, timeout: int = 8, runner=None) -> str | None:
    """問這台實際是什麼平台。回 `uname -s` 的輸出（AIX／Linux／SunOS…），問不到回 None。

    為什麼是 `uname -s`：**一個指令、免提權、Unix 類三個平台都有**，
    符合使用者 2026-09-22「不要裝軟體，指令都用內建的」。
    Windows 沒有它，所以問不到就是 None，**不可以因此推論成 Linux**。

    用途是**把答案寫到畫面上**，不是拿來自動改判定——
    使用者 2026-09-22：「畫面看不到的，就把它做到畫面上」，
    他不想被叫去跑 SQL 查 `hardware.os`。收集失敗時把這個值一起寫進納管紀錄，
    「送出平台 linux／實際 AIX」擺在一起，一眼就看得出是平台判錯。
    """
    if runner is not None:
        return runner(host)
    key_path = key_path or COLLECTOR_KEY_DEFAULT
    account = account or DEFAULT_COLLECT_ACCOUNT
    cmd = ["ssh", "-i", key_path, "-o", "BatchMode=yes", *_ssh_hostkey_opts(),
           "-o", f"ConnectTimeout={timeout}", f"{account}@{host}", "uname -s"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.SubprocessError:
        return None
    out = (r.stdout or "").strip().splitlines()
    return out[0].strip() if r.returncode == 0 and out and out[0].strip() else None


def probe_collect(host: str, key_path: str, account: str = DEFAULT_COLLECT_ACCOUNT,
                  timeout: int = 8, runner=None) -> tuple[bool, str | None]:
    """試連一台機器，看收集帳號通不通。回 (成功?, 失敗原因)。

    只跑一個無害的 `hostname`，不改目標機器任何東西。
    runner 可注入，測試不打真網路。
    """
    if runner is not None:
        return runner(host)
    cmd = [
        "ssh", "-i", key_path, "-o", "BatchMode=yes",
        *_ssh_hostkey_opts(), "-o", f"ConnectTimeout={timeout}",
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
                           runner=None, workers: int = 8,
                           only_ip: str | None = None) -> dict:
    """對「有 IP 的已登記資產」試連一次，把結果寫回 hardware。

    回 {"checked": n, "ok": n, "failed": n}。

    為什麼要存而不是每次現算：試連一台要好幾秒，8 台就十幾秒——
    畫面不能每次載入都等這個。存下來、由排程定期更新，畫面讀快取。
    所以每筆都帶 collect_checked_at，讓人知道這個結論是什麼時候的。

    `only_ip`：只重新試連這一個位址。**納管完成後一定要用這個**——
    剛納管一台就把整個機隊重測一遍，成本是 N²：納到第 100 台時，
    那一台要等前面 99 台跑完才會顯示成功（2026-08-28 實測 97～120 秒）。
    全機隊刷新是排程的事，不是單台納管的事。
    """
    from concurrent.futures import ThreadPoolExecutor
    from db import _now_local

    sql = "SELECT asset_serial, ip, os FROM hardware WHERE ip IS NOT NULL AND ip != ''"
    params: tuple = ()
    if only_ip:
        sql += " AND ip = ?"
        params = (only_ip,)
    rows = conn.execute(sql, params).fetchall()
    # 試連身分要跟平台走：AIX 上的收集帳號是 8 字元的短名（max_logname 限制），
    # 一律拿 webit3scan 去試連，那 8 台 AIX 會永遠停在「未納管」，而錯誤訊息只說
    # Permission denied，看不出是「名字對不上」而不是「金鑰沒佈」。
    targets = [(r["asset_serial"], r["ip"],
                get_collect_account(conn, collect_platform_of(
                    conn, r["ip"], r["os"], asset_serial=r["asset_serial"])))
               for r in rows]

    locals_ = local_ips()

    def work(item):
        serial, ip, account = item
        if runner is None and ip in locals_:
            # 本機不需要 SSH 帳號——直接跑一個指令確認收得到就好
            try:
                out = _local_runner()(ip, "hostname")
                return serial, bool(out.strip()), None
            except Exception as exc:  # noqa: BLE001
                return serial, False, f"本機收集失敗：{exc}"
        ok, err = probe_collect(ip, key_path, account=account, runner=runner)
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


def _exempt_serials(conn) -> set[str]:
    """人工標記的非納管設備。獨立一支方便測試注入，也避免 import 迴圈。"""
    import onboard_exempt

    return onboard_exempt.active_serials(conn)


def summarize(conn) -> dict:
    """全站四態統計＋每一台的狀態。畫面（儀表板四格、資產清單狀態欄）共用這一份，
    數字才不會跟清單對不上。"""
    from api import _latest_scan_time, _row_in_keys, _scan_keys, _scanned_alive_rows

    scan_time = _latest_scan_time(conn)
    scanned = _scanned_alive_rows(conn, scan_time)
    scan_ips, scan_hostnames = _scan_keys(scanned)

    # os_type 讓「已納管主機」表能按 Linux／Windows／AIX 二次篩選（使用者 2026-09-11：
    # 一千多台要補佈，得先挑出同一種 OS 一次跑）。重用漏斗那份分類，不另寫一套。
    from pipeline import _os_type
    import os_override
    _ov_map = os_override.active_map(conn)

    hw = conn.execute(
        "SELECT asset_serial, hostname, ip, os, collect_ok, collect_checked_at, collect_error, asset_status "
        "FROM hardware"
    ).fetchall()
    exempt_serials = _exempt_serials(conn)
    # 2026-09-21 使用者拍板：「OCP 管理放第二期，151 台要移出「要納管」，
    # 他第一期不納管他」。OCP 節點跑 RHCOS：系統區唯讀、本機帳號會在節點
    # 重佈時消失，要建帳號得改 MachineConfig（動整個節點池）——這條路本來就不該走。
    #
    # 判準沒自己再寫一份，直接用正典 onboard_eligibility.classify_os，而且**只取
    # immutable 這一種**：ESXi 與設備也在那支的不納管名單裡，但使用者這次拍的是 OCP，
    # 順手把別人也改掉等於自己擴大範圍，全站數字會在他不知情的情況下跳一大截。
    import onboard_eligibility as _oe

    def _ocp_node(os_text) -> bool:
        return _oe.classify_os(os_text)[0] == "immutable"
    # 這次掃描實際涵蓋了哪些網段。沒有紀錄時 covered_nets 是空的，
    # is_covered() 會一律回 True＝維持舊行為，不會在沒證據時把全站改標「未涵蓋」
    import scan_scope

    covered_nets = scan_scope.covered_networks(conn, scan_time)
    hw_ips = {r["ip"] for r in hw if r["ip"]}
    for r in hw:                      # 「a,b」兩個 IP 的，拆開後各自也算登記過
        hw_ips.update(ips_of(r["ip"]))
    hw_hostnames = {r["hostname"] for r in hw if r["hostname"]}

    # [B-09] 收集成功是「機器」的事實，不是某一筆登記的事實（2026-09-20 公司機數字健檢抓到）：
    # 同一台在清冊有多筆登記時，收集成功常只寫在其中一筆（B-05 之前留下的資料）。
    # 代表筆取「要處理的優先」，就會挑到沒收到那筆 → 一台收得到的機器顯示成「還沒納管」。
    # 公司 198.14：SECSVR198-011T／014T／015T 三台（跑這套系統的機器本身）就是這樣被算成未納管。
    # 這裡先把 collect_ok 收斂到「整台」，判定才跟「同一台只有一把尺」一致。
    import system_stats as _ss

    machine_ok = set()
    for r in hw:
        if r["collect_ok"] == 1:
            machine_ok.add(_ss.machine_key(r["hostname"], r["ip"], r["asset_serial"]))

    items = []
    # 收集過資料的設備（SAN switch 收集／離線匯入）。表可能還沒建（舊 DB）就當沒有
    try:
        collected_ips = {r[0] for r in conn.execute("SELECT ip FROM san_switch").fetchall() if r[0]}
    except Exception:  # noqa: BLE001
        collected_ips = set()

    for r in hw:
        _ips = ips_of(r["ip"])
        seen = _row_in_keys(r, scan_ips, scan_hostnames) or any(i in scan_ips for i in _ips)
        # 同一台只要有一筆收得到，整台就算收得到（見上面 machine_ok 那段）
        _ok = 1 if _ss.machine_key(r["hostname"], r["ip"], r["asset_serial"]) in machine_ok else r["collect_ok"]
        state = classify(True, seen, _ok, collected=(r["ip"] in collected_ips),
                         retired=((r["asset_status"] or "").strip() in RETIRED_STATUS),
                         exempt=(r["asset_serial"] in exempt_serials or _ocp_node(r["os"])),
                         # 多個 IP：任一個在涵蓋網段內就算涵蓋
                         covered=any(scan_scope.is_covered(i, covered_nets) for i in _ips) if _ips else True,
                         has_ip=bool(_ips))
        items.append({
            "asset_serial": r["asset_serial"], "hostname": r["hostname"], "ip": r["ip"],
            "state": state, "collect_checked_at": r["collect_checked_at"],
            "collect_error": r["collect_error"],
            # 人工指定優先（os_override），跟漏斗同一套
            "os_type": os_override.resolve(_ov_map, r["hostname"], r["ip"], r["asset_serial"],
                                           _os_type(r["os"], None))[0],
        })

    # 掃到但沒登記的＝未登記，它們還不在 hardware 裡，要從掃描側補進來
    for r in scanned:
        if not r["ip"] and not r["hostname"]:
            continue
        if _row_in_keys(r, hw_ips, hw_hostnames):
            continue
        _ports = r["open_ports"] if "open_ports" in r.keys() else None
        items.append({
            "asset_serial": None, "hostname": r["hostname"], "ip": r["ip"],
            "state": UNREGISTERED, "collect_checked_at": None, "collect_error": None,
            "os_type": _os_type(None, _ports),   # 未登記沒 os 字串，只能靠掃到的埠推
        })

    # ⚠️ counts 一律「逐台」算，不是逐筆——2026-09-17 使用者：「檢視每一個公式，確保不會
    # 因重複而有低級錯誤」。首頁四格／狀態統計吃這份 counts；若逐筆算，同一台登記多筆會被
    # 灌水，跟納管漏斗（逐台）打架（實測失聯 4641 筆 vs 4226 台）。去重鍵用正典 machine_key，
    # 每台取代表（最小序號，跟 system_stats.collapse_machines 同一條規則）的狀態。
    # items 仍逐筆保留：資產清單狀態欄要一列一筆各自的狀態。
    import system_stats
    groups: dict[str, list] = {}
    for it in items:
        groups.setdefault(
            system_stats.machine_key(it["hostname"], it["ip"], it["asset_serial"]), []).append(it)
    counts = {s: 0 for s in ALL_STATES}
    for g in groups.values():
        # [B-04] 跟納管漏斗同一條規則（以前取最小序號的狀態，跟漏斗差 11 台）：
        # 有退役也有使用中 → 登記矛盾；否則要處理的優先，再取最小序號
        states = {x["state"] for x in g}
        _ret = {RETIRED, RETIRED_ALIVE}   # [B-07] 退役但仍在線也是「標退役」
        if len(g) > 1 and (states & _ret) and (states - _ret):
            counts[CONFLICT] += 1
            continue
        rep = min(g, key=lambda x: (0 if x["state"] in NEEDS_ACTION_STATES else 1, x["asset_serial"] or ""))
        counts[rep["state"]] += 1

    return {
        "scan_time": scan_time,
        "counts": counts,
        "total_known": sum(counts.values()),
        # 有問題幾台＝要人處理的狀態加總（已納管、已收集都不算）
        "needs_action_total": sum(counts[s] for s in NEEDS_ACTION_STATES),
        "ok_total": counts[ONBOARDED] + counts[COLLECTED],
        "retired_total": counts[RETIRED],
        "exempt_total": counts[EXEMPT],
        "not_covered_total": counts[NOT_COVERED],
        # 掃描涵蓋了幾段：0 段＝這次判定沒有涵蓋資訊（維持舊行為），畫面要講清楚
        "covered_segments": len(covered_nets),
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




def get_collect_account(conn, platform: str = "linux") -> str:
    """讀目前設定的遠端收集身分。沒設就回該平台的預設值。

    AIX 為什麼是另一個設定值（2026-08-16 定案）：AIX 的 sys0 `max_logname` 預設 9
    （可用 8 個字元），而 webit3scan 是 10 個字元，`mkuser` 直接拒絕；放寬要 chdev
    **並重開機**——為了帳號名重開正式 AIX 不划算。所以同一個收集身分在 AIX 上用
    8 字元的名字。該環境若已放寬過，把這個設定值改成 webit3scan 就完全一致。
    """
    from db import get_setting

    # 兩個設定鍵都保留：既有環境已經各自設好值，直接忽略它們會讓那些機器立刻收不到。
    # 但**預設值現在兩邊相同**，所以新環境不會再長出兩個名字。
    if platform == "aix":
        return get_setting(conn, "collect_ssh_account_aix", AIX_COLLECT_ACCOUNT) \
            or AIX_COLLECT_ACCOUNT
    return get_setting(conn, "collect_ssh_account", DEFAULT_COLLECT_ACCOUNT) \
        or DEFAULT_COLLECT_ACCOUNT


def collect_account_migration(conn) -> dict | None:
    """這個環境還在用舊的 Linux 帳號名嗎？要遷移的話回一句話，已經統一就回 None。

    **不自動幫他改設定**：已納管的 Linux 主機上存在的是舊帳號，
    設定一翻過去那些機器就立刻全部收不到——那是把一個假象換成一次真的停擺。
    遷移要先重新納管（把新帳號佈上去），確認收得到之後再翻設定。
    """
    from db import get_setting

    cur = get_setting(conn, "collect_ssh_account", DEFAULT_COLLECT_ACCOUNT) \
        or DEFAULT_COLLECT_ACCOUNT
    aix = get_setting(conn, "collect_ssh_account_aix", AIX_COLLECT_ACCOUNT) \
        or AIX_COLLECT_ACCOUNT
    if cur == aix:
        return None
    return {
        "linux": cur, "aix": aix, "target": READONLY_ACCOUNT,
        "text": (f"收集帳號目前兩個平台不同名（Linux `{cur}`／AIX `{aix}`）。"
                 f"已定案統一成 `{READONLY_ACCOUNT}`，但**還沒遷移**——"
                 "已納管的 Linux 主機上只有舊帳號，現在把設定翻過去它們會立刻全部收不到。"
                 "正確順序：重新納管（把新帳號佈上去）→ 確認收得到 → 才翻設定。"),
    }


#: 虛擬化的稱呼。**這不是措辭潔癖，是技術上不同的東西**
#: （使用者 2026-09-22：「aix 不能說是 vm 要說 lpar」）：
#:   * VM   = VMware/Hyper-V 那種，管理端是 vCenter
#:   * LPAR = IBM Power 由 PowerVM 切出來的邏輯分區，管理端是 HMC
#: 對 LPAR 講「未在 vCenter 盤點資料內」是廢話 -- 它本來就不會在 vCenter 裡，
#: 而且會讓人以為是資料缺漏，跑去追一筆永遠不會出現的資料。
VIRT_VM = "VM"
VIRT_LPAR = "LPAR"


def virt_kind(os_text=None, is_vm_val=None, device_model=None, uname_l=None):
    """這台的虛擬化該叫什麼：``LPAR`` / ``VM`` / None（實體或判不出來）。

    判斷順序：

    1. ``uname -L`` 有 LPAR 編號 -> LPAR。這是**硬體平台的事實，跟作業系統無關**，
       所以 Linux on Power 也會判成 LPAR，那是對的。只對 AIX 特判的話，
       哪天 Linux on Power 進來又會顯示成 VM，等於留一樣的坑。
    2. 平台是 AIX -> LPAR。AIX 只跑在 Power 上，不會是 VMware 的 VM。
    3. 其餘走既有的 is_vm 判定 -> VM。

    ``uname -L`` 輸出是「<LPAR 編號> <LPAR 名稱>」，非 LPAR 環境回 ``-1 NULL``，
    所以編號 -1 或 NULL 就不是 LPAR。
    """
    t = (uname_l or "").strip()
    if t and not t.startswith("-1") and "null" not in t.lower():
        return VIRT_LPAR
    if platform_from_os(os_text) == "aix":
        return VIRT_LPAR
    if is_vm_value(is_vm_val, device_model):
        return VIRT_VM
    return None


def platform_from_os(os_text: str | None) -> str | None:
    """從 OS 字串判平台。判不出來回 None——**不可以預設 linux**。

    只認明確講得出平台的字樣。認不得就交給下一層（同台其他登記／uname -s／埠號），
    在這裡硬猜等於把「不知道」變成「知道錯的」。
    """
    t = (os_text or "").strip().lower()
    if not t:
        return None
    if "aix" in t:
        return "aix"
    # ⚠️ 2026-09-22：AIX 納管成功之後，facts_collector 會把 os 寫成
    # `oslevel -s` 的輸出——`7200-05-09-2446`。**那串裡面沒有 "aix" 三個字**，
    # 於是全站所有 `"aix" in os` 的判斷在「收集成功之後」反而全部失效，
    # 收集器改拿 Linux 指令去問 AIX → 軟體／服務／帳號／硬體全部 0。
    #
    # 這是自己造成的連鎖：納管壞著的時候 os 是空的（靠同台其他登記判得出 AIX），
    # 納管修好之後 os 有值了，反而判錯。**「有資料」比「沒資料」更糟**的典型。
    #
    # oslevel -s 的格式是 VVRR-TL-SP-YYWW（4-2-2-4 位數字），例如 7200-05-09-2446。
    # 這個格式只有 AIX 有，認它是安全的。
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{4}", t):
        return "aix"
    if "windows" in t or "microsoft" in t:
        return "windows"
    for k in ("linux", "red hat", "redhat", "rhel", "centos", "rocky", "ubuntu",
              "debian", "suse", "oracle linux"):
        if k in t:
            return "linux"
    return None


def collect_platform_of(conn, ip: str, os_val: str | None = None,
                        asset_serial: str | None = None, probe=None) -> str:
    """決定收集時要用哪一組平台指令。

    ## 順序（2026-09-22 大改，順序本身就是修法）

    1. **這一筆自己的 `os`**
    2. **同一台其他登記的 `os`** ← 新增，而且這條才是真正修好 AIX 的那一條
    3. **`uname -s` 實問** ← 全部登記都沒有 os 時的最後手段
    4. 開放埠猜（既有行為，Linux／Windows 照舊）

    ## 為什麼第 2 條是關鍵

    使用者 2026-09-22 的納管漏斗截圖顯示：8 台 AIX **每一台都標「同台 2 筆」**，
    而且每一筆的 `作業系統` 都是 `AIX 7.2`。但他按納管時選到的是
    `DYN-` 前綴那筆（存活掃描產生的合成資產，`os` 是空的）——
    於是判成 linux、拿 `/etc/os-release` 去問 AIX、每個欄位都空。

    **這台機器的 AIX 身分系統早就知道，只是記在另一筆登記上。**
    B-03／B-13 那條「同一台只有一份定義」的規則，納管這條路徑沒有遵守。
    先看同一台的所有登記，比多跑一個 SSH 指令便宜，也順便修好
    漏斗上那 5 台卡在「已登記進不去」的。

    ## 為什麼第 4 條保留

    埠號猜是既有行為（3389→windows，其餘 linux）。**不改它**——
    Linux 只開 22 仍要判 linux，改了會動到全機隊。它只是最後的退路，
    前面三層都問不出來才會走到。

    原始問題：原本只看 `os_val`，判不出來就直接掉到埠號猜，AIX 因此
    永遠被當成 Linux——「連得上、但每個欄位都空的」，跟權限不足長得一模一樣。
    """
    import facts_collector

    # 1. 這一筆自己的 os
    got = platform_from_os(os_val)
    if got:
        return got

    # 2. 同一台其他登記的 os（**這條是 2026-09-22 的主修**）
    if asset_serial:
        try:
            for sib in same_machine_serials(conn, asset_serial):
                if sib == asset_serial:
                    continue
                row = conn.execute("SELECT os FROM hardware WHERE asset_serial = ?",
                                   (sib,)).fetchone()
                got = platform_from_os(row["os"] if row else None)
                if got:
                    return got
        except sqlite3.Error:
            pass
    else:
        # 沒給序號時退而求其次：同 IP 的其他登記（同一台的最常見情形）
        try:
            for row in conn.execute(
                    "SELECT os FROM hardware WHERE trim(ip) = ? AND COALESCE(os,'') <> ''",
                    ((ip or "").strip(),)):
                got = platform_from_os(row["os"])
                if got:
                    return got
        except sqlite3.Error:
            pass

    # 3. 全部登記都沒有 os → 實問一次。一個指令、免提權、Unix 三平台都有
    actual = probe_uname(ip, runner=probe) if probe is not None else probe_uname(ip)
    got = platform_from_os(actual)
    if got:
        return got

    # 4. 最後才是埠號猜（既有行為，不動）
    row = conn.execute(
        "SELECT open_ports FROM scan_history WHERE ip = ? AND scan_ok = 1 "
        "ORDER BY scan_time DESC LIMIT 1", (ip,)).fetchone()
    ports = [int(p) for p in (row["open_ports"] or "").split(",") if p.strip().isdigit()] \
        if row and row["open_ports"] else []
    return facts_collector.detect_platform(ports)


def collect_platform_for(conn, ip: str, asset_serial: str | None = None, probe=None) -> str:
    """跟 :func:`collect_platform_of` 同一套，差在**呼叫端沒有 os 字串在手**時由這裡去查。

    ⚠️ 2026-09-22：加這支的理由是「手抄本」。原本 service_inventory、
    account_inventory、auto_onboard 各自寫了一份「查最近一次掃描的開放埠 -> 猜平台」，
    **三份都完全不看 os 欄位**。於是：

    * AIX 只開 22 -> 埠號猜成 linux
    * 服務盤點拿 ``ss -tlnp`` 去問 AIX -> 收到 0 筆
    * 軟體盤點拿 ``rpm -qa`` 去問 AIX -> 收到 0 筆
    * 帳號盤點 ``if platform != "linux": continue`` -> 這台直接被跳過，**而且沒留下任何理由**

    使用者看到的就是「軟體 0／服務 0／帳號 0」。**0 不是事實，是我們問錯了指令。**
    更糟的是三個 0 長得跟「這台真的什麼都沒有」一模一樣。

    所以平台判定只留一份定義（B-03／B-13 的老規則），這裡負責把 os 補上再交給正典。
    """
    os_val = None
    try:
        if asset_serial:
            row = conn.execute(
                "SELECT os FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
            os_val = row["os"] if row else None
        if not os_val and ip:
            row = conn.execute(
                "SELECT os FROM hardware WHERE ip = ? AND os IS NOT NULL AND os != '' "
                "ORDER BY collect_ok DESC LIMIT 1", (ip,)).fetchone()
            os_val = row["os"] if row else None
    except Exception:       # noqa: BLE001 - 查不到就交給下一層判，不要因此整個收集掛掉
        os_val = None
    return collect_platform_of(conn, ip, os_val, asset_serial=asset_serial, probe=probe)

def _runner_for(ip: str, key_path: str, account: str = DEFAULT_COLLECT_ACCOUNT):
    """收集一律走 SSH，**連收集器自己也一樣**（SSH 到自己）。

    以前有「本機捷徑」：收自己時直接以跑服務的帳號在本機執行。結果收集身分跟別台
    不一樣——221 的 API 跑在 sysctl 底下，sysctl 沒有 sudo，帳號盤點 85 條需 root
    的欄位全查不到，別台卻是 webit3scan＋白名單（2026-09-11 實測）。
    而服務帳號會換（使用者 2026-09-11：「sysctl 以後沒有，只有 sysinfra」），
    讓盤點結果跟著它變是不對的。使用者選 3a：收自己也用收集帳號 SSH 進來。

    前提：收集公鑰要授權進本機收集帳號的 authorized_keys，from= 要包含本機位址
    （與遠端同一套納管腳本；221 已實測可連）。連不上會以 SSH 錯誤如實記在失敗清單，
    不會退回本機捷徑——退回去就又是兩種身分。
    """
    return _ssh_runner(key_path, account=account)


#: 每台最多留幾條指令樣本。夠看出「是連線還是指令」就好，
#: 三千台各留全部會把 DB 灌爆，而且沒有人會去看第 40 條。
TRACE_SAMPLES = 8
#: stderr 截斷長度。SSH 的錯誤訊息前兩行就講完了，後面是 debug 雜訊。
TRACE_ERR_MAX = 600


class SSHRunner:
    """收集用的 SSH runner，**同時記錄這一跳的事實**。

    ⚠️ 2026-09-22：原本這裡是 `return r.stdout` 一行，**離開碼與 stderr 直接丟掉**。
    於是所有收集器看到的都只是空字串，只能推論「指令沒跑起來或被擋」。

    真機第一次驗證就卡在這：AIX 上 `lslpp -Lc`、`/etc/passwd`、`lsdev -Cc adapter`
    三個同時空。這三件事性質完全不同，不可能各自失敗——但我們**證明不了**是
    「SSH 沒連上」還是「連上了但指令不存在」，因為證據在 `r.stderr` 裡，而它被丟了。

    這個類別呼叫方式跟原本的函式完全一樣（`runner(host, cmd) -> str`），
    多出來的是 `save(conn, asset_serial, host)`：把這次的事實落庫給畫面看。
    """

    def __init__(self, key_path: str, account: str = DEFAULT_COLLECT_ACCOUNT,
                 timeout: int = 10):
        self.key_path = key_path
        self.account = account
        self.timeout = timeout
        #: {host: {"connected": bool|None, "transport_error": str|None, "samples": [...]}}
        self.trace: dict = {}

    def _rec(self, host: str) -> dict:
        return self.trace.setdefault(host, {
            "connected": None, "transport_error": None, "samples": [],
            "total": 0, "ok": 0, "empty": 0, "failed": 0,
        })

    def __call__(self, host: str, cmd: str) -> str:
        import time

        rec = self._rec(host)
        t0 = time.time()
        argv = ["ssh", "-i", self.key_path, "-o", "BatchMode=yes", *_ssh_hostkey_opts(),
                "-o", f"ConnectTimeout={self.timeout}", f"{self.account}@{host}", cmd]
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=self.timeout + 10)
            rc, out, err = r.returncode, r.stdout or "", (r.stderr or "").strip()
        except subprocess.TimeoutExpired:
            rc, out, err = None, "", f"逾時（{self.timeout + 10} 秒內沒有回應）"
        except OSError as exc:
            rc, out, err = None, "", f"{type(exc).__name__}: {exc}"

        rec["total"] += 1
        if rc == 0:
            # 離開碼 0 = SSH 這一跳通了，而且遠端 shell 跑完了這條指令。
            # 這一點很關鍵：有了它，「輸出是空的」才能確定是**指令真的沒東西可回**，
            # 而不是根本沒跑到。
            rec["connected"] = True
            rec["ok" if out.strip() else "empty"] += 1
        else:
            rec["failed"] += 1
            # SSH 自己失敗（255）或連不上，跟「遠端指令回非 0」要分開。
            # ssh(1) 用 255 表示自己出錯；遠端指令的離開碼會原樣帶回來。
            if rc in (255, None) and rec["connected"] is not True:
                rec["connected"] = False
                if not rec["transport_error"]:
                    rec["transport_error"] = err[:TRACE_ERR_MAX] or f"ssh 離開碼 {rc}"

        if len(rec["samples"]) < TRACE_SAMPLES:
            rec["samples"].append({
                "cmd": cmd[:400],
                "exit_code": rc,
                "out_len": len(out),
                # 輸出開頭留一點：格式跟我們假設的不一樣時，這一段就是證據
                "out_head": out[:200],
                "stderr": err[:TRACE_ERR_MAX],
                "ms": int((time.time() - t0) * 1000),
            })
        return out

    def save(self, conn, asset_serial: str, host: str) -> None:
        """把這台的收集事實落庫。**收集全失敗的時候也要寫**——那才是最需要看的時候。"""
        import json

        from db import _now_local

        rec = self.trace.get(host)
        if not rec:
            return
        conn.execute(
            "INSERT INTO collect_ssh_trace (asset_serial, ip, account, key_path, connected, "
            "transport_error, cmd_total, cmd_ok, cmd_empty, cmd_failed, samples, collected_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(asset_serial) DO UPDATE SET ip=excluded.ip, account=excluded.account, "
            "key_path=excluded.key_path, connected=excluded.connected, "
            "transport_error=excluded.transport_error, cmd_total=excluded.cmd_total, "
            "cmd_ok=excluded.cmd_ok, cmd_empty=excluded.cmd_empty, "
            "cmd_failed=excluded.cmd_failed, samples=excluded.samples, "
            "collected_at=excluded.collected_at",
            (asset_serial, host, self.account, self.key_path,
             None if rec["connected"] is None else int(rec["connected"]),
             rec["transport_error"], rec["total"], rec["ok"], rec["empty"], rec["failed"],
             json.dumps(rec["samples"], ensure_ascii=False), _now_local()),
        )
        conn.commit()


def trace_verdict(row) -> dict:
    """一列 collect_ssh_trace -> 給畫面的一句結論＋依據。

    這支的存在理由：**推論要跟證據分開標示。** 畫面上原本那些「AIX 不可能零
    fileset，所以是收集失敗」是收集器的推論（對，但那是推論）；
    這裡回的 `verdict` 是從離開碼直接讀出來的事實。
    """
    if row is None:
        return {"verdict": "no_data",
                "text": "這台還沒有收集連線紀錄（沒按過收集，或這版之前收的）。"}
    connected = row["connected"]
    if connected == 0:
        return {"verdict": "no_ssh",
                "text": ("**SSH 根本沒連上**，所以每一類都是空的——"
                         f"這不是指令的問題。原始訊息：{row['transport_error'] or '（無）'}")}
    if connected is None:
        return {"verdict": "not_tried",
                "text": "這一輪沒有對這台發出任何 SSH 指令（不在收集清單內）。"}
    failed, empty, ok = row["cmd_failed"] or 0, row["cmd_empty"] or 0, row["cmd_ok"] or 0
    if failed and not ok:
        return {"verdict": "cmd_failed",
                "text": (f"SSH 連得上（有指令回離開碼 0），但 {failed} 條指令回非 0——"
                         "**是指令本身跑不起來**（不存在、被擋、語法不合這台的 shell）。"
                         "下面的 stderr 是原文。")}
    if empty and not ok:
        return {"verdict": "all_empty",
                "text": ("SSH 連得上、指令也都跑完了（離開碼 0），但**每一條的輸出都是空的**。"
                         "這代表遠端真的沒回東西，不是沒跑到——要往權限或輸出格式查。")}
    return {"verdict": "ok",
            "text": f"SSH 正常：{ok} 條有輸出、{empty} 條空、{failed} 條失敗。"}


def _ssh_runner(key_path: str, account: str = DEFAULT_COLLECT_ACCOUNT, timeout: int = 10):
    """給 facts_collector 用的 runner：runner(host, cmd) -> str。

    回的是 `SSHRunner`（可呼叫），呼叫方式跟以前一樣；多了 `.save()` 可落診斷。
    """
    return SSHRunner(key_path, account=account, timeout=timeout)


# facts 收到的欄位 → hardware 的欄位。只寫「機器裡的事實」，不碰業務欄位
# （資產用途、保管者那些是人填的，收集不該覆蓋掉人的輸入）。
FACT_FIELDS = ("hostname", "os", "device_model", "hw_serial", "mac", "is_vm")



def _save_trace(conn, run, serial: str, ip: str) -> None:
    """收集這一跳的 SSH 事實落庫。注入 runner 的測試沒有 save()，先問過再叫。"""
    if not hasattr(run, "save"):
        return
    try:
        run.save(conn, serial, ip)
    except Exception:  # noqa: BLE001 - 診斷寫不進去不可以反過來害收集失敗
        pass


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

    sql = ("SELECT asset_serial, ip, os FROM hardware "
           "WHERE ip IS NOT NULL AND ip != '' AND collect_ok = 1")
    if only_serial:
        sql += " AND asset_serial = ?"
        rows = conn.execute(sql, (only_serial,)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()

    updated, failed = 0, []
    for r in rows:
        # 平台：先看資產上已知的真 OS（AIX 只有這裡看得出來），沒有才退回埠號推測。
        # facts_collector 各平台指令集完全不同，判錯的症狀是「連得上但欄位全空」。
        # 帶 asset_serial：這樣判平台時看得到**同一台其他登記的 os**
        # （2026-09-22：DYN- 合成資產 os 是空的，但 CIA 那筆寫著 AIX 7.2）
        platform = collect_platform_of(conn, r["ip"], r["os"] if "os" in r.keys() else None,
                                       asset_serial=r["asset_serial"])
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
        # AIX 的收集帳號名不同（max_logname 上限），SSH 身分要跟著平台走，
        # 否則會拿 webit3scan 去登一台上面只有 webit3sc 的機器，永遠 Permission denied
        # 每台一個收集帳號（hardware.collect_account，NULL 就走全域）——
        # 2026-09-24 遷移用；全是 NULL 時行為跟以前一模一樣。
        import collect_account_migration as _cam
        run = runner or _runner_for(
            r["ip"], key_path,
            account=_cam.account_for_host(conn, r["asset_serial"], platform))
        try:
            facts = facts_collector.collect(run, r["ip"], platform)
        except Exception as exc:  # noqa: BLE001
            failed.append({"asset_serial": r["asset_serial"], "error": str(exc)[:200]})
            _save_trace(conn, run, r["asset_serial"], r["ip"])
            continue
        _save_trace(conn, run, r["asset_serial"], r["ip"])
        setters = {k: v for k, v in facts.items() if k in FACT_FIELDS and v not in (None, "")}
        if not setters:
            # 「收不到任何欄位」以前就到此為止，沒有人知道為什麼。
            # 現在把 SSH 那層的結論接上去——是沒連上、還是連上了但指令跑不起來。
            row = conn.execute("SELECT * FROM collect_ssh_trace WHERE asset_serial = ?",
                               (r["asset_serial"],)).fetchone()
            why = trace_verdict(row).get("text", "")
            failed.append({"asset_serial": r["asset_serial"],
                           "error": f"收不到任何欄位。{why}"[:400]})
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
#
# Linux 進一步拆成 RHEL/CentOS/Debian/Oracle Linux 四個叫得出名字的大宗＋
# 「Linux(其他)」（Rocky/Ubuntu/SUSE/Fedora/CoreOS…）——原本全部歸單一個「Linux」
# 太籠統，這幾個發行版的資安支援週期、修補節奏都不一樣，混在一起看不出真正的組成
# （使用者 2026-08-11 要求）。⚠️ 順序有意義：具體家族要排在「linux」這種籠統
# 關鍵字前面，且 RHEL 要用完整字樣（不能只用「red hat」），否則會連 Red Hat CoreOS
# 也一起吃進來——CoreOS 不是 RHEL，屬於「其他」。
_PLATFORM_RULES = (
    ("Windows", ("windows", "win server", "microsoft")),
    # 使用者 2026-08-13 實際發現：資料庫裡實際寫法是「RedHat 8.5」「Redhat9.4」這種
    # 業界慣用縮寫（甚至連空格都省略），完整字樣「red hat enterprise linux」比對
    # 不到，527+ 台清清楚楚是 RedHat 的機器全部悄悄掉進「未知」。加「redhat」
    # （無空格）安全——CoreOS 原始值是「Red Hat CoreOS」有空格，不會被誤吃。
    ("RHEL", ("red hat enterprise linux", "rhel", "redhat")),
    ("CentOS", ("centos",)),
    ("Debian", ("debian",)),
    ("Oracle Linux", ("oracle linux",)),
    ("Linux(其他)", ("linux", "rocky", "ubuntu", "suse", "fedora", "coreos", "rhcos", "alma")),
    ("AIX/Unix", ("aix", "solaris", "hp-ux", "unix")),
    # 使用者 2026-08-13 實際發現：原本完全沒收 VMware ESXi 關鍵字。
    ("VMware ESXi", ("esxi", "vsphere", "vmware")),
    # IBM i（舊稱 OS/400）：device_model 常寫「IBM AS400」，不是 os 欄位本身講清楚。
    ("IBM i", ("as400", "ibm i", "os/400", "i5/os")),
    # 使用者 2026-08-13 實際發現：原本只認 switch/router/ios/junos 這幾個英文單字，
    # 完全不認廠牌名——「Cisco C9200L」「Aruba AP 515」「Fortinet FG-61F」「ATEN
    # SN0116A」「VoiceGateway」這種 device_model 寫法認不出來，補上常見廠牌名/產品名。
    ("網路設備", ("網路設備", "switch", "router", "ios", "junos",
              "cisco", "aruba", "fortinet", "juniper", "palo alto", "forcepoint",
              "aten", "voicegateway", "voice gateway", "f5", "big-ip", "big ip",
              # 2026-09-18 使用者：「是不是還要一個網路設備，switch f5 等」——221 上仍掉在
              # 「未知」的網路設備：Paloalto（無空格）、只寫型號的 FG-／WS-C（Fortinet／Cisco）、
              # PulseSecure VPN、ixia TAP、Riverbed。Windows VM 上的 Riverbed 軟體不受影響：
              # 這支先看 OS 欄，Windows 規則排第一、命中就停。
              "paloalto", "pulsesecure", "pulse secure", "ixia", "riverbed", "ws-c", "fg-")),
    # 使用者 2026-08-13 要求繼續縮小「未知」：iDRAC／Unisphere Central 這類跟硬體
    # 綁死的管理韌體（BMC），不是主機作業系統，但也不該永遠掉進「未知」——原本這裡
    # 完全沒收「idrac」這個字，儘管 EOS 頁那邊早就用 HW_ROUTED_PRODUCTS 認得出來，
    # 平台判定卻是完全獨立的一套規則（manage_state.platform_of()），沒接到那套邏輯。
    ("管理韌體(BMC)", ("idrac", "drac", "ilo", "unisphere central")),
    # 儲存設備：EMC／IBM FlashSystem／Storwize／SVC／Avamar 這類，同樣不是「主機
    # 作業系統」，但也不是「網路設備」，獨立一類比較準確，比通通塞進「未知」有意義。
    ("儲存設備", ("儲存設備", "flashsystem", "storwize", "emc storage",
              "san switch", "avamar", "vplex", "unity")),
)


def platform_of(*candidates: str | None) -> str:
    """依序看幾個線索字串，回平台大類。都認不出來回「未知」。

    刻意接受多個候選：優先用 facts 收到的真 OS，收不到才退回掃描的 os_guess——
    真資料永遠優先於推測，但推測比空白有用。

    ⚠️ 這是純關鍵字比對，覆蓋率遠不如 normalize_os()——「9.1.9」這種裸版本號、
    要靠設備型號反推的情況，這支函式看不懂。有 os 欄位真值時優先用
    platform_of_from_os()，這支只當它判斷不出來時的最後備援。
    """
    for text in candidates:
        if not text:
            continue
        low = str(text).lower()
        for label, keys in _PLATFORM_RULES:
            if any(k in low for k in keys):
                return label
    return "未知"


def platform_of_from_os(os_val, device_model, conn, guess_os: str | None = None) -> str:
    """使用者 2026-08-13 實際發現：首頁平台統計原本只用 platform_of() 這套土砲關鍵字，
    跟 normalize_os() 那套已經很成熟、覆蓋率高很多的判斷完全脫節——「9.1.9」這種
    裸版本號 normalize_os() 能靠設備型號反推出「Palo Alto PAN-OS 9.1.9」，
    platform_of() 卻認不出來，兩邊各管各的。

    這支函式優先用 normalize_os() + os_platform_bucket()（見 normalize.py 說明），
    判不出來才退回舊的關鍵字比對（platform_of()）當備援，兩層合起來覆蓋率才是真正
    做得到的上限。
    """
    import normalize

    has_real_os = bool(os_val) and str(os_val).strip().upper() != "N/A"
    if has_real_os:
        info = normalize.normalize_os(os_val, conn, device_model)
        bucket = normalize.os_platform_bucket(info["product"], info["canonical"])
        if bucket:
            return bucket
    return platform_of(os_val, guess_os, device_model)


_LOC_CFG: dict | None = None


def _location_config() -> dict:
    """讀機房分組設定（location_groups.json），讀不到就退回「只有分公司」的保守設定。

    設定檔壞掉或不見時寧可全部歸成一組，也不要讓整個儀表板 500。
    """
    global _LOC_CFG
    if _LOC_CFG is None:
        import json
        import pathlib

        p = pathlib.Path(__file__).with_name("location_groups.json")
        try:
            _LOC_CFG = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _LOC_CFG = {"groups": [], "fallback": "分公司", "_empty_label": "未填"}
    return _LOC_CFG


def group_location(raw: str | None) -> str:
    """把 physical_location 原值歸到主要據點，其餘一律 fallback。

    比對用「包含」而非完全相等：資料裡是「01_板橋機房」「板橋IDC」這類前後綴混雜的寫法，
    要求完全相等等於全部落到 fallback，分組就失去意義。
    """
    cfg = _location_config()
    v = (raw or "").strip()
    if not v:
        return cfg.get("_empty_label", "未填")
    for g in cfg.get("groups", []):
        for kw in g.get("match", []):
            if kw and kw in v:
                return g.get("name", kw)
    return cfg.get("fallback", "分公司")


_ENV_CFG: dict | None = None


def _environment_config() -> dict:
    """讀環境別分組設定（environment_groups.json）。壞掉就退回「不分組」。

    退回時是原值照用（groups 空 → 一律 fallback… 不，見下），寧可多幾列也不要 500。
    """
    global _ENV_CFG
    if _ENV_CFG is None:
        import json
        import pathlib

        p = pathlib.Path(__file__).with_name("environment_groups.json")
        try:
            _ENV_CFG = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _ENV_CFG = {}
    return _ENV_CFG


def group_environment(raw: str | None) -> str:
    """把 environment 原值歸組。跟 group_location 同一套邏輯與理由。

    設定檔讀不到時**回傳原值**而不是丟進 fallback——機房那邊全歸「分公司」還看得懂，
    環境別全歸「其他」等於整張交叉表變成一列，不如原樣列出來至少資訊沒少。
    """
    cfg = _environment_config()
    v = (raw or "").strip()
    if not v:
        return cfg.get("_empty_label", "未填")
    groups = cfg.get("groups") or []
    if not groups:
        return v
    for g in groups:
        for kw in g.get("match", []):
            if kw and kw in v:
                return g.get("name", kw)
    return cfg.get("fallback", "其他")


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
        "SELECT ip, hostname, os, device_model, is_vm, environment, asset_status, physical_location, "
        "asset_serial FROM hardware"
    ).fetchall()

    def bump(d, k):
        d[k] = d.get(k, 0) + 1

    import system_report as _sr_ob   # 函式內 import：避開 module 載入期的循環

    def _is_offbook(serial) -> bool:
        # 帳外＝掃到/vCenter 收到、但沒登記在 CIA 的（序號前綴 DYN-/VC-/AUTO-）。
        # 一律走正典 system_report.is_off_book，別再各寫一份（前綴一改就漂走）。
        return _sr_ob.is_off_book(serial)

    import normalize

    by_platform, by_env, by_status, by_virt = {}, {}, {}, {}
    by_os, by_model, by_location = {}, {}, {}
    registered_active = 0   # 有效資產中「正式登記」的筆數（排除帳外）——跟公司清冊同口徑
    off_book_active = 0     # 有效資產中「掃到/vCenter 但沒登記」的
    # 登記「台數」（去重）：同一台被多系統／多 VIP 各登記一筆，筆數會灌水。用 machine_key
    # 去重後才是實際台數（2026-09-18 使用者：一 IP 多系統其實是同一台）。headline 顯示台、
    # 括號附登記筆數，兩邊都對得上（台跟各盤點頁一致、筆跟公司清冊一致）。
    _reg_keys: set[str] = set()

    def _mkey(r) -> str:
        # [B-03] 直接用全站同一把尺。以前這裡是 machine_key 的複製品，machine_key 加了網域
        # 處理後它沒跟著改——首頁「在管」3,116 vs 主機清單 3,113，被 verify_numbers 擋下
        import system_stats
        return system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
    # 機房 × 環境別交叉分佈（使用者 2026-08-13 要求）：「內湖有幾台正式、幾台測試」
    # 這種問法，光看 by_location／by_env 兩個各自獨立的加總答不出來，要交叉統計。
    by_location_env: dict[str, dict[str, int]] = {}
    # 平台下鑽：使用者要能點「Windows」展開看是 2016/2019/2022 各幾台，不是只有一個總數。
    # 鍵是平台大類（跟 by_platform 同一組值），值是「這個平台底下的 OS 版本 → 台數」。
    by_platform_os: dict[str, dict[str, int]] = {}
    real_os = 0
    active = 0
    _off_keys: set[str] = set()
    # 混進來會製造假重複（停用舊資料+使用中新資料同IP）、也讓平台/OS/總數失真。
    # 只有「資產狀態分布(by_status)」要算全部（那正是要看退役有幾台）；其餘統計只算有效。
    for r in rows:
        bump(by_status, r["asset_status"] or "未填")   # 狀態分布：全部都算
        if (r["asset_status"] or "").strip() in RETIRED_STATUS:
            continue                                   # 退役的到此為止，不進其他統計
        active += 1
        if _is_offbook(r["asset_serial"]):
            off_book_active += 1
            _off_keys.add(_mkey(r))       # 帳外「台數」（畫面寫台就要逐台，2026-09-20）
        else:
            registered_active += 1        # 登記筆數（跟公司清冊同口徑）
            _reg_keys.add(_mkey(r))       # 登記台數（去重）用去重鍵累積
        guess_os = guesses.get(r["ip"], (None, None))[0]
        platform = platform_of_from_os(r["os"], r["device_model"], conn, guess_os)
        bump(by_platform, platform)
        # 正規化後才統計：同一個 OS 在資料裡有多種寫法
        # （Rocky Linux 9.7 vs Rocky Linux 9.7 (Blue Onyx)），不收斂就會被算成兩種。
        # 使用者 2026-08-13 要求：os 欄位真的空白，跟填了字面「N/A」，對「我知不知道
        # 這台的版本」這個問題答案是同一個——都是「不知道」，不該在畫面上拆成兩列
        # 讓人误以为是兩種不同狀況，統一走下面「沒有真 OS」那條路徑一起算。
        has_real_os = bool(r["os"]) and str(r["os"]).strip().upper() != "N/A"
        if has_real_os:
            real_os += 1
            canonical_os = normalize.normalize_os(r["os"], conn, r["device_model"])["canonical"]
            bump(by_os, canonical_os)
            bump(by_platform_os.setdefault(platform, {}), canonical_os)
        else:
            # 沒有真 OS：有掃描推測就用推測值分組（標明是猜的），完全沒線索才歸「未知版本」。
            # 平台下鑽要能誠實回答「這台我連版本都不知道」，不能悄悄漏掉不算。
            guess_label = f"{guess_os}（推測）" if guess_os else "未知版本"
            bump(by_platform_os.setdefault(platform, {}), guess_label)
        if r["device_model"]:
            bump(by_model, normalize.normalize_model(r["device_model"], conn)["canonical"])
        # 環境別也收斂（UAT/DEV/OA → 測試，使用者 2026-08-20 定案）。
        # 收斂放在這裡而不是只在交叉表做：兩處若用不同粒度，同一頁上「環境別」
        # 那區跟交叉表的數字會對不起來，看的人只會以為系統算錯。
        env_group = group_environment(r["environment"])
        bump(by_env, env_group)
        # 機房分佈：原值有幾十種寫法（01_板橋機房、敦南…、各分公司），全列出來看不出重點，
        # 所以依 location_groups.json 收斂成幾個主要據點，其餘歸「分公司」。
        # 規則放設定檔不寫死：哪個地名算哪一組是業務判斷，會變，不該每次都改程式。
        location = group_location(r["physical_location"])
        bump(by_location, location)
        bump(by_location_env.setdefault(location, {}), env_group)
        bump(by_virt, "虛擬機" if is_vm_value(r["is_vm"], r["device_model"]) else "實體機")

    return {
        # total ＝「有效資產」（已排除退役），含帳外——保留供既有邏輯／對照用。
        "total": active,
        # registered_total ＝有效資產中「正式登記」的（再排除帳外 DYN-/VC-/AUTO-）。
        # 這是對外頭條「在管資產」該用的數字：跟公司官方清冊同一種算法（只算登記的），
        # 兩邊才對得起來。帳外另外用 off_book_total 顯示，不併進頭條（2026-09-07 使用者定案）。
        "registered_total": registered_active,    # 登記「筆數」（跟公司 CIA 清冊同口徑）
        "registered_hosts": len(_reg_keys),       # 登記「台數」（machine_key 去重）＝headline 用
        "off_book_total": off_book_active,        # 帳外**筆數**（DYN-/VC-/AUTO-，非退役）
        # 2026-09-20 公司驗收：畫面寫「另有 4,155 台未登記」，其實是帳外筆數，
        # 而且跟對帳明細的「未登記 53」（這次掃到、清冊查無）不是同一件事。逐台另給一個。
        "off_book_machines": len(_off_keys),      # 帳外**台數**（machine_key 去重）
        "total_all": len(rows),                  # 含退役的全部，供對照
        "retired_count": len(rows) - active,     # 退役**筆數**（含帳外那幾筆）
        "by_platform": by_platform,
        # 平台下鑽：{"Windows": {"Windows Server 2022": 12, "Windows Server 2016": 5, ...}, ...}
        "by_platform_os": by_platform_os,
        "by_environment": by_env,
        "by_status": by_status,
        "by_virtualization": by_virt,
        # 機房分佈（physical_location 原值），供儀表板回答「各機房各幾台」
        "by_location": by_location,
        # 機房 × 環境別交叉：{"內湖": {"正式": 900, "測試": 200, ...}, ...}
        "by_location_env": by_location_env,
        # 正規化後的明細：這才答得出「我有幾台 Rocky 9.7」
        "by_os": by_os,
        "by_model": by_model,
        # 待人工對應的髒資料（規則與字典都認不出來的原值）
        "pending_normalize": normalize.pending_values(conn),
        # 誠實揭露：多少台的 OS 是真的收到的、多少是靠掃描推測的
        "os_from_facts": real_os,
        "os_guessed": active - real_os,
        # 資料治理進度：光看「有幾台機器」看不出資料乾不乾淨，
        # 也看不出還剩多少要處理。這一區回答的是「盤點做到哪了」。
        "data_quality": data_quality(conn, total=active),
    }


def data_quality(conn, total: int | None = None) -> dict:
    """盤點資料的品質與待辦：已校正多少、還有多少要人處理。

    使用者 2026-07-30 提出：合併了 548 筆，但儀表板上看不出來——
    「有幾台 Windows」是機器組成，「還有幾筆沒對帳」是工作進度，兩者都要有。
    每一項都對應一個明確的下一步，否則只是好看的數字。
    """
    def one(q: str) -> int:
        try:
            return conn.execute(q).fetchone()[0]
        except Exception:  # noqa: BLE001 - 舊 DB 可能還沒有某些表，缺就算 0
            return 0

    if total is None:
        total = one("SELECT COUNT(*) FROM hardware")

    # 有 vm_uuid＝已經跟 vCenter 對上並拿到機器自己報的事實
    verified = one(
        "SELECT COUNT(*) FROM hardware WHERE vm_uuid IS NOT NULL AND length(trim(vm_uuid)) > 0")
    os_unknown = one(
        "SELECT COUNT(*) FROM hardware WHERE os IS NULL OR length(trim(os)) = 0")
    pending_review = one("SELECT COUNT(*) FROM merge_review WHERE status = 'open'")
    merged_done = one("SELECT COUNT(*) FROM merge_review WHERE status = 'merged'")
    # 重複登記只算「疑似真重複」（2026-09-14）：CIA 清冊是每個服務／VIP 一筆，
    # 同主機同 IP 多筆大多是一機多系統或 VIP 分列，那些不是重複、不能叫人刪。
    # 判準與 /api/assets/duplicates 共用 vip_view，兩邊數字才對得起來。
    try:
        import vip_view

        dup = vip_view.duplicate_summary(conn)
    except Exception:  # noqa: BLE001 - 舊 DB 缺欄位就當 0，不擋整頁
        dup = {"suspect_groups": 0, "suspect_extra_rows": 0,
               "multi_system_groups": 0, "split_groups": 0}

    return {
        "total": total,
        "verified_by_vcenter": verified,
        "verified_pct": round(verified * 100 / total) if total else 0,
        "os_unknown": os_unknown,
        "pending_review": pending_review,
        "merged_done": merged_done,
        "duplicate_groups": dup["suspect_groups"],
        "duplicate_extra_rows": dup["suspect_extra_rows"],
        # 同主機同 IP 多筆、但不是重複的組（一機多系統＋VIP／用途分列）
        "duplicate_legit_groups": dup["multi_system_groups"] + dup["split_groups"],
    }


# 系統健康度：由關聯主機的納管四態推導，取代人手動標。
# 對應關係刻意保守——「我看不到它」不等於「它壞了」，但也絕不能說 ok。
_STATE_TO_HEALTH = {
    LOST: "err",            # 主機失聯＝系統確實有問題
    NOT_ONBOARDED: "warn",  # 收不到資料＝我不知道它好不好，不能說 ok
    ONBOARDED: "ok",
    COLLECTED: "ok",        # 設備資料收得到＝看得到它的狀態
    RETIRED: "ok",          # 已下線的主機不該把系統拖成紅燈
    EXEMPT: "ok",           # 講好不納管的，不該一直紅著提醒
    NOT_COVERED: "warn",    # 沒掃過＝不知道，不能算 ok，但也不是「這台出事」
    NO_IP: "warn",          # 沒 IP＝資料問題，無從判斷
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


def _ssh_hostkey_opts() -> list[str]:
    """SSH 主機金鑰驗證選項。**全專案唯一一份定義在 onboard_engine**，這裡只是取用。

    2026-08-28 從 `StrictHostKeyChecking=no` 改成 `accept-new`：原本那個等於
    關掉主機金鑰驗證＝自願接受中間人攻擊，是稽核必開的缺失。
    延後 import 是為了避免模組互相 import 造成循環。
    """
    from onboard_engine import SSH_HOSTKEY_OPTS

    return SSH_HOSTKEY_OPTS



def same_machine_serials(conn, asset_serial: str) -> list[str]:
    """[B-05] 這筆登記所屬的「同一台」的全部序號（machine_key 相同、且同為退役或同為非退役）。

    同一台＝全站同一把尺 system_stats.machine_key；報廢與使用中不算同一台（IP／主機名會回收）。
    主機名或 IP 缺一（machine_key 退回序號）時只有自己。
    """
    import system_stats

    me = conn.execute("SELECT hostname, ip, asset_status FROM hardware WHERE asset_serial = ?",
                      (asset_serial,)).fetchone()
    if me is None:
        return []
    key = system_stats.machine_key(me["hostname"], me["ip"], asset_serial)
    if key.startswith("sn:"):
        return [asset_serial]
    retired = (me["asset_status"] or "").strip() in RETIRED_STATUS
    out = []
    for r in conn.execute("SELECT asset_serial, hostname, ip, asset_status FROM hardware WHERE trim(ip) = ?",
                          ((me["ip"] or "").strip(),)):
        if system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"]) != key:
            continue
        if ((r["asset_status"] or "").strip() in RETIRED_STATUS) != retired:
            continue
        out.append(r["asset_serial"])
    return out or [asset_serial]


def mark_collect_ok(conn, asset_serial: str, checked_at: str | None = None) -> list[str]:
    """[B-05] 納管／收集成功：寫到**整台**（跟失敗時寫同 IP 全部筆對稱）。回寫到的序號。

    以前成功只寫 `WHERE asset_serial = ?` 一筆：一台多筆登記時，沒寫到的兄弟筆仍是「進不去」，
    漏斗合併後可能勝出 → 納管成功的機器顯示成未納管。
    """
    serials = same_machine_serials(conn, asset_serial)
    ph = ",".join("?" for _ in serials)
    if checked_at is None:
        conn.execute(f"UPDATE hardware SET collect_ok = 1 WHERE asset_serial IN ({ph})", serials)
    else:
        conn.execute(f"UPDATE hardware SET collect_ok = 1, collect_checked_at = ?, collect_error = NULL "
                     f"WHERE asset_serial IN ({ph})", (checked_at, *serials))
    return serials



def expand_to_machines(conn, serials: list[str]) -> list[str]:
    """[B-08] 把選的登記展開成「整台」的全部序號（去重、保序）。同一台＝same_machine_serials。"""
    out: list[str] = []
    for s in serials or []:
        s = (s or "").strip()
        if not s:
            continue
        for x in same_machine_serials(conn, s) or [s]:
            if x not in out:
                out.append(x)
    return out


def preview_rows(conn, serials: list[str]) -> list[dict]:
    """[B-08] 「這個動作會改到哪幾筆」——動作前給人看的清單。"""
    import system_report as _sr

    out = []
    for s in serials:
        r = conn.execute("SELECT asset_serial, hostname, ip, asset_name, asset_purpose, api_id, asset_status "
                         "FROM hardware WHERE asset_serial = ?", (s,)).fetchone()
        if r is not None:
            d = dict(r)
            # 讓人看得出哪幾筆是系統自己產的（DY／vCenter 抓到、CIA 清冊沒有）——
            # 2026-09-20 使用者：「整台下線」會一起改到它們，按之前要知道自己動到什麼
            d["off_book"] = _sr.is_off_book(d["asset_serial"])
            out.append(d)
    return out

def why_not_collectable(conn, asset_serial: str) -> str | None:
    """這台為什麼沒被收集？回一句話；回 None 代表它**是**合格對象。

    ⚠️ 2026-09-23：加這支之前，四個收集器對「這台沒進迴圈」一律沉默——
    `candidates` 是 0、`failed` 是空的，前端只好填一句
    「這一輪沒收到（這台可能不在可收集清單、或被排除）」。

    那句話等於沒講，而且**答案其實查得出來**：沒有 IP、還沒納管、序號根本不存在，
    三種要做的事完全不同。查得出來卻寫「可能……或……」，跟顯示 0 是同一種病。

    判定順序跟 `collect_targets_sql` 一致（同一台的任一筆 collect_ok=1 就算納管），
    不然會出現「收集器說收得到、這支說收不到」的兩套答案。
    """
    rows = []
    try:
        serials = expand_to_machines(conn, [asset_serial]) or [asset_serial]
        ph = ",".join("?" for _ in serials)
        rows = conn.execute(
            f"SELECT asset_serial, ip, collect_ok FROM hardware "
            f"WHERE asset_serial IN ({ph})", tuple(serials)).fetchall()
    except Exception:  # noqa: BLE001 - 查不出來就不要亂講，交給呼叫端顯示原本的訊息
        return None
    if not rows:
        return f"資產編號 {asset_serial} 在資產表裡找不到——這一輪根本沒有這台可以收。"
    if any(r["collect_ok"] == 1 and (r["ip"] or "").strip() for r in rows):
        return None
    has_ip = any((r["ip"] or "").strip() for r in rows)
    if not has_ip:
        return ("這台**沒有可用的 IP**（同一台的每一筆登記 IP 都是空的），"
                "收集連要連去哪裡都不知道。先到資產資料補 IP。")
    return ("這台**還沒納管**（collect_ok=0）——收集帳號進不去，所以不在收集清單裡。"
            "同一台的所有登記都查過了，沒有任何一筆是已納管。"
            "先執行納管腳本，納管成功後這一項就會有資料。")


def collect_targets_sql(conn, only_serial, base_sql: str):
    """把「只收這一筆」放寬成「只收這一台」（2026-09-20）。

    使用者從帳外那筆的詳細頁按收集，收不到——因為收得到的是同一台的 CIA 那筆。
    同一台只要有**任一筆** collect_ok=1 就收得到，所以指定單台時要展開成整台的序號。
    回 (sql, params)。
    """
    if not only_serial:
        return base_sql, ()
    import manage_state

    serials = manage_state.expand_to_machines(conn, [only_serial]) or [only_serial]
    ph = ",".join("?" for _ in serials)
    return base_sql + f" AND asset_serial IN ({ph})", tuple(serials)
