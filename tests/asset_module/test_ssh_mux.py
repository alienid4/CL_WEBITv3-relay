"""收集全部對同一台只開一條 SSH 連線；登入前被斷線要重試、講實話（2026-10-21 公司 v1.480.2 實測）。

症狀：RHEL 9.6 一台「收集全部」——硬體規格 ✓，接著服務／軟體／帳號／OS 重建／組態檢核
0.1～1.1 秒內全 ✕；OS 重建的訊息有原文 `kex_exchange_identification: read: Connection reset by peer`，
其他幾樣卻寫「沒有收到資料」、組態檢核寫「帳號/金鑰/防火牆」。

根因：每條收集指令一支 ssh 行程＝一條新 TCP 連線，幾秒內幾十條，目標機（MaxStartups／IPS）
在登入前就砍。這裡用一台**假 sshd**（只認 ssh 參數，模擬 ControlMaster 共用與「第 N 條以後砍掉」）
跑**真的**收集器與真的 SSHRunner，數連線數。
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

import config_audit  # noqa: E402
import db  # noqa: E402
import health_probe  # noqa: E402
import manage_state as ms  # noqa: E402
import post_onboard  # noqa: E402
import ssh_mux  # noqa: E402
from test_os_rebuild_collector import FakeHost  # noqa: E402
from test_post_onboard_all_paths import IP, SERIAL, _add, _report_text  # noqa: E402

RESET = "kex_exchange_identification: read: Connection reset by peer\r\nConnection reset by 10.99.4.140 port 22\r\n"


class FakeSSHD:
    """假的 ssh(1)＋目標機 sshd。

    * argv 有 `ControlMaster=auto` 且這個 user@host 已有共用連線 → 走既有連線（不算新連線）
    * 否則＝新 TCP 連線，`connects += 1`；超過 `limit` 條就在登入前砍掉（rc 255＋kex 原文）
    """

    def __init__(self, host, limit=None):
        self.host = host
        self.limit = limit
        self.connects = 0
        self.masters: set[str] = set()
        self.cur = 0
        self.max_cur = 0
        self._g = threading.Lock()

    def __call__(self, argv, **kw):
        if not argv or argv[0] != "ssh":
            raise AssertionError(f"不該有非 ssh 的子行程：{argv}")
        target, cmd = argv[-2], argv[-1]
        mux = "ControlMaster=auto" in argv
        with self._g:
            self.cur += 1
            self.max_cur = max(self.max_cur, self.cur)
        try:
            time.sleep(0.001)
            if not (mux and target in self.masters):
                self.connects += 1
                if self.limit is not None and self.connects > self.limit:
                    return subprocess.CompletedProcess(argv, 255, stdout="", stderr=RESET)
                if mux:
                    self.masters.add(target)
            ip = target.split("@", 1)[1]
            if "__FCBRHEL_EOF__" in cmd:
                return subprocess.CompletedProcess(argv, 0, stdout=_report_text(), stderr="")
            out = self.host(ip, cmd)
            last = self.host.last or {"rc": 0, "err": ""}
            return subprocess.CompletedProcess(argv, last["rc"], stdout=out, stderr=last["err"])
        finally:
            with self._g:
                self.cur -= 1


@pytest.fixture()
def env(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    monkeypatch.setenv("ASSET_DB_PATH", str(p))
    db.init_db(p)
    ssh_mux.reset()
    sleeps: list[float] = []
    monkeypatch.setattr(ssh_mux, "_sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(health_probe, "check_one", lambda *a, **k: {})
    _add(p, collect_ok=1)
    yield p, sleeps
    ssh_mux.reset()


def _mux(monkeypatch, tmp_path, on: bool):
    monkeypatch.setattr(ssh_mux, "mux_enabled", lambda: on)
    monkeypatch.setattr(ssh_mux, "mux_dir", lambda: str(tmp_path / "mux"))


def _no_transport_failure(res):
    """假主機只對 OS 重建／組態檢核／規格給完整輸出（服務、帳號的空輸出是假資料的事，不是連線）；
    重點是：沒有任何一樣是因為連線被砍而失敗，而且 OS 重建、組態檢核真的收到了。"""
    for r in res.values():
        assert ssh_mux.PREAUTH_MSG not in str(r["message"]), r
    assert res["spec"]["ok"] is True and res["rebuild"]["ok"] is True, res
    assert res["config_audit"]["ok"] is True, res["config_audit"]


def _collect_all(p):
    conn = db.get_connection(p)
    try:
        return {r["job"]: r for r in post_onboard.run_now(conn, SERIAL, source="manual")}
    finally:
        conn.close()


def test_收集全部_同一台只開一條連線(env, monkeypatch, tmp_path):
    p, _ = env
    _mux(monkeypatch, tmp_path, True)
    sshd = FakeSSHD(FakeHost(nonroot={"hosts": "127.0.0.1 localhost"}))
    monkeypatch.setattr(subprocess, "run", sshd)
    res = _collect_all(p)
    assert sshd.connects == 1, f"收集全部對同一台開了 {sshd.connects} 條連線"
    assert sshd.max_cur == 1
    _no_transport_failure(res)


def test_沒有共用連線時_每條指令各連一次_這就是公司踩到的(env, monkeypatch, tmp_path):
    """對照組：關掉共用 → 連線數＝指令數（幾十條），證明上一個測試的 1 是共用造成的。"""
    p, _ = env
    _mux(monkeypatch, tmp_path, False)
    sshd = FakeSSHD(FakeHost(nonroot={"hosts": "127.0.0.1 localhost"}))
    monkeypatch.setattr(subprocess, "run", sshd)
    _collect_all(p)
    assert sshd.connects > 10


def test_目標機限連線數_有共用就全收得到(env, monkeypatch, tmp_path):
    p, sleeps = env
    _mux(monkeypatch, tmp_path, True)
    sshd = FakeSSHD(FakeHost(nonroot={"hosts": "127.0.0.1 localhost"}), limit=3)
    monkeypatch.setattr(subprocess, "run", sshd)
    res = _collect_all(p)
    _no_transport_failure(res)
    assert sleeps == []


def test_登入前被斷線_每一格都講實話_不是沒有收到資料(env, monkeypatch, tmp_path):
    """重現公司畫面：共用關掉＋目標機第 3 條以後砍。規格之後幾樣要寫真正原因。"""
    p, sleeps = env
    _mux(monkeypatch, tmp_path, False)
    sshd = FakeSSHD(FakeHost(nonroot={"hosts": "127.0.0.1 localhost"}), limit=3)
    monkeypatch.setattr(subprocess, "run", sshd)
    res = _collect_all(p)
    failed = [r for r in res.values() if r["ok"] is False]
    assert failed, res
    for r in failed:
        assert ssh_mux.PREAUTH_MSG in r["message"], r
        assert "沒有收到資料" not in r["message"] and "帳號/金鑰" not in r["message"], r
    assert sleeps[:3] == [2, 5, 10], "要等 2、5、10 秒重試"
    # 冷卻期間不再連：重試到放棄之後，後面幾樣不會再對這台連打
    before = sshd.connects
    assert before <= 3 + 1 + len(ssh_mux.BACKOFF_S), f"放棄後還在連（共 {before} 條）"


def test_偶發斷線_重試後成功(env, monkeypatch):
    calls = iter([(255, "", RESET), (255, "", RESET), (0, "Linux\n", "")])

    def fake(argv, **kw):
        rc, out, err = next(calls)
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr=err)
    monkeypatch.setattr(subprocess, "run", fake)
    _, sleeps = env
    run = ms.SSHRunner("/k", account="webit3scan", timeout=5)
    assert run("10.99.4.141", "uname -s") == "Linux\n"
    assert sleeps == [2, 5]
    assert run.trace["10.99.4.141"]["connected"] is True


def test_組態檢核_連線被斷要講實話不叫人查帳號金鑰(env, monkeypatch, tmp_path):
    p, _ = env
    _mux(monkeypatch, tmp_path, True)
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 255, "", RESET))
    conn = db.get_connection(p)
    try:
        with pytest.raises(ValueError) as ei:
            config_audit.collect_from_host(conn, SERIAL, by="t")
    finally:
        conn.close()
    assert ssh_mux.PREAUTH_MSG in str(ei.value)
    assert "帳號/金鑰" not in str(ei.value)


def test_同一台不並行(env, monkeypatch):
    sshd = FakeSSHD(FakeHost(nonroot={"hosts": "x"}))
    monkeypatch.setattr(subprocess, "run", sshd)

    def work():
        r = ms.SSHRunner("/k", account="webit3scan", timeout=5)
        for _ in range(5):
            r(IP, "hostname")
    ts = [threading.Thread(target=work) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sshd.max_cur == 1


def test_權限不足不算登入前斷線_不重試():
    assert not ssh_mux.is_preauth_reset("Permission denied (publickey).")
    assert not ssh_mux.is_preauth_reset("ssh: connect to host x port 22: Connection timed out")
    assert ssh_mux.is_preauth_reset(RESET)
    assert ssh_mux.is_preauth_reset("Connection reset by 10.99.4.140 port 22")
    # 登入後才斷（指令可能已跑過）：不可以當成登入前斷線重送
    assert not ssh_mux.is_preauth_reset("client_loop: send disconnect: Connection reset by peer")
    assert not ssh_mux.is_preauth_reset("curl: (56) Recv failure: Connection reset by peer")


def test_共用選項格式(monkeypatch, tmp_path):
    monkeypatch.setattr(ssh_mux, "mux_enabled", lambda: True)
    monkeypatch.setattr(ssh_mux, "mux_dir", lambda: str(tmp_path))
    o = ssh_mux.mux_opts()
    assert "ControlMaster=auto" in o and any(x.startswith("ControlPath=") and x.endswith("%C") for x in o)
    assert any(x.startswith("ControlPersist=") for x in o)


def test_可以用環境變數關掉共用(monkeypatch):
    monkeypatch.setenv("WEBIT3_SSH_MUX", "0")
    assert ssh_mux.mux_opts() == []
