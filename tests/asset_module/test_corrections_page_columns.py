"""3-15 系統校正清單（corrections.vue）：表頭順序＝每格順序。

2026-10-21 v1.482.2 加「需誰處理」時表頭插在「最後確認」前、格子卻加在後面——
畫面「需誰處理」那欄顯示成時間（使用者：「需誰處理怎是時間」）。這支把兩邊釘在一起。
"""
from __future__ import annotations

import re
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend" / "pages"
        / "corrections.vue").read_text(encoding="utf-8")


def _cols():
    thead = re.search(r"<thead>(.*?)</thead>", PAGE, re.S).group(1)
    heads = re.findall(r'<SortTh k="(\w+)"', thead)
    tbody = re.findall(r"<tbody>(.*?)</tbody>", PAGE, re.S)[-1]   # 最後一個＝主表（說明裡有優先序小表）
    # 每格看「顯示的內容」（開頭標籤之後），:class 裡用來上色的 r.xxx 不算
    cells = [re.search(r"\br\.(\w+)", td.split(">", 1)[1]).group(1)
             for td in re.findall(r"<td\b.*?</td>", tbody, re.S)]
    return heads, cells


def test_表頭與格子同一個欄位順序():
    heads, cells = _cols()
    assert heads == cells
    assert heads.index("needs") < heads.index("at")
    assert "action" in heads           # 要修改什麼


def test_只有要被改掉的值紅字_不整列紅():
    assert "needrow" not in PAGE       # 舊：需人處理的整列紅（使用者：「太紅了」）
    tbody = re.findall(r"<tbody>(.*?)</tbody>", PAGE, re.S)[-1]   # 最後一個＝主表（說明裡有優先序小表）
    red = [td for td in re.findall(r"<td\b.*?</td>", tbody, re.S) if "fixme" in td]
    assert len(red) == 1 and "r.cia" in red[0]
