"""baseline 的前端清單對不上時，一定要自動走「完整前端包」。

── 這是在防什麼 ──────────────────────────────────────────────────────────
打 patch 忘了帶 `--full-frontend` → 增量前端包少帶檔 → 目標機 `patch.sh` 卡在
`[2.5/5]` 的清單驗證（「缺 N 檔」）然後 `exit 1`。而那一步**在停服務與重啟之前**，
所以結果是「版號跳了、服務沒重啟」這種看不出來的失敗。公司機因此跑了
2026-09-30 ~ 10-16 兩週的舊前端，沒有人發現。

根因：`.project/frontend_manifest.json` 是 `make_patch` 每次 build 完覆寫的，
卻是**順手**跟著某一次 commit 進版控的，跟 `release_baseline` 前進的時機從來沒
對齊過。實際查證：baseline `733ec45`（v1.383.0），`git show 733ec45:...` 拿到的
卻是 `fb7b004`（v1.378.0）那次 build 留下的清單——中間差了 5 個版的前端。

修法：清單自己記下「是哪一次 build（commit）產的」，`baseline_manifest_trust()`
驗證它描述的就是 baseline 那一版前端；自證不了就回空清單＋一行理由，
打包流程據此自動改送完整包。**失敗方向固定是「多送檔」。**

這裡不只讀程式碼，是**真的開一個小 git repo** 把各種對不上的情況跑一遍。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".project"))

try:
    import make_patch as mp
    from git_env import INHERITED_GIT_VARS, clean_git_env
except ImportError:  # pragma: no cover - relay 快照裡沒有 make_patch
    pytest.skip("make_patch 不在 relay 快照裡，本測試只在主 repo 執行",
                allow_module_level=True)

#: ⚠️ 這支測試會在暫存目錄開 git repo，**每一個 git 呼叫都必須帶這份環境**。
#:
#: 為什麼：pre-commit hook 跑測試時，環境裡有 `GIT_DIR` 指向**真 repo**。
#: 沒清掉的話，`git init <暫存目錄>` 不是對暫存目錄做，而是把真 repo 翻成
#: `core.bare=true`；`git commit` 更會把測試造的 commit 推到**真分支**上。
#: 2026-10-16 寫這支測試時實際踩到一次：分支 HEAD 被換成三個名叫
#: 「無清單／舊格式／不存在」的 commit，index 整個被清空。
#: 理由與清單見 `.project/git_env.py`。
_ENV = clean_git_env()


# ===== 迷你 repo：在裡面重現「清單跟 baseline 對不上」的各種樣子 =====

def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], env=_ENV,
                       capture_output=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, f"git {' '.join(args)} 失敗：{r.stderr}"
    return r.stdout.strip()


def _write_manifest(repo: Path, files: dict, commit: str | None) -> None:
    doc: dict = {}
    if commit is not None:
        doc["commit"] = commit
    doc["files"] = files
    p = repo / ".project" / "frontend_manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def _commit(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """一個只有「前端原始碼 ＋ 前端清單」的迷你 repo，REPO 指過去。"""
    # make_patch 自己的 git 呼叫只給 cwd＝REPO，沒帶 env；在 hook 環境下
    # GIT_DIR 會讓它**無視 cwd** 去讀真 repo。所以這裡直接把那幾個變數從
    # os.environ 拔掉，整個 test 期間誰呼叫 git 都只看得到暫存 repo。
    for _var in INHERITED_GIT_VARS:
        monkeypatch.delenv(_var, raising=False)

    d = tmp_path / "mini"
    (d / "APP" / "asset-module" / "frontend" / "pages").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(d)], env=_ENV,
                   check=True, capture_output=True)
    (d / "APP/asset-module/frontend/pages/adopt.vue").write_text("v1\n", encoding="utf-8")
    monkeypatch.setattr(mp, "REPO", d)
    return d


def test_清單記的_build_commit_就是_baseline_時可信(repo):
    """同一個 commit 既 build 又當 baseline——這是唯一完全沒有疑義的情況。"""
    _write_manifest(repo, {"a.js": "aaa"}, commit="HEAD_PLACEHOLDER")
    sha = _commit(repo, "init")
    # commit 欄要填真的 sha，所以寫好再補一次
    _write_manifest(repo, {"a.js": "aaa"}, commit=sha)
    sha2 = _commit(repo, "stamp")

    files, why = mp.baseline_manifest_trust(sha2)
    assert why == "", f"不該判成不可信：{why}"
    assert files == {"a.js": "aaa"}


def test_build_之後前端又改過_就不可信_要走完整包(repo):
    """**這就是公司機那兩週的情況**：清單是 v1.378 build 的，baseline 是 v1.383。"""
    _write_manifest(repo, {"a.js": "aaa"}, commit="x")
    build_sha = _commit(repo, "build v1")
    _write_manifest(repo, {"a.js": "aaa"}, commit=build_sha)
    build_sha = _commit(repo, "stamp v1")

    # 之後前端又改了，然後才把這個 commit 定為 baseline（清單沒跟著重出）
    (repo / "APP/asset-module/frontend/pages/adopt.vue").write_text("v2\n", encoding="utf-8")
    baseline = _commit(repo, "前端又改了，但清單沒重出")

    files, why = mp.baseline_manifest_trust(baseline)
    assert files == {}, "清單描述的不是 baseline 那一版前端，不可以拿來算差集"
    assert "前端又改了" in why or "又改了" in why, why


def test_舊格式沒有_commit_欄_一律不可信(repo):
    """沒有記錄就無從自證。猜錯的代價是少送檔，所以寧可每次都送完整包。"""
    _write_manifest(repo, {"a.js": "aaa"}, commit=None)
    baseline = _commit(repo, "舊格式清單")

    files, why = mp.baseline_manifest_trust(baseline)
    assert files == {}
    assert "舊格式" in why, why


def test_baseline_那個_commit_根本沒有清單(repo):
    (repo / "APP/asset-module/frontend/pages/adopt.vue").write_text("v1\n", encoding="utf-8")
    baseline = _commit(repo, "沒有清單")
    files, why = mp.baseline_manifest_trust(baseline)
    assert files == {} and "沒有前端清單" in why, why


def test_清單記的_commit_不在_baseline_的歷史裡(repo):
    """另一條分支 build 出來的清單，不能拿來當這條線的基準。"""
    _write_manifest(repo, {"a.js": "aaa"}, commit="x")
    first = _commit(repo, "init")
    _git(repo, "checkout", "-q", "-b", "other")
    (repo / "APP/asset-module/frontend/pages/other.vue").write_text("o\n", encoding="utf-8")
    off_line = _commit(repo, "另一條線上的 build")

    _git(repo, "checkout", "-q", "-")
    _write_manifest(repo, {"a.js": "aaa"}, commit=off_line)
    baseline = _commit(repo, "清單指向另一條線")

    files, why = mp.baseline_manifest_trust(baseline)
    assert files == {} and "歷史裡" in why, why


def test_清單記的_commit_在這個_repo_裡找不到(repo):
    """CI 淺複製／對方 rebase 掉了——查不到就當不可信，不要瞎猜。"""
    _write_manifest(repo, {"a.js": "aaa"}, commit="0" * 40)
    baseline = _commit(repo, "指向不存在的 commit")
    files, why = mp.baseline_manifest_trust(baseline)
    assert files == {} and "找不到" in why, why


def test_清單壞掉不可以丟例外(repo):
    (repo / ".project").mkdir(exist_ok=True)
    (repo / ".project/frontend_manifest.json").write_text("{壞掉的", encoding="utf-8")
    baseline = _commit(repo, "壞掉的清單")
    files, why = mp.baseline_manifest_trust(baseline)
    assert files == {} and why


# ===== 打包流程真的會因此改走完整包嗎 =====

def test_打包流程用的是_trust_不是只看讀不讀得到():
    """程式碼層級的防復發：差集的 base 必須來自 baseline_manifest_trust。

    （行為本身由上面那幾題證明；這題防的是有人把它改回 `_baseline_manifest`，
    那樣「讀得到但對不上」又會變成送增量包。）
    """
    src = (ROOT / ".project" / "make_patch.py").read_text(encoding="utf-8")
    code = "".join(l for l in src.splitlines(True) if not l.lstrip().startswith("#"))
    assert "base, distrust = baseline_manifest_trust(since)" in code, \
        "打包流程沒有走 baseline_manifest_trust——讀得到但對不上又會送增量包"
    assert "base = _baseline_manifest(since)" not in code, \
        "還在只靠「讀不讀得到」決定要不要走增量"


def test_不可信時走的是完整包那一支():
    """`base` 是空的 → `if base and delta ...` 不成立 → else 分支打完整包。"""
    src = (ROOT / ".project" / "make_patch.py").read_text(encoding="utf-8")
    assert "if base and delta and len(delta) < len(cur):" in src, \
        "完整包的判斷式被改過了——base 空的時候必須落到完整包"
    assert "frontend-output.tar.gz" in src


def test_新產出的清單會記下自己是哪次_build():
    """不記的話，下一個人拿這個 commit 當 baseline，一樣自證不了。"""
    src = (ROOT / ".project" / "make_patch.py").read_text(encoding="utf-8")
    assert '"commit": head_full or head' in src, "新清單沒有記下 build 的 commit"
    assert 'json.dumps(manifest_doc' in src, "寫回版控的清單沒有帶 commit 欄"


def test_患部的失敗方向是多送檔不是少送檔(repo):
    """同型漏洞不可以再造一次：任何一條不可信的路徑，回的都必須是空清單。

    空清單 → delta 等於全部 → 落到完整包。只要有任何一條回「半套清單」，
    就又會送出一個少檔的增量包。
    """
    cases = []
    # 1) 沒清單
    (repo / "APP/asset-module/frontend/pages/adopt.vue").write_text("a\n", encoding="utf-8")
    cases.append(_commit(repo, "無清單"))
    # 2) 舊格式
    _write_manifest(repo, {"a.js": "1"}, commit=None)
    cases.append(_commit(repo, "舊格式"))
    # 3) 指向不存在的 commit
    _write_manifest(repo, {"a.js": "1"}, commit="0" * 40)
    cases.append(_commit(repo, "不存在"))

    for sha in cases:
        files, why = mp.baseline_manifest_trust(sha)
        assert files == {}, f"{sha[:7]} 回了半套清單——會送出少檔的增量包"
        assert why, f"{sha[:7]} 判不可信卻沒講理由，打包時會查不出為什麼"
