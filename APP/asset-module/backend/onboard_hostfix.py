"""納管腳本的「主機端坑」：納管顯示成功、收集卻連不進來的那幾種，在腳本裡一次處理（決策 ONBOARD-FLOW1）。

使用者 2026-10-18 要的是**同一支腳本、測試機與正式機同一套做法**：正式機沒有 root 密碼，只能
PAM 借帳號登進去跑腳本，所以腳本要自己判斷身分、自己處理主機端會擋住收集的設定，而且每一個改動
都要留下紀錄。這裡產生的 shell 片段被 Linux（bash）與 AIX（ksh88）兩支腳本共用，所以
**只用兩邊都有的語法**：不用 sed -i、install、getent、process substitution。

| 編號 | 坑 | 做法 |
|---|---|---|
| A1–A4 | 誰在跑 | root 直接做；有 sudo 免密碼 → 自己 sudo 重跑；sudo 要密碼 → 讓 sudo 問一次；都不行 → 停，請改用 root |
| B1 | sshd AllowUsers／pam_access 擋住收集帳號 | 加一條**只限收集器 IP** 的最小允許（備份、sshd -t、驗證不過就還原）；DenyUsers／AllowGroups 不動、只警告 |
| B2 | 收集帳號密碼過期被鎖 | 設成服務帳號：不過期（密碼本來就鎖著，不能用密碼登入）——資安例外，見白名單文件 |
| B3 | SELinux 標籤讓 sshd 讀不到 authorized_keys | 有開 SELinux 才 restorecon |
| B5 | 誰在什麼時候改了什麼 | 每一行 [+]／[!]／[=] 同時寫進 /var/log/webit3_onboard.log（含執行者） |
"""
from __future__ import annotations

import re

#: 主機端紀錄檔（值班／稽核看這個）。
HOST_LOG = "/var/log/webit3_onboard.log"
#: 我們加進 sshd_config 的 AllowUsers 行的前一行標記（取消納管照這個刪）。
ALLOW_MARK = "# webit3-allowusers"
#: 我們加進 access.conf 的那一行的前一行標記。
ACCESS_MARK = "# webit3-access"
#: 停下來請人改用 root 時的離開碼（外層據此印「請改用 root 再跑一次」）。
NEED_ROOT_EXIT = 4
#: 停下來請人改用 root 時的訊息（classify_failure 認「需要 root」）。
NEED_ROOT_MSG = "需要 root：這台請改用 root 執行（借 root 帳號）"


def header_sh(shell: str, result_line: str | None = None) -> str:
    """身分判斷（A1–A4）。root → 往下；不是 root：能免密碼 sudo 就自己 sudo 重跑；sudo 要密碼就讓
    sudo 自己問一次（只在有終端機時）；沒有 sudo 或問失敗 → 停，請改用 root。

    A8：sudo 只放行部分指令（`sudo -l` 沒有 ALL）→ 也停下，請改用 root。

    重跑需要腳本是個檔案（`$0`）：一行下載版與 WinSCP 上傳版都是檔案；從 stdin 進來的版本
    （網頁一鍵納管）在進來之前就已經是 root（直接 root 登入，或由執行器用 sudo -S 提權），
    走不到這一段。"""
    return f"""if [ "$(id -u)" -ne 0 ]; then
  WHY=""
  if [ -f "$0" ] && which sudo >/dev/null 2>&1; then
    # 認證與「列權限」用同一次 sudo 呼叫：timestamp_timeout=0 的主機，sudo -v 之後再 sudo -n -l
    # 會因為沒有快取而失敗，把「有完整 sudo」誤判成「sudo 不完整」（2026-10-18 審查）。
    if sudo -n true 2>/dev/null; then
      SL="$(sudo -n -l 2>/dev/null)"
    elif [ -t 0 ]; then
      echo "[*] 不是 root：sudo 會問你的密碼（輸入錯誤或取消就會停下）"
      SL="$(sudo -l 2>/dev/null)"
    else
      SL=""
    fi
    if [ -z "$SL" ]; then
      WHY="sudo 認證沒過（或這台的 sudo 需要密碼但沒有終端機可以問）"
    elif printf '%s\\n' "$SL" | grep -Eq '\\((ALL|root)([[:space:]]*:[[:space:]]*ALL)?\\)[[:space:]]+(NOPASSWD:[[:space:]]*)?ALL[[:space:]]*$'; then
      echo "[*] 不是 root，但有完整 sudo：用 sudo 重新執行"
      if sudo -n true 2>/dev/null; then exec sudo -n {shell} "$0" "$@"; fi
      exec sudo {shell} "$0" "$@"
    else
      # A8：sudo 只放行幾條指令的話，用 sudo 跑整支腳本會在半路被擋——先停
      WHY="這個帳號的 sudo 不是完整權限（sudo -l 沒有 ALL），整支腳本會在半路被擋"
    fi
  else
    WHY="這台沒有 sudo（或這個版本是從貼上執行的，不能自己 sudo 重跑）"
  fi
  printf '%s\\n' "[!] $WHY" >&2
  echo "{NEED_ROOT_MSG}" >&2{chr(10) + '  echo "' + result_line + '"' if result_line else ''}
  exit {NEED_ROOT_EXIT}
fi
"""


def body_root_check_sh() -> str:
    """內層（Linux／AIX 那段）只檢查是不是 root：提權由外層 webit3_onboard.sh 或網頁執行器做，
    內層不碰 sudo（AIX 那段因此完全沒有 sudo 字樣）。"""
    return f"""if [ "$(id -u)" -ne 0 ]; then echo "{NEED_ROOT_MSG}" >&2; exit {NEED_ROOT_EXIT}; fi
"""


def log_sh() -> str:
    """say：印出來，同時附時間與執行者寫進主機端紀錄檔。紀錄檔寫不進去不擋納管。"""
    return f"""WLOG="{HOST_LOG}"
RUNBY="${{SUDO_USER:-${{LOGNAME:-$(id -un 2>/dev/null)}}}}"
touch "$WLOG" 2>/dev/null && chmod 600 "$WLOG" 2>/dev/null || true
say() {{ echo "$*"; echo "$(date '+%Y-%m-%d %H:%M:%S') [$RUNBY] $*" >> "$WLOG" 2>/dev/null || true; }}
# 外層（webit3_onboard.sh）把自己的 sha256 當第一個參數傳進來；直接跑這段時才自己算
SCRIPT_SHA="${{1:-}}"
if [ -z "$SCRIPT_SHA" ] && [ -f "$0" ]; then
  SCRIPT_SHA="$( {{ sha256sum "$0" 2>/dev/null || openssl dgst -sha256 "$0" 2>/dev/null || csum -h SHA256 "$0" 2>/dev/null || true; }} | awk '{{for(i=1;i<=NF;i++) if(length($i)==64){{print $i; exit}}}}')"
fi
say "[*] webit3 納管腳本 v$SCRIPT_VER 開始（sha256 ${{SCRIPT_SHA:-stdin}}；收集器 $COLLECTOR_IPS；帳號 $ACCOUNT）"
"""


_HEREDOC_START = re.compile(r"<<'?([A-Za-z0-9_]+)'?")


def with_log(script: str) -> str:
    """把腳本裡（heredoc 以外）以 `echo "[+]`／`[!]`／`[=]` 開頭的行改成 `say`，
    這樣每一個改動都會寫進主機端紀錄檔。heredoc 內容（輔助程式本體、sshd 片段）原樣不動。"""
    out, end = [], None
    for ln in script.split("\n"):
        if end is not None:
            out.append(ln)
            if ln.strip() == end:
                end = None
            continue
        m = _HEREDOC_START.search(ln)
        new = re.sub(r'^(\s*)echo "\[([+!=])\]', r'\1say "[\2]', ln)
        out.append(new)
        if m and "<<" in ln:
            end = m.group(1)
    return "\n".join(out)


def _sshd_detect_sh() -> str:
    return """SSHD_BIN=/usr/sbin/sshd
[ -x "$SSHD_BIN" ] || SSHD_BIN="$(which sshd 2>/dev/null || true)"
SSHD_CONF=/etc/ssh/sshd_config
"""


def access_fix_sh() -> str:
    """B1：sshd AllowUsers／pam_access。對 $ACCOUNT，以及 root 金鑰有佈成（ROOTKEY_OK=1）時的 root。

    * AllowUsers 有設、又沒有放行這個帳號 → 在第一個 Match 之前插一行 `AllowUsers <帳號>@<收集器IP>`
      （只限收集器位址），sshd -t 過、且 sshd -T 看得到新的那條才留，否則還原。
    * DenyUsers／DenyGroups 命中、或 AllowGroups 有設 → **不改**（那是主機管理者刻意的設定），只警告。
    * pam_access 有啟用且 access.conf 有規則 → 在檔案最前面加 `+ : <帳號> : <收集器IP>`（先命中先算）。
    """
    return _sshd_detect_sh() + f"""acl_get() {{ {{ "$SSHD_BIN" -T -C "user=$1,addr=$COLLECTOR_IP,host=webit3-check" 2>/dev/null || true; }} | awk -v k="$2" '$1==k{{$1="";print}}'; }}
acl_fix_user() {{
  U="$1"
  [ -n "$SSHD_BIN" ] && [ -x "$SSHD_BIN" ] && [ -f "$SSHD_CONF" ] || return 0
  AU="$(acl_get "$U" allowusers)"; AG="$(acl_get "$U" allowgroups)"
  DU="$(acl_get "$U" denyusers)"; DG="$(acl_get "$U" denygroups)"
  if [ -n "$DU" ] || [ -n "$DG" ]; then
    echo "[!] sshd 有 DenyUsers／DenyGroups（$DU $DG）——是主機管理者刻意的設定，不改；若擋到 $U 請人工確認"
  fi
  if [ -n "$AG" ]; then
    echo "[!] sshd 有 AllowGroups（$AG）——$U 不在這些群組的話會被擋；不改群組，請人工確認"
  fi
  if [ -n "$AU" ]; then
    HIT=0
    set -f      # $AU 裡可能有 *（萬用字元），不可以被 shell 展開成檔名
    for e in $AU; do
      case "$e" in "$U"|"$U@$COLLECTOR_IP"|"$U@*"|"*") HIT=1 ;; esac
    done
    set +f
    if [ "$HIT" = "0" ]; then
      STAMP_AC="$(date +%Y%m%d_%H%M%S)"
      cp -p "$SSHD_CONF" "$SSHD_CONF.webit3-$STAMP_AC"
      LINE="AllowUsers"
      for ip in $COLLECTOR_IPS; do LINE="$LINE $U@$ip"; done
      awk -v L="$LINE" -v M="{ALLOW_MARK}" 'BEGIN{{d=0}} !d && /^[ \\t]*[Mm][Aa][Tt][Cc][Hh][ \\t]/{{print M; print L; d=1}} {{print}} END{{if(!d){{print M; print L}}}}' "$SSHD_CONF" > "$SSHD_CONF.webit3.tmp" && cat "$SSHD_CONF.webit3.tmp" > "$SSHD_CONF"
      rm -f "$SSHD_CONF.webit3.tmp"
      NEW_AU="$(acl_get "$U" allowusers)"
      # sshd -T 把 AllowUsers 每一條各印一行：逐個收集器位址確認都看得到
      GOT=1
      for ip in $COLLECTOR_IPS; do
        printf '%s\\n' "$NEW_AU" | grep -qx " *$U@$ip" || GOT=0
      done
      if "$SSHD_BIN" -t && [ "$GOT" = "1" ]; then
        systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || service ssh reload 2>/dev/null || service sshd reload 2>/dev/null || refresh -s sshd 2>/dev/null || true
        echo "[+] sshd AllowUsers 原本沒有放行 $U：加了一條「$LINE」（只限收集器位址；備份 $SSHD_CONF.webit3-$STAMP_AC）"
      else
        cp -p "$SSHD_CONF.webit3-$STAMP_AC" "$SSHD_CONF"
        echo "[!] sshd AllowUsers 擋住 $U，自動加入沒通過驗證，已還原 sshd_config——請人工加「$LINE」"
      fi
    else
      echo "[=] sshd AllowUsers 已放行 $U"
    fi
  fi
}}
access_fix_user() {{
  U="$1"; AC=/etc/security/access.conf
  [ -f "$AC" ] || return 0
  grep -Eqs '^[[:space:]]*account[[:space:]].*pam_access' /etc/pam.d/sshd /etc/pam.d/password-auth /etc/pam.d/system-auth /etc/pam.d/common-account || return 0
  grep -Eq '^[[:space:]]*[-+]' "$AC" || return 0
  L="+ : $U : $COLLECTOR_IPS"
  if grep -qxF "$L" "$AC"; then echo "[=] pam_access 已放行 $U（只限收集器）"; return 0; fi
  STAMP_PA="$(date +%Y%m%d_%H%M%S)"
  cp -p "$AC" "$AC.webit3-$STAMP_PA"
  {{ echo "{ACCESS_MARK}"; echo "$L"; cat "$AC.webit3-$STAMP_PA"; }} > "$AC.webit3.tmp" && cat "$AC.webit3.tmp" > "$AC"
  rm -f "$AC.webit3.tmp"
  echo "[+] pam_access 有啟用：access.conf 最前面加了「$L」（只限收集器位址；備份 $AC.webit3-$STAMP_PA）"
}}
acl_fix_user "$ACCOUNT"
access_fix_user "$ACCOUNT"
if [ "${{ROOTKEY_OK:-0}}" = "1" ]; then
  acl_fix_user root
  access_fix_user root
fi
"""


def aix_root_rlogin_sh() -> str:
    """AIX：root 的 rlogin=false 會連 ssh 金鑰一起擋（loginrestrictions）。

    **只檢查、照實印，不自動改**：把 root 的 rlogin 打開是放寬整個 root 的遠端登入（不只 ssh、
    不只收集器），比 sshd 的 Match Address 寬得多，不該由腳本自己決定。要開請走變更單人工改。"""
    return """if [ "${ROOTKEY_OK:-0}" = "1" ] && which lsuser >/dev/null 2>&1; then
  RL="$(lsuser -a rlogin root 2>/dev/null | awk -F'rlogin=' '{print $2}')"
  if [ "$RL" = "false" ]; then
    echo "[!] AIX：root 的 rlogin=false，root 收集金鑰會被擋。腳本不自動放寬（那是整個 root 的遠端登入）；需要的話請走變更單人工改"
  else
    echo "[=] AIX：root rlogin=${RL:-預設}，root 收集金鑰可用"
  fi
fi
"""


def aging_sh(platform: str) -> str:
    """B2：收集帳號設成服務帳號（密碼不過期、不因閒置失效、帳號不到期）。密碼本來就鎖著。"""
    if platform == "aix":
        return """if chuser maxage=0 maxexpired=-1 expires=0 "$ACCOUNT" 2>/dev/null; then
  echo "[+] $ACCOUNT 設為服務帳號：密碼不過期、不到期（maxage=0 maxexpired=-1 expires=0；沒有可用密碼，只能用金鑰）"
else
  echo "[!] 設定 $ACCOUNT 密碼效期失敗（chuser），日後密碼到期可能被擋"
fi
"""
    return """if which chage >/dev/null 2>&1 && chage -M -1 -I -1 -E -1 "$ACCOUNT" 2>/dev/null; then
  echo "[+] $ACCOUNT 設為服務帳號：密碼不過期、不因閒置失效、帳號不到期（chage -M -1 -I -1 -E -1；密碼仍鎖著，只能用金鑰）"
else
  echo "[!] 設定 $ACCOUNT 密碼效期失敗（chage），日後密碼到期可能被擋"
fi
"""


def selinux_sh() -> str:
    """B3：SELinux 有開才修標籤（沒開或沒有 restorecon 就什麼都不做）。"""
    return """if which selinuxenabled >/dev/null 2>&1 && selinuxenabled 2>/dev/null && which restorecon >/dev/null 2>&1; then
  restorecon -R "$HOME_DIR/.ssh" 2>/dev/null || true
  if [ "${ROOTKEY_OK:-0}" = "1" ]; then restorecon -R "${RHOME}/.ssh" 2>/dev/null || true; fi
  echo "[+] SELinux 有開：已 restorecon 收集帳號（與 root 金鑰）的 .ssh"
fi
"""


def revoke_access_sh() -> str:
    """取消納管：拿掉我們加的 AllowUsers 行與 access.conf 行（照標記，各只刪我們那一行）。"""
    return _sshd_detect_sh() + f"""if [ -f "$SSHD_CONF" ] && grep -qxF "{ALLOW_MARK}" "$SSHD_CONF"; then
  cp -p "$SSHD_CONF" "$SSHD_CONF.webit3-revoke-acl-$STAMP"
  awk -v M="{ALLOW_MARK}" 'k{{k=0;next}} $0==M{{k=1;next}} {{print}}' "$SSHD_CONF" > "$SSHD_CONF.webit3.tmp" && cat "$SSHD_CONF.webit3.tmp" > "$SSHD_CONF"
  rm -f "$SSHD_CONF.webit3.tmp"
  if [ -n "$SSHD_BIN" ] && "$SSHD_BIN" -t; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || service ssh reload 2>/dev/null || service sshd reload 2>/dev/null || refresh -s sshd 2>/dev/null || true
    echo "[+] 已移除 sshd_config 裡我們加的 AllowUsers（備份 $SSHD_CONF.webit3-revoke-acl-$STAMP）"
  else
    cp -p "$SSHD_CONF.webit3-revoke-acl-$STAMP" "$SSHD_CONF"
    echo "[!] 移除 AllowUsers 後 sshd -t 不過，已還原"
  fi
fi
AC=/etc/security/access.conf
if [ -f "$AC" ] && grep -qxF "{ACCESS_MARK}" "$AC"; then
  cp -p "$AC" "$AC.webit3-revoke-$STAMP"
  awk -v M="{ACCESS_MARK}" 'k{{k=0;next}} $0==M{{k=1;next}} {{print}}' "$AC" > "$AC.webit3.tmp" && cat "$AC.webit3.tmp" > "$AC"
  rm -f "$AC.webit3.tmp"
  echo "[+] 已移除 access.conf 裡我們加的那一行（備份 $AC.webit3-revoke-$STAMP）"
fi
"""


# ===== B6：系統這端驗證失敗時，講得出是哪一種 =====

NET_FAIL = "網路不通（22 連不到）"
KEY_REJECTED = "金鑰被拒"
HOSTKEY_CHANGED = "主機金鑰不符"
KEY_REJECTED_HINTS = ("sshd 的 AllowUsers／AllowGroups 或 pam_access 沒放行收集帳號",
                      "authorized_keys 的 from= 跟收集器實際來源位址不同（多網卡／NAT）",
                      "SELinux 標籤讓 sshd 讀不到 authorized_keys",
                      "收集帳號被鎖或密碼到期（腳本會設成服務帳號，舊版腳本佈的可能沒有）",
                      "腳本沒跑完（看主機上 /var/log/webit3_onboard.log）")


def classify_collect_error(err: str | None) -> dict:
    """試連失敗的 stderr → {kind, text, hints}。**永遠不是只有「納管失敗」**（B6）。"""
    low = (err or "").lower()
    if any(k in low for k in ("timed out", "timeout", "逾時", "connection refused",
                              "no route to host", "network is unreachable", "could not resolve")):
        return {"kind": "network", "text": NET_FAIL,
                "hints": ["防火牆沒開收集器→這台的 22 埠", "這台沒開機或 sshd 沒在跑"]}
    if "host key" in low and ("changed" in low or "verification failed" in low):
        return {"kind": "hostkey", "text": HOSTKEY_CHANGED,
                "hints": ["這台重灌過或 IP 換了機器；確認後清掉收集器 known_hosts 那一筆"]}
    if "permission denied" in low or "publickey" in low or "authentication" in low:
        return {"kind": "key_rejected", "text": KEY_REJECTED, "hints": list(KEY_REJECTED_HINTS)}
    return {"kind": "other", "text": (err or "沒有錯誤訊息").strip()[:300], "hints": []}
