"""Codex 21 號 #5：事件 mode（正式／演練）。可空欄位、歷史不改寫、舊庫可讀、冪等遷移、計數分開。"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import host_monthly  # noqa: E402
import incident as inc  # noqa: E402

OLD_DDL = """CREATE TABLE incident_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL, title TEXT NOT NULL, started_at TEXT NOT NULL, note TEXT,
    status TEXT NOT NULL DEFAULT 'open', scope_json TEXT, alerts_text TEXT,
    summary TEXT, created_by TEXT, created_at TEXT NOT NULL,
    closed_by TEXT, closed_at TEXT, next_report_at TEXT, category TEXT NOT NULL DEFAULT 'incident')"""


def _old_db(tmp_path):
    """部署前的舊庫：有 incident_event 與一筆歷史事件，但沒有 mode 欄位。"""
    c = sqlite3.connect(tmp_path / "old.db")
    c.row_factory = sqlite3.Row
    c.execute(OLD_DDL)
    c.execute("INSERT INTO incident_event (type,title,started_at,status,created_at) VALUES "
              "('電力','2026-10-23 UPS 演練？','2026-10-23 16:00:00','open','2026-10-23 16:00:00')")
    c.commit()
    return c


def _cols(c):
    return [r[1] for r in c.execute("PRAGMA table_info(incident_event)")]


def test_舊庫_讀取不崩_顯示未分類(tmp_path, monkeypatch):
    c = _old_db(tmp_path)
    assert "mode" not in _cols(c)
    # 模擬「新碼對尚未遷移的庫」：ensure_tables 不補欄位（例如唯讀連線），讀取路徑仍要用動態 SELECT 不炸
    monkeypatch.setattr(inc, "ensure_tables", lambda conn: None)
    assert inc._has_mode(c) is False
    evs = inc.list_events(c)
    assert len(evs) == 1 and evs[0]["mode"] is None and evs[0]["mode_label"] == inc.UNCLASSIFIED
    assert host_monthly.incidents(c, "2026-10")[0]["mode_label"] == inc.UNCLASSIFIED
    assert inc.get_event(c, evs[0]["id"])["mode"] is None
    assert "mode" not in _cols(c)                              # 沒被偷偷改


def test_舊schema升級_補欄位_歷史不改寫_重跑冪等(tmp_path):
    c = _old_db(tmp_path)
    inc.ensure_tables(c)
    assert "mode" in _cols(c)
    inc.ensure_tables(c)
    inc.ensure_tables(c)                                        # 冪等
    assert _cols(c).count("mode") == 1
    ev = inc.list_events(c)[0]
    assert ev["mode"] is None and ev["mode_label"] == "未分類（歷史）"       # 標題寫「演練？」也不自動分類
    assert c.execute("SELECT mode FROM incident_event").fetchone()[0] is None


def test_init_db_新庫有mode欄位(tmp_path):
    p = tmp_path / "n.db"
    db.init_db(p)
    c = db.get_connection(p)
    inc.ensure_tables(c)
    assert "mode" in _cols(c)


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def test_新事件缺mode或亂填_拒絕(conn):
    for bad in (None, "", "Formal", "test"):
        with pytest.raises(ValueError, match="正式或演練"):
            inc.create_event(conn, "主機", "x", None, None, "u", "incident", bad)
    assert inc.list_events(conn) == []                          # 沒有半個事件被寫進去


def test_新事件寫入mode_並進時間軸(conn):
    e = inc.create_event(conn, "主機", "演練 A", None, None, "u", "planned", "drill")
    assert e["mode"] == "drill" and e["mode_label"] == "演練"
    assert "演練" in inc.timeline(conn, e["id"])[0]["text"]


def test_計數_演練不混進正式_未分類獨立(conn):
    inc.ensure_tables(conn)
    conn.execute("INSERT INTO incident_event (type,title,started_at,status,created_at) VALUES "
                 "('電力','歷史','2026-01-01 00:00:00','open','2026-01-01 00:00:00')")
    conn.commit()
    inc.create_event(conn, "主機", "正式1", None, None, "u", mode="formal")
    inc.create_event(conn, "主機", "正式2", None, None, "u", mode="formal")
    inc.create_event(conn, "主機", "演練1", None, None, "u", mode="drill")
    evs = inc.list_events(conn)
    assert inc.mode_counts(evs) == {"formal": 2, "drill": 1, "unclassified": 1}
    assert inc.mode_counts(evs, open_only=True)["formal"] == 2
    assert sum(inc.mode_counts(evs).values()) == len(evs)       # 加總＝全部

    import v2_views
    todo = {t["key"]: t for t in v2_views.home(conn)["todo"]}
    assert todo["incident_open"]["n"] == 2                      # 只有正式，演練不混入
    assert todo["incident_open_unclassified"]["n"] == 1         # 未分類獨立計


def test_畫面_性質必選_列表可篩_預設全部():
    app = ROOT / "APP" / "asset-module" / "frontend" / "pages"
    vue = (app / "v2" / "incident" / "index.vue").read_text(encoding="utf-8")
    assert "未分類（歷史）" in vue
    assert "form.mode" in vue and "請先選擇事件性質" in vue
    assert "const modeFilter = ref<string>('all')" in vue         # 預設全部，不預設只看正式
    for lab in ("全部", "正式", "演練", "未分類"):
        assert lab in vue
    v1 = (app / "incident" / "index.vue").read_text(encoding="utf-8")
    assert "f.mode" in v1 and "請先選擇事件性質" in v1


def test_兩個首頁口徑一致_正式只算formal_未分類獨立_演練不列():
    fe = ROOT / "APP" / "asset-module" / "frontend"
    old = (fe / "pages" / "index.vue").read_text(encoding="utf-8")
    assert "x.mode === 'formal'" in old and "x.status === 'open' && x.mode !== 'drill')" not in old     # 不再把 NULL 混進正式
    assert "進行中的事件（未分類歷史）" in old
    v2 = (ROOT / "APP" / "asset-module" / "backend" / "v2_views.py").read_text(encoding="utf-8")
    assert "incident_open_unclassified" in v2 and 'oc["formal"]' in v2


def test_月報件數只算正式_演練與未分類分列(tmp_path):
    c = _old_db(tmp_path)                                         # 一筆歷史（未分類）
    inc.ensure_tables(c)
    inc.create_event(c, "主機", "正式", "2026-10-24 10:00", None, "u", mode="formal")
    inc.create_event(c, "主機", "演練", "2026-10-25 10:00", None, "u", mode="drill")
    rows = host_monthly.incidents(c, "2026-10")
    assert sorted((r["mode"] or "-") for r in rows) == ["-", "drill", "formal"]
    vue = (ROOT / "APP" / "asset-module" / "frontend" / "components" / "v2" / "HostMonthly.vue").read_text(encoding="utf-8")
    assert "r.mode === 'formal'" in vue and "不計入正式" in vue and "mode_label" in vue


def test_演練的通知信與回報文字明標(conn):
    import v2_incident
    e = inc.create_event(conn, "主機", "演練B", None, None, "u", mode="drill")
    f = inc.create_event(conn, "主機", "正式B", None, None, "u", mode="formal")
    assert "演練・非真實事件" in v2_incident.mail_draft(inc.event_view(conn, e["id"]))["subject"]
    assert "演練" not in v2_incident.mail_draft(inc.event_view(conn, f["id"]))["subject"]
    assert "演練・非真實事件" in inc.report_text(inc.event_view(conn, e["id"]))
    assert "演練" not in inc.report_text(inc.event_view(conn, f["id"]))


def test_沒有其他程式在建事件():
    """非 UI 呼叫端盤查：建事件只有 routers/incident.py 呼叫 create_event、incident.py 自己 INSERT（缺 mode→400）。"""
    import re
    back = ROOT / "APP" / "asset-module" / "backend"
    hits = sorted(str(p.relative_to(back)).replace("\\", "/") for p in back.rglob("*.py")
                  if re.search(r"incident\.create_event|INSERT INTO incident_event", p.read_text(encoding="utf-8")))
    assert hits == ["incident.py", "routers/incident.py"]
