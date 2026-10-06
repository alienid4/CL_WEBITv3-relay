"""agent_scripts 的資安迴歸守門。

## 為什麼要有這支

2026-08-28 修掉一個**真的本機提權漏洞**：root 排程去掃全域可寫的
`/tmp/webit3_agent_*`，把裡面上傳上來的 `push_agent.sh` 用 root 裝進 `/opt` 755、
排進持有全機隊 SSH 金鑰的帳號的 crontab。任何本機帳號都能照著這條路徑讓 root
幫他裝任意程式。詳見 `backend/agent_scripts/install.sh` 開頭與 README。

修好不等於修完——這種東西**會被改回去**：某天有人為了「方便測試」把投放目錄
改回 `/tmp`、或為了「支援自訂收集項目」讓 task_dir 又能帶腳本上來。這支測試就是
把那條線釘死，改回去就紅。

同一份精神也套用在 SOC 那組樣式（`CLAUDE.md` 天條）：`ExecutionPolicy Bypass`、
base64 混淆落地執行、`StrictHostKeyChecking=no`。那些不是風格問題，是會讓使用者
被 SOC 當成事件當事人去解釋的東西，所以一併釘住整個 `APP/` 樹。
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "APP" / "asset-module" / "backend" / "agent_scripts"
APP = ROOT / "APP"


def _read(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 一、提權漏洞本身
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["run_auto_web3.sh", "bootstrap_watcher.sh"])
def test_root_排程不得掃描全域可寫的_tmp(name):
    """root 撿 /tmp 底下的東西來處理 = 任何本機帳號都能餵它東西。

    只擋「拿 /tmp 當投放目錄」，不擋註解裡提到 /tmp——那些註解正是在說明
    為什麼不能這樣做，刪掉反而讓下一個人不知道這條線為什麼存在。
    """
    code = [ln for ln in _read(name).splitlines() if not ln.lstrip().startswith("#")]
    for ln in code:
        assert "/tmp/webit3" not in ln, f"{name} 又把投放目錄放回 /tmp：{ln.strip()}"
        assert "INCOMING_DIR" not in ln or "/tmp" not in ln, \
            f"{name} 的 INCOMING_DIR 指到 /tmp：{ln.strip()}"


@pytest.mark.parametrize("name", ["install.sh", "bootstrap_watcher.sh"])
def test_agent_程式碼不得取自投放目錄(name):
    """root 只執行隨佈署固定下來的程式碼，外部只能傳資料。"""
    src = _read(name)
    assert '"$TASK_DIR/push_agent.sh"' not in src, \
        f"{name} 又從投放目錄取得 push_agent.sh——那是可執行內容，不是資料"
    assert '$BIN_DIR/push_agent.sh' in src, \
        f"{name} 沒有從佈署路徑取得 push_agent.sh"


def test_投放目錄裡出現腳本要被當成攻擊跡象拒絕():
    src = _read("install.sh")
    assert "push_agent.sh)" in src, "install.sh 沒有針對 push_agent.sh 的拒絕分支"
    # 白名單而不是黑名單：只有這三個名字放行
    assert "agent_key|collector_url|.ready)" in src, \
        "install.sh 的檔名檢查不是白名單——黑名單擋不住沒想到的檔名"


def test_資料檔要驗格式不是只檢查存在():
    src = _read("install.sh")
    assert "[A-Za-z0-9_-]{16,128}" in src, "agent_key 沒有驗格式"
    assert "^https?://" in src, "collector_url 沒有驗格式"
    assert "-L " in src, "沒有擋 symlink——那是最基本的一步"


def test_投放目錄權限不對要整輪拒絕():
    """比對的是 case 的**分支條件**，不是訊息裡有沒有提到 root:730。

    第一版寫成 `assert "root:730" in src` 就漏抓了——那個字串在錯誤訊息裡也有一份，
    把分支條件放寬成 `*)` 之後測試照樣綠。守門測試自己也要被驗過會紅。
    """
    src = _read("run_auto_web3.sh")
    assert re.search(r"^\s*root:730(\|[a-z0-9:]+)*\)", src, re.M), \
        ("run_auto_web3.sh 沒有在掃描前檢查投放目錄權限是 root:730。"
         "前提壞了就不該挑幾個看起來沒問題的任務做")
    assert "INC_STAT" in src, "沒有真的去 stat 投放目錄"


# --------------------------------------------------------------------------
# 二、bootstrap 是產生物，必須跟正本同步
# --------------------------------------------------------------------------

def test_bootstrap_與正本同步():
    """同一段程式碼存在兩份 → 只改一份的話 git diff 看起來修好了，
    實際佈到主機上的還是舊版。「看起來修好、實際沒修」比沒修更危險。
    """
    r = subprocess.run([sys.executable, str(SCRIPTS / "build_bootstrap.py"), "--check"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, (
        f"bootstrap_watcher.sh 與正本不同步，請跑："
        f"python {SCRIPTS / 'build_bootstrap.py'}\n{r.stdout}{r.stderr}")


def test_bootstrap_必須是_LF():
    """這份要在 Linux/AIX 上跑；CRLF 會讓 `#!/bin/sh` 直接壞掉，
    而且錯誤訊息（`: 未預期的...`）完全看不出原因。"""
    assert b"\r\n" not in (SCRIPTS / "bootstrap_watcher.sh").read_bytes()


# --------------------------------------------------------------------------
# 三、SOC 樣式（CLAUDE.md 天條）——釘住整個 APP/ 樹
# --------------------------------------------------------------------------

#: 樣式 → 為什麼禁（訊息會直接印給改壞的人看，所以要寫出替代做法）
FORBIDDEN = {
    "StrictHostKeyChecking=no":
        "關掉主機金鑰驗證＝自願接受中間人攻擊，稽核必開缺失。"
        "改用 onboard_engine.SSH_HOSTKEY_OPTS（accept-new ＋ 明確的 known_hosts）",
    "ExecutionPolicy Bypass":
        "字面上就是 Bypass，對應 MITRE T1562.001 削弱防禦。"
        "腳本走 stdin（powershell -NoProfile -NonInteractive -Command -）不受執行原則約束，"
        "本來就不需要這個旗標",
    "| base64 -d":
        "base64 混淆後餵給 shell 是 T1027+T1059.001，勒索軟體投放的標準動作。"
        "腳本走 stdin 送純文字，可讀可稽核",
    "openssl base64 -d":
        "同上（AIX 版本）",
}

SKIP_DIRS = {"node_modules", ".nuxt", ".output", "dist", "__pycache__", ".venv"}
SCAN_EXT = {".py", ".sh", ".ps1", ".vue", ".ts", ".js", ".yml", ".yaml"}


def _scan_files():
    for p in APP.rglob("*"):
        if p.suffix not in SCAN_EXT or not p.is_file():
            continue
        if SKIP_DIRS & set(p.parts):
            continue
        yield p


def _code_hits(path: Path, pattern: str) -> list[str]:
    """找出 `pattern` 出現在**實際執行的程式碼**裡的位置。

    註解與 docstring 裡提到這些樣式，通常正是在解釋「為什麼不能這樣寫」——
    那是資產不是缺失，砍掉只會讓下一個人不知道這條線為什麼存在。
    但**一般字串**要算：真正的違規長得就是 `"-o", "StrictHostKeyChecking=no"`，
    整段掠過字串等於這條規則什麼都抓不到。

    .py 用 tokenize 精確分辨 docstring（獨立成一句的字串）與參數字串；
    其他副檔名沒有可靠的 parser，退回逐行看註解前綴。
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    if path.suffix == ".py":
        import io as _io
        import token as _tok
        import tokenize as _tokenize

        hits, prev = [], _tok.NEWLINE
        try:
            for t in _tokenize.generate_tokens(_io.StringIO(text).readline):
                if t.type in (_tok.COMMENT, _tok.NL, _tok.NEWLINE,
                              _tok.INDENT, _tok.DEDENT):
                    if t.type != _tok.COMMENT:
                        prev = t.type
                    continue
                is_docstring = (t.type == _tok.STRING and
                                prev in (_tok.NEWLINE, _tok.INDENT, _tok.DEDENT))
                if not is_docstring and pattern in t.string:
                    hits.append(f"{path.relative_to(ROOT)}:{t.start[0]}: "
                                f"{lines[t.start[0] - 1].strip()}")
                prev = t.type
            return hits
        except (_tokenize.TokenError, IndentationError, SyntaxError):
            pass    # 解析不了就退回逐行，寧可誤報也不要漏報

    return [f"{path.relative_to(ROOT)}:{i}: {ln.strip()}"
            for i, ln in enumerate(lines, 1)
            if pattern in ln and not ln.lstrip().startswith(("#", "//", "*", "·"))]


@pytest.mark.parametrize("pattern", sorted(FORBIDDEN))
def test_不得出現_SOC_告警樣式(pattern):
    hits = [h for p in _scan_files() for h in _code_hits(p, pattern)]
    assert not hits, (
        f"出現禁止樣式 `{pattern}`——{FORBIDDEN[pattern]}\n" + "\n".join(hits))
