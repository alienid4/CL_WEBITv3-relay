"""軟體盤點採集：一台主機裝了哪些套件、各是什麼版本。

使用者 2026-09-11：「我想要看 SSH 是什麼版本、OpenSSH 是什麼版本、OpenSSL 是什麼版本，
還有裝過 7-Zip 啊、裝過 Office 365 啊」、「我只要 rpm -qa」、「Windows 也要，只是現在還沒測到」。

## 收什麼、不收什麼

| 平台 | 來源 | 需要 root？ | 驗證狀態 |
|---|---|---|---|
| Linux（RHEL 家族） | `rpm -qa` | ✗（rpm 資料庫一般帳號可讀） | 家中 Rocky／RHEL 實測 |
| Windows | 登錄檔 Uninstall 機碼（＝「程式和功能」清單） | ✗（HKLM 一般帳號可讀） | **未在真機驗證** |

**看不到的**（畫面上要講，不能讓人以為「沒有」）：
- Linux：手動解壓到 /opt 的、pip／npm 裝的、容器裡的。
- Windows：只裝給單一使用者的（HKCU）、免安裝版（例如綠色版 7-Zip）。
- Debian／Ubuntu 沒有 rpm → 標「不支援」，**不當成沒裝軟體**。

## 為什麼兩個平台輸出同一種格式

`PKG<TAB>名稱<TAB>版本<TAB>架構<TAB>安裝時間<TAB>廠商`，一行一個套件。
兩邊共用同一支解析器，才不會 Linux 修了 bug、Windows 那條還留著舊行為
（服務盤點 SERVICES_PS 對齊 ss 輸出也是同一個理由）。

## 為什麼第一行一定印 SRC=

「rpm 不存在」「rpm 在但什麼都沒吐」「收到 800 個套件」三種情況對寫入層的意義完全不同：
第一種是不支援、第二種是收集失敗（rpm 機器不可能零套件）、第三種才是資料。
沒有 SRC= 行的輸出一律當失敗——那代表指令根本沒跑起來（SSH 斷掉、shell 異常）。
"""
from __future__ import annotations

from datetime import datetime

# LC_ALL=C：安裝時間我們自己用 epoch 轉，不靠 rpm 的在地化日期字串。
# gpg-pubkey 不是軟體，是 rpm 匯入的簽章金鑰，排除掉免得混進清單。
LINUX_CMD = (
    "if command -v rpm >/dev/null 2>&1; then echo 'SRC=rpm'; "
    "LC_ALL=C rpm -qa --qf 'PKG\\t%{NAME}\\t%{VERSION}-%{RELEASE}\\t%{ARCH}\\t%{INSTALLTIME}\\t%{VENDOR}\\n' "
    "2>/dev/null | grep -v '^PKG\tgpg-pubkey\t'; "
    "else echo 'SRC=none'; fi"
)

# 「程式和功能」清單的來源：64 位元與 32 位元（WOW6432Node）兩個 Uninstall 機碼都要讀，
# 只讀一個會漏掉一半——7-Zip 常見 32 位元版就在 WOW6432Node 底下。
# 過濾掉 SystemComponent（系統元件）與 ParentKeyName（更新套件掛在主程式底下），
# 那是「控制台 → 程式和功能」本身也不顯示的東西，列出來只會讓清單長三倍。
# 刻意不用 Win32_Product：查詢它會觸發 MSI 對每個已安裝程式做一致性檢查（可能自動修復），
# 那是會改動目標機的副作用，不是唯讀查詢。
# 未在真機驗證（家中沒有可收集的 Windows）。
WINDOWS_PS = r"""
$ErrorActionPreference='SilentlyContinue'
"SRC=registry"
$paths = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
           'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')
Get-ItemProperty -Path $paths | Where-Object {
  $_.DisplayName -and -not $_.SystemComponent -and -not $_.ParentKeyName
} | ForEach-Object {
  $arch = if ($_.PSPath -match 'WOW6432Node') { 'x86' } else { 'x64' }
  "PKG`t$($_.DisplayName)`t$($_.DisplayVersion)`t$arch`t$($_.InstallDate)`t$($_.Publisher)"
}
"""


def _install_time(raw: str) -> str | None:
    """安裝時間正規化成本地時間字串。

    rpm 給 epoch 秒數；Windows 登錄檔給 yyyymmdd（常常是空的）。
    認不得的格式留 None——「不知道哪天裝的」不能被寫成某個猜出來的日期。
    """
    s = (raw or "").strip()
    if not s.isdigit():
        return None
    if len(s) == 8:                       # Windows：20240115
        try:
            return datetime.strptime(s, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            return None
    if len(s) >= 9:                       # rpm：epoch 秒
        try:
            return datetime.fromtimestamp(int(s)).strftime("%Y-%m-%d %H:%M:%S")
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _clean(v: str) -> str | None:
    v = (v or "").strip()
    return None if v in ("", "(none)") else v


def parse_packages(text: str) -> tuple[str | None, list[dict]]:
    """採集輸出 → (來源, 套件清單)。來源 None ＝ 指令沒跑起來（見檔頭）。

    同一個 (名稱, 架構, 版本) 只留一筆：Windows 的 64／32 位元機碼偶爾會重複登記同一個程式。
    """
    source: str | None = None
    out: list[dict] = []
    seen: set[tuple] = set()
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r\n")
        if line.startswith("SRC="):
            source = line[4:].strip() or None
            continue
        if not line.startswith("PKG\t"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        parts += [""] * (6 - len(parts))
        name = _clean(parts[1])
        if not name:
            continue
        pkg = {
            "name": name,
            "version": _clean(parts[2]) or "",
            "arch": _clean(parts[3]) or "",
            "installed_at": _install_time(parts[4]),
            "vendor": _clean(parts[5]),
        }
        key = (pkg["name"], pkg["arch"], pkg["version"])
        if key in seen:
            continue
        seen.add(key)
        out.append(pkg)
    return source, out


def collect(runner, host: str) -> dict:
    """Linux：用 SSH runner 跑 rpm -qa。回傳 {source, packages}；判定交給寫入層。"""
    source, packages = parse_packages(runner(host, LINUX_CMD))
    return {"source": source, "packages": packages}
