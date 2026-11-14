"""[B-17] 弱點頁不可以宣稱「檔案不會上傳」（2026-09-21）。

實際行為（證據）：`public/patch/js/store.js` 用 `PUT /api/patch/blob/<key>` 把**解析後的狀態
與原始檔位元組**送到後端，後端 `patch_store.put()` 寫進 `patch_blob` 資料表。
頁面上卻寫「檔案僅在您的瀏覽器本機解析，不會上傳」「純前端本機解析」——
那是不實的資安說明，稽核看到就是缺失，使用者也會誤判「敏感報告沒離開我的電腦」。

這支守門：畫面文字不可以再出現那類宣稱。真的只存本機的地方要寫進白名單，
並且**在這裡講清楚為什麼它是真的**——沒有理由就不准放行。
"""
from pathlib import Path


PATCH_DIR = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend" / "public" / "patch-app"

# 不准出現的宣稱（弱點頁的資料確實會上傳到伺服器）
FORBIDDEN = ("不會上傳", "純前端本機解析", "不離開您的電腦", "不離開你的電腦", "只存在本機")

# 真的只寫 localStorage、沒送伺服器的地方。放行要附證據行號與理由。
ALLOW = {
    # 自動匯入的「來源資料夾」只寫 localStorage（js/autoimport.js loadCfg／saveCfg），
    # 小幫手才讀得到 UNC 路徑；這個欄位的值確實沒有送到伺服器。
    ("js/autoimport.js", "不上傳"),
}


def _files():
    return sorted([p for p in PATCH_DIR.rglob("*") if p.suffix in (".html", ".js")])


def test_弱點頁不可宣稱檔案不會上傳():
    bad = []
    for f in _files():
        text = f.read_text(encoding="utf-8", errors="replace")
        rel = f.relative_to(PATCH_DIR).as_posix()
        for i, line in enumerate(text.splitlines(), 1):
            for phrase in FORBIDDEN:
                if phrase in line:
                    bad.append(f"  {rel}:{i}  {line.strip()[:90]}")
    assert not bad, ("弱點頁的資料會存進伺服器 patch_blob，不可以這樣寫：\n" + "\n".join(bad))


def test_只有白名單能說不上傳():
    bad = []
    for f in _files():
        rel = f.relative_to(PATCH_DIR).as_posix()
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "不上傳" in line and (rel, "不上傳") not in ALLOW:
                bad.append(f"  {rel}:{i}  {line.strip()[:90]}")
    assert not bad, ("這幾處說「不上傳」，但弱點頁的設定與資料是存伺服器的；"
                     "真的只存本機才可以加進 ALLOW，並寫明理由：\n" + "\n".join(bad))


def test_有講清楚資料存在哪裡():
    """反面：不是把話拿掉就好，要正面講明資料存在伺服器。

    2026-09-21 B 指出：這支原本用 parametrize 帶兩個 phrase，但斷言對兩個參數完全相同，
    等於同一條跑兩次、parametrize 沒有作用。改成一條，接受任一種寫法。
    """
    joined = "\n".join(f.read_text(encoding="utf-8", errors="replace")
                       for f in _files() if f.suffix == ".html")
    assert any(p in joined for p in ("存到本系統伺服器", "存本系統伺服器")), \
        "拿掉錯的說明之後，要有一句正面說明資料存在哪裡"


def test_store確實會上傳_這支測試的前提還在():
    """前提查核：哪天真的改成不上傳了，這支要跟著失效提醒人改回說明。"""
    store = (PATCH_DIR / "js" / "store.js").read_text(encoding="utf-8", errors="replace")
    assert "'PUT'" in store and "/blob/" in store, \
        "store.js 不再上傳的話，上面的禁用字樣就該重新檢討（別留著錯的守門）"
