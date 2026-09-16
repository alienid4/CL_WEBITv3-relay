"""業務系統對照表：`api_id` → 系統名稱、AP 部門、AP 負責人。

## 為什麼需要這張表

資產庫裡只有 `api_id`（`N-008` 這種代碼），沒有中文名稱。畫面上到處是看不懂的
代碼，而帳號盤點要交出去的 Excel 第一欄就是 `system_id` ＋ `system`。

在這之前，系統名稱是拿同一個 `api_id` 底下 **`MIN(asset_name)`** 湊出來的
（見 api.py 的全域搜尋與 blast_radius）——那是「隨便挑一台機器的名字當系統名」，
猜對是運氣。有了這張表就有正式來源。

## 為什麼 AP 部門與 AP 負責人也放這裡

使用者提供的範例裡，`system_id`／`system`／`ap_department`／`ap_owner` 四欄
在同一個系統的每一列都**重複同一組值**——那是**業務系統的屬性**，不是機器的屬性。
放在機器欄位上會有 N 份副本，改一次要改 N 台。

## 空白的兩種原因要分得開

匯出時 `system` 欄空白有兩種完全不同的意思：
  · 這台機器**沒填 api_id**            → 要去補資產資料
  · 有 api_id，但**對照表裡沒有這個代碼** → 要去補對照表
兩者長得一樣的話，人不知道該補哪一邊。`lookup()` 回的 dict 用 `found` 分開。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

#: 匯入時可接受的欄名（大小寫、全半形、前後空白都會正規化後比對）。
#: 不寫死單一欄名是專案慣例（決策 D14）——來源 Excel 的表頭常常換。
_COLUMN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "api_id": ("system_id", "ap_id", "apid", "api_id", "系統代碼", "系統編號"),
    # 「項目名稱」是 dynassets 匯出那份 CSV 的系統名欄
    "name": ("system", "system_name", "系統名稱", "業務系統", "系統", "項目名稱", "項目"),
    "ap_department": ("ap_department", "ap_dept", "AP部門", "AP 部門", "應用部門", "部門"),
    "ap_owner": ("ap_owner", "AP負責人", "AP 負責人", "應用負責人", "負責人", "ap"),
}


def _norm(s: Any) -> str:
    return str(s or "").strip().lower().replace(" ", "").replace("　", "")


def upsert(conn: sqlite3.Connection, api_id: str, name: str | None = None,
           ap_department: str | None = None, ap_owner: str | None = None,
           needs_review: int = 0, name_candidates: str | None = None) -> None:
    """寫入一筆對照。以 api_id 為鍵——對照表會重匯（改名、加新系統），
    重匯必須是更新同一筆而不是長出重複。

    needs_review / name_candidates：同一 api_id 在來源檔有多組不同名字時，由 import_file
    aggregate 後帶進來——標「多來源·待確認」並存所有候選，不靜默取一個（會猜錯又看不出來）。
    這兩欄一律照 import 算出的值覆寫（不 COALESCE）：重匯就是重算衝突狀態，舊的多來源標記
    若這次已釐清（只剩一個名字）就要清掉。"""
    conn.execute(
        "INSERT INTO business_system (api_id, name, ap_department, ap_owner, "
        "  needs_review, name_candidates) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(api_id) DO UPDATE SET "
        "  name = COALESCE(excluded.name, name), "
        "  ap_department = COALESCE(excluded.ap_department, ap_department), "
        "  ap_owner = COALESCE(excluded.ap_owner, ap_owner), "
        "  needs_review = excluded.needs_review, "
        "  name_candidates = excluded.name_candidates, "
        "  updated_at = datetime('now','localtime')",
        (api_id.strip(), (name or "").strip() or None,
         (ap_department or "").strip() or None, (ap_owner or "").strip() or None,
         int(needs_review), (name_candidates or None)),
    )


def lookup(conn: sqlite3.Connection, api_id: str | None) -> dict:
    """查一個 api_id。**空白的原因要分得開**（見模組 docstring）。

    回 `{"found": bool, "reason": str|None, ...}`：
      · api_id 是空的      → found=False, reason="機器沒填 api_id"
      · 對照表裡查不到     → found=False, reason="對照表沒有這個代碼"
      · 查到               → found=True,  reason=None
    """
    if not (api_id or "").strip():
        return {"found": False, "reason": "機器沒填 api_id",
                "api_id": None, "name": None, "ap_department": None, "ap_owner": None}
    row = conn.execute(
        "SELECT api_id, name, ap_department, ap_owner, needs_review, name_candidates "
        "FROM business_system WHERE api_id = ?", (api_id.strip(),)).fetchone()
    if row is None:
        return {"found": False, "reason": "對照表沒有這個代碼",
                "api_id": api_id.strip(), "name": None,
                "ap_department": None, "ap_owner": None}
    return {"found": True, "reason": None, **dict(row)}


def list_all(conn: sqlite3.Connection) -> list[dict]:
    """全部對照，附「這個系統目前有幾台機器」——匯入後要看得出對得上多少。"""
    rows = conn.execute(
        "SELECT b.api_id, b.name, b.ap_department, b.ap_owner, "
        "       b.needs_review, b.name_candidates, b.updated_at, "
        "       (SELECT COUNT(*) FROM hardware h WHERE h.api_id = b.api_id) AS asset_count "
        "FROM business_system b ORDER BY b.needs_review DESC, b.api_id").fetchall()
    return [dict(r) for r in rows]


def coverage(conn: sqlite3.Connection) -> dict:
    """對帳用：資產庫裡的 api_id 有多少對得到對照表。

    只給「已匯入 N 筆」沒有用——人要知道的是**還有多少台查不到名字**，
    以及那是「對照表缺代碼」還是「機器沒填 api_id」。
    """
    total = conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0]
    no_apid = conn.execute(
        "SELECT COUNT(*) FROM hardware "
        "WHERE api_id IS NULL OR TRIM(api_id) = ''").fetchone()[0]
    unmatched = conn.execute(
        "SELECT COUNT(*) FROM hardware h WHERE h.api_id IS NOT NULL AND TRIM(h.api_id) != '' "
        "AND NOT EXISTS (SELECT 1 FROM business_system b WHERE b.api_id = h.api_id)"
    ).fetchone()[0]
    missing_codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT h.api_id FROM hardware h "
        "WHERE h.api_id IS NOT NULL AND TRIM(h.api_id) != '' "
        "AND NOT EXISTS (SELECT 1 FROM business_system b WHERE b.api_id = h.api_id) "
        "ORDER BY h.api_id LIMIT 50")]
    return {
        "mapped_systems": conn.execute(
            "SELECT COUNT(*) FROM business_system").fetchone()[0],
        "assets_total": total,
        "assets_without_api_id": no_apid,          # 要去補資產資料
        "assets_with_unmapped_api_id": unmatched,  # 要去補對照表
        "unmapped_codes": missing_codes,           # 具名列出，不要只給數字
    }


def _read_rows(path: Path) -> tuple[list, list[list]]:
    """讀 .csv 或 .xlsx，回 (表頭, 資料列)。

    CSV 編碼：台灣匯出常是 Big5/CP950，也可能是 UTF-8(帶不帶 BOM)——依序試，
    全失敗才用 replace 硬解（寧可有幾個字變 � 也不要整份匯不進來）。
    """
    ext = path.suffix.lower()
    if ext == ".csv":
        import csv

        raw = path.read_bytes()
        text = None
        for enc in ("utf-8-sig", "utf-8", "cp950", "big5"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("utf-8", "replace")
        all_rows = list(csv.reader(text.splitlines()))
        if not all_rows:
            return [], []
        return all_rows[0], [r for r in all_rows[1:]]

    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        it = ws.iter_rows(values_only=True)
        header = next(it, None) or []
        return list(header), [list(r) for r in it]
    finally:
        wb.close()


#: 系統類別欄可以接受的欄名（使用者給的 APID.xlsx 表頭是「系統類別」）
_CLASS_COLUMN_CANDIDATES = ("系統類別", "類別", "系統分類", "系統分級", "分級", "class")


def import_class_file(path: Path, conn: sqlite3.Connection) -> dict:
    """匯入系統類別（第一／二／三類）。**只更新 `sys_class` 這一欄，其他欄位一律不碰。**

    2026-09-10 使用者給了 APID.xlsx（項次／系統類別／APID／系統別，89 個系統）。

    ⚠️ 為什麼不能走 `import_file`：那支是整列覆寫（名稱、AP 部門、AP 負責人都寫），
    而這份檔只有類別——照那條路匯進去，那 82 個系統的 AP 部門與負責人會被清空。

    規則：
    - **整份取代**：這份是完整的分級清單，不在清單裡的系統要回到「未分級」，
      不然舊的類別會留著、跟清單對不起來
    - **同一個 APID 在檔案裡有兩種類別 → 不猜**，那個系統不設類別並具名回報
    - **清單有、對照表沒有的 APID → 不新建**，具名回報。新建會長出一個沒有部門、
      沒有機器的空系統，而且看起來像是真的
    - 原字串照存（例如「S1-第一類系統軟體（核心）」），「核心」這種資訊不丟
    """
    header, data_rows = _read_rows(path)
    if not header:
        raise ValueError("這份檔案是空的（連表頭都沒有）")
    norm_header = {_norm(h): i for i, h in enumerate(header) if h not in (None, "")}

    api_cands = list(_COLUMN_CANDIDATES["api_id"]) + ["APID", "AP ID"]
    ai = next((norm_header[_norm(c)] for c in api_cands if _norm(c) in norm_header), None)
    ci = next((norm_header[_norm(c)] for c in _CLASS_COLUMN_CANDIDATES
               if _norm(c) in norm_header), None)
    shown = [h for h in header if h]
    if ai is None:
        raise ValueError(f"找不到系統代碼欄（APID）。這份檔案的表頭是：{shown}")
    if ci is None:
        # 最常見的匯錯：把業務系統對照表放到這一列
        hint = ""
        if any(_norm(c) in norm_header for f in ("ap_department", "ap_owner")
               for c in _COLUMN_CANDIDATES[f]):
            hint = "這份看起來是「業務系統對照表」（有部門／負責人欄），請改用那一列匯入。"
        raise ValueError(f"匯錯檔了？找不到系統類別欄。{hint}可接受的欄名："
                         f"{'、'.join(_CLASS_COLUMN_CANDIDATES)}。這份檔案的表頭是：{shown}")

    from import_templates import is_example

    seen: dict[str, set[str]] = {}
    skipped = examples = 0
    for row in data_rows:
        if not any(v not in (None, "") for v in row):
            continue
        api = str(row[ai]).strip() if ai < len(row) and row[ai] not in (None, "") else ""
        cls = str(row[ci]).strip() if ci < len(row) and row[ci] not in (None, "") else ""
        if is_example(api):
            examples += 1         # 範例檔的範例列，沒刪就匯回來了
            continue
        if not api or not cls:
            skipped += 1          # 有內容但缺代碼或缺類別——不能寫，也不能安靜吞掉
            continue
        seen.setdefault(api, set()).add(cls)

    conflicts = {k: sorted(v) for k, v in seen.items() if len(v) > 1}
    existing = {r[0] for r in conn.execute("SELECT api_id FROM business_system")}
    not_in_table = sorted(k for k in seen if k not in existing)

    # 匯錯的保險：這是「整份取代」，一個都對不上還照做，會把現有的分級全部清成未分級。
    # 所以在動任何資料之前先擋下來，原本的類別原封不動。
    # 只在「真的會清掉東西」時擋：目前一個都沒分級的話，照做也不會損失什麼，
    # 對不上的代碼照樣具名回報（加上 warning）就好。
    rated_now = conn.execute(
        "SELECT COUNT(*) FROM business_system WHERE sys_class IS NOT NULL").fetchone()[0]
    if not seen or (rated_now and not any(k in existing for k in seen)):
        if not seen:
            raise ValueError("匯錯檔了？檔案裡沒有任何可用的資料列"
                             + (f"（{examples} 列是範例檔的範例列，已略過）" if examples else "")
                             + "。這次沒有套用，原本的類別保留。")
        raise ValueError(f"匯錯檔了？檔案裡 {len(seen)} 個代碼，對照表一個都對不上"
                         f"（例：{'、'.join(sorted(seen)[:5])}）。這次沒有套用，原本的類別保留。"
                         "如果對照表還是空的，請先匯入「業務系統對照表」。")

    # 整份取代，同一個交易裡做完：先全部清成未分級，再套清單
    conn.execute("UPDATE business_system SET sys_class = NULL")
    updated = 0
    for api, classes in seen.items():
        if api in conflicts or api not in existing:
            continue
        conn.execute(
            "UPDATE business_system SET sys_class = ?, updated_at = datetime('now','localtime') "
            "WHERE api_id = ?", (next(iter(classes)), api))
        updated += 1
    conn.commit()

    return {
        "updated": updated,
        "in_file": len(seen),
        "not_in_table": not_in_table,           # 具名列出，不要只給數字
        "conflicts": conflicts,                  # 同碼多類，沒設
        "skipped_rows": skipped,
        "example_rows": examples,                # 範例列，已略過
        # 對不上的超過一半：多半是匯錯檔或對照表太舊，要講出來，不能只顯示「完成」
        "warning": (f"檔案裡 {len(not_in_table)}／{len(seen)} 個代碼對照表沒有，請確認是不是匯錯檔"
                    if len(not_in_table) * 2 > len(seen) else None),
        "unrated_after": conn.execute(
            "SELECT COUNT(*) FROM business_system WHERE sys_class IS NULL").fetchone()[0],
    }


def set_system_class(conn, api_id: str, sys_class: str | None) -> dict:
    """手動改一個業務系統的分級（第一/二/三類）。存成 sys_class_override，
    分級表「整份取代」重匯不會沖掉它（顯示時覆寫優先）。
    傳空字串或「未分級」＝清掉覆寫、回到分級表的判定。"""
    cls = (sys_class or "").strip()
    if cls in ("", "未分級"):
        cls = None
    elif cls not in ("第一類", "第二類", "第三類"):
        raise ValueError(f"不認得的分級：{cls}（可用：第一類、第二類、第三類，或留空＝清除）")
    cur = conn.execute(
        "UPDATE business_system SET sys_class_override = ? WHERE api_id = ?", (cls, api_id))
    conn.commit()
    if cur.rowcount == 0:
        raise ValueError("查無此業務系統")
    return {"api_id": api_id, "sys_class_override": cls}


def import_file(path: Path, conn: sqlite3.Connection) -> dict:
    """吃一份對照表（.csv 或 .xlsx）。第一列當表頭，欄名比對 `_COLUMN_CANDIDATES`。

    **整檔先依 api_id 分組再寫**（不是逐列 upsert）：同一個 api_id 在來源檔出現多組
    **不同名字**時，不靜默取最後一列（那會猜錯又看不出來，2026-09-09 使用者要求
    「全部放進去、不用我挑、但不能偷偷猜」）——改成標 `needs_review=1`、把所有候選存進
    `name_candidates`（｜分隔），name 取第一個當預設值，之後人要改隨時看得出是哪些。
    `api_id` 是必要欄；名字/部門/負責人缺了就留空。
    """
    header, data_rows = _read_rows(path)
    if not header:
        raise ValueError("這份檔案是空的（連表頭都沒有）")

    norm_header = {_norm(h): i for i, h in enumerate(header) if h not in (None, "")}
    idx: dict[str, int] = {}
    for field, cands in _COLUMN_CANDIDATES.items():
        for c in cands:
            if _norm(c) in norm_header:
                idx[field] = norm_header[_norm(c)]
                break
    if "api_id" not in idx:
        raise ValueError(
            f"匯錯檔了？找不到系統代碼欄。可接受的欄名："
            f"{'、'.join(_COLUMN_CANDIDATES['api_id'])}。"
            f"這份檔案的表頭是：{[h for h in header if h]}")
    # 只有代碼、沒有名稱／部門／負責人任何一欄：照匯只會長出一堆空系統。
    # 最常見的是把系統類別表（APID.xlsx）放到這一列——那份要走「系統類別對照表」。
    if not ({"name", "ap_department", "ap_owner"} & set(idx)):
        is_class = any(_norm(c) in norm_header for c in _CLASS_COLUMN_CANDIDATES)
        raise ValueError(
            "匯錯檔了？" + ("這份看起來是「系統類別對照表」（有系統類別欄），請改用那一列匯入。"
                          if is_class else "找不到系統名稱／AP 部門／AP 負責人任何一欄，匯了也只有代碼。")
            + f"這份檔案的表頭是：{[h for h in header if h]}")

    def cell(row: list, field: str) -> str | None:
        i = idx.get(field)
        if i is None or i >= len(row):
            return None
        v = row[i]
        s = str(v).strip() if v is not None else ""
        return s or None

    # 分組：api_id -> 這個代碼在整份檔裡出現過的不同名字/部門/負責人
    from import_templates import is_example

    agg: dict[str, dict] = {}
    skipped = examples = 0
    for row in data_rows:
        if not any(v not in (None, "") for v in row):
            continue
        api_id = cell(row, "api_id")
        if is_example(api_id):
            examples += 1       # 範例檔的範例列，不能變成一個叫「範例系統A」的系統
            continue
        if not api_id:
            skipped += 1        # 有內容但沒代碼——不能寫，也不能安靜吞掉
            continue
        e = agg.setdefault(api_id, {"names": [], "dept": None, "owner": None})
        nm = cell(row, "name")
        if nm and nm not in e["names"]:
            e["names"].append(nm)
        if e["dept"] is None:
            e["dept"] = cell(row, "ap_department")
        if e["owner"] is None:
            e["owner"] = cell(row, "ap_owner")

    imported, multi_source = 0, 0
    for api_id, e in agg.items():
        names = e["names"]
        if len(names) > 1:
            needs, cands, name = 1, "｜".join(names), names[0]
            multi_source += 1
        else:
            needs, cands, name = 0, None, (names[0] if names else None)
        upsert(conn, api_id, name, e["dept"], e["owner"], needs, cands)
        imported += 1
    conn.commit()

    return {
        "imported": imported,
        "multi_source": multi_source,   # 同碼多名、已標「多來源·待確認」的筆數
        "skipped_no_api_id": skipped,
        "example_rows": examples,
        "columns_found": sorted(idx),
        # 匯完立刻對帳：對得上多少、還差哪些代碼。只回「匯了 N 筆」等於沒回答問題。
        "coverage": coverage(conn),
    }


# 舊名相容（端點/測試曾叫 import_xlsx）；現在通吃 csv/xlsx。
import_xlsx = import_file
