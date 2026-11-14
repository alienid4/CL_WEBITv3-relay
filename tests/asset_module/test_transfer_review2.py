"""檔案傳送（Q94）獨立審查後的修正（2.008.1）：中途關閘即停、全域上限、上傳串流上限、紀錄不可覆蓋、機密遮蔽、
Windows 傳輸不含編碼字樣、資格重驗、名稱規則。fixture 沿用 test_transfer_q94。"""
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import api  # noqa: E402
import transfer_policy as pol  # noqa: E402
import transfer_redact as rd  # noqa: E402
import transfer_runner as rn  # noqa: E402
import transfer_service as svc  # noqa: E402
import transfer_store as st  # noqa: E402
from test_transfer_q94 import env, make_job, raw, upload_lib, THU10, SAT10  # noqa: E402,F401


def _flag(env, on):
    c = env.conn()
    c.execute("UPDATE feature_flags SET enabled = ? WHERE module_key = 'file_transfer'", (1 if on else 0,))
    c.commit()
    c.close()


# ====================================================================== HIGH 2：中途關閘
def test_傳送中途關掉模組_剩下的台不再動(env):
    f = upload_lib(env.admin)
    env.fake.on_upload = lambda ip: _flag(env, False)
    r = env.admin.post("/api/transfer/jobs", json={"file_id": f["id"], "ips": ["10.99.8.1", "10.99.8.2", "10.99.8.3"],
                                                    "confirm_count": 3, "concurrency": 1})
    assert r.status_code == 200
    _flag(env, True)
    c = env.conn()
    jid = c.execute("SELECT MAX(id) FROM ft_job").fetchone()[0]
    rows = {x["ip"]: x for x in c.execute("SELECT * FROM ft_job_host WHERE job_id = ?", (jid,))}
    job = c.execute("SELECT * FROM ft_job WHERE id = ?", (jid,)).fetchone()
    c.close()
    assert rows["10.99.8.1"]["status"] == "stopped"                     # 傳送途中（上傳前的檢查點）就被叫停：標已停止、沒寫任何東西
    for ip in ("10.99.8.2", "10.99.8.3"):
        assert rows[ip]["status"] == "stopped" and "功能模組已停用" in rows[ip]["message"]
    assert job["status"] == "stopped" and "已停止" in job["note"]
    assert env.fake.files("10.99.8.3", "/tmp") == {} and env.fake.files("10.99.8.2", "/tmp") == {}


def test_每一波開始前也再問一次(env):
    ips = _more(env, 12)
    f = upload_lib(env.admin)
    state = {"n": 0}

    def cb(ip):
        state["n"] += 1
        if state["n"] == 10:                      # 第一波最後一台傳送中關掉
            _flag(env, False)
    env.fake.on_upload = cb
    env.admin.post("/api/transfer/jobs", json={"file_id": f["id"], "ips": ips, "confirm_count": 12, "concurrency": 1})
    c = env.conn()
    n_stopped = c.execute("SELECT COUNT(*) FROM ft_job_host WHERE status = 'stopped'").fetchone()[0]
    c.close()
    assert n_stopped == 3 and len(env.fake.calls) < 12 * 6


def _more(env, n):
    from test_transfer_q94 import _more_linux_hosts
    return _more_linux_hosts(env, n)


def test_抓回途中關閘就取消(env):
    env.fake.put("10.99.8.1", "/tmp", "a.dump", b"D" * 100)
    real = env.fake.probe
    calls = {"n": 0}

    def probe(t, folder, name=None):
        calls["n"] += 1
        if calls["n"] == 3:                         # create_pull 一次、_download_one 第一次探測後、再探測前關掉
            _flag(env, False)
        return real(t, folder, name)
    env.fake.probe = probe
    r = env.admin.post("/api/transfer/pull", json={"ip": "10.99.8.1", "name": "a.dump"})
    assert r.status_code == 200 and r.json()["status"] == "failed" and "已停止" in r.json()["hosts"][0]["message"]
    assert env.fake.calls.count(("download", "10.99.8.1")) == 0


def test_遠端大小在抓回前被改動就取消(env):
    env.fake.put("10.99.8.1", "/tmp", "a.dump", b"D" * 100)
    real, n = env.fake.probe, {"n": 0}

    def probe(t, folder, name=None):
        n["n"] += 1
        out = real(t, folder, name)
        if n["n"] >= 3 and out["exists"]:
            out["size"] = 999999
        return out
    env.fake.probe = probe
    r = env.admin.post("/api/transfer/pull", json={"ip": "10.99.8.1", "name": "a.dump"})
    assert r.json()["status"] == "failed" and "被改動" in r.json()["hosts"][0]["message"]


def test_排隊後才開始_資格重驗_已不合格的不傳(env):
    f = upload_lib(env.admin)
    env.set_now(THU10)
    monkey_small = pytest.MonkeyPatch()
    monkey_small.setattr(pol, "SMALL_MAX", 10)
    monkey_small.setattr(pol, "MID_MAX", 100)
    try:
        f2 = upload_lib(env.admin, "mid.bin", b"x" * 50)
        j = make_job(env.admin, f2, ["10.99.8.1", "10.99.8.2"]).json()
        assert j["status"] == "waiting_window"
        c = env.conn()
        c.execute("UPDATE hardware SET collect_ok = 0 WHERE asset_serial = 'L1'")      # 排隊期間被取消納管
        c.commit()
        c.close()
        env.set_now(pol.at(2026, 10, 29, 15, 0))
        assert svc.resume_waiting(env.path) == 1
        by = {h["ip"]: h for h in env.admin.get(f"/api/transfer/jobs/{j['id']}").json()["hosts"]}
        assert by["10.99.8.1"]["status"] == "failed" and "重新檢查" in by["10.99.8.1"]["message"]
        assert by["10.99.8.2"]["status"] == "ok"
        assert env.fake.files("10.99.8.1", "/tmp") == {}
    finally:
        monkey_small.undo()


def test_指令排隊後_資格重驗(env, monkeypatch):
    real = svc.list_hosts
    monkeypatch.setattr(svc, "list_hosts", lambda conn: {"hosts": [h for h in real(conn)["hosts"] if h["ip"] != "10.99.8.1"], "excluded": {}})
    d = env.admin.post("/api/transfer/commands", json={"box": "linux", "ips": ["10.99.8.1"], "command": "hostname"})
    # start_command 的 resolve 也用 list_hosts → 直接 400（資格第一道）；worker 那道由下面直接呼叫驗
    assert d.status_code == 400
    monkeypatch.setattr(svc, "list_hosts", real)
    c = env.conn()
    lid = st.cmd_log_start(c, "admin", "linux", [{"ip": "10.99.8.1"}], "hostname")
    c.close()
    monkeypatch.setattr(svc, "list_hosts", lambda conn: {"hosts": [], "excluded": {}})
    svc._cmd_pending[0] = 1
    svc._command_worker(env.path, lid, [{"ip": "10.99.8.1", "hostname": "lx1", "serial": "L1", "platform": "linux"}], "/tmp", "hostname")
    x = env.admin.get(f"/api/transfer/commands/{lid}").json()["results"][0]
    assert x["ok"] == 0 and "重新檢查" in x["output"]


# ====================================================================== HIGH 3：全域上限
def test_一次傳送台數上限_429(env):
    f = upload_lib(env.admin)
    env.admin.put("/api/transfer/settings", json={"ft_max_hosts_job": 2})
    r = make_job(env.admin, f, ["10.99.8.1", "10.99.8.2", "10.99.8.3"])
    assert r.status_code == 429 and "最多 2 台" in r.json()["detail"]


def test_同時進行的傳送上限_429(env):
    f = upload_lib(env.admin)
    c = env.conn()
    for _ in range(2):
        c.execute("INSERT INTO ft_job (kind, box, file_name, size, dest_folder, status, created_at) "
                  "VALUES ('upload', 'linux', 'x', 1, '/tmp', 'running', '2026-10-31 10:00:00')")
    c.commit()
    c.close()
    r = make_job(env.admin, f, ["10.99.8.1"])
    assert r.status_code == 429 and "同時最多 2 個" in r.json()["detail"]
    env.fake.put("10.99.8.1", "/tmp", "a.dump", b"D")
    assert env.admin.post("/api/transfer/pull", json={"ip": "10.99.8.1", "name": "a.dump"}).status_code == 429
    assert env.admin.get("/api/transfer/jobs").status_code == 200


def test_等待時段的傳送不占名額(env):
    c = env.conn()
    for _ in range(5):
        c.execute("INSERT INTO ft_job (kind, box, file_name, size, dest_folder, status, created_at) "
                  "VALUES ('upload', 'linux', 'x', 1, '/tmp', 'waiting_window', '2026-10-31 10:00:00')")
    c.commit()
    assert svc.active_job_count(c) == 0
    c.close()


def test_指令台數上限_與共用執行緒池(env):
    env.admin.put("/api/transfer/settings", json={"ft_max_hosts_cmd": 1})
    r = env.admin.post("/api/transfer/commands", json={"box": "linux", "ips": ["10.99.8.1", "10.99.8.2"], "command": "ls"})
    assert r.status_code == 429 and "最多 1 台" in r.json()["detail"]
    assert env.admin.get("/api/transfer/commands").json()["logs"] == []          # 被擋的不留「要執行」紀錄
    p = svc._command_pool()
    assert p is svc._command_pool() and p._max_workers == svc.COMMAND_POOL == 5
    assert env.admin.post("/api/transfer/commands", json={"box": "linux", "ips": ["10.99.8.1"], "command": "ls"}).status_code == 200
    assert svc._cmd_pending[0] == 0                                              # 跑完計數歸零


def test_指令排隊太多_429(env, monkeypatch):
    monkeypatch.setattr(svc, "CMD_PENDING_MAX", 1)
    r = env.admin.post("/api/transfer/commands", json={"box": "linux", "ips": ["10.99.8.1", "10.99.8.2"], "command": "ls"})
    assert r.status_code == 429 and "排隊" in r.json()["detail"]


def test_Windows預設同時一台_Linux兩台(env):
    f = upload_lib(env.admin)
    assert make_job(env.admin, f, ["10.99.9.1"]).json()["concurrency"] == 1
    assert make_job(env.admin, f, ["10.99.8.1"]).json()["concurrency"] == 2
    assert make_job(env.admin, f, ["10.99.9.2"], concurrency=3).json()["concurrency"] == 3          # 明確指定仍可
    pre = env.admin.post("/api/transfer/preflight", json={"file_id": f["id"], "ips": ["10.99.9.1", "10.99.9.2"]}).json()
    assert pre["estimate"]["concurrency"] == 1 and pre["windows_unlimited"] is True
    assert pol.default_concurrency("windows") == 1 and pol.default_concurrency("linux") == 2


# ====================================================================== HIGH 4：上傳串流上限
def test_宣告長度超過上限_讀內容前就413(env):
    env.admin.put("/api/transfer/settings", json={"ft_file_cap_gb": 0.00001})            # 約 10737 位元組
    r = raw(env.admin, "big.bin", b"x" * 50000)
    assert r.status_code == 413
    assert [p for p in st.library_dir(env.conn()).iterdir() if p.is_file()] == []


def test_沒宣告長度的串流_途中超過就中止並刪暫存(env):
    env.admin.put("/api/transfer/settings", json={"ft_file_cap_gb": 0.00001})

    def gen():
        for _ in range(20):
            yield b"y" * 2000
    r = env.admin.post("/api/transfer/library", content=gen(), headers={"X-Filename": "big.bin"})
    assert r.status_code == 413
    assert [p for p in st.library_dir(env.conn()).iterdir() if p.is_file()] == []
    assert env.admin.get("/api/transfer/library").json()["files"] == []


def test_磁碟剩餘扣保留量(env, monkeypatch):
    monkeypatch.setattr(st, "DISK_RESERVE_BYTES", 1 << 62)
    r = raw(env.admin, "a.bin", b"x" * 10)
    assert r.status_code == 413 and "空間不足" in r.json()["detail"]
    monkeypatch.setattr(st.shutil, "disk_usage", lambda p: (_ for _ in ()).throw(OSError("x")))
    assert st.disk_room(env.conn()) == 0                                                  # 查不到＝0（fail-closed）


def test_上傳要走閘門_沒權限不收內容(env):
    from fastapi.testclient import TestClient
    assert TestClient(api.app).post("/api/transfer/library", content=b"x" * 10, headers={"X-Filename": "a.bin"}).status_code == 401
    bob = env.client("bob")
    assert raw(bob, "a.bin", b"x").status_code == 403


def test_檔名標頭要過檢查(env):
    for bad in ["", "a;b.bin", "../x", "con.txt.webit3part"]:
        r = env.admin.post("/api/transfer/library", content=b"x", headers={"X-Filename": bad})
        assert r.status_code == 400, bad


@pytest.mark.skipif(os.name == "nt", reason="Windows 的 chmod 不是 POSIX 權限")
def test_檔案庫檔案0600_資料夾0700(env):
    upload_lib(env.admin)
    lib = st.library_dir(env.conn())
    assert (lib.stat().st_mode & 0o777) == 0o700
    assert all((p.stat().st_mode & 0o777) == 0o600 for p in lib.iterdir() if p.is_file())


# ====================================================================== MEDIUM 5：REPLACE 擋掉
@pytest.mark.parametrize("table,cols,vals", [
    ("ft_audit", "id, at, by_user, action, detail", "1, 'x', 'u', 'a', 'd'"),
    ("ft_cmd_log", "id, at, by_user, box, host_count, hosts_json, command", "1, 'x', 'u', 'linux', 1, '[]', 'ls'"),
    ("ft_cmd_result", "id, log_id, at, ip, ok", "1, 1, 'x', '1.1.1.1', 1"),
])
def test_INSERT_OR_REPLACE不能覆蓋紀錄(env, table, cols, vals):
    c = env.conn()
    st.ensure_tables(c)
    c.execute(f"INSERT INTO {table} ({cols}) VALUES ({vals})")
    before = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    for verb in ("INSERT OR REPLACE INTO", "REPLACE INTO"):
        with pytest.raises(sqlite3.DatabaseError, match="不可覆蓋|只增不"):
            c.execute(f"{verb} {table} ({cols}) VALUES ({vals})")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(f"INSERT INTO {table} ({cols}) VALUES ({vals})")                       # 一般重複 id 也不行
    assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == before
    c.execute(f"INSERT INTO {table} ({cols.split(', ', 1)[1]}) VALUES ({vals.split(', ', 1)[1]})")   # 正常 append 照舊
    assert c.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
    c.close()


# ====================================================================== MEDIUM 6：機密遮蔽
@pytest.mark.parametrize("text,gone", [
    ("mysql -u root -pS3cretPw db", "S3cretPw"), ("sshpass -p hunter2 ssh x", "hunter2"),
    ("curl -u admin:topsecret http://x", "topsecret"), ("curl -H 'Authorization: Bearer abc.def.ghi' x", "abc.def.ghi"),
    ("export PASS§WORD=abc123XYZ", "abc123XYZ"), ("db.pass§word: 'p@ss w0rd'", "p@ss w0rd"), ("TOK§EN=tok_9f8e7d", "tok_9f8e7d"),
    ("client_sec§ret=zzz999", "zzz999"), ("tool --pass§word hunter3 --x", "hunter3"), ("tool --tok§en=abcdef", "abcdef"),
    ("root:$6$salt$hashhashhashhash:19000:0:99999:7:::", "hashhashhashhash"),
    ("-----BEGIN RSA PRIVATE §KEY-----\nMIIEabc\n-----END RSA PRIVATE §KEY-----", "MIIEabc"),
    ("-----BEGIN OPENSSH PRIVATE §KEY-----\nAAAAB3Nza", "AAAAB3Nza"),
    ("ConvertTo-SecureString 'Sup3rS3cret' -AsPlainText -Force", "Sup3rS3cret"),
])
def test_機密樣式會被遮蔽(text, gone):
    text, gone = text.replace("§", ""), gone.replace("§", "")
    out, n = rd.redact(text)
    assert gone not in out and n >= 1, out


@pytest.mark.parametrize("text", ["ls -l /tmp", "mkdir -p /tmp/x", "echo hello", "df -g", "Get-ChildItem D:\\Temp", "grep pass file.txt"])
def test_一般指令不誤遮(text):
    assert rd.redact(text) == (text, 0)


def test_遮蔽不重複計算():
    out, n = rd.redact("pass§word=abc".replace("§", ""))
    assert rd.redact(out) == (out, 0) and n == 1


def test_指令與輸出進紀錄前就遮_實際執行的仍是原文(env, monkeypatch):
    seen = {}
    real = env.fake.run_command

    def spy(t, folder, command, timeout=120):
        seen["cmd"] = command
        return {"rc": 0, "output": "login ok pass§word=hunter9\nroot:$6$s$abcdefabcdef:1:0:::".replace("§", "")}
    monkeypatch.setattr(env.fake, "run_command", spy)
    r = env.admin.post("/api/transfer/commands", json={"box": "linux", "ips": ["10.99.8.1"], "command": "mysql -u r -pTopS3cret x"})
    d = env.admin.get(f"/api/transfer/commands/{r.json()['log_id']}").json()
    assert seen["cmd"] == "mysql -u r -pTopS3cret x"                                    # 執行用原文
    assert "TopS3cret" not in d["command"] and d["redacted"] == 1
    out = d["results"][0]["output"]
    assert "hunter9" not in out and "abcdefabcdef" not in out and d["results"][0]["redacted"] == 2
    assert d["redacted_total"] == 3
    logs = env.admin.get("/api/transfer/commands").json()["logs"]
    assert "TopS3cret" not in logs[0]["command"] and logs[0]["redacted"] == 1
    c = env.conn()
    raw_rows = c.execute("SELECT command FROM ft_cmd_log").fetchall() + c.execute("SELECT output FROM ft_cmd_result").fetchall()
    c.close()
    assert not any("TopS3cret" in r[0] or "hunter9" in (r[0] or "") for r in raw_rows)  # 資料庫裡也沒有


# ====================================================================== LOW 9：Windows 保留字
@pytest.mark.parametrize("bad", ["CON .txt", "con  .txt", "NUL.", "con  ", "Aux. .log", "COM1 .x", "lpt9...txt"])
def test_Windows保留字連尾端空白與點也擋(bad):
    with pytest.raises(pol.PolicyError):
        pol.check_name("windows", bad)


def test_一般名稱不誤擋():
    assert pol.check_name("windows", "console.txt") == "console.txt"
    assert pol.check_name("windows", "con2.txt") == "con2.txt"


# Q91：這個檔案測的是「誰能做什麼」，不套 conftest 的「測試帳號預設 admin」相容層（見 tests/conftest.py）。
ROLE_AWARE_TESTS = True


def test_傳送中途停用登入_不再被停止_稽核名稱不是admin(env, monkeypatch):
    # 2026-10-30 使用者決定登入停用也能用：中途切成停用，剩下的台照常傳；
    # 稽核記的是固定的「未登入」字串，不會假冒成真實 admin。
    f = upload_lib(env.admin)
    env.fake.on_upload = lambda ip: monkeypatch.setenv(api.AUTH_DISABLED_ENV, "1")
    r = env.admin.post("/api/transfer/jobs", json={"file_id": f["id"], "ips": ["10.99.8.1", "10.99.8.2"],
                                                    "confirm_count": 2, "concurrency": 1})
    assert r.status_code == 200
    c = env.conn()
    rows = {x["ip"]: x for x in c.execute("SELECT * FROM ft_job_host")}
    bad = c.execute("SELECT COUNT(*) FROM ft_audit WHERE by_user = 'admin' AND action = 'module_on' AND detail LIKE '%停用%'").fetchone()[0]
    c.close()
    assert rows["10.99.8.2"]["status"] != "stopped"
    assert bad == 0
