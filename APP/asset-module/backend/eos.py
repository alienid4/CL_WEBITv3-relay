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
