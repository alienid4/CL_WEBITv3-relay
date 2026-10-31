"""把三個佈署路徑的 sudoers 內容**真的送進 visudo 驗**。

2026-09-22 BOSS 在 221 真機納管時被擋下：

    /etc/sudoers.d/webit3scan.new:10:60: 語法錯誤
    webit3scan ALL=(root) NOPASSWD: /usr/bin/dmesg --level=err,crit

sudoers 裡 `,` 是**命令清單的分隔符號**，出現在參數裡要跳脫成 `\\,`，
否則 visudo 讀成「規則到 err 結束，另有一條叫 crit 的命令」。

**為什麼非得真的跑 visudo：**
當時 5 條字串比對測試全綠、閘門 2113 passed、BOSS 比對三處產出文字也全對——
**然後真機第一次跑就掛了**。因為那些檢查問的是「四個來源彼此一不一致」，
**沒有任何一關問過「visudo 收不收」**。一致地錯還是錯。

失敗形狀還特別惡劣：納管照樣往下走、sudoers 靜默還原成舊的，
畫面顯示「這兩項沒有查到」——**跟「還沒重新納管」長得一模一樣**，
使用者會照指示一再重新納管，一再看到同一句話。

沒有 visudo 的環境（例如 Windows 上的閘門）會 skip，**但會標明 skip**，
不可以讓人以為驗過了。真正的把關在 221／CI 上。
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_collector as ac  # noqa: E402
import onboard_engine as oe  # noqa: E402

ACCOUNT = "webit3scan"
VISUDO = shutil.which("visudo") or (
    "/usr/sbin/visudo" if Path("/usr/sbin/visudo").exists() else None)


def _from_shell(script: str) -> str:
    """把 `{ echo …; } > "$SUDOERS.new"` 那段還原成寫進檔案的內容。

    bash 雙引號裡：\\" → "、\\\\ → \\，其餘反斜線原樣留著（sudoers 要的就是那個）。
    """
    out, inside = [], False
    for raw in script.splitlines():
        t = raw.strip()
        if 'SUDOERS="/etc/sudoers.d/' in t:
            inside = True
            continue
        if inside and t.startswith("}") and "SUDOERS" in t:
            break
        if inside and t.startswith('echo "'):
            body = t[len('echo "'):]
            body = body[: body.rindex('"')]
            body = body.replace('\\"', '"').replace("\\\\", "\\")
            out.append(body.replace("$ACCOUNT", ACCOUNT)
                           .replace("$HELPER", ac.ACCOUNT_HELPER_PATH))
    return "\n".join(out) + "\n"


def _from_playbook(pb: str) -> str:
    yaml = pytest.importorskip("yaml", reason="沒有 pyyaml，Ansible 路徑這關略過")
    for play in yaml.safe_load(pb):
        for task in play.get("tasks", []):
            copy = task.get("ansible.builtin.copy") or task.get("copy")
            if copy and "sudoers.d" in str(copy.get("dest", "")):
                return str(copy["content"]).replace("{{ webit3_account }}", ACCOUNT)
    raise AssertionError("playbook 裡找不到寫 sudoers.d 的 copy 任務")


def _check(content: str, label: str) -> None:
    if not VISUDO:
        pytest.skip(f"這台沒有 visudo，{label} 這關略過（真正的把關在 221／CI）")
    with tempfile.NamedTemporaryFile("w", suffix=".sudoers", delete=False,
                                     encoding="utf-8", newline="\n") as f:
        f.write(content)
        path = f.name
    try:
        r = subprocess.run([VISUDO, "-cf", path], capture_output=True, text=True)
        assert r.returncode == 0, (
            f"{label} 的 sudoers 內容 visudo 不收——納管會靜默還原成舊的，"
            f"而畫面看起來跟『還沒納管』一樣：\n{r.stdout}{r.stderr}\n--- 內容 ---\n{content}")
    finally:
        Path(path).unlink(missing_ok=True)


def test_納管shell腳本的sudoers要通過visudo():
    _check(_from_shell(oe.build_linux_script("ssh-rsa KEY", "192.0.2.1")), "shell 路徑")


def test_納管Ansible劇本的sudoers要通過visudo():
    _check(_from_playbook(oe.build_linux_playbook("ssh-rsa KEY", "192.0.2.1")),
           "Ansible 路徑")


def test_sudo_rules_for的內容要通過visudo():
    _check(ac.sudo_rules_for({"id": "rocky", "version": "9", "family": "rhel"}),
           "sudo_rules_for")


def test_帶逗號的參數一定要跳脫():
    """不依賴 visudo 也要擋住的最低限度：參數裡的逗號不可以裸著。

    這條在 Windows 閘門上也會跑，所以是 visudo 不在時的第二道防線。
    """
    sources = {
        "shell": _from_shell(oe.build_linux_script("ssh-rsa KEY", "192.0.2.1")),
        "sudo_rules_for": ac.sudo_rules_for(
            {"id": "rocky", "version": "9", "family": "rhel"}),
        "SUDO_RULES": ac.SUDO_RULES,
    }
    for label, text in sources.items():
        for line in text.splitlines():
            if not line.startswith(ACCOUNT) or "NOPASSWD:" not in line:
                continue
            cmds = line.split("NOPASSWD:", 1)[1]
            # 把合法的「多命令清單」分隔（`, ` 後面接絕對路徑）先拿掉，
            # 剩下的逗號就是夾在參數裡的，必須是 `\,`
            import re
            for m in re.finditer(r"(?<!\\),(?!\s*/)", cmds):
                raise AssertionError(
                    f"{label} 這行的參數裡有沒跳脫的逗號，visudo 會擋：\n  {line.strip()}\n"
                    f"  位置 {m.start()}：sudoers 的 , 是命令分隔符，要寫成 \\,")


# ── sudoers 驗證失敗要浮上來，不能沉在 stderr ──────────────────────────
# BOSS 2026-09-22：畫面要分得出三種——還沒納管／納管過但白名單沒佈成功／佈好了但沒授權。
# 第 2 種最惡劣：腳本 exit 1、sudoers 已還原成舊的，但健檢畫面顯示「這兩項沒有查到」，
# 跟「還沒重新納管」一模一樣。使用者會照指示一再重新納管，一再看到同一句話。

def test_visudo失敗時腳本要印可解析的標記():
    s = oe.build_linux_script("ssh-rsa KEY", "192.0.2.1")
    assert oe.SUDOERS_INVALID_MARK in s, (
        "visudo 失敗分支沒有印標記，上層無從得知白名單沒佈上去")
    # 標記要在 stdout（不是只有 stderr）——上層解析的是 stdout
    i = s.index(oe.SUDOERS_INVALID_MARK)
    line_start = s.rfind("\n", 0, i) + 1
    line = s[line_start:s.index("\n", i)]
    assert ">&2" not in line, f"標記寫到 stderr 了，上層看不到：{line.strip()}"


def test_三種狀態的訊息要分得開():
    invalid = oe.success_message(f"[!] {oe.SUDOERS_INVALID_MARK} 擋下了")
    no_sudo = oe.success_message(f"[!] {oe.NO_SUDO_MARK} 這台沒有 sudo")
    normal = oe.success_message("完成。")
    assert invalid != no_sudo != normal and invalid != normal
    # 白名單沒佈成功要講明「重新納管幾次都一樣」，否則使用者只會一直重試
    assert "沒有佈上去" in invalid or "沒佈上去" in invalid
    assert "重新納管幾次都會是同樣結果" in invalid, invalid
    # 而「沒有 sudo」是那台環境如此，不是我們寫錯——兩者的下一步完全不同
    assert "沒有 sudo" in no_sudo
