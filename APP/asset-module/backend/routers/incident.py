"""事件應變 v1 的 API（2026-10-23）。邏輯都在 incident.py，這裡只接線。

讀寫：登入即可（事故當下全隊一起改同一份；每次改動都進時間軸，誰／何時都留著）。結案後唯讀。
"""
from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()

__all__ = ["incident_list_endpoint", "incident_create_endpoint", "incident_view_endpoint",
           "incident_alerts_endpoint", "incident_scope_endpoint", "incident_check_endpoint",
           "incident_runs_endpoint", "incident_run_endpoint", "incident_task_endpoint",
           "incident_report_endpoint", "incident_note_endpoint", "incident_close_endpoint",
           "incident_timeline_endpoint", "incident_export_endpoint"]


def _user(session) -> str | None:
    try:
        return session["username"]
    except (KeyError, IndexError, TypeError):
        return None


def _call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except PermissionError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class IncidentBody(BaseModel):
    type: str
    title: str
    started_at: str | None = None
    note: str | None = None
    category: str = "incident"      # 2.x：planned＝計畫停機、incident＝異常事件
    mode: str | None = None         # 2.032：formal＝正式、drill＝演練；必選（沒給＝400）


class AlertsBody(BaseModel):
    text: str


class ScopeBody(BaseModel):
    scope: dict
    replace: bool = False


class TaskBody(BaseModel):
    team: str | None = None
    status: str | None = None
    note: str | None = None


class NoteBody(BaseModel):
    text: str


class CloseBody(BaseModel):
    summary: str


class ReportBody(BaseModel):
    every_min: int = 30


@router.get("/api/incidents")
def incident_list_endpoint(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import incident
    return incident.list_events(conn)


@router.post("/api/incidents")
def incident_create_endpoint(body: IncidentBody, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    import incident
    return _call(incident.create_event, conn, body.type, body.title, body.started_at, body.note, _user(session),
                 body.category, body.mode)


@router.get("/api/incidents/{eid}")
def incident_view_endpoint(eid: int, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    import incident
    return _call(incident.event_view, conn, eid)


@router.post("/api/incidents/{eid}/alerts")
def incident_alerts_endpoint(eid: int, body: AlertsBody, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    import incident
    return _call(incident.analyze_alerts, conn, eid, body.text, _user(session))


@router.put("/api/incidents/{eid}/scope")
def incident_scope_endpoint(eid: int, body: ScopeBody, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    import incident
    return _call(incident.set_scope, conn, eid, body.scope, _user(session), body.replace)


@router.post("/api/incidents/{eid}/check")
def incident_check_endpoint(eid: int, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    import incident
    return _call(incident.run_check, conn, eid, _user(session))


@router.get("/api/incidents/{eid}/runs")
def incident_runs_endpoint(eid: int, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    import incident
    return incident.list_runs(conn, eid)


@router.get("/api/incidents/runs/{rid}")
def incident_run_endpoint(rid: int, session: sqlite3.Row = Depends(require_auth),
                          conn: sqlite3.Connection = Depends(get_db)):
    import incident
    r = incident.get_run(conn, rid)
    if not r:
        raise HTTPException(404, "找不到這一輪測試")
    return r


@router.put("/api/incidents/tasks/{tid}")
def incident_task_endpoint(tid: int, body: TaskBody, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    import incident
    return _call(incident.update_task, conn, tid, _user(session), body.team, body.status, body.note)


@router.post("/api/incidents/{eid}/report")
def incident_report_endpoint(eid: int, body: ReportBody, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    import incident
    return _call(incident.make_report, conn, eid, _user(session), max(5, min(body.every_min, 240)))


@router.post("/api/incidents/{eid}/note")
def incident_note_endpoint(eid: int, body: NoteBody, session: sqlite3.Row = Depends(require_auth),
                           conn: sqlite3.Connection = Depends(get_db)):
    import incident
    _call(incident.add_note, conn, eid, body.text, _user(session))
    return {"ok": True}


@router.post("/api/incidents/{eid}/close")
def incident_close_endpoint(eid: int, body: CloseBody, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    import incident
    return _call(incident.close_event, conn, eid, body.summary, _user(session))


@router.get("/api/incidents/{eid}/timeline")
def incident_timeline_endpoint(eid: int, session: sqlite3.Row = Depends(require_auth),
                               conn: sqlite3.Connection = Depends(get_db)):
    import incident
    return incident.timeline(conn, eid)


@router.get("/api/incidents/{eid}/export.csv")
def incident_export_endpoint(eid: int, session: sqlite3.Row = Depends(require_auth),
                             conn: sqlite3.Connection = Depends(get_db)):
    import incident
    body = _call(incident.export_csv, conn, eid)
    return Response(content=body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="incident_{eid}.csv"'})
