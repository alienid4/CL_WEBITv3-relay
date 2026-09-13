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

    # 最近一次掃描掃到誰（對照「失聯」）
    latest = conn.execute("SELECT MAX(scan_time) AS t FROM scan_history").fetchone()
    scan_time = latest["t"] if latest else None
    ctx["scan_time"] = scan_time
    ips, hosts = set(), set()
    if scan_time:
        for r in conn.execute(
            "SELECT ip, hostname FROM scan_history WHERE scan_time = ? AND scan_ok = 1",
            (scan_time,),
        ):
            if _s(r["ip"]):
                ips.add(_s(r["ip"]))
            if _s(r["hostname"]):
                hosts.add(_s(r["hostname"]).lower())
    ctx["scan_ips"], ctx["scan_hosts"] = ips, hosts
    # 這次掃描實際涵蓋了哪些網段（用掃到的 IP 反推 /24）。
    # ⚠️ 沒有這一層，判定會全面走鐘：實查 221，最近一次掃描只涵蓋 1 個網段共 5 台，
    # 於是 4641 台全部被判「失聯」——那不是失聯，是**那個網段根本沒被掃**。
    # 「防火牆不通／還沒掃」與「機器真的不在」要做的事完全不同（決策 S2 同一件事）。
    ctx["scanned_prefixes"] = {ip.rsplit(".", 1)[0] for ip in ips if ip.count(".") == 3}

    # 重複登記：同主機名＋IP 出現多次（已被人工排除的不算）
    dismissed = {
        (_s(r["hostname"]).lower(), _s(r["ip"]))
        for r in conn.execute("SELECT hostname, ip FROM duplicate_dismiss")
    } if _table_exists(conn, "duplicate_dismiss") else set()
    dup: set[tuple[str, str]] = set()
    for r in conn.execute(
        "SELECT lower(trim(hostname)) h, trim(ip) i, COUNT(*) n FROM hardware "
        "WHERE hostname IS NOT NULL AND trim(hostname) <> '' "
        "AND ip IS NOT NULL AND trim(ip) <> '' "
        "GROUP BY h, i HAVING n > 1"
    ):
        key = (r["h"], r["i"])
        if key not in dismissed:
            dup.add(key)
    ctx["dup_keys"] = dup

    # 基線失效 / 帳號稽核發現 / 單據：都以 asset_serial 對應
    ctx["drift"] = _count_by(conn, "baseline_drift", "asset_serial",
                             "status IS NULL OR status NOT IN ('resolved','accepted')")
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

    ctx["manage_state"] = manage_state
    return ctx


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
    if not ctx["scan_time"]:
        return None                      # 從來沒掃過，不能說人家失聯
    ip, host = _s(row["ip"]), _s(row["hostname"]).lower()
    if (ip and ip in ctx["scan_ips"]) or (host and host in ctx["scan_hosts"]):
        return None
    if not ip and not host:
        return None                      # 連 IP 和主機名都沒有，是資料問題不是失聯
    # 這台的網段這次根本沒被掃到 → 不能說它失聯，只能說「沒人確認過它還在不在」。
    # 這是掃描涵蓋率的問題（要去加掃描目標），不是機器的問題（要去問機器還在不在）。
    prefix = ip.rsplit(".", 1)[0] if ip.count(".") == 3 else ""
    if prefix and prefix not in ctx["scanned_prefixes"]:
        return {"key": "not_covered", "light": "data", "level": WARN, "label": "掃描沒涵蓋",
                "basis": f"最近一次掃描（{ctx['scan_time']}）實際涵蓋的網段",
                "detail": f"{prefix}.0/24 這次沒有掃到任何機器，這台的存活無從確認",
                "action": "把這個網段加進掃描目標；掃不到就永遠沒人知道它還在不在"}
    return {"key": "lost", "light": "machine", "level": BAD, "label": "失聯",
            "basis": f"最近一次掃描（{ctx['scan_time']}）的結果",
            "detail": "登記在案，但這次掃描沒有回應",
            "action": "確認機器是否已下線；真的退役就改資產狀態，不要留在在用清單裡"}


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
    if status == "expired":
        return {"key": "eos", "light": "machine", "level": BAD, "label": "版本已過保",
                "basis": f"EOS 資料表（{canon} → {hit.get('eos_date')}）",
                "detail": f"{canon} 官方已停止支援",
                "action": "排升級或申請例外；過保機器出事沒有原廠支援"}
    if status == "upcoming":
        return {"key": "eos", "light": "machine", "level": WARN, "label": "一年內過保",
                "basis": f"EOS 資料表（{canon} → {hit.get('eos_date')}）",
                "detail": f"{canon} 於 {hit.get('eos_date')} 停止支援",
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
    key = (_s(row["hostname"]).lower(), _s(row["ip"]))
    if not key[0] or not key[1] or key not in ctx["dup_keys"]:
        return None
    return {"key": "duplicate", "light": "data", "level": BAD, "label": "重複登記",
            "basis": "同主機名＋IP 的其他資產",
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

        def light_of(which: str) -> str:
            lv = [i["level"] for i in issues if i["light"] == which]
            return BAD if BAD in lv else (WARN if WARN in lv else OK)

        out[row["id"]] = {
            "machine": light_of("machine"),
            "data": light_of("data"),
            "issues": issues,
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
    }
