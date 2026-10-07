"""更新包裡的權限不可以抄磁碟上的。

為什麼要有這支：2026-09-29 查證 relay 上兩個包，**同一份程式碼、不同機器產的，
權限完全不同**——

    CI（Linux runner） 65 檔 0644 ／ 18 目錄 0755
    本機（Windows）    69 檔 0666 ／ 18 目錄 0777   ← 全世界可寫

Windows 沒有 POSIX 權限位，`os.stat` 一律回報 0666/0777，`tarfile` 就忠實地
寫進包裡。這個包會被 root 解到 /opt，於是**任何本機帳號都能改寫 root 會執行的
程式**——這不是外觀問題，是實質的本機提權（全域鐵律：root 不執行來路可控的
程式碼）。v1.371.0 那包已經這樣送到公司一次。

GitHub Actions 停掉之後所有包都改在 Windows 上產，所以這條從「CI 幫我們擋著」
變成「沒有人擋」。用測試釘死。
"""
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".project"))

try:
    import make_patch as mp
except ImportError:  # pragma: no cover - relay 快照裡沒有 make_patch
    pytest.skip("make_patch 不在 relay 快照裡，本測試只在主 repo 執行",
                allow_module_level=True)


def _tar(tmp_path: Path) -> Path:
    """用 _norm 打一個包，內容不重要，看的是 tar 裡記了什麼權限。"""
    stage = tmp_path / "stage"
    (stage / "sub").mkdir(parents=True)
    (stage / "patch.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    (stage / "sub" / "a.py").write_text("x = 1\n", encoding="utf-8")
    tgz = tmp_path / "p.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(stage, arcname="p", filter=mp._norm)
    return tgz


def test_沒有任何檔案是全世界可寫(tmp_path: Path):
    with tarfile.open(_tar(tmp_path)) as tf:
        bad = [f"{m.name} {oct(m.mode)}" for m in tf.getmembers() if m.mode & 0o002]
    assert not bad, "這些項目全世界可寫，root 解開後任何本機帳號都能改：\n  " + "\n  ".join(bad)


def test_權限固定是_0644_與_0755(tmp_path: Path):
    with tarfile.open(_tar(tmp_path)) as tf:
        for m in tf.getmembers():
            want = 0o755 if m.isdir() else 0o644
            assert m.mode == want, f"{m.name} 是 {oct(m.mode)}，應該是 {oct(want)}"


def test_擁有者歸零_誰產的包都一樣(tmp_path: Path):
    # CI 產的會寫成 runner/runner，本機產的寫成別的名字。
    # 對帳時「同一份程式碼產出的包」應該長得一樣，不該看得出是誰產的。
    with tarfile.open(_tar(tmp_path)) as tf:
        for m in tf.getmembers():
            assert (m.uid, m.gid, m.uname, m.gname) == (0, 0, "", ""), \
                f"{m.name} 帶著產包者的身分：{m.uid}/{m.gid} {m.uname}/{m.gname}"


# ===== 承接端：已經在架上的壞包要掉下來 =====
# 打包端釘死權限只擋得住「以後產的」。v1.371.0 那包已經在 relay 上，
# carry_patches 每次原樣搬過去，要再推 10 包才擠得掉——中間公司隨時抓得到。

try:
    import make_relay as mr
except ImportError:  # pragma: no cover
    mr = None


def _make_pkg(day_dir: Path, name: str, mode: int) -> Path:
    day_dir.mkdir(parents=True, exist_ok=True)
    src = day_dir.parent / f"_src_{name}"
    src.mkdir(exist_ok=True)
    (src / "patch.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    tgz = day_dir / f"{name}.tar.gz"

    def _mode(ti):
        ti.mode = 0o755 if ti.isdir() else mode
        return ti

    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(src, arcname=name, filter=_mode)
    (day_dir / f"{name}.sha256").write_text("x  " + tgz.name + "\n", encoding="utf-8")
    return tgz


@pytest.mark.skipif(mr is None, reason="make_relay 不在 relay 快照裡")
def test_全世界可寫的舊包會被丟掉_好的留著(tmp_path: Path):
    patches = tmp_path / "patches"
    bad = _make_pkg(patches / "20260924", "patch_20260924_1223_v1.371.0", 0o666)
    good = _make_pkg(patches / "20260929", "patch_20260929_1200_v1.381.0", 0o644)

    dropped = mr._drop_world_writable(patches)

    assert [b for b in dropped if "1.371.0" in b], f"壞包沒被丟：{dropped}"
    assert not bad.exists(), "壞包的 tar.gz 還在"
    assert not bad.with_suffix("").with_suffix(".sha256").exists(), "壞包的 sha256 沒一起刪"
    assert good.exists(), "好包被誤刪了"


@pytest.mark.skipif(mr is None, reason="make_relay 不在 relay 快照裡")
def test_丟光的日期目錄不留空殼(tmp_path: Path):
    patches = tmp_path / "patches"
    _make_pkg(patches / "20260924", "patch_20260924_1223_v1.371.0", 0o666)
    mr._drop_world_writable(patches)
    assert not (patches / "20260924").exists(), "整天的包都丟光了，空目錄不該留著騙人"
