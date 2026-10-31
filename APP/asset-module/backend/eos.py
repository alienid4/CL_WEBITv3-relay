"""EOS/EOL 對照：硬體型號與作業系統版本的官方終止支援日期。

## 資料來源與查詢範圍（2026-08-11 使用者指示）
只查「種類」不查每一筆資產：實際資產有 4000+ 台，但硬體型號跟 OS 版本種類遠少於這個數字
（正規化後約 200 種硬體型號、190 種 OS 版本字串，其中很大一部分是網路設備韌體版本號的
髒資料殘留，還沒被 normalize.py 的規則收斂）。真正查了官方 EOS 的是這裡面「叫得出名字、
官方有公開生命週期頁面」的主流作業系統/軟體與伺服器/網路設備型號，明細與缺口見
`eos_data/README.md`。

## 鐵律：只能是官方查到的日期，查不到就是查不到
不接受用猜的、用經驗值填的日期——資產盤點系統的可信度建立在「每個數字都有出處」上。
查不到官方公告的項目，eos_date 是 null，不會在畫面上冒出一個假日期。

## 兩層資料
- os_eos.json：作業系統／韌體版本 → EOS
- hardware_eos.json：硬體型號（或型號家族）→ EOS

## 比對邏輯：canonical 值可能比 EOS 表的鍵更精確（版本號到小版）
資產的 canonical_os 常常是「Rocky Linux 9.7」，但 EOS 通常以大版為單位公告（Rocky Linux 9）。
比對時先試完全相等，找不到再試「去掉最後一節版本號」的大版比對。
"""
from __future__ import annotations

import json
import pathlib
import re

_DATA_DIR = pathlib.Path(__file__).with_name("eos_data")
_os_table: list[dict] | None = None
_hw_table: list[dict] | None = None


def _load(filename: str) -> list[dict]:
    p = _DATA_DIR / filename
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 資料檔缺失/壞掉不該讓整個 API 掛掉，退回空表
        return []


def _os_entries() -> list[dict]:
    global _os_table
    if _os_table is None:
        _os_table = _load("os_eos.json")
    return _os_table


def _hw_entries() -> list[dict]:
    global _hw_table
    if _hw_table is None:
        _hw_table = _load("hardware_eos.json")
    return _hw_table


def reload_tables() -> None:
    """測試/管理用：清快取，下次查詢重新讀檔。"""
    global _os_table, _hw_table
    _os_table = None
    _hw_table = None


def _version_prefixes(name: str) -> list[str]:
    """版本字串一節一節往回退的候選清單，最精確排最前面。

    granularity 因產品而異：CentOS/RHEL/Rocky/Debian/Oracle Linux 的 EOS 是照「大版」
    公告（CentOS 7），但 Ubuntu/VMware ESXi 的版本識別本身就是兩節（22.04／7.0），
    這裡不猜該用幾節——每一節都生一個候選，交給呼叫端依序試，兩種產品都對得上。

    「CentOS 7.9.2009」→ ["CentOS 7.9", "CentOS 7"]
    「Ubuntu 22.04.4」  → ["Ubuntu 22.04", "Ubuntu 22"]
    「Rocky Linux 9.7」 → ["Rocky Linux 9"]
    """
    m = re.match(r"^(.*?)(?<![\d.])(\d+(?:\.\d+)*)$", name)
    if not m:
        return []
    prefix, ver = m.groups()
    parts = ver.split(".")
    return [f"{prefix}{'.'.join(parts[:n])}" for n in range(len(parts) - 1, 0, -1)]


def lookup_os_eos(canonical_os: str | None) -> dict | None:
    """依 canonical OS 名稱查 EOS。查得到回傳表列的那筆（含 name/eos_date/source_url/note...），
    查不到回 None——前端要能分辨「查過沒有資料」跟「這台還沒收到 OS」。"""
    if not canonical_os:
        return None
    entries = _os_entries()
    by_name = {e["name"]: e for e in entries}
    if canonical_os in by_name:
        return by_name[canonical_os]
    for candidate in _version_prefixes(canonical_os):
        if candidate in by_name:
            return by_name[candidate]
    return None


_HW_STOPWORDS = {
    "series", "switch", "firewall", "router", "appliance", "server", "storage",
    "controller", "mobility",
}


def _hw_tokens(name: str) -> set[str]:
    """比對硬體型號用：切成字詞集合、去泛用字（series/switch/firewall…）。
    用「集合是否互為子集」而不是子字串包含——EOS 表的鍵是「HPE ProLiant DL360 Gen10
    server」這種好讀全名，資產表的 device_model 常是「HPE DL360 Gen10」這種省略廠牌
    產品線的簡寫，中間夾了「ProLiant」就會讓子字串比對失敗，但兩邊的關鍵字詞其實
    都在，用集合子集比對才抓得到。"""
    words = re.findall(r"[a-z0-9]+", name.lower())
    return {w for w in words if w not in _HW_STOPWORDS}


def lookup_hardware_eos(canonical_model: str | None) -> dict | None:
    """依 canonical 硬體型號查 EOS。硬體型號正規化目前規則很薄（大多數是 unmatched、
    canonical＝原值直接輸出，見 normalize.py），資產表的原始寫法五花八門，完全相等
    比對機率很低，所以退而求其次比對關鍵字詞集合。

    ⚠️ 已知限制：Cisco/Juniper 這類用料號式型號（「WS-C2960X-24TDL」）跟 EOS 表好讀
    全名（「Catalyst 2960-X」）斷詞方式差太多，此比對法救不回來，仍會落空——
    這是硬體型號正規化規則本身要補強的問題，不是這支比對函式能解的。
    """
    if not canonical_model:
        return None
    entries = _hw_entries()
    low = canonical_model.lower()
    # 先試完全相等（表列本身就是完整型號時最準）
    for e in entries:
        if e["name"].lower() == low:
            return e
    # ⚠️ 至少要 2 個字詞才夠精確：canonical_model 常常沒被 normalize_model 收斂
    # （method=unmatched，canonical＝原值原樣），萬一原值只剩「Cisco」這種泛用廠牌字，
    # 子集比對會讓每一筆名字裡有「cisco」的 EOS 項目都算命中，選到的日期跟這台
    # 機器實際上是不是那個型號完全無關——寧可不比對，也不要給一個查來的假日期。
    target_tokens = _hw_tokens(canonical_model)
    if len(target_tokens) < 2:
        return None
    candidates = []
    for e in entries:
        name_tokens = _hw_tokens(e["name"])
        if len(name_tokens) < 2:
            continue
        if name_tokens <= target_tokens or target_tokens <= name_tokens:
            candidates.append(e)
    if not candidates:
        return None
    # 取字詞數最多的命中（越多字詞對上越精確，避免「cisco」這種太籠統的詞搶答）
    return max(candidates, key=lambda e: len(_hw_tokens(e["name"])))


def eos_status(eos_date: str | None) -> str:
    """把日期換成畫面要的三態：expired（已過EOS）／upcoming（一年內）／ok（一年以上）。
    查不到日期的不算這三態，由呼叫端另外處理成「未公佈」。"""
    if not eos_date:
        return "unknown"
    import datetime

    # 有些官方公告只精確到月份（如「2029-07」），補成該月第一天才能拿去比大小；
    # 用月初而不是月底是刻意保守——「一年內到期」寧可提早一點點警示，也不要因為
    # 用了月底日期而多爭取到不存在的 30 天緩衝。
    text = eos_date[:10]
    if re.fullmatch(r"\d{4}-\d{2}", text):
        text += "-01"
    try:
        d = datetime.date.fromisoformat(text)
    except ValueError:
        return "unknown"
    today = datetime.date.today()
    if d < today:
        return "expired"
    if (d - today).days <= 365:
        return "upcoming"
    return "ok"


# ── W221-005／006：原因分類與「可信」判定（2026-10-30）──────────────────────────
# eos_status() 的舊三態＋unknown 維持不變（相容）；原因另外給，讓畫面／通知分得出
# 「未公告」「無單一日期」「來源未核對」…，不再全部看成一個 unknown。
REASON_LABEL = {
    "not_announced": "官方未公告日期",
    "no_single_date": "無單一日期（依版本各自到期）",
    "unverified": "來源未核對",
    "rejected": "來源被拒（查證未通過）",
    "bad_format": "日期格式錯誤",
    "not_found": "EOS 表查無此項目",
}


def _date_parses(eos_date: str | None) -> bool:
    import datetime

    text = (eos_date or "")[:10]
    if re.fullmatch(r"\d{4}-\d{2}", text):
        text += "-01"
    try:
        datetime.date.fromisoformat(text)
        return True
    except ValueError:
        return False


def eos_reason(hit: dict | None) -> str | None:
    """表列（lookup_*_eos 的回傳）→ 原因碼；有可用且已核對的日期回 None。

    not_found／not_announced／no_single_date／unverified／rejected／bad_format。
    unverified 含「有日期但來源未核對」——日期仍回給畫面，只是不單獨觸發紅燈與通知。"""
    if not hit:
        return "not_found"
    conf = hit.get("confidence")
    date_text = hit.get("eos_date")
    if conf == "source_rejected":
        return "rejected"
    if not date_text:
        if conf == "no_single_date":
            return "no_single_date"
        if conf == "unverified":
            return "unverified"
        return "not_announced"
    if not _date_parses(date_text):
        return "bad_format"
    if conf == "unverified":
        return "unverified"
    return None


def is_trusted(hit: dict | None) -> bool:
    """有日期、格式正確、且來源已核對（才可單獨觸發紅燈／過期通知）。"""
    return eos_reason(hit) is None


def entitlement_for(conn, row) -> dict | None:
    """這台資產「實際持有」的維護權益（有適用範圍、有效期與來源）。目前系統**沒有**資產層級的權益資料來源
    （W221-005／Codex 24 號：產品有付費延伸方案≠這台有買），所以一律回 None＝權益未知。
    日後有 asset_support 之類的資料表時，只改這一處；呼叫端已經照「未知／有效／到期」三種情況處理。"""
    return None


def _scope_list(scope) -> list[str]:
    if scope is None:
        return []
    items = scope if isinstance(scope, (list, tuple, set)) else [scope]
    return [str(x).strip().lower() for x in items if str(x).strip()]


def entitlement_state(entitlement: dict | None, hit: dict | None = None, today=None) -> str:
    """資產權益狀態（Codex 24／26 號：不能只憑任意來源字串＋未到期就當可核權益）：
    unknown          沒有可核對的權益記錄（缺來源、缺有效期、缺適用範圍或日期格式錯）
    not_applicable   適用範圍不含這個產品（scope 要與 EOS 表的產品名 name **精確相同**；不做模糊比對）
    not_yet_effective 生效日還沒到
    expired          已到期
    valid            來源、適用範圍含此產品、已生效、未到期——**只有這個才允許降級風險**"""
    import datetime as _dt
    if not entitlement or not entitlement.get("source") or not entitlement.get("valid_until"):
        return "unknown"
    scope = _scope_list(entitlement.get("scope"))
    if not scope:
        return "unknown"
    name = str((hit or {}).get("name") or "").strip().lower()
    if not name or name not in scope:
        return "not_applicable"
    day = today or _dt.date.today()
    try:
        until = _dt.date.fromisoformat(str(entitlement["valid_until"])[:10])
        vfrom = entitlement.get("valid_from")
        start = _dt.date.fromisoformat(str(vfrom)[:10]) if vfrom else None
    except ValueError:
        return "unknown"
    if start and start > day:
        return "not_yet_effective"
    return "valid" if until >= day else "expired"


def assess(hit: dict | None, entitlement: dict | None = None, today=None) -> dict:
    """畫面／API 用的整包判定。status 沿用舊三態＋unknown（相容）；另有 reason、eos_kind（官方原文）、
    plan_available（產品層級：官方有付費延伸方案，**不代表這台有買**）、entitlement_state（資產層級：
    unknown／valid／expired）。**只有資產權益 valid 時才 downgraded（降級風險）**；方案存在但權益未知／到期時，
    風險維持原樣，另外標「資產權益待確認」（Codex 24 號退回的推論，2026-10-31）。"""
    date_text = (hit or {}).get("eos_date")
    status = eos_status(date_text)
    reason = eos_reason(hit)
    plan = bool((hit or {}).get("extendable"))
    ent = entitlement_state(entitlement, hit, today)
    return {
        "status": status,
        "reason": reason,
        "reason_label": REASON_LABEL.get(reason) if reason else None,
        "eos_kind": (hit or {}).get("eos_kind"),
        "extendable": plan,                       # 舊欄位名保留（相容）：等同 plan_available
        "plan_available": plan,
        "entitlement_state": ent,
        "entitlement_pending": plan and status in ("expired", "upcoming") and ent != "valid",
        "trusted": reason is None,
        "downgraded": plan and ent == "valid" and status in ("expired", "upcoming"),
    }
