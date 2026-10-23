"""dynassets 存活清單：匯出空白範本（2026-08-25 使用者通則：有匯入就要有配對的
匯出範本，新使用者才知道要填什麼）。

計畫檔查證發現的落差：畫面上原本只寫「需含 IP 欄」，但實際上還吃主機名／MAC／OS
這些欄位——範本要把系統認得的全部欄位列出來，不能讓人誤以為只填 IP 就夠。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import dynassets_import  # noqa: E402


def test_匯出範本涵蓋全部認得的欄位_不是只有IP():
    headers, example = dynassets_import.export_template_rows()
    assert len(headers) == len(dynassets_import._COLUMN_CANDIDATES)
    assert "IP" in headers
    # 查證時發現的落差：畫面文案只講 IP，但主機名/MAC/OS 其實都吃。
    # 中文語系團隊的範本要優先給中文欄名，不是塞一堆英文縮寫。
    for must_have in ("主機名稱", "MAC", "作業系統"):
        assert any(must_have in h for h in headers), f"範本表頭少了「{must_have}」"


def test_匯出範本表頭是_COLUMN_CANDIDATES認得的別名():
    """表頭要能被自己的匯入邏輯認出來，不是隨便寫一個好看的名字。"""
    headers, example = dynassets_import.export_template_rows()
    for h in headers:
        norm_h = dynassets_import._norm(h)
        recognized = any(
            any(dynassets_import._norm(c) == norm_h for c in cands)
            for cands in dynassets_import._COLUMN_CANDIDATES.values()
        )
        assert recognized, f"範本表頭「{h}」不在 _COLUMN_CANDIDATES 認得的別名裡"


def test_匯出範本可以被自己的匯入邏輯讀回來():
    import openpyxl

    headers, example = dynassets_import.export_template_rows()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "template.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        ws.append(example)
        wb.save(p)

        records = dynassets_import.parse_dynassets(p)
        assert len(records) == 1
        assert records[0]["ip"] == "10.99.1.1"
        assert records[0]["hostname"] == "server01"
