"""2.044.4：GPT 複核 8f72d629 的修正（A6 hash 對全文、A5 保固獨立重算、P2 今天發生過截斷、P2 容量驗算非數字）。全部假資料。"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import alert_notify as an  # noqa: E402
import daily_risk as dr  # noqa: E402
import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import v2_today  # noqa: E402
import verify_numbers as vn  # noqa: E402
import wug_alert as wa  # noqa: E402

NOW = datetime(2026, 11, 4, 10, 30, 0)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    qw.set_clock(lambda: NOW)
    yield c
    qw.set_clock(None)
    c.close()


def _alert(c, did, started, ended=None, tier="red", ip="192.0.2.10"):
    wa.ensure(c)
    cur = c.execute("INSERT INTO alert_event (wug_device_id, monitor, device_name, ip, match, tier, started_at, ended_at, last_seen, duration_min, created_at) "
                    "VALUES (?, 'Ping', ?, ?, 'no_asset', ?, ?, ?, ?, 10, ?)", (did, f"dev{did}", ip, tier, started, ended, started, started))
    c.commit()
    return cur.lastrowid


def _enable(c):
    wa.ensure(c)
    wa.save_settings(c, {**wa.DEFAULTS, "enabled": True})
    wa._set(c, wa.K_LAST_RECV, "2026-11-04 10:29:00")
    c.commit()


def test_P2_今天發生過_件數用COUNT_明細超過上限標已截斷(conn, monkeypatch):
    _enable(conn)
    for i in range(3):
        _alert(conn, str(10 + i), f"2026-11-04 0{i + 1}:00:00", ended="2026-11-04 09:00:00" if i else None)
    monkeypatch.setattr(v2_today, "TODAY_MAX", 2)
    d = v2_today.today(conn, NOW)["alerts"]["today"]
    assert d["n"] == 3 and len(d["rows"]) == 2 and d["truncated"] is True and "已截斷" in d["label"]
    assert d["still_open"] == 1 and d["recovered"] == 2
    monkeypatch.setattr(v2_today, "TODAY_MAX", 2000)
    d = v2_today.today(conn, NOW)["alerts"]["today"]
    assert d["n"] == len(d["rows"]) == 3 and d["truncated"] is False and d["label"] is None


def test_A6_hash對訊息全文_只有持續分鐘數變了照送_其他字變了要重新預覽(conn, monkeypatch):
    monkeypatch.setattr(an, "_target", lambda c: ({"channel": "webhook"}, "Teams 頻道（Workflow：example.test）", None))
    eid = _alert(conn, "7", "2026-11-04 08:00:00")
    pv = an.preview(conn, eid, "alice")
    conn.execute("UPDATE alert_event SET duration_min = 99 WHERE id = ?", (eid,))
    conn.commit()
    assert an.send(conn, eid, "alice", preview_hash=pv["preview_hash"])["seq"] == 1
    conn.rollback()
    for col, val in (("status_text", "Down at least 5 min"), ("ip", "192.0.2.99"), ("market", "開盤"), ("device_name", "dev-new"),
                     ("started_at", "2026-11-04 07:59:00")):
        pv = an.preview(conn, eid, "alice")
        conn.execute(f"UPDATE alert_event SET {col} = ? WHERE id = ?", (val, eid))
        conn.commit()
        with pytest.raises(an.NotifyRefused, match="重新"):
            an.send(conn, eid, "alice", preview_hash=pv["preview_hash"])
    # 收件人或通道變了也要重新預覽
    pv = an.preview(conn, eid, "alice")
    monkeypatch.setattr(an, "_target", lambda c: ({"channel": "webhook"}, "Teams 頻道（Workflow：other.example.test）", None))
    with pytest.raises(an.NotifyRefused):
        an.send(conn, eid, "alice", preview_hash=pv["preview_hash"])


def test_A5_保固獨立重算與追蹤處理同一批(conn):
    for sn, model in (("P-1", "Dell R750"), ("P-2", "Dell R750"), ("P-3", "HPE DL380"), ("P-4", "HPE DL380")):
        db.insert_hardware(conn, asset_serial=sn, hostname=sn.lower(), ip=f"192.0.2.{40 + int(sn[-1])}", device_model=model)
    conn.commit()
    dr.save_entry(conn, "model", "dell r750", {"contract_end": "2026-01-01"}, "admin")      # 型號層：已過期（P-1、P-2）
    dr.save_entry(conn, "asset", "P-2", {"contract_end": "2030-01-01"}, "admin")           # 本機層蓋過型號：P-2 不算
    dr.save_entry(conn, "asset", "P-3", {"contract_end": "2026-12-01"}, "admin")           # 90 天內
    dr.save_entry(conn, "asset", "P-4", {"contract_end": "2027-12-01"}, "admin")           # 超過 90 天
    t = v2_today.track(conn, NOW)
    got = {r["id"] for r in t["rows"] if r["cat"] == "contract"}
    raw = vn._raw_contract_ids(conn)
    assert got == raw and {i.split(":", 1)[1] for i in raw} >= set() and len(got) == 2
    assert all("p-2" not in r["target"].lower() for r in t["rows"] if r["cat"] == "contract")


def test_P2_容量獨立驗算_非數字安全轉換_不中斷且全過(conn):
    db.insert_hardware(conn, asset_serial="H-1", hostname="h1", ip="192.0.2.51")
    conn.execute("INSERT INTO host_spec (asset_serial, ip, storage_json, collected_at) VALUES ('H-1', '192.0.2.51', ?, '2026-11-04')",
                 ('{"by": "mount", "items": [{"name": "/", "size_gb": "abc", "free_gb": "x"}], "total_gb": "10"}',))
    conn.commit()
    before = vn.FAILS
    vn.check_v2_today(conn)
    assert vn.FAILS == before
    assert any("不是數字" in r["name"] and "1 個值" in r["detail"] for r in vn.RESULTS)   # free_gb "x"（total_gb 是數字字串，不算）


def test_A5_同源的三類在報告上照實標示():
    src = (ROOT / "APP" / "asset-module" / "backend" / "verify_numbers.py").read_text(encoding="utf-8")
    assert "與 1.3 同源，非獨立驗算" in src and "_raw_contract_ids(conn)" in src
