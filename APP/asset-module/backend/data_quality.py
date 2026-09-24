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


def _collected_os(conn: sqlite3.Connection) -> dict[str, str]:
    """機器自己講的 OS（帳號盤點時一併收的 os_id/os_version）。

    刻意用 host_account 這張表而不是 hardware.os：後者會被採集流程直接覆蓋
    （見 manage_state.FACT_FIELDS），拿它跟自己比永遠 100%，量了等於沒量。
    """
    out: dict[str, str] = {}
    for r in conn.execute(
        "SELECT ip, os_id, os_version FROM host_account "
        "WHERE os_id IS NOT NULL AND TRIM(os_id) != ''"
    ):
        out[r["ip"]] = f"{r['os_id']} {r['os_version'] or ''}".strip()
    return out


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
    stale_cut = _cutoff(STALE_DAYS)

    dims: list[dict] = []
    subnets = _scanned_subnets(conn)

    # ① 掃描涵蓋率：這是「我們有沒有證據」，不是「資料對不對」，所以不計入正確率分數。
    # 沒有這一層的話，公司網段從來沒掃過會被算成「幾千台資料都錯」——那是假的低分，
    # 真正的問題是「還沒去掃」（2026-08-15 實機發現：221 只掃 192.168.1.0/24，
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
    retired = conn.execute(
        f"SELECT * FROM hardware WHERE COALESCE(asset_status,'') IN ({ph})",
        tuple(ms.RETIRED_STATUS),
    ).fetchall()
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
    os_checked = [a for a in assets if (a["ip"] or "").strip() in collected_os and (a["os"] or "").strip()]
    os_ok = [a for a in os_checked if _os_matches(a["os"], collected_os[a["ip"].strip()])]
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


def _identity_complete(a) -> bool:
    """主機名與 IP 都有（IP 不是 0.0.0.0 佔位）——跟 system_stats.machine_key 的判準一致。"""
    h, ip = (a["hostname"] or "").strip(), (a["ip"] or "").strip()
    return bool(h) and bool(ip) and ip != "0.0.0.0"


def list_offenders(conn: sqlite3.Connection, dim_key: str) -> list[dict]:
    """某個維度「不合格的是哪幾台」——數字一定要能下鑽，不然沒有行動意義。"""
    assets = _active_assets(conn)
    seen = _seen_ips(conn)
    collected_os = _collected_os(conn)

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
                if (a["ip"] or "").strip() in seen]

    if dim_key == "os_match":
        out = []
        for a in assets:
            ip = (a["ip"] or "").strip()
            if ip not in collected_os or not (a["os"] or "").strip():
                continue
            observed = collected_os[ip]
            if not _os_matches(a["os"], observed):
                out.append(base(a, f"清單寫「{a['os']}」，機器回報「{observed}」"))
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
            "total": total, "sample": sample}


def apply_person_merge(conn: sqlite3.Connection, from_name: str, to_name: str) -> dict:
    """實際把 from_name 併成 to_name（TRIM 後精確相等的才改）。回各欄改了幾筆。"""
    pv = preview_person_merge(conn, from_name, to_name)   # 也負責參數檢查
    from_name, to_name = pv["from_name"], pv["to_name"]
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
    return {"from_name": from_name, "to_name": to_name, "updated": updated, "total": total}
