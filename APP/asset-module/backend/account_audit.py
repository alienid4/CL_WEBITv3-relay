"""帳號盤點：匯入上次盤點表、下載範例、匯出本次盤點（標準／全部）（2026-09-18）。

## 為什麼要有這支

使用者：「上次盤點的，在匯入的地方做一個帳號盤點匯入／範例／及盤點後的匯出」
「帳號盤點格式是用這個為準，其他的類似 sudo 等算內部管理用，匯出可選標準 A-R，跟全部兩種」。

## ⚠️ 欄位與規則一律用 account_export（只有一套）

2026-09-03 使用者已給過公司 18 欄格式，`account_export.py` 早就做好「標準帳號盤點／全匯出」
（合規表上那兩顆鈕）。這支第一版（v1.233）沒先查到，自己又訂了一套欄位與 type 規則，而且
**違反使用者 09-03 的決定**：他明講 3／4／6「目前沒有邏輯，都要人工判斷」，既有程式只自動填
1／2／5／7；第一版卻用帳號名去猜 4、6、3。v1.235 起：
- 欄位＝`account_export.STANDARD_COLUMNS`（18 欄；截圖隱藏的 H 欄＝password，固定 x；
  截圖被截斷的「type_」＝type_id）
- 每一列的值＝`account_export.standard_rows()`（部門／窗口先查業務系統對照表、login_status 三態、
  type 只填確定的四種）；全部＝`account_export.full_rows()`
- 這支**只加今天新的三件事**：
  1. 上次盤點填過的 type_id／type_info 一律沿用（人判斷過的比系統規則優先）
  2. 匯入檔案裡系統不認得的欄照樣保留，值沿用上次同一個帳號
  3. 跟上次比較：新增／不變／有變更（列欄位）／上次有這次沒有；整台沒收集到的標
     「本次未盤點到這台」，不說成帳號被刪

## 格式以使用者的檔案為準

匯出「標準」的欄位順序照**最後一次匯入的檔案表頭**；沒匯入過就用 STANDARD_COLUMNS。
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime

import account_export

#: 標準欄位（跟合規表「標準帳號盤點」同一份，不另訂）
STANDARD_DEFAULT = list(account_export.STANDARD_COLUMNS)
KNOWN = set(STANDARD_DEFAULT)
#: 使用者檔案裡可能出現的別名（Excel 欄寬不夠截斷、或舊版欄名）→ 標準欄名
HEADER_ALIAS = {"type_": "type_id", "type": "type_id"}
#: 比較時看哪些欄有沒有變
COMPARE_FIELDS = ("uid", "gid", "gecos", "home", "shell", "login_status")
CMP_COLS = ["與上次盤點比較", "變更欄位"]

#: 代碼表：account_export.TYPE_INFO（09-03）＋使用者 09-18 提供的 8 未知待查。
#: 系統**只自動填 1／2／5／7**（account_export.classify_type）；其餘人工判斷。
TYPE_TABLE = [(str(k), v) for k, v in sorted(account_export.TYPE_INFO.items())] + [("8", "未知待查")]
FALLBACK_TYPES = {info: code for code, info in TYPE_TABLE}

SQL = [
    """CREATE TABLE IF NOT EXISTS account_audit_batch (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        imported_at TEXT NOT NULL, file_name TEXT, imported_by TEXT,
        row_count INTEGER, skipped_count INTEGER, host_count INTEGER,
        header_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS account_audit_row (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id INTEGER NOT NULL,
        hostname TEXT, ip TEXT, username TEXT NOT NULL,
        data_json TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_account_audit_row_batch ON account_audit_row(batch_id)",
]


def _ensure(conn) -> None:
    for s in SQL:
        conn.execute(s)


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)                       # Excel 把 0 讀成 0.0
    return str(v).strip()


def _norm_header(h) -> str:
    k = _s(h).lower()
    return HEADER_ALIAS.get(k, k)


# ===== 讀檔 =====

def _read_rows(data: bytes, filename: str) -> list[list]:
    name = (filename or "").lower()
    if name.endswith(".webit3dump") or data[:2] == b"\x1f\x8b":
        # 本系統匯出的 dump（gzip JSON）：整包搬到另一台再匯回
        import import_export
        body = import_export.read_dump(data)
        if body.get("source") != "account_audit":
            raise ValueError(f"這是「{body.get('source')}」的 dump，不是帳號盤點的")
        return [list(body.get("headers") or [])] + [list(r) for r in body.get("rows") or []]
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    for enc in ("utf-8-sig", "cp950"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("檔案編碼認不得（試過 UTF-8、Big5），請另存成 xlsx 或 UTF-8 CSV")
    return list(csv.reader(io.StringIO(text)))


def parse(data: bytes, filename: str) -> tuple[list[str], list[dict], list[dict]]:
    """回 (表頭順序, 有效列, 略過的列＋原因)。表頭列：第一個同時有 username 與
    hostname／ip_addr 的列（上方可以有標題列）。**隱藏欄也會讀到**（openpyxl 不管隱藏）。"""
    rows = _read_rows(data, filename)
    hdr_i = None
    for i, r in enumerate(rows[:30]):
        cells = {_norm_header(c) for c in r}
        if "username" in cells and ({"hostname", "ip_addr"} & cells):
            hdr_i = i
            break
    if hdr_i is None:
        raise ValueError("找不到表頭列：要有 username，以及 hostname 或 ip_addr 其中一欄（可下載範例對照）")
    header = [_norm_header(c) for c in rows[hdr_i]]
    while header and not header[-1]:
        header.pop()                                  # 右邊的空白欄
    good, skipped = [], []
    for n, r in enumerate(rows[hdr_i + 1:], start=hdr_i + 2):
        rec = {h: (_s(r[j]) if j < len(r) else "") for j, h in enumerate(header) if h}
        if not any(rec.values()):
            continue
        if not rec.get("username"):
            skipped.append({"row": n, "reason": "沒有 username"})
            continue
        if not rec.get("hostname") and not rec.get("ip_addr"):
            skipped.append({"row": n, "reason": "hostname 與 ip_addr 都空白，認不出是哪台"})
            continue
        good.append(rec)
    return [h for h in header if h], good, skipped


def import_file(conn, data: bytes, filename: str, by: str | None) -> dict:
    header, good, skipped = parse(data, filename)
    if not good:
        raise ValueError("沒有任何有效列" + (f"（略過 {len(skipped)} 列）" if skipped else ""))
    _ensure(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hosts = {(r.get("hostname") or "").lower() + "|" + (r.get("ip_addr") or "") for r in good}
    cur = conn.execute(
        "INSERT INTO account_audit_batch (imported_at, file_name, imported_by, row_count, "
        "skipped_count, host_count, header_json) VALUES (?,?,?,?,?,?,?)",
        (now, filename, by, len(good), len(skipped), len(hosts), json.dumps(header, ensure_ascii=False)))
    bid = cur.lastrowid
    for r in good:
        conn.execute("INSERT INTO account_audit_row (batch_id, hostname, ip, username, data_json) "
                     "VALUES (?,?,?,?,?)",
                     (bid, r.get("hostname"), r.get("ip_addr"), r["username"],
                      json.dumps(r, ensure_ascii=False)))
    conn.commit()
    return {"batch_id": bid, "rows": len(good), "hosts": len(hosts), "header": header,
            "unknown_columns": [h for h in header if h not in KNOWN],
            "skipped": skipped[:50], "skipped_count": len(skipped),
            "type_map": type_map(conn, bid)}


# ===== 狀態 =====

def latest_batch(conn) -> dict | None:
    _ensure(conn)
    r = conn.execute("SELECT * FROM account_audit_batch ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return None
    d = dict(r)
    d["header"] = json.loads(d.pop("header_json") or "[]")
    return d


def history(conn, limit: int = 10) -> list[dict]:
    _ensure(conn)
    return [{k: v for k, v in dict(r).items() if k != "header_json"} for r in conn.execute(
        "SELECT * FROM account_audit_batch ORDER BY id DESC LIMIT ?", (limit,))]


def _batch_rows(conn, bid: int) -> list[dict]:
    return [json.loads(r["data_json"]) for r in conn.execute(
        "SELECT data_json FROM account_audit_row WHERE batch_id = ? ORDER BY id", (bid,))]


def baseline_rows(conn) -> tuple[list[str], list[list]]:
    """目前的比較基準（最新一批）：(表頭, 每列)。給共用的 Excel／dump 匯出用。"""
    b = latest_batch(conn)
    if not b:
        return list(STANDARD_DEFAULT), []
    header = b.get("header") or list(STANDARD_DEFAULT)
    return header, [[d.get(h, "") for h in header] for d in _batch_rows(conn, b["id"])]


def type_map(conn, batch_id: int | None = None) -> dict[str, str]:
    """type_info → type_，從匯入的檔案學來。同一個說明對到多個代碼時取最多的。"""
    _ensure(conn)
    out = dict(FALLBACK_TYPES)
    q = "SELECT data_json FROM account_audit_row" + (" WHERE batch_id = ?" if batch_id else "")
    tally: dict[str, dict[str, int]] = {}
    for r in conn.execute(q, (batch_id,) if batch_id else ()):
        d = json.loads(r["data_json"])
        info, code = d.get("type_info") or "", d.get("type_") or ""
        if info and code:
            tally.setdefault(info, {}).setdefault(code, 0)
            tally[info][code] += 1
    for info, codes in tally.items():
        out[info] = max(codes.items(), key=lambda x: x[1])[0]
    return out


# ===== 匯出 =====

def _host_of(ip, hostname) -> str:
    return (ip or "").strip() or (hostname or "").strip().lower()


def build_export(conn, mode: str = "standard") -> dict:
    """mode：standard（照最後匯入的表頭；值＝account_export.standard_rows）
    或 all（account_export.full_rows 的全部欄位）；兩者都附比較。"""
    _ensure(conn)
    base_b = latest_batch(conn)
    base: dict[tuple, dict] = {}
    if base_b:
        for d in _batch_rows(conn, base_b["id"]):
            base[(_host_of(d.get("ip_addr"), d.get("hostname")), d.get("username") or "")] = d

    if mode == "all":
        cur, full_cols = account_export.full_rows(conn)
        cur = [dict(r) for r in cur]
        for r in cur:                    # 全匯出的鍵名跟標準不同，比較要用的補上
            r.setdefault("ip_addr", r.get("ip"))
        header = list(full_cols)
        summary = {"rows": len(cur)}
    else:
        cur, summary = account_export.standard_rows(conn)
        header = (base_b or {}).get("header") or list(STANDARD_DEFAULT)

    out_rows, seen, cur_hosts = [], set(), set()
    counts = {"新增": 0, "不變": 0, "有變更": 0, "上次有這次沒有": 0, "本次未盤點到這台": 0}
    carried = 0
    for rec in cur:
        host = _host_of(rec.get("ip_addr"), rec.get("hostname"))
        k = (host, rec.get("username") or "")
        seen.add(k)
        cur_hosts.add(host)
        prev = base.get(k)
        # 1. 上次盤點有填 type 就沿用——人判斷過的比系統規則優先（系統只認得 1／2／5／7，
        #    3／4／6 本來就要人判斷；人把 7 改成 3 也是有理由的）
        if prev and _s(prev.get("type_id")):
            rec["type_id"] = prev.get("type_id")
            rec["type_info"] = prev.get("type_info") or dict(TYPE_TABLE).get(_s(prev.get("type_id")), "")
            carried += 1
        # 2. 系統不認得的欄（使用者檔案自己的欄），沿用上次同一個帳號的值
        for col in header:
            if col not in rec:
                rec[col] = (prev or {}).get(col, "")
        # 3. 比較
        if not base_b:
            cmp, changed = "（沒有匯入過上次盤點，無從比較）", ""
        elif not prev:
            cmp, changed = "新增", ""
        else:
            diff = [f for f in COMPARE_FIELDS if _s(prev.get(f)) != _s(rec.get(f))]
            cmp, changed = ("有變更", "、".join(diff)) if diff else ("不變", "")
        if cmp in counts:
            counts[cmp] += 1
        out_rows.append({"rec": rec, "cmp": cmp, "changed": changed})

    # 上次有、這次沒有：分清楚「帳號不見了」與「這台根本沒收集到」
    for k, prev in base.items():
        if k in seen:
            continue
        cmp = "上次有這次沒有" if k[0] in cur_hosts else "本次未盤點到這台"
        counts[cmp] += 1
        rec = dict(prev)
        rec.setdefault("ip", prev.get("ip_addr"))
        out_rows.append({"rec": rec, "cmp": cmp, "changed": ""})

    cols = header + (CMP_COLS if mode == "all" else [])
    table = []
    for r in out_rows:
        row = [_s(r["rec"].get(c, "")) for c in header]
        if mode == "all":
            row += [r["cmp"], r["changed"]]
        table.append(row)
    unclassified = sum(1 for r in out_rows if r["rec"].get("username") and _s(r["rec"].get("type_id")) == ""
                       and r["cmp"] not in ("上次有這次沒有", "本次未盤點到這台"))
    return {"columns": cols, "rows": table,
            "compare": [(r["rec"], r["cmp"], r["changed"]) for r in out_rows],
            "baseline": base_b, "counts": counts, "header": header,
            "type_carried": carried, "unclassified_type": unclassified, "summary": summary}


def _style_header(ws) -> None:
    from openpyxl.styles import Font, PatternFill
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="0B7A5B")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def export_xlsx(conn, mode: str = "standard") -> tuple[io.BytesIO, dict]:
    from openpyxl import Workbook
    res = build_export(conn, mode)
    wb = Workbook()
    ws = wb.active
    ws.title = "帳號盤點" if mode != "all" else "帳號全匯出"
    ws.append(res["columns"])
    for r in res["rows"]:
        ws.append(r)
    _style_header(ws)
    if mode != "all":
        # 標準版主分頁只有標準欄；比較結果另開分頁，不弄髒要交出去的那張
        wc = wb.create_sheet("與上次比較")
        wc.append(["hostname", "ip_addr", "username", "與上次盤點比較", "變更欄位"])
        for rec, cmp, changed in res["compare"]:
            wc.append([rec.get("hostname", ""), rec.get("ip_addr") or rec.get("ip", ""),
                       rec.get("username", ""), cmp, changed])
        _style_header(wc)
    b = res["baseline"]
    wn = wb.create_sheet("說明")
    for line in [
        f"匯出時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　版本：{'全部欄位' if mode == 'all' else '盤點報告（標準格式）'}",
        "比較基準：" + (f"第 {b['id']} 批（{b['imported_at']} 匯入，{b['file_name']}）" if b else "無（沒有匯入過上次盤點）"),
        "小計：" + "、".join(f"{k} {v}" for k, v in res["counts"].items()),
        f"type_id：系統只自動填確定的 1 最高權限／2 系統預設／5 自動化盤點（本系統收集帳號）／7 人員使用；"
        f"3／4／6 需人工判斷（使用者 2026-09-03 決定）。本次沿用上次人工填的 {res['type_carried']} 筆，"
        f"仍待人工填 {res['unclassified_type']} 筆。",
        "欄位順序照最後一次匯入的檔案；系統不認得的欄沿用上次同一個帳號的值。",
        "ap_department／ap_owner 先查業務系統對照表，查不到才用資產的使用單位／使用者；",
        "department＝盤點單位處別＋部門；owner＝保管者；login_status：可登入／無法登入／未採集。",
        "「本次未盤點到這台」＝這台這次沒收集到帳號資料，不代表帳號被刪除。",
        "標準版的「帳號盤點」分頁修正後，可直接當下一輪的「上次盤點」匯回。",
    ]:
        wn.append([line])
    wn.column_dimensions["A"].width = 120
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf, res


def template_xlsx() -> io.BytesIO:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "帳號盤點"
    ws.append(STANDARD_DEFAULT)
    sample = {
        "system_id": "N-000", "system": "範例系統", "ap_department": "金融交易資訊部", "ap_owner": "王小明",
        "hostname": "SVR-EXAMPLE-01", "ip_addr": "192.0.2.11", "password": "x",
        "department": "資訊管理處資訊架構部", "owner": "李小華",
    }
    for user, uid, home, shell, tid, info, login in (
            ("root", 0, "/root", "/bin/bash", 1, "最高權限帳號", "可登入"),
            ("bin", 1, "/bin", "/sbin/nologin", 2, "系統預設", "無法登入")):
        row = dict(sample, username=user, uid=uid, gid=uid, gecos=user, home=home, shell=shell,
                   type_id=tid, type_info=info, login_status=login)
        ws.append([row.get(c, "") for c in STANDARD_DEFAULT])
    _style_header(ws)
    wt = wb.create_sheet("type 代碼")
    wt.append(["type_id", "type_info"])
    for code, info in TYPE_TABLE:
        wt.append([int(code), info])
    _style_header(wt)
    wn = wb.create_sheet("說明")
    for line in [
        "帳號盤點匯入範例（以上為假資料，請換成實際內容）",
        "第一列是表頭，欄名需一致（不分大小寫）；表頭上方可以有標題列，系統會自己找到表頭。",
        "必填：username，以及 hostname 或 ip_addr 其中一個。其他欄可空白；多出來的欄會照樣保留。",
        "匯出「盤點報告」時，欄位順序照你最後一次匯入的檔案——你的格式為準。",
        "type_id：系統只自動填 1／2／5／7；3／4／6 需人工判斷。上次人工填過的，下次匯出會沿用。",
        "每次匯入存成一個批次，不覆蓋舊的；最新一批就是「上次盤點」，匯出時拿來比較。",
    ]:
        wn.append([line])
    wn.column_dimensions["A"].width = 110
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
