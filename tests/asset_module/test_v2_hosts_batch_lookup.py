"""2.1 主機查詢：貼多筆批次查詢＋重複登記唯讀清單（2.041.84；舊版缺口 230 第 2、3 項「看」的部分）。

守的是：精確 vs 部分命中標示（Codex 233/235）、查無一定列出、超量拒絕、共用解析器與同一份去重（machine_key）、
整批只建一次群組索引、不寫庫。全部用合成假庫（IP 10.99.x）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import host_lookup  # noqa: E402
import oncall  # noqa: E402
import vip_view  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "d.db"
    db.init_db(p)
    conn = db.get_connection(p)
    rows = [
        ("HW-A1", "Demo-Uni01.corp.example", "10.99.1.1", "AP-1"),
        ("HW-A2", "demo-uni01", "10.99.1.1", "AP-1"),            # 同一台第二筆（FQDN 與短名同名）
        ("HW-B1", "web01", "10.99.2.1", "AP-2"),
        ("HW-B2", "web02", "10.99.2.2", "AP-2"),
        ("HW-C1", "dup-host", "10.99.3.1", "AP-3"),              # 同機同 IP 但系統不同＝一機多系統
        ("HW-C2", "dup-host", "10.99.3.1", "AP-4"),
    ]
    for sn, h, ip, ap in rows:
        db.insert_hardware(conn, asset_serial=sn, hostname=h, ip=ip, api_id=ap, asset_status="使用中")
    conn.commit()
    return conn


def _by_term(res):
    return {r["term"]: r for r in res["rows"]}


def test_精確模式_找到_多筆_查無各一列(tmp_path):
    conn = _conn(tmp_path)
    res = host_lookup.batch(conn, ["demo-uni01", "10.99.2.1", "AP-2", "nope-host", "HW-B2"])
    r = _by_term(res)
    assert r["demo-uni01"]["status"] == "one" and r["demo-uni01"]["matched_by"] == "主機名"
    assert r["demo-uni01"]["records"] == 2                                  # 同一台兩筆登記合成一台
    assert r["10.99.2.1"]["status"] == "one" and r["10.99.2.1"]["matched_by"] == "IP"
    assert r["AP-2"]["status"] == "many" and r["AP-2"]["count"] == 2 and r["AP-2"]["matched_by"] == "APID"
    assert r["AP-2"]["candidates_text"]
    assert r["HW-B2"]["matched_by"] == "資產序號"
    assert r["nope-host"]["status"] == "none" and r["nope-host"]["key"] is None
    assert len(res["rows"]) == 5                                            # 查無也一列，不少列
    s = res["summary"]
    assert s["one"] + s["many"] + s["partial"] + s["none"] == s["unique"] == 5


def test_精確模式不做部分比對_部分模式明確標示(tmp_path):
    conn = _conn(tmp_path)
    ex = host_lookup.batch(conn, ["demo-uni"])
    assert ex["rows"][0]["status"] == "none" and ex["mode"] == "exact"      # 不把部分字串宣稱成找到
    pt = host_lookup.batch(conn, ["demo-uni"], partial=True)
    row = pt["rows"][0]
    assert row["status"] == "partial" and row["partial"] is True and row["matched_by"] == "部分比對"
    assert row["status_label"] == "部分命中"
    assert row["key"] is None                                               # 部分命中不填成「這就是那台」
    assert pt["summary"]["partial"] == 1 and pt["summary"]["one"] == 0


def test_大小寫與FQDN同一台_重複輸入合併並記次數(tmp_path):
    conn = _conn(tmp_path)
    res = host_lookup.batch(conn, ["WEB01", "web01.corp.example", "web01", "", "  ", "WEB01"])
    # 'WEB01'、'web01'、'WEB01' 屬同一輸入（不分大小寫）合成一列；FQDN 是另一個輸入但解析到同一台
    assert [r["term"] for r in res["rows"]] == ["WEB01", "web01.corp.example"]
    assert res["rows"][0]["input_count"] == 3
    assert res["rows"][0]["key"] == res["rows"][1]["key"] and res["rows"][0]["status"] == "one"
    s = res["summary"]
    assert (s["raw_lines"], s["blank"], s["unique"], s["duplicates_merged"]) == (6, 2, 2, 2)


def test_與oncall_resolve結果一致_並整批共用群組索引(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    terms = ["demo-uni01", "10.99.3.1", "AP-3", "HW-C1", "zzz"]
    calls = {"groups": 0}
    real = oncall._groups

    def spy(c):
        calls["groups"] += 1
        return real(c)

    monkeypatch.setattr(oncall, "_groups", spy)
    host_lookup.batch(conn, terms[:1])
    one = calls["groups"]
    calls["groups"] = 0
    res = host_lookup.batch(conn, terms)
    assert calls["groups"] == one                                            # 建索引次數不隨項數增加（不是每行重建）
    monkeypatch.undo()
    groups, _ = oncall._groups(conn)
    for r in res["rows"]:
        how, keys = oncall.resolve(conn, r["term"], groups, exact=True)
        assert r["matched_by"] == how and r["count"] == len(keys)


def test_exact不改預設行為_resolve原本仍有部分比對(tmp_path):
    conn = _conn(tmp_path)
    groups, _ = oncall._groups(conn)
    assert oncall.resolve(conn, "demo-uni", groups)[0] == "部分比對"
    assert oncall.resolve(conn, "demo-uni", groups, exact=True) == (None, [])
    # 精確命中兩邊相同
    assert oncall.resolve(conn, "web01", groups) == oncall.resolve(conn, "web01", groups, exact=True)


def test_機器代號輸入不支援_查無並說明(tmp_path):
    conn = _conn(tmp_path)
    groups, _ = oncall._groups(conn)
    key = next(iter(groups))
    res = host_lookup.batch(conn, [key])
    row = res["rows"][0]
    assert row["status"] == "none" and "不支援" in (row["hint"] or "")
    assert any("機器代號" in n for n in res["notes"])


@pytest.mark.parametrize("bad", [
    ["x"] * (host_lookup.MAX_RAW_LINES + 1),                                 # 原始行數
    ["a" * (host_lookup.MAX_TERM_LEN + 1)],                                  # 單項長度
    [str(i) for i in range(host_lookup.MAX_UNIQUE + 1)],                     # 去重後項數
    ["", "   "],                                                             # 全空
    ["a" * 120] * 600,                                                       # 總量
])
def test_超量或全空拒絕(tmp_path, bad):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        host_lookup.batch(conn, bad)


def test_原始行數限制算重複與空白行_不只看去重後(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        host_lookup.batch(conn, ["web01"] * (host_lookup.MAX_RAW_LINES + 1))
    ok = host_lookup.batch(conn, ["web01"] * host_lookup.MAX_RAW_LINES)
    assert ok["summary"]["unique"] == 1 and ok["summary"]["duplicates_merged"] == host_lookup.MAX_RAW_LINES - 1


def test_批次查詢不寫庫(tmp_path):
    conn = _conn(tmp_path)
    before = conn.total_changes
    host_lookup.batch(conn, ["web01", "AP-2", "nope"], partial=True)
    host_lookup.duplicates(conn)
    assert conn.total_changes == before


# ---------------------------------------------------------------- 重複登記
def test_重複登記分組與vip_view同一份(tmp_path):
    conn = _conn(tmp_path)
    ref = vip_view.duplicate_candidate_groups(conn)
    res = host_lookup.duplicates(conn)
    assert [g["key"] for g in res["groups"]] == [k for k, _ in ref]          # 同一把尺：machine_key
    assert {g["key"]: sorted(g["serials"]) for g in res["groups"]} == {k: sorted(m["asset_serial"] for m in ms) for k, ms in ref}
    assert len(res["groups"]) == 2                                           # demo-uni01、dup-host；web01／web02 各一筆不成組
    assert sum(g["count"] for g in res["groups"]) == len(res["members"]) == 4   # 組內筆數加總＝明細列數（加總＝下鑽）
    assert sum(res["by_kind"].values()) == len(res["groups"])
    assert [g["no"] for g in res["groups"]] == [1, 2]


def test_重複登記_分類_來源_差異欄位(tmp_path):
    conn = _conn(tmp_path)
    res = host_lookup.duplicates(conn)
    g = {x["hostname"].lower().split(".")[0]: x for x in res["groups"]}
    assert g["dup-host"]["kind"] == vip_view.DUP_MULTI_SYSTEM                # 兩個 AP ID＝一機多系統，不是重複
    assert "api_id" in g["dup-host"]["diff_fields"] and g["dup-host"]["extra_rows"] == 0
    assert g["demo-uni01"]["kind"] == vip_view.DUP_SUSPECT and g["demo-uni01"]["extra_rows"] == 1
    assert res["extra_rows"] == 1
    m = [x for x in res["members"] if x["group_key"] == g["dup-host"]["key"]]
    assert {x["serial"] for x in m} == {"HW-C1", "HW-C2"}
    assert all(x["how"] == "匯入或手動新增" for x in m)                       # 來源路徑（序號前綴）
    assert {x["diff"]["api_id"] for x in m} == {"AP-3", "AP-4"}
    assert g["demo-uni01"]["origin_text"].startswith("匯入或手動新增 2 筆")


def test_重複登記_來源前綴ADOPT與AUTO(tmp_path):
    conn = _conn(tmp_path)
    for sn in ("ADOPT-10.99.9.9", "AUTO-10.99.9.9"):
        db.insert_hardware(conn, asset_serial=sn, hostname="srv-x", ip="10.99.9.9", asset_status="使用中")
    conn.commit()
    res = host_lookup.duplicates(conn)
    g = next(x for x in res["groups"] if x["hostname"] == "srv-x")
    assert "掃描收編" in g["origin_text"] and "批次納管自動補" in g["origin_text"]


def test_重複登記_標過不是重複的組不列_但回報被排除組數(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO duplicate_dismiss (hostname, ip, note, dismissed_by) VALUES ('dup-host', '10.99.3.1', 't', 'u')")
    conn.commit()
    res = host_lookup.duplicates(conn)
    assert [g["hostname"] for g in res["groups"]] == ["Demo-Uni01.corp.example"] or len(res["groups"]) == 1
    assert res["dismissed_groups"] == 1
    assert [g["key"] for g in res["groups"]] == [k for k, _ in vip_view.duplicate_candidate_groups(conn)]


def test_判不出是哪一台的各自一台不成組(tmp_path):
    conn = _conn(tmp_path)
    for sn in ("HW-Z1", "HW-Z2"):
        db.insert_hardware(conn, asset_serial=sn, hostname="", ip="0.0.0.0", asset_status="使用中")
    conn.commit()
    res = host_lookup.duplicates(conn)
    assert not any(set(g["serials"]) & {"HW-Z1", "HW-Z2"} for g in res["groups"])


def test_權限登記_批次查詢viewer可用_重複清單是GET(tmp_path):
    import access_policy as ap
    assert ap.level_for("POST", "/api/v2/hosts/batch-lookup") == "viewer"
    assert ap.level_for("GET", "/api/v2/hosts/duplicates") == "viewer"
    # 沒有任何「釋放／刪除」類端點被註冊到 v2 hosts 底下
    assert not [r for r in ap.all_registered_writes() if r[1].startswith("/api/v2/hosts") and r[0] in ("DELETE", "PUT")]
