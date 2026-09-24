"""軟體盤點的寫入層：把 software_collector 收到的套件落進 host_package，並記錄異動。

跟 service_inventory 同一個模式（對已納管的機器逐台收、消失標 gone_at 不刪），
多一件事：**每一輪跟上一輪比，新裝／移除寫進 package_change**。

## 「裝過」只能從第一次盤點開始算（使用者 2026-09-11 拍板：從現在開始記錄）

一台主機**第一次**被收到時，它身上所有套件都是「本來就在」，不是「剛裝的」。
那一輪不寫任何異動——否則第一次盤點會噴出幾千筆假的「新增」，
真正的新裝被淹沒。第一次盤點以前發生過什麼，一律是「不知道」。

## 為什麼 key 含版本

同一個套件可以同時裝好幾個版本（kernel 就是：新舊核心並存，開機可選）。
key 用 (ip, 名稱, 架構, 版本)，升級就會記成「舊版移除＋新版新增」兩筆，
畫面上照名稱排在一起就看得出是升級。

## 收集失敗不能被當成「軟體被移除」

rpm 機器不可能零套件。收到 0 個、或輸出沒有 SRC= 行 → 當失敗，**不動這台的任何資料**。
不然 SSH 抖一下，隔天報表就會出現「這台 800 個套件全部被移除」。
這台的資料停在上一輪，畫面靠 last_seen 看得出來它舊了。
"""
from __future__ import annotations

from db import _now_local

UNSUPPORTED = "unsupported"


def _resolve_targets(conn, only_serial: str | None = None) -> list[dict]:
    """要收哪些主機：已納管（collect_ok=1）且有 IP 的資產。同服務盤點。"""
    import manage_state

    sql, params = manage_state.collect_targets_sql(
        conn, only_serial,
        "SELECT asset_serial, ip FROM hardware "
        "WHERE ip IS NOT NULL AND ip != '' AND collect_ok = 1")
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def upsert_packages(conn, ip: str, asset_serial: str | None, packages: list[dict],
                    source: str, run_id: int | None = None) -> dict:
    """寫入一台主機這一輪的套件，回傳 {added, removed, unchanged, baseline}。

    baseline=True 表示這台是第一次盤點：資料照寫，但不記異動（見檔頭）。
    """
    now = _now_local()
    baseline = conn.execute(
        "SELECT 1 FROM host_package WHERE ip = ? LIMIT 1", (ip,)).fetchone() is None

    existing = {
        (r["name"], r["arch"], r["version"]): r
        for r in conn.execute(
            "SELECT id, name, arch, version, gone_at FROM host_package WHERE ip = ?", (ip,))
    }
    seen: set[tuple] = set()
    added = unchanged = 0
    for p in packages:
        key = (p["name"], p["arch"], p["version"])
        seen.add(key)
        row = existing.get(key)
        if row is None:
            conn.execute(
                "INSERT INTO host_package (ip, asset_serial, name, version, arch, vendor, "
                "installed_at, source, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ip, asset_serial, p["name"], p["version"], p["arch"], p.get("vendor"),
                 p.get("installed_at"), source, now, now))
            is_new = True
        else:
            conn.execute(
                "UPDATE host_package SET asset_serial = ?, vendor = ?, installed_at = ?, "
                "source = ?, last_seen = ?, gone_at = NULL WHERE id = ?",
                (asset_serial, p.get("vendor"), p.get("installed_at"), source, now, row["id"]))
            # 之前標過消失、這次又出現＝重新裝回來，也是一筆新增
            is_new = row["gone_at"] is not None
        if is_new:
            added += 1
            if not baseline:
                _log_change(conn, ip, asset_serial, p, "added", now, run_id)
        else:
            unchanged += 1

    removed = 0
    for key, row in existing.items():
        if key in seen or row["gone_at"] is not None:
            continue
        conn.execute("UPDATE host_package SET gone_at = ? WHERE id = ?", (now, row["id"]))
        removed += 1
        name, arch, version = key
        _log_change(conn, ip, asset_serial,
                    {"name": name, "arch": arch, "version": version}, "removed", now, run_id)

    conn.commit()
    return {"added": 0 if baseline else added, "removed": removed,
            "unchanged": unchanged, "baseline": baseline}


def _log_change(conn, ip, asset_serial, p, change, when, run_id) -> None:
    conn.execute(
        "INSERT INTO package_change (ip, asset_serial, name, arch, version, change, "
        "detected_at, run_id) VALUES (?,?,?,?,?,?,?,?)",
        (ip, asset_serial, p["name"], p.get("arch") or "", p.get("version") or "",
         change, when, run_id))


def _collect_windows(conn, ip: str, asset_serial: str | None, failed: list) -> dict | None:
    """Windows：WinRM 讀登錄檔。憑證從庫裡取、用完即丟、留稽核（同服務盤點）。未在真機驗證。"""
    import credential_store
    import software_collector
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
        raw = winrm_collector.collect_software(ip, username, password)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)[:200]
        if "actively refused" in msg or "timed out" in msg or "Max retries" in msg:
            msg = f"{msg}｜{winrm_collector.ENABLE_HINT}"
        credential_store.audit_use(conn, cred_name, ip, False, msg)
        failed.append({"asset_serial": asset_serial, "ip": ip, "error": msg})
        return None
    credential_store.audit_use(conn, cred_name, ip, True, "軟體盤點成功")
    source, packages = software_collector.parse_packages(raw)
    return {"source": source, "packages": packages}




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


def collect_software(conn, key_path: str | None = None, runner=None,
                     only_serial: str | None = None, trigger: str = "manual") -> dict:
    """對已納管主機逐台收軟體清單，寫進 host_package，並留一筆執行紀錄。"""
    import manage_state
    import service_inventory
    import software_collector

    cur = conn.execute(
        "INSERT INTO software_collect_runs (trigger, status, started_at) "
        "VALUES (?, 'running', ?)", (trigger, _now_local()))
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
    failed: list[dict] = []
    unsupported: list[dict] = []
    skipped: list[dict] = []
    # 指定單台卻一台都沒進迴圈 -> 講得出為什麼（2026-09-23）。
    # 不講的話前端只能填「可能不在可收集清單、或被排除」，那句話等於沒講。
    if only_serial and not targets:
        import manage_state as _ms
        _why = _ms.why_not_collectable(conn, only_serial)
        if _why:
            skipped.append({"asset_serial": only_serial, "reason": _why})

    per_host: list[dict] = []
    total = 0

    for t in targets:
        ip, serial = t["ip"], t["asset_serial"]
        # 平台判定跟服務盤點共用同一支，兩邊不會一台判成 Windows、一台判成 Linux
        platform = service_inventory._platform_for(conn, ip, serial)
        if platform == "windows" and runner is None:
            result = _collect_windows(conn, ip, serial, failed)
            if result is None:
                continue
            source = "winrm_registry"
        else:
            run = runner or manage_state._runner_for(
                ip, key_path or manage_state.COLLECTOR_KEY_DEFAULT,
                account=_acct(conn, serial, platform))
            try:
                result = software_collector.collect(run, ip, platform)
            except Exception as exc:  # noqa: BLE001 - 單台失敗不中斷整批
                failed.append({"asset_serial": serial, "ip": ip, "error": str(exc)[:200]})
                _save_trace(conn, run, serial, ip)
                continue
            _save_trace(conn, run, serial, ip)
            if result.get("error"):
                unsupported.append({"asset_serial": serial, "ip": ip,
                                    "reason": result["error"]})
                continue
            # 來源要標對：AIX 收的是 fileset（lslpp），不是 rpm。
            # 兩者的涵蓋範圍不同，混成同一個字串會讓人以為 AIX 也查過 rpm。
            source = "ssh_lslpp" if platform == "aix" else "ssh_rpm"

        if result["source"] == "none":
            # 沒有 rpm（Debian/Ubuntu）：不支援，不是「沒裝軟體」
            unsupported.append({"asset_serial": serial, "ip": ip,
                                "reason": "這台沒有 rpm（Debian／Ubuntu 類），目前不支援"})
            continue
        if result["source"] is None or not result["packages"]:
            # 見檔頭：收到 0 個一律當失敗，不能讓這台的資料被標成全部移除
            why = ("AIX：`lslpp -Lc` 沒有回任何 fileset。AIX 不可能零 fileset，"
                   "所以這是收集失敗（指令沒跑起來或被擋），不是這台沒裝軟體。"
                   if platform == "aix"
                   else "沒有收到任何套件（指令沒跑起來或輸出為空），這台資料維持上一輪")
            failed.append({"asset_serial": serial, "ip": ip, "error": why})
            continue

        stat = upsert_packages(conn, ip, serial, result["packages"], source, run_id)
        total += len(result["packages"])
        per_host.append({"asset_serial": serial, "ip": ip,
                         "found": len(result["packages"]), **stat})

    status = "failed" if failed and not per_host else "ok"
    conn.execute(
        "UPDATE software_collect_runs SET status = ?, host_count = ?, ok_count = ?, "
        "package_count = ?, failed_count = ?, unsupported_count = ?, error = ?, "
        "finished_at = ? WHERE id = ?",
        (status, len(targets), len(per_host), total, len(failed), len(unsupported),
         "; ".join(f["error"] for f in failed)[:500] or None, _now_local(), run_id))
    conn.commit()
    return {"run_id": run_id, "status": status, "candidates": len(targets),
            "packages": total, "hosts": per_host, "failed": failed,
            "unsupported": unsupported, "skipped": skipped}


# ===== 查詢 =====

def software_summary(conn) -> dict:
    """頁首數字：全是「這份資料有多真」的線索。"""
    row = conn.execute(
        "SELECT COUNT(DISTINCT ip) AS hosts, COUNT(*) AS live, COUNT(DISTINCT name) AS names "
        "FROM host_package WHERE gone_at IS NULL").fetchone()
    changes = conn.execute(
        "SELECT COUNT(*) FROM package_change "
        "WHERE detected_at >= datetime('now','localtime','-30 days')").fetchone()[0]
    last = conn.execute(
        "SELECT * FROM software_collect_runs ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "hosts": row["hosts"] or 0, "live": row["live"] or 0, "names": row["names"] or 0,
        "changes_30d": changes or 0,
        "last_run": dict(last) if last else None,
    }


def list_software(conn, q: str | None = None) -> list[dict]:
    """依軟體名稱彙總：幾個版本、幾台。版本字串一併帶回，畫面直接列出來。"""
    where, params = ["gone_at IS NULL"], []
    if q:
        where.append("LOWER(name) LIKE ?")
        params.append(f"%{q.lower()}%")
    rows = conn.execute(
        "SELECT name, COUNT(DISTINCT version) AS version_count, COUNT(DISTINCT ip) AS host_count, "
        "GROUP_CONCAT(DISTINCT version) AS versions, "
        "GROUP_CONCAT(DISTINCT source) AS sources "
        f"FROM host_package WHERE {' AND '.join(where)} GROUP BY name", params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["versions"] = sorted((d["versions"] or "").split(","))
        d["sources"] = sorted((d["sources"] or "").split(","))
        out.append(d)
    return out


def software_versions(conn, name: str) -> list[dict]:
    """某個軟體的各版本各幾台（下鑽第一層）。"""
    return [dict(r) for r in conn.execute(
        "SELECT version, COUNT(DISTINCT ip) AS host_count, GROUP_CONCAT(DISTINCT arch) AS archs "
        "FROM host_package WHERE gone_at IS NULL AND name = ? GROUP BY version", (name,))]


def software_hosts(conn, name: str, version: str | None = None) -> list[dict]:
    """裝了某軟體（某版本）的主機（下鑽第二層）。"""
    sql = ("SELECT hp.ip, hp.asset_serial, h.hostname, hp.version, hp.arch, hp.installed_at, "
           "hp.first_seen, hp.last_seen, hp.source FROM host_package hp "
           "LEFT JOIN hardware h ON h.asset_serial = hp.asset_serial "
           "WHERE hp.gone_at IS NULL AND hp.name = ?")
    params: list = [name]
    if version is not None:
        sql += " AND hp.version = ?"
        params.append(version)
    return [dict(r) for r in conn.execute(sql, params)]


def hosts_overview(conn) -> list[dict]:
    """依主機：每台幾個套件、最後一次收到是什麼時候（看得出哪台資料舊了）。"""
    return [dict(r) for r in conn.execute(
        "SELECT hp.ip, hp.asset_serial, h.hostname, "
        "SUM(CASE WHEN hp.gone_at IS NULL THEN 1 ELSE 0 END) AS package_count, "
        "MAX(hp.last_seen) AS last_seen, MIN(hp.first_seen) AS first_seen, "
        "GROUP_CONCAT(DISTINCT hp.source) AS sources "
        "FROM host_package hp LEFT JOIN hardware h ON h.asset_serial = hp.asset_serial "
        "GROUP BY hp.ip")]


def host_packages(conn, ip: str, include_gone: bool = False) -> list[dict]:
    sql = "SELECT * FROM host_package WHERE ip = ?"
    if not include_gone:
        sql += " AND gone_at IS NULL"
    return [dict(r) for r in conn.execute(sql, (ip,))]


def list_changes(conn, days: int = 30, ip: str | None = None) -> list[dict]:
    """異動紀錄（新裝／移除）。第一次盤點不記，所以這裡只會有真的異動。"""
    days = max(1, min(int(days), 3650))
    sql = ("SELECT pc.*, h.hostname FROM package_change pc "
           "LEFT JOIN hardware h ON h.asset_serial = pc.asset_serial "
           "WHERE pc.detected_at >= datetime('now','localtime', ?)")
    params: list = [f"-{days} days"]
    if ip:
        sql += " AND pc.ip = ?"
        params.append(ip)
    return [dict(r) for r in conn.execute(sql + " ORDER BY pc.detected_at DESC, pc.id DESC",
                                          params)]


# ---- 診斷外掛（跟其他模組同一套）----
try:
    import diagnostics

    @diagnostics.register("software")
    def _diag(conn) -> dict:
        try:
            return software_summary(conn)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:200]}
except ImportError:
    pass
