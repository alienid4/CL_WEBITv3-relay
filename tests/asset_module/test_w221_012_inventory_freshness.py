"""W221-012：服務／軟體清單的「資料時間」與現行／過期。

過期＝last_seen 超過 observed_deps_stale_days（預設 30）天，或該主機最近一次收集嘗試失敗
（hardware.collect_ok=0 且發生在這列最後看到之後）——整台收集失敗時，舊列不得算「現行」。
IP 一律 10.99.x（範例）。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import service_inventory as si  # noqa: E402
import software_inventory as sw  # noqa: E402


def _ts(days_ago: float) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    for sn, host, ip in (("A-1", "fresh", "10.99.1.1"), ("A-2", "old", "10.99.1.2"), ("A-3", "failed", "10.99.1.3")):
        db.insert_hardware(c, asset_serial=sn, hostname=host, ip=ip, collect_ok=1)
    # A-3：昨天看到的服務，但今天這台整台收集失敗（collect_ok=0，檢查時間在 last_seen 之後）
    c.execute("UPDATE hardware SET collect_ok = 0, collect_checked_at = ? WHERE asset_serial = 'A-3'", (_ts(0.1),))
    for ip, sn, port, seen in (("10.99.1.1", "A-1", 80, _ts(1)), ("10.99.1.2", "A-2", 80, _ts(45)),
                               ("10.99.1.3", "A-3", 80, _ts(1))):
        c.execute("INSERT INTO host_service (ip, asset_serial, proto, port, exposure, source, first_seen, last_seen) "
                  "VALUES (?,?,?,?,?,?,?,?)", (ip, sn, "tcp", port, "all", "ssh_ss", _ts(60), seen))
        c.execute("INSERT INTO host_package (ip, asset_serial, name, version, arch, source, first_seen, last_seen) "
                  "VALUES (?,?,?,?,?,?,?,?)", (ip, sn, "openssl", "3.0", "x86_64", "rpm", _ts(60), seen))
    # 一筆已消失的：不算現行也不算過期
    c.execute("INSERT INTO host_service (ip, asset_serial, proto, port, source, first_seen, last_seen, gone_at) "
              "VALUES ('10.99.1.1','A-1','tcp',9999,'ssh_ss',?,?,?)", (_ts(60), _ts(50), _ts(40)))
    c.commit()
    try:
        yield c
    finally:
        c.close()


def test_服務卡片現行加過期等於live_整台失敗的舊列算過期(conn):
    s = si.service_summary(conn)
    assert s["live"] == 3 and s["gone"] == 1
    assert s["current"] == 1 and s["stale"] == 2          # 只有 fresh 是現行；old(45 天)與 failed(整台失敗) 過期
    assert s["current"] + s["stale"] == s["live"]
    assert s["stale_days"] == 30
    cur = si.list_services(conn, card="current")
    stl = si.list_services(conn, card="stale")
    assert [r["ip"] for r in cur] == ["10.99.1.1"]
    assert sorted(r["ip"] for r in stl) == ["10.99.1.2", "10.99.1.3"]
    assert len(cur) == s["current"] and len(stl) == s["stale"]     # 卡片數字＝下鑽列數


def test_服務清單每列帶is_stale(conn):
    rows = {r["ip"]: r for r in si.list_services(conn, include_gone=False)}
    assert rows["10.99.1.1"]["is_stale"] == 0
    assert rows["10.99.1.2"]["is_stale"] == 1 and rows["10.99.1.3"]["is_stale"] == 1


def test_過期天數沿用observed_deps_stale_days設定(conn):
    db.set_setting(conn, "observed_deps_stale_days", "60")
    s = si.service_summary(conn)
    assert s["stale_days"] == 60
    assert s["stale"] == 1            # 45 天那台在 60 天內就不過期；失敗那台仍過期
    assert [r["ip"] for r in si.list_services(conn, card="stale")] == ["10.99.1.3"]


def test_重新收集成功後整台失敗的過期自然恢復(conn):
    conn.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = 'A-3'")
    conn.commit()
    assert si.service_summary(conn)["stale"] == 1


def test_軟體摘要與清單分現行過期(conn):
    s = sw.software_summary(conn)
    assert s["live"] == 3 and s["current"] == 1 and s["stale"] == 2
    assert s["hosts_current"] == 1 and s["hosts_stale"] == 2 and s["stale_days"] == 30
    (item,) = sw.list_software(conn)
    assert item["host_count"] == 3 and item["current_host_count"] == 1 and item["stale_host_count"] == 2
    assert item["last_seen"]
    hosts = {h["ip"]: h for h in sw.software_hosts(conn, "openssl")}
    assert hosts["10.99.1.1"]["is_stale"] == 0 and hosts["10.99.1.2"]["is_stale"] == 1 and hosts["10.99.1.3"]["is_stale"] == 1


def test_卡片鍵含current與stale():
    assert {"current", "stale"} <= set(si.CARD_WHERE)
