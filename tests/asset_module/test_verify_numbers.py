"""數字核對 harness 的守門＋去重一致性回歸（2026-09-17）。

背景：verify_numbers.py 在 221 抓到「classify_assets 台數 4366」跟「分佈統計 total_assets 4367」差 1，
根因是兩把去重 key 對 0.0.0.0 佔位 IP 判斷不同（system_report._dup_key 沒防呆、system_stats.machine_key 有）。
這支測試釘住：兩把 key 對同一批資料的分組數必須一致，且 0.0.0.0 佔位不併台。
"""
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import system_report as sr  # noqa: E402
import system_stats as ss  # noqa: E402


def _asset(sn, host, ip):
    return {"asset_serial": sn, "hostname": host, "ip": ip, "os": None,
            "device_model": None, "retired": False, "platform": "linux"}


def test_placeholder_ip_not_merged():
    """兩筆同主機名、IP 都是 0.0.0.0 佔位＝兩台不同機器，不可併成一台。"""
    a = [_asset("S1", "sw", "0.0.0.0"), _asset("S2", "sw", "0.0.0.0")]
    assert len(sr._collapse_duplicates([dict(x) for x in a])) == 2


def test_real_ip_same_host_merged():
    """同主機名＋同一個真實 IP＝同一台，要併成一台。"""
    b = [_asset("S3", "h1", "10.99.1.1"), _asset("S4", "h1", "10.99.1.1")]
    assert len(sr._collapse_duplicates([dict(x) for x in b])) == 1


def test_two_dedup_keys_agree():
    """system_report._dup_key 與 system_stats.machine_key 對同一批的分組數必須一致——
    不一致就是頁A/B/月報 跟 分佈統計/漏斗 的台數會對不上（實際踩過差 1 台）。"""
    rows = [
        _asset("S1", "sw", "0.0.0.0"), _asset("S2", "sw", "0.0.0.0"),
        _asset("S3", "h1", "10.99.1.1"), _asset("S4", "h1", "10.99.1.1"),
        _asset("S5", "h2", ""), _asset("S6", "", "10.99.1.9"),
    ]
    k1 = {sr._dup_key(r) or ("sn", r["asset_serial"]) for r in rows}
    k2 = {ss.machine_key(r["hostname"], r["ip"], r["asset_serial"]) for r in rows}
    assert len(k1) == len(k2), f"_dup_key 分 {len(k1)} 組、machine_key 分 {len(k2)} 組，不一致"


def test_monthly_total_is_hosts_not_rows(tmp_path):
    """月報「總台數」必須是去重後的台，不是 COUNT(*) 筆——2026-09-17 verify_numbers 抓到
    原本掛筆數（含重複登記）當台數餵進 AI 月報，主管報告會多算。同主機名+IP 灌兩筆，
    總台數要=1、登記筆數要=2。"""
    dbp = tmp_path / "m.db"
    db.init_db(dbp)
    conn = db.get_connection(dbp)
    db.insert_hardware(conn, asset_serial="M1", hostname="srv", ip="10.99.1.10",
                       os="Linux", environment="正式")
    db.insert_hardware(conn, asset_serial="M2", hostname="srv", ip="10.99.1.10",
                       os="Linux", environment="正式")  # 同主機重複登記
    conn.commit()

    import monthly_report
    secs = monthly_report.collect(conn)["sections"]
    sec = next(s["data"] for s in secs if s.get("name") == "資產總覽")
    assert sec["總台數"] == 1, "重複登記的兩筆同一台，總台數要去重成 1"
    assert sec["登記筆數"] == 2, "登記筆數是原始 COUNT(*)，要保留 2"
    assert sec["總台數"] == len(sr.classify_assets(conn)), "總台數要跟頁A/B同一份去重"
    assert sum(sec["依環境"].values()) == sec["總台數"]
    assert sum(sec["虛實"].values()) == sec["總台數"]


def test_harness_runs_clean_on_empty_db(tmp_path):
    """空庫上跑整支 harness 應全過（exit 0）——確保 harness 自己能跑、import 正確。"""
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    import os
    os.environ["ASSET_DB_PATH"] = str(dbp)
    import importlib
    vn = importlib.import_module("verify_numbers")
    importlib.reload(vn)
    assert vn.main() == 0
