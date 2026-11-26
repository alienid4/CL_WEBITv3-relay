"""2.066.30：盤中靜默狀態列（QuietWindowBar）在 ⚙ 系統設定要看得到——不能只綁在「收集身分›掃描排程」一個子頁
（那個頁籤是管理員專用，.14 上非管理員、或預設落在別的子頁就永遠看不到）。靜態檢查：條件綁頁籤、不綁單一子頁。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "v2" / "settings.vue").read_text(encoding="utf-8")


def _cond():
    m = re.search(r"<QuietWindowBar v-if=\"([^\"]+)\"", SETTINGS)
    assert m, "settings.vue 沒有掛 QuietWindowBar"
    return m.group(1)


def test_靜默狀態列_綁頁籤_不綁單一子頁():
    cond = _cond()
    assert "sub ===" not in cond                                   # 不再綁「掃描排程」單一子頁
    assert "tab === 'identity'" in cond and "tab === 'integrations'" in cond


def test_靜默狀態列_外部整合非管理員看得到的頁籤也有():
    """外部整合（中繼匯入）非管理員唯讀看得到；窗內延後匯入的原因就在這條狀態列。"""
    assert "t.k === 'integrations'" in SETTINGS            # 非管理員仍留著「外部整合」頁籤
    assert "'integrations'" in _cond()


def test_元件本身存在且不依賴管理員才會載入():
    comp = (ROOT / "APP" / "asset-module" / "frontend" / "components" / "QuietWindowBar.vue").read_text(encoding="utf-8")
    assert "/api/quiet-window" in comp and 'v-if="isAdmin" type="button" class="qw-btn" @click="showCfg' in comp   # 只有「設定」按鈕限管理員
