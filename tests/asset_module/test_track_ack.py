"""1.4 追蹤處理「標已處理」（2.066.24）：標記、到期自動回來、取消留紀錄、權限、不改台數口徑。"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import track_ack  # noqa: E402


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def test_沒建表也能讀_回空():
    assert track_ack.active(_conn()) == {}


def test_標記後在期限內有效_到期自動失效():
    c = _conn()
    t0 = datetime(2026, 11, 15, 10, 0)
    r = track_ack.mark(c, "hardware:abc", "alice", "已通知", 7, now=t0)
    assert r["until"] == "2026-11-22"
    assert track_ack.active(c, datetime(2026, 11, 21, 23, 0))["hardware:abc"]["by"] == "alice"
    assert "hardware:abc" not in track_ack.active(c, datetime(2026, 11, 22, 0, 1))


def test_重標蓋過舊的_取消不刪紀錄():
    c = _conn()
    t0 = datetime(2026, 11, 15, 10, 0)
    track_ack.mark(c, "spof:A01", "alice", None, 3, now=t0)
    track_ack.mark(c, "spof:A01", "bob", "改期", 14, now=t0)
    act = track_ack.active(c, t0)
    assert act["spof:A01"]["by"] == "bob" and act["spof:A01"]["until"] == "2026-11-29"
    assert track_ack.clear(c, "spof:A01", "bob", now=t0) == 1
    assert track_ack.active(c, t0) == {}
    assert c.execute("SELECT COUNT(*) FROM track_ack").fetchone()[0] == 2     # 稽核：兩筆都還在


@pytest.mark.parametrize("item,days", [("bad id", 7), ("hardware:x", 0), ("hardware:x", 31), ("", 7)])
def test_不合法輸入擋下(item, days):
    with pytest.raises(ValueError):
        track_ack.mark(_conn(), item, "alice", None, days)


def test_track_帶已處理但不改口徑(monkeypatch):
    import v2_today
    rows = [{"id": "hardware:a", "level": "高"}, {"id": "hardware:b", "level": "高"}]
    monkeypatch.setattr(v2_today, "_compute", lambda conn, now: {"rows": [dict(r) for r in rows], "total": 2, "high": 2, "categories": []})
    monkeypatch.setattr(v2_today, "changes", lambda conn, now: {"new_ids": []})
    c = _conn()
    now = datetime(2026, 11, 15, 10, 0)
    track_ack.mark(c, "hardware:a", "alice", None, 7, now=now)
    t = v2_today.track(c, now)
    assert t["total"] == 2 and t["high"] == 2          # 後端口徑不變
    assert t["acked"] == 1
    assert t["rows"][0]["acked"]["by"] == "alice" and t["rows"][1]["acked"] is None


def test_寫入端點只給一般帳號以上():
    import access_policy
    assert ("POST", "/api/v2/track/ack") in access_policy.USER_WRITE
    assert ("POST", "/api/v2/track/unack") in access_policy.USER_WRITE
