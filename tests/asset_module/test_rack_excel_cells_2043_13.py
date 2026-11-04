"""2.043.13：機櫃圖（RackElevation）——每一格 IP 可點去 2.1 主機查詢、Excel 層的格子可處理（移除／改 U 位與名稱）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"


def _r(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def test_3_每一格IP都能點去主機查詢():
    v = _r("components/RackElevation.vue")
    assert "const ipHref = (ip: string) => `/v2/hosts?q=${encodeURIComponent(ip)}`" in v
    assert 'data-testid="rack-ip"' in v and 'data-testid="xl-ip"' in v
    assert v.count('@click.stop="ipClick(') == 2, "系統設備與 Excel 層兩種格子都要能點"
    # 系統設備原本點名稱進主機履歷的行為保留
    assert "function goAsset(d: Dev) { navigateTo(`/v2/machine/" in v and '@dblclick="goAsset(d)"' in v
    # 雙擊改 IP 時不跳頁
    assert "ipCancel(); openFieldEdit(d, 'ip', $event)" in v


def test_每格狀態標示用同一組字():
    v = _r("components/RackElevation.vue")
    assert "sysCellStatus, xlCellStatus } from '~/utils/rackCellStatus'" in v
    assert 'data-testid="cell-status"' in v and "sysCellStatus(d.state, exStatus(d))" in v


def test_合櫃後櫃名用Excel_CIA原名只在InfoNote():
    v = _r("components/RackElevation.vue")
    assert 'data-testid="rack-cia-names"' in v and "<InfoNote v-if=\"det.rack_cia_names?.length\"" in v
    m = _r("pages/v2/machine/[key].vue")
    assert "CIA 原始櫃名：{{ m.where.rack_info.rack_cia }}" in m
    n = _r("components/v2/RackNameMap.vue")
    assert "'/api/racks/name-map'" in n and "v-if=\"canEdit\"" in n and "!isViewer.value" in n
    assert "<V2RackNameMap" in _r("pages/v2/datacenter.vue")
