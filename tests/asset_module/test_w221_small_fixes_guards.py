"""W221-010／MICS 產生派工清單 的前端守門（只讀原始碼，不跑瀏覽器）。"""
import re
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"


def _src(rel):
    return (FE / rel).read_text(encoding="utf-8")


def test_datafix_classify分頁有母體說明InfoNote():
    s = _src("pages/v2/datafix.vue")
    i = s.index("tab === 'classify'")
    block = s[i:i + 2500]
    assert "classify-scope-note" in block
    assert "report_baseline" in block and "對帳橋" in block
    assert "<InfoNote>" in block


def test_影響範圍查詢可產生派工清單_且viewer不顯示():
    s = _src("pages/v2/incident/index.vue")
    # 呼叫跟舊 /blast 同一套 API，成功後導到 /v2/checklist/<id>
    assert "'/api/blast/snapshot'" in s and "'/api/blast/checklist'" in s
    assert "snapshot_id: snap.id" in s
    assert "/v2/checklist/${snap.id}" in s
    # 按鈕只給能寫的人
    m = re.search(r'<button[^>]*@click="makeChecklist"[^>]*>', s)
    assert m and 'v-if="!isViewer"' in m.group(0)
    assert "產生派工清單" in s
    # 說明收進 InfoNote、不直接寫在畫面上
    assert "<InfoNote>會先存一份當下的快照" in s


def test_產生派工清單的目標頁存在():
    assert (FE / "pages/v2/checklist/[id].vue").exists()
