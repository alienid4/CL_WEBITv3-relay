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

COLLECTOR_KEY_PUB = "/opt/webit3/.collector_key.pub"
DEFAULT_ACCOUNT = "webit3scan"


def collector_pubkey(path: str = COLLECTOR_KEY_PUB) -> str:
    """讀收集端公鑰。這是要塞進目標機的東西，不是機密（私鑰永遠留 221）。"""
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


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
HOME_DIR="$(getent passwd "$ACCOUNT" | cut -d: -f6)"
install -d -m 700 -o "$ACCOUNT" -g "$ACCOUNT" "$HOME_DIR/.ssh"
AUTH="$HOME_DIR/.ssh/authorized_keys"
LINE="from=\\"$COLLECTOR_IP\\",no-agent-forwarding,no-port-forwarding,no-X11-forwarding $PUBKEY"
grep -qF "$PUBKEY" "$AUTH" 2>/dev/null || echo "$LINE" >> "$AUTH"
chown "$ACCOUNT:$ACCOUNT" "$AUTH"; chmod 600 "$AUTH"
SUDOERS="/etc/sudoers.d/$ACCOUNT"
echo "$ACCOUNT ALL=(root) NOPASSWD: /usr/bin/cat /sys/class/dmi/id/*" > "$SUDOERS"
chmod 440 "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null || {{ rm -f "$SUDOERS"; echo "sudoers 錯"; exit 1; }}
echo "完成。$COLLECTOR_IP 現在可以收集這台。"
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


def build_script(platform: str, pubkey: str, collector_ip: str,
                 account: str = DEFAULT_ACCOUNT) -> str:
    if platform == "windows":
        return build_windows_script(pubkey, collector_ip, account)
    if platform == "linux":
        return build_linux_script(pubkey, collector_ip, account)
    raise ValueError(f"未支援的平台：{platform}（只支援 linux／windows）")


# ===== 執行器：真正碰網路的部分，抽成可注入 =====

@dataclass
class OnboardResult:
    ok: bool
    stage: str          # connect / execute / verify
    message: str
    output: str = ""


def _sshpass_executor(host: str, username: str, password: str, platform: str,
                      script: str, collector_ip: str, timeout: int = 40) -> OnboardResult:
    """用 sshpass 以密碼登入目標，執行納管腳本。

    密碼只透過 SSHPASS 環境變數給 sshpass 這個短命子行程——
    不進 argv（`ps` 看不到）、不寫任何檔案。函式回傳後 env 副本即消失。

    ⚠️ 未在真機驗證（家裡不能碰目標密碼、也不該由 AI 處理密碼）——這段的
    SSH/sudo 管線要靠 UI 觸發、對真機驗。已知前提與待驗點：
    - **Linux 假設登入帳號能 sudo 建帳號**（維運帳號常見 NOPASSWD sudo，或本身是 root）。
      若 sudo 另需密碼，這裡會失敗——那要改成 paramiko 互動式 sudo，屬 on-target 再處理。
      （原本想用 `sudo -S` 從 stdin 餵密碼，但 stdin 已被 base64 管線佔住，餵不進去。）
    - Windows 假設登入帳號是系統管理員、且已有 OpenSSH Server。
    """
    b64 = base64.b64encode(script.encode()).decode()
    env = dict(os.environ)
    env["SSHPASS"] = password
    try:
        if platform == "linux":
            inner = f"echo {b64} | base64 -d | sudo bash"
            cmd = ["sshpass", "-e", "ssh",
                   "-o", "StrictHostKeyChecking=no", "-o", f"ConnectTimeout={timeout}",
                   f"{username}@{host}", inner]
            proc = subprocess.run(cmd, capture_output=True,
                                  text=True, timeout=timeout + 30, env=env)
        else:  # windows
            inner = (f"powershell -NoProfile -Command "
                     f"\"[IO.File]::WriteAllText($env:TEMP+'\\wb.ps1',"
                     f"[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')),"
                     f"(New-Object Text.UTF8Encoding $true)); "
                     f"powershell -ExecutionPolicy Bypass -File $env:TEMP\\wb.ps1\"")
            cmd = ["sshpass", "-e", "ssh", "-o", "StrictHostKeyChecking=no",
                   "-o", f"ConnectTimeout={timeout}", f"{username}@{host}", inner]
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout + 30, env=env)
    except subprocess.SubprocessError as exc:
        return OnboardResult(False, "connect", f"連線／執行失敗：{exc}")
    finally:
        env.pop("SSHPASS", None)  # 明確清掉，不留在這層的 env 副本

    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 or "完成。" not in out:
        return OnboardResult(False, "execute", "腳本執行未回報完成", out[-800:])
    return OnboardResult(True, "execute", "納管腳本執行完成", out[-800:])


def onboard(host: str, platform: str, username: str, password: str,
            collector_ip: str, pubkey: str | None = None,
            executor=None, account: str = DEFAULT_ACCOUNT) -> OnboardResult:
    """把一台主機納管起來。

    ⚠️ password 只在本函式與 executor 之間傳遞、用完即丟——
    絕不寫進回傳值、DB、log。呼叫端（API）也必須遵守：收到就用、用完不留。
    executor 可注入，測試不碰真網路、不碰真密碼。
    """
    if platform not in ("linux", "windows"):
        return OnboardResult(False, "connect", f"未知平台：{platform}")
    pubkey = pubkey or collector_pubkey()
    script = build_script(platform, pubkey, collector_ip, account)
    run = executor or _sshpass_executor
    return run(host=host, username=username, password=password, platform=platform,
               script=script, collector_ip=collector_ip)


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
