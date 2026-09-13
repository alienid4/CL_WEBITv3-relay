"""公司主機離線：patch 必須自帶 paramiko 並離線安裝（2026-09-11 v1.94.0 起納管靠它）。

沒有這個，patch 會顯示成功、系統照常啟動，但納管全部失敗——最難查的那種。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _patch_sh() -> str:
    """直接從原始碼切出 PATCH_SH 範本，不 import make_patch——
    它會載入去識別化規則（desensitize_rules），那個檔不一定在測試環境裡。"""
    src = (ROOT / ".project" / "make_patch.py").read_text(encoding="utf-8")
    start = src.index("PATCH_SH = r'''") + len("PATCH_SH = r'''")
    return src[start:src.index("'''", start)]


def test_patch_wheels內附paramiko與相依套件():
    names = {p.name.split("-")[0].lower() for p in (ROOT / ".project" / "patch_wheels").glob("*.whl")}
    for must in ("paramiko", "bcrypt", "pynacl", "invoke"):
        assert must in names, f".project/patch_wheels 少了 {must}"


def test_二進位套件是linux_x86_64而且不綁特定Python版本():
    """公司主機的 Python 版本沒確認過；用 abi3（3.8/3.9 以上通用）的才不會裝不上。"""
    for p in (ROOT / ".project" / "patch_wheels").glob("*.whl"):
        if p.name.endswith("-py3-none-any.whl"):
            continue
        assert "abi3" in p.name and "x86_64" in p.name and "linux" in p.name, p.name


def test_patch_sh離線安裝而且不會連網():
    # relay（去識別化產出物）不帶打包工具 make_patch.py——那裡本來就不打 patch，
    # 沒有要測的東西；在主 repo 與 221 部署前測試照跑。（v1.94.2 在 relay CI 紅過）
    if not (ROOT / ".project" / "make_patch.py").is_file():
        import pytest
        pytest.skip("這份產出物不含 .project/make_patch.py（relay 不帶打包工具）")
    sh = _patch_sh()
    assert '"$VENV_PY" -c "import paramiko"' in sh, "要先檢查已安裝就跳過"
    line = next(l for l in sh.splitlines() if "pip install" in l and "paramiko" in l)
    assert "--no-index" in line and '--find-links "$HERE/wheels"' in line, line
    # 安裝失敗要講清楚後果，不能安靜略過
    assert "『納管』功能會失敗" in sh
