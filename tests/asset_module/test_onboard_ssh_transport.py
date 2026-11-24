"""納管的 SSH 傳輸層（paramiko）——2026-09-11 取代 sshpass。

守的東西：
1. 輸出逐行串流（progress_note），UTF-8 多位元組字切在兩個封包中間也不能亂碼
2. 密碼只交給「登入」那一步：不進遠端指令字串；root 身分時也不進 stdin
3. 連線階段的各種失敗都要被 classify_failure 歸到 connect，並給對的原因
4. 主機金鑰＝accept-new：首見寫進 SSH_KNOWN_HOSTS、之後變了就拒；寫不進去也拒
5. 本機起一個真的 SSH 伺服器（paramiko 伺服器端），把整條真實路徑跑一次

真機（221）上的密碼登入、sudo、Windows OpenSSH 這裡驗不到，只能在 221 驗。
"""
import errno
import socket
import sys
import threading
import time
from pathlib import Path

import paramiko
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import onboard_engine as eng  # noqa: E402

IP = "10.0.0.9"
# 哨兵字串，不是密碼：用來確認這個值有沒有跑到不該去的地方
SENTINEL = "SENTINEL-VALUE-FOR-SSH-TEST"
SCRIPT = "echo hello\necho 完成。\n"


@pytest.fixture(scope="module")
def keys():
    return {"a": paramiko.RSAKey.generate(2048), "b": paramiko.RSAKey.generate(2048),
            "ec": paramiko.ECDSAKey.generate()}


# ===== 假的 paramiko 物件：只測我們自己的邏輯 =====

class FakeChan:
    def __init__(self, chunks=(), rc=0, hang=False):
        self.chunks = list(chunks)
        self.rc, self.hang = rc, hang
        self.sent = b""
        self.command = None
        self.write_closed = False

    def set_combine_stderr(self, v): self.combined = v
    def exec_command(self, c): self.command = c
    def sendall(self, b): self.sent += b
    def shutdown_write(self): self.write_closed = True
    def settimeout(self, t): pass

    def recv(self, n):
        if self.hang:
            time.sleep(0.05)
            raise socket.timeout()
        return self.chunks.pop(0) if self.chunks else b""

    def exit_status_ready(self): return not self.hang
    def recv_exit_status(self): return self.rc


class FakeClient:
    def __init__(self, chan):
        self.chan, self.closed = chan, False

    def get_transport(self):
        chan = self.chan

        class _T:
            def open_session(self, timeout=None): return chan
        return _T()

    def close(self): self.closed = True


def _use_fake(monkeypatch, chan):
    client = FakeClient(chan)
    seen = {"client": client}

    def fake_connect(host, username, password, timeout):
        seen["password"] = password
        return client

    monkeypatch.setattr(eng, "_ssh_connect", fake_connect)
    return seen


def test_輸出逐行串流且中文切在封包中間不亂碼(monkeypatch):
    data = "line1\nline2\n完成。\n".encode("utf-8")
    # 第 14 個位元組切在「完」（3 bytes，從第 12 個開始）的中間
    chan = FakeChan([data[:8], data[8:14], data[14:]])
    seen = _use_fake(monkeypatch, chan)
    lines = []
    monkeypatch.setattr(eng, "progress_note", lambda host, ln: lines.append(ln))

    r = eng._ssh_executor(IP, "root", SENTINEL, "linux", SCRIPT, IP, as_root=True)

    assert r.ok, r
    assert lines == ["line1\n", "line2\n", "完成。\n"]
    assert chan.command == "bash -s"
    assert chan.sent == SCRIPT.encode("utf-8")
    assert chan.write_closed, "沒關寫端，遠端的 bash -s 會一直等 stdin"
    assert chan.combined is True, "stderr 沒併進來，目標機的錯誤訊息看不到"
    assert seen["client"].closed


def test_密碼只交給登入那一步(monkeypatch):
    for as_root in (True, False):
        chan = FakeChan([b"\xe5\xae\x8c\xe6\x88\x90\xe3\x80\x82\n"])  # 完成。
        seen = _use_fake(monkeypatch, chan)
        eng._ssh_executor(IP, "sysinfra", SENTINEL, "linux", SCRIPT, IP, as_root=as_root)
        assert seen["password"] == SENTINEL
        assert SENTINEL not in chan.command, f"密碼進了遠端指令字串：{chan.command!r}"
        stdin = chan.sent.decode("utf-8")
        if as_root:
            assert SENTINEL not in stdin, "root 身分沒有人要讀密碼，不該送出去"
        else:
            assert stdin == SENTINEL + "\n" + SCRIPT


def test_結束碼不是0就算失敗(monkeypatch):
    _use_fake(monkeypatch, FakeChan([b"oops\n"], rc=1))
    r = eng._ssh_executor(IP, "root", SENTINEL, "aix", SCRIPT, IP)
    assert not r.ok


_CASES = [
    (socket.timeout("timed out"), "逾時"),
    (ConnectionRefusedError(), "拒絕連線"),
    (OSError(errno.EHOSTUNREACH, "unreachable"), "路由不通"),
    (socket.gaierror(), "解析不到"),
    (paramiko.AuthenticationException("Authentication failed."), "登入被拒"),
    (paramiko.BadAuthenticationType("Bad authentication type", ["publickey"]), "登入方式不被接受"),
    (paramiko.ssh_exception.NoValidConnectionsError({(IP, 22): ConnectionRefusedError()}), "拒絕連線"),
    (paramiko.SSHException("Error reading SSH protocol banner"), "開場訊息"),
    (ImportError("No module named 'paramiko'"), "SSH 連線階段"),
    (RuntimeError("something odd"), "SSH 連線階段"),
]


@pytest.mark.parametrize("exc,why", _CASES, ids=[type(c[0]).__name__ for c in _CASES])
def test_連線階段失敗一律歸connect(monkeypatch, exc, why):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(eng, "_ssh_connect", boom)
    r = eng._ssh_executor(IP, "u", SENTINEL, "linux", SCRIPT, IP)
    assert r.stage == "connect", f"{exc!r} 被歸到 {r.stage}：{r.message}"
    assert why in r.message, r.message
    assert SENTINEL not in r.output and SENTINEL not in r.message


def test_主機金鑰不符歸connect(monkeypatch, keys):
    exc = paramiko.BadHostKeyException(IP, keys["b"], keys["a"])
    monkeypatch.setattr(eng, "_ssh_connect", lambda *a, **k: (_ for _ in ()).throw(exc))
    r = eng._ssh_executor(IP, "u", SENTINEL, "linux", SCRIPT, IP)
    assert r.stage == "connect" and "主機金鑰" in r.message


def test_探測連不進去要回原因(monkeypatch):
    def boom(*a, **k):
        raise paramiko.AuthenticationException("Authentication failed.")
    monkeypatch.setattr(eng, "_ssh_connect", boom)
    got = eng.probe_target(IP, "root", SENTINEL)
    assert got["os"] == "" and "登入被拒" in got["error"]
    rows = eng.batch_probe_credentials([{"ip": IP}], "root", ["x", "y"], workers=1)
    assert rows[0]["matched_password_index"] is None
    assert "登入被拒" in rows[0]["error"]


# ===== 主機金鑰策略（真的 paramiko 金鑰、真的檔案）=====

def _count(path: Path) -> int:
    return len([ln for ln in path.read_text().splitlines() if ln.strip()])


def test_首見要寫進known_hosts(tmp_path, keys):
    kh = tmp_path / "known_hosts"
    pol = eng._AcceptNewPolicy(str(kh))
    pol.missing_host_key(None, IP, keys["a"])
    hk = paramiko.HostKeys(str(kh))
    assert hk.lookup(IP)["ssh-rsa"] == keys["a"], "首見沒寫進檔案＝下次又是首見＝沒驗證"
    pol.missing_host_key(None, IP, keys["a"])
    assert _count(kh) == 1, "同一把金鑰重複寫入"


def test_已記錄的主機換了金鑰要拒(tmp_path, keys):
    kh = tmp_path / "known_hosts"
    pol = eng._AcceptNewPolicy(str(kh))
    pol.missing_host_key(None, IP, keys["a"])
    before = kh.read_text()
    for other in (keys["b"], keys["ec"]):     # 同型別不同把、以及別種型別
        with pytest.raises(paramiko.SSHException, match="Host key verification failed"):
            pol.missing_host_key(None, IP, other)
    assert kh.read_text() == before, "被拒的金鑰不可以寫進去"


def test_known_hosts寫不進去就拒絕連線(tmp_path, keys):
    kh = tmp_path / "no-such-dir" / "known_hosts"
    with pytest.raises(paramiko.SSHException, match="寫不進去"):
        eng._AcceptNewPolicy(str(kh)).missing_host_key(None, IP, keys["a"])


def test_並行首見同一台只寫一行(tmp_path, keys):
    kh = tmp_path / "known_hosts"
    pol = eng._AcceptNewPolicy(str(kh))
    ts = [threading.Thread(target=pol.missing_host_key, args=(None, IP, keys["a"]))
          for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert _count(kh) == 1


def test_connect設定只用密碼且策略是accept_new(tmp_path, monkeypatch):
    kh = tmp_path / "known_hosts"
    kh.write_text("")
    monkeypatch.setattr(eng, "SSH_KNOWN_HOSTS", str(kh))
    rec = {}

    class FakeSSHClient:
        def load_host_keys(self, p): rec["loaded"] = p
        def set_missing_host_key_policy(self, p): rec["policy"] = p
        def connect(self, host, **kw): rec["kw"] = kw
        def close(self): pass

    monkeypatch.setattr(paramiko, "SSHClient", FakeSSHClient)
    eng._ssh_connect(IP, "u", SENTINEL, 5)
    assert rec["loaded"] == str(kh)
    assert isinstance(rec["policy"], eng._AcceptNewPolicy)
    assert rec["policy"].known_hosts == str(kh)
    kw = rec["kw"]
    assert kw["password"] == SENTINEL and kw["port"] == 22
    assert kw["allow_agent"] is False and kw["look_for_keys"] is False, \
        "會拿收集器自己的私鑰去試還沒納管的機器"


# ===== 端到端：本機起一個真的 SSH 伺服器 =====

class _Server(paramiko.ServerInterface):
    def __init__(self, pw):
        self.pw, self.command = pw, None
        self.exec_event = threading.Event()

    def check_channel_request(self, kind, chanid):
        return (paramiko.OPEN_SUCCEEDED if kind == "session"
                else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED)

    def get_allowed_auths(self, username): return "password"

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if password == self.pw else paramiko.AUTH_FAILED

    def check_channel_exec_request(self, channel, command):
        self.command = command.decode() if isinstance(command, bytes) else command
        self.exec_event.set()
        return True


def _serve_once(sock, hostkey, pw, out):
    conn, _ = sock.accept()
    t = paramiko.Transport(conn)
    t.add_server_key(hostkey)
    server = _Server(pw)
    try:
        t.start_server(server=server)
        chan = t.accept(5)
        if chan is None:
            return
        server.exec_event.wait(5)
        data = b""
        while True:
            d = chan.recv(4096)
            if not d:
                break
            data += d
        out["command"], out["stdin"] = server.command, data
        chan.sendall(f"got {len(data)} bytes\n完成。\n".encode("utf-8"))
        chan.send_exit_status(0)
        chan.close()
    except Exception as exc:  # noqa: BLE001
        out["server_error"] = repr(exc)
    finally:
        out.setdefault("command", server.command)
        time.sleep(0.2)
        t.close()


@pytest.fixture
def ssh_server(tmp_path, monkeypatch):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    monkeypatch.setattr(eng, "SSH_PORT", sock.getsockname()[1])
    monkeypatch.setattr(eng, "SSH_KNOWN_HOSTS", str(tmp_path / "known_hosts"))

    def run(hostkey, pw):
        out = {}
        th = threading.Thread(target=_serve_once, args=(sock, hostkey, pw, out), daemon=True)
        th.start()
        return out, th

    yield run
    sock.close()


def _call():
    return eng._ssh_executor("127.0.0.1", "sysinfra", SENTINEL, "linux", SCRIPT,
                             "127.0.0.1", timeout=5, as_root=False)


def test_端到端_密碼登入且sudo密碼走stdin(ssh_server, keys, tmp_path):
    out, th = ssh_server(keys["a"], SENTINEL)
    r = _call()
    th.join(10)
    assert r.ok, (r, out)
    assert out["command"] == eng.SUDO_STDIN_WRAPPER
    assert out["stdin"].decode("utf-8") == SENTINEL + "\n" + SCRIPT
    assert "[127.0.0.1]:" in (tmp_path / "known_hosts").read_text(), "首見沒記進 known_hosts"


def test_端到端_密碼錯歸connect(ssh_server, keys):
    out, th = ssh_server(keys["a"], "the-real-one")
    r = _call()
    th.join(10)
    assert not r.ok and r.stage == "connect" and "登入被拒" in r.message, r
    assert out.get("command") is None, "登入失敗卻有指令被執行"


def test_端到端_主機金鑰變了就不登入(ssh_server, keys):
    out1, th1 = ssh_server(keys["a"], SENTINEL)
    assert _call().ok
    th1.join(10)
    out2, th2 = ssh_server(keys["b"], SENTINEL)   # 同一個位址、換了主機金鑰
    r = _call()
    th2.join(10)
    assert not r.ok and r.stage == "connect" and "主機金鑰" in r.message, r
    assert out2.get("command") is None, "主機金鑰不符還把腳本（含密碼）送過去了"
