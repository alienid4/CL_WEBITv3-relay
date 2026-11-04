"""標準管理帳號（sysinfra/sys004）：認得、算特權、不套閒置、密碼照樣管。

背景（使用者 2026-07-22）：sysinfra(Linux)/sys004(AIX) 是 OS 初始化就佈到全機隊的
控管帳號，通常帶 NOPASSWD:ALL。實測 sysinfra uid 645、不在 wheel——它的權限在
/etc/sudoers.d，沒 root 看不到，所以原本被歸成無名 service，權限集中點藏進雜訊。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import account_collector as ac  # noqa: E402
import account_rules as ar  # noqa: E402

# sysinfra uid 645（<UID_MIN），不在 wheel——重現真機情形
PASSWD = """root:x:0:0:root:/root:/bin/bash
sysinfra:x:645:645:standard mgmt:/home/sysinfra:/bin/bash
alice:x:1000:1000:Alice:/home/alice:/bin/bash
"""
GROUP = "wheel:x:10:\n"          # sysinfra 不在 wheel，權限全在 sudoers.d（讀不到）
OS_OUT = ("OSID=rocky\nOSVER=9.7\nUIDMIN=1000\nBIN=lastlog:/usr/bin/lastlog\n"
          "BIN=chage:/usr/bin/chage\nBIN=passwd:/usr/bin/passwd\nBIN=sudo:/usr/bin/sudo\n")
# sysinfra 從未在這台登入（全機隊佈署但只在需要時用）
LASTLOG = ("SRC=lastlog\nUsername Port From Latest\n"
           "alice pts/0 x Jul 15, 2026\nsysinfra **Never logged in**\n")


def _runner(host, cmd):
    if cmd.startswith("cat /etc/passwd"):
        return PASSWD
    if cmd.startswith("cat /etc/group"):
        return GROUP
    if "os-release" in cmd:
        return OS_OUT
    if "lastlog" in cmd:
        return LASTLOG
    return ""


def _by_name(r):
    return {a["username"]: a for a in r["accounts"]}


def test_sysinfra被認成標準管理帳號而非無名服務():
    """uid 645 <UID_MIN 原本會落成 service，權限集中點就藏進雜訊了。"""
    a = _by_name(ac.collect(_runner, "203.0.113.5"))
    assert a["sysinfra"]["kind"] == "mgmt"
    # 不是真人（不該汙染真人統計），也不是無名 service
    assert a["alice"]["kind"] == "human"


def test_標準管理帳號不套閒置規則():
    """全機隊佈署、只在需要時登入——大部分主機上從未登入是正常的，
    套 R5 會在上百台上狂噴誤報，把真正該清的閒置真人帳號淹掉。"""
    r = ac.collect(_runner, "203.0.113.5")
    f = ar.evaluate(r["accounts"], ar.DEFAULT_THRESHOLDS)
    idle = [x for x in f if x["rule_id"] == "R5" and x["username"] == "sysinfra"
            and x["verdict"] == "fail"]
    assert not idle
    # 對照：真人 alice 若閒置仍要報（此例 alice 有登入，不報，但規則有作用於它）
    assert not any(x["rule_id"] == "R5" and x["username"] == "sysinfra" for x in f
                   if x["verdict"] == "fail")


def test_標準管理帳號密碼效期照樣要管():
    """它是特權帳號（NOPASSWD:ALL），密碼不輪替是更嚴重的缺失——R2/R2b 必須適用。"""
    # 給 sysinfra 一個永不過期的密碼設定，驗證 R2b 會點名它
    passwd = PASSWD

    def runner_pwnever(host, cmd):
        if "passwd -S" in cmd or "shadow" in cmd:
            return ("ACCT sysinfra :: sysinfra P 01/01/2020 0 99999 7 :: "
                    "Last password change:Jan 01, 2020|"
                    "Maximum number of days between password change:99999|")
        return _runner(host, cmd)

    r = ac.collect(runner_pwnever, "203.0.113.5")
    f = ar.evaluate(r["accounts"], ar.DEFAULT_THRESHOLDS)
    assert any(x["rule_id"] == "R2b" and x["username"] == "sysinfra"
               and x["verdict"] == "fail" for x in f)


def test_aix標準帳號名也認得():
    assert ac.STD_MGMT_ACCOUNTS.get("sys004") == "aix"
    assert ac.classify_account({"username": "sys004", "uid": 300, "shell": "/bin/ksh"},
                               {}, set()) == "mgmt"
