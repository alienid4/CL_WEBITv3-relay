"""FCB-AIX-0087「所有帳號都要有密碼雜湊」：AIX 內建帳號不列入判定。

使用者 2026-10-16 拍板：「內建帳號本來就不會登入」。

在這之前，這一條在真機上永遠是 Non-Compliant，列出來的是
`daemon bin sys adm uucp guest nobody lpd` 這些 AIX 預設就沒有密碼雜湊的帳號——
那是設計如此，不是被人弄壞的。整串紅字的代價是：真正該看的
「有人建了一個沒設密碼的帳號」被淹在裡面，沒有人看得出來。

## 這裡守什麼

1. 排除用的是**白名單常數**，不是 UID 範圍。AIX 的 UID 配置跟 Linux 不一樣
   （nobody 是 4294967294、invscout 是 6），用範圍猜一定會放過或誤殺。
2. 排除**不可以靜默**：目前值要寫出排除了幾個。這是專案鐵律——
   不知道／沒查要顯示成不知道，不可以用一個乾淨的綠燈代替。
3. 檔案讀不到**還是 Error**。排除內建帳號之後這道更重要：
   不可以因為「沒有非內建帳號落掉」就把「根本沒讀到檔案」判成 Compliant。

## 怎麼測

把腳本裡 `# >>> FCB-AIX-0087 BEGIN` 到 `# <<< FCB-AIX-0087 END` 這一段抽出來，
包成可以單獨跑的小腳本（跟 test_fcb_aix_sanitize 同一個做法），
`emit` 換成印出「狀態|目前值」的替身，`/etc/security/passwd` 換成測試檔。
腳本本體不留任何測試用的開關——路徑是在產生 harness 時換掉的。
"""
import io
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "APP" / "asset-module" / "scripts"
NAME_RE = re.compile(r"^fcb_aixV(\d{3})\.ksh$")

SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(SH is None, reason="需要 POSIX 環境的 sh")

BEGIN = "# >>> FCB-AIX-0087 BEGIN"
END = "# <<< FCB-AIX-0087 END"

#: 註解已經點名、真機上一定會出現的 AIX 內建帳號。
NAMED_IN_COMMENT = ["daemon", "bin", "sys", "adm", "uucp", "guest", "nobody", "lpd"]


def _script() -> Path:
    found = [q for q in SCRIPTS.iterdir() if NAME_RE.match(q.name)]
    assert len(found) == 1, "scripts/ 底下要剛好一支 fcb_aixV###.ksh：%s" % found
    return found[0]


def _text() -> str:
    with _script().open(encoding="utf-8", newline="") as f:
        return f.read()


def _block() -> str:
    t = _text()
    a = t.index(BEGIN)
    b = t.index(END, a)
    return t[a:b]


def _harness(tmp_path: Path, passwd: Path, am_root: str = "1") -> Path:
    """把 0087 那一段抽出來，換掉 passwd 路徑，包成可以單獨跑的小腳本。"""
    body = _block().replace("/etc/security/passwd", str(passwd).replace("\\", "/"))
    h = tmp_path / "h0087.sh"
    with io.open(h, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            "#!/bin/sh\n"
            "CAT_ACC='帳號'\n"
            "AM_ROOT=%s\n"
            "emit() { printf '%%s|%%s\\n' \"$2\" \"$6\"; }\n" % am_root
            + body)
    return h


def _run(tmp_path: Path, content, am_root: str = "1"):
    pw = tmp_path / "passwd"
    if content is None:
        pw = tmp_path / "does_not_exist"
    else:
        with io.open(pw, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    r = subprocess.run([SH, str(_harness(tmp_path, pw, am_root))],
                       capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    out = r.stdout.strip()
    status, _, value = out.partition("|")
    return status, value


def _entry(user: str, pw: str) -> str:
    return "%s:\n\tpassword = %s\n\tlastupdate = 1700000000\n\n" % (user, pw)


def test_白名單是常數_而且涵蓋註解點名的那幾個():
    t = _text()
    m = re.search(r'^SYSACCT_0087="([^"]+)"$', t, re.M)
    assert m, "內建帳號清單要寫成一個常數（SYSACCT_0087），不要散在 awk 裡"
    names = m.group(1).split()
    missing = [n for n in NAMED_IN_COMMENT if n not in names]
    assert not missing, "白名單漏掉註解已經點名的內建帳號：%s" % missing
    assert "root" not in names, "root 不可以被排除——root 一定要有密碼雜湊"
    assert len(names) == len(set(names)), "白名單有重複"


def test_不可以用_UID_範圍猜():
    """AIX 的 UID 配置跟 Linux 不一樣，用範圍猜會放過該抓的或誤殺該留的。"""
    b = _block()
    assert "/etc/passwd" not in b, "判定不該去讀 /etc/passwd 拿 UID"
    assert not re.search(r"\buid\b", b, re.I) or "UID" in b, "不要用 UID 判斷"


def test_修正指令保留內建帳號的警告():
    """使用者可能還是想確認內建帳號不能登入，那段警告不可以順手刪掉。"""
    t = _text()
    assert "chuser login=false rlogin=false" in t


def test_只有內建帳號沒密碼_判_Compliant_而且寫得出排除幾個(tmp_path):
    content = (_entry("root", "aBcD1234ef")
               + "".join(_entry(u, "*") for u in NAMED_IN_COMMENT))
    status, value = _run(tmp_path, content)
    assert status == "Compliant", "只有內建帳號沒有雜湊，不該算不合格：%s" % value
    assert "已排除 8 個" in value, (
        "排除不可以靜默——目前值要寫出排除了幾個，實際是：%s" % value)


def test_有一個非內建帳號沒密碼_判_Non_Compliant_而且只列那一個(tmp_path):
    content = (_entry("root", "aBcD1234ef")
               + "".join(_entry(u, "*") for u in NAMED_IN_COMMENT)
               + _entry("appsvc", ""))
    status, value = _run(tmp_path, content)
    assert status == "Non-Compliant", value
    assert "appsvc" in value
    for u in NAMED_IN_COMMENT:
        assert u not in value, "內建帳號 %s 不該列進去：%s" % (u, value)
    assert "已排除 8 個" in value


def test_sysadm_不會被_adm_誤當成內建帳號(tmp_path):
    """白名單比對要整個帳號名相等，不可以用子字串。"""
    content = _entry("root", "aBcD1234ef") + _entry("sysadm", "*")
    status, value = _run(tmp_path, content)
    assert status == "Non-Compliant", value
    assert "sysadm" in value


def test_檔案讀不到仍然是_Error_不可以變成_Compliant(tmp_path):
    status, value = _run(tmp_path, None)
    assert status == "Error", (
        "讀不到檔案卻不是 Error——那是拿『沒查到』冒充『沒問題』：%s|%s" % (status, value))
    assert "未查" in value


def test_非_root_仍然是_Error(tmp_path):
    content = _entry("root", "aBcD1234ef")
    status, value = _run(tmp_path, content, am_root="0")
    assert status == "Error" and "非 root" in value


def test_標準設定值看得出判定範圍是非內建帳號():
    t = _text()
    m = re.search(r'^_std87="([^"]+)"$', t, re.M)
    assert m, "找不到 _std87"
    assert "內建" in m.group(1), (
        "標準設定值要讓人看得出判定範圍已經排除內建帳號，實際是：%s" % m.group(1))


def test_目前值不可以含分號():
    """emit 用分號當欄位分隔（會被換成逗號），值裡塞分號等於把欄位弄壞。"""
    for line in _block().split("\n"):
        if line.strip().startswith("emit "):
            assert ";" not in line, "emit 的值裡不可以有分號：%s" % line.strip()
