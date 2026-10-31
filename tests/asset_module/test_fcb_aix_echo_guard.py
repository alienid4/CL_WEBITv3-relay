"""收集到的值不可以用 `echo` 輸出 —— AIX ksh 的 echo 會展開反斜線。

2026-10-13 真機 `ksh -x` 實證。`/etc/security/login.cfg` 的 `herald` 值裡
有**字面的反斜線 n**（登入警語前面排了十幾個空行）：

    default herald="\\n\\n\\n…\\r（Unauthorized use of this system is prohibited.）\\n\\r"

**AIX ksh 的 builtin `echo` 會把它展開成真換行。** 於是 `emit` 裡的

    _nf=`echo "$_line" | awk -F';' '{print NF}'`

拿到 21 行、awk 每行印一個 NF -> `_nf` 變成「6 0 0 … 1 1 1 8」
-> `[ "$_nf" -ne 13 ]` 數值比較當場語法錯（0403-057）。

而下一行 `echo "$_line" >> "$tmp_file"` 同樣展開，
**FCB-AIX-0068 那一列在報告裡被寫成 21 行**，其中 20 行解析不出欄位。

## 為什麼要獨立成一道守門

那個 `_nf -ne 13` 的欄數守門，就是為了抓「欄位夾帶分隔字元」這種事裝的。
它**自己先炸了**，所以什麼都沒抓到，報告照樣產出、照樣寫「Error 0」。

教訓：守門只守「資料對不對」不夠，還要守**產生資料的方式安不安全**。
這一條守的是後者。

`printf '%s\\n'` 的格式字串是寫死的，值走參數進去，不會被解讀——
值裡有 `%`、有反斜線都一樣。

## 這件事唯一被發現的原因

V007 那道 stderr 守門。沒有它，這份報告會是「98 項、Error 0」，
沒有人會去查一份滿分的報告。
"""
import io
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "APP" / "asset-module" / "scripts"

#: 只有這幾個變數可以用 echo 輸出：它們是 emit 自己數出來的整數，
#: 不可能含反斜線。其他任何**收集到的**值都要走 printf。
ECHO_OK = {"N_T", "N_C", "N_N", "N_A", "N_E", "N_TODO"}

#: `echo` 之後、同一段指令裡出現的第一個變數。
#:
#: 排除 `| ; & \`` 之後的部分——那些已經是別的指令，不是 echo 在輸出的東西。
#: 也排除 `>` 之後：`echo "固定字串" >> "$tmp_file"` 裡的 `$tmp_file` 是
#: **寫到哪裡**，不是**寫什麼**，不會被 echo 展開。
ECHO_VAR_RE = re.compile(r"echo\b[^|;&`>]*?\$\{?([A-Za-z_][A-Za-z0-9_]*)")

#: `set_fix` 那幾行裡的 `echo` 是**寫給人看的修正指令文字**，
#: 不是這支腳本自己要執行的東西——它最後是以字串形式進報告的一欄。
#: 真正該管的是那串文字裡的值對不對，那是 test_fcb_aix_shell_safety 的事。
SKIP_PREFIX = ("set_fix",)


def _script() -> Path:
    found = sorted(SCRIPTS.glob("fcb_aixV???.ksh"))
    assert len(found) == 1, f"scripts/ 底下應該只有一支 fcb_aixV###.ksh，找到 {found}"
    return found[0]


def _text() -> str:
    # newline="" 要原樣讀；用 io.open 不用 Path.read_text(newline=)，
    # 後者是 Python 3.13 才有的，專案 .venv 與 CI 是 3.11。
    return io.open(_script(), encoding="utf-8", newline="").read()


def _code_lines(text: str):
    """註解裡的 echo 不會執行，不該被這道關卡擋。"""
    for n, line in enumerate(text.split("\n"), 1):
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith(SKIP_PREFIX):
            continue
        yield n, line


def test_收集到的值不可以用_echo_輸出():
    r"""⚠️ 這條紅燈的意思是：你用 `echo "$收集到的值"` 輸出。

    照著做：改成 `printf '%s\n' "$值"`。

    不要改成「先把值裡的反斜線清掉」——那會竄改我們量到的事實。
    herald 裡那些反斜線 n 是機器上真實的設定值，報告要照實呈現它；
    問題在輸出的方式，不在值。
    """
    bad = [
        (n, m.group(1), line.strip()[:70])
        for n, line in _code_lines(_text())
        for m in ECHO_VAR_RE.finditer(line)
        if m.group(1) not in ECHO_OK
    ]
    assert not bad, (
        "下面這些 echo 會輸出收集到的值。AIX ksh 的 echo 會展開反斜線 n，"
        "值裡只要有反斜線，那一列就會被切成好幾行：\n"
        + "\n".join("  第 {} 行 ${}　｜ {}".format(n, v, s) for n, v, s in bad)
        + "\n改成 printf 的形式。"
    )


def test_守門要能抓到舊版():
    """守門要證明會紅，不是證明會綠。

    拿修之前那一行（真機上真的炸掉的那一行）餵同一組規則，必須被抓到。
    抓不到就表示規則寫鬆了，那比沒有守門更糟——它會給人一種有在守的錯覺。
    """
    炸掉的那行 = """    _nf=`echo "$_line" | awk -F';' '{print NF}'`"""
    hits = [m.group(1) for m in ECHO_VAR_RE.finditer(炸掉的那行)]
    assert hits == ["_line"], f"規則抓不到真機上炸掉的那一行，抓到的是 {hits}"
    assert "_line" not in ECHO_OK
