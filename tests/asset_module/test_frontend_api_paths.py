"""前端呼叫的 API 路徑，後端必須真的有（2026-09-16）。

## 為什麼要有這支

v1.186.0 把 `/api/assets/exempt` 改名成 `/api/onboard-exempt`（原路徑會被
`GET /api/assets/{asset_serial}` 先吃掉），但只改了頁面與測試，**漏改 ExemptModal.vue**。
結果使用者在公司機按「非納管」跳出「標記失敗：Method Not Allowed」——
POST 打到只收 GET 的那條路由。

單元測試抓不到這種漏改：後端測試打的是新路徑、全綠；前端沒有測試。
所以直接比對「前端寫了哪些 /api/ 路徑」與「後端註冊了哪些路由」。

## 判定方式

- 只看字面常數（`'/api/...'` 或 `` `/api/...` ``），模板字串裡的 `${...}` 換成參數樣板
- 後端路由的 `{param}` 也換成同一個樣板再比對
- 比對**方法**：前端 `method: 'POST'` 就要求後端有 POST；沒寫 method 視為 GET
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402

#: 這些不是後端路由（外部網址或刻意的字串），列出來就不會被誤判
ALLOW = set()

_PARAM = "<p>"
_CALL = re.compile(r"""apiFetch<[^>]*>\(\s*([`'"])(/api/[^`'"]*)\1|apiFetch\(\s*([`'"])(/api/[^`'"]*)\3""")
_HREF = re.compile(r"""apiBase\}(/api/[^`'"]*)""")
_METHOD = re.compile(r"method:\s*'(\w+)'")


def _norm(path: str) -> str:
    """把 ${...} 與 {param} 都換成同一個樣板，並去掉 query string。

    `/api/classify${q}` 這種 q 是 query string（`?a=b`）不是路徑段——樣板沒有接在 `/`
    後面就當它是 query，切掉再比對，否則會誤報「後端沒有 /api/classify<p>」。
    """
    path = path.split("?")[0]
    path = re.sub(r"\$\{[^}]*\}", _PARAM, path)
    path = re.sub(r"\{[^}]*\}", _PARAM, path)
    # 樣板沒有自成一段（前面不是 "/"）＝接的是 query string，不是路徑
    if path.endswith(_PARAM) and not path.endswith("/" + _PARAM):
        path = path[: -len(_PARAM)]
    return path.rstrip("/") or "/"


def backend_routes() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in api.app.routes:
        p = getattr(r, "path", None)
        if not p or not p.startswith("/api/"):
            continue
        out.setdefault(_norm(p), set()).update(getattr(r, "methods", set()) or set())
    return out


def frontend_calls() -> list[tuple[str, str, str]]:
    """回 [(檔案, 路徑, 方法)]。"""
    calls: list[tuple[str, str, str]] = []
    for f in list(FRONTEND.glob("pages/**/*.vue")) + list(FRONTEND.glob("components/*.vue")) \
            + list(FRONTEND.glob("composables/*.ts")) + list(FRONTEND.glob("layouts/*.vue")):
        text = f.read_text(encoding="utf-8")
        for m in _CALL.finditer(text):
            path = m.group(2) or m.group(4)
            # 只看到「下一個 apiFetch 之前」為止：往後固定抓一段會撈到下一個呼叫的 method，
            # 把純 GET 誤判成 POST（實際踩到過）
            nxt = text.find("apiFetch", m.end())
            tail = text[m.end():nxt if nxt != -1 else len(text)][:400]
            mm = _METHOD.search(tail)
            method = (mm.group(1) if mm else "GET").upper()
            calls.append((f.name, _norm(path), method))
        for m in _HREF.finditer(text):
            # `${apiBase}/api/...` 有兩種：<a :href> 走瀏覽器 GET，或 fetch() 自己帶 method
            tail = text[m.end():m.end() + 300]
            mm = _METHOD.search(tail)
            calls.append((f.name, _norm(m.group(1)), (mm.group(1) if mm else "GET").upper()))
    return calls


def test_前端呼叫的路徑後端都有():
    routes = backend_routes()
    missing = [(f, p, mth) for f, p, mth in frontend_calls()
               if p not in ALLOW and p not in routes]
    assert not missing, "前端打了後端沒有的路徑（多半是端點改名漏改）：\n" + "\n".join(
        f"  {f}: {p}" for f, p, _ in missing)


def test_前端用的方法後端也收():
    routes = backend_routes()
    bad = []
    for f, p, mth in frontend_calls():
        if p in ALLOW or p not in routes:
            continue
        if mth not in routes[p]:
            bad.append(f"  {f}: {mth} {p}（後端只收 {'、'.join(sorted(routes[p]))}）")
    assert not bad, "前端用的 HTTP 方法後端不收（會回 405 Method Not Allowed）：\n" + "\n".join(bad)


def test_這支測試本身抓得到東西():
    """避免正則寫壞導致「零呼叫、永遠通過」——那比沒測還糟。"""
    calls = frontend_calls()
    assert len(calls) > 50, f"只解析到 {len(calls)} 個 API 呼叫，正則多半壞了"
    assert any(p == "/api/onboard-exempt" for _, p, _ in calls)
