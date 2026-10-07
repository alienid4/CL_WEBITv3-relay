"""收集端 paramiko 自我檢查／自我修復（2026-09-15 公司機 .14）。

現象：套到 v1.174.0 還是「paramiko 不完整、缺 SSHClient」。原因是 paramiko 被**同名空目錄**
（沒有 __init__.py 的 namespace package）蓋掉，而 patch 只掃 $APP/backend 與 $APP 兩處。

要守的：
1. 檢查要回「sys.path 上所有叫 paramiko 的東西」，壞的要標出來——位置要看得到
2. 修復只改名「目錄但沒有 __init__.py」的空殼，**不刪除**
3. **有 __init__.py 的真套件一律不碰**（那是安裝殘缺，要重裝，改名只會更糟）
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collector_env  # noqa: E402


def _pop_paramiko() -> dict:
    """把已載入的 paramiko 模組**存起來再移除**，回傳快照給之後還原。

    ⚠️ 不可以只 pop 不還原：別的測試（test_onboard_ssh_transport）在 import 時就拿到
    paramiko 模組物件並在上面 monkeypatch，被害者程式若之後才 import 會拿到**另一個**
    新載入的模組物件——patch 就失效，測試會真的去連 10.0.0.9。
    2026-09-15 就是這樣讓 221 全套紅了 4 條（本機 Windows 跳過這些 POSIX 測試，看不出來）。
    """
    saved = {m: mod for m, mod in sys.modules.items()
             if m == "paramiko" or m.startswith("paramiko.")}
    for name in saved:
        sys.modules.pop(name, None)
    return saved


def _restore_paramiko(saved: dict) -> None:
    for name in [m for m in list(sys.modules) if m == "paramiko" or m.startswith("paramiko.")]:
        sys.modules.pop(name, None)
    sys.modules.update(saved)


@pytest.fixture()
def shadow_dir(tmp_path):
    """在 sys.path 最前面放一個 paramiko 空目錄（重現公司機的狀況）。"""
    (tmp_path / "paramiko").mkdir()
    sys.path.insert(0, str(tmp_path))
    saved = _pop_paramiko()
    try:
        yield tmp_path
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        _restore_paramiko(saved)


def test_檢查要指出空目錄在哪(shadow_dir):
    out = collector_env.selfcheck()
    assert str(shadow_dir / "paramiko") in out["broken_paths"], "壞的位置要講出來，不然只能猜"
    assert out["cwd"] and out["python"]


def test_修復把空目錄改名而不是刪掉(shadow_dir):
    r = collector_env.selfheal()
    assert r["renamed"], r["message"]
    src = Path(r["renamed"][0]["from"])
    dst = Path(r["renamed"][0]["to"])
    assert not src.exists() and dst.exists(), "要改名備份，不可以刪除"
    assert collector_env.BACKUP_SUFFIX in dst.name


def test_真套件不碰(tmp_path):
    """有 __init__.py 的是真套件：安裝殘缺要重裝，改名只會讓它連 import 都不成。"""
    pkg = tmp_path / "paramiko"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    saved = _pop_paramiko()
    try:
        cands = {c["path"]: c for c in collector_env.selfcheck()["candidates"]}
        assert cands[str(pkg)]["broken"] is False
        r = collector_env.selfheal()
        assert pkg.exists() and (pkg / "__init__.py").exists()
        assert all(x["from"] != str(pkg) for x in r["renamed"])
    finally:
        sys.path.remove(str(tmp_path))
        _restore_paramiko(saved)


def test_沒問題時說本來就是好的():
    saved = dict((m, mod) for m, mod in sys.modules.items()
                 if m == "paramiko" or m.startswith("paramiko."))
    try:
        out = collector_env.selfcheck()
        if out["ok"]:
            assert collector_env.selfheal()["message"].startswith("本來就是好的")
    finally:
        _restore_paramiko(saved)
