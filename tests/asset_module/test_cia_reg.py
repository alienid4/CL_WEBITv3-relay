"""9.4 CIA 補登（P2 VM 補登，試辦）。全部假資料（IP 一律 10.99.x、主機名與 APID 虛構）。

母體＝2.1「未登錄 CIA」∩ VM、排除清單／留一回測與信心／30 欄填法（資產狀態不預填、帶出欄附來源）／分派（同組不拆、LPT、試辦類別）
／每人 Excel（公式、鎖定、跳脫）／交回（公式重算、鎖定欄比對、改派舊版拒收、巨集與多餘工作表拒收、冪等）／彙整／CIA 匯入後核對
（⚠D4：沒建表時完全不動資料庫）／verify_numbers 不變式（含突變抽查）。
"""
import io
import json
import sys
import zipfile
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import cia_reg  # noqa: E402
import db  # noqa: E402
import verify_numbers as vn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"
WIN = "Windows Server 2019"
ROOM = "01_甲機房"


def _cia(c, serial, host, ip, api_id, **kw):
    base = dict(asset_serial=serial, hostname=host, ip=ip, api_id=api_id, is_vm=1, os=WIN, rack_no="(VM)",
                physical_location=ROOM, device_model="VMware Virtual Platform", asset_status="使用中",
                inventory_division="處甲", inventory_department="部甲", group_name="G甲", asset_name="甲系統主機",
                asset_purpose="AP", owner="王一", usage_unit="單位甲", user_name="李二", custodian="陳三",
                integrity=2, confidentiality=2, availability=2, owning_company="證券", environment="正式")
    base.update(kw)
    return db.insert_hardware(c, **base)


def _vc(c, serial, host, ip, uuid, cluster="CL-A", esxi="esx1", os_=WIN, folder=None):
    hid = db.insert_hardware(c, asset_serial=serial, hostname=host, ip=ip, is_vm=1, os=os_, vm_uuid=uuid)
    payload = {"vm_uuid": uuid, "hostname": host, "cluster": cluster, "esxi_host": esxi, "powerstate": "poweredOn",
               "vi_sdk_server": "vc1", "raw": {"Folder": folder} if folder else {}}
    c.execute("INSERT INTO source_record (source, source_key, payload, resolved_status, resolved_hardware_id) VALUES ('vcenter',?,?,?,?)",
              (uuid, json.dumps(payload, ensure_ascii=False), "new", hid))
    c.commit()
    return hid


def _seed(c):
    c.executemany("INSERT INTO business_system (api_id, name) VALUES (?, ?)", [("N-001", "甲系統"), ("N-002", "乙系統")])
    for i in range(1, 7):                                   # N-001：app 前綴、10.99.1.x、CL-A
        _cia(c, f"S{i}", f"app{i:02d}", f"10.99.1.{i}", "N-001")
        _vc(c, f"VC-c{i}", f"app{i:02d}", f"10.99.1.{i}", f"u-c{i}")
    for i in range(1, 5):                                   # N-002：bat 前綴、10.99.2.x、CL-B
        _cia(c, f"T{i}", f"bat{i:02d}", f"10.99.2.{i}", "N-002", inventory_department="部乙", owner="趙四")
        _vc(c, f"VC-d{i}", f"bat{i:02d}", f"10.99.2.{i}", f"u-d{i}", cluster="CL-B", esxi="esx2")
    # 未登錄 CIA 的 VM
    _vc(c, "VC-n1", "app07", "10.99.1.7", "u-n1")                         # 高信心 N-001
    _vc(c, "VC-n2", "app08", "10.99.1.8", "u-n2")
    _vc(c, "VC-n3", "zz01", "10.99.9.1", "u-n3", cluster="CL-Z", esxi="esx9")   # 低信心
    _vc(c, "VC-n4", "lin01", "10.99.9.2", "u-n4", cluster="CL-Z", esxi="esx9", os_="Red Hat Enterprise Linux 8")  # LINUX：非試辦


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    cia_reg.save_settings(c, {"WIN": [{"name": "甲"}, {"name": "乙"}], "LINUX": [{"name": "丙"}]}, ["WIN"])
    # 回測門檻放寬到這份小資料測得出來（真實門檻在規則檔：≥20 次預測）
    rules = cia_reg.load_rules()
    rules["suggest"]["backtest_min_predictions"] = 3
    cia_reg._RULES_OVERRIDE = rules
    yield c
    cia_reg._RULES_OVERRIDE = None
    c.close()


def test_母體與排除_回測_信心(conn):
    pv = cia_reg.preview(conn)
    keys = {r["hostname"] for r in pv["rows"]}
    assert keys == {"app07", "app08", "zz01"}
    assert pv["not_pilot"] == 1 and pv["not_pilot_rows"][0]["hostname"] == "lin01"
    by = {r["hostname"]: r for r in pv["rows"]}
    assert by["app07"]["conf"] == "high" and by["app07"]["apid"] == "N-001"
    assert by["zz01"]["conf"] == "low" and by["zz01"]["apid"] is None
    bt = pv["backtest"]
    assert bt["population"] == 10
    pre = {b["id"]: b for b in bt["bases"]}
    assert pre["name_prefix"]["precision"] == 1.0 and pre["name_prefix"]["eligible"]
    assert {r["assignee"] for r in pv["rows"] if r["hostname"].startswith("app")} in ({"甲"}, {"乙"})   # 同組不拆


def test_資產狀態不預填_帶出欄附來源(conn):
    p = cia_reg.plan(conn)
    f = next(c for c in p["in_round"] if c["hostname"] == "app07")["fill"]
    assert f["asset_status"]["v"] == "" and f["api_id"]["v"] == "N-001"
    assert f["owner"]["v"] == "王一" and "6 筆中 6 筆" in f["owner"]["src"]
    assert f["physical_location"]["kind"] == "suggest"
    low = next(c for c in p["in_round"] if c["hostname"] == "zz01")["fill"]
    assert low["api_id"]["v"] == cia_reg.UNDETERMINED and low["owner"]["v"] == ""


# ---------------------------------------------------------------- 分派
def _round(conn):
    pv = cia_reg.preview(conn)
    with pytest.raises(cia_reg.Stale):
        cia_reg.create_round(conn, "tester", "wrong")
    out = cia_reg.create_round(conn, "tester", pv["fingerprint"])
    return out, {i["hostname"]: i for i in cia_reg.list_items(conn)}


def test_建立分派_未結案唯一_第二次預覽排除(conn):
    out, items = _round(conn)
    assert out["items"] == 3
    assert {i["status"] for i in items.values()} == {"assigned"}
    assert items["app07"]["assignee"] == items["app08"]["assignee"]           # 同組（APID N-001）同一人
    assert all(i["track_code"] == f"CR1-{i['id']}" for i in items.values())
    pv2 = cia_reg.preview(conn)
    assert pv2["in_round"] == 0 and pv2["excluded_by_reason"].get("已在進行中的補登裡") == 3
    with pytest.raises(Exception):
        conn.execute("INSERT INTO cia_reg_item (round_id, machine_key, status, created_at) VALUES (1, ?, 'assigned', 'x')",
                     (cia_reg.list_items(conn)[0]["machine_key"],))
    conn.rollback()


def test_UUID衝突_轉待確認不分派(conn):
    hid = conn.execute("SELECT id FROM hardware WHERE asset_serial = 'VC-n2'").fetchone()[0]
    conn.execute("UPDATE hardware SET vm_uuid = 'u-c1' WHERE id = ?", (hid,))      # 跟 app01 共用一個 UUID
    conn.commit()
    p = cia_reg.plan(conn)
    app08 = next(x for x in p["in_round"] if x["hostname"] == "app08")
    assert app08["review"] and app08["assignee"] is None


# ---------------------------------------------------------------- Excel
def _xlsx(conn, who):
    buf, n, name = cia_reg.person_xlsx(conn, who)
    return openpyxl.load_workbook(io.BytesIO(buf.getvalue())), n


def _who(items, host):
    return items[host]["assignee"]


def _fill_row(ws, host, **vals):
    head = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        if row[head.index("主機名稱")].value == host:
            for h, v in vals.items():
                row[head.index(h)].value = v
            return
    raise AssertionError(host)


def _bytes(wb):
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_每人Excel_工作表_鎖定_公式_跳脫(conn):
    _, items = _round(conn)
    wb, n = _xlsx(conn, _who(items, "app07"))
    assert wb.sheetnames == list(cia_reg.PERSON_SHEETS)
    ws = wb[cia_reg.SHEET_FILL]
    head = [c.value for c in ws[1]]
    assert head[1:31] == [h for h, _ in cia_reg.columns()] and len(head) == 1 + 30 + len(cia_reg.EXTRA_COLS)
    row = next(r for r in ws.iter_rows(min_row=2) if r[head.index("主機名稱")].value == "app07")
    assert row[head.index("API ID")].value == "N-001"
    assert str(row[head.index("擁有者")].value).startswith("=IFERROR")           # 帶出欄是公式
    assert row[head.index("資產狀態")].value in ("", None)
    assert ws.protection.sheet and row[head.index("主機名稱")].protection.locked and not row[head.index("API ID")].protection.locked
    assert wb[cia_reg.SHEET_DERIVED].sheet_state == "hidden"
    assert cia_reg.safe("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0" and cia_reg.safe("@x\x01") == "'@x"


def test_交回_公式重算_退回_通過_冪等(conn):
    _, items = _round(conn)
    who = _who(items, "app07")
    wb, _ = _xlsx(conn, who)
    ws = wb[cia_reg.SHEET_FILL]
    _fill_row(ws, "app07", **{"APID 確認": "是", "環境別": "正式", "資產狀態": "使用中", "作業系統": WIN})
    _fill_row(ws, "app08", **{"APID 確認": "是", "環境別": "正式"})                      # 資產狀態沒填 → 退回
    data = _bytes(wb)
    pv = cia_reg.upload_preview(conn, data, "x.xlsx")
    assert pv["n"]["pass"] == 1 and pv["n"]["bounce"] == 1 and pv["owner"] == who
    assert any("資產狀態" in w for w in pv["bounce"][0]["why"])
    res = cia_reg.upload_confirm(conn, data, "x.xlsx", pv["file_sha256"], "tester")
    again = cia_reg.upload_confirm(conn, data, "x.xlsx", pv["file_sha256"], "tester")
    assert again.get("already") and again["n"] == res["n"]
    it = {i["hostname"]: i for i in cia_reg.list_items(conn)}
    assert it["app07"]["status"] == "passed" and it["app08"]["status"] == "bounced"
    row = conn.execute("SELECT filled_json FROM cia_reg_item WHERE hostname = 'app07'").fetchone()[0]
    assert json.loads(row)["owner"] == "王一"                 # 公式不信，伺服器端重算
    assert len(cia_reg.item_events(conn, it["app07"]["id"])) == 2
    # 退回檔只含退回的那台
    buf, n, _ = cia_reg.person_xlsx(conn, who, 1, bounced_only=True)
    assert n == 1


def test_交回_鎖定欄被改_拒收_改派後舊檔拒收(conn):
    _, items = _round(conn)
    who = _who(items, "app07")
    wb, _ = _xlsx(conn, who)
    _fill_row(wb[cia_reg.SHEET_FILL], "app07", **{"IP": "10.99.1.99", "APID 確認": "是"})
    cia_reg.reassign(conn, [items["app08"]["id"]], "乙" if who == "甲" else "甲", "請假", "tester")
    pv = cia_reg.upload_preview(conn, _bytes(wb), "x.xlsx")
    whys = {x["hostname"]: x["why"] for x in pv["refused"]}
    assert "鎖定欄被改過" in whys["app07"] and "已改派" in whys["app08"]
    assert cia_reg.list_items(conn)[1]["assign_ver"] in (1, 2)


def test_交回_巨集_外部連結_多餘工作表拒收(conn):
    _, items = _round(conn)
    wb, _ = _xlsx(conn, _who(items, "app07"))
    wb.create_sheet("多的")
    with pytest.raises(ValueError, match="工作表"):
        cia_reg.read_return(conn, _bytes(wb))
    for name, msg in (("xl/vbaProject.bin", "巨集"), ("xl/externalLinks/externalLink1.xml", "外部連結")):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(name, b"x")
        with pytest.raises(ValueError, match=msg):
            cia_reg.read_return(conn, buf.getvalue())


def test_說不登記_確認後撤出(conn):
    _, items = _round(conn)
    wb, _ = _xlsx(conn, _who(items, "zz01"))
    _fill_row(wb[cia_reg.SHEET_FILL], "zz01", **{"不登記原因": "已下線"})
    data = _bytes(wb)
    pv = cia_reg.upload_preview(conn, data, "z.xlsx")
    assert pv["n"]["claim"] == 1
    cia_reg.upload_confirm(conn, data, "z.xlsx", pv["file_sha256"], "tester")
    iid = cia_reg.list_items(conn, "claim")[0]["id"]
    assert cia_reg.claim_decide(conn, [iid], True, "確認", "tester")["done"] == 1
    assert cia_reg.list_items(conn, "excluded")[0]["id"] == iid


# ---------------------------------------------------------------- 彙整、核對
def test_彙整_CIA匯入後標已登記(conn):
    _, items = _round(conn)
    wb, _ = _xlsx(conn, _who(items, "app07"))
    _fill_row(wb[cia_reg.SHEET_FILL], "app07", **{"APID 確認": "是", "環境別": "正式", "資產狀態": "使用中", "作業系統": WIN})
    data = _bytes(wb)
    cia_reg.upload_confirm(conn, data, "x.xlsx", cia_reg.upload_preview(conn, data, "x")["file_sha256"], "tester")
    mp = cia_reg.merge_preview(conn)
    assert mp["items"] == 1
    with pytest.raises(cia_reg.Stale):
        cia_reg.merge(conn, "tester", "bad")
    out = cia_reg.merge(conn, "tester", mp["fingerprint"])
    buf, n, _ = cia_reg.batch_xlsx(conn, out["batch_id"], 1)
    wb3 = openpyxl.load_workbook(io.BytesIO(buf.getvalue()))
    assert wb3.sheetnames[:6] == ["1 說明", "2 修正明細", "3 補登清單", "4 待填檢查", "5 選項清單", "6 修正後全表（參考）"]
    w3 = wb3["3 補登清單"]
    assert n == 1 and [c.value for c in w3[2]][[c.value for c in w3[1]].index("主機名稱")] == "app07"
    _cia(conn, "S99", "app07", "10.99.1.7", "N-001")             # CIA 登好、匯入：同名同 IP 的 CIA 列出現
    res = cia_reg.reconcile_after_import(conn, "cia.xlsx", "abc", "tester")
    assert res["registered"] == 1
    it = cia_reg.list_items(conn, "registered")[0]
    assert it["cia_serial"] == "S99" and it["closed_file"] == "cia.xlsx"


def test_沒建表_核對什麼都不做(tmp_path):
    p = tmp_path / "e.db"
    db.init_db(p)
    c = db.get_connection(p)
    assert cia_reg.reconcile_after_import(c, "f.xlsx", "x", "t") is None
    assert not cia_reg._ready(c)
    c.close()


def test_verify_numbers_綠_突變會紅(conn):
    _round(conn)
    before = vn.FAILS
    vn.check_cia_reg(conn)
    assert vn.FAILS == before, [r for r in vn.RESULTS if r["ok"] is False][-5:]
    conn.execute("UPDATE cia_reg_item SET status = 'passed' WHERE id = (SELECT MAX(id) FROM cia_reg_item)")   # 改狀態不寫事件
    conn.commit()
    vn.check_cia_reg(conn)
    assert vn.FAILS == before + 1
    vn.FAILS = before


def test_規則檔沒有真實IP與真實機房():
    raw = cia_reg.RULES_PATH.read_text(encoding="utf-8")
    assert "10.93." not in raw and "10.92." not in raw and "內湖" not in raw and "板橋" not in raw


# ---------------------------------------------------------------- API
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    db.create_user(c, "tester", auth.hash_password(_PW))
    c.close()

    def _get_db():
        cc = db.get_connection(p)
        try:
            yield cc
        finally:
            cc.close()
    api.app.dependency_overrides[api.get_db] = _get_db
    rules = cia_reg.load_rules()
    rules["suggest"]["backtest_min_predictions"] = 3
    cia_reg._RULES_OVERRIDE = rules
    cl = TestClient(api.app)
    assert cl.post("/api/auth/login", json={"username": "tester", "password": _PW}).status_code == 200
    yield cl
    cia_reg._RULES_OVERRIDE = None
    api.app.dependency_overrides.clear()


def test_API全流程(client):
    cl = client
    assert cl.get("/api/cia-reg/summary").json()["counts"]["total"] == 0
    r = cl.put("/api/cia-reg/settings", json={"assignees": {"WIN": [{"name": "甲"}]}, "pilot": ["WIN"]})
    assert r.status_code == 200, r.text
    assert cl.put("/api/cia-reg/settings", json={"pilot": ["XX"]}).status_code == 400
    pv = cl.get("/api/cia-reg/preview").json()
    assert pv["in_round"] == 3
    assert cl.post("/api/cia-reg/rounds", json={"fingerprint": "bad"}).status_code == 409
    assert cl.post("/api/cia-reg/rounds", json={"fingerprint": pv["fingerprint"]}).status_code == 200
    s = cl.get("/api/cia-reg/summary").json()
    assert s["counts"]["assigned"] == 3 and s["progress"][0]["total"] == 3 and s["files"][0]["assignee"] == "甲"
    d = cl.get("/api/cia-reg/items", params={"category": "WIN", "assignee": "甲", "metric": "remaining"}).json()
    assert d["total"] == 3
    x = cl.get("/api/cia-reg/files/甲.xlsx")
    assert x.status_code == 200 and x.headers["x-row-count"] == "3"
    up = cl.post("/api/cia-reg/upload/preview", files={"file": ("a.xlsx", x.content, "application/octet-stream")})
    assert up.status_code == 200 and up.json()["n"]["bounce"] == 3
    bt = cl.get("/api/cia-reg/backtest").json()
    assert bt["population"] == 10
    assert cl.get("/api/cia-reg/backtest", params={"basis": "name_prefix", "outcome": "correct"}).json()["total"] >= 1
    assert cl.get("/api/cia-reg/items/1/events").json()["items"][0]["action"] == "create"


# ---------------------------------------------------------------- 審查 P2-1：找不到先待確認，連續兩次才結案
def _drop(conn, serial):
    conn.execute("DELETE FROM source_record WHERE resolved_hardware_id = (SELECT id FROM hardware WHERE asset_serial = ?)", (serial,))
    conn.execute("DELETE FROM hardware WHERE asset_serial = ?", (serial,))
    conn.commit()


def test_找不到_先待確認_連續兩次才消失_找回來恢復(conn):
    _, items = _round(conn)
    _drop(conn, "VC-n1")                                   # app07 從所有來源消失
    r1 = cia_reg.reconcile_after_import(conn, "c1.xlsx", "a", "t")
    assert r1["missing"] == 1 and r1["gone"] == 0
    it = cia_reg.list_items(conn, "missing")[0]
    assert it["hostname"] == "app07" and it["prev_status"] == "assigned"
    r2 = cia_reg.reconcile_after_import(conn, "c2.xlsx", "b", "t")
    assert r2["gone"] == 1 and cia_reg.list_items(conn, "gone")[0]["hostname"] == "app07"
    # 另一台：找不到一次之後又出現（改名改回來／鍵恢復）→ 回原狀態、不結案
    row = conn.execute("SELECT * FROM hardware WHERE asset_serial = 'VC-n2'").fetchone()
    _drop(conn, "VC-n2")
    cia_reg.reconcile_after_import(conn, "c3.xlsx", "c", "t")
    assert {i["hostname"] for i in cia_reg.list_items(conn, "missing")} == {"app08"}
    _vc(conn, "VC-n2", "app08", "10.99.1.8", "u-n2")
    r4 = cia_reg.reconcile_after_import(conn, "c4.xlsx", "d", "t")
    assert r4["found"] == 1
    app08 = next(i for i in cia_reg.list_items(conn) if i["hostname"] == "app08")
    assert app08["status"] == "assigned" and app08["miss_count"] == 0
    assert row is not None


def test_找不到_管理員確認消失或恢復(conn):
    _round(conn)
    _drop(conn, "VC-n3")
    cia_reg.reconcile_after_import(conn, "c1.xlsx", "a", "t")
    iid = cia_reg.list_items(conn, "missing")[0]["id"]
    assert cia_reg.missing_decide(conn, [iid], False, "改名了", "t")["done"] == 1
    assert next(i for i in cia_reg.list_items(conn) if i["id"] == iid)["status"] == "assigned"
    cia_reg.reconcile_after_import(conn, "c2.xlsx", "b", "t")
    assert cia_reg.missing_decide(conn, [iid], True, "確認下線", "t")["done"] == 1
    assert cia_reg.list_items(conn, "gone")[0]["id"] == iid
    assert [e["action"] for e in cia_reg.item_events(conn, iid)] == ["create", "missing", "reopen_missing", "missing", "confirm_gone"]


# ---------------------------------------------------------------- 負責人（AD）、上限、暫停
def _ad(conn):
    conn.execute(cia_ad_sql())
    conn.executemany("INSERT INTO ad_person (employee_id, display_name, mail, dept_name, enabled) VALUES (?,?,?,?,?)",
                     [("E1", "甲君", "e1@x.test", "部甲", 1), ("E2", "乙君", "e2@x.test", "部甲", 1),
                      ("E3", "停用君", "e3@x.test", "部甲", 0), ("E4", "沒信箱", "", "部甲", 1)])
    conn.commit()


def cia_ad_sql():
    import ad_import
    return ad_import.TABLE_SQL


def test_負責人只能從AD挑_停用與沒信箱擋下_離職警示(conn):
    _ad(conn)
    with pytest.raises(ValueError, match="停用"):
        cia_reg.save_settings(conn, {"WIN": [{"key": "E3"}]}, None)
    with pytest.raises(ValueError, match="信箱"):
        cia_reg.save_settings(conn, {"WIN": [{"key": "E4"}]}, None)
    with pytest.raises(ValueError, match="AD 名單裡沒有"):
        cia_reg.save_settings(conn, {"WIN": [{"key": "NOPE"}]}, None)
    st = cia_reg.save_settings(conn, {"WIN": [{"key": "E1", "cap": 5}, {"key": "E2", "paused": True}]}, ["WIN"])
    assert [(p["key"], p["name"], p["email"], p["cap"], p["paused"]) for p in st["assignees"]["WIN"]] == \
        [("E1", "甲君", "e1@x.test", 5, False), ("E2", "乙君", "e2@x.test", None, True)]
    assert [p["key"] for p in cia_reg.people_search(conn, "")] == ["E1", "E2"]
    conn.execute("UPDATE ad_person SET enabled = 0 WHERE employee_id = 'E1'")      # 之後離職
    conn.commit()
    assert "停用" in cia_reg.get_settings(conn)["assignees"]["WIN"][0]["warn"]


def test_上限與暫停_組不拆_放不下留待分派附原因(conn):
    _ad(conn)
    cia_reg.save_settings(conn, {"WIN": [{"key": "E1", "cap": 1}, {"key": "E2", "paused": True}]}, ["WIN"])
    pv = cia_reg.preview(conn)
    by = {r["hostname"]: r for r in pv["rows"]}
    assert by["app07"]["assignee"] is None and "組不拆" in by["app07"]["unassigned_why"]     # app07/app08 同組 2 台 > 上限 1
    assert by["zz01"]["assignee"] == "E1"
    assert any("沒分出去" in w for w in pv["warnings"])
    sp = {x["key"]: x for x in pv["spread"]}
    assert sp["E1"]["new"] == 1 and sp["E1"]["cap"] == 1 and not sp["E1"]["over"] and sp["E2"]["paused"]


# ---------------------------------------------------------------- 檔案登記、待重寄、催收、ZIP、輪次
def test_檔案登記_改派後作廢_標記已寄_待重寄_催收(conn):
    _, items = _round(conn)
    who = _who(items, "app07")
    other = "乙" if who == "甲" else "甲"
    cia_reg.person_xlsx(conn, who, 1, False, "t")
    f = cia_reg.files(conn, who)[0]
    assert f["state"] == "現行" and f["machines"] == 2 and len(f["sha256"]) == 64
    assert {i["hostname"] for i in cia_reg.list_items(conn, metric="resend")} >= {"app07", "app08"}
    cia_reg.mark_sent(conn, f["id"], "t")
    assert not ({"app07", "app08"} & {i["hostname"] for i in cia_reg.list_items(conn, metric="resend")})
    ch = {c["assignee"]: c for c in cia_reg.chase(conn)}
    assert ch[who]["due"] == 2 and ch[who]["resend"] == 0 and ch[who]["last_sent"] and ch[who]["next"] == "等交回"
    cia_reg.reassign(conn, [items["app08"]["id"]], other, "請假", "t")
    assert cia_reg.files(conn, who)[0]["state"] == "作廢"
    assert "app08" in {i["hostname"] for i in cia_reg.list_items(conn, metric="resend")}
    cia_reg.set_deadline(conn, 1, "2026-11-01", "t")
    ch = {c["assignee"]: c for c in cia_reg.chase(conn, today="2026-11-05")}
    assert ch[other]["overdue_days"] == 4 and ch[other]["deadline"] == "2026-11-01"
    rd = cia_reg.rounds(conn)[0]
    assert rd["deadline"] == "2026-11-01" and rd["completion"] == 0 and rd["snapshot"]["pilot"] == ["WIN"]


def test_整批ZIP_含派送清冊(conn):
    _round(conn)
    data, name, n = cia_reg.zip_files(conn, "t")
    z = zipfile.ZipFile(io.BytesIO(data))
    assert "派送清冊.xlsx" in z.namelist() and len(z.namelist()) == n + 1
    ws = openpyxl.load_workbook(io.BytesIO(z.read("派送清冊.xlsx"))).active
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(rows) == n and sum(r[5] for r in rows) == 3
    assert all(len(cia_reg.files(conn)) >= n for _ in [0])


# ---------------------------------------------------------------- 審查 P2-2：匯入後立刻核對（跨請求快取要失效）
def _cia_xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "硬體"
    ws.append(["資產序號", "主機名稱", "IP", "API ID", "資產實體位置", "機櫃編號"])
    for r in rows:
        ws.append(list(r))
    wb.create_sheet("人員").append(["資產序號", "人員姓名"])
    wb.create_sheet("軟體").append(["資產序號", "資料庫/軟體"])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_API_匯入CIA後立刻核對_標已登記(client, tmp_path, monkeypatch):
    import cia_import_preview as cp
    monkeypatch.setattr(cp, "PREVIEW_DIR", tmp_path / "preview")
    cl = client
    cl.put("/api/cia-reg/settings", json={"assignees": {"WIN": [{"name": "甲"}]}, "pilot": ["WIN"]})
    cl.get("/api/cia-reg/summary")                     # 先讓機隊算一次、進快取
    pv = cl.get("/api/cia-reg/preview").json()
    assert cl.post("/api/cia-reg/rounds", json={"fingerprint": pv["fingerprint"], "deadline": "2026-12-01"}).status_code == 200
    f = _cia_xlsx([("S900", "app07", "10.99.1.7", "N-001", "01_甲機房", "(VM)")])
    up = cl.post("/api/import/excel/preview", files={"file": ("cia_20261120.xlsx", f, "application/octet-stream")})
    assert up.status_code == 200, up.text
    r = cl.post("/api/import/excel/confirm", json={"token": up.json()["token"], "file_sha256": up.json()["file_sha256"]})
    assert r.status_code == 200, r.text
    assert r.json()["cia_reg"]["registered"] == 1, r.json()["cia_reg"]
    items = cl.get("/api/cia-reg/items", params={"status": "registered"}).json()["items"]
    assert [i["hostname"] for i in items] == ["app07"] and items[0]["cia_serial"] == "S900"
    assert cl.get("/api/cia-reg/rounds").json()["items"][0]["deadline"] == "2026-12-01"
    z = cl.get("/api/cia-reg/files.zip")
    assert z.status_code == 200 and z.headers["x-file-count"] == "1"
    fl = cl.get("/api/cia-reg/files").json()
    assert cl.post(f"/api/cia-reg/files/{fl['items'][0]['id']}/sent").status_code == 200
    assert cl.get("/api/cia-reg/chase").json()["items"][0]["last_sent"]


# ---------------------------------------------------------------- 審查 P1-1：可逆性
def test_同一份CIA檔重匯_不算第二次找不到(conn):
    _round(conn)
    _drop(conn, "VC-n1")
    cia_reg.reconcile_after_import(conn, "c1.xlsx", "sha-A", "t")
    r = cia_reg.reconcile_after_import(conn, "c1.xlsx", "sha-A", "t")        # 同一份檔再匯一次
    assert r["same_file"] == 1 and r["gone"] == 0
    assert cia_reg.list_items(conn, "missing")[0]["miss_count"] == 1
    r = cia_reg.reconcile_after_import(conn, "c2.xlsx", "sha-B", "t")        # 不同的檔才算第二次
    assert r["gone"] == 1


def test_資產已消失_可恢復成原狀態_留事件(conn):
    _round(conn)
    _drop(conn, "VC-n1")
    cia_reg.reconcile_after_import(conn, "c1.xlsx", "a", "t")
    cia_reg.reconcile_after_import(conn, "c2.xlsx", "b", "t")
    it = cia_reg.list_items(conn, "gone")[0]
    with pytest.raises(ValueError):
        cia_reg.restore_gone(conn, [it["id"]], "", "t")
    assert cia_reg.restore_gone(conn, [it["id"]], "其實改名了", "t")["done"] == 1
    back = next(i for i in cia_reg.list_items(conn) if i["id"] == it["id"])
    assert back["status"] == "assigned" and back["closed_at"] is None and back["miss_count"] == 0
    ev = cia_reg.item_events(conn, it["id"])
    assert ev[-1]["action"] == "restore_gone" and ev[-1]["note"] == "其實改名了"
    before = vn.FAILS
    vn.check_cia_reg(conn)
    assert vn.FAILS == before


def test_一次超過兩成找不到_當可疑匯入不計次(conn, monkeypatch):
    _round(conn)
    monkeypatch.setattr(cia_reg, "SUSPICIOUS_MIN_OPEN", 1)
    _drop(conn, "VC-n1")                                    # 3 台掉 1 台＝33% > 20%
    r = cia_reg.reconcile_after_import(conn, "bad.xlsx", "x", "t")
    assert r["suspicious"] and "疑似匯錯檔" in r["warning"] and r["missing"] == 0
    assert not cia_reg.list_items(conn, "missing")
    assert cia_reg.last_reconcile(conn)["warning"] == r["warning"]
    monkeypatch.setattr(cia_reg, "SUSPICIOUS_MISS_RATIO", 0.5)            # 門檻以下就照常計次
    assert cia_reg.reconcile_after_import(conn, "ok.xlsx", "y", "t")["missing"] == 1


# ---------------------------------------------------------------- 審查 P2-1／P2-2
def test_檔名含員編_同名的人分得開_去控制字元(conn):
    _ad(conn)
    conn.execute("UPDATE ad_person SET display_name = '同名君' WHERE employee_id IN ('E1', 'E2')")
    conn.commit()
    cia_reg.save_settings(conn, {"WIN": [{"key": "E1"}, {"key": "E2"}]}, ["WIN"])
    _round(conn)
    names = {cia_reg.person_xlsx(conn, k)[2] for k in ("E1", "E2")}
    assert len(names) == 2 and any("同名君_E1_" in x for x in names) and any("同名君_E2_" in x for x in names)
    data, zname, n = cia_reg.zip_files(conn, "t")
    assert len(set(zipfile.ZipFile(io.BytesIO(data)).namelist())) == n + 1
    assert cia_reg.fname_safe("a\x01b\x7fc/d") == "a_b_c_d"


def test_手動改派給暫停或超過上限_允許但警示並記在事件(conn):
    _ad(conn)
    cia_reg.save_settings(conn, {"WIN": [{"key": "E1"}, {"key": "E2", "cap": 0}]}, ["WIN"])
    _, items = _round(conn)
    iid = items["zz01"]["id"]
    r = cia_reg.reassign(conn, [iid], "E2", "支援", "t")
    assert r["done"] == 1 and "超過上限" in r["warning"]
    assert "警示" in cia_reg.item_events(conn, iid)[-1]["note"]
    cia_reg.save_settings(conn, {"WIN": [{"key": "E1", "paused": True}, {"key": "E2"}]}, None)
    assert "暫停" in cia_reg.reassign_warning(conn, "E1", 1)
