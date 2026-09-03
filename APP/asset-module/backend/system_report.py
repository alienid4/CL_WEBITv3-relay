"""系統組月報：三張表，畫面直接複製貼進部門報告。

## 為什麼要做這個

使用者每個月要交部門報告，裡面固定有三張表——平台數量與生命週期、叢集分類、
虛擬化與 AIX/IBM i 環境資訊。原本是**人工統計**的。他 2026-08-21 的原話：
「以後我就 COPY 畫面，不用再自己統計」。

所以這一頁的成敗標準不是「數字算得出來」，是**算出來的數字他敢直接貼進報告**。
那要求兩件事：

1. **口徑要跟他的報告一致**，不然貼上去被主管問「怎麼跟上個月差 300 台」答不出來
2. **要講得出數字怎麼來的**——排除了什麼、依據是什麼、資料多舊

## 三張表各自的資料來源與可信度

| 表 | 來源 | 可信度 |
|---|---|---|
| 表1 平台生命週期 | `hardware` ＋ `eos` 對照表 | 證據（登記值）＋ 官方 EOS 日期 |
| 表2 叢集分類 | RVTools `vHost` 的 Cluster 欄 ＋ **名稱關鍵字分類** | 臺數是證據；**分類是推論** |
| 表3 虛擬化環境 | RVTools `vHost`（Host/Cluster/ESX Version/VI SDK Server） | 證據 |

表2 的「交易服務／Log服務」RVTools 沒有這個欄位，是從叢集名稱推的
（`BQ_PROD_LOG_Cluster` 的 LOG）。2026-08-21 拿 221 正式資料驗過：板橋 Log 服務
算出 10 台，跟使用者月報上的 10 台完全吻合。但**這仍然是推論不是證據**——
哪天有叢集沒照命名慣例就會歸錯，所以畫面要標明依據。

## 資料新鮮度是這一頁的頭號風險

三張表裡有兩張的底層是 RVTools，而 RVTools 是**手動匯出的快照**。
2026-08-21 當下 221 上那批是 **7/30** 匯出的，貼進 8 月報告會有三週落差
（使用者報告上 BQ_PROD_A 是 10 台，系統算 13 台，差異就是這樣來的）。
所以 `meta()` 一定要回匯出日期，前端固定顯示，不給關。
「數字看起來很精確」跟「數字是新的」是兩件事。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731 - 本地時間，決策T6

# 表1 的列順序。固定寫死是刻意的：月報的表格順序不該每個月不一樣，
# 而且「這個月某類變 0 台」時那一列還是要在（消失會被誤讀成「沒這個東西」）。
PLATFORM_ORDER = [
    "Windows Server",
    "Windows Client",
    "Linux",
    "VMware ESXi",
    "IBM AIX",
    "Windows 未明版本",
    "IBM i",
]

# EOS 五態。使用者 2026-08-21 確認的定義。
#
# 「尚未公布」跟「需確認」原本混成同一種（都叫需確認），使用者反問：「這些資訊
# 資料庫裡不是都查過了嗎？為什麼還要再查？」——查了，但要分兩種完全不同的
# 「查了沒結果」：
# - 需確認：canonical 對不到 EOS 表任何一筆，我們真的不認識這個產品，值得去查
# - 尚未公布：EOS 表裡有這個產品（VMware ESXi 8.0／AIX 7.2／IBM i 各版本…），
#   是已知、現行受支援的東西，只是官方沒公開發布終止支援日期——這不是「不知道」，
#   是「官方自己都還沒講」，不該跟前者用同一個標籤，會讓人誤以為系統沒查過。
STATUS_ORDER = ["已EOS", "一年內EOS", "尚未公布", "需確認", "支援中"]
_STATUS_MAP = {"expired": "已EOS", "upcoming": "一年內EOS", "ok": "支援中", "unknown": "需確認"}


def _eos_lookup(canonical: str | None, product: str | None = None) -> tuple[str | None, str | None]:
    """依 canonical 查 EOS，回 (狀態標籤, 日期)。三張表(表1平台/表2叢集版本/表3
    實體主機)都走這支，狀態判斷邏輯只寫一次——不然三處各自判，遲早有一處漏改。

    product 是退路：IBM i 沒有「IBM i 7.3」這種逐版本的 EOS 條目（官方本來就
    不逐版公告），只有一筆籠統的「IBM i」描述性條目——eos.lookup_os_eos() 的
    版本退階只會把「IBM i 7.3」退到「IBM i 7」，退不到不帶版本號的「IBM i」，
    所以帶版本查詢原本會直接落空變「需確認」，但「不認識這個產品」跟「認識這個
    產品、只是沒有逐版日期」是兩回事——這裡多退一步，用產品名（不含版本）
    再查一次。"""
    import eos

    if not canonical:
        return None, None
    hit = eos.lookup_os_eos(canonical)
    if hit is None and product:
        hit = eos.lookup_os_eos(product)
    if hit is None:
        return "需確認", None
    if hit["eos_date"] is None:
        return "尚未公布", None
    raw_status = eos.eos_status(hit["eos_date"])
    return _STATUS_MAP.get(raw_status, "需確認"), hit["eos_date"]

# vCenter IP → 機房 的對照存這個 app_settings key（JSON 字串）。
# 刻意不放設定檔：那個檔會進公開 relay，真實 IP 與內部拓撲不外送。
VCENTER_LOCATION_KEY = "report_vcenter_location"

# 表3「實體主機」的固定名單，同樣存 app_settings 不進版控（真實IP）。
# 2026-08-21 使用者：「我只要這幾台的資訊，總共10台主機就可以」——AIX/IBM i
# 混著正式跟備援、混著好幾個不同業務系統，不是單一規則能算出來的，classify_
# assets() 那套自動偵測會混進LAN Console/HMC/測試環境重複列，抓半天還是要
# 一台一台核對。改成固定清單，每台的OS/版本/EOS狀態仍即時查（IP對不到資產
# 時也要講出來，不能悄悄消失）。
PHYSICAL_HOSTS_KEY = "report_physical_hosts"

_CFG: dict | None = None


def _cfg() -> dict:
    """讀 report_groups.json。壞掉就回空設定——寧可分類全部落到 fallback
    （看得出來不對勁），也不要整頁 500。"""
    global _CFG
    if _CFG is None:
        import pathlib

        p = pathlib.Path(__file__).with_name("report_groups.json")
        try:
            _CFG = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _CFG = {}
    return _CFG


def reload_config() -> None:
    """設定檔改完不必重啟整個服務也能生效（測試也用得到）。"""
    global _CFG
    _CFG = None


def _match_group(value: str | None, rules: list[dict], fallback: str) -> str:
    """名稱關鍵字分類。大小寫不敏感——叢集命名實際上大小寫混用
    （`BQ_PROD_LOG_Cluster` vs `DN_UAT_Cluster01`）。"""
    v = (value or "").upper()
    for g in rules or []:
        for kw in g.get("match", []):
            if kw and kw.upper() in v:
                return g.get("name", kw)
    return fallback


def cluster_service(name: str | None) -> str:
    c = _cfg()
    return _match_group(name, c.get("cluster_service", []),
                        c.get("cluster_service_fallback", "交易服務"))


def cluster_environment(name: str | None) -> str:
    c = _cfg()
    return _match_group(name, c.get("cluster_environment", []),
                        c.get("cluster_environment_fallback", "正式"))


def vcenter_location(ip: str | None, conn: sqlite3.Connection | None = None,
                     cluster: str | None = None) -> str:
    """判定機房。順序：叢集名稱前綴 → 資料庫的 vCenter 對照 → 設定檔 → 「未對應」。

    ## 為什麼優先用叢集名稱而不是 vCenter IP

    `report_groups.json` **會進公開 relay**（它在 APP/ 底下）。把「哪台 vCenter
    服務哪個機房」寫死在裡面，等於把內部拓撲一起送出去——那比單一個 IP 出現在
    註解裡嚴重，因為它是結構化的運維資訊。

    而你們的叢集命名本身就帶著答案（`BQ_PROD_…` / `NH_PROD_…` / `DN_UAT_…`），
    跟叢集用途看 LOG 是同一招。前綴是代號，資訊量低得多。

    IP 對照表當退路（有些叢集名沒有機房代號），但**存在資料庫的 app_settings**，
    不進版控、不外送，由使用者在畫面上維護。

    對不到一律回「未對應」而不是猜——對不到代表少了一筆對照，那要有人去補，
    不是讓它默默歸進某個機房，那會讓月報的機房分佈悄悄失真。
    """
    c = _cfg()
    if cluster:
        hit = _match_group(cluster, c.get("location_prefix", []), "")
        if hit:
            return hit

    key = (ip or "").strip()
    if conn is not None and key:
        try:
            from db import get_setting

            raw = get_setting(conn, VCENTER_LOCATION_KEY, "")
            if raw:
                m = json.loads(raw)
                if key in m:
                    return m[key]
        except Exception:  # noqa: BLE001 - 設定壞掉不該讓整張報表消失
            pass

    return (c.get("vcenter_location") or {}).get(key, "未對應")


# ===== 表1：平台數量與生命週期 =====

def _report_platform(bucket: str, canonical: str | None) -> str | None:
    """把 manage_state 的平台分類再對到月報的類別。回 None 代表本報告不列入。

    Windows 拆 Server/Client 是使用者 2026-08-21 拍板的，判定看正規化後的 OS 名稱：
    含 "server" → Server；Win 7/8/10/11 → Client；兩者都不是（例如只寫「Windows」
    這種登打不全的）→ 「Windows 未明版本」，**不是猜一個**。
    他報告上本來就有這一列（29 台），那正是要人去補資料的清單。
    """
    excluded = set(_cfg().get("excluded_platforms") or [])
    if bucket in excluded:
        return None

    if bucket == "Windows":
        low = (canonical or "").lower()
        if "server" in low:
            return "Windows Server"
        # Win11 算 Client（使用者 2026-08-21 明確指定）
        if any(k in low for k in ("windows 11", "windows 10", "win11", "win10",
                                  "windows 8", "windows 7", "windows xp")):
            return "Windows Client"
        return "Windows 未明版本"

    if bucket == "VMware ESXi":
        return "VMware ESXi"
    if bucket == "AIX/Unix":
        return "IBM AIX"
    if bucket == "IBM i":
        return "IBM i"

    # RHEL / CentOS / Debian / Oracle Linux / Linux(其他) / SUSE… 一律併成 Linux。
    # 月報看的是「要換幾台」，不是發行版比例——發行版明細在既有的 EOS 頁看得到。
    if bucket and ("linux" in bucket.lower() or bucket in
                   ("RHEL", "CentOS", "Debian", "Oracle Linux", "SUSE", "Ubuntu")):
        return "Linux"
    return None


def classify_assets(conn: sqlite3.Connection) -> list[dict]:
    """把每一台資產分好類，回一列一台的明細。**表1 的所有數字都是從這裡加總出來的。**

    ## 為什麼一定要有這一層

    使用者 2026-08-21 的要求：「你每個數字我都要可以追」。

    如果加總跟明細各寫一份查詢，兩邊遲早會漂走——格子寫 62 台、點進去列出 58 台，
    那時候沒有人知道哪個才對，整張報表就不能用了。所以只算一次：
    先產生每一台的分類結果，聚合與下鑽都讀這同一份。

    順帶解決另一件事：使用者的月報跟系統算的口徑對不起來（Windows Client 系統算
    62、他報告 349）。有了明細他自己點開就看得到是哪些機器被分到哪裡，
    不必等我猜——**能追就能自己判斷口徑差在哪**。

    每一列都帶 `reason`：這台為什麼被分到這一類、EOS 判定的依據是什麼。
    數字可追不只是「列得出是哪幾台」，還要「講得出為什麼是這一台」。
    """
    import manage_state
    import normalize

    out: list[dict] = []
    for r in conn.execute(
        "SELECT asset_serial, hostname, ip, os, device_model, asset_status, "
        "physical_location, environment, asset_name FROM hardware"
    ):
        retired = (r["asset_status"] or "").strip() in manage_state.RETIRED_STATUS

        canonical = None
        product = None
        if r["os"] and str(r["os"]).strip().upper() != "N/A":
            os_info = normalize.normalize_os(r["os"], conn, r["device_model"])
            canonical = os_info["canonical"]
            product = os_info.get("product")
        bucket = manage_state.platform_of_from_os(r["os"], r["device_model"], conn)
        plat = _report_platform(bucket, canonical)

        status, eos_date = _eos_lookup(canonical, product)
        status = status or "需確認"

        # 判定依據要分得出「查無這個產品」跟「查到了但官方沒公布日期」，不能都
        # 寫「查無官方EOS日期」——2026-08-21使用者：「判斷依據這種資訊我不要
        # 全部拿掉」，這欄留著、只是把EOS那句話講精準。
        if eos_date:
            eos_note = f"；EOS {eos_date}"
        elif status == "尚未公布":
            eos_note = "；官方已收錄此產品但尚未公布EOS日期"
        else:
            eos_note = "；EOS表查無此產品，需確認"

        out.append({
            "asset_serial": r["asset_serial"],
            "hostname": r["hostname"],
            "ip": r["ip"],
            "asset_name": r["asset_name"],
            "os_raw": r["os"],                 # 登記的原始值——口徑對不上時要看的就是這欄
            "os_canonical": canonical,         # 正規化後的名稱（分類依據）
            "device_model": r["device_model"],
            "location": r["physical_location"],
            "environment": r["environment"],
            "bucket": bucket,                  # manage_state 的平台大類
            "platform": plat,                  # 月報類別；None = 本報告不列入
            "eos_status": status,
            "eos_date": eos_date,
            "retired": retired,
            "reason": (
                f"OS 原始值「{r['os'] or '（空）'}」→ 正規化「{canonical or '認不出'}」"
                f"→ 平台「{bucket}」" + eos_note
            ),
        })
    return out


def platform_lifecycle(conn: sqlite3.Connection, detail: bool = False) -> dict[str, Any]:
    """表1。回每個平台類別的 總量／已EOS／一年內EOS／需確認／支援中。

    只算有效資產（排除退役）——理由同 composition()：報廢的機器沒有「還要不要換」
    的問題，混進來會讓「還有幾台要處理」失真。

    排除的平台（網路設備等）**不是刪掉**：`excluded` 會回排除了哪幾類、各幾台，
    前端要在表下方註明。否則總量跟資產總數對不起來時，沒有人講得出為什麼。

    `detail=True` 時每一格附上是哪幾台（下鑽用）。預設不附——3661 台的明細每次
    開頁面都送一遍太重，改由下鑽端點按需求要。
    """
    assets = classify_assets(conn)

    table: dict[str, dict[str, int]] = {
        p: {"total": 0, **{s: 0 for s in STATUS_ORDER}} for p in PLATFORM_ORDER
    }
    excluded: dict[str, int] = {}
    retired = 0

    for a in assets:
        if a["retired"]:
            retired += 1
            continue
        if a["platform"] is None:
            k = a["bucket"] or "未知"
            excluded[k] = excluded.get(k, 0) + 1
            continue
        table[a["platform"]]["total"] += 1
        table[a["platform"]][a["eos_status"]] += 1

    result = {
        "rows": [{"platform": p, **table[p]} for p in PLATFORM_ORDER],
        "total": sum(table[p]["total"] for p in PLATFORM_ORDER),
        # 排除的要講出來，不能靜默消失
        "excluded": [{"platform": k, "count": v}
                     for k, v in sorted(excluded.items(), key=lambda x: -x[1])],
        "excluded_total": sum(excluded.values()),
        "retired_excluded": retired,
    }
    if detail:
        result["items"] = [a for a in assets]
    return result


def drill_platform(conn: sqlite3.Connection, platform: str | None = None,
                   status: str | None = None, bucket: str | None = None,
                   retired: bool = False, os_canonical: str | None = None) -> list[dict]:
    """表1 任何一格點下去要看的清單。跟加總走同一份 `classify_assets()`，
    所以「格子上的數字」與「清單筆數」必然一致——這是可追的前提。

    platform=None 且 bucket 有值 → 查被排除的那幾類（網路設備等）。
    os_canonical 有值 → 版本明細表(見 os_version_breakdown())那一列點下去。
    傳進來的是併過的大版本鍵（如「Red Hat Enterprise Linux 7」），比對時
    同樣把每一台的完整版本併成大版本鍵再比——不然「7.9」對不上「7」，
    點下去會是空的。
    """
    items = classify_assets(conn)
    out = []
    for a in items:
        if a["retired"] != retired:
            continue
        if bucket is not None:
            if a["platform"] is not None or a["bucket"] != bucket:
                continue
        elif platform is not None and a["platform"] != platform:
            continue
        if status is not None and a["eos_status"] != status:
            continue
        if os_canonical is not None:
            key = _major_version_key(a["os_canonical"]) or "（認不出版本）"
            if key != os_canonical:
                continue
        out.append(a)
    out.sort(key=lambda x: (x["os_canonical"] or "", x["hostname"] or ""))
    return out


# EOS狀態的優先序——已EOS最急，支援中最不急。版本明細表照這個排，急的排前面。
_EOS_URGENCY = {"已EOS": 0, "一年內EOS": 1, "尚未公布": 2, "需確認": 3, "支援中": 4}

# 「Red Hat Enterprise Linux 7.9」→「Red Hat Enterprise Linux 7」：只在版本號
# 帶小數點時才砍尾巴（「Windows Server 2019」這種沒有小版號的維持原樣）。
_MAJOR_VER_RE = re.compile(r"^(.*?)(\d+)(?:\.\d+)+$")


def _major_version_key(canonical: str | None) -> str | None:
    """版本明細表 2026-08-21 使用者拍板：小版本併進大版本（7.9併進7），
    不要每個小版本各自一列——那樣表格太長，主管要的是「大方向」。"""
    if not canonical:
        return canonical
    m = _MAJOR_VER_RE.match(canonical)
    return f"{m.group(1)}{m.group(2)}" if m else canonical


def os_version_breakdown(conn: sqlite3.Connection) -> list[dict]:
    """作業系統版本明細——表1只到「平台大類」(Linux/Windows Server…)的粗顆粒度，
    2026-08-21 使用者：「只寫這樣子主管不會知道詳細資訊，譬如RHEL 7.9、26台」。

    三個拍板規則：
    1. 版本併到大版本（見 `_major_version_key()`）——不是每個小版本各自一列。
    2. **只列已EOS／一年內EOS的**，其他狀態不用列在這張表——這是一份「要處理
       什麼」的行動清單，不是全量統計（全量統計是表1的事）。
    3. **依台數排序，不是依急迫度排序**——原本「已EOS一律排在一年內EOS前面」
       會把真正衝擊最大的項目埋掉：Windows Server 2016 有549台、一年內就要
       到期，卻因為排序規則被擠到已EOS那些個位數台的項目後面，18個版本裡
       台數最多的一筆反而看不到（2026-08-21 使用者從畫面上發現這個問題）。
       改成單純依台數大到小排，衝擊最大的自然排最前面。

    EOS狀態用**每一台自己的完整版本**去查（沿用 classify_assets() 已經算好
    的 eos_status/eos_date），不是拿併過的大版本鍵去查——AIX 的 EOS 表是照
    小版本公告的（AIX 7.1／7.2 可能日期不同），併成「AIX 7」去查表反而查
    不到會誤判成需確認。同一個大版本群組裡有多台、EOS狀態不一致時，取最
    急迫的那個代表整組——不平均掉風險，這一列有任何一台快到期就該被看見。
    """
    groups: dict[str, dict] = {}
    for a in classify_assets(conn):
        if a["retired"] or a["platform"] is None:
            continue
        key = _major_version_key(a["os_canonical"]) or "（認不出版本）"
        g = groups.setdefault(key, {
            "os_canonical": key, "platform": a["platform"], "count": 0,
            "eos_status": None, "eos_date": None, "_urgency": 99,
        })
        g["count"] += 1
        urgency = _EOS_URGENCY.get(a["eos_status"], 9)
        if urgency < g["_urgency"]:
            g["_urgency"] = urgency
            g["eos_status"] = a["eos_status"]
            g["eos_date"] = a["eos_date"]

    rows = [
        {k: v for k, v in g.items() if k != "_urgency"}
        for g in groups.values()
        if g["eos_status"] in ("已EOS", "一年內EOS")
    ]
    rows.sort(key=lambda r: (-r["count"], r["os_canonical"]))
    return rows


# ===== 表2、表3 的共同底料：RVTools vHost =====

def _vhosts(conn: sqlite3.Connection) -> list[dict]:
    """把 vHost 分頁的每一列攤出來。這是表2與表3唯一的來源，算一次共用——
    兩張表若各自查一次、各自解析，數字遲早會不一致而且沒人知道哪張才對。"""
    out = []
    for r in conn.execute(
        "SELECT payload FROM source_record WHERE source = 'vcenter_extra:vHost'"
    ):
        try:
            d = json.loads(r[0])
        except Exception:  # noqa: BLE001 - 單筆壞掉不該讓整張報表消失
            continue
        out.append({
            "host": d.get("Host"),
            "cluster": d.get("Cluster"),
            "datacenter": d.get("Datacenter"),
            "version": d.get("ESX Version"),
            "vcenter": d.get("VI SDK Server"),
        })
    return out


def drill_cluster(conn: sqlite3.Connection, location: str | None = None,
                  service: str | None = None, vcenter: str | None = None,
                  cluster: str | None = None, version: str | None = None) -> list[dict]:
    """表2／表3 任何一格點下去要看的 ESXi 清單。同樣走 `_vhosts()` 那唯一一份來源。

    version：表3 R2 拆成一列一版本後，該列的「臺數」要能下鑽回剛好那幾台
    （不含 build 號的乾淨版本比對，跟 virtualization_env() 用同一個
    `_esxi_display_version()`，兩邊算出來的數字才會一致）。
    """
    out = []
    for h in _vhosts(conn):
        loc = vcenter_location(h["vcenter"], conn, h["cluster"])
        svc = cluster_service(h["cluster"])
        if location is not None and loc != location:
            continue
        if service is not None and svc != service:
            continue
        if vcenter is not None and h["vcenter"] != vcenter:
            continue
        if cluster is not None and h["cluster"] != cluster:
            continue
        if version is not None and _esxi_display_version(h["version"]) != version:
            continue
        out.append({**h, "location": loc, "service": svc,
                    "cluster_env": cluster_environment(h["cluster"]),
                    "reason": f"叢集名稱「{h['cluster']}」→ {svc}；vCenter {h['vcenter']} → {loc}"})
    out.sort(key=lambda x: (x["cluster"] or "", x["host"] or ""))
    return out


def cluster_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """表2：各機房的叢集分類 × ESXi 數量。

    分類依據是**叢集名稱**（RVTools 沒有用途欄位），所以回傳一定帶 `basis` 說明，
    前端要顯示出來——看報告的人有權知道這個分類是怎麼來的。
    """
    per_loc: dict[str, dict[str, int]] = {}
    unmapped: list[str] = []

    for h in _vhosts(conn):
        loc = vcenter_location(h["vcenter"], conn, h["cluster"])
        if loc == "未對應" and h["vcenter"] and h["vcenter"] not in unmapped:
            unmapped.append(h["vcenter"])
        svc = cluster_service(h["cluster"])
        per_loc.setdefault(loc, {})[svc] = per_loc.setdefault(loc, {}).get(svc, 0) + 1

    return {
        "locations": [
            {"location": loc,
             "rows": [{"service": s, "esxi_count": n}
                      for s, n in sorted(items.items(), key=lambda x: -x[1])],
             "total": sum(items.values())}
            for loc, items in sorted(per_loc.items())
        ],
        "basis": "分類依叢集名稱判定（含 LOG 者歸 Log 服務，其餘歸交易服務）",
        # 設定檔少一台 vCenter 時要指名，不然那批 ESXi 會靜靜地歸到「未對應」
        "unmapped_vcenters": unmapped,
    }


def _esxi_display_version(raw: str | None) -> str | None:
    """RVTools 的 ESX Version 是「VMware ESXi 7.0.3 build-21930508」，build 號對
    畫面跟 EOS 比對都是雜訊——只取到 `VMware ESXi 7.0.3`。"""
    if not raw:
        return None
    m = re.search(r"VMware ESXi [\d.]+", raw)
    return m.group(0) if m else raw


def virtualization_env(conn: sqlite3.Connection) -> dict[str, Any]:
    """表3：虛擬化（vCenter/Cluster/臺數/版本，含EOS）＋ AIX/IBM i 清單。

    R2（2026-08-21 backlog）：版本欄要標EOS，混版的兩個版本要分兩行——原本
    混版擠成一行用「／」分隔看不出「哪個版本」已經EOS，也沒辦法個別下鑽。
    改成每個 (vcenter, cluster, version) 各自一列，各自算自己的臺數與EOS狀態；
    mixed_version 仍標在每一列上，讓人看得出這個叢集還有別的版本混跑。
    """
    agg: dict[tuple, dict] = {}
    cluster_versions: dict[tuple, set[str]] = {}
    for h in _vhosts(conn):
        version = _esxi_display_version(h["version"])
        ckey = (h["vcenter"], h["cluster"])
        cluster_versions.setdefault(ckey, set())
        if version:
            cluster_versions[ckey].add(version)

        key = (h["vcenter"], h["cluster"], version)
        e = agg.setdefault(key, {
            "location": vcenter_location(h["vcenter"], conn, h["cluster"]),
            "vcenter": h["vcenter"],
            "environment": cluster_environment(h["cluster"]),
            "cluster": h["cluster"],
            "version": version,
            "count": 0,
        })
        e["count"] += 1

    clusters = []
    for (vcenter, cluster, version), e in agg.items():
        status, eos_date = _eos_lookup(version)
        clusters.append({
            **e,
            "eos_status": status,
            "eos_date": eos_date,
            "mixed_version": len(cluster_versions.get((vcenter, cluster), set())) > 1,
        })
    clusters.sort(key=lambda x: (x["location"] or "", x["vcenter"] or "", x["cluster"] or "",
                                 x["version"] or ""))

    return {"clusters": clusters, "physical_hosts": physical_hosts_report(conn)}


def physical_hosts_report(conn: sqlite3.Connection) -> list[dict]:
    """表3「實體主機（AIX／IBM i）」固定名單（2026-08-21 使用者拍板）。

    ## 為什麼不是規則算出來，是固定清單

    R1 一開始走 `classify_assets()` 的 platform 欄位自動偵測，結果混進 LAN
    Console（靠 device_model 猜出 IBM i bucket）、HMC、測試環境重複列——每修
    一個新冒出一個。使用者最後直接把他要的10台整份列出來：「我只要這幾台的
    資訊，總共10台主機就可以」，混了AIX(好麥證券)跟IBM i(複委託/期貨/財管)、
    混了正式跟備援，不是單一規則能收斂出來的組合。

    清單存 app_settings（`PHYSICAL_HOSTS_KEY`），不進版控——真實IP會進公開
    relay。每台的 OS／版本／EOS 仍即時查 hardware 表，不是把這些也寫死：
    韌體升級、EOS新公告要立刻反映，不用改清單。

    IP 在 hardware 表對不到資產時仍要出現在清單裡、標明查無登記——不能因為
    對不到就悄悄從報告消失，那樣主管看報告會以為這台不存在。
    """
    import normalize
    from db import get_setting

    raw = get_setting(conn, PHYSICAL_HOSTS_KEY, "[]")
    try:
        entries = json.loads(raw) or []
    except Exception:  # noqa: BLE001 - 設定壞掉不該讓整張報表消失
        entries = []

    out = []
    for e in entries:
        hw = conn.execute(
            "SELECT hostname, os, device_model FROM hardware WHERE ip = ?", (e["ip"],)
        ).fetchone()

        canonical = None
        product = None
        if hw and hw["os"] and str(hw["os"]).strip().upper() != "N/A":
            info = normalize.normalize_os(hw["os"], conn, hw["device_model"])
            canonical = info["canonical"]
            product = info.get("product")

        status, eos_date = _eos_lookup(canonical, product)

        out.append({
            "location": e.get("location"), "environment": e.get("environment"),
            "service": e.get("service"), "ip": e.get("ip"),
            "hostname": hw["hostname"] if hw else None,
            "found": hw is not None,
            "os_raw": hw["os"] if hw else None,
            "product": product,
            "os_canonical": canonical,
            "eos_status": status,
            "eos_date": eos_date,
        })
    return out


# ===== 部門報告圖表頁：頁A（各環境實體機分布）／頁B（主機系統總覽）=====
#
# 2026-08-25 使用者提供兩張現有簡報頁，要求「格式相同、數據是新的即可」——版面照
# 他的簡報，數字改成系統即時算的。開工前已對過帳（見計畫檔），這裡的口徑是：
#
#   全環境台數 ＝ CIA 正式登記 ＋ 排除退役 ＋ 排除網路/儲存/BMC/未知
#
# 跟 platform_lifecycle() 的排除規則共用同一份 excluded_platforms（report_groups.json），
# 兩頁報表的「全環境」數字才會對得起來，不會各講各的。

# 帳外資產：DYN-（存活清單掃到）／VC-（vCenter 收到）／AUTO-（納管流程建立）開頭的，
# 實際存在但不在 CIA 清單上。這批**不可以**跟 CIA 登記的相加——2026-08-25 實測
# 踩過：兩者混算，報告數字對不上任何一邊（4,641 vs 使用者的 2,724）。要分開呈現。
OFF_BOOK_PREFIXES = ("DYN-", "VC-", "AUTO-")


def is_off_book(asset_serial: str | None) -> bool:
    return bool(asset_serial) and asset_serial.startswith(OFF_BOOK_PREFIXES)


# 頁A三個機房的顯示順序（板橋／內湖／敦南）。跟 location_groups.json 的
# groups 順序（板橋／敦南／內湖）不一樣是刻意的——那個順序是比對優先序
# （先命中先算），這個是畫面顯示順序，兩者無關，不要混用同一份設定。
ROOM_ORDER = ["板橋", "內湖", "敦南"]

# 頁B「核心交易／非核心」對照表存這個 app_settings key（JSON：{api_id: 分類名稱}）。
# 刻意不放設定檔：真實的業務系統分類是公司資訊（同 VCENTER_LOCATION_KEY 的理由）。
SYSTEM_CATEGORY_KEY = "report_system_category"


def _system_category_map(conn: sqlite3.Connection) -> dict[str, str]:
    """`api_id → 分類`。**這是舊的、不夠用的模型，只留作退路。**

    2026-08-26 拿管理員的《系統盤點(全環境)Data》驗證後確認：155 個 api_id 裡
    有 **88 個橫跨多種分類**（N-070 正式環境的機器就散在 6 個分類）。也就是說
    分類是**逐台的人工判斷**，不是業務系統的屬性——api_id 對照表在結構上就
    表達不了這件事。

    正式的來源改成 `hardware.system_category`（見 `asset_category_map()`）。
    這支留著是為了：那台機器自己還沒分類時，若它的業務系統剛好整組同一類，
    可以先給一個合理的預設值，總比一片「未分類」有用。**但它只是預設值，
    人在畫面上改過的一律以逐台的為準。**
    """
    from db import get_setting

    raw = get_setting(conn, SYSTEM_CATEGORY_KEY, "{}")
    try:
        return json.loads(raw) or {}
    except Exception:  # noqa: BLE001 - 設定壞掉就當空，不擋整頁
        return {}


def asset_category_map(conn: sqlite3.Connection) -> dict[str, str]:
    """`asset_serial → 分類`，取自 `hardware.system_category`。**這是正式來源。**

    回傳只含有值的，沒分類的不放進來——呼叫端要能分辨「這台歸 X 類」與
    「這台還沒分類」，用 `.get()` 拿到 None 就是後者，不要用空字串混在一起。
    """
    return {
        r["asset_serial"]: r["system_category"]
        for r in conn.execute(
            "SELECT asset_serial, system_category FROM hardware "
            "WHERE system_category IS NOT NULL AND TRIM(system_category) != ''")
    }


def category_of(asset_serial: str | None, api_id: str | None,
                per_asset: dict[str, str], per_apid: dict[str, str]) -> str | None:
    """單一一台的分類。逐台優先，api_id 對照只是還沒分類時的預設值。

    兩份都查不到就回 None（＝未分類），**不要回「其他」**——「其他」是使用者
    報告上一個真實的分類（90 台），跟「我們還不知道」是兩件事，混在一起就再也
    分不出「該去補資料的」有哪些。
    """
    hit = per_asset.get(asset_serial or "")
    if hit:
        return hit
    return per_apid.get((api_id or "").strip()) or None


#: 分類清單存在 app_settings 的鍵。
CATEGORY_DEFS_KEY = "report_system_categories"


def _category_defs(conn: sqlite3.Connection) -> list[dict]:
    """分類白名單，每項帶 name/group/color。

    group 只接受「核心交易」「非核心」「測試」——頂部四格與機房分布表靠這欄位
    換算，不是看分類名稱字面（分類已經細到 M.金融交易服務／Y.監控維運平台
    那個顆粒度，不會再直接等於「核心交易」這三個字）。

    ## 為什麼真實清單存 DB，不寫在 report_groups.json

    2026-08-26 打 patch 時當場踩到：分類名稱含公司識別字，而 `APP/` 底下的檔案
    **一定會走去識別化**（patch 與 relay 共用同一份規則）。結果 `C.<公司>證券App`
    在包裡變成 `C.（示範企業）App`——套下去那三類 125 台會全部變成「分類名稱不合法」，
    而且畫面上只會顯示一個數字，沒有人看得出是打包過程改的。

    這其實是既有規則（CLAUDE.md）寫過的：**含真實值的對照表放 app_settings 不進版控**。
    我把它寫進 JSON 是違反自己訂的規則。

    所以：**DB 裡有就用 DB 的**（由匯入盤點表時登錄，見 seed_categories_from_rows），
    沒有才退回 report_groups.json 的預設值。JSON 裡那份現在只是「還沒匯入前的
    出廠預設」，名稱可以被去識別化改掉也不影響正確性。
    """
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?", (CATEGORY_DEFS_KEY,)
    ).fetchone() if conn is not None else None
    if row and row["value"]:
        try:
            defs = json.loads(row["value"])
            if isinstance(defs, list) and defs:
                return defs
        except (ValueError, TypeError):
            pass          # 壞掉就退回預設，不要整頁 500
    return _cfg().get("system_categories") or []


def set_category_defs(conn: sqlite3.Connection, defs: list[dict]) -> None:
    """把分類清單寫進 app_settings（**不進版控**，見 _category_defs 的說明）。"""
    from db import set_setting

    set_setting(conn, CATEGORY_DEFS_KEY, json.dumps(defs, ensure_ascii=False))


def group_for_category(name: str, known: dict[str, str] | None = None) -> str:
    """從分類名稱推它屬於哪一組。

    依據是來源表的字母編號：**A~M＝核心交易、N~Z＝非核心、AA＝測試**。
    這個規律是 2026-08-26 拿既有 19 個分類逐一比對出來的（全部符合），
    但仍然是**推論不是證據**——管理員若說法不同，改 app_settings 裡那份即可。

    沒有字母編號的（例如「其他」）歸非核心；已知的分類直接沿用原本的組別，
    不要因為重新匯入就把人改過的組別蓋掉。
    """
    if known and name in known:
        return known[name]
    m = re.match(r"^([A-Za-z]{1,3})[.．、]", name.strip())
    if not m:
        return "非核心"
    letter = m.group(1).upper()
    if letter == "AA":
        return "測試"
    if len(letter) == 1 and letter <= "M":
        return "核心交易"
    return "非核心"


def _category_group_map(conn: sqlite3.Connection) -> dict[str, str]:
    return {c["name"]: c.get("group") for c in _category_defs(conn)}


def is_excluded_model(device_model: str | None) -> bool:
    """這個型號是不是「不算機房伺服器」（頁A 用）。

    ⚠️ **整格完全相等**比對（去頭尾空白、不分大小寫），不是子字串。
    用子字串會誤殺：清單裡有「PC」，做子字串比對的話
    「HPE ProLiant」「Exadata High Capacity」這種含 pc/PC 的正常伺服器會一起被排掉。
    這跟 2026-08-26 修 is_vm 時的教訓是同一個——當時若用子字串找「VM」，
    「ATEN…KVM」這種實體 KVM 切換器就會被誤判成虛擬機。

    清單來自使用者 2026-08-26 逐一確認（見 report_groups.json 的說明），
    因為那是業務判斷、程式推不出來。
    """
    v = (device_model or "").strip().casefold()
    if not v:
        return False
    return any(v == str(m).strip().casefold()
               for m in (_cfg().get("excluded_device_models") or []))


def report_baseline(conn: sqlite3.Connection) -> list[dict]:
    """頁A／頁B共用的起點：CIA 登記、排除退役、排除 excluded_platforms 的那批。

    跟 classify_assets() 分開一層，是因為那支回傳的是「全部資產」（含帳外、含
    退役、含被排除的平台），頁A／頁B要的是已經套用口徑之後的子集——兩邊各自
    再篩一次的話，篩選條件遲早會漂走，篩一次全部共用才保證兩頁口徑一致。

    ⚠️ **型號排除（excluded_device_models）刻意不放在這裡**，只在頁A 生效。
    頁B 算的是「全環境系統組成」，那些機器仍然是資產、仍然要算進去；
    頁A 問的是「機房裡有幾台實體伺服器」，PC／NB／入侵偵測設備不該計入。
    兩頁問的不是同一件事，共用起點但各自再套自己的規則。
    """
    return [
        a for a in classify_assets(conn)
        if not a["retired"] and not is_off_book(a["asset_serial"]) and a["platform"] is not None
    ]


def off_book_summary(conn: sqlite3.Connection) -> dict[str, int]:
    """帳外資產各來源幾台。獨立講出來，不併進主數字（口徑已確認的鐵則）。"""
    import manage_state

    counts = {p.rstrip("-"): 0 for p in OFF_BOOK_PREFIXES}
    retired = tuple(manage_state.RETIRED_STATUS)
    for r in conn.execute(
        "SELECT asset_serial FROM hardware WHERE COALESCE(asset_status,'') NOT IN "
        f"({','.join('?' for _ in retired)})", retired
    ):
        s = r["asset_serial"] or ""
        for p in OFF_BOOK_PREFIXES:
            if s.startswith(p):
                counts[p.rstrip("-")] += 1
                break
    return counts


# ===== 頁A：各環境實體機分布現況 =====

def physical_distribution(conn: sqlite3.Connection) -> dict[str, Any]:
    """三個機房各一個圓環圖 ＋ 分公司逐一列出。

    圓環依業務用途分色——但那張對照表使用者「以後再提供」，資料庫也沒有這欄
    （asset_purpose 相異值 1476 種，關鍵字猜必定分錯，見計畫檔）。所以現在
    每個機房只有一段「未分類」，等對照表補上後這裡會自動分色（畫面上要講清楚
    這件事，不能讓人以為系統壞了或漏算）。
    """
    import manage_state

    hw = {
        r["asset_serial"]: r
        for r in conn.execute(
            "SELECT asset_serial, is_vm, physical_location, device_model FROM hardware")
    }

    per_room: dict[str, int] = {name: 0 for name in ROOM_ORDER}
    branch_ct: dict[str, int] = {}
    excluded_ct: dict[str, int] = {}
    for a in report_baseline(conn):
        r = hw.get(a["asset_serial"])
        if r is None or manage_state.is_vm_value(r["is_vm"], r["device_model"]):
            continue                                  # 頁A只算實體機，虛擬機不算
        if is_excluded_model(r["device_model"]):
            # 排除的**不是刪掉**：記下是哪個型號、幾台，回傳出去讓畫面列。
            # 總數對不起來時要講得出少的那些去哪了（同表1 的 excluded 原則）。
            k = (r["device_model"] or "").strip()
            excluded_ct[k] = excluded_ct.get(k, 0) + 1
            continue
        room = manage_state.group_location(r["physical_location"])
        if room in per_room:
            per_room[room] += 1
        else:
            # fallback「分公司」：逐一列出原始據點名稱，不要合併成一個數字——
            # 使用者的簡報上分公司是一間一間列的，合併會少掉這個資訊。
            raw = (r["physical_location"] or "").strip() or "（未填機房）"
            branch_ct[raw] = branch_ct.get(raw, 0) + 1

    rooms = [
        {
            "room": name, "total": n,
            "categories": [{"name": "未分類", "count": n}] if n else [],
        }
        for name, n in per_room.items()
    ]
    branches = [
        {"name": n, "count": c}
        for n, c in sorted(branch_ct.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return {
        "rooms": rooms,
        "branches": branches,
        "total_physical": sum(r["total"] for r in rooms) + sum(b["count"] for b in branches),
        "off_book": off_book_summary(conn),
        "category_note": "業務用途尚未有對照表，全部歸「未分類」；補上對照表後這裡會自動分色。",
        # 排除了哪些型號、各幾台。畫面要列出來——不列的話總數對不起來時
        # 沒有人講得出為什麼（2026-08-26 使用者逐一確認的清單，見 report_groups.json）
        "excluded_models": [
            {"device_model": k, "count": v}
            for k, v in sorted(excluded_ct.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "excluded_models_total": sum(excluded_ct.values()),
    }


def drill_physical(conn: sqlite3.Connection, room: str | None = None,
                   branch: str | None = None, excluded_model: str | None = None) -> list[dict]:
    """頁A任何一格數字點下去要看的清單。跟 physical_distribution() 走同一份
    report_baseline()，格子上的數字跟這裡回的筆數必然一致。

    `excluded_model` 是給「被排除的型號」那一區用的——排除的也要能點開看是哪幾台，
    否則使用者無從判斷排除規則對不對（他還要拿去跟管理員核對）。
    """
    import manage_state

    hw = {
        r["asset_serial"]: r
        for r in conn.execute(
            "SELECT asset_serial, is_vm, physical_location, device_model FROM hardware")
    }
    out = []
    for a in report_baseline(conn):
        r = hw.get(a["asset_serial"])
        if r is None or manage_state.is_vm_value(r["is_vm"], r["device_model"]):
            continue

        model_excluded = is_excluded_model(r["device_model"])
        if excluded_model is not None:
            # 查「被排除的那批」：只收被排除、且型號相符的
            if not model_excluded:
                continue
            if (r["device_model"] or "").strip() != excluded_model:
                continue
            out.append({**a, "physical_location_raw": r["physical_location"],
                        "device_model": r["device_model"]})
            continue
        # 查機房／分公司：被排除的型號不算（跟聚合同一條規則，數字才對得起來）
        if model_excluded:
            continue

        loc_group = manage_state.group_location(r["physical_location"])
        raw = (r["physical_location"] or "").strip() or "（未填機房）"
        if room is not None and loc_group != room:
            continue
        if branch is not None and raw != branch:
            continue
        if room is None and branch is None:
            continue
        out.append({**a, "physical_location_raw": r["physical_location"],
                    "device_model": r["device_model"]})
    out.sort(key=lambda x: x["hostname"] or "")
    return out


# ===== 頁B：主機系統總覽 =====

def system_overview(conn: sqlite3.Connection) -> dict[str, Any]:
    """頂部四格（核心交易／非核心／測試／全環境）＋ 業務系統排行＋核心/非核心
    細分類明細（金融交易處理服務／監控維運平台…）。

    測試環境獨立一類（不進核心/非核心比較）：`environment` 歸組後等於「測試」的
    直接算測試，正式/備援才進核心/非核心的判定——核心交易/非核心/測試三者互斥、
    加總等於全環境，跟使用者簡報上「1168+957+599=2724」的關係一致。

    2026-08-25 使用者拍板：分類要跟簡報一樣細（核心9項、非核心10項），不維持
    粗略二分——report_groups.json 的 system_categories 每項帶一個 group 欄位
    （核心交易／非核心），核心/非核心的加總靠這個 group 換算，不是看分類名稱
    字面。細分類對照表沒填之前全部算「未分類」——不是假裝算得出來。
    業務系統排行（Top5／各系統台數）不需要那張表，api_id 聚合直接就有，
    所以先做、不用等對照表（計畫檔「先做讀取機制」那條）。
    """
    import manage_state

    hw = {
        r["asset_serial"]: r
        for r in conn.execute(
            "SELECT asset_serial, api_id, asset_name, environment, physical_location, "
            "is_vm, device_model FROM hardware")
    }
    # 逐台分類優先, api_id 對照只是還沒分類時的預設值(見 category_of 說明)
    cat_map = _system_category_map(conn)
    per_asset = asset_category_map(conn)
    group_of = _category_group_map(conn)
    cat_color = {c["name"]: c.get("color", "chart-gray") for c in _category_defs(conn)}

    baseline = report_baseline(conn)
    core_n = noncore_n = test_n = uncategorized_n = 0
    vm_n = physical_n = 0
    by_system: dict[str, dict] = {}
    cat_ct: dict[str, int] = {}
    core_system_ct: dict[str, int] = {}   # 只算正式/備援＋核心分類的台數，用來跟 core_n 對得起來
    room_ct: dict[str, dict[str, int]] = {
        name: {"core": 0, "noncore": 0, "test": 0, "uncategorized": 0} for name in ROOM_ORDER
    }
    room_ct["分公司"] = {"core": 0, "noncore": 0, "test": 0, "uncategorized": 0}
    for a in baseline:
        r = hw.get(a["asset_serial"])
        env = manage_state.group_environment(r["environment"] if r else None)
        aid = ((r["api_id"] if r else "") or "").strip()
        room = manage_state.group_location(r["physical_location"] if r else None)
        cat = category_of(a["asset_serial"], aid, per_asset, cat_map)
        if manage_state.is_vm_value(r["is_vm"] if r else None, r["device_model"] if r else None):
            vm_n += 1
        else:
            physical_n += 1
        if aid:
            entry = by_system.setdefault(
                aid, {"api_id": aid, "name": (r["asset_name"] if r else "") or aid,
                     "count": 0, "category": cat})
            entry["count"] += 1

        # 三桶全部看**分類**，不看 CIA 清冊的環境別。使用者 2026-08-26 講清楚了：
        # 「這個應該跟 CIA 無關，這個分類是為了要算出這三張 PPT 的類別所產生的
        # 一個獨特的分類。」——原本這裡是 `if env == "測試"`，等於讓 CIA 的環境別
        # 蓋過分類，那是把兩套本來就不同用途的東西當成同一件事在對照。
        grp = group_of.get(cat) if cat else None
        if grp == "測試":
            test_n += 1
            bucket = "test"
        else:
            if grp == "核心交易":
                core_n += 1
                bucket = "core"
                cat_ct[cat] = cat_ct.get(cat, 0) + 1
                if aid:
                    core_system_ct[aid] = core_system_ct.get(aid, 0) + 1
            elif grp == "非核心":
                noncore_n += 1
                bucket = "noncore"
                cat_ct[cat] = cat_ct.get(cat, 0) + 1
            else:
                uncategorized_n += 1
                bucket = "uncategorized"
        room_ct.setdefault(room, {"core": 0, "noncore": 0, "test": 0, "uncategorized": 0})
        room_ct[room][bucket] += 1

    systems = sorted(by_system.values(), key=lambda x: -x["count"])
    rooms = [
        {"room": name, **counts, "total": sum(counts.values())}
        for name, counts in room_ct.items() if name != "分公司"
    ]
    rooms.append({"room": "分公司", **room_ct["分公司"], "total": sum(room_ct["分公司"].values())})

    def _cat_breakdown(group_name: str, denom: int) -> list[dict]:
        names = [c["name"] for c in _category_defs(conn) if c.get("group") == group_name]
        rows = [
            {"name": n, "count": cat_ct.get(n, 0), "color": cat_color.get(n, "chart-gray"),
             "pct": round(cat_ct.get(n, 0) / denom * 100, 1) if denom else 0.0}
            for n in names
        ]
        return sorted(rows, key=lambda x: -x["count"])

    # core_top5 用 core_system_ct（只算正式/備援環境）而不是 by_system 的全量
    # 台數——by_system 連測試環境那份也算進去，拿它排 Top5 會讓「Top5 加其他
    # 系統小計」對不上 core_n（測試環境的機器另外歸「測試」桶，不該混進來）。
    core_top5_names = sorted(core_system_ct.items(), key=lambda kv: -kv[1])[:5]
    core_top5 = [
        {"api_id": aid, "name": by_system[aid]["name"], "count": n}
        for aid, n in core_top5_names
    ]
    core_top5_sum = sum(n for _, n in core_top5_names)
    return {
        "total": len(baseline),
        "core": core_n, "noncore": noncore_n, "test": test_n,
        "uncategorized": uncategorized_n,
        "vm": vm_n, "physical": physical_n,
        "virtualization_rate": round(vm_n / len(baseline) * 100, 1) if baseline else 0.0,
        "rooms": rooms,
        "core_categories": _cat_breakdown("核心交易", core_n),
        "noncore_categories": _cat_breakdown("非核心", noncore_n),
        "core_top5": core_top5,
        "core_top5_pct": [
            round(s["count"] / core_n * 100, 1) if core_n else 0.0 for s in core_top5
        ],
        "core_other_count": max(core_n - core_top5_sum, 0),
        "top5": systems[:5],
        "noncore_systems": [s for s in systems if group_of.get(s["category"]) == "非核心"],
        "all_systems": systems,
        "systems_without_category": sum(1 for s in systems if group_of.get(s["category"]) is None),
        "category_note": (
            "業務系統尚未有分類對照表，先以「未分類」呈現；"
            "業務系統排行（Top5／各系統台數）不受影響，已經是即時真實數字。"
        ),
        "room_note": (
            "內湖目前資料只有一個機房代號，無法拆成瑞光／港墘兩個據點；"
            "如需拆分需另外提供對照依據（例如網段或叢集名稱規則）。"
        ),
    }


def drill_system_overview(conn: sqlite3.Connection, bucket: str | None = None,
                          api_id: str | None = None, room: str | None = None,
                          category: str | None = None) -> list[dict]:
    """頁B/頁C任何一格數字（含 Top5／各系統列／各機房分布表／細分類卡片）
    點下去要看的清單。bucket 只接受 core／noncore／test／uncategorized。
    category 是細分類名稱（例如「監控維運平台」），跟 bucket 可以疊加也可以
    單獨用——卡片上就是點細分類，不是點粗分類。"""
    import manage_state

    hw = {
        r["asset_serial"]: r
        for r in conn.execute(
            "SELECT asset_serial, api_id, environment, physical_location FROM hardware")
    }
    # 逐台分類優先, api_id 對照只是還沒分類時的預設值(見 category_of 說明)
    cat_map = _system_category_map(conn)
    per_asset = asset_category_map(conn)
    group_of = _category_group_map(conn)
    out = []
    for a in report_baseline(conn):
        r = hw.get(a["asset_serial"])
        env = manage_state.group_environment(r["environment"] if r else None)
        aid = ((r["api_id"] if r else "") or "").strip()
        cat = category_of(a["asset_serial"], aid, per_asset, cat_map)
        if api_id is not None and aid != api_id:
            continue
        if room is not None and manage_state.group_location(r["physical_location"] if r else None) != room:
            continue
        if category is not None and cat != category:
            continue
        if bucket is not None:
            grp = group_of.get(cat) if cat else None
            if bucket == "test":
                if grp != "測試":
                    continue
            elif grp == "測試":
                continue
            elif bucket == "core" and group_of.get(cat) != "核心交易":
                continue
            elif bucket == "noncore" and group_of.get(cat) != "非核心":
                continue
            elif bucket == "uncategorized" and group_of.get(cat):
                continue
        out.append({**a, "api_id": aid or None})
    out.sort(key=lambda x: x["hostname"] or "")
    return out


# ===== 業務系統對照表：空白範本匯出／填好後匯入 =====

def system_category_template(conn: sqlite3.Connection) -> list[dict]:
    """177 個業務系統的空白對照表，依台數大到小排序——填前 30 個就涵蓋大部分
    機器，使用者不必一次填完全部（計畫檔「不必一次填完 177 個」）。"""
    import manage_state

    hw = {
        r["asset_serial"]: r
        for r in conn.execute("SELECT asset_serial, api_id, asset_name FROM hardware")
    }
    cat_map = _system_category_map(conn)
    ct: dict[str, int] = {}
    name: dict[str, str] = {}
    for a in report_baseline(conn):
        r = hw.get(a["asset_serial"])
        aid = ((r["api_id"] if r else "") or "").strip()
        if not aid:
            continue
        ct[aid] = ct.get(aid, 0) + 1
        name.setdefault(aid, (r["asset_name"] if r else "") or "")
    return [
        {"api_id": aid, "name": name.get(aid, ""), "count": n, "category": cat_map.get(aid, "")}
        for aid, n in sorted(ct.items(), key=lambda kv: -kv[1])
    ]


def import_system_category(conn: sqlite3.Connection, mapping: dict[str, str]) -> dict[str, Any]:
    """把填好的對照表寫進 app_settings。

    分類名稱一律對照 report_groups.json 的 system_categories 白名單——不驗證
    的話，打錯字（多一個空白、簡繁體不同）會讓那個系統永遠卡在「未分類」，
    畫面卻看不出原因，人只會以為系統又壞了。無效的分類值略過，回傳筆數讓
    使用者知道有沒有整批漏收。
    """
    from db import set_setting

    valid = {c["name"] for c in (_cfg().get("system_categories") or [])}
    if not valid:
        valid = {"核心交易", "非核心"}

    existing = _system_category_map(conn)
    clean: dict[str, str] = dict(existing)
    accepted = rejected = 0
    for api_id, cat in mapping.items():
        api_id = (api_id or "").strip()
        cat = (cat or "").strip()
        if not api_id:
            continue
        if not cat:
            clean.pop(api_id, None)          # 空白＝清掉這筆既有分類
            continue
        if cat not in valid:
            rejected += 1
            continue
        clean[api_id] = cat
        accepted += 1

    set_setting(conn, SYSTEM_CATEGORY_KEY, json.dumps(clean, ensure_ascii=False))
    return {"accepted": accepted, "rejected": rejected, "total_mapped": len(clean),
            "valid_categories": sorted(valid)}


# ===== 分類作業（/classify 頁）=====
#
# 2026-08-26 使用者：「所以是不是要一個頁面專門來做這個分類？」——是，而且理由比
# 「方便」更強：分類這件事需要一個地方讓人**看到不一致、逐台判斷、改完知道還剩幾台**。
# 塞在資產詳細頁裡做不到，那裡一次只看得到一台。
#
# 實測要人工處理的量：報告範圍 2740 台裡，管理員那份 Excel 蓋得到 2288 台（83%），
# **452 台蓋不到**（Windows Server 186／Linux 136／ESXi 71／IBM i 43／AIX 8／
# Windows Client 8）。一台一台點是好幾小時的事，
# **所以批次修改不是加分項，是必要功能**，不然這 452 台不會有人做完。
#
# ⚠️ 這裡原本還有一個「環境別與分類不一致」的旗標（拿 CIA 清冊的環境別去對
# `AA.測試環境`，標出 23 台）。2026-08-26 使用者指正後移除：
# 「這個應該跟 CIA 無關，這個分類是為了要算出這三張 PPT 的類別所產生的
# 一個獨特的分類。」——兩者本來就不是在講同一件事，拿來互相對照是我把
# 「名字看起來像」當成「意思一樣」。**那 23 台不是問題，是我製造的假問題。**


def classify_rows(conn: sqlite3.Connection, only: str | None = None) -> list[dict]:
    """分類作業的清單。一列一台，帶目前分類。

    `only`：`unclassified`（未分類）／`classified`／None（全部）。

    `environment` 欄仍然回傳，但那是**給人判斷時當參考的旁證**（「這台清冊寫
    測試，那分類大概是測試環境」），不是判定依據——分類是獨立的一套，
    不跟 CIA 清冊對帳。
    """
    per_asset = asset_category_map(conn)
    per_apid = _system_category_map(conn)
    hw = {
        r["asset_serial"]: r
        for r in conn.execute(
            "SELECT asset_serial, api_id, hostname, ip, environment, "
            "physical_location, asset_name, device_model FROM hardware")
    }

    out: list[dict] = []
    for a in report_baseline(conn):
        r = hw.get(a["asset_serial"])
        if r is None:
            continue
        cat = category_of(a["asset_serial"], r["api_id"], per_asset, per_apid)
        env = (r["environment"] or "").strip()

        row = {
            "asset_serial": a["asset_serial"],
            "hostname": r["hostname"],
            "ip": r["ip"],
            "api_id": r["api_id"],
            "asset_name": r["asset_name"],
            "environment": env or None,
            "location": r["physical_location"],
            "platform": a.get("platform"),
            "category": cat,
            "from_asset": a["asset_serial"] in per_asset,   # 逐台設的還是靠 api_id 推的
        }
        if only == "unclassified" and cat:
            continue
        if only == "classified" and not cat:
            continue
        out.append(row)

    # 未分類排最前面——這頁的用途是把它們清掉，不是瀏覽
    out.sort(key=lambda x: (bool(x["category"]), x["hostname"] or "",
                            x["asset_serial"] or ""))
    return out


def classify_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """頂部進度：已分類／未分類，以及各分類幾台。

    「還剩幾台」是這頁唯一能讓人做得完的東西——沒有進度條就沒有人會做完。
    """
    rows = classify_rows(conn)
    by_cat: dict[str, int] = {}
    for r in rows:
        if r["category"]:
            by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    classified = sum(1 for r in rows if r["category"])
    return {
        "total": len(rows),
        "classified": classified,
        "unclassified": len(rows) - classified,
        "percent": round(classified * 100 / len(rows), 1) if rows else 0.0,
        "by_category": [{"name": k, "count": v}
                        for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])],
        "valid_categories": [c["name"] for c in _category_defs(conn)],
    }


def set_asset_categories(conn: sqlite3.Connection, asset_serials: list[str],
                         category: str | None, updated_by: str | None = None) -> dict[str, Any]:
    """設定（或清除）一批資產的分類。批次是主要用法，不是附加功能。

    `category=None` 或空字串＝清掉分類（回到未分類）。

    分類名稱一律對照 `report_groups.json` 的白名單——不驗證的話，打錯字
    （多一個空白、簡繁不同）會讓那台永遠卡在「未分類」而畫面看不出原因，
    人只會以為系統壞了。
    """
    valid = {c["name"] for c in _category_defs(conn)}
    cat = (category or "").strip() or None
    if cat and cat not in valid:
        raise ValueError(f"分類「{cat}」不在允許清單裡。可用的有：{'、'.join(sorted(valid))}")

    serials = [s.strip() for s in asset_serials if (s or "").strip()]
    if not serials:
        return {"updated": 0}

    now = NOW()
    marks = ",".join("?" for _ in serials)
    # manual_updated_at 一起更新：這是人改的，要跟自動匯入刷新的 updated_at 分開，
    # 否則「有沒有人在維護」那個指標會被自動匯入灌成假的（既有原則，見 schema.sql）
    conn.execute(
        f"UPDATE hardware SET system_category = ?, manual_updated_at = ? "
        f"WHERE asset_serial IN ({marks})",
        [cat, now, *serials],
    )
    conn.commit()
    return {"updated": len(serials), "category": cat, "updated_by": updated_by}


def seed_categories_from_rows(conn: sqlite3.Connection, rows: list[dict],
                              dry_run: bool = True) -> dict[str, Any]:
    """用外部盤點表（主機名 → 分類）批次帶入分類。**預設只試算不寫入。**

    `rows` 每筆要有 `hostname` 與 `category`。

    ## 只取分類，其餘一律以資產清冊為準

    2026-08-26 使用者明確指示：「其實都不用參考，只有 H 分類可以參考。其他的
    類別、主機名稱，其實都是要按照《資訊資產清冊》來看。」——所以這裡**只讀
    主機名（當對照鍵）與分類**，來源表的環境別／機房／APID 全部忽略。

    主機名比對要正規化：來源表混了 FQDN（`secsvr004-159t.example.local`）與大小寫，
    資產清冊登記的是全大寫無網域。不正規化的話對得上的會從 97% 掉到個位數。

    `dry_run=True` 先回「對上幾筆、對不上哪些、會改動哪些」讓人看過再寫——
    這是 2026-08-24 待複核佇列那次學到的：**先試跑再寫入**，第一版規則把重複
    列當成衝突全擋掉，1464 筆被略過，乾跑才看出來。
    """
    defs = _category_defs(conn)
    known_group = {c["name"]: c.get("group") for c in defs}
    valid = set(known_group)

    def norm(v: Any) -> str:
        """主機名正規化：去網域、轉大寫。"""
        s = str(v or "").strip()
        return s.split(".")[0].strip().upper() if s else ""

    def norm_cat(v: Any) -> str:
        """對照來源表的分類到白名單。**名稱照抄，不剝前綴。**

        2026-08-26 使用者指正：「我已經給你分類了，你還把 A.XXX 的 A. 拿掉」。
        我原本判斷那個 A~AA 是他們表上的排序編號、不是名稱的一部分——**那是我的
        推論**，而使用者給的就是完整字串。白名單現在也存完整字串，所以正常情況
        直接對得上。

        只有一種情況要多做事：來源檔**沒有**寫前綴（例如手打的補充清單寫
        `資安管理系統`）。那時拿去尾比對，對得到唯一一個就用它，對到多個就
        當成不合法讓人自己挑——猜錯會把機器歸到錯的分類，寧可讓人看到。
        """
        raw = str(v or "").strip()
        if not raw or raw in valid:
            return raw
        bare = re.sub(r"^[A-Za-z]{1,3}[.．、]\s*", "", raw).strip()
        hits = [n for n in valid
                if re.sub(r"^[A-Za-z]{1,3}[.．、]\s*", "", n).strip() == bare]
        return hits[0] if len(hits) == 1 else raw

    # 來源表出現、而且 norm_cat 也對不到既有分類的 → 當成新分類。
    #
    # ⚠️ 順序很重要：**要先跑 norm_cat**。不然來源表寫「資安管理系統」（沒前綴）
    # 會被當成新分類登錄一份，跟既有的「X.資安管理系統」變成兩個看起來一樣的項目，
    # 而報表上會分成兩塊——那種錯誤在畫面上完全看不出原因。
    #
    # 使用者 2026-08-26 拍板「管理員那份是源頭」，所以**分類清單本身也該由那份表
    # 決定**，不是我寫死在版控裡（寫死那份還會被去識別化改掉名稱，見
    # _category_defs 的說明）。乾跑時列出來讓人看過，寫入時才登錄。
    #
    # X4：字母編號當識別，同字母且既有那筆 0 台就直接取代名稱（改名），不是新增。
    #
    # 為什麼會冒出殭屍分類：norm_cat() 對不到既有名稱時把原始字串當「新分類」
    # 收下——但如果來源表這次的字面跟既有的「C.XXX」差一點（例如去識別化 patch
    # 曾經把公司名改成「（示範企業）」、或管理員這次表格打字打法不同），會被當成
    # 「跟 C 完全無關的新分類」加進去，變成兩筆同字母的分類，其中一筆永遠 0 台。
    # 這種 0 台的舊筆之後也沒人會再選到——除非有人手動從下拉選單挑到它。
    #
    # 字母只會出現在**這次唯一沒對到既有名稱**的情況，所以拿它當弱比對key很安全：
    # 兩筆真的是同一個分類（打字不同而已），不會有兩個无关分類剛好字母一樣還都是
    # 0 台（字母是使用者自己在源頭表格編的序號，不是我們發明的）。
    existing_ct: dict[str, int] = {}
    for r in conn.execute(
        "SELECT system_category, COUNT(*) c FROM hardware "
        "WHERE system_category IS NOT NULL AND system_category != '' "
        "GROUP BY system_category"
    ):
        existing_ct[r["system_category"]] = r["c"]

    def _letter(name: str) -> str | None:
        m = re.match(r"^([A-Za-z]{1,3})[.．、]", name.strip())
        return m.group(1).upper() if m else None

    zombie_rename: dict[str, str] = {}   # 舊(殭屍)名稱 -> 這次來的新名稱
    new_cats: list[dict] = []
    seen_new: set[str] = set()
    for _row in rows:
        c = norm_cat(_row.get("category"))
        if c and c not in valid and c not in seen_new:
            seen_new.add(c)
            letter = _letter(c)
            zombie = None
            if letter:
                for existing_name in known_group:
                    if (_letter(existing_name) == letter
                            and existing_ct.get(existing_name, 0) == 0
                            and existing_name not in zombie_rename):
                        zombie = existing_name
                        break
            if zombie:
                zombie_rename[zombie] = c
            else:
                new_cats.append({"name": c, "group": group_for_category(c, known_group),
                                 "color": "chart-gray"})
    valid |= seen_new

    by_host: dict[str, list[str]] = {}
    for r in conn.execute("SELECT asset_serial, hostname FROM hardware"):
        n = norm(r["hostname"])
        if n:
            by_host.setdefault(n, []).append(r["asset_serial"])

    in_scope = {a["asset_serial"] for a in report_baseline(conn)}
    matched: list[dict] = []
    unmatched: list[dict] = []
    invalid_cat: list[dict] = []
    multi: list[dict] = []
    seen_hosts: set[str] = set()
    dup_rows = 0
    no_host = 0
    for row in rows:
        host = norm(row.get("hostname"))
        cat = norm_cat(row.get("category"))
        if not host:
            no_host += 1
            continue
        if cat and cat not in valid:
            invalid_cat.append({"hostname": row.get("hostname"), "category": cat})
            continue
        if host not in by_host:
            unmatched.append({"hostname": row.get("hostname"), "category": cat})
            continue
        # 一個主機名可能對到多台資產。實測 2026-08-26：331 個主機名對到 2 台以上，
        # 大多是「同一台機器同時有 CIA 登記的 HW- 與 vCenter 掃到的 VC- 帳外列」，
        # 但也有兩筆都是 HW- 的（那是資產庫真的重複登記，屬於待複核佇列的事）。
        #
        # 帳外那幾筆照樣寫分類——它們日後被納管合併時分類就已經在了，沒有壞處；
        # 但**要把數字拆開報**，不然畫面顯示「對上 3715 台」而報表只多了 2288 台，
        # 人會以為系統算錯。
        if host in seen_hosts:
            dup_rows += 1
        seen_hosts.add(host)
        if len(by_host[host]) > 1:
            multi.append({"hostname": row.get("hostname"),
                          "asset_serials": by_host[host][:5]})
        for serial in by_host[host]:
            matched.append({"asset_serial": serial, "hostname": row.get("hostname"),
                            "category": cat, "in_scope": serial in in_scope})

    matched_serials = {m["asset_serial"] for m in matched}

    if not dry_run:
        if new_cats or zombie_rename:
            # 先把殭屍改名（同一筆，只是名稱換掉），再併入真正的新分類——
            # 順序不能反：新分類要用改名「後」的清單去判斷字母有沒有衝突的話，
            # 這裡不需要，因為 zombie_rename 的 key 在偵測階段就已經排除，
            # 不會又被當成一筆新分類重複加入。
            merged = [
                {**c, "name": zombie_rename[c["name"]]} if c["name"] in zombie_rename else c
                for c in defs
            ]
            merged += new_cats

            def _order(c):
                m = re.match(r"^([A-Za-z]{1,3})[.．、]", c["name"])
                return (0, len(m.group(1)), m.group(1).upper()) if m else (1, 0, c["name"])

            merged.sort(key=_order)
            set_category_defs(conn, merged)
        now = NOW()
        for m in matched:
            conn.execute(
                "UPDATE hardware SET system_category = ?, manual_updated_at = ? "
                "WHERE asset_serial = ?", (m["category"] or None, now, m["asset_serial"]))
        conn.commit()

    return {
        "dry_run": dry_run,
        "source_rows": len(rows),
        # ⚠️ **去重後的台數**，不是「列×資產」的配對數。
        #
        # 2026-08-26 實測：來源表 2414 列裡有 615 列的主機名重複出現，配對數會膨脹到
        # 3715——那個數字放在畫面上，跟分類頁的「已分類 2288 台」永遠對不起來，
        # 人只會以為系統算錯了。天條：**加總與下鑽必須走同一份計算**，這裡也一樣，
        # 匯入摘要講的台數要能跟分類頁的台數對得上。
        "matched": len(matched_serials),
        # 再拆一層：報表只看得到 in_scope 那些（帳外、退役、被排除的平台不進報表）。
        "matched_in_scope": len(matched_serials & in_scope),
        "matched_off_scope": len(matched_serials - in_scope),
        "duplicate_source_rows": dup_rows,
        "multi_match": len(multi),
        # 來源表帶進來的新分類。乾跑時只是預告，寫入時才真的登錄進 app_settings。
        "new_categories": [{"name": c["name"], "group": c["group"]} for c in new_cats],
        # 取代掉的殭屍分類（同字母、0台）——不是新增，是改名。乾跑時預告，
        # 寫入時才真的把舊名稱換掉。
        "renamed_categories": [{"old": old, "new": new} for old, new in zombie_rename.items()],
        "multi_match_samples": multi[:20],
        "unmatched": len(unmatched),
        "no_hostname": no_host,
        "invalid_category": len(invalid_cat),
        # 對不上的要列出來，不能只給數字——使用者要拿去查是資產庫少登記還是名字不同
        "unmatched_samples": unmatched[:50],
        "invalid_category_samples": invalid_cat[:20],
        "valid_categories": sorted(valid),
    }


# ===== 備註（可編輯、跨月保留）=====

def get_notes(conn: sqlite3.Connection, period: str | None = None) -> dict[str, str]:
    """回 {row_key: note}。使用者 2026-08-21 要求：像「7/10 為颱風假、無開單」
    這種話是人寫的，寫一次要留著，不必每個月重打。"""
    rows = conn.execute(
        "SELECT row_key, note FROM report_note WHERE period IS ? OR period IS NULL",
        (period,),
    ).fetchall()
    return {r["row_key"]: r["note"] for r in rows if r["note"]}


def set_note(conn: sqlite3.Connection, row_key: str, note: str,
             updated_by: str | None = None, period: str | None = None) -> None:
    conn.execute(
        "INSERT INTO report_note (period, row_key, note, updated_by, updated_at) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT(period, row_key) DO UPDATE SET note=excluded.note, "
        "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
        (period, row_key, note, updated_by, NOW()),
    )
    conn.commit()


# ===== 月份快照 =====

def build(conn: sqlite3.Connection, period: str | None = None) -> dict[str, Any]:
    """組出整份報告。`meta` 一定要有資料新鮮度——這一頁最大的風險不是算錯，
    是算得很精確但底層是三週前的快照，而看報告的人不知道。"""
    exported = None
    try:
        exported = conn.execute(
            "SELECT MIN(exported_at) FROM import_log "
            "WHERE source='rvtools' AND exported_at IS NOT NULL"
        ).fetchone()[0]
    except Exception:  # noqa: BLE001 - 舊 DB 沒有 exported_at
        pass

    return {
        "meta": {
            "generated_at": NOW(),
            "period": period,
            "rvtools_exported_at": exported,
            "rvtools_note": (
                f"虛擬化相關數字來自 RVTools，vCenter 匯出於 {exported[:10]}"
                if exported else
                "虛擬化相關數字來自 RVTools，但**認不出這批是哪天匯出的**，可能是舊資料"
            ),
        },
        "platform_lifecycle": platform_lifecycle(conn),
        "os_version_breakdown": os_version_breakdown(conn),
        "cluster_summary": cluster_summary(conn),
        "virtualization_env": virtualization_env(conn),
        "notes": get_notes(conn, period),
    }


def save_snapshot(conn: sqlite3.Connection, period: str, created_by: str | None = None) -> int:
    """存一份當月快照。

    為什麼要存：報告是每月出的，數字會隨資料變動；三個月後有人問「7 月那份怎麼算的」，
    重跑一次只會得到今天的數字。存快照才有稽核軌跡，也才做得出月與月的對比。
    同一個月重存＝覆蓋（同月本來就只該有一份定稿）。
    """
    payload = json.dumps(build(conn, period), ensure_ascii=False)
    conn.execute(
        "INSERT INTO report_snapshot (period, payload, created_by, created_at) "
        "VALUES (?,?,?,?) "
        "ON CONFLICT(period) DO UPDATE SET payload=excluded.payload, "
        "created_by=excluded.created_by, created_at=excluded.created_at",
        (period, payload, created_by, NOW()),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM report_snapshot WHERE period = ?", (period,)
    ).fetchone()[0]


def list_snapshots(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, period, created_by, created_at FROM report_snapshot "
        "ORDER BY period DESC"
    )]


def get_snapshot(conn: sqlite3.Connection, period: str) -> dict | None:
    row = conn.execute(
        "SELECT payload FROM report_snapshot WHERE period = ?", (period,)
    ).fetchone()
    return json.loads(row[0]) if row else None
