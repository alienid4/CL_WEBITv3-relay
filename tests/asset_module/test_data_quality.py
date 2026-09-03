"""資料品質量測。

這支的價值在「數字要站得住腳」，所以測試盯的是最容易造假的三件事：
  1. 收不到機器資料的主機不能算進分母（不知道 ≠ 錯，也 ≠ 對）
  2. 純人為判斷欄位不能混進正確率分數（那是編出來的）
  3. 每個數字都要能下鑽到「是哪幾台」
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import data_quality  # noqa: E402
import db  # noqa: E402
import api  # noqa: E402

from test_api import _client, _insert_hardware  # noqa: E402


def _recent(days_ago=0):
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _seen(db_path, ip, days_ago=0):
    conn = db.get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO scan_history (scan_time, ip, hostname, scan_ok) VALUES (?, ?, 'x', 1)",
            (_recent(days_ago), ip),
        )
        conn.commit()
    finally:
        conn.close()


def _collected_os(db_path, ip, os_id, os_version):
    conn = db.get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO host_account (ip, username, os_id, os_version) VALUES (?, 'root', ?, ?)",
            (ip, os_id, os_version),
        )
        conn.commit()
    finally:
        conn.close()


def _dim(summary, key):
    return next(d for d in summary["dimensions"] if d["key"] == key)


def test_使用中卻掃不到的會被抓出來且可下鑽():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "Q-001", "alive", "10.9.0.1", asset_status="使用中")
            _insert_hardware(db_path, "Q-002", "ghost", "10.9.0.2", asset_status="使用中")
            _seen(db_path, "10.9.0.1")

            summary = client.get("/api/data-quality").json()
            d = _dim(summary, "reachable")
            assert d["checked"] == 2 and d["ok"] == 1 and d["rate"] == 50.0

            items = client.get("/api/data-quality/reachable").json()["items"]
            assert [i["asset_serial"] for i in items] == ["Q-002"]
        finally:
            api.app.dependency_overrides.clear()


def test_沒掃過的網段算涵蓋率不算資料錯():
    """2026-08-15 實機發現：221 只掃 192.168.1.0/24，4194 筆 10.99.x 資產全被算成
    「使用中卻掃不到」→ 總分 3.9%。那是假的低分，真正的問題是還沒去掃。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "Q-050", "in", "192.168.1.50", asset_status="使用中")
            _insert_hardware(db_path, "Q-051", "far1", "10.99.1.1", asset_status="使用中")
            _insert_hardware(db_path, "Q-052", "far2", "10.99.1.2", asset_status="使用中")
            _seen(db_path, "192.168.1.50")

            summary = client.get("/api/data-quality").json()
            cov = _dim(summary, "coverage")
            assert cov["kind"] == "coverage"
            assert cov["checked"] == 3 and cov["ok"] == 1

            # 沒掃過的兩台不進 reachable 分母，也不該把分數拉成 33%
            reach = _dim(summary, "reachable")
            assert reach["checked"] == 1 and reach["ok"] == 1 and reach["rate"] == 100.0
            assert summary["score"] == 100.0
            # 但要講清楚這 100% 只有 1 筆證據，不然會被當成「四千台都對」
            assert summary["score_sample"] == 1

            items = client.get("/api/data-quality/coverage").json()["items"]
            assert {i["asset_serial"] for i in items} == {"Q-051", "Q-052"}
        finally:
            api.app.dependency_overrides.clear()


def test_填寫率_數字0算有填不算空白():
    """2026-08-15 實機發現：機密性有 3641 筆存 0，被 `v or ''` 判成沒填，
    填寫率直接掉成 0%。0 是值不是空白。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "Q-060", "h1", "10.9.6.1", confidentiality=0)
            _insert_hardware(db_path, "Q-061", "h2", "10.9.6.2", confidentiality=3)
            _insert_hardware(db_path, "Q-062", "h3", "10.9.6.3")  # 真的沒填

            d = _dim(client.get("/api/data-quality").json(), "filled_confidentiality")
            assert d["ok"] == 2, "存 0 的那筆要算有填"
            items = client.get("/api/data-quality/filled_confidentiality").json()["items"]
            assert [i["asset_serial"] for i in items] == ["Q-062"]
        finally:
            api.app.dependency_overrides.clear()


def test_報廢卻還活著算資料不一致():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "Q-010", "zombie", "10.9.1.1", asset_status="報廢")
            _insert_hardware(db_path, "Q-011", "gone", "10.9.1.2", asset_status="報廢")
            _seen(db_path, "10.9.1.1")

            d = _dim(client.get("/api/data-quality").json(), "retired_alive")
            assert d["checked"] == 2 and d["bad"] == 1

            items = client.get("/api/data-quality/retired_alive").json()["items"]
            assert items[0]["asset_serial"] == "Q-010"
        finally:
            api.app.dependency_overrides.clear()


def test_OS一致性_收不到機器回報的不列入分母():
    """最容易造假的地方：把「沒收到資料」當成「一致」，分數立刻變得很好看。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "Q-020", "h1", "10.9.2.1", os="RHEL 9.6")
            _insert_hardware(db_path, "Q-021", "h2", "10.9.2.2", os="RHEL 9.6")
            _insert_hardware(db_path, "Q-022", "h3", "10.9.2.3", os="Windows Server 2019")
            # 只有兩台收得到機器回報
            _collected_os(db_path, "10.9.2.1", "rhel", "9.6")      # 一致
            _collected_os(db_path, "10.9.2.2", "rocky", "9.6")     # 不一致（不同發行版）

            d = _dim(client.get("/api/data-quality").json(), "os_match")
            assert d["checked"] == 2, "沒收到回報的第三台不該進分母"
            assert d["ok"] == 1

            items = client.get("/api/data-quality/os_match").json()["items"]
            assert [i["asset_serial"] for i in items] == ["Q-021"]
            assert "rocky" in items[0]["reason"]
        finally:
            api.app.dependency_overrides.clear()


def test_OS比對放寬寫法差異但不放寬發行版差異():
    assert data_quality._os_matches("Red Hat Enterprise Linux 9.6", "rhel 9.6")
    assert data_quality._os_matches("RHEL 9.6", "redhat 9.6")
    assert not data_quality._os_matches("RHEL 9.6", "rocky 9.6")
    assert not data_quality._os_matches("RHEL 9.6", "rhel 8.10")
    assert data_quality._os_matches("Windows Server 2019", "microsoft windows server 2019")


def test_人工判斷欄位只給填寫率_不併入正確率分數():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "Q-030", "h1", "10.9.3.1", asset_status="使用中",
                             custodian="王小明")
            _insert_hardware(db_path, "Q-031", "h2", "10.9.3.2", asset_status="使用中")
            _seen(db_path, "10.9.3.1")
            _seen(db_path, "10.9.3.2")

            summary = client.get("/api/data-quality").json()
            filled = _dim(summary, "filled_custodian")
            assert filled["kind"] == "filled" and filled["rate"] == 50.0

            # 分數只能由可驗證維度組成：這裡兩台都掃得到、沒有退役資產、沒有 OS 回報，
            # 所以應該是 100 分，不會被保管者只填一半拉低
            assert summary["score"] == 100.0

            items = client.get("/api/data-quality/filled_custodian").json()["items"]
            assert [i["asset_serial"] for i in items] == ["Q-031"]
        finally:
            api.app.dependency_overrides.clear()


def test_退役資產不列入量測母體():
    """已報廢的機器對不上機器事實是正常的，算進去只會讓分數難看又沒有行動意義。"""
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            _insert_hardware(db_path, "Q-040", "h1", "10.9.4.1", asset_status="使用中")
            _insert_hardware(db_path, "Q-041", "h2", "10.9.4.2", asset_status="報廢")
            summary = client.get("/api/data-quality").json()
            assert summary["asset_total"] == 1
            assert _dim(summary, "filled_custodian")["checked"] == 1
        finally:
            api.app.dependency_overrides.clear()


def test_沒有任何資料時不會除以零():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        try:
            summary = client.get("/api/data-quality").json()
            assert summary["asset_total"] == 0
            assert summary["score"] is None
        finally:
            api.app.dependency_overrides.clear()
