"""[B-20] 弱點頁開頁要自動載入伺服器上那份，載不到要講原因（2026-09-21）。

以前三種情況都是**靜默**退回選檔畫面，使用者每天開頁都以為資料不見了：
  ① 伺服器上還沒有任何一份　② 連不到共用儲存（單機模式）　③ 讀到了但解析失敗
更根本的坑：store.js 的 API 位址只認網址上的 `?api=`，從書籤直接開就變單機模式、還不說話。

這支用靜態檢查守住「不可以再靜默」與「重新匯入鈕要在」；實際行為由 B 的驗收腳本在 221 驗。
"""
from pathlib import Path

PATCH = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend" / "public" / "patch-app"
MAIN = (PATCH / "js" / "main.js").read_text(encoding="utf-8")
STORE = (PATCH / "js" / "store.js").read_text(encoding="utf-8")
INDEX = (PATCH / "index.html").read_text(encoding="utf-8")


def test_開頁就去讀伺服器那份():
    init = MAIN[MAIN.index("function init()"):]
    assert "tryRestore();" in init[:init.index("function todayStr")], "init 要呼叫 tryRestore"
    assert "getBinary('workbook')" in MAIN, "伺服器那份是 workbook 這個 key"


def test_三種載不到的情況都要講原因():
    body = MAIN[MAIN.index("function tryRestore()"):]
    body = body[:body.index("\n  function ", 10)]
    assert "isShared()" in body and "Store.shared.error()" in body, "要問得出『為什麼沒有』"
    for phrase in ("連不到共用儲存", "讀不到伺服器上那份", "還沒有任何一份"):
        assert phrase in body, f"少了這種情況的說明：{phrase}"
    assert body.count("showError(") >= 3, "三種情況各自要有訊息，不可以靜默 return"


def test_解析失敗不可以只是清掉就算了():
    body = MAIN[MAIN.index("function tryRestore()"):]
    body = body[:body.index("\n  function ", 10)]
    i = body.index("clearState()")
    assert "showError(" in body[max(0, i - 400):i], "清掉之前要先告訴人那份壞在哪"


def test_重新匯入鈕在主畫面上():
    assert 'id="reimport-btn"' in INDEX, "要有『重新匯入』鈕"
    assert "重新匯入" in INDEX
    assert "reimport-btn" in MAIN and "resetToUpload()" in MAIN


def test_API位址要記得住_書籤直接開也能用():
    assert "vulnDashboard.apiBase" in STORE, "?api= 要記起來，下次沒帶也找得到 API"
    assert "localStorage.setItem(BASE_KEY" in STORE and "rememberedBase()" in STORE


def test_連不到共用儲存時要有原因可問():
    probe = STORE[STORE.index("function probe()"):]
    probe = probe[:probe.index("function keyUrl")]
    assert probe.count("lastError =") >= 2, "沒有 ?api=、以及連不上，兩種都要記原因"
    assert "沒有登入或沒有權限" in probe, "401/403 要分得出來（不然人會以為是網路問題）"
