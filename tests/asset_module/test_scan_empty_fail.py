"""一輪掃描 ≥3 個網段加起來 0 台＝掃描器壞了（2026-10-28，scan_health）。

釘住的事：
- 全部 0 台（≥3 段）→ scan_runs 記 failed＋那句話，開一筆「掃描無回應」，1.2 異常事件／1.1 待處理事項看得到
- 有任何一段有回應 → ok；1～2 段全 0 → 照舊 ok（count_bridge 的小的補掃規則）
- count_bridge 的對帳（recon_coverage）不受影響：它讀 scan_coverage／scan_history，不讀 scan_runs
- 標記已處理：負數 id 走同一支 API
例子 IP 一律 10.99.x。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import scan_health  # noqa: E402
import scan_service  # noqa: E402


class _Src:
    def __init__(self, cidr, ips=()):
        self.cidr = cidr
        self.name = f"seg-{cidr}"
        self._ips = ips

    def scan(self):
        return [SimpleNamespace(hostname=None, ip=ip, device_model=None, is_vm=False, segment=self.cidr,
                                mac=None, mac_vendor=None, open_ports="22", ttl=64, os_guess=None)
                for ip in self._ips]


@pytest.fixture()
def dbp(tmp_path, monkeypatch):
    p = tmp_path / "s.db"
    db.init_db(p)
    monkeypatch.setattr(scan_service, "get_connection", lambda: db.get_connection(p))
    return p


def _run(monkeypatch, sources):
    monkeypatch.setattr(scan_service, "scan_targets", lambda conn, only=None: sources)
    scan_service._running = True
    scan_service._do_scan("schedule", None, light=True)


def _last(dbp):
    c = db.get_connection(dbp)
    try:
        return dict(c.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone())
    finally:
        c.close()


def test_三個網段全部0台_記失敗並開異常(dbp, monkeypatch):
    _run(monkeypatch, [_Src("10.99.1.0/24"), _Src("10.99.2.0/24"), _Src("10.99.3.0/24")])
    r = _last(dbp)
    assert r["status"] == "failed" and r["found_count"] == 0
    assert r["error"] == "掃描 3 個網段全部無回應，掃描器可能無法連線"
    c = db.get_connection(dbp)
    try:
        assert scan_health.open_count(c) == 1
        from routers import assets as ra
        rows = ra.list_issues(issue_type=None, is_read=None, sort_by="detected_at", order="desc",
                              session=None, conn=c)
        mine = [x for x in rows if x["issue_type"] == "掃描無回應"]
        assert len(mine) == 1 and mine[0]["id"] < 0 and "3 個網段" in mine[0]["hostname"]
        # 1.1 待處理事項排第一
        import v2_views
        todo = v2_views.home(c)["todo"]
        assert todo[0]["key"] == "scan_empty" and todo[0]["n"] == 1 and todo[0]["tone"] == "bad"
        # 標記已處理（負數 id）：批次與單筆兩條都通
        out = ra.mark_issues_read_batch(body=SimpleNamespace(ids=[mine[0]["id"]]), session=None, conn=c)
        assert out["marked"] == 1 and scan_health.open_count(c) == 0
        assert ra.list_issues(issue_type=None, is_read=False, sort_by="detected_at", order="desc",
                              session=None, conn=c) == [x for x in rows if x["issue_type"] != "掃描無回應" and not x["is_read"]]
        assert "scan_empty" not in {t["key"] for t in v2_views.home(c)["todo"]}
    finally:
        c.close()


def test_單筆標記_負數id(dbp):
    c = db.get_connection(dbp)
    try:
        iid = -scan_health.raise_alert(c, None, 4, "掃描 4 個網段全部無回應，掃描器可能無法連線")
        from routers import assets as ra
        r = ra.mark_issue_read(issue_id=iid, body=SimpleNamespace(is_read=True), session=None, conn=c)
        assert r["is_read"] == 1 and r["id"] == iid
    finally:
        c.close()


def test_部分網段有回應_照舊ok(dbp, monkeypatch):
    _run(monkeypatch, [_Src("10.99.1.0/24"), _Src("10.99.2.0/24", ["10.99.2.5"]), _Src("10.99.3.0/24")])
    r = _last(dbp)
    assert r["status"] == "ok" and r["found_count"] == 1 and r["error"] is None
    c = db.get_connection(dbp)
    try:
        assert scan_health.open_count(c) == 0
    finally:
        c.close()


def test_一兩個網段的0台_走count_bridge小的補掃規則_照舊ok(dbp, monkeypatch):
    _run(monkeypatch, [_Src("10.99.1.0/24"), _Src("10.99.2.0/24")])
    assert _last(dbp)["status"] == "ok"
    assert scan_health.small_max() == 2


def test_count_bridge對帳不受影響(dbp, monkeypatch):
    """先一輪有回應、再一輪三段全 0：recon_coverage 的結果跟「沒有 scan_health」時一模一樣
    （把狀態改回 ok、刪掉異常再算一次比對），而且那輪全 0 照舊被它當成多段的空掃描（不是小的補掃）。"""
    import count_bridge

    _run(monkeypatch, [_Src("10.99.1.0/24", ["10.99.1.5"]), _Src("10.99.2.0/24", ["10.99.2.5"]),
                       _Src("10.99.3.0/24", ["10.99.3.5"])])
    import time
    time.sleep(1.1)       # scan_time 到秒：兩輪要不同時間
    _run(monkeypatch, [_Src("10.99.1.0/24"), _Src("10.99.2.0/24"), _Src("10.99.3.0/24")])
    assert _last(dbp)["status"] == "failed"
    c = db.get_connection(dbp)
    try:
        with_health = count_bridge.recon_coverage(c)
        c.execute("DELETE FROM scan_alert")
        c.execute("UPDATE scan_runs SET status = 'ok', error = NULL")
        c.commit()
        without = count_bridge.recon_coverage(c)
        assert with_health == without
        kinds = [e["kind"] for e in with_health["empty_runs"]]
        assert kinds and all(k != "suspect_down" for k in kinds)
    finally:
        c.close()


def test_規則門檻跟count_bridge同一份設定(monkeypatch):
    import count_bridge

    monkeypatch.setattr(count_bridge, "load_rules", lambda: {"empty_run_small_max_segments": 4})
    assert scan_health.empty_failure(4, 0) is None
    assert scan_health.empty_failure(5, 0).startswith("掃描 5 個網段")
    assert scan_health.empty_failure(5, 1) is None
