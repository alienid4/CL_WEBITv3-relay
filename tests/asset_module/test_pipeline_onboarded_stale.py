"""納管過的機器，清空／重匯之後不可以退回「去納管」。

2026-09-09 使用者清空重匯後回報：「我記得 10.9x.x.11 和 .13 這幾台都有納管過，
現在卻變成沒有納管的狀態。這就是我早上一開始擔心的。」

根因不是納管紀錄被刪（v1.69.0 已經保住它），而是**判定根本沒看那張表**：
`manage_state.classify` 看的是「現在收不收得到」，收集資料被清掉就變成
「還沒試過」→ 一律歸未納管，下一步還寫「對這台執行納管」。

那個指示是錯的，而且錯得有代價：那些機器上的收集帳號與金鑰還在，
重納管是白跑一趟，而且是**對正式機器動手**。

這裡守三件事：
1. 有納管成功紀錄的，不可以歸到「已登記，進不去」
2. **撤銷過的不算**——取消納管之後又掛著「納管過」是假狀態
3. 不可以直接顯示成「已納管」：我們只知道建過帳號，不知道現在還通不通
   （可能被清掉、可能重灌）——那是未驗證，不是完成
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import pipeline  # noqa: E402

IP_ONBOARDED = "10.99.9.11"
IP_PLAIN = "10.99.9.12"
IP_REVOKED = "10.99.9.13"
SCAN_AT = "2026-09-09 10:00:00"


def _setup(tmp):
    path = Path(tmp) / "t.db"
    db.init_db(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    for i, ip in enumerate((IP_ONBOARDED, IP_PLAIN, IP_REVOKED)):
        db.insert_hardware(conn, asset_serial=f"HW-{i}", ip=ip, hostname=f"H{i}",
                           environment="測試", os="Red Hat Enterprise Linux 8 (64-bit)")
        # 掃得到（不然會被判成失聯，走另一條路）
        conn.execute("INSERT INTO scan_history (scan_time, ip, scan_ok, open_ports) "
                     "VALUES (?,?,1,'22')", (SCAN_AT, ip))
    conn.commit()
    return conn


def _audit(conn, ip, trigger, ok=1):
    conn.execute("INSERT INTO onboard_audit (target_ip, trigger, ok) VALUES (?,?,?)",
                 (ip, trigger, ok))
    conn.commit()


def _stage_of(conn, ip):
    out = pipeline.summarize(conn)
    for it in out["items"]:
        if it["ip"] == ip:
            return it["stage"]
    raise AssertionError(f"{ip} 不在漏斗結果裡")


def test_納管過的不會被叫去再納管一次():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _setup(tmp)
        try:
            _audit(conn, IP_ONBOARDED, "manual")
            assert _stage_of(conn, IP_ONBOARDED) == "onboarded_stale"
            # 沒納管過的維持原樣
            assert _stage_of(conn, IP_PLAIN) == "not_onboarded"
        finally:
            conn.close()


def test_取消納管過的不算納管過():
    """先納管成功、後撤銷成功 → 回到「沒納管過」。

    只看「有沒有成功納管紀錄」的話，取消納管的機器會永遠掛著假狀態，
    而使用者當初要「取消納管」就是為了讓它真的變回未納管。
    """
    with tempfile.TemporaryDirectory() as tmp:
        conn = _setup(tmp)
        try:
            _audit(conn, IP_REVOKED, "manual")
            _audit(conn, IP_REVOKED, "revoke")
            assert _stage_of(conn, IP_REVOKED) == "not_onboarded"
        finally:
            conn.close()


def test_撤銷之後又重新納管_要算納管過():
    """順序才是重點，不是「有沒有出現過 revoke」。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _setup(tmp)
        try:
            _audit(conn, IP_REVOKED, "manual")
            _audit(conn, IP_REVOKED, "revoke")
            _audit(conn, IP_REVOKED, "manual")
            assert _stage_of(conn, IP_REVOKED) == "onboarded_stale"
        finally:
            conn.close()


def test_納管失敗的不算納管過():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _setup(tmp)
        try:
            _audit(conn, IP_PLAIN, "manual", ok=0)
            assert _stage_of(conn, IP_PLAIN) == "not_onboarded"
        finally:
            conn.close()


def test_這一關不可以叫人去納管():
    """下一步寫錯比沒寫更糟——會讓人對正式機器多做一次不必要的動作。"""
    stage = next(s for s in pipeline.STAGES if s["key"] == "onboarded_stale")
    assert stage["action"] == "collect", "動作應該是收集，不是納管"
    assert "不要重納管" in stage["next"]


def test_不可以直接顯示成已納管():
    """我們只知道建過帳號，不知道現在還通不通——那是未驗證，不是完成。"""
    stage = next(s for s in pipeline.STAGES if s["key"] == "onboarded_stale")
    assert stage["label"] != "已納管"
    assert stage["tone"] != "ok", "顯示成綠燈會讓人以為一切正常"
