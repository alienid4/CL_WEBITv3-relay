"""帳號盤點寫入層：採集 → 落 host_account → 跑規則 → 落 account_finding。

跟 service_inventory 同一個模式（只收已納管、單台失敗不中斷、消失標記不刪除），
多一段「跑稽核規則」。

## 為什麼 finding 每次重算而不是增量更新

稽核問的是「**現在**有幾條不合規」。增量更新很容易留下已經修好卻還亮著的舊紅燈，
那比沒有紅燈更糟——沒人會相信一個會謊報的稽核系統。
每次採集重算一份、標上 run_id，歷史仍然查得到（要對照「上次稽核 vs 這次」用得到）。
"""
from __future__ import annotations

from db import _now_local


def _targets(conn, only_serial: str | None = None) -> list[dict]:
    sql = ("SELECT asset_serial, ip FROM hardware "
           "WHERE ip IS NOT NULL AND ip != '' AND collect_ok = 1")
    params: tuple = ()
    if only_serial:
        sql += " AND asset_serial = ?"
        params = (only_serial,)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    # 排除清單：非標準受管主機（沒有標準管理帳號、天生收不全）可明確排除，
    # 不納入稽核也不算失敗。排除是明確設定、可還原，不是靜默漏收。
    excluded = get_excluded_serials(conn)
    return [r for r in rows if r["asset_serial"] not in excluded]


def get_excluded_serials(conn) -> set[str]:
    """讀「不納入帳號稽核」的主機清單（asset_serial，逗號分隔存 app_settings）。"""
    from db import get_setting

    raw = get_setting(conn, "account_exclude_serials", "") or ""
    return {s.strip() for s in raw.split(",") if s.strip()}


def set_host_excluded(conn, asset_serial: str, excluded: bool) -> dict:
    """把一台主機排除／納回帳號稽核。

    排除時**清掉它的舊帳號與稽核資料**——否則排除了卻還留著上次收到的殘影，
    稽核數字會虛胖，跟「排除」的語意矛盾。納回時什麼都不刪，下次收集自然補上。
    """
    from db import set_setting

    cur = get_excluded_serials(conn)
    if excluded:
        cur.add(asset_serial)
        row = conn.execute(
            "SELECT ip FROM hardware WHERE asset_serial = ?", (asset_serial,)).fetchone()
        if row and row["ip"]:
            conn.execute("DELETE FROM host_account WHERE ip = ?", (row["ip"],))
            conn.execute("DELETE FROM account_finding WHERE ip = ?", (row["ip"],))
    else:
        cur.discard(asset_serial)
    set_setting(conn, "account_exclude_serials", ",".join(sorted(cur)))
    conn.commit()
    return {"excluded": sorted(cur)}


def list_collectable_hosts(conn) -> list[dict]:
    """所有「可收集」的主機（collect_ok=1）＋是否已排除。給排除管理與健檢選單用。"""
    excluded = get_excluded_serials(conn)
    rows = conn.execute(
        "SELECT asset_serial, hostname, ip FROM hardware "
        "WHERE ip IS NOT NULL AND ip != '' AND collect_ok = 1 ORDER BY ip").fetchall()
    return [{"asset_serial": r["asset_serial"], "hostname": r["hostname"],
             "ip": r["ip"], "excluded": r["asset_serial"] in excluded} for r in rows]


def upsert_accounts(conn, ip: str, asset_serial: str | None,
                    accounts: list[dict], source: str, os_info: dict | None = None) -> dict:
    """寫入帳號並標記這次沒看到的（gone_at）。

    帳號消失可能是「離職清掉了」（好事）也可能是「被人偷偷刪掉湮滅痕跡」（大事），
    兩種都要留得下來——所以標記不刪除。
    """
    now = _now_local()
    seen = set()
    added = updated = 0
    for a in accounts:
        seen.add(a["username"])
        row = conn.execute(
            "SELECT id FROM host_account WHERE ip = ? AND username = ?",
            (ip, a["username"]),
        ).fetchone()
        cols = {
            "asset_serial": asset_serial, "uid": a.get("uid"), "gid": a.get("gid"),
            "gecos": a.get("gecos"), "home": a.get("home"), "shell": a.get("shell"),
            "can_login": 1 if a.get("can_login") else 0, "kind": a.get("kind"),
            "last_login": a.get("last_login"),
            "never_logged_in": 1 if a.get("never_logged_in") else 0,
            "pw_status": a.get("pw_status"), "pw_last_change": a.get("pw_last_change"),
            "pw_expires": a.get("pw_expires"), "pw_max_days": a.get("pw_max_days"),
            "acct_expires": a.get("acct_expires"),
            "is_sudoer": 1 if a.get("is_sudoer") else 0,
            "sudo_nopasswd": 1 if a.get("sudo_nopasswd") else 0,
            "priv_groups": a.get("priv_groups"),
            "authorized_keys": a.get("authorized_keys"),
            "login_source": a.get("login_source"),
            "os_family": (os_info or {}).get("family"),
            "os_id": (os_info or {}).get("id"),
            "os_version": (os_info or {}).get("version"),
            "source": source, "last_seen": now, "gone_at": None,
        }
        if row:
            assigns = ", ".join(f"{k} = ?" for k in cols)
            conn.execute(f"UPDATE host_account SET {assigns} WHERE id = ?",
                         (*cols.values(), row["id"]))
            updated += 1
        else:
            cols["ip"] = ip
            cols["username"] = a["username"]
            cols["first_seen"] = now
            names = ", ".join(cols)
            marks = ", ".join("?" for _ in cols)
            conn.execute(f"INSERT INTO host_account ({names}) VALUES ({marks})",
                         tuple(cols.values()))
            added += 1

    gone = 0
    for r in conn.execute(
        "SELECT id, username FROM host_account WHERE ip = ? AND gone_at IS NULL", (ip,)
    ).fetchall():
        if r["username"] not in seen:
            conn.execute("UPDATE host_account SET gone_at = ? WHERE id = ?", (now, r["id"]))
            gone += 1
    conn.commit()
    return {"added": added, "updated": updated, "gone": gone}


def collect_accounts(conn, key_path: str | None = None, runner=None,
                     only_serial: str | None = None, trigger: str = "manual") -> dict:
    """對已納管 Linux 主機收帳號並跑稽核規則。

    Windows 目前不收：AD 帳號要走 AD 查詢（不是本機帳號），
    本機帳號在網域環境幾乎沒有稽核意義——留到接 AD 那一片再做，
    現在硬做只會產出一堆沒人要看的 local Administrator。
    """
    import account_collector
    import account_rules
    import manage_state

    cur = conn.execute(
        "INSERT INTO account_collect_runs (trigger, status, started_at) "
        "VALUES (?, 'running', ?)", (trigger, _now_local()),
    )
    conn.commit()
    run_id = cur.lastrowid

    thresholds = account_rules.get_thresholds(conn)
    collect_account = manage_state.get_collect_account(conn)
    targets = _targets(conn, only_serial)
    total_acc = total_find = needs_root_hosts = 0
    failed: list[dict] = []
    per_host: list[dict] = []

    for t in targets:
        ip, serial = t["ip"], t["asset_serial"]
        platform = _platform_for(conn, ip)
        if platform != "linux":
            continue                                  # Windows/AD 見上面 docstring
        run = runner or manage_state._runner_for(
            ip, key_path or manage_state.COLLECTOR_KEY_DEFAULT, account=collect_account)
        try:
            result = account_collector.collect(run, ip, "linux")
        except Exception as exc:  # noqa: BLE001 - 單台失敗不中斷整批
            failed.append({"asset_serial": serial, "ip": ip, "error": str(exc)[:200]})
            continue

        source = "local_shell" if ip in manage_state.local_ips() else "ssh_shell"
        stat = upsert_accounts(conn, ip, serial, result["accounts"], source,
                               os_info=result.get("os"))
        findings = account_rules.evaluate(result["accounts"], thresholds)
        for f in findings:
            conn.execute(
                "INSERT INTO account_finding (run_id, ip, asset_serial, username, rule_id, "
                "label, severity, verdict, law, detail, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, ip, serial, f["username"], f["rule_id"], f["label"],
                 f["severity"], f["verdict"], f["law"], f["detail"], _now_local()),
            )
        total_acc += len(result["accounts"])
        total_find += len(findings)
        if result["needs_root"]:
            needs_root_hosts += 1
        per_host.append({
            "asset_serial": serial, "ip": ip,
            "accounts": len(result["accounts"]), "findings": len(findings),
            "needs_root": result["needs_root"],
            "os": f"{result.get('os', {}).get('id')} {result.get('os', {}).get('version')}",
            "login_source": result.get("login_source"),
            **stat,
        })

    status = "failed" if failed and not per_host else "ok"
    conn.execute(
        "UPDATE account_collect_runs SET status = ?, host_count = ?, account_count = ?, "
        "finding_count = ?, failed_count = ?, needs_root_count = ?, error = ?, "
        "finished_at = ? WHERE id = ?",
        (status, len(targets), total_acc, total_find, len(failed), needs_root_hosts,
         "; ".join(f["error"] for f in failed)[:500] or None, _now_local(), run_id),
    )
    conn.commit()
    return {
        "run_id": run_id, "status": status, "candidates": len(targets),
        "accounts": total_acc, "findings": total_find,
        "needs_root_hosts": needs_root_hosts, "hosts": per_host, "failed": failed,
    }


def _platform_for(conn, ip: str) -> str:
    import service_collector

    row = conn.execute(
        "SELECT open_ports FROM scan_history WHERE ip = ? AND scan_ok = 1 "
        "ORDER BY scan_time DESC LIMIT 1", (ip,)
    ).fetchone()
    ports = [int(p) for p in (row["open_ports"] or "").split(",") if p.strip().isdigit()] \
        if row else []
    return service_collector.detect_platform(ports)


def list_accounts(conn, ip: str | None = None, asset_serial: str | None = None,
                  kind: str | None = None, include_gone: bool = False,
                  sudoer_only: bool = False) -> list[dict]:
    where, params = ["1=1"], []
    if ip:
        where.append("ha.ip = ?")
        params.append(ip)
    if asset_serial:
        where.append("ha.asset_serial = ?")
        params.append(asset_serial)
    if kind:
        where.append("ha.kind = ?")
        params.append(kind)
    if sudoer_only:
        where.append("(ha.is_sudoer = 1 OR ha.uid = 0)")
    if not include_gone:
        where.append("ha.gone_at IS NULL")
    rows = conn.execute(
        "SELECT ha.*, h.hostname AS hostname FROM host_account ha "
        "LEFT JOIN hardware h ON h.asset_serial = ha.asset_serial "
        f"WHERE {' AND '.join(where)} ORDER BY ha.ip, ha.username", params
    ).fetchall()
    import account_rules

    out = []
    for r in rows:
        d = dict(r)
        d["is_builtin"] = _is_builtin(d)
        # 密碼到期明講狀態（never/expired/valid/na/unknown），畫面直接顯示「已過期/未過期」
        d["pw_expiry_status"] = account_rules.password_expiry_status(d)
        out.append(d)
    return out


def set_account_note(conn, ip: str, username: str, note: str) -> dict:
    """設一台主機上某帳號的「手動備註」（稽核人員輸入，收集不覆蓋）。"""
    cur = conn.execute(
        "UPDATE host_account SET note = ? WHERE ip = ? AND username = ?",
        (note or None, ip, username))
    conn.commit()
    if cur.rowcount == 0:
        raise ValueError("查無此帳號")
    return {"ip": ip, "username": username, "note": note}


def _is_builtin(acc: dict) -> bool:
    """這個帳號是不是「可以拉掉的內建噪音」（給『拉掉內建帳號』篩選用）。

    使用者定義（2026-07-22）：bin/daemon/sshd 這類**不能登入的系統帳號**才是噪音；
    root、guest 這種**可登入的預設帳號**是稽核焦點，不該被藏。

    因此「可藏的內建」＝ kind in (builtin, service)（已知內建守護帳號＋無名系統帳號）。
    kind=default（root/guest/admin/oracle…）一律不藏——它們正是稽核最該盯的一群。

    ⚠️ 兩道保險，避免把該看的當噪音藏掉：
    - UID 0 但不是 root：等同 root 的後門（UID<UID_MIN 會被歸成 service），絕不算內建。
    - 有登入 shell 的服務帳號：服務帳號本該是 nologin，能登入本身就可疑，不藏。
    真正的異常判定仍交給規則引擎（有實質違規就不藏），這裡只擋最危險的情形當保險。
    """
    if acc.get("uid") == 0 and acc.get("username") != "root":
        return False
    if acc.get("kind") not in ("builtin", "service"):   # 內建與無名系統帳號都算可藏噪音
        return False
    if acc.get("can_login"):
        return False
    return True


# 合規表的欄位定義：key → (中文標題, 取值函式)。
# 這是「匯出」與「前端矩陣」的單一事實來源，兩邊顯示字必須一致，
# 否則稽核拿到的 Excel 跟畫面對不上——對稽核工具是致命的。
# 前端 accounts.vue 的 m* 函式是這份的鏡像，改一邊要改兩邊（有測試守）。
KIND_LABEL = {
    "human": "真人", "mgmt": "標準管理帳號", "default": "系統預設",
    "builtin": "內建帳號", "service": "服務帳號",
}


def _cell_pw_expired(a: dict) -> str:
    return {"expired": "已過期", "never": "永不過期", "valid": "未過期",
            "unknown": "需 root"}.get(a.get("pw_expiry_status"), "—")


def _norm_pw(a: dict):
    """passwd -S 狀態碼 → set/locked/empty/None（跟前端 pwState 一致）。"""
    import account_collector
    s = account_collector.normalize_pw_status(a.get("pw_status") or "")
    return s if s in ("set", "locked", "empty") else None


def _cell_disabled(a: dict) -> str:
    s = _norm_pw(a)
    return "已停用" if s == "locked" else ("需 root" if s is None else "啟用中")


def _cell_sudo(a: dict) -> str:
    if a.get("uid") == 0:
        return "UID 0"
    if a.get("sudo_nopasswd"):
        return "是·免密碼"
    return "是" if a.get("is_sudoer") else "否"


def _cell_empty(a: dict) -> str:
    s = _norm_pw(a)
    return "需 root" if s is None else ("是" if s == "empty" else "否")


def _cell_uid0(a: dict) -> str:
    if a.get("uid") == 0 and a.get("username") != "root":
        return "是"
    return "root" if a.get("uid") == 0 else "否"


def _cell_keys(a: dict) -> str:
    n = a.get("authorized_keys")
    if n is None:
        return "需 root"
    return f"{n} 把" if n > 0 else "無"


def _cell_login(a: dict) -> str:
    return "從未登入" if a.get("never_logged_in") else "有"


# 匯出可選欄位：key → (標題, 取值函式)。前 5 個是身分/備註，後 7 個是狀態欄。
MATRIX_EXPORT_COLS = {
    "hostname": ("主機", lambda a: a.get("hostname") or a.get("ip") or ""),
    "ip": ("IP", lambda a: a.get("ip") or ""),
    "username": ("帳號", lambda a: a.get("username") or ""),
    "note": ("備註", lambda a: a.get("note") or a.get("gecos") or ""),
    "kind": ("類型", lambda a: KIND_LABEL.get(a.get("kind"), a.get("kind") or "")),
    "pwExpired": ("密碼過期", _cell_pw_expired),
    "disabled": ("帳號停用", _cell_disabled),
    "sudo": ("sudo 權限", _cell_sudo),
    "empty": ("空密碼", _cell_empty),
    "uid0": ("UID 0", _cell_uid0),
    "keys": ("免密碼金鑰", _cell_keys),
    "login": ("曾登入", _cell_login),
}


DISPOSITION_STATUSES = ("open", "ack", "exception", "fixed")


def set_finding_disposition(conn, ip: str, username: str, rule_id: str, status: str,
                            note: str | None = None, exempt_until: str | None = None,
                            decided_by: str | None = None) -> dict:
    """設一條發現的處置狀態（跨盤點持久）。以 (ip,username,rule_id) 為鍵 upsert。"""
    if status not in DISPOSITION_STATUSES:
        raise ValueError(f"未支援的狀態：{status}")
    # 只有「核准例外」才需要到期日；其餘狀態把到期日清掉，避免殘留
    if status != "exception":
        exempt_until = None
    conn.execute(
        "INSERT INTO finding_disposition (ip, username, rule_id, status, note, exempt_until, "
        "decided_by, decided_at) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(ip, username, rule_id) DO UPDATE SET status=excluded.status, "
        "note=excluded.note, exempt_until=excluded.exempt_until, "
        "decided_by=excluded.decided_by, decided_at=excluded.decided_at",
        (ip, username, rule_id, status, note, exempt_until, decided_by, _now_local()),
    )
    conn.commit()
    return {"ip": ip, "username": username, "rule_id": rule_id, "status": status}


def latest_findings(conn, severity: str | None = None,
                    rule_id: str | None = None) -> list[dict]:
    """最近一次採集的稽核發現。舊 run 的留著供對照，但預設只看現況——
    稽核問的是「現在有幾條」，混入舊 run 會虛報。"""
    last = conn.execute(
        "SELECT id FROM account_collect_runs WHERE status = 'ok' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not last:
        return []
    where = ["af.run_id = ?"]
    params: list = [last["id"]]
    if severity:
        where.append("af.severity = ?")
        params.append(severity)
    if rule_id:
        where.append("af.rule_id = ?")
        params.append(rule_id)
    # 「核准例外」且未到期的不列（過期自動回來亮燈——永久例外等於把問題藏起來）。
    # 狀態改看持久的 finding_disposition，不是每次盤點就重置的 account_finding。
    where.append(
        "NOT (fd.status = 'exception' AND fd.exempt_until IS NOT NULL "
        "AND fd.exempt_until >= date('now','localtime'))")
    # join：host_account 帶 gecos/note；finding_disposition 帶處置狀態（跨盤點持久）。
    rows = conn.execute(
        "SELECT af.*, h.hostname, ha.gecos AS gecos, ha.note AS note, "
        "COALESCE(fd.status, 'open') AS status, fd.note AS disp_note, "
        "fd.exempt_until AS exempt_until, fd.decided_by AS decided_by, "
        "fd.decided_at AS decided_at "
        "FROM account_finding af "
        "LEFT JOIN hardware h ON h.asset_serial = af.asset_serial "
        "LEFT JOIN host_account ha ON ha.ip = af.ip AND ha.username = af.username "
        "LEFT JOIN finding_disposition fd ON fd.ip = af.ip AND fd.username = af.username "
        "AND fd.rule_id = af.rule_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY CASE af.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
        "af.rule_id, af.ip, af.username", params
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # 標為「已修復」卻還被偵測到＝矛盾，要提醒（修了沒生效 / 又復發）
        d["contradiction"] = d["status"] == "fixed"
        out.append(d)
    return out


def audit_summary(conn) -> dict:
    """稽核摘要：幾條不合規、幾條查不到、涵蓋率多少。

    「查不到」要單獨算並顯著呈現——它代表這份稽核報告有多少是空白的，
    把它藏起來會讓人誤以為全都查過了。
    """
    last = conn.execute(
        "SELECT * FROM account_collect_runs WHERE status = 'ok' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not last:
        return {"has_data": False}
    rows = conn.execute(
        "SELECT severity, verdict, COUNT(*) AS n FROM account_finding "
        "WHERE run_id = ? GROUP BY severity, verdict", (last["id"],)
    ).fetchall()
    by_sev = {"high": 0, "medium": 0, "low": 0}
    unknown = 0
    for r in rows:
        if r["verdict"] == "unknown":
            unknown += r["n"]
        else:
            by_sev[r["severity"]] = by_sev.get(r["severity"], 0) + r["n"]
    # 標準管理帳號(mgmt)一律算特權：它設計上就帶 NOPASSWD:ALL，只是那份權限在
    # /etc/sudoers.d 裡、沒 root 讀不到，不能因為「目前看不到」就不當它是特權。
    acc = conn.execute(
        "SELECT COUNT(*) AS n, "
        "SUM(CASE WHEN is_sudoer = 1 OR uid = 0 OR kind = 'mgmt' THEN 1 ELSE 0 END) AS priv, "
        "SUM(CASE WHEN kind = 'human' THEN 1 ELSE 0 END) AS humans, "
        "SUM(CASE WHEN kind = 'mgmt' THEN 1 ELSE 0 END) AS mgmt "
        "FROM host_account WHERE gone_at IS NULL"
    ).fetchone()
    return {
        "has_data": True,
        "run": dict(last),
        "fail_high": by_sev["high"], "fail_medium": by_sev["medium"],
        "fail_low": by_sev["low"], "unknown": unknown,
        "accounts": acc["n"] or 0, "privileged": acc["priv"] or 0,
        "humans": acc["humans"] or 0, "std_mgmt": acc["mgmt"] or 0,
        "hosts_needing_root": last["needs_root_count"] or 0,
        # 收集失敗（連不上/認證失敗）的主機數必須顯著呈現——不然「4 台裡 3 台沒收到」
        # 會被 unknown=0 的漂亮數字蓋掉，稽核工具最怕這種「看起來全綠、其實一大半沒查」。
        "failed_count": last["failed_count"] or 0,
        "host_count": last["host_count"] or 0,
        "run_error": last["error"],
        # 排除的主機明確列出——透明呈現，不是靜默不收
        "excluded": sorted(get_excluded_serials(conn)),
    }


try:
    import diagnostics

    @diagnostics.register("accounts")
    def _diag(conn) -> dict:
        try:
            return audit_summary(conn)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:200]}
except ImportError:
    pass
