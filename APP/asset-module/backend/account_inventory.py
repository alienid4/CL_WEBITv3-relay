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
    import manage_state

    sql, params = manage_state.collect_targets_sql(
        conn, only_serial,
        "SELECT asset_serial, ip FROM hardware "
        "WHERE ip IS NOT NULL AND ip != '' AND collect_ok = 1")
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    # 排除清單：非標準受管主機（沒有標準管理帳號、天生收不全）可明確排除，
    # 不納入稽核也不算失敗。排除是明確設定、可還原，不是靜默漏收。
    excluded = get_excluded_serials(conn)
    import machine_scope

    # 同一個 IP 只收一次（2026-10-16，見 machine_scope.dedupe_targets）
    return machine_scope.dedupe_targets(
        [r for r in rows if r["asset_serial"] not in excluded], only_serial)


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


def _account_time_col(conn) -> str:
    """host_account 用哪一欄當「最後盤點時間」。不寫死欄位名——寫錯就是整支 500。"""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(host_account)")}
    except Exception:  # noqa: BLE001
        return "last_seen"
    for c in ("last_seen", "collected_at", "first_seen"):
        if c in cols:
            return c
    return "id"          # 最後保底：至少不會讓整支查詢炸掉


# 帳號儀表板依 OS 分頁（2026-10-21 使用者：「3-7 帳號儀表板 這要分 WINDOWS 跟 LINUX」）。
# 每台歸一個 OS 桶；同一台所有帳號、稽核發現都跟著那台走，加總＝各桶相加。
OS_BUCKETS = ("linux", "windows", "aix", "ocp", "unknown")
# 收集器自己回報的家族（account_collector.os_family／Windows 收集器）→ 桶
_FAMILY_BUCKET = {"rhel": "linux", "debian": "linux", "suse": "linux",
                  "windows": "windows", "aix": "aix"}


def ocp_ips(conn) -> set[str]:
    """資產庫認定是 OpenShift 節點的 IP（2026-10-21 使用者：3-7 要多一個 OCP 分頁）。

    跟漏斗／OCP 叢集頁同一把尺（pipeline 的 OS 類型＝OpenShift 節點）：登記 OS 判成 immutable
    （OCP／OpenShift／CoreOS／RHCOS），或人工指定成 OpenShift 節點。退役的不算（IP 可能已給別台用）。
    ⚠ OCP 節點跑 RHCOS，收集器回報的家族會是 rhel——所以這個判斷要**優先於** os_family，
    不然 OCP 會被當成一般 Linux 算進去（使用者：「OCP 的 OS 也是 rhel 要小心」）。
    登記 OS 只寫 RHEL、也沒人工指定的 OCP 節點這裡認不出來（要靠掃描埠／連上驗證，另案）。
    """
    import manage_state
    import os_override
    import pipeline
    from onboard_eligibility import classify_os
    from system_stats import is_retired_row

    # 人工指定跟漏斗一樣「有最終決定權」：登記寫 RHCOS 但被人改成 Linux 的，就不算 OCP（獨立審查 #1）
    ov_map = os_override.active_map(conn)
    out: set[str] = set()
    for r in conn.execute("SELECT * FROM hardware WHERE ip IS NOT NULL AND TRIM(ip) != ''"):
        if is_retired_row(r):
            continue
        auto = pipeline.OS_OPENSHIFT if classify_os(r["os"])[0] == "immutable" else ""
        final = os_override.resolve(ov_map, r["hostname"], r["ip"], r["asset_serial"], auto)[0] if ov_map else auto
        if final == pipeline.OS_OPENSHIFT:
            out.update(manage_state.ips_of(r["ip"]))
    return out


def ocp_nodes(conn, buckets: dict[str, str] | None = None) -> list[dict]:
    """OCP 分頁的節點清單：OCP 叢集頁（arch.ocp_topology）的現役節點，每台標有沒有帳號資料。

    清單跟 OCP 叢集頁同一份來源，兩頁台數才對得上。OCP 第一期不納管、沒有帳密，帳號由 OCP 平台管理，
    所以這裡多半全是「不盤點」——那是刻意的，不是漏收。"""
    import arch
    import manage_state

    if buckets is None:
        buckets = host_os_buckets(conn)
    out = []
    for c in arch.ocp_topology(conn).get("clusters", []):
        for n in c.get("nodes", []):
            if n.get("stage") == "retired":
                continue
            ips = manage_state.ips_of(n.get("ip"))
            out.append({"cluster": c.get("cluster"), "hostname": n.get("hostname"), "ip": n.get("ip"),
                        "asset_serial": n.get("asset_serial"), "role": n.get("role"), "os": n.get("os"),
                        "environment": n.get("environment"),
                        "has_accounts": any(ip in buckets for ip in ips)})
    out.sort(key=lambda x: (x["cluster"] or "", x["role"] or "", x["hostname"] or ""))
    return out


def host_os_buckets(conn) -> dict[str, str]:
    """每台（IP）屬於哪個 OS 桶：linux／windows／aix／ocp／unknown。

    0. 資產庫認定是 OCP 節點的（ocp_ips）→ ocp，優先於收集器回報（RHCOS 會回報成 rhel）
    1. 先看收集時機器自己回報的 os_family（帳號合規表 3-8 判 Windows 用的就是這欄）
    2. 沒有（舊資料、或收集器認不出發行版）才退回資產庫登記的 OS（onboard_eligibility.classify_os）
    3. 兩邊都判不出來 → unknown，**照樣計數、可下鑽**，不默默丟掉
    """
    fam: dict[str, dict[str, int]] = {}
    for r in conn.execute(
            "SELECT ip, os_family, COUNT(*) AS n FROM host_account "
            "WHERE gone_at IS NULL GROUP BY ip, os_family"):
        d = fam.setdefault(r["ip"], {})
        b = _FAMILY_BUCKET.get((r["os_family"] or "").strip().lower())
        if b:
            d[b] = d.get(b, 0) + (r["n"] or 0)
    need = {ip for ip, d in fam.items() if not d}
    hw: dict[str, str] = {}
    if need:
        from onboard_eligibility import classify_os

        for r in conn.execute(
                "SELECT ip, os FROM hardware WHERE ip IS NOT NULL AND ip != '' "
                "AND TRIM(COALESCE(os, '')) != '' ORDER BY asset_serial"):
            if r["ip"] in need and r["ip"] not in hw:
                cls = classify_os(r["os"])[0]
                if cls in ("linux", "windows", "aix"):
                    hw[r["ip"]] = cls
    ocp = ocp_ips(conn)
    return {ip: ("ocp" if ip in ocp else max(d, key=lambda k: d[k]) if d else hw.get(ip, "unknown"))
            for ip, d in fam.items()}


# 「特權帳號」唯一定義（帳號儀表板卡片、下鑽清單共用）：sudo／UID 0／標準管理帳號。
# mgmt 設計上帶 NOPASSWD:ALL（在 sudoers.d、沒 root 讀不到），所以一律算特權。{a}＝表別名前綴
PRIV_SQL = "({a}is_sudoer = 1 OR {a}uid = 0 OR {a}kind = 'mgmt')"


def os_bucket_counts(buckets: dict[str, str]) -> dict[str, int]:
    """各桶幾台；四個桶一定都有鍵（0 是真的 0，不是查不到）。"""
    out = {b: 0 for b in OS_BUCKETS}
    for b in buckets.values():
        out[b] = out.get(b, 0) + 1
    return out


def inventoried_hosts(conn) -> list[dict]:
    """**盤點到哪幾台**，每台幾個帳號、幾個稽核發現（2026-09-16 使用者）。

    使用者原話：「我只知道四台? 不知道哪四台，應該是先列出哪幾台，
    我再進去點後才是那一台的全部帳號資訊」，並補充「每個盤點都是這概念」。

    以前這頁只有彙總卡片（129 個帳號、321 個發現、4 台），看不到是哪四台——
    數字對得起來也沒用，因為人要做的事是「去那台上面改」，不是「知道有 129 個帳號」。

    所以先給主機清單，點進去才是那台的帳號明細。
    """
    excluded = get_excluded_serials(conn)
    rows = conn.execute(
        f"SELECT a.ip, "
        "       MAX(a.asset_serial) AS asset_serial, "
        "       COUNT(*) AS accounts, "
        "       SUM(CASE WHEN COALESCE(a.kind_override, a.kind) = 'human' THEN 1 ELSE 0 END) AS human, "
        "       SUM(CASE WHEN a.is_sudoer = 1 THEN 1 ELSE 0 END) AS sudoers, "
        "       SUM(CASE WHEN a.never_logged_in = 1 THEN 1 ELSE 0 END) AS never_login, "
        # host_account 的時間欄是 last_seen，不是 collected_at。
        # 2026-09-17 踩到：寫成 collected_at → 整支 500 → 前端把錯誤吞掉、畫面說
        # 「這一輪沒有收到任何主機的帳號資料」，但實際上有 7 台。
        # 程式壞掉被顯示成「沒有資料」是最糟的失敗方式，所以這裡不寫死欄位名。
        f"       MAX(a.{_account_time_col(conn)}) AS collected_at "
        "FROM host_account a WHERE a.gone_at IS NULL GROUP BY a.ip ORDER BY a.ip"
    ).fetchall()

    # 每台幾個發現：稽核發現才是這個模組的產出，主機清單上就要看得到
    finding_counts: dict[str, dict[str, int]] = {}
    for f in latest_findings(conn):
        c = finding_counts.setdefault(f.get("ip"), {"total": 0, "high": 0})
        c["total"] += 1
        if (f.get("severity") or "") in ("high", "高"):
            c["high"] += 1

    # 部門與窗口（2026-09-17 使用者：「這個還缺一個很重要的欄位，對應部門與對應窗口。
    # 沒這個以後怎麼找窗口盤點」）——盤點的下一步是「去找人」，沒有窗口這份清單只能看不能用。
    hw = {r["ip"]: r for r in conn.execute(
        "SELECT ip, hostname, os, environment, physical_location, "
        "usage_unit, user_name, custodian FROM hardware "
        "WHERE ip IS NOT NULL AND ip != ''")}

    # 已納管交叉引用（3-8 帳號合規表「盤點到的主機」旁那一句）：
    # 「已納管」只有一個定義——manage_state.summarize() 的 ONBOARDED（EXIST1 起：收集帳號
    # 7 天內連得上，跟 CIA 有沒有登記無關）。這裡不自己另判，直接拿正典的代表筆集合交集。
    # CIA 有沒有登記是另一件事（cia_registered），分開給，不再混成「CIA 未登記＝沒納管」。
    import manage_state
    import system_report as _sr

    cia_ips = {r["ip"] for r in conn.execute(
        "SELECT ip, asset_serial FROM hardware WHERE ip IS NOT NULL AND ip != ''")
        if not _sr.is_off_book(r["asset_serial"])}

    _onboarded = manage_state.summarize(conn)["rep_items"].get(manage_state.ONBOARDED, [])
    onboarded_ips = {it["ip"] for it in _onboarded if it.get("ip")}
    onboarded_serials = {it["asset_serial"] for it in _onboarded if it.get("asset_serial")}
    buckets = host_os_buckets(conn)

    out = []
    for r in rows:
        h = hw.get(r["ip"])
        f = finding_counts.get(r["ip"], {"total": 0, "high": 0})
        out.append({
            "ip": r["ip"],
            "asset_serial": r["asset_serial"],
            "hostname": h["hostname"] if h else None,
            "os": h["os"] if h else None,
            "environment": h["environment"] if h else None,
            "physical_location": h["physical_location"] if h else None,
            # 使用單位＝部門；使用者＝AP 窗口；保管者＝SP 保管人。
            # 三個都留著：實務上要找的人有時是使用者、有時是保管者，只給一個會找錯人。
            "department": h["usage_unit"] if h else None,
            "contact": h["user_name"] if h else None,
            "custodian": h["custodian"] if h else None,
            "accounts": r["accounts"],
            "human": r["human"] or 0,
            "sudoers": r["sudoers"] or 0,
            "never_login": r["never_login"] or 0,
            "findings": f["total"],
            "findings_high": f["high"],
            "collected_at": r["collected_at"],
            "excluded": r["asset_serial"] in excluded,
            # 已納管＝收集帳號 7 天內連得上（跟首頁「對帳明細」同一把尺），不是這個模組自己判的
            "onboarded": r["ip"] in onboarded_ips or r["asset_serial"] in onboarded_serials,
            # CIA 有沒有登記（業務資料完整度）；沒有＝CIA 待補登
            "cia_registered": r["ip"] in cia_ips,
            # OS 桶（帳號儀表板 Linux／Windows 分頁用，host_os_buckets 同一把尺）
            "os_bucket": buckets.get(r["ip"], "unknown"),
        })
    return out


def hosts_with_account(conn, username: str) -> list[dict]:
    """這個帳號存在於哪幾台（2026-09-17 使用者：「帳號 我想要找 例如 webit3 有哪幾台有這帳號」）。

    這是跟「主機→帳號」相反的問法，而且同樣重要：要停用一個離職者的帳號、
    或確認服務帳號佈到哪些機器，都是從帳號反查主機。

    只算現況（`gone_at IS NULL`）——已經被刪掉的帳號不該還列在「有這個帳號的主機」裡。
    帶上部門與窗口，因為下一步就是去找人。
    """
    username = (username or "").strip()
    if not username:
        return []
    rows = conn.execute(
        "SELECT a.ip, a.asset_serial, a.username, a.uid, a.is_sudoer, a.sudo_nopasswd, "
        "       a.never_logged_in, a.last_login, a.pw_status, a.os_family, a.priv_groups, "
        "       COALESCE(a.kind_override, a.kind) AS kind "
        "FROM host_account a WHERE a.gone_at IS NULL AND LOWER(a.username) = LOWER(?) "
        "ORDER BY a.ip", (username,)).fetchall()
    hw = {r["ip"]: r for r in conn.execute(
        "SELECT ip, hostname, environment, physical_location, usage_unit, user_name, custodian "
        "FROM hardware WHERE ip IS NOT NULL AND ip != ''")}
    out = []
    for r in rows:
        h = hw.get(r["ip"])
        out.append({
            "ip": r["ip"], "asset_serial": r["asset_serial"],
            "hostname": h["hostname"] if h else None,
            "environment": h["environment"] if h else None,
            "physical_location": h["physical_location"] if h else None,
            "department": h["usage_unit"] if h else None,
            "contact": h["user_name"] if h else None,
            "custodian": h["custodian"] if h else None,
            "username": r["username"], "uid": r["uid"], "kind": r["kind"],
            "is_sudoer": r["is_sudoer"], "sudo_nopasswd": r["sudo_nopasswd"],
            "never_logged_in": r["never_logged_in"], "last_login": r["last_login"],
            "pw_status": r["pw_status"],
            # Windows 主機：特權欄顯示「本機管理員」，不是 sudo（2026-10-21）
            "os_family": r["os_family"], "priv_groups": r["priv_groups"],
            # 收到帳號但資產庫查不到這個 IP——要看得見
            "registered": h is not None,
        })
    return out


ACCOUNT_HOSTS_HEADERS = ["IP", "主機名稱", "資產序號", "機房", "環境",
                         "使用單位（部門）", "使用者（窗口）", "保管者",
                         "帳號", "UID", "類型", "有 sudo", "免密碼 sudo",
                         "從未登入", "最後登入", "密碼狀態"]


def account_hosts_export_rows(conn, username: str) -> list[list]:
    return [[h["ip"], h["hostname"], h["asset_serial"], h["physical_location"], h["environment"],
             h["department"], h["contact"], h["custodian"],
             h["username"], h["uid"], h["kind"],
             (("本機管理員" if h.get("os_family") == "windows" else "是") if h["is_sudoer"] else ""),
             "是" if h["sudo_nopasswd"] else "",
             "是" if h["never_logged_in"] else "", h["last_login"], h["pw_status"]]
            for h in hosts_with_account(conn, username)]


HOST_EXPORT_HEADERS = ["IP", "主機名稱", "資產序號", "機房", "環境", "作業系統",
                       "使用單位（部門）", "使用者（窗口）", "保管者",
                       "帳號數", "真人帳號", "有 sudo", "從未登入", "稽核發現", "高風險發現",
                       "盤點時間"]


def hosts_export_rows(conn) -> list[list]:
    return [[h["ip"], h["hostname"], h["asset_serial"], h["physical_location"], h["environment"],
             h["os"], h["department"], h["contact"], h["custodian"],
             h["accounts"], h["human"], h["sudoers"], h["never_login"],
             h["findings"], h["findings_high"], h["collected_at"]]
            for h in inventoried_hosts(conn)]


def upsert_accounts(conn, ip: str, asset_serial: str | None,
                    accounts: list[dict], source: str, os_info: dict | None = None,
                    mark_gone: bool = True, keep_priv: bool = False) -> dict:
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
            # Windows 專屬（2026-10-21）；Linux／AIX 收集器不給，存 NULL
            "sid": a.get("sid"), "local_groups": a.get("local_groups"),
            "principal_class": a.get("principal_class"),
            "principal_source": a.get("principal_source"),
            "source": source, "last_seen": now, "gone_at": None,
        }
        if row:
            if keep_priv:
                # 這一輪特權群組成員沒收到（權限不足）：不把舊的 is_sudoer／priv_groups 洗成「沒有」
                cols.pop("is_sudoer", None)
                cols.pop("priv_groups", None)
                cols.pop("local_groups", None)
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
    for r in ([] if not mark_gone else conn.execute(
        "SELECT id, username FROM host_account WHERE ip = ? AND gone_at IS NULL", (ip,)
    ).fetchall()):
        if r["username"] not in seen:
            conn.execute("UPDATE host_account SET gone_at = ? WHERE id = ?", (now, r["id"]))
            gone += 1
    conn.commit()
    return {"added": added, "updated": updated, "gone": gone}




def _root_acct(conn, asset_serial, platform: str) -> str:
    """帳號盤點需要 root 的欄位（密碼效期、sudo 明細、金鑰數）：root 金鑰主機走 root（ROOTKEY1）。"""
    import root_key

    return root_key.account_for(conn, asset_serial, platform)


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


def _insert_findings(conn, run_id, ip, serial, findings) -> None:
    for f in findings:
        conn.execute(
            "INSERT INTO account_finding (run_id, ip, asset_serial, username, rule_id, "
            "label, severity, verdict, law, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, ip, serial, f["username"], f["rule_id"], f["label"],
             f["severity"], f["verdict"], f["law"], f["detail"], _now_local()),
        )


def _collect_windows_accounts(conn, ip: str, asset_serial: str | None,
                              failed: list) -> dict | None:
    """Windows 本機帳號：WinRM 收（憑證從庫裡取、用完即丟、留稽核，同服務／軟體盤點）。

    一個帳號都沒收到 → 失敗，原因照 ERR 講（「權限不足（需 X）」），不寫成 0 個帳號。
    """
    import account_collector
    import credential_store
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
        raw = winrm_collector.collect_accounts(ip, username, password)
    except Exception as exc:  # noqa: BLE001 - 原因如實回報（含開通指引），不吞
        msg = str(exc)[:200]
        if "actively refused" in msg or "timed out" in msg or "Max retries" in msg:
            msg = f"{msg}｜{winrm_collector.enable_hint()}"
        credential_store.audit_use(conn, cred_name, ip, False, msg)
        failed.append({"asset_serial": asset_serial, "ip": ip, "error": msg})
        return None
    credential_store.audit_use(conn, cred_name, ip, True, "帳號盤點成功")
    res = account_collector.parse_windows_accounts(raw)
    notes = winrm_collector.explain_errors(res["errors"])
    if not any(a["kind"] != "domain" for a in res["accounts"]):
        # 本機一定至少有 Administrator（RID 500）；一個本機帳號都沒有＝沒收到，不是沒有
        why = "；".join(notes) or "沒有收到任何本機帳號（WinRM 連得上但查詢沒有回資料）"
        failed.append({"asset_serial": asset_serial, "ip": ip, "error": why[:400]})
        return None
    if res.get("local_groups_failed"):
        notes.append(f"本機群組：{res['local_groups_failed']} 組讀不到成員（「本機群組」欄可能不完整；特權群組不受影響）")
    res["notes"] = notes
    return res


def collect_accounts(conn, key_path: str | None = None, runner=None,
                     only_serial: str | None = None, trigger: str = "manual") -> dict:
    """對已納管的 Linux／AIX／Windows 主機收帳號並跑稽核規則。

    AIX（2026-09-22 起支援）：指令集與 Linux 完全不同（`lsuser`／`lssec`，
    沒有 lastlog／chage／多半也沒有 sudo），細節見 `account_collector` 的 AIX 區塊。
    以前這裡一句 `platform != "linux": continue` 把 8 台 AIX 全部靜靜跳過，
    畫面上只看得到「帳號 0」。

    Windows（2026-10-20 起支援）：WinRM 收本機帳號＋本機特權群組成員（含網域成員，標 domain）。
    網域帳號本身的密碼／閒置歸 AD 管，這裡不判；本機 Administrators 裡有誰才是這裡要看的。
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
    # ⚠️ 2026-09-23：收集帳號**要按平台決定，而且要在迴圈裡決定**。
    # AIX 的收集帳號是 8 字元的 webit3sc（max_logname 限制），不是 webit3scan。
    # 原本這裡在迴圈外呼叫 get_collect_account(conn)（沒帶平台），於是：
    #   · 一律用 webit3scan -> 那個帳號在 AIX 上不存在 -> SSH 認證失敗
    #   · 每一條指令都回空字串 -> 畫面顯示「收不到」，看起來像指令有問題
    # 真機實測（test1T，公司機）四樣全空就是這個原因。
    # 放在迴圈外還有第二個問題：一批機器混著 Linux 與 AIX 時只會有一個答案。
    targets = _targets(conn, only_serial)
    total_acc = total_find = needs_root_hosts = 0
    failed: list[dict] = []
    skipped: list[dict] = []
    per_host: list[dict] = []
    # 指定單台卻一台都沒進迴圈 -> 講得出為什麼（2026-09-23）。
    # 不講的話前端只能填「可能不在可收集清單、或被排除」，那句話等於沒講。
    if only_serial and not targets:
        import manage_state as _ms
        _why = _ms.why_not_collectable(conn, only_serial)
        if _why:
            skipped.append({"asset_serial": only_serial, "reason": _why})


    for t in targets:
        ip, serial = t["ip"], t["asset_serial"]
        platform = _platform_for(conn, ip, serial)
        if platform == "windows" and runner is None:
            # 2026-10-20：Windows 收本機帳號＋特權本機群組成員（WinRM／PSRP，非管理員可跑）
            result = _collect_windows_accounts(conn, ip, serial, failed)
            if result is None:
                continue
        elif platform not in ("linux", "aix"):
            # ⚠️ 2026-09-22：這裡原本是一句 `continue`，**什麼都不留**。
            # 結果是判不出平台的機器帳號永遠 0，而畫面上
            # 「這台真的沒有帳號」跟「我們根本沒去收」長得一模一樣。
            # 使用者的規則：收不到要講原因，不可以只顯示 0。
            skipped.append({
                "asset_serial": serial, "ip": ip, "platform": platform,
                "reason": ("Windows 帳號走 WinRM 收集，這次指定的 SSH 收集器不適用"
                           if platform == "windows"
                           else f"判不出這台是什麼平台（判成 {platform}），沒有對應的帳號採集指令"),
            })
            continue
        else:
            result = None
        if result is not None:
            source = "winrm_local"
            # 特權群組成員有任何一組沒收到 → 不標消失（網域成員可能只是這次沒列到）、
            # 不把舊的 sudo／特權群組洗成「沒有」——權限不足不等於沒有管理員
            partial = any((e.get("item") or "").startswith("members")
                          for e in result.get("errors") or [])
            stat = upsert_accounts(conn, ip, serial, result["accounts"], source,
                                   os_info={"family": "windows", "id": "windows",
                                            "version": None},
                                   mark_gone=not partial, keep_priv=partial)
            findings = account_rules.evaluate(result["accounts"], thresholds)
            _insert_findings(conn, run_id, ip, serial, findings)
            total_acc += len(result["accounts"])
            total_find += len(findings)
            per_host.append({
                "asset_serial": serial, "ip": ip, "platform": "windows",
                "accounts": len(result["accounts"]), "findings": len(findings),
                "needs_root": [], "os": "windows", "login_source": result.get("login_source"),
                "notes": result.get("notes") or [], **stat,
            })
            continue
        run = runner or manage_state._runner_for(
            ip, key_path or manage_state.COLLECTOR_KEY_DEFAULT,
            account=_root_acct(conn, serial, platform))
        try:
            result = account_collector.collect(run, ip, platform)
        except Exception as exc:  # noqa: BLE001 - 單台失敗不中斷整批
            failed.append({"asset_serial": serial, "ip": ip, "error": str(exc)[:200]})
            _save_trace(conn, run, serial, ip)
            continue
        _save_trace(conn, run, serial, ip)

        # 注入 runner（測試）以外一律 SSH，連收集器自己也是（見 manage_state._runner_for）
        source = "ssh_shell"
        stat = upsert_accounts(conn, ip, serial, result["accounts"], source,
                               os_info=result.get("os"))
        findings = account_rules.evaluate(result["accounts"], thresholds)
        _insert_findings(conn, run_id, ip, serial, findings)
        total_acc += len(result["accounts"])
        total_find += len(findings)
        if result["needs_root"]:
            needs_root_hosts += 1
        per_host.append({
            "asset_serial": serial, "ip": ip,
            "platform": platform,
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
    snapshot_privileged(conn, run_id)          # 存一筆特權帳號現值，累積趨勢（2026-09-19）
    return {
        "run_id": run_id, "status": status, "candidates": len(targets),
        "accounts": total_acc, "findings": total_find,
        "needs_root_hosts": needs_root_hosts, "hosts": per_host, "failed": failed,
        # 「沒收」跟「收到 0」是兩件事，分開回報（2026-09-22）
        "skipped": skipped,
    }


# 特權帳號現值的單一定義（趨勢快照與帳號儀表板共用，不各寫一份）。
# privileged = is_sudoer OR uid=0 OR kind='mgmt'（mgmt 設計上帶 NOPASSWD:ALL，見儀表板註）。
_PRIV_SNAPSHOT_SQL = (
    "SELECT COUNT(*) AS total_accounts, "
    "SUM(CASE WHEN is_sudoer = 1 OR uid = 0 OR kind = 'mgmt' THEN 1 ELSE 0 END) AS privileged, "
    "SUM(CASE WHEN is_sudoer = 1 THEN 1 ELSE 0 END) AS sudoers, "
    "SUM(CASE WHEN uid = 0 THEN 1 ELSE 0 END) AS uid0, "
    "SUM(CASE WHEN sudo_nopasswd = 1 THEN 1 ELSE 0 END) AS nopasswd, "
    "SUM(CASE WHEN kind = 'mgmt' THEN 1 ELSE 0 END) AS mgmt, "
    "SUM(CASE WHEN kind = 'human' THEN 1 ELSE 0 END) AS human "
    "FROM host_account WHERE gone_at IS NULL"
)
_PRIV_SNAPSHOT_COLS = ("total_accounts", "privileged", "sudoers", "uid0", "nopasswd", "mgmt", "human")


def snapshot_privileged(conn, run_id: int | None = None) -> dict:
    """每次採集後存一筆「當下有幾個特權帳號」，累積趨勢。回存進去的那筆現值。"""
    r = conn.execute(_PRIV_SNAPSHOT_SQL).fetchone()
    vals = {c: (r[c] or 0) for c in _PRIV_SNAPSHOT_COLS}
    conn.execute(
        "INSERT INTO account_priv_snapshot (taken_at, run_id, total_accounts, privileged, "
        "sudoers, uid0, nopasswd, mgmt, human) VALUES (?,?,?,?,?,?,?,?,?)",
        (_now_local(), run_id, vals["total_accounts"], vals["privileged"], vals["sudoers"],
         vals["uid0"], vals["nopasswd"], vals["mgmt"], vals["human"]),
    )
    conn.commit()
    return vals


def privileged_trend(conn, limit: int = 60) -> list[dict]:
    """特權帳號快照時間序列（舊→新），給趨勢圖用。"""
    rows = conn.execute(
        "SELECT taken_at, total_accounts, privileged, sudoers, uid0, nopasswd, mgmt, human "
        "FROM account_priv_snapshot ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _platform_for(conn, ip: str, asset_serial: str | None = None) -> str:
    """呼叫正典 `manage_state.collect_platform_for`（理由同 service_inventory）。"""
    import manage_state

    return manage_state.collect_platform_for(conn, ip, asset_serial=asset_serial)


def list_accounts(conn, ip: str | None = None, asset_serial: str | None = None,
                  kind: str | None = None, include_gone: bool = False,
                  sudoer_only: bool = False, os_bucket: str | None = None,
                  privileged_only: bool = False) -> list[dict]:
    where, params = ["1=1"], []
    if ip:
        where.append("ha.ip = ?")
        params.append(ip)
    if asset_serial:
        # 「這台」＝同一台的全部登記＋這個 IP 收到的（2026-10-16，machine_scope）。
        # 以前只認 asset_serial：同一台兩筆登記時，資料掛在另一筆上，這一頁就顯示 0。
        import machine_scope
        w, wp = machine_scope.scope_where(machine_scope.scope(conn, asset_serial), "ha")
        where.append(w)
        params.extend(wp)
    if kind:
        where.append("ha.kind = ?")
        params.append(kind)
    if sudoer_only:
        where.append("(ha.is_sudoer = 1 OR ha.uid = 0)")
    if privileged_only:
        # 跟 audit_summary「特權帳號」卡同一個條件（含標準管理帳號），卡片數＝這份清單筆數
        where.append(PRIV_SQL.format(a="ha."))
    if not include_gone:
        where.append("ha.gone_at IS NULL")
    rows = conn.execute(
        # 部門／窗口／保管者（2026-09-18 使用者：「這個怎沒有對應部門跟窗口？這樣盤點誰要確認？」）
        # 對應跟 inventoried_hosts 同一套：部門＝使用單位、窗口＝使用者（AP User）、保管者。
        # 帳號掛的那一筆沒填時，改找同 IP 的其他登記——一台多筆時常常只有其中一筆有填。
        "SELECT ha.*, h.hostname AS hostname, "
        "  COALESCE(NULLIF(TRIM(h.usage_unit),''), (SELECT h2.usage_unit FROM hardware h2 WHERE h2.ip = ha.ip "
        "     AND TRIM(COALESCE(h2.usage_unit,'')) <> '' ORDER BY h2.asset_serial LIMIT 1)) AS department, "
        "  COALESCE(NULLIF(TRIM(h.user_name),''), (SELECT h2.user_name FROM hardware h2 WHERE h2.ip = ha.ip "
        "     AND TRIM(COALESCE(h2.user_name,'')) <> '' ORDER BY h2.asset_serial LIMIT 1)) AS contact, "
        "  COALESCE(NULLIF(TRIM(h.custodian),''), (SELECT h2.custodian FROM hardware h2 WHERE h2.ip = ha.ip "
        "     AND TRIM(COALESCE(h2.custodian,'')) <> '' ORDER BY h2.asset_serial LIMIT 1)) AS custodian "
        "FROM host_account ha "
        "LEFT JOIN hardware h ON h.asset_serial = ha.asset_serial "
        f"WHERE {' AND '.join(where)} ORDER BY ha.ip, ha.username", params
    ).fetchall()
    import account_rules

    buckets = host_os_buckets(conn)
    out = []
    for r in rows:
        d = dict(r)
        # 手動覆寫的類型蓋過程式判的——要在算 is_builtin／規則適用之前套，
        # 否則把 CBoss 從 builtin 改成 service 了，規則卻還照 builtin 判（改了等於沒改）。
        if d.get("kind_override"):
            d["kind_computed"] = d["kind"]   # 保留程式原判，UI 標示「手動改過」
            d["kind"] = d["kind_override"]
        d["is_builtin"] = _is_builtin(d)
        # 密碼到期明講狀態（never/expired/valid/na/unknown），畫面直接顯示「已過期/未過期」
        d["pw_expiry_status"] = account_rules.password_expiry_status(d)
        d["os_bucket"] = buckets.get(d.get("ip"), "unknown")
        if os_bucket and d["os_bucket"] != os_bucket:
            continue
        out.append(d)
    _add_context(conn, out)
    return out


def _add_context(conn, rows: list[dict]) -> None:
    """合規表要的脈絡欄（2026-09-18 使用者：「盤點帳號跟機房無關，跟業務系統才有關係」
    「順序要跟盤點表一樣」）。

    - 業務系統／部門／窗口／保管：**跟盤點報告同一套**（account_export._host_context：
      先查業務系統對照表、查不到才用資產的使用單位／使用者；一台多系統併在同一格；
      已退役的登記不算）。不然合規表畫面跟匯出的報告會各講各的
    - 環境別：env_group（跟漏斗同一套）
    - OS 類型：pipeline._os_type（跟漏斗同一套），並套用人工指定（os_override）；
      沒登記在資產庫的主機，退回用收集到的 os_id／os_version
    - 還需要處理：有失敗的稽核發現、而且處置是「待處理」或「已確認」（已確認＝看過、還沒改好）；
      核准例外、已修復不算
    """
    import account_export as ae
    import env_group
    import os_override
    import pipeline

    ctx = ae._host_context(conn)
    ov_map = os_override.active_map(conn)
    todo: dict[tuple, int] = {}
    try:
        for f in latest_findings(conn):
            if f.get("verdict") == "fail" and (f.get("status") or "open") in ("open", "ack"):
                k = (f.get("ip"), f.get("username"))
                todo[k] = todo.get(k, 0) + 1
    except Exception:  # noqa: BLE001 - 稽核發現讀不到時不擋整張表，但標記看得出來
        todo = {}
    for d in rows:
        c = ctx.get((d.get("ip") or "").strip())
        systems = (c or {}).get("systems", [])
        live = (c or {}).get("rows", [])
        first = live[0] if live else {}
        d["system_id"] = ae._j(s["api_id"] for s in systems) or None
        d["system"] = ae._j(s["name"] for s in systems) or None
        if c:
            d["department"] = ae._j(s["ap_department"] for s in systems) or None
            d["contact"] = ae._j(s["ap_owner"] for s in systems) or None
            d["custodian"] = c.get("owner") or d.get("custodian")
            d["owner_department"] = c.get("department") or None
            if not d.get("hostname"):
                d["hostname"] = c.get("hostname")
        d["env_group"] = env_group.classify(first.get("environment")) if first else env_group.UNSET
        if first:
            auto = pipeline._os_type(first.get("os"), None, first.get("device_model"),
                                     (first.get("hostname"), first.get("asset_name")))
        else:
            auto = pipeline._os_type(" ".join(x for x in (d.get("os_id"), d.get("os_version")) if x) or None, None)
        d["os_type"] = os_override.resolve(ov_map, d.get("hostname"), d.get("ip"),
                                           first.get("asset_serial") if first else None, auto)[0]
        d["open_findings"] = todo.get((d.get("ip"), d.get("username")), 0)
        d["needs_action"] = d["open_findings"] > 0


def explain_search(conn, q: str) -> dict:
    """搜尋 0 筆時用：這個詞在資產庫對得到幾台機器、其中幾台還沒收集帳號。

    2026-09-18 自我拷問第 6 問：查「陳大文」得 0 筆，可能是他真的沒有帳號，也可能是
    **他負責的機器還沒收集帳號**。兩件事要做的完全不同，只回「查無資料」等於要人猜。
    多個詞（空白隔開）要全部符合。比對：主機名、IP、使用單位、使用者、保管者、AP ID、系統名稱。
    """
    import account_export as ae
    terms = [x.lower() for x in (q or "").split() if x.strip()]
    if not terms:
        return {"q": q, "machines": 0, "collected": 0, "not_collected": []}
    ctx = ae._host_context(conn)
    collected_ips = {r[0] for r in conn.execute("SELECT DISTINCT ip FROM host_account WHERE gone_at IS NULL")}
    hits = []
    for ip, c in ctx.items():
        hay = " ".join([ip, c.get("hostname") or "", c.get("owner") or ""]
                       + [f"{s['api_id']} {s['name']} {s['ap_department']} {s['ap_owner']}" for s in c["systems"]]
                       + [f"{r.get('usage_unit') or ''} {r.get('user_name') or ''}" for r in c["rows"]]).lower()
        if all(t in hay for t in terms):
            hits.append((ip, c.get("hostname") or ip))
    not_col = [h for ip, h in hits if ip not in collected_ips]
    return {"q": q, "machines": len(hits), "collected": len(hits) - len(not_col),
            "not_collected": sorted(not_col)[:20], "not_collected_count": len(not_col)}


def set_account_note(conn, ip: str, username: str, note: str) -> dict:
    """設一台主機上某帳號的「手動備註」（稽核人員輸入，收集不覆蓋）。"""
    cur = conn.execute(
        "UPDATE host_account SET note = ? WHERE ip = ? AND username = ?",
        (note or None, ip, username))
    conn.commit()
    if cur.rowcount == 0:
        raise ValueError("查無此帳號")
    return {"ip": ip, "username": username, "note": note}


def set_account_kind(conn, ip: str, username: str, kind: str | None) -> dict:
    """手動改一台主機上某帳號的類型（程式判錯時用，例如 CBoss 被判成內建帳號）。
    收集永不覆寫這欄（同 note）。kind 傳空字串＝清掉覆寫、回到程式判定。"""
    kind = (kind or "").strip() or None
    if kind is not None and kind not in KIND_LABEL:
        raise ValueError(f"不認得的類型：{kind}（可用：{'、'.join(KIND_LABEL)}）")
    cur = conn.execute(
        "UPDATE host_account SET kind_override = ? WHERE ip = ? AND username = ?",
        (kind, ip, username))
    conn.commit()
    if cur.rowcount == 0:
        raise ValueError("查無此帳號")
    return {"ip": ip, "username": username, "kind_override": kind}


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
    "builtin": "內建帳號", "service": "服務帳號", "domain": "網域帳號",
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
    if (a.get("os_family") or "") == "windows" and _norm_pw(a) != "locked":
        return "啟用中" if a.get("can_login") else "已停用"
    s = _norm_pw(a)
    return "已停用" if s == "locked" else ("需 root" if s is None else "啟用中")


def _is_win(a: dict) -> bool:
    return (a.get("os_family") or "") == "windows"


def _cell_sudo(a: dict) -> str:
    # Windows 沒有 sudo：對應「本機管理員」（Administrators 成員）。跟前端 mSudo 同字
    if _is_win(a):
        return "本機管理員" if a.get("is_sudoer") else "否"
    if a.get("uid") == 0:
        return "UID 0"
    if a.get("sudo_nopasswd"):
        return "是·免密碼"
    return "是" if a.get("is_sudoer") else "否"


def _cell_empty(a: dict) -> str:
    if (a.get("os_family") or "") == "windows" and _norm_pw(a) is None:
        return "不適用"
    s = _norm_pw(a)
    return "需 root" if s is None else ("是" if s == "empty" else "否")


def _cell_uid0(a: dict) -> str:
    if _is_win(a):
        return "不適用"
    if a.get("uid") == 0 and a.get("username") != "root":
        return "是"
    return "root" if a.get("uid") == 0 else "否"


def _cell_keys(a: dict) -> str:
    if _is_win(a):
        return "不適用"
    n = a.get("authorized_keys")
    if n is None:
        return "需 root"
    return f"{n} 把" if n > 0 else "無"


def _cell_login(a: dict) -> str:
    if a.get("never_logged_in"):
        return "從未登入"
    # Windows 只有走 SAMR 才知道最後登入；沒走到＝查不到，不可以寫成「有」（跟前端 mNeverLogin 同字）
    if _is_win(a) and (a.get("login_source") or "") != "samr":
        return "查不到"
    return "有"


# 匯出可選欄位：key → (標題, 取值函式)。前 5 個是身分/備註，後 7 個是狀態欄。
MATRIX_EXPORT_COLS = {
    "hostname": ("主機", lambda a: a.get("hostname") or a.get("ip") or ""),
    "ip": ("IP", lambda a: a.get("ip") or ""),
    "department": ("部門（使用單位）", lambda a: a.get("department") or "未填"),
    "contact": ("窗口（使用者）", lambda a: a.get("contact") or "未填"),
    "custodian": ("保管者", lambda a: a.get("custodian") or "未填"),
    "username": ("帳號", lambda a: a.get("username") or ""),
    # 盤點時間（2026-09-18 使用者：「盤點須加日期戳記嗎？」）——每台收集時間不同，
    # 不能只看畫面上那一行「最新盤點」；每一列都要能說出「這是哪一天的狀態」
    "collected": ("盤點時間", lambda a: a.get("last_seen") or ""),
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


def audit_summary(conn, os_bucket: str | None = None) -> dict:
    """稽核摘要：幾條不合規、幾條查不到、涵蓋率多少。

    「查不到」要單獨算並顯著呈現——它代表這份稽核報告有多少是空白的，
    把它藏起來會讓人誤以為全都查過了。

    os_bucket（linux／windows／aix／unknown）：只算那個 OS 桶的主機（帳號儀表板分頁）。
    不給＝全部。各桶加總＝全部（同一份逐台計數，只是篩不篩）。
    """
    last = conn.execute(
        "SELECT * FROM account_collect_runs WHERE status = 'ok' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not last:
        return {"has_data": False}
    buckets = host_os_buckets(conn) if os_bucket else {}

    def keep(ip) -> bool:
        return not os_bucket or buckets.get(ip, "unknown") == os_bucket

    # 數字＝下鑽筆數（2026-10-21 使用者規則）：直接數「稽核發現」分頁那份清單（latest_findings，
    # 已排除未到期的核准例外），不另外對 account_finding 整輪 COUNT——那樣會把例外也算進去，
    # 卡片比點進去的清單多。下鑽：高／中風險＝verdict=fail＋severity；查不到＝verdict=unknown。
    by_sev = {"high": 0, "medium": 0, "low": 0}
    unknown = 0
    for f in latest_findings(conn):
        if not keep(f.get("ip")):
            continue
        if f.get("verdict") == "unknown":
            unknown += 1
        elif f.get("verdict") == "fail":
            sev = f.get("severity")
            by_sev[sev] = by_sev.get(sev, 0) + 1
    # 標準管理帳號(mgmt)一律算特權：它設計上就帶 NOPASSWD:ALL，只是那份權限在
    # /etc/sudoers.d 裡、沒 root 讀不到，不能因為「目前看不到」就不當它是特權。
    acc = {"n": 0, "priv": 0, "humans": 0, "mgmt": 0}
    for r in conn.execute(
        "SELECT ip, COUNT(*) AS n, "
        f"SUM(CASE WHEN {PRIV_SQL.format(a='')} THEN 1 ELSE 0 END) AS priv, "
        "SUM(CASE WHEN kind = 'human' THEN 1 ELSE 0 END) AS humans, "
        "SUM(CASE WHEN kind = 'mgmt' THEN 1 ELSE 0 END) AS mgmt "
        "FROM host_account WHERE gone_at IS NULL GROUP BY ip"
    ):
        if keep(r["ip"]):
            for k in acc:
                acc[k] += r[k] or 0
    return {
        "has_data": True,
        "run": dict(last),
        "os_bucket": os_bucket,
        "fail_high": by_sev["high"], "fail_medium": by_sev["medium"],
        "fail_low": by_sev["low"], "unknown": unknown,
        "accounts": acc["n"], "privileged": acc["priv"],
        "humans": acc["humans"], "std_mgmt": acc["mgmt"],
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
