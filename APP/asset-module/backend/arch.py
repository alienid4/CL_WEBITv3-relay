"""架構圖資料來源。

兩種來源，刻意分開：
- vCenter：從 CI 圖（ci_node/ci_edge）即時算，資料變自動更新，不需人維護。
- FTP 等：內容（哪些主機是 FTP、整併階段、帳號數）系統收不到，是人維護的。
  只存「哪些 IP 是這張圖的成員」在 app_settings（真實 IP 不進 git），
  每台的主機名/機房/環境別一律回頭 join hardware 用真資料填，
  查不到的欄位不編（使用者 2026-09-07：查不到的就不要寫上去）。
"""
from __future__ import annotations

import json
import re
import sqlite3

from db import get_setting

# cluster label 前綴 → 機房。用開頭比對，認不出來就原樣顯示（不猜）。
_SITE_LABELS = {"BQ": "板橋機房", "NH": "內湖機房", "DN": "敦南機房"}
_SITE_ORDER = {"內湖機房": 0, "板橋機房": 1, "敦南機房": 2}

FTP_HOSTS_SETTING = "arch_ftp_hosts"  # app_settings：JSON 陣列，元素 {"ip","zone"}


def _site_of(label: str) -> str:
    prefix = (label or "").split("_", 1)[0].upper()
    return _SITE_LABELS.get(prefix, prefix or "未分類")


def vcenter_topology(conn: sqlite3.Connection) -> dict:
    """vCenter 虛擬化架構：機房 → cluster（ESXi 數／VM 數／datastore 數／是否 vSAN）。

    可下鑽：每個 cluster 帶它的 ESXi 清單與各台 VM 數，ESXi 若對得到資產就給 asset_serial。
    """
    clusters = conn.execute(
        "SELECT node_id, label FROM ci_node WHERE node_type = 'cluster'"
    ).fetchall()

    out: list[dict] = []
    for cl in clusters:
        cid, label = cl["node_id"], cl["label"]
        esxi_rows = conn.execute(
            """SELECT n.node_id, n.label, n.asset_serial
                 FROM ci_edge m
                 JOIN ci_node n ON n.node_id = m.src_node_id
                WHERE m.dst_node_id = ? AND m.edge_type = 'member_of'
                  AND n.node_type = 'esxi'""",
            (cid,),
        ).fetchall()

        esxi_list: list[dict] = []
        vm_total = 0
        for e in esxi_rows:
            vm = conn.execute(
                "SELECT count(*) c FROM ci_edge WHERE dst_node_id = ? AND edge_type = 'runs_on'",
                (e["node_id"],),
            ).fetchone()["c"]
            vm_total += vm
            esxi_list.append(
                {"label": e["label"], "asset_serial": e["asset_serial"], "vm": vm}
            )

        datastore_count = 0
        esxi_ids = [e["node_id"] for e in esxi_rows]
        if esxi_ids:
            placeholders = ",".join("?" for _ in esxi_ids)
            datastore_count = conn.execute(
                f"""SELECT count(DISTINCT so.dst_node_id) c
                      FROM ci_edge ro
                      JOIN ci_edge so ON so.src_node_id = ro.src_node_id
                                      AND so.edge_type = 'stores_on'
                     WHERE ro.edge_type = 'runs_on'
                       AND ro.dst_node_id IN ({placeholders})""",
                esxi_ids,
            ).fetchone()["c"]

        low = (label or "").lower()
        out.append(
            {
                "cluster": label,
                "site": _site_of(label),
                "env": "UAT" if "uat" in low else "PROD",
                "vsan": "vsan" in low,
                "esxi_count": len(esxi_rows),
                "vm_count": vm_total,
                "datastore_count": datastore_count,
                "esxi": sorted(esxi_list, key=lambda x: -x["vm"]),
            }
        )

    out.sort(
        key=lambda c: (
            c["env"] != "PROD",
            _SITE_ORDER.get(c["site"], 9),
            -c["vm_count"],
        )
    )

    datastore_total = conn.execute(
        "SELECT count(*) c FROM ci_node WHERE node_type = 'datastore'"
    ).fetchone()["c"]

    totals = {
        "sites": len({c["site"] for c in out}),
        "clusters": len(out),
        "esxi": sum(c["esxi_count"] for c in out),
        "vm": sum(c["vm_count"] for c in out),
        "datastore": datastore_total,
        "vsan_clusters": sum(1 for c in out if c["vsan"]),
        "uat_clusters": sum(1 for c in out if c["env"] == "UAT"),
    }
    return {"clusters": out, "totals": totals}


def ftp_diagram(conn: sqlite3.Connection) -> dict:
    """FTP 主機架構：成員清單（哪些 IP 是 FTP）是人維護的，存 app_settings；

    每台的主機名／機房／環境別一律 join hardware 用真資料填。系統查不到的欄位
    （FTP 帳號數、近月檔案數、整併階段、FTP 軟體）一律不編——使用者定的：查不到就不寫。
    """
    raw = get_setting(conn, FTP_HOSTS_SETTING, "") or ""
    try:
        members = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        members = []

    hosts: list[dict] = []
    for m in members:
        ip = (m.get("ip") or "").strip()
        zone = (m.get("zone") or "").strip()  # DMZ／正式／測試：人畫圖的分區，非系統欄位
        row = None
        if ip:
            row = conn.execute(
                "SELECT hostname, ip, physical_location, environment, asset_serial "
                "FROM hardware WHERE ip = ? LIMIT 1",
                (ip,),
            ).fetchone()
        hosts.append(
            {
                "ip": ip,
                "zone": zone,
                "hostname": (row["hostname"] if row else None),
                "site": (row["physical_location"] if row else None),
                "environment": (row["environment"] if row else None),
                "asset_serial": (row["asset_serial"] if row else None),
                "registered": bool(row),
            }
        )

    return {
        "hosts": hosts,
        "totals": {
            "hosts": len(hosts),
            "registered": sum(1 for h in hosts if h["registered"]),
        },
        "manual": True,  # 提醒前端：這張是人維護的成員清單，不是系統自動偵測
    }


def vm_placement(conn: sqlite3.Connection, asset_serial: str) -> dict:
    """這台 VM 歸哪個 VC/cluster、跑在哪台 ESXi（給資產詳細頁）。

    vi_sdk_server（RVTools 的 vCenter Server 名）目前為空，所以「VC」用 cluster 代表
    （cluster 名已含機房/環境，足以辨識是哪套 vCenter）；ESXi 給實際承載主機。
    走 CI 圖：host(asset_serial) → runs_on → esxi → member_of → cluster。
    非 VM／查無關聯回 {"found": False}。
    """
    row = conn.execute(
        """SELECT e.label AS esxi, e.asset_serial AS esxi_serial,
                  ehw.ip AS esxi_ip, ehw.physical_location AS esxi_location,
                  ehw.vi_sdk_server AS esxi_vc, cl.label AS cluster
             FROM ci_node h
             JOIN ci_edge ro ON ro.src_node_id = h.node_id AND ro.edge_type = 'runs_on'
             JOIN ci_node e  ON e.node_id = ro.dst_node_id AND e.node_type = 'esxi'
        LEFT JOIN hardware ehw ON ehw.asset_serial = e.asset_serial
        LEFT JOIN ci_edge m  ON m.src_node_id = e.node_id AND m.edge_type = 'member_of'
        LEFT JOIN ci_node cl ON cl.node_id = m.dst_node_id AND cl.node_type = 'cluster'
            WHERE h.node_type = 'host' AND h.asset_serial = ?
            LIMIT 1""",
        (asset_serial,),
    ).fetchone()
    if not row:
        return {"found": False}
    cluster = row["cluster"]
    return {
        "found": True,
        "esxi": row["esxi"],
        "esxi_serial": row["esxi_serial"],
        "esxi_ip": row["esxi_ip"],  # ESXi 實體主機 IP（該 ESXi 有登記成資產才有）
        "cluster": cluster,
        "site": _site_of(cluster) if cluster else None,
        "env": ("UAT" if cluster and "uat" in cluster.lower() else "PROD") if cluster else None,
        # 承載這台 VM 的 ESXi 登記在哪個機房（2026-09-17 使用者：
        # 「機房地點，其實可以參考 VC……應該就可以判斷出來它是什麼機房」）。
        #
        # 用 ESXi 而不是「VC→機房對照表」：ESXi 本身就是資產、機房欄位已經有人在維護，
        # 不必再多一份要人手動同步的對照表（多一份就多一個會過期的地方）。
        # 221 實測：68 台 ESXi 有 45 台查得到機房，而 1142 台主機自己沒填機房。
        #
        # ⚠️ 這是**推導值**，呼叫端顯示時必須標明來源，不可以混進「登記值」裡
        # （幾分證據說幾分話：VM 跑在板橋的 ESXi 上 → 它現在在板橋，但那不是登記資料）。
        "esxi_location": row["esxi_location"],
        # ESXi 自己登記的 vCenter（RVTools vi_sdk_server）。VM 那筆常常是空的，
        # 但承載它的 ESXi 有——對使用者來說「這台歸哪個 VC」是同一個答案。
        "esxi_vc": row["esxi_vc"],
    }


# ===== OCP（OpenShift）叢集架構 =====
# 使用者 2026-09-20：「OCP 能做一個架構圖嗎？我們在架構圖就可以知道他的狀態」
# ——並說明 OCP「其實是一台大設備，但由很多小主機建立起來」。
# 所以這張圖要同時成立兩件事：對外看得到「一個平台」，對內查得到「每個節點」。
#
# 分群依據（照可信度排，畫面會標出用了哪一種）：
#   1. FQDN：node-01.paas-a.ocp.example.com → 叢集 paas-bq（最準，機器自己報的名字）
#   2. 用途／資產名稱裡的 paas-xxx 字樣（人填的，次之）
#   3. 都沒有 → 「未分群」，不猜
#   4. 人工指定（ocp_cluster）——判不出來的由人自己換群組，指定值優先於上面全部
#
# ⚠️ 規則是照 221 實際資料長出來的，不是想像的（2026-09-20 使用者：「能分的先分」）：
#    原本只認 .ocp. 與 paas-xxx，47 台被丟進未分群；看了那 47 台才發現三種漏網寫法。
_OCP_FQDN = re.compile(r"^[^.]+\.([^.]+)\.ocp[a-z]*\.", re.I)   # 測試叢集網域是 ocpt 不是 ocp
_OCP_PAREN = re.compile(r"OCP[^()]*\(([A-Za-z0-9-]{2,})\)", re.I)   # 「OCP Infra (uat-nh01)」
_OCP_NAME = re.compile(r"\b(paas-[a-z0-9-]+)\b", re.I)              # 「…paas-bq AP 09」
_OCP_DASH = re.compile(r"\bOCP\b[^A-Za-z0-9]*[0-9.]*\s*([a-z]{2,}-[a-z0-9]{2,})\b", re.I)
# 角色字不可以被當成叢集名（例：「OCP (Infra)」不是一座叫 infra 的叢集）
_ROLE_WORDS = {"infra", "master", "worker", "control", "bootstrap", "node"}
# RHCOS = Red Hat CoreOS，同一個東西兩種寫法；大小寫與空白也不該算成不同版本。
_VER_ALIAS = re.compile(r"\b(rhcos|red\s*hat\s*coreos|coreos)", re.I)


def _norm_ver(v: str) -> str:
    s = _VER_ALIAS.sub("coreos", (v or "").strip().lower())
    return re.sub(r"[\s_-]+", "", s)


# 中文也要認：資產用途是人填的，不是每個人都寫英文。
# 只認英文的話，寫「主控節點」的那些台會全部掉進「未標示」，
# 看起來像資料沒填，其實是我們沒讀懂（兩者要做的事完全不同）。
_ROLE_RULES = (("master", "Master"), ("control", "Master"), ("主控", "Master"),
               ("控制節點", "Master"), ("infra", "Infra"), ("基礎節點", "Infra"),
               ("worker", "Worker"), ("工作節點", "Worker"), ("運算節點", "Worker"),
               ("bootstrap", "Bootstrap"), ("引導節點", "Bootstrap"))


def _ocp_cluster_of(hostname: str | None, *texts: str | None) -> tuple[str, str]:
    """回 (叢集名, 依據)。判不出來就是「未分群」——不要用機房或網段硬湊。"""
    m = _OCP_FQDN.match((hostname or "").strip())
    if m:
        return m.group(1).lower(), "FQDN"
    for t in texts:
        for rx in (_OCP_PAREN, _OCP_NAME, _OCP_DASH):
            m2 = rx.search(t or "")
            if m2 and m2.group(1).lower() not in _ROLE_WORDS:
                return m2.group(1).lower(), "用途／名稱"
    return "未分群", "判不出來"


def _ocp_role(*texts: str | None) -> str:
    blob = " ".join(t or "" for t in texts).lower()
    for kw, label in _ROLE_RULES:
        if kw in blob:
            return label
    return "未標示"


def ocp_siblings(conn, asset_serial: str) -> dict:
    """這台的「兄弟姊妹」＝同一座叢集的其他節點（2026-09-20 使用者：
    「在資訊欄寫他的兄弟姊妹是誰，出事怎看出關聯」）。

    出事時最常問的是「這台掛了，同一座還有誰、還活著幾台」——所以回同叢集節點與各自狀態。
    """
    topo = ocp_topology(conn)
    for c in topo["clusters"]:
        me = next((n for n in c["nodes"] if n["asset_serial"] == asset_serial), None)
        if not me:
            continue
        others = [n for n in c["nodes"] if n["asset_serial"] != asset_serial]
        healthy = sum(1 for n in c["nodes"] if n["stage"] in ("complete", "collected"))
        return {"is_ocp": True, "cluster": c["cluster"], "basis": c["basis"],
                "me": me, "siblings": others, "node_count": c["node_count"],
                "by_role": c["by_role"], "by_stage": c["by_stage"], "risks": c["risks"],
                "healthy": healthy}
    return {"is_ocp": False}


def ocp_topology(conn) -> dict:
    """OCP 叢集 → 節點：每個節點的角色、版本、納管狀態，叢集層給風險提示。

    節點判定用全站正典（pipeline 的 OS 類型＝OpenShift 節點），不自己寫一套規則；
    納管狀態也直接沿用漏斗的關卡，畫面上兩邊才不會各說各話。唯讀。
    """
    import ocp_cluster
    import pipeline

    s = pipeline.summarize(conn)
    meta = {r["asset_serial"]: dict(r) for r in conn.execute(
        "SELECT asset_serial, asset_name, asset_purpose, api_id, remark, big_ip_vip FROM hardware")}
    manual = ocp_cluster.active_map(conn)

    clusters: dict[str, dict] = {}
    for it in s["items"]:
        if it.get("os_type") != pipeline.OS_OPENSHIFT:
            continue
        m = meta.get(it.get("asset_serial")) or {}
        name, purpose = m.get("asset_name"), m.get("asset_purpose")
        cluster, basis = _ocp_cluster_of(it.get("hostname"), purpose, name, m.get("remark"))
        # 人工指定永遠優先，並標明是誰指定的（使用者 2026-09-20：判不出來的由人自己換群組）
        ov = next((manual[s2] for s2 in ([it.get("asset_serial")] + list(it.get("dup_serials") or []))
                   if s2 in manual), None)
        if ov and (ov.get("cluster") or "").strip():
            cluster, basis = ov["cluster"], "人工指定"
        c = clusters.setdefault(cluster, {
            "cluster": cluster, "basis": basis, "nodes": [],
            "by_role": {}, "by_stage": {}, "versions": {}, "locations": {}, "environments": {},
            "vips": [], "role_tiers": {},
        })
        role = _ocp_role(purpose, name, it.get("hostname"))
        role_basis = "用途／名稱" if role != "未標示" else "判不出來"
        if (ov or {}).get("role"):
            role, role_basis = ov["role"], "人工指定"
        ver = (it.get("os") or "").strip() or "未填"
        loc = (it.get("physical_location") or "").strip() or "未填"
        c["nodes"].append({
            "hostname": it.get("hostname"), "ip": it.get("ip"),
            "asset_serial": it.get("asset_serial"), "role": role, "os": ver,
            "stage": it.get("stage"), "stage_label": it.get("stage_label"), "tone": it.get("tone"),
            "location": it.get("physical_location"), "environment": it.get("environment"),
            "api_id": m.get("api_id"), "purpose": purpose,
            "dup_count": it.get("dup_count", 1), "dup_reason": it.get("dup_reason") or "",
            "last_check": it.get("last_check"),
            "cluster_basis": basis,
            "cluster_by": (ov or {}).get("updated_by"), "cluster_at": (ov or {}).get("updated_at"),
            "cluster_reason": (ov or {}).get("reason"),
            "role_basis": role_basis,
        })
        # 入口 VIP：OCP 的 api／*.apps 入口通常登記在 BIG IP/VIP 欄；有才顯示，沒有不編
        vip = (m.get("big_ip_vip") or "").strip()
        if vip and vip not in ("無", "N/A", "n/a", "-") and vip not in c["vips"]:
            c["vips"].append(vip)
        env = (it.get("environment") or "").strip() or "未填"
        for key, val in (("by_role", role), ("by_stage", it.get("stage_label")),
                         ("versions", ver), ("locations", loc), ("environments", env)):
            c[key][val] = c[key].get(val, 0) + 1

    out = []
    for c in clusters.values():
        c["nodes"].sort(key=lambda n: (n["role"], n["hostname"] or ""))
        c["node_count"] = len(c["nodes"])
        # 機房 × 環境（2026-09-20 使用者：「架構圖分 機房*環境」）：
        # 取這座叢集最多節點所在的機房／環境當它的格子；跨格的另外標出來，不要偷偷歸成一格。
        # 角色分層是骨架（2026-09-20 使用者：「架構圖給角色，這些都放在第二層資訊」）：
        # 每一層附「這層幾台、其中幾台資料齊全、卡在哪一關最多」，版本與分群依據退到第二層。
        for role in ("Master", "Infra", "Worker", "Bootstrap", "未標示"):
            nodes = [n for n in c["nodes"] if n["role"] == role]
            if not nodes:
                continue
            stages: dict[str, int] = {}
            for n in nodes:
                stages[n["stage_label"]] = stages.get(n["stage_label"], 0) + 1
            # 2026-09-21：台數只算**現役**。paas-bq 的「Master 6」實際是現役 3 ＋ 已退役 3
            # （SECSVR175-014/015/016 換過機器，主機名沿用、資產編號換新），把退役算進去
            # 會讓架構圖上的規模灌水一倍。退役的照樣畫出來（前端用灰虛框），但不進主數字。
            #
            # ⚠ 只排退役，**不能順手排 exempt**：同一天使用者拍板「第一期不納管 OCP」之後，
            # 所有 OCP 節點都會是 exempt——一起排掉的話每一排都變 0 台，整張架構圖空掉。
            # 「不納管」不等於「不存在」。
            gone = [n for n in nodes if n["stage"] == "retired"]
            live = [n for n in nodes if n not in gone]
            c["role_tiers"][role] = {
                "count": len(live),
                "retired": len(gone),
                "ready": sum(1 for n in live if n["stage"] in ("complete", "collected")),
                # 整排都是「非納管設備」時，前端要寫「第一期不納管」而不是「0 台資料齊全」——
                # 後者讀起來像漏做，前者才是事實。
                "not_managed": all(n["stage"] == "exempt" for n in live) if live else False,
                "worst": max(stages, key=lambda k: stages[k]),
                "by_stage": stages,
            }
        c["primary_location"] = max(c["locations"], key=lambda k: c["locations"][k], default="未填")
        c["primary_env"] = max(c["environments"], key=lambda k: c["environments"][k], default="未填")
        c["spans"] = []
        if len(c["locations"]) > 1:
            c["spans"].append("跨機房：" + "、".join(f"{k}×{v}" for k, v in sorted(c["locations"].items())))
        if len(c["environments"]) > 1:
            c["spans"].append("跨環境：" + "、".join(f"{k}×{v}" for k, v in sorted(c["environments"].items())))
        # 叢集層的風險：版本不一致（OCP 混版是真風險）、有節點不在掌握中、重複登記
        risks = []
        # 2026-09-21：**比對前先正規化**。原本 13 座裡大部分的「混版」都是假警報：
        # paas-bq 的「CoreOS 4.12 vs RHCOS4.12」——RHCOS 就是 Red Hat CoreOS，同一個東西；
        # uat-nh 的「CoreOS 4.12 vs Coreos4.12」更只是大小寫。拿這種東西報風險，
        # 人會跑去查一個不存在的升版問題，下次真的混版他就不看了。
        # 顯示還是照原字串（不自作主張改資料），只有判定用正規化結果。
        norm: dict[str, list[str]] = {}
        for k in c["versions"]:
            norm.setdefault(_norm_ver(k), []).append(k)
        if len(norm) > 1:
            risks.append("同叢集有 " + str(len(norm)) + " 種版本："
                         + "、".join(f"{k}×{v}" for k, v in sorted(c["versions"].items())))
        elif any(len(v) > 1 for v in norm.values()):
            same = sorted(next(v for v in norm.values() if len(v) > 1))
            risks.append("版本其實一致，只是寫法不同（資料品質）："
                         + "、".join(same))
        bad = sum(v for k, v in c["by_stage"].items() if k not in ("資料齊全", "已收集（設備）"))
        if bad:
            risks.append(f"{bad} 台還沒進到「資料齊全」（見各節點狀態）")
        # 同主機名、不同資產編號，而且舊的已退役 → 汰換。這不是重複登記，分開講，
        # 不然使用者會以為資料髒掉而去「清重複」，反而把汰換紀錄刪掉。
        by_host: dict[str, list] = {}
        for n in c["nodes"]:
            if n["hostname"]:
                by_host.setdefault(n["hostname"], []).append(n)
        # 同樣只比「退役 vs 非退役」：OCP 節點第一期全部是 exempt，把 exempt 當成「不在了」
        # 會讓汰換永遠偵測不到（新舊兩台會被看成同一邊）。
        swapped = [h for h, ns in by_host.items()
                   if len(ns) > 1 and any(x["stage"] == "retired" for x in ns)
                   and any(x["stage"] != "retired" for x in ns)]
        if swapped:
            risks.append("疑似汰換（主機名沿用、資產編號換新）："
                         + "、".join(sorted(swapped)[:3])
                         + (f" 等 {len(swapped)} 組" if len(swapped) > 3 else f" 共 {len(swapped)} 組"))
        dups = sum(1 for n in c["nodes"] if (n["dup_count"] or 1) > 1)
        if dups:
            risks.append(f"{dups} 台有多筆登記（同一節點被登記兩次會讓台數多算）")
        c["risks"] = risks
        out.append(c)
    out.sort(key=lambda c: (c["cluster"] == "未分群", -c["node_count"]))
    return {
        "clusters": out,
        "totals": {
            "clusters": sum(1 for c in out if c["cluster"] != "未分群"),
            "nodes": sum(c["node_count"] for c in out),
            "ungrouped": next((c["node_count"] for c in out if c["cluster"] == "未分群"), 0),
        },
        "scan_time": s.get("scan_time"),
        "note": "節點判定用全站正典的 OS 類型（OpenShift 節點）；狀態沿用納管漏斗的關卡。"
                "分群依 FQDN ＞ 用途／名稱 ＞ 人工指定優先；都沒有就標未分群，不用機房或網段硬湊。",
        "cluster_names": sorted({c["cluster"] for c in out if c["cluster"] != "未分群"}),
        # 畫面用的矩陣軸：機房（列）×環境（欄），順序固定才不會每次重整跳來跳去
        "locations": sorted({c["primary_location"] for c in out}),
        "environments": sorted({c["primary_env"] for c in out}),
    }
