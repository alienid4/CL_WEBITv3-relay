"""hmc_parse 測試（合成資料，不含真實識別字）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import hmc_parse  # noqa: E402


def test_kv_line_handles_quoted_commas():
    d = hmc_parse.parse_kv_line('name=srv1,type_model=8286-42A,note="a,b,c",serial_num=SN0001')
    assert d["name"] == "srv1"
    assert d["note"] == "a,b,c"          # 引號內逗號不切
    assert d["serial_num"] == "SN0001"


def test_build_inventory_merges_lpar_proc_mem():
    sys_text = "name=Server-A,type_model=8286-42A,serial_num=SN0001,state=Operating,curr_sys_firmware=FW950\n"
    lpar = ("name=vio1,lpar_id=1,lpar_env=vioserver,state=Running,os_version=VIOS 3.1\n"
            "name=aix1,lpar_id=2,lpar_env=aixlinux,state=Running,os_version=AIX 7.2\n")
    proc = "lpar_name=vio1,lpar_id=1,curr_proc_units=1.0\nlpar_name=aix1,lpar_id=2,curr_proc_units=2.0\n"
    mem = "lpar_name=vio1,lpar_id=1,curr_mem=8192\nlpar_name=aix1,lpar_id=2,curr_mem=32768\n"
    inv = hmc_parse.build_inventory(sys_text, {"Server-A": lpar}, {"Server-A": proc},
                                    {"Server-A": mem}, hmc_version="V10R1")
    assert inv["system_count"] == 1 and inv["lpar_count"] == 2
    s = inv["systems"][0]
    assert s["serial"] == "SN0001" and s["firmware"] == "FW950"
    by = {lp["name"]: lp for lp in s["lpars"]}
    assert by["aix1"]["env"] == "aixlinux"
    assert by["aix1"]["proc_units"] == "2.0"
    assert by["aix1"]["mem_mb"] == "32768"


def test_empty_dont_crash():
    inv = hmc_parse.build_inventory("")
    assert inv["systems"] == [] and inv["lpar_count"] == 0
