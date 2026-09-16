"""RVTools 匯出裡的日期欄不可以炸掉整份匯入（2026-08-20 使用者實測五個檔全掛）。

錯誤訊息是
    匯入失敗，請確認是 RVTools 匯出的檔：Object of type datetime is not JSON serializable
而那五個檔就是 RVTools 匯出的 —— 訊息在怪使用者，實際上是我們自己的 bug。

爆點在「額外分頁整列原樣存進 source_record」：RVTools 有一堆日期時間欄
（快照建立時間、開機時間…），openpyxl 讀成 datetime，json.dumps 直接拋例外。
而且是整份檔案共用一次序列化，**任一列任一格是日期，整個檔案就全軍覆沒**——
這就是為什麼五個檔一個都進不來。
"""
import datetime
import sys
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import rvtools_import  # noqa: E402


def _make_xlsx(path: Path) -> Path:
    """做一份最小的 RVTools 樣子的檔：vInfo 一台 VM ＋ vSnapshot 帶日期欄。"""
    wb = openpyxl.Workbook()
    vinfo = wb.active
    vinfo.title = "vInfo"
    vinfo.append(["VM", "DNS Name", "VM UUID", "Powerstate", "Host", "Cluster",
                  "Primary IP Address", "OS according to the VMware Tools", "Path",
                  "Creation date"])
    vinfo.append(["VM-A", "vm-a.example.com", "uuid-a", "poweredOn", "esxi01.example.com",
                  "CL01", "10.0.0.5", "Rocky Linux 9", "[DS_A] uuid/VM-A.vmx",
                  datetime.datetime(2024, 3, 1, 9, 30, 0)])

    snap = wb.create_sheet("vSnapshot")
    snap.append(["VM", "Name", "Date / time", "Size MiB"])
    # ← 這一格就是害五個檔全掛的東西
    snap.append(["VM-A", "before-patch", datetime.datetime(2025, 11, 2, 14, 5, 0), Decimal("1024.5")])
    snap.append(["VM-A", "old-snap", datetime.date(2023, 1, 9), 2048])

    wb.save(path)
    return path


def test_日期欄不會讓整份匯入失敗(tmp_path):
    xlsx = _make_xlsx(tmp_path / "rv.xlsx")
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        # 修好之前，這一行會拋 TypeError: Object of type datetime is not JSON serializable
        summary = rvtools_import.import_rvtools(xlsx, conn)
        assert summary is not None
        conn.commit()

        # VM 要真的進來
        n = conn.execute("SELECT COUNT(*) FROM source_record WHERE source='vcenter'").fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_日期轉成字串保留而不是被丟掉(tmp_path):
    """payload 的用途是「這份匯出還告訴我們什麼」。快照建立時間正是之後做
    快照稽核最需要的欄位，為了繞過錯誤把它丟掉，等於白存。"""
    xlsx = _make_xlsx(tmp_path / "rv.xlsx")
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        rvtools_import.import_rvtools(xlsx, conn)
        conn.commit()
        rows = conn.execute(
            "SELECT payload FROM source_record WHERE source LIKE 'vcenter_extra:%'"
        ).fetchall()
        assert rows, "vSnapshot 分頁要有存進來"
        blob = " ".join(r[0] for r in rows)
        assert "2025-11-02 14:05:00" in blob      # datetime → ISO 字串
        assert "2023-01-09" in blob               # date → ISO 字串
    finally:
        conn.close()


@pytest.mark.parametrize("raw,expect", [
    (datetime.datetime(2024, 5, 6, 7, 8, 9), "2024-05-06 07:08:09"),
    (datetime.date(2024, 5, 6), "2024-05-06"),
    (datetime.time(7, 8, 9), "07:08:09"),
    (Decimal("12.5"), 12.5),
    ("已經是字串", "已經是字串"),
    (123, 123),
    (None, None),
])
def test_轉換規則(raw, expect):
    assert rvtools_import._jsonable(raw) == expect


# ===== 匯出時間 vs 匯入時間（2026-08-20 使用者要求標出來）=====
#
# 2026-08-20 匯進來的五個檔全部是 07-30 匯出的，中間隔三週。畫面只寫「最後匯入
# 8/20」會讓人以為資料是新的 —— 而拿三週前的快照算爆炸半徑，會漏掉搬過來的 VM、
# 也會多算搬走的。查 8/18 事故那台 ESXI169-220 只列出 16 台，當天手抄是 30 台。

@pytest.mark.parametrize("name,expect", [
    ("BQ_10.99.169.191_RVTools_export_all_2026-07-30_10.34.59.xlsx", "2026-07-30 10:34:59"),
    ("NH_10.99.198.21_RVTools_export_all_2026-07-30_10.22.15.xlsx", "2026-07-30 10:22:15"),
    ("RVTools_export_all_2026-01-05_09.00.00.xlsx", "2026-01-05 09:00:00"),
    ("匯出_2026-03-11.xlsx", "2026-03-11"),          # 只有日期也接受
])
def test_從檔名認出匯出時間(name, expect):
    assert rvtools_import.export_time_from_filename(name) == expect


@pytest.mark.parametrize("name", [
    None, "", "rvtools.xlsx", "匯出檔.xlsx", "export_all.xlsx",
])
def test_認不出來要回None而不是猜一個(name):
    """「不知道這份多舊」跟「這份是今天的」是完全不同的兩句話。
    拿檔案時間或匯入時間頂替＝製造假證據，比沒有這個欄位更糟。"""
    assert rvtools_import.export_time_from_filename(name) is None
