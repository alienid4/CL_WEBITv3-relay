"""舊版 SSH 測連線小工具（2026-09-15）。

要守的：
1. **絕不認證**：握手完就斷線，伺服器端的認證函式一次都不能被叫到
2. TCP 連不上、SSH 談判失敗、談成，三種結果結束碼與訊息分得清楚
3. 談判細節從 paramiko log 挑得出來
"""
import importlib.util
import socket
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "APP" / "asset-module" / "backend" / "tools" / "ssh_legacy_probe.py"
spec = importlib.util.spec_from_file_location("ssh_legacy_probe", SCRIPT)
probe_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe_mod)

paramiko = pytest.importorskip("paramiko")


def test_TCP連不上回3():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                  # 關掉 → 這個埠一定連不上
    code, lines = probe_mod.probe("127.0.0.1", port, timeout=3)
    assert code == 3 and any("TCP 連不上" in l for l in lines)


def test_談成但絕不認證():
    auth_calls = []
    host_key = paramiko.RSAKey.generate(2048)

    class Srv(paramiko.ServerInterface):
        def check_auth_password(self, u, p):
            auth_calls.append(u)
            return paramiko.AUTH_FAILED

        def get_allowed_auths(self, u):
            return "password"

    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(1)
    port = lsock.getsockname()[1]

    def serve():
        c, _ = lsock.accept()
        t = paramiko.Transport(c)
        t.add_server_key(host_key)
        try:
            t.start_server(server=Srv())
            t.accept(3)
        except Exception:  # noqa: BLE001 - 用戶端握手完就斷，伺服器端丟例外是正常的
            pass
        finally:
            t.close()

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    code, lines = probe_mod.probe("127.0.0.1", port, timeout=10)
    th.join(5)
    lsock.close()
    assert code == 0, lines
    assert any("談判成功" in l for l in lines)
    assert any("主機金鑰：ssh-rsa" in l or "主機金鑰：rsa" in l for l in lines)
    assert auth_calls == [], "測連線工具絕對不能送帳密"


def test_談判細節挑得出來():
    log = ("DEBUG:paramiko.transport:Kex agreed: diffie-hellman-group14-sha1\n"
           "DEBUG:paramiko.transport:HostKey agreed: ssh-dss\n"
           "DEBUG:paramiko.transport:some unrelated line\n")
    out = probe_mod.summarize_log(log)
    assert any("Kex agreed" in l for l in out) and any("ssh-dss" in l for l in out)
    assert not any("unrelated" in l for l in out)
    # paramiko 3.5.1 實際的寫法是「Kex: …」「HostKey: …」（不帶 agreed），沒有 formatter 時也不帶前綴
    real = ("=== Key exchange agreements ===\n"
            "Kex: diffie-hellman-group1-sha1\n"
            "HostKey: ssh-dss\n"
            "Cipher: local=aes128-cbc, remote=aes128-cbc\n")
    got = probe_mod.summarize_log(real)
    assert "Kex: diffie-hellman-group1-sha1" in got and "HostKey: ssh-dss" in got
    assert any(l.startswith("Cipher:") for l in got)


def test_參數錯誤回4(capsys):
    assert probe_mod.main(["x"]) == 4
