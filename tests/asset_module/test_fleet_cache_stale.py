"""主機履歷頁的「限時舊值」層（fleet_cache.stale_ok，2.069.4）。

背景：正式機收集每分鐘都在寫庫，fleet_cache 的跨請求指紋一直變、永遠命不中，開一次主機頁要把全機隊算 9 種各一遍
（6 千台量測：summary 約 6 秒、profile 約 3.5 秒）。主機頁端點明確選擇「接受最多 N 秒前算好的機隊結果」，並在回應標示。

釘住的是「不可顯示舊數字當新數字」：
- 只有 stale_ok() 範圍內才用舊值；平常的路徑一個字都不變（寫入後下一次一定重算）
- 超過期限、有人改過東西（非 GET 請求）、設定／口徑變了、換資料庫、這條連線自己寫過 → 一律即時重算
- 算到一半有人改了東西 → 不把「改之前」的結果存成新的
- 回應有用到舊值才帶 fleet_as_of／fleet_age_s；沒用到時回應與以前完全一樣
- 背景重算（refresh_hot）只重算最近有人看的鍵，且重算出來的是「現在的」資料
"""
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import fleet_cache  # noqa: E402

_SEQ = {"n": 0}


def _db(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    w = db.get_connection(p)
    db.insert_hardware(w, asset_serial="S-1", hostname="h1", ip="10.99.1.1", os="RHEL 8")
    w.commit()
    w.close()
    return p


def _other_write(p):
    """別的連線（收集、匯入…）寫入一筆並提交。寫完等一下：指紋看 -wal 的修改時間（粒度約 10ms）。"""
    _SEQ["n"] += 1
    w = db.get_connection(p)
    db.insert_hardware(w, asset_serial="S-w%d" % _SEQ["n"], hostname="hx%d" % _SEQ["n"], ip="10.99.9.%d" % _SEQ["n"], os="RHEL 8")
    w.commit()
    w.close()
    time.sleep(0.25)


def _counter(name="count_hw"):
    calls = {"n": 0}

    @fleet_cache.cached
    def count_hw(conn):
        calls["n"] += 1
        return {"n": conn.execute("SELECT count(*) FROM hardware").fetchone()[0], "rows": [1, 2]}

    return count_hw, calls


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("WEBIT3_HOST_PAGE_STALE_S", "120")      # 預設是關閉（0），這個檔案要測「開啟時」的行為，所以明確打開
    monkeypatch.delenv("WEBIT3_FLEET_REFRESH", raising=False)
    monkeypatch.delenv("ASSET_SCHEDULER", raising=False)
    fleet_cache.clear()
    yield
    fleet_cache.clear()


def _req(p, fn, stale=True, max_age=None):
    """模擬一個請求：新連線＋請求範圍（＋限時舊值）。回 (結果, 報告)。"""
    c = db.get_connection(p)
    try:
        if stale:
            with fleet_cache.stale_ok(max_age) as rep:
                with fleet_cache.request_scope(c):
                    return fn(c), rep
        with fleet_cache.request_scope(c):
            return fn(c), None
    finally:
        c.close()


def test_別人一直在寫_主機頁第二次吃舊值_不重算_並標示():
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        fn, calls = _counter()
        a, rep1 = _req(p, fn)
        assert rep1["used"] == 0 and calls["n"] == 1
        _other_write(p)                                   # 收集在寫：跨請求指紋變了
        b, rep2 = _req(p, fn)
        assert calls["n"] == 1, "限時舊值內不該重算"
        assert rep2["used"] == 1 and rep2["oldest_built"] is not None and rep2["max_age_s"] >= 0
        assert b == a, "舊值＝上次算好的那一份"


def test_沒有選擇限時舊值的路徑_寫入後一定重算_行為不變():
    """只有 stale_ok() 範圍內才用舊值；其他請求（清單頁、驗算、命令列）照舊。改回「一律用舊值」就會紅。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        fn, calls = _counter()
        _req(p, fn)                                       # 主機頁先存了一份舊值
        _other_write(p)
        r, _ = _req(p, fn, stale=False)
        assert calls["n"] == 2, "一般請求不可以拿到限時舊值"
        assert r["n"] == 2
        # 完全沒有範圍的呼叫也一樣
        _other_write(p)
        c = db.get_connection(p)
        assert fn(c)["n"] == 3 and calls["n"] == 3
        c.close()


def test_超過期限就即時重算_拿到的是新資料():
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        fn, calls = _counter()
        _req(p, fn, max_age=0.3)
        _other_write(p)                                   # 已睡 0.25 秒
        time.sleep(0.2)                                   # 合計超過 0.3 秒
        r, rep = _req(p, fn, max_age=0.3)
        assert calls["n"] == 2 and rep["used"] == 0
        assert r["n"] == 2, "過期後要看到新寫入的資料"


def test_有人改了東西_非GET請求_舊值全部作廢():
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        fn, calls = _counter()
        _req(p, fn)
        _other_write(p)
        fleet_cache.invalidate_recent()                   # api.py 的 middleware 在每個非 GET 請求前後呼叫
        r, rep = _req(p, fn)
        assert calls["n"] == 2 and rep["used"] == 0 and r["n"] == 2


def test_算到一半有人改了東西_不把改之前的結果存成新的():
    """epoch 保護：重算途中 invalidate_recent() 被呼叫，這份結果不可以進限時舊值層。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        calls = {"n": 0}

        @fleet_cache.cached
        def slow(conn):
            calls["n"] += 1
            n = conn.execute("SELECT count(*) FROM hardware").fetchone()[0]
            fleet_cache.invalidate_recent()               # 算的途中，另一個請求改了東西
            return {"n": n}

        _req(p, slow)
        _other_write(p)
        r, rep = _req(p, slow)
        assert calls["n"] == 2 and rep["used"] == 0, "改之前算的結果不可以被存起來"
        assert r["n"] == 2


def test_這條連線自己寫過_不用舊值():
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        fn, calls = _counter()
        _req(p, fn)
        c = db.get_connection(p)
        db.insert_hardware(c, asset_serial="S-own", hostname="own", ip="10.99.8.8", os="RHEL 8")   # 自己寫、還沒提交
        with fleet_cache.stale_ok() as rep:
            r = fn(c)
        assert calls["n"] == 2 and rep["used"] == 0 and r["n"] == 2
        c.close()


def test_設定檔或口徑環境變數變了_舊值作廢(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        fn, calls = _counter()
        _req(p, fn)
        monkeypatch.setenv("WEBIT3_MACHINE_KEY", "legacy")      # 台數口徑覆寫：算法本身變了
        r, rep = _req(p, fn)
        assert calls["n"] == 2 and rep["used"] == 0


def test_換資料庫不會拿到別的庫的舊值():
    with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
        p1, p2 = _db(t1), _db(t2)
        w = db.get_connection(p2)
        db.insert_hardware(w, asset_serial="S-2", hostname="h2", ip="10.99.1.2", os="RHEL 8")
        w.commit()
        w.close()
        fn, calls = _counter()
        a, _ = _req(p1, fn)
        b, rep = _req(p2, fn)
        assert rep["used"] == 0 and a["n"] == 1 and b["n"] == 2


def test_快取關閉或期限為0_不用舊值(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        fn, calls = _counter()
        _req(p, fn)
        _other_write(p)
        monkeypatch.setenv("WEBIT3_HOST_PAGE_STALE_S", "0")
        r, rep = _req(p, fn)
        assert rep["used"] == 0 and calls["n"] == 2
        monkeypatch.setenv("WEBIT3_HOST_PAGE_STALE_S", "120")
        monkeypatch.setenv("WEBIT3_FLEET_CACHE", "0")
        r, rep = _req(p, fn)
        assert rep["used"] == 0 and calls["n"] == 3


def test_舊值是拷貝_改了不污染下一個人():
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        fn, calls = _counter()
        _req(p, fn)
        _other_write(p)
        a, _ = _req(p, fn)
        a["rows"].append("毒")
        b, _ = _req(p, fn)
        assert b["rows"] == [1, 2]


def test_退役或不同參數各自一把鍵():
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        calls = {"n": 0}

        @fleet_cache.cached
        def f(conn, flag):
            calls["n"] += 1
            return {"flag": flag, "n": conn.execute("SELECT count(*) FROM hardware").fetchone()[0]}

        a, _ = _req(p, lambda c: f(c, False))
        b, _ = _req(p, lambda c: f(c, True))
        _other_write(p)
        a2, rep = _req(p, lambda c: f(c, False))
        b2, _ = _req(p, lambda c: f(c, True))
        assert a2["flag"] is False and b2["flag"] is True and rep["used"] == 1


def test_背景重算只算最近有人看的鍵_且算出來的是現在的資料():
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        fn, calls = _counter()
        _req(p, fn)                                       # 有人看主機頁 → 這把鍵進入「熱」清單
        assert len(fleet_cache._HOT) == 1
        _other_write(p)
        # 還沒到重算間隔：不重算
        assert fleet_cache.refresh_hot() == 0 and calls["n"] == 1
        # 到了間隔：重算，之後的請求吃到的是「新資料」的舊值
        fleet_cache.REFRESH_MIN_AGE_S, old = 0.0, fleet_cache.REFRESH_MIN_AGE_S
        try:
            assert fleet_cache.refresh_hot() == 1
        finally:
            fleet_cache.REFRESH_MIN_AGE_S = old
        assert calls["n"] == 2
        r, rep = _req(p, fn)
        assert r["n"] == 2 and calls["n"] == 2, "重算出來的是新資料，之後的請求不再算"
        assert len(fleet_cache._HOT) == 1, "重算自己不可以讓鍵永遠不過期"


def test_沒人看的鍵不再重算():
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        fn, calls = _counter()
        _req(p, fn)
        for v in fleet_cache._HOT.values():
            v["seen"] -= fleet_cache.HOT_IDLE_S + 5          # 很久沒人看了
        fleet_cache.REFRESH_MIN_AGE_S, old = 0.0, fleet_cache.REFRESH_MIN_AGE_S
        try:
            assert fleet_cache.refresh_hot() == 0
        finally:
            fleet_cache.REFRESH_MIN_AGE_S = old
        assert calls["n"] == 1 and not fleet_cache._HOT


def test_背景執行緒預設只在有排程器的正式服務開(monkeypatch):
    assert fleet_cache._refresher_wanted() is False
    monkeypatch.setenv("ASSET_SCHEDULER", "1")
    assert fleet_cache._refresher_wanted() is True
    monkeypatch.setenv("WEBIT3_FLEET_REFRESH", "0")
    assert fleet_cache._refresher_wanted() is False


# ---------- scope_memo ----------
def test_scope_memo_範圍內只建一次_沒範圍或自己寫過就每次現算():
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        n = {"n": 0}

        def build():
            n["n"] += 1
            return {"id": n["n"]}

        c = db.get_connection(p)
        a = fleet_cache.scope_memo(c, "x", build)
        b = fleet_cache.scope_memo(c, "x", build)
        assert a["id"] != b["id"] and n["n"] == 2             # 沒有範圍：每次現算
        with fleet_cache.request_scope(c):
            a = fleet_cache.scope_memo(c, "x", build)
            b = fleet_cache.scope_memo(c, "x", build)
            assert a is b and n["n"] == 3                     # 範圍內：同一個物件
        with fleet_cache.request_scope(c):
            fleet_cache.scope_memo(c, "x", build)
            db.insert_hardware(c, asset_serial="S-own", hostname="own", ip="10.99.8.8", os="RHEL 8")
            n0 = n["n"]
            fleet_cache.scope_memo(c, "x", build)
            fleet_cache.scope_memo(c, "x", build)
            assert n["n"] == n0 + 2                           # 自己寫過：不備忘
        c.close()


def test_machine_by_key_跟原本的線性查找同一列():
    import v2_hosts

    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        w = db.get_connection(p)
        for i in range(2, 6):
            db.insert_hardware(w, asset_serial="S-%d" % i, hostname="h%d" % i, ip="10.99.1.%d" % i, os="RHEL 8")
        w.commit()
        w.close()
        c = db.get_connection(p)
        rows = v2_hosts.machines(c)
        with fleet_cache.request_scope(c):
            for r in rows:
                old = next((x for x in v2_hosts.machines(c) if x["key"] == r["key"]), None)
                assert v2_hosts.machine_by_key(c, r["key"]) == old
            assert v2_hosts.machine_by_key(c, "不存在的 key") is None
        assert v2_hosts.machine_by_key(c, rows[0]["key"]) == rows[0]        # 沒範圍也行
        c.close()


def test_for_host_與舊寫法結果相同():
    """data_coverage.for_host 改用 machine_by_key 後，每一類資料、每一台的結果必須與原本逐欄相同。"""
    import data_coverage as dcv
    import v2_hosts

    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        c = db.get_connection(p)
        keys = [m["key"] for m in v2_hosts.machines(c)]
        for kind in ("account", "software", "service"):
            for k in keys:
                with fleet_cache.request_scope(c):
                    a = dcv.for_host(c, k, kind)
                b = dcv.for_host(c, k, kind)
                assert a == b and a is not None
        c.close()


# ---------- collected_os ----------
def test_measured_map_掛了快取_結果與原函式相同():
    import collected_os

    assert hasattr(collected_os._measured_map, "__wrapped_uncached__")
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        c = db.get_connection(p)
        raw = collected_os._measured_map.__wrapped_uncached__(c)
        assert collected_os._measured_map(c) == raw
        assert collected_os._measured_map(c) == raw
        c.close()


def test_measured_for_rows_不跨請求快取_連線id重用也不回舊結果():
    """2.070.1（審查 p1#3／#4／#5）：舊寫法用 (路徑, id(conn), data_version, total_changes) 當跨請求鍵——連線關閉後 id 會被
    重用、新連線的 data_version／total_changes 又都是初始值，同序號集合就直接回舊 OS。現在只在請求範圍內備忘，沒範圍就照算。"""
    import collected_os

    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        calls = {"n": 0}
        orig = collected_os._measured_map.__wrapped_uncached__

        def fake(conn, rows=None):
            calls["n"] += 1
            return orig(conn, rows)
        c = db.get_connection(p)
        rows = [dict(r) for r in c.execute("SELECT * FROM hardware")]
        import unittest.mock as um
        if True:
            with um.patch.object(collected_os, "_measured_map", fake):
                collected_os.measured_for_rows(c, rows)
                collected_os.measured_for_rows(c, rows)
                assert calls["n"] == 2, "沒有請求範圍：不快取，每次都算"
                c.close()
                w = db.get_connection(p)                           # 資料在兩次請求之間被另一條連線更新
                w.execute("UPDATE hardware SET os = 'AIX 7.3' WHERE asset_serial = 'S-1'")
                w.commit()
                w.close()
                c2 = db.get_connection(p)                          # 新連線（id 可能被重用）
                rows2 = [dict(r) for r in c2.execute("SELECT * FROM hardware")]
                collected_os.measured_for_rows(c2, rows2)
                assert calls["n"] == 3
                c2.close()


def test_measured_for_rows_範圍內備忘_回深拷貝_且有筆數上限():
    import collected_os

    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        calls = {"n": 0}

        def fake(conn, rows=None):
            calls["n"] += 1
            return {str(r["asset_serial"]): {"os": "RHEL 9", "nested": {"k": [1]}} for r in rows}
        c = db.get_connection(p)
        rows = [dict(r) for r in c.execute("SELECT * FROM hardware")]
        import unittest.mock as um
        with um.patch.object(collected_os, "_measured_map", fake):
            with fleet_cache.request_scope(c):
                a = collected_os.measured_for_rows(c, rows)
                a["S-1"]["nested"]["k"].append(99)                 # 呼叫端亂改回傳值
                b = collected_os.measured_for_rows(c, rows)
                assert calls["n"] == 1, "同一範圍同一組登記只算一次"
                assert b["S-1"]["nested"]["k"] == [1], "備忘不被呼叫端污染（深拷貝）"
                rows_changed = [dict(rows[0], os="AIX 7.3")]
                collected_os.measured_for_rows(c, rows_changed)
                assert calls["n"] == 2, "登記內容不同（同序號）不可命中舊結果"
                for i in range(collected_os.FOR_ROWS_SCOPE_MAX + 5):
                    collected_os.measured_for_rows(c, [dict(rows[0], asset_serial="X-%d" % i)])
                box = fleet_cache.scope_store(c, "collected_os.measured_for_rows")
                assert len(box) <= collected_os.FOR_ROWS_SCOPE_MAX
            assert collected_os.measured_for_rows(c, rows) is not None
            n_after = calls["n"]
            collected_os.measured_for_rows(c, rows)
            assert calls["n"] == n_after + 1, "離開範圍就不快取"
        c.close()


def test_scope_store_自己寫過或沒範圍_回None():
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        c = db.get_connection(p)
        assert fleet_cache.scope_store(c, "x") is None
        with fleet_cache.request_scope(c):
            box = fleet_cache.scope_store(c, "x")
            assert box == {} and fleet_cache.scope_store(c, "x") is box
            db.insert_hardware(c, asset_serial="S-own", hostname="own", ip="10.99.8.8", os="RHEL 8")
            assert fleet_cache.scope_store(c, "x") is None
        c.close()


def test_cached_參數含不可雜湊的rows_bypass_互不命中():
    """2.070.1（審查 p1#21）：單台版傳 list rows 時要安全 bypass（不快取、不進 stale／scope 層），兩組不同 rows 結果互不洩漏。"""
    seen = {"n": 0}

    @fleet_cache.cached
    def per_rows(conn, rows=None):
        seen["n"] += 1
        return {"serials": [r["asset_serial"] for r in rows], "tag": seen["n"]}
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        c = db.get_connection(p)
        r1 = [{"asset_serial": "S-A"}]
        r2 = [{"asset_serial": "S-B"}]
        with fleet_cache.request_scope(c), fleet_cache.stale_ok(120):
            a1 = per_rows(c, r1)
            a2 = per_rows(c, r2)
            a1b = per_rows(c, r1)
        assert a1["serials"] == ["S-A"] and a2["serials"] == ["S-B"], "互不命中"
        assert a1b["tag"] != a1["tag"] and seen["n"] == 3, "不可雜湊的參數每次都照算（bypass）"
        assert not fleet_cache._RECENT or all("per_rows" not in str(k) for k in fleet_cache._RECENT)
        c.close()


def test_rvtools_can_correct_os_依檔案時間記住_檔案變了就重讀(monkeypatch):
    import json
    import os

    import collected_os

    collected_os._CORR_CACHE.clear()
    loads = {"n": 0}
    real = json.load

    def spy(f, *a, **k):
        loads["n"] += 1
        return real(f, *a, **k)
    monkeypatch.setattr(json, "load", spy)
    v1 = collected_os._rvtools_can_correct_os()
    v2 = collected_os._rvtools_can_correct_os()
    assert v1 == v2 and loads["n"] == 1, "沒變就不重讀"
    real_stat = os.stat

    class _St:
        def __init__(self, st):
            self.st_mtime_ns = st.st_mtime_ns + 1
            self.st_size = st.st_size

    def fake_stat(path, *a, **k):
        st = real_stat(path, *a, **k)
        return _St(st) if str(path).endswith("correction_fields.json") else st
    monkeypatch.setattr(os, "stat", fake_stat)
    assert collected_os._rvtools_can_correct_os() == v1 and loads["n"] == 2, "檔案時間變了要重讀"
    collected_os._CORR_CACHE.clear()


# ---------- 端點 ----------
@pytest.fixture()
def client(tmp_path):
    import api
    from fastapi.testclient import TestClient

    p = _db(tmp_path)

    def _get_db():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester", "role": "admin"}
    import os
    old = os.environ.get("ASSET_DB_PATH")
    os.environ["ASSET_DB_PATH"] = str(p)
    try:
        yield TestClient(api.app, raise_server_exceptions=False), p
    finally:
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)
        if old is None:
            os.environ.pop("ASSET_DB_PATH", None)
        else:
            os.environ["ASSET_DB_PATH"] = old


def test_端點_summary_profile_section_有用到舊值才帶標示(client):
    cl, p = client
    key = "S-1"
    r1 = cl.get("/api/v2/machine/summary", params={"key": key})
    assert r1.status_code == 200
    assert "fleet_as_of" not in r1.json(), "第一次是即時算的，不可以標成舊值"
    _other_write(p)
    r2 = cl.get("/api/v2/machine/summary", params={"key": key})
    j2 = r2.json()
    assert r2.status_code == 200 and "fleet_as_of" in j2 and j2["fleet_age_s"] >= 0
    base = {k: v for k, v in j2.items() if k not in ("fleet_as_of", "fleet_age_s")}
    assert set(base) == set(r1.json()), "多出來的只有兩個標示欄"
    r3 = cl.get("/api/v2/machine/profile", params={"key": key})
    assert r3.status_code == 200
    r4 = cl.get("/api/v2/machine/section", params={"key": key, "name": "software"})
    assert r4.status_code == 200 and r4.json()["key"] == "software"


def test_端點_非GET請求會清空舊值_GET不會(client):
    cl, p = client
    spy = {"n": 0}
    real = fleet_cache.invalidate_recent

    def counting():
        spy["n"] += 1
        real()
    fleet_cache.invalidate_recent = counting
    try:
        cl.get("/api/version")
        assert spy["n"] == 0, "GET 不可以清舊值，否則主機頁永遠吃不到"
        cl.post("/api/v2/__nope__", json={})
        assert spy["n"] >= 2, "非 GET 請求要在前後都清（改完立刻看得到）"
    finally:
        fleet_cache.invalidate_recent = real


def test_端點_使用者改了東西後_主機頁立刻看到新資料(client):
    """端對端：主機頁先存了舊值；一個非 GET 請求之後，下一次 summary 必須是即時算的（沒有標示欄）。"""
    cl, p = client
    key = "S-1"
    cl.get("/api/v2/machine/summary", params={"key": key})
    _other_write(p)
    assert "fleet_as_of" in cl.get("/api/v2/machine/summary", params={"key": key}).json()
    cl.post("/api/v2/__nope__", json={})
    j = cl.get("/api/v2/machine/summary", params={"key": key}).json()
    assert "fleet_as_of" not in j, "非 GET 請求之後不可以再吃改之前的舊值"


def test_v2_三支主機頁端點走_call_host_其他讀取端點不走():
    src = (ROOT / "APP" / "asset-module" / "backend" / "routers" / "v2.py").read_text(encoding="utf-8")
    for name in ("summary", "profile", "section"):
        assert f"_call_host(v2_sections.{name}," in src, f"{name} 要走限時舊值"
    assert src.count("_call_host(") == 4          # 三支呼叫＋函式定義；別的端點（清單、匯出、同值下鑽…）不可偷偷用舊值
    assert "fleet_cache.stale_ok" in src[src.index("def _call_host("):src.index("def _call_host(") + 900]
    api_src = (ROOT / "APP" / "asset-module" / "backend" / "api.py").read_text(encoding="utf-8")
    i = api_src.index("async def _activity_log_middleware")
    assert api_src[i:i + 2500].count("fleet_cache.invalidate_recent()") == 2


# ===== 預設關閉與設定驗證（2.069.4：GPT 審查後，舊值層預設不啟用）=====
def test_預設關閉_沒設環境變數就不吃舊值(monkeypatch):
    monkeypatch.delenv("WEBIT3_HOST_PAGE_STALE_S", raising=False)
    assert fleet_cache.stale_max_age() == 0.0
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        fn, calls = _counter()
        _req(p, fn)
        _other_write(p)
        r, rep = _req(p, fn)
        assert rep["used"] == 0 and calls["n"] == 2


@pytest.mark.parametrize("bad", ["nan", "inf", "-5", "99999", "abc", "", "1e9"])
def test_設定值不合法_一律關閉(monkeypatch, bad):
    monkeypatch.setenv("WEBIT3_HOST_PAGE_STALE_S", bad)
    assert fleet_cache.stale_max_age() == 0.0


def test_設定值合法_採用(monkeypatch):
    monkeypatch.setenv("WEBIT3_HOST_PAGE_STALE_S", "90")
    assert fleet_cache.stale_max_age() == 90.0
    monkeypatch.setenv("WEBIT3_HOST_PAGE_STALE_S", "600")
    assert fleet_cache.stale_max_age() == 600.0


def test_舊值層關閉時背景重算也不啟動(monkeypatch):
    monkeypatch.delenv("WEBIT3_HOST_PAGE_STALE_S", raising=False)
    monkeypatch.setenv("ASSET_SCHEDULER", "1")
    assert fleet_cache._refresher_wanted() is False
    monkeypatch.setenv("WEBIT3_HOST_PAGE_STALE_S", "120")
    assert fleet_cache._refresher_wanted() is True
