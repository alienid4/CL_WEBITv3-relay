"""VIP（CIA 登記的「BIG IP/VIP」欄）的整理與查詢（2026-09-14）。

## 背景

CIA 資產清冊的單位是「每個服務／VIP 一筆」，不是一台一筆。實例：一台 VM
在清冊裡登記 12 筆＝3 個 VIP × 5 個系統，每筆「系統＋VIP＋用途」的組合都不同。
這種組**不是重複登記**，可是舊的重複偵測只比「主機名＋IP」，把它列成重複、
還建議「可少 11 筆」——照做就等於刪掉「這台也跑期貨官網」這件事。

## 這支模組做三件事

1. `normalize_vip`：「無」「N/A」「NA」「-」這類值一律當空白。
   只用於計算與比對，**CIA 原始資料不改**。
2. `classify_duplicate_group`：同主機同 IP 的多筆分三類——
   疑似真重複（有兩筆的系統＋VIP＋用途＋名稱完全一樣）／一機多系統／VIP·用途分列。
   只有第一類才算得出「可少幾筆」。
3. `entries` / `entry_detail`：從 VIP 欄反查「這個入口後面是哪幾台、影響哪些系統」。

## 限制（畫面上要講）

- 網路組說 **VIP 是浮動的**：VIP 不是機器的屬性，只是服務入口，而且會變。
  這裡的結果只代表「CIA 當初怎麼登記」，**未經 F5 設定驗證**。
- VIP 欄混了不是 VIP 的東西（AP ID、主機名、Windows 叢集 IP 如「x.x.x.x-s-cluster」），
  不硬塞進入口清單，另外列出來給人看，不吞掉。
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Iterable

#: 當空白的寫法（比對前會 casefold）。刻意只列看得到的實際值，不做模糊判斷。
_NULLISH_VIP = {"", "無", "n/a", "na", "-", "--", "none", "null", "無vip", "nil"}
_IP_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
_APID_RE = re.compile(r"^[A-Za-z]-\d+$")
_HOST_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9._-]*$")
#: IP 後面帶這些字的，多半是 Windows 叢集／SQL AG 的 IP，不是 F5 的 VIP（推論）
_CLUSTER_RE = re.compile(r"cluster|msdtc|listener", re.I)

KIND_VIP = "vip"
KIND_CLUSTER = "cluster"
KIND_APID = "apid"
KIND_HOSTNAME = "hostname"
KIND_TEXT = "text"
KIND_LABEL = {
    KIND_VIP: "VIP",
    KIND_CLUSTER: "疑似叢集 IP",
    KIND_APID: "填的是 AP ID",
    KIND_HOSTNAME: "填的是主機名",
    KIND_TEXT: "其他文字",
}

DUP_SUSPECT = "suspect"
DUP_MULTI_SYSTEM = "multi_system"
DUP_SPLIT = "split"
DUP_KIND_LABEL = {
    DUP_SUSPECT: "疑似真重複",
    DUP_MULTI_SYSTEM: "一機多系統",
    DUP_SPLIT: "VIP／用途分列",
}
DUP_KIND_ORDER = (DUP_SUSPECT, DUP_MULTI_SYSTEM, DUP_SPLIT)
NO_APID = "（沒填 AP ID）"


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def normalize_vip(raw: Any) -> str:
    """「無」「N/A」這類一律回空字串；其餘去頭尾空白原樣回。"""
    s = _s(raw)
    return "" if s.casefold() in _NULLISH_VIP else s


def parse_vip(raw: Any) -> dict:
    """拆一格 VIP 欄：回 {value, ips, kind}。kind 為 None 代表空白。"""
    value = normalize_vip(raw)
    if not value:
        return {"value": "", "ips": [], "kind": None}
    ips = _IP_RE.findall(value)
    if ips:
        rest = _IP_RE.sub("", value)
        return {"value": value, "ips": ips,
                "kind": KIND_CLUSTER if _CLUSTER_RE.search(rest) else KIND_VIP}
    if _APID_RE.match(value):
        return {"value": value, "ips": [], "kind": KIND_APID}
    if _HOST_RE.match(value):
        return {"value": value, "ips": [], "kind": KIND_HOSTNAME}
    return {"value": value, "ips": [], "kind": KIND_TEXT}


def classify_duplicate_group(rows: Iterable[dict]) -> dict:
    """同主機同 IP 的一組多筆，判是哪一類。

    - 有兩筆的（AP ID, VIP, 用途, 資產名稱）完全一樣 → 疑似真重複；
      可少筆數＝筆數 − 不同組合數（只少掉一模一樣的那幾筆，不是整組只留一筆）
    - 否則 AP ID 不只一種 → 一機多系統
    - 否則（同系統，VIP 或用途不同）→ VIP／用途分列
    後兩類**不是重複**，不給「可少」。
    """
    rows = list(rows)
    combos = {(_s(r.get("api_id")), normalize_vip(r.get("big_ip_vip")),
               _s(r.get("asset_purpose")), _s(r.get("asset_name"))) for r in rows}
    systems = sorted({_s(r.get("api_id")) for r in rows} - {""})
    vips = sorted({normalize_vip(r.get("big_ip_vip")) for r in rows} - {""})
    extra = len(rows) - len(combos)
    if extra > 0:
        kind = DUP_SUSPECT
    elif len(systems) > 1:
        kind = DUP_MULTI_SYSTEM
    else:
        kind = DUP_SPLIT
    return {"kind": kind, "kind_label": DUP_KIND_LABEL[kind],
            "extra_rows": max(extra, 0), "systems": systems, "vips": vips}


def duplicate_summary(conn: sqlite3.Connection) -> dict:
    """給儀表板用的彙總，判準跟 /api/assets/duplicates 一致
    （排除退役、排除人工標記「不是重複」的組）。"""
    import manage_state as ms
    import system_stats as ss

    # 人工按過「不是重複」的組：鍵也要用同一把尺算，不然鍵格式不同、那份名單整個失效
    try:
        dismissed = {ss.machine_key(r[0], r[1], None) for r in
                     conn.execute("SELECT hostname, ip FROM duplicate_dismiss").fetchall()}
    except sqlite3.Error:
        dismissed = set()
    groups: dict[str, list[dict]] = {}
    cur = conn.execute(
        "SELECT hostname, ip, api_id, big_ip_vip, asset_purpose, asset_name, asset_status "
        "FROM hardware WHERE hostname IS NOT NULL AND length(trim(hostname)) > 0 "
        "AND ip IS NOT NULL AND length(trim(ip)) > 0")
    cols = [d[0] for d in cur.description]
    for tup in cur.fetchall():
        r = dict(zip(cols, tup))
        if _s(r["asset_status"]) in ms.RETIRED_STATUS:
            continue
        # [B-03] 用全站同一把尺（machine_key：含網域處理），不再自己拼 (主機名, IP)
        groups.setdefault(ss.machine_key(r["hostname"], r["ip"], None), []).append(r)
    out = {"groups": 0, "suspect_groups": 0, "suspect_extra_rows": 0,
           "multi_system_groups": 0, "split_groups": 0}
    for key, rows in groups.items():
        if len(rows) < 2 or key in dismissed:
            continue
        c = classify_duplicate_group(rows)
        out["groups"] += 1
        out[f"{c['kind']}_groups"] += 1
        out["suspect_extra_rows"] += c["extra_rows"]
    return out


def _rows(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute("""
        SELECT h.asset_serial, h.hostname, h.ip, h.api_id, h.big_ip_vip, h.asset_purpose,
               h.environment, h.physical_location, b.name AS system_name
        FROM hardware h LEFT JOIN business_system b ON b.api_id = h.api_id
    """)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, tup)) for tup in cur.fetchall()]


def entries(conn: sqlite3.Connection) -> dict:
    """每個入口（VIP 欄裡的 IP）後面幾台主機、影響幾個系統。"""
    import system_stats as ss

    ents: dict[str, dict] = {}
    others: dict[str, dict] = {}
    rows = _rows(conn)
    empty = 0
    for r in rows:
        p = parse_vip(r["big_ip_vip"])
        if p["kind"] is None:
            empty += 1
            continue
        key = ss.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        api = _s(r["api_id"])
        if p["ips"]:
            for ip in p["ips"]:
                e = ents.setdefault(ip, {"vip": ip, "cluster": False, "machines": set(),
                                         "systems": set(), "rows": 0, "raw": set()})
                e["machines"].add(key)
                if api:
                    e["systems"].add(api)
                e["rows"] += 1
                e["raw"].add(p["value"])
                e["cluster"] = e["cluster"] or p["kind"] == KIND_CLUSTER
            continue
        o = others.setdefault(p["value"], {"value": p["value"], "kind": p["kind"],
                                           "machines": set(), "rows": 0})
        o["machines"].add(key)
        o["rows"] += 1

    def _ent(e: dict) -> dict:
        kind = KIND_CLUSTER if e["cluster"] else KIND_VIP
        return {"vip": e["vip"], "kind": kind, "kind_label": KIND_LABEL[kind],
                "hosts": len(e["machines"]), "systems": len(e["systems"]),
                "rows": e["rows"], "raw_values": sorted(e["raw"])}

    return {
        "entries": sorted((_ent(e) for e in ents.values()),
                          key=lambda x: (-x["hosts"], x["vip"])),
        "others": sorted(({"value": o["value"], "kind": o["kind"],
                           "kind_label": KIND_LABEL[o["kind"]],
                           "hosts": len(o["machines"]), "rows": o["rows"]}
                          for o in others.values()),
                         key=lambda x: (-x["rows"], x["value"])),
        "empty_rows": empty,
        "total_rows": len(rows),
        "basis": "CIA 登記的 BIG IP/VIP 欄，未經 F5 設定驗證；VIP 會浮動，只代表登記當時",
    }


def entry_detail(conn: sqlite3.Connection, value: str) -> dict:
    """一個入口（或一個非 IP 的值）後面是哪幾台、各掛哪些系統。找不到丟 LookupError。"""
    import system_stats as ss

    v = _s(value)
    if not v:
        raise ValueError("要給一個入口（VIP）")
    is_ip = bool(_IP_RE.fullmatch(v))
    machines: dict[str, dict] = {}
    systems: dict[str, dict] = {}
    n = 0
    for r in _rows(conn):
        p = parse_vip(r["big_ip_vip"])
        hit = (v in p["ips"]) if is_ip else (not p["ips"] and p["value"] == v)
        if not hit:
            continue
        n += 1
        key = ss.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        m = machines.setdefault(key, {
            "hostname": r["hostname"], "ip": r["ip"],
            "environment": ss.normalize_env(r["environment"]),
            "location": ss.normalize_location(r["physical_location"]),
            "systems": [], "purposes": [], "serials": []})
        m["serials"].append(r["asset_serial"])
        api = _s(r["api_id"])
        label = (api + " " + _s(r["system_name"])).strip() if api else NO_APID
        if label not in m["systems"]:
            m["systems"].append(label)
        purpose = _s(r["asset_purpose"])
        if purpose and purpose not in m["purposes"]:
            m["purposes"].append(purpose)
        s = systems.setdefault(api or NO_APID, {"api_id": api or None, "name": r["system_name"],
                                               "machines": set(), "rows": 0})
        s["machines"].add(key)
        s["rows"] += 1
    if not n:
        raise LookupError(f"CIA 登記裡沒有這個入口：{v}")
    out_m = []
    for m in machines.values():
        m["serials"].sort()
        m["asset_serial"] = m["serials"][0]
        m["row_count"] = len(m["serials"])
        m["systems_text"] = "、".join(m["systems"])
        m["purposes_text"] = "、".join(m["purposes"])
        out_m.append(m)
    out_m.sort(key=lambda x: (_s(x["hostname"]), _s(x["ip"])))
    return {
        "value": v, "is_ip": is_ip, "rows": n, "hosts": len(out_m),
        "machines": out_m,
        "systems": sorted(({"api_id": s["api_id"], "name": s["name"],
                            "hosts": len(s["machines"]), "rows": s["rows"]}
                           for s in systems.values()),
                          key=lambda x: (-x["hosts"], _s(x["api_id"]))),
        "basis": "CIA 登記的 BIG IP/VIP 欄，未經 F5 設定驗證",
    }


def host_vips(conn: sqlite3.Connection, hostname: Any, ip: Any, retired: bool = False) -> dict:
    """從「一台主機」的角度看它掛了哪些入口，以及每個入口後面還有誰（2026-09-18）。

    ## 為什麼要有這支

    使用者在詳細頁看到一台主機的 13 筆登記上有好幾個 VIP，問「是綁多 IP 還是 VIP」，
    接著要求「一個 IP 綁多 VIP 這種怎麼呈現，你要想辦法」。

    221 實測 VIP 跟主機是**多對多**：220 個 VIP 裡 186 個後面有 2 台以上（最多 21 台），
    每台主機掛 1～4 個 VIP。所以對一台主機來說，真正有用的不是「VIP 有哪幾個」，而是
    **每個入口：這台上跑哪些服務、同一入口後面還有誰**——那直接回答維運最常問的
    「這台關掉，服務會不會斷」。

    ## 口徑

    - 同一台＝同主機名＋同 IP，且**同生命週期**（跟別名 v1.213.1 同規則：報廢≠使用中）
    - 同入口的其他主機**排除退役**：已報廢的機器不能算備援。退役的另外計數，不吞掉
    - VIP 欄的解析沿用 `parse_vip`（叢集 IP、AP ID、主機名混入都已分類）
    - ⚠️ 資料來源是 CIA 登記，**未經 F5 設定驗證**，而且 VIP 會浮動。畫面措辭必須是
      「CIA 登記上只有這台」，不可以寫成「單點故障」——那是我們沒查證的事
    """
    import system_stats as ss
    import manage_state as ms

    h, i = _s(hostname).lower(), _s(ip)
    if not h or not i or i == "0.0.0.0":
        return {"vips": [], "misfiled": [], "basis": None}

    rows = conn.execute(
        "SELECT h.asset_serial, h.hostname, h.ip, h.big_ip_vip, h.asset_name, h.asset_purpose, "
        "h.api_id, h.environment, h.asset_status, b.name AS system_name "
        "FROM hardware h LEFT JOIN business_system b ON b.api_id = h.api_id").fetchall()
    cols = ["asset_serial", "hostname", "ip", "big_ip_vip", "asset_name", "asset_purpose",
            "api_id", "environment", "asset_status", "system_name"]
    rows = [dict(zip(cols, r)) for r in rows]
    self_key = ss.machine_key(hostname, ip, None)

    def is_ret(r):
        return _s(r["asset_status"]) in ms.RETIRED_STATUS

    mine: dict[str, dict] = {}
    misfiled = []
    for r in rows:
        if _s(r["hostname"]).lower() != h or _s(r["ip"]) != i or is_ret(r) != retired:
            continue
        p = parse_vip(r["big_ip_vip"])
        if p["kind"] is None:
            continue
        if not p["ips"]:
            misfiled.append({"value": p["value"], "kind": p["kind"],
                             "kind_label": KIND_LABEL[p["kind"]], "asset_serial": r["asset_serial"]})
            continue
        svc = _s(r["asset_name"]) or _s(r["asset_purpose"]) or _s(r["asset_serial"])
        api = _s(r["api_id"])
        for vip in p["ips"]:
            e = mine.setdefault(vip, {"vip": vip, "cluster": False, "raw": set(), "services": []})
            e["cluster"] = e["cluster"] or p["kind"] == KIND_CLUSTER
            e["raw"].add(p["value"])
            item = {"name": svc, "api_id": api or None, "system_name": r["system_name"],
                    "asset_serial": r["asset_serial"]}
            if item not in e["services"]:
                e["services"].append(item)

    if not mine:
        return {"vips": [], "misfiled": misfiled, "basis": None}

    # 同入口後面的主機（排除退役；退役的另外計數）
    pools: dict[str, dict] = {v: {} for v in mine}
    retired_also: dict[str, set] = {v: set() for v in mine}
    for r in rows:
        p = parse_vip(r["big_ip_vip"])
        for vip in p["ips"]:
            if vip not in pools:
                continue
            key = ss.machine_key(r["hostname"], r["ip"], r["asset_serial"])
            if is_ret(r):
                retired_also[vip].add(key)
                continue
            m = pools[vip].setdefault(key, {
                "asset_serial": r["asset_serial"], "hostname": r["hostname"], "ip": r["ip"],
                "environment": r["environment"], "is_self": key == self_key})
            if _s(r["asset_serial"]) < _s(m["asset_serial"]):
                m["asset_serial"] = r["asset_serial"]      # 代表序號＝最小，跟全站一致

    out = []
    for vip, e in mine.items():
        pool = sorted(pools[vip].values(), key=lambda m: (not m["is_self"], _s(m["hostname"])))
        # 退役那台如果同時也在使用中的池裡（同 key 另有使用中登記），不重複算
        ret_n = len(retired_also[vip] - set(pools[vip]))
        kind = KIND_CLUSTER if e["cluster"] else KIND_VIP
        out.append({
            "vip": vip, "kind": kind, "kind_label": KIND_LABEL[kind],
            "raw_values": sorted(e["raw"]),
            "services_here": e["services"],
            "pool": pool,
            "pool_size": len(pool),
            "retired_also": ret_n,
        })
    out.sort(key=lambda x: (-len(x["services_here"]), x["vip"]))
    return {
        "vips": out,
        "misfiled": misfiled,
        "basis": "CIA 登記的 BIG IP/VIP 欄，未經 F5 設定驗證；VIP 會浮動，只代表登記當時",
    }
