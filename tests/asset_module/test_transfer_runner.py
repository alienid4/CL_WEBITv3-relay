"""檔案傳送（Q94）的遠端執行層：Ansible 設定／inventory／腳本。**不碰網路、不真的跑 Ansible**。

守的重點：
  · 主機金鑰檢查維持開啟（accept-new＋專用 known_hosts），絕不關閉
  · 密碼不進任何檔案，只放子行程環境變數；臨時資料夾用完就刪
  · 送到目標機的腳本沒有 SOC 告警樣式（執行原則、編碼指令、base64…），路徑與檔名不能變成注入點
  · Linux／AIX 的每支腳本最前面都有 VMware 守門句
  · 能跑 sh 的機器上，真的把腳本跑一遍（驗雜湊相符才改名、不符就刪暫存、符號連結不處理…）
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import transfer_policy as pol  # noqa: E402
import transfer_runner as rn  # noqa: E402
import transfer_scripts as sc  # noqa: E402

FAKE_CRED = "pw-" + "secret-123"
WIN = rn.Target(ip="10.99.9.1", platform="windows", hostname="win1", username="svc-user", password=FAKE_CRED)
LNX = rn.Target(ip="10.99.8.1", platform="linux", hostname="lx1", username="webit3sc", key_path="/opt/webit3/.collector_key")
AIX = rn.Target(ip="10.99.8.3", platform="aix", hostname="aix1", username="webit3sc", key_path="/opt/webit3/.collector_key")


class FakeProc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _stream_popen(data: bytes, rc: int = 0, err: bytes = b"", log: list | None = None):
    """假的 Popen：stdout 吐出 data（供 ssh dd 串流抓回用），結束時回 rc。log 收到每次呼叫的 (cmd, kw)。"""
    import io

    class P:
        returncode = rc

        def __init__(self, cmd, **kw):
            if log is not None:
                log.append((cmd, kw))
            self.stdout = io.BytesIO(data)
            if kw.get("stderr") not in (None, subprocess.DEVNULL, subprocess.PIPE):
                kw["stderr"].write(err)
            self.terminated = False

        def wait(self, timeout=None):
            return rc

        def poll(self):
            return rc

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True
    return P


def make_runner(tmp_path, result: dict | None, seen: dict, rc=0):
    """假的 subprocess.run：偷看 Ansible 會讀到的檔案（跑完資料夾就刪了），並把結果寫到 out_file。"""
    def run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        inv = Path(cmd[cmd.index("-i") + 1])
        play = Path(cmd[cmd.index("-i") + 2])
        vars_path = Path(cmd[cmd.index("-e") + 1].lstrip("@"))
        seen["inv"] = inv.read_text(encoding="utf-8")
        seen["play"] = json.loads(play.read_text(encoding="utf-8"))
        seen["vars"] = json.loads(vars_path.read_text(encoding="utf-8"))
        seen["work"] = inv.parent
        seen["cfg"] = Path(kw["env"]["ANSIBLE_CONFIG"]).read_text(encoding="utf-8")
        if result is not None:
            Path(seen["vars"]["out_file"]).write_text(json.dumps(result), encoding="utf-8")
        return FakeProc(rc, "", "")
    return rn.AnsibleRunner(base_dir=tmp_path / "ans", run=run)


# ---------------------------------------------------------------- 設定與金鑰檢查
def test_主機金鑰檢查維持開啟(tmp_path):
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans")
    cfg = r.config_text()
    assert "host_key_checking = True" in cfg and "host_key_checking = False" not in cfg
    assert "StrictHostKeyChecking=accept-new" in cfg and "StrictHostKeyChecking=no" not in cfg
    import onboard_engine
    assert f"UserKnownHostsFile={onboard_engine.SSH_KNOWN_HOSTS}" in cfg
    assert "BatchMode=yes" in cfg and "ControlMaster=no" in cfg
    assert "/etc/ansible" not in cfg                                   # 不用系統的 ansible 設定與 inventory
    p = r.ensure_config()
    assert p == tmp_path / "ans" / "ansible.cfg" and p.read_text(encoding="utf-8") == cfg


class FakeSession:
    def __init__(self, tr):
        self.tr = tr

    def invoke(self, script, params=None, deadline_s=None):
        self.tr.calls.append((script, params))
        return self.tr.reply(script, params)

    def invoke_raw(self, script, params=None, deadline_s=None):
        self.tr.calls.append((script, params))
        return self.tr.raw_reply(script, params)


class FakeWin:
    """假的 Windows PSRP 傳輸：不碰網路。reply／raw_reply 由測試設定。"""

    def __init__(self, text="", errors=(), data=b""):
        self.calls, self.text, self.errors, self.data = [], text, list(errors), data
        self.targets = []
        self.fail_open = None

    def reply(self, script, params):
        return (["OK"] if params and "Bytes" in params else self.text.splitlines()), list(self.errors)

    def raw_reply(self, script, params):
        off, cnt = params["Offset"], params["Count"]
        chunk = self.data[off:off + cnt]
        return [len(chunk), chunk + b"\0" * (cnt - len(chunk))]

    def open(self, t, timeout=120):
        import contextlib
        self.targets.append(t)

        @contextlib.contextmanager
        def cm():
            if self.fail_open:
                raise self.fail_open
            yield FakeSession(self)
        return cm()


def test_Windows不走Ansible(tmp_path):
    """審查 BLOCKER 1：Windows 的 inventory／playbook 一律拒絕；探測走 PSRP 傳輸，ansible-playbook 一次都沒被叫。"""
    seen = {}
    called = []
    win = FakeWin("DIR=1\nFREE=5000\n__WEBIT_END__")
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", run=lambda *a, **k: called.append(a), win=win)
    with pytest.raises(rn.RunnerError, match="不走 Ansible"):
        r.inventory(WIN)
    p = r.probe(WIN, "D:\\Temp")
    assert p["dir_exists"] and p["free_bytes"] == 5000 and called == [] and seen == {}
    assert win.targets[0].password == FAKE_CRED and FAKE_CRED not in win.calls[0][0]       # 密碼不在腳本文字裡


def test_Linux用收集金鑰_不帶密碼環境變數(tmp_path):
    seen = {}
    r = make_runner(tmp_path, {"stdout": "DIR=1\n__WEBIT_END__\n", "rc": 0}, seen)
    r.probe(LNX, "/tmp")
    inv = json.loads(seen["inv"])["all"]["hosts"]["target"]
    assert inv["ansible_connection"] == "ssh" and inv["ansible_user"] == "webit3sc"
    assert inv["ansible_ssh_private_key_file"] == "/opt/webit3/.collector_key"
    assert "WEBIT3_ANSIBLE_PW" not in seen["kw"]["env"] and "ansible_password" not in inv


def test_用專用設定檔_隔離HOME_臨時資料夾用完就刪(tmp_path):
    seen = {}
    r = make_runner(tmp_path, {"stdout": "DIR=1\n__WEBIT_END__\n", "rc": 0}, seen)
    r.probe(LNX, "/tmp")
    env = seen["kw"]["env"]
    assert env["ANSIBLE_CONFIG"] == str(tmp_path / "ans" / "ansible.cfg") and env["HOME"] == str(tmp_path / "ans" / "home")
    assert env["ANSIBLE_HOST_KEY_CHECKING"] == "True"
    assert not seen["work"].exists()                                    # 含 inventory／變數檔，用完整個刪掉
    assert "-i" in seen["cmd"] and "/etc/ansible/hosts" not in " ".join(seen["cmd"])
    assert seen["cmd"][0] == "ansible-playbook"


def test_腳本內容當成不被樣板展開的資料送(tmp_path):
    seen = {}
    r = make_runner(tmp_path, {"stdout": "x", "rc": 0}, seen)
    r.run_command(LNX, "/tmp", "echo '{{ 1+1 }}' {% raw %}")
    assert isinstance(seen["vars"]["script"], dict) and "__ansible_unsafe" in seen["vars"]["script"]
    task = seen["play"][0]["tasks"][0]
    assert task["register"] == "r" and task["failed_when"] is False and task["ignore_unreachable"] is True
    assert task["ansible.builtin.raw"] == "{{ script }}" and task["args"] == {"executable": "/bin/sh"}


def test_Windows指令用純文字腳本_從資料夾開始(tmp_path):
    win = FakeWin("ok")
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", win=win)
    out = r.run_command(WIN, "D:\\Temp", "Get-ChildItem")
    script = win.calls[0][0]
    # 資料夾不存在（沒有 D 槽）退回 C:\Windows\Temp；指令本身原文不動、仍是純文字（不 base64）
    assert script.startswith("if (Test-Path -LiteralPath 'D:\\Temp') { Set-Location -LiteralPath 'D:\\Temp' } ")
    assert "else { Set-Location -LiteralPath 'C:\\Windows\\Temp' }" in script and script.endswith("\nGet-ChildItem\n")
    assert win.calls[0][1] is None
    assert out == {"rc": 0, "output": "ok"}
    win.errors = ["Get-Foo : not found"]
    assert r.run_command(WIN, "D:\\Temp", "Get-Foo")["rc"] == 1


def test_輸出與錯誤裡的密碼和指令文字會被遮掉(tmp_path):
    win = FakeWin("oops " + FAKE_CRED + " here; ran: net use X: secretcmd1234")
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", win=win)
    out = r.run_command(WIN, "D:\\Temp", "net use X: secretcmd1234")["output"]
    assert FAKE_CRED not in out and "secretcmd1234" not in out and "[指令已遮蔽]" in out
    import transfer_winrm as wt
    win2 = FakeWin()
    win2.fail_open = wt.WinTransportError("auth failed for " + FAKE_CRED + " running secretcmd9999")
    r2 = rn.AnsibleRunner(base_dir=tmp_path / "ans", win=win2)
    with pytest.raises(rn.RunnerError) as e:
        r2.run_command(WIN, "D:\\Temp", "secretcmd9999")
    assert FAKE_CRED not in str(e.value) and "secretcmd9999" not in str(e.value)


def test_Ansible錯誤尾巴不回顯指令文字(tmp_path):
    seen = {}

    def run(cmd, **kw):
        return FakeProc(2, "TASK [script] raw: echo topsecretvalue99 > /tmp/x failed", "")
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", run=run)
    with pytest.raises(rn.RunnerError) as e:
        r.run_command(LNX, "/tmp", "echo topsecretvalue99 > /tmp/x")
    assert "topsecretvalue99" not in str(e.value) and "[指令已遮蔽]" in str(e.value)


def test_連不上_沒結果_逾時都有清楚的錯誤(tmp_path):
    seen = {}
    r = make_runner(tmp_path, {"unreachable": True, "msg": "Failed to connect"}, seen)
    with pytest.raises(rn.RunnerError, match="連不上"):
        r.probe(LNX, "/tmp")
    with pytest.raises(rn.RunnerError, match="沒有產生結果"):
        make_runner(tmp_path, None, {}, rc=4).probe(LNX, "/tmp")

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(rn.RunnerError, match="逾時"):
        rn.AnsibleRunner(base_dir=tmp_path / "x", run=boom).probe(LNX, "/tmp")

    def nofile(cmd, **kw):
        raise FileNotFoundError("ansible-playbook")
    with pytest.raises(rn.RunnerError, match="找不到 ansible-playbook"):
        rn.AnsibleRunner(base_dir=tmp_path / "y", run=nofile).probe(LNX, "/tmp")


def test_遠端腳本沒跑完不當成功(tmp_path):
    seen = {}
    r = make_runner(tmp_path, {"stdout": "DIR=1\n", "rc": 0}, seen)            # 沒有結束標記
    with pytest.raises(rn.RunnerError, match="沒有跑完"):
        r.probe(LNX, "/tmp")


def test_VMware守門句命中_一律拒絕(tmp_path):
    seen = {}
    r = make_runner(tmp_path, {"stdout": sc.VMWARE_MARK + "\n", "rc": 0}, seen)
    with pytest.raises(rn.VmwareRefused):
        r.probe(LNX, "/tmp")
    with pytest.raises(rn.VmwareRefused):
        make_runner(tmp_path, {"stdout": sc.VMWARE_MARK + "\n", "rc": 0}, seen).run_command(LNX, "/tmp", "ls")


def test_解析探測結果(tmp_path):
    seen = {}
    out = ("DIR=1\nFREE_KB=2048\nEXIST=1\nSYMLINK=0\nSIZE=1234\nMTIME=Oct 29 10:00\n__WEBIT_END__\n")
    p = make_runner(tmp_path, {"stdout": out, "rc": 0}, seen).probe(LNX, "/tmp", "a.bin")
    assert p == {"dir_exists": True, "free_bytes": 2048 * 1024, "exists": True, "size": 1234, "mtime": "Oct 29 10:00", "symlink": False}
    out = "DIR=1\nFREE=5000\nEXIST=0\n__WEBIT_END__\n"
    p = rn.AnsibleRunner(base_dir=tmp_path / "ans", win=FakeWin(out)).probe(WIN, "D:\\Temp", "a.bin")
    assert p["free_bytes"] == 5000 and p["exists"] is False and p["dir_exists"]


def test_finalize結果由腳本回報(tmp_path):
    seen = {}
    sha = "a" * 64
    out = f"SHA256={sha}\nRESULT=ok\n__WEBIT_END__\n"
    res = make_runner(tmp_path, {"stdout": out, "rc": 0}, seen).finalize(LNX, "/tmp", "w3_1_1_x.webit3part", "a.bin", sha, False)
    assert res == {"result": "ok", "sha256": sha}
    assert sha in seen["vars"]["script"]["__ansible_unsafe"]


# ---------------------------------------------------------------- scp（Linux／AIX 檔案本體）
def test_scp限速與主機金鑰(tmp_path):
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans")
    cmd = r.scp_command(LNX, ["-q", "a", "webit3sc@10.99.8.1:/tmp/p"], 5)
    assert cmd[cmd.index("-l") + 1] == str(5 * 8 * 1024)                # 5 MB/s = 40960 Kbit/s
    assert "-B" in cmd and "BatchMode=yes" in cmd and "StrictHostKeyChecking=accept-new" in cmd
    assert not any("StrictHostKeyChecking=no" in c for c in cmd)
    assert cmd[cmd.index("-i") + 1] == "/opt/webit3/.collector_key"
    assert "-l" not in r.scp_command(LNX, ["a", "b"], None)


def test_scp失敗_逾時_都有原因(tmp_path):
    class Fail:
        returncode = 1

        def __init__(self, cmd, **kw):
            kw["stderr"].write(b"Permission denied (publickey)")

        def wait(self, timeout=None):
            return 1

        def poll(self):
            return 1
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", popen=Fail)
    with pytest.raises(rn.RunnerError, match="scp 失敗.*Permission denied"):
        r.upload(LNX, Path(__file__), "/tmp", "w3_1_1_a.webit3part", 5)


def test_Linux抓回只收單純檔名(tmp_path):
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", run=lambda *a, **k: FakeProc())
    for bad in ["a b.txt", "a(1).txt", "a;b", "$x", "中文.txt"]:
        with pytest.raises(rn.RunnerError, match="檔名含空白或特殊字元"):
            r.download(LNX, "/tmp", bad, tmp_path / "o", 5, 1000)
    assert rn.scp_safe_name("core.12345") and not rn.scp_safe_name("")


def test_暫存檔名是自己產的安全名稱():
    p = rn.part_name(12, 3, "ab12cd34")
    assert p == "w3_12_3_ab12cd34.webit3part" and rn.scp_safe_name(p) and p.endswith(pol.PART_SUFFIX)


# ---------------------------------------------------------------- 腳本
def _all_scripts():
    out = []
    for f in ("/tmp",):
        out += [sc.sh_probe(f, None), sc.sh_probe(f, "a b(1).zip"), sc.sh_part_free(f, "w3_1_1_x.webit3part"),
                sc.sh_sha256(f, "a.zip"), sc.sh_finalize(f, "w3_1_1_x.webit3part", "a.zip", "b" * 64, False),
                sc.sh_finalize(f, "w3_1_1_x.webit3part", "a.zip", "b" * 64, True),
                sc.sh_remove_part(f, "w3_1_1_x.webit3part"), sc.sh_list(f), sc.sh_command(f, "ls -l")]
    for f in ("D:\\Temp", "C:\\Windows\\Temp"):
        out += [sc.ps_probe(f, None), sc.ps_probe(f, "a b(1).zip"), sc.ps_part_free(f, "w3_1_1_x.webit3part"),
                sc.ps_sha256(f, "a.zip"), sc.ps_finalize(f, "w3_1_1_x.webit3part", "a.zip", "b" * 64, False),
                sc.ps_remove_part(f, "w3_1_1_x.webit3part"), sc.ps_list(f)]
    return out


FORBIDDEN = [r"Bypass", r"ExecutionPolicy", r"-EncodedCommand", r"(?<![\w-])-enc\b", r"base64", r"FromBase64String",
             r"Invoke-Expression", r"\biex\b", r"Add-Type", r"Start-Process", r"New-PSSession", r"Invoke-Command",
             r"DownloadString|DownloadFile|Invoke-WebRequest|\bcurl\b|\bwget\b", r"\.ps1\b", r"(?<![\w-])-File\b",
             r"StrictHostKeyChecking", r"sshpass", r"\{\{|\{%"]


def test_腳本沒有SOC告警樣式():
    for s in _all_scripts():
        for pat in FORBIDDEN:
            assert not re.search(pat, s, re.I), (pat, s[:200])


def test_Linux腳本最前面都有VMware守門句():
    for s in _all_scripts():
        if s.startswith("if ["):                         # sh 系列（守門句以 if [ 開頭）
            assert "VMkernel" in s.split("\n")[0] and sc.VMWARE_MARK in s.split("\n")[0], s[:120]
            assert "/etc/photon-release" in s.split("\n")[0]
    assert len([s for s in _all_scripts() if sc.VMWARE_MARK in s]) == 9


def test_路徑和檔名不能變成注入點():
    for bad in ["a'b", "a\nb", "a\x00b"]:
        with pytest.raises(sc.ScriptError):
            sc.sh_probe("/tmp", bad)
        with pytest.raises(sc.ScriptError):
            sc.ps_probe("D:\\Temp", bad)
    with pytest.raises(sc.ScriptError):
        sc.sh_finalize("/tmp", "p.webit3part", "a.zip", "not-a-hash", False)
    with pytest.raises(sc.ScriptError):
        sc.ps_finalize("D:\\Temp", "p.webit3part", "a.zip", "x" * 64, False)         # 不是 0-9a-f
    with pytest.raises(sc.ScriptError):
        sc.sh_remove_part("/tmp", "important.conf")                                    # 只能清自己的暫存檔
    with pytest.raises(sc.ScriptError):
        sc.ps_remove_part("D:\\Temp", "important.conf")


def test_解析器():
    kv = sc.parse_kv("DIR=1\nF|a|b.txt|12|Oct 29 10:00\nF|c.txt|5|x\nFREE=9\n__WEBIT_END__\n")
    assert kv["DIR"] == "1" and kv["complete"] and not kv["vmware"]
    assert kv["files"] == [{"name": "a|b.txt", "size": 12, "mtime": "Oct 29 10:00"}, {"name": "c.txt", "size": 5, "mtime": "x"}]
    assert not sc.parse_kv("DIR=1\n")["complete"] and sc.parse_kv(sc.VMWARE_MARK)["vmware"]
    assert sc.hash_of("SHA256 (x) = " + "AB" * 32) == "ab" * 32 and sc.hash_of("NOHASH") is None


# ---------------------------------------------------------------- 真的跑一次 Linux 腳本（有 sh 才跑）
def _sh():
    for name in ("sh", "bash"):
        p = shutil.which(name)
        if p:
            try:
                r = subprocess.run([p, "-c", "command -v sha256sum >/dev/null && echo ok"], capture_output=True, text=True, timeout=20)
                if r.stdout.strip() == "ok":
                    return p
            except (OSError, subprocess.SubprocessError):
                continue
    return None


@pytest.fixture
def shell():
    p = _sh()
    if not p:
        pytest.skip("這台沒有可用的 sh＋sha256sum")
    return p


def _run(shell, script):
    r = subprocess.run([shell, "-c", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return sc.parse_kv(r.stdout)


def test_實跑_探測_列檔_雜湊(shell, tmp_path):
    d = tmp_path.as_posix()
    (tmp_path / "a.txt").write_bytes(b"hello")
    kv = _run(shell, sc.sh_probe(d, "a.txt"))
    assert kv["complete"] and kv["DIR"] == "1" and kv["EXIST"] == "1" and kv["SIZE"] == "5" and kv["SYMLINK"] == "0"
    assert kv["FREE_KB"].isdigit()
    kv = _run(shell, sc.sh_probe(d, "nope.txt"))
    assert kv["EXIST"] == "0"
    assert _run(shell, sc.sh_probe(d + "/missing", None))["DIR"] == "0"
    kv = _run(shell, sc.sh_list(d))
    assert [f["name"] for f in kv["files"]] == ["a.txt"] and kv["files"][0]["size"] == 5
    import hashlib
    assert sc.hash_of(_run(shell, sc.sh_sha256(d, "a.txt"))["SHA256"]) == hashlib.sha256(b"hello").hexdigest()


def test_實跑_收尾_雜湊相符才改名(shell, tmp_path):
    import hashlib
    d = tmp_path.as_posix()
    (tmp_path / "p.webit3part").write_bytes(b"data")
    good = hashlib.sha256(b"data").hexdigest()
    kv = _run(shell, sc.sh_finalize(d, "p.webit3part", "final.bin", good, False))
    assert kv["RESULT"] == "ok" and (tmp_path / "final.bin").read_bytes() == b"data" and not (tmp_path / "p.webit3part").exists()


def test_實跑_雜湊不符_刪暫存_不留檔(shell, tmp_path):
    d = tmp_path.as_posix()
    (tmp_path / "p.webit3part").write_bytes(b"data")
    kv = _run(shell, sc.sh_finalize(d, "p.webit3part", "final.bin", "0" * 64, False))
    assert kv["RESULT"] == "mismatch" and kv["complete"]
    assert not (tmp_path / "p.webit3part").exists() and not (tmp_path / "final.bin").exists()


def test_實跑_已有同名檔_沒要覆蓋就不蓋(shell, tmp_path):
    import hashlib
    d = tmp_path.as_posix()
    (tmp_path / "final.bin").write_bytes(b"OLD")
    (tmp_path / "p.webit3part").write_bytes(b"data")
    good = hashlib.sha256(b"data").hexdigest()
    assert _run(shell, sc.sh_finalize(d, "p.webit3part", "final.bin", good, False))["RESULT"] == "exists"
    assert (tmp_path / "final.bin").read_bytes() == b"OLD" and not (tmp_path / "p.webit3part").exists()
    (tmp_path / "p.webit3part").write_bytes(b"data")
    assert _run(shell, sc.sh_finalize(d, "p.webit3part", "final.bin", good, True))["RESULT"] == "ok"
    assert (tmp_path / "final.bin").read_bytes() == b"data"


def test_實跑_暫存檔不見_回missing(shell, tmp_path):
    assert _run(shell, sc.sh_finalize(tmp_path.as_posix(), "none.webit3part", "f.bin", "0" * 64, False))["RESULT"] == "missing"


def test_實跑_清暫存檔(shell, tmp_path):
    (tmp_path / "p.webit3part").write_bytes(b"x")
    assert _run(shell, sc.sh_remove_part(tmp_path.as_posix(), "p.webit3part"))["REMOVED"] == "1"
    assert not (tmp_path / "p.webit3part").exists()


def test_實跑_使用者指令從資料夾開始(shell, tmp_path):
    (tmp_path / "marker.txt").write_text("x")
    r = subprocess.run([shell, "-c", sc.sh_command(tmp_path.as_posix(), "ls marker.txt")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == "marker.txt"
    r = subprocess.run([shell, "-c", sc.sh_command(tmp_path.as_posix() + "/missing", "echo no")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 97 and "no" not in r.stdout                      # 資料夾進不去就不跑指令


def test_實跑_守門句遇到VCSA標記就離開_什麼都不做(shell, tmp_path):
    """假裝是 VCSA：把守門句裡的標記檔路徑換成我們建的檔案，確認命中時只印標記、不執行本文。"""
    marker = tmp_path / "photon-release"
    marker.write_text("VMware Photon OS")
    (tmp_path / "victim.bin").write_bytes(b"x")
    script = sc.sh_remove_part(tmp_path.as_posix(), "victim.webit3part")
    script = script.replace("/etc/photon-release", marker.as_posix())
    r = subprocess.run([shell, "-c", script], capture_output=True, text=True, timeout=30)
    assert sc.VMWARE_MARK in r.stdout and "REMOVED" not in r.stdout and r.returncode == 0
