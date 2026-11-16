"""光碟（DVD／CD 掛載）不算空間告警（2026-10-29 使用者：「空間告警如果是 DVD 應該忽略」）。

光碟片塞滿是常態（唯讀、永遠 100%），算進檔案系統的剩餘％只會製造假告警、拉低整台機器的剩餘比例。
單一定義放這裡，收集端（df 解析）、5.2 容量預警、磁碟風險（1.3）、健康檢查共用——不各寫一份。

判斷依據（獨立審查 2026-10-29：名稱規則太寬會把真的塞滿的 /backup/iso 之類藏起來，所以改成「有裝置／檔案系統資訊就只信那個」）：
  · 有檔案系統類型或裝置（即時收集到的）：只看這兩個——iso9660／udf／cd9660／cdrfs，或 /dev/sr*、/dev/scd*、/dev/cd*、/dev/dvd*。
    名稱再像 DVD，只要裝置是一般磁碟（/dev/sdb1、LV）就**照常算**。
  · 只有掛載點名稱（以前收集進資料庫的舊資料沒留裝置）：只認「放在 /mnt、/media、/run/media 底下、最後一層就是
    dvd／cdrom／cd-rom／cdrw／sr0／RHEL-9-DVD 這種」的，或掛載點本身就是 /cdrom、/dvd。一般資料目錄不會被誤傷。
"""
from __future__ import annotations

import re

_FS = {"iso9660", "udf", "cd9660", "cdrfs"}
_DEV = re.compile(r"^/dev/(sr\d+|scd\d+|cd\d+|dvd\w*|cdrom\w*)$", re.I)
_MEDIA_DIRS = ("/mnt/", "/media/", "/run/media/")
_BASE = re.compile(r"^(dvd\d*|cd-?rom\d*|cdrw\d*|sr\d+|scd\d+|.*-dvd\d*)$", re.I)


def is_optical(mount: str | None, device: str | None = None, fs: str | None = None) -> bool:
    fs, device = (fs or "").strip().lower(), (device or "").strip()
    if fs or device:                    # 有即時資訊：只信檔案系統／裝置
        return fs in _FS or bool(_DEV.match(device))
    m = (mount or "").strip().rstrip("/")
    if not m:
        return False
    if m.lower() in ("/cdrom", "/dvd", "/cd-rom"):
        return True
    return m.startswith(_MEDIA_DIRS) and bool(_BASE.match(m.rsplit("/", 1)[-1]))
