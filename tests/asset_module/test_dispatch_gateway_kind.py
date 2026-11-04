"""閘道位址／只憑 TTL 不可以被叫去裝 Push Agent（v1.468.3）。

公司機 v1.468.1，2-1 待納管 → Push Agent 頁簽 18 列：全是資產庫沒登記的 .1／.31／.254、
22／445／3389／5985 都沒開，判成「Linux／Unix 推測」、待裝 Agent、按鈕「取得安裝包」。
那些是閘道／路由器或防火牆介面／HSRP-VRRP 虛擬 IP（只回 ping），不是 Linux 伺服器。

根因：host_kind_rules.json 的 TTL 1~64 → Linux（推測）單獨就下判斷；網段掃描的
os_guess「Linux/Unix（TTL≈64）」也只是同一個 TTL。

這支釘住（舊程式紅、新程式綠）：
  1. 沒登記＋網段頭尾（.1／.254）＋沒開主機服務埠 → 網路設備（閘道位址）推測、網路設備頁簽、
     下一步「多半是閘道／網路設備介面，確認後可標為非納管」
  2. TTL 64 單獨（含 TTL 推來的 os_guess）→ 不明，不是 Linux
  3. .31 這種位址沒證據就是不明
  4. TTL ≥200 → 網路設備推測
  5. 這些推測只會把機器移出 Push Agent，不會讓任何一台變可納管；有登記的不套閘道規則
  6. verify_numbers 新不變式：舊形狀（TTL-only Linux／閘道位址在 Push Agent）會紅
"""
import contextlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402
import host_kind as hk  # noqa: E402

# 公司機那幾種形狀（IP 換成說明用的 10.99.x）
GATEWAYS = ["10.99.1.254", "10.99.18.254", "10.99.163.254", "10.99.1.1"]
ODD = "10.99.1.31"


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


def _run(conn, ports, ttls):
    return cd.run_dispatch(conn, "\n".join(ports), prober=lambda ip: ports.get(ip),
                           ttl_prober=lambda ip: ttls.get(ip),
                           ssh_prober=lambda ip: (False, "denied"))


def _company_shape(conn):
    """公司機的樣子：活著、沒開任何探測埠、TTL 64 附近、資產庫沒登記；網段掃描也只看到 TTL。"""
    ips = GATEWAYS + [ODD]
    for ip in ips:
        conn.execute("INSERT INTO scan_history (ip, scan_ok, ttl, os_guess, open_ports) "
                     "VALUES (?, 1, 63, 'Linux/Unix（TTL≈64）', NULL)", (ip,))
    conn.commit()
    return _run(conn, {ip: [] for ip in ips}, {ip: 63 for ip in ips})


# ===================== 1. 判斷規則 =====================

def test_TTL64單獨_不是Linux_是不明():
    g = hk.infer(ttl=64)
    assert g["kind"] == hk.KIND_UNKNOWN and g["confidence"] == hk.CONF_UNKNOWN
    assert any("TTL 64" in e for e in g["evidence"]), "線索照樣列出來"
    g2 = hk.infer(ttl=63, os_guess="Linux/Unix（TTL≈64）")
    assert g2["kind"] == hk.KIND_UNKNOWN, "TTL 推來的 os_guess 也只是同一個 TTL"


def test_Linux推測要TTL以外的線索():
    assert hk.infer(ttl=63, os_guess="Linux/Unix（SSH 22）")["kind"] == "linux"
    assert hk.infer(ttl=63, open_ports=[111])["kind"] == "linux"
    g = hk.infer(ttl=63, registry_class="Linux", registry_text="RHEL 8")
    assert g["kind"] == "linux" and g["confidence"] == hk.CONF_CONFIRMED


def test_閘道位址_沒登記沒開埠_是網路設備推測():
    for ip in GATEWAYS:
        g = hk.infer(ip=ip, registered=0, ttl=63)
        assert g["kind"] == "gateway" and g["confidence"] == hk.CONF_LIKELY, ip
        assert g["action"] == hk.ACTION_NOK and g["nok_kind"] == "network"
        assert g["next_step"] == "多半是閘道／網路設備介面，確認後可標為非納管"


def test_閘道規則的界線():
    assert hk.infer(ip="10.99.1.31", registered=0, ttl=63)["kind"] == hk.KIND_UNKNOWN, ".31 沒證據就是不明"
    assert hk.infer(ip="10.99.1.254", registered=1, ttl=63)["kind"] == hk.KIND_UNKNOWN, "有登記不套閘道規則"
    assert hk.infer(ip="10.99.1.254", registered=None)["kind"] == hk.KIND_UNKNOWN, "不知道有沒有登記＝不命中"
    assert hk.infer(ip="10.99.1.254", registered=0, open_ports=[22])["kind"] != "gateway", "開了 SSH 就不是"
    assert hk.infer(ip="10.99.1.1", registered=0, open_ports=[3389])["kind"] == "windows"


def test_TTL255是網路設備推測():
    g = hk.infer(ttl=252)
    assert g["kind"] == "network" and g["confidence"] == hk.CONF_LIKELY and g["action"] == hk.ACTION_NOK


def test_位址清單在設定檔(monkeypatch):
    cfg = hk.load_rules()
    rules = [dict(r, ip_last_octet_in=[31]) if r.get("kind") == "gateway" else r for r in cfg["scan_rules"]]
    monkeypatch.setattr(hk, "_RULES", dict(cfg, scan_rules=rules))
    assert hk.infer(ip="10.99.1.31", registered=0)["kind"] == "gateway"
    assert hk.infer(ip="10.99.1.254", registered=0)["kind"] == hk.KIND_UNKNOWN


# ===================== 2. 分派（公司機的形狀）=====================

def test_公司機形狀_閘道離開PushAgent_31留著但是不明():
    with _db() as conn:
        for out in (_company_shape(conn), cd.latest_run(conn)):
            by = {r["ip"]: r for r in out["results"]}
            for ip in GATEWAYS:
                r = by[ip]
                assert r["status"] == cd.STATUS_NOT_ONBOARDABLE, ip
                assert r["tab"] == cd.TAB_NETWORK, ip
                assert r["kind_guess"]["kind"] == "gateway"
                assert "取得安裝包" not in r["message"]
                assert "多半是閘道／網路設備介面，確認後可標為非納管" in r["message"]
                assert r["guide"]["note"] == "多半是閘道／網路設備介面，確認後可標為非納管"
                assert r["vendor"]["text"].startswith("網路設備・")
            r = by[ODD]
            assert r["tab"] == cd.TAB_AGENT and r["status"] == cd.STATUS_NEEDS_AGENT
            assert r["kind_guess"]["kind"] == hk.KIND_UNKNOWN, "不是 Linux 推測"
            assert "先確認是什麼機器" in r["message"]
            assert out["by_tab"][cd.TAB_AGENT] == 1
            assert out["by_tab"][cd.TAB_NETWORK] == len(GATEWAYS)
            assert sum(out["by_tab"].values()) == out["total"]


def test_舊run存成待裝Agent的閘道_讀出來就移到網路設備():
    """升版不重跑收集：舊 run（v1.468.1 存的待裝 Agent）讀出來就要搬走。"""
    with _db() as conn:
        rid = conn.execute("INSERT INTO collect_dispatch_run (trigger, status, target_count) "
                           "VALUES ('manual', 'ok', 2)").lastrowid
        for ip in ("10.99.18.254", ODD):
            conn.execute(
                "INSERT INTO collect_dispatch_result (run_id, ip, alive, open_ports, route, status, "
                "registered, message, ttl) VALUES (?,?,1,NULL,'agent','needs_agent',0,'舊訊息',64)",
                (rid, ip))
        conn.commit()
        by = {r["ip"]: r for r in cd.latest_run(conn)["results"]}
        assert by["10.99.18.254"]["tab"] == cd.TAB_NETWORK
        assert by[ODD]["tab"] == cd.TAB_AGENT and by[ODD]["kind_guess"]["kind"] == hk.KIND_UNKNOWN


def test_有登記的頭尾位址不套閘道規則():
    with _db() as conn:
        db.insert_hardware(conn, asset_serial="L-1", ip="10.99.7.254", os=None, hostname="box",
                           environment="測試", asset_status="使用中")
        r = _run(conn, {"10.99.7.254": []}, {"10.99.7.254": 63})["results"][0]
        assert r["tab"] == cd.TAB_AGENT and r["kind_guess"]["kind"] == hk.KIND_UNKNOWN


def test_推測永遠不會讓機器變可納管():
    """閘道／TTL 規則只產生另有納管方式或不明，不會產生 SSH／WinRM 收集的狀態。"""
    with _db() as conn:
        out = _company_shape(conn)
        assert all(r["status"] in (cd.STATUS_NOT_ONBOARDABLE, cd.STATUS_NEEDS_AGENT)
                   for r in out["results"])
        assert not any(r["tab"] in (cd.TAB_SSH, cd.TAB_WINRM) for r in out["results"])


# ===================== 3. verify_numbers =====================

def _vn_failures(conn):
    import verify_numbers as vn
    before = len(vn.RESULTS)
    vn.check_adopt_tabs(conn)
    return [x for x in vn.RESULTS[before:] if x["ok"] is False], vn.RESULTS[before:]


def test_verify_numbers_新資料全過():
    with _db() as conn:
        _company_shape(conn)
        bad, mine = _vn_failures(conn)
        assert not bad, bad
        names = [x["name"] for x in mine]
        assert any("網段頭尾位址" in n for n in names)
        assert any("只憑 TTL" in n for n in names)


def test_verify_numbers_舊形狀會紅(monkeypatch):
    """模擬舊判斷（TTL 64 單獨 → Linux）：閘道位址留在 Push Agent、TTL-only Linux，兩條都要紅。"""
    old = {"kind": "linux", "label": "Linux／Unix", "confidence": "likely", "confidence_label": "推測",
           "evidence": ["TTL 63（≈64，Linux／Unix 的預設值）"], "action": "agent", "nok_kind": None}
    monkeypatch.setattr(hk, "infer_from", lambda *a, **k: dict(old))
    with _db() as conn:
        _company_shape(conn)
        bad, _ = _vn_failures(conn)
        names = [x["name"] for x in bad]
        assert any("網段頭尾位址" in n for n in names), names
        assert any("只憑 TTL" in n for n in names), names
