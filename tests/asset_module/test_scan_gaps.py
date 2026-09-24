"""掃不到的網段清單（2026-09-20）。

使用者限制：公司那台只連得到非正式區，非正式區也有防火牆沒申請到。
所以「掃不到」必須分得出來是「沒跑到」「掃失敗」還是「掃通了沒人回應」——
最後一種加上「那段有登記資產」，才是申請防火牆的依據。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import scan_gaps as sg  # noqa: E402
import scan_scope  # noqa: E402

T1 = "2026-09-19 01:00:00"
T2 = "2026-09-20 01:00:00"      # 最近一次


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    scan_scope._ensure(c)
    segs = [
        ("10.99.1.0/24", "內湖", "正式", "SERVER"),     # 掃通、有回應 → 正常
        ("10.99.2.0/24", "內湖", "正式", "SERVER"),     # 掃通、整段沒回應（有登記資產 → 防火牆第一順位）
        ("10.99.3.0/24", "板橋", "測試", "SERVER"),     # 從沒掃過
        ("10.99.4.0/24", "板橋", "測試", "SERVER"),     # 最近一次掃失敗
        ("10.99.5.0/24", "板橋", "測試", "SERVER"),     # 以前掃通、最近一次沒掃到
    ]
    for cidr, loc, env, cat in segs:
        c.execute("INSERT INTO network_segment (cidr, raw_cidr, location, environment, category, imported_at) "
                  "VALUES (?,?,?,?,?,?)", (cidr, cidr, loc, env, cat, T1))
        # 逐段手選模式：直接把段放進掃描來源
        scan_scope.apply_scope(c, [r[0] for r in c.execute(
            "SELECT id FROM network_segment WHERE cidr = ?", (cidr,))])
    # 涵蓋紀錄
    for cidr, when, ok in (("10.99.1.0/24", T2, 1), ("10.99.2.0/24", T2, 1),
                           ("10.99.4.0/24", T2, 0), ("10.99.5.0/24", T1, 1)):
        c.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES (?,?,?,?)",
                  (when, cidr, ok, when))
    # 掃到的主機：只有 1 號段有回應
    c.execute("INSERT INTO scan_history (scan_time, ip, segment, scan_ok) VALUES (?,?,?,1)",
              (T2, "10.99.1.10", "10.99.1.0/24"))
    # 2 號段有登記資產，卻一台都沒掃到
    db.insert_hardware(c, asset_serial="H-1", hostname="h1", ip="10.99.2.10", asset_status="使用中")
    db.insert_hardware(c, asset_serial="H-2", hostname="h2", ip="10.99.2.11", asset_status="使用中")
    c.commit()
    return c


def _by_cidr(rep):
    return {r["cidr"]: r for r in rep["rows"]}


def test_四種掃不到分得開(tmp_path):
    r = _by_cidr(sg.report(_conn(tmp_path)))
    assert r["10.99.1.0/24"]["kind"] == sg.OK
    assert r["10.99.2.0/24"]["kind"] == sg.NO_REPLY, "掃通了整段沒回應——多半是防火牆"
    assert r["10.99.3.0/24"]["kind"] == sg.NEVER, "一次涵蓋紀錄都沒有＝沒跑到，不是機器不在"
    assert r["10.99.4.0/24"]["kind"] == sg.FAILED, "掃描失敗要獨立一類"
    assert r["10.99.5.0/24"]["kind"] == sg.STALE


def test_申請防火牆第一順位_掃不到又有登記資產(tmp_path):
    rep = sg.report(_conn(tmp_path))
    assert rep["summary"]["firewall_first"] == 1
    assert rep["rows"][0]["cidr"] == "10.99.2.0/24", "最該處理的排最上面"
    assert rep["rows"][0]["registered_machines"] == 2


def test_每段都附依據_0要分得出沒掃還是沒東西(tmp_path):
    r = _by_cidr(sg.report(_conn(tmp_path)))
    never, noreply = r["10.99.3.0/24"], r["10.99.2.0/24"]
    assert never["scanned_ok_times"] == 0 and never["last_ok_scan"] is None, "沒掃過：沒有掃通紀錄"
    assert noreply["scanned_ok_times"] == 1 and noreply["last_ok_scan"] == T2, "沒回應：有掃通紀錄佐證"
    assert never["hosts_found_ever"] == 0 and noreply["hosts_found_ever"] == 0
    assert all(x["advice"] for x in r.values()), "每一段都要寫下一步"


def test_匯出CSV欄位齊全(tmp_path):
    rep = sg.report(_conn(tmp_path))
    csv = sg.to_csv(rep)
    head, *body = csv.splitlines()
    assert head.startswith("網段,機房,環境") and "這段登記台數" in head and "下一步" in head
    assert len(body) == len(rep["rows"])
    assert "10.99.2.0/24" in body[0]


def test_唯讀_重跑結果一樣(tmp_path):
    c = _conn(tmp_path)
    a = sg.report(c)
    b = sg.report(c)
    assert a["summary"] == b["summary"]


def test_預期不通的環境不進申請名單(tmp_path):
    """2026-09-20 使用者：「正式區本來就不會通，從 198.14 這台掃不到正式區是正常的」。"""
    c = _conn(tmp_path)
    # 沒設定時：不替人假設，正式那段照樣算缺口
    assert sg.report(c)["summary"]["firewall_first"] == 1
    sg.set_visible_environments(c, ["測試"])          # 這台只看得到測試區
    rep = sg.report(c)
    r = _by_cidr(rep)
    assert r["10.99.2.0/24"]["expected_unreachable"] is True, "正式區＝這台看不到"
    assert r["10.99.3.0/24"]["expected_unreachable"] is False, "測試區＝該通"
    assert rep["summary"]["firewall_first"] == 0, "預期不通的不可以排進申請名單"
    assert rep["summary"]["expected_unreachable_segments"] == 1
    assert rep["summary"]["expected_unreachable_machines"] == 2
    assert [x["expected_unreachable"] for x in rep["rows"]][-1] is True, "預期不通的排最後"
    assert "這台看得到嗎" in sg.to_csv(rep).splitlines()[0]
    assert sg.visible_environments(c) == ["測試"]


def test_搜尋與篩選_匯出要跟畫面一樣(tmp_path):
    """2026-09-20 使用者：「要搜尋 跟過濾 譬如 正式 測試 各地機房」。
    篩完再匯出，拿到的必須是篩過的那幾列——不然送出去的申請附件是錯的。"""
    rep = sg.report(_conn(tmp_path))
    rows = rep["rows"]
    assert len(sg.filter_rows(rows, environment="測試")) == 3
    assert len(sg.filter_rows(rows, location="內湖")) == 2
    assert [r["cidr"] for r in sg.filter_rows(rows, q="10.99.2")] == ["10.99.2.0/24"]
    assert len(sg.filter_rows(rows, q="板橋 測試")) == 3, "空白隔開＝每個詞都要命中"
    assert len(sg.filter_rows(rows, only_registered=True)) == 1
    assert len(sg.filter_rows(rows, kind=sg.NEVER)) == 1
    csv = sg.to_csv({**rep, "rows": sg.filter_rows(rows, environment="內湖")})
    assert len(csv.splitlines()) == 1, "沒有命中就只剩標題列（匯出＝所見即所得）"


def test_人工標註_標了就收起來且不算待辦(tmp_path):
    """2026-09-20 使用者：「是不是也是一個按鈕把它忽略掉，就代表這個網段目前沒在使用？」
    「平常這些都是不用顯示出來的」。"""
    c = _conn(tmp_path)
    before = sg.report(c)["summary"]
    sg.set_note(c, "10.99.3.0/24", sg.UNUSED, note="網路組先開好，裡面還沒有機器", by="tester")
    rep = sg.report(c)
    r = _by_cidr(rep)["10.99.3.0/24"]
    assert r["note_status"] == sg.UNUSED and r["note_label"] == "目前沒在使用"
    assert r["hidden_by_note"] is True, "標了就從畫面收起來"
    assert r["note"] and r["note_by"] == "tester" and r["note_at"], "誰標的、何時、為什麼都要留著"
    assert r["kind"] == sg.NEVER, "標註不可以覆蓋掃描事實"
    assert rep["summary"]["gap_segments"] == before["gap_segments"] - 1, "不再算成待辦"
    assert rep["summary"]["hidden_by_note_segments"] == 1
    assert [x["cidr"] for x in rep["rows"]][-1] == "10.99.3.0/24", "收起來的排最後"


def test_預計台數_掃到比預計少要看得出來(tmp_path):
    c = _conn(tmp_path)
    sg.set_note(c, "10.99.2.0/24", sg.PENDING, expected_hosts=5, note="申請中", by="tester")
    rep = sg.report(c)
    r = _by_cidr(rep)["10.99.2.0/24"]
    assert r["expected_hosts"] == 5 and r["hosts_found_last"] == 0
    assert rep["summary"]["short_of_expected"] == 1
    assert r["hidden_by_note"] is False, "「等開通」要留在畫面上追蹤"


def test_清除標註(tmp_path):
    c = _conn(tmp_path)
    sg.set_note(c, "10.99.3.0/24", sg.UNUSED, by="t")
    sg.set_note(c, "10.99.3.0/24", "", by="t")
    assert _by_cidr(sg.report(c))["10.99.3.0/24"]["note_status"] == ""
