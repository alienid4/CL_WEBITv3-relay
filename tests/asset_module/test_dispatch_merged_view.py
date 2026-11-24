"""2-1 收集結果改成合併檢視＋只收新的（v1.468.4）。

2026-10-19 使用者（公司機）：「收集一次要很久，有什麼改善方式；今天用單一主機收集完之後，
發現資料全部不見了，我得全部重新收集一次，這個有問題。」

根因：collect_dispatch.latest_run() 只回最近一次收集（ORDER BY id DESC LIMIT 1）。收一台就把
前一次 9000 個位址的結果藏起來（資料沒刪，只是不顯示）。

這支釘住（舊程式紅、新程式綠）：
  1. 合併檢視：每個位址取最新一次的結果；收一台之後總數不變、那一台換成新結果
  2. 每列帶自己的收集時間與「舊」；表頭講合併幾次、最早／最新
  3. run_id 只看某一次
  4. 頁簽加總＝下鑽、verify_numbers 新不變式（舊形狀會紅）
  5. 只收新的：已納管／空位址在時間窗內不重探；待辦一律重探；單一位址一定重探；
     空位址後來被登記就要重探；全部重收＝都重探
  6. 各階段秒數寫進 run.timing；節奏設定存得進去、超出範圍擋下
  7. 前端：表頭「合併」、上次收集欄可排序、舊結果可下鑽、全部重收勾選、6 系統設定有節奏卡
"""
import contextlib
import json
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402

FE = ROOT / "APP" / "asset-module" / "frontend" / "pages"
ADOPT_VUE = (FE / "adopt.vue").read_text(encoding="utf-8")
SETTINGS_VUE = (FE / "settings" / "index.vue").read_text(encoding="utf-8")


@contextlib.contextmanager
def _db():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            yield conn
        finally:
            conn.close()


def _run(conn, ports, *, full=True, ssh_ok=(), calls=None, **kw):
    """ports: {ip: [開放埠] 或 None(沒回應)}。ssh_ok：SSH 收集帳號連得上的 IP。"""
    def prober(ip):
        if calls is not None:
            calls.append(ip)
        return ports.get(ip)
    return cd.run_dispatch(conn, "\n".join(ports), prober=prober, ttl_prober=lambda ip: None,
                           ssh_prober=lambda ip: (ip in ssh_ok, None if ip in ssh_ok else "denied"),
                           full=full, **kw)


BIG = {"10.99.9.10": [22], "10.99.9.11": [22], "10.99.9.12": [], "10.99.9.13": None,
       "10.99.9.14": None, "10.99.9.15": [3389]}


# ===================== 1~3. 合併檢視 =====================

def test_收一台之後_其他位址的結果還在():
    with _db() as conn:
        _run(conn, BIG, ssh_ok={"10.99.9.10"})
        before = cd.latest_run(conn)
        _run(conn, {"10.99.9.11": [22]}, ssh_ok={"10.99.9.11"})
        after = cd.latest_run(conn)
        assert after["total"] == before["total"] == len(BIG), "收一台不可以讓總數變少（舊程式這裡是 1）"
        by = {r["ip"]: r for r in after["results"]}
        assert by["10.99.9.11"]["status"] == cd.STATUS_COLLECTED, "那一台換成新結果"
        assert by["10.99.9.10"]["status"] == cd.STATUS_COLLECTED, "其他位址還是上次的結果"
        assert by["10.99.9.11"]["run_id"] > by["10.99.9.10"]["run_id"]
        assert after["merged"]["mode"] == "merged" and after["merged"]["run_count"] == 2
        assert after["merged"]["earliest"] <= after["merged"]["latest"]
        assert sum(after["by_tab"].values()) == after["total"]
        for t, n in after["by_tab"].items():
            assert n == sum(1 for r in after["results"] if r["tab"] == t), t
        assert after["run"]["target_count"] == 1, "run 仍是最近一次（進度條看它）"


def test_每列有自己的收集時間_超過天數標舊():
    with _db() as conn:
        _run(conn, BIG)
        conn.execute("UPDATE collect_dispatch_result SET created_at = datetime('now','localtime','-40 days') "
                     "WHERE ip IN ('10.99.9.10','10.99.9.11')")
        conn.commit()
        out = cd.latest_run(conn)
        by = {r["ip"]: r for r in out["results"]}
        assert by["10.99.9.10"]["stale"] and by["10.99.9.11"]["stale"]
        assert not by["10.99.9.12"]["stale"] and by["10.99.9.12"]["collected_at"]
        assert out["merged"]["stale_count"] == 2 and out["merged"]["stale_days"] == cd.DEFAULT_STALE_DAYS


def test_只看某一次收集():
    with _db() as conn:
        r1 = _run(conn, BIG)["run_id"]
        _run(conn, {"10.99.9.11": [22]})
        one = cd.latest_run(conn, run_id=r1)
        assert one["total"] == len(BIG) and one["merged"]["mode"] == "run"
        assert {r["run_id"] for r in one["results"]} == {r1}
        assert cd.latest_run(conn, run_id=99999) is None


# ===================== 4. verify_numbers =====================

def _vn(conn):
    import verify_numbers as vn
    before = len(vn.RESULTS)
    vn.check_adopt_tabs(conn)
    vn.check_adopt_registered_split(conn)
    mine = vn.RESULTS[before:]
    return [x for x in mine if x["ok"] is False], mine


def test_verify_numbers_合併檢視全過():
    with _db() as conn:
        _run(conn, BIG, ssh_ok={"10.99.9.10"})
        _run(conn, {"10.99.9.11": [22]})
        bad, mine = _vn(conn)
        assert not bad, bad
        assert any("合併檢視列數" in x["name"] for x in mine)


def test_verify_numbers_舊形狀會紅(monkeypatch):
    """模擬舊程式（只回最近一次收集）：合併檢視列數不等於不同位址數 → 紅。"""
    with _db() as conn:
        _run(conn, BIG)
        rid = _run(conn, {"10.99.9.11": [22]})["run_id"]
        orig = cd._merged_results
        monkeypatch.setattr(cd, "_merged_results", lambda c, run_id: orig(c, rid))
        bad, _ = _vn(conn)
        assert any("合併檢視列數" in x["name"] for x in bad), bad


# ===================== 5. 只收新的 =====================

def test_只收新的_跳過已納管與空位址_待辦照樣重探():
    with _db() as conn:
        _run(conn, BIG, ssh_ok={"10.99.9.10"})
        calls = []
        out = _run(conn, BIG, full=False, ssh_ok={"10.99.9.10"}, calls=calls)
        # 10 已納管、13/14 空位址 → 不重探；11 待佈身分、12 進不去、15 Windows → 重探
        assert out["skipped"] == 3
        assert sorted(calls) == ["10.99.9.11", "10.99.9.12", "10.99.9.15"]
        merged = cd.latest_run(conn)
        assert merged["total"] == len(BIG), "跳過的位址在合併檢視照樣看得到"
        run = merged["run"]
        assert run["skipped_count"] == 3 and run["target_count"] == 3


def test_全部重收_與_單一位址_都一定重探():
    with _db() as conn:
        _run(conn, BIG, ssh_ok={"10.99.9.10"})
        calls = []
        _run(conn, BIG, full=True, calls=calls)
        assert len(calls) == len(BIG)
        calls.clear()
        out = _run(conn, {"10.99.9.10": [22]}, full=False, ssh_ok={"10.99.9.10"}, calls=calls)
        assert calls == ["10.99.9.10"] and out["skipped"] == 0, "人指名收這一台"


def test_空位址後來被登記了_要重探():
    with _db() as conn:
        _run(conn, BIG)
        db.insert_hardware(conn, asset_serial="N-1", ip="10.99.9.13", hostname="new-box",
                           environment="測試", asset_status="使用中")
        calls = []
        _run(conn, BIG, full=False, calls=calls)
        assert "10.99.9.13" in calls and "10.99.9.14" not in calls


def test_超過時間窗就重探():
    with _db() as conn:
        _run(conn, BIG, ssh_ok={"10.99.9.10"})
        # v1.468.5：空位址另有較長時間窗（預設 7 天），所以這裡老化到 8 天兩種窗都過
        conn.execute("UPDATE collect_dispatch_result SET created_at = datetime('now','localtime','-8 days')")
        conn.commit()
        calls = []
        out = _run(conn, BIG, full=False, calls=calls)
        assert out["skipped"] == 0 and len(calls) == len(BIG)


def test_全部都跳過_也是一輪正常的收集():
    with _db() as conn:
        ports = {"10.99.9.13": None, "10.99.9.14": None}
        _run(conn, ports)
        out = _run(conn, ports, full=False)
        assert out["skipped"] == 2 and out["total"] == 0
        merged = cd.latest_run(conn)
        assert merged["run"]["status"] == "ok" and merged["total"] == 2


# ===================== 6. 計時與設定 =====================

def test_各階段秒數寫進run():
    with _db() as conn:
        _run(conn, BIG, gap_seconds=0)
        t = json.loads(cd.latest_run(conn)["run"]["timing"])
        assert t["final"] is True
        assert set(t) >= {"probe", "ttl", "ssh", "winrm", "gap", "other", "total"}
        assert abs(sum(t[k] for k in ("probe", "ttl", "ssh", "winrm", "gap", "other")) - t["total"]) < 0.5


def test_節奏設定_存得進去_超出範圍擋下():
    with _db() as conn:
        cfg = cd.dispatch_settings(conn)
        assert cfg["batch_size"] == cd.BATCH_SIZE and cfg["gap_seconds"] == cd.DEFAULT_BATCH_GAP_SECONDS
        assert cfg["skip_fresh_hours"] == cd.DEFAULT_SKIP_FRESH_HOURS
        out = cd.save_dispatch_settings(conn, {"batch_size": 256, "gap_seconds": 60,
                                               "skip_fresh_hours": 0, "stale_days": 7})
        assert (out["batch_size"], out["gap_seconds"], out["skip_fresh_hours"], out["stale_days"]) == (256, 60, 0, 7)
        with pytest.raises(ValueError):
            cd.save_dispatch_settings(conn, {"batch_size": 99999})
        # 只收新的設成 0＝關：全部重探
        _run(conn, BIG)
        calls = []
        assert _run(conn, BIG, full=False, calls=calls)["skipped"] == 0 and len(calls) == len(BIG)


def test_一批幾台照設定切():
    with _db() as conn:
        cd.save_dispatch_settings(conn, {"batch_size": 16})
        ports = {f"10.99.8.{i}": None for i in range(1, 41)}
        _run(conn, ports, gap_seconds=0)
        assert cd.latest_run(conn)["run"]["batch_total"] == 3


# ===================== 7. 前端 =====================

def test_前端_合併表頭_上次收集欄可排序_舊可下鑽_全部重收():
    assert "合併 {{ dispatch.merged.run_count }} 次收集" in ADOPT_VUE
    assert re.search(r'<SortTh k="collected_at"[^>]*>上次收集</SortTh>', ADOPT_VUE)
    assert "stale: (r) => Boolean(r.stale)" in ADOPT_VUE
    assert "'stale'" in ADOPT_VUE.split("const D_RESULT_KEYS: DResult[] = [")[1].split("]")[0]
    assert "pickResult('stale')" in ADOPT_VUE
    assert 'v-model="dFull"' in ADOPT_VUE and "full: dFull.value" in ADOPT_VUE
    # 還在跑也把已存檔的批換上去（結果一批一批長出來）
    assert "if (r?.results?.length) dispatch.value = r" in ADOPT_VUE


def test_前端_6系統設定有收集節奏卡_現值照實顯示():
    assert "2-1 開始收集的節奏" in SETTINGS_VUE
    assert "/api/collect/dispatch/settings" in SETTINGS_VUE
    for k in ("batch_size", "gap_seconds", "skip_fresh_hours", "stale_days"):
        assert f"dCfg.{k}" in SETTINGS_VUE, k
