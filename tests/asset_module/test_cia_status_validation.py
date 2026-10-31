"""CIA 匯入：資產狀態不是合法值 → 不寫入、列進匯入警告與資料品質頁（2026-10-21）。

起因：CIA 匯出的 CSV 有資產用途含換行的列被切成兩行，下半行整排左移，
資產狀態欄位出現「1」「0」「A02櫃」。這種值寫進去會被當成在用、默默算進台數。
"""
import sys
import tempfile
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import data_quality  # noqa: E402
import db  # noqa: E402
import excel_import  # noqa: E402

HEAD = ["資產序號", "資產狀態", "主機名稱", "IP"]


def _wb(path: Path, rows) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "硬體"
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _setup(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def test_invalid_status_not_written_and_reported():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _setup(tmp)
        x = Path(tmp) / "a.xlsx"
        _wb(x, [["HW-T-1", "使用中", "h1", "10.99.0.1"],
                ["HW-T-2", "停用", "h2", "10.99.0.2"]])
        excel_import.import_excel(x, conn)
        # 第二份：HW-T-1 狀態變成錯位值 1、HW-T-3 新增帶「A02櫃」、HW-T-2 帶前後空白的合法值
        _wb(x, [["HW-T-1", 1, "h1", "10.99.0.1"],
                ["HW-T-2", " 報廢 ", "h2", "10.99.0.2"],
                ["HW-T-3", "A02櫃", "h3", "10.99.0.3"]])
        s = excel_import.import_excel(x, conn)
        assert s["invalid_status"] == [{"asset_serial": "HW-T-1", "value": "1"},
                                       {"asset_serial": "HW-T-3", "value": "A02櫃"}]
        assert any("資產狀態不是合法值：2 筆" in e for e in s["errors"])
        st = {r["asset_serial"]: r["asset_status"]
              for r in conn.execute("SELECT asset_serial, asset_status FROM hardware")}
        assert st == {"HW-T-1": "使用中", "HW-T-2": "報廢", "HW-T-3": None}

        # 資料品質頁：加總＝下鑽，兩筆都列出來
        m = data_quality.measure(conn)
        d = next(d for d in m["dimensions"] if d["key"] == "status_valid")
        assert (d["checked"], d["bad"]) == (3, 2)
        off = data_quality.list_offenders(conn, "status_valid")
        assert len(off) == d["bad"]
        assert {o["asset_serial"] for o in off} == {"HW-T-1", "HW-T-3"}
        assert any("「1」" in o["reason"] for o in off)

        # CIA 改好重匯 → 警告清空
        _wb(x, [["HW-T-1", "使用中", "h1", "10.99.0.1"],
                ["HW-T-2", "報廢", "h2", "10.99.0.2"],
                ["HW-T-3", "閒置", "h3", "10.99.0.3"]])
        s = excel_import.import_excel(x, conn)
        assert s["invalid_status"] == []
        assert data_quality.list_offenders(conn, "status_valid") == []
        conn.close()


def test_non_cia_assets_without_status_not_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _setup(tmp)
        conn.execute("INSERT INTO hardware (asset_serial, hostname) VALUES ('DYN-x1', 'd1')")
        conn.execute("INSERT INTO hardware (asset_serial, hostname, asset_status) "
                     "VALUES ('HW-M-1', 'm1', '待上線')")
        conn.commit()
        m = data_quality.measure(conn)
        d = next(d for d in m["dimensions"] if d["key"] == "status_valid")
        assert (d["checked"], d["bad"]) == (1, 0)
        conn.close()
