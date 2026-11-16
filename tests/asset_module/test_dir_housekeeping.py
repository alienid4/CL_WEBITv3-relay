"""目錄清理掃描（dir_housekeeping，2.007.17）：時段、BUSY、解析、閘門、佇列、腳本安全、真的跑一次 Linux／AIX awk。

使用者：「手動＋BUSY 退出＋08:30～15:00 不可執行」「空間所有 OS 都要」。
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import api  # noqa: E402
import db  # noqa: E402
import dir_housekeeping as hk  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_STUB = "test-password-123"
NIGHT = dt.datetime(2026, 10, 28, 22, 0)
DAY = dt.datetime(2026, 10, 28, 10, 0)

LINUX_OUT = (
    "@@FS\t/data\t91\n"
    "@@FILE\t/data\t3072\t1715398858\t/data/app/logs/a.log\n"
    "@@DIR\t/data\t/data\t8492\t5120\t2\t1792726858\n"
    "@@DIR\t/data\t/data/app\t5420\t3072\t1\t1792726858\n"
    "@@DIR\t/data\t/data/tmp\t3072\t2048\t1\t1689478858\n"
    "@@STAT\t/data\t7\t0\n@@DONE\n")


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="L1", hostname="lx1", ip="10.99.8.1", os="Red Hat Enterprise Linux 8",
                       asset_status="使用中")
    db.insert_hardware(c, asset_serial="L2", hostname="lx2", ip="10.99.8.2", os="Red Hat Enterprise Linux 8",
                       asset_status="使用中")
    c.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = 'L1'")
    c.commit()
    return c, p


@pytest.fixture
def night(monkeypatch):
    monkeypatch.setattr(hk, "_now", lambda: NIGHT)


# ---------------------------------------------------------------- 時段
def test_08_30到15_00不可執行():
    for h, m, blocked in [(8, 29, False), (8, 30, True), (12, 0, True), (14, 59, True), (15, 0, False), (23, 0, False), (3, 0, False)]:
        assert hk.in_blocked_window(dt.datetime(2026, 10, 28, h, m)) is blocked, (h, m)
    assert hk.window_status(DAY)["blocked"] and "15:00" in hk.window_status(DAY)["message"]


def test_上班時段伺服器端直接拒絕(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        monkeypatch.setattr(hk, "_now", lambda: DAY)
        with pytest.raises(PermissionError):
            hk.queue_scan(c, ["L1"], "admin", sync=True)
        assert hk.scan_host(c, "L1")["status"] == "blocked"
        assert c.execute("SELECT COUNT(*) FROM hk_scan").fetchone()[0] == 0     # 連排隊都沒有
        c.close()


# ---------------------------------------------------------------- 解析
def test_解析與忙碌退出():
    p = hk.parse_output(LINUX_OUT)
    assert p["status"] == "ok" and p["fs"] == [{"mount": "/data", "pct": 91}]
    assert p["stats"]["/data"]["denied"] == 7 and len(p["dirs"]) == 3
    assert hk.parse_output("@@BUSY\tload=9.1\tcpus=4\n")["status"] == "busy"
    assert hk.parse_output("@@NOFS\t80\n")["status"] == "nofs"
    assert hk.parse_output("garbage")["status"] == "failed"                    # 沒有結束標記＝沒跑完，不猜
    part = hk.parse_output(LINUX_OUT.replace("@@DONE", "@@STAT\t/data\t0\t1\n@@DONE"))
    assert part["status"] == "partial"


def test_summary寫出權限與逾時的實話():
    p = hk.parse_output(LINUX_OUT.replace("@@DONE", "@@STAT\t/data\t0\t1\n@@DONE"))
    s = hk.build_summary("lx1", p, 80, "2026-10-28 22:00")
    assert "未刪除或變更任何檔案" in s and "/data/app" in s and "7 個目錄沒有權限" in s and "只掃了一部分" in s
    assert "2 年以上" in s


# ---------------------------------------------------------------- 閘門＋佇列
def test_沒納管的不掃(night):
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        r = hk.scan_host(c, "L2", runner=lambda h, cmd: LINUX_OUT)
        assert r["status"] == "failed" and r["note"]
        c.close()


def test_VMware不掃(night, monkeypatch):
    import manage_state
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        monkeypatch.setattr(manage_state, "drop_vmware_targets",
                            lambda conn, rows: ([], [{"asset_serial": "L1", "ip": "10.99.8.1", "reason": "VMware 不可納管"}]))
        called = []
        r = hk.scan_host(c, "L1", runner=lambda h, cmd: called.append(cmd) or LINUX_OUT)
        assert r["status"] == "failed" and "VMware" in r["note"] and not called   # 連一條指令都沒送
        c.close()


def test_Linux掃描完整流程(night):
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        sent = []
        r = hk.queue_scan(c, ["L1"], "admin", sync=True, runner=lambda h, cmd: sent.append(cmd) or LINUX_OUT)
        assert r["queued"] == 1
        res = hk.result(c, r["ids"][0])
        assert res["status"] == "ok" and res["detail"]["summary"] and res["detail"]["dirs"]
        assert res["requested_by"] == "admin" and res["platform"] == "linux"
        assert any("find" in c and "-printf" in c for c in sent)                      # 送的是 Linux 版
        ov = hk.overview(c)
        assert [h["serial"] for h in ov["hosts"]] == ["L1"] and ov["hosts"][0]["status_label"] == "完成"
        c.close()


def test_主機忙就退出且不寫摘要(night):
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        r = hk.queue_scan(c, ["L1"], "admin", sync=True, runner=lambda h, cmd: "@@BUSY\tload=9.00\tcpus=2\n")
        res = hk.result(c, r["ids"][0])
        assert res["status"] == "busy" and "忙碌" in res["detail"]["note"] and "summary" not in res["detail"]
        c.close()


def test_沒有檔案系統超標_不需清理(night):
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        r = hk.queue_scan(c, ["L1"], "admin", sync=True, runner=lambda h, cmd: "@@NOFS\t80\n")
        assert hk.result(c, r["ids"][0])["status"] == "nofs"
        c.close()


def test_佇列跑到0830就停(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        c.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = 'L2'")
        c.commit()
        clock = {"t": NIGHT}
        monkeypatch.setattr(hk, "_now", lambda: clock["t"])

        def runner(h, cmd):
            clock["t"] = DAY            # 第一台掃完，時間到了 10:00
            return LINUX_OUT
        r = hk.queue_scan(c, ["L1", "L2"], "admin", sync=True, runner=runner)
        st = [hk.result(c, i)["status"] for i in r["ids"]]
        assert st == ["ok", "stopped"], st
        c.close()


def test_一次只能一批(night):
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        hk._active["running"] = True
        try:
            with pytest.raises(RuntimeError):
                hk.queue_scan(c, ["L1"], "admin", sync=True)
        finally:
            hk._active["running"] = False
        with pytest.raises(ValueError):
            hk.queue_scan(c, [], "admin", sync=True)
        c.close()


def test_Windows走scriptblock並收下輸出(night, monkeypatch):
    import win_onboard
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        c.execute("UPDATE hardware SET os='Windows Server 2019' WHERE asset_serial='L1'")
        c.commit()
        monkeypatch.setattr(win_onboard, "collect_block", lambda *a, **k: None)
        seen = []
        out = "【磁碟空間 Housekeeping 盤點】\n主機：WIN1\n■ 磁碟使用率\n  D:　91%"
        r = hk.scan_host(c, "L1", win_runner=lambda ip, s: seen.append(s) or out)
        assert r["status"] == "ok" and "Housekeeping" in r["summary"] and r["platform"] == "windows"
        assert "[scriptblock]::Create" in seen[0] and "-NoReport" in seen[0] and "*>&1" in seen[0]
        busy = hk.scan_host(c, "L1", win_runner=lambda ip, s: "系統負載過高，本次不執行：CPU 95%")
        assert busy["status"] == "busy"
        c.close()


# ---------------------------------------------------------------- 腳本安全（SOC：唯讀、純文字、不編碼、不寫檔）
@pytest.mark.parametrize("name,fn", [("linux", hk.linux_script), ("aix", hk.aix_script)])
def test_Linux_AIX腳本唯讀且純文字(name, fn):
    s = fn(80)
    for bad in ("base64", "sshpass", "sudo", " rm ", "rm -", "mv ", "chmod", "chown", "> /", ">>", "tee ", "mktemp", "eval ",
                "curl", "wget", "kill -9"):
        assert bad not in s, (name, bad)
    assert "-xdev" in s and "nice" in s


def test_Windows腳本不碰SOC紅線():
    s = hk.windows_script(80)
    low = s.lower()
    for bad in ("-executionpolicy", "bypass", "invoke-expression", "iex ", "add-type", "frombase64", "-encodedcommand",
                "downloadstring", "remove-item", "set-content"):
        assert bad not in low, bad
    assert "-NoReport" in s and "exit " not in "\n".join(l for l in s.splitlines() if l.strip().startswith("exit"))
    assert not any(l.strip().startswith("exit") for l in s.splitlines())          # exit 會結束整個 PSRP 工作階段
    for ip in ("192.168.", "10.93."):
        assert ip not in s


# ---------------------------------------------------------------- 真的跑一次 awk（有 bash＋GNU find 才跑）
def _find_bash():
    """能直接讀 Windows 原生路徑、又有 GNU find 的 bash（Git Bash）；WSL 的 bash 讀不到 C:/ 路徑，不用。"""
    git = os.environ.get("ProgramFiles", "C:/Program Files") + "/Git"
    for c in (shutil.which("bash"), git + "/bin/bash.exe", git + "/usr/bin/bash.exe"):
        if not c or not os.path.exists(c):
            continue
        try:
            f = Path(tempfile.mkdtemp()) / "p.sh"
            f.write_text("find . -maxdepth 0 -printf x\n", encoding="utf-8", newline="\n")
            r = subprocess.run([c, f.as_posix()], capture_output=True, text=True, timeout=20)
            if r.stdout == "x":
                return c
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _bash_ok():
    return _find_bash() is not None


def _mk(d, rel, size, age_days):
    p = os.path.join(d, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(b"x" * size)
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))


@pytest.mark.skipif(not _bash_ok(), reason="需要 bash 與 GNU find")
@pytest.mark.parametrize("kind", ["linux", "aix"])
def test_腳本在真目錄上算得對(kind):
    d = tempfile.mkdtemp().replace("\\", "/")
    try:
        _mk(d, "app/logs/a.log", 3 * 1024 * 1024, 900)      # 超過 2 年
        _mk(d, "app/logs/b.log", 2 * 1024 * 1024, 10)
        _mk(d, "app/data/big.bin", 300 * 1024, 5)
        _mk(d, "tmp/old1", 2 * 1024 * 1024, 1200)           # 超過 2 年
        _mk(d, "tmp/mid", 1024 * 1024, 700)                 # 不到 2 年，不算舊
        s = hk.linux_script(1) if kind == "linux" else hk.aix_script(1)
        marker = "CUT=$((" if kind == "linux" else "set -m"
        head = ("export LC_ALL=C\nPCT=1; OLDD=730; MAXSEC=60; BIGKB=100; TOTAL=600\n" if kind == "linux"
                else "export LC_ALL=C LANG=C\nPCT=1; OLDY=2; MAXSEC=60; BIGKB=100; TOTAL=600\n") +"MOUNTS=$(printf '90\\t%s\\n' \"" + d + "\")\n"
        f = Path(tempfile.mkdtemp()) / "t.sh"
        f.write_text(head + s[s.index(marker):], encoding="utf-8", newline="\n")
        run = subprocess.run([_find_bash(), f.as_posix()], capture_output=True, text=True, encoding="utf-8")
        out = run.stdout
        p = hk.parse_output(out)
        assert p["status"] == "ok", (out, run.stderr)
        top = next(x for x in p["dirs"] if x["path"] == d)
        assert top["kb"] == 3072 + 2048 + 300 + 2048 + 1024         # 全部檔案加總（mid 也算進總量，只是不算舊）
        assert top["old_kb"] == 3072 + 2048 and top["old_files"] == 2
        logs = next(x for x in p["dirs"] if x["path"].endswith("/app/logs"))
        assert logs["kb"] == 5120 and logs["old_kb"] == 3072
        assert {Path(x["path"]).name for x in p["files"]} >= {"a.log", "big.bin"}
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.skipif(not _bash_ok(), reason="需要 bash 與 GNU find")
@pytest.mark.parametrize("kind", ["linux", "aix"])
def test_腳本不受外部語系影響(kind):
    """Q97：父環境是中文語系（find -ls 月份印成「11月」）時，腳本自己 export LC_ALL=C 仍要算對。"""
    s = hk.linux_script(1) if kind == "linux" else hk.aix_script(1)
    assert s.startswith("export LC_ALL=C"), "腳本第一行要自己固定語系"
    d = tempfile.mkdtemp().replace("\\", "/")
    try:
        _mk(d, "x/old.log", 2 * 1024 * 1024, 900)
        marker = "CUT=$((" if kind == "linux" else "set -m"
        head = s[:s.index("PCT=")]          # 保留腳本自己的 export 行
        head += ("PCT=1; OLDD=730; MAXSEC=60; BIGKB=100; TOTAL=600\n" if kind == "linux"
                 else "PCT=1; OLDY=2; MAXSEC=60; BIGKB=100; TOTAL=600\n")
        head += "MOUNTS=$(printf '90\\t%s\\n' \"" + d + "\")\n"
        f = Path(tempfile.mkdtemp()) / "t.sh"
        f.write_text(head + s[s.index(marker):], encoding="utf-8", newline="\n")
        env = dict(os.environ, LC_ALL="zh_TW.UTF-8", LANG="zh_TW.UTF-8")
        run = subprocess.run([_find_bash(), f.as_posix()], capture_output=True, text=True, encoding="utf-8", env=env)
        p = hk.parse_output(run.stdout)
        assert p["status"] == "ok", (run.stdout, run.stderr)
        top = next(x for x in p["dirs"] if x["path"] == d)
        assert top["old_files"] == 1 and top["old_kb"] == 2048
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- API
def _client(tmp, user):
    p = Path(tmp) / "t.db"
    db.init_db(p)

    def _ov():
        c = db.get_connection(p)
        try:
            yield c
        finally:
            c.close()
    api.app.dependency_overrides[api.get_db] = _ov
    cl = TestClient(api.app)
    c = db.get_connection(p)
    db.create_user(c, user, auth.hash_password(_STUB))
    db.insert_hardware(c, asset_serial="L1", hostname="lx1", ip="10.99.8.1", os="Red Hat Enterprise Linux 8", asset_status="使用中")
    c.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = 'L1'")
    c.commit()
    c.close()
    assert cl.post("/api/auth/login", json={"username": user, "password": _STUB}).status_code == 200
    return cl


def test_API權限與時段(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        cl = _client(tmp, "tester")
        assert cl.get("/api/housekeeping/overview").status_code == 200
        assert cl.post("/api/housekeeping/scan", json={"serials": ["L1"]}).status_code == 403     # 非管理員
    with tempfile.TemporaryDirectory() as tmp:
        cl = _client(tmp, "admin")
        monkeypatch.setattr(hk, "_now", lambda: DAY)
        r = cl.post("/api/housekeeping/scan", json={"serials": ["L1"]})
        assert r.status_code == 423 and "15:00" in r.json()["detail"]
        ov = cl.get("/api/housekeeping/overview").json()
        assert ov["window"]["blocked"] is True and ov["hosts"][0]["serial"] == "L1"
        assert cl.get("/api/housekeeping/result/999").status_code == 404


# ---------------------------------------------------------------- 審查後補（2.007.17）
def test_離0830不到15分鐘不開始且預算算對():
    assert hk.seconds_to_block(dt.datetime(2026, 10, 28, 15, 0)) == 17.5 * 3600
    assert hk.seconds_to_block(dt.datetime(2026, 10, 28, 8, 0)) == 30 * 60
    assert hk.seconds_to_block(dt.datetime(2026, 10, 28, 12, 0)) == 0


def test_臨近0830不開始新的一台(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        monkeypatch.setattr(hk, "_now", lambda: dt.datetime(2026, 10, 28, 8, 20))
        called = []
        r = hk.scan_host(c, "L1", runner=lambda h, cmd: called.append(cmd) or LINUX_OUT)
        assert r["status"] == "stopped" and not called
        c.close()


def test_總預算會寫進腳本且Windows時限跟著縮():
    assert "TOTAL=1200" in hk.linux_script(80, 1200) and "TOTAL=1200" in hk.aix_script(80, 1200)
    assert "-MaxMinutes 3" in hk.windows_script(80, 3)


def test_連上後再驗一次ESXi_是就整台不掃(night):
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        sent = []

        def runner(h, cmd):
            sent.append(cmd)
            return "VMkernel\nVMware ESXi 7.0.3" if "vmware -v" in cmd else LINUX_OUT
        r = hk.scan_host(c, "L1", runner=runner)
        assert r["status"] == "failed" and "ESXi" in r["note"]
        assert not any("find" in x for x in sent)                       # 連掃描指令都沒送
        c.close()


def test_只掃本機檔案系統():
    assert "-l" in hk.linux_script().split("df -P")[1].split("|")[0] and "-x nfs" in hk.linux_script()
    a = hk.aix_script()
    assert "lsfs -c" in a and "jfs2" in a and "@@NOLOCAL" in a
    assert hk.parse_output("@@NOLOCAL\n")["status"] == "nolocal"


def test_find只吐錯誤訊息不能報ok():
    out = "@@FS\t/data\t91\n@@JUNK\t/data\t2\n@@STAT\t/data\t0\t0\n@@DONE\n"
    assert hk.parse_output(out)["status"] == "failed"


def test_root金鑰主機不掃(night, monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        monkeypatch.setattr(hk, "_collect_account", lambda conn, serial, platform: "root")
        r = hk.scan_host(c, "L1")
        assert r["status"] == "failed" and "root" in r["note"]
        c.close()


def test_重啟留下的排隊中標成失敗(night):
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        hk.ensure_table(c)
        c.execute("INSERT INTO hk_scan (asset_serial, status) VALUES ('L1','running')")
        c.commit()
        assert hk.reap_stale(c) == 1
        assert c.execute("SELECT status FROM hk_scan").fetchone()[0] == "failed"
        hk._active["running"] = True
        try:
            c.execute("INSERT INTO hk_scan (asset_serial, status) VALUES ('L1','running')")
            assert hk.reap_stale(c) == 0                                 # 真的在跑就不動
        finally:
            hk._active["running"] = False
        c.close()


def test_Windows逾時與錯誤不再報ok(night, monkeypatch):
    import win_onboard
    with tempfile.TemporaryDirectory() as tmp:
        c, _ = _conn(tmp)
        c.execute("UPDATE hardware SET os='Windows Server 2019' WHERE asset_serial='L1'")
        c.commit()
        monkeypatch.setattr(win_onboard, "collect_block", lambda *a, **k: None)
        err = hk.scan_host(c, "L1", win_runner=lambda ip, s: "[ERROR] 執行錯誤：xx")
        assert err["status"] == "failed" and err["summary"]
        part = hk.scan_host(c, "L1", win_runner=lambda ip, s: "完成 D:… 中止原因：timeout")
        assert part["status"] == "partial"
        c.close()


def test_結果頁只給管理員():
    with tempfile.TemporaryDirectory() as tmp:
        cl = _client(tmp, "tester")
        assert cl.get("/api/housekeeping/result/1").status_code == 403


# Q91：這個檔案測的是「誰能做什麼」，不套 conftest 的「測試帳號預設 admin」相容層（見 tests/conftest.py）。
ROLE_AWARE_TESTS = True
