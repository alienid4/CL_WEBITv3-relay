"""納管／重新納管後「六樣都收一次」——每條入口端到端（2026-10-18 使用者：「我再說一次」）。

症狀：公司機重新納管一台 RHEL 後，資產頁 OS 重建「尚未收集」、組態檢核空白。
查到的根因（每一條都有一個測試釘住）：

1. 組態檢核收到了，但**掛不到這台**：Linux 腳本的「IP地址」是 `hostname -I` 的全部 IP（空白分隔），
   IP 永遠比不中；主機名又常跟清冊不同 → 掛成「對不到」或掛到同 IP 的另一筆。
   一鍵收集明明知道是哪一台，卻丟掉序號重新比對。
2. 資產頁的「納管後自動收集」結果**永遠是空的**：端點讀 last["extra"]，但紀錄放在 last["detail"]。
3. 單台納管：六樣收集排在「試連／收 facts」同一個 try 裡，那兩步一丟例外就一樣都不跑、沒有紀錄；
   而且收不到 facts 時寫的 collect_ok=0／納管紀錄沒 commit，請求結束就被回滾。
4. 上一輪還在跑時重新納管：start() 靜默回 False，重新納管後的新權限從來沒被收。
5. 批次：收尾一丟例外就 `pass`，六樣一樣都沒跑、沒有紀錄。
6. 讀取端只看「這一筆」：收集掛在同一台（machine_key）的另一筆時，這頁說「尚未收集」。

SSH 全部是假的（不連任何主機）；OS 重建與組態檢核走**真的**收集器與**真的**讀取 API。
"""
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

import account_inventory  # noqa: E402
import auto_onboard  # noqa: E402
import batch_onboard_service as svc  # noqa: E402
import config_audit  # noqa: E402
import db  # noqa: E402
import health_probe  # noqa: E402
import host_spec_collector  # noqa: E402
import manage_state  # noqa: E402
import onboard_engine  # noqa: E402
import os_rebuild_collector  # noqa: E402
import post_onboard  # noqa: E402
import service_inventory  # noqa: E402
import software_inventory  # noqa: E402
from test_linux_config_audit import _as_webit, _fixture_text  # noqa: E402
from test_os_rebuild_collector import FakeHost  # noqa: E402

IP = "10.99.4.140"
SERIAL = "HW-140T"
SIX = set(post_onboard.JOBS)
#: Linux 實際會呼叫到收集器的幾樣：「排程」在 Linux 讀 OS 重建資料、略過不另收（2026-10-20）
CALLED = SIX - {"schedule"}


def _report_text() -> str:
    """真實腳本的表頭：IP 是全部 IP（空白分隔）、主機名跟清冊不一樣——都比不中。"""
    t = _as_webit(_fixture_text(), summary=True)
    out = []
    for ln in t.splitlines():
        if ln.startswith("IP地址:"):
            ln = "IP地址: 10.99.77.1 10.99.88.1 10.99.99.1"
        elif ln.startswith("主機名:"):
            ln = "主機名: kafka01"
        out.append(ln)
    return "\n".join(out) + "\n"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    monkeypatch.setenv("ASSET_DB_PATH", str(p))
    db.init_db(p)
    fake = FakeHost(nonroot={"hosts": "127.0.0.1 localhost"})

    def runner(ip, cmd):
        if "__FCBRHEL_EOF__" in cmd:
            return _report_text()
        return fake(ip, cmd)

    class R:
        def __init__(self, *a, **k):
            pass

        def __call__(self, ip, cmd):
            return runner(ip, cmd)

    monkeypatch.setattr(manage_state, "_ssh_runner", lambda *a, **k: R())
    monkeypatch.setattr(manage_state, "probe_collect",
                        lambda ip, key, account=None, runner=None: (True, None))
    monkeypatch.setattr(health_probe, "check_one", lambda *a, **k: {})

    # 六樣都包一層計數（照樣呼叫真的收集器）
    called: list[str] = []

    def spy(mod, name, job):
        real = getattr(mod, name)

        def wrapped(*a, **k):
            called.append(job)
            return real(*a, **k)
        monkeypatch.setattr(mod, name, wrapped)

    spy(host_spec_collector, "run_collection", "spec")
    spy(service_inventory, "collect_services", "service")
    spy(software_inventory, "collect_software", "software")
    spy(account_inventory, "collect_accounts", "account")
    spy(os_rebuild_collector, "run_collection", "rebuild")
    spy(config_audit, "collect_from_host", "config_audit")
    return p, called


def _add(p, serial=SERIAL, ip=IP, hostname="demo-140t", os_text="RedHat 8.8", collect_ok=0):
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial=serial, hostname=hostname, ip=ip, os=os_text,
                       environment="測試", asset_status="使用中")
    c.execute("UPDATE hardware SET collect_ok=? WHERE asset_serial=?", (collect_ok, serial))
    c.commit()
    c.close()


def _client():
    import api
    from fastapi.testclient import TestClient
    # 全套平行跑時，同一個 worker 先前的測試可能留下別的 get_db 覆寫（指到別人的暫存 DB）——
    # 這裡明確釘回本測試的 ASSET_DB_PATH，不靠「剛好沒人留」。
    api.app.dependency_overrides.clear()

    def _db():
        conn = db.get_connection()
        try:
            yield conn
        finally:
            conn.close()
    api.app.dependency_overrides[api.get_db] = _db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester", "role": "admin"}
    return api, TestClient(api.app, raise_server_exceptions=False)


def _wait_idle():
    for _ in range(200):
        if not post_onboard._running:
            return
        time.sleep(0.05)
    raise AssertionError("納管後自動收集一直沒跑完")


def _assert_readable(cl, serial=SERIAL):
    """資產頁用的同一批 API 讀得到：六樣結果、OS 重建、組態檢核。"""
    pc = cl.get(f"/api/onboard/post-collect/{serial}").json()
    assert {r["job"] for r in pc["results"]} == SIX, pc
    by = {r["job"]: r for r in pc["results"]}
    assert by["rebuild"]["ok"] is True, by["rebuild"]
    assert by["config_audit"]["ok"] is True, by["config_audit"]
    rb = cl.get(f"/api/host-spec/rebuild?asset_serial={serial}").json()["host_rebuild"]
    assert rb and rb["collected_at"], "OS 重建分頁讀不到"
    ca = cl.get(f"/api/config-audit/by-asset?asset_serial={serial}").json()
    assert ca["state"] == "有檢核結果" and len(ca["items"]) == 61, ca["state"]


class _OK:
    ok = True
    stage = "done"
    message = "ok"
    output = ""
    root_key = False      # ROOTKEY1：腳本沒說佈了 root 金鑰


def _onboard(monkeypatch, cl):
    monkeypatch.setattr(onboard_engine, "onboard", lambda **k: _OK())
    monkeypatch.setattr(onboard_engine, "resolve_collector_ip", lambda conn: "10.99.0.9")
    r = cl.post("/api/onboard", json={"ip": IP, "platform": "linux", "username": "x", "password": "y"})
    assert r.status_code == 200, r.text
    _wait_idle()
    return r.json()


# ===== 單台：新納管 =====

def test_單台新納管_六樣都跑_資產頁讀得到(env, monkeypatch):
    p, called = env
    _add(p, collect_ok=0)
    monkeypatch.setattr(manage_state, "collect_facts_into_assets",
                        lambda conn, **k: {"updated": 3, "failed": []})
    api, cl = _client()
    try:
        _onboard(monkeypatch, cl)
        assert set(called) == CALLED, called
        _assert_readable(cl)
    finally:
        api.app.dependency_overrides.clear()


# ===== 單台：重新納管，而且試連／收 facts 這步丟例外 =====

def test_單台重新納管_收facts炸了六樣照跑(env, monkeypatch):
    p, called = env
    _add(p, collect_ok=1)

    def boom(conn, **k):
        raise RuntimeError("facts 收集逾時")
    monkeypatch.setattr(manage_state, "collect_facts_into_assets", boom)
    api, cl = _client()
    try:
        out = _onboard(monkeypatch, cl)
        assert "facts 收集逾時" in out["message"]          # 收尾的錯照樣講出來
        assert set(called) == CALLED, f"facts 一炸六樣就不跑：{called}"
        _assert_readable(cl)
    finally:
        api.app.dependency_overrides.clear()


def test_單台納管_收不到facts的狀態要存下來不被回滾(env, monkeypatch):
    p, _ = env
    _add(p, collect_ok=1)
    monkeypatch.setattr(manage_state, "collect_facts_into_assets",
                        lambda conn, **k: {"updated": 0, "failed": [{"error": "收不到任何欄位"}]})
    api, cl = _client()
    try:
        out = _onboard(monkeypatch, cl)
        assert out["ok"] is False and out["stage"] == "collect"
        c = db.get_connection(p)
        row = c.execute("SELECT collect_ok, collect_error FROM hardware WHERE asset_serial=?",
                        (SERIAL,)).fetchone()
        assert row["collect_ok"] == 0 and row["collect_error"], "collect_ok=0 被回滾了"
        n = c.execute("SELECT COUNT(*) FROM onboard_audit WHERE stage='collect'").fetchone()[0]
        c.close()
        assert n == 1, "「收集這步敗的」納管紀錄被回滾了"
        # 六樣照樣有一筆紀錄，每一樣講得出原因（不是一句「沒有收到資料」）
        pc = cl.get(f"/api/onboard/post-collect/{SERIAL}").json()
        by = {r["job"]: r for r in pc["results"]}
        assert set(by) == SIX
        assert "還沒納管" in by["rebuild"]["message"], by["rebuild"]
    finally:
        api.app.dependency_overrides.clear()


# ===== 批次（2-1 勾選一次納管／重新納管走的 batch-auto 引擎）=====

def _batch(monkeypatch, p, refresh):
    c = db.get_connection(p)
    run_id = c.execute(
        "INSERT INTO batch_onboard_run (triggered_by, target_count, unify, status, started_at) "
        "VALUES ('t',1,0,'running','2026-10-18 10:00:00')").lastrowid
    c.commit()
    c.close()
    monkeypatch.setattr(onboard_engine, "resolve_collector_ip", lambda conn: "10.99.0.9")
    monkeypatch.setattr(onboard_engine, "collector_pubkey", lambda: "ssh-ed25519 AAAA")
    monkeypatch.setattr(onboard_engine, "resolve_account_comment", lambda conn: "test")
    monkeypatch.setattr(onboard_engine, "batch_auto_onboard", lambda targets, **k: [
        {"ip": targets[0]["ip"], "login_ok": True, "password_index": 0, "platform": "linux",
         "onboarded": True, "password_unified": False, "fail_stage": None, "fail_reason": None}])
    monkeypatch.setattr(manage_state, "refresh_collect_status", refresh)
    monkeypatch.setattr(svc, "_HOST_PAUSE_SEC", 0)
    svc._do_batch(p, run_id, [{"ip": IP, "environment": "測試"}], "root", ["pw"], False)


def test_批次新納管_六樣都跑_資產頁讀得到(env, monkeypatch):
    p, called = env
    _add(p, collect_ok=0)

    def refresh(conn, only_ip=None, **k):
        conn.execute("UPDATE hardware SET collect_ok=1 WHERE ip=?", (only_ip,))
        conn.commit()
    _batch(monkeypatch, p, refresh)
    assert set(called) == CALLED, called
    api, cl = _client()
    try:
        _assert_readable(cl)
    finally:
        api.app.dependency_overrides.clear()


def test_批次重新納管_試連炸了六樣照跑(env, monkeypatch):
    p, called = env
    _add(p, collect_ok=1)

    def refresh(conn, only_ip=None, **k):
        raise RuntimeError("試連逾時")
    _batch(monkeypatch, p, refresh)
    assert set(called) == CALLED, f"試連一炸六樣就不跑：{called}"
    api, cl = _client()
    try:
        _assert_readable(cl)
    finally:
        api.app.dependency_overrides.clear()


def test_批次收尾失敗不可以靜默(env, monkeypatch):
    p, called = env
    _add(p, collect_ok=1)
    monkeypatch.setattr(svc, "_post_onboard", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("補資產失敗")))
    _batch(monkeypatch, p, lambda conn, **k: None)
    c = db.get_connection(p)
    import collect_log
    recs = collect_log.recent(c, kinds=["post_onboard"])
    c.close()
    assert recs and "補資產失敗" in recs[0]["message"]


# ===== 排程自動納管 =====

def test_排程納管_六樣都跑_資產頁讀得到(env, monkeypatch):
    p, called = env
    _add(p, collect_ok=1)
    c = db.get_connection(p)
    try:
        auto_onboard.run_post_onboard(c, [{"action": "onboarded", "ip": IP}])
    finally:
        c.close()
    assert set(called) == CALLED, called
    api, cl = _client()
    try:
        _assert_readable(cl)
    finally:
        api.app.dependency_overrides.clear()


# ===== 同一台兩筆登記：收集掛在另一筆，這筆的資產頁也要讀得到 =====

def test_同一台另一筆登記_資產頁讀得到(env, monkeypatch):
    p, called = env
    # DYN 那筆先進資產表（rowid 小）→ 納管用 IP 找序號會找到它
    _add(p, serial="DYN-" + IP, hostname="demo-140t.corp.example",
         os_text="Red Hat Enterprise Linux 8 (64-bit)", collect_ok=1)
    _add(p, collect_ok=1)
    monkeypatch.setattr(manage_state, "collect_facts_into_assets",
                        lambda conn, **k: {"updated": 3, "failed": []})
    api, cl = _client()
    try:
        _onboard(monkeypatch, cl)
        assert set(called) == CALLED
        _assert_readable(cl, SERIAL)
    finally:
        api.app.dependency_overrides.clear()


# ===== 跑到一半又重新納管：跑完補一輪 =====

def test_跑到一半又重新納管_跑完補一輪(env, monkeypatch):
    p, _ = env
    _add(p, collect_ok=1)
    import threading
    gate = threading.Event()
    rounds = []

    def fake_run_now(conn, serial, by=None, **k):
        rounds.append(serial)
        if len(rounds) == 1:
            gate.wait(5)
        return []
    monkeypatch.setattr(post_onboard, "run_now", fake_run_now)
    assert post_onboard.start(None, SERIAL) is True
    for _ in range(100):
        if rounds:
            break
        time.sleep(0.02)
    assert post_onboard.start(None, SERIAL) is False      # 不並行跑兩輪
    gate.set()
    _wait_idle()
    assert rounds == [SERIAL, SERIAL], f"重新納管後沒有補收：{rounds}"


# ===== 本機執行（貼指令）→ 按「驗證」成功 =====

def test_本機執行驗證成功_六樣都跑_資產頁讀得到(env, monkeypatch):
    p, called = env
    _add(p, collect_ok=0)
    monkeypatch.setattr(manage_state, "collect_facts_into_assets",
                        lambda conn, **k: {"updated": 3, "failed": []})
    api, cl = _client()
    try:
        r = cl.post("/api/onboard/verify", json={"ip": IP, "platform": "linux"})
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        _wait_idle()
        assert sorted(called) == sorted(CALLED), called       # 六樣各一次
        _assert_readable(cl)
    finally:
        api.app.dependency_overrides.clear()


def test_本機執行驗證失敗_不跑六樣(env, monkeypatch):
    p, called = env
    _add(p, collect_ok=0)
    monkeypatch.setattr(manage_state, "collect_facts_into_assets",
                        lambda conn, **k: {"updated": 0, "failed": [{"error": "連不上"}]})
    api, cl = _client()
    try:
        r = cl.post("/api/onboard/verify", json={"ip": IP, "platform": "linux"})
        assert r.json()["ok"] is False, r.text
        _wait_idle()
        assert called == [], called
    finally:
        api.app.dependency_overrides.clear()


# ===== 資產頁「收集全部」按鈕：跟納管後同一套六樣（2026-10-18 使用者：所有收集都要一樣）=====

def test_收集全部按鈕_六樣都跑_資產頁讀得到(env, monkeypatch):
    p, called = env
    _add(p, collect_ok=1)
    api, cl = _client()
    try:
        r = cl.post(f"/api/onboard/post-collect/{SERIAL}")
        assert r.status_code == 200 and r.json()["started"] is True, r.text
        assert {j["job"] for j in r.json()["jobs"]} == SIX
        _wait_idle()
        assert sorted(called) == sorted(CALLED), called       # 六樣各一次
        _assert_readable(cl)
        last = cl.get(f"/api/onboard/post-collect/{SERIAL}").json()["last"]
        assert last["message"].startswith("手動收集全部"), last["message"]
        assert last["actor"] == "tester"
    finally:
        api.app.dependency_overrides.clear()


def test_收集全部按鈕_Windows_OS重建與組態檢核都照收(env, monkeypatch):
    # 2026-10-21：Windows 跑公司基準 286 條（需本機管理員），組態檢核照收；OS 重建也有 Windows 版（WinRM）了
    p, called = env
    _add(p, collect_ok=1, os_text="Windows Server 2019")
    api, cl = _client()
    try:
        assert cl.post(f"/api/onboard/post-collect/{SERIAL}").json()["started"] is True
        _wait_idle()
        assert "rebuild" in called and "config_audit" in called, called
        by = {x["job"]: x for x in cl.get(f"/api/onboard/post-collect/{SERIAL}").json()["results"]}
        assert set(by) == SIX
        assert by["rebuild"]["ok"] is not None and by["config_audit"]["ok"] is not None, by
    finally:
        api.app.dependency_overrides.clear()


def test_收集全部按鈕_VMware六樣一律略過不SSH(env, monkeypatch):
    p, called = env
    _add(p, collect_ok=1, hostname="esx01", os_text="VMware ESXi 7.0.3")
    api, cl = _client()
    try:
        cl.post(f"/api/onboard/post-collect/{SERIAL}")
        _wait_idle()
        by = {x["job"]: x for x in cl.get(f"/api/onboard/post-collect/{SERIAL}").json()["results"]}
        assert set(by) == SIX
        for j in SIX:
            assert by[j]["ok"] is None and "VMware" in by[j]["message"], by[j]
        assert called == [], f"ESXi 天條：不可以對它跑任何 SSH 收集：{called}"
    finally:
        api.app.dependency_overrides.clear()


def test_收集全部按鈕_資產不存在回404(env):
    api, cl = _client()
    try:
        assert cl.post("/api/onboard/post-collect/NOPE").status_code == 404
    finally:
        api.app.dependency_overrides.clear()


def test_批次撞上正在收的同一台_不並行_跑完補一輪(env, monkeypatch):
    """單台納管的背景收集還在跑時，批次／排程又輪到同一台：不同時跑兩輪，改成那輪跑完再補一輪。"""
    p, _ = env
    _add(p, collect_ok=1)
    import threading
    gate = threading.Event()
    rounds = []

    def fake_run_now(conn, serial, by=None, **k):
        rounds.append(by)
        if len(rounds) == 1:
            gate.wait(5)
        return []
    monkeypatch.setattr(post_onboard, "run_now", fake_run_now)
    assert post_onboard.start(None, SERIAL, by="single") is True
    for _ in range(100):
        if rounds:
            break
        time.sleep(0.02)
    c = db.get_connection(p)
    try:
        out = auto_onboard.run_post_onboard(c, [{"action": "onboarded", "ip": IP}])
    finally:
        c.close()
    assert rounds == ["single"], f"批次跟單台同時對同一台跑：{rounds}"
    assert "補一輪" in out[0]["message"]
    gate.set()
    _wait_idle()
    assert rounds == ["single", "single"], f"沒有補一輪：{rounds}"


# ===== 同 IP 有一筆停用的舊登記（IP 回收）：納管要掛到現役那筆（2026-10-18 ACCEPT-E2E1）=====
# 原本五個入口都寫 `SELECT asset_serial FROM hardware WHERE ip = ?` 不排序取第一筆——
# 拿到的是最早建的那筆，也就是停用的舊機器。facts、六樣全掛到舊登記，現役那筆的資產頁
# 「尚未收集 OS 重建資料」、組態檢核空白，而且退役跟現役不算同一台，連紀錄都看不到。

OLD = "HW-OLD-140"


def _add_retired_first(p):
    _add(p, serial=OLD, hostname="old-box-140", os_text="CentOS 6", collect_ok=0)
    c = db.get_connection(p)
    c.execute("UPDATE hardware SET asset_status='停用' WHERE asset_serial=?", (OLD,))
    c.commit()
    c.close()


def test_同IP有停用舊登記_網頁一鍵重新納管_掛到現役那筆(env, monkeypatch):
    p, called = env
    _add_retired_first(p)
    _add(p, collect_ok=1)
    seen = []
    real = manage_state.collect_facts_into_assets

    def facts(conn, only_serial=None, **k):
        seen.append(only_serial)
        return {"updated": 3, "failed": []}
    monkeypatch.setattr(manage_state, "collect_facts_into_assets", facts)
    api, cl = _client()
    try:
        out = _onboard(monkeypatch, cl)
        assert out["ok"] is True, out
        assert seen == [SERIAL], f"facts 收到停用的舊登記上：{seen}"
        _assert_readable(cl, SERIAL)
        assert cl.get(f"/api/onboard/post-collect/{SERIAL}").json()["last"]["target"] == SERIAL
    finally:
        api.app.dependency_overrides.clear()
    assert real is not None


def test_同IP有停用舊登記_本機執行驗證_掛到現役那筆(env, monkeypatch):
    p, called = env
    _add_retired_first(p)
    _add(p, collect_ok=1)
    monkeypatch.setattr(manage_state, "collect_facts_into_assets",
                        lambda conn, **k: {"updated": 3, "failed": []})
    api, cl = _client()
    try:
        r = cl.post("/api/onboard/verify", json={"ip": IP, "platform": "linux"})
        assert r.status_code == 200 and r.json()["asset_serial"] == SERIAL, r.text
        _wait_idle()
        _assert_readable(cl, SERIAL)
    finally:
        api.app.dependency_overrides.clear()


def test_同IP有停用舊登記_取得納管指令自動偵測_掛到現役那筆(env, monkeypatch):
    p, called = env
    _add_retired_first(p)
    _add(p, collect_ok=1)
    import paste_watch
    from datetime import datetime
    c = db.get_connection(p)
    paste_watch.issue(c, [IP], "tester", platform_of=lambda ip: "linux")
    c.commit()
    started = []
    out = paste_watch._try_one(
        c, {"ip": IP, "platform": "linux", "issued_by": "tester"}, datetime.now(),
        probe=lambda ip, acct: (True, None), root_probe=lambda ip: False,
        collect=lambda serial: {"updated": 3},
        post_start=lambda s, by: (started.append(s), post_onboard.start(None, s, by=by)),
        source_ip=None)
    c.close()
    assert out["asset_serial"] == SERIAL and started == [SERIAL], (out, started)
    _wait_idle()
    api, cl = _client()
    try:
        st = cl.get(f"/api/onboard/paste/status?ips={IP}").json()["rows"][0]   # 2-1 畫面輪詢的那支
        assert st["state"] == "ok" and st["asset_serial"] == SERIAL, st
        _assert_readable(cl, SERIAL)
    finally:
        api.app.dependency_overrides.clear()


def test_serial_for_ip_挑法(env):
    p, _ = env
    _add_retired_first(p)
    _add(p, serial="DYN-" + IP, hostname="demo-140t", collect_ok=1)
    _add(p, collect_ok=1)
    c = db.get_connection(p)
    try:
        assert manage_state.serial_for_ip(c, IP) == SERIAL          # 現役＞已納管＞非 DYN
        c.execute("UPDATE hardware SET collect_ok=0 WHERE asset_serial=?", (SERIAL,))
        assert manage_state.serial_for_ip(c, IP) == "DYN-" + IP     # 已納管優先
        assert manage_state.serial_for_ip(c, "10.99.9.9") is None
    finally:
        c.close()


# ===== 一次重新納管幾百台：前面那台的六樣紀錄不可以被擠掉／找不到 =====

def test_一次重新納管幾百台_前面那台資產頁仍讀得到六樣結果(env, monkeypatch):
    p, _ = env
    _add(p, collect_ok=1)
    import collect_log
    c = db.get_connection(p)
    collect_log.record(c, "post_onboard", SERIAL, True, "納管後自動收集：收到 …",
                       extra={"results": [{"job": "rebuild", "label": "OS 重建資料", "ok": True,
                                           "skipped": False, "message": "收到了"}]})
    for i in range(collect_log.MAX_ROWS + 50):          # 後面幾百台各一筆，把總筆數擠爆
        collect_log.record(c, "post_onboard", f"HW-{i:04d}", True, "x")
    c.close()
    api, cl = _client()
    try:
        r = cl.get(f"/api/onboard/post-collect/{SERIAL}").json()
        assert r["last"] and r["last"]["target"] == SERIAL, "這台的紀錄被擠掉／找不到"
        assert r["results"][0]["job"] == "rebuild"
    finally:
        api.app.dependency_overrides.clear()


def test_排隊中與正在收第幾樣_資產頁看得到(env, monkeypatch):
    p, _ = env
    _add(p, collect_ok=1)
    import threading
    gate = threading.Event()
    real = post_onboard._collect_one

    def slow(conn, job, serial):
        if job == "rebuild":
            gate.wait(5)
        return real(conn, job, serial)
    monkeypatch.setattr(post_onboard, "_collect_one", slow)
    api, cl = _client()
    try:
        assert cl.post(f"/api/onboard/post-collect/{SERIAL}").json()["started"] is True
        prog = None
        for _ in range(200):
            prog = cl.get(f"/api/onboard/post-collect/{SERIAL}").json()["progress"]
            if prog and prog.get("job") == "rebuild":
                break
            time.sleep(0.02)
        assert prog and prog["state"] == "running" and prog["label"] == "OS 重建資料" and prog["step"] == post_onboard.JOBS.index("rebuild") + 1, prog
        gate.set()
        _wait_idle()
        assert cl.get(f"/api/onboard/post-collect/{SERIAL}").json()["progress"] is None
    finally:
        gate.set()
        api.app.dependency_overrides.clear()
