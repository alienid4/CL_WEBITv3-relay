"""1.5 每日檢查（xServer 每日檢查紀錄自動化，第一期試辦，2.060.x）。

設計：docs/設計_每日檢查自動化_2026-11-11.md（含 GPT 審查 12 點判定與每個指標的規則）。

## 怎麼運作
06:30（設定）排程用**已經匯入**的資料產生當天快照：每一列一個系統判定（正常／異常／無資料／前一日參考／人工）＋數值＋資料時間＋來源批次；
之後畫面只讀快照（值班確認的就是那一刻的內容）。可「重新產生」（新的一次，舊的保留）。值班逐列確認（正常／異常＋說明），全部確認完才能送出；
送出後唯讀，只能「更正」（留事件）。07:45 還沒送出提醒、08:15 升級（設定），走現有 teams_notify 佇列。

## 守的規則
- 舊資料、不完整、範圍未設定、對不到 → 一律「無資料」，絕不「正常」（GPT 3、4）。
- 前一日效能（CPU／記憶體／磁碟／輪詢）＝「前一日參考」，永遠不自動正常、一定要人勾（GPT 2）。
- 批次確認只限「資料新鮮的正常列」，事件記範圍、筆數、來源時間（GPT 8）。
- 單台磁碟例外門檻：要原因、到期日、上限 95%；過期自動失效並在快照記警示（GPT 7）。
- 不連任何主機、不在公司主機加任何東西：只讀 alert_scan／alert_event（WhatsUp 告警快照）、capacity_whatsup_perf、relay_disk_util、relay_import_batches、
  ad_time.latest（AD 對時中繼，2.060.9）。
- 真實 IP／主機名／網址只在 app_settings（daily_check_settings），項目結構在 daily_check_items.json。
- 讀取函式不建表；寫入路徑才 ensure。

## 2.062.0（S13）CPU／記憶體／磁碟／輪詢改讀 WhatsUp「上次輪循」群組快照（設計 docs/設計_每日檢查改抓上次輪循_2026-11-12.md §10）
- 設定 daily_check_perf_source＝lastpoll（預設）讀 wug_lastpoll 的「最新且完整」一批；＝w3 走原本的前一日規則（舊程式保留一版）。
- 範圍＝每列設定的 WhatsUp 群組（完整路徑＋可選 ID）；收集端回報對不上 → 該列無資料「群組對不上，停判」。
- 新鮮度兩層：最新完整批次 ≤ lp_fresh_min 分鐘；單筆「有效年齡」＝age_s＋（產生時間－批次收集時間）> poll_min 分鐘＝舊值
  （不算正常、不算異常，數進無資料）；輪詢列也用有效年齡。
- 等號照正本「需低於」：CPU ≥ cpu%、記憶體 ≥ mem% 就異常；硬碟 ≥ disk_alert 異常；輪詢 > poll_min。
- deviceId 去重；沒有該指標的設備另外數「無資料」；對不到資產照算、明細標「資產對不到」（IP→資產用 relay_import.match_asset）。
- 成員 0 台或比已核准快照增減超過 wug_member_jump_pct → 範圍未確認（無資料），管理員核准後恢復。
- 每列的台數放 stats（scope＝唯一 deviceId 數＝明細筆數；有資料＋無資料＝範圍；異常 ≤ 有資料），verify_numbers 釘住。

## 2.064.0 VMware 段（正本 2-1～2-4）「系統預填、人確認」（設計 docs/設計_每日檢查全表範圍_含VMware_2026-11-12.md §3、§5）
- 設定 vm_vcenters（每座 vCenter：名稱、vcid＝vROps 的 VMEntityVCID、收集器 ID 可選、應有 ESXi 清單）有填 → 每座一列 VC-n，kind=ref、
  永遠要人勾；資料從 aria_dailycheck（跳板機 讀 vROps 經中繼）。沒填 → 照舊人工列。天條：只讀已匯入的表，不連 vROps／vCenter／ESXi。
- 應有清單是母體（GPT 1）：應有／看到／連線／維護／異常／舊值、缺與多都列（GPT 9、10）；以 vROps 資源 ID 為主、名稱只顯示（GPT 11）。
- 資料存放區：剩餘 vm_lun_op vm_lun_threshold vm_lun_unit（預設 < 600 GB；使用者 11-13 確認：LUN＝資料存放區、單位 GB）；本機碟＝類型 VMFS 且掛載主機數 1（GPT 16）
  才排除，名稱規則（vm_local_patterns）只輔助、判不出列出不排除；資源 ID 去重（GPT 17）。
- 警示：等級用明確清單 vm_alert_levels（GPT 18）；vROps 靜音／已認領／清單靜音分列不排除（GPT 19）；清單靜音要期限＋理由＋核准人，
  逾期自動恢復成未解（GPT 20）。
- 無資料（不得當正常）：沒有完整批次、批次太舊（vm_fresh_min）、收集器對不上或停收（vm_adapter_stale_min，GPT 4、27）、
  這座 ESXi 或資料存放區 0 筆（GPT 6）、分頁不完整（批次不完整，GPT 5）。
- 人工確認拆「已檢視（Error Log／Mail）／結果／異常單號」（GPT 21、23）；手填 ESXi 台數、LUN 最小剩餘、有警示跟預填值不同＝覆寫，
  要原因，存原值／新值／原因／人／時間（GPT 22）。
- 新的完整批次到了、今天的快照還沒人確認任何一列 → 重新產生（新版本），舊版寫「已作廢」事件（GPT 24）；有人確認過就只提示。
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

ITEMS_PATH = Path(__file__).parent / "daily_check_items.json"
SETTING = "daily_check_settings"
FMT = "%Y-%m-%d %H:%M:%S"
VERDICT = {"ok": "正常", "bad": "異常", "nodata": "無資料", "ref": "前一日參考", "manual": "人工"}
RESULT = {"ok": "正常", "bad": "異常"}
DISK_EXC_CAP = 99.0          # 2.062.0：上次輪循模式異常門檻 95%，正本的單台例外是 97%／98.4%，上限改 99%（仍要原因＋到期日）
LP_RULES = ("perf_cpu", "perf_mem", "disk", "swap", "poll")
LP_SRC = "WhatsUp 上次輪循"
#: 正本 1-3～1-7 的群組（名稱以 WhatsUp 實際為準；管理員在設定頁改成完整路徑＋ID）
#: NB 11-13 第一次實跑：WhatsUp 實名「1-xServer (重要系統/交易)」（括號前半形空白）、「5-Infra(主機類_Windows/Linux)」；
#: 「1-xServer(正式-電券/交易)」已併進「1-xServer (重要系統/交易)」不存在了。比對時去空白、全形轉半形（wug_lastpoll.norm_name）。
DEFAULT_GROUPS = {
    "dc_group_cpu_mem": {"path": "1-xServer (重要系統/交易)", "id": ""},
    "dc_group_swap": {"path": "1-xServer (重要系統/交易)", "id": ""},
    "dc_group_disk": {"path": "Server", "id": ""},
    "dc_group_poll": [{"path": "1-xServer (重要系統/交易)", "id": ""}, {"path": "5-Infra(主機類_Windows/Linux)", "id": ""}],
}
DEFAULTS = {
    "enabled": False,                     # 排程自動產生／提醒（試辦：管理員打開）；手動產生不受影響
    "snapshot_time": "06:30", "remind_time": "07:45", "escalate_time": "08:15", "notify_stop_time": "09:30",
    "holidays": [],                       # 不上班的日期（YYYY-MM-DD；週六日本來就不跑），管理員維護
    "alert_fresh_min": 10, "perf_fresh_h": 30,
    "cpu": 75.0, "mem": 90.0, "disk": 85.0, "swap": 90.0, "poll_min": 60,
    "xserver_ips": [],                    # 1-xServer 範圍（IP 清單）；空＝未設定
    "banqiao": [],                        # [{name, ip}]
    "vcenters": [],                       # [名稱…]（VMware 人工列一座一列）；空＝一列
    "disk_exceptions": [],                # [{ip, threshold, reason, expires}]
    "escalate_to": [],                    # 升級通知收件人（信箱）；空＝只送 Teams 頻道並警示
    "urls": {},                           # {項次: 檢查網址}
    # 2.062.0（S13）上次輪循
    "daily_check_perf_source": "lastpoll",  # lastpoll＝WhatsUp 上次輪循群組快照；w3＝舊規則（前一日 W3 效能／磁碟中繼）
    "lp_fresh_min": 35,                   # 最新完整批次最多幾分鐘前（收集每 30 分鐘一批）
    "disk_list": 85.0, "disk_alert": 95.0,   # 磁碟：≥ disk_list 列出（參考）、≥ disk_alert 異常（正本 1-6）
    "wug_member_jump_pct": 20.0,          # 群組成員比已核准快照增減超過這個比例 → 範圍未確認
    **DEFAULT_GROUPS,
    # 2.064.0 VMware 段（正本 2-1～2-4）
    "vm_vcenters": [],                    # [{name, vcid, adapter_id, expected: [ESXi 名稱或 IP]}]；空＝VC 列照舊人工
    "vm_lun_threshold": 600.0,            # 使用者 11-13 確認：正本「LUN 低於 600」＝資料存放區剩餘 < 600 GB
    "vm_lun_unit": "GB",                  # 使用者 11-13 確認 GB
    "vm_lun_op": "<",                     # 等於 600 不算低於
    "vm_alert_levels": ["CRITICAL", "IMMEDIATE", "WARNING"],
    "vm_alert_mutes": [],                 # [{name（警示名稱）, res_id（vROps 資源 ID，空＝這個名稱的全部）, reason, approver, expires}]
    "vm_mute_max_days": 90,               # 靜音到期日最多今天＋幾天
    "vm_local_patterns": ["^datastore1( [(][0-9]+[)])?$", "local"],   # 本機碟名稱規則（只輔助，判不出列出不排除）
    "vm_fresh_min": 120,                  # 最新完整 VMware 批次最多幾分鐘前
    "vm_adapter_stale_min": 30,           # vROps 收集器最後收集比批次時間早超過幾分鐘＝停收
    "vm_record_stale_min": 60,            # 單筆採集時間比批次時間早超過幾分鐘＝舊值
}
VM_PENDING = ("vm_lun_threshold", "vm_lun_unit", "vm_lun_op")   # 口徑（11-13 已確認；API 仍回傳現值供畫面顯示）
VM_LEVELS = ("CRITICAL", "IMMEDIATE", "WARNING", "INFORMATION")
VM_SRC = "vROps（VMware）"

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_check_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, created_at TEXT NOT NULL, trigger TEXT NOT NULL, created_by TEXT,
    items_sha TEXT, settings_sha TEXT, sources_json TEXT, warnings_json TEXT,
    status TEXT NOT NULL DEFAULT 'open', submitted_by TEXT, submitted_at TEXT);
CREATE INDEX IF NOT EXISTS ix_daily_check_run_day ON daily_check_run(day, id);
CREATE TABLE IF NOT EXISTS daily_check_row (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES daily_check_run(id), no INTEGER NOT NULL,
    code TEXT NOT NULL, section TEXT, name TEXT NOT NULL, kind TEXT NOT NULL, rule TEXT, extra TEXT,
    verdict TEXT NOT NULL, value_text TEXT, reason TEXT, detail_json TEXT, data_at TEXT, source TEXT, batch TEXT, fresh INTEGER NOT NULL DEFAULT 0,
    result TEXT, note TEXT, extra_json TEXT, confirmed_by TEXT, confirmed_at TEXT);
CREATE INDEX IF NOT EXISTS ix_daily_check_row_run ON daily_check_row(run_id, no);
CREATE TABLE IF NOT EXISTS daily_check_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, row_id INTEGER, at TEXT NOT NULL, by TEXT,
    action TEXT NOT NULL, detail_json TEXT);
CREATE INDEX IF NOT EXISTS ix_daily_check_event_run ON daily_check_event(run_id, id);
CREATE TABLE IF NOT EXISTS daily_check_notice (
    day TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL, status TEXT NOT NULL,
    mail_status TEXT, mail_attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, kind));
CREATE TABLE IF NOT EXISTS daily_check_setting_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, by TEXT, key TEXT NOT NULL, before_json TEXT, after_json TEXT);
CREATE TABLE IF NOT EXISTS daily_check_mail_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL,
    recipients TEXT NOT NULL, ok INTEGER NOT NULL, http INTEGER, error TEXT, phase TEXT);
"""
#: 寄信用的傳送函式（None＝teams_notify.mail_transport，跟 Teams 通知「寄信模式」同一支、同一份 SMTP 設定）；測試換成假的
MAIL_TRANSPORT = None


# ---------------------------------------------------------------- 共用
def now_local() -> datetime:
    import quiet_window as qw
    return qw.local_now()


def _s(d: datetime) -> str:
    return d.strftime(FMT)


def _parse(t) -> datetime | None:
    """時間字串 → 台北當地 naive datetime。帶時區（+08:00、Z…）的照時區換算；沒帶的當台北時間（本系統存的都是台北時間）。"""
    if not t:
        return None
    raw = str(t).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if d.tzinfo is not None:
        from datetime import timezone
        d = d.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    return d


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def ensure(conn) -> None:
    conn.executescript(SCHEMA)
    if "stats_json" not in {r[1] for r in conn.execute("PRAGMA table_info(daily_check_row)")}:   # 2.062.0：上次輪循列的台數
        conn.execute("ALTER TABLE daily_check_row ADD COLUMN stats_json TEXT")
        conn.commit()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_check_notice)")}
    if "mail_status" not in cols:                       # 2.060.x 試辦前期的表：補欄
        conn.execute("ALTER TABLE daily_check_notice ADD COLUMN mail_status TEXT")
        conn.execute("ALTER TABLE daily_check_notice ADD COLUMN mail_attempts INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def _ready(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_check_run'").fetchone() is not None


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def load_items() -> dict:
    return json.loads(ITEMS_PATH.read_text(encoding="utf-8"))


def settings(conn) -> dict:
    r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING,)).fetchone()
    cur = {}
    if r and r[0]:
        try:
            cur = json.loads(r[0])
        except ValueError:
            cur = {}                       # 壞掉的設定：照預設（畫面的設定頁會看到是預設值，存一次就修好）
    return {**json.loads(json.dumps(DEFAULTS)), **{k: v for k, v in cur.items() if k in DEFAULTS}}


def lun_rule(cfg: dict) -> dict:
    """資料存放區「剩餘低於門檻」的唯一一把尺（1.5 VMware 段與 5.2 儲存容量共用；使用者 11-13／11-14 定）。
    門檻、單位、比較都從每日檢核設定來（DEFAULTS：< 600 GB；等於 600 不算低於）。"""
    th, unit, op = float(cfg["vm_lun_threshold"]), cfg["vm_lun_unit"], cfg["vm_lun_op"]
    return {"threshold": th, "unit": unit, "op": op, "threshold_gb": th * 1024 if unit == "TB" else th}


def lun_low(free_gb, rule: dict) -> bool:
    """剩餘 GB 是否低於門檻；查不到（None）不算低於（另列無剩餘值）。"""
    if free_gb is None:
        return False
    return free_gb < rule["threshold_gb"] if rule["op"] == "<" else free_gb <= rule["threshold_gb"]


def _hhmm(v: str) -> str:
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(v or "")):
        raise ValueError(f"時間格式要是 HH:MM：{v}")
    return v


def save_settings(conn, patch: dict, today: date | None = None, by: str | None = None) -> dict:
    """管理員改設定。例外門檻：要原因、到期日（今天以後）、門檻 ≤ 95%（GPT 7）。VMware（vm_*）的改動留紀錄（誰、何時、改前、改後）。"""
    import ipaddress

    cur = settings(conn)
    before = json.loads(json.dumps(cur))
    expired_mutes: list = []
    for k, v in (patch or {}).items():
        if k not in DEFAULTS:
            continue
        if k in ("snapshot_time", "remind_time", "escalate_time", "notify_stop_time"):
            v = _hhmm(v)
        elif k == "holidays":
            out = set()
            for x in v or []:
                x = str(x).strip()
                if not x:
                    continue
                try:
                    out.add(date.fromisoformat(x).isoformat())
                except ValueError:
                    raise ValueError(f"假日要是 YYYY-MM-DD：{x}") from None
            v = sorted(out)
        elif k in ("cpu", "mem", "disk", "swap"):
            v = float(v)
            if not 0 < v <= 100:
                raise ValueError(f"{k} 門檻要在 0～100")
        elif k in ("alert_fresh_min", "perf_fresh_h", "poll_min"):
            v = int(v)
            if v <= 0:
                raise ValueError(f"{k} 要大於 0")
        elif k == "enabled":
            v = bool(v)
        elif k == "xserver_ips":
            out = []
            for x in v or []:
                x = str(x).strip()
                if x:
                    out.append(str(ipaddress.ip_address(x)))
            v = sorted(set(out), key=lambda s: tuple(int(p) for p in s.split(".")) if "." in s else (0,))
        elif k == "banqiao":
            out = []
            for x in v or []:
                ip = str((x or {}).get("ip") or "").strip()
                out.append({"name": str((x or {}).get("name") or "").strip() or ip, "ip": str(ipaddress.ip_address(ip))})
            v = out
        elif k == "vcenters":
            v = [str(x).strip() for x in v or [] if str(x).strip()]
        elif k == "escalate_to":
            v = [str(x).strip() for x in v or [] if str(x).strip()]
            for x in v:
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", x):
                    raise ValueError(f"升級收件人要是信箱：{x}")
        elif k == "disk_exceptions":
            t = today or now_local().date()
            out = []
            for x in v or []:
                ip = str(ipaddress.ip_address(str(x.get("ip") or "").strip()))
                th = float(x.get("threshold"))
                if th > DISK_EXC_CAP or th <= 0:
                    raise ValueError(f"{ip} 的例外門檻最多 {DISK_EXC_CAP:g}%")
                reason = str(x.get("reason") or "").strip()
                if not reason:
                    raise ValueError(f"{ip} 的例外要寫原因")
                exp = str(x.get("expires") or "").strip()
                try:
                    if date.fromisoformat(exp) <= t:
                        raise ValueError(f"{ip} 的到期日要在今天之後")
                except ValueError as exc:
                    if "到期日" in str(exc):
                        raise
                    raise ValueError(f"{ip} 的到期日要是 YYYY-MM-DD") from None
                out.append({"ip": ip, "threshold": th, "reason": reason, "expires": exp})
            v = out
        elif k == "urls":
            v = {str(a): str(b) for a, b in (v or {}).items() if str(b).strip()}
        elif k in ("dc_group_cpu_mem", "dc_group_swap", "dc_group_disk"):
            v = _group_cfg(v, k)
        elif k == "dc_group_poll":
            v = [_group_cfg(x, k) for x in (v or [])]
            if not v or len(v) > 10:
                raise ValueError("輪詢列的群組要 1～10 個")
        elif k in ("disk_list", "disk_alert"):
            v = float(v)
            if not 0 < v <= 100:
                raise ValueError(f"{k} 門檻要在 0～100")
        elif k == "lp_fresh_min":
            v = int(v)
            if not 0 < v <= 1440:
                raise ValueError("上次輪循新鮮度要在 1～1440 分鐘")
        elif k == "wug_member_jump_pct":
            v = float(v)
            if not 0 < v <= 100:
                raise ValueError("成員跳動門檻要在 0～100%")
        elif k == "daily_check_perf_source":
            if v not in ("lastpoll", "w3"):
                raise ValueError("效能資料來源只能是 lastpoll 或 w3")
        elif k == "vm_alert_mutes":
            maxd = int(_vm_cfg("vm_mute_max_days", patch.get("vm_mute_max_days", cur.get("vm_mute_max_days") or 90), today or now_local().date()))
            v, gone = _vm_mutes(v, today or now_local().date(), maxd)
            expired_mutes = gone
        elif k.startswith("vm_"):
            v = _vm_cfg(k, v, today or now_local().date())
        cur[k] = v
    if "vm_mute_max_days" in (patch or {}) and "vm_alert_mutes" not in (patch or {}) and cur.get("vm_alert_mutes"):
        # 審查二 P3：只改「靜音最長天數」也要重新檢查既有靜音（超過新上限就擋；已逾期的照樣移出、記紀錄）
        cur["vm_alert_mutes"], gone = _vm_mutes(cur["vm_alert_mutes"], today or now_local().date(), int(cur["vm_mute_max_days"]))
        expired_mutes += gone
    if float(cur["disk_list"]) > float(cur["disk_alert"]):
        raise ValueError("磁碟「列出」門檻不能大於「異常」門檻")
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (SETTING, json.dumps(cur, ensure_ascii=False)))
    changed = [k for k in cur if k.startswith("vm_") and before.get(k) != cur[k]]
    if changed:
        ensure(conn)
        at = _s(now_local())
        conn.executemany("INSERT INTO daily_check_setting_log (at, by, key, before_json, after_json) VALUES (?,?,?,?,?)",
                         [(at, by, k, json.dumps(before.get(k), ensure_ascii=False), json.dumps(cur[k], ensure_ascii=False)) for k in changed])
    if expired_mutes:      # 審查 P2-3：逾期的靜音存檔時自動移出（不擋整份），另記一筆
        ensure(conn)
        conn.execute("INSERT INTO daily_check_setting_log (at, by, key, before_json, after_json) VALUES (?,?,?,?,?)",
                     (_s(now_local()), by, "vm_alert_mutes（逾期自動移出）", json.dumps(expired_mutes, ensure_ascii=False), "[]"))
    conn.commit()
    return cur


def norm_host(n) -> str:
    """ESXi 名稱口徑：小寫、去空白；FQDN 只留短名（IP 照原樣）。只用在「應有清單 vs vROps 名稱」比對，不是全站的同一台判斷。"""
    import ipaddress
    t = str(n or "").strip().lower()
    try:
        return str(ipaddress.ip_address(t))
    except ValueError:
        return t.split(".", 1)[0]


def _vm_cfg(k: str, v, today: date):
    if k == "vm_vcenters":
        out, names = [], set()
        for x in v or []:
            if not isinstance(x, dict):
                raise ValueError("vCenter 設定要是 {name, vcid, adapter_id, expected}")
            name = str(x.get("name") or "").strip()
            if not name or len(name) > 80:
                raise ValueError("vCenter 名稱要填（最多 80 字）")
            if name in names:
                raise ValueError(f"vCenter 名稱重複：{name}")
            names.add(name)
            vcid, aid = str(x.get("vcid") or "").strip(), str(x.get("adapter_id") or "").strip()
            if len(vcid) > 120 or len(aid) > 80:
                raise ValueError(f"{name}：vCenter 識別或收集器 ID 太長")
            exp = x.get("expected") or []
            if isinstance(exp, str):
                exp = re.split(r"[,\s]+", exp)
            exp = sorted({norm_host(h) for h in exp if str(h).strip()})
            if len(exp) > 500:
                raise ValueError(f"{name}：應有 ESXi 最多 500 台")
            out.append({"name": name, "vcid": vcid, "adapter_id": aid, "expected": exp})
        if len(out) > 20:
            raise ValueError("vCenter 最多 20 座")
        return out
    if k == "vm_lun_threshold":
        v = float(v)
        if not 0 < v <= 1_000_000:
            raise ValueError("LUN 門檻要大於 0")
        return v
    if k == "vm_lun_unit":
        if v not in ("GB", "TB"):
            raise ValueError("LUN 門檻單位只能是 GB 或 TB")
        return v
    if k == "vm_lun_op":
        if v not in ("<", "<="):
            raise ValueError("LUN 比較只能是 < 或 <=")
        return v
    if k == "vm_alert_levels":
        v = [str(x).strip().upper() for x in v or [] if str(x).strip()]
        if not v or any(x not in VM_LEVELS for x in v):
            raise ValueError(f"警示等級要從 {'、'.join(VM_LEVELS)} 挑（至少一個）")
        return sorted(set(v), key=VM_LEVELS.index)
    if k == "vm_mute_max_days":
        v = int(v)
        if not 1 <= v <= 365:
            raise ValueError("靜音最長天數要在 1～365")
        return v
    if k == "vm_alert_mutes":
        raise AssertionError("vm_alert_mutes 由 _vm_mutes 處理")
    if k == "vm_local_patterns":
        v = [str(x).strip() for x in v or [] if str(x).strip()]
        for x in v:
            try:
                re.compile(x)
            except re.error:
                raise ValueError(f"本機碟名稱規則不是合法的規則運算式：{x}") from None
        return v
    if k in ("vm_fresh_min", "vm_adapter_stale_min", "vm_record_stale_min"):
        v = int(v)
        if not 0 < v <= 1440:
            raise ValueError(f"{k} 要在 1～1440 分鐘")
        return v
    raise ValueError(f"不認得的設定：{k}")


def _vm_mutes(v, today: date, max_days: int) -> tuple[list, list]:
    """警示靜音清單：警示名稱必填、資源 ID 可空（＝這個名稱的全部）、理由＋核准人必填、到期日 ≤ 今天＋max_days。
    已逾期的不擋整份：自動移出，回傳給呼叫端記紀錄。"""
    out, gone = [], []
    for x in v or []:
        x = x or {}
        name = str(x.get("name") or x.get("key") or "").strip()
        if not name:
            raise ValueError("靜音要填警示名稱")
        reason, appr = str(x.get("reason") or "").strip(), str(x.get("approver") or "").strip()
        exp = str(x.get("expires") or "").strip()
        try:
            d = date.fromisoformat(exp)
        except ValueError:
            raise ValueError(f"靜音「{name}」的到期日要是 YYYY-MM-DD") from None
        item = {"name": name[:300], "res_id": str(x.get("res_id") or "").strip()[:80], "reason": reason[:300], "approver": appr[:80], "expires": exp}
        if str(x.get("vcenter") or "").strip():
            item["vcenter"] = str(x["vcenter"]).strip()[:80]
        if d <= today:
            gone.append(item)
            continue
        if not reason or not appr:
            raise ValueError(f"靜音「{name}」要寫理由與核准人")
        if d > today + timedelta(days=max_days):
            raise ValueError(f"靜音「{name}」的到期日最多到 {(today + timedelta(days=max_days)).isoformat()}（今天＋{max_days} 天）")
        out.append(item)
    return out, gone


def setting_log(conn, limit: int = 100) -> list[dict]:
    if not _has(conn, "daily_check_setting_log"):
        return []
    return [{**dict(r), "before": json.loads(r["before_json"] or "null"), "after": json.loads(r["after_json"] or "null")}
            for r in conn.execute("SELECT * FROM daily_check_setting_log ORDER BY id DESC LIMIT ?", (limit,))]


def _group_cfg(v, k: str) -> dict:
    """群組設定：{"path": 完整路徑, "id": WhatsUp 群組 ID（可空）}；也收單純的路徑文字。"""
    if isinstance(v, str):
        v = {"path": v}
    if not isinstance(v, dict):
        raise ValueError(f"{k}：群組要是 {{path, id}}")
    path = str(v.get("path") or "").strip()
    gid = str(v.get("id") or "").strip()
    if not path or len(path) > 300:
        raise ValueError(f"{k}：群組路徑要填（最多 300 字）")
    if len(gid) > 40 or (gid and not re.fullmatch(r"[A-Za-z0-9_-]+", gid)):
        raise ValueError(f"{k}：群組 ID 只能是英數（最多 40 字）")
    return {"path": path, "id": gid}


def auto_xserver_ips(conn) -> list[str]:
    """1-xServer 沒手填時的預設範圍（2.060.26，使用者 11-11「照建議」）：第一類業務系統、正式環境、非退役的登記 IP。
    分級用全站同一份（system_stats._system_classes，含人工覆寫）；環境用 env_group；一筆多個 IP 全收。"""
    import ipaddress

    import env_group
    import manage_state as ms
    import system_stats as ss
    s1 = {a for a, c in ss._system_classes(conn).items() if c.get("cls") == "第一類"}
    if not s1 or not _has(conn, "hardware"):
        return []
    out = set()
    for r in conn.execute("SELECT ip, api_id, environment, asset_status FROM hardware WHERE COALESCE(ip, '') <> ''"):
        if (r["api_id"] or "").strip() not in s1 or (r["asset_status"] or "").strip() in ms.RETIRED_STATUS:
            continue
        if env_group.classify(r["environment"]) != env_group.PROD:
            continue
        for x in re.split(r"[,;/\s]+", r["ip"] or ""):
            try:
                out.add(str(ipaddress.ip_address(x)))
            except ValueError:
                continue                   # 空字串或不是 IP 的片段：不是這一台的位址，跳過
    return sorted(out, key=lambda s: tuple(int(p) for p in s.split(".")) if "." in s else (0,))


def effective(conn, cfg: dict) -> dict:
    """產生快照用的設定：1-xServer 有手填用手填，沒有就用第一類正式主機（xserver_source 標來源）。"""
    if cfg["xserver_ips"]:
        return {**cfg, "xserver_source": "manual"}
    return {**cfg, "xserver_ips": auto_xserver_ips(conn), "xserver_source": "auto"}


def setting_warnings(cfg: dict, today: date) -> list[str]:
    w = []
    if cfg.get("daily_check_perf_source") == "w3":
        w.append("CPU／記憶體／磁碟／輪詢列用舊規則（W3 前一日資料）；改回「上次輪循」在設定頁")
        if cfg.get("xserver_source") == "auto":
            w.append(f"1-xServer 範圍未手填：改用第一類業務系統的正式環境主機（{len(cfg['xserver_ips'])} 個 IP）")
        if not cfg["xserver_ips"]:
            w.append("1-xServer 範圍（IP 清單）未設定：CPU／記憶體／磁碟／輪詢列都會是無資料")
    if not cfg["banqiao"]:
        w.append("板橋交易所主機清單未設定")
    if not cfg["escalate_to"]:
        w.append("升級通知收件人未設定：08:15 還沒送出時只會送到 Teams 頻道")
    for x in cfg["disk_exceptions"]:
        if str(x.get("expires") or "") <= today.isoformat():
            w.append(f"{x.get('ip')} 的磁碟例外門檻已於 {x.get('expires')} 到期，已失效（照一般門檻判）")
    if cfg.get("vm_vcenters"):
        for x in cfg.get("vm_alert_mutes") or []:
            if str(x.get("expires") or "") <= today.isoformat():
                w.append(f"VMware 警示靜音「{x.get('name') or x.get('key')}」已於 {x.get('expires')} 到期，已自動恢復成未解")
    return w


# ---------------------------------------------------------------- 資產對應（GPT 5：對得到就給資產，對不到照實寫）
def _who(conn, ip: str) -> dict:
    r = conn.execute("SELECT asset_serial, hostname FROM hardware WHERE TRIM(ip) = ? ORDER BY asset_serial LIMIT 1", (ip,)).fetchone()
    return {"ip": ip, "serial": r["asset_serial"] if r else None, "name": (r["hostname"] if r else None) or "（對不到資產）"}


# ---------------------------------------------------------------- 來源：WhatsUp 告警快照（GPT 4：分母可驗證）
def wug_source(conn, cfg: dict, now: datetime) -> dict:
    if not _has(conn, "alert_scan"):
        return {"ok": False, "reason": "從沒收到 WhatsUp 告警快照"}
    r = conn.execute("SELECT * FROM alert_scan WHERE kind = 'scan' ORDER BY snap_at DESC, id DESC LIMIT 1").fetchone()
    if not r:
        return {"ok": False, "reason": "從沒收到 WhatsUp 告警快照"}
    try:
        group = json.loads(r["group_json"] or "{}")
    except ValueError:
        group = {}
    total = group.get("totalDevices") if isinstance(group, dict) else None
    blk = {"batch": r["batch_id"], "snap_at": r["snap_at"], "complete": bool(r["complete"]), "devices_seen": r["devices_seen"],
           "group_total": total, "stopped_reason": r["stopped_reason"], "problems": r["problems"]}
    # 收集端狀態沒變時只送心跳（kind=heartbeat），代表「跟上一次完整掃描一樣」：新鮮度看最後一次掃描或之後的心跳（2.060.26）
    hb = conn.execute("SELECT MAX(snap_at) FROM alert_scan WHERE kind = 'heartbeat' AND snap_at >= ?", (r["snap_at"],)).fetchone()[0]
    last = max(r["snap_at"], hb) if hb else r["snap_at"]
    blk["last_seen"] = last
    at = _parse(last)
    why = None
    if at is None or now - at > timedelta(minutes=int(cfg["alert_fresh_min"])):
        why = f"告警快照太舊（最後 {last}，門檻 {cfg['alert_fresh_min']} 分鐘）"
    elif not r["complete"]:
        why = f"最近一次掃描不完整：{r['stopped_reason'] or '收集端標不完整'}"
    elif type(total) is not int or total <= 0 or (r["devices_seen"] or 0) < total * 0.98:
        why = f"掃描看到 {r['devices_seen']} 台、群組總數 {total}，對不上"
    return {"ok": why is None, "reason": why, "block": blk}


def _open_events(conn, kind: str) -> list[dict]:
    if not _has(conn, "alert_event"):
        return []
    if kind == "down":
        q = "SELECT * FROM alert_event WHERE ended_at IS NULL AND LOWER(COALESCE(wug_state,'')) LIKE 'down%' ORDER BY ip"
    else:
        q = "SELECT * FROM alert_event WHERE ended_at IS NULL AND wug_state = 'Maintenance' ORDER BY ip"
    return [dict(r) for r in conn.execute(q)]


# ---------------------------------------------------------------- 來源：WhatsUp 效能／磁碟中繼（前一日參考）
def perf_source(conn, cfg: dict, now: datetime) -> dict:
    if not _has(conn, "relay_import_batches"):
        return {"ok": False, "reason": "從沒收到 WhatsUp 效能中繼"}
    r = conn.execute("SELECT batch_id, collected_at FROM relay_import_batches WHERE source = 'whatsup' AND status = 'imported' "
                     "ORDER BY collected_at DESC, id DESC LIMIT 1").fetchone()
    if not r:
        return {"ok": False, "reason": "從沒收到 WhatsUp 效能中繼"}
    at = _parse(r["collected_at"])
    if at is None or now - at > timedelta(hours=int(cfg["perf_fresh_h"])):
        return {"ok": False, "reason": f"效能資料太舊（最後收集 {r['collected_at']}，門檻 {cfg['perf_fresh_h']} 小時）",
                "batch": r["batch_id"], "collected_at": r["collected_at"]}
    if not cfg["xserver_ips"]:
        return {"ok": False, "reason": "1-xServer 範圍（IP 清單）未設定", "batch": r["batch_id"], "collected_at": r["collected_at"]}
    return {"ok": True, "batch": r["batch_id"], "collected_at": r["collected_at"], "day": (now.date() - timedelta(days=1)).isoformat(),
            "at": at}


def _perf_day(conn, ip: str, day: str) -> dict | None:
    import capacity_analysis as ca
    if not _has(conn, "capacity_whatsup_perf"):
        return None
    r = conn.execute("SELECT data FROM capacity_whatsup_perf WHERE host = ? AND day = ?", (ip, day)).fetchone()
    if not r:
        return None
    try:
        return ca._decode(r[0])
    except ValueError:
        return None                         # 壞掉的資料列：當這台沒資料（不猜）


def _peak(obj: dict, metric: str) -> float | None:
    """容量分析同一份序列：每個時間桶的 max（沒有 max 用平均）取最高。"""
    mx = (obj.get("max") or {}).get(metric) or []
    av = (obj.get("cols") or {}).get(metric) or []
    vals = [b if b is not None else a for a, b in zip(av + [None] * (len(mx) - len(av)), mx + [None] * (len(av) - len(mx)))]
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(max(vals), 1) if vals else None


# 2.060.x：WhatsUp 用 SNMP 看主機時，SWAP 常被列成一顆「磁碟」（Swap space／Virtual memory）。磁碟規則不算它（SWAP 滿不是硬碟滿），
# SWAP 規則只算它。認不出的名稱一律當一般磁碟（不猜）。
_SWAP_NAME = re.compile(r"swap|virtual\s*memory|虛擬記憶體", re.I)


def _is_swap(disk: str) -> bool:
    return bool(_SWAP_NAME.search(str(disk or "")))


def _disks(conn, ip: str, day: str) -> list[tuple[str, float, str]]:
    if not _has(conn, "relay_disk_util"):
        return []
    out = []
    for r in conn.execute("SELECT disk, data FROM relay_disk_util WHERE host = ? AND day = ? ORDER BY disk", (ip, day)):
        try:
            d = json.loads(gzip.decompress(r["data"]).decode())
            pts = [(t, a) for t, a in zip(d.get("ts") or [], d.get("avg") or []) if isinstance(a, (int, float))]
        except (OSError, ValueError, TypeError):
            continue                        # 壞掉的列：當這顆碟沒資料（不猜）
        if pts:
            out.append((r["disk"], round(pts[-1][1], 1), pts[-1][0]))
    return out


# ---------------------------------------------------------------- 規則
def _row(verdict, value, reason=None, detail=None, data_at=None, source=None, batch=None, fresh=False, stats=None) -> dict:
    return {"verdict": verdict, "value_text": value, "reason": reason, "detail": detail or [], "data_at": data_at, "source": source,
            "batch": batch, "fresh": fresh, "stats": stats}


def rule_wug(conn, kind: str, src: dict) -> dict:
    if not src["ok"]:
        return _row("nodata", None, src["reason"], source="WhatsUp 告警快照", data_at=(src.get("block") or {}).get("snap_at"),
                    batch=(src.get("block") or {}).get("batch"))
    b = src["block"]
    evs = _open_events(conn, kind)
    devs = {}
    for e in evs:
        devs.setdefault(e["wug_device_id"], e)
    detail = [{**(_who(conn, e["ip"]) if e.get("ip") else {"ip": None, "serial": None, "name": e.get("device_name") or "—"}),
               "what": f"{e.get('device_name') or ''} {e.get('monitor') or ''} {e.get('wug_state') or ''}".strip(), "since": e.get("started_at")}
              for e in devs.values()]
    n = len(devs)
    value = f"{n} 台（掃描看到 {b['devices_seen']}／群組 {b['group_total']} 台）"
    if kind == "maint":
        # 審查 P2-4：有設備在維護中就要人看一眼（ref）；0 台＝正常
        return _row("ok" if n == 0 else "ref", value, None if n == 0 else "有設備在維護中，請確認是預期的維護",
                    detail, b["snap_at"], "WhatsUp 告警快照", b["batch"], n == 0)
    return _row("ok" if n == 0 else "bad", value, None, detail, b["snap_at"], "WhatsUp 告警快照", b["batch"], True)


def rule_perf(conn, metric: str, cfg: dict, src: dict) -> dict:
    label = {"cpu_busy": "CPU", "mem_used": "記憶體"}[metric]
    if not src["ok"]:
        return _row("nodata", None, src["reason"], source="WhatsUp 效能中繼", data_at=src.get("collected_at"), batch=src.get("batch"))
    th = float(cfg["cpu" if metric == "cpu_busy" else "mem"])
    over, nod = [], 0
    for ip in cfg["xserver_ips"]:
        obj = _perf_day(conn, ip, src["day"])
        pk = _peak(obj, metric) if obj else None
        if pk is None:
            nod += 1
            continue
        if pk > th:
            over.append({**_who(conn, ip), "what": f"{label}最高 {pk:g}%", "since": src["day"]})
    tot = len(cfg["xserver_ips"])
    if nod == tot:
        return _row("nodata", None, f"設定的 {tot} 台前一日（{src['day']}）都沒有{label}資料", source="WhatsUp 效能中繼",
                    data_at=src["collected_at"], batch=src["batch"])
    return _row("ref", f"{len(over)} 台超過 {th:g}%（{tot - nod}／{tot} 台有資料）", f"前一日（{src['day']}）每台最高值；要人確認",
                over, src["collected_at"], "WhatsUp 效能中繼", src["batch"])


def rule_disk(conn, cfg: dict, src: dict, today: date) -> dict:
    if not src["ok"]:
        return _row("nodata", None, src["reason"], source="WhatsUp 磁碟中繼", data_at=src.get("collected_at"), batch=src.get("batch"))
    base = float(cfg["disk"])
    exc = {x["ip"]: x for x in cfg["disk_exceptions"] if str(x.get("expires") or "") > today.isoformat()}
    import disk_alert
    pats = disk_alert.exclude_list(conn)
    over, nod = [], 0
    for ip in cfg["xserver_ips"]:
        ds = _disks(conn, ip, src["day"])
        if not ds:
            nod += 1
            continue
        th = float(exc[ip]["threshold"]) if ip in exc else base
        for disk, pct, ts in ds:
            if _is_swap(disk) or disk_alert.excluded(disk, pats):   # 2.060.19（S6）：交換器 devfs 這類假磁碟跟告警同一份排除清單
                continue
            if pct > th:
                over.append({**_who(conn, ip), "what": f"{disk} {pct:g}%（門檻 {th:g}%{'・單台例外' if ip in exc else ''}）", "since": ts})
    tot = len(cfg["xserver_ips"])
    if nod == tot:
        return _row("nodata", None, f"設定的 {tot} 台前一日（{src['day']}）都沒有磁碟資料", source="WhatsUp 磁碟中繼",
                    data_at=src["collected_at"], batch=src["batch"])
    return _row("ref", f"{len(over)} 顆超過門檻（{tot - nod}／{tot} 台有資料）", f"前一日（{src['day']}）每顆碟最後一點；要人確認",
                over, src["collected_at"], "WhatsUp 磁碟中繼", src["batch"])


def rule_swap(conn, cfg: dict, src: dict) -> dict:
    """Linux SWAP 超過門檻（預設 90%，同手填表）：只看 WhatsUp 磁碟資料裡名稱像 SWAP 的那一顆；前一日參考、永遠要人勾。
    範圍內沒有任何一台有 SWAP 項目＝無資料（WhatsUp 沒把 SWAP 當磁碟收，或這批主機沒有）——不可以當正常。"""
    if not src["ok"]:
        return _row("nodata", None, src["reason"], source="WhatsUp 磁碟中繼", data_at=src.get("collected_at"), batch=src.get("batch"))
    th = float(cfg["swap"])
    over, seen = [], 0
    for ip in cfg["xserver_ips"]:
        for disk, pct, ts in _disks(conn, ip, src["day"]):
            if not _is_swap(disk):
                continue
            seen += 1
            if pct > th:
                over.append({**_who(conn, ip), "what": f"{disk} {pct:g}%（門檻 {th:g}%）", "since": ts})
    tot = len(cfg["xserver_ips"])
    if not tot:
        return _row("nodata", None, "1-xServer 範圍（IP 清單）未設定", source="WhatsUp 磁碟中繼", data_at=src["collected_at"], batch=src["batch"])
    if not seen:
        # 2.060.20（S8，NB 11-11 實測：WhatsUp 磁碟資料沒有 Swap）→ 退到納管健康檢查快照（health_snapshot.swap_pct，每週六＋手動）；
        # 都沒有就寫「不知道」，不可以顯示 0／正常
        return _swap_from_health(conn, cfg, th, src)
    return _row("ref", f"{len(over)} 台超過 {th:g}%（{seen} 個 SWAP 項目有資料）", f"前一日（{src['day']}）最後一點；要人確認",
                over, src["collected_at"], "WhatsUp 磁碟中繼", src["batch"])


def _swap_from_health(conn, cfg: dict, th: float, src: dict) -> dict:
    """WhatsUp 沒有 SWAP 時的第二來源：每台最近一份納管健康檢查快照的 SWAP 使用率（標快照日期；超過 14 天的不採用）。"""
    tot = len(cfg["xserver_ips"])
    srcname = "WhatsUp 磁碟中繼／納管健康檢查"
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='health_snapshot'").fetchone()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(health_snapshot)")} if has else set()
    if "swap_pct" not in cols:
        return _row("nodata", None, f"不知道：WhatsUp 磁碟資料沒有 SWAP 項目，也還沒有納管健康檢查的 SWAP 資料（{tot} 台）",
                    source=srcname, data_at=src.get("collected_at"), batch=src.get("batch"))
    edge = (src["at"] - timedelta(days=14)).strftime("%Y-%m-%d")
    over, seen, newest = [], 0, None
    for ip in cfg["xserver_ips"]:
        r = conn.execute("SELECT taken_at, swap_pct FROM health_snapshot WHERE ip = ? AND swap_pct IS NOT NULL AND taken_at >= ? "
                         "ORDER BY id DESC LIMIT 1", (ip, edge)).fetchone()
        if not r:
            continue
        seen += 1
        newest = max(newest or "", str(r["taken_at"]))
        if float(r["swap_pct"]) > th:
            over.append({**_who(conn, ip), "what": f"SWAP {float(r['swap_pct']):g}%（門檻 {th:g}%，健康檢查 {str(r['taken_at'])[5:16]}）",
                         "since": r["taken_at"]})
    if not seen:
        return _row("nodata", None, f"不知道：WhatsUp 磁碟資料沒有 SWAP 項目；{tot} 台近 14 天也沒有納管健康檢查的 SWAP 資料",
                    source=srcname, data_at=src.get("collected_at"), batch=src.get("batch"))
    return _row("ref", f"{len(over)} 台超過 {th:g}%（{seen}／{tot} 台有健康檢查 SWAP 資料）",
                f"WhatsUp 沒有 SWAP，改用最近一次納管健康檢查（最新 {newest[:16]}）；其餘 {tot - seen} 台不知道；要人確認",
                over, newest, "納管健康檢查（每週）", None)


def rule_poll(conn, cfg: dict, src: dict) -> dict:
    if not src["ok"]:
        return _row("nodata", None, src["reason"], source="WhatsUp 效能中繼", data_at=src.get("collected_at"), batch=src.get("batch"))
    lim = timedelta(minutes=int(cfg["poll_min"]))
    late, nod = [], 0
    for ip in cfg["xserver_ips"]:
        obj = _perf_day(conn, ip, src["day"])
        last = _parse((obj or {}).get("ts", [None])[-1]) if obj and obj.get("ts") else None
        if last is None:
            nod += 1
            late.append({**_who(conn, ip), "what": "前一日沒有任何輪詢資料", "since": src["day"]})
            continue
        # 序列時間是 UTC（tz=UTC）：轉台北再比
        last_tpe = last + timedelta(hours=8) if (obj.get("tz") or "UTC") == "UTC" else last
        day_end = datetime.fromisoformat(src["day"]) + timedelta(days=1)
        ref = min(src["at"], day_end)
        if ref - last_tpe > lim:
            late.append({**_who(conn, ip), "what": f"最後輪詢 {_s(last_tpe)}", "since": _s(last_tpe)})
    tot = len(cfg["xserver_ips"])
    if nod == tot:
        return _row("nodata", None, f"設定的 {tot} 台前一日（{src['day']}）都沒有輪詢資料", source="WhatsUp 效能中繼",
                    data_at=src["collected_at"], batch=src["batch"])
    return _row("ref", f"{len(late)} 台超過 {cfg['poll_min']} 分鐘沒回報（{tot - nod}／{tot} 台有資料）",
                f"前一日（{src['day']}）每台最後一個輪詢時間；要人確認", late, src["collected_at"], "WhatsUp 效能中繼", src["batch"])


def rule_ad_time(conn) -> dict:
    """AD 對時（使用者 11-11 選自動、全部 DC）：資料來源 ad_time.latest（9.26 跑 w32tm /monitor 經中繼送來；唯讀、不建表）。
    ok → 正常；warn → 異常；no_data（太舊、有 DC 沒回應、從沒收到）→ 無資料＋原因。明細逐台 DC。"""
    try:
        import ad_time
    except ImportError:
        return _row("nodata", None, "AD 對時資料來源還沒上線（ad_time）", source="AD 對時中繼")
    d = ad_time.latest(conn) or {}
    dcs = d.get("dcs") or []
    vlab = {"normal": "正常", "warn": "超過門檻", "no_data": "沒回應／無資料"}       # ad_time 的 verdict（ok 欄＝有沒有回應）
    detail = [{"ip": x.get("ip"), "serial": None, "name": x.get("name") or x.get("ip") or "—",
               "what": (f"時間差 {x['offset_s']:+g} 秒" if isinstance(x.get("offset_s"), (int, float)) else "沒有時間差")
                       + (f"・{vlab.get(x.get('verdict'), x.get('verdict'))}" if x.get("verdict") else "") + (f"・{x.get('note')}" if x.get("note") else ""),
               "since": d.get("collected_at")} for x in dcs]
    for x in detail:
        if x["ip"]:
            w = _who(conn, x["ip"])
            if w["serial"]:
                x["serial"] = w["serial"]
    st = d.get("status")
    n_bad = sum(1 for x in dcs if x.get("verdict") == "warn")
    n_nr = sum(1 for x in dcs if x.get("verdict") == "no_data")
    lim = d.get("warn_seconds")
    value = f"{len(dcs)} 台 DC" + (f"，{n_bad} 台超過 {lim:g} 秒" if isinstance(lim, (int, float)) and n_bad else "") \
        + (f"，{n_nr} 台沒回應" if n_nr else "")
    if st == "ok":
        return _row("ok", value, None, detail, d.get("collected_at"), "AD 對時中繼", None, True)
    if st == "warn":
        return _row("bad", value, d.get("reason"), detail, d.get("collected_at"), "AD 對時中繼", None, True)
    return _row("nodata", value if dcs else None, d.get("reason") or "沒有 AD 對時資料", detail, d.get("collected_at"), "AD 對時中繼")


def rule_banqiao(conn, ent: dict, src: dict) -> dict:
    """GPT 3：告警快照只列有問題的設備，沒有逐台 Up 紀錄 → 只有「明確 Down／維護」能判（異常），其餘一律無資料。"""
    who = _who(conn, ent["ip"])
    if not src["ok"]:
        return _row("nodata", None, src["reason"], [who], (src.get("block") or {}).get("snap_at"), "WhatsUp 告警快照", (src.get("block") or {}).get("batch"))
    b = src["block"]
    ev = [e for e in _open_events(conn, "down") + _open_events(conn, "maint") if e.get("ip") == ent["ip"]]
    if ev:
        return _row("bad", "、".join(sorted({e.get("wug_state") or "Down" for e in ev})), None,
                    [{**who, "what": f"{e.get('monitor') or ''} {e.get('wug_state') or ''}", "since": e.get("started_at")} for e in ev],
                    b["snap_at"], "WhatsUp 告警快照", b["batch"], True)
    return _row("nodata", None, "告警快照只列有問題的設備，沒有這台明確的 Up 紀錄（第一期不從「不在問題清單」推 Up）",
                [who], b["snap_at"], "WhatsUp 告警快照", b["batch"])


# ---------------------------------------------------------------- 來源：WhatsUp 上次輪循（2.062.0，S13；正本 1-3～1-7）
def lp_source(conn, cfg: dict, now: datetime) -> dict:
    """最新且完整的一批，且 ≤ lp_fresh_min 分鐘；不然整個來源無資料（原因寫清楚，後面那批為什麼不完整也寫）。"""
    import wug_lastpoll as lp
    b, last = lp.latest_complete(conn), lp.latest_any(conn)
    later = (f"；之後那批（{last['collected_at']}）不完整：{'；'.join(last['problems'][:2])}" if last and (b is None or last["batch_id"] != b["batch_id"])
             and last["problems"] else "")
    if b is None:
        return {"ok": False, "reason": "從沒收到完整的 WhatsUp 上次輪循批次" + later, "batch": last and last["batch_id"],
                "collected_at": last and last["collected_at"]}
    at = datetime.strptime(b["collected_at"], FMT)
    age = (now - at).total_seconds() / 60
    out = {"batch": b["batch_id"], "collected_at": b["collected_at"], "at": at, "b": b, "skew": max(0, int((now - at).total_seconds()))}
    if age > int(cfg["lp_fresh_min"]):
        return {**out, "ok": False, "reason": f"上次輪循資料太舊（最新完整一批 {b['collected_at']}，{age:.0f} 分鐘前，門檻 {cfg['lp_fresh_min']} 分鐘）" + later}
    return {**out, "ok": True, "reason": None}


def _lp_group(conn, cfg: dict, src: dict, want) -> tuple[dict | None, str | None]:
    import wug_lastpoll as lp
    if not src["ok"]:
        return None, src["reason"]
    g, why = lp.match_group(src["b"], want)
    if why:
        return None, why
    sc = lp.scope_check(conn, src["b"], g["path"], float(cfg["wug_member_jump_pct"]))
    if not sc["ok"]:
        return None, sc["reason"]
    return g, None


def _eff(v: dict, src: dict) -> int | None:
    """有效年齡（秒）＝WhatsUp 回的距上次輪詢秒數＋（產生快照時間－批次收集時間）。沒有 age_s＝不知道（不算新鮮）。"""
    return None if v["age_s"] is None else int(v["age_s"]) + int(src.get("skew") or 0)


def _lp_nodata(src: dict, why: str) -> dict:
    return _row("nodata", None, why, source=LP_SRC, data_at=src.get("collected_at"), batch=src.get("batch"))


def _lp_who(conn, cache: dict, ip: str | None, name: str | None) -> dict:
    """設備 → 資產（同一台的判斷用 relay_import.match_asset＝machine_key，不自寫去重）；對不到照算、標「資產對不到」。"""
    ip = (ip or "").strip()
    if ip not in cache:
        serial = host = None
        if ip:
            import relay_import as ri
            res, _n, serial = ri.match_asset(conn, ip)
            if serial:
                h = conn.execute("SELECT hostname FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
                host = h[0] if h else None
        cache[ip] = (serial, host)
    serial, host = cache[ip]
    return {"ip": ip or None, "serial": serial, "name": host or name or "—", "asset": "" if serial else "資產對不到"}


def _lp_universe(conn, src: dict, path: str, metric_rows: list[dict]) -> dict:
    """範圍＝群組成員 ∪ 報表裡出現的設備，以 deviceId 去重（同一台在多個子群組只算一次）。"""
    import wug_lastpoll as lp
    devs: dict = {}
    for m in lp.members_of(conn, src["b"], path):
        devs.setdefault(m["device_id"], {"ip": m["ip"], "name": m["name"]})
    for v in metric_rows:
        d = devs.setdefault(v["device_id"], {"ip": v["ip"], "name": v["name"]})
        d["ip"] = d["ip"] or v["ip"]
    return devs


_STATE_ORDER = {"異常": 0, "參考": 1, "舊值": 2, "無資料": 3, "正常": 4}


def _lp_finish(detail: list[dict]) -> list[dict]:
    detail.sort(key=lambda x: (_STATE_ORDER.get(x["state"], 9), x.get("ip") or "", x["device_id"]))
    return detail


def _lp_counts(detail: list[dict], scope: int, extra: dict) -> dict:
    nod = sum(1 for d in detail if d["state"] in ("舊值", "無資料"))
    return {"scope": scope, "with_data": scope - nod, "no_data": nod, "stale": sum(1 for d in detail if d["state"] == "舊值"),
            "bad": sum(1 for d in detail if d["state"] == "異常"), "asset_unmatched": sum(1 for d in detail if d.get("asset")), **extra}


def _lp_partial(g: dict, metrics) -> str | None:
    """收集端只讀到部分的報表（頁數上限／呼叫預算／時間盒）：回一句說明；沒有就 None。讀到的照算，沒讀到的設備逐台無資料。"""
    hit = [(k, g.get(k) or {}) for k in metrics if (g.get(k) or {}).get("partial")]
    if not hit:
        return None
    return "WhatsUp 報表只讀到部分（" + "、".join(f"{METRIC_ZH[k]} {m.get('pages') or '?'} 頁 {m.get('rows')} 筆" for k, m in hit) + \
        "，依使用率由高到低），沒讀到的設備列無資料，要人看一眼"


def _lp_tail(st: dict) -> str:
    t = f"有資料 {st['with_data']}／範圍 {st['scope']} 台"
    if st["no_data"]:
        t += f"；無資料 {st['no_data']} 台" + (f"（其中舊值 {st['stale']}）" if st["stale"] else "")
    return t


def rule_lp_pct(conn, cfg: dict, src: dict, metric: str, cache: dict) -> dict:
    """1-3 CPU（> cpu%）／1-4 記憶體（> mem%）：群組 dc_group_cpu_mem 每台「上次輪循」平均值。記憶體不算 SWAP／虛擬記憶體項目。"""
    import wug_lastpoll as lp
    label = {"cpu": "CPU", "mem": "記憶體"}[metric]
    th = float(cfg["cpu" if metric == "cpu" else "mem"])
    g, why = _lp_group(conn, cfg, src, cfg["dc_group_cpu_mem"])
    if why:
        return _lp_nodata(src, why)
    if (g.get(metric) or {}).get("unit_err"):            # 審查 P2：單位判不出／>100% → 不判，絕不顯示正常
        return _lp_nodata(src, f"WhatsUp {label}：{g[metric]['unit_err']}")
    vals = [v for v in lp.values(conn, src["batch"], g["path"], metric) if not (metric == "mem" and _is_swap(v["item"]))]
    devs = _lp_universe(conn, src, g["path"], vals)
    per: dict = {}
    for v in vals:
        per.setdefault(v["device_id"], []).append(v)
    lim = int(cfg["poll_min"]) * 60
    detail = []
    for did, d in devs.items():
        vs = [x for x in per.get(did, []) if x["pct"] is not None]
        fresh = [x for x in vs if _eff(x, src) is not None and _eff(x, src) <= lim]
        if fresh:
            pk = max(fresh, key=lambda x: x["pct"])
            state, what, since = ("異常" if pk["pct"] >= th else "正常"), f"{label} {pk['pct']:g}%" + (f"（{pk['item']}）" if pk["item"] and metric == "mem" else ""), pk["poll_at"]
        elif vs:
            old = max(vs, key=lambda x: _eff(x, src) or 0)
            state, what, since = "舊值", f"上次輪詢 {(_eff(old, src) or 0) // 60} 分鐘前（舊值 {old['pct']:g}%，不算數）" if old["age_s"] is not None else "沒有輪詢時間（不算數）", old["poll_at"]
        else:
            state, what, since = "無資料", f"WhatsUp 沒有這台的{label}值", None
        detail.append({**_lp_who(conn, cache, d["ip"], d["name"]), "device_id": did, "state": state, "what": what, "since": since, "src": g["path"]})
    st = _lp_counts(_lp_finish(detail), len(devs), {"group": g["path"], "group_id": g.get("id"), "threshold": th})
    cmp = ""
    if cfg.get("xserver_source") == "manual" and cfg["xserver_ips"]:
        ips = {d["ip"] for d in devs.values() if d["ip"]}
        manual = set(cfg["xserver_ips"])
        st["manual_only"], st["group_only"] = sorted(manual - ips), sorted(ips - manual)
        cmp = f"；手填比對：手填有群組沒有 {len(st['manual_only'])}、群組有手填沒有 {len(st['group_only'])}"
    if not st["with_data"]:
        return _row("nodata", None, f"群組「{g['path']}」{st['scope']} 台都沒有新的{label}值", detail, src["collected_at"], LP_SRC, src["batch"], stats=st)
    value = f"{st['bad']} 台達 {th:g}% 以上（{_lp_tail(st)}）{cmp}"
    reason = f"有 {st['no_data']} 台沒有新的{label}值，要人看一眼" if st["no_data"] else None
    pn = _lp_partial(g, (metric,))
    if pn:
        reason = f"{reason}；{pn}" if reason else pn
    return _row("bad" if st["bad"] else "ok", value, reason, detail, src["collected_at"], LP_SRC, src["batch"], fresh=not st["no_data"] and not pn, stats=st)


def rule_lp_disk(conn, cfg: dict, src: dict, today: date, cache: dict) -> dict:
    """1-6 硬碟：群組 dc_group_disk 每顆碟；≥ disk_alert 異常、disk_list～disk_alert 參考；單台例外（disk_exceptions）取代異常門檻。
    SWAP 項目與磁碟告警的排除清單（交換器 devfs 這類假磁碟）不算。"""
    import disk_alert
    import wug_lastpoll as lp
    g, why = _lp_group(conn, cfg, src, cfg["dc_group_disk"])
    if why:
        return _lp_nodata(src, why)
    if (g.get("disk") or {}).get("unit_err"):            # 審查 P2：單位判不出／>100% → 不判，絕不顯示正常
        return _lp_nodata(src, f"WhatsUp 磁碟：{g['disk']['unit_err']}")
    pats = disk_alert.exclude_list(conn)
    vals = [v for v in lp.values(conn, src["batch"], g["path"], "disk") if not (_is_swap(v["item"]) or disk_alert.excluded(v["item"], pats))]
    devs = _lp_universe(conn, src, g["path"], vals)
    per: dict = {}
    for v in vals:
        per.setdefault(v["device_id"], []).append(v)
    lst, alert = float(cfg["disk_list"]), float(cfg["disk_alert"])
    exc = {x["ip"]: x for x in cfg["disk_exceptions"] if str(x.get("expires") or "") > today.isoformat()}
    lim = int(cfg["poll_min"]) * 60
    detail, n_disks = [], 0
    for did, d in devs.items():
        vs = [x for x in per.get(did, []) if x["pct"] is not None]
        fresh = [x for x in vs if _eff(x, src) is not None and _eff(x, src) <= lim]
        th = float(exc[d["ip"]]["threshold"]) if d["ip"] in exc else alert
        tag = "・單台例外" if d["ip"] in exc else ""
        if fresh:
            hot = sorted([x for x in fresh if x["pct"] >= th], key=lambda x: -x["pct"])
            warm = sorted([x for x in fresh if lst <= x["pct"] < th], key=lambda x: -x["pct"])
            n_disks += len(hot)
            top = max(fresh, key=lambda x: x["pct"])
            if hot:
                state, what = "異常", "、".join(f"{x['item']} {x['pct']:g}%" for x in hot[:6]) + f"（≥ {th:g}%{tag}）"
            elif warm:
                state, what = "參考", "、".join(f"{x['item']} {x['pct']:g}%" for x in warm[:6]) + f"（{lst:g}～{th:g}%{tag}）"
            else:
                state, what = "正常", f"最高 {top['item']} {top['pct']:g}%"
            since = top["poll_at"]
        elif vs:
            old = max(vs, key=lambda x: _eff(x, src) or 0)
            state, what, since = "舊值", f"上次輪詢 {(_eff(old, src) or 0) // 60} 分鐘前（舊值，不算數）" if old["age_s"] is not None else "沒有輪詢時間（不算數）", old["poll_at"]
        else:
            state, what, since = "無資料", "WhatsUp 沒有這台的磁碟值", None
        detail.append({**_lp_who(conn, cache, d["ip"], d["name"]), "device_id": did, "state": state, "what": what, "since": since, "src": g["path"]})
    st = _lp_counts(_lp_finish(detail), len(devs), {"group": g["path"], "group_id": g.get("id"), "threshold": alert, "list": lst,
                                                    "ref": sum(1 for x in detail if x["state"] == "參考"), "bad_disks": n_disks})
    if not st["with_data"]:
        return _row("nodata", None, f"群組「{g['path']}」{st['scope']} 台都沒有新的磁碟值", detail, src["collected_at"], LP_SRC, src["batch"], stats=st)
    value = f"{st['bad']} 台達 {alert:g}%（{n_disks} 顆）；參考 {st['ref']} 台（{lst:g}～{alert:g}%）（{_lp_tail(st)}）"
    reason = f"有 {st['no_data']} 台沒有新的磁碟值，要人看一眼" if st["no_data"] else None
    pn = _lp_partial(g, ("disk",))
    if pn:
        reason = f"{reason}；{pn}" if reason else pn
    return _row("bad" if st["bad"] else "ok", value, reason, detail, src["collected_at"], LP_SRC, src["batch"], fresh=not st["no_data"] and not pn, stats=st)


def rule_lp_poll(conn, cfg: dict, src: dict, cache: dict) -> dict:
    """1-7 輪詢未回報：dc_group_poll 每個群組（任一個對不上／範圍未確認＝整列停判）；三份報表的「上次輪循」任一超過 poll_min 分鐘＝列出。
    沒有任何指標的設備＝無資料（沒有指標≠沒輪詢，不算逾時也不算正常）。"""
    import wug_lastpoll as lp
    lim = int(cfg["poll_min"]) * 60
    devs: dict = {}
    ages: dict = {}
    paths, partial = [], []
    for want in cfg["dc_group_poll"]:
        g, why = _lp_group(conn, cfg, src, want)
        if why:
            return _lp_nodata(src, why)
        paths.append(g["path"])
        pn = _lp_partial(g, ("cpu", "mem", "disk"))
        if pn:
            partial.append(f"{g['path']}：{pn}")
        rows = {k: lp.values(conn, src["batch"], g["path"], k) for k in ("cpu", "mem", "disk")}
        for did, d in _lp_universe(conn, src, g["path"], [v for k in rows for v in rows[k]]).items():
            e = devs.setdefault(did, {"ip": d["ip"], "name": d["name"], "src": []})
            e["ip"] = e["ip"] or d["ip"]
            if g["path"] not in e["src"]:
                e["src"].append(g["path"])
        for k, vs in rows.items():
            for v in vs:
                if v["age_s"] is not None:
                    a = ages.setdefault(v["device_id"], {})
                    a[k] = max(a.get(k, -1), _eff(v, src))
    per_metric = {"cpu": 0, "mem": 0, "disk": 0}
    detail = []
    for did, d in devs.items():
        a = ages.get(did) or {}
        late = {k: x for k, x in a.items() if x > lim}
        for k in late:
            per_metric[k] += 1
        if not a:
            state, what = "無資料", "沒有輪詢時間（三份報表都沒有這台，或 WhatsUp 沒給輪詢秒數）"
        elif late:
            state, what = "異常", "、".join(f"{METRIC_ZH[k]} {x // 60} 分鐘前" for k, x in sorted(late.items()))
        else:
            state, what = "正常", f"最久 {max(a.values()) // 60} 分鐘前"
        detail.append({**_lp_who(conn, cache, d["ip"], d["name"]), "device_id": did, "state": state, "what": what, "since": None, "src": "、".join(d["src"])})
    st = _lp_counts(_lp_finish(detail), len(devs), {"groups": paths, "poll_min": int(cfg["poll_min"]), "per_metric": per_metric})
    if not st["with_data"]:
        return _row("nodata", None, f"群組 {'、'.join(paths)} 共 {st['scope']} 台都沒有輪詢時間", detail, src["collected_at"], LP_SRC, src["batch"], stats=st)
    value = (f"{st['bad']} 台超過 {cfg['poll_min']} 分鐘沒輪詢（CPU {per_metric['cpu']}、記憶體 {per_metric['mem']}、磁碟 {per_metric['disk']}；"
             f"{_lp_tail(st)}）")
    if st["bad"]:
        return _row("bad", value, None, detail, src["collected_at"], LP_SRC, src["batch"], True, stats=st)
    if partial:
        # 報表只讀到部分：沒讀到的設備輪詢時間不明，不能判正常
        return _row("ref", value, "；".join(partial)[:400], detail, src["collected_at"], LP_SRC, src["batch"], stats=st)
    if st["no_data"]:
        # 審查 P1：成員在但沒有輪詢時間的設備，不能讓整列變正常；列為參考、一定要人看（明細列出這幾台）
        return _row("ref", value, f"有 {st['no_data']} 台沒有輪詢時間（看明細「無資料」），不能判正常，要人確認", detail, src["collected_at"], LP_SRC,
                    src["batch"], stats=st)
    return _row("ok", value, None, detail, src["collected_at"], LP_SRC, src["batch"], True, stats=st)


METRIC_ZH = {"cpu": "CPU", "mem": "記憶體", "disk": "磁碟"}


def rule_lp_swap(conn, cfg: dict, src: dict, now: datetime) -> dict:
    """1-5 SWAP：WhatsUp 的 Linux_SWAP 是「自訂效能工具」，REST 沒有報表 → 維持退路（納管健康檢查快照，_swap_from_health）。
    範圍用 dc_group_swap 群組成員的 IP（對不上／範圍未確認就退回 1-xServer IP 清單，並寫清楚）。"""
    import wug_lastpoll as lp
    th = float(cfg["swap"])
    g, why = _lp_group(conn, cfg, src, cfg["dc_group_swap"])
    ips = sorted({m["ip"] for m in lp.members_of(conn, src["b"], g["path"]) if m["ip"]}) if g else []
    note = f"範圍＝群組「{g['path']}」{len(ips)} 個 IP" if ips else f"範圍＝1-xServer IP 清單（群組不能用：{why or '成員沒有 IP'}）"
    scope = ips or cfg["xserver_ips"]
    r = _swap_from_health(conn, {**cfg, "xserver_ips": scope}, th, {"at": now, "collected_at": src.get("collected_at"), "batch": src.get("batch")})
    r["reason"] = f"WhatsUp Linux_SWAP 沒有 API（自訂效能工具），改用納管健康檢查；{note}。" + (r["reason"] or "")
    return r


# ---------------------------------------------------------------- 來源：VMware（vROps，2.064.0；正本 2-1～2-4）
def vm_source(conn, cfg: dict, now: datetime) -> dict:
    """最新且完整的 VMware 批次，且 ≤ vm_fresh_min 分鐘；不然整段無資料（後面那批為什麼不完整也寫）。只讀表，不連 vROps。"""
    import aria_dailycheck as ad
    b, last = ad.latest_complete(conn), ad.latest_any(conn)
    later = (f"；之後那批（{last['collected_at']}）不完整：{'；'.join(last['problems'][:2])}" if last and (b is None or last["batch_id"] != b["batch_id"])
             and last["problems"] else "")
    if b is None:
        return {"ok": False, "reason": "從沒收到完整的 VMware（vROps）批次" + later, "batch": last and last["batch_id"], "collected_at": last and last["collected_at"]}
    at = datetime.strptime(b["collected_at"], FMT)
    age = (now - at).total_seconds() / 60
    out = {"batch": b["batch_id"], "collected_at": b["collected_at"], "at": at, "b": b}
    if age > int(cfg["vm_fresh_min"]):
        return {**out, "ok": False, "reason": f"VMware 資料太舊（最新完整一批 {b['collected_at']}，{age:.0f} 分鐘前，門檻 {cfg['vm_fresh_min']} 分鐘）" + later}
    return {**out, "ok": True, "reason": None}


def ds_local(ds: dict, patterns: list[str]) -> tuple[str, str]:
    """本機碟判斷（GPT 16＋審查 P2-5）：類型 VMFS、只掛 1 台 ESXi，而且（名稱規則命中，或 vROps 本機屬性＝true）才排除；
    掛 2 台以上或 NFS／vSAN／vVol＝共用；其餘都是判不出（照樣檢查門檻、列出不排除）。回 (local|shared|unknown, 說明)。"""
    t = (ds.get("ds_type") or "").strip().upper()
    h = ds.get("hosts")
    hit = next((p for p in patterns if re.search(p, ds.get("name") or "", re.I)), None)
    loc = ds.get("local")
    if t == "VMFS" and h == 1 and (hit or loc in (1, True)):
        return "local", "VMFS、只掛 1 台 ESXi、" + (f"名稱像本機碟（{hit}）" if hit else "vROps 標本機")
    if (isinstance(h, int) and h >= 2) or t in ("NFS", "NFS41", "VSAN", "VVOL"):
        return "shared", ""
    why = f"類型 {t or '不知道'}、掛載 {h if h is not None else '不知道'} 台"
    if t == "VMFS" and h == 1:
        why += "，但名稱不像本機碟、vROps 也沒標本機"
    elif hit:
        why += f"；名稱像本機碟（{hit}）但掛載數或類型不足以確認"
    return "unknown", why + "；照樣檢查門檻、列出不排除"


def _mute_for(a: dict, vc_name: str, mutes: list[dict], today: date) -> tuple[dict | None, dict | None]:
    """清單靜音（審查 P3）：警示名稱相同（不分大小寫），資源 ID 有填就要相同（空＝這個名稱的全部）。回 (有效的那筆, 已逾期的那筆)。"""
    live = dead = None
    for m in mutes or []:
        if str(m.get("name") or m.get("key") or "").strip().lower() != (a.get("name") or "").strip().lower():
            continue
        if m.get("res_id") and str(m["res_id"]).strip() != (a.get("res_id") or ""):
            continue
        if m.get("vcenter") and m["vcenter"] != vc_name:        # 舊格式（有 vcenter 欄位）照舊限定那一座
            continue
        if str(m.get("expires") or "") > today.isoformat():
            live = live or m
        else:
            dead = dead or m
    return live, dead


def _ua_why(vcid, adp_vc: dict) -> str:
    if vcid and vcid in adp_vc:
        return f"（vROps 收集器有「{adp_vc[vcid]}」、設定沒有這座 vCenter）"
    return "（資源查不到或不屬於任何已知 vCenter）"


def _vm_nodata(vsrc: dict, why: str, stats: dict | None = None) -> dict:
    return _row("nodata", None, why, source=VM_SRC, data_at=vsrc.get("collected_at"), batch=vsrc.get("batch"), stats=stats)


def rule_vmware(conn, cfg: dict, vsrc: dict, vc: dict, today: date) -> dict:
    """一座 vCenter 一列（正本 2-1～2-4），永遠要人勾（ref）；人再看 Error Log／Mail。抓不到＝無資料＋原因，不得當正常。"""
    import aria_dailycheck as ad
    if not vsrc["ok"]:
        return _vm_nodata(vsrc, vsrc["reason"])
    if not vc.get("vcid"):
        return _vm_nodata(vsrc, f"「{vc['name']}」沒有設定 vCenter 識別（vcid），對不到 vROps 的資料（設定頁可從「看到的 vCenter」抄）")
    if not vc.get("expected"):
        return _vm_nodata(vsrc, f"「{vc['name']}」的應有 ESXi 清單沒有設定（應有清單是母體，沒有就不能判缺／多）")
    bid, b_at = vsrc["batch"], vsrc["at"]
    # 收集器（adapter）：設定的 ID 優先，否則 vcid 一樣的那一個；要唯一（GPT 7），而且最後收集不能太舊（GPT 4、27）
    ads = ad.adapters(conn, bid)
    hit = [a for a in ads if a["adapter_id"] == vc["adapter_id"]] if vc.get("adapter_id") else [a for a in ads if a["vcid"] == vc["vcid"]]
    if len(hit) != 1:
        return _vm_nodata(vsrc, f"「{vc['name']}」的 vROps 收集器對不上（找到 {len(hit)} 個），停判；設定頁填收集器 ID")
    adp = hit[0]
    if vc.get("adapter_id") and adp["vcid"] and adp["vcid"] != vc["vcid"]:
        return _vm_nodata(vsrc, f"「{vc['name']}」設定的收集器 {vc['adapter_id']} 屬於另一座 vCenter（vcid {adp['vcid']}，設定的是 {vc['vcid']}），停判")
    lc = _parse(adp["last_collected"])
    if lc is None or (b_at - lc) > timedelta(minutes=int(cfg["vm_adapter_stale_min"])):
        return _vm_nodata(vsrc, f"「{vc['name']}」的 vROps 收集器（{adp['name'] or adp['adapter_id']}）停收或資料過期：最後收集 {adp['last_collected'] or '沒有'}"
                                f"（批次 {vsrc['collected_at']}，門檻 {cfg['vm_adapter_stale_min']} 分鐘）")
    hs, dss, als = ad.hosts(conn, bid, vc["vcid"]), ad.datastores(conn, bid, vc["vcid"]), ad.alerts(conn, bid, vc["vcid"])
    if not hs:
        return _vm_nodata(vsrc, f"「{vc['name']}」這批 ESXi 0 台（不當作沒有問題）")
    if not dss:
        return _vm_nodata(vsrc, f"「{vc['name']}」這批資料存放區 0 個（不當作沒有問題）")
    rec_lim = timedelta(minutes=int(cfg["vm_record_stale_min"]))

    def old(t) -> bool:
        x = _parse(t)
        return x is None or (b_at - x) > rec_lim

    detail: list[dict] = []
    # ---- ESXi：應有清單（母體）vs 看到（vROps 資源 ID 去重）
    exp = set(vc["expected"])
    st = {"vm": True, "vcenter": vc["name"], "vcid": vc["vcid"], "adapter": adp["name"] or adp["adapter_id"], "adapter_last": adp["last_collected"],
          "expected": len(exp), "seen": len({h["res_id"] for h in hs}), "connected": 0, "maint": 0, "abnormal": 0, "stale_hosts": 0}
    seen_names: set = set()
    for h in hs:
        nm = norm_host(h["name"])
        seen_names.add(nm)
        conn_s, mt = (h["conn"] or "").lower(), (h["maint"] or "").lower()
        if conn_s == "notresponding":
            state, what = "異常", f"連線狀態 {h['conn']}" + ("（維護模式中仍不回應）" if mt in ("inmaintenance", "maintenance", "true") else "")
            st["abnormal"] += 1
        elif mt in ("inmaintenance", "maintenance", "true"):
            state, what = "維護", "維護模式"
            st["maint"] += 1
        elif conn_s and conn_s != "connected":
            state, what = "異常", f"連線狀態 {h['conn']}"
            st["abnormal"] += 1
        elif not conn_s or old(h["collected_at"]):
            state, what = "舊值", f"連線狀態 {h['conn'] or '沒有'}；採集時間 {h['collected_at'] or '沒有'}（不算數）"
            st["stale_hosts"] += 1
        else:
            state, what = "正常", "已連線"
            st["connected"] += 1
        detail.append({"kind": "ESXi", "name": h["name"], "ip": None, "serial": None, "state": state, "presence": "應有" if nm in exp else "多",
                       "what": what + ("" if nm in exp else "；不在應有清單（多出來）"), "since": h["collected_at"], "res_id": h["res_id"]})
    missing = sorted(exp - seen_names)
    for nm in missing:
        detail.append({"kind": "ESXi", "name": nm, "ip": None, "serial": None, "state": "缺", "presence": "缺", "what": "應有清單有、vROps 沒看到",
                       "since": None, "res_id": None})
    st["missing"], st["extra"], st["seen_names"] = len(missing), len(seen_names - exp), len(seen_names)
    # ---- 資料存放區：資源 ID 去重（同一個 datastore 掛在多台只算一次）、排除本機碟、剩餘門檻
    lr = lun_rule(cfg)
    th, unit, op, th_gb = lr["threshold"], lr["unit"], lr["op"], lr["threshold_gb"]
    pats = cfg.get("vm_local_patterns") or []
    ds_st = {"低於門檻": 0, "正常": 0, "本機碟（排除）": 0, "無剩餘值": 0, "舊值": 0}
    unk_local, min_free = 0, None
    for d in {x["res_id"]: x for x in dss}.values():
        kind, why = ds_local(d, pats)
        free = d["free_gb"]
        if kind == "local":
            state, what = "本機碟（排除）", why
        elif free is None:
            state, what = "無剩餘值", "vROps 沒有剩餘空間的值（不算數）"
        elif old(d["collected_at"]):
            state, what = "舊值", f"剩餘 {free:g} GB；採集時間 {d['collected_at'] or '沒有'}（不算數）"
        else:
            low = lun_low(free, lr)
            state = "低於門檻" if low else "正常"
            what = f"剩餘 {free:g} GB" + (f"／共 {d['cap_gb']:g} GB" if d["cap_gb"] is not None else "") + (f"（{op} {th:g} {unit}）" if low else "")
            min_free = free if min_free is None else min(min_free, free)
        if kind == "unknown" and state != "本機碟（排除）":
            unk_local += 1
            what += f"；判不出是不是本機碟：{why}"
        ds_st[state] += 1
        detail.append({"kind": "資料存放區", "name": d["name"], "ip": None, "serial": None, "state": state, "presence": d["ds_type"] or "",
                       "what": what, "since": d["collected_at"], "res_id": d["res_id"], "free_gb": free})
    st.update({"ds_total": sum(ds_st.values()), "ds_low": ds_st["低於門檻"], "ds_ok": ds_st["正常"], "ds_local": ds_st["本機碟（排除）"],
               "ds_nofree": ds_st["無剩餘值"], "ds_stale": ds_st["舊值"], "ds_unknown_local": unk_local, "ds_dup": len(dss) - len({x["res_id"] for x in dss}),
               "ds_min_free_gb": min_free, "threshold": th, "threshold_gb": th_gb, "unit": unit, "op": op})
    # ---- 警示：等級清單、狀態分列（vROps 靜音／已認領／清單靜音／未解），都列出不排除
    levels = set(cfg.get("vm_alert_levels") or [])
    al_st = {"未解": 0, "vROps 靜音": 0, "清單靜音": 0, "已認領": 0}
    for a in als:
        if a["level"] not in levels or a["status"] == "CANCELED":
            continue
        live, dead = _mute_for(a, vc["name"], cfg.get("vm_alert_mutes") or [], today)
        base = f"{a['level']}・{a['res_kind'] or ''} {a['res_name'] or a['res_id'] or ''}".strip()
        if a["control"] == "SUPPRESSED":
            state, what = "vROps 靜音", base
        elif live:
            state, what = "清單靜音", f"{base}；靜音到 {live['expires']}（{live['reason']}，核准：{live['approver']}）"
        elif a["control"] == "ASSIGNED" or a["owner"]:
            state, what = "已認領", f"{base}；認領：{a['owner'] or '—'}"
        else:
            state, what = "未解", base + (f"；靜音已於 {dead['expires']} 到期，自動恢復" if dead else "")
        al_st[state] += 1
        detail.append({"kind": "警示", "name": a["name"], "ip": None, "serial": None, "state": state, "presence": a["level"], "what": what,
                       "since": a["start_at"], "res_id": a["res_id"], "alert_id": a["alert_id"]})
    st.update({"alerts_open": al_st["未解"], "alerts_suppressed": al_st["vROps 靜音"], "alerts_muted": al_st["清單靜音"], "alerts_owned": al_st["已認領"],
               "alerts_total": sum(al_st.values())})
    # ---- 無法歸屬 vCenter（審查 P1）：vcid 空或不是任何已知 vCenter 的警示／資料存放區，每一列 VC 都標出來、列明細，不可默默消失
    # 只有設定裡的 vCenter 算已知（審查二 P2）：收集器有、設定沒有的 vCenter，它的警示與資料存放區也算無法歸屬，不可安靜消失
    known = {x.get("vcid") for x in cfg.get("vm_vcenters") or [] if x.get("vcid")}
    adp_vc = {a["vcid"]: (a["name"] or a["adapter_id"]) for a in ads if a["vcid"]}
    ua = ad.unassigned(conn, bid, known)
    ua_al = [a for a in ua["alerts"] if a["level"] in levels and a["status"] != "CANCELED"]
    ua_ds = list({x["res_id"]: x for x in ua["datastores"]}.values())
    for a in ua_al:
        detail.append({"kind": "無法歸屬 vCenter", "name": a["name"], "ip": None, "serial": None, "state": "無法歸屬", "presence": f"警示・{a['level']}",
                       "what": f"{a['res_kind'] or '資源'} {a['res_name'] or a['res_id'] or ''}；vcid {a['vcid'] or '空'}" + _ua_why(a["vcid"], adp_vc),
                       "since": a["start_at"], "res_id": a["res_id"], "alert_id": a["alert_id"]})
    for d in ua_ds:
        detail.append({"kind": "無法歸屬 vCenter", "name": d["name"], "ip": None, "serial": None, "state": "無法歸屬", "presence": "資料存放區",
                       "what": (f"剩餘 {d['free_gb']:g} GB；" if d["free_gb"] is not None else "") + f"vcid {d['vcid'] or '空'}" + _ua_why(d["vcid"], adp_vc),
                       "since": d["collected_at"], "res_id": d["res_id"]})
    st.update({"unassigned_alerts": len(ua_al), "unassigned_ds": len(ua_ds)})
    order = {"缺": 0, "異常": 1, "低於門檻": 2, "未解": 3, "無法歸屬": 3, "舊值": 4, "無資料": 5, "無剩餘值": 5, "維護": 6, "已認領": 7, "清單靜音": 8, "vROps 靜音": 9,
             "本機碟（排除）": 10, "正常": 11}
    detail.sort(key=lambda x: (order.get(x["state"], 9), x["kind"], (x["name"] or "").lower()))
    value = vm_value_text(st)
    issues = [f"{lab} {st[k]}" for k, lab in (("abnormal", "ESXi 異常"), ("missing", "ESXi 缺"), ("extra", "ESXi 多"), ("stale_hosts", "ESXi 舊值"),
                                                ("ds_low", "資料存放區低於門檻"), ("ds_nofree", "資料存放區無剩餘值"), ("ds_stale", "資料存放區舊值"),
                                                ("ds_unknown_local", "判不出本機碟"), ("alerts_open", "未解警示"),
                                                ("unassigned_alerts", "無法歸屬 vCenter 的警示"), ("unassigned_ds", "無法歸屬 vCenter 的資料存放區")) if st[k]]
    st["has_issue"] = bool(issues)
    reason = ("有：" + "、".join(issues) if issues else "預填看起來沒有異常") + "；請看 Error Log／Mail 異常通知後勾「已檢視」再確認"
    return _row("ref", value, reason, detail, vsrc["collected_at"], VM_SRC, vsrc["batch"], False, stats=st)


def vm_value_text(st: dict) -> str:
    """列上的文字；數字全部來自 stats（verify_numbers 用同一個格式反查）。"""
    return (f"ESXi 應有 {st['expected']}／看到 {st['seen']}（連線 {st['connected']}、維護 {st['maint']}、異常 {st['abnormal']}、舊值 {st['stale_hosts']}；"
            f"缺 {st['missing']}、多 {st['extra']}）；資料存放區低於門檻 {st['ds_low']} 個（共 {st['ds_total']} 個；排除本機 {st['ds_local']}、"
            f"判不出本機 {st['ds_unknown_local']}、無剩餘值 {st['ds_nofree']}、舊值 {st['ds_stale']}）；未解警示 {st['alerts_open']} 筆"
            f"（vROps 靜音 {st['alerts_suppressed']}、清單靜音 {st['alerts_muted']}、已認領 {st['alerts_owned']}）"
            + (f"；無法歸屬 vCenter：警示 {st.get('unassigned_alerts', 0)} 筆、資料存放區 {st.get('unassigned_ds', 0)} 個"
               if st.get("unassigned_alerts") or st.get("unassigned_ds") else ""))


# ---------------------------------------------------------------- 產生快照
def build_rows(conn, cfg: dict, now: datetime) -> tuple[list[dict], dict]:
    items = load_items()["items"]
    today = now.date()
    lpmode = cfg.get("daily_check_perf_source", "lastpoll") != "w3"
    wsrc = wug_source(conn, cfg, now)
    psrc = perf_source(conn, cfg, now) if not lpmode else {"ok": False, "reason": "未使用（改讀 WhatsUp 上次輪循）"}
    lsrc = lp_source(conn, cfg, now) if lpmode else None
    vsrc = vm_source(conn, cfg, now) if cfg.get("vm_vcenters") else None
    cache: dict = {}
    rows = []
    for it in items:
        base = {"code": it["code"], "section": it["section"], "name": it["name"], "kind": it["kind"], "rule": it.get("rule"), "extra": it.get("extra")}
        r = it.get("rule")
        if it.get("per") == "vcenters" and vsrc is not None:
            # 2.064.0：每座 vCenter 一列「系統預填、人確認」（kind=ref；原本手填欄位保留當退路）
            for i, vc in enumerate(cfg["vm_vcenters"], 1):
                rows.append({**base, "kind": "ref", "code": f"{it['code']}-{i}", "name": f"{it['name']}：{vc['name']}", **rule_vmware(conn, cfg, vsrc, vc, today)})
        elif it["kind"] == "manual":
            base["rule"] = None
            if it.get("per") == "vcenters" and cfg["vcenters"]:
                for i, vc in enumerate(cfg["vcenters"], 1):
                    rows.append({**base, "code": f"{it['code']}-{i}", "name": f"{it['name']}：{vc}", **_row("manual", None)})
            else:
                rows.append({**base, **_row("manual", None)})
        elif r == "wug_down":
            rows.append({**base, **rule_wug(conn, "down", wsrc)})
        elif r == "wug_maint":
            rows.append({**base, **rule_wug(conn, "maint", wsrc)})
        elif lpmode and r in ("perf_cpu", "perf_mem", "disk", "poll"):
            fn = {"perf_cpu": lambda: rule_lp_pct(conn, cfg, lsrc, "cpu", cache), "perf_mem": lambda: rule_lp_pct(conn, cfg, lsrc, "mem", cache),
                  "disk": lambda: rule_lp_disk(conn, cfg, lsrc, today, cache), "poll": lambda: rule_lp_poll(conn, cfg, lsrc, cache)}[r]
            rows.append({**base, "kind": "auto", **fn()})
        elif lpmode and r == "swap":
            rows.append({**base, **rule_lp_swap(conn, cfg, lsrc, now)})
        elif r == "perf_cpu":
            rows.append({**base, **rule_perf(conn, "cpu_busy", cfg, psrc)})
        elif r == "perf_mem":
            rows.append({**base, **rule_perf(conn, "mem_used", cfg, psrc)})
        elif r == "disk":
            rows.append({**base, **rule_disk(conn, cfg, psrc, today)})
        elif r == "swap":
            rows.append({**base, **rule_swap(conn, cfg, psrc)})
        elif r == "poll":
            rows.append({**base, **rule_poll(conn, cfg, psrc)})
        elif r == "ad_time":
            rows.append({**base, **rule_ad_time(conn)})
        elif r == "banqiao":
            if not cfg["banqiao"]:
                rows.append({**base, **_row("nodata", None, "板橋交易所主機清單未設定")})
            for i, ent in enumerate(cfg["banqiao"], 1):
                rows.append({**base, "code": f"{it['code']}-{i}", "name": f"{it['section']}：{ent['name']}（{ent['ip']}）", **rule_banqiao(conn, ent, wsrc)})
        else:
            rows.append({**base, **_row("nodata", None, f"不認得的規則：{r}")})
    sources = {"whatsup_alert": {k: wsrc.get(k) for k in ("ok", "reason", "block")},
               "whatsup_perf": {k: psrc.get(k) for k in ("ok", "reason", "batch", "collected_at", "day")}}
    if lsrc is not None:
        sources["whatsup_lastpoll"] = {k: lsrc.get(k) for k in ("ok", "reason", "batch", "collected_at")}
    if vsrc is not None:
        sources["vmware"] = {k: vsrc.get(k) for k in ("ok", "reason", "batch", "collected_at")}
        if vsrc.get("b"):
            sources["vmware"].update({"file_sha": vsrc["b"].get("file_sha"), "raw_sha": vsrc["b"].get("raw_sha"), "rule_version": vsrc["b"].get("rule_version")})
    return rows, sources


def generate(conn, by: str | None, trigger: str = "manual", now: datetime | None = None, only_if_untouched: int | None = None) -> int | None:
    """only_if_untouched＝run_id：同一個交易裡再確認一次「當天最新的還是這一次、還沒送出、沒有任何一列被確認」，不是就不產生、回 None
    （審查 P2-4：vm_refresh 檢查完到寫入之間有人確認，不能把他確認的那次蓋成作廢）。"""
    now = now or now_local()
    cfg = effective(conn, settings(conn))
    rows, sources = build_rows(conn, cfg, now)
    ensure(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if only_if_untouched is not None:
            top = conn.execute("SELECT id, status FROM daily_check_run WHERE day = ? ORDER BY id DESC LIMIT 1", (now.date().isoformat(),)).fetchone()
            if (top is None or top["id"] != only_if_untouched or top["status"] != "open"
                    or conn.execute("SELECT 1 FROM daily_check_row WHERE run_id = ? AND result IS NOT NULL LIMIT 1", (only_if_untouched,)).fetchone()):
                conn.rollback()
                return None
        rid = conn.execute(
            "INSERT INTO daily_check_run (day, created_at, trigger, created_by, items_sha, settings_sha, sources_json, warnings_json) VALUES (?,?,?,?,?,?,?,?)",
            (now.date().isoformat(), _s(now), trigger, by, _sha(load_items()), _sha(cfg), json.dumps(sources, ensure_ascii=False, default=str),
             json.dumps(setting_warnings(cfg, now.date()), ensure_ascii=False))).lastrowid
        for i, r in enumerate(rows, 1):
            conn.execute("INSERT INTO daily_check_row (run_id, no, code, section, name, kind, rule, extra, verdict, value_text, reason, detail_json, "
                         "data_at, source, batch, fresh, stats_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (rid, i, r["code"], r["section"], r["name"], r["kind"], r["rule"], r["extra"], r["verdict"], r["value_text"], r["reason"],
                          json.dumps(r["detail"], ensure_ascii=False), r["data_at"], r["source"], r["batch"], 1 if r["fresh"] else 0,
                          json.dumps(r["stats"], ensure_ascii=False) if r.get("stats") is not None else None))
        _event(conn, rid, None, by, "generate", {"trigger": trigger, "rows": len(rows)}, now)
        # 同一天重新產生＝新版本；還沒送出的舊版標「已作廢」（GPT 24；只能送出最新那一次，submit 已守）
        for (old,) in conn.execute("SELECT id FROM daily_check_run WHERE day = ? AND id < ? AND status = 'open'", (now.date().isoformat(), rid)).fetchall():
            if not conn.execute("SELECT 1 FROM daily_check_event WHERE run_id = ? AND action = 'superseded'", (old,)).fetchone():
                _event(conn, old, None, by, "superseded", {"by_run": rid, "trigger": trigger}, now)
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    return rid


def _event(conn, run_id, row_id, by, action, detail, now=None) -> None:
    conn.execute("INSERT INTO daily_check_event (run_id, row_id, at, by, action, detail_json) VALUES (?,?,?,?,?,?)",
                 (run_id, row_id, _s(now or now_local()), by, action, json.dumps(detail, ensure_ascii=False, default=str)))


# ---------------------------------------------------------------- 讀取（不建表）
def _row_dict(r) -> dict:
    d = dict(r)
    d["detail"] = json.loads(d.pop("detail_json") or "[]")
    d["extra_vals"] = json.loads(d.pop("extra_json") or "{}")
    d["stats"] = json.loads(d.pop("stats_json", None) or "null")
    d["verdict_label"] = VERDICT.get(d["verdict"], d["verdict"])
    d["result_label"] = RESULT.get(d["result"] or "", "")
    return d


def latest_run_id(conn, day: str) -> int | None:
    if not _ready(conn):
        return None
    r = conn.execute("SELECT id FROM daily_check_run WHERE day = ? ORDER BY id DESC LIMIT 1", (day,)).fetchone()
    return r[0] if r else None


def run_view(conn, run_id: int) -> dict:
    if not _ready(conn):
        raise LookupError("還沒有任何每日檢查")
    r = conn.execute("SELECT * FROM daily_check_run WHERE id = ?", (run_id,)).fetchone()
    if not r:
        raise LookupError(f"找不到第 {run_id} 次檢查")
    rows = [_row_dict(x) for x in conn.execute("SELECT * FROM daily_check_row WHERE run_id = ? ORDER BY no", (run_id,))]
    run = dict(r)
    run["sources"] = json.loads(run.pop("sources_json") or "{}")
    run["warnings"] = json.loads(run.pop("warnings_json") or "[]")
    runs = [dict(x) for x in conn.execute("SELECT id, created_at, trigger, created_by, status, submitted_by, submitted_at "
                                          "FROM daily_check_run WHERE day = ? ORDER BY id DESC", (run["day"],))]
    top = runs[0]["id"] if runs else None
    for x in runs:
        x["superseded"] = x["status"] == "open" and x["id"] != top      # 作廢＝同一天後來又產生過、自己沒送出
    run["superseded"] = run["status"] == "open" and run["id"] != top
    return {"run": run, "rows": rows, "counts": counts_of(rows), "runs": runs}


def counts_of(rows: list[dict]) -> dict:
    """卡片：各判定筆數＋未確認＋確認為異常。加總＝下鑽由 verify_numbers 釘住。"""
    out = {k: 0 for k in VERDICT}
    for r in rows:
        out[r["verdict"]] = out.get(r["verdict"], 0) + 1
    out["unconfirmed"] = sum(1 for r in rows if not r["result"])
    out["result_bad"] = sum(1 for r in rows if r["result"] == "bad")
    out["result_ok"] = sum(1 for r in rows if r["result"] == "ok")
    out["total"] = len(rows)
    return out


def filter_rows(rows: list[dict], key: str) -> list[dict]:
    if key in VERDICT:
        return [r for r in rows if r["verdict"] == key]
    if key == "unconfirmed":
        return [r for r in rows if not r["result"]]
    if key == "result_bad":
        return [r for r in rows if r["result"] == "bad"]
    if key == "result_ok":
        return [r for r in rows if r["result"] == "ok"]
    return rows


def events(conn, run_id: int) -> list[dict]:
    if not _ready(conn):
        return []
    return [{**dict(r), "detail": json.loads(r["detail_json"] or "{}")} for r in
            conn.execute("SELECT * FROM daily_check_event WHERE run_id = ? ORDER BY id", (run_id,))]


# ---------------------------------------------------------------- 確認、送出、更正
def _run_open(conn, run_id) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM daily_check_run WHERE id = ?", (run_id,)).fetchone()
    if not r:
        raise LookupError(f"找不到第 {run_id} 次檢查")
    return r


def confirm(conn, row_ids: list[int], result: str, note: str | None, by: str, extra: dict | None = None) -> dict:
    """逐列確認（送出前）。異常要寫說明；VMware 列可帶 extra（ESXi 台數、LUN 最小剩餘 GB、警示）。"""
    if result not in RESULT:
        raise ValueError("確認結果只能是 正常／異常")
    note = (note or "").strip()
    if result == "bad" and not note:
        raise ValueError("異常要寫說明")
    if not row_ids:
        raise ValueError("沒有選任何一列")
    if not _ready(conn):
        raise LookupError("還沒有任何每日檢查")
    now = now_local()
    conn.execute("BEGIN IMMEDIATE")
    try:
        done = 0
        for rid in dict.fromkeys(int(x) for x in row_ids):
            r = conn.execute("SELECT * FROM daily_check_row WHERE id = ?", (rid,)).fetchone()
            if not r:
                raise LookupError(f"找不到第 {rid} 列")
            if _run_open(conn, r["run_id"])["status"] != "open":
                raise ValueError("這次檢查已送出，只能用「更正」")
            if r["extra"] == "vmware" and r["rule"] == "vmware":
                ex = json.dumps(vm_prefill_extra(r, extra, result, by, now, note), ensure_ascii=False)
            else:
                ex = json.dumps(vmware_extra(extra), ensure_ascii=False) if r["extra"] == "vmware" else (r["extra_json"] or None)
            conn.execute("UPDATE daily_check_row SET result = ?, note = ?, extra_json = ?, confirmed_by = ?, confirmed_at = ? WHERE id = ?",
                         (result, note or None, ex, by, _s(now), rid))
            _event(conn, r["run_id"], rid, by, "confirm", {"from": r["result"], "to": result, "note": note, "extra": extra}, now)
            done += 1
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {"updated": done}


def vmware_extra(extra: dict | None) -> dict:
    """VMware 人工列的選填欄位：ESXi 台數（整數）、LUN 最小剩餘 GB（數字）、有警示（是／否）；勾了有警示就一定要寫說明。"""
    e = dict(extra or {})
    out: dict = {}
    n = str(e.get("esxi_count") if e.get("esxi_count") is not None else "").strip()
    if n:
        if not n.isdigit():
            raise ValueError("ESXi 台數要是整數")
        out["esxi_count"] = int(n)
    g = str(e.get("lun_free_gb") if e.get("lun_free_gb") is not None else "").strip()
    if g:
        try:
            out["lun_free_gb"] = float(g)
        except ValueError:
            raise ValueError("LUN 最小剩餘要是數字（GB）") from None
        if out["lun_free_gb"] < 0:
            raise ValueError("LUN 最小剩餘不能是負的")
    if e.get("has_alert"):
        desc = str(e.get("alert_note") or "").strip()
        if not desc:
            raise ValueError("勾了「有警示」要寫警示說明")
        out["has_alert"] = True
        out["alert_note"] = desc
    elif e.get("has_alert") is False:
        out["has_alert"] = False
    return out


VM_LABEL = {"esxi_count": "ESXi 台數", "lun_free_gb": "LUN 最小剩餘 GB", "has_alert": "有警示", "alert_note": "警示說明",
            "reviewed": "已檢視 Error Log／Mail", "ticket": "異常單號", "overrides": "人工覆寫"}


def vm_prefill_extra(r, extra: dict | None, result: str, by: str, now: datetime, note: str | None = None) -> dict:
    """VC 預填列的人工確認（GPT 21～23）：一定要勾「已檢視」（Error Log／Mail 系統看不到）；結果另外選；異常單號選填。
    預填有異常卻確認為正常，一定要寫說明（審查 P3）。
    手填欄位（ESXi 台數、LUN 最小剩餘、有警示）跟系統預填不同＝覆寫：要原因，存原值／新值／原因／人／時間。"""
    e = dict(extra or {})
    if e.get("reviewed") is not True:
        raise ValueError("VMware 列要先勾「已檢視 Error Log／Mail」才能確認")
    pre = json.loads(r["stats_json"] or "null") or {}
    if result == "ok" and pre.get("has_issue") and not (note or "").strip():
        raise ValueError("預填有異常（看系統說明），確認為正常要寫說明（例如已知維護、已通報）")
    out = vmware_extra(e)
    out["reviewed"] = True
    t = str(e.get("ticket") or "").strip()
    if len(t) > 60:
        raise ValueError("異常單號最多 60 字")
    if t:
        out["ticket"] = t
    st = json.loads(r["stats_json"] or "null") or {}
    if not st.get("vm"):
        return out                                   # 無資料列：沒有預填值，手填的就是唯一的值（不算覆寫）
    sysv = {"esxi_count": st.get("seen"), "lun_free_gb": st.get("ds_min_free_gb"), "has_alert": bool(st.get("alerts_open"))}
    why = str(e.get("override_reason") or "").strip()
    ovs = []
    for k, orig in sysv.items():
        if k not in out:
            continue
        new = out[k]
        same = (orig is not None and float(new) == float(orig)) if k != "has_alert" else (new == orig)
        if not same:
            ovs.append({"field": k, "label": VM_LABEL[k], "original": orig, "new": new, "reason": why, "by": by, "at": _s(now)})
    if ovs:
        if not why:
            raise ValueError("手填的值跟系統預填不同（" + "、".join(o["label"] for o in ovs) + "），要寫覆寫原因")
        out["overrides"] = ovs
    return out


def vm_refresh(conn, now: datetime) -> str:
    """VMware 補抓（06:20／07:30）匯入新的完整批次後：今天的最新快照還沒送出、還沒有人確認任何一列、用的不是這一批 → 重新產生（新版本，
    舊版標作廢）。有人確認過就不動（畫面提示「有較新的 VMware 資料」，人決定要不要重新產生）。排程沒開、非工作日、過了通知截止不動。"""
    import aria_dailycheck as ad
    cfg = settings(conn)
    if not cfg["enabled"] or not cfg.get("vm_vcenters") or not is_working_day(cfg, now.date()):
        return "skip"
    day = now.date().isoformat()
    if now >= _at(now.date(), cfg["notify_stop_time"]):
        return "after_stop"
    rid = latest_run_id(conn, day)
    b = ad.latest_complete(conn)
    if rid is None or b is None:
        return "no_run"
    run = conn.execute("SELECT status, sources_json FROM daily_check_run WHERE id = ?", (rid,)).fetchone()
    if run["status"] != "open":
        return "submitted"
    used = (json.loads(run["sources_json"] or "{}").get("vmware") or {}).get("batch")
    if used == b["batch_id"]:
        return "same"
    if conn.execute("SELECT 1 FROM daily_check_row WHERE run_id = ? AND result IS NOT NULL LIMIT 1", (rid,)).fetchone():
        return "confirmed"
    if generate(conn, "（排程：VMware 補抓）", "vm_refresh", now, only_if_untouched=rid) is None:
        return "confirmed"
    return "regenerated"


def vm_newer(conn, run: dict | None) -> dict | None:
    """畫面提示：有比這次快照用的更新的完整 VMware 批次。"""
    import aria_dailycheck as ad
    if not run or run.get("status") != "open":
        return None
    v = (run.get("sources") or {}).get("vmware")
    if not v:
        return None
    b = ad.latest_complete(conn)
    if b and b["batch_id"] != v.get("batch") and b["collected_at"] > (v.get("collected_at") or ""):
        return {"batch": b["batch_id"], "collected_at": b["collected_at"]}
    return None


def trial_day(conn, today: date) -> int:
    """試辦第幾個工作天：從第一次產生快照那天到今天（含）的週一～週五天數；沒產生過＝0。"""
    if not _ready(conn):
        return 0
    r = conn.execute("SELECT MIN(day) FROM daily_check_run").fetchone()
    if not r or not r[0]:
        return 0
    d0 = date.fromisoformat(r[0])
    return sum(1 for i in range((today - d0).days + 1) if (d0 + timedelta(days=i)).weekday() < 5)


def confirm_fresh_ok(conn, run_id: int, by: str) -> dict:
    """批次確認：只限「系統判正常且資料新鮮」還沒確認的列（GPT 8）；事件記範圍、筆數、來源時間。"""
    if not _ready(conn):
        raise LookupError("還沒有任何每日檢查")
    now = now_local()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if _run_open(conn, run_id)["status"] != "open":
            raise ValueError("這次檢查已送出")
        rows = conn.execute("SELECT id, code, data_at FROM daily_check_row WHERE run_id = ? AND verdict = 'ok' AND fresh = 1 AND result IS NULL",
                            (run_id,)).fetchall()
        for r in rows:
            conn.execute("UPDATE daily_check_row SET result = 'ok', confirmed_by = ?, confirmed_at = ? WHERE id = ?", (by, _s(now), r["id"]))
        _event(conn, run_id, None, by, "confirm_fresh_ok",
               {"scope": "系統判正常且資料新鮮、尚未確認", "count": len(rows), "rows": [r["code"] for r in rows],
                "data_at": sorted({r["data_at"] for r in rows if r["data_at"]})}, now)
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {"updated": len(rows)}


def submit(conn, run_id: int, by: str) -> dict:
    if not _ready(conn):
        raise LookupError("還沒有任何每日檢查")
    now = now_local()
    conn.execute("BEGIN IMMEDIATE")
    try:
        run = _run_open(conn, run_id)
        if run["status"] != "open":
            raise ValueError("這次檢查已送出")
        if latest_run_id(conn, run["day"]) != run_id:
            raise ValueError("這不是當天最新的一次快照，請送出最新的那一次（紀錄頁可看）")
        left = conn.execute("SELECT COUNT(*) FROM daily_check_row WHERE run_id = ? AND result IS NULL", (run_id,)).fetchone()[0]
        if left:
            raise ValueError(f"還有 {left} 列沒確認")
        conn.execute("UPDATE daily_check_run SET status = 'submitted', submitted_by = ?, submitted_at = ? WHERE id = ?", (by, _s(now), run_id))
        _event(conn, run_id, None, by, "submit", {}, now)
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    try:
        _notify(conn, run_id, "submitted", now)
    except Exception as exc:  # noqa: BLE001 - 送出已存檔；通知失敗只記 log，不讓這次送出變成錯誤
        import logging
        logging.getLogger("daily_check").error("送出後通知失敗：%s", type(exc).__name__)
        if conn.in_transaction:
            conn.rollback()
    return {"submitted": True}


def correct(conn, row_id: int, result: str, note: str | None, by: str, extra: dict | None = None) -> dict:
    """送出後更正一列（留事件，原值在事件裡）。VMware 預填列同確認：要勾已檢視、覆寫留痕（審查 P3）；原本的人工欄位記在事件裡。"""
    if result not in RESULT:
        raise ValueError("確認結果只能是 正常／異常")
    note = (note or "").strip()
    if not note:
        raise ValueError("更正要寫原因")
    if not _ready(conn):
        raise LookupError("還沒有任何每日檢查")
    now = now_local()
    conn.execute("BEGIN IMMEDIATE")
    try:
        r = conn.execute("SELECT * FROM daily_check_row WHERE id = ?", (row_id,)).fetchone()
        if not r:
            raise LookupError(f"找不到第 {row_id} 列")
        ex = r["extra_json"]
        if r["extra"] == "vmware" and r["rule"] == "vmware":
            ex = json.dumps(vm_prefill_extra(r, extra, result, by, now, note), ensure_ascii=False)
        elif r["extra"] == "vmware" and extra is not None:
            ex = json.dumps(vmware_extra(extra), ensure_ascii=False)
        conn.execute("UPDATE daily_check_row SET result = ?, note = ?, extra_json = ?, confirmed_by = ?, confirmed_at = ? WHERE id = ?",
                     (result, note, ex, by, _s(now), row_id))
        _event(conn, r["run_id"], row_id, by, "correct", {"from": r["result"], "from_note": r["note"], "to": result, "note": note,
                                                          "from_extra": json.loads(r["extra_json"] or "null"), "extra": extra}, now)
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {"updated": 1}


# ---------------------------------------------------------------- 通知（現有 teams_notify 佇列）與排程
MAIL_MAX_ATTEMPTS = 3            # 第一次＋失敗後最多再試 2 次（每次都記寄送紀錄）


def _notify(conn, run_id: int | None, kind: str, now: datetime, day: str | None = None) -> str:
    """排一則通知（Teams 佇列，一天一種一次）；升級另外寄信給設定的收件人。
    寄信前先把通知列寫成 mail_status='sending' 並 commit，寄完再改 sent／failed；failed 的下一輪（還在時段內）最多再試 2 次。"""
    import teams_notify as tn

    day = day or now.date().isoformat()
    ensure(conn)
    cfg = settings(conn)
    want_mail = kind == "escalate" and bool(cfg["escalate_to"])
    old = conn.execute("SELECT * FROM daily_check_notice WHERE day = ? AND kind = ?", (day, kind)).fetchone()
    if old is not None:
        if want_mail and old["mail_status"] == "failed" and (old["mail_attempts"] or 0) < MAIL_MAX_ATTEMPTS:
            return "retry:" + _mail_step(conn, day, kind, cfg, run_id, now)
        return "dup"
    title, facts, tcfg = _content(conn, run_id, kind, day, cfg)
    status = "recorded"
    try:
        tcfg = tn.load_settings(conn)
        on = tcfg["enabled"]
    except Exception:  # noqa: BLE001 - Teams 設定讀不到：只記錄（同 wug_alert 的做法），不讓送出失敗
        tcfg, on = {}, False
    if on:
        tn.ensure(conn)
        tn._enqueue(conn, f"dailycheck:{day}:{kind}", f"dailycheck_{kind}", "daily_check", "warning" if kind != "submitted" else "info",
                    title, tn._card(title, "Attention" if kind != "submitted" else "Good", facts, link=tn._link(tcfg, "/v2/dailycheck")), now)
        status = "queued"
    conn.execute("INSERT INTO daily_check_notice (day, kind, at, status, mail_status, mail_attempts) VALUES (?,?,?,?,?,0)",
                 (day, kind, _s(now), status, "sending" if want_mail else None))
    conn.commit()
    if want_mail:
        return status + "+mail:" + _mail_step(conn, day, kind, cfg, run_id, now)
    return status


def _content(conn, run_id, kind, day, cfg) -> tuple[str, list, dict]:
    title = {"submitted": "每日檢查已送出", "remind": "每日檢查還沒送出（提醒）", "escalate": "每日檢查還沒送出（升級）",
             "missing": "每日檢查今天沒有產生快照"}[kind] + f"：{day}"
    facts: list = []
    if run_id:
        v = run_view(conn, run_id)
        c = v["counts"]
        facts = [("異常（系統）", str(c["bad"])), ("無資料", str(c["nodata"])), ("前一日參考", str(c["ref"])), ("人工", str(c["manual"])),
                 ("未確認", str(c["unconfirmed"])), ("確認為異常", str(c["result_bad"]))]
        if v["run"]["submitted_by"]:
            facts.append(("送出", f"{v['run']['submitted_by']} {v['run']['submitted_at']}"))
        facts += [(f"{r['code']} {r['name']}", r["note"] or r["value_text"] or "") for r in v["rows"] if r["result"] == "bad"][:15]
    if kind == "escalate":
        facts.append(("升級收件人", "、".join(cfg["escalate_to"]) or "未設定（只送 Teams 頻道）"))
    return title, facts, {}


def _mail_step(conn, day, kind, cfg, run_id, now) -> str:
    import teams_notify as tn

    title, facts, _ = _content(conn, run_id, kind, day, cfg)
    try:
        tcfg = tn.load_settings(conn)
        link = tn._link(tcfg, "/v2/dailycheck")
    except Exception:  # noqa: BLE001 - 連結只是方便，讀不到設定就不放連結
        link = None
    res = _send_mail(conn, day, kind, cfg["escalate_to"], tn._card(title, "Attention", facts, link=link), now)
    conn.execute("UPDATE daily_check_notice SET mail_status = ?, mail_attempts = mail_attempts + 1 WHERE day = ? AND kind = ?", (res, day, kind))
    conn.commit()
    return res


def _send_mail(conn, day: str, kind: str, to: list[str], card: dict, now: datetime) -> str:
    """升級信：寄給設定的收件人（只在有設定時）。走 teams_notify.mail_transport＋同一份 SMTP 設定（vuln_notify 的寄信設定）；
    每一次寄送（成功或失敗）都記 daily_check_mail_log。SMTP 沒設定也記一筆失敗，不吞。"""
    import teams_notify as tn

    smtp = tn.smtp_settings(conn)
    miss = tn.smtp_missing(conn)
    if miss:
        code, err, phase = None, "寄信設定不完整：" + "、".join(miss), "refused"
    else:
        tr = MAIL_TRANSPORT or tn.mail_transport
        try:
            code, err, phase = tr(smtp, tn.MAIL_PREFIX + ",".join(to), json.dumps(card, ensure_ascii=False).encode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 寄信失敗不能讓排程掛掉；照實記進寄送紀錄
            code, err, phase = None, f"{type(exc).__name__}: {exc}", "error"
    ok = code == 200
    conn.execute("INSERT INTO daily_check_mail_log (day, kind, at, recipients, ok, http, error, phase) VALUES (?,?,?,?,?,?,?,?)",
                 (day, kind, _s(now), ",".join(to), 1 if ok else 0, code, err or None, phase or None))
    return "sent" if ok else "failed"


def mail_log(conn, limit: int = 100) -> list[dict]:
    if not _has(conn, "daily_check_mail_log"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM daily_check_mail_log ORDER BY id DESC LIMIT ?", (limit,))]


def today_status(conn, now: datetime | None = None) -> dict:
    """1.1 首頁那一行：今天的每日檢查狀態（未產生／待確認 N 列／已送出 HH:MM）。讀取不建表。"""
    now = now or now_local()
    day = now.date().isoformat()
    out = _today_status(conn, now, day)
    try:
        import wug_lastpoll as lp
        hb = lp.heartbeat_view(conn, now)
    except Exception as exc:  # noqa: BLE001 - 心跳只是附註；讀不到就照實寫「不知道」，不讓首頁那一行壞掉
        hb = {"down": True, "reason": f"上次輪循收集狀態不知道（心跳讀不到：{type(exc).__name__}）"}
    if hb.get("down"):
        out["lp_alert"] = hb["reason"]
    return out


def _today_status(conn, now: datetime, day: str) -> dict:
    rid = latest_run_id(conn, day)
    if rid is None:
        out = {"day": day, "state": "none", "text": "未產生"}
        # 2.060.19（S5）：今天還沒產生時附上一筆（最近一個有紀錄的日子），首頁灰字標「MM-DD 的資料，今日尚未產生」
        if _ready(conn):
            pr = conn.execute("SELECT day FROM daily_check_run WHERE day < ? ORDER BY day DESC, id DESC LIMIT 1", (day,)).fetchone()
            if pr:
                p = _day_status(conn, pr[0], latest_run_id(conn, pr[0]))
                out["prev"] = {**p, "note": f"{pr[0]} 的資料，今日尚未產生"}
        return out
    return _day_status(conn, day, rid)


def _day_status(conn, day: str, rid: int) -> dict:
    r = conn.execute("SELECT status, submitted_at FROM daily_check_run WHERE id = ?", (rid,)).fetchone()
    if r["status"] == "submitted":
        return {"day": day, "state": "submitted", "run_id": rid, "text": f"已送出 {(r['submitted_at'] or '')[11:16]}"}
    n = conn.execute("SELECT COUNT(*) FROM daily_check_row WHERE run_id = ? AND result IS NULL", (rid,)).fetchone()[0]
    bad = conn.execute("SELECT COUNT(*) FROM daily_check_row WHERE run_id = ? AND verdict = 'bad'", (rid,)).fetchone()[0]
    return {"day": day, "state": "open", "run_id": rid, "unconfirmed": n, "bad": bad,
            "text": f"待確認 {n} 列" + (f"・系統異常 {bad} 列" if bad else "")}


def _at(day: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m)


def is_working_day(cfg: dict, d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in set(cfg.get("holidays") or [])


def tick(conn, now: datetime | None = None) -> dict:
    """排程器每分鐘呼叫：設定沒開、週末、假日都不動。
    06:30 產生；產生時段過了還沒有快照→通知；提醒（07:45～08:15）、升級（08:15～09:30）只在各自時段內、只對當天、
    只在「當天沒有任何一次已送出」時發；快照在該時間點之後才產生的（晚到的人工產生）不馬上發。09:30 之後一律不再通知。
    只讀本機已匯入的資料、寫本機表、排 Teams 佇列與寄信（C 類：只通知，不連任何主機）。"""
    now = now or now_local()
    cfg = settings(conn)
    if not cfg["enabled"]:
        return {"status": "disabled"}
    d = now.date()
    if not is_working_day(cfg, d):
        return {"status": "non_working"}
    day = d.isoformat()
    out = {"status": "ok"}
    t_snap, t_rem, t_esc, t_stop = (_at(d, cfg[k]) for k in ("snapshot_time", "remind_time", "escalate_time", "notify_stop_time"))
    rid = latest_run_id(conn, day)
    if rid is None and t_snap <= now < t_snap + timedelta(minutes=15):
        rid = generate(conn, "（排程）", "schedule", now)
        out["generated"] = rid
    if now >= t_stop:
        return {**out, "status": "after_stop"}
    if rid is None:
        if now >= t_snap + timedelta(minutes=15):
            out["missing"] = _notify(conn, None, "missing", now, day)
        return out
    if conn.execute("SELECT 1 FROM daily_check_run WHERE day = ? AND status = 'submitted' LIMIT 1", (day,)).fetchone():
        return {**out, "status": "submitted"}
    created = _parse(conn.execute("SELECT created_at FROM daily_check_run WHERE id = ?", (rid,)).fetchone()[0]) or now
    first = _parse(conn.execute("SELECT MIN(created_at) FROM daily_check_run WHERE day = ?", (day,)).fetchone()[0]) or created
    if t_esc <= now < t_stop:
        if first <= t_esc:
            out["escalate"] = _notify(conn, rid, "escalate", now, day)
    elif t_rem <= now < t_esc:
        if first <= t_rem:
            out["remind"] = _notify(conn, rid, "remind", now, day)
    return out


def _xl_val(x) -> str:
    if x is True:
        return "是"
    if x is False:
        return "否"
    if isinstance(x, list):   # 人工覆寫：原值→新值（原因，人 時間）
        return "、".join(f"{o.get('label')} {o.get('original')}→{o.get('new')}（{o.get('reason')}，{o.get('by')} {str(o.get('at') or '')[11:16]}）" for o in x)
    return str(x)


# ---------------------------------------------------------------- Excel（格式同現行＋稽核工作表，GPT 10）
EXCEL_COLS = ["檢查時間", "項目", "檢查網址", "檢查內容", "異常訊息", "檢查人員"]


def export_xlsx(conn, run_id: int) -> tuple[io.BytesIO, str]:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    v = run_view(conn, run_id)
    cfg = settings(conn)
    run = v["run"]
    wb = Workbook()
    ws = wb.active
    ws.title = "每日檢查"
    ws.append(EXCEL_COLS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in v["rows"]:
        content = r["value_text"] or VERDICT[r["verdict"]]
        if r["extra_vals"]:
            content += "；" + "；".join(f"{VM_LABEL.get(k, k)}：{_xl_val(x)}" for k, x in r["extra_vals"].items() if x not in (None, ""))
        ws.append([(r["confirmed_at"] or "")[11:16], f"{r['code']} {r['name']}", cfg["urls"].get(r["code"], ""), content,
                   r["note"] if r["result"] == "bad" else "", r["confirmed_by"] or ""])
    au = wb.create_sheet("稽核")
    au.append(["項次", "項目", "系統判定", "數值", "原因", "資料時間", "來源", "來源批次", "確認結果", "說明", "確認人", "確認時間"])
    for r in v["rows"]:
        au.append([r["no"], f"{r['code']} {r['name']}", r["verdict_label"], r["value_text"] or "", r["reason"] or "", r["data_at"] or "",
                   r["source"] or "", r["batch"] or "", r["result_label"], r["note"] or "", r["confirmed_by"] or "", r["confirmed_at"] or ""])
    au.append([])
    au.append(["檢查日", run["day"], "產生", run["created_at"], run["trigger"], "規則版本", (run["items_sha"] or "")[:12],
               "設定版本", (run["settings_sha"] or "")[:12], "送出", run["submitted_by"] or "未送出", run["submitted_at"] or ""])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf, f"xServer每日檢查紀錄_{run['day']}_第{run_id}次.xlsx"
