"""匯出防公式注入（2.007.8，審查 B1）。

openpyxl 會把「=」開頭的字串當成公式寫進 xlsx；CSV 被 Excel 打開時，= + - @ 開頭（以及 Tab／CR 開頭）的儲存格也會被當公式。
資產資料是外部來源（CIA 匯入、掃描、收集），主機名稱、備註裡出現「=HYPERLINK(...)」之類的字，匯出的人一打開就會執行。
規則（OWASP CSV Injection）：字串開頭是這幾個字元就前面加一個「'」，一律當文字。數字照原樣（-1 仍是數字）。
"""
from __future__ import annotations

_RISKY = ("=", "+", "-", "@", "\t", "\r")
_BAD_TITLE = str.maketrans({c: "" for c in "[]\\:*?/"})


def cell(v):
    """單一儲存格：字串開頭是公式字元就加「'」。"""
    if isinstance(v, str) and v.startswith(_RISKY):
        return "'" + v
    return v


def row(vals):
    return [cell(v) for v in vals]


def sheet_title(s, default: str = "匯出") -> str:
    """工作表名稱：去掉 Excel 不允許的 [ ] \ : * ? /，最長 31 字。"""
    t = str(s or "").translate(_BAD_TITLE).strip() or default
    return t[:31]


def neutralize_workbook(wb) -> None:
    """存檔前把整本活頁簿每一格掃一遍：公式或公式字元開頭的字串一律改成文字（含標題列）。"""
    for ws in wb.worksheets:
        for r in ws.iter_rows():
            for c in r:
                v = c.value
                if isinstance(v, str) and (c.data_type == "f" or v.startswith(_RISKY)):
                    c.value = "'" + v
                    c.data_type = "s"
