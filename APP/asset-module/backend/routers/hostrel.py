"""主機關聯探索 API（工作包 B，只讀；功能旗標 host_relations 預設關）。

  GET /api/host-relations/neighbors   以一個節點為起點回一跳鄰居（分頁、snapshot、證據）

規格與契約：docs/host-relations/。不連線、不收集、不寫庫。
"""
from __future__ import annotations

import uuid

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["host_relations_neighbors_endpoint"]

_ALLOWED_PARAMS = {"node_id", "direction", "limit", "cursor", "snapshot_id", "protocol", "port", "evidence_kind"}


@router.get("/api/host-relations/neighbors")
def host_relations_neighbors_endpoint(
    request: Request,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """一跳鄰居。參數：node_id（必填）、direction＝outbound|inbound|both、limit≤50、cursor、snapshot_id、
    protocol＝tcp、port（可重複）、evidence_kind＝observed_socket。不認得的參數回 422。"""
    import host_relations as hr

    q = request.query_params
    rid = uuid.uuid4().hex[:12]
    try:
        unknown = sorted(set(q.keys()) - _ALLOWED_PARAMS)
        if unknown:
            raise hr.BadParam("不認得的參數")
        for k in q.keys():
            if k != "port" and len(q.getlist(k)) > 1:
                raise hr.BadParam("參數重複")
        node_id = q.get("node_id")
        if not node_id:
            raise hr.BadParam("缺少 node_id")
        try:
            limit = int(q.get("limit", hr.DEFAULT_LIMIT))
            ports = [int(p) for p in q.getlist("port")]
        except ValueError as exc:
            raise hr.BadParam("limit／port 要是整數") from exc
        filters = hr.normalize_filters(q.get("protocol"), ports, q.get("evidence_kind"))
        return hr.neighbors(conn, node_id, q.get("direction", "outbound"), limit,
                            q.get("cursor"), q.get("snapshot_id"), filters, request_id=rid)
    except hr.RelationError as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message, "request_id": rid}) from exc
