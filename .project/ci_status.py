"""遠端自動化（GitHub Actions）現在是不是紅燈。

為什麼需要這支：2026-08-15 15:19 起 relay 同步連續失敗 9 次（去識別化殘留掃描擋下
一個公司識別字），整整一天不只更新包沒出去、連程式碼都沒出去，而**沒有任何人發現**
——Actions 失敗是安靜的，要主動去點才看得到。最後是使用者問「怎麼沒看到 patch」
才偶然摸到。checks.py 每次 commit 前都會跑，把這個狀態拉到那裡，它才會被看見。

抽成獨立模組而不是寫在 checks.py 裡：checks.py 是一支「一 import 就整個跑完」的腳本，
沒辦法在測試裡呼叫它的某一段。這裡照專案既有慣例把真正碰外部的部分做成可注入的
runner——錯誤路徑才測得到，而這次的教訓正是「沒驗過的錯誤路徑等於不存在」。
"""
from __future__ import annotations

import json
import subprocess

QUERY = ["gh", "run", "list", "--limit", "1",
         "--json", "conclusion,status,displayTitle"]


def _default_runner() -> tuple[int, str]:
    """真的去問 GitHub。不裝 gh／沒網路／不是 GitHub repo 都回非 0，呼叫端當作沒這回事。"""
    try:
        p = subprocess.run(QUERY, capture_output=True, text=True, timeout=12,
                           encoding="utf-8", errors="replace")
        return p.returncode, p.stdout or ""
    except Exception:  # noqa: BLE001 - 這是額外的眼睛，不是新的相依，掛掉不能影響 checks
        return 1, ""


def last_failure(runner=None) -> str:
    """上一次 CI 失敗的標題；沒失敗、或查不到，一律回空字串。

    刻意只看最新一次：要回答的是「現在還壞著嗎」，不是「歷史上壞過幾次」。
    """
    rc, out = (runner or _default_runner)()
    if rc != 0 or not (out or "").strip():
        return ""
    try:
        runs = json.loads(out)
    except ValueError:
        return ""
    if not runs or not isinstance(runs, list):
        return ""
    first = runs[0]
    if not isinstance(first, dict) or first.get("conclusion") != "failure":
        return ""
    return (first.get("displayTitle") or "(無標題)")[:40]
