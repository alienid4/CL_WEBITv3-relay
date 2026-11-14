"""檔案傳送／下載紀錄 Codex 審查 F1～F5（2.015.1）。

F1 抓回接收途中硬性上限　F2 scp 上傳可被閘門中途叫停　F3 排隊指令重驗建立者現在的帳號
F4 指令紀錄存原文 SHA256　F5 匯出紀錄的條件只留無害鍵
"""
import hashlib
import io
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import access_control  # noqa: E402
import transfer_runner as rn  # noqa: E402
import transfer_service as svc  # noqa: E402
import transfer_store as st  # noqa: E402
from test_transfer_q94 import env  # noqa: E402,F401
from test_transfer_review3 import _hosts  # noqa: E402
from test_transfer_runner import LNX, _stream_popen  # noqa: E402


# ====================================================================== F1 抓回：接收途中硬性上限
def test_F1_遠端檔案撐大_接收途中就中止_不寫滿磁碟(tmp_path):
    out = tmp_path / "dl"
    big = b"x" * (5 * 65536)                          # 遠端實際吐出的比上限 100 位元組多得多
    log = []
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", popen=_stream_popen(big, log=log))
    with pytest.raises(rn.RunnerError, match="超過允許"):
        r.download(LNX, "/tmp", "a.zip", out, None, 100)
    assert not out.exists()                           # 檔案已刪


def test_F1_接收途中累計超過上限就停_不是傳完才檢查(tmp_path):
    seen = {"chunks": 0}

    class P:
        returncode = 0

        def __init__(self, cmd, **kw):
            outer = self

            class S:
                def read(self, n):
                    seen["chunks"] += 1
                    if seen["chunks"] > 50:
                        raise AssertionError("超過上限後還在繼續讀")
                    return b"y" * 1000
            self.stdout = S()
            self.terminated = False

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def kill(self):
            pass
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", popen=P)
    with pytest.raises(rn.RunnerError, match="超過允許"):
        r.download(LNX, "/tmp", "a.zip", tmp_path / "o", None, 2500)
    assert seen["chunks"] <= 10                       # 超過上限就停（讀取執行緒最多多讀幾塊緩衝）


def test_F1_ssh指令是固定參數_路徑加引號_沿用accept_new(tmp_path):
    log = []
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", popen=_stream_popen(b"abc", log=log))
    assert r.download(LNX, "/data dir/x", "core.1", tmp_path / "o", None, 1000) == 3
    cmd, kw = log[0]
    assert isinstance(cmd, list) and cmd[0] == "ssh" and not kw.get("shell")
    assert "StrictHostKeyChecking=accept-new" in cmd and "BatchMode=yes" in cmd
    assert not any("StrictHostKeyChecking=no" in c for c in cmd)
    assert cmd[-2] == "webit3sc@10.99.8.1"
    assert "'/data dir/x/core.1'" in cmd[-1] and "dd if=" in cmd[-1] and "test -f" in cmd[-1] and "test -L" in cmd[-1]
    assert "sshpass" not in " ".join(cmd)


def test_F1_遠端失敗_不留半個檔(tmp_path):
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", popen=_stream_popen(b"par", rc=255, err=b"Connection reset"))
    with pytest.raises(rn.RunnerError, match="Connection reset"):
        r.download(LNX, "/tmp", "a.zip", tmp_path / "o", None, 1000)
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize("rc,word", [(10, "找不到"), (11, "符號連結"), (12, "不是一般檔案"), (13, "權限")])
def test_L3_拒絕原因講清楚(tmp_path, rc, word):
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", popen=_stream_popen(b"", rc=rc))
    with pytest.raises(rn.RunnerError, match=word):
        r.download(LNX, "/tmp", "a.zip", tmp_path / "o", None, 1000)
    assert not (tmp_path / "o").exists()


def test_L3_沒有stderr也不是空白訊息(tmp_path):
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", popen=_stream_popen(b"", rc=1))
    with pytest.raises(rn.RunnerError) as ei:
        r.download(LNX, "/tmp", "a.zip", tmp_path / "o", None, 1000)
    assert "離開碼 1" in str(ei.value) and not str(ei.value).endswith("：")


class BlockingPopen:
    """stdout 永遠讀不到資料（像 FIFO／掛住的 NFS），直到行程被 terminate 才回 EOF。"""
    instances: list = []

    def __init__(self, cmd, **kw):
        import threading
        self.ev = threading.Event()
        self.terminated = False
        outer = self

        class S:
            def read(self, n):
                outer.ev.wait(30)
                return b""

            def close(self):
                pass
        self.stdout = S()
        BlockingPopen.instances.append(self)

    def terminate(self):
        self.terminated = True
        self.ev.set()

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return -15 if self.terminated else 0

    def poll(self):
        return -15 if self.terminated else None


def test_M1_遠端卡住_逾時看門狗中止並刪檔(tmp_path):
    BlockingPopen.instances = []
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", popen=BlockingPopen, poll_s=0.01)
    out = tmp_path / "o"
    with pytest.raises(rn.RunnerError, match="逾時"):
        r._capped_get(LNX, "/tmp", "fifo", out, None, 1000, 0)
    assert BlockingPopen.instances[0].terminated and not out.exists()


def test_M1_遠端卡住_閘門關了就中止_已停止(tmp_path):
    BlockingPopen.instances = []
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", popen=BlockingPopen, poll_s=0.01)
    out = tmp_path / "o"
    n = {"i": 0}

    def gate():
        n["i"] += 1
        return "登入已停用" if n["i"] >= 3 else None
    with pytest.raises(rn.GateStopped, match="登入已停用"):
        r.download(LNX, "/tmp", "fifo", out, None, 1000, gate)
    assert BlockingPopen.instances[0].terminated and not out.exists()


def test_M1_服務層下載有把閘門傳進去():
    src = (ROOT / "APP" / "asset-module" / "backend" / "transfer_service.py").read_text(encoding="utf-8")
    assert "check=lambda: block_reason(conn, job[\"created_by\"])" in src


# ====================================================================== F2 上傳：scp 傳輸中可被叫停
class SlowScp:
    """假 scp：一直不結束，直到被 terminate；記錄 terminate／kill。"""
    instances: list = []

    def __init__(self, cmd, **kw):
        self.cmd, self.kw = cmd, kw
        self.terminated = self.killed = False
        self.waits = 0
        type(self).instances.append(self)

    def wait(self, timeout=None):
        self.waits += 1
        if self.terminated or self.killed:
            return -15
        raise subprocess.TimeoutExpired(self.cmd, timeout)

    def poll(self):
        return -15 if (self.terminated or self.killed) else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def _local_file(tmp_path):
    f = tmp_path / "up.bin"
    f.write_bytes(b"z" * 10)
    return f


def test_F2_閘門傳輸中翻轉_scp被終止並回已停止(tmp_path):
    SlowScp.instances = []
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", popen=SlowScp, poll_s=0.01)
    calls = {"n": 0}

    def gate():
        calls["n"] += 1
        return None if calls["n"] <= 2 else "功能模組已停用"       # 開始前 + 第 1 次輪詢正常，之後關掉
    with pytest.raises(rn.GateStopped, match="功能模組已停用"):
        r.upload(LNX, _local_file(tmp_path), "/tmp", "w3_1_1_a.webit3part", 5, gate)
    assert SlowScp.instances[0].terminated
    assert calls["n"] == 3


def test_F2_terminate後不走就kill(tmp_path):
    class Stubborn(SlowScp):
        def terminate(self):
            self.terminated = True                    # 假裝收到但不退出

        def wait(self, timeout=None):
            if self.killed:
                return -9
            raise subprocess.TimeoutExpired(self.cmd, timeout)
    Stubborn.instances = []
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", popen=Stubborn, poll_s=0.01)
    with pytest.raises(rn.GateStopped):
        r.upload(LNX, _local_file(tmp_path), "/tmp", "w3_1_1_a.webit3part", 5, lambda: "登入已停用" if Stubborn.instances else None)
    assert Stubborn.instances[0].killed


def test_F2_逾時也會終止行程(tmp_path):
    SlowScp.instances = []
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", popen=SlowScp, poll_s=0.01)
    with pytest.raises(rn.RunnerError, match="逾時"):
        r._scp(LNX, ["-q", "a", "b"], None, 0, 0, None)
    assert SlowScp.instances[0].terminated


def test_F2_服務層_被叫停標已停止_清暫存檔_不改名(env, monkeypatch):
    """閘門在 scp 途中翻轉 → 這台「已停止」、呼叫了 remove_part、沒有 finalize（沒有改成正式檔名）。"""
    from test_transfer_q94 import upload_lib

    f = upload_lib(env.admin)
    calls = {"fin": 0, "rm": 0}

    class R(rn.FakeRunner):
        def upload(self, t, local_path, folder, part, rate_mbps, check=None):
            raise rn.GateStopped("已停止：功能模組已停用")

        def finalize(self, *a, **k):
            calls["fin"] += 1
            return super().finalize(*a, **k)

        def remove_part(self, *a, **k):
            calls["rm"] += 1
            return super().remove_part(*a, **k)
    fake = R()
    rn.set_runner(fake)
    j = svc.create_upload_job(env.conn(), "admin", f["id"], ["10.99.8.1"], None, confirm_count=1)
    d = svc.job_detail(env.conn(), j["id"])
    h = d["hosts"][0]
    assert h["status"] == "stopped" and "功能模組已停用" in (h["message"] or "")
    assert calls["fin"] == 0 and calls["rm"] >= 1


# ====================================================================== F3 排隊指令重驗建立者
def _delete_admin(env):
    c = env.conn()
    c.execute("PRAGMA foreign_keys = OFF")                # 只為了模擬「帳號已被刪掉」，不管 session 等關聯列
    c.execute("DELETE FROM users WHERE username = 'admin'")
    c.commit()
    c.close()


def _run_worker(env, by_user, lid=None):
    c = env.conn()
    lid = lid or st.cmd_log_start(c, by_user, "linux", [{"ip": "10.99.8.1"}], "ls")
    c.close()
    svc._cmd_pending[0] = 1
    svc._command_worker(env.path, lid, _hosts(env), "/tmp", "ls", None, by_user)
    return env.conn().execute("SELECT ok, output FROM ft_cmd_result WHERE log_id = ?", (lid,)).fetchone()


def test_F3_建立者仍是有效admin_照常執行(env):
    r = _run_worker(env, "admin")
    assert r["ok"] == 1


def test_F3_建立者被刪除_剩下的主機不執行(env):
    _delete_admin(env)
    r = _run_worker(env, "admin")
    assert r["ok"] == 0 and "已停止" in r["output"] and "沒有執行" in r["output"]


def test_F3_建立者被降級_不執行(env):
    c = env.conn()
    c.execute("UPDATE users SET role = 'user' WHERE username = 'admin'")
    c.commit()
    r = _run_worker(env, "admin")
    assert r["ok"] == 0 and "已停止" in r["output"]


def test_F3_沒帶建立者就讀指令紀錄的建立者_不再寫死admin(env):
    c = env.conn()
    c.execute("INSERT INTO users (username, password_hash, role) VALUES ('carol', 'x', 'user')")      # 一般帳號
    c.commit()
    lid = st.cmd_log_start(c, "carol", "linux", [{"ip": "10.99.8.1"}], "ls")
    c.close()
    svc._cmd_pending[0] = 1
    svc._command_worker(env.path, lid, _hosts(env), "/tmp", "ls")
    r = env.conn().execute("SELECT ok, output FROM ft_cmd_result WHERE log_id = ?", (lid,)).fetchone()
    assert r["ok"] == 0 and "已停止" in r["output"]


def test_F3_block_reason對排隊上傳也用現在的帳號(env):
    assert svc.block_reason(env.conn(), "admin") is None
    _delete_admin(env)
    assert "不是有效的 admin" in (svc.block_reason(env.conn(), "admin") or "")
    assert svc.block_reason(env.conn(), None)


# ====================================================================== F4 指令原文 SHA256
def test_F4_指令紀錄存原文雜湊_遮蔽文字照舊(env):
    c = env.conn()
    cmd = "echo hunter2 | sudo -S ls"
    lid = st.cmd_log_start(c, "admin", "linux", [{"ip": "10.99.8.1"}], cmd)
    row = c.execute("SELECT command, cmd_sha256 FROM ft_cmd_log WHERE id = ?", (lid,)).fetchone()
    assert "hunter2" not in row["command"]
    assert row["cmd_sha256"] == hashlib.sha256(cmd.encode("utf-8")).hexdigest()
    assert st.get_cmd_log(c, lid)["cmd_sha256"] == row["cmd_sha256"]
    assert st.list_cmd_logs(c)[0]["cmd_sha256"] == row["cmd_sha256"]


def test_F4_欄位受只增不改保護(env):
    c = env.conn()
    lid = st.cmd_log_start(c, "admin", "linux", [{"ip": "10.99.8.1"}], "ls")
    import sqlite3
    with pytest.raises(sqlite3.DatabaseError, match="只增不改"):
        c.execute("UPDATE ft_cmd_log SET cmd_sha256 = 'x' WHERE id = ?", (lid,))


def test_F4_舊庫補欄_舊列為空(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE ft_cmd_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, by_user TEXT, box TEXT NOT NULL, "
              "host_count INTEGER NOT NULL, hosts_json TEXT NOT NULL, command TEXT NOT NULL, redacted INTEGER NOT NULL DEFAULT 0)")
    c.execute("INSERT INTO ft_cmd_log (at, by_user, box, host_count, hosts_json, command) VALUES ('t','u','linux',1,'[]','ls')")
    c.commit()
    st.ensure_tables(c)
    st.ensure_tables(c)                                # 重跑不炸
    assert c.execute("SELECT cmd_sha256 FROM ft_cmd_log").fetchone()[0] is None
    lid = st.cmd_log_start(c, "u", "linux", [], "pwd")
    assert c.execute("SELECT cmd_sha256 FROM ft_cmd_log WHERE id = ?", (lid,)).fetchone()[0]


def test_F4_畫面有顯示指紋():
    vue = (ROOT / "APP" / "asset-module" / "frontend" / "components" / "v2" / "FileTransferCmd.vue").read_text(encoding="utf-8")
    assert "cmd_sha256" in vue and "原文指紋" in vue


# ====================================================================== F5 匯出紀錄條件
def test_F5_網址帶token密碼_不存值_q只存字數():
    s = access_control.safe_query_summary("token=abc&password=x&q=web&status=ok&room=A1")
    assert "abc" not in s and "=x" not in s and "web" not in s
    assert "token=<已遮蔽>" in s and "password=<已遮蔽>" in s and "q=<3字>" in s
    assert "status=ok" in s and "room=A1" in s


def test_F5_未知鍵只留鍵名_白名單值截80字():
    s = access_control.safe_query_summary("custom=hello&sort=" + "a" * 200 + "&API_KEY=zzz&x-auth=1")
    assert "hello" not in s and "custom=<略>" in s
    assert "sort=" + "a" * 80 in s and "a" * 81 not in s
    assert "zzz" not in s and "API_KEY=<已遮蔽>" in s and "x-auth=<已遮蔽>" in s
    assert access_control.safe_query_summary("") == "" and access_control.safe_query_summary(None) == ""


def test_M2_真實前端鍵_識別保留_機密遮蔽():
    s = access_control.safe_query_summary(
        "key=MK-001&only=rebuild&username=alice&asset_serial=S123&mode=all&fmt=standard&kind=account&room=R1"
        "&token=abc&password=x&api_key=zzz&secret_key=s&access_key=a&private_key=p&x_session_id=1&kw=ab")
    for keep in ("key=MK-001", "only=rebuild", "username=alice", "asset_serial=S123", "mode=all", "fmt=standard", "kind=account"):
        assert keep in s, s
    for gone in ("abc", "=x", "zzz", "=s&", "=a&", "=p&"):
        assert gone not in s, s
    assert "token=<已遮蔽>" in s and "api_key=<已遮蔽>" in s and "secret_key=<已遮蔽>" in s and "kw=<2字>" in s


def test_M2_總長度有上限():
    assert len(access_control.safe_query_summary("&".join(f"room={'a' * 80}" for _ in range(50)))) == 400


def test_F5_中介層真的用它_匯出紀錄裡沒有密碼(tmp_path, monkeypatch):
    import api
    import auth
    import db
    from fastapi.testclient import TestClient

    monkeypatch.delenv(api.AUTH_DISABLED_ENV, raising=False)
    path = tmp_path / "t.db"
    db.init_db(path)

    def _get_db():
        c = db.get_connection(path)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _get_db
    try:
        c = db.get_connection(path)
        db.create_user(c, "alice", auth.hash_password("s3cure-pass!"), role="user")
        c.close()
        cl = TestClient(api.app)
        assert cl.post("/api/auth/login", json={"username": "alice", "password": "s3cure-pass!"}).status_code == 200
        r = cl.post("/api/export/table-xlsx?token=abc&password=x&q=web",
                    json={"title": "小表", "headers": ["主機"], "rows": [["h"]]})
        assert r.status_code == 200
        c = db.get_connection(path)
        summ = c.execute("SELECT filter_summary FROM export_log ORDER BY id DESC LIMIT 1").fetchone()[0] or ""
        c.close()
        assert "abc" not in summ and "password=x" not in summ and "web" not in summ
        assert "token=<已遮蔽>" in summ and "q=<3字>" in summ
    finally:
        api.app.dependency_overrides.clear()
