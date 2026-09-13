"""backend 不准再出現 sshpass（2026-09-11 移除）。

sshpass 在多數金融環境列為禁用／告警工具。納管要對上千台跑，只要程式裡還呼叫它，
每跑一次就是一筆 SOC 告警，而觸發的帳號持有全機隊金鑰。現在改用程式內 SSH 用戶端
（paramiko）——這支測試守住「不會有人為了方便又把它加回來」。

同一支順便守 paramiko 內建的兩個不安全主機金鑰策略：
- AutoAddPolicy：只記在記憶體、不寫 known_hosts → 每次都是首見＝沒有驗證
- WarningPolicy：只警告照連 → 等於 StrictHostKeyChecking=no

只放過**註解**：解釋「為什麼拿掉」的註解是有價值的。字串（含 docstring）一律不放過——
`["sshpass", "-e", ...]` 就是字串，放過字串等於這支測試什麼都沒守。
"""
import io
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
BANNED = ("sshpass", "autoaddpolicy", "warningpolicy")
_SKIP = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE}


def banned_hits(source: str) -> list[tuple[int, str]]:
    """回傳 (行號, 命中的字)。註解以外的任何 token 都算。"""
    hits = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in _SKIP:
            continue
        low = tok.string.lower()
        hits.extend((tok.start[0], b) for b in BANNED if b in low)
    return hits


def _backend_py_files():
    return [p for p in BACKEND.rglob("*.py") if "__pycache__" not in p.parts]


def test_backend除了註解以外不准出現sshpass():
    files = _backend_py_files()
    assert files, f"找不到 backend 的 .py：{BACKEND}"
    bad = []
    for p in files:
        for line, word in banned_hits(p.read_text(encoding="utf-8")):
            bad.append(f"{p.relative_to(ROOT)}:{line} 出現 {word}")
    assert not bad, (
        "backend 出現禁用的 SSH 樣式（sshpass 會觸發 SOC 告警；AutoAddPolicy／"
        "WarningPolicy 等於不驗主機金鑰）。改用 onboard_engine._ssh_exec：\n"
        + "\n".join(bad))


def test_守門本身抓得到():
    """突變驗證：確認這支測試真的會紅，不是永遠綠。"""
    assert banned_hits('cmd = ["sshpass", "-e", "ssh"]\n'), "字串裡的 sshpass 沒被抓到"
    assert banned_hits('def f():\n    """用 sshpass 登入"""\n'), "docstring 裡的沒被抓到"
    assert banned_hits('env["SSHPASS"] = pw\n'), "大寫環境變數名沒被抓到"
    assert banned_hits("c.set_missing_host_key_policy(paramiko.AutoAddPolicy())\n")
    assert banned_hits("from paramiko import WarningPolicy\n")
    assert not banned_hits("x = 1  # sshpass 已移除，改用 paramiko\n"), "註解應該放過"


def test_requirements有列paramiko():
    req = (BACKEND / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "paramiko" in req, "改用 paramiko 之後 requirements.txt 要列它，不然部署後納管直接失敗"


if __name__ == "__main__":
    sys.exit(0)
