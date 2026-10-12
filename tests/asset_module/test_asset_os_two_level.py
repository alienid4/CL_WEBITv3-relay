"""資產查詢的 OS 篩選改成兩層，大類跟漏斗同一套（2026-09-18）。

使用者：「我們平台不是還有，這部分是要對應嗎？」——資產查詢的「平台」跟漏斗的「OS 類型」
原本是兩套規則（同叫網路設備，一邊 666 筆、一邊 581 台，還各有對方沒有的類）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import asset_classify  # noqa: E402
import db  # noqa: E402
import os_override  # noqa: E402
import pipeline  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    rows = [("A-1", "h1", "192.0.2.1", "Red Hat Enterprise Linux 8.10", None),
            ("A-2", "h2", "192.0.2.2", "CentOS 7.9", None),
            ("A-3", "h3", "192.0.2.3", "17.03.04b", "Cisco C9200L-48P-4X"),
            ("A-4", "h4", "192.0.2.4", "儲存設備", "EMC Storage"),
            ("A-5", "h5", "192.0.2.5", "Microsoft Windows Server 2019", None),
            ("A-6", "airwave", "192.0.2.6", "N/A", "(VM)")]
    for sn, h, ip, os_, model in rows:
        db.insert_hardware(c, asset_serial=sn, hostname=h, ip=ip, os=os_, device_model=model,
                           asset_status="使用中")
    c.commit()
    return c


def test_大類跟漏斗同一套_Linux才有細分(tmp_path):
    c = _conn(tmp_path)
    cls = asset_classify.classify_all(c)
    for sn in cls:
        r = c.execute("SELECT os, device_model, hostname, asset_name FROM hardware WHERE asset_serial=?", (sn,)).fetchone()
        assert cls[sn]["os_type"] == pipeline._os_type(r["os"], None, r["device_model"], (r["hostname"], r["asset_name"]))
    assert cls["A-1"]["os_type"] == "Linux" and cls["A-1"]["os_detail"] == "RHEL"
    assert cls["A-2"]["os_detail"] == "CentOS"
    assert cls["A-3"]["os_type"] == "網路設備" and cls["A-3"]["os_detail"] is None
    assert cls["A-4"]["os_type"] == "Storage/SAN"
    assert cls["A-5"]["os_type"] == "Windows" and cls["A-5"]["os_detail"] is None


def test_人工指定在資產查詢也生效(tmp_path):
    c = _conn(tmp_path)
    os_override.set_override(c, "airwave", "192.0.2.6", "A-6", "網路設備", "未填", None, "t")
    assert asset_classify.classify_all(c)["A-6"]["os_type"] == "網路設備"


def test_Linux細分加總等於Linux大類(tmp_path):
    c = _conn(tmp_path)
    cls = asset_classify.classify_all(c).values()
    linux = sum(1 for x in cls if x["os_type"] == "Linux")
    detail = sum(1 for x in cls if x["os_detail"])
    assert linux == detail == 2
