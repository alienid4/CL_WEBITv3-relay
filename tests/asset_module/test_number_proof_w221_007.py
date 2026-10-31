"""W221-007：數字健檢多帶母體／單位／產生時間／資料指紋，並有完整差異清單（sample 仍只列前 20 台）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "tests" / "asset_module"))

import number_proof as np_  # noqa: E402
from test_b09_number_proof import _conn  # noqa: E402


def test_health多帶母體單位時間指紋(tmp_path):
    c = _conn(tmp_path)
    h = np_.health(c)
    assert h["population"] and h["unit"] == "台"
    assert len(h["generated_at"]) == 19
    fp = h["db_fingerprint"]
    assert len(fp["hash"]) == 10 and fp["counts"]["hardware"] == 5
    for n in h["numbers"]:
        assert n["population"] and n["unit"] == "台"
    # 同一份資料指紋一致；資料變了指紋要變
    assert np_.health(c)["db_fingerprint"]["hash"] == fp["hash"]
    c.execute("INSERT INTO scan_history (ip, scan_ok, open_ports, scan_time) VALUES ('192.0.2.77', 1, '22', '2026-09-18 02:00:00')")
    c.commit()
    assert np_.health(c)["db_fingerprint"]["hash"] != fp["hash"]


def test_舊欄位不變(tmp_path):
    h = np_.health(_conn(tmp_path))
    assert [n["key"] for n in h["numbers"]] == np_.KEYS
    for n in h["numbers"]:
        assert {"screen", "recalc", "diff", "mismatch", "explanations", "unexplained", "zero"} <= set(n)


def test_完整差異清單不截斷_數量對得上health(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    # 造 30 台只有畫面有的差異：把重算 total 集合清空，畫面 30 台全算差異
    real = np_.independent

    def fake_ind(conn):
        r = real(conn)
        r["sets"]["total"] = set()
        return r

    monkeypatch.setattr(np_, "independent", fake_ind)
    h = np_.health(c)
    total_row = next(n for n in h["numbers"] if n["key"] == "total")
    d = np_.diff_list(c)
    mine = [i for i in d["items"] if i["key"] == "total"]
    assert total_row["mismatch"] == len(mine) > 0
    assert sum(e["n"] for e in total_row["explanations"]) == len(mine)
    assert all(e["sample"].__len__() <= 20 for e in total_row["explanations"])
    assert [i["no"] for i in d["items"]] == list(range(1, d["total"] + 1))   # 項次連續
    assert {"machine", "hostname", "ip", "side", "reason", "label"} <= set(mine[0])


def test_差異清單端點登記為user級讀取():
    import access_policy

    assert access_policy.level_for("GET", "/api/number-proof/diff") == access_policy.USER
    assert access_policy.level_for("GET", "/api/number-proof") == access_policy.VIEWER
