"""守門：全站不准再新增「V1 深連結」，例外只能進白名單（Codex 1349 第 3 點／1353：N16、N26、N27 那一族）。

test_v2_no_v1_links.py 只掃「V2 頁面＋V2 用到的共用元件＋後端字串開頭」。這支補三個洞：
  1. 範圍改成「全部前端程式」（components/**、composables、utils、layouts、middleware、plugins、pages/v2），
     不再靠「V2 有沒有用到這個元件」的標籤比對——新元件第一次被 V2 用到前就該寫 V2 網址。
  2. 舊網址清單＝V1 頁面檔 ∪ 轉址表（v2LegacyMap 的 key）∪ PORTED。V1 頁面檔日後刪掉，
     舊網址照樣會從書籤／信件進來，守門不能因此變寬。
  3. 「網址前面接主機」的寫法也抓：前端 `${origin}/assets`、`base + '/assets'`；
     後端 f"{base}/changes…"、base_url + "/assets/"（原守門只看字串開頭，抓不到）。

例外只有一個入口：ALLOW（檔案 → 理由）。白名單裡的檔若已經沒有 V1 網址，也會紅（叫你把它拿掉），
避免白名單只增不減。舊版頁面本身（pages/*.vue、非 v2）不掃：那就是 V1，進來會被 middleware 轉走。
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
BE = ROOT / "APP" / "asset-module" / "backend"
MAP = FE / "utils" / "v2LegacyMap.ts"

#: 前端白名單：檔案（相對 frontend/）→ 為什麼可以有 V1 網址
ALLOW = {
    "utils/v2LegacyMap.ts": "舊網址→V2 轉址表本身（key 就是舊網址）",
    "utils/featureRoutes.ts": "功能開關用的「路徑→模組」對照，判斷舊網址屬哪個模組，不是連結",
    "composables/useNav.ts": "舊版外框（layouts/default.vue）的選單；點了會被 middleware 轉到 V2",
    "composables/useFavorites.ts": "舊版外框「我的最愛」預設值（舊選單 key），只在 V1 外框用",
}
#: 後端白名單：檔案（相對 backend/）→ 理由（與 test_v2_no_v1_links.BE_OK 一致）
BE_ALLOW = {
    "nav_prefs.py": "舊版外框「我的最愛」的預設（舊選單的路由 key），V1 網址進來會被 middleware 轉走",
}
#: 前端可以出現的 V1 前綴（純列印頁、登入、靜態工具）
FE_OK = ("/print/", "/login", "/patch-app/")


def _legacy_paths() -> list[str]:
    out = set()
    for p in (FE / "pages").rglob("*.vue"):
        rel = p.relative_to(FE / "pages").as_posix()
        if rel.startswith("v2/"):
            continue
        r = re.sub(r"/index$", "", "/" + rel[:-4]) or "/"
        out.add(r)
    m = MAP.read_text(encoding="utf-8")
    out |= set(re.findall(r"^\s*'(/[^']+)':", m, re.M))
    ported = re.search(r"export const PORTED = \[(.*?)\n\]", m, re.S).group(1)
    out |= {x.rstrip("/*") for x in re.findall(r"'(/[^']+)'", ported)}
    return sorted(out)


LEGACY = _legacy_paths()
HEADS = sorted({r.split("/")[1] for r in LEGACY if r != "/" and r.split("/")[1]}, key=len, reverse=True)
_H = "|".join(map(re.escape, HEADS))
_END = r"(?=[/?#\"'`$]|$)"
#: 引號開頭的站內網址（同 test_v2_no_v1_links._PATH，但用擴充後的舊網址清單）
_FE_QUOTED = re.compile(r"""["'`](/(?:%s))%s([^"'`\s]*)""" % (_H, _END))
#: 前面接主機：`${location.origin}/assets`、`${base}/changes`、origin + '/assets'
_FE_HOSTED = re.compile(r"""(?:\$\{[^}]*(?:origin|[bB]ase|[hH]ost)[^}]*\}|(?:origin|[bB]ase\w*|[hH]ost)\s*\+\s*["'`])(/(?:%s))%s([^"'`\s]*)""" % (_H, _END))
_BE_HEAD = re.compile(r"^/(?:%s)%s" % (_H, r"(?=[/?#]|$)"))
_HOSTY = re.compile(r"base|url|origin|host", re.I)


def _fe_files():
    for d in ("components", "composables", "utils", "layouts", "middleware", "plugins", "pages/v2"):
        p = FE / d
        if p.exists():
            yield from sorted(x for x in list(p.rglob("*.vue")) + list(p.rglob("*.ts")) if not x.name.endswith(".spec.ts"))
    for n in ("app.vue", "error.vue"):
        if (FE / n).exists():
            yield FE / n


def _fe_hits(text: str):
    for i, line in enumerate(text.splitlines(), 1):
        s = line.lstrip()
        if s.startswith(("//", "#", "*", "/*")) or "legacy=1" in line:
            continue
        for rx in (_FE_QUOTED, _FE_HOSTED):
            for m in rx.finditer(line):
                path = m.group(1) + m.group(2)
                if not path.startswith(FE_OK):
                    yield i, path


def _be_hits(src: str):
    """後端：字串開頭是舊網址（同原守門），以及「主機變數＋舊網址」的 f-string／字串相加。docstring 不算。"""
    tree = ast.parse(src)
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            docs.add(id(n.value))
        if isinstance(n, ast.JoinedStr):
            docs.update(id(v) for v in n.values)
    out = set()      # 同一個字串可能同時符合「開頭」與「主機＋相加」，只算一次
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs and _BE_HEAD.match(n.value):
            out.add((n.lineno, n.col_offset, n.value))
        elif isinstance(n, ast.JoinedStr) and n.values:
            v0 = n.values[0]
            if isinstance(v0, ast.Constant) and isinstance(v0.value, str) and _BE_HEAD.match(v0.value):
                out.add((n.lineno, n.col_offset, v0.value))
            elif (isinstance(v0, ast.FormattedValue) and len(n.values) > 1 and _HOSTY.search(ast.unparse(v0.value))
                  and isinstance(n.values[1], ast.Constant) and _BE_HEAD.match(str(n.values[1].value))):
                out.add((n.lineno, n.col_offset, str(n.values[1].value)))
        elif (isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add) and isinstance(n.right, ast.Constant)
              and isinstance(n.right.value, str) and _BE_HEAD.match(n.right.value) and _HOSTY.search(ast.unparse(n.left))):
            out.add((n.right.lineno, n.right.col_offset, n.right.value))
    for ln, _c, s in sorted(out):
        yield ln, s


def _be_files():
    for f in sorted(BE.rglob("*.py")):
        rel = f.relative_to(BE).as_posix()
        if rel.startswith("tests/") or ".venv" in rel or "/site-packages/" in rel:
            continue
        yield rel, f


def test_前端沒有新增V1深連結_白名單外():
    bad = []
    for f in _fe_files():
        rel = f.relative_to(FE).as_posix()
        if rel in ALLOW:
            continue
        for i, path in _fe_hits(f.read_text(encoding="utf-8")):
            bad.append(f"{rel}:{i} → {path}")
    assert not bad, ("前端出現 V1 網址（改寫成 V2 網址並保留日期／分類／篩選參數；"
                     "V2 真的沒有對應頁才加 ?legacy=1 並標「舊版」，或附理由加進 ALLOW）：\n" + "\n".join(bad))


def test_後端沒有新增V1深連結_白名單外():
    bad = []
    for rel, f in _be_files():
        if Path(rel).name in BE_ALLOW:
            continue
        for i, s in _be_hits(f.read_text(encoding="utf-8")):
            bad.append(f"{rel}:{i} → {s[:60]}")
    assert not bad, "後端組出 V1 網址（信件／通知／指路連結一律 V2；例外附理由加進 BE_ALLOW）：\n" + "\n".join(bad)


def test_白名單沒有過期():
    """白名單裡的檔已經沒有 V1 網址＝該拿掉；檔案不見了也要拿掉。"""
    stale = []
    for rel in ALLOW:
        f = FE / rel
        if not f.exists() or not list(_fe_hits(f.read_text(encoding="utf-8"))):
            stale.append(rel)
    for name in BE_ALLOW:
        hits = [h for rel, f in _be_files() if Path(rel).name == name for h in _be_hits(f.read_text(encoding="utf-8"))]
        if not hits:
            stale.append(name)
    assert not stale, f"白名單過期（已經沒有 V1 網址或檔案不在了），請拿掉：{stale}"


def test_舊網址清單不靠V1頁面檔():
    """V1 頁面檔刪掉後，轉址表裡的舊網址仍要在守門清單裡（書籤、舊信件還會連進來）。"""
    m = MAP.read_text(encoding="utf-8")
    for k in re.findall(r"^\s*'(/[^']+)':", m, re.M):
        assert k in LEGACY, k
    for h in ("changes", "contacts", "account-matrix", "accounts", "machine-key", "activity"):
        assert h in HEADS, h


def test_掃描器抓得到():
    """防呆：典型寫法要抓得到，/v2、/api 與非站內字串不能誤抓（不然上面永遠綠或永遠紅）。"""
    hits = ['<NuxtLink to="/contacts?tab=todo&api=X">', "to: `/account-matrix?col=uid0&val=yes`",
            "const u = `${location.origin}/changes?date=${d}&tab=storage_low`", "base + '/assets/' + s",
            "navigateTo('/machine-key')"]
    for s in hits:
        assert list(_fe_hits(s)), s
    for s in ['to="/v2/settings?st=contacts"', "'/api/contacts/card'", "`${origin}/v2/changes`",
              "`/api/q/${k}/reset`", "x = '/assets-x'", "// 舊 /contacts 已轉", 'href="/contacts?legacy=1"']:
        assert not list(_fe_hits(s)), s
    py = ('def f(base_url, day, k, cfg):\n    """說明裡提到 /contacts 不算"""\n'
          '    a = f"{base_url}/changes?date={day}"\n    b = cfg["base_url"] + "/assets/" + k\n'
          '    c = f"/api/q/{k}/reset"\n    d = f"{base_url}/v2/changes"\n    e = "/account-matrix?col=x"\n'
          '    g = f"{k}/reset"\n    return a, b, c, d, e, g\n')
    found = sorted(s for _, s in _be_hits(py))
    assert found == ["/account-matrix?col=x", "/assets/", "/changes?date="], found
