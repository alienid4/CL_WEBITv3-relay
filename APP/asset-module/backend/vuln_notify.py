"""B-19 弱點到期自動寄信。

使用者 2026-09-22 當面拍板：
  * **每週一 08:00** 寄一次、**每月 1 號 08:00** 寄一次
  * 寄「弱點修補期限到期」，門檻 **14 / 30 / 60 天**
  * **收件人＝弱點報告上的負責人**；月報**主管收副本**（負責人在前、主管副本）
  * SMTP 主機／埠／寄件者由管理者在設定頁填（公司是**免認證內部 relay**）
  * **以上每一個值都不可以寫死在程式裡**，存 DB、畫面可改。
    實際值只記在 AI/ 的需求文件裡——**APP/ 是會去識別化外送的路徑**，
    真實 IP 與公司網域不可以出現在這裡（這條是我自己的測試抓到的）。
  * 值是空的就明確報錯，**不准有預設主機、不准悄悄退回去用別的**

收件人怎麼來：**查 B-23 匯入的 `ad_person` 表**，不另做「人名→信箱」對照表。
使用者：「以後我就是 EXCEL 匯入」「第一次匯入後就進 DB，除非我手動更新」。
多做一張對照表會變成**第二份真相**，兩份不一致時沒人知道該信哪個。

設計上最重要的四件（做錯的話這個功能會比沒有更糟）：

1. **門檻只決定「要不要出現在這期的信裡」，不當觸發條件。**
   否則同一筆會在 60/30/14 各寄一次，收信的人直接把整個寄件者設成已讀——功能作廢。
   每筆記 `last_bucket`，**跨進更急的一級才再出現**。

2. **只有寄成功才更新 `last_bucket`。**
   SMTP 失敗下一期會再進同一封。**寧可重複寄也不可以假裝寄過**——
   「以為寄了其實沒寄」是這個功能最壞的結果。

3. **找不到收件人的不可以靜靜跳過。**
   沒有負責人、AD 查無此人、**同名疑慮**、帳號已停用——四種都進「沒人收」那一籃，
   寄給 fallback 並在畫面上看得到。靜靜跳過等於那批弱點永遠沒人知道，
   跟「沒查到當成沒問題」是同一種錯。

4. **同名疑慮不可以亂猜寄給其中一個。** 寄錯人＝把弱點清單送給不相干的人。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta

#: 到期門檻（天）。由急到緩，**順序有意義**：判 bucket 時取最急的那一級。
DEFAULT_BUCKETS = (14, 30, 60)

#: 設定鍵。**全部存 DB，程式碼裡沒有任何預設值**（使用者：不准有預設主機）。
SETTING_KEY = "vuln_notify"

#: 必填欄位。缺任何一個就不准寄，而且錯誤要**指名是哪一項**空的。
REQUIRED = ("smtp_host", "smtp_port", "mail_from")


class SettingsCorrupt(RuntimeError):
    """設定存在但讀不出來。**跟「沒設定」要分開**——兩者的下一步完全不同：
    沒設定是去填，壞掉是去看誰把它寫壞了。"""

STATE_SQL = """
CREATE TABLE IF NOT EXISTS vuln_notify_state (
    fingerprint TEXT PRIMARY KEY,      -- 弱點指紋；判不出來的用 id
    last_bucket INTEGER,               -- 上次通知用的門檻（14/30/60），逾期用 0
    last_sent_at TEXT
)
"""

LOG_SQL = """
CREATE TABLE IF NOT EXISTS vuln_notify_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at     TEXT NOT NULL,
    schedule    TEXT,                  -- weekly / monthly
    recipient   TEXT,                  -- 實際收件地址
    cc          TEXT,                  -- 副本（月報的主管）
    owner_name  TEXT,                  -- 弱點報告上的負責人名字
    subject     TEXT,
    item_count  INTEGER,
    status      TEXT,                  -- sent / failed
    detail      TEXT,                  -- SMTP 回應或錯誤原文
    smtp_host   TEXT,                  -- 出事時要答得出「那天寄到哪去了」
    mail_from   TEXT
)
"""


RUN_SQL = """
CREATE TABLE IF NOT EXISTS vuln_notify_run (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date   TEXT NOT NULL,          -- 這一期的日期（YYYY-MM-DD）
    schedule   TEXT NOT NULL,          -- weekly / monthly / manual
    started_at TEXT NOT NULL,
    config_ok  INTEGER,                -- 「跑得起來」1/0。**不是「寄到了」**：
                                       -- 一封都沒寄成也會是 1，要看 failed。
    sent       INTEGER,
    failed     INTEGER,
    reason     TEXT                    -- 沒寄成的話，原因原文
)
"""

def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(STATE_SQL)
    conn.execute(LOG_SQL)
    conn.execute(RUN_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vnlog_at ON vuln_notify_log(sent_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vnrun_date "
                 "ON vuln_notify_run(run_date, schedule)")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 設定 ───────────────────────────────────────────────────────────────
def load_settings(conn: sqlite3.Connection) -> dict:
    """讀設定。**沒設定就是空的，不給預設值。**

    使用者：「不准有預設主機、不准悄悄退回去用別的」——
    寄錯地方比沒寄更糟，所以寧可整個功能不動。
    """
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                       (SETTING_KEY,)).fetchone()
    if not row:
        return {}
    raw = row["value"] if hasattr(row, "keys") else row[0]
    try:
        return json.loads(raw) or {}
    except (ValueError, TypeError) as exc:
        # **不可以吞成 {}**：那會讓「設定壞掉」跟「從來沒設定過」長得一模一樣，
        # 使用者會照畫面提示重填一次已經填過的東西，而真正的原因沒人看到。
        raise SettingsCorrupt(
            f"寄信設定讀不出來（內容壞掉，不是沒設定）：{type(exc).__name__}: {exc}"
        ) from exc


def save_settings(conn: sqlite3.Connection, cfg: dict) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SETTING_KEY, json.dumps(cfg, ensure_ascii=False)))
    conn.commit()


def check_settings(cfg: dict) -> list[str]:
    """回缺了哪幾項。**要指名**——只說「設定不完整」的話，人得自己一欄一欄找。"""
    label = {"smtp_host": "SMTP 主機", "smtp_port": "SMTP 埠", "mail_from": "寄件者"}
    return [label[k] for k in REQUIRED if not str(cfg.get(k) or "").strip()]


def buckets_of(cfg: dict) -> tuple[int, ...]:
    raw = cfg.get("buckets") or DEFAULT_BUCKETS
    try:
        return tuple(sorted({int(x) for x in raw}))
    except (ValueError, TypeError):
        return DEFAULT_BUCKETS


# ── 班表 ───────────────────────────────────────────────────────────────
def schedules_due(today: date) -> list[str]:
    """今天要寄哪些班表。

    **週一剛好是 1 號時只回 monthly**，不是兩封。
    月報是週報的超集（含 14/30/60＋逾期＋單位統計），寄兩封只會讓人覺得系統在洗版。
    """
    if today.day == 1:
        return ["monthly"]
    if today.weekday() == 0:          # 週一
        return ["weekly"]
    return []


def bucket_for(days_left: int | None, buckets: tuple[int, ...]) -> int | None:
    """這筆該落在哪一級。逾期回 0；還很遠回 None（這期不用提）。

    取**最急**的那一級：剩 12 天時落在 14 而不是 30/60。
    """
    if days_left is None:
        return None
    if days_left < 0:
        return 0
    for b in sorted(buckets):
        if days_left <= b:
            return b
    return None


# ── 收件人 ─────────────────────────────────────────────────────────────
#: 找不到收件人的四種原因。**都要看得到，不可以靜靜跳過。**
NO_OWNER = "弱點報告沒有填負責人"
NOT_IN_AD = "AD 名單裡查無此人"
AMBIGUOUS = "同名疑慮，有多筆對得上"
DISABLED = "AD 帳號已停用（離職？）"
NO_MAIL = "AD 裡這個人沒有信箱"


def resolve_recipient(conn: sqlite3.Connection, owner_name: str | None) -> tuple[dict | None, str | None]:
    """弱點報告的負責人（**人名**）→ AD 的人（**員編為鍵**）。回 (人, 不能寄的原因)。

    中間這一跳一定會遇到同名——`ad_person.manager_state` 本身就有「同名疑慮」
    這個狀態，就是為此。**同名時回 None＋AMBIGUOUS，不可以挑其中一個寄**：
    寄錯人等於把弱點清單送給不相干的人。
    """
    name = (owner_name or "").strip()
    if not name:
        return None, NO_OWNER
    rows = conn.execute(
        "SELECT * FROM ad_person WHERE TRIM(display_name) = ?", (name,)).fetchall()
    if not rows:
        return None, NOT_IN_AD
    if len(rows) > 1:
        return None, AMBIGUOUS
    p = dict(rows[0])
    if p.get("enabled") == 0:
        # 停用的不可以當收件人，但他名下的弱點**要進「沒人收」那一籃**，不是消失
        return None, DISABLED
    if not (p.get("mail") or "").strip():
        return None, NO_MAIL
    return p, None


def ad_ready(conn: sqlite3.Connection) -> bool:
    """AD 名單匯進來了沒。空的時候要**明確報錯並指路**，不是只說「找不到收件人」。"""
    try:
        return bool(conn.execute("SELECT 1 FROM ad_person LIMIT 1").fetchone())
    except sqlite3.Error:
        return False


# ── 組批次 ─────────────────────────────────────────────────────────────
def _days_left(due: str | None, today: date) -> int | None:
    t = (due or "").strip()[:10]
    if not t:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return (datetime.strptime(t, fmt).date() - today).days
        except ValueError:
            continue
    return None


def collect(conn: sqlite3.Connection, today: date | None = None,
            schedule: str = "weekly") -> dict:
    """挑出這一期要提的弱點，分成「有收件人」與「沒人收」兩邊。

    週報只提最急的（逾期＋14 天內），月報提全部門檻——月報是給人看全貌的，
    週報是給人今天就動手的。兩者混成一樣，週報就會變成沒人看的長清單。
    """
    _ensure(conn)
    today = today or date.today()
    cfg = load_settings(conn)
    buckets = buckets_of(cfg)
    if schedule == "weekly":
        buckets = tuple(b for b in buckets if b <= min(buckets))

    state = {r["fingerprint"]: r["last_bucket"]
             for r in conn.execute("SELECT * FROM vuln_notify_state")}

    batch = conn.execute(
        "SELECT id, file_name, imported_at, imported_by FROM vuln_batch "
        "WHERE status='ready' ORDER BY id DESC LIMIT 1").fetchone()
    if not batch:
        return {"ready": False, "reason": "還沒有匯入過弱點報告", "by_owner": {}, "orphans": []}

    rows = conn.execute(
        "SELECT * FROM vuln_finding WHERE batch_id = ? AND closed = 0", (batch["id"],)).fetchall()

    by_owner: dict[str, list] = {}
    orphans: list[dict] = []
    no_due: list[dict] = []
    for r in rows:
        d = dict(r)
        left = _days_left(d.get("due_date"), today)
        if left is None:
            # **沒有期限的不可以靜默略過**（需求單必守 2）。
            # 這種筆數永遠不會觸發任何門檻，等於系統幫它們決定「不用提醒」——
            # 而真相是「沒有人填期限」，那是要有人去補的事，不是沒事。
            no_due.append(d)
            continue
        b = bucket_for(left, buckets)
        if b is None:
            continue                       # 還很遠，這期不用提
        key = d.get("fingerprint") or f"id:{d['id']}"
        prev = state.get(key)
        # **門檻只決定「要不要出現」，不當觸發**：同一級不重複提，
        # 跨進更急的一級（數字更小，逾期＝0）才再出現。
        if prev is not None and b >= prev:
            continue
        d["_days_left"] = left
        d["_bucket"] = b
        d["_key"] = key
        person, why = resolve_recipient(conn, d.get("owner"))
        if person is not None and not in_scope(cfg, person):
            # 不在通知範圍內（使用者只選了某些單位／某些人）。
            # **不可以就這樣消失**——那批弱點還是存在，只是這個系統不提醒了。
            # 歸進「沒人收」那一籃，代收信箱會看到，畫面上也算得出來。
            person, why = None, "不在通知範圍內（單位／人選設定）"
        if person is None:
            d["_why"] = why
            orphans.append(d)
        else:
            by_owner.setdefault(person["employee_id"], {"person": person, "items": []})
            by_owner[person["employee_id"]]["items"].append(d)
    # 各單位統計（月報用）。**逾期另外算一欄**——「20 筆」跟「20 筆其中 11 筆已逾期」
    # 是兩件完全不同的事，混成一個數字，主管看不出哪個單位在失控。
    dept_stats: dict[str, dict] = {}
    for grp in by_owner.values():
        d = (grp["person"].get("dept_name") or "").strip() or "(未填單位)"
        row = dept_stats.setdefault(d, {"dept": d, "total": 0, "overdue": 0})
        row["total"] += len(grp["items"])
        row["overdue"] += sum(1 for it in grp["items"] if it["_bucket"] == 0)
    if orphans:
        dept_stats["(沒人收)"] = {
            "dept": "(沒人收)", "total": len(orphans),
            "overdue": sum(1 for o in orphans if bucket_for(
                _days_left(o.get("due_date"), today), buckets) == 0)}

    return {"ready": True, "batch_id": batch["id"], "by_owner": by_owner,
            "orphans": orphans, "no_due": no_due, "buckets": buckets, "schedule": schedule,
            "dept_stats": sorted(dept_stats.values(),
                                 key=lambda x: (-x["overdue"], -x["total"], x["dept"])),
            # 信裡固定要帶資料時間：收信的人才分得出「這是今天的報告」還是
            # 「排程照樣在寄，但資料是三個月前那一份」。
            "source": {"file_name": batch["file_name"],
                       "imported_at": batch["imported_at"],
                       "imported_by": batch["imported_by"]}}


# ── 收件範圍：先選單位、再挑人 ─────────────────────────────────────────
#
# 使用者 2026-09-22：「我是架構部，我想選只要通知架構部（人選）」。
# 兩層是刻意的：單位會異動、人也會換，只存人會在組織調整後悄悄漏人，
# 只存單位又挑不了「這個單位只通知這三個」。
#
# **預設是「全部」**——沒設過就是誰都通知。把預設做成「沒有人」的話，
# 剛啟用的系統會安安靜靜一封都不寄，而畫面上看起來一切正常。


def departments(conn: sqlite3.Connection) -> list[dict]:
    """AD 名單裡有哪些單位，各幾個人（給畫面挑）。"""
    _ensure(conn)
    try:
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(TRIM(dept_name), ''), '(未填單位)') AS dept, "
            "COUNT(*) AS n FROM ad_person WHERE enabled = 1 "
            "GROUP BY dept ORDER BY n DESC, dept").fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            # 資料表在、查詢卻炸了＝真的壞了。吞成「沒有人」會讓畫面顯示
            # 「沒有任何單位可挑」，跟「AD 還沒匯入」長得一模一樣。
            raise
        return []          # AD 還沒匯入；畫面另有「AD 名單還沒匯入」的提示
    return [dict(r) for r in rows]


def people_of(conn: sqlite3.Connection, depts: list[str] | None = None) -> list[dict]:
    """某幾個單位裡的人（給畫面挑）。沒給單位就全部。"""
    _ensure(conn)
    sql = ("SELECT employee_id, display_name, mail, dept_name FROM ad_person "
           "WHERE enabled = 1")
    args: list = []
    if depts:
        sql += " AND COALESCE(NULLIF(TRIM(dept_name), ''), '(未填單位)') IN (%s)" % \
               ",".join("?" * len(depts))
        args += list(depts)
    sql += " ORDER BY dept_name, display_name"
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise          # 同上：真的壞了不可以看起來像「這個單位沒有人」
        return []          # AD 還沒匯入


def in_scope(cfg: dict, person: dict) -> bool:
    """這個人在這次的通知範圍裡嗎。

    **人的清單優先**：選了人就照那份名單（使用者是挑了特定幾位）；
    只選單位就整個單位都通知；兩個都沒設＝全部。
    """
    ids = [str(x).strip() for x in (cfg.get("only_people") or []) if str(x).strip()]
    if ids:
        return str(person.get("employee_id") or "").strip() in set(ids)
    depts = [str(x).strip() for x in (cfg.get("only_depts") or []) if str(x).strip()]
    if depts:
        d = (person.get("dept_name") or "").strip() or "(未填單位)"
        return d in set(depts)
    return True


def always_cc(cfg: dict) -> list[str]:
    """設定裡的「一律副本給」（窗口／主管信箱，逗號分隔）。

    跟月報的主管副本是兩件事：那個是**依人不同**（走 AD 的 manager_mail），
    這個是**全域固定**的窗口。混成同一個欄位的話，換窗口要去改每個人的 AD 資料。
    """
    raw = cfg.get("always_cc") or ""
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    else:
        parts = str(raw).replace("；", ",").replace(";", ",").replace("、", ",").split(",")
    out, seen = [], set()
    for x in parts:
        a = x.strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


def _subject(cfg: dict, schedule: str, name: str, n: int, today: date) -> str:
    kind = "月報" if schedule == "monthly" else "週報"
    prefix = (cfg.get("subject_prefix") or "").strip()
    return f"{prefix}[弱點修補提醒·{kind}] {name} 共 {n} 筆（{today:%Y-%m-%d}）"


def _footer(source: dict | None, no_due: list | None) -> list[str]:
    """每一封信都要有的結尾。兩件事都是「沒寫出來就會被當成沒事」：

    * **資料時間**——排程照樣在寄，但資料可能是三個月前那一份。
    * **沒有期限、無法提醒的筆數**——它們不會觸發任何門檻，
      不寫出來就等於系統替它們決定了「不用管」。
    """
    out = ["", "─" * 30]
    if source:
        out.append(f"資料來源：{source.get('file_name') or '(未知檔名)'}"
                   f"｜匯入時間 {source.get('imported_at') or '—'}"
                   f"｜匯入者 {source.get('imported_by') or '—'}")
    n = len(no_due or [])
    if n:
        out.append(f"**沒有期限、無法提醒：{n} 筆**——這些沒有填修補期限，"
                   f"不會出現在任何一期的提醒裡，請到系統的弱點報表補上期限。")
    return out


def _dept_lines(stats: list[dict] | None) -> list[str]:
    """月報的各單位統計。主管會看到全部單位（使用者 2026-09-21 已同意）。

    排序刻意用「逾期多的在最上面」而不是總數——要看的是哪裡在失控，
    總數大但都還沒到期的單位，不是這封信要處理的事。
    """
    if not stats:
        return []
    out = ["", "各單位統計（逾期多的排前面）：",
           f"  {'單位':<14}{'本期筆數':>8}{'其中已逾期':>10}"]
    for r in stats:
        out.append(f"  {r['dept']:<14}{r['total']:>8}{r['overdue']:>10}")
    return out


def _body(items: list[dict], schedule: str, today: date,
          source: dict | None = None, no_due: list | None = None,
          dept_stats: list[dict] | None = None) -> str:
    lines = [f"以下弱點即將到期或已逾期，請安排修補（{today:%Y-%m-%d}）：", ""]
    for it in sorted(items, key=lambda x: (x["_days_left"] is None, x["_days_left"])):
        left = it["_days_left"]
        when = f"已逾期 {-left} 天" if left is not None and left < 0 else f"剩 {left} 天"
        lines.append(f"· {it.get('host') or '(未知主機)'}｜{it.get('name') or ''}"
                     f"｜{it.get('severity') or ''}｜到期 {it.get('due_date') or '—'}（{when}）")
    lines += ["", "—— 這封信由資訊戰情室自動寄出。清單以系統上最新一次匯入的弱點報告為準。"]
    if schedule == "monthly":
        lines.append("（月報：含 14／30／60 天內到期與已逾期；週報只提最急的那一批）")
        lines += _dept_lines(dept_stats)
    lines += _footer(source, no_due)
    return "\n".join(lines)


def _zero_body(schedule: str, today: date, source: dict | None,
               no_due: list | None) -> str:
    """**本期 0 筆也要寄**（需求單必守 1）。

    不寄的話，收信的人分不出「這週真的沒事」與「排程死了／設定被改壞了」。
    這封信的價值不在內容，在**它有出現**——沉默沒有辦法證明系統還活著。
    """
    kind = "月報" if schedule == "monthly" else "週報"
    lines = [f"弱點修補{kind}（{today:%Y-%m-%d}）：**本期 0 筆**。", "",
             "這一期沒有任何弱點落在提醒門檻內。",
             "這封信是刻意寄的——沒有信不代表沒事，也可能是排程沒跑起來，",
             "所以就算 0 筆也會寄一封，讓你知道系統今天有跑。"]
    lines += _footer(source, no_due)
    return "\n".join(lines)


# ── 寄送 ───────────────────────────────────────────────────────────────
def _send_smtp(cfg: dict, to: str, cc: list[str], subject: str, body: str) -> tuple[bool, str]:
    """實際寄一封。回 (成功, 說明)。

    公司是**免認證內部 relay**，所以不做登入；但主機／埠／寄件者一律從設定讀，
    程式碼裡沒有任何預設值——寄錯地方比沒寄更糟。
    """
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = cfg["mail_from"]
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=30) as s:
            refused = s.send_message(msg)
        if refused:
            # 部分收件人被退：**不算成功**。算成功的話那些人永遠收不到也沒人知道。
            return False, f"部分收件人被退回：{refused}"
        return True, "已送交 SMTP"
    except Exception as exc:                      # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def run(conn: sqlite3.Connection, today: date | None = None,
        schedule: str | None = None, dry_run: bool = False) -> dict:
    """跑一期。dry_run=True 只組信不寄（設定頁的「預覽」用）。

    **設定不齊就整個不動**，並指名缺哪一項；**AD 沒匯入也不動**，並指路到資料匯入頁。
    這兩種都不可以「先寄能寄的」——那會讓人以為功能正常，其實一半的人收不到。
    """
    _ensure(conn)
    today = today or date.today()
    try:
        cfg = load_settings(conn)
    except SettingsCorrupt as exc:
        return {"ok": False, "reason": str(exc), "sent": 0, "failed": 0}

    missing = check_settings(cfg)
    if missing:
        return {"ok": False, "reason": f"寄信設定還缺：{'、'.join(missing)}。"
                                       "請到「7-3 系統設定」（/settings）的『寄信設定』分頁填好再啟用。",
                "sent": 0, "failed": 0}
    if not ad_ready(conn):
        return {"ok": False, "reason": "AD 名單還沒匯入，查不到任何人的信箱。"
                                       "請先到「6 資料匯入」（/import）匯入 AD 名單。",
                "sent": 0, "failed": 0}

    todo = [schedule] if schedule else schedules_due(today)
    if not todo:
        return {"ok": True, "reason": "今天不是寄送日（週一或每月 1 號）",
                "sent": 0, "failed": 0}

    result = {"ok": True, "sent": 0, "failed": 0, "orphan_items": 0, "details": []}
    for sched in todo:
        data = collect(conn, today, sched)
        if not data.get("ready"):
            result["ok"] = False
            result["reason"] = data.get("reason")
            return result

        for _eid, grp in data["by_owner"].items():
            p, items = grp["person"], grp["items"]
            to = p["mail"]
            # **月報才給主管副本**（使用者拍板：負責人在前、主管副本）。
            # 主管是**依人不同**，走 AD 的 manager_mail，不做全域主管設定。
            cc = list(always_cc(cfg))
            if sched == "monthly" and (p.get("manager_mail") or "").strip():
                mgr = p["manager_mail"].strip()
                # 同一個信箱不要出現兩次——收信的人會以為系統壞了。
                if mgr.lower() not in {c.lower() for c in cc}:
                    cc.append(mgr)
            subject = _subject(cfg, sched, p.get("display_name") or to, len(items), today)
            body = _body(items, sched, today, data.get("source"), data.get("no_due"),
                         data.get("dept_stats"))

            if dry_run:
                result["details"].append({"to": to, "cc": cc, "subject": subject,
                                          "count": len(items), "status": "預覽"})
                continue

            ok, detail = _send_smtp(cfg, to, cc, subject, body)
            conn.execute(
                "INSERT INTO vuln_notify_log (sent_at, schedule, recipient, cc, owner_name, "
                "subject, item_count, status, detail, smtp_host, mail_from) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), sched, to, ", ".join(cc), p.get("display_name"), subject,
                 len(items), "sent" if ok else "failed", detail,
                 cfg["smtp_host"], cfg["mail_from"]))
            if ok:
                result["sent"] += 1
                # **只有寄成功才更新 bucket**。失敗的下一期會再進同一封——
                # 寧可重複寄也不可以假裝寄過。
                for it in items:
                    conn.execute(
                        "INSERT INTO vuln_notify_state (fingerprint, last_bucket, last_sent_at) "
                        "VALUES (?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET "
                        "last_bucket = excluded.last_bucket, last_sent_at = excluded.last_sent_at",
                        (it["_key"], it["_bucket"], _now()))
            else:
                result["failed"] += 1
            result["details"].append({"to": to, "cc": cc, "subject": subject,
                                      "count": len(items),
                                      "status": "sent" if ok else "failed", "detail": detail})

        # 「沒人收」那一籃：**照樣寄出去**給 fallback，並在信裡寫明原因分類。
        orphans = data["orphans"]
        result["orphan_items"] = len(orphans)
        fb = (cfg.get("fallback_to") or "").strip()
        if orphans and fb and not dry_run:
            by_why: dict[str, list] = {}
            for o in orphans:
                by_why.setdefault(o["_why"], []).append(o)
            lines = [f"以下 {len(orphans)} 筆弱點**找不到收件人**，需要有人指派：", ""]
            for why, its in by_why.items():
                lines.append(f"【{why}】{len(its)} 筆")
                for it in its[:20]:
                    lines.append(f"  · {it.get('host') or '(未知主機)'}"
                                 f"｜負責人「{it.get('owner') or '(空白)'}」"
                                 f"｜{it.get('name') or ''}｜到期 {it.get('due_date') or '—'}")
                if len(its) > 20:
                    lines.append(f"  …其餘 {len(its) - 20} 筆請到系統查看")
                lines.append("")
            lines += _footer(data.get("source"), data.get("no_due"))
            subject = _subject(cfg, sched, "無法決定收件人", len(orphans), today)
            occ = always_cc(cfg)
            ok, detail = _send_smtp(cfg, fb, occ, subject, "\n".join(lines))
            conn.execute(
                "INSERT INTO vuln_notify_log (sent_at, schedule, recipient, cc, owner_name, "
                "subject, item_count, status, detail, smtp_host, mail_from) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), sched, fb, ", ".join(occ), "(沒人收)", subject, len(orphans),
                 "sent" if ok else "failed", detail, cfg["smtp_host"], cfg["mail_from"]))
            result["sent" if ok else "failed"] += 1
        elif orphans and not fb:
            # **沒設 fallback 不可以靜靜吞掉**——那批弱點會永遠沒人知道
            result["details"].append({
                "to": "(未設定)", "cc": [], "count": len(orphans), "status": "failed",
                "subject": "沒人收的弱點",
                "detail": "有找不到收件人的弱點，但沒有設定「查無收件人時轉寄給誰」，"
                          "這批沒有寄出去。請到設定頁補上。"})
            result["failed"] += 1

        # **本期 0 筆也要寄一封**（需求單必守 1）。
        # 沉默沒有辦法證明系統還活著：收信的人分不出「這週真的沒事」與
        # 「排程死了／設定被改壞了」。寄給「查無收件人時轉寄給誰」那個信箱
        # （負責人這一期沒有任何一筆，寄給他們反而是噪音）。
        if not data["by_owner"] and not orphans:
            result["zero"] = True
            subject = _subject(cfg, sched, "本期 0 筆", 0, today)
            body = _zero_body(sched, today, data.get("source"), data.get("no_due"))
            if dry_run:
                result["details"].append({"to": fb or "(未設定)", "cc": [],
                                          "subject": subject, "count": 0, "status": "預覽"})
            elif fb:
                zcc = always_cc(cfg)
                ok, detail = _send_smtp(cfg, fb, zcc, subject, body)
                conn.execute(
                    "INSERT INTO vuln_notify_log (sent_at, schedule, recipient, cc, owner_name, "
                    "subject, item_count, status, detail, smtp_host, mail_from) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (_now(), sched, fb, ", ".join(zcc), "(本期 0 筆)", subject, 0,
                     "sent" if ok else "failed", detail, cfg["smtp_host"], cfg["mail_from"]))
                result["sent" if ok else "failed"] += 1
                result["details"].append({"to": fb, "cc": zcc, "subject": subject, "count": 0,
                                          "status": "sent" if ok else "failed", "detail": detail})
            else:
                # 一樣**不可以靜靜跳過**：沒設收件人就講出來，
                # 否則畫面會顯示「這期跑完、寄了 0 封」，看起來跟正常沒兩樣。
                result["details"].append({
                    "to": "(未設定)", "cc": [], "count": 0, "status": "failed",
                    "subject": subject,
                    "detail": "本期 0 筆的通知沒有寄出去——還沒設定「查無收件人時轉寄給誰」。"
                              "沒有這封信，沒收到信的人分不出是真的沒事還是排程死了。"})
                result["failed"] += 1

        result["no_due_items"] = len(data.get("no_due") or [])
        result["source"] = data.get("source")
    conn.commit()
    return result


def logs(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """寄送紀錄。出事時要答得出「那天到底寄到哪去了」。"""
    _ensure(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM vuln_notify_log ORDER BY id DESC LIMIT ?", (limit,))]


# ── 排程：到點自己跑 ───────────────────────────────────────────────────
#
# 使用者拍板「每週一 08:00、每月 1 號 08:00」。時間點可以改，所以存設定；
# 但 **enabled 預設是關的**——設定還沒填完的系統不可以自己開始寄信。
DEFAULT_SEND_TIME = "08:00"

#: 這一期失敗了，隔多久再試一次。08:00 剛好 SMTP 在維護就整週不寄，
#: 是「靜靜跳過」的另一種面貌；但每分鐘重試又會洗版。折衷成每小時一次。
RETRY_MINUTES = 60

#: 重試到幾點為止。過了下班時間才寄出的到期通知沒有人會當天處理，
#: 而且會讓人以為系統壞了。當天補不成就留在紀錄上，下一期會再進同一封。
RETRY_UNTIL_HOUR = 18


def send_time_of(cfg: dict) -> tuple[int, int]:
    """設定裡的寄送時刻。壞掉就退回 08:00——**不可以因為時間格式打錯就整個不寄**。"""
    raw = str(cfg.get("send_time") or DEFAULT_SEND_TIME).strip()
    try:
        hh, mm = raw.split(":")
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (ValueError, AttributeError):
        pass
    return 8, 0


def _attempts(conn: sqlite3.Connection, run_date: str, schedule: str) -> list[dict]:
    _ensure(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM vuln_notify_run WHERE run_date = ? AND schedule = ? "
        "ORDER BY id", (run_date, schedule))]


def mark_run(conn: sqlite3.Connection, run_date: str, schedule: str, result: dict,
             now: datetime | None = None) -> None:
    """**每一次嘗試都記，成功失敗都記。**

    只記成功的話，「今天寄了嗎」這個問題在出事當下答不出來——
    沒有紀錄會被讀成「沒到點」，而真相可能是「跑了五次都被 SMTP 拒絕」。
    """
    _ensure(conn)
    conn.execute(
        "INSERT INTO vuln_notify_run (run_date, schedule, started_at, config_ok, sent, failed, reason)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_date, schedule, (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
         1 if result.get("ok") else 0, int(result.get("sent") or 0),
         int(result.get("failed") or 0), result.get("reason")))
    conn.commit()


def is_due(conn: sqlite3.Connection, now: datetime) -> list[str]:
    """現在這一刻該跑哪些班表。沒到點、已經成功跑過、剛失敗還在冷卻 → 都回空。

    關掉就回空，而且**是看設定裡明確的 enabled**：沒設定過＝關著。
    寧可讓人多按一次開關，也不要一個剛匯完 AD 的系統自己開始對全公司寄信。
    """
    try:
        cfg = load_settings(conn)
    except SettingsCorrupt:
        return []                      # 設定壞掉不是「到點」；run() 會在手動觸發時講清楚
    if not cfg.get("enabled"):
        return []

    h, m = send_time_of(cfg)
    if (now.hour, now.minute) < (h, m):
        return []
    if now.hour >= RETRY_UNTIL_HOUR:
        return []

    run_date = now.strftime("%Y-%m-%d")
    due = []
    for s in schedules_due(now.date()):
        tries = _attempts(conn, run_date, s)
        # **config_ok 不等於寄到了**：它只說「設定沒問題、這期跑得完」，
        # 一封都沒寄成也會是 ok。拿它當「這期做完了」，等於 SMTP 一掛就整週不補寄——
        # 正是這個功能要防的那件事。要「跑完而且沒有任何一封失敗」才算數。
        # 部分失敗的情況會重跑，但寄成功的那幾筆已經更新 last_bucket、不會再被收進來，
        # 所以補的只有真正沒寄到的人。
        if any(t["config_ok"] and not t["failed"] for t in tries):
            continue
        if tries:
            last = datetime.strptime(tries[-1]["started_at"], "%Y-%m-%d %H:%M:%S")
            if now - last < timedelta(minutes=RETRY_MINUTES):
                continue               # 還在冷卻，等下一輪
        due.append(s)
    return due


def tick(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    """排程器每分鐘叫一次。回這一輪實際跑了哪些（沒到點就是空清單）。"""
    now = now or datetime.now()
    out = []
    for s in is_due(conn, now):
        r = run(conn, now.date(), s)
        mark_run(conn, now.strftime("%Y-%m-%d"), s, r, now)
        out.append({"schedule": s, **r})
    return out


def last_runs(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    """最近幾期的執行紀錄。畫面要能分辨「寄了 0 封」與「根本沒跑」——
    只給數字不給依據，等於要人用猜的。"""
    _ensure(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM vuln_notify_run ORDER BY id DESC LIMIT ?", (limit,))]
