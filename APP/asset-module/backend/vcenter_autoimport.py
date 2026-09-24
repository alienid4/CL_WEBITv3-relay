"""S19 VC 自動匯入（方案 B：檔案中繼）。

## 為什麼走「檔案落地、我去抓」而不是直連 vCenter（使用者 2026-07-20 定案）

直連 vCenter 要我們存一組權限帳號、能連到它、還要自己追它每次改版的 API 變化——
最會變的那塊擔子全揹在我們身上，斷了還可能悄悄斷。

改走中繼檔：一台常開的 Windows 排一個「每晚用 RVTools 匯出到共享資料夾」的工作，
我們**只負責去那個資料夾抓最新的檔**。追 VMware 改版的責任在 RVTools 身上（那是它的
工作），我們只依賴一份格式很穩的 Excel——把最會變的外包出去，只碰最穩的。
而且沿用今天做好的 rvtools_import，幾乎不用再寫解析。

## 這支負責什麼

- 監看設定的資料夾，挑**最新**一份 RVTools 匯出。
- 只在「比上次處理過的還新」時才匯（同一份不重複匯）。
- **半寫入保護**：檔案 mtime 太新（可能還在寫）先跳過，下一輪再看，避免讀到寫一半的檔。
- 鮮度燈：太久沒有新檔就示警——「今晚沒收到」要看得見，不能默默沒進來。

真正連 vCenter 的動作發生在那台 Windows 上（RVTools），這支完全不碰 vCenter、不存它的帳號。
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from db import get_connection, get_setting, set_setting

# app_settings 鍵
ENABLED_KEY = "vcenter_autoimport_enabled"       # 預設關
DIR_KEY = "vcenter_autoimport_dir"               # 監看資料夾
MAX_AGE_KEY = "vcenter_autoimport_max_age_hours"  # 超過多久沒新檔算逾時（黃燈）
LAST_FILE_KEY = "vcenter_autoimport_last_file"   # 上次處理過的檔名
LAST_SIG_KEY = "vcenter_autoimport_last_sig"     # 上次處理過的檔案簽章（名+mtime+size）
LAST_AT_KEY = "vcenter_autoimport_last_at"       # 上次成功匯入的本地時間
LAST_RESULT_KEY = "vcenter_autoimport_last_result"  # 上次匯入摘要（人看的一句話）

DEFAULT_MAX_AGE_HOURS = 36     # 每晚匯出，給一天多的寬限
STABLE_SECONDS = 120           # 檔案至少靜置這麼久才算寫完，避免抓到寫一半的


def get_config(conn) -> dict:
    return {
        "enabled": get_setting(conn, ENABLED_KEY, "0") == "1",
        "dir": get_setting(conn, DIR_KEY, "") or "",
        "max_age_hours": int(get_setting(conn, MAX_AGE_KEY, str(DEFAULT_MAX_AGE_HOURS))),
        "last_file": get_setting(conn, LAST_FILE_KEY, "") or "",
        "last_at": get_setting(conn, LAST_AT_KEY, "") or "",
        "last_result": get_setting(conn, LAST_RESULT_KEY, "") or "",
    }


def set_config(conn, enabled: bool, directory: str, max_age_hours: int) -> None:
    set_setting(conn, ENABLED_KEY, "1" if enabled else "0")
    set_setting(conn, DIR_KEY, (directory or "").strip())
    set_setting(conn, MAX_AGE_KEY, str(int(max_age_hours)))


def _sig(p: Path) -> str:
    """檔案簽章：名字＋mtime＋大小。任何一項變了就當成「新的一份」該重匯。"""
    st = p.stat()
    return f"{p.name}|{int(st.st_mtime)}|{st.st_size}"


def newest_export(directory: str, now: datetime | None = None,
                  stable_seconds: int = STABLE_SECONDS) -> Path | None:
    """資料夾裡最新、且已經寫完（靜置夠久）的 .xlsx。沒有回 None。

    只挑靜置超過 stable_seconds 的：正在被 RVTools 寫入的檔 mtime 會很新，
    這時抓會讀到寫一半的檔——寧可下一輪再抓，也不要匯進壞資料。
    """
    if not directory:
        return None
    d = Path(directory)
    if not d.is_dir():
        return None
    now = now or datetime.now()
    cutoff = now.timestamp() - stable_seconds
    candidates = []
    for f in d.glob("*.xlsx"):
        if f.name.startswith("~$"):
            continue  # Excel 暫存鎖檔
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if mtime <= cutoff:
            candidates.append((mtime, f))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def pickup(conn, directory: str | None = None, importer=None,
           now: datetime | None = None) -> dict:
    """抓一次：找最新且寫完的 RVTools 檔，比上次處理過的新才匯。

    回 {"status": ...}：
      imported        真的匯了一份新的（附匯入摘要）
      already_current 最新的那份已經匯過了，沒有新檔
      no_file         資料夾裡沒有可用的檔（或還在寫）
      no_dir          沒設定資料夾／資料夾不存在
      error           匯入過程出錯（附原因）
    importer 可注入，測試不必真的解析 Excel。
    """
    from db import _now_local

    cfg = get_config(conn)
    directory = directory if directory is not None else cfg["dir"]
    if not directory or not Path(directory).is_dir():
        return {"status": "no_dir", "dir": directory}

    newest = newest_export(directory, now=now)
    if newest is None:
        return {"status": "no_file", "dir": directory}

    sig = _sig(newest)
    if sig == get_setting(conn, LAST_SIG_KEY, ""):
        return {"status": "already_current", "file": newest.name}

    run_import = importer or _default_importer
    try:
        summary = run_import(conn, newest)
    except Exception as exc:  # noqa: BLE001 - 壞檔/非RVTools格式如實回報，不讓排程整個掛
        set_setting(conn, LAST_RESULT_KEY, f"匯入失敗：{str(exc)[:200]}")
        return {"status": "error", "file": newest.name, "error": str(exc)[:300]}

    # 記住已處理，同一份不會再匯；並留人看得懂的一句話
    set_setting(conn, LAST_FILE_KEY, newest.name)
    set_setting(conn, LAST_SIG_KEY, sig)
    set_setting(conn, LAST_AT_KEY, _now_local())
    line = (f"讀到 {summary.get('total_vms', 0)} 台"
            f"（新增 {summary.get('inserted', 0)}／更新 {summary.get('updated', 0)}"
            f"／待確認 {summary.get('pending_review', 0)}）")
    set_setting(conn, LAST_RESULT_KEY, line)
    return {"status": "imported", "file": newest.name, "summary": summary, "line": line}


def _default_importer(conn, path: Path) -> dict:
    import rvtools_import

    return rvtools_import.import_rvtools(path, conn)


def health(conn, now: datetime | None = None) -> dict:
    """鮮度燈：今晚的匯出到底有沒有進來。

      綠 = 有近期匯入、且資料夾裡最新檔在時限內
      黃 = 逾時（太久沒新檔）／已啟用但還沒抓過／資料夾沒設
      紅 = 已啟用但資料夾不存在（排程或路徑壞了，會一直收不到）
    未啟用就回灰（不示警——是刻意沒開，不是壞了）。
    """
    now = now or datetime.now()
    cfg = get_config(conn)
    if not cfg["enabled"]:
        return {"status": "off", "reason": "自動匯入未啟用", **cfg}

    directory = cfg["dir"]
    if not directory:
        return {"status": "yellow", "reason": "尚未設定監看資料夾", **cfg}
    if not Path(directory).is_dir():
        return {"status": "red", "reason": f"資料夾不存在或讀不到：{directory}", **cfg}

    newest = newest_export(directory, now=now)
    age_hours = None
    if newest is not None:
        age_hours = round((now.timestamp() - newest.stat().st_mtime) / 3600, 1)

    max_age = cfg["max_age_hours"]
    if newest is None:
        status, reason = "yellow", "資料夾裡目前沒有任何匯出檔"
    elif age_hours is not None and age_hours > max_age:
        status = "yellow"
        reason = f"最新一份匯出是 {age_hours} 小時前，超過 {max_age} 小時（今晚可能沒匯出）"
    else:
        status, reason = "green", "近期有新的匯出檔"
    return {"status": status, "reason": reason, "newest_file": newest.name if newest else None,
            "newest_age_hours": age_hours, **cfg}


def tick(conn=None) -> dict:
    """排程器每輪呼叫：啟用才抓。反應式——有新檔才動作，同一份不會重匯。

    刻意不綁掃描時序：vCenter 匯出的節奏跟網段掃描無關，各走各的。
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        if get_setting(conn, ENABLED_KEY, "0") != "1":
            return {"status": "disabled"}
        return pickup(conn)
    finally:
        if own:
            conn.close()


# ---- 診斷外掛 ----
try:
    import diagnostics

    @diagnostics.register("vcenter_autoimport")
    def _diag(conn) -> dict:
        try:
            return {"health": health(conn)}
        except Exception:  # noqa: BLE001
            return {"health": None}
except ImportError:
    pass


if __name__ == "__main__":
    # 命令列補抓一輪（給沒跑常駐服務的環境）。站內排程器已會自動抓，這是手動備援。
    from db import init_db

    init_db()
    connection = get_connection()
    try:
        result = pickup(connection)
        print(result.get("line") or result.get("status"))
    finally:
        connection.close()
