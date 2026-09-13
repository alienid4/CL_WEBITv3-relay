"""系統(APID) × 環境／機房 統計，可下鑽到機房、再到 AP／DB。

2026-09-10 使用者要求：「一頁看系統 × 環境／機房，預設列前 10 大系統其餘收起來，
點一個系統看它在三個機房各幾台，再進一步分 AP 主機／DB 主機。」

## 每個數字的證據強度不同，畫面必須講得出來是怎麼來的

| 層 | 依據 | 強度 |
|---|---|---|
| 系統 × 環境／機房 | `business_system` 對照表 ＋ `hardware` 登記欄位 | **登記**（人填的） |
| 角色（AP／DB） | 機器上實際跑的服務（`host_service.process`） | **證據** |
| 角色（退而求其次） | 用途說明／機型／OS 的關鍵字 | **推論** |
| 角色（都不行） | 「資料不完整」 | **不完整不等於沒資料** |

⚠️ 2026-09-10 使用者兩次糾正這一欄的用字，兩次都對：

1. 原本叫「未分類」——他指出那把「還沒去看」跟「看了也不知道」混在一起。
2. 改成「未納管，還沒收到服務」之後他又說：「**目前就算沒有納管，資料還是會在
   啊，不一定要納管才能統計出來。我可以接受有些資訊不完整，你直接寫『不完整』
   就好，但不能因為沒納管，你就去說這都沒資料。**」

第二次的重點：登記資料（OS、用途說明、機型、機房、環境）**本來就在**，
缺的只是判角色的線索。寫成「還沒收到服務」會讀成「這台什麼都沒有」——那是假的，
而且會讓人以為統計要等納管做完才有意義。**納管只是補齊線索的其中一條路。**

所以：欄位名＝「資料不完整」（事實）；有沒有納管＝**原因**，另外統計、放說明裡講。

## 機房名稱要正規化

同一個機房在資料裡有兩種寫法（`02_內湖機房` 與 `內湖機房`），不合併的話
同一個機房會被算成兩個。這裡只做「去掉前面的編號、去掉空白」這種
**看得懂也講得出來的**正規化，不做模糊比對——猜錯了沒人發現。
"""
from __future__ import annotations

import re
import sqlite3

#: 機房名稱前面的排序編號（`02_內湖機房` 的 `02_`）。只脫這個，不做模糊比對。
_LOC_PREFIX = re.compile(r"^\s*\d+[_\-\s]*")

#: 判 DB 的線索。分開列是為了讓每一條都能被質疑與修改。
_DB_HINTS = re.compile(
    r"\bdb\b|database|oracle|mysql|mariadb|mssql|sql\s*server|postgre|mongo|redis|"
    r"exadata|\boda\b|資料庫", re.I)
#: 判 AP 的線索。**先判 DB 再判 AP**——`web` 這種字太泛，會把資料庫的網管介面也吃進來。
_AP_HINTS = re.compile(
    r"\bap\b|\bweb\b|\bapi\b|tomcat|weblogic|websphere|\biis\b|nginx|apache|"
    r"應用|前端|網站", re.I)

#: 容器平台節點。2026-09-10 查 221 才發現：「技術中台」348 台裡有 310 台是
#: OpenShift 節點（用途說明寫 OCP Worker／Infra／Master、OS 是 CoreOS／RHCOS）。
#: 它既不是 AP 也不是 DB，硬歸進去兩邊都不對。
_PLATFORM_HINTS = re.compile(
    r"\bocp\b|openshift|coreos|rhcos|kubernetes|\bk8s\b|vmware\s*esxi|\besxi\b|"
    r"hypervisor|虛擬化", re.I)
#: 網通／儲存設備。這些本來就不該被問「是 AP 還是 DB」。
_DEVICE_HINTS = re.compile(
    r"網路設備|儲存設備|fortigate|big-?ip|idrac|\bapic\b|aten|san\s*switch", re.I)

#: 從機器上實際跑的服務判角色——**這是證據，不是推論**（行程名是機器自己講的）。
_SVC_DB = re.compile(r"oracle|tnslsnr|mysqld|mariadb|postgres|mongod|redis-server|"
                     r"sqlservr|db2sysc", re.I)
_SVC_AP = re.compile(r"tomcat|nginx|httpd|apache|node|\biis\b|w3wp|weblogic|websphere", re.I)

#: LOG 主機（使用者 2026-09-10：「LOG機要獨立分出來」）。
#: 要排在 AP 前面比對——log server 上常常也跑 web 介面（Kibana／Graylog），
#: 順序反了會被標成 AP。
_LOG_HINTS = re.compile(
    r"\blog\b|syslog|rsyslog|splunk|logstash|kibana|graylog|elastic|\belk\b|"
    r"qradar|arcsight|\bsiem\b|日誌|稽核紀錄", re.I)

ROLE_DB = "DB"
ROLE_AP = "AP"
ROLE_LOG = "LOG"
ROLE_PLATFORM = "平台節點"
ROLE_DEVICE = "網通／儲存設備"
#: 判不出角色時就寫「資料不完整」——**不要寫成「沒資料」**。
#:
#: 2026-09-10 使用者糾正：「目前就算沒有納管，資料還是會在啊，不一定要納管才能
#: 統計出來。我可以接受有些資訊不完整，你直接寫『不完整』就好，但不能因為沒納管，
#: 你就去說這都沒資料。」
#:
#: 他是對的：登記資料（OS、用途說明、機型、機房、環境）本來就在，
#: 缺的只是「足以判斷 AP 還是 DB 的線索」。把它標成「還沒收到服務」會讀成
#: 「這台什麼都沒有」，那是假的，而且會讓人以為統計要等納管做完才有意義。
#:
#: 納管只是**補齊線索的其中一條路**，不是統計的前提。所以：
#:   欄位名＝「資料不完整」（這是事實）
#:   有沒有納管＝**原因**，放在明細裡講，不當成標籤
ROLE_INCOMPLETE = "資料不完整"

#: 畫面上的固定順序＝講故事的順序：分得出來的先講，不完整的排最後。
ROLE_ORDER = (ROLE_AP, ROLE_DB, ROLE_LOG, ROLE_PLATFORM, ROLE_DEVICE, ROLE_INCOMPLETE)

#: 系統分兩類（使用者 2026-09-10：「基礎跟 AP 系統要分開」）。
#: 依據是 **APID 前綴**，不是人工判斷——221 實測 `I-` 13 個全是基礎設施
#: （Cisco、工作站、FortiGate、EMC Storage、IBM San Switch、HMC…），
#: `N-` 150 個是業務系統。
#:
#: ⚠️ 前綴不是完美的分類：`N-207 虛擬化平台`(93 台) 掛在 N- 底下，
#: 但它比較像基礎設施。**這裡刻意不做人工搬移**——搬了就是我在猜，猜錯不會有人
#: 發現，數字會一路錯下去。畫面上要標明「分類依據是 APID 前綴」，
#: 歸錯邊由使用者指出來再加設定。
KIND_INFRA = "infra"
KIND_AP = "ap"

#: 前綴分錯、由**使用者點名**搬過來的系統。不是我猜的——每一筆都要有出處。
#: 2026-09-10 使用者確認：`N-207 虛擬化平台`(93 台) 掛 N- 但實際是基礎設施。
#: 以後再發現歸錯邊的，加在這裡並註明是誰、什麼時候說的。
_KIND_OVERRIDE = {
    "N-207": KIND_INFRA,     # 虛擬化平台（使用者 2026-09-10 指定）
}

#: UAT／DEV 併進「測試」（使用者 2026-09-10）。
#: **合併規則要寫在畫面的註裡**——默默併會讓人以為原始資料就長這樣，
#: 之後有人拿去跟來源系統對帳會對不起來。
_ENV_MERGE = {
    "使用者測試(UAT)": "測試", "使用者測試": "測試", "UAT": "測試",
    "開發環境(DEV)": "測試", "開發環境": "測試", "DEV": "測試",
    "OA": "測試",
}


#: 基礎設施再分群。**編號與名稱都是使用者 2026-09-10 逐項定的**，不是我分的：
#:   1. 網路設備
#:   2. Storage —— **SAN switch 屬於此類**（他特別指定；名字裡有 switch，
#:      規則順序沒排對就會被歸到網路設備）
#:   3. VMware —— 虛擬化平台
#:   4. IBM system —— HMC
#:   5. 軟體 —— 軟體測試環境、軟體存放區
#:   6. 其他 —— 工作站、SSIS 測試機、印表機周邊設備
#:
#: 「未分群」留著當安全網：以後新增的系統如果對不上任何規則，會落在這裡並
#: 顯示在畫面上等人點名——**不要讓它自動塞進最像的那一群**，塞錯了沒人會發現。
#: 目前實際資料裡這一群是空的。
GROUP_NETWORK = "網路設備"
GROUP_STORAGE = "Storage"
GROUP_VMWARE = "VMware"
GROUP_IBM = "IBM system"
GROUP_SOFTWARE = "軟體"
GROUP_OTHER = "其他"
GROUP_UNGROUPED = "未分群"
INFRA_GROUP_ORDER = (GROUP_NETWORK, GROUP_STORAGE, GROUP_VMWARE,
                     GROUP_IBM, GROUP_SOFTWARE, GROUP_OTHER, GROUP_UNGROUPED)

#: ⚠️ 順序有意義：**Storage 要排在網路設備前面**。
#: `IBM San Switch` 名字裡有 switch，先比網路設備的話就會歸錯——
#: 這正是使用者特別交代「SAN switch 屬於 Storage」的原因。
_INFRA_GROUP_RULES = (
    (GROUP_STORAGE, re.compile(r"san\s*switch|storage|儲存|\bemc\b|netapp|\bsan\b", re.I)),
    (GROUP_NETWORK, re.compile(
        r"cisco|catalyst|fortigate|juniper|procurve|big-?ip|\bf5\b|switch|router|"
        r"網路|網管|lan\s*console", re.I)),
    (GROUP_IBM, re.compile(r"\bhmc\b|mainframe|\bibm\b|power\s*系統|主機管理", re.I)),
    (GROUP_SOFTWARE, re.compile(r"軟體|software", re.I)),
)

#: 規則判不出來、由**使用者點名**的。跟 `_KIND_OVERRIDE` 一樣：每一筆都要有出處。
#: 用 api_id 當鍵而不是名稱——名稱會被改，代碼不會。
_INFRA_GROUP_OVERRIDE: dict[str, str] = {
    # 使用者 2026-09-10：「工作站、SSIS測試機、印表機周邊設備 歸其他，
    #                    虛擬化平台歸 VMWARE」
    "I-003": GROUP_OTHER,      # 工作站（98 台）
    "I-100": GROUP_OTHER,      # SSIS 測試機（4 台）
    "I-021": GROUP_OTHER,      # 印表機周邊設備（1 台）
    "N-207": GROUP_VMWARE,     # 虛擬化平台（93 台）
    # 使用者 2026-09-10 第二輪：「5. 軟體（56 台）」＝原本落在未分群的那兩個。
    # 雖然關鍵字規則也抓得到「軟體」，仍然明列——**點名過的東西不要只靠規則**：
    # 規則改一次就可能悄悄改變分類，而這兩筆是他親口指定的。
    "I-004": GROUP_SOFTWARE,   # 軟體測試環境（52 台）
    "I-005": GROUP_SOFTWARE,   # 軟體存放區（4 台）
}


def infra_group(api_id: str | None, name: str | None = None) -> str:
    """基礎設施系統屬於哪一群。判不出來回「未分群」，**不硬塞**。"""
    key = (api_id or "").strip().upper()
    if key in _INFRA_GROUP_OVERRIDE:
        return _INFRA_GROUP_OVERRIDE[key]
    text = f"{api_id or ''} {name or ''}"
    for group, pat in _INFRA_GROUP_RULES:
        if pat.search(text):
            return group
    return GROUP_UNGROUPED


def system_kind(api_id: str | None) -> str:
    """`I-` 開頭＝基礎設施，其餘＝AP 業務系統；`_KIND_OVERRIDE` 優先。"""
    key = (api_id or "").strip().upper()
    if key in _KIND_OVERRIDE:
        return _KIND_OVERRIDE[key]
    return KIND_INFRA if key.startswith("I-") else KIND_AP


def kind_overrides() -> dict[str, str]:
    """人工搬過的清單——**畫面要顯示出來**。

    使用者看到「虛擬化平台」出現在基礎設施那頁，要有辦法知道它為什麼在那裡，
    否則下次有人問「這不是 N- 開頭嗎」就沒人答得出來。
    """
    return dict(_KIND_OVERRIDE)


def normalize_env(raw: str | None) -> str:
    """環境別正規化：UAT／DEV／OA 都算「測試」，空的算「未填」。"""
    s = (raw or "").strip()
    if not s:
        return "未填"
    return _ENV_MERGE.get(s, _ENV_MERGE.get(s.upper(), s))


def normalize_location(raw: str | None) -> str:
    """`02_內湖機房` 與 `內湖機房` 是同一個機房。空的就回「未填」，不要回空字串。

    回「未填」而不是空字串，是因為畫面上「未填 1141 台」跟一格空白是兩回事——
    前者看得出有一千多台沒登記機房，後者會被當成排版問題忽略。
    """
    s = (raw or "").strip()
    if not s:
        return "未填"
    return _LOC_PREFIX.sub("", s).strip() or "未填"


def role_from_services(services: list[str] | None) -> str | None:
    """從機器上實際跑的服務判角色——**這是證據**（機器自己講的行程名）。

    判不出來回 None，讓呼叫端往下走推論那條，不要在這裡就下結論。
    """
    if not services:
        return None
    text = " ".join(services)
    if _SVC_DB.search(text):
        return ROLE_DB
    if _SVC_AP.search(text):
        return ROLE_AP
    return None


def guess_role(*fields: str | None, onboarded: bool = True,
               services: list[str] | None = None, default_ap: bool = False) -> str:
    """判這台是什麼角色。**回傳值本身就說明了這個答案是怎麼來的。**

    順序（強證據優先）：
      1. 實際跑的服務 → AP／DB（**證據**）
      2. 網通／儲存設備（本來就不該問它是 AP 還是 DB）
      3. 平台節點（OpenShift／ESXi，既不是 AP 也不是 DB）
      4. 登記欄位的關鍵字 → AP／DB（**推論**）
      5. 都不行 → 「資料不完整」

    第 5 步的用字是使用者 2026-09-10 定的：「我可以接受有些資訊不完整，你直接寫
    『不完整』就好，但不能因為沒納管，你就去說這都沒資料。」

    **登記資料本來就在**（OS、用途、機型、機房、環境），缺的只是判角色的線索。
    寫成「還沒收到服務」會讀成「這台什麼都沒有」，那是假的。
    納管只是補齊線索的其中一條路，不是統計的前提。

    先判 DB 再判 AP：`web` 這類字太泛，資料庫主機的管理介面也常帶 web，
    順序反過來會把 DB 誤標成 AP。
    """
    by_svc = role_from_services(services)
    if by_svc:
        return by_svc
    text = " ".join(f or "" for f in fields)
    if _DEVICE_HINTS.search(text):
        return ROLE_DEVICE
    if _PLATFORM_HINTS.search(text):
        return ROLE_PLATFORM
    if _DB_HINTS.search(text):
        return ROLE_DB
    # LOG 要排在 AP 前面：log server 上常常也跑 web 介面（Kibana／Graylog），
    # 順序反了會被標成 AP
    if _LOG_HINTS.search(text):
        return ROLE_LOG
    if _AP_HINTS.search(text):
        return ROLE_AP
    # 業務系統（N-）判不出來時一律當 AP——使用者 2026-09-10：「除了 DB 以外
    # 都是 AP 主機」。母體本來就是業務系統，那個假設站得住。
    #
    # ⚠️ 但**只在最後這一步套用**：上面判得出設備／平台節點／DB 的照判，
    # 不被這條蓋掉。理由是那些有依據——把 OpenShift 節點標成 AP 之後，
    # 去問「這台 AP 是哪個系統的」會問不出答案。
    #
    # 基礎設施（I-）不套用：交換器、儲存設備本來就不是 AP 主機，
    # 判不出來就誠實寫「資料不完整」。
    if default_ap:
        return ROLE_AP
    return ROLE_INCOMPLETE


#: 系統類別（使用者 2026-09-10：「我想分第一類系統、第二類系統、第三類系統」）。
#:
#: **來源是使用者給的 APID 分級表**（docs/APID.xlsx，89 個系統），經「系統類別對照表」
#: 匯入存在 `business_system.sys_class`。原字串照存，例如：
#:   S1-第一類系統軟體（核心）／S1-第一類系統軟體／S2-第二類系統軟體／S3-第三類系統軟體
#:
#: 一開始我用「正式機的可用性取最高」推過一版，使用者隨即指出「昨天的 APID 表有寫
#: 哪個是第幾類」——**有權威來源就用來源，不用推的**。那版已拿掉。
#:
#: ⚠️ 分級表**不能寫死在程式裡**：那是公司的真實資料，而程式會同步到公開 relay。
#:
#: 清單以外的系統＝未分級（221 實測 68 個業務系統不在清單，多半是支援類：
#: 虛擬化平台、硬體遠端管理、壓測機、備份、防毒、監控）。
CLASS_UNRATED = "未分級"
CLASS_ORDER = ("第一類", "第二類", "第三類", CLASS_UNRATED)
_CLASS_RANK = {c: i for i, c in enumerate(CLASS_ORDER)}


def class_from_label(raw: str | None) -> tuple[str, bool]:
    """分級表的原字串 → (第幾類, 是不是核心)。認不出來回 (未分級, False)，不猜。"""
    s = (raw or "").strip()
    if not s:
        return CLASS_UNRATED, False
    up = s.upper()
    core = "核心" in s
    if up.startswith("S1") or "第一類" in s:
        return "第一類", core
    if up.startswith("S2") or "第二類" in s:
        return "第二類", core
    if up.startswith("S3") or "第三類" in s:
        return "第三類", core
    return CLASS_UNRATED, False


def _system_classes(conn) -> dict[str, dict]:
    """每個業務系統屬於第幾類。

    讀 business_system.sys_class（分級表匯入的，程式判的）＋ sys_class_override
    （稽核人員手動改的）。**有覆寫就蓋過**——分級表是「整份取代」匯入，會把 547 個
    未分級的系統一直維持未分級；手動分級要能撐過重匯，所以存成獨立覆寫欄（同帳號類型）。

    回 api_id → {cls（生效值）, core, override, computed（程式原判）}。
    欄位還不存在（遷移前的舊 DB）就當全部未分級，不讓整頁掛掉。
    """
    try:
        rows = conn.execute(
            "SELECT api_id, sys_class, sys_class_override FROM business_system "
            "WHERE sys_class IS NOT NULL OR sys_class_override IS NOT NULL").fetchall()
    except sqlite3.Error:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        computed, core = class_from_label(r["sys_class"])
        ovr = (r["sys_class_override"] or "").strip()
        out[r["api_id"]] = {
            "cls": ovr or computed, "core": core,
            "override": ovr or None, "computed": computed,
        }
    return out


def by_system(conn, limit: int | None = 10, kind: str | None = None) -> dict:
    """每個業務系統幾台，含環境別拆分。limit=None 代表全部。

    只算「對得上業務系統」的資產——對不上的另外用 `unmapped` 回報台數，
    不要混進來也不要靜默丟掉：使用者要知道這張表涵蓋了多少、漏了多少。
    """
    rows = conn.execute("""
        SELECT b.api_id, b.name, b.ap_department, h.environment AS env_raw, COUNT(*) AS n
        FROM hardware h
        JOIN business_system b ON b.api_id = h.api_id
        GROUP BY b.api_id, env_raw
    """).fetchall()

    classes = _system_classes(conn)
    systems: dict[str, dict] = {}
    for r in rows:
        if kind is not None and system_kind(r["api_id"]) != kind:
            continue
        s = systems.setdefault(r["api_id"], {
            "api_id": r["api_id"], "name": r["name"], "ap_department": r["ap_department"],
            "kind": system_kind(r["api_id"]),
            # 基礎設施再分群（網路設備／Storage／其他大型系統／未分群）。
            # AP 系統那邊用不到，但一律帶著，畫面自己決定要不要用。
            "group": infra_group(r["api_id"], r["name"]),
            "sys_class": (classes.get(r["api_id"]) or {}).get("cls", CLASS_UNRATED),
            # 第一類裡還分「核心」與否——原檔有這個資訊，不丟
            "sys_class_core": (classes.get(r["api_id"]) or {}).get("core", False),
            "sys_class_override": (classes.get(r["api_id"]) or {}).get("override"),
            "sys_class_computed": (classes.get(r["api_id"]) or {}).get("computed", CLASS_UNRATED),
            "total": 0, "by_env": {},
        })
        env = normalize_env(r["env_raw"])
        s["total"] += r["n"]
        s["by_env"][env] = s["by_env"].get(env, 0) + r["n"]

    # 排序（使用者 2026-09-10）：「用第一類先排，再排序正式機最多的」。
    # ⚠️ **一定要在這裡排，不能交給前端**：清單只回前 10 大，
    # 前端才排的話，那 10 個本身就是用錯的順序挑出來的。
    ordered = sorted(systems.values(), key=lambda x: (
        _CLASS_RANK.get(x["sys_class"], len(CLASS_ORDER)),
        -x["by_env"].get("正式", 0),
        -x["total"],
        x["api_id"]))
    total_assets = conn.execute("SELECT COUNT(*) AS n FROM hardware").fetchone()["n"]
    # mapped 一律算「對得上業務系統的全部台數」，不受 kind 篩選影響——
    # 不然切到基礎設施分頁時，「對不上 N 台」會突然暴增，看的人會以為資料壞了
    all_mapped = conn.execute(
        "SELECT COUNT(*) AS n FROM hardware h JOIN business_system b ON b.api_id = h.api_id"
    ).fetchone()["n"]
    mapped = sum(s["total"] for s in ordered)
    envs = sorted({e for s in ordered for e in s["by_env"]})

    return {
        "systems": ordered if limit is None else ordered[:limit],
        "system_count": len(ordered),
        "shown": len(ordered) if limit is None else min(limit, len(ordered)),
        "envs": envs,
        "kind": kind,
        "group_order": list(INFRA_GROUP_ORDER),
        "class_order": list(CLASS_ORDER),
        # 這個分頁涵蓋幾台
        "mapped": mapped,
        # 對不上業務系統的台數——這個數字要看得見，不然沒人知道這頁漏了多少。
        # 用全庫口徑算，不隨分頁變動。
        "unmapped": total_assets - all_mapped,
        "total_assets": total_assets,
    }


def _services_by_asset(conn) -> dict[str, list[str]]:
    """每台機器上收到的服務名。**只收機器自己講的行程名**（guess_source='process'）。

    埠號猜的不算——`8080` 可能是任何東西，拿它當「證據」就不是證據了。
    """
    out: dict[str, list[str]] = {}
    try:
        rows = conn.execute(
            "SELECT asset_serial, process, service_guess, guess_source FROM host_service "
            "WHERE asset_serial IS NOT NULL AND gone_at IS NULL").fetchall()
    except sqlite3.Error:
        return out
    for r in rows:
        name = r["process"] if r["guess_source"] == "process" else None
        if name:
            out.setdefault(r["asset_serial"], []).append(name)
    return out


def _onboarded_ips(conn) -> set[str]:
    """納管過的 IP。撤銷過的不算——以最後一筆成功動作為準。

    跟 `batch_onboard_service` 同一套判定；那邊改了這裡也要跟著改。
    """
    ips: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT target_ip, trigger FROM onboard_audit WHERE ok = 1 ORDER BY id").fetchall()
    except sqlite3.Error:
        return ips
    for r in rows:
        if r["trigger"] == "revoke":
            ips.discard(r["target_ip"])
        else:
            ips.add(r["target_ip"])
    return ips


def drilldown(conn, api_id: str, env: str | None = None) -> dict:
    """一個系統的下鑽：機房 × 環境，以及 AP／DB 推論。

    `env` 有值時只算那個環境的機器——對應畫面上「點某一格數字」的行為：
    使用者 2026-09-10「譬如我點 207，這 207 機房各占多少」。
    傳進來的值是**合併後**的環境名（測試已含 UAT／DEV）。
    """
    sys_row = conn.execute(
        "SELECT api_id, name, ap_department, ap_owner FROM business_system WHERE api_id = ?",
        (api_id,)).fetchone()
    if sys_row is None:
        raise ValueError(f"對照表裡沒有這個系統代碼：{api_id}")

    rows = conn.execute("""
        SELECT asset_serial, hostname, ip, physical_location, environment,
               asset_purpose, asset_name, device_model, os
        FROM hardware WHERE api_id = ?
    """, (api_id,)).fetchall()

    services = _services_by_asset(conn)
    onboarded = _onboarded_ips(conn)

    by_loc: dict[str, dict] = {}
    roles = {k: 0 for k in ROLE_ORDER}
    # 角色 × 機房的交叉（使用者 2026-09-10 要的形狀）：
    #     　　　板橋  內湖  敦南  合計
    #     AP    105   70    2     152
    #     DB    20    10    —     30
    # 每一列都要留著，即使是 0——**合計必須等於這個系統的總台數**，
    # 少一列使用者一加就發現對不起來，然後整張表都不能信了。
    role_matrix: dict[str, dict[str, int]] = {k: {} for k in ROLE_ORDER}
    counted = 0
    for r in rows:
        env_name = normalize_env(r["environment"])
        if env is not None and env_name != env:
            continue
        counted += 1
        loc = normalize_location(r["physical_location"])
        slot = by_loc.setdefault(loc, {"location": loc, "total": 0, "by_env": {}})
        slot["total"] += 1
        slot["by_env"][env_name] = slot["by_env"].get(env_name, 0) + 1
        role = guess_role(
            r["asset_purpose"], r["asset_name"], r["device_model"], r["os"],
            onboarded=(r["ip"] in onboarded),
            services=services.get(r["asset_serial"]),
            default_ap=(system_kind(api_id) == KIND_AP))
        roles[role] += 1
        role_matrix[role][loc] = role_matrix[role].get(loc, 0) + 1

    return {
        "api_id": sys_row["api_id"],
        "name": sys_row["name"],
        "ap_department": sys_row["ap_department"],
        "ap_owner": sys_row["ap_owner"],
        "kind": system_kind(sys_row["api_id"]),
        "env_filter": env,
        "total": counted,
        "locations": sorted(by_loc.values(), key=lambda x: (-x["total"], x["location"])),
        "roles": dict(roles),
        # 角色 × 機房交叉，列的順序固定（ROLE_ORDER），畫面直接照著畫
        "role_matrix": [
            {"role": k, "by_location": role_matrix[k], "total": roles[k]}
            for k in ROLE_ORDER
        ],
        "role_basis": "服務證據優先，其次用登記欄位推論",
    }


def assets_in_cell(conn, api_id: str, env: str | None = None,
                   location: str | None = None, role: str | None = None) -> list[dict]:
    """交叉表某一格是**哪幾台**（使用者 2026-09-10：「板橋機房 DB 有 18 台，
    點進去要知道是哪 18 台」）。

    為什麼要另外做一支、不能直接連到資產查詢頁：**角色不是資料庫欄位**，
    是這裡算出來的（服務證據／關鍵字推論）。資產查詢頁沒辦法用它篩選，
    硬連過去只會篩出不同的一批機器，數字對不起來——那比沒有連結更糟。

    回傳每台都帶 `role`，讓畫面能顯示「它為什麼被歸在這一格」。
    """
    rows = conn.execute("""
        SELECT asset_serial, hostname, ip, os, environment, physical_location,
               asset_purpose, asset_name, device_model
        FROM hardware WHERE api_id = ?
    """, (api_id,)).fetchall()
    services = _services_by_asset(conn)
    onboarded = _onboarded_ips(conn)

    out = []
    for r in rows:
        if env is not None and normalize_env(r["environment"]) != env:
            continue
        loc = normalize_location(r["physical_location"])
        if location is not None and loc != location:
            continue
        is_on = r["ip"] in onboarded
        role_name = guess_role(
            r["asset_purpose"], r["asset_name"], r["device_model"], r["os"],
            onboarded=is_on, services=services.get(r["asset_serial"]),
            default_ap=(system_kind(api_id) == KIND_AP))
        if role is not None and role_name != role:
            continue
        out.append({
            "asset_serial": r["asset_serial"],
            "hostname": r["hostname"],
            "ip": r["ip"],
            "os": r["os"],
            "environment": normalize_env(r["environment"]),
            "location": loc,
            "role": role_name,
            "onboarded": is_on,
            # 判角色時看到的東西一起回去——使用者問「為什麼這台算 DB」時
            # 要答得出來，不能只給一個結論
            "purpose": r["asset_purpose"],
            "device_model": r["device_model"],
        })
    out.sort(key=lambda x: (x["hostname"] or "", x["ip"] or ""))
    return out


def lookup_host(conn, q: str, limit: int = 20) -> list[dict]:
    """輸入主機名或 IP，回答「它屬於哪個系統」。

    這是下鑽的**反方向**（使用者 2026-09-10：「SEC01 是哪個 APID，我怎麼找不到」）。
    分佈統計本來只能由上往下鑽（系統 → 機房 → 哪幾台），知道機器卻找不到系統。

    主機名比對**不分大小寫**：真實資料裡 `sec01` 是小寫，人打 `SEC01`；
    兩邊都要找得到——這是查不到東西時最常見、也最沒必要的原因。
    """
    kw = (q or "").strip()
    if not kw:
        return []
    like = f"%{kw}%"
    rows = conn.execute("""
        SELECT h.asset_serial, h.hostname, h.ip, h.api_id, h.environment,
               h.physical_location, h.asset_purpose, h.asset_name, h.device_model, h.os,
               b.name AS system_name, b.ap_department
        FROM hardware h
        LEFT JOIN business_system b ON b.api_id = h.api_id
        WHERE lower(COALESCE(h.hostname, '')) LIKE lower(?)
           OR COALESCE(h.ip, '') LIKE ?
           OR lower(COALESCE(h.asset_serial, '')) LIKE lower(?)
        ORDER BY h.hostname
        LIMIT ?
    """, (like, like, like, limit)).fetchall()

    services = _services_by_asset(conn)
    onboarded = _onboarded_ips(conn)
    out = []
    for r in rows:
        api_id = (r["api_id"] or "").strip()
        out.append({
            "asset_serial": r["asset_serial"],
            "hostname": r["hostname"],
            "ip": r["ip"],
            "api_id": api_id or None,
            "system_name": r["system_name"],
            "ap_department": r["ap_department"],
            # 沒有 api_id 的機器要講清楚是「沒填」而不是「查不到系統」——
            # 前者是資料缺，後者是對照表缺，要補的地方不同
            "kind": system_kind(api_id) if api_id else None,
            "group": infra_group(api_id, r["system_name"]) if api_id else None,
            "environment": normalize_env(r["environment"]),
            "location": normalize_location(r["physical_location"]),
            "role": guess_role(
                r["asset_purpose"], r["asset_name"], r["device_model"], r["os"],
                onboarded=(r["ip"] in onboarded),
                services=services.get(r["asset_serial"]),
                default_ap=bool(api_id) and system_kind(api_id) == KIND_AP),
        })
    return out


def role_coverage(conn) -> dict:
    """全庫的角色分佈——**這個數字要放在畫面上**。

    使用者要看的是「這個分類可不可信」，而不是一張看起來分好了的圓餅圖。

    `incomplete` 是判不出角色的台數。**它不代表那些機器沒有資料**——
    登記資料都在，只是不足以判斷 AP 還是 DB。
    `incomplete_onboarded` / `incomplete_not_onboarded` 把原因拆開，
    讓人知道「還有一條路沒走」還是「走完了還是不知道」。
    """
    services = _services_by_asset(conn)
    onboarded = _onboarded_ips(conn)
    roles = {k: 0 for k in ROLE_ORDER}
    # 「資料不完整」那批**為什麼**不完整——這是原因不是標籤，所以另外算。
    # 已納管的代表「進去看過了還是分不出來」，那是真的要人判斷；
    # 未納管的代表「還有一條路沒走」。兩者要做的事不同，但都不叫「沒資料」。
    incomplete_onboarded = 0
    incomplete_not_onboarded = 0
    for r in conn.execute(
            "SELECT asset_serial, ip, api_id, asset_purpose, asset_name, device_model, os "
            "FROM hardware"):
        is_on = r["ip"] in onboarded
        # 只有「掛在業務系統底下」的才套「不是 DB 就是 AP」。
        # 沒有 api_id 的機器連屬於哪個系統都不知道，不能替它假設角色。
        role = guess_role(
            r["asset_purpose"], r["asset_name"], r["device_model"], r["os"],
            onboarded=is_on, services=services.get(r["asset_serial"]),
            default_ap=bool((r["api_id"] or "").strip())
            and system_kind(r["api_id"]) == KIND_AP)
        roles[role] += 1
        if role == ROLE_INCOMPLETE:
            if is_on:
                incomplete_onboarded += 1
            else:
                incomplete_not_onboarded += 1
    total = sum(roles.values())
    return {
        "roles": dict(roles),
        "role_order": list(ROLE_ORDER),
        "total": total,
        "incomplete": roles[ROLE_INCOMPLETE],
        # 不完整的原因拆開講（不是分類，是說明）
        "incomplete_onboarded": incomplete_onboarded,
        "incomplete_not_onboarded": incomplete_not_onboarded,
        "classified_pct": round(
            100 * (total - roles[ROLE_INCOMPLETE]) / total, 1) if total else 0.0,
    }
