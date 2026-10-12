"""弱點彙總（CL_Patch）搬上系統版的共享儲存（2026-09-21 使用者：

> 「CL_Patch 是小工具，我們目前已經在用了，我只是要轉移到系統上，變系統版不是單機版」

## 為什麼只做一張 key/value 表

那支工具的分析邏輯（5,430 行）已經在用、已經驗過，**這次是搬家不是重做**。
它原本把東西存在瀏覽器 localStorage，所以「單機版」——換台電腦就看不到、
主管跟各負責單位各看各的、誰催過誰也不共用。

搬上系統要變的只有一件事：**那幾項該共享的東西改存伺服器**。不是每一項都要搬：

| 原本的 localStorage | 搬不搬 | 為什麼 |
|---|---|---|
| 整份 Excel ＋ 目前資料      | 搬 | 這就是單機版與系統版的差別，別人才看得到同一份 |
| 歷史快照（差異的來源）      | 搬 | 不搬的話每個人的「跟上週比」都不一樣 |
| 發信紀錄（誰催過誰）        | 搬 | 不共用會重複催同一個人 |
| Email 設定                  | 搬 | 全站一份 |
| 目前看第幾張表 / 篩選條件   | **不搬** | 個人操作位置。搬上去兩個人同時看會互相蓋掉畫面 |
| 勾了哪些人要催              | **不搬** | 同上 |

所以這裡只收「該共享」的那幾項，而且用白名單擋——不開放任意 key 寫入，
不然這張表會變成前端想塞什麼就塞什麼的垃圾桶，半年後沒人知道裡面是什麼。

## 大小

整份 Excel 以原始位元組存（BLOB），不存 base64——base64 會膨脹 33%，
而且前端本來就要轉回 ArrayBuffer。原本存在 localStorage 代表它一定小於
瀏覽器那 5MB 上限，DB 這邊再給一個明確上限，超過就明講「太大」，不要靜默失敗
（那支工具原本就吃過「配額耗盡靜默失敗、歷史悄悄停在舊的一期」的虧，見 history.js）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

#: 允許共享的 key。要加新的就在這裡加，並寫清楚是什麼——不開放任意 key。
SHARED_KEYS = {
    "workbook": "最近一次匯入的 Excel（原始位元組）與檔名",
    "history": "歷史快照（本次 vs 上次的差異來源，最近 12 期）",
    "mail_log": "發信紀錄（誰在什麼時候催過誰）",
    "mail_config": "Email 設定（主旨範本、寄件方式等）",
}

#: 單一 key 的大小上限。8MB 是給 Excel 用的——其餘都是 JSON，遠小於此。
MAX_BYTES = 8 * 1024 * 1024

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS patch_blob (
    key          TEXT PRIMARY KEY,
    body         BLOB NOT NULL,
    content_type TEXT,
    file_name    TEXT,
    updated_by   TEXT,
    updated_at   TEXT NOT NULL
)
"""


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(TABLE_SQL)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def check_key(key: str) -> str:
    key = (key or "").strip()
    if key not in SHARED_KEYS:
        raise ValueError(
            f"不認得的項目「{key}」。可用的是：{'、'.join(sorted(SHARED_KEYS))}")
    return key


def get(conn: sqlite3.Connection, key: str) -> dict | None:
    """讀一項。沒有就回 None——「還沒有人匯入過」不是錯誤。"""
    key = check_key(key)
    _ensure(conn)
    r = conn.execute(
        "SELECT key, body, content_type, file_name, updated_by, updated_at "
        "FROM patch_blob WHERE key = ?", (key,)).fetchone()
    return dict(r) if r else None


def put(conn: sqlite3.Connection, key: str, body: bytes,
        content_type: str | None = None, file_name: str | None = None,
        by: str | None = None) -> dict:
    """覆蓋寫入一項。太大就明講，不要靜默失敗。"""
    key = check_key(key)
    if body is None:
        raise ValueError("沒有內容")
    if len(body) > MAX_BYTES:
        raise ValueError(
            f"「{SHARED_KEYS[key]}」有 {len(body) / 1024 / 1024:.1f}MB，"
            f"超過上限 {MAX_BYTES // 1024 // 1024}MB，沒有存上去")
    _ensure(conn)
    now = _now()
    conn.execute(
        "INSERT INTO patch_blob (key, body, content_type, file_name, updated_by, updated_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET body = excluded.body, "
        "content_type = excluded.content_type, file_name = excluded.file_name, "
        "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
        (key, sqlite3.Binary(body), content_type, file_name, by, now))
    conn.commit()
    return {"key": key, "bytes": len(body), "file_name": file_name,
            "updated_by": by, "updated_at": now}


def delete(conn: sqlite3.Connection, key: str) -> dict:
    """清掉一項（對應畫面上的「清除記憶」）。"""
    key = check_key(key)
    _ensure(conn)
    conn.execute("DELETE FROM patch_blob WHERE key = ?", (key,))
    conn.commit()
    return {"key": key, "cleared": True}


def status(conn: sqlite3.Connection) -> dict:
    """每一項現在有沒有、多大、誰在什麼時候更新的。

    畫面上要看得到「這份是誰在什麼時候匯入的」——系統版跟單機版的差別就在這裡，
    不標的話使用者不知道自己看的是不是同事剛換掉的那一份。
    """
    _ensure(conn)
    rows = {r["key"]: r for r in conn.execute(
        "SELECT key, LENGTH(body) AS bytes, file_name, updated_by, updated_at FROM patch_blob")}
    return {
        "items": [
            {
                "key": k, "label": label,
                "present": k in rows,
                "bytes": rows[k]["bytes"] if k in rows else 0,
                "file_name": rows[k]["file_name"] if k in rows else None,
                "updated_by": rows[k]["updated_by"] if k in rows else None,
                "updated_at": rows[k]["updated_at"] if k in rows else None,
            }
            for k, label in sorted(SHARED_KEYS.items())
        ],
        "max_bytes": MAX_BYTES,
    }
