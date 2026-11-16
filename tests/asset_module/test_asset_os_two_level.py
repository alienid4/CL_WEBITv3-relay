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


# ===== 單台查詢與人工搬移（2026-10-13）=====
# 使用者站在資產明細頁指著一台 EMC UnityXT480 NAS：「是 storage 你怎麼抓不出來」，
# 接著：「不能百分百分類，可設計手動搬移群組」。
#
# 那一頁以前根本沒顯示大類——看不到系統把它歸成什麼，當然也無從搬。

def test_單台分類跟全部分類不能給出不同答案(tmp_path):
    """classify_one 與 classify_all 共用 _classify_row。若各寫一套，
    「查一台」跟「查全部」會對同一台給出不同結論，而且沒人會發現。"""
    c = _conn(tmp_path)
    all_ = asset_classify.classify_all(c)
    for sn in all_:
        one = asset_classify.classify_one(c, sn)
        for k in ("platform", "os_type", "os_detail", "roles"):
            assert one[k] == all_[sn][k], (sn, k)


def test_查不到的序號回None(tmp_path):
    assert asset_classify.classify_one(_conn(tmp_path), "沒這台") is None


def test_人工搬移之後大類跟著變_並且說得出是人搬的(tmp_path):
    c = _conn(tmp_path)
    before = asset_classify.classify_one(c, "A-6")      # (VM)、os 是 N/A
    assert before["os_type_override"] is None, "還沒搬就不該有人工指定"
    auto = before["os_type_auto"]
    assert before["os_type"] == auto

    os_override.set_override(c, "airwave", "192.0.2.6", "A-6",
                             os_type="Storage/SAN", auto_os_type=auto,
                             reason="實際是 NAS", by="tester")

    after = asset_classify.classify_one(c, "A-6")
    assert after["os_type"] == "Storage/SAN"
    # 機器原本判什麼要留著：不留的話三個月後沒人知道這台為什麼是這一類
    assert after["os_type_auto"] == auto
    ov = after["os_type_override"]
    assert ov and ov["by"] == "tester" and ov["reason"] == "實際是 NAS"
    assert ov["auto_at_time"] == auto


def test_還原之後回到機器判定_紀錄不消失(tmp_path):
    c = _conn(tmp_path)
    auto = asset_classify.classify_one(c, "A-6")["os_type_auto"]
    os_override.set_override(c, "airwave", "192.0.2.6", "A-6", "Storage/SAN", auto, None, "tester")
    os_override.clear(c, "airwave", "192.0.2.6", "A-6", "tester")

    back = asset_classify.classify_one(c, "A-6")
    assert back["os_type"] == auto and back["os_type_override"] is None
    # 軟刪除：有效清單空了，但資料列還在，稽核查得到誰搬過
    assert os_override.list_active(c) == []
    assert c.execute("SELECT COUNT(*) FROM os_type_override").fetchone()[0] == 1
