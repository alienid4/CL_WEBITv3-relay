"""採集底座：網段掃描來源測試（注入 probe，不打真網路，確定性）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import net_scan  # noqa: E402
from net_scan import NetworkSweepSource  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch):
    # 測試不做真實反解，避免慢/不確定
    monkeypatch.setattr(net_scan, "_hostname", lambda ip: None)


def test_sweep_只回活著的主機():
    alive = {"10.0.0.5": [22, 80], "10.0.0.6": [443]}  # /29 合法主機為 .1–.6（.7 是廣播）

    def fake_probe(ip, timeout=0.6):
        return alive.get(ip)  # 不在清單 = None = 沒回應

    src = NetworkSweepSource("10.0.0.0/29", probe=fake_probe)
    results = src.scan()

    assert sorted(r.ip for r in results) == ["10.0.0.5", "10.0.0.6"]
    assert all(r.segment == "10.0.0.0/29" for r in results)
    r5 = next(r for r in results if r.ip == "10.0.0.5")
    assert "open:22,80" in r5.device_model  # open port 有記進 device_model


def test_全部沒回應回空清單():
    src = NetworkSweepSource("10.0.0.0/29", probe=lambda ip, timeout=0.6: None)
    assert src.scan() == []


def test_網段格式錯誤要raise不能吞掉():
    src = NetworkSweepSource("not-a-cidr", probe=lambda ip, timeout=0.6: None)
    with pytest.raises(ConnectionError):
        src.scan()
