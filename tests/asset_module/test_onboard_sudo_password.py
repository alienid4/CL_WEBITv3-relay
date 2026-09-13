"""sudo 要密碼的機器要能納管（2026-09-08 公司實測擋在這裡）。

實際看到的錯誤：

    sudo: a terminal is required to read the password; either use the -S option...
    sudo: a password is required

原因不是設定問題，是這支程式的 bug：腳本本來走 `sudo bash -s`，從 stdin 餵進去，
sudo 想問密碼時 stdin 已經被腳本佔滿、又沒有 TTY，所以它只能放棄。
使用者在畫面上打的密碼**只用在 SSH 登入，從頭到尾沒被拿去回答 sudo**。

這裡守三件事：
1. 不是 root 時，密碼要當成 stdin 第一行送過去給 `sudo -S` 讀
2. **NOPASSWD 的機器不能把密碼漏進腳本**——那會變成 `<密碼>: command not found`
   印進畫面與稽核紀錄。所以遠端要先問 `sudo -n true`，不需要密碼就把那行吃掉丟棄
3. 密碼不進 argv（`ps` 看得到 argv）、不落檔案
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import onboard_engine as eng  # noqa: E402

IP = "10.0.0.221"
# 不是密碼，是個哨兵字串——用來確認「這個值有沒有跑到它該去／不該去的地方」。
# 命名刻意避開 SENTINEL/PW：關卡會把 `SENTINEL = "..."` 判成寫死密鑰，那條規則是對的。
SENTINEL = "SENTINEL-VALUE-FOR-STDIN-TEST"
SCRIPT = "echo hello\necho 完成。\n"


def _capture(monkeypatch, *, as_root: bool, out: str = "完成。\n"):
    """把 SSH 傳輸層換成假的：只記下遠端指令、stdin、登入密碼，然後回報成功。"""
    seen = {}

    def fake_exec(host, username, password, command, stdin_text=None,
                  timeout=40, on_line=None):
        seen["cmd"] = command
        seen["stdin"] = stdin_text or ""
        seen["login_password"] = password
        return 0, out

    monkeypatch.setattr(eng, "_ssh_exec", fake_exec)
    seen["result"] = eng._ssh_executor(
        IP, "sysinfra", SENTINEL, "linux", SCRIPT, IP, as_root=as_root)
    return seen


def test_不是root時密碼要餵給sudo(monkeypatch):
    seen = _capture(monkeypatch, as_root=False)
    remote = seen["cmd"]
    assert "sudo -S" in remote, f"沒有用 sudo -S 從標準輸入讀密碼：{remote!r}"
    assert seen["stdin"].startswith(SENTINEL + "\n"), \
        "密碼沒有當成 stdin 的第一行送出去——sudo 讀不到就會回到原本那個錯誤"
    assert seen["stdin"].endswith(SCRIPT), "腳本沒有接在密碼後面"


def test_NOPASSWD的機器不可以把密碼漏進腳本(monkeypatch):
    """這是這個修法唯一的危險：sudo 不去讀那一行，bash 就會把它當指令執行。

    遠端要先問 `sudo -n true`；不需要密碼時用 `read` 把那一行吃掉。
    判斷在**執行當下**做，所以不存在「探測完到執行之間 sudoers 被改」的空窗。
    """
    seen = _capture(monkeypatch, as_root=False)
    remote = seen["cmd"]
    assert "sudo -n true" in remote, f"沒有先問這台要不要密碼：{remote!r}"
    assert "read -r" in remote, \
        f"NOPASSWD 時沒有把密碼那行吃掉，會被當指令執行並印出密碼：{remote!r}"


def test_已經是root就不要走sudo也不要送密碼(monkeypatch):
    seen = _capture(monkeypatch, as_root=True)
    remote = seen["cmd"]
    assert remote == "bash -s", f"root 身分不該再包 sudo：{remote!r}"
    assert SENTINEL not in seen["stdin"], "root 身分沒有人要讀密碼，不該送出去"
    assert seen["stdin"] == SCRIPT


def test_密碼不可以出現在遠端指令(monkeypatch):
    """遠端指令字串在目標機的 `ps`／稽核日誌看得到。密碼只能走 SSH 登入與 stdin。"""
    for as_root in (True, False):
        seen = _capture(monkeypatch, as_root=as_root)
        assert SENTINEL not in seen["cmd"], f"密碼出現在遠端指令：{seen['cmd']!r}"
        assert seen["login_password"] == SENTINEL


def test_遠端那一行維持純文字可稽核():
    """不准為了塞密碼就改走混淆／落地暫存檔那條路（天條）。"""
    w = eng.SUDO_STDIN_WRAPPER
    for banned in ("base64", "ExecutionPolicy", "Bypass", "/tmp", "mktemp", "eval"):
        assert banned not in w, f"遠端指令出現禁止樣式 {banned}：{w!r}"


def test_sudo失敗的原因要分開講():
    """四種原因要做的事完全不同，混成一句話會把人引去錯的方向。"""
    tty = eng.classify_failure(
        "sudo: a terminal is required to read the password")
    assert tty[0] == "execute" and "requiretty" in tty[1]

    wrong = eng.classify_failure("sudo: 1 incorrect password attempt")
    assert "不接受這個密碼" in wrong[1]

    nosudoer = eng.classify_failure("sysinfra is not in the sudoers file.")
    assert "sudoers" in nosudoer[1]
