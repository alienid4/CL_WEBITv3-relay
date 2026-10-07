"""去識別化快照（.project/make_relay.py）本身的守門。

為什麼要用「看原始碼」而不是「跑一遍看結果」：
這個 bug **只在 Windows 上長出來**——Python 的 text mode 預設會把 `\n` 翻成
作業系統的換行，Linux 上翻出來還是 `\n`。CI 跑在 Linux，所以功能測試在 CI 上
永遠是綠的，卻擋不住有人在自己的 Windows 機器上產一包 CRLF 的更新包送出去。
平台相依的錯，要用不看平台的方式釘住。

2026-09-23 實際踩到：本機重現 relay 產出後，`bootstrap_watcher.sh` 變成 CRLF，
test_bootstrap_必須是_LF 紅燈。那份要在 Linux/AIX 上跑，CRLF 會讓 `#!/bin/sh`
直接壞掉，而錯誤訊息（`: 未預期的...`）完全看不出原因。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKE_RELAY = ROOT / ".project" / "make_relay.py"
#: 跟 make_relay.py 一起被排除在產出物外的同夥（見 make_relay.py 的 EXCLUDE）。
#: 用它來分辨「跑在產出物上」與「來源 repo 裡的檔真的不見了」。
_ALSO_EXCLUDED = ROOT / ".project" / "desensitize_rules.py"


def _skip_if_relay_artifact() -> str:
    """回 skip 理由；空字串＝這裡是來源 repo，測試照跑。

    ⚠️ **不可以無條件 skip。** 那會讓「有人不小心把 make_relay.py 刪了」
    跟「這裡本來就不該有它」變成同一件事——又是一次「沒查到」被當成「沒問題」。

    分辨方式：`make_relay.py` 與 `desensitize_rules.py` 是**一起**被 EXCLUDE 的。
      兩個都不在 -> 這裡是 relay 產出物，缺席是刻意的
      只有 make_relay.py 不在 -> 來源 repo 的檔真的不見了，要紅燈
    """
    if MAKE_RELAY.exists():
        return ""
    if not _ALSO_EXCLUDED.exists():
        return ("這裡是 relay 產出物：`make_relay.py` 依設計就不在裡面"
                "（見 make_relay.py 的 EXCLUDE——它的替換表直接寫著真實 IP／主機名／"
                "公司識別字，送出去等於把對照表附在包裹裡）。"
                "**絕對不要為了讓這條綠而把它放進產出物。**"
                "這支測試的職責是守來源 repo 的原始碼，在產出物上沒有對象可守。")
    return ""


def test_產出的文字檔一律保留原本的換行():
    """每一個 write_text 都要指定 newline=""，否則 Windows 上產出的包會是 CRLF。"""
    why = _skip_if_relay_artifact()
    if why:
        pytest.skip(why)
    assert MAKE_RELAY.exists(), (
        f"{MAKE_RELAY} 不見了，但 desensitize_rules.py 還在——"
        "這不是 relay 產出物，是來源 repo 的檔真的少了一支")
    src = MAKE_RELAY.read_text(encoding="utf-8")
    calls = re.findall(r"\.write_text\((?:[^()]|\([^()]*\))*\)", src)
    assert calls, "找不到任何 write_text——這支測試的前提變了，要重寫"
    bad = [c for c in calls if "newline=" not in c]
    assert not bad, (
        "這些 write_text 沒有指定 newline=\"\"，在 Windows 上會把整份檔案寫成 CRLF；"
        "送到 Linux/AIX 的 shell script 會直接壞掉：\n  " + "\n  ".join(bad))
