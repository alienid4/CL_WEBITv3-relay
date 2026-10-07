"""月報／EOS 平台表改成照主機算（2026-09-17）。

使用者拿這個月的平台表（合計 6084）跟上個月的報告（合計 3654）對照，問
「這數值不對，圖二是上個月的，是不是計算錯誤」。

查證：`classify_assets()` 是逐筆掃 `hardware`，**一列算一台**，而同一台機器會被
CIA／dynassets／RVTools 各登記一筆。證據是使用者自己的兩張畫面——平台表寫
「總計 8,085 台」，同一份資料的納管漏斗寫「共 5,405 台（已收起 2,681 筆重複登記）」。
納管漏斗 v1.188.0 已改成照主機算，報告這邊漏掉了。

要守的：

1. 主機名＋IP **都相同**才算同一台（跟漏斗、資產查詢頁同一個保守判準）
2. 少一邊的不收——只有 IP 相同可能是 DHCP 回收；只有主機名相同可能是搬遷
3. 留**資料最完整**的那筆當代表：留到空白那筆會讓這台掉進「需確認」，等於憑空生出待辦
4. **不刪資料**：只在統計上收，`dup_serials` 要留著給人追
5. 收了幾筆要講出來（`duplicates_merged`）——數字變動沒有說明，人只會更不信
6. 各平台加總＋不列入＋已退役 = 去重後的母體（對帳仍成立）
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import system_report as sr  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        yield c
    finally:
        c.close()


def add(conn, serial, hostname, ip, os_=None, model=None, status="使用中", env=None, loc=None):
    db.insert_hardware(conn, asset_serial=serial, hostname=hostname, ip=ip, os=os_,
                       device_model=model, asset_status=status, environment=env,
                       physical_location=loc)


def test_同主機名同IP收成一台(conn):
    add(conn, "CIA-1", "SRV01", "192.0.2.10", os_="Red Hat Enterprise Linux 8.8")
    add(conn, "DYN-1", "srv01 ", " 192.0.2.10", os_="Red Hat Enterprise Linux 8.8")
    add(conn, "RVT-1", "SRV01", "192.0.2.10", os_="Red Hat Enterprise Linux 8.8")
    conn.commit()
    assets = sr.classify_assets(conn)
    assert len(assets) == 1, "大小寫與前後空白不算不同台"
    assert assets[0]["dup_count"] == 3
    assert set(assets[0]["dup_serials"]) == {"CIA-1", "DYN-1", "RVT-1"}


def test_只有一邊相同不收(conn):
    add(conn, "A-1", "SRV01", "192.0.2.10")
    add(conn, "A-2", "SRV01", "192.0.2.11")     # 換網段＝搬遷
    add(conn, "A-3", "SRV02", "192.0.2.10")     # DHCP 回收給別台
    conn.commit()
    assert len(sr.classify_assets(conn)) == 3


def test_缺主機名或IP不收(conn):
    add(conn, "A-1", None, "192.0.2.20")
    add(conn, "A-2", None, "192.0.2.20")
    add(conn, "A-3", "SRV09", None)
    add(conn, "A-4", "SRV09", None)
    conn.commit()
    assert len(sr.classify_assets(conn)) == 4, "少一邊就無法確定是同一台，寧可不收"


def test_留資料最完整的那筆(conn):
    add(conn, "EMPTY", "SRV05", "192.0.2.30")                       # OS 空白
    add(conn, "FULL", "SRV05", "192.0.2.30",
        os_="Microsoft Windows Server 2019", model="HP DL380", env="正式", loc="內湖機房")
    conn.commit()
    a = sr.classify_assets(conn)
    assert len(a) == 1
    assert a[0]["asset_serial"] == "FULL", "留到空白那筆會讓這台掉進「需確認」，憑空生出待辦"
    assert a[0]["os_canonical"] and a[0]["eos_status"] != "需確認"
    assert a[0]["dup_count"] == 2 and "EMPTY" in a[0]["dup_serials"]


def test_非退役優先當代表(conn):
    add(conn, "OLD", "SRV06", "192.0.2.31", os_="Red Hat Enterprise Linux 6.5", status="報廢")
    add(conn, "NOW", "SRV06", "192.0.2.31", os_="Red Hat Enterprise Linux 9.4", status="使用中")
    conn.commit()
    a = sr.classify_assets(conn)
    assert len(a) == 1 and a[0]["asset_serial"] == "NOW"
    assert a[0]["retired"] is False, "報廢那筆的 OS 常是舊的，不可以拿它當代表"


def test_平台表講得出收了幾筆_且對帳成立(conn):
    add(conn, "CIA-1", "SRV01", "192.0.2.10", os_="Red Hat Enterprise Linux 8.8")
    add(conn, "DYN-1", "SRV01", "192.0.2.10", os_="Red Hat Enterprise Linux 8.8")
    add(conn, "CIA-2", "SRV02", "192.0.2.11", os_="Microsoft Windows Server 2019")
    add(conn, "CIA-3", "SRV03", "192.0.2.12", os_="Red Hat Enterprise Linux 7.9", status="報廢")
    conn.commit()
    pl = sr.platform_lifecycle(conn)
    assert pl["duplicates_merged"] == 1, "收了幾筆要講出來，不能靜默少掉"
    assert pl["total"] + pl["excluded_total"] + pl["retired_excluded"] == len(sr.classify_assets(conn))


def test_去重不會動到資料庫(conn):
    add(conn, "CIA-1", "SRV01", "192.0.2.10")
    add(conn, "DYN-1", "SRV01", "192.0.2.10")
    conn.commit()
    sr.classify_assets(conn)
    assert conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 2, \
        "只在統計上收成一台，不刪任何列——要留哪一筆是人的決定"


def test_下鑽跟加總同一份口徑(conn):
    add(conn, "CIA-1", "SRV01", "192.0.2.10", os_="Red Hat Enterprise Linux 8.8")
    add(conn, "DYN-1", "SRV01", "192.0.2.10", os_="Red Hat Enterprise Linux 8.8")
    conn.commit()
    pl = sr.platform_lifecycle(conn)
    rows = sr.drill_platform(conn, platform="Linux")
    total_linux = next(r["total"] for r in pl["rows"] if r["platform"] == "Linux")
    assert total_linux == len(rows) == 1, "格子上的數字必須等於點進去的筆數"
