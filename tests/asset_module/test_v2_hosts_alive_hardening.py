"""2.1 批次偵測存活的安全補強（2.041.98，GPT 獨立審查必修 1～4）。

1 只探測私有位址白名單；2 只偵測現役狀態白名單；3 全域互斥／同 IP 5 分鐘沿用／逾時停止派送並回報仍在跑的數量；
4 報告保留上限（同一交易刪最舊整份，舊頁格式不變）。fixture 與假探測沿用 test_v2_hosts_alive.py；IP 一律 10.99.x 或明確的黑名單測資。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import host_ping  # noqa: E402
import v2_alive  # noqa: E402
from test_v2_hosts_alive import CALLS, DEAD_OUT, _by_serial, env  # noqa: E402,F401


def _add(p, rows):
    conn = db.get_connection(p)
    try:
        for sn, ip, st in rows:
            db.insert_hardware(conn, asset_serial=sn, hostname="h-" + sn.lower(), ip=ip, asset_status=st, os="Red Hat Enterprise Linux 9")
        conn.commit()
    finally:
        conn.close()


def test_只探測私有位址_其餘略過並講原因(env):
    c, p, _ = env
    bad = [("N-1", "127.0.0.1"), ("N-2", "169.254.1.1"), ("N-3", "8.8.8.8"), ("N-4", "0.0.0.0"), ("N-5", "224.0.0.1"),
           ("N-6", "172.32.0.1"), ("N-7", "255.255.255.255"), ("N-8", "100.64.0.1")]
    _add(p, [(sn, ip, "使用中") for sn, ip in bad] + [("G-1", "172.16.5.5", "使用中"), ("G-2", "10.99.0.1", "使用中"), ("G-3", "10.99.7.7", "使用中")])
    rep = c.post("/api/v2/hosts/alive-check", json={"serials": [sn for sn, _ in bad] + ["G-1", "G-2", "G-3"]}).json()
    rows = _by_serial(rep)
    for sn, ip in bad:
        assert rows[sn]["state"] == "skipped", ip
    assert not {ip for _, ip in bad} & set(CALLS), "不在白名單的位址不得有任何連線"
    assert all(rows[s]["state"] in ("alive", "no_response") for s in ("G-1", "G-2", "G-3"))
    assert "私有位址" in rows["N-3"]["verdict"]


def test_只偵測現役白名單_空白未知停用報廢全略過(env):
    c, p, _ = env
    _add(p, [("S-1", "10.99.3.1", ""), ("S-2", "10.99.3.2", "維修中"), ("S-3", "10.99.3.3", "停用"), ("S-4", "10.99.3.4", "報廢"),
             ("S-5", "10.99.3.5", "待上線"), ("AUTO-9", "10.99.3.6", ""), ("S-6", "10.99.3.7", "1"), ("S-7", "10.99.3.8", "閒置")])
    rep = c.post("/api/v2/hosts/alive-check", json={"serials": ["S-1", "S-2", "S-3", "S-4", "S-5", "AUTO-9", "S-6", "S-7"]}).json()
    rows = _by_serial(rep)
    for sn in ("S-1", "S-2", "S-3", "S-4", "S-6", "S-7"):
        assert rows[sn]["state"] == "skipped", sn
    assert "空白" in rows["S-1"]["verdict"] and "不是已知" in rows["S-2"]["verdict"] and "非現役" in rows["S-4"]["verdict"]
    assert rows["S-5"]["state"] != "skipped", "待上線屬現役"
    assert rows["AUTO-9"]["state"] != "skipped", "非 CIA 來源序號本來沒有 CIA 狀態，放行"
    assert not {"10.99.3.1", "10.99.3.2", "10.99.3.3", "10.99.3.4", "10.99.3.7", "10.99.3.8"} & set(CALLS)


def test_全域互斥_已有偵測在進行回409(env):
    c, _, _ = env
    assert v2_alive._RUN_LOCK.acquire(blocking=False)
    try:
        r = c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1"]})
        assert r.status_code == 409 and "已有偵測在進行" in r.json()["detail"]
        assert not CALLS
    finally:
        v2_alive._RUN_LOCK.release()
    assert c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1"]}).status_code == 200


def test_上一批探測還在背景跑_新的一批也擋(env, monkeypatch):
    c, _, _ = env
    monkeypatch.setattr(host_ping, "_INFLIGHT", 2)
    r = c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1"]})
    assert r.status_code == 409 and "背景" in r.json()["detail"] and not CALLS
    monkeypatch.setattr(host_ping, "_INFLIGHT", 0)


def test_同IP五分鐘內不重複探測_沿用上次結果(env):
    c, _, _ = env
    first = c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1", "H-3"]}).json()
    n = len(CALLS)
    assert n > 0
    second = c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1", "H-3", "H-2"]}).json()
    assert set(CALLS[n:]) <= {"10.99.1.2"}, "只有第一次沒測過的 H-2 會真的探測"
    rows = _by_serial(second)
    assert rows["H-1"]["verdict"].startswith("沿用") and rows["H-3"]["verdict"].startswith("沿用")
    assert not rows["H-2"]["verdict"].startswith("沿用")
    assert second["reused"] == 2 and rows["H-1"]["state"] == _by_serial(first)["H-1"]["state"]
    assert c.get(f"/api/v2/hosts/alive-runs/{second['run_id']}").json()["reused"] == 2
    v2_alive.reset_cache()                      # 超過 5 分鐘（快取過期）就重新探測
    n2 = len(CALLS)
    c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1"]})
    assert len(CALLS) > n2


def test_逾時後停止派送_仍在執行的數量算進報告():
    import time

    seen = []

    def slow_run(cmd, timeout):
        seen.append(cmd[-1])
        time.sleep(0.5)
        return 1, DEAD_OUT

    targets = [{"asset_serial": f"T-{i}", "hostname": f"t{i}", "ip": f"10.99.9.{i + 1}"} for i in range(4)]
    out, hit, running = host_ping.check_many(targets, runner=slow_run, prober=lambda ip, *a, **k: None,
                                             workers=1, deadline=0.15, with_flag=True)
    assert hit is True and running == 1, "只有已經開始的那一個還在跑"
    assert [r["result"] for r in out] == ["unknown"] * 4
    time.sleep(1.5)
    assert len(seen) == 1, "到期後不得再派送新的探測"
    assert host_ping.inflight() == 0


def test_報告保留上限_超過刪最舊整份且舊頁仍可讀(env, monkeypatch):
    c, p, _ = env
    monkeypatch.setattr(host_ping, "MAX_RUNS", 2)
    ids = []
    for _i in range(4):
        v2_alive.reset_cache()
        ids.append(c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1", "E-1"]}).json()["run_id"])
    lst = c.get("/api/v2/hosts/alive-runs").json()["items"]
    assert [x["run_id"] for x in lst] == [ids[3], ids[2]]
    assert c.get(f"/api/v2/hosts/alive-runs/{ids[0]}").status_code == 404
    assert c.get(f"/api/tools/alive-check/runs/{ids[1]}").status_code == 404
    assert c.get(f"/api/tools/alive-check/runs/{ids[3]}").json()["total"] == 1       # 舊頁格式不變
    conn = db.get_connection(p)
    try:
        assert conn.execute("SELECT COUNT(*) FROM alive_check_run").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM alive_run_extra").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(DISTINCT run_id) FROM alive_check_item").fetchone()[0] == 2
    finally:
        conn.close()


def test_保留策略寫進說明():
    src = (ROOT / "APP" / "asset-module" / "frontend" / "components" / "v2" / "HostAlive.vue").read_text(encoding="utf-8")
    assert "最近 200 份" in src and "5 分鐘" in src and host_ping.MAX_RUNS == 200


# ===== GPT 複查（2.041.99）=====
def test_現役白名單是明確的兩個值_不由其他集合推導(monkeypatch):
    import lifecycle

    assert v2_alive.ACTIVE_STATUS == {"使用中", "待上線"}
    monkeypatch.setattr(lifecycle, "VALID_STATUS_VALUES", lifecycle.VALID_STATUS_VALUES | {"維修中"})
    assert v2_alive.status_ok("S-1", "維修中")[0] is False, "其他地方新增合法值不會自動變成可偵測"
    assert v2_alive.status_ok("S-1", "使用中")[0] and v2_alive.status_ok("S-1", "待上線")[0]


def test_無法判斷也沿用_五分鐘內不重測(env, monkeypatch):
    c, _, _ = env
    monkeypatch.setattr(host_ping, "icmp", lambda ip, runner=None, **kw: {"ran": False, "sent": 3, "received": None,
                                                                          "avg_ms": None, "reason": "沒有 ping", "output": ""})
    first = c.post("/api/v2/hosts/alive-check", json={"serials": ["H-3"]}).json()
    assert _by_serial(first)["H-3"]["state"] == "unknown"
    n = len(CALLS)
    second = c.post("/api/v2/hosts/alive-check", json={"serials": ["H-3"]}).json()
    assert len(CALLS) == n, "無法判斷的 IP 5 分鐘內也不重複探測"
    assert _by_serial(second)["H-3"]["verdict"].startswith("沿用") and second["reused"] == 1


def test_逾時沒跑完的不記入沿用(env, monkeypatch):
    import time

    c, _, _ = env

    def slow(ip, runner=None, **kw):
        time.sleep(0.5)
        return {"ran": True, "sent": 3, "received": 0, "avg_ms": None, "reason": None, "output": ""}

    monkeypatch.setattr(host_ping, "icmp", slow)
    monkeypatch.setattr(v2_alive, "BATCH_DEADLINE", 0.1)
    c.post("/api/v2/hosts/alive-check", json={"serials": ["H-1"]})
    assert "10.99.1.1" not in v2_alive._RECENT


def test_逾時後不可能再開始探測_計數與停止旗標同一把鎖(monkeypatch):
    import threading
    import time

    started = []

    def slow_run(cmd, timeout):
        started.append(cmd[-1])
        time.sleep(0.3)
        return 1, DEAD_OUT

    # 讓派送剛好卡在「拿鎖前」：先拿走鎖，到期 stop.set() 也要等這把鎖，放開後排隊的任務一定看到停止旗標
    targets = [{"asset_serial": f"R-{i}", "hostname": f"r{i}", "ip": f"10.99.8.{i + 1}"} for i in range(6)]
    out, hit, running = host_ping.check_many(targets, runner=slow_run, prober=lambda ip, *a, **k: None,
                                             workers=2, deadline=0.05, with_flag=True)
    assert hit and running == len(started) <= 2
    time.sleep(1.0)
    assert len(started) == running, "到期後沒有任何新的探測開始"
    assert host_ping.inflight() == 0
    assert all(r.get("probed") is False for r in out)
    # 原始碼守門：加計數與檢查停止旗標在同一個 with 區塊、stop.set() 也在同一把鎖內
    import inspect

    src = inspect.getsource(host_ping.check_many)
    assert "_INFLIGHT += 1\n            if stop.is_set():" in src
    assert "with _INFLIGHT_LOCK:                  # 先停派送" in src and threading
