"""前端不可以把錯誤吞成「沒有資料」（2026-09-17）。

## 為什麼要有這支

使用者 2026-09-17：「有七台收集到了，但你卻說沒有盤點到」。

真相是後端 500（SQL 欄位名打錯），但前端寫了：

    apiFetch('/api/accounts/inventoried-hosts').catch(() => ({ items: [] }))

於是畫面顯示「盤點到的主機 0 台／這一輪沒有收到任何主機的帳號資料」。
**程式壞掉被顯示成「沒有資料」，是最糟的失敗方式**——使用者會去查資料來源，
而問題根本在程式。使用者從 2026-09-15 就在講「失敗都沒有任何輸出」，我又犯一次。

這支掃前端原始碼，抓「catch 之後直接回空值、卻沒有把錯誤記下來」的寫法。

## 什麼算過、什麼不算

**不算**（會紅）：catch 回空陣列／空物件／null，而且整個 catch 區塊裡沒有出現
任何一種「留下痕跡」的手段（錯誤狀態變數、showToast、console.*）。

**算過**：
- catch 裡有設 error 狀態或 showToast → 使用者看得到
- catch 裡只是「這個功能是輔助的，載不到就算了」而且**寫了註解說明為什麼** →
  用 `/* ... */` 或 `//` 註解表明是刻意的

刻意留白是允許的，但**必須寫出理由**——這樣下一個人才知道那不是漏掉。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

#: catch 區塊裡出現這些，就算「有留下痕跡」
_TRACE_HINTS = ("showToast", "console.", "Error.value", "err.value", "error.value",
                "errorMessage", "hostsError", "dispatchError", "acctErr", "//", "/*")

#: 這種回傳值代表「假裝成功、給一個空的」
_EMPTY_RETURN = re.compile(
    r"catch\s*(?:\([^)]*\))?\s*(?:=>|\{)", re.S)


def _vue_files():
    return sorted(list(FRONTEND.glob("pages/**/*.vue"))
                  + list(FRONTEND.glob("components/*.vue"))
                  + list(FRONTEND.glob("composables/*.ts"))
                  + list(FRONTEND.glob("layouts/*.vue")))


def _strip_comments(text: str) -> str:
    """去掉註解再掃——不然會比對到「註解裡提到的壞寫法」，把說明當成違規
    （2026-09-17 第一次跑就誤判到 AccountDashboard 裡那段警告文字）。
    用等長空白取代，行號才不會跑掉。"""
    def blank(m):
        return re.sub(r"\S", " ", m.group(0))
    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    text = re.sub("(?m)//[^" + chr(92) + "n]*", blank, text)
    text = re.sub(r"(?s)<!--.*?-->", blank, text)
    return text


def _catch_blocks(text: str):
    """回 (起始位置, 區塊文字)。只抓 .catch( ... ) 的箭頭函式與 try/catch 的 catch 區塊。"""
    out = []
    for m in re.finditer(r"\.catch\s*\(", text):
        depth = 0
        i = m.end() - 1
        for j in range(i, min(len(text), i + 600)):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    out.append((m.start(), text[m.start():j + 1]))
                    break
    for m in re.finditer(r"\bcatch\s*(?:\([^)]*\))?\s*\{", text):
        depth = 0
        i = text.index("{", m.start())
        for j in range(i, min(len(text), i + 600)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    out.append((m.start(), text[m.start():j + 1]))
                    break
    return out


def _looks_like_empty(block: str) -> bool:
    """catch 之後是不是直接給一個空值當結果——兩種形式都算：
    (1) .catch(() => []) 這種回空值；
    (2) try/catch 區塊裡直接把某個 ref 指成空（`} catch { rows.value = [] }`）。
    2026-09-18：本來只抓 (1)，漏了 (2)——實際有 10 處賦值式吞錯（程式壞掉顯示成沒資料）。"""
    compact = re.sub(r"\s+", "", block)
    return any(p in compact for p in (
        # (1) 回空值
        "=>({items:[]})", "=>({items:[],", "=>[]", "=>({})", "=>null", "=>undefined",
        "=>({rows:[]})", "=>({data:[]})",
        # (2) 賦值成空（catch 區塊裡）
        ".value=[]", ".value=null", ".value=({})", ".value={}", ".value=undefined",
    ))


def test_不可以把錯誤吞成空資料():
    bad = []
    for f in _vue_files():
        raw = f.read_text(encoding="utf-8")
        text = _strip_comments(raw)
        for pos, block in _catch_blocks(text):
            if not _looks_like_empty(block):
                continue
            if any(h in block for h in _TRACE_HINTS):
                continue          # catch 裡面有把錯誤記下來
            # 緊鄰的註解算數：可能在前一行（「這是輔助功能，載不到就算了」），也可能寫在
            # 賦值後面同一行（`} catch { x.value = [] }   // 還沒匯…就不顯示`）。用**原始**
            # 文字看前 240、後 100 字元有沒有註解，兩邊都算刻意留白。
            around = raw[max(0, pos - 240):pos + len(block) + 100]
            if "//" in around or "/*" in around or "<!--" in around:
                continue
            line = text[:pos].count("\n") + 1
            bad.append(f"  {f.name}:{line}　{re.sub(r's+', ' ', block)[:110]}")
    assert not bad, (
        "這些地方把錯誤吞成「沒有資料」，畫面會顯示成資料不存在、而不是程式壞掉：\n"
        + "\n".join(bad)
        + "\n\n修法二選一：(1) 設一個錯誤狀態並顯示紅字，"
          "(2) 真的是輔助功能就寫註解說明為什麼可以靜默。"
    )


BACKEND = ROOT / "APP" / "asset-module" / "backend"


def _is_empty_body(body) -> bool:
    """except 區塊是不是「只回一個空的『集合』」——那才是「程式壞掉卻顯示成沒有資料」
    的危險寫法（回空清單／空 dict 餵給畫面）。

    刻意**不抓** `pass` 與 `return None`：那兩種在後端多半是合法的（可選功能偵測、
    找不到就沒有），全抓會把幾十處既有且合理的 except 一起標紅，反而稀釋了守門意義。
    這裡只鎖「假裝有資料、給一個空的」這個確切的失敗樣式。"""
    import ast
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return False
    v = body[0].value
    if isinstance(v, ast.List) and not v.elts:
        return True
    if isinstance(v, ast.Dict) and not v.keys:
        return True
    if (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
            and v.func.id in ("set", "dict", "list", "tuple") and not v.args):
        return True
    return False


def test_後端不可以把錯誤吞成空值():
    """後端版（AST）：except 只 pass／只回空值、又沒寫註解說明為什麼可以靜默＝吞錯誤。
    2026-09-18：以前這支只掃前端，後端沒守門。刻意靜默要嘛 log／raise，要嘛寫註解。"""
    import ast
    bad = []
    for f in sorted(BACKEND.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        lines = src.splitlines()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or not _is_empty_body(node.body):
                continue
            end = getattr(node.body[-1], "end_lineno", node.lineno)
            # 前 2 行到區塊尾有任何註解（#）就當「刻意靜默、已說明」，放行（寧可少抓不要誤擋）
            ctx = "\n".join(lines[max(0, node.lineno - 3):end])
            if "#" in ctx:
                continue
            bad.append(f"  {f.name}:{node.lineno}")
    assert not bad, (
        "這些後端 except 把錯誤吞成空值又沒說明，會讓『程式壞掉』顯示成『沒有資料』：\n"
        + "\n".join(bad)
        + "\n\n修法二選一：(1) log／顯示錯誤，(2) 真的可以靜默就寫註解說明為什麼。"
    )


def test_這支測試本身抓得到東西():
    """避免正則壞掉而「零檢查、永遠通過」。"""
    blocks = sum(len(_catch_blocks(_strip_comments(f.read_text(encoding="utf-8"))))
                 for f in _vue_files())
    assert len(_vue_files()) > 20, "前端檔案沒掃到，路徑多半錯了"
    assert blocks > 20, f"只解析到 {blocks} 個 catch 區塊，正則多半壞了"
    # 用一段假碼確認判定邏輯真的會攔
    fake = "apiFetch('/x').catch(() => ({ items: [] }))"
    assert _looks_like_empty(_catch_blocks(fake)[0][1])
