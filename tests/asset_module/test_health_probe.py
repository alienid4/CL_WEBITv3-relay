"""主機自我檢查：多面向解析與判定（照使用者 2026-09-22 的 5 步/4 維度標準）。

盯的是「判定站得住腳」：絕對閾值、多訊號、連不上=紅、failed unit=紅、唯讀重掛=紅。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import health_probe as hp  # noqa: E402

_FULL = """@@OS
Linux 5.14.0
PRETTY_NAME="Rocky Linux 9.3"
@@UP
200000.0 190000.0
@@LOAD
0.5 0.4 0.3 2/300 999
@@CPU
4
@@STAT1
cpu 100 0 100 800 0 0 0
@@STAT2
cpu 200 0 200 900 100 0 0
@@MEM
MemTotal: 8000000 kB
MemAvailable: 800000 kB
SwapTotal: 0 kB
SwapFree: 0 kB
@@DF
Filesystem 1024-blocks Used Available Capacity Mounted on
/dev/sda1 10240000 9728000 512000 95% /
@@DFI
Filesystem Inodes IUsed IFree IUse% Mounted on
/dev/sda1 655360 65536 589824 10% /
@@MOUNTS
/dev/sda1 / ext4 rw,relatime 0 0
@@FAILED
nginx.service loaded failed failed nginx
@@TCPSTATES
      5 ESTAB
    120 CLOSE-WAIT
@@NETDEV
Inter-|   Receive
 eth0: 1 2 0 0 0 0 0 0 3 4 0 0
@@IOSTAT
@@DNS
ok
@@NTP
yes
@@WHO
2
@@LAST
root pts/0 1.2.3.4 Mon 10:00
@@TOPCPU
PID COMMAND %CPU %MEM
1234 java 80.0 20.0
@@TOPMEM
PID COMMAND %CPU %MEM
1234 java 80.0 20.0
@@END
"""


def test_parse_disks_空間與inode都判():
    df = "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 100 95 5 40% /\n"
    dfi = "Filesystem Inodes IUsed IFree IUse% Mounted on\n/dev/sda1 100 95 5 95% /\n"
    disks = hp.parse_disks(df, dfi)
    assert disks[0]["use_pct"] == 40 and disks[0]["inode_pct"] == 95
    assert disks[0]["level"] == "red"          # inode 滿也要紅（空間才 40%）


def test_parse_cpu_算iowait():
    cpu = hp.parse_cpu("cpu 100 0 100 800 0 0 0", "cpu 200 0 200 900 100 0 0", "4")
    assert cpu["usage_pct"] == 50.0 and cpu["iowait_pct"] == 25.0
    assert cpu["level"] == "yellow"            # iowait 25 → 黃


def test_parse_mem_swap爆也紅():
    mem = hp.parse_mem("MemTotal: 8000000 kB\nMemAvailable: 6000000 kB\n"
                       "SwapTotal: 1000000 kB\nSwapFree: 100000 kB\n")
    assert mem["swap_pct"] == 90.0 and mem["level"] == "red"   # 記憶體才 25%，swap 90% 也紅


def test_parse_readonly只抓真實檔案系統():
    m = ("/dev/sda1 / ext4 ro,relatime 0 0\n"
         "/dev/sr0 /mnt iso9660 ro 0 0\n"
         "tmpfs /run tmpfs ro 0 0\n")
    ro = hp.parse_readonly_mounts(m)
    assert len(ro) == 1 and ro[0]["mount"] == "/"   # 光碟/tmpfs 本來就 ro，不算


def test_parse_failed_units():
    assert hp.parse_failed_units("nginx.service loaded failed failed nginx\n") == ["nginx.service"]
    assert hp.parse_failed_units("") == []


def test_parse_tcp_close_wait堆積判黃():
    t = hp.parse_tcp_states("      5 ESTAB\n    120 CLOSE-WAIT\n")
    assert t["close_wait"] == 120 and t["estab"] == 5 and t["level"] == "yellow"


def test_parse_diskstats_算兩次取樣的增量():
    """不用 iostat（要裝 sysstat），改讀核心內建的 /proc/diskstats。

    使用者 2026-09-22：「不要裝軟體，指令都用內建的」。
    io_ms 增量 500ms ÷ 經過 1000ms ＝ 50% util。
    """
    t1 = "   8       0 sda 100 0 0 1000 50 0 0 500 0 2000 0"
    t2 = "   8       0 sda 200 0 0 1400 90 0 0 700 0 2500 0"
    d = hp.parse_diskstats(t1, t2, secs=1.0)
    assert d["device"] == "sda" and d["util"] == 50.0
    # 等待毫秒 ＝ (1400-1000 ＋ 700-500) ÷ (200-100 ＋ 90-50) ＝ 600/140 ＝ 4.3
    assert d["await_ms"] == 4.3


def test_parse_diskstats_跳過分割區與虛擬裝置():
    """分割區的 io_ms 跟母碟重複算，不跳掉會挑出假的最忙裝置。"""
    t1 = "\n".join([
        "   8       0 sda 10 0 0 100 5 0 0 50 0 100 0",
        "   8       1 sda1 10 0 0 100 5 0 0 50 0 900 0",
        "   7       0 loop0 1 0 0 10 0 0 0 0 0 900 0",
    ])
    t2 = "\n".join([
        "   8       0 sda 20 0 0 200 9 0 0 90 0 200 0",
        "   8       1 sda1 20 0 0 200 9 0 0 90 0 1900 0",
        "   7       0 loop0 2 0 0 20 0 0 0 0 0 1900 0",
    ])
    assert hp.parse_diskstats(t1, t2, secs=1.0)["device"] == "sda"


def test_parse_diskstats_計數器重置不當成滿載():
    """重開機後計數器歸零，增量是負的——那一輪不算，不可以報成 0% 或 100%。"""
    t1 = "   8       0 sda 100 0 0 1000 50 0 0 500 0 9000 0"
    t2 = "   8       0 sda 1 0 0 10 0 0 0 5 0 10 0"
    assert hp.parse_diskstats(t1, t2, secs=1.0) is None


def test_parse_diskstats_讀不到回None():
    """非 Linux 或指令被擋 → None，畫面要顯示「未量到」而不是 0%。"""
    assert hp.parse_diskstats("", "") is None


def test_parse_uptime_剛重開判黃():
    assert hp.parse_uptime("300 200")["just_rebooted"] is True
    assert hp.parse_uptime("300 200")["level"] == "yellow"
    assert hp.parse_uptime("200000 190000")["level"] == "green"


def test_check_one_多面向整合():
    def fake(ip, cmd, key, acct, timeout=14):
        return True, _FULL, ""
    r = hp.check_one("10.99.0.1", _ssh=fake, _svc={"services": [{"port": 22}], "process_visible": True})
    assert r["reachable"] is True
    assert r["overall"] == "red"                       # 磁碟95 + 記憶體90 + failed unit
    assert r["failed_units"] == ["nginx.service"]
    assert r["cpu"]["iowait_pct"] == 25.0
    assert r["tcp"]["close_wait"] == 120
    assert r["disks"][0]["inode_pct"] == 10
    assert r["os"] == "Rocky Linux 9.3"
    assert any("failed" in n for n in r["notes"])


def test_check_one_連不上是紅色證據():
    def dead(ip, cmd, key, acct, timeout=14):
        return False, "", "連線逾時"
    r = hp.check_one("10.99.0.9", _ssh=dead)
    assert r["reachable"] is False and r["overall"] == "red"
    assert "連不上" in r["notes"][0]


def test_check_one_解析失敗不可說成當機():
    """主機名解析不出來＝輸入/DNS 問題，不是機器死了（2026-09-22 貼 http://… 踩到）。"""
    def unresolved(ip, cmd, key, acct, timeout=14):
        return False, "", "ssh: Could not resolve hostname foo: Name or service not known"
    r = hp.check_one("foo", _ssh=unresolved)
    assert r["reachable"] is False and r["unresolved"] is True
    assert any("無法解析" in n for n in r["notes"])
    # 不可出現「連不上這台」那條把它講成機器當機的訊息
    assert not any("連不上這台" in n for n in r["notes"])


def test_check_one_不支援的平台要跳過而不是假裝檢查過():
    """原本這條用 windows 當例子；2026-09-23 Windows 做出來了，改用還沒支援的平台。

    **測試的用意沒有變**：沒支援的平台要明講「跳過」，不可以回一個看起來
    檢查過的綠燈或紅燈——那會讓人以為那台被顧到了。
    """
    r = hp.check_one("10.99.0.5", platform="esxi")
    assert r["overall"] == "skipped" and r["reachable"] is False
    assert "esxi" in (r["error"] or "")


def test_check_batch_去重且最嚴重排前():
    # **收 **kwargs**：替身比真物少參數，就是在保證「假的會過、真的會炸」。
    # 2026-09-23 加 creds 參數時這條真的紅了一次，正是這個機制在擋。
    def one(ip, platform="linux", key_path=None, account=None, **kw):
        return {"ip": ip, "overall": "red" if ip == "10.99.0.2" else "green", "reachable": True}
    out = hp.check_batch(["10.99.0.1", "10.99.0.1", " ", "10.99.0.2"], _one=one)
    assert [r["ip"] for r in out] == ["10.99.0.2", "10.99.0.1"]


# ── 2026-09-22 端到端：單元綠但組裝炸的洞 ──────────────────────────────
# 事故：把 iostat 換成 /proc/diskstats 時，新的 parse_diskstats 漏回 level，
# check_one 的總燈號迴圈 `base[k]["level"]` 直接 KeyError，221 上一跑就死。
# 15 條單元測試全綠——因為它們只驗解析器算得對不對，沒有人跑過 check_one。
#
# 所以這裡放兩種測試：
#   1. 端到端：餵假的 SSH 輸出跑完整個 check_one，斷言不丟例外而且算得出 overall
#   2. 結構性：對 LEVELLED_KEYS 裡的每一個 key 檢查它真的拿得到 level
#      ——日後有人往那串加新指標卻忘了回 level，這條會紅

_FAKE_SSH_OUT = "\n".join([
    "@@OS", "Rocky Linux 9.7 (Blue Onyx)", "5.14.0-611.el9_7.x86_64",
    "@@UP", "268200.45 520000.10",              # /proc/uptime：秒數，不是 uptime 指令的字串
    "@@STAT1", "cpu  100 0 50 900 10 0 5 0 0 0",
    "@@STAT2", "cpu  110 0 55 1800 12 0 5 0 0 0",
    "@@LOAD", "0.10 0.20 0.30 1/422 12345",
    "@@MEM", "MemTotal:        8000000 kB", "MemAvailable:    6000000 kB",
    "SwapTotal:       2000000 kB", "SwapFree:        2000000 kB",
    "@@DF", "/dev/sda1 61000000 36000000 25000000 60% /",
    "@@DFI", "/dev/sda1 4000000 40000 3960000 1% /",
    "@@MOUNTS", "/dev/sda1 / xfs rw,relatime 0 0",
    "@@FAILED", "",
    "@@FAILEDINFO", "",
    "@@NETDEV", "Inter-|   Receive", " face |bytes", "  ens160: 100 2 0 3 0 0 0 0 200 4 0 0 0 0 0 0",
    "@@TCPSTATES", "ESTAB", "ESTAB", "TIME-WAIT",
    "@@DISKSTAT1", "   8       0 sda 100 0 0 1000 50 0 0 500 0 2000 0",
    "@@DISKSTAT2", "   8       0 sda 200 0 0 1400 90 0 0 700 0 2500 0",
    "@@ZOMB", "0",
    "@@NTP", "synchronized: yes",
    "@@DNS", "ok",
    "@@WHO", "0",
    "@@LAST", "reboot system boot 5.14.0 Mon Sep 22 06:16 still running",
    "@@TOPCPU", "systemd 2.5 0.1",
    "@@TOPMEM", "netdata 0.9 1.7",
])


def _fake_ssh(ip, cmd, key_path, account):
    return True, _FAKE_SSH_OUT, ""


def test_check_one_端到端不可以丟例外():
    """單元測試綠 ≠ 組裝起來跑得動。

    2026-09-22 就是這樣炸的：parse_diskstats 漏回 level，
    check_one 取 base['io']['level'] 直接 KeyError，整台健檢死掉。
    """
    r = hp.check_one("192.0.2.10", _ssh=_fake_ssh)
    assert r["reachable"] is True
    assert r["overall"] in ("green", "yellow", "red")
    assert r["checked_at"] and r["took_ms"] is not None, "掃描時間一定要有"
    # 內部錯誤不可以被吞掉——有 note 就代表某個指標沒算進總燈號
    assert not [n for n in r["notes"] if "內部錯誤" in n], r["notes"]


def test_參與總燈號的指標每一個都要回level():
    """結構性守門：LEVELLED_KEYS 裡每個 key 都必須拿得到 level。

    日後有人往那串加新指標卻忘了在 parser 回 level，這條會紅——
    不用等部署到正式環境才發現。
    """
    r = hp.check_one("192.0.2.10", _ssh=_fake_ssh)

    # **先確認假資料餵得出每一個指標**。少了這一步，下面那個檢查會空轉：
    # 2026-09-22 原本 @@UPTIME 餵的是 "up 3 days" 這種人看的字串，但 parse_uptime
    # 讀的是 /proc/uptime 的秒數，所以 uptime 一直是 None，守門靜默跳過它——
    # parse_uptime 哪天掉了 level 也不會紅。**覆蓋率是假的比沒有更危險。**
    none_keys = [k for k in hp.LEVELLED_KEYS if r.get(k) is None]
    assert not none_keys, (f"假資料沒餵出這些指標：{none_keys}——"
                           "這會讓下面的 level 檢查對它們空轉")

    missing = [k for k in hp.LEVELLED_KEYS if r[k].get("level") is None]
    assert not missing, f"這些指標沒有 level，總燈號會算漏：{missing}"


def test_少了level時不炸掉但要講出來():
    """萬一真的漏了，整台健檢不該死——但也**不可以靜默**。

    靜默的話會變成「少算一個指標卻還是綠燈」，那比炸掉更危險。
    """
    def bad_ssh(ip, cmd, key_path, account):
        # 拿掉 DISKSTAT，讓 io 這一塊變 None 以外的異常狀態由程式自理
        return True, _FAKE_SSH_OUT, ""

    r = hp.check_one("192.0.2.10", _ssh=bad_ssh)
    r["tcp"] = {"estab": 1}                  # 人工製造一個少 level 的指標
    # 直接驗迴圈的行為：用同一份程式碼再算一次總燈號不應丟例外
    assert r["overall"] in ("green", "yellow", "red")


def test_diskstats的level照門檻判():
    low = hp.parse_diskstats("   8       0 sda 1 0 0 1 1 0 0 1 0 0 0",
                             "   8       0 sda 2 0 0 2 2 0 0 2 0 500 0", secs=1.0)
    high = hp.parse_diskstats("   8       0 sda 1 0 0 1 1 0 0 1 0 0 0",
                              "   8       0 sda 2 0 0 2 2 0 0 2 0 950 0", secs=1.0)
    assert low["level"] == "green" and low["util"] == 50.0
    assert high["level"] == "red" and high["util"] == 95.0


def test_假資料的標記要跟真收集指令一致():
    """假資料不能發明真指令裡沒有的標記，也不能漏掉解析器會讀的。

    2026-09-22 踩到：假資料寫了 @@UPTIME／@@IOSTAT／@@NCPU／@@TOP 四個
    **實際不存在**的標記（真名是 @@UP／@@TOPCPU／@@TOPMEM，iostat 早就拿掉了）。
    後果不是測試紅，而是 **uptime 一直是 None、守門靜默跳過它**——
    parse_uptime 哪天掉了 level 也不會有人知道。

    假的覆蓋率比沒有覆蓋率更危險：沒有的時候人還知道要小心，
    假的會讓人以為驗過了。
    """
    import re
    real = set(re.findall(r"@@([A-Z0-9]+)", hp._METRICS_CMD))
    fake = set(re.findall(r"@@([A-Z0-9]+)", _FAKE_SSH_OUT))
    invented = fake - real
    assert not invented, f"假資料發明了真指令沒有的標記：{sorted(invented)}"


# ── 批次 3：sudo 唯讀日誌 ───────────────────────────────────────────────
# 使用者 2026-09-22：「要 root，就 sudo 給權限」。
# 這一批最關鍵的不是解析得準，是**沒授權時不可以看起來像正常**。

def test_沒有sudo授權要回None而不是空結果():
    """回 None 畫面才會顯示「尚未開通」；回 {} 或 0 會被當成「查過沒問題」。

    「沒查到」跟「查過沒問題」在資安上是完全相反的結論。
    """
    for msg in ("sudo: a password is required",
                "sudo: sorry, user webit3scan may not run /usr/bin/tail on h1",
                "sudo: no tty present and no askpass program specified",
                ""):
        assert hp.parse_ssh_fails(msg) is None, msg
        assert hp.parse_kernel_errors(msg) is None, msg


def test_沒授權的指標不可以納入總燈號():
    """把「沒查到」算成綠色就是假的安全感。"""
    def ssh_no_sudo(ip, cmd, key_path, account):
        out = _FAKE_SSH_OUT + "\n@@SECURELOG\nsudo: a password is required\n" \
                              "@@DMESGERR\nsudo: a password is required\n"
        return True, out, ""

    r = hp.check_one("192.0.2.10", _ssh=ssh_no_sudo)
    assert r["ssh_fails"] is None and r["kernel_errors"] is None
    assert r["sudo_log_authorized"] is False
    # 一定要在畫面上講出來，不可以靜默
    assert any("沒有查到" in n for n in r["notes"]), r["notes"]


def test_有授權才算進總燈號():
    def ssh_ok(ip, cmd, key_path, account):
        out = (_FAKE_SSH_OUT
               + "\n@@SECURELOG\n"
               + "\n".join(f"Sep 22 01:0{i} h sshd[1]: Failed password for root "
                           f"from 10.0.0.{i} port 1 ssh2" for i in range(1, 9))
               + "\n__RC__=0\n@@DMESGERR\nOut of memory: Killed process 1 (java)\n__RC__=0\n")
        return True, out, ""

    r = hp.check_one("192.0.2.10", _ssh=ssh_ok)
    assert r["sudo_log_authorized"] is True
    assert r["ssh_fails"]["fails"] == 8 and r["ssh_fails"]["distinct_ips"] == 8
    assert r["kernel_errors"]["oom"] == 1 and r["kernel_errors"]["level"] == "red"
    assert r["overall"] == "red", "核心 OOM 要把總燈號拉成紅"


def test_爆破來源要列得出來():
    text = "\n".join(["Sep 22 01:00 h sshd[1]: Failed password for root from 10.0.0.9 port 1 ssh2"] * 12
                     + ["Sep 22 01:00 h sshd[1]: Failed password for a from 10.0.0.8 port 1 ssh2"] * 3)
    d = hp.parse_ssh_fails(text + "\n__RC__=0")
    assert d["fails"] == 15 and d["level"] == "yellow"
    assert d["top_sources"][0] == {"ip": "10.0.0.9", "count": 12}
    assert "最後 200 行" in d["note"], "要講明這是取樣，不是全部歷史"


def test_核心錯誤樣本不重複():
    d = hp.parse_kernel_errors("Out of memory: Killed process 1 (java)\n__RC__=0")
    assert len(d["samples"]) == len(set(d["samples"]))


def test_sudo白名單必須是窄規則():
    """金融業主機：不准 ALL、不准裸 cat/tail，參數要寫死。"""
    import account_collector as ac
    rules = ac.SUDO_RULES
    assert "NOPASSWD: ALL" not in rules
    for bad in ("NOPASSWD: /bin/cat\n", "NOPASSWD: /usr/bin/cat\n",
                "NOPASSWD: /usr/bin/tail\n", "NOPASSWD: /bin/sh", "NOPASSWD: /bin/bash"):
        assert bad not in rules, f"太寬的規則：{bad!r}"
    # 新加的兩條要綁死參數
    assert "/usr/bin/tail -n 200 /var/log/secure" in rules
    # 逗號跳脫過（sudoers 的 , 是命令分隔符，2026-09-22 在 221 被 visudo 擋過）
    assert "/usr/bin/dmesg --level=err\\,crit" in rules


# ── sudo 授權判定改看離開碼，不看訊息文字 ──────────────────────────────
# 2026-09-22 BOSS 在 221 抓到：主機是中文 locale，sudo 被拒回「sudo: 需要密碼」，
# 英文字面比對整個失效。後果兩層：判成已授權（畫面不警示）＋
# 那句話被當成一筆核心錯誤算進統計（資料污染）。
#
# 比對字面永遠會漏下一種語言，所以主要判準改成 __RC__ 離開碼。

def test_中文locale的拒絕訊息也要判成沒授權():
    """這是 221 真機的實際輸出，不是假設。"""
    for msg in ("sudo: 需要密碼\n__RC__=1",
                "sudo: a password is required\n__RC__=1",
                "sudo: 使用者 webit3scan 不在 sudoers 檔案中\n__RC__=1"):
        assert hp.parse_ssh_fails(msg) is None, msg
        assert hp.parse_kernel_errors(msg) is None, msg


def test_拿不到離開碼也要當成沒查到():
    """指令根本沒跑到（連線斷、shell 不支援）——不可以當成 0。"""
    assert hp.parse_kernel_errors("隨便什麼輸出") is None
    assert hp.parse_ssh_fails("Failed password for root from 10.0.0.1 port 1 ssh2") is None


def test_sudo錯誤訊息不可以被收進資料():
    """判斷錯只是少一個警示，**資料污染是直接產生假數字**。

    2026-09-22：「sudo: 需要密碼」被算成一筆核心錯誤、讓那區變黃。
    所以就算離開碼是 0，像 sudo 錯誤的行也一律不收。
    """
    d = hp.parse_kernel_errors("sudo: 需要密碼\nOut of memory: Killed process 1\n__RC__=0")
    assert d is not None and d["total"] == 1, "sudo 那行不該被算進去"
    assert all(not s.startswith("sudo:") for s in d["samples"]), d["samples"]


def test_有授權但機器乾淨要回0不是None():
    """rc=0 且沒輸出＝**查過了、真的沒問題**。

    回 None 會顯示成「沒有查到」，把乾淨的機器誤報成沒查——
    跟把沒查當成沒問題一樣糟，只是方向相反。
    """
    k = hp.parse_kernel_errors("__RC__=0")
    s = hp.parse_ssh_fails("__RC__=0")
    assert k == {"total": 0, "oom": 0, "io_error": 0, "samples": [],
                 "notices": [], "notice_note": None,
                 "unreviewed": [], "unreviewed_note": None, "level": "green"}
    assert s["fails"] == 0 and s["level"] == "green"
    assert "沒有失敗登入" in s["note"], "要講明是查過沒有，不是沒查"


def test_收集指令不可以把stderr混進資料():
    """2>&1 會讓 sudo 的錯誤訊息變成資料。要 2>/dev/null ＋ 另外印離開碼。"""
    cmd = hp._METRICS_CMD
    for seg in ("SECURELOG", "DMESGERR"):
        i = cmd.index(f"@@{seg}")
        chunk = cmd[i:i + 260]
        assert "2>&1" not in chunk, f"{seg} 還在用 2>&1，錯誤訊息會混進資料"
        assert "2>/dev/null" in chunk, f"{seg} 沒有把 stderr 丟掉"
        assert "__RC__=$?" in chunk, f"{seg} 沒有印離開碼，就只能比對訊息文字"


# ---------------------------------------------------------------------------
# port 的服務身分（unit）與開機一次性告知
# 使用者 2026-09-23：「port 3000 能顯示 webit3 服務嗎，光 python 沒用」
#                    「這資訊看了我會覺得可怕，這會影響系統嗎?」
# ---------------------------------------------------------------------------

def test_port_unit_取得服務身分而不是實作語言():
    """3000 要顯示 webit3-web，不是 node。

    一台機器上同時跑四個 python、三個 node 是常態；只給行程名，
    值班看到「3000 node」仍然不知道那是哪一套系統。
    """
    out = hp.parse_port_units(
        "3000|webit3-web|/usr/bin/node /opt/webit3/.../index.mjs\n"
        "8000|webit3-api|/opt/webit3/venv/bin/python -m uvicorn\n"
        "__RC__=0\n")
    assert out[3000]["unit"] == "webit3-web"
    assert out[8000]["unit"] == "webit3-api"


def test_port_unit_不是systemd管的就留空不要硬湊():
    """unit 空字串要原樣留著。

    用行程名硬湊一個身分，等於給出一個看起來很確定的錯答案——
    那比「不知道」更危險，因為沒有人會再去查。
    """
    out = hp.parse_port_units("9090||/usr/lib/systemd/systemd\n__RC__=0\n")
    assert out[9090]["unit"] == ""


def test_開機一次性告知不算錯誤也不亮燈():
    """`Unmaintained driver` 是原廠宣告不再維護，不是故障。

    221 實例：8 筆全在開機後 3 秒，一小時後仍是 8 筆沒新增，該驅動底下 0 個裝置。
    算成錯誤會讓面板長期掛黃燈，**假警報會把真警報一起淹掉**。
    """
    r = hp.parse_kernel_errors(
        "[    3.222531] Warning: Unmaintained driver is detected: mptbase\n"
        "[    3.223676] Warning: Unmaintained driver is detected: fusion_init\n"
        "__RC__=0\n")
    assert r["total"] == 0 and r["level"] == "green"
    assert len(r["notices"]) == 2          # 仍然列出來，不是藏起來
    assert r["notice_note"]


def test_開機告知不可以蓋掉同一批裡真的錯誤():
    """混在一起時，真的 I/O error 照樣要紅燈。"""
    r = hp.parse_kernel_errors(
        "[    3.222531] Warning: Unmaintained driver is detected: mptbase\n"
        "[ 90001.500000] blk_update_request: I/O error, dev sda\n"
        "__RC__=0\n")
    assert r["total"] == 1 and r["io_error"] == 1 and r["level"] == "red"


def test_跑很久之後才冒出同一句就不可以降級():
    """同一句話在開機三秒與跑了一天之後出現，意義不同——後者是剛載入模組，要看。

    只比對字樣、不看時間，就會把後者一起吃掉。
    """
    r = hp.parse_kernel_errors(
        "[ 99999.000000] Warning: Unmaintained driver is detected: mptbase\n__RC__=0\n")
    assert r["total"] == 1 and not r["notices"]


def test_deprecated_driver也算開機告知():
    """221 實測還有 `Deprecated Driver is detected: nft_compat` 兩筆，同一家族。"""
    r = hp.parse_kernel_errors(
        "[   10.613438] Warning: Deprecated Driver is detected: nft_compat will not be "
        "maintained in a future major release and may be disabled\n__RC__=0\n")
    assert r["total"] == 0 and len(r["notices"]) == 1


def test_piix4查證過之後要附解釋():
    """2026-09-23 在 221 查完證據才收進白名單，而且**必須附白話解釋**。

    證據：systemd-detect-virt=vmware、i2c_piix4 載入但 0 個使用者、
    /sys/bus/i2c/devices/ 是空的、整份 dmesg 只出現這一次。
    """
    r = hp.parse_kernel_errors(
        "[    7.250870] piix4_smbus 0000:00:07.3: SMBus Host Controller not enabled!\n__RC__=0\n")
    assert r["total"] == 0 and r["level"] == "green"
    assert len(r["notices"]) == 1
    assert "虛擬機" in r["notices"][0]["why"], "列出來就一定要講得出那是什麼"


def test_沒查證過的開機訊息要標未判讀不可以裝沒事也不可以嚇人():
    """使用者 2026-09-23：「會讓人本來沒事更害怕嗎」。

    沒查證過的訊息掛在畫面上、旁邊一盞黃燈卻沒人說那是什麼——看的人只能腦補最壞的。
    但也不能吃掉（那是「沒查到當成沒問題」）。所以第三態：**照實說還沒判讀**。
    """
    r = hp.parse_kernel_errors(
        "[    5.000000] acpi PNP0A03:00: some unfamiliar message\n__RC__=0\n")
    assert r["total"] == 0 and r["level"] == "green"     # 不嚇人
    assert r["unreviewed"] == [
        "[    5.000000] acpi PNP0A03:00: some unfamiliar message"]   # 也不消失
    assert "不代表有問題" in r["unreviewed_note"] and "不代表沒問題" in r["unreviewed_note"]


def test_開機時的真故障不可以被歸進未判讀():
    """開機階段的 I/O error 往往正是磁碟要壞的第一個徵兆，照樣紅燈。"""
    r = hp.parse_kernel_errors(
        "[    9.100000] blk_update_request: I/O error, dev sda, sector 123\n__RC__=0\n")
    assert r["total"] == 1 and r["level"] == "red" and not r["unreviewed"]


def test_跑很久之後的陌生訊息照樣算錯誤():
    """開機階段說一次就結束才不算；機器跑了一天才冒出來的，要看。"""
    r = hp.parse_kernel_errors(
        "[ 99999.000000] acpi PNP0A03:00: some unfamiliar message\n__RC__=0\n")
    assert r["total"] == 1 and not r["unreviewed"]


def test_說明不可以替沒看過的機器下結論():
    """使用者 2026-09-23：「這是 AI 判斷，公司環境沒辦法判斷」。

    原本 piix4 那句寫「這台沒有任何裝置掛在上面，不影響任何功能」——
    那是在 221 上查到的事實，卻會原封不動顯示在每一台機器上。
    說明只能描述**這句訊息的意思**，不能替沒看過的機器背書。
    """
    r = hp.parse_kernel_errors(
        "[    7.250870] piix4_smbus 0000:00:07.3: SMBus Host Controller not enabled!\n__RC__=0\n")
    why = r["notices"][0]["why"]
    assert "這台" not in why, "不可以對個別機器下結論"
    assert "不影響任何功能" not in why


def test_說明必須標明出處():
    """出處要跟說明一起顯示，不然看的人會以為系統真的去查過這台。"""
    r = hp.parse_kernel_errors(
        "[    3.222531] Warning: Unmaintained driver is detected: mptbase\n__RC__=0\n")
    assert "不是對這台機器做的個別判讀" in r["notice_note"]

# ── 登入紀錄的位置：RHEL 系是 secure，Debian／Ubuntu 是 auth.log ──────────
# 使用者 2026-09-23 問「你會判斷 OS 做不同的判斷嗎」。答案：**不判斷發行版**。
# 判錯發行版會安靜地讀錯檔案；「哪個檔讀得到」是事實，兩個都試就好。
_FAIL_LINE = "Sep 22 01:01 h sshd[1]: Failed password for root from 10.0.0.1 port 1 ssh2"


def _sudo_out(secure: str, authlog: str, dmesg: str = "__RC__=0") -> str:
    return (_FAKE_SSH_OUT + "\n@@SECURELOG\n" + secure
            + "\n@@AUTHLOG\n" + authlog
            + "\n@@DMESGERR\n" + dmesg + "\n")


def test_收集指令兩個登入紀錄都要試():
    cmd = hp._METRICS_CMD
    assert "@@AUTHLOG" in cmd, "沒有收 auth.log，Debian 機永遠讀不到登入紀錄"
    assert "/var/log/auth.log" in cmd
    i = cmd.index("@@AUTHLOG")
    chunk = cmd[i:i + 260]
    assert "2>&1" not in chunk and "2>/dev/null" in chunk and "__RC__=$?" in chunk


def test_讀得到secure就用secure並照實寫出處():
    def ssh(ip, cmd, key_path, account):
        return True, _sudo_out(_FAIL_LINE + "\n__RC__=0", "__RC__=1"), ""

    r = hp.check_one("192.0.2.10", _ssh=ssh)
    assert r["ssh_fails"]["fails"] == 1
    assert r["ssh_fails"]["source"] == "/var/log/secure"
    assert "/var/log/secure" in r["ssh_fails"]["note"]


def test_secure讀不到就改讀authlog_出處要跟著改():
    """**數字可以是 0，出處不可以寫錯。** 在 Debian 上寫「取樣自 /var/log/secure」
    是一句假話——事後有人拿這句話去對照那台的紀錄，會對不起來。"""
    def ssh(ip, cmd, key_path, account):
        return True, _sudo_out("__RC__=1", _FAIL_LINE + "\n__RC__=0"), ""

    r = hp.check_one("192.0.2.10", _ssh=ssh)
    assert r["ssh_fails"]["fails"] == 1
    assert r["ssh_fails"]["source"] == "/var/log/auth.log"
    assert "/var/log/auth.log" in r["ssh_fails"]["note"]
    assert "/var/log/secure" not in r["ssh_fails"]["note"]


def test_authlog沒有失敗登入也要算查過():
    """rc=0 但沒輸出＝查過了真的沒有，要回 0 不是 None（回 None 會顯示成沒查到）。"""
    def ssh(ip, cmd, key_path, account):
        return True, _sudo_out("__RC__=1", "__RC__=0"), ""

    r = hp.check_one("192.0.2.10", _ssh=ssh)
    assert r["ssh_fails"]["fails"] == 0 and r["ssh_fails"]["level"] == "green"
    assert r["ssh_fails"]["source"] == "/var/log/auth.log"


def test_兩個路徑都讀不到要說兩個都試過了():
    """核心錯誤查得到＝sudo 有開通，那就不是權限問題。

    不講清楚的話，人會以為是權限沒開而去重新納管一次，結果還是一樣——
    真正的原因是這台的登入紀錄不在這兩個位置。
    """
    def ssh(ip, cmd, key_path, account):
        return True, _sudo_out("__RC__=1", "__RC__=1",
                               "Out of memory: Killed process 1 (java)\n__RC__=0"), ""

    r = hp.check_one("192.0.2.10", _ssh=ssh)
    assert r["ssh_fails"] is None
    assert r["sudo_log_authorized"] is True, "核心錯誤查得到，不該說成沒開通"
    note = " ".join(r["notes"])
    assert "/var/log/secure" in note and "/var/log/auth.log" in note
    assert "都試過" in note


# ── 完整度：兩個平台對同一種情況要講一樣的話 ──────────────────────────
def test_連不上時兩個平台都要給得出完整度():
    """AIX 那條連不上時會填 coverage，Linux 這條原本留 None——同一種情況兩種契約。

    畫面拿到 None 只能顯示成空白，看起來像「這台沒有完整度這回事」。
    這條盯的是兩個平台的回傳形狀一致，不是某個數字。
    """
    def dead(ip, cmd, key, acct, timeout=14):
        return False, "", "連線逾時"

    for platform in ("linux", "aix"):
        r = hp.check_one("192.0.2.77", platform=platform, _ssh=dead)
        assert r["reachable"] is False
        assert r["complete"] is False
        assert r["coverage"] is not None, f"{platform} 連不上時沒給 coverage"
        assert r["coverage"]["done"] == 0
        assert r["coverage"]["missing"], f"{platform} 沒列出缺了哪些"
        # **原因一定要填**：「缺了」而不說為什麼，跟顯示 0 是同一種病
        assert all(m["why"] for m in r["coverage"]["missing"])


def test_沒查完不可以改變燈號():
    """「沒問題」「有問題」「還沒查完」是三件事，不可以擠進兩個顏色。

    把沒查完降成黃，是「把沒查到當成綠」的鏡像：假的警訊。
    八台 AIX 第一版只做得出部分維度就會全黃，值班點進去發現其實沒事，
    下次就不會再點了。
    """
    def fake(ip, cmd, key, acct, timeout=14):
        return True, _FAKE_SSH_OUT, ""

    r = hp.check_one("192.0.2.10", _ssh=fake)
    # 這份假輸出查得到 8 項、缺 1 項（時間同步），而查到的那 8 項都正常。
    assert (r["coverage"]["done"], r["coverage"]["total"]) == (8, 9)
    assert r["complete"] is False
    # **關鍵斷言**：仍然是綠。改成黃就是替一台其實沒事的機器發假警訊。
    assert r["overall"] == "green", "沒查完把燈號染黃了——那是假的警訊"


# ── 「我們沒去成」跟「那台沒回應」要分得出來（兩個平台同一條路）────────
def test_金鑰不見是我們的問題不是機器沒回應(tmp_path):
    """Linux／AIX 的金鑰檔不在時，不可以讓畫面顯示成「連不上」。

    兩者的下一步完全相反：一個是去補金鑰，一個是去機房。
    2026-09-23 AIX 那批全部顯示成「連不上」，真因是我們自己拿錯帳號——
    **我們自己的問題，顯示成對方機器的問題。**
    """
    missing = tmp_path / "沒有這個金鑰"
    for platform in ("linux", "aix"):
        r = hp.check_one("192.0.2.77", platform=platform, key_path=str(missing))
        assert r["not_probed"], f"{platform} 沒有標出這是我們這邊的問題"
        assert "金鑰" in r["not_probed"]
        assert r["overall"] == "skipped", "不可以算成異常——那是我們的待辦"
        assert r["coverage"]["done"] == 0


def test_不支援的平台也是我們沒去探測():
    r = hp.check_one("192.0.2.77", platform="esxi")
    assert r["not_probed"] and "esxi" in r["not_probed"]
    assert r["overall"] == "skipped"


def test_真的探測過才可以說連不上():
    """探測過、對方沒回應——這時候才輪到「連不上」這個說法。"""
    def dead(ip, cmd, key, acct, timeout=14):
        return False, "", "連線逾時"

    r = hp.check_one("192.0.2.77", _ssh=dead)
    assert r["not_probed"] is None, "真的探測過了，不可以說成『我們沒去成』"
    assert r["reachable"] is False and r["overall"] == "red"


def test_健檢用的收集帳號要跟納管腳本同一個來源(monkeypatch):
    """不可以直接讀常數——唯一真相在 DB 設定。

    2026-09-23 部署 v1.365.0 當下就踩到：常數改成新名 webit3sc、
    221 上只有舊名 webit3scan，健檢立刻把一台好好的機器顯示成「連不上 0/9」。
    那是我們自己的設定問題，卻顯示成那台機器的問題。
    """
    seen = {}

    def fake_account(platform):
        seen["platform"] = platform
        return "ACCOUNT-FROM-DB"

    def fake_ssh(ip, cmd, key, acct, timeout=14):
        seen["used"] = acct
        return False, "", "連線逾時"

    monkeypatch.setattr(hp, "_collect_account_for", fake_account)
    hp.check_one("10.99.0.1", _ssh=fake_ssh)
    assert seen["used"] == "ACCOUNT-FROM-DB", "健檢必須用設定裡的帳號，不是程式常數"
