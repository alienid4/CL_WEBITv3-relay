"""Codex 997／1003／1005 回歸（固定 209 驗出的畫面文字與角色）：靜態檢查 V2 元件。"""
import re
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"


def _read(rel):
    return (FE / rel).read_text(encoding="utf-8")


def test_997_試算提示不再把本次命中數說成寫入後已分類總數():
    for rel in ("components/v2/Classify.vue", "pages/reports/classify.vue"):
        s = _read(rel)
        assert "就會是這個數字" not in s, rel
        assert "本次會寫入" in s, rel


def test_1005_來源對照兩個匯出按鈕只給管理員():
    s = _read("components/v2/HostSources.vue")
    tpl = s[s.index("<template>"):]
    for fn in ("exportMaster", "exportAllHosts"):
        btn = tpl.index(f'@click="{fn}"')
        bar = tpl.rfind("<div", 0, btn)
        assert 'v-if="isAdmin"' in tpl[bar:btn], fn          # 修前是 !isViewer：一般 user 看得到、點了 403
    assert "isViewer" not in s


def test_1003_全庫匯出按鈕台數含退役備查_與實際匯出列數一致():
    s = _read("components/v2/HostSources.vue")
    m = re.search(r'@click="exportAllHosts">([^<]*)</button>', s)
    assert m and "retired_hosts" in m.group(1)


def test_987_封存明細連結保留清單篩選_被篩掉時明細有提示():
    s = _read("pages/v2/capacity.vue")
    m = re.search(r"url\(\{ tab: 'archive', ar: String\(r\.id\)([^}]*)\}\)", s)
    assert m and "arFilterQuery()" in m.group(1)               # 修前只有 tab／ar，ast／apr／alt 被丟掉
    assert "data-archive-detail-filtered" in s and "arOneFiltered" in s
