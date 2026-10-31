"""/adopt 收集結果的頁簽：儲存設備／網路設備／ESXi 從「SSH 收集」分出來（2026-10-13）。

使用者截圖（公司機）：「SSH 收集（Linux）40」裡混著
  - 儲存設備：NAS、作業系統欄寫「儲存設備」的那台
  - 網路設備：Aruba_MM_1／Aruba_MM_2 等（作業系統欄寫「網路設備」）
  - ESXi：VMware Photon OS（虛擬化平台本身）
  - 其他不納管：Avamar、SecureKi Harden
它們都開 22（route=ssh），但結果欄都是「不納管」——不該跟真正能走 SSH 收集的混在一頁。

這支釘住：
  1. 各頁簽互斥、加總＝全部
  2. 頁簽文字不得有「（Linux）」
  3. 上面那些範例主機各落在正確頁簽
  4. 舊 run（類別沒存／網路設備還叫 appliance）讀出來也分得對
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402
import onboard_eligibility as oe  # noqa: E402

ADOPT_VUE = ROOT / "APP" / "asset-module" / "frontend" / "pages" / "adopt.vue"

# 範例主機（IP 用文件保留段，主機名去掉公司字首；作業系統欄照截圖的寫法）
SAMPLES = [
    # ip,            hostname,       os,                    預期頁簽
    ("192.0.2.55",  "NAS019-055",   "儲存設備",            cd.TAB_STORAGE),
    ("192.0.2.203", "SVR19-203",    "儲存設備",            cd.TAB_STORAGE),
    ("192.0.2.240", "Aruba_MM_2",   "網路設備",            cd.TAB_NETWORK),
    ("192.0.2.242", "Aruba_MM_1",   "網路設備",            cd.TAB_NETWORK),
    ("192.0.2.228", "net-228",      "網路設備",            cd.TAB_NETWORK),
    ("192.0.2.236", "net-236",      "ArubaOS 8.10",        cd.TAB_NETWORK),
    ("192.0.2.241", "net-241",      "Cisco NX-OS 9.3",     cd.TAB_NETWORK),
    ("192.0.2.184", "svr019-184",   "VMware Photon OS",    cd.TAB_ESXI),
    # Avamar：全站唯一的儲存規則（system_stats.STORAGE_RE）本來就收 avamar——
    # 它是 Dell EMC 的備份儲存設備，歸儲存，不另開一類
    ("192.0.2.70",  "avamar-01",    "Avamar 客製OS",       cd.TAB_STORAGE),
    # SecureKi Harden：不在可納管白名單、也不是儲存／網路／ESXi → 其他不納管
    ("192.0.2.71",  "harden-01",    "SecureKi Harden",     cd.TAB_NOK_OTHER),
    ("192.0.2.72",  "ocp-01",       "Red Hat Enterprise Linux CoreOS 4.14", cd.TAB_OCP),   # 2026-10-21 OCP 自成頁簽
    ("192.0.2.73",  "idrac-01",     "Idrac 7.00.00.172",   cd.TAB_NOK_OTHER),
    # 真正能走 SSH 收集的
    ("192.0.2.10",  "app-10",       "Rocky Linux 9",       cd.TAB_SSH),
]


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn, ip, hostname, os):
    db.insert_hardware(conn, asset_serial=f"S-{ip}", ip=ip, os=os, hostname=hostname,
                       environment="測試", asset_status="使用中")


def _prober(ip):
    last = int(ip.rsplit(".", 1)[1])
    if last == 20:
        return [445, 5985]       # WinRM
    if last == 30:
        return [80]              # 活著但進不去 → Agent
    if last >= 250:
        return None              # 沒回應 → 只能匯入
    return [22]


def _run(conn):
    for ip, host, os, _ in SAMPLES:
        _seed(conn, ip, host, os)
    targets = [s[0] for s in SAMPLES] + ["192.0.2.20", "192.0.2.30", "192.0.2.251", "192.0.2.252"]
    return cd.run_dispatch(conn, "\n".join(targets), prober=_prober,
                           ssh_prober=lambda ip: (True, None),
                           winrm_runner=lambda *a, **k: (False, "no cred"))


def test_範例主機各落在正確頁簽():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        out = _run(conn)
        tab_of = {r["ip"]: r["tab"] for r in out["results"]}
        for ip, host, os, want in SAMPLES:
            assert tab_of[ip] == want, f"{host}（{os}）落在 {tab_of[ip]}，應該是 {want}"
        assert tab_of["192.0.2.20"] == cd.TAB_WINRM
        assert tab_of["192.0.2.30"] == cd.TAB_AGENT
        # 2026-10-18：沒回應又沒登記＝空位址，自成一格、畫面不列（「只能匯入」只算真的有機器的）
        assert tab_of["192.0.2.251"] == cd.TAB_EMPTY
        conn.close()


def test_頁簽互斥且加總等於全部_重新整理後也一樣():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        for out in (_run(conn), cd.latest_run(conn)):
            by_tab = out["by_tab"]
            assert set(by_tab) == set(cd.TAB_ORDER)
            assert sum(by_tab.values()) == out["total"] == len(out["results"])
            # 下鑽：每個頁簽的數字＝帶這個 tab 的列數（同一份計算）
            for t in cd.TAB_ORDER:
                assert by_tab[t] == sum(1 for r in out["results"] if r["tab"] == t), t
            # 互斥：每列剛好一個頁簽
            assert all(r["tab"] in cd.TAB_ORDER for r in out["results"])
            # SSH 頁簽只剩真正能走 SSH 收集的（沒有任何不納管的混進來）
            assert all(r["status"] != cd.STATUS_NOT_ONBOARDABLE
                       for r in out["results"] if r["tab"] == cd.TAB_SSH)
            assert by_tab[cd.TAB_SSH] == 1
            assert by_tab[cd.TAB_STORAGE] == 3
            assert by_tab[cd.TAB_NETWORK] == 5
            assert by_tab[cd.TAB_ESXI] == 1
            assert by_tab[cd.TAB_OCP] == 1
            assert by_tab[cd.TAB_NOK_OTHER] == 2
            # 不納管各頁簽合計＝不納管總數（tile 跟頁簽講同一件事）
            nok = sum(by_tab[t] for t in (cd.TAB_STORAGE, cd.TAB_NETWORK, cd.TAB_ESXI,
                                          cd.TAB_OCP, cd.TAB_NOK_OTHER))
            assert nok == out["not_onboardable"]
        conn.close()


def test_舊run沒存類別_讀出來照現在的登記重判():
    """升版後不重跑收集，舊結果也要分得出來（不然頁簽全是 0，修正看不到）。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _run(conn)
        # 模擬 1.422.0 以前的 run：類別欄是 NULL；以及 network 出現前存成 appliance 的
        conn.execute("UPDATE collect_dispatch_result SET not_onboardable_kind = NULL "
                     "WHERE ip IN ('192.0.2.55', '192.0.2.184', '192.0.2.71')")
        conn.execute("UPDATE collect_dispatch_result SET not_onboardable_kind = 'appliance' "
                     "WHERE ip IN ('192.0.2.240', '192.0.2.242')")
        conn.commit()
        out = cd.latest_run(conn)
        tab_of = {r["ip"]: r["tab"] for r in out["results"]}
        assert tab_of["192.0.2.55"] == cd.TAB_STORAGE
        assert tab_of["192.0.2.184"] == cd.TAB_ESXI
        assert tab_of["192.0.2.71"] == cd.TAB_NOK_OTHER
        assert tab_of["192.0.2.240"] == cd.TAB_NETWORK
        assert tab_of["192.0.2.242"] == cd.TAB_NETWORK
        assert sum(out["not_onboardable_by_kind"].values()) == out["not_onboardable"]
        conn.close()


def test_banner認得出歸屬的_歸到對應類別():
    assert oe.kind_from_banner("Cisco 網路設備") == oe.KIND_NETWORK
    assert oe.kind_from_banner("Aruba 網路設備") == oe.KIND_NETWORK
    assert oe.kind_from_banner("F5 BIG-IP") == oe.KIND_NETWORK
    assert oe.kind_from_banner("NetApp 儲存") == "storage"
    assert oe.kind_from_banner("Brocade SAN switch") == "storage"   # SAN switch 屬儲存（使用者指定）
    assert oe.kind_from_banner("VMware ESXi") == "esxi"
    # Avi 帶 VMware 字樣但是負載平衡器，不是虛擬化平台
    assert oe.kind_from_banner("VMware Avi Load Balancer（NSX ALB）") == oe.KIND_BANNER
    assert oe.kind_from_banner("iDRAC 管理卡") == oe.KIND_BANNER
    assert oe.kind_from_banner(None) == oe.KIND_BANNER


def test_網路設備只在擋下來之後細分_不改擋不擋的結論():
    # 作業系統欄是網路設備 → 擋，類別 network
    assert oe.not_onboardable("網路設備")[0] == oe.KIND_NETWORK
    assert oe.kind_label(oe.KIND_NETWORK) == "網路設備"
    # classify_os 不變（pipeline／批次納管讀它，不能突然多出 network）
    assert oe.classify_os("網路設備")[0] == "appliance"
    # 可納管的 Linux 不受影響；用途寫「網管」的 Linux 也不會被擋
    assert oe.not_onboardable("Rocky Linux 9") is None
    assert oe.not_onboardable("", "h", "1.1.1.1", frozenset(), extra_text="網管監控主機") is None


def test_頁簽文字不得有Linux括號():
    labels = " ".join(cd.TAB_LABEL.values())
    assert "Linux" not in labels
    src = ADOPT_VUE.read_text(encoding="utf-8")
    # 只看模板裡實際會顯示的字（排掉 <!-- --> 註解與 script 的 // 註解）
    tpl = src[src.index("<template>"):]
    tpl = re.sub(r"<!--.*?-->", "", tpl, flags=re.S)
    assert "（Linux）" not in tpl and "(Linux)" not in tpl
    # 三個新頁簽在畫面上要有
    for t in (cd.TAB_STORAGE, cd.TAB_NETWORK, cd.TAB_ESXI):
        assert f"'{t}'" in src, f"adopt.vue 沒有 {t} 頁簽"


def test_verify_numbers_另有納管方式頁簽合計含OCP():
    """2026-10-21 獨立審查：OCP 自成頁簽後，部署關卡的「另有納管方式頁簽合計＝方塊」要把 OCP 算進去，
    不然只要庫裡有一台 OCP 節點部署就會被 [2d/7] 擋下。"""
    import verify_numbers as vn

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        out = _run(conn)
        assert out["by_tab"][cd.TAB_OCP] >= 1
        before = len(vn.RESULTS)
        vn.check_adopt_tabs(conn)
        mine = vn.RESULTS[before:]
        assert any("另有納管方式" in x["name"] and "AS400" in x["name"] for x in mine)
        bad = [x for x in mine if x["ok"] is False]
        assert not bad, bad
        conn.close()
