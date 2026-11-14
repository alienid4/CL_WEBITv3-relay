"""sudo 白名單：**驗納管腳本真的產得出來**，不是驗常數寫對。

2026-09-22 退回事件：兩條規則只加進 account_collector.SUDO_RULES
（那是「給管理員自己貼」的文件版，唯一消費者是 api.py 的端點），
納管實際寫 /etc/sudoers.d/ 的三份一份都沒改到。

後果：使用者重新納管也開不通，而且畫面還寫著「重新納管一次即可開通」——
錯誤指示。更糟的是它不報錯，走「沒授權」分支顯示橘色警示，看起來一切正常。

所以這裡不斷言常數，斷言**產生出來的腳本文字**。同一個病今天出現兩次
（KeyError: 'level' 是「改介面沒看呼叫端」，這次是「改文件版沒看佈署版」），
守門要擋的是「同一份規則散在多處、改了其中一處」這個結構。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_collector as ac  # noqa: E402
import onboard_engine as oe  # noqa: E402

#: 值班健檢需要、且必須出現在**每一個**佈署路徑的規則。
HEALTH_RULES = (
    "/usr/bin/tail -n 200 /var/log/secure",
    # Debian／Ubuntu 的登入紀錄在 auth.log。少了這條，那些機器的 SSH 爆破偵測
    # 永遠讀不到東西，而且畫面看起來會像是「權限沒開通」——人會一直去重新納管。
    "/usr/bin/tail -n 200 /var/log/auth.log",
    # 逗號要跳脫：sudoers 的 , 是命令分隔符，不跳脫 visudo 會擋下整份
    "/usr/bin/dmesg --level=err\\,crit",
    # 監聽 port 的行程名（ss -tlnp 的 -p 需 root，2026-09-22 使用者核准）
    "/usr/sbin/ss -tlnp",
)

PUBKEY = "ssh-rsa AAAATESTKEY"
COLLECTOR_IP = "192.0.2.1"


def _shell_script() -> str:
    return oe.build_linux_script(PUBKEY, COLLECTOR_IP)


def _playbook() -> str:
    r"""取 playbook 裡**YAML 解析後**的 sudoers 內容，不是原始文字。

    2026-09-22：原始 YAML 寫的是 err\,crit（兩個反斜線），YAML 解析後才是
    err\,crit，而那才是真正寫進 /etc/sudoers.d 的東西。比對原始文字會誤判。
    """
    import pytest as _pt
    yaml = _pt.importorskip('yaml', reason='沒有 pyyaml，Ansible 路徑這關略過')
    pb = oe.build_linux_playbook(PUBKEY, COLLECTOR_IP)
    for play in yaml.safe_load(pb):
        for task in play.get('tasks', []):
            copy = task.get('ansible.builtin.copy') or task.get('copy')
            if copy and 'sudoers.d' in str(copy.get('dest', '')):
                return str(copy['content'])
    raise AssertionError('playbook 裡找不到寫 sudoers.d 的 copy 任務')


def test_納管shell腳本要真的佈那兩條規則():
    s = _shell_script()
    for rule in HEALTH_RULES:
        assert rule in s, (
            f"納管 shell 腳本裡沒有 {rule!r}——"
            "使用者重新納管也開不通，但畫面會說『重新納管一次即可開通』")


def test_納管Ansible劇本要真的佈那兩條規則():
    s = _playbook()
    for rule in HEALTH_RULES:
        assert rule in s, f"Ansible 路徑沒有 {rule!r}——走這條路納管的機器開不通"


def test_sudo_rules_for也要有():
    s = ac.sudo_rules_for({"id": "rocky", "version": "9", "family": "rhel"})
    for rule in HEALTH_RULES:
        assert rule in s, f"sudo_rules_for 沒有 {rule!r}"


def test_文件版與佈署版不可以分岔():
    """SUDO_RULES（給管理員貼的）跟納管腳本必須講同一套。

    這正是這次 bug 的形狀：同一份規則散在四個地方，改了其中一個。
    以「健檢那兩條」為樣本鎖住——四個來源缺任何一個就紅。
    """
    sources = {
        "SUDO_RULES（文件版）": ac.SUDO_RULES,
        "sudo_rules_for（產生器）": ac.sudo_rules_for(
            {"id": "rocky", "version": "9", "family": "rhel"}),
        "納管 shell 腳本": _shell_script(),
        "納管 Ansible 劇本": _playbook(),
    }
    for rule in HEALTH_RULES:
        missing = [name for name, text in sources.items() if rule not in text]
        assert not missing, f"{rule!r} 只寫在部分來源，這幾個沒有：{missing}"


def test_佈署出去的規則不可以太寬():
    """金融業主機：不准 ALL、不准裸 cat/tail、不准給 shell。"""
    for name, text in (("shell", _shell_script()), ("ansible", _playbook()),
                       ("sudo_rules_for", ac.sudo_rules_for(
                           {"id": "rocky", "version": "9", "family": "rhel"}))):
        assert "NOPASSWD: ALL" not in text, name
        for bad in ("NOPASSWD: /bin/sh", "NOPASSWD: /bin/bash",
                    "NOPASSWD: /usr/bin/cat\n", "NOPASSWD: /usr/bin/tail\n"):
            assert bad not in text, f"{name} 有太寬的規則：{bad!r}"
