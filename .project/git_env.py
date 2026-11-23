#!/usr/bin/env python3
"""清掉會讓 git 打錯 repo 的繼承環境變數。

**為什麼要這支**：git 執行 hook（pre-commit 等）時，會在環境裡塞 `GIT_DIR`
指向真 repo。此時任何 `git -C <暫存目錄> init` 都**不是**對那個暫存目錄做，
而是對 `GIT_DIR` 指到的真 repo 做——把主 repo 設成 `core.bare=true`，
整個工作區看起來就壞了（git status 會說 this operation must be run in a work tree）。

2026-10-13 實際踩到：`.project/checks.py` 的 `_relay_source_sha()` 要在暫存目錄
建一個 repo 去問 relay 進度，沒清 GIT_DIR，於是**每次在這個專案 commit
都把主 repo 翻成 bare 一次**，當天反覆發生、每次都要手動 `git config core.bare false`。

**用法**：凡是「在暫存目錄或指定 cwd 操作另一個 repo」的 subprocess，
一律帶 `env=clean_git_env()`。不要只改出事的那一行——同一支腳本裡其他
git 呼叫一樣會中招，所以 relay_local.py / sync_from_221.py 是改在共用的 run() 裡。
"""
from __future__ import annotations

import os

#: git 用這些環境變數覆寫「這次要操作哪個 repo」。繼承下去就會打錯對象。
#: 前 5 個是 hook 必塞的；後 2 個在 rebase/merge 等情境也會出現，一併清掉。
INHERITED_GIT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def clean_git_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """回一份拿掉 repo 指向變數的環境副本（不動呼叫端的 os.environ）。"""
    env = dict(os.environ if base is None else base)
    for name in INHERITED_GIT_VARS:
        env.pop(name, None)
    return env
