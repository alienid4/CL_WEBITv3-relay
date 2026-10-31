"""排程盤點的 API（2026-10-20）。Linux 讀 OS 重建資料；Windows 走 WinRM 收（schedule_inventory）。

照 routers/ 的拆法用 _RouteCollector：api.py 只多一個模組名。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["host_schedule_get_endpoint", "host_schedule_collect_endpoint"]


@router.get("/api/host-schedule")
def host_schedule_get_endpoint(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """資產詳細頁「排程」分頁：已排序、已編號的排程列＋備註＋空白原因。"""
    import schedule_inventory
    return schedule_inventory.detail_for(conn, asset_serial)


@router.post("/api/host-schedule/collect")
def host_schedule_collect_endpoint(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """收這一台的排程。Windows：WinRM 唯讀列舉工作排程器；Linux 不另收（讀 OS 重建資料）。"""
    import schedule_inventory
    return schedule_inventory.collect_windows(conn, only_serial=asset_serial, trigger="manual")
