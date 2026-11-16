"""採集目標要整台，不是那一筆登記（2026-09-20）。

使用者從帳外那筆（DYN-…）的詳細頁按「收集全部」，服務／軟體／帳號都說沒收到——
收得到的是同一台的 CIA 那筆（HW-…）。顯示早上已改成整台（v1.276），採集卻還逐筆。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_inventory  # noqa: E402
import db  # noqa: E402
import manage_state  # noqa: E402
import service_inventory  # noqa: E402
import software_inventory  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    # 同一台兩筆：CIA 那筆收得到、帳外那筆沒有收集狀態
    db.insert_hardware(c, asset_serial="HW-1", hostname="h1", ip="192.0.2.10", asset_status="使用中")
    db.insert_hardware(c, asset_serial="DYN-00:11", hostname="h1", ip="192.0.2.10", asset_status="使用中")
    c.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = 'HW-1'")
    # 另一台，完全無關，不可以被掃進來
    db.insert_hardware(c, asset_serial="HW-9", hostname="h9", ip="192.0.2.99", asset_status="使用中")
    c.execute("UPDATE hardware SET collect_ok = 1 WHERE asset_serial = 'HW-9'")
    c.commit()
    return c


def test_從帳外那筆指定也要收得到同一台(tmp_path):
    c = _conn(tmp_path)
    for name, fn in (("服務", service_inventory._resolve_targets),
                     ("軟體", software_inventory._resolve_targets),
                     ("帳號", account_inventory._targets)):
        got = [t["asset_serial"] for t in fn(c, "DYN-00:11")]
        assert got == ["HW-1"], f"{name}：指定帳外那筆時要收同一台收得到的那筆，得到 {got}"


def test_不可以擴散到別台(tmp_path):
    c = _conn(tmp_path)
    got = [t["asset_serial"] for t in service_inventory._resolve_targets(c, "HW-1")]
    assert got == ["HW-1"], got


def test_不指定時照舊收全部可收集的(tmp_path):
    c = _conn(tmp_path)
    got = sorted(t["asset_serial"] for t in service_inventory._resolve_targets(c, None))
    assert got == ["HW-1", "HW-9"]


def test_硬體規格也走同一條規則(tmp_path):
    c = _conn(tmp_path)
    sql, params = manage_state.collect_targets_sql(
        c, "DYN-00:11",
        "SELECT asset_serial, ip FROM hardware WHERE collect_ok = 1 AND ip IS NOT NULL AND ip != ''")
    got = [r["asset_serial"] for r in c.execute(sql, params)]
    assert got == ["HW-1"], got
