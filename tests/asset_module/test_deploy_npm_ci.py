"""deploy.sh 現場 build 一律 npm ci（2026-10-17）。

npm install 會依該台 npm 版本改寫 package-lock.json（221 npm 10.8.2 每次刪 114 行 libc 欄位），
部署完 repo 變髒、/api/version 永遠 dirty:true。ci 照 lock 裝、不寫回 lock。
"""
import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "deploy.sh"


def test_build分支用npm_ci_不用npm_install():
    cmds = [ln.strip() for ln in DEPLOY.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith("echo")]
    assert any(re.match(r"npm ci\b", c) for c in cmds), "build 分支要跑 npm ci"
    assert not any(re.match(r"npm (install|i)\b", c) for c in cmds), "不可以再跑 npm install（會改寫 lock）"
