"""前端計時器守門（2.069.1）：計時器沒在離開頁面時清掉，會一直在背景跑，App 越用越卡。

規則（都是靜態掃描，不執行前端）：
1. 用到 setInterval 的元件／頁面／composable，必須同時有 clearInterval 與卸載鉤子（onBeforeUnmount／onUnmounted／onScopeDispose）。
2. 輪詢型 setTimeout（函式裡又呼叫 setTimeout 排下一輪，或 setTimeout 的回呼是同一個函式）必須有 clearTimeout 與卸載鉤子。
   一次性的 setTimeout（例如提示 3 秒後消失）不受限。
新增輪詢時照這條寫：把計時器存進變數，離開頁面時清掉。
"""
import re
from pathlib import Path

FRONT = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"
DIRS = ("components", "pages", "composables", "layouts")
UNMOUNT = re.compile(r"onBeforeUnmount|onUnmounted|onScopeDispose|tryOnScopeDispose")


def _files():
    for d in DIRS:
        for ext in ("*.vue", "*.ts"):
            yield from sorted((FRONT / d).rglob(ext))


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _polling_timeouts(s: str) -> list[str]:
    """回傳疑似遞迴輪詢的函式名：function f(){ ... setTimeout(f ...) } 或 setTimeout(() => f(), ...) 於 f 內。"""
    hits = []
    for m in re.finditer(r"(?:async\s+)?function\s+(\w+)\s*\([^)]*\)\s*\{(.*?)\n\}", s, flags=re.S):
        name, body = m.group(1), m.group(2)
        if re.search(r"\bsetTimeout\s*\(", body) and re.search(r"\b%s\b" % re.escape(name), re.sub(r"function\s+%s" % re.escape(name), "", body)):
            hits.append(name)
    for m in re.finditer(r"(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{(.*?)\n\}", s, flags=re.S):
        name, body = m.group(1), m.group(2)
        if re.search(r"\bsetTimeout\s*\(", body) and re.search(r"\b%s\s*\(" % re.escape(name), body):
            hits.append(name)
    return hits


def test_setInterval_一定清除且掛卸載鉤子():
    bad = []
    for p in _files():
        s = _src(p)
        if re.search(r"\bsetInterval\s*\(", s):
            if not re.search(r"\bclearInterval\s*\(", s) or not UNMOUNT.search(s):
                bad.append(str(p.relative_to(FRONT)))
    assert not bad, "這些檔案用了 setInterval，但沒有 clearInterval 或離開頁面的卸載鉤子：%s" % bad


def test_輪詢型setTimeout_一定清除且掛卸載鉤子():
    bad = []
    for p in _files():
        s = _src(p)
        poll = _polling_timeouts(s)
        if poll and (not re.search(r"\bclearTimeout\s*\(", s) or not UNMOUNT.search(s)):
            bad.append("%s（%s）" % (p.relative_to(FRONT), ",".join(poll)))
    assert not bad, "這些檔案用 setTimeout 做輪詢，但沒有 clearTimeout 或卸載鉤子：%s" % bad


def test_守門本身會抓到錯誤寫法():
    """突變驗證：壞的寫法一定被判違規、好的寫法一定放行。"""
    bad_poll = "async function tick() {\n  await load()\n  setTimeout(tick, 5000)\n}\n"
    good_once = "function hide() {\n  setTimeout(() => { show.value = false }, 3000)\n}\n"
    assert _polling_timeouts(bad_poll) == ["tick"]
    assert _polling_timeouts(good_once) == []
