from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()



# ===== 資料可信度評分（2026-09-11）=====
# 規則與配分在 trust_score.py（使用者拍板的錨點由測試盯著）。這裡只是薄 HTTP 層。

@router.get("/api/trust/distribution")
def trust_distribution_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """100 分幾台、90 分幾台…＋來源組合各幾台。每個數字都能用 /api/trust/hosts 下鑽。"""
    import trust_score

    return trust_score.distribution(conn)


@router.get("/api/trust/hosts")
def trust_hosts_endpoint(
    score: int | None = None,
    combo: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """某個分數（或某個來源組合）是哪幾台。跟分布表同一份計算，數字一定對得上。"""
    import trust_score

    return {"items": trust_score.hosts_with(conn, score, combo)}


@router.get("/api/trust/asset/{asset_serial}")
def trust_asset_endpoint(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """單台的分數拆解（資產詳細頁用）。"""
    import trust_score

    r = trust_score.compute_all(conn).get(asset_serial)
    if r is None:
        raise HTTPException(404, "查無此資產，或它是退役資產（退役資產不評分）")
    return r


# ---------------------------------------------------------------------------
# 組態檢核（選單 9-2）。使用者 2026-09-21：「先做 AIX 那份，弱掃以後再改善」。
#
# 吃的是各平台檢核腳本產出的純文字檔（Linux 61 條／AIX 98 條／Windows 286 條）。
# 平台一律讀表頭的「平台:」，**不看檔名**——檔名可以被改。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AD 人員名單（B-23）。關聯鍵是**員編**：主機帳號 gecos 開頭就是員編，
# AD 的 SamAccountName／employeeID 也是。姓名會改、會同名，不能當鍵。
#
# ⚠️ 這是全體員工個資：檔案只暫存在記憶體解析後就丟，**不落磁碟**。
# ---------------------------------------------------------------------------


@router.post("/api/ad/import")
def ad_import_endpoint(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯入 AD 人員名單（.xlsx／.csv）。整份取代——AD 名單是全量快照。"""
    import ad_import

    blob = file.file.read()
    if not blob:
        raise HTTPException(400, "檔案是空的")
    try:
        rows = ad_import.rows_from_bytes(blob, file.filename)
        return ad_import.import_rows(conn, rows, file.filename,
                                     session["username"] if session else None)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"解析失敗：{exc}") from exc


# ===== 收集帳號遷移（2026-09-24 統一成 webit3sc）=====
# 設計見 AI/收集帳號遷移設計_2026-09-24.md。這三支**全部唯讀**：
# 探測只跑 `id`，不切換任何一台、不刪任何帳號。

@router.get("/api/collect-account/migration")
def collect_account_migration_summary(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """遷移看板：四個數字＋分母講清楚是哪些。"""
    import collect_account_migration as cam

    return cam.summary(conn)


@router.get("/api/collect-account/migration/detail")
def collect_account_migration_detail(
    state: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """下鑽：每台的證據（幾點測的、兩個帳號各自的結果與錯誤原文）。"""
    import collect_account_migration as cam

    return {"items": cam.detail(conn, state)}


@router.post("/api/collect-account/migration/probe")
def collect_account_migration_probe(
    asset_serial: str | None = None,
    limit: int = 200,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """探測：對每台用新帳號與舊帳號各試一次 `id`。**唯讀。**

    分批（`limit`）是刻意的：三千台 × 2 次 SSH 不可以一次打完，
    而且中途被中斷時**已經測完的要留著**（每台獨立寫入，不用整批 transaction）。
    """
    import collect_account_migration as cam
    import manage_state

    sql = ("SELECT asset_serial, ip, os FROM hardware "
           "WHERE ip IS NOT NULL AND ip != '' AND collect_ok = 1")
    args: tuple = ()
    if asset_serial:
        sql += " AND asset_serial = ?"
        args = (asset_serial,)
    sql += f" LIMIT {max(1, min(limit, 500))}"
    rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        try:
            plat = manage_state.collect_platform_for(conn, r["ip"], r["asset_serial"])
            out.append(cam.probe_one(conn, r["asset_serial"], r["ip"], plat))
        except Exception as exc:  # noqa: BLE001 - 單台失敗不中斷整批
            out.append({"asset_serial": r["asset_serial"], "ip": r["ip"],
                        "error": str(exc)[:200]})
    return {"probed": len(out), "items": out, "summary": cam.summary(conn)}



@router.post("/api/collect-account/migration/switch")
def collect_account_migration_switch(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把**這一台**的收集帳號切成新的。

    ⚠️ 切換前會**重新探測這一台**，只有當下仍然是「可切換」才切——
    看板上的狀態是上一次探測的結果，可能已經過期，
    拿過期的狀態去切那台會立刻收不到（2026-09-23 就是這個形狀）。
    """
    import collect_account_migration as cam

    try:
        return cam.switch_one(conn, asset_serial, by=(session["username"] if session else None))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/collect-account/migration/revert")
def collect_account_migration_revert(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把這一台改回沿用全域設定。**還原路徑一定要有**——
    切了才發現收不到時要能立刻退回去，而不是等人去改資料庫。"""
    import collect_account_migration as cam

    return cam.revert_one(conn, asset_serial, by=(session["username"] if session else None))


@router.get("/api/collect-account/migration/history")
def collect_account_migration_history(
    asset_serial: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """切換紀錄（含換帳號前後的收集筆數）。

    沒有這個對照，**數字變大跟資料跑掉在畫面上長得一樣**——
    公司那八台 AIX 現在是假成功，遷移後數字會暴增。
    """
    import collect_account_migration as cam

    return {"items": cam.switch_history(conn, asset_serial)}


@router.get("/api/collect-account/migration/removal-plan")
def collect_account_migration_removal_plan(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """要移除舊帳號的清單。**唯讀，只是列出來。**"""
    import collect_account_migration as cam

    return cam.removal_plan(conn)


@router.get("/api/collect-account/migration/removal-script")
def collect_account_migration_removal_script(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """移除舊帳號的指令清單（純文字）。

    ⚠️ **系統不代為執行。** 刪帳號要 root，而收集帳號是唯讀非 root；
    為了一次性的遷移給常駐收集身分刪帳號的權力並不划算——那條權限會一直留著，
    卻只用得到一次。所以產出純文字讓管理員看過再自己跑，
    不做 base64、不做一鍵管道、不落地暫存檔。
    """
    from fastapi.responses import PlainTextResponse

    import collect_account_migration as cam

    return PlainTextResponse(cam.removal_script(conn))



@router.get("/api/ad/summary")
def ad_summary_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯入概況。每個數字都要點得進去，所以下面三支是對應的下鑽端點。"""
    import ad_import

    return ad_import.summary(conn)


@router.get("/api/ad/unmatched-managers")
def ad_unmatched_managers(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """主管 DN 對不到名單裡任何一列的人。

    「沒有主管」跟「對不上」是兩件事——前者不用處理，後者是名單有缺。
    """
    import ad_import

    return {"items": ad_import.unmatched_managers(conn)}


@router.get("/api/ad/unmatched-accounts")
def ad_unmatched_accounts(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """主機帳號對不到人的兩種情況，分開回——原因與處理方式不同。"""
    import ad_import

    return ad_import.unmatched_accounts(conn)


@router.get("/api/ad/disabled-with-account")
def ad_disabled_with_account(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """AD 已停用（離職）但主機上還留著帳號——**這是稽核發現，不是雜訊**。"""
    import ad_import

    return {"items": ad_import.disabled_with_host_account(conn)}


@router.get("/api/export-key")
def export_key_status(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """這台在加密匯出這件事上是什麼角色（決策 SEC5）。

    兩個角色，一台可以同時是（但實務上不會）：
      · **收件端**：持私鑰，解得開別人寄來的匯出。公鑰顯示給人複製。
      · **來源端**：存了收件人公鑰，匯出時用它加密。自己解不開自己的匯出。
    """
    import export_crypto

    priv_path = Path(export_crypto.private_key_path())
    has_private = priv_path.exists()
    out = {
        "is_recipient": has_private,
        "private_key_path": str(priv_path),
        "my_public_key": None,
        "my_fingerprint": None,
        "recipient_fingerprint": None,
        "encryption_enabled": export_crypto.encryption_enabled(conn),
        "can_export": not export_crypto.encryption_enabled(conn),
    }
    if has_private:
        pub = export_crypto.public_key_of()
        out["my_public_key"] = pub.hex()
        out["my_fingerprint"] = export_crypto.fingerprint_hex(pub)
    recipient = export_crypto.get_recipient_public(conn)
    if recipient is not None:
        out["recipient_fingerprint"] = export_crypto.fingerprint_hex(recipient)
        out["can_export"] = True
    return out


@router.put("/api/export-key/encryption")
def export_key_encryption(
    body: ExportEncryptBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """開／關「匯出時加密」。預設關（2026-09-18 使用者）。"""
    import export_crypto
    return {"encryption_enabled": export_crypto.set_encryption_enabled(conn, body.enabled)}


@router.post("/api/export-key/generate")
def export_key_generate(
    session: sqlite3.Row = Depends(require_auth),
):
    """在收件端產生金鑰對。私鑰落地 0600，公鑰回傳給人複製到來源端。

    ⚠️ 已經有私鑰就**拒絕**，不覆蓋——覆蓋之後過去所有加密匯出永遠打不開，
    而且是無聲的（畫面顯示成功，要還原時才發現）。真的要換金鑰，
    人得自己去刪掉那個檔，那一步逼他先想清楚舊備份怎麼辦。
    """
    import export_crypto

    try:
        return export_crypto.generate_keypair()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/api/export-key/recipient")
def export_key_set_recipient(
    body: RecipientKeyBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """在來源端設定收件人公鑰。公鑰不需要保密，直接貼上即可。

    存之前先驗格式——貼錯（貼到指紋、貼到 SSH 公鑰）如果留到匯出當下才爆，
    那時候人已經在等檔案了。
    """
    import export_crypto

    try:
        return export_crypto.set_recipient_public(conn, body.public_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/accounts")
def list_accounts_endpoint(
    ip: str | None = None,
    asset_serial: str | None = None,
    kind: str | None = None,
    sudoer_only: bool = False,
    hide_builtin: bool = False,
    include_gone: bool = False,
    sort_by: str = "ip",
    order: str = "asc",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import account_inventory

    rows = account_inventory.list_accounts(
        conn, ip=ip, asset_serial=asset_serial, kind=kind,
        include_gone=include_gone, sudoer_only=sudoer_only,
    )

    # 「拉掉內建帳號」：只藏乾淨的系統/內建帳號，被稽核點名的一律留著——
    # 把有 finding 的內建帳號藏起來，正好會蓋掉 UID 0 後門這種最該看的東西。
    # 藏了幾個會回報，不靜默丟（靜默截斷會讓人以為「就這些帳號」）。
    hidden_builtin = 0
    if hide_builtin:
        # 只有「實質違規(fail)」才讓內建帳號留下。不能用全部 finding——
        # 沒 root 時每個帳號都有一堆 unknown(查不到)，那會讓每個帳號都被保護、
        # 篩選器一個都藏不掉（真機 306 條 unknown 就是這情形）。
        # unknown 是「這欄沒查到」不是「這帳號有問題」，不該撐住整列。
        flagged = {(f["ip"], f["username"])
                   for f in account_inventory.latest_findings(conn)
                   if f["verdict"] == "fail"}
        kept = []
        for r in rows:
            if r.get("is_builtin") and (r["ip"], r["username"]) not in flagged:
                hidden_builtin += 1
                continue
            kept.append(r)
        rows = kept

    key = sort_by if sort_by in ALLOWED_ACCOUNT_SORT else "ip"
    reverse = order == "desc"

    def sv(r):
        v = r.get(key)
        if v is None or v == "":
            return (1, "")
        if key == "ip" and isinstance(v, str) and v.count(".") == 3:
            try:
                return (0, tuple(int(p) for p in v.split(".")))
            except ValueError:
                return (0, v)
        return (0, v if not isinstance(v, str) else v.lower())

    try:
        rows.sort(key=sv, reverse=reverse)
    except TypeError:
        rows.sort(key=lambda r: (r.get(key) is None, str(r.get(key) or "")), reverse=reverse)
    return {"items": rows, "summary": account_inventory.audit_summary(conn),
            "hidden_builtin": hidden_builtin}


@router.get("/api/accounts/findings")
def account_findings_endpoint(
    severity: str | None = None,
    rule_id: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """稽核發現。這是這個模組真正的產出——清單只是中間產物。"""
    import account_inventory
    import account_rules

    return {
        "items": account_inventory.latest_findings(conn, severity=severity, rule_id=rule_id),
        "summary": account_inventory.audit_summary(conn),
        "rules": [{"id": r["id"], "label": r["label"], "severity": r["severity"],
                   "law": r["law"]} for r in account_rules.RULES],
        "thresholds": account_rules.get_thresholds(conn),
    }


@router.get("/api/accounts/findings/export")
def export_findings(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把稽核發現匯出成 Excel——稽核當天要交的是可存檔的證據表，不是叫人看畫面。

    每條含：風險/項目/帳號/帳號備註/主機/IP/判定/處置狀態/例外到期/覆核人/覆核時間/
    手動備註/法規依據。含處置狀態＝這份報告本身就是稽核軌跡。
    """
    import account_inventory

    findings = account_inventory.latest_findings(conn)
    sev_label = {"high": "高", "medium": "中", "low": "低"}
    status_label = {"open": "待處理", "ack": "已確認", "exception": "核准例外", "fixed": "已修復"}

    wb = Workbook()
    ws = wb.active
    ws.title = "帳號稽核發現"
    ws.append(["風險", "項目", "帳號", "帳號備註", "主機", "IP", "判定",
               "處置狀態", "例外到期", "覆核人", "覆核時間", "手動備註", "法規依據"])
    for f in findings:
        ws.append([
            "查不到" if f.get("verdict") == "unknown" else sev_label.get(f.get("severity"), ""),
            f.get("label"), f.get("username"), f.get("gecos"),
            f.get("hostname") or "", f.get("ip"), f.get("detail"),
            status_label.get(f.get("status"), f.get("status")),
            f.get("exempt_until") or "", f.get("decided_by") or "", f.get("decided_at") or "",
            f.get("note") or "", f.get("law"),
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"帳號稽核發現_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    from urllib.parse import quote
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/api/accounts/matrix/export")
def export_matrix(
    cols: list[str] = Query(default=[]),
    kind: str | None = None,
    hide_builtin: bool = False,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """合規表匯出：使用者自選欄位，資料最小化給稽核看。

    只吐勾選的欄位——稽核不需要看到 UID、shell、金鑰把數這些內部細節，
    給越少越好。狀態欄的計算走 account_inventory.MATRIX_EXPORT_COLS（與前端同源）。
    """
    import account_inventory

    valid = account_inventory.MATRIX_EXPORT_COLS
    chosen = [c for c in cols if c in valid]
    if not chosen:
        raise HTTPException(400, "至少要選一個有效欄位")

    rows = account_inventory.list_accounts(conn, kind=kind or None)
    if hide_builtin:
        flagged = {(f["ip"], f["username"])
                   for f in account_inventory.latest_findings(conn) if f["verdict"] == "fail"}
        rows = [r for r in rows
                if not (r.get("is_builtin") and (r["ip"], r["username"]) not in flagged)]

    wb = Workbook()
    ws = wb.active
    ws.title = "帳號合規表"
    ws.append([valid[c][0] for c in chosen])
    for r in rows:
        ws.append([valid[c][1](r) for c in chosen])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"帳號合規表_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    from urllib.parse import quote
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/api/collector-key")
def get_collector_key(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """現況：指紋、公鑰、換掉會影響幾台。**不回傳私鑰。**

    「會影響幾台」是這頁最重要的數字——沒有它，換金鑰看起來只是改個設定，
    實際上是讓那些主機的收集當場失效（它們 authorized_keys 裡是舊公鑰）。
    """
    import collector_key
    from db import get_setting

    st = collector_key.status(conn)
    st["last_import"] = get_setting(conn, "collector_key_last_import", None)
    return st


@router.post("/api/collector-key/import")
def import_collector_key(
    body: CollectorKeyBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯入收集金鑰。要打 REPLACE 確認。

    為什麼要打字確認：這個動作會讓**已經在運作的東西停止運作**——所有已納管主機
    的收集會當場失效，除非匯入的正是它們認得的那把。跟取消納管同一個道理，
    按鈕太容易誤觸。

    ⚠️ 私鑰進來就寫檔，**不回傳、不寫 log、不進稽核紀錄**。
    稽核只記「誰在什麼時候換了金鑰、新舊指紋各是什麼」——指紋是公開資訊，
    足以追溯且洩漏不出東西。
    """
    import collector_key

    if body.confirm != "REPLACE":
        raise HTTPException(400, "匯入金鑰會讓所有已納管主機的收集失效，請輸入 REPLACE 確認")
    try:
        if body.from_path:
            result = collector_key.adopt_from_path(
                conn, body.from_path, session["username"])
        elif body.private_key:
            result = collector_key.import_key(
                conn, body.private_key, body.public_key, session["username"])
        else:
            raise HTTPException(400, "要嘛貼上私鑰，要嘛給主機上的檔案路徑")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    result["status"] = collector_key.status(conn)
    result["next_step"] = (
        "金鑰已換。**如果這不是原本那把**，已納管主機的收集會失效——"
        "請到「納入管理」重新納管，或確認目標主機的 authorized_keys 裡是這把新公鑰。"
        if result["changed"] else
        "指紋跟原本一樣——這就是同一把金鑰，已納管主機不受影響。")
    return result


@router.get("/api/accounts/export")
def export_accounts(
    fmt: str = "standard",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """帳號盤點匯出，兩種格式（使用者 2026-08-28 指定）。

    · `standard`：公司現行的 18 欄格式，可以直接交出去
    · `full`：系統知道的全部欄位，給自己人查用

    ⚠️ `type_id` 只自動填確定的四種（1 最高權限／2 系統預設／5 我們的收集帳號／
    7 人員使用）。3 程式運行、4 資料庫運行、6 Anchor·APPM 從 /etc/passwd 分不出來
    ——使用者明講目前沒有邏輯、要人工判斷，所以**留空讓人在 Excel 裡填**。
    填一個看起來對的值，代價是稽核文件上的假資料，而且沒有人會發現。

    回應標頭 `X-Export-Summary` 帶「還差多少才完整」的數字，讓呼叫端顯示出來——
    只給一個檔案而不講「其中 N 筆的類型要人工填」，人會以為它是完整的。
    """
    import json as _json

    import account_export

    if fmt not in ("standard", "full"):
        raise HTTPException(400, "fmt 只接受 standard（公司標準格式）或 full（全匯出）")

    # 2026-09-18 起跟資料匯入頁的「帳號盤點」匯出同一份（account_audit.export_xlsx）：
    # 值仍來自 account_export（18 欄、type 只填 1／2／5／7），再加上沿用上次人工 type、
    # 保留使用者檔案的欄、跟上次比較。兩個地方按出來的檔案一模一樣，不會有兩套。
    import account_audit

    buf, res = account_audit.export_xlsx(conn, "standard" if fmt == "standard" else "all")
    summary = {
        "rows": len(res["rows"]),
        "unclassified_type": res["unclassified_type"],
        "type_carried": res["type_carried"],
        "rows_without_system_name": (res.get("summary") or {}).get("rows_without_system_name", 0),
    }
    label = "帳號盤點" if fmt == "standard" else "帳號全匯出"
    fname = f"{label}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    from urllib.parse import quote
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}",
            "X-Export-Summary": quote(_json.dumps(summary, ensure_ascii=False)),
            "Access-Control-Expose-Headers": "X-Export-Summary, Content-Disposition",
        },
    )


@router.post("/api/accounts/collect")
def collect_accounts_endpoint(
    asset_serial: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import account_inventory

    try:
        return account_inventory.collect_accounts(
            conn, only_serial=asset_serial, trigger="manual")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"帳號採集失敗：{exc}") from exc


@router.get("/api/accounts/collect-config")
def get_collect_config(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """目前的遠端收集身分，以及切成管理帳號時要先做的授權動作。

    webit3 用自己的金鑰、但以設定的身分登入——所以切成 sysinfra 前，要先把
    webit3 收集公鑰授權進各機的 sysinfra/.ssh/authorized_keys（一次性、可撤）。
    這裡回傳那把公鑰與現成指令，讓使用者用既有管道（巡檢 ansible）佈下去。
    """
    import manage_state

    current = manage_state.get_collect_account(conn)
    # 走同一支解析函式：沒有金鑰就當場產一把，不要叫人去命令列跑腳本
    # （使用者 2026-08-16 指正：這是畫面功能的前提，不該依賴一次人工動作）
    try:
        pubkey = onboard_engine.collector_pubkey()
    except ValueError as exc:
        pubkey = f"（{exc}）"
    collector_ip = onboard_engine.resolve_collector_ip(conn)
    from db import get_setting

    ip_setting = (get_setting(conn, onboard_engine.COLLECTOR_IP_SETTING, "") or "").strip()
    return {
        "account": current,
        # 收集器自己的位址：會被寫進目標主機 authorized_keys 的 from= 來源限制。
        # 填錯不會報錯，只會讓金鑰永遠被拒而納管顯示成功——所以畫面要看得到它是誰、
        # 從哪來的（畫面設定／環境變數／自動偵測）。
        "collector_ip": collector_ip,
        "collector_ip_source": ("畫面設定" if ip_setting
                                else "環境變數" if os.environ.get("ASSET_COLLECTOR_IP")
                                else "自動偵測"),
        "collector_ip_detected": onboard_engine.detect_collector_ip(),
        "options": [
            {"value": "webit3scan", "label": "webit3scan（唯讀最小權限）",
             "note": "看不到需 root 的欄位（密碼效期、sudo 明細、authorized_keys）"},
            {"value": "sysinfra", "label": "sysinfra（標準管理帳號，完整資料）",
             "note": "NOPASSWD:ALL，sudo -n 全通；需先授權收集公鑰進 sysinfra"},
        ],
        "pubkey": pubkey,
        # 收集帳號在 /etc/passwd 的備註欄。實際值存 DB 不進版控（含員工編號與姓名）。
        "account_comment": onboard_engine.resolve_account_comment(conn),
        "provision_hint": (
            "切成 sysinfra 前，先在各主機把上面這把公鑰加進 "
            "~sysinfra/.ssh/authorized_keys（用巡檢 ansible 一次佈完）。"
            "webit3 不持有 sysinfra 私鑰，授權隨時可撤（移掉該行即失效）。"
        ),
    }


@router.put("/api/accounts/collect-config")
def set_collect_config(
    body: CollectAccountBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    from db import set_setting

    if body.account not in _allowed_collect_accounts():
        raise HTTPException(
            400, f"不支援的收集身分：{body.account}（只接受 "
                 f"{', '.join(sorted(_allowed_collect_accounts()))}）")
    set_setting(conn, "collect_ssh_account", body.account)

    # 帳號備註（/etc/passwd 的 GECOS）。**實際值不進版控**——使用者要填的是
    # 「員工編號-姓名_部門_系統」，裡面有個資，而納管引擎的原始碼會進公開 relay。
    # 空字串＝清掉，退回中性預設（不是把空值當成「設成空的」）。
    if body.account_comment is not None:
        val = body.account_comment.strip()
        if val:
            try:
                onboard_engine.sanitize_account_comment(val)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
        set_setting(conn, onboard_engine.ACCOUNT_COMMENT_SETTING, val)

    if body.collector_ip is not None:
        val = body.collector_ip.strip()
        if val:
            try:
                onboard_engine.validate_collector_ip(val)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
        # 空字串＝清掉，退回自動偵測（不是把空值當成「設成空的」）
        set_setting(conn, onboard_engine.COLLECTOR_IP_SETTING, val)
    return {"account": body.account,
            "collector_ip": onboard_engine.resolve_collector_ip(conn),
            "account_comment": onboard_engine.resolve_account_comment(conn)}


# ===== 盤點主機清單：每種盤點都先回答「是哪幾台」（2026-09-16）=====
# 使用者：「我只知道四台? 不知道哪四台，應該是先列出哪幾台，我再進去點後才是那一台的全部資訊」
# ——並補「**每個盤點都是這概念**」。服務／軟體／EOS 共用同一支，見 inventory_hosts。


@router.get("/api/inventory/{kind}/hosts")
def inventory_hosts_endpoint(
    kind: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """某一種盤點實際收到資料的主機清單。kind：service／software／eos。"""
    import inventory_hosts

    if kind == "eos":
        items = inventory_hosts.eos_hosts(conn)
    elif kind in inventory_hosts.KINDS:
        items = inventory_hosts.hosts(conn, kind)
    else:
        raise HTTPException(404, f"沒有這種盤點：{kind}")
    return {"items": items, "total": len(items)}


@router.get("/api/inventory/{kind}/hosts/export")
def inventory_hosts_export(
    kind: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """主機清單匯出 Excel（使用者：「沒匯出功能，不方便找 IP」）。"""
    import inventory_hosts
    from urllib.parse import quote

    if kind == "eos":
        headers, rows, title = inventory_hosts.EOS_HEADERS, inventory_hosts.eos_export_rows(conn), "EOS"
    elif kind in inventory_hosts.KINDS:
        headers = inventory_hosts.headers(kind)
        rows = inventory_hosts.export_rows(conn, kind)
        title = inventory_hosts.KINDS[kind]["label"]
    else:
        raise HTTPException(404, f"沒有這種盤點：{kind}")

    wb = Workbook()
    ws = wb.active
    ws.title = f"{title}盤點主機清單"[:31]
    ws.append(headers)
    for r in rows:
        ws.append(["" if v is None else v for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"{title}盤點主機清單_{_now_local()[:10]}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


@router.get("/api/accounts/inventoried-hosts")
def account_inventoried_hosts(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """**盤點到哪幾台**，每台幾個帳號、幾個稽核發現（2026-09-16 使用者：
    「我只知道四台? 不知道哪四台，應該是先列出哪幾台，我再進去點後才是那一台的全部帳號資訊」）。"""
    import account_inventory

    hosts = account_inventory.inventoried_hosts(conn)
    return {"items": hosts, "total": len(hosts)}


@router.get("/api/accounts/privileged-trend")
def accounts_privileged_trend(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """特權帳號趨勢：每次採集後存的一筆現值，累積成時間序列（2026-09-19）。
    資料是採集後才開始累積的——點數少代表剛開始收，不是沒有特權帳號。"""
    import account_inventory

    return {"items": account_inventory.privileged_trend(conn)}


@router.get("/api/accounts/by-username")
def accounts_by_username(
    username: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """這個帳號存在於哪幾台（2026-09-17 使用者：「帳號 我想要找 例如 webit3 有哪幾台有這帳號」）。

    跟「主機→帳號」相反的問法，實務上同樣重要：停用離職者帳號、確認服務帳號佈到哪些機器。
    回傳帶部門與窗口——下一步就是去找人。
    """
    import account_inventory

    items = account_inventory.hosts_with_account(conn, username)
    return {"username": username, "items": items, "total": len(items)}


@router.get("/api/accounts/by-username/export")
def accounts_by_username_export(
    username: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """反查結果匯出 Excel——拿去給窗口對的就是這張。"""
    import account_inventory
    from urllib.parse import quote

    wb = Workbook()
    ws = wb.active
    ws.title = f"{username}"[:31] or "帳號反查"
    ws.append(account_inventory.ACCOUNT_HOSTS_HEADERS)
    for r in account_inventory.account_hosts_export_rows(conn, username):
        ws.append(["" if v is None else v for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"帳號反查_{username}_{_now_local()[:10]}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


@router.get("/api/accounts/inventoried-hosts/export")
def account_inventoried_hosts_export(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """主機清單匯出 Excel（使用者：「沒匯出功能，不方便找 IP」）。"""
    import account_inventory
    from urllib.parse import quote

    wb = Workbook()
    ws = wb.active
    ws.title = "帳號盤點主機清單"
    ws.append(account_inventory.HOST_EXPORT_HEADERS)
    for r in account_inventory.hosts_export_rows(conn):
        ws.append(["" if v is None else v for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"帳號盤點主機清單_{_now_local()[:10]}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


@router.get("/api/accounts/hosts")
def list_account_hosts(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """可收集的主機清單＋是否已排除。給排除管理與健檢選單用。"""
    import account_inventory

    return {"hosts": account_inventory.list_collectable_hosts(conn)}


@router.put("/api/accounts/findings/disposition")
def set_finding_disposition_endpoint(
    body: DispositionBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """設一條稽核發現的處置狀態（待處理/已確認/核准例外/已修復）。跨盤點持久。

    decided_by 記登入者——稽核要能查「這條例外是誰、何時核准的」。
    """
    import account_inventory

    try:
        return account_inventory.set_finding_disposition(
            conn, body.ip, body.username, body.rule_id, body.status,
            note=body.note, exempt_until=body.exempt_until,
            decided_by=session["username"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/api/accounts/note")
def set_account_note_endpoint(
    body: AccountNoteBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """設某帳號的「手動備註」——稽核人員自己輸入的註記（跟 gecos 自動備註是兩回事）。"""
    import account_inventory

    try:
        return account_inventory.set_account_note(conn, body.ip, body.username, body.note)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/api/accounts/kind")
def set_account_kind_endpoint(
    body: AccountKindBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """手動改某帳號的類型——程式判錯時用（例如 CBoss 被判成內建帳號、實際是服務帳號）。
    收集不覆寫；傳空字串＝清掉覆寫回到程式判定。"""
    import account_inventory

    try:
        return account_inventory.set_account_kind(conn, body.ip, body.username, body.kind)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/api/accounts/exclude")
def set_account_exclude(
    body: ExcludeBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把一台主機排除／納回帳號稽核。排除時清掉它的舊帳號與稽核資料。"""
    import account_inventory

    if conn.execute("SELECT 1 FROM hardware WHERE asset_serial = ?",
                    (body.asset_serial,)).fetchone() is None:
        raise HTTPException(404, "查無此資產")
    return account_inventory.set_host_excluded(conn, body.asset_serial, body.exclude)


@router.get("/api/accounts/diagnose")
def diagnose_account_host(
    asset_serial: str,
    desensitize: bool = True,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """逐主機收集健檢：即時跑每條收集指令，回報為什麼收不到。

    給公司多 OS 上線 debug 用。安全：不含任何原始輸出內容（見 collect_probe），
    再走 diagnostics 的去識別化＋殘留掃描閘門，可安全匯出貼給開發者。
    desensitize 預設 True——真實資料不出這台機器是硬規則。
    """
    import collect_probe
    import diagnostics as dg
    import manage_state

    row = conn.execute(
        "SELECT ip FROM hardware WHERE asset_serial = ?", (asset_serial,)
    ).fetchone()
    if not row or not row["ip"]:
        raise HTTPException(404, "查無此資產或它沒有 IP")

    account = manage_state.get_collect_account(conn)
    try:
        report = collect_probe.probe(
            row["ip"], account, manage_state.COLLECTOR_KEY_DEFAULT,
            local_ips=manage_state.local_ips())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"健檢執行失敗：{exc}") from exc

    if desensitize:
        d = dg.Desensitizer(enabled=True)
        report = d.walk(report)
        residual = dg.residual_scan(json.dumps(report, ensure_ascii=False))
        report["_desensitized"] = True
        report["_residual_scan"] = residual or "通過（未發現未遮蔽的位址）"
        # 殘留掃描沒過就不給——真實位址不出這台機器
        if residual:
            raise HTTPException(
                500, f"健檢輸出殘留掃描未通過，已擋下不外送：{residual[:3]}")
    return report


@router.get("/api/accounts/sudo-rules")
def account_sudo_rules(session: sqlite3.Row = Depends(require_auth)):
    """收集帳號要拿到密碼/sudo/金鑰資訊所需的 sudo 白名單。

    刻意做成「給你看、你自己決定要不要佈」而不是自動佈署：
    這是擴權動作，且會改動所有已納管主機的 sudoers。
    白名單本身不含 `cat /etc/shadow`——chage -l／passwd -S 拿得到同樣的稽核結論
    卻不吐密碼雜湊，能用小權限達成就不該要大的。
    """
    import account_collector

    return {
        "rules": account_collector.SUDO_RULES,
        "note": "存成 /etc/sudoers.d/webit3scan-audit（chmod 440），"
                "並用 visudo -cf 驗證後才生效。不含 /etc/shadow，不會洩漏密碼雜湊。",
    }


@router.get("/api/account-audit/template")
def account_audit_template(session: sqlite3.Row = Depends(require_auth)):
    import account_audit
    return _xlsx_response(account_audit.template_xlsx(), "帳號盤點_範例.xlsx")


@router.get("/api/account-audit/status")
def account_audit_status(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """最近匯入的上次盤點批次、歷史、類型代碼對照。"""
    import account_audit
    b = account_audit.latest_batch(conn)
    return {"latest": b, "history": account_audit.history(conn),
            "type_table": [{"code": c, "info": i} for c, i in account_audit.TYPE_TABLE],
            "standard_default": account_audit.STANDARD_DEFAULT}


@router.post("/api/account-audit/import")
async def account_audit_import(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import account_audit
    data = await file.read()
    try:
        out = account_audit.import_file(conn, data, file.filename or "upload", session["username"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # 原始檔留一份（跟其他來源一樣在端點存，模組本身不碰磁碟——否則測試會寫進正式的原始檔目錄）。
    # dump 匯回不算原始檔，不覆蓋。
    fname = (file.filename or "upload.xlsx")
    if not fname.lower().endswith(".webit3dump") and data[:2] != b"\x1f\x8b":
        import import_export
        try:
            import_export.save_original("account_audit", fname, data)
        except OSError as exc:
            out["original_saved"] = False
            out["original_error"] = str(exc)
    return out


@router.get("/api/account-audit/export")
def account_audit_export(
    mode: str = "standard",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """mode=standard：A～R（照最後匯入的表頭）＋比較分頁；mode=all：加內部管理欄與比較欄。"""
    import account_audit
    if mode not in ("standard", "all"):
        raise HTTPException(400, "mode 只接受 standard 或 all")
    buf, _res = account_audit.export_xlsx(conn, mode)
    tag = "標準A-R" if mode == "standard" else "全部"
    return _xlsx_response(buf, f"帳號盤點_{tag}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx")



@router.get("/api/accounts/search-explain")
def accounts_search_explain(
    q: str = "",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """合規表搜尋 0 筆時的說明：這個詞在資產庫對得到幾台、幾台還沒收集帳號（2026-09-18）。"""
    import account_inventory
    return account_inventory.explain_search(conn, q)


@router.get("/api/accounts/coverage")
def accounts_coverage(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """帳號收集涵蓋率（2026-09-18 十項盤點第 9 條）。

    221 上收到帳號的只有 1 台（它自己），在管 3,116 台——合規表、帳號盤點看起來像
    「全公司只有 33 個帳號」，畫面卻沒講這件事。「沒有資料」跟「資料很少」要分得開。
    在管的母體跟首頁「在管」同一套（host_view：排退役＋帳外、machine_key 去重）。
    """
    import host_view

    hv = host_view.list_hosts(conn)
    managed_ips = {(h.get("ip") or "").strip() for h in hv["hosts"] if h.get("ip")}
    got = {r[0] for r in conn.execute(
        "SELECT DISTINCT ip FROM host_account WHERE gone_at IS NULL") if r[0]}
    in_managed = len(got & managed_ips)
    return {
        "managed_hosts": hv["total_hosts"],
        "collected_hosts": len(got),
        "collected_managed": in_managed,
        "collected_outside": len(got) - in_managed,   # 收到了但不在在管清單（帳外／未登記）
        "pct": round(in_managed / hv["total_hosts"] * 100, 2) if hv["total_hosts"] else None,
    }


__all__ = [
    'trust_distribution_endpoint',
    'trust_hosts_endpoint',
    'trust_asset_endpoint',
    'ad_import_endpoint',
    'collect_account_migration_summary',
    'collect_account_migration_detail',
    'collect_account_migration_probe',
    'collect_account_migration_switch',
    'collect_account_migration_revert',
    'collect_account_migration_history',
    'collect_account_migration_removal_plan',
    'collect_account_migration_removal_script',
    'ad_summary_endpoint',
    'ad_unmatched_managers',
    'ad_unmatched_accounts',
    'ad_disabled_with_account',
    'export_key_status',
    'export_key_encryption',
    'export_key_generate',
    'export_key_set_recipient',
    'list_accounts_endpoint',
    'account_findings_endpoint',
    'export_findings',
    'export_matrix',
    'get_collector_key',
    'import_collector_key',
    'export_accounts',
    'collect_accounts_endpoint',
    'get_collect_config',
    'set_collect_config',
    'inventory_hosts_endpoint',
    'inventory_hosts_export',
    'account_inventoried_hosts',
    'accounts_privileged_trend',
    'accounts_by_username',
    'accounts_by_username_export',
    'account_inventoried_hosts_export',
    'list_account_hosts',
    'set_finding_disposition_endpoint',
    'set_account_note_endpoint',
    'set_account_kind_endpoint',
    'set_account_exclude',
    'diagnose_account_host',
    'account_sudo_rules',
    'account_audit_template',
    'account_audit_status',
    'account_audit_import',
    'account_audit_export',
    'accounts_search_explain',
    'accounts_coverage',
]
