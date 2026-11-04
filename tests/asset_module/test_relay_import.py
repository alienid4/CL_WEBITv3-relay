"""中繼匯入（方案 A）：批次契約、守門、冪等、鮮度燈、WhatsUp adapter、排程入口與 API 權限。

不連任何網路；批次全部在暫存資料夾現做（範例 IP 只用 192.0.2.x）。
"""
import gzip
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import capacity_analysis as ca  # noqa: E402
import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402

MON = lambda h, m=0, s=0: datetime(2026, 11, 2, h, m, s)     # noqa: E731  週一
SAT = lambda h, m=0, s=0: datetime(2026, 11, 7, h, m, s)     # noqa: E731

IP1, IP2 = "192.0.2.10", "192.0.2.11"
ROLE_AWARE_TESTS = True      # API 權限測試要用真實的角色判斷


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)       # 負值：避開檔案時間比系統時鐘快一點點的情況
    qw.set_clock(lambda: SAT(10))
    db.set_setting(c, ri.DIR_KEY, str(drop))
    db.set_setting(c, ri.ENABLED_KEY, "1")
    yield c, drop
    qw.set_clock(None)
    c.close()


def _frac(v):
    """測試裡用 0–100 寫比較好讀；WhatsUp 原生回 0～1 小數（2.047.6 實測），所以餵進批次前一律 ÷100，跟正式資料同刻度。"""
    return v / 100 if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def rows_of(report, pts, **ident):
    return [{"pollTimeUtc": t, "avgPercent": _frac(a), "maxPercent": _frac(m), "minPercent": _frac(n), **ident} for t, a, m, n in pts]


def device(ip=IP1, cpu=None, mem=None, disk=None, extra=None):
    reports = {}
    if cpu is not None:
        reports["cpu-utilization"] = cpu
    if mem is not None:
        reports["memory-utilization"] = mem
    if disk is not None:
        reports["disk-utilization"] = disk
    return {"ip": ip, "id": 7, "name": "dev-" + ip.split(".")[-1], "reports": reports, **(extra or {})}


CPU = rows_of("cpu", [("2026-11-01T00:00:00Z", 10.0, 20.0, 5.0), ("2026-11-01T00:03:00Z", 12.0, 30.0, 6.0), ("2026-11-01T00:06:00Z", 14.0, 25.0, 7.0)], cpuId=1)
MEM = rows_of("mem", [("2026-11-01T00:00:00Z", 50.0, 55.0, 48.0), ("2026-11-01T00:03:00Z", 51.0, 56.0, 49.0)], memoryId=1)
DISK = rows_of("disk", [("2026-11-01T00:00:00Z", 70.0, 70.0, 70.0), ("2026-11-01T00:10:00Z", 71.0, 71.0, 71.0)], diskId=3, disk="C:")


def write_batch(drop, name, devices, source="whatsup", mutate=None, tamper=None, manifest_extra=None, collected="2026-11-02T21:05:00+08:00"):
    """寫一個完整批次。mutate(manifest) 可改 manifest；tamper(folder) 在算完 sha 後動手腳。回資料夾。"""
    d = Path(drop) / name
    d.mkdir(parents=True)
    files = []
    for fname, content in devices.items():
        raw = json.dumps(content, separators=(",", ":")).encode()
        (d / fname).write_bytes(raw)
        files.append({"name": fname, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)})
    m = {"source": source, "schema_version": 1, "collector_version": "wug_collect 2", "collected_at": collected,
         "range": {"from": "2026-11-01", "to": "2026-11-01"}, "count": len(files), "files": files, **(manifest_extra or {})}
    if mutate:
        mutate(m)
    if tamper:
        tamper(d)
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return d


def good(drop, name="b1", **kw):
    return write_batch(drop, name, {IP1 + ".json": device(IP1, CPU, MEM, DISK)}, **kw)


def perf(c, ip=IP1, day="2026-11-01"):
    r = c.execute("SELECT data, interval_s, samples FROM capacity_whatsup_perf WHERE host=? AND day=?", (ip, day)).fetchone()
    return (ca._decode(r[0]), r[1], r[2]) if r else None


def batch_row(c, name="b1"):
    return c.execute("SELECT * FROM relay_import_batches WHERE batch_id=?", (name,)).fetchone()


# ---------------------------------------------------------------- 正常匯入
def test_好批次匯入_容量分析來源表有資料_標來源relay_時區UTC_不補零(env):
    c, drop = env
    good(drop)
    out = ri.run_scan(c, "manual", "admin")
    assert out["imported"] == 1 and out["rejected"] == 0, out
    d, itv, n = perf(c)
    assert d["src"] == "relay" and d["tz"] == "UTC" and d["kind"] == "bucket"
    assert d["ts"] == ["00:00:00", "00:03:00", "00:06:00"] and itv == 180 and n == 3
    assert d["cols"]["cpu_busy"] == [10.0, 12.0, 14.0] and d["max"]["cpu_busy"] == [20.0, 30.0, 25.0]
    assert d["cols"]["mem_used"] == [50.0, 51.0, None]             # 06 分沒有記憶體資料：留空，不是 0
    assert d["min"]["cpu_busy"] == [5.0, 6.0, 7.0]
    b = batch_row(c)
    assert b["status"] == "imported" and b["imported_by"] == "admin" and b["trigger"] == "manual" and b["devices"] == 1 and b["points"] == 3
    assert b["collected_at"] == "2026-11-02 21:05:00"
    assert c.execute("SELECT COUNT(*) FROM relay_disk_util").fetchone()[0] == 1      # 磁碟另表保存
    assert ca.list_hosts(c) == [IP1]                                              # 容量分析看得到這台


def test_冪等_同一批放兩次或掃兩次不重複寫(env):
    c, drop = env
    good(drop)
    ri.run_scan(c)
    before = c.execute("SELECT updated_at, data FROM capacity_whatsup_perf").fetchall()
    out2 = ri.run_scan(c, now=MON(3))
    assert out2["imported"] == 0 and out2["found"] == 1
    assert c.execute("SELECT COUNT(*) FROM relay_import_batches").fetchone()[0] == 1
    after = c.execute("SELECT updated_at, data FROM capacity_whatsup_perf").fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert ri.pending_batches(c) == []


def test_時區_沒帶時區的時間依manifest的time_zone解讀(env):
    c, drop = env
    cpu = rows_of("cpu", [("2026-11-01T08:00:00", 10.0, 20.0, 5.0)], cpuId=1)
    write_batch(drop, "b1", {IP1 + ".json": device(IP1, cpu)}, manifest_extra={"time_zone": "Asia/Taipei"})
    ri.run_scan(c)
    d, _, _ = perf(c)
    assert d["ts"] == ["00:00:00"]                                                # 台北 08:00 = UTC 00:00


def test_同時間點多個cpuId取平均與極值(env):
    c, drop = env
    cpu = rows_of("cpu", [("2026-11-01T00:00:00Z", 10.0, 20.0, 5.0)], cpuId=1) + rows_of("cpu", [("2026-11-01T00:00:00Z", 30.0, 90.0, 1.0)], cpuId=2)
    write_batch(drop, "b1", {IP1 + ".json": device(IP1, cpu)})
    ri.run_scan(c)
    d, _, _ = perf(c)
    assert d["cols"]["cpu_busy"] == [20.0] and d["max"]["cpu_busy"] == [90.0] and d["min"]["cpu_busy"] == [1.0]


def test_壞值當缺值_不補零(env):
    c, drop = env
    cpu = rows_of("cpu", [("2026-11-01T00:00:00Z", 10.0, 20.0, 5.0), ("2026-11-01T00:03:00Z", 150.0, None, None), ("bad-time", 1.0, 1.0, 1.0)], cpuId=1)
    write_batch(drop, "b1", {IP1 + ".json": device(IP1, cpu)})
    ri.run_scan(c)
    d, _, _ = perf(c)
    assert d["ts"] == ["00:00:00"]                                                # 全是壞值的時間點整個不收
    assert batch_row(c)["bad_values"] == 2


def test_跨批次合併_同一天兩段資料依時間點合併_新的蓋舊的(env):
    c, drop = env
    write_batch(drop, "b1", {IP1 + ".json": device(IP1, rows_of("c", [("2026-11-01T00:00:00Z", 10.0, 20.0, 5.0), ("2026-11-01T00:03:00Z", 11.0, 21.0, 6.0)], cpuId=1))})
    ri.run_scan(c)
    write_batch(drop, "b2", {IP1 + ".json": device(IP1, rows_of("c", [("2026-11-01T00:03:00Z", 99.0, 99.0, 99.0), ("2026-11-01T00:06:00Z", 12.0, 22.0, 7.0)], cpuId=1))}, collected="2026-11-03T21:05:00+08:00")
    ri.run_scan(c)
    d, _, _ = perf(c)
    assert d["ts"] == ["00:00:00", "00:03:00", "00:06:00"] and d["cols"]["cpu_busy"] == [10.0, 99.0, 12.0]


def test_不覆蓋既有的REST來源資料(env):
    c, drop = env
    rest = {"ts": ["00:00:00"], "cols": {"cpu_busy": [1.0]}, "max": {}, "cnt": {}, "exp": {}, "tz": "UTC", "kind": "bucket", "top": {}}
    blob = gzip.compress(json.dumps(rest).encode())
    c.execute(ca.WU_TABLE_SQL)
    c.execute("INSERT INTO capacity_whatsup_perf VALUES (?,?,?,?,?,?)", (IP1, "2026-11-01", 3600, 1, blob, "x"))
    c.commit()
    good(drop)
    ri.run_scan(c)
    assert c.execute("SELECT data FROM capacity_whatsup_perf WHERE host=?", (IP1,)).fetchone()[0] == blob
    assert batch_row(c)["skipped_existing"] == 1 and batch_row(c)["status"] == "imported"


def test_不碰NMON資料(env):
    c, drop = env
    c.execute("CREATE TABLE IF NOT EXISTS nmon_series (host TEXT NOT NULL, day TEXT NOT NULL, interval_s INTEGER, samples INTEGER, data BLOB NOT NULL, updated_at TEXT, PRIMARY KEY (host, day))")
    c.execute("INSERT INTO nmon_series VALUES (?,?,?,?,?,?)", (IP1, "2026-11-01", 60, 1, b"x", "x"))
    c.commit()
    good(drop)
    ri.run_scan(c)
    assert c.execute("SELECT data FROM nmon_series").fetchone()[0] == b"x" and c.execute("SELECT COUNT(*) FROM nmon_series").fetchone()[0] == 1


# ---------------------------------------------------------------- 拒收
def reject_code(c, name="b1"):
    r = batch_row(c, name)
    assert r is not None and r["status"] == "rejected", dict(r) if r else None
    return r["reason_code"]


def nothing_written(c):
    c.execute(ca.WU_TABLE_SQL)
    return c.execute("SELECT COUNT(*) FROM capacity_whatsup_perf").fetchone()[0] == 0 and c.execute("SELECT COUNT(*) FROM relay_disk_util").fetchone()[0] == 0


def test_manifest缺欄位拒收(env):
    c, drop = env
    good(drop, mutate=lambda m: m.pop("collector_version"))
    ri.run_scan(c)
    assert reject_code(c) == "manifest_invalid" and nothing_written(c)


def test_count和檔案數不一致拒收(env):
    c, drop = env
    good(drop, mutate=lambda m: m.update(count=5))
    ri.run_scan(c)
    assert reject_code(c) == "manifest_invalid"


def test_sha不符拒收_修好後重新處理(env):
    c, drop = env
    d = good(drop, tamper=lambda f: (f / (IP1 + ".json")).write_bytes((f / (IP1 + ".json")).read_bytes().replace(b"dev-", b"dex-")))
    ri.run_scan(c)
    assert reject_code(c) == "sha_mismatch" and nothing_written(c)
    out = ri.run_scan(c)                                  # 內容沒變：不重複處理、也不重複記
    assert out["rejected"] == 0 and c.execute("SELECT COUNT(*) FROM relay_import_batches").fetchone()[0] == 1
    # 收集端重新放一份對的
    for p in d.iterdir():
        p.unlink()
    d.rmdir()
    good(drop)
    out = ri.run_scan(c)
    assert out["imported"] == 1 and batch_row(c)["status"] == "imported"


def test_空批次拒收_不讓鮮度燈誤判(env):
    c, drop = env
    write_batch(drop, "b1", {})
    ri.run_scan(c)
    assert reject_code(c) == "empty_batch"
    assert {s["source"]: s for s in ri.sources_view(c)}["whatsup"]["status"] == "never"


def test_大小不符拒收(env):
    c, drop = env

    def bump(m):
        m["files"][0]["size"] += 1
    good(drop, mutate=bump)
    ri.run_scan(c)
    assert reject_code(c) == "size_mismatch"


def test_檔案缺少拒收(env):
    c, drop = env
    good(drop, tamper=lambda f: (f / (IP1 + ".json")).unlink())
    ri.run_scan(c)
    assert reject_code(c) == "file_missing"


@pytest.mark.parametrize("key", ["password", "Password", "access_token", "api-key", "clientSecret", "Authorization", "bearer"])
def test_含密碼或token類欄位拒收(env, key):
    c, drop = env
    write_batch(drop, "b1", {IP1 + ".json": device(IP1, CPU, extra={key: "x"})})
    ri.run_scan(c)
    assert reject_code(c) == "secret_field" and nothing_written(c)
    assert "x" not in (batch_row(c)["reason"] or "").replace("欄位名稱", "")        # 原因只寫欄位名稱，不回顯值


def test_巢狀的機密欄位與bearer字串也拒收(env):
    c, drop = env
    cpu = [{"pollTimeUtc": "2026-11-01T00:00:00Z", "avgPercent": 1.0, "meta": {"auth": {"Token": "abc"}}}]
    write_batch(drop, "b1", {IP1 + ".json": device(IP1, cpu)})
    write_batch(drop, "b2", {IP2 + ".json": device(IP2, extra={"note": "Bearer abcdefghijklmnop"})})
    ri.run_scan(c)
    assert reject_code(c, "b1") == "secret_field" and reject_code(c, "b2") == "secret_field"


def test_manifest含機密欄位拒收(env):
    c, drop = env
    good(drop, manifest_extra={"token": "abc"})
    ri.run_scan(c)
    assert reject_code(c) == "secret_field"


@pytest.mark.parametrize("bad", ["../x.json", "sub/x.json", "..\\x.json", "/etc/passwd", "x.json.tmp", "manifest.json", ""])
def test_路徑跳脫或不合法檔名拒收(env, bad):
    c, drop = env
    d = good(drop)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["files"][0]["name"] = bad
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    ri.run_scan(c)
    assert reject_code(c) == "path_unsafe" and nothing_written(c)


def test_符號連結的資料檔不跟隨(env, tmp_path):
    c, drop = env
    outside = tmp_path / "outside.json"
    raw = json.dumps(device(IP1, CPU)).encode()
    outside.write_bytes(raw)
    d = write_batch(drop, "b1", {IP1 + ".json": device(IP1, CPU)})
    (d / (IP1 + ".json")).unlink()
    try:
        os.symlink(outside, d / (IP1 + ".json"))
    except (OSError, NotImplementedError):
        pytest.skip("這個環境不能建符號連結")
    ri.run_scan(c)
    assert reject_code(c) == "path_unsafe" and nothing_written(c)


def test_符號連結的批次資料夾不收(env, tmp_path):
    c, drop = env
    real = tmp_path / "elsewhere"
    real.mkdir()
    good(real, "b1")
    try:
        os.symlink(real / "b1", drop / "linked", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("這個環境不能建符號連結")
    assert ri.list_batch_folders(str(drop))[0] == []


def test_單檔超過上限拒收(env):
    c, drop = env
    db.set_setting(c, ri.MAX_FILE_MB_KEY, "0")
    good(drop)
    ri.run_scan(c)
    assert reject_code(c) == "too_large"


def test_壞JSON與壞資料拒收(env):
    c, drop = env
    d = write_batch(drop, "b1", {IP1 + ".json": device(IP1, CPU)})
    raw = b"{not json"
    (d / (IP1 + ".json")).write_bytes(raw)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["files"][0].update(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    write_batch(drop, "b2", {"x.json": {"ip": "not-an-ip", "reports": {}}})
    ri.run_scan(c)
    assert reject_code(c, "b1") == "bad_json" and reject_code(c, "b2") == "bad_data"


def test_不認得的來源_未實作的Unity_不支援的版本_都拒收(env):
    c, drop = env
    write_batch(drop, "b1", {"x.json": {}}, source="mystery")
    write_batch(drop, "b2", {"x.json": {}}, source="unity")
    good(drop, "b3", mutate=lambda m: m.update(schema_version=9))
    ri.run_scan(c)
    assert reject_code(c, "b1") == "unknown_source" and reject_code(c, "b2") == "unsupported" and reject_code(c, "b3") == "schema_unsupported"
    assert nothing_written(c)


def test_整批原子性_一台壞整批都不寫(env):
    c, drop = env
    write_batch(drop, "b1", {"a.json": device(IP1, CPU), "b.json": {"ip": "bad", "reports": {}}})
    ri.run_scan(c)
    assert reject_code(c) == "bad_data" and nothing_written(c)


def test_寫入中途出錯整批回滾並標failed_下一輪會再試(env, monkeypatch):
    c, drop = env
    good(drop)
    real = ri.WhatsUpAdapter._write_disk

    def boom(self, *a, **k):
        raise sqlite_err()
    monkeypatch.setattr(ri.WhatsUpAdapter, "_write_disk", boom)
    out = ri.run_scan(c)
    assert out["failed"] == 1 and nothing_written(c) and batch_row(c)["status"] == "failed"
    assert ri.pending_batches(c)                                    # failed 的下一輪還會再試
    monkeypatch.setattr(ri.WhatsUpAdapter, "_write_disk", real)
    assert ri.run_scan(c)["imported"] == 1 and batch_row(c)["status"] == "imported"


def sqlite_err():
    import sqlite3
    return sqlite3.OperationalError("disk full (test)")


# ---------------------------------------------------------------- 未匹配
def test_對不到資產的列入未匹配清單_資料照樣匯入_不丟棄(env):
    c, drop = env
    good(drop)
    ri.run_scan(c)
    u = ri.list_unmatched(c)
    assert len(u) == 1 and u[0]["ident"] == IP1 and u[0]["reason"] == "no_asset" and u[0]["__no"] == 1
    assert perf(c) is not None and batch_row(c)["unmatched"] == 1


def _hw(c, serial, host, ip):
    c.execute("INSERT INTO hardware (asset_serial, hostname, ip) VALUES (?,?,?)", (serial, host, ip))
    c.commit()


def test_對得到資產就不在未匹配_之後才對到會從清單移除(env):
    c, drop = env
    good(drop)
    ri.run_scan(c)
    assert len(ri.list_unmatched(c)) == 1
    _hw(c, "SN1", "web01", IP1)
    write_batch(drop, "b2", {IP1 + ".json": device(IP1, CPU)}, collected="2026-11-03T21:05:00+08:00")
    ri.run_scan(c)
    assert ri.list_unmatched(c) == [] and batch_row(c, "b2")["unmatched"] == 0


def test_同IP對到兩台不同機器_標多重對應不猜(env):
    c, drop = env
    _hw(c, "SN1", "web01", IP1)
    _hw(c, "SN2", "db02", IP1)
    good(drop)
    ri.run_scan(c)
    u = ri.list_unmatched(c)
    assert len(u) == 1 and u[0]["reason"] == "ambiguous" and u[0]["candidates"] == 2


# ---------------------------------------------------------------- 鮮度燈
def test_鮮度燈三態(env):
    c, drop = env
    v = {s["source"]: s for s in ri.sources_view(c)}
    assert v["whatsup"]["status"] == "never" and v["whatsup"]["status_label"] == "從未收到"
    assert v["unity"]["status"] == "never" and v["unity"]["implemented"] is False
    good(drop)                                         # collected_at = 2026-11-02 21:05 台北
    ri.run_scan(c)
    assert {s["source"]: s for s in ri.sources_view(c, datetime(2026, 11, 3, 21, 5))}["whatsup"]["status"] == "ok"
    st = {s["source"]: s for s in ri.sources_view(c, datetime(2026, 11, 4, 22, 0))}["whatsup"]
    assert st["status"] == "stale" and st["status_label"] == "資料過期"
    db.set_setting(c, ri.STALE_DAYS_KEY, "5")
    assert {s["source"]: s for s in ri.sources_view(c, datetime(2026, 11, 4, 22, 0))}["whatsup"]["status"] == "ok"      # 門檻可設定


def test_被拒收的批次不會讓鮮度燈變綠(env):
    c, drop = env
    good(drop, mutate=lambda m: m.pop("count"))
    ri.run_scan(c)
    s = {s["source"]: s for s in ri.sources_view(c)}["whatsup"]
    assert s["status"] == "never" and s["problem_batches"] == 1


# ---------------------------------------------------------------- 排程入口與盤中靜默窗
def test_tick_預設關閉(env):
    c, drop = env
    db.set_setting(c, ri.ENABLED_KEY, "0")
    good(drop)
    assert ri.tick(c, SAT(10))["status"] == "disabled" and batch_row(c) is None


def test_tick_窗內不動並記延後_窗外才匯入(env):
    c, drop = env
    good(drop)
    qw.set_clock(lambda: MON(10))
    assert ri.tick(c, MON(10))["status"] == "deferred_quiet_window"
    assert batch_row(c) is None and c.execute("SELECT count FROM quiet_window_log WHERE entry='relay_import_tick' AND kind='deferred'").fetchone()[0] >= 1      # 窗內連資料夾都不讀、記延後
    qw.set_clock(lambda: MON(15, 1))
    out = ri.tick(c, MON(15, 1))
    assert out["status"] == "ok" and out["imported"] == 1 and batch_row(c)["trigger"] == "schedule" and batch_row(c)["imported_by"] == "system"


def test_tick_沒有新批次不記延後_也依間隔略過(env):
    c, drop = env
    qw.set_clock(lambda: SAT(10))
    assert ri.tick(c, SAT(10))["status"] == "idle"
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='quiet_window_log'").fetchone() or c.execute("SELECT COUNT(*) FROM quiet_window_log WHERE entry='relay_import_tick'").fetchone()[0] == 0
    assert ri.tick(c, SAT(10, 1))["status"] == "not_due"


def test_tick_間隔到了才再看(env):
    c, drop = env
    good(drop)
    assert ri.tick(c, SAT(10))["status"] == "ok"
    write_batch(drop, "b2", {IP1 + ".json": device(IP1, CPU)}, collected="2026-11-03T21:05:00+08:00")
    assert ri.tick(c, SAT(10, 5))["status"] == "not_due"
    assert ri.tick(c, SAT(10, 20))["imported"] == 1


def test_tick_不連線_模組沒有任何網路相關匯入():
    src = (ROOT / "APP" / "asset-module" / "backend" / "relay_import.py").read_text(encoding="utf-8")
    for bad in ("socket", "requests", "urllib", "http.client", "paramiko", "subprocess", "httpx"):
        assert f"import {bad}" not in src and f"from {bad}" not in src, bad


# ---------------------------------------------------------------- 設定
def test_設定驗證(env):
    c, _ = env
    with pytest.raises(ValueError):
        ri.set_config(c, True, "", 2, 15)
    with pytest.raises(ValueError):
        ri.set_config(c, False, "relative/path", 2, 15)
    with pytest.raises(ValueError):
        ri.set_config(c, False, "/a/../b", 2, 15)
    with pytest.raises(ValueError):
        ri.set_config(c, False, "/ok", 0, 15)
    with pytest.raises(ValueError):
        ri.set_config(c, False, "/ok", 2, 0)
    assert ri.set_config(c, True, "/mnt/relay", 3, 10)["stale_days"] == 3
    assert ri.set_config(c, False, "D:\\relay\\drop", 2, 15)["dir"] == "D:\\relay\\drop"


# ---------------------------------------------------------------- API
def _api(tmp, users):
    import api
    import auth
    from fastapi.testclient import TestClient
    p = Path(tmp) / "t.db"
    db.init_db(p)

    def _ov():
        cn = db.get_connection(p)
        try:
            yield cn
        finally:
            cn.close()
    api.app.dependency_overrides[api.get_db] = _ov
    cn = db.get_connection(p)
    for name, role in users:
        db.create_user(cn, name, auth.hash_password("test-password-123"))
        cn.execute("UPDATE users SET role = ? WHERE username = ?", (role, name))
    cn.commit()
    cn.close()

    def client(name):
        cl = TestClient(api.app, raise_server_exceptions=False)
        assert cl.post("/api/auth/login", json={"username": name, "password": "test-password-123"}).status_code == 200
        tok = cl.cookies.get("session_token")
        if tok:
            cl.headers["X-CSRF-Token"] = auth.csrf_token_for(tok)      # 自己帶 CSRF，不依賴 conftest 的補丁（TestClient 版本不同時補丁可能沒生效）
        return cl
    return client, p


def test_API_只有admin能設定與執行_大家可看_窗內要確認():
    qw.set_clock(lambda: SAT(10))
    saved = ri.STABLE_SECONDS
    ri.STABLE_SECONDS = -5
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            drop = Path(tmp) / "drop"
            drop.mkdir()
            client, p = _api(tmp, [("admin", "admin"), ("alice", "user"), ("vera", "viewer")])
            admin, alice, vera = client("admin"), client("alice"), client("vera")
            body = {"enabled": True, "dir": str(drop), "stale_days": 2, "interval_min": 15}
            for who in (alice, vera):
                assert who.put("/api/v2/relay-import/settings", json=body).status_code == 403
                assert who.post("/api/v2/relay-import/run").status_code == 403
                r = who.get("/api/v2/relay-import")
                assert r.status_code == 200 and {s["source"] for s in r.json()["sources"]} == {"whatsup", "unity", "cert", "whatsup_alert", "aria_host"}
                assert who.get("/api/v2/relay-import/batches").status_code == 200 and who.get("/api/v2/relay-import/unmatched").status_code == 200
            r = admin.put("/api/v2/relay-import/settings", json={**body, "dir": "relative"})
            assert r.status_code == 400, r.text
            assert admin.put("/api/v2/relay-import/settings", json=body).status_code == 200
            good(drop)
            r = admin.post("/api/v2/relay-import/run")
            assert r.status_code == 200 and r.json()["imported"] == 1
            rows = vera.get("/api/v2/relay-import/batches").json()["rows"]
            assert len(rows) == 1 and rows[0]["status"] == "imported" and rows[0]["__no"] == 1 and rows[0]["folder"] == "b1" and not rows[0]["sig"]
            assert {s["source"]: s for s in vera.get("/api/v2/relay-import").json()["sources"]}["whatsup"]["last_batch"] == "b1"
            assert len(vera.get("/api/v2/relay-import/unmatched").json()["rows"]) == 1
            qw.set_clock(lambda: MON(10))
            r = admin.post("/api/v2/relay-import/run")
            assert r.status_code == 409 and r.json()["detail"]["code"] == "quiet_window_confirm"
            assert admin.post("/api/v2/relay-import/run?confirm_in_window=true").status_code == 200
            cn = db.get_connection(p)
            acts = [x[0] for x in cn.execute("SELECT action FROM activity_log WHERE action LIKE 'relay_import%'")]
            assert "relay_import_settings" in acts and "relay_import_run" in acts
            cn.close()
    finally:
        ri.STABLE_SECONDS = saved
        qw.set_clock(None)
        import api
        api.app.dependency_overrides.clear()


def test_tick_跑到一半進入盤中靜默窗_後面的批次留待窗外(env, monkeypatch):
    """Codex 305：排程一輪多批時，每一批開始前重新看窗；第一批做完時鐘進窗，後兩批不做、記延後，窗外下一輪補。"""
    c, drop = env
    for i, ip in enumerate((IP1, IP2, "192.0.2.12"), 1):
        write_batch(drop, f"b{i}", {ip + ".json": device(ip, CPU)}, collected=f"2026-11-0{i}T21:05:00+08:00")
    qw.set_clock(lambda: MON(8, 29))
    real = ri.import_batch

    def first_then_window(*a, **kw):
        out = real(*a, **kw)
        qw.set_clock(lambda: MON(8, 30))          # 第一批做完，時間走進盤中
        return out

    monkeypatch.setattr(ri, "import_batch", first_then_window)
    out = ri.tick(c)
    assert out["status"] == "ok" and out["imported"] == 1 and "盤中靜默窗" in out["message"]
    assert c.execute("SELECT COUNT(*) FROM quiet_window_log WHERE entry='relay_import_tick' AND kind='deferred'").fetchone()[0] >= 1
    monkeypatch.setattr(ri, "import_batch", real)
    qw.set_clock(lambda: MON(15, 1))
    db.set_setting(c, ri.LAST_CHECK_KEY, "")
    out = ri.tick(c)
    assert out["status"] == "ok" and out["imported"] == 2


def test_人工匯入_窗內已確認_整輪做完不中途停(env):
    c, drop = env
    for i, ip in enumerate((IP1, IP2), 1):
        write_batch(drop, f"m{i}", {ip + ".json": device(ip, CPU)}, collected=f"2026-11-0{i}T21:05:00+08:00")
    qw.set_clock(lambda: MON(10))
    out = ri.run_scan(c, "manual", "admin")
    assert out["imported"] == 2


# ---------------------------------------------------------------- 2.047.6 WhatsUp 百分比刻度（原生 0～1 小數 → 0–100）
@pytest.mark.parametrize("raw, want", [(0.96, 96.0), (1.0, 100.0), (0, 0.0), (0.0, 0.0), (0.004, 0.4), (1.0000001, 100.0),
                                       (None, None), (1.5, None), (-0.1, None), (float("nan"), None), (float("inf"), None),
                                       (True, None), ("0.5", None)])
def test_刻度換算_小數變百分比_缺值仍是缺值_超出範圍當缺值(raw, want):
    assert ri.to_percent(raw, ri.WUG_SCALES["fraction"]) == want


@pytest.mark.parametrize("raw, want", [(96, 96.0), (100.0, 100.0), (0.5, 0.5), (101, None), (None, None)])
def test_刻度換算_manifest註明percent就原樣(raw, want):
    assert ri.to_percent(raw, ri.WUG_SCALES["percent"]) == want


def _raw_rows(pts, **ident):
    """不經 _frac：直接寫 WhatsUp 原樣的數值。"""
    return [{"pollTimeUtc": t, "avgPercent": a, "maxPercent": m, "minPercent": n, **ident} for t, a, m, n in pts]


def _disk(c, ip=IP1, day="2026-11-01", disk="C:"):
    r = c.execute("SELECT data FROM relay_disk_util WHERE host=? AND day=? AND disk=?", (ip, day, disk)).fetchone()
    return json.loads(gzip.decompress(r[0]).decode()) if r else None


def test_WhatsUp原樣小數_匯入後全站看到的是百分比(env):
    c, drop = env
    cpu = _raw_rows([("2026-11-01T00:00:00Z", 0.4, 0.96, 0.1)], cpuId=1)
    mem = _raw_rows([("2026-11-01T00:00:00Z", 0.5, None, 0.45)], memoryId=1)
    disk = _raw_rows([("2026-11-01T00:00:00Z", 0.4, 1.0, 0.39)], diskId=3, disk="C:")
    write_batch(drop, "b1", {IP1 + ".json": device(IP1, cpu, mem, disk)})
    assert ri.run_scan(c)["imported"] == 1
    d, _, _ = perf(c)
    assert d["cols"]["cpu_busy"] == [40.0] and d["max"]["cpu_busy"] == [96.0] and d["min"]["cpu_busy"] == [10.0]
    assert d["cols"]["mem_used"] == [50.0] and d["max"]["mem_used"] == [None]           # 缺值仍是缺值，不是 0
    assert d["pct_scale"] == "percent"
    k = _disk(c)
    assert k["avg"] == [40.0] and k["max"] == [100.0] and k["min"] == [39.0] and k["pct_scale"] == "percent"


def test_manifest註明percent_收集端已換好的0到100原樣收(env):
    c, drop = env
    disk = _raw_rows([("2026-11-01T00:00:00Z", 40.0, 96.0, 39.0)], diskId=3, disk="C:")
    write_batch(drop, "b1", {IP1 + ".json": device(IP1, disk=disk)}, manifest_extra={"percent_scale": "percent"})
    assert ri.run_scan(c)["imported"] == 1
    assert _disk(c)["max"] == [96.0]


def test_manifest的percent_scale不認得_整批拒收(env):
    c, drop = env
    good(drop, manifest_extra={"percent_scale": "auto"})
    ri.run_scan(c)
    assert reject_code(c) == "manifest_invalid" and nothing_written(c)


def test_預設小數刻度卻收到0到100的檔_只那個檔不收_同批其他檔照收(env):
    c, drop = env
    pct = _raw_rows([("2026-11-01T00:00:00Z", 40.0, 96.0, 39.0)], diskId=3, disk="C:")
    ok = _raw_rows([("2026-11-01T00:00:00Z", 0.4, 0.96, 0.39)], diskId=3, disk="C:")
    write_batch(drop, "b1", {IP1 + ".json": device(IP1, disk=pct), IP2 + ".json": device(IP2, disk=ok)})
    assert ri.run_scan(c)["imported"] == 1
    b = batch_row(c)
    assert b["status"] == "imported" and "percent_scale" in b["reason"] and IP1 + ".json" in b["reason"] and IP2 not in b["reason"]
    assert b["bad_values"] == 3                                               # 那個檔的 3 個數值記壞值
    assert _disk(c) is None                                                   # 可疑的檔：一筆都沒寫
    assert _disk(c, ip=IP2)["max"] == [96.0]                                  # 同批其他檔照收


def test_刻度判斷是嚴格過半_剛好一半大於1不算(env):
    assert ri.looks_percent(2, 1) is False and ri.looks_percent(3, 2) is True and ri.looks_percent(0, 0) is False


def _legacy(c, batch="old1", cpu=(0.2, 0.5), mx=(0.3, 0.96), disk=(0.4, 0.96), mem=(0.5, None), extra=None):
    """造 2.047.5 以前的舊列：小數刻度、沒有 pct_scale 標記，batch 是已匯入的 whatsup 批次。extra 可直接蓋掉列的欄位。"""
    c.execute(ca.WU_TABLE_SQL)
    c.execute("INSERT OR IGNORE INTO relay_import_batches (source, batch_id, status, imported_at) VALUES ('whatsup', ?, 'imported', 'x')", (batch,))
    row = {"ts": ["00:00:00", "00:03:00"], "cols": {"cpu_busy": list(cpu), "mem_used": list(mem)}, "max": {"cpu_busy": list(mx), "mem_used": [0.55, None]},
           "min": {"cpu_busy": [0.1, 0.2], "mem_used": [0.45, None]}, "cnt": {}, "exp": {}, "tz": "UTC", "kind": "bucket", "top": {}, "src": "relay", "batch": batch,
           **(extra or {})}
    c.execute("INSERT INTO capacity_whatsup_perf VALUES (?,?,?,?,?,?)", (IP1, "2026-10-31", 180, 2, gzip.compress(json.dumps(row).encode()), "x"))
    dd = {"ts": ["00:00:00"], "avg": [disk[0]], "max": [disk[1]], "min": [disk[0]], "tz": "UTC"}
    c.execute("INSERT INTO relay_disk_util VALUES (?,?,?,?,?,?,?,?)", (IP1, "2026-10-31", "C:", 3600, 1, gzip.compress(json.dumps(dd).encode()), batch, "x"))
    c.execute("DELETE FROM relay_import_migrations WHERE name = ?", (ri.MIGRATION_WUG_PERCENT,))
    c.commit()


def test_遷移_舊的小數列乘100_只做一次_跑兩次結果一樣(env):
    c, drop = env
    _legacy(c)
    rest = {"ts": ["00:00:00"], "cols": {"cpu_busy": [0.5]}, "max": {}, "cnt": {}, "exp": {}, "tz": "UTC", "kind": "bucket", "top": {}}
    rest_blob = gzip.compress(json.dumps(rest).encode())
    c.execute("INSERT INTO capacity_whatsup_perf VALUES (?,?,?,?,?,?)", (IP2, "2026-10-31", 3600, 1, rest_blob, "x"))   # REST 來源：不碰
    c.commit()
    out = ri.migrate_wug_percent(c)
    assert out["status"] == "done" and out["perf_scaled"] == 1 and out["disk_scaled"] == 1 and out["skipped_other"] == 1, out
    d, _, _ = perf(c, day="2026-10-31")
    assert d["cols"]["cpu_busy"] == [20.0, 50.0] and d["max"]["cpu_busy"] == [30.0, 96.0] and d["min"]["mem_used"] == [45.0, None]
    assert d["pct_scale"] == "percent"
    assert _disk(c, day="2026-10-31")["max"] == [96.0]
    assert c.execute("SELECT data FROM capacity_whatsup_perf WHERE host=?", (IP2,)).fetchone()[0] == rest_blob
    snap = [tuple(r) for r in c.execute("SELECT * FROM capacity_whatsup_perf ORDER BY host")] + [tuple(r) for r in c.execute("SELECT * FROM relay_disk_util")]
    assert ri.migrate_wug_percent(c)["status"] == "already"                  # 第二次：有紀錄就不動
    db.init_db()                                                             # 啟動時再跑一次也一樣
    # 就算遷移紀錄不見了，每列的標記也擋得住，不會乘兩次
    c.execute("DELETE FROM relay_import_migrations")
    c.commit()
    assert ri.migrate_wug_percent(c)["status"] == "done"
    again = [tuple(r) for r in c.execute("SELECT * FROM capacity_whatsup_perf ORDER BY host")] + [tuple(r) for r in c.execute("SELECT * FROM relay_disk_util")]
    assert again == snap


def test_遷移_舊列已是0到100的只補標記不改值_不是whatsup批次的不碰(env):
    c, drop = env
    _legacy(c, cpu=(20.0, 50.0), mx=(30.0, 96.0), disk=(40.0, 96.0), mem=(50.0, None),
            extra={"max": {"cpu_busy": [30.0, 96.0], "mem_used": [55.0, None]}, "min": {"cpu_busy": [10.0, 20.0], "mem_used": [45.0, None]}})
    c.execute("INSERT INTO relay_disk_util VALUES (?,?,?,?,?,?,?,?)", (IP2, "2026-10-31", "C:", 3600, 1,
              gzip.compress(json.dumps({"ts": ["00:00:00"], "avg": [0.5], "max": [0.5], "min": [0.5], "tz": "UTC"}).encode()), "cert_x", "x"))
    c.commit()
    out = ri.migrate_wug_percent(c)
    assert out["perf_already_percent"] == 1 and out["disk_already_percent"] == 1 and out["perf_scaled"] == 0 and out["skipped_other"] == 0, out   # 非 whatsup 批次在 SQL 就篩掉
    assert perf(c, day="2026-10-31")[0]["max"]["cpu_busy"] == [30.0, 96.0]
    assert _disk(c, day="2026-10-31")["max"] == [96.0]
    assert _disk(c, ip=IP2, day="2026-10-31")["max"] == [0.5]                # 不是 whatsup 批次寫的：不碰


def test_遷移_每條序列各自判斷_少數大於1的值當缺值_不會整列被當成已換好(env):
    c, drop = env
    _legacy(c, cpu=(0.3, 0.9), mem=(0.5, 1.02))                               # 審查 P1 的例子：mem 有一個 1.02（少數）
    out = ri.migrate_wug_percent(c)
    assert out["perf_scaled"] == 1 and out["perf_already_percent"] == 0, out
    d, _, _ = perf(c, day="2026-10-31")
    assert d["cols"]["cpu_busy"] == [30.0, 90.0] and d["cols"]["mem_used"] == [50.0, None]   # 1.02 換算前 > 1 → 缺值，不是 102、也不是 1.02
    assert d["max"]["cpu_busy"] == [30.0, 96.0] and d["min"]["mem_used"] == [45.0, None]


def test_已標記的列即使全部值都小於等於1_重跑遷移與合併都不再乘(env):
    c, drop = env
    _legacy(c, extra={"pct_scale": "percent"})                               # 已換好、但機器很閒（全部 ≤ 1%）
    c.execute("UPDATE relay_disk_util SET data=? WHERE day='2026-10-31'",
              (gzip.compress(json.dumps({"ts": ["00:00:00"], "avg": [0.4], "max": [0.96], "min": [0.4], "tz": "UTC", "pct_scale": "percent"}).encode()),))
    c.commit()
    before = (perf(c, day="2026-10-31")[0], _disk(c, day="2026-10-31"))
    out = ri.migrate_wug_percent(c)
    assert out["perf_scaled"] == 0 and out["disk_scaled"] == 0 and out["perf_already_percent"] == 0, out
    assert (perf(c, day="2026-10-31")[0], _disk(c, day="2026-10-31")) == before
    write_batch(drop, "b1", {IP1 + ".json": device(IP1, rows_of("c", [("2026-10-31T00:06:00Z", 0.5, 0.8, 0.2)], cpuId=1),
                                                    disk=rows_of("d", [("2026-10-31T01:00:00Z", 0.5, 0.6, 0.4)], diskId=3, disk="C:"))})
    assert ri.run_scan(c)["imported"] == 1
    d, _, _ = perf(c, day="2026-10-31")
    assert d["cols"]["cpu_busy"] == [0.2, 0.5, 0.5] and d["max"]["cpu_busy"] == [0.3, 0.96, 0.8]   # 舊點原樣，新點 0.5%
    assert _disk(c, day="2026-10-31")["max"] == [0.96, 0.6]


def test_遷移紀錄已存在_啟動時仍補換之後才進來的未標記小數列(env):
    """部署 [3/7] 跑完遷移、[7/7] 重啟前，舊版服務還可能寫進未標記的小數列：下次啟動要補換，不能因為有紀錄就永遠漏掉。"""
    c, drop = env
    _legacy(c)
    c.execute("INSERT INTO relay_import_migrations (name, done_at) VALUES (?, 'x')", (ri.MIGRATION_WUG_PERCENT,))
    c.commit()
    db.init_db()
    d, _, _ = perf(c, day="2026-10-31")
    assert d["cols"]["cpu_busy"] == [20.0, 50.0] and d["pct_scale"] == "percent"
    assert _disk(c, day="2026-10-31")["max"] == [96.0]
    assert "catch_up_at" in c.execute("SELECT detail FROM relay_import_migrations").fetchone()[0]
    assert ri.migrate_wug_percent(c)["status"] == "already"                  # 再跑：沒有未標記的列，什麼都不動
    assert perf(c, day="2026-10-31")[0]["cols"]["cpu_busy"] == [20.0, 50.0]


def test_整批每個檔刻度都不對_記成拒收_修好後同批號會重新處理(env):
    c, drop = env
    pct = _raw_rows([("2026-11-01T00:00:00Z", 40.0, 96.0, 39.0)], diskId=3, disk="C:")
    d = write_batch(drop, "b1", {IP1 + ".json": device(IP1, disk=pct), IP2 + ".json": device(IP2, disk=pct)})
    ri.run_scan(c)
    assert reject_code(c) == "bad_data" and "percent_scale" in batch_row(c)["reason"]
    assert c.execute("SELECT COUNT(*) FROM relay_disk_util").fetchone()[0] == 0
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
    write_batch(drop, "b1", {IP1 + ".json": device(IP1, disk=pct)}, manifest_extra={"percent_scale": "percent"})   # 收集端補上標記重送
    assert ri.run_scan(c)["imported"] == 1 and batch_row(c)["status"] == "imported"
    assert _disk(c)["max"] == [96.0]


def test_遷移_中途出錯整批回滾_不寫紀錄_下次再試(env, monkeypatch):
    c, drop = env
    _legacy(c)
    orig = ri.rescale_legacy_disk

    def boom(_d):
        raise RuntimeError("boom")
    monkeypatch.setattr(ri, "rescale_legacy_disk", boom)
    assert ri.migrate_wug_percent(c)["status"] == "failed"
    assert perf(c, day="2026-10-31")[0]["cols"]["cpu_busy"] == [0.2, 0.5]    # perf 那一段也回滾了
    assert c.execute("SELECT COUNT(*) FROM relay_import_migrations").fetchone()[0] == 0
    monkeypatch.setattr(ri, "rescale_legacy_disk", orig)
    assert ri.migrate_wug_percent(c)["status"] == "done"
    assert perf(c, day="2026-10-31")[0]["cols"]["cpu_busy"] == [20.0, 50.0]


def test_合併_遷移沒跑到的舊小數列_新批次合併時先換刻度_不混刻度(env):
    c, drop = env
    _legacy(c)
    c.execute("INSERT INTO relay_import_migrations (name, done_at) VALUES (?, 'x')", (ri.MIGRATION_WUG_PERCENT,))   # 假裝遷移已跑（實際沒換）
    c.commit()
    cpu = rows_of("c", [("2026-10-31T00:06:00Z", 12.0, 22.0, 7.0)], cpuId=1)
    disk = rows_of("d", [("2026-10-31T01:00:00Z", 50.0, 60.0, 40.0)], diskId=3, disk="C:")
    write_batch(drop, "b1", {IP1 + ".json": device(IP1, cpu, disk=disk)})
    assert ri.run_scan(c)["imported"] == 1
    d, _, _ = perf(c, day="2026-10-31")
    assert d["cols"]["cpu_busy"] == [20.0, 50.0, 12.0] and d["max"]["cpu_busy"] == [30.0, 96.0, 22.0]
    assert _disk(c, day="2026-10-31")["max"] == [96.0, 60.0]


def test_數字核對_已換刻度的列都在0到100_舊列只觀察不擋部署(env):
    import verify_numbers as vn
    c, drop = env
    good(drop)
    ri.run_scan(c)

    def run():
        before = vn.FAILS
        vn.check_wug_percent_scale(c)
        return vn.FAILS - before, [r for r in vn.RESULTS if r["sec"].startswith("10.5")][-3:]
    fails, res = run()
    assert fails == 0 and all(r["ok"] is True for r in res), res
    _legacy(c)                                                              # 還沒遷移的舊列：⚠ 觀察，不計失敗
    fails, res = run()
    assert fails == 0 and res[-1]["ok"] is None and "待遷移" in res[-1]["detail"]
    ri.migrate_wug_percent(c)
    k = _disk(c, day="2026-10-31")
    k["max"] = [9600.0]                                                     # 假設有人又乘了一次 100
    c.execute("UPDATE relay_disk_util SET data=? WHERE day='2026-10-31'", (gzip.compress(json.dumps(k).encode()),))
    c.commit()
    fails, res = run()
    assert fails == 1 and res[-3]["ok"] is False
