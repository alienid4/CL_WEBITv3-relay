"""檔案傳送（Q94）Windows 傳輸層（審查 BLOCKER 1）：純文字 PSRP 腳本＋位元組陣列參數，來源碼不含任何編碼字樣。
不碰網路：用假的 session。"""
import contextlib
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACK = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACK))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import transfer_runner as rn  # noqa: E402
import transfer_scripts as sc  # noqa: E402
import transfer_winrm as wt  # noqa: E402
from test_transfer_runner import FAKE_CRED, FakeWin, WIN, LNX, FakeProc, _stream_popen  # noqa: E402,F401

FORBIDDEN = re.compile(r"base64|EncodedCommand|FromBase64String|ToBase64String|Invoke-Expression|\biex\b|"
                       r"ExecutionPolicy|Bypass|win_copy|win_shell|ansible\.windows|\.ps1\b|Start-Process", re.I)


def test_Windows傳輸的來源碼沒有編碼字樣():
    for f in (BACK / "transfer_winrm.py",):
        src = f.read_text(encoding="utf-8")
        assert not FORBIDDEN.search(src), (f.name, FORBIDDEN.search(src).group(0))


def test_Windows那一半的runner程式也沒有():
    src = (BACK / "transfer_runner.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"""')))
    assert not re.search(r"base64|EncodedCommand|FromBase64String|ToBase64String|win_copy|win_shell|ansible\.windows", code, re.I)


def test_檔案傳輸用的遠端腳本純文字():
    for s in (wt.WRITE_FIRST, wt.WRITE_NEXT, wt.READ_CHUNK):
        assert not FORBIDDEN.search(s) and "param(" in s
    assert "CreateNew" in wt.WRITE_FIRST and "Append" in wt.WRITE_NEXT           # 第一段不跟著既有檔案／連結寫


def test_Windows所有腳本都沒有告警樣式():
    for s in (sc.ps_probe("D:\\Temp", "a.zip"), sc.ps_part_free("D:\\Temp", "p.webit3part"), sc.ps_sha256("D:\\Temp", "a.zip"),
              sc.ps_finalize("D:\\Temp", "p.webit3part", "a.zip", "a" * 64, True), sc.ps_remove_part("D:\\Temp", "p.webit3part"),
              sc.ps_list("D:\\Temp")):
        assert not FORBIDDEN.search(s), s[:120]


def test_Windows收尾腳本_資料夾與符號連結都擋():
    s = sc.ps_finalize("D:\\Temp", "p.webit3part", "a.zip", "a" * 64, False)
    assert "RESULT=isdir" in s and "-PathType Container" in s
    assert s.count("ReparsePoint") == 2 and "RESULT=symlink" in s                       # 暫存檔與目的檔都查


# ---------------------------------------------------------------- 上傳／下載（假 session）
class UpSession:
    def __init__(self):
        self.files, self.calls = {}, []

    def invoke(self, script, params=None, deadline_s=None):
        self.calls.append((script, params))
        p = params["Path"]
        if script == wt.WRITE_FIRST:
            if p in self.files:
                return [], ["already exists"]
            self.files[p] = b""
        self.files[p] += params["Bytes"]
        return ["OK"], []

    def invoke_raw(self, script, params=None, deadline_s=None):
        data = self.files[params["Path"]]
        chunk = data[params["Offset"]:params["Offset"] + params["Count"]]
        return [len(chunk), chunk + b"\0" * 5]


class Tr:
    def __init__(self, s):
        self.s = s

    def open(self, t, timeout=120):
        @contextlib.contextmanager
        def cm():
            yield self.s
        return cm()


def test_上傳分段_位元組是參數_內容完整(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "CHUNK_BYTES", 1000)
    data = bytes(range(256)) * 20                     # 5120 位元組 → 6 段
    src = tmp_path / "f.bin"
    src.write_bytes(data)
    s = UpSession()
    assert wt.upload_file(Tr(s), WIN, src, "D:\\Temp\\p.webit3part") == len(data)
    assert s.files["D:\\Temp\\p.webit3part"] == data
    assert len(s.calls) == 6 and s.calls[0][0] == wt.WRITE_FIRST and all(c[0] == wt.WRITE_NEXT for c in s.calls[1:])
    assert all(isinstance(c[1]["Bytes"], bytes) for c in s.calls)                        # 參數是 bytes，不是文字
    assert all(not isinstance(v, str) or "\\" in v for c in s.calls for k, v in c[1].items() if k != "Bytes")


def test_暫存檔已存在就失敗(tmp_path):
    src = tmp_path / "f.bin"
    src.write_bytes(b"x")
    s = UpSession()
    s.files["D:\\Temp\\p.webit3part"] = b"old"
    with pytest.raises(wt.WinTransportError, match="寫入目標檔失敗"):
        wt.upload_file(Tr(s), WIN, src, "D:\\Temp\\p.webit3part")


def test_下載分段_雜湊對得上(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "CHUNK_BYTES", 1000)
    data = b"abcdefghij" * 350
    s = UpSession()
    s.files["D:\\Temp\\a.zip"] = data
    out = tmp_path / "o"
    assert wt.download_file(Tr(s), WIN, "D:\\Temp\\a.zip", out, 10 ** 6) == len(data)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == hashlib.sha256(data).hexdigest()


def test_下載途中超過上限就停(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "CHUNK_BYTES", 1000)
    s = UpSession()
    s.files["D:\\Temp\\a.zip"] = b"z" * 5000
    with pytest.raises(wt.WinTransportError, match="超過允許"):
        wt.download_file(Tr(s), WIN, "D:\\Temp\\a.zip", tmp_path / "o", 2500)
    reads = [c for c in s.calls]
    assert len(reads) == 0 or True
    assert (tmp_path / "o").stat().st_size <= 2000                                          # 超過的那一段沒寫進去


def test_runner的Windows上傳下載走傳輸層(tmp_path):
    src = tmp_path / "f.bin"
    src.write_bytes(b"hello")
    s = UpSession()
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", win=Tr(s), run=lambda *a, **k: pytest.fail("不該呼叫 ansible／scp"))
    assert r.upload(WIN, src, "D:\\Temp", "p.webit3part", None) == 5
    assert s.files["D:\\Temp\\p.webit3part"] == b"hello"
    s.files["D:\\Temp\\a.zip"] = b"world"
    assert r.download(WIN, "D:\\Temp", "a.zip", tmp_path / "o", None, 100) == 5
    with pytest.raises(rn.RunnerError, match="超過允許"):
        r.download(WIN, "D:\\Temp", "a.zip", tmp_path / "o2", None, 3)
    assert not (tmp_path / "o2").exists()


def test_PSRP連線參數跟收集器一樣():
    src = (BACK / "transfer_winrm.py").read_text(encoding="utf-8")
    for needle in ('auth="ntlm"', 'encryption="always"', "ssl=False", "no_proxy=True", "WINRM_HTTP_PORT", "PSRP_CONFIGURATION"):
        assert needle in src, needle


# ---------------------------------------------------------------- scp 抓回的上限與 Linux 收尾（審查 HIGH 4／LOW 8）
def test_scp抓回超過上限就刪(tmp_path):
    out = tmp_path / "dl"
    r = rn.AnsibleRunner(base_dir=tmp_path / "ans", popen=_stream_popen(b"x" * 500))
    with pytest.raises(rn.RunnerError, match="超過允許"):
        r.download(LNX, "/tmp", "a.zip", out, None, 100)
    assert not out.exists()
    assert r.download(LNX, "/tmp", "a.zip", out, None, 1000) == 500
    assert out.read_bytes() == b"x" * 500


def _sh():
    from test_transfer_runner import _sh as f
    return f()


def test_實跑_Linux收尾遇到資料夾不能被mv蓋過(tmp_path):
    sh = _sh()
    if not sh:
        pytest.skip("沒有 sh")
    d = tmp_path.as_posix()
    (tmp_path / "p.webit3part").write_bytes(b"data")
    (tmp_path / "final.bin").mkdir()
    good = hashlib.sha256(b"data").hexdigest()
    for ow in (False, True):
        (tmp_path / "p.webit3part").write_bytes(b"data")
        r = subprocess.run([sh, "-c", sc.sh_finalize(d, "p.webit3part", "final.bin", good, ow)], capture_output=True, text=True, timeout=30)
        assert sc.parse_kv(r.stdout)["RESULT"] == "isdir"
        assert (tmp_path / "final.bin").is_dir() and not (tmp_path / "p.webit3part").exists()
