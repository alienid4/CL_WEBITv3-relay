"""1.2 每日異動／摘要信「從沒收集過」（2.066.2，docs/驗收_完整日報_2026-11-13.md 疑點 3）。

釘住：
  1. 台數＝data_coverage 主機收集類（spec）「未納管＋已納管尚無資料」，不另寫一套
  2. 下鑽 state=never＝同一份 rows，筆數＝台數
  3. 摘要信多一節「四、從沒收集過」，數字＝同一個值；不算進主旨的變化／收集中斷
  4. 算不出來時寫「讀取失敗」，不是 0
  5. 整份日報摘要頁多一列
  6. /api/changes 回 never_collected；畫面分類表有那一列並下鑽到 /api/v2/coverage
"""
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import daily_digest as dd  # noqa: E402
import data_coverage as dc  # noqa: E402
import db  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _db(tmp_path):
    p = tmp_path / "d.db"
    db.init_db(p)
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="A-1", hostname="h1", ip="10.99.1.1", os="RHEL 9",
                       asset_status="使用中", collect_ok=1, collect_checked_at="2026-10-01 01:00:00")
    db.insert_hardware(conn, asset_serial="A-2", hostname="h2", ip="10.99.1.2", os="Windows",
                       asset_status="使用中")
    db.insert_hardware(conn, asset_serial="A-3", hostname="h3", ip="10.99.1.3", os="RHEL 8",
                       asset_status="使用中")
    # 已納管（7 天內連上）但沒有 host_spec＝已納管尚無資料
    db.insert_hardware(conn, asset_serial="A-4", hostname="h4", ip="10.99.1.4", os="RHEL 9",
                       asset_status="使用中", collect_ok=1,
                       collect_checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    conn.commit()
    return conn


def test_台數等於覆蓋率兩格_下鑽同一份(tmp_path):
    conn = _db(tmp_path)
    now = datetime(2026, 10, 23, 7, 0)
    nc = dd.never_collected(conn, now)
    r = dc.coverage(conn, "spec", now)
    assert nc["count"] == r["counts"][dc.UNMANAGED] + r["counts"][dc.PENDING]
    assert (nc["unmanaged"], nc["pending"]) == (r["counts"][dc.UNMANAGED], r["counts"][dc.PENDING])
    # A-2、A-3 未納管；A-4 已納管尚無資料；A-1 已納管但失聯＝收集失敗（不列入）
    assert (nc["count"], nc["unmanaged"], nc["pending"]) == (3, 2, 1)
    assert {x["serial"] for x in dc.drill(conn, "spec", "never", now)["rows"]} == {"A-2", "A-3", "A-4"}
    d = dc.drill(conn, "spec", "never", now)
    assert len(d["rows"]) == nc["count"]
    assert {x["state"] for x in d["rows"]} <= set(dc.NEVER)


def test_信多一節_數字同源_不進主旨(tmp_path):
    conn = _db(tmp_path)
    dd.write_snapshot(conn, datetime(2026, 10, 22, 6, 0), "schedule")
    dd.write_snapshot(conn, datetime(2026, 10, 23, 6, 0), "schedule")
    subj, body = dd.build_mail(conn)
    n = dd.never_collected(conn)["count"]
    assert "四、從沒收集過" in body
    assert f"共 {n} 台（未納管" in body
    assert "cat=never" in body
    assert "從沒收集過" not in subj


def test_算不出來寫讀取失敗不是0(tmp_path, monkeypatch):
    conn = _db(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("壞了")
    monkeypatch.setattr(dc, "coverage", boom)
    nc = dd.never_collected_or_error(conn)
    assert nc["count"] is None and "壞了" in nc["error"]
    counts = {k: 0 for k, _ in dd.CATEGORIES}
    _s, body = dd.compose(dict(dd.DEFAULTS), {"date": "2026-10-23", "prev_date": "2026-10-22", "counts": counts,
                                             "never_collected": nc}, None)
    assert "讀取失敗" in body and "共 0 台（未納管" not in body


def test_沒給這一列的舊呼叫不受影響():
    counts = {k: 0 for k, _ in dd.CATEGORIES}
    _s, body = dd.compose(dict(dd.DEFAULTS), {"date": "2026-10-23", "prev_date": "2026-10-22", "counts": counts}, None)
    assert "從沒收集過" not in body


def test_整份日報摘要頁有這一列(tmp_path):
    conn = _db(tmp_path)
    dd.write_snapshot(conn, datetime(2026, 10, 23, 6, 0), "schedule")
    wb, _day, _n = dd.export_workbook(conn)
    vals = {r[0]: r[1] for r in wb["摘要"].iter_rows(values_only=True) if r and r[0]}
    assert vals.get("從沒收集過") == dd.never_collected(conn)["count"]


def test_API與畫面接線():
    router = (BACKEND / "routers" / "digest.py").read_text(encoding="utf-8")
    assert 'out["never_collected"] = daily_digest.never_collected_or_error(conn)' in router
    vue = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "v2" / "changes.vue").read_text(encoding="utf-8")
    assert "'/api/v2/coverage'" in vue and "state: NEVER" in vue and "kind: 'spec'" in vue
    assert "從沒收集過（目前）" in vue and 'prefix="N"' in vue


def test_verify_獨立重算等於日報(tmp_path):
    import verify_numbers as vn
    conn = _db(tmp_path)
    assert vn._never_indep(conn) == dd.never_collected(conn)["count"] == 3
