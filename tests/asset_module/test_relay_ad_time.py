"""AD 對時走中繼（2.060.x）：9.26 的 ad_time.ps1（w32tm /monitor）→ relay_import 的 ad_time 來源 → ad_time 判定。

示範資料只用 *.example.test、192.0.2.x；不連網路。腳本只做靜態檢查。
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ad_time  # noqa: E402
import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402
from test_relay_import import SAT, write_batch  # noqa: E402


def dc(name, off=0.0, ok=True, note=""):
    return {"name": name, "ip": "192.0.2.10", "ok": ok, "offset_s": off if ok else None, "note": note}


def _ai_script(name):
    """AI/ 不進公開中繼（去識別化快照裡沒有）：不在就跳過，正本一致性只在私有 repo 驗。"""
    p = ROOT / "AI" / "tools_relay" / name
    if not p.exists():
        pytest.skip("AI/ 不在這份快照（公開中繼）")
    return p.read_text(encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)
    qw.set_clock(lambda: SAT(10))
    db.set_setting(c, ri.DIR_KEY, str(drop))
    db.set_setting(c, ri.ENABLED_KEY, "1")
    yield c, drop
    qw.set_clock(None)
    c.close()


def send(drop, name, dcs, when=None, fname="dcs.json"):
    when = when or datetime.now(timezone.utc)
    return write_batch(drop, name, {fname: {"dcs": dcs}}, source="ad_time", collected=when.strftime("%Y-%m-%dT%H:%M:%S+00:00"))


def test_沒資料就是無資料_不建表(env):
    c, _ = env
    r = ad_time.latest(c)
    assert r["status"] == "no_data" and "從未收到" in r["reason"]
    assert c.execute("SELECT name FROM sqlite_master WHERE name='ad_time_batch'").fetchone() is None


def test_全部在門檻內_正常(env):
    c, drop = env
    send(drop, "adtime_1", [dc("dc01.corp.example.test", 0.0), dc("dc02.corp.example.test", -0.4)])
    res = ri.run_scan(c, "manual", "admin")
    assert res["results"][0]["status"] == "imported", res
    r = ad_time.latest(c)
    assert r["status"] == "ok" and [d["verdict"] for d in r["dcs"]] == ["normal", "normal"]


def test_超過門檻_異常_門檻可設(env):
    c, drop = env
    send(drop, "adtime_1", [dc("dc01.corp.example.test", 0.0), dc("dc02.corp.example.test", 2.5)])
    ri.run_scan(c, "manual", "admin")
    assert ad_time.latest(c)["status"] == "warn"
    db.set_setting(c, ad_time.WARN_KEY, "3")
    assert ad_time.latest(c)["status"] == "ok"


def test_沒回應的DC_無資料_不當正常(env):
    c, drop = env
    send(drop, "adtime_1", [dc("dc01.corp.example.test", 0.0), dc("dc03.corp.example.test", ok=False, note="error ERROR_TIMEOUT")])
    ri.run_scan(c, "manual", "admin")
    r = ad_time.latest(c)
    assert r["status"] == "no_data" and r["dcs"][1]["verdict"] == "no_data"


def test_資料太舊_無資料(env):
    c, drop = env
    send(drop, "adtime_1", [dc("dc01.corp.example.test", 0.0)], when=datetime.now(timezone.utc) - timedelta(hours=30))
    ri.run_scan(c, "manual", "admin")
    r = ad_time.latest(c)
    assert r["status"] == "no_data" and "太舊" in r["reason"]


@pytest.mark.parametrize("dcs,fname", [
    ([], "dcs.json"),
    ([{"name": "dc01", "ok": True}], "dcs.json"),
    ([dc("dc01"), dc("DC01")], "dcs.json"),
    ([dc("dc01")], "other.json"),
])
def test_壞資料拒收(env, dcs, fname):
    c, drop = env
    send(drop, "adtime_bad", dcs, fname=fname)
    res = ri.run_scan(c, "manual", "admin")
    assert res["results"][0]["status"] == "rejected", res


def test_腳本_唯讀_無反斜線():
    s = _ai_script("ad_time.ps1")
    assert s.startswith("# AD-TIME-SCRIPT v1") and "w32tm" in s and "/monitor" in s
    assert "source = 'ad_time'" in s and s.index("'manifest.json'") > s.index("'dcs.json'")
    for bad in ("/resync", "/config", "/register", "Set-Date", "Remove-", "Invoke-WebRequest"):
        assert bad not in s
    assert "\\" not in s and "`" not in s and "${" not in s


def test_devkit_D9_內嵌和AI正本一致():
    dk = (ROOT / "APP" / "asset-module" / "frontend" / "content" / "devkit.ts").read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))
    src = _ai_script("ad_time.ps1").replace(chr(13) + chr(10), chr(10))
    assert dk.count(src) == 1
