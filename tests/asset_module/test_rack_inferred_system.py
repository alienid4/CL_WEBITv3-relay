"""Codex 21 號 #3：工程系統整套是「推定」——標籤、規則、筆數要帶出來，數字不得因此改變。"""
import re
from pathlib import Path

import rack_layout as rl

ROOT = Path(__file__).resolve().parents[2] / "APP" / "asset-module"
SL = {"psu_max_w": 5000, "weight_kg_max": 400.0, "rack_u": 8}


def _dev(i, *, w=None, kg=None, state="active", sl=True, u=1):
    return {"key": f"k{i}", "state": state, "w": w, "kg": kg, "u_start": u + i * 2, "u_end": u + i * 2,
            "system_level": SL if sl else None, "spec_model": "Exadata X9" if sl else None}


def _power(n, sl_min=3, **kw):
    devs = [_dev(i, **kw) for i in range(n)]
    return rl.rack_power(devs, 42, 220.0, sl_min)


def test_門檻邊界_少一筆不推定_達門檻推定():
    assert _power(2)["system_level"] == []                      # N-1：不當整套
    out = _power(3)["system_level"]                             # N：推定
    assert len(out) == 1
    s = out[0]
    assert s["inferred"] is True and s["records"] == 3 and s["sl_min"] == 3
    assert s["rule"] == "同櫃同型號≥3筆推定為同一套"


def test_門檻跟著設定走():
    assert _power(4, sl_min=5)["system_level"] == []
    s = _power(5, sl_min=5)["system_level"][0]
    assert s["sl_min"] == 5 and "≥5" in s["rule"]


def test_數字與推定標籤無關_整套只算一次():
    p = _power(3)
    assert p["w"] == 5000.0 and p["kg"] == 400.0               # 與改動前相同：整套一次
    assert p["unknown_count"] == 0 and p["known_count"] == 3


def test_缺元件規格_仍標未知():
    devs = [_dev(i) for i in range(3)]
    for d in devs:
        d["system_level"] = {"psu_max_w": None, "weight_kg_max": None, "rack_u": 8}
    p = rl.rack_power(devs, 42, 220.0, 3)
    s = p["system_level"][0]
    assert s["inferred"] is True and s["w"] is None and s["kg"] is None
    assert p["w"] == 0 and p["unknown_count"] == 3


def test_重複登記不重複計():
    p = _power(6)                                               # 6 筆登記仍是一套
    assert p["w"] == 5000.0 and p["system_level"][0]["records"] == 6


def test_畫面有推定標籤與文字():
    vue = (ROOT / "frontend/components/RackElevation.vue").read_text(encoding="utf-8")
    assert "推定為同一套" in vue and "同櫃同型號 ≥" in vue and "筆登記" in vue
    assert "不會重複計 U、功率、重量" in vue
    assert re.search(r"<InfoNote>[^<]*這是依規則推定", vue)    # 說明收進 InfoNote
