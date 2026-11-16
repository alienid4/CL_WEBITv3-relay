"""憑證盤點 第二批（工作台化）：條件過濾、搜尋擴大、更換進度／預排日期、批次更新（兩段式／上限／復原／衝突／稽核）、
掛載型態可增減與匯入未登記提示、觀測作廢、停用原因、唯讀路徑不寫庫、舊庫升級。
測試資料一律虛構：192.0.2.x／198.51.100.x、example.test、虛構人名。
"""
import io
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import certs  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROLE_AWARE_TESTS = True
_PW = "s3cure-pass!"
UTC = timezone.utc
NOW = datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    yield c
    c.close()


def _dbp(conn):
    return conn.execute("PRAGMA database_list").fetchone()[2]


def mk(conn, name, expiry=None, **kw):
    body = {"name": name, "environment": "正式", "endpoint": kw.pop("endpoint", f"192.0.2.{abs(hash(name)) % 200 + 1}"),
            "mount_type": "F5", **kw}
    iid = certs.create_item(conn, body, "u", True)["id"]
    if expiry:
        certs.add_observation(conn, iid, {"not_after": expiry, **kw.get("obs", {})}, "u")
    return iid


@pytest.fixture()
def seeded(conn):
    ids = {}
    ids["A"] = mk(conn, "a.example.test", "2026-11-05 00:00:00+00:00", endpoint="198.51.100.10", req_unit="營運部", mount_type="F5",
                  f5_bigip="198.51.100.10", host_ips="192.0.2.21", announce_needed="是")
    ids["B"] = mk(conn, "b.example.test", "2026-11-30 15:59:59+00:00", endpoint="192.0.2.31", req_unit="資訊處", mount_type="IIS")
    ids["C"] = mk(conn, "c.example.test", "2026-11-30 16:00:00+00:00", endpoint="192.0.2.32", req_unit="營運部", mount_type="Nginx", environment="測試")
    ids["D"] = mk(conn, "d.example.test", "2026-10-31 00:00:00+00:00", endpoint="192.0.2.33", req_unit="資訊處", mount_type="APP", announce_needed="否")
    ids["E"] = mk(conn, "e.example.test", None, endpoint="192.0.2.34", req_unit="營運部", mount_type="F5")
    ids["F"] = mk(conn, "f.example.test", "2027-06-01 00:00:00+00:00", endpoint="192.0.2.35", mount_type="F5", environment="測試")
    certs.set_active(conn, ids["F"], False, "u", "retired")
    certs.add_observation(conn, ids["A"], {"not_after": "2026-11-05 00:00:00+00:00", "serial": "ab:cd:12", "subject_cn": "a.example.test",
                                            "issuer_dn": "CN=Test CA", "san": "alt.example.test,second.example.test"}, "u") if False else None
    return ids


def names(res):
    return [r["name"].split(".")[0] for r in res["items"]]


def q(conn, **f):
    return certs.query_items(conn, f, now=NOW, size=0)


# ------------------------------------------------------------------ 條件過濾
def test_預設只看啟用中_停用用active切換_total_matched不同(conn, seeded):
    r = q(conn)
    assert names(r) == ["a", "b", "c", "d", "e"] and r["total"] == 5 and r["matched"] == 5
    assert names(q(conn, active="off")) == ["f"] and q(conn, active="off")["total"] == 1
    assert len(q(conn, active="all")["items"]) == 6
    r = q(conn, env="正式")
    assert names(r) == ["a", "b", "d", "e"] and r["matched"] == 4 and r["total"] == 5     # total 是同範圍總數，matched 是符合數


def test_條件可組合(conn, seeded):
    assert names(q(conn, env="正式", unit="營運部")) == ["a", "e"]
    assert names(q(conn, env="正式", unit="營運部", mount="F5", has_expiry="1")) == ["a"]
    assert names(q(conn, mount="IIS", unit="營運部")) == []
    assert names(q(conn, announce="yes")) == ["a"] and names(q(conn, announce="no")) == ["d"]
    assert names(q(conn, announce="blank")) == ["b", "c", "e"]
    assert names(q(conn, has_expiry="0")) == ["e"]


def test_空白選項_unit空白(conn, seeded):
    certs.update_item(conn, seeded["E"], {"req_unit": ""}, "u", True)
    assert names(q(conn, unit=certs.BLANK)) == ["e"]


def test_風險等級與狀態分段(conn, seeded):
    assert names(q(conn, risk="expired")) == ["d"]
    assert names(q(conn, risk="high")) == ["a"]                       # 剩 4 天 ≤ 14
    assert names(q(conn, status="d30")) == ["b", "c"]
    assert names(q(conn, risk="none")) == ["e"]


def test_N天內到期_含邊界_已過期不算(conn, seeded):
    # a 剩 4 天整；N=4 含、N=3 不含；d 已過期永遠不在
    assert names(q(conn, within=4)) == ["a"]
    assert names(q(conn, within=3)) == []
    assert "d" not in names(q(conn, within=3650))
    with pytest.raises(ValueError):
        q(conn, within=0)
    with pytest.raises(ValueError):
        q(conn, within="abc")


def test_到期日區間_以UTC加8日期_起迄含當天_本月邊界(conn, seeded):
    # b：UTC 11-30 15:59:59 = UTC+8 11-30 23:59:59（本月）；c：UTC 11-30 16:00 = UTC+8 12-01 00:00（下個月）
    assert names(q(conn, due_from="2026-11-01", due_to="2026-11-30")) == ["a", "b"]
    assert names(q(conn, due_from="2026-12-01", due_to="2026-12-31")) == ["c"]
    assert names(q(conn, due_from="2026-11-30", due_to="2026-11-30")) == ["b"]
    assert names(q(conn, due_from="2026-12-01")) == ["c"]                       # 只有起日
    assert names(q(conn, due_to="2026-10-31")) == ["d"]                         # 只有迄日
    assert "e" not in names(q(conn, due_from="2000-01-01"))                     # 沒有到期日的永遠不在區間內
    with pytest.raises(ValueError):
        q(conn, due_from="2026-12-01", due_to="2026-11-01")
    with pytest.raises(ValueError):
        q(conn, due_from="2026-13-01")


def test_搜尋擴大_F5_BIGIP_主機IP_SAN_序號_作廢的序號不算(conn, seeded):
    a = seeded["A"]
    r = certs.add_observation(conn, a, {"not_after": "2026-11-06 00:00:00+00:00", "serial": "ab:cd:12", "subject_cn": "cn-a.example.test",
                                        "issuer_dn": "CN=Test CA", "san": "alt.example.test,second.example.test"}, "u")
    assert names(q(conn, q="198.51.100.10")) == ["a"]            # F5 BIG-IP / 端點
    assert names(q(conn, q="192.0.2.21")) == ["a"]               # 主機 IP
    assert names(q(conn, q="second.example")) == ["a"]           # SAN
    assert names(q(conn, q="ABCD12")) == ["a"]                   # 序號（登錄時去冒號、轉大寫）
    assert names(q(conn, q="ab:cd:12")) == ["a"]                 # 從憑證視窗複製的帶冒號寫法也要找得到
    assert names(q(conn, q="營運部")) == ["a", "c", "e"]
    certs.void_observation(conn, a, r["id"], "登錯了", "u")
    assert names(q(conn, q="ABCD12")) == []                      # 作廢觀測的序號不再被搜到
    assert certs.SEARCH_FIELDS == ("憑證名稱", "端點", "SNI", "申請單位", "F5 BIG-IP", "主機 IP", "SAN", "序號")


def test_排序_沒有值的永遠排最後_分頁(conn, seeded):
    asc = certs.query_items(conn, {}, sort="not_after_utc", desc=False, now=NOW, size=0)
    desc = certs.query_items(conn, {}, sort="not_after_utc", desc=True, now=NOW, size=0)
    assert names(asc) == ["d", "a", "b", "c", "e"] and names(desc) == ["c", "b", "a", "d", "e"]     # e 沒到期日，升降都在最後
    p2 = certs.query_items(conn, {}, sort="seq", page=2, size=2, now=NOW)
    assert names(p2) == ["c", "d"] and p2["matched"] == 5
    with pytest.raises(ValueError):
        certs.query_items(conn, {}, sort="evil")


def test_list_items舊介面不變_卡片下鑽同一份(conn, seeded):
    s = certs.summary(conn, NOW)
    for c in s["cards"]:
        assert len(certs.list_items(conn, status=c["key"], now=NOW)) == c["count"]
    assert sum(c["count"] for c in s["cards"]) == s["total_active"] == len(certs.list_items(conn, now=NOW))


# ------------------------------------------------------------------ 更換進度／預排日期
def test_更換進度只收四個值_預排日期驗證(conn, seeded):
    a = seeded["A"]
    for v in ("待排程", "已更換", "已驗測", ""):
        certs.update_item(conn, a, {"replace_status": v}, "u", True)
    with pytest.raises(ValueError):
        certs.update_item(conn, a, {"replace_status": "亂寫"}, "u", True)
    certs.schedule_item(conn, a, "2026-12-05", "u", True)                      # 排定更換：預排日期＋已排程一起設
    assert certs.item_detail(conn, a, True)["planned_date"] == "2026-12-05" and certs.item_detail(conn, a, True)["replace_status"] == "已排程"
    for bad in ("2026-02-30", "12/5", "1999-01-01", "abc"):
        with pytest.raises(ValueError):
            certs.update_item(conn, a, {"planned_date": bad}, "u", True)
    certs.update_item(conn, a, {"planned_date": "", "replace_status": "待排程"}, "u", True)              # 清空（同時退回待排程，兩欄一致）
    assert certs.item_detail(conn, a, True)["planned_date"] == ""


def test_預排日期可排序篩選_文字能解析時當有效預排日期(conn, seeded):
    certs.schedule_item(conn, seeded["A"], "2026-12-05", "u", True)
    certs.update_item(conn, seeded["B"], {"planned_text": "2026-11-20 22:00 夜間"}, "u", True)      # 文字能解析 → 有效預排日期 11-20
    certs.update_item(conn, seeded["C"], {"planned_text": "下週末"}, "u", True)                     # 解析不了 → 沒有預排日期，文字照存
    assert names(q(conn, has_plan="1")) == ["a", "b"] and names(q(conn, has_plan="0")) == ["c", "d", "e"]
    assert names(q(conn, plan_from="2026-12-01", plan_to="2026-12-31")) == ["a"]
    assert names(q(conn, plan_to="2026-11-30")) == ["b"]
    r = certs.query_items(conn, {}, sort="planned_date", now=NOW, size=0)
    assert names(r)[:2] == ["b", "a"]
    assert [x for x in r["items"] if x["name"].startswith("c")][0]["planned_text"] == "下週末"


# ------------------------------------------------------------------ 批次更新
def _prev(conn, ids, changes, admin=True, flt=None):
    return certs.batch_preview(conn, ids, flt, changes, admin, NOW)


def test_批次預覽不寫庫_回影響筆數與前後差異(conn, seeded):
    ids = [seeded["A"], seeded["B"], seeded["E"]]
    certs.update_item(conn, seeded["A"], {"replace_status": "待排程"}, "u", True)
    before = conn.execute("SELECT id, replace_status, updated_at, manual_fields FROM cert_item ORDER BY id").fetchall()
    pv = _prev(conn, ids, {"replace_status": "已驗測", "planned_date": "2026-12-05"})
    assert pv["selected"] == 3 and pv["will_change"] == 3 and pv["unchanged"] == 0 and pv["digest"]
    fs = {f["field"]: f for f in pv["fields"]}
    assert {x["value"]: x["count"] for x in fs["replace_status"]["from"]} == {"待排程": 1, "（空）": 2}
    assert pv["sample"][0]["changes"][0]["old"] and pv["sample"][0]["name"]
    assert conn.execute("SELECT id, replace_status, updated_at, manual_fields FROM cert_item ORDER BY id").fetchall() == before
    assert conn.execute("SELECT count(*) FROM cert_batch").fetchone()[0] == 0
    # 已經是目標值的不算變更
    pv2 = _prev(conn, [seeded["A"]], {"replace_status": "待排程"})
    assert pv2["will_change"] == 0 and pv2["unchanged"] == 1


def test_批次套用_單一交易_留逐項前後值_並可整批復原(conn, seeded):
    ids = [seeded["A"], seeded["B"]]
    certs.update_item(conn, seeded["A"], {"replace_status": "待排程"}, "u", True)
    pv = _prev(conn, ids, {"replace_status": "已驗測", "announce_needed": "是"})
    out = certs.batch_apply(conn, pv["ids"], {"replace_status": "已驗測", "announce_needed": "是"}, "alice", True, pv["digest"])
    assert out["changed"] == 2 and out["unchanged"] == 0
    d = certs.item_detail(conn, seeded["A"], True)
    assert d["replace_status"] == "已驗測" and d["announce_needed"] == "是" and "replace_status" in d["manual_fields"]
    b = certs.list_batches(conn, True)[0]
    assert b["item_count"] == 2 and b["state"] == "active" and b["created_by"] == "alice" and "更換進度→已驗測" in b["summary"]
    assert "created_by" not in certs.list_batches(conn, False)[0]
    res = certs.batch_undo(conn, out["batch_id"], "alice")
    assert res["restored"] == 2 and res["conflicts"] == []
    assert certs.item_detail(conn, seeded["A"], True)["replace_status"] == "待排程"
    assert certs.item_detail(conn, seeded["B"], True)["replace_status"] == ""
    assert certs.item_detail(conn, seeded["B"], True)["manual_fields"] == [k for k in json.loads(
        conn.execute("SELECT manual_fields FROM cert_item WHERE id=?", (seeded["B"],)).fetchone()[0])]
    assert certs.list_batches(conn, True)[0]["state"] == "undone"
    with pytest.raises(ValueError):
        certs.batch_undo(conn, out["batch_id"], "alice")                # 已全部復原不能再來一次


def test_批次套用中途失敗_整批回滾_不留一半(conn, seeded):
    ids = [seeded["A"], seeded["B"]]
    conn.execute("UPDATE cert_item SET name='boom-item' WHERE id=?", (seeded["B"],))
    conn.execute("CREATE TRIGGER boom BEFORE UPDATE OF replace_status ON cert_item WHEN NEW.name='boom-item' BEGIN SELECT RAISE(ABORT,'boom'); END")
    conn.commit()
    pv = _prev(conn, ids, {"replace_status": "已驗測"})
    with pytest.raises(sqlite3.DatabaseError):
        certs.batch_apply(conn, pv["ids"], {"replace_status": "已驗測"}, "u", True, pv["digest"])
    assert conn.execute("SELECT count(*) FROM cert_item WHERE replace_status='已驗測'").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM cert_batch").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM cert_batch_item").fetchone()[0] == 0


def test_確認時資料已變動_digest對不上就拒絕(conn, seeded):
    ids = [seeded["A"], seeded["B"]]
    pv = _prev(conn, ids, {"replace_status": "已驗測"})
    certs.update_item(conn, seeded["B"], {"replace_status": "已更換"}, "someone", True)       # 預覽後被別人改了
    with pytest.raises(certs.PlanChanged):
        certs.batch_apply(conn, pv["ids"], {"replace_status": "已驗測"}, "u", True, pv["digest"])
    with pytest.raises(certs.PlanChanged):                                                   # 預覽的是別的改法
        pv2 = _prev(conn, ids, {"replace_status": "已驗測"})
        certs.batch_apply(conn, pv2["ids"], {"replace_status": "已更換"}, "u", True, pv2["digest"])
    assert certs.item_detail(conn, seeded["A"], True)["replace_status"] == ""


def test_批次上限500_超過就拒絕_剛好500可以(conn):
    rows = [(f"id{i}", i + 1, f"n{i}", "正式", f"192.0.2.{i % 250}", 8000 + i, "", "F5", "2026-01-01T00:00:00Z") for i in range(501)]
    conn.executemany("INSERT INTO cert_item (id, seq, name, environment, endpoint, port, sni, mount_type, created_at) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    with pytest.raises(ValueError, match="500"):
        _prev(conn, None, {"replace_status": "已驗測"}, flt={})                    # 篩選結果 501 筆
    with pytest.raises(ValueError, match="500"):
        _prev(conn, [f"id{i}" for i in range(501)], {"replace_status": "已驗測"})
    pv = _prev(conn, [f"id{i}" for i in range(500)], {"replace_status": "已驗測"})
    assert pv["will_change"] == 500
    pv = _prev(conn, None, {"replace_status": "已驗測"}, flt={"q": "n1"})      # 全選目前篩選結果（後端用同一份篩選算清單）
    assert pv["selected"] == len([i for i in range(501) if "n1" in f"n{i}"])


def test_批次只能改更換相關欄位_身分鍵與其他欄位一律拒絕(conn, seeded):
    for bad in ("environment", "endpoint", "port", "sni", "mount_type", "name", "req_unit", "note", "active"):
        with pytest.raises(ValueError, match="批次不能修改"):
            _prev(conn, [seeded["A"]], {bad: "x"})
    with pytest.raises(ValueError):
        _prev(conn, [seeded["A"]], {})
    with pytest.raises(ValueError):
        _prev(conn, [seeded["A"]], {"replace_status": "亂寫"})
    with pytest.raises(ValueError):
        _prev(conn, [seeded["A"]], {"announce_needed": "也許"})
    with pytest.raises(ValueError):
        _prev(conn, [seeded["A"]], {"planned_date": "2026-02-30"})


def test_批次_停用項目不改_並回報略過數(conn, seeded):
    pv = _prev(conn, [seeded["A"], seeded["F"]], {"replace_status": "待排程"})
    assert pv["selected"] == 2 and pv["will_change"] == 1 and pv["inactive_skipped"] == 1
    certs.batch_apply(conn, pv["ids"], {"replace_status": "待排程"}, "u", True, pv["digest"])
    assert certs.item_detail(conn, seeded["F"], True)["replace_status"] == ""


def test_批次復原_自批次後被改過就列衝突_不覆蓋_其餘照還原_可再補跑(conn, seeded):
    ids = [seeded["A"], seeded["B"], seeded["E"]]
    pv = _prev(conn, ids, {"replace_status": "已驗測"})
    out = certs.batch_apply(conn, pv["ids"], {"replace_status": "已驗測"}, "u", True, pv["digest"])
    certs.update_item(conn, seeded["B"], {"replace_status": "已更換"}, "other", True)          # 批次後有人改了同欄位
    res = certs.batch_undo(conn, out["batch_id"], "u")
    assert res["restored"] == 2 and [c["id"] for c in res["conflicts"]] == [seeded["B"]]
    assert certs.item_detail(conn, seeded["B"], True)["replace_status"] == "已更換"             # 沒被覆蓋
    assert certs.item_detail(conn, seeded["A"], True)["replace_status"] == ""
    assert certs.list_batches(conn, True)[0]["state"] == "partial"
    # 衝突者再度被改回批次值之前仍是衝突；人工處理後可再補跑
    again = certs.batch_undo(conn, out["batch_id"], "u")
    assert again["restored"] == 0 and len(again["conflicts"]) == 1


def test_批次復原_改了別的欄位但更新時間也變_同樣視為衝突(conn, seeded):
    pv = _prev(conn, [seeded["A"]], {"replace_status": "已驗測"})
    out = certs.batch_apply(conn, pv["ids"], {"replace_status": "已驗測"}, "u", True, pv["digest"])
    conn.execute("UPDATE cert_item SET rev=rev+1 WHERE id=?", (seeded["A"],))
    conn.commit()
    assert len(certs.batch_undo(conn, out["batch_id"], "u")["conflicts"]) == 1


def test_批次_執行人是人名欄_只有管理員_且批次紀錄與預覽不顯示人名(conn, seeded):
    with pytest.raises(certs.Forbidden):
        _prev(conn, [seeded["A"]], {"infra_owner": "陳阿德"}, admin=False)
    pv = _prev(conn, [seeded["A"]], {"infra_owner": "陳阿德"}, admin=True)
    assert "陳" not in json.dumps(pv, ensure_ascii=False)
    out = certs.batch_apply(conn, pv["ids"], {"infra_owner": "陳阿德"}, "u", True, pv["digest"])
    assert certs.item_detail(conn, seeded["A"], True)["infra_owner"] == "陳阿德"
    assert "陳" not in json.dumps(certs.list_batches(conn, True), ensure_ascii=False)
    certs.batch_undo(conn, out["batch_id"], "u", True)
    assert certs.item_detail(conn, seeded["A"], True)["infra_owner"] == ""


def test_批次表_讀取路徑不建表_唯讀連線可列(conn, seeded):
    conn.execute("DROP TABLE cert_batch_item")
    conn.execute("DROP TABLE cert_batch")
    conn.commit()
    ro = sqlite3.connect(f"file:{_dbp(conn)}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    ro.execute("PRAGMA query_only=ON")
    assert certs.list_batches(ro, True) == []
    assert certs.options(ro)["batch_max"] == 500
    ro.close()
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'cert_batch%'").fetchone()[0] == 0


# ------------------------------------------------------------------ 掛載型態
def test_型態預設五個_管理員可新增停用_使用中的不可刪(conn, seeded):
    assert [m["name"] for m in certs.mount_types(conn)] == ["F5", "IIS", "Nginx", "APP", "其他"]
    certs.add_mount_type(conn, "Tomcat", "u")
    with pytest.raises(certs.KeyConflict):
        certs.add_mount_type(conn, "tomcat", "u")
    mk(conn, "t.example.test", None, endpoint="192.0.2.99", mount_type="Tomcat")
    assert {m["name"]: m["in_use"] for m in certs.mount_types(conn)}["Tomcat"] == 1
    with pytest.raises(ValueError, match="使用中"):
        certs.remove_mount_type(conn, "Tomcat", "u")
    with pytest.raises(ValueError):
        certs.remove_mount_type(conn, "F5", "u")                    # 內建不可刪
    with pytest.raises(ValueError):
        certs.set_mount_active(conn, "其他", False, "u")            # 保底型態不可停用
    certs.add_mount_type(conn, "Apache", "u")
    certs.remove_mount_type(conn, "Apache", "u")                    # 沒人用的自訂型態可刪
    assert "Apache" not in [m["name"] for m in certs.mount_types(conn)]


def test_手動新增編輯_未登記或已停用的型態會被擋_不默默歸其他(conn, seeded):
    with pytest.raises(ValueError, match="尚未登記"):
        certs.create_item(conn, {"name": "x.example.test", "environment": "正式", "endpoint": "192.0.2.77", "mount_type": "Tomcat"}, "u", True)
    certs.add_mount_type(conn, "Tomcat", "u")
    iid = certs.create_item(conn, {"name": "x.example.test", "environment": "正式", "endpoint": "192.0.2.77", "mount_type": "tomcat"}, "u", True)["id"]
    assert certs.item_detail(conn, iid, True)["mount_type"] == "Tomcat"                  # 不分大小寫對到已登記的
    certs.set_mount_active(conn, "Tomcat", False, "u")
    with pytest.raises(ValueError, match="已停用"):
        certs.create_item(conn, {"name": "y.example.test", "environment": "正式", "endpoint": "192.0.2.78", "mount_type": "Tomcat"}, "u", True)
    certs.update_item(conn, iid, {"mount_type": "Tomcat", "req_unit": "資訊處"}, "u", True)   # 沒改型態：舊值放行
    with pytest.raises(ValueError, match="已停用"):
        certs.update_item(conn, seeded["A"], {"mount_type": "Tomcat"}, "u", True)


def _imp(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["憑證名稱", "F5 BIG IP", "主機 IP", "F5是否掛憑證", "OS/站台類型", "正/測", "申請單位"])
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_匯入遇到未登記型態_預覽明講_不默默歸其他_要人決定(conn):
    data = _imp([["t1.example.test", "", "192.0.2.41", "否", "Tomcat", "正式", "營運部"],
                 ["t2.example.test", "", "192.0.2.42", "否", "Tomcat", "正式", "營運部"],
                 ["i1.example.test", "", "192.0.2.43", "否", "IIS", "正式", "營運部"]])
    plan = certs.import_plan(conn, "a.xlsx", data)
    assert plan["unknown_mounts"] == [{"name": "Tomcat", "rows": 2}]
    assert [r["unknown_mount"] for r in plan["rows"]] == ["Tomcat", "Tomcat", ""]
    assert "尚未登記" in plan["rows"][0]["reason"]
    # 沒做決定：這兩列不寫入、算未決；IIS 那列照寫
    out = certs.import_apply(conn, "a.xlsx", data, "u", {}, plan["file_sha256"], plan["plan_digest"], {})
    assert out["created"] == 1 and out["unresolved"] == 2
    assert [m["name"] for m in certs.mount_types(conn)].count("Tomcat") == 0


def test_匯入_型態決定_新增型態_歸其他_略過(conn):
    data = _imp([["t1.example.test", "", "192.0.2.41", "否", "Tomcat", "正式", "營運部"],
                 ["w1.example.test", "", "192.0.2.42", "否", "Weblogic", "正式", "營運部"],
                 ["g1.example.test", "", "192.0.2.43", "否", "Glassfish", "正式", "營運部"]])
    plan = certs.import_plan(conn, "a.xlsx", data)
    out = certs.import_apply(conn, "a.xlsx", data, "u", {}, plan["file_sha256"], plan["plan_digest"],
                             {"Tomcat": "add", "Weblogic": "other", "Glassfish": "skip"})
    assert out["created"] == 2 and out["skipped"] == 1 and out["mounts_added"] == ["Tomcat"]
    got = {r["name"]: r["mount_type"] for r in certs.all_rows(conn)}
    assert got == {"t1.example.test": "Tomcat", "w1.example.test": "其他"}
    assert "Tomcat" in [m["name"] for m in certs.mount_types(conn)]
    with pytest.raises(ValueError):
        certs.import_apply(conn, "a.xlsx", data, "u", {}, None, None, {"Tomcat": "亂寫"})


def test_匯入完成記錄最後匯入時間與檔名(conn):
    assert certs.import_info(conn)["last_import_at"] is None
    data = _imp([["i1.example.test", "", "192.0.2.43", "否", "IIS", "正式", "營運部"]])
    certs.import_apply(conn, "survey.xlsx", data, "u", {})
    info = certs.import_info(conn)
    assert info["last_import_file"] == "survey.xlsx" and info["last_import_at"] and info["import_items"] == 1 and info["manual_items"] == 0


# ------------------------------------------------------------------ 觀測作廢、停用原因
def test_觀測作廢_不計入到期判斷_保留紀錄_需原因_可還原(conn, seeded):
    a = seeded["A"]
    old = certs.item_detail(conn, a, True)["observations"][0]["id"]
    new = certs.add_observation(conn, a, {"not_after": "2027-12-01 00:00:00+00:00"}, "u")["id"]
    assert next(r for r in certs.all_rows(conn, NOW) if r["id"] == a)["status"] == "ok"
    with pytest.raises(ValueError, match="原因"):
        certs.void_observation(conn, a, new, "  ", "u")
    certs.void_observation(conn, a, new, "日期登錯", "u")
    r = next(r for r in certs.all_rows(conn, NOW) if r["id"] == a)
    assert r["status"] == "d7" and r["not_after_utc"].startswith("2026-11-05")        # 回到前一筆
    d = certs.item_detail(conn, a, True)
    assert len(d["observations"]) == 2 and d["observations"][0]["voided"] and d["observations"][0]["status_label"] == "已作廢"
    assert d["observations"][0]["void_reason"] == "日期登錯"
    with pytest.raises(ValueError):
        certs.void_observation(conn, a, new, "again", "u")
    certs.void_observation(conn, a, old, "也作廢", "u")                               # 全作廢＝無到期資料
    assert next(r for r in certs.all_rows(conn, NOW) if r["id"] == a)["status"] == "none"
    certs.restore_observation(conn, a, new, "u")
    assert next(r for r in certs.all_rows(conn, NOW) if r["id"] == a)["status"] == "ok"
    with pytest.raises(ValueError):
        certs.restore_observation(conn, a, new, "u")
    with pytest.raises(LookupError):
        certs.void_observation(conn, seeded["B"], new, "x", "u")                     # 觀測不屬於這個項目


def test_停用要原因_記停用日期_重新啟用清掉(conn, seeded):
    a = seeded["A"]
    with pytest.raises(ValueError, match="停用原因"):
        certs.set_active(conn, a, False, "u")
    with pytest.raises(ValueError):
        certs.set_active(conn, a, False, "u", "亂寫")
    certs.set_active(conn, a, False, "u", "duplicate", "跟另一筆重複")
    d = certs.item_detail(conn, a, True)
    assert d["active"] is False and d["deactivate_reason"] == "duplicate" and d["deactivate_reason_label"] == "重複登錄"
    assert d["deactivate_note"] == "跟另一筆重複" and d["deactivated_at"]
    r = next(r for r in certs.all_rows(conn, NOW) if r["id"] == a)
    assert r["deactivate_reason_label"] == "重複登錄"
    certs.set_active(conn, a, True, "u")
    d = certs.item_detail(conn, a, True)
    assert d["active"] and not d["deactivate_reason"] and not d["deactivated_at"]


def test_來源與資料最後更新欄(conn, seeded):
    r = next(r for r in certs.all_rows(conn, NOW) if r["id"] == seeded["A"])
    assert r["source_label"] == "手動" and r["data_updated"] and r["data_updated_local"].endswith("(UTC+8)")


# ------------------------------------------------------------------ 唯讀與舊庫
def test_全部新讀取函式在唯讀連線可用_檔案位元組不變(conn, seeded):
    import hashlib
    path = _dbp(conn)
    h = lambda: hashlib.sha256(Path(path).read_bytes()).hexdigest()    # noqa: E731
    conn.commit()
    conn.close()
    before = h()
    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    ro.execute("PRAGMA query_only=ON")
    certs.query_items(ro, {"q": "198", "env": "正式", "due_from": "2026-11-01", "has_plan": "0"}, sort="remain_hours")
    certs.options(ro)
    certs.mount_types(ro)
    certs.import_info(ro)
    certs.list_batches(ro, True)
    certs.summary(ro)
    certs.item_detail(ro, seeded["A"], True)
    certs.export_rows(ro, True)
    ro.close()
    assert h() == before


def test_舊庫_第一批的表沒有新欄位_讀取不壞_ensure_tables補欄(tmp_path):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("""CREATE TABLE cert_item (id TEXT PRIMARY KEY, seq INTEGER NOT NULL, name TEXT NOT NULL, environment TEXT NOT NULL DEFAULT '',
        endpoint TEXT NOT NULL DEFAULT '', port INTEGER NOT NULL DEFAULT 443, sni TEXT NOT NULL DEFAULT '', mount_type TEXT NOT NULL DEFAULT '',
        host_ips TEXT, f5_bigip TEXT, f5_mounted INTEGER, os_type TEXT, req_unit TEXT, req_contact TEXT, ap_contact TEXT, infra_owner TEXT,
        ap_tester TEXT, planned_text TEXT, planned_at_utc TEXT, announce_needed TEXT, replace_method TEXT NOT NULL DEFAULT 'manual',
        replace_status TEXT NOT NULL DEFAULT '', note TEXT, active INTEGER NOT NULL DEFAULT 1, manual_fields TEXT NOT NULL DEFAULT '[]',
        source TEXT NOT NULL DEFAULT 'manual', created_by TEXT, created_at TEXT NOT NULL, updated_by TEXT, updated_at TEXT)""")
    c.execute("""CREATE TABLE cert_observation (id TEXT PRIMARY KEY, item_id TEXT NOT NULL, issuer_dn TEXT NOT NULL DEFAULT '', serial TEXT NOT NULL DEFAULT '',
        fingerprint_sha256 TEXT NOT NULL DEFAULT '', subject_cn TEXT, san_json TEXT, not_before_utc TEXT, not_after_utc TEXT NOT NULL,
        source TEXT NOT NULL, observed_at_utc TEXT NOT NULL, note TEXT, created_by TEXT)""")
    c.execute("INSERT INTO cert_item (id, seq, name, created_at) VALUES ('i1', 1, 'old.example.test', '2026-10-01T00:00:00Z')")
    c.execute("INSERT INTO cert_observation (id, item_id, not_after_utc, source, observed_at_utc) VALUES ('o1','i1','2026-11-05T00:00:00Z','manual','2026-10-01T00:00:00Z')")
    c.commit()
    r = certs.query_items(c, {}, now=NOW)                         # 升級前的庫：讀取降級但可用（verify_numbers 會先跑在新程式碼、舊結構上）
    assert r["matched"] == 1 and r["items"][0]["status"] == "d7" and r["items"][0]["planned_date"] == ""
    assert certs.item_detail(c, "i1", True)["observations"][0]["voided"] is False
    certs.ensure_tables(c)
    assert certs._has_col(c, "cert_observation", "voided") and certs._has_col(c, "cert_item", "planned_date")
    certs.ensure_tables(c)                                          # 冪等
    certs.void_observation(c, "i1", "o1", "舊資料作廢測試", "u")
    assert certs.query_items(c, {}, now=NOW)["items"][0]["status"] == "none"
    c.close()


# ------------------------------------------------------------------ API
@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    p = tmp_path / "t.db"
    db.init_db(p)

    def _ov():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _ov
    c = db.get_connection(p)
    for n, role in (("admin", "admin"), ("alice", "user"), ("vera", "viewer")):
        db.create_user(c, n, auth.hash_password(_PW), role=role)
    c.close()

    class E:
        path = p

        def client(self, name):
            t = TestClient(api.app)
            assert t.post("/api/auth/login", json={"username": name, "password": _PW}).status_code == 200
            return t

        def conn(self):
            return db.get_connection(p)

    yield E()
    api.app.dependency_overrides.pop(api.get_db, None)


def _mk_api(env, n=3):
    a = env.client("admin")
    ids = []
    for i in range(n):
        r = a.post("/api/certs/items", json={"name": f"s{i}.example.test", "environment": "正式", "endpoint": f"192.0.2.{10 + i}", "mount_type": "F5",
                                              "req_unit": "營運部", "infra_owner": "陳阿德"})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    return ids


def test_API_清單篩選排序分頁_網址參數與錯誤值回400(env):
    ids = _mk_api(env, 3)
    u = env.client("alice")
    r = u.get("/api/certs/items", params={"env": "正式", "sort": "name", "desc": "true", "size": 2, "page": 1}).json()
    assert [x["name"] for x in r["items"]] == ["s2.example.test", "s1.example.test"] and r["matched"] == 3 and r["total"] == 3
    assert u.get("/api/certs/items", params={"due_from": "bad"}).status_code == 400
    assert u.get("/api/certs/items", params={"sort": "evil"}).status_code == 400
    assert u.get("/api/certs/items", params={"within": "x"}).status_code == 400
    assert u.get("/api/certs/items", params={"rs": "亂寫"}).status_code == 400
    assert u.get("/api/certs/items", params={"active": "maybe"}).status_code == 400
    assert u.get("/api/certs/items", params={"include_inactive": "true"}).json()["matched"] == 3          # 舊參數仍可用
    assert "陳" not in u.get("/api/certs/items").text                                                       # 清單仍不含人名
    assert ids


def test_API_options_給下拉用_檢視者也可讀(env):
    _mk_api(env, 1)
    o = env.client("vera").get("/api/certs/options").json()
    assert [m["name"] for m in o["mount_types"]][:2] == ["F5", "IIS"] and o["units"] == ["營運部"]
    assert o["replace_statuses"] == ["待排程", "已排程", "已更換", "已驗測"] and o["search_fields"][-1] == "序號"


def test_API_批次權限與流程_檢視者不可_一般帳號可_執行人只有管理員(env):
    ids = _mk_api(env, 3)
    viewer, user, admin = env.client("vera"), env.client("alice"), env.client("admin")
    body = {"ids": ids, "changes": {"replace_status": "已驗測"}}
    assert viewer.post("/api/certs/batch/preview", json=body).status_code == 403
    assert viewer.get("/api/certs/batches").status_code == 200
    pv = user.post("/api/certs/batch/preview", json=body)
    assert pv.status_code == 200 and pv.json()["will_change"] == 3
    assert user.post("/api/certs/batch/preview", json={"ids": ids, "changes": {"infra_owner": "x"}}).status_code == 403
    assert user.post("/api/certs/batch/preview", json={"ids": ids, "changes": {"mount_type": "IIS"}}).status_code == 400
    assert viewer.post("/api/certs/batch/apply", json={"ids": ids, "changes": body["changes"], "digest": pv.json()["digest"]}).status_code == 403
    ap = user.post("/api/certs/batch/apply", json={"ids": pv.json()["ids"], "changes": body["changes"], "digest": pv.json()["digest"]})
    assert ap.status_code == 200 and ap.json()["changed"] == 3
    assert user.post("/api/certs/batch/apply", json={"ids": ids, "changes": body["changes"], "digest": "bad"}).status_code == 409
    bl = user.get("/api/certs/batches").json()["batches"]
    assert len(bl) == 1 and "created_by" not in bl[0]
    assert "created_by" in admin.get("/api/certs/batches").json()["batches"][0]
    assert viewer.post(f"/api/certs/batch/{ap.json()['batch_id']}/undo").status_code == 403
    un = user.post(f"/api/certs/batch/{ap.json()['batch_id']}/undo")
    assert un.status_code == 200 and un.json()["restored"] == 3
    assert user.post(f"/api/certs/batch/{ap.json()['batch_id']}/undo").status_code == 400
    assert user.post("/api/certs/batch/nope/undo").status_code == 404


def test_API_批次稽核只記代號筆數欄位_不含人名(env):
    ids = _mk_api(env, 2)
    admin = env.client("admin")
    pv = admin.post("/api/certs/batch/preview", json={"ids": ids, "changes": {"infra_owner": "林大華", "replace_status": "待排程"}}).json()
    admin.post("/api/certs/batch/apply", json={"ids": pv["ids"], "changes": {"infra_owner": "林大華", "replace_status": "待排程"}, "digest": pv["digest"]})
    c = env.conn()
    paths = " ".join(r[0] for r in c.execute("SELECT path FROM activity_log WHERE method='CERT'"))
    assert "批次更新" in paths and "2 筆" in paths and "infra_owner" in paths and "林" not in paths
    c.close()


def test_API_型態管理只有管理員_新增後下拉與新增項目可用(env):
    user, admin = env.client("alice"), env.client("admin")
    assert user.post("/api/certs/mount-types/add", json={"name": "Tomcat"}).status_code == 403
    assert user.post("/api/certs/items", json={"name": "t.example.test", "environment": "正式", "endpoint": "192.0.2.50", "mount_type": "Tomcat"}).status_code == 400
    r = admin.post("/api/certs/mount-types/add", json={"name": "Tomcat"})
    assert r.status_code == 200 and "Tomcat" in [m["name"] for m in r.json()["mount_types"]]
    assert admin.post("/api/certs/mount-types/add", json={"name": "Tomcat"}).status_code == 409
    assert user.post("/api/certs/items", json={"name": "t.example.test", "environment": "正式", "endpoint": "192.0.2.50", "mount_type": "Tomcat"}).status_code == 200
    assert admin.post("/api/certs/mount-types/remove", json={"name": "Tomcat"}).status_code == 400          # 使用中
    assert admin.post("/api/certs/mount-types/set-active", json={"name": "Tomcat", "active": False}).status_code == 200
    assert admin.post("/api/certs/mount-types/set-active", json={"name": "Tomcat"}).status_code == 400
    assert admin.post("/api/certs/mount-types/set-active", json={"name": "沒有", "active": False}).status_code == 404


def test_API_停用要原因_作廢要原因_稽核不含自由文字(env):
    iid = _mk_api(env, 1)[0]
    user = env.client("alice")
    assert user.post(f"/api/certs/items/{iid}/deactivate", json={}).status_code == 422
    assert user.post(f"/api/certs/items/{iid}/deactivate", json={"reason_code": "亂寫"}).status_code == 400
    assert user.post(f"/api/certs/items/{iid}/deactivate", json={"reason_code": "mistake", "reason_note": "洪小明建錯"}).status_code == 200
    d = user.get(f"/api/certs/items/{iid}").json()
    assert d["active"] is False and d["deactivate_reason_label"] == "誤建"
    assert user.post(f"/api/certs/items/{iid}/reactivate").status_code == 200
    o = user.post(f"/api/certs/items/{iid}/observations", json={"not_after": "2030-01-01"}).json()["id"]
    assert user.post(f"/api/certs/items/{iid}/observations/{o}/void", json={"reason": ""}).status_code == 400
    assert user.post(f"/api/certs/items/{iid}/observations/{o}/void", json={"reason": "登錯"}).status_code == 200
    assert user.post(f"/api/certs/items/{iid}/observations/{o}/restore").status_code == 200
    viewer = env.client("vera")
    assert viewer.post(f"/api/certs/items/{iid}/observations/{o}/void", json={"reason": "x"}).status_code == 403
    c = env.conn()
    paths = " ".join(r[0] for r in c.execute("SELECT path FROM activity_log WHERE method='CERT'"))
    c.close()
    assert "洪" not in paths and "登錯" not in paths and "mistake" in paths


def test_API_匯入帶型態決定(env):
    admin = env.client("admin")
    data = _imp([["t1.example.test", "", "192.0.2.41", "否", "Tomcat", "正式", "營運部"]])
    pv = admin.post("/api/certs/import/preview", files={"file": ("a.xlsx", data)}).json()
    assert pv["unknown_mounts"] == [{"name": "Tomcat", "rows": 1}]
    r = admin.post("/api/certs/import/apply", files={"file": ("a.xlsx", data)},
                   data={"file_sha256": pv["file_sha256"], "plan_digest": pv["plan_digest"], "decisions": "{}", "mount_decisions": json.dumps({"Tomcat": "add"})})
    assert r.status_code == 200 and r.json()["created"] == 1
    assert admin.post("/api/certs/import/apply", files={"file": ("a.xlsx", data)}, data={"mount_decisions": "[1]"}).status_code == 400
    assert admin.get("/api/certs/summary").json()["import_info"]["last_import_file"] == "a.xlsx"


# ------------------------------------------------------------------ 審查必修：人名復原權限、同一秒內的修改
def _freeze(monkeypatch):
    """把時間固定在同一秒，證明衝突／digest 靠 rev 而不是秒級時間。"""
    monkeypatch.setattr(certs, "_now", lambda: datetime(2026, 11, 1, 1, 2, 3, tzinfo=UTC))


def test_同一秒內改回同值_批次復原仍判衝突(conn, seeded, monkeypatch):
    _freeze(monkeypatch)
    pv = _prev(conn, [seeded["A"]], {"replace_status": "已驗測"})
    out = certs.batch_apply(conn, pv["ids"], {"replace_status": "已驗測"}, "u", True, pv["digest"])
    certs.update_item(conn, seeded["A"], {"replace_status": "已更換"}, "o", True)      # 同一秒內被改
    certs.update_item(conn, seeded["A"], {"replace_status": "已驗測"}, "o", True)      # 又改回同值：值與時間都看不出來
    assert certs.item_detail(conn, seeded["A"], True)["updated_at"] == certs._iso(certs._now())
    res = certs.batch_undo(conn, out["batch_id"], "u", True)
    assert res["restored"] == 0 and len(res["conflicts"]) == 1


def test_同一秒內改批次沒碰的欄位_復原判衝突(conn, seeded, monkeypatch):
    _freeze(monkeypatch)
    pv = _prev(conn, [seeded["A"]], {"replace_status": "已驗測"})
    out = certs.batch_apply(conn, pv["ids"], {"replace_status": "已驗測"}, "u", True, pv["digest"])
    certs.update_item(conn, seeded["A"], {"note": "別人補的備註"}, "o", True)
    assert len(certs.batch_undo(conn, out["batch_id"], "u", True)["conflicts"]) == 1


def test_同一秒內預覽後被改又改回_digest不同_確認被拒(conn, seeded, monkeypatch):
    _freeze(monkeypatch)
    certs.update_item(conn, seeded["A"], {"note": "先寫一次，讓 updated_at 已是這一秒"}, "o", True)
    pv = _prev(conn, [seeded["A"]], {"replace_status": "已驗測"})
    certs.update_item(conn, seeded["A"], {"replace_status": "已更換"}, "o", True)
    certs.update_item(conn, seeded["A"], {"replace_status": ""}, "o", True)           # 值回到預覽當時
    with pytest.raises(certs.PlanChanged):
        certs.batch_apply(conn, pv["ids"], {"replace_status": "已驗測"}, "u", True, pv["digest"])


def test_每次寫入rev加一_舊庫補欄位預設0(conn, seeded):
    r0 = conn.execute("SELECT rev FROM cert_item WHERE id=?", (seeded["A"],)).fetchone()[0]
    certs.update_item(conn, seeded["A"], {"note": "x"}, "u", True)
    certs.set_active(conn, seeded["A"], False, "u", "retired")
    certs.set_active(conn, seeded["A"], True, "u")
    assert conn.execute("SELECT rev FROM cert_item WHERE id=?", (seeded["A"],)).fetchone()[0] == r0 + 3


def test_非管理員復原含執行人的批次_人名不被改動_其餘欄位照還原(env):
    ids = _mk_api(env, 2)                                   # 兩筆都有 infra_owner=陳阿德
    admin, user = env.client("admin"), env.client("alice")
    ch = {"infra_owner": "林大華", "replace_status": "待排程"}
    pv = admin.post("/api/certs/batch/preview", json={"ids": ids, "changes": ch}).json()
    ap = admin.post("/api/certs/batch/apply", json={"ids": pv["ids"], "changes": ch, "digest": pv["digest"]}).json()
    un = user.post(f"/api/certs/batch/{ap['batch_id']}/undo")
    assert un.status_code == 200
    body = un.json()
    assert body["restored"] == 2 and body["skipped_fields"] == ["執行人"] and body["skipped_items"] == 2
    assert "林" not in un.text and "陳" not in un.text
    for i in ids:
        d = admin.get(f"/api/certs/items/{i}").json()
        assert d["infra_owner"] == "林大華" and d["replace_status"] == ""         # 人名沒被一般使用者動到；其他欄位已還原
    assert "林" not in str(user.get("/api/certs/batches").json())
    c = env.conn()
    paths = " ".join(r[0] for r in c.execute("SELECT path FROM activity_log WHERE method='CERT'"))
    c.close()
    assert "林" not in paths and "陳" not in paths
    # 管理員之後可把人名也復原
    bl = admin.get("/api/certs/batches").json()["batches"][0]
    assert bl["state"] == "partial"
    un2 = admin.post(f"/api/certs/batch/{ap['batch_id']}/undo").json()
    assert un2["restored"] == 2 and not un2.get("skipped_fields")
    assert admin.get(f"/api/certs/items/{ids[0]}").json()["infra_owner"] == "陳阿德"


def test_非管理員復原只改人名的批次_什麼都不寫_批次仍留給管理員(conn, seeded):
    pv = _prev(conn, [seeded["A"]], {"infra_owner": "林大華"})
    out = certs.batch_apply(conn, pv["ids"], {"infra_owner": "林大華"}, "admin", True, pv["digest"])
    res = certs.batch_undo(conn, out["batch_id"], "alice", False)
    assert res["restored"] == 0 and res["skipped_fields"] == ["執行人"]
    assert certs.item_detail(conn, seeded["A"], True)["infra_owner"] == "林大華"
    assert certs.batch_undo(conn, out["batch_id"], "admin", True)["restored"] == 1
    assert certs.item_detail(conn, seeded["A"], True)["infra_owner"] == ""


# ------------------------------------------------------------------ 獨立審查必修
def test_同一秒內停用後復原_判衝突_項目不被動(conn, seeded, monkeypatch):
    _freeze(monkeypatch)
    pv = _prev(conn, [seeded["A"]], {"replace_status": "已驗測"})
    out = certs.batch_apply(conn, pv["ids"], {"replace_status": "已驗測"}, "u", True, pv["digest"])
    certs.set_active(conn, seeded["A"], False, "o", "retired")
    res = certs.batch_undo(conn, out["batch_id"], "u", True)
    assert res["restored"] == 0 and len(res["conflicts"]) == 1
    d = certs.item_detail(conn, seeded["A"], True)
    assert d["replace_status"] == "已驗測" and d["active"] is False


def test_復原只拿掉批次自己加進manual_fields的欄位(conn, seeded):
    certs.update_item(conn, seeded["A"], {"note": "人工備註"}, "u", True)           # manual_fields 已有 note
    pv = _prev(conn, [seeded["A"]], {"replace_status": "已驗測"})
    out = certs.batch_apply(conn, pv["ids"], {"replace_status": "已驗測"}, "u", True, pv["digest"])
    assert sorted(certs.item_detail(conn, seeded["A"], True)["manual_fields"]) == ["note", "replace_status"]
    certs.batch_undo(conn, out["batch_id"], "u", True)
    assert certs.item_detail(conn, seeded["A"], True)["manual_fields"] == ["note"]


def test_停用說明與作廢原因對非管理員遮罩_管理員看得到(conn, seeded):
    a = seeded["A"]
    oid = certs.item_detail(conn, a, True)["observations"][0]["id"]
    certs.void_observation(conn, a, oid, "洪小明登錯", "u")
    certs.set_active(conn, a, False, "u", "mistake", "陳阿德建錯")
    adm, usr = certs.item_detail(conn, a, True), certs.item_detail(conn, a, False)
    assert adm["deactivate_note"] == "陳阿德建錯" and adm["observations"][0]["void_reason"] == "洪小明登錯"
    assert usr["deactivate_note"] == "" and usr["observations"][0]["void_reason"] == ""
    assert usr["has_deactivate_note"] is True and usr["observations"][0]["has_void_reason"] is True and usr["observations"][0]["voided"] is True
    assert "陳" not in json.dumps(usr, ensure_ascii=False) and "洪" not in json.dumps(usr, ensure_ascii=False)


def test_API_停用說明與作廢原因_一般帳號與檢視者看不到_清單也沒有(env):
    iid = _mk_api(env, 1)[0]
    admin, user, viewer = env.client("admin"), env.client("alice"), env.client("vera")
    o = admin.post(f"/api/certs/items/{iid}/observations", json={"not_after": "2030-01-01"}).json()["id"]
    admin.post(f"/api/certs/items/{iid}/observations/{o}/void", json={"reason": "洪小明登錯"})
    admin.post(f"/api/certs/items/{iid}/deactivate", json={"reason_code": "mistake", "reason_note": "陳阿德建錯"})
    assert "陳阿德建錯" in admin.get(f"/api/certs/items/{iid}").text and "洪小明登錯" in admin.get(f"/api/certs/items/{iid}").text
    for c in (user, viewer):
        assert "建錯" not in c.get(f"/api/certs/items/{iid}").text and "登錯" not in c.get(f"/api/certs/items/{iid}").text
        assert "建錯" not in c.get("/api/certs/items", params={"active": "all"}).text
    assert "陳阿德建錯" in admin.get("/api/certs/items", params={"active": "all"}).text


def test_匯入遇到已停用的型態_視同未登記要人決定(conn):
    certs.add_mount_type(conn, "Tomcat", "u")
    certs.set_mount_active(conn, "Tomcat", False, "u")
    data = _imp([["t1.example.test", "", "192.0.2.41", "否", "tomcat", "正式", "營運部"]])
    plan = certs.import_plan(conn, "a.xlsx", data)
    assert plan["unknown_mounts"] == [{"name": "Tomcat", "rows": 1}] and "尚未登記" in plan["rows"][0]["reason"]
    out = certs.import_apply(conn, "a.xlsx", data, "u", {}, plan["file_sha256"], plan["plan_digest"], {})
    assert out["created"] == 0 and out["unresolved"] == 1                  # 沒決定就不寫
    out = certs.import_apply(conn, "a.xlsx", data, "u", {}, plan["file_sha256"], plan["plan_digest"], {"Tomcat": "add"})
    assert out["created"] == 1
    assert {m["name"]: m["active"] for m in certs.mount_types(conn)}["Tomcat"] is True          # 選新增＝重新啟用


def test_型態名稱超過30字_回錯誤不截斷(conn, env=None):
    with pytest.raises(ValueError, match="30"):
        certs.add_mount_type(conn, "x" * 31, "u")
    assert certs.add_mount_type(conn, "y" * 30, "u") == "y" * 30


def test_API_型態名稱過長回400(env):
    assert env.client("admin").post("/api/certs/mount-types/add", json={"name": "x" * 31}).status_code == 400


# ------------------------------------------------------------------ 第三輪
def test_需公告只收是否空白_匯入的舊自由文字原樣保留且沒改就放行(conn, seeded):
    a = seeded["A"]
    certs.update_item(conn, a, {"announce_needed": "否"}, "u", True)
    with pytest.raises(ValueError, match="是／否"):
        certs.update_item(conn, a, {"announce_needed": "看情況"}, "u", True)
    with pytest.raises(ValueError, match="是／否"):
        certs.create_item(conn, {"name": "z.example.test", "environment": "正式", "endpoint": "192.0.2.88", "announce_needed": "也許"}, "u", True)
    conn.execute("UPDATE cert_item SET announce_needed='視情況公告' WHERE id=?", (a,))     # 舊匯入值
    conn.commit()
    certs.update_item(conn, a, {"announce_needed": "視情況公告", "note": "別的欄位"}, "u", True)   # 沒改它：放行
    assert certs.item_detail(conn, a, True)["announce_needed"] == "視情況公告"


def test_批次紀錄帶影響項次(conn, seeded):
    pv = _prev(conn, [seeded["B"], seeded["A"]], {"replace_status": "已驗測"})
    certs.batch_apply(conn, pv["ids"], {"replace_status": "已驗測"}, "u", True, pv["digest"])
    b = certs.list_batches(conn, False)[0]
    seqs = {r["name"]: r["seq"] for r in certs.all_rows(conn, NOW)}
    assert b["item_seqs"] == sorted([seqs["a.example.test"], seqs["b.example.test"]])


# ------------------------------------------------------------------ 第四輪：標記已更換、排定更換一致、預排文字、新增識別
def _rep_op(**kw):
    return {"not_after": None, "later": False, "void_old": False, **kw}


def test_標記已更換_必填新到期日或明確稍後補登(conn, seeded):
    with pytest.raises(ValueError, match="稍後補登"):
        certs.mark_replaced(conn, seeded["A"], _rep_op(), "u", True)
    with pytest.raises(ValueError, match="新到期日"):
        certs.mark_replaced(conn, seeded["A"], _rep_op(later=True, void_old=True), "u", True)   # 作廢舊的卻沒新的：會變成無到期資料
    out = certs.mark_replaced(conn, seeded["A"], _rep_op(later=True), "u", True)                # 明確稍後補登：只改進度
    d = certs.item_detail(conn, seeded["A"], True)
    assert d["replace_status"] == "已更換" and len(d["observations"]) == 1 and out["batch_id"]


def test_標記已更換_登錄新到期日並作廢舊觀測_進度已更換_狀態跟著新日期(conn, seeded):
    a = seeded["A"]
    out = certs.mark_replaced(conn, a, _rep_op(not_after="2028-01-15", void_old=True), "alice", True)
    d = certs.item_detail(conn, a, True)
    assert d["replace_status"] == "已更換"
    live = [o for o in d["observations"] if not o["voided"]]
    old = [o for o in d["observations"] if o["voided"]]
    assert len(live) == 1 and live[0]["not_after_local"].startswith("2028-01-15") and len(old) == 1 and "已更換" in old[0]["void_reason"]
    assert next(r for r in certs.all_rows(conn, NOW) if r["id"] == a)["status"] == "ok"
    b = certs.list_batches(conn, True)[0]
    assert b["item_count"] == 1 and "新到期日→2028-01-15" in b["summary"] and out["batch_id"].startswith(b["short"])


def test_標記已更換_可從批次紀錄復原_新觀測作廢_舊觀測還原_進度回前值(conn, seeded):
    a = seeded["A"]
    certs.update_item(conn, a, {"replace_status": "待排程"}, "u", True)
    out = certs.mark_replaced(conn, a, _rep_op(not_after="2028-01-15", void_old=True), "u", True)
    res = certs.batch_undo(conn, out["batch_id"], "u", True)
    assert res["restored"] == 1 and not res["conflicts"]
    d = certs.item_detail(conn, a, True)
    assert d["replace_status"] == "待排程"
    live = [o for o in d["observations"] if not o["voided"]]
    assert len(live) == 1 and live[0]["not_after_local"].startswith("2026-11-05")          # 舊的回來了
    assert any(o["voided"] and "復原批次" in o["void_reason"] for o in d["observations"])    # 新的保留紀錄但作廢


def test_標記已更換後又登錄了觀測_復原判衝突_不動(conn, seeded, monkeypatch):
    _freeze(monkeypatch)
    a = seeded["A"]
    out = certs.mark_replaced(conn, a, _rep_op(not_after="2028-01-15", void_old=True), "u", True)
    certs.add_observation(conn, a, {"not_after": "2029-01-01"}, "o")                         # 只動觀測、沒動項目欄位，同一秒
    res = certs.batch_undo(conn, out["batch_id"], "u", True)
    assert res["restored"] == 0 and len(res["conflicts"]) == 1
    assert certs.item_detail(conn, a, True)["replace_status"] == "已更換"


def test_標記已更換_同樣到期時間已存在_不重複新增(conn, seeded):
    a = seeded["A"]
    certs.mark_replaced(conn, a, _rep_op(not_after="2026-11-05 08:00"), "u", True)             # = 既有觀測的 UTC 2026-11-05 00:00
    assert len(certs.item_detail(conn, a, True)["observations"]) == 1


def test_批次也可標記已更換_預覽講清楚新增與作廢筆數_確認帶同樣的replace_op(conn, seeded):
    ids = [seeded["A"], seeded["B"]]
    op = _rep_op(not_after="2028-02-01", void_old=True)
    pv = certs.batch_preview(conn, ids, None, {}, True, NOW, op)
    assert pv["will_change"] == 2 and pv["replace_op"]["obs_added"] == 2 and pv["replace_op"]["obs_voided"] == 2 and pv["replace_op"]["void_old"] is True
    assert any(c["label"] == "新到期日" for c in pv["sample"][0]["changes"])
    with pytest.raises(certs.PlanChanged):        # 預覽的是作廢舊觀測，確認改成不作廢 → digest 不同
        certs.batch_apply(conn, pv["ids"], {}, "u", True, pv["digest"], None, _rep_op(not_after="2028-02-01"))
    certs.batch_apply(conn, pv["ids"], {}, "u", True, pv["digest"], None, op)
    assert all(certs.item_detail(conn, i, True)["replace_status"] == "已更換" for i in ids)
    with pytest.raises(ValueError, match="只能是"):
        certs.batch_preview(conn, ids, None, {"replace_status": "待排程"}, True, NOW, op)


def test_API_標記已更換_檢視者不可_缺日期400_成功後批次紀錄可見(env):
    iid = _mk_api(env, 1)[0]
    admin, user, viewer = env.client("admin"), env.client("alice"), env.client("vera")
    assert viewer.post(f"/api/certs/items/{iid}/mark-replaced", json={"later": True}).status_code == 403
    assert user.post(f"/api/certs/items/{iid}/mark-replaced", json={}).status_code == 400
    r = user.post(f"/api/certs/items/{iid}/mark-replaced", json={"not_after": "2030-05-01 10:00"})
    assert r.status_code == 200
    assert user.get(f"/api/certs/items/{iid}").json()["replace_status"] == "已更換"
    assert len(user.get("/api/certs/batches").json()["batches"]) == 1
    assert user.post(f"/api/certs/batch/{r.json()['batch_id']}/undo").status_code == 200
    c = env.conn()
    paths = " ".join(x[0] for x in c.execute("SELECT path FROM activity_log WHERE method='CERT'"))
    c.close()
    assert "標記已更換" in paths


def test_排定更換_兩欄一致_單獨改造成矛盾會被擋(conn, seeded):
    a = seeded["A"]
    with pytest.raises(ValueError, match="預排日期"):
        certs.update_item(conn, a, {"replace_status": "已排程"}, "u", True)                  # 已排定沒有日期
    certs.schedule_item(conn, a, "2026-12-05", "u", True)
    d = certs.item_detail(conn, a, True)
    assert d["planned_date"] == "2026-12-05" and d["replace_status"] == "已排程"
    for bad in ({"replace_status": "待排程"}, {"replace_status": ""}, {"planned_date": ""}):
        with pytest.raises(ValueError):
            certs.update_item(conn, a, bad, "u", True)                                       # 有日期卻待排程／日期被清掉卻還是已排程
    certs.update_item(conn, a, {"replace_status": "已更換"}, "u", True)                       # 已更換可以保留日期
    with pytest.raises(ValueError, match="預排日期"):
        certs.update_item(conn, seeded["B"], {"planned_date": "2026-12-05"}, "u", True)      # 沒設進度只填日期


def test_舊資料本來就矛盾_改別的欄位不被擋(conn, seeded):
    a = seeded["A"]
    conn.execute("UPDATE cert_item SET replace_status='已排程', planned_date=NULL WHERE id=?", (a,))     # 舊資料：已排程沒日期
    conn.commit()
    certs.update_item(conn, a, {"note": "補備註"}, "u", True)
    assert certs.item_detail(conn, a, True)["replace_status"] == "已排程"


def test_批次設預排日期與進度_矛盾整批擋下_一起設就過(conn, seeded):
    ids = [seeded["A"], seeded["B"]]
    with pytest.raises(ValueError, match="矛盾"):
        _prev(conn, ids, {"replace_status": "已排程"})
    with pytest.raises(ValueError, match="矛盾"):
        _prev(conn, ids, {"planned_date": "2026-12-05"})
    pv = _prev(conn, ids, {"replace_status": "已排程", "planned_date": "2026-12-05"})
    certs.batch_apply(conn, pv["ids"], {"replace_status": "已排程", "planned_date": "2026-12-05"}, "u", True, pv["digest"])
    assert certs.item_detail(conn, seeded["A"], True)["planned_date"] == "2026-12-05"


def test_清空預排日期後_不退回採用匯入原文字當排程(conn, seeded):
    a = seeded["A"]
    certs.update_item(conn, a, {"planned_text": "2026-12-10 22:00 夜間"}, "u", True)
    assert next(r for r in certs.all_rows(conn, NOW) if r["id"] == a)["planned_date"] == "2026-12-10"          # 沒有正式日期：暫時採用匯入文字
    certs.schedule_item(conn, a, "2026-12-20", "u", True)
    certs.update_item(conn, a, {"planned_date": "", "replace_status": "待排程"}, "u", True)                     # 使用者清空
    d = certs.item_detail(conn, a, True)
    assert d["planned_ignored"] is True and next(r for r in certs.all_rows(conn, NOW) if r["id"] == a)["planned_date"] == ""
    assert d["planned_text"].startswith("2026-12-10")                                                           # 原文字仍保留，只是不當排程


def test_批次清空預排日期_同樣不退回匯入文字_復原會還原(conn, seeded):
    a = seeded["A"]
    certs.update_item(conn, a, {"planned_text": "2026-12-10"}, "u", True)
    certs.schedule_item(conn, a, "2026-12-20", "u", True)
    pv = _prev(conn, [a], {"planned_date": "", "replace_status": "待排程"})
    out = certs.batch_apply(conn, pv["ids"], {"planned_date": "", "replace_status": "待排程"}, "u", True, pv["digest"])
    assert next(r for r in certs.all_rows(conn, NOW) if r["id"] == a)["planned_date"] == ""
    certs.batch_undo(conn, out["batch_id"], "u", True)
    d = certs.item_detail(conn, a, True)
    assert d["planned_date"] == "2026-12-20" and d["planned_ignored"] is False


def test_匯入預排文字_可轉成正式日期_或忽略_無法轉日期只能忽略(conn, seeded):
    a, b = seeded["A"], seeded["B"]
    certs.update_item(conn, a, {"planned_text": "2026-12-10 22:00"}, "u", True)
    certs.update_item(conn, b, {"planned_text": "下週末"}, "u", True)
    assert certs.item_detail(conn, a, True)["planned_text_date"] == "2026-12-10" and certs.item_detail(conn, b, True)["planned_text_date"] == ""
    r = certs.planned_import_action(conn, a, "convert", "u")
    assert r["planned_date"] == "2026-12-10" and r["replace_status"] == "已排程"
    with pytest.raises(ValueError, match="已經有"):
        certs.planned_import_action(conn, a, "convert", "u")
    with pytest.raises(ValueError, match="無法轉成日期"):
        certs.planned_import_action(conn, b, "convert", "u")
    certs.planned_import_action(conn, b, "ignore", "u")
    assert certs.item_detail(conn, b, True)["planned_ignored"] is True
    with pytest.raises(ValueError):
        certs.planned_import_action(conn, b, "亂寫", "u")


def test_新增憑證_最低識別資料_環境與端點或SNI(conn):
    with pytest.raises(ValueError, match="環境必填"):
        certs.create_item(conn, {"name": "n.example.test", "endpoint": "192.0.2.9"}, "u", True)
    with pytest.raises(ValueError, match="環境必填"):
        certs.create_item(conn, {"name": "n.example.test", "environment": "亂寫", "endpoint": "192.0.2.9"}, "u", True)
    with pytest.raises(ValueError, match="端點.*SNI"):
        certs.create_item(conn, {"name": "n.example.test", "environment": "正式"}, "u", True)
    certs.create_item(conn, {"name": "n.example.test", "environment": "正式", "sni": "n.example.test"}, "u", True)     # 只有 SNI 也夠
    certs.create_item(conn, {"name": "m.example.test", "environment": "測試", "endpoint": "192.0.2.9"}, "u", True)    # 只有端點也夠


def test_新增重複_說明是哪五欄相同(conn):
    body = {"name": "n.example.test", "environment": "正式", "endpoint": "192.0.2.9", "sni": "n.example.test", "mount_type": "F5"}
    certs.create_item(conn, body, "u", True)
    with pytest.raises(certs.KeyConflict) as e:
        certs.create_item(conn, {**body, "name": "換個名字"}, "u", True)
    msg = str(e.value)
    assert all(k in msg for k in ("環境", "端點", "埠", "SNI", "掛載型態", "都相同", "n.example.test"))
    certs.create_item(conn, {**body, "environment": "測試"}, "u", True)             # 只差環境：不同項目，不算重複


def test_含人名匯出沿用目前篩選(conn, seeded):
    head, rows = certs.export_rows(conn, True, NOW, {"env": "測試"})
    assert len(rows) == 1 and rows[0][1] == "c.example.test"
    head2, rows2 = certs.export_rows(conn, True, NOW)
    assert len(rows2) == 6                                                           # 沒帶篩選＝全部（含停用）
    with pytest.raises(ValueError):
        certs.export_rows(conn, True, NOW, {"within": "x"})


def test_API_含人名匯出帶篩選_稽核列數是篩選後的筆數(env):
    _mk_api(env, 3)
    admin = env.client("admin")
    r = admin.post("/api/certs/export-identified", json={"purpose_code": "audit", "filter": {"q": "s1"}})
    assert r.status_code == 200
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(r.content)).active
    assert ws.max_row == 2                                                           # 表頭＋1 筆
    c = env.conn()
    paths = " ".join(x[0] for x in c.execute("SELECT path FROM activity_log WHERE method='CERT'"))
    c.close()
    assert "含人名匯出 1 列" in paths
    assert admin.post("/api/certs/export-identified", json={"purpose_code": "audit", "filter": {"within": "x"}}).status_code == 400


# ------------------------------------------------------------------ 第五輪：匯入觀測 rev、稽核同交易、忽略後不回傳可轉日期
def _imp_with_expiry(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["憑證名稱", "F5 BIG IP", "主機 IP", "F5是否掛憑證", "OS/站台類型", "正/測", "申請單位", "憑證到期日"])
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_標記已更換後_匯入又新增觀測_復原要列衝突(conn):
    iid = certs.create_item(conn, {"name": "i1.example.test", "environment": "正式", "endpoint": "192.0.2.43", "sni": "i1.example.test",
                                   "mount_type": "IIS", "host_ips": "192.0.2.43", "os_type": "IIS", "req_unit": "營運部", "f5_mounted": 0}, "u", True)["id"]
    out = certs.mark_replaced(conn, iid, {"not_after": None, "later": True, "void_old": False}, "u", True)      # 稍後補登：這筆還沒有任何觀測
    data = _imp_with_expiry([["i1.example.test", "", "192.0.2.43", "否", "IIS", "正式", "營運部", "2027-05-01"]])
    plan = certs.import_plan(conn, "a.xlsx", data)
    assert plan["counts"]["update"] == 1                                                                    # 只差到期日觀測
    r = certs.import_apply(conn, "a.xlsx", data, "u", {}, plan["file_sha256"], plan["plan_digest"], {})
    assert r["observations"] == 1
    assert certs.item_detail(conn, iid, True)["replaced_pending"] == 0                                      # 匯入補了到期日＝不再待補
    res = certs.batch_undo(conn, out["batch_id"], "u", True)
    assert res["restored"] == 0 and len(res["conflicts"]) == 1                                              # 匯入動過這筆：不可默默還原
    assert certs.item_detail(conn, iid, True)["replace_status"] == "已更換"


def _boom(env, action):
    c = env.conn()
    c.execute(f"CREATE TRIGGER boom BEFORE INSERT ON activity_log WHEN NEW.action='{action}' BEGIN SELECT RAISE(ABORT,'boom'); END")
    c.commit()
    c.close()


def _state(env, iid):
    c = env.conn()
    try:
        return (c.execute("SELECT replace_status, planned_date, planned_ignored, rev, active, note FROM cert_item WHERE id=?", (iid,)).fetchone(),
                c.execute("SELECT count(*) FROM cert_observation WHERE item_id=?", (iid,)).fetchone()[0],
                c.execute("SELECT count(*) FROM cert_batch").fetchone()[0], c.execute("SELECT count(*) FROM cert_batch_item").fetchone()[0])
    finally:
        c.close()


def test_API_標記已更換_稽核寫失敗_資料一併回滾(env):
    iid = _mk_api(env, 1)[0]
    before = _state(env, iid)
    _boom(env, "cert_mark_replaced")
    r = env.client("alice").post(f"/api/certs/items/{iid}/mark-replaced", json={"not_after": "2030-05-01 10:00", "void_old": False})
    assert r.status_code >= 500          # 稽核寫失敗＝整個請求失敗
    assert _state(env, iid) == before                                           # 項目、觀測、批次紀錄都沒留下


def test_API_預排文字處理_稽核寫失敗_資料一併回滾(env):
    iid = _mk_api(env, 1)[0]
    env.client("admin").put(f"/api/certs/items/{iid}", json={"planned_text": "2026-12-10 22:00"})
    before = _state(env, iid)
    _boom(env, "cert_planned_import")
    for act in ("convert", "ignore"):
        r = env.client("alice").post(f"/api/certs/items/{iid}/planned-import", json={"action": act})
        assert r.status_code >= 500          # 稽核寫失敗＝整個請求失敗
        assert _state(env, iid) == before


@pytest.mark.parametrize("action,call", [
    ("cert_update", lambda c, i: c.put(f"/api/certs/items/{i}", json={"note": "改備註"})),
    ("cert_deactivate", lambda c, i: c.post(f"/api/certs/items/{i}/deactivate", json={"reason_code": "retired"})),
    ("cert_observe", lambda c, i: c.post(f"/api/certs/items/{i}/observations", json={"not_after": "2031-01-01"})),
    ("cert_batch", lambda c, i: _batch_call(c, i)),
])
def test_API_其他寫入端點_稽核寫失敗_資料一併回滾(env, action, call):
    iid = _mk_api(env, 1)[0]
    before = _state(env, iid)
    _boom(env, action)
    r = call(env.client("alice"), iid)
    assert r.status_code >= 500          # 稽核寫失敗＝整個請求失敗
    assert _state(env, iid) == before


def _batch_call(c, iid):
    ch = {"replace_status": "已驗測"}
    pv = c.post("/api/certs/batch/preview", json={"ids": [iid], "changes": ch}).json()
    return c.post("/api/certs/batch/apply", json={"ids": pv["ids"], "changes": ch, "digest": pv["digest"]})


def test_API_批次復原_稽核寫失敗_復原一併回滾(env):
    iid = _mk_api(env, 1)[0]
    user = env.client("alice")
    ap = _batch_call(user, iid).json()
    before = _state(env, iid)
    _boom(env, "cert_batch_undo")
    r = user.post(f"/api/certs/batch/{ap['batch_id']}/undo")
    assert r.status_code >= 500          # 稽核寫失敗＝整個請求失敗
    assert _state(env, iid) == before
    c = env.conn()
    assert c.execute("SELECT count(*) FROM cert_batch_item WHERE undone=1").fetchone()[0] == 0
    c.close()


def test_稽核成功時資料與稽核在同一個commit_且沒有掛鉤時行為不變(conn, seeded):
    seen = []

    def hook(c, info):
        seen.append(("hook", c.in_transaction, dict(info)))

    with certs.audit_scope(hook):
        certs.update_item(conn, seeded["A"], {"note": "x"}, "u", True)
    assert seen and seen[0][1] is True and seen[0][2]["changed"] == ["note"]        # 呼叫時資料異動還沒 commit
    seen.clear()
    certs.update_item(conn, seeded["A"], {"note": "y"}, "u", True)
    assert seen == []                                                              # 範圍外不呼叫


def test_忽略匯入值後_詳情不再回傳可轉成的日期(conn, seeded):
    a = seeded["A"]
    certs.update_item(conn, a, {"planned_text": "2026-12-10 22:00"}, "u", True)
    assert certs.item_detail(conn, a, True)["planned_text_date"] == "2026-12-10"
    certs.planned_import_action(conn, a, "ignore", "u")
    d = certs.item_detail(conn, a, True)
    assert d["planned_ignored"] is True and d["planned_text_date"] is None


# ------------------------------------------------------------------ 第六輪
def _later(conn, iid):
    return certs.mark_replaced(conn, iid, {"not_after": None, "later": True, "void_old": False}, "u", True)


def test_稍後補登_舊到期不再判斷風險_歸無到期資料_風險等級待補到期日(conn, seeded):
    a = seeded["A"]
    before = next(r for r in certs.all_rows(conn, NOW) if r["id"] == a)
    assert before["status"] == "d7" and before["risk"] == "高"                           # 原本剩 4 天：高風險
    _later(conn, a)
    r = next(r for r in certs.all_rows(conn, NOW) if r["id"] == a)
    assert r["status"] == "none" and r["risk"] == "待補到期日" and r["risk_code"] == "pending"
    assert r["status_label"] == "待補新到期日" and r["pending_expiry"] is True and r["not_after_utc"] is None and r["remain_text"] == "待補新到期日"
    d = certs.item_detail(conn, a, True)
    assert d["view"]["status_label"] == "待補新到期日" and len(d["observations"]) == 1       # 舊觀測仍保留，只是不再拿來判斷


def test_稍後補登_卡片_清單_篩選同一份計算(conn, seeded):
    a = seeded["A"]
    s0 = certs.summary(conn, NOW)
    _later(conn, a)
    s1 = certs.summary(conn, NOW)
    c0 = {c["key"]: c["count"] for c in s0["cards"]}
    c1 = {c["key"]: c["count"] for c in s1["cards"]}
    assert c1["d7"] == c0["d7"] - 1 and c1["none"] == c0["none"] + 1 and sum(c1.values()) == s1["total_active"]
    assert names(q(conn, pending="1")) == ["a"] and "a" not in names(q(conn, pending="0"))
    assert "a" in names(q(conn, status="none")) and "a" in names(q(conn, has_expiry="0")) and names(q(conn, risk="pending")) == ["a"]
    assert len(q(conn, status="none")["items"]) == c1["none"]
    with pytest.raises(ValueError):
        q(conn, pending="x")


def test_補登新到期日後_待補消失_風險回來(conn, seeded):
    a = seeded["A"]
    _later(conn, a)
    certs.add_observation(conn, a, {"not_after": "2028-01-01 00:00:00+00:00"}, "u")
    r = next(r for r in certs.all_rows(conn, NOW) if r["id"] == a)
    assert r["pending_expiry"] is False and r["status"] == "ok" and r["risk"] == ""
    assert names(q(conn, pending="1")) == []


def test_復原稍後補登的批次_舊到期與待補旗標都回來(conn, seeded):
    a = seeded["A"]
    out = _later(conn, a)
    res = certs.batch_undo(conn, out["batch_id"], "u", True)
    assert res["restored"] == 1 and not res["conflicts"]
    r = next(r for r in certs.all_rows(conn, NOW) if r["id"] == a)
    assert r["status"] == "d7" and r["pending_expiry"] is False and certs.item_detail(conn, a, True)["replaced_pending"] == 0


def test_批次標記已更換_稍後補登_每筆都待補_填新到期日的不待補(conn, seeded):
    ids = [seeded["A"], seeded["B"]]
    certs.batch_apply(conn, ids, {}, "u", True, certs.batch_preview(conn, ids, None, {}, True, NOW, _rep_op(later=True))["digest"], None, _rep_op(later=True))
    assert names(q(conn, pending="1")) == ["a", "b"]
    op = _rep_op(not_after="2028-03-01")
    pv = certs.batch_preview(conn, ids, None, {}, True, NOW, op)
    assert pv["will_change"] == 2
    certs.batch_apply(conn, pv["ids"], {}, "u", True, pv["digest"], None, op)
    assert names(q(conn, pending="1")) == [] and certs.item_detail(conn, seeded["A"], True)["view"]["status"] == "ok"


def test_verify_numbers_待補到期日項目_卡片與獨立重算一致(conn, seeded):
    import verify_numbers as vn
    fails0 = vn.FAILS
    _later(conn, seeded["A"])
    before = len(vn.RESULTS)
    vn.check_certs(conn)
    mine = vn.RESULTS[before:]
    assert len(mine) >= 6 and not [x for x in mine if x["ok"] is False], [x for x in mine if x["ok"] is False]
    vn.FAILS = fails0


def _imp_planned(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["憑證名稱", "F5 BIG IP", "主機 IP", "F5是否掛憑證", "OS/站台類型", "正/測", "申請單位", "預排更換時間"])
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_匯入重跑_預排文字換了就重新讓人決定_相同則維持忽略(conn):
    def run(text):
        data = _imp_planned([["p1.example.test", "", "192.0.2.61", "否", "IIS", "正式", "營運部", text]])
        pl = certs.import_plan(conn, "a.xlsx", data)
        return certs.import_apply(conn, "a.xlsx", data, "u", {}, pl["file_sha256"], pl["plan_digest"], {})
    run("2026-12-10")
    iid = certs.all_rows(conn)[0]["id"]
    certs.planned_import_action(conn, iid, "ignore", "u")
    assert certs.item_detail(conn, iid, True)["planned_ignored"] is True
    run("2026-12-10")                                                              # 文字相同：維持忽略
    assert certs.item_detail(conn, iid, True)["planned_ignored"] is True
    run("2026-12-20")                                                              # 文字換了：歸 0，新值重新讓人決定
    d = certs.item_detail(conn, iid, True)
    assert d["planned_ignored"] is False and d["planned_text"] == "2026-12-20" and d["planned_text_date"] == "2026-12-20"


def test_改身分鍵_端點與SNI不能同時變空(conn, seeded):
    a = seeded["A"]
    certs.update_item(conn, a, {"sni": "a.example.test"}, "u", True)
    with pytest.raises(ValueError, match="至少要留一個"):
        certs.update_item(conn, a, {"endpoint": "", "sni": ""}, "u", True)
    certs.update_item(conn, a, {"endpoint": ""}, "u", True)                        # 還有 SNI：可以
    with pytest.raises(ValueError, match="至少要留一個"):
        certs.update_item(conn, a, {"sni": ""}, "u", True)                         # 這下兩個都空
    certs.update_item(conn, seeded["B"], {"note": "別的欄位不受影響"}, "u", True)


def test_忽略匯入值_沒有預排文字回錯誤_已忽略是空操作不加版本(conn, seeded):
    a = seeded["A"]
    with pytest.raises(ValueError, match="沒有匯入的預排文字"):
        certs.planned_import_action(conn, a, "ignore", "u")
    certs.update_item(conn, a, {"planned_text": "下週末"}, "u", True)
    certs.planned_import_action(conn, a, "ignore", "u")
    rev = conn.execute("SELECT rev FROM cert_item WHERE id=?", (a,)).fetchone()[0]
    r = certs.planned_import_action(conn, a, "ignore", "u")
    assert r.get("noop") is True and conn.execute("SELECT rev FROM cert_item WHERE id=?", (a,)).fetchone()[0] == rev


def test_API_含人名匯出稽核只記篩選鍵名_不記值(env):
    _mk_api(env, 2)
    admin = env.client("admin")
    assert admin.post("/api/certs/export-identified", json={"purpose_code": "audit", "filter": {"q": "秘密關鍵字", "env": "正式"}}).status_code == 200
    c = env.conn()
    paths = " ".join(x[0] for x in c.execute("SELECT path FROM activity_log WHERE method='CERT'"))
    c.close()
    assert "篩選鍵：env,q" in paths and "秘密關鍵字" not in paths
