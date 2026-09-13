"""MICS 切片2：影響範圍查詢引擎（`/blast` 頁的後端，殺手級功能）。

三種問法共用同一顆引擎，只差版面（見 api.py 的 mode 參數）：
  mode=probe    陌生 IP 的即時研判——同時回「誰依賴它」與「它依賴誰」，後者可能是肇因
  mode=incident 事故當下的爆炸半徑——只看「誰依賴它」
  mode=planned  計畫性停機事前評估——跟 incident 同一份 dependents，版面改標題（切片3）

## 解析（resolve）：陌生輸入怎麼變成一個節點

依序比對 `ci_node.node_id` → `hardware.asset_serial` → `hardware.ip` 精確 → `hostname`
精確 → `ci_node.label`；多筆命中一律回候選清單不自動選（自動選錯一台，爆炸半徑就全錯）。

全部落空**不回 404**——改用 `segments.find_segment_for_ip()` 的結果：「未登記於資產庫；
屬 XX機房/XX網段」，標「未驗證」。這步直接把陌生 IP 變成有用資訊，是使用者原話
「打 10.1.19.100 PORT 22 FAIL，能檢查出什麼」這種問法的一半答案。

## 遍歷（impact）：BFS 不用遞迴 CTE

圖規模數百至數千節點，一次 SELECT 全部有效邊（`gone_at IS NULL`）進記憶體做鄰接表，
Python BFS 比較好處理「confidence 遍歷取路徑上最弱的一環」這種邏輯——SQL 遞迴 CTE
硬塞得進去但可讀性差、還要在 SQL 裡維護一個字串優先序，不划算。

confidence 強弱序：證據 > 推論 > 未驗證。整條路徑取最弱的那一環，不是取起點的等級。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

CONFIDENCE_RANK = {"證據": 3, "推論": 2, "未驗證": 1}
CONFIDENCE_LABEL = {3: "證據", 2: "推論", 1: "未驗證"}


def _node_for_asset_serial(conn: sqlite3.Connection, asset_serial: str) -> sqlite3.Row | None:
    """一個資產序號可能對到不只一個 ci_node——最常見是 ESXi 主機：`hw:X`（資產登記本身）
    跟 `esxi:Y`（vCenter 推出來的角色節點，asset_serial 有回填）兩個節點共用同一個
    asset_serial。真正掛了 VM 依賴邊的是角色節點（`esxi:`），不是那筆資產登記本身；
    query 沒有 ORDER BY 時 SQLite 給哪筆完全看插入順序，2026-08-19 使用者實測踩到：
    查 ESXi 主機 IP 查到的是 `hw:` 節點（只有業務系統那條推論邊，1個依賴），不是
    `esxi:` 節點（真正掛了幾十台 VM 的那個）——同一台主機兩個身分，選錯一個等於
    看不到重點。角色節點（非 'host' 類型）優先。
    """
    return conn.execute(
        "SELECT * FROM ci_node WHERE asset_serial = ? AND gone_at IS NULL "
        "ORDER BY CASE WHEN node_type = 'host' THEN 1 ELSE 0 END, node_id LIMIT 1",
        (asset_serial,),
    ).fetchone()


def resolve(conn: sqlite3.Connection, q: str, port: int | None = None) -> dict[str, Any]:
    """把使用者輸入（IP/主機名/資產序號/節點ID/名稱）解析成節點，或退而求其次給網段資訊。

    回傳其中一種：
      {"status": "resolved", "node_id": ..., "label": ...}
      {"status": "ambiguous", "candidates": [...]}         多筆命中，不自動選
      {"status": "unregistered", "segment": {...} | None, "confidence": "未驗證"}
    """
    import segments

    q = (q or "").strip()
    if not q:
        return {"status": "unregistered", "segment": None, "confidence": "未驗證"}

    # 1. 直接是 node_id
    row = conn.execute("SELECT * FROM ci_node WHERE node_id = ? AND gone_at IS NULL", (q,)).fetchone()
    if row:
        return {"status": "resolved", "node_id": row["node_id"], "label": row["label"]}

    # 2. asset_serial 精確
    hw = conn.execute(
        "SELECT asset_serial FROM hardware WHERE asset_serial = ?", (q,)
    ).fetchone()
    if hw:
        node = _node_for_asset_serial(conn, hw["asset_serial"])
        if node:
            return {"status": "resolved", "node_id": node["node_id"], "label": node["label"]}

    # 3. IP 精確（可能對到多台，例如同 IP 不同機器判不準的殘留資料——回候選不猜）
    ip_rows = conn.execute(
        "SELECT asset_serial, hostname FROM hardware WHERE ip = ?", (q,)
    ).fetchall()
    if len(ip_rows) == 1:
        node = _node_for_asset_serial(conn, ip_rows[0]["asset_serial"])
        if node:
            return {"status": "resolved", "node_id": node["node_id"], "label": node["label"]}
    elif len(ip_rows) > 1:
        return {
            "status": "ambiguous",
            "candidates": [
                {"asset_serial": r["asset_serial"], "label": r["hostname"] or r["asset_serial"]}
                for r in ip_rows
            ],
        }

    # 4. hostname 精確
    host_rows = conn.execute(
        "SELECT asset_serial, hostname FROM hardware WHERE hostname = ?", (q,)
    ).fetchall()
    if len(host_rows) == 1:
        node = _node_for_asset_serial(conn, host_rows[0]["asset_serial"])
        if node:
            return {"status": "resolved", "node_id": node["node_id"], "label": node["label"]}
    elif len(host_rows) > 1:
        return {
            "status": "ambiguous",
            "candidates": [
                {"asset_serial": r["asset_serial"], "label": r["hostname"] or r["asset_serial"]}
                for r in host_rows
            ],
        }

    # 5. ci_node.label 模糊比對（多筆一樣回候選）
    label_rows = conn.execute(
        "SELECT node_id, label FROM ci_node WHERE label LIKE ? AND gone_at IS NULL", (f"%{q}%",)
    ).fetchall()
    if len(label_rows) == 1:
        return {"status": "resolved", "node_id": label_rows[0]["node_id"], "label": label_rows[0]["label"]}
    if len(label_rows) > 1:
        return {
            "status": "ambiguous",
            "candidates": [{"node_id": r["node_id"], "label": r["label"]} for r in label_rows],
        }

    # 6. 全部落空：退回網段推導，不回 404
    seg = segments.find_segment_for_ip(conn, q)
    result: dict[str, Any] = {
        "status": "unregistered",
        "segment": (
            {"location": seg.get("location"), "environment": seg.get("environment"),
             "purpose": seg.get("purpose_desc"), "category": seg.get("category")}
            if seg else None
        ),
        "confidence": "未驗證",
    }
    if port is not None:
        result["service"] = _lookup_service(conn, q, port)
    return result


def _lookup_service(conn: sqlite3.Connection, ip: str, port: int) -> dict[str, Any]:
    """帶 port 時查 host_service。有採集過就給答案，沒採集過要明講「未驗證」，
    不能寫成「沒有服務」——那是兩件不同的事（真的沒開 vs 我們根本沒收過）。"""
    row = conn.execute(
        "SELECT * FROM host_service WHERE ip = ? AND port = ? AND gone_at IS NULL "
        "ORDER BY last_seen DESC LIMIT 1",
        (ip, port),
    ).fetchone()
    if row:
        return {
            "known": True,
            "service_guess": row["service_guess"],
            "process": row["process"],
            "last_seen": row["last_seen"],
        }
    return {"known": False, "note": f"此 IP 未曾採集過 port {port} 服務資料，未驗證"}


def _load_edges(conn: sqlite3.Connection) -> tuple[dict[str, list], dict[str, list]]:
    """一次撈全部有效邊，回 (reverse_adj, forward_adj)。
    reverse_adj[dst] = [(src, edge_type, confidence)]  ← 誰依賴 dst（爆炸半徑用）
    forward_adj[src]  = [(dst, edge_type, confidence)]  ← dst 依賴誰（找肇因用）
    """
    reverse_adj: dict[str, list] = {}
    forward_adj: dict[str, list] = {}
    for r in conn.execute(
        "SELECT src_node_id, dst_node_id, edge_type, confidence FROM ci_edge WHERE gone_at IS NULL"
    ):
        reverse_adj.setdefault(r["dst_node_id"], []).append(
            (r["src_node_id"], r["edge_type"], r["confidence"])
        )
        forward_adj.setdefault(r["src_node_id"], []).append(
            (r["dst_node_id"], r["edge_type"], r["confidence"])
        )
    return reverse_adj, forward_adj


def _bfs(adj: dict[str, list], start: str, depth: int, only_evidence: bool) -> list[dict]:
    """從 start 沿 adj 走，回 [{node_id, depth, edge_type, confidence, path}]，
    confidence 是路徑上最弱的一環（起點不計）。visited set 防環。"""
    out: list[dict] = []
    visited = {start}
    frontier = [(start, 0, [], 4)]  # 4 = 比「證據」還強的哨兵值，第一步就會被蓋掉
    while frontier:
        node, d, path, weakest = frontier.pop(0)
        if d >= depth:
            continue
        for neighbor, edge_type, confidence in adj.get(node, []):
            if neighbor in visited:
                continue
            rank = CONFIDENCE_RANK.get(confidence, 1)
            new_weakest = min(weakest, rank)
            if only_evidence and new_weakest < CONFIDENCE_RANK["證據"]:
                continue
            new_path = path + [{"node_id": neighbor, "edge_type": edge_type, "confidence": confidence}]
            visited.add(neighbor)
            out.append({
                "node_id": neighbor, "depth": d + 1, "edge_type": edge_type,
                "confidence": CONFIDENCE_LABEL[new_weakest], "path": new_path,
            })
            frontier.append((neighbor, d + 1, new_path, new_weakest))
    return out


def _node_info(conn: sqlite3.Connection, node_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM ci_node WHERE node_id = ?", (node_id,)).fetchone()


def label_for_node(conn: sqlite3.Connection, node_id: str) -> str:
    """node_id → 人看得懂的名字，找不到就回 node_id 本身。CSV 匯出用——盤點清單
    不能給人看內部節點鍵值（如 hw:HW-00011202），要給主機名這種看得懂的字。"""
    n = _node_info(conn, node_id)
    return n["label"] if n else node_id


def _summarize(conn: sqlite3.Connection, hits: list[dict]) -> dict[str, Any]:
    """把命中節點彙總成固定結構：counts/by_biz_system/by_location/by_environment/
    notify/unknown_owner/evidence_breakdown——每類都要有 unknown 桶，不能讓「查不到」
    的東西悄悄從統計裡消失（那樣看起來像「沒問題」，其實是「不知道」）。
    """
    counts: dict[str, int] = {}
    # 存清單而不是只存計數：2026-08-20 使用者「如果查到 unknown 又是哪幾台？要能點進去看」。
    # 「有 2 台位置不明」這句話本身沒有可執行性——要知道是哪 2 台才有辦法去查、去補。
    # 而且 unknown 這一桶正是最需要點開的：其他桶至少還知道去哪找。
    by_location: dict[str, list[dict]] = {}
    by_environment: dict[str, list[dict]] = {}
    biz_systems: dict[str, dict] = {}
    notify: dict[str, dict] = {}
    unknown_owner: list[dict] = []
    evidence_breakdown = {"證據": 0, "推論": 0, "未驗證": 0}

    for hit in hits:
        node = _node_info(conn, hit["node_id"])
        if not node:
            continue
        counts[node["node_type"]] = counts.get(node["node_type"], 0) + 1
        evidence_breakdown[hit["confidence"]] = evidence_breakdown.get(hit["confidence"], 0) + 1

        attrs = json.loads(node["attrs"]) if node["attrs"] else {}
        # 這一筆在清單上長什麼樣。node_type 一起帶：環境別的「未填」桶裡有一部分
        # 根本不是資產（ESXi/cluster/網段這類節點沒有環境別可言），跟「是資產但欄位
        # 沒填」是兩回事，展開後要分得出來，不能都叫 unknown 了事。
        entry = {
            "node_id": node["node_id"],
            "node_type": node["node_type"],
            "label": node["label"],
            "asset_serial": node["asset_serial"],
            "hostname": None,
            "ip": None,
        }
        env = attrs.get("environment") or "未填"
        by_environment.setdefault(env, []).append(entry)

        if not node["asset_serial"]:
            continue
        hw = conn.execute(
            "SELECT * FROM hardware WHERE asset_serial = ?", (node["asset_serial"],)
        ).fetchone()
        if not hw:
            continue

        # 補上只有 hardware 才有的欄位。entry 是同一個物件，環境別那邊也會跟著有值。
        entry["hostname"] = hw["hostname"]
        entry["ip"] = hw["ip"]

        loc = hw["physical_location"] or "未填"
        by_location.setdefault(loc, []).append(entry)

        # 先把這台的負責人算出來——底下 notify（全域去重的聯絡清單）與
        # by_biz_system 的每台明細都要用，算兩次會不一致。
        #
        # 2026-08-20 使用者原話：「超音樹有三台，但我點進去不知道是哪三台，
        # 聯絡人／部門又是誰」。事故當下要的是**當場看到**，不是先記下數字、
        # 再去別的頁面查一遍——所以負責人要跟著每一台一起回，不能只有一份
        # 全域 notify 清單（那份答不出「哪個人對應哪一台」）。
        owners: list[dict] = []
        # 除了姓名電話還要有部門，不然收到清單的人不知道要找哪個單位對口
        # （2026-08-19 使用者：拿到影響清單第一件事就是匯出聯絡資料發給每個人去查）。
        for p in conn.execute(
            "SELECT person_name, phone, proxy1, proxy1_phone, belong_division, belong_department "
            "FROM personnel WHERE asset_serial = ?",
            (node["asset_serial"],),
        ):
            if p["person_name"]:
                dept = " ".join(x for x in (p["belong_division"], p["belong_department"]) if x)
                owners.append({
                    "name": p["person_name"], "phone": p["phone"], "department": dept or None,
                    "proxy": p["proxy1"], "proxy_phone": p["proxy1_phone"], "role": "登記負責人",
                    "email": None, "email_note": EMAIL_NOTE,
                })
        # SP保管者(custodian)不列進聯絡清單——2026-08-20 使用者：「我們自己就是SP，
        # 不用聯絡自己，全部都是聯絡AP管理者就可以了」。custodian 欄位資料本身還在
        # hardware 表，只是事故通知/檢查清單不拿它當聯絡人（那是自己人，打了也白打）。
        for field, role in (("user_name", "AP User"),):
            val = hw[field]
            if val:
                # phone / email 一律帶欄位、值為 None，並附一句話說明「為什麼是空的」。
                # 2026-08-20 使用者要求 email 先留空、分機以後補——但**留白不等於沒有**：
                # 空白會被讀成「這個人沒有分機」，實際是「我們還沒有這份資料」。
                # 這兩句話在事故當下差很多：前者不用再找，後者要趕快去問。
                owners.append({
                    "name": val, "role": role, "department": hw["usage_unit"],
                    "phone": None, "phone_note": PHONE_NOTE,
                    "email": None, "email_note": EMAIL_NOTE,
                })

        for o in owners:
            notify.setdefault(o["name"], o)

        if hw["api_id"]:
            biz = biz_systems.setdefault(
                hw["api_id"],
                # name：人看得懂的系統名（例如「STO 交易管理系統」）——api_id 代碼本身
                # （如「N-218」）留著給連結/篩選用，畫面顯示要用 name，2026-08-19 使用者
                # 反映「只顯示代碼看不出是什麼系統」
                {"api_id": hw["api_id"], "name": hw["asset_name"] or hw["api_id"], "availability": None, "assets": []},
            )
            if hw["availability"] is not None:
                biz["availability"] = max(biz["availability"] or 0, hw["availability"])
            biz["assets"].append({
                "asset_serial": node["asset_serial"],
                "label": node["label"],
                "hostname": hw["hostname"],
                "ip": hw["ip"],
                "location": hw["physical_location"],
                "environment": hw["environment"],
                # 空陣列跟「沒查」是兩件事：這裡一定給陣列，前端據此顯示
                # 「無登記負責人」而不是留白——留白會被當成還沒載完。
                "owners": owners,
                # 這台跟查詢主機「怎麼」有關係——隔幾層、什麼關係、可信度多強。
                # 這幾個值 hit 裡本來就有（BFS算出來的），2026-08-20 使用者問「這三台
                # 跟這台主機有關係嗎」才發現漏接：資料一直都在，只是沒串進這個列表，
                # 使用者得自己跑去另一張「受影響節點清單」表用主機名對一次才找得到。
                "depth": hit["depth"],
                "edge_type": hit["edge_type"],
                "confidence": hit["confidence"],
            })

        if not owners:
            unknown_owner.append({
                "asset_serial": node["asset_serial"], "label": node["label"],
            })

    return {
        "counts": counts,
        "by_biz_system": [
            {**v, "severity": "重大" if (v["availability"] or 0) >= 3 else "一般"}
            for v in biz_systems.values()
        ],
        # count 一律由 items 算出來，不另外維護一個計數器——兩者若各自累加，
        # 遲早會出現「寫 3 台、點開只有 2 台」這種讓人對系統失去信任的畫面。
        "by_location": [{"location": k, "count": len(v), "items": v}
                        for k, v in by_location.items()],
        "by_environment": [{"environment": k, "count": len(v), "items": v}
                           for k, v in by_environment.items()],
        "notify": list(notify.values()),
        "unknown_owner": unknown_owner,
        "evidence_breakdown": evidence_breakdown,
    }


# 為什麼欄位是空的——這兩句話會直接顯示在畫面上。
#
# 2026-08-20 使用者拍板「沒有 AD 的話 email 先空白」「分機以後補」。但空白欄位有
# 兩種完全不同的意思：「這個人沒有分機」與「我們還沒拿到這份資料」。事故當下前者
# 代表不用再找、後者代表要趕快去問，讀錯會浪費時間。所以一律附上原因，不留純空白。
EMAIL_NOTE = "未接 AD，無法取得"
PHONE_NOTE = "尚未匯入人員通訊錄（personnel 表目前 0 筆）"


def _rvtools_detail(last_import, oldest, newest, stale_days) -> str:
    """RVTools 這一列要講的話。分開寫是因為「舊」有好幾種不同的狀況，
    每一種要講的話不一樣，塞在三元運算子裡會變成沒人看得懂的一行。"""
    if not last_import:
        return "尚未匯入任何 RVTools，這條關聯目前是空的"
    if not oldest:
        # 有資料但檔名認不出匯出時間。不可以因此就說它是新的。
        return (f"最後匯入 {last_import}，但**認不出這批是哪天從 vCenter 匯出的**"
                f"（檔名沒有匯出時間）。資料可能是舊的，未驗證。")
    span = f"{oldest[:10]}" if oldest[:10] == (newest or "")[:10] else f"{oldest[:10]} ~ {newest[:10]}"
    if stale_days is not None and stale_days > 7:
        return (f"vCenter 匯出於 **{span}**（距今 {stale_days} 天），匯入於 {last_import}。"
                f"⚠️ VM 會被搬移，這批快照反映的是匯出當天的樣子，"
                f"不是現在——請重新匯出一份再判讀。")
    return f"vCenter 匯出於 {span}，匯入於 {last_import}"


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """這次查詢**沒有涵蓋到什麼**。固定隨每次 impact 回傳，前端不可折疊地顯示。

    ## 為什麼這是必要的，而不是錦上添花

    2026-08-20 使用者問：「我是 AIX，怎知道有沒有影響？」查證後發現：8 台 AIX
    （IBM S922／S1024，全實體機）在圖譜裡只有機櫃與業務系統兩條邊，**沒有任何
    儲存關聯**——因為儲存關聯是靠 RVTools 建的，而 RVTools 只看得到 vCenter 裡的
    虛擬機，實體主機完全不在它的視野內。

    後果是：查一台儲存設備，畫面會列出一串 VM 看起來很完整，**AIX 不會出現**。
    使用者會讀成「沒影響」，實際是「根本沒查」。這兩件事在畫面上長得一模一樣，
    是這類工具最危險的錯誤——它讓人放心地做出錯誤決定。

    所以照 CLAUDE.md 的鐵律：「找到 0 台」必須能分辨「查了但真的沒有」與「根本沒查」。
    數字要能點、缺口要講明，而且**不能折疊**——可折疊等於預設隱藏，等於沒講。

    回傳的每一項都是實際算出來的，不是寫死的文案：寫死的警語會在資料補齊後
    繼續嚇人，久了就被當成背景雜訊忽略。
    """
    def one(q: str, *args) -> int:
        try:
            return conn.execute(q, args).fetchone()[0]
        except Exception:  # noqa: BLE001 - 舊 DB 可能沒有某些表，缺就算 0
            return 0

    rvtools_last = None
    try:
        rvtools_last = conn.execute(
            "SELECT MAX(collected_at) FROM source_record WHERE source = 'vcenter'"
        ).fetchone()[0]
    except Exception:  # noqa: BLE001
        pass

    # 「哪天匯進系統」不等於「哪天從 vCenter 匯出」。2026-08-20 匯的五個檔全是 07-30
    # 匯出的，差三週——只寫匯入時間會讓人以為資料是新的。取**最舊**的那份當代表：
    # 圖譜是所有匯入拼起來的，可信度由最舊的那塊決定，取平均或最新都會高估。
    exported_oldest = exported_newest = None
    try:
        row = conn.execute(
            "SELECT MIN(exported_at), MAX(exported_at) FROM import_log "
            "WHERE source = 'rvtools' AND exported_at IS NOT NULL"
        ).fetchone()
        exported_oldest, exported_newest = row[0], row[1]
    except Exception:  # noqa: BLE001 - 舊 DB 還沒有 exported_at 欄位
        pass

    stale_days = None
    if exported_oldest:
        try:
            d = datetime.strptime(str(exported_oldest)[:10], "%Y-%m-%d")
            stale_days = (datetime.now() - d).days
        except ValueError:
            pass

    # 有儲存關聯的主機（stores_on 的來源端）
    with_storage = one(
        "SELECT COUNT(DISTINCT n.asset_serial) FROM ci_edge e "
        "JOIN ci_node n ON n.node_id = e.src_node_id "
        "WHERE e.edge_type = 'stores_on' AND e.gone_at IS NULL AND n.asset_serial IS NOT NULL"
    )
    # 實體主機（is_vm 在資料裡混了 0/1 與字串，比照 manage_state 的判定）
    physical_total = one(
        "SELECT COUNT(*) FROM hardware "
        "WHERE COALESCE(NULLIF(TRIM(CAST(is_vm AS TEXT)), ''), '0') "
        "NOT IN ('1', 'VM', 'vm', 'TRUE', 'true', '是')"
    )
    physical_no_storage = one(
        "SELECT COUNT(*) FROM hardware h "
        "WHERE COALESCE(NULLIF(TRIM(CAST(h.is_vm AS TEXT)), ''), '0') "
        "NOT IN ('1', 'VM', 'vm', 'TRUE', 'true', '是') "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM ci_edge e JOIN ci_node n ON n.node_id = e.src_node_id "
        "  WHERE e.edge_type = 'stores_on' AND e.gone_at IS NULL "
        "    AND n.asset_serial = h.asset_serial)"
    )

    dims = [
        {
            "name": "虛擬機 → ESXi／Datastore",
            # 資料太舊就不能算「ok」。VM 每天都在搬，三週前的快照拿來算爆炸半徑，
            # 會漏掉搬過來的、也會多算搬走的——那是錯的答案，不是舊的答案。
            "status": ("none" if not rvtools_last
                       else "partial" if (stale_days is None or stale_days > 7)
                       else "ok"),
            "detail": _rvtools_detail(rvtools_last, exported_oldest, exported_newest, stale_days),
        },
        {
            "name": "實體主機 → 儲存設備",
            # 只要還有實體主機沒有儲存關聯，就是缺口，不因為有幾台有了就算過關
            "status": "partial" if with_storage else "none",
            "detail": (f"{physical_no_storage} / {physical_total} 台實體主機沒有儲存關聯資料"
                       f"（含 AIX）。查儲存設備時它們**不會出現在結果裡**，"
                       f"這不代表不受影響。"),
        },
        {
            "name": "Datastore → 實體儲存陣列",
            "status": "none",
            "detail": "尚未建立對應。目前只知道 VM 存在哪個 datastore，"
                      "不知道那個 datastore 屬於哪一台儲存設備。",
        },
        {
            "name": "SAN／Switch／VLAN",
            "status": "out_of_scope",
            "detail": "不在本系統範圍（無資料來源）。",
        },
        {
            "name": "電力（PDU／UPS／PSU）",
            "status": "out_of_scope",
            "detail": "不在本系統範圍（已於需求階段排除）。",
        },
    ]
    return {
        "dimensions": dims,
        "complete": sum(1 for d in dims if d["status"] == "ok"),
        "total": len(dims),
    }


def impact(
    conn: sqlite3.Connection, node_id: str, depth: int = 6, mode: str = "incident",
    only_evidence: bool = False,
) -> dict[str, Any]:
    """對 node_id 算影響範圍。mode=probe 額外回 dependencies（它依賴誰，可能是肇因）。"""
    node = _node_info(conn, node_id)
    if node is None:
        raise ValueError(f"查無此節點：{node_id}")

    reverse_adj, forward_adj = _load_edges(conn)
    dependents = _bfs(reverse_adj, node_id, depth, only_evidence)
    result: dict[str, Any] = {
        "node_id": node_id, "label": node["label"], "node_type": node["node_type"],
        "dependents": dependents,
        "summary": _summarize(conn, dependents),
        "coverage": coverage(conn),
    }
    if mode == "probe":
        dependencies = _bfs(forward_adj, node_id, depth, only_evidence)
        result["dependencies"] = dependencies
    return result


def graph_elements(
    conn: sqlite3.Connection, node_id: str, depth: int = 3, direction: str = "dependents",
) -> dict[str, Any]:
    """回 topology.vue 的 elements 形狀（cytoscape nodes/edges），前端零轉換即可餵給
    既有的 cy/dagre 設定（/blast 頁複製 topology.vue:45-131，這支要對得上它要的格式）。

    direction="dependents"（預設，事故／計畫性停機用）只畫「誰依賴它」那半邊。
    direction="both"（陌生IP研判 probe 用）兩半都畫，不然「它依賴誰」那張表只有
    node_id 沒有 label——因為那些節點沒被收進圖的節點清單裡。
    """
    reverse_adj, forward_adj = _load_edges(conn)
    hits = _bfs(reverse_adj, node_id, depth, only_evidence=False)
    node_ids = {node_id} | {h["node_id"] for h in hits}
    if direction == "both":
        node_ids |= {h["node_id"] for h in _bfs(forward_adj, node_id, depth, only_evidence=False)}

    nodes = []
    for nid in node_ids:
        n = _node_info(conn, nid)
        if n:
            nodes.append({"data": {"id": n["node_id"], "label": n["label"], "type": n["node_type"]}})

    edges = []
    for r in conn.execute(
        "SELECT src_node_id, dst_node_id, edge_type, confidence FROM ci_edge WHERE gone_at IS NULL"
    ):
        if r["src_node_id"] in node_ids and r["dst_node_id"] in node_ids:
            edges.append({"data": {
                "source": r["src_node_id"], "target": r["dst_node_id"],
                "type": r["edge_type"], "confidence": r["confidence"],
            }})
    return {"elements": {"nodes": nodes, "edges": edges}}


def list_business_systems(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """瀏覽清單，不用先知道要查什麼名字才能開始——2026-08-19 使用者原話：
    全庫5148節點畫成一張圖是看不出結構的毛球，選瀏覽清單當首頁入口，
    不做「全部關聯圖」。每一列是一個業務系統，直接給 node_id 供前端組
    `/blast?q=` 連結；depends_on 邊的數量當「已知會被這台影響的東西」提示，
    但不在這裡展開（開了才算，避免每列都跑一次 impact() BFS 拖慢清單）。
    """
    rows = conn.execute(
        "SELECT h.api_id, MIN(h.asset_name) AS name, MIN(h.usage_unit) AS usage_unit, "
        "MIN(h.custodian) AS custodian, MAX(h.availability) AS availability, "
        "COUNT(*) AS asset_count "
        "FROM hardware h WHERE h.api_id IS NOT NULL AND h.api_id != '' "
        "GROUP BY h.api_id ORDER BY h.api_id"
    ).fetchall()
    return [
        {
            "node_id": f"bizsys:{r['api_id']}",
            "api_id": r["api_id"],
            "name": r["name"] or r["api_id"],
            "usage_unit": r["usage_unit"],
            "custodian": r["custodian"],
            "severity": "重大" if (r["availability"] or 0) >= 3 else "一般",
            "asset_count": r["asset_count"],
        }
        for r in rows
    ]


# ===== 切片3：計畫性停機評估存證快照 =====
# mode=planned 沿用跟 incident 一樣的 impact() 結果，唯一差別是使用者按下「存快照」時
# 把當下整包結果原封存證——圖會變，三週後要拿得出「當初評估說不影響」的證據，
# 存快照當下的完整 impact() 輸出，不是只存幾個欄位事後重算（重算會跟事後的圖對不起來）。

def save_snapshot(
    conn: sqlite3.Connection, node_id: str, mode: str, reason: str | None, asked_by: str,
    depth: int = 6,
) -> dict[str, Any]:
    result = impact(conn, node_id, depth=depth, mode="incident")
    asked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "INSERT INTO change_impact_snapshot (node_id, mode, reason, asked_by, asked_at, result_json) "
        "VALUES (?,?,?,?,?,?)",
        (node_id, mode, reason, asked_by, asked_at, json.dumps(result, ensure_ascii=False)),
    )
    conn.commit()
    return {
        "id": cur.lastrowid, "node_id": node_id, "mode": mode, "reason": reason,
        "asked_by": asked_by, "asked_at": asked_at, "result": result,
    }


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM change_impact_snapshot WHERE id = ?", (snapshot_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["result"] = json.loads(d.pop("result_json"))
    return d


def list_snapshots(conn: sqlite3.Connection, node_id: str | None = None) -> list[dict[str, Any]]:
    """列表不含 result_json（可能很大），只給挑選用的摘要欄位；細節走 get_snapshot。"""
    sql = "SELECT id, node_id, mode, reason, asked_by, asked_at FROM change_impact_snapshot"
    params: tuple = ()
    if node_id:
        sql += " WHERE node_id = ?"
        params = (node_id,)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(sql, params)]


# ===== 檢查清單（2026-08-20 拍板方案A）=====
# 使用者原話：「今天是很緊急的狀況，你要怎麼很快速地把全部的資訊列出來，給每一位
# 同事開始幫忙做檢查、幫忙做聯絡？甚至於聯絡完的時候，旁邊要寫備註」。
# 攤平成「一列一個（主機,聯絡人）配對」，掛在快照底下——快照凍結一份事實，
# 清單就是照那份事實派工，不會因為圖後來變了而讓清單跟著漂移。

CHECKLIST_STATUS_OPTIONS = ("未聯絡", "聯絡中", "已確認正常", "已確認異常", "聯絡不到")


def create_checklist(conn: sqlite3.Connection, snapshot_id: int) -> list[dict[str, Any]]:
    """從快照的 by_biz_system 攤平出檢查清單。冪等：已經建過就直接回既有清單，
    不重複產生——多人同時點「建立檢查清單」不會炸出兩份。"""
    existing = list_checklist(conn, snapshot_id)
    if existing:
        return existing

    snap = get_snapshot(conn, snapshot_id)
    if not snap:
        raise ValueError(f"查無此快照：{snapshot_id}")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for biz in snap["result"]["summary"]["by_biz_system"]:
        for asset in biz["assets"]:
            # 沒有登記負責人也要出現一列——「查不到聯絡人」本身就是要處理的事，
            # 不能因為沒有人可以填聯絡人欄，這台就從清單裡悄悄消失。
            owners = asset["owners"] or [{"name": None, "role": None, "department": None, "phone": None}]
            for o in owners:
                conn.execute(
                    "INSERT INTO checklist_item (snapshot_id,asset_serial,hostname,ip,environment,"
                    "physical_location,biz_system,severity,sort_depth,"
                    "contact_name,contact_role,contact_department,contact_phone,status,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'未聯絡',?)",
                    (snapshot_id, asset["asset_serial"], asset["hostname"], asset["ip"],
                     asset.get("environment"), asset.get("location"), biz["name"], biz["severity"],
                     asset.get("depth"),
                     o.get("name"), o.get("role"), o.get("department"), o.get("phone"), now),
                )
    conn.commit()
    return list_checklist(conn, snapshot_id)


def list_checklist(conn: sqlite3.Connection, snapshot_id: int) -> list[dict[str, Any]]:
    # 排序不給人工調（2026-08-20 拍板方案A）：事故當下沒空排優先級，直接照
    # 「重大先於一般、隔越近越前面」排好給你照順序打。severity 用 CASE 排在
    # '重大'之前——字串排序碰運氣不可靠（'一般' < '重大' 剛好會反過來）。
    return [dict(r) for r in conn.execute(
        "SELECT * FROM checklist_item WHERE snapshot_id = ? "
        "ORDER BY CASE WHEN severity = '重大' THEN 0 ELSE 1 END, "
        "sort_depth ASC, biz_system, hostname, id",
        (snapshot_id,),
    )]


def update_checklist_item(
    conn: sqlite3.Connection, item_id: int, status: str | None, note: str | None, updated_by: str,
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM checklist_item WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise ValueError(f"查無此檢查項目：{item_id}")
    if status is not None and status not in CHECKLIST_STATUS_OPTIONS:
        raise ValueError(f"不支援的狀態：{status}（只接受 {'/'.join(CHECKLIST_STATUS_OPTIONS)}）")

    sets = ["updated_by = ?", "updated_at = ?"]
    params: list[Any] = [updated_by, datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if note is not None:
        sets.append("note = ?")
        params.append(note)
    params.append(item_id)
    conn.execute(f"UPDATE checklist_item SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    return dict(conn.execute("SELECT * FROM checklist_item WHERE id = ?", (item_id,)).fetchone())
