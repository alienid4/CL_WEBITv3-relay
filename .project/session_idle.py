"""各開發 session 停了多久——BOSS 用來抓「空等」。

2026-09-22 使用者指出：「你違反了 不能空等」。
BOSS 立了「不准空等」的規矩，卻只靠 session 主動回報，**沒有任何檢查機制**，
結果 SysCheck 停了 2 小時 21 分，是使用者問起才發現。規矩沒有檢查機制＝沒有規矩。

用法：
    py -3 .project/session_idle.py

判讀：
  * 「停多久」算的是**最後一筆 commit 到現在**，不是有沒有在打字。
    一批大的改動本來就會久，所以這支只負責**指出來**，不負責下結論。
  * 「未提交」有數字而且停很久 = 做到一半卡住，最值得問。
  * 超過 THRESHOLD_MIN 標警示，BOSS 要去問「卡在哪、要不要拆小先推」。
"""
from __future__ import annotations

import datetime
import os
import subprocess

#: 超過這個分鐘數就標警示。45 分鐘 ≈ 一批小改動加一次閘門（4.5 分）的合理上限。
THRESHOLD_MIN = 45

#: 分支 → 該 session 的工作區。main 是 BOSS 的，不算空等。
BRANCHES = [
    ("origin/main", r"C:\AiProject\CL_WEBITv3", "BOSS（合併與部署）"),
    ("origin/feature/syscheck", r"C:\AiProject\CL_WEBITv3-syscheck", "SysCheck"),
    ("origin/feature/aix-hardening", r"C:\AiProject\CL_WEBITv3-aix", "AIX_Hardering"),
]


def _run(cmd: str, cwd: str | None = None) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=cwd)
    return (r.stdout or "").strip()


def main() -> int:
    root = r"C:\AiProject\CL_WEBITv3"
    _run("git fetch -q", root)
    now = datetime.datetime.now()
    print(f"{'Session':<18}{'分支':<24}{'最後產出':<10}{'停多久':<10}未提交")
    print("-" * 78)
    stale = []
    for branch, worktree, who in BRANCHES:
        ts = _run(f"git log -1 --format=%cI {branch}", root)
        if not ts:
            print(f"{who:<18}{branch:<24}（還沒推過分支）")
            continue
        t = datetime.datetime.fromisoformat(ts).replace(tzinfo=None)
        mins = int((now - t).total_seconds() / 60)
        dirty = _run("git status --short", worktree) if os.path.isdir(worktree) else ""
        n_dirty = len([x for x in dirty.splitlines() if not x.startswith("??")])
        warn = ""
        if branch != "origin/main" and mins > THRESHOLD_MIN:
            warn = "  ⚠ 停太久，去問卡在哪"
            stale.append((who, mins, n_dirty))
        print(f"{who:<18}{branch.replace('origin/', ''):<24}"
              f"{t.strftime('%H:%M'):<10}{str(mins) + ' 分':<10}{n_dirty} 檔{warn}")
    if stale:
        print()
        for who, mins, n in stale:
            extra = "（而且有改到一半的檔案）" if n else ""
            print(f"  → 去問 {who}：停 {mins} 分{extra}。"
                  f"問三件：做到哪／卡在什麼／要不要拆小先推")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
