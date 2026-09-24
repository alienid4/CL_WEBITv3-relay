"""服務盤點的寫入層：把 service_collector 收到的東西落進 host_service。

跟 manage_state.collect_facts_into_assets 是同一個模式（對已納管的機器逐台收），
差別在這裡多一件事：**比對上一次的結果，標記消失的服務**。

## 為什麼「消失」要標而不是刪

一台機器上週在聽 3306、今天不聽了，可能是：服務掛了、被人停掉、搬到別台。
這三種都是要有人知道的事。直接 DELETE 的話，畫面上只會安靜地少一列，
沒有任何人會發現——跟 M1 的「異常消失」是同一個道理。

## 收集帳號拿不到行程名是常態

webit3scan 是唯讀非 root，多數情況只收得到「埠」不含「行程」。
這裡不會用埠號猜測去填 process 欄位，寧可留 NULL 讓畫面顯示「未知（需 root）」。
"""
from __future__ import annotations

from db import _now_local


def _resolve_targets(conn, only_serial: str | None = None) -> list[dict]:
    """要收哪些主機：已納管（collect_ok=1）且有 IP 的資產。

    未納管的機器連不進去，收也是白收——那類主機的服務只能靠 M1 的外部埠掃描
    （scan_history.open_ports），不在這支的職責內。
    """
    import manage_state

    sql, params = manage_state.collect_targets_sql(
        conn, only_serial,
        "SELECT asset_serial, ip FROM hardware "
        "WHERE ip IS NOT NULL AND ip != '' AND collect_ok = 1")
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _platform_for(conn, ip: str, asset_serial: str | None = None) -> str:
    """平台判定**呼叫正典** `manage_state.collect_platform_for`，這裡不自己判。

    ⚠️ 2026-09-22：原本這支只看掃描指紋（開放埠）。AIX 只開 22，於是一律判成
    linux，服務盤點就拿 `ss -tlnp` 去問 AIX——AIX 沒有 `ss`，退回的
    `netstat -tlnp` 那些旗標 AIX 也不認，結果是**每台 AIX 服務數都是 0**。
    軟體盤點借用這支，所以一起壞掉。
    """
    import manage_state

    return manage_state.collect_platform_for(conn, ip, asset_serial=asset_serial)


def upsert_services(conn, ip: str, asset_serial: str | None, services: list[dict],
                    source: str) -> dict:
    """把一台主機這次收到的服務寫進 DB，並標記這次沒看到的（gone_at）。

    回傳 {added, updated, gone}——三個數字都要，因為「這次多了什麼、少了什麼」
    才是使用者真正關心的，總數本身沒什麼資訊量。
    """
    now = _now_local()
    seen_keys: set[tuple[str, int, str]] = set()
    added = updated = 0

    for s in services:
        bind = s.get("bind") or ""
        proto = s.get("proto") or "tcp"
        port = s["port"]
        seen_keys.add((proto, port, bind))
        existing = conn.execute(
            "SELECT id FROM host_service WHERE ip = ? AND proto = ? AND port = ? "
            "AND bind_addr = ?", (ip, proto, port, bind)
        ).fetchone()
        if existing:
            # 回來了的服務要把 gone_at 清掉，否則畫面會一直顯示「已消失」
            conn.execute(
                "UPDATE host_service SET asset_serial = ?, exposure = ?, process = ?, "
                "service_guess = ?, guess_source = ?, is_infra = ?, source = ?, "
                "last_seen = ?, gone_at = NULL WHERE id = ?",
                (asset_serial, s.get("exposure"), s.get("process"), s.get("service_guess"),
                 s.get("guess_source"), s.get("is_infra", 0), source, now, existing["id"]),
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO host_service (ip, asset_serial, proto, port, bind_addr, exposure, "
                "process, service_guess, guess_source, is_infra, source, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ip, asset_serial, proto, port, bind, s.get("exposure"), s.get("process"),
                 s.get("service_guess"), s.get("guess_source"), s.get("is_infra", 0),
                 source, now, now),
            )
            added += 1

    # 這次沒收到、且先前不是已標消失的 → 標記消失（不刪）
    gone = 0
    for row in conn.execute(
        "SELECT id, proto, port, bind_addr FROM host_service WHERE ip = ? AND gone_at IS NULL",
        (ip,)
    ).fetchall():
        key = (row["proto"], row["port"], row["bind_addr"] or "")
        if key not in seen_keys:
            conn.execute("UPDATE host_service SET gone_at = ? WHERE id = ?", (now, row["id"]))
            gone += 1

    conn.commit()
    return {"added": added, "updated": updated, "gone": gone}


def _collect_windows_services(conn, ip: str, asset_serial: str | None,
                              failed: list) -> dict | None:
    """Windows 服務收集：WinRM + Get-NetTCPConnection。憑證從庫裡取、用完即丟、留稽核。

    跟 manage_state._collect_windows 同一套流程（同憑證、同稽核），
    差別只在跑的是「誰在聽埠」而不是「這台機器是什麼」。
    失敗回 None 並把**能行動的原因**放進 failed——「沒有憑證」跟「WinRM 沒開」
    要做的事完全不同，混成一句「連線失敗」等於沒講。
    """
    import credential_store
    import service_collector
    import winrm_collector

    cred_name = credential_store.pick_for_host(conn, ip, kind="winrm")
    if not cred_name:
        failed.append({"asset_serial": asset_serial, "ip": ip,
                       "error": "沒有可用的收集憑證——請先在系統設定新增 WinRM 服務帳號"})
        return None
    got = credential_store.get_for_use(conn, cred_name)
    if got is None:
        failed.append({"asset_serial": asset_serial, "ip": ip,
                       "error": f"憑證「{cred_name}」解不開（加密金鑰可能已更換），請重新設定"})
        return None
    username, password = got
    try:
        raw = winrm_collector.collect_services(ip, username, password)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)[:200]
        if "actively refused" in msg or "timed out" in msg or "Max retries" in msg:
            msg = f"{msg}｜{winrm_collector.ENABLE_HINT}"
        credential_store.audit_use(conn, cred_name, ip, False, msg)
        failed.append({"asset_serial": asset_serial, "ip": ip, "error": msg})
        return None
    credential_store.audit_use(conn, cred_name, ip, True, "服務收集成功")

    # 解析共用 Linux 那支：SERVICES_PS 的輸出格式刻意跟 ss 對齊，
    # 兩邊共用才不會 Linux 修了 bug、Windows 這條還留著舊行為。
    services = service_collector.parse_listen(raw)
    for s in services:
        s["exposure"] = service_collector.exposure_of(s["bind"])
        s["service_guess"] = service_collector.guess_service(s["port"], s["process"])
        s["guess_source"] = "process" if s["process"] else (
            "port" if s["service_guess"] else None)
        s["is_infra"] = 1 if s["port"] in service_collector.INFRA_PORTS else 0
    return {"services": services, "units": [],
            "process_visible": any(s["process"] for s in services)}




def _ssh_verdict(conn, asset_serial: str) -> str:
    """SSH 那一跳的結論（離開碼與 stderr 的事實，不是推論）。"""
    import manage_state as _ms

    try:
        row = conn.execute("SELECT * FROM collect_ssh_trace WHERE asset_serial = ?",
                           (asset_serial,)).fetchone()
    except Exception:  # noqa: BLE001
        row = None
    return _ms.trace_verdict(row).get("text", "")



def _acct(conn, asset_serial, platform: str) -> str:
    """這一台的收集帳號。**每台一個答案**（`hardware.collect_account`，NULL 就走全域）。

    2026-09-24 收集帳號統一成 webit3sc 的遷移需要它：全域一次翻＝ 09-23 那個
    「好好的機器變成 0/9」的機隊放大版。驗過的那一台才寫新帳號，其餘不受影響。
    """
    import collect_account_migration as cam

    return cam.account_for_host(conn, asset_serial, platform)


def _save_trace(conn, run, serial: str, ip: str) -> None:
    """把這一跳的 SSH 事實落庫（離開碼／stderr／指令原文）。

    ⚠️ **失敗的時候也要寫**——那才是最需要看診斷的時候。
    2026-09-22 真機第一次驗證：AIX 四類全空，但我們證明不了是「SSH 沒連上」
    還是「連上了但指令跑不起來」，因為證據在 stderr 裡而它被丟掉了。

    注入 runner 的測試沒有 save()，所以先問過再叫。
    """
    if not hasattr(run, "save"):
        return
    try:
        run.save(conn, serial, ip)
    except Exception:  # noqa: BLE001 - 診斷寫不進去不可以反過來害收集失敗
        pass


def collect_services(conn, key_path: str | None = None, runner=None,
                     only_serial: str | None = None, trigger: str = "manual") -> dict:
    """對已納管主機逐台收服務，寫進 host_service，並留一筆執行紀錄。

    runner 可注入（測試不碰網路）；不給就沿用 manage_state 的 SSH/本機 runner——
    收集器跑在 221 上，收 221 自己不必繞 SSH，那段邏輯已經寫在 manage_state。
    """
    import manage_state
    import service_collector

    cur = conn.execute(
        "INSERT INTO service_collect_runs (trigger, status, started_at) VALUES (?, 'running', ?)",
        (trigger, _now_local()),
    )
    conn.commit()
    run_id = cur.lastrowid

    # ⚠️ 2026-09-23：收集帳號**要按平台決定，而且要在迴圈裡決定**。
    # AIX 的收集帳號是 8 字元的 webit3sc（max_logname 限制），不是 webit3scan。
    # 原本這裡在迴圈外呼叫 get_collect_account(conn)（沒帶平台），於是：
    #   · 一律用 webit3scan -> 那個帳號在 AIX 上不存在 -> SSH 認證失敗
    #   · 每一條指令都回空字串 -> 畫面顯示「收不到」，看起來像指令有問題
    # 真機實測（test1T，公司機）四樣全空就是這個原因。
    # 放在迴圈外還有第二個問題：一批機器混著 Linux 與 AIX 時只會有一個答案。
    targets = _resolve_targets(conn, only_serial)
    total_services = 0
    failed: list[dict] = []
    skipped: list[dict] = []
    # 指定單台卻一台都沒進迴圈 -> 講得出為什麼（2026-09-23）。
    # 不講的話前端只能填「可能不在可收集清單、或被排除」，那句話等於沒講。
    if only_serial and not targets:
        import manage_state as _ms
        _why = _ms.why_not_collectable(conn, only_serial)
        if _why:
            skipped.append({"asset_serial": only_serial, "reason": _why})

    per_host: list[dict] = []

    for t in targets:
        ip, serial = t["ip"], t["asset_serial"]
        platform = _platform_for(conn, ip, t["asset_serial"])

        # Windows 走 WinRM，不走 SSH——這是 2026-07-19 定案（見 winrm_collector 檔頭）：
        # 走 WinRM 納管的機器根本沒裝 OpenSSH，硬用 SSH 這條路必定連不上。
        # facts 收集已經是這樣分流，服務收集不分流就會兩套行為不一致。
        if platform == "windows" and runner is None:
            result = _collect_windows_services(conn, ip, serial, failed)
            if result is None:
                continue
        else:
            run = runner or manage_state._runner_for(
                ip, key_path or manage_state.COLLECTOR_KEY_DEFAULT,
                account=_acct(conn, serial, platform)
            )
            try:
                result = service_collector.collect(run, ip, platform)
            except Exception as exc:  # noqa: BLE001 - 單台失敗不整批中斷，原因原樣留著
                failed.append({"asset_serial": serial, "ip": ip, "error": str(exc)[:200]})
                _save_trace(conn, run, serial, ip)
                continue
            _save_trace(conn, run, serial, ip)
            # ⚠️ 2026-09-23 真機實測：這一格顯示「這一輪沒收到，而且系統也講不出為什麼」。
            # 那句話是畫面在**後端什麼都沒給**時的最後退路——而後端確實什麼都沒給：
            # 收到 0 筆服務不算失敗（collect 不拋例外），所以 failed 是空的。
            #
            # 但 0 筆在一台連得上的機器上**幾乎不可能**（至少會有 sshd 在聽 22），
            # 所以收到 0 筆時要去看 SSH 那一跳的事實，把原因講出來。
            if not result.get("services"):
                failed.append({
                    "asset_serial": serial, "ip": ip,
                    "error": ("一個監聽埠都沒收到——連得上的機器至少會有 sshd 在聽 22，"
                              "所以這是收集失敗，不是這台沒有服務。"
                              + _ssh_verdict(conn, serial))[:400]})
                continue
        # 來源要看得出「這筆是怎麼收到的」——查資料可信度時第一個看的就是它
        # 收集器自己這台也走 SSH（2026-09-11 起，見 manage_state._runner_for）
        source = "winrm_nettcp" if platform == "windows" and runner is None else "ssh_ss"
        stat = upsert_services(conn, ip, serial, result["services"], source)
        total_services += len(result["services"])
        per_host.append({
            "asset_serial": serial, "ip": ip, "found": len(result["services"]),
            "process_visible": result["process_visible"], **stat,
        })

    status = "failed" if failed and not per_host else "ok"
    conn.execute(
        "UPDATE service_collect_runs SET status = ?, host_count = ?, service_count = ?, "
        "failed_count = ?, error = ?, finished_at = ? WHERE id = ?",
        (status, len(targets), total_services, len(failed),
         "; ".join(f["error"] for f in failed)[:500] or None, _now_local(), run_id),
    )
    conn.commit()

    return {
        "run_id": run_id, "status": status, "candidates": len(targets),
        "services": total_services, "hosts": per_host, "failed": failed,
        "skipped": skipped,
    }


def list_services(conn, ip: str | None = None, asset_serial: str | None = None,
                  include_gone: bool = False, include_infra: bool = True) -> list[dict]:
    """查服務清單。預設含基礎服務（SSH/NTP…），但不含已消失的。"""
    where, params = ["1=1"], []
    if ip:
        where.append("hs.ip = ?")
        params.append(ip)
    if asset_serial:
        where.append("hs.asset_serial = ?")
        params.append(asset_serial)
    if not include_gone:
        where.append("hs.gone_at IS NULL")
    if not include_infra:
        where.append("hs.is_infra = 0")
    rows = conn.execute(
        "SELECT hs.*, h.hostname AS hostname FROM host_service hs "
        "LEFT JOIN hardware h ON h.asset_serial = hs.asset_serial "
        f"WHERE {' AND '.join(where)} ORDER BY hs.ip, hs.port",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def service_summary(conn) -> dict:
    """給儀表板/頁首的幾個數字。全是「這份資料有多真」的線索，不是裝飾。"""
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN gone_at IS NULL THEN 1 ELSE 0 END) AS live, "
        "SUM(CASE WHEN gone_at IS NOT NULL THEN 1 ELSE 0 END) AS gone, "
        "COUNT(DISTINCT ip) AS hosts, "
        "SUM(CASE WHEN gone_at IS NULL AND exposure = 'all' THEN 1 ELSE 0 END) AS exposed, "
        "SUM(CASE WHEN gone_at IS NULL AND guess_source = 'process' THEN 1 ELSE 0 END) AS confirmed "
        "FROM host_service"
    ).fetchone()
    last = conn.execute(
        "SELECT * FROM service_collect_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {
        "total": row["total"] or 0,
        "live": row["live"] or 0,
        "gone": row["gone"] or 0,
        "hosts": row["hosts"] or 0,
        "exposed": row["exposed"] or 0,      # 綁 0.0.0.0＝別台連得到，是依賴分析的起點
        "confirmed": row["confirmed"] or 0,  # 有行程名佐證的（其餘只是埠號推測）
        "last_run": dict(last) if last else None,
    }


# ---- 診斷外掛（跟其他模組同一套，讓診斷包看得到這塊的現況）----
try:
    import diagnostics

    @diagnostics.register("services")
    def _diag(conn) -> dict:
        try:
            return service_summary(conn)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:200]}
except ImportError:
    pass
