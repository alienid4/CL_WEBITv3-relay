"""守門：全套通過證明（.project/pass_stamp.py）只在「測的就是這一份」時才被沿用。

這張證明讓 221 部署時不用再跑一次 10 分鐘的全套（PC 已經跑過同一份）。
它一旦被錯誤沿用，就等於「沒過全套的碼進了正式機」——所以這裡釘住每一種該拒絕的情況：
  · 內容改了（多一個 commit）→ 拒絕、跑全套
  · 工作區有未提交改動 → 拒絕（PC 端也不發證明）
  · 證明裡有失敗／Python 主版本不同／格式不對 → 拒絕
  · 強制旗標 → 拒絕
以及唯一該接受的情況：內容完全相同 → 接受，且 PC 沒實際跑過的（skip）列進補跑清單。
"""
from __future__ import annotations

import importlib.util
import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".project"))

from git_env import clean_git_env  # noqa: E402


def _load():
    spec = importlib.util.spec_from_file_location("pass_stamp", ROOT / ".project" / "pass_stamp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ps = _load()


def _git_ok() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _git_ok(), reason="這台沒有 git")


def _g(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
                        "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
                       capture_output=True, text=True, env=clean_git_env())
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """最小的 repo：APP/、tests/（一條會過、一條會 skip）、.project/。"""
    (tmp_path / "APP").mkdir()
    (tmp_path / "APP" / "x.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(
        "import pytest\n"
        "def test_ok():\n    assert True\n"
        "@pytest.mark.skip(reason='只在 Linux')\n"
        "def test_linux_only():\n    assert True\n", encoding="utf-8")
    (tmp_path / ".project").mkdir()
    (tmp_path / ".project" / "note.txt").write_text("n\n", encoding="utf-8")
    _g(tmp_path, "init", "-q", "-b", "main")
    _g(tmp_path, "add", "APP", "tests", ".project")
    _g(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def _stamp(repo: Path, **over) -> dict:
    key = ps.key_of(ps.trees(repo))
    s = {"schema": ps.SCHEMA, "key": key, "status": "passed", "exitstatus": 0,
         "counts": {"passed": 1, "skipped": 1, "failed": 0, "error": 0},
         "python": platform.python_version(), "commit": "abc", "host": "pc",
         "finished_at": "2026-10-19T08:00:00+08:00", "deps": {},
         "passed": ["tests/test_a.py::test_ok"]}
    s.update(over)
    return s


def _write(d: Path, s: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{s['key']}.json").write_text(json.dumps(s), encoding="utf-8")


def test_內容鍵_改一個字就不同(repo: Path):
    k1 = ps.key_of(ps.trees(repo))
    assert k1 == ps.key_of(ps.trees(repo))          # 同一份 → 同一把
    (repo / "APP" / "x.py").write_text("X = 2\n", encoding="utf-8")
    _g(repo, "commit", "-qam", "change")
    assert ps.key_of(ps.trees(repo)) != k1


def test_只改文件_AI資料夾_內容鍵不變(repo: Path):
    k1 = ps.key_of(ps.trees(repo))
    (repo / "AI").mkdir()
    (repo / "AI" / "交接.md").write_text("x\n", encoding="utf-8")
    _g(repo, "add", "AI")
    _g(repo, "commit", "-qm", "docs")
    assert ps.key_of(ps.trees(repo)) == k1


def test_相符_接受_且skip的列進補跑(repo: Path, tmp_path_factory, monkeypatch):
    monkeypatch.delenv("WEBIT_FORCE_TESTS", raising=False)
    monkeypatch.delenv("REQUIRE_TESTS", raising=False)
    d = tmp_path_factory.mktemp("stamps")
    _write(d, _stamp(repo))
    rem = d / "remain.txt"
    assert ps.cmd_check(repo, d, rem) == 0
    assert rem.read_text(encoding="utf-8").split() == ["tests/test_a.py::test_linux_only"]


def test_內容不同_拒絕(repo: Path, tmp_path_factory, monkeypatch):
    monkeypatch.delenv("WEBIT_FORCE_TESTS", raising=False)
    d = tmp_path_factory.mktemp("stamps")
    _write(d, _stamp(repo))                       # 證明是舊內容的
    (repo / "APP" / "x.py").write_text("X = 3\n", encoding="utf-8")
    _g(repo, "commit", "-qam", "new")             # 部署的是新內容
    assert ps.cmd_check(repo, d, None) == 2


def test_檔名對但內容鍵被改過_拒絕(repo: Path, tmp_path_factory, monkeypatch):
    """有人把別份的證明改名成這一份的檔名：內容裡的 key 對不上就不收。"""
    monkeypatch.delenv("WEBIT_FORCE_TESTS", raising=False)
    d = tmp_path_factory.mktemp("stamps")
    s = _stamp(repo)
    real = s["key"]
    s["key"] = "0" * 40
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{real}.json").write_text(json.dumps(s), encoding="utf-8")
    assert ps.cmd_check(repo, d, None) == 2


def test_工作區有改動_拒絕(repo: Path, tmp_path_factory, monkeypatch):
    monkeypatch.delenv("WEBIT_FORCE_TESTS", raising=False)
    d = tmp_path_factory.mktemp("stamps")
    _write(d, _stamp(repo))
    (repo / "APP" / "x.py").write_text("X = 9\n", encoding="utf-8")   # 沒 commit
    assert ps.cmd_check(repo, d, None) == 2


def test_多一個未追蹤的測試檔_拒絕(repo: Path, tmp_path_factory, monkeypatch):
    monkeypatch.delenv("WEBIT_FORCE_TESTS", raising=False)
    d = tmp_path_factory.mktemp("stamps")
    _write(d, _stamp(repo))
    (repo / "tests" / "test_new.py").write_text("def test_x():\n    assert 0\n", encoding="utf-8")
    assert ps.cmd_check(repo, d, None) == 2


def test_強制旗標_拒絕(repo: Path, tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("stamps")
    _write(d, _stamp(repo))
    monkeypatch.setenv("WEBIT_FORCE_TESTS", "1")
    assert ps.cmd_check(repo, d, None) == 2


@pytest.mark.parametrize("over, why", [
    ({"status": "failed"}, "狀態"),
    ({"exitstatus": 1}, "狀態"),
    ({"counts": {"passed": 10, "skipped": 0, "failed": 1, "error": 0}}, "失敗"),
    ({"counts": {"passed": 0, "skipped": 0, "failed": 0, "error": 0}}, "失敗"),
    ({"python": "2.7.18"}, "Python"),
    ({"schema": 999}, "格式"),
])
def test_證明本身不合格_拒絕(repo: Path, over, why):
    s = _stamp(repo, **over)
    reason = ps.validate(s, s["key"])
    assert reason and why in reason


def test_PC端_工作區髒_不發證明也不跑測試(repo: Path, tmp_path_factory, monkeypatch):
    called = []
    monkeypatch.setattr(ps, "run_pytest", lambda *a, **k: called.append(1) or (0, {}))
    (repo / "tests" / "test_a.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    out = tmp_path_factory.mktemp("out")
    assert ps.cmd_run(repo, out, None) == 2
    assert not called and not list(out.glob("*.json"))


def test_PC端_全過才發證明_有失敗不發(repo: Path, tmp_path_factory, monkeypatch):
    monkeypatch.delenv("WEBIT_FORCE_TESTS", raising=False)
    out = tmp_path_factory.mktemp("out")
    monkeypatch.setattr(ps, "run_pytest", lambda *a, **k: (1, {"tests/test_a.py::test_ok": "failed"}))
    assert ps.cmd_run(repo, out, None) == 1
    assert not list(out.glob("*.json"))
    monkeypatch.setattr(ps, "run_pytest", lambda *a, **k: (0, {
        "tests/test_a.py::test_ok": "passed", "tests/test_a.py::test_linux_only": "skipped"}))
    assert ps.cmd_run(repo, out, None) == 0
    got = json.loads((out / f"{ps.key_of(ps.trees(repo))}.json").read_text(encoding="utf-8"))
    assert got["passed"] == ["tests/test_a.py::test_ok"] and got["status"] == "passed"


def test_外掛_真的跑pytest_記下逐條結果(repo: Path, tmp_path_factory, monkeypatch):
    """不 mock：用本外掛實跑一次這個小 repo，確認 passed／skipped 分得出來。"""
    monkeypatch.setattr(ps, "_xdist_args", lambda: [])
    rep = tmp_path_factory.mktemp("rep") / "r.json"
    rc, res = ps.run_pytest(repo, ["tests"], rep)
    assert rc == 0
    assert res == {"tests/test_a.py::test_ok": "passed", "tests/test_a.py::test_linux_only": "skipped"}
