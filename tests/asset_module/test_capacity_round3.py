"""ACT-04 容量分析第三輪審查修正（2.041.41）：格子不混 kind／來源／資產、偏好來源要先驗證可用、逐日間隔、歸屬日裁切、時區白名單、
內容級快取指紋、verify 不把『略過』當通過、資料與稽核同交易、錯誤不外洩、wu_refresh 沒有任何觸發點。全部假資料。"""
import gzip
import json
import re
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import capacity_analysis as ca  # noqa: E402
import nmon_series  # noqa: E402
import verify_numbers as vn  # noqa: E402


LEGACY_SLOTS = [  # 這些既有測試是用舊的六個範例時段寫的；真正的預設時段另有 test_capacity_defaults.py 測
    {"key": "pre_open", "label": "開盤前", "start": "08:30", "end": "09:00"},
    {"key": "open", "label": "開盤", "start": "09:00", "end": "09:30"},
    {"key": "early", "label": "早盤", "start": "09:30", "end": "11:00"},
    {"key": "mid", "label": "盤中", "start": "11:00", "end": "13:00"},
    {"key": "close", "label": "收盤", "start": "13:00", "end": "13:35"},
    {"key": "batch", "label": "盤後批次", "start": "18:00", "end": "23:00"},
]


@pytest.fixture(autouse=True)
def _legacy_slots(monkeypatch):
    monkeypatch.setattr(ca, "DEFAULT_SLOTS", [dict(s) for s in LEGACY_SLOTS])


def mem_conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(nmon_series.SERIES_SQL)
    c.execute(ca.WU_TABLE_SQL)
    return c


def put(c, table, host, day, fn=lambda m: 10.0, itv=60, start=0, end=1440, extra=None, **meta):
    step = max(1, itv // 60)
    ts = [f"{m // 60:02d}:{m % 60:02d}:00" for m in range(start, end, step)]
    row = {"ts": ts, "cols": {"cpu_busy": [fn(m) for m in range(start, end, step)]}, "top": {}}
    if extra:
        row["cols"].update(extra(len(ts)))
    row.update(meta)
    c.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?,?)", (host, day, itv, len(ts), gzip.compress(json.dumps(row).encode()), "2026-09-30 00:00:00"))


def weekdays(start, n):
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def cells(h, **kw):
    return [x for x in h["cells"] if all(x.get(k) == v for k, v in kw.items())]


# ---------------- 1 同一格不混 kind；缺 kind 要有限制
def test_raw與bucket不混同一格_缺kind列限制():
    c = mem_conn()
    ca.save_config(c, {"params": {"min_days": 1}})
    put(c, "nmon_series", "K", "2026-09-07", lambda m: 10.0, itv=60, kind="raw")
    row = {"kind": "bucket", "interval_s": 300, "ts": [f"{h // 60:02d}:{h % 60:02d}:00" for h in range(0, 1440, 5)],
           "cols": {"cpu_busy": [50.0] * 288}, "max": {"cpu_busy": [99.0] * 288}, "cnt": {"cpu_busy": [5] * 288}}
    c.execute("INSERT INTO nmon_series VALUES (?,?,?,?,?,?)", ("K", "2026-09-08", 300, 288, gzip.compress(json.dumps(row).encode()), "x"))
    h = ca.analyze_host(c, "K", ca.get_config(c))
    r, b = cells(h, kind="raw", slot="early", metric="cpu_busy"), cells(h, kind="bucket", slot="early", metric="cpu_busy")
    assert len(r) == 1 and len(b) == 1 and r[0]["p95"] == 10.0 and b[0]["p95"] is None and b[0]["max"] == 99.0
    assert r[0]["samples"] == 90 and b[0]["samples"] == 18 * 5          # 各自的樣本，沒有互相摻
    put(c, "nmon_series", "U", "2026-09-07")                              # 沒標 kind
    assert any(l["code"] == "kind_unmarked" for l in ca.analyze_host(c, "U", ca.get_config(c))["limits"])


# ---------------- 2 bucket 缺 cnt；disk_busy 缺碟
def test_bucket缺筆數_不平均各桶avg_只留最大值():
    c = mem_conn()
    ca.save_config(c, {"params": {"min_days": 1}})
    ts = [f"{9 + m // 60:02d}:{m % 60:02d}:00" for m in range(30, 120, 5)]
    n = len(ts)
    row = {"kind": "bucket", "interval_s": 300, "ts": ts, "cols": {"cpu_busy": [10.0] * n}, "max": {"cpu_busy": [40.0] * n}}
    c.execute("INSERT INTO capacity_whatsup_perf VALUES ('B','2026-09-07',300,?,?,'x')", (n, gzip.compress(json.dumps(row).encode())))
    e = cells(ca.analyze_host(c, "B", ca.get_config(c)), slot="early", metric="cpu_busy")[0]
    assert e["status"] == "no_count" and e["avg"] is None and e["p95"] is None and e["max"] == 40.0


def test_磁碟忙碌缺任一碟_視為不完整_不取剩餘磁碟的最大值():
    c = mem_conn()
    for d in weekdays(date(2026, 8, 3), 25):
        put(c, "nmon_series", "D", d, kind="raw", extra=lambda n: {"disk:d0:busy": [20.0] * n, "disk:d1:busy": [90.0] * 10 + [None] * (n - 10)})
    h = ca.analyze_host(c, "D", ca.get_config(c))
    assert any(l["code"] == "partial_missing" for l in h["limits"])
    # 只有前 10 個取樣點（00:00–00:09，屬『其他時間』）兩顆都有；其餘缺 d1 → 排除，不得拿 d0 的 20 冒充
    early = cells(h, slot="early", metric="disk_busy_max", day_type="normal")[0]
    assert early["samples"] == 0 and early["status"] == "no_data"


# ---------------- 3 偏好來源要先驗證可用
@pytest.mark.parametrize("bad", ["corrupt", "empty", "lowcov"])
def test_偏好來源某天壞_空_低覆蓋_改用NMON_不丟掉NMON(bad):
    c = mem_conn()
    ca.save_config(c, {"params": {"min_days": 1}})
    days = weekdays(date(2026, 9, 7), 3)
    for d in days:
        put(c, "nmon_series", "S", d, lambda m: 10.0, kind="raw")
        put(c, "capacity_whatsup_perf", "S", d, lambda m: 99.0, kind="raw")
    if bad == "corrupt":
        c.execute("UPDATE capacity_whatsup_perf SET data = ? WHERE day = ?", (b"not-gzip", days[1]))
    elif bad == "empty":
        c.execute("UPDATE capacity_whatsup_perf SET data = ? WHERE day = ?", (gzip.compress(json.dumps({"ts": [], "cols": {}}).encode()), days[1]))
    else:
        put(c, "capacity_whatsup_perf", "S", days[1], lambda m: 99.0, start=0, end=60, kind="raw")
    h = ca.analyze_host(c, "S", ca.get_config(c))
    w, n = cells(h, source="whatsup", slot="early", metric="cpu_busy")[0], cells(h, source="nmon", slot="early", metric="cpu_busy")[0]
    assert w["days"] == 2 and n["days"] == 1 and n["avg"] == 10.0 and w["avg"] == 99.0         # 壞的那天改用 NMON
    assert h["data_days"] == 3 and any(l["code"] == "multi_source" for l in h["limits"])


# ---------------- 4 取樣間隔逐日
def test_間隔逐日保留_期望樣本數用當天實際間隔():
    c = mem_conn()
    ca.save_config(c, {"params": {"min_days": 1}})
    put(c, "nmon_series", "I", "2026-09-07", itv=60, kind="raw")
    put(c, "nmon_series", "I", "2026-09-08", itv=300, kind="raw")
    e = cells(ca.analyze_host(c, "I", ca.get_config(c)), slot="early", metric="cpu_busy", day_type="normal")[0]
    assert e["expected"] == 90 + 18 and e["samples"] == 90 + 18         # 不是 2 天 × 全期最短間隔 60 秒＝180


# ---------------- 5 跨午夜日期裁切用歸屬日
def test_跨午夜裁切用歸屬日():
    c = mem_conn()
    ca.save_config(c, {"slots": [{"key": "n", "label": "夜間", "start": "22:00", "end": "02:00"}]})
    put(c, "nmon_series", "W", "2026-11-02", lambda m: 60.0, start=0, end=120, kind="raw")     # 11/02 00:00–02:00 屬 11/01 的時段
    assert ca.analyze_host(c, "W", ca.get_config(c), "2026-11-01", "2026-11-01")["total_ts"] == 120      # to=11/01 要含
    assert ca.analyze_host(c, "W", ca.get_config(c), "2026-11-02", "2026-11-02")["total_ts"] == 0        # from=11/02 要不含


# ---------------- 6 時區白名單
@pytest.mark.parametrize("tz,ok", [("", True), ("UTC", True), ("utc", True), ("Asia/Taipei", True), ("Z", False), ("+00:00", False), ("America/New_York", False)])
def test_tz白名單(tz, ok):
    c = mem_conn()
    put(c, "nmon_series", "T", "2026-09-07", kind="raw", tz=tz)
    h = ca.analyze_host(c, "T", ca.get_config(c))
    assert (h["total_ts"] > 0) is ok and (not ok) == any(l["code"] == "bad_tz" for l in h["limits"])


# ---------------- 7 資產分段：用該日實際來源查對應，兩組數字
def test_資產分段_每日用當天採用的來源查對應_統計分開():
    c = mem_conn()
    ca.save_config(c, {"params": {"min_days": 1}})
    c.execute("CREATE TABLE hardware (asset_serial TEXT, hostname TEXT, ip TEXT, environment TEXT, asset_purpose TEXT)")
    c.execute("INSERT INTO hardware VALUES ('SN1','a','','正式','x'),('SN2','b','','DR','備援')")
    ca.save_mapping(c, "nmon", "M", "SN1", "2026-01-01")
    ca.save_mapping(c, "whatsup", "M", "SN2", "2026-01-01")
    d1, d2, d3 = weekdays(date(2026, 9, 7), 3)
    put(c, "nmon_series", "M", d1, lambda m: 10.0, kind="raw")
    put(c, "nmon_series", "M", d2, lambda m: 10.0, kind="raw")
    put(c, "capacity_whatsup_perf", "M", d3, lambda m: 70.0, kind="raw")
    h = ca.analyze_host(c, "M", ca.get_config(c))
    segs = h["context"]["segments"]
    assert [(s["from"], s["to"], s["asset_serial"]) for s in segs] == [(d1, d2, "SN1"), (d3, d3, "SN2")]
    a1 = cells(h, asset_serial="SN1", slot="early", metric="cpu_busy")[0]
    a2 = cells(h, asset_serial="SN2", slot="early", metric="cpu_busy")[0]
    assert a1["avg"] == 10.0 and a1["days"] == 2 and a2["avg"] == 70.0 and a2["days"] == 1          # 兩組數字，沒有混成一組
    assert h["context"]["standby"] is True and sum(s["samples"] for s in segs) == sum(x["samples"] for x in h["cells"] if x["metric"] == "cpu_busy")
    o = ca.overview(c, host="M")["hosts"][0]
    assert [x["samples"] for x in o["cells"]] == [x["samples"] for x in h["cells"]]              # 總覽與單機一致


# ---------------- 8 內容級快取指紋
def test_快取指紋是內容級_同長度改值也失效():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = sqlite3.connect(Path(tmp) / "t.db")
        c.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute(nmon_series.SERIES_SQL)
        b1 = gzip.compress(json.dumps({"ts": ["09:30:00"], "cols": {"cpu_busy": [10.0]}}).encode())
        b2 = gzip.compress(json.dumps({"ts": ["09:30:00"], "cols": {"cpu_busy": [20.0]}}).encode())
        assert len(b1) == len(b2) and b1 != b2
        c.execute("INSERT INTO nmon_series VALUES ('F','2026-09-07',60,1,?,'t')", (b1,))
        c.commit()
        cfg = ca.get_config(c)
        a1 = ca.analyze_cached(c, "F", cfg)
        assert max(x["max"] or 0 for x in a1["cells"]) == 10.0
        c.execute("UPDATE nmon_series SET data = ? WHERE host = 'F'", (b2,))        # 同長度、同 updated_at、同 samples
        c.commit()
        a2 = ca.analyze_cached(c, "F", cfg)
        assert a2 is not a1 and max(x["max"] or 0 for x in a2["cells"]) == 20.0
        c.close()


# ---------------- 9 verify：未獨立驗證要顯示、平均竄改會紅、輪替、隔離
def _nmon_db(n_hosts=1, days=8):
    c = mem_conn()
    for h in range(n_hosts):
        for d in weekdays(date(2026, 9, 7), days):
            put(c, "nmon_series", f"V{h:02d}", d, lambda m: 80.0 if 9 * 60 + 30 <= m < 11 * 60 else 5.0, kind="raw")
    return c


def _verify(c, strict=True, **kw):
    if strict:
        c.execute("INSERT OR REPLACE INTO app_settings VALUES ('verify_strict_capacity', '1')")
    vn.FAILS = vn.CHECKS = 0
    vn.RESULTS.clear()
    vn.check_capacity(c, **kw)
    out = (vn.FAILS, [r for r in vn.RESULTS if r["ok"] is None])
    vn.FAILS = vn.CHECKS = 0
    return out


def test_verify_多來源_UTC_不是略過等於通過_而是未獨立驗證警示():
    c = _nmon_db(0)
    put(c, "nmon_series", "MS", "2026-09-07", kind="raw")
    put(c, "capacity_whatsup_perf", "MS", "2026-09-08", kind="raw")
    put(c, "nmon_series", "UT", "2026-09-07", kind="raw", tz="UTC")
    fails, warns = _verify(c)
    names = " ".join(w["name"] for w in warns)
    assert fails == 0 and "MS：未獨立驗證" in names and "UT：未獨立驗證" in names


def test_verify_平均被整體乘0點5仍會紅(monkeypatch):
    import copy
    c = _nmon_db(1, 25)
    real = ca.analyze_cached

    def bad(conn, h, cfg, *a, **k):
        r = copy.deepcopy(real(conn, h, cfg, *a, **k))
        for x in r["cells"]:
            for f in ("avg", "p95", "max"):
                if x[f] is not None:
                    x[f] = round(x[f] * 0.5, 2)
        return r
    monkeypatch.setattr(ca, "analyze_cached", bad)
    assert _verify(c)[0] >= 1


def test_verify_抽樣依日期輪替_壞資料與逾時隔離為該台失敗(monkeypatch):
    c = _nmon_db(12, 2)
    seen = []
    real = ca.analyze_cached
    monkeypatch.setattr(ca, "analyze_cached", lambda conn, h, cfg, *a, **k: (seen.append(h), real(conn, h, cfg, *a, **k))[1])
    _verify(c, max_hosts=3, today=date(2026, 9, 1))
    first = list(seen)
    seen.clear()
    _verify(c, max_hosts=3, today=date(2026, 9, 2))
    assert len(first) == 3 and seen != first                      # 不是永遠前 3 台
    c.execute("INSERT INTO nmon_series VALUES ('BADGZ','2026-09-07',60,1,?,NULL)", (b"not-gzip",))
    fails, _ = _verify(c, max_hosts=20)
    assert fails == 0                                              # 壞 gzip 的那台被隔離，其他台照常檢查完
    monkeypatch.setattr(vn, "independent_capacity", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    fails, _ = _verify(c, max_hosts=3)
    assert fails >= 1                                              # 單台檢查自己出錯 → 記為該台失敗，沒有中止
    monkeypatch.undo()
    assert _verify(_nmon_db(1, 3), host_budget_s=-1)[0] >= 1       # 單台逾時 → 該台失敗


def test_verify預設是觀察模式_不一致只出警示_不使部署失敗_strict才失敗(monkeypatch):
    import copy
    c = _nmon_db(1, 25)
    real = ca.analyze_cached

    def bad(conn, h, cfg, *a, **k):
        r = copy.deepcopy(real(conn, h, cfg, *a, **k))
        for x in r["cells"]:
            if x["avg"] is not None:
                x["avg"] = round(x["avg"] * 0.5, 2)
        return r
    monkeypatch.setattr(ca, "analyze_cached", bad)
    fails, warns = _verify(c, strict=False)
    assert fails == 0 and any("觀察模式" in w["name"] for w in warns)             # 只有 ⚠️，並計入結果摘要（ok=None），不使 verify 失敗
    c.execute("INSERT OR REPLACE INTO app_settings VALUES ('verify_strict_capacity', '0')")
    assert _verify(c, strict=False)[0] == 0                                          # 設成 0 仍是觀察模式
    assert _verify(c, strict=True)[0] >= 1                                           # verify_strict_capacity=1 才升級為會中止的 ❌


# ---------------- 10 寫入與稽核同一交易
def _api(tmp):
    import api
    import auth
    import db
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
    cl = TestClient(api.app, raise_server_exceptions=False)
    cn = db.get_connection(p)
    db.create_user(cn, "admin", auth.hash_password("test-password-123"))
    cn.close()
    assert cl.post("/api/auth/login", json={"username": "admin", "password": "test-password-123"}).status_code == 200
    return cl, p, api


def test_稽核寫不進去_資料一併回滾():
    import db
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        cl, p, api = _api(tmp)
        cn = db.get_connection(p)
        cn.execute("INSERT INTO hardware (asset_serial) VALUES ('SN')")
        cn.execute("CREATE TRIGGER no_audit BEFORE INSERT ON activity_log WHEN NEW.action LIKE 'capacity_%' BEGIN SELECT RAISE(ABORT, 'audit down'); END")
        cn.commit()
        cn.close()
        r = cl.put("/api/v2/capacity/config", json={"heavy_days": [{"date": "2026-09-09"}]})
        assert r.status_code >= 500
        r = cl.put("/api/v2/capacity/mappings", json={"source": "nmon", "device": "D", "asset_serial": "SN", "valid_from": "2026-01-01"})
        assert r.status_code >= 500
        cn = db.get_connection(p)
        assert cn.execute("SELECT COUNT(*) FROM app_settings WHERE key = ?", (ca.HEAVY_KEY,)).fetchone()[0] == 0
        have = cn.execute("SELECT name FROM sqlite_master WHERE name = 'capacity_device_map'").fetchone()
        assert (cn.execute("SELECT COUNT(*) FROM capacity_device_map").fetchone()[0] if have else 0) == 0
        cn.execute("DROP TRIGGER no_audit")
        cn.commit()
        cn.close()
        assert cl.put("/api/v2/capacity/mappings", json={"source": "nmon", "device": "D", "asset_serial": "SN", "valid_from": "2026-01-01"}).status_code == 200
        cn = db.get_connection(p)
        cn.execute("CREATE TRIGGER no_audit2 BEFORE INSERT ON activity_log WHEN NEW.action = 'capacity_mapping_end' BEGIN SELECT RAISE(ABORT, 'audit down'); END")
        cn.commit()
        cn.close()
        mid = cl.get("/api/v2/capacity/mappings").json()["mappings"][0]["id"]
        assert cl.put(f"/api/v2/capacity/mappings/{mid}/end", json={"valid_to": "2026-12-31"}).status_code >= 500
        assert cl.get("/api/v2/capacity/mappings").json()["mappings"][0]["valid_to"] is None          # 結束日沒生效
        api.app.dependency_overrides.clear()


# ---------------- 11 錯誤不外洩
def test_單台失敗只回錯誤代碼_不回例外內容(monkeypatch):
    c = mem_conn()
    put(c, "nmon_series", "E", "2026-09-07")
    monkeypatch.setattr(ca, "analyze_cached", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(r"secret C:\inner\path")))
    h = ca.overview(c)["hosts"][0]
    assert h["error"] == "ANALYSIS_ERROR" and "secret" not in json.dumps(h, ensure_ascii=False)


# ---------------- 防呆：wu_refresh 沒有任何觸發點
def test_wu_refresh沒有任何呼叫點_畫面寫明尚未啟用():
    root = ROOT / "APP" / "asset-module" / "backend"
    hits = [str(p.relative_to(root)) for p in root.rglob("*.py") if p.name != "capacity_analysis.py" and re.search(r"\bwu_refresh\s*\(", p.read_text(encoding="utf-8", errors="ignore"))]
    assert hits == [], f"wu_refresh 目前不得被排程或路由呼叫：{hits}"
    assert not list(root.rglob("*.sh")) or not any("wu_refresh" in p.read_text(encoding="utf-8", errors="ignore") for p in root.rglob("*.sh"))
    c = mem_conn()
    o = ca.overview(c)
    assert o["wu_refresh_enabled"] is False
    assert any("尚未啟用" in s["note"] for s in o["sources"] if s["key"] == "capacity_whatsup_perf")
    page = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "v2" / "capacity.vue").read_text(encoding="utf-8")
    assert "WhatsUp 自動取數尚未啟用" in page
