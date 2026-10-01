"""會直接顯示給人看的字串，不准用 markdown 寫法。

2026-09-23 實際發生：健檢頁的「摘要」欄印出「這兩項**沒有查到**」——
後端用 `**粗體**` 寫，前端是純文字渲染，星號就原樣印給值班看。

前端已經在顯示層剝掉了，但那是治標：源頭還是會一直產生 markdown。
這條擋在源頭，本機閘門就會紅，不會等到有人在畫面上看到星號才發現。

**為什麼不乾脆讓前端渲染 markdown**：`notes` 內含主機名與 SSH 錯誤原文，
用 `v-html` 等於替後端字串開一個注入口。為了一點排版在金融業主機的工具上
開洞，一次都不能發生。所以方向是「後端不要產生 markdown」。

掃描範圍刻意很窄——**只掃真的會被畫面直接顯示的那幾種**：
  * `xxx["notes"].append(...)` / `notes.append(...)`
  * 字典裡 `"note"` / `"why"` 這兩個鍵的值

docstring 與註解不在此限：那是寫給改程式的人看的，用 markdown 強調沒有問題。
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"

#: **反向白名單：預設「全部都算給人看的」，只列出不是的。**
#:
#: 2026-09-23 這道門漏過兩次：第一次是只掃 dict 鍵、漏掉「先指派給同名變數再放進
#: dict」的寫法（parse_failed_detail 的 verdict）；第二次是 USER_KEYS 裡沒有 `text`
#: （AIX session 新寫的顯示欄位就叫 text，10 處違規有一半沒被抓到）。
#: **同一道門，兩次都是「漏了一個鍵」。**
#:
#: 正向白名單的失敗方向是**危險的**：新增一個顯示欄位、忘了回來加名字，
#: 這道門就**靜靜放行**——它平常都綠，所以沒有人會去懷疑它。
#: 反向之後失敗方向變成安全的：忘了列進來，最多是多擋一個不該擋的，
#: **會紅、會被看到、會有人來處理**。
#:
#: 這裡面的東西**不是寫給人看的散文**，所以 markdown 規則不適用：
NOT_USER_FACING = {
    # dict 鍵：給人複製去跑的指令字串（照原樣輸出，不是說明文字）
    "inspect", "act", "command", "cmd",
}

#: 模組常數：腳本／sudoers／SQL／prompt，或**外部指令真的會印出來的字面值**。
#: `_LASTLOG_NEVER` 是最好的例子——`lastlog` 的輸出就是 `**Never logged in**`，
#: 那兩個星號是**別人的資料**，不是我們的排版，拿掉會讓比對失效。
NOT_USER_FACING_NAMES = {
    "ACCOUNT_HELPER_SCRIPT", "SUDO_RULES", "RUN_SQL", "PROMPT", "_LASTLOG_NEVER",
}


def _strings_in(node: ast.AST) -> list[str]:
    """把一個運算式裡的字串常值全撈出來（含 f-string 的固定段與相加的字串）。"""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
    return out


def _is_notes_append(node: ast.Call) -> bool:
    f = node.func
    if not isinstance(f, ast.Attribute) or f.attr != "append":
        return False
    target = f.value
    if isinstance(target, ast.Name) and target.id == "notes":
        return True
    # base["notes"].append(...) / r["notes"].append(...)
    return (isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == "notes")


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and _is_notes_append(n):
            for arg in n.args:
                for s in _strings_in(arg):
                    if "**" in s:
                        bad.append(f"{path.name}:{n.lineno} notes：{s[:50]}")
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if (isinstance(k, ast.Constant) and isinstance(k.value, str)
                        and k.value not in NOT_USER_FACING):
                    for s in _strings_in(v):
                        if "**" in s:
                            bad.append(f"{path.name}:{k.lineno} {k.value}：{s[:50]}")
        # 先指派給同名區域變數、再放進 dict 的寫法（`verdict = "..."` → `"verdict": verdict`）。
        # 2026-09-23：只看 dict 鍵會漏掉這一種，而 parse_failed_detail 正是這樣寫的——
        # 畫面上真的出現「這是**批次型**工作」，這道關卡卻是綠的。
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if (isinstance(t, ast.Name) and t.id not in NOT_USER_FACING
                        and t.id not in NOT_USER_FACING_NAMES):
                    for s in _strings_in(n.value):
                        if "**" in s:
                            bad.append(f"{path.name}:{n.lineno} {t.id}=：{s[:50]}")
    return bad


def test_顯示給人看的字串不准用markdown寫法():
    bad = []
    for f in sorted(BACKEND.glob("*.py")):
        bad += _violations(f)
    assert not bad, (
        "這些字串會**原樣**印在畫面上，星號不會變粗體，值班看到的就是一堆 *：\n  "
        + "\n  ".join(bad)
        + "\n\n改法：把 ** 拿掉，用「」或直接寫清楚就好。"
          "要強調就在前端的 template 裡用 <b>，不要在後端字串裡塞 markdown。\n"
          "如果這個字串**不是給人看的**（指令、腳本、SQL、或外部指令真的會印出來的\n"
          "字面值），把它的鍵名／變數名加進 NOT_USER_FACING(_NAMES) 並寫明理由——\n"
          "但先想一遍：它真的不會出現在畫面或信件上嗎？")
