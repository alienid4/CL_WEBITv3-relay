"""非納管設備（人工豁免）——使用者 2026-09-16。

## 為什麼要有

納管漏斗裡有一批機器，**既不能納管、也不是下線**：客製化系統、Oracle 資料庫主機、
廠商維護不准動的機器。使用者原話：

> 「如果我發現這個沒辦法納管、也不是下線，譬如客製化系統或者是 Oracle 的話，
>   那我應該是寫『非納管設備』嗎？」

系統本來只有**自動**判不納管（`onboard_eligibility`：ESXi／OpenShift 節點／儲存設備），
判準是 OS 字串與 vCenter 匯入。客製系統、Oracle 這種**只有人知道**的情況沒有入口，
結果那些機器永遠掛在「要處理」裡，把真正要處理的淹掉。

## 設計

- **原因必填**：半年後有人問「這台為什麼不納管」要答得出來
- **可以取消**：軟刪（`removed_at`），不是 DELETE——誰標的、誰取消的都留著
- **不碰資產狀態**：豁免是「我們不去納管它」，不是「它不在了」。停用／報廢走
  `cia_pending.batch_set_status`（那個要同步回 CIA 清冊），這個純粹是我們自己的作業判斷，
  **不需要**改 CIA 清冊，所以不記 CIA 待異動
- **不影響可信度**：沒有機器證據就是沒有，標了豁免不代表資料變可信

被豁免的機器在納管狀態裡歸 `manage_state.EXEMPT`，不算「有問題」。
"""
from __future__ import annotations

from datetime import datetime

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS onboard_exempt (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_serial TEXT NOT NULL,
    kind         TEXT,               -- 常見原因分類，見 KINDS
    reason       TEXT NOT NULL,      -- 必填
    created_by   TEXT,
    created_at   TEXT NOT NULL,
    removed_at   TEXT,               -- 取消豁免（軟刪，保留紀錄）
    removed_by   TEXT
)
"""

#: 常見分類。給選單用，仍然要填自由文字原因——分類不能取代「為什麼」
KINDS = ("客製化系統", "資料庫主機（Oracle 等）", "廠商維護中", "OS 不支援", "資安政策限制", "其他")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure(conn) -> None:
    conn.execute(TABLE_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_onboard_exempt_serial "
                 "ON onboard_exempt(asset_serial, removed_at)")


def add(conn, serials: list[str], reason: str, kind: str | None, by: str | None) -> dict:
    """標記為非納管設備。已經標過的不重複標。"""
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("要填原因（例：客製化系統，廠商不准建帳號）")
    if kind and kind not in KINDS:
        raise ValueError(f"分類只能是：{'、'.join(KINDS)}")
    uniq = [s.strip() for s in dict.fromkeys(serials or []) if s and s.strip()]
    if not uniq:
        raise ValueError("沒有選任何資產")
    _ensure(conn)
    now = _now()
    added, already, not_found = [], [], []
    for s in uniq:
        if conn.execute("SELECT 1 FROM hardware WHERE asset_serial = ?", (s,)).fetchone() is None:
            not_found.append(s)
            continue
        if conn.execute("SELECT 1 FROM onboard_exempt WHERE asset_serial = ? AND removed_at IS NULL",
                        (s,)).fetchone():
            already.append(s)
            continue
        conn.execute("INSERT INTO onboard_exempt (asset_serial, kind, reason, created_by, created_at) "
                     "VALUES (?,?,?,?,?)", (s, kind, reason, by, now))
        added.append(s)
    conn.commit()
    return {"added": added, "already": already, "not_found": not_found}


def remove(conn, serials: list[str], by: str | None) -> int:
    """取消豁免：這台又要納管了。軟刪，紀錄留著。"""
    _ensure(conn)
    uniq = [s.strip() for s in dict.fromkeys(serials or []) if s and s.strip()]
    if not uniq:
        return 0
    ph = ",".join("?" for _ in uniq)
    cur = conn.execute(
        f"UPDATE onboard_exempt SET removed_at = ?, removed_by = ? "
        f"WHERE removed_at IS NULL AND asset_serial IN ({ph})", (_now(), by, *uniq))
    conn.commit()
    return cur.rowcount


def active_serials(conn) -> set[str]:
    """目前有效的豁免清單。納管統計、漏斗、清單頁都靠這支，不要各算各的。"""
    try:
        _ensure(conn)
        return {r[0] for r in conn.execute(
            "SELECT asset_serial FROM onboard_exempt WHERE removed_at IS NULL")}
    except Exception:  # noqa: BLE001 - 舊庫還沒有這張表時不要把整個統計拖垮
        return set()


def get(conn, asset_serial: str) -> dict | None:
    """這台現在有沒有被豁免；有的話原因是什麼（詳細頁顯示用）。"""
    _ensure(conn)
    cur = conn.execute(
        "SELECT * FROM onboard_exempt WHERE asset_serial = ? AND removed_at IS NULL "
        "ORDER BY id DESC LIMIT 1", (asset_serial,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def list_all(conn, include_removed: bool = False) -> list[dict]:
    _ensure(conn)
    where = "" if include_removed else "WHERE e.removed_at IS NULL "
    cur = conn.execute(
        "SELECT e.*, h.hostname, h.ip, h.os, h.device_model FROM onboard_exempt e "
        "LEFT JOIN hardware h ON h.asset_serial = e.asset_serial "
        f"{where}ORDER BY e.id DESC")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
