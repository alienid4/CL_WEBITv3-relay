"""今日開盤尖峰（2.045.0）：跳板機收盤後匯出的 Aria ESXi 主機檔 → 中繼匯入（source=aria_host）→ opening_peak → 首頁右欄。

不連任何網路；批次在暫存資料夾現做。主機名用 esxi99-*.example.test，資產 IP 用 10.99.x。
"""
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import opening_peak as op  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402
import v2_today  # noqa: E402

DAY = "2026-11-02"                       # 週一（collected_at 不能在真實時鐘的未來，所以用過去的日子）
TPE = timezone(timedelta(hours=8))
AFTER_CLOSE = datetime(2026, 11, 2, 15, 30)
MORNING = datetime(2026, 11, 2, 10, 0)


def ms(day, hh, mm):
    return int(datetime.strptime(f"{day} {hh:02d}:{mm:02d}", "%Y-%m-%d %H:%M").replace(tzinfo=TPE).timestamp() * 1000)


def series(day, vals, start=(9, 0)):
    """從 start 起每 5 分鐘一點。"""
    h, m = start
    t = []
    for i in range(len(vals)):
        mm = m + 5 * i
        t.append(ms(day, h + mm // 60, mm % 60))
    return {"t": t, "v": vals}


def host(name, cpu=None, mem=None, swap=None, day=DAY):
    metrics = {}
    if cpu is not None:
        metrics["cpu|usage_average"] = series(day, cpu)
    if mem is not None:
        metrics["mem|usage_average"] = series(day, mem)
    if swap is not None:
        metrics["mem|swapped_average"] = series(day, swap)
    return {"name": name, "resource_id": "r-" + name.split(".")[0], "metrics": metrics}


def write_batch(drop, bid, hosts, day=DAY, collected=None, mutate=None):
    d = Path(drop) / bid
    d.mkdir(parents=True)
    files = []
    for h in hosts:
        raw = json.dumps(h, separators=(",", ":")).encode()
        fname = h["name"] + ".json"
        (d / fname).write_bytes(raw)
        files.append({"name": fname, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)})
    m = {"source": "aria_host", "batch_id": bid, "schema_version": 1, "collector_version": "aria_probe 2",
         "collected_at": collected or f"{day}T15:06:00+08:00", "time_zone": "Asia/Taipei", "range": {"from": day, "to": day},
         "scope": "esxi99-*", "data_until": f"{day}T15:05:00+08:00", "count": len(files), "files": files}
    if mutate:
        mutate(m)
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)
    qw.set_clock(lambda: AFTER_CLOSE)
    db.set_setting(c, ri.DIR_KEY, str(drop))
    db.set_setting(c, ri.ENABLED_KEY, "1")
    yield c, drop
    qw.set_clock(None)
    c.close()


def asset(c, serial, hostname, ip, api_id=None):
    c.execute("INSERT INTO hardware (asset_serial, hostname, ip, api_id) VALUES (?,?,?,?)", (serial, hostname, ip, api_id))
    c.commit()


def imp(c):
    out = ri.run_scan(c, "manual", "admin")
    return out


# ---------------------------------------------------------------- 匯入與四欄
def test_三欄只算0900到0930_沒有Current_swap也存(env):
    c, drop = env
    # 09:00..09:45 每 5 分鐘：09:30 以後的點不算 Max/Avg/Min；Current 已拿掉（GPT 審 B2：會被誤讀成 09:30 的值）
    write_batch(drop, "ariahost_a", [host("esxi99-01.example.test", cpu=[10, 40, 20, 30, 50, 60, 99, 5, 7, 8],
                                          mem=[50, 51, 52, 53, 54, 55, 56], swap=[0, 0, 1, 0, 0, 0])])
    out = imp(c)
    assert out["imported"] == 1, out
    r = c.execute("SELECT * FROM opening_peak WHERE day=? AND metric='cpu'", (DAY,)).fetchone()
    assert (r["max"], r["min"], r["points"], r["max_at"]) == (60, 10, 6, "09:25")
    assert r["avg"] == round((10 + 40 + 20 + 30 + 50 + 60) / 6, 2)
    assert "cur" not in r.keys() and "cur" not in op.rows(c, DAY)[0]
    assert c.execute("SELECT max FROM opening_peak WHERE day=? AND metric='swap'", (DAY,)).fetchone()[0] == 1
    assert c.execute("SELECT max FROM opening_peak WHERE day=? AND metric='mem'", (DAY,)).fetchone()[0] == 55


def test_Aria沒值的點跳過不補零_別天的點不算(env):
    c, drop = env
    h = host("esxi99-01.example.test", cpu=[10, None, 30])
    h["metrics"]["cpu|usage_average"]["t"].append(ms("2026-11-03", 9, 10))
    h["metrics"]["cpu|usage_average"]["v"].append(99)
    write_batch(drop, "ariahost_a", [h])
    assert imp(c)["imported"] == 1
    r = c.execute("SELECT * FROM opening_peak WHERE day=? AND metric='cpu'", (DAY,)).fetchone()
    assert (r["max"], r["min"], r["points"]) == (30, 10, 2)
    raw = [tuple(x) for x in c.execute("SELECT ts, value FROM opening_point WHERE metric='cpu' ORDER BY ts")]
    assert raw == [(ms(DAY, 9, 0), 10.0), (ms(DAY, 9, 5), None), (ms(DAY, 9, 10), 30.0)]   # 缺值原樣存 null，別天的點不收


@pytest.mark.parametrize("bad,code", [
    (lambda m: m["range"].update(to="2026-11-05"), "manifest_invalid"),
    (lambda m: m.update(collected_at="2099-01-01T00:00:00+08:00"), "manifest_invalid"),
])
def test_manifest不對整批拒收(env, bad, code):
    c, drop = env
    write_batch(drop, "ariahost_bad", [host("esxi99-01.example.test", cpu=[1])], mutate=bad)
    out = imp(c)
    assert out["rejected"] == 1 and out["imported"] == 0, out
    assert c.execute("SELECT reason_code FROM relay_import_batches").fetchone()[0] == code
    assert not op._has(c, "opening_peak") or c.execute("SELECT COUNT(*) FROM opening_peak").fetchone()[0] == 0


def test_資料檔壞了或同一台出現兩次_整批拒收(env):
    c, drop = env
    h = host("esxi99-01.example.test", cpu=[1, 2])
    h["metrics"]["cpu|usage_average"]["v"].pop()
    write_batch(drop, "ariahost_b1", [h])
    a = host("esxi99-02.example.test", cpu=[1])
    b = dict(host("esxi99-02.example.test", cpu=[2]))
    b["name"] = "ESXI99-02.example.test"
    write_batch(drop, "ariahost_b2", [a, b])
    out = imp(c)
    assert out["rejected"] == 2 and out["imported"] == 0, out


def test_同一天新批次整天換掉_舊批次不蓋新的(env):
    c, drop = env
    write_batch(drop, "ariahost_new", [host("esxi99-01.example.test", cpu=[70])], collected=f"{DAY}T15:20:00+08:00")
    assert imp(c)["imported"] == 1
    write_batch(drop, "ariahost_old", [host("esxi99-01.example.test", cpu=[10]), host("esxi99-02.example.test", cpu=[20])],
                collected=f"{DAY}T15:06:00+08:00")
    assert imp(c)["imported"] == 1
    assert [tuple(r) for r in c.execute("SELECT host, max FROM opening_peak WHERE metric='cpu'")] == [("esxi99-01.example.test", 70)]
    write_batch(drop, "ariahost_newer", [host("esxi99-03.example.test", cpu=[33])], collected=f"{DAY}T16:00:00+08:00")
    assert imp(c)["imported"] == 1
    assert [tuple(r) for r in c.execute("SELECT host, max FROM opening_peak WHERE metric='cpu'")] == [("esxi99-03.example.test", 33)]


# ---------------------------------------------------------------- 資產對應（machine_key，不猜）
def test_主機對資產_去網域比主機名_對到多台或沒有就列未匹配(env):
    c, drop = env
    asset(c, "S1", "ESXI99-01", "10.99.0.1", "AP01")
    asset(c, "S2", "esxi99-02.example.test", "10.99.0.2")
    asset(c, "S3", "esxi99-02", "10.99.0.3")                 # 同名、不同 IP（現行口徑＝兩台）→ 對到多台
    write_batch(drop, "ariahost_a", [host("esxi99-01.example.test", cpu=[10]), host("esxi99-02.example.test", cpu=[20]),
                                     host("esxi99-03.example.test", cpu=[30])])
    assert imp(c)["imported"] == 1
    m = {r["host"]: (r["match"], r["machine_key"]) for r in c.execute("SELECT * FROM opening_peak WHERE metric='cpu'")}
    import system_stats
    assert m["esxi99-01.example.test"] == ("matched", system_stats.machine_key("ESXI99-01", "10.99.0.1", "S1"))
    assert m["esxi99-02.example.test"][0] == "ambiguous" and m["esxi99-02.example.test"][1] is None
    assert m["esxi99-03.example.test"] == ("no_asset", None)
    um = {r["ident"]: r["reason"] for r in c.execute("SELECT * FROM relay_import_unmatched WHERE source='aria_host'")}
    assert um == {"esxi99-02.example.test": "ambiguous", "esxi99-03.example.test": "no_asset"}
    # 之後資產補登了 → 下一批對得到，未匹配清單移除
    asset(c, "S4", "esxi99-03", "10.99.0.4")
    write_batch(drop, "ariahost_b", [host("esxi99-03.example.test", cpu=[30])], collected=f"{DAY}T16:00:00+08:00")
    assert imp(c)["imported"] == 1
    assert "esxi99-03.example.test" not in {r["ident"] for r in c.execute("SELECT ident FROM relay_import_unmatched")}


def test_AP聯絡五欄_ESXi沒掛APID照實寫未提供(env):
    c, drop = env
    asset(c, "S1", "esxi99-01", "10.99.0.1")
    write_batch(drop, "ariahost_a", [host("esxi99-01.example.test", cpu=[10]), host("esxi99-09.example.test", cpu=[20])])
    imp(c)
    rs = op.attach_contacts(c, op.rows(c, DAY))
    for r in rs:
        assert all(r[k] == "未提供" for k in ("c_ap_team", "c_unit", "c_system_name", "c_sys_class", "c_email"))
        assert r["c_note"]


# ---------------------------------------------------------------- 首頁右欄
def test_首頁_今天檔案還沒到寫尚未匯入不放數字(env):
    c, _drop = env
    s = op.summary(c, MORNING)
    assert s["status"] == "pending" and s["label"] == op.PENDING_LABEL and "top" not in s and "covered" not in s


def test_首頁_週末寫休市(env):
    c, _drop = env
    assert op.summary(c, datetime(2026, 11, 7, 16, 0))["status"] == "holiday"


def test_首頁_Top10依CPU_Max_涵蓋應有_沒資料排最後_台數等於下鑽筆數(env):
    c, drop = env
    hs = [host(f"esxi99-{i:02d}.example.test", cpu=[i * 5.0, i * 4.0]) for i in range(1, 13)]
    hs.append(host("esxi99-99.example.test"))                   # 範圍內但 Aria 沒回值
    write_batch(drop, "ariahost_a", hs)
    assert imp(c)["imported"] == 1
    s = v2_today.today(c, AFTER_CLOSE, record=False)["opening"]
    assert s["status"] == "ok" and s["as_of"] == "15:05" and s["resolution"] == "5 分鐘一點"
    assert (s["expected"], s["covered"], s["unmatched"]) == (13, 12, 13)
    top = [r["host"] for r in s["top"]["rows"]]
    assert top == [f"esxi99-{i:02d}.example.test" for i in range(12, 2, -1)]
    alls = op.rows(c, DAY)
    assert s["top"]["total"] == len(alls) == 13 and alls[-1]["host"] == "esxi99-99.example.test" and not alls[-1]["has_data"]
    assert [r["__no"] for r in alls] == list(range(1, 14))
    assert all("c_ap_team" in r for r in s["top"]["rows"])
    assert s["baseline_days"] == 0 and "不足 20 日" in s["baseline_label"]


def test_近20工作日基準_不足20天照實標天數_最多取20天(env):
    c, drop = env
    d0 = datetime(2026, 9, 28)
    days = []
    d = d0
    while len(days) < 23:
        if d.weekday() < 5:
            days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    assert days[-1] < DAY
    for i, dd in enumerate(days[:3]):
        write_batch(drop, f"ariahost_{dd}", [host("esxi99-01.example.test", cpu=[10.0 * (i + 1)], day=dd)], day=dd, collected=f"{dd}T15:06:00+08:00")
    write_batch(drop, "ariahost_today", [host("esxi99-01.example.test", cpu=[50.0])])
    imp(c)
    r = op.rows(c, DAY)[0]
    assert (r["baseline"], r["baseline_days"], r["delta"]) == (20.0, 3, 30.0) and "不足 20 日" in r["baseline_text"]
    for dd in days[3:]:
        write_batch(drop, f"ariahost_{dd}", [host("esxi99-01.example.test", cpu=[10.0], day=dd)], day=dd, collected=f"{dd}T15:06:00+08:00")
    imp(c)
    r = op.rows(c, DAY)[0]
    assert r["baseline_days"] == 20 and "不足" not in r["baseline_text"]
    assert op.summary(c, AFTER_CLOSE)["baseline_label"] == "近 20 個工作日"


def test_verify_numbers_開盤尖峰不變式全過(env):
    c, drop = env
    asset(c, "S1", "esxi99-01", "10.99.0.1")
    write_batch(drop, "ariahost_a", [host(f"esxi99-{i:02d}.example.test", cpu=[float(i)]) for i in range(1, 15)] + [host("esxi99-77.example.test")])
    imp(c)
    import verify_numbers as vn
    before = vn.FAILS
    n0 = len(vn.RESULTS)
    vn.check_opening_peak(c)
    got = vn.RESULTS[n0:]
    assert vn.FAILS == before and len(got) >= 7 and all(x["ok"] for x in got), got


def test_首頁與API只讀_沒有批次時不建表(env):
    c, _drop = env
    op.summary(c, AFTER_CLOSE)
    op.rows(c, DAY)
    assert not op._has(c, "opening_peak")


# ---------------------------------------------------------------- 跳板機腳本（AI/tools_relay/aria_probe.ps1）
def test_跳板機腳本_ASCII_只GET加登入_不用Bypass或base64_manifest最後寫():
    p = ROOT / "AI" / "tools_relay" / "aria_probe.ps1"
    if not p.exists():
        pytest.skip("公開 relay 沒有 AI/")
    raw = p.read_bytes()
    assert all(b < 128 for b in raw), "aria_probe.ps1 要全部 ASCII（PowerShell 5.1 讀無 BOM 的檔會亂碼）"
    s = raw.decode()
    low = s.lower()
    assert "bypass" not in low and "base64" not in low and "-encodedcommand" not in low
    posts = re.findall(r"-Method\s+(\w+)", s)
    assert posts == ["Post"] and "auth/token/acquire" in s
    assert "resourceKind=HostSystem&pageSize=" in s and "mem|swapped_average" in s and "ariahost_" in s
    assert "source = 'aria_host'" in s
    assert s.index("'manifest.json'") > s.index("foreach ($o in @($out))"), "manifest.json 要在每台的檔都寫完之後才寫"


# ================================================================ 2.045.1（GPT 審 c8bad905 的修正，每條一個會紅的測試）
def vc_vm(c, esxi, vm_host=None, ip=None, api_id=None, serial=None):
    """vCenter／RVTools 匯入的一筆 VM（source_record source='vcenter'，Host 欄＝esxi）；serial 有給就建資產並回填 resolved_hardware_id。"""
    hid = None
    if serial:
        hid = c.execute("INSERT INTO hardware (asset_serial, hostname, ip, api_id, is_vm) VALUES (?,?,?,?,1)", (serial, vm_host, ip, api_id)).lastrowid
    c.execute("INSERT INTO source_record (source, source_key, payload, resolved_status, resolved_hardware_id) VALUES ('vcenter', ?, ?, 'matched', ?)",
              (serial or vm_host or esxi, json.dumps({"vm_name": vm_host, "hostname": vm_host, "ip": ip, "esxi_host": esxi}), hid))
    c.commit()


def test_A1_JSON裡的null缺值不算點_不會被當成0拉低Min(env):
    c, drop = env
    write_batch(drop, "ariahost_a", [host("esxi99-01.example.test", cpu=[40, None, None, 50])])
    imp(c)
    r = op.rows(c, DAY)[0]
    assert (r["min"], r["points"], r["avg"]) == (40, 2, 45)


PS_FRAGMENT_RUNNER = r"""
$src = Get-Content -Raw '{script}'
$a = $src.IndexOf('# ---- relay batch'); $b = $src.IndexOf('# ---- console report')
$frag = $src.Substring($a, $b - $a)
$ScriptVersion='aria_probe test'; $NameLike='esxi99-*'; $Day='{day}'; $tz8=New-TimeSpan -Hours 8; $listedTotal=2
$OutDir='{out}'
$keys=@('cpu|usage_average','mem|usage_average','mem|swapped_average')
$b0={b0}
$st1 = [pscustomobject]@{{ values=@([pscustomobject]@{{ 'stat-list'=[pscustomobject]@{{ stat=@(
  [pscustomobject]@{{ statKey=[pscustomobject]@{{key='cpu|usage_average'}}; timestamps=@($b0,($b0+300000),($b0+600000)); data=@(12.5,$null,40.25) }}) }} }}) }}
$out=@(@{{name='esxi99-01.example.test'; id='abc'; stats=$st1}}, @{{name='esxi99-02.example.test'; id='def'; stats=$st1}})
$hosts=$out; $e2=$b0+3600000; $script:fetchFailed=0
{extra}
Invoke-Expression $frag
"""


def _run_ps(tmp_path, extra=""):
    import shutil
    import subprocess
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("這台沒有 PowerShell（只在 Windows 開發機跑）")
    script = ROOT / "AI" / "tools_relay" / "aria_probe.ps1"
    if not script.exists():
        pytest.skip("公開 relay 沒有 AI/")
    out = tmp_path / "psout"
    code = PS_FRAGMENT_RUNNER.format(script=str(script), day=DAY, out=str(out), b0=ms(DAY, 9, 0), extra=extra)
    r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", code], capture_output=True, timeout=120)
    batches = [d for d in out.iterdir()] if out.exists() else []
    return r, batches


def test_A1_跳板機腳本實跑_缺值保留成null_批次匯得進來(env, tmp_path):
    c, drop = env
    r, batches = _run_ps(tmp_path)
    assert len(batches) == 1 and (batches[0] / "manifest.json").exists(), r.stdout.decode(errors="replace") + r.stderr.decode(errors="replace")
    d = json.loads((batches[0] / "esxi99-01.example.test.json").read_text(encoding="utf-8"))
    assert d["metrics"]["cpu|usage_average"]["v"] == [12.5, None, 40.25]
    import shutil
    shutil.copytree(batches[0], Path(drop) / batches[0].name)
    assert imp(c)["imported"] == 1
    assert (op.rows(c, DAY)[0]["min"], op.rows(c, DAY)[0]["points"]) == (12.5, 2)


def test_A5_跳板機腳本實跑_寫檔失敗就不寫manifest(tmp_path):
    fail = ("function Get-FileHash { param([string]$LiteralPath, [string]$Algorithm) if ($LiteralPath -like '*esxi99-02*') { Write-Error 'disk gone'; return [pscustomobject]@{ Hash = '00' } } "
            "else { Microsoft.PowerShell.Utility\\Get-FileHash -LiteralPath $LiteralPath -Algorithm $Algorithm } }")
    r, batches = _run_ps(tmp_path, fail)
    assert len(batches) == 1 and not (batches[0] / "manifest.json").exists(), r.stdout.decode(errors="replace")
    assert b"no manifest" in r.stdout.lower()


def test_A5_跳板機腳本實跑_有主機讀取失敗也不寫manifest(tmp_path):
    r, batches = _run_ps(tmp_path, "$script:fetchFailed=1")
    assert all(not (b / "manifest.json").exists() for b in batches), r.stdout.decode(errors="replace")
    assert b"failed to read" in r.stdout


def test_A1_A5_B3_跳板機腳本靜態檢查():
    p = ROOT / "AI" / "tools_relay" / "aria_probe.ps1"
    if not p.exists():
        pytest.skip("公開 relay 沒有 AI/")
    s = p.read_text(encoding="ascii")
    assert "[double]$_" not in s, "不可以用 [double] 轉整串（$null 會變成 0）"
    assert "ReadToEnd" not in s, "登入失敗不記伺服器回應本文，只記狀態碼"
    for line in s.splitlines():
        t = line.strip()
        if t.startswith('"') or t.startswith("'"):
            assert "$u" not in t and "$src" not in t and "$s2" not in t, f"Transcript 不記帳號／來源：{t}"
    i_stop = s.index("$ErrorActionPreference = 'Stop'")
    assert i_stop < s.index("foreach ($o in @($out))") < s.index("'manifest.json'")
    assert "page=$pg&pageSize=$PageSize" in s and "totalCount" in s, "主機清單要翻頁並跟 totalCount 對"


def test_A2_verify獨立重算_畫面數字被改就紅(env):
    c, drop = env
    write_batch(drop, "ariahost_a", [host(f"esxi99-{i:02d}.example.test", cpu=[float(i), float(i) + 1]) for i in range(1, 13)])
    imp(c)
    import verify_numbers as vn

    def run():
        n0, f0 = len(vn.RESULTS), vn.FAILS
        vn.check_opening_peak(c)
        return vn.FAILS - f0, vn.RESULTS[n0:]

    bad, got = run()
    assert bad == 0 and all(x["ok"] for x in got), got
    c.execute("UPDATE opening_peak SET max = 99 WHERE host = 'esxi99-01.example.test' AND metric = 'cpu'")   # 彙總表被算錯
    bad, got = run()
    assert bad >= 2, got                                   # 三欄不符＋Top10 排序不符
    names = {x["name"] for x in got if not x["ok"]}
    assert any("原始點獨立重算" in n for n in names) and any("獨立排序" in n for n in names)


def test_A3_資產名有實測校正_用校正後的名字對(env, monkeypatch):
    c, drop = env
    import machine_identity
    asset(c, "S1", "esxi99-o1-typo", "10.99.0.1")
    monkeypatch.setitem(machine_identity._STATE, "host_fix", {"S1": "esxi99-01"})
    write_batch(drop, "ariahost_a", [host("esxi99-01.example.test", cpu=[10])])
    imp(c)
    r = op.rows(c, DAY)[0]
    import system_stats
    assert r["match"] == "matched" and r["machine_key"] == system_stats.machine_key("esxi99-o1-typo", "10.99.0.1", "S1")


@pytest.mark.parametrize("order", [("ariahost_x1", "ariahost_x2"), ("ariahost_x2", "ariahost_x1")])
def test_A4_收集時間相同_batch_id較大的勝_不看處理順序_留警告(env, order):
    c, drop = env
    vals = {"ariahost_x1": 11.0, "ariahost_x2": 22.0}
    same = f"{DAY}T15:06:00+08:00"
    for bid in order:
        write_batch(drop, bid, [host("esxi99-01.example.test", cpu=[vals[bid]])], collected=same)
        assert imp(c)["imported"] == 1
    assert op.rows(c, DAY)[0]["max"] == 22.0
    s = op.summary(c, AFTER_CLOSE)
    assert s["batch_id"] == "ariahost_x2" and s["note"] and "ariahost_x2" in s["note"]


def test_B3_應有用資產庫同範圍ESXi_列出這批沒有的(env):
    c, drop = env
    for i in (1, 2, 3):
        vc_vm(c, f"esxi99-0{i}.example.test", vm_host=f"vm{i}")
    vc_vm(c, "esxi88-01.example.test", vm_host="vmx")           # 範圍外
    write_batch(drop, "ariahost_a", [host("esxi99-01.example.test", cpu=[10]), host("esxi99-02.example.test")])
    imp(c)
    s = op.summary(c, AFTER_CLOSE)
    assert (s["expected_basis"], s["expected"], s["listed"], s["covered"]) == ("asset", 3, 2, 1)
    assert s["missing"] == ["esxi99-03"] and "資產庫" in s["expected_label"]


def test_B3_沒有資產基準_照實寫本批列到幾台未核對資產(env):
    c, drop = env
    write_batch(drop, "ariahost_a", [host("esxi99-01.example.test", cpu=[10])])
    imp(c)
    s = op.summary(c, AFTER_CLOSE)
    assert s["expected_basis"] == "batch" and s["expected_label"] == "應有＝本批列到 1 台（未核對資產）"


def test_B4_每台ESXi帶出上面的VM與業務系統_AP聯絡照VM的APID查(env):
    c, drop = env
    vc_vm(c, "esxi99-01.example.test", vm_host="app01", ip="10.99.1.1", api_id="AP01", serial="V1")
    vc_vm(c, "ESXI99-01", vm_host="app02", ip="10.99.1.2", api_id="AP02", serial="V2")
    vc_vm(c, "esxi99-02.example.test", vm_host="db01", ip="10.99.1.3", serial="V3")
    write_batch(drop, "ariahost_a", [host("esxi99-01.example.test", cpu=[10]), host("esxi99-02.example.test", cpu=[5])])
    imp(c)
    rs = op.attach_contacts(c, op.rows(c, DAY))
    a = next(r for r in rs if r["host"].startswith("esxi99-01"))
    assert a["vm_count"] == 2 and [v["vm"] for v in a["vms"]] == ["app01", "app02"] and a["vms"][0]["link"]
    assert a["c_api_ids"] == ["AP01", "AP02"]
    b = next(r for r in rs if r["host"].startswith("esxi99-02"))
    assert b["vm_count"] == 1 and b["c_api_ids"] == [] and b["c_ap_team"] == "未提供" and "VM" in b["c_note"]


def test_舊版表少欄位_ensure_會補上():
    import sqlite3
    import opening_peak as op
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE opening_peak_batch (day TEXT PRIMARY KEY, batch_id TEXT NOT NULL, collected_at TEXT NOT NULL,"
              " data_until TEXT, scope TEXT, hosts INTEGER NOT NULL, imported_at TEXT NOT NULL)")
    op.ensure(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(opening_peak_batch)")}
    assert {"listed_total", "note"} <= cols


def test_vcenter_只取最近一輪匯入():
    import json
    import sqlite3
    import opening_peak as op
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE source_record (id INTEGER PRIMARY KEY, source TEXT, source_key TEXT, payload TEXT,"
              " collected_at TEXT, resolved_hardware_id INTEGER)")
    c.execute("INSERT INTO source_record (source, source_key, payload, collected_at) VALUES ('vcenter','a',?, '2026-11-04 10:00:00')",
              (json.dumps({"esxi_host": "esxi169-01"}),))
    c.execute("INSERT INTO source_record (source, source_key, payload, collected_at) VALUES ('vcenter','b',?, '2026-10-01 10:00:00')",
              (json.dumps({"esxi_host": "esxi169-99"}),))
    assert op.expected_hosts(c, "esxi169-*") == {"esxi169-01"}
