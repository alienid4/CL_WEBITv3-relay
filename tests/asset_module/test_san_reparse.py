"""zoneshow／nsshow 的解析與 SAN 重判讀（2026-10-07，搬遷重建第一批）。

這兩份原文從 2026-09-13 起就一直收進 `san_switch.raw_json`（`READONLY_COMMANDS`
裡本來就有 `zoneshow`／`nsshow`），但解析器從來沒看過它們。
重判讀**不連任何 switch**——正式區的 SAN 要重連得申請、得排時間。
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import san_collector  # noqa: E402
import san_parse as P  # noqa: E402

# zoneshow：Defined 段有 3 個 zone、2 個 cfg（其中 BACKUP_CFG 沒啟用），Effective 段指出啟用中那份
ZONESHOW = """\
Defined configuration:
 cfg:	PROD_CFG	Z_HOST1_STG; Z_HOST2_STG
 cfg:	BACKUP_CFG	Z_HOST3_STG
 zone:	Z_HOST1_STG
		10:00:00:00:c9:aa:bb:01; 50:06:01:60:aa:bb:cc:01
 zone:	Z_HOST2_STG
		10:00:00:00:c9:aa:bb:02; 50:06:01:60:aa:bb:cc:01
 zone:	Z_HOST3_STG
		10:00:00:00:c9:aa:bb:03; 50:06:01:60:aa:bb:cc:01
 zone:	Z_ORPHAN
		10:00:00:00:c9:aa:bb:09

Effective configuration:
 cfg:	PROD_CFG
 zone:	Z_HOST1_STG
		10:00:00:00:c9:aa:bb:01
"""

NSSHOW = """\
 N    011000;      2,3;10:00:00:00:c9:aa:bb:01;20:00:00:00:c9:aa:bb:01; na
    FC4s: FCP
    PortSymb: [35] "Emulex LPe12000 host1"
    Fabric Port Name: 20:10:00:05:1e:aa:bb:cc
 N    011100;      2,4;10:00:00:00:c9:aa:bb:02;20:00:00:00:c9:aa:bb:02; na
    FC4s: FCP
    NodeSymb: [20] "host2"
"""


# ---- zoneshow：定義過的全部，不只啟用中那份 ----
def test_zoneshow_收到定義過的全部_zone_不只啟用中那份():
    z = P.parse_zoneshow(ZONESHOW)
    # cfgactvshow 只會給 PROD_CFG 下的 2 個 zone；這裡要有全部 4 個
    assert set(z["zones"]) == {"Z_HOST1_STG", "Z_HOST2_STG", "Z_HOST3_STG", "Z_ORPHAN"}
    assert z["zones"]["Z_HOST1_STG"] == [
        "10:00:00:00:c9:aa:bb:01", "50:06:01:60:aa:bb:cc:01"]


def test_zoneshow_收到未啟用的_cfg():
    # 搬遷常見做法：為新機房預先定義一份 config，搬完才 cfgenable。
    # 只收 active 的話那份在我們的資料裡完全不存在。
    z = P.parse_zoneshow(ZONESHOW)
    assert set(z["configs"]) == {"PROD_CFG", "BACKUP_CFG"}
    assert z["configs"]["PROD_CFG"] == ["Z_HOST1_STG", "Z_HOST2_STG"]
    assert z["configs"]["BACKUP_CFG"] == ["Z_HOST3_STG"]


def test_zoneshow_標出沒被任何_cfg_收錄的_zone():
    z = P.parse_zoneshow(ZONESHOW)
    assert z["zones"]["Z_ORPHAN"]
    assert z["inactive_zones"] == ["Z_ORPHAN"]


def test_zoneshow_認得出啟用中那份():
    assert P.parse_zoneshow(ZONESHOW)["active_cfg"] == "PROD_CFG"


def test_zoneshow_空輸入_active_cfg_是_none_而且不丟例外():
    z = P.parse_zoneshow("")
    # None 的意思是「這份輸出沒講」，不是「沒有啟用任何 config」
    assert z["active_cfg"] is None
    assert z["zones"] == {} and z["configs"] == {}


# ---- nsshow：本機裝置，而且帶埠號 ----
def test_nsshow_撈到埠號():
    d = P.parse_nsshow(NSSHOW)
    assert set(d) == {"10:00:00:00:c9:aa:bb:01", "10:00:00:00:c9:aa:bb:02"}
    one = d["10:00:00:00:c9:aa:bb:01"]
    # 這是「插在第幾個埠」——重建要照著插回去的那個數字。nscamshow 沒有這欄。
    assert one["port_index"] == "3"
    assert one["port_id"] == "011000"
    assert one["wwnn"] == "20:00:00:00:c9:aa:bb:01"
    assert one["device_type"] == "N"
    assert one["fc4"] == "FCP"
    assert one["symbol"] == "Emulex LPe12000 host1"


def test_nsshow_沒有_symb_的也要列出來_只是那欄是_none():
    d = P.parse_nsshow(NSSHOW)
    assert d["10:00:00:00:c9:aa:bb:02"]["symbol"] == "host2"


def test_nsshow_空輸入回空_dict():
    assert P.parse_nsshow("") == {}


# ---- build_details 有把兩份接上 ----
def test_build_details_帶上_zoning_all_與_local_devices():
    d = P.build_details({"zoneshow": ZONESHOW, "nsshow": NSSHOW})
    assert d["zoning_all"]["active_cfg"] == "PROD_CFG"
    assert len(d["zoning_all"]["zones"]) == 4
    assert len(d["local_devices"]) == 2


def test_build_details_缺這兩個指令不丟例外():
    d = P.build_details({})
    assert d["zoning_all"]["zones"] == {}
    assert d["local_devices"] == {}


# ---- reparse_stored ----
def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE san_switch (ip TEXT PRIMARY KEY, switch_name TEXT, switch_wwn TEXT, "
                 "zoning_cfg TEXT, vendor TEXT, port_count INTEGER, zone_count INTEGER, "
                 "wwpn_count INTEGER, data_json TEXT, raw_json TEXT, collected_at TEXT, "
                 "collected_by TEXT)")
    return conn


def _insert(conn, ip, raw, collected_at="2026-09-13 10:00:00"):
    conn.execute("INSERT INTO san_switch (ip, data_json, raw_json, collected_at) VALUES (?,?,?,?)",
                 (ip, json.dumps({"rows": []}),
                  json.dumps(raw) if raw is not None else None, collected_at))
    conn.commit()


def test_reparse_從既有_raw_json_挖出_zoning_與本機裝置():
    conn = _db()
    _insert(conn, "10.0.0.1", {"zoneshow": ZONESHOW, "nsshow": NSSHOW})
    r = san_collector.reparse_stored(conn)
    assert r["updated"] == 1
    # 這幾個數字就是「這次多挖到什麼」
    u = r["updated_detail"][0]
    assert u["defined_zones"] == 4 and u["configs"] == 2
    assert u["inactive_zones"] == 1 and u["local_devices"] == 2

    d = json.loads(conn.execute("SELECT data_json FROM san_switch").fetchone()[0])
    assert d["details"]["zoning_all"]["configs"]["BACKUP_CFG"] == ["Z_HOST3_STG"]
    assert d["reparsed_at"]


def test_reparse_不動_raw_json_也不更新_collected_at():
    conn = _db()
    raw = {"zoneshow": ZONESHOW}
    _insert(conn, "10.0.0.1", raw)
    san_collector.reparse_stored(conn)
    row = conn.execute("SELECT raw_json, collected_at FROM san_switch").fetchone()
    assert json.loads(row["raw_json"]) == raw      # 原文是證據
    assert row["collected_at"] == "2026-09-13 10:00:00"   # 重判讀沒讓資料變新


def test_reparse_沒有_raw_json_要講原因():
    conn = _db()
    _insert(conn, "10.0.0.1", None)
    r = san_collector.reparse_stored(conn)
    # 「沒原文可重判」≠「這台沒有 zoning」
    assert r["updated"] == 0 and r["no_raw"] == 1
    assert "重收" in r["no_raw_detail"][0]["reason"]


def test_reparse_只做單一_ip():
    conn = _db()
    _insert(conn, "10.0.0.1", {"zoneshow": ZONESHOW})
    _insert(conn, "10.0.0.2", {"zoneshow": ZONESHOW})
    r = san_collector.reparse_stored(conn, only_ip="10.0.0.1")
    assert r["total"] == 1 and r["updated"] == 1
