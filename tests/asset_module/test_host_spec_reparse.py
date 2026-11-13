"""已存 raw_json 的重新判讀（2026-10-07，搬遷重建第一批）。

這一批的全部價值在於「原文一直都在、只是沒人判讀過」，所以測的是：
1. 新解析器真的把當年丟掉的欄位撈出來（WWID／插槽／成員碟／RAID 層級／容量）
2. 舊鍵的**形狀沒變**——畫面 `mpaths.join()`／`raidList.join()` 直接吃它，變了就印出 [object Object]
3. 「沒有原文」與「這台沒有」分得開（鐵律：空白要講得出是哪一種空白）
4. 重判讀**不碰機器、不動 raw_json、不更新 collected_at**
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import host_spec_collector as H  # noqa: E402

MULTIPATH = """\
mpatha (3600508b4000156d700012000000b0000) dm-3 HP,HSV200
size=100G features='1 queue_if_no_path' hwhandler='0' wp=rw
|-+- policy='round-robin 0' prio=1 status=active
| `- 1:0:0:1 sdb 8:16 active ready running
`-+- policy='round-robin 0' prio=1 status=enabled
  `- 2:0:0:3 sdc 8:32 active ready running
mpathb (3600508b4000156d700012000000b0001) dm-4 HP,HSV200
size=500G features='0' hwhandler='0' wp=rw
`-+- policy='round-robin 0' prio=1 status=active
  `- 1:0:0:2 sdd 8:48 active ready running
"""

MDSTAT = """\
Personalities : [raid1]
md0 : active raid1 sdb1[1] sda1[0]
      976630464 blocks super 1.2 [2/2] [UU]

md1 : active raid1 sdd1[2](S) sdc1[0]
      100000 blocks super 1.2 [2/1] [U_]

unused devices: <none>
"""

DIMM = """\
Handle 0x1100, DMI type 17, 84 bytes
Memory Device
	Size: 16 GB
	Locator: DIMM_A1
	Bank Locator: NODE 1
	Type: DDR4
	Manufacturer: Samsung
	Part Number: M393A2K40BB1-CRC
	Serial Number: DEADBEEF
	Configured Memory Speed: 2400 MT/s

Handle 0x1101, DMI type 17, 84 bytes
Memory Device
	Size: No Module Installed
	Locator: DIMM_A2
	Bank Locator: NODE 1
	Manufacturer: Not Specified
"""

SMART = """\
@@DISK sda
Device Model:     ST300MM0008
Serial Number:    W1A2B3C4
Firmware Version: HPD4
User Capacity:    300,000,000,000 bytes [300 GB]
Rotation Rate:    10000 rpm
SMART overall-health self-assessment test result: PASSED
@@DISK sdb
Model Number:     SAMSUNG MZ7LH960
Serial Number:    S4A5B6C7
User Capacity:    960,197,124,096 bytes [960 GB]
Rotation Rate:    Solid State Device
SMART overall-health self-assessment test result: PASSED
"""

STORCLI = """\
CLI Version = 007.1017.0000.0000
Controller = 0
Product Name = PERC H730P Mini
FW Package Build = 25.5.5.0005

TOPOLOGY :
========
DG Arr Row EID:Slot DID Type  State BT     Size PDC  PI SED DS3  FSpace TR
 0 -   -   -        -   RAID1 Optl  N  278.464 GB dflt N  N   none N      N
 0/0   RAID1 Optl  RW     Yes     RWBD  -   ON  278.464 GB
 0:1   9  Onln  0 278.464 GB SAS HDD N  N  512B ST300MM0008 U
 0:2   10 Onln  0 278.464 GB SAS HDD N  N  512B ST300MM0008 U
"""

SSACLI = """\
Smart Array P440ar in Slot 0 (Embedded)
   array A
      logicaldrive 1 (279.4 GB, RAID 1, OK)
      physicaldrive 1I:1:1 (port 1I:box 1:bay 1, SAS, 300 GB, OK)
      physicaldrive 1I:1:2 (port 1I:box 1:bay 2, SAS, 300 GB, OK)
"""


# ---- 1. 當年丟掉的欄位真的撈回來了 ----
def test_multipath_撈回_wwid_與_lun_與路徑數():
    d = H.parse_multipath_detail(MULTIPATH)
    assert [m["name"] for m in d] == ["mpatha", "mpathb"]
    # WWID 才是磁碟的身分；mpatha 這個名字只是本機 bindings 的流水號
    assert d[0]["wwid"] == "3600508b4000156d700012000000b0000"
    assert d[0]["dm"] == "dm-3"
    assert d[0]["size"] == "100G"
    assert d[0]["write_protect"] == "rw"
    # 兩條路＝備援在；搬完只剩一條就是備援沒接回來
    assert d[0]["path_count"] == 2
    assert [p["dev"] for p in d[0]["paths"]] == ["sdb", "sdc"]
    # H:C:T:L 的 L 是 LUN 號（這兩條路的 LUN 刻意給不同值，確認是取最後一段不是第一段）
    assert [p["lun"] for p in d[0]["paths"]] == ["1", "3"]
    assert d[0]["paths"][0]["state"] == "active ready running"
    assert d[1]["path_count"] == 1


def test_mdstat_撈回成員碟與降級():
    a = H.parse_mdstat_detail(MDSTAT)
    assert [x["name"] for x in a] == ["md0", "md1"]
    assert a[0]["level"] == "raid1" and a[0]["state"] == "active"
    # 重建要知道是「哪兩顆」組起來的，不是只知道「有一個 raid1」
    assert [m["dev"] for m in a[0]["members"]] == ["sdb1", "sda1"]
    assert a[0]["blocks"] == 976630464
    assert a[0]["degraded"] is False and a[0]["status"] == "UU"
    # spare 要標得出來，否則重建時會把備援碟當成資料碟
    assert [m["flag"] for m in a[1]["members"]] == ["S", None]
    assert a[1]["degraded"] is True


def test_dimm_撈回插槽位置與空槽():
    d = H.parse_dmidecode_dimm(DIMM)
    assert d["count"] == 1
    m = d["modules"][0]
    assert m["locator"] == "DIMM_A1" and m["bank"] == "NODE 1"
    assert m["type"] == "DDR4" and m["manufacturer"] == "Samsung"
    assert m["part_number"] == "M393A2K40BB1-CRC"
    # 舊鍵語意不變
    assert m["size"] == "16 GB" and m["speed"] == "2400 MT/s"
    # 刻意不收序號：重建要回答「這個槽該插什麼」，不是「這條是哪一條」
    assert "serial" not in m
    # 空槽要列得出來：8 槽插滿 vs 16 槽插 8 條，擴充要不要停機完全不同
    assert d["empty_slots"] == ["DIMM_A2"]


def test_dimm_的_not_specified_不可以當成值():
    # 照字面存下來，畫面上會出現「製造商：Not Specified」這種假資料
    d = H.parse_dmidecode_dimm(DIMM)
    assert "Not Specified" not in json.dumps(d)


def test_smart_撈回容量與_ssd_hdd():
    ds = H.parse_smart(SMART)
    assert len(ds) == 2
    # 同型號會有多種容量，配錯容量 LV 佈局擺不回去
    assert ds[0]["capacity"].startswith("300,000,000,000")
    assert ds[0]["serial"] == "W1A2B3C4" and ds[0]["firmware"] == "HPD4"
    assert ds[0]["kind"] == "HDD"
    assert ds[1]["kind"] == "SSD"


def test_raid_hw_storcli_解出陣列與成員碟():
    r = H.parse_raid_hw(STORCLI)
    assert r["parsed"] is True and r["source"] == "storcli"
    assert r["controllers"][0]["model"] == "PERC H730P Mini"
    assert r["controllers"][0]["firmware"] == "25.5.5.0005"
    assert r["virtual_drives"][0]["raid_level"] == "RAID1"
    assert [d["eid_slot"] for d in r["drives"]] == ["0:1", "0:2"]


def test_raid_hw_ssacli_解出陣列與成員碟():
    r = H.parse_raid_hw(SSACLI)
    assert r["parsed"] is True and r["source"] == "ssacli"
    assert r["controllers"][0]["slot"] == "0"
    vd = r["virtual_drives"][0]
    assert vd["array"] == "A" and vd["raid_level"] == "RAID 1" and vd["size"] == "279.4 GB"
    assert [d["id"] for d in r["drives"]] == ["1I:1:1", "1I:1:2"]
    assert r["drives"][0]["array"] == "A"


def test_raid_hw_沒輸出與認不出格式要講原因_不可以靜默回空():
    empty = H.parse_raid_hw("")
    assert empty["parsed"] is False and empty["note"]
    weird = H.parse_raid_hw("some vendor tool nobody has ever seen")
    assert weird["parsed"] is False and "raw_json" in weird["note"]


# ---- 2. 舊鍵形狀不可以變（畫面直接 join） ----
def test_舊鍵形狀不變_畫面才不會印出物件():
    # [serial].vue 的 mpaths / raidList 是 string[] 直接 join
    assert H.parse_multipath(MULTIPATH) == ["mpatha", "mpathb"]
    assert all(isinstance(x, str) for x in H.parse_multipath(MULTIPATH))
    sw = H.parse_mdstat(MDSTAT)
    assert all(isinstance(x, str) for x in sw)
    assert sw[0] == "md0 raid1 (active)"


def test_build_linux_spec_extra_同時給舊鍵與新明細():
    raw = {"multipath": MULTIPATH, "mdstat": MDSTAT, "dimm": DIMM,
           "smart": SMART, "raid_hw": STORCLI, "lscpu": "", "tools": ""}
    e = H.build_linux_spec_extra(raw)
    assert e["multipath"] == ["mpatha", "mpathb"]          # 舊
    assert e["multipath_detail"][0]["wwid"]                 # 新
    assert e["raid_sw"][0].startswith("md0")                # 舊
    assert e["raid_sw_detail"][0]["members"]                # 新
    assert e["raid_hw"] is True                             # 舊（布林）
    assert e["raid_hw_detail"]["virtual_drives"]            # 新（真的組態）


def test_raid_hw_detail_解不出來時要留著_不可以被濾掉():
    # `if v` 會把 parsed=False 濾掉，那就又變成一片空白、沒有原因
    e = H.build_linux_spec_extra({"raid_hw": "unknown vendor output"})
    assert e["raid_hw_detail"]["parsed"] is False
    assert e["raid_hw_detail"]["note"]


# ---- 3. 「沒收到」與「沒有」要分得開 ----
def test_coverage_分得出沒跑_沒輸出_與_解不出來():
    raw = {"multipath": MULTIPATH, "mdstat": "", "smart": SMART}
    e = H.build_linux_spec_extra(raw)
    cov = H.extra_coverage(raw, e)
    assert cov["multipath_detail"]["state"] == "collected"
    # 指令跑了但沒輸出＝沒裝工具或沒授權，不是「這台沒有軟體 RAID」
    assert cov["raid_sw_detail"]["state"] == "no_output"
    assert "沒有輸出" in cov["raid_sw_detail"]["reason"]
    # 連那條指令都沒有＝收集時間早於這個項目。跟上面是**不同的** state，
    # 混成同一個的話畫面分不出「去補授權」還是「去重收一次」。
    assert cov["dimm"]["state"] == "not_run"


def test_coverage_有原文但解不出來要標_unparsed_並留得到原文的路():
    raw = {"raid_hw": "unknown vendor output"}
    e = H.build_linux_spec_extra(raw)
    cov = H.extra_coverage(raw, e)
    assert cov["raid_hw_detail"]["state"] == "unparsed"
    # 這是我們自己的解析缺陷，畫面要點得進去看原文；沒有 raw_key 就走不到證據
    assert cov["raid_hw_detail"]["raw_key"] == "raid_hw"


# ---- 3b. 文案：六個字以內，而且五種空白仍然分得出來（2026-10-07 使用者裁示）----
def test_coverage_空狀態文案六字以內_不可以寫成句子():
    raw = {"multipath": MULTIPATH, "mdstat": "", "raid_hw": "unknown vendor output"}
    cov = H.extra_coverage(raw, H.build_linux_spec_extra(raw))
    for key, c in cov.items():
        if c["state"] == "collected":
            assert c["label"] is None
            continue
        label = c["label"]
        assert label, f"{key} 是空白卻沒有 label，畫面只能印「無資訊」"
        # 中文字數算在六以內（工具名這種 ASCII 識別字不計，照使用者示範的
        # `未裝 multipath`）；句號逗號以外的敘述一律不行
        zh = [ch for ch in label if ord(ch) > 0x2E80]
        assert len(zh) <= 6, f"{key} 的 label「{label}」中文超過六個字，塞不進一格"
        assert "。" not in label, f"{key} 的 label「{label}」寫成句子了"


def test_coverage_五種空白各自是不同的_state_不可以合併():
    # 沒收過
    assert H.extra_coverage({}, {})["raid_hw_detail"]["state"] == "not_collected"
    # 指令沒跑 / 跑了沒輸出 / 解不出來
    raw = {"multipath": "", "raid_hw": "unknown vendor output"}
    cov = H.extra_coverage(raw, H.build_linux_spec_extra(raw))
    assert cov["dimm"]["state"] == "not_run"
    assert cov["multipath_detail"]["state"] == "no_output"
    assert cov["raid_hw_detail"]["state"] == "unparsed"
    # 形態不適用
    vm = H.extra_coverage({"raid_hw": ""}, {}, is_vm=True)
    assert vm["raid_hw_detail"]["state"] == "not_applicable"
    assert len({"not_collected", "not_run", "no_output", "unparsed", "not_applicable"}) == 5


def test_coverage_VM_的硬體RAID是_不適用_不可以說_重收一次就有():
    # 使用者的抱怨：畫面只寫「無資訊」，而且 reason 叫人去重收——
    # 對 VM 那是錯的，重收一百次也不會有實體 RAID 控制器
    cov = H.extra_coverage({"lscpu": "x"}, {}, is_vm=True)
    assert cov["raid_hw_detail"]["state"] == "not_applicable"
    assert cov["raid_hw_detail"]["label"] == "VM，無 RAID"
    assert "重收" not in cov["raid_hw_detail"]["reason"]
    assert cov["disks"]["label"] == "VM，無 SMART"


def test_coverage_VM_的multipath與DIMM_不可以擅自標成不適用():
    # VM 真的可能有（iSCSI／FC passthrough、虛擬 DIMM）。標「本來就沒有」
    # 會讓「該有卻沒收到」永遠沒人去查——方向比說不出原因更糟
    cov = H.extra_coverage({"multipath": "", "dimm": ""}, {}, is_vm=True)
    assert cov["multipath_detail"]["state"] == "no_output"
    assert cov["dimm"]["state"] == "no_output"


def test_coverage_實體機不可以被標成_不適用():
    cov = H.extra_coverage({"raid_hw": ""}, {}, is_vm=False)
    assert cov["raid_hw_detail"]["state"] == "no_output"


def test_is_vm_row_旗標沒填也要認得出_device_model_的標記():
    assert H._is_vm_row(1, None) is True
    assert H._is_vm_row(0, "PowerEdge R740 (VM)") is True
    assert H._is_vm_row(None, "VMware Virtual Platform") is True
    assert H._is_vm_row(0, "VM") is True
    assert H._is_vm_row(0, "PowerEdge R740") is False
    assert H._is_vm_row(None, None) is False


# ---- 4. reparse_stored 的行為 ----
def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE host_spec (asset_serial TEXT PRIMARY KEY, ip TEXT, platform TEXT, "
                 "spec_json TEXT, raw_json TEXT, collected_at TEXT)")
    # reparse_stored 會 LEFT JOIN hardware 拿 is_vm（coverage 要分得出「VM 形態上沒有」
    # 與「該有卻沒收到」）。測試的表要跟上，不然測到的是另一個 schema。
    conn.execute("CREATE TABLE hardware (asset_serial TEXT PRIMARY KEY, is_vm INTEGER, "
                 "device_model TEXT)")
    return conn


def _mark_vm(conn, serial, device_model="VMware Virtual Platform", is_vm=1):
    conn.execute("INSERT INTO hardware VALUES (?,?,?)", (serial, is_vm, device_model))
    conn.commit()


def test_reparse_從既有_raw_json_撈出新欄位_且不碰_raw_與_collected_at():
    conn = _db()
    raw = {"multipath": MULTIPATH, "mdstat": MDSTAT, "raid_hw": STORCLI, "lscpu": "", "tools": ""}
    conn.execute("INSERT INTO host_spec VALUES (?,?,?,?,?,?)",
                 ("S1", "10.0.0.1", "linux", json.dumps({"raid_hw": True}),
                  json.dumps(raw), "2026-09-01 10:00:00"))
    conn.commit()

    r = H.reparse_stored(conn)
    assert r["updated"] == 1
    row = conn.execute("SELECT * FROM host_spec WHERE asset_serial='S1'").fetchone()
    sj = json.loads(row["spec_json"])
    assert sj["multipath_detail"][0]["wwid"]
    assert sj["raid_hw_detail"]["source"] == "storcli"
    assert sj["coverage"]["multipath_detail"]["state"] == "collected"
    # 原文是證據，不可以被重寫
    assert json.loads(row["raw_json"]) == raw
    # 重判讀沒有讓資料變新——改了 collected_at 會讓人以為剛收過
    assert row["collected_at"] == "2026-09-01 10:00:00"


def test_reparse_保留收集器寫的_net_notes():
    conn = _db()
    conn.execute("INSERT INTO host_spec VALUES (?,?,?,?,?,?)",
                 ("S1", "10.0.0.1", "linux",
                  json.dumps({"net_notes": {"S1": {"error": "沒權限"}}}),
                  json.dumps({"mdstat": MDSTAT}), "2026-09-01 10:00:00"))
    conn.commit()
    H.reparse_stored(conn)
    sj = json.loads(conn.execute("SELECT spec_json FROM host_spec").fetchone()[0])
    # net_notes 不是從 raw 算來的，重算會把「為什麼收不到」弄丟
    assert sj["net_notes"]["S1"]["error"] == "沒權限"


def test_reparse_沒有_raw_json_要回報原因_不可以算成成功或失敗():
    conn = _db()
    conn.execute("INSERT INTO host_spec VALUES ('S1','10.0.0.1','linux',NULL,NULL,'2026-09-01 10:00:00')")
    conn.commit()
    r = H.reparse_stored(conn)
    assert r["updated"] == 0 and r["no_raw"] == 1
    # 「沒有原文可重判」≠「這台沒有 RAID」
    assert "重收" in r["no_raw_detail"][0]["reason"]


def test_reparse_非_linux_算_skipped_不算失敗():
    conn = _db()
    conn.execute("INSERT INTO host_spec VALUES ('A1','10.0.0.2','aix',NULL,'{}','2026-09-01 10:00:00')")
    conn.commit()
    r = H.reparse_stored(conn)
    assert r["skipped"] == 1 and r["no_raw"] == 0 and r["updated"] == 0


def test_reparse_第二次跑沒有變化_不算更新():
    conn = _db()
    conn.execute("INSERT INTO host_spec VALUES (?,?,?,?,?,?)",
                 ("S1", "10.0.0.1", "linux", None, json.dumps({"mdstat": MDSTAT}), "x"))
    conn.commit()
    assert H.reparse_stored(conn)["updated"] == 1
    assert H.reparse_stored(conn)["unchanged"] == 1


def test_reparse_VM_的coverage要標不適用_走_hardware_的旗標():
    conn = _db()
    conn.execute("INSERT INTO host_spec VALUES (?,?,?,?,?,?)",
                 ("S1", "10.0.0.1", "linux", None,
                  json.dumps({"mdstat": MDSTAT, "lscpu": "", "tools": ""}), "x"))
    conn.commit()
    _mark_vm(conn, "S1")
    H.reparse_stored(conn)
    sj = json.loads(conn.execute("SELECT spec_json FROM host_spec").fetchone()[0])
    assert sj["coverage"]["raid_hw_detail"]["label"] == "VM，無 RAID"
    # 這台真的有軟體 RAID，不可以被一併當成不適用
    assert sj["coverage"]["raid_sw_detail"]["state"] == "collected"


def test_reparse_資產表沒有對應列_退回未查_不可以宣告本來就沒有():
    # LEFT JOIN 的 is_vm 會是 NULL。保守方向：說「沒查到」要人去看，
    # 不要擅自說「本來就沒有」
    conn = _db()
    conn.execute("INSERT INTO host_spec VALUES (?,?,?,?,?,?)",
                 ("S9", "10.0.0.9", "linux", None,
                  json.dumps({"mdstat": MDSTAT, "lscpu": "", "tools": ""}), "x"))
    conn.commit()
    H.reparse_stored(conn)
    sj = json.loads(conn.execute("SELECT spec_json FROM host_spec").fetchone()[0])
    assert sj["coverage"]["raid_hw_detail"]["state"] == "not_run"


def test_reparse_只收單台():
    conn = _db()
    for s in ("S1", "S2"):
        conn.execute("INSERT INTO host_spec VALUES (?,?,?,?,?,?)",
                     (s, "10.0.0.1", "linux", None, json.dumps({"mdstat": MDSTAT}), "x"))
    conn.commit()
    r = H.reparse_stored(conn, only_serial="S1")
    assert r["total"] == 1 and r["updated"] == 1
