"""2-1「收集中…」掛很久（2026-10-20 公司機 v1.470.2）。

221 重現：開一輪分批收集 → systemctl restart webit3-api → collect_dispatch_run 那列永遠 running，
畫面「收集中…」一直亮，每週排程也被擋。要守的：

1. 服務啟動收尾孤兒列 →「已中斷（服務重啟）」＋結束時間
2. 心跳逾時（預設 5 分，可設）→ liveness=stale；批次間隔中以「下一批預計時間」為準，不誤判
3. 真的在跑的 → live；stale 才給「標記為已中斷」，live 不給
4. 停止收集：目前那台收完就停（v1.471.1，原本是整批）、剩下的不跑、已收的留著；間隔等待中按停止要馬上醒來
5. 涵蓋率：網段收過幾個位址、從未收集的網段；點進去逐址列「從未收集」
6. 頁簽不再是光禿禿的「收集中…」
"""
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402

ADOPT = ROOT / "APP" / "asset-module" / "frontend" / "pages" / "adopt.vue"


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        yield c
    finally:
        c.close()


def _ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _running_row(conn, heartbeat=None, next_batch=None, started=None):
    cur = conn.execute(
        "INSERT INTO collect_dispatch_run (trigger, target_count, status, started_at, batch_total, "
        "batch_done, done_count, heartbeat_at, next_batch_at) VALUES ('manual', 100, 'running', ?, 10, 3, 30, ?, ?)",
        (started or _ts(datetime.now() - timedelta(hours=1)), heartbeat, next_batch))
    conn.commit()
    return cur.lastrowid


def _run(conn, rid):
    return dict(conn.execute("SELECT * FROM collect_dispatch_run WHERE id=?", (rid,)).fetchone())


def test_服務重啟的孤兒列_啟動時收尾成已中斷(conn):
    rid = _running_row(conn, heartbeat=_ts(datetime.now()))
    assert cd.finalize_orphans(conn) == 1
    r = _run(conn, rid)
    assert r["status"] == cd.STATUS_INTERRUPTED
    assert r["error"] == "已中斷（服務重啟）" and r["finished_at"]
    assert cd.finalize_orphans(conn) == 0, "跑過一次就沒有孤兒了"


def test_心跳逾時_回stale(conn):
    rid = _running_row(conn, heartbeat=_ts(datetime.now() - timedelta(minutes=12)))
    lv = cd.run_liveness(conn, _run(conn, rid))
    assert lv["liveness"] == "stale" and lv["stale_minutes"] == 5
    assert lv["last_progress_at"], "畫面要講得出最後進度幾點"


def test_心跳逾時分鐘數可設定(conn):
    from db import set_setting

    rid = _running_row(conn, heartbeat=_ts(datetime.now() - timedelta(minutes=12)))
    set_setting(conn, "dispatch_heartbeat_stale_minutes", "30")
    assert cd.run_liveness(conn, _run(conn, rid))["liveness"] == "live"
    assert cd.dispatch_settings(conn)["heartbeat_stale_minutes"] == 30


def test_批次間隔中_以下一批時間為準_不誤判(conn):
    rid = _running_row(conn, heartbeat=_ts(datetime.now() - timedelta(minutes=20)),
                       next_batch=_ts(datetime.now() + timedelta(minutes=10)))
    lv = cd.run_liveness(conn, _run(conn, rid))
    assert lv["liveness"] == "live" and lv["next_batch_at"]


def test_活著的不給標中斷_逾時的可以(conn):
    live = _running_row(conn, heartbeat=_ts(datetime.now()))
    with pytest.raises(ValueError):
        cd.mark_interrupted(conn, live, by="admin")
    stale = _running_row(conn, heartbeat=_ts(datetime.now() - timedelta(minutes=30)))
    r = cd.mark_interrupted(conn, stale, by="admin")
    assert r["status"] == cd.STATUS_INTERRUPTED and "人工標記" in r["error"]


def test_latest_run帶出liveness(conn, monkeypatch):
    monkeypatch.setattr(cd, "_sleep", lambda s: None)
    cd.run_dispatch(conn, "192.0.2.1", prober=lambda ip: None)
    _running_row(conn, heartbeat=_ts(datetime.now() - timedelta(minutes=30)))
    out = cd.latest_run(conn)
    assert out["run"]["liveness"] == "stale"


def test_跑的時候有心跳(conn, monkeypatch):
    monkeypatch.setattr(cd, "_sleep", lambda s: None)
    out = cd.run_dispatch(conn, "192.0.2.0/24", prober=lambda ip: None, batch_size=100, gap_seconds=0)
    r = _run(conn, out["run_id"])
    assert r["heartbeat_at"] and r["status"] == "ok"


def test_每週排程不被孤兒列擋(conn):
    import dispatch_weekly

    _running_row(conn, heartbeat=_ts(datetime.now() - timedelta(minutes=30)),
                 started=_ts(datetime.now() - timedelta(minutes=40)))
    assert dispatch_weekly._run_in_progress(conn) is False
    _running_row(conn, heartbeat=_ts(datetime.now()), started=_ts(datetime.now()))
    assert dispatch_weekly._run_in_progress(conn) is True


def test_停止收集_目前那台收完就停_已收的留著(conn, monkeypatch):
    # v1.471.1：停止在逐台之間也看（每台最多卡 180 秒有上限），不用等整批 50 台做完
    calls = {"n": 0}
    real = cd._do_import

    def imp(c, ip, asset):
        calls["n"] += 1
        if calls["n"] == 70:            # 第二批第 20 台收的當下按停止
            cd.request_stop(max(cd._stop_events))
        return real(c, ip, asset)

    monkeypatch.setattr(cd, "_do_import", imp)
    monkeypatch.setattr(cd, "_sleep", lambda s: None)
    out = cd.run_dispatch(conn, "192.0.2.0/24", prober=lambda ip: None, batch_size=50, gap_seconds=0)
    r = _run(conn, out["run_id"])
    assert r["status"] == cd.STATUS_STOPPED
    assert r["error"] == "已手動停止（完成 1/6 批；第 2 批收了 20/50 台）"
    assert r["batch_done"] == 1 and r["done_count"] == 70
    kept = conn.execute("SELECT COUNT(*) FROM collect_dispatch_result WHERE run_id=?",
                        (out["run_id"],)).fetchone()[0]
    assert kept == 70, "已收的 70 個位址要留著"
    assert calls["n"] == 70, "按停止之後不可以再收下一台"


def test_停止收集_批與批之間(conn, monkeypatch):
    calls = {"n": 0}
    real = cd._probe_all

    def probe(ips, prober, workers=64):
        calls["n"] += 1
        if calls["n"] == 2:            # 第二批探測的當下按停止 → 第二批一台都不收
            cd.request_stop(max(cd._stop_events))
        return real(ips, prober, workers=workers)

    monkeypatch.setattr(cd, "_probe_all", probe)
    monkeypatch.setattr(cd, "_sleep", lambda s: None)
    out = cd.run_dispatch(conn, "192.0.2.0/24", prober=lambda ip: None, batch_size=50, gap_seconds=0)
    r = _run(conn, out["run_id"])
    assert r["status"] == cd.STATUS_STOPPED and r["batch_done"] == 1
    assert r["error"] == "已手動停止（完成 1/6 批；第 2 批收了 0/50 台）"


def test_停止收集_間隔等待中馬上醒來(conn):
    # 不 monkeypatch _sleep：真的用 Event.wait 等 300 秒，按停止後應該秒醒
    res = {}

    def go():
        c = db.get_connection(Path(conn.execute("PRAGMA database_list").fetchone()[2]))
        try:
            res["out"] = cd.run_dispatch(c, "192.0.2.0/25", prober=lambda ip: None,
                                         batch_size=50, gap_seconds=300)
        finally:
            c.close()

    t = threading.Thread(target=go)
    t0 = time.monotonic()
    t.start()
    for _ in range(100):
        if cd._stop_events:
            r = conn.execute("SELECT next_batch_at FROM collect_dispatch_run ORDER BY id DESC LIMIT 1").fetchone()
            if r and r[0]:
                break
        time.sleep(0.05)
    assert cd.request_stop(max(cd._stop_events))
    t.join(10)
    assert not t.is_alive() and time.monotonic() - t0 < 10, "間隔等待要能被停止打斷"
    assert res["out"]["stopped_after"] == 1


def test_不是這輪的停止_不收(conn):
    assert cd.request_stop(999999) is False


def test_涵蓋率_收過的與從未收集的網段(conn, monkeypatch):
    import dispatch_coverage

    conn.execute("INSERT INTO network_segment (cidr, raw_cidr, net_start, net_end, location, environment) "
                 "VALUES ('192.0.2.0/24', '192.0.2.0/24', 3221225984, 3221226239, '機房A', '測試')")
    conn.execute("INSERT INTO network_segment (cidr, raw_cidr, net_start, net_end, location, environment) "
                 "VALUES ('198.51.100.0/24', '198.51.100.0/24', 3325256704, 3325256959, '機房A', '測試')")
    conn.commit()
    monkeypatch.setattr(cd, "_sleep", lambda s: None)
    cd.run_dispatch(conn, "192.0.2.1-10\n203.0.113.5", prober=lambda ip: None)
    cov = dispatch_coverage.coverage(conn)
    by = {s["cidr"]: s for s in cov["segments"]}
    assert by["192.0.2.0/24"]["collected"] == 10 and by["192.0.2.0/24"]["total"] == 254
    assert by["192.0.2.0/24"]["last_at"]
    assert by["198.51.100.0/24"]["never"] is True
    assert by["203.0.113.0/24"]["in_table"] is False, "收過但配置表沒有的 /24 也要列出來"
    assert [s["no"] for s in cov["segments"]] == list(range(1, len(cov["segments"]) + 1))
    d = dispatch_coverage.segment_detail(conn, "192.0.2.0/24")
    assert d["total"] == 254 and d["collected"] == 10 and d["listed_all"]
    assert d["items"][0]["ip"] == "192.0.2.1" and not d["items"][0]["never"]
    assert d["items"][20]["never"] is True


def test_頁簽不再是光禿禿的收集中():
    src = ADOPT.read_text(encoding="utf-8")
    assert "　收集中…</span>" not in src
    assert "collectBusyText" in src
    # 要講得出第幾批、下一批幾點、可能已中斷、納管後收集排隊
    for k in ("下一批", "可能已中斷", "排隊中", "停止收集", "標記為已中斷", "從未收集"):
        assert k in src, k
    assert re.search(r"收集中：第 \$\{", src)
