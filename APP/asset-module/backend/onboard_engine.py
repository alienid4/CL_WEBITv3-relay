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

#: 收集帳號在 /etc/passwd 的備註欄（GECOS）預設值。
#:
#: ⚠️ **實際值不寫在這裡**。使用者要填的是「員工編號-姓名_部門_系統」這種格式，
#: 裡面有員工編號與真實姓名——而這個檔案會進 relay（公開 repo）。寫死等於
#: 把個資推上公開程式庫，而且去識別化替換表會把姓名換掉，佈到主機上的會是
#: 被改過的字串。
#:
#: 所以走跟分類清單同一套：程式碼只留中性預設，實際值存 app_settings
#: （`onboard_account_comment`），由管理者在畫面上填一次。
DEFAULT_ACCOUNT_COMMENT = "webit3 唯讀收集"

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

    helper_path = account_collector.ACCOUNT_HELPER_PATH
    return f"""#!/usr/bin/env bash
set -uo pipefail
ACCOUNT="{account}"
PUBKEY='{pubkey}'
STAMP="$(date +%Y%m%d_%H%M%S)"
if [ "$(id -u)" -ne 0 ]; then echo "需要 root" >&2; exit 1; fi

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


#: 備註欄允許的字元。刻意收窄——這個值會被放進 `useradd -c "…"`，
#: 含雙引號、反引號、`$` 或換行就能改變整行指令的語意（那是命令注入）。
#: 中日韓文字、英數、`-_.@()／空白` 夠用了；使用者要的
#: 「員工編號-姓名_部門_系統」格式完全在範圍內。
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
            f"這個值會被放進 useradd -c，只接受中英數與 - _ . @ ( ) ／ 空白，"
            f"最多 120 字（引號、$、反引號會改變指令語意，一律拒絕）。")
    return val


def build_linux_script(pubkey: str, collector_ip: str, account: str = DEFAULT_ACCOUNT,
                       comment: str | None = None) -> str:
    """即時組出 Linux 納管腳本。內容只含公鑰，無任何機密。

    `comment` 是建帳號時寫進 /etc/passwd 的備註欄，讓在主機上看到這個帳號的人
    知道是誰佈的、為什麼。**不要在這裡寫死實際值**——見 DEFAULT_ACCOUNT_COMMENT。
    備註只允許安全字元（見 sanitize_account_comment），因為它會被放進
    `useradd -c "…"`，含引號或換行會改變指令語意。
    """
    import account_collector

    _c = sanitize_account_comment(comment)
    helper_path = account_collector.ACCOUNT_HELPER_PATH
    helper_script = account_collector.ACCOUNT_HELPER_SCRIPT
    return f"""#!/usr/bin/env bash
set -euo pipefail
ACCOUNT="{account}"
COLLECTOR_IP="{collector_ip}"
PUBKEY='{pubkey}'
HELPER="{helper_path}"
if [ "$(id -u)" -ne 0 ]; then echo "需要 root" >&2; exit 1; fi

# ---- 0. 動任何東西之前先備份，並產生還原腳本 ----
# 使用者 2026-09-11：「記得移動前就要備份，要有還原的，以後才可以還原」。
# 還原腳本逐檔記錄：原本有的 → 複製回去；原本沒有的 → 刪掉。所以還原後就是納管前的樣子。
BK="/var/backups/webit3/$(date +%Y%m%d_%H%M%S)"
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
if id "$ACCOUNT" >/dev/null 2>&1; then
  backup_one "$(getent passwd "$ACCOUNT" | cut -d: -f6)/.ssh/authorized_keys" authorized_keys.orig
else
  echo "echo '[!] 帳號 $ACCOUNT 是這次納管才建立的，還原不會刪帳號；要連帳號一起移除請用「取消納管」'" >> "$R"
fi
echo "echo '還原完成（備份在 $BK）'" >> "$R"
chmod 700 "$R"
echo "[+] 已備份現況到 $BK（還原：sudo bash $R）"

if ! id "$ACCOUNT" >/dev/null 2>&1; then
  useradd -m -s /bin/bash -c "{_c}" "$ACCOUNT"
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
echo "[*] 設定唯讀 sudo 白名單（機型序號＋帳號盤點輔助程式）"
if command -v sudo >/dev/null 2>&1 && [ -d /etc/sudoers.d ]; then
  # 輔助程式：內容固定、root 擁有、只有 root 能改。sudo 只准「不帶參數」執行它。
  # 先寫 .new 再 mv：寫到一半中斷不會留下半支程式。目錄是 root 的 755，一般帳號寫不進來。
  install -d -m 755 -o root -g root "$(dirname "$HELPER")"
  cat > "$HELPER.new" <<'WEBIT3_HELPER_EOF'
{helper_script}WEBIT3_HELPER_EOF
  chown root:root "$HELPER.new"
  chmod 755 "$HELPER.new"
  mv -f "$HELPER.new" "$HELPER"
  SUDOERS="/etc/sudoers.d/$ACCOUNT"
  # 先寫成帶「.」的暫存檔（sudo 會略過含「.」的檔名），驗證過才換上去——
  # 直接覆寫的話，寫壞的 sudoers 會在驗證前就生效。
  {{
    echo "$ACCOUNT ALL=(root) NOPASSWD: /usr/bin/cat /sys/class/dmi/id/*"
    echo "$ACCOUNT ALL=(root) NOPASSWD: $HELPER \\"\\""
  }} > "$SUDOERS.new"
  chmod 440 "$SUDOERS.new"
  if visudo -cf "$SUDOERS.new" >/dev/null; then
    mv -f "$SUDOERS.new" "$SUDOERS"
  else
    rm -f "$SUDOERS.new"
    bash "$R"
    echo "sudoers 驗證失敗，已還原成納管前的樣子" >&2
    exit 1
  fi
else
  echo "[!] {NO_SUDO_MARK} 這台沒有 sudo（或沒有 /etc/sudoers.d），跳過唯讀白名單"
  echo "[!] 後果：硬體序號收不到（product_serial 是 0400，只有 root 讀得到）"
  echo "[!]       帳號盤點需 root 的欄位（密碼效期、sudo 明細、金鑰數）也收不到"
  echo "[!]       機型、OS、帳號清單、服務這些照常收得到（product_name 是 0444）"
  echo "[!] 要補：裝 sudo 之後再跑一次納管，這支腳本可以重複執行"
fi
echo "完成。$COLLECTOR_IP 現在可以收集這台。"
echo "要還原成納管前的樣子：sudo bash $R"
"""


def build_aix_script(pubkey: str, collector_ip: str,
                     account: str = DEFAULT_ACCOUNT_AIX) -> str:
    """即時組出 AIX 納管腳本（ksh）。內容只含公鑰，無任何機密。

    ⚠️ 不是「Linux 腳本換個名字」——照 Linux 那份跑在 AIX 上一定失敗：

    - 建帳號是 `mkuser` 不是 `useradd`；鎖密碼是 `chuser account_locked=true`
      不是 `passwd -l`。
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
    # 備註欄跟腳本用同一個設定值：原本寫死英文，對已存在的帳號會把管理者填的備註蓋掉。
    # 放進 YAML 雙引號字串，所以雙引號與反斜線要再擋一次（sanitize 本來就不收這兩個）。
    _c = sanitize_account_comment(comment).replace("\\", "").replace('"', "")
    import account_collector

    helper_path = account_collector.ACCOUNT_HELPER_PATH
    helper_dir = helper_path.rsplit("/", 1)[0]
    # YAML 區塊字面值：每行縮排 10 格（content: | 底下）；空行保持空行
    helper_indented = "\n".join(
        ("          " + ln) if ln else "" for ln in account_collector.ACCOUNT_HELPER_SCRIPT.splitlines())
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

    - name: 唯讀 sudo 白名單（機型序號＋只准不帶參數執行輔助程式；不含 /etc/shadow）
      when: webit3_sudoers_dir.stat.isdir | default(false)
      ansible.builtin.copy:
        dest: "/etc/sudoers.d/{{{{ webit3_account }}}}"
        content: "{{{{ webit3_account }}}} ALL=(root) NOPASSWD: /usr/bin/cat /sys/class/dmi/id/*\\n{{{{ webit3_account }}}} ALL=(root) NOPASSWD: {helper_path} \\"\\"\\n"
        mode: "0440"
        backup: true
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


#: 帳號備註存在 app_settings 的哪個 key。實際值不進版控（含員工編號與姓名）。
ACCOUNT_COMMENT_SETTING = "onboard_account_comment"


def resolve_account_comment(conn) -> str:
    """讀管理者設定的帳號備註；沒設就用中性預設值。

    跟 `resolve_collector_ip()` 同一個模式：程式碼只留預設，實際值在 DB。
    """
    from db import get_setting

    return sanitize_account_comment(
        get_setting(conn, ACCOUNT_COMMENT_SETTING, None))


def build_script(platform: str, pubkey: str, collector_ip: str,
                 account: str | None = None, comment: str | None = None) -> str:
    # 在這裡擋而不是在各呼叫端：遠端納管、本機一行指令、排程自動納管、Ansible playbook
    # 全部經過這裡，擋一次就四條路都守到。
    validate_collector_ip(collector_ip)
    account = account or default_account_for(platform)
    if platform == "windows":
        return build_windows_script(pubkey, collector_ip, account)
    if platform == "linux":
        return build_linux_script(pubkey, collector_ip, account, comment)
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
                return {"ip": ip, "matched_password_index": idx, "platform": probe["os"],
                        "uid": probe.get("uid"), "has_sudo": probe.get("has_sudo"),
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
PROBE_COMMAND = ("uname -s; id -u; "
                 "command -v sudo >/dev/null 2>&1 && echo HASSUDO || echo NOSUDO")


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
        # 帳號對方只開 keyboard-interactive 時，paramiko 的密碼認證會自動退回用同一組
        # 密碼回答那一題（auth_password 的 fallback），不用另外處理。
        client.connect(host, port=SSH_PORT, username=username, password=password,
                       timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
                       allow_agent=False, look_for_keys=False)
    except BaseException:
        client.close()
        raise
    return client


def _ssh_error_text(host: str, exc: BaseException) -> str:
    """把連線階段的例外翻成 OpenSSH 的講法。

    為什麼要仿 OpenSSH 的字：classify_failure 靠這些字把失敗歸到 connect 階段、
    並給「查網路／埠／帳密」而不是「查 sudo」的建議。字對不上，連不到的機器就會
    被標成 execute，把人引去查一個根本不存在的 sudo 問題（2026-08-16 的教訓）。
    """
    import paramiko

    if isinstance(exc, paramiko.ssh_exception.NoValidConnectionsError):
        inner = next(iter(exc.errors.values()), None)
        if inner is not None:
            return _ssh_error_text(host, inner)
    if isinstance(exc, paramiko.BadHostKeyException):
        return (f"Host key verification failed：{host} 的主機金鑰跟 known_hosts 記錄的不同"
                f"（可能重灌過，也可能是中間人）")
    if isinstance(exc, paramiko.BadAuthenticationType):
        allowed = ", ".join(getattr(exc, "allowed_types", []) or [])
        return f"No supported authentication methods available（對方只接受：{allowed}）"
    if isinstance(exc, paramiko.AuthenticationException):
        return "Permission denied（帳號或密碼不對，或該帳號不允許 SSH 登入）"
    where = f"ssh: connect to host {host} port {SSH_PORT}"
    if isinstance(exc, socket.gaierror):
        return f"ssh: Could not resolve hostname {host}"
    if isinstance(exc, ConnectionRefusedError):
        return f"{where}: Connection refused"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return f"{where}: Connection timed out"
    if isinstance(exc, OSError) and exc.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH):
        return f"{where}: No route to host"
    if isinstance(exc, ImportError):
        return ("SSH 連線失敗：這台收集器沒有安裝 paramiko"
                "（requirements.txt 有列，重跑 deploy.sh；離線包要把它的 wheel 帶上）")
    return f"SSH 連線失敗：{type(exc).__name__}: {exc}"


#: 連線層失敗時回的結束碼——跟 OpenSSH 的 ssh 一樣用 255。
SSH_CONNECT_FAILED = 255


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
        # 不加 sudo：AIX 未必有；改成要求以 root 登入（UI 會講清楚）
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
    return OnboardResult(True, "execute", success_message(out), out[-800:])


def success_message(out: str) -> str:
    """納管成功時要回的那句話。**成功不一定是完整的成功。**

    沒有 sudo 的機器（Debian 最小安裝的預設）跳過了唯讀白名單，帳號與公鑰都佈好，
    但 `/sys/class/dmi/id/*` 是 0400 只有 root 讀得到，所以序號與機型收不到。
    如果這裡一律回「納管腳本執行完成」，那台的序號欄以後會是空的，而看到空白的人
    會去查收集是不是壞了——實際上是納管當下就決定了收不到，只是沒人講。
    """
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


def onboard(host: str, platform: str, username: str, password: str,
            collector_ip: str, pubkey: str | None = None,
            executor=None, account: str | None = None,
            comment: str | None = None) -> OnboardResult:
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
                       passwd_executor=None) -> list[dict]:
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

        # 2) 佈金鑰（＝納管）
        r = onboard(host=ip, platform=probe["platform"] or "linux", username=username,
                    password=pw, collector_ip=collector_ip, pubkey=pubkey,
                    account=account_of(probe["platform"] or "linux"),
                    # 帳號備註：批次原本漏傳，會用預設值「webit3 唯讀收集」。
                    # 而批次正是一次建幾百個帳號的地方——那個備註存在的理由就是
                    # 「別人在那台看到這個帳號時知道是誰佈的」，全寫預設值等於
                    # 這個功能對最需要它的場景失效（2026-09-08 按下批次之前發現）。
                    comment=comment,
                    executor=onboard_executor)
        if not r.ok:
            row["fail_stage"] = r.stage
            row["fail_reason"] = r.message
            results.append(row)
            continue
        row["onboarded"] = True

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
    if platform != "linux":
        return OnboardResult(
            False, "connect",
            f"取消納管目前只支援 Linux（這台是 {platform}）。"
            f"AIX 用 rmuser、Windows 用 Remove-LocalUser，指令不同，"
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
        script = build_linux_revoke_script(pubkey, account or DEFAULT_ACCOUNT)
    except ValueError as exc:
        return OnboardResult(False, "connect", str(exc))

    run = executor or _ssh_executor
    kw = {}
    if executor is None:
        kw["as_root"] = detected.get("uid") == 0
    # collector_ip 對撤銷沒有意義（不佈任何東西），但執行器的簽章共用，
    # 傳一個明確的佔位字串比傳空字串好認——出現在 log 裡看得懂是撤銷不是納管。
    return run(host=host, username=username, password=password, platform="linux",
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
