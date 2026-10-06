"""OS 類型的人工指定（2026-09-18）。

## 為什麼要有這支

使用者：「如果少數錯誤，我可以手動編輯 OS 類型搬移」。

OS 類型是系統依 OS／設備機型／名稱自動判的（pipeline._os_type），總會有少數判錯。
但**不能叫人去改資產的 OS 欄**：重匯 CIA 時 `excel_import._upsert` 會覆寫每個非鍵欄位
（excel_import.py:147-153），改了只撐到下次匯入。所以人工指定另外存一張表，重匯不碰它。

## 認台方式

存原始的主機名＋IP＋序號，**讀取時**才用正典 `system_stats.machine_key` 算鍵：
- 主機名＋IP 都相同才套用 → IP 被回收給另一台（主機名不同）時，指定自動失效，
  不會把舊判斷套到新機器上
- 將來 machine_key 的正規化規則改了（例如 B-03 處理網域），舊指定也跟著用新規則算，
  不會變成孤兒

## 留痕

誰、何時、原因（選填）、系統原本判成什麼。取消用軟刪除（removed_at），紀錄不消失。
只影響顯示與統計，不改 CIA 原始資料。
"""
from __future__ import annotations

from datetime import datetime

SQL = """
CREATE TABLE IF NOT EXISTS os_type_override (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname     TEXT,
    ip           TEXT,
    asset_serial TEXT,
    os_type      TEXT NOT NULL,        -- 人工指定的類別
    auto_os_type TEXT,                 -- 指定當下系統自動判的類別（留證據）
    reason       TEXT,
    created_by   TEXT,
    created_at   TEXT NOT NULL,
    removed_by   TEXT,
    removed_at   TEXT
)
"""


def _ensure(conn) -> None:
    conn.execute(SQL)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def choices() -> list[str]:
    """可以指定的類別：漏斗的正式類別，不含「推測」與「未填」（人工指定就不是推測／未填了）。"""
    import pipeline
    return [c for c in pipeline.OS_ORDER
            if c not in (pipeline.OS_GUESS_LINUX, pipeline.OS_GUESS_WINDOWS, pipeline.OS_UNSET)]


def _key(hostname, ip, asset_serial) -> str:
    import system_stats
    return system_stats.machine_key(hostname, ip, asset_serial)


def active_map(conn) -> dict[str, dict]:
    """目前有效的指定：machine_key → 那一筆。同一台多次指定取最新。"""
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT * FROM os_type_override WHERE removed_at IS NULL ORDER BY id").fetchall()
    except Exception:  # noqa: BLE001 - 唯讀庫或舊庫：當作沒有指定，不擋整頁
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        out[_key(r["hostname"], r["ip"], r["asset_serial"])] = dict(r)
    return out


def resolve(ov_map: dict, hostname, ip, asset_serial, auto: str) -> tuple[str, dict | None]:
    """回 (最終類別, 人工指定資訊或 None)。"""
    ov = ov_map.get(_key(hostname, ip, asset_serial))
    if not ov:
        return auto, None
    return ov["os_type"], {"by": ov["created_by"], "at": ov["created_at"],
                           "reason": ov["reason"], "auto_at_time": ov["auto_os_type"]}


def set_override(conn, hostname, ip, asset_serial, os_type: str, auto_os_type: str | None,
                 reason: str | None, by: str | None) -> dict:
    if os_type not in choices():
        raise ValueError(f"不認得的類別：{os_type}（可選：{'、'.join(choices())}）")
    if not ((hostname and ip) or asset_serial):
        raise ValueError("要有主機名＋IP，或資產序號，才認得出是哪一台")
    _ensure(conn)
    clear(conn, hostname, ip, asset_serial, by, _commit=False)   # 同一台只留一筆有效
    now = _now()
    conn.execute(
        "INSERT INTO os_type_override (hostname, ip, asset_serial, os_type, auto_os_type, reason, "
        "created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (hostname, ip, asset_serial, os_type, auto_os_type, (reason or "").strip() or None, by, now))
    conn.commit()
    return {"ok": True, "os_type": os_type, "at": now}


def clear(conn, hostname, ip, asset_serial, by: str | None, _commit: bool = True) -> int:
    """恢復自動：軟刪除這台目前有效的指定。回清掉幾筆。"""
    _ensure(conn)
    k = _key(hostname, ip, asset_serial)
    now = _now()
    n = 0
    for r in conn.execute("SELECT id, hostname, ip, asset_serial FROM os_type_override "
                          "WHERE removed_at IS NULL").fetchall():
        if _key(r["hostname"], r["ip"], r["asset_serial"]) == k:
            conn.execute("UPDATE os_type_override SET removed_at = ?, removed_by = ? WHERE id = ?",
                         (now, by, r["id"]))
            n += 1
    if _commit:
        conn.commit()
    return n


def list_active(conn) -> list[dict]:
    _ensure(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM os_type_override WHERE removed_at IS NULL ORDER BY created_at DESC")]
