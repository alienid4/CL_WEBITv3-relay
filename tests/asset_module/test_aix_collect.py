"""B-24：AIX 收集器。使用者 2026-09-22：「軟體 0／服務 0／帳號 0、硬體全空」。

這一組測試鎖的是**同一個病根**：平台判定只要判錯一次，後面每一個收集器都會
拿 Linux 的指令去問 AIX，然後安靜地回 0。而 0 在畫面上跟「這台真的很乾淨」
長得一模一樣——那是這套系統最不能犯的錯。

所以這裡不只測「AIX 收得到」，也測「收不到的時候有沒有講原因」。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402


# ═══ 平台判定：只能有一份定義 ═══════════════════════════════════════

def test_oslevel_格式要認得出是_aix(backend):
    import manage_state

    # 納管成功之後 os 欄位寫的是 `oslevel -s` 的輸出，裡面**沒有 aix 三個字**。
    # 這正是 2026-09-22 的病根：納管修好反而讓平台判定壞掉。
    assert manage_state.platform_from_os("7200-05-09-2446") == "aix"
    assert manage_state.platform_from_os("AIX 7.2") == "aix"
    assert manage_state.platform_from_os("Red Hat Enterprise Linux 9.4") == "linux"
    # 位數不對的不要亂認，判不出來要回 None（寧可交給下一層，不要猜）
    assert manage_state.platform_from_os("7200-05-09-244") is None


def test_收集器不准各寫一份平台判定(backend):
    """service／account／auto_onboard 三處手抄本必須都改成呼叫正典。

    它們原本各自「查最近一次掃描的開放埠 -> 猜平台」，完全不看 os 欄位；
    AIX 只開 22，於是三處一起判成 linux。
    """
    import inspect

    import account_inventory
    import auto_onboard
    import service_inventory

    for fn in (service_inventory._platform_for,
               account_inventory._platform_for,
               auto_onboard._platform_of):
        src = inspect.getsource(fn)
        assert "collect_platform_for" in src, f"{fn.__qualname__} 又自己判平台了"
        assert "scan_history" not in src, f"{fn.__qualname__} 還在只看開放埠"


# ═══ 服務：AIX 的 netstat 跟 Linux 不是同一種格式 ═══════════════════

AIX_NETSTAT = """f1000e0001d0b3b8 tcp4       0      0  *.22               *.*                LISTEN
f1000e0002a1c000 tcp        0      0  127.0.0.1.32768    *.*                LISTEN
f1000e0003b2d111 tcp6       0      0  ::1.25             *.*                LISTEN
f1000e0004c3e222 tcp4       0      0  10.0.0.16.1521     10.0.0.20.5566     ESTABLISHED"""


def test_aix_的埠號是點分隔不是冒號():
    import service_collector

    assert service_collector.split_addr("*.22") == ("*", 22)
    assert service_collector.split_addr("127.0.0.1.32768") == ("127.0.0.1", 32768)
    # IPv6：位址裡本來就有冒號，埠號還是在最後一個點後面
    assert service_collector.split_addr("::1.25") == ("::1", 25)
    # Linux 的冒號格式不可以被改壞
    assert service_collector.split_addr("0.0.0.0:22") == ("0.0.0.0", 22)
    assert service_collector.split_addr("[::]:80") == ("::", 80)


def test_aix_netstat_解析得出監聽埠():
    import service_collector

    rows = service_collector.parse_listen(AIX_NETSTAT)
    ports = sorted(r["port"] for r in rows)
    assert ports == [22, 25, 32768], "AIX 的 PCB 開頭格式沒被解析 -> 服務數會是 0"
    # ESTABLISHED 不是監聽，不可以混進來（那會讓服務數虛胖）
    assert 1521 not in ports
    # AIX 的 netstat 沒有 -p，拿不到行程名。留 None，不准拿埠號猜的東西填進去
    assert all(r["process"] is None for r in rows)


def test_linux_的兩種格式不可以被改壞():
    import service_collector

    ss = ('LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))')
    assert service_collector.parse_listen(ss) == [
        {"bind": "0.0.0.0", "port": 22, "proto": "tcp", "process": "sshd"}]
    netstat = "tcp 0 0 0.0.0.0:3306 0.0.0.0:* LISTEN 999/mysqld"
    assert service_collector.parse_listen(netstat)[0]["process"] == "mysqld"


def test_lssrc_只取有在跑的子系統():
    import service_collector

    out = service_collector.parse_units(
        "Subsystem         Group            PID          Status\n"
        " sshd             ssh              7078050      active\n"
        " sendmail         mail                          inoperative\n")
    assert out == ["sshd"]


# ═══ 軟體：AIX 有 fileset，不是只有 rpm ═════════════════════════════

def test_aix_走_lslpp_不是_rpm():
    import software_collector

    seen = {}

    def runner(host, cmd):
        seen["cmd"] = cmd
        return ("SRC=lslpp\n"
                "PKG\tbos.rte\t7.2.5.0\t\t\tbos\n"
                "PKG\topenssh.base.client\t8.1.102.2100\t\t\topenssh.base\n")

    r = software_collector.collect(runner, "h", "aix")
    assert "lslpp" in seen["cmd"], "AIX 還在被問 rpm"
    assert "command -v" not in seen["cmd"], "ksh88 沒有 command -v，要用 whence"
    assert r["source"] == "lslpp"
    assert [p["name"] for p in r["packages"]] == ["bos.rte", "openssh.base.client"]


def test_認不得的平台要講認不得_不可以拿linux指令碰運氣():
    import software_collector

    r = software_collector.collect(lambda h, c: "", "h", "openvms")
    assert r["packages"] == []
    assert r["source"] is None
    assert "openvms" in r["error"]


# ═══ 帳號：AIX 沒有 lastlog／chage／多半也沒有 sudo ═════════════════

AIX_FAKE = {
    "passwd": ("root:!:0:0::/:/usr/bin/ksh\n"
               "daemon:!:1:1::/etc:\n"
               "appuser:!:205:1:Wang Hsiao-ming:/home/appuser:/usr/bin/ksh"),
    "group": "system:!:0:root\nstaff:!:1:appuser\nsecurity:!:7:root",
    "os": ("OSID=aix\nOSVER=7200-05-09-2446\nOSLIKE=aix\nUIDMIN=200\n"
           "BIN=lsuser:/usr/sbin/lsuser\nBIN=sudo:"),
    "lastlog": "SRC=lsuser\nroot time_last_login=1758000000\nappuser ",
    "shadow_status": ("SRC=lsuser\n"
                      "root account_locked=false maxage=13 lastupdate=1750000000\n"
                      "appuser account_locked=true maxage=0"),
    "sudoers": "",
    "authkeys": "",
    "helper": "",
}


def _aix_runner():
    import account_collector

    rev = {v: k for k, v in account_collector.AIX_CMDS.items() if v}

    def runner(host, cmd):
        return AIX_FAKE[rev[cmd]]

    return runner


def test_aix_收得到帳號():
    import account_collector

    r = account_collector.collect(_aix_runner(), "h", "aix")
    assert r["platform"] == "aix"
    names = [a["username"] for a in r["accounts"]]
    assert names == ["root", "daemon", "appuser"]


def test_aix_的真人門檻是200不是1000():
    """AIX 沒有 /etc/login.defs，mkuser 從 200 起編。

    沿用 Linux 的 1000，UID 200~999 的真人會整批被判成服務帳號——
    那正是稽核最該看的一群人。
    """
    import account_collector

    r = account_collector.collect(_aix_runner(), "h", "aix")
    appuser = next(a for a in r["accounts"] if a["username"] == "appuser")
    assert appuser["uid"] == 205
    assert appuser["kind"] == "human"


def test_aix_的特權群組是system_security_不是wheel():
    import account_collector

    r = account_collector.collect(_aix_runner(), "h", "aix")
    root = next(a for a in r["accounts"] if a["username"] == "root")
    assert "system" in (root["priv_groups"] or "")
    assert "security" in (root["priv_groups"] or "")


def test_aix_的maxage是週要換算成天():
    """不換算的話畫面上每台 AIX 都像「13 天就要改密碼」，那是假警報。"""
    import account_collector

    r = account_collector.collect(_aix_runner(), "h", "aix")
    root = next(a for a in r["accounts"] if a["username"] == "root")
    assert root["pw_max_days"] == 91          # 13 週
    appuser = next(a for a in r["accounts"] if a["username"] == "appuser")
    assert appuser["pw_status"] == "locked"
    assert appuser["pw_max_days"] is None     # maxage=0 ＝不到期，不是 0 天


def test_aix_查不到登入時間不可以講成從未登入():
    """AIX 的 lastlog 要 root 才讀得到。

    lsuser 拿不到 -> 那是「不知道」，不是「從未登入」。
    這兩個在稽核上的意義完全相反：後者會讓人去砍一個其實天天在用的帳號。
    """
    import account_collector

    r = account_collector.collect(_aix_runner(), "h", "aix")
    assert r["login_source"] == "lsuser"
    assert all(a["never_logged_in"] is False for a in r["accounts"])
    appuser = next(a for a in r["accounts"] if a["username"] == "appuser")
    assert appuser["last_login"] is None


def test_aix_只讀到一部分也要標需要root():
    """非 root 讀得到自己那一份 authorized_keys。

    收到 1 筆不等於查完 300 個帳號——不看涵蓋率的話畫面會顯示「已查完」。
    """
    import account_collector

    fake = dict(AIX_FAKE, authkeys="KEYS root 2")
    rev = {v: k for k, v in account_collector.AIX_CMDS.items() if v}
    r = account_collector.collect(lambda h, c: fake[rev[c]], "h", "aix")
    assert "authorized_keys" in r["needs_root"], "只收到一筆就當查完了"


def test_aix_指令必須唯讀且是內建():
    """使用者的三條硬限制：不裝軟體、不影響效能、不變更任何設定。"""
    import re

    import account_collector
    import service_collector
    import software_collector

    # 只認「出現在指令位置」的寫入型指令：`cat /etc/passwd` 裡的 passwd 是路徑，
    # 用純子字串比對會誤判（第一版就踩到了）。
    寫入型 = ("chuser", "mkuser", "rmuser", "chsec", "pwdadm", "passwd", "chmod",
              "chown", "startsrc", "stopsrc", "refresh", "installp", "yum", "dnf",
              "mkdir", "touch", "rm")
    位於指令位置 = re.compile(
        r"(?:^|[;|&(]|then|do|`|\$\()\s*(?:sudo\s+-n\s+)?(%s)"
        % "|".join(寫入型))
    cmds = list(account_collector.AIX_CMDS.values())
    cmds += list(service_collector.AIX_CMDS.values())
    cmds.append(software_collector.AIX_CMD)
    for c in cmds:
        m = 位於指令位置.search(c)
        assert m is None, f"AIX 指令有寫入/變更疑慮：{m.group(1)} in {c[:70]}"
        # 重導向只准倒進 /dev/null。倒進檔案就是在目標機留下東西（使用者：不能變更）。
        # 注意 awk 的 `NF >= 3` 也長得像重導向，所以要看 `>` 後面接的是什麼。
        寫檔 = re.search(r">>|>\s*(?!&|/dev/null)[/\w$]", c)
        assert 寫檔 is None, f"AIX 指令有寫檔疑慮：{c[:70]}"


def test_aix_指令不可以用ksh88沒有的語法():
    """AIX 預設 shell 是 ksh88：沒有 `command -v`、沒有 `if ! cmd`。"""
    import account_collector
    import service_collector
    import software_collector

    cmds = (list(account_collector.AIX_CMDS.values())
            + list(service_collector.AIX_CMDS.values())
            + [software_collector.AIX_CMD])
    for c in cmds:
        assert "command -v" not in c, f"ksh88 沒有 command -v，要用 whence：{c[:60]}"
        assert "if ! " not in c, f"ksh88 不支援 if ! cmd：{c[:60]}"


# ═══ 跳過要講原因，不可以只顯示 0 ═══════════════════════════════════

def test_跳過的機器要留下原因(backend):
    """使用者的規則：收不到要講原因，不可以只顯示 0。

    原本這裡是一句 `continue`，什麼都不留——畫面上「沒去收」跟「真的沒帳號」
    長得一模一樣。
    """
    import account_inventory

    conn = backend
    db.insert_hardware(conn, asset_serial="WIN-TEST-1", hostname="win01",
                       ip="10.0.0.99", os="Microsoft Windows Server 2019")
    conn.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = ?",
                 ("WIN-TEST-1",))
    conn.commit()

    r = account_inventory.collect_accounts(conn, runner=lambda h, c: "",
                                           only_serial="WIN-TEST-1")
    assert r["accounts"] == 0
    assert len(r["skipped"]) == 1, "跳過了卻沒留下任何理由"
    assert "AD" in r["skipped"][0]["reason"]


# ═══ 硬體：HBA／網卡／Storage ═══════════════════════════════════════

AIX_HW = {
    "lsdev": ("ent0 Available 00-00 2-Port 10 Gigabit Ethernet Adapter\n"
              "ent1 Available 00-01 2-Port 10 Gigabit Ethernet Adapter\n"
              "fcs0 Available 01-00 8Gb PCI Express Dual Port FC Adapter\n"
              "sissas0 Available 00-50 PCIe2 SAS Adapter"),
    "netstat": ("Name  Mtu   Network     Address            Ipkts Ierrs\n"
                "en0   1500  link#2      fa.ce.0.1.2.3      100 0\n"
                "en0   1500  10.0.0      10.0.0.16          100 0"),
    "ifconfig": ("en0: flags=1e080863,480<UP,BROADCAST,NOTRAILERS,RUNNING>\n"
                 "        inet 10.0.0.16 netmask 0xffffff00 broadcast 10.0.0.255\n"
                 "en1: flags=1e080862<BROADCAST,NOTRAILERS>"),
    "route": "Route Tree\ndefault  10.0.0.254  UG  0 0 en0",
    "entinfo": ("@@ENT ent0\nmedia_speed 10000_Full_Duplex Media Speed True\n"
                "  ent0  U78CA.001.X-P1-C2-T1  2-Port 10 Gigabit Ethernet Adapter\n"
                "@@ENT ent1\nmedia_speed Auto_Negotiation Media Speed True"),
    "fcsinfo": ("@@FCS fcs0\n"
                "  fcs0  U78CA.001.X-P1-C3-T1  8Gb Dual Port Fibre Channel Adapter\n"
                "        Network Address.............10000000C9AB1234\n"
                "        Device Specific.(Z8)........20000000C9AB1234"),
    "entstat": "",
    "fcstat": "",
}


def test_aix網卡的mac要補零():
    """AIX 的 MAC 不補零（`fa.ce.0.1.2.3`）。

    照原樣存的話，同一張卡在掃描／RVTools 那邊收到的 `FA:CE:00:01:02:03`
    會被當成另一張卡，機器比對就對不起來。
    """
    import net_collector

    assert net_collector._aix_mac("fa.ce.0.1.2.3") == "fa:ce:00:01:02:03"
    assert net_collector._aix_mac("not a mac") is None


def test_aix網卡收得到ip遮罩與閘道():
    import net_collector

    nics, _, _ = net_collector._collect_aix_net(AIX_HW)
    ent0 = next(n for n in nics if n["nic_name"] == "ent0")
    assert ent0["mac"] == "fa:ce:00:01:02:03"
    assert ent0["ip"] == "10.0.0.16"
    assert ent0["subnet"] == "/24"          # AIX 的 netmask 是 0xffffff00
    assert ent0["gateway"] == "10.0.0.254"
    assert ent0["speed_max"] == 10000
    assert ent0["state"] == "up"


def test_額定速率有收到也不可以拿來當目前速率():
    """目前速率要 root（entstat）。

    拿額定值頂替的話，「10G 卡實際跑 1G」這種最該被看到的事永遠不會亮燈。
    """
    import net_collector

    nics, _, notes = net_collector._collect_aix_net(AIX_HW)
    ent0 = next(n for n in nics if n["nic_name"] == "ent0")
    assert ent0["speed_cur"] is None
    assert ent0["speed_max"] == 10000
    assert "root" in notes["nic_speed"]


def test_aix_hba_收得到wwpn():
    import net_collector

    _, hbas, _ = net_collector._collect_aix_net(AIX_HW)
    assert len(hbas) == 1
    assert hbas[0]["wwpn"] == "10:00:00:00:c9:ab:12:34"
    assert hbas[0]["wwnn"] == "20:00:00:00:c9:ab:12:34"


def test_沒有fc跟問不到是兩句不同的話():
    """這是整組測試最重要的一條。

    `lsdev` 列得出卡但沒有 fcs -> **可以**說「這台沒有 FC HBA」（有依據）
    `lsdev` 什麼都列不出來     -> **不可以**說沒有，那是連線／權限問題
    兩句話混成同一句，使用者會以為那台機器很乾淨。
    """
    import net_collector

    沒有fc = dict(AIX_HW, lsdev="ent0 Available 00-00 Virtual I/O Ethernet Adapter",
                  fcsinfo="")
    _, hbas, notes = net_collector._collect_aix_net(沒有fc)
    assert hbas == []
    assert "沒有 FC HBA" in notes["hbas"]

    問不到 = dict(AIX_HW, lsdev="")
    nics, hbas, notes = net_collector._collect_aix_net(問不到)
    assert nics == [] and hbas == []
    assert "不是這台沒有網卡" in notes["hbas"]
    assert "沒有 FC HBA" not in notes["hbas"], "問不到卻講成『沒有』"


def test_有卡卻拿不到wwpn要講原因():
    """沒有 WWPN 就沒辦法跟 SAN 交換器的 zone 對起來——這件事不能默默留白。"""
    import net_collector

    _, hbas, notes = net_collector._collect_aix_net(dict(AIX_HW, fcsinfo=""))
    assert hbas and hbas[0]["wwpn"] is None
    assert "fcs0" in notes["hba_wwpn"]


def test_aix磁碟要看得出哪顆沒掛進vg():
    """買了容量沒在用，是要有人知道的事，不是雜訊。"""
    import host_spec_collector

    out = host_spec_collector.parse_aix_pv_detail(
        "@@PV hdisk0\nVOLUME GROUP:     rootvg\n"
        "TOTAL PPs:      799 (204544 megabytes)\nFREE PPs:  100 (25600 megabytes)\n"
        "@@PV hdisk1\nVOLUME GROUP:     None\nTOTAL PPs:  399 (102144 megabytes)")
    assert out[0] == {"name": "hdisk0", "vg": "rootvg", "size_gb": 200, "free_gb": 25}
    assert out[1]["vg"] is None


def test_prtconf拿不到序號要退回odm():
    import host_spec_collector

    got = host_spec_collector.parse_aix_sys0(
        "systemid IBM,0212ABCDE Hardware system identifier False\n"
        "modelname IBM,9009-42A Machine name False")
    assert got == {"serial": "IBM,0212ABCDE", "model": "IBM,9009-42A"}


def test_aix硬體指令也必須唯讀():
    import re

    import host_spec_collector
    import net_collector

    寫入型 = ("chdev", "mkdev", "rmdev", "cfgmgr", "chvg", "mkvg", "extendvg",
              "reducevg", "mklv", "rmlv", "chfs", "mkfs", "rm", "mv", "cp")
    位於指令位置 = re.compile(
        r"(?:^|[;|&(]|\bthen\b|\bdo\b|`|\$\()\s*(%s)\b" % "|".join(寫入型))
    for c in list(net_collector.CMD_AIX.values()) + list(host_spec_collector.CMD_AIX.values()):
        m = 位於指令位置.search(c)
        assert m is None, f"AIX 硬體指令有變更疑慮：{m.group(1)} in {c[:70]}"


@pytest.fixture()
def backend(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        yield c
    finally:
        c.close()
