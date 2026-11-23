"""Codex 985 回歸：資產明細的納管紀錄取「時間最新」的 50 筆，不是寫入順序（ID）最大的 50 筆。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
from test_nmon_series import _client  # noqa: E402


def test_985_納管紀錄取時間最新的50筆(tmp_path):
    cl = _client(tmp_path)
    c = db.get_connection(tmp_path / "t.db")
    db.insert_hardware(c, asset_serial="S1", hostname="h1", ip="10.99.3.1")
    ins = "INSERT INTO onboard_audit (target_ip, created_at, platform, trigger, ok, stage, message) VALUES (?,?,?,'manual',?,?,?)"
    c.execute(ins, ("10.99.3.1", "2026-11-01 09:00:00", "linux", 0, "x", "newest-first-written"))
    for i in range(50):
        c.execute(ins, ("10.99.3.1", f"2026-10-01 09:{i:02d}:00", "linux", 0, "x", f"old-{i}"))
    c.commit()
    c.close()
    import api
    try:
        r = cl.get("/api/assets/S1")
    finally:
        api.app.dependency_overrides.clear()
    assert r.status_code == 200
    hist = r.json()["onboard_history"]
    assert len(hist) == 50
    assert hist[0]["message"] == "newest-first-written"           # 修前被 ID 排序擠掉
