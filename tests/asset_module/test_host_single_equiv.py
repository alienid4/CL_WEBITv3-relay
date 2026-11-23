"""主機履歷「單台版」計算（2.069.4）＝全機隊版：逐台、逐欄必須相等（D3：同一份判定，只有資料取得範圍不同）。

主機履歷頁（summary／profile）原本為了取一台，要先把整個機隊算一遍（正式機約 6 千台，8 秒）。單台版只為這一台準備資料，
但判定規則是同一份程式（manage_state._collect_items／health_check._evaluate_rows／collected_os._measured_map／
vip_view.duplicate_candidate_groups／v2_hosts 同一批欄位函式）。這裡拿「全機隊版」當標準答案（它們都還在，沒改行為），
對假資料庫裡**每一台**逐欄比對：多登記、退役與現役同名、只有退役那筆收集成功、虛擬機、ESXi、疑似重複、無資料、台數口徑兩種模式。
全部假資料（10.99.x）。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collected_os  # noqa: E402
import data_coverage  # noqa: E402
import db  # noqa: E402
import existence  # noqa: E402
import health_check  # noqa: E402
import manage_state  # noqa: E402
import machine_identity  # noqa: E402
import oncall  # noqa: E402
import v2_hosts  # noqa: E402
import v2_sections as vs  # noqa: E402
import v2_views  # noqa: E402
import vip_view  # noqa: E402

NOW = "2099-01-01 00:00:00"      # 比對用；collect_checked_at 用「現在」附近的值，下面各自設


def _hw(conn, serial, hostname, ip, **kw):
    fields = {"asset_serial": serial, "hostname": hostname, "ip": ip, "physical_location": "01_甲機房", "rack_no": "B2/10-11U",
              "device_model": "Dell R750", "os": "Red Hat Enterprise Linux 9", "api_id": "N-901", "asset_status": "使用中",
              "asset_name": f"系統-{serial}", "asset_purpose": "測試"}
    fields.update(kw)
    db.insert_hardware(conn, **fields)


@pytest.fixture(params=["hostname", "legacy"])
def conn(tmp_path, request):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    c.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('machine_key_mode', ?)", (request.param,))
    c.commit()
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 多登記：同一台三筆（兩個 IP），其中第二筆收集成功（整台收集成功的規則）
    _hw(c, "A-1", "multi01", "10.99.1.1", api_id="N-901")
    _hw(c, "A-2", "multi01", "10.99.1.1", api_id="N-902", collect_ok=1, collect_checked_at=now, os="Red Hat Enterprise Linux 9.2")
    _hw(c, "A-3", "multi01", "10.99.1.2", api_id="N-901")
    # 同名：現役一筆＋退役一筆（只有退役那筆收集成功＝整台連上過的規則不分生命週期）
    _hw(c, "B-1", "pair01", "10.99.2.1")
    _hw(c, "B-R", "pair01", "10.99.2.1", asset_status="報廢", collect_ok=1, collect_checked_at=now)
    # 沒有主機名（退回序號）、沒有 IP
    _hw(c, "C-1", None, None)
    _hw(c, "C-2", "noip01", None)
    # 虛擬機、ESXi、網路設備、AIX 收集成功
    _hw(c, "V-1", "vm01", "10.99.3.1", rack_no="VM", device_model="(VM)", is_vm="Y")
    _hw(c, "E-1", "esxi01", "10.99.4.1", os="VMware ESXi 7.0", device_model="Dell R650")
    _hw(c, "N-1", "sw01", "10.99.5.1", os="", device_model="Cisco C9300")
    _hw(c, "X-1", "aix01", "10.99.6.1", os="AIX 7.2", device_model="IBM S1024", collect_ok=1, collect_checked_at=now)
    # 疑似重複（兩筆內容完全一樣）、帳外（DYN-）
    _hw(c, "D-1", "dup01", "10.99.7.1", api_id="N-903", asset_purpose="同")
    _hw(c, "D-2", "dup01", "10.99.7.1", api_id="N-903", asset_purpose="同")
    _hw(c, "D-3", "dup02", "10.99.7.2", api_id="N-904", asset_purpose="同", asset_name="同名系統")      # 疑似真重複（asset_name 也相同才算，上面 dup01 只是同機分列）
    _hw(c, "D-4", "dup02", "10.99.7.2", api_id="N-904", asset_purpose="同", asset_name="同名系統")
    _hw(c, "DYN-10.99.8.1", "dyn01", "10.99.8.1")
    # 最近一次掃描：只有一部分活著（其餘＝失聯），另有一台沒登記的；讓「逐筆狀態」與體檢的「失聯」真的有差別
    for ip, hn in (("10.99.1.1", "multi01"), ("10.99.3.1", "vm01"), ("10.99.9.9", "stray01")):
        c.execute("INSERT INTO scan_history (scan_time, ip, hostname, scan_ok) VALUES (?, ?, ?, 1)", (now, ip, hn))
    c.commit()
    machine_identity.refresh(c)
    yield c
    c.close()


def _all_serials(conn):
    return [r[0] for r in conn.execute("SELECT asset_serial FROM hardware ORDER BY asset_serial")]


def test_單台撈同一台的全部登記_等於全表分組(conn):
    groups, _of = oncall._groups(conn)
    by_base: dict[str, set] = {}
    for k, g in groups.items():
        by_base.setdefault(k[4:] if k.startswith("ret:") else k, set()).update(r["asset_serial"] for r in g)
    for s in _all_serials(conn):
        got = {r["asset_serial"] for r in manage_state.same_base_key_rows(conn, s)}
        me_key = next(k for k, g in groups.items() if any(r["asset_serial"] == s for r in g))
        base = me_key[4:] if me_key.startswith("ret:") else me_key
        assert got == by_base[base], f"{s}：單台撈到 {sorted(got)}，全表分組是 {sorted(by_base[base])}"


def test_單台逐筆狀態_等於全機隊summarize的items(conn):
    fleet = {it["asset_serial"]: it["state"] for it in manage_state.summarize(conn)["items"] if it.get("asset_serial")}
    for s in _all_serials(conn):
        rows = manage_state.same_base_key_rows(conn, s)
        one = manage_state.item_states_for_rows(conn, rows)
        assert one[s] == fleet[s], f"{s}：單台 {one[s]} ≠ 全機隊 {fleet[s]}"
        for t, st in one.items():          # 同一台的其他登記也要一樣
            assert st == fleet[t], f"{s}→{t}：單台 {st} ≠ 全機隊 {fleet[t]}"


def test_單台體檢_等於全機隊evaluate_all(conn):
    allv = health_check.evaluate_all(conn)
    ids = {r[0]: r[1] for r in conn.execute("SELECT asset_serial, id FROM hardware")}
    for s in _all_serials(conn):
        assert health_check.evaluate_one(conn, s) == allv.get(ids[s]), f"{s} 體檢結果不同"


def test_單台實測OS_等於全機隊measured_map(conn):
    fleet = collected_os.measured_map(conn)
    for s in _all_serials(conn):
        rows = manage_state.same_base_key_rows(conn, s)
        one = collected_os.measured_for_rows(conn, rows)
        assert one.get(s) == fleet.get(s), f"{s} 實測 OS 不同"


def test_單台重複登記分組_等於全表同key的組(conn):
    full = {k: sorted(r["asset_serial"] for r in g) for k, g in vip_view.duplicate_candidate_groups(conn)}
    for s in _all_serials(conn):
        rows = manage_state.same_base_key_rows(conn, s)
        one = {k: sorted(r["asset_serial"] for r in g) for k, g in vip_view.duplicate_candidate_groups(conn, rows)}
        for k, v in one.items():
            assert full.get(k) == v, f"{s}：組 {k} 單台 {v} ≠ 全表 {full.get(k)}"
        # 全表有、而且屬於這一台的組，單台版也要有（不可漏）
        mine = {r["asset_serial"] for r in rows}
        for k, v in full.items():
            if mine & set(v):
                assert one.get(k) == v, f"{s}：全表有組 {k}，單台版漏了"


def test_精簡列欄位_等於全機隊列的同名欄位(conn):
    fleet = {m["key"]: m for m in v2_hosts.machines(conn)}
    groups, _of = oncall._groups(conn)
    for k, g in groups.items():
        if k.startswith("ret:"):
            continue
        ex_m = existence.for_serial(conn, g[0]["asset_serial"])
        base = manage_state.same_base_key_rows(conn, g[0]["asset_serial"])
        row = v2_hosts.coverage_row(k, [dict(r) for r in g], ex_m, collected_os.measured_for_rows(conn, base))
        f = fleet[k]
        for fld in ("key", "name", "ip", "serial", "os_ver", "onboarded"):
            assert row[fld] == f[fld], f"{k}.{fld}：單台 {row[fld]!r} ≠ 全機隊 {f[fld]!r}"
        assert row["badge"]["code"] == f["badge"]["code"] and row["badge"]["os_class"] == f["badge"]["os_class"], k


def test_for_host單台版_等於全機隊版(conn):
    groups, _of = oncall._groups(conn)
    for k, g in groups.items():
        ctx = vs.Ctx(conn, k)
        for kind in data_coverage.KINDS:
            fleet = None if k.startswith("ret:") else data_coverage.for_host(conn, k, kind, groups=groups)
            one = ctx.for_host(kind)
            assert one == fleet, f"{k}／{kind}：單台版與全機隊版不同"


def _fleet_item_states(conn):
    """全機隊版 summarize 的逐筆狀態 → 單台版介面（item_states_for_rows）的替身。"""
    summ = {it["asset_serial"]: it["state"] for it in manage_state.summarize(conn)["items"] if it.get("asset_serial")}
    return lambda c, rows: {r["asset_serial"]: summ[r["asset_serial"]] for r in rows if r["asset_serial"] in summ}


def _fleet_dup_groups(orig_dup):
    """全表分組（不傳 only_rows）→ 單台版介面（duplicate_candidate_groups(conn, only_rows)）的替身：取與這幾筆登記有交集的組。"""
    def fleet(conn, only_rows=None):
        full = orig_dup(conn)
        if only_rows is None:
            return full
        mine = {r["asset_serial"] for r in only_rows}
        return [(k, ms_) for k, ms_ in full if mine & {m["asset_serial"] for m in ms_}]
    return fleet


def _run_summary_profile(conn, mode: str, groups):
    """mode：
    single＝現行單台版；
    fleet＝單台捷徑全部換成全機隊結果（health／measured／for_host／item_states／duplicate groups）；
    parts＝health_check.evaluate_one 保持**真的單台版**，但它內部用的 item_states_for_rows、duplicate_candidate_groups 換成
           全機隊結果取值——這樣「這兩條組裝錯了」才會讓整包測試紅（2.070.1，審查 p1#11：以前 summ 算了沒用、兩條都沒被換掉）。"""
    import json
    orig = (health_check.evaluate_one, collected_os.measured_for_rows, vs.Ctx.for_host, manage_state.item_states_for_rows,
            vip_view.duplicate_candidate_groups)
    out = {}
    try:
        if mode in ("fleet", "parts"):
            manage_state.item_states_for_rows = _fleet_item_states(conn)
            vip_view.duplicate_candidate_groups = _fleet_dup_groups(orig[4])
        if mode == "fleet":
            allv = health_check.evaluate_all(conn)
            mm = collected_os.measured_map(conn)
            ids = {r[0]: r[1] for r in conn.execute("SELECT asset_serial, id FROM hardware")}
            health_check.evaluate_one = lambda c, s, rows=None: allv.get(ids[s])
            collected_os.measured_for_rows = lambda c, rows: {r["asset_serial"]: mm[r["asset_serial"]]
                                                               for r in rows if r["asset_serial"] in mm}

            def fleet_for_host(self, kind):
                return None if self.key.startswith("ret:") else data_coverage.for_host(conn, self.key, kind, groups=self.groups)
            vs.Ctx.for_host = fleet_for_host
        for k in groups:
            g = [dict(r) for r in groups[k]]
            # health＝資產明細端點（routers/assets）實際走的單台體檢：item_states／重複分組兩條都經過這裡
            health = {r["asset_serial"]: health_check.evaluate_one(conn, r["asset_serial"]) for r in g}
            out[k] = json.loads(json.dumps({"summary": vs.summary(conn, k), "profile": vs.profile(conn, k), "health": health},
                                           ensure_ascii=False, default=str))
    finally:
        (health_check.evaluate_one, collected_os.measured_for_rows, vs.Ctx.for_host, manage_state.item_states_for_rows,
         vip_view.duplicate_candidate_groups) = orig
    return out


def test_summary與profile_單台版與全機隊拼法逐欄相同(conn):
    """summary／profile 整包比對：單台版（現行程式）對「把單台版關掉、全部走全機隊函式」的拼法，以及「只把 item_states／重複分組
    兩條換成全機隊結果」的拼法（parts）。"""
    groups, _of = oncall._groups(conn)
    single = _run_summary_profile(conn, "single", groups)
    fleet = _run_summary_profile(conn, "fleet", groups)
    parts = _run_summary_profile(conn, "parts", groups)
    assert single.keys() == fleet.keys() == parts.keys()
    for k in single:
        assert single[k] == fleet[k], f"{k}：summary／profile 單台版與全機隊版不同"
        assert single[k] == parts[k], f"{k}：item_states／重複分組 單台版與全機隊版取值不同"


def test_突變_item_states或重複分組單台版算錯_整包測試必須紅(conn, monkeypatch):
    """突變驗證：把單台版的 item_states_for_rows 改成回空（或把重複分組改成不分組），整包比對一定要抓到。"""
    groups, _of = oncall._groups(conn)
    parts = _run_summary_profile(conn, "parts", groups)
    with monkeypatch.context() as m:
        m.setattr(manage_state, "item_states_for_rows", lambda c, rows: {})
        mutated = _run_summary_profile(conn, "single", groups)
    assert mutated != parts, "item_states_for_rows 單台版算錯（回空）整包測試竟然沒抓到"
    with monkeypatch.context() as m:
        m.setattr(vip_view, "duplicate_candidate_groups", lambda c, only_rows=None: [])
        mutated = _run_summary_profile(conn, "single", groups)
    assert mutated != parts, "duplicate_candidate_groups 單台版算錯（不分組）整包測試竟然沒抓到"


def test_summary只取筆數時_同業務系統段筆數仍等於完整版的列數(conn):
    """summary 不外送每列內容，所以同業務系統段不先算整個機隊的虛擬機角色；筆數與成員必須跟完整版（section）相同（加總＝下鑽）。"""
    groups, _of = oncall._groups(conn)
    for k in groups:
        full = vs.section(conn, k, "same_system")
        cnt = next(s for s in vs.summary(conn, k)["sections"] if s["key"] == "same_system")
        assert cnt["count"] == full["count"], f"{k}：summary 筆數 {cnt['count']} ≠ 完整版列數 {full['count']}"
