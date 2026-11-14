"""Codex 1083／1085／1131／1133 回歸（固定 209 驗出）：NMON 上傳交易邊界、壞列重匯、月份驗證、月報快照一致。"""
import gzip
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import capacity_analysis as ca  # noqa: E402
import db  # noqa: E402
import host_monthly  # noqa: E402
import monthly_report as mr  # noqa: E402
import nmon_series as ns  # noqa: E402
from test_nmon_series import make_aix  # noqa: E402


def _db(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _counts(c):
    return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("nmon_series", "nmon_sample", "nmon_file"))


# ---------------------------------------------------------------- 1085
def test_1085_檔案登錄寫入失敗_圖表資料一起回滾_重試成功三表一致():
    with tempfile.TemporaryDirectory() as tmp:
        c = _db(tmp)
        host_monthly.ensure(c)
        ns.ensure(c)
        c.execute("CREATE TRIGGER nofile BEFORE INSERT ON nmon_file BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
        c.commit()
        with pytest.raises(sqlite3.DatabaseError):
            host_monthly.import_nmon(c, make_aix().encode(), "x.nmon", "tester")
        assert _counts(c) == (0, 0, 0)                 # 修前 nmon_series 已先 commit 1 列
        c.execute("DROP TRIGGER nofile")
        c.commit()
        host_monthly.import_nmon(c, make_aix().encode(), "x.nmon", "tester")
        s, smp, f = _counts(c)
        assert s == 1 and smp > 0 and f == 1
        c.close()


# ---------------------------------------------------------------- 1083
def test_1083_既存列gzip截斷_有效重匯取代壞列():
    with tempfile.TemporaryDirectory() as tmp:
        c = _db(tmp)
        host_monthly.import_nmon(c, make_aix(cpu=lambda i: 2.0).encode(), "a.nmon", "tester")
        blob = c.execute("SELECT data FROM nmon_series").fetchone()[0]
        c.execute("UPDATE nmon_series SET data = ?", (blob[:-8],))
        c.commit()
        r = host_monthly.import_nmon(c, make_aix(cpu=lambda i: 56.0).encode(), "b.nmon", "tester")   # 修前 500
        assert r["samples"] > 0
        d = ns._decode(c.execute("SELECT data FROM nmon_series").fetchone()[0])
        assert set(v for v in d["cols"]["cpu_busy"] if v is not None) == {56.0}
        c.close()


def test_1083_好的既存列_重匯仍然合併不取代():
    with tempfile.TemporaryDirectory() as tmp:
        c = _db(tmp)
        host_monthly.import_nmon(c, make_aix(n=60, start_min=60).encode(), "a.nmon", "tester")
        host_monthly.import_nmon(c, make_aix(n=60, start_min=200).encode(), "b.nmon", "tester")
        assert c.execute("SELECT samples FROM nmon_series").fetchone()[0] == 120
        c.close()


def test_1083_封存分析也拒絕截斷的gzip_跟圖表一致():
    good = gzip.compress(b'{"ts": ["09:00:00"], "cols": {"cpu_busy": [5]}}')
    assert ca._decode(good)["ts"] == ["09:00:00"]
    with pytest.raises(ValueError):
        ca._decode(good[:-8])


# ---------------------------------------------------------------- 1089（第一點）
def test_1089_同主機同名原件同秒上傳_下載取後一份(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        c = _db(tmp)
        monkeypatch.setattr(host_monthly, "_now", lambda: "2026-10-01 10:00:00")
        host_monthly.import_nmon(c, make_aix(cpu=lambda i: 2.0).encode(), "same.nmon", "tester")
        second = make_aix(cpu=lambda i: 56.0).encode()
        host_monthly.import_nmon(c, second, "same.nmon", "tester")
        p = ns.raw_file(c, "hostA", "same.nmon")
        assert p is not None and p.read_bytes() == second              # 修前同秒取到第一份
        c.close()


# ---------------------------------------------------------------- 1131
@pytest.mark.parametrize("bad", ["2026-00", "2026-13", "2026-0", "abc"])
def test_1131_主機月報備註_無效月份拒絕(bad):
    with tempfile.TemporaryDirectory() as tmp:
        c = _db(tmp)
        with pytest.raises(ValueError):
            host_monthly.save_note(c, bad, {}, "tester")
        c.close()


def test_1131_既存無效月份備註不被下個月繼承():
    with tempfile.TemporaryDirectory() as tmp:
        c = _db(tmp)
        host_monthly.save_note(c, "2025-12", {"verdict": "OK-1225"} if "verdict" in host_monthly.NOTE_DEFAULT else {}, "tester")
        host_monthly.ensure(c)
        c.execute("INSERT INTO host_monthly_note (month, data, updated_by, updated_at) VALUES ('2026-00', '{}', 'old', 'x')")
        c.commit()
        assert host_monthly.get_note(c, "2026-01")["_inherited"] == "2025-12"
        c.close()


def test_1131_一般月報_無效月份400_正常月份200():
    assert mr.valid_ym("2026-01") and mr.valid_ym("2026-12")
    assert not any(mr.valid_ym(x) for x in ("2026-00", "2026-13", "2026-1", "x", "", None))


# ---------------------------------------------------------------- 1133
def test_1133_下載檔與同次快照用同一次收集():
    with tempfile.TemporaryDirectory() as tmp:
        c = _db(tmp)
        payload = mr.collect(c)
        md = mr.render_markdown(c, "2026-09", "alice", payload=payload)
        snap = mr.freeze(c, "2026-09", "alice", payload=payload)
        assert payload["generated_at"] in md and snap["generated_at"] == payload["generated_at"]
        c.close()


def test_1133_交錯重產_較早收集的不可蓋掉較新的快照():
    with tempfile.TemporaryDirectory() as tmp:
        c = _db(tmp)
        older = mr.collect(c)
        older["generated_at"] = "2026-10-01 10:00:00"
        newer = mr.collect(c)
        newer["generated_at"] = "2026-10-01 10:00:05"
        mr.freeze(c, "2026-09", "bob", payload=newer)
        s = mr.freeze(c, "2026-09", "alice", payload=older)
        assert s["generated_by"] == "bob" and s["generated_at"] == "2026-10-01 10:00:05"
        c.close()
