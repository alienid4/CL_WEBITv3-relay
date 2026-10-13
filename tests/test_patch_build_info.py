"""patch 安裝的機器也要答得出「我跑的是哪個 commit」。

2026-09-30 公司主機（10.92.198.14）套完 v1.383.0，畫面顯示：

    v1.383.0 · n/a
                ^^^ commit 這一欄

查證：`/api/version` 的 git_commit 是直接問 git 的——服務跑在 git 工作區上時
那最準，因為 stamp 檔會被「檔案換了但服務沒重啟」騙過去（2026-07-18 踩過）。
但公司主機是 patch 安裝、**沒有 git 工作區**，就退回讀 backend/build_info.json，
而我們從來沒把這個檔放進包裡。

顯示 n/a 本身是誠實的，但代價是出事時查不出那台跑的是哪一版程式——
而那正是要這一欄的唯一理由。
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


def test_打包會寫_build_info到_backend():
    import inspect
    src = inspect.getsource(mp)
    assert 'stage / "files" / "backend" / "build_info.json"' in src, \
        "沒有把 build_info.json 放進 files/backend/——patch 機器的 commit 會是 n/a"
    assert '"git_commit": head' in src, "build_info.json 裡沒有寫 commit"


def test_放的位置跟後端讀的位置一致():
    """api.py 讀的是 `Path(__file__).parent / 'build_info.json'`，
    也就是 backend 目錄；打包要放進 files/backend/ 才會被 patch.sh 複製到那裡。"""
    api = (ROOT / "APP" / "asset-module" / "backend" / "api.py").read_text(encoding="utf-8")
    assert 'here / "build_info.json"' in api, \
        "後端讀取位置變了，打包端要跟著改"
    assert "here = Path(__file__).parent" in api, \
        "here 不再是 backend 目錄，build_info.json 會放錯地方"
