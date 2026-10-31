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
「打 10.99.19.100 PORT 22 FAIL，能檢查出什麼」這種問法的一半答案。

## 遍歷（impact）：BFS 不用遞迴 CTE

圖規模數百至數千節點，一次 SELECT 全部有效邊（`gone_at IS NULL`）進記憶體做鄰接表，
Python BFS 比較好處理「confidence 遍歷取路徑上最弱的一環」這種邏輯——SQL 遞迴 CTE
硬塞得進去但可讀性差、還要在 SQL 裡維護一個字串優先序，不划算。

confidence 強弱序：證據 > 推論 > 未驗證。整條路徑取最弱的那一環，不是取起點的等級。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

CONFIDENCE_RANK = {"證據": 3, "推論": 2, "未驗證": 1}
CONFIDENCE_LABEL = {3: "證據", 2: "推論", 1: "未驗證"}
OBSERVED_EDGE_TYPE = "observed_conn"   # 同 observed_deps.EDGE_TYPE（test_observed_deps 釘住兩者相同）


def _node_for_asset_serial(conn: sqlite3.Connection, asset_serial: str) -> sqlite3.Row | None:
    """一個資產序號可能對到不只一個 ci_node——最常見是 ESXi 主機：`hw:X`（資產登記本身）
    跟 `esxi:Y`（vCenter 推出來的角色節點，asset_serial 有回填）兩個節點共用同一個
    asset_serial。真正掛了 VM 依賴邊的是角色節點（`esxi:`），不是那筆資產登記本身；
    query 沒有 ORDER BY 時 SQLite 給哪筆完全看插入順序，2026-08-19 使用者實測踩到：
    查 ESXi 主機 IP 查到的是 `hw:` 節點（只有業務系統那條推論邊，1個依賴），不是
    `esxi:` 節點（真正掛了幾十台 VM 的那個）——同一台主機兩個身分，選錯一個等於
    看不到重點。角色節點（非 'host' 類型）優先。
    """
    node = conn.execute(
        "SELECT * FROM ci_node WHERE asset_serial = ? AND gone_at IS NULL "
        "ORDER BY CASE WHEN node_type = 'host' THEN 1 ELSE 0 END, node_id LIMIT 1",
        (asset_serial,),
    ).fetchone()
    if node is None or node["node_type"] == "host":
        # 同一台 ESXi 常有兩筆登記（DYN-IP 與 HW-編號）：角色節點的 asset_serial 只回填其中一筆，
        # 另一筆就只剩 hw: 節點、看不到 VM（2026-10-29 夜間速查實測：查 DYN- 那筆影響範圍是空的）。
        # 改用主機名／IP 補找角色節點。
        role = esxi_role_node(conn, asset_serial)
        if role is not None:
            return role
    return node


def esxi_role_node(conn: sqlite3.Connection, asset_serial: str) -> sqlite3.Row | None:
    """這個資產序號對應的 ESXi 角色節點（`esxi:`）。先看角色節點回填的 asset_serial，
    再用登記的主機名（去網域、轉大寫，跟 ci_graph 建節點同一套正規化）與 IP（RVTools 的 Host 欄有時是 IP，
    節點標籤就是那串 IP）去比。找不到回 None。
    主機名／IP 的補找**只在這筆登記本身就是 ESXi（作業系統大類＝虛擬化平台）時才做**：
    一般主機的短名稱剛好跟某台 ESXi 同名（如 RHEL 的 esxi018-01.other），不可以被當成那台 ESXi。
    只比 node_type='esxi'。"""
    hw = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
    if hw is None:
        return None
    import onboard_eligibility as oe

    is_esxi = oe.is_vmware_platform(hw)
    names: set[str] = set()
    h = (hw["hostname"] or "").strip() if is_esxi else ""
    ips = {x.strip() for x in re.split(r"[,\s]+", hw["ip"] or "") if x.strip()} if is_esxi else set()
    if h:
        names.add(h.upper())
        if not _RE_IPV4.match(h):
            names.add(h.split(".")[0].upper())
    names |= {i.upper() for i in ips}
    names_l = sorted(names)
    ph = ",".join("?" * len(names_l))
    cond = "asset_serial = ?" + (f" OR node_id IN ({ph}) OR UPPER(label) IN ({ph})" if names_l else "")
    params = [asset_serial] + ([f"esxi:{n}" for n in names_l] + names_l if names_l else [])
    return conn.execute(
        f"SELECT * FROM ci_node WHERE node_type = 'esxi' AND gone_at IS NULL AND ({cond}) "
        "ORDER BY CASE WHEN asset_serial = ? THEN 0 ELSE 1 END, node_id LIMIT 1",
        (*params, asset_serial),
    ).fetchone()


def _start_nodes(conn: sqlite3.Connection, node_id: str) -> list[str]:
    """走訪的起點。hw: 節點若是 ESXi，連它的 esxi: 角色節點一起當起點（VM 的依賴邊掛在角色節點上，
    業務系統的登記邊掛在 hw: 上，兩邊都要看）。"""
    starts = [node_id]
    if node_id.startswith("hw:"):
        role = esxi_role_node(conn, node_id[3:])
        if role is not None and role["node_id"] != node_id:
            starts.append(role["node_id"])
    return starts


def _bfs_multi(adj: dict[str, list], starts: list[str], depth: int, only_evidence: bool,
               observed_max_depth: int | None, no_expand: set[str] | None) -> list[dict]:
    """多個起點各走一次再合併（同一節點只留先走到的那筆；起點自己不算受影響對象）。"""
    out: list[dict] = []
    seen = set(starts)
    for s in starts:
        for h in _bfs(adj, s, depth, only_evidence, observed_max_depth, no_expand):
            if h["node_id"] not in seen:
                seen.add(h["node_id"])
                out.append(h)
    return out


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


def _registered_deps_total(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM system_deps").fetchone()[0]


def registered_systems(conn: sqlite3.Connection) -> set[str]:
    """有人工登記上下游（system_deps 任一端）的系統。這些系統以人工為準，不用連線推論。"""
    out: set[str] = set()
    for r in conn.execute("SELECT source, target FROM system_deps"):
        out.add((r["source"] or "").strip())
        out.add((r["target"] or "").strip())
    out.discard("")
    return out


def observed(conn: sqlite3.Connection) -> dict:
    """連線推論的結果（observed_deps.infer，帶快取）。用不用由逐系統規則決定（registered_systems）。"""
    import observed_deps

    return observed_deps.infer(conn)


def observed_edge_applies(e: dict, registered: set[str]) -> bool:
    """這條連線推論邊要不要用：兩端都有人工登記才不用（4-1／4-2 共用這一個判斷）。"""
    return not (e["caller"] in registered and e["callee"] in registered)


def _add_observed_edges(reverse_adj: dict, forward_adj: dict, inferred: dict | None,
                        registered: set[str]) -> None:
    """把觀察到的「T 依賴 S」加進鄰接表（bizsys:T → bizsys:S，可信度＝推論）。
    主機掛了 → 它的系統 S（既有 bizsys→hw 邊）→ 呼叫 S 的系統 T（這裡加的邊）。

    規則（逐邊）：兩端**都**有人工登記上下游 → 以人工為準，這條連線推論不用；只要有一端沒有登記，
    這條就用，而且兩端看到的一樣（4-1 兩邊的系統地圖、4-2 兩個方向）。節點 id 走 ci_graph.bizsys_node_id。"""
    if not inferred:
        return
    import observed_deps

    nodes = inferred["nodes_of_api"]
    for e in inferred["edges"]:
        if not observed_edge_applies(e, registered):
            continue
        for src in nodes.get(e["caller"], []):
            for dst in nodes.get(e["callee"], []):
                reverse_adj.setdefault(dst, []).append((src, observed_deps.EDGE_TYPE, observed_deps.CONFIDENCE))
                forward_adj.setdefault(src, []).append((dst, observed_deps.EDGE_TYPE, observed_deps.CONFIDENCE))


def _hub_nodes(inferred: dict | None) -> set[str]:
    import observed_deps

    if not inferred:
        return set()
    return {n for a in observed_deps.hub_ids(inferred) for n in inferred["nodes_of_api"].get(a, [])}


def _bfs(adj: dict[str, list], start: str, depth: int, only_evidence: bool,
         observed_max_depth: int | None = None, no_expand: set[str] | None = None) -> list[dict]:
    """從 start 沿 adj 走，回 [{node_id, depth, edge_type, confidence, path}]，
    confidence 是路徑上最弱的一環（起點不計）。visited set 防環。

    連線推論的邊（observed_conn）另有兩條限制（沒有連線推論資料時兩者都不起作用）：
    · observed_max_depth：一條路徑最多走幾段連線推論邊
    · no_expand（樞紐系統的節點）：經連線推論走到它之後列出來，但不再從它往外走"""
    out: list[dict] = []
    visited = {start}
    frontier = [(start, 0, [], 4, 0)]  # 4 = 比「證據」還強的哨兵值，第一步就會被蓋掉
    while frontier:
        node, d, path, weakest, obs = frontier.pop(0)
        if d >= depth:
            continue
        for neighbor, edge_type, confidence in adj.get(node, []):
            if neighbor in visited:
                continue
            is_obs = edge_type == OBSERVED_EDGE_TYPE
            if is_obs and observed_max_depth is not None and obs >= observed_max_depth:
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
            if is_obs and no_expand and neighbor in no_expand:
                continue      # 樞紐：列出來（直接受影響），不經過它往外擴散
            frontier.append((neighbor, d + 1, new_path, new_weakest, obs + (1 if is_obs else 0)))
    return out


def _node_info(conn: sqlite3.Connection, node_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM ci_node WHERE node_id = ?", (node_id,)).fetchone()


def label_for_node(conn: sqlite3.Connection, node_id: str) -> str:
    """node_id → 人看得懂的名字，找不到就回 node_id 本身。CSV 匯出用——盤點清單
    不能給人看內部節點鍵值（如 hw:HW-00011202），要給主機名這種看得懂的字。"""
    n = _node_info(conn, node_id)
    return n["label"] if n else node_id


# ===== 名稱比對補負責人（2026-10-23 事故：某台 ESXi 當機）=====
# vCenter 那筆 VM 叫「APP163-97」、CIA 登記的是「APP163-097」，只差一個補零，
# 影響範圍就把它丟進「查不到負責人」。這裡**只在呈現層**補：VM／主機自己沒有系統也沒有負責人時，
# 用正規化主機名（不分大小寫、去網域、數字段去掉前導零）去 hardware 找登記那筆，借它的系統與負責人，
# 並標明「名稱比對」——這是推論，不是證據。machine_key／去重／合併／台數一律不動（危險區域 D3）。
# 對到 2 筆以上意見不同的登記（系統或負責人不同）就不挑，列成「名稱比對有多筆」。
NAME_MATCH_ZERO = "名稱比對（補零）"
NAME_MATCH_PLAIN = "名稱比對"
# 主機名對不到時，再看 IP：同一個 IP 的登記那筆（例：主機名拼錯 SRV／SVR）。只認 IP 完全相同，不做拼字模糊比對。
NAME_MATCH_IP = "IP 相同"
_RE_DIGITS = re.compile(r"\d+")
_RE_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def host_match_key(hostname: str | None) -> str | None:
    """APP163-97、app163-097.corp.local → 'app163-97'。IP、空白、N/A 回 None（不拿來比）。"""
    h = (hostname or "").strip()
    if not h or h.upper() in ("N/A", "NA", "-", "NONE", "NULL") or _RE_IPV4.match(h):
        return None
    h = h.split(".")[0].lower()
    return _RE_DIGITS.sub(lambda m: str(int(m.group(0))), h) or None


class _NameMatcher:
    """hardware 一次讀進來，用正規化主機名分組；只收有系統或有 AP User／登記負責人的那幾筆。"""

    def __init__(self, conn: sqlite3.Connection):
        import manage_state

        self.conn = conn
        pers = {r["asset_serial"] for r in conn.execute(
            "SELECT DISTINCT asset_serial FROM personnel WHERE COALESCE(person_name,'') != ''")}
        self.by_key: dict[str, list] = {}
        self.by_ip: dict[str, list] = {}
        for r in conn.execute("SELECT * FROM hardware"):
            if (r["asset_status"] or "").strip() in manage_state.RETIRED_STATUS:
                continue
            if not ((r["api_id"] or "").strip() or (r["user_name"] or "").strip() or r["asset_serial"] in pers):
                continue
            k = host_match_key(r["hostname"])
            if k:
                self.by_key.setdefault(k, []).append(r)
            ip = (r["ip"] or "").strip()
            if ip:
                self.by_ip.setdefault(ip, []).append(r)

    def find(self, hostname: str | None, own_serial: str, ip: str | None = None) -> dict[str, Any] | None:
        """回 {"row": 借用的那筆, "method": 標記} 或 {"ambiguous": [...]}；沒對到回 None。
        先比主機名；主機名一筆都對不到才比 IP（IP 完全相同）。"""
        k = host_match_key(hostname)
        cands = [r for r in self.by_key.get(k, []) if r["asset_serial"] != own_serial] if k else []
        by_ip = False
        if not cands and (ip or "").strip():
            cands = [r for r in self.by_ip.get(ip.strip(), []) if r["asset_serial"] != own_serial]
            by_ip = True
        if not cands:
            return None
        # 同一台在 CIA 登記多筆、系統與 AP User 都一樣 → 視為同一個答案；意見不同才算有多筆
        opinions = {((r["api_id"] or "").strip(), (r["user_name"] or "").strip()) for r in cands}
        if len(opinions) > 1:
            return {"ambiguous": [{"asset_serial": r["asset_serial"], "hostname": r["hostname"],
                                   "api_id": (r["api_id"] or "").strip() or None,
                                   "user_name": r["user_name"]} for r in cands]}
        row = sorted(cands, key=lambda r: r["asset_serial"])[0]
        if by_ip:
            return {"row": row, "method": NAME_MATCH_IP}
        raw_same = (row["hostname"] or "").split(".")[0].lower() == (hostname or "").strip().split(".")[0].lower()
        return {"row": row, "method": NAME_MATCH_PLAIN if raw_same else NAME_MATCH_ZERO}


def _owners_of_row(conn: sqlite3.Connection, hw, asset_serial: str) -> list[dict]:
    owners: list[dict] = []
    for p in conn.execute(
        "SELECT person_name, phone, proxy1, proxy1_phone, belong_division, belong_department "
        "FROM personnel WHERE asset_serial = ?",
        (asset_serial,),
    ):
        if p["person_name"]:
            dept = " ".join(x for x in (p["belong_division"], p["belong_department"]) if x)
            owners.append({
                "name": p["person_name"], "phone": p["phone"], "department": dept or None,
                "proxy": p["proxy1"], "proxy_phone": p["proxy1_phone"], "role": "登記負責人",
                "email": None, "email_note": EMAIL_NOTE,
            })
    if hw["user_name"]:
        owners.append({
            "name": hw["user_name"], "role": "AP User", "department": hw["usage_unit"],
            "phone": None, "phone_note": PHONE_NOTE,
            "email": None, "email_note": EMAIL_NOTE,
        })
    return owners


def vm_export_age(exported_at: str | None, as_of: str | None = None) -> dict[str, Any]:
    """VM 位置資料（RVTools／vCenter 匯出）幾天前的。as_of 給快照時間就算「快照當時幾天」。
    超過 STALE_VM_DAYS 天畫面標紅：VM 會被 vMotion 搬走，舊快照算出來的受影響 VM 可能已經不對。"""
    out: dict[str, Any] = {"exported_at": None, "days": None, "days_at": None, "stale": None,
                           "stale_days": STALE_VM_DAYS}
    if not exported_at:
        return out
    try:
        d = datetime.strptime(str(exported_at)[:10], "%Y-%m-%d")
    except ValueError:
        return out
    out["exported_at"] = d.strftime("%Y-%m-%d")
    out["days"] = (datetime.now() - d).days
    if as_of:
        try:
            out["days_at"] = (datetime.strptime(str(as_of)[:10], "%Y-%m-%d") - d).days
        except ValueError:
            pass
    out["stale"] = out["days"] > STALE_VM_DAYS
    return out


STALE_VM_DAYS = 2



def _summarize(conn: sqlite3.Connection, hits: list[dict],
               start_systems: set[str] | None = None) -> dict[str, Any]:
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
    name_matched: list[dict] = []
    name_ambiguous: list[dict] = []
    matcher: _NameMatcher | None = None
    evidence_breakdown = {"證據": 0, "推論": 0, "未驗證": 0}
    # 每個可信度是哪幾個節點（Q82）：畫面點「證據等級 N」要列出那 N 個——跟計數同一個迴圈、同一個判斷
    evidence_ids: dict[str, list[str]] = {"證據": [], "推論": [], "未驗證": []}

    for hit in hits:
        node = _node_info(conn, hit["node_id"])
        if not node:
            continue
        counts[node["node_type"]] = counts.get(node["node_type"], 0) + 1
        evidence_breakdown[hit["confidence"]] = evidence_breakdown.get(hit["confidence"], 0) + 1
        evidence_ids.setdefault(hit["confidence"], []).append(hit["node_id"])

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
            # 經連線推論邊（observed_conn）走到的業務系統本身：主機沒壞（assets 空），但系統受影響。
            # 只有這種才加；其他沒有序號的節點維持原本行為（略過）。起點自己的系統不另列。
            if hit["edge_type"] == OBSERVED_EDGE_TYPE and node["node_id"].startswith("bizsys:"):
                sid = node["node_id"][len("bizsys:"):].strip()
                if sid not in biz_systems and sid not in (start_systems or set()):
                    hw = conn.execute(
                        "SELECT MIN(asset_name) AS n, MAX(availability) AS a FROM hardware "
                        "WHERE TRIM(api_id) = ?", (sid,)).fetchone()
                    biz_systems[sid] = {
                        "api_id": sid, "name": (hw["n"] if hw else None) or node["label"] or sid,
                        "availability": hw["a"] if hw else None, "assets": [],
                        "via": hit["edge_type"], "depth": hit["depth"], "confidence": hit["confidence"],
                    }
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

        # 自己沒系統也沒負責人 → 用名稱比對借登記那筆的（只補畫面，標明是推論）
        api_id = hw["api_id"]
        sys_name = hw["asset_name"]
        name_match = None
        if not (api_id or "").strip() and not owners:
            matcher = matcher or _NameMatcher(conn)
            m = matcher.find(hw["hostname"], node["asset_serial"], hw["ip"])
            if m and "row" in m:
                src = m["row"]
                api_id = (src["api_id"] or "").strip() or None
                sys_name = src["asset_name"]
                owners = [{**o, "role": f"{o['role']}・{m['method']}", "inferred": m["method"]}
                          for o in _owners_of_row(conn, src, src["asset_serial"])]
                name_match = {"method": m["method"], "asset_serial": src["asset_serial"],
                              "hostname": src["hostname"], "api_id": api_id}
                name_matched.append({"asset_serial": node["asset_serial"], "label": node["label"],
                                     "hostname": hw["hostname"], **{f"matched_{k}": v for k, v in name_match.items()}})
            elif m:
                name_ambiguous.append({"asset_serial": node["asset_serial"], "label": node["label"],
                                       "hostname": hw["hostname"], "candidates": m["ambiguous"]})

        for o in owners:
            notify.setdefault(o["name"], o)

        if api_id:
            biz = biz_systems.setdefault(
                api_id,
                # name：人看得懂的系統名（例如「STO 交易管理系統」）——api_id 代碼本身
                # （如「N-218」）留著給連結/篩選用，畫面顯示要用 name，2026-08-19 使用者
                # 反映「只顯示代碼看不出是什麼系統」
                {"api_id": api_id, "name": sys_name or api_id, "availability": None, "assets": []},
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
                # 不是 None＝系統／負責人是用主機名比對借來的（推論），畫面要標出來
                "name_match": name_match,
            })

        if not owners:
            amb = next((x["candidates"] for x in name_ambiguous if x["asset_serial"] == node["asset_serial"]), None)
            unknown_owner.append({
                "asset_serial": node["asset_serial"], "label": node["label"],
                **({"ambiguous_candidates": amb} if amb else {}),
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
        "name_matched": name_matched,
        "name_match_ambiguous": name_ambiguous,
        "evidence_breakdown": evidence_breakdown,
        "evidence_ids": evidence_ids,
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
    # 實體主機＝非虛擬機。虛擬機判定走正典 manage_state.is_vm_sql（含 device_model「(VM)」標記），
    # 不再自己寫（以前只看 is_vm 欄，漏了 device_model 標記，跟儀表板差 1,188 台）。
    import manage_state as _ms
    # 排退役：退役實體機已下線、本來就不需要儲存關聯，算進來只會灌大分母、又跟首頁「實體機」
    # （composition 排退役）對不上（2026-09-19：全表 1477 vs 首頁 1445，差 32 台退役實體）。
    _ret = ",".join("?" for _ in _ms.RETIRED_STATUS)
    _rp = sorted(_ms.RETIRED_STATUS)
    physical_total = one(
        f"SELECT COUNT(*) FROM hardware WHERE NOT {_ms.is_vm_sql()} "
        f"AND COALESCE(asset_status,'') NOT IN ({_ret})", *_rp
    )
    physical_no_storage = one(
        f"SELECT COUNT(*) FROM hardware h "
        f"WHERE NOT {_ms.is_vm_sql('h.is_vm', 'h.device_model')} "
        f"AND COALESCE(h.asset_status,'') NOT IN ({_ret}) "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM ci_edge e JOIN ci_node n ON n.node_id = e.src_node_id "
        "  WHERE e.edge_type = 'stores_on' AND e.gone_at IS NULL "
        "    AND n.asset_serial = h.asset_serial)", *_rp
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
                       f"（含 AIX）。查儲存設備時它們不會出現在結果裡，"
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
        # 畫面頂端的「VM 位置資料：vCenter 匯出 YYYY-MM-DD（N 天前）」——跟上面同一個日期（最舊那份）
        "vm_export": vm_export_age(exported_oldest),
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
    inferred = observed(conn)
    registered = registered_systems(conn)
    _add_observed_edges(reverse_adj, forward_adj, inferred, registered)
    hubs = _hub_nodes(inferred)
    max_obs = inferred["settings"]["max_depth"]
    starts = _start_nodes(conn, node_id)
    dependents = _bfs_multi(reverse_adj, starts, depth, only_evidence, max_obs, hubs)
    summary = _summarize(conn, dependents, _systems_of_node(conn, node))
    result: dict[str, Any] = {
        "node_id": node_id, "label": node["label"], "node_type": node["node_type"],
        "dependents": dependents,
        "summary": summary,
        "coverage": coverage(conn),
        # 系統之間的上下游有沒有任何資料。沒有＝影響只算到直接掛在這台上的系統，
        # 畫面要明講「不完整」，不能讓「1 個系統」看起來像全部（使用者 2026-10-22 查 AIX 核心機踩到）
        # 人工登記的系統間依賴，或連線推論推出了任何邊，都算「有資料」
        "deps_available": system_deps_available(conn) or bool(inferred and inferred.get("edges")),
        # 受影響系統各自是「怎麼被走到的」：observed（經連線推論邊）／registered（經登記的關聯）
        "deps_source_by_system": {
            b["api_id"]: ("observed" if b.get("via") == OBSERVED_EDGE_TYPE else "registered")
            for b in summary["by_biz_system"]},
        "observed": _observed_meta(inferred, hubs_expanded=_hubs_expanded(node, dependents, hubs, inferred)),
    }
    if mode == "probe":
        dependencies = _bfs_multi(forward_adj, starts, depth, only_evidence, max_obs, hubs)
        result["dependencies"] = dependencies
    return result


def system_deps_available(conn: sqlite3.Connection) -> bool:
    """CI 圖裡有沒有任何「系統 → 系統」的依賴邊（system_deps 投影的 sys:，或 bizsys 之間）。"""
    row = conn.execute(
        "SELECT 1 FROM ci_edge WHERE gone_at IS NULL AND edge_type = 'depends_on' AND ("
        "(src_node_id LIKE 'sys:%' AND dst_node_id LIKE 'sys:%') OR "
        "(src_node_id LIKE 'bizsys:%' AND dst_node_id LIKE 'bizsys:%')) LIMIT 1").fetchone()
    return row is not None
def _systems_of_node(conn: sqlite3.Connection, node) -> set[str]:
    """起點自己的業務系統（起點是主機＝它登記的 APID；起點是業務系統節點＝它自己）。"""
    if node["node_id"].startswith("bizsys:"):
        return {node["node_id"][len("bizsys:"):].strip()}
    if not node["asset_serial"]:
        return set()
    r = conn.execute("SELECT api_id FROM hardware WHERE asset_serial = ?", (node["asset_serial"],)).fetchone()
    return ({(r["api_id"] or "").strip()} - {""}) if r else set()


def _hubs_expanded(node, dependents: list[dict], hub_nodes: set[str], inferred: dict | None) -> list[dict]:
    """哪些樞紐**有**往外擴散：起點本身，或經登記的關聯（不是連線推論邊）走到的。
    樞紐只有經連線推論走到時才停（pass-through 規則），畫面要講得出這兩種差在哪。"""
    if not hub_nodes:
        return []
    out: dict[str, str] = {}
    if node["node_id"] in hub_nodes:
        out[node["node_id"]] = "start"
    for h in dependents:
        if h["node_id"] in hub_nodes and h["edge_type"] != OBSERVED_EDGE_TYPE:
            out.setdefault(h["node_id"], "registered_edge")
    api_of = {n: a for a, ns in (inferred or {}).get("nodes_of_api", {}).items() for n in ns}
    return [{"api_id": api_of.get(n, n), "node_id": n, "reason": why} for n, why in sorted(out.items())]


def _observed_meta(inferred: dict | None, hubs_expanded: list | None = None) -> dict | None:
    """回應裡給畫面講「為什麼停在這／哪些沒算」：樞紐（含哪些因為是經登記關聯走到而照樣擴散）、
    模稜兩可、各種排除的筆數、設定。"""
    if not inferred:
        return None
    meta = {"hubs": inferred["hubs"], "hub_threshold": inferred["hub_threshold"],
            "ambiguous": inferred["ambiguous"], "stats": inferred["stats"],
            # 自動判定為管理連線（:22 扇出超過門檻的來源）：哪個 IP／系統、連進幾個系統、丟了幾列
            "auto_admin_ssh": inferred.get("auto_admin_ssh", []),
            "settings": inferred["settings"]}
    if hubs_expanded is not None:
        meta["hubs_expanded"] = hubs_expanded
    return meta


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
    inferred = observed(conn)
    _add_observed_edges(reverse_adj, forward_adj, inferred, registered_systems(conn))
    hubs, max_obs = _hub_nodes(inferred), inferred["settings"]["max_depth"]
    starts = _start_nodes(conn, node_id)
    hits = _bfs_multi(reverse_adj, starts, depth, False, max_obs, hubs)
    node_ids = set(starts) | {h["node_id"] for h in hits}
    if direction == "both":
        node_ids |= {h["node_id"] for h in _bfs_multi(forward_adj, starts, depth, False, max_obs, hubs)}

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
    seen_pairs: set[tuple[str, str]] = set()
    obs_pairs = [(src, dst, conf) for src, lst in forward_adj.items() for dst, t, conf in lst
                 if t == OBSERVED_EDGE_TYPE]
    obs_pairs += [(src, dst, conf) for dst, lst in reverse_adj.items() for src, t, conf in lst
                  if t == OBSERVED_EDGE_TYPE]
    for src, dst, conf in obs_pairs:
        if src in node_ids and dst in node_ids and (src, dst) not in seen_pairs:
            seen_pairs.add((src, dst))
            edges.append({"data": {"source": src, "target": dst, "type": OBSERVED_EDGE_TYPE,
                                   "confidence": conf}})
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
    # 系統分級（第一／二／三類）給系統地圖挑選框分層用；手動覆寫優先（同 business_system 口徑）
    cls = {}
    for b in conn.execute(
            "SELECT api_id, COALESCE(NULLIF(TRIM(sys_class_override),''), sys_class) AS c FROM business_system"):
        cls[b["api_id"]] = b["c"]
    return [
        {
            "node_id": f"bizsys:{r['api_id']}",
            "api_id": r["api_id"],
            "name": r["name"] or r["api_id"],
            "usage_unit": r["usage_unit"],
            "custodian": r["custodian"],
            "severity": "重大" if (r["availability"] or 0) >= 3 else "一般",
            "asset_count": r["asset_count"],
            "sys_class": cls.get((r["api_id"] or "").strip()),
            "class_group": class_group(cls.get((r["api_id"] or "").strip())),
        }
        for r in rows
    ]


_CLASS_RE = re.compile(r"第[一二三四五六七八九十0-9]+類")


def class_group(sys_class: str | None) -> str:
    """「S1-第一類系統軟體（核心）」→「第一類」；空白或認不出→「未分類」。"""
    m = _CLASS_RE.search(sys_class or "")
    return m.group(0) if m else "未分類"


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
    d["vm_export"] = _snapshot_vm_export(conn, d)
    return d


def _snapshot_vm_export(conn: sqlite3.Connection, snap: dict) -> dict[str, Any]:
    """快照當時的 VM 位置資料日期：快照裡有存就用存的；舊快照沒存，就用「快照時間以前匯入的」最舊匯出日。
    回傳 days_at＝快照當時幾天、days＝到今天幾天。"""
    cov = (snap.get("result") or {}).get("coverage") or {}
    exp = (cov.get("vm_export") or {}).get("exported_at")
    if not exp:
        row = conn.execute(
            "SELECT MIN(exported_at) FROM import_log WHERE source = 'rvtools' AND exported_at IS NOT NULL "
            "AND (imported_at IS NULL OR imported_at <= ?)", (snap.get("asked_at") or "9999",)).fetchone()
        exp = row[0] if row else None
    return vm_export_age(exp, snap.get("asked_at"))


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


def _system_contacts(conn: sqlite3.Connection, api_id: str) -> list[dict[str, Any]]:
    """一個業務系統（現役登記）的聯絡人：每台的登記負責人（personnel）＋AP User，依姓名去重。
    SP 保管者（custodian）不列——2026-08-20 使用者：「我們自己就是SP，不用聯絡自己」。"""
    import manage_state

    out: dict[str, dict] = {}
    for hw in conn.execute("SELECT asset_serial, user_name, usage_unit, asset_status FROM hardware "
                           "WHERE TRIM(api_id) = ? ORDER BY asset_serial", (api_id,)):
        if (hw["asset_status"] or "").strip() in manage_state.RETIRED_STATUS:
            continue
        for p in conn.execute(
            "SELECT person_name, phone, belong_division, belong_department FROM personnel "
            "WHERE asset_serial = ?", (hw["asset_serial"],),
        ):
            if p["person_name"] and p["person_name"] not in out:
                dept = " ".join(x for x in (p["belong_division"], p["belong_department"]) if x)
                out[p["person_name"]] = {"name": p["person_name"], "role": "登記負責人",
                                         "department": dept or None, "phone": p["phone"]}
        if hw["user_name"] and hw["user_name"] not in out:
            out[hw["user_name"]] = {"name": hw["user_name"], "role": "AP User",
                                    "department": hw["usage_unit"], "phone": None}
    return list(out.values())


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
        if not biz["assets"]:
            # 經連線推論受影響的系統：它自己的主機沒壞（assets 空），但一樣要通知它的人。
            # 聯絡人取這個系統登記主機的負責人／AP User（同上面每台的規則：SP 保管者不列）。
            owners = _system_contacts(conn, biz["api_id"]) or [
                {"name": None, "role": None, "department": None, "phone": None}]
            for o in owners:
                conn.execute(
                    "INSERT INTO checklist_item (snapshot_id,asset_serial,hostname,ip,environment,"
                    "physical_location,biz_system,severity,sort_depth,"
                    "contact_name,contact_role,contact_department,contact_phone,status,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'未聯絡',?)",
                    (snapshot_id, None, None, None, None, None, biz["name"], biz["severity"],
                     biz.get("depth"), o.get("name"), o.get("role"), o.get("department"),
                     o.get("phone"), now),
                )
            continue
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


# ===== 系統地圖（4-1 系統聯通圖／4-2 影響範圍查詢共用）=====
# 使用者選一個業務系統（APID），一眼看到：跑在哪幾台主機、那些 VM 掛在哪台 ESXi
# （有的話再到 Cluster／Datastore）、上下游系統。只走一層，不做 BFS——這張圖的用途是
# 「這個系統長什麼樣」，不是爆炸半徑（那是 impact() 的事）。
#
# 「幾台」走 system_stats.collapse_machines（全站唯一一把尺 machine_key），不自寫去重；
# 退役＝備查，不計入台數也不畫（只回 retired 筆數讓畫面講得出「另有 N 筆退役」）。
# 同一台機器的其他登記（例如 RVTools 建的 VC- 那筆）掛著 runs_on 邊時也要找得到 ESXi，
# 所以 ESXi 對應是用「同一台」的全部序號去找，不是只看掛 APID 的那一筆。
#
# 上下游只讀 system_deps（人工／日後介面清冊匯入），不推論、不造邊：
# 整張表是空的 → deps_available=False，畫面講「尚無資料」，跟「查過了沒有」分得開。

def _as_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):  # 空值／非數字的 CIA 值當 0（＝不算重大），不讓整張圖 500
        return 0


MAP_ENV_GROUPS = ("正式", "備援", "測試", "其他")


def map_room(location: str | None) -> str:
    """系統地圖的機房分組：去掉「01_」這類編號前綴（normalize_location）；
    「分公司-XX」一律併成「分公司」（各分公司通常一台實體機，逐間列沒有資訊量）。
    不用 location_groups.json——那份用「包含」比對，會把「分公司-板橋」算進板橋機房。"""
    import system_stats

    name = system_stats.normalize_location(location)
    return "分公司" if name.startswith("分公司") else name


BRANCH_SITE_RE = re.compile(r"^分公司\s*[-－—‐]+\s*\S+")      # 必須有連字號（正式機寫法「分公司-館前」，連字號前後可有空白）；純空白分隔（「分公司 A01」「分公司 本部」）不當分點，歸舊鍵並標待確認


def rack_room(location: str | None) -> str:
    """機櫃／容量／預留／盤點用的機房鍵（W221-016）：跟 map_room 一樣去掉編號前綴，但「分公司-XX」
    **保留完整分點名**（「分公司-台中」≠「分公司-高雄」），不同分點的同名櫃（都叫 A01）才不會共用 U 佔用、
    容量、用電、預留。只寫「分公司」沒有分點名＝舊鍵「分公司」（分點不明，待確認，不猜）。
    其他機房（板橋／敦南／內湖／未歸類／未填）跟 map_room 完全相同。顯示分組（各頁彙總）仍用 map_room。"""
    import system_stats

    name = system_stats.normalize_location(location)
    if BRANCH_SITE_RE.match(name):
        rest = re.sub(r"^分公司\s*[-－—‐]+\s*", "", name)
        return "分公司-" + "".join(rest.split())      # 「分公司 － 台中」「分公司-台中」同鍵
    if name.startswith("分公司"):
        return "分公司"                   # 「分公司」「分公司本部」「分公司A01」：沒有分點名＝舊鍵，待確認
    return name


def map_env_group(environment: str | None) -> str:
    """登記的環境別 → 正式／備援／測試（含 UAT／DEV／OA，normalize_env）／其他（含未填）。"""
    import system_stats

    v = system_stats.normalize_env(environment)
    return v if v in MAP_ENV_GROUPS[:3] else "其他"


def _rooms(hosts: list[dict], esxi_map: dict, cluster_map: dict) -> list[dict]:
    """主機依機房分組，機房內再依 Cluster 分列，沒有 ESXi 的歸「實體／未對應」。

    一台主機只歸一列（取它第一個 Cluster，依名稱排序），列加總＝機房台數；
    Cluster 橫跨多個機房時，每個機房各列一次、各算自己機房的台數——不猜它「屬於」哪一間。"""
    by_room: dict[str, dict] = {}
    seen_of = {h["key"]: h["seen"] for h in hosts}
    for h in hosts:
        r = by_room.setdefault(h["room"], {
            "room": h["room"], "hosts": [], "env": {g: 0 for g in MAP_ENV_GROUPS}, "groups": {}})
        r["hosts"].append(h["key"])
        r["env"][h["env_group"]] += 1
        if not h["esxi"]:
            gid, kind, label = "physical", "physical", "實體／未對應"
        else:
            clusters = sorted({c for e in h["esxi"] for c in esxi_map[e]["clusters"]})
            if clusters:
                gid, kind = clusters[0], "cluster"   # 同叢集未登記 VM 由前端依 id 到 clusters[] 取
                label = cluster_map[gid]["label"] if gid in cluster_map else gid
            else:
                gid, kind, label = "nocluster", "nocluster", "ESXi（Cluster 不明）"
        g = r["groups"].setdefault(gid, {"id": gid, "kind": kind, "label": label,
                                         "hosts": [], "esxi": set()})
        g["hosts"].append(h["key"])
        g["esxi"].update(h["esxi"])
    out = []
    for r in by_room.values():
        groups = sorted(r["groups"].values(), key=lambda g: (
            g["kind"] == "physical", g["kind"] == "nocluster", -len(g["hosts"]), (g["label"] or "").lower()))
        for g in groups:
            g["esxi"] = sorted(g["esxi"])
            g["host_count"] = len(g["hosts"])
        seen = [k for k in r["hosts"] if seen_of.get(k)]
        only = [k for k in r["hosts"] if not seen_of.get(k)]
        out.append({"room": r["room"], "host_count": len(r["hosts"]), "env": r["env"],
                    "hosts": r["hosts"], "seen": len(seen), "registered_only": len(only),
                    "seen_hosts": seen, "registered_only_hosts": only, "groups": groups})
    out.sort(key=lambda r: (-r["host_count"], r["room"]))
    return out


MAP_OS_FAMILIES = ("Linux", "Windows", "AIX", "其他")


def map_os_family(cls: str | None) -> str:
    """主機清單篩選用：os_class 大類（全站唯一一份判斷）收成 Linux／Windows／AIX／其他。"""
    return cls if cls in MAP_OS_FAMILIES[:3] else "其他"


def search_norm(v: str | None) -> str:
    """搜尋比對用：小寫、去空白／連字號／底線。「Test 1」「test-1」「TEST1」都比得到。"""
    return "".join(ch for ch in (v or "").lower() if ch not in " -_\t")


def ip_match(ip: str | None, q: str | None) -> bool:
    """IP 比對以「整段」為單位：完全相同，或查詢字是到某個點為止的前綴（10.99.1 / 10.99.1.）。
    不做子字串比對——查 10.99.1.12 不該冒出 10.99.1.128。查詢字不像 IP 就回 False。"""
    ip = (ip or "").strip()
    q = (q or "").strip()
    if not ip or not q or not all(ch.isdigit() or ch == "." for ch in q):
        return False
    if ip == q:
        return True
    return ip.startswith(q if q.endswith(".") else q + ".")


def host_systems(conn: sqlite3.Connection, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """系統挑選框打的是主機名／IP 時：找出符合的主機（逐台，machine_key），列出它屬於哪些業務系統。

    比對：主機名去網域（strip_domain）後 search_norm 包含查詢字；或 IP 包含查詢字。
    同一台的多筆登記任一筆有 APID 就列進它的系統；退役不列。查詢字少於 2 個字回空。"""
    import system_stats

    qn = search_norm(q)
    qi = (q or "").strip()
    if len(qn) < 2:
        return []
    groups: dict[str, dict] = {}
    for r in conn.execute("SELECT asset_serial, hostname, ip, asset_status, api_id, asset_name FROM hardware"):
        if system_stats.is_retired_row(r):
            continue
        k = system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        g = groups.setdefault(k, {"key": k, "rows": []})
        g["rows"].append(r)
    out = []
    for k, g in groups.items():
        rows = sorted(g["rows"], key=lambda r: r["asset_serial"] or "")
        hit = any(qn in search_norm(system_stats.strip_domain(r["hostname"])) for r in rows if r["hostname"]) \
            or any(ip_match(r["ip"], qi) for r in rows)
        if not hit:
            continue
        apis = sorted({(r["api_id"] or "").strip() for r in rows if (r["api_id"] or "").strip()})
        rep = rows[0]
        out.append({
            "key": k, "hostname": rep["hostname"] or rep["asset_serial"], "asset_serial": rep["asset_serial"],
            "ips": sorted({(r["ip"] or "").strip() for r in rows if (r["ip"] or "").strip()}),
            "systems": [{"api_id": a, "name": _system_name(conn, a)} for a in apis],
        })
    out.sort(key=lambda h: ((h["hostname"] or "").lower(), h["asset_serial"] or ""))
    return out[:limit]


def _system_name(conn: sqlite3.Connection, api_id: str) -> str:
    r = conn.execute("SELECT name FROM business_system WHERE api_id = ?", (api_id,)).fetchone()
    if r and r["name"]:
        return r["name"]
    r = conn.execute("SELECT MIN(asset_name) FROM hardware WHERE TRIM(api_id) = ?", (api_id,)).fetchone()
    return r[0] if r and r[0] else api_id


def _exist_fields(m: dict | None) -> dict:
    """一台主機的存在可信度欄位（來自 existence.compute 的同一台）。找不到＝不知道，
    當「只有登記」（不計入看得到），不猜它在。"""
    if not m:
        return {"exist_level": None, "exist_label": "不明", "exist_score": None, "seen": False}
    return {"exist_level": m["level"], "exist_label": m["level_label"], "exist_score": m["score"],
            "seen": bool(m["counted"])}


def _unregistered_vms(cluster_id, reverse_adj, key_of_serial, row_of_serial, keys_with_api,
                      ex_by_key, node) -> list[dict]:
    """同一個 Cluster 的 ESXi 上、整台沒有任何一筆登記 APID 的 VM（逐台，machine_key）。
    不計入任何系統的台數；退役不列。"""
    esxi_ids = sorted(src for src, t, _c in reverse_adj.get(cluster_id, []) if t == "member_of")
    found: dict[str, dict] = {}
    for eid in esxi_ids:
        for src, t, _c in reverse_adj.get(eid, []):
            if t != "runs_on" or not src.startswith("hw:"):
                continue
            serial = src[3:]
            k = key_of_serial.get(serial)
            if not k or k.startswith("ret:") or k in keys_with_api:
                continue
            r = row_of_serial[serial]
            v = found.setdefault(k, {"key": k, "asset_serial": serial, "hostname": r["hostname"] or serial,
                                     "ip": r["ip"], "os": r["os"], "esxi": [], **_exist_fields(ex_by_key.get(k))})
            n = node(eid)
            lab = n["label"] if n else eid
            if lab not in v["esxi"]:
                v["esxi"].append(lab)
    return sorted(found.values(), key=lambda v: ((v["hostname"] or "").lower(), v["asset_serial"]))


def room_verdict(here_prod: int, else_prod: int, else_dr: int) -> tuple[str, str]:
    """機房停機判讀（規則同畫面上的「註」）。回 (kind, 文字)。"""
    if here_prod == 0:
        return "low", "影響小"
    if else_prod + else_dr == 0:
        return "down", "整套停擺"
    if else_dr < here_prod:
        return "short", f"備援不足（{else_dr}/{here_prod}）"
    return "ok", "備援足夠"


def _room_outage(hosts: list[dict], rooms: list[dict]) -> list[dict]:
    """某機房整間停機時：這裡的正式／備援 vs 別處還有的正式／備援。
    數字只來自登記的環境別；不知道哪台備援對應哪台正式。每格都帶主機鍵供下鑽。"""
    rows = []
    for r in rooms:
        cell = {
            "here_prod": [h["key"] for h in hosts if h["room"] == r["room"] and h["env_group"] == "正式"],
            "here_dr": [h["key"] for h in hosts if h["room"] == r["room"] and h["env_group"] == "備援"],
            "else_prod": [h["key"] for h in hosts if h["room"] != r["room"] and h["env_group"] == "正式"],
            "else_dr": [h["key"] for h in hosts if h["room"] != r["room"] and h["env_group"] == "備援"],
        }
        kind, text = room_verdict(len(cell["here_prod"]), len(cell["else_prod"]), len(cell["else_dr"]))
        rows.append({"room": r["room"], "keys": cell, **{k: len(v) for k, v in cell.items()},
                     "verdict_kind": kind, "verdict": text})
    return rows


def _norm_api_id(api_id: str | None) -> str:
    v = (api_id or "").strip()
    return v[len("bizsys:"):] if v.startswith("bizsys:") else v


def system_map(conn: sqlite3.Connection, api_id: str) -> dict[str, Any]:
    import os_class
    import rack_layout
    import system_stats

    api_id = _norm_api_id(api_id)
    if not api_id:
        raise ValueError("缺少業務系統代碼")

    own_rows = conn.execute(
        "SELECT asset_serial, hostname, ip, os, environment, asset_status, asset_name, "
        "usage_unit, custodian, availability, physical_location, device_model, group_name, asset_purpose, "
        "hw_serial "
        "FROM hardware WHERE TRIM(api_id) = ? ORDER BY asset_serial", (api_id,)
    ).fetchall()
    if not own_rows:
        raise ValueError(f"查無此業務系統：{api_id}")

    # 中心系統資訊：對照表（business_system）優先，沒有就用登記上的資產名稱
    bs = conn.execute(
        "SELECT name, ap_department, ap_owner, COALESCE(NULLIF(TRIM(sys_class_override),''), "
        "sys_class) AS sys_class FROM business_system WHERE api_id = ?", (api_id,)).fetchone()
    name = (bs["name"] if bs and bs["name"] else None) or min(
        (r["asset_name"] for r in own_rows if r["asset_name"]), default=api_id)

    groups = system_stats.collapse_machines(own_rows)
    live_groups = {k: g for k, g in groups.items() if not k.startswith("ret:")}
    # 退役那幾筆也要列得出是哪幾筆（卡片寫「退役 N」點下去要看得到那 N 筆）
    retired = [
        {"asset_serial": r["asset_serial"], "hostname": r["hostname"] or r["asset_name"],
         "ip": r["ip"], "asset_status": r["asset_status"]}
        for k, g in groups.items() if k.startswith("ret:") for r in g
    ]
    retired.sort(key=lambda x: x["asset_serial"] or "")
    retired_records = len(retired)

    # 同一台的其他登記：用同一把 machine_key 對全表找
    # 順便建「序號 → 哪一台」與「哪幾台有任何一筆登記了 APID」，給同叢集未登記 VM 用
    serials_of: dict[str, set[str]] = {k: {r["asset_serial"] for r in g} for k, g in live_groups.items()}
    key_of_serial: dict[str, str] = {}
    row_of_serial: dict[str, sqlite3.Row] = {}
    keys_with_api: set[str] = set()
    for r in conn.execute("SELECT asset_serial, hostname, ip, os, asset_status, api_id FROM hardware"):
        k = system_stats.lifecycle_prefix(r) + system_stats.machine_key(
            r["hostname"], r["ip"], r["asset_serial"])
        key_of_serial[r["asset_serial"]] = k
        row_of_serial[r["asset_serial"]] = r
        if (r["api_id"] or "").strip():
            keys_with_api.add(k)
        if k in serials_of:
            serials_of[k].add(r["asset_serial"])

    # 存在可信度：沿用 existence.compute()（同一把 machine_key、退役同樣加 ret: 前綴），不另寫評分
    import existence

    ex = existence.compute(conn)
    ex_by_key = {m["key"]: m for m in ex["machines"]}
    ex_threshold = int(ex["summary"]["count_threshold"])

    reverse_adj, forward_adj = _load_edges(conn)
    node_cache: dict[str, sqlite3.Row | None] = {}

    def node(nid: str) -> sqlite3.Row | None:
        if nid not in node_cache:
            node_cache[nid] = _node_info(conn, nid)
        return node_cache[nid]

    esxi_map: dict[str, dict] = {}
    cluster_map: dict[str, dict] = {}
    ds_map: dict[str, dict] = {}
    hosts: list[dict] = []
    ordered = sorted(live_groups.items(), key=lambda kv: (
        (kv[1][0]["hostname"] or "").lower(), kv[1][0]["asset_serial"] or ""))
    for key, g in ordered:
        rep = g[0]
        esxi_ids: set[str] = set()
        ds_ids: set[str] = set()
        for s in serials_of[key]:
            for dst, etype, _conf in forward_adj.get(f"hw:{s}", []):
                if etype == "runs_on" and dst.startswith("esxi:"):
                    esxi_ids.add(dst)
                elif etype == "stores_on":
                    ds_ids.add(dst)
        for eid in sorted(esxi_ids):
            if eid not in esxi_map:
                n = node(eid)
                clusters = sorted(d for d, t, _c in forward_adj.get(eid, []) if t == "member_of")
                esxi_map[eid] = {
                    "node_id": eid, "label": n["label"] if n else eid,
                    "asset_serial": n["asset_serial"] if n else None,
                    "clusters": clusters, "hosts": [],
                }
                for cid in clusters:
                    cn = node(cid)
                    cluster_map.setdefault(cid, {"node_id": cid, "label": cn["label"] if cn else cid,
                                                 "esxi": []})["esxi"].append(eid)
            esxi_map[eid]["hosts"].append(key)
        for did in sorted(ds_ids):
            dn = node(did)
            ds_map.setdefault(did, {"node_id": did, "label": dn["label"] if dn else did,
                                    "hosts": []})["hosts"].append(key)
        ips = sorted({(r["ip"] or "").strip() for r in g if (r["ip"] or "").strip()})
        hosts.append({
            "key": key,
            "asset_serial": rep["asset_serial"],
            "serials": sorted(serials_of[key]),
            "hostname": rack_layout.display_name(rep["hostname"], rep["asset_name"], rep["asset_serial"]),
            "models": sorted({r["device_model"].strip() for r in g if (r["device_model"] or "").strip()}),
            "hw_serials": sorted({r["hw_serial"].strip() for r in g if (r["hw_serial"] or "").strip()}),
            "asset_names": sorted({r["asset_name"] for r in g if r["asset_name"]}),
            "os_class": os_class.classify_row(rep),
            "os_family": map_os_family(os_class.classify_row(rep)),
            "ips": ips,
            "ip": ips[0] if ips else None,
            "os": rep["os"],
            "environment": rep["environment"],
            "location": rep["physical_location"],
            "room": map_room(rep["physical_location"]),
            **_exist_fields(ex_by_key.get(key)),
            "env_group": map_env_group(rep["environment"]),
            "record_count": len(g),
            "esxi": sorted(esxi_ids),
            "datastores": sorted(ds_ids),
            "on_esxi": bool(esxi_ids),
        })

    # 上下游（逐系統規則）：這個系統有人工登記（system_deps）就只用人工；沒有才用連線推論
    # （observed_deps）。每一列帶 source。src 依賴 dst（方向約定見 ci_graph.py 開頭）
    deps_total = _registered_deps_total(conn)
    dep_rows = conn.execute(
        "SELECT id, TRIM(source) AS source, TRIM(target) AS target, dep_type FROM system_deps "
        "WHERE TRIM(source) = ? OR TRIM(target) = ? ORDER BY id", (api_id, api_id)).fetchall()

    def other_system(sid: str, dep_type: str | None) -> dict:
        r = conn.execute("SELECT name FROM business_system WHERE api_id = ?", (sid,)).fetchone()
        # 名稱順序跟中心系統一致：對照表 → 資產登記的系統名 → systems 人工標籤 → 代碼
        label = r["name"] if r and r["name"] else None
        hw = conn.execute("SELECT MIN(asset_name) AS n, COUNT(*) AS c FROM hardware "
                          "WHERE TRIM(api_id) = ?", (sid,)).fetchone()
        label = label or hw["n"]
        if not label:
            r = conn.execute("SELECT label FROM systems WHERE id = ?", (sid,)).fetchone()
            label = r["label"] if r and r["label"] else None
        return {"api_id": sid, "name": label or sid, "dep_type": dep_type,
                "has_hosts": bool(hw["c"])}

    depends_on, depended_by = [], []
    for d in dep_rows:
        if d["source"] == api_id and d["target"] != api_id:
            depends_on.append({**other_system(d["target"], d["dep_type"]),
                               "source": "registered", "evidence": None})
        elif d["target"] == api_id and d["source"] != api_id:
            depended_by.append({**other_system(d["source"], d["dep_type"]),
                                "source": "registered", "evidence": None})
    import observed_deps

    inferred = observed(conn)
    hubs = observed_deps.hub_ids(inferred)
    registered = registered_systems(conn)      # 跟上面 dep_rows 一樣用 TRIM 後的值比
    n_obs = 0
    for e in inferred["edges"]:
        if api_id not in (e["caller"], e["callee"]) or not observed_edge_applies(e, registered):
            continue
        dep_type = "連線推論" + (f"（{'、'.join(e['protocols'])}）" if "SSH/SFTP" in e["protocols"] else "")
        other, lst = (e["callee"], depends_on) if e["caller"] == api_id else (e["caller"], depended_by)
        lst.append({**other_system(other, dep_type), "source": "observed",
                    "hub": other in hubs, "evidence": observed_deps.evidence_view(e)})
        n_obs += 1
    use_observed = n_obs > 0
    deps_source = ("mixed" if dep_rows and use_observed else "registered" if dep_rows
                   else "observed" if use_observed else None)

    esxi_list = sorted(esxi_map.values(), key=lambda e: (e["label"] or "").lower())
    for c in cluster_map.values():
        c["unregistered_vms"] = _unregistered_vms(
            c["node_id"], reverse_adj, key_of_serial, row_of_serial, keys_with_api, ex_by_key, node)
        c["unregistered_count"] = len(c["unregistered_vms"])
    rooms = _rooms(hosts, esxi_map, cluster_map)
    outage = _room_outage(hosts, rooms)
    for e in esxi_list:
        e["host_count"] = len(e["hosts"])
    cluster_list = sorted(cluster_map.values(), key=lambda c: (c["label"] or "").lower())
    ds_list = sorted(ds_map.values(), key=lambda d: (d["label"] or "").lower())
    for d in ds_list:
        d["host_count"] = len(d["hosts"])

    summary = {
        "hosts": len(hosts),
        "records": sum(h["record_count"] for h in hosts),
        "on_esxi": sum(1 for h in hosts if h["on_esxi"]),
        "no_esxi": sum(1 for h in hosts if not h["on_esxi"]),
        "esxi": len(esxi_list),
        "clusters": len(cluster_list),
        "datastores": len(ds_list),
        "depends_on": len(depends_on),
        "depended_by": len(depended_by),
        "retired_records": retired_records,
        "seen": sum(1 for h in hosts if h["seen"]),
        "registered_only": sum(1 for h in hosts if not h["seen"]),
    }
    result = {
        "api_id": api_id,
        "node_id": f"bizsys:{api_id}",
        "name": name,
        "usage_unit": min((r["usage_unit"] for r in own_rows if r["usage_unit"]), default=None),
        "custodian": min((r["custodian"] for r in own_rows if r["custodian"]), default=None),
        "ap_department": bs["ap_department"] if bs else None,
        "ap_owner": bs["ap_owner"] if bs else None,
        "sys_class": bs["sys_class"] if bs else None,
        "severity": "重大" if max(_as_int(r["availability"]) for r in own_rows) >= 3 else "一般",
        "hosts": hosts,
        "retired": retired,
        "esxi": esxi_list,
        "clusters": cluster_list,
        "datastores": ds_list,
        "depends_on": depends_on,
        "depended_by": depended_by,
        "rooms": rooms,
        "outage": outage,
        "deps_available": deps_total > 0 or use_observed,
        "deps_source": deps_source,
        # 連線推論的附帶資訊（只有本系統走連線推論時才有）：未知來源 {rows, peers}、是不是樞紐
        "unknown_callers": inferred["unknown_callers"].get(api_id) if api_id not in registered else None,
        "is_hub": api_id in hubs,
        "observed": _observed_meta(inferred) if use_observed else None,
        "exist_threshold": ex_threshold,
        "summary": summary,
    }
    # 機櫃頁簽：實體機用自己的機櫃，VM 沿用 ESXi 主機的機櫃（rack_layout，跟 4-5 機房架構圖同一份解析）
    result["racks"] = rack_layout.system_racks(conn, result)
    return result
