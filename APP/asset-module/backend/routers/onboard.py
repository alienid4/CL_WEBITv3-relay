from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()



@router.post("/api/onboard")
def onboard_endpoint(
    body: OnboardBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """A：UI 一鍵納管。使用者在畫面輸入 sysctl 帳密 → 系統用它去目標機建 webit3scan。

    ⚠️ 憑證不落地是寫死的底線：body.password 只傳給引擎、用完即丟——
    不寫 DB、不寫 log、不進稽核紀錄。稽核只記帳號名、平台、成敗，不記密碼。
    """
    import manage_state
    import onboard_engine

    collector_ip = onboard_engine.resolve_collector_ip(conn)
    # 開一格進度：執行器會逐行把目標主機的輸出寫進去，畫面同時輪詢 /api/onboard/progress。
    # 這支端點本身仍然是同步的（回傳最終結果），進度只是讓等待中的人看得到在做什麼。
    onboard_engine.progress_start(body.ip)
    try:
        result = onboard_engine.onboard(
            host=body.ip, platform=body.platform,
            username=body.username, password=body.password,   # 用完即丟，下面不再引用
            collector_ip=collector_ip,
            # 建出來的帳號名要跟收集端之後拿去登入的一致，否則建完仍然連不進去
            account=manage_state.get_collect_account(conn, body.platform),
            # 帳號備註（/etc/passwd 的 GECOS）由管理者在設定頁填，不寫死在程式碼——
            # 那裡面有員工編號與姓名，而 onboard_engine.py 會進公開 relay。
            comment=onboard_engine.resolve_account_comment(conn),
        )
    finally:
        onboard_engine.progress_done(body.ip)
    # 稽核：明確只寫無憑證的欄位。body.password 到這裡就結束生命週期，不進 SQL。
    conn.execute(
        "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, "
        "triggered_by, ok, stage, message, output) VALUES (?,?,?,?,?,?,?,?,?)",
        (body.ip, body.platform, body.username, "manual", session["username"],
         1 if result.ok else 0, result.stage, result.message, result.output),
    )
    conn.commit()

    # 納管成功後：
    # 1) 「發現但沒登記」的主機要先補一筆最小資產，否則收到的 facts 無處可落。
    #    只填系統知道的（IP／掃到的主機名／預設環境），業務欄位留空給人補。
    # 2) 試連＋收 facts，讓使用者立刻看到狀態變「已納管」＋真 OS/序號進來。
    post_note = None
    collect_error: str | None = None
    if result.ok:
        try:
            import manage_state
            exists = conn.execute("SELECT 1 FROM hardware WHERE ip = ?", (body.ip,)).fetchone()
            if not exists:
                scan_hn = conn.execute(
                    "SELECT hostname FROM scan_history WHERE ip = ? AND scan_ok = 1 "
                    "ORDER BY scan_time DESC LIMIT 1", (body.ip,)).fetchone()
                insert_hardware(
                    conn, asset_serial=f"AUTO-{body.ip}", ip=body.ip,
                    hostname=(scan_hn["hostname"] if scan_hn and scan_hn["hostname"] else None),
                    environment="正式", asset_status="使用中",
                )
            # ⚠️ 只做剛納管的這一台。2026-08-28 實測：原本這兩行不帶條件，
            # 等於每納管一台就把整個機隊重測＋重收一遍——畫面上腳本早就印完
            # 「完成。」，計時器還在跑 97～120 秒，跑的全是別台的事。
            # 成本是 N²，而明天要納管的是整個網段的幾百台。
            # 全機隊刷新交給排程（refresh_collect_status 的 docstring 本來就這樣寫）。
            serial_row = conn.execute(
                "SELECT asset_serial FROM hardware WHERE ip = ?", (body.ip,)).fetchone()
            manage_state.refresh_collect_status(conn, only_ip=body.ip)
            if serial_row:
                # ⚠️ 2026-09-22：這裡原本**把回傳值丟掉**。
                # 使用者原話：「AIX 我可以連線，畫面也顯示納管成功，納管後我沒看到
                # 納管後的任何資訊跟畫面呈現，例如帳號、軟體、硬體資訊等等」。
                # 原因就在這行：帳號確實建好了（result.ok=True → 畫面「納管成功」），
                # 但緊接著的收集拿到 0 個欄位，而**沒有人看那個回傳值**——
                # post_note 只在丟例外時才寫，「跑完了但什麼都沒收到」不算例外。
                # 於是「連得上、報成功、資料全空」三件事同時成立，
                # 使用者完全分不出是納管壞了還是這台真的沒有帳號。
                collected = manage_state.collect_facts_into_assets(
                    conn, only_serial=serial_row["asset_serial"])
                if collected.get("updated", 0) < 1:
                    os_row = conn.execute(
                        "SELECT os FROM hardware WHERE asset_serial = ?",
                        (serial_row["asset_serial"],)).fetchone()
                    base = (collected["failed"][0]["error"]
                            if collected.get("failed") else None)
                    collect_error = _collect_diagnosis(
                        conn, body.ip, body.platform,
                        os_row["os"] if os_row else None,
                        collected.get("updated", 0), base)
                    # 明確寫回「收不到」，不要停在上一次的舊狀態
                    conn.execute(
                        "UPDATE hardware SET collect_ok = 0, collect_checked_at = ?, "
                        "collect_error = ? WHERE asset_serial = ?",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                         collect_error[:500], serial_row["asset_serial"]))
                    # 納管紀錄也要留一筆，資產頁才看得到「這次是收集這步敗的」
                    _log_onboard(conn, hardware_ip=body.ip, platform=body.platform,
                                 who=session["username"] if session else None,
                                 ok=False, stage="collect", message=collect_error,
                                 trigger="manual", login_user=body.username)
                # 納管後自動收一輪：規格／服務／軟體／帳號（2026-09-16 使用者：
                # 「以後納管後 要自動跑一次，收集全部 是手動狀態」）。
                # **在背景跑**——納管端點本身已經要花數十秒，再同步收四樣會讓瀏覽器逾時，
                # 使用者會以為納管失敗然後再按一次。結果寫進採集紀錄（kind=post_onboard）。
                import post_onboard

                post_onboard.start(None, serial_row["asset_serial"],
                                   by=session["username"] if session else None)
        except Exception as exc:  # noqa: BLE001
            # 收集失敗不影響「納管本身已完成」這個事實，但**不可以靜默**：
            # 原本是 `pass`，於是「跑了 90 秒然後全部失敗」跟「一切正常」
            # 在畫面上長得一模一樣。至少要讓人看得到收尾這步出了事。
            post_note = f"（納管成功，但納管後的試連／收集這步失敗：{exc}）"

    # 納管的目的是「之後收得到資料」。帳號建好但一個欄位都收不到，
    # 對使用者而言就是失敗——顯示成功會讓他以為完成了，然後過幾天才發現什麼都沒有。
    # （使用者 2026-09-22 就是這樣做了三次。）
    # 所以這裡把兩件事分開回報：script_ok=腳本有沒有跑成，ok=這次納管有沒有達到目的。
    if result.ok and collect_error:
        return {
            "ok": False, "script_ok": True, "stage": "collect",
            "message": ("納管腳本執行成功（收集帳號已建立），"
                        f"但納管後收不到任何資料。{collect_error}"),
            "output": result.output,
        }
    message = result.message + (post_note or "")
    return {"ok": result.ok, "script_ok": result.ok, "stage": result.stage,
            "message": message, "output": result.output}


@router.post("/api/onboard/revoke")
def revoke_endpoint(
    body: RevokeBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """取消納管：把這台上的收集帳號、金鑰、sudo 白名單收回來。

    ## 為什麼要打 YES

    這個動作**在目標主機上刪東西**，而且刪完之後這台就收不到資料了。
    跟「再納管一次」不對稱——納管失敗頂多沒佈成功，撤銷失敗可能刪錯東西。
    所以要一個「不會手滑」的確認：打字比按鈕難按錯。

    ## 撤銷後的狀態不是「未納管」

    `collect_ok` 設成 0 之外，`collect_error` 會寫明是**人主動撤銷**的，
    不是「連不上」。這兩件事在畫面上都顯示成「收不到」，但一個是待辦、
    一個是刻意的結果——分不出來的話，之後有人會跑去「修」一台你剛撤銷的機器。
    """
    if body.confirm != "YES":
        raise HTTPException(400, "取消納管需要輸入 YES 確認")
    import manage_state
    import onboard_engine

    onboard_engine.progress_start(body.ip)
    try:
        result = onboard_engine.revoke(
            host=body.ip, platform=body.platform,
            username=body.username, password=body.password,
            account=manage_state.get_collect_account(conn, body.platform),
        )
    finally:
        onboard_engine.progress_done(body.ip)

    # 稽核：跟納管走同一張表，trigger 標 revoke 才分得出來。
    # 「誰在什麼時候撤了哪一台」正是稽核會問的第三個問題。
    conn.execute(
        "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, "
        "triggered_by, ok, stage, message, output) VALUES (?,?,?,?,?,?,?,?,?)",
        (body.ip, body.platform, body.username, "revoke", session["username"],
         1 if result.ok else 0, result.stage, result.message, result.output),
    )
    if result.ok:
        # 撤銷成功 → 這台現在收不到，而且是**刻意**收不到。
        conn.execute(
            "UPDATE hardware SET collect_ok = 0, collect_checked_at = ?, "
            "collect_error = ? WHERE ip = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             f"已由 {session['username']} 取消納管（不是連線失敗）", body.ip),
        )
    conn.commit()
    return {"ok": result.ok, "stage": result.stage, "message": result.message,
            "output": result.output}


@router.post("/api/agent/stage")
def agent_stage(
    body: AgentStageBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """核發一把新的 agent key，組出 Stage 1 要上傳的**資料**（呼叫端負責實際 SCP
    上傳，這支端點本身不連任何主機）。asset_serial 必須已經是 hardware 表裡的一筆
    資產——沒有資產記錄就沒地方掛這把 key。

    ⚠️ 2026-08-28：這支端點**不再交出 push_agent.sh／install.sh**。
    那兩支是程式碼，已經隨 bootstrap_watcher.sh 固定佈在目標主機的
    /opt/webit3-agent/bin/ 了。舊版把它們當成「要上傳的檔案」交出去，配上 root
    排程去 /tmp 撿東西執行，合起來就是本機提權（任何本機帳號都能讓 root 幫他裝
    任意程式）——完整說明見 agent_scripts/install.sh 開頭。

    現在投放目錄只收兩個資料檔。**如果還照舊版把腳本一起上傳，install.sh 的白名單
    會把整個任務當成攻擊跡象拒絕**，所以這裡回一個 upload_dir 講清楚要放哪、放什麼。
    """
    exists = conn.execute(
        "SELECT 1 FROM hardware WHERE asset_serial = ?", (body.asset_serial,)
    ).fetchone()
    if not exists:
        raise HTTPException(404, "找不到這筆資產，請先建立資產記錄再種 agent")

    key = agent_auth.issue_host_key(conn, body.asset_serial)
    collector_url = f"http://{onboard_engine.resolve_collector_ip(conn)}:8000"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return {
        "asset_serial": body.asset_serial,
        "upload_dir": f"/var/lib/webit3-agent/incoming/{body.asset_serial}_{stamp}",
        "note": "兩個檔案寫完之後，最後才建一個空的 .ready 標記檔——先有 .ready "
                "會讓 root 排程撿到上傳到一半的半成品。"
                "不要上傳任何腳本：投放目錄只收資料，出現 .sh 整個任務會被拒絕。",
        "files": {
            "agent_key": key,
            "collector_url": collector_url,
        },
    }


@router.post("/api/agent/facts")
def agent_facts(
    body: AgentFactsBody,
    asset_serial: str = Depends(require_host_key),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Push agent 每日回報進來的資料。asset_serial 一律用 require_host_key 驗證出來的
    那個，不信任 body 裡任何主機識別欄位——agent 只能寫自己的資料，這是防冒充的關鍵。
    """
    for m in body.metrics:
        key = m.get("key")
        if not key:
            continue
        conn.execute(
            "INSERT INTO host_metric_latest (asset_serial, metric_key, value, unit, collected_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(asset_serial, metric_key) DO UPDATE SET "
            "value = excluded.value, unit = excluded.unit, collected_at = excluded.collected_at, "
            "received_at = datetime('now','localtime')",
            (asset_serial, key, m.get("value"), m.get("unit"), m.get("collected_at")),
        )
    conn.commit()
    return {"ok": True}


@router.post("/api/onboard/batch-probe")
def onboard_batch_probe(
    body: BatchProbeBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """B：一批主機、同一帳號、多組候選密碼，回報「哪一組通」——不建帳號、
    不執行任何納管腳本，純粹唯讀探測（借 uname -s 這行）。

    2026-09-06 使用者的實際情境：機隊裡 root 密碼混了 A/B 兩種，不想一台
    一台手動試。這支只回「第幾組密碼對」，body.passwords 到這裡就結束
    生命週期，不進 SQL、不進稽核紀錄——跟 /api/onboard 的密碼不落地是
    同一條底線。

    ⚠️ 不落密碼不代表這支端點沒有風險：短時間對多台主機連續嘗試多組密碼，
    形狀就是密碼噴灑攻擊，可能觸發目標機的帳號鎖定（fail2ban/PAM tally）。
    數量大時前端要把這個風險講清楚，不能包裝成「安全的批次工具」。
    """
    import onboard_engine

    if not body.ips:
        raise HTTPException(400, "至少要給一台 IP")
    if not body.passwords:
        raise HTTPException(400, "至少要給一組密碼")
    if len(body.ips) > 100:
        raise HTTPException(400, "一次最多 100 台——數量再大，密碼噴灑的風險也跟著放大，分批做")

    results = onboard_engine.batch_probe_credentials(
        targets=[{"ip": ip} for ip in body.ips],
        username=body.username, passwords=body.passwords)
    matched = sum(1 for r in results if r["matched_password_index"] is not None)
    return {"total": len(results), "matched": matched, "unmatched": len(results) - matched,
            "results": results}


@router.get("/api/onboard/hint")
def onboard_hint(
    ip: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """納管前的平台研判——**即時探測，不信登記值**。

    為什麼不用 hardware.os：登記的 OS 可能過時、可能是假資料（實例：.101 登記成
    Ubuntu，實際 banner 是 OpenSSH_for_Windows）。拿錯平台的腳本去打，一定失敗。
    這裡即時抓 SSH banner（不需帳密），banner 自報最準；並回報跟登記值是否衝突，
    讓使用者看得到「畫面說 Ubuntu 但實測是 Windows」這種矛盾。
    """
    import fingerprint

    banner = fingerprint.grab_banner(ip, 22)
    scan = conn.execute(
        "SELECT open_ports, os_guess FROM scan_history WHERE ip = ? AND scan_ok = 1 "
        "ORDER BY scan_time DESC LIMIT 1", (ip,)).fetchone()
    ports = [int(p) for p in (scan["open_ports"] or "").split(",") if p.strip().isdigit()] \
        if scan else []
    os_guess = scan["os_guess"] if scan else None
    method = fingerprint.onboard_method(os_guess=os_guess, open_ports=ports, banner=banner)

    registered = conn.execute("SELECT os FROM hardware WHERE ip = ?", (ip,)).fetchone()
    registered_os = registered["os"] if registered else None
    # 衝突：登記說 Linux 但實測 Windows（或反過來）
    conflict = None
    if registered_os and method["method"] in ("linux", "windows"):
        reg_is_win = any(k in registered_os.lower() for k in ("windows", "microsoft"))
        detected_win = method["method"] == "windows"
        if reg_is_win != detected_win:
            conflict = f"登記為「{registered_os}」，但實測是 {'Windows' if detected_win else 'Linux'}"

    return {
        "ip": ip,
        "platform": method["method"] if method["method"] in ("linux", "windows") else "",
        "confidence": method["confidence"],
        "evidence": method["evidence"],
        "banner": banner,
        "registered_os": registered_os,
        "conflict": conflict,
    }


@router.get("/api/credentials")
def list_credentials(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """列出收集用憑證——**永不含密碼，連密文都不給**。"""
    import credential_store

    return credential_store.list_public(conn)


@router.post("/api/credentials")
def save_credential(
    body: CredentialBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """新增／更新收集用憑證（如 Windows WinRM 服務帳號）。

    密碼加密後才進 DB，加密金鑰另存 0600 檔案（不在 DB 裡——DB 會被備份、被複製，
    金鑰跟著走就等於沒加密）。回應只回 metadata。
    """
    import credential_store

    credential_store.save(conn, body.name, body.kind, body.username, body.password,
                          scope=body.scope, note=body.note)
    return {"ok": True, "credentials": credential_store.list_public(conn)}


@router.delete("/api/credentials/{name}")
def delete_credential(
    name: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import credential_store

    n = credential_store.delete(conn, name)
    if not n:
        raise HTTPException(404, "查無此憑證")
    return {"ok": True}


@router.get("/api/onboard/script")
def onboard_script(
    platform: str,
    fmt: str = "oneliner",
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """產生「在目標機本機自己跑」的一行指令，或給維運批次佈署的 Ansible playbook。

    為什麼需要這條路：遠端納管要目標機的管理員帳密，但很多情況拿不到——
    Windows 沒有維運帳號慣例、單機環境沒網域、或人就坐在那台機器前面（根本不用遠端）。
    這時給他一行可貼的指令最實際，且**完全不需要任何密碼**。
    腳本內容與遠端用的完全同一份（同一個引擎產出），不會有兩套行為分岔。

    fmt=ansible（僅 Linux）：把同一組動作翻成 playbook，交給資安／維運一次佈完整批。
    ⚠️ playbook 也是即時從收集公鑰組出來的，不是 repo 裡的靜態檔——換過金鑰之後
    靜態檔會安靜地變成錯的（佈下去每台都成功，只是收集全部連不進來）。
    AIX 沒有這個選項：Ansible 不支援 AIX，只能給可貼的腳本。
    """
    import onboard_engine

    if platform not in ("linux", "aix", "windows"):
        raise HTTPException(400, "platform 只接受 linux、aix 或 windows")
    collector_ip = onboard_engine.resolve_collector_ip(conn)
    import manage_state

    account = manage_state.get_collect_account(conn, platform)

    if fmt == "ansible":
        if platform != "linux":
            raise HTTPException(
                400, "Ansible playbook 只適用 Linux——AIX 不支援 Ansible（改用可貼的腳本），"
                     "Windows 走 WinRM 不需要在目標機建帳號")
        try:
            content = onboard_engine.build_linux_playbook(
                onboard_engine.collector_pubkey(), collector_ip, account,
                onboard_engine.resolve_account_comment(conn))
        except ValueError as exc:      # 收集金鑰還沒產生
            raise HTTPException(400, str(exc))
        return {
            "platform": platform, "fmt": "ansible",
            "filename": f"{account}_bootstrap.yml",
            "content": content,
            "note": "交給資安／維運：ansible-playbook -i <inventory> "
                    f"{account}_bootstrap.yml",
        }
    if fmt != "oneliner":
        raise HTTPException(400, "fmt 只接受 oneliner 或 ansible")

    try:
        script = onboard_engine.build_script(
            platform, onboard_engine.collector_pubkey(), collector_ip, account,
            onboard_engine.resolve_account_comment(conn))
    except ValueError as exc:      # AIX 帳號名過長、或收集金鑰還沒產生
        raise HTTPException(400, str(exc))
    # ⚠️ 2026-08-28：拿掉 base64。這支產出的是**給人貼上去執行的**指令——
    #
    # 把腳本 base64 起來，執行的人**看不懂自己在跑什麼**（違反可稽核性），
    # 同時 SOC 看到的是「有人在這台解碼一段東西餵給 root shell」，也就是投毒的樣子。
    # 純文字反而三贏：貼的人看得懂、稽核查得到、SOC 不告警。
    #
    # 用 heredoc 包起來仍然是「一段貼上去就好」，但內容全程可讀。
    if platform == "linux":
        cmd = f"sudo bash -s <<'WEBIT3_EOF'\n{script}\nWEBIT3_EOF"
        note = ("在該機器以可 sudo 的帳號貼上執行。內容是純文字，"
                "貼之前可以先看過（已經是 root 的話把開頭的 sudo 去掉）")
    elif platform == "aix":
        # AIX 未必裝 sudo（常在 /opt/freeware/bin 或改用 RBAC），所以要求以 root 登入
        cmd = f"ksh -s <<'WEBIT3_EOF'\n{script}\nWEBIT3_EOF"
        note = "在該機器以 root 執行（AIX 未必裝 sudo，所以不走 sudo）"
    else:
        # PowerShell：直接貼腳本本文到「系統管理員 PowerShell」視窗。
        # **貼上去執行的指令不受執行原則約束**（那管的是腳本檔），
        # 所以不需要 -ExecutionPolicy Bypass，也不需要先把檔案寫到 TEMP。
        cmd = script
        note = ("在該機器開「系統管理員 PowerShell」，把下面整段貼進去。"
                "不需要調整執行原則——貼上去的指令本來就不受它約束")
    return {"platform": platform, "command": cmd, "note": note}


@router.get("/api/onboard/progress")
def onboard_progress(
    ip: str,
    session: sqlite3.Row = Depends(require_auth),
):
    """納管執行中的即時進度：目標主機現在做到哪一步。

    為什麼需要（使用者 2026-08-16）：畫面原本只能顯示「已經幾秒」，而腳本本來就會
    逐行印「已建立帳號」「佈署收集公鑰」這些話——資訊一直都在，只是被 subprocess
    一次收完、等結束才回來。改成邊跑邊寫進進度表，這裡讀出來給畫面輪詢。

    ⚠️ 只回腳本的 stdout，永不含憑證（腳本本身設計上就不含機密）。
    """
    return onboard_engine.progress_of(ip)


@router.post("/api/onboard/verify")
def onboard_verify(
    body: OnboardVerifyBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """本機執行納管指令跑完後，立刻試連驗證——不用等重新掃描、不用猜「有沒有生效」。

    使用者 2026-08-17 明確要求：「本機執行」這條路（PAM 代登、拿不到密碼那種情境）
    使用者是在系統看不到的地方跑完指令，系統原本要等下一輪「重新掃描」才會發現這台，
    完成與否完全不明顯。這支端點對單一 IP 立即試一次真收集，成功就直接看到
    hostname/os，失敗就直接看到原因——不用等、不用猜。

    跟 /api/onboard（帳密流程）不同：那支是「系統代你連進去執行納管腳本」，這支是
    「你已經自己執行過了，系統只是幫你驗證有沒有生效」，語意完全不同，所以是獨立端點。
    """
    import manage_state

    if body.platform not in ("linux", "aix", "windows"):
        raise HTTPException(400, "platform 只接受 linux、aix 或 windows")

    row = conn.execute("SELECT asset_serial FROM hardware WHERE ip = ?", (body.ip,)).fetchone()
    if row:
        asset_serial = row["asset_serial"]
    else:
        # 還沒有資產記錄（本機執行流程不像帳密流程會自動先補一筆）——
        # 驗證的前提是「這台在系統裡掛得上號」，沒有就先補到最小記錄，跟
        # /api/onboard 成功後的自動補記錄邏輯一致。
        asset_serial = f"AUTO-{body.ip}"
        conn.execute(
            "INSERT OR IGNORE INTO hardware (asset_serial, ip, environment, asset_status) "
            "VALUES (?, ?, '正式', '使用中')",
            (asset_serial, body.ip),
        )

    # 驗證的本質就是「現在馬上試連一次」，不受之前 collect_ok 的舊狀態影響——
    # 這正是使用者剛執行完腳本、還沒被任何排程重新探測過的那一刻。
    #
    # ⚠️ 2026-09-22 修正：這裡原本在**收集之前**就無條件 mark_collect_ok(=1)，
    # 而且失敗那條 return 路徑沒有收回。症狀是「按驗證當下顯示已納管，
    # 隔天排程探測又打回未納管」——使用者以為納管會自己失效。
    # 現在改成：**先收集，成功才寫 1；失敗明確寫 0 並留下原因**。
    result = manage_state.collect_facts_into_assets(conn, only_serial=asset_serial)
    row = conn.execute(
        "SELECT hostname, os, device_model FROM hardware WHERE asset_serial = ?", (asset_serial,)
    ).fetchone()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if result["updated"] >= 1:
        # [B-05] 成功寫整台（同一台的多筆登記一起），不只這一筆
        manage_state.mark_collect_ok(conn, asset_serial, now)
        _log_onboard(conn, hardware_ip=body.ip, platform=body.platform,
                     who=session["username"], ok=True, stage="verify",
                     message=f"本機執行後驗證成功：收到 {result['updated']} 個欄位")
        conn.commit()
        return {
            "ok": True, "asset_serial": asset_serial,
            "hostname": row["hostname"], "os": row["os"], "device_model": row["device_model"],
        }

    # 失敗：**明確寫回 0 並留原因**，不要停在上一次的舊狀態讓人以為還好好的
    base_err = result["failed"][0]["error"] if result["failed"] else None
    error = _collect_diagnosis(conn, body.ip, body.platform,
                               row["os"] if row else None, result["updated"], base_err)
    conn.execute(
        "UPDATE hardware SET collect_ok = 0, collect_checked_at = ?, collect_error = ? "
        "WHERE asset_serial = ?", (now, error[:500], asset_serial))
    _log_onboard(conn, hardware_ip=body.ip, platform=body.platform,
                 who=session["username"], ok=False, stage="verify", message=error)
    conn.commit()
    return {"ok": False, "asset_serial": asset_serial, "error": error}


@router.get("/api/onboard/post-collect/{asset_serial}")
def onboard_post_collect_status(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """納管後自動收集跑到哪了（2026-09-17 使用者：「我認為我納管後，所有的資料都要跟著變動」）。

    納管完成 ≠ 資料進來了：規格／服務／軟體／帳號是背景收的，要等幾十秒。
    以前畫面不會自己更新，使用者看到的還是「軟體 0」，會以為收集壞掉。
    這支讓畫面知道「還在收 / 收完了 / 哪一樣沒收到」，收完就自己重載。
    """
    import collect_log
    import post_onboard

    running = post_onboard.running_for(asset_serial)
    last = None
    for rec in collect_log.recent(conn, limit=50, kinds=["post_onboard"]):
        if rec.get("target") == asset_serial:
            last = rec
            break
    results = []
    if last:
        try:
            extra = json.loads(last.get("extra") or "{}")
            results = extra.get("results") or []
        except (TypeError, ValueError):
            results = []
    return {"running": running, "last": last, "results": results,
            "jobs": [{"job": j, "label": post_onboard.JOB_LABEL[j]} for j in post_onboard.JOBS]}


@router.get("/api/onboard/failures")
def onboard_failures_endpoint(
    include_resolved: bool = False,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """納管失敗清單：哪些機器試過但進不去、為什麼、要找誰（2026-09-17 使用者：
    「這些帳號納管失敗，我以後要怎麼查那些納管失敗過，他的 root 密碼可能不是預設密碼」）。

    只回**每台最後一次**的結果——一台上週失敗今天成功了就不該再出現在待處理裡。
    歷史仍在納管紀錄（onboard_audit）裡查得到。見 onboard_failures。
    """
    import onboard_failures

    return {"items": onboard_failures.current_failures(conn, include_resolved),
            "summary": onboard_failures.summary(conn)}


@router.get("/api/onboard/failures/export")
def onboard_failures_export(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯出納管失敗清單——拿去跟管理員要密碼的就是這張。"""
    import onboard_failures
    from urllib.parse import quote

    wb = Workbook()
    ws = wb.active
    ws.title = "納管失敗清單"
    ws.append(onboard_failures.EXPORT_HEADERS)
    for r in onboard_failures.export_rows(conn):
        ws.append(["" if v is None else v for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"納管失敗清單_{_now_local()[:10]}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


@router.get("/api/onboard/audit")
def onboard_audit_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """納管稽核紀錄（不含任何憑證）。"""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM onboard_audit ORDER BY id DESC LIMIT 100")]


@router.get("/api/onboard/audit/latest-by-ip")
def onboard_audit_latest_by_ip(
    ips: str = "",
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db),
):
    """批量查這批 IP 各自**最後一次**納管稽核紀錄（2026-10-18，不含任何憑證）。

    給 /adopt「收集結果」表用：那張表一次可能列幾百個 IP，各打一次
    `/api/onboard/audit` 太貴。`ips` 是逗號分隔的位址清單；回 `{ip: 最後一筆}`，
    **查不到的 IP 不在回傳裡**——前端要顯示「—」（這台還沒被系統納管過），
    不可以補一個假的 0 或空字串進去讓人以為是「查過、沒有」。

    跟 `onboard_failures.current_failures()` 同一套「每台最後一筆」寫法
    （MAX(id) 分組），差別是這裡不篩成敗、只給這批 IP、也不展開失敗原因。
    """
    ip_list = [x.strip() for x in ips.split(",") if x.strip()]
    if not ip_list:
        return {}
    out: dict[str, dict] = {}
    # SQLite 參數上限分批問，跟 collect_dispatch._known_assets 同一個理由
    for i in range(0, len(ip_list), 400):
        chunk = ip_list[i:i + 400]
        qs = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"""
            SELECT a.target_ip, a.platform, a.login_user, a.ok, a.created_at
              FROM onboard_audit a
              JOIN (SELECT target_ip, MAX(id) AS last_id FROM onboard_audit
                     WHERE target_ip IN ({qs}) GROUP BY target_ip) last
                ON last.last_id = a.id
            """, chunk).fetchall()
        for r in rows:
            out[r["target_ip"]] = dict(r)
    return out


@router.get("/api/auto-onboard")
def auto_onboard_state(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """B 排程自動納管的現況：總開關、授權網段、最近的自動納管稽核。

    授權網段是這功能的安全閘門——排程只碰列在這裡且啟用的網段前綴。稽核只含帳號名/成敗，
    永不含密碼。
    """
    import auto_onboard

    return {
        "enabled": auto_onboard.is_enabled(conn),
        "segments": auto_onboard.list_segments(conn),
        "recent": auto_onboard.recent_auto_audit(conn, 50),
    }


@router.patch("/api/auto-onboard/enabled")
def auto_onboard_set_enabled(
    body: AutoOnboardEnabledBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """總開關。關閉＝排程不會自動納管任何機器（預設關閉）。

    這只擋「無人在場的排程」；就算開著，也只碰授權網段內的主機。
    """
    import auto_onboard

    auto_onboard.set_enabled(conn, body.enabled)
    return {"ok": True, "enabled": auto_onboard.is_enabled(conn)}


@router.post("/api/auto-onboard/segments")
def auto_onboard_save_segment(
    body: SegmentBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """新增／更新一個授權網段（以前綴為唯一鍵）。加進來才代表「授權排程自動納管這段」。"""
    import auto_onboard

    try:
        auto_onboard.save_segment(conn, body.prefix, body.enabled, body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "segments": auto_onboard.list_segments(conn)}


@router.patch("/api/auto-onboard/segments/{seg_id}/enabled")
def auto_onboard_toggle_segment(
    seg_id: int,
    body: SegmentEnabledBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """啟用／停用一個授權網段。停用＝排程跳過但保留設定。"""
    import auto_onboard

    if not auto_onboard.set_segment_enabled(conn, seg_id, body.enabled):
        raise HTTPException(404, "查無此授權網段")
    return {"ok": True, "segments": auto_onboard.list_segments(conn)}


@router.delete("/api/auto-onboard/segments/{seg_id}")
def auto_onboard_delete_segment(
    seg_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import auto_onboard

    if not auto_onboard.delete_segment(conn, seg_id):
        raise HTTPException(404, "查無此授權網段")
    return {"ok": True, "segments": auto_onboard.list_segments(conn)}


@router.post("/api/auto-onboard/run")
def auto_onboard_run_now(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """手動「立即執行」一輪自動納管：試連 → 納管授權網段內未納管的 Linux → 收 facts。

    操作者在場的手動觸發，不受總開關約束，但仍受授權網段約束（沒授權就沒候選）。
    會花數秒到數十秒（每台一次試連／SSH），前端要顯示執行中三態。
    """
    import auto_onboard

    collector_ip = onboard_engine.resolve_collector_ip(conn)
    return auto_onboard.scheduled_cycle(collector_ip=collector_ip, conn=conn)


@router.post("/api/onboard/batch-auto/preview")
def batch_auto_preview(
    body: BatchOnboardBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """先看這批會跑哪些、擋掉哪些（正式/備援）、哪些未登記要你確認——不動任何機器。

    畫面在真的按下去之前先叫這支，讓人看清楚範圍，避免手滑把正式機放進去。
    """
    import batch_onboard_service
    return batch_onboard_service.classify_targets(conn, body.ips)


@router.post("/api/onboard/batch-auto")
def batch_auto_start(
    body: BatchOnboardBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """啟動一批自動納管（背景跑，馬上回 run_id；畫面輪詢 /status）。

    ⚠️ 統一密碼會改正式機的 root 密碼、又收不回，所以勾了 unify_password 一定要
    confirm=="YES" 才放行（同「反納管」的打字確認）。
    ⚠️ body.passwords 只往 service 傳、用完即丟——不進 DB、不進 log、不進回應。
    """
    import batch_onboard_service

    if body.unify_password and body.confirm != "YES":
        raise HTTPException(400, "勾了『統一密碼成 A』就要在確認欄輸入 YES——這會改正式機密碼且收不回")
    try:
        out = batch_onboard_service.start(
            conn, body.ips, body.username, body.passwords,
            unify_password=body.unify_password, triggered_by=session["username"],
            include_ips=body.include_ips)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return out


@router.get("/api/onboard/batch-auto/status")
def batch_auto_status(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """最近一批的進度＋逐台結果。畫面輪詢這個。"""
    import batch_onboard_service
    return batch_onboard_service.status(conn)


@router.get("/api/onboard/batch-auto/export")
def batch_auto_export(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把最近一批的結果匯出 xlsx（留存／交資安）。**不含任何密碼**，只有『用第幾組』。"""
    import batch_onboard_service

    st = batch_onboard_service.status(conn)
    wb = Workbook()
    ws = wb.active
    ws.title = "批次納管結果"
    ws.append(["IP", "環境別", "登入", "用哪組密碼", "已納管", "統一成A", "失敗階段", "原因"])
    pw_label = {0: "A", 1: "B"}
    for r in st["results"]:
        ws.append([
            r["ip"], r["environment"] or "",
            "成功" if r["login_ok"] else "失敗",
            pw_label.get(r["password_index"], "都不對" if r["login_ok"] is not None else ""),
            "是" if r["onboarded"] else "否",
            "是" if r["password_unified"] else "",
            r["fail_stage"] or "", r["fail_reason"] or "",
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from urllib.parse import quote
    fname = f"批次納管結果_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/api/scripts")
def scripts_list(
    session: sqlite3.Row = Depends(require_auth),
):
    """可以單獨下載的檢核／維運腳本。

    使用者要的動線是「網頁下載 → scp 到 AIX → ksh 跑」，為了一支腳本下載 3.3MB
    的完整更新包不合理。清單是**掃 scripts/ 目錄**算出來的，不是寫死檔名——
    檔名會變（fcbaixsh 正在改名成 fcb_aix.ksh），寫死那天就會壞在「按鈕還在、
    點下去 404」這種最難聯想的地方。
    """
    import script_downloads

    return {"items": script_downloads.list_scripts()}


@router.get("/api/scripts/{name}/download")
def scripts_download(
    name: str,
    session: sqlite3.Row = Depends(require_auth),
):
    """下載單一腳本。以附件送出、內容一律 LF。

    · 只認 list_scripts() 列得出來的檔名，帶路徑的輸入在查表那一步就落空
      （見 script_downloads.read_script 的說明），不是靠字串過濾去猜。
    · LF：這些檔案要在 AIX/Linux 跑，CRLF 會讓 #!/usr/bin/ksh 帶著 \r 直接執行失敗。
    · 走 require_auth，跟其他端點同一層存取控制——不因為「只是一支腳本」就放行。
    """
    import script_downloads

    try:
        fname, body = script_downloads.read_script(name)
    except KeyError:
        raise HTTPException(404, f"沒有這支可下載的腳本：{name}")
    return Response(
        content=body,
        # text/plain 會被瀏覽器直接渲染在分頁裡；要的是存成檔案。
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{fname}"',
                 "X-Content-Type-Options": "nosniff"},
    )


@router.post("/api/onboard-exempt")
def add_onboard_exempt(
    body: ExemptBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """標記為非納管設備（客製化系統／Oracle／廠商維護等）。原因必填。見 onboard_exempt。"""
    import onboard_exempt

    try:
        return onboard_exempt.add(conn, body.serials, body.reason, body.kind,
                                  session["username"] if session else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/onboard-exempt/remove")
def remove_onboard_exempt(
    body: ExemptRemoveBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """取消豁免：這台又要納管了。軟刪，紀錄留著。"""
    import onboard_exempt

    return {"removed": onboard_exempt.remove(conn, body.serials,
                                             session["username"] if session else None)}


@router.get("/api/onboard-exempt")
def list_onboard_exempt(
    include_removed: bool = False,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """目前被豁免的清單（誰標的、為什麼）。"""
    import onboard_exempt

    return {"items": onboard_exempt.list_all(conn, include_removed), "kinds": list(onboard_exempt.KINDS)}


@router.get("/api/golive")
def golive_list(
    status: str = Query("open"),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """上線檢查表清單（預設看還沒過的——那才是待辦）。"""
    rows = conn.execute(
        "SELECT g.*, h.hostname, h.ip, h.os, h.asset_status, h.asset_name "
        "FROM golive_check g LEFT JOIN hardware h ON h.asset_serial = g.asset_serial "
        "WHERE g.status = ? ORDER BY g.started_at DESC",
        (status,),
    ).fetchall()
    out = []
    for r in rows:
        detail = golive.get_check_detail(conn, r["asset_serial"])
        out.append({**dict(r), "total": detail["total"], "done": detail["done"]})
    return {"checks": out}


@router.get("/api/golive/{asset_serial}")
def golive_detail(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if conn.execute(
        "SELECT 1 FROM hardware WHERE asset_serial = ?", (asset_serial,)
    ).fetchone() is None:
        raise HTTPException(404, f"找不到資產 {asset_serial}")
    golive.refresh_auto_results(conn, asset_serial)
    return golive.get_check_detail(conn, asset_serial)


@router.post("/api/golive/{asset_serial}/item")
def golive_set_item(
    asset_serial: str,
    body: GoliveItemBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        golive.set_item_verdict(conn, asset_serial, body.item_key, body.verdict,
                                session["username"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return golive.get_check_detail(conn, asset_serial)


@router.post("/api/golive/{asset_serial}/pass")
def golive_pass(
    asset_serial: str,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """全部項目處理完才准通過；通過同時把資產轉「使用中」並把 auto 項存成基線。"""
    try:
        return golive.pass_check(conn, asset_serial, session["username"])
    except ValueError as e:
        raise HTTPException(400, str(e))


# ===== 機房搬遷盤點表匯出（2026-09-13）=====
@router.get("/api/relocation/locations")
def relocation_locations_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    import relocation_export
    return {"items": relocation_export.locations(conn)}


@router.post("/api/relocation/import")
def relocation_import_endpoint(
    file: UploadFile = File(...),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """匯入現場填回的 Master → 逐欄跟系統對帳 → 回一份校對版（綠同/紅異/藍補）＋差異列進CIA待異動。
    只讀不寫回資料庫。"""
    import relocation_import
    from urllib.parse import quote

    try:
        raw = file.file.read()
        buf, summary = relocation_import.reconcile(conn, raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 — openpyxl 對壞檔的例外型態不一
        raise HTTPException(400, f"讀不了這個檔：{exc}")
    fname = f"機房搬遷盤點表_校對版_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}",
                 "X-Same": str(summary["same"]), "X-Diff": str(summary["diff"]),
                 "X-Filled": str(summary["filled"]), "X-Unknown": str(summary["unknown_serial"])},
    )


@router.get("/api/relocation/export")
def relocation_export_endpoint(
    location: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把現有資料預填成搬遷 Master 的 4 張表（Server Master/LAN/FC SAN/Power）並下載。"""
    import relocation_export
    from urllib.parse import quote

    buf, n = relocation_export.build(conn, location)
    tag = (location or "全部").replace("/", "_")
    fname = f"機房搬遷盤點表_{tag}_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}",
                 "X-Row-Count": str(n)},
    )


@router.get("/api/adopt/move-choices")
def adopt_move_choices(session: sqlite3.Row = Depends(require_auth)):
    """可以搬到哪些頁簽，以及搬過去會寫進 os_override 的哪一類。"""
    import collect_dispatch
    return {"items": collect_dispatch.move_choices()}


@router.post("/api/adopt/move-tab")
def adopt_move_tab(
    body: AdoptMoveBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把一台或多台搬到別的頁簽（批次用同一支，逐台回報成敗）。

    認不出是哪一台的（沒登記、既沒有主機名＋IP 也沒有序號）會逐筆回在 `failed`，
    不是整批失敗——一批 20 台裡有 2 台認不出來，不該讓另外 18 台也白做。
    """
    import collect_dispatch
    import os_override

    if not body.targets:
        raise HTTPException(400, "沒有要搬的目標")
    os_type = None
    if body.tab is not None:
        os_type = collect_dispatch.os_type_for_tab(body.tab)
        if not os_type:
            raise HTTPException(400, f"不能搬到「{body.tab}」這個頁簽"
                                     f"（可搬：{'、'.join(collect_dispatch.MOVABLE_TABS)}）")

    moved, failed = [], []
    for t in body.targets:
        try:
            if os_type is None:
                os_override.clear(conn, t.hostname, t.ip, t.asset_serial, session["username"])
            else:
                # auto_os_type：指定當下系統自動判的類別（留證據，畫面要寫得出「從哪類改到哪類」）
                auto = None
                row = conn.execute(
                    "SELECT os, device_model, hostname, asset_name FROM hardware "
                    "WHERE ip = ? LIMIT 1", (t.ip,)).fetchone()
                if row is not None:
                    import pipeline
                    auto = pipeline._os_type(row["os"], None, row["device_model"],
                                             (row["hostname"], row["asset_name"]))
                os_override.set_override(conn, t.hostname, t.ip, t.asset_serial,
                                         os_type, auto, body.reason, session["username"])
            moved.append(t.ip)
        except ValueError as exc:
            failed.append({"ip": t.ip, "error": str(exc)})
    return {"ok": not failed, "moved": moved, "failed": failed,
            "tab": body.tab, "os_type": os_type}


__all__ = [
    'onboard_endpoint',
    'revoke_endpoint',
    'agent_stage',
    'agent_facts',
    'onboard_batch_probe',
    'onboard_hint',
    'list_credentials',
    'save_credential',
    'delete_credential',
    'onboard_script',
    'onboard_progress',
    'onboard_verify',
    'onboard_post_collect_status',
    'onboard_failures_endpoint',
    'onboard_failures_export',
    'onboard_audit_endpoint',
    'auto_onboard_state',
    'auto_onboard_set_enabled',
    'auto_onboard_save_segment',
    'auto_onboard_toggle_segment',
    'auto_onboard_delete_segment',
    'auto_onboard_run_now',
    'batch_auto_preview',
    'batch_auto_start',
    'batch_auto_status',
    'batch_auto_export',
    'scripts_list',
    'scripts_download',
    'add_onboard_exempt',
    'remove_onboard_exempt',
    'list_onboard_exempt',
    'golive_list',
    'golive_detail',
    'golive_set_item',
    'golive_pass',
    'relocation_locations_endpoint',
    'relocation_import_endpoint',
    'relocation_export_endpoint',
    'adopt_move_choices',
    'adopt_move_tab',
]
