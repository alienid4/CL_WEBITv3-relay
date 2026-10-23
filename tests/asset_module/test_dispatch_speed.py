"""2-1 開始收集再加速（v1.468.5，2026-10-19 使用者：「收集一次要很久，這有什麼改善方式」）。

v1.468.4 已有「只收新的」＋節奏設定。這支釘住再往下的兩件：
  1. 探測器四個埠同時連（fast_probe）：判定規則跟 net_scan._probe_host 一致，空位址不再排隊等 4×0.6 秒
  2. 空位址另用較長的時間窗（預設 7 天）；存活掃描在上次收集之後看到它活著 → 照樣重探；
     0＝空位址每次重探；「只收新的」整個關掉（skip_fresh_hours=0）時全部重探
"""
import contextlib
import socket
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402
import fast_probe  # noqa: E402
import net_scan  # noqa: E402

SETTINGS_VUE = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "settings" / "index.vue"
                ).read_text(encoding="utf-8")


# ===================== 1. 探測器 =====================

def _listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    return s, s.getsockname()[1]


def _closed_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_開放埠與被拒_判定跟舊探測器一致():
    srv, open_p = _listener()
    try:
        closed = _closed_port()
        for ports in ((open_p,), (closed,), (closed, open_p), (open_p, closed)):
            got = fast_probe.probe_host("127.0.0.1", ports, timeout=0.6, ping=lambda ip: False)
            want = net_scan._probe_host("127.0.0.1", ports=ports, timeout=0.6, icmp=False)
            assert got == want, (ports, got, want)
        assert fast_probe.probe_host("127.0.0.1", (closed, open_p), ping=lambda ip: False) == [open_p]
        # Windows 對本機關閉的埠會重送 SYN 約 2 秒才回被拒，所以這裡給長一點的等待
        assert fast_probe.probe_host("127.0.0.1", (closed,), timeout=3.0,
                                     ping=lambda ip: False) == [], "被拒＝主機在、埠關，算活著"
    finally:
        srv.close()


def test_完全沒反應_四個埠一起等_不是一個一個等():
    # 192.0.2.0/24 是文件用位址（TEST-NET-1），不會有人回
    t = time.monotonic()
    got = fast_probe.probe_host("192.0.2.123", (22, 445, 3389, 5985), timeout=0.6,
                                ping=lambda ip: False)
    assert got is None
    assert time.monotonic() - t < 1.5, "四個埠同時等，應該約 0.6 秒，不是 2.4 秒"


def test_TCP全無反應_ping得通_算活著沒有開放埠():
    assert fast_probe.probe_host("192.0.2.124", (22,), timeout=0.2, ping=lambda ip: True) == []
    assert fast_probe.probe_host("192.0.2.124", (22,), timeout=0.2, icmp=False,
                                 ping=lambda ip: True) is None


def test_收集預設探測器用的是同時連那一顆(monkeypatch):
    seen = []
    monkeypatch.setattr(fast_probe, "probe_host",
                        lambda ip, ports, timeout=0.6: seen.append((ip, tuple(ports))) or None)
    assert cd._default_prober("10.99.9.1") is None
    assert seen == [("10.99.9.1", cd.DISPATCH_PORTS)]


# ===================== 2. 空位址的時間窗 =====================

@contextlib.contextmanager
def _db():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            yield conn
        finally:
            conn.close()


PORTS = {"10.99.9.10": [22], "10.99.9.13": None, "10.99.9.14": None}


def _run(conn, *, full=True, calls=None):
    def prober(ip):
        if calls is not None:
            calls.append(ip)
        return PORTS.get(ip)
    return cd.run_dispatch(conn, "\n".join(PORTS), prober=prober, ttl_prober=lambda ip: None,
                           ssh_prober=lambda ip: (ip == "10.99.9.10", None), full=full,
                           gap_seconds=0)


def _age(conn, days):
    conn.execute("UPDATE collect_dispatch_result SET created_at = "
                 f"datetime('now','localtime','-{days} days')")
    conn.commit()


def test_兩天前_已納管要重探_空位址還在七天窗內不重探():
    with _db() as conn:
        _run(conn)
        _age(conn, 2)
        calls = []
        out = _run(conn, full=False, calls=calls)
        assert calls == ["10.99.9.10"] and out["skipped"] == 2


def test_空位址超過七天就重探():
    with _db() as conn:
        _run(conn)
        _age(conn, 8)
        calls = []
        _run(conn, full=False, calls=calls)
        assert sorted(calls) == sorted(PORTS)


def test_存活掃描在上次收集之後看到它活著_要重探():
    with _db() as conn:
        _run(conn)
        _age(conn, 2)
        conn.execute("INSERT INTO scan_history (ip, scan_ok, scan_time) VALUES "
                     "('10.99.9.13', 1, datetime('now','localtime','-1 days')),"
                     "('10.99.9.14', 1, datetime('now','localtime','-3 days'))")   # 比收集舊：不算
        conn.commit()
        calls = []
        _run(conn, full=False, calls=calls)
        assert "10.99.9.13" in calls and "10.99.9.14" not in calls


def test_空位址窗設0_空位址每次重探_已納管照24小時窗():
    with _db() as conn:
        cd.save_dispatch_settings(conn, {"skip_empty_hours": 0})
        _run(conn)
        calls = []
        _run(conn, full=False, calls=calls)
        assert sorted(calls) == ["10.99.9.13", "10.99.9.14"]


def test_只收新的整個關掉_全部重探_全部重收也全部重探():
    with _db() as conn:
        _run(conn)
        calls = []
        _run(conn, full=True, calls=calls)
        assert sorted(calls) == sorted(PORTS)
        cd.save_dispatch_settings(conn, {"skip_fresh_hours": 0})
        calls.clear()
        _run(conn, full=False, calls=calls)
        assert sorted(calls) == sorted(PORTS)


def test_設定_空位址窗_存得進去_範圍擋下_前端有欄位():
    with _db() as conn:
        assert cd.dispatch_settings(conn)["skip_empty_hours"] == cd.DEFAULT_SKIP_EMPTY_HOURS == 168
        assert cd.save_dispatch_settings(conn, {"skip_empty_hours": 72})["skip_empty_hours"] == 72
        with pytest.raises(ValueError):
            cd.save_dispatch_settings(conn, {"skip_empty_hours": 99999})
    assert "dEdit.skip_empty_hours" in SETTINGS_VUE and "空位址多久重探" in SETTINGS_VUE


# ===================== 3. 只掃已登記的 IP =====================

ADOPT_VUE = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "adopt.vue").read_text(encoding="utf-8")


def _reg(conn, sn, ip, status="使用中"):
    db.insert_hardware(conn, asset_serial=sn, ip=ip, hostname=f"h-{sn}", environment="測試",
                       asset_status=status)


def test_已登記IP_退役不算_多IP拆開_無效位址不算():
    with _db() as conn:
        _reg(conn, "A", "10.99.9.20")
        _reg(conn, "B", "10.99.9.21, 10.99.9.22")
        _reg(conn, "C", "10.99.9.23", status="報廢")
        _reg(conn, "D", "0.0.0.0")
        _reg(conn, "E", "不是IP")
        _reg(conn, "F", "10.99.9.20")          # 同 IP 兩筆登記只算一次
        assert cd.registered_ips(conn) == ["10.99.9.20", "10.99.9.21", "10.99.9.22"]


def test_只掃已登記_有範圍取交集_空白絕不代表全部_單一IP照收():
    # 2026-10-20 公司機：框空著按開始收集 → 以前跑了全部 4565 個登記 IP。空白一律擋。
    with _db() as conn:
        _reg(conn, "A", "10.99.9.20")
        _reg(conn, "B", "10.99.8.5")
        with pytest.raises(ValueError) as e:
            cd.registered_targets(conn, "")
        assert "空白不代表全部" in str(e.value)
        text, label = cd.registered_targets(conn, "10.99.9.0/24")
        assert text == "10.99.9.20" and "10.99.9.0/24" in label
        with pytest.raises(cd.NoRegisteredInRange):
            cd.registered_targets(conn, "10.99.7.0/24")
        # 單一 IP 不受「只掃已登記」限制（沒登記的測試機也照收）；範圍照樣篩
        text, label = cd.registered_targets(conn, "10.99.7.142\n10.99.9.0/24\n10.99.8.1-9")
        assert text.split("\n") == ["10.99.7.142", "10.99.8.5", "10.99.9.20"]
        assert "直接指定 1 個" in label
        text, _ = cd.registered_targets(conn, "10.99.7.142/32")
        assert text == "10.99.7.142", "/32 是指名一台，不受只掃已登記限制"


def test_只掃已登記_run上記的是說明不是一長串IP():
    with _db() as conn:
        _reg(conn, "A", "10.99.9.20")
        text, label = cd.registered_targets(conn, "10.99.9.0/24")
        cd.run_dispatch(conn, text, prober=lambda ip: None, ttl_prober=lambda ip: None,
                        targets_label=label, gap_seconds=0)
        run = conn.execute("SELECT targets_raw FROM collect_dispatch_run ORDER BY id DESC").fetchone()
        assert run["targets_raw"] == label


def test_API_只掃已登記_目標換成登記IP_沒登記回400(tmp_path, monkeypatch):
    import api
    from fastapi.testclient import TestClient

    p = tmp_path / "t.db"
    db.init_db(p)

    def _get_db():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester"}
    got = {}

    def fake_run(conn, targets, triggered_by=None, full=True, **kw):
        got.update(targets=targets, **kw)
        return {"run_id": 1, "total": 1, "collected": 0, "needs_action": 0, "not_onboardable": 0,
                "empty_addresses": 0, "by_status": {}, "by_route": {}, "results": []}

    monkeypatch.setattr(cd, "run_dispatch", fake_run)
    try:
        client = TestClient(api.app, raise_server_exceptions=False)
        r = client.post("/api/collect/dispatch", json={"targets": "", "registered_only": True})
        assert r.status_code == 400 and "空白不代表全部" in r.json()["detail"], "空白絕不代表全部"
        assert not got, "空白不可以開跑"
        r = client.post("/api/collect/dispatch", json={"targets": "10.99.9.0/24", "registered_only": True})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "no_registered_in_range"
        c = db.get_connection(p)
        _reg(c, "A", "10.99.9.20")
        c.close()
        assert client.get("/api/collect/dispatch/gap").json()["registered_count"] == 1
        r = client.post("/api/collect/dispatch", json={"targets": "10.99.9.0/24", "registered_only": True})
        assert r.status_code == 200
        assert got["targets"] == "10.99.9.20" and "只掃已登記" in got["targets_label"]
        got.clear()
        client.post("/api/collect/dispatch", json={"targets": "10.99.9.21"})
        assert got["targets"] == "10.99.9.21" and "targets_label" not in got, "沒勾就照舊"
    finally:
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)


# ===================== 5. 每週六晚上整片網段 =====================

def test_每週_預設週六22點_沒範圍不跑_手動整片才記範圍():
    from datetime import datetime

    import dispatch_weekly as dw

    with _db() as conn:
        s = dw.status(conn)
        assert (s["enabled"], s["weekday"], s["time"]) == (True, 5, "22:00")
        sat = datetime(2026, 10, 24, 22, 5)                 # 週六
        assert sat.weekday() == 5
        assert not dw.is_due(conn, sat), "沒有範圍不跑"
        assert not dw.remember_full_targets(conn, "10.99.9.5", 1), "單台補收不算整片"
        assert dw.remember_full_targets(conn, "10.99.9.0/24", 254)
        assert dw.settings(conn)["targets"] == "10.99.9.0/24"
        assert dw.next_run(conn, datetime(2026, 10, 19, 9, 0)) == "2026-10-24 22:00"


def test_每週_到點才跑_一天只跑一次_全部重收_有在跑就等():
    from datetime import datetime

    import dispatch_weekly as dw

    with _db() as conn:
        dw.save(conn, targets="10.99.9.0/24")
        calls = []

        def starter(db_path, targets, **kw):
            calls.append((targets, kw))
        assert not dw.tick(conn, datetime(2026, 10, 24, 21, 59), starter=starter), "還沒到點"
        assert not dw.tick(conn, datetime(2026, 10, 23, 22, 30), starter=starter), "不是週六"
        assert dw.tick(conn, datetime(2026, 10, 24, 22, 0), starter=starter)
        assert calls[0][0] == "10.99.9.0/24"
        assert calls[0][1]["full"] is True and calls[0][1]["trigger"] == "weekly"
        assert not dw.tick(conn, datetime(2026, 10, 24, 23, 0), starter=starter), "同一天不跑第二次"
        assert dw.next_run(conn, datetime(2026, 10, 24, 23, 0)) == "2026-10-31 22:00"
        dw.save(conn, enabled=False)
        assert not dw.tick(conn, datetime(2026, 10, 31, 22, 1), starter=starter), "關掉不跑"


def test_每週_有一輪在跑就等_背景或同步都算(monkeypatch):
    from datetime import datetime

    import dispatch_weekly as dw

    with _db() as conn:
        dw.save(conn, targets="10.99.9.0/24")
        calls = []
        sat = datetime(2026, 10, 24, 22, 0)
        monkeypatch.setattr(cd, "_bg_running", True)
        assert not dw.tick(conn, sat, starter=lambda *a, **k: calls.append(1))
        monkeypatch.setattr(cd, "_bg_running", False)
        conn.execute("INSERT INTO collect_dispatch_run (trigger, status, started_at, target_count) "
                     "VALUES ('manual','running', datetime('now','localtime'), 900)")
        conn.commit()
        assert not dw.tick(conn, sat, starter=lambda *a, **k: calls.append(1)), "同步跑的那輪也算"
        assert calls == []


def test_每週_到點三小時後才開開關_不補跑_等下週():
    from datetime import datetime

    import dispatch_weekly as dw

    with _db() as conn:
        dw.save(conn, targets="10.99.9.0/24")
        assert dw.is_due(conn, datetime(2026, 10, 25, 0, 30)) is False, "週日凌晨不是週六"
        assert dw.is_due(conn, datetime(2026, 10, 24, 23, 59)) is True
        dw.save(conn, time="18:00")
        assert dw.is_due(conn, datetime(2026, 10, 24, 22, 30)) is False, "超過 3 小時補跑窗"


def test_每週範圍_手動補跑一小段不會把大範圍縮小():
    import dispatch_weekly as dw

    with _db() as conn:
        assert dw.remember_full_targets(conn, "10.99.8.0/24\n10.99.9.0/24", 508)
        assert not dw.remember_full_targets(conn, "10.99.9.0/24", 254), "子集不蓋掉"
        assert dw.settings(conn)["targets"] == "10.99.8.0/24\n10.99.9.0/24"
        assert dw.remember_full_targets(conn, "10.99.8.0/23\n10.99.10.0/24", 762), "涵蓋舊範圍才換"


def test_每週_設定格式擋下_最後一次結果看得到():
    import dispatch_weekly as dw

    with _db() as conn:
        with pytest.raises(ValueError):
            dw.save(conn, time="25:00")
        with pytest.raises(ValueError):
            dw.save(conn, weekday=7)
        with pytest.raises(ValueError):
            dw.save(conn, targets="這不是IP")
        cd.run_dispatch(conn, "10.99.9.40\n10.99.9.41", prober={"10.99.9.40": [], "10.99.9.41": None}.get,
                        ttl_prober=lambda ip: None, gap_seconds=0, trigger="weekly")
        last = dw.status(conn)["last_run"]
        assert last["status"] == "ok" and last["done"] == 2 and last["alive"] == 1


def test_排程器有掛每週收集_前端有卡片_白天預設只掃已登記():
    sched = (ROOT / "APP" / "asset-module" / "backend" / "scan_service.py").read_text(encoding="utf-8")
    assert "_dispatch_weekly_tick(conn)" in sched
    assert "/api/collect/dispatch/weekly" in SETTINGS_VUE and "saveWeekly" in SETTINGS_VUE
    assert "const dRegOnly = ref(true)" in ADOPT_VUE


def test_前端_開始收集有只掃已登記勾選():
    assert 'v-model="dRegOnly"' in ADOPT_VUE and "registered_only: dRegOnly.value" in ADOPT_VUE
    # v1.471.2 推翻：空白絕不代表全部（勾了只掃已登記也一樣要輸入範圍）
    assert "!dRegOnly.value && !targets.value.trim()" not in ADOPT_VUE


# ===================== 4. 逾時縮短後：以前活過的再試一次 =====================

def test_沒回應但以前活過_用較長逾時再試一次_沒活過的不重試():
    with _db() as conn:
        first = {"10.99.9.30": [22], "10.99.9.31": None}
        cd.run_dispatch(conn, "\n".join(first), prober=first.get, ttl_prober=lambda ip: None,
                        ssh_prober=lambda ip: (False, "denied"), gap_seconds=0)
        retried = []

        def retry(ip):
            retried.append(ip)
            return [22]
        out = cd.run_dispatch(conn, "10.99.9.30\n10.99.9.31", prober=lambda ip: None,
                              retry_prober=retry, ttl_prober=lambda ip: None,
                              ssh_prober=lambda ip: (False, "denied"), gap_seconds=0)
        assert retried == ["10.99.9.30"], "只有以前活過的才重試"
        by_ip = {r["ip"]: r for r in out["results"]}
        assert by_ip["10.99.9.30"]["alive"] == 1 and by_ip["10.99.9.31"]["alive"] == 0


def test_存活掃描看過的也算以前活過():
    with _db() as conn:
        conn.execute("INSERT INTO scan_history (ip, scan_ok) VALUES ('10.99.9.32', 1)")
        conn.commit()
        assert cd._ever_alive(conn, ["10.99.9.32", "10.99.9.33"]) == {"10.99.9.32"}


def test_探測設定_預設64並行400毫秒_上限128_前端有欄位():
    with _db() as conn:
        assert cd.probe_settings(conn) == (64, 0.4)
        cfg = cd.save_dispatch_settings(conn, {"probe_workers": 64, "probe_timeout_ms": 600})
        assert (cfg["probe_workers"], cfg["probe_timeout_ms"]) == (64, 600)
        with pytest.raises(ValueError):
            cd.save_dispatch_settings(conn, {"probe_workers": 129})
    assert "dEdit.probe_workers" in SETTINGS_VUE and "dEdit.probe_timeout_ms" in SETTINGS_VUE


def test_每週_開著沒範圍_亮紅提醒_不自己猜範圍():
    """決策 WEEKLY-RANGE1（使用者 2026-10-20）：「畫面亮紅提醒就好，不讓系統自己猜範圍」。"""
    import dispatch_weekly as dw

    adopt = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "adopt.vue").read_text(encoding="utf-8")
    with _db() as conn:
        s = dw.status(conn)
        assert s["range_missing"] is True and s["targets"] == "", "開著、沒範圍 → 要提醒，而且範圍維持空白（沒猜）"
        assert dw.next_run(conn) is None
        dw.save(conn, enabled=False)
        assert dw.status(conn)["range_missing"] is False, "關掉就不提醒"
        dw.save(conn, enabled=True, targets="10.99.9.0/24")
        assert dw.status(conn)["range_missing"] is False
    # 兩處畫面：6 系統設定 → 掃描排程、2-1 開始收集（帶連結到系統設定）
    assert 'v-if="wCfg.range_missing"' in SETTINGS_VUE and "var(--bad)" in SETTINGS_VUE
    assert 'v-if="weeklyNoRange"' in adopt
    assert "每週整片收集還沒有範圍：先手動跑一次整片網段，或在" in adopt
    assert 'to="/settings?tab=schedule"' in adopt


def test_畫面_空白不開跑_勾了沒加入要講_大量要確認_篩空是提示不是失敗():
    # 2026-10-20 公司機：勾了網段沒按「加入範圍」→ 框空著 → 跑了全部 4565 個登記 IP
    for k in ("空白不代表全部", "還沒按「加入範圍」", "CONFIRM_OVER", "no_registered_in_range",
              "取消「只掃已登記」再收一次", "單一 IP 不受「只掃已登記」限制",
              ':disabled="dispatching || !targets.trim()"'):
        assert k in ADOPT_VUE, k
