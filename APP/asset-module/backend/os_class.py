"""作業系統「大類」：全站唯一一份判斷（OS-CLASS-AUDIT，2026-10-18）。

規則在 os_class_groups.json（分類是業務判斷、會變，放設定檔不寫死）。這支只負責照規則比對，
**只用標準函式庫**——scripts/machine_key_trial.py 在公司機獨立跑、不能 import 這裡，所以它內嵌
一份同樣的規則與同樣的比對程式，tests/asset_module/test_os_class.py 釘住兩邊完全一樣。

用到的地方（都呼叫 classify，不准再自寫一份關鍵字表）：
  - 首頁「作業系統平台」長條（manage_state.composition 的 by_os_class）
  - 資產查詢 ?os_class=（asset_classify._classify_row 的 os_class）→ 首頁長條點進去的筆數
  - 月報表1（system_report._report_platform 以大類決定列）
  - 台數口徑試算 machine_key_trial.os_class（內嵌副本）

跟別的「平台」不是同一件事、刻意不併：collect_platform_of（決定收集要跑哪組指令）、
pipeline._os_type（納管漏斗：OpenShift 節點、推測類要分開，因為納管方式不同）、
normalize.os_category（EOS 頁的維護權責）。
"""
from __future__ import annotations

import json
import pathlib
import re

_PATH = pathlib.Path(__file__).with_name("os_class_groups.json")
_RULES: dict | None = None
_COMPILED: list | None = None

#: 判斷會看的欄位（guess＝掃描推測的 OS，只有 os 判不出來時才用）
FIELDS = ("os", "device_model", "group_name", "asset_purpose", "guess")


def load_rules() -> dict:
    """讀規則。設定檔壞掉直接丟錯——大類判錯會讓所有頁面的台數一起錯，寧可整頁報錯被看見。"""
    global _RULES, _COMPILED
    if _RULES is None:
        _RULES = json.loads(_PATH.read_text(encoding="utf-8"))
        _COMPILED = compile_rules(_RULES)
    return _RULES


def reload_rules() -> None:
    global _RULES, _COMPILED
    _RULES = _COMPILED = None


def compile_rules(cfg: dict) -> list:
    out = []
    for r in cfg.get("rules", []):
        out.append((r["class"], r["field"], tuple(k.lower() for k in r.get("contains", ())),
                    tuple(re.compile(p, re.I) for p in r.get("regex", ()))))
    return out


def class_order() -> list[str]:
    return list(load_rules()["classes"])


def _text(v) -> str:
    return " ".join(str(v or "").split()).strip().lower()   # 換行／多個空白收成一個（CIA 機型常有換行）


def classify_with(cfg: dict, compiled: list, os=None, device_model=None, group_name=None,
                  asset_purpose=None, guess=None) -> str:
    """純比對（給測試與試算腳本用同一份程式）。"""
    vals = {"os": _text(os), "device_model": _text(device_model), "group_name": _text(group_name),
            "asset_purpose": _text(asset_purpose), "guess": _text(guess)}
    unknown = set(cfg.get("unknown_values", ()))
    os_known = vals["os"] not in unknown
    if os_known:
        vals["guess"] = ""            # 有 OS 字串就不拿推測蓋它（真資料優先）
    for cls, field, kws, pats in compiled:
        t = vals.get(field, "")
        if not t or t in unknown:
            continue
        if any(k in t for k in kws) or any(p.search(t) for p in pats):
            return cls
    return cfg["fallback_known"] if os_known else cfg["fallback_empty"]


def classify(os=None, device_model=None, group_name=None, asset_purpose=None, guess=None) -> str:
    """一筆資產的 OS 大類。"""
    cfg = load_rules()
    return classify_with(cfg, _COMPILED, os, device_model, group_name, asset_purpose, guess)


def classify_row(row, guess=None) -> str:
    """hardware 列（sqlite3.Row 或 dict）。缺欄位當空白。"""
    keys = row.keys() if hasattr(row, "keys") else ()

    def g(k):
        return row[k] if k in keys else None
    return classify(g("os"), g("device_model"), g("group_name"), g("asset_purpose"), guess)
