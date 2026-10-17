"""「同一台」只能有一把尺：system_stats.machine_key（[B-03] 2026-09-18）。

B-03 改 machine_key（加網域處理）時，陸續發現四個地方各自有一份複製品或另一套規則：
api._duplicate_groups（SQL GROUP BY）、vip_view.duplicate_summary、system_report._dup_key、
manage_state.composition 的 _mkey。複製品不會跟著改——首頁「在管」3,116 vs 主機清單 3,113。
這支掃後端原始碼：除了 system_stats，不准自己拼「主機名|IP」鍵。
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
# f"{h}|{i}"、f"{host}|{ip}" 這類——自己拼 machine key 的寫法
PAT = re.compile(r'f"\{[a-z_]+\}\|\{[a-z_]+\}"')


def test_只有system_stats可以拼同一台的鍵():
    bad = []
    for f in sorted(BACKEND.glob("*.py")):
        if f.name == "system_stats.py":
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if PAT.search(code):
                bad.append(f"  {f.name}:{i}  {line.strip()[:90]}")
    assert not bad, "這些地方自己拼「主機名|IP」鍵，請改呼叫 system_stats.machine_key：\n" + "\n".join(bad)


def test_網域與堆疊成員():
    import sys
    sys.path.insert(0, str(BACKEND))
    import system_stats as ss
    assert ss.machine_key("ap02.sit.example.com.tw", "192.0.2.1", None) == ss.machine_key("AP02", "192.0.2.1", None)
    assert ss.machine_key("sw-254.254-1", "192.0.2.9", None) != ss.machine_key("sw-254.254-2", "192.0.2.9", None), \
        "堆疊成員點後面不是網域，不可以被併成一台"
    assert ss.machine_key("host.local", "192.0.2.3", None) == "host.local|192.0.2.3", "單段不算網域"
