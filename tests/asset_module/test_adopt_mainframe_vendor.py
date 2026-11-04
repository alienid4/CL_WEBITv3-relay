"""2-1 待納管：頁簽與下一步用同一個判斷（大型主機）＋另有納管方式的列寫廠牌（2026-10-19）。

## 1. 大型主機頁簽與下一步打架（使用者在公司機 v1.468.1 看到的 4 列，IP 換成 10.99.x）

    FUTAS21  AS400（登記 V7R3／IBM AS400），只開 445   → 大型主機頁簽，下一步卻寫「認不出是什麼設備」
    test1T   AIX（9/22 用 root 納管過），開 22         → 大型主機頁簽，卻寫「另有納管方式／目前不收」
    test2    AIX 7.2，開 22，上次失敗：收集沒收到資料   → SSH 收集、納管失敗（本來就對）
    test3    AIX 7.2，開 22，上次失敗：密碼錯誤         → SSH 收集、納管失敗（本來就對）

根因：頁簽看 OS 大類（os_class：認得 oslevel 格式 `7200-05-09-2446`、認得 `V7R3`、採收集到的 OS），
擋不擋看 onboard_eligibility.not_onboardable（只讀作業系統欄的白名單：那兩種寫法都不在名單 → 認不出
的設備），指路又看第三條規則。AIX 納管後 facts 收集把作業系統欄改寫成 oslevel 輸出，所以每一台
納管過的 AIX 下一輪都會被擋成「認不出的設備」。

釘住：大型主機頁簽的列，下一步一定是大型主機的路（AIX → SSH 收集／待開 SSH；AS400 → HMC 收集），
絕不出現「認不出」；**批次納管白名單不變**（classify_os 對 oslevel 仍回 appliance、batch 仍不跑 AIX）。

## 2. 網路設備要寫廠牌（使用者：「對，網路設備要寫廠牌」）

另有納管方式裡的網路設備／儲存設備／設備列寫出廠牌：banner 自報＝確定、資產登記＝確定、
MAC 廠商（OUI）＝只能推測、都沒有＝「廠牌不明」。MAC 廠商不可以讓可納管的機器被擋。
"""
import contextlib
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402
import host_kind as hk  # noqa: E402
import onboard_eligibility as oe  # noqa: E402

ADOPT_VUE = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "adopt.vue").read_text(encoding="utf-8")


@contextlib.contextmanager
def _db():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        try:
            yield conn
        finally:
            conn.close()


def _hw(conn, ip, serial, os, hostname, name, model=None, collect_ok=0, mac=None):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, os=os, hostname=hostname,
                       asset_name=name, device_model=model, environment="測試",
                       asset_status="使用中", collect_ok=collect_ok, mac=mac)


def _fail(conn, ip, stage, message, at):
    conn.execute("INSERT INTO onboard_audit (trigger, target_ip, platform, login_user, ok, stage, message, "
                 "created_at) VALUES ('manual',?,?,?,?,?,?,?)", (ip, "aix", "root", 0, stage, message, at))
    conn.commit()


FUTAS, TEST1T, TEST2, TEST3 = "10.99.18.14", "10.99.18.16", "10.99.18.15", "10.99.18.82"
PORTS = {FUTAS: [445], TEST1T: [22], TEST2: [22], TEST3: [22]}


def _seed_four(conn):
    """公司機那 4 列。test1T 的作業系統欄是納管後 facts 收集寫進去的 oslevel 輸出。"""
    _hw(conn, FUTAS, "C-FUTAS21", "V7R3", "FUTAS21", "中菲期貨系統", model="IBM AS400")
    _hw(conn, TEST1T, "C-TEST1T", "7200-05-09-2446", "test1T", "好麥證券", model="IBM S922",
        collect_ok=1)
    _hw(conn, TEST2, "C-TEST2", "AIX 7.2", "test2", "好麥證券", model="IBM S922")
    _hw(conn, TEST3, "C-TEST3", "AIX 7.2", "test3", "好麥證券", model="IBM S922")
    conn.execute("INSERT INTO onboard_audit (trigger, target_ip, platform, login_user, ok, stage, message, "
                 "created_at) VALUES ('manual',?,?,?,?,?,?,?)",
                 (TEST1T, "aix", "root", 1, "verify", "ok", "2026-09-22 10:00:00"))
    _fail(conn, TEST2, "verify", "收集沒收到資料", "2026-09-22 11:00:00")
    _fail(conn, TEST3, "connect", "Authentication failed: 密碼錯誤", "2026-09-17 11:00:00")


def _run(conn, ports, ssh_ok=()):
    return cd.run_dispatch(conn, "\n".join(ports), prober=lambda ip: ports.get(ip),
                           ttl_prober=lambda ip: None,
                           ssh_prober=lambda ip: (ip in ssh_ok, None if ip in ssh_ok else "denied"))


def _assert_mainframe_consistent(out):
    """頁簽說大型主機，下一步就是大型主機的路——不准出現「認不出」「目前不收」。"""
    for r in out["results"]:
        if r["tab"] != cd.TAB_MAINFRAME:
            continue
        g = r.get("guide")
        assert not (g and g["kind"] == "appliance"), (r["ip"], "大型主機頁簽不可以指成認不出的設備")
        assert not (g and not g.get("how")), (r["ip"], "大型主機頁簽不可以寫「目前不收」")
        if r.get("os_class") == "AIX":
            assert r["status"] != cd.STATUS_NOT_ONBOARDABLE, (r["ip"], "AIX 不是另有納管方式")
        if r.get("os_class") == "IBM i":
            assert r["status"] == cd.STATUS_NOT_ONBOARDABLE and g and g["kind"] == "ibmi", r["ip"]


# ===================== 1. 大型主機 =====================

def test_公司機4列_新收集_頁簽與下一步一致():
    with _db() as conn:
        _seed_four(conn)
        out = _run(conn, PORTS)
        by = {r["ip"]: r for r in out["results"]}
        assert all(r["tab"] == cd.TAB_MAINFRAME for r in by.values())
        _assert_mainframe_consistent(out)
        # FUTAS21：AS400 → 另有納管方式，指路 HMC 收集（不是「認不出」）
        f = by[FUTAS]
        assert f["status"] == cd.STATUS_NOT_ONBOARDABLE
        assert f["guide"]["kind"] == "ibmi" and f["guide"]["how"] == "HMC 收集"
        assert "AS400" in f["message"] and "認不出" not in f["message"]
        # test1T：AIX＋22 → 真的去 SSH 試連（不是「目前不收」）
        t = by[TEST1T]
        assert t["route"] == cd.ROUTE_SSH
        assert t["status"] == cd.STATUS_NEEDS_CREDENTIAL, t["message"]
        assert not t.get("guide")
        assert t["mainframe_onboardable"] is True
        # test2／test3：照舊納管失敗
        assert by[TEST2]["status"] == cd.STATUS_ONBOARD_FAILED
        assert by[TEST3]["status"] == cd.STATUS_ONBOARD_FAILED
        # 重新整理（讀 DB）也一樣
        again = cd.latest_run(conn)
        _assert_mainframe_consistent(again)
        assert {r["ip"]: r["status"] for r in again["results"]}[TEST1T] == cd.STATUS_NEEDS_CREDENTIAL


def test_test1T類_收集帳號通了就是已納管():
    with _db() as conn:
        _seed_four(conn)
        r = {x["ip"]: x for x in _run(conn, PORTS, ssh_ok={TEST1T})["results"]}[TEST1T]
        assert r["status"] == cd.STATUS_COLLECTED and r["tab"] == cd.TAB_MAINFRAME


def test_舊run_修正前存成認不出的設備_讀出來就改對():
    """公司機現在那一輪是舊程式跑的：test1T／FUTAS21 存成 not_onboardable＋appliance。
    升版後不重跑收集，畫面也不可以再寫「認不出」「目前不收」。"""
    with _db() as conn:
        _seed_four(conn)
        rid = conn.execute("INSERT INTO collect_dispatch_run (trigger, status, target_count) "
                           "VALUES ('manual','ok',4)").lastrowid
        old = "設備（不在可納管作業系統清單）——另有納管方式。依據：資產登記的作業系統「…」"
        for ip, port, route, status, kind in (
                (FUTAS, "445", "winrm", "not_onboardable", "appliance"),
                (TEST1T, "22", "ssh", "not_onboardable", "appliance"),
                (TEST2, "22", "ssh", "onboard_failed", None),
                (TEST3, "22", "ssh", "onboard_failed", None)):
            conn.execute("INSERT INTO collect_dispatch_result (run_id, ip, alive, open_ports, route, "
                         "status, registered, message, not_onboardable_kind) VALUES (?,?,1,?,?,?,1,?,?)",
                         (rid, ip, port, route, status, old if kind else "納管失敗", kind))
        conn.commit()
        out = cd.latest_run(conn)
        by = {r["ip"]: r for r in out["results"]}
        _assert_mainframe_consistent(out)
        assert by[FUTAS]["guide"]["how"] == "HMC 收集"
        t = by[TEST1T]
        assert t["status"] == cd.STATUS_NEEDS_CREDENTIAL and not t.get("guide")
        assert "重新收集" in t["message"], "沒試連過就不可以假裝知道結果，要講明重新收集"
        assert out["not_onboardable"] == 1
        assert sum(out["by_tab"].values()) == out["total"] == 4


def test_AIX沒開22_不論探測走哪條路都是待開SSH():
    with _db() as conn:
        _hw(conn, "10.99.18.20", "C-A", "7200-05-09-2446", "aix-a", "好麥證券", collect_ok=1)
        _hw(conn, "10.99.18.21", "C-B", "7200-05-09-2446", "aix-b", "好麥證券", collect_ok=1)
        out = _run(conn, {"10.99.18.20": [], "10.99.18.21": [445]})
        for r in out["results"]:
            assert r["status"] == cd.STATUS_NEEDS_SSH_PORT, (r["ip"], r["status"], r["message"])
            assert r["tab"] == cd.TAB_MAINFRAME
        _assert_mainframe_consistent(out)


def test_真的認不出的設備_不進大型主機頁簽():
    with _db() as conn:
        _hw(conn, "10.99.18.30", "C-X", "SecureKi 5.2", "gw-x", "門禁系統")
        r = _run(conn, {"10.99.18.30": [22]})["results"][0]
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE
        assert r["tab"] != cd.TAB_MAINFRAME
        assert r["guide"]["kind"] == "appliance"


def test_白名單不放寬_批次納管照舊不跑AIX():
    import batch_onboard_service as bos
    assert oe.classify_os("7200-05-09-2446")[0] == "appliance", "白名單結論不變"
    assert oe.classify_os("V7R3")[0] == "appliance"
    assert not oe.is_onboardable("AIX 7.2")
    with _db() as conn:
        _seed_four(conn)
        res = bos.classify_targets(conn, [TEST1T, FUTAS, TEST2])
        assert not res["run"], "批次納管（會建帳號）一台都不可以跑"


# ===================== 2. 廠牌 =====================

def test_廠牌_banner認得出_確定():
    v = hk.vendor_judge(banner_product="Cisco 網路設備")
    assert v["vendor"] == "Cisco" and v["category"] == "network"
    assert v["confidence"] == hk.CONF_CONFIRMED


def test_廠牌_只有MAC是Cisco_推測():
    v = hk.vendor_judge(mac_vendor="Cisco")
    assert v["vendor"] == "Cisco" and v["confidence"] == hk.CONF_LIKELY
    assert any("只能推測" in e for e in v["evidence"])


def test_廠牌_什麼都沒有_寫廠牌不明():
    v = hk.vendor_judge()
    assert v["vendor"] is None and v["confidence"] == hk.CONF_UNKNOWN
    assert hk.load_rules()["vendor_unknown_text"] == "廠牌不明"


def test_廠牌_VMware虛擬網卡不算廠牌():
    v = hk.vendor_judge(mac_vendor="VMware")
    assert v["vendor"] is None and any("不採用" in e for e in v["evidence"])


def test_分派_banner自報的設備_寫確定廠牌():
    with _db() as conn:
        _hw(conn, "10.99.20.1", "N-1", None, "sw-1", "核心交換器")
        out = cd.run_dispatch(conn, "10.99.20.1", prober=lambda ip: [22], ttl_prober=lambda ip: None,
                              ssh_prober=lambda ip: (False, "banner: Cisco Nexus Operating System (NX-OS)"))
        r = out["results"][0]
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE and r["tab"] == cd.TAB_NETWORK
        assert r["vendor"]["vendor"] == "Cisco" and r["vendor"]["confidence"] == hk.CONF_CONFIRMED
        assert r["vendor"]["text"] == "網路設備・Cisco"


def test_分派_登記的設備沒有廠牌字樣_寫廠牌不明_不只寫設備():
    with _db() as conn:
        _hw(conn, "10.99.20.2", "N-2", "7.2.10", "box-2", "某系統")
        r = _run(conn, {"10.99.20.2": [22]})["results"][0]
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE
        assert r["vendor"]["text"] == "設備・廠牌不明"
        assert r["vendor"]["confidence_label"] == "不明"


def test_分派_認不出的設備_MAC是Cisco_推測進網路設備頁簽():
    with _db() as conn:
        _hw(conn, "10.99.20.3", "N-3", "7.2.10", "box-3", "某系統")
        conn.execute("INSERT INTO scan_history (ip, scan_ok, mac, mac_vendor) "
                     "VALUES ('10.99.20.3', 1, '00:00:0c:12:34:56', 'Cisco')")
        conn.commit()
        for out in (_run(conn, {"10.99.20.3": [22]}), cd.latest_run(conn)):
            r = out["results"][0]
            assert r["tab"] == cd.TAB_NETWORK
            assert r["vendor"]["text"] == "網路設備・Cisco"
            assert r["vendor"]["confidence"] == hk.CONF_LIKELY, "只有 MAC 的要標推測"
            assert r["vendor"]["moved_from"] == "appliance"
            assert out["by_tab"][cd.TAB_NETWORK] == 1 and out["not_onboardable_by_kind"] == {"network": 1}


def test_MAC是Cisco的Linux伺服器_照樣可納管_不被擋():
    """Cisco 也做伺服器（UCS）。MAC 廠商單獨出現時不可以把機器排除在白名單外。"""
    with _db() as conn:
        _hw(conn, "10.99.20.4", "L-4", "Red Hat Enterprise Linux 8", "ucs-4", "某系統",
            mac="00:00:0c:aa:bb:cc")
        conn.execute("INSERT INTO scan_history (ip, scan_ok, mac, mac_vendor) "
                     "VALUES ('10.99.20.4', 1, '00:00:0c:aa:bb:cc', 'Cisco')")
        conn.commit()
        r = _run(conn, {"10.99.20.4": [22]})["results"][0]
        assert r["status"] == cd.STATUS_NEEDS_CREDENTIAL and r["tab"] == cd.TAB_SSH
        assert not r.get("vendor")
        assert oe.is_onboardable("Red Hat Enterprise Linux 8")


def test_前端_判斷欄顯示廠牌():
    assert "r.vendor" in ADOPT_VUE
    assert re.search(r"vendorTitle\(", ADOPT_VUE), "滑過要看得到廠牌依據"


def test_verify_numbers_新加的不變式在公司機4列上全過():
    import verify_numbers as vn
    with _db() as conn:
        _seed_four(conn)
        _hw(conn, "10.99.20.9", "N-9", "7.2.10", "box-9", "某系統")
        _run(conn, {**PORTS, "10.99.20.9": [22]})
        before = len(vn.RESULTS)
        vn.check_adopt_tabs(conn)
        mine = vn.RESULTS[before:]
        assert any("大型主機頁簽沒有" in x["name"] for x in mine)
        assert any("每列都寫廠牌" in x["name"] for x in mine)
        bad = [x for x in mine if x["ok"] is False]
        assert not bad, bad
