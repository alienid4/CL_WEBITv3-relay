"""帳號盤點唯讀輔助程式：取代帶萬用字元的 sudo 白名單（2026-09-11）。

守的線（任何一條鬆掉都是資安缺陷，不是外觀問題）：
1. **sudo 規則不能有帶 `*` 的帳號指令**——sudoers 的 `*` 會吃空白，
   `cat /home/*/.ssh/authorized_keys` 能被拿去順便讀 /etc/shadow。
2. **輔助程式只准不帶參數執行**（sudoers 的 `""`），程式本身也拒收參數。
3. **不吐密碼雜湊、不吐金鑰內容**：只給狀態碼、日期、金鑰數量。
4. **動任何東西之前先備份，並留 restore.sh**（使用者：「移動前就要備份，要有還原的」）。
5. **sudoers 先寫暫存檔、驗證過才換上去**，驗證失敗自動還原。
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_collector as ac  # noqa: E402
import onboard_engine as eng  # noqa: E402

PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIStubKeyForTestsOnly webit3 collector"


def _rule_lines(text):
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]


# ---------------------------------------------------------------------------
# sudo 規則
# ---------------------------------------------------------------------------

def test_sudo規則沒有帶萬用字元的帳號指令():
    for text in (ac.SUDO_RULES, ac.sudo_rules_for(ac.parse_os("OSID=rocky\nOSVER=9.7\n"))):
        for l in _rule_lines(text):
            if "/sys/class/dmi/id/" in l:
                continue          # 機型序號那條的 * 只落在 sysfs 目錄底下
            assert "*" not in l, f"帳號盤點規則不可以有 *（會吃空白）：{l}"
            for bad in ("chage", "passwd", "/home/", "authorized_keys", "/etc/shadow"):
                assert bad not in l, f"規則出現不該直接給的指令：{l}"


def test_輔助程式規則禁止帶參數():
    helper_lines = [l for l in _rule_lines(ac.SUDO_RULES) if ac.ACCOUNT_HELPER_PATH in l]
    assert helper_lines and all(l.endswith('""') for l in helper_lines)


# ---------------------------------------------------------------------------
# 輔助程式本身
# ---------------------------------------------------------------------------

def test_輔助程式拒收參數且不讀shadow也不吐金鑰內容():
    s = ac.ACCOUNT_HELPER_SCRIPT
    assert 'if [ "$#" -ne 0 ]' in s
    assert "/etc/shadow" not in s.replace("# 不輸出密碼雜湊", "")
    keys_part = s.split("=== KEYS", 1)[1]
    assert 'cat "$f"' not in keys_part, "KEYS 段只能數數量，不能把金鑰內容印出來"
    assert "grep -c" in keys_part
    assert s.rstrip().endswith('echo "=== END"'), "要有結尾標記，沒跑完才分得出來"


def test_輔助程式在本機bash可以跑且格式對得上解析器():
    """在有 bash 的環境實跑一次（Linux CI／221 部署前測試）：
    輸出要能被三支既有解析器吃下，而且帶參數要被拒絕。"""
    bash = shutil.which("bash")
    if not bash or sys.platform == "win32":
        pytest.skip("需要 Linux bash 實跑；Windows 開發機略過")
    r = subprocess.run([bash, "-c", ac.ACCOUNT_HELPER_SCRIPT, "account-facts"],
                       capture_output=True, text=True, timeout=60)
    sec = ac.split_helper_output(r.stdout)
    assert sec is not None and {"SHADOW", "SUDOERS", "KEYS"} <= set(sec)
    assert ac.parse_authkeys(sec["KEYS"]), "KEYS 段要每個帳號一列"
    bad = subprocess.run([bash, "-c", ac.ACCOUNT_HELPER_SCRIPT, "account-facts", "/etc/shadow"],
                         capture_output=True, text=True, timeout=30)
    assert bad.returncode == 2 and "=== SHADOW" not in bad.stdout


def test_沒有結尾標記就當作沒跑完():
    half = "=== SHADOW\nACCT root :: root PS :: \n=== SUDOERS\nroot ALL=(ALL) ALL\n=== KEYS\n"
    assert ac.split_helper_output(half) is None
    assert ac.split_helper_output("") is None


def test_收集時先用輔助程式_一次拿齊三樣():
    passwd = "root:x:0:0:root:/root:/bin/bash\nalice:x:1000:1000::/home/alice:/bin/bash\n"
    helper_out = ("=== SHADOW\n"
                  "ACCT alice :: alice PS 2026-01-01 0 90 7 -1 :: Last password change : Jan 01, 2026|\n"
                  "=== SUDOERS\nalice ALL=(ALL) NOPASSWD: ALL\n"
                  "=== KEYS\nKEYS root 0\nKEYS alice 2\n=== END\n")

    def runner(host, cmd):
        if cmd == ac.LINUX_CMDS["passwd"]:
            return passwd
        if "account-facts" in cmd:
            return helper_out
        return ""

    r = ac.collect(runner, "192.0.2.5")
    assert r["needs_root"] == [], r["needs_root"]
    alice = {a["username"]: a for a in r["accounts"]}["alice"]
    assert alice["authorized_keys"] == 2
    assert alice["sudo_nopasswd"] is True
    assert alice["pw_status"] == "set"


# ---------------------------------------------------------------------------
# 納管／撤銷腳本
# ---------------------------------------------------------------------------

def _linux_script():
    return eng.build_linux_script(PUB, "192.0.2.1")


def test_納管腳本動手前先備份並產生還原腳本():
    s = _linux_script()
    i_backup = s.index('backup_one "/etc/sudoers.d/$ACCOUNT"')
    i_useradd = s.index("useradd")
    i_helper = s.index('cat > "$HELPER.new"')
    assert i_backup < i_useradd < i_helper, "備份必須排在任何變更之前"
    assert 'R="$BK/restore.sh"' in s and "sudo bash $R" in s
    assert "/var/backups/webit3/" in s


def test_納管腳本_sudoers先驗證才換上去_失敗自動還原():
    s = _linux_script()
    assert 'visudo -cf "$SUDOERS.new"' in s
    assert 'mv -f "$SUDOERS.new" "$SUDOERS"' in s
    assert 'bash "$R"' in s, "驗證失敗要自動還原"
    assert f'NOPASSWD: $HELPER \\"\\"' in s, "規則要禁止帶參數"
    assert "<<'WEBIT3_HELPER_EOF'" in s, "heredoc 分隔字要加引號，內容才不會被展開"
    assert ac.ACCOUNT_HELPER_SCRIPT in s, "佈上去的輔助程式要跟收集端認得的是同一份"


def test_納管腳本語法正確():
    """f-string 大括號、跳脫字元最容易在這裡寫壞，而壞掉的腳本只有在真機跑才會發現。"""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("沒有 bash 可做語法檢查")
    for script in (_linux_script(), eng.build_linux_revoke_script(PUB)):
        r = subprocess.run([bash, "-n"], input=script, capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
        assert r.returncode == 0, r.stderr


def test_納管腳本沒有禁止樣式():
    s = _linux_script()
    for banned in ("base64", "/tmp", "mktemp", "eval", "StrictHostKeyChecking=no"):
        assert banned not in s, f"納管腳本出現禁止樣式：{banned}"


def test_playbook佈同一份輔助程式且覆寫前留備份():
    y = eng.build_linux_playbook(PUB, "192.0.2.1")
    assert ac.ACCOUNT_HELPER_PATH in y
    assert y.count("backup: true") >= 2
    assert 'validate: "visudo -cf %s"' in y
    assert 'echo "=== END"' in y, "playbook 裡的輔助程式要是完整的"


def test_playbook的公鑰選項不可以有空白():
    """2026-09-11 在 221 跑 ansible --check --diff 抓到：key_options 用 `>-` 折兩行，
    逗號後面多一個空白 → sshd 讀到空白就停 → 已經有這把公鑰的主機會被改寫成壞的一行，
    收集帳號登不進去。整批跑一千台就是一千台失聯。"""
    y = eng.build_linux_playbook(PUB, "192.0.2.1")
    line = next(l for l in y.splitlines() if l.strip().startswith("key_options:"))
    assert ">" not in line.split(":", 1)[1][:3], "key_options 不可以用折疊區塊"
    assert ", " not in line, f"公鑰選項裡有空白：{line}"
    # 跟腳本那條 authorized_keys 的選項完全同一串（兩條路佈出來要一樣）
    assert 'from="{{ webit3_collector_ip }}",no-agent-forwarding,no-port-forwarding,no-X11-forwarding' in line
    try:
        import yaml
    except ImportError:
        return
    task = next(t for t in yaml.safe_load(y)[0]["tasks"] if "authorized_key" in str(t))
    # 只擋「逗號後面的空白」：{{ webit3_collector_ip }} 這個 Jinja 變數本身帶空白是正常的
    assert ", " not in task["ansible.posix.authorized_key"]["key_options"]


def test_playbook用管理者設定的帳號備註():
    """原本寫死英文，對已存在的帳號會把管理者填的備註蓋掉（221 --check 顯示 changed）。"""
    y = eng.build_linux_playbook(PUB, "192.0.2.1", comment="webit3 唯讀收集")
    assert 'comment: "webit3 唯讀收集"' in y
    assert "webit3 readonly collector" not in y.split("tasks:", 1)[1].split("authorized_key")[0]


def test_取消納管也收掉輔助程式並留備份():
    s = eng.build_linux_revoke_script(PUB)
    assert f'rm -f "{ac.ACCOUNT_HELPER_PATH}"' in s
    assert "/var/backups/webit3/account-facts.webit3-revoke-" in s
    # 順序：金鑰 → sudoers → 輔助程式 → 帳號（先斷存取權）
    assert s.index("authorized_keys") < s.index("/etc/sudoers.d/$ACCOUNT") \
        < s.index(ac.ACCOUNT_HELPER_PATH) < s.index("userdel")
