"""第三輪複查（GPT 5.6）補丁：外部腳本設定驗證、補跑執行緒鎖釋放。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module"))
import scan_segments  # noqa: E402


def _cfg(tmp_path, start, end):
    p = tmp_path / "q.json"
    p.write_text(json.dumps({"format": "webit3-quiet-window", "version": 1, "enabled": True,
                             "start": start, "end": end, "holidays": []}), encoding="utf-8")
    return str(p)


@pytest.mark.parametrize("start,end", [("99:99", "15:00"), ("08:30", "25:00"), ("15:00", "08:30"), ("08:30", "08:30")])
def test_非法時間設定視同窗內(tmp_path, start, end):
    assert scan_segments.load_quiet_config(_cfg(tmp_path, start, end)).get("broken") is True


def test_合法設定可讀(tmp_path):
    c = scan_segments.load_quiet_config(_cfg(tmp_path, "08:30", "15:00"))
    assert c["start_hm"] == (8, 30) and c["end_hm"] == (15, 0)


def test_補跑開庫失敗也放鎖(monkeypatch):
    import threading
    import time

    import quiet_window as qw
    import scan_service as ss
    monkeypatch.setattr(qw, "in_quiet_window", lambda conn: False)
    monkeypatch.setattr(ss, "is_running", lambda: False)
    monkeypatch.setattr(qw, "pending_list", lambda conn: [{"entry": "x"}])
    monkeypatch.setattr(ss, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    ss._quiet_catchup_tick(object())
    for _ in range(50):
        if not ss._catchup_lock.locked():
            break
        time.sleep(0.05)
    assert not ss._catchup_lock.locked()


def test_掃描中途進窗會重新標待補跑_跑完才結算(tmp_path):
    import quiet_window as qw
    import scan_service as ss
    from db import get_connection, init_db
    dbp = tmp_path / "t.db"
    init_db(dbp)
    c = get_connection(dbp)
    ss._scan_pending_settle(c, "schedule", paused=True, ok=False)
    assert qw.pending_is(c, "scan_schedule")
    ss._scan_pending_settle(c, "schedule", paused=False, ok=True)
    assert not qw.pending_is(c, "scan_schedule")
    ss._scan_pending_settle(c, "manual", paused=True, ok=False)      # 非排程觸發不碰待辦
    assert not qw.pending_is(c, "scan_schedule")
    c.close()


def test_設定庫開不起來_擋下且狀態條有警示(monkeypatch):
    import quiet_window as qw
    import db
    monkeypatch.setattr(db, "get_connection", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db")))
    qw._HEALTH.pop("config_unreadable", None)
    run = qw.Run("scan_schedule", human=False, db_path="x.db")
    assert qw.may_connect("scan_schedule", run) is False
    assert run.paused is True
    assert any("讀不到靜默窗設定" in m for m in qw.health_warnings())


def test_票證換人使用即失效():
    import quiet_window as qw
    t = qw.issue_ticket("scan_schedule", "alice")
    assert qw.consume_ticket(t, "scan_schedule", "bob") is False
    t2 = qw.issue_ticket("scan_schedule", "alice")
    assert qw.consume_ticket(t2, "scan_schedule", "alice") is True
