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

import json

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
    import machine_scope

    # 同一個 IP 只收一次（2026-10-16）：同一台有多筆登記時以前每筆各收一次，
    # 「收到 N 筆」變成 N×登記數，資料列的序號還會被最後一次改寫。見 machine_scope。
    rows, _vm = manage_state.drop_vmware_targets(conn, [dict(r) for r in conn.execute(sql, params).fetchall()])  # 天條
    return machine_scope.dedupe_targets(rows, only_serial)


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
                              failed: list, with_connections: bool = True) -> dict | None:
    """Windows 服務收集：WinRM + Get-NetTCPConnection。憑證從庫裡取、用完即丟、留稽核。

    跟 manage_state._collect_windows 同一套流程（同憑證、同稽核），
    差別只在跑的是「誰在聽埠」而不是「這台機器是什麼」。
    失敗回 None 並把**能行動的原因**放進 failed——「沒有憑證」跟「WinRM 沒開」
    要做的事完全不同，混成一句「連線失敗」等於沒講。
    """
    import credential_store
    import service_collector
    import win_onboard
    import winrm_collector

    # Windows 勾選才收（2026-10-22）：沒納管的不登入（第二道；第一道是只挑 collect_ok=1）
    blocked = win_onboard.collect_block(conn, ip, asset_serial)
    if blocked:
        failed.append({"asset_serial": asset_serial, "ip": ip, "error": blocked})
        return None
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
        # 連線收集關掉時才多帶參數（開著維持原呼叫，注入/替身相容）
        raw = (winrm_collector.collect_services(ip, username, password) if with_connections
               else winrm_collector.collect_services(ip, username, password, with_connections=False))
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)[:200]
        if "actively refused" in msg or "timed out" in msg or "Max retries" in msg:
            msg = f"{msg}｜{winrm_collector.enable_hint()}"
        credential_store.audit_use(conn, cred_name, ip, False, msg)
        failed.append({"asset_serial": asset_serial, "ip": ip, "error": msg})
        return None
    credential_store.audit_use(conn, cred_name, ip, True, "服務收集成功")

    # 監聽埠的解析共用 Linux 那支（SERVICES_PS 的 LISTEN 行刻意跟 ss 對齊）；
    # 另外帶回 Win32_Service 清單與每一項的 ERR（權限不足要講出來，不靜默變 0）。
    got = service_collector.parse_windows_services(raw)
    services = got["ports"]
    errors = got["errors"]
    if not services and not got["winservices"]:
        # 兩樣都空：一定是收集失敗（連得上 WinRM 的機器至少在聽 5985），講得出原因就講
        why = "；".join(winrm_collector.explain_errors(errors)) or \
            "監聽埠與服務清單都沒收到（WinRM 連得上的機器至少在聽 5985），這是收集失敗"
        failed.append({"asset_serial": asset_serial, "ip": ip, "error": why[:400]})
        return None
    # 部分收到（例如服務清單退回登錄檔、沒有執行狀態）也要講：少了哪一格、為什麼少
    notes = winrm_collector.explain_errors(errors)
    if got.get("ports_source") == "dotnet":
        notes.append("監聽埠／連線改用 .NET 退路收（這台的 Get-NetTCPConnection 不能用）：拿得到，但沒有行程／服務名")
    import iis_inventory

    return {"iis": iis_inventory.parse_iis(raw), "services": services, "units": [], "winservices": got["winservices"],
            # SCM 被拒後退回登錄檔、登錄檔又讀到一半被拒 → 清單不完整，這輪不標消失
            "winservices_complete": not any(e["item"] == "services_registry" for e in errors),
            "notes": notes, "process_visible": any(s["process"] for s in services),
            # 已建立的連線（observed_deps 推上下游用；寫入在 collect_services 共用那段）
            "connections": got.get("connections") or [],
            "connections_truncated": got.get("connections_truncated", 0),
            "connections_dropped": got.get("connections_dropped", 0)}


def upsert_winservices(conn, ip: str, asset_serial: str | None, rows: list[dict],
                       mark_gone: bool = True) -> dict:
    """Windows 服務清單寫入 host_winservice；這次沒看到的標 gone_at（不刪）。

    rows 是空的（服務清單整個拿不到）時**什麼都不動**——拿不到不等於服務全被移除。
    """
    if not rows:
        return {"added": 0, "updated": 0, "gone": 0}
    now = _now_local()
    seen = set()
    added = updated = 0
    for r in rows:
        seen.add(r["name"])
        src = "winrm_registry" if r.get("via") == "registry" else "winrm_cim"
        ex = conn.execute("SELECT id FROM host_winservice WHERE ip = ? AND name = ?",
                          (ip, r["name"])).fetchone()
        vals = (asset_serial, r.get("display_name"), r.get("state"), r.get("start_mode"),
                r.get("account"), r.get("pid"), r.get("path_name"), r.get("description"),
                r.get("delayed"), r.get("vendor"), src, now)
        if ex:
            conn.execute(
                "UPDATE host_winservice SET asset_serial = ?, display_name = ?, state = ?, "
                "start_mode = ?, account = ?, pid = ?, path_name = ?, description = ?, delayed = ?, "
                "vendor = ?, source = ?, last_seen = ?, gone_at = NULL "
                "WHERE id = ?", (*vals, ex["id"]))
            updated += 1
        else:
            conn.execute(
                "INSERT INTO host_winservice (asset_serial, display_name, state, start_mode, "
                "account, pid, path_name, description, delayed, vendor, source, last_seen, ip, name, first_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*vals, ip, r["name"], now))
            added += 1
    gone = 0
    for r in ([] if not mark_gone else conn.execute(
            "SELECT id, name FROM host_winservice WHERE ip = ? AND gone_at IS NULL", (ip,)).fetchall()):
        if r["name"] not in seen:
            conn.execute("UPDATE host_winservice SET gone_at = ? WHERE id = ?", (now, r["id"]))
            gone += 1
    conn.commit()
    return {"added": added, "updated": updated, "gone": gone}


def list_winservices(conn, asset_serial: str | None = None, ip: str | None = None,
                     include_gone: bool = False) -> list[dict]:
    """Windows 服務清單（資產頁「服務」分頁的第二張表）。範圍同 list_services（整台）。"""
    where, params = ["1=1"], []
    if ip:
        where.append("w.ip = ?")
        params.append(ip)
    if asset_serial:
        import machine_scope
        w, wp = machine_scope.scope_where(machine_scope.scope(conn, asset_serial), "w")
        where.append(w)
        params.extend(wp)
    if not include_gone:
        where.append("w.gone_at IS NULL")
    rows = [dict(r) for r in conn.execute(
        f"SELECT w.* FROM host_winservice w WHERE {' AND '.join(where)} ORDER BY w.ip, w.name",
        params)]
    _decorate_winservices(conn, rows)
    return rows


#: 系統內建帳號（大小寫不分）：其餘（網域帳號、本機使用者帳號）＝「用人的帳號跑服務」，畫面要標出來
_WIN_SYSTEM_ACCOUNTS = {"localsystem", "nt authority\\system", "nt authority\\localservice",
                        "nt authority\\local service", "nt authority\\networkservice",
                        "nt authority\\network service", ".\\localsystem"}


def win_account_kind(account: str | None) -> str | None:
    """執行帳號 → system（內建）／virtual（NT SERVICE\\、虛擬帳號）／domain／local。None＝沒回報。"""
    a = (account or "").strip().lower()
    if not a:
        return None
    if a in _WIN_SYSTEM_ACCOUNTS:
        return "system"
    if a.startswith(("nt service\\", "nt authority\\")):
        return "virtual"
    if a.startswith(".\\") or "\\" not in a and "@" not in a:
        return "local"
    return "domain"


def unquoted_path(path: str | None) -> bool:
    """沒加引號、且執行檔路徑本身含空白（CWE-428：未加引號的服務路徑）。"""
    p = (path or "").strip()
    if not p or p.startswith('"'):
        return False
    low = p.lower()
    i = low.find(".exe")
    exe = p[: i + 4] if i >= 0 else p.split(" ")[0]
    return " " in exe


def is_microsoft(vendor: str | None, path: str | None) -> bool | None:
    """執行檔公司名有 Microsoft＝微軟；讀不到公司名就看路徑在不在 Windows 目錄。都沒有＝不知道。"""
    v = (vendor or "").lower()
    if v:
        return "microsoft" in v
    p = (path or "").strip().strip('"').lower()
    if not p:
        return None
    return p.startswith(("c:\\windows\\", "%systemroot%", "\\systemroot\\", "system32\\"))


def _decorate_winservices(conn, rows: list[dict]) -> None:
    """列出時補：執行帳號種類、未加引號路徑、非微軟、監聽埠（PID→服務，看 host_service 的行程名）、IIS 站台。"""
    import re

    ips = sorted({r["ip"] for r in rows})
    ports_of: dict[tuple, list[str]] = {}
    if ips:
        ph = ",".join("?" for _ in ips)
        for p in conn.execute(
                f"SELECT ip, proto, port, process FROM host_service WHERE gone_at IS NULL AND ip IN ({ph}) "
                "ORDER BY port", ips):
            m = re.search(r"\(([^()]*)\)\s*$", p["process"] or "")
            if not m:
                continue
            for name in m.group(1).split(","):
                k = (p["ip"], name.strip().lower())
                lab = f"{p['port']}/{p['proto'] or 'tcp'}"
                if name.strip() and lab not in ports_of.setdefault(k, []):
                    ports_of[k].append(lab)
    sites_of: dict[str, list[str]] = {}
    for ip in ips:
        try:
            row = conn.execute("SELECT payload FROM host_iis WHERE ip = ?", (ip,)).fetchone()
        except Exception:  # noqa: BLE001 - 舊庫沒有 host_iis：IIS 欄留空（畫面顯示 —），不影響服務清單
            row = None
        if row and row["payload"]:
            try:
                sites_of[ip] = [s.get("name") for s in (json.loads(row["payload"]).get("sites") or [])
                                if s.get("name")]
            except (ValueError, AttributeError):
                sites_of[ip] = []
    for r in rows:
        r["account_kind"] = win_account_kind(r.get("account"))
        r["unquoted_path"] = unquoted_path(r.get("path_name"))
        r["is_microsoft"] = is_microsoft(r.get("vendor"), r.get("path_name"))
        r["ports"] = ports_of.get((r["ip"], (r.get("name") or "").lower()), [])
        # IIS 站台都跑在 W3SVC（WAS 管集區）底下
        r["iis_sites"] = sites_of.get(r["ip"], []) if (r.get("name") or "").upper() == "W3SVC" else []




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
    # 連線收集總開關（observed_deps.COLLECT_KEY，預設開）：每輪開頭讀一次，收集當下生效
    import observed_deps as _od

    conn_on = _od.collect_enabled(conn)
    conn_sum = {"enabled": conn_on, "hosts": 0, "rows": 0, "added": 0, "updated": 0, "pruned": 0,
                "expired": 0, "retention_days": _od.retention_days(conn)}

    for t in targets:
        ip, serial = t["ip"], t["asset_serial"]
        platform = _platform_for(conn, ip, t["asset_serial"])

        # Windows 走 WinRM，不走 SSH——這是 2026-07-19 定案（見 winrm_collector 檔頭）：
        # 走 WinRM 納管的機器根本沒裝 OpenSSH，硬用 SSH 這條路必定連不上。
        # facts 收集已經是這樣分流，服務收集不分流就會兩套行為不一致。
        if platform == "windows" and runner is None:
            result = (_collect_windows_services(conn, ip, serial, failed) if conn_on
                      else _collect_windows_services(conn, ip, serial, failed, with_connections=False))
            if result is None:
                continue
        else:
            run = runner or manage_state._runner_for(
                ip, key_path or manage_state.COLLECTOR_KEY_DEFAULT,
                account=_acct(conn, serial, platform)
            )
            try:
                result = (service_collector.collect(run, ip, platform) if conn_on
                          else service_collector.collect(run, ip, platform, with_connections=False))
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
        # 監聽埠整項拿不到（ERR ports）時不要把這台的舊埠全標消失——拿不到不等於關掉了
        if result["services"] or not result.get("notes"):
            stat = upsert_services(conn, ip, serial, result["services"], source)
        else:
            stat = {"added": 0, "updated": 0, "gone": 0}
        # 觀察到的連線（2026-10-22 方案 B）：同一份 ss／netstat 輸出的 ESTAB 列，推系統上下游用。
        # Linux／AIX（ss／netstat）與 Windows（Get-NetTCPConnection／.NET 退路，2026-10-26）共用這段。寫不進去不影響服務盤點本身，但要講出來。
        cstat = None
        if not conn_on:      # 總開關關掉：即使主機端回了連線也不存
            result["connections"], result["connections_truncated"], result["connections_dropped"] = [], 0, 0
        if result.get("connections"):
            import observed_deps

            try:
                cstat = observed_deps.store(conn, ip, serial, result["connections"])
                conn_sum["hosts"] += 1
                conn_sum["rows"] += len(result["connections"])
                conn_sum["added"] += cstat["added"]
                conn_sum["updated"] += cstat["updated"]
                conn_sum["pruned"] += cstat["pruned"]
            except Exception as exc:  # noqa: BLE001 - 連線是加分項；失敗原因放進 notes 讓畫面看得到
                result.setdefault("notes", []).append(f"連線紀錄寫入失敗：{str(exc)[:120]}")
        dropped = result.get("connections_dropped") or 0
        if dropped:
            result.setdefault("notes", []).append(f"已建立連線太多，主機端只送回前一部分（略過 {dropped} 條）")
        if (result.get("connections_truncated") or 0) - dropped > 0:
            result.setdefault("notes", []).append(
                f"連線組數超過上限，只留連線數最多的 {len(result['connections'])} 組"
                f"（截掉 {result['connections_truncated'] - dropped} 組）")
        wstat = upsert_winservices(conn, ip, serial, result.get("winservices") or [],
                                   mark_gone=result.get("winservices_complete", True))
        if result.get("iis") is not None:
            import iis_inventory

            iis_note = iis_inventory.store(conn, ip, serial, result["iis"])
            if iis_note and iis_note != "此機未使用 IIS":
                result.setdefault("notes", []).append(iis_note)
        total_services += len(result["services"])
        per_host.append({
            "asset_serial": serial, "ip": ip, "found": len(result["services"]),
            "winservices": len(result.get("winservices") or []),
            "notes": result.get("notes") or [],
            "process_visible": result["process_visible"], **stat,
            "connections": len(result.get("connections") or []),
            "connection_added": (cstat or {}).get("added", 0),
            "winservice_added": wstat["added"], "winservice_gone": wstat["gone"],
        })

    # 連線推論先算好放進快取（observed_deps.infer）：之後第一個打開 4-1／4-2 的人不用等
    observed_error = None
    if any(h.get("connections") for h in per_host):
        import observed_deps

        try:
            observed_deps.infer(conn)
        except Exception as exc:  # noqa: BLE001 - 預先計算失敗不影響收集；原因回在 observed_error，畫面打開時會再算一次
            observed_error = f"連線推論預先計算失敗：{exc}"[:200]

    # 過期的連線列（超過保留天數）每輪結尾清一次；清不掉不影響收集
    try:
        conn_sum["expired"] = _od.prune_expired(conn)
    except Exception as exc:  # noqa: BLE001 - 清理失敗只留原因，下一輪再清
        conn_sum["expired_error"] = str(exc)[:120]

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
        "skipped": skipped, "observed_error": observed_error, "connections": conn_sum,
    }


def stale_days(conn=None) -> int:
    """資料多久沒更新算「過期」：沿用 observed_deps_stale_days（預設 30）。"""
    if conn is None:
        return DEFAULT_STALE_DAYS
    import observed_deps

    return observed_deps.stale_days(conn)


DEFAULT_STALE_DAYS = 30


def stale_cond(days: int, a: str = "hs.", ip_col: str = "ip") -> str:
    """W221-012：一列「資料過期」的條件（已消失的不在此討論，呼叫端另外限 gone_at IS NULL）。

    過期＝沒有 last_seen、或 last_seen 超過 N 天、或這台主機最近一次收集嘗試（hardware.collect_ok=0）
    失敗而且發生在這列最後一次看到之後——整台收集失敗時，舊列不得算「現行」。
    a 是資料表別名（含點），子查詢內的 hardware 一律用 hh，避免欄位名撞到。"""
    d = int(days)
    return (f"({a}last_seen IS NULL OR {a}last_seen < datetime('now','localtime','-{d} days') "
            f"OR EXISTS (SELECT 1 FROM hardware hh WHERE hh.ip = {a}{ip_col} AND hh.collect_ok = 0 "
            f"AND hh.collect_checked_at IS NOT NULL AND hh.collect_checked_at > {a}last_seen))")


def card_where(days: int = DEFAULT_STALE_DAYS, a: str = "hs.") -> dict[str, str]:
    """頁首數字卡的判定條件（欄位一律帶表別名 a）。"""
    st = stale_cond(days, a)
    return {
        "total": "1=1",
        "live": f"{a}gone_at IS NULL",
        "gone": f"{a}gone_at IS NOT NULL",
        "hosts": "1=1",                     # 台數＝這些列的不重複 IP（summary 用 COUNT(DISTINCT ip)）
        "exposed": f"{a}gone_at IS NULL AND {a}exposure = 'all'",
        "confirmed": f"{a}gone_at IS NULL AND {a}guess_source = 'process'",
        # W221-012：live ＝ current ＋ stale（兩者不重疊）
        "current": f"{a}gone_at IS NULL AND NOT {st}",
        "stale": f"{a}gone_at IS NULL AND {st}",
    }


#: 頁首數字卡的判定條件——**唯一一份**（Q82，2026-10-17）。service_summary 用它數、
#: list_services(card=…) 用它篩，所以「卡片上寫 N」點進去一定是 N 筆（hosts 是 N 台）。
#: 新增卡片只能加在 card_where()，不可以在 summary 或前端另寫一份條件。
#: CARD_WHERE 只是預設天數的版本（給「卡片名稱有哪些」用）；實際判定一律走 card_where(stale_days(conn))。
CARD_WHERE: dict[str, str] = card_where()


def list_services(conn, ip: str | None = None, asset_serial: str | None = None,
                  include_gone: bool = False, include_infra: bool = True,
                  card: str | None = None) -> list[dict]:
    """查服務清單。預設含基礎服務（SSH/NTP…），但不含已消失的。

    card＝頁首數字卡的下鑽：只套 CARD_WHERE[card]，忽略 include_gone／include_infra
    （卡片的數字本來就沒扣管理服務、也沒扣已消失，篩掉會變成「寫 N 點進去少幾筆」）。
    """
    where, params = ["1=1"], []
    if card is not None:
        cw = card_where(stale_days(conn))
        if card not in cw:
            raise ValueError(f"unknown card: {card}")
        where.append("(" + cw[card] + ")")
        include_gone, include_infra = True, True
    if ip:
        where.append("hs.ip = ?")
        params.append(ip)
    sc = None
    if asset_serial:
        # 「這台」＝同一台的全部登記＋它們的 IP 收到的（2026-10-16／10-28，machine_scope）
        import machine_scope
        sc = machine_scope.scope(conn, asset_serial)
        w, wp = machine_scope.scope_where(sc, "hs")
        where.append(w)
        params.extend(wp)
    if not include_gone:
        where.append("hs.gone_at IS NULL")
    if not include_infra:
        where.append("hs.is_infra = 0")
    days = stale_days(conn)
    rows = conn.execute(
        "SELECT hs.*, h.hostname AS hostname, "
        f"CASE WHEN hs.gone_at IS NULL AND {stale_cond(days)} THEN 1 ELSE 0 END AS is_stale "
        "FROM host_service hs "
        "LEFT JOIN hardware h ON h.asset_serial = hs.asset_serial "
        f"WHERE {' AND '.join(where)} ORDER BY hs.ip, hs.port",
        params,
    ).fetchall()
    out = [dict(r) for r in rows]
    if sc is not None:
        # 同一台兩個 IP 各收一次：同一個服務只列一次（數字＝asset_collect_status 同一把鍵）
        out = machine_scope.dedupe_rows(out, "host_service", sc.get("ip"))
    return out


def service_summary(conn) -> dict:
    """給儀表板/頁首的幾個數字。全是「這份資料有多真」的線索，不是裝飾。"""
    days = stale_days(conn)
    W = card_where(days)
    row = conn.execute(
        f"SELECT SUM(CASE WHEN {W['total']} THEN 1 ELSE 0 END) AS total, "
        f"SUM(CASE WHEN {W['live']} THEN 1 ELSE 0 END) AS live, "
        f"SUM(CASE WHEN {W['gone']} THEN 1 ELSE 0 END) AS gone, "
        f"COUNT(DISTINCT CASE WHEN {W['hosts']} THEN hs.ip END) AS hosts, "
        f"SUM(CASE WHEN {W['exposed']} THEN 1 ELSE 0 END) AS exposed, "
        f"SUM(CASE WHEN {W['confirmed']} THEN 1 ELSE 0 END) AS confirmed, "
        f"SUM(CASE WHEN {W['current']} THEN 1 ELSE 0 END) AS current, "
        f"SUM(CASE WHEN {W['stale']} THEN 1 ELSE 0 END) AS stale "
        "FROM host_service hs"
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
        # W221-012：live ＝ current ＋ stale。stale＝last_seen 超過 stale_days 天、或該主機最近一次收集失敗
        "current": row["current"] or 0,
        "stale": row["stale"] or 0,
        "stale_days": days,
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
