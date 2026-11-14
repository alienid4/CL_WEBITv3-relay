"""資料品質量測：把「盤點清單到底準不準」變成一個查得到來源的數字。

起因（2026-08-15 使用者）：「現在真實的數據可能只有六七十 % 的正確性」——但那是感覺，
沒人真的量過。沒有數字就無法證明導入這套系統之後有沒有變好，也不知道該先修哪一類。

刻意的立場：**只量「有辦法證明」的東西**。
保管者、資產用途、機密性這種純人為判斷的欄位，機器永遠驗不了，硬給一個「正確率」
是在編數字。那類欄位改用「填寫率」與「新鮮度」兩個可證明的替代指標——講清楚它衡量的
不是對錯，是「有沒有人在維護」。這比給一個看起來很專業、其實沒有根據的百分比誠實。

每個維度都能下鑽到「是哪幾台」，不能只給一個數字（見專案慣例：任何值點下去要看得到關聯）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

# 幾天內被機器「看到過」才算資料新鮮。掃描是每日排程，抓 7 天可容忍連假／排程停一週。
FRESH_DAYS = 7
# 人工欄位多久沒更新就算失去可信度。一年是資安稽核常見的複核週期。
STALE_DAYS = 365

# 純人為判斷、機器驗不了的欄位——這些只量填寫率與新鮮度，不宣稱正確率
JUDGEMENT_FIELDS = [
    ("custodian", "保管者"),
    ("user_name", "使用者"),
    ("asset_purpose", "資產用途"),
    ("confidentiality", "機密性(C)"),
    ("integrity", "完整性(I)"),
    ("availability", "可用性(A)"),
    ("inventory_department", "盤點單位-部門"),
    ("physical_location", "資產實體位置"),
]


def _cutoff(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _filled(v) -> bool:
    """有沒有填。**0 是值，不是空白**——CIA 分級、數量這些欄位存 0 很正常，
    用 `v or ''` 判斷會把 0 當成沒填，填寫率直接掉一大截（2026-08-15 實機發現：
    機密性 3641 筆存 0 被算成全部沒填）。"""
    return v is not None and str(v).strip() != ""


def _subnet_of(ip: str) -> str:
    """粗略 /24。掃描是按網段跑的，判斷「這台在不在我們掃過的範圍」用 /24 就夠。"""
    parts = (ip or "").split(".")
    return ".".join(parts[:3]) if len(parts) == 4 else ""


def _scanned_subnets(conn: sqlite3.Connection) -> set[str]:
    """歷來掃描過的網段。用來區分「掃了但找不到」與「根本沒掃過那段」——
    這兩件事在資料品質上的意義完全相反，混在一起算會得到一個假的低分。"""
    out = {
        _subnet_of(r["ip"])
        for r in conn.execute("SELECT DISTINCT ip FROM scan_history WHERE scan_ok = 1")
        if r["ip"]
    }
    out |= {
        _subnet_of(r["ip"])
        for r in conn.execute("SELECT DISTINCT ip FROM host_service")
        if r["ip"]
    }
    out.discard("")
    return out


def _active_assets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """量測母體＝非退役資產。已報廢的機器對不上機器事實是正常的，
    算進去只會讓分數難看又沒有行動意義。"""
    import manage_state as ms

    ph = ",".join("?" for _ in ms.RETIRED_STATUS)
    return conn.execute(
        f"SELECT * FROM hardware WHERE COALESCE(asset_status,'') NOT IN ({ph})",
        tuple(ms.RETIRED_STATUS),
    ).fetchall()


def _seen_ips(conn: sqlite3.Connection) -> set[str]:
    """近 FRESH_DAYS 天被掃描或服務採集看到過的 IP。"""
    cut = _cutoff(FRESH_DAYS)
    ips = {
        r["ip"]
        for r in conn.execute(
            "SELECT DISTINCT ip FROM scan_history WHERE scan_time >= ? AND scan_ok = 1", (cut,)
        )
        if r["ip"]
    }
    ips |= {
        r["ip"]
        for r in conn.execute(
            "SELECT DISTINCT ip FROM host_service WHERE last_seen >= ? OR first_seen >= ?",
            (cut, cut),
        )
        if r["ip"]
    }
    return ips


def _collected_os(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """機器自己講的 OS（帳號盤點時一併收的 os_id/os_version）。IP → 證據清單（新到舊）。

    刻意用 host_account 這張表而不是 hardware.os：後者會被採集流程直接覆蓋
    （見 manage_state.FACT_FIELDS），拿它跟自己比永遠 100%，量了等於沒量。

    Codex 1155／1157：以前是 {ip: 字串}、逐列覆蓋——同一 IP 有兩筆證據時「最後讀到的」贏，
    結果隨寫入順序變；而且不看序號，IP 被重新分配後會拿舊機器的帳號資料來比新資產。
    現在保留每筆證據的序號與時間，由 _os_evidence 依資產身分挑最新的一筆。
    """
    out: dict[str, list[dict]] = {}
    for r in conn.execute(
        "SELECT ip, asset_serial, os_id, os_version, COALESCE(last_seen, first_seen, '') AS seen, id "
        "FROM host_account WHERE os_id IS NOT NULL AND TRIM(os_id) != '' "
        "ORDER BY seen DESC, id DESC"
    ):
        ip = (r["ip"] or "").strip()
        if not ip:
            continue
        out.setdefault(ip, []).append({
            "os": f"{r['os_id']} {r['os_version'] or ''}".strip(),
            "asset_serial": (r["asset_serial"] or "").strip() or None,
            "seen": r["seen"] or None,
        })
    return out


def _ip_machine_counts(assets) -> dict[str, int]:
    """IP → 目前（非退役）掛在這個 IP 上的機器數，同一台用 system_stats.machine_key 去重（全站唯一一把尺）。"""
    import system_stats

    keys: dict[str, set[str]] = {}
    for a in assets:
        ip = (a["ip"] or "").strip()
        if ip:
            keys.setdefault(ip, set()).add(system_stats.machine_key(a["hostname"], a["ip"], a["asset_serial"]))
    return {ip: len(ks) for ip, ks in keys.items()}


def _os_evidence(collected: dict[str, list[dict]], a, ip_machines: dict[str, int] | None = None) -> dict | None:
    """這台資產可採用的 OS 證據：同 IP、且證據有記序號時序號要相同（Codex 1155：IP 換人用，
    舊機器的帳號資料不算新資產的證據）；多筆取最新（Codex 1157：結果不隨寫入順序變）。
    證據沒記序號（NULL）時認不出是哪一台的：只有這個 IP 目前只對到一台機器（machine_key 去重後）才採用；
    同 IP 掛兩台以上＝不知道是誰的，當作沒有證據（不知道 ≠ 不一致，不進分母）。ip_machines 由 _ip_machine_counts 算；
    沒給（舊呼叫端）＝不知道幾台，NULL 序號一律不採用（從嚴）。
    審查 P3（2.065.2）：沒記序號的證據，時間早於這台資產的登記時間（hardware.created_at，DB 預設本地時間、
    update_hardware 不准改）→ 不採用：IP 剛換手時舊機器已退役（不算台數，所以 IP 只剩新機一台），
    舊機器留下的帳號資料不可套到新機。created_at 或證據時間任一是空的＝比不出先後，照原規則採用
    （account_inventory 寫入一定帶 last_seen／first_seen，正式資料不會缺；只有極舊資料可能缺 created_at）。
    證據時效（多久以前的算過期）口徑未定，這裡不擋，只把採用證據的時間帶出去給下鑽看。"""
    ip = (a["ip"] or "").strip()
    serial = (a["asset_serial"] or "").strip()
    unique_ip = ip_machines is not None and ip_machines.get(ip, 0) == 1
    registered = _norm_ts(_row_get(a, "created_at"))
    for ev in collected.get(ip, ()):
        if ev["asset_serial"] == serial:
            return ev
        if ev["asset_serial"] is None and unique_ip:
            seen = _norm_ts(ev["seen"])
            if registered and seen and seen < registered:
                continue
            return ev
    return None


def _row_get(row, key: str):
    """sqlite3.Row／dict 都能用；沒有這個欄位回 None（測試或舊呼叫端可能給不完整的列）。"""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _norm_ts(v) -> str | None:
    """時間字串統一成 'YYYY-MM-DD HH:MM:SS' 前綴才能直接比大小（ISO 的 T 換成空白、去掉小數秒／時區尾巴）。"""
    s = (v or "").strip() if isinstance(v, str) else ""
    if not s:
        return None
    return s.replace("T", " ")[:19]


def _os_matches(declared: str, observed: str) -> bool:
    """OS 字串比對放寬：「RHEL 9.6」vs「rocky 9.6」不同，但「Red Hat Enterprise Linux 9.6」
    vs「rhel 9.6」是同一件事。比對發行版關鍵字＋主版號就夠——要求完全一致只會製造
    一整頁沒有行動意義的假不符。"""
    d, o = declared.lower(), observed.lower()
    families = [
        ("rhel", ("rhel", "red hat", "redhat")),
        ("rocky", ("rocky",)),
        ("centos", ("centos",)),
        ("ubuntu", ("ubuntu",)),
        ("debian", ("debian",)),
        ("suse", ("suse", "sles")),
        ("windows", ("windows", "microsoft")),
        ("aix", ("aix",)),
    ]
    def fam(s: str) -> str | None:
        for name, keys in families:
            if any(k in s for k in keys):
                return name
        return None

    if fam(d) is None or fam(o) is None or fam(d) != fam(o):
        return False

    def major(s: str) -> str | None:
        import re
        m = re.search(r"(\d+)", s)
        return m.group(1) if m else None

    md, mo = major(d), major(o)
    return md is None or mo is None or md == mo


def measure(conn: sqlite3.Connection) -> dict:
    """算出各維度的分數與不合格清單摘要。回傳結構給畫面直接用。"""
    assets = _active_assets(conn)
    total = len(assets)
    seen = _seen_ips(conn)
    collected_os = _collected_os(conn)
    ip_machines = _ip_machine_counts(assets)
    stale_cut = _cutoff(STALE_DAYS)

    dims: list[dict] = []
    subnets = _scanned_subnets(conn)

    # ① 掃描涵蓋率：這是「我們有沒有證據」，不是「資料對不對」，所以不計入正確率分數。
    # 沒有這一層的話，公司網段從來沒掃過會被算成「幾千台資料都錯」——那是假的低分，
    # 真正的問題是「還沒去掃」（2026-08-15 實機發現：221 只掃自己所在的那一個網段，
    # 4194 筆資產都在 10.99.x，總分被算成 3.9%）。
    in_use = [a for a in assets if (a["asset_status"] or "") == "使用中"]
    covered = [a for a in in_use if _subnet_of((a["ip"] or "").strip()) in subnets]
    dims.append({
        "key": "coverage",
        "label": "使用中資產落在掃描涵蓋的網段內",
        "kind": "coverage",
        "checked": len(in_use),
        "ok": len(covered),
        "note": "沒掃過的網段不是資料錯，是還沒去驗；這格低就代表下面的分數只代表一小部分資產",
    })

    # ② 機器看得到嗎：**只算掃描涵蓋範圍內的**，沒掃過的網段不列入分母
    reachable = [a for a in covered if (a["ip"] or "").strip() in seen]
    dims.append({
        "key": "reachable",
        "label": f"涵蓋範圍內的使用中資產近 {FRESH_DAYS} 天有被看到",
        "kind": "verifiable",
        "checked": len(covered),
        "ok": len(reachable),
        "note": "在有掃的網段裡卻連續一週掃不到，不是機器關了就是清單過期",
    })

    # ③ 狀態矛盾：標成退役（停用/報廢/閒置）卻還活著
    import manage_state as ms

    ph = ",".join("?" for _ in ms.RETIRED_STATUS)
    retired_all = conn.execute(
        f"SELECT * FROM hardware WHERE COALESCE(asset_status,'') IN ({ph})",
        tuple(ms.RETIRED_STATUS),
    ).fetchall()
    # Codex 1151：沒有觀測（沒 IP、或所在網段從沒掃過）不是「已驗證不在線」，不能算符合、不進分母——
    # 跟 ② 一樣只算掃描涵蓋範圍內的。在線的（seen）一定落在涵蓋網段，所以下鑽仍是分母的子集。
    retired = [a for a in retired_all if _subnet_of((a["ip"] or "").strip()) in subnets]
    ghosts = [a for a in retired if (a["ip"] or "").strip() in seen]
    dims.append({
        "key": "retired_alive",
        "label": "退役資產確實已經不在線上",
        "kind": "verifiable",
        "checked": len(retired),
        "ok": len(retired) - len(ghosts),
        "note": "標成停用/報廢卻還在跑——不是狀態錯，就是機器該關沒關（資安風險）",
    })

    # ③ OS 一致性：清單寫的 vs 機器自己講的
    os_checked = [a for a in assets if _os_evidence(collected_os, a, ip_machines) and (a["os"] or "").strip()]
    os_ok = [a for a in os_checked if _os_matches(a["os"], _os_evidence(collected_os, a, ip_machines)["os"])]
    dims.append({
        "key": "os_match",
        "label": "作業系統與機器實際回報一致",
        "kind": "verifiable",
        "checked": len(os_checked),
        "ok": len(os_ok),
        "note": "只算收得到機器回報的那些；收不到的不列入分母（不知道 ≠ 錯）",
    })

    # ⑤ 人工欄位填寫率：不宣稱對錯，只講「有沒有人填」
    for field, label in JUDGEMENT_FIELDS:
        filled = [a for a in assets if _filled(a[field])]
        dims.append({
            "key": f"filled_{field}",
            "label": f"{label} 有填寫",
            "kind": "filled",
            "checked": total,
            "ok": len(filled),
            "note": "機器驗不了對錯，只能看有沒有人維護",
        })

    # ⑦ 識別完整（2026-09-18 十項盤點第 8 條）：主機名或 IP 缺一，machine_key 會退回序號
    # 各算一台——認不出是不是同一台、也永遠對不到其他來源（DY／vCenter／掃描）。
    # 221：缺 IP 310 筆、缺主機名 216 筆。這是「缺資料」不是「資料錯」，不併入分數；
    # 列出來給清冊維護單位補（點進去可看是哪幾台）。
    ident_ok = [a for a in assets if _identity_complete(a)]
    dims.append({
        "key": "identity",
        "label": "主機名與 IP 都有填（才認得出是哪一台）",
        "kind": "filled",
        "checked": total,
        "ok": len(ident_ok),
        "note": "缺一個就無法判斷是不是同一台，台數會偏多、也對不到 DY／vCenter／掃描——請回頭補 CIA 清冊",
    })

    # ⑧ 同名不同 IP（2026-09-18 十項盤點第 7 條）：同一個主機名登記了好幾個 IP，會被算成好幾台。
    # 可能是一台多 IP（業務口＋管理口、叢集）→ 台數高估；也可能是不同設備同名（型號當主機名、N/A）
    # → 分開才對。系統分不出來，**只列出來給人確認，不自動合併**。221：110 個名字、最多高估 162 台。
    multi = _same_name_multi_ip(assets)
    named = [a for a in assets if (a["hostname"] or "").strip()]
    dims.append({
        "key": "same_name_multi_ip",
        "label": "主機名沒有同時登記好幾個 IP（否則會被算成好幾台）",
        "kind": "filled",
        "checked": len(named),
        "ok": len(named) - sum(1 for a in named if _hn(a) in multi),
        "note": "可能是一台多 IP（台數高估），也可能是不同設備同名（型號、N/A 當主機名）——系統分不出來，只列出來給人確認，不自動合併",
    })

    # ⑨ 資產狀態是合法值（2026-10-21）：CIA 來源檔欄位錯位時，資產狀態欄會出現「1」「A02櫃」；
    # 匯入時已擋下不寫，這裡把「上次匯入被擋的」＋「庫裡本來就不合法的」列出來給清冊窗口改。
    cia_rows, status_bad = _status_offenders(conn)
    dims.append({
        "key": "status_valid",
        "label": "CIA 資產狀態是合法值（使用中／停用／報廢／閒置）",
        "kind": "filled",
        "checked": cia_rows,
        "ok": cia_rows - len(status_bad),
        "note": "不合法的值（例如「1」「A02櫃」）多半是來源檔欄位錯位；匯入時不寫入、保留原值，請 CIA 窗口更正清冊後重匯",
    })

    # ⑥ 新鮮度：用 manual_updated_at（人動過才寫）而不是 updated_at。
    # updated_at 每次自動匯入都會刷新，用它算出來的新鮮度是 100%，但那只代表
    # 「匯入跑過」不代表「有人在維護」——2026-08-15 自我檢查抓到的假指標。
    fresh = [a for a in assets if (a["manual_updated_at"] or "") >= stale_cut]
    dims.append({
        "key": "fresh",
        "label": f"近 {STALE_DAYS} 天內有人工維護過",
        "kind": "freshness",
        "checked": total,
        "ok": len(fresh),
        "note": "只算「人」改過的（自動匯入不算）；從沒人動過的資料，內容再完整也難主張它還對",
    })

    for d in dims:
        d["bad"] = d["checked"] - d["ok"]
        d["rate"] = round(d["ok"] / d["checked"] * 100, 1) if d["checked"] else None

    # 總分只用「可驗證」維度算——把填寫率或涵蓋率混進來會讓分數失去意義：
    # 前者是沒有證據的猜測，後者衡量的是「我們掃了多少」而不是「資料對不對」。
    verifiable = [d for d in dims if d["kind"] == "verifiable" and d["checked"]]
    checked_sum = sum(d["checked"] for d in verifiable)
    ok_sum = sum(d["ok"] for d in verifiable)
    coverage = next((d for d in dims if d["key"] == "coverage"), None)
    return {
        "asset_total": total,
        "score": round(ok_sum / checked_sum * 100, 1) if checked_sum else None,
        # 分數是幾筆證據算出來的一定要講。100% 但只驗了 143 筆，跟 100% 驗了 4000 筆，
        # 是完全不同的兩件事——只給百分比會讓人把前者當成後者。
        "score_sample": checked_sum,
        "score_basis": "只計可用機器事實驗證的維度；掃描涵蓋率與人工欄位填寫率另計，不併入分數",
        "coverage_rate": coverage["rate"] if coverage else None,
        "fresh_days": FRESH_DAYS,
        "stale_days": STALE_DAYS,
        "dimensions": dims,
    }


def _hn(a) -> str:
    """比對用主機名：去網域（同 machine_key 的規則）、小寫。"""
    import system_stats
    return system_stats.strip_domain(a["hostname"]).lower()


def _same_name_multi_ip(assets) -> dict[str, set[str]]:
    """主機名（去網域、小寫）→ 登記過的 IP 集合；只回有 2 個以上 IP 的。"""
    by: dict[str, set[str]] = {}
    for a in assets:
        h, ip = _hn(a), (a["ip"] or "").strip()
        if h and ip and ip != "0.0.0.0":
            by.setdefault(h, set()).add(ip)
    return {h: ips for h, ips in by.items() if len(ips) > 1}


def _status_offenders(conn: sqlite3.Connection) -> tuple[int, list[dict]]:
    """(CIA 資產筆數, 資產狀態不合法的清單)。measure 與 list_offenders 共用這一份（加總＝下鑽）。

    兩種來源，以序號去重：
      1. 上次 CIA 匯入被擋下的值（app_settings，匯入器寫）——庫裡保留的是原值，看不出來；
      2. 庫裡 CIA 資產的資產狀態本身不合法或空白。
    只算目前還在 hardware 的 CIA 序號（DYN-／VC-… 帳外資產沒有 CIA 狀態，本來就該空白）。
    """
    import json

    import lifecycle

    rows = [r for r in conn.execute("SELECT * FROM hardware")
            if not str(r["asset_serial"] or "").startswith(lifecycle.NON_CIA_PREFIXES)]
    by_serial = {r["asset_serial"]: r for r in rows}
    out: dict[str, dict] = {}
    try:
        raw = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                           (lifecycle.INVALID_STATUS_SETTING,)).fetchone()
        last = json.loads(raw["value"]) if raw and raw["value"] else []
    except (sqlite3.Error, ValueError):
        last = []   # 設定壞掉不擋整頁；第 2 種來源照算
    for item in last:
        a = by_serial.get(item.get("asset_serial"))
        if a is not None and a["asset_serial"] not in out:
            out[a["asset_serial"]] = (a, f"上次 CIA 匯入資產狀態是「{item.get('value')}」（未寫入，"
                                         f"系統保留「{a['asset_status'] or '空白'}」）")
    for a in rows:
        st = (a["asset_status"] or "").strip()
        if a["asset_serial"] in out or st in lifecycle.VALID_STATUS_VALUES:
            continue
        out[a["asset_serial"]] = (a, f"資產狀態「{st}」不是合法值" if st else "CIA 資產沒有資產狀態")
    return len(rows), list(out.values())


def _identity_complete(a) -> bool:
    """主機名與 IP 都有（IP 不是 0.0.0.0 佔位）——跟 system_stats.machine_key 的判準一致。"""
    h, ip = (a["hostname"] or "").strip(), (a["ip"] or "").strip()
    return bool(h) and bool(ip) and ip != "0.0.0.0"


def list_offenders(conn: sqlite3.Connection, dim_key: str) -> list[dict]:
    """某個維度「不合格的是哪幾台」——數字一定要能下鑽，不然沒有行動意義。"""
    assets = _active_assets(conn)
    seen = _seen_ips(conn)
    collected_os = _collected_os(conn)
    ip_machines = _ip_machine_counts(assets)

    def base(a: sqlite3.Row, reason: str) -> dict:
        return {
            "asset_serial": a["asset_serial"], "hostname": a["hostname"], "ip": a["ip"],
            "asset_status": a["asset_status"], "os": a["os"],
            "inventory_department": a["inventory_department"], "custodian": a["custodian"],
            "updated_at": a["updated_at"], "reason": reason,
        }

    subnets = _scanned_subnets(conn)

    if dim_key == "coverage":
        return [
            base(a, f"IP 所在網段從來沒被掃描過（{_subnet_of((a['ip'] or '').strip()) or '沒有 IP'}）")
            for a in assets
            if (a["asset_status"] or "") == "使用中"
            and _subnet_of((a["ip"] or "").strip()) not in subnets
        ]

    if dim_key == "reachable":
        return [
            base(a, f"近 {FRESH_DAYS} 天掃不到")
            for a in assets
            if (a["asset_status"] or "") == "使用中"
            and _subnet_of((a["ip"] or "").strip()) in subnets
            and (a["ip"] or "").strip() not in seen
        ]

    if dim_key == "retired_alive":
        import manage_state as ms

        ph = ",".join("?" for _ in ms.RETIRED_STATUS)
        rows = conn.execute(
            f"SELECT * FROM hardware WHERE COALESCE(asset_status,'') IN ({ph})",
            tuple(ms.RETIRED_STATUS),
        ).fetchall()
        return [base(a, f"標「{a['asset_status']}」卻仍在線上") for a in rows
                if (a["ip"] or "").strip() in seen
                and _subnet_of((a["ip"] or "").strip()) in subnets]

    if dim_key == "os_match":
        out = []
        for a in assets:
            ev = _os_evidence(collected_os, a, ip_machines)
            if ev is None or not (a["os"] or "").strip():
                continue
            observed = ev["os"]
            if not _os_matches(a["os"], observed):
                out.append(base(a, f"清單寫「{a['os']}」，機器回報「{observed}」"
                                   f"（{ev['seen'] or '時間不明'}）"))
        return out

    if dim_key.startswith("filled_"):
        field = dim_key[len("filled_"):]
        if field not in [f for f, _ in JUDGEMENT_FIELDS]:
            return []
        return [base(a, "沒有填寫") for a in assets if not _filled(a[field])]

    if dim_key == "identity":
        out = []
        for a in assets:
            if _identity_complete(a):
                continue
            h, ip = (a["hostname"] or "").strip(), (a["ip"] or "").strip()
            why = ("主機名與 IP 都沒有" if not h and not ip
                   else "沒有 IP（或是 0.0.0.0 佔位）" if not ip or ip == "0.0.0.0" else "沒有主機名")
            out.append(base(a, why))
        return out

    if dim_key == "same_name_multi_ip":
        multi = _same_name_multi_ip(assets)
        out = []
        for a in assets:
            ips = multi.get(_hn(a))
            if ips:
                others = sorted(ips - {(a["ip"] or "").strip()})
                out.append(base(a, f"主機名「{_hn(a)}」共登記 {len(ips)} 個 IP，另有：{'、'.join(others[:6])}"
                                   + ("…" if len(others) > 6 else "")))
        return sorted(out, key=lambda r: ((r["hostname"] or "").lower(), r["ip"] or ""))

    if dim_key == "status_valid":
        _, bad = _status_offenders(conn)
        return [base(a, why) for a, why in bad]

    if dim_key == "fresh":
        cut = _cutoff(STALE_DAYS)
        return [
            base(a, f"人工最後維護 {a['manual_updated_at'] or '從來沒有人在系統裡改過'}")
            for a in assets if (a["manual_updated_at"] or "") < cut
        ]

    return []


# ===== 人名合併（打錯字修正）=====
# 只出現一次的人名常是把「李太明」打成「李太名」。這裡把錯名併到正確名——
# 但**先預覽會改幾筆**再讓人確認才寫（2026-09-19 使用者要「預覽版」）。
# 人名散在三處：hardware.user_name、hardware.custodian、personnel.person_name。
# 一律 TRIM 後精確比對，不做模糊比對——模糊併錯比不併更糟。
_PERSON_NAME_FIELDS = (
    ("hardware", "user_name", "使用者"),
    ("hardware", "custodian", "保管者"),
    ("personnel", "person_name", "人員名冊"),
)


def preview_person_merge(conn: sqlite3.Connection, from_name: str, to_name: str) -> dict:
    """預覽：把 from_name 併成 to_name 會動到哪些欄位、各幾筆。不寫入。"""
    from_name = (from_name or "").strip()
    to_name = (to_name or "").strip()
    if not from_name or not to_name:
        raise ValueError("原名與正確名都要填")
    if from_name == to_name:
        raise ValueError("原名與正確名相同，不用合併")
    fields = []
    total = 0
    for table, col, label in _PERSON_NAME_FIELDS:
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE TRIM(COALESCE({col},'')) = ?", (from_name,)
        ).fetchone()[0]
        if n:
            fields.append({"table": table, "column": col, "label": label, "count": n})
            total += n
    # 抓幾筆 hardware 樣本（序號＋主機名）讓人確認改對了機器
    sample = [
        {"asset_serial": r["asset_serial"], "hostname": r["hostname"], "field": r["field"]}
        for r in conn.execute(
            "SELECT asset_serial, hostname, '使用者' AS field FROM hardware "
            "WHERE TRIM(COALESCE(user_name,'')) = ? "
            "UNION ALL SELECT asset_serial, hostname, '保管者' FROM hardware "
            "WHERE TRIM(COALESCE(custodian,'')) = ? LIMIT 20",
            (from_name, from_name),
        ).fetchall()
    ]
    return {"from_name": from_name, "to_name": to_name, "fields": fields,
            "total": total, "sample": sample,
            "preview_token": _merge_token(conn, from_name, to_name)}


class MergePreviewStale(Exception):
    """執行時的候選跟預覽時不一樣（Codex 1145／1149）。路由回 409，請重新預覽。"""


def _merge_token(conn: sqlite3.Connection, from_name: str, to_name: str) -> str:
    """預覽代號＝這次會改到「哪幾筆」的指紋（不只筆數）。

    Codex 1145：預覽列資產甲，中途同名搬到乙，筆數欄位都沒變，執行卻改到乙——只比筆數抓不到。
    所以把每一筆目標（表、欄、列 id）都算進去；換了任何一筆，代號就變。"""
    import hashlib

    ids = []
    for table, col, _label in _PERSON_NAME_FIELDS:
        # 審查 P3：指紋用 (rowid, asset_serial)——rowid 可能被刪後重用（VACUUM／刪最後一列再新增），
        # 加上序號才認得出「同一個 rowid 已經是別台」。
        rows = conn.execute(
            f"SELECT rowid, asset_serial FROM {table} WHERE TRIM(COALESCE({col},'')) = ? ORDER BY rowid",
            (from_name,)
        ).fetchall()
        ids.append([table, col, [[r[0], r[1]] for r in rows]])
    raw = json.dumps([from_name, to_name, ids], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def apply_person_merge(conn: sqlite3.Connection, from_name: str, to_name: str,
                       preview_token: str | None = None) -> dict:
    """實際把 from_name 併成 to_name（TRIM 後精確相等的才改）。回各欄改了幾筆。

    Codex 1145／1149：一定要帶預覽拿到的 preview_token，而且候選（哪幾筆）要跟預覽時一樣才寫；
    沒預覽直接送、或預覽後候選換了 → MergePreviewStale（409，請重新預覽）。
    比對與寫入在同一個寫入鎖（BEGIN IMMEDIATE）裡，比對完到寫入之間別人改不了；任一欄寫失敗整筆復原。"""
    from_name = (from_name or "").strip()
    to_name = (to_name or "").strip()
    if not from_name or not to_name:
        raise ValueError("原名與正確名都要填")
    if from_name == to_name:
        raise ValueError("原名與正確名相同，不用合併")
    if not preview_token:
        raise MergePreviewStale("請先預覽，確認會改到哪幾筆後再執行")
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        if _merge_token(conn, from_name, to_name) != preview_token:
            raise MergePreviewStale("預覽之後資料有變動（要改的筆數或對象不同了），請重新預覽")
        updated = {}
        total = 0
        for table, col, label in _PERSON_NAME_FIELDS:
            cur = conn.execute(
                f"UPDATE {table} SET {col} = ? WHERE TRIM(COALESCE({col},'')) = ?",
                (to_name, from_name),
            )
            if cur.rowcount:
                updated[label] = cur.rowcount
                total += cur.rowcount
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {"from_name": from_name, "to_name": to_name, "updated": updated, "total": total}
