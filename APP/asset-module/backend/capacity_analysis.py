"""證券業分時段容量分析（ACT-04，2.041.40）——唯讀「依據＋限制」，不自動調任何資源。

為什麼要分時段（Codex 103／104 原則）：
  早盤 90%＋離峰 10% 的主機，全天平均只有 ~20%，用平均會被判「閒置」而建議減配，開盤那一刻就爆。
  所以這裡**不輸出全天平均**：每台主機 × 每個時段 × 每種日型（交易日／大量交易日／非交易日）× 資料來源，各自算
  樣本數、覆蓋率、平均、P95、最大值（含發生時間）、時段內最大連續缺口；覆蓋不足／天數不足／間隔太粗的格子不給平均與 P95。

資料來源介面（SOURCES，順序＝偏好）：WhatsUp 歷史（預留，只讀本機表 capacity_whatsup_perf；本模組沒有任何對外連線）、
  NMON 逐分鐘檔（nmon_series）。同一台在同一個日型內，逐日取偏好來源（同一天只用一個來源），不同來源的格子分開列、
  每格標來源，不混算同一格。資料列必須標 kind=raw／bucket（來源預設值：NMON=raw、WhatsUp=bucket），不由是否有 max 猜。
  bucket（彙整桶）一律不算 P95、max 取桶內 max。

設定（app_settings，不進版控）：capacity_slots／capacity_heavy_days／capacity_holidays／capacity_params。
  時段預設只是「範例」；大量交易日、休市日預設空；日期一律正規化存 YYYY-MM-DD。

時間：原始資料可標 tz=UTC，分析時轉 Asia/Taipei 再分時段與日型；沒標則視為已是 Asia/Taipei（限制欄會寫出來）。
  跨午夜時段：午夜後的樣本歸屬「時段開始那天」的日型。
"""
from __future__ import annotations

import gzip
import hashlib
import logging
import json
import math
import re
import sqlite3
import threading
import time
import zlib
from datetime import date, datetime, timedelta
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------- 設定預設值（範例，可改）
#: 預設時段＝使用者提供（2026-11-01）：9:00–13:30 是開盤／早盤時間，其中 9:00–9:30 最重要（priority）。
#: 13:30 之後與 9:00 之前＝「盤前／盤後（其餘時間）」，盤後批次的起訖時間使用者尚未提供，不編；管理員可在設定頁新增。
DEFAULT_SLOTS: list[dict] = [
    {"key": "open", "label": "開盤時段（重點）", "start": "09:00", "end": "09:30", "priority": True},
    {"key": "mid", "label": "盤中", "start": "09:30", "end": "13:30"},
]
EXAMPLE_SLOTS = DEFAULT_SLOTS          # 舊名稱相容
DEFAULT_SLOTS_SOURCE = "使用者提供（2026-11-01）"
DEFAULT_PARAMS: dict[str, Any] = {
    "min_coverage_pct": 80,        # 一個格子的樣本數 ÷ 期望樣本數 低於這個就標「覆蓋不足」、不給數字
    "min_days": 20,                # 一般交易日至少幾天有資料才算夠
        "target_span_days": 90,        # 要支撐的觀察期（三個月）
    "high_pct": 80,                # 只用來標「接近上限」的視覺提示，不是減配／加配門檻
    "wu_read_interval_hours": 24,  # 低頻讀 WhatsUp 彙整結果的最短間隔（小時）；保守預設一天一次，下限 6，不做輪詢
    "wu_manual_cooldown_hours": 1,  # 手動讀取的硬性最短冷卻（小時）：手動只能略過排程等待，不能連打
    "wu_max_hosts": 200,           # 一次最多讀幾台（上限，防止一次拉太多）
    "overview_max_hosts": 20,      # 總覽一次最多算幾台（其餘分頁／單台按需）
}
SLOTS_KEY, HEAVY_KEY, HOLIDAY_KEY, PARAMS_KEY = "capacity_slots", "capacity_heavy_days", "capacity_holidays", "capacity_params"

DAY_TYPES = [("normal", "交易日"), ("off", "非交易日")]          # 大量交易日不是日型，只是事後可選的標註（見 day_type）
DAY_LABEL = dict(DAY_TYPES)
OTHER_KEY, OTHER_LABEL = "_other", "盤前／盤後（其餘時間）"
MAX_BLOB_BYTES = 64 * 1024 * 1024      # 解壓後上限（防壓縮炸彈）
MAX_ARRAY = 200_000                    # 單一陣列長度上限
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d:[0-5]\d")

#: 分析的指標。src：col＝來源的欄；disk_busy＝各碟 busy 取最忙那顆；disk_kbs＝各碟讀＋寫加總（缺任一碟就是不完整）
METRICS: list[dict] = [
    {"key": "cpu_busy", "label": "CPU 忙碌", "unit": "%", "src": "col"},
    {"key": "mem_used", "label": "記憶體使用", "unit": "%", "src": "col"},
    {"key": "swap_used", "label": "換頁空間使用", "unit": "%", "src": "col"},
    {"key": "disk_busy_max", "label": "磁碟忙碌（最忙那顆）", "unit": "%", "src": "disk_busy"},
    {"key": "disk_kbs", "label": "磁碟吞吐（讀＋寫）", "unit": "KB/s", "src": "disk_kbs"},
]
METRIC_KEYS = [m["key"] for m in METRICS]
STATS = ("avg", "p95", "max")

STATIC_LIMITS = [
    {"code": "no_business_latency", "text": "系統沒有收集業務延遲／交易量／回應時間：無法證明資源餘裕不影響交易品質。這份只看資源使用率。"},
    {"code": "tz_assumed", "text": "來源沒標時區的資料（NMON）假設已是 Asia/Taipei；主機時區不同時時段會錯位。標 UTC 的資料會轉成 Asia/Taipei。"},
    {"code": "extreme_day_unknown", "text": "大量交易日無法事先定義，系統以觀測到的尖峰日代替；觀測期間內若沒有發生過極端日，結果不代表極端日承載能力。"},
    {"code": "no_auto_action", "text": "唯讀分析，不自動調整任何資源；增減配需人工依業務與關鍵時段證據判斷。"},
]


# ---------------------------------------------------------------- 設定讀寫
def _get(conn: sqlite3.Connection, key: str):
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return None      # 沒有 app_settings 表（記憶體測試／舊庫）＝沒設定，走範例預設
    if not r or r[0] in (None, ""):
        return None
    try:
        return json.loads(r[0])
    except (TypeError, ValueError):
        return "__corrupt__"      # 有存但壞掉：由 get_config 轉成警示，不默默當沒設定


def norm_date(v: Any, name: str, required: bool = True) -> str | None:
    """只收 YYYY-MM-DD（含補零），驗證是真實日期後以 isoformat 存；空字串、20260908、2026-9-8 一律拒絕。"""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        if required:
            raise ValueError(f"{name}不可為空")
        return None
    s = str(v).strip()
    if not DATE_RE.fullmatch(s):
        raise ValueError(f"{name}要是 YYYY-MM-DD（收到 {s!r}）")
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError as exc:
        raise ValueError(f"{name}不是真實日期（收到 {s!r}）") from exc


def _hhmm_min(s: str) -> int:
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(s or "").strip())
    if not m:
        raise ValueError(f"時間格式要是 HH:MM（收到 {s!r}）")
    return int(m.group(1)) * 60 + int(m.group(2))


def _slot_ranges(slot: dict) -> list[tuple[int, int, bool]]:
    """時段涵蓋的分鐘區間 [start, end)；跨午夜 start > end 切成兩段，第二段（午夜後）標 wrap=True。start==end 視為無效。"""
    a, b = _hhmm_min(slot["start"]), _hhmm_min(slot["end"])
    if a == b:
        raise ValueError(f"時段「{slot.get('label')}」開始與結束不能相同")
    return [(a, b, False)] if a < b else [(a, 1440, False), (0, b, True)]


def validate_slots(slots: Any) -> list[dict]:
    if not isinstance(slots, list) or not slots:
        raise ValueError("時段清單不可為空")
    if len(slots) > 12:
        raise ValueError("時段最多 12 個")
    out, keys, rng = [], set(), []
    for i, s in enumerate(slots):
        if not isinstance(s, dict):
            raise ValueError("時段格式錯誤")
        label = str(s.get("label") or "").strip()
        if not label or len(label) > 20:
            raise ValueError(f"第 {i + 1} 個時段名稱要 1～20 字")
        key = str(s.get("key") or f"s{i + 1}").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{1,24}", key) or key == OTHER_KEY:
            raise ValueError(f"第 {i + 1} 個時段代號只能英數底線（1～24 字）")
        if key in keys:
            raise ValueError(f"時段代號重複：{key}")
        keys.add(key)
        item = {"key": key, "label": label, "start": str(s.get("start")).strip(), "end": str(s.get("end")).strip()}
        if s.get("priority"):
            item["priority"] = True
        for r in _slot_ranges(item):
            for (o, p, lab) in rng:
                if r[0] < p and o < r[1]:
                    raise ValueError(f"時段「{label}」與「{lab}」時間重疊")
            rng.append((r[0], r[1], label))
        out.append(item)
    return out


def _validate_dates(v: Any, name: str) -> list[dict]:
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > 400:
        raise ValueError(f"{name}要是清單（最多 400 筆）")
    seen, out = set(), []
    for it in v:
        d = norm_date(it.get("date") if isinstance(it, dict) else it, f"{name}的日期")
        if d in seen:
            continue
        seen.add(d)
        out.append({"date": d, "note": str(it.get("note") or "")[:60] if isinstance(it, dict) else ""})
    return sorted(out, key=lambda x: x["date"])


# 超標分鐘門檻（使用者 11-13 拍板）：記憶體用 90，跟 NMON 月報同一把尺（nmon_series.THRESHOLDS）；
# 其他 % 指標仍用 high_pct（預設 80）。原本記憶體也用 80，同一台在月報（90）與封存（80）判法不同。
MEM_OVER_PCT = 90


def over_threshold(metric: str, params: dict) -> float:
    return float(MEM_OVER_PCT) if metric == "mem_used" else float(params["high_pct"])


_PARAM_RANGE = {"min_coverage_pct": (1, 100), "min_days": (1, 400), "target_span_days": (7, 800), "high_pct": (1, 100),
                "wu_read_interval_hours": (6, 168), "wu_manual_cooldown_hours": (1, 24), "wu_max_hosts": (1, 2000), "overview_max_hosts": (1, 500)}


def _validate_params(p: Any) -> dict:
    out = dict(DEFAULT_PARAMS)
    if p is None:
        return out
    if not isinstance(p, dict):
        raise ValueError("參數格式錯誤")
    for k, (lo, hi) in _PARAM_RANGE.items():
        if k in p:
            try:
                x = float(p[k])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"參數 {k} 要是數字") from exc
            if not lo <= x <= hi:
                raise ValueError(f"參數 {k} 範圍 {lo}～{hi}")
            out[k] = int(x) if x == int(x) else x
    return out


def get_config(conn: sqlite3.Connection) -> dict:
    warnings: list[str] = []
    slots = _get(conn, SLOTS_KEY)
    saved = slots is not None
    try:
        slots = validate_slots(slots) if saved else [dict(s) for s in DEFAULT_SLOTS]
    except ValueError:
        slots, saved = [dict(s) for s in DEFAULT_SLOTS], False
        warnings.append("已儲存的時段設定損壞，目前改用範例時段；請到「時段設定」重新儲存。")
    try:
        params = _validate_params(_get(conn, PARAMS_KEY))
    except ValueError:
        params = dict(DEFAULT_PARAMS)
        warnings.append("已儲存的參數損壞，目前使用預設值；請到「時段設定」重新儲存。")
    heavy: list[dict] = []
    holidays: list[dict] = []
    try:
        heavy = _validate_dates(_get(conn, HEAVY_KEY), "大量交易日")
    except ValueError:
        warnings.append("已儲存的大量交易日標註損壞，目前視為『沒有標註』（標註只是事後對照，不影響分析結果）；可到「時段設定」重新輸入。")
    try:
        holidays = _validate_dates(_get(conn, HOLIDAY_KEY), "休市日")
    except ValueError:
        warnings.append("已儲存的休市日清單損壞，目前視為『沒有休市日』；請到「時段設定」重新輸入，否則休市日會被當成交易日。")
    try:
        import capacity_markers as mk
        marker_days = mk.annotated_days(conn)
        mopt = mk.options(conn)
    except Exception:  # noqa: BLE001 - 標記表壞掉只影響事後標註，不能讓分析整個失敗
        marker_days, mopt = {}, {"types": [], "reasons": []}
        warnings.append("交易日情境標記讀取失敗，本次分析不含標記日（標註只是事後對照，不影響其他結果）。")
    return {"marker_days": marker_days, "marker_types": mopt["types"], "marker_reasons": mopt["reasons"],
            "slots": slots, "slots_are_example": False, "slots_source": ("管理員設定" if saved else DEFAULT_SLOTS_SOURCE), "unprovided": ["盤後批次的起訖時間（使用者尚未提供）"] if not saved else [], "heavy_days": heavy, "holidays": holidays, "params": params,
            "warnings": warnings, "defaults": {"slots": DEFAULT_SLOTS, "params": DEFAULT_PARAMS}}


def save_config(conn: sqlite3.Connection, body: dict, commit: bool = True) -> dict:
    """整份驗證通過才寫（任何一項錯整批不存）；日期正規化後存。"""
    cur = get_config(conn)
    slots = validate_slots(body.get("slots", cur["slots"]))
    heavy = _validate_dates(body.get("heavy_days", cur["heavy_days"]), "大量交易日")
    holidays = _validate_dates(body.get("holidays", cur["holidays"]), "休市日")
    params = _validate_params(body.get("params", cur["params"]))
    import capacity_markers as mk
    mtypes, mreasons = mk.validate_options(body.get("marker_types", cur["marker_types"] or mk.DEFAULT_TYPES), body.get("marker_reasons", cur["marker_reasons"] or mk.DEFAULT_REASONS))
    both = {h["date"] for h in heavy} & {h["date"] for h in holidays}
    if both:
        raise ValueError(f"同一天不能同時是大量交易日與休市日：{sorted(both)[0]}")
    for k, v in ((SLOTS_KEY, slots), (HEAVY_KEY, heavy), (HOLIDAY_KEY, holidays), (PARAMS_KEY, params),
                 ("capacity_marker_types", mtypes), ("capacity_marker_reasons", mreasons)):
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (k, json.dumps(v, ensure_ascii=False)))
    if commit:
        conn.commit()          # commit=False：由呼叫端（路由）在稽核紀錄也寫好之後一次 commit
    return get_config(conn)


# ---------------------------------------------------------------- 日型
def day_type(day: str, heavy: set[str], holidays: set[str]) -> str:
    """回 heavy／off／normal。heavy＝管理員事後『標註』的大量交易日（優先於 off，人明確指定那天有交易）——分析時 heavy 與 normal 都是交易日，
    heavy 只是標註，用來做「標註日 vs 其他日」對照，不是分析前提；off＝週六日或休市日（選填清單）。"""
    if day in heavy:
        return "heavy"
    if day in holidays:
        return "off"
    try:
        return "off" if date.fromisoformat(day).weekday() >= 5 else "normal"
    except ValueError:
        return "off"


# ---------------------------------------------------------------- 統計
def percentile(sorted_vals: list[float], p: float) -> float:
    """nearest-rank：第 ceil(p/100·n) 小。樣本少時偏保守（取較大值）。"""
    n = len(sorted_vals)
    return sorted_vals[max(1, math.ceil(p / 100 * n)) - 1]


def _stats(vals: list[float]) -> dict:
    s = sorted(vals)
    return {"avg": round(sum(s) / len(s), 2), "p95": round(percentile(s, 95), 2), "max": round(s[-1], 2)}


def _bucket_stats(avgs: list[float], mxs: list, cns: list) -> dict:
    """彙整（bucket）：avg 以筆數加權，max 取各桶最大；**P95 一律不可得**，不平均各桶 P95、也不由 avg／max 推。"""
    weighted = len(cns) == len(avgs) and bool(cns) and all(c for c in cns)
    avg = sum(a * c for a, c in zip(avgs, cns)) / sum(cns) if weighted else sum(avgs) / len(avgs)
    mm = [x for x in mxs if x is not None]
    return {"avg": round(avg, 2), "p95": None, "max": round(max(mm), 2) if mm else None, "p95_note": "不可得：來源是彙整桶，不能推算 P95"}


def _median(vals: list[float]) -> float:
    """一般（未加權）樣本中位數：奇數取中間、偶數取中間兩個的平均。"""
    s_ = sorted(vals)
    n = len(s_)
    return s_[n // 2] if n % 2 else (s_[n // 2 - 1] + s_[n // 2]) / 2


def _num_or_none(x):
    return x if type(x) in (int, float) and x == x and x not in (math.inf, -math.inf) else None


DIST_UNKNOWN = {"not_collected": "未收集", "no_data": "無資料", "coarse_interval": "間隔太粗"}
DIST_KEYS = ("median", "median_note", "over_threshold", "over_min", "over_lo", "over_hi", "over_missing_min", "over_note", "over_text")


def slot_clipper(slot_secs: int):
    """一般時段：取樣點 [t, t＋間隔) 只算落在 [時段起點, 時段終點) 的部分（off＝距時段起點的秒數）。"""
    def clip(off: int, d: float) -> float:
        return max(0.0, min(float(d), float(slot_secs - off)))
    return clip


def other_clipper(slot_ranges_min: list[tuple[int, int]]):
    """「其餘時間」：off＝當天第幾秒；只算 [t, t＋間隔) 中不屬於任何設定時段、且不跨到隔天的部分。"""
    rs = [(a * 60, b * 60) for a, b in slot_ranges_min]

    def clip(off: int, d: float) -> float:
        lo, hi = float(off), min(float(off) + float(d), 86400.0)
        if hi <= lo:
            return 0.0
        inside = sum(max(0.0, min(hi, b) - max(lo, a)) for a, b in rs)
        return max(0.0, (hi - lo) - inside)
    return clip


def merge_same_time(pts: list[tuple]) -> list[tuple]:
    """同一時間（同一個 off 秒）有多個取樣：只留一個代表，時間不重複計（超標分鐘因此不會超過時段長度）。
    代表取保守值：值、桶內最小、桶內最大都取最大的，間隔取最大的，任何一個是推算就算推算。依 off 排序回傳。"""
    by: dict[int, list] = {}
    for p in pts:
        cur = by.get(p[3])
        if cur is None:
            by[p[3]] = list(p)
            continue
        cur[0] = max(cur[0], p[0])
        cur[1] = p[1] if cur[1] is None else (cur[1] if p[1] is None else max(cur[1], p[1]))
        cur[2] = p[2] if cur[2] is None else (cur[2] if p[2] is None else max(cur[2], p[2]))
        cur[4] = max(cur[4] or 0, p[4] or 0) or None
        cur[5] = bool(cur[5] or p[5])
    return [tuple(by[k]) for k in sorted(by)]


def point_durations(pts: list[tuple]) -> list[float | None]:
    """每個取樣點自己代表多久：該點來源列記錄的間隔，但不超過到下一個取樣點的距離（避免重疊重算）；
    來源列沒有間隔（None）就是 None。pts 依 off 排序後計算，回傳順序與排序後相同。"""
    out: list[float | None] = []
    for i, p in enumerate(pts):
        itv = p[4]
        if not itv:
            out.append(None)
            continue
        d = float(itv)
        if i + 1 < len(pts) and pts[i + 1][3] > p[3]:
            d = min(d, float(pts[i + 1][3] - p[3]))
        out.append(d)
    return out


def dist_stats(per_day: dict, is_bucket: bool, status: str, slot_secs: int, threshold: float | None, clip=None, expected_days=None) -> dict:
    """中位數與超標分鐘（2.042.06／07，Codex 封存摘要口徑）。只用真實取樣、依來源自己的取樣間隔換算，不由平均／P95 推，缺的不補 0。

    per_day：{歸屬日: [(值, 桶內最小, 桶內最大, off 秒, 來源列間隔秒, 間隔是否推算), …]}（raw 的最小／最大為 None）。
    off＝距時段起點的秒數（「其餘時間」則是當天第幾秒），clip(off, 秒數)＝把 [t, t＋間隔) 裁到這個時段裡的秒數。
    中位數：raw 且格子狀態 ok（覆蓋達門檻）＝觀測樣本的一般中位數；其他狀態「不知道」；彙整桶「不可得」。
    超標分鐘：嚴格大於門檻（>，等於門檻不算）；每個取樣點代表它自己的間隔（來源列記錄的間隔、但不超過到下一點的距離），
      再裁到時段邊界內。raw：超標點的時間加總；彙整桶：桶內最小 > 門檻＝整桶確定超標、桶內最大 ≤ 門檻＝確定沒超標，其餘不確定 → 下限～上限。
      時段長度 − 所有取樣點涵蓋的時間＝沒有資料的時間，只加進上限，不當成沒超標；沒缺漏且沒有不確定時才給精確值。
      任何一點的間隔是推算的（來源沒寫）→ 不換算分鐘（不把兩個點之間的空白當成已知時間）。
    expected_days：這格應該有資料的日子（與覆蓋率同一個基準）；其中完全沒有取樣的日子，整個時段長度都算「沒有資料」。
      同一時間的多個取樣只算一次（merge_same_time），所以超標上限不會超過（天數 × 時段長度）。"""
    clip = clip or slot_clipper(slot_secs)
    out = {"median": None, "median_note": "", "over_threshold": threshold, "over_min": None, "over_lo": None, "over_hi": None,
           "over_missing_min": None, "over_note": "", "over_text": "不知道"}
    if status in DIST_UNKNOWN:
        why = DIST_UNKNOWN[status]
        out.update(median_note=f"不知道：{why}", over_note=f"不知道：{why}", over_text=f"不知道（{why}）")
        return out
    vals = [p[0] for pts in per_day.values() for p in pts]
    if is_bucket:
        out["median_note"] = "不可得：來源是彙整桶（只有平均／最大值），不能推算中位數"
    elif status == "ok" and vals:
        out["median"] = round(_median(vals), 2)
        out["median_note"] = f"觀測樣本中位數（{len(vals)} 筆，未加權）"
    else:
        out["median_note"] = "不知道：覆蓋不足或天數不足（不以平均或 P95 代替）"
    if threshold is None:
        out.update(over_note="不適用（單位不是百分比）", over_text="不適用")
        return out
    if any(p[5] or not p[4] for pts in per_day.values() for p in pts):
        out.update(over_note="不知道：取樣間隔是推算的（來源沒寫間隔），不能換算成分鐘", over_text="不知道（取樣間隔未知）")
        return out
    lo = hi = miss = 0.0
    days = list(dict.fromkeys(list(expected_days or []) + list(per_day)))
    for day in days:
        pts = merge_same_time(per_day.get(day) or [])
        covered = 0.0
        for p, d in zip(pts, point_durations(pts)):
            v, mn, mx = p[0], p[1], p[2]
            sec = clip(p[3], d)
            covered += sec
            if not is_bucket:
                if v > threshold:
                    lo += sec
                    hi += sec
                continue
            if mn is not None and mn > threshold:
                lo += sec
                hi += sec
            elif mx is not None and mx <= threshold:
                continue
            else:
                hi += sec                       # 桶內有一部分可能超標，不知道多久
        miss += max(0.0, float(slot_secs) - min(covered, float(slot_secs)))
    lo, hi, miss = round(lo / 60, 1), round((hi + miss) / 60, 1), round(miss / 60, 1)
    out.update(over_lo=lo, over_hi=hi, over_missing_min=miss)
    if lo == hi:
        out["over_min"] = lo
        out["over_note"] = f"精確值（> {threshold:g}，依每個取樣點的實際間隔換算、裁到時段邊界）"
        out["over_text"] = f"{lo:g} 分"
    else:
        tail = f"；其中 {miss:g} 分鐘沒有資料" if miss else ""
        why = "彙整桶只知道最小／最大值" if is_bucket else "有缺漏的取樣"
        out["over_note"] = f"只能給範圍：{why}{tail}"
        out["over_text"] = f"至少 {lo:g} 分、至多 {hi:g} 分" + (f"（{miss:g} 分沒有資料）" if miss else "")
    return out


def _decode(blob: bytes) -> dict:
    """gzip JSON；解壓後大小與陣列長度有上限（防壓縮炸彈與超大陣列）。壞資料丟 ValueError。"""
    try:
        d = zlib.decompressobj(31)
        raw = d.decompress(blob, MAX_BLOB_BYTES)
        if d.unconsumed_tail:
            raise ValueError("解壓後超過大小上限")
        if not d.eof:
            raise ValueError("gzip 不完整（尾端缺漏）")      # Codex 1083：圖表讀不開的截斷列，封存／分析也不可當成有效樣本
        obj = json.loads(raw.decode("utf-8"))
    except (zlib.error, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"資料列壞掉：{exc}") from exc
    if not isinstance(obj, dict) or not isinstance(obj.get("ts"), list) or not isinstance(obj.get("cols"), dict):
        raise ValueError("資料列格式錯誤（缺 ts／cols）")
    if len(obj["ts"]) > MAX_ARRAY or any(not isinstance(c, list) or len(c) > MAX_ARRAY for c in obj["cols"].values()):
        raise ValueError("陣列長度超過上限")
    return obj


def _interval_of(ts: list[str], declared: int | None) -> tuple[int | None, bool]:
    """(間隔秒, 是否推算)。來源沒寫間隔時用相鄰取樣的中位數推算，並標推算。"""
    if declared and declared > 0:
        return int(declared), False
    secs = []
    for a, b in zip(ts, ts[1:]):
        try:
            sa = int(a[0:2]) * 3600 + int(a[3:5]) * 60 + int(a[6:8])
            sb = int(b[0:2]) * 3600 + int(b[3:5]) * 60 + int(b[6:8])
        except ValueError:
            continue
        if sb > sa:
            secs.append(sb - sa)
    if not secs:
        return None, True
    secs.sort()
    return secs[len(secs) // 2], True


def _disk_keys(cols: dict, kinds: tuple[str, ...]) -> list[str]:
    return [k for k in cols if k.startswith("disk:") and k.endswith(kinds)]


def _at(arr, i):
    return arr[i] if isinstance(arr, list) and i < len(arr) else None


def _collected(cols: dict, m: dict) -> bool:
    if m["src"] == "col":
        return m["key"] in cols
    return bool(_disk_keys(cols, (":busy",) if m["src"] == "disk_busy" else (":rd", ":wr")))


def _prep_slots(slots: list[dict]) -> list[dict]:
    out = []
    for s in slots:
        s = dict(s)
        s["_r"] = _slot_ranges(s)
        s["_len"] = sum(b - a for a, b, _ in s["_r"])
        s["_start"] = _hhmm_min(s["start"])
        out.append(s)
    return out


def _slot_of(minute: int, slots: list[dict]) -> tuple[str, bool]:
    for s in slots:
        for a, b, wrap in s["_r"]:
            if a <= minute < b:
                return s["key"], wrap
    return OTHER_KEY, False


# ---------------------------------------------------------------- 資料來源介面
class _TableSource:
    name, label, table, default_kind = "", "", "", "raw"

    def hosts(self, conn: sqlite3.Connection) -> list[str]:
        try:
            return [r[0] for r in conn.execute(f"SELECT DISTINCT host FROM {self.table} ORDER BY host")]
        except sqlite3.OperationalError:
            return []      # 來源表不存在＝這個來源沒資料（WhatsUp 預留表尚未建立時就是這樣）

    def rows(self, conn: sqlite3.Connection, host: str, frm: str | None, to: str | None) -> list[tuple]:
        """日期篩選前後各擴一天：UTC 來源轉台北後的日子可能落在 UTC 前一天／後一天；精確裁切在分析時做。"""
        q, a = f"SELECT day, interval_s, data FROM {self.table} WHERE host = ?", [host]
        if frm:
            q += " AND day >= ?"
            a.append((date.fromisoformat(frm) - timedelta(days=1)).isoformat())
        if to:
            q += " AND day <= ?"
            a.append((date.fromisoformat(to) + timedelta(days=1)).isoformat())
        try:
            return conn.execute(q + " ORDER BY day", a).fetchall()
        except sqlite3.OperationalError:
            return []      # 同上：沒表就沒歷史

    def latest_day(self, conn: sqlite3.Connection, host: str | None = None) -> str | None:
        try:
            r = conn.execute(f"SELECT MAX(day) FROM {self.table}" + (" WHERE host = ?" if host else ""), (host,) if host else ()).fetchone()
        except sqlite3.OperationalError:
            return None      # 沒表＝沒資料
        return r[0] if r else None


class NmonSource(_TableSource):
    name, label, table, default_kind = "nmon", "NMON 逐分鐘檔（使用者上傳）", "nmon_series", "raw"


class WhatsUpHistorySource(_TableSource):
    name, label, table, default_kind = "whatsup", "WhatsUp Gold 效能歷史（唯讀匯入，預留）", "capacity_whatsup_perf", "bucket"


SOURCES: list[_TableSource] = [WhatsUpHistorySource(), NmonSource()]     # 順序＝偏好：WhatsUp 優先，NMON 補充
_SRC = {s.name: s for s in SOURCES}


def list_hosts(conn: sqlite3.Connection) -> list[str]:
    return sorted({h for src in SOURCES for h in src.hosts(conn)})


def latest_data_day(conn: sqlite3.Connection) -> str | None:
    ds = [d for d in (s.latest_day(conn) for s in SOURCES) if d]
    return max(ds) if ds else None


# ---------------------------------------------------------------- 主機對照（device → 資產）
# 不得只靠名稱或 IP 自動綁定：名稱／IP 只當「候選」；正式對應存在 capacity_device_map（帶生效起訖日），
# 多重或衝突列「未對應」、不猜。VIP 不等於實體資產。
MAP_DDL = ("CREATE TABLE IF NOT EXISTS capacity_device_map (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, device TEXT NOT NULL, "
           "asset_serial TEXT NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT, note TEXT, created_by TEXT, created_at TEXT)")
MAP_IDX = "CREATE UNIQUE INDEX IF NOT EXISTS idx_capmap_unique ON capacity_device_map(source, device, valid_from)"


def _short(h: str | None) -> str:
    return str(h or "").strip().lower().split(".")[0]


def candidates(conn: sqlite3.Connection, device: str) -> list[dict]:
    """名稱（短主機名）或 IP 相同的資產——只是候選，不代表已對應。"""
    try:
        rows = conn.execute("SELECT asset_serial, hostname, ip, environment, asset_purpose FROM hardware").fetchall()
    except sqlite3.OperationalError:
        return []      # 沒有資產表＝沒有候選
    k = _short(device)
    return [{"asset_serial": r[0], "hostname": r[1], "ip": r[2], "environment": r[3], "purpose": r[4]} for r in rows
            if k and (_short(r[1]) == k or (r[2] or "").strip() == device.strip())]


def _asset(conn: sqlite3.Connection, serial: str) -> dict | None:
    try:
        r = conn.execute("SELECT asset_serial, hostname, ip, environment, asset_purpose FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
    except sqlite3.OperationalError:
        return None      # 沒有資產表＝查不到
    return {"asset_serial": r[0], "hostname": r[1], "ip": r[2], "environment": r[3], "purpose": r[4]} if r else None


_MAP_COLS = ("id", "source", "device", "asset_serial", "valid_from", "valid_to", "note", "created_by", "created_at")


def mappings(conn: sqlite3.Connection, source: str | None = None, device: str | None = None, asof: str | None = None) -> list[dict]:
    q, a = "SELECT id, source, device, asset_serial, valid_from, valid_to, note, created_by, created_at FROM capacity_device_map WHERE 1=1", []
    if source:
        q += " AND source = ?"
        a.append(source)
    if device:
        q += " AND device = ?"
        a.append(device)
    if asof:
        q += " AND valid_from <= ? AND (valid_to IS NULL OR valid_to = '' OR valid_to >= ?)"
        a += [asof, asof]
    try:
        rows = conn.execute(q + " ORDER BY source, device, valid_from", a).fetchall()
    except sqlite3.OperationalError:
        return []      # 對照表尚未建立＝沒有任何人工對應
    return [dict(zip(_MAP_COLS, r)) for r in rows]


def _overlap(a_from: str, a_to: str | None, b_from: str, b_to: str | None) -> bool:
    return a_from <= (b_to or "9999-12-31") and b_from <= (a_to or "9999-12-31")


def save_mapping(conn: sqlite3.Connection, source: str, device: str, asset_serial: str, valid_from: str, valid_to: str | None = None,
                 note: str = "", by: str | None = None, commit: bool = True) -> dict:
    """先正規化（device strip、日期 isoformat）再查重疊；查詢與寫入在同一個 BEGIN IMMEDIATE 交易內，另有唯一索引兜底。"""
    if source not in _SRC:
        raise ValueError("來源不認得")
    device, asset_serial = str(device or "").strip(), str(asset_serial or "").strip()
    if not device or not asset_serial:
        raise ValueError("device 與資產序號都要填")
    if len(device) > 128 or len(asset_serial) > 64:
        raise ValueError("device 最長 128 字、資產序號最長 64 字")
    vf = norm_date(valid_from, "生效日")
    vt = norm_date(valid_to, "結束日", required=False)
    if vt and vt < vf:
        raise ValueError("結束日不可早於生效日")
    have_hw = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'hardware'").fetchone()
    if have_hw and not _asset(conn, asset_serial):
        raise ValueError("找不到這個資產序號")
    conn.execute(MAP_DDL)
    conn.execute(MAP_IDX)
    own_tx = not conn.in_transaction
    if own_tx:
        conn.execute("BEGIN IMMEDIATE")
    try:
        for m in mappings(conn, source, device):
            if _overlap(vf, vt, m["valid_from"], m["valid_to"]):
                raise ValueError(f"與既有對應期間重疊（{m['valid_from']}～{m['valid_to'] or '迄今'}）；請先結束舊的再新增")
        conn.execute("INSERT INTO capacity_device_map (source, device, asset_serial, valid_from, valid_to, note, created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (source, device, asset_serial, vf, vt, str(note or "")[:100], by, datetime.now().isoformat(timespec="seconds")))
        if commit and own_tx:
            conn.commit()
    except sqlite3.IntegrityError as exc:
        if own_tx:
            conn.rollback()
        raise ValueError("同一裝置同一生效日已有對應") from exc
    except Exception:
        if own_tx:
            conn.rollback()          # 只回滾自己開的交易；呼叫端已有的交易由呼叫端處理
        raise
    return {"ok": True}


def end_mapping(conn: sqlite3.Connection, map_id: int, valid_to: str, by: str | None = None, commit: bool = True) -> dict:
    """結束（或更正結束日）一筆既有對應；開放式對應要換任就先結束它再新增。結束日不可早於生效日，也不可侵入後面一筆的期間。"""
    vt = norm_date(valid_to, "結束日")
    own_tx = not conn.in_transaction
    if own_tx:
        conn.execute("BEGIN IMMEDIATE")         # 讀取、檢查、更新在同一個交易內，避免兩個人同時改
    try:
        row = next((m for m in mappings(conn) if m["id"] == int(map_id)), None)
        if not row:
            raise LookupError("找不到這筆對應")
        if vt < row["valid_from"]:
            raise ValueError("結束日不可早於生效日")
        for m in mappings(conn, row["source"], row["device"]):
            if m["id"] != row["id"] and _overlap(row["valid_from"], vt, m["valid_from"], m["valid_to"]):
                raise ValueError(f"結束日會與另一筆對應重疊（{m['valid_from']}～{m['valid_to'] or '迄今'}）")
        conn.execute("UPDATE capacity_device_map SET valid_to = ? WHERE id = ?", (vt, row["id"]))
        if commit and own_tx:
            conn.commit()
    except Exception:
        if own_tx:
            conn.rollback()
        raise
    return {"ok": True, "id": row["id"], "valid_to": vt, "was": row["valid_to"], "by": by}


_STANDBY = re.compile(r"備援|備用|\bDR\b|standby|\bHA\b|待命", re.I)


def _resolve(conn: sqlite3.Connection, device: str, source: str, day: str | None, cache: dict | None = None) -> dict:
    """某 device 在某資料日期對應到哪個資產（依生效期）。mapped 時帶 map_id（段落識別用）；未對應時帶 gap_from（這段空窗從哪天開始）。"""
    d = day or datetime.now().date().isoformat()
    allm = mappings(conn, source, device)
    ms = [m for m in allm if m["valid_from"] <= d and (not m["valid_to"] or m["valid_to"] >= d)]
    if not ms:
        ends = [m["valid_to"] for m in allm if m["valid_to"] and m["valid_to"] < d]
        gap = (date.fromisoformat(max(ends)) + timedelta(days=1)).isoformat() if ends else ""
        return {"mapped": False, "asset_serial": None, "gap_from": gap}
    if len(ms) > 1:
        return {"mapped": False, "asset_serial": None, "conflict": True}
    a = _asset(conn, ms[0]["asset_serial"])
    if a is None:
        return {"mapped": False, "asset_serial": ms[0]["asset_serial"], "missing_asset": True}
    return {"mapped": True, "asset_serial": a["asset_serial"], "map_id": ms[0]["id"], "environment": a.get("environment"), "purpose": a.get("purpose"),
            "standby": bool(_STANDBY.search(f"{a.get('environment') or ''} {a.get('purpose') or ''}"))}


def _seg_key(r: dict) -> str:
    """段落識別：資產序號＋對應紀錄編號（A→B→A 的兩段 A 不同編號、不合併）；未對應依空窗起日各自成段。"""
    if r.get("mapped"):
        return f"{r['asset_serial']}#{r['map_id']}"
    if r.get("conflict"):
        return "conflict"
    if r.get("missing_asset"):
        return f"missing:{r.get('asset_serial')}"
    return f"-@{r.get('gap_from') or ''}"


def _context_segments(conn: sqlite3.Connection, device: str, source: str, days: list[str]) -> list[dict]:
    """依資料日期逐日解析資產，連續且同結果的日子併成一段；不得用最後一天套全期。"""
    segs: list[dict] = []
    for d in days:
        r = _resolve(conn, device, source, d)
        key = (r.get("asset_serial"), r.get("mapped"), r.get("conflict"), r.get("missing_asset"))
        if segs and segs[-1]["_k"] == key:
            segs[-1]["to"] = d
        else:
            segs.append({**r, "_k": key, "from": d, "to": d})
    for s in segs:
        s.pop("_k", None)
    return segs


def _host_context(conn: sqlite3.Connection, device: str, source: str, asof: str | None) -> dict:
    """單日 context（相容用）；分析本身用 _context_segments。"""
    cands = candidates(conn, device)
    r = _resolve(conn, device, source, asof)
    r["candidates"] = len(cands)
    if not r["mapped"]:
        r["standby"] = None
        r["reason"] = ("同一時間有多筆對應衝突，列未對應" if r.get("conflict") else "對應的資產序號在資產表找不到" if r.get("missing_asset") else
                       f"尚未人工對應到資產（名稱／IP 候選 {len(cands)} 筆，僅供人工對應參考）：不自動用名稱或 IP 綁定，無法確認用途（是否備援）；VIP 也不等於實體資產")
    return r


# ---------------------------------------------------------------- 單機分析
TZ_OK = {"": "TW", "ASIA/TAIPEI": "TW", "UTC": "UTC"}       # 只接受這幾種；Z、+00:00、其他時區一律拒絕（該列不採用）


def _tt_ok(t: Any) -> str | None:
    """合法時刻（時 0–23、分秒 0–59）才收，回 HH:MM:SS；24:00:00、99:99:99、非字串一律 None。"""
    if not isinstance(t, str) or len(t) < 8 or (len(t) > 8 and t[8] != "."):
        return None
    s = t[:8]
    return s if TIME_RE.fullmatch(s) else None


def _nums_ok(arr: Any, nonneg: bool) -> bool:
    """陣列元素只能是 None 或有限數字（不收字串、布林、NaN、Infinity）；彙整桶的數值另外要求非負。"""
    if not isinstance(arr, list):
        return False
    for x in arr:
        if x is None:
            continue
        if type(x) not in (int, float) or x != x or x in (math.inf, -math.inf) or (nonneg and x < 0):
            return False
    return True


def _row_arrays_ok(d: dict, bucket: bool) -> bool:
    for name in ("cols", "max", "cnt", "exp"):
        part = d.get(name) or {}
        if not isinstance(part, dict):
            return False
        if name != "cols" and not bucket:
            continue                          # raw 列的 max／cnt／exp 不使用
        for arr in part.values():
            if not _nums_ok(arr, nonneg=bucket):
                return False
    return True


def _metric_arrays(cols: dict, mx_cols: dict, m: dict) -> tuple[str, list, list]:
    if m["src"] == "col":
        return "col", [cols.get(m["key"])], [mx_cols.get(m["key"])]
    ks = _disk_keys(cols, (":busy",) if m["src"] == "disk_busy" else (":rd", ":wr"))
    return ("busy" if m["src"] == "disk_busy" else "kbs"), [cols[x] for x in ks], [mx_cols.get(x) for x in ks]


def _prepare_rows(conn: sqlite3.Connection, host: str, frm: str | None, to: str | None, min_cov: float) -> tuple[list[dict], dict, dict]:
    """解開所有來源的列、驗證（壞檔、時區、時刻、數值）、把每個時間戳換成台北日期＋時刻，再『逐台北日、逐指標』挑來源：
    偏好來源要在那一天有該指標的資料且涵蓋率達門檻才採用，否則回退到其他來源（損壞／空／缺指標／低覆蓋的偏好來源不會先占位）。
    回 (準備好的列, 計數器, choose{(台北日, 指標): 來源名})。"""
    cnt = {"corrupt": 0, "bad_tz": 0, "bad_time": 0, "bad_value": 0, "unmarked": 0, "fallback": 0, "dropped": 0, "dups": 0}
    prepared: list[dict] = []
    avail: dict[tuple, dict] = {}            # (來源, 台北日) → {tts, itv, has}
    for rank, src in enumerate(SOURCES):
        for day, declared, blob in src.rows(conn, host, frm, to):
            try:
                d = _decode(blob)
            except ValueError:
                cnt["corrupt"] += 1
                continue
            if not d["ts"]:
                continue
            tz = str(d.get("tz") or "").strip().upper()
            if tz not in TZ_OK:
                cnt["bad_tz"] += 1
                continue
            kind = str(d.get("kind") or "").lower()
            marked = kind in ("raw", "bucket")
            if not marked:
                kind = src.default_kind
            if not _row_arrays_ok(d, kind == "bucket"):
                cnt["bad_value"] += 1
                continue
            tts = [_tt_ok(t) for t in d["ts"]]
            if any(t is None for t in tts):
                cnt["bad_time"] += 1                       # 整列拒絕：有非法時刻的列不計入覆蓋率或樣本
                continue
            itv, inferred = _interval_of(d["ts"], declared)
            utc = TZ_OK[tz] == "UTC"
            conv: list[tuple] = []
            seen: set[str] = set()
            for i, tt in enumerate(tts):
                if tt in seen:
                    cnt["dups"] += 1
                    continue
                seen.add(tt)
                tday, t2 = day, tt
                if utc:
                    z = datetime.fromisoformat(f"{day}T{tt}") + timedelta(hours=8)      # Asia/Taipei 固定 UTC+8（無夏令時間）
                    tday, t2 = z.date().isoformat(), z.strftime("%H:%M:%S")
                conv.append((i, tday, t2))
            if not conv:
                continue
            cols, mx_cols = d["cols"], (d.get("max") or {})
            plans = [(m["key"],) + _metric_arrays(cols, mx_cols, m) for m in METRICS]
            by_day: dict[str, list[int]] = {}
            for i, tday, _ in conv:
                by_day.setdefault(tday, []).append(i)
            row = {"src": src, "rank": rank, "day": day, "d": d, "itv": itv, "inferred": inferred, "kind": kind, "marked": marked, "utc": utc,
                   "conv": conv, "plans": plans, "colset": {m["key"] for m in METRICS if _collected(cols, m)}}
            prepared.append(row)
            for tday, idxs in by_day.items():
                a = avail.setdefault((src.name, tday), {"tts": set(), "itv": None, "has": set(), "mts": {}})
                a["tts"].update(t2 for i, td, t2 in conv if td == tday)
                if itv and (a["itv"] is None or itv < a["itv"]):
                    a["itv"] = itv
                i2t = {i: t2 for i, td, t2 in conv if td == tday}
                for k, pk, arrs, _mx in plans:
                    # Codex 1081：覆蓋率要看「這個指標」有值的時間點，不是整天的時間戳（WhatsUp 記憶體全滿、CPU 只有 1 點時，
                    # CPU 不可算成 100% 覆蓋而壓過 NMON 整天的 CPU）。
                    vt = {i2t[i] for i in idxs if any(isinstance(arr, list) and i < len(arr) and arr[i] is not None for arr in arrs)}
                    if vt:
                        a["has"].add(k)
                        a["mts"].setdefault(k, set()).update(vt)
    # 逐（台北日, 指標）選來源
    choose: dict[tuple, str] = {}
    tdays = sorted({td for (_, td) in avail})
    for td in tdays:
        for m in METRICS:
            k = m["key"]
            cands = []
            for rank, src in enumerate(SOURCES):
                a = avail.get((src.name, td))
                if a and k in a["has"]:
                    cov = min(100.0, 100.0 * len(a["mts"].get(k, ())) * a["itv"] / 86400) if a["itv"] else 0.0
                    cands.append((rank, src.name, cov))
            if not cands:
                continue
            good = [c for c in cands if c[2] >= min_cov]
            choose[(td, k)] = (good[0] if good else max(cands, key=lambda c: (c[2], -c[0])))[1]
        first_any = next((s.name for s in SOURCES if (s.name, td) in avail), None)
        if first_any and choose.get((td, "cpu_busy"), first_any) != first_any:
            cnt["fallback"] += 1                           # 偏好來源這天不可用，改用其他來源
    used = {(s, td) for (td, _k), s in choose.items()}
    cnt["dropped"] = sum(1 for key in avail if key not in used)
    cnt["unmarked"] = sum(1 for r in prepared if not r["marked"])
    return prepared, cnt, choose


def _peak_summary(b: dict, k: str, is_bucket: bool, ann: dict) -> dict:
    """每個（時段×指標）格：期間內最高的前 5 天（依當天該時段最大值，次依 P95、再依平均；P95 不可得就排在後面）、全期最大與發生日、
    前 5 尖峰日最大值的中位數；有人工標註的大量交易日時另給『標註日 vs 其他日』對照。容量判斷看這些，不看平均。"""
    rows = []
    for aday, vs in b["dv"][k].items():
        if is_bucket:
            mxs = [x for x in b["dm"][k].get(aday, []) if x is not None]
            mxv = max(mxs) if mxs else None
            cn = b["dc"][k].get(aday, [])
            avg = sum(v * c for v, c in zip(vs, cn)) / sum(cn) if cn and len(cn) == len(vs) and all(cn) else None
            p95 = None
        else:
            s_ = sorted(vs)
            mxv, p95, avg = s_[-1], percentile(s_, 95), sum(s_) / len(s_)
        rows.append({"date": aday, "max": None if mxv is None else round(mxv, 2), "p95": None if p95 is None else round(p95, 2),
                     "avg": None if avg is None else round(avg, 2), "samples": len(vs), "annotated": ann.get(aday)})
    rows.sort(key=lambda r: (-(r["max"] if r["max"] is not None else -1), -(r["p95"] if r["p95"] is not None else -1), -(r["avg"] if r["avg"] is not None else -1), r["date"]))
    top = rows[:5]
    mx_top = sorted(r["max"] for r in top if r["max"] is not None)
    med = None
    if mx_top:
        n = len(mx_top)
        med = round(mx_top[n // 2] if n % 2 else (mx_top[n // 2 - 1] + mx_top[n // 2]) / 2, 2)
    cmp_ = None
    if ann:
        a_rows = [r for r in rows if r["annotated"] and r["max"] is not None]
        o_rows = [r for r in rows if not r["annotated"] and r["max"] is not None]
        by_kind = {}
        for kd in sorted({r["annotated"] for r in a_rows}):               # 事前預告(pre)／事後補標(post)／舊清單(legacy) 分開，不混成一類
            kr = [r for r in a_rows if r["annotated"] == kd]
            by_kind[kd] = {"days": len(kr), "max": max(r["max"] for r in kr)}
        cmp_ = {"annotated_days": len(a_rows), "annotated_max": max((r["max"] for r in a_rows), default=None),
                "other_days": len(o_rows), "other_max": max((r["max"] for r in o_rows), default=None), "by_kind": by_kind}
    return {"peak_days": top, "period_max": rows[0]["max"] if rows else None, "period_max_day": rows[0]["date"] if rows else None,
            "top5_max_median": med, "annotated_cmp": cmp_}


def analyze_host(conn: sqlite3.Connection, host: str, cfg: dict, frm: str | None = None, to: str | None = None,
                 idx: Any = False) -> dict:
    """回 {host, cells, limits, ...}。cells：每個（來源 × kind × 資產分段 × 日型 × 時段 × 指標）一格——不同來源、raw 與 bucket、
    不同資產段落的資料絕不混在同一格。（idx 參數保留相容，已不使用。）"""
    params = cfg["params"]
    slots = _prep_slots(cfg["slots"])
    slot_by = {s["key"]: s for s in slots}
    other_len = max(0, 1440 - sum(s["_len"] for s in slots))
    other_clip = other_clipper([(a, b) for s in slots for a, b, _w in s["_r"]])
    kinds_of = {h["date"]: "legacy" for h in cfg["heavy_days"]}                 # 舊的手動標註清單
    kinds_of.update(cfg.get("marker_days") or {})                              # 交易日情境標記（事前預告 pre／事後補標 post）
    heavy = set(kinds_of)
    holidays = {h["date"] for h in cfg["holidays"]}
    rows, cnt, choose = _prepare_rows(conn, host, frm, to, params["min_coverage_pct"])

    def newb():
        return {"days": set(), "ts": 0, "v": {k: [] for k in METRIC_KEYS}, "mx": {k: [] for k in METRIC_KEYS}, "cn": {k: [] for k in METRIC_KEYS},
                "ex": {k: [] for k in METRIC_KEYS}, "partial": {k: 0 for k in METRIC_KEYS},
                "off": {k: {} for k in METRIC_KEYS}, "at": {k: (None, None) for k in METRIC_KEYS},
                "dv": {k: {} for k in METRIC_KEYS}, "dm": {k: {} for k in METRIC_KEYS}, "dc": {k: {} for k in METRIC_KEYS},
                "dn": {k: {} for k in METRIC_KEYS}}
    bk: dict[tuple, dict] = {}
    day_itv: dict[tuple, dict] = {}                 # (來源, kind, 段落, 日型) → {歸屬日: 該天實際取樣間隔}
    day_inf: dict[tuple, dict] = {}                 # 同上 → {歸屬日: 間隔是不是推算的（來源沒寫）}
    gdays: dict[tuple, set] = {}
    ann_days: dict[str, str] = {}                   # 被標註為大量交易日的歸屬日 → 標註種類（只做事後對照）
    collected_g: dict[tuple, set] = {}              # (來源, kind, 段落) → 有收集的指標
    total_ts = 0
    interval_inferred = False
    all_intervals: set[int] = set()
    all_days: set[str] = set()
    aday_info: dict[str, tuple] = {}                # 歸屬日 → (來源, 段落識別)
    dt_cache: dict[str, str] = {}
    mcache: dict[int, tuple] = {}
    acache: dict[tuple, str] = {}
    seg_cache: dict[tuple, dict] = {}
    seg_info: dict[str, dict] = {}                  # 段落識別 → resolve 結果

    def seg_of(src_name: str, aday: str) -> str:
        k = (src_name, aday)
        r = seg_cache.get(k)
        if r is None:
            r = seg_cache[k] = _resolve(conn, host, src_name, aday)
        key = _seg_key(r)
        seg_info.setdefault(key, r)
        return key

    for c in rows:
        src, d, itv, kind = c["src"], c["d"], c["itv"], c["kind"]
        cols, plans = d["cols"], c["plans"]
        bucket = kind == "bucket"
        cn_cols, ex_cols = (d.get("cnt") or {}), (d.get("exp") or {})
        mn_cols = (d.get("min") or {}) if bucket else {}
        if not isinstance(mn_cols, dict):
            mn_cols = {}
        interval_inferred = interval_inferred or c["inferred"]
        if itv:
            all_intervals.add(itv)
        for i, day, tt in c["conv"]:
            use = [p for p in plans if choose.get((day, p[0])) == src.name]
            if not use:
                continue                                   # 這個台北日這些指標由別的來源採用
            minute = int(tt[0:2]) * 60 + int(tt[3:5])
            sm = mcache.get(minute)
            if sm is None:
                sk0, wrap0 = _slot_of(minute, slots)
                sm = mcache[minute] = (sk0, wrap0, (minute - slot_by[sk0]["_start"]) % 1440 if sk0 in slot_by else minute)
            sk, wrap, off = sm
            ak = (day, wrap)
            aday = acache.get(ak)
            if aday is None:
                aday = acache[ak] = (date.fromisoformat(day) - timedelta(days=1)).isoformat() if wrap else day      # 午夜後歸屬時段開始那天
            if (frm and aday < frm) or (to and aday > to):
                continue            # 精確裁切用『歸屬日』（SQL 已前後各擴一天）
            dt0 = dt_cache.get(aday) or dt_cache.setdefault(aday, day_type(aday, heavy, holidays))
            if dt0 == "heavy":
                ann_days[aday] = kinds_of.get(aday, "legacy")
            dt = "normal" if dt0 == "heavy" else dt0          # 標註日與一般交易日同屬「交易日」
            seg = seg_of(src.name, aday)
            aday_info.setdefault(aday, (src.name, seg))
            gk = (src.name, kind, seg, dt)
            collected_g.setdefault(gk[:3], set()).update(c["colset"])
            di = day_itv.setdefault(gk, {})
            if itv and (aday not in di or itv < di[aday]):
                di[aday] = itv
            dinf = day_inf.setdefault(gk, {})
            dinf[aday] = dinf.get(aday, False) or bool(c["inferred"])
            gdays.setdefault(gk, set()).add(aday)
            all_days.add(aday)
            total_ts += 1
            bkey = gk + (sk,)
            b = bk.get(bkey)
            if b is None:
                b = bk[bkey] = newb()
            b["days"].add(aday)
            b["ts"] += 1
            for k, pk, arrs, mxarrs in use:
                mnv = None
                if pk == "col":
                    v = _at(arrs[0], i)
                    mxv = _at(mxarrs[0], i) if bucket else None
                    mnv = _num_or_none(_at(mn_cols.get(k), i)) if bucket else None
                    part = False
                else:
                    vs = [_at(a_, i) for a_ in arrs]
                    if not arrs or any(x is None for x in vs):
                        v, mxv, part = None, None, bool(arrs) and any(x is not None for x in vs)      # 缺任一碟＝不完整，不取剩餘碟的值
                    else:
                        mxs = [_at(a_, i) for a_ in mxarrs] if bucket else []
                        mall = bool(mxs) and all(x is not None for x in mxs)
                        part = False
                        mns = [_num_or_none(_at(mn_cols.get(x), i)) for x in _disk_keys(cols, (":busy",) if pk == "busy" else (":rd", ":wr"))] if bucket else []
                        nall = bool(mns) and all(x is not None for x in mns)
                        if pk == "busy":
                            v, mxv, mnv = max(vs), (max(mxs) if mall else None), (max(mns) if nall else None)
                        else:
                            v, mxv, mnv = sum(vs), (sum(mxs) if mall else None), (sum(mns) if nall else None)
                if part:
                    b["partial"][k] += 1
                if v is None:
                    continue
                b["v"][k].append(v)
                b["dv"][k].setdefault(aday, []).append(v)
                b["off"][k].setdefault(aday, []).append(off)
                b["dn"][k].setdefault(aday, []).append((v, mnv, mxv if bucket else None, off * 60 + int(tt[6:8]), itv, bool(c["inferred"])))
                if bucket:
                    b["dm"][k].setdefault(aday, []).append(mxv)
                    b["dc"][k].setdefault(aday, []).append(_at(cn_cols.get(k), i))
                    b["mx"][k].append(mxv)
                    b["cn"][k].append(_at(cn_cols.get(k), i))
                    b["ex"][k].append(_at(ex_cols.get(k), i))
                peak_v = mxv if (bucket and mxv is not None) else v
                cur = b["at"][k]
                if cur[0] is None or peak_v > cur[0]:
                    b["at"][k] = (peak_v, f"{day} {tt}")

    cells: list[dict] = []
    bucketed_ts = 0
    any_bucket = any(g[1] == "bucket" for g in day_itv)
    groups = sorted(day_itv, key=lambda g: (g[0] != SOURCES[0].name, g))
    # 應有日子（2.042.09，審查決定）：分析區間內「每一個」日型相符的日曆日都算應有，不管來源那天有沒有任何資料——
    # 來源整天停擺要算缺漏（覆蓋率下降、超標分鐘的上限增加），不能因為沒有列就當它不存在。
    # 區間＝這台第一天有任何資料起（之前是「還沒開始收」，另有 span_short 限制說明，不算停擺）～呼叫端給的 to（沒給用最後有資料日）。
    # 同一來源的其他資產段落的日子不算這一段的應有日子；某指標那天由其他來源提供也不算停擺（見下方 absent_k）。
    cal_days: dict[str, list[str]] = {}
    if all_days:
        lo_d = max(date.fromisoformat(frm), date.fromisoformat(min(all_days))) if frm else date.fromisoformat(min(all_days))
        hi_d = date.fromisoformat(to) if to else date.fromisoformat(max(all_days))
        dd = lo_d
        while dd <= hi_d:
            ds_ = dd.isoformat()
            t0 = day_type(ds_, heavy, holidays)
            cal_days.setdefault("normal" if t0 == "heavy" else t0, []).append(ds_)
            dd += timedelta(days=1)
    for gk in groups:
        sn, kind, seg, dt = gk
        di = day_itv[gk]
        dset = gdays.get(gk, set())
        ndays = len(dset)
        if not ndays:
            continue
        other_seg = {d_ for g2, ds2 in gdays.items() if g2[0] == sn and g2[1] == kind and g2[3] == dt and g2[2] != seg for d_ in ds2} - dset
        exp_days = sorted((set(cal_days.get(dt, [])) - other_seg) | dset)
        absent_days = [d_ for d_ in exp_days if d_ not in dset]          # 這一段落整天沒有任何資料的應有日子（下面再依指標排除『那天由別的來源提供』）
        colg = collected_g.get(gk[:3], set())
        r_seg = seg_info.get(seg, {})
        asset = r_seg.get("asset_serial") if r_seg.get("mapped") else None
        is_bucket = kind == "bucket"
        min_d = params["min_days"] if dt == "normal" else 1
        for s in slots + [{"key": OTHER_KEY, "label": OTHER_LABEL, "_len": other_len, "_start": 0}]:
            b = bk.get(gk + (s["key"],))
            if b:
                bucketed_ts += b["ts"]
            slot_secs = s["_len"] * 60
            known = [v for v in (di.get(a) for a in dset) if v]
            exp_buckets = sum(int(slot_secs / v) for v in known) if (known and slot_secs) else None
            coarse = bool(s["key"] != OTHER_KEY and slot_secs and known and max(known) > slot_secs / 3)
            for m in METRICS:
                k = m["key"]
                # 整天沒資料、而且那天這個指標也沒有任何其他來源提供＝真的停擺：算進應有點數與「沒有資料」
                absent_k = [d_ for d_ in absent_days if (d_, k) not in choose]
                exp_k = (exp_buckets + len(absent_k) * int(slot_secs / min(known))) if (exp_buckets is not None and absent_k) else exp_buckets
                vals = b["v"][k] if b else []
                cns = [c_ for c_ in (b["cn"][k] if is_bucket and b else []) if c_ is not None]
                exs = [c_ for c_ in (b["ex"][k] if is_bucket and b else []) if c_ is not None]
                have_cnt = is_bucket and bool(vals) and len(cns) == len(vals)
                samples = int(sum(cns)) if have_cnt else len(vals)
                expected = exp_k
                if have_cnt and exp_k and len(exs) == len(vals) and vals:
                    expected = round(exp_k * sum(exs) / len(vals))
                cell = {"source": sn, "kind": kind, "segment": seg, "asset_serial": asset, "day_type": dt, "slot": s["key"], "slot_label": s["label"], "metric": k,
                        "samples": samples, "expected": expected, "days": len(b["days"]) if b else 0, "coverage_pct": None, "status": "", "avg": None,
                        "p95": None, "max": None, "max_at": None, "p95_note": "", "max_gap_min": None, "bucket": is_bucket, "partial": b["partial"][k] if b else 0,
                        "priority": bool(s.get("priority")), "peak_days": [], "period_max": None, "period_max_day": None, "top5_max_median": None, "annotated_cmp": None}
                for dk in DIST_KEYS:
                    cell[dk] = None
                cov = None
                if exp_k:
                    cov = min(100.0, 100 * len(vals) / exp_k)
                    if have_cnt and expected:
                        cov = min(cov, 100.0 * samples / expected)
                    cell["coverage_pct"] = round(cov, 1)
                if slot_secs and k in colg:
                    g = 0
                    for dd in dset:
                        offs = sorted(set((b["off"][k].get(dd) if b else None) or []))
                        pts = [0] + offs + [s["_len"]]
                        g = max(g, max(y - x for x, y in zip(pts, pts[1:])))
                    cell["max_gap_min"] = g
                if k not in colg:
                    cell["status"] = "not_collected"          # 這個來源／kind／段落完全沒有收這個指標
                elif not vals:
                    cell["status"] = "no_data"
                elif coarse:
                    cell["status"] = "coarse_interval"
                elif cov is None:
                    cell["status"] = "no_data"
                elif cov < params["min_coverage_pct"]:
                    cell["status"] = "low_coverage"
                elif s["key"] != OTHER_KEY and ndays < min_d:
                    cell["status"] = "few_days"
                elif is_bucket and not have_cnt:
                    cell["status"] = "no_count"          # 彙整桶缺完整筆數：不能加權平均，只留最大值
                else:
                    cell["status"] = "ok"
                if vals and cell["status"] == "ok":
                    cell.update(_bucket_stats(vals, b["mx"][k], cns) if is_bucket else _stats(vals))
                    cell["max_at"] = b["at"][k][1]
                elif vals and cell["status"] in ("low_coverage", "few_days", "no_count"):
                    mm = [x for x in b["mx"][k] if x is not None] if is_bucket else vals
                    cell["max"] = round(max(mm), 2) if mm else None        # 只留最大值當「至少出現過」的下限證據
                    cell["max_at"] = b["at"][k][1]
                    if is_bucket:
                        cell["p95_note"] = "不可得：來源是彙整桶，不能推算 P95"
                if vals and cell["status"] not in ("coarse_interval", "not_collected", "no_data"):
                    cell.update(_peak_summary(b, k, is_bucket, ann_days))          # 尖峰日排名（資料自己找尖峰；不靠事先定義大量交易日）
                thr = over_threshold(m["key"], params) if m["unit"] == "%" else None
                clip = other_clip if s["key"] == OTHER_KEY else slot_clipper(slot_secs)
                cell.update(dist_stats((b["dn"][k] if b else {}), is_bucket, cell["status"], slot_secs, thr, clip, sorted(dset | set(absent_k))))
                cells.append(cell)

    all_sorted = sorted(all_days)
    days_by_type = {dt: set() for dt, _ in DAY_TYPES}
    for gk_, ds_ in gdays.items():
        days_by_type[gk_[3]].update(ds_)
    span_days = (date.fromisoformat(all_sorted[-1]) - date.fromisoformat(all_sorted[0])).days + 1 if all_sorted else None
    src_names = sorted({g[0] for g in groups}, key=lambda n: [s.name for s in SOURCES].index(n))
    primary = max(src_names, key=lambda n: sum(1 for a, (s_, _) in aday_info.items() if s_ == n)) if src_names else SOURCES[-1].name
    itv_main = min(all_intervals) if all_intervals else None
    collected_any = {k for v in collected_g.values() for k in v}

    limits: list[dict] = list(STATIC_LIMITS)

    def reject_limits():
        if cnt["corrupt"]:
            limits.append({"code": "corrupt_rows", "text": f"{cnt['corrupt']} 筆來源資料列壞掉或超過大小上限，已略過。"})
        if cnt["bad_tz"]:
            limits.append({"code": "bad_tz", "text": f"{cnt['bad_tz']} 筆資料列的時區標記不支援（只接受空白、UTC、Asia/Taipei），已拒絕。"})
        if cnt["bad_time"]:
            limits.append({"code": "bad_time", "text": f"{cnt['bad_time']} 筆資料列含非法時刻（例如 24:00:00、99:99:99），整列拒絕，不計入覆蓋率或樣本。"})
        if cnt["bad_value"]:
            limits.append({"code": "bad_value", "text": f"{cnt['bad_value']} 筆資料列含非數字／NaN／無限大（彙整桶另不得為負數），整列拒絕。"})
    if not all_sorted:
        limits.insert(0, {"code": "no_history", "text": "這台沒有任何歷史資料（或所選期間內沒有）。"})
        reject_limits()
    else:
        if span_days is not None and span_days < params["target_span_days"]:
            limits.insert(0, {"code": "span_short", "text": f"資料只涵蓋 {span_days} 天（{all_sorted[0]}～{all_sorted[-1]}，實際有資料 {len(all_sorted)} 天），不到目標 {params['target_span_days']} 天，不能當三個月證據。"})
        if len(days_by_type["normal"]) < params["min_days"]:
            limits.insert(0, {"code": "few_normal_days", "text": f"一般交易日只有 {len(days_by_type['normal'])} 天有資料（至少 {params['min_days']} 天）。"})
        pri = [c_ for c_ in cells if c_["status"] == "coarse_interval" and c_["priority"] and c_["metric"] == "cpu_busy" and c_["day_type"] == "normal"]
        if pri:
            sl = slot_by[pri[0]["slot"]]
            limits.append({"code": "open_coarse", "text": f"重點時段「{sl['label']}」{sl['start']}–{sl['end']}：來源取樣間隔太粗（需細到約 {max(1, sl['_len'] // 3)} 分鐘以內），間隔太粗無法評估開盤尖峰。"})
        coarse_n = sum(1 for c_ in cells if c_["status"] == "coarse_interval" and c_["metric"] == "cpu_busy" and c_["slot"] != OTHER_KEY)
        if coarse_n:
            limits.append({"code": "coarse_interval", "text": f"{coarse_n} 個時段（來源×日型）的取樣間隔太粗（短於 3 個取樣點，以該格最粗的一天為準），標「間隔太粗無法評估」，不給平均（會被時段外的資料稀釋）。"})
        if any_bucket:
            limits.append({"code": "p95_unavailable", "text": "來源是彙整桶（bucket）：P95 標「不可得」，不由多個桶的 P95 平均或 avg／max 推算；尖峰請看最大值。"})
        nocount = sum(1 for c_ in cells if c_["status"] == "no_count")
        if nocount:
            limits.append({"code": "bucket_no_count", "text": f"{nocount} 格彙整桶缺完整筆數（cnt）：不能加權平均，平均標「不可得」，只留最大值。"})
        if cnt["unmarked"]:
            limits.append({"code": "kind_unmarked", "text": f"{cnt['unmarked']} 筆資料列沒有標明 raw／bucket，已依來源預設處理（NMON＝raw、WhatsUp＝bucket）；請來源補標。"})
        if interval_inferred:
            limits.append({"code": "interval_inferred", "text": "來源沒寫取樣間隔，覆蓋率用相鄰取樣中位數推算。"})
        if len(all_intervals) > 1:
            limits.append({"code": "mixed_interval", "text": f"取樣間隔不一致（{sorted(all_intervals)} 秒）；覆蓋率與間隔判斷按每一天自己的實際間隔。"})
        if len(src_names) > 1 or cnt["fallback"]:
            limits.append({"code": "multi_source", "text": f"期間內用到多個來源（{'、'.join(_SRC[n].label for n in src_names)}）；每個台北日、每個指標只用一個可用來源（{cnt['fallback']} 天因偏好來源損壞／空／缺指標／低覆蓋而改用其他來源），格子分來源列出、不混算。"})
        if cnt["dropped"]:
            limits.append({"code": "source_overlap_dropped", "text": f"{cnt['dropped']} 個（來源×台北日）的資料同日已有更適合的來源，未採用。"})
        reject_limits()
        if cnt["dups"]:
            limits.append({"code": "duplicate_ts", "text": f"{cnt['dups']} 個重複時間戳已略過（不重複計入樣本）。"})
        miss = [m["label"] for m in METRICS if m["key"] not in collected_any]
        if miss:
            limits.append({"code": "metric_not_collected", "text": f"沒收集到：{'、'.join(miss)}（顯示「未收集」，不是 0 也不是正常）。"})
        part = sum(c_["partial"] for c_ in cells)
        if part:
            limits.append({"code": "partial_missing", "text": f"磁碟指標有 {part} 個取樣點缺少部分磁碟的值，不完整的取樣點已排除（不取剩餘磁碟的值冒充完整）。"})
        low = sum(1 for c_ in cells if c_["status"] == "low_coverage" and c_["day_type"] != "off" and c_["slot"] != OTHER_KEY)
        if low:
            limits.append({"code": "low_coverage_cells", "text": f"{low} 格（來源×日型×時段×指標）覆蓋率低於 {params['min_coverage_pct']}%，標「證據不足」，不給平均／P95。"})
        few = sum(1 for c_ in cells if c_["status"] == "few_days" and c_["day_type"] != "off")
        if few:
            limits.append({"code": "few_days_cells", "text": f"{few} 格有資料的天數不足門檻（至少 {params['min_days']} 天），不標正常、只留最大值。"})
        if not any(c_["samples"] for c_ in cells if c_["day_type"] != "off" and c_["slot"] != OTHER_KEY):
            limits.append({"code": "no_slot_overlap", "text": "取樣時間完全落在設定的時段之外（例如只有夜間），沒有任何時段可分析。"})
        limits.append({"code": "mem_disk_scope", "text": "記憶體只看使用率（沒有 available／swap／持續時間）、磁碟容量沒有成長率與預估耗盡日：這些尚未納入，不能據此做記憶體或容量結論。"})

    # 資產分段：段落識別＝資產序號＋對應紀錄編號（A→B→A 是三段，兩段 A 不合併）；未對應期間依空窗起日各自成段
    segs: list[dict] = []
    for a in all_sorted:
        sname, key = aday_info[a]
        r = seg_cache.get((sname, a)) or _resolve(conn, host, sname, a)
        if segs and segs[-1]["segment"] == key:
            segs[-1]["to"] = a
            segs[-1]["days"] += 1
        else:
            segs.append({**{k_: r.get(k_) for k_ in ("mapped", "asset_serial", "environment", "purpose", "standby", "conflict", "missing_asset")},
                         "segment": key, "from": a, "to": a, "days": 1})
    for sg in segs:
        sg["samples"] = sum(c_["samples"] for c_ in cells if c_["metric"] == "cpu_busy" and c_["segment"] == sg["segment"])
    standby_segs = [s_ for s_ in segs if s_.get("standby")]
    unmapped_segs = [s_ for s_ in segs if not s_.get("mapped")]
    cands = len(candidates(conn, host))
    ctx = {"segments": segs, "mapped": bool(segs) and not unmapped_segs, "candidates": cands,
           "standby": True if standby_segs else (None if unmapped_segs or not segs else False)}
    if len(segs) == 1 and segs[0].get("mapped"):
        ctx.update({k_: segs[0].get(k_) for k_ in ("asset_serial", "environment", "purpose")})
    if len(segs) > 1:
        limits.append({"code": "asset_changed", "text": f"這個 device 在期間內分成 {len(segs)} 段對應（" + "；".join(f"{s_['from']}～{s_['to']} {s_.get('asset_serial') or '未對應'}" for s_ in segs) +
                       "）：統計已按段落分開算（cells 的 segment／asset_serial 欄），同一資產不同時期也不合併，未對應期間單獨成段。"})
    if standby_segs:
        rng = "、".join(f"{s_['from']}～{s_['to']}" for s_ in standby_segs)
        limits.insert(0, {"code": "standby_use", "text": f"資產登記顯示 {rng} 可能為備援／DR／HA 用途：只能說『待命期間負載』，不得推論承載能力，也不能據以減配。"})
    if unmapped_segs:
        um_rng = "、".join(s_["from"] + "～" + s_["to"] for s_ in unmapped_segs)
        ctx["reason"] = (f"{um_rng} 尚未人工對應到資產（名稱／IP 候選 {cands} 筆，僅供參考）：不自動用名稱或 IP 綁定，"
                         "無法確認用途（是否備援）；VIP 也不等於實體資產")
        limits.append({"code": "purpose_unknown", "text": ctx["reason"]})
    elif not segs:
        ctx["reason"] = "沒有資料日期可解析對應"
    ok_cells = [c_ for c_ in cells if c_["status"] == "ok" and c_["day_type"] != "off" and c_["slot"] != OTHER_KEY]
    if not ok_cells:
        evidence = "insufficient"
    elif any(l_["code"] in ("span_short", "few_normal_days", "open_coarse", "standby_use", "purpose_unknown", "p95_unavailable",
                            "coarse_interval", "mem_disk_scope", "few_days_cells", "low_coverage_cells", "asset_changed", "bucket_no_count", "kind_unmarked")
             for l_ in limits):
        evidence = "limited"
    else:
        evidence = "adequate"

    peak = None
    for c_ in ok_cells:
        if c_["metric"] == "cpu_busy":
            val, basis = (c_["p95"], "p95") if c_["p95"] is not None else (c_["max"], "max")
            if val is not None and (peak is None or val > peak["value"]):
                peak = {"day_type": c_["day_type"], "slot": c_["slot"], "slot_label": c_["slot_label"], "value": val, "basis": basis, "p95": c_["p95"], "max": c_["max"],
                        "source": c_["source"], "asset_serial": c_["asset_serial"]}
    return {"host": host, "source": primary, "source_label": _SRC[primary].label, "source_table": _SRC[primary].table, "sources": src_names, "context": ctx,
            "from": all_sorted[0] if all_sorted else None, "to": all_sorted[-1] if all_sorted else None,
            "span_days": span_days, "data_days": len(all_sorted), "days_by_type": {k_: len(v_) for k_, v_ in days_by_type.items()}, "annotated_days": sorted(ann_days), "annotated_kinds": dict(sorted(ann_days.items())),
            "interval_s": itv_main, "total_ts": total_ts, "bucketed_ts": bucketed_ts, "cells": cells, "limits": limits,
            "evidence": evidence, "peak_cpu": peak, "error": None}


def evidence_label(e: str) -> str:
    return {"adequate": "無額外資料限制", "limited": "有限制，只能參考", "insufficient": "覆蓋不足", "error": "分析失敗"}.get(e, e)


# ---------------------------------------------------------------- 快取（以資料指紋為鍵）
_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_MAX, _CACHE_TTL = 256, 600


def _db_path(conn: sqlite3.Connection) -> str:
    try:
        r = conn.execute("PRAGMA database_list").fetchone()
        return r[2] if r and r[2] else ""
    except sqlite3.Error:
        return ""


def _fingerprint(conn: sqlite3.Connection, host: str, frm: str | None, to: str | None) -> str:
    """內容級指紋：來源資料列（含 blob 內容）、對應表、相關資產欄位的內容雜湊——同長度改值也會讓快取失效。"""
    h = hashlib.md5()
    for src in SOURCES:
        for day, itv, blob in src.rows(conn, host, frm, to):
            h.update(f"{src.name}|{day}|{itv}|".encode())
            h.update(blob if isinstance(blob, (bytes, bytearray)) else str(blob).encode())
    maps = mappings(conn, None, host)
    h.update(repr(maps).encode())
    assets = {c_["asset_serial"]: c_ for c_ in candidates(conn, host)}
    for m in maps:
        a_ = _asset(conn, m["asset_serial"])
        if a_:
            assets[a_["asset_serial"]] = a_
    h.update(repr(sorted(assets.items())).encode())
    return h.hexdigest()


def analyze_cached(conn: sqlite3.Connection, host: str, cfg: dict, frm: str | None = None, to: str | None = None) -> dict:
    path = _db_path(conn)
    if not path:
        return analyze_host(conn, host, cfg, frm, to)         # 記憶體庫不快取
    cfg_h = hashlib.md5(json.dumps({k: cfg[k] for k in ("slots", "heavy_days", "holidays", "params")}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    key = (path, host, frm, to, cfg_h, _fingerprint(conn, host, frm, to))
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    res = analyze_host(conn, host, cfg, frm, to)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (time.time(), res)
    return res


def _failed_host(host: str, exc: Exception) -> dict:
    _log.warning("capacity analysis failed for %s: %s: %s", host, type(exc).__name__, exc)      # 細節只進日誌，不回給使用者
    return {"host": host, "source": "", "source_label": "", "sources": [], "context": {"mapped": False, "segments": []}, "from": None, "to": None, "span_days": None,
            "data_days": 0, "days_by_type": {"normal": 0, "heavy": 0, "off": 0}, "interval_s": None, "total_ts": 0, "bucketed_ts": 0, "cells": [],
            "limits": [{"code": "analysis_error", "text": "這台分析失敗（錯誤代碼 ANALYSIS_ERROR，細節在伺服器日誌），已略過，不影響其他主機。"}], "evidence": "error", "peak_cpu": None, "error": "ANALYSIS_ERROR"}


def overview(conn: sqlite3.Connection, frm: str | None = None, to: str | None = None, host: str | None = None,
             limit: int | None = None, offset: int = 0) -> dict:
    """預設：期間＝目標觀察天數（結束於最新資料日）、主機一次最多 overview_max_hosts 台（其餘用 offset 分頁或 host= 單台按需）。
    單台壞資料不讓整頁失敗。"""
    cfg = get_config(conn)
    frm = norm_date(frm, "from", required=False)
    to = norm_date(to, "to", required=False)
    if not frm and not to:
        last = latest_data_day(conn)
        if last:
            to = last
            frm = (date.fromisoformat(last) - timedelta(days=cfg["params"]["target_span_days"] - 1)).isoformat()
    allh = [host] if host else list_hosts(conn)
    lim = max(1, min(int(limit or cfg["params"]["overview_max_hosts"]), 500))
    offset = max(0, int(offset or 0))
    page = allh[offset: offset + lim]
    out = []
    for h in page:
        try:
            out.append(dict(analyze_cached(conn, h, cfg, frm, to)))
        except Exception as exc:  # noqa: BLE001 - 單台壞資料只標失敗，不讓整頁 500
            out.append(_failed_host(h, exc))
    for i, h in enumerate(out, offset + 1):
        h["no"] = i              # 項次：依主機名排序的固定編號，不隨前端排序改變
        h["evidence_label"] = evidence_label(h["evidence"])
    return {"config": cfg, "metrics": METRICS, "day_types": [{"key": k, "label": v} for k, v in DAY_TYPES],
            "other_key": OTHER_KEY, "other_label": OTHER_LABEL, "range": {"from": frm, "to": to}, "static_limits": STATIC_LIMITS,
            "sources": source_inventory(conn), "hosts": out, "hosts_total": len(allh), "offset": offset, "limit": lim,
            "truncated": offset + len(page) < len(allh), "demo": False, "wu_refresh_enabled": False}


def reconcile(conn: sqlite3.Connection, max_hosts: int | None = None, budget_s: float | None = None) -> list[dict]:
    """加總＝下鑽：每台各格（所有來源／日型／時段＋其他時間）cpu 取樣點加總，對照來源表以 SQL 獨立數出的取樣點。
    max_hosts／budget_s 讓大資料時可抽樣、總時間有上限（超過就停，不拖長部署關卡）。"""
    cfg = get_config(conn)
    res = []
    t0 = time.time()
    for h in list_hosts(conn)[:max_hosts]:
        if budget_s is not None and time.time() - t0 > budget_s:
            res.append({"host": h, "skipped": True})
            break
        a = analyze_cached(conn, h, cfg)
        db = 0
        for sn in a["sources"]:
            r = conn.execute(f"SELECT COALESCE(SUM(samples),0) FROM {_SRC[sn].table} WHERE host = ?", (h,)).fetchone()    # 表名來自程式內常數
            db += int(r[0])
        res.append({"host": h, "db_samples": db, "analysis_total": a["total_ts"], "bucketed": a["bucketed_ts"],
                    "cells_cpu_samples": sum(c["samples"] for c in a["cells"] if c["metric"] == "cpu_busy" and not c["bucket"])})
    return res


# ---------------------------------------------------------------- WhatsUp 低頻讀取（節流＋退避＋互斥；傳輸層注入，本模組不連網）
WU_LAST_OK, WU_LAST_FAIL, WU_FAILS, WU_LAST_ATTEMPT = "capacity_wu_last_ok", "capacity_wu_last_fail", "capacity_wu_fail_count", "capacity_wu_last_attempt"
WU_HOST_WM, WU_CLAIM, WU_OVERLAP_DAYS = "capacity_wu_host_wm", "capacity_wu_claim", 1
WU_BACKOFF_HOURS = (1, 3, 12, 24)      # 連續失敗後的最短等待（小時），之後維持 24；不會連打
WU_TABLE_SQL = ("CREATE TABLE IF NOT EXISTS capacity_whatsup_perf (host TEXT NOT NULL, day TEXT NOT NULL, interval_s INTEGER, samples INTEGER, "
                "data BLOB NOT NULL, updated_at TEXT, PRIMARY KEY (host, day))")
_WU_LOCK = threading.Lock()


class FixtureAdapter:
    """取數 adapter 的契約與假實作：adapter(hosts, frm, to) → {host: {day: {kind?, interval_s, ts, cols, max?, cnt?, exp?, tz?}}}。
    真實取數方式（REST／唯讀 SQL／WhatsUp 內建排程報表）都**尚未實測驗證**，所以不寫死任一種。"""

    def __init__(self, data: dict):
        self.data = data

    def __call__(self, hosts, frm, to):
        return {h: {d: v for d, v in (self.data.get(h) or {}).items() if frm <= d <= to} for h in hosts}


def _set(conn, k, v):
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, str(v)))


def _getraw(conn, k):
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (k,)).fetchone()
    except sqlite3.OperationalError:
        return None      # 沒設定表＝從沒讀過
    return r[0] if r else None


def _backoff_until(conn) -> datetime | None:
    fail = _getraw(conn, WU_LAST_FAIL)
    if not fail:
        return None
    n = int(_getraw(conn, WU_FAILS) or 1)
    return datetime.fromisoformat(fail) + timedelta(hours=WU_BACKOFF_HOURS[min(n, len(WU_BACKOFF_HOURS)) - 1])


def _claim(conn, now: datetime) -> bool:
    """跨行程互斥：以 app_settings 一列當租約，30 分鐘沒釋放視為過期。"""
    cur = conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value WHERE value < ?",
                       (WU_CLAIM, now.isoformat(timespec="seconds"), (now - timedelta(minutes=30)).isoformat(timespec="seconds")))
    conn.commit()
    return cur.rowcount == 1


def _release(conn):
    conn.execute("UPDATE app_settings SET value = '' WHERE key = ?", (WU_CLAIM,))
    conn.commit()


def _validate_day(host: str, day: Any, d: Any, min_cov: float) -> tuple[dict | None, bool]:
    """回 (可入庫的正規化列 or None, 覆蓋是否足夠)。結構壞 → (None, False)。"""
    if not isinstance(d, dict):
        return None, False
    ts, cols = d.get("ts"), d.get("cols")
    if not isinstance(ts, list) or not isinstance(cols, dict) or not ts or len(ts) > MAX_ARRAY:
        return None, False
    if any(not isinstance(t, str) or not TIME_RE.fullmatch(t[:8]) or len(t) != 8 for t in ts):
        return None, False
    if len(set(ts)) != len(ts):
        return None, False             # 重複時間戳：不可信，不入庫、不算覆蓋
    for name in ("cols", "max", "cnt", "exp"):
        part = d.get(name) or {}
        if not isinstance(part, dict):
            return None, False
        for arr in part.values():
            if not isinstance(arr, list) or len(arr) != len(ts):
                return None, False     # 指標陣列長度與時間軸不符
    itv = d.get("interval_s") or 3600
    if not isinstance(itv, (int, float)) or itv <= 0:
        return None, False
    kind = str(d.get("kind") or "bucket").lower()
    if kind not in ("raw", "bucket"):
        return None, False
    row = {"ts": ts, "cols": cols, "max": d.get("max") or {}, "cnt": d.get("cnt") or {}, "exp": d.get("exp") or {}, "tz": d.get("tz") or "", "kind": kind, "top": {}}
    return {"row": row, "itv": int(itv)}, min(len(ts) * itv, 86400) >= 86400 * min_cov / 100


def in_quiet_window(t: datetime | None = None, holidays: set | None = None) -> bool:
    """盤中靜默窗＝平日開盤時間（沿用 transfer_policy 既有的 MARKET_OPEN～MARKET_CLOSE，不另寫一份；目前 08:30–15:00，休市日可選）。
    窗內任何『自動』動作都不准對外取數：使用者硬性要求，開盤時間除非人為主動，不然不要有自動排查，以免影響線上服務。"""
    import quiet_window as qw
    import transfer_policy as tp
    return qw.is_quiet_time(t or tp.now(), tp.MARKET_OPEN, tp.MARKET_CLOSE, holidays or ())


def wu_refresh(conn: sqlite3.Connection, fetcher, now: datetime | None = None, manual: bool = False, hosts: list[str] | None = None,
               ticket: str | None = None) -> dict:
    """fetcher(hosts, frm, to) → {host: {day: 列}}，列的格式見 FixtureAdapter；**已按桶彙整**，不逐點。
    - 排程：成功後等 wu_read_interval_hours；手動：只能略過排程等待，但有硬性最短冷卻 wu_manual_cooldown_hours（距離上次嘗試），
      且失敗退避兩者都適用，並有互斥鎖（執行緒＋租約）防併發。
    - 游標按主機維護（每台一個 watermark＝最後一個『整日完整』的日子）：每次最多處理 wu_max_hosts 台，**水位最舊的先**（沒抓過的新主機最優先、
      會從 target_span_days 前補抓），所以不會永遠只抓前 N 台；只有該台所有『應有的日子』都回傳、結構正確、覆蓋達門檻才推進該台水位。
    - 結果結構不合（不是 dict、日鍵不是日期、超出範圍、未來日期、列不是 dict）→ 整次視為失敗：記退避、不入庫。
    - 沒有目標主機時不呼叫取數函式。本函式不自帶任何網路程式。"""
    now = now or datetime.now()
    cfg = get_config(conn)
    p = cfg["params"]
    if not _WU_LOCK.acquire(blocking=False):
        return {"ran": False, "reason": "另一個讀取正在進行"}
    claimed = False
    try:
        try:
            claimed = _claim(conn, now)
        except sqlite3.OperationalError:
            claimed = True            # 沒有 app_settings 表（極端情況）：只靠執行緒鎖
        if not claimed:
            return {"ran": False, "reason": "另一個讀取正在進行（其他程序）"}
        import quiet_window as qw     # AUD-02：全站共用的靜默窗（含管理員開關／起訖設定／fail-closed）；休市日仍是同一份 capacity_holidays
        quiet = qw.in_quiet_window(conn, now)
        # 硬守門：manual=True 不代表人為授權——窗內只有持有人工 API 簽發的一次性票證才放行
        human = bool(quiet and ticket and qw.consume_ticket(ticket, "capacity_wu_refresh"))
        if not manual and not human and qw.defer_if_quiet(conn, "capacity_wu_refresh", now):      # 自動路徑：窗內一律延後（並記一筆）
            _set(conn, "capacity_wu_deferred", now.isoformat(timespec="seconds"))        # 窗外第一個 tick 會補跑
            conn.commit()
            return {"ran": False, "deferred": True, "reason": "盤中靜默，延後（平日 08:30–15:00 不自動對外取數）"}
        if quiet and manual and not human:
            return {"ran": False, "needs_confirm": True, "reason": "現在是盤中靜默窗（平日 08:30–15:00）：人為主動取數需要明確二次確認，並會寫稽核；請盡量在非開盤時間執行"}
        if quiet and human:
            _set(conn, "capacity_wu_inwindow_manual", now.isoformat(timespec="seconds"))       # 稽核用：誰在窗內確認由路由寫 activity_log
            conn.commit()
        until = _backoff_until(conn)
        if until and until > now:
            return {"ran": False, "reason": "退避中，稍後再試", "next_allowed": until.isoformat(timespec="seconds")}
        last_attempt = _getraw(conn, WU_LAST_ATTEMPT)
        if last_attempt:
            cool = datetime.fromisoformat(last_attempt) + timedelta(hours=p["wu_manual_cooldown_hours"])
            if manual and cool > now:
                return {"ran": False, "reason": "手動讀取冷卻中", "next_allowed": cool.isoformat(timespec="seconds")}
        ok = _getraw(conn, WU_LAST_OK)
        if ok and not manual:
            nxt = datetime.fromisoformat(ok) + timedelta(hours=p["wu_read_interval_hours"])
            if nxt > now:
                return {"ran": False, "reason": "未到下次讀取時間", "next_allowed": nxt.isoformat(timespec="seconds")}
        all_hosts = sorted(set(hosts if hosts is not None else list_hosts(conn)))
        if not all_hosts:
            return {"ran": False, "reason": "沒有目標主機"}
        to = (now.date() - timedelta(days=1)).isoformat()            # 只抓「已完成」的日子
        floor = (now.date() - timedelta(days=p["target_span_days"])).isoformat()
        try:
            wm = json.loads(_getraw(conn, WU_HOST_WM) or "{}")
            if not isinstance(wm, dict):
                wm = {}
        except ValueError:
            wm = {}      # 水位 JSON 壞掉＝當作都沒抓過（會從 floor 補抓，不會漏）
        order = sorted(all_hosts, key=lambda h: (wm.get(h) or "", h))        # 水位最舊（含沒抓過）的先
        targets = order[: p["wu_max_hosts"]]
        _set(conn, WU_LAST_ATTEMPT, now.isoformat(timespec="seconds"))
        conn.commit()

        def frm_of(h):
            return max(floor, (date.fromisoformat(wm[h]) - timedelta(days=WU_OVERLAP_DAYS)).isoformat()) if wm.get(h) else floor
        groups: dict[str, list[str]] = {}
        for h in targets:
            groups.setdefault(frm_of(h), []).append(h)

        def fail(msg: str) -> dict:
            conn.rollback()
            _set(conn, WU_LAST_FAIL, now.isoformat(timespec="seconds"))
            _set(conn, WU_FAILS, int(_getraw(conn, WU_FAILS) or 0) + 1)
            conn.commit()
            return {"ran": True, "ok": False, "error": msg[:200]}

        stored, incomplete_hosts, advanced = 0, [], []
        try:
            conn.execute(WU_TABLE_SQL)
            for frm, hs in sorted(groups.items()):
                if frm > to:
                    continue
                got = fetcher(hs, frm, to)
                if not isinstance(got, dict):
                    return fail("取數結果不是 {host: {day: 列}}")
                host_days: dict[str, dict] = {}
                for h, days in got.items():
                    if h not in hs:
                        continue                      # 沒要的主機回來的資料不收
                    if not isinstance(days, dict):
                        return fail(f"{h} 的結果不是 {{day: 列}}")
                    for day, d in days.items():
                        if not isinstance(day, str) or not DATE_RE.fullmatch(day):
                            return fail("日期鍵不是 YYYY-MM-DD")
                        try:
                            date.fromisoformat(day)
                        except ValueError:
                            return fail("日期鍵不是真實日期")
                        if day > to or day < frm:
                            return fail("結果含有範圍外或未來的日期")
                        if not isinstance(d, dict):
                            return fail(f"{h} {day} 的列不是物件")
                    host_days[h] = days
                for h in hs:
                    days = host_days.get(h) or {}
                    good: dict[str, bool] = {}
                    for day, d in days.items():
                        # 刻度（2.047.6）：本表一律存 0–100。WhatsUp REST 原生回 0～1 小數；日後真接 REST 取數時，fetcher 要先用
                        # relay_import.to_percent(v, relay_import.WUG_SCALES["fraction"]) 換好再交進來（中繼匯入已這樣做），這裡不再換一次。
                        v, cov_ok = _validate_day(h, day, d, p["min_coverage_pct"])
                        if v is None:
                            good[day] = False
                            continue
                        blob = gzip.compress(json.dumps(v["row"], separators=(",", ":")).encode())
                        conn.execute("INSERT OR REPLACE INTO capacity_whatsup_perf VALUES (?,?,?,?,?,?)",
                                     (h, day, v["itv"], len(v["row"]["ts"]), blob, now.isoformat(timespec="seconds")))
                        stored += 1
                        good[day] = cov_ok
                    # 應有的日子：第一個有回傳的日子（沒有就是 frm）到 to，逐日確認；缺的日子也算不完整
                    start = min(good) if good else frm
                    need = [(date.fromisoformat(start) + timedelta(days=i)).isoformat() for i in range((date.fromisoformat(to) - date.fromisoformat(start)).days + 1)]
                    if good and all(good.get(x) for x in need):
                        wm[h] = to
                        advanced.append(h)
                    else:
                        incomplete_hosts.append(h)
            _set(conn, WU_HOST_WM, json.dumps(wm, ensure_ascii=False))
            _set(conn, WU_LAST_OK, now.isoformat(timespec="seconds"))
            _set(conn, WU_FAILS, 0)
            conn.execute("DELETE FROM app_settings WHERE key = ?", (WU_LAST_FAIL,))
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - 取數、解析、寫入的任何例外都進退避，不連打
            return fail(f"{type(exc).__name__}: {exc}")
        return {"ran": True, "ok": True, "in_quiet_window": quiet, "stored_host_days": stored, "advanced_hosts": sorted(advanced), "incomplete_hosts": sorted(incomplete_hosts),
                "remaining_hosts": len(all_hosts) - len(targets), "all_hosts_current": len(advanced) == len(all_hosts), "range_to": to}
    finally:
        if claimed:
            try:
                _release(conn)
            except sqlite3.Error:
                pass      # 釋放租約失敗不影響結果；30 分鐘後租約自動過期
        _WU_LOCK.release()


# ---------------------------------------------------------------- 資料來源盤點（唯讀、即時從 DB 數）
def source_inventory(conn: sqlite3.Connection) -> list[dict]:
    def one(sql: str, *a):
        try:
            return conn.execute(sql, a).fetchone()
        except sqlite3.OperationalError:
            return None      # 該表在這個資料庫不存在
    nm = one("SELECT COUNT(DISTINCT host), COUNT(*), MIN(day), MAX(day), COALESCE(SUM(samples),0) FROM nmon_series")
    hs = one("SELECT COUNT(DISTINCT ip), COUNT(*), MIN(taken_at), MAX(taken_at) FROM health_snapshot")
    lt = one("SELECT COUNT(DISTINCT asset_serial), COUNT(*), MIN(collected_at), MAX(collected_at) FROM host_metric_latest")
    sd = one("SELECT COUNT(DISTINCT volume_id), COUNT(DISTINCT snap_date), MIN(snap_date), MAX(snap_date) FROM storage_daily")
    wu = one("SELECT COUNT(DISTINCT host), COUNT(*), MIN(day), MAX(day) FROM capacity_whatsup_perf")

    def row(key, label, r, usable, why):
        return {"key": key, "label": label, "available": r is not None, "hosts": r[0] if r else None, "rows": r[1] if r else None,
                "from": r[2] if r else None, "to": r[3] if r else None, "usable_for_slots": usable, "note": why}
    return [
        row("capacity_whatsup_perf", "WhatsUp Gold 效能歷史（首選；預留，尚未接線）", wu, True,
            "WhatsUp 自動取數尚未啟用（沒有任何排程或畫面會觸發）。使用者說明可保存至 90 天（未驗證）；粒度待確認。本系統不連 WhatsUp。"),
        row("nmon_series", "NMON 逐分鐘時間序列（使用者上傳）", nm, True, "CPU／記憶體／換頁／磁碟，逐取樣點，可分時段；靠人上傳，非自動收集。"),
        row("health_snapshot", "每週健康基準快照", hs, False, "每台最多保留 8 份、單點，不能分時段。"),
        row("host_metric_latest", "Push agent 最新指標", lt, False, "每個指標只存最新一筆（覆蓋式），沒有歷史。"),
        row("storage_daily", "儲存卷宗每日容量快照", sd, False, "只有 datastore 容量，與 CPU／記憶體／IO 無關。"),
    ]


# ---------------------------------------------------------------- 示範資料（記憶體內，絕不寫進真實資料庫）
def demo_overview() -> dict:
    """用同一套分析跑在「記憶體內的模擬資料」上。主機名一律 DEMO-，畫面要標「示範」。"""
    import random
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE nmon_series (host TEXT NOT NULL, day TEXT NOT NULL, interval_s INTEGER, samples INTEGER, data BLOB NOT NULL, updated_at TEXT, PRIMARY KEY (host, day))")
    conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    rnd = random.Random(20261101)
    start = date(2026, 8, 3)
    heavy = ["2026-09-04", "2026-09-25"]

    def mins(h, m):
        return h * 60 + m

    def build(day: str, kind: str):
        ts, cpu, mem = [], [], []
        for k in range(0, 1440, 5):
            hh, mm = divmod(k, 60)
            ts.append(f"{hh:02d}:{mm:02d}:00")
            in_early = mins(9, 0) <= k < mins(11, 0)
            if kind == "front":           # 早盤 90%、離峰 ~8%
                base = 88 if in_early else 8
            elif kind == "idle":           # 全天都低
                base = 6
            elif kind == "batch":          # 盤後批次高
                base = 75 if mins(18, 0) <= k < mins(23, 0) else 12
            else:
                base = 30
            if day in heavy and kind == "front":
                base = min(99, base + 8) if in_early else base
            cpu.append(round(max(0, min(100, base + rnd.uniform(-4, 4))), 2))
            mem.append(round(60 + rnd.uniform(-2, 2) + (10 if in_early and kind == "front" else 0), 2))
        return ts, {"cpu_busy": cpu, "mem_used": mem}

    def put(host, kind, day_from, ndays, gap_after=None):
        for i in range(ndays):
            d = day_from + timedelta(days=i)
            if gap_after and d >= gap_after:
                continue
            day = d.isoformat()
            ts, cols = build(day, kind)
            blob = gzip.compress(json.dumps({"ts": ts, "cols": cols, "top": {}}, separators=(",", ":")).encode())
            conn.execute("INSERT INTO nmon_series VALUES (?,?,?,?,?,?)", (host, day, 300, len(ts), blob, None))

    put("DEMO-FRONT01", "front", start, 70)                              # 早盤型：90% 早盤＋8% 離峰
    put("DEMO-IDLE01", "idle", start, 70)                                # 全天都低（但備援與否不明）
    put("DEMO-BATCH01", "batch", start, 70)                              # 盤後批次型
    put("DEMO-SPARSE01", "front", start, 70, gap_after=date(2026, 8, 20))  # 只有 2.5 週資料
    conn.execute("INSERT INTO app_settings VALUES (?,?)", (HEAVY_KEY, json.dumps([{"date": d, "note": "示範"} for d in heavy])))
    conn.commit()
    out = overview(conn)
    out["demo"] = True
    out["sources"] = []
    conn.close()
    return out
