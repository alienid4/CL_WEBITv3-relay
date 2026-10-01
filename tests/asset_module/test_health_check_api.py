"""健檢端點的摘要：`incomplete` 只能有一個意思。

2026-09-23 差點造出第三個「兩份定義」：後端的 incomplete 把「連不上」也算進去，
前端要顯示的卻是「連得到卻沒查完」，於是前端自己又算了一份。

兩個地方各算一次 ＝ 兩份定義，遲早對不起來；使用者一旦看過一次數字對不上，
之後兩個數字都不會信。所以語意收斂在後端，前端直接用。

「連不上」有自己的名字（unreachable）。**同一個名字不可以在兩個地方是兩個意思。**
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import health_probe  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _row(ip, *, reachable, complete, overall, done, total=9, not_probed=None):
    """做一筆長得像 check_one 回傳的資料——只留這個測試會看的欄位。"""
    return {
        "ip": ip, "platform": "linux", "reachable": reachable, "error": None,
        "unresolved": False, "overall": overall, "notes": [],
        "not_probed": not_probed,
        "complete": complete,
        "coverage": {"done": done, "total": total, "missing": [], "text": ""},
    }


@pytest.fixture()
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)

        def _get_db():
            c = db.get_connection(p)
            try:
                yield c
            finally:
                c.close()

        api.app.dependency_overrides[api.get_db] = _get_db
        api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester"}
        try:
            yield TestClient(api.app, raise_server_exceptions=False)
        finally:
            api.app.dependency_overrides.pop(api.get_db, None)
            api.app.dependency_overrides.pop(api.require_auth, None)


def test_incomplete只算連得到卻沒查完的(client, monkeypatch):
    """連不上的那台什麼都沒查到，但它已經算在 unreachable 也顯示成紅色。

    再把它算進 incomplete，等於把真正要看的那批（連得到、綠燈、但少查了幾項）
    稀釋掉——值班點下去會發現數字跟列數對不起來。
    """
    rows = [
        _row("10.99.0.10", reachable=True, complete=False, overall="green", done=8),
        _row("10.99.0.11", reachable=True, complete=True, overall="green", done=9),
        _row("10.99.0.99", reachable=False, complete=False, overall="red", done=0),
    ]
    monkeypatch.setattr(health_probe, "check_batch", lambda *a, **k: rows)

    r = client.post("/api/health-check/run",
                    json={"ips": ["10.99.0.10", "10.99.0.11", "10.99.0.99"]})
    assert r.status_code == 200
    s = r.json()["summary"]
    assert s["incomplete"] == 1, "連不上的被算進未完整了"
    # 「連不上」有自己的名字，兩個數字各自代表一件事
    assert s["unreachable"] == 1
    assert s["total"] == 3 and s["green"] == 2 and s["red"] == 1


def test_全部查完時incomplete是0(client, monkeypatch):
    """0 要真的是 0——有值班會用「未完整檢查 0」當作「這批都查齊了」的依據。"""
    rows = [_row("10.99.0.11", reachable=True, complete=True, overall="green", done=9)]
    monkeypatch.setattr(health_probe, "check_batch", lambda *a, **k: rows)

    r = client.post("/api/health-check/run", json={"ips": ["10.99.0.11"]})
    assert r.json()["summary"]["incomplete"] == 0


# ── 「連不上」與「我們沒去成」是兩件事 ────────────────────────────────
# 2026-09-23 AIX 那批全部顯示成「連不上」，真因是我們自己拿錯帳號
# （webit3scan vs webit3sc）——**我們自己的問題，顯示成對方機器的問題**。
def test_沒有憑證不可以讓連不上的數字增加(client, monkeypatch):
    """「沒有憑證」的主詞是我們，「連不上」的主詞是那台機器。

    混在一起，值班會跑去機房看一台好好的機器，而正確的下一步是
    走三步到設定頁補一個帳號。**這個代價是具體的，不是潔癖。**
    """
    rows = [
        _row("10.99.0.21", reachable=False, complete=False, overall="skipped", done=0,
             not_probed="沒有可用的 WinRM 收集憑證"),
        _row("10.99.0.99", reachable=False, complete=False, overall="red", done=0),
    ]
    monkeypatch.setattr(health_probe, "check_batch", lambda *a, **k: rows)

    s = client.post("/api/health-check/run",
                    json={"ips": ["10.99.0.21", "10.99.0.99"]}).json()["summary"]
    assert s["unreachable"] == 1, "沒有憑證那台被算進連不上了"
    assert s["not_probed"] == 1
    # 那台不可以算進「異常」——那是我們的待辦，不是那台的狀態
    assert s["red"] == 1


def test_沒去成的不算未完整檢查(client, monkeypatch):
    """未完整檢查是「查了但沒查齊」；沒去探測過是另一件事，不要互相灌水。"""
    rows = [_row("10.99.0.21", reachable=False, complete=False, overall="skipped",
                 done=0, not_probed="沒有金鑰")]
    monkeypatch.setattr(health_probe, "check_batch", lambda *a, **k: rows)
    s = client.post("/api/health-check/run", json={"ips": ["10.99.0.21"]}).json()["summary"]
    assert s["incomplete"] == 0, "沒去探測過的不該算成『查了但沒查齊』"
    assert s["not_probed"] == 1
