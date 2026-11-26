"""本機檔案保留與磁碟用量（2.07x）：公司測試機根目錄曾滿到 100%，原因是三類檔案無限累積——
patch 安裝備份 /opt/webit3/data/patch_backup_*、每日 DB 備份 data/backups、中繼共用資料夾（relay_in）已匯入的原始批次。

這裡是這幾個保留值的**唯一一份**定義（鍵名、預設、範圍）。讀的人有三種：
  · 後端（backup.py 清備份、relay_import.py 清已匯入批次、系統設定 API）
  · patch.sh（shell；用 venv python 唯讀讀 asset.db，讀不到就用預設 3——見 make_patch.PATCH_SH 的 prune 段）
  · deploy.sh（決定要不要 enable 每日備份 timer：`python retention.py timer-decision <systemctl is-enabled 的輸出>`）

所有值都在「⚙ 系統設定 → 備份與還原 → 保留與自動清理」調整（admin 才能改、改了寫操作紀錄）。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from pathlib import Path

# 鍵名 → (預設, 最小, 最大, 中文名)。patch.sh 的 prune 段寫死了 PATCH_KEEP_KEY 的字串（shell 不能 import），改名要同步。
PATCH_KEEP_KEY = "retention_patch_backup_keep"
DB_BACKUP_KEEP_KEY = "retention_db_backup_keep"
RELAY_DAYS_KEY = "retention_relay_in_days"
DISK_PCT_KEY = "local_disk_alert_pct"
TIMER_KEY = "backup_timer_enabled"           # "1"／"0"；沒有值＝還沒決定過（deploy.sh 會依 timer 現況補寫）

INT_SPECS: dict[str, tuple[int, int, int, str]] = {
    PATCH_KEEP_KEY: (3, 1, 30, "patch 安裝備份保留份數"),
    DB_BACKUP_KEEP_KEY: (3, 1, 30, "每日資料庫備份保留份數"),
    RELAY_DAYS_KEY: (7, 1, 90, "中繼已匯入檔案保留天數"),
    DISK_PCT_KEY: (85, 50, 99, "本機磁碟使用率警示門檻（%）"),
}
# API／畫面用的短名 → 鍵名
FIELD_KEYS = {
    "patch_backup_keep": PATCH_KEEP_KEY,
    "db_backup_keep": DB_BACKUP_KEEP_KEY,
    "relay_in_days": RELAY_DAYS_KEY,
    "disk_alert_pct": DISK_PCT_KEY,
}


def _get(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return row[0] if not isinstance(row, sqlite3.Row) else row["value"]


def get_int(conn, key: str) -> int:
    """讀一個整數保留值；沒設、壞值、超出範圍一律退回預設（不讓壞設定變成「全部刪光」或「永遠不清」）。"""
    default, lo, hi, _ = INT_SPECS[key]
    if conn is None:
        return default
    try:
        raw = _get(conn, key)
    except sqlite3.Error:
        return default
    try:
        v = int(str(raw).strip()) if raw is not None else default
    except ValueError:
        return default
    return v if lo <= v <= hi else default


def timer_enabled(conn) -> bool | None:
    """每日自動備份開關：True／False；None＝從沒決定過（畫面顯示為「開」，deploy.sh 會補寫）。"""
    try:
        raw = _get(conn, TIMER_KEY)
    except sqlite3.Error:
        return None
    if raw is None:
        return None
    return str(raw).strip() == "1"


def timer_actual() -> str | None:
    """systemd 上 webit3-backup.timer 的實際狀態（enabled／disabled…）；不是 Linux 或查不到回 None。不需要 root。"""
    exe = shutil.which("systemctl")
    if not exe:
        return None
    import subprocess
    try:
        r = subprocess.run([exe, "is-enabled", "webit3-backup.timer"], capture_output=True, text=True, timeout=5)
        return (r.stdout or "").strip() or None
    except (OSError, subprocess.SubprocessError):     # 查不到＝畫面寫「無法確認」，不猜
        return None


def get_config(conn) -> dict:
    out = {name: get_int(conn, key) for name, key in FIELD_KEYS.items()}
    t = timer_enabled(conn)
    out["backup_timer_enabled"] = True if t is None else t
    out["backup_timer_decided"] = t is not None
    # 畫面要看得出「設定」跟「排程實際狀態」不一樣（審查 M3）：在畫面打開後要等下次部署才會真的 enable
    out["backup_timer_actual"] = timer_actual()
    out["limits"] = {name: {"min": INT_SPECS[key][1], "max": INT_SPECS[key][2], "default": INT_SPECS[key][0]}
                     for name, key in FIELD_KEYS.items()}
    return out


def validate(values: dict) -> dict[str, int]:
    """只收認得的欄位；整數且在範圍內，否則 ValueError（訊息直接給使用者看）。"""
    clean: dict[str, int] = {}
    for name, v in values.items():
        if v is None or name not in FIELD_KEYS:
            continue
        _, lo, hi, label = INT_SPECS[FIELD_KEYS[name]]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{label}要填整數")
        if not lo <= v <= hi:
            raise ValueError(f"{label}請填 {lo} 到 {hi}")
        clean[name] = v
    return clean


def set_config(conn, values: dict, timer: bool | None = None) -> tuple[dict, list[str]]:
    """寫入（同一筆交易）。回 (新設定, 異動說明清單)；說明給操作紀錄用（原值→新值）。"""
    clean = validate(values)
    before = get_config(conn)
    changes: list[str] = []
    for name, v in clean.items():
        key = FIELD_KEYS[name]
        if before[name] != v:
            changes.append(f"{INT_SPECS[key][3]} {before[name]}→{v}")
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, str(v)))
    if timer is not None:
        if before["backup_timer_enabled"] != bool(timer) or not before["backup_timer_decided"]:
            changes.append(f"每日自動備份 {'開' if before['backup_timer_enabled'] else '關'}→{'開' if timer else '關'}")
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (TIMER_KEY, "1" if timer else "0"))
    conn.commit()
    return get_config(conn), changes


# ---------------------------------------------------------------- deploy.sh：timer 要不要 enable
def timer_decision(conn, systemctl_state: str) -> str:
    """回 "enable" 或 "disable"。

    · app_settings 有值 → 照設定。
    · 沒有值、timer 目前是 disabled（有人手動 `systemctl disable`）→ 視為關閉，**寫回 "0"**，回 disable。
    · 沒有值、其他狀態（enabled／not-found／新裝）→ enable（不寫，維持「沒決定過＝預設開」）。
    · DB 讀不到（conn=None）→ 只看現況：disabled 就維持 disabled，絕不把使用者關掉的重新打開。
    """
    state = (systemctl_state or "").strip().lower()
    if conn is None:
        return "disable" if state == "disabled" else "enable"
    t = timer_enabled(conn)
    if t is not None:
        return "enable" if t else "disable"
    if state == "disabled":
        try:
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, '0') ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (TIMER_KEY,))
            conn.commit()
        except sqlite3.Error:
            pass                    # 寫不回去也照樣不重開；下次部署再試
        return "disable"
    return "enable"


# ---------------------------------------------------------------- 本機磁碟用量（1.1 今日概況）
def local_disk(data_dir: Path | str, alert_pct: int) -> list[dict]:
    """戰情室本機根目錄 / 與資料目錄所在檔案系統的使用率。同一個檔案系統只列一次（st_dev 相同）。
    讀不到的列 state="unknown"——不知道≠沒問題。"""
    targets = [("根目錄", Path(os.path.abspath(os.sep))), ("資料目錄", Path(data_dir))]
    out: list[dict] = []
    seen: dict[int, dict] = {}
    for label, p in targets:
        probe = p if p.exists() else p.parent
        try:
            dev = os.stat(probe).st_dev
            if dev in seen:
                seen[dev]["label"] += "＋" + label
                seen[dev]["paths"].append(str(p))
                continue
            u = shutil.disk_usage(probe)
            # 跟 df 的 Use% 同一個算法：已用 ÷（已用＋一般帳號可用）。ext4 的 root 保留空間不算可用（審查低 1）
            pct = round(u.used * 100.0 / (u.used + u.free), 1) if (u.used + u.free) else 0.0
            row = {"label": label, "paths": [str(p)], "total_gb": round(u.total / 1024 ** 3, 1),
                   "used_gb": round(u.used / 1024 ** 3, 1), "free_gb": round(u.free / 1024 ** 3, 1),
                   "pct": pct, "state": "high" if pct >= alert_pct else "ok"}     # 全站門檻一律「達到」就算
            seen[dev] = row
            out.append(row)
        except OSError as exc:
            out.append({"label": label, "paths": [str(p)], "total_gb": None, "used_gb": None, "free_gb": None,
                        "pct": None, "state": "unknown", "error": f"{type(exc).__name__}: {exc}"})
    return out


# ---------------------------------------------------------------- CLI（deploy.sh 用）
def _open(path: str, write: bool):
    if not path or not os.path.isfile(path):
        return None
    try:
        if write:
            conn = sqlite3.connect(path, timeout=10)
        else:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        conn.execute("SELECT 1 FROM app_settings LIMIT 1")
        return conn
    except sqlite3.Error:
        return None


def main(argv: list[str]) -> int:
    db_path = os.environ.get("ASSET_DB_PATH", "")
    if len(argv) >= 2 and argv[0] == "get" and argv[1] in FIELD_KEYS:
        conn = _open(db_path, write=False)
        print(get_int(conn, FIELD_KEYS[argv[1]]))
        return 0
    if len(argv) >= 1 and argv[0] == "timer-decision":
        conn = _open(db_path, write=True)
        print(timer_decision(conn, argv[1] if len(argv) > 1 else ""))
        return 0
    print("用法：retention.py get <patch_backup_keep|db_backup_keep|relay_in_days|disk_alert_pct> ｜ timer-decision <systemctl 狀態>",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
