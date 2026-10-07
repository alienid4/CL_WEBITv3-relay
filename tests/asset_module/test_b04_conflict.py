"""[B-04] 合併後不可藏住待辦：同一台有退役也有使用中 → 登記矛盾（2026-09-18）。

221 查證：以前漏斗合併時「關卡編號小的勝出」，已退役排在失聯前面，31 台的待辦被藏成「已退役」；
首頁四態則取最小序號的狀態——兩頁差 11 台、三頁的「已退役」各是 140／129／109。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state as ms  # noqa: E402
import pipeline  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    # 同一台（主機名＋IP 相同）：一筆報廢、一筆使用中
    db.insert_hardware(c, asset_serial="A-1", hostname="m1", ip="192.0.2.1", asset_status="報廢")
    db.insert_hardware(c, asset_serial="A-2", hostname="m1", ip="192.0.2.1", asset_status="使用中")
    # 單純退役的一台
    db.insert_hardware(c, asset_serial="B-1", hostname="m2", ip="192.0.2.2", asset_status="報廢")
    c.commit()
    return c


def test_漏斗_登記矛盾算待辦_不替人挑邊(tmp_path):
    c = _conn(tmp_path)
    out = pipeline.summarize(c)
    it = {i["ip"]: i for i in out["items"]}
    assert it["192.0.2.1"]["stage"] == "conflict"
    assert "conflict" in out["todo_stages"]
    assert {m["asset_serial"] for m in it["192.0.2.1"]["conflict_detail"]} == {"A-1", "A-2"}, "要附各筆讓人判斷"
    assert it["192.0.2.2"]["stage"] == "retired", "單純退役的不受影響"
    assert out["reconcile"]["ok"]


def test_首頁四態跟漏斗同一條規則(tmp_path):
    c = _conn(tmp_path)
    m = ms.summarize(c)
    p = pipeline.summarize(c)
    assert m["counts"][ms.CONFLICT] == p["counts"]["conflict"] == 1
    assert m["counts"][ms.RETIRED] == p["counts"]["retired"] == 1
    assert m["needs_action_total"] == p["todo"], "兩頁的「還需要處理」要一樣"


def test_B07_退役但仍在線要列待辦():
    """[B-07] classify(已登記, 掃到, 收得到, 已退役) 不可以是「已退役」結案。"""
    assert ms.classify(True, seen_in_scan=True, collect_ok=None, retired=True) == ms.RETIRED_ALIVE
    assert ms.classify(True, seen_in_scan=False, collect_ok=1, retired=True) == ms.RETIRED_ALIVE
    assert ms.RETIRED_ALIVE in ms.NEEDS_ACTION_STATES
    assert ms.classify(True, seen_in_scan=False, collect_ok=0, retired=True) == ms.RETIRED, "掃不到收不到＝正常退役"
    assert "retired_alive" in pipeline.TODO_STAGES
