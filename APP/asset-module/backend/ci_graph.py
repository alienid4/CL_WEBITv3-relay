"""MICS 切片 1：CI 圖譜（影響範圍查詢的地基）。

## 為什麼另開 ci_node/ci_edge，不沿用 systems/system_deps

那組是「業務系統」單一節點型別的扁平圖，且主機掛系統靠 `hardware.api_id` 單值字串
（一台主機只能屬一個系統）。要回答「這台 ESXi 死了影響哪些 VM／哪些業務／要通知誰」
需要異質節點（VM/ESXi/Cluster/Datastore/機櫃/網段/業務系統）與具型別的邊，撐不起來。

`systems`/`system_deps` 不動、不搬、不退役：`rebuild()` 把它們**單向投影**進來
（永不回寫），`topology.vue` 與既有 5 支 API 完全不受影響——那組承載的是別處沒有的
「業務層人工真相」，這裡只是借用顯示，不動它的資料本體。

## 方向約定（跟 system_deps 一致，照抄不要自己重想）

**src 依賴 dst。** 所以「誰依賴我」＝反向找 src ＝ 爆炸半徑；「我依賴誰」＝正向找 dst ＝
找肇因。VM→ESXi、ESXi→Cluster、bizsys→hw 都是 src 指向被依賴者。

## 覆蓋原則（這是本檔最容易寫錯的地方）

只 upsert `source LIKE 'derive:%' AND manual_locked=0`；本輪 rebuild 沒再出現的 derive
列軟刪（`gone_at`，不 DELETE）；`source='manual'` 或 `manual_locked=1` **完全不碰**——
這是日後人工補 switch/storage 關聯的入口，一開始就要留，否則人補完的資料第一次
rebuild 就會消失。

## ESXi 主機名格式（2026-08-18 查證，見計畫檔）

vCenter/RVTools 報的是小寫＋FQDN（`esxi169-220.<公司網域>`），`hardware.hostname`
登記的是全大寫＋無網域（`ESXI169-220`）。node_id 用正規化過的名稱（大寫、去網域）當
key，這樣兩邊資料才會落在同一個節點上；`label` 保留原始 RVTools 顯示名稱給人看。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731 - 本地時間，決策T6


def _normalize_esxi_name(name: str) -> str:
    """ESXi 主機名正規化：去網域後綴、轉大寫。用於 node_id 比對/回填 asset_serial，
    不用於顯示（顯示用原始字串）。"""
    return str(name).strip().split(".")[0].upper()


def _upsert_node(
    conn: sqlite3.Connection, seen: set[str], *, node_id: str, node_type: str, label: str,
    asset_serial: str | None, source: str, source_key: str | None, attrs: dict | None,
) -> None:
    seen.add(node_id)
    now = NOW()
    existing = conn.execute(
        "SELECT manual_locked FROM ci_node WHERE node_id = ?", (node_id,)
    ).fetchone()
    if existing and existing["manual_locked"]:
        return  # 人工鎖定，rebuild 完全不碰
    attrs_json = json.dumps(attrs, ensure_ascii=False) if attrs else None
    if existing:
        conn.execute(
            "UPDATE ci_node SET node_type=?, label=?, asset_serial=?, source=?, source_key=?, "
            "attrs=?, last_seen_at=?, gone_at=NULL, updated_at=? WHERE node_id=?",
            (node_type, label, asset_serial, source, source_key, attrs_json, now, now, node_id),
        )
    else:
        conn.execute(
            "INSERT INTO ci_node (node_id, node_type, label, asset_serial, source, source_key, "
            "attrs, first_seen_at, last_seen_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (node_id, node_type, label, asset_serial, source, source_key, attrs_json,
             now, now, now, now),
        )


def _upsert_edge(
    conn: sqlite3.Connection, seen: set[tuple[str, str, str]], *, src: str, dst: str,
    edge_type: str, confidence: str, evidence: str | None, source: str, source_key: str | None,
) -> None:
    key = (src, dst, edge_type)
    seen.add(key)
    now = NOW()
    existing = conn.execute(
        "SELECT manual_locked FROM ci_edge WHERE src_node_id=? AND dst_node_id=? AND edge_type=?",
        (src, dst, edge_type),
    ).fetchone()
    if existing and existing["manual_locked"]:
        return
    if existing:
        conn.execute(
            "UPDATE ci_edge SET confidence=?, evidence=?, source=?, source_key=?, "
            "last_seen_at=?, gone_at=NULL WHERE src_node_id=? AND dst_node_id=? AND edge_type=?",
            (confidence, evidence, source, source_key, now, src, dst, edge_type),
        )
    else:
        conn.execute(
            "INSERT INTO ci_edge (src_node_id, dst_node_id, edge_type, confidence, evidence, "
            "source, source_key, first_seen_at, last_seen_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (src, dst, edge_type, confidence, evidence, source, source_key, now, now, now),
        )


def rebuild(conn: sqlite3.Connection) -> dict[str, int]:
    """全量重建 CI 圖譜。回傳 {node_count, edge_count, gone_count}。

    冪等：同一份底層資料重跑兩次，節點/邊數不變、內容不變（UPSERT 而非 INSERT）。
    """
    import segments

    seen_nodes: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()

    # ===== 1. hardware → hw 節點（每一筆登記資產，含 RVTools 建的 VC- 那些）=====
    hw_rows = conn.execute(
        "SELECT asset_serial, hostname, asset_name, ip, os, environment, rack_no, "
        "physical_location, api_id FROM hardware"
    ).fetchall()
    for r in hw_rows:
        label = r["hostname"] or r["asset_name"] or r["asset_serial"]
        _upsert_node(
            conn, seen_nodes,
            node_id=f"hw:{r['asset_serial']}", node_type="host", label=label,
            asset_serial=r["asset_serial"], source="derive:hardware", source_key=r["asset_serial"],
            attrs={"ip": r["ip"], "os": r["os"], "environment": r["environment"]},
        )

        # bizsys:<api_id> → hw:<serial>（業務系統，人工登記在 hardware.api_id 上）。
        # label 用 asset_name（人看得懂的系統名，例如「STO 交易管理系統」），不是
        # api_id 代碼本身——api_id 只留在 node_id 裡當內部鍵值，2026-08-19 使用者
        # 反映查出來的節點只顯示代碼「N-218」看不出是什麼系統，要看名字。
        if r["api_id"]:
            bizsys_id = f"bizsys:{r['api_id']}"
            _upsert_node(
                conn, seen_nodes, node_id=bizsys_id, node_type="business_service",
                label=r["asset_name"] or r["api_id"], asset_serial=None, source="derive:hardware",
                source_key=r["api_id"], attrs=None,
            )
            _upsert_edge(
                conn, seen_edges, src=bizsys_id, dst=f"hw:{r['asset_serial']}",
                edge_type="depends_on", confidence="證據", evidence="hardware.api_id 登記",
                source="derive:hardware", source_key=r["asset_serial"],
            )

        # hw:<serial> → rack:<loc>#<no>（機櫃）
        if r["rack_no"] and r["physical_location"]:
            rack_id = f"rack:{r['physical_location']}#{r['rack_no']}"
            _upsert_node(
                conn, seen_nodes, node_id=rack_id, node_type="rack",
                label=f"{r['physical_location']} {r['rack_no']}", asset_serial=None,
                source="derive:hardware", source_key=rack_id, attrs=None,
            )
            _upsert_edge(
                conn, seen_edges, src=f"hw:{r['asset_serial']}", dst=rack_id,
                edge_type="located_in", confidence="證據",
                evidence="hardware.rack_no/physical_location 登記",
                source="derive:hardware", source_key=r["asset_serial"],
            )

        # hw:<serial> → seg:<cidr>（網段，IP 推導，非實測，故「推論」）
        if r["ip"]:
            seg = segments.find_segment_for_ip(conn, r["ip"])
            if seg and seg.get("cidr"):
                seg_id = f"seg:{seg['cidr']}"
                _upsert_node(
                    conn, seen_nodes, node_id=seg_id, node_type="segment",
                    label=seg.get("purpose_desc") or seg["cidr"], asset_serial=None,
                    source="derive:hardware", source_key=seg["cidr"],
                    attrs={"location": seg.get("location"), "environment": seg.get("environment")},
                )
                _upsert_edge(
                    conn, seen_edges, src=f"hw:{r['asset_serial']}", dst=seg_id,
                    edge_type="located_in", confidence="推論",
                    evidence=f"IP {r['ip']} 落在網段表 {seg['cidr']}（推導，非實測）",
                    source="derive:hardware", source_key=r["asset_serial"],
                )

    # ===== 2. source_record(source='vcenter') → esxi/cluster/ds 節點與邊 =====
    # esxi_host 已經在既有匯入的 payload JSON 裡（免重匯即可用）；cluster/datastore
    # 要擴欄位後重匯才有值，沒有就自然不建那條邊，不會報錯。
    hostname_to_serial = {
        (r["hostname"] or "").strip().upper(): r["asset_serial"]
        for r in hw_rows if r["hostname"]
    }
    vc_rows = conn.execute(
        "SELECT payload, resolved_hardware_id, collected_at FROM source_record "
        "WHERE source = 'vcenter'"
    ).fetchall()
    id_to_serial = {}
    if vc_rows:
        ids = conn.execute("SELECT id, asset_serial FROM hardware").fetchall()
        # hardware.id 不在上面 hw_rows 的 SELECT 裡，另外查一次對照表
        id_to_serial = {row["id"]: row["asset_serial"] for row in ids}

    for r in vc_rows:
        try:
            rec = json.loads(r["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        vm_serial = id_to_serial.get(r["resolved_hardware_id"])
        esxi_host = rec.get("esxi_host")
        if not vm_serial or not esxi_host:
            continue
        esxi_key = _normalize_esxi_name(esxi_host)
        esxi_id = f"esxi:{esxi_key}"
        esxi_serial = hostname_to_serial.get(esxi_key)
        _upsert_node(
            conn, seen_nodes, node_id=esxi_id, node_type="esxi", label=str(esxi_host),
            asset_serial=esxi_serial, source="derive:rvtools", source_key=esxi_key,
            attrs=None if esxi_serial else {"note": "未登記於資產庫"},
        )
        evidence = f"RVTools vInfo Host 欄，匯入於 {r['collected_at']}"
        _upsert_edge(
            conn, seen_edges, src=f"hw:{vm_serial}", dst=esxi_id, edge_type="runs_on",
            confidence="證據", evidence=evidence, source="derive:rvtools", source_key=vm_serial,
        )

        cluster = rec.get("cluster")
        if cluster:
            cluster_id = f"cluster:{cluster}"
            _upsert_node(
                conn, seen_nodes, node_id=cluster_id, node_type="cluster", label=str(cluster),
                asset_serial=None, source="derive:rvtools", source_key=str(cluster), attrs=None,
            )
            _upsert_edge(
                conn, seen_edges, src=esxi_id, dst=cluster_id, edge_type="member_of",
                confidence="證據", evidence=evidence, source="derive:rvtools", source_key=esxi_key,
            )

        datastore = rec.get("datastore")
        if datastore:
            ds_id = f"ds:{datastore}"
            _upsert_node(
                conn, seen_nodes, node_id=ds_id, node_type="datastore", label=str(datastore),
                asset_serial=None, source="derive:rvtools", source_key=str(datastore), attrs=None,
            )
            _upsert_edge(
                conn, seen_edges, src=f"hw:{vm_serial}", dst=ds_id, edge_type="stores_on",
                confidence="證據", evidence=evidence, source="derive:rvtools", source_key=vm_serial,
            )

    # ===== 3. systems/system_deps → sys 節點與邊（單向投影，永不回寫）=====
    sys_rows = conn.execute("SELECT id, label FROM systems").fetchall()
    for r in sys_rows:
        _upsert_node(
            conn, seen_nodes, node_id=f"sys:{r['id']}", node_type="system", label=r["label"],
            asset_serial=None, source="derive:systems", source_key=r["id"], attrs=None,
        )
    dep_rows = conn.execute("SELECT source, target, dep_type FROM system_deps").fetchall()
    for r in dep_rows:
        _upsert_edge(
            conn, seen_edges, src=f"sys:{r['source']}", dst=f"sys:{r['target']}",
            edge_type="depends_on", confidence="證據", evidence=r["dep_type"] or "system_deps 人工登記",
            source="derive:systems", source_key=f"{r['source']}->{r['target']}",
        )

    # ===== 4. 本輪沒再出現的 derive 列軟刪（manual 完全不碰，覆蓋原則的另一半）=====
    now = NOW()
    gone_count = 0
    for row in conn.execute(
        "SELECT node_id FROM ci_node WHERE source LIKE 'derive:%' AND manual_locked=0 "
        "AND gone_at IS NULL"
    ).fetchall():
        if row["node_id"] not in seen_nodes:
            conn.execute("UPDATE ci_node SET gone_at=? WHERE node_id=?", (now, row["node_id"]))
            gone_count += 1
    for row in conn.execute(
        "SELECT id, src_node_id, dst_node_id, edge_type FROM ci_edge "
        "WHERE source LIKE 'derive:%' AND manual_locked=0 AND gone_at IS NULL"
    ).fetchall():
        if (row["src_node_id"], row["dst_node_id"], row["edge_type"]) not in seen_edges:
            conn.execute("UPDATE ci_edge SET gone_at=? WHERE id=?", (now, row["id"]))
            gone_count += 1

    conn.commit()
    node_count = conn.execute("SELECT COUNT(*) FROM ci_node WHERE gone_at IS NULL").fetchone()[0]
    edge_count = conn.execute("SELECT COUNT(*) FROM ci_edge WHERE gone_at IS NULL").fetchone()[0]
    return {"node_count": node_count, "edge_count": edge_count, "gone_count": gone_count}


# ===== run 記錄與排程（切片 1 的收尾）=====
#
# 這一段是從 api.py 的 /api/ci/rebuild 端點抽出來的。原本 run 記錄（開 running 列、
# 成功寫 counts、失敗寫 error）整段寫在 HTTP 端點裡；排程要做同一件事，若在
# scan_service 再寫一份，兩份就會漂走——2026-08-19 的去識別化規則表就是這樣壞掉的
# （兩份複本，其中一份少一條規則，沒人發現）。所以先抽成函式，端點與排程共用。


class RebuildInProgress(RuntimeError):
    """已有一次重建在跑。呼叫端自行決定要 409 還是安靜跳過。"""


def reclaim_stale_runs(conn: sqlite3.Connection) -> int:
    """把遺留的 running 列收掉，回傳收了幾筆。

    為什麼需要：程序若在 rebuild 半途被砍（部署重啟、OOM、機器重開），那一列會
    **永遠**停在 running，之後每一次重建都被「已有一次重建正在進行」擋掉，而且
    畫面上看起來像是「一直在跑」——沒有任何東西會把它收掉。只有手動觸發時這還算
    少見；加了每天自動排程之後，這變成隨時可能踩到而且沒人會察覺的死結。

    收掉時標成 failed 並寫明原因，不是靜默刪除——「跑到一半被中斷」跟「沒跑過」
    是兩件不同的事，之後有人查為什麼圖譜沒更新，要看得到這一筆。
    """
    rows = conn.execute("SELECT id FROM ci_graph_runs WHERE status = 'running'").fetchall()
    if not rows:
        return 0
    conn.execute(
        "UPDATE ci_graph_runs SET status='failed', error=?, finished_at=? WHERE status='running'",
        ("上次重建未正常結束（程序中斷）；此列由啟動時的回收程序標記", NOW()),
    )
    conn.commit()
    return len(rows)


def run_rebuild(
    conn: sqlite3.Connection, trigger: str, triggered_by: str | None = None
) -> dict[str, Any]:
    """帶 run 記錄的重建。trigger = 'manual' | 'schedule'。

    已有 running 時丟 RebuildInProgress（不自己決定要不要等——端點要回 409，
    排程則是安靜跳過等下一輪，兩種處置不同）。
    """
    if conn.execute("SELECT id FROM ci_graph_runs WHERE status='running'").fetchone():
        raise RebuildInProgress("已有一次重建正在進行")

    run_id = conn.execute(
        "INSERT INTO ci_graph_runs (trigger, triggered_by, status) VALUES (?, ?, 'running')",
        (trigger, triggered_by),
    ).lastrowid
    conn.commit()
    try:
        result = rebuild(conn)
    except Exception as exc:  # noqa: BLE001 - 重建失敗要如實記錄，不吞
        conn.execute(
            "UPDATE ci_graph_runs SET status='failed', error=?, finished_at=? WHERE id=?",
            (str(exc), NOW(), run_id),
        )
        conn.commit()
        raise
    conn.execute(
        "UPDATE ci_graph_runs SET status='done', node_count=?, edge_count=?, gone_count=?, "
        "finished_at=? WHERE id=?",
        (result["node_count"], result["edge_count"], result["gone_count"], NOW(), run_id),
    )
    conn.commit()
    return {"run_id": run_id, **result}


# ---- 排程設定（存 app_settings，UI 可改、不用動主機）----
#
# 預設 03:00 而不是 00:00：圖譜的來源是 hardware 與 source_record，而這兩者由
# scan_service 的夜間掃描（預設 01:00）刷新，掃完後面還串了自動納管、服務採集、
# 帳號盤點。排在 00:00 等於拿前一天的舊資料重建，看起來有跑、內容卻是舊的——
# 這比沒跑更難察覺。要改時間請一併確認它仍晚於掃描那一串的結束時間。
_SCHED_DEFAULTS = {"ci_graph_enabled": "1", "ci_graph_time": "03:00"}


def get_schedule(conn: sqlite3.Connection) -> dict[str, Any]:
    from db import get_setting

    return {
        "enabled": get_setting(conn, "ci_graph_enabled", _SCHED_DEFAULTS["ci_graph_enabled"]) == "1",
        "time": get_setting(conn, "ci_graph_time", _SCHED_DEFAULTS["ci_graph_time"]),
    }


def set_schedule(conn: sqlite3.Connection, enabled: bool, time_str: str) -> None:
    from db import set_setting

    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(time_str)):
        raise ValueError("時間格式須為 HH:MM（24 小時制）")
    set_setting(conn, "ci_graph_enabled", "1" if enabled else "0")
    set_setting(conn, "ci_graph_time", str(time_str))


def is_due(conn: sqlite3.Connection, now: datetime) -> bool:
    """今天到點了、而且今天還沒被排程觸發過。判定方式照抄 scan_service._due。

    「今天還沒被排程觸發過」只看 trigger='schedule' 的列：使用者早上手動按過一次
    重建，不該讓當晚的排程被跳掉——那兩件事的目的不同（一個是臨時查證，一個是
    每天的定期刷新）。
    """
    sched = get_schedule(conn)
    if not sched["enabled"]:
        return False
    hh, mm = sched["time"].split(":")
    target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if now < target:
        return False
    row = conn.execute(
        "SELECT started_at FROM ci_graph_runs WHERE trigger='schedule' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None or not row["started_at"]:
        return True
    try:
        last = datetime.strptime(str(row["started_at"])[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return last.date() < now.date()
