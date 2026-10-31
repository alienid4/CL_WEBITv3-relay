"""稽核版 HTML 不可以漏出操作欄。

使用者 2026-10-13：「分兩個 HTML 給我，一個給稽核，一個自己用，
稽核的我不想接露太多」，並確認查核指令也不要留。

## 設計

同一次執行、同一段程式碼（`gen_html`）產兩份，用 `HTML_MODE` 分流：

* `full`  —— 13 欄，自己用
* `audit` —— 省略「查核指令」「修正指令」「生效方式」，其餘相同

**不是事後另存**：靠人記得刪，總有一次會忘記。
**不是複製一份程式改**：複製的那份一定會漂掉，而先壞的會是沒人每天在看的那一份。

## 留著目前值是刻意的

目前值是「做過檢查」的佐證。藏掉反而會被追問「你憑什麼說 Compliant」，
那比揭露太多更麻煩。要遮的是內部作法，不是證據。

## 這道守門為什麼存在

第一版把表格的三欄拿掉了，**頁尾的「逐條工作單」卻照樣印出每一條的
查核指令與修正指令**——整欄從表格移掉、卻從頁尾漏出去，等於沒遮。
那是產出後逐行比對才發現的，不是想出來的。

所以這裡守的不是「有沒有寫 if」，是**每一個會輸出指令的地方都被分流涵蓋**。
"""
import io
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "APP" / "asset-module" / "scripts"


def _script() -> Path:
    found = sorted(SCRIPTS.glob("fcb_aixV???.ksh"))
    assert len(found) == 1, f"scripts/ 底下應該只有一支 fcb_aixV###.ksh，找到 {found}"
    return found[0]


def _text() -> str:
    return io.open(_script(), encoding="utf-8", newline="").read()


def test_兩份報告由同一段程式碼產出():
    """複製一份改會漂掉，所以兩份一定要走同一個函式。"""
    t = _text()
    assert "gen_html() {" in t, "找不到 gen_html——兩份 HTML 的共用產生函式"
    calls = re.findall(r'HTML_MODE=(\w+)\s*;?\s*gen_html', t)
    assert sorted(calls) == ["audit", "full"], (
        f"gen_html 應該剛好被呼叫兩次（full 與 audit），實際是 {calls}"
    )


def test_稽核版檔名看得出來是稽核版():
    """兩個檔會躺在同一個目錄，名字分不出來就一定會寄錯那一份。"""
    t = _text()
    assert re.search(r'audit_file="[^"]*_audit\.html"', t), (
        "稽核版的檔名要以 _audit.html 結尾"
    )


def test_三個操作欄在稽核版不可以出現():
    """表頭、欄寬、逐列內容三個地方都要分流，少一個就會版面錯位或漏資料。"""
    t = _text()

    # 表頭
    thead = re.search(
        r'if \[ "\$HTML_MODE" != audit \]; then\s*\n(.*?)\nfi', t, re.S
    )
    assert thead, "表頭那三欄沒有被 HTML_MODE 分流包住"
    assert "查核指令" in thead.group(1)
    assert "修正指令" in thead.group(1)
    assert "生效方式" in thead.group(1)

    # 逐列：awk 要拿得到 MODE，而且三個 cmd/eff 的 <td> 要在 MODE != audit 之下
    assert '-v MODE="$HTML_MODE"' in t, "awk 沒有收到 MODE，逐列就分不了流"
    assert 'if (MODE != "audit")' in t, "逐列的操作欄沒有被 MODE 分流"


def test_頁尾整段只給完整版():
    """⚠️ 這條紅燈的意思是：稽核版表格後面又長出東西了。

    2026-10-14 使用者拍板方案 A（取代 10-13 方案 B 的聲明句）：
    「這個資訊我在稽核版本，任何一個字我都不想再看到，這表格畫面就結束了」。
    頁尾有聲明句、Check ended at、Check summary、未完成清單、逐條工作單（含指令），
    稽核版一個字都不留——連空的 <div class="tail"> 都不輸出。
    原則：稽核版只放逐項檢核結果表，不加摘要、統計、清單、說明句。
    """
    t = _text()
    after = t[t.index("echo '</tbody></table></div>' >> \"$HTML_OUT\""):]
    after = after[: after.index("cat >> \"$HTML_OUT\" <<'FCBHTMLJS'")]
    code = [l for l in after.split("\n") if l.strip() and not l.lstrip().startswith("#")]
    assert code[1] == 'if [ "$HTML_MODE" != audit ]; then', code[:3]
    body = code[2:code.index("fi")]
    assert body == [
        "    echo '<div class=\"tail\">' >> \"$HTML_OUT\"",
        '    cat "$tail_file" >> "$HTML_OUT"',
        "    echo '</div>' >> \"$HTML_OUT\"",
    ], body
    # fi 之後只剩清暫存檔，不可以再輸出任何東西到 HTML
    rest = code[code.index("fi") + 1:]
    assert rest == ['rm -f "$tail_file" 2>/dev/null'], rest


def test_稽核版不可以提到另一份():
    """稽核版的任何輸出都不可以暗示「還有完整版」，也不再有聲明句（方案 A）。

    只看會執行的行（註解裡講沿革沒關係，不會進報告）。
    """
    code = "\n".join(l for l in _text().split("\n") if not l.lstrip().startswith("#"))
    for bad in ("完整版由系統管理者保管", "僅列於完整版", "本份為稽核用版本", "未列入本報告"):
        assert bad not in code, f"稽核版還會印出「{bad}」"
    # 「修正指令只是報告裡的文字」這句提示只能出現在完整版
    hint = [l for l in code.split("\n") if "「修正指令」只是" in l]
    assert hint and all(l.lstrip().startswith('[ "${HTML_MODE}" = audit ] ||') for l in hint), (
        "稽核版沒有修正指令欄，那句提示要用 HTML_MODE 擋掉"
    )


def test_頁尾樣式只有完整版有_黃底():
    t = _text()
    m = re.search(r'\[ "\$\{HTML_MODE\}" = audit \] \|\| \\\n(.*?\n.*?)\n', t)
    assert m and ".tail {" in m.group(1) and "#fffbe6" in m.group(1), "完整版頁尾維持黃底（自己用）"
    # 共用的 CSS heredoc 裡不可以有 .tail
    css = t[t.index("cat <<'FCBHTMLB'"):]
    css = css[: css.index("\nFCBHTMLB\n")]
    assert ".tail" not in css


SH = shutil.which("sh")


@pytest.mark.skipif(SH is None or os.name == "nt", reason="需要 POSIX 環境的 sh")
def test_實跑_稽核版表格之後沒有任何文字_完整版頁尾照舊(tmp_path):
    """整支實跑一次（唯讀，非 AIX 會有很多 Error，不影響輸出結構）。"""
    work = tmp_path / "w"
    work.mkdir()
    shutil.copy(_script(), work / _script().name)
    env = dict(os.environ, FCBAIX_OUTDIR=str(work))
    r = subprocess.run([SH, _script().name], cwd=work, env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    audit = list(work.glob("FCB_*_audit.html"))
    full = [p for p in work.glob("FCB_*.html") if not p.name.endswith("_audit.html")]
    txt = list(work.glob("FCB_*.txt"))
    assert len(audit) == 1 and len(full) == 1 and len(txt) == 1

    a = audit[0].read_text(encoding="utf-8")
    tail = a[a.index("</tbody></table></div>") + len("</tbody></table></div>"):]
    visible = re.sub(r"<script>.*?</script>", "", tail, flags=re.S)
    visible = re.sub(r"<[^>]+>", "", visible).strip()
    assert visible == "", f"稽核版表格之後還有文字：{visible[:200]!r}"
    for bad in ("Check ended at", "Check summary", "未完成清單", "未列入本報告", 'class="tail"'):
        assert bad not in a, f"稽核版出現「{bad}」"

    # 內部版（完整 HTML、.txt）照舊
    f = full[0].read_text(encoding="utf-8")
    for keep in ('<div class="tail">', "Check ended at", "Check summary", "未完成清單"):
        assert keep in f, f"完整版少了「{keep}」"
    assert "Check summary:" in txt[0].read_text(encoding="utf-8")
