"""收集帳號統一成 `webit3sc` 的遷移：**探測**與**狀態**（唯讀，不改任何機器）。

使用者 2026-09-24 拍板「還是統一都叫 webit3sc」。設計見
`AI/收集帳號遷移設計_2026-09-24.md`，這一支做設計的第 1~3 步：
per-host 帳號解析、實測探測、狀態統計。**不切換任何一台、不刪任何帳號。**

## 為什麼狀態一定要用「實測」而不是「設定值」

2026-09-23 部署 v1.365.0 當下，`health_probe` 讀程式常數（新名）、
其餘讀 DB 設定（舊名），一台好好的 221 立刻顯示成「連不上・未完整檢查 0/9」。

**設定說 A、機器上是 B**——那就是整件事的坑。所以這裡兩個帳號各連一次，
把離開碼與 stderr 原文記下來，狀態由那個結果決定。

## 三種「連不上」要分得開

兩個帳號都進不去，**第二種解釋是機器根本不通**（關機／網路／防火牆），
那跟「新帳號還沒佈上去」要做的事完全不同。不分層的話，
一批關機的機器會被算成「遷移失敗」，完成率被拉低而且沒人知道該修哪個——
那又是一次「把別的問題顯示成這個問題」。
"""
from __future__ import annotations

import manage_state as ms

#: 遷移狀態。刻意用中文，直接顯示在畫面上。
DONE = "已完成"          # 只有新帳號進得去
SWITCHABLE = "可切換"    # 新舊都進得去 -> 新帳號驗過了，可以切這台
PENDING = "待納管"       # 只有舊帳號進得去 -> 還沒佈新帳號
UNKNOWN = "無法判斷"     # 機器不通，**不是遷移失敗**
FAILED = "兩個都進不去"  # 機器通、但兩個帳號都被拒 -> 這才是真的要查
NA = "不適用"            # Windows 走 WinRM，沒有這個帳號

#: 探測用的指令。最小、唯讀、任何 Unix 都有。
PROBE_CMD = "id"

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS collect_account_probe (
    asset_serial TEXT PRIMARY KEY,
    ip           TEXT,
    platform     TEXT,
    new_account  TEXT,
    new_ok       INTEGER,      -- 1/0；NULL = 沒試
    new_err      TEXT,
    old_account  TEXT,
    old_ok       INTEGER,
    old_err      TEXT,
    host_alive   INTEGER,      -- 機器本身通不通（分辨「帳號問題」與「機器不通」）
    state        TEXT,
    probed_at    TEXT
)
"""


def _ensure(conn) -> None:
    conn.execute(TABLE_SQL)
    conn.commit()


def account_for_host(conn, asset_serial: str | None, platform: str = "linux") -> str:
    """這一台要用哪個收集帳號。**每台一個答案**，這是整份遷移設計的支點。

    `hardware.collect_account` 有值就用它，NULL 就沿用全域設定。

    沒有這一欄就只能全域一次翻，而全域一次翻＝2026-09-23 那個
    「好好的機器變成 0/9」的機隊放大版。有了它：驗過的那一台才寫新帳號，
    其餘完全不受影響；**全域設定最後才翻，而那時候翻不翻已經不影響任何一台。**
    """
    if asset_serial:
        try:
            row = conn.execute(
                "SELECT collect_account FROM hardware WHERE asset_serial = ?",
                (asset_serial,)).fetchone()
            v = (row["collect_account"] or "").strip() if row else ""
            if v:
                return v
        except Exception:      # noqa: BLE001 - 舊 DB 還沒有這欄時照走全域，不要炸
            pass
    return ms.get_collect_account(conn, platform)


def classify(new_ok, old_ok, alive: bool, platform: str) -> str:
    """兩次探測結果 -> 狀態。

    ⚠️ `alive` 是用來把「機器不通」從「帳號沒佈好」裡拆出來的。
    兩者在畫面上都是「連不上」，但要做的事完全不同：
    前者去看機器，後者去重新納管。
    """
    if platform == "windows":
        return NA
    if new_ok:
        return DONE if not old_ok else SWITCHABLE
    if old_ok:
        return PENDING
    return FAILED if alive else UNKNOWN


def probe_one(conn, asset_serial: str, ip: str, platform: str,
              key_path: str | None = None, _probe=None) -> dict:
    """對一台機器，用新帳號與舊帳號各試一次 `id`。**唯讀，不改任何東西。**

    回傳落庫後的那一列。`_probe(ip, account, key)` 可注入（測試不碰網路）。
    """
    from db import _now_local

    _ensure(conn)
    key_path = key_path or ms.COLLECTOR_KEY_DEFAULT
    new_acct = ms.READONLY_ACCOUNT
    old_acct = ms.LEGACY_LINUX_ACCOUNT

    if platform == "windows":
        rec = {"new_ok": None, "new_err": None, "old_ok": None, "old_err": None,
               "alive": None, "state": NA}
    else:
        probe = _probe or _default_probe
        n_ok, n_err = probe(ip, new_acct, key_path)
        o_ok, o_err = probe(ip, old_acct, key_path)
        # 只有兩個都失敗時才需要知道機器本身通不通——通的話就不用多敲一次。
        alive = True if (n_ok or o_ok) else _host_alive(ip)
        rec = {"new_ok": 1 if n_ok else 0, "new_err": n_err,
               "old_ok": 1 if o_ok else 0, "old_err": o_err,
               "alive": 1 if alive else 0,
               "state": classify(n_ok, o_ok, alive, platform)}

    conn.execute(
        "INSERT INTO collect_account_probe (asset_serial, ip, platform, new_account, "
        "new_ok, new_err, old_account, old_ok, old_err, host_alive, state, probed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(asset_serial) DO UPDATE SET ip=excluded.ip, platform=excluded.platform, "
        "new_account=excluded.new_account, new_ok=excluded.new_ok, new_err=excluded.new_err, "
        "old_account=excluded.old_account, old_ok=excluded.old_ok, old_err=excluded.old_err, "
        "host_alive=excluded.host_alive, state=excluded.state, probed_at=excluded.probed_at",
        (asset_serial, ip, platform, new_acct, rec["new_ok"], rec["new_err"],
         old_acct, rec["old_ok"], rec["old_err"], rec["alive"], rec["state"],
         _now_local()))
    conn.commit()
    return {"asset_serial": asset_serial, "ip": ip, "platform": platform, **rec}


def _default_probe(ip: str, account: str, key_path: str):
    """真的連一次。回 (通不通, 錯誤原文)。**只跑 `id`，什麼都不改。**"""
    runner = ms.SSHRunner(key_path, account=account, timeout=8)
    out = runner(ip, PROBE_CMD)
    rec = runner.trace.get(ip) or {}
    if out.strip() and rec.get("ok"):
        return True, None
    err = rec.get("transport_error") or ""
    if not err:
        for sm in rec.get("samples") or []:
            if sm.get("stderr"):
                err = sm["stderr"]
                break
    return False, (err or "沒有輸出，也沒有錯誤訊息")[:300]


def _host_alive(ip: str) -> bool:
    """機器本身通不通。**沿用既有的 host_ping，不自己寫第二套存活判定。**

    host_ping.verdict() 的第一個回傳值是 alive（分不出來時是 None）。
    None 一律當成「不活」——這裡的用途是把 UNKNOWN 跟 FAILED 分開，
    分不出來就該落到 UNKNOWN（保守），不可以誤報成「機器好好的、是帳號問題」。
    """
    try:
        import host_ping

        alive, _why, _lvl = host_ping.verdict(host_ping.icmp(ip), host_ping.tcp(ip))
        return alive is True
    except Exception:      # noqa: BLE001 - 探不出來就當「不知道」，交給呼叫端標 UNKNOWN
        return False


def summary(conn) -> dict:
    """四個數字＋每個都點得進去。

    ⚠️ **「無法判斷」不算進遷移失敗**，也不算進分母的「該遷移」那一塊——
    機器不通是另一件事，混進來會讓完成率變成一個沒有意義的數字。
    """
    _ensure(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT p.*, h.hostname FROM collect_account_probe p "
        "LEFT JOIN hardware h ON h.asset_serial = p.asset_serial").fetchall()]
    buckets: dict[str, list] = {DONE: [], SWITCHABLE: [], PENDING: [],
                                UNKNOWN: [], FAILED: [], NA: []}
    for r in rows:
        buckets.setdefault(r.get("state") or UNKNOWN, []).append(r)
    need = len(buckets[DONE]) + len(buckets[SWITCHABLE]) + len(buckets[PENDING]) \
        + len(buckets[FAILED])
    return {
        "target_account": ms.READONLY_ACCOUNT,
        "legacy_account": ms.LEGACY_LINUX_ACCOUNT,
        "counts": {k: len(v) for k, v in buckets.items()},
        # 分母講清楚是哪些，不要只給一個百分比
        "denominator": need,
        "denominator_text": (
            f"完成率的分母是 {need} 台＝已完成＋可切換＋待納管＋兩個都進不去。"
            f"「{UNKNOWN}」{len(buckets[UNKNOWN])} 台（機器不通）與「{NA}」"
            f"{len(buckets[NA])} 台（Windows 走 WinRM）**不算在內**——"
            "機器不通是另一件事，算進來只會讓完成率變成沒有意義的數字。"),
        "done_pct": round(len(buckets[DONE]) / need * 100, 1) if need else None,
        "probed": len(rows),
    }


def detail(conn, state: str | None = None) -> list[dict]:
    """下鑽：每台的證據（幾點測的、兩個帳號各自的結果與錯誤原文）。"""
    _ensure(conn)
    sql = ("SELECT p.*, h.hostname FROM collect_account_probe p "
           "LEFT JOIN hardware h ON h.asset_serial = p.asset_serial")
    args: tuple = ()
    if state:
        sql += " WHERE p.state = ?"
        args = (state,)
    sql += " ORDER BY p.state, p.ip"
    out = []
    for r in conn.execute(sql, args).fetchall():
        d = dict(r)
        d["why"] = _why(d)
        out.append(d)
    return out


def _why(d: dict) -> str:
    """這一台為什麼是這個狀態。**講事實，不要只給狀態字。**"""
    st = d.get("state")
    if st == NA:
        return "Windows 走 WinRM，沒有 SSH 收集帳號這回事，不列入遷移。"
    if st == DONE:
        return f"新帳號 {d.get('new_account')} 連得上，舊帳號已不存在或已停用——這台完成了。"
    if st == SWITCHABLE:
        return (f"新舊帳號都連得上。新帳號已經驗過收得到，**可以切這一台的設定**；"
                f"切完驗收沒問題，再另外按「移除舊帳號」。")
    if st == PENDING:
        return (f"只有舊帳號 {d.get('old_account')} 連得上，新帳號還沒佈上去。"
                "要重新納管才會有新帳號——**不要先翻設定**，翻了這台立刻收不到。")
    if st == UNKNOWN:
        return ("**機器本身不通**（關機／網路／防火牆），所以判斷不了帳號狀態。"
                "這不是遷移失敗，先去看機器。")
    if st == FAILED:
        return (f"機器是通的，但兩個帳號都被拒——新：{d.get('new_err') or '（無訊息）'}"
                f"｜舊：{d.get('old_err') or '（無訊息）'}")
    return "還沒探測過這一台。"


# ═══════════════════════════════════════════════════════════════════
# 第 4 步：切換這一台
# ═══════════════════════════════════════════════════════════════════

SWITCH_SQL = """
CREATE TABLE IF NOT EXISTS collect_account_switch (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_serial  TEXT,
    ip            TEXT,
    from_account  TEXT,        -- 切換前用的（NULL 代表當時走全域設定）
    to_account    TEXT,
    switched_at   TEXT,
    switched_by   TEXT,
    before_counts TEXT,        -- JSON：切換前四類收集各收到幾筆
    note          TEXT
)
"""


def _counts_now(conn, asset_serial: str) -> dict:
    """這台目前四類收集各有幾筆。

    切換前後都要留一份——**沒有這個對照，數字變大跟資料跑掉在畫面上長得一樣**。
    公司那八台 AIX 現在是「假成功」（拿錯帳號卻顯示收到 2 筆），
    遷移後才會真的有資料，使用者會看到數字暴增。
    """
    def one(sql, args):
        try:
            return conn.execute(sql, args).fetchone()[0] or 0
        except Exception:      # noqa: BLE001 - 少一個數字不可以害切換失敗
            return None

    return {
        "服務": one("SELECT COUNT(*) FROM host_service WHERE asset_serial = ? "
                    "AND gone_at IS NULL", (asset_serial,)),
        "軟體": one("SELECT COUNT(*) FROM host_package WHERE asset_serial = ? "
                    "AND gone_at IS NULL", (asset_serial,)),
        "帳號": one("SELECT COUNT(*) FROM host_account WHERE asset_serial = ? "
                    "AND gone_at IS NULL", (asset_serial,)),
        "硬體規格": one("SELECT COUNT(*) FROM host_spec WHERE asset_serial = ?",
                        (asset_serial,)),
    }


def switch_one(conn, asset_serial: str, by: str | None = None,
               _probe=None) -> dict:
    """把這一台的收集帳號切成新的。**只切這一台。**

    ⚠️ **切換前會重新探測這一台**，而且只有當下仍然是「可切換」才切。

    為什麼不信看板上的狀態：那是上一次探測的結果，可能已經過期。
    拿過期的「可切換」去切，那台就立刻收不到——
    2026-09-23 那個「好好的機器變成 0/9」正是這個形狀（設定與實況不一致）。
    多花兩次 SSH 換掉這個風險，划算。
    """
    import json

    from db import _now_local

    _ensure(conn)
    conn.execute(SWITCH_SQL)

    row = conn.execute(
        "SELECT ip, collect_account FROM hardware WHERE asset_serial = ?",
        (asset_serial,)).fetchone()
    if row is None:
        raise ValueError(f"資產編號 {asset_serial} 在資產表裡找不到")
    ip = (row["ip"] or "").strip()
    if not ip:
        raise ValueError(f"{asset_serial} 沒有 IP，切了也連不上")

    import manage_state as _ms

    platform = _ms.collect_platform_for(conn, ip, asset_serial)
    fresh = probe_one(conn, asset_serial, ip, platform, _probe=_probe)
    if fresh["state"] != SWITCHABLE:
        raise ValueError(
            f"這一台現在的狀態是「{fresh['state']}」，不是「{SWITCHABLE}」，所以不切。"
            f"（切換前會重新探測一次，不信上一次的結果——上一次的可能已經過期，"
            f"拿過期的狀態去切，這台會立刻收不到。）依據：{_why(dict(fresh, **{'state': fresh['state']}))}")

    before = _counts_now(conn, asset_serial)
    conn.execute("UPDATE hardware SET collect_account = ? WHERE asset_serial = ?",
                 (READONLY_TARGET(), asset_serial))
    conn.execute(
        "INSERT INTO collect_account_switch (asset_serial, ip, from_account, "
        "to_account, switched_at, switched_by, before_counts, note) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (asset_serial, ip, row["collect_account"], READONLY_TARGET(),
         _now_local(), by, json.dumps(before, ensure_ascii=False),
         "切換前已重新探測，狀態為可切換"))
    conn.commit()
    return {"asset_serial": asset_serial, "ip": ip,
            "to_account": READONLY_TARGET(), "before_counts": before,
            "text": (f"已把 {asset_serial}（{ip}）的收集帳號切成 "
                     f"{READONLY_TARGET()}。**只有這一台改變**，其餘機器不受影響。"
                     "下一步：按一次收集，確認四類都收得到；"
                     "確認沒問題之後，才輪到「移除舊帳號」那一步。")}


def READONLY_TARGET() -> str:
    """目標帳號名。包一層是為了讓測試能改，正式一律是 manage_state 那個常數。"""
    return ms.READONLY_ACCOUNT


def revert_one(conn, asset_serial: str, by: str | None = None) -> dict:
    """把這一台切回「沿用全域設定」。**還原路徑一定要有。**

    切換之後才發現那台收不到時，要能立刻退回去，而不是等人去改資料庫。
    """
    from db import _now_local

    conn.execute(SWITCH_SQL)
    conn.execute("UPDATE hardware SET collect_account = NULL WHERE asset_serial = ?",
                 (asset_serial,))
    conn.execute(
        "INSERT INTO collect_account_switch (asset_serial, from_account, to_account, "
        "switched_at, switched_by, note) VALUES (?,?,?,?,?,?)",
        (asset_serial, READONLY_TARGET(), None, _now_local(), by, "還原：改回沿用全域設定"))
    conn.commit()
    return {"asset_serial": asset_serial,
            "text": f"{asset_serial} 已改回沿用全域設定（{ms.get_collect_account(conn)}）。"}


def switch_history(conn, asset_serial: str | None = None) -> list[dict]:
    """切換紀錄。含切換前的筆數——給「數字突然變大」那一行用。"""
    import json

    conn.execute(SWITCH_SQL)
    sql = "SELECT * FROM collect_account_switch"
    args: tuple = ()
    if asset_serial:
        sql += " WHERE asset_serial = ?"
        args = (asset_serial,)
    sql += " ORDER BY id DESC LIMIT 200"
    out = []
    for r in conn.execute(sql, args).fetchall():
        d = dict(r)
        try:
            d["before_counts"] = json.loads(d["before_counts"] or "{}")
        except (ValueError, TypeError):
            d["before_counts"] = {}
        if d.get("to_account"):
            d["after_counts"] = _counts_now(conn, d["asset_serial"])
            d["text"] = (
                f"這台的收集帳號於 {d['switched_at']} 從 "
                f"{d['from_account'] or '（全域設定）'} 換成 {d['to_account']}；"
                + "；".join(f"{k} 換之前 {v}、換之後 {d['after_counts'].get(k)}"
                            for k, v in (d["before_counts"] or {}).items()))
        out.append(d)
    return out


# ═══════════════════════════════════════════════════════════════════
# 第 5 步：移除舊帳號 —— **只產出指令，系統不代為執行**
# ═══════════════════════════════════════════════════════════════════

def removal_plan(conn) -> dict:
    """哪些機器還留著舊帳號、該下什麼指令。**先列清單，什麼都不做。**

    ## 為什麼系統不自己刪

    刪帳號要 root，而收集帳號是**唯讀非 root**（刻意的，見 manage_state 的說明）。
    要讓系統遠端刪帳號，就得給收集帳號 root 或一條 sudo 白名單——
    **為了一次性的遷移動作，給常駐收集身分刪帳號的權力，是不划算的交換**：
    那條權限會一直留著，而它只會用到一次。

    所以這裡產出**純文字、可讀、可稽核**的指令，由管理員自己看過再執行。
    不做 base64、不做一鍵管道、不落地暫存檔——
    那些手法對應 MITRE 的削弱防禦與混淆，在金融業會被 SOC 當成事件，
    而且執行的人看不懂自己在跑什麼。
    """
    _ensure(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT p.*, h.hostname FROM collect_account_probe p "
        "LEFT JOIN hardware h ON h.asset_serial = p.asset_serial "
        "WHERE p.new_ok = 1 AND p.old_ok = 1").fetchall()]
    items = []
    for r in rows:
        plat = (r.get("platform") or "linux").lower()
        # AIX 用 rmuser，Linux 用 userdel。-r／-p 會一併清掉家目錄與安全屬性，
        # 否則舊的 authorized_keys 會留著——那才是真正要移除的東西。
        cmd = (f"rmuser -p {r['old_account']}" if plat == "aix"
               else f"userdel -r {r['old_account']}")
        items.append({
            "asset_serial": r["asset_serial"], "hostname": r.get("hostname"),
            "ip": r["ip"], "platform": plat, "account": r["old_account"],
            "command": cmd, "probed_at": r.get("probed_at"),
        })
    items.sort(key=lambda d: (d["platform"], d["ip"] or ""))
    return {
        "count": len(items), "items": items,
        "note": ("這些機器的「新帳號已經實測連得上」，舊帳號也還在——所以舊的可以移除了。"
                 "清單裡沒有的機器要嘛新帳號還沒佈好（移除舊的會讓它完全連不進去），"
                 "要嘛舊的已經不在。"),
        "warning": ("⚠️ 系統不會替你執行這些指令。 刪帳號要 root，而收集帳號是唯讀非 root；"
                    "為了一次性的遷移給常駐收集身分刪帳號的權力並不划算——"
                    "那條權限會一直留著，卻只用得到一次。"
                    "請把下面的指令**看過之後**自己在各機器上以 root 執行。"),
    }


def removal_script(conn) -> str:
    """把移除計畫印成一份純文字、可讀、可稽核的指令清單。

    刻意**不是**一支可以直接 `sudo bash` 的腳本：一台一行、看得懂、
    可以只挑幾台執行。要貼給別人跑的東西，讀的人必須看得懂自己在跑什麼。
    """
    plan = removal_plan(conn)
    lines = [
        "# 移除舊的收集帳號（收集帳號已統一為 " + ms.READONLY_ACCOUNT + "）",
        "#",
        "# 這份是**給人看過再執行**的指令清單，不是自動化腳本。",
        "# 每一行請在對應的那台主機上以 root 執行。可以只挑幾台先做。",
        "#",
        "# 下面每一台的新帳號都已經實測連得上（探測時間附在後面），",
        "# 所以移除舊帳號不會讓系統失去存取。",
        "#",
        "# 假設的環境：Linux 為 RHEL/Rocky 系列（userdel），AIX 為 7.2（rmuser）。",
        "# 若你的環境不同，**先確認指令再跑**。",
        "",
    ]
    if not plan["items"]:
        lines.append("# （目前沒有任何一台符合條件：新帳號已驗證且舊帳號仍在）")
        return "\n".join(lines) + "\n"
    cur = ""
    for it in plan["items"]:
        if it["platform"] != cur:
            cur = it["platform"]
            lines.append(f"# ── {cur.upper()} ──")
        host = it["hostname"] or it["asset_serial"]
        lines.append(f"# {host}  {it['ip']}   （探測於 {it['probed_at']}）")
        lines.append(it["command"])
    lines.append("")
    lines.append(f"# 共 {plan['count']} 台。執行完回系統按「重新探測」，")
    lines.append("# 狀態會從「可切換」變成「已完成」。")
    return "\n".join(lines) + "\n"
