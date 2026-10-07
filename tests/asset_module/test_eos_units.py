"""EOS 頁的口徑與分類（2026-09-20 公司 198.14 驗收）。

抓到三件事：
1. 頁面上面寫「盤點到的主機 0 台／沒有任何主機查得到 EOS 日期」，下面卻列著 221 台——
   `inventory_hosts.eos_hosts` 讀錯欄位（"eos" vs 實際的 "eos_date"），所以永遠 0 台。
2. 四個磚塊寫「種類」，其實是台數（而且逐筆算）。
3. 作業系統分頁第二層是 EOS 自己那套（AIX/Unix），使用者拍板改用全站正典 OS 類型。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import inventory_hosts  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    # 同一台兩筆登記（同主機名＋IP），OS 是查得到官方 EOS 日期的版本
    db.insert_hardware(c, asset_serial="A-1", hostname="w1", ip="192.0.2.1", os="Windows Server 2012")
    db.insert_hardware(c, asset_serial="A-2", hostname="w1", ip="192.0.2.1", os="Windows Server 2012")
    # 另一台，已退役（不該算）
    db.insert_hardware(c, asset_serial="B-1", hostname="w2", ip="192.0.2.2", os="Windows Server 2012",
                       asset_status="報廢")
    c.commit()
    return c


def test_盤點到的主機不可以永遠是0台(tmp_path):
    hosts = inventory_hosts.eos_hosts(_conn(tmp_path))
    assert len(hosts) == 2, "兩筆登記都查得到日期（這裡刻意逐筆列，讓人點得到每一筆）"
    assert all(h["os_eos"] for h in hosts), "要帶出官方日期，不能是 None"
    assert all(h["os_status"] for h in hosts)


def test_磚塊是台數且逐台_同一台多筆只算一次(tmp_path):
    s = api.eos_summary(session=None, conn=_conn(tmp_path))
    total = sum(s[b]["by_status"].get("expired", 0)
                for b in ("host_os", "firmware", "software", "insufficient", "other", "hardware"))
    assert total == 1, "同一台兩筆登記只能算一台；退役那台不算"
    item = s["host_os"]["items"][0]
    assert item["count"] == 1, "每一列的台數也要逐台"


def test_作業系統第二層用正典OS類型(tmp_path):
    import pipeline

    s = api.eos_summary(session=None, conn=_conn(tmp_path))
    assert s["os_order"] == list(pipeline.OS_ORDER), "第二層順序＝全站正典"
    fams = {it.get("family") for it in s["host_os"]["items"]}
    assert fams <= set(pipeline.OS_ORDER), f"第二層只能用正典的字：{fams}"
    assert "Windows" in fams
