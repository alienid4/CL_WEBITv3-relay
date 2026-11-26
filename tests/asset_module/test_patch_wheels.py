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


def test_patch_wheels內附pypsrp_而且requirements有列():
    """Windows 收集改走 PSRP（2026-10-20）：公司機離線，patch 不附 pypsrp 就會「patch 成功、Windows 全部收不到」。
    pypsrp／pyspnego 是純 Python；其餘相依（requests、cryptography）已由 pywinrm／paramiko 鏈帶上。"""
    names = {p.name.split("-")[0].lower() for p in (ROOT / ".project" / "patch_wheels").glob("*.whl")}
    assert "pypsrp" in names and "pyspnego" in names
    req = (ROOT / "APP" / "asset-module" / "backend" / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "pypsrp" in req, "install pack 的離線 wheels 由 requirements.txt 產生，沒列就不會帶"


def test_patch_sh離線安裝pypsrp而且用服務帳號驗證():
    if not (ROOT / ".project" / "make_patch.py").is_file():
        import pytest
        pytest.skip("這份產出物不含 .project/make_patch.py（relay 不帶打包工具）")
    sh = _patch_sh()
    line = next(l for l in sh.splitlines() if "pip install" in l and "pypsrp" in l)
    assert "--no-index" in line and '--find-links "$HERE/wheels"' in line, line
    assert "import pypsrp.wsman" in sh
    assert '_as_svc "$VENV_PY" -c "$_PSCHK"' in sh


def test_patch_wheels內附ldap3與pyasn1_requirements鎖版本_patch_sh離線裝():
    """AD 人員名單 LDAPS 同步（2.068.x）：公司機離線，patch 不附 ldap3 就會「patch 成功、AD 同步一直失敗」。
    2.068.2：版本鎖定＋雜湊（--require-hashes）＋裝完 pip check。"""
    names = {p.name.split("-")[0].lower() for p in (ROOT / ".project" / "patch_wheels").glob("*.whl")}
    assert "ldap3" in names and "pyasn1" in names
    req = (ROOT / "APP" / "asset-module" / "backend" / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "ldap3==2.9.1" in req and "pyasn1==0.6.5" in req, "install pack 的離線 wheels 由 requirements.txt 產生，要鎖版本"
    if not (ROOT / ".project" / "make_patch.py").is_file():
        return                                       # relay 不帶打包工具
    sh = _patch_sh()
    line = next(l for l in sh.splitlines() if "pip install" in l and "_LDREQ" in l)
    assert "--no-index" in line and '--find-links "$HERE/wheels"' in line and "--require-hashes" in line, line
    assert '_as_svc "$VENV_PY" -c "$_LDCHK"' in sh and "pip check" in sh


def _ldap_req_lines() -> list[str]:
    sh = _patch_sh()
    body = sh.split("<<'LDREQ'\n", 1)[1].split("\nLDREQ\n", 1)[0]
    return [l for l in body.splitlines() if l.strip()]


def test_patch_sh的ldap3雜湊_跟實際打包的wheel一致_而且真的裝得起來(tmp_path):
    """檢查實際打包產物：patch.sh 寫死的 sha256 必須等於 .project/patch_wheels 裡那兩個檔；
    再用同一份 requirement（--require-hashes、--no-index、只從 patch_wheels 找）真的裝到暫存資料夾、import 得到。"""
    import hashlib
    import subprocess
    if not (ROOT / ".project" / "make_patch.py").is_file():
        import pytest
        pytest.skip("這份產出物不含 .project/make_patch.py（relay 不帶打包工具）")
    lines = _ldap_req_lines()
    want = {l.split("==")[0]: (l.split("==")[1].split()[0], l.split("sha256:")[1].strip()) for l in lines}
    assert set(want) == {"ldap3", "pyasn1"}
    for name, (ver, h) in want.items():
        whl = next((ROOT / ".project" / "patch_wheels").glob(f"{name}-{ver}-*.whl"))
        assert hashlib.sha256(whl.read_bytes()).hexdigest() == h, whl.name
    req = tmp_path / "req.txt"
    req.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tgt = tmp_path / "site"
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--find-links", str(ROOT / ".project" / "patch_wheels"),
                        "--require-hashes", "--no-deps", "--target", str(tgt), "-r", str(req)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
    chk = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import ldap3, ldap3.utils.dn, pyasn1; "
                          "print(ldap3.__version__)", str(tgt)], capture_output=True, text=True)
    assert chk.returncode == 0 and chk.stdout.strip() == "2.9.1", chk.stderr[-500:]
    bad = tmp_path / "bad.txt"                       # 雜湊不對＝整個不裝
    bad.write_text(lines[0].split("sha256:")[0] + "sha256:" + "0" * 64 + "\n", encoding="utf-8")
    r2 = subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--find-links", str(ROOT / ".project" / "patch_wheels"),
                         "--require-hashes", "--no-deps", "--target", str(tmp_path / "site2"), "-r", str(bad)], capture_output=True, text=True)
    assert r2.returncode != 0


def test_二進位套件是linux_x86_64():
    """公司主機（.14）後端 venv 已確認是 Linux x86_64 / Python 3.11（不是系統的 3.9）。
    C 擴充輪子必須是 linux x86_64；cffi 沒有 abi3 輪子、只能綁版本（cp311），所以不要求全 abi3——
    但平台一定要對，裝錯平台的輪子會 import 就炸。若後端 Python 版本改變，重出這批 wheels。"""
    for p in (ROOT / ".project" / "patch_wheels").glob("*.whl"):
        if p.name.endswith("-none-any.whl"):          # 純 Python（py3 或 py2.py3）不分平台
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
