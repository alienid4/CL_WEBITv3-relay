"""納管引擎：把一台「發現了但進不去」的主機，帶到「已納管、系統收得到」。

一顆引擎，兩種觸發（使用者 2026-07-19 定案）：
    A（UI 一鍵）  憑證＝人當下輸入、用完即丟
    B（排程自動）  憑證＝授權過的金鑰庫；只碰標為「可自動納管」的網段
兩者共用這顆引擎，差別只在「誰觸發、憑證哪來」。這一版先做引擎本身。

## 三條寫死的安全底線

1. **憑證絕不落地**：登入用的帳密只在一次納管過程存在於記憶體，
   **不寫 DB、不寫 log、不進稽核紀錄、不進診斷包**。密碼交給程式內的 SSH 用戶端
   （paramiko）直接走 SSH 協定，不經環境變數、不進 argv（`ps` 看不到）、不落磁碟，
   也不啟動任何外部的密碼代填工具。
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
import codecs
import errno
import json
import os
import re
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# 收集金鑰位置。deploy.sh 會在這裡產生（冪等），私鑰永遠只留在收集器這台。
# 可用環境變數覆蓋：公司環境的安裝路徑不一定是 /opt/webit3（setup.sh 可改 WEBIT_DATA），
# 寫死的話會出現「金鑰產在 A、程式去 B 找」這種各說各話的狀況。
COLLECTOR_KEY_PUB = os.environ.get(
    "ASSET_COLLECTOR_KEY", "/opt/webit3/.collector_key") + ".pub"
#: SSH 主機金鑰驗證的共用選項。**全專案唯一一份**，不要各處自己寫。
#:
#: ⚠️ 2026-08-28 從 `StrictHostKeyChecking=no` 改過來。原本那個等於**關掉主機金鑰
#: 驗證＝自願接受中間人攻擊**，是稽核必開的缺失（使用者的天條明列禁止）。
#:
#: `accept-new` 的語意：**首次見到就接受並記錄，之後金鑰變更即拒絕**。
#: 這正是大量納管要的行為——第一次接觸幾百台新主機時不會卡住，
#: 但任何一台的金鑰之後被換掉（可能是中間人）就會擋下來。
#:
#: 必須明確指定 `UserKnownHostsFile`：服務以非登入身分執行時 `~/.ssh` 未必可寫，
#: 寫不進去的話 `accept-new` 每次都當成「首見」，等於退化回沒有驗證。
SSH_KNOWN_HOSTS = os.environ.get(
    "WEBIT3_KNOWN_HOSTS", "/opt/webit3/data/known_hosts")
SSH_HOSTKEY_OPTS = [
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", f"UserKnownHostsFile={SSH_KNOWN_HOSTS}",
]

#: 腳本輸出裡的機器可讀標記：這台沒有 sudo，唯讀白名單被跳過了。
#:
#: 為什麼要一個標記而不是只印中文：納管**成功了**（帳號、公鑰都佈好，絕大多數
#: 收集項目照常運作），但它是**降級的成功**——硬體序號收不到。
#: 只回「納管成功」等於把這件事藏起來，之後有人看到序號空白會以為是收集壞了。
#:
#: ⚠️ 少的**只有序號**，不是連機型也少。2026-08-28 在真機查證：
#:     -r--r--r--  /sys/class/dmi/id/product_name     ← 一般帳號讀得到
#:     -r--------  /sys/class/dmi/id/product_serial   ← 只有 root
#: 第一版訊息寫成「序號與機型都收不到」，那會害人為了根本沒少的東西跑去裝 sudo。
#: facts_collector 本來就寫了 `sudo -n cat … || cat …` 的退路，不需要改。
#:
#: 2026-08-28 公司實測 Debian 11 踩到（那台連 /etc/sudoers.d 目錄都沒有）。
NO_SUDO_MARK = "WEBIT3_NO_SUDO"
#: sudoers 語法被 visudo 擋下（已還原成舊的）。**要跟「沒有 sudo」分開**：
#: 沒有 sudo 是那台環境如此，白名單被擋是**我們寫錯規則**，要修的是這邊。
SUDOERS_INVALID_MARK = "WEBIT3_SUDOERS_INVALID"
#: 舊收集帳號換新（2026-10-18）：新帳號本機驗過、舊的已移除／驗不過舊的保留。上層靠這兩個標記回報。
LEGACY_REMOVED_MARK = "WEBIT3_LEGACY_REMOVED"
LEGACY_KEPT_MARK = "WEBIT3_LEGACY_KEPT"


def legacy_accounts() -> tuple[str, ...]:
    """要換掉的舊收集帳號名（唯一來源 manage_state.LEGACY_LINUX_ACCOUNT）。"""
    import manage_state

    return (manage_state.LEGACY_LINUX_ACCOUNT,)


def target_account(account: str | None, platform: str = "linux") -> str:
    """納管要建的帳號：舊名（webit3scan）一律換成新名——重新納管就是遷移的那一刻。
    （2026-10-18 公司機實例：設定還是舊名，重新納管照舊名建，於是永遠換不過來。）"""
    account = account or default_account_for(platform)
    return DEFAULT_ACCOUNT if account in legacy_accounts() else account


def legacy_migration_sh() -> str:
    """Linux：這台還留著舊收集帳號（webit3scan）時，**先驗新帳號、驗過才移除舊的**。

    使用者 2026-10-18：「可以先刪掉再加入，因為真的有問題，我本來就有 sysinfra 可以進去」。
    順序刻意改成「新帳號建好 → 驗 → 刪舊」：最後狀態一樣（只剩新帳號），但中間沒有一刻這台是收不到的；
    驗不過就不刪，舊帳號照樣能收。

    驗的是「新舊帳號之間可能不一樣」的每一樣（網路路徑、收集金鑰、from= 位址兩者相同）：
    shell 可登入、收集公鑰在 authorized_keys 且擁有者對、家目錄／.ssh 不可被他人寫（StrictModes）、
    舊帳號有 sudo 白名單的話新帳號也要有（root 以 `sudo -l -U` 查）、sshd 對每個收集器位址
    沒有 AllowUsers／DenyUsers 擋新帳號（sshd -T -C）。收集器那端緊接著再用新帳號實際連一次
    （collect_account_migration.settle_after_onboard），連得上才把這台的收集帳號記成新帳號。

    移除前先備份舊帳號的 sudoers.d 與 authorized_keys 到 /var/backups/webit3/，restore.sh 補上
    「把舊帳號建回來」的指令。需要：ACCOUNT、PUBKEY、HELPER、COLLECTOR_IPS；BK／R／SSHD_BIN 有就用。
    內容不得出現 Jinja 的 `{{`、`{%`、`{#`（Ansible 劇本也嵌同一段）。
    """
    olds = " ".join(legacy_accounts())
    return f"""# ---- 舊收集帳號換新：新帳號驗過才移除舊的（驗不過舊的留著）----
for OLD in {olds}; do
  [ "$OLD" != "$ACCOUNT" ] || continue
  id "$OLD" >/dev/null 2>&1 || continue
  echo "[*] 這台還有舊收集帳號 $OLD：先驗新帳號 $ACCOUNT，驗過才移除舊的"
  MIG_WHY=""
  NHOME="$(getent passwd "$ACCOUNT" | cut -d: -f6)"
  NSHELL="$(getent passwd "$ACCOUNT" | cut -d: -f7)"
  case "$NSHELL" in */nologin|*/false|"") MIG_WHY="$MIG_WHY；shell 不能登入（$NSHELL）" ;; esac
  NAUTH="$NHOME/.ssh/authorized_keys"
  if ! grep -qF "$PUBKEY" "$NAUTH" 2>/dev/null; then MIG_WHY="$MIG_WHY；收集公鑰不在 $NAUTH"; fi
  if [ "$(stat -c %U "$NAUTH" 2>/dev/null || true)" != "$ACCOUNT" ]; then MIG_WHY="$MIG_WHY；authorized_keys 擁有者不是 $ACCOUNT"; fi
  if [ -n "$(find "$NHOME" "$NHOME/.ssh" -maxdepth 0 -perm /022 2>/dev/null || true)" ]; then
    MIG_WHY="$MIG_WHY；家目錄或 .ssh 可被他人寫入（sshd StrictModes 會拒）"
  fi
  if [ -f "/etc/sudoers.d/$OLD" ] && command -v sudo >/dev/null 2>&1; then
    SL="$(sudo -l -U "$ACCOUNT" 2>/dev/null || true)"
    case "$SL" in *"$HELPER"*) : ;; *) MIG_WHY="$MIG_WHY；新帳號的 sudo 白名單沒生效" ;; esac
  fi
  MSSHD="${{SSHD_BIN:-/usr/sbin/sshd}}"
  if [ -x "$MSSHD" ]; then
    for ip in $COLLECTOR_IPS; do
      T="$("$MSSHD" -T -C "user=$ACCOUNT,addr=$ip,host=webit3-check" 2>/dev/null || true)"
      AU="$(printf '%s\\n' "$T" | awk '$1=="allowusers"{{print $2}}')"
      if [ -n "$AU" ]; then
        OKU=0
        set -f
        for e in $AU; do case "$e" in "$ACCOUNT"|"$ACCOUNT@$ip"|"$ACCOUNT@*"|"*") OKU=1 ;; esac; done
        set +f
        if [ "$OKU" = "0" ]; then MIG_WHY="$MIG_WHY；sshd AllowUsers 沒放行 $ACCOUNT@$ip"; fi
      fi
      DUS=" $(printf '%s\\n' "$T" | awk '$1=="denyusers"{{printf "%s ", $2}}') "
      case "$DUS" in *" $ACCOUNT "*) MIG_WHY="$MIG_WHY；sshd DenyUsers 擋 $ACCOUNT" ;; esac
    done
  fi
  if [ -n "$MIG_WHY" ]; then
    echo "[!] {LEGACY_KEPT_MARK} 新帳號 $ACCOUNT 驗證沒過（${{MIG_WHY#；}}），舊收集帳號 $OLD 保留、沒有刪"
    continue
  fi
  BKL="${{BK:-/var/backups/webit3/$(date +%Y%m%d_%H%M%S)_legacy_$$}}"
  install -d -m 700 "$BKL"
  RL="${{R:-$BKL/restore.sh}}"
  if [ ! -f "$RL" ]; then
    printf '%s\\n' '#!/usr/bin/env bash' 'set -eu' 'if [ "$(id -u)" -ne 0 ]; then echo "需要 root" >&2; exit 1; fi' > "$RL"
    chmod 700 "$RL"
  fi
  OHOME="$(getent passwd "$OLD" | cut -d: -f6)"
  OGECOS="$(getent passwd "$OLD" | cut -d: -f5)"
  {{
    echo "# ---- 把舊收集帳號 $OLD 建回來（納管時換成 $ACCOUNT 後移除；備份在 $BKL）----"
    printf 'id %q >/dev/null 2>&1 || {{ useradd -m -s /bin/bash -c %q %q && passwd -l %q >/dev/null; }}\\n' "$OLD" "$OGECOS" "$OLD" "$OLD"
  }} >> "$RL"
  if [ -f "/etc/sudoers.d/$OLD" ]; then
    cp -p "/etc/sudoers.d/$OLD" "$BKL/legacy_${{OLD}}_sudoers"
    printf 'install -m 440 -o root -g root %q %q\\n' "$BKL/legacy_${{OLD}}_sudoers" "/etc/sudoers.d/$OLD" >> "$RL"
  fi
  if [ -f "$OHOME/.ssh/authorized_keys" ]; then
    cp -p "$OHOME/.ssh/authorized_keys" "$BKL/legacy_${{OLD}}_authorized_keys"
    printf 'H=$(getent passwd %q | cut -d: -f6); install -d -m 700 -o %q -g %q "$H/.ssh"; install -m 600 -o %q -g %q %q "$H/.ssh/authorized_keys"\\n' \\
      "$OLD" "$OLD" "$OLD" "$OLD" "$OLD" "$BKL/legacy_${{OLD}}_authorized_keys" >> "$RL"
  fi
  echo "echo '舊收集帳號 $OLD 已建回（收集帳號要改回它，請到系統把這台的收集帳號改回）'" >> "$RL"
  # 先停掉舊帳號正在跑的收集連線（不然 userdel 會說帳號使用中），再刪 sudoers 與帳號
  pkill -KILL -u "$OLD" 2>/dev/null || true
  sleep 1
  rm -f "/etc/sudoers.d/$OLD"
  userdel -r "$OLD" >/dev/null 2>&1 || userdel -f -r "$OLD" >/dev/null 2>&1 || true
  if id "$OLD" >/dev/null 2>&1; then
    echo "[!] {LEGACY_KEPT_MARK} 移除舊收集帳號 $OLD 失敗（userdel），請人工確認；新帳號 $ACCOUNT 已可用"
  else
    echo "[+] {LEGACY_REMOVED_MARK} 舊收集帳號 $OLD 已移除（新帳號 $ACCOUNT 驗過；sudoers／authorized_keys 備份在 $BKL，要建回：bash $RL）"
  fi
done
"""

#: 收集帳號在 /etc/passwd 的備註欄（GECOS）預設值。
#:
#: ⚠️ **實際值不寫在這裡**。管理者可能填含個資的字串（曾考慮「員工編號-姓名_部門_系統」），
#: 而這個檔案會進 relay（公開 repo）。寫死等於
#: 把個資推上公開程式庫，而且去識別化替換表會把姓名換掉，佈到主機上的會是
#: 被改過的字串。
#:
#: 所以走跟分類清單同一套：程式碼只留中性預設，實際值存 app_settings
#: （`onboard_account_comment`），由管理者在畫面上填一次。
DEFAULT_ACCOUNT_COMMENT = "WEBIT3 collector"

#: 納管時要在目標主機上建立的收集帳號。**所有平台同一個名字**
#: （使用者 2026-09-23：「還是統一都叫 webit3sc」）。
#:
#: 8 個字元是被 AIX 的 `max_logname` 逼出來的（預設只給 8，放寬要 chdev 並重開機），
#: Linux 沒有這個限制，所以兩邊能共用的只有短的那個。
#:
#: ⚠️ 為什麼要統一：以前 Linux 建 webit3scan、AIX 建 webit3sc，
#: 每個呼叫端都得記得帶 platform。2026-09-23 四個收集器忘了帶，
#: 全部拿 webit3scan 去登 AIX，而那個帳號在 AIX 上不存在——
#: 每條指令都回空字串，症狀跟「指令有問題」一模一樣，查了一整天。
#: **一個名字就沒有「忘了帶 platform」這回事。**
#:
#: ⚠️ 既有的 Linux 主機上是舊名 webit3scan，**重新納管才會換過來**。
#: 換過來之前不要去翻 collect_ssh_account 設定，翻了那些機器會立刻全部收不到。
DEFAULT_ACCOUNT = "webit3sc"
DEFAULT_ACCOUNT_AIX = DEFAULT_ACCOUNT
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
    if path == COLLECTOR_KEY_PUB:
        # ROOTKEY1：root 收集金鑰也在這裡備好（只在不存在時產，絕不覆蓋）。
        # 只有預設路徑才產——測試或指定路徑讀公鑰時不在別處多產一把。
        try:
            import root_key

            root_key.ensure_root_key()
        except Exception:  # noqa: BLE001 - 產不出來時納管腳本會印「沒佈 root 金鑰」，不擋 webit3sc
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


# ★ 天條：ESXi 不可納管（2026-10-17）。最後一道防線寫進**腳本本身**：
# 不管是引擎送的、還是有人把本機一行版／playbook 貼到 ESXi 上跑，第一件事就是認出 ESXi 並離開，
# 在任何 useradd／mkuser／sudoers／authorized_keys 之前。exit 3 = 刻意拒絕（不是執行失敗）。
# 2026-10-18 使用者擴大天條（「連帳號都不能建立」）：vCenter Server Appliance（VCSA，Photon OS）
# 也是 VMware 管的設備，`uname -s` 回 Linux，只靠 VMkernel 擋不到。改用 VCSA 特有的標記檔／目錄
# 辨識——全部是唯讀的存在測試，不讀內容。exit 3 = 刻意拒絕。
VCSA_MARKER_TEST = ("[ -e /etc/photon-release ] || [ -d /etc/applmgmt/appliance ] || "
                    "[ -d /usr/lib/vmware-vpx ] || command -v vpxd >/dev/null 2>&1")
VCSA_REFUSED_MESSAGE = ("VMware 設備（vCenter）不可納管，請改由 vCenter API／RVTools 取得"
                        "（未建帳號、未佈金鑰、未改 sudoers）")
ESXI_SCRIPT_GUARD_SH = ('if [ "$(uname -s 2>/dev/null)" = "VMkernel" ]; then '
                        'echo "ESXi 不可納管，請改由 vCenter／RVTools 取得" >&2; exit 3; fi\n'
                        f'if {VCSA_MARKER_TEST}; then '
                        'echo "VMware 設備（vCenter）不可納管，請改由 vCenter API／RVTools 取得" >&2; '
                        'exit 3; fi')
ESXI_SCRIPT_GUARD_PS = ("if (Get-Command uname -ErrorAction SilentlyContinue) { "
                        "if ((uname -s) -eq 'VMkernel') { "
                        "[Console]::Error.WriteLine('ESXi 不可納管，請改由 vCenter／RVTools 取得'); "
                        "exit 3 } }")


# ★ 平台白名單（2026-10-19 使用者：「很害怕納管到網路設備，或者是非 Red Hat、非 CentOS、Debian 的設備」；
# 同日拍板「Alma，Oracle Linux 擋」）。fail-closed：uname=Linux 的主機，只有 os-release 的 ID 在白名單、
# 且沒有命中任何設備標記才納管。網路設備多半是非 POSIX shell，在 uname 那關就擋掉了；這一關擋的是
# 「底下是 Linux 的設備」（F5、Nutanix CVM、Exadata、防火牆管理機…）與非白名單發行版。
# 規則放 onboard_platform_rules.json（可改），兩處用同一份：
#   1. 引擎探測（PROBE_COMMAND 多問 os-release＋標記；parse_probe → platform_refusal）
#   2. 腳本本身（PLATFORM_GUARD_SH，跟 ESXI_SCRIPT_GUARD_SH 一樣是縱深防禦；人手跑 webit3_onboard.sh 也擋）
# 全部是唯讀的存在／內容測試。exit 5 = 刻意拒絕（3 是 VMware、4 是要 root）。
PLATFORM_RULES_PATH = Path(__file__).with_name("onboard_platform_rules.json")
PLATFORM_REFUSED_STAGE = "refused_platform"
PLATFORM_REFUSED_EXIT = 5
_SAFE_ID = re.compile(r"^[a-z0-9._-]+$")
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/+-]+$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9 ._+-]+$")
_UNSAFE_NAME_CHARS = set("'\"`$\\;&|<>\r\n")


def load_platform_rules(path: Path | None = None) -> dict:
    """讀白名單設定並驗字元（值會被組進 shell 指令，不安全的字一律拒絕載入）。

    讀不到或格式不對就丟例外——寧可整個納管起不來，也不要靜默變成「沒有白名單」。
    """
    cfg = json.loads(Path(path or PLATFORM_RULES_PATH).read_text(encoding="utf-8"))
    ids = [str(x).strip().lower() for x in cfg.get("allowed_ids") or []]
    if not ids or not all(_SAFE_ID.match(x) for x in ids):
        raise ValueError(f"onboard_platform_rules.json：allowed_ids 不可為空且只能是小寫英數：{ids}")
    leg = cfg.get("legacy_release") or {}
    prefixes = []
    for p in leg.get("prefixes") or []:
        pre, pid = str(p.get("prefix") or ""), str(p.get("id") or "").lower()
        if not (_SAFE_TEXT.match(pre) and _SAFE_ID.match(pid)):
            raise ValueError(f"onboard_platform_rules.json：legacy_release.prefixes 字元不安全：{p}")
        prefixes.append({"prefix": pre, "id": pid})
    leg_file = str(leg.get("file") or "/etc/redhat-release")
    deny = [str(x) for x in leg.get("deny_files") or []]
    for x in [leg_file, *deny]:
        if not _SAFE_PATH.match(x):
            raise ValueError(f"onboard_platform_rules.json：路徑字元不安全：{x}")
    markers = []
    for m in cfg.get("appliance_markers") or []:
        mid, name, mpath = str(m.get("id") or ""), str(m.get("name") or ""), str(m.get("path") or "")
        contains = m.get("contains")
        if not (_SAFE_ID.match(mid) and _SAFE_PATH.match(mpath) and name
                and not (set(name) & _UNSAFE_NAME_CHARS)):
            raise ValueError(f"onboard_platform_rules.json：appliance_markers 字元不安全：{m}")
        if contains is not None and not _SAFE_TEXT.match(str(contains)):
            raise ValueError(f"onboard_platform_rules.json：contains 字元不安全：{m}")
        markers.append({"id": mid, "name": name, "path": mpath,
                        "contains": None if contains is None else str(contains)})
    return {"allowed_ids": ids, "legacy_file": leg_file, "legacy_prefixes": prefixes,
            "legacy_deny_files": deny, "markers": markers}


def _marker_test_sh(m: dict) -> str:
    if m["contains"] is None:
        return f"[ -e {m['path']} ]"
    return f"grep -F -q '{m['contains']}' {m['path']} 2>/dev/null"


def platform_probe_sh(rules: dict | None = None) -> str:
    """探測用（唯讀）：印 os-release 四個欄位、舊版 release 第一行、命中的標記 id。"""
    r = rules or load_platform_rules()
    parts = ["for webit3_f in /etc/os-release /usr/lib/os-release; do "
             "if [ -r $webit3_f ]; then echo OSREL_FILE=$webit3_f; grep -E '^(ID|ID_LIKE|VERSION_ID|PRETTY_NAME)=' $webit3_f "
             "| sed 's/^/OSREL_/'; break; fi; done",
             f"[ -r {r['legacy_file']} ] && head -n 1 {r['legacy_file']} | sed 's/^/LEGACYREL_/'"]
    parts += [f"[ -e {d} ] && echo LEGACYDENY={d}" for d in r["legacy_deny_files"]]
    parts += [f"{_marker_test_sh(m)} && echo APPLIANCE_MARKER={m['id']}" for m in r["markers"]]
    return "; ".join(p + " || true" for p in parts)


def platform_guard_sh(rules: dict | None = None, result_prefix: str = "") -> str:
    """腳本自我守門（POSIX sh／bash／ksh 都能跑）：只在 uname=Linux 時判；任何建帳號之前。

    result_prefix：給 webit3_onboard.sh 外層用——訊息改印到 stdout 並帶「停止：」，成為最後一行結果。
    """
    r = rules or load_platform_rules()
    to = "" if result_prefix else " >&2"
    lines = ['if [ "$(uname -s 2>/dev/null)" = "Linux" ]; then',
             '  webit3_mk=""']
    for m in r["markers"]:
        lines.append(f"  if [ -z \"$webit3_mk\" ] && {_marker_test_sh(m)}; then webit3_mk='{m['name']}'; fi")
    lines += [
        f'  if [ -n "$webit3_mk" ]; then echo "{result_prefix}偵測到設備標記：$webit3_mk——未納管"{to}; '
        f'exit {PLATFORM_REFUSED_EXIT}; fi',
        '  webit3_id=""; webit3_pn=""; webit3_osr=""',
        '  for webit3_f in /etc/os-release /usr/lib/os-release; do',
        '    if [ -r "$webit3_f" ]; then',
        '      webit3_osr="$webit3_f"',
        # sed 取第一行就 q（不接 head：pipefail 下 head 先關會讓 sed 吃 SIGPIPE）
        "      webit3_id=\"$(sed -n '/^ID=/{s/^ID=//p;q;}' \"$webit3_f\" | tr -d '\"' | tr -d \"'\" | tr 'A-Z' 'a-z')\"",
        "      webit3_pn=\"$(sed -n '/^PRETTY_NAME=/{s/^PRETTY_NAME=//p;q;}' \"$webit3_f\" | tr -d '\"' | tr -d \"'\")\"",
        '      break',
        '    fi',
        '  done',
    ]
    if r["legacy_prefixes"]:
        deny = " && ".join(f"[ ! -e {d} ]" for d in r["legacy_deny_files"]) or "true"
        lines += [f'  if [ -z "$webit3_osr" ] && [ -r {r["legacy_file"]} ]; then',
                  f'    webit3_pn="$(head -n 1 {r["legacy_file"]})"',
                  f'    if {deny}; then',
                  '      case "$webit3_pn" in']
        for p in r["legacy_prefixes"]:
            lines.append(f"        '{p['prefix']}'*) webit3_id={p['id']} ;;")
        lines += ['      esac', '    fi', '  fi']
    allowed = "|".join(r["allowed_ids"])
    lines += [
        '  case "$webit3_id" in',
        f'    {allowed}) : ;;',
        f'    *) echo "{result_prefix}這台是 Linux，但不是允許的發行版（偵測到：${{webit3_pn:-${{webit3_id:-無 os-release}}}}）——未納管"{to}; '
        f'exit {PLATFORM_REFUSED_EXIT} ;;',
        '  esac',
        'fi']
    return "\n".join(lines)


def _strip_quotes(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def platform_refusal(probe: dict, rules: dict | None = None) -> str | None:
    """探測結果 → 拒絕訊息（不准納管）或 None（可以）。只管 Linux；AIX／Windows 回 None。

    fail-closed：沒有 os-release 又對不上舊版規則、ID 不在白名單、命中任何標記 → 拒絕。
    """
    if probe.get("os") != "linux":
        return None
    try:
        r = rules or load_platform_rules()
    except Exception as exc:  # noqa: BLE001 - 設定壞了＝不知道白名單，fail-closed
        return f"平台白名單設定讀不到（{exc}）——未納管"
    names = {m["id"]: m["name"] for m in r["markers"]}
    hits = probe.get("appliance") or []
    if hits:
        return f"偵測到設備標記：{'、'.join(names.get(h, h) for h in hits)}——未納管"
    osr = probe.get("os_release") or {}
    pid = (osr.get("ID") or "").lower()
    seen = osr.get("PRETTY_NAME") or (f"ID={pid}" if pid else "")
    if not osr:      # 連 os-release 檔都沒有（OSREL_FILE 也沒印）才走舊版 release 規則，跟腳本一致
        legacy = probe.get("legacy_release") or ""
        if legacy:
            seen = legacy
            if not probe.get("legacy_deny"):
                for p in r["legacy_prefixes"]:
                    if legacy.startswith(p["prefix"]):
                        pid = p["id"]
                        break
    if pid and pid in r["allowed_ids"]:
        return None
    return f"這台是 Linux，但不是允許的發行版（偵測到：{seen or '無 os-release'}）——未納管"


def build_linux_revoke_script(pubkey: str, account: str = DEFAULT_ACCOUNT) -> str:
    """即時組出 Linux **反納管**腳本：把收集帳號連同金鑰與 sudo 白名單收回來。

    ## 三步的順序是設計的一部分

        1. 移除 authorized_keys   <- 存取權**當場**斷掉
        2. 移除 sudoers 白名單
        3. userdel 刪帳號

    先斷金鑰：就算後面兩步失敗，該收回的權限已經收回，剩下的只是殘留檔案。
    反過來（先 userdel）失敗的話權限還在——那才是要命的失敗模式。

    ## 冪等

    帳號本來就不存在不算失敗（可能有人先手動清掉了）。每一步分別回報
    「移除了」與「本來就沒有」——這兩件事對人的意義完全不同。

    ## 只刪我們放進去的那一行

    納管有一個分支：帳號**已經存在**時，我們是把公鑰 append 到它既有的
    `authorized_keys`。所以撤銷不能 `rm` 整個檔案——那會把別人的金鑰一起刪掉，
    而且目標機上沒有任何備份機制，還原不回來。

    比對收集公鑰、只刪那一行；動之前先留一份時間戳備份。
    （使用者 2026-09-03 問「取消納管前有備份嗎」才發現第一版寫錯。）

    ## 不含任何機密

    公鑰是公開的，跟納管腳本一樣純文字、可稽核，貼給人看也沒關係。
    """
    import account_collector
    import onboard_hostfix as hf
    import root_key

    helper_path = account_collector.ACCOUNT_HELPER_PATH
    return f"""#!/usr/bin/env bash
set -uo pipefail
ACCOUNT="{account}"
PUBKEY='{pubkey}'
STAMP="$(date +%Y%m%d_%H%M%S)"
if [ "$(id -u)" -ne 0 ]; then echo "需要 root" >&2; exit 1; fi

# ROOTKEY1／ONBOARD-FLOW1：root 收集金鑰、sshd Match 段、AllowUsers、access.conf
# 都照標記只刪我們加的那一行／那一段，**最先做**（先斷 root 存取權）。
{root_key.revoke_sh()}{hf.revoke_access_sh()}
# ---- 1. 先斷金鑰。這一步做完，收集端就再也連不進來了 ----
echo "[*] 移除收集公鑰（存取權在這一步斷掉）"
if id "$ACCOUNT" >/dev/null 2>&1; then
  HOME_DIR="$(getent passwd "$ACCOUNT" | cut -d: -f6)"
  AUTH="$HOME_DIR/.ssh/authorized_keys"
  if [ -f "$AUTH" ]; then
    if grep -qF "$PUBKEY" "$AUTH"; then
      # 先備份再動。這台可能是「帳號本來就存在」那種，檔案裡有別人的金鑰。
      cp -p "$AUTH" "$AUTH.webit3-revoke-$STAMP"
      # 只刪含收集公鑰的那一行，其餘原樣保留
      grep -vF "$PUBKEY" "$AUTH" > "$AUTH.tmp" && mv "$AUTH.tmp" "$AUTH"
      chown "$ACCOUNT:$ACCOUNT" "$AUTH" 2>/dev/null || true
      chmod 600 "$AUTH"
      LEFT="$(grep -c . "$AUTH" 2>/dev/null || echo 0)"
      echo "[+] 已移除收集公鑰那一行（備份：$AUTH.webit3-revoke-$STAMP）"
      if [ "$LEFT" -gt 0 ]; then
        echo "[!] 這個檔案裡還有 $LEFT 把別的金鑰，**沒有動它們**——這台的帳號不是我們獨佔的"
      fi
    else
      echo "[=] authorized_keys 裡沒有我們的公鑰（可能已經撤過，或當初佈的是別把）"
    fi
  else
    echo "[=] 沒有 authorized_keys（本來就沒有，或已經被清過）"
  fi
else
  echo "[=] 帳號 $ACCOUNT 不存在，沒有金鑰可移除"
fi

# ---- 2. sudo 白名單 ----
echo "[*] 移除唯讀 sudo 白名單"
if [ -f "/etc/sudoers.d/$ACCOUNT" ]; then
  # 這個檔名是我們專屬的（納管時就叫這個名字），整檔移除是對的。
  # 還是先備份——撤錯了要能證明原本長什麼樣。
  cp -p "/etc/sudoers.d/$ACCOUNT" "/etc/sudoers.d/.$ACCOUNT.webit3-revoke-$STAMP"
  rm -f "/etc/sudoers.d/$ACCOUNT"
  echo "[+] 已移除 /etc/sudoers.d/$ACCOUNT（備份：/etc/sudoers.d/.$ACCOUNT.webit3-revoke-$STAMP）"
else
  echo "[=] 沒有 sudoers 白名單（這台可能本來就沒有 sudo）"
fi
# 帳號盤點輔助程式：sudoers 拿掉之後它已經沒有人能以 root 執行，這裡把檔案也收掉。
# 備份放 /var/backups/webit3（root 專屬 700），不留一份可執行的副本在 libexec 裡。
if [ -f "{helper_path}" ]; then
  install -d -m 700 /var/backups/webit3
  cp -p "{helper_path}" "/var/backups/webit3/account-facts.webit3-revoke-$STAMP"
  rm -f "{helper_path}"
  rmdir "$(dirname "{helper_path}")" 2>/dev/null || true
  echo "[+] 已移除帳號盤點輔助程式（備份：/var/backups/webit3/account-facts.webit3-revoke-$STAMP）"
fi

# ---- 3. 刪帳號 ----
# ---- 3. 刪帳號 ----
# ⚠️ 只有「這個帳號是我們建的」才刪。判準：家目錄裡除了 .ssh 幾乎什麼都沒有，
# 而且 GECOS 是我們寫的那串。判不準就不刪——留一個沒有金鑰的帳號是小事，
# 刪掉別人在用的帳號是大事。
echo "[*] 刪除帳號 $ACCOUNT"
if id "$ACCOUNT" >/dev/null 2>&1; then
  pkill -u "$ACCOUNT" 2>/dev/null || true      # 有 session 掛著時 userdel 會拒絕
  sleep 1
  if userdel -r "$ACCOUNT" 2>/dev/null; then
    echo "[+] 已刪除帳號與家目錄"
  elif userdel "$ACCOUNT" 2>/dev/null; then
    # -r 失敗多半是家目錄有東西刪不掉；帳號本身刪掉就已經沒有登入能力
    echo "[+] 已刪除帳號（家目錄保留，可人工清理）"
  else
    echo "[!] 帳號刪除失敗——但金鑰與 sudo 白名單已經移除，收集端已經連不進來"
  fi
else
  echo "[=] 帳號本來就不存在"
fi

echo "完成。$ACCOUNT 已從這台移除。"
"""


def build_aix_revoke_script(pubkey: str, account: str = DEFAULT_ACCOUNT_AIX) -> str:
    """AIX 反納管（ksh）：root 收集金鑰與 sshd Match 段（若有）→ 收集公鑰那一行 → rmuser。

    跟 Linux 同一個順序（先斷存取權）。⚠️ 未在真 AIX 上跑過（2026-10-18），先上測試機演練。"""
    import onboard_hostfix as hf
    import root_key

    return f"""#!/usr/bin/ksh
ACCOUNT="{account}"
PUBKEY='{pubkey}'
STAMP="$(date +%Y%m%d_%H%M%S)"
if [ "$(id -u)" -ne 0 ]; then echo "需要 root" >&2; exit 1; fi
{root_key.revoke_sh()}{hf.revoke_access_sh()}echo "[*] 移除收集公鑰"
if lsuser "$ACCOUNT" >/dev/null 2>&1; then
  HOME_DIR=$(lsuser -a home "$ACCOUNT" | awk -F'home=' '{{print $2}}')
  AUTH="$HOME_DIR/.ssh/authorized_keys"
  if [ -f "$AUTH" ] && grep -F "$PUBKEY" "$AUTH" >/dev/null 2>&1; then
    cp -p "$AUTH" "$AUTH.webit3-revoke-$STAMP"
    grep -vF "$PUBKEY" "$AUTH" > "$AUTH.webit3.tmp"
    cat "$AUTH.webit3.tmp" > "$AUTH"; rm -f "$AUTH.webit3.tmp"
    echo "[+] 已移除收集公鑰那一行（備份：$AUTH.webit3-revoke-$STAMP）"
  else
    echo "[=] authorized_keys 裡沒有我們的公鑰"
  fi
  echo "[*] 刪除帳號 $ACCOUNT"
  if rmuser -p "$ACCOUNT" 2>/dev/null; then
    echo "[+] 已刪除帳號（家目錄保留，可人工清理）"
  else
    echo "[!] 帳號刪除失敗——但金鑰已經移除，收集端已經連不進來"
  fi
else
  echo "[=] 帳號 $ACCOUNT 本來就不存在"
fi
echo "完成。$ACCOUNT 已從這台移除。"
"""


#: 備註欄允許的字元。刻意收窄——這個值會被放進 `useradd -c "…"`，
#: 含雙引號、反引號、`$` 或換行就能改變整行指令的語意（那是命令注入）。
#: 中日韓文字、英數、`-_.@()／空白` 夠用了。`:` 不在白名單（/etc/passwd 的欄位分隔字元，
#: 放進 GECOS 會把整行切歪），單引號也不在（腳本用 COMMENT='…' 單引號賦值）。
#: ⚠️ 這個值會出現在**每一台**被納管主機的 /etc/passwd（任何本機帳號都讀得到），
#: 不要放員編、姓名這類個資（使用者 2026-10-18 決定預設改中性「WEBIT3 collector」）。
# ⚠️ 不要用 `\\s`：它放行**換行**，而在 `useradd -c "…"` 裡多一個換行
# 就是多一行指令。只允許半形空白。（第一版寫 `\\s`，自我驗證當場抓到。）
_COMMENT_OK = re.compile(r"^[\w \-_.@()（）／/\u4e00-\u9fff]{1,120}$", re.UNICODE)


def sanitize_account_comment(comment: str | None) -> str:
    """把帳號備註洗成可以安全放進 `useradd -c` 的字串。

    空的就回預設值。**不合規不是靜默改掉而是丟 ValueError**——
    使用者填了什麼卻被系統偷偷換成別的，比直接說「這個字不能用」糟得多：
    他會以為設定生效了，直到有一天在主機上看到不是自己填的字串。
    """
    val = (comment or "").strip()
    if not val:
        return DEFAULT_ACCOUNT_COMMENT
    if not _COMMENT_OK.match(val):
        raise ValueError(
            f"帳號備註含不允許的字元：{val!r}。"
            f"這個值會被放進 useradd -c／usermod -c／chuser gecos=，只接受中英數與 - _ . @ ( ) ／ 空白，"
            f"最多 120 字（冒號會弄壞 /etc/passwd；引號、$、反引號、換行會改變指令語意，一律拒絕）。")
    return val


def _sudoers_escape(spec: str) -> str:
    """規則原文放進雙引號字串（bash 的 echo "…" 與 YAML 的 "…" 規則相同）：\\ 與 " 要跳脫。"""
    return spec.replace("\\", "\\\\").replace('"', '\\"')


def _rebuild_sudoers_sh() -> str:
    """OS 重建收集要佈的 sudoers 行（shell 版，接在 build_linux_script 的 { … } > $SUDOERS.new 區塊裡）。"""
    import os_rebuild_collector

    return "\n".join(f'    echo "$ACCOUNT ALL=(root) NOPASSWD: {_sudoers_escape(s)}"'
                     for s in os_rebuild_collector.rebuild_sudoers_specs())


def _rebuild_sudoers_yaml() -> str:
    """同一批規則的 Ansible 版：接在 sudoers copy 任務的 YAML 雙引號 content 後面，換行寫成 \\n。"""
    import os_rebuild_collector

    return "".join("{{ webit3_account }} ALL=(root) NOPASSWD: " + _sudoers_escape(s) + "\\n"
                   for s in os_rebuild_collector.rebuild_sudoers_specs())


def linux_sudo_specs() -> list[str]:
    """Linux 納管腳本佈給收集帳號的 sudoers 指令規格（sudoers 原文格式，參數逗號已跳脫）。

    **跟下面 build_linux_script 的 sudoers 區塊同一份內容**（test_root_key 逐條比對）。
    root 金鑰主機（沒有 sudo）收集時用這份當白名單：只送清單上的固定指令（決策 ROOTKEY1）。
    """
    import account_collector
    import os_rebuild_collector

    return [
        f'{account_collector.ACCOUNT_HELPER_PATH} ""',
        "/usr/sbin/smartctl -a /dev/*, /sbin/smartctl -a /dev/*, /usr/sbin/smartctl --scan, "
        "/usr/sbin/smartctl --scan-open, /sbin/smartctl --scan, /sbin/smartctl --scan-open",
        "/usr/sbin/multipath -ll, /sbin/multipath -ll",
        "/usr/sbin/storcli64 /call show, /opt/MegaRAID/storcli/storcli64 /call show, "
        "/usr/sbin/perccli64 /call show, /opt/MegaRAID/perccli/perccli64 /call show",
        "/usr/sbin/ssacli ctrl all show config, /opt/smartstorageadmin/ssacli/bin/ssacli ctrl all show config",
        "/usr/bin/tail -n 200 /var/log/secure",
        "/usr/bin/tail -n 200 /var/log/auth.log",
        "/usr/bin/dmesg --level=err\\,crit",
        "/usr/sbin/ss -tlnp, /usr/bin/ss -tlnp",
        *os_rebuild_collector.rebuild_sudoers_specs(),
    ]


#: 收集器的備援位址（例如 DR 那台）。主位址之外，from=／AllowUsers／access.conf／Match Address
#: 都會一起放行。存 app_settings（含真實 IP，不進版控），逗號分隔。
COLLECTOR_IPS_EXTRA_SETTING = "collector_ips_extra"


def resolve_extra_collector_ips(conn=None) -> list[str]:
    """備援收集器位址清單（不含主位址）。讀不到設定就是空的——不猜。"""
    import ipaddress

    raw = ""
    try:
        from db import get_setting

        if conn is None:
            import db

            c = db.get_connection()
            try:
                raw = get_setting(c, COLLECTOR_IPS_EXTRA_SETTING, "") or ""
            finally:
                c.close()
        else:
            raw = get_setting(conn, COLLECTOR_IPS_EXTRA_SETTING, "") or ""
    except Exception:  # noqa: BLE001 - 單元測試／離線工具沒有 DB
        raw = os.environ.get("ASSET_COLLECTOR_IPS_EXTRA", "")
    out = []
    for tok in re.split(r"[,\s]+", raw.strip()):
        if not tok:
            continue
        try:
            ipaddress.ip_address(tok)
        except ValueError:
            continue          # 只收 IP：from=／Match Address 吃不了主機名
        if tok not in out:
            out.append(tok)
    return out


def _collector_ips(collector_ip: str, extra_ips: list[str] | None) -> list[str]:
    ips = [collector_ip]
    for ip in extra_ips or []:
        if ip and ip not in ips:
            ips.append(ip)
    return ips


def _root_key_section(root_pubkey: str | None, ips: list[str]) -> str:
    """納管腳本裡 root 收集金鑰那一段（決策 ROOTKEY1）。條件不足時只印原因、不動任何東西。"""
    import root_key

    if not root_pubkey:
        return root_key.bootstrap_skip_sh("收集端還沒有 root 收集金鑰，這次不佈（需 root 的收集項目會是權限不足）")
    if not all(root_key.is_ip_literal(ip) for ip in ips):
        return root_key.bootstrap_skip_sh(
            "收集器位址不是 IP（Match Address 與 from= 只收 IP），這次不佈 root 金鑰")
    return root_key.bootstrap_sh(root_pubkey, ips)


def _script_version() -> str:
    try:
        import json

        return json.loads((Path(__file__).parent / "version.json").read_text(encoding="utf-8"))["version"]
    except Exception:  # noqa: BLE001
        return "unknown"


def build_linux_script(pubkey: str, collector_ip: str, account: str = DEFAULT_ACCOUNT,
                       comment: str | None = None, root_pubkey: str | None = None,
                       extra_ips: list[str] | None = None) -> str:
    """即時組出 Linux 納管腳本。內容只含公鑰，無任何機密。

    `comment` 是建帳號時寫進 /etc/passwd 的備註欄，讓在主機上看到這個帳號的人
    知道是誰佈的、為什麼。**不要在這裡寫死實際值**——見 DEFAULT_ACCOUNT_COMMENT。
    備註只允許安全字元（見 sanitize_account_comment），因為它會被放進
    `useradd -c "…"`，含引號或換行會改變指令語意。
    """
    import account_collector
    import os_rebuild_collector

    account = target_account(account)      # 舊名一律換新名（重新納管＝遷移）
    _c = sanitize_account_comment(comment)
    helper_path = account_collector.ACCOUNT_HELPER_PATH
    helper_script = account_collector.ACCOUNT_HELPER_SCRIPT
    rebuild_helper_path = os_rebuild_collector.REBUILD_HELPER_PATH
    rebuild_helper_script = os_rebuild_collector.REBUILD_HELPER_SCRIPT
    rebuild_sudoers_sh = _rebuild_sudoers_sh()
    import onboard_hostfix as hf

    ips = _collector_ips(collector_ip, extra_ips)
    root_section = _root_key_section(root_pubkey, ips)
    script = f"""#!/usr/bin/env bash
# webit3 納管腳本 v{_script_version()}（Linux 通用；內容固定、純文字，可先審閱）
set -euo pipefail
{ESXI_SCRIPT_GUARD_SH}
{platform_guard_sh()}
ACCOUNT="{account}"
COMMENT='{_c}'
COLLECTOR_IP="{collector_ip}"
COLLECTOR_IPS="{' '.join(ips)}"
COLLECTOR_FROM="{','.join(ips)}"
SCRIPT_VER="{_script_version()}"
PUBKEY='{pubkey}'
HELPER="{helper_path}"
{hf.body_root_check_sh()}{hf.log_sh()}
# ---- 0. 動任何東西之前先備份，並產生還原腳本 ----
# 使用者 2026-09-11：「記得移動前就要備份，要有還原的，以後才可以還原」。
# 還原腳本逐檔記錄：原本有的 → 複製回去；原本沒有的 → 刪掉。所以還原後就是納管前的樣子。
# 加上 PID：同一秒跑兩次（例如 root 跑完、換 sudo 帳號馬上再跑）不會共用同一個備份目錄、互相覆蓋 restore.sh
BK="/var/backups/webit3/$(date +%Y%m%d_%H%M%S)_$$"
install -d -m 700 "$BK"
R="$BK/restore.sh"
printf '%s\\n' '#!/usr/bin/env bash' 'set -eu' 'if [ "$(id -u)" -ne 0 ]; then echo "需要 root" >&2; exit 1; fi' > "$R"
backup_one() {{
  if [ -e "$1" ]; then
    cp -p "$1" "$BK/$2"
    echo "cp -p '$BK/$2' '$1'" >> "$R"
  else
    echo "rm -f '$1'" >> "$R"
  fi
}}
backup_one "/etc/sudoers.d/$ACCOUNT" sudoers.orig
backup_one "$HELPER" account-facts.orig
# 這支腳本可能改到的其他檔（ROOTKEY1／ONBOARD-FLOW1）：root 的 authorized_keys、sshd_config、
# access.conf——restore.sh 也要能把它們（含 root 收集金鑰那一行）一起還原（2026-10-18 審查）
RHOME0="$(awk -F: '$1=="root"{{print $6}}' /etc/passwd)"
[ -n "$RHOME0" ] || RHOME0=/root
backup_one "$RHOME0/.ssh/authorized_keys" root_authorized_keys.orig
backup_one /etc/ssh/sshd_config sshd_config.orig
backup_one /etc/security/access.conf access.conf.orig
if id "$ACCOUNT" >/dev/null 2>&1; then
  backup_one "$(getent passwd "$ACCOUNT" | cut -d: -f6)/.ssh/authorized_keys" authorized_keys.orig
else
  echo "echo '[!] 帳號 $ACCOUNT 是這次納管才建立的，還原不會刪帳號；要連帳號一起移除請用「取消納管」'" >> "$R"
fi
echo "systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || service ssh reload 2>/dev/null || service sshd reload 2>/dev/null || true" >> "$R"
echo "echo '還原完成（備份在 $BK）'" >> "$R"
chmod 700 "$R"
echo "[+] 已備份現況到 $BK（還原：sudo bash $R）"

if ! id "$ACCOUNT" >/dev/null 2>&1; then
  useradd -m -s /bin/bash -c "$COMMENT" "$ACCOUNT"
  passwd -l "$ACCOUNT" >/dev/null
  echo "[+] 已建立帳號 $ACCOUNT（備註：$COMMENT）"
else
  echo "[=] 帳號已存在"
  # 重新納管時把備註對齊設定值（設定改了，已納管的主機重跑一次就跟上；一樣就不動）
  OLD_GECOS="$(getent passwd "$ACCOUNT" | cut -d: -f5)"
  if [ "$OLD_GECOS" != "$COMMENT" ]; then
    usermod -c "$COMMENT" "$ACCOUNT"
    echo "[+] 帳號備註已更新：「$OLD_GECOS」→「$COMMENT」"
  else
    echo "[=] 帳號備註已是「$COMMENT」"
  fi
fi
{hf.aging_sh("linux")}
echo "[*] 佈署收集公鑰"
HOME_DIR="$(getent passwd "$ACCOUNT" | cut -d: -f6)"
install -d -m 700 -o "$ACCOUNT" -g "$ACCOUNT" "$HOME_DIR/.ssh"
AUTH="$HOME_DIR/.ssh/authorized_keys"
LINE="from=\\"$COLLECTOR_FROM\\",no-agent-forwarding,no-port-forwarding,no-X11-forwarding $PUBKEY"
# 重跑時（A7）收集器位址可能變了（加了 DR）：有我們那一行就換成新的，別的金鑰原樣保留
if grep -qxF "$LINE" "$AUTH" 2>/dev/null; then
  echo "[=] 收集公鑰已在 authorized_keys"
else
  touch "$AUTH"
  grep -vF "$PUBKEY" "$AUTH" > "$AUTH.webit3.tmp" || true
  echo "$LINE" >> "$AUTH.webit3.tmp"
  cat "$AUTH.webit3.tmp" > "$AUTH"; rm -f "$AUTH.webit3.tmp"
  echo "[+] authorized_keys 已佈收集公鑰（只限收集器 $COLLECTOR_FROM）"
fi
chown "$ACCOUNT:$ACCOUNT" "$AUTH"; chmod 600 "$AUTH"
# 唯讀輔助程式（帳號盤點＋OS 重建）：內容固定、root 擁有、只有 root 能改。
# 有 sudo 的主機由 sudoers 只准固定子指令執行它；沒有 sudo 的主機由 root 收集金鑰執行同一批子指令。
install_helpers() {{
  # 輔助程式：內容固定、root 擁有、只有 root 能改。sudo 只准「不帶參數」執行它。
  # 先寫 .new 再 mv：寫到一半中斷不會留下半支程式。目錄是 root 的 755，一般帳號寫不進來。
  install -d -m 755 -o root -g root "$(dirname "$HELPER")"
  cat > "$HELPER.new" <<'WEBIT3_HELPER_EOF'
{helper_script}WEBIT3_HELPER_EOF
  chown root:root "$HELPER.new"
  chmod 755 "$HELPER.new"
  mv -f "$HELPER.new" "$HELPER"
  # OS 重建唯讀輔助程式（Q78-SHRINK）：同樣 root 擁有、固定內容；sudoers 只放行固定子指令，
  # 它自己列舉固定目錄、拒符號連結與逃出目錄——取代原本「cat 萬用字元可讀任意檔」。
  REBUILD_HELPER="{rebuild_helper_path}"
  cat > "$REBUILD_HELPER.new" <<'WEBIT3_REBUILD_EOF'
{rebuild_helper_script}WEBIT3_REBUILD_EOF
  chown root:root "$REBUILD_HELPER.new"
  chmod 755 "$REBUILD_HELPER.new"
  mv -f "$REBUILD_HELPER.new" "$REBUILD_HELPER"
  # 驗證兩支輔助程式與其目錄都是 root 擁有、且 group/other 不可寫——
  # 不然能寫它的帳號等於能讓 sudo 以 root 跑它改過的內容。任一不符就中止，不佈 sudo 白名單。
  for _p in "$(dirname "$HELPER")" "$HELPER" "$REBUILD_HELPER"; do
    if [ "$(stat -c '%U' "$_p" 2>/dev/null)" != "root" ] \
       || find "$_p" -maxdepth 0 \\( -perm -020 -o -perm -002 \\) 2>/dev/null | grep -q .; then
      echo "[!] 輔助程式或其目錄不是 root 專屬／可被他人寫入（$_p），中止佈署 sudo 白名單" >&2
      exit 1
    fi
  done
}}
echo "[*] 設定唯讀 sudo 白名單（機型序號＋帳號盤點輔助程式）"
ROOTKEY_OK=0
RHOME=/root
if command -v sudo >/dev/null 2>&1 && [ -d /etc/sudoers.d ]; then
  install_helpers
  SUDOERS="/etc/sudoers.d/$ACCOUNT"
  # 先寫成帶「.」的暫存檔（sudo 會略過含「.」的檔名），驗證過才換上去——
  # 直接覆寫的話，寫壞的 sudoers 會在驗證前就生效。
  {{
    echo "$ACCOUNT ALL=(root) NOPASSWD: $HELPER \\"\\""
    # 機型序號／dmidecode／vgs／lvs／pvs 已於 Q78-SHRINK（2026-10-18）改成固定規則與唯讀輔助程式，
    # 不再用萬用字元——改由下方那段（唯一來源 os_rebuild_collector.REBUILD_SUDOERS）統一產出。
    # 第二批唯讀（2026-09-14 使用者核准）：磁碟健康(SMART -a)、多路徑、硬體 RAID 唯讀 show。皆不改設定。
    echo "$ACCOUNT ALL=(root) NOPASSWD: /usr/sbin/smartctl -a /dev/*, /sbin/smartctl -a /dev/*, /usr/sbin/smartctl --scan, /usr/sbin/smartctl --scan-open, /sbin/smartctl --scan, /sbin/smartctl --scan-open"
    echo "$ACCOUNT ALL=(root) NOPASSWD: /usr/sbin/multipath -ll, /sbin/multipath -ll"
    echo "$ACCOUNT ALL=(root) NOPASSWD: /usr/sbin/storcli64 /call show, /opt/MegaRAID/storcli/storcli64 /call show, /usr/sbin/perccli64 /call show, /opt/MegaRAID/perccli/perccli64 /call show"
    echo "$ACCOUNT ALL=(root) NOPASSWD: /usr/sbin/ssacli ctrl all show config, /opt/smartstorageadmin/ssacli/bin/ssacli ctrl all show config"
    # 值班健檢的兩項唯讀日誌（2026-09-22 使用者核准：「要 root，就 sudo 給權限」）。
    # **參數寫死**：tail 綁 -n 200 與固定路徑、dmesg 綁固定 level。
    # 不可以放寬成 /usr/bin/tail 或 /usr/bin/cat——那等於能讀系統上任何檔案。
    echo "$ACCOUNT ALL=(root) NOPASSWD: /usr/bin/tail -n 200 /var/log/secure"
    echo "$ACCOUNT ALL=(root) NOPASSWD: /usr/bin/tail -n 200 /var/log/auth.log"
    echo "$ACCOUNT ALL=(root) NOPASSWD: /usr/bin/dmesg --level=err\\,crit"
    # 監聽 port 的行程名（ss -tlnp 的 -p 需 root）。參數寫死 -tlnp、路徑兩種都列。
    echo "$ACCOUNT ALL=(root) NOPASSWD: /usr/sbin/ss -tlnp, /usr/bin/ss -tlnp"
    # 機型序號／儲存藍圖／排程／網路設定／授權金鑰指紋（Q78-SHRINK，2026-10-18）：唯一來源
    # os_rebuild_collector.REBUILD_SUDOERS，全部固定規則、無萬用字元（唯讀輔助程式＋固定參數）。
{rebuild_sudoers_sh}
  }} > "$SUDOERS.new"
  chmod 440 "$SUDOERS.new"
  if visudo -cf "$SUDOERS.new" >/dev/null; then
    # B5：白名單有變才換、並寫進主機紀錄檔（2026-10-18 演練發現：換了白名單但紀錄檔沒有這一筆）
    if [ -f "$SUDOERS" ] && cmp -s "$SUDOERS.new" "$SUDOERS"; then
      rm -f "$SUDOERS.new"
      echo "[=] sudo 唯讀白名單未變（$SUDOERS）"
    else
      mv -f "$SUDOERS.new" "$SUDOERS"
      echo "[+] sudo 唯讀白名單已更新（$SUDOERS，visudo 驗證通過）"
    fi
  else
    rm -f "$SUDOERS.new"
    bash "$R"
    # 印到 stdout（不是只有 stderr）：上層靠這個標記分辨「白名單沒佈成功」，
    # 不然它跟「還沒納管」在畫面上長得一模一樣。
    echo "[!] {SUDOERS_INVALID_MARK} sudoers 語法被 visudo 擋下，白名單沒佈上去"
    visudo -cf "$SUDOERS.new" 2>&1 | head -5 || true
    echo "sudoers 驗證失敗，已還原成納管前的樣子" >&2
    exit 1
  fi
else
  echo "[!] {NO_SUDO_MARK} 這台沒有 sudo（或沒有 /etc/sudoers.d），跳過唯讀白名單"
  echo "[!] 後果：硬體序號收不到（product_serial 是 0400，只有 root 讀得到）"
  echo "[!]       帳號盤點需 root 的欄位（密碼效期、sudo 明細、金鑰數）也收不到"
  echo "[!]       機型、OS、帳號清單、服務這些照常收得到（product_name 是 0444）"
  echo "[!] 要補：裝 sudo 之後再跑一次納管，這支腳本可以重複執行"
  echo "[!]   （或由下面的 root 收集金鑰補上——決策 ROOTKEY1，暫時，待收斂）"
  install_helpers
{root_section}fi
{hf.access_fix_sh()}{hf.selinux_sh()}{legacy_migration_sh()}
echo "完成。本機部分：帳號已建立、公鑰已佈署。"
echo "⚠ 遠端連線尚未驗證——以上都是在這台機器上跑的。"
echo "   請回系統畫面按「驗證」，由 $COLLECTOR_IP 實際連一次才算數。"
echo "要還原成納管前的樣子：sudo bash $R"
"""
    return hf.with_log(script)


# ⚠️ 三個平台的腳本**最後一定要有一行以「完成。」開頭**——
# 上層靠 `"完成。" not in out` 判定失敗（本檔 1398），進度解析也靠
# `line.startswith("完成。")`（本檔 134）。2026-09-22 差點踩到：
# 為了讓收尾訊息誠實而改寫那行，結果把成功標記一起拿掉，
# **所有納管都會被回報成失敗**。誠實跟標記可以並存：標記照留，後面補實話。
def build_aix_script(pubkey: str, collector_ip: str,
                     account: str = DEFAULT_ACCOUNT_AIX, root_pubkey: str | None = None,
                     extra_ips: list[str] | None = None, comment: str | None = None) -> str:
    """即時組出 AIX 納管腳本（ksh）。內容只含公鑰，無任何機密。

    ⚠️ 不是「Linux 腳本換個名字」——照 Linux 那份跑在 AIX 上一定失敗：

    - 建帳號是 `mkuser` 不是 `useradd`。
    - ⚠️ **「鎖密碼」在 AIX 上沒有對應的單一指令，而且不可以用 `account_locked=true`。**
      2026-09-22 踩到（8 台 AIX 全部卡住）：這份 docstring 原本就寫著
      「鎖密碼是 `chuser account_locked=true` 不是 `passwd -l`」——**錯的心智模型
      寫進文件，腳本就照著寫，然後沒人再回頭質疑**。
        · Linux `passwd -l` / `usermod -L` → 只鎖**密碼**，金鑰登入照常
        · AIX `account_locked=true`        → 鎖**整個帳號**，sshd 連公鑰也拒絕
      症狀是 `Permission denied (publickey,password)`：公鑰佈好了、權限也對，
      但帳號鎖著，怎麼試都進不來。
      AIX 要「只能金鑰、不能密碼」的正確做法是**帳號開著但不給密碼**——
      `mkuser` 建出來預設就沒有密碼（`/etc/security/passwd` 的 password 是 `*`），
      本來就登不了密碼；所以只要 `chuser account_locked=false rlogin=true`。
    - **不佈 sudoers**。Linux 版要 sudo 是為了讀 `product_serial`（0400 只有 root
      讀得到；同目錄的 `product_name` 是 0444，不需要 sudo）；AIX 根本沒有 dmi，
      序號機型走 `uname -M`／`uname -u`，一般帳號就讀得到。而且 AIX 未必裝 sudo（常在 /opt/freeware/bin 或改用 RBAC），
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
    # AIX 不佈 sudoers（見上），需要 root 的收集項目（組態檢核 30 多條、OS 重建）只能靠
    # root 收集金鑰（決策 ROOTKEY1，2026-10-18 擴大到 AIX）。sshd 不支援 Match／sshd -T 時
    # 該段自己會停下、照實印原因，不會放寬全域 PermitRootLogin。
    import onboard_hostfix as hf

    _c = sanitize_account_comment(comment)
    ips = _collector_ips(collector_ip, extra_ips)
    root_section = _root_key_section(root_pubkey, ips)
    script = f"""#!/usr/bin/ksh
# webit3 納管腳本 v{_script_version()}（AIX 通用；內容固定、純文字，可先審閱）
set -e
{ESXI_SCRIPT_GUARD_SH}
{platform_guard_sh()}
ACCOUNT="{account}"
COMMENT='{_c}'
COLLECTOR_IP="{collector_ip}"
COLLECTOR_IPS="{' '.join(ips)}"
COLLECTOR_FROM="{','.join(ips)}"
SCRIPT_VER="{_script_version()}"
PUBKEY='{pubkey}'
{hf.body_root_check_sh()}{hf.log_sh()}# 先確認這台的帳號名長度上限，不要等 mkuser 吐一句看不懂的錯才發現
MAXLOG=$(lsattr -El sys0 -a max_logname 2>/dev/null | awk '{{print $2}}')
if [ -n "$MAXLOG" ] && [ "$MAXLOG" -le "${{#ACCOUNT}}" ]; then
  echo "此主機 max_logname=$MAXLOG，容不下 $ACCOUNT（${{#ACCOUNT}} 字元）。" >&2
  echo "請改用較短的收集帳號名，或 chdev -l sys0 -a max_logname=32 後重開機。" >&2
  exit 1
fi
if ! lsuser "$ACCOUNT" >/dev/null 2>&1; then
  mkuser shell=/usr/bin/ksh gecos="$COMMENT" "$ACCOUNT"
  echo "[+] 已建立帳號 $ACCOUNT（備註：$COMMENT）"
else
  echo "[=] 帳號已存在"
  # 重新納管時把備註對齊設定值；一樣就不動（lsuser -c 輸出 #name:gecos / acct:值）
  OLD_GECOS="$(lsuser -c -a gecos "$ACCOUNT" 2>/dev/null | sed -n '2p' | cut -d: -f2-)"
  if [ "$OLD_GECOS" != "$COMMENT" ]; then
    chuser gecos="$COMMENT" "$ACCOUNT"
    echo "[+] 帳號備註已更新：「$OLD_GECOS」→「$COMMENT」"
  else
    echo "[=] 帳號備註已是「$COMMENT」"
  fi
fi
# ⚠️ 2026-09-22 修正（8 台 AIX 全部卡在這裡）：
# 這裡原本是 `chuser account_locked=true`，註解寫「只能用金鑰登入，沒有可用密碼」。
# **那是把 Linux 的慣用法套到 AIX，兩邊語意完全不同：**
#   Linux  usermod -L / passwd -l → 只鎖**密碼**，金鑰登入照常
#   AIX    account_locked=true    → 鎖**整個帳號**，sshd 連公鑰也一律拒絕
# 症狀就是 `Permission denied (publickey,password)`——公鑰佈好了、權限也對，
# 但帳號是鎖的，怎麼試都進不來。
#
# AIX 上要達到「只能用金鑰、不能用密碼」，正確做法是**帳號開著但不給密碼**：
# mkuser 建出來的帳號預設沒有密碼（/etc/security/passwd 的 password 是 `*`），
# 本來就登不了密碼。所以只要確保帳號沒被鎖、允許遠端登入即可。
#
# 已存在的帳號也要做——使用者那台就是「帳號已存在」但鎖著（前一版腳本鎖的）。
echo "[*] 確認帳號可登入（AIX 的 account_locked 會連金鑰一起擋）"
chuser account_locked=false rlogin=true "$ACCOUNT"
{hf.aging_sh("aix")}echo "[*] 佈署收集公鑰"
HOME_DIR=$(lsuser -a home "$ACCOUNT" | awk -F'home=' '{{print $2}}')
if [ -z "$HOME_DIR" ]; then HOME_DIR="/home/$ACCOUNT"; fi
mkdir -p "$HOME_DIR/.ssh"
AUTH="$HOME_DIR/.ssh/authorized_keys"
LINE="from=\\"$COLLECTOR_FROM\\",no-agent-forwarding,no-port-forwarding,no-X11-forwarding $PUBKEY"
if grep -xF "$LINE" "$AUTH" >/dev/null 2>&1; then
  echo "[=] 收集公鑰已在 authorized_keys"
else
  touch "$AUTH"
  grep -vF "$PUBKEY" "$AUTH" > "$AUTH.webit3.tmp" || true
  echo "$LINE" >> "$AUTH.webit3.tmp"
  cat "$AUTH.webit3.tmp" > "$AUTH"; rm -f "$AUTH.webit3.tmp"
  echo "[+] authorized_keys 已佈收集公鑰（只限收集器 $COLLECTOR_FROM）"
fi
chown -R "$ACCOUNT" "$HOME_DIR/.ssh"
chmod 700 "$HOME_DIR/.ssh"; chmod 600 "$AUTH"
ROOTKEY_OK=0
RHOME=""
{root_section}{hf.aix_root_rlogin_sh()}{hf.access_fix_sh()}# 驗證分兩段，而且要講清楚哪一段證明了什麼。
# ⚠️ 2026-09-22：原本只有下面「本機欄位」那段，最後卻印一句
# 「完成，收集器已可收集這台」——**那句話沒有根據**。
# 這些指令是在**這台機器上本機執行**的，證明的是「欄位讀得到」，
# 完全沒有驗證「收集器從遠端連得進來」，而後者才是納管的目的。
# 使用者就是被這句話誤導：腳本說完成，實際上帳號鎖著、遠端一直進不來。
echo ""
echo "--- 驗證 1／2：帳號狀態（這段直接關係到遠端連不連得進來）---"
lsuser -a account_locked rlogin login "$ACCOUNT" 2>/dev/null
echo ""
echo "--- 驗證 2／2：收集會用到的欄位（本機執行，不需 root）---"
echo "  oslevel : $(oslevel -s 2>/dev/null)"
echo "  model   : $(uname -M 2>/dev/null)"
echo "  serial  : $(uname -u 2>/dev/null)"
echo ""
echo "完成。本機部分：帳號已建立、公鑰已佈署、欄位讀得到。"
echo "⚠ 但**遠端連線尚未驗證**——上面兩段都是在這台機器上跑的。"
echo "   請回系統畫面按「驗證」，由 $COLLECTOR_IP 實際連一次才算數。"
"""
    return hf.with_log(script)


def _indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + ln for ln in text.splitlines())


def build_linux_playbook(pubkey: str, collector_ip: str,
                         account: str = DEFAULT_ACCOUNT, comment: str | None = None) -> str:
    """把 Linux 納管腳本翻成 Ansible playbook，交給資安／維運一次佈完整批主機。

    ⚠️ **為什麼要從這顆引擎產、而不是另外維護一份 yml 檔**：公鑰必須永遠跟收集端的
    私鑰同步。手寫一份 yml 放在 repo 裡，換過金鑰之後那份就是錯的，而且錯得很安靜
    ——佈下去每台都成功，只是收集全部連不進來。這裡即時組出來，不會有漂移。

    內容與 build_linux_script 等價（同一組動作：建帳號、鎖密碼、佈 authorized_keys
    帶 from= 限來源、只給讀 dmi 的 sudo 白名單），差別只在交付形式。
    這三道鎖是決策 C1 定案專用帳號的前提，少一道就不成立。
    """
    validate_collector_ip(collector_ip)   # playbook 不經過 build_script，這裡自己擋
    account = target_account(account)
    # 備註欄跟腳本用同一個設定值：原本寫死英文，對已存在的帳號會把管理者填的備註蓋掉。
    # 放進 YAML 雙引號字串，所以雙引號與反斜線要再擋一次（sanitize 本來就不收這兩個）。
    _c = sanitize_account_comment(comment).replace("\\", "").replace('"', "")
    import account_collector
    import os_rebuild_collector

    helper_path = account_collector.ACCOUNT_HELPER_PATH
    helper_dir = helper_path.rsplit("/", 1)[0]
    rebuild_helper_path = os_rebuild_collector.REBUILD_HELPER_PATH
    rebuild_sudoers_yaml = _rebuild_sudoers_yaml()
    # 舊收集帳號換新：跟 shell 腳本同一段（legacy_migration_sh），前面補上它要的變數
    legacy_vars = ('ACCOUNT="{{ webit3_account }}"', 'PUBKEY="{{ webit3_pubkey }}"',
                   f'HELPER="{helper_path}"', 'COLLECTOR_IPS="{{ webit3_collector_ip }}"', "set -u")
    legacy_indented = "\n".join(("          " + ln) if ln else "" for ln in (
        [*legacy_vars, *legacy_migration_sh().splitlines()]))
    # YAML 區塊字面值：每行縮排 10 格（content: | 底下）；空行保持空行
    helper_indented = "\n".join(
        ("          " + ln) if ln else "" for ln in account_collector.ACCOUNT_HELPER_SCRIPT.splitlines())
    rebuild_helper_indented = "\n".join(
        ("          " + ln) if ln else "" for ln in os_rebuild_collector.REBUILD_HELPER_SCRIPT.splitlines())
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
    - name: 天條——ESXi 不可納管（在任何變更之前）
      ansible.builtin.raw: '[ "$(uname -s 2>/dev/null)" != "VMkernel" ] || {{ echo "ESXi 不可納管" >&2; exit 3; }}'
      changed_when: false

    - name: 天條——VMware 設備（vCenter／VCSA）不可納管（在任何變更之前）
      ansible.builtin.raw: '! {{ {VCSA_MARKER_TEST}; }} || {{ echo "VMware 設備（vCenter）不可納管" >&2; exit 3; }}'
      changed_when: false

    - name: 平台白名單——只納管允許的 Linux 發行版、不納管 Linux 底的設備（在任何變更之前）
      ansible.builtin.raw: |
{_indent(platform_guard_sh(), 8)}
      changed_when: false

    - name: 建立收集帳號（無密碼，僅金鑰登入）
      ansible.builtin.user:
        name: "{{{{ webit3_account }}}}"
        shell: /bin/bash
        comment: "{_c}"
        create_home: true
        password_lock: true
        state: present

    - name: 佈署收集公鑰（限制來源 IP，關閉所有轉送）
      ansible.posix.authorized_key:
        user: "{{{{ webit3_account }}}}"
        key: "{{{{ webit3_pubkey }}}}"
        # ⚠️ 一定要寫在同一行。原本用 `>-` 折兩行，折疊會在逗號後面塞一個空白：
        #   from="…",no-agent-forwarding, no-port-forwarding,…
        # sshd 讀選項讀到空白就停，後半段變成無效的一行——對「已經有這把公鑰」的主機，
        # Ansible 會把那一行改寫成壞的，收集帳號從此登不進去。
        # 2026-09-11 在 221 用 --check --diff 抓到（整批跑一千台就是一千台失聯）。
        key_options: 'from="{{{{ webit3_collector_ip }}}}",no-agent-forwarding,no-port-forwarding,no-X11-forwarding'
        exclusive: false
        state: present

    # 沒裝 sudo 的機器（Debian 最小安裝）連 /etc/sudoers.d 目錄都沒有，寫下去必失敗。
    # ⚠️ 注意：play 層的 `become: true` 預設也是走 sudo，所以完全沒有 sudo 的機器
    # 這份 playbook 本來就打不進去——要在 inventory 指定 `ansible_become_method: su`
    # 或直接以 root 連線。這個 when 擋的是「有 sudo 但沒有 sudoers.d」那種。
    - name: 這台有沒有 sudoers.d
      ansible.builtin.stat:
        path: /etc/sudoers.d
      register: webit3_sudoers_dir

    - name: 帳號盤點輔助程式的目錄（root 專屬，一般帳號寫不進來）
      when: webit3_sudoers_dir.stat.isdir | default(false)
      ansible.builtin.file:
        path: "{helper_dir}"
        state: directory
        owner: root
        group: root
        mode: "0755"

    # 內容固定、只有 root 能改、不接受任何參數（見 account_collector.ACCOUNT_HELPER_SCRIPT）。
    # backup: true ＝覆寫前留一份時間戳備份，要還原就把那份複製回去。
    - name: 帳號盤點唯讀輔助程式
      when: webit3_sudoers_dir.stat.isdir | default(false)
      ansible.builtin.copy:
        dest: "{helper_path}"
        owner: root
        group: root
        mode: "0755"
        backup: true
        content: |
{helper_indented}

    # OS 重建唯讀輔助程式（Q78-SHRINK）：root 擁有、固定內容；sudoers 只放行固定子指令，
    # 它自己列舉固定目錄、拒符號連結與逃出目錄——取代原本「cat 萬用字元可讀任意檔」。
    - name: OS 重建唯讀輔助程式
      when: webit3_sudoers_dir.stat.isdir | default(false)
      ansible.builtin.copy:
        dest: "{rebuild_helper_path}"
        owner: root
        group: root
        mode: "0755"
        backup: true
        content: |
{rebuild_helper_indented}

    - name: 唯讀 sudo 白名單（固定規則＋唯讀輔助程式，無萬用字元；不含 /etc/shadow）
      when: webit3_sudoers_dir.stat.isdir | default(false)
      ansible.builtin.copy:
        dest: "/etc/sudoers.d/{{{{ webit3_account }}}}"
        content: "{{{{ webit3_account }}}} ALL=(root) NOPASSWD: {helper_path} \\"\\"\\n{{{{ webit3_account }}}} ALL=(root) NOPASSWD: /usr/sbin/smartctl -a /dev/*, /sbin/smartctl -a /dev/*, /usr/sbin/smartctl --scan, /usr/sbin/smartctl --scan-open, /sbin/smartctl --scan, /sbin/smartctl --scan-open\\n{{{{ webit3_account }}}} ALL=(root) NOPASSWD: /usr/sbin/multipath -ll, /sbin/multipath -ll\\n{{{{ webit3_account }}}} ALL=(root) NOPASSWD: /usr/sbin/storcli64 /call show, /opt/MegaRAID/storcli/storcli64 /call show, /usr/sbin/perccli64 /call show, /opt/MegaRAID/perccli/perccli64 /call show\\n{{{{ webit3_account }}}} ALL=(root) NOPASSWD: /usr/sbin/ssacli ctrl all show config, /opt/smartstorageadmin/ssacli/bin/ssacli ctrl all show config\\n{{{{ webit3_account }}}} ALL=(root) NOPASSWD: /usr/bin/tail -n 200 /var/log/secure\\n{{{{ webit3_account }}}} ALL=(root) NOPASSWD: /usr/bin/tail -n 200 /var/log/auth.log\\n{{{{ webit3_account }}}} ALL=(root) NOPASSWD: /usr/bin/dmesg --level=err\\\\,crit\\n{{{{ webit3_account }}}} ALL=(root) NOPASSWD: /usr/sbin/ss -tlnp, /usr/bin/ss -tlnp\\n{rebuild_sudoers_yaml}"
        mode: "0440"
        backup: true
        validate: "visudo -cf %s"

    - name: 驗證收集帳號可用
      ansible.builtin.command: id -un
      become: true
      become_user: "{{{{ webit3_account }}}}"
      changed_when: false

    # 舊收集帳號（webit3scan）換新：新帳號本機驗過才移除舊的，驗不過舊的保留（2026-10-18）
    - name: 舊收集帳號換新（驗過才移除舊帳號；備份在 /var/backups/webit3）
      ansible.builtin.shell:
        executable: /bin/bash
        cmd: |
{legacy_indented}
      register: webit3_legacy
      changed_when: "'{LEGACY_REMOVED_MARK}' in webit3_legacy.stdout"
"""


def build_windows_script(pubkey: str, collector_ip: str, account: str = DEFAULT_ACCOUNT) -> str:
    """即時組出 Windows 納管腳本（PowerShell）。⚠️ authorized_keys 一律用 ASCII 無 BOM
    寫（PowerShell 5.1 的 -Encoding utf8 會塞 BOM，sshd 讀了會壞——實際踩過）。"""
    return f"""$ErrorActionPreference='Stop'
{ESXI_SCRIPT_GUARD_PS}
$Account='{account}'; $CollectorIP='{collector_ip}'
$PubKey='{pubkey}'
if (-not (Get-LocalUser -Name $Account -ErrorAction SilentlyContinue)) {{
  # 隨機密碼（沒人會用，登入走金鑰）。不用 System.Web 的 GeneratePassword：那要先載入組件，
  # 載入組件的寫法是 SOC 規則常見告警樣式（2026-10-20）。
  $rb=New-Object byte[] 24; ([Security.Cryptography.RandomNumberGenerator]::Create()).GetBytes($rb)
  $pw='Aa1!'+(-join ($rb | ForEach-Object {{ [char](33 + ($_ % 94)) }}))
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
Write-Host "完成。本機部分：帳號已建立、憑證已佈署。"
Write-Host "[!] 遠端連線尚未驗證——以上都是在這台機器上跑的。"
Write-Host "    請回系統畫面按「驗證」，由 $CollectorIP 實際連一次才算數。"
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


#: 帳號備註存在 app_settings 的哪個 key（6 系統設定可改；預設中性，不進版控）。
ACCOUNT_COMMENT_SETTING = "onboard_account_comment"


def resolve_account_comment(conn) -> str:
    """讀管理者設定的帳號備註；沒設就用中性預設值。

    跟 `resolve_collector_ip()` 同一個模式：程式碼只留預設，實際值在 DB。
    """
    from db import get_setting

    return sanitize_account_comment(
        get_setting(conn, ACCOUNT_COMMENT_SETTING, None))


def build_script(platform: str, pubkey: str, collector_ip: str,
                 account: str | None = None, comment: str | None = None,
                 root_pubkey: str | None = None, extra_ips: list[str] | None = None) -> str:
    # 在這裡擋而不是在各呼叫端：遠端納管、本機一行指令、排程自動納管、Ansible playbook
    # 全部經過這裡，擋一次就四條路都守到。
    validate_collector_ip(collector_ip)
    account = target_account(account, platform)
    if platform == "windows":
        # Windows 永遠不佈 root 金鑰（決策 ROOTKEY1 範圍外）
        return build_windows_script(pubkey, collector_ip, account)
    if platform in ("linux", "aix"):
        import root_key

        if root_pubkey is None:
            root_pubkey = root_key.read_pubkey()     # 只讀不產；產金鑰在 collector_pubkey()
        if extra_ips is None:
            extra_ips = resolve_extra_collector_ips()
    if platform == "linux":
        return build_linux_script(pubkey, collector_ip, account, comment, root_pubkey, extra_ips)
    if platform == "aix":
        return build_aix_script(pubkey, collector_ip, account, root_pubkey, extra_ips, comment)
    raise ValueError(f"未支援的平台：{platform}（只支援 linux／aix／windows）")


# ===== 執行器：真正碰網路的部分，抽成可注入 =====

@dataclass
class OnboardResult:
    ok: bool
    stage: str          # connect / execute / verify
    message: str
    output: str = ""
    #: 納管腳本回報「root 收集金鑰已佈」（決策 ROOTKEY1）。是否真的能登，呼叫端再用 root 金鑰試連確認。
    root_key: bool = False


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

    回 {"os": "linux"/"aix"/"windows"/"esxi"/"", "uid": int|None, "has_sudo": bool}。
    "esxi" 代表這台不可納管（天條，見 ESXI_REFUSED_MESSAGE）。
    runner 可注入，測試不碰真網路、不碰真密碼。
    """
    if runner is not None:
        raw = runner(host, username, password)
    else:
        rc, raw = _ssh_exec(host, username, password, PROBE_COMMAND, timeout=timeout)
        if rc == SSH_CONNECT_FAILED:
            # 根本沒進去（連不到／密碼錯／主機金鑰不符）：回人看得懂的原因，
            # 批次探測會把它當成「這組密碼不行」並顯示原因。
            return {"os": "", "uid": None, "has_sudo": False,
                    "error": classify_failure(raw)[1]}
    return parse_probe(raw)


def batch_probe_credentials(targets: list[dict], username: str, passwords: list[str],
                            timeout: int = 12, workers: int = 8, runner=None) -> list[dict]:
    """對一批主機依序試同一個帳號的多組候選密碼，只回報「哪一組通」——

    2026-09-06 使用者原話：「機隊裡 root 密碼混了 A/B 兩種，我不想一台一台試」。
    這支只做「連得進去嗎」的唯讀探測（借 probe_target 那行 `uname -s` 就好），
    **不執行任何納管腳本、不建帳號**，跟 onboard() 是完全不同量級的動作。

    ⚠️ 憑證不落地：passwords 只在這次呼叫裡活著，回傳值只有「第幾組密碼對
    （index）」，不含密碼本身——跟 onboard()/revoke() 同一條底線。

    ⚠️ 密碼依序試，**第一組通了就停**，不會把每組都打過一輪。理由不是效能，
    是安全：短時間對很多台主機連續嘗試同一帳號的多組密碼，形狀就是密碼
    噴灑攻擊，會撞到 fail2ban／PAM tally 之類的鎖定機制，也是資安會關注的
    行為模式——即使密碼真的是自己人的。呼叫端（UI）要把這條風險講給
    使用者看，不能藏起來當作理所當然。

    targets: [{"ip": "..."}, ...]（platform 由探測結果自己回報，不用先給）
    回傳每台：{"ip", "matched_password_index": int|None, "platform", "uid",
    "has_sudo", "error"}——matched_password_index 是 0-based，None 代表
    給的密碼沒有一組能登入。
    """
    from concurrent.futures import ThreadPoolExecutor

    def work(target: dict) -> dict:
        ip = target["ip"]
        last_err = None
        for idx, pw in enumerate(passwords):
            probe = probe_target(ip, username, pw, timeout=timeout, runner=runner)
            if probe.get("error"):
                last_err = probe["error"]
                continue
            if probe.get("os"):
                uid = probe.get("uid")
                return {"ip": ip, "matched_password_index": idx, "platform": probe["os"],
                        "uid": uid, "has_sudo": probe.get("has_sudo"),
                        # 使用者 2026-10-18：沒 sudo 的要提醒「用 root 納管」（AIX 一律要 root）
                        "needs_root": bool(uid not in (0, None)
                                           and (probe["os"] == "aix" or not probe.get("has_sudo"))),
                        # 平台白名單：登得進去，但這台不准納管（None＝可以）
                        "refusal": probe.get("refusal"),
                        "error": None}
            # 登入成功、指令也跑了，但認不出平台——例如登入後被強制進選單程式、
            # 或 shell 不是 POSIX（密碼錯的情況在上面 error 那條就處理掉了）。
            last_err = "登入失敗或連線異常（帳密可能不對，或該台不支援這個探測方式）"
        return {"ip": ip, "matched_password_index": None, "platform": None,
                "uid": None, "has_sudo": None, "error": last_err or "沒有提供候選密碼"}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(work, targets))


def parse_probe(raw: str) -> dict:
    """把探測輸出解析成平台/身分。純函式，好測。"""
    all_lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    lines = [ln for ln in all_lines if not ln.startswith(_PROBE_EXTRA_PREFIXES)]
    os_release, appliance, legacy, legacy_deny = {}, [], "", []
    for ln in all_lines:
        if ln.startswith("OSREL_") and "=" in ln:
            k, v = ln[len("OSREL_"):].split("=", 1)
            os_release.setdefault(k, _strip_quotes(v))
        elif ln.startswith("LEGACYREL_") and not legacy:
            legacy = ln[len("LEGACYREL_"):].strip()
        elif ln.startswith("LEGACYDENY="):
            legacy_deny.append(ln.split("=", 1)[1])
        elif ln.startswith("APPLIANCE_MARKER="):
            appliance.append(ln.split("=", 1)[1])
    text = " ".join(lines).lower()
    plat = ""
    if is_esxi_output(raw):
        # 放最前面：ESXi 的 `vmware -v` 輸出不含 linux／aix，但寧可先判掉不靠順序巧合
        plat = "esxi"
    elif "VMWARE_APPLIANCE" in (raw or ""):
        # vCenter（VCSA）：uname 是 Linux，必須排在 linux 之前判掉
        plat = "vcsa"
    elif "aix" in text:
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
    out = {"os": plat, "uid": uid, "has_sudo": "HASSUDO" in (raw or ""),
           "os_release": os_release, "appliance": appliance,
           "legacy_release": legacy, "legacy_deny": legacy_deny}
    # 平台白名單：只有 Linux 才判；None＝可以納管
    out["refusal"] = platform_refusal(out)
    return out


# ===== SSH 傳輸層：程式內 SSH 用戶端（paramiko） =====
#
# 2026-09-11 改（使用者要求）：原本用 sshpass 帶密碼呼叫外部 ssh。sshpass 在多數金融環境
# 列為禁用／告警工具——納管要對上千台跑，每跑一次就是一筆 SOC 告警，而觸發的帳號
# 持有全機隊金鑰。換成程式內 SSH 用戶端之後：
#   · 收集器上不再出現任何「密碼代填工具」行程，EDR 沒有東西可以對上
#   · 密碼不經環境變數、不進 argv、不落磁碟——只在這個行程的記憶體裡，直接交給 SSH 協定
#   · 主機金鑰策略跟 SSH_HOSTKEY_OPTS 等價（見 _AcceptNewPolicy）
# paramiko 延後到用的時候才 import：少裝它只會讓「納管」這一個功能失敗並講清楚，
# 不會拖垮整個 API 起不來。

#: 目標機的 SSH 埠。只給測試改（本機起一個假 SSH 伺服器用非 22 埠）；正式一律 22。
SSH_PORT = 22

#: 連線／執行的總上限 = timeout + 這個寬限秒數（跟原本串流看門狗的 +30 一致）。
_STREAM_GRACE = 30

#: 寫 known_hosts 的鎖：批次探測是多執行緒並行，兩條執行緒同時首見同一台時不能各寫各的。
_KNOWN_HOSTS_LOCK = threading.Lock()

#: 探測用的遠端指令（唯讀）。
#: 最後一段 `vmware -v` 是 ESXi 天條（2026-10-17）用的第二個訊號：ESXi 上 `uname -s`
#: 回 `VMkernel`、`vmware -v` 回 `VMware ESXi x.y.z build-…`；其他平台沒這個指令，
#: 錯誤導進 /dev/null、`|| true` 讓整行 rc 不受影響。
#: 再後面一段是 vCenter（VCSA）天條（2026-10-18）：VCSA 的 `uname -s` 是 Linux，只能看
#: 它特有的標記檔／目錄（唯讀的存在測試），命中就印 VMWARE_APPLIANCE。
PROBE_COMMAND = ("uname -s; id -u; "
                 "command -v sudo >/dev/null 2>&1 && echo HASSUDO || echo NOSUDO; "
                 "vmware -v 2>/dev/null || true; "
                 "{ " + VCSA_MARKER_TEST + "; } && echo VMWARE_APPLIANCE || true; "
                 # 平台白名單（2026-10-19）：os-release 四欄、舊版 release、設備標記——唯讀
                 + platform_probe_sh())

#: 探測輸出裡平台白名單那幾行的前綴；判 uname／uid 時要先排除（PRETTY_NAME 會含 Linux 字樣）
_PROBE_EXTRA_PREFIXES = ("OSREL_", "LEGACYREL_", "LEGACYDENY=", "APPLIANCE_MARKER=")

#: 天條涵蓋的 VMware 平台（探測回報的 os 值）。onboard／批次一律拒絕。
VMWARE_PLATFORMS = ("esxi", "vcsa")


def vmware_refusal(platform: str | None) -> str | None:
    """VMware 平台回拒絕訊息，其他回 None。"""
    if platform == "esxi":
        return ESXI_REFUSED_MESSAGE
    if platform == "vcsa":
        return VCSA_REFUSED_MESSAGE
    return None

# ===== 天條：ESXi 不可納管、不可在 ESXi 上建任何帳號（使用者 2026-10-17）=====
#
# ESXi 是虛擬化平台本身，資訊只能從 vCenter／vROps API／RVTools 取。
# 手動一鍵、批次、排程自動三條路都走 onboard()，所以守門只寫在這裡一份：
# 連線探測後、**任何 bootstrap（建帳號／佈金鑰／改 sudoers）之前**判斷，是 ESXi 就拒絕。
# 呼叫端照原本寫稽核（onboard_audit／batch_onboard_result），stage 會是 ESXI_REFUSED_STAGE。
ESXI_REFUSED_STAGE = "refused"
ESXI_REFUSED_MESSAGE = ("ESXi 不可納管，請改由 vCenter／RVTools 取得"
                        "（未建帳號、未佈金鑰、未改 sudoers）")


def is_esxi_output(raw: str | None) -> bool:
    """探測輸出是不是 ESXi：`uname -s`=VMkernel，或 `vmware -v` 含 VMware ESXi。"""
    low = (raw or "").lower()
    return "vmkernel" in low or "vmware esxi" in low


# 主機金鑰策略刻意自己寫，不用 paramiko 內建的兩個：
#   · AutoAddPolicy 只記在記憶體、不寫檔 → 每次連線都是「首見」＝沒有驗證
#   · WarningPolicy 只印警告照連 → 等於 StrictHostKeyChecking=no
# 兩個都會被 test_no_sshpass.py 擋下來。
class _AcceptNewPolicy:
    """paramiko 版的 `StrictHostKeyChecking=accept-new`（寫進 SSH_KNOWN_HOSTS）。

    - 已記錄且相同 → paramiko 自己放行（不會進到這裡）
    - 已記錄、同型別但不同 → paramiko 自己丟 BadHostKeyException（不會進到這裡）
    - 從沒記錄過 → 進到這裡：**寫進 known_hosts 才放行**
    - 記錄過「別種型別」的金鑰 → 也會進到這裡；**拒絕**，不當成首見

    寫不進 known_hosts 就拒絕連線。OpenSSH 遇到這種情況只警告照連，但那樣之後每次
    都是首見，驗證形同虛設（SSH_HOSTKEY_OPTS 註解講的那個退化）——寧可擋下來講清楚。
    """

    def __init__(self, known_hosts: str):
        self.known_hosts = known_hosts

    def missing_host_key(self, client, hostname, key):
        import paramiko

        with _KNOWN_HOSTS_LOCK:
            # 鎖內重讀：同一批並行探測可能剛有別的執行緒寫進同一台
            current = paramiko.HostKeys()
            if os.path.exists(self.known_hosts):
                current.load(self.known_hosts)
            known = current.lookup(hostname)
            if known:
                if known.get(key.get_name()) == key:
                    return
                raise paramiko.SSHException(
                    f"Host key verification failed：{hostname} 在 known_hosts 已有記錄，"
                    f"但這次給的主機金鑰不同（{key.get_name()}）")
            line = f"{hostname} {key.get_name()} {key.get_base64()}\n"
            try:
                fd = os.open(self.known_hosts,
                             os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
                with os.fdopen(fd, "a", encoding="ascii") as fh:
                    fh.write(line)
            except OSError as exc:
                raise paramiko.SSHException(
                    f"Host key verification failed：known_hosts 寫不進去"
                    f"（{self.known_hosts}：{exc.strerror}），不接受未記錄的主機金鑰") from exc


def _ssh_connect(host: str, username: str, password: str, timeout: int):
    """以密碼登入，回傳已連線的 paramiko.SSHClient。失敗直接丟例外。

    allow_agent / look_for_keys 關掉：**只用這次給的密碼**。不然 paramiko 會先拿收集器
    自己的私鑰（持有全機隊權限的那把）去試目標機——多一筆失敗登入紀錄、多撞一次鎖定
    門檻，而且把收集金鑰亮給還沒納管的機器看，沒有任何好處。
    """
    import paramiko

    client = paramiko.SSHClient()
    if os.path.exists(SSH_KNOWN_HOSTS):
        client.load_host_keys(SSH_KNOWN_HOSTS)
    client.set_missing_host_key_policy(_AcceptNewPolicy(SSH_KNOWN_HOSTS))
    try:
        # ⚠️ 2026-09-22 更正：這裡原本的註解寫「對方只開 keyboard-interactive 時，
        # paramiko 的密碼認證會自動退回……不用另外處理」——**那是錯的**。
        # paramiko 5.0.0 的 SSHClient._auth 是：
        #     if password is not None:  try auth_password; return
        #     elif two_factor:          try auth_interactive_dumb
        # 是 `elif` 不是 fallback——給了密碼就只試 auth_password。
        # 留著這句錯註解會讓下一個人以為這條路徑已經處理過了。
        # （這台目前的證據顯示對方 allowed methods 含 password，所以先不加退路，
        #   等看到原始錯誤再決定要不要做。）
        client.connect(host, port=SSH_PORT, username=username, password=password,
                       timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
                       allow_agent=False, look_for_keys=False)
    except BaseException as exc:
        # 失敗時把**談判到的事實**釘在例外上，讓上層顯示得出來。
        # 2026-09-22 的教訓：真正的原因被一句「帳號或密碼不對」蓋掉，
        # 使用者照著那句話去查帳號鎖定／PermitRootLogin／rlogin，全部是對的，
        # 因為問題根本不在那裡。猜了兩輪都錯，所以改成「把事實攤開給人看」。
        try:
            exc._ssh_facts = _transport_facts(client, host, username)  # noqa: SLF001
        except Exception:  # noqa: BLE001 - 蒐證失敗不可以蓋掉原始例外
            pass
        client.close()
        raise
    return client


def _transport_facts(client, host: str, username: str) -> dict:
    """把這次連線談判到的事實收集起來：對方 SSH 版本、談成的演算法、接受的認證方法。

    為什麼要這些：`ssh -v` 印的就是這幾行，人靠它判斷「是協商問題還是認證問題」。
    系統不給，人就只能自己去目標機上跑 ssh -v——而使用者明講過他手上只有畫面。

    **蒐證失敗一律吞掉**：這是附加資訊，不可以因為蒐證出錯而蓋掉真正的例外。
    """
    facts: dict = {}
    t = None
    try:
        t = client.get_transport()
    except Exception:  # noqa: BLE001
        t = None
    if t is not None:
        for key, getter in (("remote_version", lambda: t.remote_version),
                            ("local_version", lambda: t.local_version),
                            ("active", lambda: t.is_active())):
            try:
                facts[key] = getter()
            except Exception:  # noqa: BLE001
                pass
        try:
            so = t.get_security_options()
            facts["kex"] = list(so.kex)[:1]
            facts["cipher"] = list(so.ciphers)[:1]
            facts["mac"] = list(so.digests)[:1]
        except Exception:  # noqa: BLE001
            pass
    # 對方接受哪些認證方法：送一次 none 認證，RFC 4252 規定會回「可用的是這些」。
    # 這不是攻擊手法——OpenSSH 客戶端每次連線都這樣做，`ssh -v` 的
    # 「Authentications that can continue: ...」就是它。
    try:
        import paramiko

        probe = paramiko.Transport((host, SSH_PORT))
        probe.banner_timeout = 8
        probe.start_client(timeout=8)
        facts.setdefault("remote_version", probe.remote_version)
        try:
            probe.auth_none(username)
            facts["allowed_auth"] = ["none（對方讓 none 通過）"]
        except Exception as e:  # noqa: BLE001 - 就是靠這個例外拿清單
            facts["allowed_auth"] = list(getattr(e, "allowed_types", None) or [])
        probe.close()
    except Exception:  # noqa: BLE001
        pass
    return facts


def _facts_text(exc: BaseException) -> str:
    """把 _transport_facts 蒐到的事實排成一行。沒有就回空字串。"""
    f = getattr(exc, "_ssh_facts", None)
    if not isinstance(f, dict) or not f:
        return ""
    bits = []
    if f.get("remote_version"):
        bits.append(f"對方 SSH={f['remote_version']}")
    if f.get("allowed_auth") is not None:
        allowed = "、".join(f["allowed_auth"]) or "（問不出來）"
        bits.append(f"對方接受的認證方法={allowed}")
    for k, label in (("kex", "kex"), ("cipher", "cipher"), ("mac", "mac")):
        if f.get(k):
            bits.append(f"{label}={f[k][0]}")
    return "｜" + "，".join(bits) if bits else ""


def _ssh_error_text(host: str, exc: BaseException) -> str:
    """把連線階段的例外翻成 OpenSSH 的講法。

    為什麼要仿 OpenSSH 的字：classify_failure 靠這些字把失敗歸到 connect 階段、
    並給「查網路／埠／帳密」而不是「查 sudo」的建議。字對不上，連不到的機器就會
    被標成 execute，把人引去查一個根本不存在的 sudo 問題（2026-08-16 的教訓）。
    """
    # ⚠️ 這裡**完全不 import paramiko 的例外類別**——用 duck-typing（型別名稱＋屬性）判斷。
    # 理由（2026-09-14 連環踩到）：paramiko 各環境暴露方式天差地遠——
    #   · `paramiko.ssh_exception.X` 屬性 → 某些版本 AttributeError
    #   · `paramiko.NoValidConnectionsError` 頂層 → 5.0.0 根本沒有
    #   · `from paramiko.ssh_exception import X` → 公司機那份 paramiko 連子模組都沒有（ModuleNotFoundError）
    # 錯誤「格式化」這一步只要一崩，就把真正的失敗原因整個蓋掉。所以這步不准依賴 paramiko 任何內部結構。
    name = type(exc).__name__
    low = str(exc).lower()

    # NoValidConnectionsError 有 .errors dict（{addr: 內層例外}）——不靠 import 它的類別，直接看屬性
    errs = getattr(exc, "errors", None)
    if isinstance(errs, dict) and errs:
        inner = next(iter(errs.values()), None)
        if inner is not None and inner is not exc:
            return _ssh_error_text(host, inner)

    where = f"ssh: connect to host {host} port {SSH_PORT}"
    if name == "BadHostKeyException" or "host key" in low:
        return (f"Host key verification failed：{host} 的主機金鑰跟 known_hosts 記錄的不同"
                f"（可能重灌過，也可能是中間人）")
    if name == "BadAuthenticationType":
        allowed = ", ".join(getattr(exc, "allowed_types", []) or [])
        return (f"No supported authentication methods available（對方只接受：{allowed}）"
                f"｜原始錯誤：{name}: {exc}" + _facts_text(exc))
    if name in ("AuthenticationException", "PartialAuthentication") \
            or "authentication" in low or "permission denied" in low:
        # ⚠️ 2026-09-22：這裡原本只回這一句，**把所有認證類例外壓成同一句話**，
        # 原始訊息整個丟掉。使用者的密碼是對的（同一台、同一個帳號，
        # OpenSSH 密碼登入成功），卻被這句話帶去查帳號鎖定、PermitRootLogin、
        # rlogin——全部是對的，因為問題不在那裡。
        # 白話留著（它對多數情況是對的），但後面一定要附**原始例外與談判事實**。
        msg = ("Permission denied（帳號或密碼不對，或該帳號不允許 SSH 登入）"
               f"｜原始錯誤：{name}: {exc}" + _facts_text(exc))
        # AIX 專屬提示（2026-09-22 實例：8 台 AIX 全部卡在這裡）。
        # 公鑰剛佈好、權限也對，卻回 publickey,password 都被拒——
        # AIX 的 account_locked=true 會連金鑰一起擋，而 Linux 的同義操作不會。
        # **只在 AIX 顯示**：對 Linux 講這句只會多一條雜訊把人帶偏。
        if "publickey" in low and "password" in low:
            msg += ("｜AIX 常見原因：**收集帳號被鎖**。"
                    "AIX 的 account_locked=true 會連公鑰登入一起擋（Linux 的鎖密碼不會）。"
                    "確認：`lsuser -a account_locked <帳號>`；"
                    "解鎖：`chuser account_locked=false rlogin=true <帳號>`")
        return msg
    if isinstance(exc, socket.gaierror):
        return f"ssh: Could not resolve hostname {host}"
    if isinstance(exc, ConnectionRefusedError):
        return f"{where}: Connection refused"
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in low:
        return f"{where}: Connection timed out"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in (errno.EHOSTUNREACH, errno.ENETUNREACH):
        return f"{where}: No route to host"
    # paramiko 本身沒裝好（含子模組缺失 ModuleNotFoundError）——講清楚是收集端環境問題，不是目標主機
    if isinstance(exc, (ImportError, ModuleNotFoundError)) or "paramiko" in low:
        return (f"SSH 連線失敗：這台收集端的 paramiko 沒裝好（{name}: {exc}）"
                "——請在收集端重裝 paramiko（pip install --force-reinstall paramiko），"
                "或改用『離線匯入』（貼指令畫面，完全不需要 paramiko）")
    # 金鑰交換/加密演算法不相容（老 FOS/網通設備常見）——講明是協商問題，方便對症
    if name == "SSHException" and ("key exchange" in low or "cipher" in low or "kex" in low
                                   or "no acceptable" in low or "unable to agree" in low):
        return f"SSH 連線失敗：金鑰交換/加密演算法不相容（{exc}）——老設備常見，需啟用 legacy 演算法"
    return f"SSH 連線失敗：{name}: {exc}"


#: 連線層失敗時回的結束碼——跟 OpenSSH 的 ssh 一樣用 255。
SSH_CONNECT_FAILED = 255

#: 最近一次連線失敗的完整 traceback（依主機），給收集紀錄用（collect_log）。
#: 2026-09-15：_ssh_exec 把例外翻成人話字串回傳，原始 traceback 就丟了——
#: 「paramiko 沒有 SSHClient」修了五輪，每輪都缺這一段證據。
#: traceback 只含程式碼行，不含區域變數的值，密碼不會出現在這裡。
_LAST_CONNECT_TRACE: dict[str, str] = {}


def last_connect_trace(host: str) -> str | None:
    return _LAST_CONNECT_TRACE.get(host)


def _remember_connect_error(host: str, exc: BaseException) -> None:
    import traceback as _tb

    if len(_LAST_CONNECT_TRACE) > 200:
        _LAST_CONNECT_TRACE.pop(next(iter(_LAST_CONNECT_TRACE)))
    _LAST_CONNECT_TRACE[host] = "".join(
        _tb.format_exception(type(exc), exc, exc.__traceback__))[-4000:]


def _ssh_exec(host: str, username: str, password: str, command: str,
              stdin_text: str | None = None, timeout: int = 40,
              on_line=None) -> tuple[int, str]:
    """登入 host、執行 command、把 stdin_text 從標準輸入送進去，回 (結束碼, 輸出)。

    - stderr 併進 stdout：目標主機的錯誤訊息也要即時看得到
    - 輸出**逐行**交給 on_line（邊跑邊回報進度，不是等結束才一次拿到）
    - 連線階段失敗回 (SSH_CONNECT_FAILED, 仿 OpenSSH 的錯誤字串)，classify_failure 照樣認得
    - **一定有總時限**（timeout + _STREAM_GRACE）：目標主機不吐東西又不結束時
      （sudo 在等密碼、連線半開）讀取迴圈不能永遠卡住——2026-08-16 公司主機
      看過畫面秒數一直跑、超過上限也不回來。超時就中止並回「超過 N 秒…強制中止」

    password 只傳給 _ssh_connect，不進 command、不寫檔、不進回傳值。
    """
    deadline_s = timeout + _STREAM_GRACE
    deadline = time.monotonic() + deadline_s
    try:
        client = _ssh_connect(host, username, password, timeout)
    except Exception as exc:  # noqa: BLE001 — 連線階段的任何失敗都要翻成人話回去
        _remember_connect_error(host, exc)
        return SSH_CONNECT_FAILED, _ssh_error_text(host, exc) + "\n"

    collected: list[str] = []
    rc = -1
    timed_out = False

    def _emit(text: str) -> None:
        collected.append(text)
        if on_line is not None:
            on_line(text)

    try:
        chan = client.get_transport().open_session(timeout=timeout)
        chan.set_combine_stderr(True)
        chan.exec_command(command)
        # 腳本走 stdin 進去，**不進遠端指令字串**（目標機的 `ps`、稽核看不到內容以外的東西）、
        # **不落磁碟**、**不做混淆**。送完就關寫端，遠端讀到 EOF 才會開始跑。
        if stdin_text:
            chan.sendall(stdin_text.encode("utf-8"))
        chan.shutdown_write()

        chan.settimeout(1.0)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        while True:
            if time.monotonic() > deadline:
                timed_out = True
                break
            try:
                data = chan.recv(32768)
            except socket.timeout:
                continue
            if not data:
                break
            pending += decoder.decode(data)
            *lines, pending = pending.split("\n")
            for ln in lines:
                _emit(ln + "\n")
        pending += decoder.decode(b"", final=True)
        if pending:
            _emit(pending)

        if timed_out:
            collected.append(
                f"\n[!] 超過 {deadline_s} 秒沒有完成，已強制中止。"
                f"常見原因：登入帳號的 sudo 需要密碼（本流程沒辦法回答它）、"
                f"或 SSH 連線卡在半開狀態。\n")
        else:
            # 輸出讀完之後結束碼通常馬上到；最多等 10 秒，不讓這一步變成新的卡點
            wait_until = time.monotonic() + 10
            while not chan.exit_status_ready() and time.monotonic() < wait_until:
                time.sleep(0.05)
            if chan.exit_status_ready():
                rc = chan.recv_exit_status()
    except Exception as exc:  # noqa: BLE001 — 執行中斷線：留在輸出裡，交給 classify_failure
        collected.append(f"\n[!] SSH 連線中斷：{type(exc).__name__}: {exc}\n")
    finally:
        client.close()
    return rc, "".join(collected)


def _ssh_executor(host: str, username: str, password: str, platform: str,
                  script: str, collector_ip: str, timeout: int = 40,
                  as_root: bool = False) -> OnboardResult:
    """以密碼登入目標，執行納管腳本（程式內 SSH 用戶端，見上方 _ssh_exec）。

    密碼只在記憶體裡交給 SSH 協定——不進 argv（`ps` 看不到）、不經環境變數、
    不寫任何檔案、不進回傳值。

    ⚠️ 真機密碼登入只能在 221 驗（家裡不能碰目標密碼、也不該由 AI 處理密碼）。
    已知前提：
    - **Linux 不是 root 時走 SUDO_STDIN_WRAPPER**：密碼當 stdin 第一行給 `sudo -S`，
      NOPASSWD 的機器先 `read` 吃掉那行（不會被當指令執行印出來）。
    - Windows 假設登入帳號是系統管理員、且已有 OpenSSH Server。
    - **AIX 必須以 root 登入**：AIX 未必裝 sudo（常在 /opt/freeware/bin 或改用 RBAC），
      不能像 Linux 那樣假設 `sudo bash` 會通。改成 `ksh -s`，腳本走 stdin。

    ## ⚠️ 腳本一律走 stdin，不做 base64（2026-08-28 改）

    原本三個平台都是「把腳本 base64 編碼後塞進遠端指令」：
      · Linux/AIX：`echo <b64> | base64 -d | sudo bash`
      · Windows：解碼寫進 `$env:TEMP` 再 `-ExecutionPolicy Bypass -File` 執行

    那是**勒索軟體投放的標準動作**（T1027 混淆 ＋ T1059.001 ＋ T1562.001 削弱防禦）。
    單台跑或許沒人注意，但這支就是拿來**大量納管**的——一個上午對幾百台連續執行，
    EDR/SOC 看到的畫面會是橫向移動加惡意程式投放，而觸發帳號持有全機隊金鑰。

    改成 stdin 之後每個面向都更好：
      · **純文字可稽核**——目標主機的稽核日誌看得到實際跑了什麼
      · **不進 argv**（`ps` 看不到）、**不落磁碟**（Windows 不再寫 TEMP）
      · Windows 不需要 Bypass：`-Command -` 從管道讀指令，
        **執行原則管的是腳本檔，管不到管道進來的指令**
      · 原本用 base64 多半是為了避開引號跳脫，走 stdin 之後那個問題直接消失
    """
    if platform == "linux":
        # 已經是 root 就別加 sudo：實際錯誤是 `sudo: command not found`——
        # 那台根本沒裝 sudo，而它其實不需要（登入身分已是 root）。
        if as_root:
            command, payload = "bash -s", script
        else:
            command, payload = SUDO_STDIN_WRAPPER, password + "\n" + script
    elif platform == "aix":
        # 不加 sudo：AIX 未必有；改成要求以 root 登入。不是 root 的話腳本自己會停下、印「請改用 root 再跑一次」
        command, payload = "ksh -s", script
    else:  # windows
        # `-Command -` 從 stdin 讀指令。**這條路徑不受執行原則約束**
        # （ExecutionPolicy 管的是腳本檔，不是管道進來的指令），
        # 所以既不用 Bypass、也不用先把檔案寫到 TEMP。
        command, payload = "powershell -NoProfile -NonInteractive -Command -", script

    rc, out = _ssh_exec(host, username, password, command, stdin_text=payload,
                        timeout=timeout, on_line=lambda ln: progress_note(host, ln))
    if rc != 0 or "完成。" not in out:
        stage, msg = classify_failure(out)
        return OnboardResult(False, stage, msg, out[-800:])
    import root_key

    return OnboardResult(True, "execute", success_message(out), out[-800:],
                         root_key=root_key.ROOT_KEY_MARK in out)


def success_message(out: str) -> str:
    """納管成功時要回的那句話。**成功不一定是完整的成功。**

    沒有 sudo 的機器（Debian 最小安裝的預設）跳過了唯讀白名單，帳號與公鑰都佈好，
    但 `/sys/class/dmi/id/*` 是 0400 只有 root 讀得到，所以序號與機型收不到。
    如果這裡一律回「納管腳本執行完成」，那台的序號欄以後會是空的，而看到空白的人
    會去查收集是不是壞了——實際上是納管當下就決定了收不到，只是沒人講。
    """
    if SUDOERS_INVALID_MARK in out:
        return ("納管腳本跑完了，但**唯讀白名單沒有佈上去**："
                "sudoers 語法被 visudo 擋下，已還原成納管前的樣子。"
                "這不是這台的問題，是我們寫進去的規則有語法錯——"
                "重新納管幾次都會是同樣結果，要先修規則。"
                "受影響的收集項：硬體序號、帳號盤點需 root 的欄位、"
                "值班健檢的 SSH 爆破與核心錯誤")
    if NO_SUDO_MARK in out:
        return ("納管完成。這台沒有 sudo，唯讀白名單跳過了——"
                "影響只有硬體序號一項（product_serial 是 0400，只有 root 讀得到）；"
                "機型、OS、帳號、服務照常收。VM 的話影響更小，識別本來就靠 "
                "vCenter 給的 vm_uuid。真的需要序號（通常是實體機）就裝 sudo "
                "之後再跑一次納管，腳本可重複執行")
    return "納管腳本執行完成"


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
    ("ssh protocol banner", "22 埠有回應但讀不到 SSH 服務的開場訊息——可能不是 SSH、"
                            "服務卡住，或連線被中間設備切斷"),
    # 放最後：上面認不出來的連線層失敗（_ssh_error_text 的兜底字串）也要歸 connect
    ("ssh 連線失敗", "SSH 連線階段就失敗了（還沒進到目標機）——原因見輸出"),
)


#: 不是 root 時，遠端要跑的那一行。**這是 2026-09-08 公司實測 sudo 要密碼才發現的。**
#:
#: 原本寫 `sudo bash -s`：腳本從 stdin 餵進去，sudo 想問密碼時 stdin 已經被腳本
#: 佔滿、又沒有 TTY，只能吐 `sudo: a terminal is required to read the password`。
#: 使用者打的密碼其實只用在 SSH 登入，**從頭到尾沒被拿去回答 sudo**。
#:
#: 修法：把密碼當成 stdin 的第一行送過去，讓 `sudo -S`（從標準輸入讀密碼，
#: 這是 sudo 自己的文件功能）去讀，剩下的才是腳本。
#:
#: 為什麼要先問 `sudo -n true`：如果這台其實是 NOPASSWD，sudo 不會去讀那一行，
#: 密碼就會變成腳本的第一行被 bash 執行——輸出裡會出現
#: `<密碼>: command not found`，等於把密碼印進畫面與稽核紀錄。所以先問一句
#: 「你要不要密碼」，不要的話就 `read` 把那行吃掉丟棄。判斷在**遠端執行當下**
#: 做，不是事前探測，所以沒有「探測完到執行之間 sudoers 被改」的空窗。
#:
#: 這一行刻意維持純文字、可讀、可稽核：沒有 base64、沒有落地暫存檔、
#: 沒有 ExecutionPolicy Bypass。密碼只走 stdin，不進 argv（`ps` 看不到）、
#: 不寫檔案。`-p ''` 是把提示字串清空，免得提示文字混進輸出。
SUDO_STDIN_WRAPPER = (
    "if sudo -n true 2>/dev/null; then read -r _pw; exec sudo bash -s; "
    "else exec sudo -S -p '' bash -s; fi"
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
    if "a terminal is required" in low or "must have a tty" in low:
        # 這台的 sudoers 有 requiretty：不給 TTY 就不准跑 sudo，餵密碼也沒用。
        return "execute", ("進得去，但這台的 sudo 設了 requiretty（不給終端機就不准執行）"
                           "——密碼餵不進去，要請主機管理端拿掉那條設定，或改用 root 登入")
    if "sorry, try again" in low or "incorrect password attempt" in low:
        return "execute", "進得去，但 sudo 不接受這個密碼（登入密碼與 sudo 密碼可能不同）"
    if "is not in the sudoers" in low or "not allowed to execute" in low:
        return "execute", "進得去，但這個帳號不在 sudoers 裡（沒有 sudo 權限）"
    if "sudo" in low and ("password" in low or "密碼" in (out or "")):
        return "execute", "進得去，但 sudo 這一關沒過（密碼或權限問題）"
    return "execute", "腳本執行未回報完成"


# ===== 一鍵納管「做之前」的判斷（單一來源，2026-10-20）=====
#
# 使用者 2026-10-20：預檢（只看不動）要準確預告一鍵納管會做什麼。原本預檢自己寫一份判斷，
# Windows 那台預檢說「不會納管，請開 WinRM」，一鍵納管卻實際走了 Windows 那條（探測認出 Windows
# → 平台改成 windows → 跑 build_windows_script）。所以把 onboard() 裡「任何副作用之前」的判斷
# 抽成下面三支純函式：onboard() 用它決定做不做，precheck_service 用同一支預告結論。
# 每一支都不連線、不建帳號、不碰金鑰——只看參數與探測結果。

#: 判斷結果代碼（預檢用它對到「可以納管／需改用 root／不會納管／登不進去」）
DECIDE_OK = "ok"
DECIDE_ARGS = "args"              # 參數／設定不對（收集器位址、AIX 帳號名過長、未知平台）
DECIDE_VMWARE = "vmware"          # ★ 天條
DECIDE_PROBE_FAILED = "probe_failed"   # 登不進去／探測沒跑完
DECIDE_UNKNOWN_OS = "unknown_os"  # 登得進去但認不出平台（網路設備、非 POSIX shell）
DECIDE_PLATFORM = "platform"      # 平台白名單拒絕
DECIDE_NEED_ROOT = "need_root"    # 不是 root、又沒有（可用的）sudo


def onboard_args_refusal(platform: str, collector_ip: str, account: str | None) -> dict | None:
    """連線**之前**就判得出來的拒絕。回 {code, stage, message} 或 None（可以往下）。"""
    if platform in VMWARE_PLATFORMS:
        # 呼叫端已經知道是 VMware 平台（例如批次探測回報的平台）——連都不用再連
        return {"code": DECIDE_VMWARE, "stage": ESXI_REFUSED_STAGE, "message": vmware_refusal(platform)}
    if platform not in ("linux", "aix", "windows"):
        return {"code": DECIDE_ARGS, "stage": "connect", "message": f"未知平台：{platform}"}
    # 收集器位址在**連線之前**就要驗（2026-09-17）。
    # 原本只在 build_script 裡驗，也就是已經 SSH 進目標主機之後才發現參數是佔位字串——
    # 那趟連線白跑，而且失敗訊息要等 20 秒的連線逾時才出現。
    # 明顯錯的參數不該讓人先等網路：先擋掉，訊息立刻出來。
    try:
        validate_collector_ip(collector_ip)
        # 帳號名長度同理：AIX 的 mkuser 會直接拒絕超過 max_logname 的名字，
        # 這件事在本機就判得出來，不必先 SSH 過去才發現。
        if platform == "aix" and account and len(account) > AIX_MAX_LOGNAME:
            raise ValueError(
                f"AIX 帳號名「{account}」有 {len(account)} 個字元，超過預設上限 "
                f"{AIX_MAX_LOGNAME}——mkuser 會直接拒絕。請改用較短的名字"
                f"（預設 {DEFAULT_ACCOUNT_AIX}），或先在該主機放寬 max_logname（要重開機）")
    except ValueError as exc:
        return {"code": DECIDE_ARGS, "stage": "connect", "message": str(exc)}
    return None


def decide_after_probe(platform: str, detected: dict, username: str) -> dict:
    """探測完、任何副作用之前：做不做、用哪個平台做。純函式。

    回 {ok, code, stage, message, platform}——ok=True 時 platform 是實際會跑的平台
    （探測認出的優先於畫面上選的），stage／message 是拒絕時 onboard() 回的那兩個值。
    """
    os_ = detected.get("os")

    def no(code, stage, message, plat=platform):
        return {"ok": False, "code": code, "stage": stage, "message": message, "platform": plat}

    # ★ 天條：ESXi 不可納管。必須在 collector_pubkey()／build_script()／執行器之前，
    # 這之後的每一步都是副作用（建帳號、佈金鑰、改 sudoers）。
    if os_ in VMWARE_PLATFORMS:
        return no(DECIDE_VMWARE, ESXI_REFUSED_STAGE, vmware_refusal(os_), os_)
    # fail-closed：探測沒進去、逾時、或認不出平台 → 不知道這台是什麼，就不納管。
    # Linux／AIX 的 `uname -s` 一定認得出來，所以這條不會誤擋正常機器；
    # 認不出來的（含輸出被截、選單程式、非 POSIX shell）正是不該盲送 bootstrap 的。
    if detected.get("error"):
        return no(DECIDE_PROBE_FAILED, "connect", f"探測失敗，未執行納管：{detected['error']}")
    if not os_:
        return no(DECIDE_UNKNOWN_OS, "connect", "探測失敗，未執行納管：認不出這台的平台")
    # ★ 平台白名單（2026-10-19）：Linux 底的設備、非白名單發行版 → 拒絕，同樣在任何副作用之前。
    # 已納管過的主機重新納管也走這裡：照樣拒絕並講原因，**不**自動收回既有帳號。
    if detected.get("refusal"):
        return no(DECIDE_PLATFORM, PLATFORM_REFUSED_STAGE, detected["refusal"], os_)
    real = os_          # 探測認出的優先於畫面上選的
    uid, has_sudo = detected.get("uid"), detected.get("has_sudo")
    if uid is not None and uid != 0 and not has_sudo:
        return no(DECIDE_NEED_ROOT, "connect",
                  f"登入帳號 {username} 不是 root，而且這台沒有 sudo——納管需要 root "
                  f"才能建帳號、寫 authorized_keys。請改用 root 登入"
                  f"（AIX 常見；Linux 也可能沒裝 sudo）。", real)
    if real == "aix" and uid is not None and uid != 0:
        # AIX 執行器是 `ksh -s`、不加 sudo（AIX 未必有）：不是 root 進去，腳本第一段就會停下
        # 印「請改用 root 再跑一次」（沒有副作用）。2026-10-20 提前到這裡講，結果一樣、少跑一趟。
        return no(DECIDE_NEED_ROOT, "connect",
                  f"登入帳號 {username} 不是 root——AIX 一鍵納管要用 root 登入"
                  f"（AIX 這條不走 sudo）。請改用 root 登入。", real)
    return {"ok": True, "code": DECIDE_OK, "stage": "", "message": "", "platform": real}


def decide_windows_selected(detected: dict) -> dict:
    """畫面選 Windows：探測只用來守天條（VMware 一律拒絕），其餘照舊直接走 Windows 那條。

    · 登得進去但認不出平台（Windows 的 shell 跑不了 uname）不擋——那正是 Windows 的常態。
    · **登不進去**（probe_target 只在 SSH 連線／認證失敗時回 error）就停：執行器用同一組帳密一定也
      登不進去，再試一次只是多一筆登入失敗——網域帳號有鎖定門檻時會更快被鎖（2026-10-20 審查）。"""
    os_ = detected.get("os")
    if os_ in VMWARE_PLATFORMS:
        return {"ok": False, "code": DECIDE_VMWARE, "stage": ESXI_REFUSED_STAGE,
                "message": vmware_refusal(os_), "platform": os_}
    if detected.get("error"):
        return {"ok": False, "code": DECIDE_PROBE_FAILED, "stage": "connect",
                "message": f"登入失敗，未執行納管：{detected['error']}", "platform": "windows"}
    return {"ok": True, "code": DECIDE_OK, "stage": "", "message": "", "platform": "windows"}


def onboard_decision(platform: str, detected: dict | None, username: str,
                     collector_ip: str, account: str | None = None) -> dict:
    """onboard() 在任何副作用之前會怎麼決定——給預檢用。跟 onboard() 走同樣兩步、同樣順序：
    先 onboard_args_refusal，再（不是 windows 才）decide_after_probe。

    畫面選 Windows 時只守天條（decide_windows_selected），其餘直接走 Windows 那條——跟 onboard() 一樣。
    """
    refused = onboard_args_refusal(platform, collector_ip, account)
    if refused:
        return {"ok": False, "platform": platform, **refused}
    if platform == "windows":
        return decide_windows_selected(detected or {})
    return decide_after_probe(platform, detected or {}, username)


def onboard(host: str, platform: str, username: str, password: str,
            collector_ip: str, pubkey: str | None = None,
            executor=None, account: str | None = None,
            comment: str | None = None, probe_runner=None,
            script: str | None = None) -> OnboardResult:
    """把一台主機納管起來。

    probe_runner：注入探測（測試用）。有給就算 executor 也是假的，照樣先探測——
    ESXi 守門靠的就是這一步。

    ⚠️ password 只在本函式與 executor 之間傳遞、用完即丟——
    絕不寫進回傳值、DB、log。呼叫端（API）也必須遵守：收到就用、用完不留。
    executor 可注入，測試不碰真網路、不碰真密碼。
    """
    # 「做不做、怎麼做」的判斷全部在 onboard_decision 系列（2026-10-20）：預檢（只看不動）呼叫
    # 同一組函式，所以預檢說什麼，一鍵納管就做什麼——不會一邊說「不會納管」、一邊實際跑 Windows 那條。
    refused = onboard_args_refusal(platform, collector_ip, account)
    if refused:
        return OnboardResult(False, refused["stage"], refused["message"])

    # 登入後先問機器自己是什麼，不要相信畫面上選的（2026-08-16 公司主機踩到兩次）：
    # AIX 與 Linux 從網路上分不出來，選錯就拿 useradd 去打 AIX，一定失敗——
    # 但登進去一行 uname -s 就確定了，沒有理由讓人猜。
    # 只在真的要連網路時做（executor 有注入代表在測試，不多打一次網路）。
    detected = {}
    if executor is None or probe_runner is not None:
        detected = probe_target(host, username, password, runner=probe_runner)
        # ★ 天條（ESXi／vCenter）、fail-closed、平台白名單、root／sudo——全部在任何副作用之前。
        # 畫面選 Windows 也探測一次（2026-10-20 審查）：原本選 Windows 完全不探測，VMware 設備被選成
        # Windows 時引擎這端沒有任何守門；現在只多看「是不是 VMware」，其餘照舊走 Windows。
        d = (decide_windows_selected(detected) if platform == "windows"
             else decide_after_probe(platform, detected, username))
        if not d["ok"]:
            return OnboardResult(False, d["stage"], d["message"])
        platform = d["platform"]

    try:
        pubkey = pubkey or collector_pubkey()          # 金鑰還沒產生也走這條
        if platform in ("linux", "aix"):
            # ONBOARD-FLOW1：網頁一鍵納管跑的是**跟下載的 webit3_onboard.sh 同一份檔案**
            # （同版本、同 sha256），外層自己依 uname 選 Linux／AIX 那段——兩條路不會分岔。
            import onboard_bundle

            validate_collector_ip(collector_ip)
            # 呼叫端（API、批次、排程）傳 onboard_bundle.bundle_for(conn) 進來——跟下載的那份
            # 逐位元組相同（含 AIX 帳號、備援位址等設定）。沒傳（單元測試）才自己組。
            if not script:
                script = onboard_bundle.build_bundle(pubkey, collector_ip, account, comment)
        else:
            script = build_script(platform, pubkey, collector_ip, account, comment)
    except ValueError as exc:   # 例如 AIX 帳號名超過 max_logname、公鑰讀不到
        return OnboardResult(False, "connect", str(exc))
    run = executor or _ssh_executor
    kw = {}
    if executor is None:
        kw["as_root"] = detected.get("uid") == 0
    return run(host=host, username=username, password=password, platform=platform,
               script=script, collector_ip=collector_ip, **kw)


def build_linux_passwd_script(new_password: str, target_account: str = "root") -> str:
    """把 target_account 的密碼設成 new_password。用 chpasswd 走 stdin，不落地。

    ⚠️ new_password 是真密碼——這段腳本文字本身含密碼，所以：
    - 一律走 stdin 送純文字（同 SEC3，不寫目標機的 TEMP）
    - 呼叫端**絕不可以**把這段腳本文字寫進 DB／log／回傳值／稽核
    - 腳本自己不 echo 密碼（set -euo pipefail 不會印，chpasswd 也不回顯）

    只改 Linux。改密碼前提是已經先佈好金鑰（呼叫順序保證）——這樣就算改完
    密碼有問題，金鑰那條路還進得去，等於保險。
    """
    # 單引號 here-doc：$ 反引號都不展開，避免密碼裡的特殊字元改變語意
    return f"""#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "需要 root 才能改密碼" >&2; exit 1; fi
/usr/sbin/chpasswd <<'WEBIT3_PWEOF' 2>/dev/null || chpasswd <<'WEBIT3_PWEOF2'
{target_account}:{new_password}
WEBIT3_PWEOF
{target_account}:{new_password}
WEBIT3_PWEOF2
echo "密碼已更新"
"""


def change_root_password(host: str, username: str, login_password: str,
                         new_password: str, collector_ip: str,
                         executor=None) -> OnboardResult:
    """把目標機 root 密碼改成 new_password。與登入用的 login_password 分開兩個參數，
    因為「用舊密碼登入、把它改成新密碼」正是這支的用途。

    ⚠️ 兩個密碼都用完即丟——不寫回傳值、DB、log。回傳的 OnboardResult.output
    是目標機的 stdout（chpasswd 不回顯密碼），不含任何密碼。
    """
    script = build_linux_passwd_script(new_password, "root")
    run = executor or _ssh_executor
    kw = {}
    if executor is None:
        kw["as_root"] = False   # 用 login 帳號進去，靠 sudo/su 取得 root（跟 onboard 一致）
    return run(host=host, username=username, password=login_password, platform="linux",
               script=script, collector_ip=collector_ip, **kw)


# 非 RHEL 的 Linux 發行版：腳本大致通用，但沒把握。分流用——RHEL 系列直接批次，
# 其餘先試一台再批次（使用者 2026-09-06 定案：「不熟就不盲批」）。
RHEL_FAMILY_HINTS = ("rhel", "red hat", "redhat", "rocky", "centos", "almalinux", "oracle linux")


def is_rhel_family(os_text: str | None) -> bool:
    """從 OS 字串判斷是不是 RHEL 系列。判不出來一律回 False（當成「不熟」，走保守路）。"""
    low = (os_text or "").lower()
    return any(h in low for h in RHEL_FAMILY_HINTS)


def batch_auto_onboard(targets: list[dict], username: str, passwords: list[str],
                       collector_ip: str, account_of, pubkey: str | None = None,
                       unify_password: bool = False, comment: str | None = None,
                       probe_runner=None, onboard_executor=None,
                       passwd_executor=None, script: str | None = None) -> list[dict]:
    """一批主機自動納管：每台試密碼→佈金鑰→（可選）把密碼統一成第一組。

    2026-09-06 使用者定案的「一鍵批次自動納管」的核心。設計重點：

    - **密碼依序試、第一組通就停**（passwords[0]=A 是現行密碼，大多一次就中，
      幾乎不會留失敗紀錄；只有舊 B 密碼的少數會失敗一次）——不是盲噴每一組
    - **unify_password**：登入用的若不是第一組（＝這台還在用舊密碼），就把它
      改成第一組。用 A 登入成功的本來就是 A，不用改
    - **順序是保險**：先佈金鑰、再改密碼。改密碼萬一出事，金鑰那條路還在
    - 回傳每台一列，**絕不含任何密碼**，只講「用第幾組（A/B）」

    ⚠️ 呼叫端負責「環境別＝正式的要擋掉／另外確認」與「非 RHEL 先試一台」——
    這支只忠實執行給它的 targets，判斷正式/測試、OS 分流是上一層的事。

    targets: [{"ip": ...}, ...]。account_of(platform)->收集帳號名。
    回傳每台：{ip, login_ok, password_index, platform, onboarded, password_unified,
    fail_stage, fail_reason}。
    """
    results = []
    for t in targets:
        ip = t["ip"]
        row = {"ip": ip, "login_ok": False, "password_index": None, "platform": None,
               "onboarded": False, "password_unified": False,
               "fail_stage": None, "fail_reason": None}

        # 1) 找出哪一組密碼登得進去（依序、第一組通就停）
        probe = batch_probe_credentials([{"ip": ip}], username, passwords,
                                        runner=probe_runner)[0]
        idx = probe["matched_password_index"]
        if idx is None:
            row["fail_stage"] = "connect"
            row["fail_reason"] = probe.get("error") or "密碼都不對"
            results.append(row)
            continue
        row["login_ok"] = True
        row["password_index"] = idx
        row["platform"] = probe["platform"]
        pw = passwords[idx]
        if probe["platform"] in VMWARE_PLATFORMS:
            # ★ 天條：VMware 設備（ESXi、vCenter）不可納管。探測已經認出來，不呼叫 onboard()、不改密碼
            row["fail_stage"] = ESXI_REFUSED_STAGE
            row["fail_reason"] = vmware_refusal(probe["platform"])
            results.append(row)
            continue
        if probe.get("refusal"):
            # ★ 平台白名單：不呼叫 onboard()、不改密碼
            row["fail_stage"] = PLATFORM_REFUSED_STAGE
            row["fail_reason"] = probe["refusal"]
            results.append(row)
            continue

        # 2) 佈金鑰（＝納管）
        r = onboard(host=ip, platform=probe["platform"] or "linux", username=username,
                    password=pw, collector_ip=collector_ip, pubkey=pubkey,
                    account=account_of(probe["platform"] or "linux"),
                    # 帳號備註：批次原本漏傳，會用預設值「webit3 唯讀收集」。
                    # 而批次正是一次建幾百個帳號的地方——那個備註存在的理由就是
                    # 「別人在那台看到這個帳號時知道是誰佈的」，全寫預設值等於
                    # 這個功能對最需要它的場景失效（2026-09-08 按下批次之前發現）。
                    comment=comment,
                    executor=onboard_executor, script=script)
        if not r.ok:
            row["fail_stage"] = r.stage
            row["fail_reason"] = r.message
            results.append(row)
            continue
        row["onboarded"] = True
        row["root_key"] = bool(getattr(r, "root_key", False))   # ROOTKEY1：腳本說 root 金鑰已佈

        # 3) 統一密碼：只有「登入用的不是第一組」才要改（用 A 進來的本來就是 A）
        if unify_password and idx != 0 and (probe["platform"] or "linux") == "linux":
            pr = change_root_password(ip, username, pw, passwords[0], collector_ip,
                                      executor=passwd_executor)
            row["password_unified"] = pr.ok
            if not pr.ok:
                # 納管已成功，只是統一密碼這步沒成——講清楚，不要蓋掉「已納管」
                row["fail_stage"] = "unify"
                row["fail_reason"] = f"已納管，但統一密碼失敗：{pr.message}"
        results.append(row)
    return results


def revoke(host: str, platform: str, username: str, password: str,
           pubkey: str | None = None, executor=None,
           account: str | None = None) -> OnboardResult:
    """把一台主機的收集帳號收回來。與 `onboard()` 對稱。

    ## 為什麼要有這支

    `webit3scan` 是持有全機隊金鑰的帳號。在此之前系統只有「怎麼佈出去」，
    沒有「怎麼收回來」——只有 playbook 註解裡一句話，沒有工具。
    稽核對特權帳號的標準問法是「怎麼建、怎麼撤、誰批准」，第三個答不出來。
    佈到幾百台之後才發現收不回來，是另一個等級的問題。

    ## 一樣需要 root

    `webit3scan` 沒有權限刪自己，也沒有權限動 `/etc/sudoers.d`。所以撤銷跟納管
    一樣要 root／sudo 帳密——不是按一下就好，畫面要照樣問。

    ## AIX／Windows 還沒做

    先只做 Linux。AIX 的移除指令不同（`rmuser` 不是 `userdel`），Windows 走
    `Remove-LocalUser`——各自要另外寫並且各自實測過才算數。硬套 Linux 那份
    在 AIX 上一定失敗，而失敗在中途會留下半套狀態（見 2026-08-16 的教訓）。
    這裡明確擋掉並講清楚，不要讓人以為按了有效。

    password 只在本函式與 executor 之間傳遞、用完即丟，不進回傳值、DB、log。
    """
    if platform not in ("linux", "aix"):
        return OnboardResult(
            False, "connect",
            f"取消納管目前只支援 Linux／AIX（這台是 {platform}）。"
            f"Windows 用 Remove-LocalUser，"
            f"還沒實作也還沒實測——請先手動移除該帳號的 authorized_keys（存取權立刻斷），"
            f"再視需要刪除帳號。")

    detected = {}
    if executor is None:
        detected = probe_target(host, username, password)
        uid, has_sudo = detected.get("uid"), detected.get("has_sudo")
        if uid is not None and uid != 0 and not has_sudo:
            return OnboardResult(
                False, "connect",
                f"登入帳號 {username} 不是 root，而且這台沒有 sudo——"
                f"取消納管需要 root 才能刪帳號與移除 sudoers。請改用 root 登入。")

    try:
        pubkey = pubkey or collector_pubkey()
        if platform == "aix":
            script = build_aix_revoke_script(pubkey, account or DEFAULT_ACCOUNT_AIX)
        else:
            script = build_linux_revoke_script(pubkey, account or DEFAULT_ACCOUNT)
    except ValueError as exc:
        return OnboardResult(False, "connect", str(exc))

    run = executor or _ssh_executor
    kw = {}
    if executor is None:
        kw["as_root"] = detected.get("uid") == 0
    # collector_ip 對撤銷沒有意義（不佈任何東西），但執行器的簽章共用，
    # 傳一個明確的佔位字串比傳空字串好認——出現在 log 裡看得懂是撤銷不是納管。
    return run(host=host, username=username, password=password, platform=platform,
               script=script, collector_ip="(revoke)", **kw)


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
