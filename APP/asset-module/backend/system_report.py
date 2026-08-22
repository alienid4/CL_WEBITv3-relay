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
