"""W221-005／006：EOS 原因分類、unverified 不單獨觸發紅燈／通知；extendable（產品有延伸方案）≠ 資產持有權益，
只有資產權益 valid 才降級（Codex 24 號退回後改正）。"""
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import eos  # noqa: E402
import eos_notify as en  # noqa: E402
import health_check  # noqa: E402
import normalize  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    yield
    eos.reload_tables()


def test_eos_reason_六種原因分得開():
    assert eos.eos_reason(None) == "not_found"
    assert eos.eos_reason({"eos_date": None, "confidence": "no_official_date"}) == "not_announced"
    assert eos.eos_reason({"eos_date": None, "confidence": "no_single_date"}) == "no_single_date"
    assert eos.eos_reason({"eos_date": None, "confidence": "source_rejected"}) == "rejected"
    assert eos.eos_reason({"eos_date": "2029-07", "confidence": "unverified"}) == "unverified"
    assert eos.eos_reason({"eos_date": None, "confidence": "unverified"}) == "unverified"
    assert eos.eos_reason({"eos_date": "not-a-date", "confidence": "confirmed"}) == "bad_format"
    assert eos.eos_reason({"eos_date": "2029-07", "confidence": "confirmed"}) is None
    assert eos.eos_reason({"eos_date": "2029-07-31", "confidence": "confirmed_via_attachment"}) is None


def test_eos_status舊值不變_相容():
    assert eos.eos_status(None) == "unknown"
    assert eos.eos_status("garbage") == "unknown"
    assert eos.eos_status("2000-01-01") == "expired"
    assert eos.eos_status("2999-01-01") == "ok"


def test_assess_unverified有日期但不可信_extendable不當成已涵蓋():
    a = eos.assess({"eos_date": "2000-01-01", "confidence": "unverified", "eos_kind": "End of Life (EOL)",
                    "extendable": True})
    assert a["status"] == "expired" and a["reason"] == "unverified" and not a["trusted"]
    assert a["downgraded"] is False and a["plan_available"] and a["entitlement_state"] == "unknown"
    assert a["eos_kind"] == "End of Life (EOL)"
    b = eos.assess({"eos_date": "2000-01-01", "confidence": "confirmed", "extendable": False})
    assert b["trusted"] and not b["downgraded"] and b["reason"] is None
    c = eos.assess({"eos_date": None, "confidence": "no_single_date"})
    assert c["status"] == "unknown" and c["reason"] == "no_single_date" and c["reason_label"]


def test_vulns與assets帶reason欄位():
    import routers.assets as ra
    import routers.vulns as rv

    x = rv._eos_extra({"eos_date": None, "confidence": "source_rejected", "eos_kind": "End of Support"})
    assert x["reason"] == "rejected" and x["eos_kind"] == "End of Support" and x["trusted"] is False
    y = ra._eos_assess_fields({"eos_date": "2000-01-01", "confidence": "confirmed"})
    assert y["status"] == "expired" and y["reason"] is None and y["trusted"] is True


@pytest.fixture()
def conn(tmp_path):
    db.init_db(tmp_path / "t.db")
    c = db.get_connection(tmp_path / "t.db")
    try:
        yield c
    finally:
        c.close()


def _chk(conn, hit):
    eos._os_table = [dict(hit, name="Rocky Linux 9")]
    row = {"os": "Rocky Linux 9.7", "device_model": ""}
    return health_check._chk_eos(row, {}, eos, normalize, conn)


def test_unverified過期日期不亮紅燈(conn):
    r = _chk(conn, {"eos_date": "2000-01-01", "confidence": "unverified", "eos_kind": "End of Life"})
    assert r and r["level"] == health_check.WARN and "未核對" in r["label"]


def test_已核對過期紅燈_措辭官方公告_不說必須更換(conn):
    r = _chk(conn, {"eos_date": "2000-01-01", "confidence": "confirmed", "eos_kind": "End of Support"})
    assert r["level"] == health_check.BAD and "官方公告" in r["detail"] and "必須更換" not in r["detail"]


def test_產品有延伸方案但資產權益未知_不降級_標權益待確認(conn):
    # Codex 24 號退回：方案存在（Red Hat ELS 是選購附加訂閱）≠ 這台持有；沒有資產權益證據就不能降風險
    r = _chk(conn, {"eos_date": "2000-01-01", "confidence": "confirmed", "eos_kind": "End of Support",
                    "extendable": True})
    assert r["level"] == health_check.BAD
    assert "權益待確認" in r["label"] and "不當作已涵蓋" in r["detail"]


def test_相同產品_一台有效權益降級_其餘情況維持原風險(conn, monkeypatch):
    hit = {"eos_date": "2000-01-01", "confidence": "confirmed", "eos_kind": "End of Support", "extendable": True}
    P = "Rocky Linux 9"                                                   # _chk 的產品名
    cases = {
        "valid": ({"source": "合約 X-1", "scope": [P], "valid_from": "2000-01-01", "valid_until": "2999-12-31"}, health_check.WARN),
        "expired": ({"source": "合約 X-1", "scope": [P], "valid_until": "2000-12-31"}, health_check.BAD),
        "no_source": ({"scope": [P], "valid_until": "2999-12-31"}, health_check.BAD),
        "no_scope": ({"source": "合約 X-1", "valid_until": "2999-12-31"}, health_check.BAD),
        "wrong_product": ({"source": "合約 X-1", "scope": ["RHEL 7"], "valid_until": "2999-12-31"}, health_check.BAD),   # Codex 26：RHEL7 範圍不能套 Rocky
        "not_yet_effective": ({"source": "合約 X-1", "scope": [P], "valid_from": "2999-01-01", "valid_until": "2999-12-31"}, health_check.BAD),
        "bad_date": ({"source": "合約 X-1", "scope": [P], "valid_until": "not-a-date"}, health_check.BAD),
        "none": (None, health_check.BAD),
    }
    for name, (ent, level) in cases.items():
        monkeypatch.setattr(eos, "entitlement_for", lambda conn_, row_, e=ent: e)
        assert _chk(conn, dict(hit))["level"] == level, name


def test_assess只有有效且適用的權益才downgraded():
    hit = {"name": "Rocky Linux 9", "eos_date": "2000-01-01", "confidence": "confirmed",
           "eos_kind": "End of Support", "extendable": True}
    a0 = eos.assess(hit)
    assert a0["plan_available"] and a0["entitlement_state"] == "unknown" and a0["entitlement_pending"] and not a0["downgraded"]
    ok = {"source": "S", "scope": ["Rocky Linux 9"], "valid_until": "2999-01-01"}
    a1 = eos.assess(hit, ok)
    assert a1["entitlement_state"] == "valid" and a1["downgraded"] and not a1["entitlement_pending"]
    a2 = eos.assess(hit, {**ok, "valid_until": "2000-06-01"})
    assert a2["entitlement_state"] == "expired" and not a2["downgraded"] and a2["entitlement_pending"]
    a3 = eos.assess({**hit, "extendable": False})
    assert not a3["plan_available"] and not a3["entitlement_pending"] and not a3["downgraded"]
    a4 = eos.assess(hit, {**ok, "scope": ["RHEL 7"]})                     # 範圍是別的產品
    assert a4["entitlement_state"] == "not_applicable" and not a4["downgraded"] and a4["entitlement_pending"]
    a5 = eos.assess(hit, {**ok, "valid_from": "2999-01-01"})              # 尚未生效
    assert a5["entitlement_state"] == "not_yet_effective" and not a5["downgraded"]
    a6 = eos.assess(hit, {k: v for k, v in ok.items() if k != "scope"})   # 沒有適用範圍
    assert a6["entitlement_state"] == "unknown" and not a6["downgraded"]


def _hw(conn, serial, host, ip, os_):
    conn.execute("INSERT INTO hardware (asset_serial, hostname, ip, os, asset_status) VALUES (?,?,?,?,'使用中')",
                 (serial, host, ip, os_))
    conn.commit()


TODAY = date(2026, 11, 1)


def test_通知排除unverified_另計來源未核對(conn):
    eos._os_table = [{"name": "Rocky Linux 9", "eos_date": "2027-03-01", "confidence": "unverified"}]
    eos._hw_table = []
    _hw(conn, "S1", "srv01", "10.99.0.1", "Rocky Linux 9.7")
    cands, stats = en.eos_candidates(conn, TODAY)
    assert cands == [] and stats["unverified"] == 1
    assert stats["unverified_list"][0]["item"].startswith("Rocky Linux 9")
    assert "來源尚未核對" in "\n".join(en._footer(stats))


def test_通知措辭官方公告_extendable加註(conn):
    eos._os_table = [{"name": "Rocky Linux 9", "eos_date": "2027-03-01", "confidence": "confirmed",
                      "extendable": True}]
    eos._hw_table = []
    _hw(conn, "S1", "srv01", "10.99.0.1", "Rocky Linux 9.7")
    cands, _ = en.eos_candidates(conn, TODAY)
    assert len(cands) == 1
    line = en._item_line({**cands[0], "_bucket": 180})
    assert "官方公告" in line and "結束" in line and "延伸方案" in line and "權益待確認" in line


def test_月精度通知不稱官方精確日_日精度照舊():
    """Codex 39 號：官方只公告到月份時，通知要寫原文年月並說明月初是保守換算，不能寫成官方精確日。"""
    base = {"track": "hw", "hostname": "h", "ip": "10.99.1.1", "days_left": 1, "item": "Fixture Model",
            "eos_date": "2030-04-01", "extendable": False}
    month = en._item_line({**base, "eos_date_text": "2030-04", "date_precision": "month"})
    assert "2030-04（官方只公告到月份；提醒以月初 2030-04-01 保守換算）" in month
    assert "官方公告 Fixture Model 於 2030-04-01 結束" not in month
    day = en._item_line({**base, "eos_date_text": "2030-04-15", "date_precision": "day", "eos_date": "2030-04-15"})
    assert "官方公告 Fixture Model 於 2030-04-15 結束" in day and "保守換算" not in day
    assert en.is_month_precision("2030-04") and not en.is_month_precision("2030-04-15") and not en.is_month_precision(None)
