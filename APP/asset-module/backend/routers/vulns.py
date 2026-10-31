from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()



# ===== 弱點彙總（CL_Patch）共享儲存（2026-09-21）=====
# 使用者：「我只是要轉移到系統上，變系統版不是單機版」。
# 那支工具的分析邏輯一行不動，只把「該共享的幾項」從 localStorage 改存這裡。
# 哪幾項該共享、哪幾項不該（個人篩選搬上來會互相蓋掉），見 patch_store 的說明。


@router.get("/api/patch/status")
def patch_status(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """每一項現在有沒有、多大、誰在什麼時候更新的。"""
    import patch_store

    return patch_store.status(conn)


@router.get("/api/patch/blob/{key}")
def patch_blob_get(
    key: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """讀一項。還沒人匯入過回 404——前端要能分辨「沒有」跟「壞掉」。"""
    from urllib.parse import quote as _quote

    import patch_store

    try:
        row = patch_store.get(conn, key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not row:
        raise HTTPException(404, "還沒有這一項")
    return Response(
        content=bytes(row["body"]),
        media_type=row["content_type"] or "application/octet-stream",
        headers={
            "X-Updated-At": row["updated_at"] or "",
            "X-Updated-By": _quote(row["updated_by"] or ""),  # HTTP 標頭只吃 latin-1，中文帳號要編碼
            "X-File-Name": _quote(row["file_name"] or ""),
        },
    )


@router.put("/api/patch/blob/{key}")
async def patch_blob_put(
    key: str,
    request: Request,
    file_name: str = "",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """覆蓋寫入一項。太大就明講，不要靜默失敗。"""
    import patch_store

    body = await request.body()
    try:
        return patch_store.put(
            conn, key, body,
            content_type=request.headers.get("content-type"),
            file_name=file_name or None,
            by=session["username"] if session else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/api/patch/blob/{key}")
def patch_blob_delete(
    key: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """清掉一項（對應畫面上的「清除記憶」）。"""
    import patch_store

    try:
        return patch_store.delete(conn, key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ===== 弱點：解析成一筆一筆，才查得動單台（2026-09-21）=====
# 使用者：「以後每一台就可以看到有修過什麼弱點了吧? 多一個弱點管理紀錄」
# patch_store 存的是整包 Excel（給那支工具還原畫面用），查不了單台；
# 這裡解析成列存進 vuln_finding，給資產頁與差異比對用。兩件事不衝突。


@router.post("/api/vulns/import")
def vulns_import(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把目前存著的那份 Excel 解析成一筆一筆（匯入成功後由工具呼叫）。"""
    import patch_store
    import vuln_import

    row = patch_store.get(conn, "workbook")
    if not row:
        raise HTTPException(400, "還沒有匯入過弱點報告")
    try:
        return vuln_import.import_workbook(
            conn, bytes(row["body"]), row["file_name"],
            session["username"] if session else None)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"解析失敗：{exc}") from exc


@router.get("/api/vulns/batches")
def vulns_batches(
    limit: int = 20,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯入批次。以**匯入時間**排序，不以檔名或週次——同一週匯兩次就是兩批。"""
    import vuln_import

    return {"items": vuln_import.batches(conn, limit)}


@router.get("/api/vulns/by-asset")
def vulns_by_asset(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """某一台的弱點：還沒結的、以及修過的。"""
    import vuln_import

    return vuln_import.by_asset(conn, asset_serial)


@router.get("/api/vulns/diff")
def vulns_diff(
    new_id: int, old_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """兩批次的差異：新增／已修復／無法判定（這次沒掃到那台）三類。"""
    import vuln_import

    return vuln_import.diff(conn, new_id, old_id)


@router.get("/api/vulns/unmatched")
def vulns_unmatched(
    batch_id: int | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """對不回資產清冊的主機——這本身就是發現。"""
    import vuln_import

    return {"items": vuln_import.unmatched_hosts(conn, batch_id)}


def _eos_extra(hit: dict | None) -> dict:
    """W221-005／006：每個 EOS 項目多帶原因碼／標籤、官方原文類型、可延伸、是否可信。
    status 維持舊值（相容），原因另外給。"""
    import eos

    a = eos.assess(hit)
    return {"reason": a["reason"], "reason_label": a["reason_label"], "eos_kind": a["eos_kind"],
            "extendable": a["extendable"], "plan_available": a["plan_available"],
            "entitlement_state": a["entitlement_state"], "entitlement_pending": a["entitlement_pending"],
            "trusted": a["trusted"], "downgraded": a["downgraded"]}


@router.get("/api/eos/summary")
def eos_summary(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """軟硬體 EOS 總覽：全站有幾台的 OS／硬體已過 EOS、一年內到期、還沒查到官方日期。

    只算有效資產（排除退役）——道理跟 composition() 一樣：停用/報廢的機器沒有
    「還要不要換」的問題，混進來會讓「還有幾台要處理」這個數字失真。
    """
    import eos
    import manage_state
    import normalize

    rows = conn.execute(
        "SELECT asset_serial, hostname, ip, os, device_model, asset_status, asset_name, asset_purpose "
        "FROM hardware"
    ).fetchall()
    # 人工歸類覆寫（使用者 2026-08-12）：有些設備技術上偵測到的 OS 沒錯（例：Palo Alto
    # Panorama／Finika 底層真的跑 CentOS/Linux），但維護權責歸網路設備組，優先於自動分類。
    overrides = {
        r["canonical"]: r["category"]
        for r in conn.execute("SELECT canonical, category FROM eos_category_override")
    }

    def bump(d, k):
        d[k] = d.get(k, 0) + 1

    # [口徑] 2026-09-20：磚塊與每一列的「資產數」以前逐筆算（同一台多筆登記會被算好幾次）。
    # 畫面寫「台」就要逐台——狀態統計與每一列改成收集正典 machine_key，最後再換成數量。
    import system_stats as _ss

    def bump_m(d, k, mk):
        d.setdefault(k, set()).add(mk)

    # 使用者 2026-08-12：伺服器/主機 OS 跟網路設備韌體要分開，因為要找不同的人維護；
    # 規則/字典都認不出來的（裸版本號、iDRAC 這類設備類型標籤）獨立一桶「未分類」——
    # 這桶正是使用者最需要人工補資料的清單，不能悄悄混進另外兩桶假裝有分類。
    # 使用者 2026-08-13：MySQL 這類資料庫軟體常被登打人員誤填進 OS 欄位，
    # 技術上偵測到的字串沒錯，但維護權責是「軟體」不是「作業系統」——跟 host_os/firmware
    # 一樣走人工覆寫（eos_category_override），不做自動關鍵字判斷，因為「哪些字串算軟體」
    # 沒有一致規則，交給人工補比較不會誤判。
    # 使用者 2026-08-13 追加「資訊不足」：像「網路設備」「儲存設備」「客製化系統」這種
    # 登打人員填的泛用設備類型標籤，不是規則認不出來（那是「未分類」的情境），是原始
    # 資料本身就沒有可辨識的產品資訊，兩種情況分開才不會讓「未分類」清單裡混進「查也沒用」
    # 的雜訊，稀釋掉真正還有機會補資料的項目。
    BUCKETS = ("host_os", "firmware", "software", "insufficient", "other")
    by_status: dict[str, dict[str, int]] = {b: {} for b in BUCKETS}
    items: dict[str, dict[str, dict]] = {b: {} for b in BUCKETS}
    hw_by_status: dict[str, int] = {}
    hw_items: dict[str, dict] = {}
    device_models_by_canonical: dict[str, dict[str, int]] = {}  # canonical os -> {device_model: 台數}

    for r in rows:
        if (r["asset_status"] or "").strip() in manage_state.RETIRED_STATUS:
            continue
        mk = _ss.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        if r["os"]:
            os_info = normalize.normalize_os(r["os"], conn, r["device_model"])
            canonical = os_info["canonical"]
            if os_info["product"] in normalize.HW_ROUTED_PRODUCTS:
                # 使用者 2026-08-13：iDRAC／Unisphere Central 這類跟硬體綁死的管理
                # 軟韌體，維護權責跟隨它管理的硬體本身，直接併進硬體側對應分類，
                # 不落在 os 側五桶的任何一桶。
                hit = eos.lookup_os_eos(canonical)
                status = eos.eos_status(hit["eos_date"]) if hit else "unknown"
                bump_m(hw_by_status, status, mk)
                hw_family = overrides.get(canonical, normalize.HW_ROUTED_PRODUCTS[os_info["product"]])
                entry = hw_items.setdefault(canonical, {
                    "name": canonical, "status": status,
                    "eos_date": hit["eos_date"] if hit else None,
                    "source_url": hit["source_url"] if hit else None,
                    "confidence": hit.get("confidence") if hit else None,
                    "note": hit.get("note") if hit else None,
                    **_eos_extra(hit),
                    "overridden": canonical in overrides,
                    "count": 0, "_m": set(), "_m": set(),
                    "family": hw_family, "vendor": os_info["vendor"],
                    "model_confirmed": True,  # iDRAC/Unisphere Central 這類是 os 值直接對到具體
                                               # 產品規則，不是靠 hint 猜的，永遠算 confirmed。
                    # 使用者 2026-08-13 實際發現：這批雖然顯示在硬體型號分頁，但 canonical
                    # 其實是 normalize_os() 算出來的，不是 normalize_model()——前端如果只憑
                    # 「顯示在硬體分頁」就假設是 device_model 種類去存改名覆寫，會存錯種類、
                    # 存了但查不到（跟「N台→」篩到0筆是同一個病灶）。這裡明講真正的種類，
                    # 前端改名時要用這個而不是自己用 _origin 猜。
                    "kind": "os",
                })
                entry["_m"].add(mk)
                continue
            bucket = overrides.get(canonical) or normalize.os_category(os_info["product"], canonical)
            hit = eos.lookup_os_eos(canonical)
            status = eos.eos_status(hit["eos_date"]) if hit else "unknown"
            bump_m(by_status[bucket], status, mk)
            family, linux_distro = normalize.os_family(os_info["product"], canonical)
            # [2026-09-20 使用者拍板：「類別要一樣，改第二」] 作業系統分頁的第二層改用
            # 全站正典的 OS 類型（pipeline._os_type，AIX 與 IBM i 分開、含 OpenShift 節點等），
            # 不再用 EOS 自己那套（Linux／Windows／AIX/Unix／VMware）。第一層分頁不動。
            if bucket == "host_os":
                try:
                    import pipeline as _pl

                    family = _pl._os_type(canonical, None) or family
                except Exception:  # noqa: BLE001 - 分類失敗就退回原本的 family，不讓整頁掛掉
                    pass
            # 使用者 2026-08-13 要求：「未分類」清單裡，raw OS 值認不出來時，附上
            # 系統推測（見 _identity_guess()：OS 規則猜不到就換型號規則，不是只
            # 用 OS 規則）。只在第一次建這個 canonical 的項目時算一次——⚠️ 這對
            # 「N/A」這種很多不同資產共用同一個 raw 值的群組是已知限制：只反映
            # 群組裡第一台的猜測，不代表整組都一樣。要看每一台各自的推測，
            # 點「N 台 →」進資產清單，那邊逐列都有算（list_assets 的
            # identity_guess 欄位），不會被群組彙總擋住。
            suggested = None
            if bucket == "other":
                suggested, _, _ = _identity_guess(r, conn)
            entry = items[bucket].setdefault(canonical, {
                "name": canonical, "status": status,
                "eos_date": hit["eos_date"] if hit else None,
                "source_url": hit["source_url"] if hit else None,
                "confidence": hit.get("confidence") if hit else None,
                "note": hit.get("note") if hit else None,
                **_eos_extra(hit),
                "count": 0, "_m": set(),
                "family": family, "linux_distro": linux_distro, "vendor": os_info["vendor"],
                "overridden": canonical in overrides,
                "suggested": suggested,
            })
            entry["_m"].add(mk)
            # 使用者 2026-08-12：光看「15.7(3)M8」這種裸版本號看不出是什麼設備，
            # 規則認不出來時尤其如此——附上這個版本掛在哪些機型上，人一看機型就知道
            # 這是什麼（例：機型是 Cisco Catalyst，就知道 15.7(3)M8 是 IOS 版本）。
            if r["device_model"]:
                dm_canonical = normalize.normalize_model(r["device_model"], conn, _model_hint(r))["canonical"]
                bump(device_models_by_canonical.setdefault(canonical, {}), dm_canonical)
        if r["device_model"]:
            model_info = normalize.normalize_model(r["device_model"], conn, _model_hint(r))
            canonical = model_info["canonical"]
            hw_family, hw_vendor = normalize.hardware_family(model_info["vendor"], canonical)
            if canonical in overrides:
                hw_family = overrides[canonical]  # 人工覆寫優先於自動判斷（跟 os 側同一張表）
            # 使用者 2026-08-13：「虛擬化」（VMware/KVM/Hyper-V 虛擬機、平台不明的裸
            # 「(VM)」標記）不是實體硬體，根本沒有 EOS 這回事可查——這台機器真正該追的
            # EOS 是它的作業系統，已經算在 host_os 那邊，所以不計入頭部四格統計，避免
            # 同一台機器的「查不到」被算兩次。但使用者後續要求清單本身還是要看得到
            # （純對照用途，「這批資產裡有哪些是虛擬機」），所以項目照樣建立，只是
            # 不 bump 統計數字，也不查 EOS（虛擬機沒有查的意義，直接標未公佈）。
            hit = None if hw_family == "虛擬化" else eos.lookup_hardware_eos(canonical)
            status = eos.eos_status(hit["eos_date"]) if hit else "unknown"
            if hw_family != "虛擬化":
                bump_m(hw_by_status, status, mk)
            entry = hw_items.setdefault(canonical, {
                "name": canonical, "status": status,
                "eos_date": hit["eos_date"] if hit else None,
                "source_url": hit["source_url"] if hit else None,
                "confidence": hit.get("confidence") if hit else None,
                "note": hit.get("note") if hit else None,
                **_eos_extra(hit),
                "overridden": canonical in overrides,
                "count": 0, "_m": set(),
                "family": hw_family, "vendor": hw_vendor,
                # 使用者 2026-08-13 要求：硬體型號分頁也要看得出這個型號名是規則直接從
                # device_model 讀到的（confirmed）還是靠 hint（資產名稱/資產用途）
                # 猜出來的——跟 /api/assets 的 identity_guess_confirmed 同一個精神，
                # 只是這裡是彙總層級，只記第一筆建這個 canonical 的判斷方式。
                "model_confirmed": model_info["method"] in ("rule", "alias"),
                "kind": "device_model",  # 真的是從 normalize_model() 算出來的，跟上面
                                          # HW_ROUTED_PRODUCTS 分支（kind="os"）要分清楚。
            })
            entry["_m"].add(mk)

    for bucket in BUCKETS:
        for canonical, entry in items[bucket].items():
            models = device_models_by_canonical.get(canonical, {})
            entry["device_models"] = [
                m for m, _ in sorted(models.items(), key=lambda kv: -kv[1])
            ]

    def sort_items(d: dict) -> list[dict]:
        order = {"expired": 0, "upcoming": 1, "unknown": 2, "ok": 3}
        for x in d.values():                      # 台數＝去重後的機器數（見 bump_m）
            if "_m" in x:
                x["count"] = len(x.pop("_m"))
        return sorted(d.values(), key=lambda x: (order.get(x["status"], 9), -x["count"]))

    def st(d: dict) -> dict:                      # 狀態統計：set → 台數
        return {k: len(v) if isinstance(v, set) else v for k, v in d.items()}

    import pipeline as _pl2

    return {
        # 第二層分類的順序（跟漏斗、OS×環境同一份），前端照這個排
        "os_order": list(_pl2.OS_ORDER),
        "host_os": {"by_status": st(by_status["host_os"]), "items": sort_items(_merge_patch_versions(items["host_os"]))},
        "firmware": {"by_status": st(by_status["firmware"]), "items": sort_items(_merge_patch_versions(items["firmware"]))},
        "software": {"by_status": st(by_status["software"]), "items": sort_items(_merge_patch_versions(items["software"]))},
        "insufficient": {"by_status": st(by_status["insufficient"]), "items": sort_items(_merge_patch_versions(items["insufficient"]))},
        "other": {"by_status": st(by_status["other"]), "items": sort_items(_merge_patch_versions(items["other"]))},
        "hardware": {"by_status": st(hw_by_status), "items": sort_items(hw_items)},
    }


@router.get("/api/eos/export")
def export_eos_summary(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """匯出 EOS 頁面清單，每個分類一個分頁，逐資產列出（使用者 2026-08-13 要求，
    給 OS 帳號盤點交 AP 單位統計用途，AP部門/AP負責人/SP負責人依 D27 對應：
    使用單位=AP部門、使用者=AP負責人（AP User）、保管者=SP負責人（SP保管者））。

    跟 /api/normalize/pending/export 不一樣：那支只匯出「查不到」待補的清單，用來
    回填別名。這支是逐資產列出「這台在哪個分類」，一台資產若 OS 跟設備機型分屬
    不同分類（例：OS 是「軟體」但設備機型是「硬體型號」），會各自出現在對應分頁
    ——這是判斷依據本來就不同的兩件事，不能只選一邊。
    """
    import eos
    import manage_state
    import normalize

    PAGE_ORDER = ["作業系統", "網路設備", "硬體型號", "軟體", "資訊不足", "未分類"]
    OS_BUCKET_TO_PAGE = {
        "host_os": "作業系統", "firmware": "網路設備", "software": "軟體",
        "insufficient": "資訊不足", "other": "未分類",
    }
    STATUS_LABEL = {"expired": "已過 EOS", "upcoming": "一年內到期", "ok": "尚在支援期", "unknown": "未公佈"}
    # 使用者 2026-08-13 要求：前面欄位要對齊公司既有的專案工作表單格式（項目/階段/
    # 工作項目/主機IP/負責單位/負責人/開始日/結束日/工期(天)/狀態/備註/聯繫單單號），
    # 方便直接複製貼上去用；沒有資料可填的規劃性欄位（項目/階段/開始日/結束日/
    # 工期(天)/狀態）留空給人工填，不亂塞值。「負責單位」「負責人」先帶 AP部門/
    # AP負責人（跟後面詳細欄位同一個來源，方便對照）。其他細節資訊放後面。
    TEMPLATE_COLUMNS = [
        "項目", "階段", "工作項目", "主機/IP", "負責單位", "負責人",
        "開始日", "結束日", "工期(天)", "狀態", "備註", "聯繫單單號",
    ]
    DETAIL_COLUMNS = [
        "資產狀態", "主機名稱", "IP", "作業系統", "設備機型",
        "EOS狀態", "EOS日期", "可信度", "來源", "AP部門", "AP負責人", "SP負責人",
    ]
    COLUMNS = TEMPLATE_COLUMNS + DETAIL_COLUMNS

    rows = conn.execute(
        "SELECT asset_serial, hostname, ip, os, device_model, asset_status, "
        "asset_name, asset_purpose, usage_unit, user_name, custodian FROM hardware"
    ).fetchall()
    overrides = {
        r["canonical"]: r["category"]
        for r in conn.execute("SELECT canonical, category FROM eos_category_override")
    }

    sheets: dict[str, list] = {p: [] for p in PAGE_ORDER}

    def base_row(r):
        host_ip = " / ".join(v for v in (r["hostname"], r["ip"]) if v)
        return [
            "", "", "", host_ip, r["usage_unit"] or "", r["user_name"] or "",
            "", "", "", "", "", "",
            r["asset_status"] or "", r["hostname"] or "", r["ip"] or "",
            r["os"] or "", r["device_model"] or "",
        ]

    def eos_cols(hit):
        status = eos.eos_status(hit["eos_date"]) if hit else "unknown"
        return [
            STATUS_LABEL.get(status, status),
            hit["eos_date"] if hit else "",
            hit.get("confidence") if hit else "",
            hit.get("source_url") if hit else "",
        ]

    def tail(r):
        return [r["usage_unit"] or "", r["user_name"] or "", r["custodian"] or ""]

    for r in rows:
        if (r["asset_status"] or "").strip() in manage_state.RETIRED_STATUS:
            continue
        # OS 側跟設備機型側各自獨立判斷分類跟 EOS 查詢結果——一台資產若兩邊分屬不同
        # 分類（例：OS 是「軟體」但設備機型是「硬體型號」），或兩邊 EOS 結果不同
        # （這正是這次要補的欄位重點），都不能只選一邊代表，各自輸出一列。
        if r["os"]:
            os_info = normalize.normalize_os(r["os"], conn, r["device_model"])
            canonical = os_info["canonical"]
            if os_info["product"] in normalize.HW_ROUTED_PRODUCTS:
                hw_family = overrides.get(canonical, normalize.HW_ROUTED_PRODUCTS[os_info["product"]])
                page = "網路設備" if hw_family == "網路設備" else "硬體型號"
            else:
                bucket = overrides.get(canonical) or normalize.os_category(os_info["product"], canonical)
                page = OS_BUCKET_TO_PAGE[bucket]
            hit = eos.lookup_os_eos(canonical)
            sheets[page].append(base_row(r) + eos_cols(hit) + tail(r))
        if r["device_model"]:
            model_info = normalize.normalize_model(r["device_model"], conn, _model_hint(r))
            hw_canonical = model_info["canonical"]
            hw_family, _ = normalize.hardware_family(model_info["vendor"], hw_canonical)
            if hw_canonical in overrides:
                hw_family = overrides[hw_canonical]
            page = "網路設備" if hw_family == "網路設備" else "硬體型號"
            hit = None if hw_family == "虛擬化" else eos.lookup_hardware_eos(hw_canonical)
            sheets[page].append(base_row(r) + eos_cols(hit) + tail(r))

    wb = Workbook()
    wb.remove(wb.active)
    for page in PAGE_ORDER:
        ws = wb.create_sheet(page)
        ws.append(COLUMNS)
        for row_out in sheets[page]:
            ws.append(row_out)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"eos_full_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/api/eos/category-override")
def upsert_eos_category_override(
    body: EosCategoryOverrideBody,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """人工把某個 canonical 名稱強制指定分類，優先於自動判斷。

    給 Palo Alto Panorama／Finika 這類「技術上偵測到的 OS 沒錯，但維護權責歸網路設備組」
    的情況用——這不是名稱認錯（normalize_alias 的情境），是分類權責跟技術判斷對不上。
    os 側跟 hardware 側共用這張表、共用這支端點，category 值兩邊字彙不同（os 側是
    host_os/firmware/other；hardware 側是網路設備/主機設備/儲存設備/虛擬化/其他），
    eos_summary() 分別在各自的迴圈用各自的字彙解讀，這裡只驗證值屬於兩邊之一即可。
    """
    valid = {
        "host_os", "firmware", "software", "insufficient", "other",
        "網路設備", "主機設備", "儲存設備", "虛擬化", "其他",
    }
    if body.category not in valid:
        raise HTTPException(400, f"category 不支援這個值：{body.category}")
    conn.execute(
        "INSERT INTO eos_category_override (canonical, category, overridden_by) VALUES (?, ?, ?) "
        "ON CONFLICT(canonical) DO UPDATE SET category = excluded.category, "
        "overridden_by = excluded.overridden_by, overridden_at = datetime('now','localtime')",
        (body.canonical, body.category, session["username"]),
    )
    conn.commit()
    return {"ok": True}


@router.delete("/api/eos/category-override/{canonical}")
def delete_eos_category_override(
    canonical: str,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """撤銷覆寫，改回系統自動判斷的分類。"""
    cur = conn.execute("DELETE FROM eos_category_override WHERE canonical = ?", (canonical,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "查無此筆覆寫")
    return {"ok": True}


@router.get("/api/vuln-notify/settings")
def vuln_notify_get_settings(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """寄信設定＋**還缺什麼**。畫面要能一眼看出為什麼現在不會寄。"""
    import vuln_notify

    try:
        cfg = vuln_notify.load_settings(conn)
        corrupt = None
    except vuln_notify.SettingsCorrupt as exc:
        # 壞掉跟沒設定要分開：沒設定是去填，壞掉是去看誰把它寫壞了。
        cfg, corrupt = {}, str(exc)
    h, m = vuln_notify.send_time_of(cfg)
    return {
        "settings": _vn_public(cfg),
        "corrupt": corrupt,
        "missing": vuln_notify.check_settings(cfg),
        "ad_ready": vuln_notify.ad_ready(conn),
        "send_time": f"{h:02d}:{m:02d}",
        "buckets": list(vuln_notify.buckets_of(cfg)),
        "last_runs": vuln_notify.last_runs(conn, 10),
    }


@router.put("/api/vuln-notify/settings")
def vuln_notify_put_settings(
    body: VulnNotifySettings,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """存設定。**缺必填卻按啟用時直接擋下並指名缺哪一項**——
    讓它存成「啟用但不會寄」的狀態，畫面會顯示開著、實際一封都不寄，最難查。"""
    import vuln_notify

    # 只濾掉 None（沒給），**空清單／空字串要留著**——
    # 否則使用者把「只通知架構部」清掉之後，舊設定還在 DB 裡繼續生效，
    # 畫面顯示「全部」但實際只寄給架構部。
    cfg = {k: v for k, v in body.model_dump().items() if v is not None}
    missing = vuln_notify.check_settings(cfg)
    if cfg.get("enabled") and missing:
        raise HTTPException(400, f"要啟用就得先填：{'、'.join(missing)}")
    vuln_notify.save_settings(conn, cfg)
    return {"ok": True, "settings": _vn_public(cfg), "missing": missing}


@router.get("/api/vuln-notify/recipients")
def vuln_notify_recipients(
    depts: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """可以挑的單位與人（來源是 AD 名單）。depts 用逗號分隔，給了就只回那幾個單位的人。"""
    import vuln_notify

    picked = [d.strip() for d in (depts or "").split(",") if d.strip()]
    return {"departments": vuln_notify.departments(conn),
            "people": vuln_notify.people_of(conn, picked or None)}


@router.post("/api/vuln-notify/preview")
def vuln_notify_preview(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """預覽會寄給誰、幾筆、誰沒人收。**不寄、也不動 last_bucket。**

    固定用月報（週報的超集）而不是看今天是不是寄送日：人會在平日按這顆，
    回「今天不是寄送日」等於預覽在需要它的時候不能用。
    """
    import vuln_notify

    return vuln_notify.run(conn, schedule="monthly", dry_run=True)


@router.post("/api/vuln-notify/run")
def vuln_notify_run_now(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """立刻寄一期（月報內容）。第一次鋪底、或排程當天出事要補寄時用。

    記成 schedule='manual'：**不可以佔掉當天排程那一期的位置**，
    否則手動補一次就會讓當天的自動班表被當成做過了。
    """
    import vuln_notify
    from datetime import datetime as _dt

    now = _dt.now()
    r = vuln_notify.run(conn, now.date(), "monthly")
    vuln_notify.mark_run(conn, now.strftime("%Y-%m-%d"), "manual", r, now)
    return r


@router.get("/api/vuln-notify/logs")
def vuln_notify_logs(
    limit: int = 200,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """寄送紀錄：那天寄到哪去了、誰沒收到、SMTP 回了什麼。"""
    import vuln_notify

    return {"items": vuln_notify.logs(conn, min(max(limit, 1), 1000)),
            "runs": vuln_notify.last_runs(conn, 50)}


# ===== Q95 EOS 到期自動通知（硬體 SP／作業系統 AP）=====
# 仿上面的弱點通知：SMTP 沿用弱點通知那一份設定；門檻 180／365 天存設定、可改；
# 預設關閉；寄信與改設定限管理員，預覽與看紀錄登入即可。

class EosNotifySettings(BaseModel):
    enabled: bool = False
    send_time: str | None = None
    buckets: list[int] | None = None
    fallback_to: str | None = None
    always_cc: str | None = None
    subject_prefix: str | None = None
    manager_cc: bool = True


def _eos_admin_only(session, conn) -> None:
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員（admin 帳號）能改 EOS 通知的設定或立刻寄信")


@router.get("/api/eos-notify/settings")
def eos_notify_get_settings(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """設定＋還缺什麼。畫面要能一眼看出為什麼現在不會寄。"""
    import eos_notify
    import vuln_notify

    try:
        cfg, corrupt = eos_notify.load_settings(conn), None
    except eos_notify.SettingsCorrupt as exc:
        cfg, corrupt = {}, str(exc)
    try:
        missing = eos_notify.mail_missing(conn, cfg)
    except vuln_notify.SettingsCorrupt as exc:
        missing, corrupt = [f"SMTP 設定壞掉：{exc}"], corrupt or str(exc)
    h, m = eos_notify.send_time_of(cfg)
    return {
        "settings": _vn_public(cfg),
        "corrupt": corrupt,
        "missing": missing,
        "ad_ready": vuln_notify.ad_ready(conn),
        "send_time": f"{h:02d}:{m:02d}",
        "buckets": list(eos_notify.buckets_of(cfg)),
        "last_runs": eos_notify.last_runs(conn, 10),
        "can_edit": _can_edit_nav(session, conn),
    }


@router.put("/api/eos-notify/settings")
def eos_notify_put_settings(
    body: EosNotifySettings,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """存設定（限管理員）。缺 SMTP 卻按啟用 → 擋下並指名；門檻不是兩個不同的正整數 → 擋下。"""
    import eos_notify
    import vuln_notify

    _eos_admin_only(session, conn)
    cfg = {k: v for k, v in body.model_dump().items() if v is not None}
    if "buckets" in cfg:
        try:
            cfg["buckets"] = list(eos_notify.validate_buckets(cfg["buckets"]))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    try:
        missing = eos_notify.mail_missing(conn, cfg)
    except vuln_notify.SettingsCorrupt as exc:
        raise HTTPException(400, f"SMTP 設定壞掉，先到寄信設定修好：{exc}")
    if cfg.get("enabled") and missing:
        raise HTTPException(400, f"要啟用就得先填：{'、'.join(missing)}")
    eos_notify.save_settings(conn, cfg)
    return {"ok": True, "settings": _vn_public(cfg), "missing": missing}


@router.get("/api/eos-notify/preview")
def eos_notify_preview(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """預覽（乾跑）：會寄給誰、各幾筆、哪些沒人收（含原因）。不寄、不寫紀錄、不動 last_bucket。"""
    import eos_notify

    return eos_notify.preview(conn)


@router.post("/api/eos-notify/run")
def eos_notify_run_now(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """立刻寄一期（限管理員）。記成 schedule='manual'，不佔掉當月 1 號排程那一期的位置。"""
    import eos_notify
    from datetime import datetime as _dt

    _eos_admin_only(session, conn)
    now = _dt.now()
    r = eos_notify.run(conn, now.date(), "monthly")
    eos_notify.mark_run(conn, now.strftime("%Y-%m-%d"), "manual", r, now)
    return r


@router.get("/api/eos-notify/logs")
def eos_notify_logs(
    limit: int = 200,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """寄送紀錄：那天寄到哪去了、誰沒收到、SMTP 回了什麼。"""
    import eos_notify

    return {"items": eos_notify.logs(conn, min(max(limit, 1), 1000)),
            "runs": eos_notify.last_runs(conn, 50)}


__all__ = [
    'patch_status',
    'patch_blob_get',
    'patch_blob_put',
    'patch_blob_delete',
    'vulns_import',
    'vulns_batches',
    'vulns_by_asset',
    'vulns_diff',
    'vulns_unmatched',
    'eos_summary',
    'export_eos_summary',
    'upsert_eos_category_override',
    'delete_eos_category_override',
    'vuln_notify_get_settings',
    'vuln_notify_put_settings',
    'vuln_notify_recipients',
    'vuln_notify_preview',
    'vuln_notify_run_now',
    'vuln_notify_logs',
    'eos_notify_get_settings',
    'eos_notify_put_settings',
    'eos_notify_preview',
    'eos_notify_run_now',
    'eos_notify_logs',
]
