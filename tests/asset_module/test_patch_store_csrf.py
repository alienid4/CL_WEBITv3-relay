"""弱點彙總追蹤（嵌入的 patch-app）共用儲存：會改東西的 XHR 一定要帶 X-CSRF-Token（Codex 317）。

store.js 是同步 XHR，不經過 useApi 的 CSRF 處理；少了標頭，後端回 403 code=csrf，寫入會靜靜失敗。"""
from pathlib import Path

STORE = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend" / "public" / "patch-app" / "js" / "store.js"


def test_共用xhr_非GET都帶CSRF標頭():
    src = STORE.read_text(encoding="utf-8")
    body = src[src.index("function xhr(method, url, body, type)"):]
    body = body[:body.index("return x;")]
    assert "X-CSRF-Token" in body and "document.cookie" in body and "csrf_token" in body
    assert "method !== 'GET'" in body
    assert body.index("X-CSRF-Token") < body.index("x.send(")          # 送出前設好


def test_寫入只走共用xhr_沒有另一條沒帶CSRF的寫入路徑():
    src = STORE.read_text(encoding="utf-8")
    opens = [ln.strip() for ln in src.splitlines() if ".open(" in ln]
    assert all("open(method" in o or "open('GET'" in o for o in opens), opens
