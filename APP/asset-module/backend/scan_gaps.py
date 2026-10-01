"""掃不到的網段清單（2026-09-20）。

使用者在公司 198.14 驗收時提出的限制：**這台只連得到非正式區，而且非正式區也有些防火牆沒申請到**。
所以「掃不到」不可以一律當成「機器不在」——它有三種完全不同的解釋，處置也不同：

  A. 掃描機根本沒跑到這段（沒有任何涵蓋紀錄）        → 去看排程有沒有跑完、規則有沒有納入
  B. 跑了但那一段掃描失敗（涵蓋紀錄 ok=0）            → 看錯誤：路由不通、權限、逾時
  C. 那一段跑完了，但一台都沒回應                      → 防火牆擋掉／路由不通／真的沒機器／只開別的埠

這張表就是把這三種分開，並且附上「那段 CIA 上登記了幾台」——
**有登記資產卻一台都掃不到的網段，就是申請防火牆的第一順位**。

唯讀，不改任何資料；重跑結果一樣。
"""
from __future__ import annotations

import ipaddress

import db
import system_stats

# 分類（順序＝畫面呈現與排序用）
NEVER = "從沒掃過"
FAILED = "掃描失敗"
NO_REPLY = "這段跑完了，但沒有任何回應"
STALE = "最近一次沒掃到這段"
OK = "正常"

ADVICE = {
    NEVER: "這段一次涵蓋紀錄都沒有：先確認排程有跑完、規則有把它納入；掃描機到不了也算這類",
    FAILED: "最近一次掃這段失敗：看失敗原因（路由不通／權限／逾時），不是機器不在",
    NO_REPLY: "掃描程序跑完這段、沒有任何位址回應。對掃描器來說這四種長得一樣："
              "①防火牆整段擋掉 ②路由根本不通 ③那段真的沒有機器 ④機器只開別的埠且 ICMP 被擋。"
              "那段 CIA 上有登記資產的話，③幾乎可以排除 → 多半要申請防火牆開通",
    STALE: "以前掃通過、最近一次沒掃到：先看排程是不是沒跑完（時間窗不夠）",
    OK: "正常：最近一次掃到這段，也有主機回應",
}


# 這台掃描機看得到哪些環境（2026-09-20 使用者：「正式區本來就不會通，從 198.14 這台掃不到正式區是正常的」）。
# 沒設＝不知道，全部一起列（不替人假設）；設了才把「預期不通」跟「該通卻不通」分開。
VISIBLE_SETTING = "scanner_visible_environments"
EXPECTED = "預期不通（這台看不到這個環境）"


def visible_environments(conn) -> list[str]:
    raw = db.get_setting(conn, VISIBLE_SETTING, "") or ""
    return [x for x in (s.strip() for s in raw.split(",")) if x]


def set_visible_environments(conn, envs: list[str]) -> list[str]:
    clean = [str(e).strip() for e in (envs or []) if str(e).strip()]
    db.set_setting(conn, VISIBLE_SETTING, ",".join(clean))
    return clean


# ===== 人工標註（2026-09-20 使用者：「我人工設定即可」「平常這些都是不用顯示出來的」）=====
#
# 掃描只講得出「掃到幾台」，講不出「這段本來就該有幾台」「這段根本還沒在用」。
# 那是人才知道的事，所以開一張人工標註表：標了之後那段預設收起來，畫面只留還要處理的。
# ⚠️ 標註**不覆蓋事實**：掃到幾台、最後掃通時間照舊顯示，人只是補上判斷與備註（附誰標的、何時）。
UNUSED = "unused"          # 已開通但目前沒在使用（網路組先開好、裡面還沒有機器）
POLICY = "policy"          # 政策不掃（例：DMZ，內網不得主動連線）
PENDING = "pending"        # 等開通（已知該通、還在申請）
WATCH = "watch"            # 盯著（先不處理，但別收起來）
NOTE_LABEL = {UNUSED: "目前沒在使用", POLICY: "政策不掃", PENDING: "等開通", WATCH: "盯著"}
# 標了這幾種＝不用每天看到（WATCH 例外，那是刻意要留在畫面上的）
HIDDEN_BY_DEFAULT = (UNUSED, POLICY)

_NOTE_DDL = """
CREATE TABLE IF NOT EXISTS scan_segment_note (
    cidr           TEXT PRIMARY KEY,
    status         TEXT,              -- unused / policy / pending / watch，空＝清除標註
    expected_hosts INTEGER,           -- 人填的「這段預計有幾台」，掃到數少於它就看得出缺口
    note           TEXT,
    updated_by     TEXT,
    updated_at     TEXT
)
"""


def _ensure_notes(conn) -> None:
    conn.execute(_NOTE_DDL)


def notes(conn) -> dict[str, dict]:
    try:
        _ensure_notes(conn)
        return {r["cidr"]: dict(r) for r in conn.execute("SELECT * FROM scan_segment_note")}
    except Exception:  # noqa: BLE001 - 唯讀庫：當作沒有標註，不擋整頁
        return {}


def set_note(conn, cidr: str, status: str = "", expected_hosts=None, note: str = "",
             by: str | None = None) -> dict:
    """設定或清除一段的人工標註。status 空字串＝清除（連同預計台數與備註一起清掉）。"""
    if status and status not in NOTE_LABEL:
        raise ValueError(f"不認得的標註：{status}（可選：{'、'.join(NOTE_LABEL)}）")
    if not (cidr or "").strip():
        raise ValueError("要指定網段")
    _ensure_notes(conn)
    if not status:
        conn.execute("DELETE FROM scan_segment_note WHERE cidr = ?", (cidr,))
        conn.commit()
        return {"cidr": cidr, "status": "", "cleared": True}
    exp = None
    if expected_hosts not in (None, ""):
        exp = max(0, int(expected_hosts))
    import datetime

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO scan_segment_note (cidr, status, expected_hosts, note, updated_by, updated_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(cidr) DO UPDATE SET status = excluded.status, "
        "expected_hosts = excluded.expected_hosts, note = excluded.note, "
        "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
        (cidr, status, exp, (note or "").strip(), by, now))
    conn.commit()
    return {"cidr": cidr, "status": status, "expected_hosts": exp, "note": (note or "").strip(),
            "updated_by": by, "updated_at": now}


def _net(cidr):
    try:
        return ipaddress.ip_network(cidr, strict=False)
    except (ValueError, TypeError):
        return None


def _seg_of(ip, nets):
    """這個 IP 落在哪一段（沒有就 None）。nets 是 [(cidr, network)]。"""
    try:
        a = ipaddress.ip_address(str(ip).strip())
    except (ValueError, TypeError):
        return None
    for cidr, n in nets:
        if a in n:
            return cidr
    return None


def report(conn, only_in_scope: bool = True) -> dict:
    """每個網段：掃到過沒有、什麼時候、那段登記了幾台、該做什麼。

    only_in_scope=True 只看「規則說要掃的」——沒要掃的段掃不到是本來就知道的事，
    混進來會把真正要申請防火牆的段淹掉。
    """
    import scan_scope

    scope = scan_scope.list_scope(conn)["items"]
    latest = conn.execute("SELECT MAX(scan_time) FROM scan_history").fetchone()[0]
    visible = visible_environments(conn)
    marks = notes(conn)

    # 涵蓋紀錄：一次 GROUP BY 算完（443 段逐段查會很慢）
    cov_ok, cov_fail = {}, {}
    try:
        for cidr, n, last in conn.execute(
                "SELECT cidr, COUNT(*), MAX(scan_time) FROM scan_coverage WHERE ok = 1 GROUP BY cidr"):
            cov_ok[cidr] = {"times": n, "last": last}
        for cidr, n, last in conn.execute(
                "SELECT cidr, COUNT(*), MAX(scan_time) FROM scan_coverage WHERE ok = 0 GROUP BY cidr"):
            cov_fail[cidr] = {"times": n, "last": last}
    except Exception:  # noqa: BLE001 - 舊庫沒這張表＝沒有涵蓋紀錄，全部算「從沒掃過」
        pass
    latest_ok = set()
    if latest:
        try:
            latest_ok = {r[0] for r in conn.execute(
                "SELECT DISTINCT cidr FROM scan_coverage WHERE scan_time = ? AND ok = 1", (latest,))}
        except Exception:  # noqa: BLE001
            latest_ok = set()

    # 這段掃到過幾台（scan_history.segment 是掃描當下記的網段）
    hosts_ever, hosts_last = {}, {}
    for seg, n in conn.execute(
            "SELECT segment, COUNT(DISTINCT ip) FROM scan_history WHERE scan_ok = 1 AND segment IS NOT NULL "
            "GROUP BY segment"):
        hosts_ever[seg] = n
    if latest:
        for seg, n in conn.execute(
                "SELECT segment, COUNT(DISTINCT ip) FROM scan_history WHERE scan_ok = 1 AND scan_time = ? "
                "AND segment IS NOT NULL GROUP BY segment", (latest,)):
            hosts_last[seg] = n

    # 那段 CIA／帳外登記了幾台（逐台，用正典 machine_key）
    nets = [(s["cidr"], _net(s["cidr"])) for s in scope if s["cidr"] and _net(s["cidr"])]
    # 2026-09-21：**登記數要排除退役**。不排的話，一個網段「登記 10 台、只掃到 7 台」
    # 看起來像缺口，實際上那 3 台早就退役——會把人送去查一個不存在的問題。
    # 退役的改成另一個數字列出來（那些 IP 還被占著，是另一種情報）。
    # 排除規則走正典 manage_state.RETIRED_STATUS，跟漏斗／分佈／EOS 同一把尺。
    import manage_state as _ms
    reg_machines: dict[str, set] = {}
    retired_machines: dict[str, set] = {}
    for r in conn.execute(
        "SELECT asset_serial, hostname, ip, COALESCE(asset_status, '') "
        "FROM hardware WHERE ip IS NOT NULL AND ip != ''"
    ):
        seg = _seg_of(r[2], nets)
        if not seg:
            continue
        bucket = retired_machines if (r[3] or "").strip() in _ms.RETIRED_STATUS else reg_machines
        bucket.setdefault(seg, set()).add(system_stats.machine_key(r[1], r[2], r[0]))

    rows = []
    for s in scope:
        in_scope = bool(s["in_scope"] or s.get("rule_in_scope"))
        if only_in_scope and not in_scope:
            continue
        cidr = s["cidr"]
        ok, fail = cov_ok.get(cidr), cov_fail.get(cidr)
        found_ever = hosts_ever.get(cidr, 0)
        if not ok and not fail:
            kind = NEVER
        elif fail and (not ok or (fail["last"] or "") > (ok["last"] or "")):
            kind = FAILED
        elif cidr not in latest_ok:
            kind = STALE
        elif found_ever == 0:
            kind = NO_REPLY
        else:
            kind = OK
        # 這台掃描機本來就看不到的環境（例：非正式區的掃描機對正式區）——
        # 它掃不到是預期中的事，不是要申請防火牆的缺口，也不可以排進第一順位
        expected = bool(visible) and (s["environment"] or "").strip() not in visible
        mk = marks.get(cidr) or {}
        rows.append({
            "expected_unreachable": expected,
            # 人工標註（不覆蓋事實，只是補上人的判斷）
            "note_status": mk.get("status") or "",
            "note_label": NOTE_LABEL.get(mk.get("status") or "", ""),
            "expected_hosts": mk.get("expected_hosts"),
            "note": mk.get("note") or "",
            "note_by": mk.get("updated_by"),
            "note_at": mk.get("updated_at"),
            # 標了「沒在使用／政策不掃」的預設收起來（跟預期不通一樣，不是要辦的事）
            "hidden_by_note": (mk.get("status") or "") in HIDDEN_BY_DEFAULT,
            "cidr": cidr, "raw_cidr": s["raw_cidr"], "location": s["location"], "purpose": s["purpose"],
            "environment": s["environment"], "category": s["category"], "vlan": s["vlan"],
            "addresses": s["addresses"], "in_scope": in_scope,
            "kind": kind, "advice": ADVICE[kind],
            "registered_machines": len(reg_machines.get(cidr, ())),
            "retired_machines": len(retired_machines.get(cidr, ())),
            "scanned_ok_times": (ok or {}).get("times", 0),
            "last_ok_scan": (ok or {}).get("last"),
            "failed_times": (fail or {}).get("times", 0),
            "last_failed_scan": (fail or {}).get("last"),
            "hosts_found_ever": found_ever,
            "hosts_found_last": hosts_last.get(cidr, 0),
        })

    # 排序：最該先處理的在最上面——掃不到又有登記資產的、位址多的
    order = {NO_REPLY: 0, NEVER: 1, FAILED: 2, STALE: 3, OK: 4}
    # 預期不通的一律往後排：它們不是缺口，排在前面會把真正要申請的蓋掉
    rows.sort(key=lambda r: (r["hidden_by_note"], r["expected_unreachable"], order[r["kind"]],
                             -r["registered_machines"], -r["addresses"]))

    def _todo(r):      # 還要處理的＝掃不到、不是預期不通、也沒被人工標成不用管
        return r["kind"] != OK and not r["expected_unreachable"] and not r["hidden_by_note"]

    gaps = [r for r in rows if _todo(r)]
    expected_rows = [r for r in rows if r["kind"] != OK and r["expected_unreachable"]]
    # 預計台數對不上（人說該有 N 台、這次一台都沒掃到）——這種最值得看
    short = [r for r in rows if r.get("expected_hosts") and r["hosts_found_last"] < r["expected_hosts"]]
    return {
        "latest_scan": latest,
        "rows": rows,
        "visible_environments": visible,
        "environments": sorted({(s["environment"] or "").strip() for s in scope if (s["environment"] or "").strip()}),
        "summary": {
            # 預期不通的另外算，不混進缺口
            "expected_unreachable_segments": len(expected_rows),
            "expected_unreachable_machines": sum(r["registered_machines"] for r in expected_rows),
            "in_scope_segments": len(rows),
            "gap_segments": len(gaps),
            "gap_addresses": sum(r["addresses"] for r in gaps),
            "gap_registered_machines": sum(r["registered_machines"] for r in gaps),
            "by_kind": {k: sum(1 for r in rows if r["kind"] == k and not r["expected_unreachable"])
                        for k in (NO_REPLY, NEVER, FAILED, STALE, OK)},
            # 申請防火牆的第一順位：掃不到、但那段 CIA 上有登記資產
            "firewall_first": sum(1 for r in gaps if r["registered_machines"] > 0),
            # 人工標註的統計（畫面用來說「已經標掉幾段、平常不顯示」）
            "noted_segments": sum(1 for r in rows if r["note_status"]),
            "hidden_by_note_segments": sum(1 for r in rows if r["hidden_by_note"]),
            "by_note": {k: sum(1 for r in rows if r["note_status"] == k) for k in NOTE_LABEL},
            "short_of_expected": len(short),
        },
    }


def filter_rows(rows: list[dict], q: str = "", environment: str = "", location: str = "",
                kind: str = "", only_registered: bool = False, include_expected: bool = True) -> list[dict]:
    """畫面上的搜尋／篩選條件，後端也有一份——匯出要跟畫面看到的一樣（所見即所得）。

    q 用空白分隔，每個詞都要命中（網段／機房／環境／用途／VLAN／類別），跟其他頁的搜尋同慣例。
    """
    out = []
    terms = [w for w in (q or "").lower().split() if w]
    envs = {e.strip() for e in (environment or "").split(",") if e.strip()}
    locs = {x.strip() for x in (location or "").split(",") if x.strip()}
    for r in rows:
        if not include_expected and (r.get("expected_unreachable") or r.get("hidden_by_note")):
            continue
        if kind and r["kind"] != kind:
            continue
        if envs and (r.get("environment") or "").strip() not in envs:
            continue
        if locs and (r.get("location") or "").strip() not in locs:
            continue
        if only_registered and not r.get("registered_machines"):
            continue
        if terms:
            hay = " ".join(str(r.get(k) or "") for k in
                           ("cidr", "raw_cidr", "location", "environment", "category", "vlan", "purpose", "kind")).lower()
            if not all(w in hay for w in terms):
                continue
        out.append(r)
    return out


COLUMNS = [
    ("cidr", "網段"), ("location", "機房"), ("environment", "環境"), ("category", "類別"),
    ("vlan", "VLAN"), ("purpose", "用途"), ("addresses", "位址數"), ("expected_note", "這台看得到嗎"),
    ("note_label", "人工標註"), ("expected_hosts", "預計台數"), ("note", "備註"),
    ("registered_machines", "這段登記台數"),
    ("retired_machines", "另有退役台數"), ("kind", "狀態"), ("hosts_found_last", "最近一次掃到台數"),
    ("hosts_found_ever", "歷來掃到台數"), ("last_ok_scan", "最後一次掃通"),
    ("failed_times", "掃描失敗次數"), ("last_failed_scan", "最後一次失敗"), ("advice", "下一步"),
]


def to_csv(data: dict) -> str:
    """CSV（給防火牆申請當附件）。Excel 開不亂碼靠呼叫端加 BOM。"""
    out = [",".join(t for _, t in COLUMNS)]
    for r in data["rows"]:
        r = dict(r, expected_note=(EXPECTED if r.get("expected_unreachable") else "看得到"))
        out.append(",".join('"' + str(r.get(k) if r.get(k) is not None else "").replace('"', '""') + '"'
                            for k, _ in COLUMNS))
    return "\n".join(out)


# ⚠️ 用詞（2026-09-20 使用者追問「掃通了，整段沒回應是什麼狀況?」後修正）：
# 「掃通了」會讓人以為封包真的到得了那段——其實只代表**掃描程序正常跑完、沒有錯誤**。
# TCP 逾時與 ICMP 沒回，在被擋、路由不通、沒機器三種情況下長得一模一樣，所以這一類
# 只能縮小範圍，不能直接當結論；要再分辨得靠 traceroute 之類的旁證（尚未實作）。
