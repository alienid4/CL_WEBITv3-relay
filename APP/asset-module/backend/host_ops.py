"""單機維運記錄本（ACT-01 / ACT-05 / ACT-02，2.041.43）。

主機履歷「事件與維運紀錄」頁籤的後端：把這一台機器在各來源留下的事件並列出來（只讀、不合併根因），
再讓人補上「處理措施／已確認根因／改善結果」（新表，改前改後留稽核，更正／作廢不抹除），並能按需產生單機 MD。

## 鐵律（Codex 102/103/104 + 使用者）

* 事件是**算出來的**，不是另存一份：來源是 whatsup_alert_history、nmon_series、incident_event；本模組不建第二套事件來源。
  事件 ID 由來源列的 id 組成（WUG-<history id>-<類別>、NMON-<主機>-<指標>-<起點>、INC-<事件 id>），重算不會變。
* 關聯鍵：WhatsUp／NMON 沒有 asset_id，只有 IP／主機名——一律「對回目前資產登記」才算這台的事件。
  只有「IP 唯一對到這一台、而且主機名也對得上這一台」才計次；IP 對到多台、IP 與主機名指向不同台（含已退役主機）、
  只有 IP 或只有主機名符合 → 列「待核」，**不計次**（避免換 IP／重用 IP 讓新機繼承舊機歷史）。已退役主機不做 IP 關聯。
* 計次：disk 按「這台＋分割區」、memory 另列；同一個告警被持續輪詢／重送（changed）不增加次數；
  告警解除後再出現（new）才算再次獨立發生；不同來源並列、**不跨來源加總**（可能是同一件事）。
  計次與下鑽明細是同一份（counts[*].event_ids 就是明細的 id 清單）。
* 缺資料是「未取得」，不是 0、也不是正常。只有現況快照的來源不回推歷史。涵蓋判斷看「這台＋這個期間」，不看全站。
* 已恢復 ≠ 根因已解決；WhatsUp 的時間是「輪詢到的時間」，不是設備實際發生時間。
* 只有能證明與目前現況相連的最後一段才標「持續中」；沒有解除紀錄的舊段標「結束時間未知」。
* 時間：來源（WhatsUp 輪詢、事件應變）存的是伺服器本地時間，這裡一律用同一個伺服器本地時間（預期 Asia/Taipei），不另外換算。
* 讀取絕不寫庫、不建表、不呼叫會 CREATE／ALTER 的 ensure（NMON 也自己讀）；表不存在＝當作沒有資料。
* 不接模型 API；MD 內排除密碼／金鑰／token／Webhook，並中和 Markdown 連結、圖片與 HTML（不產生會請求外部資源的語法）。
"""
from __future__ import annotations

import bisect
import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta

def _tz_label() -> str:
    """顯示伺服器實際的時區與偏移（不寫死 Asia/Taipei）；預期是 Asia/Taipei。"""
    d = datetime.now().astimezone()
    z = d.strftime("%z") or "+0000"
    return f"伺服器本地時間 {d.tzname()}（UTC{z[:3]}:{z[3:]}；預期 Asia/Taipei）"


PENDING_HINT = "可能是改名或換 IP 的合法歷史；要計入請先把資產登記的主機名／IP 補齊（本系統沒有改名／換 IP 的異動歷程可自動對應）"
FMT = "%Y-%m-%d %H:%M:%S"

PERIODS = {"today": "今天", "week": "本週", "7d": "最近 7 天", "30d": "最近 30 天", "90d": "最近 90 天", "next30": "未來 30 天"}
KIND_LABEL = {"action": "處理措施", "root_cause": "已確認根因", "outcome": "改善結果"}
CAT_LABEL = {"disk": "磁碟", "memory": "記憶體", "cpu": "CPU", "other": "其他告警", "incident": "事件應變"}
SRC_LABEL = {"whatsup": "WhatsUp Gold 告警", "nmon": "NMON 門檻超標", "incident": "事件應變（4.1）"}
MATCH_LABEL = {"ip_name": "IP＋主機名一致", "host": "主機名符合", "scope": "事件範圍涵蓋此機", "relay": "中繼匯入時已對到這台"}
NOTE_MAX = 2000
REASON_MAX = 300
NO_DATA = "未取得"
CLOSE_LABEL = "結案時間，非單機恢復時間"
UNKNOWN_END = "結束時間未知（沒有解除紀錄）"

SQL = [
    """CREATE TABLE IF NOT EXISTS host_ops_note (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        machine_key TEXT NOT NULL, asset_serial TEXT NOT NULL,
        event_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',          -- active／corrected（被新版取代）／voided（作廢）
        supersedes_id INTEGER,
        event_ctx TEXT,                                  -- 寫入當下的事件摘要（事件日後超出期間仍看得出是哪一件）
        created_by TEXT, created_at TEXT NOT NULL,
        voided_by TEXT, voided_at TEXT, void_reason TEXT, client_key TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_host_ops_note_serial ON host_ops_note(asset_serial, event_id)",
    """CREATE TABLE IF NOT EXISTS host_ops_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, note_id INTEGER NOT NULL, op TEXT NOT NULL,
        at TEXT NOT NULL, by TEXT, before_json TEXT, after_json TEXT)""",
]


def ensure_tables(conn: sqlite3.Connection) -> None:
    """只在寫入入口呼叫；讀取路徑絕不呼叫。"""
    for s in SQL:
        conn.execute(s)
    if not any(r[1] == "client_key" for r in conn.execute("PRAGMA table_info(host_ops_note)")):      # 舊庫補欄位
        conn.execute("ALTER TABLE host_ops_note ADD COLUMN client_key TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_host_ops_note_ckey ON host_ops_note(client_key) WHERE client_key IS NOT NULL")


def _table(conn: sqlite3.Connection, name: str) -> bool:
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
    except sqlite3.Error:
        return False


def _now() -> datetime:
    """伺服器本地時間（跟 WhatsUp 輪詢、事件應變寫入的時間同一把尺）。"""
    return datetime.now()


def _p(s) -> datetime | None:
    """解析時間字串成伺服器本地的 naive datetime。含 Z／offset 的先換算到本地時區。"""
    if not s:
        return None
    raw = str(s).strip()
    t = raw.replace("T", " ")
    if t[-1:] in ("Z", "z") or re.search(r"[+-]\d\d:?\d\d$", t[10:]):
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError:
            return None
        return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
    t = t[:19]
    for f in (FMT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, f)
        except ValueError:
            continue
    return None


def _f(d: datetime | None) -> str | None:
    return d.strftime(FMT) if d else None


def _nt(s) -> str | None:
    """來源時間字串 → 統一格式（伺服器本地、YYYY-MM-DD HH:MM:SS）；解析不了就原樣保留。"""
    if s in (None, ""):
        return None
    return _f(_p(s)) or str(s)


def _hostkey(h) -> str:
    import system_stats

    return system_stats.strip_domain(h or "").strip().lower()


# ===================================================================== 期間
def window(period: str, now: datetime | None = None) -> dict:
    """period：today／week／7d／30d／90d／next30，或 <N>d（內部驗證事件用，N≤400）。伺服器本地時間。"""
    now = now or _now()
    p = (period or "30d").strip()
    if p == "today":
        a, b = now.replace(hour=0, minute=0, second=0, microsecond=0), now
    elif p == "week":
        a, b = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0), now
    elif p == "next30":
        a, b = now, now + timedelta(days=30)
    else:
        m = re.fullmatch(r"(\d{1,3})d", p)
        if not m or not 1 <= int(m.group(1)) <= 400:
            raise ValueError("期間要是 今天／本週／最近7天／最近30天／最近90天／未來30天")
        a, b = now - timedelta(days=int(m.group(1))), now
    return {"period": p, "label": PERIODS.get(p, f"最近 {p[:-1]} 天"), "start": _f(a), "end": _f(b), "tz": _tz_label(),
            "future": p == "next30"}


# ===================================================================== 這台機器（對回目前資產登記）
class _Idx:
    """全站 IP／主機名 → 機器代號。host＝現役；host_all＝含已退役（用來辨認『名稱其實是舊機』）。"""

    def __init__(self, conn: sqlite3.Connection):
        import manage_state as ms
        import oncall

        self.groups, _ = oncall._groups(conn)
        self.ip: dict[str, set[str]] = {}
        self.host: dict[str, set[str]] = {}
        self.host_all: dict[str, set[str]] = {}
        for k, g in self.groups.items():
            retired = k.startswith("ret:")
            for r in g:
                hk = _hostkey(r["hostname"])
                if hk:
                    self.host_all.setdefault(hk, set()).add(k)
                if retired:
                    continue
                for ip in ms.ips_of(r["ip"]):
                    self.ip.setdefault(ip, set()).add(k)
                if hk:
                    self.host.setdefault(hk, set()).add(k)

    def label(self, k: str) -> str:
        g = self.groups.get(k)
        if not g:
            return k
        import v2_views
        return v2_views.name_of(g[0])


def machine_info(conn: sqlite3.Connection, key: str, idx: _Idx | None = None) -> dict:
    import manage_state as ms
    import oncall
    import v2_views

    idx = idx or _Idx(conn)
    k = (key or "").strip()
    if k not in idx.groups:
        how, keys = oncall.resolve(conn, k, idx.groups)
        if len(keys) != 1:
            raise LookupError(f"找不到這台主機：{k}")
        k = keys[0]
    g = idx.groups[k]
    ips: list[str] = []
    for r in g:
        for ip in ms.ips_of(r["ip"]):
            if ip not in ips:
                ips.append(ip)
    return {"key": k, "name": v2_views.name_of(g[0]), "hostname": g[0]["hostname"], "serials": [r["asset_serial"] for r in g],
            "ips": ips, "hostkeys": {h for h in (_hostkey(r["hostname"]) for r in g) if h}, "retired": k.startswith("ret:"),
            "rows": g}


def _match(idx: _Idx, ip: str | None, name: str | None) -> tuple[str, set[str], str]:
    """一個告警裝置（IP＋名稱）對回資產登記。回 (結果, 候選機器代號, 說明)。
    結果：ip_name（IP 唯一且主機名也對得上，才可計次）、pending（待核）、none（不屬於任何已登記主機）。"""
    ipk = idx.ip.get((ip or "").strip(), set())
    hk = _hostkey(name) if name else ""
    nk = idx.host.get(hk, set()) if hk else set()
    nall = idx.host_all.get(hk, set()) if hk else set()
    if ipk:
        if len(ipk) > 1:
            return "pending", ipk | nk, "這個 IP 登記在多台主機（共用／VIP／重用），無法確定是哪一台"
        (k,) = tuple(ipk)
        if k in nk:
            return "ip_name", ipk, ""
        if nall:
            return "pending", ipk | nall, "IP 與主機名指向不同主機（含已退役；可能 IP 被重用或改名）"
        return "pending", ipk, "只有 IP 符合、名稱不在資產登記裡，無法印證是這一台（可能 IP 被重用）"
    if len(nk) == 1:
        return "pending", nk, "僅主機名符合、IP 對不上登記（可能換 IP）"
    if len(nk) > 1:
        return "pending", nk, "主機名對到多台"
    if nall:
        return "pending", nall, "名稱屬於已退役主機、IP 不在現役登記"
    return "none", set(), ""


# ===================================================================== 告警文字分類（磁碟／記憶體／CPU／其他）
_DISK_W = re.compile(r"disk|磁碟|硬碟|partition|分割|filesystem|file ?system|檔案系統|volume|free ?space|drive|inode|儲存空間|容量|/dev/", re.I)
_MEM_W = re.compile(r"memory|記憶體|\bram\b|\bswap\b|換頁|\boom\b", re.I)
_CPU_W = re.compile(r"\bcpu\b|處理器|processor", re.I)
_WIN_DRIVE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]):(?=[\\/\s)\]、,;]|$)")
_UNIX_PATH = re.compile(r"(?<![A-Za-z0-9:/])(/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*|/)(?=[\s)\],;、]|$)")
_TRUNC = re.compile(r"\s*等\s*\d+\s*項\s*$")


def classify_problem(problem: str | None) -> tuple[list[tuple[str, str, str]], bool]:
    """WhatsUp 問題說明 → [(類別, 分割區或項目名, 原文項目)]，與「文字被截斷（只留前 5 項）」旗標。
    空白說明＝(other, 未取得) 且視為截斷（不能拿『沒寫』當『已恢復』）。"""
    text = (problem or "").strip()
    if not text:
        return [("other", NO_DATA, "")], True
    trunc = bool(_TRUNC.search(text))
    text = _TRUNC.sub("", text)
    out: list[tuple[str, str, str]] = []
    for it in [x.strip() for x in text.split("、") if x.strip()]:
        if _DISK_W.search(it) or (_WIN_DRIVE.search(it) and not _MEM_W.search(it) and not _CPU_W.search(it)):
            m = _WIN_DRIVE.search(it)
            part = f"{m.group(1).upper()}:" if m else None
            if not part:
                m2 = _UNIX_PATH.search(it)
                part = (m2.group(1).rstrip("/") or "/") if m2 else NO_DATA
            out.append(("disk", part, it))
        elif _MEM_W.search(it):
            out.append(("memory", "", it))
        elif _CPU_W.search(it):
            out.append(("cpu", "", it))
        else:
            out.append(("other", it[:60], it))
    seen, uniq = set(), []
    for c, p, it in out:
        k = _ev_key(c, p, it)
        if k not in seen:
            seen.add(k)
            uniq.append((c, p, it))
    return uniq, trunc


def _ev_key(cat: str, part: str, item: str = "") -> str:
    """磁碟抓不到分割區的項目（Disk Queue、Disk Read Latency…）以告警項原文區分，不併成同一個事件而少算。"""
    if cat == "disk" and part == NO_DATA:
        return f"disk:{NO_DATA}:{item}"
    return f"{cat}:{part}" if part else cat


# ===================================================================== 來源 1：WhatsUp（有歷史，無指標值）
def _overlap_t(a0, a1, start, end) -> bool:
    """期間重疊。四個時間一律先經 _p() 正規化成同一種本地 datetime 再比，不做字串比較；解析不了的視為不重疊。"""
    t0, t1, ws, we = _p(a0), _p(a1), _p(start), _p(end)
    if t0 is None or ws is None or we is None:
        return False
    return t0 <= we and (t1 is None or a1 in (None, "") or t1 >= ws)


def _whatsup(conn, info, idx, start: str, end: str) -> tuple[list[dict], list[dict], dict]:
    cov = {"source": "whatsup", "label": SRC_LABEL["whatsup"], "kind": "event_history", "state": "no_data", "rows": 0,
           "covers_from": None, "covers_to": None, "note": ""}
    if not _table(conn, "whatsup_alert_history"):
        cov["note"] = "WhatsUp 輪詢表不存在（從未啟用）。這不代表沒有告警，是來源沒資料。"
        return [], [], cov
    one = conn.execute("SELECT MIN(at) a, MAX(at) b, COUNT(*) n FROM whatsup_alert_history").fetchone()
    from db import get_setting
    enabled = get_setting(conn, "whatsup_enabled", "0") == "1"
    lr = conn.execute("SELECT at, status FROM whatsup_runs ORDER BY id DESC LIMIT 1").fetchone() if _table(conn, "whatsup_runs") else None
    failed = conn.execute("SELECT COUNT(*) FROM whatsup_runs WHERE status='failed' AND at BETWEEN ? AND ?", (start, end)).fetchone()[0] \
        if _table(conn, "whatsup_runs") else 0
    cov.update(enabled=enabled, source_covers_from=one["a"], source_covers_to=one["b"], failed_runs_in_window=failed,
               last_run_at=lr["at"] if lr else None, last_run_status=lr["status"] if lr else None)
    notes = []
    if not one["n"]:
        cov["note"] = "WhatsUp 還沒有任何歷史紀錄；此機沒有事件不代表正常。"
        return [], [], cov
    if not enabled:
        notes.append("WhatsUp 定期輪詢目前未啟用，最新資料可能停在 " + str(one["b"]))
    if failed:
        notes.append(f"期間內輪詢失敗 {failed} 次（失敗時現況不更新，也不會誤判為解除）")
    if (_p(one["a"]) or datetime.min) > (_p(start) or datetime.min):
        notes.append(f"WhatsUp 歷史從 {one['a']} 才開始，期間前段沒有資料")
    if info["retired"]:
        cov["state"] = "no_machine_data"
        notes.append("已退役主機不做 IP 關聯（IP 可能已被別台重用），WhatsUp 歷史不併入。")
        cov["note"] = "；".join(notes)
        return [], [], cov
    dev_ids: set[str] = set()
    if info["ips"]:
        ph = ",".join("?" * len(info["ips"]))
        dev_ids |= {r[0] for r in conn.execute(f"SELECT DISTINCT device_id FROM whatsup_alert_history WHERE ip IN ({ph})", info["ips"])}
    for r in conn.execute("SELECT DISTINCT device_id, name FROM whatsup_alert_history"):
        if r["name"] and _hostkey(r["name"]) in info["hostkeys"]:
            dev_ids.add(r["device_id"])
    cur_alerts = {r["device_id"]: {**dict(r), "last_seen": _nt(r["last_seen"])} for r in conn.execute("SELECT device_id, last_seen, since, state FROM whatsup_alerts")} \
        if _table(conn, "whatsup_alerts") else {}
    events: list[dict] = []
    pending: list[dict] = []
    mine_at: list[str] = []          # 落在查詢期間內的紀錄時間
    mine_all: list[str] = []         # 這台所有的紀錄時間（只用來說明『有紀錄但不在期間內』）
    ids = sorted(dev_ids)
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        ph = ",".join("?" * len(chunk))
        rows_by: dict[str, list[dict]] = {}
        for r in conn.execute(f"SELECT * FROM whatsup_alert_history WHERE device_id IN ({ph}) ORDER BY id", chunk):
            rows_by.setdefault(r["device_id"], []).append({**dict(r), "at": _nt(r["at"])})
        for did, rows in rows_by.items():
            eps = _episodes(rows)
            for n, ep in enumerate(eps):
                first = ep["rows"][0]
                res, cands, why = _match(idx, first["ip"], first["name"])
                if res == "none" or info["key"] not in cands:
                    continue
                end_at = ep["cleared"]["at"] if ep["cleared"] else None
                if res == "pending":
                    if _overlap_t(first["at"], end_at, start, end):
                        pending.append({"source": "whatsup", "source_label": SRC_LABEL["whatsup"], "ref": f"WUG-{first['id']}",
                                        "device": first["name"], "ip": first["ip"], "first_at": first["at"], "reason": why,
                                        "candidates": [idx.label(c) for c in sorted(cands)], "hint": PENDING_HINT})
                    continue
                times = [r["at"] for r in ep["rows"]] + ([end_at] if end_at else [])
                mine_all += times
                if _overlap_t(first["at"], end_at, start, end):
                    mine_at += times
                # 只有「這個裝置最後一段、沒有解除、而且現況表裡它還亮著（狀態仍是告警）」才證明持續中；其他舊段結束時間未知
                current = cur_alerts.get(did) if (n == len(eps) - 1 and not ep["cleared"]) else None
                if current and not _is_alert_state(current.get("state")):
                    current = None
                events += _occurrences(ep, did, res, current)
    if mine_at:
        cov.update(state="has_data", rows=len(mine_at), covers_from=min(mine_at), covers_to=max(mine_at))
    else:
        cov["state"] = "no_machine_data"
        notes.append("此機在這個期間沒有可確定屬於它的 WhatsUp 紀錄（可能沒被監控，也可能沒有告警，這裡無法分辨；不代表正常）"
                     + (f"；此機只有 {min(mine_all)} ～ {max(mine_all)} 的紀錄，不在期間內" if mine_all else "")
                     + (f"；另有 {len(pending)} 筆待核" if pending else ""))
        if mine_all:
            cov.update(covers_from=min(mine_all), covers_to=max(mine_all))
    notes.append("已知限制：改名或換 IP 的合法歷史會被列待核而少算（沒有資產別名／異動歷程資料）")
    cov["note"] = "；".join(notes)
    out = [e for e in events if _overlap(e, start, end)]
    return out, pending, cov


def _wug_relay(conn, info, start: str, end: str, now: datetime) -> tuple[list[dict], list[dict], dict]:
    """2.066.x（Q15）：4.1 告警事件（WhatsUp 經中繼進來的 alert_event）有啟用時，這台的 WhatsUp 事件改讀這一份
    ——跟 1.1 今日概況、夜間速查、Teams 同一張表（wug_alert.for_machine 同一支查詢），不再讀舊的 API 輪詢歷史。
    一筆 alert_event（一個設備＋監控項目的一次問題）＝一個事件；維護中的不計次。對資產用中繼匯入時算好的 machine_key，
    對不到資產的告警用這台的 IP 補（跟夜間速查每台卡片同一個條件）；已退役主機只認 machine_key，不用 IP（IP 可能被別台重用）。"""
    import wug_alert as wa
    cov = {"source": "whatsup", "label": SRC_LABEL["whatsup"] + "（4.1 告警事件，經中繼）", "kind": "event_history", "state": "no_data", "rows": 0,
           "covers_from": None, "covers_to": None, "note": ""}
    try:
        cfg = wa.load_settings(conn)
    except ValueError as exc:
        cov.update(state="error", note=f"4.1 告警設定讀不到：{exc}")
        return [], [], cov
    st = wa.status(conn, now)
    live = st["heartbeat"] == "ok"
    mk = info["key"][4:] if info["key"].startswith("ret:") else info["key"]
    got = wa.for_machine(conn, mk, [] if info["retired"] else info["ips"], recent=1_000_000)
    rows = got["current"] + got["recent"]
    notes = []
    if st["heartbeat"] == "never":
        cov["note"] = "4.1 告警事件已啟用，但還沒收到任何 WhatsUp 告警資料（中繼還沒送來）；此機沒有事件不代表正常。"
        return [], [], cov
    if st["heartbeat"] == "down":
        notes.append(f"告警資料中斷（最後收到 {st['last_received']}）：之後的狀況不知道，還沒恢復的標「結束時間未知」")
    events = []
    for r in rows:
        first, rec = r["started_at"], r["ended_at"]
        if not _overlap_t(first, rec, start, end):
            continue
        items, _tr = classify_problem(r["monitor"])
        cat, part = (items[0][0], items[0][1]) if items else ("other", NO_DATA)
        nm = {"disk": "磁碟 " + (part if part != NO_DATA else r["monitor"]), "memory": "記憶體", "cpu": "CPU"}.get(cat, r["monitor"] or "其他")
        events.append({
            "id": f"WUGA-{r['id']}", "source": "whatsup", "source_label": SRC_LABEL["whatsup"],
            "category": cat, "category_label": CAT_LABEL[cat], "partition": part if cat == "disk" else (r["monitor"] or ""),
            "title": nm, "first_at": first, "last_at": rec or r["last_seen"], "recovered_at": rec,
            "status": "recovered" if rec else ("ongoing" if live else "unknown"), "partial_recover": False,
            "match": "relay", "match_label": MATCH_LABEL["relay"] if r["machine_key"] else "IP 符合（對不到資產的告警）",
            "countable": r["tier"] != "maint",
            "metric": None, "metric_text": f"{NO_DATA}（WhatsUp 只提供告警文字，沒有量測數值）",
            "evidence": [{"k": "監控項目", "v": r["monitor"] or NO_DATA}, {"k": "WhatsUp 狀態", "v": r["status_text"] or r["wug_state"] or NO_DATA},
                         {"k": "分級", "v": r["tier_label"]}, {"k": "裝置", "v": f"{r['who'] or NO_DATA}／{r['ip'] or NO_DATA}（WhatsUp 裝置 {r['wug_device_id']}）"},
                         {"k": "時間說明", "v": "開始時間依 WhatsUp「Down at least N min」往回推，推不出來用收到快照的時間"}],
            "known_start": True, "recovered_label": None if (rec or live) else "告警資料中斷，現在是否仍在告警不知道",
        })
    if events:
        ts = [e["first_at"] for e in events] + [e["last_at"] for e in events if e["last_at"]]
        cov.update(state="has_data", rows=len(events), covers_from=min(ts), covers_to=max(ts))
    else:
        cov["state"] = "no_machine_data"
        notes.append("此機在這個期間沒有 4.1 告警事件（可能沒被監控，也可能沒有告警，這裡無法分辨；不代表正常）")
    cov["note"] = "；".join(notes)
    return events, [], cov


def _is_alert_state(state) -> bool:
    """現況表裡的狀態還是告警（不是正常／已解除）。"""
    import whatsup_constants as C

    st = str(state or "").strip()
    return st.lower() in C.ALERT_STATES or st in C.STATE_LABEL.values()


def _episodes(rows: list[dict]) -> list[dict]:
    """同一個裝置的歷史列 → 一段一段的告警（new 起、cleared 止；changed 屬於當前這段，不增加次數）。"""
    eps, cur = [], None
    for r in rows:
        ev = r["event"]
        if ev == "new" or (ev == "changed" and cur is None):
            if cur is not None:        # 沒看到 cleared 就又 new：前一段結束時間不明
                eps.append(cur)
            cur = {"rows": [r], "known_start": ev == "new", "cleared": None}
        elif ev == "changed":
            cur["rows"].append(r)
        elif ev == "cleared":
            if cur is not None:
                cur["cleared"] = r
                eps.append(cur)
                cur = None
    if cur is not None:
        eps.append(cur)
    return eps


def _occurrences(ep: dict, did: str, match: str, current: dict | None) -> list[dict]:
    """一段告警 → 每個（類別，分割區）的獨立發生。項目從問題說明中消失（說明沒被截斷）→ 該項恢復；之後再出現＝再次發生。"""
    act: dict[str, dict] = {}
    done: list[dict] = []
    for r in ep["rows"]:
        items, trunc = classify_problem(r["problem"])
        keys = {_ev_key(c, p, it): (c, p, it) for c, p, it in items}
        ph_key = _ev_key("other", NO_DATA)
        inherit = act.pop(ph_key) if (ph_key in act and ph_key not in keys) else None    # 起初沒讀到說明、後來才補上：起點算最早那一筆
        for k, (c, p, it) in keys.items():
            if k not in act:
                act[k] = {"cat": c, "part": p, "key": k, "start": inherit["start"] if inherit else r, "item": it, "last": r["at"],
                          "raw": r["problem"] or "", "state": inherit["state"] if inherit else r["new_state"],
                          "end": None, "partial": False}
            else:
                act[k]["last"] = r["at"]
        if not trunc:
            for k in [k for k in act if k not in keys]:
                o = act.pop(k)
                o.update(end=r["at"], partial=True)
                done.append(o)
    cl = ep["cleared"]
    out = []
    for o in done + list(act.values()):
        o_cleared = cl and o["end"] is None
        recovered = cl["at"] if o_cleared else o["end"]
        if recovered:
            status, last = "recovered", o["last"]
        elif current:
            status, last = "ongoing", current["last_seen"] or o["last"]
        else:
            status, last = "unknown", o["last"]
        nm = {"disk": "磁碟 " + (o["part"] if o["part"] != NO_DATA else f"{NO_DATA}（{o['item'][:40]}）"), "memory": "記憶體", "cpu": "CPU"}.get(
            o["cat"], o["part"] or "其他")
        out.append({
            "id": f"WUG-{o['start']['id']}-{o['key']}", "source": "whatsup", "source_label": SRC_LABEL["whatsup"],
            "category": o["cat"], "category_label": CAT_LABEL[o["cat"]], "partition": o["part"] if o["cat"] in ("disk", "other") else "",
            "title": nm, "first_at": o["start"]["at"], "last_at": last, "recovered_at": recovered, "status": status,
            "partial_recover": bool(recovered and not o_cleared),
            "match": match, "match_label": MATCH_LABEL[match], "countable": True,
            "metric": None, "metric_text": f"{NO_DATA}（WhatsUp 只提供告警文字，沒有量測數值）",
            "evidence": _wug_evidence(o, did, ep, current, status),
            "known_start": ep["known_start"],
            "recovered_label": UNKNOWN_END if status == "unknown" else None,
        })
    return out


def _wug_evidence(o: dict, did: str, ep: dict, current: dict | None, status: str) -> list[dict]:
    ev = [{"k": "告警原文", "v": o["raw"] or NO_DATA}, {"k": "起始狀態", "v": o["state"] or NO_DATA},
          {"k": "裝置", "v": f"{o['start']['name'] or NO_DATA}／{o['start']['ip'] or NO_DATA}（WhatsUp 裝置 {did}）"},
          {"k": "時間說明", "v": "時間是本系統輪詢到的時間，不是設備實際發生時間；首發可能比實際晚一個輪詢間隔"}]
    if status == "unknown":
        ev.append({"k": "結束時間", "v": UNKNOWN_END + "；不能說它仍在持續，也不能說已恢復"})
    if status == "ongoing":
        ev.append({"k": "持續中的依據", "v": "WhatsUp 現況表裡這個裝置目前仍亮著（取自最後一次成功輪詢）"})
    if not ep["known_start"]:
        ev.append({"k": "起點", "v": "紀錄從這一筆才開始，實際起點可能更早"})
    if o["cat"] == "memory":
        ev += [{"k": "available／swap／OOM／程序", "v": f"{NO_DATA}（此來源沒有）"}]
    if current and current.get("since"):
        ev.append({"k": "WhatsUp 自己記的開始時間（現況）", "v": current["since"]})
    return ev


def _overlap(e: dict, start: str, end: str) -> bool:
    return _overlap_t(e["first_at"], e.get("closed_at") or e["recovered_at"], start, end)


# ===================================================================== 來源 2：NMON（自己讀表，不呼叫 nmon_series 的 ensure）
def _nmon_hosts(conn) -> list[dict]:
    q = "SELECT host, COUNT(*) d, SUM(samples) s, MIN(day) a, MAX(day) b, MAX(interval_s) i FROM nmon_series GROUP BY host ORDER BY host"
    return [{"host": r[0], "days": r[1], "samples": r[2], "from": r[3], "to": r[4], "interval_s": r[5]} for r in conn.execute(q)]


def _nload(conn, host: str, month: str, day: str | None = None) -> list[tuple[str, dict]]:
    import nmon_series as ns

    if day:
        q, a = "SELECT day, data FROM nmon_series WHERE host = ? AND day = ? ORDER BY day", (host, day)
    else:
        q, a = "SELECT day, data FROM nmon_series WHERE host = ? AND day LIKE ? ORDER BY day", (host, f"{month}%")
    return [(r[0], ns._decode(r[1])) for r in conn.execute(q, a)]


def _nmon(conn, info, idx, start: str, end: str) -> tuple[list[dict], list[dict], dict]:
    cov = {"source": "nmon", "label": SRC_LABEL["nmon"], "kind": "metric_history", "state": "no_data", "rows": 0,
           "covers_from": None, "covers_to": None, "note": ""}
    if not _table(conn, "nmon_series"):
        cov["note"] = "沒有上傳過 NMON 資料；沒有事件不代表正常。"
        return [], [], cov
    allh = _nmon_hosts(conn)
    hosts = [h for h in allh if _hostkey(h["host"]) in info["hostkeys"]]
    pending = []
    d0, d1 = start[:10], end[:10]
    if info["retired"] or any(len(idx.host.get(_hostkey(h["host"]), ())) > 1 for h in hosts):
        for h in hosts:
            if h["from"] <= d1 and h["to"] >= d0:
                pending.append({"source": "nmon", "source_label": SRC_LABEL["nmon"], "ref": h["host"], "device": h["host"], "ip": None,
                                "first_at": h["from"], "reason": "NMON 主機名對到多台（或此機已退役），無法確定是哪一台",
                                "candidates": [idx.label(c) for c in sorted(idx.host.get(_hostkey(h["host"]), ()))], "hint": PENDING_HINT})
        cov.update(state="no_machine_data", note="NMON 主機名歧義，已列待核、不計次")
        return [], pending, cov
    if not hosts:
        cov.update(state="no_machine_data", note="這台沒有上傳過 NMON 資料（NMON 只涵蓋有上傳的 AIX／Linux 主機）；沒有事件不代表正常。")
        return [], pending, cov
    cov.update(rows=sum(h["samples"] or 0 for h in hosts), covers_from=min(h["from"] for h in hosts), covers_to=max(h["to"] for h in hosts))
    inwin = [h for h in hosts if h["from"] <= d1 and h["to"] >= d0]
    if not inwin:
        cov.update(state="no_machine_data", note=f"有 NMON 資料，但都不在這個期間（涵蓋 {cov['covers_from']} ～ {cov['covers_to']}）；不代表期間內正常。")
        return [], pending, cov
    cov["state"] = "has_data"
    events: list[dict] = []
    # 先收齊所有月份的超標時段再合併（跨月持續超標只算一次）；多往前看一個月，讓期間前就開始的超標找得到起點
    months = _months(_prev_month(start[:7]), end[:7])
    import nmon_series as ns
    for h in inwin:
        step = h["interval_s"] or 60
        runs_by: dict[str, list[dict]] = {}
        ser: dict[str, list[tuple[str, float]]] = {}       # 每個指標的所有取樣（時間字串, 值），用來證明恢復
        for mo in months:
            for dy, data in _nload(conn, h["host"], mo):
                for key, col in data["cols"].items():
                    if key == "cpu_busy" or key == "mem_used" or key.startswith("fs:"):
                        thr = ns.describe(key)["threshold"]
                        if thr is not None:
                            runs_by.setdefault(key, []).extend(ns._runs(data["ts"], col, dy, thr, step))
                            ser.setdefault(key, []).extend((f"{dy} {t}", v) for t, v in zip(data["ts"], col) if v is not None)
        for key, runs in runs_by.items():
            cat, part = ("cpu", "") if key == "cpu_busy" else ("memory", "") if key == "mem_used" else ("disk", key[3:])
            meta = ns.describe(key)
            for run in _merge_runs(runs, step):
                rec = _proof_recovery(ser.get(key, []), run, step, meta["threshold"])
                events.append(_nmon_event(conn, h, key, cat, part, run, meta["threshold"], meta.get("unit") or "%", rec))
    seen, uniq = set(), []
    for e in events:
        if e["id"] not in seen:
            seen.add(e["id"])
            uniq.append(e)
    cov["note"] = "NMON 是門檻超標時段（磁碟 80%／記憶體 90%／CPU 80%），不是告警；資料只到上傳為止。"
    return [e for e in uniq if _overlap(e, start, end)], pending, cov


def _prev_month(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y - 1:04d}-12" if mo == 1 else f"{y:04d}-{mo - 1:02d}"


def _months(a: str, b: str) -> list[str]:
    y, m = int(a[:4]), int(a[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= b:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _merge_runs(runs: list[dict], step: int) -> list[dict]:
    """每天各算一段；跨午夜、跨月連著的合併成一段（不然同一次超標會被算多次）。"""
    rs = sorted(runs, key=lambda r: r["from"])
    out: list[dict] = []
    for r in rs:
        if out:
            a, b = _p(out[-1]["to"]), _p(r["from"])
            if a and b and 0 <= (b - a).total_seconds() <= step * 2:
                o = out[-1]
                o["to"] = r["to"]
                o["minutes"] = (o.get("minutes") or 0) + (r.get("minutes") or 0)
                if r["peak"] > o["peak"]:
                    o["peak"], o["peak_at"] = r["peak"], r["peak_at"]
                continue
        out.append(dict(r))
    return out


def _proof_recovery(ser: list[tuple[str, float]], run: dict, step: int, thr) -> str | None:
    """只有『緊接超標段之後、間隔 ≤ 2 個取樣間隔、而且實際低於（不高於）門檻』的樣本才證明恢復，回該樣本時間。
    資料日／月份中斷、或之後沒有這樣的樣本（資料到此為止）→ None（結束時間未知）：缺資料期間沒有任何樣本可證明恢復。"""
    i = bisect.bisect_right([t for t, _ in ser], run["to"])
    if i >= len(ser):
        return None
    t, v = ser[i]
    a, b = _p(run["to"]), _p(t)
    if a and b and 0 < (b - a).total_seconds() <= step * 2 and v <= thr:
        return t
    return None


def _nmon_event(conn, h: dict, key: str, cat: str, part: str, run: dict, thr, unit: str, rec: str | None) -> dict:
    at_end = rec is None
    title = {"disk": f"磁碟 {part}", "memory": "記憶體", "cpu": "CPU"}[cat]
    ev = [{"k": "NMON 主機名", "v": h["host"]}, {"k": "超標時段", "v": f"{run['from']} ～ {run['to']}（約 {run.get('minutes') or '?'} 分鐘）"},
          {"k": "門檻", "v": f"{thr}{unit}"}]
    if at_end:
        ev.append({"k": "恢復", "v": "超標段之後沒有緊接的、低於門檻的取樣（資料中斷或到此為止），無法證明何時恢復"})
    if cat == "memory":
        ev += _mem_evidence(conn, h, run)
    return {
        "id": "NMON-%s-%s-%s" % (h["host"], key, run["from"].replace("-", "").replace(":", "").replace(" ", "T")),
        "source": "nmon", "source_label": SRC_LABEL["nmon"], "category": cat, "category_label": CAT_LABEL[cat], "partition": part,
        "title": title, "first_at": run["from"], "last_at": run["to"], "recovered_at": rec, "status": "unknown" if at_end else "recovered",
        "partial_recover": False, "match": "host", "match_label": MATCH_LABEL["host"], "countable": True,
        "metric": {"label": "峰值", "value": run["peak"], "unit": unit, "at": run["peak_at"], "threshold": thr},
        "metric_text": f"峰值 {run['peak']}{unit}（{run['peak_at']}；門檻 {thr}{unit}）", "evidence": ev, "known_start": True,
        "recovered_label": UNKNOWN_END if at_end else None,
    }


def _data_end(conn, h: dict) -> datetime | None:
    try:
        d = _nload(conn, h["host"], h["to"][:7], h["to"])
        if d and d[-1][1].get("ts"):
            return _p(f"{d[-1][0]} {d[-1][1]['ts'][-1]}")
    except Exception:  # noqa: BLE001 - 讀不到資料尾端＝不判斷恢復，事件仍列
        return None
    return None


def _top_at(conn, host: str, ts: str) -> dict:
    """尖峰時間最近的 TOP 快照（同 nmon_series.top_at，但不呼叫 ensure）。"""
    day, t = ts[:10], ts[11:19]
    data = _nload(conn, host, day[:7], day)
    top = (data[0][1].get("top") or {}) if data else {}
    if not top:
        return {"at": None, "procs": []}

    def secs(x: str) -> int:
        hh, mm, ss = (int(v) for v in x.split(":"))
        return hh * 3600 + mm * 60 + ss
    try:
        at = min(top, key=lambda k: abs(secs(k) - secs(t)))
    except ValueError:
        return {"at": None, "procs": []}
    return {"at": f"{day} {at}", "procs": [{"pid": p, "cpu": c, "cmd": m} for p, c, m in top[at]]}


def _mem_evidence(conn, h: dict, run: dict) -> list[dict]:
    """記憶體事件的佐證：換頁空間使用率峰值、尖峰時最吃資源的程式。沒這個欄位就明寫未取得（不是 0）。"""
    out = []
    swap = None
    try:
        d0, d1 = run["from"][:10], run["to"][:10]
        vals = []
        for day, data in _nload(conn, h["host"], d0[:7]):
            if not (d0 <= day <= d1):
                continue
            col = data["cols"].get("swap_used")
            if col is None:
                continue
            for t, v in zip(data["ts"], col):
                if v is not None and run["from"] <= f"{day} {t}" <= run["to"]:
                    vals.append(v)
        swap = max(vals) if vals else None
    except Exception:  # noqa: BLE001
        swap = None
    out.append({"k": "換頁空間使用率峰值", "v": f"{swap}%" if swap is not None else f"{NO_DATA}（NMON 沒收這個欄位）"})
    try:
        top = _top_at(conn, h["host"], run["peak_at"])
        procs = top.get("procs") or []
        out.append({"k": "尖峰時最吃資源的程式", "v": "、".join(f"{p['cmd']}({p['cpu']}%)" for p in procs[:3]) + (f"（取自 {top['at']}）" if top.get("at") else "")
                    if procs else f"{NO_DATA}（NMON 沒有 TOP 快照）"})
    except Exception:  # noqa: BLE001
        out.append({"k": "尖峰時最吃資源的程式", "v": NO_DATA})
    out.append({"k": "available／OOM", "v": f"{NO_DATA}（NMON 沒收）"})
    return out


# ===================================================================== 來源 3：事件應變（4.1；有開始／結案時間，沒有逐台指標）
def _incidents(conn, info, start: str, end: str, now: datetime) -> tuple[list[dict], dict, list[dict]]:
    cov = {"source": "incident", "label": SRC_LABEL["incident"], "kind": "event_record", "state": "no_data", "rows": 0,
           "covers_from": None, "covers_to": None, "note": ""}
    if not _table(conn, "incident_event"):
        cov["note"] = "還沒有任何事件應變紀錄。"
        return [], cov, []
    import incident

    evs = incident.list_events(conn)
    cov.update(state="none_recorded", note="沒有涉及此機的事件應變紀錄（事件應變是人開的，沒開不代表沒故障）。")
    if not evs:
        return [], cov, []
    s_d, e_d, lim_d = _p(start), _p(end), now + timedelta(days=30)
    cand = [e for e in evs if (_p(e["started_at"]) or datetime.max) <= max(e_d, lim_d)
            and (e["closed_at"] is None or (_p(e["closed_at"]) or datetime.min) >= min(s_d, now))]
    if not cand:
        return [], cov, []
    world = incident.World(conn)
    out, upcoming, mine_at = [], [], []
    for e in cand:
        full = incident.get_event(conn, e["id"])
        exp = incident.expand(world, full["scope"])
        mine = [m for m in exp["machines"] if m.get("key") == info["key"] or (m.get("asset_serial") in info["serials"])]
        if not mine:
            continue
        started = _nt(full["started_at"])
        closed_at = _nt(full["closed_at"])
        mine_at.append(started)
        chk = conn.execute("SELECT i.result, i.checked_at, r.started_at FROM incident_check_item i JOIN incident_check_run r ON r.id=i.run_id "
                           "WHERE r.event_id=? AND i.key IN (%s) ORDER BY i.id DESC LIMIT 1" % ",".join("?" * (1 + len(info["serials"]))),
                           [e["id"], info["key"], *info["serials"]]).fetchone() if _table(conn, "incident_check_item") else None
        planned = (full.get("category") or "incident") == "planned"
        drill = full.get("mode") == "drill"
        closed = full["status"] == "closed"
        ev_ = [{"k": "事件標題", "v": full["title"]}, {"k": "類型", "v": f"{incident.CATEGORIES.get(full.get('category') or 'incident', '')}／{full['type']}／{full.get('mode_label')}"},
               {"k": "範圍", "v": exp["scope_text"]},
               {"k": "此機最近一輪連線測試", "v": f"{incident.RESULT_LABEL.get(chk['result'], chk['result'])}（{chk['checked_at'] or chk['started_at']}）" if chk else f"{NO_DATA}（沒測過）"}]
        if full.get("summary"):
            ev_.append({"k": "結案摘要", "v": full["summary"]})
        rec = {
            "id": f"INC-{e['id']}", "source": "incident", "source_label": SRC_LABEL["incident"], "category": "incident",
            "category_label": CAT_LABEL["incident"], "partition": "", "title": full["title"], "first_at": started,
            "last_at": closed_at, "recovered_at": None, "closed_at": closed_at, "status": "closed" if closed else "ongoing",
            "partial_recover": False, "match": "scope", "match_label": MATCH_LABEL["scope"],
            "countable": not (planned or drill), "planned": planned, "drill": drill,
            "metric": None, "metric_text": f"{NO_DATA}（事件應變沒有逐台量測值）", "evidence": ev_, "known_start": True,
            "recovered_label": None, "closed_label": CLOSE_LABEL if closed else None,
        }
        if planned and now < (_p(full["started_at"]) or datetime.min) <= lim_d:
            upcoming.append({"id": rec["id"], "kind": "planned_incident", "kind_label": "計畫停機／維護", "title": full["title"],
                             "date": started[:16], "precision": "分", "source_label": SRC_LABEL["incident"],
                             "detail": exp["scope_text"]})
        if _overlap(rec, start, end):
            out.append(rec)
    if out:
        cov.update(state="has_data", rows=len(out), covers_from=min(e["first_at"] for e in out), covers_to=max(e["first_at"] for e in out), note="")
    elif mine_at:
        cov["note"] = "此機有事件應變紀錄，但不在這個期間內。"
    return out, cov, upcoming


# ===================================================================== 其他來源盤點（只列覆蓋狀況，不產生事件）
def _snapshot_coverage(conn, info) -> list[dict]:
    out = []
    n = last = None
    if _table(conn, "health_snapshot") and info["ips"]:
        ph = ",".join("?" * len(info["ips"]))
        r = conn.execute(f"SELECT COUNT(*) n, MAX(taken_at) t FROM health_snapshot WHERE ip IN ({ph})", info["ips"]).fetchone()
        n, last = r["n"], r["t"]
    out.append({"source": "health_snapshot", "label": "每週健康基準快照", "kind": "snapshot", "state": "has_data" if n else "no_data",
                "rows": n or 0, "covers_from": None, "covers_to": last,
                "note": "定期快照（磁碟使用率、記憶體），不是事件；兩次快照之間發生又恢復的事看不到，不用來補造事件。"})
    r2 = None
    if _table(conn, "host_metric_latest"):
        ph = ",".join("?" * len(info["serials"]))
        r2 = conn.execute(f"SELECT COUNT(*) n, MAX(collected_at) t FROM host_metric_latest WHERE asset_serial IN ({ph})", info["serials"]).fetchone()
    out.append({"source": "host_metric_latest", "label": "Push agent 最新指標", "kind": "snapshot", "state": "has_data" if r2 and r2["n"] else "no_data",
                "rows": r2["n"] if r2 else 0, "covers_from": None, "covers_to": r2["t"] if r2 else None,
                "note": "只存最新一筆（upsert），沒有歷史，不能回推過去的事件。"})
    out.append({"source": "oncall_monthly", "label": "值班卡／月報（oncall、host_monthly、monthly_report）", "kind": "aggregate", "state": "n/a",
                "rows": 0, "covers_from": None, "covers_to": None,
                "note": "現況卡片與全站月度彙總，沒有逐台事件，不作為事件來源。"})
    return out


# ===================================================================== 未來 30 天已知排程／到期
def _upcoming(conn, info, now: datetime, incident_up: list[dict]) -> tuple[list[dict], list[dict]]:
    items = list(incident_up)
    cov = []
    a, b = now.date(), (now + timedelta(days=30)).date()
    n_cert = 0
    try:
        import certs
        for r in certs.all_rows(conn):
            if not r["active"] or r.get("pending_expiry"):
                continue
            toks = {t.strip() for t in re.split(r"[,;\s、／/]+", r.get("host_ips") or "") if t.strip()}
            if not (toks & set(info["ips"])):
                continue
            n_cert += 1
            d = certs.parse_utc(r["not_after_utc"])
            if d and a <= d.astimezone(certs.TZ8).date() <= b:
                dd = d.astimezone(certs.TZ8).strftime("%Y-%m-%d")
                items.append({"id": f"CERT-{r['id']}-exp", "kind": "cert_expire", "kind_label": "憑證到期", "title": r["name"], "date": dd,
                              "precision": "日", "source_label": "憑證盤點", "detail": f"{r['endpoint']}:{r['port']}（{r['environment'] or '環境未填'}）"})
            pd = r.get("planned_date")
            if pd and a.isoformat() <= pd <= b.isoformat():
                items.append({"id": f"CERT-{r['id']}-plan", "kind": "cert_plan", "kind_label": "憑證預排更換", "title": r["name"], "date": pd,
                              "precision": "日", "source_label": "憑證盤點", "detail": f"{r['endpoint']}:{r['port']}"})
        cov.append({"source": "certs", "label": "憑證盤點", "state": "has_data" if n_cert else "no_data", "rows": n_cert,
                    "note": "以憑證的『主機 IP』欄與此機 IP 文字比對；沒填 IP 的憑證對不到這台。"})
    except Exception as exc:  # noqa: BLE001 - 憑證模組讀不到：明寫缺口，不當作沒有
        cov.append({"source": "certs", "label": "憑證盤點", "state": "error", "rows": 0, "note": f"讀取失敗（{type(exc).__name__}），未列入"})
    n_eos = 0
    try:
        import eos
        import normalize

        for r in info["rows"]:
            for kind, val in (("作業系統", r["os"]), ("硬體", r["device_model"])):
                if not val:
                    continue
                inf = normalize.normalize_os(val, conn, r["device_model"]) if kind == "作業系統" else normalize.normalize_model(val, conn)
                hit = eos.lookup_os_eos(inf["canonical"]) if kind == "作業系統" else eos.lookup_hardware_eos(inf["canonical"])
                if not hit or not hit.get("eos_date") or not eos.is_trusted(hit):
                    continue
                n_eos += 1
                txt = str(hit["eos_date"])[:10]
                month_only = bool(re.fullmatch(r"\d{4}-\d{2}", txt))
                try:
                    d0 = date.fromisoformat(txt + "-01" if month_only else txt)
                except ValueError:
                    continue
                d1 = (date(d0.year + (d0.month == 12), d0.month % 12 + 1, 1) - timedelta(days=1)) if month_only else d0
                if d0 <= b and d1 >= a:
                    items.append({"id": f"EOS-{r['asset_serial']}-{kind}", "kind": "eos", "kind_label": "EOS（官方終止支援）",
                                  "title": f"{kind}：{inf['canonical']}", "date": txt, "precision": "月（實際日未知）" if month_only else "日",
                                  "source_label": "EOS 對照表", "detail": f"資產 {r['asset_serial']}"})
    except Exception as exc:  # noqa: BLE001
        cov.append({"source": "eos", "label": "EOS 對照表", "state": "error", "rows": 0, "note": f"讀取失敗（{type(exc).__name__}），未列入"})
    else:
        cov.append({"source": "eos", "label": "EOS 對照表", "state": "has_data" if n_eos else "no_data", "rows": n_eos,
                    "note": "只列有官方日期且來源可信的項目；日期只到月份的標『月（實際日未知）』。"})
    cov.append({"source": "maintenance_calendar", "label": "維護窗行事曆", "state": "n/a", "rows": 0,
                "note": "本系統沒有通用的維護行事曆；『計畫停機』只來自事件應變（4.1），沒登記的維護看不到。"})
    seen, uniq = set(), []
    for i in items:
        dd = _p(i["date"])
        if i["id"] in seen or not ((dd is not None and a <= dd.date() <= b) or i["kind"] == "eos"):     # 嚴格限制 now～now+30 天
            continue
        seen.add(i["id"])
        uniq.append(i)
    uniq.sort(key=lambda i: (i["date"], i["id"]))
    for n, i in enumerate(uniq, 1):
        i["__no"] = n
    return uniq, cov


# ===================================================================== 處理紀錄（ACT-05）
def _notes_of(conn, serials: list[str]) -> list[dict]:
    if not _table(conn, "host_ops_note") or not serials:
        return []
    ph = ",".join("?" * len(serials))
    return [dict(r) for r in conn.execute(f"SELECT * FROM host_ops_note WHERE asset_serial IN ({ph}) ORDER BY id", serials)]


PUBLIC_NOTE_FIELDS = ("id", "kind", "text", "status", "created_by", "created_at", "event_id")


def _public_notes(notes: list[dict]) -> list[dict]:
    """只能看的帳號：只給現行有效版（不含舊版、作廢原文與原因、更正鏈）。"""
    return [{k: n[k] for k in PUBLIC_NOTE_FIELDS if k in n} for n in notes if n["status"] == "active"]


def _attach_notes(events: list[dict], notes: list[dict]) -> list[dict]:
    """把處理紀錄掛到事件；沒填的標『未填／未確認』。每筆標原作者（沿更正鏈找到最早一筆）。找不到事件的回成孤兒清單。"""
    byid = {n["id"]: n for n in notes}
    for n in notes:
        root, hops = n, 0
        while root.get("supersedes_id") in byid and hops < 50:
            root, hops = byid[root["supersedes_id"]], hops + 1
        n["original_by"] = root.get("created_by")
    by_ev: dict[str, list[dict]] = {}
    for n in notes:
        by_ev.setdefault(n["event_id"], []).append(n)
    ids = {e["id"] for e in events}
    for e in events:
        ns = by_ev.get(e["id"], [])
        e["notes"] = ns
        h = {}
        for kind in KIND_LABEL:
            act = [n for n in ns if n["kind"] == kind and n["status"] == "active"]
            h[kind] = act[-1] if act else None
        e["handling"] = h
        e["handling_text"] = {k: (v["text"] if v else ("未確認" if k == "root_cause" else "未填")) for k, v in h.items()}
    return [n for n in notes if n["event_id"] not in ids and n["status"] == "active"]


# ===================================================================== 整包（畫面與 MD 共用同一份）
def _batch_id(d: dict) -> str:
    """批次號＝畫面與 MD 實際輸出內容的規範化雜湊（主機、事件全欄位含證據、計次、來源涵蓋、排程、待核、處理紀錄全欄位含更正鏈、限制）。
    排除隨查詢時間變動的欄位（產生時間、期間的精確起訖）：同資料不同時間查，批次號相同；任何輸出內容變了，批次號就變。"""
    w = d["window"]
    norm = {k: d[k] for k in ("machine", "events", "counts", "ongoing_ids", "evidence_any", "totals", "coverage", "upcoming_coverage",
                              "upcoming", "pending", "orphan_notes", "limits")}
    norm["window"] = {k: w[k] for k in ("period", "label", "tz", "future")}
    return hashlib.sha1(json.dumps(norm, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:12]


def build(conn: sqlite3.Connection, key: str, period: str = "30d", now: datetime | None = None, full_history: bool = True) -> dict:
    """full_history=False（只能看的帳號）：處理紀錄只給現行有效版，不含舊版、作廢原文與原因、更正鏈。批次號仍以完整內容算（兩種角色同批次）。"""
    now = now or _now()
    win = window(period, now)
    idx = _Idx(conn)
    info = machine_info(conn, key, idx)
    events, pending, cov = [], [], []
    if not win["future"]:
        import night_alerts
        # Q15：4.1 告警事件（中繼）有啟用＝跟首頁同一份；沒啟用才讀舊的 WhatsUp API 輪詢歷史
        e1, p1, c1 = (_wug_relay(conn, info, win["start"], win["end"], now) if night_alerts.relay_enabled(conn) is not False
                      else _whatsup(conn, info, idx, win["start"], win["end"]))
        e2, p2, c2 = _nmon(conn, info, idx, win["start"], win["end"])
        events, pending, cov = e1 + e2, p1 + p2, [c1, c2]
    ev3, c3, up_inc = _incidents(conn, info, win["start"] if not win["future"] else _f(now), win["end"], now)
    if not win["future"]:
        events += ev3
    cov.append(c3)
    cov += _snapshot_coverage(conn, info)
    upcoming, up_cov = _upcoming(conn, info, now, up_inc)
    events.sort(key=lambda e: (e["first_at"], e["id"]), reverse=True)
    for n, e in enumerate(events, 1):
        e["__no"] = n
    all_notes = _notes_of(conn, info["serials"])
    orphan = _attach_notes(events, all_notes)
    for e in events:
        e.pop("handling", None)
    counts = count_events(events)
    ongoing = [e["id"] for e in events if e["status"] == "ongoing" and e.get("countable")]
    evidence_any = any(c["state"] == "has_data" for c in cov if c.get("kind") in ("event_history", "metric_history"))
    d = {"machine": {k: info[k] for k in ("key", "name", "hostname", "serials", "ips", "retired")}, "window": win,
         "generated_at": _f(now), "events": events, "counts": counts, "ongoing_ids": ongoing, "evidence_any": evidence_any,
         "totals": {"events": len(events), "countable": sum(1 for e in events if e.get("countable")),
                    "ongoing": len(ongoing), "unknown_end": sum(1 for e in events if e["status"] == "unknown"),
                    "disk": sum(c["count"] for c in counts if c["category"] == "disk"),
                    "memory": sum(c["count"] for c in counts if c["category"] == "memory")},
         "coverage": cov, "upcoming_coverage": up_cov, "upcoming": upcoming, "pending": pending, "orphan_notes": orphan,
         "limits": ["已恢復不等於根因已解決；事件應變的結案時間不是單機恢復時間",
                    "時間依伺服器本地時間（預期 Asia/Taipei）；WhatsUp 時間是輪詢到的時間",
                    "不同來源的事件並列，不自動合併成同一個根因，也不跨來源加總",
                    "只有現況快照的來源不能回推過去的事件，缺資料標『未取得』而非 0 或正常",
                    "只有 IP 與主機名都對得上才計次；改名或換 IP 的合法歷史會被列待核而少算（沒有資產別名／異動歷程資料），這是已知取捨",
                    "機密過濾只擋常見樣式，拆字、同形字、刻意變形的繞過擋不住，請勿在處理紀錄貼密碼、金鑰、token、Webhook"]}
    d["batch"] = _batch_id(d)
    if not full_history:
        for e in events:
            e["notes"] = _public_notes(e["notes"])
        d["orphan_notes"] = _public_notes(orphan)
    return d


def count_events(events: list[dict]) -> list[dict]:
    """計次：（類別，分割區／項目，來源）各一列；同一段持續告警只算一次。event_ids 就是下鑽明細（同一份）。
    計畫停機與演練不算故障次數。"""
    g: dict[tuple, dict] = {}
    for e in events:
        if not e.get("countable"):
            continue
        k = (e["category"], e["partition"], e["source"])
        r = g.setdefault(k, {"category": e["category"], "category_label": e["category_label"], "partition": e["partition"],
                             "source": e["source"], "source_label": e["source_label"], "count": 0, "ongoing": 0, "event_ids": []})
        r["count"] += 1
        r["ongoing"] += 1 if e["status"] == "ongoing" else 0
        r["event_ids"].append(e["id"])
    rows = sorted(g.values(), key=lambda r: (list(CAT_LABEL).index(r["category"]), r["partition"], r["source"]))
    for n, r in enumerate(rows, 1):
        r["__no"] = n
        r["key"] = f"{r['category']}|{r['partition']}|{r['source']}"
    return rows


def find_event(conn, key: str, event_id: str, now: datetime | None = None) -> dict | None:
    for e in build(conn, key, "400d", now)["events"]:
        if e["id"] == event_id:
            return e
    return None


# ===================================================================== 機密過濾（寫入檢查與 MD 輸出共用）
_KEYW = r"(?:password|passwd|passphrase|pwd|pw|pass|secret|api[ _-]?key|access[ _-]?key|client[ _-]?secret|token|密碼|口令|金鑰|私鑰|憑證密碼|帳密)"
#: 一定遮蔽的樣式：URL 內帳密／query 帶憑證／Webhook 網址、私鑰、已知前綴與 JWT
_ALWAYS = [
    re.compile(r"(?i)[a-z][a-z0-9+.\-]*://[^\s/@:]+:[^\s/@]+@\S*"),
    re.compile(r"(?i)https?://\S*[?&;][\w\-]*(?:token|key|secret|password|passwd|pwd|pw|sig|signature|auth|credential)[\w\-]*=\S+"),
    re.compile(r"(?i)https?://\S*(?:hooks\.slack\.com|discord(?:app)?\.com/api/webhooks|webhook\.office\.com|outlook\.office\.com/webhook|"
               r"logic\.azure\.com|powerplatform|webhook|/workflows?/)\S*"),
    re.compile(r"(?i)-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----[\s\S]*?(?:-----END [A-Z ]*-----|$)"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), re.compile(r"\bAKIA[0-9A-Z]{16}\b"), re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"),
]
#: 關鍵字＋值：只有『值』本身像憑證才遮蔽（中文敘述、「已過期」「completed」這類一般說明不算）
_KV = re.compile(r"(?i)(?<![A-Za-z])" + _KEYW + r"(?P<sep>\s*(?:是|為|[:=])\s*|\s+)(?P<v>[^\s,;，；。、]+)")
_AUTH = re.compile(r"(?i)authorization\s*[:=]\s*(?P<v>\S+(?:\s+\S+)?)")
_BEARER = re.compile(r"(?i)\b(?:bearer|basic)\s+(?P<v>[^\s,;]+)")
MASK = "【已遮蔽】"


def _looks_secret(v: str, strict: bool = False) -> bool:
    """值像憑證：ASCII 英數混合且夠長（有分隔符 ≥6、只靠空白分隔 ≥8），或 ≥20 的連續英數／符號，或 JWT、長 hex。中文不算。"""
    v = v.strip("'\"“”‘’()（）[]【】<>.,;:")
    if not v or not re.fullmatch(r"[A-Za-z0-9+/_\-=.!@#$%^&*~?:]+", v):
        return False
    if re.fullmatch(r"eyJ[\w\-]+\.[\w\-]+\.[\w\-]+", v) or re.fullmatch(r"[A-Fa-f0-9]{32,}", v):
        return True
    if len(v) >= 20 and re.fullmatch(r"[A-Za-z0-9+/_\-=.]+", v):
        return True
    return len(v) >= (8 if strict else 6) and bool(re.search(r"[A-Za-z]", v) and re.search(r"\d", v))


def _strip_invisible(text: str | None) -> str:
    """去掉控制字元與零寬／格式字元（可拿來把『pass​word』藏過比對），保留換行與 Tab。"""
    return "".join(c for c in (text or "") if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf"))


def _auth_sub(m) -> str:
    """Authorization：帶 Basic／Bearer 等方案＋值就遮蔽；否則只有值像憑證才遮蔽（「authorization: completed successfully」是一般說明）。"""
    parts = m.group("v").split()
    if len(parts) > 1 and parts[0].lower() in ("basic", "bearer", "digest", "token", "apikey", "negotiate", "ntlm"):
        return MASK
    return MASK if any(_looks_secret(x) for x in parts) else m.group(0)


def _redact_text(out: str) -> str:
    for rx in _ALWAYS:
        out = rx.sub(MASK, out)
    out = _AUTH.sub(_auth_sub, out)
    out = _BEARER.sub(lambda m: MASK if _looks_secret(m.group("v")) else m.group(0), out)
    return _KV.sub(lambda m: MASK if _looks_secret(m.group("v"), strict=not re.search(r"[是為:=]", m.group("sep"))) else m.group(0), out)


def redact(text: str | None) -> str:
    """遮蔽密碼／金鑰／token／Webhook 樣式（MD 輸出與寫入檢查共用同一份規則）。先去隱形字元並做 NFKC（全形→半形）再檢查；
    沒有機密時原樣回傳（不改變使用者的標點）。擋不住：拆字、同形字、刻意變形。"""
    base = _strip_invisible(text)
    norm = unicodedata.normalize("NFKC", base)
    red = _redact_text(norm)
    return red if red != norm else base


def _clean(text: str | None, limit: int = NOTE_MAX, required: bool = True, what: str = "內容") -> str:
    t = _strip_invisible(text).strip()
    if not t:
        if required:
            raise ValueError(f"{what}不能是空的")
        return ""
    if len(t) > limit:
        raise ValueError(f"{what}太長（上限 {limit} 字）")
    if redact(t) != t:
        raise ValueError(f"{what}疑似含有密碼／金鑰／token／Webhook，請移除後再存（這份記錄會被匯出到 MD）")
    return t


def _ctx(e: dict) -> str:
    return json.dumps({"title": e["title"], "source": e["source"], "category": e["category"], "partition": e["partition"],
                       "first_at": e["first_at"]}, ensure_ascii=False)


def _audit(conn, note_id: int, op: str, by: str | None, before, after, now: datetime) -> None:
    conn.execute("INSERT INTO host_ops_audit (note_id, op, at, by, before_json, after_json) VALUES (?,?,?,?,?,?)",
                 (note_id, op, _f(now), by, json.dumps(before, ensure_ascii=False) if before is not None else None,
                  json.dumps(after, ensure_ascii=False) if after is not None else None))


def _finish(conn, audit_hook, info: dict) -> None:
    """稽核（activity_log）與資料寫進同一個交易：稽核失敗＝整筆回滾。"""
    try:
        if audit_hook:
            audit_hook(conn, info)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ===================================================================== 寫入（ACT-05）
def add_note(conn, key: str, event_id: str, kind: str, text: str, by: str | None, now: datetime | None = None, audit_hook=None,
             client_key: str | None = None) -> dict:
    """client_key＝client 端冪等鍵：同一個請求重送（逾時重試、連點）只新增一次，回同一筆；同鍵但內容不同＝拒絕。"""
    now = now or _now()
    if kind not in KIND_LABEL:
        raise ValueError("類型要是 處理措施／已確認根因／改善結果")
    t = _clean(text)
    ck = (client_key or "").strip() or None
    if ck and not re.fullmatch(r"[A-Za-z0-9_\-]{8,64}", ck):
        raise ValueError("請求編號格式不對（8～64 字，英數與 _ -）")
    ensure_tables(conn)
    info = machine_info(conn, key)
    if ck:
        old = conn.execute("SELECT * FROM host_ops_note WHERE client_key=?", (ck,)).fetchone()
        if old:
            if (old["asset_serial"], old["event_id"], old["kind"], old["text"]) != (info["serials"][0], event_id, kind, t):
                raise ValueError("同一個請求編號已用在不同內容，請重新整理後再送")
            return dict(old)
    ev = find_event(conn, key, event_id, now)
    if ev is None:
        raise LookupError("找不到這個事件（可能已超出 400 天或不屬於這台主機）")
    try:
        cur = conn.execute("INSERT INTO host_ops_note (machine_key, asset_serial, event_id, kind, text, status, event_ctx, created_by, created_at) "
                           "VALUES (?,?,?,?,?, 'active', ?,?,?)", (info["key"], info["serials"][0], event_id, kind, t, _ctx(ev), by, _f(now)))
        nid = cur.lastrowid
        if ck:
            conn.execute("UPDATE host_ops_note SET client_key=? WHERE id=?", (ck, nid))
        _audit(conn, nid, "create", by, None, {"kind": kind, "text": t, "event_id": event_id}, now)
    except Exception:
        conn.rollback()
        raise
    _finish(conn, audit_hook, {"op": "create", "note_id": nid, "event_id": event_id, "kind": kind})
    return get_note(conn, nid)


def get_note(conn, nid: int) -> dict:
    r = conn.execute("SELECT * FROM host_ops_note WHERE id=?", (nid,)).fetchone() if _table(conn, "host_ops_note") else None
    if not r:
        raise LookupError(f"找不到處理紀錄 {nid}")
    return dict(r)


def _owned(conn, key: str, nid: int) -> dict:
    """處理紀錄必須屬於網址上的這台主機（任何一筆資產序號相符）。"""
    n = get_note(conn, nid)
    info = machine_info(conn, key)
    if n["asset_serial"] not in info["serials"]:
        raise LookupError("這筆處理紀錄不屬於這台主機")
    return n


def correct_note(conn, key: str, nid: int, text: str, reason: str | None, by: str | None, now: datetime | None = None, audit_hook=None) -> dict:
    """更正＝舊版標 corrected（保留原文），新增一筆新版（supersedes_id 指向舊版）。只有 active 的能更正。"""
    now = now or _now()
    t = _clean(text)
    why = _clean(reason, REASON_MAX, required=False, what="更正原因") or None
    ensure_tables(conn)
    old = _owned(conn, key, nid)
    if old["status"] != "active":
        raise ValueError("這筆已更正或作廢，請更正最新那一版")
    if t == old["text"]:
        raise ValueError("內容沒有變更")
    try:
        r = conn.execute("UPDATE host_ops_note SET status='corrected' WHERE id=? AND status='active'", (nid,))
        if r.rowcount != 1:
            raise ValueError("這筆剛被別人更正或作廢，請重新整理")
        cur = conn.execute("INSERT INTO host_ops_note (machine_key, asset_serial, event_id, kind, text, status, supersedes_id, event_ctx, created_by, created_at) "
                           "VALUES (?,?,?,?,?, 'active', ?,?,?,?)", (old["machine_key"], old["asset_serial"], old["event_id"], old["kind"], t, nid,
                                                                     old["event_ctx"], by, _f(now)))
        new_id = cur.lastrowid
        _audit(conn, new_id, "correct", by, {"note_id": nid, "text": old["text"], "status": "active", "created_by": old["created_by"]},
               {"text": t, "supersedes_id": nid, "reason": why}, now)
        _audit(conn, nid, "superseded", by, {"status": "active"}, {"status": "corrected", "by_note": new_id}, now)
    except Exception:
        conn.rollback()
        raise
    _finish(conn, audit_hook, {"op": "correct", "note_id": new_id, "event_id": old["event_id"], "kind": old["kind"]})
    return get_note(conn, new_id)


def void_note(conn, key: str, nid: int, reason: str, by: str | None, now: datetime | None = None, audit_hook=None) -> dict:
    """作廢＝標 voided 並留原因，原文與來源告警都不刪。"""
    now = now or _now()
    why = _clean(reason, REASON_MAX, what="作廢原因")
    ensure_tables(conn)
    old = _owned(conn, key, nid)
    if old["status"] != "active":
        raise ValueError("這筆已更正或作廢")
    try:
        r = conn.execute("UPDATE host_ops_note SET status='voided', voided_by=?, voided_at=?, void_reason=? WHERE id=? AND status='active'",
                         (by, _f(now), why, nid))
        if r.rowcount != 1:
            raise ValueError("這筆剛被別人更正或作廢，請重新整理")
        _audit(conn, nid, "void", by, {"status": "active", "text": old["text"], "created_by": old["created_by"]}, {"status": "voided", "reason": why}, now)
    except Exception:
        conn.rollback()
        raise
    _finish(conn, audit_hook, {"op": "void", "note_id": nid, "event_id": old["event_id"], "kind": old["kind"]})
    return get_note(conn, nid)


def note_audit(conn, nid: int) -> list[dict]:
    if not _table(conn, "host_ops_audit"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM host_ops_audit WHERE note_id=? ORDER BY id", (nid,))]


# ===================================================================== MD（ACT-02；按需產生，不落檔，不接模型）
_URLISH = re.compile(r"(?i)(?:\b(?:https?|ftp|file)[:：]\S*|\bwww\.\S+|(?<![\w.])[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+)")


def _c(v) -> str:
    """表格儲存格／內文：遮蔽機密，並中和 Markdown 連結／圖片／HTML。順序很重要：
    先把輸入原有的反斜線全轉成全形（之後只剩我們自己加的跳脫，不會被偶數反斜線抵銷），再跳脫 []()<>、打斷 `//`，
    最後把裸網址與 email 包進反引號。"""
    s = "" if v is None else str(v)
    s = redact(s).replace("\\", "＼").replace("\r", " ").replace("\n", " ").replace("|", "／").replace("`", "'")
    s = s.replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"([\[\]()])", r"\\\1", s).replace("//", "／／")
    s = _URLISH.sub(lambda m: "`" + m.group(0) + "`", s)
    return s.strip() or "—"


def render_md(d: dict, screen_batch: str | None = None) -> str:
    """把 build() 的結果（畫面同一份）轉成 Markdown。事件 ID、統計、明細同一批；內部版保留主機／IP／分割區。"""
    m, w = d["machine"], d["window"]
    L = [f"# 單機維運記錄：{_c(m['name'])}", "",
         f"- 範圍：{w['label']}（{w['start']} ～ {w['end']}，{_c(w['tz'])}）",
         f"- 批次：`{d['batch']}`（與畫面同一批；資料不變則批次不變）",
         f"- 產生時間：{d['generated_at']}（{_c(w['tz'])}）",
         f"- 主機：{_c(m['name'])}／IP {_c('、'.join(m['ips']) or NO_DATA)}／資產編號 {_c('、'.join(m['serials']))}／機器代號 `{_c(m['key'])}`", "",
         "> 內部使用。已恢復不等於根因已解決；不同來源的事件並列、不自動合併；缺資料標「未取得」，不是 0 也不是正常。", ""]
    if screen_batch and screen_batch != d["batch"]:
        L[3:3] = [f"- **資料已更新，與畫面批次 `{_c(screen_batch)}` 不同**（目前批次 `{d['batch']}`）：以本檔為準，畫面請重新整理。"]
    if not w["future"]:
        t = d["totals"]
        if not t["events"] and not d["evidence_any"]:
            L += ["## 摘要統計", "", "- 事件：**此期間未取得**（事件來源對這台在這個期間都沒有資料；不代表沒有異常，請看下方資料來源與缺口）", ""]
        else:
            L += ["## 摘要統計", "", f"- 事件 {t['events']} 筆（計入故障次數 {t['countable']}、持續中 {t['ongoing']}、結束時間未知 {t['unknown_end']}）；磁碟 {t['disk']} 次、記憶體 {t['memory']} 次", ""]
        L += ["| 項次 | 類別 | 分割區／項目 | 來源 | 次數 | 持續中 | 事件ID |", "|---|---|---|---|---|---|---|"]
        for r in d["counts"]:
            L.append(f"| C{r['__no']} | {_c(r['category_label'])} | {_c(r['partition'])} | {_c(r['source_label'])} | {r['count']} | {r['ongoing']} | {_c('、'.join(r['event_ids']))} |")
        if not d["counts"]:
            L.append("| — | 此期間各來源沒有可計次的事件（請看下方資料來源涵蓋，來源沒資料不等於正常） | | | | | |")
        L += ["", "## 事件明細", "", "| 項次 | 事件ID | 來源 | 類別 | 項目 | 首發 | 最後 | 恢復／結案／結束 | 狀態 | 當時指標 | 比對 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        ST = {"ongoing": "持續中", "recovered": "已恢復", "closed": "已結案", "unknown": "結束時間未知"}
        for e in d["events"]:
            if e["recovered_at"]:
                rec = e["recovered_at"] + ("（僅此項消失，裝置仍告警）" if e.get("partial_recover") else "")
            elif e.get("closed_at"):
                rec = f"結案 {e['closed_at']}（{e['closed_label']}）"
            else:
                rec = "尚未恢復" if e["status"] == "ongoing" else (e.get("recovered_label") or NO_DATA)
            L.append(f"| E{e['__no']} | {_c(e['id'])} | {_c(e['source_label'])} | {_c(e['category_label'])} | {_c(e['title'])} | {_c(e['first_at'])} | "
                     f"{_c(e['last_at'] or NO_DATA)} | {_c(rec)} | {ST.get(e['status'], e['status'])} | "
                     f"{_c(e['metric_text'])} | {_c(e['match_label'])} |")
        if not d["events"]:
            L.append("| — | 此期間沒有事件 | | | | | | | | | |")
        L += ["", "## 處理紀錄（人工填寫）", ""]
        for e in d["events"]:
            h = e["handling_text"]
            L.append(f"- **E{e['__no']} {_c(e['id'])}**：處理措施＝{_c(h['action'])}；已確認根因＝{_c(h['root_cause'])}；改善結果＝{_c(h['outcome'])}")
        if not d["events"]:
            L.append("- （無事件）")
        if d["orphan_notes"]:
            L.append("")
            L.append(f"另有 {len(d['orphan_notes'])} 筆處理紀錄對應的事件已超出本期間（事件ID：{_c('、'.join(sorted({n['event_id'] for n in d['orphan_notes']})))}）。")
        L.append("")
    L += ["## 未來 30 天已知排程／到期", "", "| 項次 | 類型 | 項目 | 日期 | 日期精度 | 來源 | 說明 |", "|---|---|---|---|---|---|---|"]
    for u in d["upcoming"]:
        L.append(f"| U{u['__no']} | {_c(u['kind_label'])} | {_c(u['title'])} | {_c(u['date'])} | {_c(u['precision'])} | {_c(u['source_label'])} | {_c(u['detail'])} |")
    if not d["upcoming"]:
        L.append("| — | 已知來源在未來 30 天內沒有項目（見下方來源缺口） | | | | | |")
    L += ["", "## 資料來源與缺口", "", "| 來源 | 狀態 | 涵蓋（此機） | 說明 |", "|---|---|---|---|"]
    SL = {"has_data": "有資料", "no_data": "無資料", "no_machine_data": "此機未取得", "none_recorded": "無紀錄", "n/a": "不適用／無此來源", "error": "讀取失敗"}
    for c in d["coverage"] + d["upcoming_coverage"]:
        rng = f"{c.get('covers_from') or '—'} ～ {c.get('covers_to') or '—'}" if c.get("covers_from") or c.get("covers_to") else "—"
        L.append(f"| {_c(c['label'])} | {SL.get(c['state'], c['state'])} | {_c(rng)} | {_c(c.get('note'))} |")
    if d["pending"]:
        L += ["", "## 待核（未計次）", "", "| 來源 | 裝置 | IP | 首次出現 | 原因 | 候選主機 | 提示 |", "|---|---|---|---|---|---|---|"]
        for p in d["pending"]:
            L.append(f"| {_c(p['source_label'])} | {_c(p['device'])} | {_c(p['ip'])} | {_c(p['first_at'])} | {_c(p['reason'])} | {_c('、'.join(p['candidates']))} | {_c(p.get('hint'))} |")
    L += ["", "## 已知限制", ""] + [f"- {_c(x)}" for x in d["limits"]]
    L += ["", "---", "資料範圍與數字同畫面；本檔由系統按需產生，不接任何模型 API，不含密碼／金鑰／token／Webhook。", ""]
    return "\n".join(L)


def md(conn, key: str, period: str = "30d", now: datetime | None = None, screen_batch: str | None = None) -> tuple[str, dict]:
    d = build(conn, key, period, now)
    return render_md(d, screen_batch), d
