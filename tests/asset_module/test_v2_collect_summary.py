"""主機履歷「收集摘要」(codex 207)：最後成功時間、距今天數、過期門檻（app_settings.collect_stale_days，預設 8 天）。
全部假資料（10.99.x）；不連任何機器。"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import v2_collect_summary as cs  # noqa: E402

NOW = datetime(2026, 11, 10, 12, 0, 0)


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def test_classify_three_states_and_boundary():
    assert cs.classify(None, NOW, 8)["state"] == "never"
    assert cs.classify("看不懂", NOW, 8)["state"] == "never"       # 時間讀不懂＝未取得，不猜
    assert cs.classify("2026-11-02 12:00:00", NOW, 8)["state"] == "fresh"       # 剛好 8 天＝不過期
    r = cs.classify("2026-11-01 11:00:00", NOW, 8)
    assert r["state"] == "stale" and r["days"] == 9 and r["state_label"] == "資料已過期"


def test_stale_days_setting(tmp_path):
    conn = _conn(tmp_path)
    assert cs.stale_days(conn) == 8
    for bad in ("abc", "0", "999", ""):
        db.set_setting(conn, cs.SETTING_KEY, bad)
        assert cs.stale_days(conn) == 8
    db.set_setting(conn, cs.SETTING_KEY, "3")
    assert cs.stale_days(conn) == 3


def test_summary_rows(tmp_path):
    import service_inventory
    import software_inventory

    conn = _conn(tmp_path)
    db.insert_hardware(conn, asset_serial="L-1", hostname="lnx01", ip="10.99.2.14", physical_location="01_甲機房",
                       rack_no="VM", device_model="(VM)", os="Red Hat Enterprise Linux 9", is_vm="Y")
    conn.commit()
    software_inventory.upsert_packages(conn, "10.99.2.14", "L-1", [{"name": "bash", "version": "5.1", "arch": "x86_64"}], "ssh_rpm")
    service_inventory.upsert_services(conn, "10.99.2.14", "L-1", [{"port": 22, "proto": "tcp", "bind": "0.0.0.0"}], "ssh_ss")
    conn.execute("UPDATE host_package SET last_seen = '2026-10-01 08:00:00'")
    conn.execute("UPDATE host_service SET last_seen = '2026-11-09 08:00:00'")
    conn.execute("INSERT INTO health_snapshot (ip, taken_at, overall, reachable) VALUES ('10.99.2.14', '2026-11-08 01:00:00', 'green', 1)")
    conn.execute("INSERT INTO health_snapshot (ip, taken_at, overall, reachable) VALUES ('10.99.2.14', '2026-11-10 01:00:00', 'red', 0)")
    conn.commit()
    r = cs.summary(conn, "L-1", NOW)
    assert r["applicable"] and r["stale_days"] == 8
    by = {x["kind"]: x for x in r["rows"]}
    assert [x["__no"] for x in r["rows"]] == [1, 2, 3, 4, 5]
    assert by["software"]["state"] == "stale" and by["software"]["days"] == 40
    assert by["service"]["state"] == "fresh"
    assert by["account"]["state"] == "never" and by["account"]["last"] is None
    assert by["spec"]["state"] == "never"
    assert by["health"]["state"] == "fresh" and by["health"]["last"].startswith("2026-11-08")   # 連不上的那筆不算成功
    assert r["counts"] == {"fresh": 2, "stale": 1, "never": 2}
    db.set_setting(conn, cs.SETTING_KEY, "1")
    assert {x["kind"]: x["state"] for x in cs.summary(conn, "L-1", NOW)["rows"]}["health"] == "stale"


def test_summary_unknown_machine(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(LookupError):
        cs.summary(conn, "NOPE", NOW)


def test_過期判斷_超過門檻即過期_未來時間異常_時區轉台北():
    from datetime import datetime
    import v2_collect_summary as cs
    now = datetime(2026, 11, 10, 12, 0, 0)
    assert cs.classify("2026-11-02 12:00:00", now, 8)["state"] == "fresh"          # 剛好 8 天
    assert cs.classify("2026-11-02 10:59:00", now, 8)["state"] == "stale"          # 8 天 1 小時
    assert cs.classify("2026-11-20 12:00:00", now, 8)["state"] == "never"          # 未來
    assert cs.classify("2026-11-10T04:00:00Z", now, 8)["days"] == 0                # UTC 04:00 ＝ 台北 12:00
