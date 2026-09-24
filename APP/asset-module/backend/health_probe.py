"""主機自我檢查（值班即時健檢）。

值班情境（2026-09-21 使用者）：WhatsUp Gold 對某台發告警（當機／服務異常），
值班的人不知道是誤報還是真出事。輸入 IP → 這裡對那台**唯讀**跑一輪、一次掃過
一台 Linux 主機該看的面向，產出可回報的證據。

## 檢查面向（專業健檢，不是只看四樣）
- 可達性：連得上？剛重開機（uptime<10 分＝可能剛當過）？OS／kernel。
- CPU：load÷核數、使用率、**iowait（磁碟瓶頸的關鍵指標）**、吃最兇前 5。
- 記憶體：用量、**swap**、吃最兇前 5。
- 磁碟：每掛載空間%、**inode%（空間夠但 inode 滿照樣當）**、**唯讀重掛（磁碟故障徵兆）**。
- 網路：介面丟包／錯誤、TCP established 數。
- 服務：**systemctl --failed（掛掉的服務直接列）**、監聽 port＋服務、殭屍進程、進程數。
- 時間：NTP 同步（時間飄會連鎖出怪事）。

## 判定：多訊號、絕對閾值
單一動態值不亂判；絕對閾值不依賴 baseline（baseline 比較是批次 2）。
連不上、唯讀重掛、有 failed unit＝紅（這些幾乎一定是真問題）。

## 權限與界線（批次 1：免提權）
- 以上**全部免提權**（df／/proc／ss／systemctl --failed／ps／timedatectl 一般帳號就收得到）。
- 讀 log（dmesg 的 OOM／I/O error、journalctl -p err）要 root／sudo，是**批次 3**，本檔不碰。
- 走既有收集管道：webit3scan ＋ collector 金鑰、系統 ssh（同 service_collector），非 paramiko。
- 只打已知目標；並發上限見 api；只支援 Linux（非 Linux 回 skipped，不假裝檢查過）。
"""
from __future__ import annotations

import datetime as _dt
import re
import subprocess

import manage_state as ms

# ===== 判定門檻（絕對閾值，不依賴 baseline）=====
DISK_RED, DISK_YELLOW = 90, 80          # 空間使用率 %
INODE_RED, INODE_YELLOW = 90, 80        # inode 使用率 %
MEM_RED, MEM_YELLOW = 90, 80            # 記憶體使用率 %
SWAP_RED, SWAP_YELLOW = 80, 50          # swap 使用率 %（一直吃 swap＝記憶體壓力）
LOAD_RED, LOAD_YELLOW = 1.0, 0.7        # load1 ÷ 核數
IOWAIT_RED, IOWAIT_YELLOW = 50, 25      # CPU 花在等 I/O 的 %（磁碟塞住）
CPU_RED, CPU_YELLOW = 95, 85            # CPU 總使用率 %
UPTIME_FRESH_MIN = 10                   # 開機不到這麼久＝剛重開（可能剛當過），標黃
ZOMBIE_YELLOW = 10                      # 殭屍進程數
NETDROP_YELLOW = 1000                   # 介面累計丟包／錯誤（大量才提示）

# 掛載點雜訊：暫存／虛擬檔案系統滿不滿沒有維運意義，排掉不然一堆假黃燈。
_DF_X = "-x tmpfs -x devtmpfs -x overlay -x squashfs -x aufs -x iso9660"

# 一次 SSH 收完所有指標（減少往返）。marker 切段，解析端各認自己那段。
# CPU 使用率／iowait 要兩次取樣算差，中間 sleep 1。
_METRICS_CMD = (
    "echo @@OS; uname -sr 2>/dev/null; grep PRETTY_NAME /etc/os-release 2>/dev/null; "
    "echo @@UP; cat /proc/uptime 2>/dev/null; "
    "echo @@LOAD; cat /proc/loadavg 2>/dev/null; "
    "echo @@CPU; nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null "
    "|| grep -c ^processor /proc/cpuinfo 2>/dev/null; "
    "echo @@STAT1; grep '^cpu ' /proc/stat 2>/dev/null; sleep 1; "
    "echo @@STAT2; grep '^cpu ' /proc/stat 2>/dev/null; "
    "echo @@MEM; cat /proc/meminfo 2>/dev/null; "
    "echo @@DF; df -P -k " + _DF_X + " 2>/dev/null; "
    "echo @@DFI; df -P -i " + _DF_X + " 2>/dev/null; "
    "echo @@MOUNTS; cat /proc/mounts 2>/dev/null; "
    "echo @@FAILED; systemctl --failed --no-legend --plain 2>/dev/null; "
    # 失敗的 unit 再補問三件事，因為「failed」對兩種 unit 的意思完全不同：
    #   Type=oneshot（多半由 timer 觸發的批次）→ 是「上一次那批沒跑成」
    #   Type=simple/notify（常駐服務）        → 是「服務現在是掛的」
    # 混在一起講，值班會把「昨晚批次失敗」當成「服務死了」而去重啟，
    # 或反過來把服務死掉當成批次沒跑而放著。
    "echo @@FAILEDINFO; for u in $(systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}'); do echo \"##$u\"; systemctl show \"$u\" -p Type -p Result -p ExecMainStatus -p TriggeredBy -p ActiveEnterTimestamp --no-pager 2>/dev/null; done; "
    "echo @@ZOMB; ps -eo stat= 2>/dev/null | grep -c '^Z'; "
    "echo @@TCPSTATES; ss -tan 2>/dev/null | awk 'NR>1{print $1}' | sort | uniq -c; "
    "echo @@NETDEV; cat /proc/net/dev 2>/dev/null; "
    # 磁碟忙碌度：取兩次 /proc/diskstats，中間隔 1 秒，自己算增量。
    # **不用 iostat**——那要裝 sysstat（使用者 2026-09-22：「不要裝軟體，
    # 指令都用內建的」）。/proc/diskstats 是核心介面，任何 Linux 都有，
    # 不需要任何套件，也不需要提權。
    "echo @@DISKSTAT1; cat /proc/diskstats 2>/dev/null; sleep 1; "
    "echo @@DISKSTAT2; cat /proc/diskstats 2>/dev/null; "
    "echo @@DNS; (getent hosts github.com >/dev/null 2>&1 && echo ok || echo fail); "
    "echo @@NTP; timedatectl show -p NTPSynchronized --value 2>/dev/null; "
    "echo @@WHO; who 2>/dev/null | wc -l; "
    "echo @@LAST; last -n 6 -w 2>/dev/null | head -6; "
    "echo @@TOPCPU; ps -eo pid,comm,pcpu,pmem --sort=-pcpu 2>/dev/null | head -6; "
    "echo @@TOPMEM; ps -eo pid,comm,pcpu,pmem --sort=-pmem 2>/dev/null | head -6; "
    # 批次 3：需要 sudo 的兩項唯讀日誌。使用者 2026-09-22：「要 root，就 sudo 給權限」。
    #
    # `sudo -n` 不會卡在密碼提示；**沒授權時要看得出來是「沒授權」而不是「沒問題」**，
    # 所以把 stderr 一起收（sudo 會寫 "a password is required" 或 "not allowed"），
    # 由 parse_sudo_log 判成「尚未開通」。靜默跳過會變成假的安全感。
    # **stderr 丟掉、離開碼另外印**。
    # 2>&1 會把 sudo 的錯誤訊息混進資料裡：221 是中文 locale，
    # 「sudo: 需要密碼」曾被當成一筆核心錯誤算進統計（2026-09-22）。
    # 判「有沒有授權」只看 __RC__，不看訊息文字——訊息會隨語言與版本變，離開碼不會。
    # **兩個檔都試，不猜發行版**：/var/log/secure 是 RHEL 系專有，Debian／Ubuntu
    # 是 /var/log/auth.log。判錯發行版會安靜地讀錯檔案；而「哪個檔讀得到」是事實。
    # 兩段都收，由 parser 挑讀得到的那一個，並把**實際讀到哪一個檔**寫進 note——
    # 數字可以是 0，出處不可以寫錯。
    "echo @@SECURELOG; sudo -n /usr/bin/tail -n 200 /var/log/secure 2>/dev/null; echo __RC__=$?; "
    "echo @@AUTHLOG; sudo -n /usr/bin/tail -n 200 /var/log/auth.log 2>/dev/null; echo __RC__=$?; "
    "echo @@DMESGERR; sudo -n /usr/bin/dmesg --level=err,crit 2>/dev/null; echo __RC__=$?; "
    # 監聽 port 的行程名：ss -tlnp 的 -p 要 root。有 sudo 白名單就拿得到真實服務名，
    # 沒授權（__RC__!=0）就維持免提權那份（只有 port 號）。2026-09-22 使用者核准 sudo。
    "echo @@LISTENP; sudo -n ss -tlnp 2>/dev/null; echo __RC__=$?; "
    # port 的**服務身分**（使用者 2026-09-23：「port 3000 能顯示 webit3 服務嗎，光 python 沒用」）。
    # 行程名只講「用什麼寫的」——node／python／gunicorn 在一台機器上會重複好幾個，
    # 值班看到 3000 是 node 仍然不知道那是哪一套系統。真正的身分在 systemd unit：
    # /proc/<pid>/cgroup 的最後一段就是 webit3-web.service。
    #
    # 不需要新權限：cgroup 與 cmdline 都是 0444，收集帳號本來就讀得到（221 實測）。
    # 用的 sudo 只有既有白名單那條 `ss -tlnp`——**參數必須一字不差**，
    # 寫成 `-tlnpH`（省掉表頭）會被 sudoers 當成另一條指令而**靜默拒絕**，
    # 什麼都收不到也不報錯（2026-09-23 在 221 踩到）。所以照收表頭再跳過。
    "echo @@PORTUNIT; sudo -n ss -tlnp 2>/dev/null | while read -r line; do "
    "case \"$line\" in State*) continue;; esac; "
    "addr=$(echo \"$line\" | tr -s ' ' | cut -d' ' -f4); port=${addr##*:}; "
    "pid=$(echo \"$line\" | sed -n 's/.*pid=\\([0-9]*\\).*/\\1/p'); "
    "[ -n \"$pid\" ] || continue; "
    "unit=$(sed -n 's|.*/||;s|\\.service$||p' /proc/$pid/cgroup 2>/dev/null | tail -1); "
    "cmd=$(tr '\\0' ' ' < /proc/$pid/cmdline 2>/dev/null | cut -c1-100); "
    "echo \"$port|$unit|$cmd\"; done | sort -u; echo __RC__=$?; "
    "echo @@END"
)

# TCP 狀態判定門檻（維度 3）
CLOSE_WAIT_YELLOW = 100                  # 連線沒關乾淨堆積（多半是應用忘了 close）
SYN_RECV_YELLOW = 50                    # 半開連線堆積（可能 SYN flood 或 backlog 滿）
#: 會參與「總燈號」計算的指標。每一個的 parser **都必須回 level**。
#: 抽成常數是為了讓測試能 import 它逐一檢查——2026-09-22 就是因為
#: 這串寫死在迴圈裡，測試沒辦法跟著同步，io 漏了 level 才沒被擋下來。
LEVELLED_KEYS = ("cpu", "load", "mem", "uptime", "net", "tcp", "io")

#: 需要 sudo 才查得到的指標。**拿不到時是 None，不納入總燈號**——
#: 把「沒查到」算成綠色就是假的安全感。它們沒授權時由 notes 講明。
SUDO_LEVELLED_KEYS = ("ssh_fails", "kernel_errors")

#: SSH 登入失敗次數門檻（取樣自登入紀錄最後 200 行）
SSHFAIL_RED, SSHFAIL_YELLOW = 50, 10

IOUTIL_RED, IOUTIL_YELLOW = 90, 70      # 磁碟忙碌度 %util（由 /proc/diskstats 增量算）

_ORDER = {"green": 0, "yellow": 1, "red": 2}


def _worst(*levels: str) -> str:
    return max((l for l in levels if l), key=lambda x: _ORDER.get(x, 0), default="green")


def _lv(v: float, red: float, yellow: float) -> str:
    return "red" if v >= red else "yellow" if v >= yellow else "green"


def _run_ssh(ip: str, cmd: str, key_path: str, account: str, timeout: int = 14):
    """跑一條唯讀指令，回 (ok, stdout, err)。多回 returncode／stderr，才分得出
    「連不上」（紅色證據）與「連得上但有問題」。"""
    try:
        r = subprocess.run(
            ["ssh", "-i", key_path, "-o", "BatchMode=yes", *ms._ssh_hostkey_opts(),
             "-o", f"ConnectTimeout={timeout}", f"{account}@{ip}", cmd],
            capture_output=True, text=True, timeout=timeout + 12,
        )
        return r.returncode == 0, r.stdout, (r.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return False, "", "連線逾時（機器沒回應／被防火牆擋／已當機）"
    except OSError as exc:
        return False, "", f"無法執行 ssh：{exc}"


def _split_markers(out: str) -> dict:
    seg: dict[str, list[str]] = {}
    cur = None
    for line in out.splitlines():
        if line.startswith("@@"):
            cur = line[2:].strip()
            seg[cur] = []
        elif cur is not None:
            seg[cur].append(line)
    return {k: "\n".join(v) for k, v in seg.items()}


# ---------- 各面向解析 ----------
def parse_os(text: str) -> dict:
    kern = ""
    pretty = ""
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME"):
            pretty = line.split("=", 1)[1].strip().strip('"')
        elif line.strip():
            kern = line.strip()
    return {"kernel": kern, "os": pretty}


def parse_uptime(text: str) -> dict | None:
    parts = text.split()
    if not parts:
        return None
    try:
        secs = float(parts[0])
    except ValueError:
        return None
    fresh = secs < UPTIME_FRESH_MIN * 60
    return {"uptime_sec": int(secs), "days": round(secs / 86400, 1),
            "just_rebooted": fresh, "level": "yellow" if fresh else "green"}


def parse_cpu(stat1: str, stat2: str, ncpu_text: str) -> dict | None:
    def _row(t):
        p = t.split()
        return [int(x) for x in p[1:]] if len(p) > 4 and p[0] == "cpu" else None
    a, b = _row(stat1), _row(stat2)
    if not a or not b:
        return None
    da = [y - x for x, y in zip(a, b)]
    total = sum(da)
    if total <= 0:
        return None
    idle = da[3] + (da[4] if len(da) > 4 else 0)         # idle + iowait
    iowait = da[4] if len(da) > 4 else 0
    usage = round(100 * (total - idle) / total, 1)
    iowait_pct = round(100 * iowait / total, 1)
    ncpu = next((int(t) for t in ncpu_text.split() if t.isdigit()), 1) or 1
    return {"usage_pct": usage, "iowait_pct": iowait_pct, "ncpu": ncpu,
            "level": _worst(_lv(usage, CPU_RED, CPU_YELLOW),
                            _lv(iowait_pct, IOWAIT_RED, IOWAIT_YELLOW))}


def parse_load(loadavg: str, ncpu: int) -> dict | None:
    parts = loadavg.split()
    if len(parts) < 3:
        return None
    try:
        l1, l5, l15 = float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None
    proc_total = 0
    if len(parts) >= 4 and "/" in parts[3]:
        proc_total = int(parts[3].split("/")[1])
    ncpu = ncpu or 1
    per = round(l1 / ncpu, 2)
    return {"load1": l1, "load5": l5, "load15": l15, "ncpu": ncpu, "per_cpu": per,
            "proc_total": proc_total, "level": _lv(per, LOAD_RED, LOAD_YELLOW)}


def parse_mem(meminfo: str) -> dict | None:
    vals = {}
    for line in meminfo.splitlines():
        k, _, rest = line.partition(":")
        num = rest.strip().split()
        if num and num[0].isdigit():
            vals[k.strip()] = int(num[0])          # KB
    total = vals.get("MemTotal")
    if not total:
        return None
    avail = vals.get("MemAvailable")
    if avail is None:
        avail = vals.get("MemFree", 0) + vals.get("Buffers", 0) + vals.get("Cached", 0)
    used_pct = round((total - avail) / total * 100, 1)
    swap_total = vals.get("SwapTotal", 0)
    swap_free = vals.get("SwapFree", 0)
    swap_pct = round((swap_total - swap_free) / swap_total * 100, 1) if swap_total else 0.0
    return {"total_kb": total, "avail_kb": avail, "used_pct": used_pct,
            "swap_total_kb": swap_total, "swap_pct": swap_pct,
            "level": _worst(_lv(used_pct, MEM_RED, MEM_YELLOW),
                            _lv(swap_pct, SWAP_RED, SWAP_YELLOW) if swap_total else "green")}


def _parse_df(text: str, pct_idx_from_end: int = 1):
    """df -P 通用解析：回 {mount: (…, pct)}。空白掛載點併回。"""
    out = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) < 6 or p[0].lower() == "filesystem":
            continue
        try:
            pct = int(p[-2].rstrip("%"))
        except ValueError:
            continue
        mount = " ".join(p[5:])
        out[mount] = (p, pct)
    return out


def parse_disks(df_text: str, dfi_text: str) -> list[dict]:
    space = _parse_df(df_text)
    inode = _parse_df(dfi_text)
    disks = []
    for mount, (p, pct) in space.items():
        try:
            size_kb, used_kb, avail_kb = int(p[1]), int(p[2]), int(p[3])
        except (ValueError, IndexError):
            size_kb = used_kb = avail_kb = 0
        ipct = inode.get(mount, (None, -1))[1]
        disks.append({
            "mount": mount, "fs": p[0], "size_kb": size_kb, "used_kb": used_kb,
            "avail_kb": avail_kb, "use_pct": pct, "inode_pct": ipct,
            "level": _worst(_lv(pct, DISK_RED, DISK_YELLOW),
                            _lv(ipct, INODE_RED, INODE_YELLOW) if ipct >= 0 else "green"),
        })
    disks.sort(key=lambda d: -max(d["use_pct"], d["inode_pct"]))
    return disks


# 這些檔案系統本來就是唯讀（光碟／squashfs／唯讀 bind），不算異常。
_RO_OK_FS = {"squashfs", "iso9660", "cramfs"}
_RO_OK_MOUNT_PREFIX = ("/snap", "/sys", "/proc", "/run")


def parse_readonly_mounts(mounts: str) -> list[dict]:
    """從 /proc/mounts 找**該可寫卻變唯讀**的真實檔案系統——磁碟出錯時 kernel 會把它
    重掛成 ro，這是很強的故障訊號。過濾掉本來就唯讀的（光碟／snap／虛擬檔案系統）。"""
    ro = []
    for line in mounts.splitlines():
        p = line.split()
        if len(p) < 4:
            continue
        dev, mount, fstype, opts = p[0], p[1], p[2], p[3]
        flags = opts.split(",")
        if "ro" not in flags:
            continue
        if fstype in _RO_OK_FS or not dev.startswith("/dev"):
            continue
        if any(mount.startswith(pre) for pre in _RO_OK_MOUNT_PREFIX):
            continue
        ro.append({"mount": mount, "fs": fstype, "dev": dev})
    return ro


def parse_failed_units(text: str) -> list[str]:
    units = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        units.append(line.split()[0])          # 第一欄＝unit 名
    return units


def parse_failed_detail(text: str, units: list[str]) -> list[dict]:
    """每個 failed unit 給：判讀 ＋ 該下哪些指令。

    **只給唯讀的查詢指令當第一組**，重啟類的另外標成「確認原因後再做」。
    這是金融業主機：系統不會替人按重啟，畫面只負責把「該看哪裡」講清楚。
    指令一律純文字可複製，不做成會執行的按鈕——按鈕會讓人跳過判斷。
    """
    info: dict[str, dict] = {}
    cur = None
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("##"):
            cur = line[2:].strip()
            info[cur] = {}
        elif cur and "=" in line:
            k, v = line.split("=", 1)
            info[cur][k.strip()] = v.strip()

    out = []
    for u in units:
        d = info.get(u, {})
        typ = d.get("Type", "")
        result = d.get("Result", "")
        code = d.get("ExecMainStatus", "")
        trig = d.get("TriggeredBy", "")
        oneshot = typ == "oneshot" or bool(trig)
        if oneshot:
            verdict = ("這是「批次型」工作（由 timer 觸發），failed 代表"
                       "「上一次執行沒成功」，不是服務現在掛著。"
                       "要看的是那次為什麼失敗，重啟它只會再跑一次。")
        elif typ:
            verdict = ("這是「常駐服務」，failed 代表它「現在沒在跑」。"
                       "先看日誌確認原因，再決定要不要拉起來。")
        else:
            verdict = "查不到這個 unit 的型態（可能已被移除），先看它的狀態與日誌。"
        why = {"exit-code": "程式自己回傳非 0",
               "timeout": "執行超時被砍",
               "signal": "被訊號終止（常見是 OOM）",
               "core-dump": "程式崩潰",
               "start-limit-hit": "短時間內重啟太多次，systemd 放棄了",
               "resources": "資源不足起不來"}.get(result, "")
        out.append({
            "unit": u,
            "type": typ or None,
            "result": result or None,
            "exit_status": code or None,
            "triggered_by": trig or None,
            "oneshot": oneshot,
            "verdict": verdict,
            "reason_hint": why or None,
            "last_active": d.get("ActiveEnterTimestamp") or None,
            # 先查（唯讀，隨時可跑）
            "inspect": [
                f"systemctl status {u} --no-pager -l",
                f"journalctl -u {u} -n 100 --no-pager",
                f"systemctl cat {u}",
            ] + ([f"systemctl list-timers --all | grep -i {u.split('.')[0]}"] if oneshot else []),
            # 再處理（**確認原因後**才做，畫面上要標清楚）
            "act": ([f"systemctl reset-failed {u}",
                     f"systemctl start {u}   # 手動補跑這一次"]
                    if oneshot else
                    [f"systemctl reset-failed {u}",
                     f"systemctl restart {u}"]),
        })
    return out


def parse_netdev(text: str) -> dict:
    """/proc/net/dev → 累計 rx/tx 錯誤＋丟包（排掉 lo）。回總數＋有問題的介面。"""
    total_err = total_drop = 0
    ifaces = []
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        if name == "lo":
            continue
        f = rest.split()
        if len(f) < 16:
            continue
        # rx: bytes packets errs drop ... (idx2=errs,3=drop) ; tx errs=idx10 drop=idx11
        try:
            rx_err, rx_drop, tx_err, tx_drop = int(f[2]), int(f[3]), int(f[10]), int(f[11])
        except (ValueError, IndexError):
            continue
        err = rx_err + tx_err
        drop = rx_drop + tx_drop
        if err or drop:
            ifaces.append({"iface": name, "errs": err, "drops": drop})
        total_err += err
        total_drop += drop
    return {"total_errs": total_err, "total_drops": total_drop, "ifaces": ifaces,
            "level": "yellow" if (total_err + total_drop) >= NETDROP_YELLOW else "green"}


def parse_top(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        p = line.split(None, 3)
        if len(p) < 4 or p[0].upper() == "PID" or not p[0].isdigit():
            continue
        try:
            rows.append({"pid": int(p[0]), "comm": p[1],
                         "cpu": float(p[2]), "mem": float(p[3])})
        except ValueError:
            continue
    return rows[:5]


def parse_tcp_states(text: str) -> dict:
    """ss -tan 的狀態計數（維度 3）：關注 CLOSE-WAIT／SYN-RECV／TIME-WAIT 堆積。"""
    counts: dict[str, int] = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) == 2 and p[0].isdigit():
            counts[p[1].upper()] = int(p[0])
    cw = counts.get("CLOSE-WAIT", 0)
    sr = counts.get("SYN-RECV", 0)
    lvl = "yellow" if (cw >= CLOSE_WAIT_YELLOW or sr >= SYN_RECV_YELLOW) else "green"
    return {"counts": counts, "estab": counts.get("ESTAB", 0),
            "time_wait": counts.get("TIME-WAIT", 0), "close_wait": cw,
            "syn_recv": sr, "level": lvl}


def parse_diskstats(t1: str, t2: str, secs: float = 1.0) -> dict | None:
    """兩次 /proc/diskstats 的增量 → 最忙裝置的 %util 與平均等待毫秒。

    為什麼不用 iostat：那要裝 sysstat。使用者 2026-09-22：「不要裝軟體，
    指令都用內建的」。`/proc/diskstats` 是核心介面，任何 Linux 都有，
    免安裝、免提權，而且 iostat 本身也是讀它算出來的。

    欄位（Linux 核心 Documentation/admin-guide/iostats.rst）：
      1 major  2 minor  3 name  4 reads  ...  7 read_ms  8 writes  ...
      11 write_ms  12 in_flight  **13 io_ms（花在 I/O 上的毫秒）**  14 weighted_ms

    %util = io_ms 增量 ÷ 經過毫秒 × 100；平均等待 = (read_ms+write_ms) 增量 ÷ 次數增量。

    只看整顆實體碟，**跳過分割區與虛擬裝置**（loop/ram/dm/sr）——
    分割區的 io_ms 會跟母碟重複計算，不跳掉會挑出假的最忙裝置。
    """
    def rows(text):
        out = {}
        for line in (text or "").splitlines():
            f = line.split()
            if len(f) < 14:
                continue
            name = f[2]
            if name.startswith(("loop", "ram", "dm-", "sr", "zram", "md")):
                continue
            if name[-1].isdigit() and not name.startswith("nvme"):
                continue          # sda1 這種分割區；nvme0n1 結尾是數字但那是整顆
            if name.startswith("nvme") and "p" in name.split("n")[-1]:
                continue          # nvme0n1p1 才是分割區
            try:
                out[name] = {"reads": int(f[3]), "rms": int(f[6]), "writes": int(f[7]),
                             "wms": int(f[10]), "io_ms": int(f[12])}
            except (ValueError, IndexError):
                continue
        return out

    a, b = rows(t1), rows(t2)
    if not a or not b:
        return None
    elapsed_ms = max(secs, 0.1) * 1000.0
    worst = None
    for name, cur in b.items():
        prev = a.get(name)
        if not prev:
            continue
        d_io = cur["io_ms"] - prev["io_ms"]
        if d_io < 0:
            continue                              # 計數器重置（重開機）→ 這輪不算
        util = min(100.0, d_io / elapsed_ms * 100.0)
        d_cnt = (cur["reads"] - prev["reads"]) + (cur["writes"] - prev["writes"])
        d_wait = (cur["rms"] - prev["rms"]) + (cur["wms"] - prev["wms"])
        await_ms = round(d_wait / d_cnt, 1) if d_cnt > 0 else None
        if worst is None or util > worst["util"]:
            worst = {"device": name, "util": round(util, 1), "await_ms": await_ms}
    if worst is not None:
        # level 一定要有：check_one 的總燈號會對每個有值的指標取 level。
        # 2026-09-22 把 iostat 換成 diskstats 時漏了這個欄位，
        # 結果 221 上 check_one 直接 KeyError 炸掉——單元測試全綠但一跑就死。
        worst["level"] = ("red" if worst["util"] >= IOUTIL_RED
                          else "yellow" if worst["util"] >= IOUTIL_YELLOW
                          else "green")
    return worst

def parse_last(text: str) -> list[str]:
    """last -n 的近期登入（維度 4）。過濾表尾 wtmp begins 那行。"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("wtmp begins") or s.startswith("btmp begins"):
            continue
        out.append(s)
    return out[:5]


# ═══════════════════════════════════════════════════════════════════
# AIX（2026-09-23）。**不是把 Linux 指令硬套**——AIX 沒有 /proc、沒有 systemd，
# 記憶體會計的行為也跟 Linux 完全不同。
#
# ## 一定要分開的三件事（照抄 Linux 門檻就會整批誤判）
#
# 1. **記憶體使用率不能用 inuse。** AIX 的 VMM 預設會把大量閒置記憶體拿去當
#    檔案快取（numperm 可長到接近 maxperm），所以 `svmon -G` 的 inuse 長期就在
#    90% 以上——**那是正常的**。套 Linux 的 MEM 90/80，八台 AIX 會全部紅燈。
#    要看的是 computational（`virtual`，程式真正吃的），不是 inuse。
# 2. **`lssrc` 的 inoperative 不等於 systemd 的 failed。**
#    failed ＝「試著起來但失敗了」；inoperative ＝「現在沒在跑」，
#    而那可能是**刻意不啟用**（很多 AIX 的子系統預設就不跑）。
#    當成 failed 一律紅燈的話，每一台都會紅——那就是假警報。
# 3. **`lssrc -s xntpd` 只證明 daemon 活著，不證明時間有同步。**
#    Linux 的 `timedatectl NTPSynchronized` 講的是後者。兩者不可以混填同一欄。
#
# ## 權限
# 以下全部**非 root 可執行、全部唯讀**。AIX 的錯誤日誌 `errpt` 要 root，
# 屬於批次 3，本檔不碰，**也不去要 sudo 白名單**（要開得先問使用者）。
# ═══════════════════════════════════════════════════════════════════

#: AIX 的 computational memory（程式真正吃的）門檻。
#: 比 Linux 的 MEM 門檻寬一點點是刻意的：AIX 把記憶體用好用滿是設計如此，
#: 真正的壓力訊號是 paging space 開始被吃，不是記憶體看起來很滿。
AIX_COMP_RED, AIX_COMP_YELLOW = 92, 85
#: paging space 使用率。AIX 的 paging 滿了會直接殺行程，比 Linux 的 swap 嚴重。
AIX_PGSP_RED, AIX_PGSP_YELLOW = 70, 40

#: 這幾個子系統沒在跑才值得說話。其餘 inoperative 一律只列出來當資訊，
#: 不亮燈——理由見上面第 2 點。
AIX_CRITICAL_SUBSYS = ("syslogd", "xntpd", "inetd")

AIX_METRICS_CMD = (
    "echo @@OS; oslevel -s 2>/dev/null; uname -a 2>/dev/null; "
    # uptime 一行同時有「開機多久」與 load average，兩個維度共用這一條
    "echo @@UPLOAD; uptime 2>/dev/null; "
    # 邏輯 CPU 數。LPAR 上這**不等於**真正拿得到的算力（那是 entitled capacity），
    # 所以 load/CPU 這個比值在共享 LPAR 上只能當參考——畫面會標明。
    "echo @@NCPU; bindprocessor -q 2>/dev/null; "
    "echo @@LPAR; lparstat -i 2>/dev/null | grep -E "
    "'Online Virtual CPUs|Entitled Capacity|Type|Mode'; "
    # vmstat 取兩次，**第二筆才是即時值**（第一筆是開機以來的平均，拿它判會失真）
    "echo @@VMSTAT; vmstat 1 2 2>/dev/null; "
    "echo @@SVMON; svmon -G -O unit=MB 2>/dev/null; "
    "echo @@PGSP; lsps -s 2>/dev/null; "
    # AIX 的 df -k 一條就同時給 %Used 與 %Iused，不必再跑一次 df -i
    "echo @@DF; df -k 2>/dev/null; "
    "echo @@MOUNT; mount 2>/dev/null; "
    "echo @@SRC; lssrc -a 2>/dev/null; "
    "echo @@NTPQ; ntpq -p 2>/dev/null; "
    "echo @@NETSTATI; netstat -in 2>/dev/null; "
    "echo @@TCPSTATES; netstat -an 2>/dev/null | grep -i tcp | "
    "awk '{print $NF}' | sort | uniq -c; "
    # AIX 的 ps 沒有 pmem，也沒有 --sort。用 <defunct> 抓殭屍最可靠。
    "echo @@ZOMB; ps -ef 2>/dev/null | grep -c '<defunct>'; "
    "echo @@TOPCPU; ps -eo pid,comm,pcpu 2>/dev/null | sort -rn -k3 | head -6; "
    "echo @@WHO; who 2>/dev/null | wc -l; "
    "echo @@LAST; last 2>/dev/null | head -6; "
    "echo @@END"
)


def parse_aix_os(text: str) -> dict:
    """`oslevel -s` ＋ `uname -a` -> {os, kernel}。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    lvl = next((ln for ln in lines if re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{4}", ln)), "")
    uname = next((ln for ln in lines if ln.lower().startswith("aix")), "")
    return {"os": f"AIX {lvl}" if lvl else (uname or ""), "kernel": uname}


def parse_aix_uptime(text: str) -> dict | None:
    """AIX 的 `uptime`：`10:30AM up 120 days, 3:45, 2 users, load average: ...`。

    AIX 沒有 /proc/uptime，只有這行人看的文字。剛重開機時是
    `up 25 mins`／`up 1:05`（沒有 days），三種都要吃。
    """
    t = (text or "").strip()
    if " up " not in t:
        return None
    seg = t.split(" up ", 1)[1].split("user")[0]
    days = 0
    m = re.search(r"(\d+)\s+day", seg)
    if m:
        days = int(m.group(1))
    mins = 0
    m = re.search(r"(\d+):(\d+)", seg)
    if m:
        mins = int(m.group(1)) * 60 + int(m.group(2))
    else:
        m = re.search(r"(\d+)\s+min", seg)
        if m:
            mins = int(m.group(1))
    secs = days * 86400 + mins * 60
    if secs <= 0 and days == 0 and mins == 0:
        return None
    fresh = secs < UPTIME_FRESH_MIN * 60
    return {"uptime_sec": secs, "days": round(secs / 86400, 1),
            "just_rebooted": fresh, "level": "yellow" if fresh else "green"}


def parse_aix_load(text: str, ncpu: int) -> dict | None:
    """AIX 的 load average 在 `uptime` 那一行尾巴。回傳形狀跟 Linux 版一致。"""
    m = re.search(r"load average:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)", text or "")
    if not m:
        return None
    l1, l5, l15 = (float(m.group(i)) for i in (1, 2, 3))
    ncpu = ncpu or 1
    per = round(l1 / ncpu, 2)
    return {"load1": l1, "load5": l5, "load15": l15, "ncpu": ncpu, "per_cpu": per,
            "proc_total": 0, "level": _lv(per, LOAD_RED, LOAD_YELLOW)}


def parse_aix_ncpu(text: str) -> int:
    """`bindprocessor -q` -> `The available processors are:  0 1 2 3`。"""
    m = re.search(r":\s*(.+)$", (text or "").strip())
    if not m:
        return 0
    return len([t for t in m.group(1).split() if t.isdigit()])


def parse_aix_vmstat(text: str) -> dict | None:
    """`vmstat 1 2` 的**最後一筆**：末四欄是 us sy id wa。

    第一筆是開機以來的平均值，拿它當「現在的 CPU 使用率」會失真——
    一台開機 120 天的機器，平均值永遠很漂亮，看不出此刻正在燒 CPU。
    """
    rows = []
    for ln in (text or "").splitlines():
        p = ln.split()
        if len(p) < 16:
            continue
        try:
            rows.append([int(x) for x in p[-4:]])
        except ValueError:
            continue
    if len(rows) < 2:
        return None
    us, sy, idle, wa = rows[-1]
    used = us + sy
    return {"used_pct": float(used), "iowait_pct": float(wa), "idle_pct": float(idle),
            "ncpu": 0,
            "level": _worst(_lv(used, CPU_RED, CPU_YELLOW),
                            _lv(wa, IOWAIT_RED, IOWAIT_YELLOW))}


def parse_aix_mem(svmon: str, pgsp: str) -> dict | None:
    """`svmon -G -O unit=MB` ＋ `lsps -s` -> 記憶體與 paging space。

    ⚠️ **判的是 computational（virtual），不是 inuse。**
    AIX 的 inuse 含檔案快取，長期就在 90%+，那是 VMM 設計如此不是壓力。
    拿 inuse 判，每一台 AIX 都會是紅燈，而它們其實好好的。
    """
    total = comp = inuse = avail = None
    for ln in (svmon or "").splitlines():
        p = ln.split()
        if p and p[0] == "memory" and len(p) >= 6:
            try:
                total, inuse, comp = float(p[1]), float(p[2]), float(p[5])
                # available 是 svmon 自己算的（扣掉 pin 與必要保留），
                # 拿 total-virtual 去推會高估——那是兩個不同的東西。
                avail = float(p[6]) if len(p) >= 7 else None
            except ValueError:
                return None
            break
    if not total:
        return None
    comp_pct = round(comp / total * 100, 1)
    inuse_pct = round(inuse / total * 100, 1)

    pg_pct = None
    for ln in (pgsp or "").splitlines():
        m = re.search(r"(\d+)\s*%", ln)
        if m:
            pg_pct = float(m.group(1))
            break

    lv = _lv(comp_pct, AIX_COMP_RED, AIX_COMP_YELLOW)
    if pg_pct is not None:
        lv = _worst(lv, _lv(pg_pct, AIX_PGSP_RED, AIX_PGSP_YELLOW))
    return {
        "total_kb": int(total * 1024),
        "avail_kb": int(avail * 1024) if avail is not None else None,
        # used_pct 這一欄是給畫面共用的，AIX 填的是 **computational**，
        # 不是 inuse——inuse 另外放，讓看的人知道兩個數字都在、意思不同。
        "used_pct": comp_pct, "inuse_pct": inuse_pct,
        "swap_total_kb": 0, "swap_pct": pg_pct if pg_pct is not None else 0.0,
        "basis": ("AIX 判的是 computational memory（程式真正吃的），"
                  f"不是 inuse（{inuse_pct}%）——AIX 會把閒置記憶體拿去當檔案快取，"
                  "inuse 長期就很高，那是正常的"),
        "level": lv,
    }


def parse_aix_df(text: str) -> list[dict]:
    """AIX 的 `df -k`：`Filesystem 1024-blocks Free %Used Iused %Iused Mounted on`。

    **跟 Linux 的 df 不一樣**：第三欄是 Free 不是 Used，而且空間與 inode
    在同一行（不用跑第二次 df -i）。照 Linux 的欄位位置讀會整排錯位。
    """
    out = []
    for ln in (text or "").splitlines():
        p = ln.split()
        if len(p) < 7 or p[0].lower() == "filesystem":
            continue
        try:
            size_kb, free_kb = int(p[1]), int(p[2])
            pct = int(p[3].rstrip("%"))
            ipct = int(p[5].rstrip("%"))
        except ValueError:
            continue
        mount = " ".join(p[6:])
        out.append({
            "mount": mount, "fs": p[0], "size_kb": size_kb,
            "used_kb": size_kb - free_kb, "avail_kb": free_kb,
            "use_pct": pct, "inode_pct": ipct,
            "level": _worst(_lv(pct, DISK_RED, DISK_YELLOW),
                            _lv(ipct, INODE_RED, INODE_YELLOW)),
        })
    out.sort(key=lambda d: -max(d["use_pct"], d["inode_pct"]))
    return out


def parse_aix_readonly(text: str) -> list[dict]:
    """AIX `mount` 的 options 欄有 `ro` -> 被掛成唯讀。

    /cdrom 之類本來就唯讀的不算；JFS2 因為 I/O 錯誤被重掛成 ro 才是要命的訊號。
    """
    out = []
    for ln in (text or "").splitlines():
        p = ln.split()
        if len(p) < 5 or p[0] in ("node", "----"):
            continue
        opts = p[-1]
        if "ro" not in opts.split(","):
            continue
        # 欄位：[node] mounted mounted-over vfs date... options
        mounted_over = p[1] if len(p) >= 6 and p[0].startswith("/") else (
            p[2] if len(p) > 2 else "")
        if mounted_over.startswith(("/cdrom", "/mnt/cd", "/proc")):
            continue
        out.append({"mount": mounted_over, "fs": "", "options": opts})
    return out


def parse_aix_subsystems(text: str) -> dict:
    """`lssrc -a` -> {"inoperative": [...], "critical_down": [...]}。

    ⚠️ **inoperative 不等於 systemd 的 failed**：
    failed ＝試著起來但失敗；inoperative ＝現在沒在跑，而那多半是**刻意不啟用**。
    一台正常的 AIX 本來就有一堆 inoperative 的子系統。

    所以這裡**不把 inoperative 當紅燈**，只列出來當資訊；
    只有 AIX_CRITICAL_SUBSYS 那幾個沒在跑才值得說話。
    這是為了不製造假警報——紅燈多到沒人看，是稽核工具最大的死因。
    """
    inop = []
    for ln in (text or "").splitlines():
        p = ln.split()
        if len(p) < 2 or p[0].lower() in ("subsystem",):
            continue
        if p[-1].lower() == "inoperative":
            inop.append(p[0])
    crit = [s for s in inop if s in AIX_CRITICAL_SUBSYS]
    return {"inoperative": inop, "critical_down": crit}


def parse_aix_ntp(ntpq: str, subsys: dict) -> dict:
    """時間同步。**daemon 活著 ≠ 時間有同步**，兩件事分開回。

    `ntpq -p` 裡開頭是 `*` 的那一列＝目前實際同步的來源。
    有 daemon 沒有 `*` ＝ 它在跑但沒跟上任何來源，時間照樣會飄。
    """
    running = "xntpd" not in subsys.get("inoperative", [])
    if not running:
        # ⚠️ daemon 沒在跑 ＝ **查到了，而且確定沒在同步**。
        # 這不是「沒查到」——把它算成缺項，畫面會同時說「xntpd 沒在跑（紅燈）」
        # 又說「時間同步這項還沒查」，自相矛盾。
        return {"daemon_running": False, "synced": False,
                "basis": "xntpd 這個 daemon 沒在跑，所以時間確定沒有在同步"}
    synced = None
    for ln in (ntpq or "").splitlines():
        if ln.startswith("*"):
            synced = True
            break
    if synced is None and (ntpq or "").strip():
        synced = False          # ntpq 有輸出但沒有 * ＝ 確定沒同步上
    return {"daemon_running": True, "synced": synced,
            "basis": ("`ntpq -p` 有一列標成同步中" if synced
                      else "xntpd 在跑，但 `ntpq -p` 沒有任何來源標成同步中"
                      if synced is False
                      else "xntpd 在跑，但 `ntpq -p` 沒有輸出，無法確認真的有同步")}


def parse_aix_netstat_in(text: str) -> dict:
    """`netstat -in` 的 Ierrs／Oerrs 累計。AIX 沒有 /proc/net/dev。

    欄位：Name Mtu Network Address Ipkts Ierrs Opkts Oerrs Coll
    """
    total_err = 0
    ifaces = []
    seen = set()
    for ln in (text or "").splitlines():
        p = ln.split()
        if len(p) < 9 or p[0].lower() == "name":
            continue
        name = p[0].rstrip("*")
        if name.startswith("lo") or name in seen:
            continue
        seen.add(name)
        try:
            ierr, oerr = int(p[5]), int(p[7])
        except ValueError:
            continue
        total_err += ierr + oerr
        if ierr + oerr:
            ifaces.append({"iface": name, "err": ierr + oerr, "drop": 0})
    return {"err_total": total_err, "drop_total": 0, "ifaces": ifaces,
            "level": "yellow" if total_err >= NETDROP_YELLOW else "green"}


# ═══════════════════════════════════════════════════════════════════
# 完整度（2026-09-23）
#
# `_worst()` 只看**有值**的維度。所以四項只做出三項時，總燈號是那三項裡最嚴重的，
# 畫面上卻長得跟「四項全查過、沒問題」一模一樣。
#
# ⚠️ **修法不是把綠降成黃。** 那是把「沒查到」當成「有問題」——
# 跟「把沒查到顯示成綠」是同一個錯誤的鏡像，兩個都在回答一個沒被問的問題。
# 八台 AIX 第一版只做得出三個維度就會全黃，值班點進去發現「其實沒事，只是沒查完」，
# 下次就不會再點了。（使用者 2026-09-23 上午原話：「這資訊看了我會覺得可怕」。）
#
# **「沒問題」「有問題」「還沒判讀」是三件事，不可以擠進兩個顏色。**
# 所以顏色維持原意，另外帶一個獨立旗標：
#   green/yellow/red ＝ 查過的結果
#   complete=False   ＝ 還沒查完（畫面在燈號旁邊寫「未完整檢查 3/4」，點得進去看缺什麼）
# ═══════════════════════════════════════════════════════════════════

#: 一台「查完整」該有的維度。key -> 給人看的名字。
DIMENSIONS = {
    "os": "OS／核心",
    "uptime": "開機時間",
    "cpu": "CPU／負載",
    "mem": "記憶體／分頁",
    "disks": "磁碟空間",
    "mounts": "掛載狀態",
    "services": "服務狀態",
    "ports": "監聽埠",
    "time_sync": "時間同步",
}


def _coverage(got: dict, why: dict) -> dict:
    """做到哪幾項、缺哪幾項、各自為什麼。

    `got`：{維度 key: 有沒有拿到}；`why`：{維度 key: 沒拿到的原因}。
    原因**一定要填**——「缺了」而不說為什麼，跟顯示 0 是同一種病。
    """
    done = [k for k, v in got.items() if v]
    missing = [{"key": k, "name": DIMENSIONS.get(k, k),
                "why": why.get(k) or "這一版還沒做這個平台的這個維度"}
               for k in DIMENSIONS if not got.get(k)]
    return {"done": len(done), "total": len(DIMENSIONS), "missing": missing,
            "text": (f"完整檢查（{len(done)}/{len(DIMENSIONS)} 項）" if not missing
                     else f"未完整檢查 {len(done)}/{len(DIMENSIONS)} 項")}
def split_rc(text: str) -> tuple[str, int | None]:
    """把 `__RC__=<n>` 從段落尾巴拆出來。回（內容, 離開碼）。

    離開碼是**跟語言無關**的判準。訊息文字會隨 locale 與 sudo 版本變
    （221 是中文，回的是「sudo: 需要密碼」），比對字面永遠會漏下一種。

    拿不到 __RC__ 就回 None——那代表指令根本沒跑到（連線斷、shell 不支援），
    一樣要當成「沒查到」，不可以當成 0。
    """
    rc = None
    kept = []
    for line in (text or "").splitlines():
        t = line.strip()
        if t.startswith("__RC__="):
            try:
                rc = int(t.split("=", 1)[1])
            except ValueError:
                rc = None
            continue
        kept.append(line)
    return "\n".join(kept), rc


def looks_like_sudo_error(line: str) -> bool:
    """這一行像不像 sudo 吐的錯誤，而不是資料。

    **不管判斷對不對，這種行都不可以被收進資料。**
    2026-09-22：「sudo: 需要密碼」被算成一筆核心錯誤、讓那區變黃——
    判斷錯只是少一個警示，資料污染是直接產生假數字。
    `sudo:` 這個前綴是 sudo 自己印的，各語言都一樣。
    """
    return line.strip().startswith(("sudo:", "sudo :"))


def sudo_denied(text: str) -> bool:
    """這段輸出是不是「sudo 沒授權」而不是真的沒東西。

    sudo -n 沒授權時會吐這幾種訊息（各版本措辭不同，所以比對多個關鍵字）。
    判錯的代價不對稱：把「沒授權」當成「沒問題」會給假的安全感，
    反過來只是多顯示一行提醒，所以**寧可寬鬆地判成沒授權**。
    """
    t = (text or "").lower()
    return any(k in t for k in (
        "password is required", "a terminal is required", "not allowed to execute",
        "sudo: no tty", "may not run", "command not allowed", "not in the sudoers",
        # 中文 locale（221 就是）。**這只是第三道防線**——主要判準是離開碼，
        # 因為下一台可能是日文或別的措辭，比對字面永遠追不完。
        "需要密碼", "不在 sudoers", "不允許執行", "沒有權限",
    ))


#: 登入紀錄可能的位置。RHEL 系是 secure，Debian／Ubuntu 系是 auth.log。
#: 順序有意義：先試 secure（公司機隊以 RHEL 為主），讀不到再試 auth.log。
SSH_LOG_PATHS = ("/var/log/secure", "/var/log/auth.log")


def parse_ssh_fails(text: str, source: str = "/var/log/secure") -> dict | None:
    """登入紀錄最後 200 行裡的 SSH 登入失敗。`source` 是實際讀的那個檔。

    回 None ＝**沒授權或讀不到**，畫面要顯示「尚未開通」；
    回 dict 才是真的查過。兩者絕不可以混——
    「沒查到」跟「查過沒問題」在資安上是完全相反的結論。
    """
    body, rc = split_rc(text)
    # rc 不是 0（含拿不到 rc）＝沒跑成。**這是主要判準**，跟語言無關。
    # **離開碼是主要判準**：rc=0 就是指令真的跑成了，這時不可以再被字面比對推翻
    # （輸出裡剛好有「需要密碼」這類字樣是有可能的，那不代表沒授權）。
    # sudo_denied 只在**拿不到 rc** 時當第二道用。
    if rc is None:
        return None
    if rc != 0 or (rc is None and sudo_denied(body)):
        return None
    # rc=0 但沒輸出＝**查過了、真的沒有失敗登入**，要回 0 不是 None。
    # 回 None 會顯示成「沒有查到」，把乾淨的機器誤報成沒查——
    # 那跟把沒查當成沒問題一樣糟，只是方向相反。
    if not body.strip():
        return {"fails": 0, "distinct_ips": 0, "top_sources": [], "level": "green",
                "source": source,
                "note": f"取樣自 {source} 最後 200 行，期間沒有失敗登入"}
    # 就算判成有授權，像 sudo 錯誤的行也一律不收——不可以變成統計數字
    body = "\n".join(l for l in body.splitlines() if not looks_like_sudo_error(l))
    if not body.strip():
        return None
    text = body
    import re as _re
    fails = 0
    by_ip: dict[str, int] = {}
    for line in text.splitlines():
        if "Failed password" not in line and "Invalid user" not in line:
            continue
        fails += 1
        m = _re.search(r"from (\d{1,3}(?:\.\d{1,3}){3})", line)
        if m:
            by_ip[m.group(1)] = by_ip.get(m.group(1), 0) + 1
    top = sorted(by_ip.items(), key=lambda kv: -kv[1])[:5]
    lvl = ("red" if fails >= SSHFAIL_RED
           else "yellow" if fails >= SSHFAIL_YELLOW else "green")
    return {"fails": fails, "distinct_ips": len(by_ip),
            "top_sources": [{"ip": i, "count": n} for i, n in top],
            "level": lvl, "source": source,
            "note": f"取樣自 {source} 最後 200 行，不是全部歷史"}


#: 開機一次性告知：**樣式 → 給人看的白話解釋**。
#:
#: 為什麼一定要配一句解釋：使用者 2026-09-23 問「會讓人本來沒事更害怕嗎」。
#: 他說的對。一筆看不懂的訊息掛在畫面上、沒有人告訴你那是什麼，
#: 比一盞黃燈更折磨人——看的人只能自己腦補最壞的情況。
#: **所以這裡的規則是：要嘛給得出解釋才列進來，要嘛就照實說「還沒判讀」**，
#: 絕不留一句沒頭沒尾的核心訊息讓人自己猜。
#:
#: 每一條都是 2026-09-23 在 221 上查過證據才加的，不是憑印象：
_BOOT_NOTICE_EXPLAIN = {
    # RHEL9/Rocky9 對「原廠不再維護」的驅動在載入時提醒一次。
    # 依據：訊息本身就是發行版的公告文字（"will not be maintained in a future
    # major release"），意思固定，不隨機器而變。
    "unmaintained driver is detected":
        "作業系統原廠宣告這個驅動不再維護，未來大版本可能移除。是預告，不是故障。",
    "deprecated driver is detected":
        "作業系統原廠宣告這個驅動不再維護，未來大版本可能移除。是預告，不是故障。",
    # PIIX4 南橋的 SMBus 功能沒被韌體開啟，驅動載入時說一聲。
    # **刻意只描述這句話的意思，不替任何一台下結論。**
    # 2026-09-23 原本寫「這台沒有任何裝置掛在上面，不影響任何功能」——
    # 那是我在 221 上查到的事實，卻會原封不動顯示在每一台機器上。
    # 使用者當場指出：「這是 AI 判斷，公司環境沒辦法判斷」。他是對的：
    # 拿一台的觀察去替沒看過的機器背書，正是這個系統最不該做的事。
    "smbus host controller not enabled":
        "韌體沒有開啟主機板上的 SMBus（溫度／電壓感測匯流排），驅動載入時提示一次。"
        "虛擬機通常沒有這條匯流排，常見於此。",
}
#: 說明的出處，必須跟說明一起顯示。
#: 這些是**依訊息類型的通則說明**，不是對「這一台」做過的個別檢查——
#: 兩者混在一起，等於用一台機器的觀察替全公司的機器背書。
_NOTICE_SOURCE = ("說明依訊息類型而定（來自作業系統原廠對該訊息的定義），"
                  "**不是對這台機器做的個別判讀**。要確認這台的實際狀況，"
                  "仍須看該機器自己的組態。")
_BOOT_NOTICE_PATTERNS = tuple(_BOOT_NOTICE_EXPLAIN)
#: 開機秒數上限。dmesg 的 `[   3.222531]` 是開機後秒數；超過這個秒數就不算「開機時」，
#: 因為同樣一句話在跑了三天的機器上冒出來，意義完全不同（那是剛剛載入了某個模組）。
_BOOT_NOTICE_MAX_SEC = 120.0
_NOTICE_NOTE = ("以下是開機時說一次就結束的訊息，不是執行中的錯誤，"
                "不計入錯誤數、不影響燈號。" + _NOTICE_SOURCE)
#: 還沒判讀過的那些：**不假裝沒事，也不假裝有事**。
_UNREVIEWED_NOTE = ("以下這幾筆我們還沒判讀過——**既不代表有問題，也不代表沒問題**。"
                    "它們出現在開機階段且之後沒有再出現，所以不影響燈號；"
                    "需要確認的話把整行給我們，查證後會補上說明。")


def _boot_notice_explain(line: str) -> str | None:
    """開機時的一次性告知？是的話回那句白話解釋，不是的話回 None。

    要**同時**滿足「樣式已查證」與「發生在開機階段」：
    只看樣式會誤判——機器跑了三天之後才冒出同一句，那是剛載入模組，值得看。
    只看時間更不行——開機階段真的會有 I/O error、OOM。兩個條件缺一不可。
    """
    low = line.lower()
    hit = next((p for p in _BOOT_NOTICE_PATTERNS if p in low), None)
    if hit is None:
        return None
    m = re.match(r"\[\s*([0-9.]+)\]", line)
    if not m:                      # 沒有時間戳＝無法證明它在開機階段，就不降級
        return None
    try:
        if float(m.group(1)) > _BOOT_NOTICE_MAX_SEC:
            return None
    except ValueError:
        return None
    return _BOOT_NOTICE_EXPLAIN[hit]


#: 就算發生在開機階段也**不可以**歸進「還沒判讀」的——這些本身就是明確的故障，
#: 開機階段一樣算數（開機時的 I/O error 往往正是磁碟要壞的第一個徵兆）。
_HARD_ERROR_HINTS = ("out of memory", "oom-kill", "i/o error", "ata error",
                     "medium error", "filesystem error", "call trace",
                     "kernel panic", "hardware error", "mce:")


def _looks_like_hard_error(line: str) -> bool:
    low = line.lower()
    return any(h in low for h in _HARD_ERROR_HINTS)


def _is_boot_phase(line: str) -> bool:
    """發生在開機階段（有時間戳且在門檻內）。沒有時間戳一律不算。"""
    m = re.match(r"\[\s*([0-9.]+)\]", line)
    if not m:
        return False
    try:
        return float(m.group(1)) <= _BOOT_NOTICE_MAX_SEC
    except ValueError:
        return False


def parse_port_units(text: str) -> dict[int, dict]:
    """`port|unit|cmdline` 每行一筆 → {port: {"unit", "cmd"}}。

    unit 來自 `/proc/<pid>/cgroup` 的最後一段（`webit3-web.service` → `webit3-web`）。
    **為什麼要這個**：使用者 2026-09-23「port 3000 能顯示 webit3 服務嗎，光 python 沒用」。
    行程名只講實作語言，一台機器上四個 python、三個 node 是常態，值班分不出誰是誰。

    不是 systemd 管的（例如 systemd 自己、或 user scope）unit 會是空字串——
    **就讓它空著**。用行程名硬湊一個身分，等於給出一個看起來很確定的錯答案。
    """
    out: dict[int, dict] = {}
    body, _rc = split_rc(text)
    for line in (body or "").splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        out[int(parts[0])] = {"unit": parts[1].strip(),
                              "cmd": (parts[2].strip() if len(parts) > 2 else "")}
    return out


def parse_kernel_errors(text: str) -> dict | None:
    """dmesg 的 err/crit。OOM 與 I/O error 是最該先看到的兩種。

    回 None ＝沒授權或讀不到（同上，要顯示「尚未開通」）。
    """
    body, rc = split_rc(text)
    # **離開碼是主要判準**：rc=0 就是指令真的跑成了，這時不可以再被字面比對推翻
    # （輸出裡剛好有「需要密碼」這類字樣是有可能的，那不代表沒授權）。
    # sudo_denied 只在**拿不到 rc** 時當第二道用。
    if rc is None:
        return None
    if rc != 0 or (rc is None and sudo_denied(body)):
        return None
    raw = [l.strip() for l in body.splitlines()
           if l.strip() and not looks_like_sudo_error(l)]
    # 開機時的一次性「告知」要跟執行中的錯誤分開算。
    # 2026-09-23 使用者在 221 看到「核心錯誤 8 筆」而且整區變黃，問「這會影響系統嗎」——
    # 那 8 筆全是 `[3.2秒] Warning: Unmaintained driver is detected: mptbase` 這類，
    # 開機三秒印的，一小時後還是 8 筆、沒有新增，而且那個驅動底下 0 個裝置。
    # 它的意思是「這個驅動原廠不再維護、未來大版本可能移除」，不是現在壞掉。
    # 把它算成錯誤會讓面板長期掛著黃燈，值班很快就學會忽略整個面板——
    # **假警報比不顯示更糟**，因為它會連真的警報一起淹掉。
    # 仍然照列出來（不是隱藏），只是不算 total、不影響燈號。
    # 三堆，不是兩堆：
    #   notices    已查證的開機告知 → 附白話解釋，不算錯誤
    #   unreviewed 開機階段、但我們還沒查證過 → **照實說還沒判讀**，不算錯誤
    #   lines      其餘 → 錯誤，照舊影響燈號
    #
    # 為什麼要有第三堆：使用者 2026-09-23 問「會讓人本來沒事更害怕嗎」。
    # 原本的做法是「沒查證過的就留著算錯誤」，結果畫面上掛一筆看不懂的核心訊息、
    # 旁邊一盞黃燈，卻沒有人告訴他那是什麼——**看的人只能腦補最壞的情況**。
    # 那跟假警報一樣糟：一個讓人忽略面板，一個讓人怕面板。
    #
    # 但也不能直接吃掉：吃掉就變成「沒查到當成沒問題」，那是這個系統最不能犯的錯。
    # 所以第三堆兩句話都講滿：「既不代表有問題，也不代表沒問題」＋為什麼不亮燈
    # （開機階段出現、之後沒再出現）＋下一步（把整行給我們，查證後補說明）。
    # 已知的真錯誤（OOM／I/O error）不會掉進這一堆——它們有自己的判斷，照樣紅燈。
    notices, unreviewed, lines = [], [], []
    for l in raw:
        why = _boot_notice_explain(l)
        if why:
            notices.append({"line": l, "why": why})
        elif _is_boot_phase(l) and not _looks_like_hard_error(l):
            unreviewed.append(l)
        else:
            lines.append(l)
    tail = {"notices": notices, "notice_note": _NOTICE_NOTE if notices else None,
            "unreviewed": unreviewed,
            "unreviewed_note": _UNREVIEWED_NOTE if unreviewed else None}
    if not lines:
        # 有授權但 dmesg 真的沒有 err/crit＝好事，回 0 而不是 None
        return {"total": 0, "oom": 0, "io_error": 0, "samples": [], **tail,
                "level": "green"}
    oom = [l for l in lines if "out of memory" in l.lower() or "oom-kill" in l.lower()]
    io_err = [l for l in lines if "i/o error" in l.lower() or "ata error" in l.lower()
              or "medium error" in l.lower()]
    lvl = "red" if (oom or io_err) else ("yellow" if lines else "green")
    # 去重：一行可能同時落在 oom 與 lines，直接串接會重複顯示同一條。
    # 保留順序（嚴重的先）：dict.fromkeys 比 set 好，set 會把順序打亂。
    samples = list(dict.fromkeys(oom + io_err + lines))[:5]
    return {"total": len(lines), "oom": len(oom), "io_error": len(io_err),
            "samples": samples, **tail, "level": lvl}


# ===== Windows（值班健檢 8-3）=====
#
# 使用者 2026-09-23：「AIX／Windows 也要啊」。權限那題使用者拍板
# **不做登入失敗偵測（Event ID 4625）、不加 Event Log Readers、不動任何權限**，
# 所以這裡完全不碰安全性記錄檔；畫面會標「未授權讀取安全性記錄檔，沒有查」。
#
# 門檻**不沿用 Linux**。Windows 正常運作時就會把大量實體記憶體用在 standby
# cache，拿 Linux 的「使用率 %」去判會整批誤判成記憶體吃緊。改看兩件事：
#   * 可用實體記憶體（MB）——低到某個絕對值才是真的不夠
#   * 認可使用量（commit charge / commit limit）——這才是「再開得起東西嗎」
# 兩個都不好才算紅。單看一個都會誤判：commit 高但實體還很多是正常的快取行為。
WIN_MEM_FREE_RED_MB, WIN_MEM_FREE_YELLOW_MB = 512, 1024
WIN_COMMIT_RED, WIN_COMMIT_YELLOW = 95, 85


def parse_win_kv(text: str) -> dict:
    """`key=value` 一行一組。PowerShell 那支刻意輸出這個格式，不用 JSON——
    舊版 PowerShell 的 JSON 輸出格式不穩（見 winrm_collector 檔頭）。"""
    out = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def parse_win_uptime(text: str) -> dict | None:
    """開機多久。收不到就 None——**不可以回 0**，0 會被讀成「剛剛才開機」。"""
    kv = parse_win_kv(text)
    try:
        boot = _dt.datetime.strptime(kv["boot"], "%Y-%m-%d %H:%M:%S")
        now = _dt.datetime.strptime(kv["now"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, ValueError):
        return None
    mins = max(0.0, (now - boot).total_seconds() / 60)
    fresh = mins < UPTIME_FRESH_MIN
    return {"days": round(mins / 1440, 1), "just_rebooted": fresh,
            "level": "yellow" if fresh else "green"}


def parse_win_cpu(text: str) -> dict | None:
    """CPU 使用率。

    **iowait 與 load average 回 None**：Windows 沒有這兩個概念的等價物。
    填 0 會被讀成「量到了，而且很好」——那是在編一個數字。
    """
    kv = parse_win_kv(text)
    try:
        usage = float(kv["usage"])
    except (KeyError, ValueError):
        return None
    try:
        ncpu = int(kv.get("ncpu") or 1)
    except ValueError:
        ncpu = 1
    lvl = ("red" if usage >= CPU_RED else "yellow" if usage >= CPU_YELLOW else "green")
    return {"usage_pct": round(usage, 1), "iowait_pct": None, "ncpu": max(1, ncpu),
            "level": lvl,
            "note": "Windows 沒有 iowait 這個指標，這一欄不是 0 是「沒有這種東西」"}


def parse_win_mem(text: str) -> dict | None:
    """記憶體。**判準跟 Linux 不同**，理由見上面那段常數的註解。"""
    kv = parse_win_kv(text)
    try:
        total_kb = float(kv["total_kb"])
        free_kb = float(kv["free_kb"])
    except (KeyError, ValueError):
        return None
    free_mb = free_kb / 1024
    used_pct = round((total_kb - free_kb) / total_kb * 100, 1) if total_kb else 0.0

    commit_pct = None
    try:
        ct = float(kv["commit_total_kb"])
        cf = float(kv["commit_free_kb"])
        if ct:
            commit_pct = round((ct - cf) / ct * 100, 1)
    except (KeyError, ValueError, ZeroDivisionError):
        commit_pct = None

    # **兩個都不好才算紅**：commit 高但實體還很多，是 Windows 正常的快取行為。
    free_lv = ("red" if free_mb < WIN_MEM_FREE_RED_MB
               else "yellow" if free_mb < WIN_MEM_FREE_YELLOW_MB else "green")
    commit_lv = "green"
    if commit_pct is not None:
        commit_lv = ("red" if commit_pct >= WIN_COMMIT_RED
                     else "yellow" if commit_pct >= WIN_COMMIT_YELLOW else "green")
    lvl = "green"
    if free_lv == "red" and commit_lv == "red":
        lvl = "red"
    elif "red" in (free_lv, commit_lv) or "yellow" in (free_lv, commit_lv):
        lvl = "yellow"

    # total_kb／avail_kb 用既有的欄位名，讓畫面那些共用的算式（用了多少/共多少）
    # 直接work——**這兩個是真的量到的值，不是為了填欄位編出來的**。
    # swap_total_kb 給 0：Windows 的分頁檔已經算在 commit 裡，
    # 再列一個「swap 使用率」會變成同一件事講兩次。
    return {"used_pct": used_pct, "free_mb": int(free_mb), "commit_pct": commit_pct,
            "total_kb": int(total_kb), "avail_kb": int(free_kb),
            "swap_total_kb": 0, "swap_pct": 0, "level": lvl,
            "note": "Windows 的判準是「可用實體記憶體 ＋ 認可使用量」，"
                    "不是使用率——正常運作就會把記憶體用在快取上"}


def parse_win_disks(text: str) -> list[dict]:
    """`磁碟機|總位元組|可用位元組`。inode 是檔案系統概念，NTFS 沒有 → None。"""
    out = []
    for line in (text or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3:
            continue
        dev, total_s, free_s = parts
        try:
            total, free = float(total_s), float(free_s)
        except ValueError:
            continue
        if total <= 0:
            continue
        use_pct = round((total - free) / total * 100)
        lvl = ("red" if use_pct >= DISK_RED else "yellow" if use_pct >= DISK_YELLOW
               else "green")
        # size_kb／avail_kb 用畫面既有的欄位名（「已用/總」那一欄吃這兩個）。
        # 少給的話會算出 NaN，畫面印「50.0/NaNG」——**看起來像壞掉，而不是缺資料**。
        out.append({"mount": dev, "fs": "NTFS", "use_pct": use_pct, "inode_pct": None,
                    "used_kb": int((total - free) / 1024), "total_kb": int(total / 1024),
                    "size_kb": int(total / 1024), "avail_kb": int(free / 1024),
                    "level": lvl})
    return out


def parse_win_services(text: str) -> list[str]:
    """設為自動啟動、卻沒在跑的服務。

    這才是 `systemctl --failed` 的**語意**對應——不是「列出所有服務」。
    名稱像不算對應：硬湊一個名字像的欄位，比沒有這一欄更糟，
    因為人會拿它當同一件事看。

    `Start Pending` 不算：那是**正在啟動**，不是沒在跑。把它算進去，
    每次重開機後幾分鐘內都會紅一次，值班很快就學會忽略這個燈。
    """
    out = []
    for line in (text or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) < 3:
            continue
        name, _disp, state = parts[0], parts[1], parts[2]
        if state.strip().lower() in ("start pending", "starting"):
            continue
        if name.strip():
            out.append(name.strip())
    return out


def parse_win_time(text: str) -> bool | None:
    """時間有沒有跟外部來源同步。回 None ＝查不到（不是「沒同步」）。

    看 `w32tm /query /source` 吐出來的**來源字串本身**，不看 /status 的欄位標題——
    標題是翻譯過的，比對字面一定會漏掉某個語言版本。
    """
    kv = parse_win_kv(text)
    if kv.get("rc") not in (None, "0"):
        return None
    src = (kv.get("src") or "").strip()
    if not src:
        return None
    # 這兩個值代表「只靠自己的時鐘」，就是沒有跟外部同步
    local = ("local cmos clock", "free-running system clock")
    return not any(x in src.lower() for x in local)


def _check_windows(base: dict, ip: str, creds, _winrm=None, _t0=None) -> dict:
    """Windows 走 WinRM（不是 SSH）。

    沿用既有的 winrm_collector 與收集憑證，**不另開管道**：
    另開等於要在 Windows 上裝 SSH server（新增攻擊面）＋第二套憑證來源
    （第二份真相）。健檢只需一支 PowerShell 一次往返，成本跟 SSH 同級。
    """
    _t0 = _t0 or _dt.datetime.now()

    def _fail(msg: str, why_all: str, ours: bool = False) -> dict:
        """`ours=True` ＝這是**我們**的問題（沒有憑證），不是那台機器沒回應。"""
        base["error"] = msg
        base["notes"].append(msg)
        base["coverage"] = _coverage({}, {k: why_all for k in DIMENSIONS})
        if ours:
            base["not_probed"] = msg
            base["overall"] = "skipped"      # 不進「異常」——那是我們的待辦
        base["took_ms"] = int((_dt.datetime.now() - _t0).total_seconds() * 1000)
        return base

    got_creds = creds(ip) if callable(creds) else creds
    if not got_creds:
        # **講原因，不要顯示成連不上。** 沒有憑證跟機器掛掉是完全不同的下一步：
        # 一個是去設定頁補帳號，一個是去機房。
        return _fail("沒有可用的 WinRM 收集憑證——請先在「7-3 系統設定 → 收集憑證」"
                     "新增 Windows 服務帳號",
                     "沒有 WinRM 憑證，這一輪根本沒去探測", ours=True)

    username, password = got_creds
    try:
        import winrm_collector

        out = (_winrm or winrm_collector.collect_health)(ip, username, password)
    except Exception as exc:  # noqa: BLE001 - 連不上的原因要原樣講給人看
        msg = str(exc)[:200] or "WinRM 連線失敗"
        try:
            import winrm_collector as _wc

            if any(k in msg for k in ("refused", "timed out", "Max retries")):
                msg = f"{msg}｜{_wc.ENABLE_HINT}"
        except Exception:  # noqa: BLE001 - 取不到提示不影響主要訊息
            pass
        return _fail(msg, "WinRM 連不上，這一輪什麼都沒查到")

    base["reachable"] = True
    seg = _split_markers(out)

    osd = parse_win_kv(seg.get("OS", ""))
    base["os"] = osd.get("caption") or None
    base["kernel"] = osd.get("version") or None
    base["uptime"] = parse_win_uptime(seg.get("UP", ""))
    base["cpu"] = parse_win_cpu(seg.get("CPU", ""))
    base["mem"] = parse_win_mem(seg.get("MEM", ""))
    base["disks"] = parse_win_disks(seg.get("DISK", ""))
    base["failed_units"] = parse_win_services(seg.get("SVC", ""))
    base["ntp_synced"] = parse_win_time(seg.get("TIME", ""))

    import service_collector

    base["ports"] = service_collector.parse_listen(seg.get("LISTEN", ""))
    base["process_visible"] = any(p.get("process") for p in base["ports"])

    levels = [base[k]["level"] for k in ("cpu", "mem", "uptime") if base.get(k)]
    levels += [d["level"] for d in base["disks"]]
    if base["failed_units"]:
        levels.append("red")
        base["notes"].append(
            f"有 {len(base['failed_units'])} 個「自動啟動」的服務目前沒在跑："
            + "、".join(base["failed_units"][:5])
            + ("…" if len(base["failed_units"]) > 5 else ""))
    if base["ntp_synced"] is False:
        base["notes"].append("時間只跟本機時鐘走，沒有跟外部來源同步（時間飄會連鎖出怪事）")
        levels.append("yellow")
    base["overall"] = _worst(*levels) if levels else "red"

    # 這一版查得到／查不到的分別。**沒查到的要說為什麼，而且不可以填 0 充數。**
    base["coverage"] = _coverage(
        {
            "os": bool(base["os"]),
            "uptime": base["uptime"] is not None,
            "cpu": base["cpu"] is not None,
            "mem": base["mem"] is not None,
            "disks": bool(base["disks"]),
            "mounts": False,
            "services": "SVC" in seg,
            "ports": "LISTEN" in seg,
            "time_sync": base["ntp_synced"] is not None,
        },
        {
            "mounts": "Windows 沒有「因磁碟錯誤被重新掛成唯讀」這個機制，"
                      "不是沒查到，是這個平台沒有這件事",
            "time_sync": "w32tm 查不到時間來源（可能服務沒啟用）",
            "os": "CIM 查不到 Win32_OperatingSystem",
            "cpu": "CIM 查不到 Win32_Processor 的負載",
            "mem": "CIM 查不到記憶體用量",
            "disks": "CIM 查不到本機磁碟",
        })
    base["complete"] = not base["coverage"]["missing"]

    # 使用者 2026-09-23 拍板：**不做登入失敗偵測、不動任何權限。**
    # 這一句一定要留在畫面上——沒有這句，值班會以為「沒有爆破」是查過的結論。
    base["sudo_log_authorized"] = False
    base["notes"].append(
        "登入失敗偵測沒有查：未授權讀取安全性記錄檔（依 2026-09-23 決定不開這個權限）。"
        "不是「沒有異常」")

    base["took_ms"] = int((_dt.datetime.now() - _t0).total_seconds() * 1000)
    return base


def _collect_account_for(platform: str) -> str:
    """收集帳號的唯一真相在 DB 設定（納管腳本與四個收集器都讀它）。

    讀不到 DB（單元測試、離線工具）才退回常數——退回時用的是同一個常數，
    不會出現第三個答案。
    """
    try:
        import db
        return ms.get_collect_account(db.get_connection(), platform)
    except Exception:  # noqa: BLE001 - 讀不到設定不該讓整台健檢失敗
        return ms.DEFAULT_COLLECT_ACCOUNT


def check_one(ip: str, platform: str = "linux",
              key_path: str | None = None, account: str | None = None,
              _ssh=_run_ssh, _svc=None, creds=None, _winrm=None) -> dict:
    """對單台跑完整健檢。回結構化結果（含 overall 燈號）。"""
    key_path = key_path or ms.COLLECTOR_KEY_DEFAULT
    # ⚠️ AIX 的收集帳號**不是同一個名字**：AIX 的 max_logname 上限讓它只能是
    # 8 字元（webit3sc），拿 webit3scan 去登一台 AIX 會每次都 Permission denied，
    # 而症狀跟「這台連不上」一模一樣。健檢這條原本沒處理，接上 AIX 就會整批失敗。
    # 2026-09-23 起兩個平台同一個帳號名（見 manage_state.READONLY_ACCOUNT 的說明），
    # 所以這裡不用再依平台分。留一份平台判斷只會變成第二套定義。
    #
    # ⚠️ 但**不可以直接讀常數**：既有機器上佈的可能還是舊名，
    # 「用哪個帳號」的唯一真相在 DB 設定（納管腳本與四個收集器都讀它）。
    # 2026-09-23 部署 v1.365.0 當下就踩到：常數改成新名、221 上只有舊名，
    # 健檢立刻把一台好好的機器顯示成「連不上 0/9」——
    # 又是同一個病根：同一件事兩份定義，而且拿到的是我們自己的問題卻顯示成那台的問題。
    account = account or _collect_account_for(platform)
    # 掃描時間（使用者 2026-09-22：「掃描時間要顯示出來」）。
    # 沒有時間的數字沒人敢用——三分鐘前跟三天前的「綠燈」意義完全不同。
    # 記開始時間而不是結束時間：那才是這些數字的量測時點。
    _t0 = _dt.datetime.now()
    base = {
        "ip": ip, "platform": platform, "reachable": False, "error": None, "unresolved": False,
        "checked_at": _t0.strftime("%Y-%m-%d %H:%M:%S"), "took_ms": None,
        "os": None, "kernel": None, "uptime": None,
        "cpu": None, "load": None, "mem": None,
        "disks": [], "readonly_mounts": [], "failed_units": [],
        "net": None, "tcp": None, "io": None, "zombies": 0,
        "ntp_synced": None, "dns_ok": None, "logins": None, "recent_logins": [],
        # 批次 3：None ＝**沒授權或讀不到**，畫面要顯示「尚未開通」而不是「正常」
        "ssh_fails": None, "kernel_errors": None,
        "sudo_log_authorized": None,
        "top_cpu": [], "top_mem": [], "ports": [], "process_visible": False,
        "overall": "red", "notes": [],
        # 顏色講「查過的結果」，完整度講「查完了沒」。兩件事分開放，
        # 不可以互相頂替（見 DIMENSIONS 上面那段）。
        "complete": False, "coverage": None,
        # **主詞是我們，不是那台機器。**
        # 「沒有憑證／沒有金鑰／平台不支援」是關於**我們**的事實；
        # 「連不上」是關於**那台機器**的宣稱。把前者講成後者，值班會跑去機房
        # 看一台好好的機器，而正確的下一步是走三步到設定頁補一個帳號。
        #
        # 2026-09-23 AIX 那批全部顯示成「連不上」，真因是我們自己拿錯帳號
        # （webit3scan vs webit3sc）——**我們自己的問題，顯示成對方機器的問題**。
        # None ＝我們真的去探測過（結果好壞另計）；有字串＝我們根本沒去成。
        "not_probed": None,
    }
    # 金鑰檔不在＝**我們這邊沒準備好**，不是那台沒回應。
    # 不先擋的話，ssh 會失敗，畫面顯示「連不上」——跟機器真的掛掉長得一模一樣，
    # 而兩者的下一步完全相反（一個是去補金鑰，一個是去機房）。
    # 只在沒有注入假 ssh 時檢查：測試餵的是假的連線，本來就不需要金鑰檔。
    if platform in ("linux", "aix") and _ssh is _run_ssh:
        import os as _os

        if key_path and not _os.path.exists(key_path):
            base["overall"] = "skipped"
            base["not_probed"] = (
                f"找不到收集金鑰 {key_path}——這是我們這邊的設定問題，"
                "不是那台機器沒有回應")
            base["error"] = base["not_probed"]
            base["notes"].append(base["not_probed"])
            base["coverage"] = _coverage({}, {k: "沒有金鑰，這一輪根本沒去探測"
                                              for k in DIMENSIONS})
            base["took_ms"] = int((_dt.datetime.now() - _t0).total_seconds() * 1000)
            return base

    if platform == "aix":
        return _check_aix(base, ip, key_path, account, _ssh, _t0)
    if platform == "windows":
        return _check_windows(base, ip, creds, _winrm, _t0)
    if platform != "linux":
        base["overall"] = "skipped"
        base["not_probed"] = f"這一版還不支援 {platform}，沒有去探測過這台"
        base["error"] = f"支援 Linux／AIX／Windows，{platform} 這台先跳過"
        base["took_ms"] = int((_dt.datetime.now() - _t0).total_seconds() * 1000)
        return base

    ok, out, err = _ssh(ip, _METRICS_CMD, key_path, account)
    if not ok and not out.strip():
        base["error"] = err or "連線失敗"
        # 區分「解析不出主機名」（輸入格式或 DNS 問題）與「連不上」（機器可能當機）。
        # 前者不是機器死了——把它說成當機會讓值班以為好好的主機掛了（2026-09-22 使用者
        # 貼 http://…／打錯時踩到）。判斷只看錯誤關鍵字，不猜。
        _e = (err or "").lower()
        if "resolve" in _e or "name or service not known" in _e or "nodename nor servname" in _e:
            base["unresolved"] = True
            base["notes"].append(
                "主機名無法解析——多半是輸入帶了 http://／打錯／DNS 查不到，"
                "不是機器當機。確認輸入的是純 IP 或正確主機名。")
        else:
            base["notes"].append("連不上這台——可能真的當機／關機，或網路/防火牆問題")
        # AIX 那條連不上時會填 coverage，Linux 這條原本留 None——**同一種情況兩種契約**。
        # 畫面拿到 None 只能顯示成空白，看起來像「這台沒有完整度這回事」。
        # 兩個平台對同一件事要講一樣的話（AIX 分支上面那句註解就是這個意思）。
        base["coverage"] = _coverage({}, {k: "SSH 連不上，這一輪什麼都沒查到"
                                          for k in DIMENSIONS})
        base["took_ms"] = int((_dt.datetime.now() - _t0).total_seconds() * 1000)
        return base

    base["reachable"] = True
    seg = _split_markers(out)
    osd = parse_os(seg.get("OS", ""))
    base["os"], base["kernel"] = osd["os"], osd["kernel"]
    base["uptime"] = parse_uptime(seg.get("UP", ""))
    ncpu_text = seg.get("CPU", "")
    base["cpu"] = parse_cpu(seg.get("STAT1", ""), seg.get("STAT2", ""), ncpu_text)
    ncpu = base["cpu"]["ncpu"] if base["cpu"] else \
        next((int(t) for t in ncpu_text.split() if t.isdigit()), 1)
    base["load"] = parse_load(seg.get("LOAD", ""), ncpu)
    base["mem"] = parse_mem(seg.get("MEM", ""))
    base["disks"] = parse_disks(seg.get("DF", ""), seg.get("DFI", ""))
    base["readonly_mounts"] = parse_readonly_mounts(seg.get("MOUNTS", ""))
    base["failed_units"] = parse_failed_units(seg.get("FAILED", ""))
    # 先 secure、讀不到再 auth.log。**不是猜發行版，是看哪個檔真的讀得到。**
    base["ssh_fails"] = parse_ssh_fails(seg.get("SECURELOG", ""), SSH_LOG_PATHS[0])
    if base["ssh_fails"] is None:
        base["ssh_fails"] = parse_ssh_fails(seg.get("AUTHLOG", ""), SSH_LOG_PATHS[1])
    base["kernel_errors"] = parse_kernel_errors(seg.get("DMESGERR", ""))
    # 兩項都拿不到就是 sudo 白名單還沒佈；要讓畫面講清楚，不可以看起來像「都正常」
    base["sudo_log_authorized"] = not (
        base["ssh_fails"] is None and base["kernel_errors"] is None)
    if not base["sudo_log_authorized"]:
        base["notes"].append(
            "SSH 爆破偵測與核心錯誤這兩項沒有查到（收集帳號還沒拿到 sudo 授權），"
            "不是「沒有異常」")
    elif base["ssh_fails"] is None:
        # 核心錯誤查得到（＝sudo 有開通），偏偏登入紀錄兩個路徑都讀不到。
        # 要講清楚「兩個都試過了」，否則人會以為是權限沒開而去重新納管一次，
        # 結果還是一樣——真正的原因是這台的登入紀錄不在這兩個位置。
        base["notes"].append(
            "SSH 爆破偵測沒有查到：" + "、".join(SSH_LOG_PATHS) +
            " 兩個路徑都試過，都讀不到。不是「沒有異常」，也不是權限沒開"
            "（同一批 sudo 的核心錯誤查得到）")
    base["failed_detail"] = parse_failed_detail(
        seg.get("FAILEDINFO", ""), base["failed_units"])
    base["net"] = parse_netdev(seg.get("NETDEV", ""))
    base["tcp"] = parse_tcp_states(seg.get("TCPSTATES", ""))
    # 兩次取樣算增量；取不到（例如非 Linux）回 None，畫面顯示「未量到」而不是 0
    base["io"] = parse_diskstats(seg.get("DISKSTAT1", ""), seg.get("DISKSTAT2", ""))
    try:
        base["zombies"] = int((seg.get("ZOMB", "0").strip() or "0").splitlines()[0])
    except (ValueError, IndexError):
        base["zombies"] = 0
    dns = seg.get("DNS", "").strip().lower()
    base["dns_ok"] = True if "ok" in dns else False if "fail" in dns else None
    ntp = seg.get("NTP", "").strip().lower()
    base["ntp_synced"] = True if ntp == "yes" else False if ntp == "no" else None
    try:
        base["logins"] = int((seg.get("WHO", "").strip() or "0").splitlines()[0])
    except (ValueError, IndexError):
        base["logins"] = None
    base["recent_logins"] = parse_last(seg.get("LAST", ""))
    base["top_cpu"] = parse_top(seg.get("TOPCPU", ""))
    base["top_mem"] = parse_top(seg.get("TOPMEM", ""))

    # port／服務：復用 service_collector（同一個 runner 抽象）。收不到不讓整份掛掉。
    svc = _svc
    if svc is None:
        import service_collector

        def _runner(host, cmd):
            _ok, _out, _e = _ssh(host, cmd, key_path, account)
            return _out
        try:
            svc = service_collector.collect(_runner, ip, "linux")
        except Exception as exc:  # noqa: BLE001 - 服務收不到只是少一塊證據，不是整台失敗
            svc = None
            base["notes"].append(f"服務／port 這次沒收到（{str(exc)[:80]}）")
    if svc:
        base["ports"] = svc.get("services", [])
        base["process_visible"] = svc.get("process_visible", False)

    # 有 sudo 授權時，用 `sudo ss -tlnp` 拿到的行程名覆蓋上去（免提權那份只有 port 號）。
    # 只補 process／refresh service_guess，不動 is_infra／exposure——最小改動，沒授權就跳過。
    port_units = parse_port_units(seg.get("PORTUNIT", ""))
    lp = seg.get("LISTENP", "")
    if "__RC__=0" in lp and base["ports"]:
        try:
            import service_collector as _sc
            proc_by_port = {s["port"]: s["process"]
                            for s in _sc.parse_listen(lp) if s.get("process")}
            if proc_by_port:
                for p in base["ports"]:
                    if not p.get("process") and p.get("port") in proc_by_port:
                        p["process"] = proc_by_port[p["port"]]
                        p["service_guess"] = _sc.guess_service(p["port"], p["process"], [])
                base["process_visible"] = True
        except Exception:  # noqa: BLE001 - 補行程名失敗不影響其餘健檢
            pass

    # 再補**服務身分**（unit）。行程名回答「用什麼寫的」，unit 回答「這是哪一套系統」——
    # 值班要的是後者：一台機器上同時有四個 python、三個 node 是常態。
    # 拿不到就不填，不要用行程名硬湊一個假的身分。
    for _p in base["ports"]:
        _u = port_units.get(_p.get("port"))
        if not _u:
            continue
        _p["unit"] = _u["unit"] or None
        _p["cmdline"] = _u["cmd"] or None
        # unit 是機器自己講的事實，優先度高於由 port 號猜出來的 service_guess。
        if _u["unit"]:
            _p["service_guess"] = _u["unit"]
            _p["guess_source"] = "unit"

    # ---- 總燈號：多訊號取最嚴重 ----
    levels = [d["level"] for d in base["disks"]]
    for k in LEVELLED_KEYS + SUDO_LEVELLED_KEYS:
        v = base[k]
        if not v:
            continue
        lv = v.get("level")
        if lv is None:
            # 少了 level 是**程式的錯**，不是那台主機的問題。
            # 這裡不炸掉整台健檢（一個欄位缺失不該讓值班看不到其他證據），
            # 但要在畫面上明講，不可以靜默當成沒事——
            # 靜默的話會變成「少算一個指標卻還是綠燈」。
            base["notes"].append(f"內部錯誤：{k} 少了 level 欄位，這個指標沒算進總燈號")
            continue
        levels.append(lv)
    if base["readonly_mounts"]:
        levels.append("red")                       # 該可寫卻唯讀＝磁碟故障徵兆
        base["notes"].append("有檔案系統被重掛成唯讀（磁碟可能出錯）")
    if base["failed_units"]:
        levels.append("red")                       # systemd 有服務掛了
        base["notes"].append(f"systemd 有 {len(base['failed_units'])} 個 failed 服務")
    if base["zombies"] >= ZOMBIE_YELLOW:
        levels.append("yellow")
    if base["ntp_synced"] is False:
        levels.append("yellow")
        base["notes"].append("NTP 未同步（時間可能飄）")
    base["overall"] = _worst(*levels)
    # Linux 的完整度。**沒裝 iostat 的機器維持綠燈**，只是旁邊標「未完整檢查」——
    # 不因為這次改動讓任何一台突然變黃。
    got = {
        "os": bool(base["os"] or base["kernel"]), "uptime": base["uptime"] is not None,
        "cpu": base["cpu"] is not None, "mem": base["mem"] is not None,
        "disks": bool(base["disks"]), "mounts": "MOUNTS" in seg,
        "services": "FAILED" in seg, "ports": bool(svc),
        "time_sync": base["ntp_synced"] is not None,
    }
    base["coverage"] = _coverage(got, {
        "cpu": "/proc/stat 讀不到（這台的 /proc 掛載或權限有異常）",
        "mem": "/proc/meminfo 讀不到",
        "disks": "df 沒有回任何檔案系統",
        "ports": "服務／port 這次沒收到（見上面的 notes）",
        "time_sync": "timedatectl 沒有回 NTPSynchronized（可能不是 systemd 管時間）",
    })
    base["complete"] = not base["coverage"]["missing"]
    base["took_ms"] = int((_dt.datetime.now() - _t0).total_seconds() * 1000)
    return base


def _check_aix(base: dict, ip: str, key_path: str, account: str, _ssh,
               _t0=None) -> dict:
    """AIX 的健檢。**不是把 Linux 指令硬套**——理由見 AIX_METRICS_CMD 上面那段。

    `_t0` 是 check_one 記的量測起點。AIX 這條也要帶掃描時間——
    使用者 2026-09-22 指名要看它，八台 AIX 沒有的話那一欄會是空白。
    """
    _t0 = _t0 or _dt.datetime.now()
    ok, out, err = _ssh(ip, AIX_METRICS_CMD, key_path, account)
    if not ok and not out.strip():
        base["error"] = err or "連線失敗"
        base["notes"].append("連不上這台——可能真的當機／關機，或網路/防火牆問題")
        base["coverage"] = _coverage({}, {k: "SSH 連不上，這一輪什麼都沒查到"
                                          for k in DIMENSIONS})
        # 主機名解析不出來 ≠ 機器當機。沿用 Linux 那條的判準，不要兩個平台講不一樣的話。
        _e = (err or "").lower()
        if ("resolve" in _e or "name or service not known" in _e
                or "nodename nor servname" in _e):
            base["unresolved"] = True
        base["took_ms"] = int((_dt.datetime.now() - _t0).total_seconds() * 1000)
        return base

    base["reachable"] = True
    seg = _split_markers(out)

    osd = parse_aix_os(seg.get("OS", ""))
    base["os"], base["kernel"] = osd["os"], osd["kernel"]

    upload = seg.get("UPLOAD", "")
    base["uptime"] = parse_aix_uptime(upload)
    ncpu = parse_aix_ncpu(seg.get("NCPU", ""))
    base["load"] = parse_aix_load(upload, ncpu)
    base["cpu"] = parse_aix_vmstat(seg.get("VMSTAT", ""))
    if base["cpu"] is not None:
        base["cpu"]["ncpu"] = ncpu

    # LPAR 的 load 分母問題**要講為什麼**，不是叫人「僅供參考」就算了。
    lpar = seg.get("LPAR", "")
    if base["load"] and ("Shared" in lpar or "shared" in lpar):
        base["load"]["basis"] = (
            "這台是 LPAR，共享 CPU 的實際可用量看的是 entitled capacity；"
            f"這個比值用邏輯 CPU 數（{ncpu}）當分母，**會低估壓力**。"
            "所以它不單獨亮紅燈，要跟 entitled capacity 一起看。")
        if base["load"]["level"] == "red":
            base["load"]["level"] = "yellow"

    base["mem"] = parse_aix_mem(seg.get("SVMON", ""), seg.get("PGSP", ""))
    base["disks"] = parse_aix_df(seg.get("DF", ""))
    base["readonly_mounts"] = parse_aix_readonly(seg.get("MOUNT", ""))
    base["net"] = parse_aix_netstat_in(seg.get("NETSTATI", ""))
    base["tcp"] = parse_tcp_states(seg.get("TCPSTATES", ""))

    subsys = parse_aix_subsystems(seg.get("SRC", ""))
    base["aix_subsystems"] = subsys
    # ⚠️ inoperative **不填進 failed_units**：那一欄在畫面上是紅燈條件，
    # 而 AIX 一台正常機器本來就有一堆 inoperative 的子系統（多半是刻意不啟用）。
    # 只有關鍵那幾個沒在跑才說話。
    base["failed_units"] = list(subsys["critical_down"])
    if subsys["inoperative"]:
        base["notes"].append(
            f"lssrc 有 {len(subsys['inoperative'])} 個子系統 inoperative。"
            "這跟 Linux 的 failed 不是同一件事——inoperative 只代表現在沒在跑，"
            "多數是刻意不啟用，所以這裡不當異常；只有關鍵子系統"
            f"（{'、'.join(AIX_CRITICAL_SUBSYS)}）沒在跑才會亮燈。")

    ntp = parse_aix_ntp(seg.get("NTPQ", ""), subsys)
    base["ntp_synced"] = ntp["synced"]
    base["aix_ntp"] = ntp
    if ntp["daemon_running"] and ntp["synced"] is None:
        base["notes"].append(
            "xntpd 有在跑，但 `ntpq -p` 沒有輸出，所以無法確認時間真的有同步。"
            "daemon 活著不等於跟上了來源——兩件事不一樣。")

    try:
        base["zombies"] = int((seg.get("ZOMB", "0").strip() or "0").splitlines()[0])
    except (ValueError, IndexError):
        base["zombies"] = 0
    # `grep -c '<defunct>'` 會把 grep 自己算進去，扣掉
    base["zombies"] = max(0, base["zombies"] - 1)
    base["top_cpu"] = parse_top(seg.get("TOPCPU", ""))
    try:
        base["logins"] = int((seg.get("WHO", "").strip() or "0").splitlines()[0])
    except (ValueError, IndexError):
        base["logins"] = None
    base["recent_logins"] = [ln.strip() for ln in seg.get("LAST", "").splitlines()
                             if ln.strip()][:6]

    # 監聽埠：復用 B-24 補好的 AIX netstat 解析，不另寫一份
    svc = None
    try:
        import service_collector

        def _runner(host, cmd):
            _ok, _out, _e = _ssh(host, cmd, key_path, account)
            return _out

        svc = service_collector.collect(_runner, ip, "aix")
    except Exception as exc:  # noqa: BLE001 - 少一塊證據，不是整台失敗
        base["notes"].append(f"服務／port 這次沒收到（{str(exc)[:80]}）")
    if svc:
        base["ports"] = svc.get("services", [])
        base["process_visible"] = svc.get("process_visible", False)

    levels = [d["level"] for d in base["disks"]]
    for k in ("cpu", "load", "mem", "uptime", "net", "tcp"):
        if base[k]:
            levels.append(base[k]["level"])
    if base["readonly_mounts"]:
        levels.append("red")
        base["notes"].append("有檔案系統被重掛成唯讀（磁碟可能出錯）")
    if base["failed_units"]:
        levels.append("red")
        base["notes"].append(
            f"關鍵子系統沒在跑：{'、'.join(base['failed_units'])}")
    if base["zombies"] >= ZOMBIE_YELLOW:
        levels.append("yellow")
    if base["ntp_synced"] is False:
        levels.append("yellow")
        base["notes"].append("`ntpq -p` 沒有任何來源標成同步中（時間會飄）")
    base["overall"] = _worst(*levels)

    got = {
        "os": bool(base["os"]), "uptime": base["uptime"] is not None,
        "cpu": base["cpu"] is not None, "mem": base["mem"] is not None,
        "disks": bool(base["disks"]), "mounts": bool(seg.get("MOUNT", "").strip()),
        "services": bool(seg.get("SRC", "").strip()), "ports": bool(svc),
        "time_sync": ntp["synced"] is not None,
    }
    base["coverage"] = _coverage(got, {
        "cpu": "`vmstat 1 2` 沒有回兩筆取樣（第一筆是開機以來的平均，不能當現值）",
        "mem": "`svmon -G` 沒有回 memory 那一行（這台可能沒有 svmon，或輸出格式不同）",
        "disks": "`df -k` 沒有回任何檔案系統",
        "mounts": "`mount` 沒有輸出",
        "services": "`lssrc -a` 沒有輸出",
        "ports": "服務／port 這次沒收到（見上面的 notes）",
        "time_sync": ("`ntpq -p` 沒有輸出，只知道 xntpd 這個 daemon 在不在跑——"
                      "**daemon 活著不等於時間有同步**，所以這一項算沒查到"),
    })
    base["complete"] = not base["coverage"]["missing"]
    base["took_ms"] = int((_dt.datetime.now() - _t0).total_seconds() * 1000)
    return base


def check_batch(ips: list[str], platform_of=None, concurrency: int = 8,
                key_path: str | None = None, account: str | None = None,
                _one=check_one, creds_of=None) -> list[dict]:
    """並發對多台健檢。concurrency 由呼叫端夾在上限內（見 api）。只打傳進來的已知 IP。

    `creds_of(ip)` 回 (帳號, 密碼)：Windows 走 WinRM 需要，Linux／AIX 用不到。
    做成呼叫端傳進來的函式而不是在這裡開 DB——**這支不該知道憑證存在哪裡**，
    那是 credential_store 的事；混進來之後這支就沒辦法單獨測試了。
    """
    from concurrent.futures import ThreadPoolExecutor

    seen, targets = set(), []
    for ip in ips:
        ip = (ip or "").strip()
        if ip and ip not in seen:
            seen.add(ip)
            targets.append(ip)
    if not targets:
        return []

    def _task(ip: str) -> dict:
        plat = (platform_of(ip) if platform_of else "linux") or "linux"
        return _one(ip, platform=plat, key_path=key_path, account=account,
                    creds=creds_of)

    workers = max(1, min(concurrency, len(targets)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(_task, targets))
    results.sort(key=lambda r: -_ORDER.get(r["overall"], 0))
    return results
