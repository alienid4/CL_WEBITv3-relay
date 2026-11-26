"""B：排程自動納管。把授權網段內「已登記但未納管」的主機，無需人在場帶到已納管。

與 A（UI 一鍵）共用 onboard_engine 這顆引擎，差別只在「誰觸發、憑證哪來」（引擎 docstring 定案）：
    A：人當下輸入帳密、用完即丟
    B：憑證從加密憑證庫取（credential_store），只碰操作者「明確授權」的網段

## 兩道寫死的安全設計

1. **網段授權閘門**：納管腳本會在目標機建帳號、改 sshd_config——是有副作用的動作。
   讓它「無人在場自動對主機跑」風險很高（打錯機器、碰到不該碰的網段都是事故）。
   所以 B 只碰 `auto_onboard_segment` 裡「明確加入且啟用」的網段前綴，其餘一律不碰。
   整體另有一個總開關 `auto_onboard_enabled`（app_settings，預設 0）——排程要跑得先打開。

2. **只碰「已登記未納管」，不自動把未登記主機建成資產**：未登記＝掃到但還不是資產，
   要不要收它是人的決定（自動把來路不明的 IP 建成資產，正是「假資料混進真實盤點」
   要防的事）。B 只處理已登記卻連不進去的那批——那正是 bootstrap 能修好的集合。

## 憑證不落地仍然成立

B 用的憑證來自加密憑證庫（使用者明確授權、要給排程反覆使用），取出的明文只在記憶體、
用完即丟，稽核只記帳號名/成敗，永不含密碼——跟 A 的三條底線一致（見 onboard_engine）。

## Windows 不走 bootstrap

Windows 收集走 WinRM/CIM，完全不動目標機、不需在目標機建帳號佈金鑰（使用者定案）。
所以 Windows 主機的「自動納管」＝排程用 WinRM 憑證直接收集（由 collect_facts_into_assets
負責），這裡不對它跑 bootstrap，只在明細裡註明。
"""
from __future__ import annotations

from db import get_connection

# ---- 總開關（app_settings）----
ENABLED_KEY = "auto_onboard_enabled"


def is_enabled(conn) -> bool:
    from db import get_setting

    return get_setting(conn, ENABLED_KEY, "0") == "1"


def set_enabled(conn, on: bool) -> None:
    from db import set_setting

    set_setting(conn, ENABLED_KEY, "1" if on else "0")


# ---- 授權網段 CRUD ----
def list_segments(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, prefix, enabled, note, created_at, updated_at "
        "FROM auto_onboard_segment ORDER BY prefix")]


def save_segment(conn, prefix: str, enabled: bool = True, note: str | None = None) -> int:
    """新增／更新一個授權網段（以 prefix 為唯一鍵，重複就更新）。"""
    prefix = (prefix or "").strip()
    if not prefix:
        raise ValueError("網段前綴不可為空")
    cur = conn.execute(
        "INSERT INTO auto_onboard_segment (prefix, enabled, note) VALUES (?,?,?) "
        "ON CONFLICT(prefix) DO UPDATE SET enabled=excluded.enabled, note=excluded.note, "
        "updated_at=datetime('now','localtime')",
        (prefix, 1 if enabled else 0, note),
    )
    conn.commit()
    return cur.lastrowid


def set_segment_enabled(conn, seg_id: int, enabled: bool) -> int:
    cur = conn.execute(
        "UPDATE auto_onboard_segment SET enabled = ?, updated_at = datetime('now','localtime') "
        "WHERE id = ?", (1 if enabled else 0, seg_id))
    conn.commit()
    return cur.rowcount


def delete_segment(conn, seg_id: int) -> int:
    cur = conn.execute("DELETE FROM auto_onboard_segment WHERE id = ?", (seg_id,))
    conn.commit()
    return cur.rowcount


# ---- 授權判定（純函式，好測）----
def authorized_prefixes(conn) -> list[str]:
    """目前「啟用中」的授權網段前綴。停用的不算——排程不會碰。"""
    return [r["prefix"] for r in conn.execute(
        "SELECT prefix FROM auto_onboard_segment WHERE enabled = 1 ORDER BY prefix")]


def matches(prefixes: list[str], ip: str) -> bool:
    """ip 是否落在任一授權前綴內。刻意用單純的前綴比對，與憑證庫 scope 同一套規則。"""
    if not ip:
        return False
    return any(ip.startswith(p.rstrip("*")) for p in prefixes if p)


# ---- 候選探查 ----
def _platform_of(conn, ip: str) -> str:
    """要對這台送哪一份納管腳本。**呼叫正典 `manage_state.collect_platform_for`。**

    ⚠️ 2026-09-22：原本只看開放埠。AIX 只開 22 -> 判成 linux -> 送 Linux 納管腳本
    （`useradd`／`/etc/sudoers.d`）過去，AIX 上這些指令根本不存在。
    而資產表裡其實早就寫著 `AIX 7.2`——**答案一直在，只是這支不看。**
    """
    import manage_state

    return manage_state.collect_platform_for(conn, ip)


def find_candidates(conn) -> list[dict]:
    """授權網段內「已登記但未納管」的主機。這就是排程會去處理的集合。

    來源用 manage_state.summarize 的四態判定（同一份真相），只挑 state=未納管、
    且 ip 落在啟用中授權網段內的。平台從掃描指紋猜，決定用哪種手法。
    """
    import manage_state

    prefixes = authorized_prefixes(conn)
    if not prefixes:
        return []
    import onboard_eligibility

    esxi_names = onboard_eligibility.esxi_host_names(conn)   # 整輪只查一次
    out = []
    seen: set[str] = set()
    for item in manage_state.summarize(conn)["items"]:
        st = item.get("state")
        # IP 欄寫壞了（八進位、IPv6…）的登記，四態判成「沒有 IP」——那種也要留下原因，不能悄悄消失
        if st not in (manage_state.NOT_ONBOARDED, manage_state.NO_IP):
            continue
        # 2.058.15 審查 P2-2：跟手動納管同一套正規化——去空白、「a, b」拆開判斷。
        # 2.058.16 審查 P1-B：**一筆登記＝一個候選**（多個 IP 是同一台）：每個 IP 都要過關卡，任一個被拒＝整筆不納管；
        # 不是 IPv4 的（看得出是授權網段裡的）不安靜丟掉，帶著原因進候選，執行時寫進明細與稽核
        parts = _split_ips(item.get("ip"))
        if not parts:
            continue
        v4s = [onboard_eligibility.onboard_ipv4(p) for p in parts]
        in_scope = [v for v in v4s if v and matches(prefixes, v)]
        bad = [p for p, v in zip(parts, v4s) if v is None]
        if not in_scope and not any(x.rstrip("*") and x.rstrip("*") in p for p in bad for x in prefixes):
            continue
        key = ", ".join(parts)
        if key in seen or (in_scope and in_scope[0] in seen):
            continue
        seen.add(key)
        ips = list(dict.fromkeys(v for v in v4s if v))
        if bad:
            out.append({"ip": in_scope[0] if in_scope else bad[0], "ips": ips, "record_ip": key,
                        "asset_serial": item.get("asset_serial"), "platform": "invalid",
                        "reject_reason": f"{onboard_eligibility.IPV4_ONLY_ERROR}（{bad[0]}）"})
            continue
        if st != manage_state.NOT_ONBOARDED:
            continue
        seen.add(in_scope[0])
        c = _candidate(conn, item, in_scope[0], esxi_names)
        c["ips"] = ips
        if len(parts) > 1:
            c["record_ip"] = key
        out.append(c)
    return out


def _split_ips(raw) -> list[str]:
    """IP 欄拆成一個一個（同 manage_state.ips_of 的分隔符號），去空白；不判斷合不合法（呼叫端判，拒絕要留原因）。"""
    import re as _re

    return [x for x in (p.strip() for p in _re.split(r"[,;／/、\s]+", str(raw or ""))) if x and x != "0.0.0.0"]


def _candidate(conn, item, ip, esxi_names) -> dict:
    """一台候選（標準寫法的 IPv4）：平台、ESXi 判定依據。"""
    # ★ 天條：ESXi 不可納管（2026-10-17）。資產登記／vCenter 已經說是 ESXi 的，
    # 候選階段就標出來，連 SSH 都不去試（引擎那道是連上後的第二道）。
    esxi_why = _esxi_reason(conn, item, ip, esxi_names)
    c = {
        "ip": ip,
        "asset_serial": item.get("asset_serial"),
        "platform": "esxi" if esxi_why else _platform_of(conn, ip),
    }
    if esxi_why:
        # 判定依據一定要帶出去：自由文字（例「ESXi 叢集上的 VM」）可能誤判，不能安靜跳過
        c["esxi_reason"] = esxi_why
    return c


def _esxi_reason(conn, item, ip, esxi_names) -> str | None:
    """這台資產的登記（作業系統／名稱用途／vCenter 名單）是 ESXi 就回判定依據，否則 None。同一支正典判定。"""
    import onboard_eligibility

    row = None
    serial = item.get("asset_serial")
    if serial:
        row = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?",
                           (serial,)).fetchone()
    asset = dict(row) if row else {"ip": ip, "hostname": item.get("hostname")}
    got = onboard_eligibility.judge_for_asset(conn, asset, ip=ip, esxi_names=esxi_names)
    return got[2] if got and got[0] == "esxi" else None


# ---- 核心：跑一輪自動納管 ----
def _default_cred_lookup(conn, cred_key_path):
    """預設憑證解析：從加密憑證庫依網段挑一組 ssh 憑證。回 (name, user, pw)。

    - 找不到適用憑證 → None
    - 找到但解不開（金鑰換過）→ (name, None, None)
    明文只在記憶體、交給引擎後即丟，絕不寫回任何地方。
    """
    import credential_store

    def lookup(ip: str):
        name = credential_store.pick_for_host(conn, ip, kind="ssh")
        if not name:
            return None
        got = credential_store.get_for_use(conn, name, key_path=cred_key_path)
        if got is None:
            return (name, None, None)
        return (name, got[0], got[1])

    return lookup


def _audit(conn, ip, platform, login_user, ok, stage, message, output=""):
    """寫一筆 trigger='auto' 的納管稽核。**永不含密碼**（login_user 只是帳號名）。"""
    conn.execute(
        "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, "
        "triggered_by, ok, stage, message, output) VALUES (?,?,?,?,?,?,?,?,?)",
        (ip, platform, login_user, "auto", "(排程)", 1 if ok else 0, stage, message, output),
    )
    conn.commit()


def run_auto_onboard(conn, collector_ip: str, *, executor=None, pubkey=None,
                     cred_lookup=None, cred_key_path: str | None = None) -> dict:
    """對授權網段內的未納管主機跑一輪 bootstrap（僅 Linux）。回統計＋逐台明細。

    ⚠️ 這裡**不檢查總開關**——總開關（auto_onboard_enabled）只擋「無人在場的排程」，
    由呼叫端（scan_service）判斷；真正的安全閘門是授權網段（沒授權就沒候選）。
    手動「立即執行」時操作者在場，照樣受授權網段約束即可。

    executor / cred_lookup 皆可注入，測試不碰真網路、不碰真憑證。
    密碼只在本函式與引擎之間傳遞、用完即丟——絕不寫回傳值、DB、log。
    """
    import credential_store
    import onboard_engine

    # 走 resolve_collector_ip（畫面設定 → 環境變數 → 自動偵測），**不給寫死的預設值**。
    # 這裡原本是 `os.environ.get("ASSET_COLLECTOR_IP", "<開發機 IP>")`——那正是
    # onboard_engine.resolve_collector_ip 在 2026-08-16 就修掉的寫法，這兩處（本行與
    # scheduled_cycle）當時漏改，開發機位址因此一路被打進交付包（2026-10-06 查出）。
    #
    # 為什麼不是換一個預設值而是改走 resolve：collector_ip 會被寫進**目標主機**
    # authorized_keys 的 `from=` 來源限制。填錯的後果不是報錯，是「納管顯示成功、
    # 金鑰永遠被拒、資料永遠不進來」——所有紅綠燈都綠的那種安靜故障。
    # validate_collector_ip() 就是為了擋這個而存在，所以這裡解完一定要過它。
    collector_ip = collector_ip or onboard_engine.resolve_collector_ip(conn)
    onboard_engine.validate_collector_ip(collector_ip)
    if cred_key_path is None:
        cred_key_path = credential_store.KEY_PATH_DEFAULT
    lookup = cred_lookup or _default_cred_lookup(conn, cred_key_path)

    candidates = find_candidates(conn)
    onboarded = failed = skipped = 0
    details = []
    import onboard_eligibility

    # DNS 健康檢查（2.058.17 審查 P1-2）：無人在場，解析器壞了就整輪不納管（每台留稽核）；
    # 通過＝「沒有 PTR」可以當真（strict）；沒有可測的名稱＝連「沒有 PTR」也不登入（all）
    ptr_mode = "strict"
    if candidates:
        try:
            health, probe_name = onboard_eligibility.resolver_health(conn)
        except Exception as exc:  # noqa: BLE001 - 健康檢查本身出錯＝當作異常，不納管
            health, probe_name = "fail", f"({type(exc).__name__})"
        if health == "fail":
            msg = f"{onboard_eligibility.DNS_UNHEALTHY_ERROR}（解析不到 {probe_name}）"
            for c in candidates:
                skipped += 1
                _audit(conn, c["ip"], c["platform"], None, False, "refused", msg)
                details.append({**c, "action": "skipped", "message": msg})
            candidates_to_run = []
        else:
            ptr_mode = "strict" if health == "ok" else "all"
            candidates_to_run = candidates
    else:
        candidates_to_run = candidates

    for c in candidates_to_run:
        if not __import__("quiet_window").checkpoint(conn, "scan_post_auto_onboard"):
            break      # AUD-02：進窗後不再對下一台納管
        ip, platform = c["ip"], c["platform"]
        if platform == "invalid":
            skipped += 1
            msg = f"不自動納管：{c['reject_reason']}（資產登記的 IP 欄：{c.get('record_ip') or ip}）"
            for one in dict.fromkeys([ip, *(c.get("ips") or [])]):
                _audit(conn, one, platform, None, False, "refused", msg)
            details.append({**c, "action": "skipped", "message": msg})
            continue
        if platform == "esxi":
            skipped += 1
            msg = f"{onboard_engine.ESXI_REFUSED_MESSAGE}｜{c.get('esxi_reason') or ''}"
            # 留稽核：判錯（例如自由文字誤判）時使用者才看得到是哪一台、為什麼被跳過
            _audit(conn, ip, platform, None, False, onboard_engine.ESXI_REFUSED_STAGE, msg)
            details.append({**c, "action": "skipped", "message": msg})
            continue
        if platform == "windows":
            skipped += 1
            details.append({**c, "action": "skipped",
                            "message": "Windows 不走自動納管（不建帳號、不需 bootstrap）："
                                       "到 2-1 WinRM 頁簽勾選後按「納管」才會登入收集"
                                       "（需已設定 WinRM 憑證且目標 5985 可連）"})
            continue
        if platform != "linux":
            skipped += 1
            details.append({**c, "action": "skipped",
                            "message": f"未支援自動納管的平台：{platform}"})
            continue
        last = conn.execute(
            "SELECT stage, message FROM onboard_audit WHERE target_ip = ? "
            "ORDER BY id DESC LIMIT 1", (ip,)).fetchone()
        if last and last[0] == onboard_engine.PLATFORM_REFUSED_STAGE:
            # 平台白名單（2026-10-19）上次已拒絕：排程不再每輪去登入試（白名單改了要納管就手動按一次）
            skipped += 1
            details.append({**c, "action": "skipped", "message": last[1]})
            continue

        # ★ 天條（2.058.15 審查 P1）：登入前過所有登入入口同一道關卡（onboard_eligibility.login_target_refusal）——
        # 只看資產登記不夠：只有 IP 的 AUTO 登記＋vCenter 名單只有 FQDN 時，要用 PTR 反解出的名稱才對得到 ESXi。
        # 判不出來（例外）也當作拒絕；拒絕的留稽核、寫進明細，不安靜跳過。
        import onboard_eligibility

        # 2.058.16：同一筆登記的**每一個** IP 都過（P1-B，任一個被拒＝整筆不納管）；無人在場，PTR 反查失敗也不登入（P2-2）；
        # 關卡本身丟例外也只擋這一台、留稽核，不中斷整輪（P2-1）。
        why = None
        rec_ips = c.get("ips") or [ip]
        for one in rec_ips:
            try:
                _ok, w = onboard_eligibility.login_targets(conn, one, ptr_strict=ptr_mode)
            except Exception as exc:  # noqa: BLE001 - 判不出來＝不登入，原因寫進明細與稽核
                w = f"無法確認能不能登入（{type(exc).__name__}），不登入"
            if w:
                why = w if len(rec_ips) == 1 else f"同一筆登記的 {one}：{w}——整筆（{c.get('record_ip')}）不納管"
                break
        if why:
            skipped += 1
            for one in dict.fromkeys([ip, *rec_ips]):
                _audit(conn, one, platform, None, False, onboard_engine.ESXI_REFUSED_STAGE
                       if "VMware" in why else "refused", why)
            details.append({**c, "action": "skipped", "message": why})
            continue
        cred = lookup(ip)
        if cred is None:
            failed += 1
            msg = "找不到適用的 SSH 憑證（請在收集憑證新增 kind=ssh 並設定適用網段）"
            _audit(conn, ip, platform, None, False, "connect", msg)
            details.append({**c, "action": "failed", "message": msg})
            continue
        cred_name, username, password = cred
        if username is None:
            failed += 1
            msg = f"SSH 憑證「{cred_name}」解不開（加密金鑰可能已更換），請重設"
            _audit(conn, ip, platform, None, False, "connect", msg)
            details.append({**c, "action": "failed", "message": msg})
            continue

        # 憑證取出後只在這裡與引擎之間存在，用完即丟
        result = onboard_engine.onboard(
            host=ip, platform=platform, username=username, password=password,
            collector_ip=collector_ip, pubkey=pubkey, executor=executor,
            # ONBOARD-FLOW1：跟下載的 webit3_onboard.sh 同一份（測試注入 executor 時讓引擎自己組）
            script=(_bundle(conn) if executor is None and platform in ("linux", "aix") else None),
        )
        password = None  # 明確結束密碼生命週期
        _audit(conn, ip, platform, username, result.ok, result.stage,
               result.message, result.output)
        if result.ok:
            onboarded += 1
            try:
                import root_key

                root_key.record_after_onboard(conn, ip, platform,
                                             bool(getattr(result, "root_key", False)))
            except Exception as exc:  # noqa: BLE001 - 不影響「已納管」，但不可靜默
                _audit(conn, ip, platform, username, False, "root_key",
                       f"root 收集金鑰標記失敗（照收集帳號繼續收）：{exc}")
            # 收集帳號換新：收集器用新帳號實際連一次，連得上才記（連不上要留紀錄）
            try:
                import collect_account_migration as cam

                st = cam.settle_after_onboard(conn, ip, platform)
                if st.get("ok") is False:
                    _audit(conn, ip, platform, username, False, "account", st["error"])
            except Exception as exc:  # noqa: BLE001 - 不可靜默
                _audit(conn, ip, platform, username, False, "account",
                       f"收集帳號換新那步失敗：{exc}")
            details.append({**c, "action": "onboarded", "message": result.message})
        else:
            failed += 1
            details.append({**c, "action": "failed", "message": result.message})

    return {
        "enabled": is_enabled(conn),
        "authorized_prefixes": authorized_prefixes(conn),
        "candidates": len(candidates),
        "onboarded": onboarded,
        "failed": failed,
        "skipped": skipped,
        "details": details,
    }


def _bundle(conn):
    import onboard_bundle

    return onboard_bundle.bundle_for(conn)


@__import__("quiet_window").guarded("scan_post_auto_onboard")
def scheduled_cycle(collector_ip: str | None = None, conn=None,
                    refresh_runner=None, collect_runner=None, **kw) -> dict:
    """完整一輪：試連 → 自動納管 Linux 未納管 → 再試連 → 收 facts。

    掃描剛跑完時呼叫最有意義（有新鮮的存活與指紋資料）。這是排程／手動立即執行／
    命令列共用的入口。refresh_runner/collect_runner 可注入給測試，正式環境走真網路。
    """
    import manage_state

    own = conn is None
    conn = conn or get_connection()
    try:
        # collector_ip 刻意**不在這裡解析**：下面的 run_auto_onboard() 已經會走
        # resolve_collector_ip + validate_collector_ip。原本這裡自己又解一次（還帶著
        # 寫死的開發機預設值），等於同一件事有兩個來源——兩處必然漂走，而且漂了不會有人
        # 發現。留一個解析點，None 直接往下傳即可。
        manage_state.refresh_collect_status(conn, runner=refresh_runner)   # 先知道誰未納管
        result = run_auto_onboard(conn, collector_ip, **kw)
        manage_state.refresh_collect_status(conn, runner=refresh_runner)   # 納管後重試連
        result["collected"] = manage_state.collect_facts_into_assets(conn, runner=collect_runner)
        result["post_onboard"] = run_post_onboard(conn, result.get("details") or [])
        return result
    finally:
        if own:
            conn.close()


def run_post_onboard(conn, details: list[dict]) -> list[dict]:
    """排程納管成功的每一台，也跑一輪「納管後自動收集」（2026-10-18 使用者：
    「納管時這些動作預設都要跑一次」——單台、批次、排程三條路一致）。

    **逐台同步**：排程本來就在背景執行緒，一台收完才下一台，不會同時打幾百台。
    單台失敗寫進 collect_log（kind=post_onboard），不中斷下一台。
    """
    import collect_log
    import manage_state
    import post_onboard

    out = []
    for d in details:
        if d.get("action") != "onboarded":
            continue
        ip = d.get("ip")
        serial = d.get("asset_serial")
        if not serial and ip:
            serial = manage_state.serial_for_ip(conn, ip)   # 同 IP 多筆：現役／已納管那筆
        if not serial:
            out.append({"ip": ip, "ok": False, "message": "資產清冊沒有這台，未收集"})
            continue
        try:
            res = post_onboard.run_exclusive(conn, serial, by="auto_onboard")
            out.append({"ip": ip, "asset_serial": serial, "ok": True, "results": res or [],
                        **({} if res is not None else
                           {"message": "這台已有一輪在收，跑完會再補一輪"})})
        except Exception as exc:  # noqa: BLE001 - 一台失敗不擋下一台，但要留紀錄
            msg = f"納管後自動收集整輪失敗：{type(exc).__name__}: {exc}"
            collect_log.record(conn, "post_onboard", serial, False, msg, actor="auto_onboard")
            out.append({"ip": ip, "asset_serial": serial, "ok": False, "message": msg})
    return out


def recent_auto_audit(conn, limit: int = 50) -> list[dict]:
    """最近的排程納管稽核（trigger='auto'）。不含任何憑證。"""
    return [dict(r) for r in conn.execute(
        "SELECT target_ip, platform, login_user, ok, stage, message, created_at "
        "FROM onboard_audit WHERE trigger = 'auto' ORDER BY id DESC LIMIT ?", (limit,))]


# ---- 診斷外掛：只給設定與稽核，永不給密碼 ----
try:
    import diagnostics

    @diagnostics.register("auto_onboard")
    def _diag(conn) -> dict:
        try:
            segs = list_segments(conn)
            recent = recent_auto_audit(conn, 30)
        except Exception:  # noqa: BLE001
            segs, recent = [], []
        return {"enabled": is_enabled(conn), "segments": segs, "recent_auto_onboards": recent}
except ImportError:
    pass


if __name__ == "__main__":
    # 排程觸發（部署環境）：systemd timer／工作排程器每天呼叫一次 `python auto_onboard.py`。
    # 站內排程器（ASSET_SCHEDULER=1）已在掃描後自動接這一輪，這條命令列是給沒跑常駐服務、
    # 或想手動補一輪的環境用。總開關關閉時不動作（跟排程一致）。
    import sys

    import quiet_window as _qw
    from db import init_db

    if not _qw.cli_gate("scan_post_auto_onboard", "--confirm-in-window" in sys.argv, "自動納管"):    # AUD-02
        raise SystemExit(0)
    init_db()
    connection = get_connection()
    try:
        if not is_enabled(connection):
            print("自動納管總開關未啟用（auto_onboard_enabled=0），不動作。")
            raise SystemExit(0)
        summary = scheduled_cycle(conn=connection)
        print(f"授權網段 {summary['authorized_prefixes']}｜候選 {summary['candidates']}｜"
              f"納管成功 {summary['onboarded']}｜失敗 {summary['failed']}｜跳過 {summary['skipped']}")
        for d in summary["details"]:
            print(f"  [{d['action']}] {d['ip']} ({d['platform']}) — {d['message']}")
    finally:
        connection.close()
