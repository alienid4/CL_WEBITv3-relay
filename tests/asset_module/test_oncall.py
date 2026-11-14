"""值班一頁卡（v1.500.0）：IP／主機名／資產序號／APID 都要解得到，對到多台不自動挑。"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import daily_digest  # noqa: E402
import db  # noqa: E402
import oncall  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "o.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="CIA-1", hostname="app01.corp.example.com", ip="10.99.5.1,10.99.5.2",
                       os="RHEL 9", device_model="HP DL380", environment="正式", asset_status="使用中",
                       api_id="N-100", physical_location="02_機房A", rack_no="M03/20-21U",
                       owner="王大明", custodian="李小華", user_name="陳AP", availability=3)
    db.insert_hardware(c, asset_serial="CIA-2", hostname="app02", ip="10.99.5.3", os="RHEL 9",
                       environment="正式", asset_status="使用中", api_id="N-100")
    db.insert_hardware(c, asset_serial="CIA-3", hostname="db01", ip="10.99.6.1", os="AIX",
                       asset_status="使用中")
    c.execute("INSERT INTO business_system (api_id, name, ap_department, ap_owner) "
              "VALUES ('N-100','網銀','數金部','張主管')")
    c.execute("INSERT INTO personnel (asset_serial, person_name, phone, proxy1, proxy1_phone) "
              "VALUES ('CIA-1','王大明','1234','林代理','5678')")
    c.commit()
    return c


def test_IP解到一台_一格兩個IP也認(tmp_path):
    c = _conn(tmp_path)
    r = oncall.lookup(c, "10.99.5.2")
    assert r["mode"] == "machine" and r["matched_by"] == "IP"
    card = r["card"]
    assert card["what"]["serials"] == ["CIA-1"]
    assert card["what"]["ips"] == ["10.99.5.1", "10.99.5.2"]
    assert card["where"]["room"] == "機房A" and card["where"]["u_start"] == 20
    assert card["systems"][0]["name"] == "網銀" and card["systems"][0]["severity"] == "重大"
    assert card["who"]["contacts"][0]["phone"] == "1234"
    assert card["who"]["ap_owner"] == "張主管"
    assert "link" in card["impact"]


def test_主機名去網域不分大小寫(tmp_path):
    c = _conn(tmp_path)
    r = oncall.lookup(c, "APP01")
    assert r["mode"] == "machine" and r["matched_by"] == "主機名"
    assert r["card"]["what"]["serials"] == ["CIA-1"]


def test_資產序號(tmp_path):
    c = _conn(tmp_path)
    r = oncall.lookup(c, "cia-3")
    assert r["mode"] == "machine" and r["matched_by"] == "資產序號"
    # 沒登錄聯絡人要講「未登錄」，不是空白
    assert r["card"]["who"]["contacts_note"] == oncall.NOT_REGISTERED
    assert r["card"]["who"]["owner"] == oncall.NOT_REGISTERED


def test_APID回系統與機器清單(tmp_path):
    c = _conn(tmp_path)
    r = oncall.lookup(c, "n-100")
    assert r["mode"] == "system"
    assert r["system"]["name"] == "網銀"
    assert sorted(x["asset_serial"] for x in r["candidates"]) == ["CIA-1", "CIA-2"]


def test_模糊多台回候選_不自動挑(tmp_path):
    c = _conn(tmp_path)
    r = oncall.lookup(c, "app0")
    assert r["mode"] == "candidates" and len(r["candidates"]) == 2
    assert oncall.lookup(c, "zzz")["mode"] == "none"
    assert oncall.lookup(c, "")["mode"] == "none"


def test_最近變化來自快照(tmp_path):
    c = _conn(tmp_path)
    now = datetime.now()
    daily_digest.write_snapshot(c, now - timedelta(days=1))
    c.execute("UPDATE hardware SET os = 'RHEL 10' WHERE asset_serial = 'CIA-3'")
    c.commit()
    daily_digest.write_snapshot(c, now)
    ch = oncall.lookup(c, "db01")["card"]["changes"]
    assert any(x["category"] == "os" and x["after"] == "RHEL 10" for x in ch)
