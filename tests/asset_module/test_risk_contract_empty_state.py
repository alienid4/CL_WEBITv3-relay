"""功能地圖 #13 保固到期（同步定案 ESJE 第 9 點：拆「功能 100%／合約覆蓋率」）。

功能驗收＝沒有合約資料時畫面講「無資料＋去哪匯入」，不能顯示 0（0＝有資料、真的沒有到期，意思完全不同）。
雙向：沒資料 → 無資料＋匯入連結、數字位置寫「無資料」；有資料 → 照實列數字，不再出現「無資料」那行。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import daily_risk as dr  # noqa: E402
from test_daily_risk import NOW, _conn, _fleet  # noqa: E402

FE = ROOT / "APP" / "asset-module" / "frontend"
RISK = (FE / "pages" / "v2" / "risk.vue").read_text(encoding="utf-8")
SEG = RISK[RISK.index("tab === 'contract'\">"):RISK.index("tab === 'hk'")]


def test_後端_沒有合約資料_回無資料旗標_不是0筆到期(tmp_path):
    c = _conn(tmp_path)
    _fleet(c)
    k = dr.risk_overview(c, NOW)["contract"]
    assert k["has_data"] is False and k["rows"] == [] and k["counts"]["none"] == k["total"] > 0
    dr.save_entry(c, "asset", "A-1", {"contract_end": "2026-10-22"}, "admin")
    k = dr.risk_overview(c, NOW)["contract"]
    assert k["has_data"] is True and k["counts"]["expired"] == 1 and len(k["rows"]) == 1


def test_畫面_無資料時頁籤與數字都寫無資料_不寫0():
    assert "'保固到期（無資料）'" in RISK and "!risk.value.contract?.has_data" in RISK
    # 「180 天內到期／已過期」「180 天以上」兩個數字：沒資料時寫「無資料」
    assert "risk.contract.has_data ? ctRows.length : '無資料'" in SEG
    assert "risk.contract.has_data ? risk.contract.counts.ok : '無資料'" in SEG


def test_畫面_無資料時有去哪匯入的連結_指到合約匯入分頁():
    m = re.search(r'<p v-if="!risk\.contract\.has_data"[^>]*>(.*?)</p>', SEG, re.S)
    assert m, "沒有「無資料」那一行"
    line = m.group(1)
    assert "無資料" in line
    assert re.search(r'<NuxtLink[^>]*to="/v2/settings\?st=contacts&tab=import"', line), "無資料那一行要有去合約匯入的連結"
    # 連結的目的地真的存在：聯絡資訊有「合約匯入」分頁，資料匯入頁的合約列也指到同一處
    contacts = (FE / "components" / "v2" / "Contacts.vue").read_text(encoding="utf-8")
    assert "{ k: 'import', label: '合約匯入' }" in contacts
    assert 'to="/v2/settings?st=contacts&tab=import"' in (FE / "pages" / "import.vue").read_text(encoding="utf-8")


def test_畫面_有資料時不顯示無資料那一行():
    assert '<p v-else class="note">' in SEG       # 有資料走另一段（照實數字）
    assert SEG.index('v-if="!risk.contract.has_data"') < SEG.index('<p v-else class="note">')
