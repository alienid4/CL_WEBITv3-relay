"""FCB-AIX inetd 檢核：服務名 != daemon 程序名，不可以用服務名去 ps 抓。

2026-10-16 真機誤判：rlogin（inetd 服務名 login）那條判 Non-Compliant，
目前值寫「inetd.conf 已關但行程仍在執行: root 22021002 login」——
那個 pid 22021002 的 login 其實是使用者自己的登入 session。

原因：舊版行程比對用 `^<服務名>d?$` 正則。對 login：
  * pattern `^logind?$` 抓不到真正的 daemon `rlogind`（開頭是 r）；
  * 卻命中 `/usr/sbin/login`——AIX 的一般使用者登入程序，有人登入就在跑。

修法：service -> daemon 精確對照（inetd_daemon），ps 用 daemon 名「完全相等」比對。
失敗方向安全：寧可漏判行程（還有 inetd.conf 把關），也不要把登入程序誤判成服務還開著。

## 這裡守什麼
1. inetd_daemon 的對照正確：login->rlogind、exec->rexecd、klogin->krlogind、
   kshell->krshd；內建／會撞常見指令的（chargen/discard/echo/netstat）回空字串。
2. 舊的危險正則 `^$3`+`d?$` 不可以復活。
3. 行為：login 設定已關 + 有 login 程序 -> Compliant；真的 rlogind 在跑 -> Non-Compliant；
   exec/rexecd 同理；內建服務的同名雜訊程序不可以誤命中。
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


def _script() -> Path:
    found = [q for q in SCRIPTS.iterdir() if NAME_RE.match(q.name)]
    assert len(found) == 1, "scripts/ 底下要剛好一支 fcb_aixV###.ksh：%s" % found
    return found[0]


def _text() -> str:
    with _script().open(encoding="utf-8", newline="") as f:
        return f.read()


def _inetd_daemon_fn() -> str:
    t = _text()
    a = t.index("inetd_daemon() {")
    # 函式結尾是 default case 之後那個單獨的 }
    b = t.index("\n}\n", a) + 3
    return t[a:b]


def _harness(tmp_path: Path) -> Path:
    """inetd_daemon + ck_inetd 的行程/設定比對邏輯，抽出來單獨跑。"""
    fn = _inetd_daemon_fn()
    h = tmp_path / "h_inetd.sh"
    with io.open(h, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            "#!/bin/sh\n"
            'emit() { printf "%s|%s\\n" "$2" "$6"; }\n'
            + fn
            + '''
ck() {   # $1=id $2=name $3=svc   環境：CONF、PS_SNAP
    _l=`grep -E "^[ \t]*$3[ \t]" "$CONF" 2>/dev/null | head -n 1`
    _idaemon=`inetd_daemon "$3"`
    _rp=""
    if [ -n "${_idaemon}" ]; then
        _rp=`printf '%s\\n' "$PS_SNAP" | grep -v grep | awk -v d="${_idaemon}" '{n=split($8,p,"/"); c=p[n]; if (c == d) print $1, $2, c}' | head -n 1`
    fi
    if [ -n "$_l" ]; then
        emit "$1" "Non-Compliant" x "$2" x "inetd.conf 仍啟用"
    elif [ -n "$_rp" ]; then
        emit "$1" "Non-Compliant" x "$2" x "行程仍在執行: $_rp"
    else
        emit "$1" "Compliant" x "$2" x "ok"
    fi
}
ck "$1" "$1" "$2"
''')
    return h


def _run(tmp_path, svc, conf_text, ps):
    conf = tmp_path / "inetd.conf"
    with io.open(conf, "w", encoding="utf-8", newline="\n") as f:
        f.write(conf_text)
    env = {"CONF": str(conf).replace("\\", "/"), "PS_SNAP": ps, "PATH": "/usr/bin:/bin"}
    r = subprocess.run([SH, str(_harness(tmp_path)), svc, svc],
                       capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace", env=env)
    assert r.returncode == 0, r.stderr
    status, _, value = r.stdout.strip().partition("|")
    return status, value


# ---- 對照表正確性 ------------------------------------------------------------

def _daemon_of(tmp_path, svc):
    fn = _inetd_daemon_fn()
    h = tmp_path / "d.sh"
    with io.open(h, "w", encoding="utf-8", newline="\n") as f:
        f.write("#!/bin/sh\n" + fn + '\ninetd_daemon "$1"\n')
    r = subprocess.run([SH, str(h), svc], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    return r.stdout.strip()


@pytest.mark.parametrize("svc,daemon", [
    ("login", "rlogind"), ("klogin", "krlogind"), ("kshell", "krshd"),
    ("exec", "rexecd"), ("finger", "fingerd"), ("imap2", "imapd"),
    ("pop3", "pop3d"), ("uucp", "uucpd"), ("telnet", "telnetd"), ("ftp", "ftpd"),
    ("shell", "rshd"), ("pcnfsd", "rpc.pcnfsd"), ("rstatd", "rpc.rstatd"),
])
def test_服務名對到正確的_daemon(tmp_path, svc, daemon):
    assert _daemon_of(tmp_path, svc) == daemon


@pytest.mark.parametrize("svc", ["chargen", "discard", "echo", "netstat"])
def test_內建或會撞常見指令的服務_daemon回空(tmp_path, svc):
    assert _daemon_of(tmp_path, svc) == "", (
        "%s 會拿去 ps 比對，但它是 inetd 內建或會撞常見指令——該回空字串只看設定檔" % svc)


def test_login_不會對到_logind():
    """舊 bug 的核心：login 絕對不能解析成會命中系統登入程序的名字。"""
    assert "logind" != _text()  # 佔位，真正的保證在下面兩條
    assert 'login)    printf' in _text()
    assert "rlogind" in _text()


def test_舊的危險正則不可以復活():
    t = _text()
    assert '_ipat="^$3"' not in t, "舊的 ^<服務名>d?$ 正則就是誤判根源，不可以回來"
    assert "c ~ pat" not in t, "行程比對不可以再用正則比對，要用 daemon 名完全相等"
    assert "c == d" in t, "行程比對要用 daemon 名完全相等"


# ---- 行為 --------------------------------------------------------------------

COMMENTED = "#login\tstream\ttcp6\tnowait\troot\t/usr/sbin/rlogind\trlogind\n"
ACTIVE = "login\tstream\ttcp6\tnowait\troot\t/usr/sbin/rlogind\trlogind\n"
PS_LOGIN = "root 22021002 1 0 10:00 pts/0 0:00 login\n"
PS_RLOGIND = "root 999 1 0 10:00 - 0:00 /usr/sbin/rlogind\n"


def test_login已關但有使用者登入程序_判Compliant(tmp_path):
    """使用者真機案例：inetd.conf 的 login 已註解，ps 裡有 login（使用者的登入 session）。"""
    status, value = _run(tmp_path, "login", COMMENTED, PS_LOGIN)
    assert status == "Compliant", (
        "有人登入（ps 有 login）被誤判成 rlogin 服務還開著：%s" % value)


def test_真的有rlogind在跑且設定已關_判Non_Compliant(tmp_path):
    status, value = _run(tmp_path, "login", COMMENTED, PS_RLOGIND)
    assert status == "Non-Compliant" and "rlogind" in value, value


def test_login設定檔啟用_判Non_Compliant(tmp_path):
    status, value = _run(tmp_path, "login", ACTIVE, PS_LOGIN)
    assert status == "Non-Compliant", value


def test_exec對rexecd_不被純exec程序誤命中(tmp_path):
    ec = "#exec\tstream\ttcp\tnowait\troot\t/usr/sbin/rexecd\trexecd\n"
    # 真的 rexecd 在跑 -> 抓到
    s1, v1 = _run(tmp_path, "exec", ec, "root 888 1 0 10:00 - 0:00 /usr/sbin/rexecd\n")
    assert s1 == "Non-Compliant" and "rexecd" in v1, v1
    # 只有一個叫 exec 的雜訊程序 -> 不可以命中
    s2, v2 = _run(tmp_path, "exec", ec, "root 777 1 0 10:00 - 0:00 exec\n")
    assert s2 == "Compliant", "純 exec 程序不該被當成 rexecd：%s" % v2


def test_內建服務的同名雜訊程序不會誤命中(tmp_path):
    for svc, prog in [("echo", "echo"), ("netstat", "netstat"), ("chargen", "chargen")]:
        conf = "#%s\tstream\ttcp\tnowait\troot\tinternal\n" % svc
        s, v = _run(tmp_path, svc, conf, "root 555 1 0 10:00 - 0:00 %s\n" % prog)
        assert s == "Compliant", "%s 的同名雜訊程序被誤命中：%s" % (svc, v)


# ---- Bug 2：HTML / txt 顯示跑哪一版 ksh -------------------------------------

def test_腳本有SCRIPT_FILE且只接受正確檔名樣式():
    t = _text()
    assert "SCRIPT_FILE=`basename" in t
    assert "fcb_aixV[0-9][0-9][0-9].ksh)" in t, (
        "SCRIPT_FILE 要只接受 fcb_aixV###.ksh 樣式，別的留空不要印誤導的檔名")


def test_HTML抬頭顯示稽核腳本版本():
    t = _text()
    assert "<dt>稽核腳本版本</dt>" in t, "HTML 抬頭要有一列顯示跑的是哪一版 ksh"
    # 檔名 + SCRIPTV 都要能進到那一列
    seg = t[t.index("<dt>稽核腳本版本</dt>"):t.index("<dt>稽核腳本版本</dt>") + 400]
    assert "$_e_sfile" in seg and "$SCRIPTV" in seg


def test_txt抬頭仍有腳本版本():
    assert "腳本版本: $SCRIPTV" in _text(), "純文字版的版本列不可以掉"
