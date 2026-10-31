"""檔案傳送（Q94）第二輪審查修正（2.008.2）：遮蔽樣式與 ReDoS、名額競態、上傳預留與唯一名稱、傳送途中閘門、指令計數不洩漏。"""
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import transfer_policy as pol  # noqa: E402
import transfer_redact as rd  # noqa: E402
import transfer_runner as rn  # noqa: E402
import transfer_service as svc  # noqa: E402
import transfer_store as st  # noqa: E402
import transfer_winrm as wt  # noqa: E402
from test_transfer_q94 import env, make_job, raw, upload_lib, THU10  # noqa: E402,F401
from test_transfer_review2 import _flag  # noqa: E402
from test_transfer_runner import WIN  # noqa: E402


def U(s):
    return s.replace("§", "")


# ====================================================================== M1 遮蔽
@pytest.mark.parametrize("text,gone", [
    ("DB_PASS=hunter2", "hunter2"), ("DB_PASS§WORD='a b'", "a b"), ("SECRET_KEY=abc999", "abc999"),
    ("AWS_SECRET_ACCESS_KEY=abc999", "abc999"), ("PGPASS§WORD=abc999", "abc999"), ("export MY_API_KEY=k123456", "k123456"),
    ("x.cred§ential: zzz777", "zzz777"), ("DB_TOK§EN=t0k3n99", "t0k3n99"), ("app.pwd = 'p w d'", "p w d"),
    ("echo hunter2 | sudo -S ls", "hunter2"), ("echo -n 'my pw' | sudo -S -k id", "my pw"),
    ("net use X: \\\\srv\\sh /user:dom\\u Pw123abc", "Pw123abc"), ("net use \\\\srv\\sh Pw123abc /user:u", "Pw123abc"),
])
def test_各種變數名稱與指令樣式都遮(text, gone):
    text, gone = U(text), U(gone)
    out, n = rd.redact(text)
    assert gone not in out and n >= 1, out


@pytest.mark.parametrize("text", ["echo hello | sudo ls", "net use X: \\\\srv\\sh", "net use X: \\\\srv\\sh /user:u /persistent:no",
                                  "bypass.txt", "cat passenger.log", "FOO=bar", "path=/tmp", "ls -l"])
def test_相近但不是機密的不誤遮(text):
    assert rd.redact(text) == (text, 0)


@pytest.mark.parametrize("payload", ["a" * 200000, "pass" * 50000, "A_" * 100000 + "x", "x" * 100000 + "=", "pwd " * 50000,
                                     "net use " * 20000, "echo " * 50000 + "|", "-----BEGIN PRIVATE §KEY-----".replace("§", "") * 3000],
                         ids=["a", "pass", "underscore", "eq", "pwd", "netuse", "echo", "keyblock"])
def test_長字串不會ReDoS(payload):
    t0 = time.perf_counter()
    rd.redact(payload)
    assert time.perf_counter() - t0 < 2.0, f"{payload[:20]!r} 太慢"


# ====================================================================== M2 名額競態
def _quiet(monkeypatch):
    monkeypatch.setattr(svc, "start_job", lambda *a, **k: True)
    svc.SYNC = False


def test_並行建立傳送_不會超過同時上限(env, monkeypatch):
    _quiet(monkeypatch)
    f = upload_lib(env.admin)
    env.set_now(env.set_now and pol.at(2026, 10, 31, 10, 0))
    barrier = threading.Barrier(8)

    def go(i):
        c = db.get_connection(env.path)
        try:
            barrier.wait()
            svc.create_upload_job(c, "admin", f["id"], ["10.99.8.1"], None, confirm_count=1)
            return "ok"
        except svc.TransferError as exc:
            return exc.status
        finally:
            c.close()
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(go, range(8)))
    assert res.count("ok") == 2 and res.count(429) == 6, res
    c = env.conn()
    assert svc.active_job_count(c) == 2
    c.close()


def test_等待時段的傳送有自己的上限(env, monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(pol, "SMALL_MAX", 10)
    monkeypatch.setattr(pol, "MID_MAX", 100)
    f = upload_lib(env.admin, "mid.bin", b"x" * 50)
    env.set_now(THU10)                                                      # 中型檔在開盤時間＝要等
    c = env.conn()
    svc_set = st.set_settings(c, {"ft_max_waiting_jobs": 2})
    assert svc_set["ft_max_waiting_jobs"] == 2 and st.get_settings(c)["ft_max_waiting_jobs"] == 2
    for _ in range(2):
        assert svc.create_upload_job(c, "admin", f["id"], ["10.99.8.1"], None, confirm_count=1)["status"] == "waiting_window"
    with pytest.raises(svc.TransferError) as e:
        svc.create_upload_job(c, "admin", f["id"], ["10.99.8.1"], None, confirm_count=1)
    assert e.value.status == 429 and "等待允許時段" in str(e.value)
    assert svc.active_job_count(c) == 0                                      # 等待中的不占「進行中」名額
    assert st.get_settings(c)["ft_max_waiting_jobs"] == 2
    c.close()
    assert st.SETTING_DEFAULTS["ft_max_waiting_jobs"] == "20"


def test_失敗的建立不留半套資料(env, monkeypatch):
    _quiet(monkeypatch)
    f = upload_lib(env.admin)
    c = env.conn()
    monkeypatch.setattr(st, "audit", lambda *a, **k: None)
    real = c.execute
    n = {"i": 0}

    class Boom(Exception):
        pass

    def exe(sql, *a):
        if "INSERT INTO ft_job_host" in sql:
            n["i"] += 1
            raise Boom()
        return real(sql, *a)
    class W:                       # sqlite3.Connection 的 execute 不能直接換，包一層
        def __getattr__(self, k): return getattr(c, k)
        def execute(self, sql, *a): return exe(sql, *a)
    with pytest.raises(Boom):
        svc.create_upload_job(W(), "admin", f["id"], ["10.99.8.1"], None, confirm_count=1)
    assert c.execute("SELECT COUNT(*) FROM ft_job").fetchone()[0] == 0          # 整個交易回滾
    c.close()


# ====================================================================== M3 上傳預留、唯一名稱
def _small_caps(env):
    env.admin.put("/api/transfer/settings", json={"ft_library_cap_gb": 0.00002, "ft_file_cap_gb": 0.00001})   # 單檔約 10737、總量約 21474


def test_並行宣告的上傳會互相預留(env):
    _small_caps(env)
    barrier = threading.Barrier(6)

    def go(i):
        c = env.conn()
        try:
            barrier.wait()
            return ("ok", st.UploadSink(c, f"f{i}.bin", 5000))
        except st.LibraryError as exc:
            return (exc.status, None)
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(go, range(6)))
    sinks = [s for k, s in res if k == "ok"]
    assert len(sinks) == 2 and [k for k, _ in res].count(413) == 4          # 10737 只放得下兩個 5000
    for s in sinks:
        s.abort()
    assert st._reserved[0] == 0
    st.UploadSink(env.conn(), "again.bin", 5000).abort()                     # 釋放後又收得進來


def test_沒宣告長度的串流也互相預留(env):
    _small_caps(env)
    a, b = st.UploadSink(env.conn(), "a.bin"), st.UploadSink(env.conn(), "b.bin")
    a.write(b"x" * 6000)
    with pytest.raises(st.LibraryError) as e:
        b.write(b"x" * 6000)                                                  # 6000 + 6000 > 10737
    assert e.value.status == 413
    a.abort()
    assert st._reserved[0] == 0


def test_同名搶著收_後到的409_且不留孤兒檔(env):
    a, b = st.UploadSink(env.conn(), "same.bin", 10), st.UploadSink(env.conn(), "same.bin", 10)       # 兩條都通過「名稱不存在」的早期檢查
    a.write(b"x" * 10)
    b.write(b"y" * 10)
    a.commit("admin")
    with pytest.raises(st.LibraryError) as e:
        b.commit("admin")
    assert e.value.status == 409 and "已經有" in str(e.value)
    lib = st.library_dir(env.conn())
    assert len([p for p in lib.iterdir() if p.is_file()]) == 1
    assert st._reserved[0] == 0
    # 資料庫層級也不允許同名活檔
    import sqlite3
    c = env.conn()
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO ft_file (name, stored_name, size, sha256, uploaded_at) VALUES ('same.bin', 'z', 1, 'h', 'x')")
    c.close()


def test_INSERT失敗_磁碟上的檔案要刪掉(env, monkeypatch):
    s = st.UploadSink(env.conn(), "x.bin", 5)
    s.write(b"12345")
    monkeypatch.setattr(st, "now_str", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        s.commit("admin")
    monkeypatch.undo()
    assert [p for p in st.library_dir(env.conn()).iterdir() if p.is_file()] == [] and st._reserved[0] == 0


def test_commit前用最新用量再驗一次(env):
    _small_caps(env)
    a = st.UploadSink(env.conn(), "a.bin")
    a.write(b"x" * 9000)
    # 寫的期間別人把檔案庫塞滿（直接寫表模擬）
    c = env.conn()
    c.execute("INSERT INTO ft_file (name, stored_name, size, sha256, uploaded_at) VALUES ('other', 'o', 20000, 'h', 'x')")
    c.commit()
    c.close()
    with pytest.raises(st.LibraryError) as e:
        a.commit("admin")
    assert e.value.status == 413
    assert [p for p in st.library_dir(env.conn()).iterdir() if p.is_file()] == []


def test_上傳端點原始碼_Content_Length只收ASCII數字_且不佔事件迴圈():
    src = (ROOT / "APP" / "asset-module" / "backend" / "routers" / "transfer.py").read_text(encoding="utf-8")
    assert 'fullmatch(r"[0-9]{1,18}"' in src and "isascii()" in src
    assert src.count("run_in_threadpool") >= 5 and "async for chunk in request.stream()" in src
    assert "await run_in_threadpool(sink.write, chunk)" in src


def test_端點對重名回409(env):
    upload_lib(env.admin, "dup.bin")
    r = raw(env.admin, "dup.bin", b"y")
    assert r.status_code == 409


# ====================================================================== M4 傳送途中閘門
class Sess:
    def __init__(self):
        self.files, self.calls = {}, []

    def invoke(self, script, params=None, deadline_s=None):
        self.calls.append(script)
        p = params["Path"]
        if script == wt.REMOVE_PART:
            self.files.pop(p, None)
            return ["OK"], []
        if script == wt.WRITE_FIRST:
            self.files[p] = b""
        self.files[p] += params["Bytes"]
        return ["OK"], []


class Tr:
    def __init__(self, s):
        self.s = s

    def open(self, t, timeout=120):
        import contextlib

        @contextlib.contextmanager
        def cm():
            yield self.s
        return cm()


def test_Windows上傳每16段問一次閘門_要停就刪暫存(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "CHUNK_BYTES", 10)
    f = tmp_path / "f.bin"
    f.write_bytes(b"z" * 400)                                                # 40 段
    s = Sess()
    asked = []

    def check():
        asked.append(len(s.calls))
        return None if len(asked) < 3 else "功能模組已停用"                    # 第 3 次（第 32 段前）叫停
    with pytest.raises(wt.GateStopped, match="已停止：功能模組已停用"):
        wt.upload_file(Tr(s), WIN, f, "D:\\Temp\\p.webit3part", None, check)
    assert asked == [0, 16, 32]
    assert "D:\\Temp\\p.webit3part" not in s.files and s.calls[-1] == wt.REMOVE_PART      # 暫存檔刪掉了


def test_Windows上傳只能寫暫存檔名():
    with pytest.raises(wt.WinTransportError, match="暫存檔"):
        wt.upload_file(Tr(Sess()), WIN, Path(__file__), "D:\\Temp\\important.txt")


def test_空檔的第一段也檢查結果(tmp_path):
    f = tmp_path / "e.bin"
    f.write_bytes(b"")

    class Bad(Sess):
        def invoke(self, script, params=None, deadline_s=None):
            return [], ["access denied"]
    with pytest.raises(wt.WinTransportError, match="寫入目標檔失敗"):
        wt.upload_file(Tr(Bad()), WIN, f, "D:\\Temp\\p.webit3part")
    ok = Sess()
    assert wt.upload_file(Tr(ok), WIN, f, "D:\\Temp\\p.webit3part") == 0 and ok.files["D:\\Temp\\p.webit3part"] == b""


def test_runner把Windows的叫停轉成GateStopped(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"x" * 10)
    r = rn.AnsibleRunner(base_dir=tmp_path / "a", win=Tr(Sess()))
    with pytest.raises(rn.GateStopped):
        r.upload(WIN, f, "D:\\Temp", "p.webit3part", None, lambda: "登入已停用")


def test_傳送途中模組被關_這台標已停止_暫存檔清掉(env):
    f = upload_lib(env.admin)
    real = env.fake.upload

    def upload_then_flip(t, local, folder, part, rate, check=None):
        n = real(t, local, folder, part, rate, check)
        _flag(env, False)                                  # 傳完、改名前關掉模組
        return n
    env.fake.upload = upload_then_flip
    r = env.admin.post("/api/transfer/jobs", json={"file_id": f["id"], "ips": ["10.99.8.1", "10.99.8.2"], "confirm_count": 2, "concurrency": 1})
    assert r.status_code == 200
    c = env.conn()
    rows = {x["ip"]: x for x in c.execute("SELECT * FROM ft_job_host")}
    c.close()
    assert rows["10.99.8.1"]["status"] == "stopped" and "功能模組已停用" in rows["10.99.8.1"]["message"]
    assert rows["10.99.8.2"]["status"] == "stopped"
    assert env.fake.files("10.99.8.1", "/tmp") == {}        # 暫存檔和正式檔都沒有


def test_傳送開始前模組被關_一個位元組都不寫(env):
    f = upload_lib(env.admin)
    env.fake.on_upload = lambda ip: _flag(env, False)
    env.admin.post("/api/transfer/jobs", json={"file_id": f["id"], "ips": ["10.99.8.1"], "confirm_count": 1})
    assert env.fake.files("10.99.8.1", "/tmp") == {}
    c = env.conn()
    assert c.execute("SELECT status FROM ft_job_host").fetchone()[0] == "stopped"
    c.close()


# ====================================================================== M5 指令計數不洩漏
def _hosts(env):
    return svc.resolve_hosts(env.conn(), ["10.99.8.1"], "linux")


def test_spawn失敗_排隊計數要還回去(env, monkeypatch):
    svc._cmd_pending[0] = 0

    def boom(*a, **k):
        raise RuntimeError("no thread")
    monkeypatch.setattr(svc, "spawn", boom)
    with pytest.raises(RuntimeError):
        svc.start_command(env.conn(), "admin", "linux", ["10.99.8.1"], "ls")
    assert svc._cmd_pending[0] == 0
    c = env.conn()
    assert c.execute("SELECT COUNT(*) FROM ft_cmd_log").fetchone()[0] == 1       # 「要執行」紀錄先寫了（append-only）
    c.close()


def test_記錄失敗_排隊計數也還回去(env, monkeypatch):
    svc._cmd_pending[0] = 0
    monkeypatch.setattr(st, "cmd_log_start", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    with pytest.raises(RuntimeError):
        svc.start_command(env.conn(), "admin", "linux", ["10.99.8.1"], "ls")
    assert svc._cmd_pending[0] == 0


def test_稽核失敗_排隊計數也還回去(env, monkeypatch):
    svc._cmd_pending[0] = 0
    monkeypatch.setattr(st, "audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    with pytest.raises(RuntimeError):
        svc.start_command(env.conn(), "admin", "linux", ["10.99.8.1"], "ls")
    assert svc._cmd_pending[0] == 0


def test_worker開連線失敗_計數還回去(env, monkeypatch):
    svc._cmd_pending[0] = 1
    c = env.conn()
    lid = st.cmd_log_start(c, "admin", "linux", [{"ip": "10.99.8.1"}], "ls")
    c.close()
    real = svc._open
    n = {"i": 0}

    def flaky(p):
        n["i"] += 1
        if n["i"] == 1:
            return real(p)                       # 第一次是資格檢查那條
        raise OSError("disk full")
    monkeypatch.setattr(svc, "_open", flaky)
    h = _hosts(env)[0]
    with pytest.raises(OSError):
        svc._command_worker(env.path, lid, [h], "/tmp", "ls")
    assert svc._cmd_pending[0] == 0 and lid not in svc._cmd_taken


def test_正常執行後計數歸零(env):
    svc._cmd_pending[0] = 0
    assert env.admin.post("/api/transfer/commands", json={"box": "linux", "ips": ["10.99.8.1", "10.99.8.2"], "command": "ls"}).status_code == 200
    assert svc._cmd_pending[0] == 0 and not svc._cmd_taken


# ====================================================================== 前端上傳呼叫
def test_前端上傳送X_Filename與原始位元組_並顯示錯誤():
    src = (ROOT / "APP" / "asset-module" / "frontend" / "components" / "v2" / "FileTransferFiles.vue").read_text(encoding="utf-8")
    assert "body: f," in src and "'X-Filename': encodeURIComponent(f.name)" in src and "application/octet-stream" in src
    assert "showToast(`上傳失敗：${errMsg(e)}`, 'error')" in src                # 413／429／409 的後端訊息（detail）直接顯示
    assert "FormData" not in src
