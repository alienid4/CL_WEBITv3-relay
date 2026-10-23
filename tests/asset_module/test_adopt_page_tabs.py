"""2-1 /adopt 拆頁簽（v1.459.1，使用者 2026-10-18：頁面太長、不接受一頁很長）。

釘住：
  1. 三個頁簽都在：開始收集／待納管／已納管主機 N（N 用 managed.length，跟首頁同一個數）
  2. 預設頁簽是「待納管」；hash #collect／#pending／#onboarded 可直接連進來，舊錨點 #managed-hosts 也還有效
  3. 上次頁簽記 localStorage，讀寫都包 try/catch
  4. 頁簽面板用 v-show（不是 v-if）——切頁簽不能弄丟勾選、搜尋、CollectLog ref、輪詢狀態
  5. 每塊內容落在對的頁簽
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "adopt.vue").read_text(encoding="utf-8")
TPL = SRC[SRC.index("<template>"):SRC.rindex("</template>")]


def _panel(tab: str) -> str:
    """回傳 v-show="activeTab === '<tab>'" 那個面板到下一個面板（或模態視窗區）之前的模板。"""
    start = TPL.index(f"v-show=\"activeTab === '{tab}'\"")
    nxt = [TPL.find("v-show=\"activeTab === '", start + 10), TPL.find("<!-- ===== 搬到別的頁簽", start)]
    nxt = [i for i in nxt if i > 0]
    return TPL[start:min(nxt)] if nxt else TPL[start:]


def test_three_tabs_exist():
    for t in ("collect", "pending", "unregistered", "failures", "onboarded"):
        assert f"setTab('{t}')" in TPL, f"缺頁簽 {t}"
        assert f"v-show=\"activeTab === '{t}'\"" in TPL, f"頁簽 {t} 的面板沒用 v-show"
    assert "開始收集" in TPL and "待納管" in TPL
    assert "已納管主機 {{ managed.length }}" in TPL


def test_default_hash_and_storage():
    assert "ref<AdoptTab>('pending')" in SRC
    assert "ADOPT_TABS: AdoptTab[] = ['collect', 'pending', 'unregistered', 'failures', 'onboarded']" in SRC
    assert "t === 'managed-hosts'" in SRC, "舊的 #managed-hosts 錨點要導到已納管頁簽"
    assert "watch(() => route.hash" in SRC
    # localStorage 每次讀寫都包在 try 裡
    for m in re.finditer(r"localStorage\.(get|set)Item", SRC):
        before = SRC[max(0, m.start() - 160):m.start()]
        assert "try" in before, "localStorage 讀寫沒有包 try/catch"


def test_panels_hold_right_content():
    collect = _panel("collect")
    assert "runDispatch" in collect and "<CollectLog" in collect and "segpick" in collect
    pending = _panel("pending")
    for needle in ("pickStatus('collected')", "setRoute('actionable')", "showBatchProbe"):
        assert needle in pending, f"待納管頁簽缺 {needle}"
    # 2026-10-22 使用者：「我不想往下拉」——未登記主機、納管失敗各自一個頁簽
    unreg = _panel("unregistered")
    for needle in ("toggleAllAdopt", "openOnboard(h)", "openAdopt(h)"):
        assert needle in unreg, f"未登記主機頁簽缺 {needle}"
    fails = _panel("failures")
    assert ':href="failExportUrl"' in fails and 'v-for="f in fShown"' in fails
    assert "failExportUrl" not in pending and "toggleAllAdopt" not in pending
    # 2026-10-22：沒讀完顯示「…」或上次資料，不顯示 0
    assert "未登記主機 {{ hosts.length }}" in TPL and "納管失敗 {{ fLoaded || cacheAt ? failures.length : '…' }}" in TPL
    onboarded = _panel("onboarded")
    for needle in ("toggleAllManaged", "↻ 勾選的一次重新納管", "revokeIp = m.ip", "managedKw", "managedOs"):
        assert needle in onboarded, f"已納管頁簽缺 {needle}"
    assert "jumpbar" not in TPL, "舊的「已納管主機 N ↓」跳轉鈕應已移除"
