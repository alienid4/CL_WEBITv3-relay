"""帳號盤點採集：一台主機上有哪些帳號、誰有特權、密碼狀態如何。

跟 facts_collector／service_collector 同一套模式（runner 可注入、per-platform 指令集）。

## ⚠️ 權限現實：沒有 root 只拿得到一半

| 欄位 | 來源 | 需要 root？ |
|---|---|---|
| 帳號清單、UID、GID、shell、家目錄 | `/etc/passwd` | ✗ |
| 群組成員（含 wheel/sudo 特權群組） | `/etc/group` | ✗ |
| 最後登入 | `lastlog`／`last` | ✗（檔案通常 644）|
| **密碼到期、上次改密碼** | `chage -l <user>` | ✅（查別人要 root）|
| **空密碼／鎖定狀態** | `passwd -S <user>` | ✅ |
| **sudo 權限明細、NOPASSWD** | `sudo -l -U`、`/etc/sudoers.d/*` | ✅ |
| **authorized_keys（免密碼登入者）** | `~/.ssh/authorized_keys` | ✅（讀別人家目錄）|

收不到的一律留 None 並在 `needs_root` 標明，**絕不用推測填充**——
「密碼到期日不明」跟「密碼永不過期」是完全相反的稽核結論，猜錯比留白危險得多。

## 為什麼建議的 sudo 白名單不含 `cat /etc/shadow`

`chage -l` 給的是日期，`passwd -S` 給的是狀態碼，兩者都**不吐密碼雜湊**；
直接開放讀 shadow 等於把全機密碼雜湊送出去給人離線破解。
拿得到同樣的稽核結論就不該要更大的權限——見 SUDO_RULES。
"""
from __future__ import annotations

import re

# ===== 需 root 的欄位：固定內容的唯讀輔助程式（2026-09-11 取代舊的萬用字元白名單）=====
#
# 舊版建議的白名單是一條條帶 `*` 的指令（`passwd -S *`、`cat /home/*/.ssh/authorized_keys`）。
# **sudoers 的 `*` 會吃空白**，所以 `cat /home/a/.ssh/authorized_keys /etc/shadow
# /home/b/.ssh/authorized_keys` 符合那條規則——收集帳號就讀得到全機密碼雜湊；
# `passwd -S *` 也能在後面多塞別的選項。那份建議從未佈到任何主機，但照原樣佈就是實質漏洞。
#
# 改法：root 在納管時放一支**內容固定、只有 root 能改、不接受任何參數**的程式，
# sudo 只允許「不帶參數執行這一支」（sudoers 的 `""` ＝禁止任何參數）。
# 一句話講得清楚為什麼安全：root 只執行佈署當下就固定下來的程式，呼叫端傳不進任何東西。
#
# 它只輸出三樣：passwd -S 狀態碼與 chage -l 日期（都不含密碼雜湊）、sudoers 內容
# （本身不含機密）、每個帳號授權金鑰的**數量**（不含金鑰內容）。
# 輸出格式刻意跟既有的三支解析器對齊，解析不另寫一套。
ACCOUNT_HELPER_PATH = "/usr/local/libexec/webit3/account-facts"

ACCOUNT_HELPER_SCRIPT = r"""#!/bin/bash
# webit3 帳號盤點唯讀輔助程式——由納管腳本以 root 佈署，內容固定、不接受任何參數。
# 只輸出：密碼狀態碼與效期日期、sudoers 內容、每個帳號的授權金鑰「數量」。
# 不輸出密碼雜湊、不輸出金鑰內容、不讀任何呼叫端給的路徑。
set -u
export LC_ALL=C
PATH=/usr/sbin:/usr/bin:/sbin:/bin
if [ "$#" -ne 0 ]; then echo "account-facts: 不接受任何參數" >&2; exit 2; fi

# 名稱服務（NSS）可能掛住：nsswitch.conf 寫了 sss／ldap 但那個服務沒在跑時，
# getent 會一直等 socket，整個收集就卡死而且看不出原因。
# 2026-09-17 在 221 實際踩到：passwd: sss files systemd，但 sssd 沒啟動，
# getent passwd 回 0 筆並卡超過 60 秒，害部署前的測試整個逾時。
#
# 所以：先用 timeout 試一次完整查詢；沒回應就退回只讀本機檔（-s files），
# 並在輸出裡標明是退回來的——**不可以靜默少掉一批帳號**，那會讓稽核報告漏人。
NSS_NOTE=""
_pw() { getent passwd; }
if command -v timeout >/dev/null 2>&1; then
  if ! timeout 15 getent passwd 2>/dev/null | grep -q .; then
    if getent -s files passwd 2>/dev/null | grep -q .; then
      NSS_NOTE="名稱服務(NSS)沒有回應，已退回只讀本機 /etc/passwd；網域帳號可能沒列進來"
      _pw() { getent -s files passwd; }
    else
      NSS_NOTE="名稱服務(NSS)沒有回應，連本機 /etc/passwd 也讀不到"
    fi
  fi
fi
if [ -n "$NSS_NOTE" ]; then echo "=== NOTE"; echo "$NSS_NOTE"; fi

echo "=== SHADOW"
_pw | cut -d: -f1 | while IFS= read -r u; do
  s=$(passwd -S "$u" 2>/dev/null)
  c=$(chage -l "$u" 2>/dev/null | tr '\n' '|')
  [ -n "$s$c" ] && echo "ACCT $u :: $s :: $c"
done
echo "=== SUDOERS"
cat /etc/sudoers 2>/dev/null
for f in /etc/sudoers.d/*; do
  case "$(basename "$f")" in *~|*.*) continue ;; esac   # sudo 本身也略過這兩種檔名
  [ -f "$f" ] && cat "$f" 2>/dev/null
done
echo "=== KEYS"
_pw | while IFS=: read -r u _ _ _ _ home _; do
  f="$home/.ssh/authorized_keys"
  if [ -f "$f" ]; then
    n=$(grep -cvE '^[[:space:]]*(#|$)' "$f" 2>/dev/null)
    echo "KEYS $u ${n:-0}"
  else
    echo "KEYS $u 0"
  fi
done
echo "=== END"
"""

# 建議佈署的 sudo 規則（納管腳本會照這份寫進 /etc/sudoers.d/webit3scan）。
# 通用版；實際佈署用 sudo_rules_for(os_info) 對目標機路徑。
SUDO_RULES = rf"""# webit3 收集帳號的唯讀 sudo 規則
# 1) 讀機型與序號（product_serial 0400，只有 root 讀得到）
webit3scan ALL=(root) NOPASSWD: /usr/bin/cat /sys/class/dmi/id/*
# 2) 帳號盤點：只准「不帶參數」執行固定內容的輔助程式（"" ＝禁止任何參數）
webit3scan ALL=(root) NOPASSWD: {ACCOUNT_HELPER_PATH} ""
# 3) 值班健檢的兩項唯讀日誌（使用者 2026-09-22：「要 root，就 sudo 給權限」）。
#    **參數寫死**：tail 綁 -n 200 與固定路徑、dmesg 綁固定 level。
#    不可以放寬成 /usr/bin/tail 或 /usr/bin/cat——那等於能讀系統上任何檔案。
webit3scan ALL=(root) NOPASSWD: /usr/bin/tail -n 200 /var/log/secure
webit3scan ALL=(root) NOPASSWD: /usr/bin/tail -n 200 /var/log/auth.log
# sudoers 的 `,` 是命令清單分隔符，出現在參數裡要跳脫成 `\\,`——
# 不跳脫 visudo 會讀成「規則到 err 結束，另有一條叫 crit 的命令」而擋下整份，
# 而且納管會靜默還原成舊的（2026-09-22 在 221 真機踩到）。
webit3scan ALL=(root) NOPASSWD: /usr/bin/dmesg --level=err\,crit
# 4) 監聽 port 的行程名（值班要知道哪個服務占哪個 port）。ss -tlnp 的 -p 需要 root。
#    參數寫死 -tlnp，路徑兩種都列（RHEL 在 /usr/sbin、Debian 在 /usr/bin）。
webit3scan ALL=(root) NOPASSWD: /usr/sbin/ss -tlnp, /usr/bin/ss -tlnp
"""

# 系統預設帳號：這些是「本來就存在」的，不該被當成有人偷開的帳號，
# 但 root/admin/guest 這類**可登入的**預設帳號仍是稽核項目（R9）。
SYSTEM_ACCOUNTS = {
    "bin", "daemon", "adm", "lp", "sync", "shutdown", "halt", "mail", "operator",
    "games", "ftp", "nobody", "systemd-network", "systemd-resolve", "dbus",
    "polkitd", "sshd", "chrony", "tss", "sssd", "rpc", "rpcuser", "nfsnobody",
    "postfix", "ntp", "apache", "nginx", "mysql", "postgres", "redis", "mongod",
}

# R9 稽核關注的預設帳號：存在且「可登入」就要問
DEFAULT_RISK_ACCOUNTS = {"root", "admin", "administrator", "guest", "oracle", "test"}

# 機構標準管理帳號：OS 初始化就佈到全機隊的控管帳號（使用者 2026-07-22 提供）。
#   Linux → sysinfra、AIX → sys004
# 為什麼要單獨認得它們（不能當成一般 service 帳號）：
#   1. 它們是「權限集中點」——通常帶 NOPASSWD:ALL（sysinfra 實測就是），稽核第一個要盯，
#      不能混在無名服務帳號裡。標成 mgmt 類讓它們在畫面上一眼可辨、算進特權統計。
#   2. 全機隊佈署但只在需要時登入 → 大部分主機上「從未登入」是正常的，
#      套 R5 閒置規則會在上百台主機上狂噴誤報。mgmt 類不套 R5（見 account_rules）。
#   3. 但它們是特權帳號，密碼效期照樣要管 → mgmt 仍套 R2/R2b。
# 值可擴充：之後有別的標準帳號名（或不同平台）加進來即可。
STD_MGMT_ACCOUNTS = {
    "sysinfra": "linux",
    "sys004": "aix",
}

# 特權群組：給了群組等於給了權限，比逐人給權更容易漏看
PRIVILEGED_GROUPS = {"wheel", "sudo", "adm", "root", "docker", "systemd-journal"}

# 不能登入的 shell。halt/shutdown/sync 的 shell 是「執行完就結束」的指令而不是 nologin，
# 實測會被誤判成「可登入但從未登入」的閒置帳號——它們本來就不是拿來登入的。
NOLOGIN_SHELLS = {
    "/sbin/nologin", "/usr/sbin/nologin", "/bin/false", "/usr/bin/false", "",
    "/sbin/halt", "/sbin/shutdown", "/bin/sync", "/usr/sbin/halt", "/usr/sbin/shutdown",
    "/sbin/poweroff",
}

# ===== 發行版差異 =====
#
# 實測（2026-07-21，家中 4 台）：Rocky 9.7 ×3、Debian 13 ×1。
# **Debian 13 已經沒有 `lastlog` 指令**（shadow-utils 棄用，Trixie 移除），
# 退回 `last` 之後行為完全不同：`last` 只列「有登入過的人」，
# 從未登入的帳號根本不會出現 → 閒置帳號直接漏報（223 一條 R5 都沒有就是這個原因）。
#
# 這類差異不能靠「猜發行版」處理，要靠**探測指令在不在**（command -v）：
# 同一個 RHEL 9 minor 版本之間也可能一個有一個沒有，版本號不是可靠依據。
# 但仍然收 os-release，因為 sudoers 路徑、UID_MIN 這些要照發行版走。
#
# ⚠️ 全部指令都掛 LC_ALL=C。實測這幾台都是中文語系，`lastlog` 印「**從未登入過**」、
# 日期是「二  7月 21 06:32:15 +0800 2026」——不強制語系解析器全部認不出來。

OS_PROBE = (
    ". /etc/os-release 2>/dev/null; "
    'echo "OSID=${ID:-unknown}"; echo "OSVER=${VERSION_ID:-unknown}"; '
    'echo "OSLIKE=${ID_LIKE:-}"; '
    "echo \"UIDMIN=$(awk '/^UID_MIN/{print $2}' /etc/login.defs 2>/dev/null)\"; "
    'for c in lastlog lastlog2 chage passwd sudo; do '
    'p=$(command -v $c 2>/dev/null); echo "BIN=$c:${p:-}"; done'
)

# 登入紀錄：優先 lastlog（每個帳號一列，含「從未登入」）；
# 其次 lastlog2（RHEL 10／新 Debian 的接班）；最後才 last（只有登入過的人）。
# 前綴 SRC= 讓解析器知道這批資料是哪來的——三種的語意強度不同，不能混為一談。
LOGIN_PROBE = (
    "if command -v lastlog >/dev/null 2>&1; then echo 'SRC=lastlog'; LC_ALL=C lastlog 2>/dev/null; "
    "elif command -v lastlog2 >/dev/null 2>&1; then echo 'SRC=lastlog2'; LC_ALL=C lastlog2 2>/dev/null; "
    "else echo 'SRC=last'; LC_ALL=C last -w -F 2>/dev/null | head -300; fi"
)

LINUX_CMDS = {
    "passwd": "cat /etc/passwd",
    "group": "cat /etc/group",
    "os": OS_PROBE,
    "lastlog": LOGIN_PROBE,
    # 固定輔助程式（納管時佈署）。沒佈署或沒授權時 sudo -n 直接失敗、輸出空白，退回下面三條
    "helper": "sudo -n /usr/local/libexec/webit3/account-facts 2>/dev/null",
    # 以下需 root，沒權限時 sudo -n 會直接失敗（不卡密碼提示），輸出留空
    "shadow_status": (
        "for u in $(cut -d: -f1 /etc/passwd); do "
        "s=$(LC_ALL=C sudo -n passwd -S \"$u\" 2>/dev/null); "
        "c=$(LC_ALL=C sudo -n chage -l \"$u\" 2>/dev/null | tr '\\n' '|'); "
        "[ -n \"$s$c\" ] && echo \"ACCT $u :: $s :: $c\"; done"
    ),
    "sudoers": "sudo -n cat /etc/sudoers /etc/sudoers.d/* 2>/dev/null",
    # 掃每個帳號的「真實家目錄」（從 passwd 取），不是只掃 /root+/home/*。
    # 系統帳號家目錄在 /var/adm、/bin、/var/lib/chrony 等處，只掃 /home 會整批漏掉，
    # 害它們被誤報成「取不到 authorized_keys（需 root）」——那不是權限問題，是沒掃到。
    # 沒有 authorized_keys 檔就明確回 0（＝確定沒有免密碼金鑰）；
    # 只有「家目錄存在但真的讀不到」才留空，那才是真的需 root。
    # 判定用 sudo-true 當閘門，不用「家目錄可讀」這種脆弱推測：
    #   sudo -n test -e 檔在 → 數金鑰數；sudo 可用但檔不在 → 確定 0；sudo 不可用 → 留白(真需 root)。
    # 「家目錄可讀(755) ≠ .ssh 進得去(700)」，用 test -r "$home" 判 0 會把「看不到」假報成
    # 「確定沒金鑰」（實測 tss=/dev/null、saslauth=/run/saslauthd 這種怪家目錄也會漏）。
    "authkeys": (
        "getent passwd | while IFS=: read -r u x uid gid gecos home shell; do "
        "f=\"$home/.ssh/authorized_keys\"; "
        "if sudo -n test -e \"$f\" 2>/dev/null; then "
        "echo \"KEYS $u $(sudo -n grep -cE '^(ssh-|ecdsa-|sk-)' \"$f\" 2>/dev/null || echo 0)\"; "
        "elif sudo -n true 2>/dev/null; then echo \"KEYS $u 0\"; fi; done"
    ),
}


# ══════════════════════════════════════════════════════════════════
# AIX（2026-09-22，B-24）
#
# 為什麼要獨立一組：AIX 沒有 /etc/os-release、沒有 /etc/login.defs、
# 沒有 lastlog(1)、沒有 chage、多數機器也沒有 sudo。拿 Linux 那組去問，
# 每一條都吐空字串 -> `parse_passwd` 收到空的 -> 丟 ConnectionError，
# 或更糟：靜靜地回 0 個帳號。**0 個帳號在 /etc/passwd 存在的機器上不可能發生**，
# 那一定是我們問錯指令。
#
# 三條硬限制（使用者 2026-09-16／09-20）：
#   1. 不准裝軟體，只能用系統內建指令
#   2. 不准影響效能
#   3. 不准變更任何設定（全部唯讀查詢）
# 另外 AIX 的預設 shell 是 **ksh88**：沒有 `command -v`（要用 `whence`）、
# 沒有 `if ! cmd`。下面所有指令都照 ksh88 寫。
# ══════════════════════════════════════════════════════════════════

# AIX 的「真人 UID 從哪開始」：AIX 沒有 /etc/login.defs。`mkuser` 預設從 **200** 起編，
# 200 以下是 OS 內建帳號。沿用 Linux 的 1000 會把 UID 200~999 的真人帳號
# 整批判成服務帳號 -- 那正是稽核最該看的一群人。
AIX_UID_MIN = 200

# AIX 的特權不是 wheel／sudo，而是 `system`／`security` 群組與 RBAC 角色。
# 拿 Linux 那份名單去比，AIX 的特權帳號清單會是**空的**。
AIX_PRIVILEGED_GROUPS = {"system", "security", "audit", "cron", "shutdown"}

AIX_OS_PROBE = (
    'echo "OSID=aix"; '
    'echo "OSVER=`oslevel -s 2>/dev/null`"; '
    'echo "OSLIKE=aix"; '
    'echo "UIDMIN=%d"; '
    'for c in lsuser lsgroup lssec sudo; do '
    'p=`whence $c 2>/dev/null`; echo "BIN=$c:$p"; done'
) % AIX_UID_MIN

# 登入時間：AIX 記在 /etc/security/lastlog（0640 root:security），
# 非 root 讀不到別人的 -> `lsuser` 會回空的 attr。
# 前綴 SRC=lsuser 讓解析器知道**這批資料不完整**：
# 名字不在裡面只代表「查不到」，**不可以推論成「從未登入」**
# （lastlog 那條路才能下這個結論，因為它每個帳號都有一列）。
AIX_LOGIN_PROBE = "echo 'SRC=lsuser'; lsuser -a time_last_login ALL 2>/dev/null"

# 密碼屬性在 /etc/security/user 與 /etc/security/passwd，兩個都要 root（或 security 群組）。
# 沒權限時 lsuser 只會印帳號名、不印 attr -> 解析後是空的 -> needs_root 會標出來。
AIX_PW_PROBE = (
    "echo 'SRC=lsuser'; "
    "lsuser -a account_locked maxage minage lastupdate expires login rlogin ALL 2>/dev/null"
)

AIX_CMDS = {
    "passwd": "cat /etc/passwd",
    "group": "cat /etc/group",
    "os": AIX_OS_PROBE,
    "lastlog": AIX_LOGIN_PROBE,
    # 固定輔助程式只佈在 Linux（AIX 沒有 sudo 可用，佈了也叫不動）。
    # 空字串 = 這條不跑，不是「跑了沒東西」。
    "helper": "",
    "shadow_status": AIX_PW_PROBE,
    # 多數 AIX 沒有 sudo；有的話是 AIX Toolbox 裝在 /opt/freeware。
    # 讀不到就讀不到，標 needs_root，不假裝「這台沒有人有 sudo」。
    "sudoers": "cat /etc/sudoers /opt/freeware/etc/sudoers 2>/dev/null",
    # authorized_keys：非 root 只讀得到自己那一份。**收到一份不代表查完了**，
    # 所以 AIX 這條收完還要比對涵蓋率（見 collect()），涵蓋不全一律標 needs_root。
    "authkeys": (
        "cat /etc/passwd | while IFS=: read u x uid gid gecos home shell; do "
        'f="$home/.ssh/authorized_keys"; '
        'if [ -r "$f" ]; then '
        "echo \"KEYS $u `grep -c '^\\(ssh-\\|ecdsa-\\|sk-\\)' \"$f\" 2>/dev/null`\"; "
        "fi; done"
    ),
}

PLATFORM_CMDS = {"linux": LINUX_CMDS, "aix": AIX_CMDS}


def parse_lsuser(text: str) -> dict[str, dict[str, str]]:
    """`lsuser -a <attr...> ALL` -> {帳號: {attr: 值}}。

    一行一個帳號：``root account_locked=false maxage=13 ...``。
    沒有權限看某個 attr 時 AIX **不會報錯，只會不印那個欄位** --
    所以「欄位不在」要當成「不知道」，不能當成「沒設定」。
    """
    out: dict[str, dict[str, str]] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("SRC="):
            continue
        parts = line.split()
        name = parts[0]
        if "=" in name:
            continue
        attrs = {}
        for kv in parts[1:]:
            k, sep, v = kv.partition("=")
            if sep:
                attrs[k] = v
        out[name] = attrs
    return out


def aix_shadow_from_lsuser(text: str) -> dict[str, dict]:
    """AIX 密碼屬性 -> 跟 `parse_shadow_status` 同樣形狀的 dict。

    只填**真的問到的**欄位。AIX 的 `maxage` 單位是**週**不是天，換算成天才能
    跟 Linux 的 `chage -M` 並排比較 -- 不換算的話畫面上 AIX 會全部看起來像
    「13 天就要改密碼」，那是假警報。
    """
    out: dict[str, dict] = {}
    for name, a in parse_lsuser(text).items():
        rec: dict = {}
        locked = a.get("account_locked")
        if locked is not None:
            rec["pw_status"] = "locked" if locked.lower() == "true" else "usable"
        maxage = a.get("maxage")
        if maxage is not None and maxage.isdigit():
            # AIX maxage 以「週」計；0 = 不到期
            weeks = int(maxage)
            rec["pw_max_days"] = weeks * 7 if weeks else None
        if a.get("lastupdate", "").isdigit():
            rec["pw_last_change"] = _epoch_to_local(a["lastupdate"])
        if a.get("expires") and a["expires"] != "0":
            rec["acct_expires"] = a["expires"]
        if rec:
            out[name] = rec
    return out


def _epoch_to_local(v: str) -> str | None:
    """epoch 秒 -> 本地時間字串。認不得就 None（不猜日期）。"""
    from datetime import datetime

    try:
        return datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return None


def parse_passwd(text: str) -> list[dict]:
    """/etc/passwd → 帳號清單。格式：name:x:uid:gid:gecos:home:shell"""
    out = []
    for line in (text or "").splitlines():
        parts = line.strip().split(":")
        if len(parts) < 7 or not parts[0]:
            continue
        try:
            uid, gid = int(parts[2]), int(parts[3])
        except ValueError:
            continue
        out.append({
            "username": parts[0], "uid": uid, "gid": gid,
            "gecos": parts[4] or None, "home": parts[5] or None, "shell": parts[6] or None,
        })
    return out


def parse_group(text: str) -> dict[str, list[str]]:
    """/etc/group → {群組名: [成員]}。只取顯式成員清單（第 4 欄）。"""
    out: dict[str, list[str]] = {}
    for line in (text or "").splitlines():
        parts = line.strip().split(":")
        if len(parts) < 4:
            continue
        members = [m for m in parts[3].split(",") if m]
        out[parts[0]] = members
    return out


# 指令已強制 LC_ALL=C，正常情況只會看到英文。中文字樣留著當第二道防線：
# 有些環境的 sshd 會用 PAM 強制設定語系，LC_ALL 未必吃得到（實測過中文輸出）。
_LASTLOG_NEVER = ("**Never logged in**", "Never logged in", "從未登入過", "从未登录过")


def parse_os(text: str) -> dict:
    """OS 探測輸出 → {id, version, like, uid_min, bins}。

    bins 記錄哪些指令真的存在——**這比版本號可靠**：
    同一個大版本的不同 minor／不同安裝選項都可能少掉某支工具。
    """
    info: dict = {"id": "unknown", "version": "unknown", "like": "",
                  "uid_min": 1000, "bins": {}}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("OSID="):
            info["id"] = line[5:] or "unknown"
        elif line.startswith("OSVER="):
            info["version"] = line[6:] or "unknown"
        elif line.startswith("OSLIKE="):
            info["like"] = line[7:]
        elif line.startswith("UIDMIN="):
            try:
                info["uid_min"] = int(line[7:])
            except ValueError:
                pass
        elif line.startswith("BIN="):
            name, _, path = line[4:].partition(":")
            info["bins"][name] = path or None
    info["family"] = os_family(info)
    return info


def os_family(info: dict) -> str:
    """歸成三大家族。版本各自跨好幾版，但同家族的指令與路徑慣例一致。

    rhel   RHEL / CentOS / Rocky / AlmaLinux / Oracle Linux（wheel 群組、/usr/bin）
    debian Debian / Ubuntu（sudo 群組、usrmerge 後 /usr/bin）
    suse   SLES / openSUSE
    """
    ident = (info.get("id") or "").lower()
    like = (info.get("like") or "").lower()
    blob = f"{ident} {like}"
    if any(k in blob for k in ("rhel", "centos", "rocky", "almalinux", "fedora", "ol")):
        return "rhel"
    if any(k in blob for k in ("debian", "ubuntu")):
        return "debian"
    if any(k in blob for k in ("suse", "sles")):
        return "suse"
    # AIX 不是 Linux 的任何一家：沒有 wheel、沒有 sudoers.d、指令全不同。
    # 歸進 unknown 的話，sudo_rules_for 會吐一份 AIX 套不上的規則。
    if "aix" in blob:
        return "aix"
    return "unknown"


def _parse_lsuser_lastlog(text: str) -> dict[str, str | None]:
    """AIX：`lsuser -a time_last_login ALL` -> {帳號: 時間字串或 None}。

    ⚠️ 這裡的 None 跟 lastlog 的 None **意思不一樣**：
    lastlog 的 None ＝「確定從未登入」，這裡的 None ＝「這個 attr 我們沒權限看」。
    所以回傳的 source 是 `lsuser`，呼叫端據此**不會**寫 never_logged_in=True。
    """
    out: dict[str, str | None] = {}
    for name, a in parse_lsuser(text).items():
        v = a.get("time_last_login")
        out[name] = _epoch_to_local(v) if (v or "").isdigit() and v != "0" else None
    return out


def parse_lastlog(text: str) -> tuple[dict[str, str | None], str]:
    """登入紀錄 → ({帳號: 最後登入字串或 None}, 資料來源)。

    ⚠️ 回傳 source 是必要的，不是附加資訊：
      lastlog／lastlog2 → 每個帳號都有一列，「Never logged in」是**確定**從未登入
      last              → 只列有登入過的人，帳號不在裡面**只代表 wtmp 保存期內沒登入**，
                          不能斷言從未登入（wtmp 通常一個月就輪替）
    把兩者當成同一件事，會在 Debian 13 這種沒有 lastlog 的系統上編造出確定性。

    刻意保留原始字串不轉 datetime：格式差異大，硬解析失敗會變成「查無登入紀錄」，
    那是完全相反的稽核結論。
    """
    source = "lastlog"
    out: dict[str, str | None] = {}
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("SRC="):
            source = line[4:].strip() or "lastlog"
            continue
        low = line.lower()
        if low.startswith(("username", "wtmp begins", "btmp begins", "reboot", "login")):
            continue
        parts = line.split()
        if not parts:
            continue
        user = parts[0]
        if source == "last" and user in ("wtmp", "system"):
            continue
        if any(n in line for n in _LASTLOG_NEVER):
            out[user] = None
            continue
        rest = line[len(user):].strip()
        # last 同一個帳號會有多列（每次登入一列），第一列就是最近的一次
        if source == "last" and user in out:
            continue
        out[user] = rest or None
    return out, source


_CHAGE_FIELDS = {
    "last password change": "pw_last_change",
    "password expires": "pw_expires",
    "password inactive": "pw_inactive",
    "account expires": "acct_expires",
    "maximum number of days between password change": "pw_max_days",
    "minimum number of days between password change": "pw_min_days",
}


# passwd -S 狀態碼 → 正規化狀態。單字母(util-linux) 與兩字母(shadow-utils) 都涵蓋。
# 未知碼保留原字串（誠實：不硬套，讓後端當 unknown 處理），但一律轉大寫比對。
_PW_STATUS_CODES = {
    "P": "set", "PS": "set",
    "L": "locked", "LK": "locked",
    "NP": "empty",
}


def normalize_pw_status(code: str) -> str:
    return _PW_STATUS_CODES.get((code or "").strip().upper(), code)


def parse_shadow_status(text: str) -> dict[str, dict]:
    """`ACCT <user> :: <passwd -S 輸出> :: <chage -l 輸出用|分隔>` → 每個帳號的密碼狀態。

    passwd -S 第二欄狀態碼跨發行版有兩種寫法，都要吃：
    單字母 P/NP/L（部分 util-linux）與兩字母 PS/NP/LK（shadow-utils，多數 Linux）。
    只認單字母會讓 shadow-utils 機器上一堆「已鎖定(LK)」被留成原始碼、
    規則引擎當成「還能登入」——鎖定帳號對稽核隱形，比沒查更糟。
    """
    out: dict[str, dict] = {}
    for raw in (text or "").splitlines():
        if not raw.startswith("ACCT "):
            continue
        try:
            _, user, rest = raw.split(" ", 2)
            s_part, _, c_part = rest.partition(" :: ")
            s_part = s_part.lstrip(": ").strip()
        except ValueError:
            continue
        info: dict = {}
        sp = s_part.split()
        if len(sp) >= 2:
            info["pw_status"] = normalize_pw_status(sp[1])
        for chunk in c_part.split("|"):
            if ":" not in chunk:
                continue
            k, _, v = chunk.partition(":")
            key = _CHAGE_FIELDS.get(k.strip().lower().rstrip("s").strip())
            if key is None:
                for label, field in _CHAGE_FIELDS.items():
                    if k.strip().lower().startswith(label[:22]):
                        key = field
                        break
            if key:
                info[key] = v.strip() or None
        if info:
            out[user] = info
    return out


# 開頭的 % 不能漏：`%wheel ALL=(ALL) ALL` 是群組式授權，實務上比逐人列名更常見。
# 漏掉它會讓「靠 wheel 群組拿到 sudo 的人」全部被判成非 sudoer——那是最容易漏看的一群。
_SUDO_USER_RE = re.compile(r"^\s*(%?[A-Za-z0-9_.-]+)\s+(\S+)\s*=\s*(.+)$")


def parse_sudoers(text: str) -> dict[str, dict]:
    """sudoers → {帳號或%群組: {nopasswd: bool, spec: 原文}}。

    只解析「使用者規格行」，別名/Defaults 一律略過——
    解析不了的行**不猜**，寧可漏報也不要報一個不存在的特權。
    """
    out: dict[str, dict] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("Defaults"):
            continue
        if line.split()[0] in ("User_Alias", "Cmnd_Alias", "Host_Alias", "Runas_Alias"):
            continue
        m = _SUDO_USER_RE.match(line)
        if not m:
            continue
        who = m.group(1)
        spec = m.group(3)
        entry = out.setdefault(who, {"nopasswd": False, "spec": []})
        if "NOPASSWD" in spec.upper():
            entry["nopasswd"] = True
        entry["spec"].append(line)
    return out


def parse_authkeys(text: str) -> dict[str, int]:
    """`KEYS <user> <n>` → {帳號: 授權公鑰數}。免密碼就能登入的人。"""
    out: dict[str, int] = {}
    for raw in (text or "").splitlines():
        parts = raw.split()
        if len(parts) == 3 and parts[0] == "KEYS":
            try:
                out[parts[1]] = int(parts[2])
            except ValueError:
                continue
    return out


def sudo_rules_for(info: dict) -> str:
    """依發行版產生 sudo 白名單。

    路徑不能寫死：RHEL 家族的 cat 在 /usr/bin（RHEL 7 的 /bin 是 symlink），
    Debian usrmerge 之後也是 /usr/bin——但 sudoers 比對的是**字面路徑**，
    寫 /bin/cat 在只認 /usr/bin/cat 的系統上不會生效，而且不會報錯，
    只會安靜地繼續拿不到資料（最難查的那種失敗）。
    實際路徑用探測到的 bins 為準，探不到才退回家族預設。
    """
    # 帳號盤點那條走固定路徑的輔助程式，跟發行版無關；只有 cat 的路徑要看 usrmerge。
    # 2026-09-11 起不再逐條列 chage／passwd／cat 帶 `*` 的規則——`*` 會吃空白，
    # 能被拿去讀 /etc/shadow（見 ACCOUNT_HELPER_SCRIPT 上方說明）。
    cat = "/usr/bin/cat"
    return f"""# webit3 收集帳號的唯讀 sudo 規則
# 產生依據：{info.get('id')} {info.get('version')}（家族 {info.get('family')}）
# 刻意不含 /etc/shadow，也不含任何帶 * 的帳號指令（* 會吃空白，可被拿去多讀檔案）。
webit3scan ALL=(root) NOPASSWD: {cat} /sys/class/dmi/id/*
webit3scan ALL=(root) NOPASSWD: {ACCOUNT_HELPER_PATH} ""
# 值班健檢的兩項唯讀日誌（2026-09-22 使用者核准）。參數寫死，不可放寬成裸 tail／cat。
webit3scan ALL=(root) NOPASSWD: /usr/bin/tail -n 200 /var/log/secure
webit3scan ALL=(root) NOPASSWD: /usr/bin/tail -n 200 /var/log/auth.log
webit3scan ALL=(root) NOPASSWD: /usr/bin/dmesg --level=err\\,crit
# 監聽 port 的行程名（ss -tlnp 的 -p 需 root）。參數寫死 -tlnp。
webit3scan ALL=(root) NOPASSWD: /usr/sbin/ss -tlnp, /usr/bin/ss -tlnp
"""


def split_helper_output(text: str) -> dict[str, str] | None:
    """輔助程式輸出 → {SHADOW, SUDOERS, KEYS}。沒有結尾標記＝沒跑完或沒佈署，回 None。

    結尾標記是必要的：程式跑到一半被中斷時，前兩段有資料、KEYS 是空的——
    沒有標記就會把「沒跑完」當成「每個帳號都沒有金鑰」。
    """
    if not text or "=== END" not in text:
        return None
    # 可能有 NOTE 段（名稱服務沒回應而退回讀本機檔）。它代表**這次的帳號清單不完整**，
    # 呼叫端要把它顯示出來——靜默少掉一批網域帳號會讓稽核報告漏人。
    out: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("=== "):
            cur = line[4:].strip()
            out.setdefault(cur, [])
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def classify_account(acc: dict, sudo_map: dict, priv_members: set,
                     uid_min: int = 1000) -> str:
    """帳號分類。沒有這欄，密碼到期之類的判定會對服務帳號大量誤報，
    紅燈多到沒人看——誤報是稽核工具最大的死因。

    human   有登入用 shell 且 UID >= UID_MIN（真人區間）
    default 系統預設且屬於稽核關注名單（root/admin/guest…）
    builtin 叫得出名字的內建守護帳號（sshd/chrony/bin/daemon…）——OS/套件裝的，噪音
    service 無名的系統帳號（nologin 或 UID<UID_MIN 但不在已知名單）——來路要問

    內建 > 服務：sshd 這種是 OS 內建、能對得上用途；落到 service 的才是查不出名字、
    需要人去確認的。分開才不會把「已知內建噪音」跟「不明系統帳號」混成一堆。

    ⚠️ UID 門檻讀目標機的 /etc/login.defs，不寫死 1000：
    RHEL 6 以前是 500，部分企業自訂更高。寫死會讓一整批系統帳號被誤判成真人。
    """
    name = acc["username"]
    if name in DEFAULT_RISK_ACCOUNTS:
        return "default"
    # 標準管理帳號要在系統帳號判定「之前」認出來——它 uid 可能 <UID_MIN（sysinfra 是 645），
    # 落到下面就會被歸成無名 service，權限集中點就藏進雜訊裡了。
    if name in STD_MGMT_ACCOUNTS:
        return "mgmt"
    if name in SYSTEM_ACCOUNTS:
        return "builtin"          # 叫得出名字的內建守護帳號，跟無名 service 分開
    if (acc.get("shell") or "") in NOLOGIN_SHELLS:
        return "service"
    if acc["uid"] < uid_min:
        return "service"
    # 2026-09-18 十項盤點第 10 條：以前完全沒看備註（gecos）。221 的 ansible_svc 備註寫
    # 「Ansible Service Account」，有 bash、UID 夠大 → 落到真人，匯出 type 變成 7 人員使用帳號。
    # 只認**明確字樣**，不做模糊比對：誤把真人判成服務，會讓他的密碼到期等規則被放過。
    if _looks_like_service(name, acc.get("gecos")):
        return "service"
    return "human"


def _looks_like_service(username: str, gecos: str | None) -> bool:
    """帳號名或備註明確寫著是服務／程式帳號。"""
    u = (username or "").lower()
    g = (gecos or "").lower()
    if u.endswith("_svc") or u.startswith("svc_") or u.endswith("-svc") or u.startswith("svc-"):
        return True
    return ("service account" in g or "svc account" in g or "服務帳號" in g
            or "程式帳號" in g or "application account" in g)


def collect(runner, host: str, platform: str = "linux") -> dict:
    """對 host 收帳號。回 {accounts: [...], needs_root: [...], root_ok: bool}。

    needs_root 列出「這次因為權限不足而拿不到」的欄位類別，畫面要照實顯示，
    不可讓使用者以為那些欄位是「查過了、沒問題」。
    """
    cmds = PLATFORM_CMDS.get(platform)
    if cmds is None:
        raise ValueError(f"帳號採集目前只支援 linux／aix（收到 {platform}）")

    def run(key: str) -> str:
        cmd = cmds.get(key)
        if not cmd:                # 空字串＝這個平台沒有這條路（不是跑了沒東西）
            return ""
        try:
            return runner(host, cmd)
        except Exception:  # noqa: BLE001 - 單一指令失敗不整組掛掉
            return ""

    passwd_raw = runner(host, cmds["passwd"])         # 這條失敗就是真的連不上，讓它往上拋
    accounts = parse_passwd(passwd_raw)
    if not accounts:
        raise ConnectionError("取不到 /etc/passwd，無法盤點帳號")

    os_info = parse_os(run("os"))
    groups = parse_group(run("group"))
    if platform == "aix":
        lastlog, login_source = _parse_lsuser_lastlog(run("lastlog")), "lsuser"
    else:
        lastlog, login_source = parse_lastlog(run("lastlog"))
    # 需 root 的三樣：先問固定輔助程式（佈過就一次拿齊），沒佈署才退回舊的逐條 sudo -n
    # （那條路只在收集身分本來就有完整 sudo 時才拿得到，例如 sysinfra）。
    helper = split_helper_output(run("helper"))
    # 名稱服務沒回應時輔助程式會退回只讀本機 /etc/passwd，並留一行 NOTE。
    # 那代表**這台的帳號清單不完整**（網域帳號沒列進來），要往上帶、不可以吞掉。
    nss_note = (helper or {}).get("NOTE", "").strip() or None
    if helper is not None:
        shadow = parse_shadow_status(helper.get("SHADOW", ""))
        sudoers = parse_sudoers(helper.get("SUDOERS", ""))
        authkeys = parse_authkeys(helper.get("KEYS", ""))
    elif platform == "aix":
        shadow = aix_shadow_from_lsuser(run("shadow_status"))
        sudoers = parse_sudoers(run("sudoers"))
        authkeys = parse_authkeys(run("authkeys"))
    else:
        shadow = parse_shadow_status(run("shadow_status"))
        sudoers = parse_sudoers(run("sudoers"))
        authkeys = parse_authkeys(run("authkeys"))

    needs_root = []
    if not shadow:
        needs_root.append("password")     # 密碼到期／上次變更／空密碼
    if not sudoers:
        needs_root.append("sudo")         # sudo 權限明細與 NOPASSWD
    if not authkeys:
        needs_root.append("authorized_keys")
    if platform == "aix":
        # ⚠️ AIX 這三條**部分成功**的樣子跟完全失敗不一樣：非 root 讀得到自己那一份，
        # 於是 `authkeys` 有 1 筆、`shadow` 有 1 筆，上面三個 if 都不會觸發，
        # 畫面就會顯示「已查完」。**收到一筆不等於查完 300 個帳號。**
        # 涵蓋不到全部帳號一律照實標成「需 root」。
        total = len(accounts)
        if shadow and len(shadow) < total and "password" not in needs_root:
            needs_root.append("password")
        if authkeys and len(authkeys) < total and "authorized_keys" not in needs_root:
            needs_root.append("authorized_keys")

    # 特權群組成員（群組給權比逐人給權更容易漏看）
    priv_members: dict[str, list[str]] = {}
    for g in (AIX_PRIVILEGED_GROUPS if platform == "aix" else PRIVILEGED_GROUPS):
        for m in groups.get(g, []):
            priv_members.setdefault(m, []).append(g)

    out = []
    for acc in accounts:
        name = acc["username"]
        sudo_entry = sudoers.get(name) or sudoers.get(f"%{name}")
        in_priv_group = bool(priv_members.get(name))
        # 群組式 sudo：使用者在 wheel/sudo 群組，而 sudoers 有 %wheel 那行
        group_sudo = [g for g in priv_members.get(name, []) if sudoers.get(f"%{g}")]
        # ⚠️ 沒有 root 時 sudoers 讀不到，但 /etc/group 讀得到——這時「在 wheel/sudo 群組」
        # 就是我們僅有的特權線索，而那幾乎必然代表有 sudo（那正是 wheel 的用途）。
        # 不採計的話，最該被看的那張「特權帳號清單」在無 root 環境下會是空的，
        # 空的特權清單比不精確的特權清單危險得多。
        is_sudoer = bool(sudo_entry) or bool(group_sudo) or in_priv_group
        nopasswd = bool(sudo_entry and sudo_entry["nopasswd"]) or any(
            sudoers.get(f"%{g}", {}).get("nopasswd") for g in priv_members.get(name, []))
        sd = shadow.get(name, {})
        # 「從未登入」的確定性取決於資料來源：
        #   lastlog/lastlog2 每個帳號都有一列 → 沒登入過就是確定沒登入過
        #   last 只列登入過的人 → 不在裡面只代表 wtmp 保存期內沒紀錄，不能斷言從未登入
        if login_source in ("lastlog", "lastlog2"):
            never = name in lastlog and lastlog[name] is None
            login_known = name in lastlog
        else:
            never = False
            login_known = name in lastlog

        out.append({
            **acc,
            "kind": classify_account(acc, sudoers, set(priv_members),
                                     uid_min=os_info["uid_min"]),
            "last_login": lastlog.get(name),
            "never_logged_in": never,
            # 畫面與規則都要知道這筆的登入資料有多可信
            "login_source": login_source,
            "login_known": login_known,
            "pw_status": sd.get("pw_status"),
            "pw_last_change": sd.get("pw_last_change"),
            "pw_expires": sd.get("pw_expires"),
            "pw_max_days": sd.get("pw_max_days"),
            "acct_expires": sd.get("acct_expires"),
            "is_sudoer": is_sudoer,
            # 依據要留著：sudoers 明列是確定的，只靠群組推斷的要標明，
            # 免得覆核的人以為兩者證據強度一樣
            "sudo_basis": "sudoers" if sudo_entry or group_sudo else (
                "group" if in_priv_group else None),
            "sudo_nopasswd": nopasswd,
            "priv_groups": ",".join(priv_members.get(name, [])) or None,
            "authorized_keys": authkeys.get(name),
            "can_login": (acc.get("shell") or "") not in NOLOGIN_SHELLS,
        })

    return {"accounts": out, "needs_root": needs_root, "root_ok": not needs_root,
            "platform": platform,
            "os": os_info, "login_source": login_source,
            # 名稱服務沒回應而只讀到本機帳號時，這裡會有一句話。
            # 收集「成功」但資料不完整，是最容易被當成正常的情況——要帶出去顯示。
            "nss_note": nss_note,
            "sudo_rules": sudo_rules_for(os_info)}
