"""整台狀態聚合（machine_state_mode，Codex 36 號條件 1～8；D3 台數／口徑）。

旗標 app_settings.machine_state_mode：representative（預設，現行代表筆）／machine（整台聚合）。
這裡全是隔離 fixture（tmp_path SQLite、192.0.2.x 文件保留網段），不連網、不碰正式資料。
"""
import itertools
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "tests" / "asset_module"))

import db  # noqa: E402
import manage_state as ms  # noqa: E402
import pipeline  # noqa: E402
from test_b09_number_proof import _conn  # noqa: E402

OLD = "2000-01-01 00:00:00"


@pytest.fixture(autouse=True)
def _restore_mode():
    yield
    import machine_identity
    machine_identity._STATE.update({"mode": machine_identity.MODE_LEGACY, "pending": frozenset(), "host_fix": {}})


def _mk_conn(tmp_path, name="a", mode=None):
    """基底 fixture（web1/aix1/far1/old1…）＋主機名口徑；mode 非 None 時設 machine_state_mode。"""
    import machine_identity
    d = tmp_path / name
    d.mkdir()
    c = _conn(d)
    db.set_setting(c, "machine_key_mode", "hostname")
    machine_identity.refresh(c)
    if mode:
        db.set_setting(c, ms.STATE_MODE_SETTING, mode)
    return c


def _add(c, sn, host, ip, status="使用中", env=None, ok=None, ts=None):
    f = dict(asset_serial=sn, hostname=host, ip=ip, os="RHEL 9", asset_status=status)
    if env:
        f["environment"] = env
    db.insert_hardware(c, **f)
    if ok is not None:
        c.execute("UPDATE hardware SET collect_ok = ?, collect_checked_at = COALESCE(?, datetime('now','localtime')) "
                  "WHERE asset_serial = ?", (ok, ts, sn))
    c.commit()
    import machine_identity
    machine_identity.refresh(c)


def _states(c, host):
    s = ms.summarize(c)
    return [st for st, lst in s["rep_items"].items() for it in lst if it["hostname"] == host]


def _one(c, host):
    got = _states(c, host)
    assert len(got) == 1, (host, got)
    return got[0]


def _swap_case(tmp_path, tag, mode):
    c = _mk_conn(tmp_path, tag, mode)
    return c


# ---------------------------------------------------------------- Codex 反例
def test_預設旗標是代表筆_Codex反例序號對調狀態互換(tmp_path):
    c1 = _mk_conn(tmp_path, "c1")
    _add(c1, "Z1", "cx1", None)            # A 無 IP
    _add(c1, "Z2", "cx1", "192.0.2.77")    # B 有 IP、涵蓋內、未見存活
    c2 = _mk_conn(tmp_path, "c2")
    _add(c2, "Z1", "cx1", "192.0.2.77")
    _add(c2, "Z2", "cx1", None)
    assert ms.state_mode(c1) == ms.MODE_REPRESENTATIVE
    a, b = _one(c1, "cx1"), _one(c2, "cx1")
    assert (a, b) == (ms.NO_IP, ms.LOST), "預設維持舊行為：結果取決於序號（這是已知缺陷，旗標預設不改數字）"


def test_整台模式_Codex反例序號對調結果不變(tmp_path):
    c1 = _mk_conn(tmp_path, "c1", ms.MODE_MACHINE)
    _add(c1, "Z1", "cx1", None)
    _add(c1, "Z2", "cx1", "192.0.2.77")
    c2 = _mk_conn(tmp_path, "c2", ms.MODE_MACHINE)
    _add(c2, "Z1", "cx1", "192.0.2.77")
    _add(c2, "Z2", "cx1", None)
    assert _one(c1, "cx1") == _one(c2, "cx1") == ms.LOST
    # 缺 IP 的列仍保留為該列自己的資料問題：逐筆 state 不變、下鑽看得到
    s = ms.summarize(c1)
    row_states = {i["asset_serial"]: i["state"] for i in s["items"] if i["hostname"] == "cx1"}
    assert row_states == {"Z1": ms.NO_IP, "Z2": ms.LOST}
    rep = [it for it in s["rep_items"][ms.LOST] if it["hostname"] == "cx1"][0]
    assert {r["asset_serial"]: r["state"] for r in rep["machine_rows"]} == row_states


def test_排列不變性_同台三筆任意序號安排結果相同(tmp_path):
    rows = [None, "192.0.2.77", "198.51.100.7"]   # 無 IP／涵蓋內未見／涵蓋外
    seen = set()
    for n, perm in enumerate(itertools.permutations(rows)):
        c = _mk_conn(tmp_path, f"p{n}", ms.MODE_MACHINE)
        for i, ip in enumerate(perm):
            _add(c, f"S{i}", "perm1", ip)
        seen.add(_one(c, "perm1"))
    assert seen == {ms.LOST}


def test_不同IP不同涵蓋_任一IP涵蓋就是失聯_全在涵蓋外才是未涵蓋(tmp_path):
    c = _mk_conn(tmp_path, "cv1", ms.MODE_MACHINE)
    _add(c, "V1", "cov1", "192.0.2.77")
    _add(c, "V2", "cov1", "198.51.100.7")
    assert _one(c, "cov1") == ms.LOST
    c = _mk_conn(tmp_path, "cv2", ms.MODE_MACHINE)
    _add(c, "V1", "cov2", "198.51.100.7")
    _add(c, "V2", "cov2", "198.51.100.8")
    assert _one(c, "cov2") == ms.NOT_COVERED


def test_任一筆IP被掃到存活就不是失聯(tmp_path):
    c = _mk_conn(tmp_path, "sn", ms.MODE_MACHINE)
    _add(c, "W1", "seen1", "192.0.2.50")     # 掃描存活
    _add(c, "W2", "seen1", "192.0.2.78")     # 未見
    assert _one(c, "seen1") == ms.NOT_ONBOARDED
    c = _mk_conn(tmp_path, "sn2", ms.MODE_REPRESENTATIVE)
    _add(c, "W1", "seen1", "192.0.2.50")
    _add(c, "W2", "seen1", "192.0.2.78")
    assert _one(c, "seen1") in (ms.NOT_ONBOARDED, ms.LOST)   # 舊算法：看哪筆當代表


def test_新舊收集時間_過舊的連上過不算現在可用(tmp_path):
    c = _mk_conn(tmp_path, "old", ms.MODE_MACHINE)
    _add(c, "O1", "stale1", "192.0.2.77", ok=1, ts=OLD)    # 很久以前連上過
    _add(c, "O2", "stale1", None)
    assert _one(c, "stale1") == ms.ONBOARDED_STALE
    c = _mk_conn(tmp_path, "new", ms.MODE_MACHINE)
    _add(c, "O1", "fresh1", "192.0.2.77", ok=1)            # 剛連上
    _add(c, "O2", "fresh1", None)
    assert _one(c, "fresh1") == ms.ONBOARDED


def test_退役與現役分組_退役警訊不被吞掉(tmp_path):
    c = _mk_conn(tmp_path, "rt", ms.MODE_MACHINE)
    _add(c, "T1", "rt1", "192.0.2.50", "報廢")     # 退役但掃得到
    _add(c, "T2", "rt1", "192.0.2.99", "報廢")
    _add(c, "T3", "rt1", "192.0.2.77", "使用中")   # 同名現役：獨立一台，不被退役吞
    s = ms.summarize(c)
    mine = [(st, it["asset_serial"]) for st, lst in s["rep_items"].items() for it in lst if it["hostname"] == "rt1"]
    assert sorted(st for st, _ in mine) == sorted([ms.RETIRED_ALIVE, ms.LOST])
    c2 = _mk_conn(tmp_path, "rt2", ms.MODE_REPRESENTATIVE)
    _add(c2, "T1", "rt1", "192.0.2.50", "報廢")
    _add(c2, "T2", "rt1", "192.0.2.99", "報廢")
    _add(c2, "T3", "rt1", "192.0.2.77", "使用中")
    s2 = ms.summarize(c2)
    assert s["counts"] == s2["counts"]


def test_待確認同名_不同環境不併(tmp_path):
    c = _mk_conn(tmp_path, "pe", ms.MODE_MACHINE)
    _add(c, "E1", "envdup", "192.0.2.77", env="正式")
    _add(c, "E2", "envdup", "192.0.2.78", env="測試")
    assert len(_states(c, "envdup")) == 2, "同名但環境不同＝待確認，整台聚合沿用既有身分鍵、不另併"


def test_單列正常案例_兩模式逐台一致(tmp_path):
    c1 = _mk_conn(tmp_path, "s1")
    c2 = _mk_conn(tmp_path, "s2", ms.MODE_MACHINE)
    a, b = ms.summarize(c1), ms.summarize(c2)
    assert a["counts"] == b["counts"]
    sa = {it["asset_serial"]: st for st, lst in a["rep_items"].items() for it in lst}
    sb = {it["asset_serial"]: st for st, lst in b["rep_items"].items() for it in lst}
    # 基底 fixture 的 A-1／A-2 是同台兩筆（都已納管），其餘單列
    assert {k: v for k, v in sa.items() if k not in ("A-1", "A-2")} == {k: v for k, v in sb.items() if k not in ("A-1", "A-2")}


# ---------------------------------------------------------------- 回退／單一出口／下鑽
def test_回退_切回代表筆不改資料且數字回到原樣(tmp_path):
    c = _mk_conn(tmp_path, "rb")
    _add(c, "Z1", "cx1", None)
    _add(c, "Z2", "cx1", "192.0.2.77")
    before_rows = [tuple(r) for r in c.execute("SELECT * FROM hardware ORDER BY asset_serial")]
    base = ms.summarize(c)["counts"]
    db.set_setting(c, ms.STATE_MODE_SETTING, ms.MODE_MACHINE)
    assert ms.summarize(c)["counts"] != base
    db.set_setting(c, ms.STATE_MODE_SETTING, ms.MODE_REPRESENTATIVE)
    assert ms.summarize(c)["counts"] == base
    assert [tuple(r) for r in c.execute("SELECT * FROM hardware ORDER BY asset_serial")] == before_rows
    db.set_setting(c, ms.STATE_MODE_SETTING, "亂寫的值")
    assert ms.state_mode(c) == ms.MODE_REPRESENTATIVE, "不認得的值一律當代表筆（fail-safe）"


@pytest.mark.parametrize("mode", [ms.MODE_REPRESENTATIVE, ms.MODE_MACHINE])
def test_下鑽筆數等於卡片_且漏斗同口徑(tmp_path, mode):
    c = _mk_conn(tmp_path, "dd" + mode, mode)
    _add(c, "Z1", "cx1", None)
    _add(c, "Z2", "cx1", "192.0.2.77")
    _add(c, "Q1", "cx2", None)
    _add(c, "Q2", "cx2", "198.51.100.7")
    s = ms.summarize(c)
    assert all(s["counts"][st] == len(s["rep_items"][st]) for st in ms.ALL_STATES)
    assert s["machine_state_mode"]["mode"] == mode and s["machine_state_mode"]["label"] in ("代表筆", "整台")
    assert s["machine_state_mode"]["version"]
    p = pipeline.summarize(c)
    assert p["machine_state_mode"]["mode"] == mode
    # 漏斗（逐台）與首頁同一口徑：這幾態兩邊台數相同
    # （代表筆口徑下漏斗自己另有一套「留 stage_index 最小」的代表筆，與首頁本來就可能不同——既有行為，不在此釘）
    if mode == ms.MODE_MACHINE:
        for st, key in ((ms.LOST, "lost"), (ms.NO_IP, "no_ip"), (ms.NOT_COVERED, "not_covered"),
                        (ms.RETIRED, "retired"), (ms.RETIRED_ALIVE, "retired_alive")):
            assert p["counts"][key] == s["counts"][st], (mode, st)
    # items 逐筆 state 在兩種口徑下都保留（逐筆資料品質不消失）
    assert {i["asset_serial"]: i["state"] for i in s["items"] if i["hostname"] == "cx1"} == \
        {"Z1": ms.NO_IP, "Z2": ms.LOST}


def test_machine模式item帶整台狀態_representative不帶(tmp_path):
    c = _mk_conn(tmp_path, "mi")
    assert all("machine_state" not in i for i in ms.summarize(c)["items"])
    assert all("_ev" not in i for i in ms.summarize(c)["items"])
    db.set_setting(c, ms.STATE_MODE_SETTING, ms.MODE_MACHINE)
    s = ms.summarize(c)
    assert all("machine_state" in i and "_ev" not in i for i in s["items"])


# ---------------------------------------------------------------- compare
def test_compare_逐台變動清單_脫敏_唯讀(tmp_path):
    c = _mk_conn(tmp_path, "cmp")
    _add(c, "Z1", "cx1", None)
    _add(c, "Z2", "cx1", "192.0.2.77")
    before = [tuple(r) for r in c.execute("SELECT * FROM hardware")]
    settings_before = [tuple(r) for r in c.execute("SELECT * FROM app_settings ORDER BY key")]
    out = ms.compare_modes(c)
    assert out["read_only"] and out["total_equal"] and not out["counts_equal"]
    assert out["representative"]["drilldown_equals_card"] and out["machine"]["drilldown_equals_card"]
    assert out["changed_machines"] == 1
    ch = out["changes"][0]
    assert (ch["before"], ch["after"]) == (ms.NO_IP, ms.LOST) and ch["no"] == "M1"
    assert ch["rows"] == 2 and "cx1" not in str(ch) and "192.0.2" not in str(ch), "脫敏：不含主機名／IP"
    assert out["change_matrix"] == {f"{ms.NO_IP}→{ms.LOST}": 1}
    # 變動清單加總＝兩邊各態台數的差
    for st in ms.ALL_STATES:
        d = out["machine"]["counts"][st] - out["representative"]["counts"][st]
        moved = sum(1 for x in out["changes"] if x["after"] == st) - sum(1 for x in out["changes"] if x["before"] == st)
        assert d == moved, st
    assert [tuple(r) for r in c.execute("SELECT * FROM hardware")] == before
    assert [tuple(r) for r in c.execute("SELECT * FROM app_settings ORDER BY key")] == settings_before


def test_compare_兩模式無差異時零變動(tmp_path):
    c = _mk_conn(tmp_path, "cmp0")
    out = ms.compare_modes(c)
    assert out["counts_equal"] and out["changed_machines"] == 0 and out["changes"] == []


def test_compare端點權限_非admin_403(tmp_path, monkeypatch):
    import access_policy
    import api
    import auth
    from fastapi.testclient import TestClient

    assert access_policy.level_for("GET", "/api/machine-state/compare") == "admin"
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    p = tmp_path / "ep.db"
    db.init_db(p)

    def _ov():
        conn = db.get_connection(p)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _ov
    try:
        conn = db.get_connection(p)
        for name, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
            db.create_user(conn, name, auth.hash_password("Pw-12345-xyz!"), role=role)
        conn.close()
        res = {}
        for name in ("alice", "vera", "admin"):
            cl = TestClient(api.app)
            r = cl.post("/api/auth/login", json={"username": name, "password": "Pw-12345-xyz!"})
            assert r.status_code == 200, r.text
            res[name] = cl.get("/api/machine-state/compare").status_code
        assert res == {"alice": 403, "vera": 403, "admin": 200}
    finally:
        api.app.dependency_overrides.clear()


# ---------------------------------------------------------------- 既有不變式在兩種模式都過 / 獨立重算不得呼叫 summarize
@pytest.mark.parametrize("mode", [ms.MODE_REPRESENTATIVE, ms.MODE_MACHINE])
def test_verify_numbers不變式兩模式都過(tmp_path, mode):
    import verify_numbers as vn
    c = _mk_conn(tmp_path, "vn" + mode, mode)
    _add(c, "Z1", "cx1", None)
    _add(c, "Z2", "cx1", "192.0.2.77")
    for fn in (vn.check_manage_state_rep_items, vn.check_machine_state_mode):
        before = vn.FAILS
        fn(c)
        assert vn.FAILS == before, (mode, fn.__name__, [r for r in vn.RESULTS if r["ok"] is False][-3:])


def test_數字健檢兩模式都能跑_獨立重算沒有改成呼叫summarize(tmp_path):
    import number_proof as np_
    src = (BACKEND / "number_proof.py").read_text(encoding="utf-8")
    ind = src[src.index("def independent"):src.index("def ", src.index("def independent") + 10)]
    assert "manage_state.summarize" not in ind and "_machine_state_of" not in ind and "compare_modes" not in ind
    for mode in (ms.MODE_REPRESENTATIVE, ms.MODE_MACHINE):
        c = _mk_conn(tmp_path, "np" + mode, mode)
        _add(c, "Z1", "cx1", None)
        _add(c, "Z2", "cx1", "192.0.2.77")
        h = np_.health(c)
        assert {n["key"] for n in h["numbers"]} >= {"total", "lost", "not_covered"}
