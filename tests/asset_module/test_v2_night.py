"""夜間速查（Q98，/v2/night）：半夜值班用的獨立頁。釘住幾條不能退步的設計——
全部唯讀、VMware 設備絕不即時檢測、只用既有 API、貼整段告警能抓出 IP、釘在選單最上面。"""
import re
from pathlib import Path

FE = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"
PAGE = (FE / "pages" / "v2" / "night.vue").read_text(encoding="utf-8")
# 2.029：判斷邏輯與端點呼叫抽到 composable（互動行為由 frontend/tests/nightLive.spec.ts 的 vitest 驗證；這裡只釘結構）
LIVE = (FE / "composables" / "useNightLive.ts").read_text(encoding="utf-8")
PAGE_ALL = PAGE + "\n" + LIVE
LAYOUT = (FE / "layouts" / "v2.vue").read_text(encoding="utf-8")
NAV = (FE / "composables" / "useNavV2.ts").read_text(encoding="utf-8")


def test_釘在選單最上面_不在任何一組裡面():
    assert "export const V2_PINNED" in NAV and "to: '/v2/night'" in NAV
    # 釘選項在群組迴圈之前；也不在 V2_NAV 的任何一組裡（群組裡不會出現 /v2/night 以外的第二份）
    assert LAYOUT.index('class="pin"') < LAYOUT.index('v-for="g in V2_NAV"')
    nav_body = NAV[NAV.index("export const V2_NAV"):NAV.index("export function v2ActiveTo")]
    assert "/v2/night" not in nav_body
    assert "layout: 'v2'" in PAGE


def test_只呼叫既有唯讀端點_沒有任何寫入():
    calls = set(re.findall(r"apiFetch[^(]*\(\s*'(/api/[^']+)'", PAGE_ALL))
    assert calls == {"/api/oncall", "/api/blast/resolve", "/api/contacts/card", "/api/health-check/run", "/api/blast/impact", "/api/night/settings"}
    # 唯一的 POST 是即時檢測（只讀探測）；沒有 PUT／DELETE／PATCH
    assert not re.search(r"method:\s*'(PUT|DELETE|PATCH)'", PAGE_ALL)
    assert len(re.findall(r"method:\s*'POST'", PAGE_ALL)) == 1


def test_VMware設備不發即時檢測():
    # 在發出 health-check 之前，先看後端判定的 vmware 旗標，直接 return
    # 頁面在呼叫 runLiveCheck（唯一發 health-check 的地方）之前，先問 decideLive，vmware 動作直接 return
    i = PAGE.index("async function liveCheck")
    j = PAGE.index("runLiveCheck(apiFetch", i)
    guard = PAGE[i:j]
    assert "c.what.vmware" in guard and "dec.action === 'vmware'" in guard and "return" in guard
    assert "天條" in guard
    assert "if (p.isVmware) return { action: 'vmware'" in LIVE


def test_即時檢測有逾時_不會無限轉圈():
    assert "TIMEOUT_MS = 14000" in PAGE and "runLiveCheck(apiFetch, ips, TIMEOUT_MS)" in PAGE
    assert "timeout: timeoutMs" in LIVE
    assert "連不上／逾時" in PAGE and "重新檢測" in PAGE


def test_沒登記值班電話要大聲講_並有退路():
    assert "沒有登記值班電話" in PAGE
    assert "這台沒有任何電話資料" in PAGE
    assert "tel:" in PAGE


def test_最近查過_localStorage都包try_catch():
    for m in re.finditer(r"localStorage\.(getItem|setItem)", PAGE):
        before = PAGE[max(0, m.start() - 200):m.start()]
        assert "try" in before, PAGE[m.start() - 80:m.start() + 60]


def test_深色預設且可切換():
    assert "data-night" in PAGE and "toggleTheme" in PAGE and "prefers-color-scheme" in PAGE
    css = (FE / "assets" / "css" / "main.css").read_text(encoding="utf-8")
    assert ":root[data-night='dark']" in css


def test_說明收進註_畫面沒有長篇說明():
    assert "<InfoNote>" in PAGE


def test_正式用語_沒有口語():
    for w in ["出事了", "要停機", "掛了", "還在嗎", "在線", "連得進去", "連不進去", "很久沒看到", "不知道", "病歷"]:
        assert w not in PAGE, w


def test_候選用資產序號查_不是拿打的字再解析一次():
    assert "run(c.asset_serial, false, true)" in PAGE
    assert "你正在看：" in PAGE and "資產序號" in PAGE        # 選定後鎖定的標頭


def test_貼多個IP或無效詞_列成選項不默默拿第一個():
    assert "function tokens(" in PAGE and "checkIp" in PAGE and "超出 0 到 255" in PAGE
    assert "good.length > 1" in PAGE and "choices.value = toks" in PAGE
    assert "加入" in PAGE and "dropChoice" in PAGE            # 可編輯


def test_綠燈要說查了什麼_查不到不能是綠():
    assert "已檢查：" in PAGE and "無法確認" in PAGE
    assert "miss.has('services')" in PAGE and "miss.has('disks')" in PAGE and "miss.has('uptime')" in PAGE
    assert "unknownLights(" in PAGE and PAGE.count("unknownLights(") >= 5     # 逾時、錯誤、連不上、沒檢測、權限都是無法確認


def test_電話顯示與撥打同一個值_有更新時間():
    assert ':href="telHref(o.phone as string)"' in PAGE and "{{ o.phone }}" in PAGE
    assert ':href="telHref(f.phone)"' in PAGE and "{{ f.phone }}" in PAGE
    assert "更新時間未記錄" in PAGE and "登記更新" in PAGE


def test_失敗訊息只講事實_不下指令():
    for w in ["禁止停機", "不要停機", "可以停機", "請勿", "可能剛當過機", "這台可能真的有問題"]:
        assert w not in PAGE, w
    assert "權限不足" in PAGE and "基準資料較舊" in PAGE and "逾時" in PAGE


def test_卡3標方向_且講明沒有備援流量資料():
    assert "上游：依賴它的" in PAGE and "它依賴的下游" in PAGE
    assert "備援（HA）、流量、負載分擔的資料，系統裡沒有" in PAGE and "不能據此判斷停掉是否安全" in PAGE


def test_時間都標伺服器時間與資料年齡():
    assert "伺服器時間 Asia/Taipei" in PAGE and "ageText(" in PAGE and "分鐘前檢測" in PAGE
    assert "開機時間" in PAGE


# ---------------------------------------------------------------- W221-N1 自動即時檢測開關
def test_自動檢測關時不自動呼叫_但有立即檢測鈕():
    # 自動路徑（查完主機）傳 manual=false；關閉時在發出 health-check 之前就 return
    assert "void liveCheck(r.card, my, false)" in PAGE
    i = PAGE.index("async function liveCheck")
    j = PAGE.index("runLiveCheck(apiFetch", i)
    guard = PAGE[i:j]
    assert "decideLive(apiFetch" in guard and "manual" in guard and "dec.action === 'manual'" in guard and "mk('manual'" in guard
    assert "if (p.manual)" in LIVE and "resolveAutoLive(" in LIVE
    assert "已設定為不自動檢測（可按「立即檢測」）" in PAGE
    assert ">立即檢測</button>" in PAGE and 'data-testid="live-manual"' in PAGE


def test_VMware在開關檢查之前就return():
    i = LIVE.index("export async function decideLive")
    assert LIVE.index("p.isVmware", i) < LIVE.index("/api/night/settings", i)
    j = LIVE.index("export function shouldAutoCheck")
    assert LIVE.index("p.isVmware", j) < LIVE.index("p.mode === 'manual'", j)


def test_設定讀不到_fail_closed_且不快取():
    # 一份關閉的稽核設定不能被暫時性失敗繞過：讀取失敗＝不自動檢測，提示按立即檢測；電話與影響範圍照查
    i = LIVE.index("export async function resolveAutoLive")
    body = LIVE[i:i + 500]
    assert "failed: true" in body and "on: false" in body
    assert "無法讀取設定，請按立即檢測" in LIVE
    assert "autoLivePromise" not in PAGE_ALL            # 不快取到頁面生命週期：每次查詢都重讀


def test_設定頁有開關_只admin看得到():
    contacts = (FE / "pages" / "contacts.vue").read_text(encoding="utf-8")
    assert "/api/night/settings" in contacts and "setNightAuto" in contacts
    i = contacts.index("<b>夜間速查：自動即時檢測")
    assert 'v-if="canEdit"' in contacts[max(0, i - 200):i]
    assert "<InfoNote>" in contacts[i:i + 400]
