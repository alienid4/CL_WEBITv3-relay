"""弱點報告解析與逐台對照（2026-09-21 使用者：

> 「以後每一台就可以看到有修過什麼弱點了吧? 多一個弱點管理紀錄」

## 為什麼要在後端再解析一次

那支工具（public/patch）在瀏覽器裡解析 Excel，畫的是「全站看板」。
但使用者要的是**在資產詳細頁看這一台**——那就得查得動單台，也就得一筆一筆進 DB。
整包 blob 存著（patch_store）查不了單台，兩件事不衝突：blob 給工具自己還原畫面用，
這裡解析出來的列給資產頁與差異比對用。

## 比對鍵（哪兩筆算「同一個弱點」）

`Plugin ID + Host + Protocol + Port`——Nessus 的天然主鍵。
不能只用「主機＋弱點名稱」：同一台同一個弱點開在 443 與 8443 是兩回事，
併成一筆會讓「修好了幾個」直接算錯。

沒有 Plugin ID 的表（IoT、BitSight 那幾張欄位長得不一樣）**標成無法比對**，
不硬湊一個鍵。湊出來的鍵會讓差異數字看起來很漂亮但全是假的。

## 「修過什麼」怎麼判

優先用 Excel 自己的「結案狀態／結案日期」——那是人填的、有人負責的事實。
**不從批次差異反推**「這次沒看到所以修好了」：那筆弱點消失也可能是這次根本沒掃那台
（見 scan 的未涵蓋），兩者要做的事完全不同。差異比對另外走，結論分三類不是兩類。

## Host 對不回資產怎麼辦

不猜、不丟掉：標成未對應並且列得出來。「這台沒有弱點」與「這台不在弱點報告裡」
是兩件事，畫面要分得出來——不分的話，一台從沒被掃過的機器會顯示成「很乾淨」。
"""
from __future__ import annotations

import io
import re
import sqlite3
from datetime import datetime

BATCH_SQL = """
CREATE TABLE IF NOT EXISTS vuln_batch (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name   TEXT,
    imported_by TEXT,
    imported_at TEXT NOT NULL,
    status      TEXT NOT NULL,        -- importing / ready / failed
    rows        INTEGER DEFAULT 0,
    matched     INTEGER DEFAULT 0,
    sheets      TEXT,
    note        TEXT
)
"""

FINDING_SQL = """
CREATE TABLE IF NOT EXISTS vuln_finding (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id      INTEGER NOT NULL,
    sheet         TEXT,
    fingerprint   TEXT,               -- plugin|host|proto|port；判不出來為 NULL
    plugin_id     TEXT,
    host          TEXT,
    protocol      TEXT,
    port          TEXT,
    name          TEXT,
    severity      TEXT,
    due_date      TEXT,
    overdue       TEXT,
    unit          TEXT,
    owner         TEXT,
    closed        INTEGER DEFAULT 0,  -- 1 = 已結案
    closed_at     TEXT,
    note          TEXT,
    asset_serial  TEXT,               -- 對回資產清冊；對不到為 NULL
    match_by      TEXT                -- ip / hostname / 對不到
)
"""

#: 嚴重度：中／英／BitSight 各種寫法統一，保留原值在 severity_raw 的概念由前端處理。
_SEV = {
    "critical": "Critical", "嚴重": "Critical", "極高": "Critical",
    "high": "High", "高": "High",
    "medium": "Medium", "中": "Medium", "中等": "Medium",
    "low": "Low", "低": "Low",
    "info": "Info", "informational": "Info", "資訊": "Info",
}

#: 欄位別名。各張表欄位不同，依名稱對應而不是依位置——依位置一改欄序就全錯。
_ALIAS: dict[str, tuple[str, ...]] = {
    "plugin_id": ("plugin id", "pluginid", "plugin_id", "弱點編號", "cve"),
    "host": ("host", "主機", "主機名稱", "ip", "ip位址", "資產ip"),
    "protocol": ("protocol", "協定"),
    "port": ("port", "埠", "通訊埠"),
    "name": ("name", "弱點名稱", "弱點", "項目", "finding", "title"),
    "severity": ("severity", "risk", "嚴重度", "嚴重性", "風險等級",
                 "發現嚴重性 finding severity", "finding severity"),
    "due_date": ("修補期限", "首次展延上限", "到期日", "期限"),
    "overdue": ("逾期狀態", "逾期"),
    "unit": ("負責單位", "部門", "權責單位"),
    "owner": ("負責人", "負責人員", "處理人"),
    "closed_status": ("結案狀態", "狀態", "處理狀態"),
    "closed_at": ("結案日期", "結案時間"),
    "note": ("備註", "說明"),
}

#: 判「已結案」。先比否定詞再比肯定詞——**「未結案」裡面有「結案」**，
#: 只做子串比對會把所有未結案都判成已結案，那是「弱點都修完了」這種最危險的誤讀。
#: 2026-09-21 實測時就是這樣被抓到的。
_OPEN_WORDS = ("未結案", "尚未", "未處理", "處理中", "未修復", "open", "not closed", "pending")
_CLOSED_WORDS = ("已結案", "結案", "已修復", "完成", "closed", "resolved", "fixed")
_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(BATCH_SQL)
    conn.execute(FINDING_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vuln_finding_asset "
                 "ON vuln_finding(asset_serial, batch_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vuln_finding_fp "
                 "ON vuln_finding(batch_id, fingerprint)")


def _norm_header(v) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip().lower())


def _severity(v) -> str:
    s = str(v or "").strip()
    return _SEV.get(s.lower(), s)


def _is_closed(v) -> int:
    s = str(v or "").strip().lower()
    if not s:
        return 0                                  # 沒填不算結案，不替人填答案
    if any(w in s for w in _OPEN_WORDS):
        return 0                                  # 否定詞優先：「未結案」不是「結案」
    return 1 if any(w in s for w in _CLOSED_WORDS) else 0


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    return str(v).strip()


def _map_columns(headers: list) -> dict[str, int]:
    """欄名 → 欄位索引。找不到的欄位就是沒有，不要硬塞第一個。"""
    got: dict[str, int] = {}
    norm = [_norm_header(h) for h in headers]
    for field, names in _ALIAS.items():
        for i, h in enumerate(norm):
            if not h:
                continue
            if h in names or any(h.startswith(n) for n in names):
                got.setdefault(field, i)
                break
    return got


def parse(blob: bytes) -> list[dict]:
    """把整份 Excel 解析成一筆一筆。只讀「數字-」開頭的工作表，跟那支工具同一條規則。"""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    out: list[dict] = []
    for ws in wb.worksheets:
        title = str(ws.title or "")
        if not re.match(r"^\d+\s*-", title):
            continue
        rows = ws.iter_rows(values_only=True)
        try:
            headers = list(next(rows))
        except StopIteration:
            continue
        cols = _map_columns(headers)
        if "host" not in cols:
            continue                     # 連主機欄都沒有，這張表對不回資產
        for r in rows:
            host = _cell(r[cols["host"]]) if cols["host"] < len(r) else ""
            if not host:
                continue

            def g(field):
                i = cols.get(field)
                return _cell(r[i]) if i is not None and i < len(r) else ""

            plugin = g("plugin_id")
            proto, port = g("protocol"), g("port")
            # 判不出鍵就留 None——寧可標「無法比對」，也不要湊一個假的
            fp = (f"{plugin}|{host}|{proto}|{port}".lower() if plugin else None)
            out.append({
                "sheet": title, "fingerprint": fp, "plugin_id": plugin or None,
                "host": host, "protocol": proto or None, "port": port or None,
                "name": g("name") or None, "severity": _severity(g("severity")) or None,
                "due_date": g("due_date") or None, "overdue": g("overdue") or None,
                "unit": g("unit") or None, "owner": g("owner") or None,
                "closed": _is_closed(g("closed_status")), "closed_at": g("closed_at") or None,
                "note": g("note") or None,
            })
    return out


def _asset_index(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """(ip → serial, 主機短名大寫 → serial)。多筆登記取第一筆，對照只是為了「哪一台」。"""
    import manage_state

    by_ip: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for r in conn.execute("SELECT asset_serial, hostname, ip FROM hardware"):
        for ip in manage_state.ips_of(r["ip"]):
            by_ip.setdefault(ip, r["asset_serial"])
        h = (r["hostname"] or "").strip()
        if h:
            by_name.setdefault(h.upper(), r["asset_serial"])
            by_name.setdefault(h.split(".")[0].upper(), r["asset_serial"])
    return by_ip, by_name


def _match(host: str, by_ip: dict, by_name: dict) -> tuple[str | None, str]:
    h = (host or "").strip()
    if not h:
        return None, "對不到"
    if _IPV4.match(h):
        s = by_ip.get(h)
        return (s, "ip") if s else (None, "對不到")
    s = by_name.get(h.upper()) or by_name.get(h.split(".")[0].upper())
    return (s, "hostname") if s else (None, "對不到")


def import_workbook(conn: sqlite3.Connection, blob: bytes,
                    file_name: str | None = None, by: str | None = None) -> dict:
    """解析並存成一個批次。

    原子化：先寫 status='importing'，全部寫完才改 'ready'。差異比對只看 ready 的批次——
    不然匯入到一半掛掉，那半批會被當成「本週」拿去比，差異整個是錯的。
    """
    _ensure(conn)
    now = _now()
    cur = conn.execute(
        "INSERT INTO vuln_batch (file_name, imported_by, imported_at, status) VALUES (?,?,?,?)",
        (file_name, by, now, "importing"))
    batch_id = cur.lastrowid
    conn.commit()
    try:
        rows = parse(blob)
        by_ip, by_name = _asset_index(conn)
        matched = 0
        payload = []
        sheets = []
        for d in rows:
            serial, how = _match(d["host"], by_ip, by_name)
            if serial:
                matched += 1
            if d["sheet"] not in sheets:
                sheets.append(d["sheet"])
            payload.append((
                batch_id, d["sheet"], d["fingerprint"], d["plugin_id"], d["host"],
                d["protocol"], d["port"], d["name"], d["severity"], d["due_date"],
                d["overdue"], d["unit"], d["owner"], d["closed"], d["closed_at"],
                d["note"], serial, how))
        conn.executemany(
            "INSERT INTO vuln_finding (batch_id, sheet, fingerprint, plugin_id, host, "
            "protocol, port, name, severity, due_date, overdue, unit, owner, closed, "
            "closed_at, note, asset_serial, match_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
        conn.execute(
            "UPDATE vuln_batch SET status='ready', rows=?, matched=?, sheets=? WHERE id=?",
            (len(payload), matched, "、".join(sheets), batch_id))
        conn.commit()
        return {"batch_id": batch_id, "rows": len(payload), "matched": matched,
                "unmatched": len(payload) - matched, "sheets": sheets}
    except Exception as exc:  # noqa: BLE001 - 失敗要留下痕跡，不可以裝作沒發生
        conn.execute("UPDATE vuln_batch SET status='failed', note=? WHERE id=?",
                     (str(exc)[:500], batch_id))
        conn.execute("DELETE FROM vuln_finding WHERE batch_id=?", (batch_id,))
        conn.commit()
        raise


def latest_batch(conn: sqlite3.Connection) -> dict | None:
    _ensure(conn)
    r = conn.execute(
        "SELECT * FROM vuln_batch WHERE status='ready' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def batches(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    _ensure(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM vuln_batch ORDER BY id DESC LIMIT ?", (limit,))]


def by_asset(conn: sqlite3.Connection, asset_serial: str) -> dict:
    """某一台的弱點：現在還沒結的、以及修過的。

    「這台沒有弱點」與「這台不在弱點報告裡」要分得出來——不分的話，
    一台從沒被掃過的機器會顯示成很乾淨，那是最危險的誤讀。
    """
    _ensure(conn)
    b = latest_batch(conn)
    if not b:
        return {"has_report": False, "batch": None, "open": [], "closed": [],
                "in_report": False}
    import manage_state

    serials = manage_state.expand_to_machines(conn, [asset_serial]) or [asset_serial]
    ph = ",".join("?" for _ in serials)
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM vuln_finding WHERE batch_id=? AND asset_serial IN ({ph}) "
        "ORDER BY closed, severity, due_date", (b["id"], *serials))]
    return {
        "has_report": True,
        "batch": {"id": b["id"], "file_name": b["file_name"],
                  "imported_by": b["imported_by"], "imported_at": b["imported_at"],
                  "rows": b["rows"], "matched": b["matched"]},
        "in_report": bool(rows),
        "open": [r for r in rows if not r["closed"]],
        "closed": [r for r in rows if r["closed"]],
    }


def diff(conn: sqlite3.Connection, new_id: int, old_id: int) -> dict:
    """兩個批次的差異。三類不是兩類——見模組說明。

    「這次沒看到」不等於「修好了」：那台這次可能根本沒被掃。所以只有**同一台在新批次
    仍然出現過**（代表這次有掃到它），它身上消失的弱點才算修復；整台都不見的另外列。
    """
    _ensure(conn)
    def fps(bid):
        return {r[0]: r for r in conn.execute(
            "SELECT fingerprint, host, name, severity, asset_serial, closed "
            "FROM vuln_finding WHERE batch_id=? AND fingerprint IS NOT NULL", (bid,))}

    new, old = fps(new_id), fps(old_id)
    hosts_new = {r[1] for r in new.values()}
    added = [new[k] for k in new.keys() - old.keys()]
    gone = [old[k] for k in old.keys() - new.keys()]
    fixed = [r for r in gone if r[1] in hosts_new]        # 那台這次有出現＝有掃到
    unknown = [r for r in gone if r[1] not in hosts_new]  # 整台都沒出現＝無法判定
    def pack(rs):
        return [{"fingerprint": r[0], "host": r[1], "name": r[2],
                 "severity": r[3], "asset_serial": r[4]} for r in rs]
    return {
        "new_id": new_id, "old_id": old_id,
        "added": pack(added), "fixed": pack(fixed), "unknown": pack(unknown),
        "counts": {"added": len(added), "fixed": len(fixed), "unknown": len(unknown),
                   "new_total": len(new), "old_total": len(old)},
        "no_fingerprint": conn.execute(
            "SELECT COUNT(*) FROM vuln_finding WHERE batch_id=? AND fingerprint IS NULL",
            (new_id,)).fetchone()[0],
    }


def unmatched_hosts(conn: sqlite3.Connection, batch_id: int | None = None) -> list[dict]:
    """對不回資產清冊的主機。這本身就是發現——清冊少一台，或弱點單寫錯主機名。"""
    _ensure(conn)
    if batch_id is None:
        b = latest_batch(conn)
        if not b:
            return []
        batch_id = b["id"]
    return [dict(r) for r in conn.execute(
        "SELECT host, COUNT(*) AS findings, SUM(CASE WHEN closed=0 THEN 1 ELSE 0 END) AS open_n "
        "FROM vuln_finding WHERE batch_id=? AND asset_serial IS NULL "
        "GROUP BY host ORDER BY open_n DESC, findings DESC", (batch_id,))]
