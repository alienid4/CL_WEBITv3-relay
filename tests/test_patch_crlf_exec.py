# -*- coding: utf-8 -*-
r"""patch.sh 的「修換行 + 權限」那一步不能照副檔名篩檔案。

為什麼要有這支：AIX 稽核腳本上游本來就沒有副檔名（`scripts/fcbaixsh`，
2026-09-30 才在 AIX 上更名為 `fcb_aix.ksh`）。原本 patch.sh 只做
`find ... -name '*.sh'`，這幾支於是：

    · 沒被轉成 LF → 經 Windows 中轉後是 CRLF，到公司主機上直接
      `語法錯誤 $'do\r' 附近` 整支跑不動
    · 沒被 chmod +x → 就算換行對了也還是 Permission denied

.gitattributes 在 2026-09-22 為了同一個原因加寬過一次（見該檔註解）；
打包端這半邊當時沒一起補，所以再踩一次。用測試把行為釘死：
**判斷依據是檔頭的 `#!` shebang，不是副檔名。**
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".project"))

try:
    import make_patch as mp
except ImportError:  # pragma: no cover - relay 快照裡沒有 make_patch
    pytest.skip("make_patch 不在 relay 快照裡，本測試只在主 repo 執行",
                allow_module_level=True)


def _step3() -> str:
    """把 PATCH_SH 的第 3 步切出來單獨跑（整支要 root＋systemd，測不動）。"""
    m = re.search(r"^# ===== 3\..*?(?=^# ===== 4\.)", mp.PATCH_SH, re.S | re.M)
    assert m, "PATCH_SH 找不到第 3 步的段落標記，切法要跟著改"
    return m.group(0)


# ===== 靜態檢查：哪台機器都跑得到 =====

def test_不可以只靠副檔名篩():
    step = _step3()
    assert "head -c 2" in step and "'#!'" in step, \
        "要用檔頭的 #! shebang 認腳本，不能只看副檔名"
    assert "! -name '*.*'" in step, "沒有副檔名的檔案要一起撈進來看"
    assert "-name '*.ksh'" in step, "fcb_aix.ksh 這類 .ksh 要收"


def test_保留原本為什麼要有這一步的說明():
    step = _step3()
    assert "CRLF" in step and "command not found" in step, \
        "這一步存在的理由（Windows 中轉變 CRLF）要留在註解裡"
    assert ".gitattributes" in step, "要留下跟 .gitattributes 同源的線索"


# ===== 實跑：在有 bash 的環境驗真的行為 =====

SCRIPTS = ("scripts/fcbaixsh", "scripts/fcb_aix.ksh",
           "scripts/fcb_collect.sh", "deploy.sh")
NOT_SCRIPTS = ("scripts/fcb_aix使用說明.md", "backend/NOTES")

# Windows 的 os.stat 沒有 POSIX 權限位，一律回報 0666，驗不出 chmod +x。
posix_only = pytest.mark.skipif(sys.platform == "win32",
                                reason="執行位是 POSIX 才有的，Windows 驗不到")


def _mkapp(tmp_path: Path) -> Path:
    """擺出跟 patch 包同樣形狀的 $APP：backend/、scripts/ 都在第一層。"""
    app = tmp_path / "app"
    (app / "scripts").mkdir(parents=True)
    (app / "backend").mkdir()
    crlf = b"#!/bin/ksh\r\nfor i in 1 2\r\ndo\r\n  echo $i\r\ndone\r\n"
    for rel in SCRIPTS:                     # 舊名（沒副檔名）／新名（.ksh）／.sh／根目錄
        (app / rel).write_bytes(crlf)
    for rel in NOT_SCRIPTS:                 # 沒有 shebang，不是腳本
        (app / rel).write_bytes("# 說明\r\n".encode("utf-8"))
    return app


def _bash_usable(b) -> bool:
    """bash 存在還不夠，要真的跑得動帶原生路徑的腳本。Windows 的 bash.EXE（WSL）
    把 `C:/...` 當成 WSL 下不存在的路徑：rc 仍是 0、卻什麼檔都沒處理（轉 0 支、
    留 CRLF），於是測試假紅。git-bash（MSYS）／Linux 才真的驗得到換行那半邊。
    用實測探針分辨：跑不動就 skip（不是程式壞，是這台沒有能跑的 bash）。"""
    if not b:
        return False
    import tempfile as _tf
    d = _tf.mkdtemp()
    try:
        p = Path(d) / "probe.sh"
        p.write_text("#!/bin/sh\nprintf ok\n", encoding="utf-8", newline="\n")
        r = subprocess.run([b, str(p)], capture_output=True, timeout=30)
        return r.returncode == 0 and r.stdout.strip() == b"ok"
    except Exception:
        return False
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _run_step3(app: Path) -> str:
    """實跑這一步。換行那半邊連 Windows 的 git-bash 都驗得到，所以只要 bash 跑得動
    就跑——本機閘門在 Windows 上一律 skip 的話，等於這支永遠沒真的被執行過。"""
    bash = shutil.which("bash")
    if not _bash_usable(bash):
        pytest.skip("這台的 bash 跑不了帶原生路徑的腳本（如 WSL），無法實跑")
    script = 'set -uo pipefail\nAPP="$1"\n' + _step3()
    # patch.sh 的訊息是中文；不指定 utf-8 的話 Windows 會用 cp950 解，直接炸。
    r = subprocess.run([bash, "-c", script, "step3", app.as_posix()],
                       capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_沒有副檔名的腳本也會轉成LF(tmp_path: Path):
    app = _mkapp(tmp_path)
    _run_step3(app)
    for rel in SCRIPTS:
        assert b"\r" not in (app / rel).read_bytes(), \
            f"{rel} 還是 CRLF，到主機上會跑不動"


@posix_only
def test_沒有副檔名的腳本也會拿到執行位(tmp_path: Path):
    app = _mkapp(tmp_path)
    _run_step3(app)
    for rel in SCRIPTS:
        assert (app / rel).stat().st_mode & 0o111, f"{rel} 沒有執行位"


@posix_only
def test_不是腳本的檔案不會被加上執行位(tmp_path: Path):
    app = _mkapp(tmp_path)
    _run_step3(app)
    for rel in NOT_SCRIPTS:
        assert not (app / rel).stat().st_mode & 0o111, \
            f"{rel} 沒有 shebang，不該變成可執行檔"


def test_不是腳本的檔案不會被動到(tmp_path: Path):
    # 說明文件被順手轉成 LF 沒有壞處，但那代表篩選條件沒收緊，
    # 下一步就會連 .py、圖檔都一起 chmod +x。原封不動才是對的。
    app = _mkapp(tmp_path)
    before = {rel: (app / rel).read_bytes() for rel in NOT_SCRIPTS}
    _run_step3(app)
    for rel in NOT_SCRIPTS:
        assert (app / rel).read_bytes() == before[rel], f"{rel} 不該被改到"


def test_轉過的支數會報出來_看得出有沒有漏(tmp_path: Path):
    # 「找到 0 支」要能跟「根本沒掃」分得出來，所以這一步要把數量印出來。
    out = _run_step3(_mkapp(tmp_path))
    m = re.search(r"正規化 (\d+) 支腳本", out)
    assert m, f"沒有印出正規化的支數：\n{out}"
    assert int(m.group(1)) == len(SCRIPTS), \
        f"應該是 {len(SCRIPTS)} 支（含沒有副檔名的），實際 {m.group(1)}\n{out}"
