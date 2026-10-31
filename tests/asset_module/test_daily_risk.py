"""設備風險清單＋出事聯絡資訊（v1.499.0）：開機時間解析／門檻、硬體異常判讀、單點規則邊界、
合約檔解析與欄位對應、比對順序、三層解析優先序、稽核、0 筆資料、待補加總＝下鑽。"""
import datetime as dt
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import daily_risk as dr  # noqa: E402
import db  # noqa: E402

ADMIN = {"username": "admin"}
USER = {"username": "someone"}
NOW = dt.datetime(2026, 10, 23, 12, 0, 0)


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, sn, host, ip, **kw):
    db.insert_hardware(conn, asset_serial=sn, hostname=host, ip=ip, **kw)


def _spec(conn, sn, ip, spec, at="2026-10-20 12:00:00", platform="linux"):
    conn.execute("INSERT INTO host_spec (asset_serial, ip, platform, spec_json, collected_at) VALUES (?,?,?,?,?)",
                 (sn, ip, platform, json.dumps(spec, ensure_ascii=False), at))
    conn.commit()


# ---------------------------------------------------------------- 開機時間
@pytest.mark.parametrize("text,secs", [
    ("up 1 year, 23 weeks, 6 days, 10 hours, 16 minutes", 365 * 86400 + 23 * 7 * 86400 + 6 * 86400 + 10 * 3600 + 16 * 60),
    ("up 11 weeks, 2 days, 20 minutes", 11 * 7 * 86400 + 2 * 86400 + 20 * 60),
    ("up 3 minutes", 180),
    (" 10:30:01 up 120 days,  3:45,  2 users,  load average: 0.1, 0.2, 0.3", 120 * 86400 + 3 * 3600 + 45 * 60),
    ("10:30AM up 5 days, 12 mins, 1 user", 5 * 86400 + 12 * 60),
    ("12345.67 2345.60", 12345),
])
def test_開機時間各種寫法(text, secs):
    up = dr.parse_uptime(text, "2026-10-20 12:00:00")
    assert up["seconds"] == secs
    assert up["boot"] == dt.datetime(2026, 10, 20, 12) - dt.timedelta(seconds=secs)


def test_Windows_直接用開機時刻_認不出來回None():
    up = dr.parse_uptime("up 12 days, 3 hours（開機 2026-10-08 09:00:00）", "2026-10-20 12:00:00")
    assert up["boot"] == dt.datetime(2026, 10, 8, 9)
    assert dr.parse_uptime(None, "2026-10-20") is None
    assert dr.parse_uptime("看不懂", "2026-10-20 12:00:00") is None
    assert dr.parse_uptime("up 3 days", None) is None, "沒有收集時間就推不回開機時刻，不猜"


def test_未重開機門檻_剛好等於門檻算_差一天不算(tmp_path):
    c = _conn(tmp_path)
    _hw(c, "A1", "old1", "10.99.1.1")
    _hw(c, "A2", "new1", "10.99.1.2")
    _hw(c, "A3", "aix1", "10.99.1.3")
    # 收集時 2026-10-20 12:00，到 NOW（10-23 12:00）再 +3 天
    _spec(c, "A1", "10.99.1.1", {"uptime": "up 362 days"})        # 365 天 → 列
    _spec(c, "A2", "10.99.1.2", {"uptime": "up 361 days"})        # 364 天 → 不列
    _spec(c, "A3", "10.99.1.3", {"uptime": None}, platform="aix")  # 讀不到
    ms = dr.machines(c)
    u = dr.uptime_tab(c, ms, {}, NOW, 365)
    assert [r["serial"] for r in u["rows"]] == ["A1"] and u["rows"][0]["days"] == 365
    assert u["collected"] == 3 and u["with_uptime"] == 2 and u["unreadable_count"] == 1
    dr.save_settings(c, 364, None)
    assert dr.settings(c)["uptime_days"] == 364
    assert dr.uptime_tab(c, ms, {}, NOW, 364)["count"] == 2
    with pytest.raises(ValueError):
        dr.save_settings(c, 0, None)


def test_序號已不在資產庫_用IP對回同一台(tmp_path):
    c = _conn(tmp_path)
    _hw(c, "CIA-1", "h1", "10.99.2.1")
    _spec(c, "DYN-00:50:56:00:00:01", "10.99.2.1", {"uptime": "up 400 days"})
    u = dr.uptime_tab(c, dr.machines(c), {}, NOW, 365)
    assert u["count"] == 1 and u["rows"][0]["serial"] == "CIA-1"


def test_退役不列(tmp_path):
    c = _conn(tmp_path)
    _hw(c, "R1", "r1", "10.99.3.1", asset_status="報廢")
    _spec(c, "R1", "10.99.3.1", {"uptime": "up 900 days"})
    assert dr.uptime_tab(c, dr.machines(c), {}, NOW, 365)["count"] == 0


# ---------------------------------------------------------------- 硬體異常
def test_硬體異常只判讀收到的欄位():
    spec = {
        "disks": [{"disk": "sda", "model": "X", "health": "PASSED"}, {"disk": "sdb", "model": "Y", "health": "FAILED!"},
                  {"disk": "sdc", "model": "VM", "health": None}],
        "raid_sw_detail": [{"name": "md0", "level": "raid1", "status": "U_", "degraded": True, "members": []},
                           {"name": "md1", "level": "raid1", "status": "UU", "degraded": False, "members": []}],
        "raid_hw_detail": {"parsed": True, "virtual_drives": [{"dg": "0", "vd": "0", "raid_level": "RAID1", "state": "Dgrd"}],
                           "drives": [{"eid_slot": "0:1", "state": "Onln"}, {"eid_slot": "0:2", "state": "Failed"}]},
        "multipath_detail": [{"name": "mpatha", "path_count": 2,
                              "paths": [{"state": "active ready running"}, {"state": "failed faulty running"}]}],
    }
    f = dr.hw_findings(spec)
    assert [x["source"] for x in f] == ["SMART", "軟體 RAID", "硬體 RAID", "硬體 RAID", "多路徑"]
    assert dr.hw_findings({"raid_hw_detail": {"parsed": False, "note": "沒有輸出"}}) == []


def test_健康基準磁碟使用率_門檻可調(tmp_path):
    c = _conn(tmp_path)
    _hw(c, "D1", "d1", "10.99.4.1")
    _spec(c, "D1", "10.99.4.1", {"uptime": "up 1 days"})
    c.execute("INSERT INTO health_snapshot (ip, taken_at, disks_json) VALUES (?,?,?)",
              ("10.99.4.1", "2026-10-01 00:00:00", json.dumps({"/": 99})))
    c.execute("INSERT INTO health_snapshot (ip, taken_at, disks_json) VALUES (?,?,?)",
              ("10.99.4.1", "2026-10-18 00:00:00", json.dumps({"/": 50, "/data": 92})))
    c.commit()
    ms = dr.machines(c)
    h = dr.hardware_tab(c, ms, {}, 90)
    assert h["count"] == 1 and h["rows"][0]["detail"] == "/data 92%", "只看最新一份"
    assert dr.hardware_tab(c, ms, {}, 95)["count"] == 0


# ---------------------------------------------------------------- 單點規則
@pytest.mark.parametrize("rooms,expect", [
    ([], (None, True)),
    (["內湖"], (dr.SPOF_SINGLE, True)),
    (["未填"], (dr.SPOF_SINGLE, True)),
    (["內湖", "內湖"], (dr.SPOF_SAME_ROOM, True)),
    (["內湖", "板橋"], (None, True)),
    (["內湖", "未填"], (None, False)),
    (["未填", "未填"], (None, False)),
    (["內湖", "板橋", "未填"], (None, True)),
])
def test_單點規則邊界(rooms, expect):
    assert dr.spof_rule(rooms) == expect


def test_單點以台計_只看正式_退役不算(tmp_path):
    c = _conn(tmp_path)
    # S-1：同一台登記兩筆（同主機名同 IP）→ 算 1 台 → 單機
    _hw(c, "S1a", "app1", "10.99.5.1", api_id="N-901", environment="正式", physical_location="01_內湖機房")
    _hw(c, "S1b", "app1", "10.99.5.1", api_id="N-901", environment="正式", physical_location="內湖機房")
    # S-2：兩台同機房 → 同機房；測試機在別間不算
    _hw(c, "S2a", "b1", "10.99.5.2", api_id="N-902", environment="正式", physical_location="板橋機房")
    _hw(c, "S2b", "b2", "10.99.5.3", api_id="N-902", environment="正式", physical_location="板橋機房")
    _hw(c, "S2c", "b3", "10.99.5.4", api_id="N-902", environment="測試", physical_location="內湖機房")
    # S-3：兩間機房 → 不是單點；第三台退役不影響
    _hw(c, "S3a", "c1", "10.99.5.5", api_id="N-903", environment="正式", physical_location="板橋機房")
    _hw(c, "S3b", "c2", "10.99.5.6", api_id="N-903", environment="正式", physical_location="內湖機房")
    # S-4：一台機房未填 → 無法判定
    _hw(c, "S4a", "d1", "10.99.5.7", api_id="N-904", environment="正式", physical_location="內湖機房")
    _hw(c, "S4b", "d2", "10.99.5.8", api_id="N-904", environment="正式")
    # S-5：正式只剩一台（另一台退役）→ 單機
    _hw(c, "S5a", "e1", "10.99.5.9", api_id="N-905", environment="正式", physical_location="內湖機房")
    _hw(c, "S5b", "e2", "10.99.5.10", api_id="N-905", environment="正式", physical_location="板橋機房",
        asset_status="停用")
    s = dr.spof_tab(c, {})
    got = {r["api_id"]: (r["reason"], r["prod_count"]) for r in s["rows"]}
    assert got == {"N-901": ("單機", 1), "N-902": ("同機房", 2), "N-905": ("單機", 1)}
    assert [r["api_id"] for r in s["undetermined"]] == ["N-904"]
    assert s["ok_count"] == 1 and s["systems_with_prod"] == 5
    n902 = next(r for r in s["rows"] if r["api_id"] == "N-902")
    assert n902["dr_other_room"] == 0 and len(n902["machines"]) == 2


def test_單點不碰_ci_node_is_spof():
    src = (ROOT / "APP" / "asset-module" / "backend" / "daily_risk.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    assert "UPDATE systems" not in code and "UPDATE ci_node" not in code


# ---------------------------------------------------------------- 日期、檔案、欄位對應
@pytest.mark.parametrize("v,expect", [
    ("2026/10/23", "2026-10-23"), ("2026-1-5", "2026-01-05"), ("20261023", "2026-10-23"),
    ("115/10/23", "2026-10-23"), ("115年10月23日", "2026-10-23"), (46318, "2026-10-23"),
    (dt.datetime(2026, 10, 23, 8), "2026-10-23"), (dt.date(2026, 10, 23), "2026-10-23"),
    ("", None), ("永久", None), ("2026/13/40", None), (None, None),
])
def test_日期各種寫法(v, expect):
    assert dr.parse_date(v) == expect


def test_欄位建議_長的同義字先比():
    m = dr.suggest_mapping(["資產序號", "硬體序號", "設備機型", "主機名稱", "維護廠商", "合約編號",
                            "合約起日", "保固到期", "服務電話", "聯絡窗口", "備註"])
    assert m == {"asset_code": "資產序號", "serial": "硬體序號", "model": "設備機型", "hostname": "主機名稱",
                 "vendor": "維護廠商", "contract_no": "合約編號", "start_date": "合約起日",
                 "end_date": "保固到期", "phone": "服務電話", "contact": "聯絡窗口", "note": "備註"}
    # 英文表頭；vendor 裡的 "end" 不可以被當成迄日
    m2 = dr.suggest_mapping(["Serial Number", "Model", "Vendor", "End Date", "Phone"])
    assert m2 == {"serial": "Serial Number", "model": "Model", "vendor": "Vendor",
                  "end_date": "End Date", "phone": "Phone"}


def test_讀CSV_Big5_前面有標題列():
    text = "2026 年維護合約清冊\n序號,型號,迄日\nSN001,DELL R750,115/12/31\n\nSN002,DELL R760,2027-01-31\n"
    h, rows = dr.read_table("a.csv", text.encode("cp950"))
    assert h == ["序號", "型號", "迄日"] and len(rows) == 2
    recs = dr.apply_mapping(h, rows, dr.suggest_mapping(h))
    assert [r["end_date"] for r in recs] == ["2026-12-31", "2027-01-31"]


def test_讀xlsx_表頭重複加編號():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["序號", "電話", "電話"])
    ws.append(["SN9", "02-1", "02-2"])
    buf = io.BytesIO()
    wb.save(buf)
    h, rows = dr.read_table("x.xlsx", buf.getvalue())
    assert h == ["序號", "電話", "電話(2)"] and rows == [["SN9", "02-1", "02-2"]]
    with pytest.raises(ValueError):
        dr.read_table("x.pdf", b"x")


# ---------------------------------------------------------------- 比對順序
def _fleet(c):
    _hw(c, "A-1", "web01", "10.99.6.1", hw_serial="HW-AAA", device_model="DELL R750")
    _hw(c, "A-2", "web02.corp.example.com", "10.99.6.2", hw_serial="HW-BBB", device_model="DELL R750")
    _hw(c, "A-3", "db01", "10.99.6.3", hw_serial="HW-CCC", device_model="DELL R760")
    _hw(c, "A-4", "dup", "10.99.6.4", device_model="HPE DL380")
    _hw(c, "A-5", "dup", "10.99.6.5", device_model="HPE DL380")   # 同名不同 IP（舊口徑）＝兩台
    _hw(c, "V-1", "vm01", "10.99.6.9", device_model="(VM)", is_vm=1)


def test_比對順序_序號先_主機名再_型號最後(tmp_path):
    c = _conn(tmp_path)
    _fleet(c)
    idx = dr.match_index(dr.machines(c))
    # 序號對得到就用序號（就算主機名指向別台）
    st, by, keys = dr.match_row({"serial": "hw-aaa", "hostname": "db01", "model": "DELL R760"}, idx)
    assert (st, by) == ("matched", "serial") and len(keys) == 1
    # 資產編號（asset_serial）也算序號
    assert dr.match_row({"asset_code": "A-3"}, idx)[:2] == ("matched", "serial")
    # 序號對不到 → 主機名（去網域、不分大小寫）
    assert dr.match_row({"serial": "NOPE", "hostname": "WEB02"}, idx)[:2] == ("matched", "hostname")
    # 主機名對到兩台 → 多筆，不猜
    assert dr.match_row({"hostname": "dup"}, idx)[:2] == ("ambiguous", "hostname")
    # 只有型號 → 型號層級，一次套全部同型號
    st, by, keys = dr.match_row({"model": "dell  r750"}, idx)
    assert (st, by, len(keys)) == ("matched", "model", 2)
    # VM 不吃型號合約；都對不到
    assert dr.match_row({"model": "(VM)"}, idx)[0] == "unmatched"
    assert dr.match_row({"serial": "X", "hostname": "nobody"}, idx) == ("unmatched", None, set())


def _csv(rows):
    return ("\n".join(",".join(r) for r in rows) + "\n").encode("utf-8")


def test_匯入預覽_存檔取代上一份_留歷史與稽核(tmp_path):
    c = _conn(tmp_path)
    _fleet(c)
    data = _csv([["序號", "主機名", "型號", "廠商", "迄日", "維護電話"],
                 ["HW-AAA", "", "", "甲公司", "2026/11/30", "02-1111"],
                 ["", "dup", "", "乙公司", "2027/01/01", ""],
                 ["", "", "DELL R760", "丙公司", "2025/01/01", "02-3333"],
                 ["", "ghost", "", "丁公司", "2027/01/01", ""],
                 ["", "", "", "", "", ""]])
    p = dr.preview(c, "c.csv", data)
    assert p["mapping_from"] == "suggested" and not p["problems"]
    assert p["row_count"] == 4 and p["counts"] == {"matched": 2, "unmatched": 1, "ambiguous": 1}
    assert sum(p["counts"].values()) == p["row_count"]
    with pytest.raises(ValueError):
        dr.save_import(c, "c.csv", data, {"vendor": "廠商"}, "admin")   # 沒有可比對的欄位
    r = dr.save_import(c, "c.csv", data, p["mapping"], "admin")
    assert r["counts"]["matched"] == 2
    # 記住對應：下次同表頭直接用
    assert dr.preview(c, "c.csv", data)["mapping_from"] == "saved"
    cur = dr.contract_current(c)
    assert cur["current"]["row_count"] == 4 and len(cur["rows"]) == 4
    assert [x["match_status"] for x in cur["rows"]].count("unmatched") == 1, "對不上的列要留著看得到"
    # 再匯一次 → 取代，舊的留在歷史
    dr.save_import(c, "c2.csv", _csv([["序號", "迄日"], ["HW-BBB", "2026/12/01"]]), {"serial": "序號", "end_date": "迄日"}, "admin")
    cur = dr.contract_current(c)
    assert cur["current"]["filename"] == "c2.csv" and len(cur["history"]) == 2 and len(cur["rows"]) == 1
    assert c.execute("SELECT COUNT(*) FROM contact_audit WHERE kind='contract_import'").fetchone()[0] == 2


# ---------------------------------------------------------------- 三層解析、稽核、待補
def test_越具體越優先_標出來源(tmp_path):
    c = _conn(tmp_path)
    _fleet(c)
    c.execute("UPDATE hardware SET api_id='N-950' WHERE asset_serial IN ('A-1','A-2')")
    c.commit()
    dr.save_entry(c, "model", "dell r750", {"vendor": "型號廠商", "phone": "02-0000", "contract_end": "2027/1/1"}, "admin")
    dr.save_entry(c, "system", "N-950", {"vendor": "系統廠商"}, "admin")
    dr.save_entry(c, "asset", "A-1", {"phone": "02-9999"}, "admin")
    res = dr.resolve_all(c)
    ms = {m["serial"]: m["key"] for m in dr.machines(c)}
    a1 = res[ms["A-1"]]
    assert a1["phone"] == {"value": "02-9999", "source": "本機（手動）"}
    assert a1["vendor"] == {"value": "系統廠商", "source": "系統 N-950（手動）"}
    assert a1["contract_end"] == {"value": "2027-01-01", "source": "型號（手動）"}
    a3 = res[ms["A-3"]]
    assert a3["vendor"]["value"] is None, "沒登錄就是沒有，不猜"
    # 合約匯入（序號）排在系統手動之前、本機手動之後
    dr.save_import(c, "c.csv", _csv([["序號", "廠商", "維護電話"], ["HW-AAA", "合約廠商", "02-5555"]]),
                   {"serial": "序號", "vendor": "廠商", "phone": "維護電話"}, "admin")
    a1 = dr.resolve_all(c)[ms["A-1"]]
    assert a1["vendor"]["source"] == "合約匯入（序號）" and a1["phone"]["source"] == "本機（手動）"


def test_手動資料稽核_清空等於刪除_日期驗證(tmp_path):
    c = _conn(tmp_path)
    _fleet(c)
    dr.save_entry(c, "asset", "A-1", {"vendor": "甲"}, "admin")
    dr.save_entry(c, "asset", "A-1", {"vendor": "乙"}, "admin")
    assert dr.save_entry(c, "asset", "A-1", {}, "admin") is None
    log = dr.audit_log(c)
    assert [(x["kind"], json.loads(x["after_json"])["vendor"] if x["after_json"] else None) for x in log] == \
        [("contact", None), ("contact", "乙"), ("contact", "甲")]
    assert all(x["changed_by"] == "admin" and x["changed_at"] for x in log)
    with pytest.raises(ValueError):
        dr.save_entry(c, "asset", "NOT-EXIST", {"vendor": "x"}, "admin")
    with pytest.raises(ValueError):
        dr.save_entry(c, "asset", "A-1", {"contract_end": "明年"}, "admin")
    with pytest.raises(ValueError):
        dr.save_entry(c, "rack", "x", {"vendor": "x"}, "admin")
    # 型號層級：大小寫／空白不同視為同一筆（改而不是新增）
    dr.save_entry(c, "model", "DELL R750", {"vendor": "A"}, "admin")
    dr.save_entry(c, "model", "dell   r750", {"vendor": "B"}, "admin")
    assert c.execute("SELECT COUNT(*) FROM contact_info WHERE level='model'").fetchone()[0] == 1


def test_合約分頁_0筆資料不出錯_分級正確(tmp_path):
    c = _conn(tmp_path)
    _fleet(c)
    o = dr.risk_overview(c, NOW)
    k = o["contract"]
    assert k["has_data"] is False and k["total"] == 5, "VM 不算進保固範圍"
    assert k["counts"]["none"] == 5 and k["rows"] == []
    dr.save_entry(c, "asset", "A-1", {"contract_end": "2026-10-22"}, "admin")   # 昨天 → 已過期
    dr.save_entry(c, "asset", "A-2", {"contract_end": "2027-01-21"}, "admin")   # 90 天 → 90 天內
    dr.save_entry(c, "asset", "A-3", {"contract_end": "2027-01-22"}, "admin")   # 91 天 → 180 天內
    dr.save_entry(c, "asset", "A-4", {"contract_end": "2027-06-01"}, "admin")   # 180 天以上
    k = dr.risk_overview(c, NOW)["contract"]
    assert k["has_data"] and k["counts"] == {"expired": 1, "d90": 1, "d180": 1, "ok": 1, "none": 1}
    assert sum(k["counts"].values()) == k["total"]


def test_待補_依型號加總等於缺資料實體機_依系統每台每系統一次(tmp_path):
    c = _conn(tmp_path)
    _fleet(c)
    c.execute("UPDATE hardware SET api_id='N-960' WHERE asset_serial IN ('A-1','A-3','V-1')")
    c.commit()
    t = dr.todo(c)
    assert t["in_book_total"] == 6 and t["missing_count"] == 6 and t["physical_missing"] == 5
    assert sum(x["count"] for x in t["models"]) == t["physical_missing"]
    assert t["models"][0]["model"] in ("DELL R750", "HPE DL380") and t["models"][0]["count"] == 2
    assert {x["api_id"]: x["count"] for x in t["systems"]} == {"N-960": 3, "（未填 APID）": 3}
    assert all(x["count"] == len(x["machines"]) for x in t["systems"] + t["models"])
    dr.save_entry(c, "model", "DELL R750", {"phone": "02-1"}, "admin")
    t = dr.todo(c)
    assert t["missing_count"] == 4 and "DELL R750" not in [x["model"] for x in t["models"]]


# ---------------------------------------------------------------- 值班電話、聯絡卡、端點權限
def test_值班電話_稽核與聯絡卡(tmp_path):
    c = _conn(tmp_path)
    _fleet(c)
    c.execute("INSERT INTO business_system (api_id, name, ap_department, ap_owner) VALUES ('N-970','測試系統','資訊部','王小明')")
    c.execute("UPDATE hardware SET api_id='N-970', custodian='保管甲', user_name='承辦乙' WHERE asset_serial='A-1'")
    c.commit()
    dr.save_oncall(c, "N-970", "0900-000-000", None, "admin")
    card = dr.contact_card(c, asset_serial="A-1")
    assert card["people"]["owners"] == [{"api_id": "N-970", "name": "王小明", "department": "資訊部"}]
    assert card["people"]["custodians"] == ["保管甲"] and card["people"]["ap_users"] == ["承辦乙"]
    assert card["oncall"][0]["phone"] == "0900-000-000"
    assert card["maint"]["vendor"]["value"] is None
    sc = dr.contact_card(c, api_id="N-970")
    assert sc["scope"]["machines"] == 1 and sc["scope"]["machines_with_info"] == 0
    assert dr.save_oncall(c, "N-970", "", "", "admin") is None
    assert [x["kind"] for x in dr.audit_log(c)] == ["oncall", "oncall"]
    with pytest.raises(LookupError):
        dr.contact_card(c, asset_serial="NOPE")


def test_端點_非管理員不能改(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBIT3_DISABLE_AUTH", raising=False)
    c = _conn(tmp_path)
    _fleet(c)
    with pytest.raises(api.HTTPException) as e:
        _save(c, USER)
    assert e.value.status_code == 403
    r = _save(c, ADMIN)
    assert r["saved"]["vendor"] == "x"
    with pytest.raises(api.HTTPException) as e:
        api.risk_settings_endpoint(_risk_mod().RiskSettingsBody(uptime_days=10), session=USER, conn=c)
    assert e.value.status_code == 403
    out = api.risk_overview_endpoint(session=USER, conn=c)
    assert out["can_edit"] is False and set(out) >= {"uptime", "hardware", "spof", "contract"}


def _risk_mod():
    from routers import risk
    return risk


def _save(c, session):
    body = _risk_mod().ContactEntryBody(level="asset", key="A-1", vendor="x")
    return api.contacts_entry_save_endpoint(body, session=session, conn=c)
