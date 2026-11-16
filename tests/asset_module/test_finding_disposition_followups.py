"""#7 補強：沒帶欄位保留／帶空字串或 null 清除、URL 進階攻擊樣式、時區固定 UTC+8。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_inventory as ai  # noqa: E402
import db  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _row(conn):
    return conn.execute("SELECT * FROM finding_disposition WHERE ip='10.99.0.1'").fetchone()


def _set(conn, status="fixed", **kw):
    return ai.set_finding_disposition(conn, "10.99.0.1", "alice", "R1", status, **kw)


def test_沒帶欄位保留_帶空字串或None清除(tmp_path):
    conn = _conn(tmp_path)
    try:
        _set(conn, "fixed", fixed_at="2026-10-30", verified_at="2026-10-31", verify_due="2026-12-01",
             ref_url="https://itsm.example.test/T-5")
        _set(conn, "fixed", note="只改備註")                      # 四個欄位都沒帶
        r = _row(conn)
        assert r["ref_url"] == "https://itsm.example.test/T-5"
        assert (r["fixed_at"], r["verified_at"], r["verify_due"]) == ("2026-10-30", "2026-10-31", "2026-12-01")
        _set(conn, "fixed", ref_url="")                          # 空字串＝清除
        r = _row(conn)
        assert r["ref_url"] is None and r["fixed_at"] == "2026-10-30"
        _set(conn, "fixed", verified_at=None)                    # 明確 None＝清除該欄
        assert _row(conn)["verified_at"] is None and _row(conn)["fixed_at"] == "2026-10-30"
    finally:
        conn.close()


BAD_URLS = [
    "https://ｅxample.com/a",            # 全形 ｅ
    "https://examplе.com/a",            # 西里爾 е
    "https://example.com%0a.evil.test/",
    "https://exa%6dple.com/",
    "https://exam​ple.com/a",           # 零寬空白 Cf
    "https://example.com/a‮bc",         # RTL override Cf
    "https://example.com/a b",          # NBSP Zs
    "https://user:pass@example.com/",
    "https://user@example.com/",
]


@pytest.mark.parametrize("bad", BAD_URLS)
def test_URL進階攻擊樣式被拒(tmp_path, bad):
    conn = _conn(tmp_path)
    try:
        with pytest.raises(ValueError):
            _set(conn, "ack", ref_url=bad)
    finally:
        conn.close()


def test_URL大寫scheme與punycode可接受(tmp_path):
    conn = _conn(tmp_path)
    try:
        _set(conn, "ack", ref_url="HTTP://itsm.example.test/x")
        assert _row(conn)["ref_url"] == "HTTP://itsm.example.test/x"
        _set(conn, "ack", ref_url="https://xn--fsq.example.test/x")
        assert _row(conn)["ref_url"] == "https://xn--fsq.example.test/x"
    finally:
        conn.close()


def test_時區換算不受主機TZ影響(tmp_path, monkeypatch):
    import time
    if not hasattr(time, "tzset"):
        pytest.skip("Windows 沒有 tzset；固定 UTC+8 的結果已由 test_帶時區的時間換成固定UTC+8 鎖住")
    conn = _conn(tmp_path)
    try:
        for tz in ("UTC", "America/New_York", "Asia/Tokyo"):
            monkeypatch.setenv("TZ", tz)
            time.tzset()
            _set(conn, "fixed", fixed_at="2026-10-30T01:00:00Z")
            assert _row(conn)["fixed_at"] == "2026-10-30 09:00:00"
    finally:
        conn.close()


def test_帶時區的時間換成固定UTC加8(tmp_path):
    conn = _conn(tmp_path)
    try:
        for v in ("2026-10-30T01:00:00Z", "2026-10-30T09:00:00+08:00", "2026-10-29T20:00:00-05:00"):
            _set(conn, "fixed", fixed_at=v)
            assert _row(conn)["fixed_at"] == "2026-10-30 09:00:00"
    finally:
        conn.close()


def test_API沒帶保留_空字串與null清除(tmp_path):
    import api
    import test_finding_lifecycle as L

    client, p = L._client(str(tmp_path))
    try:
        c = db.get_connection(p)
        try:
            db.create_user(c, "admin", L.auth.hash_password(L._PW))
            L._seed(c)
            L.account_inventory.collect_accounts(c, runner=L._runner)
        finally:
            c.close()
        client.post("/api/auth/login", json={"username": "admin", "password": L._PW})
        client.headers[L.auth.CSRF_HEADER_NAME] = L.auth.csrf_token_for(
            client.cookies.get(L.auth.SESSION_COOKIE_NAME))
        c = db.get_connection(p)
        try:
            f = ai.latest_findings(c)[0]
        finally:
            c.close()
        key = {"ip": f["ip"], "username": f["username"], "rule_id": f["rule_id"]}

        def put(**kw):
            return client.put("/api/accounts/findings/disposition", json={**key, **kw})

        def got():
            return [x for x in client.get("/api/accounts/findings").json()["items"]
                    if x["rule_id"] == f["rule_id"] and x["username"] == f["username"]][0]

        url = "https://itsm.example.test/T-7"
        assert put(status="fixed", fixed_at="2026-10-30", ref_url=url).status_code == 200
        assert put(status="fixed").status_code == 200
        assert got()["ref_url"] == url and got()["fixed_at"] == "2026-10-30"        # 沒帶＝保留
        assert put(status="ack").status_code == 200
        assert got()["ref_url"] == url and got()["fixed_at"] is None                # 非已修復清時間、留連結
        assert put(status="ack", ref_url="").status_code == 200
        assert got()["ref_url"] is None                                             # 空字串清除
        put(status="ack", ref_url=url)
        put(status="ack", ref_url=None)
        assert got()["ref_url"] is None                                             # null 清除
    finally:
        api.app.dependency_overrides.clear()
