"""2.043.11：GPT 審查 2.043.10（程式 A1–A5、挑剔使用者 B1–B5）。純函式在 vitest（impactView／incidentHost／v2LegacyMap），
這裡釘畫面確實接上。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"


def _r(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def test_A1_影響範圍帶mode與port():
    idx = _r("pages/v2/incident/index.vue")
    assert "port: impPort.value" in idx and "mode: impMode.value" in idx and ':mode="impMode"' in idx
    d = _r("components/v2/ImpactDetail.vue")
    assert "mode: props.mode" in d, "CSV 用同一個 mode"


def test_A2_A3_快照():
    d = _r("components/v2/ImpactDetail.vue")
    assert "snapshotBelongs(snap, props.nodeId)" in d
    assert "if (!b || viewing.value) return" in d, "看快照時不去查現在的連線列"
    assert "snapshotObsRows(viewing.value.result" in d


def test_A4_ESXi列用VM結果彙總():
    v = _r("pages/v2/incident/[id].vue")
    assert "hostStatus(hostKey, MS.value)" in v


def test_A5_B3_匯出連結只給能匯出的人_依系統也有():
    v = _r("pages/v2/datafix.vue")
    assert v.count("...(isViewer ? [] : [{ k: 'exp'") == 2
    assert "label: '匯出這個系統'" in v and "&ub=sys&sys=" in v
    assert "|| isViewer.value) return" in v


def test_B1_B2_B5():
    idx = _r("pages/v2/incident/index.vue")
    assert re.search(r"受影響節點 <b>\{\{ impHit\.affected \}\}</b> 個（主機", idx)
    d = _r("components/v2/ImpactDetail.vue")
    assert "label: '影響系統'" in d and "data-testid=\"impact-scale\"" in d
    assert "k: 'csv', label: '下載'" in d and "K{{ s.__no }}" not in d, "快照下載在同一列，不再另列 K1 K2"


def test_B4_生命週期下一步與誰():
    c = _r("components/v2/Lifecycle.vue")
    assert "label: '下一步'" in c and "label: '誰'" in c and "hasNext.value" in c


# ---- 2.043.12：GPT 複核補的三條 ----
def test_2043_12_快照歸屬隨查詢對象重驗():
    d = _r("components/v2/ImpactDetail.vue")
    assert "watch(() => [route.query.snap, props.nodeId]" in d


def test_2043_12_blast地圖轉到系統頁上下游頁籤():
    u = _r("utils/v2LegacyMap.ts")
    assert "to(`/v2/system/${encodeURIComponent(f.system)}`, { tab: 'deps' })" in u
    s = _r("pages/v2/system/[id].vue")
    assert "{ k: 'deps', label:" in s, "系統頁真的有 deps 頁籤"


def test_2043_12_生命週期第一步是做得到的事_帶這台():
    c = _r("components/v2/Lifecycle.vue")
    assert "到 CIA 登記這台" not in c
    assert "/v2/hosts?tab=no_cia&q=${encodeURIComponent(r.hostname || r.ip || r.serial)}" in c
    v2h = (ROOT / "APP" / "asset-module" / "backend" / "v2_hosts.py").read_text(encoding="utf-8")
    assert '("no_cia", "未登錄 CIA")' in v2h, "2.1 真的有這個頁籤"
