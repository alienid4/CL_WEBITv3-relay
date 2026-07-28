"""S12/S14：資料庫備份（D6：每天備份、留 7 天）＋ 健康狀態判讀。

排程觸發（部署環境）：systemd timer 每天呼叫一次 `python backup.py`。
S14 另外把它接上 API，讓管理後台可以手動觸發、看燈號，不用進命令列。

⚠️ 為什麼不是 shutil.copy2：
原本用 shutil.copy2 直接複製 .db 檔。SQLite 的資料庫是活的——複製當下若有寫入
交易進行中（或 WAL 裡還有沒 checkpoint 的內容），複製出來的檔案可能是**破損或缺資料**的，
而且壞掉當下不會有任何錯誤，要等到真的拿去還原才發現。備份的價值就在還原那一刻，
這種「看起來有備份其實還原不了」是最糟的失敗模式。

改用 sqlite3 的 `VACUUM INTO`：由 SQLite 自己在一個交易裡把整個資料庫寫成一個乾淨的新檔，
過程中不會拿到寫到一半的頁面，也順便把碎片整理掉。備份完再對產出的檔案跑
`PRAGMA integrity_check`，確認它真的能開、內容完整——沒驗過的備份不算備份。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from db import get_connection, get_db_path

RETENTION_DAYS = 7
BACKUP_FILENAME_PREFIX = "asset_"

# 超過這個時數沒有成功備份就算「逾時」（黃燈）。排程是每日一次，給一天的寬限。
BACKUP_MAX_AGE_HOURS = 36
# 剩餘空間低於這個值算偏低（黃燈）
LOW_DISK_FREE_MB = 500
# 剩餘空間低於這個值算不足（紅燈）——連下一份備份都放不下
CRITICAL_DISK_FREE_MB = 100


def get_backup_dir(db_path: Path | None = None) -> Path:
    """本地備份目錄。預設放在 DB 旁邊的 backups/。"""
    override = os.environ.get("ASSET_BACKUP_DIR")
    if override:
        return Path(override)
    return (db_path or get_db_path()).parent / "backups"


# 異地備份目錄的 app_settings 鍵。讓它能在畫面上設定，不用改 systemd 環境變數再重啟。
OFFSITE_SETTING_KEY = "backup_offsite_dir"


def get_offsite_dir(conn=None) -> Path | None:
    """異地備份目錄（另一顆磁碟／另一台機器的掛載點）。沒設＝沒有異地，健康狀態標黃燈。

    只有本地備份的話，磁碟壞掉時備份跟正本一起沒了——那不叫備份。

    解析順序：畫面設定（app_settings）優先，退回環境變數 ASSET_BACKUP_OFFSITE_DIR。
    app_settings 存了空字串＝使用者明確清掉異地，這時**不**再退回環境變數（否則清不掉）。
    """
    if conn is not None:
        from db import get_setting

        val = get_setting(conn, OFFSITE_SETTING_KEY, None)
        if val is not None:
            val = val.strip()
            return Path(val) if val else None
    override = os.environ.get("ASSET_BACKUP_OFFSITE_DIR")
    return Path(override) if override else None


@dataclass
class BackupResult:
    ok: bool
    path: Path | None = None
    size_bytes: int = 0
    integrity_ok: bool = False
    integrity_detail: str = ""
    offsite_path: Path | None = None
    offsite_error: str = ""
    pruned: list[Path] = field(default_factory=list)
    error: str = ""
    took_seconds: float = 0.0


def snapshot(db_path: Path, dest: Path) -> None:
    """用 VACUUM INTO 產生一致的資料庫快照。

    VACUUM INTO 要求目標檔不存在，存在就直接報錯——這是好事，不會默默覆蓋掉別人的備份。
    """
    if dest.exists():
        raise FileExistsError(f"備份目標已存在，不覆蓋：{dest}")
    conn = sqlite3.connect(db_path)
    try:
        # 參數化不能用在 VACUUM INTO 的路徑上，改用 SQL 字串常值並跳脫單引號
        escaped = str(dest).replace("'", "''")
        conn.execute(f"VACUUM INTO '{escaped}'")
    finally:
        conn.close()


def verify_integrity(db_file: Path) -> tuple[bool, str]:
    """對備份檔跑 integrity_check。沒驗過的備份不算備份。"""
    if not db_file.exists():
        return False, "備份檔不存在"
    try:
        conn = sqlite3.connect(db_file)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return False, f"無法開啟備份檔：{exc}"
    detail = "; ".join(r[0] for r in rows) if rows else "（無回應）"
    return detail.strip().lower() == "ok", detail


def run_backup(
    db_path: Path,
    backup_dir: Path,
    now: datetime,
    offsite_dir: Path | None = None,
) -> BackupResult:
    """做一次備份：安全快照 -> 完整性驗證 -> 複製到異地 -> 清理過期檔。

    now 由外部傳入（不是內部 datetime.now()），測試才能注入固定時間驗證保留天數，
    不用真的等 7 天。
    """
    started = now
    result = BackupResult(ok=False)
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%d_%H%M%S")
        dest = backup_dir / f"{BACKUP_FILENAME_PREFIX}{stamp}.db"

        snapshot(db_path, dest)
        result.path = dest
        result.size_bytes = dest.stat().st_size

        ok, detail = verify_integrity(dest)
        result.integrity_ok = ok
        result.integrity_detail = detail
        if not ok:
            # 驗不過的備份留在原地供事後檢查，但這次備份算失敗
            result.error = f"完整性檢查未通過：{detail}"
            return result

        if offsite_dir is not None:
            try:
                offsite_dir.mkdir(parents=True, exist_ok=True)
                offsite_dest = offsite_dir / dest.name
                shutil.copy2(dest, offsite_dest)
                result.offsite_path = offsite_dest
            except OSError as exc:
                # 異地失敗不讓整次備份算失敗（本地那份是好的），但要讓燈號看得到
                result.offsite_error = str(exc)

        result.pruned = prune_old_backups(backup_dir, now)
        if offsite_dir is not None:
            try:
                result.pruned += prune_old_backups(offsite_dir, now)
            except OSError:
                pass

        result.ok = True
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.took_seconds = round((datetime.now() - started).total_seconds(), 2)
    return result


def prune_old_backups(backup_dir: Path, now: datetime) -> list[Path]:
    """刪除超過 RETENTION_DAYS 天的備份檔，回傳被刪掉的清單。"""
    cutoff_ts = now.timestamp() - RETENTION_DAYS * 86400
    removed = []
    for f in sorted(backup_dir.glob(f"{BACKUP_FILENAME_PREFIX}*.db")):
        if f.stat().st_mtime < cutoff_ts:
            f.unlink()
            removed.append(f)
    return removed


def list_backups(backup_dir: Path) -> list[dict]:
    """依時間新到舊列出備份檔。"""
    if not backup_dir.exists():
        return []
    items = []
    for f in backup_dir.glob(f"{BACKUP_FILENAME_PREFIX}*.db"):
        st = f.stat()
        items.append({
            "name": f.name,
            "size_bytes": st.st_size,
            "modified_at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "_mtime": st.st_mtime,
        })
    items.sort(key=lambda x: x["_mtime"], reverse=True)
    for it in items:
        it.pop("_mtime")
    return items


def _free_mb(path: Path) -> float | None:
    try:
        target = path if path.exists() else path.parent
        return round(shutil.disk_usage(target).free / (1024 * 1024), 1)
    except OSError:
        return None


def health(
    db_path: Path | None = None,
    backup_dir: Path | None = None,
    offsite_dir: Path | None = None,
    now: datetime | None = None,
    conn=None,
) -> dict:
    """把備份狀態壓成一顆燈號 + 明細，給管理後台一眼看懂。

    燈號規則（backlog S14 定義）：
      綠 = 上次備份成功且在時限內 + integrity_check PASS + 空間足
      黃 = 備份逾時 / 只有本地無異地 / 空間偏低
      紅 = 備份失敗 / integrity_check 失敗 / DB 讀不到

    紅燈優先於黃燈：同時有兩種問題時顯示比較嚴重的那個。
    """
    now = now or datetime.now()
    db_path = db_path or get_db_path()
    backup_dir = backup_dir if backup_dir is not None else get_backup_dir(db_path)
    offsite_dir = offsite_dir if offsite_dir is not None else get_offsite_dir(conn)

    reds: list[str] = []
    yellows: list[str] = []

    # --- 主庫本身讀不讀得到 ---
    db_ok, db_detail = verify_integrity(db_path) if db_path.exists() else (False, "資料庫檔不存在")
    if not db_ok:
        reds.append(f"資料庫讀取異常：{db_detail}")

    journal_mode = None
    if db_path.exists():
        try:
            conn = get_connection(db_path)
            try:
                journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.DatabaseError:
            pass

    # --- 最近一次備份 ---
    backups = list_backups(backup_dir)
    last = backups[0] if backups else None
    last_age_hours = None
    if last:
        last_dt = datetime.strptime(last["modified_at"], "%Y-%m-%d %H:%M:%S")
        last_age_hours = round((now - last_dt).total_seconds() / 3600, 1)
        if last_age_hours > BACKUP_MAX_AGE_HOURS:
            yellows.append(
                f"最近一次備份是 {last_age_hours} 小時前，超過 {BACKUP_MAX_AGE_HOURS} 小時"
            )
    else:
        reds.append("沒有任何備份")

    # --- 最近一份備份的完整性 ---
    last_integrity_ok = None
    last_integrity_detail = ""
    if last:
        last_integrity_ok, last_integrity_detail = verify_integrity(backup_dir / last["name"])
        if not last_integrity_ok:
            reds.append(f"最近一份備份完整性檢查失敗：{last_integrity_detail}")

    # --- 異地 ---
    offsite_count = len(list_backups(offsite_dir)) if offsite_dir else 0
    if offsite_dir is None:
        yellows.append("沒有設定異地備份（只有本地一份，磁碟壞掉就一起沒了）")
    elif offsite_count == 0:
        yellows.append("異地備份目錄裡沒有任何備份")

    # --- 空間 ---
    free_mb = _free_mb(backup_dir)
    if free_mb is not None:
        if free_mb < CRITICAL_DISK_FREE_MB:
            reds.append(f"備份磁碟剩餘空間只剩 {free_mb} MB")
        elif free_mb < LOW_DISK_FREE_MB:
            yellows.append(f"備份磁碟剩餘空間偏低（{free_mb} MB）")

    status = "red" if reds else ("yellow" if yellows else "green")
    return {
        "status": status,
        "reasons": reds + yellows,
        "checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "db": {
            "path": str(db_path),
            "exists": db_path.exists(),
            "integrity_ok": db_ok,
            "integrity_detail": db_detail,
            "journal_mode": journal_mode,
            "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        },
        "last_backup": (
            {
                **last,
                "age_hours": last_age_hours,
                "integrity_ok": last_integrity_ok,
                "integrity_detail": last_integrity_detail,
            }
            if last
            else None
        ),
        "local": {
            "dir": str(backup_dir),
            "count": len(backups),
            "free_mb": free_mb,
            "retention_days": RETENTION_DAYS,
        },
        "offsite": {
            "configured": offsite_dir is not None,
            "dir": str(offsite_dir) if offsite_dir else None,
            "count": offsite_count,
        },
    }


if __name__ == "__main__":
    src = get_db_path()
    outcome = run_backup(src, get_backup_dir(src), datetime.now(), get_offsite_dir())
    if outcome.ok:
        print(f"備份完成：{outcome.path}（{outcome.size_bytes} bytes，"
              f"完整性 {'PASS' if outcome.integrity_ok else 'FAIL'}，{outcome.took_seconds}s）")
        if outcome.offsite_path:
            print(f"異地副本：{outcome.offsite_path}")
        elif outcome.offsite_error:
            print(f"異地副本失敗：{outcome.offsite_error}")
        if outcome.pruned:
            print(f"清掉 {len(outcome.pruned)} 份過期備份")
    else:
        print(f"備份失敗：{outcome.error}")
        raise SystemExit(1)
