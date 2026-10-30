"""OS 重建資料的 API（2026-10-16 機房搬遷，Q78）。

照 routers/ 的拆法用 _RouteCollector：api.py 把收集到的路由原樣轉成 app.<method>，
所以 app.routes 是扁平的（test_auth_coverage／test_frontend_api_paths 都數得到）。
api.py 只多一個模組名，不再長大。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["host_rebuild_collect_endpoint", "host_rebuild_get_endpoint",
           "host_rebuild_sudo_whitelist_endpoint", "detail_for"]


@router.post("/api/host-spec/rebuild-collect")
def host_rebuild_collect_endpoint(
    asset_serial: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """收 OS 重建資料（Linux 走 SSH、Windows 走 WinRM；AIX 單獨一批；ESXi 一律不收）。唯讀、走收集金鑰、不改設定。
    跟「收集規格」分開一顆按鈕：這批較重、是搬遷前一次性快照。
    傳 asset_serial＝只收這一台；不傳＝全部已納管 Linux／Windows。"""
    import os_rebuild_collector
    return os_rebuild_collector.run_collection(conn, trigger="manual", only_serial=asset_serial)


@router.get("/api/host-spec/rebuild")
def host_rebuild_get_endpoint(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """資產詳細頁「OS 重建」分頁的資料。沒收過回 {"host_rebuild": null}。"""
    return {"host_rebuild": detail_for(conn, asset_serial)}


@router.get("/api/host-spec/rebuild-sudo-whitelist")
def host_rebuild_sudo_whitelist_endpoint(session: sqlite3.Row = Depends(require_auth)):
    """這個收集器會用到的 sudo 指令清單（給資安審；不連任何主機）。"""
    import os_rebuild_collector
    return {"items": os_rebuild_collector.sudo_whitelist()}


def detail_for(conn: sqlite3.Connection, asset_serial: str) -> dict | None:
    """資產詳細頁要的重建摘要：分組項目＋收集時間。沒收過回 None。raw_json 不外送（量大）。"""
    try:
        row = conn.execute("SELECT asset_serial, platform, data_json, collected_at "
                           "FROM host_rebuild WHERE asset_serial = ?", (asset_serial,)).fetchone()
        if not row:
            # 同一台（machine_key）的其他登記收過就用那份——收集器是「整台」收、存在 collect_ok=1
            # 那筆底下；從同一台的另一筆點進來不可以顯示「尚未收集」（2026-10-18）。
            import manage_state
            sibs = [x for x in manage_state.expand_to_machines(conn, [asset_serial])
                    if x != asset_serial]
            if sibs:
                ph = ",".join("?" for _ in sibs)
                row = conn.execute(
                    "SELECT asset_serial, platform, data_json, collected_at FROM host_rebuild "
                    f"WHERE asset_serial IN ({ph}) ORDER BY collected_at DESC LIMIT 1",
                    sibs).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    out = dict(row)
    try:
        out["data"] = json.loads(out.pop("data_json") or "{}")
    except (ValueError, TypeError):
        out["data"] = None
    return out
