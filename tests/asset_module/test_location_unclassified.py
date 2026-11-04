"""Codex #2 第一階段：認不出的地點不再靜默歸「分公司」。

守的規矩：
1. 空值回「未填」；命中設定檔的回該組（含「分公司」）；認不出的回「（未歸類：原值）」。
2. 未歸類不併入分公司、也不當已確認分點；彙總收成一桶「（未歸類）」，可下鑽，加總＝下鑽。
3. 原始 physical_location 不被改寫。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state  # noqa: E402
import system_report  # noqa: E402


def test_group_location_各種原值():
    g = manage_state.group_location
    assert g(None) == "未填" and g("") == "未填" and g("   ") == "未填"
    assert g("01_板橋機房") == "板橋"
    assert g("敦南機房") == "敦南"
    assert g("內湖IDC") == "內湖"
    assert g("台中分公司") == "分公司"           # 命中的仍歸分公司
    assert g("台中") == "（未歸類：台中）"        # 認不出的：保留原值、不是分公司
    assert g("???") == "（未歸類：???）"           # 無法解析
    assert g("  高雄機房 ") == "（未歸類：高雄機房）"
    assert manage_state.is_unclassified_location(g("台中"))
    assert not manage_state.is_unclassified_location("分公司")
    assert not manage_state.is_unclassified_location("未填")


def test_bucket與matches():
    b, m = manage_state.location_bucket, manage_state.location_matches
    assert b("台中") == manage_state.LOC_UNCLASSIFIED == "（未歸類）"
    assert b("板橋") == "板橋" and b("") == "未填" and b("北分公司") == "分公司"
    assert m("台中", "（未歸類）") and m("台中", "（未歸類：台中）")
    assert not m("台中", "分公司") and not m("台中", "（未歸類：高雄）")
    assert m("北分公司", "分公司") and not m("北分公司", "（未歸類）")
    assert not m("板橋", None)


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn):
    rows = [("U-1", "板橋機房"), ("U-2", "台中"), ("U-3", "台中"), ("U-4", "高雄機房"),
            ("U-5", "北分公司"), ("U-6", ""), ("U-7", "內湖機房")]
    for serial, loc in rows:
        db.insert_hardware(conn, asset_serial=serial, hostname=serial.lower(),
                           ip="10.99.0." + serial[-1], os="Windows Server 2019",
                           physical_location=loc, environment="正式", is_vm="0",
                           asset_status="在用")


def test_composition_未歸類獨立一桶_加總等於母體_原值不改(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        c = manage_state.composition(conn)
        bl = c["by_location"]
        assert bl.get("（未歸類）") == 3                 # 台中×2 + 高雄
        assert bl.get("分公司") == 1                     # 只有命中「分公司」的
        assert bl.get("未填") == 1
        assert sum(bl.values()) == c["total"] == 7
        for loc, envmap in c["by_location_env"].items():
            assert sum(envmap.values()) == bl[loc]
        got = {r["asset_serial"]: r["physical_location"]
               for r in conn.execute("SELECT asset_serial, physical_location FROM hardware")}
        assert got["U-2"] == "台中" and got["U-6"] == ""     # 原始值沒被改寫
    finally:
        conn.close()


def test_頁A未歸類獨立列_總數加總_可下鑽(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        # report_baseline 需要 CIA 基準；這裡以所有 hardware 當基準，隔離測分組邏輯
        monkeypatch.setattr(system_report, "report_baseline",
                            lambda c: [{"asset_serial": r["asset_serial"], "hostname": r["asset_serial"]}
                                       for r in c.execute("SELECT asset_serial FROM hardware")])
        d = system_report.physical_distribution(conn)
        assert [b["name"] for b in d["branches"]] == ["北分公司"]
        un = {b["name"]: b["count"] for b in d["unclassified"]}
        assert un == {"台中": 2, "高雄機房": 1}               # 未填不在未歸類裡
        assert d["unclassified_total"] == 3 and d["unfilled_total"] == 1
        assert d["total_physical"] == sum(r["total"] for r in d["rooms"]) + 1 + 3 + 1 == 7
        # 下鑽：「（未歸類）」只含有填但認不出的；「未填」獨立一桶，兩者不重複計數
        assert len(system_report.drill_physical(conn, room="（未歸類）")) == 3
        assert len(system_report.drill_physical(conn, room="未填")) == 1
        assert len(system_report.drill_physical(conn, branch="台中")) == 2
        assert len(system_report.drill_physical(conn, branch="（未填機房）")) == 1
        assert len(system_report.drill_physical(conn, room="分公司")) == 1
        assert sum(len(system_report.drill_physical(conn, branch=b["name"]))
                   for b in d["unclassified"]) == d["unclassified_total"]
    finally:
        conn.close()


def _hw_all(monkeypatch):
    monkeypatch.setattr(system_report, "report_baseline",
                        lambda c: [{"asset_serial": r["asset_serial"], "hostname": r["asset_serial"]}
                                   for r in c.execute("SELECT asset_serial FROM hardware")])


def test_未填永遠獨立一桶_各頁一致(tmp_path, monkeypatch):
    """同一批空值在 composition／頁A／頁B／下鑽都落在「未填」，不進「（未歸類）」、不重複計數。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _hw_all(monkeypatch)
        c = manage_state.composition(conn)
        assert c["by_location"]["未填"] == 1 and c["by_location"]["（未歸類）"] == 3
        d = system_report.physical_distribution(conn)
        assert d["unfilled_total"] == c["by_location"]["未填"]
        assert d["unclassified_total"] == c["by_location"]["（未歸類）"]
        ov = system_report.system_overview(conn)
        rooms = {r["room"]: r["total"] for r in ov["rooms"]}
        assert rooms["未填"] == 1 and rooms["（未歸類）"] == 3 and rooms["分公司"] == 1
        assert sum(rooms.values()) == ov["total"] == 7
        d1 = system_report.drill_system_overview(conn, room="未填")
        d2 = system_report.drill_system_overview(conn, room="（未歸類）")
        assert len(d1) == 1 and len(d2) == 3
        assert not {x["asset_serial"] for x in d1} & {x["asset_serial"] for x in d2}
    finally:
        conn.close()


def test_設定檔新增群組_各頁自動出現同名群組_不進未歸類(tmp_path, monkeypatch):
    cfg = dict(manage_state._location_config())
    cfg["groups"] = list(cfg["groups"]) + [{"name": "青埔", "match": ["青埔"]}]
    monkeypatch.setattr(manage_state, "_LOC_CFG", cfg)
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        db.insert_hardware(conn, asset_serial="U-8", hostname="u-8", ip="10.99.0.8",
                           os="Windows Server 2019", physical_location="青埔機房", environment="正式",
                           is_vm="0", asset_status="在用")
        _hw_all(monkeypatch)
        assert manage_state.group_location("青埔機房") == "青埔"
        c = manage_state.composition(conn)
        assert c["by_location"]["青埔"] == 1 and c["by_location"]["（未歸類）"] == 3
        d = system_report.physical_distribution(conn)
        assert {r["room"]: r["total"] for r in d["rooms"]}["青埔"] == 1
        assert d["unclassified_total"] == 3 and d["total_physical"] == 8
        assert len(system_report.drill_physical(conn, room="青埔")) == 1
        ov = system_report.system_overview(conn)
        assert {r["room"]: r["total"] for r in ov["rooms"]}["青埔"] == 1
        assert "青埔" in system_report._room_names()
    finally:
        conn.close()


def _api_conn(tmp_path):
    return _conn(tmp_path)


def test_網段反推_未歸類與未填不建網段_列待確認_一般分點與分公司照建(tmp_path):
    from routers import collect_scan

    conn = _conn(tmp_path)
    try:
        rows = [("S-1", "10.99.1.1", "板橋機房"), ("S-2", "10.99.2.1", "北分公司"),
                ("S-3", "10.99.3.1", "台中"), ("S-4", "10.99.4.1", ""), ("S-5", "10.99.5.1", "內湖機房")]
        for serial, ip, loc in rows:
            db.insert_hardware(conn, asset_serial=serial, hostname=serial.lower(), ip=ip,
                               os="Windows Server 2019", physical_location=loc, environment="正式",
                               is_vm="0", asset_status="在用")
        sug = collect_scan.suggest_segments(fmt="json", session=None, conn=conn)["segments"]
        flag = {s["cidr"]: s["location_pending"] for s in sug}
        assert flag == {"10.99.1.0/24": "", "10.99.2.0/24": "", "10.99.3.0/24": "地點未歸類",
                        "10.99.4.0/24": "地點未填", "10.99.5.0/24": ""}
        body = collect_scan.SegmentBatchBody(cidrs=list(flag))
        out = collect_scan.batch_create_segments(body, session=None, conn=conn)
        assert {c["cidr"] for c in out["created"]} == {"10.99.1.0/24", "10.99.2.0/24", "10.99.5.0/24"}
        assert {(p["cidr"], p["reason"]) for p in out["pending"]} == {
            ("10.99.3.0/24", "地點未歸類"), ("10.99.4.0/24", "地點未填")}
        names = [r["name"] for r in conn.execute("SELECT name FROM connections")]
        assert len(names) == 3 and not any("未歸類" in n or "未填" in n for n in names)
        # 重跑：已建的略過、待確認的仍不建
        out2 = collect_scan.batch_create_segments(body, session=None, conn=conn)
        assert not out2["created"] and len(out2["pending"]) == 2
    finally:
        conn.close()


def test_verify_numbers_機房不變式在隔離資料上全綠(tmp_path, monkeypatch):
    import verify_numbers as vn

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        _hw_all(monkeypatch)
        vn.FAILS = 0
        vn.RESULTS.clear()
        vn.check_page_a(conn)
        vn.check_composition(conn)
        loc = [r for r in vn.RESULTS if any(k in r["name"] for k in ("機房", "未歸類", "未填", "網段", "total_physical"))]
        assert len(loc) >= 8
        assert [r for r in loc if r["ok"] is False] == []
    finally:
        conn.close()


def test_分公司的板橋分點不被板橋機房吃掉():
    """正式機實測：『分公司-板橋』8 台原本被『板橋』先命中算成板橋機房（Codex／2026-10-31 發現）。
    分公司組必須排在最前；真正的板橋機房（01_板橋機房）仍歸板橋。"""
    import manage_state as ms
    assert ms.group_location("分公司-板橋") == "分公司"
    assert ms.group_location("分公司-忠孝") == "分公司"
    assert ms.group_location("01_板橋機房") == "板橋"
    assert ms.group_location("敦南機房") == "敦南"
    assert ms.group_location("內湖機房") == "內湖"


def test_X分公司_歸分公司_刻意行為():
    """刻意行為（Codex 審查確認）：『板橋分公司』是分公司，不是板橋機房。
    分公司組排最前，所以含『分公司』的地名一律先歸分公司；真正的板橋機房（01_板橋機房）仍歸板橋。"""
    import manage_state as ms
    assert ms.group_location("板橋分公司") == "分公司"
    assert ms.group_location("01_板橋機房") == "板橋"
