"""守門：在別的目錄建／操作 repo 的 git 呼叫，不可以繼承 GIT_DIR。

**這支在擋什麼**（2026-10-13 真的發生，交辦單第 0 項）：
git 跑 hook 時會在環境裡塞 `GIT_DIR` 指向真 repo。`.project/checks.py` 的
`_relay_source_sha()` 在暫存目錄 `git init` 去問 relay 進度，沒清掉 GIT_DIR，
於是那個 init 是**對真 repo 做**，把主 repo 設成 `core.bare=true`。
症狀：commit 完 `git status` 變成 "this operation must be run in a work tree"，
當天每 commit 一次就要手動 `git config core.bare false` 救一次。

所以這裡**不是**測 git，是測「我們有沒有把環境清乾淨」。
下面第二支是反證（故意不清），證明第一支抓得到，不是永遠綠的裝飾品。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".project"))

from git_env import INHERITED_GIT_VARS, clean_git_env  # noqa: E402

#: 會在暫存目錄／別的 cwd 操作 repo 的腳本，全部都要清環境。
SCRIPTS = ("checks.py", "relay_local.py", "sync_from_221.py")


def _git_ok() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _git_ok(), reason="這台沒有 git")


def _core_bare(repo: Path) -> str:
    r = subprocess.run(["git", "-C", str(repo), "config", "--get", "core.bare"],
                       capture_output=True, encoding="utf-8", env=clean_git_env())
    return (r.stdout or "").strip()


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True,
                   capture_output=True, env=clean_git_env())
    return path


def test_clean_git_env_拿掉所有_repo_指向變數():
    fake = {v: "/somewhere/.git" for v in INHERITED_GIT_VARS}
    fake["PATH"] = os.environ.get("PATH", "")
    out = clean_git_env(fake)
    assert not [v for v in INHERITED_GIT_VARS if v in out], "還有漏掉的變數"
    assert out["PATH"] == fake["PATH"], "不該把無關的環境變數也清掉"
    assert "GIT_DIR" not in clean_git_env(), "預設要讀 os.environ 並清乾淨"


def test_設了GIT_DIR時_在暫存目錄_init_不會波及真repo(tmp_path, monkeypatch):
    real = _make_repo(tmp_path / "real")
    assert _core_bare(real) in ("false", ""), "前置條件：真 repo 一開始不是 bare"

    monkeypatch.setenv("GIT_DIR", str(real / ".git"))  # 重現 hook 的環境
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "-C", str(work), "init", "-q"], check=True,
                   capture_output=True, env=clean_git_env())

    assert _core_bare(real) in ("false", ""), "真 repo 被改成 bare 了——環境沒清乾淨"
    assert (work / ".git").exists(), "暫存目錄自己那份 repo 沒建起來"


def test_反證_不清環境真的會把真repo改掉(tmp_path, monkeypatch):
    """故意不清環境，證明上面那支守門不是裝飾品。重現不出來就 skip，不要假紅。"""
    real = _make_repo(tmp_path / "real")
    monkeypatch.setenv("GIT_DIR", str(real / ".git"))
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "-C", str(work), "init", "-q"],
                   capture_output=True, env=os.environ.copy())  # ← 繼承，就是當初的寫法

    if _core_bare(real) in ("false", ""):
        pytest.skip("這個 git 版本沒重現 GIT_DIR 汙染；上面的守門仍然有效")
    assert _core_bare(real) == "true"


@pytest.mark.parametrize("name", SCRIPTS)
def test_會操作別的repo的腳本_都有清環境(name):
    path = ROOT / ".project" / name
    if not path.exists():
        # sync_from_221.py 不進 relay（make_relay 排除），relay 產出物上跑時就沒有這支
        pytest.skip(f"{name} 不在這份樹裡（relay 產出物）")
    src = path.read_text(encoding="utf-8")
    assert "clean_git_env" in src, f"{name} 沒有用 git_env.clean_git_env()"


def test_relay_source_sha_三個git呼叫都帶了env():
    """釘在呼叫點上：有人日後把 env= 拿掉，這裡要紅。"""
    src = (ROOT / ".project" / "checks.py").read_text(encoding="utf-8")
    start = src.index("def _relay_source_sha")
    body = src[start:src.index("\ntry:", start)]
    assert body.count('"-C", d') == 3, "暫存 repo 的 git 呼叫數量變了，請一起確認 env"
    assert body.count("env=env") == 3, "三個 git 呼叫要全部帶 env=env（清過 GIT_DIR 的）"
    assert "clean_git_env()" in body
