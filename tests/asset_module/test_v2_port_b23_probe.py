"""V2 移植第 23 批 第 6、7 片（2.058.6／2.058.7）：舊 /adopt 待納管表的「批次試密碼」「預檢（只看不動）」「納管前資訊」
搬到 6.1 待納管；未登記主機的「我沒有密碼：取得納管指令」搬到 2.1 掃到未登記。⚠D1 D2 D5 純搬移：
全部沿用共用視窗（BatchProbeModal／PrecheckModal／PreinfoPanel／PasteOnboardModal），同一支端點、同樣的上限；
能勾哪幾台＝舊頁 canProbe 一字不改；密碼只在共用視窗裡，V2 頁面完全不碰密碼。"""
import re
import sys
import tempfile
from pathlib import Path

from test_v2_port_b23 import ROOT, _src

ONB = "pages/v2/onboard.vue"
UNREG = "components/v2/HostUnregistered.vue"


def _fn_body(src, name):
    i = src.index(f"function {name}(")
    return src[i:src.index("\n}\n", i)]


def test_canProbe跟舊頁一字不差():
    old, u = _src("pages/adopt.vue"), _src("utils/adoptCollect.ts")
    assert "const NO_ONBOARD_TABS = ['esxi', 'storage', 'network', 'ocp', 'nok_other', 'import', 'agent']" in old
    assert "export const NO_ONBOARD_TABS = ['esxi', 'storage', 'network', 'ocp', 'nok_other', 'import', 'agent']" in u
    strip = lambda b: [ln.strip() for ln in b.splitlines()[1:] if ln.strip().startswith(("if ", "return"))]
    assert strip(_fn_body(old, "canProbe")) == strip(_fn_body(u, "canProbe"))
    assert "export const PRECHECK_MAX = 20" in u and "probeSelected.size > 20" in old


def test_待納管_三顆按鈕沿用共用視窗_送的只有canProbe的IP():
    o = _src(ONB)
    assert '<BatchProbeModal v-if="isAdmin && batchProbeIps" :ips="batchProbeIps"' in o
    assert '<PrecheckModal v-if="isAdmin && precheckIps" :ips="precheckIps"' in o
    # 審查 P2-2（2.058.8 收緊）：批次試密碼、預檢、取得納管指令只給管理員
    assert '<button v-if="isAdmin" type="button" class="btn2" data-test="pend-probe"' in o
    assert '<button v-if="isAdmin" type="button" class="btn2" data-test="pend-precheck"' in o
    assert '<button v-if="isAdmin" type="button" class="btn2" :disabled="!toSend.length" data-test="unreg-paste"' in _src(UNREG)
    assert '<PreinfoPanel v-if="preinfoIp" :ip="preinfoIp"' in o
    assert '@click="batchProbeIps = probeIps"' in o and '@click="precheckIps = probeIps"' in o
    assert ':disabled="!probeIps.length || probeIps.length > PRECHECK_MAX"' in o
    assert "canProbe({ ...r, last_fail_short: lastFail.value[r.ip]?.fail?.short ?? null })" in o
    assert "(lastFailOk.value ? pickedRows.value : [])" in o          # 讀不到上次紀錄就不給勾（fail-closed）
    # 上次紀錄＝舊頁同一支（POST 批量查，只讀）
    assert "'/api/onboard/audit/latest-by-ip', { method: 'POST', body: { ips } }" in o
    assert "'/api/onboard/audit/latest-by-ip', { method: 'POST', body: { ips: uniq } }" in _src("pages/adopt.vue")


def test_待納管欄位帶得出canProbe要的東西():
    v2 = (ROOT / "APP/asset-module/backend/routers/v2.py").read_text(encoding="utf-8")
    m = re.search(r"_PENDING_FIELDS = \(([^)]*)\)", v2, re.S)
    for k in ("status", "tab", "route", "guide", "mainframe_onboardable"):
        assert f'"{k}"' in m.group(1), k


def test_取得納管指令沿用共用視窗_只送看得到的():
    s = _src(UNREG)
    assert '<PasteOnboardModal v-if="pasteIps" :ips="pasteIps"' in s and '@click="pasteIps = toSend"' in s


def test_V2頁面完全不碰密碼():
    """密碼只在共用視窗（送出就清、不落地，見 test_onboard_batch_probe／test_onboard_precheck）；
    V2 這幾頁不得有密碼欄位、不得把密碼放進網址或瀏覽器儲存。"""
    for p in (ONB, UNREG, "components/v2/AdoptClassify.vue", "components/v2/AdoptCollect.vue", "components/v2/AdoptRegister.vue"):
        s = _src(p)
        assert 'type="password"' not in s, p
        assert not re.search(r"password\s*[:=]", s, re.I), p
        assert "localStorage" not in s and "sessionStorage" not in s, p


def test_共用視窗_密碼送出就清_初始為空():
    bp, pc = _src("components/BatchProbeModal.vue"), _src("components/PrecheckModal.vue")
    assert "const passwordA = ref('')" in bp and "const passwordB = ref('')" in bp
    fin = bp[bp.index("} finally {"):]
    assert "passwordA.value = ''" in fin and "passwordB.value = ''" in fin
    assert "const cred = reactive({ username: 'sysinfra', password: '' })" in pc
    run = pc[pc.index("async function run()"):]
    assert run.index("cred.password = ''") < run.index("'/api/onboard/precheck'")
    assert "onBeforeUnmount(() => { cred.password = '' })" in pc
    for s in (bp, pc):
        assert "console." not in s and "localStorage" not in s


def test_批次試密碼與預檢_HTTP回應不含密碼(monkeypatch):
    """審查 P2-5（2.058.8）：假的目標機把密碼原樣吐回錯誤訊息（echo）——回應裡仍不可以出現密碼。
    原本的假錯誤是 password[:0]（空字串），根本測不到 echo。"""
    sys.path.insert(0, str(ROOT / "tests" / "asset_module"))
    import test_onboard_batch_probe as tb
    import onboard_engine
    fake_pw = "Leak#Check-998"
    echo = lambda host, username, password, timeout=20, runner=None: {
        "os": "", "uid": None, "has_sudo": False, "error": f"登入被拒（{username}:{password}）"}
    with tempfile.TemporaryDirectory() as tmp:
        client = tb._client(tmp)
        try:
            monkeypatch.setattr(onboard_engine, "probe_target", echo)     # 真的 batch_probe_credentials，假的目標機
            r = client.post("/api/onboard/batch-probe", json={"ips": ["10.99.0.1"], "username": "root", "passwords": [fake_pw, fake_pw + "B"]})
            assert r.status_code == 200, r.text
            assert "已隱藏" in r.text and fake_pw not in r.text   # 2.058.14：含密碼的錯誤訊息整欄藏起來
            r = client.post("/api/onboard/precheck", json={"ips": ["10.99.0.1"], "username": "root", "password": fake_pw})
            assert r.status_code == 200, r.text
            assert "已隱藏" in r.text and fake_pw not in r.text
        finally:
            tb.api.app.dependency_overrides.clear()


def test_天條_批次試密碼與預檢_VMware在伺服器端就擋下_不登入(monkeypatch):
    """審查 P2-4（2.058.8，⚠D1 天條）：不能只靠畫面不給勾。登記成 VMware ESXi 的 IP 送到 batch-probe／precheck，
    後端用既有的 onboard_eligibility.vmware_targets 判定、直接回拒絕，**完全不呼叫 SSH**；一般 IP 照常探測。"""
    sys.path.insert(0, str(ROOT / "tests" / "asset_module"))
    import test_onboard_batch_probe as tb
    import onboard_engine
    import db
    from pathlib import Path as _P
    tried = []
    def probe(host, username, password, timeout=20, runner=None):
        tried.append(host)
        return {"os": "", "uid": None, "has_sudo": False, "error": "登入被拒"}
    with tempfile.TemporaryDirectory() as tmp:
        client = tb._client(tmp)
        conn = db.get_connection(_P(tmp) / "t.db")
        db.insert_hardware(conn, asset_serial="A-1", ip="10.99.0.11", os="VMware ESXi 8.0.3",
                           hostname="esx-example-01", environment="測試", asset_status="使用中")
        conn.commit(); conn.close()
        try:
            monkeypatch.setattr(onboard_engine, "probe_target", probe)
            import onboard_eligibility as _oe
            monkeypatch.setattr(_oe, "_reverse_names", lambda ip: set())   # 不打真的 DNS
            r = client.post("/api/onboard/batch-probe", json={"ips": ["10.99.0.11", "10.99.0.12"], "username": "root", "passwords": ["x1x"]})
            assert r.status_code == 200, r.text
            res = {x["ip"]: x for x in r.json()["results"]}
            assert [x["ip"] for x in r.json()["results"]] == ["10.99.0.11", "10.99.0.12"]
            assert res["10.99.0.11"]["vmware"] is True and res["10.99.0.11"]["matched_password_index"] is None
            assert "VMware" in res["10.99.0.11"]["error"] and res["10.99.0.11"]["refusal"]
            assert tried == ["10.99.0.12"]                      # ESXi 那台一次都沒試
            tried.clear()
            r = client.post("/api/onboard/precheck", json={"ips": ["10.99.0.11", "10.99.0.12"], "username": "root", "password": "x1x"})
            assert r.status_code == 200, r.text
            pres = {x["ip"]: x for x in r.json()["results"]}
            assert pres["10.99.0.11"]["verdict"] == "不會納管" and pres["10.99.0.11"]["vmware"] is True
            assert "VMware" in pres["10.99.0.11"]["reason"]
            assert tried == ["10.99.0.12"]
            # 只有 VMware 一台：一次都不登入
            tried.clear()
            r = client.post("/api/onboard/batch-probe", json={"ips": ["10.99.0.11"], "username": "root", "passwords": ["x1x"]})
            assert r.status_code == 200 and r.json()["results"][0]["vmware"] is True and tried == []
        finally:
            tb.api.app.dependency_overrides.clear()
