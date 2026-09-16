"""san_collector.split_transcript 測試（離線匯入用；合成資料）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import san_collector  # noqa: E402

TRANSCRIPT = """\
TESTSW01:admin> switchshow
switchName:     TESTSW01
Index Port Address Media Speed State     Proto
==============================================
  0    0   010000  id    N32   Online    FC  F-Port  20:00:00:00:00:00:aa:01
TESTSW01:admin> alishow
Defined configuration:
 alias: hostA_hba0
                20:00:00:00:00:00:aa:01
TESTSW01:admin>
"""


def test_split_by_command_echo():
    outs = san_collector.split_transcript(TRANSCRIPT)
    assert set(outs) == {"switchshow", "alishow"}
    assert "switchName:     TESTSW01" in outs["switchshow"]
    assert "hostA_hba0" in outs["alishow"]
    # 指令回音那一行本身不算進輸出
    assert "switchshow" not in outs["switchshow"].splitlines()[0]


def test_ignores_non_whitelisted_lines():
    outs = san_collector.split_transcript("foo> ls\nbar\nfoo> switchshow\nswitchName: X\n")
    assert set(outs) == {"switchshow"}   # 'ls' 不在白名單，不當成段落
