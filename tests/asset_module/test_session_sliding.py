"""有在用就不會登出（2.066.25）：有效 session 每 15 分鐘最多延長一次到「現在＋8 小時」；過期的不復活；回應補發 cookie。"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402

FMT = "%Y-%m-%d %H:%M:%S"


def _db(exp: datetime, born: datetime | None = None):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE sessions (token TEXT PRIMARY KEY, user_id INTEGER, created_at TEXT, expires_at TEXT)")
    c.execute("INSERT INTO sessions VALUES ('t1', 1, ?, ?)", ((born or datetime.now(timezone.utc) - timedelta(hours=1)).strftime(FMT), exp.strftime(FMT)))
    return c


def _row(c):
    return c.execute("SELECT * FROM sessions WHERE token='t1'").fetchone()


def test_用了超過15分鐘_延長到現在加8小時():
    now = datetime.now(timezone.utc)
    c = _db(now + timedelta(hours=7))                      # 1 小時前登入
    assert auth.maybe_extend(c, _row(c)) is True
    new = datetime.strptime(_row(c)["expires_at"], FMT).replace(tzinfo=timezone.utc)
    assert abs((new - (now + auth.SESSION_TTL)).total_seconds()) < 5


def test_剛延長過_15分鐘內不再寫():
    now = datetime.now(timezone.utc)
    c = _db(now + auth.SESSION_TTL - timedelta(minutes=5))
    before = _row(c)["expires_at"]
    assert auth.maybe_extend(c, _row(c)) is False and _row(c)["expires_at"] == before


def test_已過期的不會復活():
    now = datetime.now(timezone.utc)
    c = _db(now - timedelta(minutes=1))
    assert auth.maybe_extend(c, _row(c)) is False
    assert datetime.strptime(_row(c)["expires_at"], FMT).replace(tzinfo=timezone.utc) < now


def test_欄位壞掉不出錯():
    assert auth.maybe_extend(sqlite3.connect(":memory:"), {"token": "x", "expires_at": "garbage", "created_at": "x"}) is False


def test_登入滿24小時_再怎麼用都不延長():
    now = datetime.now(timezone.utc)
    c = _db(now + timedelta(hours=2), born=now - timedelta(hours=20))   # 20 小時前登入，還剩 2 小時
    assert auth.maybe_extend(c, _row(c)) is True                       # 只能延到登入＋24 小時＝再 4 小時，不是 8 小時
    new = datetime.strptime(_row(c)["expires_at"], FMT).replace(tzinfo=timezone.utc)
    assert abs((new - (now + timedelta(hours=4))).total_seconds()) < 5
    c2 = _db(now + timedelta(minutes=30), born=now - timedelta(hours=23, minutes=30))
    assert auth.maybe_extend(c2, _row(c2)) is False                    # 已經到 24 小時上限


def test_並行時只有一個延長成功():
    now = datetime.now(timezone.utc)
    c = _db(now + timedelta(hours=7))
    stale = _row(c)                                                    # 兩個請求都先讀到舊的到期時間
    assert auth.maybe_extend(c, stale) is True
    assert auth.maybe_extend(c, stale) is False


def test_回應會補發cookie_登出不補():
    src = (Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend" / "api.py").read_text(encoding="utf-8")
    i = src.index("async def _refresh_session_cookie")
    body = src[i:i + 600]
    assert "set_session_cookies(response, tok)" in body and "/api/auth/logout" in body
    shared = (Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend" / "_app_shared.py").read_text(encoding="utf-8")
    assert "auth.maybe_extend(conn, session)" in shared and "request.state.refresh_session" in shared
