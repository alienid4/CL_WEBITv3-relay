"""relay 更新包的擺放與修剪。

為什麼要有測試：修剪會**刪掉 public repo 裡的檔案**。今天的教訓是「沒驗過的路徑
等於不存在」，而這條路徑平常不會觸發（要累積超過上限才會），等它第一次真的動時
才發現刪錯，檔案已經沒了。

擺放規範（使用者 2026-08-16 指正）：`patches/YYYYMMDD/patch_YYYYMMDD_HHMM.tar.gz`。
先前我自作主張放成固定的 `patches/latest/patch.tar.gz`，不合規範。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".project"))

# make_relay.py 自己在它的 EXCLUDE 清單裡——它的替換表寫著真實 IP／主機名／公司識別字，
# 不能進去識別化快照。所以在 relay 產出物上跑測試時這支 import 一定失敗，
# 那不是壞掉，是設計如此：這支測試只在主 repo 有意義。
# （第一次沒加這層，relay 的「在產出物上跑測試」閘門直接擋下整包——閘門是對的。）
try:
    import make_relay as mr  # noqa: E402
except ImportError:  # pragma: no cover - 只有在 relay 快照裡才會走到
    pytest.skip("make_relay 不在 relay 快照裡（EXCLUDE），本測試只在主 repo 執行",
                allow_module_level=True)


def _pkg(root: Path, day: str, stamp: str) -> None:
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    (d / f"patch_{stamp}.tar.gz").write_bytes(b"x")
    (d / f"patch_{stamp}.sha256").write_text("deadbeef", encoding="utf-8")


def test_擺放照規範_日期目錄加時間戳檔名(tmp_path):
    src = tmp_path / "src"
    _pkg(src, "20260816", "20260816_1642")
    out = tmp_path / "out"
    (out / "patches").mkdir(parents=True)

    added, pruned = mr.add_patch_dir(out, src)

    assert added == 2 and pruned == 0
    assert (out / "patches" / "20260816" / "patch_20260816_1642.tar.gz").is_file()
    assert (out / "patches" / "20260816" / "patch_20260816_1642.sha256").is_file()


def test_不會蓋掉承接下來的舊包(tmp_path):
    """force push 蓋掉的是遠端，舊包是承接回來的——新包只能「併進去」不能取代整個 patches/。"""
    out = tmp_path / "out"
    _pkg(out / "patches", "20260814", "20260814_2007")
    src = tmp_path / "src"
    _pkg(src, "20260816", "20260816_1642")

    mr.add_patch_dir(out, src)

    assert (out / "patches" / "20260814" / "patch_20260814_2007.tar.gz").is_file()
    assert (out / "patches" / "20260816" / "patch_20260816_1642.tar.gz").is_file()


def test_舊版自作主張的latest目錄會被清掉(tmp_path):
    """它是從遠端承接下來的，不主動刪就會永遠留著，讓人不知道該抓哪個。"""
    out = tmp_path / "out"
    legacy = out / "patches" / "latest"
    legacy.mkdir(parents=True)
    (legacy / "patch.tar.gz").write_bytes(b"x")
    src = tmp_path / "src"
    _pkg(src, "20260816", "20260816_1642")

    mr.add_patch_dir(out, src)

    assert not legacy.exists()


def test_只留最新N包_舊的連sha256一起刪(tmp_path):
    root = tmp_path / "patches"
    for i in range(1, 8):
        _pkg(root, "20260810", f"20260810_{i:02d}00")

    pruned = mr.prune_patches(root, keep=3)

    assert pruned == 4
    left = sorted(p.name for p in root.rglob("patch_*.tar.gz"))
    assert left == ["patch_20260810_0500.tar.gz", "patch_20260810_0600.tar.gz",
                    "patch_20260810_0700.tar.gz"]
    # .sha256 要跟著走，不能留一堆對不到包的孤兒校驗碼
    assert sorted(p.name for p in root.rglob("*.sha256")) == [
        "patch_20260810_0500.sha256", "patch_20260810_0600.sha256",
        "patch_20260810_0700.sha256"]


def test_修剪用檔名排序不是檔案時間(tmp_path):
    """檔案時間會被複製/解壓改掉（承接回來的舊包 mtime 是「剛剛」），
    拿它當依據會把最新的當成最舊的刪掉。檔名本身就是打包時間。"""
    root = tmp_path / "patches"
    _pkg(root, "20260814", "20260814_2007")     # 舊
    _pkg(root, "20260816", "20260816_1642")     # 新
    # 讓「舊」的 mtime 比「新」的還新，模擬承接回來的情況
    for f in (root / "20260814").iterdir():
        f.touch()

    mr.prune_patches(root, keep=1)

    assert (root / "20260816" / "patch_20260816_1642.tar.gz").is_file()
    assert not (root / "20260814").exists()


def test_修剪後空掉的日期目錄要清掉(tmp_path):
    root = tmp_path / "patches"
    _pkg(root, "20260810", "20260810_0100")
    _pkg(root, "20260816", "20260816_1642")

    mr.prune_patches(root, keep=1)

    assert not (root / "20260810").exists()


def test_沒超過上限時一包都不動(tmp_path):
    root = tmp_path / "patches"
    _pkg(root, "20260816", "20260816_1642")
    assert mr.prune_patches(root, keep=10) == 0
    assert (root / "20260816" / "patch_20260816_1642.tar.gz").is_file()


def test_來源沒有更新包要明確報錯_不要安靜產出空的(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    (out / "patches").mkdir(parents=True)
    try:
        mr.add_patch_dir(out, src)
    except RuntimeError:
        return
    raise AssertionError("空的來源目錄應該明確報錯")


def test_不要把make_patch的暫存目錄一起搬出去(tmp_path):
    """make_patch 會在日期目錄下留 .stage_* 暫存，那是內部產物，送出去只是垃圾。"""
    src = tmp_path / "src"
    _pkg(src, "20260816", "20260816_1642")
    stage = src / "20260816" / ".stage_20260816_1642" / "files"
    stage.mkdir(parents=True)
    (stage / "api.py").write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    (out / "patches").mkdir(parents=True)

    mr.add_patch_dir(out, src)

    assert not list((out / "patches").rglob(".stage_*"))
    assert not list((out / "patches").rglob("api.py"))


# ===== 2026-10-13：檔名加系統名 patch_webit3_YYYYMMDD_HHMM_v<版本> =====
# relay 與公司端都還有舊名的包，承接回來會跟新包混放；讀取端新舊都要認得，
# 而且「哪包最新」要用版本序（1.417.0 修 install.sh 時踩過字典序挑錯包）。

def _named(root: Path, day: str, name: str) -> None:
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.tar.gz").write_bytes(b"x")
    (d / f"{name}.sha256").write_text("deadbeef", encoding="utf-8")


def test_檔名解析_新舊三種格式都認得():
    k_new = mr.patch_sort_key("patch_webit3_20261013_1842_v1.426.0.tar.gz")
    k_old = mr.patch_sort_key("patch_20261013_1842_v1.426.0.tar.gz")
    k_older = mr.patch_sort_key("patch_20260816_1642.tar.gz")
    assert k_new == k_old == (1, (1, 426, 0), "20261013_1842")
    assert k_older == (1, (), "20260816_1642")
    # 認不出來的排最前面（最先被修剪），不拋錯
    assert mr.patch_sort_key("patch_亂七八糟.tar.gz") < k_older


def test_新舊混放_修剪留下版本最大的不是字典序最大的(tmp_path):
    """字典序下 `patch_2…` < `patch_w…`、`1.9.0` > `1.10.0`，兩個都會挑錯。"""
    root = tmp_path / "patches"
    _named(root, "20261014", "patch_webit3_20261014_0900_v1.9.0")   # 新名、版本小
    _named(root, "20261013", "patch_20261013_1842_v1.426.0")         # 舊名、版本大
    _named(root, "20261012", "patch_20261012_1000_v1.10.0")          # 舊名、版本中

    mr.prune_patches(root, keep=1)

    left = sorted(p.name for p in root.rglob("*") if p.is_file())
    assert left == ["patch_20261013_1842_v1.426.0.sha256",
                    "patch_20261013_1842_v1.426.0.tar.gz"]


def test_新舊混放_同版本取打包時間較晚的(tmp_path):
    root = tmp_path / "patches"
    _named(root, "20261013", "patch_20261013_1842_v1.426.0")
    _named(root, "20261013", "patch_webit3_20261013_1900_v1.426.0")

    mr.prune_patches(root, keep=1)

    assert (root / "20261013" / "patch_webit3_20261013_1900_v1.426.0.tar.gz").is_file()
    assert (root / "20261013" / "patch_webit3_20261013_1900_v1.426.0.sha256").is_file()
    assert not (root / "20261013" / "patch_20261013_1842_v1.426.0.tar.gz").exists()
    assert not (root / "20261013" / "patch_20261013_1842_v1.426.0.sha256").exists()


def test_新檔名的包也能放進來(tmp_path):
    src = tmp_path / "src"
    _named(src, "20261013", "patch_webit3_20261013_1842_v1.426.0")
    out = tmp_path / "out"
    _named(out / "patches", "20261012", "patch_20261012_1000_v1.420.0")   # 承接的舊名包

    added, pruned = mr.add_patch_dir(out, src)

    assert added == 2 and pruned == 0
    assert (out / "patches" / "20261013" / "patch_webit3_20261013_1842_v1.426.0.tar.gz").is_file()
    assert (out / "patches" / "20261012" / "patch_20261012_1000_v1.420.0.tar.gz").is_file()


def test_make_patch產生端與relay讀取端用同一種格式():
    """產生端改了格式、讀取端沒跟上，修剪就會把新包當成「認不出來」最先刪掉。"""
    try:
        import make_patch as mp
    except ImportError:  # pragma: no cover
        pytest.skip("make_patch 不在 relay 快照裡")
    name = mp.patch_name("20261013_1842", "1.426.0")
    assert name == "patch_webit3_20261013_1842_v1.426.0"
    assert mr.PATCH_NAME_RE.match(name + ".tar.gz")["system"] == "webit3"
    assert mr.patch_sort_key(name + ".tar.gz") == (1, (1, 426, 0), "20261013_1842")
    # 讀不到版號時不寫出 `_v` 空尾巴
    assert mp.patch_name("20261013_1842", "") == "patch_webit3_20261013_1842"
    assert mr.patch_sort_key("patch_webit3_20261013_1842.tar.gz")[0] == 1
