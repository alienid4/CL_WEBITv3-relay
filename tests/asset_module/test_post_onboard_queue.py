"""多台排隊收六樣（2026-10-20）：兩個入口共用 post_onboard.enqueue。

1. 2-1「開始收集」第一次把一台收成功 → 自動收一輪六樣（以前只有其他納管入口會收）。
   只有第一次：已經有 post_onboard 紀錄的台，重跑收集不再重收。
2. 2-1「已納管主機」勾選多台 →「收集全部」。

都不能一次轟一大批（一條執行緒逐台）、單台出事不能拖垮整批或拖垮收集本身。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_dispatch as cd  # noqa: E402
import collect_log  # noqa: E402
import db  # noqa: E402
import post_onboard  # noqa: E402

PORTS = {"10.99.0.1": [22], "10.99.0.2": [22], "10.99.0.3": [22]}


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn, ip, serial):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, environment="正式", asset_status="使用中")


def _run(conn, ips, enq):
    return cd.run_dispatch(conn, ips, prober=lambda ip: PORTS.get(ip),
                           ssh_prober=lambda ip: (True, None), post_onboard_enqueue=enq)


def test_開始收集第一次收成功的台_排進六樣收集():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "10.99.0.1", "SN-1")
        _seed(conn, "10.99.0.2", "SN-2")
        calls = []
        _run(conn, "10.99.0.1 10.99.0.2 10.99.0.3",
             lambda db_path, serials, by=None, source=None: calls.append((list(serials), source)))
        assert calls == [(["SN-1", "SN-2"], "onboard")], "沒登記的 10.99.0.3 沒有序號，不排"
        conn.close()


def test_已經收過六樣的台_重跑收集不再重收():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "10.99.0.1", "SN-1")
        _seed(conn, "10.99.0.2", "SN-2")
        collect_log.record(conn, "post_onboard", "SN-1", True, "納管後自動收集：收到 全部")
        # 另一台只有用 IP 記過（批次納管那條是用 IP 當 target）也算收過
        collect_log.record(conn, "post_onboard", "10.99.0.2", False, "沒收到 帳號")
        calls = []
        _run(conn, "10.99.0.1 10.99.0.2",
             lambda db_path, serials, by=None, source=None: calls.append(list(serials)))
        assert calls == []
        conn.close()


def test_排隊那步炸掉不影響這次收集_而且留紀錄():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "10.99.0.1", "SN-1")

        def boom(*a, **k):
            raise RuntimeError("queue down")

        out = _run(conn, "10.99.0.1", boom)
        assert out["results"][0]["status"] == cd.STATUS_COLLECTED
        msgs = [r["message"] for r in collect_log.recent(conn, kinds=["post_onboard"])]
        assert any("queue down" in m for m in msgs), "排不進去不可以靜默"
        conn.close()


def test_注入假收集器又沒指定排隊函式時_不會去跑真的六樣():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "10.99.0.1", "SN-1")
        before = post_onboard.queue_status()["total"]
        cd.run_dispatch(conn, "10.99.0.1", prober=lambda ip: PORTS.get(ip),
                        ssh_prober=lambda ip: (True, None))
        assert post_onboard.queue_status()["total"] == before
        conn.close()


# ── 隊伍本身 ──

def _reset_queue():
    with post_onboard._q_lock:
        post_onboard._queue.clear()
        post_onboard._queued.clear()
        post_onboard._q_state.update(running=False, total=0, done=0, ok=0, failed=0, deferred=0,
                                     current=None)


def test_隊伍逐台跑_單台失敗不拖垮其他台_去重():
    _reset_queue()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        seen = []
        live = {"now": 0, "max": 0}

        def runner(conn, serial, by, source):
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
            seen.append(serial)
            live["now"] -= 1
            return {"A": "ok", "B": "failed", "C": "deferred"}[serial]

        out = post_onboard.enqueue(p, ["A", "B", "A", "C", ""], by="tester", source="manual",
                                   _runner=runner, _sync=True)
        assert out["added"] == 3
        st = post_onboard.queue_status()
        assert seen == ["A", "B", "C"], "照勾選順序、重複的只跑一次"
        assert live["max"] == 1, "一次只跑一台"
        assert (st["total"], st["done"], st["ok"], st["failed"], st["deferred"]) == (3, 3, 1, 1, 1)
        assert st["running"] is False and st["pending"] == 0


def test_隊伍裡某台整輪炸掉_記一筆失敗紀錄後繼續下一台(monkeypatch):
    _reset_queue()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        ran = []

        def fake_exclusive(conn, serial, by=None, source="onboard"):
            ran.append(serial)
            if serial == "BAD":
                raise RuntimeError("kaboom")
            return [{"job": "spec", "label": "硬體規格", "ok": True, "message": ""}]

        monkeypatch.setattr(post_onboard, "run_exclusive", fake_exclusive)
        post_onboard.enqueue(p, ["BAD", "GOOD"], by="tester", source="manual", _sync=True)
        assert ran == ["BAD", "GOOD"]
        st = post_onboard.queue_status()
        assert (st["ok"], st["failed"]) == (1, 1)
        conn = db.get_connection(p)
        msgs = [r["message"] for r in collect_log.recent(conn, kinds=["post_onboard"])]
        conn.close()
        assert any("kaboom" in m and "手動收集全部" in m for m in msgs)


# ── API ──

_PW = "test-password-123"


def _client(tmp):
    from fastapi.testclient import TestClient

    import api
    import auth

    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)

    def _override_get_db():
        c = db.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    client = TestClient(api.app)
    c = db.get_connection(db_path)
    try:
        db.create_user(c, "tester", auth.hash_password(_PW))
    finally:
        c.close()
    assert client.post("/api/auth/login", json={"username": "tester", "password": _PW}).status_code == 200
    return client, db_path


def test_收集全部端點_排進隊伍_不存在的序號另外列出(monkeypatch):
    import api

    captured = {}

    def fake_enqueue(db_path, serials, by=None, source="onboard", **k):
        captured.update(serials=list(serials), by=by, source=source)
        return {"added": len(serials), "already": 0, **post_onboard.queue_status()}

    monkeypatch.setattr(post_onboard, "enqueue", fake_enqueue)
    with tempfile.TemporaryDirectory() as tmp:
        client, p = _client(tmp)
        try:
            c = db.get_connection(p)
            _seed(c, "10.99.0.1", "SN-1")
            c.close()
            r = client.post("/api/onboard/post-collect-batch",
                            json={"asset_serials": ["SN-1", "NOPE", "SN-1"]})
            assert r.status_code == 200, r.text
            assert captured == {"serials": ["SN-1"], "by": "tester", "source": "manual"}
            assert r.json()["missing"] == ["NOPE"]
            assert client.post("/api/onboard/post-collect-batch",
                               json={"asset_serials": []}).status_code == 400
            q = client.get("/api/onboard/post-collect-queue")
            assert q.status_code == 200 and "total" in q.json() and len(q.json()["jobs"]) == len(post_onboard.JOBS)
        finally:
            api.app.dependency_overrides.clear()


def test_隊伍收到字串路徑也開得了庫():
    """開始收集傳來的是 PRAGMA database_list 給的字串路徑；get_connection 只吃 Path，
    沒轉的話隊伍一開就 AttributeError、整批默默不見（實際踩過，這條釘住）。"""
    _reset_queue()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        post_onboard.enqueue(str(p), ["A"], source="manual",
                             _runner=lambda c, s, b, src: "ok", _sync=True)
        st = post_onboard.queue_status()
        assert st["error"] is None and (st["done"], st["ok"]) == (1, 1)


def test_沒東西可排時不清掉上一批的進度():
    _reset_queue()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        post_onboard.enqueue(p, ["A", "B"], source="manual", _runner=lambda c, s, b, src: "ok", _sync=True)
        out = post_onboard.enqueue(p, [], source="manual", _runner=lambda c, s, b, src: "ok", _sync=True)
        assert out["added"] == 0
        st = post_onboard.queue_status()
        assert (st["total"], st["done"], st["ok"]) == (2, 2, 2)
