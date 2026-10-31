"""納管成功後自動收集（規格／服務／軟體／帳號）——單台與批次兩條路都要觸發。

守的重點（使用者 2026-10-16：「納管當下應該是全部收集才對」）：
1. 納管成功後四樣收集都被呼叫，沿用「收集全部」同一條 *_inventory.collect_* 路徑。
2. 某一樣收集丟例外，納管結果仍是成功，且**那一樣**標明失敗、訊息具體（哪一樣、為什麼），
   不吞成一句「收集失敗」，也不拖垮其他樣。
3. 批次納管每一台都觸發收集（之前只刷收集狀態、沒有真的收四樣）。

全部用 fake/injected，**不連任何主機**。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_inventory  # noqa: E402
import batch_onboard_service as svc  # noqa: E402
import collect_log  # noqa: E402
import db  # noqa: E402
import health_probe  # noqa: E402
import host_spec_collector  # noqa: E402
import manage_state  # noqa: E402
import onboard_engine  # noqa: E402
import post_onboard  # noqa: E402
import service_inventory  # noqa: E402
import software_inventory  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="HW-A", ip="10.99.1.1",
                       os="Red Hat Enterprise Linux 8 (64-bit)", hostname="host-a",
                       environment="測試", asset_status="使用中")
    conn.commit()
    return p, conn


def _stub_collectors(monkeypatch, *, called, raise_job=None):
    """把四樣收集器換成 fake：記錄呼叫、依 raise_job 決定哪一樣丟例外。
    每個 fake 都收 only_serial，驗證傳進去的是剛納管那台。"""
    def mk(job):
        def fake(conn, *a, only_serial=None, trigger=None, **k):
            called.append((job, only_serial, trigger))
            if raise_job == job:
                raise ValueError(f"{job} 收集逾時")
            return {"ok": True}
        return fake

    monkeypatch.setattr(host_spec_collector, "run_collection", mk("spec"))
    monkeypatch.setattr(service_inventory, "collect_services", mk("service"))
    monkeypatch.setattr(software_inventory, "collect_software", mk("software"))
    monkeypatch.setattr(account_inventory, "collect_accounts", mk("account"))
    # 2026-10-18 起納管後也收 OS 重建資料與組態檢核（Q78／Q84）
    import config_audit
    import os_rebuild_collector
    monkeypatch.setattr(os_rebuild_collector, "run_collection", mk("rebuild"))
    monkeypatch.setattr(config_audit, "collect_from_host",
                        lambda conn, serial, runner=None, by=None:
                        called.append(("config_audit", serial, "post_onboard")) or {"total": 61, "counts": {}})
    # 健康基準會連出去——測試裡擋掉（run_now 內部已 try/except，不影響收集本體）
    monkeypatch.setattr(health_probe, "check_one", lambda *a, **k: {})


# ===== (a) 納管成功後四樣收集都被呼叫 =====

def test_納管後四樣收集都被呼叫(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            called = []
            _stub_collectors(monkeypatch, called=called)
            results = post_onboard.run_now(conn, "HW-A", by="sysinfra")

            jobs = {c[0] for c in called}
            # 「排程」在 Linux 讀 OS 重建資料、略過不另收（2026-10-20），其餘每樣都要呼叫到
            assert jobs == set(post_onboard.JOBS) - {"schedule"}, f"少收了：{jobs}"
            # 收的是剛納管那一台，不是整個機隊
            for _job, only_serial, trigger in called:
                assert only_serial == "HW-A"
                assert trigger == "post_onboard"
            # 回傳每一樣各自獨立
            assert {r["job"] for r in results} == set(post_onboard.JOBS)
            assert all(r["ok"] for r in results if r["job"] != "schedule")
            assert next(r for r in results if r["job"] == "schedule")["ok"] is None   # 略過，不是失敗
            # 採集紀錄留了一筆 post_onboard
            row = conn.execute(
                "SELECT ok, message FROM collect_attempt_log WHERE kind='post_onboard'"
                " AND target='HW-A'").fetchone()
            assert row is not None and row["ok"] == 1
        finally:
            conn.close()


# ===== (b) 其中一樣丟例外：納管仍成功、那一樣標明失敗、其他樣不受影響 =====

def test_一樣收集失敗不拖垮其他樣也不影響納管(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        _, conn = _conn(tmp)
        try:
            called = []
            _stub_collectors(monkeypatch, called=called, raise_job="software")
            # run_now 不可以把例外往外丟（納管成功是已發生的事實）
            results = post_onboard.run_now(conn, "HW-A", by="sysinfra")

            by_job = {r["job"]: r for r in results}
            assert by_job["software"]["ok"] is False
            # 訊息要具體：哪一樣、為什麼——不可以吞成一句「收集失敗」
            assert "ValueError" in by_job["software"]["message"]
            assert "逾時" in by_job["software"]["message"]
            # 其他三樣照收、不受影響
            assert by_job["spec"]["ok"] and by_job["service"]["ok"] and by_job["account"]["ok"]
            # 紀錄把失敗那樣標出來（軟體），且整體 ok=0
            row = conn.execute(
                "SELECT ok, message FROM collect_attempt_log WHERE kind='post_onboard'"
                " AND target='HW-A'").fetchone()
            assert row["ok"] == 0
            assert "軟體" in row["message"]
        finally:
            conn.close()


# ===== (c) 批次納管：每一台都觸發收集 =====

def test_批次納管每一台都觸發收集(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        for serial, ip in [("HW-A", "10.99.1.1"), ("HW-B", "10.99.1.2")]:
            db.insert_hardware(conn, asset_serial=serial, ip=ip,
                               os="Red Hat Enterprise Linux 8 (64-bit)",
                               environment="測試", asset_status="使用中")
        cur = conn.execute(
            "INSERT INTO batch_onboard_run (triggered_by, target_count, unify, status,"
            " started_at) VALUES ('sysinfra',2,0,'running','2026-10-16 10:00:00')")
        run_id = cur.lastrowid
        conn.commit()
        conn.close()

        # 不連任何主機：納管引擎、收集狀態、基準全部換成 fake
        monkeypatch.setattr(onboard_engine, "resolve_collector_ip", lambda conn: "10.99.0.9")
        monkeypatch.setattr(onboard_engine, "collector_pubkey", lambda: "ssh-ed25519 AAAA")
        monkeypatch.setattr(onboard_engine, "resolve_account_comment", lambda conn: "test")

        def fake_batch(targets, **k):
            ip = targets[0]["ip"]
            return [{"ip": ip, "login_ok": True, "password_index": 0, "platform": "linux",
                     "onboarded": True, "password_unified": False,
                     "fail_stage": None, "fail_reason": None}]
        monkeypatch.setattr(onboard_engine, "batch_auto_onboard", fake_batch)
        monkeypatch.setattr(manage_state, "refresh_collect_status",
                            lambda conn, only_ip=None, **k: None)
        monkeypatch.setattr(svc, "_HOST_PAUSE_SEC", 0)

        collected = []
        monkeypatch.setattr(post_onboard, "run_now",
                            lambda conn, serial, by=None, **k: collected.append((serial, by)) or [])

        targets = [{"ip": "10.99.1.1", "environment": "測試"},
                   {"ip": "10.99.1.2", "environment": "測試"}]
        svc._do_batch(p, run_id, targets, "root", ["pw"], False)

        serials = {c[0] for c in collected}
        assert serials == {"HW-A", "HW-B"}, f"不是每台都觸發收集：{serials}"
        # 操作者傳到了收集（by）
        assert all(c[1] == "root" for c in collected)


# ===== 納管紀錄要帶登入帳號（login_user）與操作者（triggered_by） =====

def test_納管紀錄帶login_user與triggered_by_null也正常():
    """資產明細頁的納管紀錄要分得出「登入目標機的帳號」跟「網頁操作者」。
    舊資料／自動納管的 login_user 可能是 null，回傳要帶得出來、不能爆掉。"""
    import sqlite3

    import api  # noqa: F401
    import auth
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        db.create_user(conn, "u", auth.hash_password("pw"))
        db.insert_hardware(conn, asset_serial="HW-A", ip="10.99.1.1", hostname="h",
                           environment="測試", asset_status="使用中")
        conn.execute(
            "INSERT INTO onboard_audit (target_ip,platform,login_user,trigger,triggered_by,"
            "ok,stage,message) VALUES ('10.99.1.1','linux','root','manual','sysinfra',1,'done','ok')")
        conn.execute(
            "INSERT INTO onboard_audit (target_ip,platform,login_user,trigger,triggered_by,"
            "ok,stage,message) VALUES ('10.99.1.1','linux',NULL,'auto',NULL,1,'done','ok')")
        conn.commit()
        conn.close()

        def ov():
            c = db.get_connection(p)
            try:
                yield c
            finally:
                c.close()

        api.app.dependency_overrides[api.get_db] = ov
        try:
            cl = TestClient(api.app)
            assert cl.post("/api/auth/login",
                           json={"username": "u", "password": "pw"}).status_code == 200
            hist = cl.get("/api/assets/HW-A").json()["onboard_history"]
            assert len(hist) == 2
            # 欄位要在
            assert "login_user" in hist[0] and "triggered_by" in hist[0]
            # 最新那筆（id DESC）是 null 的自動納管
            assert hist[0]["login_user"] is None and hist[0]["triggered_by"] is None
            # 舊那筆帶得出 root / sysinfra
            assert hist[1]["login_user"] == "root" and hist[1]["triggered_by"] == "sysinfra"
        finally:
            api.app.dependency_overrides.clear()


def test_asset_detail帶collect_status三類(monkeypatch):
    """資產明細頁回傳 collect_status，帳號／軟體／服務／規格各有筆數與最後收集時間。"""
    import api  # noqa: F401
    import auth
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        db.create_user(conn, "u", auth.hash_password("pw"))
        db.insert_hardware(conn, asset_serial="HW-A", ip="10.99.1.1", hostname="h",
                           os="Red Hat Enterprise Linux 8.8",
                           environment="測試", asset_status="使用中")
        conn.execute("UPDATE hardware SET collect_ok=1 WHERE asset_serial='HW-A'")
        conn.commit()
        conn.close()

        def ov():
            c = db.get_connection(p)
            try:
                yield c
            finally:
                c.close()

        api.app.dependency_overrides[api.get_db] = ov
        try:
            cl = TestClient(api.app)
            cl.post("/api/auth/login", json={"username": "u", "password": "pw"})
            cs = cl.get("/api/assets/HW-A").json()["collect_status"]
            assert cs["targeted"] is True and cs["collect_ok"] == 1
            for kind in ("account", "software", "service"):
                assert "count" in cs[kind] and "last_collected_at" in cs[kind]
            assert "last_collected_at" in cs["spec"]
        finally:
            api.app.dependency_overrides.clear()
