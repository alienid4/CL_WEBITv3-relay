"""[B-09] 數字可舉證——第一刀：納管漏斗的 5 個數字（2026-09-18）。

總數、已納管、失聯、未涵蓋、AIX。每個數字要能回答三件事：
1. **怎麼算的**（EXPLAIN：怎麼算、資料來源、已知限制、零的意義）——畫面 ⓘ 顯示
2. **是哪幾台**——畫面點數字下鑽，筆數要跟數字一樣
3. **對不對**——健檢頁：畫面值／獨立重算值／差異／差異解釋

⚠️ 獨立重算（independent）**不准呼叫** manage_state.classify／pipeline.summarize 的判定邏輯：
照規格（EXPLAIN 寫的定義）直接查資料表重寫一次。同一套程式寫兩次只會一致地錯，
所以這裡刻意換一種寫法——逐台看「這台有沒有哪一筆符合定義」，而不是逐筆判狀態再挑代表。
兩種寫法算出來不一樣時，差異逐台列出、自動歸因；歸不了因的標「未解釋」，那就是要查的 bug。

唯一共用的是 system_stats.machine_key——「同一台」全站只能有一把尺（test_single_machine_key 守）。
"""
from __future__ import annotations

import ipaddress

import system_stats

RETIRED = {"停用", "報廢", "閒置"}
# 已納管在漏斗上分成四關（收得到之後還差什麼）；合起來就是「已納管」
ONBOARDED_STAGES = ["no_facts", "no_services", "no_accounts", "complete"]

KEYS = ["total", "onboarded", "lost", "not_covered", "aix"]

EXPLAIN = {
    "total": {
        "label": "總數",
        "how": "每台機器算一次（主機名不分大小寫＋IP 相同＝同一台；同一台在 CIA 登記多筆只算一台）。"
               "範圍＝CIA 清冊上的全部機器（含退役、帳外）＋最近一次掃描掃到但 CIA 沒登記的。",
        "source": "hardware（CIA 清冊＋dynassets／vCenter 帳外）、scan_history（最近一次掃描）",
        "limits": "主機名或 IP 缺一個、或 IP 是 0.0.0.0 佔位時，改用資產序號各算一台——同一台若兩筆都缺，會被算成兩台。",
        "zero": "0＝CIA 還沒匯入、也還沒掃描過（沒資料），不是「沒有機器」。",
    },
    "onboarded": {
        "label": "已納管",
        "how": "CIA 有登記、沒標退役、這次掃描掃得到、而且收集帳號連得上（上次試連成功）的機器。"
               "漏斗上分四關：還沒收主機事實／還沒收服務／還沒盤帳號／資料齊全，四關加起來就是這個數。",
        "source": "hardware.collect_ok（收集試連結果）、scan_history（最近一次掃描）、hardware.asset_status",
        "limits": "「連得上」看的是上次試連的結果；試連之後帳號被刪、金鑰被換，要等下次試連才會反映。",
        "zero": "0 要看有沒有試連過：一台都沒試過＝沒查；試過都連不上＝查過真的沒有。",
    },
    "lost": {
        "label": "失聯",
        "how": "CIA 有登記、沒標退役、IP 在這次掃描涵蓋的網段內，但這次沒掃到的機器。"
               "以前收得到過也算——人現在不在，比「曾經收得到」重要。",
        "source": "hardware、scan_history（最近一次掃描存活名單）、scan_coverage（這次掃了哪些網段）",
        "limits": "掃描只探幾個固定埠＋ICMP，防火牆全擋的機器也會被算成失聯；"
                  "標了非納管、或 SAN 收集／離線匯入過、而且收不到的設備不算失聯（本來就不靠掃描判定）；"
                  "沒有 IP 的另列「沒有 IP（無法掃描）」，不算失聯——掃描本來就找不到它。",
        "zero": "0 要看有沒有掃描紀錄：沒掃過＝沒查；掃過且涵蓋了這些網段＝查過真的沒有。",
    },
    "not_covered": {
        "label": "未涵蓋",
        "how": "CIA 有登記、沒標退役、這次沒掃到（以前收得到過也算），而且 IP 根本不在這次掃描涵蓋的網段內的機器——"
               "我們沒有證據說它在不在，要做的事是把網段加進掃描範圍，不是去機房找。",
        "source": "hardware、scan_coverage（這次掃描成功掃完的網段）",
        "limits": "看的是「最近一次」掃描：最近一次只掃了一小段，其他網段的機器全部會變未涵蓋。"
                  "這次掃描沒有涵蓋紀錄（舊版掃描、或掃描範圍沒套用）時，無法判斷涵蓋，機器照舊算失聯、這裡是 0。",
        "zero": "0 要看有沒有涵蓋紀錄：沒有涵蓋紀錄＝沒查（全部被算進失聯）；有紀錄＝查過真的都涵蓋了。",
    },
    "aix": {
        "label": "AIX",
        "how": "CIA 上 OS 欄填 AIX 的機器（同一台多筆登記取漏斗代表那筆）；人工改過 OS 類型的以人工為準。",
        "source": "hardware.os（CIA 清冊）、os_type_override（人工指定）",
        "limits": "⚠️ 未登記的 AIX 從網路上分不出來（只看得到開了哪些埠），會被歸到其他類或「推測 Linux 類」。",
        "zero": "0＝CIA 上沒有 OS 填 AIX 的機器（查過）；但未登記的 AIX 本來就數不到。",
    },
}


def _net_list(conn, scan_time):
    """這次掃描涵蓋的網段——直接讀 scan_coverage，不借 scan_scope 的函式。"""
    if not scan_time:
        return []
    try:
        rows = conn.execute("SELECT DISTINCT cidr FROM scan_coverage WHERE scan_time = ? AND ok = 1",
                            (scan_time,)).fetchall()
    except Exception:  # noqa: BLE001 - 舊庫沒這張表＝沒有涵蓋紀錄
        return []
    out = []
    for (c,) in rows:
        try:
            out.append(ipaddress.ip_network(c, strict=False))
        except (ValueError, TypeError):
            pass
    return out


def _addrs(raw) -> list:
    """IP 欄裡認得出的位址（一格可能填好幾個）；0.0.0.0 佔位不算。自己拆，不借 manage_state.ips_of。"""
    out = []
    s = str(raw or "")
    for ch in "／、,;/":
        s = s.replace(ch, " ")
    for tok in s.split():
        try:
            a = ipaddress.ip_address(tok)
        except ValueError:
            continue
        if int(a) != 0:
            out.append(a)
    return out


def _in_nets(addrs, nets) -> bool:
    if not nets:
        return True              # 沒有涵蓋紀錄：無法說它沒涵蓋
    return any(a in n for a in addrs for n in nets)


def independent(conn) -> dict:
    """照 EXPLAIN 的定義獨立重算。回 {key: set(machine_key)} 與佐證事實。"""
    st = conn.execute("SELECT MAX(scan_time) FROM scan_history").fetchone()[0]
    alive = conn.execute("SELECT ip, hostname FROM scan_history WHERE scan_time = ? AND scan_ok = 1",
                         (st,)).fetchall() if st else []
    a_ip = {r[0] for r in alive if r[0]}
    a_hn = {r[1] for r in alive if r[1]}
    nets = _net_list(conn, st)
    try:
        exempt = {r[0] for r in conn.execute(
            "SELECT asset_serial FROM onboard_exempt WHERE removed_at IS NULL")}
    except Exception:  # noqa: BLE001
        exempt = set()
    try:
        san_ips = {r[0] for r in conn.execute("SELECT ip FROM san_switch") if r[0]}
    except Exception:  # noqa: BLE001
        san_ips = set()

    machines: dict[str, list] = {}
    hw_ip, hw_hn = set(), set()
    for r in conn.execute("SELECT asset_serial, hostname, ip, os, asset_status, collect_ok FROM hardware"):
        machines.setdefault(system_stats.machine_key(r[1], r[2], r[0]), []).append(r)
        if r[2]:
            hw_ip.add(r[2])
        if r[1]:
            hw_hn.add(r[1])
    unreg = set()
    for ip, hn in alive:
        if (ip and ip in hw_ip) or (hn and hn in hw_hn) or not (ip or hn):
            continue
        unreg.add(system_stats.machine_key(hn, ip, None))

    out = {k: set() for k in KEYS}
    out["total"] = set(machines) | unreg
    for k, rows in machines.items():
        if any("aix" in (r[3] or "").lower() for r in rows):
            out["aix"].add(k)
        if any((r[4] or "").strip() in RETIRED for r in rows):
            continue                      # 有任何一筆標退役：不屬於下面三個（退役或登記矛盾）
        seen = any((r[2] and r[2] in a_ip) or (r[1] and r[1] in a_hn)
                   or any(str(a) in a_ip for a in _addrs(r[2])) for r in rows)
        ok = any(r[5] == 1 for r in rows)
        if seen:
            if ok:
                out["onboarded"].add(k)
            continue
        # 這次沒掃到：以前收得到過也照算（人現在不在，比「曾經收得到」重要）；
        # 只有「豁免／SAN 收集過」而且收不到的才不算——那些本來就不靠掃描判定
        if not ok and (any(r[0] in exempt for r in rows) or any(r[2] in san_ips for r in rows)):
            continue
        if not any(_addrs(r[2]) for r in rows):
            continue                      # 沒有可掃描的 IP：沒查，不算失聯也不算未涵蓋（另列「沒有 IP」）
        if all(_in_nets(_addrs(r[2]), nets) for r in rows if _addrs(r[2])):
            out["lost"].add(k)
        else:
            out["not_covered"].add(k)

    tried = conn.execute("SELECT COUNT(*) FROM hardware WHERE collect_checked_at IS NOT NULL "
                         "OR collect_ok IS NOT NULL").fetchone()[0]
    return {"sets": out, "evidence": {
        "scan_time": st, "alive": len(alive), "covered_segments": len(nets),
        "hardware_rows": sum(len(v) for v in machines.values()), "collect_tried_rows": tried,
    }}


def screen_sets(summary: dict) -> dict:
    """畫面值：漏斗（pipeline.summarize）實際給畫面的那幾台，逐台 machine_key。"""
    out = {k: set() for k in KEYS}
    for it in summary["items"]:
        k = system_stats.machine_key(it.get("hostname"), it.get("ip"), it.get("asset_serial"))
        out["total"].add(k)
        if it["stage"] in ONBOARDED_STAGES:
            out["onboarded"].add(k)
        if it["stage"] == "lost":
            out["lost"].add(k)
        if it["stage"] == "not_covered":
            out["not_covered"].add(k)
        if it.get("os_type") == "AIX":
            out["aix"].add(k)
    return out


def zero_status(key: str, value: int, ev: dict) -> dict:
    """這個數字是 0 的時候：查過真的沒有，還是沒查？非 0 也回（畫面 ⓘ 用得到）。"""
    if key == "total":
        checked = ev["hardware_rows"] > 0 or bool(ev["scan_time"])
        why = "有 CIA 資料或掃描紀錄" if checked else "CIA 沒匯入、也沒掃描過"
    elif key == "onboarded":
        checked = ev["collect_tried_rows"] > 0
        why = f"試連過 {ev['collect_tried_rows']} 筆" if checked else "收集試連一次都沒跑過"
    elif key == "lost":
        checked = bool(ev["scan_time"])
        # 「查過」只對掃過的網段成立——只掃一段的 0 跟掃遍全公司的 0 不一樣，要講出來
        why = (f"最近一次掃描 {ev['scan_time']}，只查了涵蓋的 {ev['covered_segments']} 段；其他網段的機器在「未涵蓋」"
               if checked and ev["covered_segments"] else
               f"最近一次掃描 {ev['scan_time']}（沒有涵蓋紀錄，視為全部都掃了）" if checked else "沒有任何掃描紀錄")
    elif key == "not_covered":
        checked = ev["covered_segments"] > 0
        why = (f"這次掃描有 {ev['covered_segments']} 段涵蓋紀錄" if checked
               else "這次掃描沒有涵蓋紀錄——無法判斷，全部照舊算失聯")
    else:
        checked = ev["hardware_rows"] > 0
        why = "查了 CIA 的 OS 欄" if checked else "CIA 沒匯入"
    text = None
    if value == 0:
        text = ("查過真的沒有" if checked else "沒查") + f"（{why}）"
    return {"checked": checked, "why": why, "text": text}


def _why(key: str, it: dict | None, ov: bool) -> str:
    """差異逐台歸因：畫面怎麼判、為什麼跟重算不同。"""
    if it is None:
        return "畫面沒有這台（不在漏斗母體）"
    st = it["stage"]
    if key == "aix" and ov:
        return "人工改過 OS 類型（以人工為準）"
    if key == "aix":
        if it.get("os_type") == "AIX":
            return "重算看不到 AIX 字樣，畫面判成 AIX"
        return f"同一台多筆登記的 OS 不一致，畫面代表那筆判成「{it.get('os_type')}」"
    if st == "conflict":
        return "畫面歸到「登記矛盾」（同一台有退役也有使用中）"
    if st == "retired_alive":
        return "畫面歸到「退役但仍在線」"
    if st in ("exempt", "collected"):
        return f"畫面歸到「{it.get('stage_label')}」（豁免／已收集的設備優先）"
    return f"畫面歸到「{it.get('stage_label')}」——未解釋"


def health(conn) -> dict:
    """健檢頁：5 個數字的 畫面值／獨立重算值／差異／差異解釋。"""
    import os_override
    import pipeline

    s = pipeline.summarize(conn)
    scr = screen_sets(s)
    ind = independent(conn)
    ev = ind["evidence"]
    by_key = {system_stats.machine_key(it.get("hostname"), it.get("ip"), it.get("asset_serial")): it
              for it in s["items"]}
    ovs = {k for k, it in by_key.items() if it.get("os_type_override")}

    rows = []
    for key in KEYS:
        a, b = scr[key], ind["sets"][key]
        only_screen, only_recalc = sorted(a - b), sorted(b - a)
        groups: dict[str, list] = {}
        for k in only_screen:
            groups.setdefault("畫面有、重算沒有：" + _why(key, by_key.get(k), k in ovs), []).append(k)
        for k in only_recalc:
            groups.setdefault("重算有、畫面沒有：" + _why(key, by_key.get(k), k in ovs), []).append(k)
        expl = [{"why": w, "n": len(ks), "unexplained": "未解釋" in w,
                 "sample": [_brief(by_key.get(k), k) for k in ks[:20]]}
                for w, ks in sorted(groups.items(), key=lambda x: -len(x[1]))]
        rows.append({
            "key": key, **EXPLAIN[key],
            "screen": len(a), "recalc": len(b), "diff": len(a) - len(b),
            "mismatch": len(only_screen) + len(only_recalc),
            "explanations": expl,
            "unexplained": sum(e["n"] for e in expl if e["unexplained"]),
            "zero": zero_status(key, len(a), ev),
        })
    return {"scan_time": s.get("scan_time"), "evidence": ev, "numbers": rows}


def _brief(it: dict | None, key: str) -> dict:
    if it is None:
        return {"machine": key}
    return {"machine": key, "hostname": it.get("hostname"), "ip": it.get("ip"),
            "asset_serial": it.get("asset_serial"), "stage_label": it.get("stage_label"),
            "os_type": it.get("os_type")}


def key_numbers(conn, summary: dict) -> dict:
    """漏斗頁頂端 5 個數字＋ⓘ說明＋下鑽條件。數字直接從 summary 數，跟下面的表同一份。"""
    ev = _evidence_light(conn, summary)
    c = summary["counts"]
    vals = {
        "total": summary["total"],
        "onboarded": sum(c.get(k, 0) for k in ONBOARDED_STAGES),
        "lost": c.get("lost", 0),
        "not_covered": c.get("not_covered", 0),
        "aix": summary["os_counts"].get("AIX", 0),
    }
    drill = {
        "total": {}, "onboarded": {"stages": ONBOARDED_STAGES}, "lost": {"stages": ["lost"]},
        "not_covered": {"stages": ["not_covered"]}, "aix": {"os_type": ["AIX"]},
    }
    return {"items": [{"key": k, "value": vals[k], "drill": drill[k], **EXPLAIN[k],
                       "zero": zero_status(k, vals[k], ev)} for k in KEYS]}


def _evidence_light(conn, summary: dict) -> dict:
    """ⓘ 用的佐證（便宜的幾個 COUNT，不跑獨立重算）。"""
    st = summary.get("scan_time")
    return {
        "scan_time": st,
        "covered_segments": len(_net_list(conn, st)),
        "hardware_rows": conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0],
        "collect_tried_rows": conn.execute(
            "SELECT COUNT(*) FROM hardware WHERE collect_checked_at IS NOT NULL "
            "OR collect_ok IS NOT NULL").fetchone()[0],
    }
