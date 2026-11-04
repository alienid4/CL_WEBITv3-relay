"""V2 不可以把人帶回 V1（2.042.20，使用者：「以後都不要再看到 V1 頁面」；docs/V2移植清單.md X1/X2/X5）。

守四件事：
  1. V2 頁面／V2 元件／V2 外框裡，不准出現 V1 站內網址（NuxtLink to、navigateTo、href、router.push、樣板字串都算）。
     例外：/print/*（layout:false 純列印頁）、/login、/patch-app/（靜態工具），
     以及同一行帶 legacy=1 的（V2 還沒移植、明確標「舊版」的功能，middleware 不轉）。
  2. 後端產生給畫面／信件用的連結（to／link／page／more_to／摘要信 URL）不准是 V1 網址。
  3. 每一個 V1 路由，要嘛在 utils/v2LegacyMap.ts 登記了 V2 對應，要嘛在 NEVER_REDIRECT（刻意不轉）。
  4. middleware 一律轉（不再限「從 /v2 出發」），legacy=1 才放行。
"""
import ast
import re

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
BE = ROOT / "APP" / "asset-module" / "backend"


def _v1_routes() -> list[str]:
    out = set()
    for p in (FE / "pages").rglob("*.vue"):
        rel = p.relative_to(FE / "pages").as_posix()
        if rel.startswith("v2/"):
            continue
        r = "/" + rel[:-4]
        r = re.sub(r"/index$", "", r) or "/"
        out.add(r)
    return sorted(out)


V1 = _v1_routes()
#: 路徑第一段（/assets、/reports、/print…），用來認「這串字是不是 V1 網址」
V1_HEADS = sorted({r.split("/")[1] for r in V1 if r != "/"}, key=len, reverse=True)
_PATH = re.compile(r"""["'`](/(?:%s))(?=[/?"'`$#]|$)([^"'`\s]*)""" % "|".join(map(re.escape, V1_HEADS)))
#: 前端允許的例外（前綴）
FE_OK = ("/print/", "/login", "/patch-app/")
#: 後端允許的例外：檔案 → 原因
BE_OK = {
    "nav_prefs.py": "舊版外框「我的最愛」的預設（舊選單的路由 key），只在 V1 外框用；V1 網址進來會被 middleware 轉走",
}


def _fe_files():
    yield from (FE / "pages" / "v2").rglob("*.vue")
    yield from (FE / "components" / "v2").rglob("*.vue")
    yield FE / "layouts" / "v2.vue"
    yield FE / "composables" / "useNavV2.ts"


def _kebab(n: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"-\1", n).lower()


def _shared_files():
    """V2 頁面／元件直接用到的共用元件（components/*.vue，例：機櫃圖、系統圖、聯絡卡）＋它們用的連結 composable。
    這些在 V2 裡被點到時，舊網址雖會被 middleware 轉走，但程式裡就該寫 V2 網址（X3／N21／N26／N27）。"""
    src = "\n".join(f.read_text(encoding="utf-8") for f in _fe_files())
    for p in sorted((FE / "components").glob("*.vue")):
        n = p.stem
        if re.search(r"<%s[\s/>]" % n, src) or re.search(r"<%s[\s/>]" % _kebab(n), src):
            yield p
    yield FE / "components" / "AccountDashboard.vue"      # 嵌在 7.2 合規矩陣（舊頁嵌入）裡
    yield FE / "composables" / "accountCells.ts"          # AccountDashboard 下鑽連結的來源


def _scan(text: str):
    for i, line in enumerate(text.splitlines(), 1):
        s = line.lstrip()
        if s.startswith("//") or s.startswith("#") or s.startswith("*"):
            continue
        for m in _PATH.finditer(line):
            yield i, line, m.group(1) + m.group(2)


def test_V2頁面與元件沒有V1網址():
    bad = []
    for f in _fe_files():
        for i, line, path in _scan(f.read_text(encoding="utf-8")):
            if path.startswith(FE_OK) or "legacy=1" in line:
                continue
            bad.append(f"{f.relative_to(FE).as_posix()}:{i} → {path}")
    assert not bad, "V2 頁面／元件連到 V1 網址（要嘛改 V2 網址，要嘛 V2 沒有的功能才加 ?legacy=1 並標「舊版」）：\n" + "\n".join(bad)


def test_V2用到的共用元件沒有V1網址():
    bad = []
    files = list(_shared_files())
    assert any(f.name == "RackElevation.vue" for f in files), "抓不到 V2 用的共用元件（標籤比對壞了？）"
    for f in files:
        for i, line, path in _scan(f.read_text(encoding="utf-8")):
            if path.startswith(FE_OK) or "legacy=1" in line:
                continue
            bad.append(f"{f.relative_to(FE).as_posix()}:{i} → {path}")
    assert not bad, "V2 用到的共用元件連到 V1 網址（改成 V2 網址；轉址表會帶參數的也要直接寫 V2）：\n" + "\n".join(bad)


def test_功能關掉送V2設定且V2頁也吃開關():
    """N18／N28：停用導向不准再送舊 /settings；V2 路徑要有開關對照。"""
    mw = (FE / "middleware" / "features.global.ts").read_text(encoding="utf-8")
    fr = (FE / "utils" / "featureRoutes.ts").read_text(encoding="utf-8")
    code = "\n".join(l for l in (mw + fr).splitlines() if not l.lstrip().startswith("//"))
    assert "/settings?disabled" not in code
    assert "disabledTarget" in mw and "moduleForRoute" in mw
    assert "V2_MODULE_MAP" in fr and "'/v2/hosts'" in fr and "path: '/v2/settings'" in fr


_HEAD = re.compile(r"^/(?:%s)(?=[/?#]|$)" % "|".join(map(re.escape, V1_HEADS)))


def _py_strings(src: str):
    """程式裡真的會被用到的字串（含 f-string 的開頭那段）；docstring 不算（那是說明文字）。"""
    tree = ast.parse(src)
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            docs.add(id(n.value))
        if isinstance(n, ast.JoinedStr):            # f-string 中段（例 f"/api/x/{k}/reset" 的 "/reset"）不是開頭，不算
            docs.update(id(v) for v in n.values)
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            yield n.lineno, n.value
        elif isinstance(n, ast.JoinedStr) and n.values and isinstance(n.values[0], ast.Constant):
            yield n.lineno, str(n.values[0].value)


def test_後端產生的連結沒有V1網址():
    bad = []
    for f in BE.rglob("*.py"):
        rel = f.relative_to(BE).as_posix()
        if rel.startswith("tests/") or f.name in BE_OK or ".venv" in rel:
            continue
        for i, s in _py_strings(f.read_text(encoding="utf-8")):
            if _HEAD.match(s):
                bad.append(f"{rel}:{i} → {s[:60]}")
    assert not bad, "後端組出 V1 網址（信件、搜尋結果、指路連結都要給 V2 網址）：\n" + "\n".join(bad)


def test_每個V1路由都有V2對應或刻意不轉():
    m = (FE / "utils" / "v2LegacyMap.ts").read_text(encoding="utf-8")
    keys = set(re.findall(r"^\s*'(/[^']+)':", m, re.M))
    never = re.search(r"NEVER_REDIRECT = \[([^\]]*)\]", m).group(1)
    never_list = re.findall(r"'([^']+)'", never)
    dynamic = {"/golive/[serial]", "/incident/[id]", "/blast/checklist/[id]", "/assets/[serial]"}
    missing = [r for r in V1 if r != "/" and r not in keys and r not in dynamic
               and not any(r == n.rstrip("/") or r.startswith(n if n.endswith("/") else n + "/") for n in never_list)]
    assert not missing, f"這些 V1 網址沒有 V2 對應、也沒登記成刻意不轉：{missing}"
    for d in dynamic:      # 動態路徑在 v2LegacyTarget 裡用正規式處理
        head = d.split("/[")[0].replace("/", r"\/")
        assert head in m, d


def test_middleware一律轉_legacy才放行():
    w = (FE / "middleware" / "v2-legacy.global.ts").read_text(encoding="utf-8")
    code = "\n".join(l for l in w.splitlines() if not l.lstrip().startswith("//"))
    assert "from.path.startsWith('/v2')" not in code           # 不再限「從 V2 出發」
    assert "LEGACY_BYPASS" in code and "v2LegacyTarget" in code and "navigateTo" in code
    m = (FE / "utils" / "v2LegacyMap.ts").read_text(encoding="utf-8")
    assert "export const LEGACY_BYPASS = 'legacy'" in m


def _section_rows(md: str, head: str) -> list[tuple[str, str]]:
    """docs/V2移植清單.md 某一節（例 '## C'）裡還沒打勾的項目 → [(V61, '/assets'), …]。"""
    m = re.search(rf"^{re.escape(head)}.*?$(.*?)(?=^## )", md, re.M | re.S)
    assert m, head
    return re.findall(r"^- \[ \] \*\*(V\d+)\*\* `(/[^`]+)`", m.group(1), re.M)


def test_C和D類每一項在V2都有舊版入口():
    """使用者：「全部的功能我都要保留」。舊網址一律轉 V2 之後，V2 還缺的功能只能從 V2 頁上帶 ?legacy=1 的連結進去；
    清單上還沒移植完（沒打勾）的 C／D 項目，每一項都要有至少一條這種入口，不然那個功能就等於消失了。"""
    f = ROOT / "docs" / "V2移植清單.md"
    if not f.exists():
        pytest.skip("docs/ 不進公開中繼（決策 G1），中繼產出物上沒有這份清單")
    md = f.read_text(encoding="utf-8")
    rows = _section_rows(md, "## C") + _section_rows(md, "## D")
    assert len(rows) >= 2, "清單格式變了，抓不到 C／D 項目"
    src = "\n".join(f.read_text(encoding="utf-8") for f in _fe_files())
    missing = []
    for no, path in rows:
        head = path.split("/[")[0]                 # /incident/[id] → /incident/ 開頭、後面接任意編號
        pat = re.escape(head) + (r"/[^'\"`?\s]+" if "/[" in path else "") + r"\?legacy=1"
        if not re.search(r"""['"`]""" + pat, src):
            missing.append(f"{no} {path}")
    assert not missing, "這些舊版功能在 V2 找不到入口（要在 V2 對應頁加 <V2LegacyLinks> 帶 ?legacy=1）：\n" + "\n".join(missing)


def test_舊版入口不會被轉走():
    m = (FE / "utils" / "v2LegacyMap.ts").read_text(encoding="utf-8")
    w = (FE / "middleware" / "v2-legacy.global.ts").read_text(encoding="utf-8")
    assert "export const LEGACY_BYPASS = 'legacy'" in m and "to.query[LEGACY_BYPASS] === '1'" in w


def test_掃描器抓得到():
    """防呆：掃描規則要抓得到典型寫法，也不能把 /v2、/api 誤抓（不然上面幾支永遠綠或永遠紅）。"""
    hits = ["<NuxtLink to=\"/topology\">", ":to=\"`/datacenter?room=${r}`\"", "navigateTo({ path: '/adopt' })",
            "link: (r) => `/assets/${r.serial}`", "\"to\": f\"/changes?date={day}\"", "'page': '/san'"]
    for s in hits:
        assert list(_scan(s)), s
    for s in ["to=\"/v2/hosts\"", "'/api/assets'", "`/v2/changes?tab=daily`", "'/assets-x'"]:
        assert not [p for _, _, p in _scan(s) if not p.startswith("/api/")], s
    py = ('def f(day, k):\n    """說明裡提到 /blast 不算"""\n'
          '    return {"to": f"/changes?date={day}", "page": "/san", "ok": "/v2/collect", "api": "/api/san",\n'
          '            "p": f"/api/q/{k}/reset"}\n')
    found = sorted(s for _, s in _py_strings(py) if _HEAD.match(s))
    assert found == ["/changes?date=", "/san"], found
