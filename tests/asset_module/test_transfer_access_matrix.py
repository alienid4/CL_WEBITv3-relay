"""檔案傳送（功能地圖 #213）驗收：「只限管理員」與「開盤時段不開放」雙向測試（同步定案 ESJE 第 9 點：通過即視為功能 100%）。

雙向＝該擋的擋、該放的放；只測一邊的話，「全部擋掉」也會過。
既有的單邊測試在 test_transfer_q94.py（不是admin一律403、admin可以用 status、排隊等時段、抓回遵守時段），這裡補對面那一邊與邊界。
環境、假主機、假傳送都沿用 test_transfer_q94 的 env fixture（不碰網路）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_transfer_q94 import SAT10, THU10, _call, _transfer_routes, env, make_job, pol, upload_lib  # noqa: E402,F401

ROLE_AWARE_TESTS = True          # 用正式的角色規則（bob 是一般使用者），見 tests/conftest.py 的 Q91 相容層
FRI10 = pol.at(2026, 10, 30, 10, 0)


# ── 只限管理員：雙向 ──
@pytest.mark.parametrize("when", ["週六", "週四開盤"])
def test_非admin_開不開盤都一律403(env, when):
    env.set_now(SAT10 if when == "週六" else THU10)
    bob = env.client("bob")
    bad = [f"{m} {p} -> {r.status_code}" for m, p in _transfer_routes()
           if (r := _call(bob, m, p)).status_code != 403 or "admin" not in r.json().get("detail", "")]
    assert not bad, bad


def test_admin_每支端點都不被權限擋(env):
    """反方向：同樣的呼叫換成 admin，沒有一支回「只限管理員」的 403（參數不對回 4xx 可以，但不能是權限擋）。"""
    routes = _transfer_routes()
    assert len(routes) >= 10
    bad = []
    for m, p in routes:
        r = _call(env.admin, m, p)
        if r.status_code in (401, 403):
            bad.append(f"{m} {p} -> {r.status_code} {r.text[:80]}")
    assert not bad, bad


# ── 開盤時段不開放：雙向（中型檔 10～100MB 用縮小的門檻模擬）──
@pytest.mark.parametrize("label,at,expect", [
    ("週四開盤前 08:00", pol.at(2026, 10, 29, 8, 0), "done"),
    ("週四 08:30 開盤", pol.at(2026, 10, 29, 8, 30), "waiting_window"),
    ("週四 14:59 盤中", pol.at(2026, 10, 29, 14, 59), "waiting_window"),
    ("週四 15:00 收盤", pol.at(2026, 10, 29, 15, 0), "done"),
    ("週五 10:00 盤中", FRI10, "waiting_window"),
    ("週六 10:00", SAT10, "done"),
])
def test_開盤時段_中型檔_該擋的擋該放的放(env, monkeypatch, label, at, expect):
    monkeypatch.setattr(pol, "SMALL_MAX", 10)
    monkeypatch.setattr(pol, "MID_MAX", 100)
    f = upload_lib(env.admin, "mid.bin", b"x" * 50)
    env.set_now(at)
    j = make_job(env.admin, f, ["10.99.8.1"]).json()
    assert j["status"] == expect, (label, j)
    uploaded = any(c[0] == "upload" for c in env.fake.calls)
    assert uploaded is (expect == "done"), label                     # 擋下的一台都沒開始送


def test_開盤時段_小檔照樣可傳(env, monkeypatch):
    monkeypatch.setattr(pol, "SMALL_MAX", 10)
    monkeypatch.setattr(pol, "MID_MAX", 100)
    f = upload_lib(env.admin, "small.bin", b"x" * 5)
    env.set_now(THU10)
    assert make_job(env.admin, f, ["10.99.8.1"]).json()["status"] == "done"


@pytest.mark.parametrize("at,code", [(THU10, 409), (pol.at(2026, 10, 29, 15, 0), 200), (SAT10, 200)])
def test_開盤時段_抓回中型檔_雙向(env, monkeypatch, at, code):
    monkeypatch.setattr(pol, "SMALL_MAX", 10)
    monkeypatch.setattr(pol, "MID_MAX", 100)
    env.fake.put("10.99.8.1", "/tmp", "mid.dump", b"D" * 50)
    env.set_now(at)
    r = env.admin.post("/api/transfer/pull", json={"ip": "10.99.8.1", "name": "mid.dump"})
    assert r.status_code == code, r.text
    assert (len(env.admin.get("/api/transfer/library").json()["files"]) == 1) is (code == 200)
