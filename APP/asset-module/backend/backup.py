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
CRITCIAL_DISK_FREE_MB = 100


class RestoreFailed(RuntimeError):
    """還原時伺服器端出錯（不是「你的檔案不對」，那類仍用 ValueError → 400）。

    2026-08-19 使用者回報「失敗沒寫原因」：還原端點只接 ValueError，磁碟滿、目錄
    沒寫入權、database is locked 這些通通穿出去變成 FastAPI 的裸 500，前端只拿得到
    `Internal Server Error`。使用者面對的是一個**不可逆**的操作，卻被告知「失敗了，
    不告訴你為什麼、也不告訴你資料現在是什麼狀態」——這比失敗本身更糟。

    訊息是要**直接顯示給使用者看**的，所以每一則都要回答三件事：
    哪一步失敗、可能的原因、**正式資料到底有沒有被動到**。
    """


# 大部分失敗都發生在真正開始覆蓋之前。使用者最需要先知道的就是這句。
_UNTOUCHED = "正式資料庫未被更動。"


def _why(exc: Exception) -> str:
    """例外轉成人看得懂的一行。保留類別名稱——`PermissionError` 跟 `OSError`
    對排查的人來說差很多，只給 str(exc) 有時候是空字串。"""
    msg = str(exc).strip()
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


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
        if free_mb < CRITCIAL_DISK_FREE_MB:
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


def restore(uploaded_bytes: bytes, db_path: Path) -> dict:
    """整庫覆蓋還原（方案B：簡單覆蓋＋前端單一確認框，2026-08-19 拍板；預設關閉的
    feature flag "restore" 是唯一防呆，不做差異預覽——真的要精細比對的場景，
    走人工 SSH+sqlite3 指令，不值得為低頻操作做整套 UI）。

    驗證只擋「根本不是這套系統的資料庫」這種明顯誤觸（integrity_check + hardware 表
    存在），不驗證內容合不合理——使用者確認要覆蓋就是確認了，這是方案B的精神。

    唯一的後悔藥：覆蓋前一定對現有正式庫做一次快照，失敗了還能從 backups/ 裡找回來。

    覆蓋用 sqlite3 內建的 Online Backup API（`Connection.backup()`），不是在檔案系統層級
    搬檔／改名——這支端點掛在 require_auth 底下，require_auth 自己也吃掉一條 `Depends(get_db)`
    連線、整個請求期間都開著；若改用 os.replace 直接動 db_path 這個檔案，Windows 會因為
    還有其他控制代碼開著它而丟 PermissionError（Linux 正式機不會，但本機開發/測試在
    Windows 上跑，還是要選一個到處都對的做法）。Online Backup API 是 SQLite 自己管理鎖，
    跟其他連線並存本來就是它設計要處理的場景，不會有這個問題。
    """
    # 全新安裝時 data/ 目錄還不存在（被 gitignore，乾淨 checkout 裡沒有），
    # 直接往底下寫暫存檔會噴 FileNotFoundError。2026-08-19 CI 實際踩到：
    # relay 的產出物是全新目錄，這支測試必定失敗、整條發布線被擋住一整天。
    # 建目錄本來就是還原流程該做的事——還原的目的地不存在就把它建出來。
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = db_path.parent / f".restore_upload_{os.getpid()}.db"
        tmp.write_bytes(uploaded_bytes)
    except OSError as exc:
        raise RestoreFailed(
            f"寫入暫存檔失敗（{db_path.parent}）：{_why(exc)}。"
            f"常見原因是磁碟空間不足或該目錄沒有寫入權限。{_UNTOUCHED}"
        ) from exc

    try:
        ok, detail = verify_integrity(tmp)
        if not ok:
            raise ValueError(f"上傳的檔案未通過完整性檢查，可能不是有效的 SQLite 資料庫：{detail}")

        try:
            src_conn = sqlite3.connect(tmp)
        except sqlite3.Error as exc:
            raise RestoreFailed(f"開啟上傳的檔案失敗：{_why(exc)}。{_UNTOUCHED}") from exc
        try:
            tables = {r[0] for r in src_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "hardware" not in tables:
                raise ValueError("上傳的檔案不是本系統的資料庫（找不到 hardware 表）")

            pre_restore_backup: str | None = None
            if db_path.exists():
                # 存證備份是這個功能唯一的後悔藥。它失敗就**不覆蓋**，直接中止——
                # 沒有後悔藥還硬幹，等於把「不可逆」變成「不可逆且無法還原」。
                try:
                    backup_dir = get_backup_dir(db_path)
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    dest = backup_dir / f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
                    snapshot(db_path, dest)
                except (OSError, sqlite3.Error) as exc:
                    raise RestoreFailed(
                        f"覆蓋前的存證備份失敗：{_why(exc)}。"
                        f"存證備份是唯一的後悔藥，做不出來就不能覆蓋，因此已中止。{_UNTOUCHED}"
                    ) from exc
                pre_restore_backup = str(dest)

            try:
                dst_conn = sqlite3.connect(db_path)
            except sqlite3.Error as exc:
                raise RestoreFailed(
                    f"開啟正式資料庫失敗（{db_path}）：{_why(exc)}。{_UNTOUCHED}"
                ) from exc
            try:
                src_conn.backup(dst_conn)
            except sqlite3.Error as exc:
                # 這裡是唯一「可能已經寫到一半」的階段，不能說資料沒被動到。
                raise RestoreFailed(
                    f"寫入正式資料庫時失敗：{_why(exc)}。"
                    f"⚠ 這一步已經開始覆蓋，資料可能處於不完整狀態，"
                    f"請用覆蓋前的存證備份還原："
                    f"{pre_restore_backup or '（無存證備份，這是首次建庫）'}"
                ) from exc
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

        return {
            "ok": True,
            "pre_restore_backup": pre_restore_backup,
            "restored_size_bytes": db_path.stat().st_size,
        }
    finally:
        tmp.unlink(missing_ok=True)


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
