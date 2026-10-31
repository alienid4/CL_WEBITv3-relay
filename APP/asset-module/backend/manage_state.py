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

import fleet_cache as _fleet_cache
import functools
import re
import subprocess
import sqlite3

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
# EXIST1（使用者 2026-10-18）：已納管＝收集帳號 7 天內連得上（跟 CIA 有沒有登記無關，
# 也不再要求這次掃描看得到）。連上過、但超過 7 天沒連上＝已納管但失聯。天數在 existence_rules.json。
ONBOARDED_STALE = "已納管但失聯"

ALL_STATES = (UNREGISTERED, NOT_ONBOARDED, ONBOARDED, COLLECTED, LOST, RETIRED, EXEMPT, NOT_COVERED, CONFLICT,
              RETIRED_ALIVE, NO_IP, ONBOARDED_STALE)
#: 「要人處理」的狀態。已納管、已收集都不算——統計「有問題幾台」一律用這份，不要各處自己寫「!= 已納管」
NEEDS_ACTION_STATES = (UNREGISTERED, NOT_ONBOARDED, LOST, NOT_COVERED, CONFLICT, RETIRED_ALIVE, NO_IP,
                       ONBOARDED_STALE)

# 停用/報廢/閒置＝退役資產，是資產生命週期的歷史，不算進「有效盤點」。
# 全站要排除退役的地方（composition 統計、重複偵測…）都共用這個常數，避免各處各自定義漏同步。
# 正本在 lifecycle.py（number_proof 不准 import 本檔，所以口徑放在一支沒有相依的小檔）。
from lifecycle import RETIRED_STATUS  # noqa: E402


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
    ONBOARDED_STALE: "納管過，但最近 7 天收集帳號都沒連上——先收一輪；還是連不上再查是否關機、換 IP 或金鑰被移除",
}


def ips_of(raw) -> list[str]:
    """IP 欄拆成可掃描的 IP 清單。CIA 常見一格填兩個（「a,b」「a／b」）；0.0.0.0 是佔位不算。"""
    # 純函式（只看字串本身），結果快取：summarize 一次對每筆呼叫兩次，ipaddress 解析佔大半時間（Q42）。
    # 回傳新 list，呼叫端改它不會汙染快取。
    return list(_ips_of_cached(str(raw or "")))


@functools.lru_cache(maxsize=65536)
def _ips_of_cached(raw: str) -> tuple:
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
    return tuple(out)


def classify(registered: bool, seen_in_scan: bool, collect_ok: int | None,
             collected: bool = False, retired: bool = False, exempt: bool = False,
             covered: bool = True, has_ip: bool = True, collect_fresh: bool = True) -> str:
    """把「有沒有登記／這次有沒有掃到／收集連不連得上」三個事實變成一個狀態。

    刻意寫成純函式（不碰 DB、不碰網路）——這是整個功能的判定核心，
    必須能被直接測到，不能藏在 SQL 或 API 裡。

    collect_fresh（EXIST1，2026-10-18）：collect_ok=1 是不是在 7 天內（existence.CollectIndex 算）。
    已納管＝7 天內連得上，**不看有沒有登記、也不看這次掃不掃得到**——收集帳號連得進去，
    比掃描回應更直接證明它在。連上過但超過 7 天＝已納管但失聯。
    """
    fresh_ok = collect_ok == 1 and collect_fresh
    if not registered:
        # CIA／資產庫沒有這台，但收集帳號 7 天內連得上——就是已納管（CIA 待補登另外算）
        return ONBOARDED if fresh_ok else UNREGISTERED
    # 退役優先：下線的機器掃不到、收不到都是正常的，不能再算成失聯或未納管
    if retired:
        # [B-07] 但標了退役卻還掃得到／收得到，不可以直接結案——「清冊寫報廢、實際還在跑」
        # 本身就是稽核發現（該關沒關，或清冊標錯），要列待辦
        if seen_in_scan or fresh_ok:
            return RETIRED_ALIVE
        return RETIRED
    # 已經收得到就顯示已納管——豁免是「不去納管它」，不是「不准收」。
    # 真的收到了卻還顯示「非納管設備」會讓人以為資料是假的。
    if exempt and not fresh_ok:
        return EXEMPT
    # 已收集優先於失聯：SAN switch 多半在 OOB 網段、我們的掃描本來就看不到，
    # 但收集／匯入到它的資料本身就證明它在。已納管的（收得到）照舊走下面。
    if collected and not fresh_ok:
        return COLLECTED
    if fresh_ok:
        return ONBOARDED
    if collect_ok == 1:
        # 連上過、但超過 7 天沒再連上（或時間不明）——不可以再顯示「已納管」讓人以為一切正常
        return ONBOARDED_STALE
    if not seen_in_scan:
        # [B-09] 沒有 IP 的機器掃描本來就找不到它——那是「沒查」，不可以說成失聯
        if not has_ip:
            return NO_IP
        # 「沒掃過」不可以說成「失聯」（2026-09-16）：掃描範圍沒涵蓋到它，
        # 我們根本沒有證據說它在不在。要做的事也不同——是去把網段加進掃描範圍，
        # 不是去查機房有沒有關機。
        if not covered:
            return NOT_COVERED
        # 登記了卻掃不到、收集帳號也從沒連上過＝失聯。
        # （收集帳號 7 天內連得上的，上面已經判成已納管——那比掃描回應更直接證明它在；EXIST1）
        return LOST
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
    import ssh_mux

    cmd = ["ssh", "-i", key_path, "-o", "BatchMode=yes", *_ssh_hostkey_opts(), *ssh_mux.mux_opts(),
           "-o", f"ConnectTimeout={timeout}", f"{account}@{host}", "uname -s"]
    try:
        r = ssh_mux.run(cmd, host, timeout=timeout + 5)
    except (subprocess.SubprocessError, OSError):
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
    import ssh_mux

    cmd = [
        "ssh", "-i", key_path, "-o", "BatchMode=yes",
        *_ssh_hostkey_opts(), *ssh_mux.mux_opts(), "-o", f"ConnectTimeout={timeout}",
        f"{account}@{host}", "hostname",
    ]
    try:
        r = ssh_mux.run(cmd, host, timeout=timeout + 5)
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"執行失敗：{exc}"
    if r.returncode == 0 and r.stdout.strip():
        return True, None
    return False, ((ssh_mux.explain(r.stderr) if r.returncode == 255 else None)
                  or _clean_ssh_error(r.stderr or r.stdout or ""))


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
    # 天條（2026-10-27 終審 F1）：VMware 設備不試連（不發 SSH）。天條前就納管的 ESXi 若照舊試連成功會被寫回
    # collect_ok=1（又變回「已納管」）——這裡直接寫 0＋原因，排程／手動刷新／納管後單台刷新都一樣。
    rows, vm_rows = drop_vmware_targets(conn, rows)
    # 試連身分要跟平台走：AIX 上的收集帳號是 8 字元的短名（max_logname 限制），
    # 一律拿 webit3scan 去試連，那 8 台 AIX 會永遠停在「未納管」，而錯誤訊息只說
    # Permission denied，看不出是「名字對不上」而不是「金鑰沒佈」。
    # 每台一個收集帳號（hardware.collect_account；遷移過的那台是新帳號，舊的已刪）
    import collect_account_migration as _cam
    import collected_os as _cos

    _cos.measured_map(conn)     # 先算一份（有快取）：下面逐台判平台時共用，不逐台查實測
    targets = [(r["asset_serial"], r["ip"],
                _cam.account_for_host(conn, r["asset_serial"], collect_platform_of(
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
    import onboard_eligibility as _oe
    for x in vm_rows:
        # 只改原本 collect_ok=1 的（天條前已納管）：改 0、原因寫明「待移除」（onboarded_esxi 靠這句認得它）；
        # 從沒納管過的 ESXi 不動
        conn.execute("UPDATE hardware SET collect_ok = 0, collect_checked_at = ?, collect_error = ? "
                     "WHERE asset_serial = ? AND collect_ok = 1", (now, _oe.VMWARE_LEGACY_REASON, x["asset_serial"]))
    conn.commit()
    ok_n = sum(1 for _, ok, _ in results if ok)
    return {"checked": len(results), "ok": ok_n, "failed": len(results) - ok_n, "skipped_vmware": len(vm_rows)}


def _exempt_serials(conn) -> set[str]:
    """人工標記的非納管設備。獨立一支方便測試注入，也避免 import 迴圈。"""
    import onboard_exempt

    return onboard_exempt.active_serials(conn)


#: 整台狀態聚合開關（Codex 36 號條件 1／8）：app_settings.machine_state_mode。
#: representative＝代表筆（現行，預設）；machine＝整台聚合。**單一出口**：全站只有 summarize() 讀這個設定，
#: 首頁卡片／下鑽／漏斗／匯出都吃 summarize 的結果，不會有兩種算法同時供應不同卡片。
STATE_MODE_SETTING = "machine_state_mode"
MODE_REPRESENTATIVE, MODE_MACHINE = "representative", "machine"
STATE_MODE_LABEL = {MODE_REPRESENTATIVE: "代表筆", MODE_MACHINE: "整台"}


def state_mode(conn) -> str:
    """目前採用的台數狀態口徑。沒設、值不認得、讀不到＝代表筆（部署後數字不變）。"""
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (STATE_MODE_SETTING,)).fetchone()
    except Exception:  # noqa: BLE001 - 還沒有 app_settings（全新庫初始化途中）：當沒設
        return MODE_REPRESENTATIVE
    v = (r[0] if r else "") or ""
    return MODE_MACHINE if str(v).strip().lower() == MODE_MACHINE else MODE_REPRESENTATIVE


def state_mode_info(mode: str) -> dict:
    """給 API／畫面顯示「目前採用口徑」與來源版本。"""
    ver = None
    try:
        import json
        from pathlib import Path
        ver = json.loads((Path(__file__).with_name("version.json")).read_text(encoding="utf-8")).get("version")
    except Exception:  # noqa: BLE001 - 版本只是顯示用，讀不到不影響數字
        ver = None
    return {"mode": mode, "label": STATE_MODE_LABEL[mode], "version": ver, "setting": STATE_MODE_SETTING}


@_fleet_cache.cached   # 跨請求快取：資料庫沒變就不重算（fleet_cache）；口徑開關存在資料庫，切換即換指紋
def summarize(conn) -> dict:
    """全站四態統計＋每一台的狀態。畫面（儀表板四格、資產清單狀態欄）共用這一份，
    數字才不會跟清單對不上。口徑（代表筆／整台）由 app_settings.machine_state_mode 決定，只在這裡切。"""
    return _aggregate(_collect_items(conn), state_mode(conn))


def _collect_items(conn) -> dict:
    """逐筆判狀態（口徑無關的共用部分）。回傳 ctx 給 _aggregate。"""
    from api import _latest_scan_time, _row_in_keys, _scan_keys, _scanned_alive_rows

    scan_time = _latest_scan_time(conn)
    scanned = _scanned_alive_rows(conn, scan_time)
    scan_ips, scan_hostnames = _scan_keys(scanned)

    # os_type 讓「已納管主機」表能按 Linux／Windows／AIX 二次篩選（使用者 2026-09-11：
    # 一千多台要補佈，得先挑出同一種 OS 一次跑）。重用漏斗那份分類，不另寫一套。
    from pipeline import _os_type
    import os_override
    _ov_map = os_override.active_map(conn)
    import collected_os
    _collected = collected_os.platform_map(conn)

    hw = conn.execute(
        "SELECT asset_serial, hostname, ip, os, collect_ok, collect_checked_at, collect_error, asset_status "
        "FROM hardware"
    ).fetchall()
    exempt_serials = _exempt_serials(conn)
    # ROOTKEY1：用 root 收集金鑰的主機（畫面標「root 金鑰（暫時）」）。舊 DB 沒這欄就當沒有。
    try:
        root_key_serials = {x[0] for x in conn.execute(
            "SELECT asset_serial FROM hardware WHERE root_key_at IS NOT NULL")}
    except Exception:  # noqa: BLE001
        root_key_serials = set()
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
    # 2026-10-20：「掃過沒有」改看近 N 天的合併涵蓋（網路掃描各網段最近一次＋2-1 開始收集每個位址
    # 最新一次），不再只看最近一次網路掃描——那次只掃一段，其他網段的機器就全被說成「未涵蓋」，
    # 但 2-1 明明每天都探測過它們。2-1 探測有回應的也算「掃到」。全站只有 scan_scope.recent_coverage
    # 這一份；完全沒有任何涵蓋證據時維持舊行為（看最近一次掃描的涵蓋紀錄，沒紀錄＝無法判斷）。
    cov = scan_scope.recent_coverage(conn)
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
    # EXIST1（2026-10-18）：「已納管」＝收集帳號 7 天內連得上。整台＋逐 IP 的收集證據索引
    # 跟 existence.compute 共用同一份（existence.CollectIndex），首頁已納管跟存在可信度才對得起來。
    import existence

    _cidx = existence.CollectIndex(conn)
    # 收集證據也要「整台」看（2026-10-28，verify_numbers 在 221 抓到首頁已納管 256 vs 存在可信度 257）：
    # 以前逐筆 row_status——同一台兩筆登記、資料只收在其中一個 IP 上時，另一筆判成「未納管」，
    # 代表筆又是「要處理的優先」，整台就被算成未納管；existence.compute 卻是整台（全部登記的
    # machine_key＋全部 IP）一起看。這裡照 existence 同一個分組（退役另組＋machine_key）算一次，
    # 每一筆都用它這台的結果——「已納管」只有一個定義。
    _mgrp: dict[str, dict] = {}
    for r in hw:
        _base = _ss.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        _gk = ("ret:" if (r["asset_status"] or "").strip() in RETIRED_STATUS else "") + _base
        _g = _mgrp.setdefault(_gk, {"keys": set(), "ips": set()})
        _g["keys"].add(_base)
        _g["ips"].update(ips_of(r["ip"]))
    _mstat = {k: _cidx.status(g["keys"], g["ips"]) for k, g in _mgrp.items()}

    def _covered_fn(ips) -> bool:
        # 多個 IP：任一個近 N 天掃過就算涵蓋；沒有 IP＝無從判斷（True，維持舊行為）
        if not ips:
            return True
        return (scan_scope.is_recently_covered(ips, cov) if cov["has_evidence"]
                else any(scan_scope.is_covered(i, covered_nets) for i in ips))

    items = []
    # 收集過資料的設備（SAN switch 收集／離線匯入）。表可能還沒建（舊 DB）就當沒有
    try:
        collected_ips = {r[0] for r in conn.execute("SELECT ip FROM san_switch").fetchall() if r[0]}
    except Exception:  # noqa: BLE001
        collected_ips = set()

    for r in hw:
        _ips = ips_of(r["ip"])
        seen = (_row_in_keys(r, scan_ips, scan_hostnames) or any(i in scan_ips for i in _ips)
                or any(i in cov["alive"] for i in _ips))
        # 同一台只要有一筆收得到，整台就算收得到（見上面 machine_ok 那段）
        _ok = 1 if _ss.machine_key(r["hostname"], r["ip"], r["asset_serial"]) in machine_ok else r["collect_ok"]
        # 收集證據（含帳號／服務收集）7 天內 → 已納管；有證據但過期 → 已納管但失聯
        _cst = _mstat[("ret:" if (r["asset_status"] or "").strip() in RETIRED_STATUS else "")
                      + _ss.machine_key(r["hostname"], r["ip"], r["asset_serial"])]
        if _cst is not None:
            _ok = 1
        state = classify(True, seen, _ok, collect_fresh=(_cst == "fresh"),
                         collected=(r["ip"] in collected_ips),
                         retired=((r["asset_status"] or "").strip() in RETIRED_STATUS),
                         exempt=(r["asset_serial"] in exempt_serials or _ocp_node(r["os"])),
                         covered=_covered_fn(_ips),
                         has_ip=bool(_ips))
        # 整台聚合要用的逐筆證據（私有欄位 _ev，_aggregate 會拿掉，不外洩到輸出）
        _ev = {"seen": bool(seen), "ips": _ips, "ok": _ok, "fresh": (_cst == "fresh"),
               "collected": (r["ip"] in collected_ips),
               "exempt": bool(r["asset_serial"] in exempt_serials or _ocp_node(r["os"]))}
        # 人工指定優先（os_override），跟漏斗同一套
        # 優先序：人工指定 > 收集到的 OS（已納管主機）> 登記 OS 欄（collected_os）
        _ot, _ov = os_override.resolve(_ov_map, r["hostname"], r["ip"], r["asset_serial"],
                                       collected_os.apply_os_type(
                                           _os_type(r["os"], None),
                                           _collected.get(r["asset_serial"])))
        items.append({
            "asset_serial": r["asset_serial"], "hostname": r["hostname"], "ip": r["ip"],
            "state": state, "collect_checked_at": r["collect_checked_at"],
            "collect_error": r["collect_error"],
            "root_key": r["asset_serial"] in root_key_serials,
            "os_type": _ot,
            # 人工指定的留痕（2-1 已納管表「改分類」視窗要講得出是誰改的、能還原）
            "os_override": _ov,
            "_ev": _ev,
        })

    # 掃到但沒登記的＝未登記，它們還不在 hardware 裡，要從掃描側補進來
    for r in scanned:
        if not r["ip"] and not r["hostname"]:
            continue
        if _row_in_keys(r, hw_ips, hw_hostnames):
            continue
        _ports = r["open_ports"] if "open_ports" in r.keys() else None
        _cst = _cidx.status([], [r["ip"]] if r["ip"] else [])
        items.append({
            "asset_serial": None, "hostname": r["hostname"], "ip": r["ip"],
            # 沒登記但收集帳號 7 天內連得上 → 已納管（EXIST1：已納管跟 CIA 無關）
            "state": {"fresh": ONBOARDED, "stale": ONBOARDED_STALE}.get(_cst, UNREGISTERED),
            "collect_checked_at": None, "collect_error": None,
            "os_type": _os_type(None, _ports),   # 未登記沒 os 字串，只能靠掃到的埠推
        })
    # 資產庫、這次掃描都沒有，但收集帳號收得到的 IP（帳號／服務收集）——
    # 收得到就是有這台（EXIST1）。跟 existence.compute 同一條：資產庫或掃描對得到就不另算
    _scan_ip_set = {r["ip"] for r in scanned if r["ip"]}
    for _ip in sorted(_cidx.ip_ts):
        if _ip in hw_ips or _ip in _scan_ip_set:
            continue
        _cst = _cidx.status([], [_ip])
        items.append({
            "asset_serial": None, "hostname": None, "ip": _ip,
            "state": ONBOARDED if _cst == "fresh" else ONBOARDED_STALE,
            "collect_checked_at": _cidx.ip_ts.get(_ip), "collect_error": None, "os_type": _os_type(None, None),
        })

    return {"scan_time": scan_time, "items": items, "covered_nets": covered_nets, "cov": cov,
            "covered_fn": _covered_fn}


def _aggregate(ctx: dict, mode: str) -> dict:
    """逐台彙總。mode=representative（代表筆，現行）或 machine（整台聚合）。
    不改 ctx（compare 會對同一份 ctx 各跑一次）。"""
    import existence

    scan_time, covered_nets, cov = ctx["scan_time"], ctx["covered_nets"], ctx["cov"]
    ev_of: dict[int, dict | None] = {}
    items = []
    for it0 in ctx["items"]:
        it = {k: v for k, v in it0.items() if k != "_ev"}
        ev_of[id(it)] = it0.get("_ev")
        items.append(it)

    # ⚠️ counts 一律「逐台」算，不是逐筆——2026-09-17 使用者：「檢視每一個公式，確保不會
    # 因重複而有低級錯誤」。首頁四格／狀態統計吃這份 counts；若逐筆算，同一台登記多筆會被
    # 灌水，跟納管漏斗（逐台）打架（實測失聯 4641 筆 vs 4226 台）。去重鍵用正典 machine_key，
    # 每台取代表（最小序號，跟 system_stats.collapse_machines 同一條規則）的狀態。
    # items 仍逐筆保留：資產清單狀態欄要一列一筆各自的狀態。
    import system_stats
    groups: dict[str, list] = {}
    for it in items:
        # 退役＝備查，不跟現役合併（使用者 2026-10-17；審查 N1）：退役狀態的列自成一組
        _pre = "ret:" if it.get("state") in (RETIRED, RETIRED_ALIVE) else ""
        # 沒有序號（資產庫沒有、只有掃描／收集看到）的用 IP 佔位——不然沒主機名的全部
        # 落到同一個 'sn:None'，幾十台被併成一台（跟 existence.extra_key 同一條）
        groups.setdefault(_pre + existence.item_key(it["hostname"], it["ip"], it["asset_serial"]), []).append(it)
    # rep_items：每一態「代表筆」的完整清單（2026-10-18，首頁死連結改真下鑽用）。
    # ⚠️ 跟 counts 必須算在同一個迴圈、同一份資料裡——這是「數字」跟「點進去看到的清單」
    # 保證對得起來的唯一方法。counts 從這份清單的筆數算出來（len），不是另外再加一次，
    # 否則將來有人改其中一邊、兩邊就會悄悄漂走（verify_numbers.py 的對應不變式只驗得到
    #「現在是不是還相等」，驗不到「算法有沒有被拆成兩套」）。
    rep_items: dict[str, list] = {s: [] for s in ALL_STATES}
    # 首頁「掃描範圍不完整」紅燈的分母（2026-10-20 使用者：「我不可能有 7,000 多台」）：
    # 以前寫「4,399 / 7,330 台」——分母是全部機器（含退役、CIA 沒登記、沒 IP 的）。
    # 分母改成「現役、有登記、有 IP 的機器」（逐台）＝「未涵蓋」這一態可能出現的母體；
    # 分子就是 counts[未涵蓋]（下鑽＝rep_items[未涵蓋]，同一份計算）。
    gap_base = 0
    for g in groups.values():
        _sts = {x["state"] for x in g}
        if (not (_sts & {RETIRED, RETIRED_ALIVE})
                and any(x.get("asset_serial") and ips_of(x.get("ip")) for x in g)):
            gap_base += 1
    for gk, g in groups.items():
        if mode == MODE_MACHINE and all(ev_of.get(id(x)) is not None for x in g):
            mstate, mrep = _machine_state_of(g, ev_of, ctx["covered_fn"])
            for x in g:
                x["machine_state"] = mstate
            mrep["machine_state"] = mstate
            rep_items[mstate].append(mrep)
            continue
        if mode == MODE_MACHINE:
            for x in g:
                x["machine_state"] = x["state"]
        # [B-04] 跟納管漏斗同一條規則（以前取最小序號的狀態，跟漏斗差 11 台）：
        # 有退役也有使用中 → 登記矛盾；否則要處理的優先，再取最小序號
        states = {x["state"] for x in g}
        _ret = {RETIRED, RETIRED_ALIVE}   # [B-07] 退役但仍在線也是「標退役」
        if len(g) > 1 and (states & _ret) and (states - _ret):
            # 這一組本身沒有一筆真的標「登記矛盾」——那是從「退役＋使用中同時存在」
            # 這個事實推出來的結論，取最小序號那筆當代表、把顯示用的 state 蓋成 CONFLICT
            conflict_rep = dict(min(g, key=lambda x: x["asset_serial"] or ""))
            conflict_rep["state"] = CONFLICT
            rep_items[CONFLICT].append(conflict_rep)
            continue
        rep = min(g, key=lambda x: (0 if x["state"] in NEEDS_ACTION_STATES else 1, x["asset_serial"] or ""))
        rep_items[rep["state"]].append(rep)
    counts = {s: len(rep_items[s]) for s in ALL_STATES}

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
        # 紅燈用（近 N 天合併涵蓋，見上面 gap 那段）：逐台、排除退役、只算有登記有 IP 的
        "scan_gap": {"n": counts[NOT_COVERED], "base": gap_base, "days": cov["days"],
                     "segments": cov["segments"], "probed": cov["probed_count"]},
        "next_action": NEXT_ACTION,
        "items": items,
        # 每一態的代表筆清單（逐台，不是逐筆）。/api/manage-state?state=<state> 直接回這份，
        # 保證「首頁數字」跟「點進去看到的清單」永遠同一份計算、筆數一樣。
        "rep_items": rep_items,
        # 目前採用的口徑與來源版本（Codex 36 號條件 8）：畫面與 API 都看得到
        "machine_state_mode": state_mode_info(mode),
    }


def _machine_state_of(g: list[dict], ev_of: dict, covered_fn) -> tuple[str, dict]:
    """整台狀態（Codex 36 號條件 1～4）。g＝同一個分組鍵（退役自成 ret: 組）的逐筆 item，全部是登記列。

    * 退役組：任一筆還掃得到／收得到（退役但仍在線）→ 整台退役但仍在線，警訊不被吞掉；否則已退役。
    * 現役組：把證據合併到「整台」再判一次——掃到、涵蓋、收集、人工豁免、有可用 IP 全看整台，
      所以結果與序號、與列的排列順序都無關。缺 IP 的列仍保留為該列自己的資料問題
      （items 逐筆 state 不變，下鑽看得到），不會把整台拖成「沒有 IP」。
      新鮮度沿用既有規則：seen／fresh 已經是「現在」的證據（最近一次掃描／近 N 天涵蓋／7 天收集），
      不會把歷史 IP 任一次成功永久推成現在可用。
      豁免＝整台全部登記都豁免才算（有一筆沒豁免＝仍要處理，不用一筆豁免蓋掉其他）。
    代表筆（顯示用）：自己的狀態等於整台狀態的優先、再有 IP 的、再最小序號——確定性、與排列無關。"""
    states = {x["state"] for x in g}
    if states <= {RETIRED, RETIRED_ALIVE}:
        mstate = RETIRED_ALIVE if RETIRED_ALIVE in states else RETIRED
    else:
        evs = [ev_of[id(x)] for x in g]
        ips = sorted({i for e in evs for i in e["ips"]})
        oks = [e["ok"] for e in evs]
        ok = 1 if 1 in oks else (0 if 0 in oks else None)
        mstate = classify(True, any(e["seen"] for e in evs), ok,
                          collected=any(e["collected"] for e in evs), retired=False,
                          exempt=all(e["exempt"] for e in evs),
                          covered=covered_fn(ips), has_ip=bool(ips),
                          collect_fresh=any(e["fresh"] for e in evs))
    chosen = min(g, key=lambda x: (0 if x["state"] == mstate else 1,
                                   0 if ips_of(x.get("ip")) else 1, x["asset_serial"] or ""))
    rep = dict(chosen)
    rep["state"] = mstate
    if len(g) > 1:
        rep["row_state"] = chosen["state"]
        rep["machine_rows"] = sorted(({"asset_serial": x["asset_serial"], "state": x["state"]} for x in g),
                                     key=lambda d: d["asset_serial"] or "")
    return mstate, rep


def compare_modes(conn) -> dict:
    """上線前評估影響（Codex 36 號條件 4／6）：同一個資料庫、同一份逐筆判定，同時算「代表筆」與「整台」兩種口徑。
    **唯讀、不改任何資料、不寫設定**；不受目前開關影響（兩種都算）。

    回傳：總數、各態台數、「每台狀態變動」清單（machine_key 脫敏成項次 M1…，只列有變的）、
    各態計數與下鑽筆數是否一致。只比總數會漏掉狀態互換，所以逐台比。"""
    ctx = _collect_items(conn)
    rep = _aggregate(ctx, MODE_REPRESENTATIVE)
    mac = _aggregate(ctx, MODE_MACHINE)

    def _by_group(summary: dict) -> dict:
        out = {}
        for st, lst in summary["rep_items"].items():
            for it in lst:
                out[_group_key(it)] = (st, it)
        return out

    def _group_key(it: dict) -> str:
        pre = "ret:" if it.get("state") in (RETIRED, RETIRED_ALIVE) else ""
        return pre + existence.item_key(it["hostname"], it["ip"], it["asset_serial"])

    import existence
    # 整台口徑的 rep 可能是別筆，但同一組的 item_key 相同 → 以 item_key（含 ret: 前綴）對齊
    a, b = _by_group(rep), _by_group(mac)
    # 退役前綴以「該台整台狀態」為準；兩邊 key 不同代表退役／現役分組不同，也算變動
    keys = sorted(set(a) | set(b))
    changes = []
    for k in keys:
        sa, ia = a.get(k, (None, None))
        sb, ib = b.get(k, (None, None))
        if sa == sb:
            continue
        src = ib or ia
        rows = [{"asset_serial": x.get("asset_serial"), "state": x.get("state")} for x in
                ((ib or {}).get("machine_rows") or [])] or [{"asset_serial": (src or {}).get("asset_serial"),
                                                            "state": (ia or {}).get("state")}]
        n = len(changes) + 1
        row_states = sorted({r["state"] for r in rows if r["state"]})
        changes.append({
            "no": f"M{n}", "before": sa, "after": sb, "rows": len(rows), "row_states": row_states,
            "reason": (f"同一台 {len(rows)} 筆登記狀態不一致（{'、'.join(row_states)}）；"
                       f"代表筆口徑取「要處理優先、再最小序號」，整台口徑把掃描／涵蓋／收集／IP 證據合併後判為「{sb}」"
                       if len(rows) > 1 else "分組方式不同"),
            # 脫敏：不放 machine_key／主機名／IP，只給序號，要看明細用序號回資產頁
            "asset_serials": [r["asset_serial"] for r in rows if r["asset_serial"]][:10],
        })
    # 重新編號固定（依 key 排序），變動矩陣
    matrix: dict[str, int] = {}
    for c in changes:
        mk = f"{c['before']}→{c['after']}"
        matrix[mk] = matrix.get(mk, 0) + 1

    def _consistent(summary: dict) -> bool:
        return all(summary["counts"][st] == len(summary["rep_items"][st]) for st in ALL_STATES)

    return {
        "current_mode": state_mode_info(state_mode(conn)),
        "representative": {"total": rep["total_known"], "counts": rep["counts"], "drilldown_equals_card": _consistent(rep)},
        "machine": {"total": mac["total_known"], "counts": mac["counts"], "drilldown_equals_card": _consistent(mac)},
        "total_equal": rep["total_known"] == mac["total_known"],
        "counts_equal": rep["counts"] == mac["counts"],
        "changed_machines": len(changes),
        "change_matrix": matrix,
        "changes": changes,
        "read_only": True,
    }


def retired_breakdown(conn, summary: dict | None = None) -> dict:
    """首頁「已退役 N 台」點進去看的組成（2026-10-20 使用者：「退役有 2,029，這一定是有算錯」）。

    逐台＝summarize 的 rep_items[已退役]（跟卡片數字同一份），每台再附它的全部登記列：
    資產狀態值（停用／報廢／閒置）× 來源（CIA 清冊／帳外 DYN-·VC-·AUTO-）、
    同一台還有沒有現役登記（同 machine_key 另有使用中那筆＝「舊登記退役、新登記在用」）、
    一台幾筆。另附：合併紀錄（掃描空殼併回正本，不算任何台數）筆數、待 CIA 異動筆數。"""
    import existence
    import lifecycle
    import system_stats

    s = summary or summarize(conn)
    rows = conn.execute("SELECT * FROM hardware").fetchall()
    by_key: dict[str, list] = {}
    active_keys = set()
    for r in rows:
        k = existence.item_key(r["hostname"], r["ip"], r["asset_serial"])
        if lifecycle.is_retired_value(r["asset_status"]):
            by_key.setdefault("ret:" + k, []).append(r)
        else:
            active_keys.add(system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"]))
    items, by_status, by_source, matrix = [], {}, {}, {}
    for rep in s["rep_items"].get(RETIRED, []):
        g = by_key.get("ret:" + existence.item_key(rep["hostname"], rep["ip"], rep["asset_serial"]), [])
        sts = sorted({(x["asset_status"] or "").strip() for x in g}) or ["?"]
        srcs = sorted({"CIA 清冊" if lifecycle.is_from_cia(x["asset_serial"]) else "帳外（DYN-／VC-／AUTO-）"
                       for x in g}) or ["?"]
        st_label, src_label = "＋".join(sts), "＋".join(srcs)
        also = any(system_stats.machine_key(x["hostname"], x["ip"], x["asset_serial"]) in active_keys for x in g)
        by_status[st_label] = by_status.get(st_label, 0) + 1
        by_source[src_label] = by_source.get(src_label, 0) + 1
        mk = (st_label, src_label)
        m = matrix.setdefault(mk, {"status": st_label, "source": src_label, "machines": 0, "rows": 0})
        m["machines"] += 1
        m["rows"] += len(g)
        items.append({**rep, "statuses": st_label, "source": src_label, "row_count": len(g),
                      "serials": [x["asset_serial"] for x in g], "also_active": also})
    # 卡片點進去是 3-1「CIA 待異動」清單（還沒同步的那幾筆）——數字取同一份，加總＝下鑽
    try:
        pend = conn.execute("SELECT COUNT(*) FROM cia_pending_change WHERE synced_at IS NULL").fetchone()[0]
    except Exception:  # noqa: BLE001 - 舊庫還沒有 cia_pending_change：沒有待異動
        pend = 0
    return {
        "state": RETIRED, "total": len(items), "items": items,
        "rows": sum(i["row_count"] for i in items),
        "by_status": by_status, "by_source": by_source,
        "matrix": sorted(matrix.values(), key=lambda m: -m["machines"]),
        "also_active": sum(1 for i in items if i["also_active"]),
        "multi_row": sum(1 for i in items if i["row_count"] > 1),
        "retired_alive": s["counts"].get(RETIRED_ALIVE, 0),
        "merged_shells": len(lifecycle.merged_rows(conn)),
        "pending_cia": pend,
        "retired_values": sorted(lifecycle.RETIRED_STATUS),
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
    # 全域收集帳號不可以是 root：root 只能逐台、由 root 金鑰確認後才用（決策 ROOTKEY1，
    # root_key.account_for）。設定被填成 root 時一律退回預設帳號。
    if platform == "aix":
        v = get_setting(conn, "collect_ssh_account_aix", AIX_COLLECT_ACCOUNT) or AIX_COLLECT_ACCOUNT
        return AIX_COLLECT_ACCOUNT if v == "root" else v
    v = get_setting(conn, "collect_ssh_account", DEFAULT_COLLECT_ACCOUNT) or DEFAULT_COLLECT_ACCOUNT
    return DEFAULT_COLLECT_ACCOUNT if v == "root" else v


def onboard_account(conn, platform: str = "linux") -> str:
    """納管時要在目標機建（並驗）的帳號。設定若還是舊名（webit3scan），一律換成新名——
    重新納管就是遷移的那一刻（2026-10-18：設定是舊名 → 重新納管照舊名建 → 永遠換不過來）。
    跟 onboard_engine.target_account 同一條規則（建帳號那端由它保證）。"""
    v = get_collect_account(conn, platform)
    return READONLY_ACCOUNT if v == LEGACY_LINUX_ACCOUNT else v


def account_for_ip(conn, ip: str, platform: str = "linux") -> str:
    """只知道 IP 的收集器（健檢、收集方式判定）用這支：同 IP 挑現役那筆登記，照**每台**的收集帳號。"""
    import collect_account_migration as cam

    try:
        serial = serial_for_ip(conn, ip)
    except Exception:  # noqa: BLE001 - 舊 DB／測試庫沒有完整欄位時照走全域
        serial = None
    return cam.account_for_host(conn, serial, platform)


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
                 f"已定案統一成 `{READONLY_ACCOUNT}`，但「還沒遷移」——"
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

    ## 0. 實測 OS 優先（2026-10-21，見 collect_platform_explain）
    """
    return collect_platform_explain(conn, ip, os_val, asset_serial=asset_serial, probe=probe)["platform"]


#: collect_platform_explain 的 basis：判斷依據（畫面／步驟訊息據此講「依什麼判的」）
BASIS_MEASURED = "measured"        # 同一台機器自己講的 OS（facts／帳號收集／AIX 規格），確定是實測
BASIS_COLLECTED = "collected"      # 這筆自己收過（collect_ok=1），但分不出 os 欄是 CIA 原值還是收集值
BASIS_REGISTRY = "registry"        # 還沒實測：照這筆登記的作業系統欄（CIA）
BASIS_SIBLING = "sibling"          # 還沒實測：照同一台其他登記的作業系統欄
BASIS_WINRM = "winrm"              # 這個 IP 上次走 WinRM 收成功
BASIS_UNAME = "uname"              # 當場 uname -s 問到的
BASIS_GUESS = "guess"              # 作業系統不知道，依開放埠推測


def _measured_entry(conn, ip: str, asset_serial: str | None) -> dict | None:
    """這台的實測 OS（collected_os.measured_for）。沒給序號就用 IP 找同 IP 的登記。"""
    import collected_os

    serials = [asset_serial] if asset_serial else []
    if not serials and (ip or "").strip():
        serials = [r["asset_serial"] for r in conn.execute(
            "SELECT asset_serial FROM hardware WHERE trim(ip) = ? ORDER BY collect_ok DESC, asset_serial "
            "LIMIT 5", ((ip or "").strip(),))]
    cached = collected_os.measured_map_if_cached(conn)
    weak = None
    for s in serials:
        if cached is not None:
            m = cached.get(s)
            m = m if m and m.get("source") == "collect" else None     # RVTools 不算實測
        else:
            m = collected_os.measured_for(conn, s)
        if m and m.get("platform") in collected_os.OS_CLASS_OF:
            if m.get("strong"):
                return m
            weak = weak or m
    return weak


def collect_platform_explain(conn, ip: str, os_val: str | None = None,
                             asset_serial: str | None = None, probe=None) -> dict:
    """collect_platform_of 的完整版：回 {platform, basis, note, measured_os, cia_os}。

    ## 0. 實測 OS 優先（2026-10-21 使用者：「組態檢核／略過判斷一律照實測 OS」）

    公司機 SECSVR194-068T：CIA 登記 Windows Server 2019，實際 SSH 登入的是 RHEL 9.4（實測記在同一台的
    另一筆）。舊順序第 1 條就拿 CIA 的 Windows → 納管後收集把組態檢核、OS 重建判成 Windows 那條。
    現在第 0 條先看**同一台機器自己講的 OS**（collected_os.measured_for，跟分類／EOS 用的實測同一套），
    沒有實測才往下走舊的 1～4 條。VMware 判定不在這裡（呼叫端先走 judge_for_asset，fail-closed），
    而且 measured_for 不會把登記成虛擬化平台的那筆翻成 Linux。

    note：要讓人知道的一句（實測跟 CIA 不同、尚未實測照 CIA、作業系統不知道只能猜）；沒什麼好講就 None。
    """
    import facts_collector

    cia_text = (os_val or "").strip()
    if asset_serial:
        try:
            r = conn.execute("SELECT os, os_cia FROM hardware WHERE asset_serial = ?",
                             (asset_serial,)).fetchone()
            if r is not None:
                cia_text = str(r["os_cia"] or "").strip() or str(r["os"] or "").strip() or cia_text
        except sqlite3.Error:
            pass        # 讀不到登記原值：只少了「跟 CIA 不同」那句提示，判斷照常
    cia_platform = platform_from_os(cia_text)

    def out(platform, basis, note=None, measured_os=None):
        return {"platform": platform, "basis": basis, "note": note,
                "measured_os": measured_os, "cia_os": cia_text or None}

    # 0. 實測 OS（機器自己講的）
    try:
        m = _measured_entry(conn, ip, asset_serial)
    except sqlite3.Error as exc:
        # 查實測失敗（舊庫缺表）：退回舊順序，note 會講「尚未實測」；真正的 DB 錯誤留在 log
        import logging
        logging.getLogger("webit3.manage_state").warning(
            "measured OS lookup failed for %s/%s: %s", asset_serial, ip, exc)
        m = None
    if m is not None and not m.get("strong") and platform_from_os(os_val) not in (None, m["platform"]):
        m = None        # 弱證據（分不出是不是 CIA 原值）跟呼叫端給的 os 打架：照舊順序，不讓弱證據蓋掉
    if m is not None:
        p = m["platform"]
        if not m.get("strong"):
            # 收過，但 os 欄跟 CIA 原值分不出來（facts 沒改過它）——照實講是照 CIA 登記判的
            return out(p, BASIS_COLLECTED, "依 CIA 登記判斷（尚未確認實測）", measured_os=m.get("os"))
        note = None
        if cia_platform and cia_platform != p:
            note = f"依實測 OS（{m.get('os')}）判斷；CIA 登記為 {cia_text}，與實測不同"
        return out(p, BASIS_MEASURED, note, measured_os=m.get("os"))

    # 1. 這一筆自己的 os
    got = platform_from_os(os_val)
    if got:
        import collected_os
        note = ("依登記的作業系統欄判斷（尚未實測）" if collected_os._not_cia(asset_serial)
                else "依 CIA 登記判斷（尚未實測）")
        return out(got, BASIS_REGISTRY, note)

    # 2. 同一台其他登記的 os（**這條是 2026-09-22 的主修**）
    sib_note = "依同一台其他登記的作業系統判斷（尚未實測）"
    if asset_serial:
        try:
            for sib in same_machine_serials(conn, asset_serial):
                if sib == asset_serial:
                    continue
                row = conn.execute("SELECT os FROM hardware WHERE asset_serial = ?",
                                   (sib,)).fetchone()
                got = platform_from_os(row["os"] if row else None)
                if got:
                    return out(got, BASIS_SIBLING, sib_note)
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
                    return out(got, BASIS_SIBLING, sib_note)
        except sqlite3.Error:
            pass

    # 2.5 這個 IP 上次是**走 WinRM 收成功**的（2-1 開始收集，2026-10-20）→ 就是 Windows。
    # 公司機實測「軟體 0 服務 0 帳號 0」的根因：WinRM 納管的台 os 欄是空的（以前 WinRM 收到的
    # facts 沒寫回資產）、掃描指紋又沒有 3389 → 掉到第 4 條判成 linux → 拿 SSH 去收一台沒開 22 的
    # Windows，六樣全失敗。WinRM 收成功是「機器自己講的」證據，比埠號猜可靠，放在 uname 之前。
    if winrm_collected(conn, ip):
        return out("windows", BASIS_WINRM)

    # 3. 全部登記都沒有 os → 實問一次。一個指令、免提權、Unix 三平台都有
    actual = probe_uname(ip, runner=probe) if probe is not None else probe_uname(ip)
    got = platform_from_os(actual)
    if got:
        return out(got, BASIS_UNAME)

    # 4. 最後才是埠號猜（既有行為，不動）——但要講清楚「作業系統不知道，這是猜的」
    row = conn.execute(
        "SELECT open_ports FROM scan_history WHERE ip = ? AND scan_ok = 1 "
        "ORDER BY scan_time DESC LIMIT 1", (ip,)).fetchone()
    ports = [int(p) for p in (row["open_ports"] or "").split(",") if p.strip().isdigit()] \
        if row and row["open_ports"] else []
    p = facts_collector.detect_platform(ports)
    return out(p, BASIS_GUESS,
               f"作業系統不知道（沒有實測、登記也沒填），依開放埠推測為 {p}"
               + ("" if ports else "（連開放埠都沒有紀錄）"))


def winrm_collected(conn, ip: str) -> bool:
    """這個 IP 最近一次 2-1 收集結果是「WinRM 收集成功」嗎。查不到（舊庫沒有這張表）回 False。"""
    try:
        row = conn.execute(
            "SELECT route, status FROM collect_dispatch_result WHERE ip = ? "
            "ORDER BY id DESC LIMIT 1", ((ip or "").strip(),)).fetchone()
    except sqlite3.Error:
        return False
    return bool(row) and row["route"] == "winrm" and row["status"] == "collected"


def apply_collected_facts(conn, asset_serial: str, facts: dict, checked_at: str | None = None) -> bool:
    """把收集到的「機器裡的事實」寫回 hardware（只寫 FACT_FIELDS、空值不洗掉既有值、不蓋 CIA 主機名）。

    collect_facts_into_assets（收集規格）與 2-1 開始收集的 WinRM 那條共用——以前 WinRM 那條
    收到 facts 卻沒寫回，資產的 OS 一直是空的，後面的收集就判不出這台是 Windows。回有沒有寫。
    """
    from db import _now_local
    import dyn_shell

    setters = {k: v for k, v in (facts or {}).items() if k in FACT_FIELDS and v not in (None, "")}
    setters = dyn_shell.cia_guard_collected(conn, asset_serial, setters)  # Q80
    if not setters:
        return False
    assigns = "".join(f"{k} = ?, " for k in setters)
    conn.execute(f"UPDATE hardware SET {assigns}collect_checked_at = ? WHERE asset_serial = ?",
                 (*setters.values(), checked_at or _now_local(), asset_serial))
    return True


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
        #: 單條指令的總逾時（秒）；None＝timeout+10。長時間唯讀掃描（目錄清理）才設，ConnectTimeout 仍用 timeout
        self.run_timeout = None
        #: {host: {"connected": bool|None, "transport_error": str|None, "samples": [...]}}
        self.trace: dict = {}
        #: 最近一次呼叫的 {"rc", "err"}；還沒呼叫過是 None
        self.last: dict | None = None

    def _rec(self, host: str) -> dict:
        return self.trace.setdefault(host, {
            "connected": None, "transport_error": None, "samples": [],
            "total": 0, "ok": 0, "empty": 0, "failed": 0,
        })

    def __call__(self, host: str, cmd: str) -> str:
        import time

        rec = self._rec(host)
        t0 = time.time()
        import ssh_mux

        # 連線共用＋同一台不並行＋登入前被斷線退避重試（2026-10-21，見 ssh_mux）
        argv = ["ssh", "-i", self.key_path, "-o", "BatchMode=yes", *_ssh_hostkey_opts(),
                *ssh_mux.mux_opts(),
                "-o", f"ConnectTimeout={self.timeout}", f"{self.account}@{host}", cmd]
        try:
            r = ssh_mux.run(argv, host, timeout=self.run_timeout or self.timeout + 10)
            rc, out, err = r.returncode, r.stdout or "", (r.stderr or "").strip()
        except subprocess.TimeoutExpired:
            rc, out, err = None, "", f"逾時（{self.run_timeout or self.timeout + 10} 秒內沒有回應）"
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

        # 最近一次的離開碼與 stderr（2026-10-16 OS 重建收集）：呼叫端要分得出
        # 「sudo 被拒」與「真的沒有輸出」，否則權限不足會被看成「此機未使用」。
        self.last = {"rc": rc, "err": err}
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
                "text": ("「SSH 根本沒連上」，所以每一類都是空的——"
                         f"這不是指令的問題。原始訊息：{row['transport_error'] or '（無）'}")}
    if connected is None:
        return {"verdict": "not_tried",
                "text": "這一輪沒有對這台發出任何 SSH 指令（不在收集清單內）。"}
    failed, empty, ok = row["cmd_failed"] or 0, row["cmd_empty"] or 0, row["cmd_ok"] or 0
    if failed and not ok:
        return {"verdict": "cmd_failed",
                "text": (f"SSH 連得上（有指令回離開碼 0），但 {failed} 條指令回非 0——"
                         "「是指令本身跑不起來」（不存在、被擋、語法不合這台的 shell）。"
                         "下面的 stderr 是原文。")}
    if empty and not ok:
        return {"verdict": "all_empty",
                "text": ("SSH 連得上、指令也都跑完了（離開碼 0），但「每一條的輸出都是空的」。"
                         "這代表遠端真的沒回東西，不是沒跑到——要往權限或輸出格式查。")}
    return {"verdict": "ok",
            "text": f"SSH 正常：{ok} 條有輸出、{empty} 條空、{failed} 條失敗。"}


def _ssh_runner(key_path: str, account: str = DEFAULT_COLLECT_ACCOUNT, timeout: int = 10):
    """給 facts_collector 用的 runner：runner(host, cmd) -> str。

    回的是 `SSHRunner`（可呼叫），呼叫方式跟以前一樣；多了 `.save()` 可落診斷。
    """
    if account == "root":
        # ROOTKEY1：root 金鑰主機（root_key.account_for 才會回 root）。用專用金鑰、
        # 只送跟 sudo 時同一批固定指令（見 root_key.make_runner）。
        import root_key

        return root_key.make_runner(timeout=timeout)
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


def _explicit_target(conn, serial: str):
    """指名收集的那一筆：回 ([列], None) 或 ([], (原因代碼, 說明))。"""
    import onboard_eligibility
    import system_stats

    r = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
    if r is None:
        return [], ("no_row", f"資產庫沒有 {serial} 這筆登記，沒有可收集的對象")
    if not (r["ip"] or "").strip():
        return [], ("no_ip", f"{serial} 沒有 IP，沒有可收集的對象")
    if system_stats.is_retired_row(r):
        return [], ("retired", f"{serial} 已退役，不收集")
    got = onboard_eligibility.judge_for_asset(conn, r)
    # 審查 N1：只判這一筆不夠——同 IP 的另一筆登記（例：CIA 寫 VMware ESXi，這筆是 os 空白的 AUTO-<ip>）也要算
    if (got and got[0] == "esxi") or onboard_eligibility.vmware_targets(
            conn, [r["ip"]] + ([r["hostname"]] if (r["hostname"] or "").strip() else []), reverse=True):
        return [], ("vmware", "VMware 設備（ESXi／vCenter）不經 SSH 收集，資料來源：vCenter／RVTools")
    return [r], None


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

    updated, failed = 0, []
    if only_serial:
        # 2026-10-27（deploy E2E 抓到）：指名一筆時**不要求 collect_ok = 1**。
        # 本機執行驗證（/api/onboard/verify）、帳密納管後、貼指令偵測、納管後收集都是「剛納管完、要用這次收集
        # 證明連得上」——09-22 起刻意不在收集前先標 collect_ok=1（避免假已納管），舊條件就變成雞生蛋：
        # 沒標 1 就不收、不收就永遠標不了 1（AUTO-<ip> 一律 candidates=0）。
        # 指名也照樣擋：VMware（天條，judge_for_asset）、已退役、沒有 IP——擋下的講原因（skipped），不是「收到 0 個欄位」。
        rows, why = _explicit_target(conn, only_serial)
        if why:
            failed.append({"asset_serial": only_serial, "error": why[1], "skipped": why[0]})
            return {"updated": 0, "failed": failed, "candidates": 0}
    else:
        rows = conn.execute("SELECT asset_serial, ip, os FROM hardware "
                            "WHERE ip IS NOT NULL AND ip != '' AND collect_ok = 1").fetchall()
        # 天條（審查：排程收集會 SSH 天條前就納管的 ESXi）：全機隊收集先濾掉 VMware（判定只算一次）
        rows, vm_skipped = drop_vmware_targets(conn, rows)
        failed += [{"asset_serial": x["asset_serial"], "skipped": "vmware", "error": x["reason"]} for x in vm_skipped]
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
            if apply_collected_facts(conn, r["asset_serial"], facts):
                updated += 1
            import winrm_collector as _wc
            _wc.record_session(conn, r["ip"], r["asset_serial"], facts)
            continue
        # AIX 的收集帳號名不同（max_logname 上限），SSH 身分要跟著平台走，
        # 否則會拿 webit3scan 去登一台上面只有 webit3sc 的機器，永遠 Permission denied
        # 每台一個收集帳號（hardware.collect_account，NULL 就走全域）——
        # 2026-09-24 遷移用；全是 NULL 時行為跟以前一模一樣。
        import root_key as _rk
        # 硬體序號需要 root：root 金鑰主機（ROOTKEY1）走 root，其餘照每台的收集帳號
        run = runner or _runner_for(
            r["ip"], key_path,
            account=_rk.account_for(conn, r["asset_serial"], platform))
        try:
            facts = facts_collector.collect(run, r["ip"], platform)
        except facts_collector.VmwareOnTarget as exc:
            failed.append({"asset_serial": r["asset_serial"], "error": str(exc), "skipped": "vmware_on_target"})
            _save_trace(conn, run, r["asset_serial"], r["ip"])
            continue
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
        import dyn_shell
        setters = dyn_shell.cia_guard_collected(conn, r["asset_serial"], setters)  # Q80：不蓋 CIA 主機名
        assigns = "".join(f"{k} = ?, " for k in setters)
        conn.execute(
            f"UPDATE hardware SET {assigns}collect_checked_at = ? WHERE asset_serial = ?",
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
            _LOC_CFG = {"groups": [], "_empty_label": "未填"}
    return _LOC_CFG


# 機房分組中「認不出的地點」的聚合桶名（#2 第一階段，2026-10-31）。
# 以前認不出的一律靜默歸「分公司」，等於把「不知道」說成「已確認是分公司」；
# 現在 group_location 回「（未歸類：<原值>）」保留原值，彙總統計用 location_bucket 收成這一桶，
# 下鑽（?location_group=（未歸類））用 location_matches 撈同一批，加總＝下鑽。
LOC_UNCLASSIFIED = "（未歸類）"
_LOC_UNCLASSIFIED_PREFIX = "（未歸類："


def group_location(raw: str | None) -> str:
    """把 physical_location 原值歸到主要據點；認不出的回「（未歸類：原值）」，空值回「未填」。

    比對用「包含」而非完全相等：資料裡是「01_板橋機房」「板橋IDC」這類前後綴混雜的寫法，
    要求完全相等等於全部落到未歸類，分組就失去意義。
    **不會**把認不出的併入分公司、也不會當成已確認分點；只有設定檔 groups 命中的才算（含「分公司」組）。
    原始 physical_location 不會被改寫，這裡只算顯示用的分組標籤。
    """
    cfg = _location_config()
    v = (raw or "").strip()
    if not v:
        return cfg.get("_empty_label", "未填")
    for g in cfg.get("groups", []):
        for kw in g.get("match", []):
            if kw and kw in v:
                return g.get("name", kw)
    return f"{_LOC_UNCLASSIFIED_PREFIX}{v}）"


def location_group_names() -> list[str]:
    """設定檔 location_groups.json 的群組名稱（依設定順序）。各頁用這份，不要再硬編碼名稱。"""
    out = []
    for g in _location_config().get("groups", []):
        n = g.get("name") or (g.get("match") or [""])[0]
        if n and n not in out:
            out.append(n)
    return out


def location_unfilled_label() -> str:
    """地點空白的標籤（預設「未填」）。**永遠是獨立一桶**，不併入「（未歸類）」——
    「（未歸類）」只指「有填但認不出」。"""
    return _location_config().get("_empty_label", "未填")


def is_unclassified_location(label: str | None) -> bool:
    """group_location 回傳的標籤是不是「未歸類」（單一原值或聚合桶都算）。"""
    return (label or "").startswith(_LOC_UNCLASSIFIED_PREFIX) or label == LOC_UNCLASSIFIED


def location_bucket(raw: str | None) -> str:
    """彙總統計用：認不出的收成單一桶「（未歸類）」，其餘同 group_location。"""
    g = group_location(raw)
    return LOC_UNCLASSIFIED if is_unclassified_location(g) else g


def location_matches(raw: str | None, wanted: str | None) -> bool:
    """下鑽篩選用：wanted 可以是分組名、聚合桶「（未歸類）」、或單一「（未歸類：原值）」。"""
    return wanted is not None and wanted in (group_location(raw), location_bucket(raw))


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
        "asset_serial, group_name, asset_purpose FROM hardware"
    ).fetchall()
    import os_class as _oc
    import collected_os as _collected_os
    _collected = _collected_os.platform_map(conn)

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
    by_os_class: dict[str, int] = {}
    by_os_class_platform: dict[str, dict[str, int]] = {}   # 大類 → 細平台（RHEL/CentOS…）→ 筆數
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
        # OS 大類（OS-CLASS-AUDIT）：全站唯一一份規則 os_class_groups.json。首頁長條讀這個，
        # 點進去 /assets?os_class= 走 asset_classify 同一支 classify，數字才對得上。
        # 已納管主機以收集到的 OS 為準（collected_os；跟 asset_classify 同一層，長條＝點進去）
        oc = _collected_os.apply_os_class(_oc.classify_row(r, guess_os),
                                          _collected.get(r["asset_serial"]))
        bump(by_os_class, oc)
        bump(by_os_class_platform.setdefault(oc, {}), platform)
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
        # 所以依 location_groups.json 收斂成幾個主要據點，其餘歸「（未歸類）」（不再靜默併入分公司）。
        # 規則放設定檔不寫死：哪個地名算哪一組是業務判斷，會變，不該每次都改程式。
        location = location_bucket(r["physical_location"])
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
        # OS 大類（首頁平台長條）：加總＝total（verify_numbers 釘住）；順序照設定檔
        "by_os_class": by_os_class,
        "by_os_class_platform": by_os_class_platform,
        "os_class_order": _oc.class_order(),
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
        # EXIST1（2026-10-18）：首頁頭條「在管」改讀這裡的 counted（存在可信度 ≥ 門檻，CIA 無關），
        # CIA 有沒有登記另外用 cia_complete_pct（CIA 完整率）講。上面 registered_*／off_book_*
        # 是「資產庫登記」的筆數／台數，保留給對照（主機清單、公司清冊同口徑）。
        "existence": _existence_summary(conn),
    }


def _existence_summary(conn) -> dict:
    import existence

    return existence.compute(conn)["summary"]


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
    ONBOARDED_STALE: "warn",  # 超過 7 天沒連上＝現在好不好不知道
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
    import win_onboard
    import winrm_collector

    # Windows 勾選才收（2026-10-22）：沒納管的不登入（第二道；第一道是只挑 collect_ok=1）
    blocked = win_onboard.collect_block(conn, ip, asset_serial)
    if blocked:
        failed.append({"asset_serial": asset_serial, "error": blocked})
        return None
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
            msg = f"{msg}｜{winrm_collector.enable_hint()}"
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
    # Q79：主機名口徑下同一台可以有不同 IP，候選也要用主機名撈（最後仍以 machine_key 為準）
    import machine_identity
    short = system_stats.strip_domain(
        machine_identity.effective_hostname(me["hostname"], asset_serial)).strip().lower()
    # 實測主機名校正（PRECEDENCE1）對到同一個名字的登記也要撈進候選（2026-10-28）：它們登記上的
    # 主機名不一樣（例：SECSVR18-64 校正成 SECSVR018-064T），上面 LIKE 撈不到——結果 A 找得到 B、
    # B 找不到 A，同一台兩筆登記看到的範圍不一樣（221：SECSVR018-064T）。最後仍以 machine_key 為準。
    fixed = [s for s, meas in (machine_identity._STATE.get("host_fix") or {}).items()
             if short and system_stats.strip_domain(meas).strip().lower() == short]
    ph = ",".join("?" for _ in fixed) or "NULL"
    for r in conn.execute("SELECT asset_serial, hostname, ip, asset_status FROM hardware "
                          "WHERE trim(ip) = ? OR (? <> '' AND lower(trim(hostname)) LIKE ?) "
                          f"OR asset_serial IN ({ph})",
                          ((me["ip"] or "").strip(), short, short + "%", *fixed)):
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



def serial_for_ip(conn, ip: str) -> str | None:
    """納管／重新納管「用 IP 找這台的資產序號」的唯一一把尺（2026-10-18，ACCEPT-E2E1）。

    原本五個入口（網頁一鍵納管、本機執行＋驗證、取得納管指令自動偵測、批次、排程）各寫一句
    `SELECT asset_serial FROM hardware WHERE ip = ?` 不排序取第一筆——同一個 IP 有好幾筆登記時
    （IP 回收後留下的**停用／報廢舊登記**、主機名改過的舊清冊列、DYN 掃描列），拿到的常是**最早建的那筆**，
    也就是舊的那台。於是 facts、納管後六樣全掛到舊登記上，使用者打開現役那筆的資產頁：
    「尚未收集 OS 重建資料」、組態檢核空白、連「有沒有跑過」都看不到（退役跟現役不算同一台）。

    挑法（依序）：現役優先（不是 RETIRED_STATUS）→ 已納管（collect_ok=1）優先 →
    清冊／正式登記優先於掃描產生的 DYN- → 最早建的那筆（rowid 小）。沒有任何登記回 None。
    """
    ip = (ip or "").strip()
    if not ip:
        return None
    rows = conn.execute(
        "SELECT asset_serial, asset_status, collect_ok, rowid AS rid FROM hardware WHERE trim(ip) = ?",
        (ip,)).fetchall()
    if not rows:
        return None
    return min(rows, key=_serial_rank)["asset_serial"]


def _serial_rank(r):
    """serial_for_ip 的挑法（批次版 serials_for_ips 共用同一支，兩邊不會挑出不同筆）。"""
    retired = (r["asset_status"] or "").strip() in RETIRED_STATUS
    return (1 if retired else 0, 0 if (r["collect_ok"] or 0) == 1 else 1,
            1 if str(r["asset_serial"]).startswith("DYN-") else 0, r["rid"])


def serials_for_ips(conn, ips) -> dict[str, str]:
    """serial_for_ip 的批次版（2-1 一次讀上千列用）：ip → 挑中的序號；沒登記的不列。"""
    want = list(dict.fromkeys((i or "").strip() for i in (ips or []) if (i or "").strip()))
    best: dict[str, sqlite3.Row] = {}
    for i in range(0, len(want), 400):
        chunk = want[i:i + 400]
        for r in conn.execute(
                "SELECT trim(ip) AS tip, asset_serial, asset_status, collect_ok, rowid AS rid FROM hardware "
                f"WHERE trim(ip) IN ({','.join('?' * len(chunk))})", chunk):
            cur = best.get(r["tip"])
            if cur is None or _serial_rank(r) < _serial_rank(cur):
                best[r["tip"]] = r
    return {ip: r["asset_serial"] for ip, r in best.items()}


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
        return ("這台「沒有可用的 IP」（同一台的每一筆登記 IP 都是空的），"
                "收集連要連去哪裡都不知道。先到資產資料補 IP。")
    return ("這台「還沒納管」（collect_ok=0）——收集帳號進不去，所以不在收集清單裡。"
            "同一台的所有登記都查過了，沒有任何一筆是已納管。"
            "先執行納管腳本，納管成功後這一項就會有資料。")


VMWARE_SKIP = "VMware 設備（ESXi／vCenter）不經 SSH 收集，資料來源：vCenter／RVTools"


def drop_vmware_targets(conn, rows) -> tuple[list, list]:
    """排程／批次收集的目標先濾掉 VMware（天條；2026-10-27 審查：天條前就納管的 ESXi 仍是 collect_ok=1，
    每次排程收集都會被 SSH）。判定＝onboard_eligibility.vmware_targets（同 IP 的每一筆登記、vCenter ESXi 名單），
    整批只算一次。回 (保留的列, [{asset_serial, ip, reason}])。"""
    import onboard_eligibility

    rows = list(rows)
    vm = onboard_eligibility.vmware_targets(conn, sorted({r["ip"] for r in rows if r["ip"]}))
    kept = [r for r in rows if r["ip"] not in vm]
    skipped = [{"asset_serial": r["asset_serial"], "ip": r["ip"], "reason": VMWARE_SKIP} for r in rows if r["ip"] in vm]
    return kept, skipped


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
