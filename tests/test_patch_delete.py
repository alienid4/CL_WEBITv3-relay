"""更新包要能「刪檔」，而且只能刪該刪的。

為什麼要有這支（2026-09-30）：
`make_patch.changed_files()` 用 `git diff --diff-filter=ACMR`，**D（刪除）不在裡面**；
而 `patch.sh` 只做 `find files/ -type f` 然後 `cp`，**從來不刪任何東西**。
於是檔案改名或廢棄之後，公司主機上會同時留著新舊兩份——舊那份的版本戳停在改名當天
而且永遠不再更新，有人跑到舊的會拿到過期的檢核結果**而且不知道自己拿到舊的**。
眼前的實例就是 `scripts/fcbaixsh` → `scripts/fcb_aix.ksh`。

刪除是不可逆的動作，又是交給目標主機上的 root 執行，所以這支測試釘的是兩件事：
  1. 該刪的刪得到（DELETE.txt 產得出來、路徑是去前綴的相對路徑、舊包沒有它也不會壞）
  2. 不該刪的一律擋下來（`..`／絕對路徑／反斜線／指到 $APP 之外／symlink／目錄），
     而且是**中止整個 patch**，不是「跳過就好」——會出現這種路徑代表上游算錯了。
"""
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".project"))

try:
    import make_patch as mp
except ImportError:  # pragma: no cover - relay 快照裡沒有 make_patch
    pytest.skip("make_patch 不在 relay 快照裡，本測試只在主 repo 執行",
                allow_module_level=True)


# ─────────────────────────────────────────────────────────────────────────
# 1. 打包端：DELETE.txt 產得出來，而且是去前綴的相對路徑
# ─────────────────────────────────────────────────────────────────────────

#: git 會從**環境變數**決定要操作哪個 repo，而且優先於 cwd。
#: pre-commit hook 執行期間 GIT_DIR／GIT_INDEX_FILE 是設好的，子行程會原封不動繼承——
#: 於是這裡本來要寫進 tmp 迷你 repo 的 commit，會直接寫進**真正的 repo 並移動分支**。
#: 2026-09-30 實際發生過一次：閘門跑這支測試時，工作分支被推到測試的假 commit 上。
#: 所以每一個 git 子行程都要把這幾個變數清乾淨，不能只靠 cwd。
_GIT_ENV_LEAK = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR",
                 "GIT_PREFIX", "GIT_CONFIG", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                 "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_LEAK}
    r = subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, f"git {' '.join(args)} 失敗：{r.stderr}"
    return r.stdout


@pytest.fixture(autouse=True)
def _no_git_env_leak(monkeypatch):
    """把洩漏進來的 git 環境變數從**這個行程**也清掉。

    _git() 只管住自己開的子行程；被測的 make_patch.deleted_files() 也會開 git，
    那一支用的是 os.environ。不清這裡的話，閘門執行期間它會去查真正的 repo，
    測試就變成在驗一個跟 fixture 無關的歷史——綠也好紅也罷都沒有意義。
    """
    for k in _GIT_ENV_LEAK:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    """一個只有兩個 commit 的迷你 repo：第二個 commit 刪掉一支腳本、改掉一支。

    刻意不拿主 repo 的真實歷史來測——歷史會被 rebase、commit 會被改寫，
    那種測試哪天紅了也看不出是程式壞了還是歷史動了。
    """
    r = tmp_path / "repo"
    (r / mp.SHIP_PREFIX / "scripts").mkdir(parents=True)
    _git(r.parent, "init", "-q", str(r))
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")

    old = r / mp.SHIP_PREFIX / "scripts" / "fcbaixsh"
    keep = r / mp.SHIP_PREFIX / "backend" / "version.json"
    keep.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("#!/usr/bin/ksh\necho old\n", encoding="utf-8", newline="\n")
    keep.write_text('{"version": "1.0.0"}\n', encoding="utf-8", newline="\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")

    old.unlink()
    (r / mp.SHIP_PREFIX / "scripts" / "fcb_aix.ksh").write_text(
        "#!/usr/bin/ksh\necho new\n", encoding="utf-8", newline="\n")
    keep.write_text('{"version": "1.1.0"}\n', encoding="utf-8", newline="\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "改名")

    # 保險：確認這些 commit 真的落在 tmp 的迷你 repo，不是寫進真正的 repo。
    # 這一條紅了就代表環境變數又洩漏進來了（見 _GIT_ENV_LEAK 的說明）。
    top = _git(r, "rev-parse", "--show-toplevel").strip()
    assert Path(top).resolve() == r.resolve(), \
        f"測試的 git 操作跑到別的 repo 去了：{top}"
    assert len(_git(r, "log", "--oneline").splitlines()) == 2

    monkeypatch.setattr(mp, "REPO", r)
    return r


def test_改名會產出刪除清單_而且路徑已去掉SHIP前綴(repo: Path):
    assert mp.deleted_files("HEAD~1") == ["scripts/fcbaixsh"]
    # 新檔仍然走原本的變更清單，兩邊不重疊
    changed = mp.changed_files("HEAD~1")
    assert mp.SHIP_PREFIX + "scripts/fcb_aix.ksh" in changed
    assert mp.SHIP_PREFIX + "scripts/fcbaixsh" not in changed


def test_只刪掉SHIP範圍以外的檔不列進清單(repo: Path, monkeypatch):
    (repo / "AI").mkdir()
    (repo / "AI" / "secret.md").write_text("x\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add")
    (repo / "AI" / "secret.md").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "del")
    # AI/ 不在 APP/asset-module/ 底下，不可以出現在給公司主機的刪除清單裡
    assert mp.deleted_files("HEAD~1") == []


def test_刪了又加回來的不算刪除(repo: Path):
    back = repo / mp.SHIP_PREFIX / "scripts" / "fcbaixsh"
    back.write_text("#!/usr/bin/ksh\necho back\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "加回來")
    # HEAD 上檔案還在，照 git diff 算是「刪了又加」，真去刪就把現役檔案刪掉了
    assert mp.deleted_files("HEAD~2") == []


@pytest.mark.parametrize("rel", [
    "../../etc/passwd",
    "scripts/../../../etc/shadow",
    "/etc/passwd",
    "C:/Windows/System32/x",
    "scripts\\evil",
    "~/.ssh/authorized_keys",
    "  ",
])
def test_不安全的路徑在打包端就中止(rel: str):
    with pytest.raises(SystemExit):
        mp._reject_unsafe(rel, "測試")


def test_正常的相對路徑不會被誤擋():
    for ok in ("scripts/fcbaixsh", "backend/a.py", "frontend/pages/x.vue", "a..b.txt"):
        mp._reject_unsafe(ok, "測試")   # 不丟例外就算過


# ─────────────────────────────────────────────────────────────────────────
# 2. 套用端：patch.sh 的刪除段實跑
# ─────────────────────────────────────────────────────────────────────────

START = "# ===== 1.5 刪除已廢棄的檔案 ====="
END = "# ===== 2. 覆蓋 ====="


def _delete_block() -> str:
    s = mp.PATCH_SH
    assert START in s, "patch.sh 少了刪除步驟"
    assert END in s
    return s[s.index(START):s.index(END)]


def test_patch_sh_確實有刪除步驟且沒有DELETE就跳過():
    blk = _delete_block()
    assert 'if [ -f "$HERE/DELETE.txt" ]; then' in blk, "沒有 DELETE.txt 的舊包要整步跳過"
    assert 'cp -p "$tgt" "$BACKUP/$rel"' in blk, "刪之前一定要先備份"
    assert 'if [ -L "$tgt" ]' in blk, "symlink 不可以跟著刪"


bash = shutil.which("bash")


def _bash_usable(b) -> bool:
    """bash 存在還不夠，要真的跑得動帶原生路徑的腳本。Windows 的 bash.EXE（WSL）
    吃不下 Windows 路徑（`C:\\run.sh` → `No such file or directory`），只檢查
    which 會誤判成「有 bash」然後整批紅——那不是程式壞，是這台沒有能跑這種腳本的
    bash。改用實測探針；Linux／git-bash 會過，照跑真覆蓋，WSL 正確 skip。"""
    if not b:
        return False
    import tempfile as _tf
    d = _tf.mkdtemp()
    try:
        p = Path(d) / "probe.sh"
        p.write_text("#!/bin/sh\nprintf ok\n", encoding="utf-8", newline="\n")
        r = subprocess.run([b, str(p)], capture_output=True, timeout=30)
        return r.returncode == 0 and r.stdout.strip() == b"ok"
    except Exception:
        return False
    finally:
        shutil.rmtree(d, ignore_errors=True)


needs_bash = pytest.mark.skipif(
    not _bash_usable(bash),
    reason="這台的 bash 跑不了帶原生路徑的腳本（如 WSL 吃不下 Windows 路徑）")


def _run(tmp_path: Path, delete_txt: str | None, seed: dict[str, str]):
    """搭一個假的 $APP，把 patch.sh 的刪除段單獨跑一次。"""
    app = tmp_path / "app"
    for rel, body in seed.items():
        p = app / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8", newline="\n")
    app.mkdir(parents=True, exist_ok=True)
    here = tmp_path / "pkg"
    here.mkdir()
    if delete_txt is not None:
        (here / "DELETE.txt").write_text(delete_txt, encoding="utf-8", newline="\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    script = textwrap.dedent(f"""\
        set -uo pipefail
        HERE="{here.as_posix()}"
        APP="{app.as_posix()}"
        BACKUP="{backup.as_posix()}"
        """) + _delete_block() + "\necho RC_OK\n"
    sh = tmp_path / "run.sh"
    sh.write_text(script, encoding="utf-8", newline="\n")
    r = subprocess.run([bash, str(sh)], capture_output=True,
                       encoding="utf-8", errors="replace", timeout=60)
    return r, app, backup


@needs_bash
def test_沒有DELETE的舊包完全不受影響(tmp_path: Path):
    r, app, _ = _run(tmp_path, None, {"scripts/a.sh": "a\n"})
    assert "RC_OK" in r.stdout and r.returncode == 0
    assert (app / "scripts" / "a.sh").is_file(), "舊包不該刪掉任何東西"
    assert "1.5/5" not in r.stdout, "沒有 DELETE.txt 就該整步跳過，連標題都不印"


@needs_bash
def test_列出來的檔會先備份再刪掉(tmp_path: Path):
    r, app, backup = _run(tmp_path, "scripts/fcbaixsh\n",
                          {"scripts/fcbaixsh": "old\n", "scripts/fcb_aix.ksh": "new\n"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (app / "scripts" / "fcbaixsh").exists(), "該刪的沒刪掉"
    assert (app / "scripts" / "fcb_aix.ksh").is_file(), "不在清單裡的不可以動"
    assert (backup / "scripts" / "fcbaixsh").read_text(encoding="utf-8") == "old\n", \
        "刪之前要先備份，否則出事回不去"
    assert "共刪除 1 個檔案" in r.stdout, "要講清楚刪了幾個"


@needs_bash
def test_已經刪過的重跑不會失敗(tmp_path: Path):
    r, _, _ = _run(tmp_path, "scripts/gone\n", {"scripts/a.sh": "a\n"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "共刪除 0 個檔案" in r.stdout


@needs_bash
@pytest.mark.parametrize("rel", [
    "../outside.txt",
    "scripts/../../outside.txt",
    "/etc/passwd",
    "scripts\\evil",
])
def test_路徑穿越會讓整個patch中止(tmp_path: Path, rel: str):
    (tmp_path / "outside.txt").write_text("victim\n", encoding="utf-8", newline="\n")
    r, app, _ = _run(tmp_path, rel + "\n", {"scripts/a.sh": "a\n"})
    assert r.returncode != 0, f"{rel} 竟然被放行了：\n{r.stdout}"
    assert (tmp_path / "outside.txt").is_file(), "$APP 之外的檔案被動到了"
    assert "RC_OK" not in r.stdout, "不可以只是跳過，必須中止整個 patch"


@needs_bash
def test_目標是目錄時中止而不是遞迴刪掉(tmp_path: Path):
    r, app, _ = _run(tmp_path, "scripts\n", {"scripts/a.sh": "a\n"})
    assert r.returncode != 0
    assert (app / "scripts" / "a.sh").is_file(), "目錄底下的東西不可以被連坐刪掉"


@needs_bash
def test_帶CRLF的清單照樣刪得到(tmp_path: Path):
    # 檔案經 Windows 中轉必定變 CRLF，沒清掉 \r 就會變成「刪不到、卻回報成功」
    r, app, _ = _run(tmp_path, "scripts/fcbaixsh\r\n", {"scripts/fcbaixsh": "old\n"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (app / "scripts" / "fcbaixsh").exists()


@needs_bash
@pytest.mark.skipif(os.name == "nt", reason="Windows 上建 symlink 需要特權")
def test_symlink不跟著刪到別處(tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("victim\n", encoding="utf-8", newline="\n")
    app = tmp_path / "app"
    (app / "scripts").mkdir(parents=True)
    (app / "scripts" / "link").symlink_to(outside)
    here = tmp_path / "pkg"
    here.mkdir()
    (here / "DELETE.txt").write_text("scripts/link\n", encoding="utf-8", newline="\n")
    backup = tmp_path / "backup"
    backup.mkdir()
    script = (f'set -uo pipefail\nHERE="{here.as_posix()}"\n'
              f'APP="{app.as_posix()}"\nBACKUP="{backup.as_posix()}"\n'
              + _delete_block() + "\necho RC_OK\n")
    sh = tmp_path / "run.sh"
    sh.write_text(script, encoding="utf-8", newline="\n")
    r = subprocess.run([bash, str(sh)], capture_output=True,
                       encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode != 0 and "RC_OK" not in r.stdout
    assert outside.is_file(), "symlink 指到的檔案被刪掉了"


# ─────────────────────────────────────────────────────────────────────────
# 3. MANIFEST 要把「會刪掉哪些檔」跟「包含的檔案」分開寫
# ─────────────────────────────────────────────────────────────────────────

def test_MANIFEST有獨立的刪除區塊():
    src = (ROOT / ".project" / "make_patch.py").read_text(encoding="utf-8")
    assert '"== 這包會刪掉哪些檔 =="' in src
    assert '"== 包含的檔案 =="' in src


# ---------------------------------------------------------------------------
# 2026-09-30 抓到的兩個靜默漏檔（都實際發生過，不是假想）
# ---------------------------------------------------------------------------

def test_中文檔名的檔案不可以被靜默跳過(repo: Path):
    """git 預設會把非 ASCII 路徑加引號跳脫，解析時整個檔會被無聲丟掉。

    實際後果：**中文檔名的檔案從來沒有被打包送出去過**，而且沒有任何警告——
    它長得就像「那個檔本來就沒改」。這條測試就是釘住那個行為不可以再回來。

        預設：  "APP/asset-module/scripts/fcb_aix\344\275\277..."
        -z  ：  APP/asset-module/scripts/fcb_aix使用說明.md
    """
    zh_new = repo / mp.SHIP_PREFIX / "scripts" / "使用說明.md"
    zh_old = repo / mp.SHIP_PREFIX / "scripts" / "舊版說明.md"
    zh_old.write_text("old\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "先放一份中文檔名的舊說明")

    zh_old.unlink()
    zh_new.write_text("new\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "中文檔名改名")

    changed = mp.changed_files("HEAD~1")
    assert mp.SHIP_PREFIX + "scripts/使用說明.md" in changed, \
        "中文檔名的新檔沒有進變更清單——又被 quotepath 的引號吃掉了"

    deleted = mp.deleted_files("HEAD~1")
    assert "scripts/舊版說明.md" in deleted, \
        "中文檔名的舊檔沒有進刪除清單——公司主機上會留著一份過期的"


def test_中途建立又中途刪掉的檔也要進刪除清單(repo: Path):
    """擋的是「只看兩端點」的 diff 會漏掉的情況。

    `fcb_aix.ksh` 就是這樣：baseline 之後才建立、HEAD 之前又被改名掉。
    兩端點 diff 看不到它（base 沒有、head 也沒有），但**套過中間那包的主機
    硬碟上就是有**——不刪的話新舊並存，有人跑到舊的會拿到過期結果而不自知。
    """
    base = _git(repo, "rev-parse", "HEAD").strip()

    mid = repo / mp.SHIP_PREFIX / "scripts" / "midlife.ksh"
    mid.write_text("#!/usr/bin/ksh\necho mid\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "中途建立")

    mid.unlink()
    (repo / mp.SHIP_PREFIX / "scripts" / "final.ksh").write_text(
        "#!/usr/bin/ksh\necho final\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "中途刪掉")

    # 先證明「兩端點 diff」確實看不到它——不然這條測試等於沒在測東西
    two_point = _git(repo, "diff", "--name-only", "--no-renames",
                     "--diff-filter=D", base + "..HEAD")
    assert "midlife.ksh" not in two_point, \
        "前提不成立：兩端點 diff 竟然看得到中途生滅的檔，這條測試要重寫"

    assert "scripts/midlife.ksh" in mp.deleted_files(base), \
        "中途建立又刪掉的檔沒有進刪除清單——套過中間包的主機會新舊並存"


def test_刪了又加回來的不可以列進刪除清單(repo: Path):
    """回歸：改成逐 commit 累積之後，最容易壞掉的就是這個。

    檔案在 range 中間被刪過、但 HEAD 上又存在（改完再改回去），
    那它**不可以**被列進 DELETE.txt——列了就是把好檔刪掉。
    """
    base = _git(repo, "rev-parse", "HEAD").strip()

    f = repo / mp.SHIP_PREFIX / "scripts" / "comeback.sh"
    f.write_text("#!/bin/sh\necho v1\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "建立")

    f.unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "刪掉")

    f.write_text("#!/bin/sh\necho v2\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "又加回來")

    assert "scripts/comeback.sh" not in mp.deleted_files(base), \
        "HEAD 上還存在的檔被列進刪除清單了——套下去會把好檔刪掉"
    assert mp.SHIP_PREFIX + "scripts/comeback.sh" in mp.changed_files(base)
