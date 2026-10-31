"""2.x 第一批（2026-10-27 藍圖定稿）：2.1 主機查詢（頁籤筆數＝列數、逐筆登記）、4.1 事件應變（計畫停機／異常事件、
通報聯絡名單與通知信草稿）、全站選單照藍圖、正式用語（決策 12 禁用口語）。全部假資料。"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import incident  # noqa: E402
import v2_hosts  # noqa: E402
import v2_incident  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn):
    conn.execute("INSERT OR REPLACE INTO business_system (api_id, name, sys_class) VALUES ('N-099', '假交易系統', 'S1-第一類系統軟體（核心）')")
    conn.execute("INSERT OR REPLACE INTO business_system (api_id, name, sys_class) VALUES ('N-100', '假報表系統', 'S3-一般')")
    db.insert_hardware(conn, asset_serial="A-1", hostname="aix01", ip="10.99.1.18", physical_location="01_甲機房",
                       rack_no="B2/12-15U", device_model="IBM S1024", os="AIX 7.2", api_id="N-099")
    db.insert_hardware(conn, asset_serial="A-1b", hostname="aix01", ip="10.99.1.18", physical_location="01_甲機房",
                       rack_no="B2/12-15U", device_model="IBM S1024", os="AIX 7.2", api_id="N-100")      # 同一台第二筆登記
    db.insert_hardware(conn, asset_serial="W-1", hostname="win01", ip="10.99.2.1", rack_no="VM", device_model="(VM)",
                       os="Windows Server 2019", api_id="N-100", is_vm="Y")
    db.insert_hardware(conn, asset_serial="DYN-10.99.3.3", hostname="dy01", ip="10.99.3.3", os="Red Hat Enterprise Linux 9")
    conn.commit()


def test_主機查詢_一台一列_頁籤筆數等於列數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        lst = v2_hosts.listing(conn, "all")
        assert [t["label"] for t in lst["tabs"]] == ["全部登記台數", "實體設備", "虛擬機", "管理口", "核心系統", "逾期未偵測", "未偵測到（僅 CIA 登記）", "未登錄 CIA", "已納管"]
        names = {r["name"]: r for r in lst["rows"]}
        assert set(names) == {"aix01", "win01", "dy01"} and names["aix01"]["records"] == 2      # 兩筆登記合成一台
        full = {r["name"]: r for r in v2_hosts.machines(conn)}     # 2.006.4：清單只回畫面用到的欄位，語意欄位看 machines()
        assert full["aix01"]["core"] and names["aix01"]["room"] == "甲機房" and names["aix01"]["rack"] == "B2"
        assert full["win01"]["badge"]["vm"] and not names["dy01"]["has_cia"]
        for t in lst["tabs"]:
            assert t["count"] == len(v2_hosts.listing(conn, t["key"])["rows"]), t
        assert [r["name"] for r in v2_hosts.listing(conn, "core")["rows"]] == ["aix01"]
        assert [r["name"] for r in v2_hosts.listing(conn, "no_cia")["rows"]] == ["dy01"]
        per = v2_hosts.listing(conn, "all", per_record=True)
        assert len(per["records"]) == 4 and {r["source"] for r in per["records"]} == {"CIA", "DY 動態資產"}
        assert [r["name"] for r in v2_hosts.listing(conn, "all", q="10.99.2")["rows"]] == ["win01"]
        with pytest.raises(ValueError):
            v2_hosts.listing(conn, "nope")
        import verify_numbers as vn
        vn.RESULTS.clear()
        vn.check_v2_hosts(conn)
        got = [r for r in vn.RESULTS if r["page"] == "2.x 主機查詢"]
        assert len(got) == 12 and all(r["ok"] for r in got), got
        cons = [r for r in vn.RESULTS if r["page"] == "2.x 一致性"]
        assert len(cons) == 2 and all(r["ok"] for r in cons), cons
        # 實體設備／虛擬機／管理口＝對帳橋（同一個詞同一個數字）
        import count_bridge
        cbo = count_bridge.compute(conn)
        trial = {t["key"]: t["value"] for t in cbo["summary"]["trial"]}
        tabs = {t["key"]: t["count"] for t in lst["tabs"]}
        assert (tabs["physical"], tabs["vm"], tabs["mgmt"]) == (trial["physical"], count_bridge.measure(cbo["items"], "vm"), trial["mgmt"])
        assert {r["name"] for r in v2_hosts.listing(conn, "vm")["rows"]} == {"win01"}
        assert "aix01" in {r["name"] for r in v2_hosts.listing(conn, "physical")["rows"]}
        # 同一個詞同一個意思：主機履歷的偵測狀態＝2.1 這一列的偵測狀態（都是 v2_views.status_of）
        import v2_views
        for r in lst["rows"]:
            assert v2_views.machine(conn, r["key"])["status"]["label"] == r["status_label"], r["name"]
        assert all(r["status"]["key"] == "stale" for r in v2_hosts.listing(conn, "stale")["rows"])
        assert all(r["status_label"] == "未偵測到" for r in v2_hosts.listing(conn, "missing")["rows"])
    finally:
        conn.close()


def test_事件應變_先選類別_通報聯絡與通知信草稿(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        with pytest.raises(ValueError, match="計畫停機"):
            incident.create_event(conn, "主機", "x", None, None, "u", "other", "formal")
        old = incident.create_event(conn, "電力", "舊式呼叫", None, None, "u", mode="formal")                     # 沒給類別＝異常事件
        assert old["category"] == "incident"
        ev = incident.create_event(conn, "主機", "aix01 停機", "2026-10-30 22:00", "韌體更新", "u", "planned", "formal")
        assert ev["category"] == "planned"
        assert {e["id"]: e["category"] for e in incident.list_events(conn)}[ev["id"]] == "planned"
        import rack_layout
        k = next(m["key"] for m in rack_layout.machines(conn) if m["asset_serial"] in ("A-1", "A-1b"))
        incident.set_scope(conn, ev["id"], {"machines": [k]}, "u")
        n = v2_incident.notify(conn, ev["id"])
        assert {c["ref"] for c in n["contacts"]} == {"N-099", "N-100"}
        assert n["mail"]["subject"].startswith("【計畫停機通知】aix01 停機")
        assert "預計於 2026-10-30 22:00" in n["mail"]["body"] and "N-099 假交易系統" in n["mail"]["body"]
        assert "請確認內容後再寄出" in n["mail"]["body"]
        assert set(n["no_phone"]) == {"N-099", "N-100"}
        csv = v2_incident.contacts_csv(conn, ev["id"])
        assert csv.startswith("﻿項次,類別,代號") and "N-099" in csv
        m = v2_incident.mail_draft(incident.event_view(conn, old["id"]))
        assert m["subject"].startswith("【異常事件通知】") and "（範圍內沒有對到業務系統的主機）" in m["body"]
    finally:
        conn.close()


def test_全站選單照藍圖():
    src = (FE / "composables" / "useNavV2.ts").read_text(encoding="utf-8")
    groups = re.findall(r"\{ no: '([^']+)', label: '([^']+)', items:", src)
    assert groups == [("1", "今日概況"), ("2", "主機"), ("3", "業務系統"), ("4", "事件應變"), ("5", "機房管理"), ("6", "納管作業"),
                      ("7", "盤點作業"), ("8", "合規稽核"), ("9", "資料治理"), ("10", "報表"), ("⚙", "系統設定")]
    items = re.findall(r"\{ no: '([^']+)', label: '([^']+)', to: '([^']+)'", src)
    want = {"1.1": "今日概況", "1.2": "異動與異常", "1.3": "設備風險", "2.1": "主機查詢", "3.1": "業務系統", "3.2": "架構圖",
            "4.1": "事件應變", "5.1": "機房平面圖", "5.2": "儲存容量", "6.1": "納管作業", "6.2": "設備收集", "7.1": "機櫃盤點",
            "7.2": "帳號盤點", "7.3": "軟體盤點", "7.4": "服務盤點", "8.1": "弱點管理", "8.2": "組態與上線檢核", "8.3": "生命週期",
            "9.1": "待補資料", "9.2": "數量對帳", "10.1": "月報", "10.2": "分布統計", "⚙": "系統設定",
            "🌙": "夜間速查"}       # 釘在選單最上面、不屬於任何一組（V2_PINNED，2.010.1）
    assert {no: lab for no, lab, _ in items} == want
    assert not any("主機履歷" in lab or "單一事件" in lab or "系統履歷" in lab for _, lab, _ in items)   # 不放選單
    built = {to for _, _, to in items if to.startswith("/v2")}
    assert built == {"/v2", "/v2/changes", "/v2/risk", "/v2/hosts", "/v2/incident",
                     # 第二批（2.004.0）：5.1、5.2、7.1～7.4
                     "/v2/datacenter", "/v2/storage", "/v2/inventory/rack", "/v2/inventory/account",
                     "/v2/inventory/software", "/v2/inventory/service",
                     # 第三批（2.005.0）：6.1、6.2、9.1（9.3 主機分類 2026-10-28 併入 9.1 頁籤）；9.2 數量對帳 2.007.12
                     "/v2/onboard", "/v2/collect", "/v2/datafix", "/v2/reconcile",
                     # 第四批（2.007.0）：3.1、3.2、8.1～8.3、10.1、10.2、⚙
                     "/v2/systems", "/v2/arch", "/v2/patch", "/v2/audit", "/v2/eos", "/v2/report", "/v2/dist", "/v2/settings",
                     # 釘選項（2.010.1）：夜間速查
                     "/v2/night"}
    for no, lab, to in items:                                   # 還沒做新版的一律標舊版
        line = next(ln for ln in src.splitlines() if f"no: '{no}', label: '{lab}', to:" in ln)
        assert ("old: true" in line) == (not to.startswith("/v2")), line
    for to in built:
        page = FE / "pages" / (to.lstrip("/") + ("/index.vue" if (FE / "pages" / to.lstrip("/")).is_dir() else ".vue"))
        assert page.exists(), to
        assert "layout: 'v2'" in page.read_text(encoding="utf-8"), to


BANNED = ["出事了", "要停機", "掛了", "還在嗎", "在線", "連得進去", "連不進去", "很久沒看到", "CIA 沒登記", "旁邊的機器",
          "重建用資料", "現在測一下", "不知道", "病歷"]


def test_正式用語_新版頁面不出現口語():
    files = list((FE / "pages" / "v2").rglob("*.vue")) + list((FE / "components" / "v2").rglob("*.vue")) + \
        [FE / "layouts" / "v2.vue", FE / "composables" / "useNavV2.ts"]
    hits = []
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for w in BANNED:
                if w in line:
                    hits.append(f"{f.relative_to(FE)}:{i} 「{w}」")
    assert not hits, hits


def test_正式用語_後端產生的頁籤與原因(tmp_path):
    import v2_sections as vs
    import v2_views as v
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        texts = [lab for _, lab in vs.GROUPS]
        for q in ("aix01", "win01", "dy01"):
            s = vs.summary(conn, q)
            texts += [x["label"] for x in s["sections"]] + [x["unknown"] or "" for x in s["sections"]] + [s["impact_total"]["text"]]
            m = v.machine(conn, q)
            texts += [m["conclusion"], m["status"]["label"], m["badge"]["label"]]
        bad = [t for t in texts for w in BANNED if w in t]
        assert not bad, bad
    finally:
        conn.close()


def test_主機查詢_伺服器端分頁_固定項次_資料不完整排最後(tmp_path):
    """找碴 C6／C7／C8／C19（2.006.4）：分頁不再「先列 1000 筆」；項次是全部清單裡的固定編號，
    篩選、換頁、換排序都不變；括號名稱、名稱就是 IP 的列排在最後；只回畫面用到的欄位。"""
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        db.insert_hardware(conn, asset_serial="J-1", hostname="(某某管理主機-VIP)", ip="10.99.9.1")
        db.insert_hardware(conn, asset_serial="J-2", hostname="10.99.9.2", ip="10.99.9.2")
        conn.commit()
        full = v2_hosts.listing(conn, "all")
        names = [r["name"] for r in full["rows"]]
        assert names[-2:] == ["(某某管理主機-VIP)", "10.99.9.2"] or set(names[-2:]) == {"(某某管理主機-VIP)", "10.99.9.2"}
        no = {r["name"]: r["no"] for r in full["rows"]}
        assert sorted(no.values()) == list(range(1, len(names) + 1))
        p1 = v2_hosts.listing(conn, "all", page=1, size=2)
        p2 = v2_hosts.listing(conn, "all", page=2, size=2)
        assert p1["total"] == p2["total"] == len(names) and len(p1["rows"]) == 2
        assert [r["no"] for r in p1["rows"] + p2["rows"]] == [1, 2, 3, 4]
        # 篩選後同一台同一個號碼
        f = v2_hosts.listing(conn, "all", q="win01")
        assert [r["no"] for r in f["rows"]] == [no["win01"]]
        # 依 IP 反向排序（伺服器端），空值排最後
        ips = [r["ip"] for r in v2_hosts.listing(conn, "all", sort="ip", desc=True)["rows"]]
        assert ips == sorted(ips, key=lambda s: [int(x) for x in s.split(".")], reverse=True)
        assert set(full["rows"][0]) <= set(v2_hosts.LIST_FIELDS)
    finally:
        conn.close()


def test_分布統計_加總等於全部_每格等於下鑽(tmp_path):
    """10.2（2.007.0）：verify_numbers check_v2_dist 全綠；所在地／作業系統／作業系統版本／環境別加總＝2.1 全部登記台數。"""
    import v2_dist
    import verify_numbers as vn

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        d = v2_dist.summary(conn)
        assert d["total"] == 3 and all(sum(r["all"] for r in rows) == 3 for rows in d["dims"].values())
        assert "dc_rooms" in d
        vn.RESULTS.clear()
        vn.check_v2_dist(conn)
        got = [r for r in vn.RESULTS if r["page"] == "2.x 分布統計"]
        assert len(got) == 19 and all(r["ok"] for r in got), got       # 2.007.51 加了「作業系統版本」維度（每維度 4 項）
    finally:
        conn.close()


def test_分布統計_環境別運作中灌水會被抓到(tmp_path, monkeypatch):
    """2.007.3 審查：獨立錨點要抓得到「環境別的運作中加倍」——運作中加總對的是存在可信度計入台數，不是 v2_hosts。"""
    from datetime import datetime

    import v2_dist
    import verify_numbers as vn

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE hardware SET collect_ok = 1, collect_checked_at = ? WHERE hostname IN ('aix01', 'win01')", (now,))
        conn.commit()
        assert sum(r["online"] for r in v2_dist.summary(conn)["dims"]["env"]) >= 1, "種子要有運作中的主機"
        vn.RESULTS.clear()
        vn.check_v2_dist(conn)
        assert all(r["ok"] for r in vn.RESULTS if r["page"] == "2.x 分布統計")
        real = v2_dist.summary

        def doubled(c):
            d = real(c)
            for r in d["dims"]["env"]:
                r["online"] *= 2
            return d
        monkeypatch.setattr(v2_dist, "summary", doubled)
        vn.RESULTS.clear()
        vn.check_v2_dist(conn)
        bad = [r["name"] for r in vn.RESULTS if r["page"] == "2.x 分布統計" and not r["ok"]]
        assert any("env：運作中加總" in n for n in bad), bad
    finally:
        conn.close()


def test_虛擬機與運作中_全站一個定義_種子含邊界(tmp_path):
    """2.007.4 審查：種子刻意放「對帳橋判虛擬機、類型標記判實體」（只有機櫃欄寫 VM）與「逾期未偵測」各一台——
    虛擬機改回類型標記定義、運作中把逾期也算進去、某一格運作中灌水，都要紅。"""
    from datetime import datetime

    import v2_dist
    import v2_views
    import verify_numbers as vn

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        db.insert_hardware(conn, asset_serial="V-9", hostname="vmrack09", ip="10.99.7.1", rack_no="VM",
                           os="Red Hat Enterprise Linux 9", environment="正式")       # 只有機櫃欄寫 VM
        db.insert_hardware(conn, asset_serial="S-9", hostname="old09", ip="10.99.7.9", os="Red Hat Enterprise Linux 9",
                           environment="正式")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE hardware SET collect_ok = 1, collect_checked_at = ? WHERE hostname IN ('aix01', 'vmrack09')", (now,))
        conn.execute("UPDATE hardware SET collect_ok = 1, collect_checked_at = '2026-01-01 00:00:00' WHERE asset_serial = 'S-9'")
        conn.commit()
        # 對帳橋說虛擬機、類型標記說實體 → 全站一律虛擬機（主機履歷、搜尋、2.1、10.2）
        assert v2_views.type_badge(conn.execute("SELECT * FROM hardware WHERE asset_serial='V-9'").fetchone())["vm"] is False
        m = v2_views.machine(conn, "vmrack09")
        assert m["badge"]["vm"] is True
        assert v2_views.search(conn, "vmrack09")["machines"][0]["badge"]["vm"] is True
        h = {r["name"]: r for r in v2_hosts.machines(conn)}
        assert h["vmrack09"]["badge"]["vm"] is True and h["old09"]["status"]["key"] == "stale"
        env = {r["value"]: r for r in v2_dist.summary(conn)["dims"]["env"]}
        assert env["正式"]["vm"] == 1 and env["正式"]["online"] == 1      # V-9 虛擬機且運作中；S-9 逾期未偵測不算運作中
        vn.RESULTS.clear()
        vn.check_v2_dist(conn)
        got = [r for r in vn.RESULTS if r["page"] == "2.x 分布統計"]
        assert got and all(r["ok"] for r in got), [r for r in got if not r["ok"]]
    finally:
        conn.close()
