"""AIX 腳本的兩個 2026-10-13 真機事故，擋在這裡不准復發。

## 一、`$VAR` 後面直接接全形字 -> 數字會消失

真機輸出是：

    Check summary: 合計 98 項；Compliant；Non-Compliant；Not-Applicable；Error 0

`98` 在，三個計數卻不見了。原因是腳本寫成 `"...$N_C；Non-Compliant..."`：
AIX ksh 在非 UTF-8 locale 下把 `；` 的位元組也當成變數名的一部分，
於是去展開一個不存在的變數 -> 空字串，順便吃掉 `；` 的前兩個位元組，
剩一個孤兒位元組 -> 網頁上顯示成亂碼。

**這比它看起來嚴重**：合規率是用這些數字算的。數字是空的，
整份統計就是假的——而且它長得一點都不像壞掉。這就是通則第一條的經典犯法現場：
不准拿一句確定的話（「Error 0」）去蓋一個不知道。

`$N_T 項` 後面有空白所以逃過一劫，`$N_E"` 在字串結尾也逃過——
剛好就是真機報告上唯一還正常的那兩個數字。靠運氣的東西不能留。

修法：加大括號 `${N_C}`。

## 二、`ck_sshd` 把驗證用的樣式當成要寫進設定檔的值

原本只做 `cut -d'|' -f1`，錨點沒剝，於是修正指令長這樣：

    echo 'PermitEmptyPasswords ^no$' >> sshd_config
    echo 'LogLevel ^(INFO'           >> sshd_config
    ... && stopsrc -s sshd && startsrc -s sshd

sshd 讀到這種設定檔會拒絕啟動 = SSH 進不去，只能從 HMC console 救。
那一欄當時還標著「可執行」。
"""
import io
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "APP" / "asset-module" / "scripts"

#: `$VAR` 緊接著一個非 ASCII 字元。AIX ksh 會把那個字的位元組吃進變數名。
GREEDY_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)(?=[^\x00-\x7f])")

#: 正規表示式的元字元。設定檔的「值」裡面出現任何一個，就不是一個字面值。
META_RE = re.compile(r"[\[\]*+?.^$()\|]")


def _script() -> Path:
    found = sorted(SCRIPTS.glob("fcb_aixV???.ksh"))
    assert len(found) == 1, f"scripts/ 底下應該只有一支 fcb_aixV###.ksh，找到 {found}"
    return found[0]


def _text() -> str:
    """⚠️ 用 io.open 不用 Path.read_text(newline=...)：

    `newline=` 是 Python 3.13 才加進 Path.read_text 的。本機 `py -3` 是 3.14
    會過，專案 .venv 與 CI 是 3.11 會 TypeError——
    那就變成「本機綠、CI 紅」，而我們就是靠 CI 綠才敢說完成。
    newline="" 不可以省：要原樣讀，不然 CRLF 會被翻成 LF，指紋就飄了。
    """
    return io.open(_script(), encoding="utf-8", newline="").read()


def _code_lines(text: str):
    """只看會執行到的行。註解裡的全形字不會進輸出，不該被這道關卡擋。"""
    for n, line in enumerate(text.split("\n"), 1):
        if not line.lstrip().startswith("#"):
            yield n, line


def test_變數後面不可以直接接全形字():
    """⚠️ 這條紅燈的意思是：你寫了 `$FOO，` 這種形式。

    照著做：改成 `${FOO}，`。

    不要改成「在後面補一個半形空白」——那會讓輸出多一個看不見的空格，
    而且下一個人照樣會寫出同樣的 bug。大括號是在講「變數名到這裡為止」，
    那才是真正要表達的意思。
    """
    text = _text()
    bad = [
        (n, m.group(1), line.strip()[:70])
        for n, line in _code_lines(text)
        for m in GREEDY_RE.finditer(line)
    ]
    assert not bad, (
        "下面這些 $變數 後面直接接了全形字，AIX ksh 會把它展開成空字串：\n"
        + "\n".join(f"  第 {n} 行 ${v} -> 改成 ${{{v}}}　｜ {s}" for n, v, s in bad)
    )


def _ck_sshd_patterns():
    text = _text()
    pat = re.compile(r'^ck_sshd\s+"[^"]*"\s+"[^"]*"\s+"([^"]*)"\s+"([^"]*)"', re.M)
    found = pat.findall(text)
    assert found, "找不到任何 ck_sshd 呼叫——這道關卡失效了，比紅燈更糟"
    return found


@pytest.mark.skipif(shutil.which("sh") is None, reason="需要 POSIX sh 才能跑腳本自己的推導邏輯")
def test_ck_sshd_推不出字面值時不可以給修正指令():
    """⚠️ 這條紅燈的意思是：ck_sshd 會把一段正規表示式寫進 sshd_config。

    照著做：確認 ck_sshd 裡那段剝錨點＋元字元守門還在，而且新加的期望值
    要嘛是乾淨的字面值（`^no$`），要嘛就讓它落進「未確認」。

    **不要為了讓這條綠燈，去手寫一個「看起來對」的建議值。**
    推不出來就填「未確認」——填錯的指令比空白危險得多。
    """
    # 直接跑腳本裡那一行推導，不另外複寫一份 Python 版：
    # 複寫的那份一漂掉，守門就在守一個不存在的東西（09-24 版本戳同型問題）。
    text = _text()
    # V009 起這一行改用 printf——AIX ksh 的 echo 會展開反斜線，
    # 見 test_fcb_aix_echo_guard。兩種都接受，才不會因為換寫法就靜默失守。
    m = re.search(r"^\s*(_sv=`(?:printf|echo)[^`]*`)\s*$", text, re.M)
    assert m, "ck_sshd 裡找不到 _sv 的推導那一行——改過名字就要一起改這裡"
    derive = m.group(1)

    for keyword, pattern in _ck_sshd_patterns():
        out = subprocess.run(
            ["sh", "-c", f'set -- a b "{keyword}" "{pattern}"\n{derive}\nprintf %s "$_sv"'],
            capture_output=True, text=True, check=True,
        ).stdout
        if META_RE.search(out) or not out:
            continue  # 落進「未確認」，正確
        assert not META_RE.search(out), (
            f"{keyword}：期望值樣式 {pattern} 推出來的設定值是 {out!r}，"
            "裡面還有正規表示式元字元。這串會被寫進 sshd_config 然後重啟 sshd。"
        )


def test_ck_sshd_有元字元守門():
    """守門本身被刪掉的話，上面那條會因為「剝得很乾淨」而全綠——

    所以再加一條直接確認守門存在。這是刻意的重複：
    上面那條測行為，這條測「那段防線還在不在」。
    """
    text = _text()
    body = text[text.index("ck_sshd() {"):]
    body = body[: body.index("\n}\n")]
    assert "set_fix" in body
    assert re.search(r"grep -q '\[\]\[\*\+\?\.\^\$\(\)", body), (
        "ck_sshd 裡的元字元守門不見了。沒有它，期望值樣式會被當成設定值"
        "寫進 sshd_config，然後 stopsrc/startsrc sshd —— SSH 會進不去。"
    )

