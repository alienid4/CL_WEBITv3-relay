"""納管成功後自動收一輪（2026-09-16 使用者：「以後納管後 要自動跑一次，收集全部 是手動狀態」）。

## 為什麼要有

使用者納管完一台、也按了「收集規格」，卻看到「軟體 0 服務 0 帳號 0」，
問「為什麼軟體跟帳號都沒有收集到」。原因是：納管只建帳號佈金鑰、收集規格只收硬體，
軟體／服務／帳號是三個各自獨立的收集，沒有人去觸發。

人剛把一台機器納管起來，他要的就是「這台的資料現在都進來了」。
所以納管成功後自動收一輪；「收集全部」按鈕保留給之後想手動重收的時候用。

## 為什麼在背景跑

納管端點本身已經要花數十秒（在目標機建帳號、佈金鑰）。再同步收四樣會讓它變成兩三分鐘，
瀏覽器多半先逾時——使用者會以為納管失敗，然後再按一次。

所以開一條背景執行緒，納管端點立刻回。收集結果寫進 `collect_log`（kind=`post_onboard`），
畫面上看得到成敗與原因。

## 失敗不影響納管

某一樣收不到（例如沒 root 收不到帳號）不影響其他樣，也**不會**把納管標成失敗——
納管成功是已經發生的事實。每一樣的結果分開記，人才知道是哪一樣沒收到。
"""
from __future__ import annotations

import logging
import threading

#: 依序收這幾樣。順序有意義：規格最快也最常成功，先讓畫面有東西；
#: 帳號最容易因為權限不足收不到，放在四樣基本收集的最後才不會擋住前面。
#: 2026-10-18 使用者：「納管時這些動作預設都要跑一次」——再加 OS 重建資料（Q78）與
#: 組態檢核（Q84：Linux 新腳本、AIX 既有）。這兩樣最久，放最後。沒有開關（先前已定案）。
#: 2026-10-20 加「排程」：Windows 走 WinRM 收；Linux 讀 OS 重建資料（不另收，這一樣略過）。
log = logging.getLogger("webit3.post_onboard")

JOBS = ("spec", "service", "software", "account", "schedule", "rebuild", "config_audit")

JOB_LABEL = {"spec": "硬體規格", "service": "服務", "software": "軟體", "account": "帳號",
             "schedule": "排程", "rebuild": "OS 重建資料", "config_audit": "組態檢核"}

#: 「跳過」不是失敗：平台本來就不做這一樣（例如 AIX 的 OS 重建走單獨一批、VMware 設備一律不收）。
#: 平台判斷一律先看實測 OS，沒有實測才照 CIA 登記（訊息會講「依 CIA 登記判斷（尚未實測）」）。
#: 回傳時 ok=None，畫面顯示「略過＋原因」，整體成敗不受影響。
SKIPPED = None

#: 同時在跑的「納管後收集」上限。單台納管各開一條背景執行緒，一次納管很多台時
#: 不可以同時對幾十台跑腳本（組態檢核、OS 重建各要幾十秒）——超過就排隊。
#: 批次與排程本來就是同一條背景執行緒逐台同步跑，不經過這裡。
MAX_CONCURRENT = 2
_slots = threading.BoundedSemaphore(MAX_CONCURRENT)

_lock = threading.Lock()
#: 同一台不要同時跑兩輪（連按兩次納管、或納管＋手動收集撞在一起）
_running: set[str] = set()
#: 在跑的期間又納管（重新納管）了一次的台：跑完這輪**再補一輪**。
#: 2026-10-18 實際踩到：上一輪還在跑（組態檢核、OS 重建各要數十秒）時使用者重新納管，
#: 原本 start() 直接回 False、什麼都沒記——重新納管後的新權限（輔助程式、sudoers）
#: 從來沒被收過，畫面上 OS 重建／組態檢核就一直停在舊的（或空的）。最多補一輪，不會無限迴圈。
_rerun: set[str] = set()
#: 每台現在做到哪（ACCEPT-E2E1，2026-10-18）：{"state": "queued"|"running", "job", "label", "step", "since"}。
#: 資產頁 OS 重建／組態檢核分頁空的時候，要講得出「排隊中／正在收第幾樣」，不是一句「尚未收集」
#: ——一次重新納管很多台時，同時只跑 MAX_CONCURRENT 台，後面的會排隊好幾分鐘。
_progress: dict[str, dict] = {}
#: 「取消排隊」時正在收的那台（2026-10-20 使用者：「哪 77 台？能取消嗎？」）：
#: 目前這一樣收完就停，剩下的幾樣不收（記成已取消，不是靜默丟掉）。
_cancel_running: set[str] = set()


def running_for(asset_serial: str) -> bool:
    return asset_serial in _running


def progress_for(asset_serial: str) -> dict | None:
    p = _progress.get(asset_serial)
    return dict(p) if p else None


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _platform_skip(conn, job: str, asset_serial: str) -> str | None:
    """這一樣在這台要不要略過。回略過原因；要跑回 None。

    VMware：六樣一律略過（2026-10-18：原本只擋 rebuild／config_audit，規格／服務／軟體／帳號
    照樣拿收集金鑰 SSH 過去——ESXi 天條是只能走 vCenter／vROps，不該有任何 SSH 收集）。

    ★ 天條：VMware 設備（ESXi、vCenter／VCSA）一律不收——判定走共用的 judge_for_asset
    （含 os_class「虛擬化平台」），不另寫關鍵字。OS 重建：Linux、Windows（WinRM）；AIX 走單獨一批。
    """
    import manage_state
    import onboard_eligibility

    row = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
    if row is None:
        return "資產不存在"
    got = onboard_eligibility.judge_for_asset(conn, dict(row))
    if got and got[0] == "esxi":
        return "VMware 設備（ESXi／vCenter）不經 SSH 收集"
    if job not in PLATFORM_JOBS:
        return None                           # 其餘四樣：只有 VMware 一律略過（天條），平台由收集器自己判
    # 平台一律先看實測 OS（2026-10-21 使用者：「組態檢核／略過判斷一律照實測 OS」）——
    # 公司機 068T：CIA 寫 Windows、實測 RHEL，以前照 CIA 判，組態檢核／OS 重建走錯路。
    info = manage_state.collect_platform_explain(conn, row["ip"] or "", row["os"],
                                                 asset_serial=asset_serial)
    platform = info["platform"]
    why = None
    if job == "config_audit" and platform not in ("linux", "aix", "windows"):
        why = f"{platform} 尚無組態檢核腳本（目前支援 Linux、AIX、Windows）"
    elif job == "schedule" and platform != "windows":
        why = ("排程讀 OS 重建資料，不另收" if platform == "linux"
               else f"{_PLATFORM_NAME.get(platform, platform)} 尚未支援排程盤點（目前 Linux、Windows）")
    elif job == "rebuild" and platform not in ("linux", "windows"):
        why = f"{_PLATFORM_NAME.get(platform, platform)} 尚無 OS 重建資料收集（目前支援 Linux、Windows）"
    return _with_basis(why, info) if why else None


#: post_onboard 自己依平台決定要不要跑／走哪條路的那幾樣（訊息後面會補「依什麼判的」）
PLATFORM_JOBS = ("rebuild", "config_audit", "schedule")

_PLATFORM_NAME = {"linux": "Linux", "aix": "AIX", "windows": "Windows"}


def _with_basis(msg: str, info: dict | None) -> str:
    """步驟訊息後面補一句判斷依據：實測跟 CIA 不同／尚未實測照 CIA／作業系統不知道只能猜。沒得講就原樣。"""
    note = (info or {}).get("note")
    return f"{msg}（{note}）" if note else msg


def _platform_info(conn, asset_serial: str) -> dict | None:
    """這台的平台判斷與依據（collect_platform_explain）。查不到資產回 None。"""
    import manage_state

    row = conn.execute("SELECT ip, os FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
    if row is None:
        return None
    # 不實問 uname：這裡只拿來寫訊息，真的要判時 _platform_skip／收集器自己會問
    return manage_state.collect_platform_explain(conn, row["ip"] or "", row["os"],
                                                 asset_serial=asset_serial, probe=lambda _h: None)


def win_admin(conn, asset_serial: str) -> int | None:
    """這台最近一次 WinRM session 是不是本機管理員（1／0；沒資料 None）。決策 WINRM-ADMIN1。"""
    import winrm_collector

    row = conn.execute("SELECT ip FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
    s = winrm_collector.session_for(conn, row["ip"]) if row and row["ip"] else None
    return None if not s or s.get("is_admin") is None else int(s["is_admin"])


def _is_windows(conn, asset_serial: str) -> bool:
    import manage_state

    row = conn.execute("SELECT ip, os FROM hardware WHERE asset_serial = ?",
                       (asset_serial,)).fetchone()
    if row is None:
        return False
    # 不實問 uname（probe 回 None）：這裡只要分出 Windows，Unix 類交給收集器自己判，不多連一次 SSH
    return manage_state.collect_platform_of(conn, row["ip"] or "", row["os"],
                                            asset_serial=asset_serial,
                                            probe=lambda _h: None) == "windows"


def _preauth_reason(conn, asset_serial: str, since: float) -> str | None:
    """這一樣跑的期間，這台的 SSH 有沒有「登入前就被斷線、重試完仍失敗」。有就回那句實話。

    2026-10-21 公司實測：OS 重建的訊息裡有 `kex_exchange_identification: Connection reset`，
    其他幾樣卻寫「沒有收到資料」、組態檢核寫「帳號/金鑰/防火牆」——人會去查錯地方。"""
    import manage_state
    import ssh_mux

    row = conn.execute("SELECT ip FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
    for ip in manage_state.ips_of(row["ip"]) if row and row["ip"] else []:
        why = ssh_mux.recent_preauth_failure(ip, since=since)
        if why:
            return why
    return None


def _collect_one(conn, job: str, asset_serial: str) -> tuple[bool | None, str]:
    """跑一樣。回 (有沒有收到, 說明)；ok=None 代表這台平台本來就不做這一樣（略過）。
    例外在這裡吃掉並轉成說明，不往外丟。"""
    import time as _time

    t0 = _time.monotonic()
    ok, msg = _collect_one_inner(conn, job, asset_serial)
    if ok is False:
        try:
            why = _preauth_reason(conn, asset_serial, t0)
        except Exception:  # noqa: BLE001 - 查不到真因就保留收集器原本的說明（不會更糟）
            why = None
        if why:
            msg = why
    if ok is not SKIPPED and job in PLATFORM_JOBS:
        # 跑了的也講依據（實測跟 CIA 不同、尚未實測照 CIA）；略過的在 _platform_skip 已經補過
        try:
            msg = _with_basis(msg, _platform_info(conn, asset_serial))
        except Exception:  # noqa: BLE001 - 依據只是補充說明，查不到就照收集器原本的訊息
            pass
    return ok, msg


def _collect_one_inner(conn, job: str, asset_serial: str) -> tuple[bool | None, str]:
    try:
        why = _platform_skip(conn, job, asset_serial)
        if why:
            return SKIPPED, why
        if job == "config_audit":
            import config_audit

            try:
                r = config_audit.collect_from_host(conn, asset_serial, by="post_onboard")
            except config_audit.ConfigAuditSkipped as exc:   # 例：Windows 組態檢核在系統設定關掉
                return SKIPPED, str(exc)
            c = r.get("counts") or {}
            return True, (f"收到了（{r.get('total', 0)} 條；不合規 {c.get('Non-Compliant', 0)}、"
                          f"未查到 {c.get('Error', 0)}）")
        if job == "schedule":
            import schedule_inventory

            r = schedule_inventory.collect_windows(conn, only_serial=asset_serial, trigger="post_onboard")
        elif job == "rebuild":
            import os_rebuild_collector

            r = os_rebuild_collector.run_collection(conn, trigger="post_onboard",
                                                    only_serial=asset_serial)
        elif job == "spec" and _is_windows(conn, asset_serial):
            # 先收主機事實（OS／機型／序號／MAC）寫回資產，後面幾樣才判得出這台是 Windows（2026-10-20）；
            # 再收 CPU／記憶體／磁碟／網卡／HBA 明細（host_spec，2026-10-21 win_host_spec）
            import host_spec_collector
            import manage_state

            r = manage_state.collect_facts_into_assets(conn, only_serial=asset_serial)
            facts_ok = bool(r.get("updated"))
            ferr = None if facts_ok else ((r.get("failed") or [{}])[0].get("error") or "WinRM 沒有收到主機事實")
            hs = host_spec_collector.run_collection(conn, trigger="post_onboard", only_serial=asset_serial) or {}
            adm = win_admin(conn, asset_serial)
            adm_txt = "" if adm is None else f"；本機管理員：{'是' if adm else '否'}"
            if hs.get("ok"):
                d = (hs.get("ok_detail") or [{}])[0]
                notes = list(d.get("notes") or [])
                if not facts_ok:
                    notes.insert(0, f"主機事實沒收到：{ferr}")
                summ = d.get("summary") or "CPU／記憶體／網卡"
                if notes:
                    return True, f"部分收到（{summ}；{'；'.join(notes)[:260]}{adm_txt}）"
                return True, f"收到了（{summ}{adm_txt}）"
            herr = ((hs.get("fail_detail") or [{}])[0].get("error")
                    or ((hs.get("skipped") or [{}])[0] or {}).get("reason") or "沒有收到資料")
            if facts_ok:
                return True, f"部分收到（主機事實有；CPU／記憶體明細沒收到：{herr[:200]}{adm_txt}）"
            return False, ferr if ferr == herr else f"{ferr}；規格：{herr[:200]}"
        elif job == "spec":
            import host_spec_collector

            r = host_spec_collector.run_collection(conn, trigger="post_onboard", only_serial=asset_serial)
        elif job == "service":
            import service_inventory

            r = service_inventory.collect_services(conn, only_serial=asset_serial, trigger="post_onboard")
        elif job == "software":
            import software_inventory

            r = software_inventory.collect_software(conn, only_serial=asset_serial, trigger="post_onboard")
        else:
            import account_inventory

            r = account_inventory.collect_accounts(conn, only_serial=asset_serial, trigger="post_onboard")
    except Exception as exc:  # noqa: BLE001 - 每一樣各自失敗，不可以拖垮其他樣
        return False, f"{type(exc).__name__}: {exc}"

    r = r or {}
    ok = bool(r.get("ok") or r.get("hosts") or r.get("collected") or r.get("count"))
    if ok:
        # 部分收到（Windows 非管理員常見：某一項權限不足）要講出少了什麼，不可以只寫「收到了」
        notes = [n for h in (r.get("hosts") if isinstance(r.get("hosts"), list) else [])
                 if isinstance(h, dict) for n in (h.get("notes") or [])]
        # 有任何一部分沒收到 → 「部分收到」（畫面 ⚠），不可以跟全部收到一樣打 ✓（2026-10-20 公司機實測）
        # 寫進去幾筆一起講（公司機「帳號 0」但 ✓：講得出存了幾筆，才分得出是沒存還是畫面沒重抓）
        n = next((r.get(k) for k in ("accounts", "services", "packages")
                  if isinstance(r.get(k), int)), None)
        cnt = f"{n} 筆" if n is not None else ""
        if notes:
            if win_admin(conn, asset_serial) == 0:
                # 不是本機管理員：資產頁上方已有一句總說明，這裡不再散列十幾個「權限不足」
                return True, f"部分收到（{cnt + '；' if cnt else ''}非本機管理員，見上方說明）"
            return True, f"部分收到（{cnt + '；' if cnt else ''}{'；'.join(notes)[:300]}）"
        return True, f"收到了（{cnt}）" if cnt else "收到了"
    if r.get("unsupported"):
        ud = r.get("unsupported_detail")
        why = (ud[0] or {}).get("reason") if isinstance(ud, list) and ud else None
        return False, why or "這台平台暫不支援"
    if isinstance(r.get("skipped"), list) and r["skipped"]:
        # 收集器講得出「為什麼這台不在這一輪」（還沒納管 collect_ok=0／沒有 IP…），照講，
        # 不要退成一句「沒有收到資料」——那句看不出該去重新納管還是補 IP。
        why = (r["skipped"][0] or {}).get("reason") if isinstance(r["skipped"][0], dict) else None
        if why:
            return False, why
    detail = (r.get("fail_detail") or [{}])[0].get("error") if r.get("fail_detail") else None
    return False, detail or "沒有收到資料"


#: 紀錄上的標題：同一套六樣，從哪個入口觸發的（採集紀錄、資產頁看得出是納管後還是手動按的）
SOURCE_TITLE = {"onboard": "納管後自動收集", "manual": "手動收集全部"}


def run_now(conn, asset_serial: str, *, by: str | None = None,
            source: str = "onboard") -> list[dict]:
    """同步收一輪（批次／排程與測試用）。回每一樣的結果。

    source="manual"＝資產頁「收集全部」按鈕（2026-10-18 使用者：所有收集都要一樣）——
    同六樣、同略過規則、同一種紀錄（kind=post_onboard，資產頁同一支狀態 API 讀得到），
    只是標題寫「手動收集全部」、不另存健康基準（那是納管當下才存的）。
    """
    import collect_log

    results = []
    cancelled = False
    for i, job in enumerate(JOBS, 1):
        if asset_serial in _cancel_running:
            cancelled = True
            for j in JOBS[i - 1:]:
                results.append({"job": j, "label": JOB_LABEL[j], "ok": SKIPPED, "skipped": True,
                                "message": "已取消"})
            break
        if asset_serial in _running:
            _progress[asset_serial] = {"state": "running", "job": job, "label": JOB_LABEL[job],
                                       "step": i, "total": len(JOBS), "since": _now(),
                                       # 已收完的幾樣（畫面邊跑邊打 ✓／⚠／✕＋秒數）
                                       # job：畫面據此「這一樣一收完就重抓那個分頁」；message：✕／⚠ 的原因
                                       # （2026-10-21 公司機「✕ 排程 11.1 秒」看不到為什麼）
                                       "done": [{"job": r["job"], "label": r["label"], "ok": r["ok"],
                                                 "warn": r.get("warn"), "secs": r.get("secs"),
                                                 "message": r.get("message")}
                                                for r in results]}
        import time as _time
        _t = _time.monotonic()
        ok, msg = _collect_one(conn, job, asset_serial)
        if ok is False:
            log.warning("post-collect %s %s failed: %s", asset_serial, job, msg)
        results.append({"job": job, "label": JOB_LABEL[job], "ok": ok,
                        "skipped": ok is SKIPPED, "message": msg,
                        # ⚠：有存到資料但某些部分權限不足／失敗（見 _collect_one 的「部分收到」）
                        "warn": ok is True and str(msg).startswith("部分收到"),
                        "secs": round(_time.monotonic() - _t, 1)})
    got = [r["label"] for r in results if r["ok"]]
    missed = [f"{r['label']}（{r['message']}）" for r in results if r["ok"] is False]
    skipped = [f"{r['label']}（{r['message']}）" for r in results if r["ok"] is SKIPPED]
    with _lock:
        _cancel_running.discard(asset_serial)
    collect_log.record(
        conn, "post_onboard", asset_serial, not missed,
        f"{SOURCE_TITLE.get(source, SOURCE_TITLE['onboard'])}"
        + ("（中途取消）" if cancelled else "")
        + f"：收到 {'、'.join(got) or '無'}"
        + (f"；沒收到 {'、'.join(missed)}" if missed else "")
        + (f"；略過 {'、'.join(skipped)}" if skipped else ""),
        actor=by, extra={"results": results})

    # 納管時先存一份健康基準（2026-09-22 使用者：「納管的時候就先一份」）——
    # 事發時的即時健檢馬上有基準可比，不必等週六排程鋪底。失敗不影響上面的收集。
    # 手動「收集全部」不存：基準是「納管當下」的那一份，不該被之後隨手一按覆蓋掉。
    if source != "onboard" or cancelled:
        return results
    try:
        import health_probe
        import health_baseline
        import manage_state

        import onboard_eligibility as oe

        row = conn.execute(
            "SELECT * FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
        ips = manage_state.ips_of(row["ip"]) if row else []
        ip = ips[0] if ips else None
        # 天條（審查 M4）：VMware 設備不做健檢（不經 SSH）——這筆登記本身、或它的任一 IP 對到 VMware 都跳過
        names = oe.esxi_host_names(conn)
        got = oe.judge_for_asset(conn, row, esxi_names=names) if row else None
        if (got and got[0] == "esxi") or (ips and oe.vmware_targets(conn, ips)):
            ip = None
        if ip:
            res = health_probe.check_one(ip, platform=manage_state.collect_platform_of(conn, ip))
            health_baseline.save_snapshot(conn, res, source="onboard")
    except Exception:  # noqa: BLE001 - 基準沒存到不影響納管收集本體
        pass
    return results


def _claim(asset_serial: str) -> bool:
    """搶這台的執行權。已有一輪在跑 → 記下「跑完再補一輪」並回 False。"""
    with _lock:
        if asset_serial in _running:
            _rerun.add(asset_serial)          # 跑完這輪再補一輪（見 _rerun）
            return False
        _running.add(asset_serial)
        _progress[asset_serial] = {"state": "queued", "since": _now()}
        return True


def _run_claimed(conn, asset_serial: str, *, by, source) -> list[dict]:
    """已搶到執行權：跑一輪；期間有別的入口（單台／批次／排程／手動）要求同一台，再補一輪。"""
    res = run_now(conn, asset_serial, by=by, source=source)
    with _lock:
        again = asset_serial in _rerun and not any(r.get("message") == "已取消" for r in res)
        _rerun.discard(asset_serial)
    if again:
        res = run_now(conn, asset_serial, by=by, source=source)
    return res


def _release(asset_serial: str) -> None:
    with _lock:
        _running.discard(asset_serial)
        _rerun.discard(asset_serial)
        _progress.pop(asset_serial, None)


def run_exclusive(conn, asset_serial: str, *, by: str | None = None,
                  source: str = "onboard") -> list[dict] | None:
    """同步版的 start()：批次／排程逐台呼叫這支，跟單台納管、手動「收集全部」共用同一把鎖。

    2026-10-18：批次／排程原本直接 run_now，不看 _running——同一台若剛好也在單台納管的
    背景收集裡，兩輪會同時對同一台跑六樣。現在：那台已在跑 → 不重跑，改成讓那一輪跑完
    **再補一輪**（批次的新權限一樣會被收到），回 None。
    """
    if not _claim(asset_serial):
        return None
    try:
        return _run_claimed(conn, asset_serial, by=by, source=source)
    finally:
        _release(asset_serial)


def start(db_path, asset_serial: str, *, by: str | None = None,
          source: str = "onboard") -> bool:
    """在背景收一輪。已經在跑同一台就不重複啟動、回 False，但會在那輪跑完後**補一輪**。"""
    from db import get_connection

    if not asset_serial:
        return False
    if not _claim(asset_serial):
        return False

    def worker():
        conn = None
        try:
            with _slots:                      # 同時最多 MAX_CONCURRENT 台，其餘排隊
                conn = get_connection(db_path)
                _run_claimed(conn, asset_serial, by=by, source=source)
        except Exception as exc:  # noqa: BLE001 - 背景執行緒不可以把例外丟到沒人接的地方
            # 不可靜默：整輪炸掉（不是某一樣失敗）也要留一筆，畫面才看得到
            try:
                import collect_log

                if conn is None:
                    conn = get_connection(db_path)
                collect_log.record(conn, "post_onboard", asset_serial, False,
                                   f"{SOURCE_TITLE.get(source, SOURCE_TITLE['onboard'])}整輪失敗："
                                   f"{type(exc).__name__}: {exc}", actor=by)
            except Exception:  # noqa: BLE001 - 連紀錄都寫不進去（DB 壞了）就只能放棄
                pass
        finally:
            if conn is not None:
                conn.close()
            _release(asset_serial)

    threading.Thread(target=worker, daemon=True).start()
    return True


# ───────────── 多台排隊（2026-10-20）─────────────
# 兩個入口共用這一條隊伍：
#   1. 2-1「開始收集」第一次把一台收成已納管 → 自動收一輪六樣（使用者規則：納管後一律自動收、沒有開關）
#   2. 2-1「已納管主機」勾選多台 →「收集全部」（手動，整批）
# 為什麼要排隊、不是每台各開一條 start()：一次收集上千位址，可能一口氣冒出幾百台要收，
# 每台一條執行緒會同時對幾十台跑組態檢核／OS 重建——像攻擊、也會拖垮收集器。
# 這裡只有**一條**背景執行緒逐台跑，每台再佔一個 _slots（跟單台納管共用上限 MAX_CONCURRENT），
# 同一台已經在收 → run_exclusive 讓那一輪跑完再補一輪，不並行。
_q_lock = threading.Lock()
_queue: list[tuple[str, str | None, str]] = []      # (asset_serial, by, source)
_queued: set[str] = set()
_queued_at: dict[str, str] = {}
_q_db: list = [None]          # 隊伍用的庫（快照寫到這裡）
#: 隊伍只在記憶體——服務一重啟就沒了。每次變動存一份快照到 app_settings，
#: 啟動時發現快照不是空的＝那些台被重啟清掉、沒收到（見 recover_after_restart）。
SNAPSHOT_KEY = "post_onboard_queue_snapshot"
LOST_KEY = "post_onboard_queue_lost"
_q_state: dict = {"running": False, "total": 0, "done": 0, "ok": 0, "failed": 0, "deferred": 0,
                  "current": None, "source": None, "started_at": None, "finished_at": None,
                  "error": None}


def queue_status() -> dict:
    with _q_lock:
        st = dict(_q_state)
        st["pending"] = len(_queue)
        if st["current"]:
            st["current_progress"] = progress_for(st["current"])
        # 逐台清單（「哪 77 台？」）：正在收的在最前面，其餘照排隊順序
        items = []
        if st["current"]:
            items.append({"asset_serial": st["current"], "state": "running",
                          "source": st.get("source"), "since": (_progress.get(st["current"]) or {}).get("since"),
                          "job_label": (st.get("current_progress") or {}).get("label"),
                          "cancel_requested": st["current"] in _cancel_running})
        for sn, by, source in _queue:
            items.append({"asset_serial": sn, "state": "queued", "source": source, "by": by,
                          "since": _queued_at.get(sn)})
        st["items"] = items
        return st


def _snapshot_serials() -> list[str]:
    """（呼叫端持有 _q_lock）現在還沒收完的台：正在收的＋排隊的。"""
    out = [_q_state["current"]] if _q_state.get("current") else []
    return out + [x[0] for x in _queue]


def _persist(conn=None) -> None:
    """把隊伍快照寫進 app_settings。寫不進去只是少了「重啟後提醒」，不影響收集本身。"""
    import json as _json

    with _q_lock:
        data = _json.dumps(_snapshot_serials())
    close = False
    try:
        if conn is None:
            import os
            from pathlib import Path

            # 沒指定庫＝預設庫：只有正式服務（ASSET_SCHEDULER=1）才寫，測試不碰任何人的預設庫
            if _q_db[0] is None and os.environ.get("ASSET_SCHEDULER") != "1":
                return

            from db import get_connection
            conn = get_connection(Path(_q_db[0]) if _q_db[0] else None)
            close = True
        from db import set_setting
        set_setting(conn, SNAPSHOT_KEY, data)
    except Exception:  # noqa: BLE001 - 快照失敗只少了重啟提醒；不讓隊伍因此停
        pass
    finally:
        if close and conn is not None:
            conn.close()


def cancel(conn, serials=None, *, by: str | None = None) -> dict:
    """取消排隊（全部或指定幾台）。排隊中的直接移除；正在收的那台目前這一樣收完就停。
    每一台都記一筆「已取消」（kind=post_onboard_queue），絕不靜默丟掉。"""
    import collect_log

    want = None if serials is None else set(serials)
    removed: list[str] = []
    running_cut = None
    with _q_lock:
        keep = []
        for item in _queue:
            if want is None or item[0] in want:
                removed.append(item[0])
                _queued.discard(item[0])
                _queued_at.pop(item[0], None)
            else:
                keep.append(item)
        _queue[:] = keep
        _q_state["total"] = max(0, _q_state["total"] - len(removed))
        cur = _q_state.get("current")
        if cur and (want is None or cur in want):
            running_cut = cur
            # 在 _q_lock 裡設旗標：跟 worker「收完這台 → 清旗標 → current=None」同一把鎖，
            # 不會在那台剛收完之後才設上、留到下一輪去
            with _lock:
                _cancel_running.add(running_cut)
    for sn in removed:
        collect_log.record(conn, "post_onboard_queue", sn, False,
                           "排隊已取消：這台這次不收（要收請在已納管主機勾選「收集全部」）", actor=by)
    if running_cut:
        collect_log.record(conn, "post_onboard_queue", running_cut, False,
                           "取消：正在收的這台，目前這一樣收完就停，其餘幾樣不收", actor=by)
    _persist(conn)
    return {"removed": removed, "running_cut": running_cut, **queue_status()}


def recover_after_restart(conn) -> list[str]:
    """服務啟動時呼叫：上一個行程留下的隊伍快照不是空的＝那幾台被重啟清掉、沒收到。
    記一筆紀錄（看得到哪些台），清單留在 LOST_KEY 給畫面「重新排入」，快照清空。"""
    import json as _json

    import collect_log
    from db import get_setting, set_setting

    try:
        lost = _json.loads(get_setting(conn, SNAPSHOT_KEY, "[]") or "[]")
    except (ValueError, TypeError):
        lost = []
    lost = [x for x in dict.fromkeys(lost) if x]
    if lost:
        collect_log.record(conn, "post_onboard_queue", None, False,
                           f"服務重啟：{len(lost)} 台排隊被清掉（沒有收到），可在 2-1 按「重新排入」",
                           extra={"serials": lost[:5000]})
        try:
            prev = _json.loads(get_setting(conn, LOST_KEY, "[]") or "[]")
        except (ValueError, TypeError):
            prev = []
        set_setting(conn, LOST_KEY, _json.dumps(list(dict.fromkeys(prev + lost))))
    set_setting(conn, SNAPSHOT_KEY, "[]")
    return lost


def lost_serials(conn) -> list[str]:
    import json as _json

    from db import get_setting
    try:
        return _json.loads(get_setting(conn, LOST_KEY, "[]") or "[]")
    except (ValueError, TypeError):  # noqa: BLE001 - 設定值壞了＝當作沒有被清掉的清單（重啟紀錄仍在收集紀錄裡）
        return []


def clear_lost(conn) -> None:
    from db import set_setting
    set_setting(conn, LOST_KEY, "[]")


def _queue_pop() -> tuple[str, str | None, str] | None:
    with _q_lock:
        if not _queue:
            _q_state.update(running=False, current=None, finished_at=_now())
            return None
        item = _queue.pop(0)
        _queued.discard(item[0])
        _queued_at.pop(item[0], None)
        _q_state["current"] = item[0]
        return item


def _queue_one(conn, serial: str, by: str | None, source: str) -> str:
    """跑一台，回 ok／failed／deferred。單台出事只記這一台，不影響隊伍裡其他台。"""
    try:
        with _slots:
            res = run_exclusive(conn, serial, by=by, source=source)
        if res is None:
            return "deferred"          # 那台本來就在收：跑完會補一輪
        return "failed" if any(r["ok"] is False for r in res) else "ok"
    except Exception as exc:  # noqa: BLE001 - 不可靜默：整輪炸掉也要留一筆，畫面看得到
        try:
            import collect_log

            collect_log.record(conn, "post_onboard", serial, False,
                               f"{SOURCE_TITLE.get(source, SOURCE_TITLE['onboard'])}整輪失敗："
                               f"{type(exc).__name__}: {exc}", actor=by)
        except Exception:  # noqa: BLE001 - 連紀錄都寫不進去（DB 壞了）就只能放棄這一筆
            pass
        return "failed"


def _queue_worker(db_path, runner=None) -> None:
    from db import get_connection

    drained = False
    conn = None
    try:
        from pathlib import Path

        # get_connection 要 Path（字串會 AttributeError）；None＝預設庫
        conn = get_connection(Path(db_path) if db_path else None)
        while True:
            item = _queue_pop()            # 隊伍空了會在鎖內把 running 設回 False
            if item is None:
                drained = True
                return
            serial, by, source = item
            _persist(conn)
            try:
                outcome = (runner or _queue_one)(conn, serial, by, source)
            finally:
                # 取消旗標只管這一輪：不清掉的話，下次（單台納管、收集全部）收這台會一開始就停
                with _lock:
                    _cancel_running.discard(serial)
            with _q_lock:
                _q_state["done"] += 1
                _q_state[outcome if outcome in ("ok", "failed", "deferred") else "failed"] += 1
                if _q_state["current"] == serial:
                    _q_state["current"] = None
                with _lock:            # 鎖的順序固定 _q_lock → _lock（cancel 也是），不會互卡
                    _cancel_running.discard(serial)
            _persist(conn)
    except Exception as exc:  # noqa: BLE001 - 不可靜默：畫面的進度列會顯示這個錯誤
        with _q_lock:
            _q_state["error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        if conn is not None:
            conn.close()
        if not drained:
            # 異常離開（例如開不了 DB）：剩下的別卡成「永遠排隊中」，清掉並標結束
            with _q_lock:
                _queue.clear()
                _queued.clear()
                _q_state.update(running=False, current=None, finished_at=_now())
            _persist()      # 快照也清掉，否則下次重啟會誤報「排隊被清掉」


def enqueue(db_path, serials, *, by: str | None = None, source: str = "onboard",
            _runner=None, _sync: bool = False) -> dict:
    """把多台排進背景隊伍；已在隊伍裡的不重複排。立刻回（不等收完）。

    回 {"added": n, "already": n, **queue_status()}。_runner／_sync 只給測試用。
    """
    added = already = 0
    start_worker = False
    with _q_lock:
        fresh = False
        for s in dict.fromkeys(x for x in (serials or []) if x):
            if s in _queued or s == _q_state["current"]:
                already += 1
                continue
            if not _q_state["running"] and not fresh:
                # 隊伍閒著、這次真的有東西要排 → 才歸零上一批的進度（沒東西排就保留上一批的結果給畫面看）
                fresh = True
                _q_state.update(total=0, done=0, ok=0, failed=0, deferred=0, current=None,
                                source=source, started_at=_now(), finished_at=None, error=None)
            _queue.append((s, by, source))
            _queued.add(s)
            _queued_at[s] = _now()
            _q_state["total"] += 1
            added += 1
        if added and not _q_state["running"]:
            _q_state["running"] = True
            start_worker = True
    if db_path is not None or _q_db[0] is None:
        _q_db[0] = db_path
    if added:
        _persist()
    if start_worker:
        if _sync:
            _queue_worker(db_path, _runner)
        else:
            threading.Thread(target=_queue_worker, args=(db_path, _runner), daemon=True).start()
    return {"added": added, "already": already, **queue_status()}


def never_ran(conn, serial: str, ip: str | None = None) -> bool:
    """這台從來沒有「納管後收集／收集全部」紀錄（collect_log kind=post_onboard，target 是序號或 IP）。

    2-1「開始收集」自動補收的判準：**只有第一次**——重跑收集不會每次都重收六樣（那要幾分鐘一台）。
    之前收過但有幾樣失敗的，不在這裡重收；要重收用資產頁或「已納管主機」的「收集全部」。
    """
    targets = [t for t in (serial, ip) if t]
    if not targets:
        return False
    try:
        row = conn.execute(
            f"SELECT 1 FROM collect_attempt_log WHERE kind = 'post_onboard' "
            f"AND target IN ({','.join('?' * len(targets))}) LIMIT 1", targets).fetchone()
    except Exception:  # noqa: BLE001 - 紀錄表還不存在（新庫）＝從來沒跑過
        return True
    return row is None
