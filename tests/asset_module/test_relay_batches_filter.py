"""2.066.30：relay-import／batches 加依來源（source）與日期（含時區，預設台北）過濾；不帶參數的舊呼叫行為不變。"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    ri.ensure(c)
    # imported_at／collected_at 都是台北時間文字；邊界：23:59:59／00:00:00 各一
    rows = [("whatsup", "w_a", "2026-11-02 23:59:59", "2026-11-02 23:50:00"),
            ("whatsup", "w_b", "2026-11-03 00:00:00", "2026-11-02 23:58:00"),
            ("aria_host", "a_a", "2026-11-03 08:00:00", "2026-11-03 07:55:00"),
            ("aria_host", "a_b", "2026-11-03 23:59:59", "2026-11-03 15:06:00"),
            ("aria_host", "a_c", "2026-11-04 00:00:00", "2026-11-04 00:00:00")]
    for src, bid, imp_at, col_at in rows:
        c.execute("INSERT INTO relay_import_batches (source, batch_id, status, imported_at, collected_at) VALUES (?,?,?,?,?)", (src, bid, "imported", imp_at, col_at))
    c.commit()
    yield c
    c.close()


def ids(rows):
    return sorted(r["batch_id"] for r in rows)


def test_不帶參數_跟以前一樣_全部且新到舊(conn):
    rows = ri.list_batches(conn)
    assert [r["batch_id"] for r in rows] == ["a_c", "a_b", "a_a", "w_b", "w_a"]
    assert ids(ri.list_batches(conn, "whatsup")) == ["w_a", "w_b"] and len(ri.list_batches(conn, None, 2)) == 2


def test_依來源(conn):
    assert ids(ri.list_batches(conn, source="aria_host")) == ["a_a", "a_b", "a_c"]
    assert ri.list_batches(conn, source="沒有這個") == []


def test_單日_台北時間_邊界_235959與000000(conn):
    assert ids(ri.list_batches(conn, date="2026-11-02")) == ["w_a"]                        # 23:59:59 算 11-02
    assert ids(ri.list_batches(conn, date="2026-11-03")) == ["a_a", "a_b", "w_b"]          # 00:00:00 算 11-03；23:59:59 仍是 11-03
    assert ids(ri.list_batches(conn, date="2026-11-04")) == ["a_c"]
    assert ri.list_batches(conn, date="2026-11-05") == []


def test_日期範圍_含頭尾_加來源(conn):
    assert ids(ri.list_batches(conn, date_from="2026-11-03", date_to="2026-11-04")) == ["a_a", "a_b", "a_c", "w_b"]
    assert ids(ri.list_batches(conn, source="aria_host", date_from="2026-11-03")) == ["a_a", "a_b", "a_c"]      # 只給起日
    assert ids(ri.list_batches(conn, date_to="2026-11-02")) == ["w_a"]                                          # 只給訖日
    assert ids(ri.list_batches(conn, source="whatsup", date_from="2026-11-03", date_to="2026-11-04")) == ["w_b"]


def test_依收集時間_by_collected(conn):
    assert ids(ri.list_batches(conn, date="2026-11-02", by="collected")) == ["w_a", "w_b"]       # 收集時間才是 11-02（w_b 是 23:58）
    assert ids(ri.list_batches(conn, date="2026-11-02", by="imported")) == ["w_a"]


def test_時區_UTC與正負8_換算成台北區間(conn):
    # UTC 11-03 ＝ 台北 11-03 08:00 ～ 11-04 08:00
    assert ids(ri.list_batches(conn, date="2026-11-03", tz="UTC")) == ["a_a", "a_b", "a_c"]
    assert ids(ri.list_batches(conn, date="2026-11-03", tz="+08:00")) == ids(ri.list_batches(conn, date="2026-11-03", tz="Asia/Taipei"))
    assert ids(ri.list_batches(conn, date="2026-11-03", tz=" 08:00")) == ids(ri.list_batches(conn, date="2026-11-03"))   # 網址裡 + 變空白
    # 台北前一天晚上 8 點起算（-04:00 的 11-03 ＝ 台北 11-03 12:00 ～ 11-04 12:00）
    assert ids(ri.list_batches(conn, date="2026-11-03", tz="-04:00")) == ["a_b", "a_c"]


@pytest.mark.parametrize("kw", [{"date": "2026/11/03"}, {"date": "abc"}, {"date_from": "2026-11-05", "date_to": "2026-11-03"},
                                {"date": "2026-11-03", "date_from": "2026-11-03"}, {"date": "2026-11-03", "tz": "Mars/Base"},
                                {"date": "2026-11-03", "tz": "+25:00"}, {"date": "2026-11-03", "by": "x"}, {"date": "2026-02-30"}])
def test_壞參數_丟ValueError_不猜(conn, kw):
    with pytest.raises(ValueError):
        ri.list_batches(conn, **kw)


def test_API_過濾參數與壞值422_舊呼叫不變():
    from test_relay_import import _api
    qw.set_clock(lambda: __import__("datetime").datetime(2026, 11, 7, 10, 0))
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            client, p = _api(tmp, [("admin", "admin"), ("vera", "viewer")])
            cn = db.get_connection(p)
            ri.ensure(cn)
            for src, bid, imp_at in (("whatsup", "w1", "2026-11-02 10:00:00"), ("aria_host", "a1", "2026-11-03 10:00:00")):
                cn.execute("INSERT INTO relay_import_batches (source, batch_id, status, imported_at) VALUES (?,?,?,?)", (src, bid, "imported", imp_at))
            cn.commit()
            cn.close()
            vera = client("vera")
            assert len(vera.get("/api/v2/relay-import/batches").json()["rows"]) == 2
            r = vera.get("/api/v2/relay-import/batches", params={"source": "aria_host", "date": "2026-11-03", "tz": "Asia/Taipei"})
            assert r.status_code == 200 and [x["batch_id"] for x in r.json()["rows"]] == ["a1"]
            assert vera.get("/api/v2/relay-import/batches", params={"date": "2026-11-02"}).json()["rows"][0]["batch_id"] == "w1"
            assert vera.get("/api/v2/relay-import/batches", params={"date": "亂寫"}).status_code == 422
            assert vera.get("/api/v2/relay-import/batches", params={"date": "2026-11-03", "tz": "Mars"}).status_code == 422
    finally:
        qw.set_clock(None)
        import api
        api.app.dependency_overrides.clear()
