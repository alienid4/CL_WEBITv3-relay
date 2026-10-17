from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()



@router.get("/api/dashboard/stats")
def dashboard_stats(
    environment: str = "正式",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """D25：環境篩選預設「正式」。重疊圖三區塊：僅CIA登記／重疊／僅掃描到（=漏登記候選）。"""
    if environment not in ENV_FILTER_PRESETS:
        raise HTTPException(400, f"不支援的環境篩選：{environment}")
    env_list = ENV_FILTER_PRESETS[environment]

    # 選環境時的「登記」範圍，要跟下面 total_* 同一套：**排除退役＋帳外**。
    # 2026-09-18 十項盤點第 4 條：以前這裡撈整張表，221 上「正式環境一致 1」＞「全部一致 0」
    # ——子集比全體大。那 1 台是 221 自己的 AUTO- 帳外登記：選環境時算進去、總數沒算。
    # v1.208 只把 total_* 改成排退役＋帳外，這半邊漏了。
    import manage_state as _ms0
    import system_report as _sr0
    if env_list is None:
        _env_rows = conn.execute(
            "SELECT ip, hostname, asset_serial, asset_status FROM hardware").fetchall()
    else:
        placeholders = ",".join("?" for _ in env_list)
        _env_rows = conn.execute(
            f"SELECT ip, hostname, asset_serial, asset_status FROM hardware WHERE environment IN ({placeholders})",
            env_list,
        ).fetchall()
    ica_rows = [r for r in _env_rows
                if (r["asset_status"] or "").strip() not in _ms0.RETIRED_STATUS
                and not str(r["asset_serial"] or "").startswith(_sr0.OFF_BOOK_PREFIXES)]
    ica_ips = {r["ip"] for r in ica_rows if r["ip"]}
    ica_hostnames = {r["hostname"] for r in ica_rows if r["hostname"]}

    last_scan_time = _latest_scan_time(conn)
    scanned_rows = _scanned_alive_rows(conn, last_scan_time)
    failed_segments: list[str] = []
    if last_scan_time:
        failed_segments = [
            r["segment"]
            for r in conn.execute(
                "SELECT DISTINCT segment FROM scan_history WHERE scan_time = ? AND scan_ok = 0",
                (last_scan_time,),
            ).fetchall()
        ]

    scan_ips, scan_hostnames = _scan_keys(scanned_rows)

    # 「相符」從**資產側**數：有幾台登記的資產這次掃得到。
    # 原本是從掃描側數（幾筆掃描結果對得上 CIA），再用 ica_count - overlap 反推「登記卻掃不到」——
    # 一旦一台資產被多筆掃描結果對到（同機多 IP／IP 與主機名分別命中），那個減法就會少算甚至變負數。
    # 現在每個數字都可以點進去看清單，磚塊上的數字必須跟清單筆數一致，所以改成各自獨立算：
    #   overlap   = 登記且掃得到（資產側）      -> /assets?scan_status=overlap
    #   ica_only  = 登記但掃不到（資產側）      -> /assets?scan_status=ica_only
    #   scan_only = 掃到但沒登記（掃描側）      -> /adopt（與 /api/scan/unregistered 同一套判定）
    overlap_count = sum(1 for r in ica_rows if _row_in_keys(r, scan_ips, scan_hostnames))
    ica_only_count = len(ica_rows) - overlap_count

    # ⚠️ scan_only 要對「全部」資產比，不能只對環境篩選後的子集：
    # 「掃到卻沒登記」的意思是「這台機器在網路上，但整個資產清單裡都沒有它」。
    # 一台登記為「測試」環境的機器是有登記的，只是不屬於目前檢視的環境——
    # 拿環境子集去比，會把它算成未登記，磚塊顯示 4 但點進去的 /adopt（不分環境）只有 3。
    # 數字對不上的下鑽比不能點更糟，實測踩到過。
    # ⚠️「未登記」偵測要對「全部」登記（含退役／帳外）比：一個掃到的 IP 只要在 hardware
    # 裡出現過（哪怕是退役或帳外那筆），就不算「資產庫查無此機」。所以這裡故意用廣義集。
    all_ica_rows = conn.execute("SELECT ip, hostname FROM hardware").fetchall()
    all_ica_ips = {r["ip"] for r in all_ica_rows if r["ip"]}
    all_ica_hostnames = {r["hostname"] for r in all_ica_rows if r["hostname"]}
    scan_only_count = sum(
        1 for r in scanned_rows if not _row_in_keys(r, all_ica_ips, all_ica_hostnames)
    )

    # 對帳「一致／搜不到」的「登記」universe 必須跟頭條「在管」同一套口徑：
    # **排除退役（停用/報廢/閒置）＋帳外（DYN-/VC-/AUTO-）**。否則「搜不到＝登記總數−一致」
    # 會把退役機（本來就不該回應）和帳外算進去，導致「搜不到」比「在管」還大——
    # 2026-09-17 使用者在公司機看到「搜不到 7823 > 在管 3768」，就是這個 bug。
    # 這樣 一致 + 搜不到 = 在管（registered_total），加總對得起頭條。
    import manage_state as _ms
    import system_report as _sr
    reg_rows = [
        r for r in conn.execute(
            "SELECT ip, hostname, asset_serial, asset_status FROM hardware").fetchall()
        if (r["asset_status"] or "").strip() not in _ms.RETIRED_STATUS
        and not str(r["asset_serial"] or "").startswith(_sr.OFF_BOOK_PREFIXES)
    ]
    total_ica_registered = len(reg_rows)
    total_overlap_count = sum(
        1 for r in reg_rows if _row_in_keys(r, scan_ips, scan_hostnames)
    )
    # [口徑] 2026-09-20 公司驗收：畫面寫「台」、這裡卻是逐筆，於是出現「搜不到 3,417 > 在管 3,374 台」。
    # 一致／搜不到改逐台（正典 machine_key，同一台只要有一筆掃得到就算掃得到），
    # 筆數仍一併回傳給畫面當副標——兩個都給，人才知道自己在看哪一種。
    import system_stats as _ss

    _machines: dict[str, bool] = {}
    for r in reg_rows:
        k = _ss.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        _machines[k] = _machines.get(k, False) or _row_in_keys(r, scan_ips, scan_hostnames)
    total_ica_machines = len(_machines)
    total_overlap_machines = sum(1 for v in _machines.values() if v)

    issue_counts = {"異常新增": 0, "異常消失": 0, "漏登記": 0}
    for row in conn.execute(
        "SELECT issue_type, COUNT(*) AS c FROM comparison_result WHERE is_read = 0 GROUP BY issue_type"
    ).fetchall():
        issue_counts[row["issue_type"]] = row["c"]

    return {
        "environment": environment,
        "ica_count": len(ica_rows),
        "scanned_count": len(scanned_rows),
        "overlap_count": overlap_count,
        "ica_only_count": ica_only_count,
        "scan_only_count": scan_only_count,
        # 一致率／對帳用這兩個（全站、不隨環境變動、排退役＋帳外＝跟頭條「在管」同口徑），
        # 別拿上面那組環境篩選過的去算
        "total_ica_count": total_ica_registered,          # 筆（副標用）
        "total_overlap_count": total_overlap_count,        # 筆
        # 檯面數字用這兩個：逐台，跟頭條「在管 N 台」同一把尺
        "total_ica_machines": total_ica_machines,
        "total_overlap_machines": total_overlap_machines,
        "last_scan_time": last_scan_time,
        "last_scan_ok": len(failed_segments) == 0,
        "failed_segments": failed_segments,
        "issue_counts": issue_counts,
    }


@router.get("/api/reports/scan-gaps")
def scan_gaps_endpoint(
    only_in_scope: bool = True,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """掃不到的網段清單：分「從沒掃過／掃描失敗／掃通了整段沒回應／最近一次沒掃到」，附那段登記幾台。"""
    import scan_gaps

    return scan_gaps.report(conn, only_in_scope=only_in_scope)


@router.put("/api/reports/scan-gaps/visibility")
def scan_gaps_visibility_endpoint(
    body: ScannerVisibilityBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """這台掃描機看得到哪些環境。設了之後，看不到的環境掃不到＝預期中的事，不列進申請名單。"""
    import scan_gaps

    return {"environments": scan_gaps.set_visible_environments(conn, body.environments)}


@router.put("/api/reports/scan-gaps/note")
def scan_gaps_note_endpoint(
    body: SegmentNoteBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """一段的人工標註：目前沒在使用／政策不掃／等開通／盯著＋預計台數＋備註。

    標註只補人的判斷，不覆蓋掃描事實；標了「沒在使用／政策不掃」的預設就不顯示了。
    """
    import scan_gaps

    try:
        return scan_gaps.set_note(conn, body.cidr, body.status, body.expected_hosts, body.note,
                                  session["username"] if session else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/reports/scan-gaps/export")
def scan_gaps_export_endpoint(
    only_in_scope: bool = True,
    q: str = "",
    environment: str = "",
    location: str = "",
    kind: str = "",
    only_registered: bool = False,
    include_expected: bool = True,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """同一份資料匯成 CSV（拿去當防火牆申請的附件）。

    帶畫面上的搜尋／篩選條件——匯出要等於畫面看到的那幾列（所見即所得），
    不然人篩完再匯出，拿到的卻是全部，送出去就錯了。
    """
    import scan_gaps

    data = scan_gaps.report(conn, only_in_scope=only_in_scope)
    data = {**data, "rows": scan_gaps.filter_rows(
        data["rows"], q=q, environment=environment, location=location, kind=kind,
        only_registered=only_registered, include_expected=include_expected)}
    csv = scan_gaps.to_csv(data)
    return Response(
        content="﻿" + csv,          # Excel 沒有 BOM 會把中文顯示成亂碼
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="scan_gaps.csv"'},
    )


@router.get("/api/dashboard/composition")
def dashboard_composition(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """全站組成統計：我的機器長什麼樣子（各平台幾台、虛實、環境、狀態）。

    這是儀表板該回答的問題——「有幾台 Windows」是統計；
    「兩邊相符／登記卻掃不到」是對帳細節，屬於小功能，不該佔戰情室頭條。
    """
    import manage_state

    return manage_state.composition(conn)


@router.get("/api/stats/systems")
def stats_systems(
    limit: int = 10,
    kind: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """業務系統 × 環境。

    limit<=0 代表全部（畫面上的「更多」）。
    kind：`infra` 只回基礎設施、`ap` 只回業務系統、不帶就全部
    （使用者 2026-09-10：「基礎跟 AP 系統要分開」）。
    """
    import system_stats

    if kind is not None and kind not in (system_stats.KIND_INFRA, system_stats.KIND_AP):
        raise HTTPException(400, f"kind 只能是 {system_stats.KIND_INFRA} 或 {system_stats.KIND_AP}")
    out = system_stats.by_system(conn, limit=None if limit <= 0 else limit, kind=kind)
    # 人工搬過的系統要能被畫面說明——不然使用者看到「虛擬化平台」在基礎設施那頁，
    # 下次有人問「這不是 N- 開頭嗎」就沒人答得出來
    out["kind_overrides"] = system_stats.kind_overrides()
    return out


@router.get("/api/stats/systems/{api_id}")
def stats_system_drilldown(
    api_id: str,
    env: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """一個系統的下鑽：機房 × 環境，加上 AP／DB 推論。

    帶 `env` 就只算那個環境——對應畫面上點某一格數字的行為
    （使用者 2026-09-10：「譬如我點 207，這 207 機房各占多少」）。
    傳合併後的環境名（測試已含 UAT／DEV）。
    """
    import system_stats

    try:
        return system_stats.drilldown(conn, api_id, env=env)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/stats/systems/{api_id}/assets")
def stats_cell_assets(
    api_id: str,
    env: str | None = None,
    location: str | None = None,
    role: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """交叉表某一格是哪幾台（使用者 2026-09-10：「板橋機房 DB 有 18 台，
    點進去要知道是哪 18 台」）。

    不能直接連資產查詢頁——**角色不是資料庫欄位**，是算出來的，
    那頁沒辦法用它篩選，硬連過去會篩出不同的一批，數字對不起來。
    """
    import system_stats

    items = system_stats.assets_in_cell(conn, api_id, env=env, location=location, role=role)
    return {"api_id": api_id, "env": env, "location": location, "role": role,
            "count": len(items), "items": items}


@router.get("/api/stats/lookup")
def stats_lookup(
    q: str,
    limit: int = 20,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """輸入主機名／IP／資產編號，回答「它屬於哪個系統」。

    下鑽的反方向（使用者 2026-09-10：「SEC01 是哪個 APID，我怎麼找不到」）。
    """
    import system_stats

    items = system_stats.lookup_host(conn, q, limit=limit)
    return {"query": q, "count": len(items), "items": items}


@router.get("/api/stats/vip-entries")
def stats_vip_entries(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """入口（VIP）清單：每個 VIP 後面幾台、影響幾個系統。
    來源是 CIA 登記的 VIP 欄，**未經 F5 驗證**（VIP 會浮動）。見 vip_view。"""
    import vip_view

    return vip_view.entries(conn)


@router.get("/api/stats/vip-entries/detail")
def stats_vip_entry_detail(
    value: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """一個入口後面是哪幾台、各掛哪些系統。"""
    import vip_view

    try:
        return vip_view.entry_detail(conn, value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/stats/role-coverage")
def stats_role_coverage(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """AP／DB 推論的整體涵蓋率——**這個數字要放在畫面上**。

    使用者要看的是「這個分類可不可信」，不是一張看起來分好了的圖。
    """
    import system_stats

    return system_stats.role_coverage(conn)


# ===== 系統組月報（2026-08-21）=====
# 薄 HTTP 層，邏輯都在 system_report.py。使用者每月要把三張表貼進部門報告，
# 原本是人工統計的——他的原話：「以後我就 COPY 畫面，不用再自己統計」。

@router.get("/api/reports/system-group")
def report_system_group(
    period: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """當下即時算的整份報告。帶 period 只影響備註的取用範圍，數字一律是即時的
    ——要看某個月的定稿數字請走 /snapshots/{period}，兩者刻意分開，
    免得有人以為畫面上的數字就是當月存檔的那份。"""
    import system_report

    return system_report.build(conn, period)


@router.put("/api/reports/system-group/note")
def report_system_group_note(
    body: ReportNoteBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import system_report

    system_report.set_note(conn, body.row_key, body.note, session["username"], body.period)
    return {"ok": True}


@router.get("/api/reports/system-group/drill")
def report_drill(
    table: str,
    platform: str | None = None,
    status: str | None = None,
    bucket: str | None = None,
    retired: bool = False,
    location: str | None = None,
    service: str | None = None,
    vcenter: str | None = None,
    cluster: str | None = None,
    version: str | None = None,
    os_canonical: str | None = None,
    registered_only: bool = False,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """任何一格數字點下去要看的清單。

    使用者 2026-08-21：「你每個數字我都要可以追」。這支跟加總走**同一份**計算
    （system_report 的 classify_assets / _vhosts），所以格子上的數字跟這裡回的
    筆數必然一致——不一致的話那張報表就不能用了。

    每一列都帶 `reason`：不只列得出是哪幾台，還講得出為什麼是這一台。
    """
    import system_report

    if table == "platform":
        return system_report.drill_platform(
            conn, platform=platform, status=status, bucket=bucket, retired=retired,
            os_canonical=os_canonical, registered_only=registered_only)
    if table in ("cluster", "virtualization"):
        return system_report.drill_cluster(
            conn, location=location, service=service, vcenter=vcenter, cluster=cluster,
            version=version)
    raise HTTPException(400, "table 只接受 platform / cluster / virtualization")


@router.get("/api/reports/system-group/snapshots")
def report_snapshots(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import system_report

    return system_report.list_snapshots(conn)


@router.post("/api/reports/system-group/snapshots")
def report_snapshot_save(
    body: ReportSnapshotBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """存當月定稿。同月重存＝覆蓋（同月本來就只該有一份定稿）。"""
    import re as _re

    import system_report

    if not _re.fullmatch(r"\d{4}-\d{2}", body.period or ""):
        raise HTTPException(400, "period 格式須為 YYYY-MM")
    sid = system_report.save_snapshot(conn, body.period, session["username"])
    return {"id": sid, "period": body.period}


@router.get("/api/reports/system-group/snapshots/{period}")
def report_snapshot_get(
    period: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import system_report

    snap = system_report.get_snapshot(conn, period)
    if snap is None:
        raise HTTPException(404, f"沒有 {period} 的存檔")
    return snap


# ===== 部門報告圖表頁：頁A（各環境實體機分布）／頁B（主機系統總覽）=====
# 薄 HTTP 層，邏輯都在 system_report.py。跟系統組月報同一套規矩：加總與下鑽
# 走同一份計算，格子上的數字要能追（2026-08-25 使用者提供簡報頁後定案的計畫）。

@router.get("/api/reports/physical-distribution")
def report_physical_distribution(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    import system_report

    return system_report.physical_distribution(conn)


@router.get("/api/reports/physical-distribution/drill")
def report_physical_distribution_drill(
    room: str | None = None,
    branch: str | None = None,
    excluded_model: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import system_report

    return system_report.drill_physical(
        conn, room=room, branch=branch, excluded_model=excluded_model)


@router.get("/api/reports/system-overview")
def report_system_overview(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    import system_report

    return system_report.system_overview(conn)


@router.get("/api/reports/system-overview/drill")
def report_system_overview_drill(
    bucket: str | None = None,
    api_id: str | None = None,
    room: str | None = None,
    category: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import system_report

    if bucket is not None and bucket not in ("core", "noncore", "test", "uncategorized"):
        raise HTTPException(400, "bucket 只接受 core／noncore／test／uncategorized")
    return system_report.drill_system_overview(
        conn, bucket=bucket, api_id=api_id, room=room, category=category)


@router.get("/api/reports/system-category/template")
def report_system_category_template(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """177 個業務系統的空白對照表，xlsx 下載——依台數大到小排序，
    使用者填前幾十個就涵蓋大部分機器，不必一次填完（計畫檔已定案）。

    表頭刻意跟匯入端點認的欄位同名（api_id/分類），保證原封不動填完再上傳就對得起來
    （跟 /api/export 保證 round-trip 的道理一樣）。
    """
    import system_report

    rows = system_report.system_category_template(conn)
    cat_defs = system_report._cfg().get("system_categories") or []
    wb = Workbook()
    ws = wb.active
    ws.title = "業務系統分類"
    ws.append(["api_id", "系統名稱", "目前台數", "分類（填法見『分類代碼表』分頁，共19項）"])
    for r in rows:
        ws.append([r["api_id"], r["name"], r["count"], r["category"]])

    ws2 = wb.create_sheet("分類代碼表")
    ws2.append(["分類名稱", "屬於"])
    for c in cat_defs:
        ws2.append([c["name"], c.get("group", "")])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from urllib.parse import quote

    fname = f"業務系統分類範本_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        # 中文檔名不能直接塞進 Content-Disposition（HTTP header 只能是 latin-1），
        # 要用 RFC 5987 的 filename* 編碼——同 /api/export 等既有端點的做法。
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.post("/api/reports/system-category/import")
def report_system_category_import(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """讀回填好的對照表。跟 /api/import/excel 同一個理由用 sync def（UploadFile.file
    是同步可讀的 SpooledTemporaryFile）。

    只認第一欄 api_id、第四欄分類（表頭文字不驗，位置驗——使用者可能把說明文字
    改掉，但欄位順序是匯出範本固定的，改順序才會出錯，那本來就該報錯）。
    """
    import system_report

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 檔案（用上面『下載範本』那份填）")

    try:
        wb = load_workbook(io.BytesIO(file.file.read()), data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl對壞檔案的錯誤型態不一，統一攔截如實回報
        raise HTTPException(400, f"檔案讀不開：{exc}") from exc

    ws = wb.active
    mapping: dict[str, str] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        api_id = str(row[0]).strip()
        cat = str(row[3]).strip() if len(row) > 3 and row[3] else ""
        mapping[api_id] = cat

    result = system_report.import_system_category(conn, mapping)
    return result


# ===== 掃描範圍（以網段配置表為準）=====
# 使用者 2026-09-16 問「你要怎麼知道要掃描那些網段」。答案是網段配置表，
# 但它一直沒接上掃描排程（connections 是空的）——這是失聯 8062 台的真因。
# 使用者決定：「全部，但可以由我來選」→ 這裡回全部網段並標明資安建議，不替他過濾。


@router.get("/api/stats/onboard-matrix")
def onboard_matrix_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """OS × 環境 的納管狀態交叉表（2026-09-16 使用者：「OS 為列、環境為欄，格子放納管」）。

    吃的是漏斗那份**已經照主機算**的資料，數字才不會跟漏斗頁對不起來。見 onboard_matrix。
    """
    import onboard_matrix

    m = onboard_matrix.build(conn)
    # 比率在後端算，前端不要自己再算一次——分母要不要含退役／豁免是規則，不是版面
    for os_type in m["rows"]:
        for col in m["cols"]:
            c = m["cells"][os_type][col["key"]]
            c["coverage"] = onboard_matrix.coverage(c)
        m["row_totals"][os_type]["coverage"] = onboard_matrix.coverage(m["row_totals"][os_type])
    for col in m["cols"]:
        m["col_totals"][col["key"]]["coverage"] = onboard_matrix.coverage(m["col_totals"][col["key"]])
    m["grand"]["coverage"] = onboard_matrix.coverage(m["grand"])
    return m


@router.get("/api/stats/onboard-matrix/export")
def onboard_matrix_export(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """交叉表匯出 Excel。"""
    import onboard_matrix
    from urllib.parse import quote

    wb = Workbook()
    ws = wb.active
    ws.title = "OS×環境納管狀態"
    ws.append(onboard_matrix.EXPORT_HEADERS)
    for r in onboard_matrix.export_rows(conn):
        ws.append(["" if v is None else v for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"OS環境納管狀態_{_now_local()[:10]}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


@router.get("/api/export")
def export_assets_endpoint(
    include_off_book: bool = False,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """匯出資產（硬體/人員/軟體三分頁 xlsx）——對應資料匯入，有進就有出。

    表頭優先用「匯入對應表(field_mapping.json)的標題」，保證匯出的檔案能原封不動再匯入
    （round-trip）；欄位依 field_meta 分類排序，讓同一類欄位在表上相鄰、好填。

    2026-08-25 使用者：「要交出新的 CIA 資產清單，也是要完全準確」，並提出一個
    設計問題——系統裡有 CIA 登記的，也有 DYN-/VC-/AUTO- 開頭的帳外資產（存活清單
    掃到／vCenter 收到／納管流程建立，實際存在但沒登記）。回交給資產清單維護單位
    時，帳外那批要不要一起交，兩種答案意思完全不同（「更新你們的清單」vs
    「這是我實際盤到的，含你們沒有的 N 台」），這件事要使用者拍板，不是我自己決定。

    做法是把決定權交給匯出當下：預設**只匯出 CIA 登記的**（跟這支端點原本名字
    「匯出資產」被理解成「回交清單」最貼近，也是比較保守的預設——不會平白把
    內部帳外資產序號送給外部維護單位）；`include_off_book=true` 才把 DYN-/VC-/AUTO-
    也含進去。硬體分頁一律多一欄「來源」，即使沒勾選帳外也仍然加這一欄
    （值必然全是 CIA），讓兩種模式的檔案結構一致，不會勾不勾選就多一欄少一欄。
    """
    base = _BACKEND_DIR
    meta_all = json.loads((base / "field_meta.json").read_text(encoding="utf-8"))
    fields_meta = meta_all.get("fields", {})
    cat_order = [c["key"] for c in meta_all.get("categories", [])]
    mapping = json.loads((base / "field_mapping.json").read_text(encoding="utf-8"))

    def _cat_rank(col: str) -> int:
        cat = fields_meta.get(col, {}).get("category")
        return cat_order.index(cat) if cat in cat_order else len(cat_order)

    # 帳外資產清單先算一次、三個分頁共用：人員/軟體表沒有自己的「這台是不是帳外」
    # 判定依據，要靠 asset_serial 回頭查 hardware 才知道——算一次避免三份查詢各判各的。
    off_book_serials = {
        r["asset_serial"] for r in conn.execute("SELECT asset_serial FROM hardware")
        if _export_source_of(r["asset_serial"]) != "CIA"
    }

    wb = Workbook()
    first = True
    for table, sheet in (("hardware", "硬體"), ("personnel", "人員"), ("software", "軟體")):
        ws = wb.active if first else wb.create_sheet(sheet)
        if first:
            ws.title = sheet
            first = False
        # 反轉匯入對應表：db欄位 -> 匯入期待的標題（確保 round-trip）。
        # 用 setdefault 保留「第一個出現的標題」當正式標題：field_mapping 允許一個db欄位
        # 掛多個標題別名（例如 api_id 同時吃 "API ID" 與 "AP ID"），若用 {v:k} 推導式會
        # last-wins，匯出表頭會被別名蓋掉、隨設定檔行序漂移。
        rev: dict[str, str] = {}
        for header, column in mapping.get(sheet, {}).items():
            if header != "_comment":
                rev.setdefault(column, header)
        orig = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        skip = {"id", "created_at", "updated_at"}
        orig = [c for c in orig if c not in skip]
        cols = sorted(orig, key=lambda c: (_cat_rank(c), orig.index(c)))
        # 表頭：先用匯入標題(可 round-trip)，退回 field_meta 中文 label，再退回欄名。
        # 「來源」欄只加在硬體分頁——人員/軟體是靠 asset_serial 掛在硬體底下的附屬資料，
        # 沒有獨立的「這筆是不是帳外」意義，重複加同一欄只是雜訊。
        headers = [rev.get(c) or fields_meta.get(c, {}).get("label", c) for c in cols]
        if table == "hardware":
            headers.append("來源")
        ws.append(headers)
        for row in conn.execute(f"SELECT {', '.join(cols)} FROM {table}"):
            if not include_off_book and row["asset_serial"] in off_book_serials:
                continue
            values = [row[c] for c in cols]
            if table == "hardware":
                values.append(_export_source_of(row["asset_serial"]))
            ws.append(values)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from urllib.parse import quote as _quote_export

    suffix = "含帳外" if include_off_book else "CIA"
    fname = f"assets_export_{suffix}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_quote_export(fname)}"},
    )


@router.get("/api/monthly-report/preview")
def monthly_report_preview(
    ym: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """畫面用：這個月能產出什麼、哪些構面有資料、哪些是外部來源。

    先讓人看得到「這份會長什麼樣」再決定要不要產——尤其是「🔌 保留（外部來源）」
    那三段，第一次看到的人會以為是我們漏做的。
    """
    import monthly_report

    ym = ym or _default_ym()
    payload = monthly_report.collect(conn)
    return {
        "year_month": ym,
        "generated_at": payload["generated_at"],
        "sections": [
            {k: v for k, v in sec.items() if k != "data"}   # 明細不進預覽，太大
            for sec in payload["sections"]
        ],
        "external_source": monthly_report.EXTERNAL_SOURCE,
        "snapshot": monthly_report.get_snapshot(conn, ym),
        "previous": monthly_report.get_snapshot(
            conn, monthly_report.previous_month(ym)),
    }


@router.get("/api/monthly-report/export")
def monthly_report_export(
    ym: str | None = None,
    freeze: bool = True,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """下載那一個檔（Markdown）。預設同時凍結當月快照。

    `freeze=false` 用於「我只是想看看」——不留下快照，才不會把還沒補完的資料
    當成當月結算凍結起來。
    """
    import monthly_report

    ym = ym or _default_ym()
    prev = monthly_report.get_snapshot(conn, monthly_report.previous_month(ym))
    md = monthly_report.render_markdown(conn, ym, session["username"], previous=prev)
    if freeze:
        monthly_report.freeze(conn, ym, session["username"])

    from urllib.parse import quote
    fname = f"月報原始資料_{ym}.md"
    return StreamingResponse(
        io.BytesIO(md.encode("utf-8")),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}",
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.get("/api/monthly-report/snapshots")
def monthly_report_snapshots(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """歷次凍結的快照。跨月比較都用這份，不要拿「現在的資料」去比上個月——
    資料每天在補，那樣比出來的差異分不清是「真的變好」還是「資料變全」。"""
    import monthly_report

    return {"snapshots": monthly_report.list_snapshots(conn)}


# ===== 報表列印：來源對照表（CIA／DY／vCenter）=====
# 2026-09-18 使用者：「給我一張 IP/hostname × DY/CIA 表，欄位我自己選，我要拿去
# Excel 跑樞紐分析」。所以這裡**不做分析、不預先過濾**，只吐乾淨的一台一列原始資料。

@router.get("/api/reports/host-sources")
def host_sources_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """來源對照表：一台一列，含小計。小計要對得回首頁與分佈統計，不然這張表就是第三套數字。"""
    import host_sources

    items = host_sources.rows(conn)
    return {
        "items": items,
        "summary": host_sources.summary(items),
        "columns": [{"key": k, "label": v} for k, v in host_sources.COLUMNS.items()],
        "default_columns": list(host_sources.DEFAULT_COLUMNS),
    }


@router.get("/api/reports/host-sources/export")
def host_sources_export(
    cols: list[str] = Query(default=[]),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯出 Excel。沒指定欄位就用預設那組，不要讓人拿到一個空檔案還不知道為什麼。"""
    import host_sources

    chosen = [c for c in cols if c in host_sources.COLUMNS] or list(host_sources.DEFAULT_COLUMNS)
    items = host_sources.rows(conn)

    wb = Workbook()
    ws = wb.active
    ws.title = "來源對照表"
    ws.append([host_sources.COLUMNS[c] for c in chosen])
    for r in items:
        row = []
        for c in chosen:
            v = r.get(c)
            # 布林在 Excel 裡顯示成 TRUE/FALSE 不好做樞紐，改成看得懂又好篩的字
            row.append(("是" if v else "") if isinstance(v, bool) else v)
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"來源對照表_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    from urllib.parse import quote
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/api/reports/hardware-master/export")
def hardware_master_export(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """資產主檔（全欄位＋來源＋machine_key＋同台筆數）＋欄位說明，一個 xlsx 兩個分頁。

    2026-09-18 使用者問「這表格系統能直接產嗎」——原本是一次性腳本產的，改成系統功能。
    """
    import hardware_master

    buf = hardware_master.export_xlsx(conn)
    fname = f"WEBIT3_資產主檔_hardware_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    from urllib.parse import quote
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


__all__ = [
    'dashboard_stats',
    'scan_gaps_endpoint',
    'scan_gaps_visibility_endpoint',
    'scan_gaps_note_endpoint',
    'scan_gaps_export_endpoint',
    'dashboard_composition',
    'stats_systems',
    'stats_system_drilldown',
    'stats_cell_assets',
    'stats_lookup',
    'stats_vip_entries',
    'stats_vip_entry_detail',
    'stats_role_coverage',
    'report_system_group',
    'report_system_group_note',
    'report_drill',
    'report_snapshots',
    'report_snapshot_save',
    'report_snapshot_get',
    'report_physical_distribution',
    'report_physical_distribution_drill',
    'report_system_overview',
    'report_system_overview_drill',
    'report_system_category_template',
    'report_system_category_import',
    'onboard_matrix_endpoint',
    'onboard_matrix_export',
    'export_assets_endpoint',
    'monthly_report_preview',
    'monthly_report_export',
    'monthly_report_snapshots',
    'host_sources_endpoint',
    'host_sources_export',
    'hardware_master_export',
]
