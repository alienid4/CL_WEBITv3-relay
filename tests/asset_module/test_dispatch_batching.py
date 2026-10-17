"""開始收集自動分批（2026-09-16 使用者：「你應該幫我自動分」「你有分批嗎?」）。

使用者貼了兩個 /24（展開 508 台）、又試過 1270 台，看到的是
「收集失敗：一次最多 1024 台，這次展開成 1270 台——請分批」。
叫人自己把 1270 台切成兩段再貼兩次，是把程式該做的事丟給人。

要守的：

1. 超過一批自動切，**不再拒絕**；但總量仍有硬上限（不能把 /8 貼進來跑到天荒地老）
2. 切批保留輸入順序（使用者貼的順序通常有意義：同機房排在一起）
3. 批與批之間停一下，**秒數可設定**（使用者：「預設每個網段間距2分鐘」「可以設定改幾分鐘」）
4. 第一批不等——按下去先發呆兩分鐘會讓人以為當掉
5. 每批做完就存檔並更新進度；跑到第 N 批壞掉，前面幾批的結果不可以跟著不見
6. 大批次走背景執行（同步會被 HTTP 逾時切斷，就是使用者看到的「跑了 0 秒」）
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        yield c
    finally:
        c.close()


def test_切批保留順序():
    ips = [f"192.0.2.{i}" for i in range(1, 11)]
    batches = cd.plan_batches(ips, size=4)
    assert [len(b) for b in batches] == [4, 4, 2]
    assert [ip for b in batches for ip in b] == ips, "順序不可以被打亂"


def test_超過一批不再被拒絕():
    # /23 ＝ 510 台；用 size=100 切成 6 批
    ips = cd.parse_targets("192.0.2.0/23")
    assert len(ips) == 510
    assert len(cd.plan_batches(ips, size=100)) == 6


def test_總量仍有硬上限():
    with pytest.raises(ValueError) as e:
        cd.parse_targets("10.0.0.0/8")
    assert str(cd.HARD_MAX_TARGETS) in str(e.value)
    assert "掃描排程" in str(e.value), "要告訴他該用哪個功能，不是只說不行"


def test_間隔秒數可設定(conn):
    assert cd.batch_gap_seconds(conn) == cd.DEFAULT_BATCH_GAP_SECONDS == 120
    from db import set_setting

    set_setting(conn, "dispatch_batch_gap_seconds", "300")
    assert cd.batch_gap_seconds(conn) == 300
    set_setting(conn, "dispatch_batch_gap_seconds", "亂寫")
    assert cd.batch_gap_seconds(conn) == cd.DEFAULT_BATCH_GAP_SECONDS, "壞值要回到預設，不可以爆掉"


def _prober(ip):
    return None          # 一律沒回應：測分批行為，不測收集本身


def test_分批執行_第一批不等_其餘有間隔(conn, monkeypatch):
    slept = []
    monkeypatch.setattr(cd, "_sleep", lambda s: slept.append(s))
    out = cd.run_dispatch(conn, "192.0.2.0/24", prober=_prober, batch_size=100, gap_seconds=120)
    assert out["total"] == 254
    assert slept == [120, 120], "3 批 → 只等 2 次；第一批不等"


def test_進度有寫進去_可以看得出跑到第幾批(conn, monkeypatch):
    monkeypatch.setattr(cd, "_sleep", lambda s: None)
    cd.run_dispatch(conn, "192.0.2.0/24", prober=_prober, batch_size=100, gap_seconds=0)
    run = conn.execute("SELECT * FROM collect_dispatch_run ORDER BY id DESC LIMIT 1").fetchone()
    assert run["batch_total"] == 3 and run["batch_done"] == 3
    assert run["done_count"] == run["target_count"] == 254
    assert run["status"] == "ok"


def test_中途壞掉_前面幾批的結果要留著(conn, monkeypatch):
    monkeypatch.setattr(cd, "_sleep", lambda s: None)
    calls = {"n": 0}

    def flaky(ips, prober, workers=64):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("第三批壞掉")
        return {ip: None for ip in ips}

    monkeypatch.setattr(cd, "_probe_all", flaky)
    with pytest.raises(OSError):
        cd.run_dispatch(conn, "192.0.2.0/24", prober=_prober, batch_size=100, gap_seconds=0)
    run = conn.execute("SELECT * FROM collect_dispatch_run ORDER BY id DESC LIMIT 1").fetchone()
    assert run["status"] == "failed" and "第三批壞掉" in (run["error"] or "")
    assert run["batch_done"] == 2, "前兩批做完的進度要留著"
    kept = conn.execute("SELECT COUNT(*) FROM collect_dispatch_result WHERE run_id=?",
                        (run["id"],)).fetchone()[0]
    assert kept == 200, "前兩批 200 台的結果不可以跟著不見"


def test_一批以內不切批也不等(conn, monkeypatch):
    slept = []
    monkeypatch.setattr(cd, "_sleep", lambda s: slept.append(s))
    cd.run_dispatch(conn, "192.0.2.0/25", prober=_prober)     # 126 台，遠小於一批
    run = conn.execute("SELECT * FROM collect_dispatch_run ORDER BY id DESC LIMIT 1").fetchone()
    assert run["batch_total"] == 1 and slept == []


# ===== 空位址不算待辦（2026-09-16 使用者：「為什麼是 508，還要人工處理?」）=====
#
# 他貼了兩個 /24（254×2＝508 個**位址**，不是 508 台機器），全部沒回應，
# 畫面卻同時寫「要人工處理 508」與「沒有回應（只能匯入）508」——
# 同一批東西講成兩個數字，還把 508 個空位址說成待辦。掃網段本來就大部分是空的。


def _mk(ip, status, registered):
    return {"ip": ip, "status": status, "route": "import", "registered": registered}


def test_沒回應又沒登記的算空位址_不算待辦():
    s = cd.summarize([
        _mk("192.0.2.1", cd.STATUS_IMPORT_ONLY, 0),
        _mk("192.0.2.2", cd.STATUS_IMPORT_ONLY, 0),
    ])
    assert s["total"] == 2
    assert s["empty_addresses"] == 2
    assert s["needs_action"] == 0, "空位址上根本沒有機器，沒有事情可以做"


def test_已登記卻沒回應_仍然要處理():
    s = cd.summarize([_mk("192.0.2.3", cd.STATUS_IMPORT_ONLY, 1)])
    assert s["empty_addresses"] == 0
    assert s["needs_action"] == 1, "機器在案卻叫不動，那是真的待辦，不可以被一起掃掉"


def test_不納管的照舊不算待辦():
    s = cd.summarize([
        _mk("192.0.2.4", cd.STATUS_NOT_ONBOARDABLE, 1),
        _mk("192.0.2.5", cd.STATUS_COLLECTED, 1),
        _mk("192.0.2.6", cd.STATUS_NEEDS_CREDENTIAL, 1),
    ])
    assert s["collected"] == 1 and s["not_onboardable"] == 1
    assert s["needs_action"] == 1, "只有「待佈身分」那台要處理"
