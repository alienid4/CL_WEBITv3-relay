"""品質分析頁的 SSH 測連線（2026-09-15）。

要守的：
1. 只收 IP——子行程參數不可能被塞東西；呼叫子行程不經 shell
2. 舊版函式庫只在子行程的 PYTHONPATH 出現，沒裝時要講清楚
3. 每次測試都寫收集紀錄
"""
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import ssh_probe  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    p = tmp_path / "t.db"
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


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.parametrize("bad", ["switch01", "192.0.2.1; rm -rf /", "", "192.0.2.1 -oProxyCommand=x"])
def test_只收IP(bad):
    with pytest.raises(ValueError):
        ssh_probe.validate(bad, 22)


@pytest.mark.parametrize("port", [0, 70000, "abc"])
def test_埠號要合法(port):
    with pytest.raises(ValueError):
        ssh_probe.validate("192.0.2.1", port)


def test_舊版函式庫沒裝要講清楚(tmp_path, monkeypatch):
    monkeypatch.setattr(ssh_probe, "LEGACY_LIB", tmp_path / "nope")
    r = ssh_probe.probe_legacy("192.0.2.1", 22)
    assert r["available"] is False and "還沒安裝" in r["lines"][0]


def test_舊版用子行程_參數清單不經shell_PYTHONPATH只指隔離資料夾(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    (lib / "paramiko").mkdir(parents=True)
    (lib / "paramiko" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(ssh_probe, "LEGACY_LIB", lib)
    seen = {}

    def fake_run(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return subprocess.CompletedProcess(args, 0, stdout="✓ SSH 談判成功\n", stderr="")

    monkeypatch.setattr(ssh_probe.subprocess, "run", fake_run)
    r = ssh_probe.probe_legacy("192.0.2.10", 22)
    assert r["code"] == 0
    assert seen["args"] == [sys.executable, str(ssh_probe.TOOL), "192.0.2.10", "22"]
    assert "shell" not in seen["kw"] or seen["kw"]["shell"] is False
    assert seen["kw"]["env"]["PYTHONPATH"] == str(lib)


def test_API_不合法IP回400(client):
    r = client.post("/api/tools/ssh-probe", json={"ip": "switch01", "port": 22})
    assert r.status_code == 400


def test_API_連不上也回結論並留紀錄(client, monkeypatch, tmp_path):
    monkeypatch.setattr(ssh_probe, "LEGACY_LIB", tmp_path / "nope")
    r = client.post("/api/tools/ssh-probe", json={"ip": "127.0.0.1", "port": _closed_port()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["modern"]["code"] == 3 and "TCP 連不上" in body["verdict"]
    items = client.get("/api/system/collect-log", params={"kind": "ssh_probe"}).json()["items"]
    assert items and items[0]["kind"] == "ssh_probe"
