"""全機隊計算的跨請求快取（fleet_cache，2026-10-21 使用者：「2-1、3-1 都卡一下」）。

釘住「寧可重算，不可給舊數字」：
- 資料沒變：第二次直接拿（不重算），而且拿到的是拷貝（改了不污染下一個人）
- 別的連線寫入（收集、匯入、排程…）→ 下一次一定重算
- 呼叫的連線自己寫過／交易沒結束 → 不用快取
- 函式算的過程寫了資料庫（有副作用）→ 不存
- 記憶體資料庫、WEBIT3_FLEET_CACHE=0 → 不快取
- 七支重的全機隊計算都掛上了
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import fleet_cache  # noqa: E402


def _db(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    w = db.get_connection(p)
    db.insert_hardware(w, asset_serial="S-1", hostname="h1", ip="10.99.1.1", os="RHEL 8")
    w.commit()
    w.close()
    return p


def _counter():
    calls = {"n": 0}

    @fleet_cache.cached
    def count_hw(conn):
        calls["n"] += 1
        return {"n": conn.execute("SELECT count(*) FROM hardware").fetchone()[0], "rows": [1, 2]}

    return count_hw, calls


def test_資料沒變_第二次不重算_而且是拷貝():
    fleet_cache.clear()
    fn, calls = _counter()
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        c1 = db.get_connection(p)
        a = fn(c1)
        c1.close()
        a["rows"].append(99)           # 呼叫端改了結果
        c2 = db.get_connection(p)
        b = fn(c2)
        c2.close()
        assert calls["n"] == 1
        assert b == {"n": 1, "rows": [1, 2]}


def test_別的連線寫入後_下一次一定重算():
    fleet_cache.clear()
    fn, calls = _counter()
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        r = db.get_connection(p)
        assert fn(r)["n"] == 1
        w = db.get_connection(p)
        db.insert_hardware(w, asset_serial="S-2", hostname="h2", ip="10.99.1.2", os="RHEL 8")
        w.commit()
        w.close()
        assert fn(r)["n"] == 2          # 同一條讀連線也看得到別人剛寫的
        r.close()
        assert calls["n"] == 2


def test_呼叫的連線自己寫過_不用快取():
    fleet_cache.clear()
    fn, calls = _counter()
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        r = db.get_connection(p)
        fn(r)
        r.close()
        w = db.get_connection(p)
        db.insert_hardware(w, asset_serial="S-3", hostname="h3", ip="10.99.1.3", os="RHEL 8")
        # 還沒 commit：檔案還沒變，但這條連線看得到——一定要重算
        assert fn(w)["n"] == 2
        w.rollback()
        w.close()
        assert calls["n"] == 2


def test_函式自己寫了資料庫_不存():
    fleet_cache.clear()
    calls = {"n": 0}

    @fleet_cache.cached
    def writes(conn):
        calls["n"] += 1
        conn.execute("UPDATE hardware SET remark = 'x'")
        conn.commit()
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        for _ in range(2):
            c = db.get_connection(p)
            writes(c)
            c.close()
        assert calls["n"] == 2


def test_記憶體資料庫與關閉開關_不快取(monkeypatch):
    fleet_cache.clear()
    fn, calls = _counter()
    m = db.get_connection(Path(":memory:")) if False else None   # get_connection 會建資料夾，改用 sqlite3
    import sqlite3
    m = sqlite3.connect(":memory:")
    m.execute("CREATE TABLE hardware (x)")
    fn(m)
    fn(m)
    assert calls["n"] == 2
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        monkeypatch.setenv("WEBIT3_FLEET_CACHE", "0")
        for _ in range(2):
            c = db.get_connection(p)
            fn(c)
            c.close()
        assert calls["n"] == 4


def test_七支全機隊計算都掛上快取():
    import asset_classify
    import collect_dispatch
    import health_check
    import manage_state
    import pipeline
    import trust_score
    import vip_view

    for f in (health_check.evaluate_all, asset_classify.classify_all, manage_state.summarize,
              trust_score.compute_all, vip_view.duplicate_candidate_groups,
              collect_dispatch.latest_run, pipeline.summarize):
        assert hasattr(f, "__wrapped_uncached__"), f.__qualname__


def test_2006新掛快取的四支():
    """2.006.2：existence／count_bridge（只快取預設參數）、rack_layout.machines、v2_hosts.machines。
    公開入口用預設參數呼叫時，第二次要命中快取，而且拿到的是副本（改了不污染下一個人）。"""
    import count_bridge
    import existence
    import rack_layout
    import v2_hosts

    for f in (existence._compute_default, count_bridge._compute_default, rack_layout._machines_cached,
              v2_hosts._machines_cached):
        assert hasattr(f, "__wrapped_uncached__"), f.__qualname__
    fleet_cache.clear()
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        fns = (existence.compute, count_bridge.compute, rack_layout.machines, v2_hosts.machines)

        def run(fn):
            c = db.get_connection(p)          # 每個請求一條新連線（跟正式環境一樣；第一次可能順手建表＝那條連線不存快取）
            try:
                return fn(c)
            finally:
                c.close()
        for fn in fns:
            run(fn)                            # 暖身：建表／種子寫入
        for fn in fns:
            fleet_cache.STATS.update(hit=0, miss=0, bypass=0)
            first = run(fn)
            first_copy = repr(first)
            if isinstance(first, dict):
                first["__poison__"] = 1
            else:
                first.append("__poison__")
            second = run(fn)
            assert fleet_cache.STATS["hit"] >= 1, f"{fn.__qualname__} 第二次沒有命中快取"
            assert repr(second) == first_copy, "快取回傳的不是副本（被呼叫端改到了）"


def test_快取與不快取結果一樣():
    """同一份資料：掛快取拿到的＝原函式直接算的（加總＝下鑽不受影響）。"""
    import manage_state

    fleet_cache.clear()
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        c = db.get_connection(p)
        raw = manage_state.summarize.__wrapped_uncached__(c)
        first = manage_state.summarize(c)
        second = manage_state.summarize(c)
        c.close()
        assert first == raw == second


def test_有收集正在跑_收集結果不用快取():
    """進度與「還活著嗎」看現在時間與記憶體裡的停止旗標，不是資料庫——跑的時候一律照算（獨立審查 #2）。"""
    import collect_dispatch as cd

    fleet_cache.clear()
    with tempfile.TemporaryDirectory() as tmp:
        p = _db(tmp)
        c = db.get_connection(p)
        cd.latest_run(c)            # 建表（舊庫沒有就建）
        c.close()
        w = db.get_connection(p)
        w.execute("INSERT INTO collect_dispatch_run (status, started_at) VALUES ('running', datetime('now'))")
        w.commit()
        w.close()
        before = dict(fleet_cache.STATS)
        for _ in range(2):
            c = db.get_connection(p)
            cd.latest_run(c)
            c.close()
        assert fleet_cache.STATS["bypass"] - before["bypass"] == 2
        assert fleet_cache.STATS["hit"] == before["hit"]


def test_冷快取時同時進來的相同請求_只算一次_其他的等它(tmp_path):
    """2026-10-29 公司正式機搜尋很慢：打字連送好幾個搜尋，快取是冷的時每個都各自重算整個機隊。"""
    import threading
    import time

    fleet_cache.clear()
    calls = {"n": 0}

    @fleet_cache.cached
    def slow_count(conn):
        calls["n"] += 1
        time.sleep(0.3)
        return {"n": conn.execute("SELECT count(*) FROM hardware").fetchone()[0], "rows": [1]}

    p = _db(str(tmp_path))
    out, errs = [], []

    def worker():
        c = db.get_connection(p)
        try:
            out.append(slow_count(c))
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)
        finally:
            c.close()

    ts = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert not errs and len(out) == 5
    assert calls["n"] == 1                                   # 只有一個真的去算
    assert all(o == {"n": 1, "rows": [1]} for o in out)
    out[0]["rows"].append(9)                                 # 每個拿到的是自己的拷貝
    assert out[1]["rows"] == [1]


def test_單飛不擋寫入後重算_資料變了拿不到舊結果(tmp_path):
    fleet_cache.clear()
    fn, calls = _counter()
    p = _db(str(tmp_path))
    c1 = db.get_connection(p)
    assert fn(c1)["n"] == 1
    c1.close()
    w = db.get_connection(p)
    db.insert_hardware(w, asset_serial="S-2", hostname="h2", ip="10.99.1.2", os="RHEL 8")
    w.commit()
    w.close()
    c2 = db.get_connection(p)
    assert fn(c2)["n"] == 2 and calls["n"] == 2
    c2.close()
