"""1388 待補小項（2.065.x）：
N2  夜間速查補舊值班卡「收集／最近保存健檢」區（14 天變化已在 2.058.23 補）；
N12 掃描缺口匯出（V2 exportTable 與後端 CSV）都含人工標註／預計台數／備註；
封存：靜默窗字串讀 quiet_window 設定、作廢原因超過上限回錯誤不截斷。"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import capacity_archive as ar  # noqa: E402
import quiet_window as qw  # noqa: E402
import scan_gaps  # noqa: E402
from test_capacity_archive import TODAY, _old, mem  # noqa: E402

NIGHT = (FE / "pages" / "v2" / "night.vue").read_text(encoding="utf-8")
ONBOARD = (FE / "pages" / "v2" / "onboard.vue").read_text(encoding="utf-8")


def test_N2_夜間速查有最近14天變化與最近保存健檢():
    assert "最近 14 天的變化" in NIGHT and "card.value?.changes" in NIGHT
    assert "最近保存健檢" in NIGHT and "card.value?.collect" in NIGHT
    assert "col.health.taken_at" in NIGHT and "沒有健檢紀錄" in NIGHT
    assert "col.last_collect" in NIGHT and "col.last_seen_any" in NIGHT


def test_N12_掃描缺口匯出含標註_預計台數_備註():
    i = ONBOARD.index("function exportGaps")
    body = ONBOARD[i:ONBOARD.index("}", ONBOARD.index("exportTable(", i)) + 200]
    for h in ("'標註'", "'預計台數'", "'備註'"):
        assert h in body, h
    for k in ("r.note_label", "r.expected_hosts", "r.note "):
        assert k in body, k
    heads = [t for _, t in scan_gaps.COLUMNS]
    assert {"人工標註", "預計台數", "備註"} <= set(heads)


def test_封存設定的靜默窗字串跟著quiet_window設定走():
    c = mem()
    ar.ensure(c)
    assert "08:30–15:00" in ar.settings_view(c)["quiet_window"]          # 預設
    c.execute("INSERT INTO app_settings VALUES (?, ?)", (qw.KEY_START, "09:00"))
    c.execute("INSERT INTO app_settings VALUES (?, ?)", (qw.KEY_END, "14:00"))
    s = ar.settings_view(c)["quiet_window"]
    assert "09:00–14:00" in s and "08:30" not in s
    c.execute("INSERT OR REPLACE INTO app_settings VALUES (?, ?)", (qw.KEY_ENABLED, "0"))
    assert "已關閉" in ar.settings_view(c)["quiet_window"]


def test_作廢原因超過上限_回錯誤講上限_不靜默截斷():
    c = mem()
    _old(c, ["2024-09-12"])
    a = ar.citation_create(c, {"title": "t", "refs": ["2024-09-12"]}, "admin", TODAY)
    with pytest.raises(ValueError, match=f"最多 {ar.CITE_VOID_REASON_MAX} 字"):
        ar.citation_void(c, a["id"], "x" * (ar.CITE_VOID_REASON_MAX + 1), "admin", TODAY)
    ok = "y" * ar.CITE_VOID_REASON_MAX
    ar.citation_void(c, a["id"], ok, "admin", TODAY)
    got = c.execute("SELECT reason FROM capacity_archive_citation_void WHERE citation_id = ?", (a["id"],)).fetchone()[0]
    assert got == ok
    src = (ROOT / "APP" / "asset-module" / "backend" / "capacity_archive.py").read_text(encoding="utf-8")
    assert not re.search(r"reason\[:\d+\]", src)
