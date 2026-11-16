"""Codex 固定 209（2.042.09）回歸工單在現行版的重現／守門（2026-11-13 夜間）。

每個測試名稱帶工單編號；雙向：壞的情況要擋／要算對，正常對照照舊。全部假資料（IP 一律 10.99.x）。
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import v2_onboard  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLE_AWARE_TESTS = True
_PW = "s3cure-pass!"          # 測試用假密碼


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    db_path = tmp_path / "t.db"
    db.init_db(db_path)

    def _override_get_db():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override_get_db
    conn = db.get_connection(db_path)
    for name, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(conn, name, auth.hash_password(_PW), role=role)
    conn.close()

    class E:
        path = db_path

        def client(self, name):
            c = TestClient(api.app)
            r = c.post("/api/auth/login", json={"username": name, "password": _PW})
            assert r.status_code == 200, r.text
            return c

        def conn(self):
            return db.get_connection(db_path)

    yield E()
    api.app.dependency_overrides.clear()


# ---------------------------------------------------------------- 1013 納管進度小數目標
def test_1013_小數目標最後才向上取整():
    assert v2_onboard.gap_to_target(2, 1, 50.1) == 1      # 50.1% × 2 = 1.002 → 要 2 台，尚差 1（舊版回 0）
    assert v2_onboard.gap_to_target(2, 1, 50.0) == 0      # 剛好整除：不多算
    assert v2_onboard.gap_to_target(10, 7, 70.0) == 0
    assert v2_onboard.gap_to_target(10, 7, 70.1) == 1
    assert v2_onboard.gap_to_target(3, 0, 33.3) == 1      # 0.999 → 1
    assert v2_onboard.gap_to_target(0, 0, 80.0) == 0
    assert v2_onboard.gap_to_target(5, 5, 80.0) == 0      # 已超過目標不回負數


# ---------------------------------------------------------------- 1017 納管失敗頁重新納管入口限管理員
def _onboard_vue() -> str:
    return (FE / "pages" / "v2" / "onboard.vue").read_text(encoding="utf-8")


def test_1017_納管失敗頁_重新納管按鈕與視窗限管理員():
    src = _onboard_vue()
    for tag in ("fail-retry-ssh", "fail-retry-win"):
        m = re.search(r'<button[^>]*data-test="%s"' % tag, src)
        assert m and 'v-if="isAdmin"' in m.group(0), tag


# ---------------------------------------------------------------- 1019 AIX 不可走 Linux 撤銷
def test_1019_撤銷視窗依登記平台傳值_不寫死linux():
    src = _onboard_vue()
    assert 'platform="linux"' not in src
    assert ':platform="revokePlatform(revoke)"' in src


def _seed_host(env, ip, os_text):
    c = env.conn()
    db.insert_hardware(c, asset_serial=f"S-{ip}", hostname=f"h{ip.split('.')[-1]}", ip=ip, os=os_text)
    c.commit()
    c.close()


def test_1019_後端_平台與清冊不符_只警告不擋_寫進稽核(env, monkeypatch):
    """審查 P2-1：清冊 OS 寫錯時不可以讓撤銷永遠出不來——不符只警告（回應＋稽核），照樣撤銷；不另外探測目標機。"""
    import onboard_engine
    called = []
    monkeypatch.setattr(onboard_engine, "revoke", lambda **kw: called.append(kw) or onboard_engine.OnboardResult(True, "done", "ok"))
    monkeypatch.setattr(onboard_engine, "probe_target", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不可探測")), raising=False)
    c0 = env.conn()
    db.insert_hardware(c0, asset_serial="S-AIX", hostname="aix11", ip="10.99.7.10,10.99.7.11", os="AIX 7.2")   # 多 IP 列
    c0.commit()
    c0.close()
    c = env.client("admin")
    r = c.post("/api/onboard/revoke", json={"ip": "10.99.7.11", "platform": "linux", "username": "root",
                                            "password": "x", "confirm": "YES"})
    assert r.status_code == 200, r.text
    assert "平台不符" in (r.json()["warning"] or "")
    assert len(called) == 1                               # 照樣撤銷（不卡死）
    c0 = env.conn()
    msg = c0.execute("SELECT message FROM onboard_audit WHERE trigger='revoke' ORDER BY id DESC LIMIT 1").fetchone()[0]
    c0.close()
    assert "平台不符" in msg
    # 對照：Linux 登記＋linux 沒有警告
    _seed_host(env, "10.99.7.12", "Red Hat Enterprise Linux 9")
    r = c.post("/api/onboard/revoke", json={"ip": "10.99.7.12", "platform": "linux", "username": "root",
                                            "password": "x", "confirm": "YES"})
    assert r.status_code == 200 and r.json()["warning"] is None
    assert len(called) == 2 and called[1]["platform"] == "linux"


# ---------------------------------------------------------------- 1043 已納管匯出標籤不可寫成公式
def test_1043_已納管匯出_公式字元開頭存成文字_一般值不變(tmp_path, monkeypatch):
    import io

    import managed_export
    from openpyxl import load_workbook
    conn = None                       # export_rows 換成替身，不碰資料庫
    row = [3, "10.99.4.3", "win03", "Windows", "S-3", "=1+1", "", "", "", "", "", "", ""]
    monkeypatch.setattr(managed_export, "export_rows", lambda c, **kw: [row, [4, "10.99.4.4", "lnx04", "Linux", "S-4", "prod", "", "", "", "", "", "", ""]])
    data = managed_export.workbook_bytes(conn)
    ws = load_workbook(io.BytesIO(data)).active
    assert ws["F2"].data_type == "s" and ws["F2"].value == "'=1+1"      # 舊版：data_type == "f"
    assert ws["F3"].value == "prod" and ws["A2"].value == 3              # 一般值、項次原樣


# ---------------------------------------------------------------- 1011 立即回檢入口限管理員（V2 已修；舊頁補）
def test_1011_立即回檢入口只給管理員_後端一般使用者403(env):
    v2 = (FE / "components" / "v2" / "Drift.vue").read_text(encoding="utf-8")
    old = (FE / "pages" / "drift.vue").read_text(encoding="utf-8")
    assert re.search(r'<button v-if="isAdmin"[^>]*@click="recheck"', v2)
    assert re.search(r'<button v-if="!isViewer && isAdmin"[^>]*@click="recheck"', old)
    assert env.client("alice").post("/api/drift/recheck").status_code == 403
    assert env.client("admin").post("/api/drift/recheck").status_code == 200


# ---------------------------------------------------------------- 1015 viewer 讀上線檢查明細不寫庫（2.047.1 已修）
def test_1015_viewer讀上線檢查明細_不建表不寫自動結果(env):
    c = env.conn()
    db.insert_hardware(c, asset_serial="V-1", hostname="v1", ip="10.99.15.1", os="RHEL 9.6", asset_status="待上線")
    c.commit()
    before = (c.execute("SELECT COUNT(*) FROM golive_check").fetchone()[0], c.execute("SELECT COUNT(*) FROM golive_check_result").fetchone()[0])
    c.close()
    r = env.client("vera").get("/api/golive/V-1")
    assert r.status_code == 200 and r.json()["total"] > 0
    c = env.conn()
    after = (c.execute("SELECT COUNT(*) FROM golive_check").fetchone()[0], c.execute("SELECT COUNT(*) FROM golive_check_result").fetchone()[0])
    c.close()
    assert after == before                                           # 固定 209：寫入 7 筆 checked_by=auto
    assert env.client("vera").post("/api/golive/V-1/refresh").status_code == 403   # 重新判定（會寫）viewer 不行


# ---------------------------------------------------------------- 1035／1037／1039 掃描缺口
def _scan_conn(tmp_path, segs):
    import scan_scope
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "sg.db"
    db.init_db(p)
    c = db.get_connection(p)
    scan_scope._ensure(c)
    for cidr in segs:
        c.execute("INSERT INTO network_segment (cidr, raw_cidr, location, environment, category, imported_at) "
                  "VALUES (?,?,?,?,?,?)", (cidr, cidr, "甲", "正式", "SERVER", "2026-11-01 00:00:00"))
        scan_scope.apply_scope(c, [r[0] for r in c.execute("SELECT id FROM network_segment WHERE cidr = ?", (cidr,))])
    c.commit()
    return c


def test_1035_標註鍵正規化_空白與主機位元都讀得到_非CIDR拒收(tmp_path):
    import scan_gaps as sg
    c = _scan_conn(tmp_path, ["10.99.21.0/24"])
    sg.set_note(c, "  10.99.21.0/24 ", "policy", None, "政策不掃", "u")
    row = {r["cidr"]: r for r in sg.report(c)["rows"]}["10.99.21.0/24"]
    assert row["note_status"] == "policy"                     # 舊版：寫了但報表讀不到
    sg.set_note(c, "10.99.21.7/24", "watch", 3, "", "u")      # 等價網段＝同一列，覆寫
    assert c.execute("SELECT COUNT(*) FROM scan_segment_note").fetchone()[0] == 1
    assert {r["cidr"]: r for r in sg.report(c)["rows"]}["10.99.21.0/24"]["note_status"] == "watch"
    with pytest.raises(ValueError):
        sg.set_note(c, "not-a-cidr", "policy")
    # 歷史上存成非正規寫法的舊列：報表照樣對得上，清除時一起清
    c.execute("DELETE FROM scan_segment_note")
    c.execute("INSERT INTO scan_segment_note (cidr, status, note) VALUES (' 10.99.21.0/24', 'policy', '舊')")
    c.commit()
    assert {r["cidr"]: r for r in sg.report(c)["rows"]}["10.99.21.0/24"]["note_status"] == "policy"
    sg.set_note(c, "10.99.21.0/24", "")
    assert c.execute("SELECT COUNT(*) FROM scan_segment_note").fetchone()[0] == 0


def test_1037_多IP各段都算_重疊網段歸最窄段與登記先後無關(tmp_path):
    import scan_gaps as sg
    c = _scan_conn(tmp_path, ["10.99.31.0/24", "10.99.32.0/24"])
    db.insert_hardware(c, asset_serial="M-1", hostname="m1", ip="10.99.31.5,10.99.32.5", asset_status="使用中")
    c.commit()
    rep = sg.report(c)
    r = {x["cidr"]: x for x in rep["rows"]}
    assert r["10.99.31.0/24"]["registered_machines"] == 1 and r["10.99.32.0/24"]["registered_machines"] == 1
    # 審查 P2-2：兩段都是缺口（從沒掃過）——明細各段各列，卡片跨段去重仍是 1 台
    assert r["10.99.31.0/24"]["is_gap"] and r["10.99.32.0/24"]["is_gap"]
    assert rep["summary"]["gap_registered_machines"] == 1
    for i, order in enumerate((["10.99.33.0/24", "10.99.33.0/25"], ["10.99.33.0/25", "10.99.33.0/24"])):
        c2 = _scan_conn(tmp_path / f"o{i}", order)
        db.insert_hardware(c2, asset_serial="O-1", hostname="o1", ip="10.99.33.10", asset_status="使用中")
        c2.commit()
        r = {x["cidr"]: x for x in sg.report(c2)["rows"]}
        assert r["10.99.33.0/25"]["registered_machines"] == 1 and r["10.99.33.0/24"]["registered_machines"] == 0, order


def test_1039_整批零回應_最近一批以涵蓋紀錄為準(tmp_path):
    import scan_gaps as sg
    c = _scan_conn(tmp_path, ["10.99.41.0/24", "10.99.42.0/24"])
    t1, t2 = "2026-11-01 01:00:00", "2026-11-02 01:00:00"
    # 舊批：41 段回應 1 台；新批：兩段都成功涵蓋但全批零回應（scan_history 沒有這批）
    c.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES (?,?,1,?)", (t1, "10.99.41.0/24", t1))
    c.execute("INSERT INTO scan_history (scan_time, ip, segment, scan_ok) VALUES (?,?,?,1)", (t1, "10.99.41.9", "10.99.41.0/24"))
    for cidr in ("10.99.41.0/24", "10.99.42.0/24"):
        c.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES (?,?,1,?)", (t2, cidr, t2))
    c.commit()
    r = {x["cidr"]: x for x in sg.report(c)["rows"]}
    assert r["10.99.42.0/24"]["kind"] == sg.NO_REPLY          # 舊版：STALE「最近一次沒掃到這段」
    assert r["10.99.41.0/24"]["hosts_found_last"] == 0         # 舊版：取到舊批的 1
    assert r["10.99.41.0/24"]["hosts_found_ever"] == 1


# ---------------------------------------------------------------- 1051／1053 待納管存活投影
def test_1051_失敗的掃描紀錄不可投影成通(tmp_path):
    c = _scan_conn(tmp_path, ["10.99.51.0/24"])
    ip = "10.99.51.5"
    c.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES (?,?,1,?)",
              ("2026-11-01 01:00:00", "10.99.51.0/24", "x"))
    c.commit()
    assert v2_onboard.alive_map(c, [{"ip": ip}])[ip]["state"] == "不通"
    c.execute("INSERT INTO scan_history (scan_time, ip, segment, scan_ok) VALUES (?,?,?,0)",
              ("2026-11-01 02:00:00", ip, "10.99.51.0/24"))
    c.commit()
    assert v2_onboard.alive_map(c, [{"ip": ip}])[ip]["state"] == "不通"      # 舊版：變「通」
    c.execute("INSERT INTO scan_history (scan_time, ip, segment, scan_ok) VALUES (?,?,?,1)",
              ("2026-11-01 03:00:00", ip, "10.99.51.0/24"))
    c.commit()
    m = v2_onboard.alive_map(c, [{"ip": ip}])[ip]                            # 對照：真的掃到就是通
    assert m["state"] == "通" and m["by"] == "網路掃描"


def test_1053_沿用的偵測結果不蓋過較新的掃描(tmp_path):
    import host_ping
    c = _scan_conn(tmp_path, ["10.99.53.0/24"])
    host_ping._ensure_run_tables(c)
    ip = "10.99.53.5"

    def run(at, verdict):
        cur = c.execute("INSERT INTO alive_check_run (started_at, finished_at, total, alive, no_response, unknown) "
                        "VALUES (?,?,1,1,0,0)", (at, at))
        c.execute("INSERT INTO alive_check_item (run_id, ip, result, verdict) VALUES (?,?,?,?)", (cur.lastrowid, ip, "alive", verdict))

    run("2026-11-01 10:00:00", "存活")
    c.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES (?,?,1,?)",
              ("2026-11-01 10:01:00", "10.99.53.0/24", "x"))
    run("2026-11-01 10:02:00", "沿用 2 分鐘前的偵測結果（5 分鐘內不重複探測）：存活")
    c.commit()
    assert v2_onboard.alive_map(c, [{"ip": ip}])[ip]["state"] == "不通"      # 舊版：10:02 沿用列蓋成「通」
    run("2026-11-01 10:03:00", "存活")                                       # 對照：真的新探測照樣蓋過
    c.commit()
    assert v2_onboard.alive_map(c, [{"ip": ip}])[ip]["state"] == "通"


# ---------------------------------------------------------------- 1049 第五步看同一台
def test_1049_同機非代表序號通過上線檢查_第五步算完成(tmp_path):
    c = _scan_conn(tmp_path, [])
    db.insert_hardware(c, asset_serial="G-1", hostname="gl01", ip="10.99.49.1", os="Red Hat Enterprise Linux 9")
    db.insert_hardware(c, asset_serial="G-2", hostname="gl01", ip="10.99.49.1", os="Red Hat Enterprise Linux 9")
    c.commit()
    nd = v2_onboard.new_devices(c)
    row = next(r for r in nd["rows"] if r["hostname"] == "gl01")
    assert sum(1 for r in nd["rows"] if r["hostname"] == "gl01") == 1          # 同一台一列
    other = "G-2" if row["serial"] == "G-1" else "G-1"
    assert row["steps"]["golive"] is False
    c.execute("INSERT INTO golive_check (asset_serial, status) VALUES (?, 'passed')", (other,))
    c.commit()
    row = next(r for r in v2_onboard.new_devices(c)["rows"] if r["hostname"] == "gl01")
    assert row["steps"]["golive"] is True and row["golive_status"] == "passed"   # 舊版：只看代表序號 → False
    # 對照：別台的通過不算到這台
    db.insert_hardware(c, asset_serial="G-9", hostname="gl09", ip="10.99.49.9", os="Red Hat Enterprise Linux 9")
    c.commit()
    assert next(r for r in v2_onboard.new_devices(c)["rows"] if r["hostname"] == "gl09")["steps"]["golive"] is False


# ---------------------------------------------------------------- 1041 IP 換手後批次貼標
def test_1041_序號與IP對不上_整列拒絕_不貼到舊資產(tmp_path):
    import asset_tags
    c = _scan_conn(tmp_path, [])
    db.insert_hardware(c, asset_serial="T-OLD", hostname="old", ip="10.99.61.20")    # 原資產已搬到別的 IP
    db.insert_hardware(c, asset_serial="T-NEW", hostname="new", ip="10.99.61.10")    # 新資產接手原 IP
    c.commit()
    out = asset_tags.add_tag_batch(c, [{"ip": "10.99.61.10", "asset_serial": "T-OLD"}], "待查")
    assert out["ok"] == 0 and out["failed"] == 1 and "換手" in out["results"][0]["reason"]
    assert asset_tags.tags_by_serial(c, ["T-OLD", "T-NEW"]) == {}                    # 舊版：T-OLD 被貼上
    # 對照：對得上照常貼；只給 IP 照 IP 找現在那台
    out = asset_tags.add_tag_batch(c, [{"ip": "10.99.61.20", "asset_serial": "T-OLD"}, {"ip": "10.99.61.10"}], "待查")
    assert out["ok"] == 2
    assert set(asset_tags.tags_by_serial(c, ["T-OLD", "T-NEW"])) == {"T-OLD", "T-NEW"}


# ---------------------------------------------------------------- 1047 整台豁免取消也要整台
def test_1047_單序號取消豁免_同一台全部取消_別台不受影響(tmp_path):
    import onboard_exempt
    c = _scan_conn(tmp_path, [])
    db.insert_hardware(c, asset_serial="E-1", hostname="ex01", ip="10.99.47.1")
    db.insert_hardware(c, asset_serial="E-2", hostname="ex01", ip="10.99.47.1")
    db.insert_hardware(c, asset_serial="E-9", hostname="ex09", ip="10.99.47.9")
    c.commit()
    onboard_exempt.add(c, ["E-1", "E-9"], "客製系統", None, "admin")
    assert onboard_exempt.active_serials(c) == {"E-1", "E-2", "E-9"}
    assert onboard_exempt.remove(c, ["E-1"], "admin") == 2
    assert onboard_exempt.active_serials(c) == {"E-9"}                  # 舊版：E-2 殘留


# ---------------------------------------------------------------- 1119～1127 掃描範圍
def _seg_row(c, cidr, category="SERVER", environment="正式", location="甲"):
    c.execute("INSERT INTO network_segment (cidr, raw_cidr, category, environment, scan_excluded, location) VALUES (?,?,?,?,0,?)",
              (cidr, cidr, category, environment, location))
    c.commit()


def _enabled(c):
    import scan_scope as ss
    return sorted(r[0] for r in c.execute("SELECT target FROM connections WHERE connection_type = ? AND (enabled IS NULL OR enabled = 1)",
                                          (ss.SCAN_TYPE,)))


def test_1123_同CIDR多列來源_強制排除全部停用_同步狀態說實話(tmp_path):
    import scan_scope as ss
    c = _scan_conn(tmp_path, [])
    _seg_row(c, "10.99.81.0/24")
    ss.set_policy(c, {"mode": "rule"})
    c.execute("INSERT INTO connections (name, connection_type, target, enabled, created_at, updated_at) VALUES (?,?,?,1,'x','x')",
              ("手加", ss.SCAN_TYPE, "10.99.81.0/24"))
    c.commit()
    assert _enabled(c) == ["10.99.81.0/24", "10.99.81.0/24"]
    ss.set_policy(c, {"force_out": ["10.99.81.0/24"]})
    assert _enabled(c) == []                                             # 舊版：只停一列
    assert ss.list_scope(c)["in_sync"] is True
    ss.set_policy(c, {"force_out": []})                                  # 對照：拿掉排除，至少一列重新啟用
    assert len(_enabled(c)) >= 1 and ss.list_scope(c)["in_sync"] is True


def test_1125_等價CIDR_強制排除照樣優先_非CIDR拒收(tmp_path):
    import scan_scope as ss
    c = _scan_conn(tmp_path, [])
    _seg_row(c, "10.99.82.0/24")
    ss.set_policy(c, {"mode": "rule", "force_in": ["10.99.82.0/24"], "force_out": ["10.99.82.5/24"]})
    assert _enabled(c) == []                                             # 舊版：納入生效
    assert {r["cidr"]: r["reason"] for r in ss.resolve_scope(c)}["10.99.82.0/24"] == "手動強制排除"
    ss.set_policy(c, {"force_in": ["10.99.82.9/24"], "force_out": []})    # 對照：只填等價的強制納入也認得
    assert _enabled(c) == ["10.99.82.0/24"]
    with pytest.raises(ValueError):
        ss.set_policy(c, {"force_out": ["not-a-cidr"]})
    assert ss.get_policy(c)["force_out"] == []                           # 拒收時沒存進去


def test_1121_清冊外仍啟用的來源要列出_規則存檔與同步同進退(tmp_path, monkeypatch):
    import scan_scope as ss
    c = _scan_conn(tmp_path, [])
    _seg_row(c, "10.99.83.0/24")
    _seg_row(c, "10.99.84.0/24")
    ss.set_policy(c, {"mode": "manual"})
    ss.apply_scope(c, [r[0] for r in c.execute("SELECT id FROM network_segment")])
    c.execute("DELETE FROM network_segment WHERE cidr = '10.99.84.0/24'")   # 換清冊：84 段移除，來源仍啟用
    c.commit()
    ls = ss.list_scope(c)
    assert ls["orphan_enabled"] == ["10.99.84.0/24"] and ls["in_sync"] is False   # 舊版：看不到、顯示同步
    # 規則模式：同步失敗時規則不可以「已存、來源沒動」
    before = ss.get_policy(c)
    monkeypatch.setattr(ss, "apply_policy", lambda conn: (_ for _ in ()).throw(RuntimeError("disk")))
    with pytest.raises(RuntimeError):
        ss.set_policy(c, {"mode": "rule", "force_out": ["10.99.83.0/24"]})
    assert ss.get_policy(c) == before


def _seg_xlsx(path, rows):
    import openpyxl
    import segments
    wb = openpyxl.Workbook()
    ws = wb.active
    hs = list(segments.HEADER_MAP.keys())
    ws.append(hs)
    for r in rows:
        ws.append([r.get(h, "") for h in hs])
    wb.save(path)
    return path


def test_1119_1127_網段匯入_同步失敗要講_政策欄位變動要講(tmp_path, monkeypatch):
    import scan_scope as ss
    import segments
    c = _scan_conn(tmp_path, [])
    base = {"使用狀況": "使用中", "使用位置": "甲機房", "使用類別": "SERVER", "網段": "10.99.85.0/24"}
    env_col = next(h for h, k in segments.HEADER_MAP.items() if k == "environment_raw")
    r1 = segments.import_segments(c, _seg_xlsx(tmp_path / "a.xlsx", [{**base, env_col: "正式"}]))
    assert r1["scope_sync"]["ok"] is True and r1["policy_changed_count"] == 0
    r2 = segments.import_segments(c, _seg_xlsx(tmp_path / "b.xlsx", [{**base, env_col: "正式", "使用位置": "乙機房"}]))
    assert r2["unchanged_count"] == 1 and r2["policy_changed_count"] == 1     # 舊版：只回「不變 1」
    r3 = segments.import_segments(c, _seg_xlsx(tmp_path / "c.xlsx", [{**base, env_col: "正式", "使用位置": "乙機房"}]))
    assert r3["policy_changed_count"] == 0                                     # 對照：真的沒變
    monkeypatch.setattr(ss, "apply_policy", lambda conn: (_ for _ in ()).throw(RuntimeError("locked")))
    r4 = segments.import_segments(c, _seg_xlsx(tmp_path / "d.xlsx", [{**base, "網段": "10.99.86.0/24"}]))
    assert r4["scope_sync"]["ok"] is False and "同步失敗" in r4["scope_sync"]["error"]   # 舊版：照樣 200 看起來全好


# ---------------------------------------------------------------- 1091／1095 上線檢查通過
def _golive_ready(tmp_path, serial="GL-1", ip="10.99.91.1", status="待上線"):
    import golive
    c = _scan_conn(tmp_path, [])
    db.insert_hardware(c, asset_serial=serial, hostname=serial.lower(), ip=ip, os="RHEL 9.6", asset_status=status)
    for port, proto in ((22, "tcp"), (161, "udp")):
        c.execute("INSERT INTO host_service (ip, proto, port, bind_addr, source) VALUES (?,?,?,'0.0.0.0','test')", (ip, proto, port))
    c.commit()
    golive.refresh_auto_results(c, serial)
    for it in golive.get_check_detail(c, serial)["items"]:
        if it["check_type"] == "manual":
            golive.set_item_verdict(c, serial, it["key"], "pass", "admin")
    return c


def test_1095_報廢資產不能經上線檢查改回使用中(tmp_path):
    import golive
    c = _golive_ready(tmp_path / "a", status="報廢")
    with pytest.raises(ValueError, match="報廢"):
        golive.pass_check(c, "GL-1", "admin")
    assert c.execute("SELECT asset_status FROM hardware WHERE asset_serial='GL-1'").fetchone()[0] == "報廢"
    assert c.execute("SELECT status FROM golive_check WHERE asset_serial='GL-1'").fetchone()[0] == "open"
    assert c.execute("SELECT COUNT(*) FROM baseline_drift").fetchone()[0] == 0
    c2 = _golive_ready(tmp_path / "b")                                          # 對照：待上線照常通過
    assert golive.pass_check(c2, "GL-1", "admin")["status"] == "passed"


def test_1091_通過的讀與寫在同一個寫鎖內_中途插入的人工不通過進不來(tmp_path, monkeypatch):
    import sqlite3

    import golive
    c = _golive_ready(tmp_path)
    manual_key = next(it["key"] for it in golive.get_check_detail(c, "GL-1")["items"] if it["check_type"] == "manual")
    db_file = c.execute("PRAGMA database_list").fetchone()[2]
    orig = golive.get_check_detail
    sneaked = []

    def hooked(conn, serial, read_only=False, in_tx=False):
        d = orig(conn, serial, read_only, in_tx)
        if not read_only and not sneaked:
            sneaked.append(None)
            other = sqlite3.connect(db_file, timeout=0.2)
            try:   # 另一位管理員此刻存「不通過」
                other.execute("UPDATE golive_check_result SET verdict='fail', checked_by='bob' WHERE item_key = ?", (manual_key,))
                other.commit()
                sneaked[0] = "written"
            except sqlite3.OperationalError:
                sneaked[0] = "blocked"
            finally:
                other.close()
        return d

    monkeypatch.setattr(golive, "get_check_detail", hooked)
    golive.pass_check(c, "GL-1", "admin")
    monkeypatch.setattr(golive, "get_check_detail", orig)
    d = golive.get_check_detail(c, "GL-1", read_only=True)
    assert sneaked[0] == "blocked"                                   # 舊版：written，結果「已通過但有阻擋」
    assert d["status"] == "passed" and d["blocking"] == []
    # 重送不覆寫通過人
    assert golive.pass_check(c, "GL-1", "bob")["passed_by"] == "admin"


def test_1091_1113_審查P3_有進行中交易就拋錯_不替呼叫端commit(tmp_path):
    import dyn_shell
    import golive
    c = _golive_ready(tmp_path)
    c.execute("UPDATE hardware SET remark = 'caller-pending' WHERE asset_serial = 'GL-1'")   # 呼叫端沒提交的寫入
    assert c.in_transaction
    with pytest.raises(RuntimeError):
        golive.pass_check(c, "GL-1", "admin")
    with pytest.raises(RuntimeError):
        dyn_shell.apply(c, [], "admin")
    c.rollback()
    assert c.execute("SELECT COALESCE(remark, '') FROM hardware WHERE asset_serial = 'GL-1'").fetchone()[0] != "caller-pending"
    assert golive.pass_check(c, "GL-1", "admin")["status"] == "passed"     # 對照：乾淨連線照常


# ---------------------------------------------------------------- 1093／1143 回檢同一筆交易
def test_1093_1143_回檢逐台證據寫失敗_告警一起回滾_重試補齊(tmp_path, monkeypatch):
    import golive
    c = _golive_ready(tmp_path)
    golive.pass_check(c, "GL-1", "admin")
    c.execute("INSERT INTO host_service (ip, proto, port, bind_addr, source) VALUES ('10.99.91.1','tcp',23,'0.0.0.0','test')")
    c.commit()                                                     # Telnet 被打開 → 該開告警
    orig = db.set_setting

    def boom(conn, key, value, commit=True):
        if key == golive.DRIFT_ASSETS_KEY:
            raise RuntimeError("disk full")
        return orig(conn, key, value, commit)

    monkeypatch.setattr(db, "set_setting", boom)
    with pytest.raises(RuntimeError):
        golive.run_drift_check(c)
    assert c.execute("SELECT COUNT(*) FROM baseline_drift WHERE status='open'").fetchone()[0] == 0   # 舊版：告警已存、證據沒存
    monkeypatch.setattr(db, "set_setting", orig)
    r = golive.run_drift_check(c)
    assert r["opened"] == 1
    assert golive.run_drift_check(c)["opened"] == 0                # 不重複開
    assert db.get_setting(c, golive.DRIFT_ASSETS_KEY)


def test_1101_1143_處置_值仍不同不能標已修復_已恢復的不被舊畫面重開(env):
    c = env.conn()
    db.insert_hardware(c, asset_serial="D-1", hostname="d1", ip="10.99.92.1")
    c.execute("INSERT INTO baseline_drift (asset_serial, item_key, baseline, current, status) VALUES ('D-1','telnet_disabled','absent','present','open')")
    c.execute("INSERT INTO baseline_drift (asset_serial, item_key, baseline, current, status) VALUES ('D-1','ssh_available','present','present','fixed')")
    c.commit()
    ids = {r[0]: r[1] for r in c.execute("SELECT item_key, id FROM baseline_drift")}
    c.close()
    u = env.client("alice")
    r = u.post(f"/api/drift/{ids['telnet_disabled']}/disposition", json={"status": "fixed", "note": ""})
    assert r.status_code == 400                                    # 舊版：200，資產頁暫顯通過
    assert u.post(f"/api/drift/{ids['telnet_disabled']}/disposition", json={"status": "ack", "note": "知道了"}).status_code == 200
    r = u.post(f"/api/drift/{ids['ssh_available']}/disposition", json={"status": "ack", "note": "舊畫面"})
    assert r.status_code == 409                                    # 舊版：200，已恢復的被重開成未結
    c = env.conn()
    st = {r[0]: r[1] for r in c.execute("SELECT item_key, status FROM baseline_drift")}
    c.close()
    assert st == {"telnet_disabled": "ack", "ssh_available": "fixed"}


# ---------------------------------------------------------------- 1057～1069 健檢／週基準
def test_1059_監聽未取得不判紅_失敗快照不當基準(tmp_path):
    import health_baseline as hb
    c = _scan_conn(tmp_path, [])
    ok = {"ip": "10.99.93.1", "reachable": True, "overall": "green", "ports": [{"port": 22}, {"port": 443}], "disks": []}
    hb.save_snapshot(c, ok)
    hb.save_snapshot(c, {"ip": "10.99.93.1", "reachable": False, "overall": "red", "ports": [], "disks": []})
    base = hb.latest_baseline(c, "10.99.93.1")
    assert base["reachable"] == 1                                    # 舊版：選到最新那份失敗快照
    cur = {"ip": "10.99.93.1", "reachable": True, "ports": [{"port": 22}, {"port": 8443}], "disks": [],
           "coverage": {"missing": []}}
    d = hb.diff(base, cur)
    assert d["level"] == "red" and d["missing_ports"] == [443] and d["new_ports"] == [8443]   # 正向對照：真的少了 443
    cur_unknown = {**cur, "ports": [], "coverage": {"missing": [{"key": "ports", "name": "監聽埠", "why": "x"}]}}
    d = hb.diff(base, cur_unknown)
    assert d["level"] == "unknown" and d["missing_ports"] == []      # 舊版：少 22／443 判紅


def _hc_seed(c, serial, ip, os_text, status="使用中", collect_ok=1):
    db.insert_hardware(c, asset_serial=serial, hostname=serial.lower(), ip=ip, os=os_text, asset_status=status)
    c.execute("UPDATE hardware SET collect_ok = ? WHERE asset_serial = ?", (collect_ok, serial))
    c.commit()


def test_1061_1063_週基準_傳WinRM憑證_停用資產不派_沒去成的不存基準(tmp_path, monkeypatch):
    import health_baseline as hb
    import quiet_window
    c = _scan_conn(tmp_path, [])
    _hc_seed(c, "W-ON", "10.99.94.1", "Red Hat Enterprise Linux 9")
    _hc_seed(c, "W-OFF", "10.99.94.2", "Red Hat Enterprise Linux 9", status="停用")
    seen = {}

    def fake(ips, **kw):
        seen.setdefault("ips", []).extend(ips)
        seen["creds_of"] = kw.get("creds_of")
        return [{"ip": ip, "reachable": False, "overall": "skipped", "not_probed": "沒有可用的 WinRM 收集憑證", "ports": []} for ip in ips]

    monkeypatch.setattr(quiet_window, "checkpoint", lambda *a, **k: True, raising=False)
    out = hb.run_weekly_baseline(c, _check_batch=fake)
    assert seen["ips"] == ["10.99.94.1"]                             # 舊版：停用的也派
    assert callable(seen["creds_of"])                                # 舊版：沒傳 → Windows 分支一律略過
    assert out["saved"] == 0 and out["not_probed"] == 1              # 舊版：存一筆「不可達、監聽 0」當基準
    assert hb.history(c, "10.99.94.1") == []


def test_1069_即時健檢_未納管Windows不派探測(env, monkeypatch):
    import health_probe
    import win_onboard
    c = env.conn()
    _hc_seed(c, "L-1", "10.99.95.1", "Red Hat Enterprise Linux 9")
    _hc_seed(c, "WIN-1", "10.99.95.2", "Windows Server 2019")
    c.close()
    probed = []
    monkeypatch.setattr(health_probe, "check_batch", lambda ips, **kw: probed.extend(ips) or
                        [{"ip": ip, "reachable": True, "overall": "green", "ports": [], "disks": []} for ip in ips])
    monkeypatch.setattr(win_onboard, "is_opted_in", lambda conn, ip, *a, **k: False)
    a = env.client("admin")
    r = a.post("/api/health-check/run", json={"ips": ["10.99.95.1", "http://10.99.95.1/", "10.99.95.2"]})
    assert r.status_code == 200, r.text
    assert probed == ["10.99.95.1"]                                  # 舊版：未納管 Windows .2 也到 WinRM 入口；重複網址只打一次
    by = {x["ip"]: x for x in r.json()["items"]}
    assert by["10.99.95.2"]["not_probed"] and "未納管" in by["10.99.95.2"]["not_probed"]
    assert r.json()["summary"]["not_probed"] == 1
    probed.clear()
    monkeypatch.setattr(win_onboard, "is_opted_in", lambda conn, ip, *a, **k: True)   # 對照：勾選納管的 Windows 照常
    a.post("/api/health-check/run", json={"ips": ["10.99.95.2"]})
    assert probed == ["10.99.95.2"]


# ---------------------------------------------------------------- 1107／1109／1113／1115 空殼併回／還原（D4）
def _other_write_blocked(db_file, sql, args) -> str:
    import sqlite3
    other = sqlite3.connect(str(db_file), timeout=0.2)
    try:
        other.execute(sql, args)
        other.commit()
        return "written"
    except sqlite3.OperationalError:
        return "blocked"
    finally:
        other.close()


def test_1113_1115_併回比對到搬移在同一寫鎖_中途改正本進不來(tmp_path, monkeypatch):
    import dyn_shell
    from test_dyn_auto_merge import CIA, SHELL, _conn, _seed
    c, p = _conn(tmp_path)
    _seed(c)
    orig = dyn_shell.classify
    seen = []

    def hooked(conn, shell):
        out = orig(conn, shell)
        seen.append(_other_write_blocked(p, "UPDATE hardware SET mac = ? WHERE asset_serial = ?", ("00:11:22:33:44:55", CIA)))
        return out

    monkeypatch.setattr(dyn_shell, "classify", hooked)
    r = dyn_shell.apply(c, [{"shell_serial": SHELL, "target_serial": CIA}], "admin")
    assert seen == ["blocked"]                                       # 舊版：written（正本換 MAC 後照樣併）
    assert len(r["merged"]) == 1


def test_1107_1109_還原_序號碰撞講清楚_重送與並發不覆寫還原人(tmp_path, monkeypatch):
    import dyn_shell
    import lifecycle
    from test_dyn_auto_merge import CIA, SHELL, _conn, _seed
    c, p = _conn(tmp_path)
    _seed(c)
    dyn_shell.apply(c, [{"shell_serial": SHELL, "target_serial": CIA}], "admin")
    aid = c.execute("SELECT id FROM shell_merge_audit").fetchone()[0]
    db.insert_hardware(c, asset_serial=SHELL, hostname="other", ip="10.99.96.9")   # 同序號新空殼
    c.commit()
    with pytest.raises(ValueError, match="已有另一筆"):                # 舊版：IntegrityError → 500
        dyn_shell.restore(c, aid, "admin")
    c.execute("DELETE FROM hardware WHERE asset_serial = ? AND hostname = 'other'", (SHELL,))
    c.commit()
    orig = lifecycle.unarchive_merged
    seen = []

    def hooked(conn, serial):
        seen.append(_other_write_blocked(p, "UPDATE shell_merge_audit SET restored_at = 'x', restored_by = 'bob' WHERE id = ?", (aid,)))
        return orig(conn, serial)

    monkeypatch.setattr(lifecycle, "unarchive_merged", hooked)
    dyn_shell.restore(c, aid, "admin")
    assert seen == ["blocked"]                                       # 舊版：另一位管理員同時還原會互相覆寫
    assert c.execute("SELECT restored_by FROM shell_merge_audit WHERE id = ?", (aid,)).fetchone()[0] == "admin"
    with pytest.raises(ValueError, match="還原過"):
        dyn_shell.restore(c, aid, "bob")
