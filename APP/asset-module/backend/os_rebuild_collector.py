"""OS 重建資料收集：機房搬遷「某台機器死了，靠收集的資料把 OS 重建回來」。

2026-10-16 機房搬遷。OS 層唯讀收集（AP 不管），補 host_spec 沒收的重建資料：
LVM 藍圖、fstab、開機服務、排程、權限、網路持久設定、時間來源。只做 Linux。

## 安全設計（金融業，會被資安稽核）

1. **全部唯讀**：只讀檔、只跑查詢指令，不寫檔、不改設定、不裝工具、不重啟服務。
2. **兩段權限，root 那段沒有 shell**（2026-10-16 依資安要求改寫）：
   * 第一段 `NONROOT`：以收集帳號本身（非 root）跑。可以用 shell 迴圈讀**誰都讀得到**
     的設定檔——這段沒有任何提權，shell 語法不會擴大權限。
   * 第二段 sudo：**每一條都是「絕對路徑＋固定參數」的單一指令**（argv 清單），
     不經 `sh -c`、沒有管線／分號／重導向／萬用字元。檔名由 Python 先驗證字元再組進去。
     這樣 sudoers 才能一條一條精確白名單，收集帳號不會因為白名單裡有 shell 而拿到
     不受限的 root。需要的白名單見 `sudo_whitelist()`。截斷一律在 Python 端做。
3. **不收祕密本體**：不讀 shadow/gshadow/私鑰；authorized_keys 只跑 `ssh-keygen -lf`
   取指紋（輸出不含公鑰本體）；sudoers／NM keyfile／SNMP 的明文祕密由 `_mask` 遮掉。
4. **沿用現有收集管道**：同一個 SSH runner（`manage_state._runner_for`）、收集金鑰與帳號。
5. **能力探測，不猜發行版**：檔案／指令存不存在由探測決定，不靠 OS 名稱判斷。
6. **權限不足要分得出來**：sudo 被拒（`a password is required` 等）標「權限不足」，
   不可以被當成「此機未使用」——那會讓人以為查過了、本來就沒有。
7. AIX 這批不做（走 fcb_aix 單獨一批）；結構用 platform 分派，之後補 AIX 即可。
"""
from __future__ import annotations

import base64
import binascii
import re

# ---- 截斷上限 ----
#: 非 root 讀檔時來源端 head -c 的硬上限（bytes）。sudo 那段不能有管線，改在 Python 截。
_HARD_CAP = 262144
#: Python 端每檔保留上限。刻意 < _HARD_CAP，「超過就一定偵測得到並標截斷」。
_FILE_CAP = 65536
#: 單段指令輸出（sysctl -a 之類）的保留上限。
_TEXT_CAP = 131072
#: 每一類「逐檔 sudo 讀」最多讀幾個檔（cron spool、NM、authorized_keys…），防一台跑太久。
_MAX_FILES = 100


# =====================================================================
# 第一段：非 root（收集帳號本身）。可以用 shell，因為這段沒有提權。
# =====================================================================
def _catfiles(globs: str) -> str:
    """讀一組檔（非 root）。讀不到的檔標 `@@NOREAD <path>`，交給第二段用 sudo 逐檔讀。"""
    return (f'for f in {globs}; do [ -f "$f" ] || continue; '
            f'if [ -r "$f" ]; then echo "@@FILE $f"; head -c {_HARD_CAP} "$f" 2>/dev/null; '
            f'echo; echo "@@END"; else echo "@@NOREAD $f"; fi; done 2>/dev/null; true')


#: A11 自訂 systemd unit 不收的檔（2026-10-18 使用者：「Docker 可以整個不要」）。
#: 比對 /etc/systemd/system 底下每一段**名稱**（含 drop-in 目錄，例如 docker.service.d/）；
#: 命中的不讀、不列權限不足、連結也不列。要加別的就加在這裡（唯一一份，收集端與判讀端共用）。
SYSTEMD_UNIT_EXCLUDE: tuple[str, ...] = ("docker.*", "containerd.*")


def _systemd_find_excl() -> str:
    """SYSTEMD_UNIT_EXCLUDE → find 參數（`! -path '*/docker.*' ! -path '*/docker.*/*'` …）。"""
    return " ".join(f"! -path '*/{g}' ! -path '*/{g}/*'" for g in SYSTEMD_UNIT_EXCLUDE)


def systemd_excluded(path: str) -> bool:
    """/etc/systemd/system 底下的路徑（或 `a -> b` 連結行的左邊）是不是排除對象。"""
    import fnmatch

    left = (path or "").split(" -> ", 1)[0].strip()
    rel = left[len("/etc/systemd/system/"):] if left.startswith("/etc/systemd/system/") else left
    return any(fnmatch.fnmatchcase(seg, g) for seg in rel.split("/") if seg for g in SYSTEMD_UNIT_EXCLUDE)


#: 探測哪些工具／執行檔／目錄存在（決定第二段要不要跑、用哪個絕對路徑）。
_PROBE_TOOLS = ("findmnt lsblk systemctl timedatectl chronyc firewall-cmd sestatus getenforce "
                "lsmod trust dnf yum swapon ip getent blkid")
_PROBE_BINS = ("/usr/sbin/pvs /sbin/pvs /usr/sbin/vgs /sbin/vgs /usr/sbin/lvs /sbin/lvs "
               "/usr/sbin/blkid /sbin/blkid /usr/sbin/iptables-save /sbin/iptables-save "
               "/usr/bin/cat /bin/cat /usr/bin/ls /bin/ls /usr/bin/ssh-keygen "
               "/usr/local/libexec/webit3/account-facts /usr/local/libexec/webit3/rebuild-facts")
#: 非 root 列不出內容、要靠 sudo ls 才看得到檔名的目錄（固定清單，不接受外來路徑）。
ROOT_DIRS = ("/var/spool/cron", "/var/spool/cron/crontabs", "/etc/NetworkManager/system-connections")

NONROOT: dict[str, str] = {
    "probe": (f'for t in {_PROBE_TOOLS}; do command -v "$t" >/dev/null 2>&1 && echo "TOOL $t"; done; '
              f'for p in {_PROBE_BINS}; do [ -x "$p" ] && echo "BIN $p"; done; '
              f'for d in {" ".join(ROOT_DIRS)} /etc/lvm/backup; do [ -d "$d" ] && echo "DIR $d"; done; true'),
    # authorized_keys 擁有者的家目錄（getent 含 LDAP；/etc/passwd 無密碼雜湊）。只用來找路徑、不留存。
    "passwd": "getent passwd 2>/dev/null || cat /etc/passwd 2>/dev/null; true",
    # ---- 儲存／檔案系統 ----
    "findmnt": "LC_ALL=C findmnt -A -o TARGET,SOURCE,FSTYPE,OPTIONS 2>/dev/null; true",
    "mounts": "cat /proc/mounts 2>/dev/null; true",
    "fstab": _catfiles("/etc/fstab"),
    "blkid_user": "blkid 2>/dev/null; true",   # 非 root 版（快取），sudo 被拒時的退路
    "multipath_conf": _catfiles("/etc/multipath.conf /etc/multipath/bindings "
                                "/etc/multipath/wwids /etc/multipath/conf.d/*"),
    "lsblk_o": ("LC_ALL=C lsblk -O 2>/dev/null "
                "|| lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,WWN,SERIAL,ROTA,SCHED 2>/dev/null; true"),
    "swapon": "LC_ALL=C swapon --show 2>/dev/null; echo '@@PROC'; cat /proc/swaps 2>/dev/null; true",
    "kdump": _catfiles("/etc/kdump.conf /etc/sysconfig/kdump"),
    "exports": _catfiles("/etc/exports /etc/exports.d/*"),
    # ---- 開機／服務 ----
    "units_enabled": "systemctl list-unit-files --state=enabled --no-legend --no-pager 2>/dev/null; true",
    "units_running": ("systemctl list-units --type=service --state=running "
                      "--no-legend --no-pager 2>/dev/null; true"),
    "systemd_custom": (
        f'for f in $(find /etc/systemd/system -type f {_systemd_find_excl()} 2>/dev/null); do '
        f'if [ -r "$f" ]; then echo "@@FILE $f"; head -c {_HARD_CAP} "$f" 2>/dev/null; echo; echo "@@END"; '
        'else echo "@@NOREAD $f"; fi; done; '
        f'echo "@@LINKS"; find /etc/systemd/system -type l {_systemd_find_excl()} '
        '-printf "%p -> %l\\n" 2>/dev/null; true'),
    "rc_local": _catfiles("/etc/rc.local /etc/rc.d/rc.local"),
    "xinetd": _catfiles("/etc/xinetd.conf /etc/xinetd.d/*"),
    "cmdline": "cat /proc/cmdline 2>/dev/null; true",
    # ---- 排程 ----
    "cron_system": _catfiles("/etc/crontab /etc/cron.d/*"),
    "cron_periodic": _catfiles("/etc/cron.hourly/* /etc/cron.daily/* /etc/cron.weekly/* /etc/cron.monthly/*"),
    "timers": "systemctl list-timers --all --no-legend --no-pager 2>/dev/null; true",
    # ---- 帳號／權限 ----
    "groups": "getent group 2>/dev/null || cat /etc/group 2>/dev/null; true",
    "pam": _catfiles("/etc/pam.d/*"),
    "limits": _catfiles("/etc/security/limits.conf /etc/security/limits.d/*"),
    "selinux": ("sestatus 2>/dev/null; echo '@@GETENFORCE'; getenforce 2>/dev/null; "
                "echo '@@CONFIG'; cat /etc/selinux/config 2>/dev/null; true"),
    # ---- 網路（持久化設定）：RHEL ifcfg／Debian interfaces／Ubuntu netplan，存在才讀 ----
    "ifcfg": _catfiles("/etc/sysconfig/network-scripts/ifcfg-* /etc/netplan/*.yaml "
                       "/etc/network/interfaces /etc/network/interfaces.d/*"),
    "routes": ("ip route show table all 2>/dev/null; echo '@@PERSIST'; "
               + _catfiles("/etc/sysconfig/network-scripts/route-* /etc/sysconfig/static-routes")),
    "hosts": "cat /etc/hosts 2>/dev/null; true",
    "resolv": "cat /etc/resolv.conf 2>/dev/null; true",
    "nsswitch": "cat /etc/nsswitch.conf 2>/dev/null; true",
    "firewall": ("firewall-cmd --list-all --permanent 2>/dev/null; echo '@@RUNNING'; "
                 "firewall-cmd --list-all 2>/dev/null; true"),
    # ---- 核心參數 ----
    "sysctl_files": _catfiles("/etc/sysctl.conf /etc/sysctl.d/*"),
    "sysctl_runtime": "sysctl -a 2>/dev/null; true",
    "modules": "lsmod 2>/dev/null; echo '@@MODPROBE'; " + _catfiles("/etc/modprobe.d/* /etc/modules-load.d/*"),
    # ---- 時間／來源 ----
    "timezone": ("timedatectl show 2>/dev/null; echo '@@TZ'; cat /etc/timezone 2>/dev/null; "
                 "echo '@@LOCALTIME'; readlink -f /etc/localtime 2>/dev/null; true"),
    "ntp": ("echo '@@SOURCES'; chronyc -n sources 2>/dev/null; echo '@@CONF'; "
            + _catfiles("/etc/chrony.conf /etc/chrony/chrony.conf /etc/ntp.conf "
                        "/etc/ntp/ntp.conf /etc/ntpsec/ntp.conf")),
    "patchlevel": ("(dnf history list 2>/dev/null || yum history list 2>/dev/null "
                   "|| cat /var/log/apt/history.log 2>/dev/null) | head -c 131072; true"),
    "repos": _catfiles("/etc/yum.repos.d/*.repo /etc/apt/sources.list /etc/apt/sources.list.d/*"),
    "syslog": _catfiles("/etc/rsyslog.conf /etc/rsyslog.d/*"),
    "catrust": "trust list 2>/dev/null | head -c 131072; true",
}


# =====================================================================
# 第二段：sudo。每一條都是「絕對路徑＋固定參數」，argv 清單，沒有 shell。
# =====================================================================
#: 固定參數（寫進白名單的就是這幾串，一個字都不能多）。
SUDO_FIXED_ARGS: dict[str, list[str]] = {
    "pvs": ["--noheadings", "--units", "g", "-o",
            "pv_name,vg_name,pv_fmt,pv_attr,pv_size,pv_free,pv_uuid"],
    "vgs": ["--noheadings", "--units", "g", "-o",
            "vg_name,pv_count,lv_count,vg_attr,vg_size,vg_free,vg_extent_size,vg_uuid"],
    "lvs": ["-a", "--noheadings", "--units", "g", "-o",
            "lv_name,vg_name,lv_attr,lv_size,seg_count,lv_layout,stripes,devices,lv_uuid"],
    "blkid": [],
    "iptables-save": [],
    "account-facts": [],
}
#: 每個工具的候選絕對路徑（依探測結果取第一個存在的）。
SUDO_BINS: dict[str, tuple[str, ...]] = {
    "pvs": ("/usr/sbin/pvs", "/sbin/pvs"),
    "vgs": ("/usr/sbin/vgs", "/sbin/vgs"),
    "lvs": ("/usr/sbin/lvs", "/sbin/lvs"),
    "blkid": ("/usr/sbin/blkid", "/sbin/blkid"),
    "iptables-save": ("/usr/sbin/iptables-save", "/sbin/iptables-save"),
    "account-facts": ("/usr/local/libexec/webit3/account-facts",),
    "rebuild-facts": ("/usr/local/libexec/webit3/rebuild-facts",),
    "cat": ("/usr/bin/cat", "/bin/cat"),
    "ls": ("/usr/bin/ls", "/bin/ls"),
    "ssh-keygen": ("/usr/bin/ssh-keygen",),
}

#: Q78-SHRINK（2026-10-18）：唯讀輔助程式路徑。納管以 root 佈、內容固定、只收一個固定子指令，
#: 自己列舉固定目錄的檔、拒符號連結與逃出目錄者、Python 端再遮密碼。sudoers 一條子指令一行、無萬用字元。
REBUILD_HELPER_PATH = "/usr/local/libexec/webit3/rebuild-facts"
#: 子指令 → 固定目錄（helper 自己列舉，不收呼叫端路徑）。
REBUILD_HELPER_DIRS = {
    "lvm-backup": "/etc/lvm/backup",
    "nm-connections": "/etc/NetworkManager/system-connections",
    "netplan": "/etc/netplan",
}
#: yum-repos：repo 設定目錄（只收 *.repo）。redhat.repo 由 subscription-manager 建、只給 root 讀（2026-10-18 公司機）。
#: 這個子指令 v1.463 才加：之前納管的主機 sudoers 沒有這條，要重新納管一次才收得到。
REBUILD_REPO_DIRS = ("/etc/yum.repos.d", "/etc/zypp/repos.d")
REPO_HELPER_SUB = "yum-repos"
#: cron-spool 收這兩個目錄（檔名就是帳號名）。
REBUILD_CRON_DIRS = ("/var/spool/cron", "/var/spool/cron/crontabs")
#: sudo 段每個 token 只准這些字元：沒有空白、引號、$、`、*、?、|、;、&、<、>、括號。
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_./,:=@+-]+$")
#: 檔名（單一路徑段）：不准以 `.` 開頭（排除 `..`、隱藏檔、sudo 本來就略過的備份檔）。
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_@+-][A-Za-z0-9._@+-]*$")
#: 就算驗證過，這些也絕不讀（防禦縱深；正常流程本來就不會碰到）。
_NEVER_READ = re.compile(r"(?:^|/)(?:g?shadow-?|opasswd)$|/private/|\.key$|"
                         r"(?:^|/)id_(?:rsa|dsa|ecdsa|ed25519)$")

#: sudo -n 被拒的固定訊息（不同版本措辭略有差異，都收）。
_DENIED_RE = re.compile(r"(?i)a password is required|is not allowed to (?:execute|run)|"
                        r"not in the sudoers|may not run sudo|sorry, user|a terminal is required|"
                        r"no tty present")
_NOTFOUND_RE = re.compile(r"(?i)no such file|cannot access|command not found|not found")


def render_sudo(argv: list[str]) -> str | None:
    """argv → 實際送出的指令字串。任何一個 token 不合規就回 None（不送）。"""
    if not argv or not argv[0].startswith("/"):
        return None
    if not all(_SAFE_TOKEN.match(t or "") for t in argv):
        return None
    if any(".." in t for t in argv) or any(_NEVER_READ.search(t) for t in argv):
        return None
    return "sudo -n " + " ".join(argv)


def _safe_path(path: str) -> bool:
    """逐段驗證要用 sudo 讀的路徑：絕對路徑、每段都是安全檔名、不碰禁讀清單。"""
    if not path.startswith("/") or ".." in path or _NEVER_READ.search(path):
        return False
    return all(_SAFE_NAME.match(seg) for seg in path.strip("/").split("/"))


def _classify(out: str, err: str, rc, has_info: bool) -> str:
    """一次 sudo 呼叫的結果：ok／empty／denied／notfound／unknown／error。"""
    if _DENIED_RE.search(err or ""):
        return "denied"
    if (out or "").strip():
        return "ok"
    if _NOTFOUND_RE.search(err or ""):
        return "notfound"
    if not has_info:
        # runner 沒回傳 stderr/離開碼時，空輸出分不出是被拒還是真的沒東西——照實標不明
        return "unknown"
    return "empty" if rc in (0, None) and not (err or "").strip() else "error"


#: 先問收集帳號自己被放行了哪些指令。只列自己的權限，**不需要額外白名單**
#: （帳號規則全是 NOPASSWD 時 sudo -l 不需密碼），固定字串、沒有 shell。
#: 前面加 `LC_ALL=C`：中文系統（LANG=zh_TW）的 sudo -l 會印「可以在 … 上執行以下指令」，
#: 固定成英文輸出才好認（2026-10-18 221 本機就是 zh_TW，整段被判成「問不到放行清單」）。
#: 只影響 sudo 自己的訊息語系，不是 sudoers 規則、不會多放行任何東西；bash／sh／ksh 都吃這種前綴。
SUDO_LIST_CMD = "LC_ALL=C sudo -n -l"

#: 規則清單開頭那一行：英文（LC_ALL=C 時）＋中文（防禦：主機不吃 LC_ALL 時）。
#: en: "User x may run the following commands on h:"
#: zh_TW: "使用者 x 可以在 h 上執行以下指令："  zh_CN: "用户 x 可以在 h 上运行以下命令："
_SUDO_LIST_HDR = re.compile(r"may run the following commands|可以在.*上(?:執行|执行|運行|运行)以下(?:指令|命令)")


def _sudo_unescape(spec: str) -> str:
    r"""sudo -l 會把規則裡的特殊字元加反斜線印出：`""` 印成 `\"\"`、`,` 印成 `\,`、`=` 印成 `\=`、
    `:` 印成 `\:`。一律還原成 sudoers 原文（反斜線＋任一字元 → 該字元），比對才會對。
    2026-10-18 公司機實例：`/usr/sbin/blkid ""` 印成 `/usr/sbin/blkid \"\"`，舊版只還原 `\,`，
    所有「不准帶參數」的規則（blkid／iptables-save／account-facts）全被判成白名單沒有、根本沒送。"""
    return re.sub(r"\\(.)", r"\1", spec)


def parse_sudo_list(text: str) -> list[str] | None:
    """`sudo -n -l` 輸出 → 放行的指令規格清單（只取 runas 是 root／ALL 的）。認不出回 None。

    為什麼要先問：白名單沒放行的指令如果照送，每一條都會在目標機留下一筆
    「command not allowed」的 sudo 紀錄——上千台乘十幾條，就是 SOC 一整面的告警。
    先問清楚，沒放行的就不送，畫面照樣標「權限不足」。
    """
    if not text or not _SUDO_LIST_HDR.search(text):
        return None
    specs: list[str] = []
    started = False
    for ln in text.splitlines():
        if _SUDO_LIST_HDR.search(ln):
            started = True
            continue
        if not started or not ln.strip():
            continue
        m = re.match(r"^\s*\(([^)]*)\)\s*((?:[A-Z_]+:\s*)*)(.*)$", ln)
        if not m:
            continue
        runas = m.group(1).split(":")[0].strip()
        if runas not in ("root", "ALL"):
            continue
        # 指令以未跳脫的「, 」分隔；參數裡的逗號 sudo -l 會印成 `\,`，切完再還原跳脫
        for spec in re.split(r"(?<!\\),\s*", m.group(3).strip()):
            if spec.strip():
                specs.append(_sudo_unescape(spec.strip()))
    return specs


def sudo_allows(specs: list[str], argv: list[str]) -> bool:
    """sudoers 規則語意的比對：只寫路徑＝任何參數；`""`＝不准參數；其餘照萬用字元比對。
    `!` 開頭的否定規則只要命中就不送（保守）。"""
    import fnmatch

    actual = " ".join(argv[1:])
    allowed = False
    for spec in specs:
        neg = spec.startswith("!")
        s = spec[1:].strip() if neg else spec
        if s == "ALL":
            hit = True
        else:
            parts = s.split(None, 1)
            if not fnmatch.fnmatchcase(argv[0], parts[0]):
                continue
            if len(parts) == 1:
                hit = True
            elif parts[1].strip() == '""':
                hit = actual == ""
            else:
                hit = fnmatch.fnmatchcase(actual, parts[1].strip())
        if hit and neg:
            return False
        allowed = allowed or hit
    return allowed


def _sudo(runner, host: str, argv: list[str], specs: list[str] | None = None,
          list_status: str = "ok") -> dict:
    cmd = render_sudo(argv)
    if cmd is None:
        return {"status": "rejected", "out": "", "err": "參數未通過安全驗證，沒有送出", "cmd": None}
    if list_status != "ok":
        # 連 sudo -l 都問不到：不逐條硬送（避免一串被拒紀錄），照實標權限不足／不明
        return {"status": "denied" if list_status == "denied" else "unknown", "out": "",
                "err": "sudo -l 問不到放行清單，未送出", "cmd": cmd, "sent": False}
    if specs is not None and not sudo_allows(specs, argv):
        return {"status": "denied", "out": "", "cmd": cmd, "sent": False,
                "err": "收集帳號的 sudo 白名單沒有這條（sudo -l 未列出），未送出"}
    out = runner(host, cmd) or ""
    last = getattr(runner, "last", None)
    has_info = isinstance(last, dict)
    err = (last.get("err") or "") if has_info else ""
    rc = last.get("rc") if has_info else None
    status = _classify(out, err, rc, has_info)
    content, trunc = _cap_text(_mask(out), _FILE_CAP)
    return {"status": status, "out": content, "truncated": trunc,
            "err": (err or "")[:300], "cmd": cmd, "sent": True}


def _both(cmd: str) -> str:
    """同一條指令列出 /usr/sbin 與 /sbin 兩個路徑（distro 擺放不一）。
    參數裡的逗號要跳脫成 `\\,`（否則 visudo 讀成命令分隔），但分隔兩條路徑的 `, ` 是分隔符、不跳脫。"""
    esc = cmd.replace(",", "\\,")
    return f"/usr/sbin/{esc}, /sbin/{esc}"


def _fixed(args_key: str) -> str:
    return " ".join(SUDO_FIXED_ARGS[args_key])


# Q78-SHRINK（2026-10-18，決策取代臨時的 Q78-B）：所有「讀任意檔／可執行任意程式」的萬用字元規則
# 全部拿掉，改成①唯讀輔助程式的固定子指令（helper <sub>，一子指令一行）②dmidecode/vgs/lvs/pvs 改成
# 與收集端實際送出的參數「一字不差」的固定規則。sudoers 裡不再有任何萬用字元（smartctl 的 /dev/* 例外，
# 見下方 SMART_RULE 說明：那是裝置節點、非檔案，風險層級不同，另案處理）。
#: 唯讀輔助程式固定子指令：一行一個，sudoers 寫 `helper <sub>`（`<sub>` 是固定字、沒有萬用字元）。
HELPER_SUBCOMMANDS = ("dmi", "lvm-backup", "cron-spool", "nm-connections", "netplan", "authkeys-fp",
                      "config-audit", REPO_HELPER_SUB)

_H = REBUILD_HELPER_PATH
# vgs/lvs/pvs：列出收集端實際會送的每一種固定參數組合（host_spec 用一種 vgs；os_rebuild 用另一種 vgs＋lvs＋pvs）。
_VGS_HOSTSPEC = "--noheadings --units g -o vg_name,vg_size,vg_free"
REBUILD_SUDOERS: tuple[dict, ...] = (
    # ---- 唯讀輔助程式（取代所有 cat 萬用字元與 ssh-keygen -lf 萬用字元）----
    {"spec": f"{_H} dmi", "wildcard": False, "deploy": True,
     "note": "機型／序號（固定讀 /sys/class/dmi/id 幾個欄位；取代 cat /sys/class/dmi/id/*）"},
    {"spec": f"{_H} lvm-backup", "wildcard": False, "deploy": True,
     "note": "LVM metadata 原文（helper 自己列舉 /etc/lvm/backup；取代 cat .../backup/*）"},
    {"spec": f"{_H} cron-spool", "wildcard": False, "deploy": True,
     "note": "使用者 crontab（helper 自己列舉 /var/spool/cron[/crontabs]；取代 cat .../cron/*）"},
    {"spec": f"{_H} nm-connections", "wildcard": False, "deploy": True,
     "note": "NetworkManager 連線設定（helper 列舉固定目錄；取代 cat .../system-connections/*）"},
    {"spec": f"{_H} netplan", "wildcard": False, "deploy": True,
     "note": "Ubuntu netplan（helper 列舉 /etc/netplan；取代 cat /etc/netplan/*）"},
    {"spec": f"{_H} {REPO_HELPER_SUB}", "wildcard": False, "deploy": True,
     "note": "軟體來源 repo 設定（helper 列舉 /etc/yum.repos.d、/etc/zypp/repos.d 的 *.repo；密碼／token 收集端遮蔽）"},
    {"spec": f"{_H} authkeys-fp", "wildcard": False, "deploy": True,
     "note": "authorized_keys 指紋（helper 自己挑登入帳號、固定參數跑 ssh-keygen -lf；取代 ssh-keygen -lf *）"},
    # Q84（2026-10-18）：Linux 組態檢核要 root 才看得到的那幾項，由 helper 回「固定一組事實」：
    # 空白密碼帳號「名單」（不回雜湊）、稽核檔權限、auditd.conf、稽核規則、sshd 生效設定、
    # 一個固定的 sudoers.d 檔。檢核腳本本身仍以收集帳號（非 root）執行。
    {"spec": f"{_H} config-audit", "wildcard": False, "deploy": True,
     "note": "組態檢核 root 項目（固定事實：空白密碼帳號名單、稽核檔權限／設定／規則、sshd -T；不回密碼雜湊）"},
    # ---- dmidecode：改成只放行收集端實際送的三個 type（取代 dmidecode -t *，杜絕 --dump-bin 寫檔）----
    {"spec": _both("dmidecode -t 0") + ", " + _both("dmidecode -t 17") + ", " + _both("dmidecode -t 39"),
     "wildcard": False, "deploy": True, "note": "BIOS／DIMM／PSU（type 0／17／39，參數寫死）"},
    # ---- LVM 查詢工具：固定 -o（取代 vgs */lvs *）----
    {"spec": _both(f"vgs {_VGS_HOSTSPEC}"), "wildcard": False, "deploy": True,
     "note": "VG 容量（host_spec 規格頁用；固定 -o）"},
    {"spec": _both(f"vgs {_fixed('vgs')}"), "wildcard": False, "deploy": True,
     "note": "VG 完整欄位（OS 重建藍圖用；固定 -o）"},
    {"spec": _both(f"lvs {_fixed('lvs')}"), "wildcard": False, "deploy": True,
     "note": "LV 完整欄位（固定 -o）"},
    {"spec": _both(f"pvs {_fixed('pvs')}"), "wildcard": False, "deploy": True, "note": "PV 清單（固定 -o）"},
    # ---- 不帶參數的唯讀匯出 ----
    {"spec": '/usr/sbin/blkid "", /sbin/blkid ""', "wildcard": False, "deploy": True,
     "note": "磁碟 UUID／LABEL（不准帶任何參數）"},
    {"spec": '/usr/sbin/iptables-save "", /sbin/iptables-save ""', "wildcard": False, "deploy": True,
     "note": "防火牆規則（唯讀匯出；不准帶任何參數）"},
)

#: 2026-09-14 核准、非本次收斂範圍：磁碟健康掃描。`/dev/*` 是**裝置節點**（不是檔案路徑），
#: smartctl 不會把它當成任意檔讀出內容，風險層級與 cat 萬用字元不同。保留，另立 backlog 收斂。
SMART_RULE = ("/usr/sbin/smartctl -a /dev/*, /sbin/smartctl -a /dev/*, "
              "/usr/sbin/smartctl --scan, /usr/sbin/smartctl --scan-open, "
              "/sbin/smartctl --scan, /sbin/smartctl --scan-open")


def rebuild_sudoers_specs() -> list[str]:
    """納管腳本／劇本要佈的規則原文（不含帳號前綴）。全部無萬用字元。"""
    return [r["spec"] for r in REBUILD_SUDOERS if r["deploy"]]


def sudo_whitelist() -> list[dict]:
    """這個收集器會用到的 sudo 規則（給資安審）。

    kind：existing＝納管本來就佈、本次沒動的（帳號盤點輔助程式、smartctl 等）；
    deployed＝Q78-SHRINK 收斂後納管會佈的固定規則（無萬用字元）。
    """
    rows = [{"cmd": SUDO_BINS["account-facts"][0] + ' ""', "kind": "existing", "wildcard": False,
             "note": "帳號盤點輔助程式（本收集取 sudoers 段與 KEYS 計數）"},
            {"cmd": SMART_RULE, "kind": "existing", "wildcard": True,
             "note": "磁碟健康掃描（2026-09-14 核准）；/dev/* 是裝置節點、非檔案，另案收斂"}]
    for r in REBUILD_SUDOERS:
        rows.append({"cmd": r["spec"], "kind": "deployed", "wildcard": r["wildcard"], "note": r["note"]})
    for r in rows:
        r["sudoers"] = r["cmd"]
    return rows


# =====================================================================
# 祕密遮蔽
# =====================================================================
# 遮蔽要精準：不能把 nsswitch.conf 的 `passwd: files sss`（資料庫名）也遮掉。所以分兩條：
#   A) 賦值式 key=value——passwd 家族只在 `=` 時才遮；
#   B) 冒號式 key: value——只認強祕密字（`password` 全字，不認 passwd/shadow/group）。
_SECRET_EQ = re.compile(
    r"(?i)\b(password|passwd|passphrase|psk|secret|community|token|"
    r"auth[-_]?key|bind(?:pw|_?password)|rootpw|credential|api[-_]?key|"
    r"shared[-_]?secret|wep[-_]?key[0-9]?|private[-_]?key)\s*(=)\s*(\S+)")
_SECRET_COLON = re.compile(
    r"(?i)\b(password|passphrase|psk|secret|token|bindpw|credential|"
    r"api[-_]?key|shared[-_]?secret|private[-_]?key)\s*(:)\s*(\S+)")
_PRIVKEY_BLOCK = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.DOTALL)
_SECRET_PREFIXED = re.compile(
    r"(?im)\b([A-Za-z0-9.-]*_(?:password|passwd|token|secret|api[-_]?key))\s*=\s*(\S+)")
_URL_CRED = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")
#: 遮蔽後填入的記號（不用星號：本專案顯示字串不得含 markdown 強調）。
_REDACT = "[已遮蔽]"


def _mask(raw: str) -> str:
    """把明文祕密遮成「[已遮蔽]」，保留欄位名與「有設過」的事實。"""
    if not raw:
        return raw
    s = _PRIVKEY_BLOCK.sub("[私鑰本體已移除]", raw)
    # SNMP community：rocommunity public 198.51.100.0/24 → 遮第二欄
    s = re.sub(r"(?im)^(\s*r[ow]community6?\s+)(\S+)", r"\1" + _REDACT, s)
    s = re.sub(r"(?im)^(\s*com2sec\s+\S+\s+\S+\s+)(\S+)", r"\1" + _REDACT, s)
    s = _SECRET_EQ.sub(lambda m: f"{m.group(1)}{m.group(2)} {_REDACT}", s)
    # 前綴式祕密鍵（repo 檔的 proxy_password=、sslclientkey_password=、xxx_token= …）：\b 擋不到底線前綴
    s = _SECRET_PREFIXED.sub(lambda m: f"{m.group(1)}= {_REDACT}", s)
    # URL 內嵌帳密（baseurl=https://user:pass@host/…）：只遮 user:pass，主機與路徑留著
    s = _URL_CRED.sub(lambda m: f"{m.group(1)}{_REDACT}@", s)
    return _SECRET_COLON.sub(lambda m: f"{m.group(1)}{m.group(2)} {_REDACT}", s)


# =====================================================================
# 文字工具
# =====================================================================
def _cap_text(text: str, cap: int = _TEXT_CAP) -> tuple[str, bool]:
    b = (text or "").encode("utf-8", "replace")
    if len(b) <= cap:
        return (text or ""), False
    return b[:cap].decode("utf-8", "ignore") + f"\n…[已截斷：原始超過 {cap} bytes]", True


def _split_files(text: str) -> list[dict]:
    """`@@FILE <path> … @@END` 多檔輸出 → [{path, content, truncated}]（先遮密再截斷）。"""
    if not text:
        return []
    out: list[dict] = []
    cur: dict | None = None
    buf: list[str] = []

    def _flush():
        if cur is not None:
            content, trunc = _cap_text(_mask("\n".join(buf).strip("\n")), _FILE_CAP)
            cur["content"], cur["truncated"] = content, trunc
            out.append(cur)

    for ln in text.splitlines():
        if ln.startswith("@@FILE "):
            _flush()
            cur, buf = {"path": ln[7:].strip()}, []
        elif ln.startswith("@@"):
            _flush()
            cur, buf = None, []
        elif cur is not None:
            buf.append(ln)
    _flush()
    return [f for f in out if f.get("content")]


def _noread(text: str) -> list[str]:
    return [ln[9:].strip() for ln in (text or "").splitlines() if ln.startswith("@@NOREAD ")]


def _section(text: str, marker: str) -> tuple[str, str]:
    idx = (text or "").find(marker)
    if idx < 0:
        return (text or ""), ""
    return text[:idx], text[idx + len(marker):]


def parse_probe(text: str) -> dict:
    tools, bins, dirs = set(), set(), set()
    for ln in (text or "").splitlines():
        p = ln.split(None, 1)
        if len(p) != 2:
            continue
        {"TOOL": tools, "BIN": bins, "DIR": dirs}.get(p[0], set()).add(p[1].strip())
    return {"tools": sorted(tools), "bins": sorted(bins), "dirs": sorted(dirs)}


def _bin(probe: dict, name: str) -> str | None:
    have = set(probe.get("bins") or [])
    return next((b for b in SUDO_BINS[name] if b in have), None)


# =====================================================================
# 結構化 parser
# =====================================================================
def parse_groups(text: str) -> dict:
    """getent group → 群組清單（名稱/GID/成員）＋ GID 重複偵測。"""
    groups: list[dict] = []
    for ln in (text or "").splitlines():
        p = ln.split(":")
        if len(p) < 3 or not p[2].isdigit():
            continue
        members = [m for m in (p[3].split(",") if len(p) > 3 and p[3] else []) if m]
        groups.append({"name": p[0], "gid": int(p[2]), "members": members})
    seen: dict[int, list[str]] = {}
    for g in groups:
        seen.setdefault(g["gid"], []).append(g["name"])
    dups = [{"gid": gid, "names": names} for gid, names in seen.items() if len(names) > 1]
    return {"groups": groups, "gid_dups": dups}


def parse_enabled_running(enabled_text: str, running_text: str) -> dict:
    """開機 enabled 清單＋「在跑但沒設開機」交叉（重開機不會自己起來的服務）。"""
    enabled = []
    for ln in (enabled_text or "").splitlines():
        p = ln.split()
        if p:
            enabled.append({"unit": p[0], "state": p[1] if len(p) > 1 else None})
    names = {e["unit"] for e in enabled}
    running = [ln.split()[0] for ln in (running_text or "").splitlines()
               if ln.split() and ln.split()[0].endswith(".service")]
    return {"enabled": enabled, "running_not_enabled": sorted(r for r in running if r not in names),
            "running_count": len(running)}


def parse_authkeys_lines(user: str, path: str, text: str) -> list[dict]:
    """ssh-keygen -lf 輸出 → 指紋（`2048 SHA256:xx comment (RSA)`）。不含公鑰本體。"""
    out = []
    for ln in (text or "").splitlines():
        m = re.match(r"^\s*(\d+)\s+(\S+)\s+(.*?)\s*\(([^)]+)\)\s*$", ln)
        if m:
            out.append({"user": user, "file": path, "bits": int(m.group(1)),
                        "fingerprint": m.group(2),
                        "comment": (m.group(3) or "").strip() or None, "type": m.group(4)})
    return out


def parse_timezone(text: str) -> dict:
    pre, rest = _section(text, "@@TZ")
    tzfile, localtime = _section(rest, "@@LOCALTIME")
    out: dict = {}
    m = re.search(r"^Timezone=(.+)$", pre, re.MULTILINE)
    if m:
        out["timezone"] = m.group(1).strip()
    m = re.search(r"^NTPSynchronized=(.+)$", pre, re.MULTILINE)
    if m:
        out["ntp_synchronized"] = m.group(1).strip()
    if not out.get("timezone"):
        tzf = (tzfile or "").strip().splitlines()
        if tzf and tzf[0].strip():
            out["timezone"] = tzf[0].strip()
    lt = (localtime or "").strip()
    if lt:
        out["localtime_link"] = lt
        if not out.get("timezone"):
            m = re.search(r"zoneinfo/(.+)$", lt)
            if m:
                out["timezone"] = m.group(1)
    return out


def parse_ntp_sources(text: str) -> dict:
    """NTP 來源清單：chronyc sources 的伺服器＋設定檔裡的 server/pool/peer 行。"""
    sources_txt, conf_txt = _section(text, "@@CONF")
    live = []
    for ln in sources_txt.splitlines():
        # chronyc -n sources：`^* 192.0.2.1  ...` 第一欄是狀態符號、第二欄是位址
        m = re.match(r"^[\^=#+\-*?x~ ]{1,2}\s*([0-9A-Fa-f.:]+)\s", ln)
        if m and re.search(r"[.:]", m.group(1)):
            live.append(m.group(1))
    files = _split_files(conf_txt)
    conf = []
    for f in files:
        for ln in f["content"].splitlines():
            m = re.match(r"^\s*(server|pool|peer)\s+(\S+)", ln)
            if m:
                conf.append({"kind": m.group(1), "host": m.group(2), "file": f["path"]})
    return {"chrony_live_sources": sorted(set(live)), "config_servers": conf, "config_files": files}


def parse_swap(text: str) -> dict:
    live, proc = _section(text, "@@PROC")
    entries = []
    for ln in (proc or "").splitlines():
        p = ln.split()
        if len(p) >= 3 and p[0].startswith("/"):
            entries.append({"name": p[0], "type": p[1], "size_kb": p[2]})
    return {"swapon": (live or "").strip() or None, "entries": entries}


def _parse_lvm_rows(text: str, cols: list[str]) -> list[dict]:
    out = []
    n = len(cols)
    for ln in (text or "").splitlines():
        p = ln.split()
        if not p:
            continue
        if len(p) >= n:
            row = dict(zip(cols, p[:n - 1]))
            row[cols[n - 1]] = p[n - 1] if len(p) == n else " ".join(p[n - 1:])
        else:
            row = dict(zip(cols, p))
            row["_raw"] = ln.strip()
        out.append(row)
    return out


_PV_COLS = ["pv", "vg", "fmt", "attr", "size", "free", "uuid"]
_VG_COLS = ["vg", "pv_count", "lv_count", "attr", "size", "free", "ext_size", "uuid"]
_LV_COLS = ["lv", "vg", "attr", "size", "segs", "layout", "stripes", "devices", "uuid"]


def _helper_section(text: str, name: str) -> str | None:
    """帳號盤點輔助程式輸出（=== SHADOW/SUDOERS/KEYS/END）取一段。沒有 END＝沒跑完，回 None。
    只取需要的段；SHADOW 段（帳號狀態）**不留存**。"""
    if not text or "=== END" not in text:
        return None
    cur, buf = None, []
    for ln in text.splitlines():
        if ln.startswith("=== "):
            if cur == name:
                break
            cur = ln[4:].strip()
            continue
        if cur == name:
            buf.append(ln)
    return "\n".join(buf)


# =====================================================================
# 收集（兩段）
# =====================================================================
_LOGIN_SHELL_BAD = ("nologin", "false", "/sync", "shutdown", "halt")


def _login_users(passwd_text: str) -> list[tuple[str, str]]:
    out = []
    for ln in (passwd_text or "").splitlines():
        p = ln.split(":")
        if len(p) < 7:
            continue
        user, home, shell = p[0], p[5], p[6].strip()
        if not shell or any(b in shell for b in _LOGIN_SHELL_BAD):
            continue
        home = home.rstrip("/")
        # 驗家目錄本身；後面接的 `/.ssh/authorized_keys` 是程式寫死的固定字尾
        if _SAFE_NAME.match(user) and home and _safe_path(home):
            out.append((user, home))
    return out


#: ESXi 自我檢查（唯讀，跟納管探測同一組訊號）。
ESXI_PROBE_CMD = "uname -s 2>/dev/null; vmware -v 2>/dev/null || true"
ESXI_SKIP_REASON = "ESXi 不收（天條：ESXi 資訊只從 vCenter／RVTools 取）"

# =====================================================================
# Q78-SHRINK：唯讀輔助程式（取代所有 cat 萬用字元與 ssh-keygen -lf 萬用字元）
# =====================================================================
#: 由納管以 root 佈。只接受一個固定子指令、自己列舉固定目錄、拒符號連結與逃出目錄、每檔上限、
#: 輸出 base64（內容含 @@ 標記也不會被誤判）。sudoers 一子指令一行、完全沒有萬用字元。
#: 密碼遮蔽留在收集端（_mask），helper 不另外做，避免兩份遮蔽規則漂移。
REBUILD_HELPER_SCRIPT = r"""#!/bin/bash
# webit3 OS 重建唯讀輔助程式（Q78-SHRINK）。root 擁有、內容固定、只收一個固定子指令。
set -u
export LC_ALL=C
PATH=/usr/sbin:/usr/bin:/sbin:/bin
CAP=65536
MAX=100
[ "$#" -eq 1 ] || { echo "rebuild-facts: 只接受一個固定子指令" >&2; exit 2; }

emit_file() {  # $1=檔案路徑 $2=允許的實體目錄
  p=$1; base=$2
  [ -L "$p" ] && { echo "D $p"; return; }          # 符號連結一律不讀
  [ -f "$p" ] || { echo "D $p"; return; }           # 只讀普通檔
  rp=$(readlink -f "$p" 2>/dev/null) || { echo "D $p"; return; }
  case "$rp" in "$base"/*) : ;; *) echo "D $p"; return ;; esac   # 逃出目錄就不讀
  sz=$(wc -c < "$p" 2>/dev/null); sz=${sz:-0}
  tr=OK; [ "$sz" -gt "$CAP" ] && tr=TRUNC
  b=$(head -c "$CAP" "$p" 2>/dev/null | base64 2>/dev/null | tr -d '\n') || { echo "D $p"; return; }
  echo "F $p $tr $b"
}

list_dir() {  # $1=目錄（固定字面值，非呼叫端提供）$2=副檔名（選填，固定字面值）
  d=$1; ext=${2:-}
  [ -d "$d" ] || return 0
  base=$(readlink -f "$d" 2>/dev/null) || return 0
  n=0
  for p in "$d"/*"$ext"; do
    [ -e "$p" ] || continue
    n=$((n+1)); [ "$n" -gt "$MAX" ] && break
    emit_file "$p" "$base"
  done
}

case "$1" in
  lvm-backup)      list_dir /etc/lvm/backup ;;
  cron-spool)      list_dir /var/spool/cron; list_dir /var/spool/cron/crontabs ;;
  nm-connections)  list_dir /etc/NetworkManager/system-connections ;;
  netplan)         list_dir /etc/netplan ;;
  yum-repos)       list_dir /etc/yum.repos.d .repo; list_dir /etc/zypp/repos.d .repo ;;
  dmi)
    for k in product_name product_serial sys_vendor; do
      v=$(cat "/sys/class/dmi/id/$k" 2>/dev/null) && printf '%s=%s\n' "$k" "$v"
    done ;;
  authkeys-fp)
    kg=$(command -v ssh-keygen 2>/dev/null) || exit 0
    getent passwd 2>/dev/null | while IFS=: read -r u _ _ _ _ home shell; do
      case "$shell" in */nologin|*/false|/bin/sync|"") continue ;; esac
      ak="$home/.ssh/authorized_keys"
      [ -L "$ak" ] && { echo "KD $u"; continue; }
      [ -f "$ak" ] || continue
      out=$("$kg" -lf "$ak" 2>/dev/null) \
        && echo "K $u $(printf '%s' "$out" | base64 2>/dev/null | tr -d '\n')" \
        || echo "KD $u"
    done ;;
  config-audit)
    # Q84 組態檢核：每一項輸出 `@@C <key> <rc>`、內容（每行前面加 `| `，內容偽造不了標記）、`@@E <key>`。
    # 只回固定的一組事實，不收任何路徑參數。刻意不用 base64：目標機上不出現編碼／解碼動作（SOC 樣式）。
    # rc：0＝取得；97＝符號連結不讀；98＝不存在；其他＝指令本身的離開碼。
    rd() { [ -L "$1" ] && return 97; [ -f "$1" ] || return 98; head -c "$CAP" "$1"; }
    put() {
      k=$1; shift
      o=$("$@" 2>/dev/null); rc=$?
      printf '@@C %s %s\n' "$k" "$rc"
      [ -n "$o" ] && printf '%s\n' "$o" | head -c "$CAP" | sed 's/^/| /'
      printf '@@E %s\n' "$k"
    }
    shadow_empty() { [ -L /etc/shadow ] && return 97; awk -F: '$2==""{print $1}' /etc/shadow; }
    rules_d() {
      [ -d /etc/audit/rules.d ] || return 98
      for f in /etc/audit/rules.d/*.rules; do
        [ -f "$f" ] && [ ! -L "$f" ] && head -c "$CAP" "$f"
      done; return 0
    }
    put shadow_empty shadow_empty
    put ls_auditd_conf ls -ld /etc/audit/auditd.conf
    put ls_audit_log ls -ld /var/log/audit/audit.log
    put ls_audit_dir ls -ld /var/log/audit
    put ls_audit_rules ls -ld /etc/audit/rules.d/audit.rules
    put auditd_conf rd /etc/audit/auditd.conf
    put auditctl_l auditctl -l
    put rules_d rules_d
    put sudoers_stig rd /etc/sudoers.d/99-stig-password-policy
    put sshd_t sshd -T
    put sshd_config rd /etc/ssh/sshd_config
    ;;
  *) echo "rebuild-facts: 未知子指令 $1" >&2; exit 2 ;;
esac
"""


def _helper_run(runner, host: str, sub: str) -> tuple[str, str]:
    """跑 `sudo -n <helper> <sub>`，回 (輸出, 整體狀態)。狀態用 _classify（分得出被拒／不明）。"""
    cmd = f"sudo -n {REBUILD_HELPER_PATH} {sub}"
    out = runner(host, cmd) or ""
    last = getattr(runner, "last", None)
    has_info = isinstance(last, dict)
    err = (last.get("err") or "") if has_info else ""
    rc = last.get("rc") if has_info else None
    return out, _classify(out, err, rc, has_info)


def _b64(s: str) -> str:
    try:
        return base64.b64decode(s).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return ""


def _helper_file_records(text: str) -> tuple[dict[str, dict], list[str]]:
    """helper 檔案輸出 → ({path: {out, truncated}}, 被拒路徑清單)。"""
    files: dict[str, dict] = {}
    denied: list[str] = []
    for ln in (text or "").splitlines():
        parts = ln.split(" ", 3)
        if parts[0] == "F" and len(parts) == 4:
            content, trunc = _cap_text(_mask(_b64(parts[3])), _FILE_CAP)
            files[parts[1]] = {"out": content, "truncated": trunc or parts[2] == "TRUNC"}
        elif parts[0] == "D" and len(parts) >= 2:
            denied.append(parts[1])
    return files, denied


def _ingest_dir(sudo: dict, text: str, status: str, base_dir: str, set_ls: bool) -> None:
    """把 helper 的目錄輸出塞進 sudo（跟直接 cat 同一套鍵）：cat:<path>＝ok，被拒的記 denied；
    需要 ls:<dir> 狀態的項目（cron/NM）另外補一筆。整段被拒時 ls 標 denied、不留假 cat 筆。"""
    if set_ls:
        sudo[f"ls:{base_dir}"] = {"status": "ok" if status == "ok" else status, "out": ""}
    if status != "ok":
        return
    files, denied = _helper_file_records(text)
    for path, rec in files.items():
        sudo[f"cat:{path}"] = {"status": "ok", "out": rec["out"],
                               "truncated": rec["truncated"], "cmd": None, "sent": True}
    for path in denied:
        sudo[f"cat:{path}"] = {"status": "denied", "out": "",
                               "err": "helper 認定不可讀（符號連結或逃出目錄）", "cmd": None}


def _ingest_authkeys(sudo: dict, text: str, status: str, allow: set | None) -> None:
    """helper authkeys-fp 輸出 → keyfp:<user>（跟直接 ssh-keygen 同一套鍵）。"""
    if status != "ok":
        return
    for ln in (text or "").splitlines():
        parts = ln.split(" ", 2)
        user = parts[1] if len(parts) >= 2 else ""
        if not user or (allow is not None and user not in allow):
            continue
        ak = f"~{user}/.ssh/authorized_keys"
        if parts[0] == "K" and len(parts) == 3:
            sudo[f"keyfp:{user}"] = {"status": "ok", "out": _b64(parts[2]),
                                     "cmd": f"{REBUILD_HELPER_PATH} authkeys-fp {ak}"}
        elif parts[0] == "KD":
            sudo[f"keyfp:{user}"] = {"status": "denied", "out": "",
                                     "cmd": f"{REBUILD_HELPER_PATH} authkeys-fp {ak}"}


def collect_rebuild(runner, host: str, platform: str) -> dict:
    """跑兩段唯讀收集、整理成重建資料。非 Linux 回 skipped（AIX 單獨一批）。"""
    if platform != "linux":
        return {"platform": platform, "data": None, "raw": {},
                "skipped": f"平台 {platform or '未知'} 這批不收（AIX 走 fcb_aix 單獨一批，只做 Linux）"}
    # ESXi 天條（2026-10-17）：資料庫登記可能是錯的，所以連上之後、跑任何收集指令之前再驗一次本機。
    # 是 ESXi（uname=VMkernel 或 vmware -v）就整台不收，連 sudo -l 都不送。
    import onboard_engine

    if onboard_engine.is_esxi_output(runner(host, ESXI_PROBE_CMD) or ""):
        return {"platform": "esxi", "data": None, "raw": {}, "skipped": ESXI_SKIP_REASON}
    nonroot = {k: (runner(host, cmd) or "") for k, cmd in NONROOT.items()}
    probe = parse_probe(nonroot["probe"])
    sudo: dict[str, dict] = {}

    # 先問放行清單；問不到就整段不送（見 parse_sudo_list 的說明）
    list_out = runner(host, SUDO_LIST_CMD) or ""
    last = getattr(runner, "last", None)
    list_err = (last or {}).get("err") or "" if isinstance(last, dict) else ""
    specs = parse_sudo_list(list_out)
    if specs is not None:
        list_status = "ok"
    elif _DENIED_RE.search(list_err):
        list_status = "denied"
    else:
        list_status = "unknown"
    sudo_list = {"status": list_status, "rules": specs or [], "err": list_err[:300]}

    def run(key: str, argv: list[str]):
        sudo[key] = _sudo(runner, host, argv, specs, list_status)

    # 固定參數的單一指令
    for name in ("pvs", "vgs", "lvs", "blkid", "iptables-save", "account-facts"):
        b = _bin(probe, name)
        if b:
            run(name, [b, *SUDO_FIXED_ARGS[name]])
        else:
            sudo[name] = {"status": "no_tool", "tool": name, "out": "", "err": "", "cmd": None}

    cat, ls_, keygen = _bin(probe, "cat"), _bin(probe, "ls"), _bin(probe, "ssh-keygen")
    helper = _bin(probe, "rebuild-facts")

    # authorized_keys 指紋：只問「帳號盤點輔助程式說有 key」的使用者
    keys_sec = _helper_section(sudo["account-facts"].get("out", ""), "KEYS")
    has_keys = None
    if keys_sec is not None:
        has_keys = {ln.split()[1] for ln in keys_sec.splitlines()
                    if len(ln.split()) == 3 and ln.split()[0] == "KEYS" and ln.split()[2] != "0"}

    # Q78-SHRINK：這台已重新納管（sudoers 有 helper 子指令、無 cat 萬用字元）就一律走 helper，
    # 完全不送任何 cat／ls／ssh-keygen 萬用字元指令。還沒重新納管的舊機（sudo -l 沒有 helper）
    # 退回原本的直接 cat／ls／ssh-keygen（靠它們既有的萬用字元 sudoers），不會新增失敗。
    helper_mode = bool(helper) and specs is not None and sudo_allows(specs, [helper, "lvm-backup"])

    if helper_mode:
        for sub, base in REBUILD_HELPER_DIRS.items():
            out, st = _helper_run(runner, host, sub)
            _ingest_dir(sudo, out, st, base, set_ls=(sub == "nm-connections"))
        out, st = _helper_run(runner, host, "cron-spool")
        _ingest_dir(sudo, out, st, REBUILD_CRON_DIRS[0], set_ls=True)
        for d in REBUILD_CRON_DIRS[1:]:
            sudo[f"ls:{d}"] = {"status": "ok" if st == "ok" else st, "out": ""}
        out, st = _helper_run(runner, host, "authkeys-fp")
        _ingest_authkeys(sudo, out, st, has_keys)
    else:
        # ---- 舊機退路（未重新納管）：沿用萬用字元 sudoers ----
        vg_names = [r.get("vg") for r in _parse_lvm_rows(sudo["vgs"].get("out", ""), _VG_COLS)]
        if cat:
            for vg in [v for v in vg_names if v and _SAFE_NAME.match(v)][:_MAX_FILES]:
                run(f"cat:/etc/lvm/backup/{vg}", [cat, f"/etc/lvm/backup/{vg}"])
        for d in ROOT_DIRS:
            if d not in probe["dirs"] or not ls_:
                continue
            run(f"ls:{d}", [ls_, "-1", d])
            if sudo[f"ls:{d}"]["status"] != "ok" or not cat:
                continue
            names = [n.strip() for n in sudo[f"ls:{d}"]["out"].splitlines() if n.strip()]
            for n in [n for n in names if _SAFE_NAME.match(n) and n != "crontabs"][:_MAX_FILES]:
                run(f"cat:{d}/{n}", [cat, f"{d}/{n}"])
        if cat:
            for text in nonroot.values():
                for path in _noread(text)[:_MAX_FILES]:
                    if _safe_path(path):
                        run(f"cat:{path}", [cat, path])
        if keygen:
            for user, home in _login_users(nonroot["passwd"])[:_MAX_FILES]:
                if has_keys is None or user in has_keys:
                    run(f"keyfp:{user}", [keygen, "-lf", f"{home}/.ssh/authorized_keys"])

    # repo 設定（v1.463）：helper 有放行 yum-repos 才送；沒放行（之前納管的主機）不送，
    # 記 not_allowed，畫面講「需重新納管」，不是籠統的 sudo 被拒。
    if list_status != "ok":
        sudo[f"helper:{REPO_HELPER_SUB}"] = {"status": list_status, "out": ""}
    elif helper and sudo_allows(specs, [helper, REPO_HELPER_SUB]):
        out, st = _helper_run(runner, host, REPO_HELPER_SUB)
        sudo[f"helper:{REPO_HELPER_SUB}"] = {"status": st, "out": ""}
        _ingest_dir(sudo, out, st, REBUILD_REPO_DIRS[0], set_ls=False)
    else:
        sudo[f"helper:{REPO_HELPER_SUB}"] = {"status": "not_allowed", "out": "",
                                             "err": "收集帳號的 sudoers 沒有這條（之前納管的主機），重新納管後就有"}

    # account-facts 的輸出含 SHADOW 段（帳號狀態），落庫前只留 SUDOERS 段
    af = sudo["account-facts"]
    if af.get("status") == "ok":
        sec = _helper_section(af.get("out", ""), "SUDOERS")
        if sec is None:
            af.update(status="error", out="", err="輔助程式輸出沒有結尾標記（沒跑完）")
        else:
            af["out"] = _cap_text(_mask(sec), _FILE_CAP)[0]
            if not sec.strip():
                af["status"] = "empty"

    raw = {"nonroot": {k: _cap_text(_mask(v), _HARD_CAP)[0] for k, v in nonroot.items()
                       if k != "passwd"},          # passwd 只拿來找家目錄，不留存
           "probe": probe, "sudo": sudo, "sudo_list": sudo_list}
    return {"platform": platform, "data": build_rebuild_data(raw), "raw": raw}


# =====================================================================
# 整理成呈現用的項目（收集與將來重判讀共用；純文字進、結構出）
# =====================================================================
ITEMS: list[dict] = [
    # 儲存／檔案系統
    {"id": "storage.pv_vg_lv", "group": "儲存／檔案系統", "title": "PV／VG／LV 完整對應與大小", "nec": "A"},
    {"id": "storage.lvm_metadata", "group": "儲存／檔案系統", "title": "LVM metadata 原文（重建 LV 藍圖）", "nec": "A"},
    {"id": "storage.vg_pp_mirror", "group": "儲存／檔案系統", "title": "VG PE size／鏡像配置", "nec": "A"},
    {"id": "storage.filesystems", "group": "儲存／檔案系統", "title": "filesystem 類型／掛載參數", "nec": "A"},
    {"id": "storage.fstab", "group": "儲存／檔案系統", "title": "fstab 原文", "nec": "A"},
    {"id": "storage.blkid", "group": "儲存／檔案系統", "title": "UUID／LABEL 對照", "nec": "A"},
    {"id": "storage.multipath", "group": "儲存／檔案系統", "title": "multipath bindings／multipath.conf", "nec": "A"},
    {"id": "storage.disk_attr", "group": "儲存／檔案系統", "title": "磁碟屬性（lsblk -O）", "nec": "A"},
    {"id": "storage.swap", "group": "儲存／檔案系統", "title": "swap／paging space", "nec": "B"},
    {"id": "storage.kdump", "group": "儲存／檔案系統", "title": "dump device（kdump）", "nec": "B"},
    {"id": "storage.nfs_exports", "group": "儲存／檔案系統", "title": "NFS exports", "nec": "B"},
    # 開機／服務
    {"id": "boot.enabled_services", "group": "開機／服務", "title": "開機 enabled 服務清單", "nec": "A"},
    {"id": "boot.running_not_enabled", "group": "開機／服務", "title": "在跑但沒設開機（交叉）", "nec": "A"},
    {"id": "boot.systemd_custom", "group": "開機／服務", "title": "自訂 systemd unit／drop-in", "nec": "A"},
    {"id": "boot.rc_local", "group": "開機／服務", "title": "rc.local／收尾腳本", "nec": "B"},
    {"id": "boot.xinetd", "group": "開機／服務", "title": "inetd／xinetd", "nec": "B"},
    {"id": "boot.cmdline", "group": "開機／服務", "title": "bootloader kernel 參數", "nec": "B"},
    # 排程
    {"id": "cron.user", "group": "排程", "title": "使用者 crontab", "nec": "A"},
    {"id": "cron.system", "group": "排程", "title": "系統 crontab（/etc/crontab、cron.d）", "nec": "A"},
    {"id": "cron.timers", "group": "排程", "title": "systemd timer", "nec": "A"},
    {"id": "cron.periodic", "group": "排程", "title": "cron.{hourly,daily…} 自訂腳本", "nec": "B"},
    # 帳號／權限
    {"id": "acct.groups", "group": "帳號／權限", "title": "群組清單 GID／成員", "nec": "A"},
    {"id": "acct.gid_dups", "group": "帳號／權限", "title": "GID 重複偵測", "nec": "A"},
    {"id": "acct.sudoers", "group": "帳號／權限", "title": "sudoers 原文（已遮密）", "nec": "A"},
    {"id": "acct.authkeys", "group": "帳號／權限", "title": "authorized_keys 指紋（不含公鑰本體）", "nec": "A"},
    {"id": "acct.pam", "group": "帳號／權限", "title": "PAM", "nec": "B"},
    {"id": "acct.limits", "group": "帳號／權限", "title": "ulimit／limits", "nec": "B"},
    {"id": "acct.selinux", "group": "帳號／權限", "title": "SELinux 模式／自訂", "nec": "B"},
    # 網路
    {"id": "net.ifcfg", "group": "網路", "title": "持久介面設定檔", "nec": "A"},
    {"id": "net.routes", "group": "網路", "title": "靜態路由（含持久設定）", "nec": "A"},
    {"id": "net.hosts", "group": "網路", "title": "/etc/hosts", "nec": "A"},
    {"id": "net.resolv", "group": "網路", "title": "DNS resolver", "nec": "A"},
    {"id": "net.nsswitch", "group": "網路", "title": "名稱服務順序（nsswitch）", "nec": "B"},
    {"id": "net.firewall", "group": "網路", "title": "防火牆（firewalld＋iptables）", "nec": "B"},
    {"id": "net.sysctl_net", "group": "網路", "title": "網路／TCP tunables（sysctl.d）", "nec": "B"},
    # 核心參數
    {"id": "kernel.sysctl", "group": "核心參數", "title": "核心參數（runtime sysctl -a）", "nec": "B"},
    {"id": "kernel.modules", "group": "核心參數", "title": "載入的核心模組／自訂", "nec": "B"},
    # 時間／來源
    {"id": "time.timezone", "group": "時間／來源", "title": "timezone", "nec": "A"},
    {"id": "time.ntp", "group": "時間／來源", "title": "NTP 來源清單", "nec": "A"},
    {"id": "time.patchlevel", "group": "時間／來源", "title": "patch level", "nec": "B"},
    {"id": "time.repos", "group": "時間／來源", "title": "repo／軟體來源", "nec": "B"},
    {"id": "time.syslog", "group": "時間／來源", "title": "syslog 轉送目的地", "nec": "B"},
    {"id": "time.catrust", "group": "時間／來源", "title": "CA 自訂憑證（不含私鑰）", "nec": "B"},
]

#: 狀態與畫面文案（翻譯表只有一份，放後端）。
STATE_LABEL = {
    "collected": "已收集",
    "empty": "此機未使用／無此檔",
    "denied": "權限不足（sudo 被拒）",
    "no_tool": "未收集（此機沒有這個指令）",
    "unknown": "不明（沒有錯誤訊息可判斷）",
    "not_run": "未收集（沒跑過這條）",
}
#: denied 裡「檔案只給 root 讀、沒送 sudo」那種（例如自訂 systemd unit 0600）：不是 sudo 被拒。
STATE_LABEL_FILE_DENIED = "權限不足（檔案只給 root 讀）"
#: denied 裡「這台納管時還沒有這條唯讀規則」那種（v1.463 新增的 yum-repos）：重新納管一次就有。
STATE_LABEL_REONBOARD = "權限不足（需重新納管）"
#: deny_kind → 畫面文案（sudo＝白名單沒放行／被擋；file＝只給 root 讀；reonboard＝需重新納管）
DENY_KIND_LABEL = {"sudo": STATE_LABEL["denied"], "file": STATE_LABEL_FILE_DENIED,
                   "reonboard": STATE_LABEL_REONBOARD}

#: 項目沒有輸出時，「需要的工具一個都不在」就是 no_tool（不是此機未使用）。
_ITEM_TOOLS = {
    "boot.enabled_services": ["systemctl"], "boot.running_not_enabled": ["systemctl"],
    "cron.timers": ["systemctl"], "storage.disk_attr": ["lsblk"],
    "kernel.modules": ["lsmod"], "time.catrust": ["trust"],
    "acct.groups": ["getent"], "acct.gid_dups": ["getent"],
}


def _nr(raw: dict, key: str) -> str | None:
    """取非 root 段的輸出；該鍵沒收過回 None（→ not_run）。"""
    nonroot = raw.get("nonroot")
    if not isinstance(nonroot, dict) or key not in nonroot:
        return None
    return nonroot.get(key) or ""


def _sudo_files(raw: dict, prefix: str, sent_only: bool = False) -> tuple[list[dict], list[str], bool]:
    """sudo 逐檔讀的結果 → (讀到的檔, 被拒的路徑, 有沒有任何一筆是不明)。
    sent_only=True：「被拒」只算真的送出去、被 sudo 擋回來的（沒送的＝白名單本來就沒有讀檔規則）。"""
    files, denied, unknown = [], [], False
    for key, r in (raw.get("sudo") or {}).items():
        if not key.startswith("cat:" + prefix):
            continue
        path = key[4:]
        st = r.get("status")
        if st == "ok":
            files.append({"path": path, "content": r.get("out", ""),
                          "truncated": bool(r.get("truncated")), "via": "sudo"})
        elif st in ("denied", "rejected"):
            if not sent_only or r.get("sent", True):
                denied.append(path)
        elif st in ("unknown", "error"):
            unknown = True
    return files, denied, unknown


def _state_from(content, denied: list[str], unknown: bool, unreadable: list[str] | None = None,
                **payload) -> dict:
    """有內容＝collected（有被拒的另列 denied_paths）；沒內容時「被拒」優先於「未使用」。

    unreadable＝檔案本身只給 root 讀、收集帳號讀不到，而且**沒有送 sudo**（沒有對應的讀檔規則；
    Q78-SHRINK 後本來就不再佈 cat 萬用字元）。跟「sudo 被拒」分開講，畫面才不會誤導成白名單問題。"""
    out = dict(payload)
    unreadable = unreadable or []
    if denied:
        out["denied_paths"] = denied
    if unreadable:
        out["unreadable_paths"] = unreadable
    if content:
        out["state"] = "collected"
        notes = []
        if denied:
            notes.append(f"有 {len(denied)} 個檔權限不足（sudo 被拒）沒讀到")
        if unreadable:
            notes.append(f"有 {len(unreadable)} 個檔權限不足（檔案只給 root 讀，收集帳號讀不到）沒讀到")
        if notes:
            out["note"] = "，".join(notes) + "，見清單"
    elif denied or unreadable:
        out["state"] = "denied"
        if not denied:
            out["deny_kind"] = "file"
    elif unknown:
        out["state"] = "unknown"
    else:
        out["state"] = "empty"
    return out


def _file_item(raw: dict, key: str, extra_prefixes: tuple[str, ...] = (), exclude=None) -> dict:
    """非 root 讀檔＋讀不到的改走 sudo。回 state 與檔案清單。exclude(path)→True 的檔整個略過。"""
    text = _nr(raw, key)
    if text is None:
        return {"state": "not_run"}
    files = [f for f in _split_files(text) if not (exclude and exclude(f["path"]))]
    denied: list[str] = []
    unreadable: list[str] = []
    unknown = False
    for path in _noread(text):
        if exclude and exclude(path):
            continue
        got, den, unk = _sudo_files(raw, path, sent_only=True)
        files += got
        denied += den
        # 讀不到、也沒真的送 sudo（白名單沒有讀檔規則、路徑沒過驗證、沒有 cat）→ 權限不足，
        # 原因是「檔案只給 root 讀」，不是 sudo 擋；不可當成沒有
        if not (got or den or unk):
            unreadable.append(path)
        unknown = unknown or unk
    for pre in extra_prefixes:
        got, den, unk = _sudo_files(raw, pre)
        sent_den = _sudo_files(raw, pre, sent_only=True)[1]
        files += got
        denied += sent_den
        # 沒送出去的（白名單沒有讀檔規則）＝檔案只給 root 讀，不是 sudo 擋
        unreadable += [p for p in den if p not in sent_den]
        unknown = unknown or unk
    seen: set = set()
    files = [f for f in files if not (f["path"] in seen or seen.add(f["path"]))]  # 同檔只留一份
    denied = list(dict.fromkeys(denied))
    unreadable = [p for p in dict.fromkeys(unreadable) if p not in denied]
    return _state_from(files, denied, unknown, unreadable,
                       files=files, truncated=any(f.get("truncated") for f in files))


def _sudo_one(raw: dict, key: str) -> dict:
    r = (raw.get("sudo") or {}).get(key)
    return r if r is not None else {"status": "not_run", "out": ""}


def _sudo_state(r: dict) -> str:
    return {"ok": "collected", "empty": "empty", "notfound": "empty", "denied": "denied",
            "rejected": "denied", "no_tool": "no_tool", "unknown": "unknown",
            "error": "unknown", "not_run": "not_run"}.get(r.get("status"), "unknown")


def _text_item(raw: dict, *keys: str) -> dict:
    parts = []
    for k in keys:
        t = _nr(raw, k)
        if t is None:
            return {"state": "not_run"}
        if t.strip():
            parts.append(t.strip() if len(keys) == 1 else f"# {k}\n{t.strip()}")
    content, trunc = _cap_text(_mask("\n\n".join(parts)))
    return {"state": "collected" if content.strip() else "empty", "text": content, "truncated": trunc}


def _item_lvm(raw: dict, iid: str) -> dict:
    rs = {n: _sudo_one(raw, n) for n in ("pvs", "vgs", "lvs")}
    names = ("pvs", "vgs", "lvs") if iid == "storage.pv_vg_lv" else ("vgs", "lvs")
    sts = {n: _sudo_state(rs[n]) for n in names}
    val = {"vgs": _parse_lvm_rows(rs["vgs"].get("out", ""), _VG_COLS),
           "lvs": _parse_lvm_rows(rs["lvs"].get("out", ""), _LV_COLS)}
    if iid == "storage.pv_vg_lv":
        val["pvs"] = _parse_lvm_rows(rs["pvs"].get("out", ""), _PV_COLS)
    denied = [n for n in names if sts[n] == "denied"]
    if any(sts[n] == "collected" for n in names):
        st = "collected"
    elif denied:
        st = "denied"
    else:
        st = next((s for s in ("no_tool", "unknown", "not_run") if s in sts.values()), "empty")
    out = {"state": st, **val}
    if denied:
        out["denied_paths"] = [f"sudo {n}" for n in denied]
        if st == "collected":
            out["note"] = "、".join(denied) + " 權限不足（sudo 被拒）沒收到"
    return out


def _item_firewall(raw: dict) -> dict:
    t = _nr(raw, "firewall")
    if t is None:
        return {"state": "not_run"}
    ipt = _sudo_one(raw, "iptables-save")
    ist = _sudo_state(ipt)
    parts = []
    if t.replace("@@RUNNING", "").strip():
        parts.append("# firewalld（permanent；@@RUNNING 之後是 running）\n" + t.strip())
    if ist == "collected":
        parts.append("# iptables-save\n" + ipt.get("out", ""))
    content, trunc = _cap_text(_mask("\n\n".join(parts)))
    out = {"text": content, "truncated": trunc}
    if content.strip():
        out["state"] = "collected"
        if ist == "denied":
            out["note"] = "iptables-save 權限不足（sudo 被拒）沒收到"
            out["denied_paths"] = ["sudo iptables-save"]
    elif ist == "denied":
        out["state"] = "denied"
        out["denied_paths"] = ["sudo iptables-save"]
    elif ist == "no_tool" and "firewall-cmd" not in ((raw.get("probe") or {}).get("tools") or []):
        out["state"] = "no_tool"
    else:
        out["state"] = "unknown" if ist == "unknown" else "empty"
    return out


def _item_blkid(raw: dict) -> dict:
    r = _sudo_one(raw, "blkid")
    st = _sudo_state(r)
    if st == "collected":
        return {"state": "collected", "text": r.get("out", "")}
    user = _nr(raw, "blkid_user")
    if user and user.strip():
        out = {"state": "collected", "text": _mask(user.strip()),
               "note": "這是非 root 的快取版 blkid，可能不完整"}
        if st == "denied":
            out["note"] = "root 版 blkid 權限不足（sudo 被拒），這是非 root 的快取版，可能不完整"
            out["denied_paths"] = ["sudo blkid"]
        return out
    if user is None and st == "not_run":
        return {"state": "not_run"}
    if st == "denied":
        return {"state": "denied", "denied_paths": ["sudo blkid"]}
    return {"state": st if st not in ("not_run",) else "empty"}


def _item_cron_user(raw: dict) -> dict:
    if "probe" not in raw:
        return {"state": "not_run"}
    sudo = raw.get("sudo") or {}
    files, denied, unk = [], [], False
    for d in ROOT_DIRS[:2]:
        ls_r = sudo.get(f"ls:{d}")
        if ls_r and ls_r.get("status") in ("denied", "rejected"):
            denied.append(d)
        if ls_r and ls_r.get("status") in ("unknown", "error"):
            unk = True
        got, den, u = _sudo_files(raw, d + "/")
        files += [f for f in got if f["path"].count("/") == d.count("/") + 1]
        denied += [p for p in den if p.count("/") == d.count("/") + 1]
        unk = unk or u
    return _state_from(files, denied, unk, files=files,
                       truncated=any(f.get("truncated") for f in files))


def _item_authkeys(raw: dict) -> dict:
    if "probe" not in raw:
        return {"state": "not_run"}
    keys, denied, unk = [], [], False
    for key, r in (raw.get("sudo") or {}).items():
        if not key.startswith("keyfp:"):
            continue
        user, st = key[6:], r.get("status")
        if st == "ok":
            keys += parse_authkeys_lines(user, (r.get("cmd") or "").split()[-1], r.get("out", ""))
        elif st in ("denied", "rejected"):
            denied.append(f"{user} 的 authorized_keys")
        elif st in ("unknown", "error"):
            unk = True
    return _state_from(keys, denied, unk, keys=keys)


def _item_sudoers(raw: dict) -> dict:
    r = _sudo_one(raw, "account-facts")
    st = _sudo_state(r)
    if st == "collected":
        return {"state": "collected", "text": r.get("out", ""),
                "note": "/etc/sudoers＋/etc/sudoers.d/*，由帳號盤點輔助程式合併輸出（不分檔）"}
    if st == "no_tool":
        return {"state": "no_tool", "tool": "account-facts",
                "note": "這台沒有帳號盤點輔助程式（納管時佈署），重新納管後就有"}
    if st == "denied":
        return {"state": "denied", "denied_paths": ["sudo account-facts"]}
    return {"state": st}


def _item_ifcfg(raw: dict) -> dict:
    out = _file_item(raw, "ifcfg", extra_prefixes=("/etc/NetworkManager/system-connections/",
                                                   "/etc/netplan/"))
    ls_r = (raw.get("sudo") or {}).get("ls:/etc/NetworkManager/system-connections")
    if ls_r and ls_r.get("status") in ("denied", "rejected"):
        out.setdefault("denied_paths", []).append("/etc/NetworkManager/system-connections")
        if out["state"] == "empty":
            out["state"] = "denied"
        elif out["state"] == "collected":
            out["note"] = "NetworkManager 連線設定權限不足（sudo 被拒）沒讀到"
    return out


def _in_repo_dir(path: str) -> bool:
    return any(path.startswith(d + "/") for d in REBUILD_REPO_DIRS)


def _item_repos(raw: dict) -> dict:
    """B17 repo／軟體來源：誰都讀得到的直接讀；只給 root 讀的（redhat.repo）由 helper yum-repos 補。
    helper 沒放行（之前納管的主機）→ 講「需重新納管」；helper 送了被擋 → 才是 sudo 被拒。"""
    out = _file_item(raw, "repos")
    h = (raw.get("sudo") or {}).get(f"helper:{REPO_HELPER_SUB}") or {}
    hst = h.get("status")
    stuck = [p for p in out.get("unreadable_paths", []) if _in_repo_dir(p)]
    if not stuck or hst is None:
        return out
    rest = [p for p in out["unreadable_paths"] if p not in stuck]
    if hst == "not_allowed":
        out["reonboard_paths"] = stuck
        kind = "reonboard"
    elif hst in ("denied", "rejected"):
        out["denied_paths"] = out.get("denied_paths", []) + stuck
        kind = "sudo"
    else:
        return out          # ok 但 helper 判定不可讀（符號連結等）／不明：維持「只給 root 讀」
    if rest:
        out["unreadable_paths"] = rest
    else:
        out.pop("unreadable_paths", None)
    if out["state"] == "denied":
        if out.get("denied_paths"):
            out.pop("deny_kind", None)              # 有真的被 sudo 擋的 → 歸 sudo 被拒
        elif not rest:
            out["deny_kind"] = "reonboard"          # 全部都是「重新納管就有」
    elif out["state"] == "collected":
        msg = (f"有 {len(stuck)} 個檔要重新納管一次才收得到（之前納管的主機沒有這條唯讀規則）"
               if kind == "reonboard" else f"有 {len(stuck)} 個檔權限不足（sudo 被拒）沒讀到")
        out["note"] = msg + "，見清單"
    return out


def _item_parsed(raw: dict, iid: str) -> dict | None:
    """非 root、有專屬 parser 的項目。不屬於這類回 None。"""
    if iid in ("boot.enabled_services", "boot.running_not_enabled"):
        en, ru = _nr(raw, "units_enabled"), _nr(raw, "units_running")
        if en is None or ru is None:
            return {"state": "not_run"}
        r = parse_enabled_running(en, ru)
        if iid == "boot.enabled_services":
            return {"state": "collected" if r["enabled"] else "empty", "enabled": r["enabled"]}
        return {"state": "collected" if ru.strip() else "empty",
                "running_not_enabled": r["running_not_enabled"], "running_count": r["running_count"]}
    if iid in ("acct.groups", "acct.gid_dups"):
        t = _nr(raw, "groups")
        if t is None:
            return {"state": "not_run"}
        g = parse_groups(t)
        key = "groups" if iid == "acct.groups" else "gid_dups"
        return {"state": "collected" if g["groups"] else "empty", key: g[key]}
    if iid == "storage.swap":
        t = _nr(raw, "swapon")
        if t is None:
            return {"state": "not_run"}
        sw = parse_swap(t)
        return {"state": "collected" if (sw["swapon"] or sw["entries"]) else "empty", **sw}
    if iid == "time.timezone":
        t = _nr(raw, "timezone")
        if t is None:
            return {"state": "not_run"}
        tz = parse_timezone(t)
        return {"state": "collected" if tz.get("timezone") else "empty", **tz}
    if iid == "time.ntp":
        t = _nr(raw, "ntp")
        if t is None:
            return {"state": "not_run"}
        nt = parse_ntp_sources(t)
        return {"state": "collected" if (nt["chrony_live_sources"] or nt["config_servers"]) else "empty", **nt}
    if iid == "net.routes":
        t = _nr(raw, "routes")
        if t is None:
            return {"state": "not_run"}
        live, persist = _section(t, "@@PERSIST")
        files = _split_files(persist)
        content, trunc = _cap_text(_mask((live or "").strip()))
        return {"state": "collected" if (content.strip() or files) else "empty",
                "live": content, "persist_files": files,
                "truncated": trunc or any(f.get("truncated") for f in files)}
    if iid == "boot.systemd_custom":
        t = _nr(raw, "systemd_custom")
        if t is None:
            return {"state": "not_run"}
        # Docker／containerd 的 unit 不收（SYSTEMD_UNIT_EXCLUDE）：舊收集原文裡有的也在這裡濾掉，
        # 不算進「讀到的檔」也不算進「權限不足」
        out = _file_item(raw, "systemd_custom", exclude=systemd_excluded)
        _, links = _section(t, "@@LINKS")
        out["links"] = [ln for ln in links.splitlines() if ln.strip() and not systemd_excluded(ln)]
        if out["links"] and out["state"] == "empty":
            out["state"] = "collected"
        return out
    return None


#: 單純「非 root 讀檔」的項目 → 對應 NONROOT 鍵。
_FILE_ITEMS = {
    "storage.fstab": "fstab", "storage.multipath": "multipath_conf", "storage.kdump": "kdump",
    "storage.nfs_exports": "exports", "boot.rc_local": "rc_local", "boot.xinetd": "xinetd",
    "cron.system": "cron_system", "cron.periodic": "cron_periodic", "acct.pam": "pam",
    "acct.limits": "limits", "net.sysctl_net": "sysctl_files", "time.syslog": "syslog",
}
#: 單純「非 root 單段文字」的項目 → 對應 NONROOT 鍵（可多個）。
_TEXT_ITEMS = {
    "storage.filesystems": ("findmnt", "mounts"), "storage.disk_attr": ("lsblk_o",),
    "boot.cmdline": ("cmdline",), "cron.timers": ("timers",), "acct.selinux": ("selinux",),
    "net.hosts": ("hosts",), "net.resolv": ("resolv",), "net.nsswitch": ("nsswitch",),
    "kernel.sysctl": ("sysctl_runtime",), "kernel.modules": ("modules",),
    "time.patchlevel": ("patchlevel",), "time.catrust": ("catrust",),
}


def _build_item(item: dict, raw: dict) -> dict:
    iid = item["id"]
    if iid in ("storage.pv_vg_lv", "storage.vg_pp_mirror"):
        return _item_lvm(raw, iid)
    if iid == "storage.lvm_metadata":
        files, denied, unk = _sudo_files(raw, "/etc/lvm/backup/")
        vst = _sudo_state(_sudo_one(raw, "vgs"))
        if not files and not denied and vst in ("denied", "no_tool", "unknown", "not_run"):
            out = {"state": vst, "files": []}
            if vst == "denied":
                out["note"] = "VG 清單權限不足（sudo 被拒），無法得知 metadata 檔名"
                out["denied_paths"] = ["sudo vgs"]
            return out
        return _state_from(files, denied, unk, files=files,
                           truncated=any(f.get("truncated") for f in files))
    special = {"storage.blkid": _item_blkid, "cron.user": _item_cron_user,
               "acct.authkeys": _item_authkeys, "acct.sudoers": _item_sudoers,
               "net.ifcfg": _item_ifcfg, "net.firewall": _item_firewall, "time.repos": _item_repos}
    if iid in special:
        return special[iid](raw)
    parsed = _item_parsed(raw, iid)
    if parsed is not None:
        return parsed
    if iid in _FILE_ITEMS:
        return _file_item(raw, _FILE_ITEMS[iid])
    if iid in _TEXT_ITEMS:
        return _text_item(raw, *_TEXT_ITEMS[iid])
    return {"state": "unknown"}


def build_rebuild_data(raw: dict) -> dict:
    """原文 → 分組項目清單。每一項都帶 state 與 label，**沒有靜默留白**。"""
    raw = raw or {}
    tools = set((raw.get("probe") or {}).get("tools") or [])
    by_group: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    deny_kinds: dict[str, int] = {}     # 權限不足的原因分布（sudo／file／reonboard），畫面摘要用
    a_collected = 0
    for item in ITEMS:
        built = _build_item(item, raw)
        need = _ITEM_TOOLS.get(item["id"])
        if built["state"] == "empty" and need and raw.get("probe") and not (tools & set(need)):
            built["state"] = "no_tool"
            built["tool"] = need[0]
        if built["state"] == "denied":
            built.setdefault("deny_kind", "sudo")
            deny_kinds[built["deny_kind"]] = deny_kinds.get(built["deny_kind"], 0) + 1
        built.update({"id": item["id"], "group": item["group"], "title": item["title"],
                      "necessity": item["nec"],
                      "label": (DENY_KIND_LABEL.get(built["deny_kind"], STATE_LABEL["denied"])
                                if built["state"] == "denied"
                                else STATE_LABEL.get(built["state"], built["state"]))})
        counts[built["state"]] = counts.get(built["state"], 0) + 1
        if item["nec"] == "A" and built["state"] == "collected":
            a_collected += 1
        by_group.setdefault(item["group"], []).append(built)
    return {"groups": [{"group": g, "items": its} for g, its in by_group.items()],
            "summary": {"total": len(ITEMS),
                        "total_a": sum(1 for i in ITEMS if i["nec"] == "A"),
                        "total_b": sum(1 for i in ITEMS if i["nec"] == "B"),
                        "a_collected": a_collected, "by_state": counts,
                        # sudo -l 問得到放行清單嗎（ok／denied／unknown；沒收過為 None）
                        "sudo_list": (raw.get("sudo_list") or {}).get("status"),
                        "collected": counts.get("collected", 0),
                        "denied": counts.get("denied", 0),
                        "denied_by_kind": deny_kinds}}


# =====================================================================
# 落庫與批次收集
# =====================================================================
def _any_rebuild(res: dict) -> bool:
    data = res.get("data")
    return bool(data) and data["summary"]["collected"] > 0


def store_rebuild(conn, asset_serial: str, ip: str, res: dict) -> None:
    import json

    conn.execute("""
        CREATE TABLE IF NOT EXISTS host_rebuild (
            asset_serial TEXT PRIMARY KEY, ip TEXT, platform TEXT,
            data_json TEXT, raw_json TEXT, collected_at TEXT)""")
    conn.execute(
        "INSERT INTO host_rebuild (asset_serial, ip, platform, data_json, raw_json, collected_at) "
        "VALUES (?,?,?,?,?, datetime('now','localtime')) "
        "ON CONFLICT(asset_serial) DO UPDATE SET ip=excluded.ip, platform=excluded.platform, "
        "data_json=excluded.data_json, raw_json=excluded.raw_json, "
        "collected_at=datetime('now','localtime')",
        (asset_serial, ip, res["platform"],
         json.dumps(res.get("data") or {}, ensure_ascii=False),
         json.dumps(res.get("raw") or {}, ensure_ascii=False)),
    )
    conn.commit()


def run_collection(conn, trigger: str = "manual", only_serial: str | None = None) -> dict:
    """對已納管（collect_ok=1）的 Linux／Windows 主機逐台收。沿用 host_spec 的目標、平台判定、runner。"""
    import manage_state

    q, _p = manage_state.collect_targets_sql(
        conn, only_serial,
        "SELECT asset_serial, ip, os, is_vm, device_model, hostname, asset_name, asset_purpose FROM hardware "
        "WHERE collect_ok = 1 AND ip IS NOT NULL AND ip != ''")
    targets = conn.execute(q, list(_p)).fetchall()
    ok, failed, unsupported, skipped = [], [], [], []
    import onboard_eligibility

    esxi_names = onboard_eligibility.esxi_host_names(conn)
    if only_serial and not targets:
        why = manage_state.why_not_collectable(conn, only_serial)
        if why:
            skipped.append({"asset_serial": only_serial, "reason": why})
    for t in targets:
        ip, serial = t["ip"], t["asset_serial"]
        got = onboard_eligibility.judge_for_asset(conn, dict(t), esxi_names=esxi_names)
        if got and got[0] == "esxi":
            unsupported.append({"asset_serial": serial, "ip": ip, "reason": ESXI_SKIP_REASON})
            continue
        platform = manage_state.collect_platform_of(conn, ip, t["os"], asset_serial=serial)
        if platform == "windows":
            # Windows（2026-10-20）：WinRM PSRP 收，同一套項目模型、同一張表（win_rebuild_collector）
            try:
                import win_rebuild_collector
                res, why = win_rebuild_collector.collect_for(conn, serial, ip)
            except Exception as exc:  # noqa: BLE001 — 單台失敗不中斷整批
                res, why = None, str(exc)[:200]
            if res is not None and res.get("data"):
                store_rebuild(conn, serial, ip, res)      # 全部權限不足也存：畫面要逐項講「權限不足」
            if res is None or not res["data"]["summary"]["a_collected"]:
                failed.append({"asset_serial": serial, "ip": ip,
                               "error": why or ("必要項一項都沒收到——WinRM 連得上但查詢被拒或沒回資料"
                                                "（收集帳號可能不是系統管理員，見資產頁 OS 重建分頁逐項原因）")})
                continue
            s = res["data"]["summary"]
            ok.append({"asset_serial": serial, "ip": ip, "collected": s["collected"],
                       "a_collected": s["a_collected"], "denied": s["denied"]})
            continue
        if platform != "linux":
            unsupported.append({"asset_serial": serial, "ip": ip,
                                "reason": "AIX 走 fcb_aix 單獨一批（這批只做 Linux／Windows）"})
            continue
        runner = None
        try:
            import root_key
            runner = manage_state._runner_for(
                ip, manage_state.COLLECTOR_KEY_DEFAULT,
                account=root_key.account_for(conn, serial, platform))
            res = collect_rebuild(runner, ip, platform)
            if not _any_rebuild(res):
                _save_trace(conn, runner, serial, ip)
                failed.append({"asset_serial": serial, "ip": ip, "error": _why_empty(conn, serial)})
                continue
            store_rebuild(conn, serial, ip, res)
            s = res["data"]["summary"]
            ok.append({"asset_serial": serial, "ip": ip, "collected": s["collected"],
                       "a_collected": s["a_collected"], "denied": s["denied"]})
            _save_trace(conn, runner, serial, ip)
        except Exception as exc:  # noqa: BLE001 — 單台失敗不中斷整批
            failed.append({"asset_serial": serial, "ip": ip, "error": str(exc)[:200]})
            _save_trace(conn, runner, serial, ip)
    return {"total": len(targets), "ok": len(ok), "failed": len(failed),
            "unsupported": len(unsupported), "ok_detail": ok[:50],
            "fail_detail": failed[:20], "unsupported_detail": unsupported[:20],
            "skipped": skipped}


def _save_trace(conn, run, serial: str, ip: str) -> None:
    if not hasattr(run, "save"):
        return
    try:
        run.save(conn, serial, ip)
    except Exception:  # noqa: BLE001 — 診斷寫不進去不可以反過來害收集失敗
        pass


def _why_empty(conn, asset_serial: str) -> str:
    import manage_state
    try:
        row = conn.execute("SELECT * FROM collect_ssh_trace WHERE asset_serial = ?",
                           (asset_serial,)).fetchone()
    except Exception:  # noqa: BLE001
        row = None
    v = manage_state.trace_verdict(row)
    return ("一項 OS 重建資料都沒收到——指令沒跑起來，不是這台沒有這些設定。"
            + v.get("text", ""))[:400]
