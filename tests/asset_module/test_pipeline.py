"""納管漏斗：每台機器落在哪一關。

使用者 2026-08-16：「300 台測試機要一台一台匯，我至少要知道哪些是我還需要處理的。」

這頁能不能信，全看兩件事：
1. **互斥窮盡**——每台剛好落一關，各關加總 = 母體。對不起來就是有 bug，
   而不是「大概差不多」。這是稽核看板的信任基礎。
2. **關卡判定跟四態一致**——四態沿用 manage_state，不另外算一套。
   同一件事兩處各算一次，遲早算出不同答案，然後兩個畫面互相打臉。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import pipeline  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _scan(conn, ip, when="2026-08-16 01:00:00"):
    conn.execute("INSERT INTO scan_history (scan_time, ip, scan_ok) VALUES (?,?,1)", (when, ip))
    conn.commit()


def _asset(conn, serial, ip, **kw):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, environment="正式",
                       asset_status="使用中", **kw)


def _service(conn, serial, ip):
    conn.execute("INSERT INTO host_service (asset_serial, ip, proto, port, source) "
                 "VALUES (?,?,'tcp',22,'test')", (serial, ip))
    conn.commit()


def _account(conn, serial, ip):
    conn.execute("INSERT INTO host_account (asset_serial, ip, username) VALUES (?,?,'root')",
                 (serial, ip))
    conn.commit()


def test_每台剛好落一關_加總等於母體():
    """互斥窮盡是這頁可信的前提。任何一台落在兩關、或哪一關都不落，
    畫面上的數字就再也對不起來。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        for i, ip in enumerate(["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"], 1):
            _scan(conn, ip)
            if i > 1:
                _asset(conn, f"A-{i}", ip, collect_ok=1 if i > 2 else 0)
        _asset(conn, "A-LOST", "10.0.0.9", collect_ok=1)      # 沒掃到 → 失聯

        out = pipeline.summarize(conn)
        assert out["reconcile"]["ok"] is True
        assert out["reconcile"]["sum_of_stages"] == out["total"]
        assert len(out["items"]) == out["total"]
        conn.close()


def test_關卡依序推進():
    """一台機器隨著資料越收越多，關卡要往後走，不能卡住也不能跳關。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _scan(conn, "10.0.0.5")

        def stage_of():
            return {i["ip"]: i["stage"] for i in pipeline.summarize(conn)["items"]}["10.0.0.5"]

        assert stage_of() == "unregistered"          # 掃到但沒登記

        _asset(conn, "A-5", "10.0.0.5", collect_ok=0)
        assert stage_of() == "not_onboarded"         # 登記了，進不去

        conn.execute("UPDATE hardware SET collect_ok=1 WHERE asset_serial='A-5'")
        conn.commit()
        assert stage_of() == "no_facts"              # 進得去，但事實還沒收到

        conn.execute("UPDATE hardware SET os='Rocky Linux 9' WHERE asset_serial='A-5'")
        conn.commit()
        assert stage_of() == "no_services"           # 有事實，沒服務

        _service(conn, "A-5", "10.0.0.5")
        assert stage_of() == "no_accounts"           # 有服務，沒帳號

        _account(conn, "A-5", "10.0.0.5")
        assert stage_of() == "complete"              # 齊全
        conn.close()


def test_失聯是終點不是中間關卡():
    """登記卻掃不到，問題是「這台還在不在」，不是「還沒收資料」——
    不該被歸到某個收集關卡然後叫人去收。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _scan(conn, "10.0.0.1")                       # 有掃描紀錄，但不含 A-X
        _asset(conn, "A-X", "10.0.0.99", collect_ok=1, os="Rocky Linux 9")
        out = pipeline.summarize(conn)
        by_ip = {i["ip"]: i for i in out["items"]}
        assert by_ip["10.0.0.99"]["stage"] == "lost"
        assert by_ip["10.0.0.99"]["tone"] == "bad"
        conn.close()


def test_序號機型收不到不算卡在事實那關():
    """序號/機型多半要 root 才讀得到，唯讀收集帳號常常拿不到（已知取捨）。
    要求全有的話幾乎所有機器都會永遠卡在同一關，那個數字就不再有意義。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _scan(conn, "10.0.0.6")
        _asset(conn, "A-6", "10.0.0.6", collect_ok=1, os="AIX 7.2")   # 只有 OS
        by_ip = {i["ip"]: i for i in pipeline.summarize(conn)["items"]}
        assert by_ip["10.0.0.6"]["stage"] == "no_services"
        conn.close()


def test_每一關都講得出下一步():
    """「還需要處理」的每一關都要有可執行的下一步，否則使用者知道卡住也不知道要幹嘛。"""
    for s in pipeline.STAGES:
        if s["key"] == "complete":
            continue
        assert s["next"] and len(s["next"]) > 5, f"{s['key']} 沒有下一步說明"


def test_待辦數等於母體減去齊全的():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        for ip in ("10.0.0.1", "10.0.0.2"):
            _scan(conn, ip)
        _asset(conn, "A-2", "10.0.0.2", collect_ok=1, os="Rocky Linux 9")
        _service(conn, "A-2", "10.0.0.2")
        _account(conn, "A-2", "10.0.0.2")
        out = pipeline.summarize(conn)
        assert out["complete"] == 1
        assert out["todo"] == out["total"] - out["complete"]
        conn.close()


def test_收集結果表不存在也不會炸():
    """舊 DB、或那個功能還沒開的環境，不該讓整頁 500。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        conn.execute("DROP TABLE IF EXISTS host_service")
        conn.commit()
        _scan(conn, "10.0.0.1")
        _asset(conn, "A-1", "10.0.0.1", collect_ok=1, os="Rocky Linux 9")
        out = pipeline.summarize(conn)
        assert out["reconcile"]["ok"] is True
        conn.close()


def test_空資料庫不會炸也不會亂報():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        out = pipeline.summarize(conn)
        assert out["total"] == 0 and out["todo"] == 0
        assert out["reconcile"]["ok"] is True
        conn.close()
