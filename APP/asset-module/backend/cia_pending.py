"""CIA 待異動：系統裡改了、CIA 資產清冊還沒改的東西（2026-09-15）。

## 為什麼要有

使用者抓到一批設備已下線，要把資產狀態改成停用／報廢。但 **CIA 清冊才是來源**：
匯入時會用 Excel 的值更新每一欄，清冊上還寫「使用中」的話，下次重匯就被蓋回去，
而且沒有人會發現（2026-09-15 查證：excel_import._upsert 會更新記錄裡的每個欄位）。

所以系統裡每一次改資產狀態，都記一筆「待同步回 CIA」：誰、什麼時候、從什麼改成什麼、為什麼。
使用者去 CIA 清冊改完後按「已同步」。清單只增不刪，已同步的也留著（稽核軌跡）。

## 批次改狀態

使用者：「我有抓到一些設備已下線，我要怎麼變更」→ 資產查詢頁勾選多台一次改。
**原因必填**：半年後有人問「這台為什麼是報廢」，要答得出來。
"""
from __future__ import annotations

from datetime import datetime

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS cia_pending_change (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_serial TEXT NOT NULL,
    hostname    TEXT,
    ip          TEXT,
    field       TEXT NOT NULL,       -- 目前只記 asset_status
    field_label TEXT,
    old_value   TEXT,
    new_value   TEXT,
    reason      TEXT,
    changed_by  TEXT,
    changed_at  TEXT NOT NULL,
    synced_at   TEXT,                -- 使用者回報 CIA 清冊已改好
    synced_by   TEXT
)
"""

#: 批次可以設的狀態。停用／報廢／閒置＝退役（manage_state.RETIRED_STATUS），使用中＝改回來
STATUS_CHOICES = ("使用中", "停用", "報廢", "閒置")
FIELD_LABEL = {"asset_status": "資產狀態"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure(conn) -> None:
    conn.execute(TABLE_SQL)


def record(conn, asset_serial: str, field: str, old_value, new_value,
           reason: str | None, by: str | None, commit: bool = True) -> None:
    """記一筆待同步。值沒變就不記。"""
    old_s = "" if old_value is None else str(old_value)
    new_s = "" if new_value is None else str(new_value)
    if old_s == new_s:
        return
    _ensure(conn)
    hw = conn.execute("SELECT hostname, ip FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
    conn.execute(
        "INSERT INTO cia_pending_change (asset_serial, hostname, ip, field, field_label, old_value, "
        "new_value, reason, changed_by, changed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (asset_serial, hw[0] if hw else None, hw[1] if hw else None, field,
         FIELD_LABEL.get(field, field), old_s or None, new_s or None, reason, by, _now()))
    if commit:
        conn.commit()


def batch_set_status(conn, serials: list[str], status: str, reason: str, by: str | None,
                     scope: str = "single") -> dict:
    """一次改多台的資產狀態，每台記一筆 CIA 待異動。

    [B-08] scope：
    - "single"（預設，維持舊行為）：只改選的那幾筆——「某個服務下線」
    - "machine"：展開成整台（同一台的全部登記）——「整台下線」
    CIA 一筆＝一個服務，「整台下線」跟「某個服務下線」是兩件事，要人選。
    **全成功或全不做**：中途任何一筆出錯就整批復原，不留下一半改了、一半沒改。
    """
    if scope not in ("single", "machine"):
        raise ValueError("scope 只能是 single（只這幾筆）或 machine（整台）")
    status = (status or "").strip()
    reason = (reason or "").strip()
    if status not in STATUS_CHOICES:
        raise ValueError(f"狀態只能是：{'、'.join(STATUS_CHOICES)}")
    if not reason:
        raise ValueError("要填原因（例：現場確認已下線），日後有人問才答得出來")
    uniq = [s.strip() for s in dict.fromkeys(serials or []) if s and s.strip()]
    if not uniq:
        raise ValueError("沒有選任何資產")
    _ensure(conn)
    if scope == "machine":
        import manage_state
        uniq = manage_state.expand_to_machines(conn, uniq)
    now = _now()
    updated, unchanged, not_found = [], [], []
    try:
        for s in uniq:
            row = conn.execute("SELECT asset_status FROM hardware WHERE asset_serial = ?", (s,)).fetchone()
            if row is None:
                not_found.append(s)
                continue
            if (row[0] or "") == status:
                unchanged.append(s)
                continue
            conn.execute("UPDATE hardware SET asset_status = ?, manual_updated_at = ? WHERE asset_serial = ?",
                         (status, now, s))
            record(conn, s, "asset_status", row[0], status, reason, by, commit=False)
            updated.append(s)
        conn.commit()
    except Exception:
        conn.rollback()          # [B-08] 全成功或全不做
        raise
    return {"updated": updated, "unchanged": unchanged, "not_found": not_found, "status": status,
            "scope": scope}


def list_changes(conn, include_synced: bool = False) -> list[dict]:
    _ensure(conn)
    where = "" if include_synced else "WHERE synced_at IS NULL "
    cur = conn.execute(f"SELECT * FROM cia_pending_change {where}ORDER BY id DESC")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def mark_synced(conn, ids: list[int], by: str | None) -> int:
    _ensure(conn)
    ids = [int(i) for i in ids or []]
    if not ids:
        return 0
    ph = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"UPDATE cia_pending_change SET synced_at = ?, synced_by = ? WHERE synced_at IS NULL AND id IN ({ph})",
        (_now(), by, *ids))
    conn.commit()
    return cur.rowcount


EXPORT_HEADERS = ["編號", "資產序號", "主機名稱", "IP", "欄位", "原本", "改成", "原因",
                  "改的人", "改的時間", "CIA 已同步時間", "同步的人"]


def export_rows(conn, include_synced: bool = False) -> list[list]:
    return [[r["id"], r["asset_serial"], r["hostname"], r["ip"], r["field_label"], r["old_value"],
             r["new_value"], r["reason"], r["changed_by"], r["changed_at"], r["synced_at"], r["synced_by"]]
            for r in list_changes(conn, include_synced)]
