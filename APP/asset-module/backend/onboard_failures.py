"""納管失敗清單：哪些機器試過但進不去、為什麼（2026-09-17）。

## 為什麼要有

使用者批次納管 7 台，5 台回「登入被拒——帳號或密碼不對」，接著問：

> 「這些帳號納管失敗，我以後要怎麼查那些納管失敗過，他的 root 密碼可能不是預設密碼」

這是真實的維運問題：那批機器的密碼跟大部分機器不一樣，要單獨處理（找管理員問、
或走 PAM 取密碼）。但納管紀錄（`onboard_audit`）是**一次動作一列**的流水帳——
同一台試過五次就有五列，要從裡面看出「現在還有哪幾台進不去」得自己用眼睛歸戶。

所以這裡做的是**現況**：每台**最後一次**的結果，而且只列現在仍然失敗的。

## 為什麼只看最後一次

一台機器上週失敗、今天成功了，就不該再出現在「待處理」裡——
把歷史失敗混進現況，清單會越積越長，最後沒人看。歷史仍然查得到（納管紀錄那張表沒動）。

## 失敗原因分類

分類決定**下一步做什麼**，所以不能混成一句「失敗」：

| 分類 | 訊息特徵 | 下一步 |
|---|---|---|
| 帳密不對 | Permission denied／登入被拒 | 這台密碼不是預設值——找管理員要或走 PAM |
| 連不上 | timed out／refused／unreachable | 防火牆或機器不在——先確認網路 |
| 另有納管方式 | 另有納管方式（ESXi／儲存／網路…） | 不走 Linux 那套，改走它自己的收集路徑 |
| 腳本失敗 | stage=execute/verify | 進得去但腳本沒跑完——看輸出 |
| 其他 | 以上皆非 | 看原始訊息 |

⚠️ 分類只看訊息文字，認不出來就歸「其他」並**原樣附上訊息**——
猜錯分類比不分類更糟，那會把人帶去錯的方向。
"""
from __future__ import annotations

REASON_CREDENTIAL = "帳密不對"
REASON_UNREACHABLE = "連不上"
# 顯示字跟 /adopt 同一份（collect_dispatch.NOT_ONBOARDABLE_TEXT）；這支是純字串模組，
# 不 import 它以免為了一個字拉進整條收集鏈的相依，改字時兩邊一起改（測試釘住一致）。
REASON_NOT_ONBOARDABLE = "另有納管方式"
REASON_SCRIPT = "腳本失敗"
REASON_OTHER = "其他"

#: 下一步該做什麼。分類存在的唯一理由就是這個。
NEXT_STEP = {
    REASON_CREDENTIAL: "這台的密碼不是預設值——跟管理員要正確密碼，或走 PAM 取得後再納管",
    REASON_UNREACHABLE: "先確認機器在不在、防火牆有沒有開通（可用「偵測存活」）",
    REASON_NOT_ONBOARDABLE: "不走 Linux 那套（SSH 建本機帳號）——改走它自己的收集路徑"
                            "（ESXi 走 vCenter／RVTools 匯入、儲存走 SAN 收集），不是待辦",
    REASON_SCRIPT: "登入成功但腳本沒跑完——看納管紀錄裡的輸出找原因",
    REASON_OTHER: "看原始訊息判斷",
}

_CREDENTIAL_HINTS = ("permission denied", "登入被拒", "authentication", "帳號或密碼",
                     "password", "auth failed")
_UNREACHABLE_HINTS = ("timed out", "timeout", "refused", "unreachable", "no route",
                      "連不上", "無法連線", "connection reset")
#: 舊訊息寫「不納管」、新訊息寫「另有納管方式」——資料庫裡兩種都有，兩種都要認得。
#: 平台白名單（2026-10-19，onboard_engine.platform_refusal）：Linux 底的設備、非白名單發行版——也是不走這套
_NOT_ONBOARDABLE_HINTS = ("另有納管方式", "不納管", "不是允許的發行版", "偵測到設備標記")


#: 系統**刻意拒絕**納管的那幾種階段（VMware 天條、平台白名單；Windows 納管擋下另有納管方式的也寫 refused）
#: ——那不是「失敗待辦」，是「這台走別條路」，不進納管失敗清單（2026-10-22 審查）
REFUSED_STAGES = ("refused", "refused_platform")


def classify_reason(stage: str | None, message: str | None) -> str:
    """把失敗訊息歸成「下一步不同」的幾類。認不出來就回「其他」，不硬猜。"""
    msg = (message or "").lower()
    raw = message or ""
    if (stage or "") in REFUSED_STAGES or any(h in raw for h in _NOT_ONBOARDABLE_HINTS):
        return REASON_NOT_ONBOARDABLE
    if any(h in msg or h in raw for h in _CREDENTIAL_HINTS):
        return REASON_CREDENTIAL
    if any(h in msg or h in raw for h in _UNREACHABLE_HINTS):
        return REASON_UNREACHABLE
    if (stage or "") in ("execute", "verify"):
        return REASON_SCRIPT
    return REASON_OTHER


#: 沒登記、判不出大類的 IP。不知道就寫不知道，不可以歸進任何一類
OS_CLASS_UNKNOWN = "不知道（沒登記）"


def _os_class_map(conn, ips) -> dict[str, str]:
    try:
        import collect_dispatch as cd
        return cd.os_class_by_ip(conn, ips)
    except Exception:  # noqa: BLE001 - 讀不到就全部當不知道，不擋清單
        return {}


def elsewhere_ips(conn) -> dict[str, str]:
    """2-1 現在歸在「另有納管方式」那幾類的 IP → 頁簽名稱（OCP／ESXi／儲存／網路／其他／AS400）。

    2026-10-22 使用者：「我匯出 105 台，怎會包含我不能納的，例如 OCP」——這張清單是拿去
    跟同事要密碼的，不走 Linux 那套的機器不是待辦。判斷**直接讀 2-1 的頁簽**（同一份
    dispatch_tab，人工搬移也算），不另寫一套規則。讀不到（舊庫、沒收集過）就回空的：
    寧可多列，不可漏列真的待辦。
    """
    try:
        import collect_dispatch as cd
        lr = cd.latest_run(conn)
    except Exception:  # noqa: BLE001 - 收集模組讀不到就當沒有分類，照舊全列
        return {}
    out: dict[str, str] = {}
    for r in (lr or {}).get("results") or []:
        tab = r.get("tab")
        if tab in (cd.TAB_STORAGE, cd.TAB_NETWORK, cd.TAB_ESXI, cd.TAB_OCP, cd.TAB_NOK_OTHER) or (
                tab == cd.TAB_MAINFRAME and not r.get("mainframe_onboardable")):
            out[r["ip"]] = cd.TAB_LABEL.get(tab, tab)
    # 不在最近一輪收集結果裡的機器（2026-10-22 公司資料：主機名稱帶 .ocpt. 的 OCP 節點仍列在
    # 納管失敗——它們沒進最近那輪收集，上面讀不到頁簽）：直接用同一支判定看資產登記。
    # 人工改分類過的以人為準（改成可收集的就不排除）。
    try:
        import onboard_eligibility as oe
        import os_override
        ov_map = os_override.active_map(conn)
        esxi = oe.esxi_host_names(conn)
        for a in conn.execute("SELECT * FROM hardware WHERE ip IS NOT NULL AND ip != ''"):
            ip = a["ip"]
            if ip in out:
                continue
            ov = cd.override_of(ov_map, {"ip": ip, "hostname": a["hostname"],
                                         "asset_serial": a["asset_serial"]}) if ov_map else None
            if ov:
                if ov["tab"] in cd._TAB_NOK_KIND:
                    out[ip] = cd.TAB_LABEL.get(ov["tab"], ov["tab"])
                continue
            got = oe.judge_for_asset(conn, a, esxi_names=esxi)
            if got:
                out[ip] = cd.TAB_LABEL.get(cd._NOK_KIND_TAB.get(got[0], cd.TAB_NOK_OTHER), got[1])
    except Exception:  # noqa: BLE001 - 判定讀不到就只用上面的收集結果，寧可多列
        pass
    return out


def current_failures(conn, include_resolved: bool = False, include_elsewhere: bool = False,
                     elsewhere: dict[str, str] | None = None) -> list[dict]:
    """每台**最後一次**納管結果；預設只回現在仍然失敗的。

    `include_resolved=True` 時連「後來成功了」的也回（附 `resolved` 旗標），
    給「我想看看以前卡過哪幾台」用。
    預設排除「另有納管方式」的機器（見 elsewhere_ips）；`include_elsewhere=True` 才列。
    `elsewhere` 可由呼叫端先算好傳進來（同一個請求只讀一次 2-1 結果）。
    """
    rows = conn.execute(
        """
        SELECT a.target_ip, a.platform, a.login_user, a.ok, a.stage, a.message,
               a.trigger, a.triggered_by, a.created_at,
               (SELECT COUNT(*) FROM onboard_audit x
                 WHERE x.target_ip = a.target_ip AND x.ok = 0) AS fail_count,
               (SELECT MIN(created_at) FROM onboard_audit x
                 WHERE x.target_ip = a.target_ip AND x.ok = 0) AS first_failed_at
          FROM onboard_audit a
          JOIN (SELECT target_ip, MAX(id) AS last_id
                  FROM onboard_audit GROUP BY target_ip) last
            ON last.last_id = a.id
         ORDER BY a.target_ip
        """
    ).fetchall()

    hw = {r["ip"]: r for r in conn.execute(
        "SELECT ip, asset_serial, hostname, os, environment, physical_location, "
        "usage_unit, user_name, custodian, collect_ok FROM hardware "
        "WHERE ip IS NOT NULL AND ip != ''")}

    if include_elsewhere:
        elsewhere = {}
    elif elsewhere is None:
        elsewhere = elsewhere_ips(conn)
    os_cls = _os_class_map(conn, [r["target_ip"] for r in rows])
    # 人工改分類（2026-10-22 使用者：「原來是 LINUX 可手動調整到我指定群，每一個都要可以調」）：
    # 跟 2-1 同一張人工指定表，人改過的以人為準。搬去另有納管方式的會被上面 elsewhere 排掉。
    try:
        import collect_dispatch as cd
        import os_override
        ov_map = os_override.active_map(conn)
    except Exception:  # noqa: BLE001 - 讀不到就當沒有人工指定
        ov_map = {}
    # 同一台另一個 IP 已經納管成功（2026-10-22 使用者：「194.47 已經納管成功了為什麼還在納管失敗清單」——
    # CIA 登記 .47、實際在 .53，.53 納管成功、收得到；.47 那筆舊失敗不是待辦）。以主機名稱（去網域）認同一台。
    # 通用名（localhost、template…，machine_identity.json）不當同一台的證據——那種名字很多台共用
    import machine_identity
    import system_stats

    def _name_key(h):
        n = machine_identity.norm_name(h)
        return None if not n or machine_identity.is_generic(n) else system_stats.strip_domain(n.upper())

    ok_names = {k for x in conn.execute(
        "SELECT hostname FROM hardware WHERE collect_ok = 1 AND hostname IS NOT NULL AND TRIM(hostname) != ''")
        if (k := _name_key(x[0]))}
    out = []
    for r in rows:
        resolved = bool(r["ok"])
        h0 = hw.get(r["target_ip"])
        if not resolved and h0 and _name_key(h0["hostname"]) in ok_names:
            resolved = True
        if resolved and not include_resolved:
            continue
        h = hw.get(r["target_ip"])
        reason = "" if resolved else classify_reason(r["stage"], r["message"])
        if not include_elsewhere and (r["target_ip"] in elsewhere or reason == REASON_NOT_ONBOARDABLE):
            continue
        ov = cd.override_of(ov_map, {"ip": r["target_ip"], "hostname": h["hostname"] if h else None,
                                     "asset_serial": h["asset_serial"] if h else None}) if ov_map else None
        cls = os_cls.get(r["target_ip"]) or OS_CLASS_UNKNOWN
        if ov:
            cls = {cd.TAB_SSH: "Linux", cd.TAB_WINRM: "Windows"}.get(ov["tab"]) or ov["os_type"]
        out.append({
            "ip": r["target_ip"],
            "asset_serial": h["asset_serial"] if h else None,
            "hostname": h["hostname"] if h else None,
            "os": h["os"] if h else None,
            # 作業系統大類（2026-10-22 使用者：「把 OS 分類出來」「我可以自己選 OS 匯出」）——
            # 跟 2-1 大型主機頁簽同一支（collect_dispatch.os_class_by_ip）；沒登記的講不知道
            "os_class": cls,
            "os_override": ({"tab": ov["tab"], "os_type": ov["os_type"], "by": ov.get("by"),
                             "at": ov.get("at"), "reason": ov.get("reason")} if ov else None),
            "environment": h["environment"] if h else None,
            "physical_location": h["physical_location"] if h else None,
            # 要找誰問密碼——這才是這張清單的用途
            "department": h["usage_unit"] if h else None,
            "contact": h["user_name"] if h else None,
            "custodian": h["custodian"] if h else None,
            "platform": r["platform"],
            "login_user": r["login_user"],
            "stage": r["stage"],
            "message": r["message"],
            "reason": reason,
            "next_step": NEXT_STEP.get(reason, ""),
            "fail_count": r["fail_count"] or 0,
            "first_failed_at": r["first_failed_at"],
            "last_tried_at": r["created_at"],
            "last_trigger": r["trigger"],
            "last_by": r["triggered_by"],
            "resolved": resolved,
            # 納管失敗但收集帳號其實通得了（以前佈過）——這種不是真的待辦
            "collect_ok": (h["collect_ok"] if h else None),
            "registered": h is not None,
        })
    return out


def summary(conn, elsewhere: dict[str, str] | None = None) -> dict:
    """依原因分組的台數。畫面上要一眼看出「幾台是密碼問題」。"""
    if elsewhere is None:
        elsewhere = elsewhere_ips(conn)
    all_items = current_failures(conn, include_elsewhere=True)
    items = [i for i in all_items
             if i["ip"] not in elsewhere and i["reason"] != REASON_NOT_ONBOARDABLE]
    by_reason: dict[str, int] = {}
    by_os_class: dict[str, int] = {}
    for i in items:
        by_reason[i["reason"]] = by_reason.get(i["reason"], 0) + 1
        by_os_class[i["os_class"]] = by_os_class.get(i["os_class"], 0) + 1
    return {
        "total": len(items),
        "by_os_class": by_os_class,
        "by_reason": by_reason,
        # 密碼不是預設值的那批——使用者真正要的就是這個數字
        "credential": by_reason.get(REASON_CREDENTIAL, 0),
        # 排除了幾台要講出來（不能讓人以為那幾台消失了）
        "excluded_elsewhere": len(all_items) - len(items),
    }


EXPORT_HEADERS = ["IP", "主機名稱", "資產序號", "機房", "環境", "作業系統大類", "作業系統",
                  "使用單位（部門）", "使用者（窗口）", "保管者",
                  "失敗原因", "下一步", "最後訊息", "登入帳號", "階段",
                  "失敗次數", "第一次失敗", "最後一次嘗試"]


#: 搜尋框找的欄位（前端 fSearchKeys 同一組，畫面與匯出才會是同一批）
SEARCH_KEYS = ("ip", "hostname", "asset_serial", "physical_location", "department", "contact",
               "custodian", "os", "os_class", "reason", "next_step")


def matches_q(item: dict, q: str | None) -> bool:
    """搜尋框：空白分隔的每個字都要出現在任一欄（不分大小寫）。"""
    if not q:
        return True
    hay = " ".join(str(item.get(k) or "") for k in SEARCH_KEYS).lower()
    return all(t in hay for t in q.lower().split())


def export_rows(conn, os_classes=None, reason: str | None = None, q: str | None = None) -> list[list]:
    """`os_classes`：只匯出這幾個作業系統大類；`reason`：只匯出這個失敗原因；`q`：搜尋框的字。
    都是畫面上選的篩選——匯出的檔案＝畫面上看到的那幾台。空＝不篩。"""
    want = set(os_classes or ())
    return [[i["ip"], i["hostname"], i["asset_serial"], i["physical_location"],
             i["environment"], i["os_class"], i["os"], i["department"], i["contact"], i["custodian"],
             i["reason"], i["next_step"], i["message"], i["login_user"], i["stage"],
             i["fail_count"], i["first_failed_at"], i["last_tried_at"]]
            for i in current_failures(conn)
            if (not want or i["os_class"] in want) and (not reason or i["reason"] == reason)
            and matches_q(i, q)]


# ===== 「上次失敗」小標籤（2026-10-18 使用者：「如果有失敗，提醒上次什麼時間納管失敗，
# 失敗理由是什麼(很簡略)」）=====
#
# 上面 classify_reason 的分類是給「下一步做什麼」用的（五類），這裡是給畫面上**一眼看懂**
# 的短字——拆得更細（沒有 sudo／帳號被鎖／逾時 各自一類），但一樣只看訊息文字、
# 認不出來就回「其他」，完整訊息另外給（滑過看得到），不硬猜。
#
# 所有納管入口都寫同一張 onboard_audit（網頁一鍵、批次一次納管＝逐台打同一支 API、
# 重新納管、本機執行＋驗證 verify、取得納管指令自動偵測 paste、排程 auto），
# 所以「最後一次」只在這裡算一次，2-1 的待納管／已納管／一次納管視窗都讀這一份。
# 撤銷（trigger=revoke）不是「納管嘗試」，不算在內——撤銷成功不該把上次失敗洗掉，
# 撤銷失敗也不是「納管失敗」。

SHORT_BAD_CRED = "密碼錯誤"
SHORT_LOCKED = "帳號被鎖"
#: AIX 的「publickey,password 都被拒」：分不出是密碼錯還是 account_locked——兩個都講，不硬猜一個
SHORT_BAD_CRED_OR_LOCKED = "密碼錯誤或帳號被鎖"
SHORT_SUDO = "沒有 sudo（非 root）"
SHORT_NO_PASSWORD_AUTH = "不接受密碼登入"
SHORT_UNREACHABLE = "連不上"
SHORT_TIMEOUT = "逾時"
SHORT_HOSTKEY = "主機金鑰變更"
SHORT_CIPHER = "加密演算法不相容"
SHORT_NOT_SSH = "22 埠不是 SSH"
SHORT_COLLECTOR = "收集端設定問題"
SHORT_NAME_TOO_LONG = "帳號名太長（AIX）"
SHORT_PLATFORM = "平台不支援"
SHORT_PLATFORM_MISMATCH = "平台判錯"
SHORT_COLLECT_LOGIN = "收集帳號連不進去"
SHORT_COLLECT_EMPTY = "收集沒收到資料"
SHORT_ROOT_KEY = "root 金鑰標記失敗"
SHORT_ACCOUNT_MIGRATE = "收集帳號換新失敗"
SHORT_VMWARE = "VMware 不可納管"
SHORT_PLATFORM_BLOCKED = "平台不在白名單"
SHORT_NOT_ONBOARDABLE = "另有納管方式"
SHORT_SCRIPT_PREFIX = "腳本失敗："
SHORT_OTHER = "其他"

#: 階段的白話（「腳本失敗：<階段>」用）
_STAGE_TEXT = {"execute": "執行", "verify": "驗證", "probe": "探測", "bootstrap": "建帳號",
               "collect": "收集"}

#: 依序比對，先命中先算（全部比小寫後的訊息）。順序的理由：
#:   - 收集端自己的問題（金鑰檔、收集器位址、paramiko）最先：跟目標主機無關，歸成「密碼／連不上」
#:     會把人帶去查一台沒問題的機器
#:   - 「逾時被中止——多半是 sudo 在等密碼」排在 sudo 前面：它講的是逾時，sudo 只是推測
#:   - 主機金鑰排在「連線失敗」前面：`Host key verification failed` 是 OpenSSH 的連線層字樣
#:   - sudo 排在密碼前面：「sudo 不接受這個密碼」是**進得去**、卡在提權，不是登入密碼錯
#:   - AIX 提示（「AIX 常見原因：收集帳號被鎖」）是**推測**不是事實，所以它不算「帳號被鎖」，
#:     只算「密碼錯誤或帳號被鎖」；真的 `account_locked` 回報（verify 階段 lsuser 的輸出）才算被鎖
#: 這些字樣全部來自各版 onboard_engine／_app_shared／routers.onboard 實際寫進 onboard_audit 的訊息
#: （2026-08 起各版本，含 sshpass 時代的「連線／執行失敗：<例外>」與 2026-09-22 AIX 那批）。
_SHORT_RULES = (
    (SHORT_VMWARE, ("esxi 不可納管", "vcenter", "vcsa", "vmware")),
    (SHORT_PLATFORM_BLOCKED, ("不是允許的發行版", "偵測到設備標記")),
    (SHORT_NOT_ONBOARDABLE, ("另有納管方式", "不納管")),
    (SHORT_COLLECTOR, ("讀不到收集端公鑰", "收集金鑰", "收集器位址", "asset_collector_ip",
                       "paramiko 沒裝好", "collector_key")),
    (SHORT_TIMEOUT, ("逾時被中止",)),
    (SHORT_HOSTKEY, ("host key verification failed", "主機金鑰", "remote host identification has changed")),
    (SHORT_PLATFORM_MISMATCH, ("判定平台與實際不符",)),
    (SHORT_BAD_CRED_OR_LOCKED, ("aix 常見原因",)),
    (SHORT_LOCKED, ("account_locked: true", "account_locked=true", "account is locked",
                    "account locked", "帳號被鎖", "帳號已鎖", "已被鎖定", "帳號鎖定")),
    (SHORT_SUDO, ("sudo", "不是 root", "需要 root", "requiretty", "sudoers", "a terminal is required")),
    (SHORT_NO_PASSWORD_AUTH, ("no supported authentication", "登入方式不被接受", "不允許密碼登入")),
    (SHORT_BAD_CRED, ("permission denied", "登入被拒", "authentication", "帳號或密碼",
                      "密碼都不對", "密碼不對", "password", "auth failed", "登入失敗")),
    (SHORT_TIMEOUT, ("逾時", "timed out", "timeout")),
    (SHORT_UNREACHABLE, ("refused", "拒絕連線", "unreachable", "no route", "路由不通",
                         "連不上", "連不到", "無法連線", "connection reset", "解析不到",
                         "could not resolve", "connection closed", "連線中斷")),
    (SHORT_CIPHER, ("加密演算法不相容", "key exchange", "no matching", "unable to agree",
                    "no acceptable")),
    (SHORT_NOT_SSH, ("ssh protocol banner", "讀不到 ssh 服務的開場訊息", "error reading ssh protocol")),
    (SHORT_NAME_TOO_LONG, ("max_logname", "mkuser 會直接拒絕")),
    (SHORT_PLATFORM, ("未知平台", "未支援的平台")),
)

#: 訊息認不出來時，看階段（這些階段本身就說明了是哪一步出事）
_STAGE_SHORT = {"root_key": SHORT_ROOT_KEY, "account": SHORT_ACCOUNT_MIGRATE}


def short_reason(stage: str | None, message: str | None) -> str:
    """失敗訊息 → 一個短字（畫面標籤用）。

    先看訊息字樣（_SHORT_RULES），再看階段：認得出階段就講「腳本失敗：<階段>」／
    「收集沒收到資料」這種**至少講得出卡在哪一步**的字；兩者都認不出來才回「其他」。
    完整訊息另外給（滑過看得到），這裡不硬猜。
    """
    st = (stage or "").strip()
    if st == "refused":          # onboard_engine.ESXI_REFUSED_STAGE
        return SHORT_VMWARE
    if st == "refused_platform":  # onboard_engine.PLATFORM_REFUSED_STAGE
        return SHORT_PLATFORM_BLOCKED
    # 納管成功之後的收尾步驟（root 金鑰標記／收集帳號換新）：階段本身就講清楚了，先認階段
    if st in _STAGE_SHORT:
        return _STAGE_SHORT[st]
    low = (message or "").lower()
    # 納管後那一輪收集失敗（_collect_diagnosis 組的「收集失敗：送出平台=…」）：這時登入的是
    # **收集帳號**，不是使用者打的密碼——原始錯誤裡的 Permission denied 不可以講成「密碼錯誤」
    if "收集失敗：送出平台" in low:
        if "判定平台與實際不符" in low:
            return SHORT_PLATFORM_MISMATCH
        if any(n in low for n in ("permission denied", "authentication", "登入被拒", "auth failed")):
            return SHORT_COLLECT_LOGIN
        if any(n in low for n in ("timed out", "逾時", "refused", "no route", "unreachable")):
            return SHORT_UNREACHABLE
        return SHORT_COLLECT_EMPTY
    for label, needles in _SHORT_RULES:
        if any(n in low for n in needles):
            return label
    # 「收集失敗：送出平台=…、收到欄位=0 個」（納管後收集／本機執行後驗證，2026-09-22 起）
    if low.startswith("收集失敗") or st == "collect":
        return SHORT_COLLECT_EMPTY
    if st == "connect":
        return SHORT_UNREACHABLE if not low else "連線失敗"
    if st in _STAGE_TEXT:
        return SHORT_SCRIPT_PREFIX + _STAGE_TEXT[st]
    return SHORT_OTHER


def last_attempts(conn, ips) -> dict[str, dict]:
    """這批 IP 各自**最後一次納管嘗試**（不含撤銷）。回 {ip: {...}}；沒試過的 IP 不在回傳裡。

    每筆帶 ok／created_at／trigger／login_user／platform，失敗的另帶 `fail`：
    {at, short, message, stage, trigger, fail_count}。成功的 `fail` 是 None——
    後來成功了，上次失敗的標籤就該消失。
    `serial`：這個 IP 現在掛在哪筆資產（manage_state.serial_for_ip，全站同一把尺）。
    """
    import manage_state

    ip_list = list(dict.fromkeys(str(x).strip() for x in (ips or []) if x and str(x).strip()))
    out: dict[str, dict] = {}
    for i in range(0, len(ip_list), 400):        # SQLite 參數上限分批
        chunk = ip_list[i:i + 400]
        qs = ",".join("?" * len(chunk))
        try:
            rows = conn.execute(
                f"""
                SELECT a.target_ip, a.platform, a.login_user, a.ok, a.stage, a.message,
                       a.trigger, a.created_at,
                       (SELECT COUNT(*) FROM onboard_audit x
                         WHERE x.target_ip = a.target_ip AND x.ok = 0
                           AND COALESCE(x.trigger, '') <> 'revoke') AS fail_count
                  FROM onboard_audit a
                  JOIN (SELECT target_ip, MAX(id) AS last_id FROM onboard_audit
                         WHERE target_ip IN ({qs}) AND COALESCE(trigger, '') <> 'revoke'
                         GROUP BY target_ip) last
                    ON last.last_id = a.id
                """, chunk).fetchall()
        except Exception:  # noqa: BLE001 - 舊庫沒有這張表：當作都沒試過（回空，不是假的 0）
            return {}
        for r in rows:
            ok = bool(r["ok"])
            out[r["target_ip"]] = {
                "ip": r["target_ip"], "ok": 1 if ok else 0, "created_at": r["created_at"],
                "trigger": r["trigger"], "login_user": r["login_user"], "platform": r["platform"],
                "serial": manage_state.serial_for_ip(conn, r["target_ip"]),
                "fail": None if ok else {
                    "at": r["created_at"], "short": short_reason(r["stage"], r["message"]),
                    "message": (r["message"] or "")[:500], "stage": r["stage"],
                    "trigger": r["trigger"], "fail_count": r["fail_count"] or 1,
                },
            }
    return out
