"""每台主機的體檢：一眼看出「完全沒問題」還是「要查看」。

## 為什麼是兩個燈不是一個

    machine  這台機器本身有沒有問題（失聯、進不去、版本過保、基線被改）
    data     這台機器的**登記資料**有沒有問題（機房沒填、身分不明、重複登記）

一台機器好好的但資料沒填，跟一台機器失聯了，**處置完全不同**——一個是找人補資料，
一個是打電話問機器還在不在。混成一個燈，紅燈就失去意義，人只會習慣性忽略它。

## 每一項都必須說得出「跟什麼比」

沒有對照基準的「有問題」是主觀感覺，窗口不會服氣，也不知道要做什麼。所以每個
CHECKS 項目都帶 `basis`（跟什麼比）與 `action`（下一步做什麼），畫面直接顯示，
不要再讓人猜。

## 燈號口徑

    ok    全部通過
    warn  有「要補、要查」的項目，但機器本身還在正常運作
    bad   有實際異常，會影響服務或讓數字失真

「未公布 EOS 日期」這種**查無結果**一律不算 bad——把「官方沒公布」跟「已經過保」
混在一起，等於製造假警報（2026-08-22 使用者already指正過同一件事）。

## 效能

4600 台 × 10 項，不可以每台各自查資料庫。`evaluate_all()` 先用批次查詢把所有
對照資料撈成 dict，再逐台純記憶體判定；每次呼叫只有固定幾條 SQL。
"""
from __future__ import annotations

import fleet_cache as _fleet_cache

OK, WARN, BAD = "ok", "warn", "bad"

# 盤點必填：沒有這些就分不進機房／環境，也找不到人負責
REQUIRED_FIELDS = {
    "physical_location": "資產實體位置",
    "environment": "環境別",
    "custodian": "保管者",
}


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _load_context(conn) -> dict:
    """把所有對照資料一次撈好。每多一條批次查詢，就少 4600 次逐台查詢。"""
    import manage_state

    ctx: dict = {}

    # 失聯／未涵蓋的判定**一律走正典 manage_state**，不在這裡自己重算一份（2026-09-18）。
    # 以前 _chk_lost 自己用「掃到的 IP 反推 /24 前綴」猜涵蓋、自己配掃描結果——那是全站
    # 第三份「失聯」判定，跟 machine_key／manage_state 不同源（B-09 已把失聯改成看
    # scan_coverage 涵蓋網段＋沒 IP 獨立成一關，這裡沒跟上就會兩個畫面對不起來）。
    # 改成讀 manage_state.summarize 的逐台狀態：serial → state。
    latest = conn.execute("SELECT MAX(scan_time) AS t FROM scan_history").fetchone()
    ctx["scan_time"] = latest["t"] if latest else None
    ms_state: dict[str, str] = {}
    for it in manage_state.summarize(conn)["items"]:
        sn = it.get("asset_serial")
        if sn:
            ms_state[sn] = it["state"]
    ctx["ms_state"] = ms_state

    # 重複登記：跟資產查詢的重複清單同一份分組（vip_view.duplicate_candidate_groups，2026-10-17 審查 #5）
    import vip_view
    # 審查 N2：只有「疑似真重複」才標重複登記（一機多系統、VIP／用途分列是同一台多個服務，不是重複）
    ctx["dup_serials"] = {m["asset_serial"] for _k, ms_ in vip_view.duplicate_candidate_groups(conn)
                          if vip_view.classify_duplicate_group(ms_)["kind"] == vip_view.DUP_SUSPECT
                          for m in ms_}

    # 基線失效 / 帳號稽核發現 / 單據：都以 asset_serial 對應
    # 基線列建立時就是 status='fixed'（＝符合基線）；只有 open／ack 才是偏移。原本把 fixed 也算
    # 成「基線失效」，等於每台通過上線檢查的機器都被報偏移（與 golive.drift_summary 口徑對齊）。
    ctx["drift"] = _count_by(conn, "baseline_drift", "asset_serial",
                             "baseline IS NOT NULL AND baseline != '' AND (status IS NULL OR status != 'fixed')")
    ctx["findings"] = _count_by(conn, "account_finding", "asset_serial",
                                "verdict IS NULL OR verdict NOT IN ('ok','exempt','fixed')")
    ctx["docs"] = _count_by(conn, "doc_archive", "asset_serial", None)
    # 單據檔案室一筆都沒有 → 這個模組還沒開始用。這時對每一台報「沒有單據」，
    # 報的是「我還沒做這件事」，不是「這台機器有問題」——那是純雜訊，會把
    # 真正要看的機器淹掉。基準資料不存在時，檢查一律沉默。
    ctx["has_docs"] = _table_exists(conn, "doc_archive") and conn.execute(
        "SELECT COUNT(*) FROM doc_archive").fetchone()[0] > 0
    # 被動來源清單：dynassets／RVTools 曾經對到過哪些 hardware.id——用來判斷
    # 「沒收集成功」的機器裡，哪些其實已經有別的來源告訴我們一些事，不是真的黑洞。
    # CIA 登記的不查這張表：只要不是帳外前綴，本來就是 CIA 匯入建的，不需要再查一次。
    ctx["passive_source_ids"] = {
        r["resolved_hardware_id"]
        for r in conn.execute(
            "SELECT DISTINCT resolved_hardware_id FROM source_record "
            "WHERE resolved_hardware_id IS NOT NULL AND source IN ('dynassets', 'vcenter')"
        )
    }

    _load_coverage_evidence(conn, ctx)
    ctx["manage_state"] = manage_state
    return ctx


ACCOUNT_STALE_DAYS_DEFAULT = 30     # 帳號盤點超過幾天算過期（app_settings: health_account_stale_days）


def _parse_ts(v):
    """'2026-10-31 07:16:00'（可帶 T／小數秒）→ datetime；判不出來回 None。"""
    from datetime import datetime
    t = _s(v).replace("T", " ")[:19]
    try:
        return datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _load_coverage_evidence(conn, ctx) -> None:
    """帳號盤點／基線回檢的「受檢證據」（2026-10-31，Codex 37 號）。

    check 函式回 None 同時代表「已驗且正常」與「根本沒跑過」；沒有發現列不能證明跑過。
    所以另外撈證據：帳號＝這台有 host_account 紀錄與盤點時間；基線＝有已上線基準＋有回檢紀錄。
    全部批次撈成 dict，逐台純記憶體判定（效能同上，固定幾條 SQL）。
    """
    from datetime import datetime

    # 帳號盤點：host_account 逐台最後盤點時間（依 asset_serial 或 ip 對應到這台）
    ctx["acct_last_serial"], ctx["acct_last_ip"] = {}, {}
    if _table_exists(conn, "host_account"):
        for r in conn.execute("SELECT asset_serial, ip, MAX(last_seen) t FROM host_account "
                              "GROUP BY asset_serial, ip"):
            for key, d in ((r["asset_serial"], ctx["acct_last_serial"]), (r["ip"], ctx["acct_last_ip"])):
                if _s(key) and (r["t"] or "") > (d.get(key) or ""):
                    d[key] = r["t"]
    # 最近一次 SSH 連線結果（盤點失敗＝連不上）
    ctx["trace"] = {}
    if _table_exists(conn, "collect_ssh_trace"):
        ctx["trace"] = {r["asset_serial"]: (r["connected"], r["collected_at"])
                        for r in conn.execute("SELECT asset_serial, connected, collected_at FROM collect_ssh_trace")}
    days = ACCOUNT_STALE_DAYS_DEFAULT
    try:
        from db import get_setting
        raw = get_setting(conn, "health_account_stale_days", None)
        if raw not in (None, ""):
            days = max(1, int(raw))
    except Exception:  # noqa: BLE001 — 設定缺表／壞值＝用預設天數，不擋體檢
        days = ACCOUNT_STALE_DAYS_DEFAULT
    ctx["acct_stale_days"] = days
    ctx["now"] = datetime.now()

    # 基線：有「已上線基準」的資產（baseline_drift 有基準值）＋上線時間；回檢是全站一次的紀錄
    ctx["baseline"] = _count_by(conn, "baseline_drift", "asset_serial",
                                "baseline IS NOT NULL AND baseline != ''")
    ctx["golive_at"] = {}
    if _table_exists(conn, "golive_check"):
        ctx["golive_at"] = {r["asset_serial"]: r["passed_at"] for r in conn.execute(
            "SELECT asset_serial, passed_at FROM golive_check WHERE status = 'passed'")}
    ctx["drift_last_run"] = None
    try:
        import json
        from db import get_setting
        raw = get_setting(conn, "drift_last_run", None)
        last = json.loads(raw) if raw else None
        ctx["drift_last_run"] = (last or {}).get("at")
    except Exception:  # noqa: BLE001 — 設定缺失／壞掉＝視為沒回檢過（寧可標未驗，不默認已驗）
        ctx["drift_last_run"] = None
    ctx["drift_assets"] = {}
    try:
        import json
        from db import get_setting
        raw = get_setting(conn, "drift_last_run_assets", None)
        ctx["drift_assets"] = (json.loads(raw) if raw else {}).get("assets", {}) or {}
    except Exception:  # noqa: BLE001 — 沒有逐台記錄＝這台沒有回檢證據，標未驗
        ctx["drift_assets"] = {}


def _has_passive_source(row, ctx) -> bool:
    """這台是不是至少有一個被動來源（CIA 登記／dynassets／RVTools）提過。"""
    import system_report

    if not system_report.is_off_book(row["asset_serial"]):
        return True                          # 不是帳外前綴＝本來就是 CIA 匯入建的
    return row["id"] in ctx["passive_source_ids"]


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _count_by(conn, table: str, col: str, where: str | None) -> dict:
    """回 {asset_serial: 筆數}。表不存在（舊 DB）就回空 dict，不擋整個體檢。"""
    if not _table_exists(conn, table):
        return {}
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        return {}
    sql = f"SELECT {col} k, COUNT(*) n FROM {table} WHERE {col} IS NOT NULL AND trim({col}) <> ''"
    if where:
        sql += f" AND ({where})"
    sql += f" GROUP BY {col}"
    return {r["k"]: r["n"] for r in conn.execute(sql)}


# ---- 檢查項 -------------------------------------------------------------
# 每一項回 None 代表通過；回 dict 代表沒過，dict 一定要有 basis 與 action，
# 因為「有問題」必須說得出跟什麼比、以及下一步做什麼。

def _chk_lost(row, ctx):
    # 直接讀正典 manage_state 的逐台狀態，不再自己算（見 _load_context 的說明）。
    # 只把「失聯」與「未涵蓋」這兩個跟掃描存活有關的狀態轉成體檢發現；其餘狀態
    # （已納管／沒有 IP／退役…）不是這一項的職責，回 None。
    if not ctx["scan_time"]:
        return None                      # 整個系統從來沒掃過，不能說任何一台失聯（假警報）
    ms = ctx["manage_state"]
    state = ctx["ms_state"].get(row["asset_serial"])
    if state == ms.NOT_COVERED:
        return {"key": "not_covered", "light": "data", "level": WARN, "label": "掃描沒涵蓋",
                "basis": "manage_state 逐台狀態（依 scan_coverage 涵蓋的網段判定）",
                "detail": "這台所在的網段最近一次掃描沒有涵蓋，存活無從確認",
                "action": "把這個網段加進掃描目標；掃不到就永遠沒人知道它還在不在"}
    if state == ms.LOST:
        return {"key": "lost", "light": "machine", "level": BAD, "label": "失聯",
                "basis": f"manage_state 逐台狀態（最近一次掃描 {ctx['scan_time']}）",
                "detail": "登記在案、掃描範圍有涵蓋，但這次掃描沒有回應",
                "action": "確認機器是否已下線；真的退役就改資產狀態，不要留在在用清單裡"}
    return None


CHECKS_TOTAL = 10   # lost/collect/eos/drift/account/identity/required/duplicate/os_guessed/doc


CHECK_KEYS = ("lost", "collect", "eos", "drift", "account",
              "identity", "required", "duplicate", "os_guessed", "doc")
PASSED, FOUND, NOT_CHECKED, NOT_APPLICABLE = "passed", "found", "not_checked", "not_applicable"
_ISSUE_TO_CHECK = {"not_covered": "lost", "os_unknown": "os_guessed"}


def _account_state(row, ctx) -> tuple[str, str]:
    """帳號盤點的受檢證據：每台各自判定，不共用結果。"""
    serial, ip = row["asset_serial"], _s(row["ip"])
    ip_ok = bool(ip) and ip not in ("0.0.0.0", "127.0.0.1")       # 通用／無效 IP 不拿來借別台的盤點時間
    last = ctx["acct_last_serial"].get(serial) or (ctx["acct_last_ip"].get(ip) if ip_ok else None)
    conn_ok, conn_at = ctx["trace"].get(serial, (None, None))
    if not last:
        why = "這台尚無帳號盤點"
        if conn_ok == 0:
            why += "（最近一次 SSH 連線失敗）"
        return NOT_CHECKED, why
    t = _parse_ts(last)
    if t is None:
        return NOT_CHECKED, f"帳號盤點時間無法判讀（{last}）"
    t_conn = _parse_ts(conn_at) if conn_at else None
    if conn_ok == 0 and t_conn is not None and t_conn > t:
        return NOT_CHECKED, f"最近一次帳號盤點失敗（SSH 連線失敗，上次成功 {last}）"
    age = (ctx["now"] - t).days
    if age > ctx["acct_stale_days"]:
        return NOT_CHECKED, f"帳號盤點已過期（{last}，超過 {ctx['acct_stale_days']} 天）"
    return PASSED, f"帳號盤點 {last}"


def _drift_state(row, ctx) -> tuple[str, str]:
    serial = row["asset_serial"]
    if not ctx["baseline"].get(serial):
        return NOT_APPLICABLE, "尚未通過上線前檢查，無基準可比"
    at = ctx["drift_last_run"]
    passed_at = ctx["golive_at"].get(serial)
    if not at or (passed_at and _s(passed_at) > _s(at)):
        return NOT_CHECKED, "有上線基準但上線後尚未回檢過"
    ev = ctx.get("drift_assets", {}).get(serial)                # 逐台證據：這台自己的比對項數（不是全站一次的時間）
    if not ev:
        return NOT_CHECKED, "最近一次回檢沒有這台的比對記錄，需重新回檢一次"
    cmp_n, unk_n = (list(ev) + [0, 0])[:2]
    if not cmp_n:
        return NOT_CHECKED, f"最近一次回檢這台的項目全部測不到（{unk_n} 項未知），無從比對"
    return PASSED, f"基線回檢 {at}（比對 {cmp_n} 項、測不到 {unk_n} 項）"


def _check_states(row, ctx, issues, eos_mod, normalize_mod, conn) -> dict:
    """十項檢查各自的四態：passed／found／not_checked／not_applicable（含原因）。
    沒有發現（None）不等於已驗通過——只有確實有受檢證據才算 passed（W221-013／Codex 37）。"""
    found = {}
    for i in issues:
        found.setdefault(_ISSUE_TO_CHECK.get(i["key"], i["key"]), i["label"])
    st = {k: {"state": PASSED, "reason": ""} for k in CHECK_KEYS}

    if not ctx["scan_time"]:
        st["lost"] = {"state": NOT_CHECKED, "reason": "整個系統從沒掃過，無從說失聯與否"}
    if row["collect_ok"] != 1 and not _s(row["ip"]):
        st["collect"] = {"state": NOT_CHECKED, "reason": "沒有 IP，無從收集"}
    os_raw = _s(row["os"])
    if not os_raw or os_raw.upper() == "N/A":
        st["eos"] = {"state": NOT_CHECKED, "reason": "OS 未知，無從比對 EOS"}
    else:
        canon = normalize_mod.normalize_os(os_raw, conn, row["device_model"]).get("canonical")
        if not eos_mod.lookup_os_eos(canon):
            st["eos"] = {"state": NOT_CHECKED, "reason": "EOS 表查無此產品"}
    s_, why = _drift_state(row, ctx)
    st["drift"] = {"state": s_, "reason": why}
    s_, why = _account_state(row, ctx)
    st["account"] = {"state": s_, "reason": why}
    if not ctx["has_docs"]:
        st["doc"] = {"state": NOT_APPLICABLE, "reason": "單據檔案室尚未啟用（沒有任何單據）"}

    for k, label in found.items():       # 有發現的一律是「已驗有發現」，不論證據多寡
        if k in st:
            st[k] = {"state": FOUND, "reason": label}
    return st


def _chk_collect(row, ctx):
    """SSH/WinRM 直接收集失敗才算「收不到資料」——這句話原本對所有沒收集成功的
    機器一律亮黃燈，把「完全不知道這台是什麼」跟「CIA/RVTools/dynassets 已經告訴
    我們不少、只是沒有親自驗證過」混成同一種問題，語氣上等於在說「我們不知道」，
    但其實知道不少（2026-08-25 使用者指出：三個被動來源本來就能拼出完整盤點，
    不該讓沒納管的機器全部看起來像黑洞）。

    所以只有**完全沒有任何被動來源資料**（不是 CIA 登記的、也沒被 dynassets／
    RVTools 對到過）才算真正的「收不到資料」。有被動來源但沒驗證過的，不算問題，
    只在 evaluate_all() 的 `verified` 欄位標記，畫面自己決定要不要用不搶眼的方式提示
    「未經 SSH 驗證」——那是誠實揭露，不是體檢紅黃燈。
    """
    if row["collect_ok"] == 1:
        return None
    if not _s(row["ip"]):
        return None
    if _has_passive_source(row, ctx):
        return None
    err = _s(row["collect_error"])
    return {"key": "collect", "light": "machine", "level": WARN, "label": "收不到資料",
            "basis": "收集器最近一次連線結果（collect_ok）＋CIA/RVTools/dynassets 都沒有這台的資料",
            "detail": err or "尚未成功收集過主機事實，而且沒有任何登記/掃描來源提過這台",
            "action": "確認收集帳號與金鑰是否佈到這台；佈不了的走 Push Agent"}


def _chk_eos(row, ctx, eos_mod, normalize_mod, conn):
    os_raw = _s(row["os"])
    if not os_raw or os_raw.upper() == "N/A":
        return None                      # OS 未知是資料問題，另一項會抓
    canon = normalize_mod.normalize_os(os_raw, conn, row["device_model"]).get("canonical")
    hit = eos_mod.lookup_os_eos(canon)
    if not hit:
        return None                      # EOS 表根本沒這個產品＝查無，不是過保
    status = eos_mod.eos_status(hit.get("eos_date"))
    kind = hit.get("eos_kind") or "EOS"
    when = hit.get("eos_date")
    if status in ("expired", "upcoming") and not eos_mod.is_trusted(hit):
        # W221-006：日期來源未核對（confidence=unverified）→ 不單獨亮紅燈，降為提醒並講明來源未核對
        return {"key": "eos", "light": "machine", "level": WARN, "label": "EOS 日期來源未核對",
                "basis": f"EOS 資料表（{canon} → {when}，{kind}；來源未核對）",
                "detail": f"{canon} 的官方公告日期（{kind} {when}）尚未重新核對來源，先不視為過保",
                "action": "到 EOS 頁核對官方頁面後再判斷"}
    plan = bool(hit.get("extendable"))
    ent = eos_mod.entitlement_state(eos_mod.entitlement_for(conn, row), hit)
    valid_cover = plan and ent == "valid"
    ext = ""
    if plan and ent == "valid":
        ext = " 這台已登記有效的延伸維護權益，須否更換依維護條件而定。"
    elif plan:
        ext = " 官方另有付費延伸方案，但這台是否持有尚未登記（權益待確認），不當作已涵蓋。"
    if status == "expired":
        # 只有「這台確有有效權益」才降級；產品有方案不等於這台有買（Codex 24 號）
        return {"key": "eos", "light": "machine", "level": WARN if valid_cover else BAD,
                "label": "官方公告已結束（已登記延伸維護權益）" if valid_cover else
                         ("官方公告已結束（資產權益待確認）" if plan else "版本已過保"),
                "basis": f"EOS 資料表（{canon} → {when}，{kind}）",
                "detail": f"官方公告 {canon} 於 {when} 結束（{kind}）。{ext}".strip(),
                "action": "確認這台是否仍有維護權益；沒有的話排升級或申請例外"}
    if status == "upcoming":
        return {"key": "eos", "light": "machine", "level": WARN, "label": "一年內結束支援",
                "basis": f"EOS 資料表（{canon} → {when}，{kind}）",
                "detail": f"官方公告 {canon} 將於 {when} 結束（{kind}）。{ext}".strip(),
                "action": "提前排升級，不要等到過期才動"}
    return None                          # ok 與 unknown 都不報——未公布不等於過保


def _chk_drift(row, ctx):
    n = ctx["drift"].get(row["asset_serial"], 0)
    if not n:
        return None
    return {"key": "drift", "light": "machine", "level": BAD, "label": "基線失效",
            "basis": "上線前檢查表當時記錄的基線",
            "detail": f"{n} 項設定與上線時不同",
            "action": "到基線失效頁看是哪幾項；是刻意改的就更新基線，不是就改回來"}


def _chk_account(row, ctx):
    n = ctx["findings"].get(row["asset_serial"], 0)
    if not n:
        return None
    return {"key": "account", "light": "machine", "level": WARN, "label": "帳號稽核發現",
            "basis": "帳號合規規則",
            "detail": f"{n} 筆未處理的發現",
            "action": "到帳號合規表處理；能豁免的標豁免並寫原因"}


def _chk_identity(row, ctx):
    if any(_s(row[k]) for k in ("vm_uuid", "hw_serial", "mac")):
        return None
    return {"key": "identity", "light": "data", "level": WARN, "label": "身分不明",
            "basis": "強識別碼（vm_uuid／硬體序號／MAC）三者皆空",
            "detail": "只能靠 IP 與主機名認人，兩者都會變",
            "action": "收得到就補；下次匯入才不會又判不準、又生一筆待審核"}


def _chk_required(row, ctx):
    missing = [label for k, label in REQUIRED_FIELDS.items() if not _s(row[k])]
    if not missing:
        return None
    return {"key": "required", "light": "data", "level": WARN, "label": "必填欄位缺漏",
            "basis": "盤點必填欄位（" + "、".join(REQUIRED_FIELDS.values()) + "）",
            "detail": "缺：" + "、".join(missing),
            "action": "補上；沒有這些就分不進機房／環境，也找不到人負責"}


def _chk_duplicate(row, ctx):
    if row["asset_serial"] not in ctx["dup_serials"]:
        return None
    return {"key": "duplicate", "light": "data", "level": BAD, "label": "重複登記",
            "basis": "同一台（machine_key）的其他資產",
            "detail": "同一台被登記成多筆",
            "action": "清掉重複的，否則資產總數是假的"}


def _chk_os_guessed(row, ctx):
    os_raw = _s(row["os"])
    if os_raw and os_raw.upper() != "N/A":
        return None
    return {"key": "os_unknown", "light": "data", "level": WARN, "label": "OS 不是實際收到的",
            "basis": "hardware.os（實際收集才會有值）",
            "detail": "版本靠掃描推測或根本未知",
            "action": "收得到就補；OS 不準，EOS 判斷跟著不可信"}


def _chk_doc(row, ctx):
    if not ctx["has_docs"]:
        return None                      # 系統裡一筆單據都沒有＝還沒開始用，不報
    if ctx["docs"].get(row["asset_serial"], 0):
        return None
    return {"key": "doc", "light": "data", "level": WARN, "label": "沒有單據",
            "basis": "單據檔案室（異動需求單／上線前檢查表）",
            "detail": "這台的存在沒有紙本依據",
            "action": "補上對應單據；稽核時要拿得出來"}


@_fleet_cache.cached   # 跨請求快取：資料庫沒變就不重算（fleet_cache）
def evaluate_all(conn) -> dict:
    """回 {hardware_id: {...}}。**唯讀，不寫任何東西。**

    退役資產（停用／報廢／閒置）不體檢——它們本來就不該還在網路上，
    對它們報「失聯」是雜訊，會把真正要看的機器淹掉。
    """
    import eos as eos_mod
    import manage_state
    import normalize as normalize_mod

    ctx = _load_context(conn)
    rows = conn.execute("SELECT * FROM hardware").fetchall()
    # 別名／改名對照表整次體檢只查一次（原本每台 _chk_eos 各查兩張表，4600 台＝約 9000 條 SQL）
    with normalize_mod.lookup_cache():
        return _evaluate_rows(rows, ctx, eos_mod, manage_state, normalize_mod, conn)


def _evaluate_rows(rows, ctx, eos_mod, manage_state, normalize_mod, conn) -> dict:
    out: dict[int, dict] = {}
    for row in rows:
        if _s(row["asset_status"]) in manage_state.RETIRED_STATUS:
            continue
        issues = []
        for fn in (_chk_lost, _chk_collect, _chk_drift, _chk_account,
                   _chk_identity, _chk_required, _chk_duplicate,
                   _chk_os_guessed, _chk_doc):
            r = fn(row, ctx)
            if r:
                issues.append(r)
        r = _chk_eos(row, ctx, eos_mod, normalize_mod, conn)
        if r:
            issues.append(r)

        states = _check_states(row, ctx, issues, eos_mod, normalize_mod, conn)
        cov = {k: sum(1 for v in states.values() if v["state"] == k)
               for k in (PASSED, FOUND, NOT_CHECKED, NOT_APPLICABLE)}
        assert sum(cov.values()) == CHECKS_TOTAL        # 四態加總恆等於分母
        # 舊欄位相容：not_checked ＝「沒驗到」的項目 key（未驗＋不適用），total - len ＝ 已驗
        nc = [k for k in CHECK_KEYS if states[k]["state"] in (NOT_CHECKED, NOT_APPLICABLE)]

        def light_of(which: str) -> str:
            lv = [i["level"] for i in issues if i["light"] == which]
            return BAD if BAD in lv else (WARN if WARN in lv else OK)

        out[row["id"]] = {
            "machine": light_of("machine"),
            "data": light_of("data"),
            "issues": issues,
            "checks_total": CHECKS_TOTAL,
            "not_checked": nc,
            "check_states": states,
            "coverage": {**cov, "total": CHECKS_TOTAL},
            # 給畫面一句話用：最嚴重的那一項。全綠就是空字串。
            "headline": next((i["label"] for i in issues if i["level"] == BAD),
                             next((i["label"] for i in issues), "")),
            # 有沒有被 SSH/WinRM 直接驗證過。false 不代表「有問題」——可能只是
            # CIA/RVTools/dynassets 已經有資料、沒必要再標成黃燈，畫面自己決定
            # 要不要用不搶眼的方式提示「未經驗證」（2026-08-25 使用者拍板方案A）。
            "verified": row["collect_ok"] == 1,
        }
    return out


def summary(conn) -> dict:
    """首頁那一句話：幾台完全沒問題、幾台要看、要看的是卡在哪幾項。"""
    ev = evaluate_all(conn)
    clean = sum(1 for v in ev.values() if v["machine"] == OK and v["data"] == OK)
    by_issue: dict[str, int] = {}
    for v in ev.values():
        for i in v["issues"]:
            by_issue[i["label"]] = by_issue.get(i["label"], 0) + 1
    return {
        "total": len(ev),
        "clean": clean,
        "needs_review": len(ev) - clean,
        "machine_bad": sum(1 for v in ev.values() if v["machine"] == BAD),
        "data_bad": sum(1 for v in ev.values() if v["data"] == BAD),
        "by_issue": dict(sorted(by_issue.items(), key=lambda kv: -kv[1])),
        # 誠實揭露、不是體檢紅黃燈：幾台沒被 SSH/WinRM 直接驗證過。這個數字會比
        # 「收不到資料」黃燈大很多是正常的——大部分未驗證的機器其實有 CIA/RVTools/
        # dynassets 資料撐著，只是沒有親自登進去確認過（2026-08-25 使用者拍板方案A）。
        "unverified": sum(1 for v in ev.values() if not v["verified"]),
        # 沒有發現問題不等於十項都驗過：幾台有項目未驗／不適用（Codex 37）
        "incomplete_coverage": sum(1 for v in ev.values() if v["not_checked"]),
    }
