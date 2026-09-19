"""來源對照表：一台一列，標明這台在 CIA 登記過、還是只在 DY／vCenter 掃到（2026-09-18）。

## 為什麼要有這支

使用者在公司機看到首頁寫「在管 3,377 台」「另有 4,155 台未登記」，而分佈統計寫
「共 5,405 台」。**3,377 ＋ 4,155 ＝ 7,532，比全庫 5,405 還多 2,127**——
同一個畫面上的加減法就對不起來。

真因：兩個頭條各自去重，但**彼此不互斥**。首頁「在管」排掉帳外前綴（DYN-/VC-/AUTO-），
「未登記」只看帳外前綴；可是**同一台機器可以同時有一筆 CIA 登記和一筆 DY 登記**
（主機名＋IP 相同、序號不同），於是兩邊各算一次。全庫去重時只算一台，差額就跑出來了。

221 上這個重疊是 0 台（3,116 ＋ 1,142 ＝ 4,258 聯集，對得起來），公司那台不是——
**所以這不是程式壞掉，是公司的資料裡真的有大量「同一台被登記兩次」**，
而畫面沒有任何地方看得到是哪幾台。這支就是把那份名單攤開。

## 判準

- 去重鍵沿用正典 `system_stats.machine_key`（主機名不分大小寫＋IP；缺任一或 IP 是
  0.0.0.0 就退回序號各算一台）。**不另開一把尺**——全站只能有一把，不然又是兩套數字。
- 來源看序號前綴：`DYN-`＝DY、`VC-`＝vCenter、`AUTO-`＝系統自動補、其餘＝CIA 正式登記。
- **退役不排除**，但獨立成一欄。這頁是給人做樞紐分析的原始資料，先過濾就等於替使用者
  決定他要看什麼（使用者 2026-09-15：「不要幫我排除，你可以建議，但我自己選」）。

## 比對可信度

主機名和 IP 都有才比得準。只有其中一個的，`machine_key` 會退回序號 → **一定**被當成
獨立的一台，永遠不會跟別的來源配對成功。那不是「確定沒有重複」，是「無法判斷」，
所以獨立出 `match_basis` 欄位講清楚，不要讓人把 `只在DY` 誤讀成「CIA 真的沒有這台」。
"""
from __future__ import annotations

import system_stats
import manage_state as ms

#: 序號前綴 → 來源代號。順序決定 sources 欄位的排列。
SOURCE_BY_PREFIX = (("DYN-", "DY"), ("VC-", "VC"), ("AUTO-", "AUTO"))
SOURCE_CIA = "CIA"

#: 匯出可選欄位：key → 中文標題。前端的勾選清單直接吃這份，兩邊不會各寫一套。
COLUMNS: dict[str, str] = {
    "hostname": "主機名",
    "ip": "IP",
    "source_class": "來源分類",
    "in_cia": "在CIA",
    "in_dy": "在DY",
    "in_vc": "在vCenter",
    "in_auto": "在AUTO",
    "match_basis": "比對依據",
    "loose_match": "疑似同一台（放寬比對）",
    "loose_basis": "疑似依據",
    "cia_serial": "CIA序號",
    "offbook_serials": "帳外序號",
    "reg_count": "登記筆數",
    "retired": "已退役",
    "asset_status": "資產狀態",
    "environment": "環境",
    "physical_location": "機房",
    "os": "OS",
    "device_model": "設備機型",
    "is_vm": "虛擬機",
    "system_count": "系統數",
    "systems": "AP ID",
    "asset_name": "資產名稱",
    "asset_purpose": "用途",
    "usage_unit": "使用單位",
    "user_name": "使用者",
    "custodian": "保管者",
    "vm_uuid": "VM UUID",
    "mac": "MAC",
    "hw_serial": "硬體序號",
}

#: 預設勾選的欄位——使用者要的是「IP／主機名 × DY／CIA」，先給那幾欄，其餘自己加。
DEFAULT_COLUMNS = ("hostname", "ip", "source_class", "in_cia", "in_dy", "in_vc",
                   "match_basis", "loose_match", "cia_serial", "offbook_serials", "reg_count",
                   "retired", "environment", "physical_location", "system_count")

_SELECT = ("SELECT asset_serial, hostname, ip, environment, physical_location, os, "
           "device_model, is_vm, asset_status, asset_name, asset_purpose, api_id, "
           "usage_unit, user_name, custodian, vm_uuid, mac, hw_serial FROM hardware")

#: 放寬比對用的身分欄位，由強到弱。vm_uuid 是 VMware 給的唯一識別，比主機名／IP 可靠得多；
#: mac 會因為多網卡／虛擬網卡重複，hw_serial 在 221 幾乎沒人填（4785 筆只有 4 筆有值）。
#: ⚠️ 「NA」是匯入來源的填空字串，不是值——當成值比對會把一票不相干的機器串在一起。
IDENTITY_FIELDS = (("vm_uuid", "VM UUID 相同"), ("hw_serial", "硬體序號相同"), ("mac", "MAC 相同"))
_NOT_A_VALUE = {"", "na", "n/a", "none", "null", "0", "-"}


def _source_of(asset_serial: str | None) -> str:
    s = str(asset_serial or "")
    for prefix, code in SOURCE_BY_PREFIX:
        if s.startswith(prefix):
            return code
    return SOURCE_CIA


def _first(rows, col):
    """同一台多筆時取第一個非空值——欄位常常只有其中一筆填了。"""
    for r in rows:
        v = r[col]
        if v is not None and str(v).strip() != "":
            return v
    return None


def _ident(row, field: str) -> str:
    """取身分欄位的值；空字串與「NA」這類填空字一律視為沒有值。

    把「NA」當成值比對，會讓所有填 NA 的機器互相配對成同一台——那是最糟的假陽性。
    """
    try:
        v = row[field]
    except (IndexError, KeyError):
        return ""
    v = str(v or "").strip()
    return "" if v.lower() in _NOT_A_VALUE else v


def _loose(key, g, by_host, by_ip, by_id) -> tuple[str, str]:
    """殘缺列的放寬比對：只憑主機名、或只憑 IP，找出可能是同一台的其他台。

    回 (候選 machine_key 串, 依據)。完整列不做放寬比對——它已經有正規的鍵了。
    """
    if not key.startswith("sn:"):
        return "", ""
    hits, basis = set(), []
    # 先看身分欄位——vm_uuid 這種唯一識別比主機名／IP 可信，要排在前面講
    for f, label in IDENTITY_FIELDS:
        for r in g:
            v = _ident(r, f)
            if v and v in by_id[f]:
                other = by_id[f][v] - {key}
                if other:
                    hits |= other
                    basis.append(label)
    for r in g:
        h = (r["hostname"] or "").strip().lower()
        i = (r["ip"] or "").strip()
        if h and h in by_host:
            hits |= by_host[h]
            basis.append("主機名相同")
        if i and i != "0.0.0.0" and i in by_ip:
            hits |= by_ip[i]
            basis.append("IP 相同")
    hits.discard(key)
    if not hits:
        return "", ""
    # 依據去重保序，講清楚是憑什麼猜的
    seen, out = set(), []
    for b in basis:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return "／".join(sorted(hits)), "／".join(out)


def rows(conn) -> list[dict]:
    """一台一列。不過濾任何東西——過濾交給畫面與 Excel。"""
    groups: dict[str, list] = {}
    for r in conn.execute(_SELECT):
        groups.setdefault(
            system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"]), []
        ).append(r)

    # 放寬比對索引：只有主機名或只有 IP 的列，machine_key 會退回序號，永遠配不到別人。
    # 這裡用「單一欄位」去找候選，**只標示疑似、絕不自動合併**——把兩台不同的機器併成
    # 一台，比漏抓更糟（IP 會被回收、主機名會重複）。要不要認定同一台由人決定。
    by_host: dict[str, set[str]] = {}
    by_ip: dict[str, set[str]] = {}
    by_id: dict[str, dict[str, set[str]]] = {f: {} for f, _ in IDENTITY_FIELDS}
    for key, g in groups.items():
        for r in g:
            # 身分欄位（vm_uuid 等）**兩邊都收**：殘缺列彼此之間也可能是同一台，
            # 只用完整列當候選會漏掉「兩筆都缺主機名、但 VM UUID 一樣」那種。
            for f, _ in IDENTITY_FIELDS:
                v = _ident(r, f)
                if v:
                    by_id[f].setdefault(v, set()).add(key)
        if key.startswith("sn:"):
            continue                                   # 主機名／IP 索引只收完整列
        for r in g:
            h = (r["hostname"] or "").strip().lower()
            i = (r["ip"] or "").strip()
            if h:
                by_host.setdefault(h, set()).add(key)
            if i and i != "0.0.0.0":
                by_ip.setdefault(i, set()).add(key)

    out = []
    for key, g in groups.items():
        g = sorted(g, key=lambda x: str(x["asset_serial"] or ""))
        _lm = _loose(key, g, by_host, by_ip, by_id)   # 算一次，兩個欄位共用
        srcs = {_source_of(r["asset_serial"]) for r in g}
        cia_serials = [r["asset_serial"] for r in g if _source_of(r["asset_serial"]) == SOURCE_CIA]
        off_serials = [r["asset_serial"] for r in g if _source_of(r["asset_serial"]) != SOURCE_CIA]
        systems, seen = [], set()
        for r in g:
            code = (r["api_id"] or "").strip()
            if code and code not in seen:
                seen.add(code)
                systems.append(code)
        # 有 CIA 也有帳外 → 這台被算兩次；只有一邊 → 各自歸類
        # 「在管」跟首頁同一套定義：**有任一筆使用中（非退役）的 CIA 登記**就算。
        # 不可以寫成「任一筆退役就算退役」——同一台一筆報廢、一筆使用中時，首頁留著它，
        # 這裡卻踢掉，兩頁就差出一截（2026-09-18 公司機實測差 35 台：3,342 vs 3,377）。
        active_cia = any(_source_of(r["asset_serial"]) == SOURCE_CIA
                         and (r["asset_status"] or "").strip() not in ms.RETIRED_STATUS for r in g)
        has_off = len(srcs - {SOURCE_CIA}) > 0
        if active_cia and has_off:
            klass = "CIA＋帳外（重複登記）"
        elif active_cia:
            klass = "只在CIA"
        elif SOURCE_CIA in srcs:
            klass = "CIA 已退役" + ("＋帳外" if has_off else "")
        else:
            klass = "只在帳外（" + "／".join(sorted(srcs)) + "）"
        out.append({
            "machine_key": key,
            "hostname": _first(g, "hostname"),
            "ip": _first(g, "ip"),
            "source_class": klass,
            "in_cia": SOURCE_CIA in srcs,
            "in_dy": "DY" in srcs,
            "in_vc": "VC" in srcs,
            "in_auto": "AUTO" in srcs,
            # key 以 sn: 開頭＝主機名或 IP 缺一，配不到別的來源是必然，不是「確定沒有」
            "match_basis": "主機名＋IP" if not key.startswith("sn:") else "只有序號（無法比對）",
            "loose_match": _lm[0],
            "loose_basis": _lm[1],
            "cia_serial": "／".join(str(s) for s in cia_serials if s),
            "offbook_serials": "／".join(str(s) for s in off_serials if s),
            "reg_count": len(g),
            "active_cia": active_cia,
            # 已退役＝有 CIA 登記、但**全部**都退役了（跟首頁「排退役後還剩不剩」同一條線）
            "retired": (SOURCE_CIA in srcs) and not active_cia,
            "asset_status": _first(g, "asset_status"),
            "environment": _first(g, "environment"),
            "physical_location": _first(g, "physical_location"),
            "os": _first(g, "os"),
            "device_model": _first(g, "device_model"),
            "is_vm": bool(ms.is_vm_value(_first(g, "is_vm"), _first(g, "device_model"))),
            "system_count": len(systems),
            "systems": "／".join(systems),
            "asset_name": _first(g, "asset_name"),
            "asset_purpose": _first(g, "asset_purpose"),
            "usage_unit": _first(g, "usage_unit"),
            "user_name": _first(g, "user_name"),
            "custodian": _first(g, "custodian"),
            "vm_uuid": _first(g, "vm_uuid"),
            "mac": _first(g, "mac"),
            "hw_serial": _first(g, "hw_serial"),
        })
    out.sort(key=lambda h: (h["source_class"], (h["hostname"] or "").lower(), h["ip"] or ""))
    return out


def summary(items: list[dict]) -> dict:
    """表頭小計。每個數字都要能對回首頁／分佈統計，不然這張表自己也變成第三套數字。"""
    # 三類互斥且窮盡：在管（有使用中 CIA）／已退役（有 CIA 但全退役）／只在帳外（沒有 CIA）
    both = [h for h in items if h["active_cia"] and (h["in_dy"] or h["in_vc"] or h["in_auto"])]
    cia_live = [h for h in items if h["active_cia"]]
    off = [h for h in items if not h["in_cia"]]
    return {
        "total_hosts": len(items),                       # ＝分佈統計「共 N 台」（全庫去重）
        "total_rows": sum(h["reg_count"] for h in items),
        "cia_hosts": len(cia_live),                      # ＝首頁「在管 N 台」
        "offbook_only_hosts": len(off),
        "both_hosts": len(both),                         # ← 被兩個頭條各算一次的台數
        "retired_hosts": sum(1 for h in items if h["retired"]),
        "unmatchable_hosts": sum(1 for h in items if h["match_basis"].startswith("只有序號")),
        # 殘缺但放寬後找得到候選的——這些最值得人去看一眼
        "loose_candidate_hosts": sum(1 for h in items if h["loose_match"]),
    }
