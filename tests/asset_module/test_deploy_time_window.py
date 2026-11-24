"""2.060.x：平日 07:00–09:30 不部署（1.5 每日檢查 06:30 快照、08:30 開盤前送出）；WEBIT_DEPLOY_ANYTIME=1 可強制。

只在有 bash 與 tzdata 的環境實跑（221 會跑）；Windows 開發機上 TZ=Asia/Taipei 不一定有效，只做靜態檢查。
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "APP" / "asset-module" / "deploy.sh"


def _guard() -> str:
    s = DEPLOY.read_text(encoding="utf-8")
    start = s.index("_now=\"$(TZ=Asia/Taipei date")
    end = s.index("fi\n", start) + 3
    return s[start:end]


def test_守門在最前面_有強制開關():
    s = DEPLOY.read_text(encoding="utf-8")
    assert s.index("WEBIT_DEPLOY_ANYTIME") < s.index("API_HOST")
    assert "-ge 700" in _guard() and "-lt 930" in _guard() and "-le 5" in _guard()


@pytest.mark.skipif(shutil.which("bash") is None or not Path("/usr/share/zoneinfo/Asia/Taipei").exists(), reason="需要 bash 與 tzdata")
@pytest.mark.parametrize("fake,blocked", [("1 0659", False), ("1 0700", True), ("3 0929", True), ("5 0930", False), ("6 0800", False)])
def test_時段判斷(fake, blocked):
    dow, hm = fake.split()
    g = _guard().replace('_now="$(TZ=Asia/Taipei date \'+%u %H%M %F %H:%M\')"', f'_now="{dow} {hm} 2026-11-10 00:00"')
    env = {k: v for k, v in os.environ.items() if k != "WEBIT_DEPLOY_ANYTIME"}   # 部署本身可能帶著強制開關跑測試，不能繼承（2.060.16 在 221 踩到）
    r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + g + "\necho PASSED"], capture_output=True, text=True, env=env)
    assert ("PASSED" not in r.stdout) == blocked, r.stdout + r.stderr
    r2 = subprocess.run(["bash", "-c", "set -euo pipefail\nWEBIT_DEPLOY_ANYTIME=1\n" + g + "\necho PASSED"], capture_output=True, text=True, env=env)
    assert "PASSED" in r2.stdout
