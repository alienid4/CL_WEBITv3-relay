"""NMON 完整指標（nmon_series，2.007.19）：解壓（.gz／.Z）、解析（AIX／Linux 格式）、儲存、顆粒度、尖峰時間、門檻時段、TOP、原始檔下載。

AIX 的格式用真檔驗證過（本機有 DATA 底下的真檔才跑 test_真檔；CI 沒有就跳過）；
Linux 是照 NMON 公開格式造的假檔，**尚未用 Linux 真檔驗證**——所以測試只能證明「照格式寫的」解得出來。
"""
import glob
import gzip
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import api  # noqa: E402
import db  # noqa: E402
import host_monthly  # noqa: E402
import nmon_series as ns  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_STUB = "test-password-123"


# ---------------------------------------------------------------- 造檔
def make_aix(host="hostA", day="08-SEP-2026", n=120, start_min=60, cpu=lambda i: 2.0, extra_top=True):
    L = [f"AAA,host,{host}", "AAA,AIX,7.2.5.209", "AAA,interval,60", f"AAA,snapshots,{n}"]
    hdr = [
        "CPU_ALL,CPU Total %s,User%%,Sys%%,Wait%%,Idle%%,Busy,PhysicalCPUs " % host,
        "MEM,Memory %s,Real Free %%,Virtual free %%,Real free(MB),Virtual free(MB),Real total(MB),Virtual total(MB)" % host,
        "PAGE,Paging %s,faults,pgin,pgout,pgsin,pgsout,reclaims,scans,cycles" % host,
        "PROC,Processes %s,Runnable,Swap-in,pswitch,syscall" % host,
        "NET,Network I/O %s,en0-read-KB/s,lo0-read-KB/s,en0-write-KB/s,lo0-write-KB/s" % host,
        "NETERROR,Network Errors %s,en0-ierrs,lo0-ierrs,en0-oerrs,lo0-oerrs,en0-collisions,lo0-collisions" % host,
        "DISKBUSY,Disk %%Busy %s,hdisk0,hdisk1" % host,
        "DISKREAD,Disk Read KB/s %s,hdisk0,hdisk1" % host,
        "DISKWRITE,Disk Write KB/s %s,hdisk0,hdisk1" % host,
        "JFSFILE,JFS Filespace %%Used %s,/,/var" % host,
    ]
    L += hdr
    L += ["TOP,%CPU Utilisation", "TOP,+PID,Time,%CPU,%Usr,%Sys,Threads,Size,ResText,ResData,CharIO,%RAM,Paging,Command,WLMclass"]
    for i in range(n):
        t = f"T{i + 1:04d}"
        mins = start_min + i
        L.append(f"ZZZZ,{t},{mins // 60 % 24:02d}:{mins % 60:02d}:08,{day}")
        c = cpu(i)
        L.append(f"CPU_ALL,{t},{c / 2},{c / 2},0.5,{100 - c - 0.5},0,4")
        L.append(f"MEM,{t},80.0,99.0,1000,2000,5000,3000")
        L.append(f"PAGE,{t},3.0,1.0,2.0,0.0,0.0,0,0,0")
        L.append(f"PROC,{t},0.5,0,100,10")
        L.append(f"NET,{t},1.5,9.9,2.5,9.9")
        L.append(f"NETERROR,{t},0.0,0.0,1.0,0.0,0,0")
        L.append(f"DISKBUSY,{t},{c},0.2")
        L.append(f"DISKREAD,{t},5.0,0.0")
        L.append(f"DISKWRITE,{t},7.0,1.0")
        L.append(f"JFSFILE,{t},{85.0 if i % 2 else 70.0},95.5")
        if extra_top and i % 10 == 0:
            L.append(f"TOP,111,{t},{c},1.0,1.0,2,100,10,90,0,1,0,myproc,Unclassified")
            L.append(f"TOP,222,{t},0.4,0.2,0.2,2,100,10,90,0,1,0,sshd,Unclassified")
    return "\n".join(L) + "\n"


def make_linux(host="lnx1", day="08-SEP-2026", n=30):
    L = [f"AAA,host,{host}", "AAA,interval,300",
         f"CPU_ALL,CPU Total {host},User%,Sys%,Wait%,Idle%,Busy,CPUs",
         f"MEM,Memory MB {host},memtotal,hightotal,lowtotal,swaptotal,memfree,highfree,lowfree,swapfree,memshared,cached,active,bigfree,buffers,swapcached,inactive",
         f"VM,Paging and Virtual Memory,nr_dirty,pswpin,pswpout,pgpgin,pgpgout",
         f"NET,Network I/O {host},eth0-read-KB/s,lo-read-KB/s,eth0-write-KB/s,lo-write-KB/s",
         f"DISKBUSY,Disk %Busy {host},sda,sdb", f"JFSFILE,JFS Filespace %Used {host},/,/data"]
    for i in range(n):
        t = f"T{i + 1:04d}"
        mins = 600 + 5 * i
        L.append(f"ZZZZ,{t},{mins // 60:02d}:{mins % 60:02d}:00,{day}")
        L.append(f"CPU_ALL,{t},10.0,5.0,1.0,84.0,0,8")
        L.append(f"MEM,{t},1000,0,0,500,200,0,0,400,0,300,0,0,100,0,0")
        L.append(f"VM,{t},0,3,4,5,6")
        L.append(f"NET,{t},1.0,5.0,2.0,5.0")
        L.append(f"DISKBUSY,{t},10.0,20.0")
        L.append(f"JFSFILE,{t},50.0,91.0")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- LZW（測試用編碼器：照 compress 的寫法，含碼寬變化與 CLEAR）
def lzw_compress(data: bytes, maxbits=16, clear_at: int | None = None) -> bytes:
    out = bytearray([0x1F, 0x9D, 0x80 | maxbits])
    st = {"n": 9, "free": 257, "group": []}
    table = {bytes([i]): i for i in range(256)}

    def flush(final=False):
        g = st["group"]
        if not g:
            return
        acc, nb = 0, 0
        for c in g:
            acc |= c << nb
            nb += st["n"]
        out.extend(acc.to_bytes((nb + 7) // 8, "little"))
        if not final:       # 換碼寬／CLEAR 時一組補滿 n 位元組（8 個碼）；檔尾最後一組只寫到有資料的位元組
            out.extend(bytes(st["n"] - (nb + 7) // 8))
        st["group"] = []

    def emit(code: int):
        st["group"].append(code)
        if len(st["group"]) == 8:
            flush()
        if st["free"] > (1 << st["n"]) - 1 and st["n"] < maxbits:
            flush()
            st["n"] += 1

    w = b""
    for idx, byte in enumerate(data):
        c = bytes([byte])
        wc = w + c
        if wc in table:
            w = wc
            continue
        emit(table[w])
        table[wc] = st["free"]
        st["free"] += 1
        w = c
        if clear_at is not None and idx == clear_at:
            emit(table[w])          # 先把目前的字送出，再送 CLEAR
            emit(256)
            flush()
            st["n"], st["free"] = 9, 257
            table = {bytes([i]): i for i in range(256)}
            w = b""
    if w:
        emit(table[w])
    flush(final=True)
    return bytes(out)


def test_LZW編碼解碼來回一致_含碼寬變化與CLEAR():
    import random
    rnd = random.Random(7)
    alphabet = b"abcdefghij0123456789,.\n"
    data = bytes(rnd.choice(alphabet) for _ in range(40000))
    assert ns.lzw_decompress(lzw_compress(data)) == data
    assert ns.lzw_decompress(lzw_compress(data, clear_at=15000)) == data
    assert ns.to_text(lzw_compress(b"AAA,host,x\n")) == "AAA,host,x\n"


def test_壞的Z檔丟ValueError():
    with pytest.raises(ValueError):
        ns.lzw_decompress(b"not a z file")
    with pytest.raises(ValueError):
        ns.lzw_decompress(bytes([0x1F, 0x9D, 0x90, 0xFF, 0xFF, 0xFF, 0xFF]))


def test_真檔解壓與解析(tmp_path):
    files = glob.glob(str(ROOT / "DATA" / "*.nmon.Z"))
    if not files:
        pytest.skip("本機沒有真的 AIX NMON 檔（DATA 底下，不進版控）")
    raw = open(files[0], "rb").read()
    text = ns.to_text(raw)
    p = ns.parse(text)
    assert p["interval_s"] == 60 and sum(len(d["rows"]) for d in p["days"].values()) >= 1400
    assert {"CPU_ALL", "MEM", "PAGE", "NET", "NETERROR", "DISKBUSY", "JFSFILE", "PROC"} <= set(p["found"])
    one = next(iter(next(iter(p["days"].values()))["rows"].values()))
    assert {"cpu_busy", "mem_used", "swap_used", "runq", "pgsout"} <= set(one)
    assert not any(k.startswith("nic:lo") for k in one)


# ---------------------------------------------------------------- 解析
def test_AIX解析欄位對得上():
    p = ns.parse(make_aix(n=20, cpu=lambda i: 40.0))
    assert p["interval_s"] == 60 and list(p["days"]) == ["2026-09-08"]
    r = next(iter(p["days"]["2026-09-08"]["rows"].values()))
    assert r["cpu_busy"] == 40.0 and r["cpu_user"] == 20.0 and r["cpu_wait"] == 0.5 and r["runq"] == 0.5
    assert r["mem_used"] == 20.0 and r["swap_used"] == 1.0                      # 100 - Real Free% / 100 - Virtual free%
    assert r["pgsin"] == 0.0 and r["pgin"] == 1.0 and r["pgout"] == 2.0
    assert r["nic:en0:rx"] == 1.5 and r["nic:en0:tx"] == 2.5 and r["nic:en0:err"] == 1.0
    assert not any(k.startswith("nic:lo") for k in r)                              # 迴圈介面不算
    assert r["disk:hdisk0:busy"] == 40.0 and r["disk:hdisk1:rd"] == 0.0 and r["fs:/"] in (70.0, 85.0)
    assert {"TOP", "NET", "MEM"} <= set(p["found"])
    top = p["days"]["2026-09-08"]["top"]
    assert next(iter(top.values()))[0][2] == "myproc"                               # 依 %CPU 排序


def test_Linux解析欄位對得上_未用真檔驗證():
    r = next(iter(next(iter(ns.parse(make_linux())["days"].values()))["rows"].values()))
    assert r["cpu_busy"] == 15.0 and r["mem_used"] == 40.0                          # (1000-200-300-100)/1000
    assert r["swap_used"] == 20.0 and r["pgsin"] == 3.0 and r["pgsout"] == 4.0 and r["pgin"] == 5.0
    assert r["nic:eth0:rx"] == 1.0 and r["fs:/data"] == 91.0 and r["disk:sdb:busy"] == 20.0


def test_跨日檔案依日期分開():
    p = ns.parse(make_aix(n=120, start_min=23 * 60 + 30))      # 23:30 起跑 120 分鐘，跨午夜（但 ZZZZ 日期欄寫同一天）
    assert sum(len(d["rows"]) for d in p["days"].values()) == 120
    with pytest.raises(ValueError):
        ns.parse("AAA,host,x\nCPU_ALL,T0001,1,2\n")                                  # 沒有 ZZZZ
    with pytest.raises(ValueError):
        ns.parse("hello")                                                            # 不是 NMON


def test_磁碟太多只留最忙的():
    names = ",".join(f"hdisk{i}" for i in range(30))
    vals = lambda i: ",".join(str(i % 7 if j != 29 else 99.0) for j in range(30))   # noqa: E731
    L = ["AAA,host,h", f"DISKBUSY,x,{names}", "CPU_ALL,x,User%,Sys%"]
    for i in range(5):
        L += [f"ZZZZ,T{i + 1:04d},10:0{i}:00,08-SEP-2026", f"CPU_ALL,T{i + 1:04d},1,1", f"DISKBUSY,T{i + 1:04d},{vals(i)}"]
    p = ns.parse("\n".join(L))
    r = next(iter(p["days"]["2026-09-08"]["rows"].values()))
    assert len([k for k in r if k.startswith("disk:")]) == ns.MAX_DISKS and "disk:hdisk29:busy" in r
    assert p["notes"]


# ---------------------------------------------------------------- 儲存與查詢
def _db(tmp):
    db.init_db(Path(tmp) / "t.db")
    return db.get_connection(Path(tmp) / "t.db")


def test_顆粒度與尖峰時間():
    with tempfile.TemporaryDirectory() as tmp:
        c = _db(tmp)
        spike = lambda i: 85.0 if 60 <= i < 70 else 2.0          # 02:00～02:09 連續 10 分鐘超標      # noqa: E731
        ns.store(c, ns.parse(make_aix(n=180, start_min=60, cpu=spike)))
        day = ns.series(c, "hostA", "2026-09", "day")
        assert len(day["series"]["cpu_busy"]) == 1 and day["series"]["cpu_busy"][0][2] == 85.0
        hr = ns.series(c, "hostA", "2026-09", "hour")
        assert len(hr["series"]["cpu_busy"]) == 3                 # 01、02、03 點
        pt = next(p for p in hr["series"]["cpu_busy"] if p[0].endswith(" 02"))
        assert pt[2] == 85.0 and pt[3].startswith("2026-09-08 02:0")
        raw = ns.series(c, "hostA", "2026-09", "raw", "2026-09-08")
        assert len(raw["series"]["cpu_busy"]) == 180
        with pytest.raises(ValueError):
            ns.series(c, "hostA", "2026-09", "raw")              # raw 一定要指定哪一天
        with pytest.raises(ValueError):
            ns.series(c, "hostA", "2026-09", "minute")
        assert ns.series(c, "nope", "2026-09", "hour")["series"] == {}
        s = ns.summary(c, "hostA", "2026-09")
        cpu = next(i for i in s["items"] if i["key"] == "cpu_busy")
        assert cpu["peak"] == 85.0 and cpu["over"] == 10 and cpu["threshold"] == 80
        run = cpu["over_runs"][0]
        assert run["minutes"] == 10 and run["peak_at"].startswith("2026-09-08 02:")
        fs = next(i for i in s["items"] if i["key"] == "fs:/")
        assert fs["over"] == 90 and fs["over_runs"]                  # 奇數分鐘 85%、偶數 70%：>80% 的有 90 個取樣
        assert next(i for i in s["items"] if i["key"] == "fs:/var")["over_crit"] == 180
        c.close()


def test_沒收集的指標不顯示成0():
    with tempfile.TemporaryDirectory() as tmp:
        c = _db(tmp)
        ns.store(c, ns.parse(make_linux()))                         # Linux 假檔沒有 PROC／NETERROR
        keys = {i["key"] for i in ns.summary(c, "lnx1", "2026-09")["items"]}
        assert "runq" not in keys and "nic:eth0:err" not in keys
        assert "runq" not in ns.series(c, "lnx1", "2026-09", "hour")["series"]
        assert "cpu" in ns.summary(c, "lnx1", "2026-09")["collected_groups"]
        c.close()


def test_同台同天再匯入依時間點覆蓋():
    with tempfile.TemporaryDirectory() as tmp:
        c = _db(tmp)
        ns.store(c, ns.parse(make_aix(n=60, cpu=lambda i: 10.0)))
        ns.store(c, ns.parse(make_aix(n=90, cpu=lambda i: 20.0)))    # 前 60 分鐘覆蓋、後 30 分鐘新增
        raw = ns.series(c, "hostA", "2026-09", "raw", "2026-09-08")["series"]["cpu_busy"]
        assert len(raw) == 90 and {p[1] for p in raw} == {20.0}
        assert c.execute("SELECT COUNT(*) FROM nmon_series").fetchone()[0] == 1
        h = ns.hosts(c, "2026-09")[0]
        assert h["days"] == 1 and h["samples"] == 90 and h["interval_s"] == 60
        c.close()


def test_TOP取最接近的快照():
    with tempfile.TemporaryDirectory() as tmp:
        c = _db(tmp)
        ns.store(c, ns.parse(make_aix(n=120, start_min=60)))
        t = ns.top_at(c, "hostA", "2026-09-08 01:10:08")
        assert t["at"] == "2026-09-08 01:10:08" and t["exact"] and t["procs"][0]["cmd"] == "myproc"
        t = ns.top_at(c, "hostA", "2026-09-08 01:15:00")
        assert t["at"] and not t["exact"]
        assert ns.top_at(c, "hostA", "2026-09-09 01:00:00")["procs"] == []        # 那天沒資料
        ns.store(c, ns.parse(make_linux(host="lnx1")))
        assert ns.top_at(c, "lnx1", "2026-09-08 10:00:00")["procs"] == []         # 沒收 TOP＝空，不編
        c.close()


# ---------------------------------------------------------------- API
def _client(tmp, user="admin"):
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
    c.close()
    assert cl.post("/api/auth/login", json={"username": user, "password": _STUB}).status_code == 200
    return cl


def test_上傳gz與Z檔後可查圖表與下載原始檔():
    text = make_aix(n=120, cpu=lambda i: 30.0).encode()
    with tempfile.TemporaryDirectory() as tmp:
        cl = _client(tmp)
        r = cl.post("/api/v2/host-monthly/nmon", files={"file": ("a.nmon.gz", gzip.compress(text))})
        assert r.status_code == 200, r.text
        assert r.json()["metrics"] and r.json()["interval_s"] == 60
        r = cl.post("/api/v2/host-monthly/nmon", files={"file": ("b.nmon.Z", lzw_compress(text))})
        assert r.status_code == 200 and r.json()["samples"] == 120
        assert cl.get("/api/v2/nmon/hosts", params={"month": "2026-09"}).json()["hosts"][0]["host"] == "hostA"
        s = cl.get("/api/v2/nmon/series", params={"host": "hostA", "month": "2026-09", "grain": "hour"}).json()
        assert s["series"]["cpu_busy"] and s["meta"]["cpu_busy"]["threshold"] == 80
        assert cl.get("/api/v2/nmon/series", params={"host": "hostA", "month": "2026-09", "grain": "raw"}).status_code == 400
        assert cl.get("/api/v2/nmon/summary", params={"host": "hostA", "month": "2026-09"}).json()["items"]
        assert cl.get("/api/v2/nmon/top", params={"host": "hostA", "ts": "2026-09-08 01:01:08"}).json()["procs"]
        assert cl.get("/api/v2/nmon/top", params={"host": "hostA", "ts": "bad"}).status_code == 400
        rep = cl.get("/api/v2/host-monthly", params={"month": "2026-09"}).json()
        assert rep["nmon_perf"][0]["host"] == "hostA" and rep["nmon"][0]["samples"] == 120 and rep["nmon_files"][0]["has_raw"]
        d = cl.get("/api/v2/nmon/raw", params={"host": "hostA", "filename": "b.nmon.Z"})
        assert d.status_code == 200 and d.content == lzw_compress(text)                      # 原樣下載
        assert cl.get("/api/v2/nmon/raw", params={"host": "hostA", "filename": "../../etc/passwd"}).status_code == 404


def test_原始檔下載只給管理員():
    with tempfile.TemporaryDirectory() as tmp:
        cl = _client(tmp, user="tester")
        assert cl.get("/api/v2/nmon/raw", params={"host": "x", "filename": "y"}).status_code == 403


def test_解壓後太大拒絕處理(monkeypatch):
    monkeypatch.setattr(ns, "MAX_TEXT_BYTES", 5000)
    big = b"A" * 20000
    with pytest.raises(ValueError):
        ns.to_text(gzip.compress(big))
    with pytest.raises(ValueError):
        ns.to_text(lzw_compress(big))
    assert ns.to_text(gzip.compress(b"small")) == "small"


def test_月報CPU表與圖表摘要同一個數字():
    """月報表（nmon_sample）與效能圖表卡片（nmon_series）講同一台同一月的 CPU：平均、尖峰、超標樣本數必須一致。"""
    with tempfile.TemporaryDirectory() as tmp:
        c = _db(tmp)
        cpu = lambda i: 85.0 if 10 <= i < 25 else (3.0 + (i % 7))      # noqa: E731
        host_monthly.import_nmon(c, make_aix(n=240, cpu=cpu).encode(), "x.nmon", "tester")
        row = host_monthly.nmon_month(c, "2026-09")[0]
        card = next(i for i in ns.summary(c, "hostA", "2026-09")["items"] if i["key"] == "cpu_busy")
        assert row["peak"] == card["peak"] and row["over"] == card["over"] and row["samples"] == card["samples"]
        assert abs(row["avg"] - card["avg"]) <= 0.1
        c.close()


# ---------------------------------------------------------------- 獨立審查（2026-10-28）發現的問題，各一條回歸測試
def test_主機名是點點加檔名asset_db_不能蓋掉資料庫():
    """審查 #2：主機名取自檔案內容、檔名取自上傳，兩個都不能用來組路徑。"""
    text = make_aix(host="..", n=30).encode()
    with tempfile.TemporaryDirectory() as tmp:
        cl = _client(tmp)
        before = (Path(tmp) / "t.db").stat().st_size
        r = cl.post("/api/v2/host-monthly/nmon", files={"file": ("t.db", text)})
        assert r.status_code == 200, r.text
        assert (Path(tmp) / "t.db").stat().st_size >= before and (Path(tmp) / "t.db").read_bytes()[:15] == b"SQLite format 3"
        raws = list((Path(tmp) / "nmon_raw").iterdir())
        assert len(raws) == 1 and raws[0].name.endswith(".nmon") and raws[0].name != "t.db"
        assert cl.get("/api/v2/nmon/hosts", params={"month": "2026-09"}).status_code == 200          # 資料庫沒壞


def test_nan與inf不收():
    """審查 #3。"""
    assert ns._num("nan") is None and ns._num("inf") is None and ns._num("-inf") is None and ns._num("3.456") == 3.46


def test_磁碟名稱有冒號不會讓摘要壞掉():
    """審查 #4。"""
    d = ns.describe("disk:a:b:busy")
    assert d["label"].startswith("a:b") and d["unit"] == "%"
    assert ns.describe("nic:en0:rx")["label"].startswith("en0")


def test_gzip好幾段接起來都要解_壞檔是ValueError():
    """審查 #5。"""
    a, b = b"AAA,host,x\n", b"CPU_ALL,T0001\n"
    assert ns.to_text(gzip.compress(a) + gzip.compress(b)) == (a + b).decode()
    bad = gzip.compress(a)[:-8] + b"garbage!"
    try:
        ns.to_text(bad)
    except ValueError:
        pass


def test_還沒有人上傳過時_hosts端點不是500():
    """審查 #7：還沒建 nmon_file 表。"""
    with tempfile.TemporaryDirectory() as tmp:
        cl = _client(tmp)
        r = cl.get("/api/v2/nmon/hosts", params={"month": "2026-09"})
        assert r.status_code == 200 and r.json()["files"] == []


# Q91：這個檔案測的是「誰能做什麼」，不套 conftest 的「測試帳號預設 admin」相容層（見 tests/conftest.py）。
ROLE_AWARE_TESTS = True
