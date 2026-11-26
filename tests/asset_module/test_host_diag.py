"""主機履歷 CPU 分析／檔案系統空間（host_diag）。全部假輸出、假 IP（10.99.x）。"""
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import host_diag as hd  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _st(pid, comm, ut, st, start=1000):
    return f"P {pid} ({comm}) S 1 1 1 0 -1 0 0 0 0 0 {ut} {st} 0 0 20 0 1 0 {start} 0 0"


def _linux(samples, ncpu=4, users="", load="1.0 1.0 1.0 1/100 9", hidepid="", cg=""):
    """samples：[(cpu 列數字, [stat 行…])]；cpu 列：user nice system idle iowait irq softirq steal"""
    out = [f"@@NCPU\n{ncpu}", "@@HZ\n100", f"@@LOAD\n{load}", f"@@HIDEPID\n{hidepid}"]
    for i, (cpu, procs) in enumerate(samples):
        out.append(f"@@S{i}\ncpu  " + " ".join(map(str, cpu)) + "\nU 864000.00 1.0\n" + "\n".join(procs))
    out += [f"@@USERS\n{users}", f"@@CG\n{cg}", "@@END"]
    return "\n".join(out)


def _z():
    return [0] * 8


def test_使用者實機top_kafka三支java各兩核_整機三成_判不忙():
    # 20 核、2 秒窗：整機 tick 4000；三支 java 各用 2.09 核＝各 418 tick；us 25.3 sy 5.4 si 1.7 id 67.6
    cpu1 = [1012, 0, 216, 2704, 0, 0, 68, 0]
    out = _linux([
        (_z(), [_st(102235, "java", 0, 0), _st(101744, "java", 0, 0), _st(102667, "java", 0, 0), _st(77208, "loader", 0, 0)]),
        (cpu1, [_st(102235, "java", 380, 38), _st(101744, "java", 360, 38), _st(102667, "java", 355, 38), _st(77208, "loader", 60, 12)]),
    ], ncpu=20, users="102235 kafka\n101744 kafka\n102667 kafka\n77208 root", load="7.22 7.52 6.64 4/380 9")
    r = hd.analyze_linux_cpu(out)
    assert r["busy_pct"] == 32.4
    assert r["verdict"] == "idle", r["verdict_text"]
    assert "kafka" in r["verdict_text"] and "共 3 個行程" in r["verdict_text"]
    assert r["groups"][0]["name"] == "java" and r["groups"][0]["count"] == 3


def test_Linux_單執行緒卡住一顆核心():
    # 8 核：整機 tick 1600；一支程式 190 tick＝0.95 核，整機才 13%
    out = _linux([(_z(), [_st(5, "batchjob", 0, 0)]), ([200, 0, 10, 1390, 0, 0, 0, 0], [_st(5, "batchjob", 190, 0)])], ncpu=8)
    assert hd.analyze_linux_cpu(out)["verdict"] == "single_core"


def test_Linux_單一程式占大部分_以整機為分母():
    out = _linux([(_z(), [_st(7, "batch job", 0, 0), _st(8, "sshd", 0, 0)]),
                  ([380, 0, 0, 20, 0, 0, 0, 0], [_st(7, "batch job", 350, 0), _st(8, "sshd", 5, 0)])], ncpu=1)
    r = hd.analyze_linux_cpu(out)
    assert r["procs"][0]["name"] == "batch job" and r["verdict"] == "single"


def test_Linux_同服務多行程合計():
    procs0 = [_st(i, "httpd", 0, 0) for i in range(10, 20)]
    procs1 = [_st(i, "httpd", 30, 0) for i in range(10, 20)]
    out = _linux([(_z(), procs0), ([330, 0, 10, 60, 0, 0, 0, 0], procs1)], ncpu=1,
                 users="\n".join(f"{i} apache" for i in range(10, 20)))
    r = hd.analyze_linux_cpu(out)
    assert r["verdict"] == "service" and "共 10 個行程" in r["verdict_text"]


def test_Linux_短命程式_未能歸屬():
    out = _linux([(_z(), [_st(1, "sshd", 0, 0)]), ([350, 0, 20, 30, 0, 0, 0, 0], [_st(1, "sshd", 2, 0), _st(99, "sh", 300, 0)])], ncpu=1)
    r = hd.analyze_linux_cpu(out)
    assert r["verdict"] == "unattributed" and "短命" in r["verdict_text"]


def test_Linux_hidepid看不到():
    out = _linux([(_z(), []), ([350, 0, 20, 30, 0, 0, 0, 0], [])], ncpu=1, hidepid="proc /proc proc rw,hidepid=2 0 0")
    assert "hidepid" in hd.analyze_linux_cpu(out)["verdict_text"]


def test_Linux_steal優先():
    out = _linux([(_z(), []), ([200, 0, 0, 100, 0, 0, 0, 100], [])], ncpu=1)
    assert hd.analyze_linux_cpu(out)["verdict"] == "steal"


def test_Linux_等磁碟與運算雙高():
    out = _linux([(_z(), [_st(3, "db", 0, 0)]), ([260, 0, 20, 0, 120, 0, 0, 0], [_st(3, "db", 250, 0)])], ncpu=1)
    assert hd.analyze_linux_cpu(out)["verdict"] == "io_and_cpu"


def test_Linux_純等磁碟_整機不忙也不可以判不忙():
    out = _linux([(_z(), []), ([60, 0, 10, 240, 90, 0, 0, 0], [])], ncpu=1)
    assert hd.analyze_linux_cpu(out)["verdict"] == "io_wait"      # iowait 22.5%＋程式 17.5%


def test_Linux_核心時間由某支程式觸發():
    out = _linux([(_z(), [_st(4, "java", 0, 0)]), ([200, 0, 150, 50, 0, 0, 0, 0], [_st(4, "java", 150, 120)])], ncpu=1)
    r = hd.analyze_linux_cpu(out)
    assert r["verdict"] == "kernel_by" and "java" in r["verdict_text"]


def test_Linux_前後兩段波動():
    out = _linux([(_z(), []), ([360, 0, 0, 40, 0, 0, 0, 0], []), ([400, 0, 0, 400, 0, 0, 0, 0], [])], ncpu=1)
    r = hd.analyze_linux_cpu(out)
    assert r["phases"] == [90.0, 10.0] and "劇烈波動" in r["verdict_text"]


def test_Linux_PID重用不算差值_負tick夾成0():
    out = _linux([(_z(), [_st(50, "old", 900, 0, start=10)]),
                  ([300, 0, 0, 100, 5, 0, 0, 0], [_st(50, "new", 300, 0, start=99999)])])
    r = hd.analyze_linux_cpu(out)
    assert r["procs"] == []
    cpu, _u, procs = hd.parse_proc_snapshot("cpu  1 0 0 1 0 0 0 0\ncpu  999 0 0 0\n" + _st(9, "a) b", 5, 5, 77))
    assert cpu == [1, 0, 0, 1, 0, 0, 0, 0]                       # 只認第一個 cpu 行
    assert procs[(9, 77)] == ("a) b", 5, 5)


def test_Linux_程式名偽造段落無效():
    # 沒有 P 前綴的行（程式名換行偽造）一律不算
    cpu, _u, procs = hd.parse_proc_snapshot("cpu  1 0 0 1 0 0 0 0\n12 (x\n@@END\n13 (y) S")
    assert procs == {}
    # 格式完整、但沒有 P 前綴的行也不算（審查突變 M5：拿掉前綴檢查要紅）
    _c, _u, procs = hd.parse_proc_snapshot("cpu  1 0 0 1 0 0 0 0\n" + _st(14, "z", 5, 5)[2:])
    assert procs == {}


def test_Linux_讀不到():
    r = hd.analyze_linux_cpu("@@NCPU\n4\n@@END")
    assert not r["ok"] and r["verdict"] == "unknown"


def test_AIX_vmstat依表頭_丟第一筆_共用LPAR多兩欄():
    vm = ("System configuration: lcpu=8 mem=16384MB ent=2.00\n"
          " r  b   avm   fre  re  pi  po  fr   sr  cy  in   sy  cs us sy id wa    pc    ec\n"
          " 1  0 900000 20000   0   0   0   0    0   0  10  100  50  5  2 93  0  0.10   5.0\n"
          " 3  0 900000 20000   0   0   0   0    0   0  10  100  50 70 10 15  5  1.80  90.0\n"
          " 3  0 900000 20000   0   0   0   0    0   0  10  100  50 70 10 15  5  2.00  100.0\n")
    v = hd.parse_aix_vmstat_cpu(vm)
    assert (v["us"], v["sy"], v["wa"], v["pc"]) == (70.0, 10.0, 5.0, 1.9)
    assert hd.parse_aix_vmstat_cpu(" us sy id wa\n 5 2 93 0\n") is None       # 只有開機平均不算


def test_AIX_ps時間差_共用LPAR以pc為分母():
    out = ("@@NCPU\nThe available processors are:  0 1 2 3 4 5 6 7\n@@LPAR\nType : Shared-SMT\n"
           "@@T0\n10:00:00\n@@P0\n  4000 oracle 01:00:00 3-00:00:00 oracle\n  12 root 00:00:01 10:00 sshd\n"
           "@@VMSTAT\n us sy id wa pc ec\n 1 1 98 0 0.1 5\n 60 20 15 5 2.0 100\n 60 20 15 5 2.0 100\n"
           "@@T1\n10:00:02\n@@P1\n  4000 oracle 01:00:03 3-00:00:02 oracle\n  12 root 00:00:01 10:02 sshd\n@@END")
    r = hd.analyze_aix_cpu(out)
    assert r["ok"] and r["ncpu"] == 8 and r["window_sec"] == 2.0 and r["pc"] == 2.0
    top = r["procs"][0]
    assert top["name"] == "oracle" and top["share_pct"] == 75.0          # 1.5 核 ÷ pc 2.0
    assert r["verdict"] == "single"
    assert "粗估" in r["precision"] and "pc" in r["note"]


def test_AIX_PID重用不算():
    out = ("@@NCPU\nThe available processors are: 0\n@@T0\n10:00:00\n@@P0\n 5 u 00:00:10 05:00 job\n"
           "@@VMSTAT\n us sy id wa\n 1 1 98 0\n 50 10 40 0\n@@T1\n10:00:02\n@@P1\n 5 u 00:00:11 00:01 job\n@@END")
    assert hd.analyze_aix_cpu(out)["procs"] == []


def test_AIX_時間格式():
    assert hd._ps_time("1-02:03:04") == 93784
    assert hd._ps_time("03:04") == 184
    assert hd._ps_time("x") is None


def test_Windows_計數器差值_服務名_核數取大():
    def snap(idle, priv, ts, p):
        return ("C|0,0|%d|%d|%d\nC|0,1|%d|%d|%d\n" % (idle, priv, ts, idle, priv, ts)) + p
    out = ("@@NCPU\n\n@@SVC\nV|4321|W3SVC\nV|0|Stopped\nA|4321_DefaultAppPool\nA|_Total\n"
           "@@S0\n" + snap(0, 0, 0, "P|4321|100|0|0|0|1000|10|w3wp\nP|8|0|0|0|0|1000|10|System\n")
           + "@@S1\n" + snap(2_000_000, 1_000_000, 10_000_000,
                             "P|4321|100|15000000|1000000|10000000|2000|10|w3wp\nP|8|0|100000|0|10000000|2000|10|System\n")
           + "@@END")
    r = hd.analyze_win_cpu(out)
    assert r["ok"] and r["ncpu"] == 2 and r["busy_pct"] == 80.0 and r["iowait_pct"] is None and r["window_sec"] == 1.0
    top = r["procs"][0]
    assert (top["pid"], top["name"], top["pct"], top["unit"]) == (4321, "w3wp", 75.0, "W3SVC、AppPool DefaultAppPool")
    assert next(p for p in r["procs"] if p["name"] == "System")["age_days"] is None   # ElapsedTime=0 不算天數
    assert r["verdict"] == "single"


def test_清理控制字元與公式字元():
    assert hd.clean("=cmd|x") == "'=cmd|x"
    assert hd.clean("a\x1b[31mb\u202e") == "a [31mb"


def test_指令沒有指令列參數_沒有編碼_沒有sudo():
    for c in (hd.LINUX_CPU_CMD, hd.AIX_CPU_CMD, hd.LINUX_FS_CMD, hd.AIX_FS_CMD):
        assert "cmdline" not in c and "sudo" not in c and "base64" not in c and "LC_ALL=C" in c
        assert "cat /proc/[0-9]*" not in c                        # 不展開成一長串參數
    assert " -o pid= -o user=" in hd.AIX_CPU_CMD                    # AIX 每欄各自 -o
    assert "df -P -k -l" in hd.LINUX_FS_CMD and "timeout" in hd.LINUX_FS_CMD
    for c in (hd.WIN_CPU_PS, hd.WIN_FS_PS):
        assert "CommandLine" not in c and "EncodedCommand" not in c and "Bypass" not in c and "SilentlyContinue" in c


def _df(inode_root="10%"):
    return ("@@DF\nFilesystem 1024-blocks Used Available Capacity Mounted on\n"
            "/dev/sda1 10485760 9646899 838861 92% /data\n"
            "/dev/sda2 10485760 8808038 1677722 84% /u01\n"
            "/dev/sda3 10485760 1048576 9437184 10% /\n"
            "/dev/sda4 10485760 1048576 9437184 10% /ro\n"
            "@@DFI\nFilesystem Inodes IUsed IFree IUse% Mounted on\n"
            f"/dev/sda1 100 10 90 10% /data\n/dev/sda2 100 10 90 10% /u01\n/dev/sda3 100 95 5 {inode_root} /\n"
            "/dev/sda4 100 1 99 1% /ro\n"
            "@@MOUNTS\n/dev/sda4 /ro xfs ro,relatime 0 0\n@@END")


def test_檔案系統空間_用1點5同一套等級詞(tmp_path):
    conn = _conn(tmp_path)
    try:
        disks, ro = hd.parse_fs("linux", _df())
        r = hd.judge_fs(conn, "10.99.1.10", disks, ro)
        lv = {m["mount"]: m["level"] for m in r["mounts"]}
        # 1.5：≥90 第一級、≥80 需處理（Antigravity D10：原本寫反）
        assert lv["/data"] == "第一級" and lv["/u01"] == "需處理" and lv["/ro"] == "唯讀重掛"
        assert r["mounts"][0]["mount"] == "/ro" and r["verdict"] == "readonly"
    finally:
        conn.close()


def test_檔案系統空間_inode滿不可以判正常(tmp_path):
    conn = _conn(tmp_path)
    try:
        df = ("@@DF\nFilesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda3 10485760 1048576 9437184 10% /\n"
              "@@DFI\nFilesystem Inodes IUsed IFree IUse% Mounted on\n/dev/sda3 100 99 1 99% /\n@@MOUNTS\n@@END")
        disks, ro = hd.parse_fs("linux", df)
        r = hd.judge_fs(conn, "10.99.1.10", disks, ro)
        assert r["mounts"][0]["level"] == "第一級" and r["verdict"] == "first" and "inode" in r["verdict_text"]
        assert "小檔" in r["advice"]
    finally:
        conn.close()


def test_檔案系統空間_全被排除不可以判正常(tmp_path):
    conn = _conn(tmp_path)
    try:
        r = hd.judge_fs(conn, "10.99.1.10", [{"mount": "swap", "use_pct": 50, "size_kb": 1, "avail_kb": 1}], [])
        assert not r["ok"]
    finally:
        conn.close()


def test_Windows_磁碟區含掛載資料夾_一位小數():
    v = hd._parse_win_vol("C:\\|100|10.4\nD:\\SQLData\\Log\\|1000|104\n")
    assert [x["mount"] for x in v] == ["C:\\", "D:\\SQLData\\Log\\"] and v[0]["use_pct"] == 89.6


def _clock(*ts):
    it = iter(ts)
    last = [None]

    def f():
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]
    return f


def test_執行_存紀錄_冷卻只算成功_複製文字(tmp_path):
    conn = _conn(tmp_path)
    try:
        t = dt.datetime(2026, 11, 26, 10, 0, 0)
        res = hd.run(conn, "10.99.1.10", "fs", "linux", "tester", runner=lambda p, k: _df(),
                     clock=_clock(t, t + dt.timedelta(seconds=2)))
        assert res["status"] == "ok" and res["finished_at"] == "2026-11-26 10:00:02"
        assert "【檔案系統空間】" in res["copy_text"] and "系統與負責：" in res["copy_text"]
        assert "tester" not in res["copy_text"] and "此刻快照" in res["copy_text"]
        with pytest.raises(hd.Busy) as e:
            hd.run(conn, "10.99.1.10", "fs", "linux", "tester", runner=lambda p, k: _df(),
                   clock=_clock(t + dt.timedelta(seconds=20)))
        assert e.value.latest_id == res["id"]
        ok2 = hd.run(conn, "10.99.1.10", "fs", "linux", "tester", runner=lambda p, k: _df(),
                     clock=_clock(t + dt.timedelta(seconds=40)))
        assert ok2["status"] == "ok"
        g = hd.get(conn, "10.99.1.10", res["id"])
        assert g["historical"] and "歷史快照" in g["copy_text"]
        assert hd.get(conn, "10.99.9.9", res["id"]) is None
    finally:
        conn.close()


def test_執行_連不上不冷卻_我們的問題不給複製(tmp_path):
    conn = _conn(tmp_path)
    try:
        def boom(p, k):
            raise RuntimeError("連線逾時")

        def ours(p, k):
            raise hd.NotProbed("沒有 WinRM 憑證")
        t = dt.datetime(2026, 11, 26, 10, 0, 0)
        r1 = hd.run(conn, "10.99.1.11", "cpu", "linux", "u", runner=boom, clock=_clock(t))
        assert r1["status"] == "failed" and "連線逾時" in r1["verdict_text"]
        r2 = hd.run(conn, "10.99.1.11", "cpu", "linux", "u", runner=ours, clock=_clock(t + dt.timedelta(seconds=1)))
        assert r2["status"] == "not_probed" and r2["copy_text"] is None
        r3 = hd.run(conn, "10.99.1.11", "cpu", "linux", "u", runner=lambda p, k: "垃圾", clock=_clock(t + dt.timedelta(seconds=2)))
        assert r3["status"] == "partial"
    finally:
        conn.close()


def test_執行_中斷的執行中紀錄會回收_全站上限(tmp_path):
    conn = _conn(tmp_path)
    try:
        hd.ensure(conn)
        t = dt.datetime(2026, 11, 26, 10, 0, 0)
        for i in range(hd.MAX_CONCURRENT):
            conn.execute("INSERT INTO host_diag_run (ip, kind, started_at, status) VALUES (?,?,?,?)",
                         (f"10.99.2.{i}", "cpu", "2026-11-26 09:59:59", "running"))
        conn.commit()
        with pytest.raises(hd.Busy):
            hd.run(conn, "10.99.1.12", "cpu", "linux", "u", runner=lambda p, k: "", clock=_clock(t))
        later = t + dt.timedelta(seconds=hd.RUNNING_STALE_SEC + 5)
        r = hd.run(conn, "10.99.1.12", "cpu", "linux", "u", runner=lambda p, k: "", clock=_clock(later))
        assert r["status"] == "partial"
        assert conn.execute("SELECT COUNT(*) FROM host_diag_run WHERE status='interrupted'").fetchone()[0] == hd.MAX_CONCURRENT
    finally:
        conn.close()


def test_執行_每人每分鐘上限(tmp_path):
    conn = _conn(tmp_path)
    try:
        t = dt.datetime(2026, 11, 26, 10, 0, 0)
        for i in range(hd.USER_PER_MIN):
            hd.run(conn, f"10.99.3.{i}", "cpu", "linux", "u", runner=lambda p, k: "", clock=_clock(t))
        with pytest.raises(hd.Busy):
            hd.run(conn, "10.99.3.99", "cpu", "linux", "u", runner=lambda p, k: "", clock=_clock(t))
    finally:
        conn.close()


def test_讀取不建表():
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    assert hd.history(c, "10.99.1.1") == [] and hd.get(c, "10.99.1.1", 1) is None
    assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='host_diag_run'").fetchone()[0] == 0


def test_API_只打已登記且現役主機_接線(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from routers import system as rs
    import health_probe

    conn = _conn(tmp_path)
    try:
        with pytest.raises(HTTPException) as e:
            rs.host_diag_run(rs.HostDiagBody(ip="10.99.7.7", kind="cpu"), None, conn)
        assert e.value.status_code == 404
        with pytest.raises(HTTPException) as e:
            rs.host_diag_run(rs.HostDiagBody(ip="10.99.7.7; rm -rf /", kind="cpu"), None, conn)
        assert e.value.status_code == 400
        db.insert_hardware(conn, asset_serial="S0", hostname="old01", ip="10.99.7.9", os="Red Hat Enterprise Linux 8",
                           asset_status="報廢")
        db.insert_hardware(conn, asset_serial="S1", hostname="lnx01", ip="10.99.7.8", os="Red Hat Enterprise Linux 8")
        conn.commit()
        with pytest.raises(HTTPException) as e:
            rs.host_diag_run(rs.HostDiagBody(ip="10.99.7.9", kind="fs"), None, conn)
        assert e.value.status_code == 409 and "停用／報廢" in e.value.detail      # 審查 M6：要是被「退役」擋下，不是碰巧別的 409
        # 天條：VMware 設備（ESXi）一律擋下，不連線（審查 M1／突變 M8）
        db.insert_hardware(conn, asset_serial="E1", hostname="esxi99", ip="10.99.7.10", os="VMware ESXi 7.0")
        conn.commit()
        with pytest.raises(HTTPException) as e:
            rs.host_diag_run(rs.HostDiagBody(ip="10.99.7.10", kind="cpu"), None, conn)
        assert e.value.status_code == 409 and "VMware" in str(e.value.detail)
        sent = []

        def fake_ssh(ip, cmd, key, account, timeout=14):
            sent.append(cmd)
            return True, _df(), ""
        monkeypatch.setattr(health_probe, "_run_ssh", fake_ssh)
        monkeypatch.setattr(rs, "_host_diag_key_ok", lambda: True)
        r = rs.host_diag_run(rs.HostDiagBody(ip="10.99.7.8", kind="fs"), None, conn)
        assert r["status"] == "ok" and r["mounts"]
        assert sent and "df -P -k -l" in sent[0] and "10.99.7.8" not in sent[0]   # IP 不進指令
        import onboard_engine
        assert sent[0].startswith(onboard_engine.vmware_marker_guard_sh())        # 縱深：連上後先自我檢查是不是 VMware（突變 M10）
        assert rs.host_diag_history("10.99.7.8", None, conn)["items"][0]["id"] == r["id"]
        assert rs.host_diag_get(r["id"], "10.99.7.8", None, conn)["historical"]
    finally:
        conn.close()
