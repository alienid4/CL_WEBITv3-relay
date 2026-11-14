"""「只涵蓋已納管主機」類頁面的共用資料狀態判定（同步碼 ESJE 第 2 點，2026-11-12）。

適用頁面：7.2 帳號盤點（特權帳號／稽核發現／合規矩陣）、7.3 軟體盤點（軟體清單／依主機／搜尋軟體）、
7.4 服務盤點、8.2 組態檢核／基線偏移、8.3 EOS 軟體、10.1 月報效能圖表。這些頁的資料只來自已納管（或已匯入）
的主機，所以「頁上沒有」可能是：真的沒有、沒納管、平台不支援、收集失敗、資料過期——五件事要分得出來，
**未知不得算成 0 或正常**。

一台一列＝v2_hosts.machines（2.1 主機查詢同一份 machine_key 分組，現役），每台**只落一格**：

  在適用範圍內（覆蓋率分母）：
    ok        有資料　　　　這類資料最後更新距今 ≤ 門檻天數
    stale     資料過期　　　有資料但超過門檻，且沒有更新的失敗紀錄
    failed    收集失敗　　　沒有未過期資料，且有失敗證據（連不上／指令全失敗＝多半權限不足／已納管但失聯／匯入失敗或截斷）
    pending   已納管尚無資料　已納管，但這類從未收到、也沒有失敗紀錄（還沒輪到收，或收集器回空）
    unmanaged 未納管　　　　沒有資料，也從沒納管成功
  不在適用範圍（不計入分母，原因可下鑽）：
    unsupported 不支援　　　平台不在這類收集的支援清單（VMware 設備依天條一律不收；設備、儲存、網路…）
    excluded    排除　　　　明確設定排除（帳號稽核排除清單）或不在本頁範圍（基線偏移只看已通過上線檢查的主機）

覆蓋率＝ok ÷（ok＋stale＋failed＋pending＋unmanaged）。卡片數字與下鑽清單一律出自同一個 rows（len 計數），
verify_numbers.check_data_coverage 核「各格相加＝分母」「格子＝下鑽筆數」「分母＋範圍外＝現役台數」。

門檻：主機收集類沿用 v2_collect_summary.stale_days（app_settings.collect_stale_days，預設 8 天，與主機履歷「收集摘要」同一把尺）；
主機規格／實體主機磁碟（host_spec，排程每週更新）預設 14 天（app_settings.spec_stale_days）；組態檢核是人工匯入報告，預設 35 天（app_settings.coverage_stale_days_config 可改）；效能圖表（NMON）沿用 8 天。
只讀，不改任何收集邏輯、不連目標機。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

# ---- 狀態（順序＝畫面順序）
OK, STALE, FAILED, PENDING, UNMANAGED, UNSUPPORTED, EXCLUDED = (
    "ok", "stale", "failed", "pending", "unmanaged", "unsupported", "excluded")
IN_SCOPE = (OK, STALE, FAILED, PENDING, UNMANAGED)
OUT_SCOPE = (UNSUPPORTED, EXCLUDED)
STATES = IN_SCOPE + OUT_SCOPE
#: 「從沒收集過」＝未納管＋已納管尚無資料（2.066.2，1.2 每日異動／摘要信那一列；下鑽 state='never' 是同一份 rows）
NEVER = (PENDING, UNMANAGED)
STATE_LABEL = {OK: "有資料", STALE: "資料過期", FAILED: "收集失敗", PENDING: "已納管尚無資料",
               UNMANAGED: "未納管", UNSUPPORTED: "不支援", EXCLUDED: "排除"}

HOST_FAMS = ("Linux", "AIX", "Windows")

#: 每一類資料：名稱、來源、支援平台、門檻設定鍵（None＝沿用收集摘要那把尺）、預設天數
KINDS: dict[str, dict] = {
    "account": {"label": "帳號", "source": "帳號收集（SSH／WinRM，host_account）", "fams": HOST_FAMS},
    "software": {"label": "軟體清單", "source": "軟體收集（rpm／lslpp／Windows 登錄檔，host_package）", "fams": HOST_FAMS},
    "eos_sw": {"label": "EOS 軟體（依已收集的軟體清單比對）", "source": "軟體收集（host_package）比對 EOS 表", "fams": HOST_FAMS},
    "service": {"label": "服務", "source": "服務收集（ss／netstat／WinRM，host_service）", "fams": HOST_FAMS},
    "config": {"label": "組態檢核", "source": "組態檢核報告匯入（config_audit_run）", "fams": HOST_FAMS,
               "setting": "coverage_stale_days_config", "default_days": 35},
    "drift": {"label": "基線偏移回檢", "source": "服務收集（host_service）＋每日基線回檢", "fams": HOST_FAMS},
    "perf": {"label": "效能資料（NMON）", "source": "NMON 檔匯入（nmon_series）", "fams": ("AIX",)},
    # 2026-11-12 擴充（1.3 設備風險、5.2 儲存容量、2.1 主機履歷頁籤；同一套判定，不另寫）
    # 規格／磁碟同一張 host_spec，排程每週更新一次（spec_collect_interval_days）→ 門檻另一把尺：spec_stale_days，預設 14 天
    "spec": {"label": "主機規格（開機時間、SMART／RAID／多路徑）", "source": "主機規格收集（host_spec）", "fams": HOST_FAMS,
             "setting": "spec_stale_days", "default_days": 14},
    "storage": {"label": "實體主機磁碟", "source": "主機磁碟收集（host_spec 的檔案系統／VG）", "fams": HOST_FAMS,
                "setting": "spec_stale_days", "default_days": 14},
    "hk": {"label": "目錄掃描（容量占用候選）", "source": "目錄清理唯讀掃描（hk_scan）", "fams": HOST_FAMS,
           "setting": "coverage_stale_days_hk", "default_days": 30},
    "relation": {"label": "連線關聯（這台自己看到的連線）", "source": "服務收集的連線列（host_connection）", "fams": HOST_FAMS},
    "rebuild": {"label": "系統重建資料", "source": "OS 重建收集（host_rebuild）", "fams": HOST_FAMS,
                "setting": "coverage_stale_days_rebuild", "default_days": 30},
}
#: 主機收集帳號跑的類別：收集軌跡（collect_ssh_trace）的失敗算這類的失敗
_SSH_KINDS = ("account", "software", "eos_sw", "service", "drift", "spec", "storage", "hk", "relation", "rebuild")
_HK_OK = ("ok", "partial", "nofs")
_HK_FAIL = {"failed": "目錄掃描失敗", "busy": "主機忙碌，沒掃", "stopped": "到 08:30 停止，沒掃完",
            "notimeout": "無遠端逾時保護，只能手動掃"}


def kind_info(kind: str) -> dict:
    if kind not in KINDS:
        raise ValueError(f"不認得的資料類別：{kind}（可用：{'、'.join(KINDS)}）")
    return KINDS[kind]


def stale_days_for(conn, kind: str) -> int:
    import v2_collect_summary as cs

    k = kind_info(kind)
    if not k.get("setting"):
        return cs.stale_days(conn)
    import db

    try:
        n = int(str(db.get_setting(conn, k["setting"], None)).strip())
    except (TypeError, ValueError):
        return int(k["default_days"])
    return n if 1 <= n <= 365 else int(k["default_days"])


def family_of(m: dict) -> str:
    """作業系統大類：VMware／Linux／AIX／Windows／未知／其他。
    取 v2_hosts.machines 的 os_ver（已套「有實測就以實測為準」），VMware 設備看類型徽章（天條：一律不支援）。"""
    b = m.get("badge") or {}
    if b.get("code") == "ESX" or b.get("os_class") == "虛擬化平台":
        return "VMware"
    v = str(m.get("os_ver") or "")
    for f in HOST_FAMS:
        if v.startswith(f):
            return f
    if v.startswith("未知"):
        return "未知"
    return "其他"


# ---- 各類資料的「最後更新時間」索引：ip／序號 → 時間；perf 用主機短名

def _maxmap(conn, sql: str, params: tuple = ()) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:      # 表還沒建（舊庫／從沒匯過）＝沒有資料，不是錯
        if "no such table" not in str(exc) and "no such column" not in str(exc):
            raise
        return out
    for r in rows:
        k, t = (str(r[0] or "").strip()), (r[1] or "")
        if k and (k not in out or str(t) > out[k]):
            out[k] = str(t)
    return out


def _short(name: str | None) -> str:
    return str(name or "").strip().lower().split(".")[0]


#: 表格型資料來源：(表, 時間欄, 條件, IP 欄)。查詢一律 GROUP BY IP 欄與 asset_serial。
_SOURCES = {
    "account": ("host_account", "last_seen", "gone_at IS NULL", "ip"),
    "software": ("host_package", "last_seen", "gone_at IS NULL", "ip"),
    "eos_sw": ("host_package", "last_seen", "gone_at IS NULL", "ip"),
    "service": ("host_service", "last_seen", "gone_at IS NULL", "ip"),
    "drift": ("host_service", "last_seen", "gone_at IS NULL", "ip"),
    "config": ("config_audit_run", "COALESCE(checked_at, imported_at)",
               "status = 'ready' AND COALESCE(completeness, '') != '截斷'", "ip"),
    "spec": ("host_spec", "collected_at", "1=1", "ip"),
    "storage": ("host_spec", "collected_at", "COALESCE(storage_json, '') != ''", "ip"),
    "hk": ("hk_scan", "COALESCE(finished_at, queued_at)", "status IN ('ok','partial','nofs')", "ip"),
    "relation": ("host_connection", "last_seen", "1=1", "host_ip"),
    "rebuild": ("host_rebuild", "collected_at", "1=1", "ip"),
}


def _data_index(conn, kind: str, only: dict[str, list[str]] | None = None) -> dict[str, str]:
    """回 {鍵: 最後時間}；鍵是 IP、資產序號（perf 是 'h:' + 主機短名）。
    only={"ip": [...], "asset_serial": [...]}＝只查這一台（主機履歷頁籤用，不掃整張表）。"""
    out: dict[str, str] = {}
    src = _SOURCES.get(kind)
    if src:
        table, tcol, cond, ipcol = src
        for col, okey in ((ipcol, "ip"), ("asset_serial", "asset_serial")):
            params: tuple = ()
            flt = ""
            if only is not None:
                vals = [v for v in only.get(okey, []) if v]
                if not vals:
                    continue
                flt = f" AND {col} IN ({','.join('?' * len(vals))})"
                params = tuple(vals)
            for k, t in _maxmap(conn, f"SELECT {col}, MAX({tcol}) FROM {table} WHERE ({cond}){flt} GROUP BY {col}",
                                params).items():
                out[k] = max(out.get(k, ""), t)
        return out
    if kind == "perf":
        for k, t in _maxmap(conn, "SELECT host, MAX(day) FROM nmon_series GROUP BY host").items():
            out["h:" + _short(k)] = max(out.get("h:" + _short(k), ""), t)
        return out
    return out


def _fail_index(conn, kind: str) -> dict[str, tuple[str, str]]:
    """失敗證據：{鍵: (時間, 原因)}。主機收集類看收集軌跡＋資產表試連結果；組態檢核看失敗／截斷的匯入；NMON 沒有失敗訊號。"""
    out: dict[str, tuple[str, str]] = {}

    def put(k, t, why):
        k = str(k or "").strip()
        if k and (k not in out or str(t or "") > out[k][0]):
            out[k] = (str(t or ""), why)

    if kind == "hk":
        try:
            for r in conn.execute("SELECT asset_serial, ip, status, COALESCE(finished_at, queued_at) AS t FROM hk_scan "
                                  "WHERE status IN ('failed','busy','stopped','notimeout')"):
                put(r["asset_serial"], r["t"], _HK_FAIL[r["status"]])
                put(r["ip"], r["t"], _HK_FAIL[r["status"]])
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
    if kind in _SSH_KINDS:
        try:
            for r in conn.execute("SELECT asset_serial, ip, connected, transport_error, cmd_total, cmd_ok, cmd_failed, "
                                  "collected_at FROM collect_ssh_trace"):
                if r["connected"] == 0:
                    te = (r['transport_error'] or '原因未記錄').strip()
                    perm = any(x in te.lower() for x in ("permission denied", "authentication"))
                    why = f"{'權限不足：登入被拒' if perm else '收集帳號連不上'}：{te[:120]}"
                elif r["connected"] == 1 and (r["cmd_total"] or 0) > 0 and not (r["cmd_ok"] or 0) and (r["cmd_failed"] or 0) > 0:
                    why = "連得上但指令全部失敗（多半是權限不足）"
                else:
                    continue
                put(r["asset_serial"], r["collected_at"], why)
                put(r["ip"], r["collected_at"], why)
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
        for r in conn.execute("SELECT asset_serial, ip, collect_error, collect_checked_at FROM hardware "
                              "WHERE collect_ok = 0 AND COALESCE(collect_error, '') != ''"):
            why = f"試連失敗：{str(r['collect_error']).strip()[:120]}"
            put(r["asset_serial"], r["collect_checked_at"], why)
        return out
    if kind == "config":
        try:
            for r in conn.execute("SELECT asset_serial, ip, status, completeness, note, imported_at FROM config_audit_run "
                                  "WHERE status = 'failed' OR completeness = '截斷'"):
                why = "報告被截斷（腳本中途結束）" if r["completeness"] == "截斷" else f"報告匯入失敗：{(r['note'] or '').strip()[:120]}"
                put(r["asset_serial"], r["imported_at"], why)
                put(r["ip"], r["imported_at"], why)
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc) and "no such column" not in str(exc):
                raise
    return out


def _excluded_serials(conn, kind: str) -> tuple[set[str] | None, set[str], str]:
    """(只看這些序號（None＝不限）, 排除的序號, 排除原因)。"""
    if kind == "account":
        import account_inventory

        return None, account_inventory.get_excluded_serials(conn), "已列入帳號稽核排除清單（7.2 收集身分）"
    if kind == "drift":
        try:
            only = {r[0] for r in conn.execute("SELECT asset_serial FROM golive_check WHERE status = 'passed'")}
        except sqlite3.OperationalError:
            only = set()
        return only, set(), "未通過上線檢查（沒有基線，不在基線偏移範圍）"
    return None, set(), ""


def coverage(conn, kind: str, now: datetime | None = None) -> dict:
    """一類資料在全部現役主機（一台一列）上的狀態。回 {..., rows: 每台一列（含範圍外）, counts, scope_total, valid, pct}。"""
    import oncall
    import v2_hosts

    info = kind_info(kind)
    now = now or datetime.now()
    limit = stale_days_for(conn, kind)
    groups, _of = oncall._groups(conn)
    data = _data_index(conn, kind)
    fails = _fail_index(conn, kind)
    only, excl, excl_why = _excluded_serials(conn, kind)
    rows = [_row(m, n, groups.get(m["key"]) or [], kind, info, data, fails, only, excl, excl_why, now, limit)
            for n, m in enumerate(v2_hosts.machines(conn), 1)]
    return _summarize(kind, info, limit, now, rows)


def _row(m, n, g, kind, info, data, fails, only, excl, excl_why, now, limit) -> dict:
    """一台在一類資料上落哪一格（coverage 與 for_host 共用，同一把尺）。"""
    import manage_state
    import v2_collect_summary as cs

    serials = [r["asset_serial"] for r in g if r["asset_serial"]]
    ips = sorted({i for r in g for i in manage_state.ips_of(r["ip"])})
    fam = family_of(m)
    keys = serials + ips
    if kind == "perf":
        keys = ["h:" + _short(r["hostname"]) for r in g if (r["hostname"] or "").strip()] + ["h:" + _short(m.get("name"))]
    last = max((data[k] for k in keys if k in data), default=None)
    fail = max(((fails[k]) for k in (serials + ips) if k in fails), default=None)
    ever_ok = any(r["collect_ok"] == 1 for r in g)
    managed = bool(m.get("onboarded")) or ever_ok
    base = {"__no": n, "key": m["key"], "name": m.get("name"), "ip": m.get("ip"), "serial": m.get("serial"),
            "family": fam, "onboarded": bool(m.get("onboarded"))}
    state, reason, source, ts = None, "", info["source"], last
    if fam == "VMware":
        state, reason, source = UNSUPPORTED, manage_state.VMWARE_SKIP, "類型判定（天條：VMware 設備不收集）"
    elif fam not in info["fams"] and fam != "未知":
        # 作業系統未知＝查不到，不是不支援：一律留在分母內（審查 P2；單平台類別 perf 也一樣）
        state = UNSUPPORTED
        reason = f"{fam} 不在這類資料的支援平台（{'／'.join(info['fams'])}）"
        source = "作業系統大類（與 2.1 主機查詢同一份）"
    elif only is not None and not (set(serials) & only):
        state, reason, source = EXCLUDED, excl_why, "上線檢查紀錄（golive_check）"
    elif set(serials) & excl:
        state, reason, source = EXCLUDED, excl_why, "帳號稽核排除清單（app_settings）"
    if state is None:
        c = cs.classify(last, now, limit) if last else {"state": "never", "days": None}
        collected_kind = kind not in ("config", "perf")      # 走收集帳號的類別；組態檢核／NMON 是匯入
        if c["state"] == "fresh":
            state, reason = OK, f"最後更新距今 {c['days']} 天（門檻 {limit} 天）"
        elif fail and (managed or not collected_kind) and (not last or fail[0] >= (last or "")):
            state, reason, source, ts = FAILED, fail[1], "收集軌跡／匯入紀錄", fail[0] or last
            if last:
                reason += f"；最後一次有資料是 {str(last)[:19]}"
        elif c["state"] == "stale":
            state, reason = STALE, f"最後更新距今 {c['days']} 天，超過門檻 {limit} 天"
        elif last and c["state"] == "never":        # 有資料但時間讀不懂／在未來：不當成正常
            state, reason = STALE, f"有資料但更新時間異常（{str(last)[:19]}），不當成正常"
        elif collected_kind and managed and not m.get("onboarded"):
            state, reason, source = FAILED, "已納管但失聯（收集帳號超過 7 天沒連上）", "納管狀態（existence）"
        elif managed:
            state, reason = PENDING, "已納管，這類資料還沒收到（尚未輪到收集，或收集器回空）"
        else:
            state, reason, source = UNMANAGED, "沒有這類資料，且從未納管成功", "納管狀態（existence）"
        if not collected_kind and state in (UNMANAGED, PENDING):
            what = "NMON 檔" if kind == "perf" else "組態檢核報告"
            reason = (f"已納管，但沒有匯入過這台的{what}" if state == PENDING
                      else f"沒有匯入過這台的{what}，且從未納管成功")
        if fam == "未知" and state in (UNMANAGED, PENDING):
            reason = f"作業系統未知（無法判斷是否為 {'／'.join(info['fams'])}）；{reason}"
    return {**base, "state": state, "state_label": STATE_LABEL[state], "reason": reason, "source": source,
            "updated_at": (str(ts)[:19] if ts else None)}


def _summarize(kind, info, limit, now, rows) -> dict:
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    scope_total = sum(counts[s] for s in IN_SCOPE)
    valid = counts[OK]
    return {
        "kind": kind, "label": info["label"], "source": info["source"], "platforms": list(info["fams"]),
        "stale_days": limit, "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "counts": counts, "scope_total": scope_total, "valid": valid, "out_of_scope": counts[UNSUPPORTED] + counts[EXCLUDED],
        "pct": (round(valid * 100.0 / scope_total, 1) if scope_total else None),
        "formula": f"有資料且未逾期 {valid} ÷ 適用範圍 {scope_total} 台（不含不支援 {counts[UNSUPPORTED]}、排除 {counts[EXCLUDED]}）",
        "state_labels": STATE_LABEL, "in_scope_states": list(IN_SCOPE),
        "rows": rows,
    }


def for_host(conn, machine_key: str, kind: str, now: datetime | None = None, groups: dict | None = None) -> dict | None:
    """主機履歷頁籤頂端「這台的狀態與資料時間」：跟清單頁頁首同一支判定（_row），只算這一台。查無這台回 None。"""
    import manage_state
    import oncall
    import v2_hosts

    info = kind_info(kind)
    now = now or datetime.now()
    m = next((x for x in v2_hosts.machines(conn) if x["key"] == machine_key), None)
    if m is None:
        return None
    if groups is None:
        groups, _of = oncall._groups(conn)
    sel, excl, excl_why = _excluded_serials(conn, kind)
    limit = stale_days_for(conn, kind)
    g = groups.get(machine_key) or []
    only = {"asset_serial": [r["asset_serial"] for r in g], "ip": sorted({i for r in g for i in manage_state.ips_of(r["ip"])})}
    r = _row(m, m.get("__no") or 0, g, kind, info, _data_index(conn, kind, only),
             _fail_index(conn, kind), sel, excl, excl_why, now, limit)
    return {**r, "kind": kind, "label": info["label"], "stale_days": limit}


def drill(conn, kind: str, state: str | None = None, now: datetime | None = None) -> dict:
    """API 用：沒指定 state 只回彙總；指定 state 回那一格的清單（＝格子數字，同一份 rows）。
    state='scope'＝適用範圍全部、'out'＝範圍外全部、'never'＝從沒收集過（未納管＋已納管尚無資料）。"""
    r = coverage(conn, kind, now)
    rows = r.pop("rows")
    if state:
        if state == "scope":
            want = set(IN_SCOPE)
        elif state == "out":
            want = set(OUT_SCOPE)
        elif state == "never":
            want = set(NEVER)
        elif state in STATES:
            want = {state}
        else:
            raise ValueError(f"不認得的狀態：{state}")
        r["rows"] = [x for x in rows if x["state"] in want]
        r["state"] = state
    return r
