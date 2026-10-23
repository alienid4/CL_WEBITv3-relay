"""`san_value()` 不可以把合法的中文毀掉。

## 為什麼有這個檔

2026-10-01 真機事故。腳本原本用 `iconv -f UTF-8 -t UTF-8` 的**離開碼**判斷
「這段字串是不是合法 UTF-8」，不合法才把非 ASCII 換成點。

上到 AIX 7.2 之後，`FCB-AIX-0023`~`0043` 共 **21 條**的「目前值」整片變成點：

    .........: ..........................inetd ......... （原值含非 UTF-8 位元組，已以 . 取代）

決定性的證據是**同一行後半段那句註記的中文是好的**——那是腳本自己加的字。
所以不是檔案編碼壞、也不是傳輸壞，是 **AIX 的 iconv 對合法輸入也回非 0**。

## 這個檔要擋的兩件事

1. **不要拿外部指令的離開碼當語意判斷**（平台行為不一致，而我們沒有那些平台可驗）
2. **不要整段一刀切**（同一格常常是「一段好中文 + 幾個壞位元組」，
   整段替換會把好的一起毀掉）

## ⚠️ 為什麼以前的測試是綠的

以前的單元測試也在 Windows 上跑，**而這個 bug 在 Windows 上不會出現**
（那台的 iconv 對合法輸入回 0）。測試綠、真機炸。

現在的實作**沒有任何外部相依**（只用 awk 的 substr/length/index/sprintf），
行為在哪個平台都一樣——所以這裡測綠，才真的代表 AIX 上也是綠的。
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "APP" / "asset-module" / "scripts"
NAME_RE = re.compile(r"^fcb_aixV(\d{3})\.ksh$")

BASH = shutil.which("bash")


def _bash_usable(bash) -> bool:
    """bash 存在還不夠，要真的跑得動「帶原生路徑的 POSIX 腳本」。

    Windows 的 `bash.EXE`（WSL）吃不下 Windows 路徑（`C:\\x` 會被吃成 `C:x`），
    所以只檢查 `which bash` 會誤判成「有 bash」然後整批紅——那不是程式壞，
    是這台沒有能跑這種腳本的 bash。改用實測探針：跑不起來就 skip。
    Linux／git-bash 這類正常環境探針會過，照跑真覆蓋。
    """
    if not bash:
        return False
    import tempfile
    d = tempfile.mkdtemp()
    try:
        p = Path(d) / "probe.sh"
        p.write_text("#!/bin/sh\nprintf ok\n", encoding="utf-8", newline="\n")
        r = subprocess.run([bash, str(p)], capture_output=True, timeout=30)
        return r.returncode == 0 and r.stdout.strip() == b"ok"
    except Exception:
        return False
    finally:
        shutil.rmtree(d, ignore_errors=True)


pytestmark = pytest.mark.skipif(
    not _bash_usable(BASH),
    reason="這台的 bash 跑不了帶原生路徑的 POSIX 腳本（如 WSL 吃不下 Windows 路徑）——不是程式壞了")


def _script() -> Path:
    found = [q for q in SCRIPTS.iterdir() if NAME_RE.match(q.name)]
    assert len(found) == 1, "scripts/ 底下要剛好一支 fcb_aixV###.ksh：%s" % found
    return found[0]


def _harness(tmp_path: Path) -> Path:
    """把 san_value 那一段抽出來，包成可以單獨跑的小腳本。

    ⚠️ 函式結尾不能找「第一個單獨的 }」——awk 程式裡面就有一行是 `}`。
    真正的結尾是「單獨一行 ' 之後那一行 }」。
    """
    lines = _script().read_text(encoding="utf-8").split("\n")
    a = next(i for i, l in enumerate(lines) if l.startswith("SAN_MAX="))
    b = next(i for i, l in enumerate(lines) if l.startswith("san_value() {"))
    e = next(i for i in range(b, len(lines))
             if lines[i] == "'" and lines[i + 1] == "}") + 1
    body = "\n".join(lines[a:e + 1])
    h = tmp_path / "harness.sh"
    h.write_text(
        '#!/bin/sh\nhave() { command -v "$1" >/dev/null 2>&1; }\n'
        + body
        + '\nsan_value "$(cat "$1")"\n',
        encoding="utf-8", newline="\n")
    return h


def _san(tmp_path: Path, raw: bytes) -> bytes:
    """餵一段**原始位元組**進去，拿回 san_value 的輸出（也是原始位元組）。"""
    f = tmp_path / "in.bin"
    f.write_bytes(raw)
    out = subprocess.run([BASH, str(_harness(tmp_path)), str(f)],
                         capture_output=True, timeout=60)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return out.stdout.rstrip(b"\n")


NOTE = "（原值含非 UTF-8 位元組，已以 . 取代）".encode("utf-8")


def test_純ASCII原樣不動(tmp_path):
    raw = b"inetd: chargen stream tcp nowait root internal"
    assert _san(tmp_path, raw) == raw


def test_合法中文原樣不動(tmp_path):
    """⚠️ 這就是 2026-10-01 真機毀掉 21 條的那個案例。

    這條紅了代表又有人把「整段判斷、整段替換」的邏輯放回來了。
    """
    raw = "此機器無 lsitab 指令（非 AIX？）——未查，不可視為合規".encode("utf-8")
    got = _san(tmp_path, raw)
    assert got == raw, (
        "合法的中文被改掉了。\n"
        "  進去：%r\n  出來：%r\n"
        "san_value 只該替換**不合法的位元組**，合法的一律原樣保留。"
        % (raw.decode("utf-8"), got.decode("utf-8", "replace")))
    assert NOTE not in got, "沒有壞位元組卻加了『已以 . 取代』的註記"


def test_壞位元組只換掉那幾個_旁邊的中文要留著(tmp_path):
    """Big5 的 \\xA4\\x40 夾在合法中文中間。

    \\xA4 不是合法的 UTF-8 前導位元組 -> 換成點；
    \\x40 是 ASCII 的 '@' -> 本來就合法，要留著；
    前後的中文**一個字都不可以動**。
    """
    raw = "前面合法中文".encode("utf-8") + b"\xa4\x40" + "後面也是合法中文".encode("utf-8")
    got = _san(tmp_path, raw)
    assert "前面合法中文".encode("utf-8") in got, "壞位元組前面的中文被毀掉了"
    assert "後面也是合法中文".encode("utf-8") in got, "壞位元組後面的中文被毀掉了"
    assert b".@" in got, "壞位元組沒有被換成點（或連 ASCII 的 @ 也一起換掉了）"
    assert b"\xa4" not in got, "不合法的位元組還留在輸出裡"
    assert NOTE in got, "換過位元組卻沒有加註記，看的人不知道原值被動過"


def test_herald型_ASCII加壞位元組加中文(tmp_path):
    raw = (b'default herald="!BANNER!' + b"\xff\xfe"
           + b' (Unauthorized)" ' + "登入警語".encode("utf-8"))
    got = _san(tmp_path, raw)
    assert b'default herald="!BANNER!' in got, "ASCII 部分被動到了"
    assert "登入警語".encode("utf-8") in got, "中文被毀掉了"
    assert b"\xff" not in got and b"\xfe" not in got, "不合法的位元組還在"


def test_全部都是壞位元組(tmp_path):
    got = _san(tmp_path, b"\xa4\x40\xa4\x41")
    assert b"\xa4" not in got
    assert NOTE in got


def test_控制字元要壓掉(tmp_path):
    got = _san(tmp_path, b"a\x01b\x02c\td")
    assert b"\x01" not in got and b"\x02" not in got
    assert b"a" in got and b"d" in got


def test_長中文截斷_切點要退到合法邊界而不是毀掉整段(tmp_path):
    """截斷的切點會落在多位元組字中間，那是預期內的。

    預期行為是「那一兩個落單的位元組變成點」，
    **不是**「整段降成 ASCII」——後者就是這次事故的形狀。
    """
    raw = "中文測試".encode("utf-8") * 200        # 2400 bytes
    got = _san(tmp_path, raw)
    assert "已截斷，原長 2400 位元組".encode("utf-8") in got, "沒有截斷或沒標明原長"
    assert got.count("中文測試".encode("utf-8")) > 20, (
        "截斷之後合法的中文剩太少，八成又被整段替換了")
    assert b"\xe4\xb8\xad" in got, "連一個完整的中文字都沒留下"


def test_長ASCII截斷(tmp_path):
    raw = b"ABCDE" * 200
    got = _san(tmp_path, raw)
    assert b"ABCDEABCDE" in got
    assert "已截斷，原長 1000 位元組".encode("utf-8") in got


def test_腳本裡不可以再用iconv判斷合法性(tmp_path):
    """擋的是「哪天有人覺得 iconv 比較簡單又改回去」。

    iconv 的離開碼在 AIX 上對合法輸入也回非 0——我們沒有 AIX 可以驗，
    所以這條路整個不能走，不是「小心一點就好」。
    """
    src = _script().read_text(encoding="utf-8")
    body = src.split("SAN_MAX=", 1)[1]
    bad = [l for l in body.split("\n")
           if "iconv" in l and not l.lstrip().startswith("#")]
    assert not bad, (
        "san_value 附近又出現會執行的 iconv：%s\n"
        "UTF-8 合法性要自己逐位元組判，不要靠外部指令的離開碼。" % bad)
