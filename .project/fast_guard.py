"""全套之前的快速檢查（約 1～2 分鐘）：先跑「格式守門」＋這次改到的測試，過了才值得跑 16 分鐘的全套。

起因（使用者 11-14：「我們現在都是小改版，這樣太慢了」）：11-13～14 有 4 次全套跑完 16 分鐘才在
守門測試紅（禁用詞、靜默 catch、machine_key 拼接、tick 沒登記靜默窗、驗算沒登記），全部是 1 秒就能抓到的。

用法（在 repo 根目錄）：
    .venv/Scripts/python.exe .project/fast_guard.py            # 守門＋相對 origin/main 改到的測試
    .venv/Scripts/python.exe .project/fast_guard.py --base X   # 改用別的比較基準
不取代 pass_stamp：這支綠了照樣要跑全套才能 push／部署（紅線不變）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = "tests/asset_module/"

#: 只掃原始碼、不起服務的守門測試（每支幾秒內）。全套曾因這些紅而白跑的都列在這裡。
GUARDS = [
    "test_no_silent_catch.py", "test_v2_batch1.py", "test_single_machine_key.py", "test_quiet_window_entries.py",
    "test_verify_numbers_all_registered.py", "test_frontend_guards.py", "test_frontend_api_paths.py",
    "test_auth_coverage.py", "test_dedup_consistency.py", "test_no_sshpass.py", "test_agent_scripts_security.py",
    "test_devkit_content.py", "test_v2_export_everywhere.py",
]


def changed_tests(base: str) -> list[str]:
    """相對 base 改到的測試檔，加上改到的程式檔對應的 test_<stem>.py（有的話）。"""
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    files = [f for f in out.stdout.splitlines() if f.strip()]
    hits: set[str] = set()
    for f in files:
        p = Path(f)
        if f.startswith(T) and p.name.startswith("test_") and p.suffix == ".py":
            hits.add(p.name)
        elif p.suffix == ".py" and f.startswith("APP/"):
            cand = ROOT / T / f"test_{p.stem}.py"
            if cand.exists():
                hits.add(cand.name)
    return sorted(h for h in hits if (ROOT / T / h).exists())


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 舊版 Python／被導向的輸出不支援：維持原編碼，只影響勾勾符號
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    a = ap.parse_args()
    tests = sorted(set(GUARDS) | set(changed_tests(a.base)))
    tests = [T + t for t in tests if (ROOT / T / t).exists()]
    py = str(ROOT / ".venv" / "Scripts" / "python.exe")
    if not Path(py).exists():
        py = sys.executable
    args = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", "--no-header", *tests]
    try:
        import xdist  # noqa: F401  # 有裝才平行；沒裝照序列跑（同 checks.py 的做法）
        args[4:4] = ["-n", "auto"]
    except ImportError:
        pass
    print(f"快速檢查：{len(tests)} 支測試（守門 {len(GUARDS)}＋改到的）…", flush=True)
    t0 = time.time()
    rc = subprocess.run(args, cwd=ROOT).returncode
    print(f"{'✓ 通過' if rc == 0 else '✗ 有紅，先修再跑全套'}（{time.time() - t0:.0f} 秒）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
