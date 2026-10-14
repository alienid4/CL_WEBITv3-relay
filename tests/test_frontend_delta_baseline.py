"""前端增量包的基準，必須是「公司確認套到的那個 commit」。

2026-09-29 公司主機（SECSVR198-014T）套包實際報錯：

    !! 前端清單驗證失敗：缺 180 檔、內容不符 25 檔
    !! 前端沒有換版（維持原本的版本），請用完整包重試

查證：baseline 2ed7b92（v1.305.0）當時前端是 570 檔，那次 build 是 627 檔，
相對公司真正要換的有 469 檔。但增量包是拿**工作目錄**的 frontend_manifest.json
算差集的——而那份每次 build 完都會被覆寫，記的是「這台機器上次 build 到哪」。
公司跳版套時，這兩者差多少，增量包就漏多少。

後端是「從 release_baseline 起算的累積包」，前端卻是「從上次 build 起算的增量」，
兩邊基準不同——這才是真正的毛病，缺 180 檔只是它顯示出來的樣子。
"""
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


def test_讀得到某個_commit_當時的前端清單():
    # HEAD 一定有這份檔（它是版控裡的）
    got = mp._baseline_manifest("HEAD")
    assert isinstance(got, dict) and got, "讀不到 HEAD 的前端清單"


def test_讀不到就回空的_讓呼叫端退回完整包():
    # 不存在的 ref：不可以丟例外，也不可以回半套——
    # 回空的，呼叫端才知道要改用完整包。
    assert mp._baseline_manifest("0000000000000000000000000000000000000000") == {}


def test_基準清單跟工作目錄那份是兩回事():
    """工作目錄那份每次 build 都會被覆寫，不可以拿來當基準。"""
    import inspect
    src = inspect.getsource(mp)
    # 差集的 base 必須來自 _baseline_manifest，不可以是 manifest_path.read_text
    assert "base = _baseline_manifest(since)" in src, \
        "前端增量的基準沒有改讀 baseline commit"
    assert "base = json.loads(manifest_path.read_text" not in src, \
        "還在拿工作目錄的清單當基準——公司跳版套就會缺檔"


# ===== 換 Python 版本那段，不可以用 os.execv =====

def test_不可以用_os_execv_換版本():
    """Windows 上 os.execv 不取代行程，父行程會立刻以 0 離開。

    2026-09-29 踩到：relay 推送回報 exit=0、relay HEAD 卻沒變，
    查下去發現子行程還在跑 npm install——「成功」是假的。
    看離開碼的東西（背景工作、CI、腳本）全部會被騙。
    """
    src = (ROOT / ".project" / "relay_local.py").read_text(encoding="utf-8")
    # 只看程式碼，註解裡提到它是為了講清楚為什麼不用，不算違規
    code = "".join(l for l in src.splitlines(True) if not l.lstrip().startswith("#"))
    assert "os.execv" not in code, "os.execv 在 Windows 上會讓父行程假裝成功"
    assert "raise SystemExit(r.returncode)" in src, "沒有把子行程的離開碼傳回去"
