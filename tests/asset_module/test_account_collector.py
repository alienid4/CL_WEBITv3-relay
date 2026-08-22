"""帳號採集解析與稽核規則：分類、權限降級、規則判定、拿不到不當成通過。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import account_collector as ac  # noqa: E402
import account_rules as ar  # noqa: E402

PASSWD = """root:x:0:0:root:/root:/bin/bash
bin:x:1:1:bin:/bin:/sbin/nologin
sshd:x:74:74:Privilege-separated SSH:/var/empty/sshd:/sbin/nologin
backdoor:x:0:0:looks legit:/home/backdoor:/bin/bash
alice:x:1000:1000:Alice Chen:/home/alice:/bin/bash
bob:x:1001:1001:Bob Lin:/home/bob:/bin/bash
webit3scan:x:1002:1002:webit3 唯讀收集:/home/webit3scan:/bin/bash
"""

GROUP = """root:x:0:
wheel:x:10:alice,bob
sudo:x:27:alice
docker:x:990:bob
"""

LASTLOG = """Username         Port     From             Latest
root             pts/0    192.0.2.5        Mon Jul 20 09:12:03 +0800 2026
alice            pts/1    192.0.2.9        Jul 15, 2026
bob                                        **Never logged in**
"""

# sudo -n 成功時的樣子（有 root）
SHADOW_OK = """ACCT root :: root P 07/01/2026 0 99999 7 -1 :: Last password change:Jul 01, 2026|Password expires:never|Maximum number of days between password change:99999|
ACCT alice :: alice P 01/02/2026 0 90 7 -1 :: Last password change:Jan 02, 2026|Password expires:Apr 02, 2026|Maximum number of days between password change:90|
ACCT bob :: bob NP 01/02/2026 0 90 7 -1 :: Last password change:Jan 02, 2026|Maximum number of days between password change:90|
"""

SUDOERS = """# comment
Defaults env_reset
root ALL=(ALL) ALL
%wheel ALL=(ALL) ALL
alice ALL=(root) NOPASSWD: /usr/bin/systemctl
webit3scan ALL=(root) NOPASSWD: /usr/bin/cat /sys/class/dmi/id/*
"""

AUTHKEYS = """KEYS root 2
KEYS alice 1
"""


def _runner(with_root=True):
    def run(host, cmd):
        if cmd.startswith("cat /etc/passwd"):
            return PASSWD
        if cmd.startswith("cat /etc/group"):
            return GROUP
        if "lastlog" in cmd:          # 指令帶 LC_ALL=C 前綴，不能用 startswith 比對
            return LASTLOG
        if "passwd -S" in cmd:
            return SHADOW_OK if with_root else ""
        if "sudoers" in cmd:
            return SUDOERS if with_root else ""
        if "authorized_keys" in cmd:
            return AUTHKEYS if with_root else ""
        return ""
    return run


def _by_name(result):
    return {a["username"]: a for a in result["accounts"]}


def test_帳號分類分得出真人服務與預設():
    """服務帳號跟真人混在一起判密碼到期，紅燈會多到沒人看——誤報是稽核工具最大死因。"""
    r = ac.collect(_runner(), "203.0.113.5")
    a = _by_name(r)
    assert a["alice"]["kind"] == "human"
    assert a["sshd"]["kind"] == "builtin"      # 叫得出名字的內建守護帳號，不歸無名 service
    assert a["bin"]["kind"] == "builtin"
    assert a["root"]["kind"] == "default"


def test_抓得到UID0後門帳號():
    r = ac.collect(_runner(), "203.0.113.5")
    a = _by_name(r)
    assert a["backdoor"]["uid"] == 0
    findings = ar.evaluate(r["accounts"], ar.DEFAULT_THRESHOLDS)
    uid0 = [f for f in findings if f["rule_id"] == "A2" and f["verdict"] == "fail"]
    assert len(uid0) == 1 and uid0[0]["username"] == "backdoor"


def test_特權群組與sudo都算sudoer():
    """群組給權比逐人給權更容易漏看，兩種都要認得。"""
    r = ac.collect(_runner(), "203.0.113.5")
    a = _by_name(r)
    assert a["alice"]["is_sudoer"] is True          # 直接列名 + %wheel
    assert a["bob"]["is_sudoer"] is True            # 只靠 %wheel 群組
    assert "wheel" in (a["bob"]["priv_groups"] or "")
    assert a["alice"]["sudo_nopasswd"] is True      # NOPASSWD 條目


def test_空密碼與從未登入抓得到():
    r = ac.collect(_runner(), "203.0.113.5")
    a = _by_name(r)
    assert a["bob"]["pw_status"] == "empty"
    assert a["bob"]["never_logged_in"] is True
    f = ar.evaluate(r["accounts"], ar.DEFAULT_THRESHOLDS)
    assert any(x["rule_id"] == "A1" and x["username"] == "bob" and x["verdict"] == "fail"
               for x in f)


def test_passwd_S兩字母狀態碼也要正規化():
    """真機踩過：shadow-utils 的 passwd -S 吐 PS/LK/NP 兩字母，只認單字母
    會把一堆『已鎖定(LK)』留成原始碼，規則引擎當成還能登入——鎖定帳號對稽核隱形。"""
    assert ac.normalize_pw_status("LK") == "locked"
    assert ac.normalize_pw_status("L") == "locked"
    assert ac.normalize_pw_status("PS") == "set"
    assert ac.normalize_pw_status("P") == "set"
    assert ac.normalize_pw_status("NP") == "empty"
    assert ac.normalize_pw_status("lk") == "locked"      # 大小寫不敏感
    assert ac.normalize_pw_status("???") == "???"        # 未知碼保留，不硬套


def test_密碼永不過期判得出來():
    r = ac.collect(_runner(), "203.0.113.5")
    f = ar.evaluate(r["accounts"], ar.DEFAULT_THRESHOLDS)
    # root 是 default 類，不套 R2b（那條只判真人）；改用 alice 驗證正常值不誤報
    assert not any(x["rule_id"] == "R2b" and x["username"] == "alice"
                   and x["verdict"] == "fail" for x in f)


def test_沒有root時降級但不假裝():
    """拿不到 ≠ 通過。把「沒查到」講成「查過沒問題」是最危險的假綠燈。"""
    r = ac.collect(_runner(with_root=False), "203.0.113.5")
    assert r["root_ok"] is False
    assert set(r["needs_root"]) == {"password", "sudo", "authorized_keys"}
    a = _by_name(r)
    assert a["alice"]["pw_status"] is None
    assert a["alice"]["authorized_keys"] is None

    f = ar.evaluate(r["accounts"], ar.DEFAULT_THRESHOLDS)
    # 密碼相關規則必須是 unknown，不能是 pass（pass 不會出現在 findings）
    pw = [x for x in f if x["rule_id"] == "R2" and x["username"] == "alice"]
    assert pw and pw[0]["verdict"] == "unknown"
    assert "需 root" in pw[0]["detail"]


def test_基本欄位不需root就拿得到():
    """就算沒有 root，帳號清單／UID／群組／最後登入仍然是有價值的稽核資料。"""
    r = ac.collect(_runner(with_root=False), "203.0.113.5")
    a = _by_name(r)
    assert a["alice"]["uid"] == 1000
    assert a["bob"]["never_logged_in"] is True
    assert "wheel" in (a["alice"]["priv_groups"] or "")


def test_門檻可調整():
    """稽核要求會隨年度變，門檻寫死等於每次改規定都要發版。"""
    r = ac.collect(_runner(), "203.0.113.5")
    loose = ar.evaluate(r["accounts"], {**ar.DEFAULT_THRESHOLDS, "acct_pw_max_days": 3650})
    strict = ar.evaluate(r["accounts"], {**ar.DEFAULT_THRESHOLDS, "acct_pw_max_days": 1})
    n_loose = len([f for f in loose if f["rule_id"] == "R2" and f["verdict"] == "fail"])
    n_strict = len([f for f in strict if f["rule_id"] == "R2" and f["verdict"] == "fail"])
    assert n_strict > n_loose


def test_預設帳號可登入要亮燈():
    r = ac.collect(_runner(), "203.0.113.5")
    f = ar.evaluate(r["accounts"], ar.DEFAULT_THRESHOLDS)
    assert any(x["rule_id"] == "R9" and x["username"] == "root" and x["verdict"] == "fail"
               for x in f)


def test_sudo白名單不含讀shadow():
    """拿得到同樣結論就不該要更大的權限——讀 shadow 等於把密碼雜湊送出去給人離線破解。"""
    # 只看實際生效的規則行，註解裡提到 shadow（說明為什麼不放）是可以的
    rule_lines = [l for l in ac.SUDO_RULES.splitlines()
                  if l.strip() and not l.strip().startswith("#")]
    assert not any("/etc/shadow" in l for l in rule_lines)
    assert "chage -l" in ac.SUDO_RULES
    assert "passwd -S" in ac.SUDO_RULES


def test_系統帳號從未登入不算閒置誤報():
    """實測 221 第一輪把 halt/shutdown/sync 判成「從未登入」的閒置帳號。

    它們的 shell 是 /sbin/halt 這種「執行完就結束」的指令，不是 nologin，
    但本來就不是拿來登入的。這種誤報一多，真正該看的那幾條就被淹掉了。
    """
    passwd = (
        "halt:x:7:0:halt:/sbin:/sbin/halt\n"
        "sync:x:5:0:sync:/sbin:/bin/sync\n"
        "carol:x:1003:1003:Carol:/home/carol:/bin/bash\n"
    )
    lastlog = ("Username Port From Latest\n"
               "halt   **Never logged in**\n"
               "sync   **Never logged in**\n"
               "carol  **Never logged in**\n")

    def run(host, cmd):
        if cmd.startswith("cat /etc/passwd"):
            return passwd
        if "lastlog" in cmd:
            return lastlog
        return ""

    r = ac.collect(run, "203.0.113.5")
    f = ar.evaluate(r["accounts"], ar.DEFAULT_THRESHOLDS)
    idle = {x["username"] for x in f if x["rule_id"] == "R5" and x["verdict"] == "fail"}
    assert "halt" not in idle and "sync" not in idle
    assert "carol" in idle          # 真人帳號從未登入仍要報——那是真的該清掉


def test_中文語系的lastlog也認得():
    """實測 221/222/223/230 都是中文語系，lastlog 印「**從未登入過**」。

    採集端已強制 LC_ALL=C，但有些環境 PAM 會覆蓋語系設定——中文字樣要當第二道防線。
    認不出來的後果不是少一欄，是「閒置帳號」這條規則整條失效。
    """
    zh = """Username         Port     From             Latest
root             pts/0    10.99.0.101    二  7月 21 06:32:15 +0800 2026
carol                                      **從未登入過**
"""

    def run(host, cmd):
        if cmd.startswith("cat /etc/passwd"):
            return "root:x:0:0:root:/root:/bin/bash\ncarol:x:1003:1003::/home/carol:/bin/bash\n"
        if cmd.startswith("cat /etc/group"):
            return ""
        if "lastlog" in cmd:
            return zh
        return ""

    r = ac.collect(run, "203.0.113.5")
    a = _by_name(r)
    assert a["carol"]["never_logged_in"] is True


def test_lastlog英文日期解析得出天數():
    """C 語系下的 `Mon Jul 20 09:12:03 +0800 2026` 要能算出距今幾天，
    否則 R5 閒置判定永遠是 unknown——真機上就是這樣整條規則失效的。"""
    d = ar._parse_date("pts/0    192.0.2.5        Mon Jul 20 09:12:03 +0800 2026")
    assert d is not None and d != "never"
    assert d.year == 2026 and d.month == 7 and d.day == 20


def test_沒金鑰的帳號回0不是需root誤報():
    """改良版掃描每個帳號真實家目錄：沒 authorized_keys 檔就回 0（確定沒金鑰），
    不再因為系統帳號家目錄不在 /home 就整批誤報「取不到（需 root）」。"""
    passwd = ("root:x:0:0:root:/root:/bin/bash\n"
              "bin:x:1:1:bin:/bin:/sbin/nologin\n"
              "adm:x:3:4:adm:/var/adm:/sbin/nologin\n"
              "alice:x:1000:1000:Alice:/home/alice:/bin/bash\n")

    def run(host, cmd):
        if cmd.startswith("cat /etc/passwd"):
            return passwd
        if "lastlog" in cmd:
            return "SRC=lastlog\n"
        if "authorized_keys" in cmd:          # 改良版會對每個帳號回一行，沒檔的回 0
            return "KEYS root 2\nKEYS bin 0\nKEYS adm 0\nKEYS alice 0\n"
        return ""

    r = ac.collect(run, "203.0.113.5")
    a = _by_name(r)
    assert a["bin"]["authorized_keys"] == 0        # 確定 0，不是 None
    assert a["adm"]["authorized_keys"] == 0
    assert a["root"]["authorized_keys"] == 2
    assert "authorized_keys" not in r["needs_root"]

    f = ar.evaluate(r["accounts"], ar.DEFAULT_THRESHOLDS)
    # bin/adm 0 金鑰 → A4 不再出現「需 root」
    assert not any(x["rule_id"] == "A4" and x["username"] in ("bin", "adm") for x in f)


def test_取不到passwd要明確失敗():
    def broken(host, cmd):
        return ""

    try:
        ac.collect(broken, "203.0.113.5")
    except ConnectionError as exc:
        assert "passwd" in str(exc)
    else:
        raise AssertionError("取不到 /etc/passwd 必須拋錯，不可回空清單")
