"""1.3 設備風險每一列帶「通知誰」（2.007.17）：保管者／使用者＋業務系統 AP 窗口與值班電話；沒登記就沒有（不猜）。"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import daily_risk  # noqa: E402
import db  # noqa: E402


def test_notify_map_有窗口與沒窗口():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = db.get_connection(p)
        db.insert_hardware(c, asset_serial="N1", hostname="a1", ip="10.99.9.1", api_id="API-1",
                           custodian="王小明", asset_status="使用中")
        db.insert_hardware(c, asset_serial="N2", hostname="a2", ip="10.99.9.2", asset_status="使用中")
        c.execute("INSERT INTO business_system (api_id, name, ap_owner, ap_department) VALUES ('API-1','測試系統','李四','資訊部')")
        c.execute("INSERT INTO system_oncall (api_id, phone) VALUES ('API-1','02-0000')")
        c.commit()
        m = daily_risk.notify_map(c, daily_risk.machines(c))
        txt = next(v for v in m.values() if "王小明" in v)
        assert "保管 王小明" in txt and "李四" in txt and "資訊部" in txt and "02-0000" in txt
        assert len(m) == 1      # N2 什麼都沒登記：不在表裡（畫面寫「未登記」）
        r = daily_risk.risk_overview(c)
        assert all("notify" in x for x in r["spof"]["rows"])
        c.close()
