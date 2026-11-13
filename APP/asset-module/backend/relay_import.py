"""中繼匯入（方案 A）：外部系統的資料先由一台能連到它的 Windows 管理主機（只讀腳本）收集成檔案，
放到共用資料夾；本系統只「讀檔案」匯入，從不直接連 WhatsUp／Unity／vCenter 這些正式環境。

## 為什麼走檔案（使用者 2026-11-02 決定）

正式環境的 WhatsUp、vCenter、儲存設備，系統主機連不到（防火牆政策上不開）。沿用 vCenter 的 RVTools 做法：
管理主機收集 → 輸出到共用資料夾 → 這裡只讀檔。檔案格式一開始就設計成「匯入後就是正式資料」，
日後系統主機能直連時，同一份資料表照用，不是暫時的測試格式。

## 批次契約（細節見 docs/中繼匯入檔案契約.md）

共用資料夾底下，每一批一個子資料夾，裡面有 manifest.json 與資料檔：

    <資料夾>/<批次名>/manifest.json
    <資料夾>/<批次名>/<資料檔>.json ...

manifest 欄位：source、schema_version、collector_version、collected_at、range{from,to}、count、files[{name,sha256,size}]，
可選 batch_id（預設＝資料夾名）、time_zone（資料檔時間字串沒帶時區時的時區，預設 UTC）。
收集端「先寫資料檔、最後才寫 manifest（先寫 .tmp 再改名）」，所以看得到 manifest 就代表這批寫完了。

## 守門（任何一項不過，整批拒收、不寫任何資料，原因記進匯入紀錄）

* manifest 欄位齊全、source 認得、schema_version 支援、count 等於檔案數。
* 每個檔：檔名單純（不得有路徑）、是一般檔案（不跟隨符號連結）、大小與 manifest 相符且不超過上限、sha256 相符。
* 任何檔（含 manifest）不得出現密碼／token／金鑰類欄位名稱或內容，發現就拒收。
* 冪等：同一個 source＋批次名只匯入一次；重複放同一批不會重複寫入。

## 排程與盤中靜默窗

tick 純讀本機檔案，但屬於「重運算」，使用者要求盤中不要有任何自動重運算，所以走 quiet_window 入口 relay_import_tick
（窗內不動並記延後，窗外下一輪補）。admin 的「立即匯入」是人為按鈕，窗內需二次確認（路由負責）。

## 鮮度

某個資料來源超過 N 天（預設 2 天）沒有新的成功批次，標「資料過期」；從沒收到過標「從未收到」。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import sqlite3
import stat
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from db import get_setting, set_setting

_log = logging.getLogger(__name__)

ENTRY = "relay_import_tick"                 # quiet_window.ENTRIES 的代號
#: 告警快照批次（資料夾名 wugalert_ 開頭）由 wug_alert.tick 每分鐘另外匯入（C 類例外，24 小時）；
#: 一般排程（15 分鐘、盤中延後）不碰它們，免得盤中每分鐘都記一筆「延後」。人工「立即匯入」照樣全部處理。
FAST_PREFIX = "wugalert_"
#: 2.062.0（S13）：WhatsUp「上次輪循」群組快照（wuglp_）同樣走快速通道，由 wug_lastpoll.tick 每分鐘匯入（C 類例外：資料小、只寫表不重算）。
LP_PREFIX = "wuglp_"
#: 2.064.0：VMware 每日檢查快照（ariadc_，跳板機 讀 vROps）同樣走快速通道，由 aria_dailycheck.tick 每分鐘匯入（C 類例外：資料小、只寫表不重算）。
ARIA_DC_PREFIX = "ariadc_"
FAST_PREFIXES = (FAST_PREFIX, LP_PREFIX, ARIA_DC_PREFIX)

# ---------------------------------------------------------------- 設定（app_settings）
ENABLED_KEY = "relay_import_enabled"        # 預設關
DIR_KEY = "relay_import_dir"
STALE_DAYS_KEY = "relay_import_stale_days"
INTERVAL_KEY = "relay_import_interval_min"  # 排程每隔幾分鐘看一次資料夾
MAX_FILE_MB_KEY = "relay_import_max_file_mb"
LAST_CHECK_KEY = "relay_import_last_check"  # 排程上次看資料夾的時間
LAST_SCAN_KEY = "relay_import_last_scan"    # 上次實際跑完一輪匯入的時間
LAST_RESULT_KEY = "relay_import_last_result"

#: 收集端整輪結果（manifest 的 run 欄：ips／ok／failed／notfound／final）。2.058.22：WhatsUp 收集端 token 30 分鐘過期後
#: 其餘全部 401，每天只進 ~73／4684 台卻沒人知道（不知道≠0）——最後一批帶 final=true 的整輪計數，存這裡給鮮度燈看。
RUN_SUMMARY_KEY = "relay_run_summary_"      # + source
RUN_FAIL_RATIO = 0.10                       # 失敗＋例外超過這個比例＝收集不完整

DEFAULT_STALE_DAYS = 2
DEFAULT_INTERVAL_MIN = 15
DEFAULT_MAX_FILE_MB = 16
STABLE_SECONDS = 30                         # manifest 至少靜置這麼久才處理（測試會改成 0）
MAX_BATCHES_PER_RUN = 20                    # 一輪最多處理幾批，其餘下一輪
MAX_FILES_PER_BATCH = 2000
MAX_JSON_NODES = 2_000_000                  # 單檔 JSON 節點數上限（防超大結構）
SUPPORTED_MANIFEST_KEYS = ("source", "schema_version", "collector_version", "collected_at", "range", "count", "files")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS relay_import_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL, batch_id TEXT NOT NULL,
        status TEXT NOT NULL,                       -- imported / rejected / failed
        reason_code TEXT, reason TEXT,
        schema_version TEXT, collector_version TEXT, collected_at TEXT, range_from TEXT, range_to TEXT, time_zone TEXT,
        files INTEGER, devices INTEGER, host_days INTEGER, points INTEGER, skipped_existing INTEGER, unmatched INTEGER, bad_values INTEGER,
        sig TEXT, imported_at TEXT NOT NULL, imported_by TEXT, trigger TEXT, folder TEXT,
        UNIQUE (source, batch_id))""",
    "CREATE INDEX IF NOT EXISTS idx_relay_batches_at ON relay_import_batches(imported_at)",
    """CREATE TABLE IF NOT EXISTS relay_import_unmatched (
        source TEXT NOT NULL, ident TEXT NOT NULL, name TEXT, reason TEXT, candidates INTEGER,
        last_batch TEXT, first_seen TEXT, last_seen TEXT,
        PRIMARY KEY (source, ident))""",
    """CREATE TABLE IF NOT EXISTS relay_disk_util (
        host TEXT NOT NULL, day TEXT NOT NULL, disk TEXT NOT NULL,
        interval_s INTEGER, samples INTEGER, data BLOB NOT NULL, batch_id TEXT, updated_at TEXT,
        PRIMARY KEY (host, day, disk))""",
    """CREATE TABLE IF NOT EXISTS relay_import_migrations (
        name TEXT PRIMARY KEY, done_at TEXT NOT NULL, detail TEXT)""",
]


def ensure(conn: sqlite3.Connection) -> None:
    """建表。**不**跑刻度遷移：ensure 會被每分鐘的告警快照匯入等路徑呼叫（不受盤中靜默窗管），遷移只在 db.init_db（啟動）跑一次。"""
    for s in SCHEMA:
        conn.execute(s)
    import storage_relay
    storage_relay.ensure(conn)          # Unity 匯入的陣列／Pool／LUN／主機表（UnityAdapter 寫入）
    conn.commit()


# ---------------------------------------------------------------- WhatsUp 百分比刻度（2.047.6）
# WhatsUp Gold REST 報表的 avgPercent／maxPercent／minPercent 是 0～1 的小數（Swagger 寫 "decimal number"；
# 2026-11-04 公司正式區實測：磁碟 maxPercent 0.96＝96%、1.0＝100%）。2.047.5 以前原樣入庫，全站看到的 WhatsUp
# CPU／記憶體／磁碟都小了 100 倍（磁碟告警永遠不會響）。現在一律在匯入時換成 0–100，只在這一個地方換。
# 刻度只看 manifest 的 percent_scale（"fraction" 預設／"percent"），不逐值猜。
WUG_SCALE_KEY = "percent_scale"
WUG_SCALES = {"fraction": 100.0, "percent": 1.0}          # 刻度 → 乘數
WUG_DEFAULT_SCALE = "fraction"
SCALE_MARK = "pct_scale"                                   # 已是 0–100 的列，資料裡帶 pct_scale="percent"（新寫入與遷移後都有）
_EPS = 1e-6
MIGRATION_WUG_PERCENT = "wug_percent_scale_v1"
_MIGRATE_PAGE = 500                                        # 遷移逐頁讀（依 rowid），不整表載入記憶體


def wug_factor(manifest: dict) -> float:
    """manifest → 乘數。沒寫＝WhatsUp 原生小數（×100）；寫了不認得的值整批拒收（不猜）。"""
    s = manifest.get(WUG_SCALE_KEY, WUG_DEFAULT_SCALE)
    if s not in WUG_SCALES:
        raise RelayReject("manifest_invalid", f"{WUG_SCALE_KEY} 只能是 fraction（WhatsUp 原生 0～1）或 percent（0～100）")
    return WUG_SCALES[s]


def to_percent(v: Any, factor: float) -> float | None:
    """單一值 → 0–100。缺值、不是數字、負數、超出該刻度上限一律回 None（缺值，不補 0）；上限邊緣的浮點誤差夾到 100。"""
    if type(v) not in (int, float) or v != v or v in (float("inf"), float("-inf")) or v < 0:
        return None
    if v > (100.0 / factor) * (1 + _EPS):
        return None
    return min(100.0, round(float(v) * factor, 4))


def looks_percent(n_numeric: int, n_over_one: int) -> bool:
    """刻度判斷（匯入與遷移共用同一把尺）：一組數值裡**過半**（嚴格多於一半）> 1 才當它已是 0–100。"""
    return n_over_one > 0 and n_over_one * 2 > n_numeric


def _num_like(x: Any) -> bool:
    return type(x) in (int, float) and x == x


def _scale_list(arr: Any) -> tuple[Any, str]:
    """舊列（2.047.5 以前，原樣小數）的一條序列。每條序列各自判斷，與匯入同一把尺：
    過半 > 1 → 本來就是 0–100，原樣（'percent'）；否則整條 ×100，換算前 > 1 的（少數異常值）當缺值（'scaled'）。"""
    if not isinstance(arr, list):
        return arr, "none"
    nums = [x for x in arr if _num_like(x)]
    if not nums:
        return arr, "none"
    if looks_percent(len(nums), sum(1 for x in nums if x > 1 + _EPS)):
        return arr, "percent"
    return [to_percent(x, 100.0) if _num_like(x) else x for x in arr], "scaled"


def _summarize(kinds: list[str]) -> str:
    if "scaled" in kinds:
        return "scaled"
    return "already_percent" if "percent" in kinds else "scaled"     # 全空的序列也標成已換好（沒有值可換）


def rescale_legacy_perf(row: dict) -> str:
    """capacity_whatsup_perf 的舊 relay 列（沒有 pct_scale）就地換成 0–100 並標記。回 scaled／already_percent／marked（已標記，不動）。"""
    if row.get(SCALE_MARK) == "percent":
        return "marked"
    kinds = []
    for k in ("cols", "max", "min"):
        p = row.get(k)
        if isinstance(p, dict):
            for m in ("cpu_busy", "mem_used"):
                if m in p:
                    p[m], kind = _scale_list(p[m])
                    kinds.append(kind)
    row[SCALE_MARK] = "percent"
    return _summarize(kinds)


def rescale_legacy_disk(data: dict) -> str:
    """relay_disk_util 的舊列同上（avg／max／min 三條序列各自判斷）。"""
    if data.get(SCALE_MARK) == "percent":
        return "marked"
    kinds = []
    for k in ("avg", "max", "min"):
        if k in data:
            data[k], kind = _scale_list(data[k])
            kinds.append(kind)
    data[SCALE_MARK] = "percent"
    return _summarize(kinds)


def _pages(conn, sql: str, args: tuple = ()):
    """依 rowid 分頁讀（每頁 _MIGRATE_PAGE 列），不整表 fetchall。sql 的第一欄必須是 rowid，且留一個 rowid > ? 的位置。"""
    last = 0
    while True:
        rows = conn.execute(sql, (*args, last, _MIGRATE_PAGE)).fetchall()
        if not rows:
            return
        yield from rows
        last = rows[-1][0]


def migrate_wug_percent(conn: sqlite3.Connection) -> dict:
    """一次性遷移：把 2.047.6 以前由中繼匯入（source=whatsup）寫的 CPU／記憶體／磁碟列 ×100。只由 db.init_db（啟動）呼叫。
    * 做過就不再做（relay_import_migrations 有紀錄）；每列另有 pct_scale 標記，就算紀錄不見重跑也不會乘兩次。
    * 只動：capacity_whatsup_perf 裡 src=relay、且 batch 是已匯入的 whatsup 批次的列；relay_disk_util 裡 batch_id 是 whatsup 批次的列
      （在 SQL 就篩）。REST／NMON／其他來源一概不碰。逐頁讀，不整表載入。
    * 全部在同一個交易，任何錯誤整批回滾、不寫紀錄（下次啟動再試），不讓啟動失敗。
    * 紀錄已存在時**仍補掃**未標記的列（部署 [3/7] 跑完遷移後、[7/7] 重啟前，舊版服務還可能匯入未標記的小數列；不補掃就永遠漏）。
      已標記的列只解壓看標記就跳過，量小時近乎零成本；同一套 looks_percent 判斷，冪等。"""
    try:
        done_before = conn.execute("SELECT 1 FROM relay_import_migrations WHERE name = ?", (MIGRATION_WUG_PERCENT,)).fetchone() is not None
    except sqlite3.OperationalError:
        return {"status": "no_table"}
    stats = {"perf_scaled": 0, "perf_already_percent": 0, "disk_scaled": 0, "disk_already_percent": 0, "skipped_other": 0, "unreadable": 0}
    wug_sql = "SELECT batch_id FROM relay_import_batches WHERE source = 'whatsup' AND status = 'imported'"
    try:
        wug_batches = {r[0] for r in conn.execute(wug_sql)}
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('capacity_whatsup_perf','relay_disk_util')")}
        if "capacity_whatsup_perf" in tables and wug_batches:
            import capacity_analysis as ca
            for rid, blob in _pages(conn, "SELECT rowid, data FROM capacity_whatsup_perf WHERE rowid > ? ORDER BY rowid LIMIT ?"):
                try:
                    row = ca._decode(blob)
                except ValueError:
                    stats["unreadable"] += 1
                    continue
                if row.get("src") != "relay" or row.get("batch") not in wug_batches:
                    stats["skipped_other"] += 1          # REST 取回或不明來源：不碰
                    continue
                res = rescale_legacy_perf(row)
                if res == "marked":
                    continue
                stats["perf_" + res] += 1
                conn.execute("UPDATE capacity_whatsup_perf SET data = ? WHERE rowid = ?",
                             (gzip.compress(json.dumps(row, separators=(",", ":")).encode()), rid))
        if "relay_disk_util" in tables:
            for rid, blob in _pages(conn, f"SELECT rowid, data FROM relay_disk_util WHERE batch_id IN ({wug_sql}) AND rowid > ? ORDER BY rowid LIMIT ?"):
                try:
                    data = json.loads(gzip.decompress(blob).decode())
                    if not isinstance(data, dict):
                        raise ValueError("not an object")
                except (OSError, ValueError, TypeError):
                    stats["unreadable"] += 1
                    continue
                res = rescale_legacy_disk(data)
                if res == "marked":
                    continue
                stats["disk_" + res] += 1
                conn.execute("UPDATE relay_disk_util SET data = ? WHERE rowid = ?",
                             (gzip.compress(json.dumps(data, separators=(",", ":")).encode()), rid))
        changed = stats["perf_scaled"] + stats["perf_already_percent"] + stats["disk_scaled"] + stats["disk_already_percent"]
        if not done_before:
            conn.execute("INSERT INTO relay_import_migrations (name, done_at, detail) VALUES (?,?,?)",
                         (MIGRATION_WUG_PERCENT, _now_text(), json.dumps(stats, ensure_ascii=False)))
        elif changed:
            conn.execute("UPDATE relay_import_migrations SET detail = ? WHERE name = ?",
                         (json.dumps({**stats, "catch_up_at": _now_text()}, ensure_ascii=False), MIGRATION_WUG_PERCENT))
        conn.commit()
        if done_before and not changed:
            return {"status": "already", **stats}
    except Exception as exc:  # noqa: BLE001 - 遷移失敗不可讓系統起不來；整批回滾、不寫紀錄，下次啟動再試（每列有標記，不會乘兩次）
        conn.rollback()
        _log.error("WhatsUp 百分比刻度遷移失敗（已回滾，下次啟動再試）：%s", exc)
        return {"status": "failed", "error": type(exc).__name__}
    if stats["perf_scaled"] or stats["disk_scaled"]:
        _log.warning("WhatsUp 百分比刻度遷移完成：%s", stats)
    return {"status": "done", **stats}


# ---------------------------------------------------------------- 錯誤與批次脈絡
class RelayReject(Exception):
    """整批拒收。code 是固定代碼（畫面與測試用），message 是給人看的原因。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class BatchContext:
    def __init__(self, folder: Path, manifest: dict):
        self.folder = folder
        self.manifest = manifest
        self.source = str(manifest.get("source") or "")
        self.batch_id = str(manifest.get("batch_id") or folder.name)
        self.schema_version = str(manifest.get("schema_version") or "")
        self.collector_version = str(manifest.get("collector_version") or "")
        self.collected_at = str(manifest.get("collected_at") or "")
        rng = manifest.get("range") if isinstance(manifest.get("range"), dict) else {}
        self.range_from = str(rng.get("from") or "")
        self.range_to = str(rng.get("to") or "")
        self.time_zone = str(manifest.get("time_zone") or "UTC")


# ---------------------------------------------------------------- 小工具
def _now_text(now: datetime | None = None) -> str:
    if now is not None:
        return now.strftime("%Y-%m-%d %H:%M:%S")
    from db import _now_local
    return _now_local()


def parse_time(s: Any, naive_tz: str = "UTC") -> datetime | None:
    """ISO 時間字串 → aware UTC datetime。沒帶時區的字串依 naive_tz（UTC 或 Asia/Taipei）解讀；壞字串回 None。"""
    if not isinstance(s, str) or not s.strip():
        return None
    t = s.strip()
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        off = 8 if naive_tz.strip().upper() == "ASIA/TAIPEI" else 0
        d = d.replace(tzinfo=timezone(timedelta(hours=off)))
    return d.astimezone(timezone.utc)


def _taipei_text(d: datetime) -> str:
    return d.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


_FORBIDDEN_EXACT = {"password", "passwd", "pwd", "secret", "token", "accesstoken", "refreshtoken", "idtoken", "authorization", "apikey",
                    "credential", "credentials", "bearer", "privatekey", "clientsecret", "sessionid", "cookie"}
_FORBIDDEN_PARTS = ("password", "passwd", "secret", "token", "apikey", "privatekey", "accesstoken", "refreshtoken", "authorization", "bearer")
_FORBIDDEN_VALUE = re.compile(r"(^\s*bearer\s+\S{8,})|(-----BEGIN [A-Z ]*PRIVATE KEY-----)", re.I)


def find_secret(obj: Any) -> str | None:
    """走過整個 JSON，找到密碼／token／金鑰類的欄位名稱或內容就回描述（不含值本身），沒有回 None。"""
    stack = [("$", obj)]
    seen = 0
    while stack:
        path, cur = stack.pop()
        seen += 1
        if seen > MAX_JSON_NODES:
            return "結構過大，無法完成機密欄位檢查"
        if isinstance(cur, dict):
            for k, v in cur.items():
                norm = re.sub(r"[^a-z0-9]", "", str(k).lower())
                if norm in _FORBIDDEN_EXACT or any(p in norm for p in _FORBIDDEN_PARTS):
                    return f"欄位名稱 {str(k)[:40]}（位置 {path}）"
                stack.append((path + "." + str(k)[:40], v))
        elif isinstance(cur, list):
            for i, v in enumerate(cur):
                stack.append((path + "[]", v))
        elif isinstance(cur, str) and _FORBIDDEN_VALUE.search(cur):
            return f"內容像是 token 或私鑰（位置 {path}）"
    return None


# ---------------------------------------------------------------- 設定
def _int_setting(conn, key: str, default: int) -> int:
    try:
        return int(get_setting(conn, key, str(default)) or default)
    except ValueError:
        return default


def get_config(conn) -> dict:
    return {
        "enabled": get_setting(conn, ENABLED_KEY, "0") == "1",
        "dir": get_setting(conn, DIR_KEY, "") or "",
        "stale_days": _int_setting(conn, STALE_DAYS_KEY, DEFAULT_STALE_DAYS),
        "interval_min": _int_setting(conn, INTERVAL_KEY, DEFAULT_INTERVAL_MIN),
        "max_file_mb": _int_setting(conn, MAX_FILE_MB_KEY, DEFAULT_MAX_FILE_MB),
        "last_scan": get_setting(conn, LAST_SCAN_KEY, "") or "",
        "last_result": get_setting(conn, LAST_RESULT_KEY, "") or "",
    }


def validate_dir(directory: str) -> str:
    d = (directory or "").strip()
    if not d:
        return ""
    if len(d) > 300 or any(ord(c) < 32 for c in d):
        raise ValueError("資料夾路徑太長或含有不合法字元")
    if not (PurePosixPath(d).is_absolute() or PureWindowsPath(d).is_absolute()):
        raise ValueError("資料夾路徑要寫完整路徑（絕對路徑）")
    if ".." in PurePosixPath(d.replace("\\", "/")).parts:
        raise ValueError("資料夾路徑不可含 ..")
    return d


def set_config(conn, enabled: bool, directory: str, stale_days: int, interval_min: int) -> dict:
    d = validate_dir(directory)
    if enabled and not d:
        raise ValueError("要啟用中繼匯入，請先填共用資料夾路徑")
    if not 1 <= int(stale_days) <= 60:
        raise ValueError("資料過期天數請填 1 到 60")
    if not 1 <= int(interval_min) <= 1440:
        raise ValueError("檢查間隔請填 1 到 1440 分鐘")
    set_setting(conn, ENABLED_KEY, "1" if enabled else "0")
    set_setting(conn, DIR_KEY, d)
    set_setting(conn, STALE_DAYS_KEY, str(int(stale_days)))
    set_setting(conn, INTERVAL_KEY, str(int(interval_min)))
    return get_config(conn)


# ---------------------------------------------------------------- Adapter 介面
class Adapter:
    """每種資料來源一個。parse 驗證並整理單一資料檔（壞資料丟 RelayReject）；apply 把整批已驗證的內容寫進正式資料表（同一個交易）。"""

    name = ""
    label = ""
    implemented = False
    schema_versions: tuple[str, ...] = ()
    note = ""
    range_optional = False          # 快照來源（例：Unity）可以不寫 range，視為 collected_at 那一天

    def parse(self, ctx: BatchContext, filename: str, data: Any) -> Any:
        raise RelayReject("unsupported", f"{self.label}尚未支援匯入")

    def apply(self, conn, ctx: BatchContext, parsed: dict[str, Any], now_text: str) -> dict:
        raise RelayReject("unsupported", f"{self.label}尚未支援匯入")


ADAPTERS: dict[str, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    ADAPTERS[adapter.name] = adapter
    return adapter


# ---------------------------------------------------------------- 批次檢查
def _read_regular(path: Path, max_bytes: int) -> bytes:
    """只讀一般檔案：符號連結、目錄、裝置都不收；超過上限不讀。"""
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise RelayReject("file_missing", f"讀不到檔案 {path.name}：{exc.strerror or exc}") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise RelayReject("path_unsafe", f"{path.name} 不是一般檔案（符號連結或其他類型不收）")
    if st.st_size > max_bytes:
        raise RelayReject("too_large", f"{path.name} 超過單檔上限 {max_bytes // (1024 * 1024)} MB")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RelayReject("file_missing", f"讀不到檔案 {path.name}：{exc.strerror or exc}") from exc
    try:
        fst = os.fstat(fd)                 # 開啟後再比對一次：檢查與開啟之間若被換成別的檔案（TOCTOU）就拒收
        if (fst.st_ino, fst.st_dev) != (st.st_ino, st.st_dev) or fst.st_size != st.st_size:
            os.close(fd)
            raise RelayReject("path_unsafe", f"{path.name} 在檢查與讀取之間被更動，已拒收")
    except OSError as exc:
        os.close(fd)
        raise RelayReject("file_missing", f"讀不到檔案 {path.name}：{exc.strerror or exc}") from exc
    with os.fdopen(fd, "rb") as fh:
        raw = fh.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise RelayReject("too_large", f"{path.name} 超過單檔上限 {max_bytes // (1024 * 1024)} MB")
    return raw


def _loads(raw: bytes, name: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise RelayReject("bad_json", f"{name} 不是合法的 JSON：{str(exc)[:80]}") from exc


_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA_OK = re.compile(r"^[0-9a-fA-F]{64}$")


def read_manifest(folder: Path, max_bytes: int) -> dict:
    raw = _read_regular(folder / "manifest.json", min(max_bytes, 4 * 1024 * 1024))
    m = _loads(raw, "manifest.json")
    if not isinstance(m, dict):
        raise RelayReject("manifest_invalid", "manifest.json 的最外層要是物件")
    leak = find_secret(m)
    if leak:
        raise RelayReject("secret_field", f"manifest 含不允許的機密內容：{leak}")
    _ad = ADAPTERS.get(m.get("source")) if isinstance(m.get("source"), str) else None
    if "range" not in m and _ad is not None and getattr(_ad, "range_optional", False):
        _d = parse_time(m.get("collected_at"), "Asia/Taipei")
        if _d is not None:
            _day = _d.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            m["range"] = {"from": _day, "to": _day}
    missing = [k for k in SUPPORTED_MANIFEST_KEYS if k not in m]
    if missing:
        raise RelayReject("manifest_invalid", "manifest 缺少欄位：" + "、".join(missing))
    if not isinstance(m["source"], str) or not isinstance(m["collector_version"], str):
        raise RelayReject("manifest_invalid", "source、collector_version 要是文字")
    if not isinstance(m["files"], list) or not isinstance(m["count"], int) or isinstance(m["count"], bool) or m["count"] < 0:
        raise RelayReject("manifest_invalid", "count 要是 0 以上的整數、files 要是清單")
    if m["count"] != len(m["files"]):
        raise RelayReject("manifest_invalid", f"count（{m['count']}）和 files 的檔案數（{len(m['files'])}）不一致")
    if len(m["files"]) > MAX_FILES_PER_BATCH:
        raise RelayReject("too_large", f"一批最多 {MAX_FILES_PER_BATCH} 個檔")
    if not isinstance(m["range"], dict) or not isinstance(m["range"].get("from"), str) or not isinstance(m["range"].get("to"), str):
        raise RelayReject("manifest_invalid", "range 要有 from、to")
    _ct = parse_time(m["collected_at"])
    if _ct is None:
        raise RelayReject("manifest_invalid", "collected_at 不是合法的時間（要 ISO 8601，例 2026-11-02T21:05:00+08:00）")
    if _ct > datetime.now(timezone.utc) + timedelta(days=1):
        raise RelayReject("manifest_invalid", "collected_at 在未來（超過 1 天），不收；請確認中繼機時鐘")
    bid = m.get("batch_id")
    if bid is not None and (not isinstance(bid, str) or not _NAME_OK.match(bid)):
        raise RelayReject("manifest_invalid", "batch_id 只能用英數、點、底線、連字號")
    return m


def check_batch(folder: Path, max_file_bytes: int) -> tuple[BatchContext, dict[str, Any]]:
    """驗證整批並回 (脈絡, {檔名: 已 parse 的內容})。任何問題丟 RelayReject。"""
    m = read_manifest(folder, max_file_bytes)
    ad = ADAPTERS.get(m["source"])
    if ad is None:
        raise RelayReject("unknown_source", f"不認得的資料來源：{m['source'][:40]}")
    if not ad.implemented:
        raise RelayReject("unsupported", f"{ad.label}尚未支援匯入（只有契約，沒有實作）")
    if str(m["schema_version"]) not in ad.schema_versions:
        raise RelayReject("schema_unsupported", f"{ad.label}不支援 schema_version {str(m['schema_version'])[:20]}（支援：{'、'.join(ad.schema_versions)}）")
    if not m["files"]:
        raise RelayReject("empty_batch", "批次裡沒有任何資料檔（空批次不收，免得讓鮮度燈誤判為有新資料）")
    ctx = BatchContext(folder, m)
    if not _NAME_OK.match(ctx.batch_id):
        raise RelayReject("manifest_invalid", "批次資料夾名稱只能用英數、點、底線、連字號")
    seen: set[str] = set()
    parsed: dict[str, Any] = {}
    for f in m["files"]:
        if not isinstance(f, dict):
            raise RelayReject("manifest_invalid", "files 內每一項要是物件")
        name, sha, size = f.get("name"), f.get("sha256"), f.get("size")
        if not isinstance(name, str) or not _NAME_OK.match(name) or name.lower() == "manifest.json" or name.lower().endswith(".tmp"):
            raise RelayReject("path_unsafe", f"檔名不合法：{str(name)[:60]}（只准單純檔名，不可含路徑）")
        if name in seen:
            raise RelayReject("manifest_invalid", f"檔名重複：{name}")
        seen.add(name)
        if not isinstance(sha, str) or not _SHA_OK.match(sha) or not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise RelayReject("manifest_invalid", f"{name} 的 sha256／size 格式不對")
        p = folder / name
        if p.resolve().parent != folder.resolve():
            raise RelayReject("path_unsafe", f"{name} 跑出批次資料夾之外")
        raw = _read_regular(p, max_file_bytes)
        if len(raw) != size:
            raise RelayReject("size_mismatch", f"{name} 大小（{len(raw)}）和 manifest 記的（{size}）不同")
        if hashlib.sha256(raw).hexdigest() != sha.lower():
            raise RelayReject("sha_mismatch", f"{name} 的 sha256 和 manifest 不符（檔案可能損毀或還在寫入）")
        data = _loads(raw, name)
        leak = find_secret(data)
        if leak:
            raise RelayReject("secret_field", f"{name} 含不允許的機密內容：{leak}")
        parsed[name] = ad.parse(ctx, name, data)
    return ctx, parsed


def batch_signature(folder: Path) -> str:
    """批次目前的樣子（manifest 內容＋各檔名稱／大小／修改時間）。內容有任何變動就會不同，讓被拒收的批次補好後能重新處理。"""
    h = hashlib.sha256()
    try:
        raw = (folder / "manifest.json").read_bytes()
    except OSError:
        return ""
    h.update(raw)
    try:
        m = json.loads(raw.decode("utf-8-sig"))
        names = [f.get("name") for f in m.get("files", []) if isinstance(f, dict) and isinstance(f.get("name"), str)]
    except (ValueError, AttributeError):
        names = []
    for n in sorted(names):
        try:
            st = os.lstat(folder / n)
            h.update(f"{n}:{st.st_size}:{st.st_mtime_ns};".encode())
        except OSError:
            h.update(f"{n}:missing;".encode())
    return h.hexdigest()


def list_batch_folders(root: str, stable_seconds: int | None = None) -> tuple[list[Path], str | None]:
    """資料夾下有 manifest.json 且已靜置夠久的批次，由舊到新。回 (清單, 問題說明)。符號連結的資料夾不收。"""
    if not root:
        return [], "尚未設定共用資料夾"
    base = Path(root)
    if not base.is_dir():
        return [], f"資料夾不存在或讀不到：{root}"
    stable = STABLE_SECONDS if stable_seconds is None else stable_seconds
    cutoff = datetime.now().timestamp() - stable
    out: list[tuple[float, str, Path]] = []
    try:
        with os.scandir(base) as it:
            for e in it:
                if e.name.startswith(".") or e.name.endswith(".tmp"):
                    continue
                try:
                    if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                        continue
                    mst = os.lstat(os.path.join(e.path, "manifest.json"))
                except OSError:
                    continue            # 沒有 manifest＝還沒寫完或不是批次
                if stat.S_ISREG(mst.st_mode) and mst.st_mtime <= cutoff:
                    out.append((mst.st_mtime, e.name, Path(e.path)))
    except OSError as exc:
        return [], f"資料夾讀取失敗：{exc.strerror or exc}"
    out.sort(key=lambda x: (x[0], x[1]))
    return [p for _, _, p in out], None


# ---------------------------------------------------------------- 匯入紀錄
def _upsert_batch(conn, source: str, batch_id: str, status: str, trigger: str, actor: str | None, now_text: str, folder: str, sig: str,
                  ctx: BatchContext | None = None, stats: dict | None = None, code: str = "", reason: str = "") -> None:
    st = stats or {}
    cols = {
        "status": status, "reason_code": code, "reason": reason[:500],
        "schema_version": ctx.schema_version if ctx else "", "collector_version": ctx.collector_version if ctx else "",
        "collected_at": _taipei_text(parse_time(ctx.collected_at)) if ctx and parse_time(ctx.collected_at) else "",
        "range_from": ctx.range_from if ctx else "", "range_to": ctx.range_to if ctx else "", "time_zone": ctx.time_zone if ctx else "",
        "files": st.get("files"), "devices": st.get("devices"), "host_days": st.get("host_days"), "points": st.get("points"),
        "skipped_existing": st.get("skipped_existing"), "unmatched": st.get("unmatched"), "bad_values": st.get("bad_values"),
        "sig": sig, "imported_at": now_text, "imported_by": actor or "system", "trigger": trigger, "folder": folder[:300],
    }
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{k} = excluded.{k}" for k in cols)
    conn.execute(f"INSERT INTO relay_import_batches (source, batch_id, {names}) VALUES (?, ?, {marks}) "
                 f"ON CONFLICT(source, batch_id) DO UPDATE SET {updates}", (source, batch_id, *cols.values()))


def _known(conn, source: str, batch_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT status, sig FROM relay_import_batches WHERE source = ? AND batch_id = ?", (source, batch_id)).fetchone()


def _peek_ident(folder: Path) -> tuple[str, str]:
    """還沒驗證前，先從 manifest 抓 (source, batch_id) 用來查「是不是已處理過」；抓不到就用 ('-', 資料夾名)。"""
    try:
        m = json.loads((folder / "manifest.json").read_bytes()[:4 * 1024 * 1024].decode("utf-8-sig"))
        src = m.get("source") if isinstance(m, dict) else None
        bid = m.get("batch_id") if isinstance(m, dict) else None
        src = src if isinstance(src, str) and 0 < len(src) <= 40 else "-"
        bid = bid if isinstance(bid, str) and _NAME_OK.match(bid) else folder.name
        return src, bid
    except (OSError, ValueError, AttributeError):
        return "-", folder.name


def is_pending(conn, folder: Path) -> bool:
    """這批要不要處理：沒處理過、或上次失敗、或上次被拒收但內容之後有變。已成功匯入的永遠不再處理（冪等）。"""
    src, bid = _peek_ident(folder)
    row = _known(conn, src, bid)
    if row is None:
        return True
    if row["status"] == "imported":
        return False
    if row["status"] == "failed":
        return True
    return (row["sig"] or "") != batch_signature(folder)


def pending_batches(conn) -> list[Path]:
    cfg = get_config(conn)
    folders, _ = list_batch_folders(cfg["dir"])
    return [f for f in folders if not f.name.startswith(FAST_PREFIXES) and is_pending(conn, f)]


# ---------------------------------------------------------------- 匯入一批
_run_lock = threading.Lock()


def import_batch(conn, folder: Path, trigger: str, actor: str | None, now_text: str, max_file_bytes: int) -> dict:
    """處理一批：驗證 → 寫入（同一交易）→ 記紀錄。回 {status, source, batch_id, ...}；從不丟 RelayReject 出去。"""
    src, bid = _peek_ident(folder)
    sig = batch_signature(folder)
    try:
        ctx, parsed = check_batch(folder, max_file_bytes)
    except RelayReject as exc:
        _upsert_batch(conn, src, bid, "rejected", trigger, actor, now_text, str(folder), sig, code=exc.code, reason=exc.message)
        conn.commit()
        return {"status": "rejected", "source": src, "batch_id": bid, "code": exc.code, "reason": exc.message}
    prior = _known(conn, ctx.source, ctx.batch_id)
    if prior is not None and prior["status"] == "imported":
        return {"status": "already", "source": ctx.source, "batch_id": ctx.batch_id}
    ad = ADAPTERS[ctx.source]
    try:
        stats = ad.apply(conn, ctx, parsed, now_text)
        stats["files"] = len(parsed)
        _upsert_batch(conn, ctx.source, ctx.batch_id, "imported", trigger, actor, now_text, str(folder), sig, ctx=ctx, stats=stats,
                      reason=str(stats.get("note") or ""))
        conn.commit()
    except RelayReject as exc:
        conn.rollback()
        _upsert_batch(conn, ctx.source, ctx.batch_id, "rejected", trigger, actor, now_text, str(folder), sig, ctx=ctx, code=exc.code, reason=exc.message)
        conn.commit()
        return {"status": "rejected", "source": ctx.source, "batch_id": ctx.batch_id, "code": exc.code, "reason": exc.message}
    except Exception as exc:  # noqa: BLE001 - 寫入失敗：整批回滾、記 failed（下一輪會再試），不讓排程掛掉
        conn.rollback()
        _log.error("中繼匯入 %s/%s 寫入失敗：%s", ctx.source, ctx.batch_id, exc)
        _upsert_batch(conn, ctx.source, ctx.batch_id, "failed", trigger, actor, now_text, str(folder), sig, ctx=ctx,
                      code="apply_failed", reason=f"{type(exc).__name__}: {exc}")
        conn.commit()
        return {"status": "failed", "source": ctx.source, "batch_id": ctx.batch_id, "code": "apply_failed", "reason": str(exc)[:200]}
    res = {"status": "imported", "source": ctx.source, "batch_id": ctx.batch_id, **stats}
    _save_run_summary(conn, ctx)
    try:                               # 匯入已提交之後才評估磁碟告警；任何失敗（含模組載入錯誤）只記日誌，不影響這一批的匯入結果
        import disk_alert
        disk_alert.after_import(conn, res)
        import opening_notify
        opening_notify.after_import(conn, res)
        import mem_alert                 # 2.060.x 記憶體／SWAP（預設關閉；自己的 try/except，失敗只記日誌）
        mem_alert.after_import(conn, res)
    except Exception:  # noqa: BLE001 - 告警是附加功能，絕不能讓已提交的匯入變成失敗
        _log.exception("中繼匯入 %s/%s：磁碟告警評估失敗（匯入本身已成功）", ctx.source, ctx.batch_id)
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
    return res


def _count(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _save_run_summary(conn, ctx: BatchContext) -> None:
    """最後一批（run.final=true）的整輪計數存起來；格式不對就不存（舊版收集端沒有這欄）。只寫設定、失敗只記日誌。"""
    run = ctx.manifest.get("run")
    if not isinstance(run, dict) or run.get("final") is not True:
        return
    nums = {k: _count(run.get(k)) for k in ("ips", "ok", "failed", "notfound")}
    if any(v is None for v in nums.values()):
        return
    try:
        set_setting(conn, RUN_SUMMARY_KEY + ctx.source, json.dumps(
            {**nums, "batch_id": ctx.batch_id, "range_to": ctx.range_to, "collected_at": ctx.collected_at,
             "collector_version": ctx.collector_version}, ensure_ascii=False))
        conn.commit()
    except sqlite3.Error:
        _log.exception("中繼匯入 %s/%s：整輪計數存不進去（匯入本身已成功）", ctx.source, ctx.batch_id)


def run_summary(conn, source: str) -> dict | None:
    try:
        d = json.loads(get_setting(conn, RUN_SUMMARY_KEY + source, "") or "null")
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def run_incomplete(summary: dict | None) -> bool:
    """失敗（含找不到以外的錯誤）超過一成＝收集不完整。找不到（清單裡有、收集來源沒有這台）另列，不算失敗。"""
    if not summary:
        return False
    ok, failed = summary.get("ok") or 0, summary.get("failed") or 0
    tried = ok + failed
    return tried == 0 or failed / tried > RUN_FAIL_RATIO


def run_scan(conn, trigger: str = "manual", actor: str | None = None, now: datetime | None = None) -> dict:
    """掃一次資料夾，處理所有未處理的批次（由舊到新，一輪最多 MAX_BATCHES_PER_RUN 批）。
    這裡不做盤中靜默窗判斷：排程入口 tick 先守門；人工入口由路由先做窗內二次確認。"""
    ensure(conn)
    cfg = get_config(conn)
    if not cfg["dir"]:
        return {"status": "no_dir", "message": "尚未設定共用資料夾"}
    if not _run_lock.acquire(blocking=False):
        return {"status": "busy", "message": "已有一次匯入正在進行，請稍後再試"}
    try:
        folders, problem = list_batch_folders(cfg["dir"])
        if problem:
            set_setting(conn, LAST_RESULT_KEY, problem)
            return {"status": "no_dir", "message": problem}
        now_text = _now_text(now)
        todo = [f for f in folders if not (trigger == "schedule" and f.name.startswith(FAST_PREFIXES)) and is_pending(conn, f)]
        results = []
        stopped_quiet = False
        for f in todo[:MAX_BATCHES_PER_RUN]:
            # 排程觸發：每一批開始前重新看一次盤中靜默窗（一批一個 checkpoint）。跑到一半進窗就停，其餘留待窗外下一輪。
            # 人工觸發不在這裡擋：路由已做窗內二次確認，確認過就整輪做完。
            if trigger == "schedule" and results:
                import quiet_window as qw
                if qw.defer_if_quiet(conn, "relay_import_tick"):          # 用時鐘的「現在」，不用整輪開始時傳進來的 now
                    stopped_quiet = True
                    break
            results.append(import_batch(conn, f, trigger, actor, now_text, cfg["max_file_mb"] * 1024 * 1024))
        n = {k: sum(1 for r in results if r["status"] == k) for k in ("imported", "rejected", "failed", "already")}
        line = f"看到 {len(folders)} 批；新匯入 {n['imported']}、拒收 {n['rejected']}、失敗 {n['failed']}" + (f"；還有 {len(todo) - len(results)} 批下一輪處理" if len(todo) > len(results) else "") + ("（進入盤中靜默窗，暫停）" if stopped_quiet else "")
        set_setting(conn, LAST_SCAN_KEY, now_text)
        set_setting(conn, LAST_RESULT_KEY, line)
        return {"status": "ok", "found": len(folders), "message": line, **n, "results": results}
    finally:
        _run_lock.release()


# ---------------------------------------------------------------- 排程入口（只在窗外動）
def tick(conn, now: datetime | None = None) -> dict:
    """排程器每輪呼叫。啟用才動；依設定的間隔檢查資料夾；盤中靜默窗（平日 08:30–15:00）內不動並記延後，窗外下一輪補。
    只在確實有未處理的批次時才記延後（資料夾沒新東西就不必擾動延後紀錄）。"""
    import quiet_window as qw
    if get_setting(conn, ENABLED_KEY, "0") != "1":
        return {"status": "disabled"}
    ensure(conn)
    interval = max(1, _int_setting(conn, INTERVAL_KEY, DEFAULT_INTERVAL_MIN))
    last = get_setting(conn, LAST_CHECK_KEY, "") or ""
    t = _now_text(now)
    if last:
        try:
            if datetime.strptime(t, "%Y-%m-%d %H:%M:%S") - datetime.strptime(last, "%Y-%m-%d %H:%M:%S") < timedelta(minutes=interval):
                return {"status": "not_due"}
        except ValueError:
            pass                        # 時間欄壞掉就當作該檢查了
    if qw.defer_if_quiet(conn, "relay_import_tick", now):          # 盤中先擋（記延後）：連列舉資料夾、讀 manifest 都不做
        return {"status": "deferred_quiet_window"}
    if not pending_batches(conn):
        set_setting(conn, LAST_CHECK_KEY, t)
        return {"status": "idle"}
    if qw.defer_if_quiet(conn, "relay_import_tick", now):
        return {"status": "deferred_quiet_window"}
    set_setting(conn, LAST_CHECK_KEY, t)
    return run_scan(conn, "schedule", None, now)


# ---------------------------------------------------------------- 讀取（畫面用）
def _missed_trading_days(conn, got: datetime, now_utc: datetime) -> int:
    """Q12（11-13）：每個交易日收盤後才有一批的來源（開盤尖峰）——最後一批之後已經過了幾個「該有資料」的交易日。
    今天是交易日且已過 20:00 才算今天該有；週末與休市日（quiet_window 設定）不算。"""
    import opening_peak
    tpe = timezone(timedelta(hours=8))
    last = got.astimezone(tpe).date()
    now_t = now_utc.astimezone(tpe)
    n, d = 0, last + timedelta(days=1)
    while d <= now_t.date():
        if (d < now_t.date() or now_t.hour >= 20) and opening_peak._is_trading_day(conn, d):
            n += 1
        d += timedelta(days=1)
    return n


def sources_view(conn, now: datetime | None = None) -> list[dict]:
    """每個資料來源一盞鮮度燈：ok 正常／stale 資料過期／never 從未收到。"""
    ensure(conn)
    cfg = get_config(conn)
    if now is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone(timedelta(hours=8)))       # 沒帶時區的時間視為台北牆上時間（全站慣例）
    now_utc = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    out = []
    for name, ad in ADAPTERS.items():
        row = conn.execute("SELECT batch_id, collected_at, imported_at FROM relay_import_batches WHERE source = ? AND status = 'imported' "
                           "ORDER BY collected_at DESC, id DESC LIMIT 1", (name,)).fetchone()
        rej = conn.execute("SELECT COUNT(*) FROM relay_import_batches WHERE source = ? AND status IN ('rejected','failed')", (name,)).fetchone()[0]
        v = {"source": name, "label": ad.label, "implemented": ad.implemented, "note": ad.note, "status": "never", "status_label": "從未收到",
             "last_batch": None, "last_collected_at": None, "age_hours": None,
             "stale_days": getattr(ad, "stale_days", None) or cfg["stale_days"], "problem_batches": rej}
        if row is not None:
            got = parse_time(row["collected_at"], "Asia/Taipei") or parse_time(row["imported_at"], "Asia/Taipei")
            age = (now_utc - got).total_seconds() / 3600 if got else None
            v.update(last_batch=row["batch_id"], last_collected_at=row["collected_at"] or row["imported_at"],
                     age_hours=round(age, 1) if age is not None else None)
            missed = _missed_trading_days(conn, got, now_utc) if getattr(ad, "stale_trading_days", None) and got else None
            if missed is not None:
                v["missed_trading_days"] = missed
            if age is None or age > v["stale_days"] * 24 or (missed is not None and missed >= ad.stale_trading_days):
                v.update(status="stale", status_label="資料過期")
            else:
                v.update(status="ok", status_label="正常")
            rs = run_summary(conn, name)
            v["run"] = rs
            if v["status"] == "ok" and run_incomplete(rs):
                v.update(status="partial",
                         status_label=f"收集不完整（成功 {rs.get('ok')}／失敗 {rs.get('failed')}）")
        out.append(v)
    return out


def view(conn, now: datetime | None = None) -> dict:
    import quiet_window as qw
    ensure(conn)
    cfg = get_config(conn)
    folders, problem = list_batch_folders(cfg["dir"]) if cfg["dir"] else ([], None)
    return {"config": cfg, "sources": sources_view(conn, now), "dir_problem": problem if cfg["dir"] else None,
            "batches_visible": len(folders), "in_quiet_window": qw.in_quiet_window(conn, now)}


def list_batches(conn, source: str | None = None, limit: int = 200) -> list[dict]:
    ensure(conn)
    q, a = "SELECT * FROM relay_import_batches", []
    if source:
        q += " WHERE source = ?"
        a.append(source)
    rows = [dict(r) for r in conn.execute(q + " ORDER BY id DESC LIMIT ?", (*a, max(1, min(int(limit), 1000))))]
    for r in rows:
        r["__no"] = r["id"]
        r["sig"] = None
        r["folder"] = os.path.basename(str(r["folder"] or "").rstrip("/\\"))     # 畫面不露完整路徑
    return rows


def list_unmatched(conn, source: str | None = None) -> list[dict]:
    ensure(conn)
    q, a = "SELECT * FROM relay_import_unmatched", []
    if source:
        q += " WHERE source = ?"
        a.append(source)
    rows = [dict(r) for r in conn.execute(q + " ORDER BY source, ident", a)]
    for i, r in enumerate(rows, 1):
        r["__no"] = i
    return rows


# ---------------------------------------------------------------- 資產對應（IP → 資產；對不到不丟棄，列未匹配）
def match_asset(conn, ip: str) -> tuple[str, int, str | None]:
    """回 (結果, 候選數, 資產序號)：matched／no_asset／ambiguous。同一台（machine_key 相同）的多筆登記算 matched。"""
    try:
        rows = conn.execute("SELECT asset_serial, hostname, ip FROM hardware WHERE TRIM(COALESCE(ip, '')) = ?", (ip,)).fetchall()
    except sqlite3.OperationalError:
        rows = []                       # 沒有資產表＝沒有可對應的
    if not rows:
        return "no_asset", 0, None
    import system_stats
    keys = {system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"]) for r in rows}
    if len(keys) == 1:
        return "matched", len(rows), rows[0]["asset_serial"]
    return "ambiguous", len(keys), None


def record_match(conn, source: str, ident: str, name: str, batch_id: str, now_text: str) -> str:
    """登記對應結果：對不到或多重對應就寫進未匯入清單；之後對得到了就移除。回 match_asset 的結果。"""
    res, n, _serial = match_asset(conn, ident)
    if res == "matched":
        conn.execute("DELETE FROM relay_import_unmatched WHERE source = ? AND ident = ?", (source, ident))
        return res
    conn.execute("INSERT INTO relay_import_unmatched (source, ident, name, reason, candidates, last_batch, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?) "
                 "ON CONFLICT(source, ident) DO UPDATE SET name = excluded.name, reason = excluded.reason, candidates = excluded.candidates, "
                 "last_batch = excluded.last_batch, last_seen = excluded.last_seen",
                 (source, ident, name[:80], res, n, batch_id, now_text, now_text))
    return res


# ---------------------------------------------------------------- Adapter：WhatsUp
class WhatsUpAdapter(Adapter):
    """WhatsUp Gold 每台 cpu／memory／disk-utilization 序列。

    資料檔（每台一檔）：{"ip", "id"?, "name"?, "reports": {"cpu-utilization": [...], "memory-utilization": [...], "disk-utilization": [...]}}
    每列只收：pollTimeUtc、avgPercent、maxPercent、minPercent，加識別欄 cpuId／memoryId／diskId／disk／series。其他欄位一律忽略。
    百分比刻度（2.047.6）：WhatsUp 原生是 0～1 小數，匯入時一律換成 0–100（見 wug_factor／to_percent）；manifest 可寫
    percent_scale="percent" 表示收集端已換好。小數刻度下一個檔裡過半的數值 > 1 ＝刻度對不上，只那個檔不收（記壞值與理由），不逐值猜。

    寫入：
      * CPU、記憶體 → capacity_whatsup_perf（容量分析既有的 WhatsUp 來源表，一台一天一列；cpu_busy／mem_used，kind=bucket，tz=UTC）。
        同一個時間點有多個 cpuId／memoryId 時取平均（avg）、最大（max）、最小（min）。同一台同一天已有 REST 取回的資料（不是本模組寫的）一律不覆蓋；
        已有本模組寫的就依時間點合併（新批次同時間點蓋舊的，缺的時間點不補 0）。
      * 磁碟 → relay_disk_util（磁碟是容量百分比，不是忙碌度，不能放進容量分析的「磁碟忙碌」指標，另表保存給儲存容量用）。
    """

    name = "whatsup"
    label = "WhatsUp Gold 效能"
    implemented = True
    schema_versions = ("1",)
    note = "每台 CPU／記憶體／磁碟使用率序列"
    REPORTS = ("cpu-utilization", "memory-utilization", "disk-utilization")
    MAX_ROWS_PER_REPORT = 200_000

    def parse(self, ctx: BatchContext, filename: str, data: Any) -> dict:
        import ipaddress
        if not isinstance(data, dict):
            raise RelayReject("bad_data", f"{filename}：最外層要是物件")
        ip = data.get("ip")
        try:
            ip = str(ipaddress.ip_address(str(ip).strip()))
        except ValueError:
            raise RelayReject("bad_data", f"{filename}：ip 不是合法的 IP 位址") from None
        reports = data.get("reports")
        if not isinstance(reports, dict):
            raise RelayReject("bad_data", f"{filename}：缺少 reports")
        name = str(data.get("name") or "")[:80]
        wug_id = data.get("id") if isinstance(data.get("id"), (int, str)) and not isinstance(data.get("id"), bool) else None
        out: dict[str, Any] = {"ip": ip, "name": name, "id": wug_id, "cpu": {}, "mem": {}, "disk": {}, "bad": 0, "_n": 0, "_over": 0}
        factor = wug_factor(ctx.manifest)
        for rname in reports:
            if rname not in self.REPORTS:
                continue                # 未知的報表忽略（日後加網卡等新報表不影響舊版匯入）
            rows = reports[rname]
            if not isinstance(rows, list) or len(rows) > self.MAX_ROWS_PER_REPORT:
                raise RelayReject("bad_data", f"{filename}：{rname} 要是清單且不超過 {self.MAX_ROWS_PER_REPORT} 列")
            self._collect(ctx, filename, rname, rows, out, factor)
        if factor != 1.0 and looks_percent(out["_n"], out["_over"]):
            # 小數刻度下這個檔過半 > 1：刻度對不上，**只**這個檔不收（數值全記壞值、理由寫進匯入紀錄），同批其他檔照收；不逐值猜
            out.update({"cpu": {}, "mem": {}, "disk": {}, "bad": out["_n"], "scale_reject": True})   # 壞值＝這個檔的數值個數（逐列已記的不重複算）
        return out

    def _collect(self, ctx: BatchContext, filename: str, rname: str, rows: list, out: dict, factor: float = 100.0) -> None:
        bucket = {"cpu-utilization": out["cpu"], "memory-utilization": out["mem"]}.get(rname)
        for r in rows:
            if not isinstance(r, dict):
                raise RelayReject("bad_data", f"{filename}：{rname} 的每一列要是物件")
            t = parse_time(r.get("pollTimeUtc"), ctx.time_zone)
            if t is None:
                out["bad"] += 1         # 時間壞掉的列不收（不猜）
                continue
            raws = (r.get("avgPercent"), r.get("maxPercent"), r.get("minPercent"))
            for x in raws:
                if type(x) in (int, float) and x == x:
                    out["_n"] += 1
                    if x > 1 + _EPS:
                        out["_over"] += 1
            vals = tuple(to_percent(x, factor) for x in raws)
            if all(v is None for v in vals):
                out["bad"] += 1
                continue
            if sum(1 for raw, v in zip(raws, vals) if raw is not None and v is None):
                out["bad"] += 1         # 超出刻度上限或不是數字的值當缺值（不補 0），記一筆壞值
            key = (t.date().isoformat(), t.strftime("%H:%M:%S"))
            if rname == "disk-utilization":
                label = str(r.get("disk") or r.get("diskId") or r.get("series") or "disk")[:64]
                label = re.sub(r"[\x00-\x1f]", "", label) or "disk"
                out["disk"].setdefault(label, {}).setdefault(key, []).append(vals)
            else:
                bucket.setdefault(key, []).append(vals)

    @staticmethod
    def _agg(samples: list[tuple]) -> tuple[float | None, float | None, float | None]:
        avgs = [s[0] for s in samples if s[0] is not None]
        mxs = [s[1] for s in samples if s[1] is not None]
        mns = [s[2] for s in samples if s[2] is not None]
        return (round(sum(avgs) / len(avgs), 2) if avgs else None, max(mxs) if mxs else None, min(mns) if mns else None)

    def apply(self, conn, ctx: BatchContext, parsed: dict[str, Any], now_text: str) -> dict:
        import capacity_analysis as ca
        conn.execute(ca.WU_TABLE_SQL)
        stats = {"devices": 0, "host_days": 0, "points": 0, "skipped_existing": 0, "unmatched": 0, "bad_values": 0, "disk_series": 0}
        scale_bad = sorted(f for f, d in parsed.items() if d.get("scale_reject"))
        if scale_bad:
            stats["scale_rejected_files"] = len(scale_bad)
            stats["note"] = (f"{len(scale_bad)} 個檔數值過半 > 1（像 0–100，但 manifest 沒寫 {WUG_SCALE_KEY}=percent），這些檔沒收："
                             + "、".join(scale_bad[:10]) + ("…" if len(scale_bad) > 10 else ""))
            if len(scale_bad) == len(parsed):
                # 整批每個檔都刻度不對：記成拒收（不是「匯入成功卻什麼都沒寫」），修好後同一個 batch_id 內容變了會重新處理
                raise RelayReject("bad_data", "整批每個檔的刻度都不對：" + stats["note"])
        for fname in sorted(parsed):
            dev = parsed[fname]
            stats["devices"] += 1
            stats["bad_values"] += dev["bad"]
            if record_match(conn, self.name, dev["ip"], dev["name"], ctx.batch_id, now_text) != "matched":
                stats["unmatched"] += 1
            days: dict[str, dict[str, dict]] = {}
            for metric, series in (("cpu_busy", dev["cpu"]), ("mem_used", dev["mem"])):
                for (day, tt), samples in series.items():
                    days.setdefault(day, {}).setdefault(tt, {})[metric] = self._agg(samples)
            for day, pts in days.items():
                self._write_day(conn, ca, dev["ip"], day, pts, ctx, now_text, stats)
            for label, series in dev["disk"].items():
                ddays: dict[str, dict[str, tuple]] = {}
                for (day, tt), samples in series.items():
                    ddays.setdefault(day, {})[tt] = self._agg(samples)
                for day, pts in ddays.items():
                    self._write_disk(conn, dev["ip"], day, label, pts, ctx, now_text, stats)
        return stats

    # ---- CPU／記憶體：寫進容量分析的 WhatsUp 來源表
    @staticmethod
    def _interval(tts: list[str]) -> int:
        secs = sorted(int(b[:2]) * 3600 + int(b[3:5]) * 60 + int(b[6:8]) - (int(a[:2]) * 3600 + int(a[3:5]) * 60 + int(a[6:8])) for a, b in zip(tts, tts[1:]))
        secs = [s for s in secs if s > 0]
        return secs[len(secs) // 2] if secs else 60      # 算不出來用小值：不會把覆蓋率灌高

    def _write_day(self, conn, ca, ip: str, day: str, pts: dict[str, dict], ctx: BatchContext, now_text: str, stats: dict) -> None:
        cur = conn.execute("SELECT data FROM capacity_whatsup_perf WHERE host = ? AND day = ?", (ip, day)).fetchone()
        merged: dict[str, dict] = {}
        if cur is not None:
            try:
                old = ca._decode(cur[0])
            except ValueError:
                stats["skipped_existing"] += 1       # 壞掉或讀不懂的既有列：不動它
                return
            if old.get("src") != "relay":
                stats["skipped_existing"] += 1       # 不是本模組寫的（例如 REST 取回的）：不覆蓋
                return
            rescale_legacy_perf(old)                 # 2.047.6 以前的小數刻度舊列：先換成 0–100 再合併（遷移沒跑到也不會混刻度）
            for metric in ("cpu_busy", "mem_used"):
                col, mx, mn = (old["cols"].get(metric) or []), ((old.get("max") or {}).get(metric) or []), ((old.get("min") or {}).get(metric) or [])
                for i, tt in enumerate(old["ts"]):
                    a = col[i] if i < len(col) else None
                    b = mx[i] if i < len(mx) else None
                    c = mn[i] if i < len(mn) else None
                    if a is not None or b is not None or c is not None:
                        merged.setdefault(tt, {})[metric] = (a, b, c)
        for tt, ms in pts.items():
            for metric, val in ms.items():
                merged.setdefault(tt, {})[metric] = val          # 新批次同時間點蓋舊的
        tts = sorted(merged)
        if not tts:
            return
        cols: dict[str, list] = {}
        mxs: dict[str, list] = {}
        mns: dict[str, list] = {}
        for metric in ("cpu_busy", "mem_used"):
            vals = [merged[t].get(metric, (None, None, None)) for t in tts]
            if any(v[0] is not None or v[1] is not None for v in vals):
                cols[metric] = [v[0] for v in vals]
                mxs[metric] = [v[1] for v in vals]
                mns[metric] = [v[2] for v in vals]
        if not cols:
            return
        itv = self._interval(tts)
        v, _cov = ca._validate_day(ip, day, {"ts": tts, "cols": cols, "max": mxs, "interval_s": itv, "kind": "bucket", "tz": "UTC"}, 0)
        if v is None:
            raise RelayReject("bad_data", f"{ip} {day} 的資料整理後不合容量分析的格式")
        row = dict(v["row"])
        row.update({"min": mns, "src": "relay", "batch": ctx.batch_id, SCALE_MARK: "percent"})
        blob = gzip.compress(json.dumps(row, separators=(",", ":")).encode())
        conn.execute("INSERT OR REPLACE INTO capacity_whatsup_perf (host, day, interval_s, samples, data, updated_at) VALUES (?,?,?,?,?,?)",
                     (ip, day, v["itv"], len(tts), blob, now_text))
        stats["host_days"] += 1
        stats["points"] += len(pts)

    # ---- 磁碟：另表
    def _write_disk(self, conn, ip: str, day: str, label: str, pts: dict[str, tuple], ctx: BatchContext, now_text: str, stats: dict) -> None:
        cur = conn.execute("SELECT data FROM relay_disk_util WHERE host = ? AND day = ? AND disk = ?", (ip, day, label)).fetchone()
        merged: dict[str, tuple] = {}
        if cur is not None:
            try:
                old = json.loads(gzip.decompress(cur[0]).decode())
                rescale_legacy_disk(old)             # 2.047.6 以前的小數刻度舊列：先換成 0–100 再合併
                for i, tt in enumerate(old["ts"]):
                    merged[tt] = (old["avg"][i], old["max"][i], old["min"][i])
            except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
                merged = {}                          # 本表只有本模組寫，壞掉就由新資料重建
        merged.update(pts)
        tts = sorted(merged)
        data = {"ts": tts, "avg": [merged[t][0] for t in tts], "max": [merged[t][1] for t in tts], "min": [merged[t][2] for t in tts], "tz": "UTC", SCALE_MARK: "percent"}
        conn.execute("INSERT OR REPLACE INTO relay_disk_util (host, day, disk, interval_s, samples, data, batch_id, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (ip, day, label, self._interval(tts), len(tts), gzip.compress(json.dumps(data, separators=(",", ":")).encode()), ctx.batch_id, now_text))
        stats["disk_series"] += 1


class UnityAdapter(Adapter):
    """Dell Unity（儲存設備）：中繼 PC 跑 unity_collect.ps1（開發協作頁 D3，只發 GET），每台陣列一個 unity_<管理位址>.json。

    資料檔：{source, schema_version, collector_version, collected_at, management_host, complete, system[1], pools[], luns[], hosts[]}
    （欄位見 docs/中繼匯入檔案契約.md §5）。驗證與寫入在 storage_relay：每台陣列一份快照（新批次整份換掉該陣列的 Pool／LUN／
    主機／對應）、Pool 每日容量留歷史；主機名對 WEBIT3 機器只用 machine_key，對不到列未匹配，不建資產。
    manifest 沒有 range（腳本 v1 不寫）：快照來源不需要涵蓋區間，視為 collected_at 那一天（read_manifest 依 range_optional 補上）。
    """

    name = "unity"
    label = "儲存設備（Dell Unity）"
    implemented = True
    schema_versions = ("1",)
    note = "每台陣列的 Pool／LUN／主機與主機↔LUN（快照）"
    range_optional = True
    stale_days = 3
    FILE_RE = re.compile(r"^unity_[A-Za-z0-9._-]+[.]json$")

    def parse(self, ctx: BatchContext, filename: str, data: Any) -> dict:
        import storage_relay
        if not self.FILE_RE.match(filename):
            raise RelayReject("bad_data", f"{filename}：Unity 批次的資料檔要是 unity_<管理位址>.json")
        got = parse_time(ctx.collected_at, "Asia/Taipei")
        if got is None or got > datetime.now(timezone.utc) + timedelta(minutes=10):
            raise RelayReject("manifest_invalid", "collected_at 比現在快超過 10 分鐘，不收；請確認中繼 PC 的時鐘")
        try:
            return storage_relay.parse_file(data)
        except ValueError as exc:
            raise RelayReject("bad_data", f"{filename}：{exc}") from None

    def apply(self, conn, ctx: BatchContext, parsed: dict[str, Any], now_text: str) -> dict:
        import storage_relay
        seen: dict[str, str] = {}
        for fname, a in parsed.items():
            if a["serial"] in seen:
                raise RelayReject("bad_data", f"{fname} 與 {seen[a['serial']]} 是同一台陣列（序號 {a['serial']}），一批每台只能一檔")
            seen[a["serial"]] = fname
        st = storage_relay.apply_arrays(conn, list(parsed.values()), ctx.collected_at, ctx.batch_id, now_text)
        note = (f"陣列 {st['devices']} 台、Pool {st['pools']}、LUN {st['luns']}、主機 {st['hosts']}（對到 {st['matched']}、"
                f"對不到 {st['unmatched']}）、主機↔LUN {st['maps']}")
        if st["older_skipped"]:
            note += f"；比現有資料舊而略過 {st['older_skipped']} 台"
        if st["accepted_empty"]:
            note += f"；管理員已接受空快照：{'、'.join(st['accepted_empty'])}"
        st["accepted_empty"] = len(st["accepted_empty"])
        return {**st, "skipped_existing": st["older_skipped"], "note": note}


class CertAdapter(Adapter):
    """憑證到期探測（2.042.02）：中繼 PC 的 cert_collect.ps1 對探測清單上的端點做只讀 TLS 握手，每個端點一檔。

    資料檔：{"endpoint", "port", "sni", "ok", "error", "subject", "issuer", "serial", "sha256",
             "not_before_utc", "not_after_utc", "san": [...]}
    只收這些欄位，其他一律忽略（不存憑證本體、不存私鑰；私鑰／token 樣式的內容整批拒收，見 find_secret）。
    寫入：對到憑證項目（啟用中、端點＋埠＋SNI 相同）→ certs.record_probe（cert_probe_observation＋cert_probe_status，不寫人工觀測表）；
          對不到或對到多筆 → 列入「未匹配」，**不自動建項目**。探測值不覆蓋人工到期日（憑證清單只標「探測不一致」）。
    """

    name = "cert"
    label = "憑證到期探測"
    implemented = True
    schema_versions = ("1",)
    note = "中繼 PC 對憑證清單端點做只讀 TLS 握手的結果"
    _HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$")
    _HEX = re.compile(r"^[0-9A-Fa-f]{1,128}$")
    _SHA = re.compile(r"^[0-9A-Fa-f]{64}$")
    TEXT_MAX = 500
    #: collected_at 最多容許比現在快幾分鐘（中繼 PC 時鐘稍快可以，太未來的批次會變成「最新」而讓之後的批次都蓋不過它）
    FUTURE_TOLERANCE = timedelta(minutes=10)

    def _host(self, filename: str, v: Any, what: str, allow_empty: bool = False) -> str:
        import ipaddress
        if v is None and allow_empty:
            return ""
        if not isinstance(v, str):
            raise RelayReject("bad_data", f"{filename}：{what} 要是文字")
        h = v.strip().lower()
        if not h and allow_empty:
            return ""
        try:
            return str(ipaddress.ip_address(h.strip("[]")))
        except ValueError:
            pass
        if not self._HOST.match(h) or ".." in h:
            raise RelayReject("bad_data", f"{filename}：{what} 不是合法的主機名或 IP")
        return h

    def _text(self, filename: str, v: Any, what: str) -> str:
        if v is None:
            return ""
        if not isinstance(v, str) or len(v) > self.TEXT_MAX or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", v):
            raise RelayReject("bad_data", f"{filename}：{what} 要是 {self.TEXT_MAX} 字以內的文字")
        return v.strip()

    def parse(self, ctx: BatchContext, filename: str, data: Any) -> dict:
        got = parse_time(ctx.collected_at)
        if got is None or got > datetime.now(timezone.utc) + self.FUTURE_TOLERANCE:
            raise RelayReject("manifest_invalid", "collected_at 比現在快超過 10 分鐘，不收；請確認中繼 PC 的時鐘")
        if not isinstance(data, dict):
            raise RelayReject("bad_data", f"{filename}：最外層要是物件")
        endpoint = self._host(filename, data.get("endpoint"), "endpoint")
        port = data.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise RelayReject("bad_data", f"{filename}：port 要是 1–65535 的整數")
        sni = self._host(filename, data.get("sni"), "sni", allow_empty=True)
        ok = data.get("ok")
        if type(ok) is not bool:
            raise RelayReject("bad_data", f"{filename}：ok 要是 true 或 false")
        out = {"endpoint": endpoint, "port": port, "sni": sni, "ok": ok, "error": self._text(filename, data.get("error"), "error"),
               "subject": "", "issuer": "", "serial": "", "sha256": "", "not_before_utc": None, "not_after_utc": None, "san": []}
        if not ok:
            if not out["error"]:
                raise RelayReject("bad_data", f"{filename}：ok=false 時要有 error 說明")
            return out
        out["subject"] = self._text(filename, data.get("subject"), "subject")
        out["issuer"] = self._text(filename, data.get("issuer"), "issuer")
        serial = re.sub(r"[\s:]", "", self._text(filename, data.get("serial"), "serial"))
        if serial and not self._HEX.match(serial):
            raise RelayReject("bad_data", f"{filename}：serial 要是十六進位")
        out["serial"] = serial.upper()
        sha = re.sub(r"[\s:]", "", self._text(filename, data.get("sha256"), "sha256"))
        if not self._SHA.match(sha):
            raise RelayReject("bad_data", f"{filename}：sha256 要是 64 個十六進位字元")
        out["sha256"] = sha.lower()
        na = parse_time(data.get("not_after_utc"), "UTC")
        if na is None or not 2000 <= na.year <= 2100:
            raise RelayReject("bad_data", f"{filename}：not_after_utc 不是合法時間（ok=true 時必填）")
        out["not_after_utc"] = na.strftime("%Y-%m-%dT%H:%M:%SZ")
        if data.get("not_before_utc") not in (None, ""):
            nb = parse_time(data.get("not_before_utc"), "UTC")
            if nb is None or not 1990 <= nb.year <= 2100:
                raise RelayReject("bad_data", f"{filename}：not_before_utc 不是合法時間")
            out["not_before_utc"] = nb.strftime("%Y-%m-%dT%H:%M:%SZ")
        san = data.get("san") or []
        if not isinstance(san, list) or len(san) > 500 or not all(isinstance(x, str) and len(x) <= 253 for x in san):
            raise RelayReject("bad_data", f"{filename}：san 要是文字清單（最多 500 項）")
        out["san"] = [x.strip() for x in san if x.strip()][:200]
        return out

    def apply(self, conn, ctx: BatchContext, parsed: dict[str, Any], now_text: str) -> dict:
        import certs
        if not certs.tables_ready(conn):
            certs.ensure_tables(conn)
        got = parse_time(ctx.collected_at) or datetime.now(timezone.utc)
        probed_at = got.strftime("%Y-%m-%dT%H:%M:%SZ")
        stats = {"devices": 0, "host_days": 0, "points": 0, "skipped_existing": 0, "unmatched": 0, "bad_values": 0}
        seen: dict[tuple, str] = {}
        for fname in sorted(parsed):
            r = parsed[fname]
            k = (r["endpoint"], r["port"], r["sni"])
            if k in seen:
                raise RelayReject("bad_data", f"{fname} 與 {seen[k]} 是同一個端點（{r['endpoint']}:{r['port']} {r['sni']}），一批每個端點只能一檔")
            seen[k] = fname
            stats["devices"] += 1
            if not r["ok"]:
                stats["bad_values"] += 1                 # 探測失敗（連不上、逾時、握手失敗）：照樣記在探測狀態裡
            ident = f"{r['endpoint']}:{r['port']}" + (f",{r['sni']}" if r["sni"] else "")
            res, ids = certs.match_probe_item(conn, r["endpoint"], r["port"], r["sni"])
            if res != "matched":
                stats["unmatched"] += 1
                conn.execute("INSERT INTO relay_import_unmatched (source, ident, name, reason, candidates, last_batch, first_seen, last_seen) "
                             "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source, ident) DO UPDATE SET name = excluded.name, reason = excluded.reason, "
                             "candidates = excluded.candidates, last_batch = excluded.last_batch, last_seen = excluded.last_seen",
                             (self.name, ident[:300], (r["subject"] or r["error"])[:80], res, len(ids), ctx.batch_id, now_text, now_text))
                continue
            conn.execute("DELETE FROM relay_import_unmatched WHERE source = ? AND ident = ?", (self.name, ident[:300]))
            if certs.record_probe(conn, ids[0], r, probed_at, ctx.batch_id):
                stats["points"] += 1
            elif r["ok"]:
                stats["skipped_existing"] += 1           # 同一張憑證已經記過（只更新最後看到時間）
        return stats


class WugAlertAdapter(Adapter):
    """WhatsUp 告警快照（2.043.0）：中繼 PC 的 wug_alert_collect.ps1（devkit D7）每分鐘一份（沒變就不寫，每 5 分鐘一份心跳）。

    資料檔：snapshot.json＝{"kind": scan|heartbeat, "group": {群組狀態原樣}, "scan": {complete, pages_read, devices_seen, stopped_reason, …},
             "devices": [{id, name, host_name, ip, worst_state, monitors_ok, monitors: [{name, type, status, enabled}]}]}
    寫入：wug_alert.apply_snapshot → alert_event（一個設備＋監控項目的一次問題一列）。規則（完整掃描才結案、Maintenance 不推…）見 wug_alert.py。
    """

    name = "whatsup_alert"
    label = "WhatsUp 告警狀態"
    implemented = True
    schema_versions = ("1",)
    note = "每分鐘的告警快照（有變化才有批次，另有心跳）"
    FUTURE_TOLERANCE = timedelta(minutes=10)

    def parse(self, ctx: BatchContext, filename: str, data: Any) -> dict:
        import wug_alert
        got = parse_time(ctx.collected_at)
        if got is None or got > datetime.now(timezone.utc) + self.FUTURE_TOLERANCE:
            raise RelayReject("manifest_invalid", "collected_at 比現在快超過 10 分鐘，不收；請確認中繼 PC 的時鐘")
        if filename != "snapshot.json":
            raise RelayReject("bad_data", f"{filename}：告警批次只收 snapshot.json")
        try:
            return wug_alert.parse_snapshot(data)
        except ValueError as exc:
            raise RelayReject("bad_data", f"{filename}：{exc}") from None

    def apply(self, conn, ctx: BatchContext, parsed: dict[str, Any], now_text: str) -> dict:
        import wug_alert
        if len(parsed) != 1:
            raise RelayReject("bad_data", "告警批次只能有一個 snapshot.json")
        snap_at = parse_time(ctx.collected_at).astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
        return wug_alert.apply_snapshot(conn, ctx.batch_id, snap_at, next(iter(parsed.values())), now_text)


class AriaHostAdapter(Adapter):
    """Aria Operations（vCOps）ESXi 主機效能（2.045.0，今日開盤尖峰）：跳板機收盤後跑 aria_probe.ps1，一台一檔。

    資料檔：{"name": ESXi 主機名, "resource_id"?, "metrics": {"cpu|usage_average"|"mem|usage_average"|"mem|swapped_average": {"t": [毫秒], "v": [值]}}}
    manifest：range.from＝那一天（台北），另有 scope（跳板機的主機範圍）、data_until（資料抓到幾點）。
    寫入：opening_peak.apply_batch → opening_peak（一天一台一指標：Current／Max／Avg／Min／點數）；對不到資產的列進未匹配清單，不猜。
    本系統不連 Aria、不連任何主機；只讀中繼資料夾（一般中繼匯入排程，盤中延後）。
    """

    name = "aria_host"
    stale_trading_days = 1          # Q12：每個交易日盤後一批，漏一個交易日就算過期（不等通用的 N 天）
    label = "Aria ESXi 主機效能（開盤尖峰）"
    implemented = True
    schema_versions = ("1",)
    note = "跳板機收盤後匯出的 ESXi 主機 CPU／記憶體／swap（5 分鐘一點）"
    FUTURE_TOLERANCE = timedelta(minutes=10)
    _DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    def _day(self, ctx: BatchContext) -> str:
        if not self._DAY.match(ctx.range_from) or ctx.range_from != ctx.range_to:
            raise RelayReject("manifest_invalid", "range 的 from、to 要是同一天（YYYY-MM-DD）")
        try:
            datetime.strptime(ctx.range_from, "%Y-%m-%d")
        except ValueError:
            raise RelayReject("manifest_invalid", "range 的日期不合法") from None
        return ctx.range_from

    def parse(self, ctx: BatchContext, filename: str, data: Any) -> dict:
        import opening_peak
        got = parse_time(ctx.collected_at)
        if got is None or got > datetime.now(timezone.utc) + self.FUTURE_TOLERANCE:
            raise RelayReject("manifest_invalid", "collected_at 比現在快超過 10 分鐘，不收；請確認跳板機的時鐘")
        try:
            return opening_peak.parse_host(data, self._day(ctx))
        except ValueError as exc:
            raise RelayReject("bad_data", f"{filename}：{exc}") from None

    def apply(self, conn, ctx: BatchContext, parsed: dict[str, Any], now_text: str) -> dict:
        import opening_peak
        hosts = list(parsed.values())
        seen: set[str] = set()
        for h in hosts:
            k = h["name"].lower()
            if k in seen:
                raise RelayReject("bad_data", f"主機 {h['name'][:60]} 在同一批出現兩次（不猜哪一份對）")
            seen.add(k)
        m = ctx.manifest
        scope = m.get("scope") if isinstance(m.get("scope"), str) else ""
        until = m.get("data_until") if isinstance(m.get("data_until"), str) else ""
        lt = m.get("listed_total")
        lt = lt if isinstance(lt, int) and not isinstance(lt, bool) and lt >= 0 else None
        return opening_peak.apply_batch(conn, ctx.batch_id, ctx.collected_at, self._day(ctx), scope[:80], until[:40], hosts, now_text, lt)


class AriaInventoryAdapter(Adapter):
    """Aria Operations（vCOps）VM 清單（2.058.31，取代日常 RVTools 匯入；使用者 11-09 選「B 先上」、「每週一次就好」）。

    跳板機 跑 aria_inventory.ps1 → 複製到 9.26 → relay_push 上傳 → 這裡匯入。五個資料檔：
    vms／hosts／clusters.json（資源清單）、props.json（{資源 id: {屬性鍵: 值}}）、rels.json（cluster_hosts／host_vms）。
    寫入：vrops_mapper.map_snapshot → rvtools_import.import_records（跟 RVTools／Aria API 匯入同一個寫入器）。
    **只寫 VM 清單（vInfo），不寫 vHost／vDatastore／vCPU／vMemory／vDisk 分頁**：那些分頁的 source_key 是「鍵#列序」，
    兩個來源的列序不同會互相蓋掉或重複（例：vCOps 的 vDisk 只有每台總量，會混進 RVTools 的逐顆明細）。
    那些明細照舊由 RVTools 每月更新（儲存容量頁的快照、逐顆磁碟只有 RVTools 有）。
    """

    name = "aria_inventory"
    label = "Aria VM 清單（取代 RVTools 日常匯入）"
    implemented = True
    schema_versions = ("1",)
    note = "跳板機每週匯出的 VM／ESXi／叢集／vCenter（只更新 VM 清單與位置，明細分頁仍看 RVTools）"
    stale_days = 9                     # 每週一次：超過 9 天沒新批次才亮過期
    FILES = ("vms.json", "hosts.json", "clusters.json", "rels.json")     # ＋屬性檔：props.json 或 props_001.json…（v3 分檔）
    PROPS_RE = re.compile(r"^props(_[0-9]{3})?[.]json$")
    LISTS = ("vms.json", "hosts.json", "clusters.json")
    WRITE_KEY = "aria_inventory_write"     # app_settings：'1' 才真的寫進資產；預設只預覽
    MIN_COVERAGE = 90.0                    # 寫入模式：UUID／主機名有值低於 9 成整批不寫

    @staticmethod
    def preview(conn, recs: list[dict]) -> dict:
        """跟現有 RVTools 資料比一比（不寫任何東西）：UUID 對得上幾台、新的幾台、各欄有值比例。"""
        n = len(recs) or 1
        try:
            have = {str(r[0]).strip().lower() for r in conn.execute(
                "SELECT source_key FROM source_record WHERE source = 'vcenter'") if r[0]}
        except sqlite3.Error:
            have = set()
        keyed = [(r.get("vm_uuid") or r.get("moref") or "").strip().lower() for r in recs]
        matched = sum(1 for k in keyed if k and k in have)
        pct = lambda f: round(100 * sum(1 for r in recs if r.get(f)) / n, 1)  # noqa: E731
        return {"vms": len(recs), "matched": matched, "new": len(recs) - matched, "existing_vcenter_rows": len(have),
                "uuid_pct": pct("vm_uuid"), "hostname_pct": pct("dns_name"), "ip_pct": pct("ip"),
                "esxi_pct": pct("esxi_host"), "cluster_pct": pct("cluster"), "vcenter_pct": pct("vi_sdk_server")}

    KEEP_FIELDS = ("esxi_host", "cluster", "datastore", "dns_name", "ip", "os")

    @staticmethod
    def keep_existing(conn, recs: list[dict]) -> None:
        """寫入前補洞（11-09 審查 P2）：source_record 的 payload 是整筆覆蓋，vCOps 這次沒帶的欄位不可以把
        RVTools 的好值蓋成空的 → 沒值的欄位沿用既有那筆。vCenter 名（vi_sdk_server）既有有值就沿用既有：
        vCOps 的 parentVcenter 跟 RVTools 的 VI SDK Server 寫法可能不同，不讓它每週每月來回翻。"""
        for r in recs:
            key = r.get("vm_uuid") or r.get("moref") or r.get("vm_name") or r.get("hostname")
            if not key:
                continue
            row = conn.execute("SELECT payload FROM source_record WHERE source = 'vcenter' AND source_key = ?",
                               (key,)).fetchone()
            if not row:
                continue
            try:
                old = json.loads(row[0] or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(old, dict):
                continue
            for f in AriaInventoryAdapter.KEEP_FIELDS:
                if r.get(f) in (None, "") and old.get(f) not in (None, ""):
                    r[f] = old[f]
            if old.get("vi_sdk_server") not in (None, ""):
                r["vi_sdk_server"] = old["vi_sdk_server"]

    @staticmethod
    def preview_text(pv: dict) -> str:
        return (f"VM {pv['vms']} 台，對得上既有 {pv['matched']}、新的 {pv['new']}；有值比例 UUID {pv['uuid_pct']}%、"
                f"主機名 {pv['hostname_pct']}%、IP {pv['ip_pct']}%、ESXi {pv['esxi_pct']}%、叢集 {pv['cluster_pct']}%、"
                f"vCenter {pv['vcenter_pct']}%")

    def parse(self, ctx: BatchContext, filename: str, data: Any) -> Any:
        if filename not in self.FILES and not self.PROPS_RE.match(filename):
            raise RelayReject("bad_data", f"{filename}：不是 VM 清單批次認得的檔名")
        if filename in self.LISTS:
            if not isinstance(data, list) or not all(isinstance(r, dict) and r.get("identifier") for r in data):
                raise RelayReject("bad_data", f"{filename}：要是資源清單（每筆要有 identifier）")
            return data
        if self.PROPS_RE.match(filename):
            if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
                raise RelayReject("bad_data", "props.json：要是 {資源 id: {屬性鍵: 值}}")
            return {str(k): {str(a): str(b) for a, b in v.items() if b not in (None, "")} for k, v in data.items()}
        if not isinstance(data, dict):
            raise RelayReject("bad_data", "rels.json：要是物件")
        out = {}
        for key in ("cluster_hosts", "host_vms"):
            m = data.get(key) or {}
            if not isinstance(m, dict) or not all(isinstance(v, list) for v in m.values()):
                raise RelayReject("bad_data", f"rels.json：{key} 要是 {{id: [id…]}}")
            out[key] = {str(k): [str(x) for x in v if x] for k, v in m.items()}
        return out

    def apply(self, conn, ctx: BatchContext, parsed: dict[str, Any], now_text: str) -> dict:
        import rvtools_import
        import vrops_mapper as mp
        missing = [f for f in self.FILES if f not in parsed]
        prop_files = [f for f in parsed if self.PROPS_RE.match(f)]
        if not prop_files:
            missing.append("props.json（或 props_001.json…）")
        if missing:
            raise RelayReject("bad_data", f"批次少了 {', '.join(missing)}")
        if not parsed["vms.json"]:
            raise RelayReject("bad_data", "VM 清單是空的（不當成「全部 VM 都不見了」）")
        rels = parsed["rels.json"]
        snap = mp.Snapshot(vms=parsed["vms.json"], hosts=parsed["hosts.json"], clusters=parsed["clusters.json"],
                           props={k: v for f in sorted(prop_files) for k, v in parsed[f].items()})
        snap.cluster_hosts = rels["cluster_hosts"]
        snap.host_vms = rels["host_vms"]
        mapped = mp.map_snapshot(snap)
        recs = mapped["records"]
        st = mapped["stats"]
        pv = self.preview(conn, recs)
        if get_setting(conn, self.WRITE_KEY, "0") != "1":
            # 預設只預覽：vCOps 的屬性鍵還沒在公司實機驗證（vrops_constants 開頭），UUID 對不上會讓每台 VM
            # 多一筆重複資產。先看對得上幾台，使用者同意後才打開寫入。
            return {"vms": st["vms"], "hosts": st["hosts"], "clusters": st["clusters"], "preview": pv,
                    "note": "預覽（未寫入）：" + self.preview_text(pv)}
        if pv["uuid_pct"] < self.MIN_COVERAGE or pv["hostname_pct"] < self.MIN_COVERAGE:
            raise RelayReject("bad_data", f"VM 的 UUID／主機名有值的比例太低（{self.preview_text(pv)}），"
                                          f"整批不寫，免得產生重複資產或用顯示名蓋掉主機名")
        self.keep_existing(conn, recs)
        result = rvtools_import.import_records(conn, recs, {},
                                               source="vcenter/aria-operations", origin="vCenter/Aria Operations（中繼）")
        return {"vms": st["vms"], "hosts": st["hosts"], "clusters": st["clusters"],
                "inserted": result["inserted"], "updated": result["updated"], "pending_review": result["pending_review"],
                "note": (f"VM {st['vms']} 台（新增 {result['inserted']}／更新 {result['updated']}／待確認 {result['pending_review']}）"
                         + (f"；寫入錯誤 {len(result.get('errors') or [])} 筆" if result.get("errors") else ""))}


class AdTimeAdapter(Adapter):
    """AD 對時（2.060.x，使用者 11-10「AD 自動」）：9.26 的 ad_time.ps1 跑 w32tm /monitor，每台 DC 的時間差一批送來。

    資料檔：dcs.json＝{"dcs": [{"name", "ip", "ok", "offset_s", "note"}]}；寫入 ad_time.apply（每批保留，歷史可回看）。
    判定（正常／異常／無資料）在 ad_time.latest，給每日檢查用；沒回應的 DC 一律無資料、不當正常。
    """

    name = "ad_time"
    label = "AD 對時"
    implemented = True
    schema_versions = ("1",)
    note = "9.26 每天量一次每台 DC 的時間差（w32tm /monitor，唯讀）"
    FUTURE_TOLERANCE = timedelta(minutes=10)

    def parse(self, ctx: BatchContext, filename: str, data: Any) -> list:
        import ad_time
        got = parse_time(ctx.collected_at)
        if got is None or got > datetime.now(timezone.utc) + self.FUTURE_TOLERANCE:
            raise RelayReject("manifest_invalid", "collected_at 解析不了或比現在快超過 10 分鐘，不收；請確認 9.26 的時鐘")
        if filename != "dcs.json":
            raise RelayReject("bad_data", f"{filename}：AD 對時批次只收 dcs.json")
        try:
            return ad_time.parse(data)
        except ValueError as exc:
            raise RelayReject("bad_data", f"{filename}：{exc}") from None

    def apply(self, conn, ctx: BatchContext, parsed: dict[str, Any], now_text: str) -> dict:
        import ad_time
        if len(parsed) != 1:
            raise RelayReject("bad_data", "AD 對時批次只能有一個 dcs.json")
        return ad_time.apply(conn, ctx.batch_id, ctx.collected_at, next(iter(parsed.values())), now_text)


class AdUsersAdapter(Adapter):
    """AD 人員名單（2.060.x，使用者 11-10：.14 碰不到 AD、只有 9.26 碰得到 → 9.26 每天 ADSI 唯讀匯出，走同一條中繼送來）。

    資料檔：users.json＝{"columns": [AD 欄名…], "rows": [[字串…], …]}，欄名同 ad_import 的別名（SamAccountName、employeeID、
    DisplayName、mail、department、manager、Enabled…）。寫入：ad_import.import_rows（整份取代，跟 6 資料匯入上傳 CSV 同一條路）。
    防呆：現有名單 ≥50 人時，新名單少於現有 80% 整批拒收（匯出時部門篩選打錯、AD 查詢中斷，不可以把全公司名單洗成幾十人）；
    要真的縮小請用 6 資料匯入手動上傳。
    注意：import_rows 自己會 commit（跟手動上傳共用），所以名單寫入與批次紀錄不是同一個交易；
    中途失敗時名單是整份取代、可重送，重送同一批會再取代一次，結果相同。
    """

    name = "ad_users"
    label = "AD 人員名單"
    implemented = True
    schema_versions = ("1",)
    note = "9.26 每天唯讀匯出的 AD 人員名單（整份取代）"
    MAX_ROWS = 50000
    SHRINK_RATIO = 0.8
    SHRINK_MIN_CURRENT = 50

    def parse(self, ctx: BatchContext, filename: str, data: Any) -> dict:
        if filename != "users.json":
            raise RelayReject("bad_data", f"{filename}：AD 名單批次只收 users.json")
        if not isinstance(data, dict) or not isinstance(data.get("columns"), list) or not isinstance(data.get("rows"), list):
            raise RelayReject("bad_data", "users.json 要有 columns 與 rows 兩個清單")
        cols = [str(c) for c in data["columns"]]
        low = {c.strip().lower() for c in cols}
        if not ({"employeeid", "displayname"} <= low):
            raise RelayReject("bad_data", "users.json 缺 employeeID 或 DisplayName 欄")
        rows = data["rows"]
        if not rows:
            raise RelayReject("bad_data", "users.json 沒有任何人（空名單不收，免得把現有名單清掉）")
        if len(rows) > self.MAX_ROWS:
            raise RelayReject("bad_data", f"users.json 超過 {self.MAX_ROWS} 列")
        out = []
        for i, r in enumerate(rows, 1):
            if not isinstance(r, list) or len(r) != len(cols):
                raise RelayReject("bad_data", f"users.json 第 {i} 列欄數和 columns 不同")
            out.append(["" if v is None else str(v) for v in r])
        return {"columns": cols, "rows": out}

    def apply(self, conn, ctx: BatchContext, parsed: dict[str, Any], now_text: str) -> dict:
        import ad_import
        if len(parsed) != 1:
            raise RelayReject("bad_data", "AD 名單批次只能有一個 users.json")
        d = next(iter(parsed.values()))
        try:
            cur = conn.execute("SELECT COUNT(*) FROM ad_person").fetchone()[0]
        except sqlite3.OperationalError:     # 還沒匯過任何 AD 名單（表不存在）＝現有 0 人，不做縮小檢查
            cur = 0
        n = len(d["rows"])
        if cur >= self.SHRINK_MIN_CURRENT and n < cur * self.SHRINK_RATIO:
            raise RelayReject("shrink", f"新名單 {n} 人，比現有 {cur} 人少超過兩成，整批不收（請確認匯出範圍；要縮小請到 6 資料匯入手動上傳）")
        counts = ad_import.import_rows(conn, [d["columns"]] + d["rows"], file_name=ctx.batch_id, by="relay")
        counts["note"] = f"AD 名單 {counts['rows']} 人（原 {cur} 人）"
        return counts


class WugLastpollAdapter(Adapter):
    """WhatsUp「上次輪循」群組快照（2.062.0，S13）：9.26 的 wug_lastpoll_collect.ps1 每 30 分鐘一批，給 1.5 每日檢查 CPU／記憶體／磁碟／輪詢列。

    資料檔：snap.json＝{"groups": [{query, path, id, ok, error, members: [{id, name, ip}], metrics: {cpu|mem|disk: {ok, error, rows: [...]}}}]}；
    manifest 帶 counts（每群組成員數與各指標筆數）。寫入 wug_lastpoll.apply；不完整的批次照存但標 complete=0（每日檢查不拿）。
    """

    name = "whatsup_lastpoll"
    label = "WhatsUp 上次輪循"
    implemented = True
    schema_versions = ("1",)
    note = "每 30 分鐘一批：群組成員＋CPU／記憶體／磁碟的「上次輪循」值（每日檢查用）"
    FUTURE_TOLERANCE = timedelta(minutes=10)

    def parse(self, ctx: BatchContext, filename: str, data: Any) -> dict:
        import wug_lastpoll
        got = parse_time(ctx.collected_at)
        if got is None or got > datetime.now(timezone.utc) + self.FUTURE_TOLERANCE:
            raise RelayReject("manifest_invalid", "collected_at 解析不了或比現在快超過 10 分鐘，不收；請確認 9.26 的時鐘")
        if filename != "snap.json":
            raise RelayReject("bad_data", f"{filename}：上次輪循批次只收 snap.json")
        try:
            return wug_lastpoll.parse(data, ctx.manifest)
        except ValueError as exc:
            raise RelayReject("bad_data", f"{filename}：{exc}") from None

    def apply(self, conn, ctx: BatchContext, parsed: dict[str, Any], now_text: str) -> dict:
        import wug_lastpoll
        if len(parsed) != 1:
            raise RelayReject("bad_data", "上次輪循批次只能有一個 snap.json")
        return wug_lastpoll.apply(conn, ctx.batch_id, ctx.collected_at, ctx.collector_version, next(iter(parsed.values())), now_text)


class AriaDailyCheckAdapter(Adapter):
    """VMware 每日檢查快照（2.064.0）：跳板機的 aria_daily_snap.ps1（只對 vROps 做 GET＋登入）每天 06:00／06:20／07:30，給 1.5 每日檢查 VC 列預填。

    資料檔：snap.json＝{aria, adapters, hosts, datastores, alerts}（各 {ok, error, total, rows}）；manifest 帶 counts（各類筆數）、raw（-SaveRaw 的原始回應 sha256）。
    寫入 aria_dailycheck.apply；不完整的批次照存但標 complete=0（每日檢查不拿）。不連 vROps、不連 vCenter／ESXi。
    """

    name = "aria_dailycheck"
    label = "VMware 每日檢查（vROps）"
    implemented = True
    schema_versions = ("1",)
    note = "每天 06:00／06:20／07:30：vCenter 收集器狀態、ESXi 連線／維護、資料存放區剩餘、未解警示（每日檢查 VC 列用）"
    FUTURE_TOLERANCE = timedelta(minutes=10)

    def parse(self, ctx: BatchContext, filename: str, data: Any) -> dict:
        import aria_dailycheck
        got = parse_time(ctx.collected_at)
        if got is None or got > datetime.now(timezone.utc) + self.FUTURE_TOLERANCE:
            raise RelayReject("manifest_invalid", "collected_at 解析不了或比現在快超過 10 分鐘，不收；請確認跳板機的時鐘")
        if filename != "snap.json":
            raise RelayReject("bad_data", f"{filename}：VMware 每日檢查批次只收 snap.json")
        try:
            return aria_dailycheck.parse(data, ctx.manifest)
        except ValueError as exc:
            raise RelayReject("bad_data", f"{filename}：{exc}") from None

    def apply(self, conn, ctx: BatchContext, parsed: dict[str, Any], now_text: str) -> dict:
        import aria_dailycheck
        if len(parsed) != 1:
            raise RelayReject("bad_data", "VMware 每日檢查批次只能有一個 snap.json")
        sha = next((f.get("sha256") for f in ctx.manifest.get("files") or [] if isinstance(f, dict) and f.get("name") == "snap.json"), None)
        raw = ctx.manifest.get("raw")
        raw = [{"name": str(x.get("name"))[:120], "sha256": str(x.get("sha256"))[:64]} for x in raw if isinstance(x, dict)][:500] if isinstance(raw, list) else []
        return aria_dailycheck.apply(conn, ctx.batch_id, ctx.collected_at, ctx.collector_version, next(iter(parsed.values())), now_text, sha, raw)


register(WhatsUpAdapter())
register(UnityAdapter())
register(CertAdapter())
register(WugAlertAdapter())
register(AriaHostAdapter())
register(AriaInventoryAdapter())
register(AdUsersAdapter())
register(AdTimeAdapter())
register(WugLastpollAdapter())
register(AriaDailyCheckAdapter())
