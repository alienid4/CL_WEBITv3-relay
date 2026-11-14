"""Codex 57：事件 GET 路徑不得遷移。舊結構（缺 category／mode）＋ query_only=ON 下，原函式不 ALTER、不拋錯。
不 mock ensure_tables。"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))
import incident  # noqa: E402


def _old_db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE incident_event (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, title TEXT NOT NULL,
        started_at TEXT NOT NULL, note TEXT, status TEXT NOT NULL DEFAULT 'open', scope_json TEXT, alerts_text TEXT,
        summary TEXT, created_by TEXT, created_at TEXT NOT NULL, closed_by TEXT, closed_at TEXT, next_report_at TEXT)""")
    c.execute("""CREATE TABLE incident_timeline (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
        at TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, created_by TEXT)""")
    c.execute("INSERT INTO incident_event (type,title,started_at,created_at) VALUES ('t','舊事件','2026-01-01 00:00:00','2026-01-01 00:00:00')")
    c.commit()
    return c


def _cols(c):
    return [r[1] for r in c.execute("PRAGMA table_info(incident_event)")]


def test_舊結構唯讀庫_list_get_timeline_不遷移不報錯():
    c = _old_db()
    before = _cols(c)
    c.execute("PRAGMA query_only=ON")
    ev = incident.list_events(c)
    assert len(ev) == 1 and ev[0]["category"] == "incident" and ev[0]["mode"] is None
    assert ev[0]["mode_label"] == incident.UNCLASSIFIED
    d = incident.get_event(c, ev[0]["id"])
    assert d["category"] == "incident" and d["mode"] is None
    assert incident.timeline(c, d["id"]) == []
    assert incident.list_tasks(c, d["id"]) == [] and incident.list_runs(c, d["id"]) == [] and incident.last_run(c, d["id"]) is None
    assert _cols(c) == before                      # 結構沒被改


def test_沒有任何事件表_讀取回空不建表():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    assert incident.list_events(c) == []
    assert incident.timeline(c, 1) == []
    try:
        incident.get_event(c, 1)
        raise AssertionError("應拋 LookupError")
    except LookupError:
        pass
    assert c.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0


def test_寫入入口仍會遷移():
    c = _old_db()
    incident.create_event(c, incident.TYPES[0], "新", None, None, "u", mode="formal")
    assert "mode" in _cols(c) and "category" in _cols(c)
    eid = c.execute("SELECT id FROM incident_event WHERE title='新'").fetchone()[0]
    c2 = _old_db()
    incident.add_note(c2, 1, "備註", "u")          # 經 _writable 也會補欄位
    assert "mode" in _cols(c2)
    assert incident.get_event(c, eid)["mode"] == "formal"


def test_首頁與數字核對讀取路徑不呼叫_ensure_tables():
    """審查抓到：v2_views.home／verify_numbers 曾在讀取路徑呼叫 incident.ensure_tables（等於 GET／部署關卡在寫庫）。"""
    base = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
    for name in ("v2_views.py", "verify_numbers.py", "v2_incident.py", "host_monthly.py"):
        assert "incident.ensure_tables" not in (base / name).read_text(encoding="utf-8"), name


def test_事件詳情_匯出_通知_在唯讀連線與位元組不變下可用(tmp_path):
    """Codex 57／015 剩餘：World→rack_layout.machines→model_specs 原本會建表／塞種子／標已同步。
    現在以 mode=ro 唯讀連線開同一個庫，event_view／export_csv／v2 通知與聯絡人匯出都能跑，且庫檔位元組不變。"""
    import hashlib
    import db
    import v2_incident

    p = tmp_path / "t.db"
    db.init_db(p)
    w = db.get_connection(p)
    db.insert_hardware(w, asset_serial="FX-1", hostname="fx-h1", ip="192.0.2.5", device_model="Fixture Model",
                       physical_location="01_Fixture Room", rack_no="A1/5-6U", api_id="FX-001")
    w.commit()
    ev = incident.create_event(w, incident.TYPES[0], "唯讀驗證", None, None, "u", mode="drill")
    w.close()
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    ro = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    ro.execute("PRAGMA query_only=ON")
    v = incident.event_view(ro, ev["id"])
    assert v["event"]["id"] == ev["id"]
    assert incident.export_csv(ro, ev["id"])
    assert incident.list_events(ro)
    for fn in ("notify", "contacts_csv"):
        if hasattr(v2_incident, fn):
            getattr(v2_incident, fn)(ro, ev["id"])
    ro.close()
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before      # 沒寫任何東西
