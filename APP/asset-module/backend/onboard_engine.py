"""納管引擎：把一台「發現了但進不去」的主機，帶到「已納管、系統收得到」。

一顆引擎，兩種觸發（使用者 2026-07-19 定案）：
    A（UI 一鍵）  憑證＝人當下輸入、用完即丟
    B（排程自動）  憑證＝授權過的金鑰庫；只碰標為「可自動納管」的網段
兩者共用這顆引擎，差別只在「誰觸發、憑證哪來」。這一版先做引擎本身。

## 三條寫死的安全底線

1. **憑證絕不落地**：登入用的帳密只在一次納管過程存在於記憶體，
   **不寫 DB、不寫 log、不進稽核紀錄、不進診斷包**。密碼透過 sshpass 的
   SSHPASS 環境變數傳給短命子行程，不進 argv（`ps` 看不到）、不落磁碟。
2. **每次納管留可稽核紀錄**（誰/何時/哪台/平台/成敗/輸出），但**永遠不含憑證**。
3. **腳本即時從收集公鑰組出**，不依賴外部檔案——公鑰永遠與 221 的私鑰同步，
   不會有「換了金鑰但腳本還是舊公鑰」的漂移。

## 為什麼執行器可注入

真正的 SSH 執行要碰網路、要目標主機的密碼——那部分只能在 221 對真機驗，
而且密碼不該經過我（AI）。所以執行器抽成可注入介面：
家裡用假執行器把「憑證不落地、稽核乾淨、腳本組對」全測到；真執行由 UI 觸發。
"""
from __future__ import annotations

import base64
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# 收集金鑰位置。deploy.sh 會在這裡產生（冪等），私鑰永遠只留在收集器這台。
# 可用環境變數覆蓋：公司環境的安裝路徑不一定是 /opt/webit3（setup.sh 可改 WEBIT_DATA），
# 寫死的話會出現「金鑰產在 A、程式去 B 找」這種各說各話的狀況。
COLLECTOR_KEY_PUB = os.environ.get(
    "ASSET_COLLECTOR_KEY", "/opt/webit3/.collector_key") + ".pub"
DEFAULT_ACCOUNT = "webit3scan"

# AIX 的帳號名有長度上限：sys0 的 max_logname 預設 9（＝可用 8 個字元），
# 而 "webit3scan" 是 10 個字元，mkuser 會直接拒絕。放寬 max_logname 要 chdev
# **並重新開機**——為了一個帳號名重開 8 台正式 AIX 不划算。
#
# 所以同一個身分在 AIX 上用 8 字元的名字。這不是兩個帳號，是同一個收集身分在
# 不同平台的合法寫法；要完全同名也可以（若該環境已放寬 max_logname），
# 把設定值 collect_ssh_account_aix 改成 webit3scan 即可。
DEFAULT_ACCOUNT_AIX = "webit3sc"
AIX_MAX_LOGNAME = 8


# ===== 執行中進度（2026-08-16 使用者要求）=====
#
# 「我怎知道有在做？」的正解不是跑秒數，是**講出目標主機現在做到哪一步**。
# 納管腳本本來就會逐行印「已建立帳號」「佈署收集公鑰」這類話，只是原本被
# subprocess.run 一次收完、等整件事結束才回來——資訊一直都在，只是被關著。
#
# 改成邊跑邊收：執行器逐行讀 stdout，寫進這個以主機為鍵的進度表；畫面輪詢它。
# 刻意放記憶體不進 DB：這是幾十秒的暫態，落地只會留一堆沒人看的列；
# 真正要留存的結果本來就會寫 onboard_audit。
#
# ⚠️ 這裡**永遠不放憑證**——只放腳本的 stdout，那是設計上就不含機密的內容。
_PROGRESS: dict[str, dict] = {}
_PROGRESS_LOCK = __import__("threading").Lock()


def progress_start(host: str) -> None:
    with _PROGRESS_LOCK:
        _PROGRESS[host] = {"stage": "連線中…", "lines": [], "done": False}


def progress_note(host: str, line: str) -> None:
    """收到目標主機的一行輸出。腳本印什麼就顯示什麼——不另外維護階段對照表，
    那種表一定會跟腳本漂走，然後畫面講的跟實際做的不是同一件事。"""
    line = (line or "").strip()
    if not line:
        return
    with _PROGRESS_LOCK:
        p = _PROGRESS.get(host)
        if p is None:
            return
        p["lines"].append(line[:300])
        del p["lines"][:-40]          # 只留最近 40 行，不要無限長大
        # [+] 建立了什麼、[=] 本來就有、[*] 正在做什麼——這三種是給人看的階段話
        if line[:3] in ("[+]", "[=]", "[*]"):
            p["stage"] = line[3:].strip()
        elif line.startswith("完成。"):
            p["stage"] = "完成"


def progress_done(host: str) -> None:
    with _PROGRESS_LOCK:
        if host in _PROGRESS:
            _PROGRESS[host]["done"] = True


def progress_of(host: str) -> dict:
    with _PROGRESS_LOCK:
        p = _PROGRESS.get(host)
        return {"stage": "", "lines": [], "done": True} if p is None else {
            "stage": p["stage"], "lines": list(p["lines"]), "done": p["done"]}


def ensure_collector_key(path: str | None = None) -> bool:
    """沒有收集金鑰就當場產一把。回 True 代表這次新產的。

    ## 為什麼由系統自己產，而不是叫人跑 deploy.sh（使用者 2026-08-16 指正）

    收集金鑰是「一鍵納管這顆按鈕能不能用」的前提。原本要人 SSH 進收集器、用 root
    跑部署腳本才生得出來——但這把金鑰是寫給**服務帳號自己**用的，根本不需要 root，
    服務有能力自己產。把它擺在部署腳本裡，等於讓一個畫面功能依賴一次人工的命令列
    動作；而且 patch.sh 只有在 systemd unit 變動時才會重跑 deploy.sh，新版腳本送過去
    也不會自己執行，人還以為更新完就好了。

    ## 兩條寫死的安全底線

    1. **只在不存在時產，絕不覆蓋**。重新產一把會讓所有已納管主機當場失聯
       （它們 authorized_keys 裡的是舊公鑰），而且沒有任何畫面會顯示「因為換了金鑰」。
    2. 私鑰權限 0600、只留在收集器這台。公鑰才是要佈出去的東西。

    用 cryptography 產（本來就是相依），不呼叫 ssh-keygen：少一個外部指令的假設，
    而且家裡 Windows 開發機也跑得起來，測試才測得到。
    """
    key_path = Path(path or COLLECTOR_KEY_PUB[:-4])
    pub_path = Path(str(key_path) + ".pub")
    # ⚠️ 只要**任一半**已經存在就不動。
    # 先前寫成「兩個都在才跳過」，於是「只有公鑰在」的機器（私鑰放別處、或只複製了
    # 公鑰過來）會被當成沒有金鑰而重新產一把，**直接覆蓋掉現有公鑰**——所有已納管
    # 主機的 authorized_keys 裡都是舊公鑰，會當場全部失聯，而且畫面不會顯示原因。
    # 這正是本函式 docstring 第一條底線要防的事，自己卻踩了；由測試抓出來。
    if key_path.exists() or pub_path.exists():
        return False

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key_path.parent.mkdir(parents=True, exist_ok=True)
    private = ed25519.Ed25519PrivateKey.generate()
    key_path.write_bytes(private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    pub = private.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode()
    pub_path.write_text(f"{pub} webit3 collector\n", encoding="utf-8")
    try:
        os.chmod(key_path, 0o600)
        os.chmod(pub_path, 0o644)
    except OSError:      # Windows 沒有 POSIX 權限，不影響金鑰本身可用
        pass
    return True


def collector_pubkey(path: str | None = None) -> str:
    """讀收集端公鑰。這是要塞進目標機的東西，不是機密（私鑰永遠留收集器）。

    ⚠️ 預設路徑在呼叫當下才讀模組常數（不是綁在預設引數上）——綁在預設引數會在
    import 當下就定值，之後改 COLLECTOR_KEY_PUB 完全無效，測試與非標準安裝路徑
    都繞不過去。

    金鑰不存在時丟 ValueError 而不是 FileNotFoundError：呼叫端要能把它翻成
    「還沒產生收集金鑰，請先跑安裝」這種看得懂的話，而不是一個 500。
    首次安裝、金鑰還沒產生時就會走到這裡。
    """
    path = path or COLLECTOR_KEY_PUB
    try:
        # 沒有就當場產一把（只在不存在時；絕不覆蓋既有的）。
        # 讀公鑰的每一條路都會經過這裡，所以擺在這裡就四條佈金鑰的路都涵蓋到。
        ensure_collector_key(path[:-4] if path.endswith(".pub") else path)
    except Exception:  # noqa: BLE001 - 產不出來就讓下面的讀取失敗，訊息比較具體
        pass
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError as exc:
        # ⚠️ 這段訊息曾經寫「deploy.sh 會建立」——**那是錯的**，當時整個專案沒有任何
        # 地方會產生這把金鑰（221 上那把是手動建的）。等於叫人去跑一個不會解決問題的
        # 指令。2026-08-16 在公司主機踩到才發現，已在 deploy.sh 補上產生步驟；
        # 這裡同時給「重跑 deploy.sh」與「手動一行」兩條路，因為既有的部署可能不會再跑它。
        key = path[:-4] if path.endswith(".pub") else path
        raise ValueError(
            f"讀不到收集端公鑰（{path}）：{exc}。這台還沒產生收集金鑰。"
            f"重跑 deploy.sh 會建立；或手動產一把："
            f"ssh-keygen -t ed25519 -N '' -C 'webit3 collector' -f {key}"
            f"（產完把擁有者設成跑服務的帳號、私鑰 600）。") from exc


def build_linux_script(pubkey: str, collector_ip: str, account: str = DEFAULT_ACCOUNT) -> str:
    """即時組出 Linux 納管腳本。內容只含公鑰，無任何機密。"""
    return f"""#!/usr/bin/env bash
set -euo pipefail
ACCOUNT="{account}"
COLLECTOR_IP="{collector_ip}"
PUBKEY='{pubkey}'
if [ "$(id -u)" -ne 0 ]; then echo "需要 root" >&2; exit 1; fi
if ! id "$ACCOUNT" >/dev/null 2>&1; then
  useradd -m -s /bin/bash -c "webit3 唯讀收集" "$ACCOUNT"
  passwd -l "$ACCOUNT" >/dev/null
  echo "[+] 已建立帳號 $ACCOUNT"
else echo "[=] 帳號已存在"; fi
echo "[*] 佈署收集公鑰"
HOME_DIR="$(getent passwd "$ACCOUNT" | cut -d: -f6)"
install -d -m 700 -o "$ACCOUNT" -g "$ACCOUNT" "$HOME_DIR/.ssh"
AUTH="$HOME_DIR/.ssh/authorized_keys"
LINE="from=\\"$COLLECTOR_IP\\",no-agent-forwarding,no-port-forwarding,no-X11-forwarding $PUBKEY"
grep -qF "$PUBKEY" "$AUTH" 2>/dev/null || echo "$LINE" >> "$AUTH"
chown "$ACCOUNT:$ACCOUNT" "$AUTH"; chmod 600 "$AUTH"
echo "[*] 設定唯讀 sudo 白名單（只給讀機型與序號）"
SUDOERS="/etc/sudoers.d/$ACCOUNT"
echo "$ACCOUNT ALL=(root) NOPASSWD: /usr/bin/cat /sys/class/dmi/id/*" > "$SUDOERS"
chmod 440 "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null || {{ rm -f "$SUDOERS"; echo "sudoers 錯"; exit 1; }}
echo "完成。$COLLECTOR_IP 現在可以收集這台。"
"""


def build_aix_script(pubkey: str, collector_ip: str,
                     account: str = DEFAULT_ACCOUNT_AIX) -> str:
    """即時組出 AIX 納管腳本（ksh）。內容只含公鑰，無任何機密。

    ⚠️ 不是「Linux 腳本換個名字」——照 Linux 那份跑在 AIX 上一定失敗：

    - 建帳號是 `mkuser` 不是 `useradd`；鎖密碼是 `chuser account_locked=true`
      不是 `passwd -l`。
    - **不佈 sudoers**。Linux 版要 sudo 是為了讀 `/sys/class/dmi/id/*`（0400 只有
      root 讀得到）；AIX 根本沒有 dmi，序號機型走 `uname -M`／`uname -u`，一般帳號
      就讀得到。而且 AIX 未必裝 sudo（常在 /opt/freeware/bin 或改用 RBAC），
      硬寫 /etc/sudoers.d 會直接失敗。少一個不需要的權限也是好事。
    - 預設 shell 用 ksh（AIX 的預設），不要假設有 bash。
    - AIX 的 `mkuser` 預設不會建家目錄內容，`.ssh` 要自己建好權限。

    這些差異是 2026-08-16 定案「AIX 走一次性納管腳本（方案 A）」時整理的：
    Ansible 不支援 AIX，所以 playbook 那條路對 AIX 不成立，只能給可貼的腳本。
    """
    if len(account) > AIX_MAX_LOGNAME:
        raise ValueError(
            f"AIX 帳號名「{account}」有 {len(account)} 個字元，超過預設上限 "
            f"{AIX_MAX_LOGNAME}——mkuser 會直接拒絕。請改用較短的名字"
            f"（預設 {DEFAULT_ACCOUNT_AIX}），或先在該主機放寬 max_logname（要重開機）")
    return f"""#!/usr/bin/ksh
set -e
ACCOUNT="{account}"
COLLECTOR_IP="{collector_ip}"
PUBKEY='{pubkey}'
if [ "$(id -u)" -ne 0 ]; then echo "需要 root" >&2; exit 1; fi
# 先確認這台的帳號名長度上限，不要等 mkuser 吐一句看不懂的錯才發現
MAXLOG=$(lsattr -El sys0 -a max_logname 2>/dev/null | awk '{{print $2}}')
if [ -n "$MAXLOG" ] && [ "$MAXLOG" -le "${{#ACCOUNT}}" ]; then
  echo "此主機 max_logname=$MAXLOG，容不下 $ACCOUNT（${{#ACCOUNT}} 字元）。" >&2
  echo "請改用較短的收集帳號名，或 chdev -l sys0 -a max_logname=32 後重開機。" >&2
  exit 1
fi
if ! lsuser "$ACCOUNT" >/dev/null 2>&1; then
  mkuser shell=/usr/bin/ksh gecos="webit3 readonly collector" "$ACCOUNT"
  chuser account_locked=true "$ACCOUNT"      # 只能用金鑰登入，沒有可用密碼
  echo "[+] 已建立帳號 $ACCOUNT"
else echo "[=] 帳號已存在"; fi
echo "[*] 佈署收集公鑰"
HOME_DIR=$(lsuser -a home "$ACCOUNT" | awk -F'home=' '{{print $2}}')
if [ -z "$HOME_DIR" ]; then HOME_DIR="/home/$ACCOUNT"; fi
mkdir -p "$HOME_DIR/.ssh"
AUTH="$HOME_DIR/.ssh/authorized_keys"
LINE="from=\\"$COLLECTOR_IP\\",no-agent-forwarding,no-port-forwarding,no-X11-forwarding $PUBKEY"
if ! grep -F "$PUBKEY" "$AUTH" >/dev/null 2>&1; then echo "$LINE" >> "$AUTH"; fi
chown -R "$ACCOUNT" "$HOME_DIR/.ssh"
chmod 700 "$HOME_DIR/.ssh"; chmod 600 "$AUTH"
# 驗證：AIX 上這幾個欄位一般帳號就讀得到，不需要 sudo——跑完直接證明給人看
echo ""
echo "--- 驗證：收集會用到的欄位（不需 root）---"
echo "  oslevel : $(oslevel -s 2>/dev/null)"
echo "  model   : $(uname -M 2>/dev/null)"
echo "  serial  : $(uname -u 2>/dev/null)"
echo ""
echo "完成。$COLLECTOR_IP 現在可以收集這台。"
"""


def build_linux_playbook(pubkey: str, collector_ip: str,
                         account: str = DEFAULT_ACCOUNT) -> str:
    """把 Linux 納管腳本翻成 Ansible playbook，交給資安／維運一次佈完整批主機。

    ⚠️ **為什麼要從這顆引擎產、而不是另外維護一份 yml 檔**：公鑰必須永遠跟收集端的
    私鑰同步。手寫一份 yml 放在 repo 裡，換過金鑰之後那份就是錯的，而且錯得很安靜
    ——佈下去每台都成功，只是收集全部連不進來。這裡即時組出來，不會有漂移。

    內容與 build_linux_script 等價（同一組動作：建帳號、鎖密碼、佈 authorized_keys
    帶 from= 限來源、只給讀 dmi 的 sudo 白名單），差別只在交付形式。
    這三道鎖是決策 C1 定案專用帳號的前提，少一道就不成立。
    """
    validate_collector_ip(collector_ip)   # playbook 不經過 build_script，這裡自己擋
    return f"""---
# webit3 資產盤點系統 — 收集帳號一次性佈署
#
# 用途：在目標主機建立唯讀收集帳號 {account}，讓 {collector_ip} 能以金鑰登入收集
#       主機事實（主機名／OS／序號／機型）、服務清單與帳號稽核資料。
#
# 三道鎖（缺一道這個帳號就不該存在）：
#   1. authorized_keys 帶 from="{collector_ip}"，只有收集器連得進來
#   2. 密碼鎖定，只能用金鑰登入
#   3. sudo 白名單只給讀 /sys/class/dmi/id/*（機型與序號），**不含 /etc/shadow**
#
# 撤銷：刪掉該帳號的 authorized_keys 那一行即刻生效；要完整移除就 userdel。
#
# 用法：ansible-playbook -i <inventory> webit3scan_bootstrap.yml
- name: 佈署 webit3 唯讀收集帳號
  hosts: all
  become: true
  vars:
    webit3_account: "{account}"
    webit3_collector_ip: "{collector_ip}"
    webit3_pubkey: "{pubkey}"
  tasks:
    - name: 建立收集帳號（無密碼，僅金鑰登入）
      ansible.builtin.user:
        name: "{{{{ webit3_account }}}}"
        shell: /bin/bash
        comment: "webit3 readonly collector"
        create_home: true
        password_lock: true
        state: present

    - name: 佈署收集公鑰（限制來源 IP，關閉所有轉送）
      ansible.posix.authorized_key:
        user: "{{{{ webit3_account }}}}"
        key: "{{{{ webit3_pubkey }}}}"
        key_options: >-
          from="{{{{ webit3_collector_ip }}}}",no-agent-forwarding,
          no-port-forwarding,no-X11-forwarding
        exclusive: false
        state: present

    - name: 唯讀 sudo 白名單（只給機型與序號，不含 /etc/shadow）
      ansible.builtin.copy:
        dest: "/etc/sudoers.d/{{{{ webit3_account }}}}"
        content: "{{{{ webit3_account }}}} ALL=(root) NOPASSWD: /usr/bin/cat /sys/class/dmi/id/*\\n"
        mode: "0440"
        validate: "visudo -cf %s"

    - name: 驗證收集帳號可用
      ansible.builtin.command: id -un
      become: true
      become_user: "{{{{ webit3_account }}}}"
      changed_when: false
"""


def build_windows_script(pubkey: str, collector_ip: str, account: str = DEFAULT_ACCOUNT) -> str:
    """即時組出 Windows 納管腳本（PowerShell）。⚠️ authorized_keys 一律用 ASCII 無 BOM
    寫（PowerShell 5.1 的 -Encoding utf8 會塞 BOM，sshd 讀了會壞——實際踩過）。"""
    return f"""$ErrorActionPreference='Stop'
$Account='{account}'; $CollectorIP='{collector_ip}'
$PubKey='{pubkey}'
if (-not (Get-LocalUser -Name $Account -ErrorAction SilentlyContinue)) {{
  Add-Type -AssemblyName System.Web
  $pw=[System.Web.Security.Membership]::GeneratePassword(24,6)
  New-LocalUser -Name $Account -Password (ConvertTo-SecureString $pw -AsPlainText -Force) `
    -PasswordNeverExpires -UserMayNotChangePassword | Out-Null
  Write-Host "[+] 已建立帳號 $Account"
}} else {{ Write-Host "[=] 帳號已存在" }}
# ⚠️ New-LocalUser **不會**把帳號加進任何群組（Linux 的 useradd 會給主要群組，Windows 不會）。
# 不在 Users 群組就沒有「從網路存取這台電腦」的權限，SSH 一律 Permission denied——
# 實測 .101 就是卡在這裡，而且錯誤訊息完全看不出是群組問題。
try {{
  if (-not (Get-LocalGroupMember -Group 'Users' -ErrorAction SilentlyContinue |
            Where-Object {{ $_.Name -like "*\\$Account" }})) {{
    Add-LocalGroupMember -Group 'Users' -Member $Account -ErrorAction Stop
    Write-Host "[+] 已加入 Users 群組（網路登入所需）"
  }} else {{ Write-Host "[=] 已在 Users 群組" }}
}} catch {{ Write-Host "[!] 加入 Users 群組失敗：$_" }}
$kd='C:\\ProgramData\\ssh\\collector_keys'
New-Item -ItemType Directory -Path $kd -Force | Out-Null
[IO.File]::WriteAllText("$kd\\$Account", $PubKey + "`n", (New-Object System.Text.ASCIIEncoding))
icacls $kd /inheritance:r /grant 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' | Out-Null
$cfg='C:\\ProgramData\\ssh\\sshd_config'; $t=Get-Content $cfg -Raw
$kpath='__PROGRAMDATA__/ssh/collector_keys/%u'
# ⚠️ OpenSSH 對同一個指令只取「第一次出現」的，後面全部忽略。
# Windows 預設 sshd_config 第 38 行左右就有一條生效中的 AuthorizedKeysFile，
# 所以「在 Match 之前另外插一條」完全沒用（實測 .101 就是這樣失敗的）——
# 必須改掉既有那一條，把中央金鑰路徑接上去。
# 先清掉先前版本可能插入的重複行，再處理，確保重跑也會修好。
$t=[regex]::new('(?m)^AuthorizedKeysFile[ \\t]+[^\\r\\n]*collector_keys[^\\r\\n]*\\r?\\n').Replace($t,'')
$rx=[regex]::new('(?m)^(AuthorizedKeysFile[ \\t]+)([^\\r\\n]*)$')
$m=$rx.Match($t)
if ($m.Success) {{
  if ($m.Groups[2].Value -notmatch 'collector_keys') {{
    $t=$rx.Replace($t, ('${{1}}${{2}} ' + $kpath), 1)
  }}
}} else {{
  $ins="AuthorizedKeysFile .ssh/authorized_keys $kpath`r`n`r`n"
  if ($t -match '(?m)^\\s*Match\\b') {{ $t=[regex]::new('(?m)^(\\s*Match\\b)').Replace($t,$ins+'$1',1) }}
  else {{ $t=$t.TrimEnd()+"`r`n`r`n"+$ins }}
}}
[IO.File]::WriteAllText($cfg,$t,(New-Object System.Text.ASCIIEncoding))
Restart-Service sshd
# 跑完自己印出結果——不要讓人事後還得另外下指令查「到底有沒有生效」。
Write-Host ""
Write-Host "--- 驗證：目前生效的 AuthorizedKeysFile（第一條才算數）---"
Select-String -Path $cfg -Pattern '^AuthorizedKeysFile' | ForEach-Object {{ Write-Host ("  " + $_.Line.Trim()) }}
Write-Host "--- 驗證：帳號群組 ---"
foreach ($g in Get-LocalGroup) {{
  try {{
    if (Get-LocalGroupMember -Group $g.Name -ErrorAction Stop |
        Where-Object {{ $_.Name -like "*\\$Account" }}) {{ Write-Host ("  " + $g.Name) }}
  }} catch {{}}
}}
Write-Host "--- 驗證：金鑰檔 ---"
if (Test-Path "$kd\\$Account") {{ Write-Host "  存在 $kd\\$Account" }} else {{ Write-Host "  !! 金鑰檔不存在" }}
Write-Host ""
Write-Host "完成。$CollectorIP 現在可以收集這台。"
"""


COLLECTOR_IP_SETTING = "collector_ip"


def detect_collector_ip() -> str:
    """猜這台對外的位址：開一個 UDP socket「連」到外部位址，看核心挑了哪張網卡。

    不會真的送出封包（UDP connect 只是設定路由），所以不需要對方存在、也不會有流量。
    多網卡時挑的是「預設路由那張」——那正是目標主機會看到的來源位址。
    """
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return ""
    finally:
        s.close()


def resolve_collector_ip(conn=None) -> str:
    """收集器自己的位址。優先序：畫面設定 → 環境變數 → 自動偵測。

    為什麼不再寫死預設值（使用者 2026-08-16 指正）：原本是
    `os.environ.get("ASSET_COLLECTOR_IP", "<開發機 IP>")`，而 patch 走去識別化管道時
    那個字面值被換成佔位字串——只要部署沒設環境變數，就會把佔位字串佈進目標主機的
    authorized_keys `from=`，金鑰永遠被拒卻顯示納管成功。

    改成「自動偵測」當底：多數情況根本不用設定，系統自己知道自己在哪。
    真的要指定（多網卡、走 NAT、對外用 DNS 名）再從畫面覆蓋——這是會變動的設定，
    照這個專案的慣例本來就該在畫面上改，不是藏在部署腳本的環境變數裡。
    """
    if conn is not None:
        try:
            from db import get_setting

            val = (get_setting(conn, COLLECTOR_IP_SETTING, "") or "").strip()
            if val:
                return val
        except Exception:  # noqa: BLE001 - 讀不到設定不該讓納管整條掛掉
            pass
    return (os.environ.get("ASSET_COLLECTOR_IP") or "").strip() or detect_collector_ip()


def validate_collector_ip(collector_ip: str) -> None:
    """收集器位址不是真的位址就拒絕產腳本。

    為什麼要擋（2026-08-16 在公司主機實際踩到）：collector_ip 會被寫進目標主機
    authorized_keys 的 `from=` 來源限制。寫進去的若是佔位字串，sshd 永遠比對不到，
    **金鑰等於被拒**——但納管腳本照樣印「完成」、畫面照樣顯示已納管，之後每次收集
    都連不進去。這是最難查的那種安靜故障：所有紅綠燈都說成功，只有資料永遠不進來。

    這種事會發生是因為 patch 走去識別化管道送出去，原始碼裡的預設 IP 會被換成
    `YOUR_SERVER_IP`；只要部署時沒設 ASSET_COLLECTOR_IP，就會拿佔位字串去佈金鑰。
    與其相信每個部署都記得設環境變數，不如在這裡大聲失敗。
    """
    import ipaddress

    val = (collector_ip or "").strip()
    if not val:
        raise ValueError(
            "沒有設定收集器位址（ASSET_COLLECTOR_IP）——納管腳本需要它來限制金鑰來源。"
            "請在服務的環境變數設成這台收集器的實際位址後重啟。")
    try:
        addr = ipaddress.ip_address(val)
    except ValueError:
        addr = None
    if addr is not None:
        # 合法 IP 還不夠：from= 是寫進**目標主機**的，這幾種在那邊指的不是收集器。
        # 127.0.0.1 指目標自己、0.0.0.0 不是可連的來源——填了等於金鑰永遠被拒。
        if addr.is_loopback or addr.is_unspecified or addr.is_multicast:
            raise ValueError(
                f"收集器位址不能是「{val}」：它會被寫進**目標主機**的 from= 來源限制，"
                f"在那台上指的不是這台收集器，金鑰會永遠被拒。"
                f"請把 ASSET_COLLECTOR_IP 設成其他機器連得到的實際位址。")
        return
    # 允許主機名（有些環境用 DNS 名而不是 IP），但擋掉一看就知道沒填的佔位字串
    placeholder = ("your_", "your-", "changeme", "example", "x.x.x.x", "localhost")
    low = val.lower()
    if any(p in low for p in placeholder) or "_" in val:
        raise ValueError(
            f"收集器位址看起來是沒填的佔位值：「{val}」。它會被寫進目標主機 "
            f"authorized_keys 的 from= 來源限制，填錯會讓金鑰永遠被拒——而納管仍會"
            f"顯示成功。請把服務的 ASSET_COLLECTOR_IP 設成這台收集器的實際位址後重啟。")


def default_account_for(platform: str) -> str:
    """該平台的預設收集帳號名。AIX 因為 max_logname 上限用較短的名字（見上方常數）。"""
    return DEFAULT_ACCOUNT_AIX if platform == "aix" else DEFAULT_ACCOUNT


def build_script(platform: str, pubkey: str, collector_ip: str,
                 account: str | None = None) -> str:
    # 在這裡擋而不是在各呼叫端：遠端納管、本機一行指令、排程自動納管、Ansible playbook
    # 全部經過這裡，擋一次就四條路都守到。
    validate_collector_ip(collector_ip)
    account = account or default_account_for(platform)
    if platform == "windows":
        return build_windows_script(pubkey, collector_ip, account)
    if platform == "linux":
        return build_linux_script(pubkey, collector_ip, account)
    if platform == "aix":
        return build_aix_script(pubkey, collector_ip, account)
    raise ValueError(f"未支援的平台：{platform}（只支援 linux／aix／windows）")


# ===== 執行器：真正碰網路的部分，抽成可注入 =====

@dataclass
class OnboardResult:
    ok: bool
    stage: str          # connect / execute / verify
    message: str
    output: str = ""


def probe_target(host: str, username: str, password: str, timeout: int = 20,
                 runner=None) -> dict:
    """登入後問機器自己：你是什麼 OS、我是不是 root、有沒有 sudo。

    ## 為什麼要有這一步（2026-08-16 公司主機連續踩到兩次）

    1. **平台從網路上分不出來**：AIX 與 Linux 的 SSH banner 長得一樣，畫面只能請人
       自己選——選錯就拿 useradd 去打 AIX（或反過來），一定失敗。但**登進去之後
       一行 `uname -s` 就確定了**，沒有理由讓人猜。
    2. **不是每台都有 sudo，也不是每台都需要**：實際錯誤是
       `bash: line 1: sudo: command not found`。登入身分若本來就是 root，根本不必
       走 sudo；不是 root 又沒有 sudo，那該一開始就講清楚，而不是讓腳本跑到一半
       噴一句看不懂的話。

    回 {"os": "linux"/"aix"/"windows"/"", "uid": int|None, "has_sudo": bool}。
    runner 可注入，測試不碰真網路、不碰真密碼。
    """
    if runner is not None:
        raw = runner(host, username, password)
    else:
        env = dict(os.environ)
        env["SSHPASS"] = password
        cmd = ["sshpass", "-e", "ssh", "-o", "StrictHostKeyChecking=no",
               "-o", f"ConnectTimeout={timeout}", f"{username}@{host}",
               "uname -s; id -u; command -v sudo >/dev/null 2>&1 && echo HASSUDO || echo NOSUDO"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout + 15, env=env)
            raw = (r.stdout or "") + (r.stderr or "")
        except (subprocess.SubprocessError, OSError) as exc:
            return {"os": "", "uid": None, "has_sudo": False, "error": str(exc)[:200]}
        finally:
            env.pop("SSHPASS", None)
    return parse_probe(raw)


def parse_probe(raw: str) -> dict:
    """把探測輸出解析成平台/身分。純函式，好測。"""
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    text = " ".join(lines).lower()
    plat = ""
    if "aix" in text:
        plat = "aix"
    elif "linux" in text:
        plat = "linux"
    elif any(k in text for k in ("cygwin", "msys", "mingw", "windows")):
        plat = "windows"
    uid = None
    for ln in lines:
        if ln.isdigit():
            uid = int(ln)
            break
    return {"os": plat, "uid": uid, "has_sudo": "HASSUDO" in (raw or "")}


def _sshpass_executor(host: str, username: str, password: str, platform: str,
                      script: str, collector_ip: str, timeout: int = 40,
                      as_root: bool = False) -> OnboardResult:
    """用 sshpass 以密碼登入目標，執行納管腳本。

    密碼只透過 SSHPASS 環境變數給 sshpass 這個短命子行程——
    不進 argv（`ps` 看不到）、不寫任何檔案。函式回傳後 env 副本即消失。

    ⚠️ 未在真機驗證（家裡不能碰目標密碼、也不該由 AI 處理密碼）——這段的
    SSH/sudo 管線要靠 UI 觸發、對真機驗。已知前提與待驗點：
    - **Linux 假設登入帳號能 sudo 建帳號**（維運帳號常見 NOPASSWD sudo，或本身是 root）。
      若 sudo 另需密碼，這裡會失敗——那要改成 paramiko 互動式 sudo，屬 on-target 再處理。
      （原本想用 `sudo -S` 從 stdin 餵密碼，但 stdin 已被 base64 管線佔住，餵不進去。）
    - Windows 假設登入帳號是系統管理員、且已有 OpenSSH Server。
    - **AIX 必須以 root 登入**：AIX 未必裝 sudo（常在 /opt/freeware/bin 或改用 RBAC），
      不能像 Linux 那樣假設 `sudo bash` 會通。而且沒有 GNU coreutils 的 `base64`，
      所以走 `openssl base64 -d`（AIX 標配）＋ ksh，不是 base64＋bash。
    """
    b64 = base64.b64encode(script.encode()).decode()
    env = dict(os.environ)
    env["SSHPASS"] = password

    def _run_streaming(cmd):
        """逐行讀 stdout，邊跑邊回報進度，而不是等結束才一次拿到。

        為什麼不用 subprocess.run：它會等到行程結束才把輸出交出來，於是使用者
        在畫面上看到的是幾十秒的空白——資訊明明一直在產生，只是被關著。
        stderr 併進 stdout：目標主機的錯誤訊息也要即時看得到。
        """
        import threading

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, env=env)
        collected = []
        # ⚠️ 一定要有看門狗：`for line in proc.stdout` **本身沒有逾時**——目標主機若
        # 不吐任何東西又不結束（sudo 在等密碼、SSH 卡在半開連線），這個迴圈會永遠
        # 阻塞。原本的 subprocess.run(timeout=) 有上限，改成串流時把它弄丟了，
        # 症狀就是畫面上的秒數一直跑、超過上限也不回來（2026-08-16 公司主機看到 90s+）。
        timed_out = {"hit": False}

        def _kill_on_deadline():
            timed_out["hit"] = True
            try:
                proc.kill()
            except OSError:
                pass

        killer = threading.Timer(timeout + 30, _kill_on_deadline)
        killer.start()
        try:
            for line in proc.stdout:
                collected.append(line)
                progress_note(host, line)
            proc.wait(timeout=10)
        finally:
            killer.cancel()
            if proc.poll() is None:
                proc.kill()
            if proc.stdout:
                proc.stdout.close()
        if timed_out["hit"]:
            collected.append(
                f"\n[!] 超過 {timeout + 30} 秒沒有完成，已強制中止。"
                f"常見原因：登入帳號的 sudo 需要密碼（本流程沒辦法回答它）、"
                f"或 SSH 連線卡在半開狀態。\n")
        return proc.returncode, "".join(collected)

    try:
        if platform == "linux":
            # 已經是 root 就別加 sudo：實際錯誤是 `sudo: command not found`——
            # 那台根本沒裝 sudo，而它其實不需要（登入身分已是 root）。
            inner = (f"echo {b64} | base64 -d | bash" if as_root
                     else f"echo {b64} | base64 -d | sudo bash")
            cmd = ["sshpass", "-e", "ssh",
                   "-o", "StrictHostKeyChecking=no", "-o", f"ConnectTimeout={timeout}",
                   f"{username}@{host}", inner]
            rc, out_all = _run_streaming(cmd)
        elif platform == "aix":
            # 不加 sudo：AIX 未必有；改成要求以 root 登入（UI 會講清楚）
            inner = f"echo {b64} | openssl base64 -d -A | ksh"
            cmd = ["sshpass", "-e", "ssh",
                   "-o", "StrictHostKeyChecking=no", "-o", f"ConnectTimeout={timeout}",
                   f"{username}@{host}", inner]
            rc, out_all = _run_streaming(cmd)
        else:  # windows
            inner = (f"powershell -NoProfile -Command "
                     f"\"[IO.File]::WriteAllText($env:TEMP+'\\wb.ps1',"
                     f"[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')),"
                     f"(New-Object Text.UTF8Encoding $true)); "
                     f"powershell -ExecutionPolicy Bypass -File $env:TEMP\\wb.ps1\"")
            cmd = ["sshpass", "-e", "ssh", "-o", "StrictHostKeyChecking=no",
                   "-o", f"ConnectTimeout={timeout}", f"{username}@{host}", inner]
            rc, out_all = _run_streaming(cmd)
    except subprocess.SubprocessError as exc:
        return OnboardResult(False, "connect", f"連線／執行失敗：{exc}")
    finally:
        env.pop("SSHPASS", None)  # 明確清掉，不留在這層的 env 副本

    out = out_all
    if rc != 0 or "完成。" not in out:
        stage, msg = classify_failure(out)
        return OnboardResult(False, stage, msg, out[-800:])
    return OnboardResult(True, "execute", "納管腳本執行完成", out[-800:])


# 連線層的失敗訊息。命中代表**根本沒進到目標機**，跟「進去了但腳本沒跑完」
# 要查的方向完全不同（前者查網路/埠/帳密，後者查權限/sudo）。
_CONNECT_ERRORS = (
    ("connection timed out", "連不到這台的 22 埠（逾時）——確認機器開著、"
                             "SSH 服務有跑、防火牆放行；Windows 通常沒開 22，該走 WinRM"),
    ("connection refused", "對方拒絕連線——22 埠沒有服務在聽"),
    ("no route to host", "路由不通——網段之間可能沒開通"),
    ("could not resolve hostname", "解析不到這個位址"),
    ("host key verification failed", "主機金鑰驗證失敗"),
    ("permission denied", "登入被拒——帳號或密碼不對，或該帳號不允許 SSH 登入"),
    ("no supported authentication", "登入方式不被接受——該機可能不允許密碼登入"),
)


def classify_failure(out: str) -> tuple[str, str]:
    """從輸出判斷失敗發生在哪一階段，並給看得懂的原因。

    為什麼不能一律報 execute（2026-08-16 公司主機發現）：畫面把 execute 解釋成
    「進去了但腳本沒跑完（多半是權限或 sudo）」，於是一台**連 22 都連不上**的
    Windows 機器被標成 execute，人會照著提示去查 sudo 權限——查一個根本不存在的
    問題。階段標錯比沒有階段更糟，因為它會主動把人引去錯的方向。
    """
    low = (out or "").lower()
    for needle, why in _CONNECT_ERRORS:
        if needle in low:
            return "connect", why
    if "超過" in (out or "") and "強制中止" in (out or ""):
        return "connect", "逾時被中止——多半是登入帳號的 sudo 在等密碼，或連線卡住"
    if "需要 root" in (out or ""):
        return "execute", "進得去，但執行身分不是 root（腳本需要 root 才能建帳號）"
    if "sudo" in low and ("password" in low or "密碼" in (out or "")):
        return "execute", "進得去，但 sudo 需要密碼——本流程沒辦法回答它"
    return "execute", "腳本執行未回報完成"


def onboard(host: str, platform: str, username: str, password: str,
            collector_ip: str, pubkey: str | None = None,
            executor=None, account: str | None = None) -> OnboardResult:
    """把一台主機納管起來。

    ⚠️ password 只在本函式與 executor 之間傳遞、用完即丟——
    絕不寫進回傳值、DB、log。呼叫端（API）也必須遵守：收到就用、用完不留。
    executor 可注入，測試不碰真網路、不碰真密碼。
    """
    if platform not in ("linux", "aix", "windows"):
        return OnboardResult(False, "connect", f"未知平台：{platform}")

    # 登入後先問機器自己是什麼，不要相信畫面上選的（2026-08-16 公司主機踩到兩次）：
    # AIX 與 Linux 從網路上分不出來，選錯就拿 useradd 去打 AIX，一定失敗——
    # 但登進去一行 uname -s 就確定了，沒有理由讓人猜。
    # 只在真的要連網路時做（executor 有注入代表在測試，不多打一次網路）。
    detected = {}
    if executor is None and platform != "windows":
        detected = probe_target(host, username, password)
        real = detected.get("os")
        if real and real != platform:
            platform = real
        uid, has_sudo = detected.get("uid"), detected.get("has_sudo")
        if uid is not None and uid != 0 and not has_sudo:
            return OnboardResult(
                False, "connect",
                f"登入帳號 {username} 不是 root，而且這台沒有 sudo——納管需要 root "
                f"才能建帳號、寫 authorized_keys。請改用 root 登入"
                f"（AIX 常見；Linux 也可能沒裝 sudo）。")

    try:
        pubkey = pubkey or collector_pubkey()          # 金鑰還沒產生也走這條
        script = build_script(platform, pubkey, collector_ip, account)
    except ValueError as exc:   # 例如 AIX 帳號名超過 max_logname、公鑰讀不到
        return OnboardResult(False, "connect", str(exc))
    run = executor or _sshpass_executor
    kw = {}
    if executor is None:
        kw["as_root"] = detected.get("uid") == 0
    return run(host=host, username=username, password=password, platform=platform,
               script=script, collector_ip=collector_ip, **kw)


# ---- 診斷外掛 ----
try:
    import diagnostics

    @diagnostics.register("onboard")
    def _diag(conn) -> dict:
        """納管稽核（不含憑證）＋引擎現況。"""
        try:
            audit = [dict(r) for r in conn.execute(
                "SELECT target_ip, platform, login_user, trigger, ok, stage, message, "
                "created_at FROM onboard_audit ORDER BY id DESC LIMIT 50")]
        except Exception:  # noqa: BLE001
            audit = []
        pub_ok = os.path.exists(COLLECTOR_KEY_PUB)
        return {"collector_pubkey_present": pub_ok, "recent_onboards": audit}
except ImportError:
    pass
