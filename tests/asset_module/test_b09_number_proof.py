"""[B-09] 數字可舉證：漏斗 5 個數字的說明、下鑽、獨立重算（2026-09-18）。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import number_proof as np_  # noqa: E402
import pipeline  # noqa: E402
import scan_scope  # noqa: E402

T = "2026-09-18 01:00:00"


def _conn(tmp_path, coverage=True):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    scan_scope._ensure(c)
    rows = [
        ("A-1", "web1", "192.0.2.1", "Red Hat Enterprise Linux 8", "使用中", 1),   # 掃到＋收得到＝已納管
        ("A-2", "web1", "192.0.2.1", "Red Hat Enterprise Linux 8", "使用中", 1),   # 同一台第二筆
        ("B-1", "aix1", "192.0.2.2", "AIX 7.2", "使用中", None),                    # 掃不到、在網段內＝失聯
        ("C-1", "far1", "198.51.100.9", "Windows 2019", "使用中", 1),               # 不在網段＝未涵蓋（收得到過也算）
        ("D-1", "old1", "192.0.2.3", "AIX 6.1", "報廢", None),                      # 退役
    ]
    for sn, h, ip, os_, st, ok in rows:
        db.insert_hardware(c, asset_serial=sn, hostname=h, ip=ip, os=os_, asset_status=st)
        c.execute("UPDATE hardware SET collect_ok = ? WHERE asset_serial = ?", (ok, sn))
    c.execute("INSERT INTO scan_history (ip, scan_ok, open_ports, scan_time) VALUES ('192.0.2.1', 1, '22', ?)", (T,))
    c.execute("INSERT INTO scan_history (ip, scan_ok, open_ports, scan_time) VALUES ('192.0.2.50', 1, '22', ?)", (T,))
    if coverage:
        c.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES (?, '192.0.2.0/24', 1, ?)", (T, T))
    c.commit()
    return c


def test_五個數字_畫面值與獨立重算逐台一致(tmp_path):
    h = np_.health(_conn(tmp_path))
    got = {n["key"]: (n["screen"], n["recalc"], n["mismatch"]) for n in h["numbers"]}
    assert got == {
        "total": (5, 5, 0),          # web1、aix1、far1、old1＋掃到未登記 192.0.2.50
        "onboarded": (1, 1, 0),
        "lost": (1, 1, 0),
        "not_covered": (1, 1, 0),
        "aix": (2, 2, 0),            # aix1＋已退役的 old1（AIX 看 OS，不看狀態）
    }


def test_差異會逐台歸因_人工改OS(tmp_path):
    c = _conn(tmp_path)
    import os_override
    os_override.set_override(c, "web1", "192.0.2.1", "A-1", "AIX", "Linux", "測試", "t")
    n = {x["key"]: x for x in np_.health(c)["numbers"]}["aix"]
    assert n["screen"] == 3 and n["recalc"] == 2 and n["unexplained"] == 0
    assert "人工改過 OS 類型" in n["explanations"][0]["why"]


def test_漏斗頂端數字_跟下鑽條件算出來一樣(tmp_path):
    c = _conn(tmp_path)
    s = pipeline.summarize(c)
    kn = {x["key"]: x for x in np_.key_numbers(c, s)["items"]}
    for key, n in kn.items():
        rows = s["items"]
        if n["drill"].get("stages"):
            rows = [r for r in rows if r["stage"] in n["drill"]["stages"]]
        if n["drill"].get("os_type"):
            rows = [r for r in rows if r["os_type"] in n["drill"]["os_type"]]
        assert len(rows) == n["value"], f"{key}：下鑽 {len(rows)} 台 ≠ 數字 {n['value']}"


def test_AIX說明_要講未登記的分不出來():
    assert "未登記的 AIX 從網路上分不出來" in np_.EXPLAIN["aix"]["limits"]
    for k in np_.KEYS:
        assert all(np_.EXPLAIN[k].get(f) for f in ("how", "source", "limits", "zero")), k


def test_零要分查過還是沒查(tmp_path):
    c = _conn(tmp_path, coverage=False)
    kn = {x["key"]: x for x in np_.key_numbers(c, pipeline.summarize(c))["items"]}
    assert kn["not_covered"]["value"] == 0
    assert kn["not_covered"]["zero"]["text"].startswith("沒查"), "沒有涵蓋紀錄的 0 不是「真的沒有」"
    ev = {"scan_time": None, "covered_segments": 0, "hardware_rows": 0, "collect_tried_rows": 0}
    assert np_.zero_status("lost", 0, ev)["text"].startswith("沒查")
    ev = {"scan_time": T, "covered_segments": 3, "hardware_rows": 9, "collect_tried_rows": 9}
    assert np_.zero_status("lost", 0, ev)["text"].startswith("查過真的沒有")


def test_獨立重算不准借畫面那套判定():
    src = (BACKEND / "number_proof.py").read_text(encoding="utf-8")
    body = re.search(r"^def independent\b[\s\S]*?(?=^def )", src, re.M).group(0)
    for bad in ("manage_state", "pipeline", "classify(", "scan_scope", "_row_in_keys"):
        assert bad not in body, f"independent() 不可以用 {bad}——同一套寫兩次只會一致地錯"


def test_沒有IP_不算失聯_另列沒有IP(tmp_path):
    """221 查證：失聯 316 台＝沒 IP 305＋IP 欄填兩個 11，沒有一台是真的失聯。"""
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="E-1", hostname="tmpl1", ip="", asset_status="使用中")
    db.insert_hardware(c, asset_serial="F-1", hostname="dual1", ip="192.0.2.60,192.0.2.1", asset_status="使用中")
    db.insert_hardware(c, asset_serial="G-1", hostname="dual2", ip="192.0.2.61,192.0.2.62", asset_status="使用中")
    c.commit()
    it = {i["asset_serial"]: i for i in pipeline.summarize(c)["items"]}
    assert it["E-1"]["stage"] == "no_ip", "沒有 IP＝沒查，不是失聯"
    assert it["F-1"]["stage"] != "lost", "兩個 IP 其中一個掃得到＝掃得到"
    assert it["G-1"]["stage"] == "lost", "兩個 IP 都在涵蓋網段、都沒掃到＝真的失聯"
    h = {n["key"]: n for n in np_.health(c)["numbers"]}
    assert all(n["mismatch"] == 0 for n in h.values()), {k: n["explanations"] for k, n in h.items() if n["mismatch"]}
