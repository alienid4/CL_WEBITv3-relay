from __future__ import annotations

from _app_shared import *  # noqa: F401,F403 - 還原原本模組命名空間

router = _RouteCollector()



@router.post("/api/collect/dispatch")
def collect_dispatch_run(
    body: CollectDispatchBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """一個入口跑完整收集流程，回一張分得出成敗的結果表。

    會花數秒到數分鐘（每台要探測、通的還要真的連進去收），前端必須顯示執行中三態。
    憑證（WinRM）由加密憑證庫提供、用完即丟，不進回應也不進結果表。
    """
    import collect_dispatch
    import collect_log

    by = session["username"]
    target = (body.targets or "").strip()[:200]

    # 自動分批（2026-09-16 使用者：「你應該幫我自動分」「你有分批嗎?」）：
    # 超過一批的量改在**背景**跑，馬上回 run_id，畫面輪詢 /latest 看進度——
    # 同步跑會被 HTTP 逾時切斷，使用者看到的就是「收集沒有完成（跑了 0 秒）」。
    # 一批以內維持同步，行為與以往完全相同。
    try:
        count = len(collect_dispatch.parse_targets(body.targets))
    except ValueError as exc:
        collect_log.record(conn, "dispatch", target, False, str(exc), actor=by)
        raise HTTPException(400, str(exc)) from exc
    if count > collect_dispatch.BATCH_SIZE:
        try:
            started = collect_dispatch.start_background(None, body.targets, triggered_by=by)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        gap = collect_dispatch.batch_gap_seconds(conn)
        collect_log.record(
            conn, "dispatch", target, True,
            f"{started['total']} 台自動分成 {started['batch_total']} 批（每批最多 "
            f"{collect_dispatch.BATCH_SIZE} 台、間隔 {gap} 秒），已在背景開始",
            actor=by, extra=started)
        return {**started, "batch_size": collect_dispatch.BATCH_SIZE, "gap_seconds": gap}
    # 成功失敗都記（2026-09-15 使用者：「搜尋不到或有什麼失敗都沒有任何輸出，不知道錯在哪」）
    try:
        out = collect_dispatch.run_dispatch(conn, body.targets, triggered_by=by)
    except ValueError as exc:
        collect_log.record(conn, "dispatch", target, False, str(exc), actor=by)
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 - 未預期的也要留證據再往上丟
        collect_log.record(conn, "dispatch", target, False, f"{type(exc).__name__}: {exc}",
                           actor=by, exc=exc)
        raise
    bs = out.get("by_status") or {}
    collect_log.record(
        conn, "dispatch", target, True,
        # 講清楚「探測了幾個位址」而不是「幾台」——掃一個 /24 是 254 個位址，多數是空的。
        # 使用者 2026-09-16 問「為什麼是 508，還要人工處理?」就是被這個說法誤導的。
        f"探測 {out.get('total', 0)} 個位址：已納管 {out.get('collected', 0)}、"
        f"要人工處理 {out.get('needs_action', 0)}、"
        f"{collect_dispatch.NOT_ONBOARDABLE_TEXT} {out.get('not_onboardable', 0)}、"
        f"空位址（沒回應也沒登記）{out.get('empty_addresses', 0)}、"
        f"已登記但只能匯入 {bs.get('import_only', 0) - out.get('empty_addresses', 0)}",
        actor=by, extra={"run_id": out.get("run_id"), "by_status": bs,
                         "by_route": out.get("by_route")})
    return out


@router.get("/api/collect/dispatch/gap")
def collect_dispatch_gap_get(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """自動分批的設定：一批幾台、批與批之間停幾秒、現在有沒有在跑。"""
    import collect_dispatch

    return {"gap_seconds": collect_dispatch.batch_gap_seconds(conn),
            "batch_size": collect_dispatch.BATCH_SIZE,
            "hard_max": collect_dispatch.HARD_MAX_TARGETS,
            "running": collect_dispatch.background_running()}


@router.put("/api/collect/dispatch/gap")
def collect_dispatch_gap_put(
    body: DispatchGapBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """批與批之間停幾秒（使用者 2026-09-16：「預設每個網段間距2分鐘」「可以設定改幾分鐘」）。

    間隔是刻意的：一次對上千台送封包對網路不客氣，也容易被 SOC 當成掃描行為。
    但要幾分鐘由使用者決定——不同公司的網路與監控容忍度差很多。
    """
    from db import set_setting

    if not 0 <= body.gap_seconds <= 3600:
        raise HTTPException(400, "間隔要在 0～3600 秒（0～60 分鐘）之間")
    set_setting(conn, "dispatch_batch_gap_seconds", str(int(body.gap_seconds)))
    return {"gap_seconds": body.gap_seconds}


@router.get("/api/collect/dispatch/latest")
def collect_dispatch_latest(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """最近一次分派的結果。頁面重新整理／隔天回來還看得到上次跑到哪，不用重跑。"""
    import collect_dispatch

    return collect_dispatch.latest_run(conn) or {"run": None, "results": [], "total": 0,
                                                 "collected": 0, "needs_action": 0,
                                                 "by_status": {}, "by_route": {}}


@router.post("/api/collect/agent-package")
def collect_agent_package(
    body: AgentPackageBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """替一台「系統進不去」的主機產 Push Agent 安裝包。

    key 綁在資產上，所以這台一定要先有資產記錄。未登記的在這裡補一筆最小資產——
    這是**人明確按下按鈕**才發生的（跟排程自動納管刻意不自動建資產不同，
    見 auto_onboard 的說明），欄位只填系統知道的，業務欄位留空給人補。
    """
    ip = (body.ip or "").strip()
    if not ip:
        raise HTTPException(400, "缺少 ip")
    row = conn.execute("SELECT asset_serial FROM hardware WHERE ip = ? LIMIT 1", (ip,)).fetchone()
    if row:
        serial = row["asset_serial"]
    else:
        scan_hn = conn.execute(
            "SELECT hostname FROM scan_history WHERE ip = ? AND scan_ok = 1 "
            "ORDER BY scan_time DESC LIMIT 1", (ip,)).fetchone()
        serial = f"AUTO-{ip}"
        insert_hardware(
            conn, asset_serial=serial, ip=ip,
            hostname=(scan_hn["hostname"] if scan_hn and scan_hn["hostname"] else None),
            environment="正式", asset_status="使用中",
        )

    scripts_dir = _BACKEND_DIR / "agent_scripts"
    key = agent_auth.issue_host_key(conn, serial)
    collector_url = f"http://{onboard_engine.resolve_collector_ip(conn)}:8000"
    return {
        "ip": ip,
        "asset_serial": serial,
        "created_asset": row is None,
        "collector_url": collector_url,
        # 交給「要請人裝」的那位的東西：一支自帶全部內容的 bootstrap 腳本＋一行指令。
        # 腳本本身固定不變（好稽核／好比對版本），會變的 key 與 URL 另外帶。
        #
        # ⚠️ 2026-08-28：push_agent.sh／install.sh 不再單獨交出去——它們已經內嵌在
        # bootstrap_watcher.sh 裡，由 root 那一次佈署固定到 /opt/webit3-agent/bin/。
        # 單獨交出去只會誘導人把可執行內容放進投放目錄，那正是被修掉的提權路徑。
        "install_command": "sudo sh /tmp/bootstrap_watcher.sh",
        "files": {
            "agent_key": key,
            "collector_url": collector_url,
            "bootstrap_watcher.sh": (scripts_dir / "bootstrap_watcher.sh").read_text(
                encoding="utf-8"),
        },
    }


@router.get("/api/pipeline")
def pipeline_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """納管漏斗：每台機器走到哪一關、下一步要做什麼。

    使用者 2026-08-16：「300 台測試機要一台一台匯，我至少要知道哪些是我還需要處理的。」
    四態只分到「連不連得進去」；這裡把「已納管」再往後拆成事實／服務／帳號幾關，
    每台剛好落在它還沒完成的第一關（互斥窮盡，可對帳）。
    """
    import number_proof
    import pipeline

    s = pipeline.summarize(conn)
    # [B-09] 頂端 5 個關鍵數字＋ⓘ說明＋下鑽條件（數字從同一份 summary 數，跟下面的表同源）
    s["key_numbers"] = number_proof.key_numbers(conn, s)
    return s


@router.get("/api/number-proof")
def number_proof_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """[B-09] 數字健檢：漏斗 5 個數字的 畫面值／獨立重算值／差異／差異解釋（逐台歸因）。"""
    import number_proof

    return number_proof.health(conn)


@router.get("/api/manage-state")
def manage_state_endpoint(
    summary: bool = False,
    state: str | None = None,
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """納管四態：未登記／未納管／已納管／失聯。互斥且窮盡，加總＝你知道的所有機器。

    跟 asset_status 是兩條獨立的軸——一台可以同時「使用中」而且「連不進去」。
    儀表板四格與資產清單的狀態欄共用這一份，數字才不會跟清單對不上。

    `state=<狀態>`（2026-10-18）：只回這一態的逐台代表清單，給首頁「對帳明細」死連結
    改真下鑽用。跟首頁數字算的是同一份 `summarize()`，筆數保證一致——不是另外重算一次。
    """
    import manage_state

    out = manage_state.summarize(conn)
    if state is not None:
        if state not in manage_state.ALL_STATES:
            raise HTTPException(
                400, f"不合法的狀態：{state}；合法值：{'、'.join(manage_state.ALL_STATES)}")
        state_items = out["rep_items"].get(state, [])
        return JSONResponse({
            "state": state, "scan_time": out["scan_time"],
            "total": len(state_items), "items": state_items,
        })
    if summary:
        # 首頁只要數字：逐筆明細（上千筆）不送，頁面才不會變慢
        out = {k: v for k, v in out.items() if k not in ("items", "rep_items")}
    # 直接回 JSONResponse：內容全是 str/int/None，FastAPI 預設會先逐格跑一遍
    # jsonable_encoder 再 dumps，4,788 筆時那一遍就佔整支一半以上（Q42）。輸出位元組不變。
    return JSONResponse(out)


@router.post("/api/manage-state/collect")
def manage_state_collect(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """對已納管的機器收 facts 並寫回資產（真 OS／序號／機型）。"""
    import manage_state

    return manage_state.collect_facts_into_assets(conn)


@router.post("/api/manage-state/refresh")
def manage_state_refresh(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """手動觸發「試連所有已登記主機」。會花數秒到數十秒（每台一次 SSH）。"""
    import manage_state

    return manage_state.refresh_collect_status(conn)


@router.get("/api/merge-reviews")
def list_merge_reviews(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """待人工判定的合併案（身分解析判不準的）。空＝沒有懸而未決的。

    這些是「同 IP 但不同 UUID」這種不敢自動合併的案子——攤開來讓人決定，
    不要藏著，否則等於資料默默對不上卻沒人知道。
    """
    rows = conn.execute(
        "SELECT mr.id, mr.reason, mr.candidates, mr.status, mr.created_at, "
        "sr.source, sr.payload FROM merge_review mr "
        "JOIN source_record sr ON sr.id = mr.source_record_id "
        "WHERE mr.status = 'open' ORDER BY mr.id DESC LIMIT 200").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["candidates"] = json.loads(d["candidates"]) if d["candidates"] else []
            d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
        except (ValueError, TypeError):
            pass
        out.append(d)
    return out


@router.get("/api/merge-reviews/auto-matchable")
def merge_reviews_auto_matchable(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """待審案裡有多少可以用「短名＋IP 同時相符」自動配對。

    1,004 筆待審一筆筆看是做不完的，而其中大半只是「vCenter 給 FQDN、Excel 填短名」
    這一個原因。先算出可安全批次處理的數量，剩下的才值得花人力逐筆判斷。
    """
    total_open = conn.execute(
        "SELECT COUNT(*) FROM merge_review WHERE status = 'open'"
    ).fetchone()[0]
    m = _scan_auto_matchable(conn)

    # 第二條規則（2026-08-24）：實查 221 發現 3333 筆待審裡有 2140 筆的證據其實比
    # 這條短名規則更強——vCenter 帶著 vm_uuid，而資產那邊 uuid 是空的。畫面上兩條
    # 規則各自列出「可解幾筆／幾台」與樣本，讓人分開判斷、分開執行。
    import merge_autoresolve

    up = merge_autoresolve.plan(conn)
    uuid_rule = {
        "rule": _UUID_RULE,
        "rule_label": "vCenter 有 vm_uuid、資產這邊還沒填（合併＝補上身分證號）",
        "matchable": len(up["resolvable"]),
        # 「幾筆」跟「幾台」是兩個數字：重複匯入讓同一台被記了好幾筆待審核，
        # 只講筆數會讓人以為要判斷 2140 次。
        "distinct_assets": up["distinct_assets"],
        "skipped": up["skipped"],
        "samples": [
            {
                "incoming_hostname": x["source_hostname"],
                "incoming_ip": x["source_ip"],
                "target_serial": x["asset_serial"],
                "target_hostname": x["asset_hostname"],
                "target_ip": x["asset_ip"],
                "vm_uuid": x["vm_uuid"],
                "matched_on": x["matched_on"],
            }
            for x in up["resolvable"][:10]
        ],
    }

    return {
        "total_open": total_open,
        "matchable": len(m),
        "remaining": total_open - len(m),
        "rule": _AUTO_RULE,
        "rule_label": "切掉網域後主機名與 IP 同時相符",
        "uuid_rule": uuid_rule,
        # 給畫面看幾筆長什麼樣，確認規則沒抓錯再按執行
        "samples": [
            {
                "incoming_hostname": x["incoming_hostname"],
                "incoming_ip": x["incoming_ip"],
                "target_serial": x["target_serial"],
                "target_hostname": x["target_hostname"],
            }
            for x in m[:10]
        ],
    }


@router.post("/api/merge-reviews/batch-merge")
def merge_reviews_batch_merge(
    body: BatchMergeBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把可自動配對的待審案一次併進既有資產。

    只接受預先定義好的規則名稱，不接受前端傳 id 清單——後者等於把「要合併哪些」
    的決定權交給畫面，一旦畫面算錯就會把資料併到錯的機器上，而合併錯不會噴錯、很難救。

    合併時**只寫機器事實**（os／vm_uuid／is_vm／hostname），業務欄位（用途、保管者、
    機房、盤點單位）一律不動——那些是人維護的，vCenter 不知道也無權覆蓋。
    """
    if body.rule == _UUID_RULE:
        import merge_autoresolve

        return merge_autoresolve.apply(conn, session["username"])

    if body.rule != _AUTO_RULE:
        raise HTTPException(400, f"不支援的規則：{body.rule}")

    matches = _scan_auto_matchable(conn)
    FACTS = ("os", "vm_uuid", "is_vm")
    merged = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for m in matches:
        d = m["payload"]
        setters = {k: d[k] for k in FACTS if d.get(k) not in (None, "")}
        import dyn_shell
        if dyn_shell.is_cia_target(conn, m["target_id"]):   # 審查 #7：寫 CIA 正本一律過 cia_safe_setters（保 os_cia）
            setters = dyn_shell.cia_safe_setters(conn, m["target_id"], setters)
        if setters:
            assigns = ", ".join(f"{k} = ?" for k in setters)
            conn.execute(
                f"UPDATE hardware SET {assigns}, updated_at = ? WHERE id = ?",
                (*setters.values(), now, m["target_id"]),
            )
        # 來源紀錄回填解析結果，之後查得出這筆是靠哪條規則併上去的
        conn.execute(
            "UPDATE source_record SET resolved_status = 'matched', resolved_hardware_id = ?, "
            "resolved_rule = ?, resolved_confidence = ? WHERE id = ?",
            (m["target_id"], f"batch:{_AUTO_RULE}", 0.9, m["source_record_id"]),
        )
        conn.execute(
            "UPDATE merge_review SET status = 'merged', decided_by = ?, decided_at = ? "
            "WHERE id = ? AND status = 'open'",
            (session["username"], now, m["review_id"]),
        )
        merged += 1
    conn.commit()

    remaining = conn.execute(
        "SELECT COUNT(*) FROM merge_review WHERE status = 'open'"
    ).fetchone()[0]
    return {"merged": merged, "remaining_open": remaining, "rule": _AUTO_RULE}


@router.get("/api/vcenter-autoimport")
def vcenter_autoimport_status(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """VC 自動匯入（方案 B）現況：設定＋鮮度燈（今晚的匯出到底有沒有進來）。"""
    import vcenter_autoimport

    return vcenter_autoimport.health(conn)


@router.put("/api/vcenter-autoimport")
def vcenter_autoimport_save(
    body: VcAutoImportBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """設定監看資料夾＋開關＋逾時門檻。開了之後排程器每輪會看資料夾有沒有新檔。"""
    import vcenter_autoimport

    vcenter_autoimport.set_config(conn, body.enabled, body.dir, body.max_age_hours)
    return vcenter_autoimport.health(conn)


@router.post("/api/vcenter-autoimport/run")
def vcenter_autoimport_run(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """手動「立即抓一次」：找監看資料夾最新且寫完的 RVTools 檔，比上次新才匯。"""
    import vcenter_autoimport

    return vcenter_autoimport.pickup(conn)


@router.get("/api/connections")
def list_connections_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    return [_serialize_connection(r) for r in list_connections(conn)]


@router.get("/api/connections/suggest-segments")
def suggest_segments(
    fmt: str = Query("json", pattern="^(json|txt)$"),
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """從既有資產的 IP 反推該掃描哪些網段，並對上機房。

    fmt=txt 回純文字的 segments.txt，直接餵給外部掃描機的 scan_segments.py。
    那台連不到這裡的資料庫（正是要外部掃描的原因），清單只能由人搬過去。

    為什麼要有這支：資產清單是匯入進來的（幾千筆），但掃描目標得一個一個手動建，
    只要漏建，那個網段的機器就全部變成「失聯」——看起來像機器出事，其實只是沒去掃。
    網段資訊本來就藏在資產的 IP 裡，不該再要人另外整理一份網段表。

    每個網段會標出資產分布在哪些機房。同一網段對到多個機房時一併回報：
    那要嘛是真的跨機房共用，要嘛是資產的機房欄位填錯——後者正是盤點系統該抓的問題。
    """
    import manage_state

    existing = set()
    for c in conn.execute("SELECT target FROM connections").fetchall():
        t = (c["target"] or "").strip()
        if t:
            existing.add(t)

    segs: dict[str, dict] = {}
    bad_ip = 0
    for r in conn.execute(
        "SELECT ip, physical_location FROM hardware WHERE ip IS NOT NULL AND ip != ''"
    ).fetchall():
        parts = (r["ip"] or "").strip().split(".")
        if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            bad_ip += 1
            continue
        cidr = ".".join(parts[:3]) + ".0/24"
        s = segs.setdefault(cidr, {"cidr": cidr, "hosts": 0, "locations": {}})
        s["hosts"] += 1
        loc = manage_state.group_location(r["physical_location"])
        s["locations"][loc] = s["locations"].get(loc, 0) + 1

    out = []
    for s in segs.values():
        locs = sorted(s["locations"].items(), key=lambda kv: -kv[1])
        main_loc = locs[0][0] if locs else "未填"
        out.append({
            "cidr": s["cidr"],
            "hosts": s["hosts"],
            "main_location": main_loc,
            "locations": dict(locs),
            "mixed": len(locs) > 1,
            "already": s["cidr"] in existing,
            # 建議的連線名稱：帶上機房，之後在清單裡一眼看得出這條是掃哪裡的
            "suggest_name": f"{main_loc} {s['cidr']}",
        })
    out.sort(key=lambda x: -x["hosts"])

    if fmt == "txt":
        # 只放整行註解、不放行尾註解：scan_segments.py 的 load_segments 只跳過整行 #，
        # 行尾註解會讓整行解析失敗被略過——那會變成「檔案看起來有 129 段、實際掃 0 段」。
        # 機房資訊是給人看的，UI 上就有，不必塞進要餵給機器的檔。
        covered = sum(s["hosts"] for s in out)
        lines = [
            "# 網段掃描清單 —— 由戰情室依現有資產 IP 反推（每筆取前三段成 /24）",
            f"# 產生時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"# 網段數：{len(out)}　涵蓋資產：{covered} 台",
        ]
        if bad_ip:
            # 這些資產不屬於任何網段，掃描永遠看不到它們。不講的話，
            # 掃完發現「還有機器沒出現」會回頭懷疑掃描器，其實是資料本身有問題。
            lines.append(
                f"# ⚠ 另有 {bad_ip} 筆資產的 IP 格式不正確，未納入任何網段——"
                "掃描不會涵蓋它們，請在系統裡修正後重新下載"
            )
        lines += [
            "#",
            "# 一行一個網段，# 開頭為註解。要增減網段直接改這個檔即可。",
            "",
        ]
        lines += [s["cidr"] for s in out]
        # 檔名固定 segments.txt，不加時間戳：mmap.sh 只認這個檔名，
        # 帶戳記就得人工改名，漏改會變成「找不到網段清單」而中止。
        # 產生時間寫在檔頭註解裡，要區分版本看那裡。
        return Response(
            content="\n".join(lines) + "\n",
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="segments.txt"'},
        )

    return {"segments": out, "bad_ip_count": bad_ip, "existing_count": len(existing)}


@router.post("/api/connections/batch-segments")
def batch_create_segments(
    body: SegmentBatchBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把選定的網段一次建成掃描來源。已存在的略過，重複點不會長出一堆重複設定。"""
    import manage_state

    existing = {
        (c["target"] or "").strip()
        for c in conn.execute("SELECT target FROM connections").fetchall()
    }

    # 先算好每個網段的主要機房，名稱才帶得出「板橋 10.99.198.0/24」
    loc_of: dict[str, str] = {}
    counter: dict[str, dict[str, int]] = {}
    for r in conn.execute(
        "SELECT ip, physical_location FROM hardware WHERE ip IS NOT NULL AND ip != ''"
    ).fetchall():
        parts = (r["ip"] or "").strip().split(".")
        if len(parts) != 4 or not all(p.isdigit() for p in parts):
            continue
        cidr = ".".join(parts[:3]) + ".0/24"
        loc = manage_state.group_location(r["physical_location"])
        counter.setdefault(cidr, {})
        counter[cidr][loc] = counter[cidr].get(loc, 0) + 1
    for cidr, d in counter.items():
        loc_of[cidr] = max(d.items(), key=lambda kv: kv[1])[0]

    created, skipped = [], []
    for cidr in body.cidrs:
        cidr = (cidr or "").strip()
        if not cidr:
            continue
        if cidr in existing:
            skipped.append(cidr)
            continue
        name = f"{loc_of.get(cidr, '未知')} {cidr}"
        create_connection_record(conn, name, "網路掃描", cidr, None, None, None)
        existing.add(cidr)
        created.append({"cidr": cidr, "name": name})
    return {"created": created, "skipped": skipped}


@router.post("/api/connections")
def create_connection_endpoint(
    body: ConnectionBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    new_id = create_connection_record(
        conn, body.name, body.connection_type, body.target, body.port, body.username, body.password
    )
    return _serialize_connection(get_connection_by_id(conn, new_id))


@router.put("/api/connections/{connection_id}")
def update_connection_endpoint(
    connection_id: int,
    body: ConnectionBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if get_connection_by_id(conn, connection_id) is None:
        raise HTTPException(404, "查無此連線設定")
    # 密碼write-only的另一半：留空（None或空字串）代表「這次不變更密碼」，不是「清空密碼」。
    password_to_set = body.password if body.password else None
    update_connection_record(
        conn,
        connection_id,
        body.name,
        body.connection_type,
        body.target,
        body.port,
        body.username,
        password_to_set,
    )
    return _serialize_connection(get_connection_by_id(conn, connection_id))


@router.patch("/api/connections/{connection_id}/enabled")
def set_connection_enabled_endpoint(
    connection_id: int,
    body: ConnectionEnabledBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """來源啟用／停用。停用的來源排程掃描會直接跳過，不會被算成「掃描失敗」。

    存在的理由：有些來源現階段本來就連不到（例如 CMDB Gateway 在家裡碰不到公司內網）。
    沒有開關的話它每次掃描都失敗、每次都把「掃描不完整」點亮——常態性的假警報
    會讓真正的掃描問題沒人看見。
    """
    if get_connection_by_id(conn, connection_id) is None:
        raise HTTPException(404, "查無此連線設定")
    set_connection_enabled(conn, connection_id, body.enabled)
    return _serialize_connection(get_connection_by_id(conn, connection_id))


@router.patch("/api/connections/{connection_id}/scan-time")
def set_connection_scan_time_endpoint(
    connection_id: int,
    body: ConnectionScanTimeBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """設這個網段自己的每日排程時間（HH:MM）。設了就在那個時間單獨掃這一段；
    留空＝清掉、回到跟全域排程一起掃。讓公司多個測試網段可以錯開時間、不擠在同一時刻。"""
    import db as _db
    if get_connection_by_id(conn, connection_id) is None:
        raise HTTPException(404, "查無此連線設定")
    t = (body.scan_time or "").strip()
    if t:
        import re as _re
        if not _re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", t):
            raise HTTPException(400, "時間格式要是 HH:MM（24 小時制），例如 03:30")
    _db.set_connection_scan_time(conn, connection_id, t or None)
    return _serialize_connection(get_connection_by_id(conn, connection_id))


@router.delete("/api/connections/{connection_id}")
def delete_connection_endpoint(
    connection_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if get_connection_by_id(conn, connection_id) is None:
        raise HTTPException(404, "查無此連線設定")
    delete_connection_record(conn, connection_id)
    return {"ok": True}


@router.post("/api/connections/{connection_id}/test")
def test_connection_endpoint(
    connection_id: int,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """D15「重新檢查」按鈕：真的做一次TCP連線測試，不是硬編一個假狀態。這個階段還沒有
    真實vCenter/SNMP協定整合（S3的ScanSource還是mock，接上真實協定是之後的工作），但
    port通不通得起來是真訊號，比起顯示假的「已連線」更誠實，符合D31精神。
    """
    row = get_connection_by_id(conn, connection_id)
    if row is None:
        raise HTTPException(404, "查無此連線設定")
    if not row["port"]:
        raise HTTPException(400, "這筆連線沒有設定Port，無法測試")
    try:
        with socket.create_connection((row["target"], row["port"]), timeout=3):
            status = "綠"
    except OSError:
        status = "紅"
    update_connection_status(conn, connection_id, status)
    return _serialize_connection(get_connection_by_id(conn, connection_id))


# ============ 掃描：手動重掃 + 排程設定 ============
@router.post("/api/scan/run")
def scan_run_endpoint(session: sqlite3.Row = Depends(require_auth)):
    """手動重掃：背景執行，馬上回覆，前端輪詢 /api/scan/status 取四態。"""
    if not scan_service.start_scan("manual"):
        raise HTTPException(409, "已有一次掃描正在進行，請稍候")
    return {"started": True}


@router.get("/api/scan/status")
def scan_status_endpoint(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    return scan_service.latest_status(conn)


@router.get("/api/scan/schedule")
def scan_schedule_get_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    return scan_service.get_schedule(conn)


@router.put("/api/scan/schedule")
def scan_schedule_put_endpoint(
    body: ScheduleBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        scan_service.set_schedule(conn, body.enabled, body.mode, body.time, body.interval_hours,
                                  body.weekday)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return scan_service.get_schedule(conn)


@router.get("/api/scan/scope")
def scan_scope_get(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """列出全部網段，標明是否已納入掃描、資安是否建議排除、展開後幾個位址。"""
    import scan_scope

    return scan_scope.list_scope(conn)


@router.put("/api/scan/scope")
def scan_scope_put(
    body: ScanScopeBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把勾選的網段同步成掃描來源。沒勾的停用（不刪，保留上次掃描時間）。"""
    import scan_scope

    # 操作紀錄由中介層統一記（每個 PUT 都會進 activity_log），這裡不重複記
    return scan_scope.apply_scope(conn, body.segment_ids,
                                  session["username"] if session else None)


@router.put("/api/scan/scope/policy")
def scan_scope_policy_put(
    body: ScanScopePolicyBody,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """存掃描範圍規則（收哪些類別×環境＋個別覆寫），存完立刻同步成掃描來源。
    回整份 list_scope，前端拿去刷新（規則、逐段在/不在＋原因、in_sync）。"""
    import scan_scope

    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    scan_scope.set_policy(conn, patch)
    return scan_scope.list_scope(conn)


@router.post("/api/scan/scope/apply")
def scan_scope_apply(
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把目前規則重新算一遍、同步成掃描來源（規則沒改、只想讓實際來源對齊規則時用）。"""
    import scan_scope

    scan_scope.apply_policy(conn)
    return scan_scope.list_scope(conn)


# ============ 納入管理（把掃到的主機登記成資產）============
@router.get("/api/cmdb/pull")
def cmdb_pull_endpoint(
    group: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """從 CMDB Gateway 拉資料：回筆數、看到的欄位名（用來對應到我們的欄位）、樣本。
    連不到/認證失敗回 502，錯誤原因如實帶出（不假裝成功）。"""
    try:
        items = cmdb_gateway.fetch_group(conn, group)
    except ConnectionError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "group": group,
        "count": len(items),
        "fields": cmdb_gateway.seen_fields(items),
        "sample": items[:20],
    }


@router.get("/api/scan/unregistered")
def unregistered_endpoint(
    session: sqlite3.Row = Depends(require_auth), conn: sqlite3.Connection = Depends(get_db)
):
    """最新一次掃到、但 hardware(CIA) 未登記的主機——納入管理的候選清單。"""
    row = conn.execute("SELECT MAX(scan_time) AS t FROM scan_history WHERE scan_ok = 1").fetchone()
    t = row["t"] if row else None
    if not t:
        return []
    scanned = conn.execute(
        "SELECT ip, hostname, device_model, is_vm, segment, "
        "mac, mac_vendor, open_ports, ttl, os_guess "
        "FROM scan_history WHERE scan_time = ? AND scan_ok = 1",
        (t,),
    ).fetchall()
    out = []
    for r in scanned:
        if not r["ip"] and not r["hostname"]:
            continue
        matched = conn.execute(
            "SELECT 1 FROM hardware WHERE (ip IS NOT NULL AND ip = ?) OR (hostname IS NOT NULL AND hostname = ?)",
            (r["ip"], r["hostname"]),
        ).fetchone()
        if matched is None:
            out.append(dict(r))
    return out


@router.get("/api/scan/results")
def scan_results_endpoint(
    registered: str | None = None,
    session: sqlite3.Row = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """最新一次掃描「掃得到（存活）」的主機，每筆標註是否已在 CIA 登記。

    給儀表板的「本次掃描 / 本次掃到存活」磚塊下鑽用。/api/scan/unregistered 只回未登記的
    那一半（納入管理專用），這支回全部並附 registered 旗標，才能對得上磚塊上的總數。

    registered：不帶=全部；yes=已登記；no=未登記（等同 unregistered 那份）。
    """
    if registered is not None and registered not in ("yes", "no"):
        raise HTTPException(400, "registered 只接受 yes 或 no")

    scan_time = _latest_scan_time(conn)
    scanned = _scanned_alive_rows(conn, scan_time)
    if not scanned:
        return {"scan_time": scan_time, "items": []}

    ica_rows = conn.execute("SELECT ip, hostname FROM hardware").fetchall()
    ica_ips = {r["ip"] for r in ica_rows if r["ip"]}
    ica_hostnames = {r["hostname"] for r in ica_rows if r["hostname"]}

    items = []
    for r in scanned:
        if not r["ip"] and not r["hostname"]:
            continue
        is_registered = _row_in_keys(r, ica_ips, ica_hostnames)
        if registered == "yes" and not is_registered:
            continue
        if registered == "no" and is_registered:
            continue
        item = dict(r)
        item["registered"] = is_registered
        # 已登記的話一併帶出資產序號，前端才能直接連到那台的詳細頁
        if is_registered:
            hw = conn.execute(
                "SELECT asset_serial FROM hardware WHERE (ip IS NOT NULL AND ip = ?) "
                "OR (hostname IS NOT NULL AND hostname = ?) LIMIT 1",
                (r["ip"], r["hostname"]),
            ).fetchone()
            item["asset_serial"] = hw["asset_serial"] if hw else None
        items.append(item)

    return {"scan_time": scan_time, "items": items}


__all__ = [
    'collect_dispatch_run',
    'collect_dispatch_gap_get',
    'collect_dispatch_gap_put',
    'collect_dispatch_latest',
    'collect_agent_package',
    'pipeline_endpoint',
    'number_proof_endpoint',
    'manage_state_endpoint',
    'manage_state_collect',
    'manage_state_refresh',
    'list_merge_reviews',
    'merge_reviews_auto_matchable',
    'merge_reviews_batch_merge',
    'vcenter_autoimport_status',
    'vcenter_autoimport_save',
    'vcenter_autoimport_run',
    'list_connections_endpoint',
    'suggest_segments',
    'batch_create_segments',
    'create_connection_endpoint',
    'update_connection_endpoint',
    'set_connection_enabled_endpoint',
    'set_connection_scan_time_endpoint',
    'delete_connection_endpoint',
    'test_connection_endpoint',
    'scan_run_endpoint',
    'scan_status_endpoint',
    'scan_schedule_get_endpoint',
    'scan_schedule_put_endpoint',
    'scan_scope_get',
    'scan_scope_put',
    'scan_scope_policy_put',
    'scan_scope_apply',
    'cmdb_pull_endpoint',
    'unregistered_endpoint',
    'scan_results_endpoint',
]
