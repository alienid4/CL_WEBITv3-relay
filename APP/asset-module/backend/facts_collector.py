"""③ facts 收集：進主機收「機器裡的事實」（真主機名/OS/序號/機型/MAC/VM）。

可插拔 per-platform、**純 SSH（不綁 Ansible，因為 AIX 不支援 Ansible，只能 SSH）**。
傳輸抽象成 runner(host, cmd)->str，測試可注入假 runner、正式用真 SSH。

現況（誠實）：
- Linux：指令集完整，能在 .221/.222 真測。
- AIX / Windows / SNMP：指令照文件寫好、**保留機制**，但家裡沒真機，未在真機驗證（標 tested=False）。
- 序號/機型多半要目標主機 root（dmidecode / /sys/class/dmi 唯讀 root）；收不到就留空，不假裝。
"""
from __future__ import annotations

# 每平台：欄位 -> 取值指令。收到後 _postprocess 再整形。
LINUX_CMDS = {
    "hostname": "hostname",
    "os": r"""(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") || uname -sr""",
    "mac": r"""cat /sys/class/net/$(ip route show default 2>/dev/null | awk '{print $5; exit}')/address 2>/dev/null""",
    # ⚠️ product_serial 權限是 0400（只有 root 讀得到），直接 cat 一定拿到空的——
    # 納管腳本已經替收集帳號開了「只能讀 /sys/class/dmi/id/*」的 sudo 規則，這裡要真的用它。
    # `sudo -n` 不會卡在密碼提示；沒有 sudo 權限時退回直接讀（product_name 通常可讀），
    # 兩條都失敗就留空，不假裝。
    "device_model": "sudo -n cat /sys/class/dmi/id/product_name 2>/dev/null "
                    "|| cat /sys/class/dmi/id/product_name 2>/dev/null",
    "hw_serial": "sudo -n cat /sys/class/dmi/id/product_serial 2>/dev/null "
                 "|| cat /sys/class/dmi/id/product_serial 2>/dev/null",
    "is_vm": "systemd-detect-virt 2>/dev/null",
}

AIX_CMDS = {  # 保留機制，未在真機驗證
    "hostname": "hostname",
    "os": "oslevel -s",
    "device_model": "uname -M",
    "hw_serial": "uname -u",             # AIX 系統序號；或 lsattr -El sys0
    "mac": "entstat -d en0 2>/dev/null | grep -i 'Hardware Address' | awk '{print $NF}'",
    "is_vm": "uname -L",                 # LPAR 資訊
}

WINDOWS_CMDS = {  # 保留機制，未在真機驗證（OpenSSH + PowerShell）
    "hostname": "powershell -NoProfile -Command hostname",
    "os": "powershell -NoProfile -Command \"(Get-CimInstance Win32_OperatingSystem).Caption\"",
    "device_model": "powershell -NoProfile -Command \"(Get-CimInstance Win32_ComputerSystem).Model\"",
    "hw_serial": "powershell -NoProfile -Command \"(Get-CimInstance Win32_BIOS).SerialNumber\"",
    "mac": "powershell -NoProfile -Command \"(Get-NetAdapter | ? Status -eq 'Up' | Select -First 1).MacAddress\"",
    "is_vm": "powershell -NoProfile -Command \"(Get-CimInstance Win32_ComputerSystem).Model\"",
}

PLATFORMS = {
    "linux": {"cmds": LINUX_CMDS, "tested": True},
    "aix": {"cmds": AIX_CMDS, "tested": False},
    "windows": {"cmds": WINDOWS_CMDS, "tested": False},
}


def detect_platform(open_ports: list[int] | None) -> str:
    """從掃描線索猜平台：3389=Windows；否則預設 Unix 類（Linux 指令，AIX 需連線設定明指）。"""
    ports = set(open_ports or [])
    if 3389 in ports:
        return "windows"
    return "linux"


def _postprocess(raw: dict) -> dict:
    out = {}
    for k, v in raw.items():
        v = (v or "").strip()
        out[k] = v or None
    if "is_vm" in out and out["is_vm"] is not None:
        # systemd-detect-virt: "none"=實體、其他=虛擬
        out["is_vm"] = 0 if out["is_vm"].lower() in ("none", "") else 1
    return out


def collect(runner, host: str, platform: str = "linux") -> dict:
    """對 host 收 facts。runner(host, cmd)->str 抽象 SSH（測試可注入）。
    收不到的欄位留 None，不假裝。回 dict 對應我們的 hardware 技術欄位。"""
    spec = PLATFORMS.get(platform)
    if not spec:
        raise ValueError(f"未支援的平台：{platform}")
    raw = {}
    for field, cmd in spec["cmds"].items():
        try:
            raw[field] = runner(host, cmd)
        except Exception:  # noqa: BLE001 - 單一指令失敗不整組掛掉，該欄留空
            raw[field] = None
    return _postprocess(raw)
