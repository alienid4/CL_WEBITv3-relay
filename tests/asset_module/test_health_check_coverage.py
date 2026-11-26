"""體檢十項涵蓋四態（Codex 37 號，W221-013）：沒有發現問題 ≠ 已驗。

passed 已驗通過／found 已驗有發現／not_checked 未驗（附原因）／not_applicable 不適用（附原因）；
四態加總恆等於 checks_total；每台各自判定。
"""
import itertools
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import health_check  # noqa: E402
from test_health_check import _FRESH, _conn, _doc, _host, _scanned  # noqa: E402

NOW = datetime.now()
_ts = lambda d: (NOW - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731


def _account(conn, serial="A1", ip="10.99.1.1", last_seen=None, username="u1"):
    conn.execute("INSERT INTO host_account (ip, asset_serial, username, uid, last_seen) VALUES (?,?,?,1000,?)",
                 (ip, serial, username, last_seen or _ts(0)))
    conn.commit()


def _baseline(conn, serial="A1", passed_at=None, last_run=None, per_asset=(1, 0)):
    conn.execute("INSERT INTO baseline_drift (asset_serial,item_key,baseline,status) "
                 "VALUES (?,?,?,?)", (serial, "telnet_disabled", "absent", "fixed"))
    if passed_at:
        conn.execute("INSERT INTO golive_check (asset_serial,status,passed_at) VALUES (?,?,?)",
                     (serial, "passed", passed_at))
    if last_run:
        conn.execute("INSERT OR REPLACE INTO app_settings (key,value) VALUES ('drift_last_run',?)",
                     (json.dumps({"at": last_run, "checked_assets": 1, "unknown_count": 0}),))
        if per_asset is not None:                      # 逐台證據：這台比對到幾項、測不到幾項（預設：比對到 1 項）
            conn.execute("INSERT OR REPLACE INTO app_settings (key,value) VALUES ('drift_last_run_assets',?)",
                         (json.dumps({"at": last_run, "assets": {serial: per_asset}}),))
    conn.commit()


def _ev(conn, hid):
    return health_check.evaluate_all(conn)[hid]


def _full_host(conn):
    hid = _host(conn, collect_ok=1, collect_checked_at=_FRESH)
    _scanned(conn, "10.99.1.1")
    _doc(conn, "A1")
    return hid


def _assert_sum(ev):
    cs = ev["check_states"]
    assert len(cs) == ev["checks_total"] == 10
    assert sum(ev["coverage"][k] for k in ("passed", "found", "not_checked", "not_applicable")) == ev["checks_total"]
    for v in cs.values():
        if v["state"] in ("not_checked", "not_applicable"):
            assert v["reason"]


def test_Codex反例_新庫只有主機掃描單據_帳號未驗_基線不適用():
    with _conn() as conn:
        hid = _full_host(conn)
        ev = _ev(conn, hid)
        assert ev["issues"] == []
        st = ev["check_states"]
        assert st["account"]["state"] == "not_checked" and "尚無帳號盤點" in st["account"]["reason"]
        assert st["drift"]["state"] == "not_applicable" and "無基準" in st["drift"]["reason"]
        assert "account" in ev["not_checked"] and "drift" in ev["not_checked"]
        assert ev["checks_total"] - len(ev["not_checked"]) < 10      # 不再是「已驗 10／10」
        _assert_sum(ev)


def test_有帳號規則但沒盤點_仍未驗():
    with _conn() as conn:
        hid = _full_host(conn)
        assert conn.execute("SELECT COUNT(*) FROM host_account").fetchone()[0] == 0
        assert _ev(conn, hid)["check_states"]["account"]["state"] == "not_checked"


def test_完整盤點零發現_passed():
    with _conn() as conn:
        hid = _full_host(conn)
        _account(conn)
        ev = _ev(conn, hid)
        assert ev["check_states"]["account"]["state"] == "passed"
        _assert_sum(ev)


def test_有盤點有發現_found():
    with _conn() as conn:
        hid = _full_host(conn)
        _account(conn)
        conn.execute("INSERT INTO account_finding (ip,asset_serial,username,rule_id,verdict) "
                     "VALUES ('10.99.1.1','A1','u1','R2','fail')")
        conn.commit()
        ev = _ev(conn, hid)
        assert ev["check_states"]["account"]["state"] == "found"
        _assert_sum(ev)


def test_盤點過期_未驗並寫原因():
    with _conn() as conn:
        hid = _full_host(conn)
        _account(conn, last_seen=_ts(90))
        s = _ev(conn, hid)["check_states"]["account"]
        assert s["state"] == "not_checked" and "過期" in s["reason"]


def test_盤點過期天數可設定():
    with _conn() as conn:
        hid = _full_host(conn)
        _account(conn, last_seen=_ts(10))
        assert _ev(conn, hid)["check_states"]["account"]["state"] == "passed"
        conn.execute("INSERT INTO app_settings (key,value) VALUES ('health_account_stale_days','5')")
        conn.commit()
        assert _ev(conn, hid)["check_states"]["account"]["state"] == "not_checked"


def test_盤點失敗_未驗並寫原因():
    with _conn() as conn:
        hid = _full_host(conn)
        _account(conn, last_seen=_ts(3))
        conn.execute("INSERT INTO collect_ssh_trace (asset_serial, ip, connected, collected_at) "
                     "VALUES ('A1','10.99.1.1',0,?)", (_ts(0),))
        conn.commit()
        s = _ev(conn, hid)["check_states"]["account"]
        assert s["state"] == "not_checked" and "失敗" in s["reason"]


def test_已上線基準但沒回檢_未驗():
    with _conn() as conn:
        hid = _full_host(conn)
        _baseline(conn, passed_at=_ts(2))
        s = _ev(conn, hid)["check_states"]["drift"]
        assert s["state"] == "not_checked" and "回檢" in s["reason"]


def test_上線後才回檢_才算已驗_回檢早於上線不算():
    with _conn() as conn:
        hid = _full_host(conn)
        _baseline(conn, passed_at=_ts(1), last_run=_ts(5))
        assert _ev(conn, hid)["check_states"]["drift"]["state"] == "not_checked"


def test_有回檢零偏移_passed():
    with _conn() as conn:
        hid = _full_host(conn)
        _baseline(conn, passed_at=_ts(5), last_run=_ts(0))
        ev = _ev(conn, hid)
        assert ev["check_states"]["drift"]["state"] == "passed"
        _assert_sum(ev)


def test_有偏移_found():
    with _conn() as conn:
        hid = _full_host(conn)
        _baseline(conn, passed_at=_ts(5), last_run=_ts(0))
        conn.execute("UPDATE baseline_drift SET status='open', current='present'")
        conn.commit()
        assert _ev(conn, hid)["check_states"]["drift"]["state"] == "found"


def test_兩台不同證據各自判定():
    with _conn() as conn:
        h1 = _full_host(conn)
        h2 = _host(conn, serial="A2", hostname="web02", ip="10.99.1.2", collect_ok=1, collect_checked_at=_FRESH)
        _scanned(conn, "10.99.1.2", hostname="web02")
        conn.execute("INSERT INTO doc_archive (doc_type, file_name, file_path, asset_serial, ip) "
                     "VALUES ('需求單','b.docx','/x/b.docx','A2','10.99.1.2')")
        conn.commit()
        _account(conn, serial="A1", ip="10.99.1.1")          # 只有 A1 盤點過
        _baseline(conn, serial="A2", passed_at=_ts(5), last_run=_ts(0))   # 只有 A2 有基準＋回檢
        ev = health_check.evaluate_all(conn)
        assert ev[h1]["check_states"]["account"]["state"] == "passed"
        assert ev[h2]["check_states"]["account"]["state"] == "not_checked"
        assert ev[h1]["check_states"]["drift"]["state"] == "not_applicable"
        assert ev[h2]["check_states"]["drift"]["state"] == "passed"
        _assert_sum(ev[h1])
        _assert_sum(ev[h2])


def test_summary有incomplete_coverage():
    with _conn() as conn:
        _full_host(conn)
        s = health_check.summary(conn)
        assert s["incomplete_coverage"] == 1


@pytest.mark.parametrize("scanned,doc,acct,base,run", list(itertools.product([0, 1], [0, 1], [0, 1], [0, 1], [0, 1])))
def test_四態加總恆等於checks_total(scanned, doc, acct, base, run):
    with _conn() as conn:
        hid = _host(conn, collect_ok=1, collect_checked_at=_FRESH)
        if scanned:
            _scanned(conn, "10.99.1.1")
        if doc:
            _doc(conn, "A1")
        if acct:
            _account(conn)
        if base:
            _baseline(conn, passed_at=_ts(5), last_run=_ts(0) if run else None)
        ev = _ev(conn, hid)
        _assert_sum(ev)
        # 沒有受檢證據就絕不是 passed
        if not acct:
            assert ev["check_states"]["account"]["state"] != "passed"
        if not base:
            assert ev["check_states"]["drift"]["state"] != "passed"
        if base and not run:
            assert ev["check_states"]["drift"]["state"] != "passed"


def test_前端缺check_states不默認全驗():
    ts = (ROOT / "APP/asset-module/frontend/utils/healthCoverage.ts").read_text(encoding="utf-8")
    assert "資料不足，無法判斷涵蓋" in ts
    vue = (ROOT / "APP/asset-module/frontend/pages/assets/[serial].vue").read_text(encoding="utf-8")
    assert "checks_total ?? 10" not in vue and "total - nc" not in vue
    assert "hc-gaps" in vue and "已驗有發現" in ts and "不適用" in ts
