"""EOS/EOL 對照表查詢邏輯：只測比對邏輯本身，不驗證表裡的日期是否為真
（那是研究得來的資料，日期正確性靠官方來源＋交叉驗證，不是這裡的測試範圍）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import eos  # noqa: E402


def _with_tables(monkeypatch, os_entries, hw_entries):
    monkeypatch.setattr(eos, "_os_table", os_entries)
    monkeypatch.setattr(eos, "_hw_table", hw_entries)


def test_os_eos_完全相等優先(monkeypatch):
    _with_tables(monkeypatch, [
        {"name": "Windows Server 2022", "eos_date": "2031-10-14", "source_url": "https://x", "note": ""},
    ], [])
    hit = eos.lookup_os_eos("Windows Server 2022")
    assert hit and hit["eos_date"] == "2031-10-14"


def test_os_eos_退回大版比對(monkeypatch):
    """canonical_os 常帶小版（Rocky Linux 9.7），EOS 通常以大版公告（Rocky Linux 9）。"""
    _with_tables(monkeypatch, [
        {"name": "Rocky Linux 9", "eos_date": "2032-05-31", "source_url": "https://x", "note": ""},
    ], [])
    hit = eos.lookup_os_eos("Rocky Linux 9.7")
    assert hit and hit["eos_date"] == "2032-05-31"


def test_os_eos_查不到回none(monkeypatch):
    _with_tables(monkeypatch, [], [])
    assert eos.lookup_os_eos("某個沒人查過的怪東西") is None
    assert eos.lookup_os_eos(None) is None


def test_hardware_eos_包含比對取最長命中(monkeypatch):
    """子型號比家族名精確，命中兩個都符合時要選比較精確（字串較長）的那個。"""
    _with_tables(monkeypatch, [], [
        {"name": "Cisco Catalyst 9300", "eos_date": "2029-01-01", "source_url": "https://x", "note": "系列"},
        {"name": "Cisco Catalyst 9300X-24Y", "eos_date": "2030-06-01", "source_url": "https://y", "note": "子型號"},
    ])
    hit = eos.lookup_hardware_eos("Cisco Catalyst 9300X-24Y-A")
    assert hit and hit["eos_date"] == "2030-06-01"


def test_hardware_eos_單一泛用字不比對(monkeypatch):
    """canonical_model 常常沒被 normalize_model 收斂（unmatched，canonical＝原值），
    萬一原值只剩「Cisco」這種泛用廠牌字，不能讓每一筆名字含「cisco」的 EOS 項目
    都算命中——寧可查不到，也不要給一個查來的假日期。"""
    _with_tables(monkeypatch, [], [
        {"name": "Cisco Catalyst 9300 series switch", "eos_date": "2029-01-01", "source_url": "https://x", "note": ""},
    ])
    assert eos.lookup_hardware_eos("Cisco") is None


def test_hardware_eos_查不到回none(monkeypatch):
    _with_tables(monkeypatch, [], [])
    assert eos.lookup_hardware_eos("沒收錄的型號") is None


def test_eos_status三態():
    import datetime
    past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    soon = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    far = (datetime.date.today() + datetime.timedelta(days=900)).isoformat()
    assert eos.eos_status(past) == "expired"
    assert eos.eos_status(soon) == "upcoming"
    assert eos.eos_status(far) == "ok"
    assert eos.eos_status(None) == "unknown"


def test_資料檔真的能載入且是合法json():
    """確保實際部署會讀到的檔案存在且格式正確，不是空殼漏放。"""
    data_dir = ROOT / "APP" / "asset-module" / "backend" / "eos_data"
    for name in ("os_eos.json", "hardware_eos.json"):
        data = json.loads((data_dir / name).read_text(encoding="utf-8"))
        assert isinstance(data, list)
        for entry in data:
            assert "name" in entry and "eos_date" in entry and "source_url" in entry
