"""2.007.13：使用者 10-27 一輪回饋——5.2 依機房分開算、6.1 存活欄＋說明收合、7.1 機櫃點進去看設備、7.3 軟體搜尋／收集時間／收集涵蓋、
10.1 主機組月報（NMON）。全部假資料（IP 一律 10.99.x）。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import host_monthly  # noqa: E402
import inventory_flow as inv  # noqa: E402
import v2_onboard  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _src(rel):
    return (FE / rel).read_text(encoding="utf-8")


# ===== 5.2 =====

def test_儲存容量_不顯示全部機房加總_選機房才看那一間():
    s = _src("pages/v2/storage.vue")
    assert "datastores.totals" not in s            # 內湖的容量不能支援敦南：不出全部機房的總容量
    assert "selRoom" in s and "各機房分開計算，不加總" in s


# ===== 6.1 存活 =====

def test_存活_取最新證據_網段之後掃過沒看到算不通(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO scan_history (hostname, ip, scan_time) VALUES ('a', '10.99.1.1', '2026-10-20 03:00:00')")
    conn.execute("INSERT INTO scan_history (hostname, ip, scan_time) VALUES ('b', '10.99.1.2', '2026-10-26 03:00:00')")
    conn.execute("CREATE TABLE IF NOT EXISTS scan_coverage (id INTEGER PRIMARY KEY, scan_time TEXT, cidr TEXT, source TEXT, ok INTEGER, created_at TEXT)")
    conn.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES ('2026-10-26 03:00:00', '10.99.1.0/24', 1, 'x')")
    conn.commit()
    rows = [{"ip": "10.99.1.1"}, {"ip": "10.99.1.2"}, {"ip": "10.99.9.9", "open_ports": "22", "collected_at": "2026-10-27 10:00:00"},
            {"ip": "10.99.8.8"}]
    m = v2_onboard.alive_map(conn, rows)
    assert m["10.99.1.1"] == {"state": "不通", "at": "2026-10-26 03:00:00", "by": "網路掃描"}
    assert m["10.99.1.2"]["state"] == "通"
    assert m["10.99.9.9"] == {"state": "通", "at": "2026-10-27 10:00:00", "by": "收集"}
    assert "10.99.8.8" not in m                    # 沒有任何證據：畫面寫「無資料」，不猜


def test_待納管_說明收合_存活欄_重新偵測():
    s = _src("pages/v2/onboard.vue")
    assert "brief: (r: any) => briefMsg(r.message)" in s
    assert "{ k: 'alive_text', label: '存活' }" in s and "/api/tools/alive-check/batch" in s
    t = _src("components/v2/Table.vue")
    assert ".t td.mono { white-space: nowrap; }" in t and "brief?: (r: any) => string" in t


# ===== 7.1 =====

def test_機櫃盤點_機櫃點得進去():
    s = _src("components/v2/InventoryFlow.vue")
    assert "link: props.kind === 'rack' ? null" not in s
    r = _src("pages/v2/inventory/rack.vue")
    assert "<RackDiagram />" in r and "<Relocation />" in r and "（舊版）</NuxtLink></p></template>" not in r


# ===== 7.3 =====

def test_軟體盤點結果有收集時間(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO host_package (ip, asset_serial, name, version, arch, source, last_seen) VALUES ('10.99.2.1','S1','php','8.1','x86_64','ssh_rpm','2026-10-20 08:00:00')")
    conn.commit()
    t = inv.create_task(conn, "software", "tester")
    r = inv.result(conn, "software", t["id"])
    assert r["rows"][0]["collected_at"] == "2026-10-20 08:00"


def test_軟體頁有搜尋與收集涵蓋():
    s = _src("pages/v2/inventory/software.vue")
    assert "/api/software/packages" in s and "/api/software/coverage" in s and "label: '搜尋軟體'" in s


# ===== 10.1 主機組月報 =====

NMON = """AAA,host,aix10
ZZZZ,T0001,00:00:00,01-SEP-2026
ZZZZ,T0002,00:05:00,01-SEP-2026
ZZZZ,T0003,00:10:00,01-SEP-2026
ZZZZ,T0004,00:15:00,02-SEP-2026
CPU_ALL,CPU Total aix10,User%,Sys%,Wait%,Idle%,Busy,CPUs
CPU_ALL,T0001,70.0,15.0,0,15,,8
CPU_ALL,T0002,60.0,25.0,0,15,,8
CPU_ALL,T0003,10.0,5.0,0,85,,8
CPU_ALL,T0004,20.0,10.0,0,70,,8
"""


def test_NMON_解析與月統計(tmp_path):
    conn = _conn(tmp_path)
    r = host_monthly.import_nmon(conn, NMON.encode(), "aix10.nmon", "tester")
    assert (r["host"], r["samples"]) == ("aix10", 4)
    m = host_monthly.nmon_month(conn, "2026-09")[0]
    assert (m["peak"], m["over"], m["longest_over_min"]) == (85.0, 2, 10)
    assert [x["date"] for x in m["days"]] == ["2026-09-01", "2026-09-02"]
    host_monthly.import_nmon(conn, NMON.encode(), "aix10.nmon", "tester")      # 再傳一次＝覆蓋，不重複
    assert host_monthly.nmon_month(conn, "2026-09")[0]["samples"] == 4


def test_NMON_不是NMON檔整份不存(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        host_monthly.import_nmon(conn, b"hello,world\n", "x.txt", "tester")


def test_管理判斷_當月沒存先帶上個月(tmp_path):
    conn = _conn(tmp_path)
    host_monthly.save_note(conn, "2026-09", {"verdict": "需關注", "actions": [{"risk": "EOS 改善", "status": "On Track"}]}, "tester")
    n = host_monthly.get_note(conn, "2026-10")
    assert n["_inherited"] == "2026-09" and n["verdict"] == "需關注"
    with pytest.raises(ValueError):
        host_monthly.save_note(conn, "2026/10", {}, "tester")


def test_月報整份算得出來(tmp_path):
    conn = _conn(tmp_path)
    out = host_monthly.report(conn, "2026-09")
    assert out["month"] == "2026-09" and out["prev"] == "2026-08"
    assert set(out["lifecycle"]["total"]) == {"total", "expired", "upcoming", "unknown", "ok"}
    assert "label: '主機組月報'" in _src("pages/v2/report.vue")


def test_數字核對_batch5_空庫與有資料都不誤報(tmp_path):
    import verify_numbers as vn
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO host_package (ip, asset_serial, name, version, arch, source, last_seen) VALUES ('10.99.3.1','S3','php','8.1','x86_64','ssh_rpm',NULL)")
    conn.commit()
    before = vn.FAILS
    vn.check_v2_batch5(conn)
    assert vn.FAILS == before


# ===== 2.007.14 =====

def test_搜尋補服務埠與功能_首頁進新版_計入台數兩條算式():
    s = _src("components/v2/Search.vue")
    assert "res.value?.services" in s and "V2_NAV.flatMap" in s
    assert "definePageMeta({ redirect: '/v2' })" in _src("pages/index.vue")
    r = _src("pages/v2/reconcile.vue")
    assert "exs.cia_registered" in r and "exs.pending_confirm" in r and "EQ_LEVELS" in r and "/api/existence/machines" in r


def test_新版搜尋_服務與埠(tmp_path):
    import v2_views
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO host_service (ip, asset_serial, port, process, service_guess, exposure, source) VALUES ('10.99.4.1','S4',1521,'tnslsnr','Oracle Listener','public','ssh_ss')")
    conn.commit()
    assert v2_views.search_services(conn, "1521")[0]["service"] == "Oracle Listener"
    assert v2_views.search_services(conn, "oracle")[0]["port"] == 1521
    assert v2_views.search_services(conn, "nginx") == []


def test_匯入頁每列有上次匯入與資料日期():
    import sys as _s
    from routers.imports import data_date_of
    assert data_date_of("BQ-10.99.169.191-RVTools_export_all_2026-08-28_13.49.57.xlsx") == "2026-08-28"
    assert data_date_of("dyn_20261013.csv") == "2026-10-13" and data_date_of("cia.xlsx") is None
    s = (FE / "pages" / "import.vue").read_text(encoding="utf-8")
    assert "<th>上次匯入（資料日期）</th>" in s and s.count("class=\"mono last\"") == 7


def test_上次匯入_沒有原始檔也看匯入紀錄(tmp_path):
    from routers.imports import _last_import
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO import_log (imported_by, hardware_count, personnel_count, software_count, error_count, imported_at, source, file_name, exported_at) "
                 "VALUES ('t', 1, 0, 0, 0, '2026-09-09 20:06:30', 'rvtools', 'X-RVTools_export_all_2026-08-28_13.49.57.xlsx', '2026-08-28 14:12:22')")
    conn.commit()
    assert _last_import(conn, "rvtools", None) == {"last_imported_at": "2026-09-09 20:06", "data_date": "2026-08-28"}
    assert _last_import(conn, "dynassets", None) == {"last_imported_at": None, "data_date": None}
