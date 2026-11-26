"""paramiko 檔案權限錯誤要被正確判讀（2026-09-15 公司機 .14 真因）。

公司機黃底：`PermissionError: Permission denied: '.../site-packages/paramiko/__init__.py'`。
套件是 root 用嚴格 umask 裝的，服務帳號讀不到——Python 把它當 namespace 空殼，
所以前面五輪一直是「沒有 SSHClient」。

要守的：
1. 讀不到時要回報「權限錯誤」＋修法指令，不能讓整支自我檢查炸成 500
2. **讀不到的真套件絕不能被當成空目錄去改名**
3. 一鍵修復遇到權限問題要直接說明，不做任何改名
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collector_env  # noqa: E402


@pytest.fixture()
def denied_pkg(tmp_path, monkeypatch):
    pkg = tmp_path / "paramiko"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    real_exists = Path.exists

    def fake_exists(self, *a, **k):
        if self.name == "__init__.py" and self.parent == pkg:
            raise PermissionError(13, "Permission denied", str(self))
        return real_exists(self, *a, **k)

    monkeypatch.setattr(Path, "exists", fake_exists)
    saved = {m: mod for m, mod in sys.modules.items() if m == "paramiko" or m.startswith("paramiko.")}
    try:
        yield pkg
    finally:
        sys.path.remove(str(tmp_path))
        for name in [m for m in list(sys.modules) if m == "paramiko" or m.startswith("paramiko.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def test_讀不到要回報權限錯誤_不能炸(denied_pkg):
    out = collector_env.selfcheck()          # 以前這裡直接丟 PermissionError → 500
    assert str(denied_pkg) in out["permission_denied_paths"]
    assert "權限" in out["reason"]
    assert "chmod -R a+rX" in out["fix_command"]


def test_讀不到的真套件不能被當成空目錄(denied_pkg):
    out = collector_env.selfcheck()
    assert str(denied_pkg) not in out["broken_paths"]


def test_一鍵修復遇到權限問題不改名(denied_pkg):
    r = collector_env.selfheal()
    assert r["renamed"] == [] and r["ok"] is False
    assert "權限" in r["message"]
    assert denied_pkg.exists.__self__ if False else True   # 目錄還在（沒被改名）
    assert (denied_pkg.parent / "paramiko").is_dir()
