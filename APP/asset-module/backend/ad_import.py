"""AD 人員名單匯入（B-23）。

用途：把 AD 匯出的員工名單接進來，讓主機帳號對得到「這個帳號是誰的、哪個部門、
主管是誰」。關聯鍵是**員編**——主機帳號的 gecos 開頭就是員編
（例如 `01002861-王小明_某某部`），而 AD 的 `SamAccountName` 與 `employeeID` 都是員編。

## 這份資料是全體員工個資

檔案只能放 `DATA/`（已 gitignore），**不進版控、不進測試資料**。
本檔的測試資料一律自己編（張三／01000001），不用真名。

## 三個從實際範例讀出來、決定怎麼寫解析的重點

1. **`SamAccountName` == `employeeID`**（都是 `01000001` 這種格式）。主機帳號 gecos 開頭也是員編，
   所以員編是唯一穩定的關聯鍵。姓名會改、會同名，不能當鍵。

2. **`manager` 是 DN 字串**，而且**主管本人也是這份名單裡的一列**：

       CN=<主管姓名>,OU=cs7110000,OU=cs7000000,OU=<組織>,DC=<網域>

   所以要從 DN 取 `CN=` 的姓名 ＋ **第一個 `OU=` 的代碼**（`cs7110000` → `7110000`），
   再用「姓名＋部門代碼」兩個一起去比對名單裡的 `DisplayName` ＋ `department` 括號代碼。

   **只比姓名不行**：AD 遇到同名會在 CN 加後綴（`王小明2`），會對到錯的人。
   兩個鍵一起比才安全，對不上就列清單，**不准用近似比對硬湊**。

3. **`department` 含部門代碼**：`營運管理處結算交割部(7110000)`。
   拆成名稱與代碼兩欄——代碼是穩定識別，名稱會改。**拆不出來的保留原字串，不可丟掉。**
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ad_person (
    employee_id   TEXT PRIMARY KEY,   -- 員編（SamAccountName／employeeID）
    display_name  TEXT,
    upn           TEXT,
    mail          TEXT,
    dept_name     TEXT,               -- 部門名稱（代碼拆掉之後）
    dept_code     TEXT,               -- 部門代碼，穩定識別
    dept_raw      TEXT,               -- 原字串，拆不出來時整段留著
    manager_dn    TEXT,               -- 原始 DN，留著才查得回去
    manager_name  TEXT,               -- 從 DN 的 CN= 取
    manager_ou    TEXT,               -- 從 DN 第一個 OU= 取出的代碼
    manager_id    TEXT,               -- 對上名單後補的主管員編
    manager_mail  TEXT,               -- 對上名單後補的主管信箱
    manager_state TEXT,               -- 對上 / 對不上 / 同名疑慮 / 沒有主管
    enabled       INTEGER,            -- 1 啟用 0 停用（離職者還留主機帳號是稽核發現）
    imported_at   TEXT,
    imported_by   TEXT
)
"""

BATCH_SQL = """
CREATE TABLE IF NOT EXISTS ad_batch (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name   TEXT,
    imported_by TEXT,
    imported_at TEXT NOT NULL,
    rows        INTEGER DEFAULT 0,
    enabled_n   INTEGER DEFAULT 0,
    disabled_n  INTEGER DEFAULT 0,
    mgr_ok      INTEGER DEFAULT 0,
    mgr_fail    INTEGER DEFAULT 0,
    mgr_dupname INTEGER DEFAULT 0,
    note        TEXT
)
"""

#: 欄位別名。AD 匯出工具的標題大小寫、底線寫法各家不同，依名稱對不依位置。
_ALIAS = {
    "employee_id": ("samaccountname", "employeeid", "員編", "員工編號", "sam"),
    "display_name": ("displayname", "姓名", "名稱", "name", "cn"),
    "upn": ("userprincipalname", "upn"),
    "mail": ("mail", "email", "信箱", "電子郵件"),
    "manager_dn": ("manager", "主管", "manager_dn"),
    "dept_raw": ("department", "部門", "dept"),
    "enabled": ("enabled", "啟用", "狀態"),
}

#: 對上主管的狀態。刻意用中文，直接顯示在畫面上。
MGR_OK = "對上"
MGR_FAIL = "對不上"
MGR_DUP = "同名疑慮"
MGR_NONE = "沒有主管"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(TABLE_SQL)
    conn.execute(BATCH_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ad_name ON ad_person(display_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ad_dept ON ad_person(dept_code)")


def split_department(raw: str | None) -> tuple[str, str]:
    """`營運管理處結算交割部(7110000)` → (`營運管理處結算交割部`, `7110000`)。

    **拆不出來就整段當名稱、代碼留空**——不可以因為格式不合就把整欄丟掉。
    代碼才是穩定識別（部門改名很常見），但名稱是人看得懂的那個。
    """
    t = (raw or "").strip()
    if not t:
        return "", ""
    m = re.search(r"[（(]\s*([0-9A-Za-z]+)\s*[)）]\s*$", t)
    if not m:
        return t, ""
    return t[: m.start()].strip(), m.group(1).strip()


def parse_manager_dn(dn: str | None) -> tuple[str, str, bool]:
    """DN → (主管姓名, 第一個 OU 的代碼, CN 是否帶同名後綴)。

        CN=<主管姓名>,OU=cs7110000,OU=cs7000000,OU=<組織>,DC=...
        → ("<主管姓名>", "7110000", False)

    **第一個 OU 才是他所屬的部門**，後面的是上層組織。
    `cs7110000` 前面的字母前綴拿掉，跟 `department` 括號裡的代碼才對得上。

    CN 結尾是數字＝AD 遇到同名時加的後綴（`王小明2`），回傳第三個值標記出來——
    **那種一定要人看過**，自動對可能對到錯的人。
    """
    t = (dn or "").strip()
    if not t:
        return "", "", False
    cn = ""
    ou = ""
    for part in t.split(","):
        part = part.strip()
        low = part.lower()
        if not cn and low.startswith("cn="):
            cn = part[3:].strip()
        elif not ou and low.startswith("ou="):
            ou = part[3:].strip()
    code = re.sub(r"^[A-Za-z]+", "", ou).strip() if ou else ""
    suspicious = bool(cn) and cn[-1].isdigit()
    return cn, code, suspicious


def _norm(v) -> str:
    return "" if v is None else str(v).strip()


def _truthy(v) -> int | None:
    t = _norm(v).lower()
    if t in ("true", "1", "yes", "y", "啟用", "enabled"):
        return 1
    if t in ("false", "0", "no", "n", "停用", "disabled"):
        return 0
    return None


def _map_columns(headers: list) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, h in enumerate(headers):
        key = re.sub(r"\s+", "", _norm(h)).lower()
        for field, names in _ALIAS.items():
            if field in out:
                continue
            if key in names:
                out[field] = i
    return out


def parse(rows: list[list], width_hint: int | None = None) -> tuple[list[dict], int]:
    """把表格列解析成人員 dict。第一列是標題。回 (人員清單, 還原前導零幾筆)。"""
    if not rows:
        raise ValueError("檔案裡沒有任何資料")
    idx = _map_columns(rows[0])
    if "employee_id" not in idx:
        # 「這個檔根本不是表格」跟「是表格但少了員編那一欄」要分開講。
        # 2026-09-23 221 實測：丟一個純文字檔進來，訊息是「找不到員編欄位」——
        # 那會叫人去一個沒有欄位的檔案裡找欄位，方向整個錯。
        認得的欄 = [f for f in idx if f != "employee_id"]
        if not 認得的欄 and len(rows[0]) <= 1:
            raise ValueError(
                "這個檔讀不出表格——只有一欄、而且標題認不得任何已知欄位。"
                "AD 名單要是 .xlsx 或逗號分隔的 .csv，第一列是標題"
                "（至少要有 SamAccountName／employeeID）。"
                f"目前讀到的第一列是：{str(rows[0])[:80]}")
        raise ValueError(
            "找不到員編欄位（SamAccountName／employeeID）——"
            "員編是主機帳號對應到人的唯一關聯鍵，沒有它整份都對不上。"
            f"這個檔的標題讀到的是：{'、'.join(str(c) for c in rows[0][:8])}")
    # 先把整欄員編抓出來還原前導零，再逐列組——**必須整欄一起看**，
    # 因為寬度是從「有保住前導零的那些值」推出來的。
    ecol = idx["employee_id"]
    raw_ids = [_norm(r[ecol]) if ecol < len(r) else "" for r in rows[1:]]
    fixed_ids, padded, _w = restore_leading_zeros(raw_ids, width_hint)

    out = []
    for n, r in enumerate(rows[1:]):
        def g(field):
            i = idx.get(field)
            return _norm(r[i]) if i is not None and i < len(r) else ""
        emp = fixed_ids[n]
        if not emp:
            continue
        name, code = split_department(g("dept_raw"))
        mgr_name, mgr_ou, dup = parse_manager_dn(g("manager_dn"))
        out.append({
            "employee_id": emp,
            "display_name": g("display_name"),
            "upn": g("upn"),
            "mail": g("mail"),
            "dept_raw": g("dept_raw"),
            "dept_name": name,
            "dept_code": code,
            "manager_dn": g("manager_dn"),
            "manager_name": mgr_name,
            "manager_ou": mgr_ou,
            "manager_dup": dup,
            "enabled": _truthy(g("enabled")),
        })
    if not out:
        raise ValueError("讀得到標題，但沒有任何一列有員編")
    return out, padded


def resolve_managers(people: list[dict]) -> list[dict]:
    """用「姓名 ＋ 部門代碼」把主管 DN 對到名單裡的那一列，補上主管員編與信箱。

    為什麼兩個鍵一起比：AD 同名時 CN 會加後綴（`王小明2`），只比姓名會對到錯的人。
    DN 的第一個 OU 代碼跟 `department` 括號裡的代碼是同一個，所以兩者一起比才安全。

    **對不上就標對不上，不做近似比對。** 主管資訊拿來寄信、拿來簽核，
    對錯人比沒有更糟。
    """
    by_key: dict[tuple, list[dict]] = {}
    for p in people:
        if p["display_name"]:
            by_key.setdefault((p["display_name"], p["dept_code"]), []).append(p)

    for p in people:
        if not p["manager_dn"]:
            p["manager_state"] = MGR_NONE
            p["manager_id"] = p["manager_mail"] = ""
            continue
        if p["manager_dup"]:
            # CN 帶數字後綴＝AD 同名處理過的痕跡。**不自動對**，列出來讓人判斷。
            p["manager_state"] = MGR_DUP
            p["manager_id"] = p["manager_mail"] = ""
            continue
        hit = by_key.get((p["manager_name"], p["manager_ou"]), [])
        if len(hit) == 1:
            p["manager_state"] = MGR_OK
            p["manager_id"] = hit[0]["employee_id"]
            p["manager_mail"] = hit[0]["mail"]
        elif len(hit) > 1:
            # 同姓名同部門有兩個人——名單本身就分不出來，不可以挑一個
            p["manager_state"] = MGR_DUP
            p["manager_id"] = p["manager_mail"] = ""
        else:
            p["manager_state"] = MGR_FAIL
            p["manager_id"] = p["manager_mail"] = ""
    return people


def import_rows(conn: sqlite3.Connection, rows: list[list],
                file_name: str | None = None, by: str | None = None) -> dict:
    """匯入一份 AD 名單。整份取代（AD 名單是全量快照，不是增量）。"""
    _ensure(conn)
    # 寬度線索從主機帳號 gecos 來——那邊是純文字，前導零不會被 Excel 吃掉
    people_raw, padded = parse(rows, width_hint=gecos_id_width(conn))
    people = resolve_managers(people_raw)
    now = _now()
    conn.execute("DELETE FROM ad_person")
    conn.executemany(
        "INSERT INTO ad_person (employee_id, display_name, upn, mail, dept_name, dept_code, "
        "dept_raw, manager_dn, manager_name, manager_ou, manager_id, manager_mail, "
        "manager_state, enabled, imported_at, imported_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(p["employee_id"], p["display_name"], p["upn"], p["mail"], p["dept_name"],
          p["dept_code"], p["dept_raw"], p["manager_dn"], p["manager_name"],
          p["manager_ou"], p["manager_id"], p["manager_mail"], p["manager_state"],
          p["enabled"], now, by) for p in people])
    counts = {
        "rows": len(people),
        "enabled_n": sum(1 for p in people if p["enabled"] == 1),
        "disabled_n": sum(1 for p in people if p["enabled"] == 0),
        "mgr_ok": sum(1 for p in people if p["manager_state"] == MGR_OK),
        "mgr_fail": sum(1 for p in people if p["manager_state"] == MGR_FAIL),
        "mgr_dupname": sum(1 for p in people if p["manager_state"] == MGR_DUP),
        # 顯示在畫面上：系統替資料做了修正，人要看得到，不是默默改
        "zero_padded": padded,
    }
    conn.execute(
        "INSERT INTO ad_batch (file_name, imported_by, imported_at, rows, enabled_n, "
        "disabled_n, mgr_ok, mgr_fail, mgr_dupname) VALUES (?,?,?,?,?,?,?,?,?)",
        (file_name, by, now, counts["rows"], counts["enabled_n"], counts["disabled_n"],
         counts["mgr_ok"], counts["mgr_fail"], counts["mgr_dupname"]))
    conn.commit()
    return counts


def employee_id_from_gecos(gecos: str | None) -> str:
    """從主機帳號的 gecos 取員編。

        `01002861-謝傑宇_數位…` → `01002861`

    只認**開頭那段連續數字**。取不到回空字串——
    **不可以硬湊**，取錯員編會把帳號掛到別人身上。
    """
    m = re.match(r"\s*(\d{4,})", gecos or "")
    return m.group(1) if m else ""


def restore_leading_zeros(values: list[str], width_hint: int | None = None
                          ) -> tuple[list[str], int, int | None]:
    """把被 Excel 吃掉前導零的員編補回來。回 (還原後, 補了幾筆, 用的寬度)。

    ## 為什麼一定要做

    員編實際值是 `01000001`——**開頭有 0**。Excel 會判定成數字存成 `1003XX8`。
    而員編是把主機帳號 gecos 對到人的**唯一鍵**，前導零一掉就全部對不上，
    **而且不會報錯**——只會安靜地變成「這些帳號都沒有主人」。
    不報錯的錯誤最貴，所以補完要把筆數顯示在畫面上，不是默默改資料。

    ## 寬度怎麼決定（**不寫死 8 碼**）

    1. 欄位裡**有值保住了前導零**（例如整欄是文字格式，或某些列被當成字串）
       → 用那些值的長度當基準，這是資料自己給的證據
    2. 都沒有 → 用 `width_hint`（呼叫端從主機帳號 gecos 的員編長度推）
    3. 兩者都沒有 → **不補**，並回 width=None

    第 3 種很重要：沒有證據就不要猜。補錯長度會製造一批看似有效、
    實際對不上任何人的員編，比不補更難查。
    """
    vals = [str(v).strip() for v in values]
    evidence = {len(v) for v in vals if v.isdigit() and v.startswith("0")}
    width = max(evidence) if evidence else width_hint
    if not width:
        return vals, 0, None
    out, padded = [], 0
    for v in vals:
        if v.isdigit() and len(v) < width:
            out.append(v.zfill(width))
            padded += 1
        else:
            out.append(v)
    return out, padded, width


def gecos_id_width(conn: sqlite3.Connection) -> int | None:
    """從主機帳號 gecos 推員編長度——那邊是純文字，前導零不會被吃掉。

    取**最常見**的長度而不是最大值：偶爾有格式怪異的一兩筆，
    用 max 會被它帶偏，整欄補成錯的長度。
    """
    from collections import Counter

    c = Counter()
    try:
        for r in conn.execute("SELECT gecos FROM host_account WHERE COALESCE(gecos,'') <> ''"):
            emp = employee_id_from_gecos(r["gecos"])
            if emp:
                c[len(emp)] += 1
    except sqlite3.Error:
        return None
    return c.most_common(1)[0][0] if c else None


def lookup(conn: sqlite3.Connection, employee_id: str) -> dict | None:
    _ensure(conn)
    r = conn.execute("SELECT * FROM ad_person WHERE employee_id = ?",
                     (employee_id,)).fetchone()
    return dict(r) if r else None


def summary(conn: sqlite3.Connection) -> dict:
    """匯入概況＋兩張「對不上」清單的筆數。每個數字都要點得進去。"""
    _ensure(conn)
    b = conn.execute("SELECT * FROM ad_batch ORDER BY id DESC LIMIT 1").fetchone()
    return {"batch": dict(b) if b else None,
            "total": conn.execute("SELECT COUNT(*) FROM ad_person").fetchone()[0]}


def unmatched_managers(conn: sqlite3.Connection) -> list[dict]:
    """主管 DN 對不到名單裡任何一列的人。

    留空會被當成「這個人沒有主管」，那跟「對不上」是兩件事——
    前者不用處理，後者是名單有缺或 DN 格式不同。
    """
    _ensure(conn)
    return [dict(r) for r in conn.execute(
        "SELECT employee_id, display_name, dept_name, dept_code, manager_dn, "
        "manager_name, manager_ou, manager_state FROM ad_person "
        "WHERE manager_state IN (?, ?) ORDER BY manager_state, dept_code",
        (MGR_FAIL, MGR_DUP))]


def disabled_with_host_account(conn: sqlite3.Connection) -> list[dict]:
    """AD 已停用（離職）但主機上還留著帳號的人——**這是稽核發現，不是雜訊**。

    用 gecos 開頭的員編對。取不到員編的不列在這裡（那是另一張清單）。
    """
    _ensure(conn)
    out = []
    for r in conn.execute(
            "SELECT ip, asset_serial, username, gecos FROM host_account "
            "WHERE COALESCE(gecos,'') <> '' AND gone_at IS NULL"):
        emp = employee_id_from_gecos(r["gecos"])
        if not emp:
            continue
        p = conn.execute(
            "SELECT display_name, dept_name, enabled FROM ad_person WHERE employee_id = ?",
            (emp,)).fetchone()
        if p and p["enabled"] == 0:
            out.append({"ip": r["ip"], "asset_serial": r["asset_serial"],
                        "username": r["username"], "employee_id": emp,
                        "display_name": p["display_name"], "dept_name": p["dept_name"]})
    return out


def unmatched_accounts(conn: sqlite3.Connection) -> dict:
    """主機帳號對不到人的兩種情況，**分開列**（原因與處理方式不同）：

      * `no_empid`：gecos 解析不出員編——gecos 格式不符或根本沒填
      * `not_in_ad`：解析得出員編，但 AD 名單裡查無此人——名單不全或員編已失效
    """
    _ensure(conn)
    no_empid, not_in_ad = [], []
    for r in conn.execute(
            "SELECT ip, asset_serial, username, gecos FROM host_account "
            "WHERE gone_at IS NULL"):
        emp = employee_id_from_gecos(r["gecos"])
        row = {"ip": r["ip"], "asset_serial": r["asset_serial"],
               "username": r["username"], "gecos": r["gecos"]}
        if not emp:
            no_empid.append(row)
            continue
        if not conn.execute("SELECT 1 FROM ad_person WHERE employee_id = ?",
                            (emp,)).fetchone():
            row["employee_id"] = emp
            not_in_ad.append(row)
    return {"no_empid": no_empid, "not_in_ad": not_in_ad}


def rows_from_bytes(blob: bytes, file_name: str | None = None) -> list[list]:
    """.xlsx／.csv → 二維陣列（含標題列）。

    AD 匯出工具給 CSV 的機會很高，而且**中文環境常是 cp950 不是 UTF-8**；
    BOM 也很常見。這裡一路試過去，**都失敗就明確報錯，不要靜默回空**——
    回空會變成「檔案沒問題但一筆都沒有」，那是最難查的狀況。
    """
    name = (file_name or "").lower()
    if name.endswith(".csv") or (not name.endswith((".xlsx", ".xlsm")) and blob[:2] != b"PK"):
        import csv
        import io as _io
        text = None
        for enc in ("utf-8-sig", "utf-8", "cp950", "big5"):
            try:
                text = blob.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("CSV 編碼認不得（試過 UTF-8／cp950／big5）")
        return [r for r in csv.reader(_io.StringIO(text)) if any(str(x).strip() for x in r)]

    import openpyxl
    import io as _io
    wb = openpyxl.load_workbook(_io.BytesIO(blob), data_only=True, read_only=True)
    ws = wb[wb.sheetnames[0]]
    out = []
    for r in ws.iter_rows(values_only=True):
        if r is None:
            continue
        # ⚠️ 浮點要轉回整數字串：openpyxl 對數值儲存格可能給 float，
        # str(1003418.0) 會變成 "1003418.0"，那串連還原前導零都救不回來。
        vals = []
        for v in r:
            if v is None:
                vals.append("")
            elif isinstance(v, float) and v.is_integer():
                vals.append(str(int(v)))
            else:
                vals.append(str(v).strip())
        if any(vals):
            out.append(vals)
    if not out:
        raise ValueError("Excel 第一個工作表是空的")
    return out
