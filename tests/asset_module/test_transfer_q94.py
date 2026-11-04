"""檔案傳送／批次查詢（Q94）：閘門、VMware 天條、傳送流程、時段與排隊、檔案庫、只增不改的紀錄、指令紀錄。

全部用 FakeRunner（記憶體裡的假主機），**測試不碰網路**；時鐘用 transfer_policy.set_clock 注入。
使用者拍板的規則見 test_transfer_policy.py；這支測「把規則串起來之後」的行為。
"""
import datetime as dt
import hashlib
import io
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import transfer_policy as pol  # noqa: E402
import transfer_runner as rn  # noqa: E402
import transfer_service as svc  # noqa: E402
import transfer_store as st  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

PW = "test-password-123"
SAT10 = pol.at(2026, 10, 31, 10, 0)          # 週六：什麼都能傳
THU10 = pol.at(2026, 10, 29, 10, 0)          # 週四開盤
THU23 = pol.at(2026, 10, 29, 23, 0)

HOSTS = [  # (序號, 主機名, IP, OS, 機房)
    ("L1", "lx1", "10.99.8.1", "Red Hat Enterprise Linux 8", "板橋"),
    ("L2", "lx2", "10.99.8.2", "Red Hat Enterprise Linux 8", "內湖"),
    ("A1", "aix1", "10.99.8.3", "AIX 7.2", "板橋"),
    ("W1", "win1", "10.99.9.1", "Windows Server 2019", "敦南"),
    ("W2", "win2", "10.99.9.2", "Windows Server 2019", "敦南"),
    ("V1", "esx1", "10.99.7.1", "VMware ESXi 7.0", "板橋"),
    ("N1", "lx-not-onboarded", "10.99.8.9", "Red Hat Enterprise Linux 8", "板橋"),
]


class Env:
    pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    import credential_store
    import win_onboard

    e = Env()
    e.path = tmp_path / "t.db"
    db.init_db(e.path)
    monkeypatch.setenv("WEBIT3_TRANSFER_DIR", str(tmp_path / "lib"))
    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    c = db.get_connection(e.path)
    quick = generate_password_hash(PW, method="pbkdf2:sha256:1000")        # 測試用快雜湊（scrypt 太慢，驗證邏輯一樣）
    db.create_user(c, "admin", quick)
    db.create_user(c, "bob", quick)
    for serial, name, ip, os_, room in HOSTS:
        db.insert_hardware(c, asset_serial=serial, hostname=name, ip=ip, os=os_, asset_status="使用中",
                           physical_location=room, environment="正式")
        if serial != "N1":
            c.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = ?", (serial,))
    c.execute("UPDATE feature_flags SET enabled = 1 WHERE module_key = 'file_transfer'")
    st.ensure_tables(c)
    c.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('ft_wave_pause_s', '0')")
    c.commit()
    c.close()
    monkeypatch.setattr(win_onboard, "collect_block", lambda *a, **k: None)
    monkeypatch.setattr(credential_store, "pick_for_host", lambda *a, **k: "wincred")
    monkeypatch.setattr(credential_store, "get_for_use", lambda *a, **k: ("svc-user", "pw-secret"))
    monkeypatch.setattr(credential_store, "audit_use", lambda *a, **k: None)

    def _get_db():
        conn = db.get_connection(e.path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    e.fake = rn.FakeRunner()
    rn.set_runner(e.fake)
    svc.SYNC = True
    monkeypatch.setattr(svc, "_sleep", lambda s: None)
    monkeypatch.setattr(st, "DISK_RESERVE_BYTES", 0)               # 開發機磁碟剩多少不影響測試
    pol.set_clock(lambda: SAT10)
    e.set_now = lambda t: pol.set_clock(lambda: t)

    def client(user="admin"):
        cl = TestClient(api.app)
        r = cl.post("/api/auth/login", json={"username": user, "password": PW})
        assert r.status_code == 200, r.text
        return cl

    e.client = client
    e.admin = client("admin")
    e.conn = lambda: db.get_connection(e.path)
    yield e
    pol.set_clock(None)
    rn.set_runner(None)
    svc.SYNC = False
    api.app.dependency_overrides.clear()


def raw(cl, name, data):
    """檔案庫上傳＝原始位元組＋X-Filename 標頭（不是 multipart）。"""
    from urllib.parse import quote
    return cl.post("/api/transfer/library", content=data,
                   headers={"X-Filename": quote(name), "Content-Type": "application/octet-stream"})


def upload_lib(cl, name="setup.bin", data=b"hello-world" * 100):
    r = raw(cl, name, data)
    assert r.status_code == 200, r.text
    return r.json()


def make_job(cl, f, ips, **kw):
    body = {"file_id": f["id"], "ips": ips, "confirm_count": len(ips)}
    body.update(kw)
    return cl.post("/api/transfer/jobs", json=body)


# ====================================================================== 閘門
def _transfer_routes():
    out = []
    for r in api.app.routes:
        p = getattr(r, "path", "")
        if p.startswith("/api/transfer/"):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                out.append((m, p))
    return out


STUBS = {"{file_id}": "1", "{job_id}": "1", "{log_id}": "1"}


def _call(cl, method, path):
    for k, v in STUBS.items():
        path = path.replace(k, v)
    if method == "POST" and path.endswith("/library"):
        return raw(cl, "a.bin", b"x")
    if method in ("POST", "PUT", "PATCH"):
        return cl.request(method, path, json={})
    return cl.request(method, path)


def test_有抓到所有檔案傳送端點():
    assert len(_transfer_routes()) >= 20


def test_模組關著時每支端點都是403_就算是admin(env):
    c = env.conn()
    c.execute("UPDATE feature_flags SET enabled = 0 WHERE module_key = 'file_transfer'")
    c.commit()
    c.close()
    bad = []
    for m, p in _transfer_routes():
        r = _call(env.admin, m, p)
        if r.status_code != 403:
            bad.append(f"{m} {p} -> {r.status_code}")
        elif "未啟用" not in r.json()["detail"]:
            bad.append(f"{m} {p} 訊息不對：{r.json()['detail']}")
    assert not bad, bad


def test_預設是關的(tmp_path):
    p = tmp_path / "fresh.db"
    db.init_db(p)
    c = db.get_connection(p)
    row = c.execute("SELECT enabled FROM feature_flags WHERE module_key = 'file_transfer'").fetchone()
    c.close()
    assert row is not None and row["enabled"] == 0


def test_登入停用時也能用_使用者2026_10_30決定取消限制(env, monkeypatch):
    # 原本是「登入停用一律 403」；使用者決定測試階段取消（221 不開登入、又要測檔案傳送）。
    monkeypatch.setenv(api.AUTH_DISABLED_ENV, "1")
    anon = TestClient(api.app)
    r = anon.get("/api/transfer/status")
    assert r.status_code == 200, r.text


def test_登入停用寫在設定裡也能用(env):
    c = env.conn()
    c.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, '1')", (api.AUTH_DISABLED_SETTING,))
    c.commit()
    c.close()
    assert TestClient(api.app).get("/api/transfer/status").status_code == 200


def test_沒登入是401(env):
    assert TestClient(api.app).get("/api/transfer/status").status_code == 401


def test_不是admin一律403(env):
    bob = env.client("bob")
    bad = []
    for m, p in _transfer_routes():
        r = _call(bob, m, p)
        if r.status_code != 403 or "admin" not in r.json()["detail"]:
            bad.append(f"{m} {p} -> {r.status_code}")
    assert not bad, bad


def test_admin可以用(env):
    r = env.admin.get("/api/transfer/status")
    assert r.status_code == 200 and r.json()["user"] == "admin"
    assert r.json()["folders"]["windows"] == ["D:\\Temp", "C:\\Windows\\Temp"] and r.json()["folders"]["linux"] == ["/tmp"]


# ---------------------------------------------------------------- 模組開關本身
def test_模組開關只有admin能動_而且留稽核(env):
    c = env.conn()
    c.execute("UPDATE feature_flags SET enabled = 0 WHERE module_key = 'file_transfer'")
    c.commit()
    c.close()
    bob = env.client("bob")
    assert bob.put("/api/feature-flags/file_transfer", json={"enabled": True}).status_code == 403
    r = env.admin.put("/api/feature-flags/file_transfer", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] == 1
    assert env.admin.get("/api/transfer/status").status_code == 200
    assert env.admin.put("/api/feature-flags/file_transfer", json={"enabled": False}).status_code == 200
    assert env.admin.get("/api/transfer/status").status_code == 403
    c = env.conn()
    rows = c.execute("SELECT by_user, action FROM ft_audit ORDER BY id").fetchall()
    c.close()
    assert [(r["by_user"], r["action"]) for r in rows] == [("admin", "module_on"), ("admin", "module_off")]


def test_登入停用時模組開關可以動(env, monkeypatch):
    monkeypatch.setenv(api.AUTH_DISABLED_ENV, "1")
    assert TestClient(api.app).put("/api/feature-flags/file_transfer", json={"enabled": True}).status_code == 200


def test_其他模組的開關行為不變(env):
    # Q91：功能開關的切換改成「管理員才能動」（決策：一般帳號不能切功能開關）。
    # 原本這裡斷言 bob（非 admin）切 golive 開關是 200；那是 Q91 之前「除了檔案傳送都沒擋」的行為，刻意改變。
    bob = env.client("bob")
    assert bob.put("/api/feature-flags/golive", json={"enabled": True}).status_code == 403
    assert env.admin.put("/api/feature-flags/golive", json={"enabled": True}).status_code == 200


# ====================================================================== 主機清單與 VMware 天條
def test_主機清單不含VMware_未納管_並且分箱(env):
    r = env.admin.get("/api/transfer/hosts")
    assert r.status_code == 200
    hs = {h["ip"]: h for h in r.json()["hosts"]}
    assert set(hs) == {"10.99.8.1", "10.99.8.2", "10.99.8.3", "10.99.9.1", "10.99.9.2"}
    assert "10.99.7.1" not in hs and "10.99.8.9" not in hs
    assert r.json()["excluded"]["vmware"] >= 1
    assert hs["10.99.8.3"]["platform"] == "aix" and hs["10.99.8.3"]["box"] == "linux"
    assert hs["10.99.9.1"]["box"] == "windows"
    assert [h["__no"] for h in r.json()["hosts"]] == [1, 2, 3, 4, 5]


def test_VMware主機不能被選成目標(env):
    f = upload_lib(env.admin)
    for call in (
        lambda: make_job(env.admin, f, ["10.99.7.1"]),
        lambda: env.admin.post("/api/transfer/preflight", json={"file_id": f["id"], "ips": ["10.99.7.1"]}),
        lambda: env.admin.post("/api/transfer/commands", json={"box": "linux", "ips": ["10.99.7.1"], "command": "ls"}),
        lambda: env.admin.post("/api/transfer/pull", json={"ip": "10.99.7.1", "name": "a.bin"}),
        lambda: env.admin.get("/api/transfer/host-files", params={"ip": "10.99.7.1"}),
    ):
        r = call()
        assert r.status_code == 400 and "不在可選主機" in r.json()["detail"], r.text
    assert env.fake.calls == []                               # 連一次遠端動作都沒有


def test_現場發現是VMware也拒絕_登記錯了也擋(env):
    """資產庫登記成 Linux，但連上去發現是 ESXi／vCenter：守門句在每個動作最前面，fail-closed。"""
    env.fake.vmware_ips.add("10.99.8.1")
    f = upload_lib(env.admin)
    r = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2"])
    assert r.status_code == 200
    hosts = {h["ip"]: h for h in r.json()["hosts"]}
    assert hosts["10.99.8.1"]["status"] == "failed" and "VMware" in hosts["10.99.8.1"]["message"]
    assert hosts["10.99.8.2"]["status"] == "ok"
    assert env.fake.files("10.99.8.1", "/tmp") == {}           # 一個位元組都沒寫到 VMware 設備


def test_指令碰到現場VMware也拒絕且有紀錄(env):
    env.fake.vmware_ips.add("10.99.8.1")
    r = env.admin.post("/api/transfer/commands", json={"box": "linux", "ips": ["10.99.8.1", "10.99.8.2"], "command": "hostname"})
    assert r.status_code == 200
    d = env.admin.get(f"/api/transfer/commands/{r.json()['log_id']}").json()
    by = {x["ip"]: x for x in d["results"]}
    assert by["10.99.8.1"]["ok"] == 0 and "已拒絕" in by["10.99.8.1"]["output"]
    assert by["10.99.8.2"]["ok"] == 1


def test_不能混著選兩個箱子(env):
    f = upload_lib(env.admin)
    r = make_job(env.admin, f, ["10.99.8.1", "10.99.9.1"])
    assert r.status_code == 400 and "同一個箱子" in r.json()["detail"]


# ====================================================================== 檔案庫
def test_上傳到檔案庫_算雜湊_檔名要合規(env):
    data = b"abc" * 1000
    f = upload_lib(env.admin, "a.bin", data)
    assert f["sha256"] == hashlib.sha256(data).hexdigest() and f["size"] == len(data)
    assert "stored_name" not in f
    assert env.admin.get("/api/transfer/library").json()["files"][0]["name"] == "a.bin"
    r = raw(env.admin, "a.bin", b"y")
    assert r.status_code == 409 and "已經有" in r.json()["detail"]
    r = raw(env.admin, "bad;name.bin", b"y")
    assert r.status_code == 400
    r = raw(env.admin, "empty.bin", b"")
    assert r.status_code == 400 and "空" in r.json()["detail"]


def test_檔案庫總量與單檔上限(env):
    r = env.admin.put("/api/transfer/settings", json={"ft_library_cap_gb": 0.00002, "ft_file_cap_gb": 0.00001})
    assert r.status_code == 200
    # 單檔上限約 10737 位元組、總量上限約 21474 位元組
    r = raw(env.admin, "big.bin", b"x" * 20000)
    assert r.status_code == 413 and "檔案太大" in r.json()["detail"]
    upload_lib(env.admin, "a.bin", b"x" * 9000)
    upload_lib(env.admin, "b.bin", b"x" * 9000)
    r = raw(env.admin, "c.bin", b"x" * 9000)
    assert r.status_code == 413 and "檔案太大" in r.json()["detail"]
    lib = Path(st.library_dir(env.conn()))
    assert len([p for p in lib.iterdir() if p.is_file()]) == 2         # 失敗的不留暫存


def test_設定值要合理(env):
    assert env.admin.put("/api/transfer/settings", json={"ft_retention_days": 0}).status_code == 400
    assert env.admin.put("/api/transfer/settings", json={"ft_retention_days": 10}).json()["ft_retention_days"] == 10


def test_保留天數到了自動清除_使用中的不清(env):
    a = upload_lib(env.admin, "old.bin")
    b = upload_lib(env.admin, "busy.bin")
    c = env.conn()
    old = (pol.now() - dt.timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("UPDATE ft_file SET uploaded_at = ?", (old,))
    c.execute("INSERT INTO ft_job (kind, box, file_id, file_name, size, dest_folder, status, created_at) "
              "VALUES ('upload', 'linux', ?, 'busy.bin', 1, '/tmp', 'waiting_window', ?)", (b["id"], old))
    c.commit()
    assert st.purge_expired(c) == 1
    names = [f["name"] for f in st.list_files(c)]
    assert names == ["busy.bin"]
    assert c.execute("SELECT delete_reason FROM ft_file WHERE id = ?", (a["id"],)).fetchone()[0] == "retention"
    c.close()
    assert not any(p.is_file() and p.name != ".tmp" for p in (st.library_dir(env.conn()) / ".").iterdir()
                   if p.stat().st_size == 0)


def test_手動刪除_傳送中的不能刪_刪了有稽核(env):
    f = upload_lib(env.admin)
    c = env.conn()
    c.execute("INSERT INTO ft_job (kind, box, file_id, file_name, size, dest_folder, status, created_at) "
              "VALUES ('upload', 'linux', ?, 'setup.bin', 1, '/tmp', 'running', '2026-10-31 10:00:00')", (f["id"],))
    c.commit()
    c.close()
    assert env.admin.delete(f"/api/transfer/library/{f['id']}").status_code == 409
    c = env.conn()
    c.execute("UPDATE ft_job SET status = 'done'")
    c.commit()
    c.close()
    assert env.admin.delete(f"/api/transfer/library/{f['id']}").status_code == 200
    assert env.admin.delete(f"/api/transfer/library/{f['id']}").status_code == 404
    assert env.admin.get("/api/transfer/library").json()["files"] == []
    assert any(a["action"] == "library_delete" for a in env.admin.get("/api/transfer/audit").json()["rows"])


# ====================================================================== 傳送流程
def test_傳到多台_逐台驗雜湊(env):
    data = b"payload-" * 5000
    f = upload_lib(env.admin, "tool.zip", data)
    r = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2", "10.99.8.3"], concurrency=2, rate_mbps=5)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "done" and j["ok_count"] == 3 and j["fail_count"] == 0
    for h in j["hosts"]:
        assert h["status"] == "ok" and h["remote_sha256"] == f["sha256"] and h["remote_path"] == "/tmp/tool.zip"
    for ip in ("10.99.8.1", "10.99.8.2", "10.99.8.3"):
        files = env.fake.files(ip, "/tmp")
        assert files == {"tool.zip": data}                   # 正式檔在、暫存檔不在
    assert {rate for _, rate in env.fake.rates} == {5}


def test_Windows預設傳到D_Temp_不限速(env):
    f = upload_lib(env.admin, "w.msi", b"msi" * 1000)
    j = make_job(env.admin, f, ["10.99.9.1", "10.99.9.2"]).json()
    assert j["status"] == "done" and j["dest_folder"] == "D:\\Temp" and j["box"] == "windows"
    assert env.fake.files("10.99.9.1", "D:\\Temp")["w.msi"] == b"msi" * 1000
    assert j["hosts"][0]["remote_path"] == "D:\\Temp\\w.msi"
    assert {rate for _, rate in env.fake.rates} == {None}    # Windows 這條傳輸不支援限速


def test_傳到C_Windows_Temp(env):
    f = upload_lib(env.admin, "w.msi", b"x" * 100)
    j = make_job(env.admin, f, ["10.99.9.1"], folder="c:\\windows\\temp").json()
    assert j["dest_folder"] == "C:\\Windows\\Temp" and j["status"] == "done"


def test_資料夾必須是白名單(env):
    f = upload_lib(env.admin)
    for folder in ["/etc", "/tmp/../etc", "D:\\Temp", "/tmp/sub"]:
        r = make_job(env.admin, f, ["10.99.8.1"], folder=folder)
        assert r.status_code == 400, (folder, r.text)
    for folder in ["E:\\Temp", "C:\\Windows", "\\\\h\\s", "D:\\Temp\\..\\x", "/tmp"]:
        r = make_job(env.admin, f, ["10.99.9.1"], folder=folder)
        assert r.status_code == 400, (folder, r.text)
    assert env.fake.calls == []


def test_要輸入台數確認_不符不送(env):
    f = upload_lib(env.admin)
    r = env.admin.post("/api/transfer/jobs", json={"file_id": f["id"], "ips": ["10.99.8.1", "10.99.8.2"], "confirm_count": 1})
    assert r.status_code == 400 and "請輸入台數 2" in r.json()["detail"]
    r = env.admin.post("/api/transfer/jobs", json={"file_id": f["id"], "ips": ["10.99.8.1", "10.99.8.2"]})
    assert r.status_code == 400
    assert env.admin.get("/api/transfer/jobs").json()["jobs"] == []


def test_總量超過20GB要二次確認(env, monkeypatch):
    monkeypatch.setattr(pol, "BIG_BATCH_BYTES", 1000)
    f = upload_lib(env.admin, "a.bin", b"x" * 600)
    r = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2"])
    assert r.status_code == 409 and "20 GB" in r.json()["detail"]
    assert make_job(env.admin, f, ["10.99.8.1", "10.99.8.2"], confirm_big=True).status_code == 200


def test_雜湊不符_失敗_且目的地不留檔(env):
    env.fake.corrupt_ips.add("10.99.8.2")
    f = upload_lib(env.admin, "tool.zip", b"data" * 500)
    j = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2"]).json()
    by = {h["ip"]: h for h in j["hosts"]}
    assert by["10.99.8.1"]["status"] == "ok"
    assert by["10.99.8.2"]["status"] == "failed" and "SHA256 不符" in by["10.99.8.2"]["message"]
    assert env.fake.files("10.99.8.2", "/tmp") == {}                 # 壞檔和暫存檔都沒留
    assert j["status"] == "partial" and j["ok_count"] == 1 and j["fail_count"] == 1


def test_預檢失敗_沒有資料夾_空間不足_不會自動處理(env):
    env.fake.no_folder.add(("10.99.9.2", "D:\\Temp"))
    env.fake.free_bytes["10.99.9.1"] = 100
    f = upload_lib(env.admin, "w.msi", b"x" * 1000)
    pre = env.admin.post("/api/transfer/preflight", json={"file_id": f["id"], "ips": ["10.99.9.1", "10.99.9.2"]}).json()
    assert pre["checked"] and len(pre["problems"]) == 2
    msgs = {p["ip"]: p["message"] for p in pre["problems"]}
    assert "沒有 D:\\Temp 資料夾" in msgs["10.99.9.2"] and "可用空間不足" in msgs["10.99.9.1"]
    j = make_job(env.admin, f, ["10.99.9.1", "10.99.9.2"]).json()
    assert j["status"] == "failed" and all(h["status"] == "failed" for h in j["hosts"])
    assert env.fake.files("10.99.9.1", "D:\\Temp") == {} and ("10.99.9.2", "D:\\Temp") in env.fake.no_folder
    assert not any(c[0] == "upload" for c in env.fake.calls)         # 預檢失敗就一個位元組都沒送


def test_空間要檔案的1點5倍(env):
    f = upload_lib(env.admin, "a.bin", b"x" * 1000)
    env.fake.free_bytes["10.99.8.1"] = 1499
    env.fake.free_bytes["10.99.8.2"] = 1500
    j = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2"]).json()
    by = {h["ip"]: h["status"] for h in j["hosts"]}
    assert by == {"10.99.8.1": "failed", "10.99.8.2": "ok"}


def test_已有同名檔_內容相同略過_不同要勾覆蓋(env):
    f = upload_lib(env.admin, "a.bin", b"new-content")
    env.fake.put("10.99.8.1", "/tmp", "a.bin", b"new-content")
    env.fake.put("10.99.8.2", "/tmp", "a.bin", b"OLD-content")
    j = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2"]).json()
    by = {h["ip"]: h for h in j["hosts"]}
    assert by["10.99.8.1"]["status"] == "ok" and "略過" in by["10.99.8.1"]["message"]
    assert by["10.99.8.2"]["status"] == "failed" and "覆蓋" in by["10.99.8.2"]["message"]
    assert env.fake.files("10.99.8.2", "/tmp")["a.bin"] == b"OLD-content"        # 沒被蓋
    j2 = make_job(env.admin, f, ["10.99.8.2"], overwrite=True).json()
    assert j2["status"] == "done" and env.fake.files("10.99.8.2", "/tmp")["a.bin"] == b"new-content"


def test_連不上_失敗_有原因(env):
    env.fake.down_ips.add("10.99.8.1")
    f = upload_lib(env.admin)
    j = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2"]).json()
    assert {h["ip"]: h["status"] for h in j["hosts"]} == {"10.99.8.1": "failed", "10.99.8.2": "ok"}
    assert "連不上" in j["hosts"][0]["message"]


def test_沒預期的例外也會被接住_不會整批消失(env, monkeypatch):
    def boom(*a, **k):
        raise KeyError("oops")
    monkeypatch.setattr(env.fake, "finalize", boom)
    f = upload_lib(env.admin)
    j = make_job(env.admin, f, ["10.99.8.1"]).json()
    assert j["hosts"][0]["status"] == "failed" and "未預期的錯誤" in j["hosts"][0]["message"] and j["status"] == "failed"


# ---------------------------------------------------------------- 時段
def test_現在不能傳就排隊等時段_時間到自動開始(env, monkeypatch):
    monkeypatch.setattr(pol, "SMALL_MAX", 10)
    monkeypatch.setattr(pol, "MID_MAX", 100)
    f = upload_lib(env.admin, "mid.bin", b"x" * 50)                  # 中型檔
    env.set_now(THU10)                                                # 週四開盤
    pre = env.admin.post("/api/transfer/preflight", json={"file_id": f["id"], "ips": ["10.99.8.1"]}).json()
    assert pre["verdict"]["ok"] is False and pre["verdict"]["next_at"] == "2026-10-29 15:00:00"
    j = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2"]).json()
    assert j["status"] == "waiting_window" and j["not_before"] == "2026-10-29 15:00:00"
    assert all(h["status"] == "queued" for h in j["hosts"])
    assert not any(c[0] == "upload" for c in env.fake.calls)         # 時段不對：一台都沒開始
    assert svc.resume_waiting(env.path) == 0                          # 還沒到
    env.set_now(pol.at(2026, 10, 29, 14, 59))
    assert svc.resume_waiting(env.path) == 0
    env.set_now(pol.at(2026, 10, 29, 15, 0))
    assert svc.resume_waiting(env.path) == 1                          # 到了自動開始
    j = env.admin.get(f"/api/transfer/jobs/{j['id']}").json()
    assert j["status"] == "done" and j["ok_count"] == 2


def test_超過100MB平日只能夜間(env, monkeypatch):
    monkeypatch.setattr(pol, "SMALL_MAX", 10)
    monkeypatch.setattr(pol, "MID_MAX", 100)
    f = upload_lib(env.admin, "big.bin", b"x" * 500)
    env.set_now(pol.at(2026, 10, 29, 16, 0))                          # 週四收盤後：中型可以，大型還不行
    j = make_job(env.admin, f, ["10.99.8.1"]).json()
    assert j["status"] == "waiting_window" and j["not_before"] == "2026-10-29 22:00:00"
    env.set_now(THU23)
    assert svc.resume_waiting(env.path) == 1
    assert env.admin.get(f"/api/transfer/jobs/{j['id']}").json()["status"] == "done"


def test_週末什麼都能傳(env, monkeypatch):
    monkeypatch.setattr(pol, "SMALL_MAX", 10)
    monkeypatch.setattr(pol, "MID_MAX", 100)
    f = upload_lib(env.admin, "big.bin", b"x" * 500)
    assert make_job(env.admin, f, ["10.99.8.1"]).json()["status"] == "done"       # SAT10


def test_平日0815之後不開新的一波(env, monkeypatch):
    monkeypatch.setattr(pol, "SMALL_MAX", 10)
    monkeypatch.setattr(pol, "MID_MAX", 100)
    f = upload_lib(env.admin, "mid.bin", b"x" * 50)
    env.set_now(pol.at(2026, 10, 29, 8, 20))                          # 大小規則還允許（08:30 才擋），但 08:15 後不開新的一波
    j = make_job(env.admin, f, ["10.99.8.1"]).json()
    assert j["status"] == "waiting_window" and j["not_before"] == "2026-10-29 15:00:00"


def test_時段中途關了_剩下的台留著等下一個時段(env, monkeypatch):
    monkeypatch.setattr(pol, "SMALL_MAX", 10)
    monkeypatch.setattr(pol, "MID_MAX", 100)
    f = upload_lib(env.admin, "mid.bin", b"x" * 50)
    env.set_now(pol.at(2026, 10, 29, 8, 0))                           # 08:00 還可以
    # 第一台傳的當下時間走到 08:31
    env.fake.on_upload = lambda ip: env.set_now(pol.at(2026, 10, 29, 8, 31))
    j = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2"], concurrency=1).json()
    by = {h["ip"]: h["status"] for h in j["hosts"]}
    assert by["10.99.8.1"] == "ok" and by["10.99.8.2"] == "queued"
    assert j["status"] == "waiting_window"
    env.fake.on_upload = None
    env.set_now(pol.at(2026, 10, 29, 15, 0))
    svc.resume_waiting(env.path)
    j = env.admin.get(f"/api/transfer/jobs/{j['id']}").json()
    assert j["status"] == "done" and j["ok_count"] == 2


def test_模組停用時等待中的傳送不會偷跑(env, monkeypatch):
    monkeypatch.setattr(pol, "SMALL_MAX", 10)
    monkeypatch.setattr(pol, "MID_MAX", 100)
    f = upload_lib(env.admin, "mid.bin", b"x" * 50)
    env.set_now(THU10)
    j = make_job(env.admin, f, ["10.99.8.1"]).json()
    c = env.conn()
    c.execute("UPDATE feature_flags SET enabled = 0 WHERE module_key = 'file_transfer'")
    c.commit()
    c.close()
    env.set_now(pol.at(2026, 10, 29, 15, 30))
    assert svc.resume_waiting(env.path) == 0
    assert not any(c[0] == "upload" for c in env.fake.calls)


# ---------------------------------------------------------------- 分波、停止、重送
def _more_linux_hosts(env, n):
    c = env.conn()
    for i in range(n):
        db.insert_hardware(c, asset_serial=f"X{i}", hostname=f"bulk{i}", ip=f"10.99.20.{i + 1}",
                           os="Red Hat Enterprise Linux 8", asset_status="使用中", physical_location="板橋")
        c.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = ?", (f"X{i}",))
    c.commit()
    c.close()
    return [f"10.99.20.{i + 1}" for i in range(n)]


def test_每波10台_波次之間暫停(env, monkeypatch):
    ips = _more_linux_hosts(env, 25)
    c = env.conn()
    c.execute("UPDATE app_settings SET value = '7' WHERE key = 'ft_wave_pause_s'")
    c.commit()
    c.close()
    sleeps = []
    monkeypatch.setattr(svc, "_sleep", lambda s: sleeps.append(s))
    f = upload_lib(env.admin)
    pre = env.admin.post("/api/transfer/preflight", json={"file_id": f["id"], "ips": ips}).json()
    assert pre["estimate"]["waves"] == 3
    j = make_job(env.admin, f, ips).json()
    assert j["status"] == "done" and j["ok_count"] == 25
    assert len(sleeps) == 2 * 7 and set(sleeps) == {1}                      # 兩次波間暫停（每次 7 個 1 秒，可隨時被停止打斷）


def test_預檢台數少時逐台檢查(env):
    ips = _more_linux_hosts(env, 25)
    f = upload_lib(env.admin)
    pre = env.admin.post("/api/transfer/preflight", json={"file_id": f["id"], "ips": ips}).json()
    assert pre["checked"] is True and pre["problems"] == []


def test_停止後未開始的台不再送(env):
    f = upload_lib(env.admin)
    state = {}

    def on_upload(ip):
        if not state:
            state["job"] = env.conn().execute("SELECT MAX(id) FROM ft_job").fetchone()[0]
            r = env.admin.post(f"/api/transfer/jobs/{state['job']}/stop")
            assert r.status_code == 200
    env.fake.on_upload = on_upload
    j = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2", "10.99.8.3"], concurrency=1).json()
    j = env.admin.get(f"/api/transfer/jobs/{j['id']}").json()
    by = {h["ip"]: h["status"] for h in j["hosts"]}
    assert by["10.99.8.1"] in ("ok", "failed")
    assert by["10.99.8.2"] == "stopped" and by["10.99.8.3"] == "stopped"
    assert j["status"] == "stopped" and env.fake.files("10.99.8.3", "/tmp") == {}
    assert env.admin.post(f"/api/transfer/jobs/{j['id']}/stop").status_code == 400      # 已結束


def test_重送失敗的_只送失敗的(env):
    env.fake.down_ips.add("10.99.8.2")
    f = upload_lib(env.admin)
    j = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2"]).json()
    assert j["status"] == "partial"
    env.fake.down_ips.clear()
    env.fake.calls.clear()
    r = env.admin.post(f"/api/transfer/jobs/{j['id']}/retry-failed")
    assert r.status_code == 200
    j2 = r.json()
    assert j2["id"] != j["id"] and [h["ip"] for h in j2["hosts"]] == ["10.99.8.2"] and j2["status"] == "done"
    assert all(ip == "10.99.8.2" for _, ip in env.fake.calls)               # 成功的那台一次都沒再碰
    assert env.admin.post(f"/api/transfer/jobs/{j2['id']}/retry-failed").status_code == 400  # 沒有失敗的了


def test_服務重啟時還在跑的台標中斷(env):
    f = upload_lib(env.admin)
    j = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2"]).json()
    c = env.conn()
    c.execute("UPDATE ft_job SET status = 'running' WHERE id = ?", (j["id"],))
    c.execute("UPDATE ft_job_host SET status = 'running' WHERE job_id = ? AND ip = '10.99.8.2'", (j["id"],))
    c.commit()
    assert svc.recover_after_restart(c) == 1
    c.close()
    j = env.admin.get(f"/api/transfer/jobs/{j['id']}").json()
    assert j["status"] == "partial"
    assert [h for h in j["hosts"] if h["ip"] == "10.99.8.2"][0]["message"].startswith("服務重啟中斷")


# ---------------------------------------------------------------- 抓回
def test_抓回一台_進檔案庫_雜湊對(env):
    env.fake.put("10.99.8.1", "/tmp", "core.dump", b"D" * 3000)
    r = env.admin.post("/api/transfer/pull", json={"ip": "10.99.8.1", "name": "core.dump"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"
    files = env.admin.get("/api/transfer/library").json()["files"]
    assert files[0]["name"] == "core.dump" and files[0]["source"] == "pull" and files[0]["from_host"] == "10.99.8.1"
    assert files[0]["sha256"] == hashlib.sha256(b"D" * 3000).hexdigest()


def test_抓回遵守時段_按實際大小(env, monkeypatch):
    monkeypatch.setattr(pol, "SMALL_MAX", 10)
    monkeypatch.setattr(pol, "MID_MAX", 100)
    env.fake.put("10.99.8.1", "/tmp", "big.dump", b"D" * 50)
    env.set_now(THU10)
    r = env.admin.post("/api/transfer/pull", json={"ip": "10.99.8.1", "name": "big.dump"})
    assert r.status_code == 409 and "現在不能抓" in r.json()["detail"] and "15:00" in r.json()["detail"]
    assert env.admin.get("/api/transfer/library").json()["files"] == []
    env.fake.put("10.99.8.1", "/tmp", "small.dump", b"D" * 5)
    assert env.admin.post("/api/transfer/pull", json={"ip": "10.99.8.1", "name": "small.dump"}).status_code == 200


def test_抓回受檔案庫上限限制_壞雜湊丟棄(env):
    env.admin.put("/api/transfer/settings", json={"ft_file_cap_gb": 0.000001})            # 約 1073 位元組
    env.fake.put("10.99.8.1", "/tmp", "big.dump", b"D" * 5000)
    r = env.admin.post("/api/transfer/pull", json={"ip": "10.99.8.1", "name": "big.dump"})
    assert r.status_code == 400 and "單一檔案上限" in r.json()["detail"]
    env.admin.put("/api/transfer/settings", json={"ft_file_cap_gb": 1})
    env.fake.corrupt_ips.add("10.99.8.1")
    env.fake.put("10.99.8.1", "/tmp", "ok.dump", b"D" * 500)
    r = env.admin.post("/api/transfer/pull", json={"ip": "10.99.8.1", "name": "ok.dump"})
    assert r.json()["status"] == "failed" and "不符" in r.json()["hosts"][0]["message"]
    assert env.admin.get("/api/transfer/library").json()["files"] == []
    assert list((st.library_dir(env.conn()) / ".tmp").iterdir()) == []                     # 暫存清掉


def test_抓回只能從白名單資料夾(env):
    r = env.admin.post("/api/transfer/pull", json={"ip": "10.99.8.1", "folder": "/etc", "name": "passwd"})
    assert r.status_code == 400
    r = env.admin.post("/api/transfer/pull", json={"ip": "10.99.8.1", "name": "../etc/passwd"})
    assert r.status_code == 400
    assert not any(c[0] in ("download", "probe") for c in env.fake.calls)


def test_右邊窗格列出白名單資料夾的檔案(env):
    env.fake.put("10.99.9.1", "D:\\Temp", "a.zip", b"1234")
    r = env.admin.get("/api/transfer/host-files", params={"ip": "10.99.9.1"})
    assert r.status_code == 200 and r.json()["folder"] == "D:\\Temp" and r.json()["files"][0]["name"] == "a.zip"
    assert env.admin.get("/api/transfer/host-files", params={"ip": "10.99.9.1", "folder": "C:\\Users"}).status_code == 400
    env.fake.no_folder.add(("10.99.9.2", "D:\\Temp"))
    assert env.admin.get("/api/transfer/host-files", params={"ip": "10.99.9.2"}).status_code == 502


# ---------------------------------------------------------------- 重新驗證與殘留
def test_重新驗證只查在不在大小時間_不重算雜湊(env):
    f = upload_lib(env.admin, "a.bin", b"abc" * 100)
    j = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2", "10.99.8.3"]).json()
    del env.fake.files("10.99.8.2", "/tmp")["a.bin"]                         # 被人刪了
    env.fake.files("10.99.8.3", "/tmp")["a.bin"] = b"changed"                # 被人改了
    env.fake.calls.clear()
    r = env.admin.post(f"/api/transfer/jobs/{j['id']}/reverify", json={"with_hash": False})
    assert r.status_code == 200
    by = {h["ip"]: h for h in env.admin.get(f"/api/transfer/jobs/{j['id']}").json()["hosts"]}
    assert by["10.99.8.1"]["verify_state"] == "exists" and by["10.99.8.2"]["verify_state"] == "gone"
    assert by["10.99.8.3"]["verify_state"] == "changed"
    assert not any(c[0] == "sha256" for c in env.fake.calls)                  # 沒讀整個檔案
    # 殘留清單：還在的列出，已刪的不列
    ips = {x["ip"] for x in env.admin.get("/api/transfer/residue").json()["rows"]}
    assert ips == {"10.99.8.1", "10.99.8.3"}


def test_重新驗證要求雜湊時遵守時段(env, monkeypatch):
    monkeypatch.setattr(pol, "SMALL_MAX", 10)
    monkeypatch.setattr(pol, "MID_MAX", 100)
    f = upload_lib(env.admin, "mid.bin", b"x" * 50)
    j = make_job(env.admin, f, ["10.99.8.1"]).json()                           # 週六傳的
    env.set_now(THU10)
    assert env.admin.post(f"/api/transfer/jobs/{j['id']}/reverify", json={"with_hash": True}).status_code == 409
    assert env.admin.post(f"/api/transfer/jobs/{j['id']}/reverify", json={"with_hash": False}).status_code == 200
    env.set_now(THU23)
    env.fake.calls.clear()
    assert env.admin.post(f"/api/transfer/jobs/{j['id']}/reverify", json={"with_hash": True}).status_code == 200
    assert any(c[0] == "sha256" for c in env.fake.calls)


# ====================================================================== 指令
def test_指令從白名單資料夾開始_每次都留紀錄(env):
    r = env.admin.post("/api/transfer/commands", json={"box": "windows", "ips": ["10.99.9.1", "10.99.9.2"], "command": "pwd"})
    assert r.status_code == 200
    d = env.admin.get(f"/api/transfer/commands/{r.json()['log_id']}").json()
    assert d["by_user"] == "admin" and d["box"] == "windows" and d["command"] == "pwd" and d["host_count"] == 2
    assert {x["output"] for x in d["results"]} == {"D:\\Temp"} and d["done"] == 2 and d["ok_count"] == 2
    logs = env.admin.get("/api/transfer/commands").json()["logs"]
    assert logs[0]["command"] == "pwd" and logs[0]["done"] == 2


def test_指令失敗也留紀錄(env):
    """離開碼非 0、遠端例外、連不上——三種失敗都要有一列結果，而且「要執行」那一列在動手前就寫了。"""
    env.fake.command_fail["10.99.8.2"] = "連不上（逾時）"
    r = env.admin.post("/api/transfer/commands", json={"box": "linux", "ips": ["10.99.8.1", "10.99.8.2"], "command": "fail now"})
    d = env.admin.get(f"/api/transfer/commands/{r.json()['log_id']}").json()
    by = {x["ip"]: x for x in d["results"]}
    assert by["10.99.8.1"]["ok"] == 0 and by["10.99.8.1"]["rc"] == 1 and "指令失敗" in by["10.99.8.1"]["output"]
    assert by["10.99.8.2"]["ok"] == 0 and "連不上" in by["10.99.8.2"]["output"]
    assert d["ok_count"] == 0 and d["done"] == 2


def test_指令紀錄在執行前就寫了(env, monkeypatch):
    seen = {}
    real = env.fake.run_command

    def peek(t, folder, command, timeout=120):
        c = env.conn()
        seen["rows"] = c.execute("SELECT command FROM ft_cmd_log").fetchall()
        c.close()
        return real(t, folder, command, timeout)
    monkeypatch.setattr(env.fake, "run_command", peek)
    env.admin.post("/api/transfer/commands", json={"box": "linux", "ips": ["10.99.8.1"], "command": "hostname"})
    assert [r["command"] for r in seen["rows"]] == ["hostname"]


def test_指令的驗證(env):
    post = lambda **b: env.admin.post("/api/transfer/commands", json={"box": "linux", "ips": ["10.99.8.1"], "command": "ls", **b})
    assert post(command="   ").status_code == 400
    assert post(command="x" * 5000).status_code == 400
    assert post(box="solaris").status_code == 400
    assert post(box="windows").status_code == 400                       # 勾的是 Linux 主機，箱子不一致
    assert post(folder="/etc").status_code == 400
    assert post(ips=[]).status_code == 400
    assert env.admin.get("/api/transfer/commands").json()["logs"] == []  # 擋掉的不留「執行」紀錄


def test_指令不受時段限制(env):
    env.set_now(THU10)                                                    # 開盤
    r = env.admin.post("/api/transfer/commands", json={"box": "linux", "ips": ["10.99.8.1"], "command": "ls"})
    assert r.status_code == 200


def test_指令輸出過長會截斷並標記(env, monkeypatch):
    monkeypatch.setattr(env.fake, "run_command", lambda t, f, c, timeout=120: {"rc": 0, "output": "Z" * (st.CMD_OUTPUT_KEEP + 500)})
    r = env.admin.post("/api/transfer/commands", json={"box": "linux", "ips": ["10.99.8.1"], "command": "cat big"})
    x = env.admin.get(f"/api/transfer/commands/{r.json()['log_id']}").json()["results"][0]
    assert x["truncated"] == 1 and len(x["output"]) == st.CMD_OUTPUT_KEEP


# ====================================================================== 紀錄只增不改不刪
def test_沒有任何刪除或修改紀錄的端點(env):
    for m, p in _transfer_routes():
        if any(w in p for w in ("/audit", "/commands", "/residue", "/jobs")):
            assert m in ("GET", "POST"), (m, p)
            if m == "POST":
                assert p.endswith(("/jobs", "/commands", "/stop", "/retry-failed", "/reverify", "/audit-digest")), p
    assert [(m, p) for m, p in _transfer_routes() if m in ("DELETE", "PATCH")] == [("DELETE", "/api/transfer/library/{file_id}")]


@pytest.mark.parametrize("table,sets", [("ft_audit", "detail = 'x'"), ("ft_cmd_log", "command = 'x'"), ("ft_cmd_result", "output = 'x'")])
def test_資料表本身擋改和刪(env, table, sets):
    c = env.conn()
    st.audit(c, "admin", "t", "d")
    lid = st.cmd_log_start(c, "admin", "linux", [{"ip": "1.1.1.1"}], "ls")
    st.cmd_log_result(c, lid, {"ip": "1.1.1.1"}, True, 0, "o")
    with pytest.raises(sqlite3.DatabaseError, match="只增不改"):
        c.execute(f"UPDATE {table} SET {sets}")
    with pytest.raises(sqlite3.DatabaseError, match="只增不刪"):
        c.execute(f"DELETE FROM {table}")
    c.close()


def test_紀錄的人與時間(env):
    upload_lib(env.admin)
    rows = env.admin.get("/api/transfer/audit").json()["rows"]
    assert rows[0]["by_user"] == "admin" and rows[0]["action"] == "library_upload" and rows[0]["at"].startswith("2026-10-31 10:00")


# ====================================================================== 憑證
def test_沒有WinRM憑證就講清楚(env, monkeypatch):
    import credential_store
    monkeypatch.setattr(credential_store, "pick_for_host", lambda *a, **k: None)
    f = upload_lib(env.admin)
    j = make_job(env.admin, f, ["10.99.9.1"]).json()
    assert j["hosts"][0]["status"] == "failed" and "WinRM" in j["hosts"][0]["message"]


def test_root金鑰主機不做檔案傳送(env, monkeypatch):
    import collect_account_migration as cam
    monkeypatch.setattr(cam, "account_for_host", lambda *a, **k: "root")
    f = upload_lib(env.admin)
    j = make_job(env.admin, f, ["10.99.8.1"]).json()
    assert j["hosts"][0]["status"] == "failed" and "root" in j["hosts"][0]["message"]
    assert not any(c[0] == "upload" for c in env.fake.calls)


# Q91：這個檔案測的是「誰能做什麼」，不套 conftest 的「測試帳號預設 admin」相容層（見 tests/conftest.py）。
ROLE_AWARE_TESTS = True
