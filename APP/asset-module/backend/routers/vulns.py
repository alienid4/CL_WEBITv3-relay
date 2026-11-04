from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間
from pydantic import ConfigDict, Field

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
            "trusted": a["trusted"], "downgraded": a["downgraded"],
            "trust": a["trust"], "trust_label": a["trust_label"], "date_precision": a["date_precision"]}


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

    # [層次契約] Codex 64／004：EOS「台」＝machine_key（主機身份，逐台），整機／元件／管理口各自成一台、各自查自己的
    # OS／機型 EOS，不把整套 EOS 複製到元件。這裡只「揭露」每個項目底下的機器在對帳橋的角色（count_bridge；
    # 關聯來自 4-5 機櫃規則＋人工「合併為同一台」決定，不是資料庫存的父子欄位），讓人看得出有多少是元件／管理口。
    # 對帳橋算不出來就全部標 unknown，不阻擋 EOS。
    try:
        import count_bridge as _cb

        _role_by_serial = {i["asset_serial"]: i["role"] for i in _cb.compute(conn)["items"]}
    except Exception:  # noqa: BLE001 - 揭露用，失敗不擋 EOS 統計
        _role_by_serial = {}
    _roles_by_mk: dict[str, set] = {}
    for _r in rows:
        if (_r["asset_status"] or "").strip() in manage_state.RETIRED_STATUS:
            continue
        _roles_by_mk.setdefault(_ss.machine_key(_r["hostname"], _r["ip"], _r["asset_serial"]), set()).add(
            _role_by_serial.get(_r["asset_serial"]) or "unknown")

    def _layer_of(mk) -> str:
        rs = _roles_by_mk.get(mk) or {"unknown"}
        return next(iter(rs)) if len(rs) == 1 else "mixed"

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
                ms = x.pop("_m")
                x["count"] = len(ms)
                lay: dict[str, int] = {}
                for k in ms:
                    lay[_layer_of(k)] = lay.get(_layer_of(k), 0) + 1
                x["layers"] = lay             # 角色→台數（加總＝count；mixed＝同一台的多筆登記角色不同）
                x["layer_basis"] = "rule"     # 規則推定（4-5 機櫃規則＋人工合併決定），不是資料庫存的父子欄位
        return sorted(d.values(), key=lambda x: (order.get(x["status"], 9), -x["count"]))

    def st(d: dict) -> dict:                      # 狀態統計：set → 台數
        return {k: len(v) if isinstance(v, set) else v for k, v in d.items()}

    import pipeline as _pl2

    return {
        # 第二層分類的順序（跟漏斗、OS×環境同一份），前端照這個排
        "os_order": list(_pl2.OS_ORDER),
        "meta": {"unit": "machine_key（主機身份，逐台）",
                 "layers": "每個項目的 layers＝底下機器在對帳橋的角色台數；整機／元件／管理口各自一台、各自查自己的 EOS，"
                           "不複製整套 EOS 到元件；關聯為規則推定，無可靠父子欄位時為 unknown"},
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
    # 可信度一律用統一語意（已確認／推定／未知，見 eos.TRUST_LABEL）；後面追加的欄位只加不改，原核對碼與證據另列
    DETAIL_COLUMNS = [
        "資產狀態", "主機名稱", "IP", "作業系統", "設備機型",
        "EOS狀態", "EOS日期", "可信度", "來源", "AP部門", "AP負責人", "SP負責人",
        "日期精度", "來源核對碼", "證據完整度", "查證人", "查證日期",
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

    import eos_evidence
    _evs = eos_evidence.evidence_map(conn)

    def eos_cols(hit):
        status = eos.eos_status(hit["eos_date"]) if hit else "unknown"
        return [
            STATUS_LABEL.get(status, status),
            hit["eos_date"] if hit else "",
            eos.TRUST_LABEL[eos.trust_level(hit)],
            hit.get("source_url") if hit else "",
        ]

    def tail(r):
        return [r["usage_unit"] or "", r["user_name"] or "", r["custodian"] or ""]

    def ev_cols(hit, kind):
        """批二：日期精度、原核對碼、官方證據完整度與查證人／日（查不到表項目就留空，不補）。"""
        if not hit:
            return ["", "", "", "", ""]
        ev = _evs.get((kind, hit.get("name")))
        st, _m = eos_evidence.evidence_state(hit, ev)
        return [eos.date_precision(hit.get("eos_date")), hit.get("confidence") or "", eos_evidence.STATE_LABEL[st],
                (ev or {}).get("verified_by") or "", (ev or {}).get("verified_on") or ""]

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
            sheets[page].append(base_row(r) + eos_cols(hit) + tail(r) + ev_cols(hit, "os"))
        if r["device_model"]:
            model_info = normalize.normalize_model(r["device_model"], conn, _model_hint(r))
            hw_canonical = model_info["canonical"]
            hw_family, _ = normalize.hardware_family(model_info["vendor"], hw_canonical)
            if hw_canonical in overrides:
                hw_family = overrides[hw_canonical]
            page = "網路設備" if hw_family == "網路設備" else "硬體型號"
            hit = None if hw_family == "虛擬化" else eos.lookup_hardware_eos(hw_canonical)
            sheets[page].append(base_row(r) + eos_cols(hit) + tail(r) + ev_cols(hit, "hw"))

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
    import eos_evidence

    old = conn.execute("SELECT category FROM eos_category_override WHERE canonical = ?", (body.canonical,)).fetchone()
    try:
        conn.execute(
            "INSERT INTO eos_category_override (canonical, category, overridden_by) VALUES (?, ?, ?) "
            "ON CONFLICT(canonical) DO UPDATE SET category = excluded.category, "
            "overridden_by = excluded.overridden_by, overridden_at = datetime('now','localtime')",
            (body.canonical, body.category, session["username"]),
        )
        eos_evidence.log_mapping_change(conn, "eos_category_override", "update" if old else "create", {"canonical": body.canonical},
                                        dict(old) if old else None, {"category": body.category}, session["username"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"ok": True}


@router.delete("/api/eos/category-override/{canonical}")
def delete_eos_category_override(
    canonical: str,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """撤銷覆寫，改回系統自動判斷的分類。"""
    import eos_evidence

    old = conn.execute("SELECT category FROM eos_category_override WHERE canonical = ?", (canonical,)).fetchone()
    if old is None:
        raise HTTPException(404, "查無此筆覆寫")
    try:
        conn.execute("DELETE FROM eos_category_override WHERE canonical = ?", (canonical,))
        eos_evidence.log_mapping_change(conn, "eos_category_override", "delete", {"canonical": canonical}, dict(old), None,
                                        session["username"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
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
    r = eos_notify.run(conn, eos_notify.today_tpe(), "monthly")
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


# ===== EOS 通知與回覆追蹤（批次一，2.041.51）：邏輯都在 eos_follow.py =====
# 管理人員提出到期風險 → 通知 SP／AP → 追蹤回覆；保留必須附依據（公司沒有審查規範，不造簽核）。
# 寫入：一般帳號以上（USER_WRITE）；類別／催辦設定與示範資料＝管理員（ADMIN_WRITE）；登入停用時全部寫入 fail-closed（403）。
# 稽核（activity_log）與資料同一個交易；`database is locked` 回 503 請重試。讀取絕不寫庫。


class EosFollowBody(BaseModel):
    model_config = ConfigDict(extra="allow")        # 不認得的欄位不在這裡 422，由 handler 回 400 並列出被拒欄位
    item_key: str = Field(max_length=300)
    owner_ref: str = Field(max_length=200)
    reason: str | None = Field(default=None, max_length=1000)
    expected_version: int | None = Field(default=None, ge=0, le=2_000_000_000)
    succession_note: str | None = None
    reply_state: str | None = None
    committed_date: str | None = None
    reply_source: str | None = None
    hold_reason_class: str | None = None
    hold_reason_note: str | None = None
    hold_basis_text: str | None = None
    hold_evidence_ref: str | None = None
    hold_review_date: str | None = None
    blocker_confirm: str | None = None
    done_state: str | None = None
    done_evidence_ref: str | None = None


class EosFollowVoidBody(BaseModel):
    item_key: str = Field(max_length=300)
    owner_ref: str = Field(max_length=200)
    reason: str = Field(max_length=1000)
    expected_version: int | None = Field(default=None, ge=0, le=2_000_000_000)
    succession_note: str | None = Field(default=None, max_length=1000)


class EosFollowSettingsBody(BaseModel):
    reason_classes: list[dict]
    review_default_months: int = 6
    max_horizon_months: int = 24
    soon_days: int = 30
    reminder: dict | None = None


def _ef_call(fn, *a, **kw):
    import eos_follow as ef
    try:
        return fn(*a, **kw)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ef.Conflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise HTTPException(503, "資料庫忙碌中（database is locked），請稍後重試", headers={"Retry-After": "3"}) from exc
        raise


def _ef_gate(conn) -> None:
    """登入停用時假 session 是 admin，但追蹤紀錄需要真實身分才留得下稽核 → fail-closed。"""
    if auth_disabled(conn):
        raise HTTPException(403, "登入停用時不允許新增或修改 EOS 追蹤紀錄（需要真實身分才能留下稽核）")


def _actor_name(session, request) -> str:
    from routers.overlaps import _actor
    return _actor(session, request)


def _ef_hook(request, session, what: str):
    uname, ip = _actor_name(session, request), activity.client_ip(request)

    def hook(c, info):
        # 只記做了什麼（哪個項目、改了哪些欄位名），不記依據全文
        extra = f" {info.get('item_key', '')}｜{info.get('owner_ref', '')}｜{','.join(info.get('fields', []))}" if info.get("item_key") else ""
        activity.log(c, username=uname, ip=ip, method="EOS", status=200, action="eos_follow_" + info["op"], commit=False,
                     path=f"EOS 追蹤：{what}{extra}"[:280])
    return hook


@router.get("/api/eos/follow/overview")
def eos_follow_overview(flag: str | None = None, demo: bool = False, session: sqlite3.Row = Depends(require_auth),
                        conn: sqlite3.Connection = Depends(get_db)):
    """追蹤總覽：管理摘要磚（tiles）＋逐列（已編固定項次）。flag 指定時 rows 只含該旗標（下鑽），tiles 永遠是全量。**只讀。**"""
    import eos_follow as ef
    if flag and flag not in ef.TILE_LABEL:
        raise HTTPException(400, f"不認得的下鑽項目：{flag}")
    ov = _ef_call(ef.overview, conn, None, demo)
    level = auth.session_level(session)
    blocked = None
    if auth_disabled(conn):
        blocked = "登入停用中：為了留下可追溯的稽核，暫時不能新增或修改追蹤紀錄。"
    elif auth.level_rank(level) < auth.level_rank("user"):
        blocked = "你的帳號是「只能看」：可以查看追蹤狀況，不能記錄回覆或依據。需要一般帳號以上。"
    ov["rows"] = ef.drill(ov, flag)
    ov["flag"] = flag
    ov["can_write"] = blocked is None
    ov["write_block"] = blocked
    ov["can_admin"] = level == "admin" and not auth_disabled(conn)
    return ov


@router.get("/api/eos/follow/detail")
def eos_follow_detail(item_key: str = Query(max_length=300), owner_ref: str = Query(max_length=200), session: sqlite3.Row = Depends(require_auth),
                      conn: sqlite3.Connection = Depends(get_db)):
    """一列展開：該負責人的通知紀錄＋這筆的變更歷史（前後值）。只讀。"""
    import eos_follow as ef
    return _ef_call(ef.detail, conn, item_key, owner_ref)


@router.get("/api/eos/follow/settings")
def eos_follow_get_settings(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    import eos_follow as ef
    cfg, corrupt = ef._safe_settings(conn)
    return {"settings": cfg, "corrupt": corrupt, "disclaimer": ef.DISCLAIMER}


@router.put("/api/eos/follow/settings")
def eos_follow_put_settings(body: EosFollowSettingsBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                            conn: sqlite3.Connection = Depends(get_db)):
    """阻礙來源類別清單、重新檢視預設期間、催辦規則（預設關閉，只預覽不發送）。管理員。"""
    import eos_follow as ef
    _ef_gate(conn)
    return _ef_call(ef.save_settings, conn, body.model_dump(), _actor_name(session, request), _ef_hook(request, session, "設定"))


@router.get("/api/eos/follow/reminders")
def eos_follow_reminders(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """應催辦清單（唯讀預覽，不發送）。"""
    import eos_follow as ef
    return ef.reminders(_ef_call(ef.overview, conn, None, False))


@router.put("/api/eos/follow")
def eos_follow_upsert(body: EosFollowBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                      conn: sqlite3.Connection = Depends(get_db)):
    """新增或更正一筆追蹤（只動有送的欄位）。保留依據、完成證據的硬規則在後端強制；更正必須附理由。"""
    import eos_follow as ef
    _ef_gate(conn)
    sent = body.model_dump(exclude_unset=True)
    extra = sorted(set(sent) - set(ef.EDIT_FIELDS) - {"item_key", "owner_ref", "reason", "expected_version"})
    if extra:
        raise HTTPException(400, f"不能修改（或不認得）的欄位：{'、'.join(extra)}；EOS 日期等資料不能從追蹤修改")
    fields = {k: v for k, v in sent.items() if k in ef.EDIT_FIELDS}
    return _ef_call(ef.upsert, conn, body.item_key, body.owner_ref, fields, body.reason, _actor_name(session, request),
                    audit_hook=_ef_hook(request, session, "記錄／更正"), expected_version=body.expected_version)


@router.post("/api/eos/follow/void")
def eos_follow_void(body: EosFollowVoidBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                    conn: sqlite3.Connection = Depends(get_db)):
    """作廢追蹤（項目或負責人已不在目前清單、由新列接替時結案舊列）：附理由、留歷史、之後不能再編輯、不進待辦磚。"""
    import eos_follow as ef
    _ef_gate(conn)
    return _ef_call(ef.void, conn, body.item_key, body.owner_ref, body.reason, _actor_name(session, request),
                    audit_hook=_ef_hook(request, session, "作廢追蹤"), expected_version=body.expected_version,
                    succession_note=body.succession_note)


@router.post("/api/eos/follow/demo")
def eos_follow_demo_seed(request: Request, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """產生示範資料（獨立標記，不混進真實資料；頁面預設不顯示）。管理員。"""
    import eos_follow as ef
    _ef_gate(conn)
    return _ef_call(ef.seed_demo, conn, _actor_name(session, request), None, _ef_hook(request, session, "產生示範資料"))


@router.delete("/api/eos/follow/demo")
def eos_follow_demo_clear(request: Request, session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """一鍵清除示範資料。管理員。"""
    import eos_follow as ef
    _ef_gate(conn)
    return _ef_call(ef.clear_demo, conn, _actor_name(session, request), _ef_hook(request, session, "清除示範資料"))


# ===== EOS 批二（2.041.73）：官方證據、待重查、資產覆蓋率與未匹配、對照規則修改歷史（邏輯在 eos_evidence.py） =====
# 不改 EOS 表與可信度；證據只是記錄（沒有簽核流程）。寫入：一般帳號以上；登入停用時 fail-closed（403）；稽核與資料同交易。


class EosEvidenceBody(BaseModel):
    model_config = ConfigDict(extra="allow")        # 不認得的欄位由 handler 回 400 並列出
    kind: str = Field(max_length=10)
    name: str = Field(max_length=300)
    reason: str | None = Field(default=None, max_length=1000)
    expected_version: int | None = Field(default=None, ge=0, le=2_000_000_000)
    verified_by: str | None = None
    verified_on: str | None = None
    quote_text: str | None = None
    scope_text: str | None = None
    date_precision: str | None = None
    source_url: str | None = None


def _ee_call(fn, *a, **kw):
    import eos_evidence as ee
    try:
        return fn(*a, **kw)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ee.Conflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise HTTPException(503, "資料庫忙碌中（database is locked），請稍後重試", headers={"Retry-After": "3"}) from exc
        raise


def _ee_perm(conn, session) -> tuple[bool, str | None]:
    if auth_disabled(conn):
        return False, "登入停用中：為了留下可追溯的稽核，暫時不能新增或修改證據記錄。"
    if auth.level_rank(auth.session_level(session)) < auth.level_rank("user"):
        return False, "你的帳號是「只能看」：可以查看證據與覆蓋率，不能新增或更正證據。需要一般帳號以上。"
    return True, None


@router.get("/api/eos/coverage")
def eos_coverage(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """資產覆蓋率：逐台×（作業系統／硬體）落在 已確認／推定／未知／未匹配／未填 哪一格，加上未匹配清單。**只讀。**"""
    import eos_evidence as ee
    snap = ee.coverage_snapshot(conn)
    return {**snap["overview"], "snapshot": {"id": snap["id"], "computed_at": snap["computed_at"]}}


@router.get("/api/eos/coverage/rows")
def eos_coverage_rows(track: str | None = Query(default=None, max_length=10), bucket: str | None = Query(default=None, max_length=20),
                      item: str | None = Query(default=None, max_length=300), review: bool = Query(default=False),
                      page: int = Query(default=1, ge=1, le=100000), size: int = Query(default=0, ge=0, le=5000),
                      sort: str | None = Query(default=None, max_length=30), dir: str = Query(default="asc", pattern="^(asc|desc)$"),
                      session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """覆蓋率下鑽：與 /api/eos/coverage 同一份快照的逐台明細（item＝未匹配清單的標準名稱；review＝只看待核）。
    伺服器端分頁 page／size；size=0＝全部（匯出）。回 total＝符合條件的總筆數。**只讀。**"""
    import eos_evidence as ee
    if track and track not in ee.KINDS:
        raise HTTPException(400, "track 只能是 os 或 hw")
    if bucket and bucket not in ee.BUCKET_ORDER:
        raise HTTPException(400, f"不認得的分類：{bucket}")
    snap = ee.coverage_snapshot(conn)
    allr = ee.sort_rows(ee.coverage_drill(snap["rows"], track, bucket, item, review), sort, dir)
    return {"rows": ee.page_rows(allr, page, size), "total": len(allr), "page": page, "size": size,
            "snapshot": {"id": snap["id"], "computed_at": snap["computed_at"]}}


@router.get("/api/eos/evidence")
def eos_evidence_overview(flag: str | None = Query(default=None, max_length=20), session: sqlite3.Row = Depends(require_auth),
                          conn: sqlite3.Connection = Depends(get_db)):
    """EOS 表每個項目的證據完整度、待重查旗標、影響台數；tiles 與 rows 同一份計算（flag 指定時 rows 為該下鑽）。**只讀。**"""
    import eos_evidence as ee
    if flag and flag not in {k for k, _ in ee.EVIDENCE_TILES}:
        raise HTTPException(400, f"不認得的下鑽項目：{flag}")
    usage = ee.usage_of(ee.coverage_snapshot(conn)["rows"])
    ov = ee.evidence_overview(conn, None, usage)
    ov["rows"] = ee.evidence_drill(ov, flag)
    ov["flag"] = flag
    ov["can_write"], ov["write_block"] = _ee_perm(conn, session)
    return ov


@router.get("/api/eos/evidence/detail")
def eos_evidence_detail(kind: str = Query(max_length=10), name: str = Query(max_length=300), session: sqlite3.Row = Depends(require_auth),
                        conn: sqlite3.Connection = Depends(get_db)):
    """一個項目的表內資料、證據、變更歷史（前後值）。**只讀。**"""
    import eos_evidence as ee
    return _ee_call(ee.evidence_detail, conn, kind, name)


@router.put("/api/eos/evidence")
def eos_evidence_save(body: EosEvidenceBody, request: Request, session: sqlite3.Row = Depends(require_auth),
                      conn: sqlite3.Connection = Depends(get_db)):
    """新增或更正一筆官方證據（查證人／日期、原文引用、適用範圍、日期精度、來源網址）。不改 EOS 日期與可信度；更正必須附理由。"""
    import eos_evidence as ee
    if auth_disabled(conn):
        raise HTTPException(403, "登入停用時不允許新增或修改證據記錄（需要真實身分才能留下稽核）")
    sent = body.model_dump(exclude_unset=True)
    extra = sorted(set(sent) - set(ee.EVIDENCE_FIELDS) - {"kind", "name", "reason", "expected_version"})
    if extra:
        raise HTTPException(400, f"不能修改（或不認得）的欄位：{'、'.join(extra)}；EOS 日期與可信度不能從證據修改")
    fields = {k: v for k, v in sent.items() if k in ee.EVIDENCE_FIELDS}
    uname, ip = _actor_name(session, request), activity.client_ip(request)

    def hook(c, info):
        activity.log(c, username=uname, ip=ip, method="EOS", status=200, action="eos_evidence_" + info["op"], commit=False,
                     path=f"EOS 證據：{info['item_key']}｜{','.join(info.get('fields', []))}"[:280])
    return _ee_call(ee.save_evidence, conn, body.kind, body.name, fields, body.reason, uname, audit_hook=hook,
                    expected_version=body.expected_version)


@router.get("/api/eos/mapping-history")
def eos_mapping_history(session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)):
    """對照規則（別名、標準名稱覆寫、EOS 分類覆寫）的修改歷史：誰、何時、改前改後。**只讀。**"""
    import eos_evidence as ee
    return {"rows": ee.mapping_history(conn)}


@router.get("/api/eos/contract-shadow")
def eos_contract_shadow(flag: str | None = Query(default=None, max_length=30), session: sqlite3.Row = Depends(require_auth),
                        conn: sqlite3.Connection = Depends(get_db)):
    """EOS 批三：維護合約與官方 EOS 的影子比對差異報告（只比較，不改任何 EOS 日期、可信度、風險，不寄通知）。tiles 與 rows 同一份計算。**只讀。**"""
    import eos_contract as ec
    if flag and flag != "all" and flag not in ec.TYPES:
        raise HTTPException(400, f"不認得的差異類型：{flag}")
    return ec.shadow_overview(conn, flag)


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
    'eos_follow_overview',
    'eos_follow_detail',
    'eos_follow_get_settings',
    'eos_follow_put_settings',
    'eos_follow_reminders',
    'eos_follow_upsert',
    'eos_follow_void',
    'eos_follow_demo_seed',
    'eos_follow_demo_clear',
    'eos_coverage',
    'eos_coverage_rows',
    'eos_evidence_overview',
    'eos_evidence_detail',
    'eos_evidence_save',
    'eos_mapping_history',
    'eos_contract_shadow',
]
