"""天條：vCenter 一體機在數量上算虛擬機（v2.007.6），但納管／收集照樣擋（2.007.8 審查）。

角色（count_bridge）只管「算幾台」；天條看的是作業系統大類（onboard_eligibility.is_vmware_platform），兩件事分開。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import count_bridge  # noqa: E402
import db  # noqa: E402
import onboard_eligibility  # noqa: E402


def test_vCenter標VM_算虛擬機但不納管不收集(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        db.insert_hardware(c, asset_serial="A-VC1", hostname="vcsa-a", ip="10.99.8.21", os="Photon OS",
                           asset_purpose="vCenter", is_vm=1)
        c.commit()
        roles = {x["asset_serial"]: x["role"] for x in count_bridge.compute(c)["items"]}
        assert roles["A-VC1"] == "vm"
        row = c.execute("SELECT * FROM hardware WHERE asset_serial = 'A-VC1'").fetchone()
        got = onboard_eligibility.judge_for_asset(c, row)
        assert got and got[0] == "esxi", got
        assert "10.99.8.21" in onboard_eligibility.vmware_targets(c, ["10.99.8.21"])
    finally:
        c.close()
