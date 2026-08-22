"""發行版差異：RHEL 家族 vs Debian、有無 lastlog、UID_MIN、sudoers 路徑。

真實背景（2026-07-21 實測）：家中 4 台是 Rocky 9.7 ×3 + Debian 13 ×1，
而 **Debian 13 已經沒有 lastlog 指令**，退回 last 之後「從未登入」判定完全失效。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import account_collector as ac  # noqa: E402
import account_rules as ar  # noqa: E402

PASSWD = """root:x:0:0:root:/root:/bin/bash
alice:x:1000:1000:Alice:/home/alice:/bin/bash
ghost:x:1001:1001:Ghost:/home/ghost:/bin/bash
"""

ROCKY_OS = """OSID=rocky
OSVER=9.7
OSLIKE=rhel centos fedora
UIDMIN=1000
BIN=lastlog:/usr/bin/lastlog
BIN=lastlog2:
BIN=chage:/usr/bin/chage
BIN=passwd:/usr/bin/passwd
BIN=sudo:/usr/bin/sudo
"""

DEBIAN13_OS = """OSID=debian
OSVER=13
OSLIKE=
UIDMIN=1000
BIN=lastlog:
BIN=lastlog2:
BIN=chage:/usr/bin/chage
BIN=passwd:/usr/bin/passwd
BIN=sudo:/usr/bin/sudo
"""

RHEL6_OS = """OSID=rhel
OSVER=6.10
OSLIKE=
UIDMIN=500
BIN=lastlog:/usr/bin/lastlog
BIN=chage:/usr/bin/chage
BIN=passwd:/usr/bin/passwd
BIN=sudo:/usr/bin/sudo
"""

LASTLOG_OUT = """SRC=lastlog
Username         Port     From             Latest
root             pts/0    192.0.2.5        Mon Jul 20 09:12:03 +0800 2026
alice            pts/1    192.0.2.9        Mon Jul 20 08:00:00 +0800 2026
ghost                                      **Never logged in**
"""

# Debian 13：只有 last，ghost 從頭到尾不會出現
LAST_OUT = """SRC=last
root     pts/0        192.0.2.5        Mon Jul 20 09:12:03 2026   still logged in
alice    pts/1        192.0.2.9        Mon Jul 20 08:00:00 2026 - Mon Jul 20 09:00:00 2026
wtmp begins Mon Jun 22 00:00:00 2026
"""


def _runner(os_out, login_out, passwd=PASSWD):
    def run(host, cmd):
        if cmd.startswith("cat /etc/passwd"):
            return passwd
        if cmd.startswith("cat /etc/group"):
            return ""
        if "os-release" in cmd:
            return os_out
        if "lastlog" in cmd or "last -w" in cmd:
            return login_out
        return ""
    return run


def test_os家族判定():
    assert ac.os_family({"id": "rocky", "like": "rhel centos fedora"}) == "rhel"
    assert ac.os_family({"id": "centos", "like": ""}) == "rhel"
    assert ac.os_family({"id": "almalinux", "like": "rhel"}) == "rhel"
    assert ac.os_family({"id": "debian", "like": ""}) == "debian"
    assert ac.os_family({"id": "ubuntu", "like": "debian"}) == "debian"
    assert ac.os_family({"id": "sles", "like": "suse"}) == "suse"


def test_有lastlog時從未登入是確定的():
    r = ac.collect(_runner(ROCKY_OS, LASTLOG_OUT), "203.0.113.5")
    a = {x["username"]: x for x in r["accounts"]}
    assert r["login_source"] == "lastlog"
    assert a["ghost"]["never_logged_in"] is True
    f = ar.evaluate(r["accounts"], ar.DEFAULT_THRESHOLDS)
    assert any(x["rule_id"] == "R5" and x["username"] == "ghost" and x["verdict"] == "fail"
               for x in f)


def test_debian13沒有lastlog時不可斷言從未登入():
    """last 只列登入過的人。ghost 不在裡面≠從未登入，只代表 wtmp 保存期內沒紀錄。

    在這裡編造確定性，會把一堆正常帳號誤報成該清掉的殘留帳號。
    """
    r = ac.collect(_runner(DEBIAN13_OS, LAST_OUT), "203.0.113.6")
    assert r["login_source"] == "last"
    a = {x["username"]: x for x in r["accounts"]}
    assert a["ghost"]["never_logged_in"] is False       # 不編造
    assert a["ghost"]["login_known"] is False

    f = ar.evaluate(r["accounts"], ar.DEFAULT_THRESHOLDS)
    ghost = [x for x in f if x["rule_id"] == "R5" and x["username"] == "ghost"]
    assert ghost and ghost[0]["verdict"] == "unknown"   # 誠實回報查不到
    assert "wtmp" in ghost[0]["detail"]


def test_last同帳號多列只取最近一次():
    r = ac.collect(_runner(DEBIAN13_OS, LAST_OUT), "203.0.113.6")
    a = {x["username"]: x for x in r["accounts"]}
    assert "still logged in" in (a["root"]["last_login"] or "")


def test_uid門檻讀login_defs不寫死():
    """RHEL 6 的 UID_MIN 是 500。寫死 1000 會讓一整批系統帳號被誤判成真人。"""
    passwd = ("svc500:x:500:500:legacy service:/var/lib/svc:/bin/bash\n"
              "alice:x:1000:1000:Alice:/home/alice:/bin/bash\n")
    r = ac.collect(_runner(RHEL6_OS, LASTLOG_OUT, passwd), "203.0.113.7")
    a = {x["username"]: x for x in r["accounts"]}
    assert a["svc500"]["kind"] == "human"      # RHEL6 下 500 就是真人區間
    r2 = ac.collect(_runner(ROCKY_OS, LASTLOG_OUT, passwd), "203.0.113.8")
    a2 = {x["username"]: x for x in r2["accounts"]}
    assert a2["svc500"]["kind"] == "service"   # UID_MIN=1000 的系統上 500 是系統帳號


def test_sudo白名單用目標機實際路徑():
    """sudoers 比對字面路徑。寫錯不會報錯，只會安靜地繼續拿不到資料。"""
    info = ac.parse_os(ROCKY_OS)
    rules = ac.sudo_rules_for(info)
    assert "/usr/bin/chage -l *" in rules
    # 不可用 /bin/cat（usrmerge 後 sudoers 字面比對不匹配）。
    # 比對「NOPASSWD: 之後緊接的路徑」，否則 /usr/bin/cat 會含有 /bin/cat 子字串而誤判。
    assert "NOPASSWD: /bin/" not in rules
    assert "/usr/bin/cat /etc/sudoers" in rules
    lines = [l for l in rules.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert not any("/etc/shadow" in l for l in lines)


def test_os資訊有帶回來供畫面顯示():
    r = ac.collect(_runner(ROCKY_OS, LASTLOG_OUT), "203.0.113.5")
    assert r["os"]["id"] == "rocky"
    assert r["os"]["version"] == "9.7"
    assert r["os"]["family"] == "rhel"
    assert r["os"]["uid_min"] == 1000
