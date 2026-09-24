"""OS 類型人工指定（2026-09-18）。

使用者：「如果少數錯誤，我可以手動編輯 OS 類型搬移」。另存一張表，重匯 CIA 不會蓋掉。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import os_override  # noqa: E402
import pipeline  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="A-1", hostname="box1", ip="192.0.2.1",
                       os="N/A", device_model="(VM)", asset_status="使用中")
    c.commit()
    return c


def _os_of(c, ip):
    out = pipeline.summarize(c)
    return [i for i in out["items"] if i["ip"] == ip][0], out


def test_人工指定會生效且看得出是手動(tmp_path):
    c = _conn(tmp_path)
    before, _ = _os_of(c, "192.0.2.1")
    assert before["os_type"] == "未填" and before["os_type_override"] is None
    os_override.set_override(c, "box1", "192.0.2.1", "A-1", "網路設備", "未填", "Airwave", "tester")
    it, out = _os_of(c, "192.0.2.1")
    assert it["os_type"] == "網路設備"
    assert it["os_type_auto"] == "未填", "系統原本判什麼要留著"
    assert it["os_type_override"]["by"] == "tester"
    assert out["os_counts"]["網路設備"] == 1 and out["os_counts"]["未填"] == 0, "統計要跟著變"


def test_重匯改了OS欄也不會蓋掉人工指定(tmp_path):
    c = _conn(tmp_path)
    os_override.set_override(c, "box1", "192.0.2.1", "A-1", "VMware", None, None, "tester")
    c.execute("UPDATE hardware SET os = 'N/A' WHERE asset_serial = 'A-1'")   # 模擬重匯覆寫
    c.commit()
    assert _os_of(c, "192.0.2.1")[0]["os_type"] == "VMware"


def test_IP被回收給另一台時指定自動失效(tmp_path):
    """主機名不同＝不同台，不可以把舊指定套到新機器上。"""
    c = _conn(tmp_path)
    os_override.set_override(c, "box1", "192.0.2.1", "A-1", "網路設備", None, None, "tester")
    c.execute("UPDATE hardware SET hostname = 'newbox' WHERE asset_serial = 'A-1'")
    c.commit()
    it = _os_of(c, "192.0.2.1")[0]
    assert it["os_type"] == "未填" and it["os_type_override"] is None


def test_恢復自動是軟刪除紀錄保留(tmp_path):
    c = _conn(tmp_path)
    os_override.set_override(c, "box1", "192.0.2.1", "A-1", "網路設備", None, None, "tester")
    assert os_override.clear(c, "box1", "192.0.2.1", "A-1", "tester") == 1
    assert _os_of(c, "192.0.2.1")[0]["os_type"] == "未填"
    assert c.execute("SELECT COUNT(*) FROM os_type_override").fetchone()[0] == 1, "紀錄不可以消失"
    assert os_override.list_active(c) == []


def test_同一台再改只留最新一筆有效(tmp_path):
    c = _conn(tmp_path)
    os_override.set_override(c, "box1", "192.0.2.1", "A-1", "網路設備", None, None, "a")
    os_override.set_override(c, "box1", "192.0.2.1", "A-1", "VMware", None, None, "b")
    assert [r["os_type"] for r in os_override.list_active(c)] == ["VMware"]
    assert _os_of(c, "192.0.2.1")[0]["os_type"] == "VMware"


def test_只能選正式類別(tmp_path):
    c = _conn(tmp_path)
    for bad in ("推測 Linux 類", "未填", "隨便寫"):
        with pytest.raises(ValueError):
            os_override.set_override(c, "box1", "192.0.2.1", "A-1", bad, None, None, "t")
    assert "網路設備" in os_override.choices() and "未填" not in os_override.choices()
