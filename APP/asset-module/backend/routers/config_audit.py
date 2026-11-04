from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()



@router.post("/api/config-audit/import")
def config_audit_import(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯入一份檢核結果 .txt。

    截斷的檔案會被擋下來（腳本宣稱的條數跟實際對不起來）——少查的條目
    如果照收，畫面上會變成「那些都沒問題」，那是最危險的誤讀。
    """
    import config_audit

    blob = file.file.read()
    if not blob:
        raise HTTPException(400, "檔案是空的")
    try:
        return config_audit.import_report(
            conn, blob, file.filename,
            session["username"] if session else None)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"解析失敗：{exc}") from exc


@router.post("/api/config-audit/collect")
def config_audit_collect(
    body: ConfigAuditCollectBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """一鍵：SSH 到那台跑組態檢核腳本、收回結果直接匯入 9-2。
    AIX（2026-10-01）＋Linux（Q84，2026-10-18）；唯讀腳本、收集帳號執行，Linux 的 root 項目走
    唯讀輔助程式 config-audit。VMware 設備（ESXi、vCenter）一律拒絕；Windows 走 WinRM（公司基準 286 條，需本機管理員）。
    納管成功後 post_onboard 也會自動跑一次。"""
    import config_audit

    try:
        return config_audit.collect_from_host(
            conn, body.asset_serial, by=session["username"] if session else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"收集失敗：{exc}") from exc


@router.get("/api/config-audit/windows-settings")
def config_audit_windows_settings_get(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Windows 組態檢核（公司基準 286 條，會在目標機執行 secedit／auditpol）開關現值。預設開。"""
    import win_fcb

    return {"enabled": win_fcb.enabled(conn), "label": win_fcb.BASELINE_LABEL}


class WinAuditSettingsBody(BaseModel):
    enabled: bool


@router.put("/api/config-audit/windows-settings")
def config_audit_windows_settings_put(
    body: WinAuditSettingsBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """開／關 Windows 組態檢核（關掉後「收集全部」的組態檢核那一步對 Windows 顯示略過＋原因）。
    （操作紀錄由全站 PUT 中介層記。）"""
    import win_fcb
    from db import set_setting

    set_setting(conn, win_fcb.SETTING_ENABLED, "1" if body.enabled else "0")
    conn.commit()
    return {"enabled": win_fcb.enabled(conn), "label": win_fcb.BASELINE_LABEL}


@router.get("/api/config-audit")
def config_audit_list(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """清單：每台一列，取最新一份。同時回總覽數字供上方卡片用。"""
    import config_audit

    return {"items": config_audit.runs(conn), "summary": config_audit.summary(conn)}


@router.get("/api/config-audit/history")
def config_audit_history(
    limit: int = 50,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯入紀錄，含失敗的——匯過但壞掉要看得見，不然沒人知道。"""
    import config_audit

    return {"items": config_audit.history(conn, limit)}


@router.get("/api/config-audit/unmatched")
def config_audit_unmatched(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """有檢核結果、卻對不回資產清冊的主機——這本身就是發現。"""
    import config_audit

    return {"items": config_audit.unmatched(conn)}


@router.get("/api/config-audit/export")
def config_audit_export(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯出給 DYN 分析：吐回各機器最新一份的**原始檔全文**，不是重組的。"""
    import config_audit
    from fastapi.responses import PlainTextResponse

    text, have, missing = config_audit.export_status(conn)
    if not text.strip():
        if missing:
            raise HTTPException(404,
                f"有 {missing} 份檢核結果，但都是在系統開始保存原始檔之前匯入的，"
                "沒有原文可以吐給 DYN。把那幾個 .txt 重新匯入一次就可以了"
                "（不用重跑檢核腳本）。")
        raise HTTPException(404, "還沒有任何檢核結果可以匯出")
    return PlainTextResponse(text, headers={
        "Content-Disposition": 'attachment; filename="config_audit_export.txt"'})


@router.get("/api/config-audit/items")
def config_audit_items(
    platform: str | None = None,
    result: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """平台表數字的下鑽：跨主機列出條目（例如 Linux 的不合規是哪幾條、在哪台）。
    筆數跟 /api/config-audit 的 summary.by_platform 同一份計算。
    路由要放在 /{run_id} 前面，不然 items 會被當成 run_id。"""
    import config_audit

    try:
        items = config_audit.items_across(conn, platform or None, result or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"items": items, "total": len(items)}


@router.get("/api/config-audit/by-asset")
def config_audit_by_asset(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """某一台的組態檢核（資產查詢的分頁）。

    路由要放在 /{run_id} **前面**——不然 `by-asset` 會被當成 run_id 吃掉，
    回 422。這種撞路由的錯，畫面只會顯示「載入失敗」，很難查。
    """
    import config_audit

    return config_audit.by_asset(conn, asset_serial)


@router.get("/api/config-audit/{run_id}")
def config_audit_detail(
    run_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """單機明細：全部條目列出來，四態各自分開。"""
    import config_audit

    d = config_audit.detail(conn, run_id)
    if not d:
        raise HTTPException(404, "查無這份檢核結果")
    return d


@router.get("/api/data-quality")
def data_quality_summary(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """資料品質量測：盤點清單到底準不準，用查得到來源的數字講。

    只對「有機器事實可以對照」的維度給正確率；保管者/用途/CIA 這種人為判斷欄位
    給填寫率與新鮮度，並在畫面上講清楚衡量的不是對錯——硬給正確率是在編數字。
    """
    import data_quality

    return data_quality.measure(conn)


@router.get("/api/data-quality/single-person-names")
def data_quality_single_names(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """只出現一次的人名——打錯字的高風險候選（2026-09-17 使用者：人名改可搜尋新增後，
    要有地方定期檢查是不是有人打錯字新增了近似名）。跨 使用者/保管者/人員名冊 統計，
    出現一次的列出來給人看（可能是真的只管一台，也可能是「李太明」打成「李太名」）。

    ⚠️ 路由要排在 /api/data-quality/{dim_key} 之前，否則 single-person-names 會被吃成 dim_key。"""
    rows = conn.execute(
        """WITH names AS (
               SELECT TRIM(user_name) AS n FROM hardware WHERE TRIM(COALESCE(user_name,'')) <> ''
               UNION ALL SELECT TRIM(custodian) FROM hardware WHERE TRIM(COALESCE(custodian,'')) <> ''
               UNION ALL SELECT TRIM(person_name) FROM personnel WHERE TRIM(COALESCE(person_name,'')) <> ''
           )
           SELECT n, COUNT(*) AS c FROM names GROUP BY n HAVING c = 1 ORDER BY n"""
    ).fetchall()
    return {"names": [r["n"] for r in rows], "total": len(rows)}


@router.post("/api/data-quality/person-merge/preview")
def data_quality_person_merge_preview(
    body: PersonMergeBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """人名合併「預覽」：把打錯的名字併到正確名字前，先看會動到哪些欄位、各幾筆（不寫入）。
    ⚠️ 路由要排在 /api/data-quality/{dim_key} 之前。"""
    import data_quality
    try:
        return data_quality.preview_person_merge(conn, body.from_name, body.to_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/data-quality/person-merge")
def data_quality_person_merge_apply(
    body: PersonMergeBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """人名合併「執行」：預覽確認後才呼叫，實際把錯名併成正確名。回各欄改了幾筆。"""
    import data_quality
    try:
        return data_quality.apply_person_merge(conn, body.from_name, body.to_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/data-quality/field-fill")
def data_quality_field_fill(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """各欄位填充率（升冪，最低在前）＝「待補資料」清單：欄位先立好、機器後補，
    這裡一眼看出哪些欄位收得最少、該去補（呼應機房搬遷的欄位先立）。
    ⚠️ 路由要排在 /api/data-quality/{dim_key} 之前，否則會被吃成 dim_key。"""
    import hardware_master
    allf = hardware_master.field_dictionary(conn)
    # 系統狀態欄位（待處理旗標、收集錯誤、時間戳、*_override／*_source）可以合法為空，
    # 不是「使用者該補的資料」；分開列，不混進待補清單（Codex W221-015，2026-10-31）。
    fields = [f for f in allf if not is_system_state_field(f["column"])]
    system_fields = [f for f in allf if is_system_state_field(f["column"])]
    fields.sort(key=lambda f: (f["fill_rate"] if f["fill_rate"] is not None else 1.0, f["column"]))
    system_fields.sort(key=lambda f: f["column"])
    total = conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0]
    return {"fields": fields, "system_fields": system_fields, "total_rows": total}


_SYSTEM_STATE_EXACT = {"pending_at", "pending_by", "pending_note", "pending_status", "root_key_at",
                       "collect_error", "manual_updated_at", "collect_ok", "collect_checked_at",
                       "updated_at", "created_at", "last_seen", "first_seen"}


def is_system_state_field(column: str) -> bool:
    c = (column or "").lower()
    return (c in _SYSTEM_STATE_EXACT or c.endswith("_override") or c.endswith("_source")
            or c.startswith("pending_") or c.startswith("collect_"))


@router.get("/api/data-quality/{dim_key}")
def data_quality_offenders(
    dim_key: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """某維度不合格的是哪幾台。數字一定要能下鑽，不然沒有行動意義。"""
    import data_quality

    return {"items": data_quality.list_offenders(conn, dim_key)}


@router.get("/api/drift")
def drift_list(
    status: str = Query("open"),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """基線失效清單：上線時刻意設定成這樣、現在不一樣了的項目。"""
    return {"drifts": golive.list_drift(conn, status if status != "all" else None)}


@router.get("/api/drift/summary")
def drift_summary(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """空狀態三態：無已上線基線／已回檢 N 台／尚未回檢（含最後回檢時間、unknown 數）。"""
    return golive.drift_summary(conn)


@router.post("/api/drift/{drift_id}/disposition")
def drift_disposition(
    drift_id: int,
    body: DriftDispositionBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """標記處置。沿用帳號盤點 finding_disposition 的精神：標過的下次回檢還記得，
    不要每天重新亮一次同一條紅燈；但 ack 只是「我知道了」，不代表基線改了——
    機器恢復成基線時仍然會自動轉 fixed。"""
    if body.status not in ("open", "ack", "fixed"):
        raise HTTPException(400, f"不支援的處置狀態：{body.status}")
    n = conn.execute(
        "UPDATE baseline_drift SET status = ?, note = ?, decided_by = ?, decided_at = ? "
        "WHERE id = ?",
        (body.status, body.note, session["username"],
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), drift_id),
    ).rowcount
    conn.commit()
    if not n:
        raise HTTPException(404, "找不到這筆基線失效紀錄")
    return {"ok": True}


@router.post("/api/drift/recheck")
def drift_recheck(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """手動觸發回檢。平常掛在每日掃描之後自動跑（見 scan_service）。"""
    return golive.run_drift_check(conn)


# ---- 掃描空殼併回（2026-10-16，v1.439.0）----
# 規則全在 dyn_shell.py；這裡只是薄薄一層 HTTP。預覽不寫入；人工併回只在使用者於
# /scan-shells 頁勾選、確認後才由前端呼叫。證據確定的由部署／patch 跑 `dyn_shell.py auto-merge`
# 自動併（決策 DYN2）；啟動、開頁面都不會觸發。

class ShellPair(BaseModel):
    shell_serial: str
    target_serial: str


class ShellApplyBody(BaseModel):
    pairs: list[ShellPair]


@router.get("/api/machine-key")
def machine_key_trial(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """台數口徑（Q79）試算頁：舊規則 vs 主機名口徑、停損 a/b/c、合併與待確認明細、最近一次判定。唯讀。"""
    import machine_identity

    out = machine_identity.analyse(conn)
    out["mode"] = machine_identity.effective_mode(conn)
    out["decision"] = machine_identity.last_decision(conn)
    return out


@router.get("/api/scan-shells")
def scan_shells_preview(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """預覽：每一筆 DYN- 歸類成 可併回／需人工／未登記，附併回紀錄。**不寫入**。"""
    import dyn_shell

    out = dyn_shell.preview(conn)
    # 還留在「可併回」的＝沒過自動併回條件（DYN2），把原因附在理由後面
    for it in out["items"]:
        if it["kind"] == "mergeable":
            row = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?",
                               (it["shell_serial"],)).fetchone()
            ok, why = dyn_shell.auto_check(conn, dict(row), it)
            it["auto_ok"] = ok
            if not ok:
                it["reason"] = f"{it['reason']}；未自動併：{why}"
    out["history"] = dyn_shell.history(conn)
    return out


@router.post("/api/scan-shells/apply")
def scan_shells_apply(
    body: ShellApplyBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """執行併回：使用者在預覽畫面勾選、確認後才呼叫。每組先留快照、後端重新比對，對不上就略過。"""
    import dyn_shell

    if not body.pairs:
        raise HTTPException(400, "沒有勾選任何一筆")
    return dyn_shell.apply(conn, [p.model_dump() for p in body.pairs], session["username"])


@router.post("/api/scan-shells/restore/{audit_id}")
def scan_shells_restore(
    audit_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """照快照還原一次併回。"""
    import dyn_shell

    try:
        return dyn_shell.restore(conn, audit_id, session["username"])
    except ValueError as e:
        raise HTTPException(400, str(e))


__all__ = [
    'config_audit_import',
    'config_audit_collect',
    'config_audit_list',
    'config_audit_history',
    'config_audit_unmatched',
    'config_audit_export',
    'config_audit_items',
    'config_audit_by_asset',
    'config_audit_detail',
    'data_quality_summary',
    'data_quality_single_names',
    'data_quality_person_merge_preview',
    'data_quality_person_merge_apply',
    'data_quality_field_fill',
    'data_quality_offenders',
    'drift_list',
    'drift_disposition',
    'drift_recheck',
    'machine_key_trial',
    'scan_shells_preview',
    'scan_shells_apply',
    'scan_shells_restore',
]
