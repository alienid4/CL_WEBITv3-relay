"""san_parse 其餘指令解析（身家調查表用，2026-09-15）。

格式照真實 FOS v9.1 輸出的「形狀」寫，但**全部是合成資料**（假序號、假料號、假 IP 10.99.x），
不含任何真實識別字。每支都要「認不出來就回空、不丟例外」。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import san_parse  # noqa: E402

VERSION = """\
Kernel:     4.1.35rt41
Fabric OS:  v9.1.1b
Made on:    Tue Jan 17 18:00:10 2023
Flash:\t    Wed Oct 23 08:43:03 2024
BootProm:   4.0.23-sb
"""
FIRMWARE = """\
Appl     Primary/Secondary Versions
------------------------------------------
FOS      v9.1.1b
         v9.1.1a
"""
CHASSIS = """\

Chassis State:\t\tEnabled

FAN  Unit: 1\t
Fan Direction:\t\tNon-portside Intake
Time Awake:            \t327 days

POWER SUPPLY  Unit: 1\t
Power Source:\t\tAC
Power Usage:          \t-95W
Time Awake:            \t327 days

CHASSIS/WWN  Unit: 1
System AirFlow:\t\tNon-portside Intake
Factory Part Num:      \t40-0000000-00
Factory Serial Num:    \tTESTFACT001
Manufacture:           \tDay: 22  Month: 10  Year: 24
Time Alive:            \t100 hours
ID:           \t\tTEST0000
Part Num:     \t\t0000000TEST
Serial Num:   \t\tTESTSN01
"""
SWITCHSHOW = """\
switchName:\tTESTSW01
switchType:\t170.5
switchState:\tOnline
switchRole:\tPrincipal
switchDomain:\t1
zoning:\t\tON (testcfg)
Index Port Address  Media Speed   State       Proto
==================================================
   0   0   010000   id    N32\t  Online      FC  F-Port  1 N Port + 2 NPIV public
   1   1   010100   id    N16\t  Online      FC  F-Port  20:00:00:00:00:00:aa:01
   2   2   010200   id    N32\t  No_Light    FC
"""
FABRIC = """\
Switch ID   Worldwide Name          Enet IP Addr    FC IP Addr      Name
  1: fffc01 10:00:00:00:00:00:00:01 10.99.0.10  0.0.0.0        >"TESTSW01"
"""
PORTERR = """\
          frames        enc     crc     crc     too     too     bad     enc    disc    link    loss    loss    frjt    fbsy     c3timeout     pcs      uncor
        tx       rx      in     err     g_eof   shrt    long    eof     out    c3      fail    sync    sig                      tx      rx      err     err
   0:   10.3g    2.8g    0       0       0       0       0       0       0       0       0       0       0       0       0       0       0       0       0
   1:   61.2g   49.0g    0       5       0       0       0       0       0       0       0       0       0       0       0       0       0       0       0
   2:   50.6g   70.0g    0       0       0       0       0       0       0     183       0       0       2       0       0       0       0       0       0
   3:    1.0g    1.0g    0       0       0       0       0       0       0       1     124       0     124       0       0       0       0       0       0
"""
SFP = """\

=============
Port  0:
=============
Identifier:  3    SFP
Transceiver: 0000000000000000 8,16,32_Gbps M5 sw Inter,Short_dist
Vendor Name: TESTVENDOR
Vendor PN:   00-0000000-00
Serial No:   TESTSFP0001
Wavelength:  850  (units nm)
Temperature: 45      Centigrade
RX Power:    -0.7    dBm (851.0uW)
TX Power:    0.1     dBm (1023.4 uW)
Pwr On Time: 1.70 years (14869 hours)

=============
Port  2:
=============
No SFP installed in port.
"""


def test_version():
    v = san_parse.parse_version(VERSION)
    assert v["fos"] == "v9.1.1b" and v["bootprom"] == "4.0.23-sb"


def test_firmware_primary_secondary_不一致要看得出來():
    f = san_parse.parse_firmwareshow(FIRMWARE)
    assert f["primary"] == "v9.1.1b" and f["secondary"] == "v9.1.1a" and f["consistent"] is False


def test_chassis_序號料號製造日期電源():
    c = san_parse.parse_chassisshow(CHASSIS)
    assert c["serial"] == "TESTSN01" and c["part_num"] == "0000000TEST"
    assert c["factory_serial"] == "TESTFACT001"
    assert c["manufactured"] == "2024-10-22"
    assert c["chassis_state"] == "Enabled"
    assert c["power_supplies"][0]["source"] == "AC" and len(c["fans"]) == 1


def test_port_統計():
    _, ports = san_parse.parse_switchshow(SWITCHSHOW)
    s = san_parse.port_summary(ports)
    assert s["total"] == 3 and s["online"] == 2 and s["no_light"] == 1
    assert s["by_speed"] == {"N32": 2, "N16": 1}


def test_porterr_只挑真的有問題的port():
    """丟框（disc_c3）、失光（loss_sig）常見而且通常無害，不能跟 CRC／link fail 一起亮紅。"""
    e = san_parse.parse_porterrshow(PORTERR)
    assert e["ports"] == 4
    assert e["ports_with_errors"] == ["1", "3"], "port1 CRC、port3 link fail 才算有問題"
    assert e["errors_detail"]["1"] == {"crc_err": "5"}
    assert e["errors_detail"]["3"] == {"link_fail": "124"}
    assert e["ports_with_other_counters"] == ["2"], "port2 只有丟框與失光"


def test_sfp_沒插的port不列():
    s = san_parse.parse_sfpshow(SFP)
    assert [x["port"] for x in s] == ["0"]
    assert s[0]["serial"] == "TESTSFP0001" and s[0]["vendor"] == "TESTVENDOR"


def test_isl_沒有串接回空():
    assert san_parse.parse_islshow("No ISL found\n") == []


def test_build_details_整合_缺指令不炸():
    d = san_parse.build_details({"switchshow": SWITCHSHOW, "fabricshow": FABRIC, "version": VERSION,
                                 "chassisshow": CHASSIS})
    assert d["mgmt_ip"] == "10.99.0.10" and d["version"]["fos"] == "v9.1.1b"
    assert d["chassis"]["serial"] == "TESTSN01" and d["ports"]["online"] == 2
    assert d["sfps"] == [] and d["isls"] == [] and d["port_errors"]["ports"] == 0
    assert san_parse.build_details({}) is not None
