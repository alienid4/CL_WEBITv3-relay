"""2.060.20（S10）伺服器資料彙整表（參考來源）：讀檔、兩段式匯入、比對分類、9.4 頁籤 API、verify_numbers 不變式。
測試資料全用假 IP（10.99.x），真檔只在 .14 匯入（不進 repo、不進 relay）。"""
import io
import sys
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import srv_sheet as ss  # noqa: E402
import verify_numbers as vn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"
EN = ["序號", "Address", "UseFor", "SystemLevel", "Vm", "Environment", "ApOwner", "InfraOwner", "Os", "OsVersion", "Host Name",
      "Location", "Cabinet", "資訊資產名稱 ", "ID", "標記時間"]
ZH = ["編號", "IP", "資產用途", "系統等級", "實體/VM", "環境別", "AP負責人", "Infra負責人", "作業系統", "作業系統版本", "Host Name",
      "放置地點", "機櫃位置", "資訊資產名稱 ", "ID (請勿刪)\n新建主機請留空", "標記時間 (請勿刪)\n新建主機請留空"]
# 編號, IP, 用途, 等級, 實體/VM, 環境, AP, Infra, OS, 版本, Host, 地點, 機櫃, 資產名稱, ID, 標記時間
ROWS = [
    (1, "10.99.1.1", "甲系統AP", "第一類系統設備", "VM(Vmware)", "PRD", "王一", "林五", "Windows", "Windows Server 2019", "app01",
     "甲機房", "", "甲系統", 1, 45587.5),                                      # 一致
    (2, "10.99.1.2", "乙系統AP", "第二類系統設備", "VM(Vmware)", "PRD", "趙六", "林五", "Windows", "Windows Server 2012 R2", "app02x",
     "乙機房", "", "乙系統", 2, datetime(2024, 10, 22, 14, 18)),                  # 主機名、OS 年份、負責人、機房都不一致
    (3, "010.099.001.003", "開戶 Listener IP", "第一類系統設備", "VM(Vmware)", "VIP", "王一", "", "Linux", "", "db03",
     "", "", "POC 組態強化測試主機", 3, None),                                    # CIA 沒有＋同列矛盾（第一類卻寫 POC）
    (4, "10.99.1.4", "風控測試機", "測試設備", "VM", "UAT", "王一", "", "Linux", "", "t04", "", "", "", "", None),  # 已關機
    (5, "#N/A", "沒 IP 的列", "", "", "PRD", "", "", "", "", "", "", "", "", "", None),
    (6, "10.99.1.6", "全新上線", "第三類系統設備", "VM", "PRD", "王一", "", "Linux", "", "new06", "", "", "", "", None),  # 系統查無
]
OFF = [("IP", "處理人員", "申請日期", "關機日期", "實體機型或 VM", "主機序號"), ("10.99.1.4", "陳三", "", "2026/10/01", "VM", "")]


def make_xlsx(rows=ROWS, off=OFF, main_name="伺服器 (主)") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "工作表1"
    m = wb.create_sheet(main_name)
    m.append(EN)
    m.append(ZH)
    for r in rows:
        m.append(list(r))
    if off:
        o = wb.create_sheet("已關機待處理")
        for r in off:
            o.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _seed(c):
    db.insert_hardware(c, asset_serial="S1", hostname="app01", ip="10.99.1.1", os="Windows Server 2019", environment="正式",
                       owner="王一", physical_location="甲機房", asset_purpose="甲系統AP", asset_status="使用中")
    db.insert_hardware(c, asset_serial="S2", hostname="app02", ip="10.99.1.2", os="Windows Server 2019", environment="正式",
                       owner="王一", physical_location="丙機房", asset_purpose="乙系統AP", asset_status="使用中")
    db.insert_hardware(c, asset_serial="DYN-3", hostname="db03", ip="10.99.1.3", os="Red Hat Enterprise Linux 8")
    db.insert_hardware(c, asset_serial="DYN-4", hostname="t04", ip="10.99.1.4", os="Red Hat Enterprise Linux 8")
    c.commit()


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _seed(c)
    yield c
    c.close()


def _imp(c, data=None):
    data = data or make_xlsx()
    pv = ss.preview(c, data, "srv.xlsx")
    return ss.confirm(c, data, "srv.xlsx", pv["sha256"], "tester")


def test_讀檔_中文表頭_IP前導零_佔位當空白_標記時間序號轉日期():
    p = ss.parse(make_xlsx())
    assert p["main_sheet"] == "伺服器 (主)" and p["off_sheet"] == "已關機待處理"
    by = {r["row_no"]: r for r in p["rows"]}
    assert by[5]["ip"] == "10.99.1.3" and by[7]["ip"] == "" and by[7]["ip_raw"] == ""
    assert by[3]["marked_at"].startswith("2024-10-22") and p["data_date"] == "2024-10-22"
    assert by[4]["sheet_id"] == "2" and by[3]["host"] == "app01"
    assert [o["ip"] for o in p["off"]] == ["10.99.1.4"] and p["off"][0]["off_date"] == "2026/10/01"


def test_拒收_有密碼_少分頁_少欄位():
    with pytest.raises(ss.Rejected, match="有密碼"):
        ss.parse(bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 100)
    with pytest.raises(ss.Rejected, match="伺服器"):
        ss.parse(make_xlsx(main_name="別的"))
    wb = openpyxl.Workbook()
    wb.create_sheet("伺服器 (主)").append(["IP", "用途"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ss.Rejected, match="少了這些欄"):
        ss.parse(buf.getvalue())


def test_兩段式_sha不符拒收_整份取代(conn):
    data = make_xlsx()
    pv = ss.preview(conn, data, "a.xlsx")
    assert pv["rows"] == 6 and pv["with_ip"] == 5 and pv["no_ip"] == 1 and pv["off_rows"] == 1 and pv["current"] is None
    with pytest.raises(ss.Rejected):
        ss.confirm(conn, data, "a.xlsx", "x" * 64, "t")
    _imp(conn)
    _imp(conn, make_xlsx(rows=ROWS[:2]))
    assert conn.execute("SELECT COUNT(*) FROM srv_sheet_row").fetchone()[0] == 2
    assert len(ss.batches(conn)) == 2 and ss.last_batch(conn)["rows"] == 2
    assert conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 4            # 不改任何資產資料


def test_比對分類(conn):
    _imp(conn)
    a = ss.analyze(conn)
    by = {r["row_no"]: r for r in a["rows"]}
    assert by[3]["kinds"] == []                                                       # 一致
    d = {x["field"] for x in by[4]["diffs"]}
    assert d == {"host", "os", "owner", "room"}, by[4]["diffs"]
    assert "no_cia" in by[5]["kinds"] and "self" in by[5]["kinds"] and by[5]["in_system"]
    assert "POC" in by[5]["self_text"]
    assert by[6]["kinds"] == ["off"] and by[6]["off_date"] == "2026/10/01"           # 已關機不算漏登
    assert by[7]["kinds"] == ["no_ip"]
    assert "no_cia" in by[8]["kinds"] and not by[8]["in_system"] and by[8]["system_status"] == "系統查無此 IP"
    assert a["counts"] == {"no_cia": 2, "diff": 1, "self": 1, "off": 1, "no_ip": 1, "all": 6, "no_cia_in_system": 1}
    assert a["field_counts"]["os"] == 1 and a["field_counts"]["env"] == 0
    assert [r["row_no"] for r in ss.listing(conn, "diff", "owner")["rows"]] == [4]
    assert [r["row_no"] for r in ss.listing(conn, "all", q="POC")["rows"]] == [5]
    with pytest.raises(ValueError):
        ss.listing(conn, "xx")


def test_環境別與作業系統判斷():
    assert ss.env_of("PRD-ESXi") == "正式" and ss.env_of("UAT") == "測試" and ss.env_of("非正式") == "測試" and ss.env_of("F5 BIP") is None
    assert ss.os_family("Oracle Linux 8") == "Linux" and ss.os_family("ESXI") == "ESXi" and ss.os_family("N/A") is None
    assert ss.ips_of("010.099.001.011, 10.99.0.2／0.0.0.0") == ["10.99.1.11", "10.99.0.2"]


def test_verify_numbers_不變式(conn, monkeypatch):
    _imp(conn)
    fails = []
    monkeypatch.setattr(vn, "inv", lambda area, name, ok, detail="": fails.append((name, detail)) if not ok else None)
    vn.check_srv_sheet(conn)
    assert not fails, fails


@pytest.fixture()
def client(tmp_path):
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
    cl = TestClient(api.app)
    assert cl.post("/api/auth/login", json={"username": "tester", "password": _PW}).status_code == 200
    yield cl
    api.app.dependency_overrides.clear()


def test_API_預覽確認摘要清單(client):
    cl = client
    assert cl.get("/api/srv-sheet/summary").json()["batch"] is None
    data = make_xlsx()
    f = {"file": ("srv.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    assert cl.post("/api/srv-sheet/preview", files={"file": ("srv.xls", b"x", "application/octet-stream")}).status_code == 400
    pv = cl.post("/api/srv-sheet/preview", files=f).json()
    assert pv["rows"] == 6
    assert cl.post("/api/srv-sheet/confirm", files=f, data={"file_sha256": "bad"}).status_code == 400
    r = cl.post("/api/srv-sheet/confirm", files=f, data={"file_sha256": pv["sha256"]})
    assert r.status_code == 200, r.text
    s = cl.get("/api/srv-sheet/summary").json()
    assert s["counts"]["no_cia"] == 2 and len(s["history"]) == 1 and "rows" not in s
    rows = cl.get("/api/srv-sheet/rows", params={"kind": "no_cia"}).json()["rows"]
    assert len(rows) == s["counts"]["no_cia"]
    assert cl.get("/api/srv-sheet/rows", params={"kind": "zz"}).status_code == 400
