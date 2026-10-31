"""沒有可用 sudo 的 Linux 與 AIX：收集端用一把**專用、受限**的 root 金鑰收集（決策 ROOTKEY1，暫時，待收斂）。

## 為什麼有這個例外（使用者 2026-10-18 拍板，風險已講過一次）

Debian 最小安裝這類主機**沒有 sudo**。收集帳號 webit3sc 只拿得到一般帳號看得到的東西，
OS 重建資料、組態檢核、帳號盤點、硬體序號裡需要 root 的那幾項永遠是「權限不足」。
使用者：「第一次先幫我建立 key，之後這一台就可以跟 Debian 做 root 的 key-based 免密碼登入，
以後再慢慢收縮權限。」→ 收斂計畫見 backlog ROOTKEY-SHRINK。

## 風險（照實寫，給資安看）

* root 金鑰登入**繞過 PAM 借帳號流程**——收集端拿這把金鑰不需要任何人核准就是 root。
* 收集端私鑰外洩 ＝ 所有「root 金鑰（暫時）」主機被完全控制。

## 已經做的收斂（小總管定，不是選項）

1. **只對沒有可用 sudo 的 Linux（主機上自己偵測，不看發行版）與 AIX**（2026-10-18 擴大；AIX 本來就
   不佈 sudoers）。有 sudo 的 Linux 照舊走 webit3sc＋固定 sudoers；Windows、VMware（ESXi／vCenter，
   天條）一律不做。使用者 2026-10-18 在被告知會違反「禁止 root 遠端登入」、SOC 會看到每日 root 登入後選 A。
2. **專用金鑰**：跟 webit3sc 那把分開（RSA 3072，FIPS／舊 AIX OpenSSH 相容），私鑰 0600、只給跑服務的帳號；
   路徑 `WEBIT3_ROOT_COLLECT_KEY`（預設跟收集金鑰同目錄的 .root_collect_key）。
   AIX root 的 rlogin=false 只檢查、照實印，不自動放寬。
3. **authorized_keys 帶限制**：`from="<收集器 IP>"`、no-port-forwarding、no-agent-forwarding、
   no-X11-forwarding、**no-pty**、no-user-rc，註解 `webit3-root-collect` 方便找、方便刪。
   收集全部走非互動指令（ssh host 'cmd'，沒有 -t），不需要 pty。
4. **sshd 不動全域設定**：全域 PermitRootLogin 原樣不改（改成 prohibit-password 會讓
   /root/.ssh/authorized_keys 裡**任何**一把金鑰從**任何**位址都能登 root）。
   只在 sshd_config **最後面**加一段 `Match Address <收集器 IP>`（PermitRootLogin
   prohibit-password＋PasswordAuthentication no）。加之前備份、加完 `sshd -t` 驗語法，
   再用 `sshd -T -C` 證明「收集器位址可用金鑰登 root、其他位址維持原樣」，任一不符就還原。
   全域本來就允許 root 金鑰登入（yes／prohibit-password）就完全不碰 sshd。
   為什麼不放 sshd_config.d：Debian 的 Include 在主檔**最上面**，舊版 sshd 裡 include 檔的
   Match 會吃掉主檔後面的全域設定；放主檔最後面是唯一不會改到別人設定的位置。
5. 收集時用**跟 sudo 時同一批固定指令**（見 RootKeyRunner）。
6. 取消納管：照註解刪掉那一行、照標記刪掉那段 Match。
"""
from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from pathlib import Path

#: 私鑰位置（公鑰是同名 .pub）。可用環境變數覆蓋——安裝路徑不一定是 /opt/webit3。
ROOT_KEY_PATH_ENV = "WEBIT3_ROOT_COLLECT_KEY"
ROOT_KEY_DEFAULT = "/opt/webit3/.root_collect_key"
#: authorized_keys 那一行的註解，也是取消納管時找那一行的依據。
ROOT_KEY_COMMENT = "webit3-root-collect"
#: 收集時代表「用 root 金鑰」的帳號名。只會從 hardware.root_key_at 推出來，不吃全域設定。
ROOT_ACCOUNT = "root"
#: 納管腳本輸出裡的標記：root 金鑰已佈（系統據此記「root 金鑰（暫時）」）。
ROOT_KEY_MARK = "WEBIT3_ROOT_KEY_SET"
#: 沒佈成（sshd 驗證不過、收集器位址不是 IP…）——照實說，不假裝成功。
ROOT_KEY_SKIP_MARK = "WEBIT3_ROOT_KEY_SKIPPED"
#: sshd_config 裡我們那段 Match 的起訖標記（取消納管照這個刪）。
SSHD_BEGIN = "# BEGIN webit3-root-collect"
SSHD_END = "# END webit3-root-collect"
#: 用來證明「別的位址不受影響」的位址：RFC 5737 文件專用，不會是真主機。
OTHER_ADDR = "192.0.2.1"
#: authorized_keys 的限制選項。順序固定，測試會逐項檢查。
KEY_OPTIONS = ("no-port-forwarding", "no-agent-forwarding", "no-X11-forwarding",
               "no-pty", "no-user-rc")
#: 只有這些平台會考慮 root 金鑰（其餘一律不做）。
ELIGIBLE_PLATFORMS = ("linux", "aix")   # AIX 2026-10-18 納入（AIX 本來就不佈 sudoers）
#: 新金鑰一律 RSA 3072：FIPS 模式主機與舊版 AIX OpenSSH 不收 ed25519（2026-10-18 審查）。
KEY_BITS = 3072


def key_path() -> str:
    """root 收集私鑰路徑：環境變數優先，否則跟 webit3sc 收集金鑰放同一個目錄
    （安裝路徑可改，兩把放一起才不會各說各話）。"""
    env = os.environ.get(ROOT_KEY_PATH_ENV)
    if env:
        return env
    try:
        import onboard_engine

        return os.path.join(os.path.dirname(onboard_engine.COLLECTOR_KEY_PUB[:-4]),
                            ".root_collect_key")
    except Exception:  # noqa: BLE001
        return ROOT_KEY_DEFAULT


def ensure_root_key(path: str | None = None) -> bool:
    """沒有就產一把（RSA 3072，不加密語）。回 True 代表這次新產。**絕不覆蓋既有的**——
    換掉會讓所有 root 金鑰主機當場收不到（跟 onboard_engine.ensure_collector_key 同一條底線）。"""
    kp = Path(path or key_path())
    pp = Path(str(kp) + ".pub")
    if kp.exists() or pp.exists():
        return False
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    kp.parent.mkdir(parents=True, exist_ok=True)
    priv = rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)
    # 先建空檔、設 0600 再寫內容：私鑰不會有一瞬間是別人讀得到的權限
    fd = os.open(str(kp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption()))
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH).decode()
    pp.write_text(f"{pub} {ROOT_KEY_COMMENT}\n", encoding="utf-8")
    try:
        os.chmod(kp, 0o600)
        os.chmod(pp, 0o644)
    except OSError:      # Windows 開發機沒有 POSIX 權限
        pass
    try:
        import collector_key

        collector_key._chown_to_service_user(kp, pp)   # 只給跑服務的帳號
    except Exception:  # noqa: BLE001 - chown 不成也不影響金鑰本身；讀不到時收集會講出來
        pass
    return True


def read_pubkey(path: str | None = None) -> str | None:
    """讀既有的 root 收集公鑰（**不產生**）；沒有回 None。產腳本走這支，避免測試或預覽到處產金鑰。"""
    try:
        return root_pubkey(path, generate=False)
    except ValueError:
        return None


def root_pubkey(path: str | None = None, generate: bool = True) -> str:
    """公鑰的「類型＋本體」兩段（註解由我們自己加，固定是 ROOT_KEY_COMMENT）。"""
    kp = path or key_path()
    if generate:
        try:
            ensure_root_key(kp)
        except Exception:  # noqa: BLE001 - 產不出來讓下面讀取失敗，訊息比較具體
            pass
    try:
        parts = Path(kp + ".pub").read_text(encoding="utf-8").split()
    except OSError as exc:
        raise ValueError(f"讀不到 root 收集公鑰（{kp}.pub）：{exc}") from exc
    if len(parts) < 2:
        raise ValueError(f"root 收集公鑰格式不對（{kp}.pub）")
    return f"{parts[0]} {parts[1]}"


def is_ip_literal(value: str) -> bool:
    """`Match Address` 與 `from=` 只收 IP；收集器位址是 DNS 名時不佈 root 金鑰（不猜）。"""
    try:
        ipaddress.ip_address((value or "").strip())
        return True
    except ValueError:
        return False


def _ips(collector_ips) -> list[str]:
    return [collector_ips] if isinstance(collector_ips, str) else list(collector_ips)


def authorized_keys_line(pubkey: str, collector_ips) -> str:
    """root 的 authorized_keys 那一行（限制選項＋來源位址（主＋備援）＋辨識註解）。"""
    opts = f'from="{",".join(_ips(collector_ips))}",' + ",".join(KEY_OPTIONS)
    return f"{opts} {pubkey} {ROOT_KEY_COMMENT}"


def sshd_match_block(collector_ips) -> str:
    """加在 sshd_config 最後面的那段。**只對「收集器位址＋root」**放行金鑰登入；全域不動。

    `User root` 是必要的（2026-10-18 審查）：只寫 Match Address 的話，PasswordAuthentication no
    會套到「從收集器位址來的所有帳號」——收集器若是跳板機或做 SNAT，其他人的密碼登入會被一起鎖掉。"""
    collector_ip = ",".join(_ips(collector_ips))
    return (f"{SSHD_BEGIN} (webit3 collector root key login; collector address only; removed on revoke)\n"
            f"Match Address {collector_ip} User root\n"
            f"    PermitRootLogin prohibit-password\n"
            f"    PasswordAuthentication no\n"
            f"{SSHD_END}\n")


def bootstrap_sh(pubkey: str, collector_ips) -> str:
    """root 收集金鑰那一段 shell。Linux（沒有可用 sudo 的分支）與 AIX 共用同一份，
    所以**只用 bash 與 AIX ksh88 都有的語法**：不用 sed -i、install、getent、command -v、
    systemctl 以外的發行版特有指令（reload 逐一試）。以 root 執行（腳本開頭已檢查）。

    順序：先確定 sshd 讀得到有效設定、authorized_keys 在預設位置 → 才加金鑰那一行 →
    再視需要加 Match 段並驗證 → 任何一步不成就把剛加的收回（不留一把用不到的 root 金鑰）。
    每個被改的檔案改之前都在原地留一份 .webit3-<時間> 備份。
    """
    line = authorized_keys_line(pubkey, collector_ips)
    block = sshd_match_block(collector_ips).rstrip("\n")
    strip_awk = (f"awk 'index($0,\"{SSHD_BEGIN}\")==1{{s=1}} !s{{print}} "
                 f"index($0,\"{SSHD_END}\")==1{{s=0}}'")
    return f"""# ==== ROOTKEY1（暫時，待收斂）：沒有可用的 sudo → 佈受限的 root 收集金鑰 ====
echo "[*] 佈署受限的 root 收集金鑰（只限收集器 $COLLECTOR_IPS、不可開終端機、不可轉發）"
STAMP_RK="$(date +%Y%m%d_%H%M%S)"
ROOT_LINE='{line}'
SSHD_BIN=/usr/sbin/sshd
[ -x "$SSHD_BIN" ] || SSHD_BIN="$(which sshd 2>/dev/null || true)"
SSHD_CONF=/etc/ssh/sshd_config
rootkey_eff() {{ {{ "$SSHD_BIN" -T -C "user=root,addr=$1,host=webit3-check" 2>/dev/null || true; }} | awk -v k="$2" '$1==k{{print $2}}'; }}
rootkey_pw() {{ {{ "$SSHD_BIN" -T -C "user=webit3chk,addr=$1,host=webit3-check" 2>/dev/null || true; }} | awk '$1=="passwordauthentication"{{print $2}}'; }}
rootkey_strip() {{ grep -vF '{ROOT_KEY_COMMENT}' "$1" > "$1.webit3.tmp" || true; cat "$1.webit3.tmp" > "$1"; rm -f "$1.webit3.tmp"; }}
ROOTKEY_OK=0
ROOTKEY_WHY=""
RHOME="$(awk -F: '$1=="root"{{print $6}}' /etc/passwd)"
[ -n "$RHOME" ] || RHOME=/root
if [ "$RHOME" = "/" ]; then RHOME=""; fi
RAK="$RHOME/.ssh/authorized_keys"
BEFORE_COL=""
BEFORE_OTHER=""
BEFORE_PW=""
AKF=""
if [ -n "$SSHD_BIN" ] && [ -x "$SSHD_BIN" ] && [ -f "$SSHD_CONF" ]; then
  BEFORE_COL="$(rootkey_eff "$COLLECTOR_IP" permitrootlogin)"
  BEFORE_OTHER="$(rootkey_eff {OTHER_ADDR} permitrootlogin)"
  BEFORE_PW="$(rootkey_pw "$COLLECTOR_IP")"
  AKF="$(rootkey_eff "$COLLECTOR_IP" authorizedkeysfile)"
fi
if [ -z "$BEFORE_COL" ]; then
  ROOTKEY_WHY="讀不到 sshd 有效設定（這台的 sshd 不支援 sshd -T -C，或找不到 sshd／sshd_config）——不改 sshd、不佈 root 金鑰"
elif [ -n "$AKF" ] && [ "$AKF" != ".ssh/authorized_keys" ] && [ "$AKF" != "%h/.ssh/authorized_keys" ]; then
  ROOTKEY_WHY="這台 sshd 的 AuthorizedKeysFile 不是預設（$AKF），不猜位置——不佈 root 金鑰"
else
  mkdir -p "$RHOME/.ssh"; chmod 700 "$RHOME/.ssh"
  touch "$RAK"
  cp -p "$RAK" "$RAK.webit3-$STAMP_RK"
  rootkey_strip "$RAK"
  echo "$ROOT_LINE" >> "$RAK"
  chown root "$RAK"; chmod 600 "$RAK"
  echo "[+] $RAK 已加入一行（註解 {ROOT_KEY_COMMENT}；備份：$RAK.webit3-$STAMP_RK）"
  case "$BEFORE_COL" in
    yes|without-password|prohibit-password)
      echo "[=] sshd 本來就允許 root 用金鑰登入（PermitRootLogin=$BEFORE_COL），不改 sshd 設定"
      ROOTKEY_OK=1 ;;
    *)
      cp -p "$SSHD_CONF" "$SSHD_CONF.webit3-$STAMP_RK"
      # 先拿掉舊的那段（重跑納管時），再加到檔案「最後面」——Match 會吃掉它後面的所有行，
      # 放最後才不會改到別人的全域設定。全域 PermitRootLogin 不動。
      if grep -qF '{SSHD_BEGIN}' "$SSHD_CONF" && ! grep -qF '{SSHD_END}' "$SSHD_CONF"; then
        ROOTKEY_WHY="sshd_config 裡有我們的開頭標記卻沒有結尾標記（被人改過），不自動處理——請人工檢查"
        AFTER_COL=""; AFTER_OTHER=""
      else
      {strip_awk} "$SSHD_CONF" > "$SSHD_CONF.webit3.tmp" && cat "$SSHD_CONF.webit3.tmp" > "$SSHD_CONF"
      rm -f "$SSHD_CONF.webit3.tmp"
      # 檔案最後一行沒有換行的話，接上去會黏成同一行
      if [ -s "$SSHD_CONF" ] && [ -n "$(tail -c 1 "$SSHD_CONF")" ]; then echo >> "$SSHD_CONF"; fi
      cat >> "$SSHD_CONF" <<'WEBIT3_SSHD_EOF'
{block}
WEBIT3_SSHD_EOF
      AFTER_COL="$(rootkey_eff "$COLLECTOR_IP" permitrootlogin)"
      AFTER_OTHER="$(rootkey_eff {OTHER_ADDR} permitrootlogin)"
      AFTER_PW="$(rootkey_pw "$COLLECTOR_IP")"
      # 三件事都要成立才留：語法過（sshd -t）、收集器位址的 root 只能用金鑰、
      # 其他位址的 root 與收集器位址的「其他帳號」（密碼登入）都跟原本一樣
      if "$SSHD_BIN" -t && {{ [ "$AFTER_COL" = "without-password" ] || [ "$AFTER_COL" = "prohibit-password" ]; }} && [ "$AFTER_OTHER" = "$BEFORE_OTHER" ] && [ "$AFTER_PW" = "$BEFORE_PW" ]; then
        if systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || service ssh reload 2>/dev/null || service sshd reload 2>/dev/null || refresh -s sshd 2>/dev/null; then
          echo "[+] sshd 已重新載入"
        else
          echo "[!] sshd 重新載入失敗——設定已寫入，sshd 下次重啟才生效（系統試連 root 金鑰會照實回報）"
        fi
        echo "[+] sshd_config 最後面加了一段 Match Address $COLLECTOR_FROM User root（PermitRootLogin prohibit-password、PasswordAuthentication no）"
        echo "[+] 全域 PermitRootLogin 未改（其他位址仍是 $BEFORE_OTHER）；備份：$SSHD_CONF.webit3-$STAMP_RK"
        ROOTKEY_OK=1
      else
        cp -p "$SSHD_CONF.webit3-$STAMP_RK" "$SSHD_CONF"
        ROOTKEY_WHY="sshd 驗證沒過（語法／收集器位址結果=$AFTER_COL／其他位址 $BEFORE_OTHER→$AFTER_OTHER／其他帳號密碼登入 $BEFORE_PW→$AFTER_PW；這台 sshd 可能不支援 Match Address），已還原 sshd_config"
      fi
      fi ;;
  esac
  if [ "$ROOTKEY_OK" != "1" ]; then
    rootkey_strip "$RAK"      # sshd 不放行就不要留一把用不到的 root 金鑰
  fi
fi
if [ "$ROOTKEY_OK" = "1" ]; then
  echo "[+] {ROOT_KEY_MARK} root 收集金鑰已佈（暫時，待收斂；取消納管會移除）"
else
  echo "[!] {ROOT_KEY_SKIP_MARK} root 收集金鑰沒有佈：$ROOTKEY_WHY"
  echo "[!] 需 root 的收集項目仍會是權限不足；全域 PermitRootLogin 沒有被放寬"
fi
"""


def bootstrap_skip_sh(reason: str) -> str:
    """收集器位址不是 IP 等原因不能佈 root 金鑰時，接在同一位置的那段（只印、不動任何東西）。"""
    return f'  echo "[!] {ROOT_KEY_SKIP_MARK} {reason}"\n'


def revoke_sh() -> str:
    """取消納管時移除 root 金鑰那一行與 sshd 那段 Match。**最先做**（先斷存取權）。
    Linux 與 AIX 共用（同樣只用兩邊都有的語法）。需要 $STAMP。"""
    strip_awk = (f"awk 'index($0,\"{SSHD_BEGIN}\")==1{{s=1}} !s{{print}} "
                 f"index($0,\"{SSHD_END}\")==1{{s=0}}'")
    return f"""# ---- 0. root 收集金鑰（ROOTKEY1）----
RHOME="$(awk -F: '$1=="root"{{print $6}}' /etc/passwd)"
[ -n "$RHOME" ] || RHOME=/root
if [ "$RHOME" = "/" ]; then RHOME=""; fi
RAK="$RHOME/.ssh/authorized_keys"
if [ -f "$RAK" ] && grep -qF '{ROOT_KEY_COMMENT}' "$RAK"; then
  cp -p "$RAK" "$RAK.webit3-revoke-$STAMP"
  grep -vF '{ROOT_KEY_COMMENT}' "$RAK" > "$RAK.webit3.tmp" || true
  cat "$RAK.webit3.tmp" > "$RAK"; rm -f "$RAK.webit3.tmp"
  echo "[+] 已移除 root 收集金鑰那一行（備份：$RAK.webit3-revoke-$STAMP）"
else
  echo "[=] root 沒有我們的收集金鑰"
fi
SSHD_CONF=/etc/ssh/sshd_config
if [ -f "$SSHD_CONF" ] && grep -qF '{SSHD_BEGIN}' "$SSHD_CONF" && ! grep -qF '{SSHD_END}' "$SSHD_CONF"; then
  echo "[!] sshd_config 有我們的開頭標記卻沒有結尾標記（被人改過）——不自動刪（避免刪到檔尾），請人工移除那段"
elif [ -f "$SSHD_CONF" ] && grep -qF '{SSHD_BEGIN}' "$SSHD_CONF"; then
  cp -p "$SSHD_CONF" "$SSHD_CONF.webit3-revoke-$STAMP"
  {strip_awk} "$SSHD_CONF" > "$SSHD_CONF.webit3.tmp" && cat "$SSHD_CONF.webit3.tmp" > "$SSHD_CONF"
  rm -f "$SSHD_CONF.webit3.tmp"
  SSHD_BIN=/usr/sbin/sshd
  [ -x "$SSHD_BIN" ] || SSHD_BIN="$(which sshd 2>/dev/null || true)"
  if [ -n "$SSHD_BIN" ] && "$SSHD_BIN" -t; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null \
      || service ssh reload 2>/dev/null || service sshd reload 2>/dev/null \
      || refresh -s sshd 2>/dev/null || true
    echo "[+] 已移除 sshd_config 裡的 Match Address 那段（備份：$SSHD_CONF.webit3-revoke-$STAMP）"
  else
    cp -p "$SSHD_CONF.webit3-revoke-$STAMP" "$SSHD_CONF"
    echo "[!] 移除那段後 sshd -t 不過，已還原 sshd_config；root 金鑰那一行已刪，收集端仍然進不來"
  fi
fi
"""


# ===== 主機標記 =====

def mark_host(conn, ip: str, on: bool) -> None:
    """記／清「這台用 root 金鑰收集」。寫到同一 IP 的全部登記（同一台）。"""
    from db import _now_local

    conn.execute("UPDATE hardware SET root_key_at = ? WHERE ip = ?",
                 (_now_local() if on else None, ip))


def is_root_host(conn, asset_serial: str | None) -> bool:
    if not asset_serial:
        return False
    try:
        row = conn.execute("SELECT root_key_at FROM hardware WHERE asset_serial = ?",
                           (asset_serial,)).fetchone()
    except Exception:  # noqa: BLE001 - 舊 DB 沒這欄
        return False
    return bool(row and row["root_key_at"])


def account_for(conn, asset_serial: str | None, platform: str = "linux") -> str:
    """**需要 root 的收集器**用這支選帳號：root 金鑰主機回 root，其餘照 account_for_host。

    只有 Linux 與 AIX（ELIGIBLE_PLATFORMS）會回 root；Windows／VMware 永遠不會，就算欄位被誤寫。
    """
    import collect_account_migration as cam

    if platform in ELIGIBLE_PLATFORMS and is_root_host(conn, asset_serial):
        return ROOT_ACCOUNT
    return cam.account_for_host(conn, asset_serial, platform)


def record_after_onboard(conn, ip: str, platform: str, script_said: bool, probe=None) -> bool:
    """網頁／批次／自動納管成功後：腳本說「root 金鑰已佈」**而且**真的用 root 金鑰登得進去，
    才記 root_key_at。只聽腳本的話，sshd 沒 reload 成功那種會被記成可用。回最後狀態。"""
    ok = False
    if script_said and platform in ELIGIBLE_PLATFORMS:
        try:
            ok = probe_root(ip, runner=probe)
        except Exception:  # noqa: BLE001
            ok = False
    if ok or not script_said:
        # 沒佈（有 sudo 的主機）要清掉舊標記：主機裝了 sudo 重新納管後，就回到 webit3sc＋sudoers
        mark_host(conn, ip, ok)
    return ok


def probe_root(host: str, timeout: int = 8, runner=None) -> bool:
    """用 root 金鑰試連一次（`id -u` 回 0 才算）。唯讀，不改任何東西。"""
    if runner is not None:
        return bool(runner(host))
    import manage_state

    kp = key_path()
    if not Path(kp).exists():
        return False
    import ssh_mux

    argv = ["ssh", "-i", kp, "-o", "BatchMode=yes", *manage_state._ssh_hostkey_opts(),
            *ssh_mux.mux_opts(),
            "-o", f"ConnectTimeout={timeout}", f"{ROOT_ACCOUNT}@{host}", "id -u"]
    try:
        r = ssh_mux.run(argv, host, timeout=timeout + 5)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and (r.stdout or "").strip() == "0"


# ===== 收集：跟 sudo 時同一批固定指令 =====

def sudo_specs() -> list[str]:
    """這台若有 sudo，webit3sc 會被放行的那份清單（唯一來源 onboard_engine）。"""
    import onboard_engine

    return onboard_engine.linux_sudo_specs()


def synthetic_sudo_list() -> str:
    """root 主機沒有 sudo，`sudo -n -l` 由收集端自己回答：答的就是 sudo 主機會拿到的同一份白名單。
    這樣 OS 重建收集照樣只送清單上的固定指令，不會因為是 root 就多送。"""
    lines = ["User root may run the following commands on this host:"]
    lines += [f"    (root) NOPASSWD: {s}" for s in sudo_specs()]
    return "\n".join(lines) + "\n"


def _split_specs(specs: list[str]) -> list[str]:
    out = []
    for spec in specs:
        out += [s.strip() for s in re.split(r"(?<!\\),\s*", spec) if s.strip()]
    return out


def allowed_binaries() -> set[str]:
    """`sudo -n <bin>` 裡 <bin> 可以照跑的集合：白名單裡每條指令的程式（含 basename，
    因為有些收集器寫 `sudo -n ss` 不帶路徑），加上不改任何東西的 true／test／grep。"""
    bins: set[str] = set()
    for s in _split_specs(sudo_specs()):
        b = s.split()[0]
        bins.add(b)
        bins.add(b.rsplit("/", 1)[-1])
    return bins | {"true", "test", "grep"}


#: 帶參數才放行的唯讀查詢（程式名 → 第一個參數必須是）。
_ALLOWED_WITH_ARG = {"passwd": "-S", "chage": "-l"}
_SUDO_SEG = re.compile(r"sudo -n (\S+)(?: (\S+))?")


def rewrite_for_root(cmd: str, allowed: set[str] | None = None) -> str:
    """把收集器的指令改成 root 直接跑的版本：`sudo -n X` 裡 X 在白名單 → 拿掉 `sudo -n `；
    不在 → 換成 `false X`（跟 sudo 拒絕一樣回非 0），**不會因為是 root 就多跑沒放行的東西**。
    內嵌腳本（heredoc）不改：那些腳本自己判 `id -u`，root 時走直接讀的分支。"""
    if "<<'" in cmd:
        return cmd
    allowed = allowed if allowed is not None else allowed_binaries()

    def sub(m: re.Match) -> str:
        b, arg = m.group(1), m.group(2)
        rest = f" {arg}" if arg is not None else ""
        if b in allowed or _ALLOWED_WITH_ARG.get(b.rsplit("/", 1)[-1]) == arg:
            return f"{b}{rest}"
        return f"false {b}{rest}"

    return _SUDO_SEG.sub(sub, cmd)


def make_runner(timeout: int = 10):
    """root 金鑰收集用的 runner（manage_state._ssh_runner 在 account=root 時呼叫）。"""
    import manage_state

    class RootKeyRunner(manage_state.SSHRunner):
        """跟 SSHRunner 一樣（記 trace、last），差在：用 root 專用金鑰、指令先過 rewrite_for_root、
        `sudo -n -l` 由收集端回答同一份白名單。"""

        def __init__(self):
            super().__init__(key_path(), account=ROOT_ACCOUNT, timeout=timeout)
            self._allowed = allowed_binaries()

        def __call__(self, host: str, cmd: str) -> str:
            # 收集端送的是 `LC_ALL=C sudo -n -l`（固定英文輸出）；舊字串也認，免得改版時漏接
            if cmd.strip() in ("sudo -n -l", "LC_ALL=C sudo -n -l"):
                self.last = {"rc": 0, "err": ""}
                return synthetic_sudo_list()
            return super().__call__(host, rewrite_for_root(cmd, self._allowed))

    return RootKeyRunner()
