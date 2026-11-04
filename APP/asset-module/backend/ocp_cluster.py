"""OCP 叢集的人工指定（2026-09-20 使用者：「都沒有 → 標『未分群』，不猜，可由人類自己更換群組」）。

自動分群只認兩種證據：FQDN（機器自己報的名字，最準）、用途／名稱裡的 paas-xxx（人填的）。
兩者都沒有就標「未分群」——**不用機房或網段硬湊**，猜錯會讓人以為兩座叢集是同一座。
判不出來的那些由人自己指定，指定值永遠優先，而且畫面要標「人工指定」與是誰指定的。

⚠️ 指定的是**整台**：同一台被登記好幾筆（FQDN 一筆、短名一筆）時，改一筆就等於改那一台，
不然畫面上同一個節點會一半在 A 叢集、一半在未分群。
"""
from __future__ import annotations

import datetime

_DDL = """
CREATE TABLE IF NOT EXISTS ocp_cluster_override (
    asset_serial TEXT PRIMARY KEY,
    cluster      TEXT NOT NULL,
    reason       TEXT,
    updated_by   TEXT,
    updated_at   TEXT
)
"""


# 2026-09-21 使用者：「我要怎麼定義角色」「怎麼編輯」——角色本來只能靠關鍵字猜，
# 猜不出來就永遠卡在「未標示」，人沒有地方可以訂正。跟叢集同一張表、同一套規矩：
# 指定值永遠優先、改的是整台、畫面要標明是人工指定與是誰改的。
ROLES = ("Master", "Infra", "Worker", "Bootstrap")


def _ensure(conn) -> None:
    conn.execute(_DDL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ocp_cluster_override)")}
    if "role" not in cols:
        conn.execute("ALTER TABLE ocp_cluster_override ADD COLUMN role TEXT")
    # 舊庫的 cluster 是 NOT NULL，只指定角色、沒指定叢集時寫空字串（不是 NULL）。


def active_map(conn) -> dict[str, dict]:
    """asset_serial → 指定內容。讀不到（唯讀庫／舊庫）就當沒有指定，不擋整頁。"""
    try:
        _ensure(conn)
        return {r["asset_serial"]: dict(r)
                for r in conn.execute("SELECT * FROM ocp_cluster_override")}
    except Exception:  # noqa: BLE001
        return {}


def set_cluster(conn, asset_serial: str, cluster: str, reason: str = "",
                by: str | None = None) -> dict:
    """指定（或清除）某一台的叢集。cluster 空字串＝清除指定、回到自動判斷。"""
    if not (asset_serial or "").strip():
        raise ValueError("要指定是哪一台")
    import manage_state

    _ensure(conn)
    serials = manage_state.expand_to_machines(conn, [asset_serial]) or [asset_serial]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cluster = (cluster or "").strip()
    if not cluster:
        ph = ",".join("?" for _ in serials)
        conn.execute(f"DELETE FROM ocp_cluster_override WHERE asset_serial IN ({ph})", serials)
        conn.commit()
        return {"cleared": True, "serials": serials}
    for s in serials:
        conn.execute(
            "INSERT INTO ocp_cluster_override (asset_serial, cluster, reason, updated_by, updated_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(asset_serial) DO UPDATE SET cluster = excluded.cluster, "
            "reason = excluded.reason, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (s, cluster, (reason or "").strip(), by, now))
    conn.commit()
    return {"cluster": cluster, "serials": serials, "updated_by": by, "updated_at": now}


def set_role(conn, asset_serial: str, role: str, reason: str = "",
             by: str | None = None) -> dict:
    """指定（或清除）某一台的角色。role 空字串＝清除、回到自動判斷。

    清除時不能把整列刪掉——那一台可能還有人工指定的叢集，
    刪整列等於順手把叢集指定也抹了（使用者看到的會是「我只改角色，叢集怎麼跡了」）。
    """
    if not (asset_serial or "").strip():
        raise ValueError("要指定是哪一台")
    import manage_state

    _ensure(conn)
    role = (role or "").strip()
    if role and role not in ROLES:
        raise ValueError(f"角色只能是：{'、'.join(ROLES)}")
    serials = manage_state.expand_to_machines(conn, [asset_serial]) or [asset_serial]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for s in serials:
        conn.execute(
            "INSERT INTO ocp_cluster_override (asset_serial, cluster, role, reason, updated_by, updated_at) "
            "VALUES (?,'',?,?,?,?) ON CONFLICT(asset_serial) DO UPDATE SET role = excluded.role, "
            "reason = excluded.reason, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (s, role, (reason or "").strip(), by, now))
    # 叢集跟角色都清了的那一列才真的刪掉，不要留一堆空記錄
    conn.execute("DELETE FROM ocp_cluster_override "
                 "WHERE COALESCE(cluster,'') = '' AND COALESCE(role,'') = ''")
    conn.commit()
    return {"role": role, "serials": serials, "updated_by": by, "updated_at": now}
