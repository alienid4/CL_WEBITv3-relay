"""規則式掃描範圍（2026-09-17 方案 A）的守門測試。

用程式證明（不是嘴上說）：
- 規則照「類別×環境×資安建議排除」正確判每段在/不在
- **重匯網段配置表後，新出現的伺服器段自動納入、不用重勾**——這條就是「不再假失聯」的鐵證
- force_in／force_out 個別覆寫規則，且以 CIDR 記，重匯不會掉
- 存規則會立刻同步成掃描來源（connections）
"""
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import scan_scope as ss  # noqa: E402


def _seg(conn, cidr, category, environment="正式", scan_excluded=0, location="機房A"):
    conn.execute(
        "INSERT INTO network_segment (cidr, raw_cidr, category, environment, "
        "scan_excluded, location) VALUES (?,?,?,?,?,?)",
        (cidr, cidr, category, environment, scan_excluded, location))
    conn.commit()


def _fresh(tmp_path) -> sqlite3.Connection:
    dbp = tmp_path / "s.db"
    db.init_db(dbp)
    return db.get_connection(dbp)


def _in(conn):
    return {r["cidr"] for r in ss.resolve_scope(conn) if r["in_scope"]}


def test_default_rule_picks_servers_drops_oa(tmp_path):
    """預設規則（SERVER+UAT-SERVER、正式+測試、尊重建議排除）：伺服器進、員工電腦出。"""
    conn = _fresh(tmp_path)
    _seg(conn, "10.99.1.0/24", "SERVER", "正式")
    _seg(conn, "10.99.2.0/24", "UAT-SERVER", "測試")
    _seg(conn, "10.99.3.0/24", "OA", "正式")            # 員工電腦 → 出
    _seg(conn, "10.99.4.0/24", "NETWORK", "正式")       # 網路設備 → 預設不收
    assert _in(conn) == {"10.99.1.0/24", "10.99.2.0/24"}


def test_respect_recommended_exclude(tmp_path):
    """respect_recommended_exclude 開關：開＝被資安標建議排除的不掃；關＝照收。
    2026-09-19 起預設是關（使用者「UAT 伺服器收」），所以這裡明確兩種都測。"""
    conn = _fresh(tmp_path)
    _seg(conn, "10.99.1.0/24", "SERVER", "正式", scan_excluded=1)
    # 預設（關）：建議排除的照樣收
    assert _in(conn) == {"10.99.1.0/24"}
    # 開起來：就不收
    ss.set_policy(conn, {"respect_recommended_exclude": True})
    assert _in(conn) == set()
    # 再關回來：又收
    ss.set_policy(conn, {"respect_recommended_exclude": False})
    assert _in(conn) == {"10.99.1.0/24"}


def test_預設收UAT伺服器_不尊重UAT環境的建議排除(tmp_path):
    """使用者 2026-09-19：UAT 伺服器要收。預設 respect=False，被標建議排除的 UAT-SERVER 照收。"""
    conn = _fresh(tmp_path)
    _seg(conn, "10.99.2.0/24", "UAT-SERVER", "測試", scan_excluded=1)  # 標「UAT環境建議排除」
    assert _in(conn) == {"10.99.2.0/24"}, "UAT 伺服器（被標建議排除）預設要收"


def test_new_server_segment_auto_in_scope_on_reimport(tmp_path):
    """重匯後多一個 SERVER 段：不動任何設定就自動納入——這是『不再假失聯』的鐵證。"""
    conn = _fresh(tmp_path)
    _seg(conn, "10.99.1.0/24", "SERVER", "正式")
    ss.apply_policy(conn)                     # 首次套用
    before = {r["target"] for r in conn.execute(
        "SELECT target FROM connections WHERE connection_type=? AND (enabled IS NULL OR enabled=1)",
        (ss.SCAN_TYPE,))}
    assert before == {"10.99.1.0/24"}
    # 模擬重匯：新增一個正式伺服器段，再跑一次自動同步（import_segments 會呼叫它）
    _seg(conn, "10.99.9.0/24", "SERVER", "正式")
    ss.apply_policy(conn)
    after = {r["target"] for r in conn.execute(
        "SELECT target FROM connections WHERE connection_type=? AND (enabled IS NULL OR enabled=1)",
        (ss.SCAN_TYPE,))}
    assert after == {"10.99.1.0/24", "10.99.9.0/24"}, "新伺服器段沒有自動納入掃描"


def test_force_out_beats_rule(tmp_path):
    """force_out 最高優先：規則本來會收的段，被強制排除就不掃。"""
    conn = _fresh(tmp_path)
    _seg(conn, "10.99.1.0/24", "SERVER", "正式")
    ss.set_policy(conn, {"force_out": ["10.99.1.0/24"]})
    assert _in(conn) == set()


def test_force_in_pulls_back_excluded(tmp_path):
    """force_in 把規則會排除的段拉回來（例如某個 OA 段其實有伺服器要收）。"""
    conn = _fresh(tmp_path)
    _seg(conn, "10.99.3.0/24", "OA", "正式")
    assert _in(conn) == set()
    ss.set_policy(conn, {"force_in": ["10.99.3.0/24"]})
    assert _in(conn) == {"10.99.3.0/24"}


def test_reason_is_explicit(tmp_path):
    """每段都要講得出為什麼在/不在（查不到≠沒有，畫面要看得出來）。"""
    conn = _fresh(tmp_path)
    _seg(conn, "10.99.3.0/24", "OA", "正式")
    r = {x["cidr"]: x["reason"] for x in ss.resolve_scope(conn)}
    assert "類別不收集" in r["10.99.3.0/24"]


def test_manual_mode_does_not_autosync(tmp_path):
    """manual 模式＝逃生口：apply_policy 不自動動 connections（交給逐段勾）。"""
    conn = _fresh(tmp_path)
    _seg(conn, "10.99.1.0/24", "SERVER", "正式")
    ss.set_policy(conn, {"mode": "manual"})
    out = ss.apply_policy(conn)
    assert out.get("mode") == "manual"
    assert not conn.execute(
        "SELECT COUNT(*) FROM connections WHERE connection_type=?", (ss.SCAN_TYPE,)).fetchone()[0]


def test_bad_policy_json_falls_back_to_default(tmp_path):
    """設定壞掉（不是資料）就回預設，不讓整頁掛掉。"""
    conn = _fresh(tmp_path)
    db.set_setting(conn, ss.POLICY_KEY, "{not json")
    p = ss.get_policy(conn)
    assert p["mode"] == "rule" and "SERVER" in p["categories"]
