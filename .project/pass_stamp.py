#!/usr/bin/env python3
"""全套測試「同一份程式只跑一次」：PC 跑過的通過證明，221 部署時核對後沿用。

## 為什麼（2026-10-19 使用者：「現在小更新都要30分，我不相信大家都是這麼改」）

量測（v1.467.2 那一輪）：
  · 221 deploy.sh [2c] 全套 pytest：10 分 38 秒（221 只有 2 顆 CPU）
  · PC 上同一份全套（16 核、xdist）：4 分 29 秒
同一份程式在 PC 已經全綠，221 再花 10 分鐘把它重跑一遍，證明的是同一件事。

## 紅線不變：「沒過全套的碼進不了正式機」

這支只把「全套在哪裡跑」從 221 換成 PC，**不是拿掉**。221 只在下列全部成立時沿用：
  1. 內容鍵完全相同：`git rev-parse HEAD:APP HEAD:tests HEAD:.project` 三個 tree hash
     （git 依內容算的指紋，改一個字就不同）——跟 PC 測試當下的一模一樣。
  2. 兩邊工作區都是乾淨的（APP/ tests/ .project/ 沒有未提交改動、沒有未追蹤檔）。
     PC 端測試**前後**各驗一次，測到一半有人改檔 → 不發證明。
  3. 證明裡 failed=0、errors=0、離開碼 0。
  4. Python 主版本相同（例 3.11.x 對 3.11.x）。
  5. **221 收得到、PC 沒有實際跑過（skip／沒收集到）的每一條測試，221 自己補跑**。
     PC 是 Windows，有幾十條只在 Linux 跑得了的（visudo、POSIX 權限、ksh…），
     這些一定在 221 補跑，全過才放行。
任一不成立 → 照舊在 221 跑全套。`WEBIT_FORCE_TESTS=1` 或 `REQUIRE_TESTS=1` 一律跑全套。

證明檔不進 git（放進 .project/ 會改變 .project 的 tree hash，自己蓋掉自己），
PC 寫在 `~/.webit3/test_pass/<key>.json`，用 `--push` 以 scp 送到 221 的
`/opt/webit3/data/test_pass/`。檔名就是內容鍵，對不上的檔案根本不會被讀到。

## 用法

  PC（在 repo 根目錄、commit 之後、部署之前）：
    .venv/Scripts/python.exe .project/pass_stamp.py run [--push root@<221>:/opt/webit3/data/test_pass]
  221（deploy.sh [2c] 呼叫）：
    python .project/pass_stamp.py check --dir /opt/webit3/data/test_pass --remainder <檔案>
      離開碼 0＝證明有效（remainder 檔＝要在這台補跑的 nodeid，一行一個，可能是空的）
      離開碼 2＝沒有有效證明（原因印在畫面）→ 呼叫端跑全套
  relay 去識別化快照**不沿用**這張證明：替換表會改到測試裡的值、快照也少了 AI/ 等檔，
  內容跟 commit 不同，必須在快照上自己跑（make_relay.py，平行跑）。

本檔同時是 pytest 外掛（`-p pass_stamp`）：記下每一條測試的結果（含 xdist 平行時）。
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_env import clean_git_env  # noqa: E402

SCHEMA = 1
#: 會影響測試結果的內容。AI/、docs/ 只有文件，改它們不需要重測。
PATHS = ("APP", "tests", ".project")
DEFAULT_DIR = Path.home() / ".webit3" / "test_pass"
#: 外掛把逐條結果寫到這個環境變數指定的檔案
REPORT_ENV = "WEBIT_STAMP_REPORT"
LF = "\n"


# ───────────────────────── 內容鍵 ─────────────────────────

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    # 清掉 hook 塞進來的 GIT_DIR 等（見 git_env.py；不清會打到別的 repo）
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=clean_git_env())


def trees(repo: Path, rev: str = "HEAD") -> dict[str, str]:
    """各路徑在 rev 的 tree hash。取不到（不是 git、路徑不存在）就丟 RuntimeError。"""
    r = _git(repo, "rev-parse", *[f"{rev}:{p}" for p in PATHS])
    hashes = r.stdout.split()
    if r.returncode != 0 or len(hashes) != len(PATHS):
        raise RuntimeError(f"取不到 {rev} 的 tree hash：{(r.stderr or r.stdout).strip()[:200]}")
    return dict(zip(PATHS, hashes))


def key_of(tree_map: dict[str, str]) -> str:
    """由三個 tree hash 算出的內容鍵。也是證明檔的檔名。"""
    raw = f"webit3-test-pass/v{SCHEMA}" + LF + LF.join(f"{p} {tree_map[p]}" for p in PATHS)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def dirty(repo: Path) -> list[str]:
    """APP/ tests/ .project/ 底下未提交的改動與未追蹤檔（.gitignore 的不算）。"""
    r = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", *PATHS)
    if r.returncode != 0:
        return [f"(git status 失敗：{r.stderr.strip()[:120]})"]
    return [ln for ln in r.stdout.splitlines() if ln.strip()]



# ───────────────────────── pytest 外掛 ─────────────────────────
# `-p pass_stamp` 載入時這幾個 hook 生效。xdist 平行時 report 會轉回主行程，
# 所以只在主行程（沒有 workerinput）寫檔。

_RESULTS: dict[str, str] = {}


def pytest_runtest_logreport(report):  # noqa: D401
    if not os.environ.get(REPORT_ENV):
        return
    prev = _RESULTS.get(report.nodeid)
    if report.failed:
        _RESULTS[report.nodeid] = "failed" if report.when == "call" else "error"
    elif report.skipped:
        if prev is None:
            _RESULTS[report.nodeid] = "skipped"
    elif report.when == "call" and report.passed and prev is None:
        _RESULTS[report.nodeid] = "passed"
    elif report.when == "teardown" and report.passed and prev is None:
        # 沒有 call 階段的（理論上不會）保守當沒跑
        _RESULTS.setdefault(report.nodeid, "skipped")


def pytest_collectreport(report):
    if not os.environ.get(REPORT_ENV):
        return
    if report.failed:
        _RESULTS[report.nodeid or "<collect>"] = "error"
    elif report.skipped:
        _RESULTS[report.nodeid or "<collect>"] = "skipped"


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get(REPORT_ENV)
    if not path or hasattr(session.config, "workerinput"):
        return
    Path(path).write_text(json.dumps({"exitstatus": int(exitstatus), "results": _RESULTS},
                                     ensure_ascii=False), encoding="utf-8")


# ───────────────────────── 執行 pytest ─────────────────────────

def _xdist_args() -> list[str]:
    try:
        import xdist  # noqa: F401
    except ImportError:
        return []
    # worksteal：長尾測試（單條 30 秒）不會卡在同一個 worker 後面排隊
    return ["-n", "auto", "--dist", "worksteal"]


def run_pytest(cwd: Path, extra: list[str], report: Path) -> tuple[int, dict]:
    """在 cwd 跑 pytest（帶本外掛），回 (離開碼, 逐條結果)。"""
    env = dict(os.environ)
    env[REPORT_ENV] = str(report)
    here = str(Path(__file__).resolve().parent)
    env["PYTHONPATH"] = here + os.pathsep + env.get("PYTHONPATH", "")
    if report.exists():
        report.unlink()
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "pass_stamp", "-p", "no:cacheprovider",
           *_xdist_args(), *extra]
    print("$ " + " ".join(cmd), flush=True)
    rc = subprocess.run(cmd, cwd=str(cwd), env=env).returncode
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    return rc, data.get("results", {})


def counts(results: dict) -> dict[str, int]:
    out = {"passed": 0, "skipped": 0, "failed": 0, "error": 0}
    for v in results.values():
        out[v] = out.get(v, 0) + 1
    return out


def _deps() -> dict[str, str]:
    """記下測試當下的主要套件版本（221 會印出差異，供查證；不擋）。"""
    from importlib import metadata
    want = ("fastapi", "starlette", "pydantic", "uvicorn", "httpx", "paramiko",
            "cryptography", "openpyxl", "pywinrm", "pypsrp", "werkzeug", "python-multipart", "pytest")
    got = {}
    for n in want:
        try:
            got[n] = metadata.version(n)
        except metadata.PackageNotFoundError:
            pass
    return got


# ───────────────────────── 子命令 ─────────────────────────

def cmd_run(repo: Path, out_dir: Path, push: str | None) -> int:
    t0 = time.time()
    bad = dirty(repo)
    if bad:
        print("!! 工作區有未提交的改動，測了也發不出證明（證明只對「已 commit 的那一份」有效）：")
        for ln in bad[:15]:
            print("   " + ln)
        return 2
    tm = trees(repo)
    key = key_of(tm)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    stamp_path = out_dir / f"{key}.json"
    if stamp_path.exists() and os.environ.get("WEBIT_FORCE_TESTS") != "1":
        old = json.loads(stamp_path.read_text(encoding="utf-8"))
        print(f"✓ 這一份內容（{key[:12]}）已在 {old.get('finished_at')} 全套通過，不重跑"
              f"（{old['counts']['passed']} passed）。要強制重跑：WEBIT_FORCE_TESTS=1")
    else:
        report = out_dir / f".report_{os.getpid()}.json"
        out_dir.mkdir(parents=True, exist_ok=True)
        rc, results = run_pytest(repo, ["tests"], report)
        report.unlink(missing_ok=True)
        c = counts(results)
        after = dirty(repo)
        if after or key_of(trees(repo)) != key:
            print("!! 測試期間工作區被改動了，這次結果不能代表任何一份 commit，不發證明。")
            return 2
        if rc != 0 or c["failed"] or c["error"] or not c["passed"]:
            print(f"!! 全套沒過（離開碼 {rc}，{c}）——不發證明。")
            return 1
        stamp = {
            "schema": SCHEMA, "key": key, "trees": tm, "commit": head,
            "status": "passed", "exitstatus": rc, "counts": c,
            "python": platform.python_version(), "platform": sys.platform,
            "host": platform.node(), "deps": _deps(),
            "duration_s": round(time.time() - t0, 1),
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            # 221 只認「passed」的；skipped／沒收集到的一律在 221 補跑
            "passed": sorted(k for k, v in results.items() if v == "passed"),
        }
        tmp = stamp_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(stamp, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(stamp_path)
        print(f"✓ 全套通過 {c['passed']} passed／{c['skipped']} skipped，"
              f"{stamp['duration_s']} 秒。證明：{stamp_path}")
    if push:
        scp = shutil.which("scp") or "scp"
        r = subprocess.run([scp, "-q", "-o", "ConnectTimeout=15", str(stamp_path), push.rstrip("/") + "/"])
        if r.returncode != 0:
            print(f"!! 送證明到 {push} 失敗（離開碼 {r.returncode}）——221 會自己跑全套，不影響正確性。")
            return 3
        print(f"✓ 證明已送到 {push}")
    return 0


def validate(stamp: dict, key: str) -> str | None:
    """證明能不能用。能用回 None，不能用回原因。"""
    if stamp.get("schema") != SCHEMA:
        return f"證明格式版本 {stamp.get('schema')} ≠ {SCHEMA}"
    if stamp.get("key") != key:
        return "內容鍵不同（證明測的不是這一份程式）"
    if stamp.get("status") != "passed" or stamp.get("exitstatus") != 0:
        return f"證明狀態不是通過（{stamp.get('status')}／離開碼 {stamp.get('exitstatus')}）"
    c = stamp.get("counts") or {}
    if c.get("failed") or c.get("error") or not c.get("passed"):
        return f"證明裡有失敗或沒有任何通過（{c}）"
    mine = ".".join(platform.python_version_tuple()[:2])
    theirs = ".".join(str(stamp.get("python", "")).split(".")[:2])
    if mine != theirs:
        return f"Python 版本不同（證明 {stamp.get('python')}，這台 {platform.python_version()}）"
    return None


def collect_nodeids(repo: Path) -> list[str] | None:
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                        "-p", "no:cacheprovider", "tests"], cwd=str(repo),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None
    return [ln.strip() for ln in r.stdout.splitlines() if "::" in ln and not ln.startswith(" ")]


def cmd_check(repo: Path, stamp_dir: Path, remainder: Path | None) -> int:
    def no(why: str) -> int:
        print(f"  沒有可沿用的全套通過證明：{why} → 這台跑全套")
        return 2

    if os.environ.get("WEBIT_FORCE_TESTS") == "1" or os.environ.get("REQUIRE_TESTS") == "1":
        return no("指定強制重跑（WEBIT_FORCE_TESTS／REQUIRE_TESTS）")
    try:
        key = key_of(trees(repo))
    except RuntimeError as e:
        return no(str(e))
    bad = dirty(repo)
    if bad:
        return no(f"這台工作區有 {len(bad)} 處未提交改動（例：{bad[0].strip()}）")
    f = stamp_dir / f"{key}.json"
    if not f.exists():
        return no(f"找不到這一份內容（{key[:12]}）的證明")
    try:
        stamp = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return no(f"證明檔讀不了：{e}")
    why = validate(stamp, key)
    if why:
        return no(why)
    ids = collect_nodeids(repo)
    if ids is None:
        return no("這台收集測試清單失敗")
    passed = set(stamp.get("passed") or [])
    todo = [i for i in ids if i not in passed]
    c = stamp["counts"]
    print(f"  已在 PC 全套通過 {c['passed']} passed／{c['skipped']} skipped @ {stamp.get('finished_at')}"
          f"（{stamp.get('host')}，Python {stamp.get('python')}，commit {str(stamp.get('commit'))[:8]}），"
          "同一份程式不重跑")
    print(f"  這台收到 {len(ids)} 條；PC 沒實際跑過的 {len(todo)} 條在這台補跑")
    mine = _deps()
    diff = [f"{k} {v}→{mine.get(k)}" for k, v in (stamp.get("deps") or {}).items()
            if mine.get(k) and mine.get(k) != v]
    if diff:
        print("  （套件版本與 PC 不同，供參考：" + "、".join(diff) + "）")
    if remainder is not None:
        remainder.write_text("".join(i + LF for i in todo), encoding="utf-8")
    return 0


def main(argv: list[str]) -> int:
    import argparse
    # Windows 導向檔案時 stdout 是 cp950，印 ✓ 會 UnicodeEncodeError（2026-10-19 第一次實跑就踩到：
    # 全套已過、證明已寫，卻在印訊息時炸掉、沒送到 221）
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split(LF)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("run")
    a.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    a.add_argument("--out", default=str(DEFAULT_DIR))
    a.add_argument("--push", default=None, help="scp 目的地，例 root@<host>:/opt/webit3/data/test_pass")
    b = sub.add_parser("check")
    b.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    b.add_argument("--dir", required=True)
    b.add_argument("--remainder", default=None)
    k = sub.add_parser("key")
    k.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    ns = ap.parse_args(argv)
    if ns.cmd == "run":
        return cmd_run(Path(ns.repo), Path(ns.out), ns.push)
    if ns.cmd == "check":
        return cmd_check(Path(ns.repo), Path(ns.dir), Path(ns.remainder) if ns.remainder else None)
    print(key_of(trees(Path(ns.repo))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
