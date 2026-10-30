"""檔案傳送（Q94）的規則：全部是純函式，沒有網路、沒有資料庫，所以可以用「注入的時鐘」把每個邊界測透。

## 這個模組管什麼

1. 白名單資料夾：只准這幾個資料夾，其他一律拒絕（含 `..`、別的磁碟、UNC、符號連結式的路徑把戲）。
2. 時段與大小規則（伺服器時間 Asia/Taipei；平日＝週一到週五，不查國定假日，一律當交易日）：
     · ≤ 10 MB：任何時間
     · 10 MB < 大小 ≤ 100 MB：平日 08:30～15:00 不可傳
     · > 100 MB：平日只能 22:00～06:00
     · 週末：不限
3. 流量控制的估算與分波：預設同時 2 台、單台 5 MB/s、每波 10 台、平日 08:15 之後不再開新的一波。

## 為什麼規則在後端（不是只有畫面）

畫面上的「現在不能傳」只是提示；真正擋的是這裡。畫面被繞過、API 被直接呼叫，規則仍然成立。

## 台灣沒有日光節約時間

用固定的 UTC+8，不依賴 tzdata（Windows 開發機沒裝也能跑，結果和 Asia/Taipei 完全一樣）。
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass

TZ = _dt.timezone(_dt.timedelta(hours=8), "Asia/Taipei")

MB = 1024 * 1024
GB = 1024 * MB

SMALL_MAX = 10 * MB          # ≤ 這個大小：任何時間
MID_MAX = 100 * MB           # ≤ 這個大小（且 > SMALL_MAX）：平日開盤時間不可傳

MARKET_OPEN = _dt.time(8, 30)
MARKET_CLOSE = _dt.time(15, 0)
NIGHT_START = _dt.time(22, 0)
NIGHT_END = _dt.time(6, 0)
NO_NEW_WAVE_FROM = _dt.time(8, 15)      # 平日 08:15 起（到收盤）不再開新的一波

WAVE_SIZE = 10
DEFAULT_CONCURRENCY = 2
MAX_CONCURRENCY = 3
DEFAULT_RATE_MBPS = 5
ALLOWED_RATES_MBPS = (2, 5, 10)
BIG_BATCH_BYTES = 20 * GB    # 總量超過這個要二次確認
SPACE_FACTOR = 1.5           # 目的地可用空間至少要檔案大小的幾倍

_clock = lambda: _dt.datetime.now(TZ)  # noqa: E731 - 測試用 set_clock 換掉


def now() -> _dt.datetime:
    return _clock()


def set_clock(fn) -> None:
    """測試用：注入時鐘。傳 None 還原。"""
    global _clock
    _clock = fn if fn is not None else (lambda: _dt.datetime.now(TZ))


def at(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> _dt.datetime:
    """建一個 Asia/Taipei 的時間（測試和估算用）。"""
    return _dt.datetime(year, month, day, hour, minute, tzinfo=TZ)


def is_weekday(t: _dt.datetime) -> bool:
    return t.weekday() < 5          # 週一=0 … 週五=4


# ---------------------------------------------------------------- 白名單資料夾
WINDOWS_FOLDERS = ("D:\\Temp", "C:\\Windows\\Temp")
LINUX_FOLDERS = ("/tmp",)

#: 檔名：只允許一般檔名字元。不含路徑分隔、不含控制字元、不含 shell 特殊字元（單引號、$、`、; 等）。
#: 這個檔名會被放進遠端指令與路徑，所以寧可嚴格：不符就請使用者改名，不要想辦法跳脫。
_NAME_RE = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff._() \-+=,@#%\[\]]{0,127}$")
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

#: 我們自己的暫存後綴：傳輸中的檔案叫 <名稱><後綴>，驗過雜湊才改成正式名稱。
PART_SUFFIX = ".webit3part"


class PolicyError(ValueError):
    """規則不允許（訊息直接給人看）。"""


def folders_for(platform: str) -> tuple[str, ...]:
    return WINDOWS_FOLDERS if platform == "windows" else LINUX_FOLDERS


def default_folder(platform: str) -> str:
    return folders_for(platform)[0]


def check_folder(platform: str, folder: str) -> str:
    """只接受白名單裡「一字不差」的資料夾（Windows 不分大小寫、容許結尾的分隔符）。
    回標準寫法。任何看起來像在繞的東西（.. 、UNC、別的磁碟、相對路徑、符號連結語法）一律 PolicyError。"""
    raw = (folder or "").strip()
    if not raw:
        raise PolicyError("沒有指定資料夾")
    if "\x00" in raw or any(ord(c) < 32 for c in raw):
        raise PolicyError("資料夾含不合法字元")
    if platform == "windows":
        norm = raw.replace("/", "\\")
        if norm.startswith("\\\\"):
            raise PolicyError("不接受網路路徑（UNC）")
        if ".." in norm.split("\\"):
            raise PolicyError("路徑不可含 ..")
        norm = norm.rstrip("\\") if len(norm) > 3 else norm
        for ok in WINDOWS_FOLDERS:
            if norm.lower() == ok.lower():
                return ok
        raise PolicyError(f"只能使用白名單資料夾：{'、'.join(WINDOWS_FOLDERS)}")
    norm = raw
    if "\\" in norm:
        raise PolicyError("Linux／AIX 路徑不可含反斜線")
    if ".." in norm.split("/"):
        raise PolicyError("路徑不可含 ..")
    while len(norm) > 1 and norm.endswith("/"):
        norm = norm[:-1]
    if "//" in norm:
        raise PolicyError("路徑不可含重複的斜線")
    if norm in LINUX_FOLDERS:
        return norm
    raise PolicyError(f"只能使用白名單資料夾：{'、'.join(LINUX_FOLDERS)}")


def check_name(platform: str, name: str) -> str:
    """檔名檢查（上傳、抓回都用）。回原樣的名稱，不合法就 PolicyError。"""
    n = name or ""
    if n != n.strip() or not n:
        raise PolicyError("檔名不可為空，前後也不可有空白")
    if "/" in n or "\\" in n or n in (".", "..") or ":" in n:
        raise PolicyError("檔名不可含路徑或冒號")
    if not _NAME_RE.match(n):
        raise PolicyError("檔名含不允許的字元（只接受中英文、數字與 . _ - ( ) 空白 + = , @ # % [ ]）")
    if n.endswith(".") or n.endswith(" "):
        raise PolicyError("檔名不可以句點或空白結尾")
    if n.lower().endswith(PART_SUFFIX):
        raise PolicyError(f"檔名不可以 {PART_SUFFIX} 結尾（那是傳輸中暫存檔專用）")
    if platform == "windows" and n.split(".")[0].rstrip(" .").upper() in _WIN_RESERVED:
        raise PolicyError("Windows 保留字不能當檔名")
    return n


def remote_path(platform: str, folder: str, name: str) -> str:
    """組出遠端完整路徑（先過白名單與檔名檢查）。"""
    f = check_folder(platform, folder)
    n = check_name(platform, name)
    return f"{f}\\{n}" if platform == "windows" else f"{f}/{n}"


# ---------------------------------------------------------------- 時段與大小
@dataclass
class Verdict:
    ok: bool
    why: str
    next_at: _dt.datetime | None = None      # 不允許時，下一個允許的時間點


def size_band(size: int) -> str:
    if size <= SMALL_MAX:
        return "small"
    if size <= MID_MAX:
        return "mid"
    return "big"


def allowed_at(size: int, t: _dt.datetime) -> bool:
    """這個大小的檔案，在 t 這個時間點可不可以開始傳。"""
    band = size_band(size)
    if band == "small" or not is_weekday(t):
        return True
    tt = t.timetz().replace(tzinfo=None)
    if band == "mid":
        return not (MARKET_OPEN <= tt < MARKET_CLOSE)
    return tt >= NIGHT_START or tt < NIGHT_END          # big：平日只有夜間窗


def next_allowed(size: int, t: _dt.datetime) -> _dt.datetime:
    """t 之後（含）第一個允許開始的時間點。所有邊界都在整點或 30 分，所以用 30 分鐘為步長找。"""
    if allowed_at(size, t):
        return t
    cur = t.replace(second=0, microsecond=0)
    cur = cur + _dt.timedelta(minutes=30 - cur.minute % 30)
    for _ in range(8 * 48):
        if allowed_at(size, cur):
            return cur
        cur += _dt.timedelta(minutes=30)
    raise RuntimeError("找不到下一個允許時段（規則設定有誤）")


def verdict(size: int, t: _dt.datetime | None = None) -> Verdict:
    t = t or now()
    if allowed_at(size, t):
        band = size_band(size)
        why = {"small": "10MB 以下，不限時間"}.get(band)
        if not why:
            why = "週末，不限制" if not is_weekday(t) else (
                "現在是夜間窗（22:00～06:00）" if band == "big" else "現在不是開盤時間（08:30～15:00）")
        return Verdict(True, why)
    nxt = next_allowed(size, t)
    if size_band(size) == "big":
        why = "超過 100MB：平日只能在 22:00～06:00 傳"
    else:
        why = "超過 10MB：平日開盤時間（08:30～15:00）不能傳"
    return Verdict(False, why, nxt)


def may_start_new_wave(size: int, t: _dt.datetime | None = None) -> bool:
    """平日 08:15～15:00 不再開新的一波（已在傳的那一台會傳完）。

    這條只管「有大小限制的檔案」（> 10MB）：10MB 以下的檔案本來就任何時間都能傳、幾秒就完，
    再擋就和「10MB 以下不限時間」的規則自相矛盾。
    """
    t = t or now()
    if size_band(size) == "small" or not is_weekday(t):
        return True
    tt = t.timetz().replace(tzinfo=None)
    return not (NO_NEW_WAVE_FROM <= tt < MARKET_CLOSE)


def next_start(size: int, t: _dt.datetime) -> _dt.datetime:
    """t 之後（含）第一個「大小規則允許、而且可以開新的一波」的時間點。"""
    if allowed_at(size, t) and may_start_new_wave(size, t):
        return t
    cur = t.replace(second=0, microsecond=0)
    cur = cur + _dt.timedelta(minutes=30 - cur.minute % 30)
    for _ in range(8 * 48):
        if allowed_at(size, cur) and may_start_new_wave(size, cur):
            return cur
        cur += _dt.timedelta(minutes=30)
    raise RuntimeError("找不到下一個允許時段（規則設定有誤）")


# ---------------------------------------------------------------- 估算與分波
def clamp_concurrency(n) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return DEFAULT_CONCURRENCY
    return max(1, min(MAX_CONCURRENCY, n))


WINDOWS_DEFAULT_CONCURRENCY = 1      # Windows 這條傳輸沒辦法限速，所以預設一次只傳 1 台（頻寬不多）


def default_concurrency(box: str, requested=None) -> int:
    """沒指定時：Linux／AIX 2 台、Windows 1 台；有指定就夾在 1～3。"""
    if requested is None:
        return WINDOWS_DEFAULT_CONCURRENCY if box == "windows" else DEFAULT_CONCURRENCY
    return clamp_concurrency(requested)


def clamp_rate(n) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return DEFAULT_RATE_MBPS
    return n if n in ALLOWED_RATES_MBPS else DEFAULT_RATE_MBPS


def waves(items: list, size: int = WAVE_SIZE) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def estimate(size: int, hosts: int, rooms: int, concurrency: int, rate_mbps: int, wave_pause_s: int) -> dict:
    """傳送前給人看的預估。分鐘數是估算（頻寬、對方磁碟、WinRM 開銷都會變），畫面要標「估算，非實測」。"""
    total = size * hosts
    conc = clamp_concurrency(concurrency)
    rate = clamp_rate(rate_mbps)
    throughput = conc * rate * MB                      # bytes/s（合計）
    seconds = (total / throughput) if hosts else 0
    n_waves = len(waves(list(range(hosts))))
    seconds += max(0, n_waves - 1) * max(0, wave_pause_s)
    return {
        "total_bytes": total, "hosts": hosts, "rooms": rooms, "waves": n_waves,
        "concurrency": conc, "rate_mbps": rate, "throughput_mbps": conc * rate,
        "minutes": int(-(-seconds // 60)) if hosts else 0,
        "needs_second_confirm": total > BIG_BATCH_BYTES,
    }


def fmt_size(n: int) -> str:
    if n >= GB:
        return f"{n / GB:.1f} GB"
    if n >= MB:
        return f"{n / MB:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"
