"""對帳橋（7 資料品質，2026-10-26）的 API。邏輯都在 count_bridge.py，這裡只接線。

讀：登入即可。人工指定角色：限管理員（跟選單同一個判定 _can_edit_nav），每次都寫稽核紀錄。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["count_bridge_endpoint", "count_bridge_items_endpoint", "count_bridge_override_endpoint",
           "count_bridge_audit_endpoint", "count_bridge_recon_endpoint"]


class CountRoleOverrideBody(BaseModel):
    asset_serial: str
    role: str | None = None      # None＝清除人工指定、回到自動判斷
    note: str | None = None


@router.get("/api/count-bridge")
def count_bridge_endpoint(session: sqlite3.Row = Depends(require_auth),
                          conn: sqlite3.Connection = Depends(get_db)):
    """每個角色的 CIA 筆／帳外筆／台（部／個）＋三個候選頭條（試算）。每一格都能用 /items 下鑽。"""
    import count_bridge

    out = count_bridge.compute(conn)
    return {**out["summary"], "count_threshold": out["count_threshold"],
            "existence_counted_in_db": out["existence_counted_in_db"], "as_of": out["as_of"],
            "invalid_overrides": out["invalid_overrides"],
            "roles_all": [{"role": k, "label": lab} for k, lab, *_ in count_bridge.ROLES],
            "can_edit": _can_edit_nav(session, conn)}


@router.get("/api/count-bridge/items")
def count_bridge_items_endpoint(role: str | None = None, side: str | None = None,
                                measure: str | None = None, counted: bool | None = None,
                                session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    """下鑽：role＝角色、side＝CIA／帳外、measure＝rack／unparsed／none（只看有機櫃／機櫃欄認不出／缺機櫃資料的）、
    counted＝存在可信度達門檻。"""
    import count_bridge

    out = count_bridge.compute(conn)
    try:
        items = count_bridge.filter_items(out["items"], role, side, measure, counted)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"total": len(items), "frames": len({x["frame"] for x in items}), "items": items}


@router.put("/api/count-bridge/override")
def count_bridge_override_endpoint(body: CountRoleOverrideBody, request: Request,
                                   session: sqlite3.Row = Depends(require_auth),
                                   conn: sqlite3.Connection = Depends(get_db)):
    """人工指定一筆的角色（邊角案例用）。存自己的表，重匯 CIA 不會蓋掉。
    寫入關卡跟 4-5 拖拉搬移／復原同一支（routers.overlaps._guard：登入停用時要管理密碼）。"""
    import count_bridge
    from routers.overlaps import _actor, _guard

    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員（admin 帳號）能改分類")
    _guard(conn, request)
    by = _actor(session, request)
    try:
        return count_bridge.set_override(conn, body.asset_serial, body.role, body.note, by)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/count-bridge/override-audit")
def count_bridge_audit_endpoint(limit: int = 200, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    import count_bridge

    return {"items": count_bridge.override_audit(conn, limit)}


@router.get("/api/count-bridge/recon")
def count_bridge_recon_endpoint(bucket: str | None = None, session: sqlite3.Row = Depends(require_auth),
                                conn: sqlite3.Connection = Depends(get_db)):
    """對帳（只拿這次掃描有涵蓋的網段當分母）：一致／搜不到／未涵蓋，逐台、下鑽同一份。"""
    import count_bridge

    out = count_bridge.recon_coverage(conn)
    if bucket and bucket not in count_bridge.RECON_BUCKETS:
        raise HTTPException(400, f"bucket 只接受 {'／'.join(count_bridge.RECON_BUCKETS)}")
    items = [x for x in out["items"] if not bucket or x["bucket"] == bucket]
    return {**{k: v for k, v in out.items() if k != "items"}, "labels": count_bridge.RECON_LABEL,
            "bucket": bucket, "items": items}
