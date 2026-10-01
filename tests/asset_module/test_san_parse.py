"""san_parse（Brocade FOS 唯讀輸出 parser）測試。
用合成資料（假 WWPN／假 switch 名／10.99.x），不含任何真實識別字。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import san_parse  # noqa: E402

SWITCHSHOW = """\
switchName:     TESTSW01
switchType:     170.5
switchState:    Online
switchRole:     Principal
switchDomain:   1
switchWwn:      10:00:00:00:00:00:00:01
zoning:         ON (testcfg)
Index Port Address Media Speed State     Proto
==============================================
  0    0   010000  id    N32   Online    FC  F-Port  20:00:00:00:00:00:aa:01
  1    1   010100  id    N32   Online    FC  F-Port  1 N Port + 2 NPIV public
"""
FABRICSHOW = """\
Switch ID   Worldwide Name           Enet IP Addr  FC IP Addr   Name
 1: fffc01 10:00:00:00:00:00:00:01  10.99.0.10    0.0.0.0      >"TESTSW01"
"""
CFGACTV = """\
Effective configuration:
 cfg:   testcfg
 zone:  Z_HOSTA_STG
                20:00:00:00:00:00:aa:01
                50:00:00:00:00:00:bb:01
"""
ALISHOW = """\
Defined configuration:
 cfg:   testcfg
                Z_HOSTA_STG
 zone:  Z_HOSTA_STG
                hostA_hba0; storageX_p0
 alias: hostA_hba0
                20:00:00:00:00:00:aa:01
 alias: storageX_p0
                50:00:00:00:00:00:bb:01
"""


def test_join_wwpn_alias_port_zone():
    inv = san_parse.build_inventory(switchshow=SWITCHSHOW, fabricshow=FABRICSHOW,
                                    cfgactvshow=CFGACTV, alishow=ALISHOW)
    assert inv["switch"] == "TESTSW01"
    assert inv["enet_ip"] == "10.99.0.10"
    assert inv["zoning_cfg"] == "testcfg"
    by = {r["wwpn"]: r for r in inv["rows"]}
    # 主機 HBA：有別名、直連在 port 0、在 zone 裡
    a = by["20:00:00:00:00:00:aa:01"]
    assert a["alias"] == "hostA_hba0"
    assert a["switch"] == "TESTSW01" and a["port"] == "0"
    assert "Z_HOSTA_STG" in a["zones"]
    # 儲存埠：有別名、在同一個 zone、但沒直連本 switch 的實體 port
    s = by["50:00:00:00:00:00:bb:01"]
    assert s["alias"] == "storageX_p0"
    assert s["port"] == ""
    assert "Z_HOSTA_STG" in s["zones"]


def test_npiv_port_has_no_single_wwpn():
    _, ports = san_parse.parse_switchshow(SWITCHSHOW)
    p1 = [p for p in ports if p["port"] == "1"][0]
    assert p1["wwpn"] is None   # NPIV「1 N Port + 2 NPIV」不抓成單一 WWPN


def test_empty_inputs_dont_crash():
    inv = san_parse.build_inventory()
    assert inv["rows"] == []
