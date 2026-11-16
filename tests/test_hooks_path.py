"""守門：core.hooksPath 的判定（.project/hooks_path.py）。

2026-10-20：共用 .git/config 的 core.hooksPath 反覆被改成絕對路徑，checks.py 只認字面
`.project/hooks` → 假警報擋下所有 commit。改成「解析後指到本 repo 的 .project/hooks 就算接上」，
但不可以因此放行指到別處的值（那才是真的沒被攔檢）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".project"))

from hooks_path import hooks_path_ok  # noqa: E402


def _mk(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name
    (d / ".project" / "hooks").mkdir(parents=True)
    (d / ".git").mkdir()
    return d


def test_relative_forms_ok(tmp_path):
    root = _mk(tmp_path, "repo")
    assert hooks_path_ok(".project/hooks", root)
    assert hooks_path_ok("./.project/hooks/", root)
    assert hooks_path_ok(".project\\hooks", root)


def test_absolute_to_this_repo_ok(tmp_path):
    root = _mk(tmp_path, "repo")
    assert hooks_path_ok(str(root / ".project" / "hooks"), root)
    # Windows 反斜線＋結尾斜線
    assert hooks_path_ok(str(root / ".project" / "hooks") + os.sep, root)


def test_absolute_to_main_checkout_from_worktree_ok(tmp_path):
    main = _mk(tmp_path, "main")
    wt = _mk(tmp_path, "wt")
    # worktree 裡：值是主 checkout 的絕對路徑，common dir 是主 checkout 的 .git
    assert hooks_path_ok(str(main / ".project" / "hooks"), wt, main / ".git")
    # 沒給 common dir 就只認目前 worktree
    assert not hooks_path_ok(str(main / ".project" / "hooks"), wt)


@pytest.mark.skipif(os.name != "nt", reason="Git Bash /c/... 形式只在 Windows 有意義")
def test_msys_form_ok_on_windows(tmp_path):
    root = _mk(tmp_path, "repo")
    p = str(root / ".project" / "hooks")
    msys = "/" + p[0].lower() + p[2:].replace("\\", "/")
    assert hooks_path_ok(msys, root)


def test_not_connected_values_rejected(tmp_path):
    root = _mk(tmp_path, "repo")
    other = _mk(tmp_path, "other")
    assert not hooks_path_ok("", root)
    assert not hooks_path_ok("   ", root)
    assert not hooks_path_ok(".githooks", root)
    assert not hooks_path_ok("hooks", root)
    assert not hooks_path_ok("/dev/null", root)
    assert not hooks_path_ok(str(other / ".project" / "hooks"), root)
    assert not hooks_path_ok(str(root / ".project"), root)
    assert not hooks_path_ok(str(root / ".project" / "hooks" / "pre-commit"), root)
