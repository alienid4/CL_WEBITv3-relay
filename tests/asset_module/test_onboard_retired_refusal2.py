"""退役／報廢主機擋點的 GPT 審查補強（04a57c24 審查 #1/#3/#4/#6/#7/#8/#11/#12/#13）。例子 IP 一律 10.99.x。

守：多 IP 欄、整台機器全部登記、閒置／報廢字樣、前後空白、批次 SQL 次數 O(批次數)、固定錯誤字樣不洩例外類型、
背景／間接入口（paste 發權杖與取回、hint、憑證測試、自動納管、批次自動納管、收集全部）都是零連線。
已知限制（不修）：CIA 正本狀態空白＝當現役（不確定就放行）；本機下載通用 bootstrap 腳本後在退役主機自行執行不在後端擋的範圍。
"""
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_v2_port_b23 import ROOT

sys.path.insert(0, str(ROOT / "tests" / "asset_module"))
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

from test_v2_port_b23_review4 import env  # noqa: E402,F401

RET, MULTI_A, MULTI_B, IDLE = "10.99.0.21", "10.99.0.31", "10.99.0.32", "10.99.0.41"
DUP_IP, BLANK_IP, HS_A, HS_B = "10.99.0.51", "10.99.0.61", "10.99.0.71", "10.99.0.72"


@pytest.fixture
def env3(env):
    client, tried, tmp = env
    import db

    dbp = Path(tmp) / "t.db"
    conn = db.get_connection(dbp)
    h = db.insert_hardware
    h(conn, asset_serial="R-1", ip=RET, hostname="old-example-01", asset_status="報廢")
    h(conn, asset_serial="ML-1", ip=f"{MULTI_A},{MULTI_B}", hostname="multi-example-01", asset_status="報廢")
    h(conn, asset_serial="I-1", ip=IDLE, hostname="idle-example-01", asset_status="閒置")
    h(conn, asset_serial="S-1", ip=DUP_IP, hostname="dup-example-01", asset_status="報廢")      # 同機：退役正式列
    h(conn, asset_serial="DYN-S1", ip=DUP_IP, hostname="dup-example-01", asset_status="")        # ＋狀態空白的系統自建重複列
    h(conn, asset_serial="B-1", ip=BLANK_IP, hostname="blank-example-01", asset_status="")       # CIA 正本狀態空白＝當現役（已知限制）
    h(conn, asset_serial="B-2", ip=BLANK_IP, hostname="blank-example-01", asset_status="報廢")
    h(conn, asset_serial="H-1", ip=HS_A, hostname="hs-example-01", asset_status="報廢")           # 同主機名、不同 IP
    h(conn, asset_serial="DYN-H2", ip=HS_B, hostname="hs-example-01", asset_status="")
    conn.commit()
    conn.close()
    try:
        yield client, tried, dbp
    finally:
        while _OPEN:
            _OPEN.pop().close()      # Windows：連線沒關，暫存目錄刪不掉


_OPEN: list = []


def _conn(dbp):
    import db

    c = db.get_connection(dbp)
    _OPEN.append(c)
    return c


def _post(client, ip):
    return client.post("/api/onboard", json={"ip": ip, "platform": "linux", "username": "root", "password": "x1x"})


def test_多IP欄位_任一個IP都擋_空白也不影響(env3):
    client, tried, dbp = env3
    import onboard_eligibility as oe

    conn = _conn(dbp)
    got = oe.retired_targets(conn, [f" {MULTI_A} ", MULTI_B, "10.99.0.99"])
    assert set(got) == {MULTI_A, MULTI_B} and "「報廢」" in got[MULTI_B]
    for ip in (MULTI_A, MULTI_B):
        r = _post(client, ip)
        assert r.status_code == 400 and "退役／報廢" in r.text
    assert tried == []


def test_訊息寫實際狀態字樣_閒置不擋_報廢擋(env3):
    client, tried, dbp = env3
    r = _post(client, IDLE)
    assert "資產狀態為" not in r.text                         # 「閒置」可能是待啟用的新機，不擋納管（2026-11-22）
    n_idle = len(tried)
    r = _post(client, RET)
    assert r.status_code == 400 and "資產狀態為「報廢」" in r.text
    assert len(tried) == n_idle                                   # 報廢那次沒有新增任何連線


def test_同機退役正式列加空白DYN重複列_兩種進法都擋(env3):
    client, tried, dbp = env3
    import post_onboard

    assert _post(client, DUP_IP).status_code == 400
    conn = _conn(dbp)
    for job in post_onboard.JOBS:     # 用 DYN 那列的序號進來收集也要略過
        assert "退役／報廢" in (post_onboard._platform_skip(conn, job, "DYN-S1") or "")
        assert "退役／報廢" in (post_onboard._platform_skip(conn, job, "S-1") or "")
    assert tried == []


def test_CIA正本狀態空白當現役_不擋_已知限制(env3):
    client, tried, dbp = env3
    import post_onboard

    assert _post(client, BLANK_IP).status_code == 200
    conn = _conn(dbp)
    assert all("退役／報廢" not in (post_onboard._platform_skip(conn, j, "B-1") or "") for j in post_onboard.JOBS)


def test_同主機名不同IP_新口徑下整台判定(env3, monkeypatch):
    client, tried, dbp = env3
    import machine_identity

    r = _post(client, HS_B)       # 舊口徑：DYN 那列自己是一台、沒有明確退役 → 不擋
    assert r.status_code == 200
    tried.clear()
    import db
    db.set_setting(_conn(dbp), machine_identity.MODE_SETTING, machine_identity.MODE_HOSTNAME)   # 每個請求會從 DB 重載模式，不能只改記憶體
    r = _post(client, HS_B)       # 新口徑：同主機名＝同一台，那台全部登記都不是現役 → 擋
    assert r.status_code == 400 and "退役／報廢" in r.text
    assert tried == []


def test_批次判定_SQL次數是批次數不是IP數(env3):
    client, tried, dbp = env3
    import onboard_eligibility as oe

    conn = _conn(dbp)
    for i in range(1, 600):
        conn.execute("INSERT INTO hardware (asset_serial, ip, hostname, asset_status) VALUES (?,?,?,?)",
                     (f"X-{i}", f"10.98.{i // 250}.{i % 250}", f"x-example-{i}", "報廢"))
    conn.commit()
    ips = [f"10.98.{i // 250}.{i % 250}" for i in range(1, 600)] + ["10.99.0.250"]
    n = []
    conn.set_trace_callback(lambda s: n.append(s))
    got = oe.retired_targets(conn, ips)
    conn.set_trace_callback(None)
    assert len(got) == 599 and "10.99.0.250" not in got
    assert len(n) <= 6, f"SQL 次數 {len(n)} 不該隨 IP 數成長"


def test_例外只回固定字樣_不洩露例外類型(env3, monkeypatch):
    client, tried, dbp = env3
    import onboard_eligibility as oe

    def boom(*a, **k):
        raise sqlite3.OperationalError("no such column: secret_col")
    monkeypatch.setattr(oe, "_hw_rows_by_ips", boom)
    got = oe.retired_targets(_conn(dbp), ["10.99.0.250"])
    assert got == {"10.99.0.250": oe.ELIGIBILITY_FAIL_ERROR}
    r = _post(client, "10.99.0.250")
    assert r.status_code == 400 and "OperationalError" not in r.text and "secret_col" not in r.text


def test_paste發權杖_退役零權杖_取回時轉退役也擋(env3, monkeypatch):
    client, tried, dbp = env3
    import onboard_bundle
    import paste_watch
    from routers import onboard as ro

    issued = []
    monkeypatch.setattr(paste_watch, "issue", lambda *a, **k: issued.append(a) or [])
    r = client.post("/api/onboard/paste/issue", json={"ips": [RET]})
    assert r.status_code == 400 and not issued
    # 取回：權杖有效（假裝發權杖時還是現役），但來源 IP 這時已是退役 → 404、不給腳本
    monkeypatch.setattr(onboard_bundle, "token_valid", lambda tok, ip: True)
    monkeypatch.setattr(onboard_bundle, "bundle_for", lambda conn: "#!/bin/sh\n")
    req = SimpleNamespace(client=SimpleNamespace(host=RET))
    with pytest.raises(Exception) as ei:
        ro.onboard_fetch("t", req, _conn(dbp))
    assert getattr(ei.value, "status_code", None) == 404
    ok = ro.onboard_fetch("t", SimpleNamespace(client=SimpleNamespace(host="10.99.0.250")), _conn(dbp))
    assert ok.status_code == 200


def test_hint_退役不碰22埠(env3, monkeypatch):
    client, tried, dbp = env3
    import fingerprint

    grabbed = []
    monkeypatch.setattr(fingerprint, "grab_banner", lambda ip, port, *a, **k: grabbed.append(ip) or None)
    r = client.get("/api/onboard/hint", params={"ip": RET})
    assert r.status_code == 400 and "退役／報廢" in r.text and grabbed == []
    r = client.get("/api/onboard/hint", params={"ip": "10.99.0.250"})
    assert r.status_code == 200 and grabbed == ["10.99.0.250"]


def test_憑證測試的Windows關卡_退役擋(env3):
    client, tried, dbp = env3
    from fastapi import HTTPException
    from routers import onboard as ro

    with pytest.raises(HTTPException) as ei:
        ro._win_login_gate(_conn(dbp), RET)     # /api/credentials/{name}/test（WinRM）與 /api/onboard/windows 共用這道
    assert ei.value.status_code == 400 and "退役／報廢" in str(ei.value.detail)


def test_批次自動納管_排隊後轉退役_連線前再擋(env3, monkeypatch):
    client, tried, dbp = env3
    import batch_onboard_service as bos
    import db
    import onboard_bundle
    import onboard_engine as eng

    calls = []
    monkeypatch.setattr(eng, "batch_auto_onboard", lambda targets, **kw: calls.append(targets) or [
        {"ip": targets[0]["ip"], "login_ok": False, "password_index": None, "platform": None, "onboarded": False,
         "password_unified": False, "fail_stage": "connect", "fail_reason": "x"}])
    monkeypatch.setattr(onboard_bundle, "bundle_for", lambda conn: "#!/bin/sh\n")
    monkeypatch.setattr(eng, "collector_pubkey", lambda *a, **k: "ssh-ed25519 AAAA test")
    monkeypatch.setattr(eng, "resolve_collector_ip", lambda conn: "10.99.0.221")
    conn = _conn(dbp)
    conn.execute("INSERT INTO batch_onboard_run (triggered_by, target_count, unify, status, started_at, targets_json) "
                 "VALUES ('t', 2, 0, 'running', '2026-11-09 01:00:00', '[]')")
    run_id = conn.execute("SELECT MAX(id) FROM batch_onboard_run").fetchone()[0]
    conn.commit()
    monkeypatch.setattr(bos, "get_connection", lambda p: db.get_connection(dbp))
    bos._do_batch(dbp, run_id, [{"ip": RET}, {"ip": "10.99.0.250"}], "root", ["x1x"], False)
    assert [t[0]["ip"] for t in calls] == ["10.99.0.250"]      # 退役那台零連線
    c = _conn(dbp)
    row = c.execute("SELECT fail_stage, fail_reason FROM batch_onboard_result WHERE ip = ?", (RET,)).fetchone()
    assert row["fail_stage"] == "refused" and "退役／報廢" in row["fail_reason"]


def test_自動納管_候選選出後才轉退役_零連線(monkeypatch):
    import auto_onboard as ao
    import db
    import onboard_eligibility as oe

    monkeypatch.setattr(oe, "_ptr_lookup", lambda ip: set())
    monkeypatch.setattr(oe, "resolver_health", lambda conn: ("ok", "vc.example"))
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        db.insert_hardware(conn, asset_serial="A-1", ip="10.99.0.60", collect_ok=0, environment="正式", asset_status="使用中")
        conn.execute("INSERT INTO scan_history (scan_time, ip, hostname, scan_ok, open_ports) VALUES ('2026-07-20 01:00:00','10.99.0.60',NULL,1,'')")
        conn.commit()
        ao.save_segment(conn, "10.99.0.")
        real = ao.find_candidates

        def flip(c):
            out = real(c)
            c.execute("UPDATE hardware SET asset_status = '報廢' WHERE asset_serial = 'A-1'")   # 選完候選之後才轉退役
            c.commit()
            return out
        monkeypatch.setattr(ao, "find_candidates", flip)
        ran = []
        r = ao.run_auto_onboard(conn, "10.99.0.221", pubkey="ssh-ed25519 AAAATESTKEY webit3-collector",
                                executor=lambda *a, **k: ran.append(a), cred_lookup=lambda ip: ("ssh-lab", "u", "FAKE-PW"))
        assert ran == [] and r["candidates"] == 1 and r["onboarded"] == 0
        assert any("退役／報廢" in (d.get("message") or "") for d in r["details"])
        conn.close()


def test_收集全部_用手動來源跑一輪_六樣都略過(env3):
    client, tried, dbp = env3
    import post_onboard

    res = post_onboard.run_now(_conn(dbp), "R-1", by="t", source="manual")
    assert len(res) == len(post_onboard.JOBS) and all(r["skipped"] and "退役／報廢" in r["message"] for r in res)
