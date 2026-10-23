from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()



@router.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response,
          conn: sqlite3.Connection = Depends(get_db)):
    user = auth.authenticate(conn, body.username, body.password)
    if user is None:
        # 帳號不存在／密碼錯誤都回同一句話，不透露「帳號不存在」讓人拿去枚舉帳號
        #
        # 但**紀錄要記下他打的帳號**：連續對同一個帳號試錯是要看得出來的訊號。
        # 對外不透露 ≠ 對內不記錄，這兩件事不衝突。
        _log_auth(conn, request, body.username, "login_failed", 401)
        raise HTTPException(401, "帳號或密碼錯誤")
    token, _expires_at = auth.issue_session(conn, user["id"])
    _log_auth(conn, request, user["username"], "login", 200)
    response.set_cookie(
        key=auth.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # 部署目標是內網IP走http（D11/D18已定案的內網簡化精神），改https要記得打開
        max_age=int(auth.SESSION_TTL.total_seconds()),
    )
    return {"username": user["username"]}


@router.post("/api/auth/logout")
def logout(
    request: Request,
    response: Response,
    session_token: str | None = Cookie(default=None),
    conn: sqlite3.Connection = Depends(get_db),
):
    if session_token:
        from db import delete_session

        who = auth.resolve_session(conn, session_token)
        _log_auth(conn, request, who["username"] if who else None, "logout", 200)
        delete_session(conn, session_token)
    response.delete_cookie(auth.SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/api/auth/me")
def me(session: sqlite3.Row = Depends(require_auth)):
    return {"username": session["username"]}


# ===== 在線人數與操作紀錄 =====
# 2026-08-26 使用者要求：「在左上角顯示在線人數，我要知道誰在用，LOG 紀錄也要」。
# 定義與取捨全部寫在 activity.py 檔頭，這裡只是薄 HTTP 層。

@router.get("/api/online")
def online_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    return activity.online_users(conn)


@router.get("/api/activity")
def activity_list_endpoint(
    username: str | None = None,
    action: str | None = None,
    since: str | None = None,
    limit: int = 200,
    offset: int = 0,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    return activity.list_log(conn, username=username, action=action, since=since,
                             limit=limit, offset=offset)


@router.get("/api/activity/summary")
def activity_summary_endpoint(
    days: int = 7,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if not 1 <= days <= 365:
        raise HTTPException(400, f"days 只接受 1~365，收到 {days}")
    return activity.log_summary(conn, days)


@router.get("/api/diagnostics")
def diagnostics_endpoint(
    note: str = "",
    desensitize: bool = True,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """萬用診斷包：出問題時一個動作產出「足以判斷問題在哪」的去識別化資料。

    不綁任何單一功能——核心負責環境快照/去識別化/打包，各功能自己註冊一段
    （見 diagnostics.register）。新功能加一個函式就會自動被收進來。

    desensitize 預設 True：公司資料不出這台機器是硬規則，要關掉必須明確指定。
    """
    import diagnostics as dg
    # 確保各功能的診斷外掛都已註冊（模組載入時才會 register）
    import auto_onboard  # noqa: F401
    import identity  # noqa: F401
    import manage_state  # noqa: F401
    import normalize  # noqa: F401
    import vcenter_autoimport  # noqa: F401

    return dg.collect(conn, note=note, desensitize=desensitize)


@router.get("/api/health/summary")
def health_summary(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """幾台完全沒問題、幾台要看、要看的卡在哪幾項。

    首頁那一句話用的就是這支。「要看」不是壞掉——它同時包含「機器有事」跟
    「資料要補」，兩者處置完全不同，所以回傳分開給 machine_bad / data_bad。
    """
    import health_check

    return health_check.summary(conn)


@router.get("/api/admin/auth-disabled")
def get_auth_disabled(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """登入有沒有被關掉，以及是**哪一種方式**關的。

    分開回報 env 與設定：環境變數蓋過設定，畫面上如果只給一個開關卻關不掉
    （因為是 env 開的），使用者會以為按鈕壞了。
    """
    import os as _os

    return {
        "disabled": auth_disabled(conn),
        "by_env": _os.environ.get(AUTH_DISABLED_ENV, "").strip() in ("1", "true", "TRUE", "yes"),
        "env_name": AUTH_DISABLED_ENV,
    }


@router.put("/api/admin/auth-disabled")
def set_auth_disabled(
    body: AuthDisabledBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """開關「停用登入」。

    ⚠️ 打開＝**這台機器上的資料對所有連得到的人公開**。所以打開時要打字確認，
    關掉不用——**讓安全的方向比較好走**是刻意的。

    只有已登入的人能打開（這支掛了 require_auth）：如果沒有這個限制，
    就會變成「未登入的人自己把登入關掉」，那是後門不是開關。
    """
    if body.enabled and (body.confirm or "").strip() != "DISABLE-LOGIN":
        raise HTTPException(400, '要停用登入必須輸入「DISABLE-LOGIN」確認（防手滑），已取消。')
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (AUTH_DISABLED_SETTING, "1" if body.enabled else "0"))
    conn.commit()
    # 這件事一定要進操作紀錄——事後查「那段時間為什麼沒有登入紀錄」時，
    # 要找得到是誰、什麼時候關的
    try:
        activity.log(conn, username=session["username"], ip=None, method="PUT",
                     path="/api/admin/auth-disabled", status=200, duration_ms=0,
                     action=("停用登入" if body.enabled else "恢復登入"))
    except Exception:  # noqa: BLE001 - 記錄失敗不該讓設定失敗
        pass
    return {"ok": True, "disabled": auth_disabled(conn)}


@router.get("/api/nav-layout")
def get_nav_layout(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """全站共用的選單順序（存 app_settings）。沒設過回 null＝前端用內建預設。
    can_edit 告訴前端這個人能不能改（決定要不要顯示「選單編輯」）。"""
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?", (NAV_LAYOUT_SETTING,)).fetchone()
    layout = None
    if row and row["value"]:
        try:
            layout = json.loads(row["value"])
        except (ValueError, TypeError):
            layout = None
    return {"layout": layout, "can_edit": _can_edit_nav(session, conn)}


@router.put("/api/nav-layout")
def set_nav_layout(
    body: NavLayoutBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """存全站選單順序（限管理員）。傳空＝清除、回到內建預設。"""
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員（admin 帳號）能改選單順序")
    if not body.layout:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (NAV_LAYOUT_SETTING,))
    else:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (NAV_LAYOUT_SETTING, json.dumps(body.layout, ensure_ascii=False)))
    conn.commit()
    try:
        activity.log(conn, username=session["username"], ip=None, method="PUT",
                     path="/api/nav-layout", status=200, duration_ms=0,
                     action=("清除選單順序（回預設）" if not body.layout else "調整選單順序"))
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


def _nav_user(session) -> str:
    return session["username"] if session is not None else "（登入停用）"


def _nav_audit(conn, session, method: str, path: str, action: str) -> None:
    try:
        activity.log(conn, username=_nav_user(session), ip=None, method=method, path=path, status=200,
                     duration_ms=0, action=action)
    except Exception as exc:  # noqa: BLE001 - 操作紀錄寫不進去不擋選單操作；印出來留痕
        print(f"[nav] activity log failed: {exc}")


@router.get("/api/nav-layout/backups")
def nav_layout_backups(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """選單版面備份（最近 5 版，新的在前）。layout=null＝當時用的是內建預設。"""
    import nav_prefs
    return [{"at": b["at"], "by": b.get("by"), "reason": b.get("reason"),
             "groups": len(b["layout"] or []), "is_default": b["layout"] is None} for b in nav_prefs.backups(conn)]


@router.post("/api/nav-layout/apply-default")
def nav_layout_apply_default(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """套用新分類（限管理員）：先備份目前的版面（留最近 5 版），再改用內建新分類。"""
    import nav_prefs
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員（admin 帳號）能改選單")
    r = nav_prefs.apply_default(conn, _nav_user(session))
    _nav_audit(conn, session, "POST", "/api/nav-layout/apply-default",
               "套用新分類（目前版面已備份）" if r["backed_up"] else "套用新分類（原本就是內建預設）")
    return r


@router.post("/api/nav-layout/restore")
def nav_layout_restore(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """還原上一版（限管理員）：取回最新一份備份；還原前的版面也會先存起來。"""
    import nav_prefs
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員（admin 帳號）能改選單")
    try:
        r = nav_prefs.restore_latest(conn, _nav_user(session))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _nav_audit(conn, session, "POST", "/api/nav-layout/restore", f"還原選單版面（{r['restored_at']} 的備份）")
    return r


class NavMappingBody(BaseModel):
    rows: list[dict]


@router.post("/api/nav-layout/mapping.xlsx")
def nav_layout_mapping_xlsx(
    body: NavMappingBody, session: sqlite3.Row = Depends(require_auth)
):
    """新舊選單編號對照匯出（xlsx）。對照內容由畫面算好送進來（選單定義在前端）。"""
    import nav_prefs
    from urllib.parse import quote

    buf = nav_prefs.mapping_xlsx(body.rows)
    fname = f"新舊選單編號對照_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


class NavFavoritesBody(BaseModel):
    items: list[str]
    recent: list[str] | None = None


@router.get("/api/nav/favorites")
def nav_favorites_get(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """我的最愛＋最近用過（每人一份）。storage=local＝登入停用中，前端改存瀏覽器。"""
    import nav_prefs
    r = nav_prefs.get_favorites(conn, session["username"] if session is not None else None)
    return {**r, "storage": "local" if auth_disabled(conn) else "server"}


@router.put("/api/nav/favorites")
def nav_favorites_put(
    body: NavFavoritesBody,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    import nav_prefs
    r = nav_prefs.set_favorites(conn, session["username"] if session is not None else None, body.items, body.recent)
    return {**r, "storage": "local" if auth_disabled(conn) else "server"}


@router.get("/api/nav-layout/export")
def export_nav_layout(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """把這台的選單設定匯出成一個檔（拿去另一台匯入）。

    2026-09-20 使用者：221 跟公司機選單順序不一樣，問「我要抓什麼回來才會同步」。
    選單順序存在各台自己的資料庫、不隨 patch 走，所以需要一個單獨搬它的方法
    （整庫備份雖然含這份設定，但為了搬選單去覆蓋整個資料庫太超過）。
    """
    layout = _nav_layout_of(conn)
    payload = {
        "kind": "webit3-nav-layout",
        "version": 1,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": os.environ.get("WEBIT_SITE") or socket.gethostname(),
        # null＝這台用內建預設；匯入端照樣吃，代表「把對方也恢復成預設」
        "layout": layout,
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="nav_layout.json"'},
    )


@router.post("/api/nav-layout/import")
def import_nav_layout(
    body: NavLayoutImportBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯入另一台匯出的選單設定（限管理員）。layout 是 null＝改用內建預設。"""
    if not _can_edit_nav(session, conn):
        raise HTTPException(403, "只有管理員（admin 帳號）能改選單順序")
    if body.kind and body.kind != "webit3-nav-layout":
        raise HTTPException(400, f"這不是選單設定檔（kind={body.kind}）")
    before = _nav_layout_of(conn)
    if body.layout:
        try:
            layout = _validate_nav_layout(body.layout)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (NAV_LAYOUT_SETTING, json.dumps(layout, ensure_ascii=False)))
        groups, items = len(layout), sum(len(g.get("items") or []) for g in layout)
        note = f"匯入選單設定（{groups} 群、{items} 項）"
    else:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (NAV_LAYOUT_SETTING,))
        groups = items = 0
        note = "匯入選單設定：改用內建預設"
    conn.commit()
    try:
        activity.log(conn, username=session["username"], ip=None, method="POST",
                     path="/api/nav-layout/import", status=200, duration_ms=0, action=note)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "groups": groups, "items": items,
            "replaced": bool(before), "note": note}


@router.post("/api/admin/reset-inventory")
def reset_inventory(
    body: ResetConfirm,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """清空所有盤點／採集資料，回到「有設定、無資料」的空白，供重新匯入。

    ⚠️ 破壞性且不可逆（只能靠備份還原）。必須 confirm == "ClearALL" 才執行。
    清：資產/人員/軟體、來源紀錄、待審、帳號盤點、服務、掃描、業務系統、匯入紀錄。
    不動：登入帳號(users)、憑證庫(collect_credential)、連線設定、授權網段、
         功能開關、系統設定(app_settings)、正規化別名。
    """
    if body.confirm != _RESET_CONFIRM:
        raise HTTPException(400, f'要清空必須輸入「{_RESET_CONFIRM}」確認（防手滑），已取消。')
    deleted: dict[str, int] = {}
    try:
        for t in _RESET_TABLES:
            deleted[t] = conn.execute(f"DELETE FROM {t}").rowcount
        conn.commit()
    except sqlite3.IntegrityError as exc:
        # 外鍵擋住＝有表指向正要清的表，卻不在名單裡。
        # **一定要把是哪一張講出來**：原本這裡直接 500，畫面只顯示「清空失敗，
        # 請稍後再試」，查了很久才知道是 ci_node（2026-09-09）。
        conn.rollback()
        done = [t for t in _RESET_TABLES if t in deleted]
        stuck = _RESET_TABLES[len(done)] if len(done) < len(_RESET_TABLES) else "（未知）"
        raise HTTPException(
            500,
            f"清空在「{stuck}」這一步被外鍵擋住，已全部復原（一筆都沒刪）。"
            f"原因是有別的表指向它、但沒被列入清空範圍。"
            f"請把這句話回報給開發：{exc}") from exc
    except sqlite3.Error as exc:
        conn.rollback()
        raise HTTPException(500, f"清空失敗，已全部復原（一筆都沒刪）：{exc}") from exc
    return {"ok": True, "cleared": deleted, "total_rows": sum(deleted.values()),
            "by": session["username"]}


# ===== S14 備份健康儀表 =====
# 目的是讓工程師/助理不用進命令列就能看狀態、手動備份。備份這件事最糟的失敗模式是
# 「以為有備份，還原時才發現壞的」，所以這裡不只顯示「有沒有跑過」，而是實際去驗證。

@router.get("/api/backup/status")
def backup_status_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """燈號 + 明細。會實際開檔跑 integrity_check，不是只看檔案存不存在。"""
    return backup.health(conn=conn)


@router.put("/api/backup/offsite")
def backup_set_offsite(
    body: OffsiteBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """設定異地備份目錄（畫面可設，不用改 systemd 環境變數再重啟）。

    這裡只存路徑；路徑本身要指到「真正獨立的儲存」才算異地——例如掛載另一台機器
    （222）的 /ai_backup。留空＝清掉異地（回黃燈）。存完立刻回最新健康狀態讓畫面看變化。
    """
    from db import set_setting

    set_setting(conn, backup.OFFSITE_SETTING_KEY, (body.dir or "").strip())
    return backup.health(conn=conn)


@router.post("/api/backup/run")
def backup_run_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """手動觸發一次備份（安全快照 + 完整性驗證 + 異地複製 + 清理過期）。

    刻意用 sync def：跟 import_excel_upload 同理，備份是 I/O 密集的阻塞操作，
    交給 FastAPI 的 threadpool 跑，不要卡住 event loop。
    """
    db_path = get_db_path()
    result = backup.run_backup(
        db_path, backup.get_backup_dir(db_path), datetime.now(), backup.get_offsite_dir(conn)
    )
    payload = {
        "ok": result.ok,
        "path": str(result.path) if result.path else None,
        "size_bytes": result.size_bytes,
        "integrity_ok": result.integrity_ok,
        "integrity_detail": result.integrity_detail,
        "offsite_path": str(result.offsite_path) if result.offsite_path else None,
        "offsite_error": result.offsite_error,
        "pruned_count": len(result.pruned),
        "took_seconds": result.took_seconds,
        "error": result.error,
    }
    if not result.ok:
        # 用 200 回失敗細節而不是丟 500：前端要能把「為什麼失敗」原樣顯示給使用者，
        # 500 只會變成一句「伺服器錯誤」，等於把診斷資訊丟掉。
        payload["message"] = f"備份失敗：{result.error}"
    return payload


@router.get("/api/backup/list")
def backup_list_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """本地與異地的備份檔清單（新到舊）。"""
    db_path = get_db_path()
    offsite = backup.get_offsite_dir(conn)
    return {
        "local": backup.list_backups(backup.get_backup_dir(db_path)),
        "offsite": backup.list_backups(offsite) if offsite else [],
    }


@router.get("/api/backup/dump")
def backup_dump_endpoint(
    fmt: str = "binary",
    split_mb: int = 0,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯出全部資料庫，供備份／搬機使用。跟 /api/backup/run（存到伺服器本機/異地）不同，
    這支是**直接下載到使用者瀏覽器**，兩種格式讓使用者自己選：

    fmt=binary（預設）：跟 backup.snapshot() 同一套 VACUUM INTO，二進位 .db 檔，
      跟既有系統/任何 SQLite 工具都能直接讀，不會被誤貼到聊天室/網頁（2026-08-18
      實際發生過 RVTools 資料被貼到公開分享碼網站，這支刻意提供二進位選項）。
    fmt=sql：純文字 SQL dump（用 sqlite3 內建的 iterdump()，不шell out），
      體積小、人可讀，但也因為是文字，複製貼上風險比二進位檔案高，使用者自己權衡。

    這支只匯出。還原是獨立端點 /api/backup/restore（2026-08-19 拍板方案B新增），
    刻意不跟這支共用——風險層級不同，還原會覆蓋正式資料。
    """
    import tempfile

    if fmt not in ("binary", "sql", "lite"):
        raise HTTPException(400, "fmt 只接受 binary、sql 或 lite")
    if split_mb < 0 or split_mb > 100:
        raise HTTPException(400, "split_mb 需在 0（不分割）到 100 之間")

    db_path = get_db_path()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ⚠️ 原本這裡有一條「不分割的 SQL 走串流」的捷徑，它會**繞過下面的加密**。
    # 決策 SEC5 之後匯出一律加密，而加密需要完整內容（沒辦法邊產邊加密），
    # 所以這條捷徑拿掉。代價是大檔要整份進記憶體一次——目前 45MB 級距可接受，
    # 真的長到吃不消要改成「分塊各自加密」，那是另一個設計。

    # 產出 dump 位元組（分割一定要拿到完整內容才能切）
    if fmt == "sql":
        content = "".join(line + "\n" for line in conn.iterdump()).encode("utf-8")
        base_name = f"asset_dump_{stamp}.sql"
    elif fmt == "lite":
        # 壓縮版（2026-10-22 使用者：「326M 太大了」「這個重點先改，不然你連不到公司很多只能瞎猜」）
        content = backup.lite_dump(db_path)
        base_name = f"asset_dump_{stamp}.zip"
    else:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / f"asset_dump_{stamp}.db"
            backup.snapshot(db_path, dest)
            ok, msg = backup.verify_integrity(dest)
            if not ok:
                raise HTTPException(500, f"匯出後完整性檢查失敗，拒絕提供下載：{msg}")
            # 讀進記憶體再回傳（見下）：回應送出「後」才讀檔時暫存目錄已被清掉。
            content = dest.read_bytes()
        base_name = f"asset_dump_{stamp}.db"

    # ===== 加密（決策 SEC5）=====
    # 這支是**會離開這台機器**的匯出（下載→email）。檔案裡有全公司主機清單、IP、
    # 主機名、帳號名、業務系統歸屬——寄錯人或信箱被入侵就是整份資產清冊外流。
    #
    # 所以一律加密，不留「不加密」選項（使用者 2026-09-08 拍板）。
    # 存在伺服器上的本機／異地備份不受影響，維持不加密——備份的意義是出事能還原，
    # 那份如果也加密，私鑰沒了連自己都救不回來。
    #
    # 加密順便把體積壓下來：實測 45MB 的庫 gzip 後 4.1MB（9%），
    # 100MB 那台會變 9-10MB，一封信寄得完，多半連 split_mb 都用不到。
    import export_crypto

    # 2026-09-18 使用者改成開關、預設關（見 export_crypto.ENCRYPT_SETTING）。
    if export_crypto.encryption_enabled(conn):
        recipient = export_crypto.get_recipient_public(conn)
        if recipient is None:
            raise HTTPException(
                400,
                "已開啟「匯出時加密」，但還沒設定收件人公鑰，無法匯出。"
                "請到系統設定貼上收件端（221）的公鑰，或把「匯出時加密」關掉。")
        content = export_crypto.encrypt(content, recipient)
        base_name += ".enc"
    else:
        # 不加密：留一筆紀錄，事後查得到誰在什麼時候拿走了整份資料
        try:
            activity.log(conn, username=session["username"], ip=None, method="GET",
                         path=f"/api/backup/dump 未加密匯出 fmt={fmt} size={len(content)}",
                         status=200, action="export_plain")
        except Exception:  # noqa: BLE001 - 紀錄失敗不擋下載，但會在伺服器 log 看到
            print("[export] 未加密匯出紀錄寫入失敗")

    if split_mb == 0:
        return Response(
            content, media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{base_name}"'},
        )

    # 分割：切成 split_mb 一片，打包成 zip（附併回說明）。
    # 用途：dump 太大、要當 email 附件寄（附件常限 10MB）。下載走區網不受此限，
    # 所以整包 zip 一次下載沒問題；解壓後每片 ≤split_mb，逐片寄信。
    import io
    import zipfile

    chunk = split_mb * 1024 * 1024
    parts = [content[i:i + chunk] for i in range(0, len(content), chunk)] or [b""]
    n = len(parts)
    readme = (
        f"這是分割檔，需「併回原檔」才能使用。共 {n} 片，每片 ≤{split_mb}MB。\n"
        f"檔案：{base_name}.001 … {base_name}.{n:03d}\n\n"
        f"【併回】\n"
        f"Windows（cmd，注意順序）：\n"
        f"  copy /b " + "+".join(f"{base_name}.{i:03d}" for i in range(1, n + 1)) + f" {base_name}\n"
        f"Linux / Mac：\n"
        f"  cat {base_name}.* > {base_name}\n\n"
        f"【併回後怎麼用】\n"
        + ("  .db → 系統設定→資料庫還原 上傳，或用任何 SQLite 工具開。\n"
           if fmt == "binary"
           else "  .sql → sqlite3 新檔.db < " + base_name + "\n")
        + "\n每片可分別當 email 附件寄；收到後放同一資料夾再依上面併回。\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, part in enumerate(parts, 1):
            zf.writestr(f"{base_name}.{i:03d}", part)
        zf.writestr("如何併檔.txt", readme)
    zip_name = f"{base_name}_split{split_mb}MB.zip"
    return Response(
        buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


@router.post("/api/backup/restore")
def backup_restore_endpoint(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
):
    """整庫覆蓋還原（2026-08-19 拍板方案B：簡單覆蓋＋前端單一確認框，開關預設關）。

    刻意不用 `Depends(get_db)`——這支要整檔替換掉資料庫本體，不能借用一條可能還開著
    讀取交易的連線；讓 backup.restore() 自己開短命連線做驗證，本體替換用檔案系統層級的
    os.replace()。前端是否顯示這個功能由 feature_flags 的 'restore' 開關決定
    （查 GET /api/feature-flags），這支本身不重複檢查開關——已登入使用者手動點開開關、
    走到這支端點，就是已經確認過要做這件事。
    """
    fn = (file.filename or "").lower()
    if not (fn.endswith(".db") or fn.endswith(".enc")):
        raise HTTPException(
            400, "只接受 .db（未加密）或 .enc（加密匯出）。"
                 "如果手上是分割檔（.001/.002…），請先照「如何併檔.txt」併回原檔。")

    contents = file.file.read()
    if not contents:
        raise HTTPException(400, "上傳的檔案是空的（0 bytes）")

    # 加密檔要先解開。解不開的三種原因（不是加密檔／不是給這台的／內容壞了）
    # 在 export_crypto 各自有明確訊息——那三件事要做的處理完全不同。
    import export_crypto

    if export_crypto.looks_encrypted(contents):
        try:
            contents = export_crypto.decrypt(contents)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    try:
        return backup.restore(contents, get_db_path())
    except ValueError as exc:
        # 「你給的檔案不對」——使用者自己就能修
        raise HTTPException(400, str(exc)) from exc
    except backup.RestoreFailed as exc:
        # 伺服器端出錯，但已經寫成人看得懂、且講明資料有沒有被動到的訊息
        raise HTTPException(500, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        # 最後一道網。沒有這段，任何沒預期到的例外都會變成 FastAPI 的裸 500
        # `{"detail":"Internal Server Error"}`，使用者在一個**不可逆**的操作上
        # 只看得到「失敗」兩個字——2026-08-19 回報的就是這個。
        raise HTTPException(
            500,
            f"還原時發生未預期的錯誤：{type(exc).__name__}: {exc}。"
            f"若失敗發生在覆蓋開始前，正式資料庫未被更動；"
            f"請檢查伺服器紀錄，或用 backups/ 目錄裡的存證備份還原。",
        ) from exc


@router.get("/api/feature-flags")
def list_feature_flags_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """D28：只列出 schema 種好的可切換模組（不含「系統設定」本身，見 schema.sql 註解）。"""
    return [dict(r) for r in list_feature_flags(conn)]


@router.put("/api/feature-flags/{module_key}")
def update_feature_flag_endpoint(
    module_key: str,
    body: FeatureFlagBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if get_feature_flag(conn, module_key) is None:
        raise HTTPException(404, "查無此功能模組")
    set_feature_flag(conn, module_key, body.enabled)
    return dict(get_feature_flag(conn, module_key))


@router.get("/api/version")
def version_endpoint(conn: sqlite3.Connection = Depends(get_db)):
    """版本 + git commit + 服務啟動時間——讓使用者一眼確認「跑的是不是新版」。

    git_commit 直接問 git（服務就跑在 git 工作區上），不再依賴部署時 stamp 的
    build_info.json。理由是實際踩過的坑：stamp 檔由部署腳本寫、版號是每次請求即時讀檔，
    所以「檔案換了但服務沒重啟」時版號照樣跳成新版，看起來部署成功，實際跑的還是舊程式
    （2026-07-18 換 0.6.0 時就是這樣，最後靠 systemd 啟動時間才抓到）。

    started_at 是「本行程的啟動時間」——只有真的重啟過才會變，才是新程式碼有沒有生效的
    可信證據。dirty=true 代表工作區有未提交的改動（就地開發時很常見，不是錯誤）。
    """
    info = {"version": "?", "git_commit": None, "dirty": None,
            "started_at": _PROCESS_STARTED_AT, "built_at": None,
            # 登入被關掉時要讓畫面知道——前端靠這個掛紅色橫幅。
            # 放在 version（唯一免登入的端點）而不是別處：登入關掉時前端第一支
            # 打得到的就是它，而且它本來就每頁都會呼叫。
            #
            # ⚠ 2026-09-21：這裡本來沒帶 conn，所以 auth_disabled() 只看得到環境變數，
            # 看不到存在 app_settings 的那個設定。221 實際上 auth_disabled='1'（登入是
            # 關的、未登入也擈得走全部資產），這支卻一直回 false——**防忘記關的
            # 那條橫幅從來沒出現過**。保險本身壞掉比沒保險更危险，
            # 因為看到畫面沒橫幅的人會以為登入是開的。
            # hasattr 防的是「不透過 HTTP、直接呼叫這支函式」的情況
            # （FastAPI 的 Depends 預設值不會被解開）。不加的話那條路徑會
            # AttributeError，而那支是免登入端點，炋了畫面連版號都拿不到。
            "auth_disabled": auth_disabled(conn if hasattr(conn, "execute") else None),
            # 台數口徑（Q79）最近一次判定：切換了 or 停損留在舊規則——頂端提示一次，絕不默默切換
            "machine_key_notice": None}
    if hasattr(conn, "execute"):
        try:
            import machine_identity
            info["machine_key_notice"] = machine_identity.notice(conn)
        except Exception as exc:  # noqa: BLE001 - 免登入端點不可因這個炸掉；把原因帶出去
            info["machine_key_notice"] = {"error": f"{type(exc).__name__}: {exc}"}
    here = _BACKEND_DIR
    try:
        info["version"] = json.loads(
            (here / "version.json").read_text(encoding="utf-8")
        ).get("version", "?")
    except Exception:  # noqa: BLE001
        pass
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=here,
                           capture_output=True, encoding="utf-8", timeout=5)
        if r.returncode == 0:
            info["git_commit"] = r.stdout.strip()
            d = subprocess.run(["git", "status", "--porcelain"], cwd=here,
                               capture_output=True, encoding="utf-8", timeout=5)
            info["dirty"] = bool(d.stdout.strip()) if d.returncode == 0 else None
    except Exception:  # noqa: BLE001
        pass
    if info["git_commit"] is None:
        # 沒有 git 的環境（例如打包部署到別台）才退回 stamp 檔
        try:
            info.update(json.loads((here / "build_info.json").read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return info


@router.get("/api/system/collector-selfcheck")
def collector_selfcheck_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """收集端環境自我檢查：在後端行程內驗 paramiko 能不能真的用（SAN 收集／納管都靠它）。

    就在後端行程裡跑，看到的 paramiko＝實際收集會用到的那個（含被同名空目錄蓋掉的情況），
    而且會回「sys.path 上所有叫 paramiko 的東西」——位置直接顯示在畫面，不用叫人跑指令。
    """
    import collect_log
    import collector_env

    try:
        out = collector_env.selfcheck()
    except Exception as exc:  # noqa: BLE001 - 檢查本身壞掉也要留證據（2026-09-15 公司機就是這樣，紀錄 0 筆）
        collect_log.record(conn, "selfcheck", None, False, f"自我檢查本身失敗：{type(exc).__name__}: {exc}",
                           actor=session["username"] if session else None, exc=exc)
        raise
    # 只記「不好」的：每次開頁都會檢查，好的也記會把真正有用的紀錄淹掉
    if not out.get("ok"):
        collect_log.record(conn, "selfcheck", None, False, out.get("reason") or "自我檢查不通過",
                           actor=session["username"] if session else None,
                           extra={"selfcheck": out})
    return out


@router.post("/api/system/collector-selfheal")
def collector_selfheal_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """一鍵修復：把蓋住 paramiko 的空目錄**改名備份**（不刪除），再驗一次。

    2026-09-15：公司機套了 v1.174.0 還是壞的——patch 只掃兩個固定位置，空目錄不在那。
    每查一次就重出一包 patch 太慢，所以讓畫面自己修得動。真套件（有 __init__.py）不碰。
    """
    import collect_log
    import collector_env

    out = collector_env.selfheal()
    collect_log.record(conn, "selfheal", None, bool(out.get("ok")), out.get("message") or "",
                       actor=session["username"] if session else None,
                       extra={"renamed": out.get("renamed"), "failed": out.get("failed"),
                              "before": out.get("before"), "after": out.get("after")})
    return out


@router.get("/api/system/collect-log")
def collect_log_list(
    limit: int = 50,
    kind: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """收集紀錄：每次 SAN 收集／自我檢查不通過／一鍵修復，連同當下完整環境（不含密碼）。
    2026-09-15 使用者：「修復至少 5 次，做個收集 LOG 或分析的」。見 collect_log。"""
    import collect_log

    kinds = [k.strip() for k in (kind or "").split(",") if k.strip()] or None
    return {"items": collect_log.recent(conn, limit=max(1, min(limit, 500)), kinds=kinds)}


@router.get("/api/system/collect-log/export")
def collect_log_export(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """整份收集分析檔（純文字），給使用者一次下載、整份交給開發者——不用再來回截圖猜。"""
    import collect_log
    from urllib.parse import quote

    fname = f"收集分析_{_now_local()[:10]}.txt"
    return Response(
        content=collect_log.export_text(conn).encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.post("/api/tools/ssh-probe")
def ssh_probe_endpoint(
    body: SshProbeBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """SSH 測連線：同一台 IP 用新版與隔離的舊版 SSH 各試一次握手，**不登入**。見 ssh_probe。"""
    import collect_log
    import ssh_probe

    try:
        out = ssh_probe.run(body.ip, body.port)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    ok = out["modern"]["code"] == 0 or out["legacy"].get("code") == 0
    collect_log.record(conn, "ssh_probe", out["ip"], ok, out["verdict"],
                       actor=session["username"] if session else None,
                       extra={"port": out["port"], "modern": out["modern"], "legacy": out["legacy"]})
    return out


@router.post("/api/tools/alive-check")
def alive_check_endpoint(
    body: AliveCheckBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """偵測存活：ICMP＋TCP 22/445/3389/5985 各試一次，只回報不改資料。見 host_ping。

    使用者 2026-09-16：「多一個 PING 按鈕動作，我可確認是否存活」，
    接著改名——「其他 port 也去測，所以不能說 PING，應該是『偵測存活』」。
    """
    import collect_log
    import host_ping

    try:
        out = host_ping.check(body.ip)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    collect_log.record(conn, "alive_check", out["ip"], bool(out["alive"]),
                       f"{out['verdict']}（{out['evidence']}）",
                       actor=session["username"] if session else None,
                       extra={"icmp": {k: v for k, v in out["icmp"].items() if k != "output"},
                              "tcp": out["tcp"]})
    return out


@router.post("/api/tools/alive-check/batch")
def alive_check_batch(
    body: AliveBatchBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """批次偵測存活，並存成一份報告。

    使用者 2026-09-16：「偵測後要 PO 報告」「每次偵測後要畫面狀態報告，不然只有沒有回應 很虛」。
    失聯 8062 台逐台按不可能，但**只對資產庫裡登記的 IP 偵測**——不展開網段、不探未知位址，
    這是維運確認，不是掃描。一次上限 host_ping.MAX_BATCH 台。
    """
    import collect_log
    import host_ping

    started = _now_local()
    targets: list[dict] = []
    seen: set[str] = set()
    if body.serials:
        ph = ",".join("?" for _ in body.serials)
        rows = conn.execute(
            f"SELECT asset_serial, hostname, ip FROM hardware WHERE asset_serial IN ({ph})",
            tuple(body.serials)).fetchall()
        for r in rows:
            if r["ip"] and r["ip"] not in seen:
                seen.add(r["ip"])
                targets.append({"asset_serial": r["asset_serial"], "hostname": r["hostname"], "ip": r["ip"]})
    for ip in body.ips or []:
        ip = (ip or "").strip()
        if not ip or ip in seen:
            continue
        r = conn.execute("SELECT asset_serial, hostname FROM hardware WHERE ip = ?", (ip,)).fetchone()
        seen.add(ip)
        targets.append({"asset_serial": r["asset_serial"] if r else None,
                        "hostname": r["hostname"] if r else None, "ip": ip})
    if not targets:
        raise HTTPException(400, "沒有可偵測的對象（勾選的資產都沒有登記 IP）")
    if len(targets) > host_ping.MAX_BATCH:
        raise HTTPException(
            400, f"一次最多偵測 {host_ping.MAX_BATCH} 台，這次選了 {len(targets)} 台——"
                 f"請分批（還有 {len(targets) - host_ping.MAX_BATCH} 台）")

    results = host_ping.check_many(targets)
    summary = host_ping.summarize_batch(results)
    run_id = host_ping.save_run(conn, results, body.scope or "", 
                                session["username"] if session else None, started)
    collect_log.record(
        conn, "alive_check", None, summary["no_response"] == 0,
        f"批次偵測 {summary['total']} 台：活著 {summary['alive']}、"
        f"沒有回應 {summary['no_response']}、無法判斷 {summary['unknown']}",
        actor=session["username"] if session else None,
        extra={"run_id": run_id, "scope": body.scope, "summary": summary})
    return {"run_id": run_id, "summary": summary, "items": results}


@router.get("/api/health-check/targets")
def health_check_targets(
    cidr: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """某網段裡「已登記」的 IP（給『整段健檢』的二次確認用）。
    只回資產庫有的 IP——健檢只打已知目標，不展開整段去探未知位址（那才像掃描）。"""
    try:
        net = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError:
        raise HTTPException(400, "網段格式不對（例：10.99.1.0/24）")
    hits = []
    for r in conn.execute(
        "SELECT DISTINCT ip, hostname FROM hardware WHERE ip IS NOT NULL AND TRIM(ip) <> ''"
    ).fetchall():
        try:
            if ipaddress.ip_address(r["ip"].strip()) in net:
                hits.append({"ip": r["ip"].strip(), "hostname": r["hostname"]})
        except ValueError:
            continue                                 # ip 欄不是合法位址（範本/KVM），略過
    hits.sort(key=lambda x: ipaddress.ip_address(x["ip"]))
    return {"cidr": str(net), "count": len(hits), "items": hits}


@router.post("/api/health-check/run")
def health_check_run(
    body: HealthCheckBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """對一批已知 IP 並發健檢，回逐台結果＋摘要。Linux／AIX 走 SSH，Windows 走 WinRM。"""
    import collect_log
    import health_probe
    import manage_state

    def _clean_host(s: str) -> str:
        # 剝掉 http://、路徑、user@、:port——值班常貼網址，整串拿去 SSH 會噴假的「解析失敗」
        s = (s or "").strip()
        if "://" in s:
            s = s.split("://", 1)[1]
        s = s.split("/", 1)[0].split("@")[-1]
        if ":" in s and s.rsplit(":", 1)[1].isdigit():
            s = s.rsplit(":", 1)[0]
        return s.strip()
    ips = [h for h in (_clean_host(ip) for ip in (body.ips or [])) if h]
    if not ips:
        raise HTTPException(400, "沒有要檢查的 IP")
    if len(ips) > _HEALTH_MAX_TARGETS:
        raise HTTPException(
            400, f"一次最多 {_HEALTH_MAX_TARGETS} 台，這次 {len(ips)} 台——請分批")
    conc = body.concurrency or _HEALTH_CONCURRENCY_DEFAULT
    conc = max(1, min(conc, _HEALTH_CONCURRENCY_MAX))

    def _platform_of(ip: str) -> str:
        try:
            # 用 collect_platform_for（會先去 hardware 查 os）而不是 collect_platform_of：
            # 後者在沒有 os 字串時只剩埠號可猜，AIX 只開 22 -> 一律猜成 linux，
            # 然後拿 /proc 那組指令去問 AIX，整台白檢查。
            return manage_state.collect_platform_for(conn, ip)
        except Exception:  # noqa: BLE001 - 查不到平台就當 linux 試
            return "linux"

    def _creds_of(ip: str):
        """Windows 走 WinRM 要帳密（Linux／AIX 用金鑰，不會走到這裡）。

        沿用既有的收集憑證，**不另外設一組健檢專用的**——多一組就是第二份真相，
        改了一邊另一邊還是舊的，而症狀只是「某些機器查不到」。
        查不到就回 None，由 health_probe 顯示「沒有可用的 WinRM 收集憑證」，
        **不可以顯示成連不上**：一個是去設定頁補帳號，一個是去機房。
        """
        import credential_store

        try:
            name = credential_store.pick_for_host(conn, ip, kind="winrm")
            if not name:
                return None
            return credential_store.get_for_use(conn, name)
        except Exception:  # noqa: BLE001 - 取不到憑證就當沒有，錯誤訊息由上層講
            return None

    items = health_probe.check_batch(ips, platform_of=_platform_of, concurrency=conc,
                                     creds_of=_creds_of)
    # 批次 2：跟最近一份 baseline 比（方向性 diff：消失 port＝紅、新增＝提示）。沒有基準回 None。
    import health_baseline
    for r in items:
        try:
            r["diff"] = health_baseline.diff(health_baseline.latest_baseline(conn, r["ip"]), r)
        except Exception:  # noqa: BLE001 - 比對失敗不影響即時健檢本體
            r["diff"] = None
    summary = {
        "total": len(items),
        "red": sum(1 for r in items if r["overall"] == "red"),
        "yellow": sum(1 for r in items if r["overall"] == "yellow"),
        "green": sum(1 for r in items if r["overall"] == "green"),
        "skipped": sum(1 for r in items if r["overall"] == "skipped"),
        # **主詞不同的兩件事，名字就要不同。**
        # unreachable＝我們真的探測過、那台沒有回應（關於那台機器的宣稱）。
        # not_probed＝我們根本沒去成：沒有憑證／沒有金鑰／平台不支援
        #            （關於我們自己的事實，是我們的待辦，不是那台的狀態）。
        # 2026-09-23：混在一起時，值班會跑去機房看一台好好的機器，
        # 而正確的下一步是走三步到設定頁補一個帳號。
        "unreachable": sum(1 for r in items
                           if not r["reachable"] and r["overall"] == "red"
                           and not r.get("not_probed")),
        "not_probed": sum(1 for r in items if r.get("not_probed")),
        # 「查完了沒」跟「查出來好不好」是兩件事，摘要要分開給——
        # 值班要能一眼把「檢查完整且綠」跟「還沒查完」分開看（2026-09-23）。
        #
        # **只算「連得到卻沒查完」**：連不上的那台什麼都沒查到，但它已經算在
        # unreachable 也顯示成紅色，再算一次只是把真正要看的那批稀釋掉。
        # 「連不上」有自己的名字（unreachable），**同一個名字不可以在兩個地方
        # 是兩個意思**——前端因此直接用這個數字，不自己再算一份。
        "incomplete": sum(1 for r in items
                          if r.get("reachable") and r.get("complete") is False),
    }
    collect_log.record(
        conn, "health_check", None, summary["red"] == 0,
        f"健檢 {summary['total']} 台：紅 {summary['red']}、黃 {summary['yellow']}、"
        f"綠 {summary['green']}（連不上 {summary['unreachable']}）",
        actor=session["username"] if session else None,
        extra={"summary": summary, "concurrency": conc})
    return {"items": items, "summary": summary, "concurrency": conc}


@router.get("/api/health-check/history")
def health_check_history(
    ip: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """某台的健康基準快照序列（批次 2）：看 port 數／燈號隨時間的變化點。"""
    import health_baseline

    return {"ip": ip, "items": health_baseline.history(conn, ip)}


@router.post("/api/health-check/baseline/run")
def health_check_baseline_run(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """手動立刻收一份週基準（批次 2）：平常週六排程自動跑；這顆給第一次鋪底／值班想立刻更新基準用。"""
    import health_baseline

    return health_baseline.run_weekly_baseline(conn)


@router.get("/api/tools/alive-check/runs")
def alive_check_runs(
    limit: int = 20,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """過去的偵測報告清單。"""
    import host_ping

    return {"items": host_ping.list_runs(conn, limit)}


@router.get("/api/tools/alive-check/runs/{run_id}")
def alive_check_run_detail(
    run_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import host_ping

    run = host_ping.get_run(conn, run_id)
    if run is None:
        raise HTTPException(404, "查無這份偵測報告")
    return run


@router.get("/api/tools/alive-check/runs/{run_id}/export")
def alive_check_run_export(
    run_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """偵測報告匯出 Excel。"""
    import host_ping
    from urllib.parse import quote

    rows = host_ping.export_rows(conn, run_id)
    if not rows:
        raise HTTPException(404, "查無這份偵測報告")
    wb = Workbook()
    ws = wb.active
    ws.title = "偵測存活報告"
    ws.append(host_ping.EXPORT_HEADERS)
    for r in rows:
        ws.append(["" if v is None else v for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"偵測存活報告_{run_id}_{_now_local()[:10]}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


@router.get("/api/hmc/consoles")
def hmc_consoles_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import hmc_collector

    items = hmc_collector.list_consoles(conn)
    done = sum(1 for i in items if i.get("collected"))
    return {"items": items, "total": len(items), "collected": done}


@router.post("/api/hmc/collect")
def hmc_collect_endpoint(
    body: HmcCollectBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import hmc_collector

    if not (body.ip and body.username and body.password):
        raise HTTPException(400, "IP／帳號／密碼都要填")
    try:
        inv = hmc_collector.collect(body.ip, body.username, body.password)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    by = session["username"] if session else None
    return {"ok": True, **hmc_collector.store(conn, body.ip, inv, by)}


@router.get("/api/hmc/consoles/{ip}")
def hmc_console_detail_endpoint(
    ip: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    row = conn.execute("SELECT * FROM hmc_console WHERE ip = ?", (ip,)).fetchone()
    if not row:
        raise HTTPException(404, "這台還沒收集過")
    d = dict(row)
    d["data"] = json.loads(d.pop("data_json") or "{}")
    return d


class TableXlsxBody(BaseModel):
    title: str = "匯出"
    headers: list[str]
    rows: list[list]


@router.post("/api/export/table-xlsx")
def table_xlsx_endpoint(
    body: TableXlsxBody,
    session: sqlite3.Row = Depends(require_auth),
):
    """畫面上這張表（篩選、排序後）原樣轉成 Excel（2026-10-22 使用者：「每個頁面都要有」搜尋＋匯出）。

    前端送的就是使用者正在看的那幾列，所以匯出＝畫面，不另寫一份查詢。
    只轉格式、不查資料庫：拿不到比畫面更多的資料。
    """
    from urllib.parse import quote

    if len(body.rows) > 50000:
        raise HTTPException(400, "一次最多 50000 列")
    wb = Workbook()
    ws = wb.active
    ws.title = (body.title or "匯出")[:31].replace("/", "／")
    ws.append(body.headers)
    for r in body.rows:
        ws.append(["" if v is None else v for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    name = f"{body.title or '匯出'}_{datetime.now():%Y%m%d_%H%M}.xlsx"
    return Response(buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


__all__ = [
    'login',
    'logout',
    'me',
    'online_endpoint',
    'activity_list_endpoint',
    'activity_summary_endpoint',
    'diagnostics_endpoint',
    'health_summary',
    'get_auth_disabled',
    'set_auth_disabled',
    'get_nav_layout',
    'set_nav_layout',
    'export_nav_layout',
    'import_nav_layout',
    'nav_layout_backups',
    'nav_layout_apply_default',
    'nav_layout_restore',
    'nav_layout_mapping_xlsx',
    'NavMappingBody',
    'nav_favorites_get',
    'nav_favorites_put',
    'NavFavoritesBody',
    'reset_inventory',
    'backup_status_endpoint',
    'backup_set_offsite',
    'backup_run_endpoint',
    'backup_list_endpoint',
    'backup_dump_endpoint',
    'backup_restore_endpoint',
    'list_feature_flags_endpoint',
    'update_feature_flag_endpoint',
    'version_endpoint',
    'collector_selfcheck_endpoint',
    'collector_selfheal_endpoint',
    'collect_log_list',
    'collect_log_export',
    'ssh_probe_endpoint',
    'alive_check_endpoint',
    'alive_check_batch',
    'health_check_targets',
    'health_check_run',
    'health_check_history',
    'health_check_baseline_run',
    'alive_check_runs',
    'alive_check_run_detail',
    'alive_check_run_export',
    'hmc_consoles_endpoint',
    'hmc_collect_endpoint',
    'hmc_console_detail_endpoint',
    'TableXlsxBody',
    'table_xlsx_endpoint',
]
