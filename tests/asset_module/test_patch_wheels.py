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


def test_patch_wheels內附paramiko與完整相依():
    """paramiko 要能離線裝起來，整條相依鏈都要在——2026-09-14 踩到：原本少了
    cryptography／cffi／pycparser，離線 pip 解不了相依、paramiko 裝不起來。"""
    names = {p.name.split("-")[0].lower() for p in (ROOT / ".project" / "patch_wheels").glob("*.whl")}
    for must in ("paramiko", "bcrypt", "pynacl", "invoke", "cryptography", "cffi", "pycparser"):
        assert must in names, f".project/patch_wheels 少了 {must}（paramiko 相依鏈不完整，離線裝不起來）"


def test_二進位套件是linux_x86_64():
    """公司主機（.14）後端 venv 已確認是 Linux x86_64 / Python 3.11（不是系統的 3.9）。
    C 擴充輪子必須是 linux x86_64；cffi 沒有 abi3 輪子、只能綁版本（cp311），所以不要求全 abi3——
    但平台一定要對，裝錯平台的輪子會 import 就炸。若後端 Python 版本改變，重出這批 wheels。"""
    for p in (ROOT / ".project" / "patch_wheels").glob("*.whl"):
        if p.name.endswith("-py3-none-any.whl"):
            continue
        assert "x86_64" in p.name and "linux" in p.name, p.name
        assert ("abi3" in p.name or "cp311" in p.name), p.name   # abi3 通用、或綁到後端的 cp311


def test_patch_sh離線安裝而且不會連網():
    # relay（去識別化產出物）不帶打包工具 make_patch.py——那裡本來就不打 patch，
    # 沒有要測的東西；在主 repo 與 221 部署前測試照跑。（v1.94.2 在 relay CI 紅過）
    if not (ROOT / ".project" / "make_patch.py").is_file():
        import pytest
        pytest.skip("這份產出物不含 .project/make_patch.py（relay 不帶打包工具）")
    sh = _patch_sh()
    # 判斷「能不能用」不是「import 得到」——殘缺 paramiko（import 成功但子模組缺）也要修
    assert "import paramiko.ssh_exception" in sh, "要驗真正會用到的子模組，不是只 import paramiko"
    line = next(l for l in sh.splitlines() if "pip install" in l and "paramiko" in l)
    assert "--no-index" in line and '--find-links "$HERE/wheels"' in line, line
    assert "--force-reinstall" in line, "殘缺 paramiko 要能蓋掉重裝"
    # 安裝失敗要講清楚後果，不能安靜略過
    assert "會失敗" in sh
