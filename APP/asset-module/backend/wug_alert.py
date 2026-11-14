"""WhatsUp 告警事件（2.043.0，第一階段）：中繼 PC 每分鐘的告警快照 → 告警事件表 → Teams 發生／升級／恢復。

設計來源：AI/設計_WUG告警判斷_2026-11-03_PC與GPT.md（含使用者 11-03 決定）、AI/NB規格_2026-11-03_WUG告警進Teams.md。

## 資料怎麼進來（現在走中繼檔案，日後 WhatsUp 直接推也寫同一張表）

中繼 PC 的 wug_alert_collect.ps1（devkit D7）每分鐘問 WhatsUp 一次群組狀態：沒變就不寫（每 5 分鐘一份心跳批次）；
有變才翻設備清單、問有問題的設備的監控項目，輸出 source=whatsup_alert 的批次（一個 snapshot.json）。
relay_import 的 WugAlertAdapter 驗證後呼叫這裡的 parse_snapshot／apply_snapshot。

## 單一事實來源：alert_event（一個「設備＋監控項目」的一次問題＝一列）

* 新出現 → 開一筆（started_at 依 WhatsUp 的「Down at least N min」往回推，推不出來用這次快照時間）。
* 還在 → 更新 last_seen／狀態文字／持續分鐘。
* **完整**掃描裡不見了 → 結案（ended_at＝這次快照時間，已恢復）。
* **不完整**掃描（翻頁逾時）或心跳批次 → 只開新的、只更新還在的，**絕不結案**（沒看到≠恢復）。
* 監控項目清單讀不到的設備：保留它既有的監控項目事件，另開／維持一筆「(設備)」層級事件。
* WhatsUp 自己的 Maintenance 狀態 → 照記（tier=maint），不推通知。
* IP 對資產：沿用 relay_import.match_asset（同一台以 machine_key 判斷）；對不到或多重對應 → machine_key 空白、
  列進 relay_import_unmatched（source=whatsup_alert），不猜。

## 分級（使用者 11-03 決定；資料缺漏往高分級，不猜成低風險）

* 業務系統分類「第一類」且環境是正式（含備援）或環境未填／認不出 → 🔴 立即推。
* 第一類的測試／OA 機、其他分類（第二、三類、未分級）的任何環境 → 🟡 摘要（2.060.19 起預設併入每天 08:00 的非緊急彙整 notice_digest）。
* IP 對不到資產（不知道它是不是第一類）→ 設定 unmatched_tier（預設 🔴）。
* 🟡 持續超過 escalate_min（預設 60 分）→ 升級成 🔴，立即推一則「升級」。
* 沒有自訂維護時段（使用者 11-03：只有開盤／非開盤）；開盤／非開盤只改訊息標示，不影響推不推。

## 通知（一個事件只推「發生、升級、恢復」，同一階段不重送；notified 欄記已排過的階段）

* 2.066.x（使用者 11-13「系統首頁才是重點，Email／Teams 照系統判斷」）：**只有進入「需要注意」推一次「發生」、恢復推一次**
  （需要注意＝斷滿 alert_attention_min 分還沒恢復、不在例行時段內，alert_attention.judge_one，首頁同一支；不分 🔴／🟡、不分開盤）。
  升級只改分級不另推；門檻內恢復、例行時段內的不立即推（彙整開著才寫進每日彙整）；反覆斷線每日彙整一台一行。
* （舊規則，2.066.x 前）🔴：發生、升級、恢復各一次（同一輪的多筆合成一則訊息，但每台各自一行，不互相掩蓋）。
* 🟡：每 digest_min（預設 60 分）整理一次摘要，存進每天 08:00 的彙整；彙整關著不再每小時推。
* 監控中斷：heartbeat_min（預設 10 分）沒收到任何告警批次 → 一則 🔴「監控中斷」；之後收到 → 一則「監控恢復」。
  從沒收到過批次時不判斷（還沒接上）。中斷期間開著的事件不結案。
* 送出沿用 teams_notify 的佇列（啟用、對外確認、登入停用 fail-closed、重試、投遞紀錄）；Teams 沒啟用時只記錄、不送，
  已記錄的階段之後也不補送（避免一啟用就洗版）。訊息第一行就是結論（哪台、什麼、多久、系統、負責人、開盤／非開盤）。

## 盤中靜默窗

本模組只讀中繼 PC 放進來的檔案、寫本機資料表、送 Teams，**不連任何主機、不連 WhatsUp**。使用者 2026-11-03 決定監控
要 24 小時（含開盤時間），所以 tick 登記為 C 類例外（quiet_window ENTRIES 的 wug_alert_tick），不受平日 08:30–15:00 限制。

## 保存

事件預設保存 retention_days＝730 天（2 年）；每天清一次已結案且超過保存期的。

資料函式（設定）不自己 commit，由路由與稽核同一交易提交；apply_snapshot 在匯入交易裡（由 relay_import 提交）；tick 自己 commit。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta

log = logging.getLogger("wug_alert")

SOURCE = "whatsup_alert"
BATCH_PREFIX = "wugalert_"
SETTING_KEY = "wug_alert"
K_LAST_RECV = "wug_alert_last_recv"        # 伺服器收到最後一批告警批次的時間（心跳用，台北時間）
K_LAST_SNAP = "wug_alert_last_snap"        # 已套用的最新快照時間（舊批次晚到不倒退）
K_HB_DOWN = "wug_alert_hb_down"            # 1＝已發過「監控中斷」，等恢復
K_LAST_DIGEST = "wug_alert_last_digest"
K_LAST_PURGE = "wug_alert_last_purge"
FMT = "%Y-%m-%d %H:%M:%S"

DEFAULTS = {"enabled": False, "heartbeat_min": 10, "escalate_min": 60, "digest_min": 60, "unmatched_tier": "red", "retention_days": 730,
            "notify_policies": "", "confirm_min": 10}
#: confirm_min（2.061.12，使用者 11-12「Outlook 還是收到一堆無用告警」）：🔴 斷線要持續滿這麼多分鐘還沒恢復才立即推「發生」；
#: 在這之前就恢復的（夜間排程重開機多半 0～8 分就回來）不立即推，改併進每天 08:00 的非緊急彙整（彙整關著才照舊推「短暫事故」）。
#: 只用在「非開盤」發生的事件；開盤時段照舊一斷就推（GPT 11-12 審查第 5 點）。0＝不等（舊行為）。代價：夜間真故障晚 confirm_min 分鐘才知道。
#: notify_policies（2.060.24，使用者 11-11「先主機組通知就好」）：WhatsUp 動作政策名稱關鍵字，逗號或換行分隔；空白＝全部照推（舊行為）。
#: 有設時只推「監控項目掛的動作政策名稱含任一關鍵字」的告警；WhatsUp 明講沒掛政策的照樣記錄、畫面看得到，只是不推。
#: 不知道有沒有掛（收集端沒帶這個欄位、整台設備層級、讀不到監控明細）一律照推——寧可多推，不可漏推。
POLICY_MAX = 500
LIMITS = {"heartbeat_min": (3, 240), "escalate_min": (5, 1440), "digest_min": (10, 1440), "retention_days": (90, 3650), "confirm_min": (0, 120)}

TIERS = ("red", "yellow", "maint")
TIER_LABEL = {"red": "🔴 立即", "yellow": "🟡 每日彙整", "maint": "維護中（不推）"}
DEVICE_MONITOR = "(設備)"
MAINT_MONITOR = "(維護中)"
UNKNOWN_MONITOR = "(狀態未知)"
MONREAD_MONITOR = "(監控明細讀取失敗)"      # 第一次出現就讀不到監控項目：是收集問題，不當設備故障（最高 🟡、不升級）
SUSPECT_HOLD_MIN = 5                        # 疑似收集異常：最初那一批擱置滿這麼久才結案
SUSPECT_NOTE_COOLDOWN_MIN = 30              # 疑似收集異常的 🟡 通知冷卻
K_SUSPECT_NOTE = "wug_alert_suspect_note"
NEVER_MIN = 15                              # 啟用後超過這麼久還沒收到第一批 → 「監控未啟動」
SUSPECT_MIN_EVENTS = 5                      # 一次完整掃描要結案超過這麼多筆且超過開著的一半 → 疑似收集異常，先不結案
K_ENABLED_AT = "wug_alert_enabled_at"
K_SUSPECT = "wug_alert_suspect"
MAX_DEVICES = 5000
MAX_MONITORS = 100
MAX_LINES = 30
#: 只通知「最近 STALE_MIN 分鐘內」發生／恢復的事件；更早的（剛啟用時、或補匯積壓批次）只標 quiet、不送，畫面照樣看得到。
#: 起因 2026-11-11：.14 啟用時有 11-10 晚上起的積壓批次，每匯入一段就推一輪，3 分鐘 8 則（177／198／684／597 筆）。
STALE_MIN = 30
NOTICE_KEEP = 2000

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS alert_event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL DEFAULT 'whatsup',
        wug_device_id TEXT NOT NULL,
        monitor TEXT NOT NULL,
        device_name TEXT, wug_host TEXT, ip TEXT,
        machine_key TEXT, asset_serial TEXT, match TEXT,          -- matched／no_asset／ambiguous
        hostname TEXT, api_id TEXT, system_name TEXT, sys_class TEXT, environment TEXT, owner TEXT, department TEXT,
        wug_state TEXT, status_text TEXT,
        tier TEXT NOT NULL, tier_reason TEXT,
        started_at TEXT NOT NULL, ended_at TEXT, last_seen TEXT NOT NULL, duration_min INTEGER NOT NULL DEFAULT 0,
        market TEXT,                                              -- 發生當下：開盤／非開盤
        first_batch TEXT, last_batch TEXT,
        confirmed INTEGER NOT NULL DEFAULT 0,                     -- 第二階段才自動確認；現在恆 0（未確認）
        verdict TEXT, verdict_by TEXT, verdict_at TEXT,           -- 人工回報 true（真）／false（假）
        notified TEXT NOT NULL DEFAULT '',                        -- 已排過的通知階段：,open,escalate,recover,digest,digest_recover,
        created_at TEXT NOT NULL)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_alert_event_open ON alert_event(wug_device_id, monitor) WHERE ended_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_alert_event_started ON alert_event(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_alert_event_mk ON alert_event(machine_key)",
    "CREATE INDEX IF NOT EXISTS idx_alert_event_ip ON alert_event(ip)",
    """CREATE TABLE IF NOT EXISTS alert_scan (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT NOT NULL UNIQUE, snap_at TEXT NOT NULL, kind TEXT NOT NULL,     -- scan／heartbeat／stale
        complete INTEGER NOT NULL, pages_read INTEGER, devices_seen INTEGER, problems INTEGER,
        stopped_reason TEXT, group_json TEXT, opened INTEGER, updated INTEGER, closed INTEGER, received_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS alert_notice (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL, kind TEXT NOT NULL,          -- open／escalate／recover／digest／heartbeat_down／heartbeat_up
        tier TEXT, events INTEGER, status TEXT NOT NULL,  -- queued（排進 Teams 佇列）／recorded（Teams 沒啟用，只記錄）
        delivery_key TEXT, title TEXT, summary TEXT)""",
    # 不通知清單（2.060.18，使用者 11-11：網頁上自己勾）：符合的事件照樣記錄、畫面看得到，只是不推 Teams
    """CREATE TABLE IF NOT EXISTS alert_mute (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wug_device_id TEXT NOT NULL,
        monitor TEXT NOT NULL,                       -- '*'＝這台全部監控項目
        days TEXT NOT NULL DEFAULT '',               -- 星期幾（1＝週一…7＝週日，逗號分隔）；空白＝每天
        time_from TEXT NOT NULL DEFAULT '',          -- HH:MM；起訖都空白＝全天；起 > 訖＝跨午夜（例 22:00～06:00）
        time_to TEXT NOT NULL DEFAULT '',
        device_label TEXT, note TEXT,
        created_by TEXT NOT NULL, created_at TEXT NOT NULL)""",
]


def ensure(conn: sqlite3.Connection) -> None:
    for s in SCHEMA:
        conn.execute(s)
    if "policy" not in {r[1] for r in conn.execute("PRAGMA table_info(alert_event)")}:
        conn.execute("ALTER TABLE alert_event ADD COLUMN policy TEXT")     # WhatsUp 動作政策名稱；NULL＝不知道、''＝明確沒掛


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _get(conn, key: str, default: str = "") -> str:
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return r[0] if r and r[0] is not None else default


def _set(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def _p(t: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(t)[:19], FMT) if t else None
    except ValueError:
        return None


def _s(d: datetime) -> str:
    return d.strftime(FMT)


def _now() -> datetime:
    import quiet_window as qw
    return qw.local_now()


# ── 設定 ───────────────────────────────────────────────────────────────
def load_settings(conn) -> dict:
    raw = _get(conn, SETTING_KEY, "")
    cfg = dict(DEFAULTS)
    if raw:
        try:
            saved = json.loads(raw) or {}
        except (ValueError, TypeError) as exc:
            raise ValueError(f"告警設定讀不出來（內容壞掉，不是沒設定）：{type(exc).__name__}") from exc
        cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
    return cfg


def validate(cfg: dict) -> dict:
    out = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    out["enabled"] = bool(out["enabled"])
    for k, (lo, hi) in LIMITS.items():
        v = out[k]
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ValueError(f"{k} 要是 {lo}～{hi} 的整數")
    if out["unmatched_tier"] not in ("red", "yellow"):
        raise ValueError("unmatched_tier 只能是 red 或 yellow（資料缺漏不可以猜成更低）")
    pol = out["notify_policies"]
    if pol is None:
        pol = ""
    if not isinstance(pol, str) or len(pol) > POLICY_MAX:
        raise ValueError(f"notify_policies 要是 {POLICY_MAX} 字以內的文字")
    out["notify_policies"] = ", ".join(policy_keywords(pol))
    return out


def save_settings(conn, cfg: dict) -> dict:
    """存設定。**不 commit**（路由與稽核同一交易）。"""
    out = validate(cfg)
    try:
        was = load_settings(conn)["enabled"]
    except ValueError:
        was = False
    if out["enabled"] and (not was or not _get(conn, K_ENABLED_AT, "")):
        _set(conn, K_ENABLED_AT, _s(_now()))          # 從這時起算「監控未啟動」
    _set(conn, SETTING_KEY, json.dumps(out, ensure_ascii=False))
    return out


# ── 快照解析（relay_import 的 parse 呼叫；壞資料丟 ValueError，由 adapter 轉成 RelayReject）──────────
_MIN_RE = re.compile(r"at least\s+(\d+)\s*(sec|second|min|minute|hour|hr|day)s?", re.I)


def minutes_from_status(text: str | None) -> int | None:
    """WhatsUp 狀態文字「Down at least 20 min」→ 20（分）。讀不出來回 None（不猜）。"""
    m = _MIN_RE.search(text or "")
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2).lower()
    if u.startswith("sec"):
        return n // 60
    if u.startswith(("hour", "hr")):
        return n * 60
    if u.startswith("day"):
        return n * 1440
    return n


def _txt(v, n: int = 200) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        raise ValueError("欄位要是文字")
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(v)).strip()[:n]


def is_down_monitor(m: dict) -> bool:
    """監控項目算「有問題」：沒被停用，且狀態文字以 Down 開頭（WhatsUp 的 'Down at least 20 min'）。"""
    if str(m.get("enabled", "")).strip().lower() in ("false", "0", "no"):
        return False
    return str(m.get("status") or "").strip().lower().startswith("down")


def parse_snapshot(data) -> dict:
    """驗證並整理 snapshot.json。回 {kind, complete, pages_read, devices_seen, stopped_reason, group, devices[...]}。"""
    import ipaddress
    if not isinstance(data, dict):
        raise ValueError("最外層要是物件")
    kind = data.get("kind")
    if kind not in ("scan", "heartbeat"):
        raise ValueError("kind 只能是 scan 或 heartbeat")
    scan = data.get("scan") if isinstance(data.get("scan"), dict) else {}
    if kind == "scan" and type(scan.get("complete")) is not bool:
        raise ValueError("scan.complete 要是 JSON 的 true／false（字串或數字不收）")
    devs = data.get("devices") or []
    if not isinstance(devs, list) or len(devs) > MAX_DEVICES:
        raise ValueError(f"devices 要是清單且不超過 {MAX_DEVICES} 台")
    group = data.get("group") if isinstance(data.get("group"), (dict, list)) else {}
    out = {"kind": kind, "complete": scan.get("complete") is True and kind == "scan",
           "pages_read": int(scan.get("pages_read") or 0) if str(scan.get("pages_read") or "0").isdigit() else 0,
           "devices_seen": int(scan.get("devices_seen") or 0) if str(scan.get("devices_seen") or "0").isdigit() else 0,
           "stopped_reason": _txt(scan.get("stopped_reason"), 300), "group": group, "devices": []}
    seen = set()
    for d in devs:
        if not isinstance(d, dict):
            raise ValueError("devices 每一項要是物件")
        did = _txt(d.get("id"), 40)
        if not did:
            raise ValueError("設備缺 id")
        if did in seen:
            raise ValueError(f"設備 id 重複：{did}")
        seen.add(did)
        ip = _txt(d.get("ip"), 64)
        try:
            ip = str(ipaddress.ip_address(ip)) if ip else ""
        except ValueError:
            ip = ""                    # WhatsUp 的 networkAddress 偶爾是名稱：不當 IP 用、不猜
        if type(d.get("monitors_ok")) is not bool:
            raise ValueError(f"設備 {did} 的 monitors_ok 要是 JSON 的 true／false")
        mons = d.get("monitors") or []
        if not isinstance(mons, list) or len(mons) > MAX_MONITORS:
            raise ValueError(f"設備 {did} 的 monitors 要是清單且不超過 {MAX_MONITORS} 項")
        ml = []
        for m in mons:
            if not isinstance(m, dict):
                raise ValueError(f"設備 {did} 的 monitors 每一項要是物件")
            ml.append({"name": _txt(m.get("name"), 120) or "(未命名監控)", "type": _txt(m.get("type"), 40),
                       "status": _txt(m.get("status"), 120), "enabled": _txt(m.get("enabled"), 10),
                       "policy": _txt(m.get("policy"), 200) if "policy" in m else None})     # 沒帶＝不知道（舊收集端）
        out["devices"].append({"id": did, "name": _txt(d.get("name"), 120), "host_name": _txt(d.get("host_name"), 160), "ip": ip,
                               "worst_state": _txt(d.get("worst_state"), 40), "monitors_ok": d["monitors_ok"], "monitors": ml})
    # 保險：收集端說完整，但看到的台數離群組總數太遠（或一台都沒有）→ 當不完整（不結案）
    total = group.get("totalDevices") if isinstance(group, dict) else None
    if out["complete"]:
        if out["devices_seen"] <= 0:
            out["complete"], out["stopped_reason"] = False, "收集端標完整但一台設備都沒看到，當不完整"
        elif type(total) is not int or total <= 0:
            out["complete"], out["stopped_reason"] = False, "群組狀態沒有 totalDevices，無法確認清單是否完整，當不完整"
        elif out["devices_seen"] < total * 0.98:
            out["complete"], out["stopped_reason"] = False, f"看到 {out['devices_seen']} 台、群組總數 {total}，差太多，當不完整"
    return out


def problems_of(dev: dict) -> list[tuple[str, str, str]]:
    """一台設備這次的問題清單 [(監控項目, WhatsUp 狀態, 狀態文字)]。Maintenance 一律一筆設備層級。"""
    ws = dev["worst_state"]
    if ws.lower() == "maintenance":
        return [(MAINT_MONITOR, "Maintenance", "Maintenance")]
    if ws.lower() == "unknown":
        return [(UNKNOWN_MONITOR, "Unknown", "狀態未知（WhatsUp 回 Unknown）")]
    if not dev["monitors_ok"]:
        return [(MONREAD_MONITOR, ws or "Down", f"監控明細讀取失敗（WhatsUp 設備狀態 {ws or '不明'}）")]
    downs = [m for m in dev["monitors"] if is_down_monitor(m)]
    if downs:
        out, names = [], set()
        for m in downs:
            if m["name"] not in names:
                names.add(m["name"])
                out.append((m["name"], "Down", m["status"]))
        return out
    return [(DEVICE_MONITOR, ws or "Down", ws or "Down")]


def policy_map(dev: dict) -> dict:
    """監控項目名稱 → 動作政策名稱（只放收集端有帶的；同名多筆取有掛政策的那筆）。整台／維護／讀不到這些合成項目不在裡面＝不知道。"""
    out: dict = {}
    for m in dev.get("monitors") or []:
        p = m.get("policy")
        if p is None:
            continue
        if p or m["name"] not in out:
            out[m["name"]] = p
    return out


def policy_keywords(text: str | None) -> list[str]:
    out = []
    for k in re.split(r"[,，\n]+", text or ""):
        k = k.strip()
        if k and k not in out:
            out.append(k)
    return out


def policy_ok(policy: str | None, keywords: list[str]) -> bool:
    """這則要不要推：沒設關鍵字＝推；不知道有沒有掛政策（NULL）＝推；有掛且名稱含任一關鍵字＝推；其餘不推。"""
    if not keywords or policy is None:
        return True
    low = policy.lower()
    return any(k.lower() in low for k in keywords)


# ── 資產對應與分級 ─────────────────────────────────────────────────────
def asset_context(conn, ip: str) -> dict:
    """IP → 資產與業務系統（不猜：對不到或多重對應回 match≠matched、其他欄空白）。"""
    import relay_import
    out = {"match": "no_asset", "machine_key": None, "asset_serial": None, "hostname": None, "api_id": None, "system_name": None,
           "sys_class": None, "environment": None, "owner": None, "department": None}
    if not ip:
        return out
    res, _n, serial = relay_import.match_asset(conn, ip)
    out["match"] = res
    if res != "matched" or not serial:
        return out
    import system_stats
    h = conn.execute("SELECT asset_serial, hostname, ip, api_id, environment FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
    if h is None:
        out["match"] = "no_asset"
        return out
    out.update(machine_key=system_stats.machine_key(h["hostname"], h["ip"], h["asset_serial"]), asset_serial=serial,
               hostname=h["hostname"], api_id=(h["api_id"] or "").strip() or None, environment=(h["environment"] or "").strip() or None)
    if out["api_id"]:
        try:
            b = conn.execute("SELECT name, ap_owner, ap_department, sys_class, sys_class_override FROM business_system WHERE api_id = ?",
                             (out["api_id"],)).fetchone()
        except sqlite3.OperationalError:
            b = None                   # 沒有業務系統表＝分類不明
        if b is not None:
            cls, _core = system_stats.class_from_label(b["sys_class"])
            ovr = (b["sys_class_override"] or "").strip()
            out.update(system_name=b["name"], owner=b["ap_owner"], department=b["ap_department"], sys_class=ovr or cls)
    return out


def classify(ctx: dict, wug_state: str, cfg: dict) -> tuple[str, str]:
    """回 (tier, 理由)。規則見檔頭；資料缺漏往高分級。"""
    import env_group
    if wug_state == "Maintenance":
        return "maint", "WhatsUp 上設為維護中，只記錄不推"
    if ctx["match"] != "matched":
        why = "IP 對不到資產" if ctx["match"] == "no_asset" else "IP 對到不只一台"
        t = cfg.get("unmatched_tier", "red")
        return t, f"{why}，不知道是不是第一類，往高分級（{'立即' if t == 'red' else '摘要'}）"
    env = env_group.classify(ctx["environment"])
    env_txt = {env_group.PROD: "正式", env_group.NONPROD: "測試", env_group.OA: "OA", env_group.UNSET: "環境未填", env_group.UNKNOWN: "環境認不出"}[env]
    cls = ctx["sys_class"] or ("未登記業務系統" if not ctx["api_id"] else "分類未知")
    if ctx["sys_class"] == "第一類" and env in (env_group.PROD, env_group.UNSET, env_group.UNKNOWN):
        return "red", f"第一類・{env_txt}" + ("（環境資料缺漏，往高分級）" if env != env_group.PROD else "")
    return "yellow", f"{cls}・{env_txt}"


def market_label(conn, at: datetime) -> str:
    """開盤／非開盤：用盤中靜默窗的起訖與休市日（不管靜默窗開關）。只影響訊息標示。"""
    import quiet_window as qw
    try:
        return "開盤" if qw.is_market_hours(conn, at) else "非開盤"
    except Exception:  # noqa: BLE001 - 判斷不了就照實寫「不明」，不影響推不推
        return "開盤時段不明"


# ── 套用快照（在匯入交易內；不 commit）────────────────────────────────
def _dur(start: str, end: datetime) -> int:
    s = _p(start)
    return max(0, int((end - s).total_seconds() // 60)) if s else 0


def apply_snapshot(conn, batch_id: str, snap_at: datetime, snap: dict, now_text: str) -> dict:
    """把一份快照套到事件表。snap_at＝快照時間（台北，naive）。回統計數字（給匯入紀錄）。"""
    import relay_import
    ensure(conn)
    cfg = load_settings(conn)
    at = _s(snap_at)
    stats = {"devices": len(snap["devices"]), "host_days": 0, "points": 0, "skipped_existing": 0, "unmatched": 0, "bad_values": 0}
    last = _get(conn, K_LAST_SNAP, "")
    _set(conn, K_LAST_RECV, now_text)                     # 心跳：伺服器收到的時間（不靠中繼 PC 時鐘）
    gj = json.dumps(snap["group"], ensure_ascii=False)[:4000]
    if last and at < last:
        conn.execute("INSERT OR IGNORE INTO alert_scan (batch_id, snap_at, kind, complete, pages_read, devices_seen, problems, stopped_reason, "
                     "group_json, opened, updated, closed, received_at) VALUES (?,?,?,?,?,?,?,?,?,0,0,0,?)",
                     (batch_id, at, "stale", 0, snap["pages_read"], snap["devices_seen"], len(snap["devices"]), "比已套用的快照舊，只記錄不套用", gj, now_text))
        stats["skipped_existing"] = 1
        return stats
    _set(conn, K_LAST_SNAP, at)
    opened = updated = closed = 0
    open_rows = {(r["wug_device_id"], r["monitor"]): r for r in conn.execute("SELECT * FROM alert_event WHERE ended_at IS NULL")}
    if snap["kind"] == "heartbeat":
        # 群組狀態沒變：開著的都還在，只更新最後看到時間與持續分鐘
        for r in open_rows.values():
            conn.execute("UPDATE alert_event SET last_seen = ?, duration_min = ?, last_batch = ? WHERE id = ?",
                         (at, _dur(r["started_at"], snap_at), batch_id, r["id"]))
            updated += 1
    else:
        present: set[tuple[str, str]] = set()
        keep_devices: set[str] = set()                     # 監控項目讀不到的設備：它既有的監控項目事件不結案
        ctx_cache: dict[str, dict] = {}
        for dev in snap["devices"]:
            ws = dev["worst_state"].lower()
            mine = [k for k in open_rows if k[0] == dev["id"] and k[1] not in (MAINT_MONITOR, UNKNOWN_MONITOR)]
            if ws == "unknown" or not dev["monitors_ok"]:
                keep_devices.add(dev["id"])          # 狀態未知／監控項目讀不到：它既有的事件都不結案
            if ws not in ("maintenance", "unknown") and not dev["monitors_ok"] and mine:
                # 已有事件的設備這次讀不到監控項目：沿用既有事件（更新最後看到），不另開「(設備)」
                for k in mine:
                    r = open_rows[k]
                    present.add(k)
                    conn.execute("UPDATE alert_event SET last_seen = ?, duration_min = ?, last_batch = ? WHERE id = ?",
                                 (at, _dur(r["started_at"], snap_at), batch_id, r["id"]))
                    updated += 1
                continue
            probs = problems_of(dev)
            named = [m for m, _st, _tx in probs if m not in (DEVICE_MONITOR, MAINT_MONITOR, UNKNOWN_MONITOR, MONREAD_MONITOR)]
            for generic in (MONREAD_MONITOR, DEVICE_MONITOR):
                dk = (dev["id"], generic)
                if dk in open_rows and named and (dev["id"], named[0]) not in open_rows:
                    # 上次只知道「設備」有問題（或讀不到監控項目），這次讀到是哪個監控項目：改名沿用同一筆（不推恢復再推一次發生）。
                    # 從「監控明細讀取失敗」改過來的要重新分級（那時最高只到 🟡），該立即的就變 🔴、由通知那邊推「發生」。
                    r = open_rows.pop(dk)
                    conn.execute("UPDATE alert_event SET monitor = ? WHERE id = ?", (named[0], r["id"]))
                    if generic == MONREAD_MONITOR:
                        t2, why2 = classify(asset_context(conn, dev["ip"]), "Down", cfg)
                        if t2 == "red":
                            conn.execute("UPDATE alert_event SET tier = 'red', tier_reason = ? WHERE id = ?", (why2, r["id"]))
                    open_rows[(dev["id"], named[0])] = conn.execute("SELECT * FROM alert_event WHERE id = ?", (r["id"],)).fetchone()
            pols = policy_map(dev)
            for mon, state, text in probs:
                key = (dev["id"], mon)
                present.add(key)
                row = open_rows.get(key)
                if row is not None:
                    conn.execute("UPDATE alert_event SET last_seen = ?, duration_min = ?, status_text = ?, wug_state = ?, device_name = ?, last_batch = ?, "
                                 "policy = COALESCE(?, policy) WHERE id = ?", (at, _dur(row["started_at"], snap_at), text, state, dev["name"] or row["device_name"],
                                                                      batch_id, pols.get(mon), row["id"]))
                    updated += 1
                    continue
                ip = dev["ip"]
                ck = ip or "wug:" + dev["id"]
                if ck not in ctx_cache:
                    ctx_cache[ck] = asset_context(conn, ip)
                    ident = ip or f"(無 IP) wug:{dev['id']}"          # 沒有 IP 的也要列進對不到的清單，不默默消失
                    if relay_import.record_match(conn, SOURCE, ident, dev["name"] or dev["host_name"], batch_id, now_text) != "matched":
                        stats["unmatched"] += 1
                c = ctx_cache[ck]
                tier, why = classify(c, state, cfg)
                if mon == MONREAD_MONITOR and tier == "red":
                    tier, why = "yellow", f"{why}；監控明細讀取失敗（收集問題），最高只到摘要"
                back = minutes_from_status(text)
                start = snap_at - timedelta(minutes=back) if back is not None else snap_at
                conn.execute(
                    "INSERT INTO alert_event (source, wug_device_id, monitor, device_name, wug_host, ip, machine_key, asset_serial, match, hostname, api_id, "
                    "system_name, sys_class, environment, owner, department, wug_state, status_text, tier, tier_reason, started_at, last_seen, duration_min, "
                    "market, first_batch, last_batch, created_at, policy) VALUES ('whatsup',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (dev["id"], mon, dev["name"], dev["host_name"], ip, c["machine_key"], c["asset_serial"], c["match"], c["hostname"], c["api_id"],
                     c["system_name"], c["sys_class"], c["environment"], c["owner"], c["department"], state, text, tier, why, _s(start), at,
                     _dur(_s(start), snap_at), market_label(conn, start), batch_id, batch_id, now_text, pols.get(mon)))
                opened += 1
        if snap["complete"]:
            to_close = [r for key, r in open_rows.items() if key not in present and key[0] not in keep_devices]
            to_close = _suspect_hold(conn, snap, snap_at, at, to_close, len(open_rows))
            for r in to_close:
                conn.execute("UPDATE alert_event SET ended_at = ?, last_seen = ?, duration_min = ?, last_batch = ? WHERE id = ?",
                             (at, at, _dur(r["started_at"], snap_at), batch_id, r["id"]))
                closed += 1
    # 升級：🟡 持續超過門檻 → 🔴（只升不降）
    lim = snap_at - timedelta(minutes=int(cfg["escalate_min"]))
    for r in conn.execute("SELECT id, tier_reason FROM alert_event WHERE ended_at IS NULL AND tier = 'yellow' AND monitor != ? AND started_at <= ?",
                          (MONREAD_MONITOR, _s(lim))).fetchall():
        conn.execute("UPDATE alert_event SET tier = 'red', tier_reason = ? WHERE id = ?",
                     (f"{r['tier_reason'] or ''}；持續超過 {cfg['escalate_min']} 分升級"[:300], r["id"]))
    conn.execute("INSERT OR IGNORE INTO alert_scan (batch_id, snap_at, kind, complete, pages_read, devices_seen, problems, stopped_reason, group_json, "
                 "opened, updated, closed, received_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (batch_id, at, snap["kind"], 1 if snap["complete"] else 0, snap["pages_read"], snap["devices_seen"], len(snap["devices"]),
                  snap["stopped_reason"], gj, opened, updated, closed, now_text))
    stats["points"] = opened + updated + closed
    stats["host_days"] = closed
    return stats


def _is_suspect(n_close: int, n_open: int) -> bool:
    return n_close > SUSPECT_MIN_EVENTS and n_close > n_open * 0.5


def _suspect_hold(conn, snap: dict, snap_at: datetime, at: str, to_close: list, n_open: int) -> list:
    """疑似收集異常的擱置（GPT 複核新 1、新 4）。回這次真的要結案的列。

    * 一次完整掃描要結案的 >5 筆且 >開著的一半 → 整批擱置，記下「最初那一批」與時間，推一則 🟡（30 分鐘冷卻）。
    * 擱置中：最初那一批裡又出現的移出（不結案、也不再等），其餘照等；滿 5 分鐘仍消失的才結案。
      不會因為中途有幾筆重現、剩下的不到 5 筆就提早結案。
    * 擱置之外新消失的另外判斷：零星幾筆照常結案；本身又是大量 → 沒有擱置就開新的一批（從這次計時），
      已有擱置就這一輪先不結案、等目前這批有結果再判斷（不重設計時，也不讓它們搭便車提早結案）。
    """
    try:
        hold = json.loads(_get(conn, K_SUSPECT, "") or "{}")
    except ValueError:
        hold = {}
    if not isinstance(hold, dict):
        hold = {}
    held = {int(x) for x in hold.get("ids") or [] if str(x).isdigit()}
    hold_at = _p(hold.get("at"))
    ids = {r["id"]: r for r in to_close}
    held &= set(ids)                                        # 最初那一批裡又出現（這次沒要結案）的移出
    out = []
    if held and hold_at is not None and snap_at - hold_at >= timedelta(minutes=SUSPECT_HOLD_MIN):
        out += [ids[i] for i in sorted(held)]               # 滿 5 分鐘仍消失 → 真的恢復
        held = set()
        hold_at = None
    done = {r["id"] for r in out}
    extra = [r for i, r in ids.items() if i not in held and i not in done]
    if extra and _is_suspect(len(extra), n_open):
        if held:
            extra = []                                     # 擱置中又冒出另一大批：這一輪先都不結案，等目前這批有結果再判斷（不重設計時）
        else:
            held = {r["id"] for r in extra}                # 新的一批：從這次開始計時
            hold_at = snap_at
            extra = []
    out += extra                                           # 零星幾筆（不是大量）照常結案
    if held:
        _set(conn, K_SUSPECT, json.dumps({"ids": sorted(held), "at": _s(hold_at or snap_at)}))
        snap["stopped_reason"] = (f"疑似收集異常：擱置 {len(held)}／{n_open} 筆不結案（滿 {SUSPECT_HOLD_MIN} 分鐘仍消失才結案）；" + snap["stopped_reason"]).strip("；")[:300]
        last_note = _p(_get(conn, K_SUSPECT_NOTE, ""))
        if last_note is None or snap_at - last_note >= timedelta(minutes=SUSPECT_NOTE_COOLDOWN_MIN):
            _set(conn, K_SUSPECT_NOTE, at)
            _send(conn, _now(), "suspect", "yellow",
                  f"🟡 疑似收集異常：一次完整掃描要把 {len(held)} 筆告警（共開著 {n_open} 筆）當成恢復，先不結案",
                  ["可能是 WhatsUp 回應不完整或收集端讀錯；請到 WhatsUp 看這幾台是否真的恢復。",
                   f"{SUSPECT_HOLD_MIN} 分鐘後的完整掃描若仍消失，系統才會結案並推恢復（30 分鐘內不重複提醒）。"], len(held), color="Warning")
    else:
        _set(conn, K_SUSPECT, "")
    return out


# ── 通知 ───────────────────────────────────────────────────────────────
def _stage(r, s: str) -> bool:
    return f",{s}," in (r["notified"] or ",")


def _mark(conn, ids: list[int], s: str) -> None:
    for i in ids:
        conn.execute("UPDATE alert_event SET notified = CASE WHEN notified LIKE ? THEN notified ELSE COALESCE(NULLIF(notified,''), ',') || ? END "
                     "WHERE id = ?", (f"%,{s},%", f"{s},", i))


def _who(r) -> str:
    return r["hostname"] or r["device_name"] or r["wug_host"] or r["ip"] or r["wug_device_id"]


def _dur_txt(m: int | None) -> str:
    m = int(m or 0)
    return f"{m} 分" if m < 120 else f"{m // 60} 小時 {m % 60} 分"


def conclusion(r, now: datetime | None = None, kind: str = "open") -> str:
    """訊息第一行（被叫醒時一眼就懂）：哪台、什麼、多久、系統、負責人、開盤／非開盤。"""
    sys_ = f"{r['api_id']} {r['system_name'] or ''}".strip() if r["api_id"] else "未登記業務系統"
    owner = r["owner"] or "負責人未登錄"
    what = r["monitor"] if r["monitor"] not in (DEVICE_MONITOR,) else "設備"
    if kind == "brief":
        return (f"⚡ 短暫事故 {_who(r)}（{r['ip'] or '無 IP'}）{what} {r['wug_state'] or 'Down'} {_dur_txt(r['duration_min'])}，已恢復"
                f"（{r['started_at'][11:16]}～{(r['ended_at'] or '')[11:16]}）｜{sys_}｜{owner}｜{r['market'] or ''}")
    if kind == "recover":
        return f"✅ 已恢復 {_who(r)}（{r['ip'] or '無 IP'}）{what}，共 {_dur_txt(r['duration_min'])}｜{sys_}｜{owner}｜{r['market'] or ''}"
    head = "🔴 升級" if kind == "escalate" else ("🔴" if r["tier"] == "red" else "🟡")
    return f"{head} {_who(r)}（{r['ip'] or '無 IP'}）{what} {r['wug_state'] or 'Down'}，已 {_dur_txt(r['duration_min'])}｜{sys_}｜{owner}｜{r['market'] or ''}｜未確認"


def detail_lines(r) -> list[str]:
    return [f"WhatsUp：{r['device_name'] or '—'}（{r['wug_host'] or '—'}）・{r['status_text'] or r['wug_state'] or '—'}",
            f"開始 {r['started_at']}・分級 {TIER_LABEL.get(r['tier'], r['tier'])}（{r['tier_reason'] or ''}）・環境 {r['environment'] or '未填'}・分類 {r['sys_class'] or '未知'}",
            f"資產：{'已對應 ' + (r['machine_key'] or '') if r['match'] == 'matched' else '對不到（' + (r['match'] or '') + '），已列入對不到的告警'}・事件 #{r['id']}"]


def build_card(title: str, lines: list[str], extra: int = 0, color: str = "Attention") -> dict:
    import teams_notify as tn
    body = [{"type": "TextBlock", "text": tn.md_escape(title)[:300], "weight": "Bolder", "size": "Medium", "color": color, "wrap": True}]
    body += [{"type": "TextBlock", "text": tn.md_escape(x)[:400], "wrap": True, "spacing": "None"} for x in lines]
    if extra:
        body.append({"type": "TextBlock", "text": tn.md_escape(f"另有 {extra} 項，請到 4.1 事件應變 → 告警事件看全部"), "wrap": True})
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None,
            "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.2", "body": body}}]}


def _send(conn, now: datetime, kind: str, tier: str, title: str, lines: list[str], n_events: int, extra: int = 0, color: str = "Attention") -> str:
    """排進 Teams 佇列（沒啟用就只記錄）。回 queued／recorded。"""
    import teams_notify as tn
    ensure(conn)
    try:
        teams_on = tn.load_settings(conn)["enabled"]
    except tn.SettingsCorrupt:
        teams_on = False
    summary = "\n".join([title] + lines)[:4000]
    key = None
    status = "recorded"
    if teams_on:
        tn.ensure(conn)
        key = f"wug:{kind}:{now.strftime('%Y%m%d%H%M%S%f')}"
        sev = "critical" if tier == "red" else ("info" if kind in ("recover", "heartbeat_up") else "warning")
        tn._enqueue(conn, key, f"wug_{kind}", "wug_alert", sev, title, build_card(title, lines, extra, color), now)
        status = "queued"
    conn.execute("INSERT INTO alert_notice (at, kind, tier, events, status, delivery_key, title, summary) VALUES (?,?,?,?,?,?,?,?)",
                 (_s(now), kind, tier, n_events, status, key, title[:300], summary))
    conn.execute(f"DELETE FROM alert_notice WHERE id NOT IN (SELECT id FROM alert_notice ORDER BY id DESC LIMIT {NOTICE_KEEP})")
    return status


def _group_message(rows: list, kind: str, now: datetime) -> tuple[str, list[str], int]:
    """一則訊息：一筆＝結論＋細節；多筆＝標題「N 筆」＋每筆一行結論（每台各自一行，不合併成「疑似網路問題」）。"""
    if len(rows) == 1:
        return conclusion(rows[0], now, kind), detail_lines(rows[0]), 0
    # 一台一行（11-11 使用者：sec02 一台 14 個服務被拆成 14 行看不懂）；同一台只有一個監控項目時照舊一行結論
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(str(r["wug_device_id"]), []).append(r)
    n_dev = len(groups)
    head = {"open": "🔴 {n} 筆告警", "escalate": "🔴 {n} 筆告警升級（持續過久）", "recover": "✅ {n} 筆告警已恢復",
            "brief": "⚡ {n} 筆短暫事故（已恢復）"}[kind].format(n=len(rows)) + (f"（{n_dev} 台）" if n_dev != len(rows) else "")
    names = [_who(g[0]) for g in groups.values()]
    lines = [conclusion(g[0], now, kind) if len(g) == 1 else device_line(g, kind) for g in list(groups.values())[:MAX_LINES]]
    return head + "：" + "、".join(names[:5]) + (f"…等 {n_dev} 台" if n_dev > 5 else ""), lines, max(0, n_dev - MAX_LINES)


def digest_lines(rows: list, now: datetime, kind: str) -> list[str]:
    """一台一行：同一台（WhatsUp 設備）只有一個監控項目＝原本那行結論；多個＝合成一行（device_line）。"""
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(str(r["wug_device_id"]), []).append(r)
    return [conclusion(g[0], now, kind) if len(g) == 1 else device_line(g, kind) for g in groups.values()]


def device_line(g: list, kind: str) -> str:
    """同一台多個監控項目合成一行：哪台、幾個項目、列前 3 個名稱、最長多久、系統、負責人、開盤／非開盤。"""
    r = g[0]
    sys_ = f"{r['api_id']} {r['system_name'] or ''}".strip() if r["api_id"] else "未登記業務系統"
    owner = r["owner"] or "負責人未登錄"
    mons = [x["monitor"] if x["monitor"] != DEVICE_MONITOR else "設備" for x in g]
    shown = "、".join(mons[:3]) + (f" 等 {len(mons)} 項" if len(mons) > 3 else "")
    dur = _dur_txt(max(int(x["duration_min"] or 0) for x in g))
    icon, verb = {"open": ("🔴", "異常"), "escalate": ("🔴 升級", "異常"), "recover": ("✅", "已恢復"), "brief": ("⚡ 短暫事故", "已恢復")}[kind]
    return f"{icon} {_who(r)}（{r['ip'] or '無 IP'}）{len(g)} 個監控項目{verb}：{shown}，最長 {dur}｜{sys_}｜{owner}｜{r['market'] or ''}"


def notify(conn, now: datetime | None = None) -> dict:
    """排通知：🔴 發生／升級／恢復立即；🟡 每 digest_min 一則摘要。自己 commit。"""
    cfg = load_settings(conn)
    if not cfg["enabled"] or not _has(conn, "alert_event"):
        return {"status": "disabled"}
    now = now or _now()
    out = {"open": 0, "escalate": 0, "brief": 0, "recover": 0, "digest": 0, "quiet": 0}
    q = "SELECT * FROM alert_event WHERE tier != 'maint' "
    edge = now - timedelta(minutes=STALE_MIN)

    def old(t) -> bool:
        d = _p(t)
        return d is not None and d < edge

    # 每個事件只在「第一次被通知流程看到」時判斷一次新舊：那時已經太舊（啟用前就存在、或補匯積壓批次裡的）→ 標 quiet 不送，
    # quiet 的事件之後也不推升級／恢復／短暫事故／摘要（畫面照樣看得到）；夠新的標 seen，之後照原規則走（含 🟡 一小時後才進摘要）。
    first = [r for r in conn.execute(q + "AND (notified IS NULL OR (notified NOT LIKE '%,quiet,%' AND notified NOT LIKE '%,seen,%'))").fetchall()
             if not any(_stage(r, k) for k in ("open", "escalate", "digest", "brief", "recover", "digest_recover"))]
    quiet = [r["id"] for r in first if old(r["ended_at"] or r["last_seen"])]   # 用快照時間，不用往回推的開始時間（"Down at least 1 hour" 的新事件不能算舊）
    if quiet:
        _mark(conn, quiet, "quiet")
        out["quiet"] = len(quiet)
    _mark(conn, [r["id"] for r in first if r["id"] not in set(quiet)], "seen")
    # 不通知清單：符合的標 muted（之後也不推升級／恢復／摘要）；畫面照樣看得到
    mutes = list_mutes(conn)
    if mutes:
        muted = [r["id"] for r in conn.execute(q + "AND (notified IS NULL OR (notified NOT LIKE '%,quiet,%' AND notified NOT LIKE '%,muted,%'))").fetchall()
                 if not any(_stage(r, k) for k in ("open", "escalate", "digest", "brief")) and is_muted(r, mutes)]
        if muted:
            _mark(conn, muted, "muted")
            out["muted"] = len(muted)
    # 動作政策篩選：WhatsUp 明講沒掛（或掛的不是指定的）→ 標 nopolicy，之後也不推；畫面照樣看得到
    kws = policy_keywords(cfg.get("notify_policies"))
    if kws:
        nop = [r["id"] for r in conn.execute(q + "AND (notified IS NULL OR (notified NOT LIKE '%,quiet,%' AND notified NOT LIKE '%,muted,%' "
                                                  "AND notified NOT LIKE '%,nopolicy,%'))").fetchall()
               if not any(_stage(r, k) for k in ("open", "escalate", "digest", "brief")) and not policy_ok(r["policy"], kws)]
        if nop:
            _mark(conn, nop, "nopolicy")
            out["nopolicy"] = len(nop)
    q += "AND (notified IS NULL OR (notified NOT LIKE '%,quiet,%' AND notified NOT LIKE '%,muted,%' AND notified NOT LIKE '%,nopolicy,%')) "
    # 2.066.x（使用者 11-13）：通知照系統首頁的判斷——只有「進入需要注意」推一次發生、恢復推一次，其他一律不立即推。
    # 需要注意＝還開著、從開始算已斷 ≥ alert_attention_min 分、現在不在這台的例行時段內（alert_attention.judge_one，首頁同一支）。
    # 不分 🔴／🟡：系統判斷要注意就推。範圍照舊（quiet／muted／nopolicy 已在 q 排除，跟首頁 Q9 同一個政策範圍）。
    import alert_attention as aa
    th = aa.thresholds(conn)                         # 開盤（market＝開盤）用 alert_attention_open_min，其他用 alert_attention_min
    wmap = aa.active_map(conn, now.strftime("%Y-%m-%d"))
    # 審查 P2-4：新規則第一次跑的時間（cutover）以前就建立的事件不推「發生」（升級當下不洗版）；首頁照列；
    # 它們的恢復只在推過「發生」時才推（下面 rec 本來就只挑推過的）。
    cut = aa.cutover(conn, now)

    cut_dt = _p(cut)

    def post_cut(r) -> bool:
        """cutover 之後建立的、或 cutover 那一刻還沒斷滿門檻的（之後才進需要注意＝照一般規則推）→ True。
        cutover 那一刻已經斷滿門檻（升級前就該推、當時只在摘要裡）的 → False，不補推。"""
        if str(r["created_at"] or "") >= cut:
            return True
        st, end = _p(r["started_at"]), _p(r["ended_at"])
        if st is None or cut_dt is None or (end is not None and end <= cut_dt):
            return False                                  # cutover 以前就結束的舊事件：不推
        return int((cut_dt - st).total_seconds() // 60) < aa.th_for(r, th)
    new_red = [r for r in conn.execute(q + "AND ended_at IS NULL ORDER BY started_at, id").fetchall()
               if not _stage(r, "open") and not _stage(r, "escalate") and post_cut(r) and aa.judge_one(r, now, th, wmap)[0] == "attention"]
    if new_red:
        title, lines, extra = _group_message(new_red, "open", now)
        _send(conn, now, "open", "red", title, lines, len(new_red), extra)
        _mark(conn, [r["id"] for r in new_red], "open")
        out["open"] = len(new_red)
    # 已恢復、還沒處理過的：
    #  * 兩次排程之間就超過門檻又恢復（不在例行時段）＝真的斷過 → 立即推一則「短暫事故（超過門檻）已恢復」（審查 P2-5，不分紅黃）
    #  * 門檻內自動恢復／例行時段內恢復 → 不立即推：🔴 彙整開著寫進每日彙整，關著標 nopush（照設計不推，畫面看得到）；🟡 走下面的摘要
    #  * cutover 以前建立的 → 不推（同上）
    done = ("open", "escalate", "digest", "brief", "nopush")
    closed = [r for r in conn.execute(q + "AND ended_at IS NOT NULL ORDER BY started_at, id").fetchall() if not any(_stage(r, k) for k in done)]
    # 讀不到監控明細、設備又不是 Down 的（收集問題）恢復了也不推短暫事故：從沒推過、也不是設備故障，推了只是雜訊
    # 長期未恢復（斷超過 alert_longterm_hours）的恢復：推過「發生」的走下面的恢復；沒推過的不補推短暫事故
    long_min = th["long_h"] * 60
    over = [r for r in closed if post_cut(r) and not (r["monitor"] == MONREAD_MONITOR and (r["wug_state"] or "").strip().lower() != "down")
            and aa._dur(r, now) < long_min and aa.judge_one(r, now, th, wmap)[0] == "recovered"]
    if over:
        title, lines, extra = _group_message(over, "brief", now)
        _send(conn, now, "brief", "red", title, lines, len(over), extra, "Warning")
        _mark(conn, [r["id"] for r in over], "brief")
        out["brief"] = len(over)
    red_short = [r for r in closed if r not in over and r["tier"] == "red"]
    if red_short:
        import notice_digest as nd
        if nd.add(conn, "wug", digest_lines(red_short, now, "brief"), now):
            _mark(conn, [r["id"] for r in red_short], "brief_digest")
            _mark(conn, [r["id"] for r in red_short], "brief")
            out["brief_digest"] = len(red_short)
        else:
            _mark(conn, [r["id"] for r in red_short], "nopush")
    # 反覆斷線：每日彙整一台一行「今天斷了 N 次」（不推、不列需要注意）
    day0 = now.strftime("%Y-%m-%d 00:00:00")
    aa.flap_digest(conn, now, conn.execute(q + "AND started_at >= ?", (day0,)).fetchall())
    # 長期未恢復：每日彙整一行「長期未恢復 N 台」（不逐台推）
    aa.longterm_digest(conn, now, conn.execute(q + "AND ended_at IS NULL").fetchall(), th["long_h"])
    # 恢復：推過「發生」或「升級」的才推恢復；已進過 🟡 摘要、後來升成 🔴 但還沒來得及推升級就恢復的也推恢復（不讓它無聲消失）
    rec = [r for r in conn.execute(q + "AND ended_at IS NOT NULL ORDER BY ended_at, id").fetchall()
           if (_stage(r, "open") or _stage(r, "escalate")) and not _stage(r, "recover")]
    if rec:
        title, lines, extra = _group_message(rec, "recover", now)
        _send(conn, now, "recover", "info", title, lines, len(rec), extra, "Good")
        _mark(conn, [r["id"] for r in rec], "recover")
        out["recover"] = len(rec)
    # 🟡 摘要
    last = _p(_get(conn, K_LAST_DIGEST, ""))
    if last is None:
        _set(conn, K_LAST_DIGEST, _s(now))               # 第一次啟用：從現在開始算一小時
    elif now - last >= timedelta(minutes=int(cfg["digest_min"])):
        y_open = [r for r in conn.execute(q + "AND tier = 'yellow' ORDER BY started_at, id").fetchall()
                  if not _stage(r, "digest") and not _stage(r, "open") and not _stage(r, "brief")]
        y_rec = [r for r in conn.execute(q + "AND tier = 'yellow' AND ended_at IS NOT NULL ORDER BY ended_at, id").fetchall()
                 if _stage(r, "digest") and not _stage(r, "digest_recover")]
        # 2.061.12：一台一行（同一台多個監控項目合成一行），彙整的「N 項」才是台次、不是監控項目數（11-12 一天 1024 項）
        lines = digest_lines([r for r in y_open if not r["ended_at"]], now, "open") \
            + digest_lines([r for r in y_open if r["ended_at"]] + list(y_rec), now, "recover")
        if lines:
            n_now = sum(1 for r in y_open if not r["ended_at"])
            title = f"🟡 告警摘要（{_s(last)[11:16]}～{_s(now)[11:16]}）：發生 {len(y_open)} 筆（仍未恢復 {n_now}）、恢復 {len(y_rec) + len(y_open) - n_now} 筆"
            import notice_digest as nd
            if nd.add(conn, "wug", lines, now):
                # 2.060.19（S6）：🟡 不再每小時發一則，存進每天 08:00 的彙整（notice_digest）；這裡只記一筆「已併入彙整」
                conn.execute("INSERT INTO alert_notice (at, kind, tier, events, status, delivery_key, title, summary) VALUES (?,?,?,?,?,?,?,?)",
                             (_s(now), "digest", "yellow", len(lines), "digest", None, title[:300], "\n".join([title] + lines)[:4000]))
            # 2.066.x：彙整關著也不再每小時推（只有需要注意才推）；畫面照樣看得到
            _mark(conn, [r["id"] for r in y_open], "digest")
            _mark(conn, [r["id"] for r in y_open if r["ended_at"]] + [r["id"] for r in y_rec], "digest_recover")
            out["digest"] = len(lines)
        _set(conn, K_LAST_DIGEST, _s(now))
    conn.commit()
    return out


def heartbeat(conn, now: datetime | None = None) -> dict:
    """heartbeat_min 分鐘沒收到任何告警批次 → 一則 🔴「監控中斷」；之後收到 → 一則「監控恢復」。
    從沒收到過：啟用後 NEVER_MIN（15）分鐘內不判斷（中繼還在接）；超過還沒收到第一批 → 一則 🔴「監控未啟動」（帳密錯、排程沒建、上傳接錯…）。
    自己 commit。"""
    cfg = load_settings(conn)
    if not cfg["enabled"]:
        return {"status": "disabled"}
    now = now or _now()
    last = _p(_get(conn, K_LAST_RECV, ""))
    down = _get(conn, K_HB_DOWN, "") == "1"
    if last is None:
        since = _p(_get(conn, K_ENABLED_AT, ""))
        if since is not None and now - since > timedelta(minutes=NEVER_MIN) and not down:
            ensure(conn)
            _send(conn, now, "heartbeat_never", "red", f"🔴 監控未啟動：告警功能 {_s(since)} 啟用後已 {int((now - since).total_seconds() // 60)} 分鐘，還沒收到任何 WhatsUp 告警資料",
                  ["WhatsUp 的告警目前完全進不來，不代表沒事。",
                   "請檢查中繼 PC：wug_account.txt 帳密、排程 webit3_wug_alert 是否已登記、上傳金鑰是否已裝在系統主機、中繼匯入的共用資料夾是否設定。"], 0)
            _set(conn, K_HB_DOWN, "1")
            conn.commit()
            return {"status": "never_sent"}
        return {"status": "never_down" if down else "never"}
    gap = now - last
    lim = timedelta(minutes=int(cfg["heartbeat_min"]))
    if gap > lim and not down:
        ensure(conn)
        title = f"🔴 監控中斷：已 {int(gap.total_seconds() // 60)} 分鐘沒收到 WhatsUp 告警資料（最後收到 {_s(last)}）"
        _send(conn, now, "heartbeat_down", "red", title,
              ["這段時間 WhatsUp 的告警不會進來，不代表沒事。", "請檢查中繼 PC（是否開機、排程 webit3_wug_alert 是否在跑、上傳是否成功）。"], 0)
        _set(conn, K_HB_DOWN, "1")
        conn.commit()
        return {"status": "down_sent"}
    if gap <= lim and down:
        ensure(conn)
        _send(conn, now, "heartbeat_up", "info", f"✅ 監控恢復：{_s(last)} 起又收到 WhatsUp 告警資料", ["中斷期間發生又恢復的告警可能漏掉，請到 WhatsUp 對照。"], 0, color="Good")
        _set(conn, K_HB_DOWN, "")
        conn.commit()
        return {"status": "up_sent"}
    return {"status": "down" if down else "ok"}


def purge(conn, now: datetime | None = None) -> int:
    """每天一次：清掉已結案且超過保存期的事件（開著的一律不清）。自己 commit。"""
    if not _has(conn, "alert_event"):
        return 0
    now = now or _now()
    today = now.strftime("%Y-%m-%d")
    if _get(conn, K_LAST_PURGE, "") == today:
        return 0
    days = int(load_settings(conn)["retention_days"])
    n = conn.execute("DELETE FROM alert_event WHERE ended_at IS NOT NULL AND ended_at < ?", (_s(now - timedelta(days=days)),)).rowcount
    conn.execute("DELETE FROM alert_scan WHERE snap_at < ?", (_s(now - timedelta(days=min(days, 90))),))
    _set(conn, K_LAST_PURGE, today)
    conn.commit()
    return n


# ── 排程入口（每分鐘；C 類例外，不受盤中靜默窗限制，理由見檔頭）────────────
def import_pending(conn) -> dict:
    """只匯 wugalert_ 開頭的告警批次（一般中繼匯入的 15 分鐘間隔與盤中靜默窗不套用在這裡）。"""
    import relay_import as ri
    ri.ensure(conn)
    d = ri.get_config(conn)["dir"]
    if not d:
        return {"status": "no_dir"}
    folders, problem = ri.list_batch_folders(d)
    if problem:
        return {"status": "no_dir", "message": problem}
    done = {r[0] for r in conn.execute("SELECT batch_id FROM relay_import_batches WHERE source = ? AND status = 'imported'", (SOURCE,))}
    todo = [f for f in folders if f.name.startswith(BATCH_PREFIX) and f.name not in done]
    todo = [f for f in todo if ri.is_pending(conn, f)][:ri.MAX_BATCHES_PER_RUN]
    if not todo:
        return {"status": "idle"}
    if not ri._run_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        now_text = ri._now_text(None)
        res = [ri.import_batch(conn, f, "alert", None, now_text, ri.get_config(conn)["max_file_mb"] * 1024 * 1024) for f in todo]
    finally:
        ri._run_lock.release()
    return {"status": "ok", "imported": sum(1 for r in res if r["status"] == "imported"), "rejected": sum(1 for r in res if r["status"] != "imported")}


def tick(conn, now: datetime | None = None) -> dict:
    """排程器每分鐘叫一次：匯入新到的告警批次 → 心跳 → 排通知 → 每天清一次過期。未啟用就什麼都不做。"""
    try:
        cfg = load_settings(conn)
    except ValueError:
        return {"status": "settings_corrupt"}
    if not cfg["enabled"]:
        return {"status": "disabled"}
    if not _get(conn, K_ENABLED_AT, ""):
        _set(conn, K_ENABLED_AT, _s(now or _now()))     # 升級前就已啟用、沒存過啟用時間：從現在起算「監控未啟動」
        conn.commit()
    out = {"import": import_pending(conn)}
    out["heartbeat"] = heartbeat(conn, now)
    out["notify"] = notify(conn, now)
    out["purged"] = purge(conn, now)
    return out


# ── 讀取（畫面用；全部讀同一張表，加總＝下鑽）────────────────────────────
COLS = ("id", "wug_device_id", "monitor", "device_name", "wug_host", "ip", "machine_key", "asset_serial", "match", "hostname", "api_id",
        "system_name", "sys_class", "environment", "owner", "department", "wug_state", "status_text", "tier", "tier_reason", "started_at",
        "ended_at", "last_seen", "duration_min", "market", "confirmed", "verdict", "notified", "policy")


def _row(r) -> dict:
    d = {k: r[k] for k in COLS}
    d["__no"] = r["id"]
    d["open"] = r["ended_at"] is None
    d["state_label"] = "未恢復" if r["ended_at"] is None else "已恢復"
    d["tier_label"] = TIER_LABEL.get(r["tier"], r["tier"])
    d["who"] = _who(r)
    return d


def events(conn, *, state: str = "", tier: str = "", month: str = "", machine_key: str = "", ip: str = "", q: str = "", wug_device_id: str = "",
           no_machine: bool = False, limit: int = 5000) -> list[dict]:
    """事件清單（篩選可組合）。month＝YYYY-MM：該月「開始」的事件（月報口徑同一條）。"""
    if not _has(conn, "alert_event"):
        return []
    w, a = [], []
    if state == "open":
        w.append("ended_at IS NULL")
    elif state == "closed":
        w.append("ended_at IS NOT NULL")
    if tier in TIERS:
        w.append("tier = ?")
        a.append(tier)
    if month:
        if not re.match(r"^\d{4}-\d{2}$", month):
            raise ValueError("month 要是 YYYY-MM")
        w.append("substr(started_at, 1, 7) = ?")
        a.append(month)
    if machine_key:
        w.append("machine_key = ?")
        a.append(machine_key)
    if ip:
        w.append("ip = ?")
        a.append(ip.strip())
    if wug_device_id:
        w.append("wug_device_id = ?")
        a.append(wug_device_id.strip())
    if no_machine:
        w.append("machine_key IS NULL")              # 月報「對不到資產」那一列的下鑽：同 IP 但已對到資產的不算進來
    if q:
        w.append("(COALESCE(device_name,'') || ' ' || COALESCE(hostname,'') || ' ' || COALESCE(ip,'') || ' ' || monitor || ' ' || "
                 "COALESCE(api_id,'') || ' ' || COALESCE(system_name,'') || ' ' || COALESCE(owner,'')) LIKE ?")
        a.append(f"%{q.strip()}%")
    sql = "SELECT * FROM alert_event" + (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY started_at DESC, id DESC LIMIT ?"
    return [_row(r) for r in conn.execute(sql, (*a, max(1, min(int(limit), 20000))))]


def for_machine(conn, machine_key: str | None = None, ips: list[str] | None = None, recent: int = 10) -> dict:
    """夜間速查／主機履歷：這台目前的告警＋最近幾次（machine_key 優先；對不到資產的告警用 IP 補）。"""
    if not _has(conn, "alert_event"):
        return {"current": [], "recent": [], "known": False}
    w, a = [], []
    if machine_key:
        w.append("machine_key = ?")
        a.append(machine_key)
    ips = [x for x in (ips or []) if x]
    if ips:
        # 有 machine key 時，IP 只用來補「對不到資產」的告警：共用／重用的 IP（VIP 等）不可以把別台已對到的告警拉進這台
        w.append(("COALESCE(machine_key, '') = '' AND " if machine_key else "") + "ip IN (%s)" % ",".join("?" for _ in ips))
        a.extend(ips)
    if not w:
        return {"current": [], "recent": [], "known": True}
    rows = [_row(r) for r in conn.execute("SELECT * FROM alert_event WHERE (" + " OR ".join(w) + ") ORDER BY started_at DESC, id DESC", a)]
    return {"current": [r for r in rows if r["open"]], "recent": [r for r in rows if not r["open"]][:recent], "total": len(rows), "known": True}


#: 月報彙總的「同一台」：對得到資產用 machine_key；對不到用 IP；連 IP 都沒有用 WhatsUp 設備 ID（下鑽帶同一個條件）
_MKEY_SQL = "COALESCE(machine_key, CASE WHEN COALESCE(ip, '') != '' THEN 'ip:' || ip ELSE 'wug:' || wug_device_id END)"


def month_summary(conn, month: str) -> dict:
    """月報：本月（依開始時間）每台幾次、總時長。用 SQL 聚合整張表（不經清單筆數上限），每台的 events 加總＝該月事件筆數；
    每列帶下鑽條件（machine_key／ip／wug_device_id 擇一），點下去＝events(month=…, 同一條件)（verify_numbers 核）。"""
    if not re.match(r"^\d{4}-\d{2}$", month or ""):
        raise ValueError("month 要是 YYYY-MM")
    empty = {"month": month, "machines": [], "events": 0, "minutes": 0, "open": 0, "unmatched": 0}
    if not _has(conn, "alert_event"):
        return empty
    rows = conn.execute(
        f"SELECT {_MKEY_SQL} AS k, MAX(machine_key) AS machine_key, MAX(ip) AS ip, MAX(wug_device_id) AS wug_device_id, "
        "MAX(COALESCE(NULLIF(hostname,''), NULLIF(device_name,''), NULLIF(wug_host,''), NULLIF(ip,''), wug_device_id)) AS who, "
        "MAX(api_id) AS api_id, MAX(system_name) AS system_name, COUNT(*) AS events, SUM(tier = 'red') AS red, SUM(ended_at IS NULL) AS open, "
        "SUM(COALESCE(duration_min, 0)) AS minutes, GROUP_CONCAT(DISTINCT monitor) AS monitors "
        "FROM alert_event WHERE substr(started_at, 1, 7) = ? GROUP BY k ORDER BY events DESC, minutes DESC, who", (month,)).fetchall()
    out = []
    for i, r in enumerate(rows, 1):
        g = dict(r)
        g["key"] = g.pop("k")
        g["red"], g["open"], g["minutes"] = int(g["red"] or 0), int(g["open"] or 0), int(g["minutes"] or 0)
        g["monitors"] = "、".join(sorted(set((g["monitors"] or "").split(","))))[:300]
        if g["machine_key"]:
            g["wug_device_id"] = None
        elif g["key"].startswith("ip:"):
            g["wug_device_id"] = None
        else:
            g["ip"] = None
        g["__no"] = i
        out.append(g)
    tot = conn.execute("SELECT COUNT(*), SUM(ended_at IS NULL), SUM(machine_key IS NULL), SUM(COALESCE(duration_min, 0)) FROM alert_event "
                       "WHERE substr(started_at, 1, 7) = ?", (month,)).fetchone()
    return {"month": month, "machines": out, "events": int(tot[0] or 0), "minutes": int(tot[3] or 0), "open": int(tot[1] or 0), "unmatched": int(tot[2] or 0)}


def drill_filter(g: dict) -> dict:
    """月報一列 → 事件清單的條件（同一個鍵）。"""
    if g.get("machine_key"):
        return {"machine_key": g["machine_key"]}
    if g.get("ip"):
        return {"ip": g["ip"], "no_machine": True}
    return {"wug_device_id": g.get("wug_device_id") or "", "no_machine": True}


def status(conn, now: datetime | None = None) -> dict:
    """畫面上方：啟用、最後收到、心跳、最近一次快照、開著幾筆、對不到幾筆。"""
    now = now or _now()
    try:
        cfg = load_settings(conn)
        corrupt = None
    except ValueError as exc:
        cfg, corrupt = dict(DEFAULTS), str(exc)
    last = _get(conn, K_LAST_RECV, "")
    lp = _p(last)
    hb = "never" if lp is None else ("down" if now - lp > timedelta(minutes=int(cfg["heartbeat_min"])) else "ok")
    scan = None
    if _has(conn, "alert_scan"):
        r = conn.execute("SELECT * FROM alert_scan WHERE kind = 'scan' ORDER BY snap_at DESC, id DESC LIMIT 1").fetchone()
        if r is not None:
            scan = {k: r[k] for k in ("batch_id", "snap_at", "complete", "pages_read", "devices_seen", "problems", "stopped_reason", "group_json")}
    n_open = conn.execute("SELECT COUNT(*) FROM alert_event WHERE ended_at IS NULL AND tier != 'maint'").fetchone()[0] if _has(conn, "alert_event") else 0
    n_maint = conn.execute("SELECT COUNT(*) FROM alert_event WHERE ended_at IS NULL AND tier = 'maint'").fetchone()[0] if _has(conn, "alert_event") else 0
    unmatched = conn.execute("SELECT COUNT(*) FROM relay_import_unmatched WHERE source = ?", (SOURCE,)).fetchone()[0] if _has(conn, "relay_import_unmatched") else 0
    return {"settings": cfg, "settings_problem": corrupt, "last_received": last or None, "heartbeat": hb, "last_scan": scan,
            "open": n_open, "maintenance": n_maint, "unmatched": unmatched, "market": market_label(conn, now)}


def notices(conn, limit: int = 300) -> list[dict]:
    if not _has(conn, "alert_notice"):
        return []
    has_d = _has(conn, "teams_notify_delivery")
    q = ("SELECT n.*, d.status AS delivery_status FROM alert_notice n LEFT JOIN teams_notify_delivery d ON d.event_key = n.delivery_key "
         if has_d else "SELECT n.*, NULL AS delivery_status FROM alert_notice n ")
    return [{**dict(r), "__no": r["id"]} for r in conn.execute(q + "ORDER BY n.id DESC LIMIT ?", (max(1, min(int(limit), 2000)),))]


def set_verdict(conn, event_id: int, verdict: str, who: str) -> dict:
    """人工回報這則告警是真的（true）還是假的（false），空字串＝清掉。**不 commit**（路由與稽核同一交易）。"""
    if verdict not in ("true", "false", ""):
        raise ValueError("verdict 只能是 true、false 或空白")
    if not _has(conn, "alert_event"):
        raise LookupError("沒有這筆告警事件")
    cur = conn.execute("UPDATE alert_event SET verdict = ?, verdict_by = ?, verdict_at = ? WHERE id = ?",
                       (verdict or None, who if verdict else None, _s(_now()) if verdict else None, int(event_id)))
    if cur.rowcount != 1:
        raise LookupError("沒有這筆告警事件")
    return _row(conn.execute("SELECT * FROM alert_event WHERE id = ?", (int(event_id),)).fetchone())


# ── 不通知清單（2.060.18；使用者 11-11：「例外名單包含時段，可以自己加，例：週六日 SEC02 會 reboot」）──
#: 比對用事件的發生時間（started_at，台北）：落在規則的星期＋時段內就不推（照樣記錄）。
WEEKDAYS = "一二三四五六日"


def _hhmm(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    try:
        h, m = v.split(":")
        h, m = int(h), int(m)
    except ValueError:
        raise ValueError("時間格式要 HH:MM，例如 02:00")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("時間要在 00:00～23:59")
    return f"{h:02d}:{m:02d}"


def _days(v) -> str:
    items = [str(x).strip() for x in (v if isinstance(v, (list, tuple)) else str(v or "").split(",")) if str(x).strip()]
    if any(x not in "1234567" or len(x) != 1 for x in items):
        raise ValueError("星期只能是 1～7（1＝週一，7＝週日）")
    return ",".join(sorted(set(items)))


def mute_text(m: dict) -> str:
    days = "每天" if not m["days"] else "週" + "、".join(WEEKDAYS[int(d) - 1] for d in m["days"].split(","))
    when = "全天" if not (m["time_from"] or m["time_to"]) else f"{m['time_from'] or '00:00'}～{m['time_to'] or '23:59'}"
    return f"{days} {when}"


def list_mutes(conn) -> list[dict]:
    if not _has(conn, "alert_mute"):
        return []
    out = [dict(r) for r in conn.execute("SELECT * FROM alert_mute ORDER BY id")]
    for m in out:
        m["when_text"] = mute_text(m)
    return out


def _in_window(m: dict, at: datetime) -> bool:
    """事件發生時間是否落在規則的星期＋時段內。跨午夜（例 22:00～06:00）的凌晨那段算「前一天」的星期。"""
    days = set(m["days"].split(",")) if m["days"] else None

    def day_ok(d: datetime) -> bool:
        return days is None or str(d.isoweekday()) in days
    if not (m["time_from"] or m["time_to"]):
        return day_ok(at)
    t, a, b = at.strftime("%H:%M"), m["time_from"], m["time_to"]
    if a <= b:
        return day_ok(at) and a <= t <= b
    if t >= a:
        return day_ok(at)
    return t < b and day_ok(at - timedelta(days=1))


def is_muted(r, mutes: list[dict]) -> bool:
    at = _p(r["started_at"])
    if at is None:
        return False
    for m in mutes:
        if str(m["wug_device_id"]) != str(r["wug_device_id"]) or m["monitor"] not in ("*", r["monitor"]):
            continue
        if _in_window(m, at):
            return True
    return False


def add_mute(conn, event_id: int, whole_device: bool, days, time_from: str, time_to: str, note: str, user: str) -> dict:
    """從一筆事件建立不通知規則（這個監控項目或這台全部＋星期＋時段）。不自己 commit。"""
    d, a, b = _days(days), _hhmm(time_from), _hhmm(time_to)
    if bool(a) != bool(b):
        raise ValueError("時段起訖要一起填（都不填＝全天）")
    ensure(conn)
    r = conn.execute("SELECT * FROM alert_event WHERE id=?", (event_id,)).fetchone()
    if r is None:
        raise LookupError("找不到這筆告警事件")
    mon = "*" if whole_device else r["monitor"]
    label = f"{_who(r)}（{r['ip'] or '無 IP'}）"
    cur = conn.execute("INSERT INTO alert_mute (wug_device_id, monitor, days, time_from, time_to, device_label, note, created_by, created_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?)", (str(r["wug_device_id"]), mon, d, a, b, label, (note or "")[:200], user, _s(_now())))
    m = dict(conn.execute("SELECT * FROM alert_mute WHERE id=?", (cur.lastrowid,)).fetchone())
    m["when_text"] = mute_text(m)
    return m


def del_mute(conn, mute_id: int) -> dict:
    ensure(conn)
    r = conn.execute("SELECT * FROM alert_mute WHERE id=?", (mute_id,)).fetchone()
    if r is None:
        raise LookupError("找不到這條不通知規則")
    conn.execute("DELETE FROM alert_mute WHERE id=?", (mute_id,))
    m = dict(r)
    m["when_text"] = mute_text(m)
    return m
