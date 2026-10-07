"""口徑統一：畫面寫「台」就一定是逐台（2026-09-20 公司 198.14 驗收）。

抓到的實況：首頁「搜不到 3,417」＞「在管 3,374 台」——檯面寫台、程式算筆。
同一批還有：圓環「另有 4,155 台未登記」其實是帳外**筆數**、而且跟對帳明細的
「未登記 53」（這次掃到、清冊查無）撞名；資料品質「7,923 台」其實是不重複序號數。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import api  # noqa: E402
import db  # noqa: E402
import manage_state as ms  # noqa: E402
import trust_score  # noqa: E402

T = "2026-09-20 01:00:00"


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    # 同一台三筆 CIA 登記（多個服務），掃得到
    for sn in ("A-1", "A-2", "A-3"):
        db.insert_hardware(c, asset_serial=sn, hostname="web1", ip="192.0.2.1", asset_status="使用中")
    # 另一台兩筆，掃不到
    for sn in ("B-1", "B-2"):
        db.insert_hardware(c, asset_serial=sn, hostname="web2", ip="192.0.2.2", asset_status="使用中")
    # 帳外同一台兩筆
    db.insert_hardware(c, asset_serial="DYN-192.0.2.9", hostname="dy1", ip="192.0.2.9", asset_status="使用中")
    db.insert_hardware(c, asset_serial="VC-192.0.2.9", hostname="dy1", ip="192.0.2.9", asset_status="使用中")
    c.execute("INSERT INTO scan_history (scan_time, ip, scan_ok) VALUES (?, '192.0.2.1', 1)", (T,))
    c.commit()
    return c


def test_首頁對帳檯面逐台_一致加搜不到等於在管台數(tmp_path):
    c = _conn(tmp_path)
    s = api.dashboard_stats(session=None, conn=c)
    comp = ms.composition(c)
    assert s["total_ica_machines"] == 2, "web1、web2 各一台（帳外不算在管）"
    assert s["total_overlap_machines"] == 1, "web1 掃得到"
    assert s["total_ica_machines"] - s["total_overlap_machines"] == 1, "搜不到 1 台"
    assert s["total_ica_machines"] == comp["registered_hosts"], "跟頭條『在管 N 台』同一把尺"
    assert s["total_ica_count"] == 5, "筆數仍照給（副標用）"
    assert s["total_overlap_machines"] <= s["total_ica_machines"], "子集不可能比總數大"


def test_帳外要有台數_不可以只給筆數(tmp_path):
    comp = ms.composition(_conn(tmp_path))
    assert comp["off_book_total"] == 2, "帳外兩筆"
    assert comp["off_book_machines"] == 1, "但只有一台"


def test_資料品質分數是逐筆_另外附台數(tmp_path):
    d = trust_score.distribution(_conn(tmp_path))
    assert d["total"] == 7, "分數母體是登記筆數（非退役，含帳外）"
    assert d["machines"] == 3, "去重後三台：web1、web2、dy1"
    assert sum(x["count"] for x in d["by_score"]) == d["total"], "分數分布加總＝筆數"
