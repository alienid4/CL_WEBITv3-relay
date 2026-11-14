"""2.058.13（批 23 第三輪獨立審查 cdd4c738）：
P1 天條——IPv4-mapped IPv6（::ffff:10.99.0.11 等）折回 IPv4 後才判 VMware；不是 IP 字面值（八進位、十六進位、整數、縮寫、主機名稱）
批次試密碼／預檢一律不登入、逐筆回「不是有效的 IP」（2.058.14 起改成「只接受 IPv4 位址」）。
P2-1——/api/onboard 打字密碼的重新納管，登入前就用 vmware_targets 擋（原本只有登入後的 VMkernel 探測）；判不出來＝不登入。
P2-2——scrub_secret 的英數字邊界只在密碼自己那一端是英數字時才檢查。"""
import sys
import tempfile
from pathlib import Path

import pytest

from test_v2_port_b23 import ROOT

sys.path.insert(0, str(ROOT / "tests" / "asset_module"))
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

from test_v2_port_b23_review2 import _setup  # noqa: E402

MAPPED = ["::ffff:10.99.0.11", "::FFFF:10.99.0.11", "0:0:0:0:0:ffff:a63:b", "::ffff:a63:b", " ::ffff:10.99.0.11 "]
NOT_IP = ["010.99.0.11", "0x0a.0x63.0.0x0b", "0x0a63000b", "174260235", "10.99.11", "esx-example-01", "esx-example-01.corp.example"]


def test_canon_ip():
    import onboard_eligibility as oe
    for x in MAPPED:
        assert oe.canon_ip(x) == "10.99.0.11", x
    for x in NOT_IP:
        assert oe.canon_ip(x) is None, x
    assert oe.canon_ip("10.99.0.11") == "10.99.0.11"
    assert oe.canon_ip("FE80::1") == "fe80::1"
    assert oe._resolve_ips("::ffff:10.99.0.11") == {"10.99.0.11"}


@pytest.mark.parametrize("ip", MAPPED)
def test_天條_IPv4mapped_批次試密碼與預檢都零次登入(monkeypatch, ip):
    tried = []
    with tempfile.TemporaryDirectory() as tmp:
        tb, client = _setup(tmp, monkeypatch, tried)
        try:
            r = client.post("/api/onboard/batch-probe", json={"ips": [ip], "username": "root", "passwords": ["x1x"]})
            assert r.status_code == 200, r.text
            assert r.json()["results"][0]["vmware"] is True
            r = client.post("/api/onboard/precheck", json={"ips": [ip], "username": "root", "password": "x1x"})
            assert r.status_code == 200, r.text
            assert r.json()["results"][0]["vmware"] is True
            assert tried == []
        finally:
            tb.api.app.dependency_overrides.clear()


@pytest.mark.parametrize("ip", NOT_IP)
def test_不是IP字面值_批次試密碼與預檢都零次登入_逐筆回原因(monkeypatch, ip):
    tried = []
    with tempfile.TemporaryDirectory() as tmp:
        tb, client = _setup(tmp, monkeypatch, tried)
        try:
            r = client.post("/api/onboard/batch-probe", json={"ips": [ip], "username": "root", "passwords": ["x1x"]})
            assert r.status_code == 200, r.text
            row = r.json()["results"][0]
            assert row["error"] == "只接受 IPv4 位址" and row["matched_password_index"] is None and row["ip"] == ip
            r = client.post("/api/onboard/precheck", json={"ips": [ip], "username": "root", "password": "x1x"})
            assert r.status_code == 200, r.text
            row = r.json()["results"][0]
            assert row["reason"] == "只接受 IPv4 位址" and row["onboardable"] is False
            assert tried == []
        finally:
            tb.api.app.dependency_overrides.clear()


def test_一般IP照常試_混在一起只登一般那台(monkeypatch):
    tried = []
    with tempfile.TemporaryDirectory() as tmp:
        tb, client = _setup(tmp, monkeypatch, tried)
        try:
            ips = ["10.99.0.12", "::ffff:10.99.0.11", "010.99.0.11", "::ffff:10.99.0.12"]
            r = client.post("/api/onboard/batch-probe", json={"ips": ips, "username": "root", "passwords": ["x1x"]})
            assert r.status_code == 200, r.text
            res = r.json()["results"]
            assert [x["ip"] for x in res] == ["10.99.0.12", "10.99.0.11", "010.99.0.11"]   # mapped 折回後去重
            assert tried == ["10.99.0.12"]
            tried.clear()
            r = client.post("/api/onboard/precheck", json={"ips": ips, "username": "root", "password": "x1x"})
            assert r.status_code == 200, r.text
            out = r.json()
            assert [x["ip"] for x in out["results"]] == ["10.99.0.12", "10.99.0.11", "010.99.0.11"]
            assert sum(out["counts"].values()) == 3
            assert tried == ["10.99.0.12"]
        finally:
            tb.api.app.dependency_overrides.clear()


def test_預檢_密碼貼進IP欄_逐筆回原因也遮密碼(monkeypatch):
    tried = []
    pw = "Leak#Check-998"
    with tempfile.TemporaryDirectory() as tmp:
        tb, client = _setup(tmp, monkeypatch, tried)
        try:
            for path, body in (("/api/onboard/precheck", {"password": pw}), ("/api/onboard/batch-probe", {"passwords": [pw]})):
                r = client.post(path, json={"ips": [f"bad-{pw}"], "username": "root", **body})
                assert r.status_code == 200, r.text
                assert "只接受 IPv4 位址" in r.text and pw not in r.text and "已隱藏" in r.text
            assert tried == []
        finally:
            tb.api.app.dependency_overrides.clear()


def _onboard_client(tmp, monkeypatch, calls):
    tried = []
    tb, client = _setup(tmp, monkeypatch, tried)
    import onboard_engine

    def fake_onboard(**kw):
        calls.append(kw["host"])
        raise AssertionError("不可以登入")
    monkeypatch.setattr(onboard_engine, "onboard", fake_onboard)
    return tb, client, tried


@pytest.mark.parametrize("ip", ["10.99.0.11", "::ffff:10.99.0.11"])
def test_天條_api_onboard打字密碼_登入前就擋_零次SSH(monkeypatch, ip):
    calls = []
    with tempfile.TemporaryDirectory() as tmp:
        tb, client, tried = _onboard_client(tmp, monkeypatch, calls)
        try:
            r = client.post("/api/onboard", json={"ip": ip, "platform": "linux", "username": "root", "password": "x1x"})
            assert r.status_code == 400, r.text
            assert "VMware" in r.text and "x1x" not in r.text
            assert calls == [] and tried == []
        finally:
            tb.api.app.dependency_overrides.clear()


def test_天條_api_onboard_判不出來就不登入(monkeypatch):
    calls = []
    with tempfile.TemporaryDirectory() as tmp:
        tb, client, tried = _onboard_client(tmp, monkeypatch, calls)
        import onboard_eligibility as oe

        def boom(*a, **k):
            raise RuntimeError("db locked")
        monkeypatch.setattr(oe, "vmware_targets", boom)
        try:
            r = client.post("/api/onboard", json={"ip": "10.99.0.12", "platform": "linux", "username": "root", "password": "x1x"})
            assert r.status_code == 400, r.text
            assert "無法確認" in r.text and calls == []
        finally:
            tb.api.app.dependency_overrides.clear()


def test_api_onboard_一般主機照常進引擎(monkeypatch):
    calls = []
    with tempfile.TemporaryDirectory() as tmp:
        tb, client, tried = _onboard_client(tmp, monkeypatch, calls)
        try:
            try:
                client.post("/api/onboard", json={"ip": "10.99.0.12", "platform": "linux", "username": "root", "password": "x1x"})
            except AssertionError:
                pass   # 假引擎故意丟例外：只看有沒有走到引擎
            assert calls == ["10.99.0.12"]
        finally:
            tb.api.app.dependency_overrides.clear()


def test_scrub_secret_整欄藏起來_結構欄位不動():
    """2.058.14 審查 P2-4（取代 2.058.13 的邊界規則）。"""
    from _app_shared import scrub_secret as s, SECRET_HIDDEN as H
    assert s([{"ip": "10.99.0.1", "error": "auth rootPass123 failed"}], "Pass123") == [{"ip": "10.99.0.1", "error": H}]
    assert s([{"ip": "10.99.0.1", "error": "登入被拒"}], "1") == [{"ip": "10.99.0.1", "error": "登入被拒"}]
    assert s([{"ip": "10.99.0.11", "host": "h1", "serial": "S1", "error": "x"}], "1") == [
        {"ip": "10.99.0.11", "host": "h1", "serial": "S1", "error": "x"}]
    row = {"ip": "10.99.0.5", "message": "bad password here", "reason": "ok", "stderr": "no"}
    assert s(row, "password") == {"ip": "10.99.0.5", "message": H, "reason": "ok", "stderr": "no"}
    assert s("Permission denied", "den") == H                               # 子字串也算（寧可整句藏起來）
    assert s({"detail": {"msg": ["a pw1 b"]}}, "pw1") == {"detail": {"msg": [H]}}
