"""Q92 Aria Operations API 匯入：用假 transport 把整條路走一遍。

⚠️ 所有回應都是依官方文件的形狀手造的假資料，**不是**真實 Aria Operations 的回應；
真實格式要在公司網路實測。這裡守的是邏輯：登入失敗、翻頁、部分失敗、對映完整、冪等、機密不外洩、模組關閉擋住。
"""
import json
import sys
import tempfile
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import credential_store  # noqa: E402
import db  # noqa: E402
import integration_http as ih  # noqa: E402
import rvtools_import as rv  # noqa: E402
import vrops_constants as C  # noqa: E402
import vrops_import as vi  # noqa: E402
import vrops_mapper as mp  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

FAKE_PW = "pw-for-test-9876"
FAKE_SESSION = "tok-abcdef-0123456789-abcdef-0123456789-abcdef-0123456789"
URL = "https://aria.example.local"


def res(rid, name, kind, ids=None):
    return {"identifier": rid, "resourceKey": {"name": name, "adapterKindKey": "VMWARE", "resourceKindKey": kind,
            "resourceIdentifiers": [{"identifierType": {"name": k}, "value": v} for k, v in (ids or {}).items()]}}


def prop_item(rid, props):
    return {"resourceId": rid, "property-contents": {"property-content": [
        {"statKey": k, "timestamps": [1], "values": [v]} for k, v in props.items()]}}


def build_world():
    vms = [res("vm-a", "web01", C.KIND_VM, {"VMEntityObjectID": "vm-101", "VMEntityInstanceUUID": "inst-1"}),
           res("vm-b", "db01", C.KIND_VM, {"VMEntityObjectID": "vm-102"}),
           res("vm-c", "app01", C.KIND_VM, {"VMEntityObjectID": "vm-103"})]
    hosts = [res("h1", "esxi-a.example.local", C.KIND_HOST, {"VMEntityObjectID": "host-1"}),
             res("h2", "esxi-b.example.local", C.KIND_HOST, {"VMEntityObjectID": "host-2"})]
    clusters = [res("c1", "CL_PROD", C.KIND_CLUSTER, {"VMEntityObjectID": "domain-c1"})]
    dss = [res("d1", "DS_PROD_A", C.KIND_DATASTORE, {"VMEntityObjectID": "datastore-1"}),
           res("d2", "DS_PROD_B", C.KIND_DATASTORE, {"VMEntityObjectID": "datastore-2"})]
    props = {
        "vm-a": {"config|uuid": "42301111-2222-3333-4444-555566667777", "guest|hostName": "web01.corp.example", "guest|ipAddress": "10.99.1.10",
                 "config|guestFullName": "Ubuntu Linux (64-bit)", "summary|runtime|powerState": "Powered On",
                 "config|hardware|num_Cpu": "4", "config|hardware|memoryKB": "8388608", "config|hardware|disk_Space": "100"},
        "vm-b": {"config|uuid": "4230aaaa-bbbb-cccc-dddd-eeeeffff0000", "guest|ipAddress": "10.99.1.20",
                 "config|guestFullName": "Microsoft Windows Server 2019", "summary|runtime|powerState": "poweredOff",
                 "config|hardware|num_Cpu": "2"},
        "vm-c": {"guest|ipAddress": "10.99.1.30", "summary|parentHost": "esxi-b.example.local"},
        "h1": {"summary|version": "7.0.3", "summary|build": "12345", "summary|parentDatacenter": "DC_A"},
        "h2": {"summary|version": "7.0.3", "summary|build": "12345", "summary|parentDatacenter": "DC_A"},
        "d1": {"capacity|total_capacity": "10", "capacity|available_space": "4", "summary|type": "VMFS"},
        "d2": {"capacity|total_capacity": "20", "capacity|available_space": "5", "summary|type": "vsan"},
    }
    rels = {("c1", "CHILD"): [hosts[0], hosts[1]], ("h1", "CHILD"): [vms[0], vms[1]], ("h2", "CHILD"): [vms[2]],
            ("d1", "CHILD"): [vms[0], vms[1]], ("d2", "CHILD"): [vms[2]],
            ("d1", "PARENT"): [hosts[0]], ("d2", "PARENT"): [hosts[0], hosts[1]]}
    return {"vms": vms, "hosts": hosts, "clusters": clusters, "dss": dss, "props": props, "rels": rels}


class FakeAria:
    """假 transport。fail_on：路徑片段 → 回 500（模擬部分失敗）。"""

    def __init__(self, world, bad_login=False, fail_on=(), expire_first_token=False):
        self.w, self.bad_login, self.fail_on = world, bad_login, tuple(fail_on)
        self.calls: list[dict] = []
        self.expire = expire_first_token
        self.closed = False

    def request(self, method, url, *, headers=None, json_body=None, form=None, params=None, timeout=30.0):
        path = urlsplit(url).path
        self.calls.append({"method": method, "path": path, "params": params, "json": json_body, "headers": headers or {}})
        R = ih.HttpResult
        if path == C.TOKEN_ACQUIRE:
            if self.bad_login:
                return R(401, {"message": f"bad credentials for {json_body['password']}"}, "")
            return R(200, {"token": FAKE_SESSION, "validity": 1}, "")
        if path == C.TOKEN_RELEASE:
            return R(204, None, "")
        if (headers or {}).get(C.AUTH_HEADER) != f"{C.AUTH_SCHEME} {FAKE_SESSION}":
            return R(401, {"message": "no token"}, "")
        if self.expire:
            self.expire = False
            return R(401, {"message": "expired"}, "")
        for frag in self.fail_on:
            if frag in path or (params and frag in json.dumps(params)) or (json_body and frag in json.dumps(json_body)):
                return R(500, {"message": f"boom with token {FAKE_SESSION}"}, "")
        if path == C.RESOURCES:
            kind = params["resourceKind"]
            items = {C.KIND_VM: self.w["vms"], C.KIND_HOST: self.w["hosts"], C.KIND_CLUSTER: self.w["clusters"],
                     C.KIND_DATASTORE: self.w["dss"]}[kind]
            size, page = params["pageSize"], params["page"]
            return R(200, {"pageInfo": {"totalCount": len(items), "page": page, "pageSize": size},
                           "resourceList": items[page * size:(page + 1) * size]}, "")
        if path.endswith("/relationships"):
            rid = path.split("/")[-2]
            return R(200, {"resourceList": self.w["rels"].get((rid, params["relationshipType"]), [])}, "")
        if path == C.PROPERTIES_LATEST_QUERY:
            return R(200, {"values": [prop_item(i, self.w["props"].get(i, {})) for i in json_body["resourceIds"]]}, "")
        return R(404, None, "")

    def close(self):
        self.closed = True


def _setup(tmp, world=None, **kw):
    db_path = Path(tmp) / "t.db"
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    vi.KEY_PATH = str(Path(tmp) / "cred.key")
    fake = FakeAria(world or build_world(), **kw)
    ih.TRANSPORT_FACTORY = lambda ca: fake
    conn.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='vrops_api'")
    conn.commit()
    credential_store.save(conn, vi.CRED_NAME, "vrops", "svc_aria", FAKE_PW, key_path=vi.KEY_PATH)   # 帳密在收集憑證庫（舊固定名稱，不設選擇也能解析）
    vi.save_config(conn, url=URL, auth_source="", ca_bundle="", enabled=False, interval_hours=24)
    return conn, fake, db_path


def _cleanup():
    ih.TRANSPORT_FACTORY = ih.HttpxTransport
    vi.KEY_PATH = credential_store.KEY_PATH_DEFAULT
    C.PAGE_SIZE = 1000


# ===== 對映完整性：形狀要跟 RVTools 解析器的輸出一樣 =====

def _rvtools_reference(tmp):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "vInfo"
    ws.append(["VM", "DNS Name", "Powerstate", "Primary IP Address", "OS according to the VMware Tools", "Host", "VM UUID", "VM ID", "Cluster", "Path"])
    ws.append(["web01", "web01.corp.example", "poweredOn", "10.99.1.10", "Ubuntu", "esxi-a", "uuid-1", "vm-101", "CL", "[DS] web01/web01.vmx"])
    p = Path(tmp) / "ref.xlsx"
    wb.save(p)
    return rv.parse_rvtools(p)[0]


def _snapshot(world):
    s = mp.Snapshot(vms=world["vms"], hosts=world["hosts"], clusters=world["clusters"], datastores=world["dss"],
                    props=world["props"])
    for (rid, t), lst in world["rels"].items():
        ids = [x["identifier"] for x in lst]
        if rid.startswith("c") and t == "CHILD":
            s.cluster_hosts[rid] = ids
        elif rid.startswith("h") and t == "CHILD":
            s.host_vms[rid] = ids
        elif rid.startswith("d") and t == "CHILD":
            s.ds_vms[rid] = ids
        elif rid.startswith("d") and t == "PARENT":
            s.ds_hosts[rid] = ids
    return s


def test_對映後的記錄鍵與RVTools解析器完全一致():
    with tempfile.TemporaryDirectory() as tmp:
        ref = _rvtools_reference(tmp)
        out = mp.map_snapshot(_snapshot(build_world()))
        assert out["records"], "沒有記錄"
        for rec in out["records"]:
            assert set(rec) == set(ref), set(rec) ^ set(ref)
            assert rec["is_vm"] == 1


def test_對映內容_主機叢集datastore來自關聯_缺的退回屬性():
    out = mp.map_snapshot(_snapshot(build_world()))
    by = {r["vm_name"]: r for r in out["records"]}
    assert by["web01"]["esxi_host"] == "esxi-a.example.local" and by["web01"]["cluster"] == "CL_PROD"
    assert by["web01"]["datastore"] == "DS_PROD_A" and by["web01"]["powerstate"] == "poweredOn"
    assert by["web01"]["vm_uuid"] == "42301111-2222-3333-4444-555566667777"      # BIOS UUID，不是 instance UUID
    assert "inst-1" not in json.dumps({k: v for k, v in by["web01"].items() if k != "raw"})
    assert by["web01"]["dns_name"] == "web01.corp.example" and by["db01"]["hostname"] == "db01"   # 沒有 guest 主機名退回 VM 名
    assert by["db01"]["powerstate"] == "poweredOff"
    assert by["app01"]["vm_uuid"] is None                                         # 取不到就留空，不猜
    assert by["app01"]["esxi_host"] == "esxi-b.example.local"                       # 關聯有 h2 → app01


def test_額外分頁涵蓋下游讀的欄位():
    out = mp.map_snapshot(_snapshot(build_world()))
    sh = out["extra_sheets"]
    assert {"Host", "Cluster", "Datacenter", "ESX Version", "Object ID"} <= set(sh["vHost"][0])
    assert sh["vHost"][0]["ESX Version"] == "VMware ESXi 7.0.3 build-12345"
    d = sh["vDatastore"][0]
    assert {"Name", "Type", "Capacity MiB", "Free MiB", "In Use MiB", "Object ID", "Hosts"} <= set(d)
    assert d["Capacity MiB"] == 10 * 1024 and d["In Use MiB"] == 6 * 1024
    assert sh["vCPU"][0]["CPUs"] in (4, 2)
    mem = next(m for m in sh["vMemory"] if m["VM"] == "web01")
    assert mem["Size MiB"] == 8192 and {"VM", "VM UUID"} <= set(mem)
    assert next(x for x in sh["vDisk"] if x["VM"] == "web01")["Capacity MiB"] == 100 * 1024


# ===== 匯入 =====

def test_匯入走同一個寫入器_資產與來源紀錄與冪等():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp)
        try:
            r1 = vi.run_import(conn, "manual", "admin")
            assert r1["status"] == "ok", r1
            assert r1["summary"]["inserted"] == 3 and r1["summary"]["vms"] == 3
            srcs = {r["source"] for r in conn.execute("SELECT DISTINCT source FROM source_record")}
            assert "vcenter" in srcs and "vcenter_extra:vHost" in srcs and "vcenter_extra:vDatastore" in srcs
            hw = {r["hostname"]: r for r in conn.execute("SELECT * FROM hardware")}
            assert hw["web01.corp.example"]["is_vm"] == 1 and "Aria Operations" in hw["web01.corp.example"]["remark"]
            n_hw = conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0]
            n_src = conn.execute("SELECT COUNT(*) FROM source_record").fetchone()[0]
            r2 = vi.run_import(conn, "manual", "admin")
            assert r2["status"] == "ok" and r2["summary"]["inserted"] == 0 and r2["summary"]["updated"] == 3
            assert conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == n_hw           # 冪等：不增資產
            assert conn.execute("SELECT COUNT(*) FROM source_record").fetchone()[0] == n_src     # 冪等：不增來源列
            runs = vi.list_runs(conn)
            assert [x["status"] for x in runs] == ["ok", "ok"] and runs[0]["vms"] == 3
        finally:
            _cleanup(); conn.close()


def test_影響範圍的邊從Aria匯入的資料也建得起來():
    import ci_graph
    with tempfile.TemporaryDirectory() as tmp:
        conn, _f, _ = _setup(tmp)
        try:
            vi.run_import(conn, "manual", "admin")
            ci_graph.rebuild(conn)
            kinds = {r["edge_type"] for r in conn.execute("SELECT edge_type FROM ci_edge")}
            assert {"runs_on", "member_of"} <= kinds
        finally:
            _cleanup(); conn.close()


def test_翻頁會抓完整份():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp)
        try:
            C.PAGE_SIZE = 2
            r = vi.run_import(conn, "manual", "admin")
            assert r["summary"]["vms"] == 3
            vm_pages = [c["params"]["page"] for c in fake.calls if c["path"] == C.RESOURCES and c["params"]["resourceKind"] == C.KIND_VM]
            assert vm_pages == [0, 1]
        finally:
            _cleanup(); conn.close()


def test_登入失敗_不動資料_訊息不含密碼():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp, bad_login=True)
        try:
            r = vi.run_import(conn, "manual", "admin")
            assert r["status"] == "failed"
            assert conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 0
            run = vi.list_runs(conn)[0]
            assert run["status"] == "failed"
            blob = json.dumps(run, ensure_ascii=False) + r["message"]
            assert FAKE_PW not in blob
            t = vi.check_connection(conn)
            assert t["ok"] is False and FAKE_PW not in t["message"]
        finally:
            _cleanup(); conn.close()


def test_VM清單失敗是整次失敗_權杖不外洩():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp, fail_on=(C.KIND_VM,))
        try:
            r = vi.run_import(conn, "manual", "admin")
            assert r["status"] == "failed"
            assert FAKE_SESSION not in json.dumps(vi.list_runs(conn), ensure_ascii=False) + r["message"]
            assert conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 0
        finally:
            _cleanup(); conn.close()


def test_部分失敗_照樣匯入但標部分成功並列出哪一段():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp, fail_on=("/h2/relationships",))
        try:
            r = vi.run_import(conn, "manual", "admin")
            assert r["status"] == "partial", r
            assert r["summary"]["vms"] == 3                              # VM 都進來了
            assert any("esxi-b" in w for w in r["warnings"])
            assert FAKE_SESSION not in json.dumps(r, ensure_ascii=False)
            run = vi.list_runs(conn)[0]
            assert run["status"] == "partial" and run["error_count"] >= 1
            # app01 的主機退回屬性（summary|parentHost）
            row = json.loads(conn.execute("SELECT payload FROM source_record WHERE source='vcenter' AND payload LIKE '%app01%'").fetchone()[0])
            assert row["esxi_host"] == "esxi-b.example.local"
        finally:
            _cleanup(); conn.close()


def test_屬性整批失敗_也是部分成功():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp, fail_on=("propertyKeys",))
        try:
            r = vi.run_import(conn, "manual", "admin")
            assert r["status"] == "partial" and r["summary"]["vms"] == 3
        finally:
            _cleanup(); conn.close()


def test_權杖過期會重新登入一次():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp, expire_first_token=True)
        try:
            r = vi.run_import(conn, "manual", "admin")
            assert r["status"] == "ok"
            assert sum(1 for c in fake.calls if c["path"] == C.TOKEN_ACQUIRE) == 2
            assert any(c["path"] == C.TOKEN_RELEASE for c in fake.calls)       # 用完釋放
        finally:
            _cleanup(); conn.close()


def test_請求用vRealizeOpsToken標頭_只讀不寫():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp)
        try:
            vi.run_import(conn, "manual", "admin")
            for c in fake.calls:
                if c["path"] in (C.TOKEN_ACQUIRE, C.TOKEN_RELEASE):
                    continue
                assert c["headers"][C.AUTH_HEADER].startswith("vRealizeOpsToken ")
                assert c["method"] == "GET" or c["path"] == C.PROPERTIES_LATEST_QUERY
        finally:
            _cleanup(); conn.close()


# ===== 設定與機密 =====

def test_網址必須https_不接受帳密夾在網址_CA路徑要存在():
    for bad in ("http://aria.example.local", "https://u:p@aria.example.local", "https://aria.example.local/x", ""):
        try:
            ih.normalize_base_url(bad)
        except ih.ConfigError:
            pass
        else:
            raise AssertionError(bad)
    assert ih.normalize_base_url("https://aria.example.local:443/") == "https://aria.example.local:443"
    try:
        ih.check_ca_bundle("Z:/no/such/ca.pem")
    except ih.ConfigError:
        pass
    else:
        raise AssertionError("CA 路徑不存在要拒絕")


def test_預設一定驗證TLS_沒有關閉驗證的選項():
    t = ih.HttpxTransport(None)
    assert t._verify is True
    src = (ROOT / "APP" / "asset-module" / "backend" / "integration_http.py").read_text(encoding="utf-8")
    assert "verify=False" not in src and "verify = False" not in src


def test_密碼不回傳_只存加密憑證庫():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _f, _ = _setup(tmp)
        try:
            cfg = vi.get_config(conn)
            assert cfg["has_password"] is True and FAKE_PW not in json.dumps(cfg)
            row = conn.execute("SELECT kind, secret_enc FROM collect_credential WHERE name=?", (vi.CRED_NAME,)).fetchone()
            assert row["kind"] == "vrops" and FAKE_PW not in row["secret_enc"]
            vi.run_import(conn, "manual", "admin")
            audit = [dict(r) for r in conn.execute("SELECT * FROM credential_use_audit WHERE credential_name=?", (vi.CRED_NAME,))]
            assert audit and FAKE_PW not in json.dumps(audit)
        finally:
            _cleanup(); conn.close()


def test_密碼留空沿用_啟用前要有帳密():
    with tempfile.TemporaryDirectory() as tmp:
        conn, _f, _ = _setup(tmp)
        try:
            vi.save_config(conn, url=URL, auth_source="LOCAL", ca_bundle="", enabled=True, interval_hours=6)
            assert vi.get_config(conn)["has_password"] and vi.get_config(conn)["auth_source"] == "LOCAL"
            assert credential_store.get_for_use(conn, vi.CRED_NAME, key_path=vi.KEY_PATH)[1] == FAKE_PW
            conn.execute("DELETE FROM collect_credential")
            try:
                vi.save_config(conn, url=URL, auth_source="", ca_bundle="", enabled=True, interval_hours=6)
            except ih.ConfigError:
                pass
            else:
                raise AssertionError("沒有密碼不能啟用")
        finally:
            _cleanup(); conn.close()


def test_排程tick_間隔內不重跑_關閉不跑():
    with tempfile.TemporaryDirectory() as tmp:
        conn, fake, _ = _setup(tmp)
        try:
            assert vi.tick(conn)["status"] == "disabled"
            vi.save_config(conn, url=URL, auth_source="", ca_bundle="", enabled=True, interval_hours=24)
            assert vi.tick(conn)["status"] == "ok"
            assert vi.tick(conn)["status"] == "not_due"
        finally:
            _cleanup(); conn.close()


# ===== API：模組關閉 → 403、權限 =====

def _client(tmp):
    db_path = Path(tmp) / "api.db"
    db.init_db(db_path)

    def _dep():
        c = db.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _dep
    return TestClient(api.app), db_path


def _login(client, db_path, username):
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, username, auth.hash_password("test-password-123"))
    finally:
        conn.close()
    assert client.post("/api/auth/login", json={"username": username, "password": "test-password-123"}).status_code == 200


def test_模組關閉_所有端點403_開了才通():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        vi.KEY_PATH = str(Path(tmp) / "cred.key")
        try:
            assert client.get("/api/integrations/vrops").status_code == 401           # 沒登入
            _login(client, db_path, "admin")
            calls = [("get", "/api/integrations/vrops"), ("put", "/api/integrations/vrops"), ("post", "/api/integrations/vrops/test"),
                     ("post", "/api/integrations/vrops/run"), ("get", "/api/integrations/vrops/runs")]
            for m, path in calls:
                kw = {"json": {}} if m == "put" else {}
                assert getattr(client, m)(path, **kw).status_code == 403, path
            conn = db.get_connection(db_path)
            conn.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='vrops_api'"); conn.commit(); conn.close()
            assert client.get("/api/integrations/vrops").status_code == 200
            assert client.get("/api/integrations/vrops/runs").json() == {"runs": []}
        finally:
            api.app.dependency_overrides.clear(); _cleanup()


def test_非admin_不能設定_也不能執行_API不回密碼():
    with tempfile.TemporaryDirectory() as tmp:
        client, db_path = _client(tmp)
        fake = FakeAria(build_world())
        ih.TRANSPORT_FACTORY = lambda ca: fake
        vi.KEY_PATH = str(Path(tmp) / "cred.key")
        try:
            conn = db.get_connection(db_path)
            conn.execute("UPDATE feature_flags SET enabled=1 WHERE module_key='vrops_api'"); conn.commit(); conn.close()
            _login(client, db_path, "someone")
            assert client.get("/api/integrations/vrops").status_code == 200           # 一般登入者可看
            assert client.put("/api/integrations/vrops", json={"url": URL, "username": "u", "password": FAKE_PW}).status_code == 403
            assert client.post("/api/integrations/vrops/run").status_code == 403
            client.post("/api/auth/logout")
            _login(client, db_path, "admin")
            _c = db.get_connection(db_path)
            credential_store.save(_c, "aria-api", "vrops", "svc_aria", FAKE_PW, key_path=vi.KEY_PATH); _c.close()
            r = client.put("/api/integrations/vrops", json={"url": URL, "credential": "aria-api", "interval_hours": 12})
            assert r.status_code == 200 and r.json()["has_password"] is True and FAKE_PW not in r.text
            assert client.put("/api/integrations/vrops", json={"url": "http://bad"}).status_code == 400
            assert FAKE_PW not in client.get("/api/integrations/vrops").text
            t = client.post("/api/integrations/vrops/test")
            assert t.status_code == 200 and t.json()["ok"] is True
            run = client.post("/api/integrations/vrops/run").json()
            assert run["status"] == "ok" and FAKE_PW not in json.dumps(run)
            runs = client.get("/api/integrations/vrops/runs").json()["runs"]
            assert len(runs) == 1 and runs[0]["__no"] == runs[0]["id"] and FAKE_PW not in json.dumps(runs)
        finally:
            api.app.dependency_overrides.clear(); _cleanup()


def test_舊的資料夾監看沒被動到():
    import vcenter_autoimport
    assert hasattr(vcenter_autoimport, "pickup") and hasattr(vcenter_autoimport, "tick")
    assert any(r.path == "/api/vcenter-autoimport/run" for r in api.app.routes)


# Q91：這個檔案測的是「誰能做什麼」，不套 conftest 的「測試帳號預設 admin」相容層（見 tests/conftest.py）。
ROLE_AWARE_TESTS = True
