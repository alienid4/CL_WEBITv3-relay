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


@router.get("/api/blast/system-map")
def blast_system_map_endpoint(
    api_id: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """以一個業務系統（APID，也接受 bizsys:<APID>）為中心的一層地圖：主機→ESXi→Cluster／
    Datastore，加上 system_deps 的上下游。4-1 系統聯通圖與 4-2 影響範圍查詢共用。"""
    import blast_radius
    try:
        return blast_radius.system_map(conn, api_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/blast/host-systems")
def blast_host_systems_endpoint(
    q: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """系統地圖的挑選框打主機名／IP：回符合的主機（逐台）與它屬於的業務系統。"""
    import blast_radius
    return blast_radius.host_systems(conn, q)


@router.get("/api/storage/overview")
def storage_overview_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """儲存容量頁：只用已匯入的 RVTools 分頁與已收集主機的磁碟資料（storage_capacity）。"""
    import storage_capacity
    return storage_capacity.overview(conn)


class ModelSpecBody(BaseModel):
    model: str
    values: dict = {}
    clear: list[str] = []


class RackStateBody(BaseModel):
    key: str
    state: str | None = None      # active／removed；None＝清除人工確認
    note: str | None = None


@router.get("/api/model-specs")
def model_specs_list_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """型號規格表（種子＋人工改值）與機櫃設備的型號對應情形。"""
    import model_specs
    return {"specs": model_specs.all_specs_readonly(conn), "coverage": model_specs.coverage(conn),
            "can_edit": _can_edit_nav(session, conn)}


@router.put("/api/model-specs")
def model_specs_update_endpoint(
    body: ModelSpecBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """人工改一個型號的規格（存 manual，重新灌種子不會蓋掉）；限管理員。"""
    import model_specs
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以改型號規格")
    try:
        return model_specs.update(conn, body.model, body.values, body.clear,
                                  session["username"] if session is not None else "system")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class RackStateBulkBody(BaseModel):
    keys: list[str]
    state: str | None = None      # off／removed／active；None＝清除標記
    note: str | None = None


@router.put("/api/racks/state/bulk")
def racks_state_bulk_endpoint(
    body: RackStateBulkBody,
    request: Request,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """套用現場盤點結果到多台（限管理員；每台寫一筆稽核紀錄）。"""
    import rack_layout
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以批次標記")
    import write_guard
    try:
        write_guard.check(conn, request)
    except write_guard.TooManyFails as exc:
        raise HTTPException(429, str(exc))
    except write_guard.NeedPassword as exc:
        raise HTTPException(428, str(exc))
    try:
        return rack_layout.set_state_bulk(conn, body.keys, body.state, body.note, write_guard.actor(session, request))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/racks/state-audit")
def racks_state_audit_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """機櫃人工標記的稽核紀錄（最近 200 筆）。"""
    import rack_layout
    return rack_layout.state_audit(conn)


@router.put("/api/racks/state")
def racks_state_endpoint(
    body: RackStateBody,
    request: Request,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """機櫃上「待確認」的設備人工確認：在用（active）或已不在櫃上（removed）；state 空＝清除。"""
    import rack_layout
    import write_guard
    try:
        write_guard.check(conn, request)
    except write_guard.TooManyFails as exc:
        raise HTTPException(429, str(exc))
    except write_guard.NeedPassword as exc:
        raise HTTPException(428, str(exc))
    try:
        return rack_layout.set_state_override(conn, body.key, body.state, body.note, write_guard.actor(session, request))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/storage/rooms")
def storage_rooms_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """各機房的 datastore 容量／已用（機房架構圖那一行用；跟儲存容量頁「依機房」同一份計算）。"""
    import storage_capacity
    return storage_capacity.room_summary(conn)


class RackPositionBody(BaseModel):
    key: str
    room: str = ""
    rack: str = ""
    u_start: int | None = None
    u_height: int | None = None
    note: str = ""
    clear: bool = False


class RackPositionBulkBody(BaseModel):
    items: list[dict]
    note: str = ""


def _who(session) -> str:
    return session["username"] if session is not None else "system"


@router.put("/api/racks/position")
def racks_position_endpoint(
    body: RackPositionBody,
    request: Request,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """位置更正（現場盤點）：機房／機櫃／起始 U／U 數＋必填備註；限管理員。回同櫃 U 位重疊的設備（只警告）。
    clear=true 清除更正（回到 CIA 的值）。每次都留稽核紀錄；CIA 匯入不會蓋掉。"""
    import rack_layout
    import write_guard
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以更正位置")
    try:
        write_guard.check(conn, request)
    except write_guard.TooManyFails as exc:
        raise HTTPException(429, str(exc))
    except write_guard.NeedPassword as exc:
        raise HTTPException(428, str(exc))
    who = write_guard.actor(session, request)
    try:
        if body.clear:
            return rack_layout.clear_position(conn, body.key, body.note or None, who)
        return rack_layout.set_position(conn, body.key, body.room, body.rack, body.u_start, body.u_height,
                                        body.note, who)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/api/racks/position/bulk")
def racks_position_bulk_endpoint(
    body: RackPositionBulkBody,
    request: Request,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """依盤點結果一次更正一櫃的多台（同一份備註）；限管理員。"""
    import rack_layout
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以更正位置")
    import write_guard
    try:
        write_guard.check(conn, request)
    except write_guard.TooManyFails as exc:
        raise HTTPException(429, str(exc))
    except write_guard.NeedPassword as exc:
        raise HTTPException(428, str(exc))
    try:
        return rack_layout.set_positions_bulk(conn, body.items, body.note, write_guard.actor(session, request))
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/racks/position/audit")
def racks_position_audit_endpoint(
    key: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import rack_layout
    return rack_layout.position_audit(conn, key)


@router.get("/api/racks/position/pending-cia")
def racks_position_pending_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """CIA 待更正清單（畫面用；匯出 xlsx 用 /api/racks/position/pending-cia.xlsx）。"""
    import rack_layout
    return {"rows": rack_layout.pending_cia_rows(conn), "columns": rack_layout.PENDING_CIA_COLS}


@router.get("/api/racks/position/pending-cia.xlsx")
def racks_position_pending_xlsx_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """CIA 待更正（xlsx）：還沒同步回 CIA 的位置更正，給 CIA 一次批次修正。欄名沿用機房搬遷盤點表。"""
    import rack_layout
    from urllib.parse import quote

    buf, n = rack_layout.pending_cia_xlsx(conn)
    fname = f"CIA待更正_位置_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}", "X-Row-Count": str(n)})


class ReservationBody(BaseModel):
    room: str
    rack: str
    u_start: int
    u_height: int = 1
    label: str
    expires_at: str
    model: str | None = None
    w: float | None = None
    kg: float | None = None
    note: str | None = None
    confirm_verified: bool = False


@router.get("/api/racks/placement")
def racks_placement_endpoint(
    u: int = 1, qty: int = 1, w: float | None = None, kg: float | None = None, rooms: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """上架助手：放得下 qty 台（每台 u 個 U、w 瓦、kg 公斤）的機櫃建議與原因。rooms＝逗號分隔的機房。"""
    import rack_layout
    try:
        return rack_layout.placement(conn, u, qty, w, kg, [x.strip() for x in (rooms or "").split(",") if x.strip()] or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/racks/reservations")
def racks_reservations_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """預留中（含已過期但還沒取消的，標 expired）。"""
    import rack_layout
    return rack_layout.reservations(conn)


@router.post("/api/racks/reservations")
def racks_reservation_create_endpoint(
    body: ReservationBody,
    request: Request,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """預留一段 U 給還沒登記的設備（限管理員）；跟現有設備或其他預留重疊只警告。"""
    import rack_layout
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以預留")
    import write_guard
    try:
        write_guard.check(conn, request)
    except write_guard.TooManyFails as exc:
        raise HTTPException(429, str(exc))
    except write_guard.NeedPassword as exc:
        raise HTTPException(428, str(exc))
    try:
        return rack_layout.reserve(conn, body.room, body.rack, body.u_start, body.u_height, body.label, body.expires_at,
                                   write_guard.actor(session, request), body.model, body.w, body.kg, body.note,
                                   bool(body.confirm_verified))
    except rack_layout.NeedVerification as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/api/racks/reservations/{rid}/cancel")
def racks_reservation_cancel_endpoint(
    rid: int,
    request: Request,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import rack_layout
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以取消預留")
    import write_guard
    try:
        write_guard.check(conn, request)
    except write_guard.TooManyFails as exc:
        raise HTTPException(429, str(exc))
    except write_guard.NeedPassword as exc:
        raise HTTPException(428, str(exc))
    try:
        return rack_layout.close_reservation(conn, rid, write_guard.actor(session, request))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/racks/find-asset")
def racks_find_asset_endpoint(
    q: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """上架助手「登記位置」：用資產序號／主機名找既有設備。"""
    import rack_layout
    return rack_layout.find_assets(conn, q)


@router.get("/api/racks/datacenter")
def racks_datacenter_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """4-5 機房架構圖：各機房的機櫃磚、無機櫃資料、排除的 VM 數（rack_layout）。"""
    import rack_layout
    return {**rack_layout.datacenter(conn), "can_edit": _can_edit_nav(session, conn)}


@router.get("/api/racks/detail")
def racks_detail_endpoint(
    room: str, rack: str, api_id: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """單一機櫃立面（也是機櫃反查：這櫃有哪些系統）；api_id 的設備標成「本系統」。"""
    import rack_layout
    try:
        return {**rack_layout.rack_detail(conn, room, rack, highlight_api=api_id),
                "can_edit": _can_edit_nav(session, conn)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/racks/print-sheet")
def racks_print_sheet_endpoint(
    room: str | None = None, rack: str | None = None, api_id: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """A4 盤點表（一櫃一頁）：一櫃、一個機房的每一櫃、或一個系統碰到的每一櫃。"""
    import rack_layout
    try:
        return rack_layout.print_sheets(conn, room=room, rack=rack, api_id=api_id)
    except ValueError as exc:
        raise HTTPException(400 if "要指定" in str(exc) else 404, str(exc)) from exc


@router.get("/api/racks/labels")
def racks_labels_endpoint(
    room: str | None = None, rack: str | None = None, api_id: str | None = None,
    q: str | None = None, angle: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """標籤貼紙：要印的實體設備＋標籤紙規格（app_settings label_presets，預設 A4 3×8 70×37mm）。"""
    import rack_layout
    try:
        return rack_layout.label_items(conn, room=room, rack=rack, api_id=api_id, q=q, angle=angle)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/racks/search")
def racks_search_endpoint(
    q: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """機型／主機名／資產名稱／IP → 實體設備在哪個機房／機櫃／U。"""
    import rack_layout
    return rack_layout.search(conn, q)
@router.get("/api/blast/observed-edge")
def blast_observed_edge_endpoint(
    caller: str, callee: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """一條「連線推論」上下游邊的完整證據（caller 依賴 callee，APID）：哪幾台、哪個埠、幾條、
    最後看到。系統地圖上的證據數字點下去就是這份（同一份計算，observed_deps.infer）。"""
    import observed_deps
    caller, callee = caller.strip(), callee.strip()
    if not caller or not callee:
        raise HTTPException(400, "缺少 caller／callee")
    rows = observed_deps.edge_rows(conn, caller, callee)
    return {"caller": caller, "callee": callee, "count": len(rows), "rows": rows,
            "stale_days": observed_deps.stale_days(conn)}


@router.get("/api/blast/observed-rows")
def blast_observed_rows_endpoint(
    bucket: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """連線推論沒造邊的那幾類（excluded／ambiguous／admin_ssh_auto）的原始連線列：筆數點下去看得到是哪幾列。"""
    import observed_deps
    try:
        return observed_deps.bucket_rows(conn, bucket)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/blast/observed-coverage")
def blast_observed_coverage_endpoint(
    days: int | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """連線推論的收集涵蓋率：現役機器（machine_key）最近 N 天有連線觀察的比例，依 OS 大類分，
    加上累積天數、有沒有跨過月底。每格帶下鑽清單（observed／missing）。"""
    import observed_deps
    if days is not None and days <= 0:
        raise HTTPException(400, "days 要大於 0")
    return observed_deps.coverage(conn, days)


class ObservedCollectBody(BaseModel):
    enabled: bool | None = None
    retention_days: int | None = None


@router.put("/api/blast/observed-collect")
def blast_observed_collect_put(
    body: ObservedCollectBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """連線觀察的收集開關與保留天數（下一輪服務收集生效）。（操作紀錄由全站 PUT 中介層記。）"""
    import observed_deps
    from db import set_setting

    if body.retention_days is not None and not 1 <= body.retention_days <= 3650:
        raise HTTPException(400, "保留天數要在 1 到 3650 之間")
    if body.enabled is not None:
        set_setting(conn, observed_deps.COLLECT_KEY, "1" if body.enabled else "0")
    if body.retention_days is not None:
        set_setting(conn, observed_deps.RETENTION_KEY, str(body.retention_days))
    conn.commit()
    return {"enabled": observed_deps.collect_enabled(conn), "retention_days": observed_deps.retention_days(conn)}


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
