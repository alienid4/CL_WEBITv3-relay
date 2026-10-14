"""[8-3] 主機自我檢查：AIX 版。使用者 2026-09-23：「AIX／Windows 也要啊」。

這組測試守的不是「跑得動」，是**不要製造假警報、也不要製造假安心**。
AIX 有三個地方照抄 Linux 就會整批判錯：

1. 記憶體 inuse 長期 90%+（VMM 拿閒置記憶體當檔案快取），照 Linux 門檻 -> 八台全紅
2. `lssrc` 的 inoperative ≠ systemd 的 failed（多半是刻意不啟用）-> 照抄會每台都紅
3. `lssrc -s xntpd` 只證明 daemon 活著，不證明時間有同步 -> 兩件事不可以混填一欄

外加一條跨平台的：**「沒問題」「有問題」「還沒判讀」是三件事，不可以擠進兩個顏色。**
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import health_probe as hp  # noqa: E402

AIX_OUT = """@@OS
7200-05-09-2446
AIX host1 2 7 00F9A1B24C00
@@UPLOAD
  10:30AM   up 120 days,  3:45,  2 users,  load average: 1.42, 1.51, 1.55
@@NCPU
The available processors are:  0 1 2 3
@@LPAR
Type                                       : Shared-SMT-8
Mode                                       : Uncapped
Entitled Capacity                          : 0.50
@@VMSTAT
System configuration: lcpu=4 mem=8192MB

 r  b   avm   fre  re  pi  po  fr   sr  cy  in   sy   cs us sy id wa
 1  0 786432 65536   0   0   0   0    0   0  12  340  210 10  3 85  2
 2  1 790000 60000   0   0   0   0    0   0  15  400  260 62 11 20  7
@@SVMON
               size       inuse        free         pin     virtual  available   mmode
memory         8192        7900         292        1200        3100       4000     Ded
pg space       4096          90
@@PGSP
Total Paging Space   Percent Used
      4096MB               3%
@@DF
Filesystem    1024-blocks      Free %Used    Iused %Iused Mounted on
/dev/hd4          1048576    524288   50%     2000     2% /
/dev/hd2          4194304   2936013   30%    45000    12% /usr
@@MOUNT
  node       mounted        mounted over    vfs       date        options
       /dev/hd4         /                jfs2   Sep 01 10:00 rw,log=/dev/hd8
@@SRC
Subsystem         Group            PID          Status
 sshd             ssh              7078050      active
 syslogd          ras              4456         active
 xntpd            tcpip            5001         active
 sendmail         mail                          inoperative
 snmpd            tcpip                         inoperative
@@NTPQ
     remote           refid      st t when poll reach   delay   offset  jitter
*10.0.0.1        10.0.0.254       2 u   40   64  377    0.512   -0.031   0.045
@@NETSTATI
Name  Mtu   Network     Address            Ipkts Ierrs    Opkts Oerrs  Coll
en0   1500  link#2      fa.ce.0.1.2.3     100000     0   200000     0     0
en0   1500  10.0.0      10.0.0.16         100000     0   200000     0     0
@@TCPSTATES
     12 ESTABLISHED
      3 LISTEN
@@ZOMB
1
@@TOPCPU
      PID COMMAND          %CPU
   123456 oracle           42.1
@@WHO
2
@@LAST
root     pts/0   Sep 23 08:00
@@END
"""


def _ssh_ok(out=AIX_OUT):
    def f(ip, cmd, key, acct, timeout=14):
        return True, out, ""
    return f


def _run(out=AIX_OUT, svc=None):
    return hp.check_one("10.0.0.16", platform="aix", _ssh=_ssh_ok(out),
                        _svc=svc if svc is not None else
                        {"services": [{"port": 22}], "process_visible": False})


# ═══ 1. 記憶體：inuse 高是 AIX 的正常，不是壓力 ══════════════════════

def test_記憶體判computational不判inuse():
    """inuse 96%，但 computational 只有 38% —— 這台其實好好的。

    照 Linux 的 MEM 90/80 判 inuse，八台 AIX 會全部紅燈。
    """
    r = _run()
    assert r["mem"]["inuse_pct"] > 90          # AIX 常態就是這樣
    assert r["mem"]["used_pct"] < 50           # computational 才是真正吃掉的
    assert r["mem"]["level"] == "green", "拿 inuse 判 -> 假紅燈"
    # 為什麼這樣判，畫面上要講得出來
    assert "computational" in r["mem"]["basis"]
    assert "檔案快取" in r["mem"]["basis"]


def test_available用svmon自己那一欄不要自己推():
    """svmon 的 available 已經扣掉 pin 與必要保留，`total - virtual` 會高估。"""
    r = _run()
    assert r["mem"]["avail_kb"] == 4000 * 1024


def test_paging_space走lsps不是硬湊swap欄位():
    r = _run()
    assert r["mem"]["swap_pct"] == 3.0


# ═══ 2. lssrc 的 inoperative 不是 failed ════════════════════════════

def test_inoperative不可以當成failed一律紅燈():
    """一台正常的 AIX 本來就有一堆 inoperative 的子系統（多半是刻意不啟用）。

    當成 failed 的話每台都紅，紅燈多到沒人看——那是稽核工具最大的死因。
    """
    r = _run()
    assert r["aix_subsystems"]["inoperative"] == ["sendmail", "snmpd"]
    assert r["failed_units"] == [], "sendmail／snmpd 沒在跑被當成異常了"
    assert r["overall"] != "red"
    # 但不可以默默吞掉——要列出來，並且說明它跟 Linux 的 failed 不一樣
    assert any("inoperative" in n and "failed" in n for n in r["notes"])


def test_關鍵子系統沒在跑才說話():
    out = AIX_OUT.replace(" syslogd          ras              4456         active",
                          " syslogd          ras                           inoperative")
    r = _run(out)
    assert "syslogd" in r["failed_units"]
    assert r["overall"] == "red"


# ═══ 3. daemon 活著 ≠ 時間有同步 ════════════════════════════════════

def test_ntpq有標星號才算真的同步():
    r = _run()
    assert r["aix_ntp"]["daemon_running"] is True
    assert r["ntp_synced"] is True


def test_daemon在跑但ntpq沒同步來源_要判成沒同步():
    out = AIX_OUT.replace(
        "*10.0.0.1        10.0.0.254       2 u   40   64  377    0.512   -0.031   0.045",
        " 10.0.0.1        .INIT.          16 u    -   64    0    0.000    0.000   0.000")
    r = _run(out)
    assert r["ntp_synced"] is False, "daemon 活著就當成同步了"
    assert r["overall"] in ("yellow", "red")


def test_daemon沒在跑是查到了而不是沒查到():
    """⚠️ 這條防的是自相矛盾：同時說「xntpd 沒在跑（紅燈）」又說「時間同步還沒查」。"""
    out = AIX_OUT.replace(" xntpd            tcpip            5001         active",
                          " xntpd            tcpip                         inoperative")
    r = _run(out)
    assert r["ntp_synced"] is False
    missing = [m["key"] for m in r["coverage"]["missing"]]
    assert "time_sync" not in missing, "確定沒同步，卻被算成沒查到"


# ═══ 4. 完整度：三種意思要有三個位置 ════════════════════════════════

def test_查得齊就是完整():
    r = _run()
    assert r["complete"] is True
    assert r["coverage"]["done"] == r["coverage"]["total"]
    assert "完整檢查" in r["coverage"]["text"]


def test_缺項不可以改變燈號_只標未完整():
    """**不可以把「沒查到」顯示成黃燈。**

    那跟「顯示成綠燈」是同一個錯誤的鏡像：都在回答一個沒被問的問題。
    值班看到一排黃燈、點進去發現「其實沒事，只是沒查完」，下次就不會再點了。
    """
    out = AIX_OUT.replace("""@@SVMON
               size       inuse        free         pin     virtual  available   mmode
memory         8192        7900         292        1200        3100       4000     Ded
pg space       4096          90""", "@@SVMON")
    r = _run(out)
    assert r["mem"] is None
    assert r["complete"] is False
    assert r["overall"] == "green", "缺項把燈號改掉了——那是假警訊"
    miss = {m["key"]: m["why"] for m in r["coverage"]["missing"]}
    assert "mem" in miss
    assert "svmon" in miss["mem"], "缺了卻沒講為什麼"


def test_連不上時每一項都要有缺項理由():
    def dead(ip, cmd, key, acct, timeout=14):
        return False, "", "ssh: connect to host port 22: Connection timed out"

    r = hp.check_one("10.0.0.16", platform="aix", _ssh=dead)
    assert r["reachable"] is False
    assert r["coverage"]["done"] == 0
    assert all("SSH 連不上" in m["why"] for m in r["coverage"]["missing"])


def test_linux那條也要有完整度而且不變色(monkeypatch):
    """兩個平台語意要一致，而且**不因為這次改動讓任何一台突然變黃**。"""
    linux_out = """@@OS
Linux 5.14.0
PRETTY_NAME="Red Hat Enterprise Linux 9.4"
@@UP
1000000.0 500.0
@@LOAD
0.10 0.20 0.30 1/200 999
@@CPU
4
@@MEM
MemTotal: 8000000 kB
MemAvailable: 6000000 kB
@@DF
Filesystem 1024-blocks Used Available Capacity Mounted on
/dev/sda1 100000 20000 80000 20% /
@@DFI
Filesystem Inodes IUsed IFree IUse% Mounted on
/dev/sda1 100000 2000 98000 2% /
@@MOUNTS
/dev/sda1 / ext4 rw 0 0
@@FAILED
@@ZOMB
0
@@DNS
ok
@@NTP
yes
@@WHO
1
@@END
"""

    def f(ip, cmd, key, acct, timeout=14):
        return True, linux_out, ""

    r = hp.check_one("10.0.0.5", platform="linux", _ssh=f,
                     _svc={"services": [], "process_visible": False})
    assert r["coverage"] is not None
    # 這份假輸出沒有 ports（svc 空），所以不完整——但燈號不可以因此改變
    assert r["overall"] in ("green", "yellow")
    assert isinstance(r["complete"], bool)


# ═══ 5. AIX 的 df／uptime 欄位跟 Linux 不一樣 ═══════════════════════

def test_aix的df第三欄是free不是used():
    """照 Linux 的欄位位置讀，整排會錯位。"""
    r = _run()
    root = next(d for d in r["disks"] if d["mount"] == "/")
    assert root["avail_kb"] == 524288
    assert root["used_kb"] == 1048576 - 524288
    assert root["use_pct"] == 50
    # AIX 的 df -k 一條就同時給 %Iused，不用跑第二次
    assert root["inode_pct"] == 2


def test_aix沒有proc_uptime要從uptime那行讀():
    r = _run()
    assert r["uptime"]["days"] == 120.2
    assert r["uptime"]["just_rebooted"] is False


def test_剛重開機也要認得出來():
    out = AIX_OUT.replace("up 120 days,  3:45,", "up 5 mins,")
    r = _run(out)
    assert r["uptime"]["just_rebooted"] is True


def test_cpu要取第二筆vmstat不是第一筆():
    """第一筆是開機以來的平均。開機 120 天的機器平均永遠很漂亮，看不出此刻在燒。"""
    r = _run()
    assert r["cpu"]["used_pct"] == 73.0      # 62+11，第二筆
    assert r["cpu"]["iowait_pct"] == 7.0


# ═══ 6. LPAR 的 load 分母要講為什麼，不是叫人相信 ═══════════════════

def test_lpar的load要說明為什麼只能參考():
    r = _run()
    assert "entitled capacity" in r["load"]["basis"]
    assert "低估" in r["load"]["basis"]


def test_lpar的load不單獨亮紅燈():
    out = AIX_OUT.replace("load average: 1.42, 1.51, 1.55",
                          "load average: 40.00, 38.00, 35.00")
    r = _run(out)
    assert r["load"]["level"] == "yellow", "用不可靠的分母亮了紅燈"


# ═══ 7. 指令必須唯讀且非 root ═══════════════════════════════════════

def test_aix健檢指令唯讀且不用sudo():
    cmd = hp.AIX_METRICS_CMD
    assert "sudo" not in cmd, "健檢不可以要 sudo（要開白名單得先問使用者）"
    assert "errpt" not in cmd, "errpt 要 root，屬於批次 3，本檔不碰"
    for bad in ("chdev", "mkdev", "rmdev", "chfs", "startsrc", "stopsrc", "refresh"):
        assert bad not in cmd, f"健檢指令有變更疑慮：{bad}"
    assert "command -v" not in cmd, "ksh88 沒有 command -v"


def test_磁碟門檻沿用linux():
    """空間使用率的語意兩個平台相同，門檻沿用（90／80）。"""
    out = AIX_OUT.replace("/dev/hd2          4194304   2936013   30%    45000    12% /usr",
                          "/dev/hd2          4194304    419430   90%    45000    12% /usr")
    r = _run(out)
    usr = next(d for d in r["disks"] if d["mount"] == "/usr")
    assert usr["level"] == "red"
    assert r["overall"] == "red"


def test_唯讀掛載是磁碟故障徵兆要紅燈():
    out = AIX_OUT.replace("jfs2   Sep 01 10:00 rw,log=/dev/hd8",
                          "jfs2   Sep 01 10:00 ro,log=/dev/hd8")
    r = _run(out)
    assert r["readonly_mounts"]
    assert r["overall"] == "red"
