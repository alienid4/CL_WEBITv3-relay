"""機房搬遷盤點表匯入＋全欄位對帳。

把現場填回來的 Master（.xlsx）讀進來，逐台、逐欄跟系統現有值比對，回一份「校對版」：
Server Master 每格上色——
  綠＝匯入值與系統一致；紅＝不一致（現場校正/與系統不同，值以匯入的為準）；
  藍＝現場沒填、系統有值（幫你補上）；黑／留白＝兩邊都沒有或系統無此欄可比。
所有不一致同時列進「CIA待異動」分頁。**只讀不寫回資料庫**（要不要 Renew 由人另外決定）。

只比「系統也有的欄位」（見 CMP_MAP）；純人事/管理欄（系統沒有獨立來源的）不在比對範圍。
"""
from __future__ import annotations

import io

# Server Master 欄名 → hardware 欄位（只列系統也有、可比對的）
CMP_MAP = {
    "資產狀態": "asset_status", "資產名稱": "asset_name", "主機名稱": "hostname",
    "APID": "api_id", "環境別": "environment", "使用單位": "usage_unit", "擁有者": "owner",
    "保管者": "custodian", "使用者": "user_name", "所屬公司": "owning_company",
    "型號": "device_model", "Serial Number": "hw_serial", "OS": "os",
    "IP(管理/主要)": "ip", "原機房": "physical_location", "原Rack": "rack_no",
}


BASE_SHEET = "CIA資產基準"
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_ZIP_TOTAL = 200 * 1024 * 1024
MAX_ZIP_FILES = 200
MAX_SHEET_ROWS = 20000
ST_OK, ST_MISSING, ST_SUSPECT, ST_REF = "ok", "missing", "疑似被改", "僅供參考"
SUSPECT_TEXT = "基準疑似被改，三方比對僅供參考"
THREE_SHEET = "三方差異"
THREE_COLS = ["資產序號", "欄位", "基準值", "現場值", "當前DB值", "分類", "說明"]
# 五類＋「無基準」（基準分頁缺／缺欄／缺該台，無從判斷是誰改的）
C_SAME, C_SITE, C_DB, C_BOTH, C_CONFLICT, C_NOBASE, C_UNTRUST = (
    "未改", "僅現場改", "僅DB後來變", "雙方同改", "雙方衝突", "無基準", "基準不可信")
THREE_KEYS = {C_SAME: "unchanged", C_SITE: "site_only", C_DB: "db_only", C_BOTH: "both_same",
              C_CONFLICT: "conflict", C_NOBASE: "no_baseline", C_UNTRUST: "untrusted"}
THREE_FILL = {C_SITE: "DDEBF7", C_DB: "FFF2CC", C_BOTH: "E2EFDA", C_CONFLICT: "F8CBAD",
              C_NOBASE: "EDEDED", C_UNTRUST: "FFC7CE"}


class RelocationTooLarge(ValueError):
    """上傳超過資源上限（API 回 413）。"""


def read_upload(fobj) -> bytes:
    import daily_risk
    return daily_risk.read_limited(fobj, MAX_UPLOAD_BYTES, RelocationTooLarge)


def _precheck(file_bytes: bytes) -> None:
    """交給 openpyxl 完整載入前：大小、zip 解壓後總量與檔數、兩張關鍵分頁的列數（解析途中中止）。"""
    import daily_risk
    from openpyxl import load_workbook
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise RelocationTooLarge(f"檔案太大（上限 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB）")
    daily_risk._check_xlsx_zip(file_bytes, MAX_ZIP_TOTAL, MAX_ZIP_FILES, RelocationTooLarge)
    wb = load_workbook(io.BytesIO(file_bytes), read_only=True)
    try:
        for name in ("Server Master", BASE_SHEET):
            if name not in wb.sheetnames:
                continue
            n = 0
            for _ in wb[name].iter_rows(values_only=True):
                n += 1
                if n > MAX_SHEET_ROWS + 1:
                    raise RelocationTooLarge(f"「{name}」列數太多（上限 {MAX_SHEET_ROWS} 列）")
    finally:
        wb.close()


def _text_guard(cell) -> None:
    """= + - @ 開頭的字串一律當文字，避免輸出檔在 Excel 被當公式執行。"""
    v = cell.value
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        cell.data_type = "s"


def _append_safe(ws, row) -> None:
    ws.append(row)
    for c in ws[ws.max_row]:
        _text_guard(c)
_FILL_BLUE = "1565C0"      # 上一輪回填「系統補值」的藍字

NOTE_MARK = "【系統對帳不一致】"
UNKNOWN_SHEET = "未知序號"
UNKNOWN_COLS = ["資產序號", "主機名稱", "Server Master 列號", "說明"]


def _norm(v) -> str:
    return str(v).strip().lower() if v not in (None, "") else ""


def _strip_mark(text) -> str:
    """去掉先前回填的系統備註（標記之後整段都是我們寫的），讓重複回填不會疊加。"""
    t = "" if text is None else str(text)
    i = t.find(NOTE_MARK)
    if i < 0:
        return t
    return t[:i].rstrip("；; ")


def _classify(b: str, s: str, d: str) -> str:
    """三方比對（已 _norm）：b=基準、s=現場、d=當前 DB。呼叫端須先把「現場沒填」換成 b。"""
    if s == b:
        return C_SAME if d == b else C_DB
    if d == b:
        return C_SITE
    return C_BOTH if s == d else C_CONFLICT


def _check_signature(wb, conn, head: dict, raw_rows: list):
    """回 (state, 說明)：valid／mismatch／deleted／legacy。金鑰與簽章都不外洩。"""
    import hmac
    from relocation_export import META_SHEET, SIGNED_MARK, base_signature, get_hmac_key
    signed = (getattr(wb.properties, "description", None) or "") == SIGNED_MARK
    sig = None
    if META_SHEET in wb.sheetnames:
        m = wb[META_SHEET]
        for row in m.iter_rows(min_row=1, max_row=5, values_only=True):
            if row and row[0] == "baseline_hmac_sha256" and len(row) > 1:
                sig = str(row[1] or "")
    if sig is None:
        return ("deleted", "簽章被移除") if signed else ("legacy", "無簽章（舊版檔）")
    key = get_hmac_key(conn, create=False)
    if not key:
        return "mismatch", "系統尚無簽章金鑰，無法驗證"
    names = list(head.keys())
    rows = [[(r[head[n]] if head[n] < len(r) else None) for n in names] for r in raw_rows]
    try:
        calc = base_signature(key, names, rows)
    except ValueError:
        return "mismatch", "簽章無法計算"
    return ("valid", "") if hmac.compare_digest(calc, sig) else ("mismatch", "簽章不符，基準內容被改過")


def _read_baseline(wb, sys_count: int, master_serials: list, conn=None):
    """讀隨檔『CIA資產基準』。回 (status, {序號: {欄名: 值}}, 疑點[])。
    status：missing（沒有此分頁）／suspect（格式與系統匯出不符，不可全信）／ok。以資產序號為鍵，不靠列序。"""
    if BASE_SHEET not in wb.sheetnames:
        return ST_MISSING, {}, ["沒有『CIA資產基準』分頁，無法三方比對"], "none"
    from relocation_export import CIA_BASE_COLS
    sh = wb[BASE_SHEET]
    heads = [str(c.value).strip() if c.value is not None else "" for c in sh[1]]
    head = {h: i for i, h in enumerate(heads) if h}
    reasons: list[str] = []
    if "資產序號" not in head:
        return ST_SUSPECT, {}, ["基準分頁找不到「資產序號」欄，無法比對"], "mismatch"
    miss = [c for c in CIA_BASE_COLS if c not in head]
    extra = [h for h in head if h not in CIA_BASE_COLS]
    if miss:
        reasons.append("基準分頁缺欄：" + "、".join(miss[:6]) + ("…" if len(miss) > 6 else ""))
    if extra:
        reasons.append("基準分頁多出非系統匯出的欄：" + "、".join(extra[:6]))
    data: dict = {}
    dup = 0
    raw_rows = []
    for row in sh.iter_rows(min_row=2, values_only=True):
        ser = row[head["資產序號"]] if head["資產序號"] < len(row) else None
        if ser is None or str(ser).strip() == "":
            continue
        raw_rows.append(row)
        k = str(ser).strip()
        if k in data:
            dup += 1
            continue
        data[k] = {h: (row[i] if i < len(row) else None) for h, i in head.items()}
    if dup:
        reasons.append(f"基準分頁有 {dup} 列資產序號重複")
    n = len(data)
    if sys_count and (n < sys_count * 0.5 or n > sys_count * 2):
        reasons.append(f"基準分頁 {n} 台，與系統 {sys_count} 台差距過大")
    absent = [m for m in master_serials if m not in data]
    if absent:
        reasons.append(f"現場表有 {len(absent)} 台不在基準分頁")
    sig_state, sig_note = _check_signature(wb, conn, head, raw_rows) if conn is not None else ("legacy", "")
    status = ST_SUSPECT if reasons else ST_OK
    if sig_state in ("mismatch", "deleted"):
        reasons.append(sig_note)
        status = ST_SUSPECT
    elif sig_state == "legacy" and status == ST_OK:
        reasons.append("無簽章（舊版檔），基準無法驗證")
        status = ST_REF
    return status, data, reasons, sig_state


def _is_blue(cell) -> bool:
    col = getattr(cell.font, "color", None)
    return bool(col is not None and isinstance(col.rgb, str) and col.rgb.upper().endswith(_FILL_BLUE))


def _three_way(cell, imported, system, iv, sv, serial, col_name, hw_field,
               base_status, base_data, base_name, suspect_tail, counts, rows, untrusted=False):
    bname = base_name.get(hw_field)
    brec = base_data.get(serial) if base_status != "missing" else None
    if untrusted and base_status != ST_MISSING:
        # 基準不可信：任何格都不得判「未改」；只列現場與 DB 有分歧的格
        counts["untrusted"] += 1
        if iv and sv and iv != sv:
            bval = brec.get(bname) if (brec and bname) else None
            rows.append([serial, col_name, bval, imported, system, C_UNTRUST,
                         "基準不可信，現場與系統值不同" + suspect_tail])
        return
    if brec is None or bname is None or bname not in brec:
        # 沒基準可比：只在現場與 DB 都有值且不同時列出（不然沒資訊）
        why = ("基準分頁缺此欄" if brec is not None else
               "基準分頁沒有這台" if base_status != "missing" else "沒有基準分頁")
        if iv and sv and iv != sv:
            counts["no_baseline"] += 1
            rows.append([serial, col_name, None, imported, system, C_NOBASE,
                         why + "，無法判斷誰改的（現場與 DB 不同）" + suspect_tail])
        return
    braw = brec[bname]
    bv = _norm(braw)
    site_empty = (not iv) or (_is_blue(cell) and iv == sv)    # 現場沒填（或只是上一輪系統補的值）
    sv_eff = bv if site_empty else iv
    cat = _classify(bv, sv_eff, sv)
    counts[THREE_KEYS[cat]] += 1
    if cat == C_SAME:
        return
    note = {C_SITE: "現場改了，系統值仍是基準；回填 CIA 時以現場為準",
            C_DB: "現場未改，系統匯出後值變了；以現場表為準會蓋掉較新的系統值，請先確認",
            C_BOTH: "現場與系統改成同一個新值，無衝突",
            C_CONFLICT: "現場與系統各自改成不同值，需人工裁決"}[cat]
    if site_empty:
        note = "現場未填，視同未改；" + note
    rows.append([serial, col_name, braw, None if site_empty else imported, system, cat,
                 note + suspect_tail])


def _read_history(wb, cols) -> dict:
    """讀既有「CIA待異動」：以（資產序號, 欄位名稱）為鍵，留住人填的處理歷史。"""
    if "CIA待異動" not in wb.sheetnames:
        return {}
    sh = wb["CIA待異動"]
    head = {str(c.value).strip(): i for i, c in enumerate(sh[1]) if c.value}
    if "資產序號" not in head or "欄位名稱" not in head:
        return {}
    out = {}
    for row in sh.iter_rows(min_row=2, values_only=True):
        ser = row[head["資產序號"]] if head["資產序號"] < len(row) else None
        fld = row[head["欄位名稱"]] if head["欄位名稱"] < len(row) else None
        if ser in (None, "") or fld in (None, ""):
            continue                      # 例如「（本次比對沒有不一致）」佔位列
        rec = {c: (row[head[c]] if c in head and head[c] < len(row) else None) for c in cols}
        out[(str(ser).strip(), str(fld).strip())] = rec
    return out


def _has_history(rec) -> bool:
    return bool(rec.get("申請單編號") or rec.get("完成日期")
                or (rec.get("處理狀態") not in (None, "", "待處理")))


def reconcile(conn, file_bytes: bytes) -> tuple[io.BytesIO, dict]:
    from openpyxl import load_workbook
    from openpyxl.styles import Font

    RED = Font(color="C00000")
    GREEN = Font(color="2E7D32")
    BLUE = Font(color="1565C0")

    _precheck(file_bytes)
    wb = load_workbook(io.BytesIO(file_bytes))
    if "Server Master" not in wb.sheetnames:
        raise ValueError("這個檔案沒有「Server Master」分頁，請確認是搬遷盤點表")
    ws = wb["Server Master"]

    # 表頭 → 欄位索引
    header = {}
    for c, cell in enumerate(ws[1], 1):
        if cell.value:
            header[str(cell.value).strip()] = c
    if "資產序號" not in header:
        raise ValueError("Server Master 找不到「資產序號」欄")
    serial_c = header["資產序號"]
    note_c = header.get("備註")   # 不一致原因寫進最後一欄「備註」

    # 系統資料：以資產序號為 key
    sys_rows = {r["asset_serial"]: r for r in conn.execute(
        "SELECT * FROM hardware WHERE asset_serial IS NOT NULL")}

    from relocation_export import CIA_BASE_MAP, CIA_DIFF_COLS
    history = _read_history(wb, CIA_DIFF_COLS)

    master_known = []
    for r in range(2, ws.max_row + 1):
        v = ws.cell(r, serial_c).value
        if v not in (None, "") and str(v).strip() in sys_rows:
            master_known.append(str(v).strip())
    base_status, base_data, base_reasons, sig_state = _read_baseline(wb, len(sys_rows), master_known, conn)
    base_name = {hw: nm for nm, hw in CIA_BASE_MAP}
    untrusted = sig_state in ("mismatch", "deleted")
    suspect_tail = ("；" + SUSPECT_TEXT) if base_status == ST_SUSPECT else (
        "；無簽章（舊版檔），僅供參考" if base_status == ST_REF else "")
    three_counts = {k: 0 for k in THREE_KEYS.values()}
    three_rows: list[list] = []

    n_same = n_diff = n_fill = n_unknown = 0
    diffs: list[list] = []
    unknowns: list[list] = []
    for r in range(2, ws.max_row + 1):
        serial = ws.cell(r, serial_c).value
        if serial in (None, ""):
            continue
        hw = sys_rows.get(str(serial).strip())
        host = ws.cell(r, header.get("主機名稱", serial_c)).value or ""
        if hw is None:
            n_unknown += 1
            unknowns.append([str(serial).strip(), host, r, "系統查無此資產序號，未比對"])
            continue
        reasons: list[str] = []
        for col_name, hw_field in CMP_MAP.items():
            c = header.get(col_name)
            if not c:
                continue
            imported = ws.cell(r, c).value
            system = hw[hw_field] if hw_field in hw.keys() else None
            iv, sv = _norm(imported), _norm(system)
            # ---- 三方比對（基準／現場／當前 DB）；只讀，不影響下面既有的二方邏輯 ----
            _three_way(ws.cell(r, c), imported, system, iv, sv, str(serial).strip(),
                       col_name, hw_field, base_status, base_data, base_name,
                       suspect_tail, three_counts, three_rows, untrusted)
            if not iv and not sv:
                continue
            if not iv and sv:                    # 現場沒填、系統有 → 幫補上（藍）
                ws.cell(r, c).value = system
                _text_guard(ws.cell(r, c))
                ws.cell(r, c).font = BLUE
                n_fill += 1
            elif iv and not sv:                  # 現場有、系統沒有 → 保留現場值（不變色）
                continue
            elif iv == sv:                       # 一致（綠）
                if _is_blue(ws.cell(r, c)):      # 上一輪系統補的值：維持藍字，下一輪仍視為「現場沒填」
                    n_fill += 1
                    continue
                ws.cell(r, c).font = GREEN
                n_same += 1
            else:                                # 不一致（紅）→ 值以匯入的為準、記差異
                ws.cell(r, c).font = RED
                n_diff += 1
                diffs.append([serial, host, col_name, system, imported,
                              "不一致", "是", "", "待處理", "", ""])
                reasons.append(f"{col_name}：{system or '(空)'} → {imported}")
        # 這台有不一致 → 在「備註」寫清楚原因（原本→要改成），該列看了就懂。
        # 冪等：先去掉上一輪自己寫的標記段再寫，重複回填不疊加；這輪已一致就把舊標記清掉。
        if note_c:
            cell = ws.cell(r, note_c)
            base = _strip_mark(cell.value)
            if reasons:
                cell.value = (base + "；" if base else "") + NOTE_MARK + "；".join(reasons)
                cell.font = RED
            elif cell.value is not None and NOTE_MARK in str(cell.value):
                cell.value = base or None

    # 寫回 CIA待異動分頁：以（資產序號, 欄位）為鍵合併，保留人填的申請單編號／處理狀態／完成日期／備註
    idx = {c: i for i, c in enumerate(CIA_DIFF_COLS)}
    merged: list[list] = []
    seen = set()
    for d in diffs:
        key = (str(d[0]).strip(), d[2])
        seen.add(key)
        old = history.get(key)
        if old:
            for c in ("申請單編號", "處理狀態", "完成日期", "備註"):
                if old.get(c) not in (None, ""):
                    d[idx[c]] = old[c]
        merged.append(d)
    kept_history = 0
    for key, old in history.items():
        if key in seen or not _has_history(old):
            continue                      # 本次已一致且沒人處理過的舊列就不留了
        row = [old.get(c) for c in CIA_DIFF_COLS]
        row[idx["差異類型"]] = "本次比對已一致（保留處理歷史）"
        merged.append(row)
        kept_history += 1
    if "CIA待異動" in wb.sheetnames:
        cd = wb["CIA待異動"]
        cd.delete_rows(1, cd.max_row)
    else:
        cd = wb.create_sheet("CIA待異動")
    cd.append(CIA_DIFF_COLS)
    for d in merged:
        _append_safe(cd, d)
    if not merged:
        cd.append(["（本次比對沒有不一致）"])

    # 未知序號：系統查無的資產序號逐筆列出（每次整張重寫，不疊加）
    if UNKNOWN_SHEET in wb.sheetnames:
        del wb[UNKNOWN_SHEET]
    uk = wb.create_sheet(UNKNOWN_SHEET)
    uk.append(UNKNOWN_COLS)
    for u in unknowns:
        _append_safe(uk, u)
    if not unknowns:
        uk.append(["（本次沒有未知序號）"])

    # 三方差異：整張重寫（冪等）；只列有變動的格，「未改」只計數
    if THREE_SHEET in wb.sheetnames:
        del wb[THREE_SHEET]
    ts = wb.create_sheet(THREE_SHEET)
    ts.append(THREE_COLS)
    if base_status == "missing":
        _append_safe(ts, ["（無法三方比對）", "", "", "", "", C_NOBASE, base_reasons[0]])
    else:
        from openpyxl.styles import PatternFill
        if base_status == ST_SUSPECT:
            _append_safe(ts, [f"（{SUSPECT_TEXT}）", "", "", "", "", "基準疑點", "；".join(base_reasons)])
        elif base_status == ST_REF:
            _append_safe(ts, ["（無簽章（舊版檔），僅供參考）", "", "", "", "", "基準疑點", "；".join(base_reasons)])
        for tr in three_rows:
            _append_safe(ts, tr)
            fill = THREE_FILL.get(tr[5])
            if fill:
                ts.cell(ts.max_row, 6).fill = PatternFill("solid", fgColor=fill)
        if not three_rows:
            ts.append(["（本次沒有任何變動）"])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    # 單位：same／diff／filled 是「格」（儲存格比對），unknown_serial 是「列」（Master 的資料列），
    # diff_rows 是 CIA待異動 分頁的「列」，diff_machines 是「台」。
    summary = {"same": n_same, "diff": n_diff, "filled": n_fill, "unknown_serial": n_unknown,
               "diff_rows": len(diffs), "diff_machines": len({str(d[0]).strip() for d in diffs}),
               "history_kept_rows": kept_history,
               "three_way": {"baseline_status": base_status, "baseline_reasons": base_reasons,
                             "signature": sig_state,
                             "note": SUSPECT_TEXT if base_status == ST_SUSPECT else "",
                             "counts": three_counts},
               "units": {"same": "格", "diff": "格", "filled": "格", "unknown_serial": "列",
                         "diff_rows": "列", "diff_machines": "台", "history_kept_rows": "列",
                         "three_way": "格"}}
    return buf, summary
