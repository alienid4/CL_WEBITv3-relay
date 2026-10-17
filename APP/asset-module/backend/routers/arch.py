from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()



@router.get("/api/arch/vcenter")
def arch_vcenter(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """vCenter 虛擬化架構圖資料（從 CI 圖即時算，可下鑽到 ESXi）。"""
    import arch

    return arch.vcenter_topology(conn)


@router.get("/api/arch/ocp")
def arch_ocp(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """OCP 叢集架構圖：叢集 → 節點，附每個節點的納管狀態與叢集層風險。"""
    import arch

    return arch.ocp_topology(conn)


@router.get("/api/arch/ocp/siblings")
def arch_ocp_siblings(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """這台的兄弟節點（同一座 OCP 叢集）——出事時要一眼看出關聯。"""
    import arch

    return arch.ocp_siblings(conn, asset_serial)


@router.put("/api/arch/ocp/cluster")
def set_ocp_cluster(
    body: OcpClusterBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """人工指定某一台屬於哪座 OCP 叢集（判不出來的由人自己換群組）。指定的是整台。"""
    import ocp_cluster

    try:
        return ocp_cluster.set_cluster(conn, body.asset_serial, body.cluster, body.reason,
                                       session["username"] if session else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/api/arch/ocp/role")
def set_ocp_role(
    body: OcpRoleBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """人工指定某一台的 OCP 角色（2026-09-21 使用者：「我要怎麼定義角色」）。

    自動判斷只看用途／主機名的關鍵字，寫不出來就卡在「未標示」；
    這裡讓人訂正，指定值優先，改的是整台（跟指定叢集同一套規矩）。
    """
    import ocp_cluster

    try:
        return ocp_cluster.set_role(conn, body.asset_serial, body.role, body.reason,
                                    session["username"] if session else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/arch/ftp")
def arch_ftp(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """FTP 主機架構圖資料（成員清單人維護存 app_settings，屬性 join hardware 真資料）。"""
    import arch

    return arch.ftp_diagram(conn)


@router.get("/api/arch/vm-placement/{asset_serial}")
def arch_vm_placement(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """這台 VM 歸哪個 VC/cluster、跑在哪台 ESXi（資產詳細頁用）。"""
    import arch

    return arch.vm_placement(conn, asset_serial)


# ===== 網段配置表 =====
# 來源是公司的「總分公司網段配置表」Excel。三個用途：IP 配置的三層選單、
# 掃描範圍的依據（弱掃說明的排除註記）、資料品質涵蓋率的分母。

@router.get("/api/segments")
def segments_list(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import segments

    return {"segments": segments.list_segments(conn)}


@router.get("/api/segments/tree")
def segments_tree(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """機房 → 環境 → 網段，給新增資產的 IP 選單用。"""
    import segments

    return {"tree": segments.tree(conn)}


@router.get("/api/segments/scan-candidates")
def segments_scan_candidates(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """該掃哪些網段／哪些被註記排除。資料品質頁「涵蓋率 0%」的下一步就是看這個。"""
    import segments

    return segments.scan_candidates(conn)


@router.get("/api/segments/ips")
def segments_ips(
    cidr: str = Query(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """這段裡已登記的 IP 與建議可用的下一個。"""
    import segments

    try:
        return segments.segment_ips(conn, cidr)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/segments/export-template")
def segments_export_template(session: sqlite3.Row = Depends(require_auth)):
    """空白範本（表頭＋一筆範例）。2026-08-25 使用者提出的通則：有匯入就要有配對的
    匯出範本，不然新使用者不知道要填什麼——這是「匯入網段配置表」旁邊那顆
    「⬇ 下載空白範本」的資料來源，緊鄰上傳按鈕（不是放頁首、不是放另一頁）。
    """
    import segments

    headers, example = segments.export_template_rows()
    wb = Workbook()
    ws = wb.active
    ws.title = "網段配置表範本"
    ws.append(headers)
    ws.append(example)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from urllib.parse import quote

    fname = "網段配置表_空白範本.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/api/segments/export")
def segments_export(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯出目前系統裡的網段清單（可直接再匯入）。

    2026-08-26 使用者：「我是有匯入，我沒有匯出嗎？」——原本只有空白範本，
    資料進得去出不來，就沒辦法拿系統的清單去跟 Excel 對帳、也沒辦法把在系統裡
    修好的版本交回去。
    """
    import segments

    headers, rows = segments.export_current_rows(conn)
    wb = Workbook()
    ws = wb.active
    ws.title = "網段配置表"
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from urllib.parse import quote

    fname = f"網段配置表_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.post("/api/segments/import")
def segments_import(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯入網段配置表（Excel 為主，也吃 Tab 分隔文字檔）。

    **整批取代**：Excel 是這份清單的唯一真相，段被刪掉就該從系統消失。
    解析失敗（一格兩段、IP 範圍）的列照樣入庫並列進警告——靜默丟掉網段，
    之後不會有人發現，而「系統裡沒有這段」跟「這段不存在」是兩件事。
    """
    import segments

    name = Path(file.filename or "segments.xlsx").name
    if Path(name).suffix.lower() not in (".xlsx", ".xlsm", ".txt", ".tsv", ".csv"):
        raise HTTPException(400, "只接受 Excel（.xlsx）或 Tab 分隔文字檔")
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(name).suffix) as tmp:
        tmp.write(file.file.read())
        tmp_path = Path(tmp.name)
    try:
        summary = segments.import_segments(conn, tmp_path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"file": name, **summary}


@router.put("/api/systems/class")
def set_system_class_endpoint(
    body: SystemClassBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """手動改一個業務系統的分級——分級表沒涵蓋（未分級）或判錯時用。
    分級表重匯不會沖掉；傳空＝清除覆寫回到分級表判定。"""
    import business_system

    try:
        return business_system.set_system_class(conn, body.api_id, body.sys_class)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/business-systems")
def list_business_systems(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """業務系統對照表 ＋ 對帳數字。

    只回「已匯入 N 筆」沒有用——人要知道的是**還有多少台查不到系統名稱**，
    以及那是「對照表缺代碼」還是「機器沒填 api_id」。兩者要補的地方不同。
    """
    import business_system

    return {"systems": business_system.list_all(conn),
            "coverage": business_system.coverage(conn)}


@router.get("/api/business-systems/class-count")
def business_system_class_count(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯入頁「系統類別對照表」那列的目前筆數：已分級幾個系統／對照表共幾個。
    2026-09-14 使用者：「明明寫成功 89 筆，但是目前筆數沒有」——那格原本寫死「—」。"""
    rated = conn.execute(
        "SELECT COUNT(*) FROM business_system WHERE sys_class IS NOT NULL "
        "AND length(trim(sys_class)) > 0").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM business_system").fetchone()[0]
    return {"rated": rated, "total": total}


@router.post("/api/business-systems/import-class")
def import_business_system_class(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯入系統類別（第一／二／三類）——**只更新類別，其他欄位不碰**。

    跟 /api/business-systems/import 分開：那支會整列覆寫，拿只有類別的檔案去匯，
    AP 部門與負責人會被清空。詳見 business_system.import_class_file。
    """
    import business_system

    fn = (file.filename or "").lower()
    if not fn.endswith((".xlsx", ".csv")):
        raise HTTPException(400, "只接受 .xlsx 或 .csv（第一列是表頭）")
    contents = file.file.read()
    suffix = ".csv" if fn.endswith(".csv") else ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)
    try:
        try:
            return business_system.import_class_file(tmp_path, conn)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/api/business-systems/import")
def import_business_systems(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯入 api_id → 系統名稱／AP 部門／AP 負責人 的對照表（.xlsx）。

    在這之前系統名稱是拿同一個 api_id 底下 MIN(asset_name) 湊的——
    「隨便挑一台機器的名字當系統名」，猜對是運氣。

    以 api_id 為鍵 upsert：對照表會重匯（改名、加新系統），重匯要更新同一筆
    而不是長出重複。欄名不寫死（決策 D14），可接受多種寫法。
    """
    import business_system

    fname = (file.filename or "").lower()
    ext = ".csv" if fname.endswith(".csv") else ".xlsx" if fname.endswith(".xlsx") else None
    if ext is None:
        raise HTTPException(400, "只接受 .xlsx 或 .csv（第一列是表頭）")
    contents = file.file.read()
    # 原始檔留一份（只留最新的），之後「匯出原始檔」才有東西可以給。
    # 存檔失敗不該讓匯入整個失敗——資料已經進去了，少一份存檔是小事。
    try:
        import import_export

        import_export.save_original("business_system", file.filename or f"upload{ext}", contents)
    except Exception as exc:  # noqa: BLE001
        print(f"[import] 原始檔保存失敗（business_system，不影響匯入）：{exc}")
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)
    try:
        try:
            return business_system.import_file(tmp_path, conn)
        except Exception as exc:  # noqa: BLE001 - 壞檔／欄位對不上統一如實回報
            raise HTTPException(400, f"匯入失敗：{exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


# ===== CI 圖譜（MICS 切片1：影響範圍查詢的地基）=====
# 薄 HTTP 層，邏輯都在 ci_graph.py。rebuild() 同步跑（資料量數百~數千節點，純 DB
# 運算不碰網路，不用像 scan_service 那樣起背景執行緒）——但仍照 scan_runs 的慣例留
# running 狀態擋同時觸發兩次，避免兩個請求並發時互相踩。
#
# run 記錄（開 running 列、成功寫 counts、失敗寫 error）在 ci_graph.run_rebuild()，
# 不寫在這裡：每天凌晨的排程要做完全一樣的事，兩邊各寫一份必然漂走。

@router.post("/api/ci/rebuild")
def ci_rebuild_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import ci_graph

    try:
        return ci_graph.run_rebuild(conn, "manual", session["username"])
    except ci_graph.RebuildInProgress as exc:
        raise HTTPException(409, "已有一次重建正在進行，請稍候") from exc
    except Exception as exc:  # noqa: BLE001 - 失敗已由 run_rebuild 記進 ci_graph_runs
        raise HTTPException(500, f"重建失敗：{exc}") from exc


@router.get("/api/ci/schedule")
def ci_schedule_get_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import ci_graph

    return ci_graph.get_schedule(conn)


@router.put("/api/ci/schedule")
def ci_schedule_put_endpoint(
    body: CiScheduleBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import ci_graph

    try:
        ci_graph.set_schedule(conn, body.enabled, body.time)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ci_graph.get_schedule(conn)


@router.get("/api/ci/rebuild/status")
def ci_rebuild_status_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    row = conn.execute("SELECT * FROM ci_graph_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


# ===== 影響範圍查詢（MICS 切片2：Blast Radius，殺手級功能）=====
# 薄 HTTP 層，邏輯都在 blast_radius.py。三種問法（陌生IP研判/事故爆炸半徑/計畫性停機）
# 共用同一顆引擎，只差 mode 參數，見 blast_radius.py 模組開頭說明。

@router.get("/api/blast/systems")
def blast_systems_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """瀏覽清單：全庫5148節點畫成一張圖是看不出結構的毛球，2026-08-19 使用者拍板
    用瀏覽清單當「不知道要查什麼名字」時的入口，不做全部關聯圖。"""
    import blast_radius
    return blast_radius.list_business_systems(conn)


@router.get("/api/blast/resolve")
def blast_resolve_endpoint(
    q: str, port: int | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import blast_radius
    return blast_radius.resolve(conn, q, port)


@router.get("/api/blast/impact")
def blast_impact_endpoint(
    node_id: str, depth: int = 6, mode: str = "incident", only_evidence: bool = False,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import blast_radius
    try:
        return blast_radius.impact(conn, node_id, depth=depth, mode=mode, only_evidence=only_evidence)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/blast/graph")
def blast_graph_endpoint(
    node_id: str, depth: int = 3, direction: str = "dependents",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import blast_radius
    return blast_radius.graph_elements(conn, node_id, depth, direction)


@router.post("/api/blast/snapshot")
def blast_snapshot_create_endpoint(
    body: BlastSnapshotBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import blast_radius
    try:
        return blast_radius.save_snapshot(
            conn, body.node_id, body.mode, body.reason, session["username"], depth=body.depth,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/blast/snapshots")
def blast_snapshot_list_endpoint(
    node_id: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import blast_radius
    return blast_radius.list_snapshots(conn, node_id)


@router.get("/api/blast/snapshot/{snapshot_id}")
def blast_snapshot_get_endpoint(
    snapshot_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import blast_radius
    snap = blast_radius.get_snapshot(conn, snapshot_id)
    if not snap:
        raise HTTPException(404, "查無此快照")
    return snap


@router.get("/api/blast/snapshot/{snapshot_id}/csv")
def blast_snapshot_csv_endpoint(
    snapshot_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import blast_radius
    snap = blast_radius.get_snapshot(conn, snapshot_id)
    if not snap:
        raise HTTPException(404, "查無此快照")

    content = _blast_result_to_csv(
        conn, snap["node_id"], snap["result"]["label"], snap["result"],
        header_rows=[
            ["快照ID", snap["id"]], ["原因", snap["reason"] or ""],
            ["查詢人", snap["asked_by"]], ["查詢時間", snap["asked_at"]],
        ],
    )
    from urllib.parse import quote
    fname = f"停機影響評估_{snap['id']}_{snap['asked_at'][:10]}.csv"
    return Response(
        content=content, media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/api/blast/impact/csv")
def blast_impact_csv_endpoint(
    node_id: str, depth: int = 6, mode: str = "incident", only_evidence: bool = False,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """即時查詢直接匯出 CSV，不用先存快照——事故當下要的是「馬上把清單發出去」，
    不是「先存證再匯出」（存證是 mode=planned 才需要的流程，事故爆炸半徑不用）。
    """
    import blast_radius
    try:
        result = blast_radius.impact(conn, node_id, depth=depth, mode=mode, only_evidence=only_evidence)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    content = _blast_result_to_csv(
        conn, node_id, result["label"], result,
        header_rows=[
            ["查詢時間", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            ["查詢人", session["username"]],
        ],
    )
    from urllib.parse import quote
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"影響範圍_{result['label']}_{stamp}.csv"
    return Response(
        content=content, media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.post("/api/blast/checklist")
def blast_checklist_create_endpoint(
    body: ChecklistCreateBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import blast_radius
    try:
        return blast_radius.create_checklist(conn, body.snapshot_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/blast/checklist/{snapshot_id}")
def blast_checklist_list_endpoint(
    snapshot_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import blast_radius
    return blast_radius.list_checklist(conn, snapshot_id)


@router.put("/api/blast/checklist/item/{item_id}")
def blast_checklist_item_update_endpoint(
    item_id: int, body: ChecklistItemUpdateBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import blast_radius
    try:
        return blast_radius.update_checklist_item(conn, item_id, body.status, body.note, session["username"])
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/topology")
def get_topology(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """M2 圖資料：一次回 systems + deps，前端餵 Cytoscape。全新建、人維護（戰情室重寫模型）。

    改為需登入（原本讀免登入）：這張圖含系統依賴與 SPOF 標記，等於直接告訴人「打哪一台
    全部會倒」，是最不該公開的資料；頁面本來就在登入牆後，公開讀沒有帶來任何好處。
    健康檢查請改用 /api/version（deploy.sh 用的就是它）。
    """
    import manage_state

    systems = [dict(r) for r in conn.execute("SELECT * FROM systems ORDER BY category, label").fetchall()]
    deps = [dict(r) for r in conn.execute("SELECT * FROM system_deps ORDER BY id").fetchall()]

    # M1↔M2 接起來：系統健康度由關聯主機的實際納管狀態推導，取代人手動標。
    # 沒有關聯主機的系統維持人工值，但明確標成 manual——把「某人半年前填的 ok」
    # 跟「剛剛確認過是 ok」混在一起，等於讓人相信一個沒有根據的綠燈。
    health = manage_state.system_health(conn)
    for s in systems:
        h = health.get(s["id"], {})
        s["health"] = h.get("health", s["health"])
        s["health_source"] = h.get("health_source", "manual")
        s["hosts"] = h.get("hosts", [])
    return {"systems": systems, "deps": deps}


@router.post("/api/systems")
def create_system(
    body: SystemBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if not _ID_RE.match(body.id):
        raise HTTPException(400, "系統代碼只能是英數字/底線/減號，1-32 字")
    if body.health not in _HEALTH:
        raise HTTPException(400, "健康度必須是 ok/warn/err")
    if conn.execute("SELECT 1 FROM systems WHERE id = ?", (body.id,)).fetchone():
        raise HTTPException(409, f"系統代碼「{body.id}」已存在")
    conn.execute(
        "INSERT INTO systems (id, label, category, domain, health, is_spof, note) VALUES (?,?,?,?,?,?,?)",
        (body.id, body.label, body.category, body.domain, body.health, int(body.is_spof), body.note),
    )
    conn.commit()
    return {"id": body.id}


@router.put("/api/systems/{system_id}")
def update_system(
    system_id: str,
    body: SystemUpdateBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if conn.execute("SELECT 1 FROM systems WHERE id = ?", (system_id,)).fetchone() is None:
        raise HTTPException(404, "查無此系統")
    fields = body.model_dump(exclude_none=True)
    if fields.get("health") and fields["health"] not in _HEALTH:
        raise HTTPException(400, "健康度必須是 ok/warn/err")
    if "is_spof" in fields:
        fields["is_spof"] = int(fields["is_spof"])
    if not fields:
        return {"id": system_id, "updated": 0}
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE systems SET {sets}, updated_at = datetime('now','localtime') WHERE id = ?",
        [*fields.values(), system_id],
    )
    conn.commit()
    return {"id": system_id, "updated": len(fields)}


@router.delete("/api/systems/{system_id}")
def delete_system(
    system_id: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if conn.execute("SELECT 1 FROM systems WHERE id = ?", (system_id,)).fetchone() is None:
        raise HTTPException(404, "查無此系統")
    # 連帶刪掉相關依賴（schema 有 ON DELETE CASCADE，但 SQLite 預設不強制外鍵，這裡明確刪）
    conn.execute("DELETE FROM system_deps WHERE source = ? OR target = ?", (system_id, system_id))
    conn.execute("DELETE FROM systems WHERE id = ?", (system_id,))
    conn.commit()
    return {"id": system_id, "deleted": True}


@router.post("/api/deps")
def create_dep(
    body: DepBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if body.source == body.target:
        raise HTTPException(400, "系統不能依賴自己")
    for sid in (body.source, body.target):
        if conn.execute("SELECT 1 FROM systems WHERE id = ?", (sid,)).fetchone() is None:
            raise HTTPException(400, f"系統「{sid}」不存在")
    if conn.execute(
        "SELECT 1 FROM system_deps WHERE source = ? AND target = ?", (body.source, body.target)
    ).fetchone():
        raise HTTPException(409, "這條依賴已存在")
    cur = conn.execute(
        "INSERT INTO system_deps (source, target, dep_type) VALUES (?,?,?)",
        (body.source, body.target, body.dep_type),
    )
    conn.commit()
    return {"id": cur.lastrowid}


@router.delete("/api/deps/{dep_id}")
def delete_dep(
    dep_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if conn.execute("SELECT 1 FROM system_deps WHERE id = ?", (dep_id,)).fetchone() is None:
        raise HTTPException(404, "查無此依賴")
    conn.execute("DELETE FROM system_deps WHERE id = ?", (dep_id,))
    conn.commit()
    return {"id": dep_id, "deleted": True}


__all__ = [
    'arch_vcenter',
    'arch_ocp',
    'arch_ocp_siblings',
    'set_ocp_cluster',
    'set_ocp_role',
    'arch_ftp',
    'arch_vm_placement',
    'segments_list',
    'segments_tree',
    'segments_scan_candidates',
    'segments_ips',
    'segments_export_template',
    'segments_export',
    'segments_import',
    'set_system_class_endpoint',
    'list_business_systems',
    'business_system_class_count',
    'import_business_system_class',
    'import_business_systems',
    'ci_rebuild_endpoint',
    'ci_schedule_get_endpoint',
    'ci_schedule_put_endpoint',
    'ci_rebuild_status_endpoint',
    'blast_systems_endpoint',
    'blast_resolve_endpoint',
    'blast_impact_endpoint',
    'blast_graph_endpoint',
    'blast_snapshot_create_endpoint',
    'blast_snapshot_list_endpoint',
    'blast_snapshot_get_endpoint',
    'blast_snapshot_csv_endpoint',
    'blast_impact_csv_endpoint',
    'blast_checklist_create_endpoint',
    'blast_checklist_list_endpoint',
    'blast_checklist_item_update_endpoint',
    'get_topology',
    'create_system',
    'update_system',
    'delete_system',
    'create_dep',
    'delete_dep',
]
