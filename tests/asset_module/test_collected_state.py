"""第五態「已收集（設備）」（2026-09-15 使用者定）。

使用者：「收集跟納管不一樣：納管是我能控制它，收集則是純收集。」
「假設我有一百台，二十台已納管，十台已收集。那有問題的，應該是七十台還是八十台？」→ 70。
「如果已收集過就是加 30 分，跟已納管的分數相同。」

要守的：
1. 已收集獨立一態，而且優先於失聯（OOB 設備掃描看不到，但收到資料就證明它在）
2. 統計「有問題」時已收集跟已納管一樣**不算**——漏斗、健康度、首頁都一樣
3. 可信度：已收集 +30，跟已納管同分
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state as ms  # noqa: E402
import pipeline  # noqa: E402
import trust_score as ts  # noqa: E402


def test_已收集獨立一態_優先於失聯():
    assert ms.classify(True, seen_in_scan=False, collect_ok=None, collected=True) == ms.COLLECTED
    assert ms.classify(True, seen_in_scan=True, collect_ok=0, collected=True) == ms.COLLECTED
    # 已納管（收得到）照舊是已納管
    assert ms.classify(True, seen_in_scan=True, collect_ok=1, collected=True) == ms.ONBOARDED
    # 沒登記就是沒登記
    assert ms.classify(False, seen_in_scan=True, collect_ok=None, collected=True) == ms.UNREGISTERED
    assert ms.COLLECTED in ms.ALL_STATES and ms.NEXT_ACTION[ms.COLLECTED]


def test_十台裡兩台已納管一台已收集_有問題的是七台():
    """使用者的例子縮小版：100／20／10 → 70。這裡 10／2／1 → 7。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            for i in range(10):
                db.insert_hardware(conn, asset_serial=f"A-{i}", hostname=f"h{i}", ip=f"192.0.2.{i + 1}")
            # 兩台已納管：收得到＋掃得到
            conn.execute("UPDATE hardware SET collect_ok=1 WHERE asset_serial IN ('A-0','A-1')")
            for ip, hn in (("192.0.2.1", "h0"), ("192.0.2.2", "h1")):
                conn.execute("INSERT INTO scan_history (scan_time, ip, hostname, scan_ok) "
                             "VALUES ('2026-09-15 10:00:00', ?, ?, 1)", (ip, hn))
            # 一台已收集：SAN 收集過（掃描看不到也一樣）
            conn.execute("INSERT INTO san_switch (ip, switch_name) VALUES ('192.0.2.3', 'sw1')")
            conn.commit()
            s = ms.summarize(conn)
            health = ms._STATE_TO_HEALTH
        finally:
            conn.close()
    c = s["counts"]
    assert c[ms.ONBOARDED] == 2 and c[ms.COLLECTED] == 1
    assert s["needs_action_total"] == 7, "已收集不能被算進有問題"
    assert s["ok_total"] == 3
    assert s["total_known"] == 10
    assert health[ms.COLLECTED] == "ok"


def test_漏斗的已收集一關不算還要處理():
    assert "collected" in pipeline.STAGE_INDEX
    assert "collected" not in pipeline.TODO_STAGES
    assert "complete" not in pipeline.TODO_STAGES


def test_可信度_已收集加30跟已納管同分():
    all3 = {"dynassets": True, "rvtools": True, "cia": True}
    managed = ts.score_one("physical", all3, alive=False, managed=True)
    collected = ts.score_one("physical", all3, alive=False, managed=False, collected=True)
    assert collected["score"] == managed["score"]
    assert collected["evidence"] == "collected"
    assert ts.BONUS_COLLECTED == ts.BONUS_MANAGED == 30
    assert "已收集" in ts.explain({**collected, "alive_basis": None})
