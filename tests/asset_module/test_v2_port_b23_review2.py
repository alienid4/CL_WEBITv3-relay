"""2.058.10（批 23 第二輪獨立審查 d8460597／810137e3）：
P1 天條——預檢／批次試密碼的目標清單先統一去空白、去重複，VMware 判定與之後每一步用同一份（「 10.99.0.11」不可以漏判）；
P2——scrub_secret 遞迴、任何長度、不弄壞正常字；VMware 判定加 reverse=True（vCenter 名單只有 FQDN 也擋）；
預檢 ValueError 路徑也遮密碼；舊 /adopt #頁簽 轉址用行為測（middleware、6.1 掛載時轉換見 frontend/tests/v2AdoptHashRedirect.spec.ts）。"""
import sys
import tempfile
from pathlib import Path

from test_v2_port_b23 import ROOT

sys.path.insert(0, str(ROOT / "tests" / "asset_module"))


def _setup(tmp, monkeypatch, tried):
    import test_onboard_batch_probe as tb
    import onboard_engine
    import db
    client = tb._client(tmp)
    conn = db.get_connection(Path(tmp) / "t.db")
    db.insert_hardware(conn, asset_serial="A-1", ip="10.99.0.11", os="VMware ESXi 8.0.3",
                       hostname="esx-example-01", environment="測試", asset_status="使用中")
    conn.commit(); conn.close()

    def probe(host, username, password, timeout=20, runner=None):
        tried.append(host)
        return {"os": "", "uid": None, "has_sudo": False, "error": "登入被拒"}
    monkeypatch.setattr(onboard_engine, "probe_target", probe)
    import onboard_eligibility as oe
    monkeypatch.setattr(oe, "_reverse_names", lambda ip: set())   # 測試不打真的 DNS（反解每台最多等 2 秒）
    return tb, client


WEIRD = [" 10.99.0.11", "10.99.0.11 ", "\t10.99.0.11\n", "10.99.0.12", "10.99.0.12", " 10.99.0.12 ", ""]


def test_天條_前後空白與重複_批次試密碼一次都不登VMware_結果不重複(monkeypatch):
    tried = []
    with tempfile.TemporaryDirectory() as tmp:
        tb, client = _setup(tmp, monkeypatch, tried)
        try:
            r = client.post("/api/onboard/batch-probe", json={"ips": WEIRD, "username": "root", "passwords": ["x1x"]})
            assert r.status_code == 200, r.text
            res = r.json()["results"]
            assert [x["ip"] for x in res] == ["10.99.0.11", "10.99.0.12"]          # 去空白、去重複、保留順序
            assert res[0]["vmware"] is True and res[0]["matched_password_index"] is None
            assert tried == ["10.99.0.12"]                                       # VMware 那台零次
            r = client.post("/api/onboard/batch-probe", json={"ips": [" 10.99.0.11 "], "username": "root", "passwords": ["x1x"]})
            assert r.status_code == 200 and r.json()["results"][0]["vmware"] is True and tried == ["10.99.0.12"]
        finally:
            tb.api.app.dependency_overrides.clear()


def test_天條_前後空白與重複_預檢一次都不登VMware(monkeypatch):
    tried = []
    with tempfile.TemporaryDirectory() as tmp:
        tb, client = _setup(tmp, monkeypatch, tried)
        try:
            r = client.post("/api/onboard/precheck", json={"ips": WEIRD, "username": "root", "password": "x1x"})
            assert r.status_code == 200, r.text
            res = r.json()["results"]
            assert [x["ip"] for x in res] == ["10.99.0.11", "10.99.0.12"]
            assert res[0]["verdict"] == "不會納管" and res[0]["vmware"] is True
            assert tried == ["10.99.0.12"]
            # 只有 VMware（帶空白）：一次都不登
            tried.clear()
            r = client.post("/api/onboard/precheck", json={"ips": ["10.99.0.11 "], "username": "root", "password": "x1x"})
            assert r.status_code == 200 and r.json()["results"][0]["vmware"] is True and tried == []
        finally:
            tb.api.app.dependency_overrides.clear()


def test_precheck_service_呼叫端沒strip_refused也擋得住():
    """第二道防線：就算呼叫端傳進來的 refused 鍵沒去空白，run() 自己也對齊。"""
    import precheck_service
    tried = []

    class R:
        def __call__(self, *a, **k):
            tried.append(a)
            raise AssertionError("不可以登入")
    out = precheck_service.run([" 10.99.0.11"], "root", "x1x", runner=R(), refused={" 10.99.0.11 ": "VMware ESXi"})
    assert out["results"][0]["vmware"] is True and tried == []


def test_天條_vCenter名單只有FQDN_反解得到_也擋(monkeypatch):
    """reverse=True（同單台驗證那條）：資產庫沒登記這個 IP、vCenter ESXi 名單只有 FQDN，IP 反解出那個 FQDN → 擋下。"""
    import onboard_eligibility as oe
    tried = []
    with tempfile.TemporaryDirectory() as tmp:
        tb, client = _setup(tmp, monkeypatch, tried)
        fqdn = "esx-fqdn-01.corp.example"
        monkeypatch.setattr(oe, "esxi_host_names", lambda conn: oe._name_keys(fqdn))
        monkeypatch.setattr(oe, "_reverse_names", lambda ip: {fqdn} if ip == "10.99.0.31" else set())
        try:
            r = client.post("/api/onboard/batch-probe", json={"ips": ["10.99.0.31"], "username": "root", "passwords": ["x1x"]})
            assert r.status_code == 200 and r.json()["results"][0]["vmware"] is True
            r = client.post("/api/onboard/precheck", json={"ips": ["10.99.0.31"], "username": "root", "password": "x1x"})
            assert r.status_code == 200 and r.json()["results"][0]["vmware"] is True
            assert tried == []
        finally:
            tb.api.app.dependency_overrides.clear()


def test_預檢_ValueError路徑也遮密碼(monkeypatch):
    """預檢只接受 IP，錯誤訊息會把那個值原樣寫回來；人把密碼貼進 IP 欄時，400 的 detail 不可以帶出密碼（突變 M4）。"""
    tried = []
    pw = "Leak#Check-998"
    with tempfile.TemporaryDirectory() as tmp:
        tb, client = _setup(tmp, monkeypatch, tried)
        try:
            r = client.post("/api/onboard/precheck", json={"ips": [f"bad-{pw}"], "username": "root", "password": pw})
            # 2.058.13 審查 P1：不是 IP 改成逐筆回「不是有效的 IP」（不再整批 400），密碼照樣遮
            assert r.status_code == 200, r.text
            assert "只接受 IPv4 位址" in r.text and pw not in r.text and "已隱藏" in r.text
            assert tried == []
        finally:
            tb.api.app.dependency_overrides.clear()


def test_scrub_secret_規則():
    """2.058.14 審查 P2-4 改規則：只看自由文字欄位，出現密碼（子字串、任何長度）就整個欄位藏起來；結構欄位不動。"""
    sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
    from _app_shared import scrub_secret as s, SECRET_HIDDEN as H
    assert s("Permission denied", "xyz") == "Permission denied"
    assert s("login den failed", "den") == H
    assert s("x", "x") == H                                                 # 1 個字也算
    nested = {"ip": "10.99.0.1", "a": [{"error": "user:p9 bad"}], "n": 0, "t": True, "z": None, "tup": ("p9",)}
    assert s(nested, "p9") == {"ip": "10.99.0.1", "a": [{"error": H}], "n": 0, "t": True, "z": None, "tup": ("p9",)}
    assert s(RuntimeError("boom Leak#1"), "Leak#1") == H                   # 例外物件轉文字再看
    assert s("abc", "") == "abc"
