"""core.hooksPath 判定：值要能指到「這個 repo 的 .project/hooks」才算接上。

2026-10-18、10-20 兩次：共用的 .git/config 裡 core.hooksPath 被改成絕對路徑
（`C:\\AiProject\\CL_WEBITv3\\.project\\hooks`），寫的人查不到（不是 AI_note 的同步腳本、
也不是 ~/.claude 的 hook）。checks.py 原本只認字面 `.project/hooks`，於是擋下所有 commit，
每次都要人手改回相對路徑——但絕對路徑本身其實有接上關卡，擋它是假警報。

規則（只認「真的指到本 repo 的 hooks 資料夾」，其他一律不認）：
- 相對路徑 `.project/hooks`（git 對每個 worktree 都從該 worktree 根目錄解析）→ 接上
- 絕對路徑：解析後等於「目前 worktree 根目錄/.project/hooks」或「主 checkout
  （git common dir 的上一層）/.project/hooks」→ 接上。Git Bash 形式 `/c/...` 也認。
- 空值、別的相對路徑、指到別的資料夾（含 /dev/null）→ 沒接上
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_MSYS = re.compile(r"^/([a-zA-Z])/(.*)$")


def _to_native(value: str) -> str:
    """Git Bash 的 /c/foo 在 Windows 上換成 C:/foo；其他平台原樣。"""
    m = _MSYS.match(value)
    if m and os.name == "nt":
        return f"{m.group(1).upper()}:/{m.group(2)}"
    return value


def _norm(p: Path) -> str:
    try:
        p = p.resolve()
    except OSError:
        pass
    return os.path.normcase(os.path.normpath(str(p)))


def hooks_path_ok(value: str, root: Path, common_dir: Path | None = None) -> bool:
    """value＝`git config core.hooksPath` 的輸出；root＝目前 worktree 根目錄；
    common_dir＝`git rev-parse --git-common-dir` 的絕對路徑（主 checkout 的 .git）。"""
    v = (value or "").strip()
    if not v:
        return False
    slashed = v.replace("\\", "/").rstrip("/")
    if slashed in (".project/hooks", "./.project/hooks"):
        return True
    native = _to_native(slashed)
    p = Path(native)
    if not p.is_absolute():
        return False
    targets = {_norm(Path(root) / ".project" / "hooks")}
    if common_dir is not None:
        targets.add(_norm(Path(common_dir).parent / ".project" / "hooks"))
    return _norm(p) in targets
