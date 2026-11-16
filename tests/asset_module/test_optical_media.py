"""2026-10-29 空間告警遇到 DVD／光碟／ISO 掛載就忽略（使用者：「空間告警如果是 DVD 應該忽略」）。全部假資料。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import health_probe  # noqa: E402
import host_spec_collector as hsc  # noqa: E402
import optical_media as om  # noqa: E402
import storage_capacity  # noqa: E402


def test_判斷_光碟算_一般掛載不誤傷():
    # 只有掛載點名稱（舊資料）：只認 /mnt|/media|/run/media 底下最後一層是光碟名的，或 /cdrom、/dvd 本身
    for mnt in ("/mnt/dvd", "/mnt/cdrom", "/run/media/root/RHEL-9-DVD", "/media/CDROM", "/mnt/sr0", "/cdrom", "/dvd", "/media/cd-rom"):
        assert om.is_optical(mnt), mnt
    # 審查 2026-10-29：真的資料目錄不可被藏起來
    for mnt in ("/", "/data", "/data/isolated", "/home/sriram", "/u01/dvdata", "/data/iso_backup", "/u01/dvd_archive",
                "/backup/iso", "/data/iso-2024", "/opt/iso.d", "/export/cd-rom", "/mnt/iso", "/backup/dvd", "/mnt/dvd_backup"):
        assert not om.is_optical(mnt), mnt
    # 有裝置／檔案系統資訊：只信那個
    assert om.is_optical("/x", "/dev/sr0") and om.is_optical("/x", "/dev/cd0")
    assert om.is_optical("/x", "tmp", "iso9660") and om.is_optical("/x", None, "UDF")
    assert not om.is_optical("/mnt/dvd", "/dev/sdb1", "xfs")            # 名稱像 DVD 但是一般磁碟：照常算
    assert not om.is_optical("/backup/iso", "/dev/mapper/vg-iso", "ext4")
    assert not om.is_optical(None) and not om.is_optical("")


def test_收集端df_光碟不進清單():
    df = ("Filesystem 1G-blocks Used Available Use% Mounted on\n"
          "/dev/mapper/rhel-root 50G 20G 30G 40% /\n"
          "/dev/sr0 8G 8G 0G 100% /mnt/dvd\n"
          "/dev/sdb1 100G 10G 90G 10% /data\n"
          "/dev/sdc1 5G 5G 0G 100% /backup/iso\n")
    names = [i["name"] for i in hsc.parse_df(df)]
    assert names == ["/", "/data", "/backup/iso"]          # 光碟排除；名稱像 iso 的一般磁碟照常列
    src = (ROOT / "APP" / "asset-module" / "backend" / "host_spec_collector.py").read_text(encoding="utf-8")
    assert "df -PBG -x tmpfs -x devtmpfs -x overlay -x iso9660 -x udf" in src       # 收集時就不列光碟（唯讀指令只多了排除條件）


def test_健康檢查_Linux_df_光碟不算():
    df = ("Filesystem 1024-blocks Used Available Capacity Mounted on\n"
          "/dev/sda1 1000000 500000 500000 50% /\n"
          "/dev/sr0 8000000 8000000 0 100% /mnt/dvd\n")
    dfi = ("Filesystem Inodes IUsed IFree IUse% Mounted on\n"
           "/dev/sda1 1000 100 900 10% /\n"
           "/dev/sr0 0 0 0 0% /mnt/dvd\n")
    d = health_probe.parse_disks(df, dfi)
    assert [x["mount"] for x in d] == ["/"] and d[0]["level"] == "green"


def test_5_2容量預警_舊資料裡的DVD掛載也排除_整台剩餘比例不被拉低(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    try:
        db.insert_hardware(conn, asset_serial="O-1", hostname="app01", ip="10.99.7.1", physical_location="01_甲機房")
        stor = {"by": "mount", "total_gb": 8 + 100,
                "items": [{"name": "/data", "size_gb": 100, "free_gb": 60}, {"name": "/mnt/dvd", "size_gb": 8, "free_gb": 0}]}
        conn.execute("INSERT INTO host_spec (asset_serial, ip, storage_json, collected_at) VALUES (?,?,?,datetime('now'))",
                     ("O-1", "10.99.7.1", json.dumps(stor)))
        conn.commit()
        rows = storage_capacity.physical(conn)["rows"]
        r = next(x for x in rows if x["asset_serial"] == "O-1")
        assert r["total_gb"] == 100 and r["free_gb"] == 60 and r["free_pct"] == 60.0      # 沒有 DVD 的 8G／0G
    finally:
        conn.close()
