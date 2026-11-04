"""選單：套用新分類／還原（備份最近 5 版）＋ 我的最愛／最近用過（每人一份）。2026-10-23。

全站選單順序存 app_settings.nav_layout（管理員拖拉存的自訂版面）。新分類上線時**不覆蓋**自訂版面：
管理員在「選單編輯」按「套用新分類」才換，換之前把當下版面（可能是 null＝內建預設）備份進
app_settings.nav_layout_backups（最近 5 版、帶時間與操作人），「還原上一版」取回最新那份。

我的最愛：nav_favorite 表，以登入帳號為 key；登入停用時前端改存瀏覽器（畫面上會講），
這裡仍接受讀寫（key 用 _noauth），API 不會壞。
"""
from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime
from typing import Any

LAYOUT_KEY = "nav_layout"
BACKUP_KEY = "nav_layout_backups"
MAX_BACKUPS = 5
DEFAULT_FAVORITES = ["/changes", "/oncall", "/assets", "/datacenter", "/topology"]
MAX_ITEMS = 50
NOAUTH_USER = "_noauth"


def _get_json(conn, key: str):
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if not r or not r[0]:
        return None
    try:
        return json.loads(r[0])
    except (ValueError, TypeError):  # 壞掉的設定值：當沒有
        return None


def _set_json(conn, key: str, value) -> None:
    if value is None:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))


def backups(conn) -> list[dict]:
    return _get_json(conn, BACKUP_KEY) or []


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def apply_default(conn, by: str) -> dict:
    """先備份當下版面（null 也備份＝「當時是內建預設」），再清掉自訂版面＝改用內建新分類。"""
    cur = _get_json(conn, LAYOUT_KEY)
    bk = [{"at": _now(), "by": by, "layout": cur, "reason": "套用新分類前"}] + backups(conn)
    _set_json(conn, BACKUP_KEY, bk[:MAX_BACKUPS])
    _set_json(conn, LAYOUT_KEY, None)
    conn.commit()
    return {"ok": True, "backed_up": cur is not None, "backups": len(bk[:MAX_BACKUPS])}


def restore_latest(conn, by: str) -> dict:
    """取回最新一份備份（它會從備份清單移掉）；當下版面也先存一份，免得還原錯了回不來。"""
    bk = backups(conn)
    if not bk:
        raise ValueError("沒有可還原的備份")
    latest, rest = bk[0], bk[1:]
    cur = _get_json(conn, LAYOUT_KEY)
    # 當下版面放最前面：再按一次「還原上一版」就回到還原前（等於復原這次還原）
    rest = [{"at": _now(), "by": by, "layout": cur, "reason": "還原前的版面"}] + rest
    _set_json(conn, BACKUP_KEY, rest[:MAX_BACKUPS])
    _set_json(conn, LAYOUT_KEY, latest["layout"])
    conn.commit()
    return {"ok": True, "restored_at": latest["at"], "restored_by": latest.get("by"),
            "restored_default": latest["layout"] is None}


# ===== 我的最愛 =====

def ensure_table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS nav_favorite (username TEXT PRIMARY KEY, items TEXT, recent TEXT, "
                 "updated_at TEXT)")


def _clean(lst, cap: int) -> list[str]:
    out: list[str] = []
    for x in lst or []:
        if isinstance(x, str) and x.startswith("/") and x not in out and len(x) < 200:
            out.append(x)
    return out[:cap]


def get_favorites(conn, username: str | None) -> dict[str, Any]:
    ensure_table(conn)
    u = username or NOAUTH_USER
    r = conn.execute("SELECT items, recent FROM nav_favorite WHERE username = ?", (u,)).fetchone()
    if not r:
        return {"username": u, "items": list(DEFAULT_FAVORITES), "recent": [], "is_default": True}
    return {"username": u, "items": json.loads(r["items"] or "[]"), "recent": json.loads(r["recent"] or "[]"),
            "is_default": False}


def set_favorites(conn, username: str | None, items, recent=None) -> dict[str, Any]:
    ensure_table(conn)
    u = username or NOAUTH_USER
    cur = get_favorites(conn, u)
    items = _clean(items, MAX_ITEMS)
    recent = _clean(recent if recent is not None else cur["recent"], 5)
    conn.execute("INSERT INTO nav_favorite (username, items, recent, updated_at) VALUES (?,?,?,?) "
                 "ON CONFLICT(username) DO UPDATE SET items = excluded.items, recent = excluded.recent, "
                 "updated_at = excluded.updated_at",
                 (u, json.dumps(items, ensure_ascii=False), json.dumps(recent, ensure_ascii=False), _now()))
    conn.commit()
    return get_favorites(conn, u)


# ===== 2.x 左側選單：哪些群組展開（2026-10-27 使用者：「平常不需要全部展開，需要時再展開，像 Excel 的群組」）=====
V2_GROUP_NOS = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "⚙")


def ensure_v2_table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS nav_v2_group (username TEXT PRIMARY KEY, open_json TEXT, updated_at TEXT)")


def get_v2_open(conn, username: str | None) -> dict[str, Any]:
    """這個人展開的群組編號（沒存過＝空，全部收起；目前頁所在的群組由前端一律打開）。"""
    ensure_v2_table(conn)
    u = username or NOAUTH_USER
    r = conn.execute("SELECT open_json FROM nav_v2_group WHERE username = ?", (u,)).fetchone()
    return {"username": u, "open": json.loads(r["open_json"] or "[]") if r else [], "is_default": not r}


def set_v2_open(conn, username: str | None, groups) -> dict[str, Any]:
    ensure_v2_table(conn)
    u = username or NOAUTH_USER
    clean = [g for g in V2_GROUP_NOS if g in set(groups or [])]
    conn.execute("INSERT INTO nav_v2_group (username, open_json, updated_at) VALUES (?,?,?) ON CONFLICT(username) DO UPDATE "
                 "SET open_json = excluded.open_json, updated_at = excluded.updated_at",
                 (u, json.dumps(clean, ensure_ascii=False), _now()))
    conn.commit()
    return get_v2_open(conn, u)


# ===== 2.x 左側選單的全站順序（2.042.21，使用者：V2 選單也要能拖拉，V43 移植）=====
# 預設版面只有一份：前端 composables/useNavV2.ts 的 V2_NAV。這裡只存管理員的「覆寫」：
#   [{"no": "<群組編號>", "items": ["/v2/…", …]}, …]   ← 群組順序＋每組放哪些項目（用網址當 key）
# 跟舊選單 nav_layout 分開存（V2_LAYOUT_KEY），舊的完全不動。後端不知道程式裡有哪些項目，
# 所以只檢查形狀與大小；「程式已移除的項目丟掉、程式新增的項目自動放回預設群組」在前端合併時做（utils/navV2Merge.ts）。
V2_LAYOUT_KEY = "nav_layout_v2"
V2_BACKUP_KEY = "nav_layout_v2_backups"
V2_META_KEY = "nav_layout_v2_meta"
V2_MAX_GROUPS = 30
V2_MAX_ITEMS = 300


def validate_v2_layout(layout) -> list[dict]:
    """回乾淨的覆寫；形狀不對丟 ValueError（訊息給人看）。同一個網址只能出現一次。"""
    if not isinstance(layout, list) or not layout:
        raise ValueError("版面要是非空的群組清單")
    if len(layout) > V2_MAX_GROUPS:
        raise ValueError(f"群組太多（上限 {V2_MAX_GROUPS}）")
    out, seen_g, seen_i, total = [], set(), set(), 0
    for g in layout:
        if not isinstance(g, dict):
            raise ValueError("每個群組要是物件")
        no = g.get("no")
        if not isinstance(no, str) or not no.strip() or len(no) > 8:
            raise ValueError("群組編號不對")
        if no in seen_g:
            raise ValueError(f"群組 {no} 重複")
        seen_g.add(no)
        items = g.get("items")
        if not isinstance(items, list):
            raise ValueError(f"群組 {no} 的項目要是清單")
        clean = []
        for it in items:
            if not isinstance(it, str) or not it.startswith("/v2") or len(it) > 200 or any(c in it for c in " \t\r\n<>\"'"):
                raise ValueError(f"項目網址不對：{str(it)[:40]}")
            if it in seen_i:
                raise ValueError(f"項目 {it} 出現兩次")
            seen_i.add(it)
            clean.append(it)
        total += len(clean)
        out.append({"no": no, "items": clean})
    if total > V2_MAX_ITEMS:
        raise ValueError(f"項目太多（上限 {V2_MAX_ITEMS}）")
    return out


def get_v2_layout(conn) -> dict[str, Any]:
    meta = _get_json(conn, V2_META_KEY) or {}
    return {"layout": _get_json(conn, V2_LAYOUT_KEY), "updated_at": meta.get("at"), "updated_by": meta.get("by")}


def _backup_v2(conn, by: str, reason: str) -> None:
    cur = _get_json(conn, V2_LAYOUT_KEY)
    bk = [{"at": _now(), "by": by, "layout": cur, "reason": reason}] + (_get_json(conn, V2_BACKUP_KEY) or [])
    _set_json(conn, V2_BACKUP_KEY, bk[:MAX_BACKUPS])


def set_v2_layout(conn, layout, by: str) -> dict[str, Any]:
    clean = validate_v2_layout(layout)
    _backup_v2(conn, by, "存新順序前")
    _set_json(conn, V2_LAYOUT_KEY, clean)
    _set_json(conn, V2_META_KEY, {"at": _now(), "by": by})
    conn.commit()
    return get_v2_layout(conn)


def reset_v2_layout(conn, by: str) -> dict[str, Any]:
    """回到程式內建預設：先備份目前的覆寫（最近 5 版），再清掉。"""
    had = _get_json(conn, V2_LAYOUT_KEY) is not None
    _backup_v2(conn, by, "回到預設前")
    _set_json(conn, V2_LAYOUT_KEY, None)
    _set_json(conn, V2_META_KEY, {"at": _now(), "by": by})
    conn.commit()
    return {**get_v2_layout(conn), "had_override": had}


# ===== 新舊選單編號對照（xlsx）=====

MAPPING_COLS = ["項次", "功能", "路徑", "舊編號（舊預設）", "目前畫面編號", "新編號（新分類）", "新群組"]


def mapping_xlsx(rows: list[dict]) -> io.BytesIO:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "新舊選單編號對照"
    for c, name in enumerate(MAPPING_COLS, 1):
        cell = ws.cell(1, c, name)
        cell.fill = PatternFill("solid", fgColor="00806A")
        cell.font = Font(color="FFFFFF", bold=True, size=10)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[cell.column_letter].width = max(10, min(28, len(name) + 6))
    keys = ["no", "label", "to", "old_no", "cur_no", "new_no", "new_group"]
    for i, r in enumerate(rows, 2):
        for c, k in enumerate(keys, 1):
            ws.cell(i, c, r.get(k, ""))
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
