from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

import lifecycle

router = _RouteCollector()


def _eos_assess_fields(hit: dict) -> dict:
    """資產詳情的 EOS：status 維持舊值，另帶原因／是否可信／是否降級（W221-005／006）。"""
    import eos

    a = eos.assess(hit)
    return {"status": a["status"], "reason": a["reason"], "reason_label": a["reason_label"],
            "plan_available": a["plan_available"], "entitlement_state": a["entitlement_state"],
            "entitlement_pending": a["entitlement_pending"], "trusted": a["trusted"], "downgraded": a["downgraded"]}


def _mac_view_of(conn, hw) -> dict | None:
    """MAC 系統校正（measured_hw.mac_view，跟系統校正清單同一份判定）。算不出來回 None 並寫進 note，不擋整頁。"""
    try:
        import measured_hw
        return measured_hw.mac_view(conn, dict(hw))
    except Exception as exc:  # noqa: BLE001 - MAC 比對壞了不可以讓資產頁打不開；原因放進回應
        return {"error": f"MAC 比對失敗：{str(exc)[:160]}"}



@router.get("/api/issues")
def list_issues(
    issue_type: str | None = None,
    is_read: bool | None = None,
    sort_by: str = "detected_at",
    order: str = "desc",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    _check_sort(sort_by, order, ALLOWED_ISSUE_SORT)

    query = "SELECT * FROM comparison_result WHERE 1=1"
    params: list = []
    if issue_type is not None:
        query += " AND issue_type = ?"
        params.append(issue_type)
    if is_read is not None:
        query += " AND is_read = ?"
        params.append(int(is_read))
    query += f" ORDER BY {sort_by} {order.upper()}"

    rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    # 掃描無回應（scan_health，2026-10-28）：跟比對結果併在同一張清單，id 是負數
    import scan_health

    if issue_type is None or issue_type == scan_health.ISSUE_TYPE:
        extra = scan_health.issues(conn, is_read)
        if extra:
            rows += extra
            rows.sort(key=lambda r: (r.get(sort_by) is None, r.get(sort_by) if r.get(sort_by) is not None else ""),
                      reverse=order.lower() == "desc")
    return rows


@router.post("/api/issues/mark-read")
def mark_issues_read_batch(
    body: MarkReadBatchBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """批次標記已處理（失聯會越積越多，逐筆點很慢；2026-09-19）。回實際標到的筆數。"""
    ids = [int(i) for i in body.ids if isinstance(i, int) or str(i).isdigit()]
    ids = [i for i in ids if i > 0]
    if not ids and not any(isinstance(i, int) and i < 0 for i in body.ids):
        return {"marked": 0}
    marked = 0
    import scan_health

    for iid in [int(i) for i in body.ids if isinstance(i, int) and i < 0]:     # 掃描無回應（負數 id）
        marked += 1 if scan_health.mark_read(conn, iid) else 0
    for iid in ids:
        if conn.execute("SELECT 1 FROM comparison_result WHERE id = ?", (iid,)).fetchone():
            mark_comparison_read(conn, iid)
            marked += 1
    return {"marked": marked}


@router.patch("/api/issues/{issue_id}")
def mark_issue_read(
    issue_id: int,
    body: MarkReadBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """標記已處理（契約已定案功能）。目前只支援標記為已讀，不支援改回未讀。"""
    if not body.is_read:
        raise HTTPException(400, "目前只支援標記已處理（is_read=true）")
    if issue_id < 0:              # 掃描無回應（scan_health，負數 id）
        import scan_health

        if not scan_health.mark_read(conn, issue_id):
            raise HTTPException(404, "查無此問題紀錄")
        return next(r for r in scan_health.issues(conn) if r["id"] == issue_id)
    existing = conn.execute(
        "SELECT id FROM comparison_result WHERE id = ?", (issue_id,)
    ).fetchone()
    if existing is None:
        raise HTTPException(404, "查無此問題紀錄")
    mark_comparison_read(conn, issue_id)
    row = conn.execute("SELECT * FROM comparison_result WHERE id = ?", (issue_id,)).fetchone()
    return dict(row)


@router.get("/api/assets/field-groups")
def asset_field_groups(session: sqlite3.Row = Depends(require_auth)):
    """D31精神延伸：常用/進階分層來自設定檔，前端不用自己硬編欄位清單。"""
    return json.loads(FIELD_GROUPS_PATH.read_text(encoding="utf-8"))


# ⚠️ 這支一定要定義在 /api/assets/{asset_serial} 之前。
# FastAPI 依「定義順序」比對路由，先中先贏：放在參數路由後面的話，
# /api/assets/facets 會被當成「查詢序號叫 facets 的資產」而回 404，
# 前端拿不到數字又被 catch 吞掉，畫面只是靜靜地少了數字，很難查。
# （2026-07-29 實際踩到，篩選按鈕的台數一直不出現就是這個原因。）
@router.get("/api/assets/facets")
def asset_facets(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """各平台／各類型分別有幾台，給篩選按鈕直接標在上面。

    沒有數字的話，使用者得一個一個點進去才知道哪個分類有東西、哪個是空的。

    刻意跟 list_assets 共用 asset_classify：兩邊若各算各的，按鈕上寫「5 台」
    點進去卻是 3 筆，比不顯示數字更糟。

    注意角色是多值的（一台同時聽 3306 和 80 就既是資料庫也是 Web），
    所以類型的數字加總會大於資產總數 —— 那是實情，不是重複計算的 bug。
    """
    import asset_classify

    import pipeline

    # [口徑] 2026-09-20 公司驗收：籌碼上的數字是**筆**而且**含退役**，
    # 加起來 8,085 跟同一頁分頁寫的「硬體 7,923」差 162（剛好是退役筆數），
    # 「AIX 16」也對不起漏斗的「AIX 8 台」。改成逐台（machine_key）並排除退役，
    # 同時把筆數一起回傳，畫面要講得出自己在數什麼。
    import manage_state as _ms
    import system_stats as _ss

    meta = {r["asset_serial"]: r for r in conn.execute(
        "SELECT asset_serial, hostname, ip, asset_status FROM hardware")}

    cls = asset_classify.classify_all(conn)
    by_platform: dict[str, set] = {}
    by_os: dict[str, set] = {}
    by_detail: dict[str, set] = {}
    by_role: dict[str, set] = {}
    rows_os: dict[str, int] = {}
    retired_rows = 0
    for serial, info in cls.items():
        m = meta.get(serial)
        if m is not None and (m["asset_status"] or "").strip() in _ms.RETIRED_STATUS:
            retired_rows += 1
            continue                      # 退役不進篩選數字（跟同頁的「硬體 N 筆」同母體）
        mk = (_ss.machine_key(m["hostname"], m["ip"], serial) if m is not None else serial)
        o = info.get("os_type") or pipeline.OS_UNSET
        by_os.setdefault(o, set()).add(mk)
        rows_os[o] = rows_os.get(o, 0) + 1
        by_platform.setdefault(info.get("platform") or "未知", set()).add(mk)
        if info.get("os_detail"):
            by_detail.setdefault(info["os_detail"], set()).add(mk)
        for r in info.get("roles") or ["unknown"]:
            by_role.setdefault(r, set()).add(mk)
    # 同一台的多筆登記若 OS 不一致，會同時被算進兩類 → 籌碼加總會比母體多幾台。
    # 那是實情不是 bug，但畫面要講出來（不講的話人只會覺得數字在騙他）。
    _seen: dict[str, int] = {}
    for mks in by_os.values():
        for mk in mks:
            _seen[mk] = _seen.get(mk, 0) + 1
    os_overlap_machines = sum(1 for n in _seen.values() if n > 1)
    _n = lambda d: {k: len(v) for k, v in d.items()}      # noqa: E731 - 集合換成台數
    by_platform, by_os, by_detail, by_role = _n(by_platform), _n(by_os), _n(by_detail), _n(by_role)
    # os_type／os_detail：兩層（2026-09-18，跟漏斗同一套）；platform 保留給舊連結相容
    return {"os_type": by_os, "os_order": list(pipeline.OS_ORDER),
            "os_detail": by_detail, "os_detail_order": list(asset_classify.LINUX_DETAILS),
            "platform": by_platform, "role": by_role,
            # total 維持「筆」（舊行為、給既有畫面用），另外給台數與母體說明
            "total": len(cls), "total_rows": len(cls) - retired_rows,
            "total_machines": len({_ss.machine_key(m["hostname"], m["ip"], s)
                                   for s, m in meta.items()
                                   if (m["asset_status"] or "").strip() not in _ms.RETIRED_STATUS}),
            "retired_rows": retired_rows,
            "os_overlap_machines": os_overlap_machines,
            "basis": "非退役（含帳外）；籌碼數字為台數，machine_key 去重"}


# 同樣必須排在 /api/assets/{asset_serial} 之前，理由見上面 facets 的說明。
@router.get("/api/assets/duplicates")
def asset_duplicates(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    import vip_view

    groups = _duplicate_groups(conn)
    return {
        "groups": groups,
        # 「多出來的筆數」＝真的清乾淨可以少掉幾筆。**只算疑似真重複**——
        # 一機多系統／VIP 分列那幾筆是真實的服務登記，刪了就丟資訊（2026-09-14）。
        "extra_rows": sum(g["extra_rows"] for g in groups),
        "by_kind": {k: sum(1 for g in groups if g["kind"] == k) for k in vip_view.DUP_KIND_ORDER},
        "kind_labels": vip_view.DUP_KIND_LABEL,
    }


@router.post("/api/assets/duplicates/release")
def release_duplicate_serials(
    body: DuplicateReleaseBody,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """人工確認「這幾個序號是同一台被重複登記」，把多出來的序號真正刪掉、還給號碼池。

    序號釋放＝直接刪除該 asset_serial 在 hardware/software/personnel 三張表的資料列
    （後兩張表對 hardware.asset_serial 有外鍵約束，必須先刪才能刪 hardware）。
    這是使用者明確要的語意：不是「報廢/停用」（那是資產生命週期狀態，序號還留在表裡，
    UNIQUE 約束會擋住之後同號的新資產進來），而是讓這個序號值真正空出來可以重用。

    這只是內部管理工具，使用者確認不需要保留刪除稽核記錄，故不做快照。

    伺服器端重新查一次目前這組（hostname, ip）真實的成員，不信任前端傳來的序號清單是不是
    還有效——避免兩個人同時操作、或畫面資料已經過期時誤刪（比照 merge_review 批次合併同樣
    的「不信任前端快照」原則）。
    """
    # 審查 #4：成員用全站同一把尺（machine_key）重抓，跟重複清單同一份分組
    import system_stats as _ss_r
    import vip_view as _vv
    _key = _ss_r.machine_key_of(conn, body.hostname, body.ip)   # 帶序號：實測主機名校正（PRECEDENCE1）
    current = {m["asset_serial"] for k, ms_ in _vv.duplicate_candidate_groups(conn) if k == _key for m in ms_}
    release_set = set(body.release_serials)
    if not release_set:
        raise HTTPException(400, "沒有要釋放的序號")
    missing = release_set - current
    if missing:
        raise HTTPException(400, f"這幾個序號已經不在這組裡了，資料可能已變更，請重新整理：{'、'.join(sorted(missing))}")
    if len(release_set) >= len(current):
        raise HTTPException(400, "釋放後這組至少要保留一筆，不能全部釋放")

    # **刪之前先存整列。**
    # 原本這裡明寫「不做快照」。但清冊是稽核對象——「少了一台」跟「合併掉一台」
    # 事後看起來一模一樣，沒有快照就永遠分不出來，也還原不回去。
    # 成本是一張表；換到的是「按錯了還有救」。
    _ensure_merge_audit(conn)
    actor = session["username"] if session else None
    for serial in release_set:
        snap = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?",
                            (serial,)).fetchone()
        conn.execute(
            "INSERT INTO merge_audit (merged_at, actor, hostname, ip, "
            "released_serial, kept_serials, snapshot) VALUES (?,?,?,?,?,?,?)",
            (_now_local(), actor, body.hostname, body.ip, serial,
             ", ".join(sorted(current - release_set)),
             json.dumps(dict(snap) if snap else {}, ensure_ascii=False, default=str)))
        conn.execute("DELETE FROM software WHERE asset_serial = ?", (serial,))
        conn.execute("DELETE FROM personnel WHERE asset_serial = ?", (serial,))
        conn.execute("DELETE FROM hardware WHERE asset_serial = ?", (serial,))
    conn.commit()
    return {"ok": True, "released": sorted(release_set), "remaining": len(current) - len(release_set)}


@router.get("/api/assets/duplicates/merged")
def merged_history(
    limit: int = 200,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """併過哪些、什麼時候、誰按的、被刪的那筆長什麼樣。

    **使用者下週看到台數少了，要分得出「合併掉的」與「掉資料的」。**
    沒有這支，兩者在畫面上一模一樣。
    """
    _ensure_merge_audit(conn)
    rows = conn.execute(
        "SELECT id, merged_at, actor, hostname, ip, released_serial, kept_serials "
        "FROM merge_audit ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 1000),)).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.post("/api/assets/duplicates/dismiss")
def dismiss_duplicate_group(
    body: DuplicateDismissBody,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """標記「這組（同主機名+IP）其實不是重複登記」，例如 DB 主機本來就會掛多個業務系統/VIP。

    之後 /api/assets/duplicates 不會再列出這組。UNIQUE(hostname, ip) 防止重複點擊堆記錄。
    """
    h, i = body.hostname.strip().lower(), body.ip.strip()
    conn.execute(
        "INSERT OR IGNORE INTO duplicate_dismiss (hostname, ip, note, dismissed_by) "
        "VALUES (?, ?, ?, ?)",
        (h, i, body.note, session["username"]),
    )
    conn.commit()
    return {"ok": True}


@router.get("/api/assets")
def list_assets(
    response: Response,
    environment: str | None = None,
    q: str | None = None,
    scan_status: str | None = None,
    filter_field: str | None = None,
    filter_value: str | None = None,
    virtual: str | None = None,
    platform: str | None = None,
    os_type: str | None = None,
    os_detail: str | None = None,
    os_class: str | None = None,
    role: str | None = None,
    canonical_os: str | None = None,
    canonical_model: str | None = None,
    location_group: str | None = None,
    os_source: str | None = None,
    environment_group: str | None = None,
    collect: str | None = None,
    source: str | None = None,
    sort_by: str = "hostname",
    order: str = "asc",
    limit: int | None = None,
    offset: int = 0,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """scan_status：讓儀表板的「兩邊相符 / 登記卻掃不到」磚塊可以點進來看是哪幾台。
    判定與 /api/dashboard/stats 共用 _row_in_keys，數字才會跟磚塊對得起來。

    environment 這裡吃單一環境值；儀表板用的是 ENV_FILTER_PRESETS（正式／正式+測試／全部），
    前端下鑽時會把 preset 展開成對應的單一環境或不帶此參數。
    """
    import manage_state as _ms
    import system_report as _sr
    # health_rank 不是資料表欄位（體檢是算出來的），SQL 排不了，改在 Python 端排。
    # 但它必須能排——天條：表格每一欄都要能點排序。沒有它，「把要看的機器排到
    # 最上面」就得自己一台一台掃，那這一欄等於白做。
    sort_health = sort_by == "health_rank"
    # 可信度（trust_score）同理：是算出來的分數，不是資料表欄位，改在 Python 端排
    sort_trust = sort_by == "trust_score"
    _check_sort("hostname" if (sort_health or sort_trust) else sort_by, order,
                ALLOWED_ASSET_SORT | _sortable_columns(conn, "hardware"))

    if scan_status is not None and scan_status not in ("overlap", "ica_only"):
        raise HTTPException(400, "scan_status 只接受 overlap（登記且掃得到）或 ica_only（登記但掃不到）")

    if os_source is not None and os_source not in ("facts", "guessed"):
        raise HTTPException(400, "os_source 只接受 facts（機器實際回報）或 guessed（掃描推測）")

    # 納管狀態。「收不到」刻意拆成三種——畫面上都是收不到，但要做的事完全不同：
    #   revoked 是刻意的結果（不要有人跑去「修」一台剛撤銷的機器）
    #   failed  才是待辦
    #   never   是還沒開始，混進 failed 會讓待辦數字灌水
    COLLECT_FILTERS = {
        "ok":      ("collect_ok = 1", ()),
        "revoked": ("collect_ok = 0 AND COALESCE(collect_error,'') LIKE '%取消納管%'", ()),
        "failed":  ("collect_ok = 0 AND COALESCE(collect_error,'') NOT LIKE '%取消納管%'", ()),
        "never":   ("collect_ok IS NULL", ()),
    }
    if collect is not None and collect not in COLLECT_FILTERS:
        raise HTTPException(
            400, "collect 只接受 ok（收得到）、revoked（人主動取消納管）、"
                 "failed（連不上）或 never（從沒試連過）")

    query = "SELECT * FROM hardware WHERE 1=1"
    params: list = []
    # 預設排除退役（停用/報廢/閒置），口徑要跟 composition()/eos_summary() 對得起來——
    # 那兩支頭條數字都已經排除退役，這裡不排除的話，點進來的筆數會比頭條數字多，
    # 使用者會以為算錯了（首頁「看全部有效資產」的連結標題就寫著「不含退役」）。
    # 例外：使用者自己用 filter_field=asset_status 明確要看某個狀態（含退役徽章本身
    # 的「另有 N 台退役」連結），這時候該尊重他要看的就是那個狀態，不能反過來擋掉。
    import manage_state as _manage_state

    if filter_field != "asset_status":
        retired_placeholders = ",".join("?" for _ in _manage_state.RETIRED_STATUS)
        query += f" AND COALESCE(asset_status, '') NOT IN ({retired_placeholders})"
        params.extend(_manage_state.RETIRED_STATUS)
    if collect is not None:
        cond, cond_params = COLLECT_FILTERS[collect]
        query += f" AND {cond}"
        params.extend(cond_params)
    # 來源篩選：registered＝正式登記（排除 DYN-/VC-/AUTO- 帳外前綴）；
    # offbook＝只看帳外（掃到/vCenter 收到、但沒登記在 CIA 上的）。
    # 讓「在管資產（只算登記）」與「另外掃到未登記 N 台」兩個頭條各自點得進正確的清單，
    # 頭條數字＝點進去的筆數（天條：加總與下鑽走同一份口徑）。
    if source == "registered":
        query += f" AND NOT {_sr.off_book_sql()}"
    elif source == "offbook":
        query += f" AND {_sr.off_book_sql()}"
    if environment is not None:
        # 同時吃「單一環境值」與儀表板用的 preset（正式／正式+測試／全部）。
        # 儀表板磚塊要能原封不動把目前的環境選擇帶進下鑽，否則點進來的筆數會跟磚塊對不上。
        if environment in ENV_FILTER_PRESETS:
            env_list = ENV_FILTER_PRESETS[environment]
            if env_list is not None:  # None＝全部，不加條件
                placeholders = ",".join("?" for _ in env_list)
                query += f" AND environment IN ({placeholders})"
                params.extend(env_list)
        else:
            query += " AND environment = ?"
            params.append(environment)
    # .strip()：使用者常從 Excel/終端機複製貼上，尾隨空白會讓搜尋直接 0 筆，
    # 看起來像「查無此主機」，其實只是多了一個空格——這是實測踩到的體感問題。
    if q and q.strip():
        query += " AND (hostname LIKE ? OR ip LIKE ? OR asset_serial LIKE ?)"
        like = f"%{q.strip()}%"
        params.extend([like, like, like])
    # 「同值篩選」：畫面上任何一個類別型的值（設備機型／群組名稱／保管者…）都要能點進來
    # 看「還有誰跟它一樣」。欄位名走真實欄位白名單，值走參數化，不會有字串拼進 SQL。
    if filter_field is not None:
        if filter_field not in _sortable_columns(conn, "hardware"):
            raise HTTPException(400, f"不支援的篩選欄位：{filter_field}")
        if filter_value in (None, ""):
            # 點「空值」也要能看——「哪些機器這欄是空的」本身就是有用的盤點問題
            query += f" AND ({filter_field} IS NULL OR {filter_field} = '')"
        elif filter_field == "asset_status" and filter_value == "__retired__":
            # 「退役」合看：值清單取正典 lifecycle.RETIRED_STATUS，前端不再寫死「停用,報廢,閒置」
            # （口徑一改——例如閒置不算退役——前端連結自動跟著走）
            _vals = sorted(_manage_state.RETIRED_STATUS)
            query += f" AND trim(coalesce(asset_status,'')) IN ({','.join('?' for _ in _vals)})"
            params.extend(_vals)
        elif filter_field == "asset_status" and "," in filter_value:
            # 多值篩選（如「退役」＝停用,報廢,閒置 三種狀態合看）：逗號分隔當 IN 清單。
            # ⚠️ 只限 asset_status 這種有限列舉值欄位——備註/用途說明這類自由文字欄若
            # 剛好存了含逗號的內容，會被誤拆成多值查詢，完全比對不到，寧可不支援。
            values = [v for v in filter_value.split(",") if v]
            placeholders = ",".join("?" for _ in values)
            query += f" AND {filter_field} IN ({placeholders})"
            params.extend(values)
        else:
            query += f" AND {filter_field} = ?"
            params.append(filter_value)
    # 虛擬／實體篩選：一律走正典 manage_state.is_vm_sql（跟 is_vm_value 同義，含 device_model
    # 「(VM)」標記）。以前這裡自己寫「非空且不是 0/none」，漏了 device_model 標記 → 跟儀表板
    # 差 1,188 台（2026-09-18 實測）。現在同一把尺。
    if virtual in ("yes", "no"):
        vm_true = _ms.is_vm_sql()
        query += f" AND {vm_true}" if virtual == "yes" else f" AND NOT {vm_true}"
    # OS 來源：首頁誠實揭露「幾台是機器自己報的、幾台是掃描猜的」，那兩個數字要能點進來
    # 看是哪幾台。判定必須跟 manage_state.composition() 的 has_real_os 完全同一條規則
    # （非空且不是字面 N/A），否則點進來的筆數會跟首頁對不上——那比不能點更糟。
    if os_source in ("facts", "guessed"):
        has_real_os = "(os IS NOT NULL AND length(trim(os)) > 0 AND UPPER(TRIM(os)) != 'N/A')"
        query += f" AND {has_real_os}" if os_source == "facts" else f" AND NOT {has_real_os}"
    query += f" ORDER BY {'hostname' if (sort_health or sort_trust) else sort_by} {order.upper()}"

    rows = conn.execute(query, params).fetchall()

    if scan_status is not None:
        # 掃描比對用 Python 做而不是塞進 SQL：判定是「IP 或主機名稱任一命中」，
        # 跟 /api/scan/unregistered、儀表板同一套；寫成 SQL 反而會分岔成兩套規則。
        scanned = _scanned_alive_rows(conn, _latest_scan_time(conn))
        ips, hostnames = _scan_keys(scanned)
        want_matched = scan_status == "overlap"
        rows = [r for r in rows if _row_in_keys(r, ips, hostnames) == want_matched]

    # 平台／角色篩選。兩者都在 Python 端做，理由跟 scan_status 一樣：
    # 平台判定是「真 OS > 掃描推測 > 機型」的優先序邏輯，角色來自 host_service 的
    # 監聽埠——都不是 hardware 表上的單一欄位，硬塞進 SQL 會分岔成兩套規則。
    if platform or role or os_type or os_detail or os_class:
        import asset_classify

        cls = asset_classify.classify_all(conn)
        # OS 大類（首頁平台長條點進來）：跟 composition 的 by_os_class 同一支 os_class.classify
        if os_class:
            wanted_c = {x.strip() for x in os_class.split(",") if x.strip()}
            rows = [r for r in rows if cls.get(r["asset_serial"], {}).get("os_class") in wanted_c]
        if platform:
            wanted = {p.strip() for p in platform.split(",") if p.strip()}
            rows = [r for r in rows if cls.get(r["asset_serial"], {}).get("platform") in wanted]
        # OS 類型（大類）＋ Linux 細分（2026-09-18，跟漏斗同一套）
        if os_type:
            wanted_o = {x.strip() for x in os_type.split(",") if x.strip()}
            rows = [r for r in rows if cls.get(r["asset_serial"], {}).get("os_type") in wanted_o]
        if os_detail:
            wanted_d = {x.strip() for x in os_detail.split(",") if x.strip()}
            rows = [r for r in rows if cls.get(r["asset_serial"], {}).get("os_detail") in wanted_d]
        if role:
            wanted_r = {x.strip() for x in role.split(",") if x.strip()}
            rows = [r for r in rows
                    if wanted_r & set(cls.get(r["asset_serial"], {}).get("roles", ["unknown"]))]

    # 平台下鑽點某個 OS 版本（如「Windows Server 2022」）：跟 platform 同理，
    # 正規化是「原值 → 收斂寫法」的邏輯，不是資料庫裡的單一欄位，只能在這層過濾。
    # 只比對有真 OS 的資產——composition() 的 by_platform_os 對純推測的機器標「未知版本／(推測)」，
    # 那些沒有穩定的原始字串可比對，這裡就不硬篩，維持只看點得到來源的部分。
    if canonical_os:
        import normalize

        rows = [
            r for r in rows
            if r["os"] and normalize.normalize_os(r["os"], conn, r["device_model"])["canonical"] == canonical_os
        ]

    # EOS 頁點某個硬體型號（如「Dell PowerEdge R740」）：跟 canonical_os 同理。
    # ⚠️ 硬體型號分頁的項目其實有兩種不同來源，篩選要兩種都檢查，缺一都會篩到
    # 0 筆（2026-08-13 實際發現，兩個都是這輪才修）：
    # 1. device_model 側：normalize_model() 算出來的——之前漏傳 hint，靠 hint
    #    才解析出來的型號（如「IBM DS3524 SAN Switch」）永遠篩不到自己。
    # 2. os 側被併過來的（HW_ROUTED_PRODUCTS，例：Dell iDRAC (BMC)／Unisphere
    #    Central）：這些 canonical 其實是 normalize_os() 算出來的，不是
    #    normalize_model()，原本的篩選完全沒檢查這條路徑，這類資產點「N台→」
    #    100% 篩到 0 筆，不是個案。
    if canonical_model:
        import normalize

        def _row_matches_hw_canonical(r: sqlite3.Row) -> bool:
            if r["os"]:
                os_info = normalize.normalize_os(r["os"], conn, r["device_model"])
                if os_info["product"] in normalize.HW_ROUTED_PRODUCTS \
                        and os_info["canonical"] == canonical_model:
                    return True
            if r["device_model"]:
                model_info = normalize.normalize_model(r["device_model"], conn, _model_hint(r))
                if model_info["canonical"] == canonical_model:
                    return True
            return False

        rows = [r for r in rows if _row_matches_hw_canonical(r)]

    # 機房分組（板橋／敦南／內湖／分公司）不是資料庫裡存的值，是 location_groups.json
    # 從 physical_location 原值（01_板橋機房…）收斂出來的，所以只能在這層過濾。
    # 儀表板點「板橋 120 台」要能跳進來看是哪 120 台，用 filter_field 精確比對做不到。
    if location_group:
        import manage_state

        rows = [r for r in rows
                if manage_state.location_matches(r["physical_location"], location_group)]

    # 環境別分組同理（UAT/DEV/OA 併進「測試」）。儀表板的機房×環境別交叉表點格子時
    # 一定要用這個而不是 filter_field=environment 精確比對——「測試」那格算的是合併後
    # 的 705 台，精確比對只撈得到原值剛好是「測試」的 695 台。
    # 格子寫 705、點進去列 695，看的人只會認定系統在騙他，這張表就白做了。
    if environment_group:
        import manage_state

        rows = [r for r in rows
                if manage_state.group_environment(r["environment"]) == environment_group]

    # 分頁：不帶 limit 時行為與以前完全相同（回全部、純陣列），既有呼叫端不受影響。
    # 總筆數放在 X-Total-Count 標頭而不是包成 {items, total}：改回應結構會弄壞所有
    # 現有前端；標頭是加法，不是改法。
    import health_check

    health = health_check.evaluate_all(conn) if rows else {}
    if sort_health:
        # 排序權重：紅 > 黃 > 綠 > 沒體檢（退役）。兩個燈取最差的當主鍵，
        # 次鍵用問題數量——同樣紅燈，問題多的先看。
        weight = {"bad": 0, "warn": 1, "ok": 2}

        def hrank(r):
            h = health.get(r["id"])
            if not h:
                return (3, 0, _s_lower(r["hostname"]))
            worst = min(weight[h["machine"]], weight[h["data"]])
            return (worst, -len(h["issues"]), _s_lower(r["hostname"]))

        rows = sorted(rows, key=hrank, reverse=(order.lower() == "desc"))

    # 資料可信度（2026-09-11）：每台有幾個來源互相印證＋有沒有機器證據。規則見 trust_score.py。
    # 退役資產不評分（compute_all 只算非退役），畫面顯示「—」。
    import trust_score

    trust = trust_score.compute_all(conn) if rows else {}
    if sort_trust:
        rows = sorted(
            rows,
            key=lambda r: (trust.get(r["asset_serial"], {}).get("score", -1), _s_lower(r["hostname"])),
            reverse=(order.lower() == "desc"))

    total = len(rows)
    if limit is not None:
        if limit < 1 or limit > 1000:
            raise HTTPException(400, "limit 只接受 1–1000")
        if offset < 0:
            raise HTTPException(400, "offset 不能是負數")
        rows = rows[offset:offset + limit]
    response.headers["X-Total-Count"] = str(total)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"

    # 平台／角色一併回傳（加欄位是加法，不動既有結構）。分類在分頁之後才算，
    # 只算這一頁的那幾十筆，不用為了顯示兩個標籤把全表都跑一遍。
    import asset_classify

    cls = asset_classify.classify_all(conn) if rows else {}
    import onboard_exempt

    _exempt_set = onboard_exempt.active_serials(conn) if rows else set()
    # vCenter 列為 ESXi 主機的名單：整個請求只查一次。
    # 放進下面的逐列迴圈會變成每列全表掃一次 source_record（2026-10-16）。
    import onboard_eligibility as _oe

    _esxi_names = _oe.esxi_host_names(conn) if rows else frozenset()
    # 同一台的登記筆數（項 10，2026-09-18）：資產查詢是逐筆列，看不出哪些是同一台。
    # 用正典 machine_key 算「這台共登記幾筆」，畫面每列標「同台 N 筆」，跟台詳細頁／主機清單口徑一致。
    # 只算非退役（退役是上一台，不算同台——跟別名一致）。一次撈完，不在迴圈逐台查。
    _mkcount: dict[str, int] = {}
    if rows:
        import system_stats as _ss
        for _hr in conn.execute(
                "SELECT hostname, ip, asset_serial, asset_status FROM hardware"):
            if (_hr["asset_status"] or "").strip() in _ms.RETIRED_STATUS:
                continue
            k = _ss.machine_key(_hr["hostname"], _hr["ip"], _hr["asset_serial"])
            _mkcount[k] = _mkcount.get(k, 0) + 1
    out = []
    for r in rows:
        d = dict(r)
        import system_stats as _ss2
        d["same_host_count"] = _mkcount.get(
            _ss2.machine_key(r["hostname"], r["ip"], r["asset_serial"]), 1)
        c = cls.get(r["asset_serial"], {})
        d["platform"] = c.get("platform", "未知")
        d["os_type"] = c.get("os_type")
        d["os_detail"] = c.get("os_detail")
        d["roles"] = c.get("roles", ["unknown"])
        h = health.get(r["id"])
        # 退役資產不體檢（evaluate_all 直接跳過），所以這裡可能沒有——
        # 沒有就給 None，畫面顯示「—」，不要假裝它是綠的。
        d["health_machine"] = h["machine"] if h else None
        d["health_data"] = h["data"] if h else None
        d["health_headline"] = h["headline"] if h else ""
        d["health_issues"] = h["issues"] if h else []
        # 誠實揭露、不算體檢問題：有沒有被 SSH/WinRM 直接驗證過（2026-08-25 方案A）。
        d["health_verified"] = h["verified"] if h else None
        t = trust.get(r["asset_serial"])
        d["trust_score"] = t["score"] if t else None
        d["trust_sources"] = t["sources"] if t else None      # 有／沒有／None＝不適用
        d["trust_evidence"] = t["evidence"] if t else None    # managed／alive／None
        d["trust_alive_basis"] = t["alive_basis"] if t else None   # scan／dynassets／None
        d["trust_dyn_at"] = t["dynassets_imported_at"] if t else None
        d["trust_kind"] = t["kind"] if t else None
        # 使用者 2026-08-13 要求：「未分類」清單點進來看到的逐台列表，每一台都要
        # 顯示系統推測，不能只在單筆資產詳情頁才有——不然像「N/A」這種大雜燴群組
        # 裡面明明有能猜到的資產，卻被群組彙總層級擋住看不到。
        # 能不能納管：清單頁的「一鍵納管」按鈕要用（2026-09-16）。只算這一頁的幾十筆
        d["onboard_block"] = _onboard_block(d, conn, _esxi_names)
        d["onboard_exempt"] = r["asset_serial"] in _exempt_set
        guess_value, guess_kind, guess_confirmed = _identity_guess(r, conn)
        d["identity_guess"] = guess_value
        d["identity_guess_kind"] = guess_kind
        d["identity_guess_confirmed"] = guess_confirmed
        out.append(d)
    return out


@router.post("/api/assets/batch-lookup")
def batch_lookup_assets(
    body: BatchLookupBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """值班用批次查詢（貼上多筆主機名稱/IP，一行一筆）：精確比對，不是like模糊搜尋。"""
    terms = [t.strip() for t in body.terms if t.strip()]
    if not terms:
        return []
    placeholders = ",".join("?" for _ in terms)
    rows = conn.execute(
        f"SELECT * FROM hardware WHERE hostname IN ({placeholders}) OR ip IN ({placeholders})",
        terms + terms,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/api/personnel")
def list_personnel(
    q: str | None = None,
    sort_by: str = "person_name",
    order: str = "asc",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    _check_sort(sort_by, order, ALLOWED_PERSONNEL_SORT | _sortable_columns(conn, "personnel"))

    query = "SELECT * FROM personnel WHERE 1=1"
    params: list = []
    if q and q.strip():
        query += " AND (person_name LIKE ? OR asset_serial LIKE ?)"
        like = f"%{q.strip()}%"
        params.extend([like, like])
    query += f" ORDER BY {sort_by} {order.upper()}"

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@router.get("/api/software")
def list_software(
    q: str | None = None,
    sort_by: str = "asset_name",
    order: str = "asc",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    _check_sort(sort_by, order, ALLOWED_SOFTWARE_SORT | _sortable_columns(conn, "software"))

    query = "SELECT * FROM software WHERE 1=1"
    params: list = []
    if q and q.strip():
        query += " AND (asset_name LIKE ? OR hostname LIKE ? OR ip LIKE ?)"
        like = f"%{q.strip()}%"
        params.extend([like, like, like])
    query += f" ORDER BY {sort_by} {order.upper()}"

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@router.get("/api/hosts")
def hosts_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """以「台」為單位的主機清單（machine_key 去重）：一台一列，附這台的多重登記（別名）。
    台數＝首頁在管台（同母體：排退役＋帳外）。見 host_view.list_hosts。"""
    import host_view

    return host_view.list_hosts(conn)


@router.get("/api/assets/{asset_serial}")
def asset_detail(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """主機詳細頁：D24分層邏輯不外露給使用者（見D31）——這裡回傳全部欄位，
    常用/進階顯示由前端依 field-groups 設定檔決定，不是後端替使用者過濾資料。
    """
    import asset_classify

    hardware = conn.execute(
        "SELECT * FROM hardware WHERE asset_serial = ?", (asset_serial,)
    ).fetchone()
    if hardware is None:
        raise HTTPException(404, "查無此資產")

    personnel = conn.execute(
        "SELECT * FROM personnel WHERE asset_serial = ?", (asset_serial,)
    ).fetchall()
    software = conn.execute(
        "SELECT * FROM software WHERE asset_serial = ?", (asset_serial,)
    ).fetchall()
    # 「軟體」分頁要顯示的是真正掃到的安裝軟體清單（host_package，軟體盤點那份），
    # 不是上面舊的 software 表（那是登記的 CIA 資料庫/軟體治理欄，多半空的）。
    # 2026-09-13 使用者發現：軟體盤點顯示這台有 5xx 套件，詳細頁卻是 0——就是這裡查錯表。
    # 範圍＝這台機器（同一台的全部登記＋這個 IP），跟上方收集狀態同一套。
    import asset_collect_status
    import machine_scope
    packages = machine_scope.machine_packages(conn, asset_serial, hardware)
    history: list[sqlite3.Row] = []
    if hardware["hostname"]:
        history = conn.execute(
            "SELECT * FROM comparison_result WHERE hostname = ? ORDER BY detected_at DESC",
            (hardware["hostname"],),
        ).fetchall()

    # 納管紀錄（2026-09-22 使用者：「AIX 我已經納管多次，每次狀態我看都是未納管」）。
    #
    # 他納管過多次，資產頁卻**什麼都看不到**——因為納管只寫 onboard_audit，
    # 而那張表在這一頁完全沒有呈現。上面那個 history 讀的是 comparison_result
    # （**差異偵測結果**，不是操作紀錄），名字叫「歷史時間軸」很容易被當成
    # 「這台發生過什麼」，實際上它只在收集成功、有東西可比對之後才會有資料。
    #
    # 結果是：納管失敗 → 收不到資料 → 沒有比對 → 時間軸 0 →
    # 使用者看到的是「系統根本沒收到我的動作」，於是再納管一次。做了三次才來問。
    #
    # 資料本來就在，只是沒拿出來。**失敗的也要列**——只列成功的話，
    # 「試過三次都失敗」跟「從來沒試過」在畫面上仍然長得一模一樣。
    onboard_history = [dict(r) for r in conn.execute(
        "SELECT created_at, platform, trigger, login_user, triggered_by, ok, stage, message "
        "FROM onboard_audit WHERE target_ip = ? ORDER BY id DESC LIMIT 50",
        (hardware["ip"],))]

    collect_status = asset_collect_status.compute(conn, asset_serial, hardware, len(packages))

    import eos
    import normalize

    hw = dict(hardware)
    # 同一台的「別名／多重登記」（2026-09-18 使用者：「一台像一個人，可以有多個外號」）。
    # CIA 清冊是「每個服務／VIP／DB 實例登記一筆」，同一台 host 常有多筆（不同序號、不同用途）。
    # 這裡把同 machine_key 的其他筆列出來，讓詳細頁看起來是「一台＋多個外號」——
    # ⚠️ 唯讀彙整，不動原始資料、不自動合併（本專案鐵律；合併是人工決定）。
    # 判準跟 system_stats.machine_key 一致：主機名+IP 相同才算同一台；缺 IP／0.0.0.0 各算一台。
    # ⚠️ 報廢／停用／閒置的登記不算同一台（2026-09-18 使用者：「報廢跟使用中不能算同一台」）——
    # IP／主機名會被回收給新機器，退役那筆是「上一台」，不是這台的外號。所以只在**同生命週期**
    # （都非退役，或都退役）內找別名，不跨退役界線。
    import manage_state as ms
    aliases: list[dict] = []
    _h = (hw.get("hostname") or "").strip()
    _ip = (hw.get("ip") or "").strip()
    _self_retired = (hw.get("asset_status") or "").strip() in ms.RETIRED_STATUS
    # 審查 #6：同一台的其他登記改用全站同一把尺（manage_state.same_machine_serials：machine_key＋同生命週期），
    # 不再自己用 (主機名, IP) 查——主機名口徑下同一台可以有不同 IP。
    _others = [s_ for s_ in ms.same_machine_serials(conn, asset_serial) if s_ != asset_serial]
    if _others:
        _ph = ",".join("?" for _ in _others)
        for r in conn.execute(
            f"SELECT asset_serial, asset_purpose, asset_name, big_ip_vip, environment, api_id, "
            f"asset_status FROM hardware WHERE asset_serial IN ({_ph}) ORDER BY asset_serial", _others):
            aliases.append(dict(r))
    hw["alias_count"] = len(aliases) + 1  # 含自己，這台總共登記幾筆
    # 這台掛的入口（VIP）＋每個入口後面還有誰（2026-09-18，見 vip_view.host_vips）
    import vip_view
    try:
        vip_pools = vip_view.host_vips(conn, _h, _ip, retired=_self_retired)
    except Exception as exc:  # noqa: BLE001 - 輔助資訊，壞了不擋整頁，但要講出來
        vip_pools = {"vips": [], "misfiled": [], "basis": None, "error": str(exc)}
    # 能不能納管（2026-09-15）：儲存設備／SAN switch 等詳細頁以前還掛著「一鍵納管」。
    # 判準跟收集分派／批次納管同一支（onboard_eligibility），畫面不另外猜。
    hw["onboard_block"] = _onboard_block(hw, conn)
    # 人工豁免（非納管設備）——詳細頁要顯示是誰標的、為什麼，並提供取消（2026-09-16）
    try:
        import onboard_exempt

        hw["onboard_exempt"] = onboard_exempt.get(conn, hw.get("asset_serial"))
    except Exception:  # noqa: BLE001
        hw["onboard_exempt"] = None
    # 作業系統實測優先（v1.473.0）：已納管、收到 OS 的主機，畫面主值＝實測、CIA 原值並列；
    # EOS／生命週期也用實測查（CIA 寫 Windows、實測 RedHat 的那台不可以掛 Windows 的 EOS）。
    # CIA 欄位本身不動（hardware.os／os_cia 照匯入），這裡只決定顯示用哪個值。
    import collected_os
    import measured_mismatch
    _meas = collected_os.measured_map(conn).get(asset_serial)
    os_view = measured_mismatch.os_view(hw, _meas)
    ip_view = measured_mismatch.ip_view(hw, _meas)
    _hfs = measured_mismatch.hostname_fix_state(conn)
    hostname_view = measured_mismatch.hostname_view(hw, _hfs)
    ip_error_view = measured_mismatch.ip_error_view(hw, _hfs)
    # RVTools 證人（2026-10-20）：用 VM 名對主機名，CIA 正本不是 RVTools 匯的也看得到 vCenter 怎麼講
    rvtools = collected_os.rvtools_map(conn, [hw]).get(asset_serial)
    if rvtools:
        _rs = rvtools.get("resolved_serial")
        rvtools["other_record"] = _rs if _rs and _rs != asset_serial else None
        rvtools["other_same_machine"] = bool(_rs) and _rs in (ms.same_machine_serials(conn, asset_serial) or [])
    os_eos_hit = None
    os_info = None
    if os_view["effective"]:
        os_info = normalize.normalize_os(os_view["effective"], conn, hw.get("device_model"))
        os_eos_hit = eos.lookup_os_eos(os_info["canonical"])
    hw_eos_hit = None
    model_info = None
    if hw.get("device_model"):
        # ⚠️ 這裡之前漏傳 hint（資產名稱/資產用途），跟 eos_summary() 統計頁用的
        # 不是同一套邏輯——同一台 device_model 只填「(VM)」的資產，統計頁能靠
        # hint 解析出「HPE 3PAR 8400 storage」，這支查單一資產卻查不到，兩邊
        # 結果對不起來。使用者 2026-08-13 實際發現、補上。
        model_info = normalize.normalize_model(hw["device_model"], conn, _model_hint(hw))
        hw_eos_hit = eos.lookup_hardware_eos(model_info["canonical"])

    # 使用者 2026-08-13 要求：來源系統的 os／device_model 欄位常常空白或認不出來，
    # 而且這個系統對來源資料沒有編輯權限，改不了。這裡唯讀顯示「系統猜測」，
    # 不寫回任何欄位、也不影響上面的 os_eos／hardware_eos 查詢結果（那兩個本來
    # 就已經用了 hint，猜測欄只是把「這是猜的」這件事明講出來，讓人知道不是
    # 來源系統本身寫的）。
    os_guess = None
    if hw.get("asset_purpose") and (not os_view["effective"] or not (os_info and os_info["matched"])):
        os_guess = normalize.suggest_os_canonical(hw["asset_purpose"])
    # ⚠️ 使用者 2026-08-13 再次要求：不管是規則直接對到（method="rule"/"alias"，
    # 可信）還是靠 hint 猜的（"hint"/"hint-vendor"，僅供參考），都要顯示——
    # 之前只顯示「靠猜的」，理由是「規則直接對到的已經看得到不用重複」，但這樣
    # 使用者沒辦法分辨「系統沒查到」跟「已經查到、只是覺得不用顯示」，兩種空白
    # 長一樣。confirmed 旗標讓前端用不同樣式區分兩種狀況。
    model_guess = None
    model_guess_confirmed = False
    if model_info and model_info["method"] != "unmatched" \
            and model_info["canonical"] not in normalize._HW_VM_PRODUCTS:
        model_guess = model_info["canonical"]
        model_guess_confirmed = model_info["method"] in ("rule", "alias")

    # 體檢：清單頁的兩個燈在這裡展開成逐項明細（哪一項沒過、跟什麼比、下一步做什麼）。
    # 清單只夠回答「要不要看這台」，這裡才回答「那我到底要做什麼」。
    import health_check

    health = health_check.evaluate_all(conn).get(hardware["id"])

    # 業務系統：這台的 api_id 對到哪個系統（中文名／第幾類／AP 部門負責人）。
    # 三態要分得開（見 business_system 模組）：沒填代碼＝None；有代碼但對照表查無＝found:False；
    # 查到＝found:True。第幾類走 system_stats.class_from_label（原字串「S1-…」→「第一類」），
    # 手動覆寫（sys_class_override）優先於分級表匯入的 sys_class。
    biz = None
    _api = (hw.get("api_id") or "").strip()
    if _api:
        import system_stats
        _bs = conn.execute(
            "SELECT api_id, name, ap_department, ap_owner, sys_class, sys_class_override "
            "FROM business_system WHERE api_id = ?", (_api,),
        ).fetchone()
        if _bs:
            _computed, _core = system_stats.class_from_label(_bs["sys_class"])
            _ovr = (_bs["sys_class_override"] or "").strip()
            biz = {
                "found": True, "api_id": _api, "name": _bs["name"],
                "ap_department": _bs["ap_department"], "ap_owner": _bs["ap_owner"],
                "sys_class": _ovr or _computed, "sys_class_core": _core,
            }
        else:
            biz = {"found": False, "api_id": _api, "name": None,
                   "ap_department": None, "ap_owner": None,
                   "sys_class": system_stats.CLASS_UNRATED, "sys_class_core": False}

    # 主機規格（納管後收集）：CPU／RAM／Storage(依 VG/掛載分組)／PSU(實體機瓦特+數量)。
    # storage_json／psu_json 在這裡解成物件給前端；raw_json 不外送（量大、且詳細頁用不到）。
    _hs = conn.execute("SELECT * FROM host_spec WHERE asset_serial = ?", (asset_serial,)).fetchone()
    host_spec = None
    if _hs:
        import json as _json
        host_spec = dict(_hs)
        host_spec.pop("raw_json", None)
        for _k in ("storage_json", "psu_json", "spec_json"):
            try:
                host_spec[_k] = _json.loads(host_spec[_k]) if host_spec.get(_k) else None
            except (ValueError, TypeError):
                host_spec[_k] = None

    # NIC／HBA 明細（納管後收集）：每張卡一列，含目前+額定雙速率、降速旗標。
    try:
        nics = [dict(r) for r in conn.execute(
            "SELECT * FROM asset_nic WHERE asset_serial = ? ORDER BY nic_name", (asset_serial,)).fetchall()]
        hbas = [dict(r) for r in conn.execute(
            "SELECT * FROM asset_hba WHERE asset_serial = ? ORDER BY hba_name", (asset_serial,)).fetchall()]
    except sqlite3.Error:
        nics, hbas = [], []

    import host_kind
    _cls = asset_classify.classify_one(conn, asset_serial)
    # 這個 IP 有沒有 SAN switch 收集資料（2026-10-21 效能）：詳細頁原本對每一台都打
    # /api/san/switches/{ip}，非 SAN 的全部 404。這裡先講有沒有，前端只在有的時候才去抓明細。
    # 查不到表＝不知道（None），前端照舊去問，不可以因此把 SAN 分頁藏掉。
    try:
        san_collected = bool(hw.get("ip")) and conn.execute(
            "SELECT 1 FROM san_switch WHERE ip = ? LIMIT 1", (hw["ip"],)).fetchone() is not None
    except sqlite3.Error:
        san_collected = None
    return {
        "hardware": hw,
        # true＝這個 IP 收過 SAN switch 資料（SAN 分頁有東西）；false＝沒有；null＝查不到表、不知道
        "san_collected": san_collected,
        # 這台被分成哪一類，以及「是機器判的還是人搬的」。
        # 2026-10-13 使用者站在這一頁指著一台 EMC UnityXT480 NAS 問「是 storage
        # 你怎麼抓不出來」——這頁以前根本沒顯示大類，他看不到系統把它归成什麼，
        # 也無從搬。分類跟漏斗／統計共用同一支 asset_classify，不是這頁另判一次。
        "classify": _cls,
        # 網路設備：主機名後面放廠牌圖示（Cisco／HP／F5／其他），依據寫在提示（2026-10-21）
        "net_vendor": (host_kind.net_vendor_view(conn, hw.get("ip"))
                       if _cls and _cls.get("os_type") == "網路設備" else None),
        # 作業系統／IP：實測 vs CIA（measured_mismatch.os_view／ip_view）。basis=measured 時畫面主值是實測
        "os_view": os_view,
        "ip_view": ip_view,
        "hostname_view": hostname_view,
        "ip_error_view": ip_error_view,
        "rvtools": rvtools,
        # 同一台的其他登記（別名／多重登記，一台多個「外號」）；空＝這台只登記一筆
        "aliases": aliases,
        "vip_pools": vip_pools,
        # 業務系統對照（名稱／第幾類）：api_id 為空時為 null——畫面用它把代碼翻成看得懂的系統
        "business_system": biz,
        # 主機規格（收集）：CPU/RAM/Storage(VG分組)/PSU；沒收過為 null
        "host_spec": host_spec,
        # NIC／HBA 明細（收集）：清單，每張卡一列
        "nics": nics,
        "hbas": hbas,
        # MAC 系統校正（2026-10-21）：CIA MAC 不在任何實體網卡上 → 主要網卡 MAC；對得上／沒得比為 null
        "mac_view": _mac_view_of(conn, hw),
        # 「這台在哪個 vCenter 上」是很常追的問題。值本身在 hw["vi_sdk_server"]，
        # 這裡回的是**空白時為什麼空白**——三種原因完全不同，但都是 NULL：
        #   · 這台不是 RVTools 匯進來的（手動建／Excel／掃描發現）
        #   · 是 RVTools 匯的，但那份匯出沒有 VI SDK Server 欄（舊版 RVTools）
        #   · 是 RVTools 匯的，且匯入時間早於這個功能（2026-08-28 之前）
        # 只顯示空白，人會讀成「查過了，沒有」——那是第四種意思，而且是假的。
        "vi_sdk_server_note": _vi_sdk_note(conn, hw),
        # 虛擬化怎麼稱呼**由後端統一判**（manage_state.virt_kind）。前端自己判
        # 就是第二份定義，兩邊遲早不一致 -- 平台判定今天才剛因為手抄本吃過虧。
        "virt_kind": _virt_kind_of(hw),
        # 序號有兩個來源（facts 與 host_spec），以前各寫各的、沒有人對帳。
        # 攤開給畫面看：值、出處、有沒有互相矛盾（2026-09-23）。
        "serial_view": _serial_view(hw, host_spec),
        # 收集這一跳的 SSH 事實（離開碼／stderr／指令原文）＋一句從離開碼直接讀出的結論。
        # 使用者的規矩：診斷要做進畫面，不要叫他跑指令；換主機還會再遇到，要能重現。
        "collect_trace": _collect_trace_of(conn, asset_serial),
        "personnel": [dict(r) for r in personnel],
        "software": [dict(r) for r in software],
        # 掃到的安裝軟體（軟體盤點 host_package）——「軟體」分頁顯示這個
        "packages": [dict(r) for r in packages],
        "history": [dict(r) for r in history],
        "onboard_history": onboard_history,
        "collect_status": collect_status,
        # 退役資產不體檢，這時是 None——畫面顯示「已退役，不列入體檢」，
        # 不要假裝它全綠。
        "health": health,
        # 這台的軟硬體 EOS 狀態：查不到官方資料就是 null，不是 0 或空字串——
        # 前端要能分辨「查過沒公開日期」跟「還沒查」。
        "os_eos": ({**os_eos_hit, **_eos_assess_fields(os_eos_hit)}
                   if os_eos_hit else None),
        "hardware_eos": ({**hw_eos_hit, **_eos_assess_fields(hw_eos_hit)}
                          if hw_eos_hit else None),
        "os_guess": os_guess,
        "model_guess": model_guess,
        "model_guess_confirmed": model_guess_confirmed,
    }


@router.get("/api/normalize/pending")
def normalize_pending(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """列出規則跟別名字典都認不出來的原始值——EOS 頁「未分類」清單的資料來源，
    也是匯出待補 Excel 的資料來源。"""
    import normalize

    return normalize.pending_values(conn)


@router.post("/api/normalize/alias")
def upsert_normalize_alias(
    body: NormalizeAliasBody,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """人工補一筆「原始值 → 標準名稱」的對應。補完立刻生效——normalize.py 的
    _load_aliases() 每次都重新查表，沒有快取，不用重啟服務。"""
    import normalize

    if body.kind not in (normalize.KIND_OS, normalize.KIND_MODEL):
        raise HTTPException(400, f"kind 只接受 {normalize.KIND_OS} 或 {normalize.KIND_MODEL}")
    if not body.canonical.strip():
        raise HTTPException(400, "標準名稱不能是空的")
    conn.execute(
        "INSERT INTO normalize_alias (kind, raw_value, canonical, note) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(kind, raw_value) DO UPDATE SET canonical = excluded.canonical, note = excluded.note",
        (body.kind, body.raw_value, body.canonical.strip(), body.note),
    )
    conn.commit()
    return {"ok": True}


@router.post("/api/normalize/canonical-override")
def upsert_canonical_override(
    body: CanonicalOverrideBody,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """使用者 2026-08-13 要求：不管系統目前顯示的名稱是規則確認還是靠 hint 猜的，
    都能直接改成正確名稱，改完永遠照使用者的為準——跟 /api/normalize/alias
    不一樣：alias 是「原始字串 → 標準名」，只對還沒被規則收斂掉的原始值有用；
    這支是「系統目前算出來的 canonical → 使用者確認的正確 canonical」，對任何
    已經顯示出來的名稱都能用。補完立刻生效，normalize.py 每次都重新查表。
    """
    import normalize

    if body.kind not in (normalize.KIND_OS, normalize.KIND_MODEL):
        raise HTTPException(400, f"kind 只接受 {normalize.KIND_OS} 或 {normalize.KIND_MODEL}")
    if not body.new_canonical.strip():
        raise HTTPException(400, "新名稱不能是空的")
    conn.execute(
        "INSERT INTO normalize_canonical_override (kind, old_canonical, new_canonical, overridden_by) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(kind, old_canonical) DO UPDATE SET new_canonical = excluded.new_canonical, "
        "overridden_by = excluded.overridden_by, overridden_at = datetime('now','localtime')",
        (body.kind, body.old_canonical, body.new_canonical.strip(), session["username"]),
    )
    conn.commit()
    return {"ok": True}


@router.delete("/api/normalize/canonical-override/{kind}/{old_canonical}")
def delete_canonical_override(
    kind: str,
    old_canonical: str,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """撤銷改名，改回系統自動判斷的名稱。"""
    cur = conn.execute(
        "DELETE FROM normalize_canonical_override WHERE kind = ? AND old_canonical = ?",
        (kind, old_canonical),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "查無此筆覆寫")
    return {"ok": True}


@router.get("/api/normalize/alias")
def list_normalize_alias(
    kind: str | None = None,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """列出目前已經補過的別名，給使用者對照/複查用。"""
    if kind:
        rows = conn.execute(
            "SELECT id, kind, raw_value, canonical, note, created_at FROM normalize_alias "
            "WHERE kind = ? ORDER BY created_at DESC", (kind,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, kind, raw_value, canonical, note, created_at FROM normalize_alias "
            "ORDER BY created_at DESC",
        ).fetchall()
    return [dict(r) for r in rows]


@router.delete("/api/normalize/alias/{alias_id}")
def delete_normalize_alias(
    alias_id: int,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """撤銷一筆補錯的別名。"""
    cur = conn.execute("DELETE FROM normalize_alias WHERE id = ?", (alias_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "查無此筆別名")
    return {"ok": True}


@router.get("/api/normalize/pending/export")
def export_normalize_pending(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """匯出「查不到 EOS 的待補清單」成 Excel，讓人（或 AI）離線查證後填「標準名稱」欄，
    再用 /api/normalize/pending/import 匯入回來。兩個工作表對應 normalize_alias 的
    kind='os'／'device_model'，欄位設計成填完直接可以匯入（不用改欄名/欄序）。
    """
    import normalize

    pending = normalize.pending_values(conn)

    # os 待補清單也附「掛在哪些機型上」——跟 EOS 頁「未分類」分頁同一個線索，
    # 光看裸版本號查不出是什麼，機型是唯一能推斷的依據。
    os_device_models: dict[str, dict[str, int]] = {}
    for r in conn.execute(
        "SELECT os AS v, device_model AS m, asset_name, asset_purpose FROM hardware "
        "WHERE os IS NOT NULL AND os != ''"
    ):
        if r["m"] and not normalize.normalize_os(r["v"], conn, r["m"])["matched"]:
            bucket = os_device_models.setdefault(r["v"], {})
            # 這裡之前漏傳 hint，同一輪其他呼叫點都已經補上（2026-08-13 全面稽核）：
            # 這欄純粹是給人看的提示文字，沒有比對用途，漏了不會造成 0 筆這種功能性
            # 錯誤，但補上讓提示文字跟其他地方顯示的名稱一致。
            dm = normalize.normalize_model(r["m"], conn, _model_hint(r))["canonical"]
            bucket[dm] = bucket.get(dm, 0) + 1

    wb = Workbook()
    ws = wb.active
    ws.title = "作業系統韌體"
    ws.append(["kind", "原始值", "掛在哪些機型上", "出現次數", "標準名稱（填這欄）", "備註"])
    for item in pending[normalize.KIND_OS]:
        models = os_device_models.get(item["raw_value"], {})
        model_str = "、".join(m for m, _ in sorted(models.items(), key=lambda kv: -kv[1]))
        ws.append([normalize.KIND_OS, item["raw_value"], model_str, item["count"], "", ""])

    ws2 = wb.create_sheet("硬體型號")
    ws2.append(["kind", "原始值", "出現次數", "標準名稱（填這欄）", "備註"])
    for item in pending[normalize.KIND_MODEL]:
        ws2.append([normalize.KIND_MODEL, item["raw_value"], item["count"], "", ""])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"eos_pending_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/api/normalize/pending/import")
def import_normalize_pending(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """匯入補好的待補清單 Excel：逐列讀「標準名稱」欄，非空就補一筆別名；
    空白代表使用者還沒查到，正常跳過，不算錯誤（不是每一列都必須這次補完）。
    """
    import normalize

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 檔案")

    contents = file.file.read()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    applied = 0
    skipped = 0
    errors: list[str] = []
    try:
        try:
            wb = load_workbook(tmp_path, read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - openpyxl對壞檔案的錯誤型態不一，統一攔截如實回報
            raise HTTPException(400, f"匯入失敗，請確認檔案格式：{exc}") from exc

        for ws in wb.worksheets:
            rows = ws.iter_rows(values_only=True)
            header = next(rows, None)
            if not header:
                continue
            col = {str(h).strip(): i for i, h in enumerate(header) if h is not None}
            required = {"kind", "原始值", "標準名稱（填這欄）"}
            if not required.issubset(col.keys()):
                continue  # 不是這個匯出格式產生的工作表，跳過不當錯誤
            for row in rows:
                if row is None or all(c is None for c in row):
                    continue
                kind = row[col["kind"]]
                raw_value = row[col["原始值"]]
                canonical = row[col["標準名稱（填這欄）"]]
                note = row[col["備註"]] if "備註" in col else None
                if not canonical or not str(canonical).strip():
                    skipped += 1
                    continue
                if kind not in (normalize.KIND_OS, normalize.KIND_MODEL) or not raw_value:
                    errors.append(f"這列 kind/原始值格式不對，略過：{row}")
                    continue
                conn.execute(
                    "INSERT INTO normalize_alias (kind, raw_value, canonical, note) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(kind, raw_value) DO UPDATE "
                    "SET canonical = excluded.canonical, note = excluded.note",
                    (str(kind), str(raw_value), str(canonical).strip(),
                     str(note).strip() if note else None),
                )
                applied += 1
        wb.close()  # read_only 模式的 workbook 會一直握著檔案控制代碼，Windows 上
        # 不先關掉就刪暫存檔會噴 PermissionError（檔案仍被本行程佔用）——2026-08-12 實測踩到。
        conn.commit()
    finally:
        tmp_path.unlink(missing_ok=True)

    return {"applied": applied, "skipped": skipped, "errors": errors}


@router.post("/api/assets/scope-preview")
def asset_scope_preview(
    body: ScopePreviewBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """[B-08] 動作前預覽：「這個動作會改到這 N 筆」。scope=machine 展開成整台。"""
    import manage_state
    if body.scope not in ("single", "machine"):
        raise HTTPException(400, "scope 只能是 single 或 machine")
    serials = ([s for s in dict.fromkeys(body.serials) if s] if body.scope == "single"
               else manage_state.expand_to_machines(conn, body.serials))
    rows = manage_state.preview_rows(conn, serials)
    return {"scope": body.scope, "count": len(rows), "rows": rows}


@router.post("/api/assets/batch-status")
def batch_asset_status(
    body: BatchStatusBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """一次把多台資產改成停用／報廢／閒置（或改回使用中），每台記一筆 CIA 待異動。
    使用者 2026-09-15：「我有抓到一些設備已下線，我要怎麼變更」。原因必填。見 cia_pending。"""
    import cia_pending

    try:
        return cia_pending.batch_set_status(conn, body.serials, body.status, body.reason,
                                            session["username"] if session else None, scope=body.scope)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/cia-pending")
def cia_pending_list(
    include_synced: bool = False,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """系統裡改了、CIA 清冊還沒改的東西（預設只列還沒同步的）。"""
    import cia_pending

    items = cia_pending.list_changes(conn, include_synced)
    return {"items": items, "open": sum(1 for i in items if not i["synced_at"])}


@router.post("/api/cia-pending/synced")
def cia_pending_synced(
    body: CiaSyncedBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """使用者回報 CIA 清冊已改好。只標記、不刪（稽核軌跡）。"""
    import cia_pending

    return {"marked": cia_pending.mark_synced(conn, body.ids, session["username"] if session else None)}


@router.get("/api/cia-pending/export")
def cia_pending_export(
    include_synced: bool = False,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """CIA 待異動匯出 Excel，拿去改 CIA 清冊用。"""
    import cia_pending
    from urllib.parse import quote

    wb = Workbook()
    ws = wb.active
    ws.title = "CIA待異動"
    ws.append(cia_pending.EXPORT_HEADERS)
    for r in cia_pending.export_rows(conn, include_synced):
        ws.append(["" if v is None else v for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"CIA待異動_{_now_local()[:10]}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


@router.put("/api/assets/{asset_serial}")
def update_asset(
    asset_serial: str,
    body: AssetUpdateBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """修改一台資產的欄位。

    在這之前系統**完全沒有編輯功能**——資料一旦進來就只能靠重新匯入 Excel 覆蓋，
    連打錯一個字都要重跑匯入。盤點系統的資料本來就會被持續修正，這是必要缺口。

    只接受 hardware 真實存在的欄位（擋掉亂塞）；asset_serial 是主鍵不給改
    （改序號等於換一台，會弄丟 personnel/software 的關聯）。空字串一律存成 NULL，
    否則「空」會有 '' 和 NULL 兩種寫法，篩選和排序就會分岔。
    """
    if conn.execute("SELECT 1 FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone() is None:
        raise HTTPException(404, "查無此資產")

    cols = _sortable_columns(conn, "hardware")
    unknown = [k for k in body.fields if k not in cols]
    if unknown:
        raise HTTPException(400, f"不支援的欄位：{', '.join(sorted(unknown))}")

    cleaned = {k: (None if v == "" else v) for k, v in body.fields.items()}
    # 2026-10-20 使用者：資產狀態只有 CIA 可以寫（見 lifecycle.py）。這裡收到資產狀態
    # **不寫 asset_status**，改記「待 CIA 異動」（徽章＋可匯出給 CIA），不動任何台數。
    # 待異動欄位也不給直接改（只能走這條路，才一定有 CIA 待異動紀錄）。
    for _k in lifecycle.PENDING_COLS:
        cleaned.pop(_k, None)
    pending_changed = False
    if "asset_status" in cleaned:
        want = cleaned.pop("asset_status")
        _row = conn.execute("SELECT asset_status, pending_status FROM hardware WHERE asset_serial = ?",
                            (asset_serial,)).fetchone()
        if _row is None:
            raise HTTPException(404, f"查無此資產：{asset_serial}")
        _by = session["username"] if session else None
        if lifecycle.set_pending(conn, asset_serial, want, "資產詳細頁編輯", _by):
            pending_changed = True
            import cia_pending

            if lifecycle.is_from_cia(asset_serial):
                cia_pending.record(conn, asset_serial, "asset_status", _row[1] or _row[0], want,
                                   "資產詳細頁編輯", _by, commit=False)
            conn.commit()
    changed = update_hardware(conn, asset_serial, cleaned) if cleaned else 0
    changed = changed or pending_changed
    # 只有「人動過」才更新 manual_updated_at。updated_at 每次自動匯入都會被刷新
    # （dynassets_import 會寫），拿它當「有沒有人在維護」的指標，跑一次匯入就全部變新鮮，
    # 那個數字是假的（2026-08-15 自我檢查發現）。這欄是資料品質新鮮度的唯一依據。
    if changed:
        conn.execute(
            "UPDATE hardware SET manual_updated_at = ? WHERE asset_serial = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), asset_serial),
        )
        conn.commit()
    row = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
    return {"updated_fields": changed and len(cleaned) or 0, "hardware": dict(row)}


@router.post("/api/assets/manual")
def create_asset_manual(
    body: AssetCreateBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """手動新增一筆資產（CIA 資產清單欄位為主，見 field_mapping.json「硬體」）。

    在這之前系統唯一的建立資產方式是批次匯入（Excel/RVTools/dynassets）——臨時要
    單獨掛一台機器（例如先裝 Push Agent 才發現主機還沒登記），沒有比重跑整份匯入
    更輕的路可走。欄位規則跟 update_asset 共用（白名單、空字串轉 NULL）；
    asset_serial 必填且要唯一（跟批次匯入的 upsert 不同：手動新增遇到重複序號直接
    擋下，不靜默覆蓋既有資產）。

    2026-08-15 起這裡同時是「主機及網路異動需求單」的轉錄入口（provision）。兩種來源
    合併成同一支 API、同一份欄位：申請單位還沒有系統帳號，短期只能填 Word 交給 IT
    承辦人轉錄；等哪天開放讓他們自己上系統填，也只是「同一頁換一個人來填」，不用重做。
    送出後資產是「待上線」，要過上線前檢查表才會變成「使用中」（見 golive.py）。
    """
    asset_serial = str(body.fields.get("asset_serial") or "").strip()
    if not asset_serial:
        raise HTTPException(400, "資產序號必填")
    # 帳外前綴（DYN-/VC-/AUTO-）是系統給「掃到/vCenter 但沒登記」的資產用的——人工新增的
    # 是正式登記，用到這些前綴會被全站統計當成帳外（不計在管台），是資料錯。擋下（2026-09-19）。
    import system_report as _sr_new
    if _sr_new.is_off_book(asset_serial):
        raise HTTPException(
            400, f"資產序號不可用系統保留前綴 {'/'.join(_sr_new.OFF_BOOK_PREFIXES)}開頭"
                 "（那是給掃描/vCenter 帳外資產用的，人工登記請用正式序號）")
    if conn.execute("SELECT 1 FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone():
        raise HTTPException(409, f"資產序號 {asset_serial} 已存在")

    cols = _sortable_columns(conn, "hardware")
    unknown = [k for k in body.fields if k not in cols]
    if unknown:
        raise HTTPException(400, f"不支援的欄位：{', '.join(sorted(unknown))}")

    # 只擋「使用中」的撞號——已停用/報廢/閒置的資產，IP 合法可能被重新分配，
    # 連這種正常情境都擋死只會逼使用者硬改資料才過得了關，防呆變成擋路。
    ip = str(body.fields.get("ip") or "").strip()
    if ip:
        conflict = conn.execute(
            "SELECT asset_serial, hostname FROM hardware WHERE ip = ? "
            "AND COALESCE(asset_status, '') NOT IN " + lifecycle.retired_sql_list(),
            (ip,),
        ).fetchone()
        if conflict:
            raise HTTPException(
                409,
                f"IP {ip} 已經被使用中的資產 {conflict['asset_serial']}"
                f"（{conflict['hostname'] or '未填主機名稱'}）使用，請確認是不是同一台",
            )

    cleaned = {k: (None if v == "" else v) for k, v in body.fields.items() if k != "id"}
    cleaned["asset_serial"] = asset_serial
    # 沒指定狀態就是「待上線」；使用者自己選了狀態就尊重他（例如補登記一台早就在跑的機器）
    if not cleaned.get("asset_status"):
        cleaned["asset_status"] = PENDING_GOLIVE_STATUS
    # 退役狀態只有 CIA 匯入可以寫（2026-10-20 使用者，見 lifecycle.py）——手動新增不收
    if lifecycle.is_retired_value(cleaned.get("asset_status")):
        raise HTTPException(400, "停用／報廢／閒置只能由 CIA 清冊匯入；系統裡要記下線請用「待 CIA 異動」")
    insert_hardware(conn, **cleaned)

    p = body.provision or ProvisionInfo()
    if p.source == "form" and not (p.request_no or "").strip():
        raise HTTPException(400, "依申請單轉錄時，單據編號必填")
    conn.execute(
        "INSERT INTO provision_request "
        "(asset_serial, source, request_no, applicant_unit, applicant, unit_manager, "
        " form_date, change_kind, raw_fields, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            asset_serial, p.source, p.request_no, p.applicant_unit, p.applicant,
            p.unit_manager, p.form_date, p.change_kind,
            json.dumps(p.raw_fields, ensure_ascii=False) if p.raw_fields else None,
            session["username"],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()

    # 一併開一份上線檢查表，並先跑一次 auto 判定（機器測得到的先填好，人只處理剩下的）
    golive.ensure_check(conn, asset_serial)
    golive.refresh_auto_results(conn, asset_serial)

    row = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
    return {"hardware": dict(row), "golive": golive.get_check_detail(conn, asset_serial)}


class RackDeviceBody(BaseModel):
    """機櫃畫面上「在這個機櫃新增設備」的表單（2.007.17 使用者：「機櫃上增加，可手動增加嗎」）。"""
    room: str
    rack: str
    u_start: int
    u_end: int | None = None
    asset_serial: str = ""          # 2026-10-28：可以留空（新設備 CIA 還沒給編號）→ 系統給暫時編號 NEW-<IP或主機名>
    asset_name: str | None = None
    device_model: str | None = None
    hw_serial: str | None = None
    hostname: str | None = None
    ip: str | None = None
    api_id: str | None = None        # 2026-10-29：所屬系統（業務系統代碼；必須是已有的）
    mgmt_ip: str | None = None       # 2026-10-29：管理 IP（iDRAC／iLO…）——另建一筆「<主機名>-<種類>」管理卡，機櫃上併進這台的面板
    mgmt_kind: str | None = None
    confirm_overlap: bool = False


_MGMT_KINDS = {"idrac": "iDRAC", "ilo": "iLO", "ilom": "ILOM", "imm": "IMM", "xcc": "XCC", "bmc": "BMC"}


@router.post("/api/racks/add-device")
def rack_add_device(
    body: RackDeviceBody,
    request: Request,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """在指定機櫃新增一台設備：機房／機櫃／U 位由表單帶入，其餘走手動新增同一套規則
    （序號必填且唯一、IP 不可撞使用中資產、資產狀態「待上線」、開上線檢查表、留申請紀錄）。
    U 位跟現有設備重疊：回 409＋重疊清單，確認後帶 confirm_overlap 再送（跟位置更正一樣只警告、不硬擋）。
    只有管理員可用、要過寫入防護（跟預留 U 位同一套）。純資料庫寫入，不連目標機。"""
    import rack_layout
    import write_guard
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以在機櫃新增設備")
    try:
        write_guard.check(conn, request)
    except write_guard.TooManyFails as exc:
        raise HTTPException(429, str(exc))
    except write_guard.NeedPassword as exc:
        raise HTTPException(428, str(exc))
    room, rack = body.room.strip(), body.rack.strip()
    u1, u2 = body.u_start, body.u_end or body.u_start
    if not room or not rack:
        raise HTTPException(400, "機房和機櫃都要有")
    if not (1 <= u1 <= 60) or not (1 <= u2 <= 60) or u2 < u1:
        raise HTTPException(400, "U 位要在 1～60，且迄 U 不可小於起 U")
    import model_naming
    try:
        model = model_naming.normalize(conn, body.device_model)       # 機型要寫成「廠牌 型號」（例：IBM S922）；整理空白／換行、廠牌統一寫法
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    try:
        hw_serial = model_naming.normalize_serial(conn, model, body.hw_serial)      # 序號：去空白連字號、大寫、依廠牌檢查碼數
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    api_id = (body.api_id or "").strip() or None
    if api_id and not conn.execute("SELECT 1 FROM business_system WHERE api_id = ?", (api_id,)).fetchone():
        raise HTTPException(400, f"系統代碼「{api_id}」不在業務系統清單裡，請從建議清單選（或留空）")
    mgmt_ip = (body.mgmt_ip or "").strip()
    mgmt_host = None
    if mgmt_ip:
        import ipaddress
        try:
            ipaddress.ip_address(mgmt_ip)
        except ValueError:
            raise HTTPException(400, f"管理 IP 不是有效的 IP：{mgmt_ip}")
        if not (body.hostname or "").strip():
            raise HTTPException(400, "填了管理 IP，主機名要填（管理卡用「主機名-iDRAC」這種名字併進這台）")
        kind = _MGMT_KINDS.get((body.mgmt_kind or "idrac").strip().lower())
        if not kind:
            raise HTTPException(400, f"管理介面種類要是：{'、'.join(_MGMT_KINDS.values())}")
        if mgmt_ip == (body.ip or "").strip():
            raise HTTPException(400, "管理 IP 不能跟主機 IP 一樣")
        if ipaddress.ip_address(mgmt_ip).is_unspecified:
            raise HTTPException(400, "管理 IP 不能是 0.0.0.0")
        mgmt_host = f"{body.hostname.strip()}-{kind}"
        if conn.execute("SELECT 1 FROM hardware WHERE lower(hostname) = lower(?) AND COALESCE(asset_status, '') NOT IN "
                        + lifecycle.retired_sql_list(), (mgmt_host,)).fetchone():
            raise HTTPException(409, f"已經有使用中的資產叫「{mgmt_host}」，管理卡會被併成同一台，請改主機名或先確認")
        if conn.execute("SELECT 1 FROM hardware WHERE asset_serial = ?", (f"NEW-{mgmt_ip}",)).fetchone():
            raise HTTPException(409, f"管理 IP {mgmt_ip} 已經建過管理卡（NEW-{mgmt_ip}）")
        used = conn.execute("SELECT asset_serial, hostname FROM hardware WHERE ip = ? AND COALESCE(asset_status, '') NOT IN "
                            + lifecycle.retired_sql_list(), (mgmt_ip,)).fetchone()
        if used:
            raise HTTPException(409, f"管理 IP {mgmt_ip} 已經被使用中的資產 {used['asset_serial']}（{used['hostname'] or '未填主機名稱'}）使用")
    import blast_radius
    locs: dict[str, int] = {}
    for r in conn.execute("SELECT physical_location AS loc, COUNT(*) AS n FROM hardware "
                          "WHERE COALESCE(physical_location,'') <> '' GROUP BY physical_location"):
        if blast_radius.rack_room(r["loc"]) == room:
            locs[r["loc"]] = r["n"]
    if not locs:
        raise HTTPException(400, f"找不到機房「{room}」的既有登記，無法決定實體位置")
    location = max(locs, key=locs.get)   # 同一機房有好幾種寫法時用最多的那種
    if not body.confirm_overlap:
        hit = rack_layout.collisions(rack_layout._machines(conn), "", room, rack, u1, u2)
        if hit:
            raise HTTPException(409, {"overlap": hit, "message": "這段 U 位已有設備登記"})
    serial = (body.asset_serial or "").strip()
    if not serial:
        # CIA 還沒給資產序號的新設備：用暫時編號 NEW-<IP>（沒有 IP 就用主機名）。主機名一定要填——
        # 全站「同一台」靠主機名（沒有主機名會退回用序號，之後 CIA 補登同一台就會被算成兩台）。
        host = (body.hostname or "").strip()
        if not host:
            raise HTTPException(400, "資產序號留空時，主機名要填（CIA 補登後才能自動認成同一台）")
        serial = f"NEW-{(body.ip or '').strip() or host}"
    fields = {
        "asset_serial": serial, "physical_location": location,
        "rack_no": f"{rack}/{u1}-{u2}U" if u2 != u1 else f"{rack}/{u1}U",
        "asset_name": body.asset_name, "device_model": model,
        "hw_serial": hw_serial or None, "hostname": body.hostname, "ip": body.ip, "api_id": api_id,
    }
    fields = {k: (v.strip() if isinstance(v, str) else v) for k, v in fields.items()}
    out = create_asset_manual(
        AssetCreateBody(fields=fields, provision=ProvisionInfo(source="direct", change_kind="機櫃新增")),
        session, conn)
    if mgmt_host:
        # 管理卡：跟主機同一段 U、同機型，名字「主機名-iDRAC」，機櫃圖會自動併進這台的面板（不是第二台）
        mfields = {"asset_serial": f"NEW-{mgmt_ip}", "physical_location": location, "rack_no": fields["rack_no"],
                   "device_model": model, "hostname": mgmt_host, "ip": mgmt_ip, "api_id": api_id}
        try:
            out["mgmt"] = create_asset_manual(
                AssetCreateBody(fields=mfields, provision=ProvisionInfo(source="direct", change_kind="機櫃新增（管理卡）")),
                session, conn)["hardware"]
        except HTTPException as exc:       # 主機已建好：不丟錯（重送會變「序號已存在」），把管理卡失敗原因回給畫面
            out["mgmt_error"] = f"主機已新增，但管理卡沒建成：{exc.detail}"
    return out


@router.get("/api/models/options")
def model_options(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """新增設備「機型」欄的建議清單：現有資料裡寫法合格的機型（廠牌 型號），依台數多到少。
    寫法不合格的（DELL 全大寫、換行、只寫廠牌…）不列，也不在這裡改既有資料。"""
    import model_naming
    cnt: dict[str, int] = {}
    for r in conn.execute("SELECT device_model AS m, COUNT(*) AS n FROM hardware "
                          "WHERE COALESCE(device_model,'') <> '' GROUP BY device_model"):
        try:
            std = model_naming.normalize(conn, r["m"])
        except ValueError:
            continue
        cnt[std] = cnt.get(std, 0) + r["n"]
    items = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:200]
    return {"items": [{"model": m, "count": n} for m, n in items], "example": model_naming.EXAMPLE}


@router.get("/api/models/cleanup-preview")
def model_cleanup_preview(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """既有資料的機型整理成「廠牌 型號」前後對照（只看、不改資料；改不改由人決定）。"""
    import model_naming
    return model_naming.cleanup_preview(conn)


class ModelVendorsBody(BaseModel):
    vendors: list[dict]


@router.get("/api/model-vendors")
def model_vendors_get(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """機型「廠牌」清單（系統設定 → 機型廠牌）。can_edit＝這個人能不能改。"""
    import model_naming
    return {"vendors": model_naming.load_list(conn), "example": model_naming.EXAMPLE,
            "can_edit": _can_edit_nav(session, conn)}


@router.put("/api/model-vendors")
def model_vendors_put(
    body: ModelVendorsBody,
    request: Request,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """存廠牌清單（限管理員、過寫入防護、留操作紀錄）。只影響「以後新增設備」的機型檢查，不改既有資料。"""
    import activity
    import model_naming
    import write_guard
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員可以改機型廠牌清單")
    try:
        write_guard.check(conn, request)
    except write_guard.TooManyFails as exc:
        raise HTTPException(429, str(exc))
    except write_guard.NeedPassword as exc:
        raise HTTPException(428, str(exc))
    before = [x["name"] for x in model_naming.load_list(conn)]
    try:
        out = model_naming.save_list(conn, body.vendors)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    after = [x["name"] for x in out]
    add, rm = [n for n in after if n not in before], [n for n in before if n not in after]
    try:
        activity.log(conn, username=session["username"] if session is not None else "（登入停用）", ip=None, method="PUT",
                     path="/api/model-vendors", status=200, duration_ms=0,
                     action="機型廠牌清單：" + (f"新增 {'、'.join(add)}；" if add else "") + (f"移除 {'、'.join(rm)}；" if rm else "") + f"共 {len(after)} 個")
    except Exception as exc:  # noqa: BLE001 - 操作紀錄寫不進去不擋設定；印出來留痕
        print(f"[model-vendors] activity log failed: {exc}")
    return {"vendors": out}


@router.get("/api/assets/manual/field-options")
def manual_field_options(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """手動新增表單用：哪些欄位該做成選單，選項是什麼——從現有資料算，不寫死。

    判斷依據：這欄「現有資料的種類數」夠少（<=30）就代表是類別型欄位（處別/部門/
    環境別這種），做成選單讓使用者選、不能亂打；種類數太多（型號/用途/IP 這種近乎
    每筆都不同）維持自由輸入，選單塞不下也沒意義。門檻是資料驅動的，欄位新增/資料
    累積了自然會跟著變，不用另外維護一份清單。

    保管者／使用者是人名，例外處理：不用 hardware 表自己的歷史值當選項（那份本來就
    可能有錯字——同一個人被打成兩三種寫法，選單只是把錯字原封不動搬進選項，防呆
    等於沒防），改用 personnel 表（人員維護，Excel「人員」分頁匯入的那份）的
    person_name 當唯一真相來源，且不受種類數上限限制（人員名冊本來就可能超過 30 人）。
    """
    _PERSON_NAME_FIELDS = ("custodian", "user_name")

    fields = [
        f for f in _table_columns(conn, "hardware")
        if f not in ("id", "asset_serial", *_PERSON_NAME_FIELDS)
    ]
    options: dict[str, list[str]] = {}
    for field in fields:
        rows = conn.execute(
            f"SELECT DISTINCT {field} FROM hardware "
            f"WHERE {field} IS NOT NULL AND TRIM({field}) != '' "
            f"ORDER BY {field} LIMIT {_MANUAL_OPTIONS_MAX_DISTINCT + 1}"
        ).fetchall()
        values = [str(r[0]) for r in rows]
        if 1 <= len(values) <= _MANUAL_OPTIONS_MAX_DISTINCT:
            options[field] = values

    person_rows = conn.execute(
        "SELECT DISTINCT person_name FROM personnel "
        "WHERE person_name IS NOT NULL AND TRIM(person_name) != '' ORDER BY person_name"
    ).fetchall()
    person_names = [str(r[0]) for r in person_rows]
    if person_names:
        for field in _PERSON_NAME_FIELDS:
            options[field] = person_names
    return options


@router.get("/api/assets/{asset_serial}/tags")
def asset_tags_get(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """這台的標籤＋系統建議（建議一定附依據，而且不會自己存進資料）。"""
    import asset_tags

    if asset_tags.machine_of(conn, asset_serial) is None:
        raise HTTPException(404, f"查無資產序號 {asset_serial}")
    return {
        "tags": asset_tags.list_tags(conn, asset_serial),
        "suggestions": asset_tags.suggest(conn, asset_serial),
        "known_tags": asset_tags.all_tags(conn),      # 給自動完成用：別人已經用過哪些字
        "registrations": len(asset_tags.rows_of_machine(conn, asset_serial)),
    }


@router.post("/api/assets/{asset_serial}/tags")
def asset_tags_add(
    asset_serial: str,
    body: AssetTagBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """貼一個標籤（同名再貼＝更新備註）。標籤掛在「台」上，不是掛在這一筆登記。"""
    import asset_tags

    try:
        return asset_tags.add_tag(conn, asset_serial, body.tag, body.note,
                                  session["username"] if session else None, body.source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/api/assets/{asset_serial}/tags/{tag}")
def asset_tags_remove(
    asset_serial: str,
    tag: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import asset_tags

    try:
        return asset_tags.remove_tag(conn, asset_serial, tag)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/asset-tags/batch")
def asset_tags_batch(
    body: AssetTagBatchBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """一次幫很多台貼同一個標籤（2-1 勾選列、批次納管失敗的那幾台）。每列回結果；
    沒登記的回「未登記，無法加標籤」，不靜默略過。"""
    import activity
    import asset_tags

    if len(body.items) > 5000:
        raise HTTPException(400, "一次最多 5000 列")
    try:
        out = asset_tags.add_tag_batch(conn, body.items, body.tag, body.note,
                                       session["username"] if session else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        activity.log(conn, username=session["username"] if session else None, ip=None, method="POST",
                     path="/api/asset-tags/batch", status=200, duration_ms=0,
                     action=f"批次加標籤「{out['tag']}」：成功 {out['ok']}、未成功 {out['failed']}")
    except Exception:  # noqa: BLE001 - 操作紀錄寫不進去不擋標籤本身
        pass
    return out


@router.post("/api/asset-tags/by-serial")
def asset_tags_by_serial(
    body: SerialListBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """這批資產序號各自的標籤（2-1 表格的「標籤」欄，一次查回來）＋全站已用過的標籤（自動完成）。"""
    import asset_tags

    if len(body.serials) > 20000:
        raise HTTPException(400, "一次最多 20000 筆")
    return {"by_serial": asset_tags.tags_by_serial(conn, body.serials),
            "known_tags": asset_tags.all_tags(conn)}


@router.get("/api/asset-tags")
def asset_tags_all(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """全站有哪些標籤、各幾台。"""
    import asset_tags

    return {"items": asset_tags.all_tags(conn)}


@router.get("/api/assets/{asset_serial}/documents")
def asset_documents(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """這台資產的單據史（申請單、上線檢查表），含兩種單互相對應的關聯。"""
    import doc_import

    return {
        "documents": doc_import.documents_of_asset(conn, asset_serial),
        # 一台機器常有多張單（新增→異動→異動）：「當初申請多少」跟「現在應該多少」
        # 是兩個問題，時間軸兩個都答得出來
        "timeline": doc_import.asset_timeline(conn, asset_serial),
    }


@router.get("/api/assets/{asset_serial}/provision")
def get_provision(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """這台資產的申請單資訊（沒有就回 null，直接新增的資產也算沒有紙本單）。"""
    row = conn.execute(
        "SELECT * FROM provision_request WHERE asset_serial = ? ORDER BY id DESC LIMIT 1",
        (asset_serial,),
    ).fetchone()
    if row is None:
        return {"provision": None}
    d = dict(row)
    if d.get("raw_fields"):
        try:
            d["raw_fields"] = json.loads(d["raw_fields"])
        except ValueError:
            pass  # 存進去的不是合法 JSON 就原字串回去，不要讓整頁掛掉
    return {"provision": d}


@router.post("/api/assets/{asset_serial}/provision-attachment")
def upload_provision_attachment(
    asset_serial: str,
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """上傳簽核用的申請單（Word/PDF 掃描檔）。存原始檔，不解析。

    刻意不做 Word 解析自動建資產：那種表是巢狀表格＋□/☑ 符號，版面一動就抓歪，
    而且抓錯不會報錯，是「安靜把錯資料寫進盤點清單」——比沒有還糟。
    """
    row = conn.execute(
        "SELECT id FROM provision_request WHERE asset_serial = ? ORDER BY id DESC LIMIT 1",
        (asset_serial,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"{asset_serial} 沒有申請單紀錄")

    name = Path(file.filename or "attachment").name
    if Path(name).suffix.lower() not in (".doc", ".docx", ".pdf", ".png", ".jpg", ".jpeg"):
        raise HTTPException(400, "只接受 Word／PDF／圖片掃描檔")
    # 檔名帶 asset_serial 與流水號，避免不同資產的同名檔互相覆蓋
    safe = f"{asset_serial}_{row['id']}_{name}"
    path = _attachment_dir() / safe
    path.write_bytes(file.file.read())

    conn.execute(
        "UPDATE provision_request SET attachment_name = ?, attachment_path = ? WHERE id = ?",
        (name, str(path), row["id"]),
    )
    conn.commit()
    return {"attachment_name": name}


@router.get("/api/assets/{asset_serial}/provision-attachment-file")
def download_provision_attachment(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """下載申請單的簽核掃描檔。存了卻打不開等於沒存（2026-08-15 自我檢查補上）。"""
    row = conn.execute(
        "SELECT attachment_name, attachment_path FROM provision_request "
        "WHERE asset_serial = ? AND attachment_path IS NOT NULL ORDER BY id DESC LIMIT 1",
        (asset_serial,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "這台資產沒有申請單附件")
    path = Path(row["attachment_path"])
    if not path.exists():
        raise HTTPException(410, f"紀錄還在，但原始檔不見了：{row['attachment_name']}")
    return FileResponse(path, filename=row["attachment_name"])


@router.get("/api/assets/manual/field-groups")
def manual_field_groups(session: sqlite3.Row = Depends(require_auth)):
    """手動新增表單的欄位分組（見 manual_form_groups.json）——29 個欄位攤平成一片沒人填得下去。

    注意跟 field_groups.json 不是同一回事：那份是「資產查詢頁先顯示哪些欄位」的常用/進階
    分層，這份是「新增表單怎麼分段排版」。兩者的分法本來就不同，不要合併。

    也獨立於 field_mapping.json：那份是「Excel 標題 → 欄位名」，匯入/匯出都靠它，
    分組硬塞進去要改 value 的型別，會一次打壞 excel_import、匯出表頭與匯入設定頁。
    這裡回傳的 key 是資料庫欄位名，前端拿去對 field_mapping 算出來的欄位清單即可。

    設定檔壞掉/讀不到不讓整頁掛掉：回空清單，前端會退回原本的單一格子排版。
    """
    path = _BACKEND_DIR / "manual_form_groups.json"
    try:
        return {"groups": json.loads(path.read_text(encoding="utf-8"))["groups"]}
    except (OSError, ValueError, KeyError):
        return {"groups": []}


@router.get("/api/os-catalog")
def os_catalog(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """手動新增表單的作業系統欄位：分層選單（大類 → 發行版／產品 → 版本），不能亂打。

    OS 原始字串種類太多（正式環境 250+ 種），沒辦法像其他欄位一樣直接攤平成一個選單；
    但也不用另外維護一份分類表——直接復用既有、已經測過的 normalize_os()／os_family()
    （EOS 頁、儀表板平台統計都在用同一套），對現有資料裡每個不同的 os 字串分類一次，
    照 (大類, 發行版) 分組，組內選項是算好的 canonical 字串（使用者選到的就是標準名，
    不會把舊資料的雜亂寫法帶進新資產）。認不出來的字串歸進「其他／未分類」，不會憑空消失。
    """
    import normalize

    rows = conn.execute(
        "SELECT DISTINCT os FROM hardware WHERE os IS NOT NULL AND TRIM(os) != ''"
    ).fetchall()

    tree: dict[str, dict[str, set[str]]] = {}
    for r in rows:
        raw = r["os"]
        result = normalize.normalize_os(raw, conn)
        canonical = result["canonical"] or str(raw).strip()
        product = result["product"]
        family, linux_distro = normalize.os_family(product, canonical)
        distro_key = linux_distro if family == "Linux" else (product or "未分類")
        tree.setdefault(family, {}).setdefault(distro_key, set()).add(canonical)

    return {
        family: {distro: sorted(versions) for distro, versions in sorted(distros.items())}
        for family, distros in sorted(tree.items())
    }


@router.post("/api/assets/scan/discover")
def scan_discover(
    body: ScanDiscoverBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """網段存活掃描：回傳掃到的主機清單（機器事實而已），不寫資料庫。
    使用者從結果裡勾選要納入的主機，再呼叫 /api/assets/scan/import 才真的建資產。
    """
    try:
        network = ipaddress.ip_network(body.cidr.strip(), strict=False)
    except ValueError:
        raise HTTPException(400, "不是合法的網段格式（例：10.99.1.0/24 或單一 IP）")
    _assert_private_network(network)
    if network.num_addresses > _SCAN_MAX_HOSTS:
        raise HTTPException(400, f"網段太大（上限 {_SCAN_MAX_HOSTS} 個位址），請縮小範圍")

    try:
        proc = subprocess.run(
            ["nmap", "-sT", "-p", _SCAN_PORTS, "--open", "-oG", "-", str(network)],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        raise HTTPException(500, "系統未安裝 nmap，無法掃描")
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "掃描逾時，請縮小網段範圍再試")

    existing = {
        r["ip"]: r["asset_serial"]
        for r in conn.execute("SELECT ip, asset_serial FROM hardware WHERE ip IS NOT NULL")
    }
    found = []
    for line in proc.stdout.splitlines():
        m = re.match(r"Host:\s+(\S+)\s+\(([^)]*)\)\s+Ports:\s+(.*?)(?:\s+Ignored State:|$)", line)
        if not m:
            continue
        ip, rdns, ports_str = m.groups()
        open_ports = [p.split("/")[0] for p in ports_str.split(",") if "/open/" in p]
        if not open_ports:
            continue
        hostname = rdns or None
        if not hostname:
            try:
                hostname = socket.gethostbyaddr(ip)[0]
            except (socket.herror, socket.gaierror, OSError):
                hostname = None
        found.append({
            "ip": ip, "hostname": hostname, "open_ports": open_ports,
            "already_registered": ip in existing,
            "existing_asset_serial": existing.get(ip),
        })
    return {"cidr": str(network), "found": found}


@router.post("/api/assets/scan/import")
def scan_import(
    body: ScanImportBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把使用者勾選的掃描結果納入資產清單。只有機器事實（IP/Hostname），沒有業務
    欄位——借道既有 dynassets 匯入管道（identity 解析／DYN- 序號／source_record
    都直接沿用），不重造一套規則。業務欄位事後用 PUT /api/assets/{serial} 補。
    """
    if not body.hosts:
        raise HTTPException(400, "沒有選擇任何主機")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
    ) as tmp:
        writer = csv.writer(tmp)
        writer.writerow(["IP", "Hostname"])
        for h in body.hosts:
            writer.writerow([h.ip, h.hostname or ""])
        tmp_path = Path(tmp.name)

    import dynassets_import
    try:
        summary = dynassets_import.import_dynassets(tmp_path, conn)
    finally:
        tmp_path.unlink(missing_ok=True)

    create_import_log(
        conn, imported_by=session["username"],
        hardware_count=summary["inserted"] + summary["updated"],
        personnel_count=0, software_count=0, error_count=len(summary["errors"]),
        source="scan_import",
    )
    return summary


# ===== 主機分類作業（逐台的部門報告分類）=====
# 為什麼要獨立一頁：報表頁B/頁C 的三桶（核心交易／非核心／測試）與分色全部靠
# hardware.system_category，而 2026-08-26 盤點的結果是 2740 台裡有 452 台管理員
# 那份 Excel 根本沒涵蓋。這 452 台只能人工判斷 —— 沒有一個「哪些還沒分、剩幾台」
# 的畫面，它們會永遠停在未分類，報表就永遠有一塊灰的。
#
# ⚠️ 這套分類**跟 CIA 資訊資產清冊無關**，是專門為了產出那三張部門報告而存在的
# 獨立分類（使用者 2026-08-26 指正）。不要拿清冊的環境別來對帳。
#
# 薄 HTTP 層，邏輯全在 system_report.py。

@router.get("/api/classify/summary")
def classify_summary_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import system_report

    return system_report.classify_summary(conn)


@router.get("/api/classify")
def classify_list_endpoint(
    only: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """`only`＝unclassified／classified，省略回全部。"""
    import system_report

    if only not in (None, "", "unclassified", "classified"):
        raise HTTPException(400, f"only 參數只接受 unclassified／classified，收到「{only}」")
    return {"rows": system_report.classify_rows(conn, only or None)}


@router.put("/api/classify")
def classify_set_endpoint(
    body: ClassifySetBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import system_report

    try:
        return system_report.set_asset_categories(
            conn, body.asset_serials, body.category, session["username"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/classify/seed")
def classify_seed_endpoint(
    file: UploadFile = File(...),
    commit: bool = Form(False),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """用外部盤點表批次帶入分類。**預設乾跑（commit=false）只回試算結果。**

    只讀「主機名稱」與「分類」兩欄，其餘欄位一律忽略 —— 使用者 2026-08-26 明確
    指示：類別、主機名稱以《資訊資產清冊》為準，來源表只有分類可以參考。

    欄位用**表頭文字**找（不是固定位置）：這份表是別人維護的，欄位順序會變，
    位置寫死會在某次他們插一欄之後安靜地把整欄資料對錯。找不到表頭就報錯，
    不猜。
    """
    import system_report

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 檔案")

    try:
        wb = load_workbook(io.BytesIO(file.file.read()), data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl對壞檔案的錯誤型態不一，統一攔截如實回報
        raise HTTPException(400, f"檔案讀不開：{exc}") from exc

    ws = wb.active
    header = [str(c or "").strip() for c in next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())]
    try:
        i_host = header.index("主機名稱")
        i_cat = header.index("分類")
    except ValueError as exc:
        raise HTTPException(
            400,
            f"找不到必要欄位。需要「主機名稱」與「分類」兩個表頭，這份檔的表頭是：{header}",
        ) from exc

    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or len(row) <= max(i_host, i_cat):
            continue
        rows.append({"hostname": row[i_host], "category": row[i_cat]})

    return system_report.seed_categories_from_rows(conn, rows, dry_run=not commit)


@router.get("/api/field-meta")
def field_meta_endpoint(session: sqlite3.Row = Depends(require_auth)):
    """欄位中繼資料（分類×兩層），前端據此把納入管理表單分區、標 tech/biz。
    路徑刻意不放 /api/assets/ 底下——會被 /api/assets/{asset_serial} 這條 path 參數路由吃掉。"""
    return json.loads((_BACKEND_DIR / "field_meta.json").read_text(encoding="utf-8"))


@router.post("/api/assets/adopt")
def adopt_endpoint(
    body: AdoptBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把一台掃到的主機登記成受管資產（technical 前端已帶入、business 使用者填）。"""
    f = {k: v for k, v in body.fields.items() if v not in (None, "")}
    ip = f.get("ip")
    if not ip:
        raise HTTPException(400, "缺少 IP，無法納入管理")
    if conn.execute("SELECT 1 FROM hardware WHERE ip = ?", (ip,)).fetchone():
        raise HTTPException(409, f"{ip} 已納入管理")
    if not f.get("asset_serial"):
        f["asset_serial"] = f"ADOPT-{ip}"   # 沒公司資產序號時先給暫用值，之後可改
    # 資產狀態**不替它填**（2026-10-20 使用者：資產狀態只有 CIA 可以寫，見 lifecycle.py）。
    # 留空，畫面顯示「CIA 未登記」——比以前填「使用中」誠實（那是系統捏造的 CIA 欄位值）。
    # 人在表單上自己填的狀態也不收：要記下線請用「待 CIA 異動」。
    f.pop("asset_status", None)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
    f = {k: v for k, v in f.items() if k in cols}
    new_id = insert_hardware(conn, **f)

    # 納管當下就撤銷這台的「漏登記」，不要等下一次掃描。
    # 實際踩到（2026-07-19）：使用者納管 5 台之後，儀表板仍顯示「漏登記 6 筆」，
    # 但實際只有 1 台沒登記——因為撤銷只發生在掃描後的重比對。中間這段時間畫面在說謊，
    # 而「剛做完的動作沒有反映在畫面上」是最容易讓人失去信任的一種。
    retracted = conn.execute(
        "UPDATE comparison_result SET is_read = 1, handled_at = datetime('now','localtime') "
        "WHERE issue_type = '漏登記' AND is_read = 0 AND ("
        "  (ip IS NOT NULL AND ip = ?) OR (hostname IS NOT NULL AND hostname != '' AND hostname = ?)"
        ")",
        (ip, f.get("hostname") or ""),
    ).rowcount
    conn.commit()
    return {"id": new_id, "asset_serial": f["asset_serial"], "retracted_issues": retracted}


@router.get("/api/search")
def global_search(
    q: str,
    limit: int = 8,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """全域搜尋：一個框找遍資產／服務／業務系統／未登記的掃描結果。

    設計取捨：
    - **分組回傳而不是混成一串**：使用者搜「3306」時，「哪台在聽 3306」跟
      「哪台資產叫 3306」是完全不同的東西，混在一起只會讓人多按一次才找到。
    - **每組限量**：搜尋框是導航用的，不是報表。要看全部就點該組的「看全部」，
      帶著同一個關鍵字跳到對應的清單頁（連結由前端組，後端只回關鍵字命中）。
    - **掃到但未登記的主機也要出現**：那正是「這個 IP 是什麼」最常被問的時候，
      如果只搜已登記資產，答案會是「查無此項」——但它明明就在網路上。
    """
    import search_terms

    kw = (q or "").strip()
    if not kw:
        return {"query": "", "groups": []}
    if limit < 1 or limit > 50:
        raise HTTPException(400, "limit 只接受 1–50")

    # 2026-08-27 使用者的批評：「全文代表 DB 我都可以查，不是我說一個案例多一個
    # 功能，沒說就查不到」。以前這裡是寫死九個欄位，等於「我事先想到的清單」。
    #
    # 現在分兩層：
    #   · 既有的專屬分組（資產／服務／業務系統／機櫃／人員／軟體／單據）——
    #     它們的標題與連結是人工設計過的，導航體驗好，保留
    #   · 通用掃表（search_terms.scan）——掃過所有該掃的表的所有文字欄位，
    #     補上「沒被寫死到的東西完全看不見」這個洞
    parsed = search_terms.parse_query(kw)
    # 既有分組仍是單一 like，但改用「展開後的第一個詞」當代表；
    # 多關鍵字與同義詞的完整效果由通用組負責，兩者在畫面上分開顯示，
    # 不會讓人以為某一組漏了東西。
    term = parsed[0]["match"][0] if parsed else kw
    like = f"%{term}%"
    # exact 是**沒有包萬用字元**的原字串，用來判「完全相同」與「從頭相符」。
    exact = term

    groups = []

    # 一併 JOIN 業務系統：搜到一台機器之後，最常接著要問的就是「它屬於哪個系統」
    # （使用者 2026-09-10：「SEC01 是哪個 APID，我怎麼找不到」）。
    # 用 LEFT JOIN 而不是另外查一次——沒對到系統的機器仍然要出現在結果裡。
    #
    # 主機名比對加 lower()：真實資料裡 `sec01` 是小寫，人會打 `SEC01`。
    # SQLite 的 LIKE 對純 ASCII 本來就不分大小寫，但寫出來是為了不依賴那個預設值
    # （`hardware.hostname` 沒有 COLLATE NOCASE，別處已經因此吃過虧）。
    assets = conn.execute(
        "SELECT h.asset_serial, h.hostname, h.ip, h.device_model, h.environment, "
        "       h.asset_purpose, h.api_id, b.name AS system_name "
        "FROM hardware h LEFT JOIN business_system b ON b.api_id = h.api_id "
        "WHERE lower(COALESCE(h.hostname,'')) LIKE lower(:a) OR h.ip LIKE :b "
        "   OR lower(COALESCE(h.asset_serial,'')) LIKE lower(:c) "
        "   OR h.device_model LIKE :d OR h.asset_purpose LIKE :e OR h.custodian LIKE :f "
        # **完全相同的排最前面。**
        # 2026-09-23 公司機實際踩到：搜 `10.99.18.16` 找不到那台，但 .161/.162/.163
        # 都列出來了。`LIKE '%10.99.18.16%'` 同時命中前綴，而排序只按主機名——
        # `SECSVR018-161T` 的大寫 S 排在 `test1T` 的小寫 t 前面，那台就被擠出 LIMIT。
        #
        # 使用者的感受是「**明明有卻搜不到**」，那比「查無此項」更傷信任：
        # 查無此項至少是一個明確的答案，這個是系統對他說謊。
        #
        # 三層排序，理由各自不同：
        #   1. IP／主機名／序號**完全相同**——那幾乎一定就是他要找的那一台
        #   2. 從頭開始相符（前綴）——比「出現在中間」更可能是他要的
        #   3. 其餘按主機名，維持既有的可預期順序
        "ORDER BY CASE WHEN h.ip = :exact "
        "               OR lower(COALESCE(h.hostname,'')) = lower(:exact) "
        "               OR lower(COALESCE(h.asset_serial,'')) = lower(:exact) "
        "          THEN 0 "
        "          WHEN h.ip LIKE :pre "
        "               OR lower(COALESCE(h.hostname,'')) LIKE lower(:pre) "
        "               OR lower(COALESCE(h.asset_serial,'')) LIKE lower(:pre) "
        "          THEN 1 ELSE 2 END, h.hostname LIMIT :lim",
        {"a": like, "b": like, "c": like, "d": like, "e": like, "f": like,
         "exact": exact, "pre": f"{exact}%", "lim": limit + 1},
    ).fetchall()
    if assets:
        groups.append({
            "key": "assets", "label": "資產",
            "items": [{
                "title": r["hostname"] or r["asset_serial"],
                # 「這台屬於哪個系統」要看得到（使用者 2026-09-10：
                # 「SEC01 是哪個 APID，我怎麼找不到」）。本來只有 IP／機型／環境，
                # 搜到之後還是答不出他真正要問的那件事。
                # 「這台屬於哪個系統」要看得到——本來只有 IP／機型／環境，
                # 搜到之後還是答不出他真正要問的那件事。
                # 有 api_id 卻沒有名稱時顯示代碼本身，不要整段消失：
                # 那代表「對照表少了這個代碼」，是另一種要補的東西。
                "subtitle": " · ".join(x for x in (
                    r["ip"],
                    (r["system_name"] or r["api_id"]) if r["api_id"] else None,
                    r["device_model"], r["environment"]) if x),
                "to": f"/assets/{r['asset_serial']}",
            } for r in assets[:limit]],
            "more": len(assets) > limit,
            "more_to": f"/assets?q={kw}",
        })

    # 服務：搜埠號、行程名、服務名都要中。數字就當埠號精準比，
    # 不然搜 "22" 會把 8022、2222 全撈進來，最想要的那筆反而被淹掉。
    if kw.isdigit():
        svc_rows = conn.execute(
            "SELECT hs.ip, hs.port, hs.process, hs.service_guess, hs.exposure, "
            "hs.asset_serial, h.hostname FROM host_service hs "
            "LEFT JOIN hardware h ON h.asset_serial = hs.asset_serial "
            "WHERE hs.gone_at IS NULL AND hs.port = ? ORDER BY hs.ip LIMIT ?",
            (int(kw), limit + 1),
        ).fetchall()
    else:
        svc_rows = conn.execute(
            "SELECT hs.ip, hs.port, hs.process, hs.service_guess, hs.exposure, "
            "hs.asset_serial, h.hostname FROM host_service hs "
            "LEFT JOIN hardware h ON h.asset_serial = hs.asset_serial "
            "WHERE hs.gone_at IS NULL AND (hs.process LIKE ? OR hs.service_guess LIKE ?) "
            "ORDER BY hs.ip LIMIT ?",
            (like, like, limit + 1),
        ).fetchall()
    if svc_rows:
        groups.append({
            "key": "services", "label": "服務",
            "items": [{
                "title": f"{r['service_guess'] or '未知服務'} · {r['port']}",
                "subtitle": f"{r['hostname'] or r['ip']}"
                            f"{'（僅本機）' if r['exposure'] == 'localhost' else ''}",
                "to": f"/services?port={r['port']}",
            } for r in svc_rows[:limit]],
            "more": len(svc_rows) > limit,
            "more_to": f"/services?port={kw}" if kw.isdigit() else "/services",
        })

    # 「業務系統」合併兩個來源，使用者不需要知道底下分開存：
    # (a) topology.vue 的 systems 表——人工維護的拓樸圖，舊資料
    # (b) hardware.api_id——MICS/blast 用的那份真相（177個真實業務系統代碼），
    #     2026-08-19 使用者反映搜代碼（如「N-218」）或系統名（如「STO 交易管理系統」）
    #     都搜不到，因為這裡原本只查 (a)，根本沒查 hardware.api_id。
    systems = conn.execute(
        "SELECT id, label, category, domain FROM systems "
        "WHERE id LIKE ? OR label LIKE ? OR category LIKE ? OR domain LIKE ? "
        "ORDER BY label LIMIT ?",
        (like, like, like, like, limit + 1),
    ).fetchall()
    biz_rows = conn.execute(
        "SELECT api_id, MIN(asset_name) AS name, COUNT(*) AS cnt FROM hardware "
        "WHERE api_id IS NOT NULL AND api_id != '' AND (api_id LIKE ? OR asset_name LIKE ?) "
        "GROUP BY api_id ORDER BY api_id LIMIT ?",
        (like, like, limit + 1),
    ).fetchall()
    system_items = [{
        "title": r["label"],
        "subtitle": " · ".join(x for x in (r["category"], r["domain"]) if x),
        # 帶 system id：topology 頁會自動選中這個節點，不然搜到系統名字進去
        # 只看到一整張沒有重點標記的圖，等於白搜。
        "to": f"/topology?system={r['id']}",
    } for r in systems]
    biz_items = [{
        "title": r["name"] or r["api_id"],
        "subtitle": f"{r['api_id']} · {r['cnt']} 台主機",
        "to": f"/assets?filter_field=api_id&filter_value={r['api_id']}",
    } for r in biz_rows]
    all_system_items = system_items + biz_items
    if all_system_items:
        groups.append({
            "key": "systems", "label": "業務系統",
            "items": all_system_items[:limit],
            "more": len(all_system_items) > limit,
            "more_to": "/topology",
        })

    # 機櫃：CI圖譜有 rack:{機房}#{櫃號} 節點（真的有依賴邊，不只是位置標籤）——
    # 2026-08-19 使用者反映連機櫃都要找得到，搜到直接帶去 /blast 查「這櫃掉電
    # 會波及誰」，比只列出同機櫃資產清單更有用。
    rack_rows = conn.execute(
        "SELECT physical_location, rack_no, COUNT(*) AS cnt FROM hardware "
        "WHERE physical_location IS NOT NULL AND physical_location != '' "
        "AND rack_no IS NOT NULL AND rack_no != '' "
        "AND (physical_location LIKE ? OR rack_no LIKE ? "
        "OR (physical_location || '/' || rack_no) LIKE ?) "
        "GROUP BY physical_location, rack_no ORDER BY physical_location, rack_no LIMIT ?",
        (like, like, like, limit + 1),
    ).fetchall()
    if rack_rows:
        from urllib.parse import quote as _quote
        groups.append({
            "key": "racks", "label": "機櫃",
            "items": [{
                "title": f"{r['physical_location']} / {r['rack_no']}",
                "subtitle": f"{r['cnt']} 台主機 · 查這櫃掉電會波及誰",
                "to": "/blast?mode=incident&q="
                      + _quote(f"rack:{r['physical_location']}#{r['rack_no']}"),
            } for r in rack_rows[:limit]],
            "more": len(rack_rows) > limit,
            "more_to": "/segments",
        })

    # 人員：2026-08-19 使用者反映「不只業務系統跟機櫃，是全部都要能全文搜」——
    # personnel 是獨立表，搜資產時的 custodian 只涵蓋 hardware.custodian 這個
    # 欄位，找不到用 personnel.person_name 登記的人。點進去帶去他名下的資產清單。
    people = conn.execute(
        "SELECT DISTINCT person_name, phone, belong_division, belong_department "
        "FROM personnel WHERE person_name IS NOT NULL AND person_name != '' "
        "AND (person_name LIKE ? OR phone LIKE ? OR job_desc LIKE ?) "
        "ORDER BY person_name LIMIT ?",
        (like, like, like, limit + 1),
    ).fetchall()
    if people:
        groups.append({
            "key": "people", "label": "人員",
            "items": [{
                "title": r["person_name"],
                "subtitle": " · ".join(
                    x for x in (r["belong_division"], r["belong_department"], r["phone"]) if x
                ),
                "to": f"/assets?filter_field=custodian&filter_value={r['person_name']}",
            } for r in people[:limit]],
            "more": len(people) > limit,
            "more_to": f"/assets?q={kw}",
        })

    # 軟體/資料庫盤點：跟資產是分開的表，搜資產名稱找不到裝在上面的軟體/DB名稱。
    soft = conn.execute(
        "SELECT asset_name, hostname, ip, db_software, asset_serial FROM software "
        "WHERE asset_name LIKE ? OR db_software LIKE ? OR hostname LIKE ? "
        "ORDER BY asset_name LIMIT ?",
        (like, like, like, limit + 1),
    ).fetchall()
    if soft:
        groups.append({
            "key": "software", "label": "軟體／資料庫",
            "items": [{
                "title": r["asset_name"] or r["db_software"] or "（未命名）",
                "subtitle": " · ".join(x for x in (r["db_software"], r["hostname"] or r["ip"]) if x),
                "to": f"/assets/{r['asset_serial']}" if r["asset_serial"] else f"/assets?q={kw}",
            } for r in soft[:limit]],
            "more": len(soft) > limit,
            "more_to": f"/assets?q={kw}",
        })

    # 掃到但沒登記的主機——「這個 IP 是什麼」最常就是在問這種
    scan_time = _latest_scan_time(conn)
    if scan_time:
        known_ips = {r["ip"] for r in conn.execute(
            "SELECT ip FROM hardware WHERE ip IS NOT NULL AND ip != ''")}
        unreg = [r for r in conn.execute(
            "SELECT ip, hostname, os_guess, mac_vendor FROM scan_history "
            "WHERE scan_time = ? AND scan_ok = 1 AND (ip LIKE ? OR hostname LIKE ?) "
            "ORDER BY ip LIMIT ?", (scan_time, like, like, limit + 10))
            if r["ip"] not in known_ips]
        if unreg:
            groups.append({
                "key": "unregistered", "label": "掃到但未登記",
                "items": [{
                    "title": r["ip"],
                    "subtitle": " · ".join(x for x in (r["hostname"], r["os_guess"],
                                                       r["mac_vendor"]) if x) or "無其他線索",
                    "to": "/adopt",
                } for r in unreg[:limit]],
                "more": len(unreg) > limit,
                "more_to": "/adopt",
            })

    # 單據檔案室：連 Word 內文一起搜。使用者原話是「以前要找相關資料，我要翻所有的
    # Word 檔」——全域搜尋不涵蓋單據的話，那個痛點只解決了一半。
    if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='doc_archive'"
    ).fetchone():
        import doc_import

        docs = conn.execute(
            "SELECT id, file_name, doc_type, request_no, ref_request_no, form_date, "
            "hostname, ip, asset_serial, full_text FROM doc_archive "
            "WHERE full_text LIKE ? OR file_name LIKE ? OR request_no LIKE ? "
            "OR ref_request_no LIKE ? OR hostname LIKE ? OR ip LIKE ? "
            "ORDER BY form_date DESC LIMIT ?",
            (like, like, like, like, like, like, limit + 1),
        ).fetchall()
        if docs:
            groups.append({
                "key": "documents", "label": "單據（Word 內文）",
                "items": [{
                    "title": (r["request_no"] or r["ref_request_no"] or r["file_name"])
                             + f"　{r['hostname'] or ''}".rstrip(),
                    # 命中片段直接顯示，使用者不用開檔就知道是不是要找的那份
                    "subtitle": doc_import.snippet(r["full_text"] or "", kw)
                                or " · ".join(x for x in (r["form_date"], r["ip"]) if x),
                    "to": f"/documents?q={kw}",
                } for r in docs[:limit]],
                "more": len(docs) > limit,
                "more_to": f"/documents?q={kw}",
            })

    # ===== 通用掃表 =====
    #
    # 上面那些分組是「人工設計過的入口」，這一段是「保證沒有東西是看不見的」。
    # 已經有專屬分組的表就不重複列（但仍算進總數，不然畫面數字對不起來）。
    tables = search_terms.searchable_tables(conn)
    CURATED = frozenset({"hardware", "host_service", "systems", "personnel",
                         "software", "doc_archive", "scan_history"})
    scanned = search_terms.scan(conn, parsed, tables, skip_tables=CURATED)
    # **略過了什麼，要在畫面上講出來，而且是人看得懂的話。**
    # 不是「部分結果」這種模糊詞：要講「哪一張表、幾筆、為什麼、去哪裡找」。
    # 而且**有略過就出現**，不是只在沒結果時才講——同一個關鍵字昨天找得到、
    # 今天找不到，使用者第一個念頭是「資料掉了」。
    if scanned.get("skipped"):
        # 表名對使用者沒有意義——`software` 要講成「軟體清單」。
        # 「去哪裡找」要寫齊選單編號＋頁名＋網址，不然他還是得問人。
        _NAME = {"software": "軟體清單", "host_service": "服務清單",
                 "scan_history": "掃描紀錄", "vuln_finding": "弱點明細",
                 "account_record": "帳號清單"}
        _WHERE = {"software": "3-12 軟體盤點（/software）",
                  "host_service": "3-9 服務盤點（/services）",
                  "scan_history": "3-2 主機清單（/hosts）",
                  "vuln_finding": "9-1 弱點報表（/patch）",
                  "account_record": "3-7 帳號儀表板（/accounts）"}
        groups.append({
            "key": "skipped", "label": "這次沒有掃的資料",
            "items": [{
                "title": (f"{_NAME.get(g['table'], g['table'])}"
                          f"：{g['rows']:,} 筆未納入這次搜尋"),
                "subtitle": (f"資料量超過 {scanned['big_table_rows']:,} 筆門檻，"
                             f"全文搜尋會讓整頁變慢。"
                             + (f"請到{_WHERE[g['table']]}搜尋。"
                                if g["table"] in _WHERE
                                else "請到該項目的專屬頁面搜尋。")),
                "to": "",
            } for g in scanned["skipped"]],
        })
    if scanned["groups"]:
        # 沒有 /search 這頁，通用命中不給死連結（點了會 404）。
        # 認得出家的就導過去：ci_node（叢集/ESXi/datastore）→ 架構圖；其餘留空不可點。
        def _generic_to(table: str) -> str:
            return "/architecture" if table == "ci_node" else ""

        groups.append({
            "key": "other", "label": "其他命中（通用掃表）",
            "items": [{
                "title": f"{g['table']}（{g['count']} 筆）",
                "subtitle": "；".join(
                    f"{s0['title']}〔{'、'.join(m['field'] for m in s0['matched'][:2])}〕"
                    for s0 in g["samples"][:2]),
                "to": _generic_to(g["table"]),
            } for g in scanned["groups"][:limit]],
            "more": False,
            "more_to": "",
        })

    # ===== 讓數字說得出來源 =====
    #
    # 這三個欄位是為了回答「為什麼查不到」。搜「測試區 vc」得 0 筆時，人要能看出
    # 是哪一個字完全沒命中，而不是兩個字都對但沒交集。
    # 「查不到」跟「沒有這個東西」是兩件事——這是這個專案的底線之一。
    return {
        "query": kw,
        "groups": groups,
        # 每個關鍵字展開成哪些詞（同義詞不可以安靜地擴大範圍）
        "terms": [{"term": p["term"], "match": p["match"],
                   "hits": scanned["term_hits"].get(p["term"], 0)} for p in parsed],
        # 這次搜了哪些表——沒列出來的表就是沒搜，人才知道結果的涵蓋範圍
        "searched_tables": [t for t, _ in tables],
        "excluded_tables": sorted(search_terms.EXCLUDE_TABLES),
        "total_hits": scanned["total"],
    }


@router.get("/api/services")
def list_services_endpoint(
    ip: str | None = None,
    asset_serial: str | None = None,
    port: int | None = None,
    exposure: str | None = None,
    include_gone: bool = False,
    include_infra: bool = True,
    card: str | None = None,
    sort_by: str = "ip",
    order: str = "asc",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """服務清單。card＝頁首數字卡下鑽（條件跟 summary 同一份 service_inventory.CARD_WHERE）。預設不含已消失的，含基礎服務（SSH/NTP…，前端可一鍵收合）。

    排序在後端做（欄位走白名單、方向只認 asc/desc），跟 /api/assets 同一套；
    這張表可能上千列，前端排序只會排到目前拿到的那批。
    """
    import service_inventory

    if card is not None and card not in service_inventory.card_where(service_inventory.stale_days(conn)):
        raise HTTPException(400, f"不認得的卡片：{card}")
    rows = service_inventory.list_services(
        conn, ip=ip, asset_serial=asset_serial,
        include_gone=include_gone, include_infra=include_infra, card=card,
    )
    if port is not None:
        rows = [r for r in rows if r["port"] == port]
    if exposure:
        rows = [r for r in rows if r["exposure"] == exposure]

    key = sort_by if sort_by in ALLOWED_SERVICE_SORT else "ip"
    reverse = order == "desc"

    def sort_val(r):
        v = r.get(key)
        if v is None or v == "":
            return (1, "")           # 空值一律排最後，不隨 asc/desc 翻面
        if key == "ip" and isinstance(v, str) and v.count(".") == 3:
            try:
                return (0, tuple(int(p) for p in v.split(".")))
            except ValueError:
                return (0, v)
        return (0, v if not isinstance(v, str) else v.lower())

    try:
        rows.sort(key=sort_val, reverse=reverse)
    except TypeError:
        rows.sort(key=lambda r: (r.get(key) is None, str(r.get(key) or "")), reverse=reverse)

    return {"items": rows, "summary": service_inventory.service_summary(conn)}


@router.get("/api/services/windows")
def list_winservices_endpoint(
    asset_serial: str | None = None,
    ip: str | None = None,
    include_gone: bool = False,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Windows 服務清單（Win32_Service；拿不到時是登錄檔來源、沒有執行狀態）。資產頁「服務」分頁用。"""
    import service_inventory

    return {"items": service_inventory.list_winservices(
        conn, asset_serial=asset_serial, ip=ip, include_gone=include_gone)}


@router.get("/api/services/iis")
def iis_for_asset_endpoint(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """IIS 樹（站台→應用程式／虛擬目錄→集區＋綁定）。state：none＝沒查過、unused＝沒裝 IIS、
    ok、denied／failed（保留上一份好的資料，note 講原因）。"""
    import iis_inventory

    return iis_inventory.for_asset(conn, asset_serial)


@router.get("/api/services/summary")
def services_summary_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import service_inventory

    return service_inventory.service_summary(conn)


@router.get("/api/san/switches")
def san_switches_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import san_collector

    items = san_collector.list_switches(conn)
    done = sum(1 for i in items if i["collected"])
    return {"items": items, "total": len(items), "collected": done}


@router.get("/api/san/offline-guide")
def san_offline_guide_endpoint(session: sqlite3.Row = Depends(require_auth)):
    """離線匯入的步驟與標準唯讀指令清單（畫面一鍵複製用；與線上收集同一份）。"""
    import san_collector

    return san_collector.offline_guide()


@router.post("/api/san/collect")
def san_collect_endpoint(
    body: SanCollectBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import san_collector

    import collect_log
    import onboard_engine

    s_user, s_pw = resolve_storage_login(conn, body, session)
    by = session["username"] if session else None
    # 每一次都記下當下環境（成功也記）：失敗時才有「同一台行程上次成功是什麼樣子」可以比
    try:
        inv = san_collector.collect(body.ip, s_user, s_pw)
    except RuntimeError as exc:
        safe = scrub_secret(str(exc), s_pw)
        collect_log.record(conn, "san_collect", body.ip, False, safe, actor=by, exc=RuntimeError(safe),
                           extra={"connect_traceback": scrub_secret(onboard_engine.last_connect_trace(body.ip) or "", s_pw)})
        raise HTTPException(502, safe)
    except Exception as exc:  # noqa: BLE001 - 未預期的也要留證據再往上丟
        collect_log.record(conn, "san_collect", body.ip, False, scrub_secret(f"{type(exc).__name__}: {exc}", s_pw),
                           actor=by, exc=RuntimeError(scrub_secret(str(exc), s_pw)),
                           extra={"connect_traceback": scrub_secret(onboard_engine.last_connect_trace(body.ip) or "", s_pw)})
        raise
    result = san_collector.store(conn, body.ip, inv, by)
    san_collector.reconcile_hba_san(conn)   # 新收的 SAN 資料 → 反查補所有主機 HBA 的 fabric/zone
    raw = inv.get("_raw") or {}
    san_collector.archive(conn, body.ip, "online_collect",
                          "\n".join(f"### {k}\n{v}" for k, v in raw.items()), True,
                          recognized=raw.keys(), by=by)
    collect_log.record(conn, "san_collect", body.ip, True,
                       f"收集成功：WWPN {result.get('wwpn_count')} 筆", actor=by)
    return {"ok": True, **result}


@router.post("/api/san/import")
def san_import_endpoint(
    body: SanImportBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """離線匯入：防火牆還沒開通時，貼上自己在 switch 跑的唯讀指令畫面 → 同一支 parser 判讀後入庫。
    完全不連線、不需要帳密。"""
    import san_collector
    import san_parse

    import collect_log

    if not body.ip or not body.transcript.strip():
        raise HTTPException(400, "IP 和貼上的內容都要填")
    by = session["username"] if session else None
    outs = san_collector.split_transcript(body.transcript)
    # 照標準清單比對：缺哪幾個要講出來（2026-09-15 使用者要格式統一）
    bases = [c.split()[0] for c in san_collector.READONLY_COMMANDS]
    missing = [c for c in bases if c not in outs]
    parse_ok = bool(outs.get("switchshow") or outs.get("nscamshow") or outs.get("alishow"))
    # 原文整段先存（成功失敗都存、每次都留）——使用者：「通通都要記錄，哪一天用得到不知道」
    archive_id = san_collector.archive(conn, body.ip, "offline_import", body.transcript, parse_ok,
                                       recognized=outs.keys(), missing=missing, by=by)
    if not parse_ok:
        msg = ("找不到 switchshow／nscamshow／alishow 等指令的輸出——"
               "請照畫面上的指令清單跑，把『整段畫面』貼上（要含提示字元＋指令那一行）")
        collect_log.record(conn, "san_import", body.ip, False, msg, actor=by,
                           extra={"recognized": sorted(outs), "missing": missing,
                                  "transcript_lines": len(body.transcript.splitlines()),
                                  "archive_id": archive_id})
        raise HTTPException(400, msg + f"（原文已存檔 #{archive_id}，修好解析後可重新判讀，不用重收）")
    # IP 防呆（2026-09-15 使用者要）：switch 自己在 fabricshow 回報的管理 IP 跟填的 IP 不同，
    # 很可能是把 A 台的畫面匯進 B 台。先擋下來問人；原文上面已經存檔，不會丟。
    import san_parse

    sw_name = san_parse.parse_switchshow(outs.get("switchshow", ""))[0].get("switchName")
    reported_ip = san_parse.local_mgmt_ip(outs.get("fabricshow", ""), sw_name)
    entered_ip = body.ip.strip()
    if reported_ip is None:
        ip_check = "unverified"      # 沒跑 fabricshow 或認不出自己這台：照常匯入，但講清楚沒驗
    elif reported_ip == entered_ip:
        ip_check = "match"
    else:
        ip_check = "mismatch_forced" if body.force else "mismatch"
    if ip_check == "mismatch":
        msg = (f"IP 對不上：這份畫面是 switch「{sw_name or '?'}」自己回報的 {reported_ip}，"
               f"你填的是 {entered_ip}。可能貼錯台了——確定要匯入到 {entered_ip} 嗎？")
        collect_log.record(conn, "san_import", entered_ip, False, msg, actor=by,
                           extra={"reported_ip": reported_ip, "switch_name": sw_name,
                                  "archive_id": archive_id, "ip_check": ip_check})
        raise HTTPException(409, msg)
    inv = san_collector._parse_outputs(outs)   # 一併保留原始輸出（raw_json）
    result = san_collector.store(conn, body.ip, inv, by)
    san_collector.reconcile_hba_san(conn)   # 匯入的 SAN 資料 → 反查補主機 HBA
    min_missing = [c for c in san_collector.MIN_REQUIRED if c not in outs]
    collect_log.record(
        conn, "san_import", body.ip, True,
        f"離線匯入：認到 {len(outs)} 個指令、WWPN {result.get('wwpn_count')} 筆"
        + (f"；缺 {'、'.join(missing)}" if missing else "")
        + ("；⚠️ IP 不符但使用者確認強制匯入" if ip_check == "mismatch_forced" else "")
        + ("；IP 無法驗證（沒有 fabricshow）" if ip_check == "unverified" else ""),
        actor=by, extra={"recognized": sorted(outs), "missing": missing, "min_missing": min_missing,
                         "archive_id": archive_id, "ip_check": ip_check, "reported_ip": reported_ip})
    return {"ok": True, "imported_cmds": sorted(outs), "missing_cmds": missing,
            "min_missing": min_missing, "archive_id": archive_id,
            "ip_check": ip_check, "reported_ip": reported_ip, **result}


@router.get("/api/san/switches/{ip}/archive")
def san_switch_archive_endpoint(
    ip: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """這台 switch 每一次離線匯入／線上收集的原文存檔（新到舊，只增不改）。"""
    import san_collector

    return {"items": san_collector.list_archive(conn, ip)}


@router.get("/api/san/archive/{archive_id}/text")
def san_archive_text_endpoint(
    archive_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """下載某一次的原文（純文字，原樣）。"""
    import san_collector
    from urllib.parse import quote

    r = san_collector.get_archive(conn, archive_id)
    if not r:
        raise HTTPException(404, "查無此存檔")
    fname = f"SAN_{r['ip']}_{r['created_at'][:10]}_#{r['id']}.txt"
    return Response(content=(r["content"] or "").encode("utf-8"),
                    media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


@router.get("/api/san/switches/{ip}")
def san_switch_detail_endpoint(
    ip: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    row = conn.execute("SELECT * FROM san_switch WHERE ip = ?", (ip,)).fetchone()
    if not row:
        raise HTTPException(404, "這台還沒收集過")
    d = dict(row)
    d["data"] = json.loads(d.pop("data_json") or "{}")
    return d


# ===== 主機硬體規格收集（已納管，2026-09-13）=====
@router.post("/api/host-spec/collect")
def host_spec_collect_endpoint(
    asset_serial: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """對已納管主機收 CPU/RAM/Storage/PSU＋NIC/HBA（AIX/Linux；Windows 待補）。
    用收集金鑰、唯讀、不用打密碼。傳 asset_serial＝只收這一台（詳細頁「收集規格」用）。"""
    import host_spec_collector
    return host_spec_collector.run_collection(conn, trigger="manual", only_serial=asset_serial)


@router.post("/api/host-spec/reparse")
def host_spec_reparse_endpoint(
    asset_serial: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把已存的 `host_spec.raw_json` 重新判讀一次（2026-10-07，搬遷重建第一批）。

    **不連任何主機、不跑任何指令、不需要任何權限。** 收集時每一條指令的原文都存了，
    但當年的解析器只取走一小部分：硬體 RAID 整段組態被壓成一個 `True`、
    multipath 只留名稱不留 WWID、mdstat 只留一句摘要不留成員碟、DIMM 不留插槽位置。
    這一刀把那些本來就在 DB 裡的東西讀回來。

    跟 `/collect` 刻意分成兩個端點：一個會連出去、一個不會。
    合成一個的話，使用者按下去之前分不出這次會不會碰到正式機。
    """
    import host_spec_collector
    return host_spec_collector.reparse_stored(conn, only_serial=asset_serial)


@router.post("/api/san/reparse")
def san_reparse_endpoint(
    ip: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把已存的 `san_switch.raw_json` 重新判讀一次（2026-10-07）。

    挖出 `zoneshow`（**定義過的全部** zone 與 config；`cfgactvshow` 只有啟用中那份）
    與 `nsshow`（帶**埠號**的本機裝置；`nscamshow` 是全 fabric、沒有埠號）。
    兩份原文從 2026-09-13 起就一直收進 raw_json，只是沒有解析器看過它們。

    **不連任何 switch** —— 正式區的 SAN 要重連得申請、得排時間，這一刀不用。
    """
    import san_collector
    return san_collector.reparse_stored(conn, only_ip=ip)


@router.get("/api/host-spec/summary")
def host_spec_summary_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        n = conn.execute("SELECT COUNT(*) FROM host_spec").fetchone()[0]
        last = conn.execute("SELECT MAX(collected_at) FROM host_spec").fetchone()[0]
    except Exception:  # noqa: BLE001
        n, last = 0, None
    return {"collected": n, "last": last}


# ===== 軟體盤點（2026-09-11）=====
# 邏輯全在 software_inventory；這裡只是薄 HTTP 層。

@router.get("/api/software/summary")
def software_summary_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import software_inventory

    return software_inventory.software_summary(conn)


# ⚠️ 不能用 /api/software：那個路徑早就是 CIA 清冊「系統軟體」分頁的清單（本檔前段），
# FastAPI 先註冊先贏，這裡同路徑會被靜默蓋掉——v1.92 就是這樣讓 2-10 整頁白掉。
@router.get("/api/software/packages")
def software_list_endpoint(
    q: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """依軟體名稱彙總（幾個版本、幾台）。排序在前端做——彙總後筆數不多。"""
    import software_inventory

    return {"items": software_inventory.list_software(conn, q),
            "summary": software_inventory.software_summary(conn)}


@router.get("/api/software/versions")
def software_versions_endpoint(
    name: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import software_inventory

    return {"items": software_inventory.software_versions(conn, name)}


@router.get("/api/software/hosts")
def software_hosts_endpoint(
    name: str,
    version: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import software_inventory

    return {"items": software_inventory.software_hosts(conn, name, version)}


@router.get("/api/software/coverage")
def software_coverage_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """7.3 收集涵蓋：已納管主機依作業系統大類，有／沒有軟體清單各幾台（每格帶清單）。"""
    import software_inventory

    return software_inventory.coverage(conn)


@router.get("/api/software/by-host")
def software_by_host_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import software_inventory

    return {"items": software_inventory.hosts_overview(conn)}


@router.get("/api/software/host")
def software_host_endpoint(
    ip: str,
    include_gone: bool = False,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import software_inventory

    return {"items": software_inventory.host_packages(conn, ip, include_gone)}


@router.get("/api/software/changes")
def software_changes_endpoint(
    days: int = 30,
    ip: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import software_inventory

    return {"items": software_inventory.list_changes(conn, days, ip)}


@router.post("/api/software/collect")
def software_collect_endpoint(
    asset_serial: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """對已納管主機收一輪軟體清單（可指定單台）。同步執行、會真的連出去。"""
    import software_inventory

    return software_inventory.collect_software(conn, only_serial=asset_serial,
                                               trigger="manual")


@router.post("/api/services/collect")
def collect_services_endpoint(
    asset_serial: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """對已納管主機收一輪服務（可指定單台）。

    ⚠️ 這是同步執行、會真的連出去——大批主機時前端要顯示執行中三態（async-feedback 規範）。
    收不到行程名（沒 root）不算失敗：埠是拿得到的，那已經是主要價值。
    """
    import service_inventory

    try:
        return service_inventory.collect_services(
            conn, only_serial=asset_serial, trigger="manual"
        )
    except Exception as exc:  # noqa: BLE001 - 失敗原因原樣回給畫面，不吞成一句「失敗」
        raise HTTPException(500, f"服務採集失敗：{exc}") from exc


@router.get("/api/os-type-overrides")
def os_type_overrides_list(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """目前有效的人工指定，外加可選類別。"""
    import os_override
    return {"items": os_override.list_active(conn), "choices": os_override.choices()}


@router.put("/api/os-type-overrides")
def os_type_override_set(
    body: OsTypeOverrideBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import os_override
    try:
        return os_override.set_override(conn, body.hostname, body.ip, body.asset_serial,
                                        body.os_type, body.auto_os_type, body.reason,
                                        session["username"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/api/os-type-overrides")
def os_type_override_clear(
    hostname: str | None = None,
    ip: str | None = None,
    asset_serial: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """恢復自動判斷（軟刪除，紀錄保留）。"""
    import os_override
    n = os_override.clear(conn, hostname, ip, asset_serial, session["username"])
    return {"ok": True, "cleared": n}


__all__ = [
    'list_issues',
    'mark_issues_read_batch',
    'mark_issue_read',
    'asset_field_groups',
    'asset_facets',
    'asset_duplicates',
    'release_duplicate_serials',
    'merged_history',
    'dismiss_duplicate_group',
    'list_assets',
    'batch_lookup_assets',
    'list_personnel',
    'list_software',
    'hosts_endpoint',
    'asset_detail',
    'normalize_pending',
    'upsert_normalize_alias',
    'upsert_canonical_override',
    'delete_canonical_override',
    'list_normalize_alias',
    'delete_normalize_alias',
    'export_normalize_pending',
    'import_normalize_pending',
    'asset_scope_preview',
    'batch_asset_status',
    'cia_pending_list',
    'cia_pending_synced',
    'cia_pending_export',
    'update_asset',
    'create_asset_manual',
    'manual_field_options',
    'asset_tags_get',
    'asset_tags_add',
    'asset_tags_remove',
    'asset_tags_all',
    'asset_documents',
    'get_provision',
    'upload_provision_attachment',
    'download_provision_attachment',
    'manual_field_groups',
    'os_catalog',
    'scan_discover',
    'scan_import',
    'classify_summary_endpoint',
    'classify_list_endpoint',
    'classify_set_endpoint',
    'classify_seed_endpoint',
    'field_meta_endpoint',
    'adopt_endpoint',
    'global_search',
    'list_services_endpoint',
    'services_summary_endpoint',
    'san_switches_endpoint',
    'san_offline_guide_endpoint',
    'san_collect_endpoint',
    'san_import_endpoint',
    'san_switch_archive_endpoint',
    'san_archive_text_endpoint',
    'san_switch_detail_endpoint',
    'host_spec_collect_endpoint',
    'host_spec_reparse_endpoint',
    'san_reparse_endpoint',
    'host_spec_summary_endpoint',
    'software_summary_endpoint',
    'software_list_endpoint',
    'software_versions_endpoint',
    'software_hosts_endpoint',
    'software_by_host_endpoint',
    'software_host_endpoint',
    'software_changes_endpoint',
    'software_collect_endpoint',
    'collect_services_endpoint',
    'os_type_overrides_list',
    'os_type_override_set',
    'os_type_override_clear',
]
