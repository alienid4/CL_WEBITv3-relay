"""v1.473.2：2-1 開始收集的幾個使用者回報（2026-10-20）。

1. 「哪 77 台？能取消嗎？」：納管後收集排隊列得出逐台、可取消（排隊的移出、正在收的這一樣收完就停），
   每台都留「已取消」紀錄；服務重啟清掉的隊伍啟動時記一筆、可重新排入
2. 「寫收集完了，我也不知道有沒有成功還是失敗」：這一輪自己的結果分五堆，加總＝位址數
3. 「應該顯示『收集中：幾台』」：逐台更新做完幾台
4. 「我明明就一個 142/32，下面怎麼會寫 431 個網段？」：涵蓋率標明不是這次的範圍、另顯示這次範圍
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402
import post_onboard  # noqa: E402

ADOPT = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "adopt.vue").read_text(encoding="utf-8")


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    old_db = post_onboard._q_db[0]
    post_onboard._q_db[0] = p
    try:
        yield c
    finally:
        post_onboard._q_db[0] = old_db
        with post_onboard._q_lock:
            post_onboard._queue.clear()
            post_onboard._queued.clear()
            post_onboard._queued_at.clear()
            post_onboard._q_state.update(running=False, total=0, done=0, ok=0, failed=0, deferred=0,
                                         current=None)
        post_onboard._cancel_running.clear()
        c.close()


def _logs(conn, kind):
    return [dict(r) for r in conn.execute(
        "SELECT target, ok, message FROM collect_attempt_log WHERE kind=? ORDER BY id", (kind,))]


def _fill_queue(serials, current=None):
    with post_onboard._q_lock:
        for s in serials:
            post_onboard._queue.append((s, "tester", "onboard"))
            post_onboard._queued.add(s)
            post_onboard._queued_at[s] = "2026-10-20 18:00:00"
        post_onboard._q_state.update(running=True, total=len(serials) + (1 if current else 0),
                                     current=current)


def test_排隊逐台列得出來(conn):
    _fill_queue(["B", "C"], current="A")
    st = post_onboard.queue_status()
    assert [(i["asset_serial"], i["state"]) for i in st["items"]] == [
        ("A", "running"), ("B", "queued"), ("C", "queued")]


def test_取消勾選的_排隊的移出_每台留已取消紀錄(conn):
    _fill_queue(["B", "C", "D"], current="A")
    out = post_onboard.cancel(conn, ["C"], by="admin")
    assert out["removed"] == ["C"] and out["running_cut"] is None
    assert [x[0] for x in post_onboard._queue] == ["B", "D"]
    logs = _logs(conn, "post_onboard_queue")
    assert [l["target"] for l in logs] == ["C"] and "已取消" in logs[0]["message"]


def test_全部取消_正在收的那台目前這一樣收完就停(conn):
    _fill_queue(["B"], current="A")
    out = post_onboard.cancel(conn, None, by="admin")
    assert out["removed"] == ["B"] and out["running_cut"] == "A"
    assert "A" in post_onboard._cancel_running
    assert {l["target"] for l in _logs(conn, "post_onboard_queue")} == {"A", "B"}


def test_正在收的被取消_剩下幾樣記成已取消_不靜默(conn, monkeypatch):
    calls = []

    def fake_one(c, job, serial):
        calls.append(job)
        if len(calls) == 2:
            post_onboard._cancel_running.add(serial)   # 收第二樣的當下按取消
        return True, "ok"

    monkeypatch.setattr(post_onboard, "_collect_one", fake_one)
    res = post_onboard.run_now(conn, "A", by="admin", source="manual")
    assert calls == list(post_onboard.JOBS[:2]), "目前這一樣收完就停"
    assert [r["message"] for r in res[2:]] == ["已取消"] * (len(post_onboard.JOBS) - 2)
    assert "A" not in post_onboard._cancel_running
    msg = _logs(conn, "post_onboard")[-1]["message"]
    assert "中途取消" in msg


def test_服務重啟清掉的隊伍_啟動時記一筆_可重新排入(conn):
    _fill_queue(["B", "C"], current="A")
    post_onboard._persist(conn)
    lost = post_onboard.recover_after_restart(conn)
    assert lost == ["A", "B", "C"]
    logs = _logs(conn, "post_onboard_queue")
    assert logs and "服務重啟：3 台排隊被清掉" in logs[-1]["message"]
    assert post_onboard.lost_serials(conn) == ["A", "B", "C"]
    assert post_onboard.recover_after_restart(conn) == [], "快照清空了，第二次啟動不重報"
    post_onboard.clear_lost(conn)
    assert post_onboard.lost_serials(conn) == []


def test_這一輪的結果卡_五堆加總等於位址數(conn, monkeypatch):
    monkeypatch.setattr(cd, "_sleep", lambda s: None)
    out = cd.run_dispatch(conn, "192.0.2.1-4", prober=lambda ip: None)
    sm = cd.run_result_summary(conn, out["run_id"])
    assert sm["total"] == 4 and sum(sm["counts"].values()) == 4
    assert sm["counts"]["no_response"] == 4
    assert [r["no"] for r in sm["rows"]] == [1, 2, 3, 4]
    with pytest.raises(LookupError):
        cd.run_result_summary(conn, 999999)


def test_逐台更新做完幾台(conn, monkeypatch):
    seen = []
    real = cd._do_import

    def imp(c, ip, asset):
        seen.append(c.execute("SELECT done_count FROM collect_dispatch_run ORDER BY id DESC LIMIT 1").fetchone()[0])
        return real(c, ip, asset)

    monkeypatch.setattr(cd, "_do_import", imp)
    cd.run_dispatch(conn, "192.0.2.1-3", prober=lambda ip: None)
    assert seen == [0, 1, 2], "每收完一台就更新，不是整批做完才更新"


def test_畫面():
    for k in ("全部已知網段的收集涵蓋率（不是這次的範圍）", "範圍：", "busyCore", "・${dElapsed.value} 秒",
              "/api/collect/dispatch/progress", "run-summary", "/summary", "pq-panel",
              "全部取消排隊", "重新排入", "post_onboard_queue"):
        assert k in ADOPT, k
    # 涵蓋率不再緊貼在開始收集按鈕底下（排在紀錄之後）
    assert ADOPT.index('data-test="coverage"') > ADOPT.index('<CollectLog ref="logRef"')
