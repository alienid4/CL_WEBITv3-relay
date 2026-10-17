from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()



@router.post("/api/documents/import")
def documents_import(
    files: list[UploadFile] = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """批次歸檔既有的 Word 單據（.doc／.docx 都吃）。

    只抽識別欄位建索引、把原檔存起來，**不解析表格內容**（見 doc_import.py 檔頭）。
    一份檔案失敗不能讓整批中斷——那會逼使用者一次次試錯找出是哪一份有問題。
    """
    import doc_import

    ok, failed = [], []
    for f in files:
        name = Path(f.filename or "doc").name
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(name).suffix) as tmp:
                tmp.write(f.file.read())
                tmp_path = Path(tmp.name)
            try:
                ok.append(doc_import.import_document(
                    conn, tmp_path, _doc_archive_dir(), session["username"],
                    original_name=name, refresh=False,
                ))
            finally:
                tmp_path.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001 - 逐份回報，不讓一顆壞蘋果毀掉整批
            failed.append({"file_name": name, "error": str(e)})
    # 整批做完才算一次「每個 IP 現行的是哪一張」——逐份跑是 O(N²) 的全表掃描。
    doc_import.refresh_current_flags(conn)
    return {
        "imported": len(ok), "failed": len(failed), "results": ok, "errors": failed,
        "auto_bound": sum(1 for r in ok if r["bind_confidence"] == "auto"),
        "need_review": sum(1 for r in ok if r["bind_confidence"] == "review"),
    }


@router.get("/api/documents")
def documents_list(
    confidence: str | None = Query(None),
    q: str | None = Query(None),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """單據清單／搜尋。q 連 Word 內文一起搜——使用者以前要找一筆資料得把每份檔案
    打開來看，只搜檔名和單號等於沒解決那個問題。命中會附上片段。"""
    import doc_import

    return {
        "documents": doc_import.list_documents(conn, confidence, q),
        "summary": doc_import.summary(conn),
    }


@router.get("/api/documents/{doc_id}/download")
def documents_download(
    doc_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """下載原始 Word。索引查到了卻打不開原檔，等於沒歸檔。"""
    row = conn.execute("SELECT * FROM doc_archive WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "找不到這份單據")
    path = Path(row["file_path"])
    if not path.exists():
        raise HTTPException(410, f"索引還在，但原始檔不見了：{row['file_name']}")
    # 全文檢索那份已遮掉帳密，但原始 Word 沒有（那是稽核證據，不能改）。
    # 既然一定要能下載，至少留下「誰在什麼時候看了哪一份」。
    conn.execute(
        "INSERT INTO doc_download_audit (doc_id, file_name, username, downloaded_at) "
        "VALUES (?, ?, ?, ?)",
        (doc_id, row["file_name"], session["username"],
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    return FileResponse(path, filename=row["file_name"])


@router.post("/api/documents/{doc_id}/review")
def documents_review(
    doc_id: int,
    body: DocReviewBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """人工審核：確認或修正抽出來的規格值（CPU／記憶體／硬碟／OS 版本）。

    使用者 2026-08-15 要求的關卡。自由填寫欄抓歪不會報錯，唯一有效的防線就是有人看過；
    沒審過的值標成 pending，不參與任何比對，避免「沒人看過的數字被當成事實」。
    """
    import doc_import

    try:
        return doc_import.review_document(conn, doc_id, body.values, session["username"])
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/api/documents/{doc_id}/bind")
def documents_bind(
    doc_id: int,
    body: DocBindBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import doc_import

    try:
        doc_import.bind_document(conn, doc_id, body.asset_serial)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.get("/api/import/last")
def import_last(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    row = get_latest_import_log(conn)
    return dict(row) if row else None


@router.get("/api/import/field-mapping")
def import_field_mapping(session: sqlite3.Row = Depends(require_auth)):
    """D14精神：欄位對應可調整、不寫死。回傳目前的field_mapping.json內容。"""
    return load_mapping()


@router.put("/api/import/field-mapping")
def update_import_field_mapping(
    body: FieldMappingBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """value為「不匯入」的項目不寫入設定檔（等於移除對應）；其餘value要對得上目標資料表
    的實際欄位名稱（PRAGMA table_info動態查，不寫死欄位清單，schema改了這裡自動跟上）。

    _comment說明欄位：load_mapping()讀取時會把它拿掉（不是真的分頁對應資料），這裡存檔
    要把原本檔案裡的_comment原封放回去，不然存一次設定檔裡給後人看的說明文字就消失了。
    """
    cleaned: dict[str, dict[str, str]] = {}
    for sheet, columns in body.mapping.items():
        if sheet not in SHEET_CONFIG:
            raise HTTPException(400, f"不支援的分頁：{sheet}")
        valid_columns = _table_columns(conn, SHEET_CONFIG[sheet]["table"])
        cleaned[sheet] = {}
        for excel_header, system_field in columns.items():
            if system_field == NO_IMPORT_SENTINEL:
                continue
            if system_field not in valid_columns:
                raise HTTPException(
                    400, f"{sheet}分頁「{excel_header}」對應到不存在的欄位：{system_field}"
                )
            cleaned[sheet][excel_header] = system_field

    existing_raw = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    to_write = {}
    if "_comment" in existing_raw:
        to_write["_comment"] = existing_raw["_comment"]
    to_write.update(cleaned)

    MAPPING_PATH.write_text(
        json.dumps(to_write, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return cleaned


@router.post("/api/import/excel")
def import_excel_upload(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """故意用sync def（不是async def）：UploadFile.file是同步可讀的SpooledTemporaryFile，
    sync路由可以直接讀，不需要await。

    ⚠️ 原本這裡寫著「FastAPI 會把 get_db 依賴跟 sync 路由排進同一個 threadpool worker
    執行緒」——那個假設是錯的，並行時兩者可能落在不同 worker，會炸
    sqlite3「不同執行緒不能共用連線」。真正的修正在 db.get_connection()
    （check_same_thread=False），不是靠 sync/async 的寫法去賭執行緒。
    """
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 檔案")

    contents = file.file.read()
    # 原始檔留一份（只留最新的），之後「匯出原始檔」才有東西可以給。
    # 存檔失敗不該讓匯入整個失敗——資料已經進去了，少一份存檔是小事，
    # 但要留下痕跡，不能讓人以為有存到。
    try:
        import import_export

        import_export.save_original("cia_excel", file.filename or "upload", contents)
    except Exception as exc:  # noqa: BLE001
        print(f"[import] 原始檔保存失敗（cia_excel，不影響匯入）：{exc}")

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        try:
            summary = import_excel(tmp_path, conn)
        except Exception as exc:  # noqa: BLE001 - openpyxl對壞檔案的錯誤型態不一，統一攔截如實回報
            raise HTTPException(400, f"匯入失敗，請確認檔案格式：{exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    create_import_log(
        conn,
        imported_by=session["username"],
        hardware_count=summary["sheets"].get("硬體", {}).get("inserted", 0)
        + summary["sheets"].get("硬體", {}).get("updated", 0),
        personnel_count=summary["sheets"].get("人員", {}).get("inserted", 0)
        + summary["sheets"].get("人員", {}).get("updated", 0),
        software_count=summary["sheets"].get("軟體", {}).get("inserted", 0)
        + summary["sheets"].get("軟體", {}).get("updated", 0),
        error_count=len(summary["errors"]),
        source="cia_excel", file_name=file.filename,
    )
    return summary


@router.post("/api/import/rvtools")
def import_rvtools_upload(
    file: UploadFile = File(...),
    data_at: str | None = Form(None),
    force: bool = Form(False),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """S19 VC 採集器：吃一份 RVTools 匯出的 vCenter 盤點（vInfo 分頁）。

    每台 VM 走身分解析：對到既有資產就更新機器事實（不碰業務欄位），新的建成 VC- 資產，
    判不準的進人工審核佇列（不自動合併）。真實 vCenter 資料，非假資料。

    ## 時序保護（data_at / force）

    匯入是「後蓋前」，沒有時間概念——不小心傳了一份舊的匯出檔，裡面的 os／is_vm
    就會直接蓋掉比較新的值，而且系統沒有欄位變更歷史，蓋掉就救不回來
    （只能翻每日備份，超過保留天數就沒了）。

    所以這裡收 data_at（前端帶上傳檔案的最後修改時間），跟上次成功匯入的時間比：
    比上次舊就先擋下來、回報兩邊時間讓人確認，確定要覆蓋才帶 force=true 再送一次。
    沒帶 data_at 就照舊直接匯（例如 curl 手動呼叫），不強制。
    """
    import rvtools_import
    from db import get_setting, set_setting

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 RVTools 匯出的 .xlsx 檔案")

    prev_at = get_setting(conn, RVTOOLS_LAST_DATA_AT, "") or ""
    if data_at and prev_at and not force and data_at < prev_at:
        # 用 409 而不是 400：這不是「檔案有問題」，是「請你確認一下」，
        # 前端要據此跳確認框而不是當成錯誤紅字。
        raise HTTPException(
            409,
            {
                "code": "stale_import",
                "message": "這份檔案的資料時間比系統現有的舊，直接匯入會用舊資料覆蓋新的。",
                "incoming_data_at": data_at,
                "current_data_at": prev_at,
                "hint": "確定要覆蓋就再送一次並帶 force=true。",
            },
        )

    contents = file.file.read()
    # 原始檔留一份（只留最新的），之後「匯出原始檔」才有東西可以給。
    # 存檔失敗不該讓匯入整個失敗——資料已經進去了，少一份存檔是小事，
    # 但要留下痕跡，不能讓人以為有存到。
    try:
        import import_export

        import_export.save_original("rvtools", file.filename or "upload", contents)
    except Exception as exc:  # noqa: BLE001
        print(f"[import] 原始檔保存失敗（rvtools，不影響匯入）：{exc}")

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)
    try:
        try:
            summary = rvtools_import.import_rvtools(tmp_path, conn)
        except ValueError as exc:
            # 「這份檔不對」——使用者自己就能判斷、能修（例如根本沒有 vInfo 分頁）
            raise HTTPException(400, f"匯入失敗，這份檔看起來不是 RVTools 匯出：{exc}") from exc
        except Exception as exc:  # noqa: BLE001
            # 其他例外是**我們自己的 bug**，不是使用者的檔案有問題。
            # 2026-08-20 踩過：五個貨真價實的 RVTools 匯出檔全部收到
            # 「請確認是 RVTools 匯出的檔：Object of type datetime is not JSON serializable」
            # ——訊息把責任推給使用者，害人去反覆檢查根本沒問題的檔案。
            # 分不出來就不要亂猜原因，如實說是系統的錯，並把錯誤原文給出來。
            raise HTTPException(
                500,
                f"匯入時系統發生錯誤（不是你的檔案有問題，請把這段訊息回報）："
                f"{type(exc).__name__}: {exc}",
            ) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    create_import_log(
        conn, imported_by=session["username"],
        hardware_count=summary["inserted"] + summary["updated"],
        personnel_count=0, software_count=0, error_count=len(summary["errors"]),
        source="rvtools", file_name=file.filename,
        # 這份是「哪天從 vCenter 匯出的」，不是「哪天匯進系統」。取不到就存 None，
        # 不拿匯入時間頂替——「不知道多舊」跟「是今天的」是完全不同的兩句話。
        exported_at=rvtools_import.export_time_from_filename(file.filename),
    )

    # 記下這份的資料時間，供下次比對。強制覆蓋舊資料時不要往回退，
    # 否則之後每一份新檔都會被拿來跟這個舊時間比，保護就失效了。
    if data_at and (not prev_at or data_at > prev_at):
        set_setting(conn, RVTOOLS_LAST_DATA_AT, data_at)
    summary["data_at"] = data_at or None
    summary["previous_data_at"] = prev_at or None
    return summary


@router.get("/api/import/log")
def list_import_log_endpoint(
    source: str | None = None, limit: int = 20,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """最近的匯入紀錄——2026-08-19 使用者原話「要有紀錄表讓我查」：一次要匯好幾份
    RVTools檔案時，需要看得出哪些已經匯過（檔名+時間），不用憑記憶判斷。
    source 篩選：rvtools/cia_excel/dynassets/scan_import；不帶就全部混著回。
    """
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit 只接受 1–100")
    return [dict(r) for r in list_import_log(conn, source=source, limit=limit)]


@router.get("/api/import/dynassets/export-template")
def dynassets_export_template(session: sqlite3.Row = Depends(require_auth)):
    """空白範本——同 segments 那支的理由。查證時發現的落差：畫面文案只講
    「需含 IP 欄」，但實際上還吃主機名／MAC／OS，範本要把全部欄位列出來，
    不能讓人以為只填 IP 就夠。
    """
    import dynassets_import

    headers, example = dynassets_import.export_template_rows()
    wb = Workbook()
    ws = wb.active
    ws.title = "存活清單範本"
    ws.append(headers)
    ws.append(example)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from urllib.parse import quote

    fname = "存活清單_空白範本.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/api/import/{source}/export-template")
def import_export_template(source: str, session: sqlite3.Row = Depends(require_auth)):
    """其餘匯入的範例檔（系統類別／業務系統對照表／RVTools／CIA）。
    2026-09-10 使用者：「每一個都要範例檔案，不然 USER 不會知道要匯入什麼資料」。
    範例列以「範例」開頭，原封不動匯回來會被略過，見 import_templates。"""
    import import_templates
    from urllib.parse import quote

    try:
        fname, data = import_templates.build(source)
    except KeyError:
        raise HTTPException(404, f"沒有這種匯入的範例檔：{source}") from None
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


# ===== 每一種匯入都配一個匯出（2026-09-09）=====
# 使用者：「Dynaset 有匯入但沒有匯出的功能……我要選原始檔、Excel 的匯出，
# 跟 dump 格式的匯出。也要有 dump 格式的匯入」，並補「rvtools、CIA 資產清冊都要」。
#
# 三種格式回答的是**不同的問題**，不是同一份東西的三種包裝：
#   原始檔 = 當初送進來的那個檔（可能有系統看不懂而丟掉的欄位）
#   Excel  = 現在系統裡是什麼（正規化之後的）
#   dump   = 整包搬到另一台
# 混為一談會讓人以為匯出的 Excel 可以拿去跟來源系統對帳。
#
# ⚠️ dump 目前**不加密**（使用者 2026-09-09：「先不加密」）。裡面是完整的
# 資產／存活清單，畫面上要明講這件事，不要讓人當備份寄出去。


@router.get("/api/import/{source}/export/original")
def export_original(
    source: str,
    session: sqlite3.Row = Depends(require_auth),
):
    """把當初上傳的那個檔原樣送回去。

    **沒有就回 404 並說清楚為什麼**——這個功能上線之前的匯入沒有留原始檔
    （舊流程處理完就把暫存檔刪了）。這種時候不可以拿重新產生的檔頂替：
    那會讓人拿它去跟來源系統對帳，對不起來卻找不到原因。
    """
    import import_export

    try:
        path = import_export.original_for(source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if path is None:
        raise HTTPException(404, "這個來源沒有留原始檔——可能是還沒匯入過，"
                                 "或那次匯入早於「保留原始檔」這個功能上線之前")
    from urllib.parse import quote

    return StreamingResponse(
        io.BytesIO(path.read_bytes()),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name)}"},
    )


@router.get("/api/import/{source}/export/excel")
def export_excel(
    source: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """現在資料庫裡這個來源的內容，做成 Excel。"""
    import import_export

    try:
        headers, rows = import_export.export_rows(conn, source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    def _xl(v):
        # Excel 不收控制字元（\x00-\x08、\x0b…），openpyxl 遇到直接丟例外→整份匯出 500。
        # 從別的系統複製貼上的資料常夾帶，拿掉不影響可讀性（原始檔匯出不經過這裡，照原樣）。
        if v is None:
            return ""
        return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v

    wb = Workbook()
    ws = wb.active
    ws.title = import_export.SOURCES[source].label[:31]
    ws.append([_xl(h) for h in headers])
    for r in rows:
        ws.append([_xl(v) for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from urllib.parse import quote

    fname = f"{import_export.SOURCES[source].label}_{_now_local()[:10]}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/api/import/{source}/export/dump")
def export_dump(
    source: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """整包搬家用的格式（gzip 過的 JSON）。**目前不加密。**"""
    import import_export

    try:
        data = import_export.build_dump(conn, source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    from urllib.parse import quote

    fname = f"{source}_{_now_local()[:10].replace('-', '')}.webit3dump"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/gzip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/api/import/sources")
def list_import_sources(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """每個來源現在有什麼可以匯出——**畫面要靠這個決定按鈕能不能按**。

    「按了才發現沒有」跟「一開始就看得出沒有」差很多：前者會讓人以為系統壞了。
    """
    import import_export

    out = []
    for key, src in import_export.SOURCES.items():
        try:
            orig = import_export.original_for(key)
        except ValueError:
            orig = None
        try:
            _, rows = import_export.export_rows(conn, key)
            n = len(rows)
        except Exception:  # noqa: BLE001 - 單一來源壞掉不該讓整頁掛掉
            n = 0
        out.append({
            "key": key, "label": src.label, "row_count": n,
            "origin": src.origin,
            "has_original": orig is not None,
            "original_name": orig.name if orig else None,
            "original_size": orig.stat().st_size if orig else None,
        })
    return {"sources": out}


@router.post("/api/import/dump/preview")
def import_dump_preview(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
):
    """先看這個 dump 是什麼，**不寫入任何東西**。

    為什麼一定要有這一步：dump 匯入會覆蓋現有資料，而檔名看不出裡面是哪個
    來源、幾筆、什麼時候匯出的。讓人先看一眼再按，比事後還原便宜得多。
    """
    import import_export

    try:
        body = import_export.read_dump(file.file.read())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"source": body.get("source"), "row_count": body.get("row_count"),
            "headers": body.get("headers", [])[:50],
            "label": import_export.SOURCES.get(body.get("source"), None)
            and import_export.SOURCES[body["source"]].label}


@router.post("/api/import/dynassets")
def import_dynassets_upload(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """dynassets 匯入：吃「存活掃描＋CMDB」清單（.csv／.xlsx），跟 CIA 登記對帳。

    每列走身分解析：對到既有資產就更新機器事實（不碰業務欄位）、掃到但沒登記的建成
    DYN- 資產（＝漏登記／帳外資產）、判不準的進人工審核佇列（不自動合併）。
    dynassets 是每次重新產生的存活快照，不做 RVTools 那種時序保護。
    """
    import dynassets_import

    fn = (file.filename or "").lower()
    if not (fn.endswith(".csv") or fn.endswith(".xlsx")):
        raise HTTPException(400, "只接受 dynassets 的 .csv 或 .xlsx 檔案")
    suffix = ".xlsx" if fn.endswith(".xlsx") else ".csv"

    contents = file.file.read()
    # 原始檔留一份（只留最新的），之後「匯出原始檔」才有東西可以給。
    # 存檔失敗不該讓匯入整個失敗——資料已經進去了，少一份存檔是小事，
    # 但要留下痕跡，不能讓人以為有存到。
    try:
        import import_export

        import_export.save_original("dynassets", file.filename or "upload", contents)
    except Exception as exc:  # noqa: BLE001
        print(f"[import] 原始檔保存失敗（dynassets，不影響匯入）：{exc}")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)
    try:
        try:
            summary = dynassets_import.import_dynassets(tmp_path, conn)
        except Exception as exc:  # noqa: BLE001 - 壞檔/非dynassets格式統一如實回報
            raise HTTPException(400, f"匯入失敗，請確認是 dynassets 存活清單：{exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    create_import_log(
        conn, imported_by=session["username"],
        hardware_count=summary["inserted"] + summary["updated"],
        personnel_count=0, software_count=0, error_count=len(summary["errors"]),
        source="dynassets", file_name=file.filename,
    )
    return summary


__all__ = [
    'documents_import',
    'documents_list',
    'documents_download',
    'documents_review',
    'documents_bind',
    'import_last',
    'import_field_mapping',
    'update_import_field_mapping',
    'import_excel_upload',
    'import_rvtools_upload',
    'list_import_log_endpoint',
    'dynassets_export_template',
    'import_export_template',
    'export_original',
    'export_excel',
    'export_dump',
    'list_import_sources',
    'import_dump_preview',
    'import_dynassets_upload',
]
