"""台數口徑切換（Q79，2026-10-16）：一個主機名＝一台＋護欄；停損成立就留在舊規則；可回退；不默默切換。
IP 一律 10.99.x（範例）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import machine_identity as mi  # noqa: E402
import manage_state  # noqa: E402
import system_stats as ss  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv(mi.ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(mi, "MIN_MACHINES", 0)       # 測試資料只有幾十台；門檻另有專門測試
    yield
    mi._STATE.update(mode=mi.MODE_LEGACY, pending=frozenset())


def _db(tmp_path, rows):
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    for r in rows:
        db.insert_hardware(conn, asset_status="使用中", **r)
    conn.commit()
    return conn


def _fleet(n_single=30):
    """一台兩個 IP（同名）＋ 環境不同的同名 ＋ 通用名 ＋ 一堆單台（讓變動 < 10%）。"""
    rows = [dict(asset_serial="HW-1", hostname="apsvr01", ip="10.99.1.1", environment="正式"),
            dict(asset_serial="HW-2", hostname="APSVR01.corp.example.com", ip="10.99.2.1", environment="正式"),
            dict(asset_serial="HW-3", hostname="dbsvr", ip="10.99.1.3", environment="正式"),
            dict(asset_serial="VC-1", hostname="dbsvr", ip="10.99.3.3", environment="測試"),
            dict(asset_serial="HW-4", hostname="cisco ws-c2960", ip="0.0.0.0"),
            dict(asset_serial="HW-5", hostname="cisco ws-c2960", ip="0.0.0.0")]
    rows += [dict(asset_serial=f"HW-S{i}", hostname=f"single{i}", ip=f"10.99.9.{i}") for i in range(n_single)]
    return rows


def test_停損未觸發就切換_留快照_畫面提示切換前後台數(tmp_path):
    conn = _db(tmp_path, _fleet())
    assert ss.machine_key("apsvr01", "10.99.1.1", "HW-1") != ss.machine_key("apsvr01", "10.99.2.1", "HW-2")
    r = mi.decide(conn)
    assert r["decided"] and r["mode"] == mi.MODE_HOSTNAME and (r["old"], r["new"]) == (36, 35)
    snap = conn.execute("SELECT snapshot FROM machine_key_switch").fetchone()["snapshot"]
    assert '"HW-1": "apsvr01|10.99.1.1"' in snap                     # 切換前每筆的舊 key
    n = mi.notice(conn)
    assert n["mode"] == mi.MODE_HOSTNAME and (n["old_n"], n["new_n"]) == (36, 35) and n["hit"] == []
    # 同名兩個 IP＝一台；環境不同不併；含空白的設備型號＋0.0.0.0 各算一台
    assert ss.machine_key("apsvr01", "10.99.1.1", "HW-1") == ss.machine_key(
        "APSVR01.corp.example.com", "10.99.2.1", "HW-2")
    assert ss.machine_key("dbsvr", "10.99.1.3", "HW-3") != ss.machine_key("dbsvr", "10.99.3.3", "VC-1")
    assert ss.machine_key("cisco ws-c2960", "0.0.0.0", "HW-4") != ss.machine_key("cisco ws-c2960", "0.0.0.0", "HW-5")
    # 「同一台」的序號跨 IP 也找得到（明細頁收集數字、Q77 machine_scope 都靠它）
    assert sorted(manage_state.same_machine_serials(conn, "HW-1")) == ["HW-1", "HW-2"]
    # 判過就不再判
    assert mi.decide(conn)["decided"] is False
    conn.close()


def test_停損成立就留在舊規則_畫面講出是哪一條與數字(tmp_path):
    conn = _db(tmp_path, _fleet(n_single=3))          # 9→8 台＝-11%，觸發 b（待確認 1/8 也觸發 c）
    r = mi.decide(conn)
    assert r["mode"] == mi.MODE_LEGACY and "b" in r["hit"]
    assert ss.machine_key("apsvr01", "10.99.1.1", "HW-1") != ss.machine_key("apsvr01", "10.99.2.1", "HW-2")
    n = mi.notice(conn)
    assert n["mode"] == mi.MODE_LEGACY and n["hit"][0]["code"] == "b" and "→" in n["hit"][0]["value"]
    conn.close()


def test_環境變數強制回舊規則(tmp_path, monkeypatch):
    conn = _db(tmp_path, _fleet())
    mi.decide(conn)
    monkeypatch.setenv(mi.ENV_OVERRIDE, "legacy")
    conn.close()
    conn = db.get_connection(tmp_path / "t.db")            # 開連線就重設口徑
    assert mi.mode() == mi.MODE_LEGACY
    assert ss.machine_key("apsvr01", "10.99.1.1", "HW-1") != ss.machine_key("apsvr01", "10.99.2.1", "HW-2")
    conn.close()


def test_版本端點帶判定_試算端點要登入(tmp_path):
    from fastapi.testclient import TestClient

    import api

    conn = _db(tmp_path, _fleet())
    mi.decide(conn)
    conn.close()

    def _override():
        c = db.get_connection(tmp_path / "t.db")
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    try:
        c = TestClient(api.app)
        v = c.get("/api/version").json()
        assert v["machine_key_notice"]["mode"] == mi.MODE_HOSTNAME
        assert c.get("/api/machine-key").status_code == 401
    finally:
        api.app.dependency_overrides.clear()


def test_主機名口徑下_classify_assets台數等於machine_key去重(tmp_path):
    """221 預演抓到：_dup_key 先擋掉 0.0.0.0，主機名口徑下跟 machine_key 對不上（4198 vs 4162）。"""
    import system_report

    rows = _fleet() + [dict(asset_serial="HW-P1", hostname="phsvr", ip="0.0.0.0"),
                       dict(asset_serial="HW-P2", hostname="phsvr", ip="10.99.8.8")]
    conn = _db(tmp_path, rows)
    mi.decide(conn)
    assert mi.mode() == mi.MODE_HOSTNAME
    keys = {ss.machine_key(r["hostname"], r["ip"], r["asset_serial"]) for r in rows}
    assert len(system_report.classify_assets(conn)) == len(keys)
    conn.close()


# ---- 2026-10-17 審查 Group 1 ----

def test_資料太少不判定_留給下一次真實資料(tmp_path, monkeypatch):
    monkeypatch.setattr(mi, "MIN_MACHINES", 100)
    conn = _db(tmp_path, _fleet())
    r = mi.decide(conn)
    assert r["decided"] is False and "資料太少" in r["reason"]
    assert mi.setting_mode(conn) is None and mi.last_decision(conn) is None
    conn.close()


def _fake_verify(base, after):
    """b.db＝切換前（舊規則）基準，a.db＝試切主機名後。"""
    def f(db_file):
        return True, list(after if db_file.name == "a.db" else base)
    return f


def test_試切後多出新的錯_不切換_不寫設定_下次會重判(tmp_path, monkeypatch):
    conn = _db(tmp_path, _fleet())
    conn.close()
    monkeypatch.setattr(mi, "_verify_fails", _fake_verify([], ["❌ [台數] 假的錯：App=1  獨立=2", "❌ [分佈] 又一個"]))
    assert mi.decide_and_verify(tmp_path / "t.db") == 3
    conn = db.get_connection(tmp_path / "t.db")
    assert mi.setting_mode(conn) is None and mi.mode() == mi.MODE_LEGACY      # 審查 N3：不寫設定，下次重判
    n = mi.notice(conn)
    assert n["mode"] == mi.MODE_LEGACY and n["hit"][0]["code"] == "verify" and n["hit"][0]["value"] == "2 項"
    conn.close()
    monkeypatch.setattr(mi, "_verify_fails", _fake_verify([], []))
    monkeypatch.setattr(mi, "_code_version", lambda: "9.9.9")             # 程式修好（換版）
    assert mi.decide_and_verify(tmp_path / "t.db") == 0                    # 修好之後重判就切
    conn = db.get_connection(tmp_path / "t.db")
    assert mi.setting_mode(conn) == mi.MODE_HOSTNAME
    conn.close()


def test_切換前本來就有的錯_不算切換造成的_照樣切(tmp_path, monkeypatch):
    conn = _db(tmp_path, _fleet())
    conn.close()
    old = ["❌ [掃描範圍] 舊問題：App=3  獨立=5"]
    # 同一項、落差沒變大（2 → 2）：不算切換造成（審查 M2：落差變大才算新的）
    monkeypatch.setattr(mi, "_verify_fails", _fake_verify(old, ["❌ [掃描範圍] 舊問題：App=7  獨立=9"]))
    assert mi.decide_and_verify(tmp_path / "t.db") == 0
    conn = db.get_connection(tmp_path / "t.db")
    assert mi.setting_mode(conn) == mi.MODE_HOSTNAME
    conn.close()


def test_驗證前正式庫完全不動_中途失敗下次重判(tmp_path, monkeypatch):
    """審查 N5：判定與驗證都在副本上，驗證過程炸掉時正式庫沒有任何判定紀錄。"""
    conn = _db(tmp_path, _fleet())
    conn.close()

    def boom(_f):
        raise RuntimeError("killed")

    monkeypatch.setattr(mi, "_verify_fails", boom)
    try:
        mi.decide_and_verify(tmp_path / "t.db")
    except RuntimeError:
        pass
    conn = db.get_connection(tmp_path / "t.db")
    assert mi.setting_mode(conn) is None and mi.last_decision(conn) is None
    conn.close()


def test_停損a_正式與測試被併成一台就觸發(tmp_path):
    """審查：停損 a 沒有測試釘住。護欄用 group_environment，a 用原始值獨立判——
    原始值「pRoD」對護欄是「其他」（別名比對分大小寫、不擋），對 a 是「正式」（不分大小寫），
    跟「測試」併成一台就要觸發。（2026-10-17 加別名後 prod／PROD／Prod 會被護欄直接擋成待確認。）"""
    rows = _fleet() + [dict(asset_serial="E-1", hostname="mixbox", ip="10.99.4.1", environment="pRoD"),
                       dict(asset_serial="E-2", hostname="mixbox", ip="10.99.4.2", environment="測試")]
    conn = _db(tmp_path, rows)
    a = mi.analyse(conn)
    assert a["stops"]["a"]["hit"] is True and a["stops"]["a"]["value"] == "1 組"
    conn.close()


def test_標籤跟著切換與回退搬key_兩個方向都不孤兒(tmp_path):
    import asset_tags

    conn = _db(tmp_path, _fleet())
    asset_tags._ensure(conn)
    conn.execute("INSERT INTO asset_tag (machine_key, tag, source) VALUES ('apsvr01|10.99.1.1', '共用主機', 'manual')")
    conn.execute("INSERT INTO asset_tag (machine_key, tag, source) VALUES ('apsvr01|10.99.2.1', '共用主機', 'manual')")
    conn.commit()
    mi.decide(conn)
    keys = {r[0] for r in conn.execute("SELECT machine_key FROM asset_tag")}
    assert keys == {"h:apsvr01"}                                   # 兩台併一台：標籤只留一份
    mi.revert(conn, "manual", "人工回退", "test")
    keys = {r[0] for r in conn.execute("SELECT machine_key FROM asset_tag")}
    assert keys == {"apsvr01|10.99.1.1", "apsvr01|10.99.2.1"}       # 回退：拆回兩台，各掛一份
    conn.close()


def test_環境變數強制舊規則時_提示不說已改為主機名(tmp_path, monkeypatch):
    conn = _db(tmp_path, _fleet())
    mi.decide(conn)
    monkeypatch.setenv(mi.ENV_OVERRIDE, "legacy")
    n = mi.notice(conn)
    assert n["mode"] == mi.MODE_LEGACY and any(h["code"] == "env" for h in n["hit"])
    conn.close()


def test_部署與patch_判定後都有數字核對關卡():
    dep = (ROOT / "APP" / "asset-module" / "deploy.sh").read_text(encoding="utf-8")
    i_db = dep.index('"$APP/backend/db.py"' + chr(10))
    i_mk = dep.index("machine_identity.py\" decide-verify")
    assert i_mk > i_db                                              # 判定在 db.py 之後
    assert "${WEBIT_SKIP_TESTS" not in dep[i_db:i_mk + 200]         # 不被略過測試開關跳掉
    assert "MK_ENV_LINE" in dep                                     # unit 重寫時帶回退環境變數
    mp_path = ROOT / ".project" / "make_patch.py"
    if mp_path.exists():   # 公開 relay 的快照沒有 make_patch.py（只帶 APP／tests）
        assert "machine_identity.py\" decide-verify" in mp_path.read_text(encoding="utf-8")


def test_退役是備查_不計入台數_也不跟現役同名機器合併(tmp_path):
    """使用者 2026-10-17：「退役的設備就是備查狀態，它不能當資產」。"""
    import host_sources
    import monthly_report
    import system_report

    rows = _fleet() + [dict(asset_serial="HW-R1", hostname="apsvr01", ip="10.99.5.5"),
                       dict(asset_serial="HW-R2", hostname="oldbox", ip="10.99.5.6")]
    conn = _db(tmp_path, rows)
    conn.execute("UPDATE hardware SET asset_status = '報廢' WHERE asset_serial IN ('HW-R1','HW-R2')")
    conn.commit()
    mi.decide(conn)
    before = (sum(1 for a in system_report.classify_assets(conn) if not a["retired"]),
              ss.by_system(conn, limit=None)["total_assets"],
              host_sources.summary(host_sources.rows(conn))["total_hosts"])
    groups = ss.collapse_machines(conn.execute(
        "SELECT asset_serial, hostname, ip, asset_status FROM hardware").fetchall())
    assert not any({"HW-R1", "HW-1"} <= {r["asset_serial"] for r in g} for g in groups.values())
    conn.execute("DELETE FROM hardware WHERE asset_serial IN ('HW-R1','HW-R2')")
    conn.commit()
    after = (sum(1 for a in system_report.classify_assets(conn) if not a["retired"]),
             ss.by_system(conn, limit=None)["total_assets"],
             host_sources.summary(host_sources.rows(conn))["total_hosts"])
    assert before == after == (35, 35, 35)
    sec = next(s["data"] for s in monthly_report.collect(conn)["sections"] if s.get("name") == "資產總覽")
    # EXIST1：月報總台數＝存在可信度計入（這批假資料沒有任何存在證據，所以是 0）；
    # 重點仍是退役不計入：跟首頁同一份 existence.compute，退役在那邊自成「備查」
    import existence
    ex = existence.compute(conn)["summary"]
    assert sec["總台數"] == ex["counted"]
    assert ex["retired"] == 0          # 退役兩筆已刪；刪之前它們只會進「退役」，不進台數
    conn.close()


# ---- 2026-10-17 審查 Group 2 ----

def test_重複清單_儀表板_健康檢查同一份分組_判不出的不成一組(tmp_path):
    import _app_shared
    import health_check
    import vip_view

    rows = [dict(asset_serial="D-1", hostname="dupbox", ip="10.99.6.1", api_id="A", asset_name="x"),
            dict(asset_serial="D-2", hostname="dupbox", ip="10.99.6.1", api_id="A", asset_name="x"),
            dict(asset_serial="Z-1", hostname="z1", ip="0.0.0.0"), dict(asset_serial="Z-2", hostname="z2", ip="0.0.0.0"),
            dict(asset_serial="Z-3", hostname="", ip="10.99.6.9"), dict(asset_serial="Z-4", hostname="", ip="10.99.6.8")]
    conn = _db(tmp_path, rows)
    lst = _app_shared._duplicate_groups(conn)
    summ = vip_view.duplicate_summary(conn)
    assert len(lst) == summ["groups"] == 1                      # 以前 summary 會多一組 sn:None
    ctx = {}
    import inspect
    assert "duplicate_candidate_groups" in inspect.getsource(health_check)
    conn.close()


def test_同名不同IP內容相同_不是可刪的疑似重複(tmp_path):
    import vip_view

    c = vip_view.classify_duplicate_group([
        dict(ip="10.99.7.1", api_id="A", big_ip_vip="", asset_purpose="p", asset_name="n"),
        dict(ip="10.99.7.2", api_id="A", big_ip_vip="", asset_purpose="p", asset_name="n")])
    assert c["kind"] != vip_view.DUP_SUSPECT and c["extra_rows"] == 0


def test_釋放重複序號_成員用machine_key重抓_主機名口徑下跨IP也認得(tmp_path):
    import inspect

    from routers import assets as ra

    src = inspect.getsource(ra.release_duplicate_serials)
    assert "lower(trim(hostname)) = ? AND trim(ip) = ?" not in src and "duplicate_candidate_groups" in src


def test_明細頁別名用same_machine_serials(tmp_path):
    import inspect

    from routers import assets as ra

    src = inspect.getsource(ra)
    assert "WHERE lower(trim(hostname)) = lower(?) AND trim(ip) = ?" not in src
    conn = _db(tmp_path, _fleet())
    mi.decide(conn)
    assert sorted(manage_state.same_machine_serials(conn, "HW-2")) == ["HW-1", "HW-2"]
    conn.close()


def test_Q80補洞_設備型號CIA有值不蓋_批次併入過cia_safe_setters(tmp_path):
    import inspect

    import dyn_shell
    from routers import collect_scan

    conn = _db(tmp_path, [dict(asset_serial="HW-9", hostname="m9", ip="10.99.9.9", device_model="R740", os="RHEL 8")])
    got = dyn_shell.cia_guard_collected(conn, "HW-9", {"device_model": "PowerEdge", "os": "RHEL 9"})
    assert "device_model" not in got and got["os_cia"] == "RHEL 8"
    got = dyn_shell.cia_safe_setters(conn, conn.execute("SELECT id FROM hardware").fetchone()[0],
                                     {"device_model": "PowerEdge"})
    assert "device_model" not in got
    assert "cia_safe_setters" in inspect.getsource(collect_scan)
    conn.close()


def test_只改設定回退_標籤照樣看得到(tmp_path):
    import asset_tags

    conn = _db(tmp_path, _fleet())
    mi.decide(conn)
    asset_tags.add_tag(conn, "HW-1", "共用主機", "n")
    conn.execute("UPDATE app_settings SET value='legacy' WHERE key=?", (mi.MODE_SETTING,))
    conn.commit()
    mi.refresh(conn)
    assert [t["tag"] for t in asset_tags.list_tags(conn, "HW-2")] == ["共用主機"]
    n = mi.notice(conn)
    assert n["mode"] == mi.MODE_LEGACY and any(h["code"] == "setting" for h in n["hit"])
    conn.close()


def test_verify獨立重算台數_與machine_key一致(tmp_path):
    import verify_numbers as vn

    conn = _db(tmp_path, _fleet())
    mi.decide(conn)
    rows = [dict(r) for r in conn.execute("SELECT * FROM hardware")]
    keys = {ss.machine_key(r["hostname"], r["ip"], r["asset_serial"]) for r in rows}
    assert vn._indep_machine_count(rows, True) == len(keys) == 35
    assert vn._indep_machine_count(rows, False) == 36
    conn.close()


def test_系統下鑽也排除退役_跟檯面一致(tmp_path):
    conn = _db(tmp_path, [dict(asset_serial="S-1", hostname="s1", ip="10.99.3.1", api_id="X1"),
                          dict(asset_serial="S-2", hostname="s2", ip="10.99.3.2", api_id="X1")])
    conn.execute("UPDATE hardware SET asset_status = '報廢' WHERE asset_serial = 'S-2'")
    conn.execute("INSERT INTO business_system (api_id, name) VALUES ('X1', '測系統')")
    conn.commit()
    top = ss.by_system(conn, limit=None)["systems"][0]
    d = ss.drilldown(conn, "X1")
    assert top["total"] == 1
    assert d["total"] == 1
    conn.close()


def test_部署指令列真的跑得起來_路徑是字串也行(tmp_path, monkeypatch):
    """221 預演抓到：ASSET_DB_PATH 是字串，db.get_connection 要 Path → AttributeError。"""
    import subprocess

    conn = _db(tmp_path, _fleet())
    conn.close()
    env = {**__import__("os").environ, "ASSET_DB_PATH": str(tmp_path / "t.db"), "PYTHONIOENCODING": "utf-8"}
    env.pop(mi.ENV_OVERRIDE, None)
    be = ROOT / "APP" / "asset-module" / "backend"
    p = subprocess.run([sys.executable, str(be / "machine_identity.py"), "decide-verify"], env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(be))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "資料太少" in p.stdout            # 36 台 < 預設門檻 100：不判


# ---- 2026-10-17 再審 N1／N2／N4 ----

def test_N1_新機沿用退役機名字_首頁與漏斗不標登記矛盾(tmp_path):
    import pipeline

    conn = _db(tmp_path, _fleet() + [dict(asset_serial="R-OLD", hostname="apsvr01", ip="10.99.1.1")])
    conn.execute("UPDATE hardware SET asset_status = '報廢' WHERE asset_serial = 'R-OLD'")
    conn.commit()
    mi.decide(conn)
    assert manage_state.summarize(conn)["counts"][manage_state.CONFLICT] == 0
    assert all(it["stage"] != "conflict" for it in pipeline.summarize(conn)["items"])
    conn.close()


def test_N2_健康檢查只把疑似真重複標成重複登記(tmp_path):
    import health_check

    conn = _db(tmp_path, [dict(asset_serial="M-1", hostname="multi", ip="10.99.2.1", api_id="A"),
                          dict(asset_serial="M-2", hostname="multi", ip="10.99.2.1", api_id="B"),
                          dict(asset_serial="D-1", hostname="dup", ip="10.99.2.2", api_id="A", asset_name="x"),
                          dict(asset_serial="D-2", hostname="dup", ip="10.99.2.2", api_id="A", asset_name="x")])
    ctx = health_check._load_context(conn)
    assert ctx["dup_serials"] == {"D-1", "D-2"}          # 一機多系統（M-1／M-2）不是重複
    conn.close()


def test_N4_業務系統資產台數不算退役(tmp_path):
    import business_system

    conn = _db(tmp_path, [dict(asset_serial="B-1", hostname="b1", ip="10.99.3.1", api_id="X1"),
                          dict(asset_serial="B-2", hostname="b2", ip="10.99.3.2", api_id="X1")])
    conn.execute("UPDATE hardware SET asset_status = '停用' WHERE asset_serial = 'B-2'")
    conn.execute("INSERT INTO business_system (api_id, name) VALUES ('X1', '測')")
    conn.commit()
    assert [x["asset_count"] for x in business_system.list_all(conn) if x["api_id"] == "X1"] == [1]
    conn.close()


def test_主機名口徑下_同名不同IP的掃描證據不自動掛CIA_送需人工(tmp_path):
    import dyn_shell

    conn = _db(tmp_path, [dict(asset_serial="HW-C", hostname="websrv", ip="10.99.8.1", api_id="A")]
               + [dict(asset_serial=f"HW-S{i}", hostname=f"single{i}", ip=f"10.99.9.{i}") for i in range(3)])
    mi.decide(conn)
    assert mi.mode() == mi.MODE_HOSTNAME
    t = dyn_shell.cia_twins(conn, "websrv", "10.99.8.2")
    assert t["twins"] == [] and [r["asset_serial"] for r in t["near"]] == ["HW-C"]
    c = dyn_shell.classify(conn, {"asset_serial": "DYN-x", "hostname": "websrv", "ip": "10.99.8.2"})
    assert c["kind"] == "review" and "IP 不同" in c["reason"]
    conn.close()



# ---- 2026-10-17 小審 M1–L7 ----

def test_M1_試切後核對沒跑完_當失敗不切換(tmp_path, monkeypatch):
    conn = _db(tmp_path, _fleet())
    conn.close()
    monkeypatch.setattr(mi, "_verify_fails", lambda f: (f.name != "a.db", []))   # 主機名副本在頂層就炸
    assert mi.decide_and_verify(tmp_path / "t.db") == 3
    conn = db.get_connection(tmp_path / "t.db")
    assert mi.setting_mode(conn) is None
    conn.close()


def test_M1_沒有總結行就算沒跑完(tmp_path, monkeypatch):
    import subprocess

    class _P:
        returncode = 1
        stdout = "Traceback (most recent call last):" + chr(10)

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _P())
    assert mi._verify_fails(tmp_path / "x.db") == (False, [])


def test_M2_本來就有的錯變嚴重_算切換造成(tmp_path, monkeypatch):
    conn = _db(tmp_path, _fleet())
    conn.close()
    monkeypatch.setattr(mi, "_verify_fails", _fake_verify(["❌ [台數] 同一項：App=10  獨立=11"],
                                                           ["❌ [台數] 同一項：App=10  獨立=210"]))
    assert mi.decide_and_verify(tmp_path / "t.db") == 3


def test_M3_同版同台數上次已驗失敗_不重跑(tmp_path, monkeypatch):
    conn = _db(tmp_path, _fleet())
    conn.close()
    monkeypatch.setattr(mi, "_verify_fails", _fake_verify([], ["❌ [x] 壞"]))
    assert mi.decide_and_verify(tmp_path / "t.db") == 3
    calls = []
    monkeypatch.setattr(mi, "_verify_fails", lambda f: calls.append(f) or (True, []))
    assert mi.decide_and_verify(tmp_path / "t.db") == 3 and calls == []      # 沒重跑
    monkeypatch.setattr(mi, "_code_version", lambda: "9.9.9")                # 換版就重判
    assert mi.decide_and_verify(tmp_path / "t.db") == 0 and calls
    dep = (ROOT / "APP" / "asset-module" / "deploy.sh").read_text(encoding="utf-8")
    assert '"$_mk_rc" = "3"' in dep and "machine_key_mode=legacy" in dep


def test_L4_驗證期間正式庫資料變了_這次不切(tmp_path, monkeypatch):
    conn = _db(tmp_path, _fleet())
    conn.close()

    def f(db_file):
        if db_file.name == "a.db":
            c = db.get_connection(tmp_path / "t.db")
            db.insert_hardware(c, asset_serial="NEW-1", hostname="newbox", ip="10.99.5.9", asset_status="使用中")
            c.commit()
            c.close()
        return True, []

    monkeypatch.setattr(mi, "_verify_fails", f)
    assert mi.decide_and_verify(tmp_path / "t.db") == 0
    conn = db.get_connection(tmp_path / "t.db")
    assert mi.setting_mode(conn) is None
    conn.close()


def test_L5_上次被砍留下的暫存副本會被清掉(tmp_path, monkeypatch):
    conn = _db(tmp_path, _fleet())
    conn.close()
    stale = tmp_path / ".mk_verify_old123"
    stale.mkdir()
    (stale / "a.db").write_bytes(b"x")
    monkeypatch.setattr(mi, "_verify_fails", _fake_verify([], []))
    mi.decide_and_verify(tmp_path / "t.db")
    assert not list(tmp_path.glob(".mk_verify_*"))


def test_L6_兩邊都是0000或空白_不算IP相同_不自動掛(tmp_path):
    import dyn_shell

    conn = _db(tmp_path, [dict(asset_serial="HW-Z", hostname="zbox", ip="0.0.0.0", api_id="A")]
               + [dict(asset_serial=f"HW-S{i}", hostname=f"single{i}", ip=f"10.99.9.{i}") for i in range(3)])
    mi.decide(conn)
    t = dyn_shell.cia_twins(conn, "zbox", "0.0.0.0")
    assert t["twins"] == []
    conn.close()


def test_L7_數字核對子程序不帶舊規則環境變數(tmp_path, monkeypatch):
    import subprocess

    seen = {}

    class _P:
        returncode = 0
        stdout = "════ 共 1 項，❌ 0 項 ════" + chr(10)

    def run(*a, **k):
        seen.update(k["env"])
        return _P()

    monkeypatch.setenv(mi.ENV_OVERRIDE, "legacy")
    monkeypatch.setattr(subprocess, "run", run)
    assert mi._verify_fails(tmp_path / "x.db") == (True, [])
    assert mi.ENV_OVERRIDE not in seen


def test_環境別名_常見寫法都對到既有三組():
    """2026-10-17 老賈：environment_groups.json 只加別名、不改護欄邏輯。"""
    ge = manage_state.group_environment
    for v in ("DR", "dr", "DR 備援機", "災備", "異地備援"):
        assert ge(v) == "備援", v
    for v in ("prod", "PROD", "Production", "正式機"):
        assert ge(v) == "正式", v
    for v in ("test", "TEST", "uat", "SIT", "dev", "測試機", "使用者測試(UAT)", "開發環境(DEV)", "OA"):
        assert ge(v) == "測試", v
    assert ge("") == "未填" and ge("其他值") == "其他"


def test_deploy_重寫unit前抓回退環境變數_沒有那行也不會讓腳本結束(tmp_path):
    """2026-10-17 221 實際踩到：unit 裡沒有 WEBIT3_MACHINE_KEY 時 grep 回 1，
    set -euo pipefail 下 MK_ENV_LINE=$(...) 讓 deploy.sh 在 [5/7] 靜默結束、服務沒重啟。"""
    import shutil
    import subprocess

    dep = (ROOT / "APP" / "asset-module" / "deploy.sh").read_text(encoding="utf-8")
    line = next(ln for ln in dep.splitlines() if ln.startswith("MK_ENV_LINE="))
    bash = shutil.which("bash")
    if bash is None:
        assert "|| true" in line
        return
    unit = tmp_path / "u.service"
    unit.write_text("[Service]\nEnvironment=ASSET_SCHEDULER=1\n", encoding="utf-8")
    script = "set -euo pipefail\n" + line.split("  #")[0].replace("/etc/systemd/system/webit3-api.service", unit.as_posix()) + "\necho REACHED\n"
    p = subprocess.run([bash, "-c", script], capture_output=True, text=True)
    assert "REACHED" in p.stdout, p.stderr


def test_Q42_待確認名單快取_資料一改就重算_沒改才沿用(tmp_path, monkeypatch):
    """refresh 每開一條連線都跑，結果用「輸入指紋」快取（Q42 /adopt 變慢）。
    指紋就是 pending_names 讀的欄位原值：任何一格改了都要重算，不能讀到舊名單。"""
    rows = [dict(asset_serial="HW-1", hostname="apsvr01", ip="10.99.1.1", environment="正式"),
            dict(asset_serial="HW-2", hostname="apsvr01", ip="10.99.2.1", environment="正式")]
    rows += [dict(asset_serial=f"HW-S{i}", hostname=f"single{i}", ip=f"10.99.9.{i}") for i in range(30)]
    conn = _db(tmp_path, rows)
    conn.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES (?, 'hostname')", (mi.MODE_SETTING,))
    conn.commit()
    mi._PENDING_CACHE.clear()
    calls = []
    real = mi.pending_names
    monkeypatch.setattr(mi, "pending_names", lambda r: calls.append(1) or real(r))

    mi.refresh(conn)
    assert mi.mode() == mi.MODE_HOSTNAME and "apsvr01" not in mi._STATE["pending"]
    mi.refresh(conn)
    assert len(calls) == 1, "資料沒變，第二次要沿用快取"

    # 改 MAC → 同名兩種 MAC → 進待確認（未 commit 也要看得到：指紋用的是同一條連線）
    conn.execute("UPDATE hardware SET mac='aa:bb:cc:00:00:01' WHERE asset_serial='HW-1'")
    conn.execute("UPDATE hardware SET mac='aa:bb:cc:00:00:02' WHERE asset_serial='HW-2'")
    mi.refresh(conn)
    assert len(calls) == 2 and "apsvr01" in mi._STATE["pending"]
    # 另一條新連線（＝下一個請求）看到 commit 後的資料，也是同一份結果
    conn.commit()
    c2 = db.get_connection(tmp_path / "t.db")
    assert "apsvr01" in mi._STATE["pending"]
    # 改成同一個 MAC、退役一筆、改環境：每一種都要重算（改回跟以前完全一樣的內容則可沿用舊結果）
    for sql in ("UPDATE hardware SET mac='aa:bb:cc:00:00:09'",
                "UPDATE hardware SET asset_status='報廢' WHERE asset_serial='HW-2'",
                "UPDATE hardware SET asset_status='使用中', environment='測試' WHERE asset_serial='HW-2'"):
        n = len(calls)
        c2.execute(sql)
        mi.refresh(c2)
        assert len(calls) == n + 1, sql
        if sql.startswith("UPDATE hardware SET mac"):
            assert "apsvr01" not in mi._STATE["pending"]
    assert "apsvr01" in mi._STATE["pending"]          # 最後一步：正式／測試不同 → 待確認
    c2.close()
    conn.close()
