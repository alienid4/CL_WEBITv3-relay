"""守門：<script setup> 頂層 `await fn()` 之前，fn 會寫的 ref 必須已宣告。

起因（2.070.1）：1.1 今日概況 `await load()` 在第 48 行，load() 成功時寫 `nowDay.value`，
但 `const nowDay = ref(...)` 宣告在第 256 行 → 瀏覽器端「Cannot access 'de' before initialization」，
畫面紅字「今日資料載入失敗」（2.067.0 起就壞，SSR 時沒噴所以測試沒抓到）。

做法：靜態掃 pages/ 與 components/ 的 .vue：找頂層 `await name(...)`，取出 `function name` 的本體，
列出本體裡 `X.value =` 的 X；凡是 X 在同檔以 `const X =` 宣告、且宣告行在 await 那行之後 → 紅。
"""
import re
from pathlib import Path

FRONT = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "frontend"


def _script(text: str) -> tuple[str, int]:
    m = re.search(r"<script setup[^>]*>", text)
    if not m:
        return "", 0
    end = text.find("</script>", m.end())
    return text[m.end():end], text[:m.end()].count("\n")


def _func_body(src: str, name: str) -> str:
    m = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", src)
    if not m:
        return ""
    i, depth = m.end(), 1
    while i < len(src) and depth:
        c = src[i]
        depth += c == "{"
        depth -= c == "}"
        i += 1
    return src[m.end():i]


def _violations(text: str) -> list[str]:
    src, _ = _script(text)
    lines = src.split("\n")
    out = []
    for ln, line in enumerate(lines):
        m = re.match(r"^await\s+([A-Za-z_$][\w$]*)\s*\(", line)
        if not m:
            continue
        body = _func_body(src, m.group(1))
        for var in sorted(set(re.findall(r"\b([A-Za-z_$][\w$]*)\.value\s*=(?!=)", body))):
            for dl, d in enumerate(lines):
                if re.match(r"^(?:const|let)\s+" + re.escape(var) + r"\b", d):
                    if dl > ln:
                        out.append(f"{m.group(1)}() 寫 {var}，但 {var} 宣告在 await 之後")
                    break
    return out


def test_self_check_catches_pattern():
    bad = "<script setup>\nconst a = ref(1)\nasync function load(){ a.value = 2; b.value = 3 }\nawait load()\nconst b = ref(0)\n</script>"
    assert _violations(bad) == ["load() 寫 b，但 b 宣告在 await 之後"]


def test_no_ref_used_before_declared_by_toplevel_await():
    bad = {}
    for p in list((FRONT / "pages").rglob("*.vue")) + list((FRONT / "components").rglob("*.vue")):
        v = _violations(p.read_text(encoding="utf-8"))
        if v:
            bad[str(p.relative_to(FRONT))] = v
    assert not bad, f"頂層 await 前未宣告的 ref（瀏覽器端會噴 TDZ）：{bad}"
