"""納管漏斗：每台機器現在走到哪一關，以及下一步該做什麼。

## 為什麼要有這頁（使用者 2026-08-16）

「測試機我有 300 台，要慢慢一台一台匯，但我至少要知道**哪些是我還需要處理的**。」

在這之前，系統回答得了「總共幾台」「有幾台已納管」，卻回答不了「**這一台走到哪、
下一步要做什麼**」。四態（未登記／未納管／已納管／失聯）只分到「連不連得進去」為止；
但連得進去之後還有好幾關——事實收了沒、服務收了沒、帳號盤點收了沒——那些關卡沒有
任何畫面，於是 300 台裡誰還缺什麼，只能靠人一台一台點進詳細頁看。

## 設計：一台機器剛好落在一關

關卡是**有序**的，每台機器落在「它還沒完成的第一關」。互斥且窮盡，所以各關加總
必定等於母體——這是對帳的基礎，也是這類看板可信的前提（數字對不起來就是有 bug，
不是「大概差不多」）。

四態的判定**直接沿用 manage_state.summarize**，不另外寫一套。理由是慘痛的：同一件事
兩處各算一次，遲早算出不同答案，然後兩個畫面互相打臉而沒人知道哪個對。這裡只做一件
事——把「已納管」那一格再往後拆成幾關。

## 失聯是終點不是關卡

登記在案卻掃不到的機器，問題不在「還沒收資料」而在「這台到底還在不在」，
下一步是去確認機器狀態，不是繼續往下收。所以它獨立成一關、排在最後。
"""
from __future__ import annotations

import re

# 關卡定義：順序有意義（越後面越完整）。key 進 API 與畫面，label 給人看。
STAGES = [
    {
        "key": "unregistered",
        "label": "掃到但沒登記",
        "tone": "warn",
        "why": "網路上有回應，但資產清單裡沒有它",
        "next": "確認它是什麼，然後在「納入管理」建成資產",
        "action": "adopt",
    },
    {
        "key": "not_onboarded",
        "label": "已登記，進不去",
        "tone": "warn",
        "why": "是資產，但收集帳號連不進去",
        "next": "對這台執行納管（一鍵納管，或在該機貼一行指令）",
        "action": "onboard",
    },
    {
        # 2026-09-09：使用者清空重匯之後，本來已納管的機器全部退回「已登記，進不去」，
        # 下一步寫「對這台執行納管」——**那是錯的指示**。那些機器上的收集帳號與
        # 金鑰還在，該做的是收一輪，不是再納管一次（重納管會白跑，而且是對正式
        # 機器動手）。
        #
        # 差別來自判定依據：`manage_state.classify` 看的是「現在收不收得到」，
        # 收集資料被清掉就變成「還沒試過」→ 一律歸未納管。它從來沒看過
        # `onboard_audit`——而那張表正是「我們在那台建過帳號」的證據，
        # 而且 v1.69.0 之後清空不會刪它。
        #
        # 這一關就是把那個證據接回來。**不直接顯示成「已納管」**：我們只知道
        # 建過帳號，不知道現在還通不通（可能被別人清掉、可能機器重灌），
        # 那是「未驗證」不是「完成」。
        "key": "onboarded_stale",
        "label": "納管過，還沒重新收集",
        "tone": "info",
        "why": "有納管成功的紀錄，但這輪還沒收到資料（多半是清空／重匯之後）",
        "next": "不要重納管——直接收一輪就好；收不到再查是不是金鑰被移除",
        "action": "collect",
    },
    {
        "key": "no_facts",
        "label": "進得去，還沒收到主機事實",
        "tone": "info",
        "why": "連得上，但作業系統／序號／機型還是空的",
        "next": "等下一輪收集，或在資產詳細頁立即收一次",
        "action": "collect",
    },
    {
        "key": "no_services",
        "label": "有主機事實，還沒收服務",
        "tone": "info",
        "why": "知道它是什麼機器了，但不知道上面在跑什麼",
        "next": "到「服務盤點」對已納管主機收一輪",
        "action": "services",
    },
    {
        "key": "no_accounts",
        "label": "有服務，還沒盤點帳號",
        "tone": "info",
        "why": "服務清單有了，但帳號稽核還沒收",
        "next": "到「帳號盤點」收一輪（目前只支援 Linux）",
        "action": "accounts",
    },
    {
        # 2026-09-15：SAN switch 等設備是「收集」不是「納管」，沒有主機事實／服務／帳號可收。
        # 放在資料齊全旁邊、**不算還要處理**（見 TODO_STAGES）——不然 100 台裡 10 台已收集
        # 會被算進「有問題」，數字從 70 變 80。
        "key": "collected",
        "label": "已收集（設備）",
        "tone": "ok",
        "why": "SAN switch 等設備的資料已收集（線上收集或離線匯入），這類設備不做納管",
        "next": "定期再收一次保持最新即可",
        "action": "",
    },
    {
        "key": "complete",
        "label": "資料齊全",
        "tone": "ok",
        "why": "事實、服務、帳號都收得到",
        "next": "無需處理",
        "action": "",
    },
    {
        # 2026-09-16：人工標記的非納管設備（客製化系統／Oracle／廠商維護）。
        # 講好不納管的東西一直掛在「要處理」，會把真正要處理的淹掉。
        "key": "exempt",
        "label": "非納管設備（已豁免）",
        "tone": "muted",
        "why": "人工標記為不納管，並填了原因",
        "next": "不需處理；情況改變（例如系統改版、廠商同意）可在資產頁取消豁免",
        "action": "",
    },
    {
        # 2026-09-15：停用／報廢／閒置的資產不再算失聯（下線本來就掃不到）。不算還要處理。
        "key": "retired",
        "label": "已退役（停用／報廢／閒置）",
        "tone": "muted",
        "why": "資產狀態已標為停用、報廢或閒置",
        "next": "不需處理；記得 CIA 清冊也要改（見資產查詢頁「CIA 待異動」）",
        "action": "",
    },
    {
        # [B-07] 2026-09-18：標停用／報廢／閒置，卻還掃得到或收得到。以前直接歸「已退役・不需處理」，
        # 但「清冊寫報廢、實際還在跑」本身就是稽核發現（該關沒關＝資安風險，或清冊標錯）。
        "key": "retired_alive",
        "label": "退役但仍在線",
        "tone": "bad",
        "why": "資產狀態標停用／報廢／閒置，這次卻還掃得到或收得到",
        "next": "確認是該關沒關（關機／下線），還是清冊標錯（改回使用中）",
        "action": "",
    },
    {
        # [B-04] 2026-09-18：同一台的多筆登記，有的標退役、有的仍在使用。
        # 以前合併時「關卡編號小的勝出」，而已退役排在失聯前面 → 221 上 31 台的待辦被藏成「已退役」。
        # 也不能反過來「留要處理的那筆」：也可能退役那筆才對、使用中那筆是 CIA 沒清的舊登記
        # （221 查證：退役那筆序號較舊 20 台、較新 11 台；31 台都沒有 vm_uuid；掃描未涵蓋）。
        # 系統沒有證據替人挑邊 → 獨立一關，算待辦，要做的是「確認 CIA 哪一筆才對」。
        "key": "conflict",
        "label": "登記矛盾（退役／使用中）",
        "tone": "warn",
        "why": "同一台有的登記標停用／報廢／閒置，有的仍是使用中——系統沒有證據判斷哪一筆對",
        "next": "到 CIA 確認：已下線就把使用中那筆改退役；還在用就把退役那筆改回使用中（重複登記清單有各筆序號）",
        "action": "",
    },
    {
        # 2026-09-16：「沒掃過」以前被算成「失聯」，害使用者去查一台其實好好的機器。
        "key": "not_covered",
        "label": "未涵蓋（沒掃過）",
        "tone": "warn",
        "why": "最近一次掃描沒有涵蓋這台所在的網段",
        "next": "這不是失聯——我們沒有證據說它在不在。去「納入管理」把那個網段加進掃描範圍",
        "action": "adopt",
    },
    {
        # [B-09] 2026-09-18：沒有 IP（或 IP 欄填的不是 IP）以前一律被算成失聯——221 失聯 316 台全是這種
        "key": "no_ip",
        "label": "沒有 IP（無法掃描）",
        "tone": "warn",
        "why": "登記了但沒有可掃描的 IP，掃描無從判斷它在不在",
        "next": "到 CIA 補 IP；是範本／KVM／非主機就標非納管",
        "action": "collect",
    },
    {
        "key": "lost",
        "label": "失聯",
        "tone": "bad",
        "why": "登記在案，但這次掃描沒看到它",
        "next": "確認是否關機、換 IP、已下線，或防火牆擋住整段",
        "action": "check",
    },
]

STAGE_INDEX = {s["key"]: i for i, s in enumerate(STAGES)}
# 「還需要我處理的」＝除了資料齊全以外的每一關。畫面最重要的那個數字就是它。
TODO_STAGES = [s["key"] for s in STAGES
               if s["key"] not in ("complete", "collected", "retired", "exempt")]


def _has_facts(row) -> bool:
    """收到主機事實了沒。判準：作業系統或硬體序號其中一個有真值。

    為什麼不要求全部欄位都有：序號/機型多半要目標主機 root 才讀得到，唯讀收集帳號
    常常拿不到（這是已知且刻意的取捨）。要求全有會讓幾乎所有機器永遠卡在這一關，
    那個數字就不再代表「收集有沒有在運作」。
    """
    for k in ("os", "hw_serial"):
        v = row[k] if k in row.keys() else None
        if v and str(v).strip() and str(v).strip().upper() != "N/A":
            return True
    return False


def _counted_serials(conn, table: str) -> set:
    """某張收集結果表裡出現過的資產序號。表還不存在時回空集合——
    舊 DB 或功能沒開的環境不該讓整頁 500。"""
    try:
        return {r[0] for r in conn.execute(
            f"SELECT DISTINCT asset_serial FROM {table} "
            "WHERE asset_serial IS NOT NULL AND asset_serial != ''")}
    except Exception:  # noqa: BLE001
        return set()


def summarize(conn) -> dict:
    """每台機器的關卡＋各關計數＋對帳。

    回傳的 items 一列一台，畫面直接拿來排序／篩選／匯出。
    """
    import manage_state

    base = manage_state.summarize(conn)

    # 資產側的補充事實：一次撈完，不要在迴圈裡逐台查（4789 台逐台查會很慢）
    hw = {}
    for r in conn.execute(
            "SELECT asset_serial, hostname, ip, os, device_model, asset_name, hw_serial, environment, physical_location, "
            "collect_ok, collect_checked_at, collect_error FROM hardware"):
        hw[r["asset_serial"]] = r
    with_services = _counted_serials(conn, "host_service")
    with_accounts = _counted_serials(conn, "host_account")

    # 納管過的 IP：以「最後一筆成功的動作是什麼」為準——
    # 納管成功之後又撤銷成功的，就**不算**納管過（trigger='revoke'）。
    # 只看有沒有成功納管紀錄的話，取消納管過的機器會永遠掛著假狀態。
    onboarded_ips: set[str] = set()
    for r in conn.execute(
            "SELECT target_ip, trigger FROM onboard_audit WHERE ok = 1 "
            "ORDER BY id"):          # 依序覆蓋 → 最後一筆說了算
        if r["trigger"] == "revoke":
            onboarded_ips.discard(r["target_ip"])
        else:
            onboarded_ips.add(r["target_ip"])

    # 人工豁免的非納管設備，一次撈完（動作列要用）
    import onboard_exempt

    exempt_serials = onboard_exempt.active_serials(conn)

    # 最新一次掃描的開放埠（依 ip），一次撈完——未登記的機器沒有 os 欄，只能靠埠推
    scan_ports = {}
    latest = conn.execute("SELECT MAX(scan_time) AS t FROM scan_history").fetchone()
    if latest and latest["t"]:
        for r in conn.execute(
                "SELECT ip, open_ports FROM scan_history WHERE scan_time = ? AND scan_ok = 1",
                (latest["t"],)):
            scan_ports[r["ip"]] = r["open_ports"]

    items = []
    counts = {s["key"]: 0 for s in STAGES}
    os_counts = {k: 0 for k in OS_ORDER}
    _ov_map = os_override.active_map(conn)

    for it in base["items"]:
        state = it.get("state")
        serial = it.get("asset_serial")
        row = hw.get(serial)

        if state == manage_state.UNREGISTERED:
            key = "unregistered"
        elif state == manage_state.RETIRED:
            key = "retired"
        elif state == manage_state.RETIRED_ALIVE:
            key = "retired_alive"
        elif state == manage_state.EXEMPT:
            key = "exempt"
        elif state == manage_state.NOT_COVERED:
            key = "not_covered"
        elif state == manage_state.NO_IP:
            key = "no_ip"
        elif state == manage_state.LOST:
            key = "lost"
        elif state == manage_state.COLLECTED:
            key = "collected"
        elif state == manage_state.NOT_ONBOARDED:
            # 有納管紀錄＝我們在那台建過帳號，別再叫人去納管一次
            key = ("onboarded_stale" if it.get("ip") in onboarded_ips
                   else "not_onboarded")
        elif row is None or not _has_facts(row):
            key = "no_facts"
        elif serial not in with_services:
            key = "no_services"
        elif serial not in with_accounts:
            key = "no_accounts"
        else:
            key = "complete"

        counts[key] += 1
        # 每列都帶「能不能納管／有沒有被豁免／收得到沒」——這三個是動作列的判準。
        # 2026-09-16 使用者：「統一下的機制」。以前漏斗只給「看資產／去查失聯」兩顆，
        # 資產查詢頁又是另一套，同一台機器在兩頁能做的事不一樣。判準集中在這裡算一次，
        # 前端 RowActions 照著渲染，四頁不會再各長各的。
        _hw = dict(row) if row is not None else {"ip": it.get("ip"), "hostname": it.get("hostname")}
        blk = _onboard_block_for(_hw)
        os_type = _os_type(row["os"] if row is not None else None,
                           scan_ports.get(it.get("ip")),
                           row["device_model"] if row is not None else None,
                           (row["hostname"], row["asset_name"]) if row is not None else ())
        # 人工指定（os_override）優先；重匯 CIA 不會蓋掉。自動判的結果仍附上，畫面看得出改過
        _auto = os_type
        os_type, _ov = os_override.resolve(
            _ov_map, row["hostname"] if row is not None else it.get("hostname"),
            it.get("ip"), serial, _auto)
        os_counts[os_type] += 1
        stage = STAGES[STAGE_INDEX[key]]
        items.append({
            "ip": it.get("ip"),
            "hostname": it.get("hostname"),
            "asset_serial": serial,
            "environment": (row["environment"] if row is not None else None),
            # 環境三分類（2026-09-16 使用者要求）：原值照顯示，分類只給篩選用
            "env_group": env_group.classify(row["environment"] if row is not None else None),
            # 機房（2026-09-16 使用者：「我還要加機房」）——失聯要先看是哪個機房才知道找誰
            "physical_location": (row["physical_location"] if row is not None else None),
            "os": (row["os"] if row is not None else None),
            "os_type": os_type,
            "os_type_auto": _auto,
            "os_type_override": _ov,
            "stage": key,
            "stage_label": stage["label"],
            "stage_index": STAGE_INDEX[key],
            "tone": stage["tone"],
            "next_action": stage["next"],
            "action": stage["action"],
            "last_check": it.get("collect_checked_at"),
            "error": it.get("collect_error"),
            "collect_ok": (row["collect_ok"] if row is not None else None),
            "onboard_block": blk,
            "onboard_exempt": serial in exempt_serials,
        })

    # 照主機算，不是照筆數算（2026-09-16 使用者：「這是不是同一台? 怎會出現多次。程式會自動合併嗎」）。
    # 同一台被 CIA／dynassets／RVTools 各登記一筆是常態，漏斗照筆數列會同一台出現三次、
    # 母體也灌水。判準沿用資產查詢頁的重複定義（主機名＋IP 都相同才算），不另立一套。
    # ⚠️ 不自動合併資料：只在顯示與統計上收成一台，並標「重複登記 N 筆」讓人點進去自己決定。
    items, dup_merged = _collapse_duplicates(items)
    counts = {k: 0 for k in counts}
    os_counts = {k: 0 for k in os_counts}
    for it in items:
        counts[it["stage"]] += 1
        os_counts[it["os_type"]] = os_counts.get(it["os_type"], 0) + 1

    total = len(items)
    todo = sum(counts[k] for k in TODO_STAGES)
    return {
        "stages": STAGES,
        # 「還需要處理」有哪幾關由這裡說了算。前端以前寫死 stage !== 'complete'，
        # 結果已收集／已退役／非納管全都漏到「只看還需要處理的」清單裡
        # （2026-09-16 使用者：「已退役 還顯示出來合理嗎?」）。同一份判準只能有一個來源。
        "todo_stages": TODO_STAGES,
        "env_groups": [{"key": k, "label": env_group.LABEL[k]} for k in env_group.CHOICES],
        "os_choices": os_override.choices(),
        "counts": counts,
        "total": total,
        "todo": todo,
        "complete": counts["complete"],
        # 對帳：各關互斥且窮盡，加總必須等於母體。對不起來就是有 bug，
        # 畫面要看得到 ✗ 而不是安靜地顯示一組錯的數字。
        "reconcile": {
            "sum_of_stages": sum(counts.values()),
            "total": total,
            "ok": sum(counts.values()) == total,
        },
        # 被收起來的重複筆數（畫面說明用：8086 筆 → 7xxx 台）
        "duplicates_merged": dup_merged,
        "scan_time": base.get("scan_time"),
        # 掃描時間旁邊要說明「掃了什麼」（2026-09-17 使用者：
        # 「這個要寫最新掃描狀態……他掃描是那些 port 上面要說明」）。
        # 一個時間戳不講方法，人就不知道「掃不到」代表什麼——是機器不在，
        # 還是我們只探了這四個埠而它剛好都沒開。
        "scan_method": {
            "ports": list(net_scan.PROBE_PORTS),
            "icmp": True,
            "text": ("每個位址先試 TCP "
                     + "／".join(str(p) for p in net_scan.PROBE_PORTS)
                     + "，全部逾時再補送一個 ICMP；任一有回應就算活著"),
        },
        "os_counts": os_counts,
        "items": items,
    }


import env_group  # noqa: E402
import os_override  # noqa: E402
import net_scan  # noqa: E402  （放這裡避免與既有 import 區塊的順序檢查衝突）


def _collapse_duplicates(items: list[dict]) -> tuple[list[dict], int]:
    """同一台收成一列。回 (收完的清單, 被收起來的筆數)。

    ⚠️ 去重鍵一律用**正典 `system_stats.machine_key`**（主機名不分大小寫＋IP；缺任一或
    IP 是 0.0.0.0 佔位就退回序號各算一台）。2026-09-17 verify_numbers 抓到：pipeline 原本
    自己寫 `(host, ip)`，對 0.0.0.0 佔位當成同一台去併，跟 classify_assets／machine_key
    差 1 台（母體 4368 vs 4367）——同一個「幾台」在不同頁不一樣。全站去重只能有一把尺。

    留哪一筆：**留最需要處理的那一關**（stage_index 最小）。留成「資料齊全」會把該做的事藏起來，
    那比重複顯示更糟。其餘序號掛在 dup_serials 上，畫面可以點進去處理重複登記。
    """
    import system_stats
    out: list[dict] = []
    seen: dict[str, dict] = {}
    members: dict[str, list[dict]] = {}
    merged = 0

    def _rank(x):
        # [B-04] 要處理的一律優先（以前只比關卡編號，已退役排在失聯前面，待辦就被藏起來）
        return (0 if x["stage"] in TODO_STAGES else 1, x["stage_index"])

    for it in items:
        key = system_stats.machine_key(it.get("hostname"), it.get("ip"), it.get("asset_serial"))
        members.setdefault(key, []).append(
            {"asset_serial": it.get("asset_serial"), "stage": it["stage"], "stage_label": it.get("stage_label")})
        keep = seen.get(key)
        if keep is None:
            it["dup_count"] = 1
            it["dup_serials"] = [it["asset_serial"]] if it.get("asset_serial") else []
            seen[key] = it
            out.append(it)
            continue
        merged += 1
        if it.get("asset_serial"):
            keep["dup_serials"].append(it["asset_serial"])
        keep["dup_count"] = len(keep["dup_serials"]) or keep["dup_count"] + 1
        # 留最需要處理的那一筆
        if _rank(it) < _rank(keep):
            for k in ("stage", "stage_label", "stage_index", "tone", "next_action", "action",
                      "asset_serial", "environment", "env_group", "physical_location", "os",
                      "os_type", "os_type_auto", "os_type_override",
                      "collect_ok", "onboard_block", "onboard_exempt",
                      "last_check", "error"):
                keep[k] = it.get(k)
    # [B-04] 同一台有退役也有使用中 → 登記矛盾（不替人挑邊），附上各筆狀態讓人判斷
    cst = STAGES[STAGE_INDEX["conflict"]]
    for key, keep in seen.items():
        ms_ = members.get(key, [])
        kinds = {m["stage"] in ("retired", "retired_alive") for m in ms_}   # [B-07] 也是標退役
        if len(ms_) > 1 and kinds == {True, False}:
            keep.update({"stage": "conflict", "stage_label": cst["label"], "stage_index": STAGE_INDEX["conflict"],
                         "tone": cst["tone"], "next_action": cst["next"], "action": cst["action"]})
            keep["conflict_detail"] = ms_
    return out, merged


def _onboard_block_for(hw: dict):
    """能不能納管。跟資產查詢頁、資產詳細頁同一支判準（onboard_eligibility），不各猜各的。"""
    try:
        import onboard_eligibility

        blk = onboard_eligibility.not_onboardable(hw.get("os"), hw.get("hostname"), hw.get("ip"))
        return {"kind": blk[0], "label": blk[1], "reason": blk[2]} if blk else None
    except Exception:  # noqa: BLE001
        return None


# ---- OS 分類（[B-02] 2026-09-18：只留一份判斷，以 onboard_eligibility.classify_os 為準）----
# 以前這裡是黑名單：「不是 AIX／Windows 就當 Linux」。221 實測統計的 Linux 2255 筆＝
# 真 Linux 1128 ＋ 設備 880 ＋ OpenShift 節點 171 ＋ ESXi 76——網路設備、儲存、Cisco IOS、
# iDRAC、IBM i 全被算成 Linux，Linux 被高估一倍。批次納管用的 classify_os 是白名單
# （認得才算），兩個方向都驗過（真 Linux 漏認 0、非 Linux 全擋），所以統一用它。
OS_LINUX = "Linux"
OS_WINDOWS = "Windows"
OS_AIX = "AIX"
OS_ESXI = "VMware"   # ESXi 主機＋vCenter／vROps 等 VMware 自家設備（使用者 9/10 基礎設施分組也叫 VMware）
OS_OPENSHIFT = "OpenShift 節點"
OS_STORAGE = "Storage/SAN"
OS_NETWORK = "網路設備"
OS_BMC = "管理韌體(BMC)"
OS_IBMI = "IBM i"
OS_APPLIANCE = "設備"
OS_UNSET = "未填"
#: 沒有 OS 字串、只能靠開放埠猜的——**標明是推測**，不跟 CIA 登記的混成同一類
OS_GUESS_WINDOWS = "推測 Windows"
OS_GUESS_LINUX = "推測 Linux 類"
#: 交叉表／漏斗的顯示順序（登記的在前、推測的在後、未填最後）
OS_ORDER = (OS_LINUX, OS_WINDOWS, OS_AIX, OS_IBMI, OS_ESXI, OS_OPENSHIFT, OS_STORAGE, OS_NETWORK, OS_BMC, OS_APPLIANCE,
            OS_GUESS_LINUX, OS_GUESS_WINDOWS, OS_UNSET)
_CLASS_LABEL = {
    "linux": OS_LINUX, "windows": OS_WINDOWS, "aix": OS_AIX, "esxi": OS_ESXI,
    "immutable": OS_OPENSHIFT, "appliance": OS_APPLIANCE,
}


#: 首頁平台統計（manage_state._PLATFORM_RULES）的標籤 → 漏斗的設備細類。
#: 只收「設備」類；那套規則回作業系統類（RHEL、Windows…）時不採用。
_PLATFORM_DEVICE = {"網路設備": OS_NETWORK, "管理韌體(BMC)": OS_BMC, "IBM i": OS_IBMI,
                    "儲存設備": OS_STORAGE}


#: 只憑名稱（主機名／資產名稱／用途）時用的**嚴格**判斷：只收明確的廠牌／產品名。
#: 不用 platform_of 那套——它有 ios／f5／ilo／fg- 這種短字，主機名裡太容易誤中（silo 含 ilo）。
_NAME_VMWARE = re.compile(r"虛擬化平台|vcenter|vrops|vcsa|\bnsx\b|vmware|esxi", re.I)
_NAME_NETWORK = re.compile(
    r"aruba|airwave|cisco|catalyst|fortinet|fortigate|forcepoint|juniper|palo\s*alto|"
    r"\baten\b|\bkvm\b|riverbed|big-?ip|\brouter\b", re.I)


def _name_kind(*names: str | None) -> str | None:
    """OS 與設備機型都認不出來時，最後才看名稱。2026-09-18 使用者：vCenter、VROPS「屬於 VM」
    （→ VMware 類）、Aruba_Airwave「屬於網路」——這幾台 OS 是 N/A、設備機型只寫 (VM)，
    線索只在主機名／資產名稱。

    ⚠️ **不看「用途」欄**：它描述的是跑什麼軟體／服務，最容易誤中。221 實例：
    「GCP-Storage Transfer Service」被歸成 Storage/SAN（其實是雲端服務）、「EMC SRM」是
    裝在伺服器上的儲存管理**軟體**，不是儲存設備。
    「虛擬化平台」是業務系統 N-207 的名稱，使用者 2026-09-10 點名歸 VMware 分組。"""
    from system_stats import STORAGE_RE
    for n in names:
        if not n:
            continue
        if STORAGE_RE.search(n):
            return OS_STORAGE
        if _NAME_VMWARE.search(n):
            return OS_ESXI
        if _NAME_NETWORK.search(n):
            return OS_NETWORK
    return None


def _device_kind(os_raw: str | None, device_model: str | None) -> str | None:
    from manage_state import platform_of
    return _PLATFORM_DEVICE.get(platform_of(os_raw, device_model))


def _os_type(os_raw: str | None, open_ports: str | None, device_model: str | None = None,
             names: tuple = ()) -> str:
    """把每台歸類——每種 OS 的納管方式不同（Linux 走 SSH 批次、Windows 走 WinRM、
    AIX 一台台，ESXi／OpenShift 節點／設備不納管），漏斗要分得出來。

    有 OS 字串 → 完全照 `onboard_eligibility.classify_os`（白名單，認不出來＝設備）。
    沒有 OS 字串 → 才靠掃到的埠**推測**，結果帶「推測」字樣。

    Storage/SAN（2026-09-18 使用者：「這些還是分到 storage/san」）：
    - OS 認不出來（設備）且 OS 字串是儲存／SAN 字樣 → Storage/SAN
    - **OS 空白**但「設備機型」寫明是儲存／SAN（HP SAN Switch、IBM Storage、EMC、Synology…）
      → Storage/SAN。這是 CIA 登記的資料，不是推測，所以不標「推測」；也不會被重匯蓋掉
      （讀的就是清冊本身）
    - 判斷式跟分佈統計的 Storage 分群共用 `system_stats.STORAGE_RE`，只有一條
    - 伺服器機型（HPE DL380）、`(VM)` 不會命中，不會被誤歸

    網路設備／管理韌體(BMC)／IBM i（2026-09-18 使用者：「是不是還要一個網路設備，switch f5 等」）：
    - 不另寫規則：沿用首頁平台統計那套 `manage_state._PLATFORM_RULES`（使用者 2026-08-13
      逐條補過 aruba／fortinet／aten／voicegateway／f5／big-ip／idrac／as400…）
    - 只在「認不出來的設備」或「OS 空白」時才細分；那套規則回的若是作業系統類（RHEL 之類），
      不採用——OS 類別一律以 classify_os 為準（B-02）
    - ⚠️ **Storage 一定先判**：那套規則的網路設備有 `switch` 且排在儲存前面，
      先比的話 SAN Switch 會被歸成網路設備（使用者 2026-09-10 特別交代 SAN switch 屬於 Storage）

    ⚠️ 誠實限制：**未登記的 AIX 從網路上分不出來**（SSH banner 跟 Linux 一樣、也開 22），
    只會落在「推測 Linux 類」。要它正確歸 AIX，得先登記時把 OS 填 AIX，或納管進來讓 uname 講話。
    """
    from onboard_eligibility import classify_os

    from system_stats import STORAGE_RE

    cls, _why = classify_os(os_raw)
    # 認不出來的設備：OS 或設備機型任一是儲存字樣就算（221：「FOS v9＋EMC SAN Switch」、
    # 「Avamar 客製OS＋EMC Storage」只看 OS 會漏）。真的 Linux／Windows 仍以 OS 為準。
    if cls == "appliance" and (STORAGE_RE.search(os_raw or "") or STORAGE_RE.search(device_model or "")):
        return OS_STORAGE
    if cls == "appliance":
        return _device_kind(os_raw, device_model) or OS_APPLIANCE
    if cls != "unknown":
        return _CLASS_LABEL[cls]
    if device_model and STORAGE_RE.search(device_model):
        return OS_STORAGE
    if device_model:
        kind = _device_kind(None, device_model)
        if kind:
            return kind                     # 設備機型寫明的是登記資料，不是推測
    kind = _name_kind(*names)               # 最後才看名稱（主機名／資產名稱／用途）
    if kind:
        return kind
    ports = {p for p in str(open_ports or "").replace(" ", "").split(",") if p.isdigit()}
    if "3389" in ports or "5985" in ports or ("445" in ports and "22" not in ports):
        return OS_GUESS_WINDOWS
    if "22" in ports:
        return OS_GUESS_LINUX
    return OS_UNSET


# ---- 診斷外掛 ----
try:
    import diagnostics

    @diagnostics.register("pipeline")
    def _diag(conn) -> dict:
        try:
            s = summarize(conn)
            return {"counts": s["counts"], "total": s["total"],
                    "reconcile_ok": s["reconcile"]["ok"]}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:200]}
except ImportError:
    pass
