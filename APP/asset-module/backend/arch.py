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
                  ehw.ip AS esxi_ip, cl.label AS cluster
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
        # vCenter 伺服器 IP：vi_sdk_server 為空，系統未收 → 不提供（誠實留白）
    }
