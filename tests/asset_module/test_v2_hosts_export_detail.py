"""N13：2.1 主機查詢匯出要等價舊 /hosts 的 13 欄（機房、環境、虛實、OS、系統數、VIP、服務、帳號、系統(AP ID)、多重登記）。
detail=1 才附加這些欄，畫面列照舊只回 LIST_FIELDS；服務／帳號數跟舊 /hosts（host_view）同一份算法。全部假資料（10.99.x）。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import host_view  # noqa: E402
import v2_hosts  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    hw = [
        dict(asset_serial="S-1", hostname="web01", ip="10.99.1.1", os="Red Hat Enterprise Linux 9", asset_status="使用中",
             api_id="AP01", big_ip_vip="10.99.8.1", physical_location="內湖機房", environment="正式", asset_name="網站前台"),
        dict(asset_serial="S-2", hostname="web01", ip="10.99.1.1", os="Red Hat Enterprise Linux 9", asset_status="使用中",
             api_id="AP02", big_ip_vip="無", physical_location="內湖機房", environment="正式", asset_purpose="報表"),
        dict(asset_serial="S-3", hostname="db01", ip="10.99.1.3", os="AIX 7.2", asset_status="使用中",
             api_id="AP01", big_ip_vip="N/A", physical_location="板橋機房", environment="測試"),
    ]
    for h in hw:
        db.insert_hardware(c, **h)
    for port in (22, 443, 8080):
        c.execute("INSERT INTO host_service (ip, proto, port, source, last_seen) VALUES (?, 'tcp', ?, 'collect', '2026-11-01')",
                  ("10.99.1.1", port))
    c.execute("INSERT INTO host_account (ip, username, uid) VALUES ('10.99.1.1', 'app', '1001')")
    c.execute("INSERT INTO host_account (ip, username, uid) VALUES ('10.99.1.3', 'oracle', '1002')")
    c.commit()
    yield c
    c.close()


def test_畫面列不帶附加欄_匯出才帶(conn):
    plain = v2_hosts.listing(conn, "all", size=0)
    assert all(set(r) <= set(v2_hosts.LIST_FIELDS) for r in plain["rows"])
    full = v2_hosts.listing(conn, "all", size=0, detail=True)
    assert [r["uid"] for r in full["rows"]] == [r["uid"] for r in plain["rows"]]      # 同一份列、同樣順序
    for r in full["rows"]:
        assert set(v2_hosts.DETAIL_FIELDS) <= set(r)


def test_十三欄內容_跟舊主機清單同口徑(conn):
    rows = {r["name"]: r for r in v2_hosts.listing(conn, "all", size=0, detail=True)["rows"]}
    old = {h["hostname"]: h for h in host_view.list_hosts(conn)["hosts"]}
    w = rows["web01"]
    assert w["records"] == 2 and w["system_count"] == 2 and w["api_text"] == "AP01｜AP02"
    assert w["vip_count"] == 1                    # 「無」不算 VIP
    assert w["service_count"] == 3 and w["account_count"] == 1
    assert w["regs_text"] == "S-1:網站前台｜S-2:報表"
    assert w["env"] == "正式" and w["os"] == "Red Hat Enterprise Linux 9" and w["vm_text"] in ("VM", "實體")
    d = rows["db01"]
    assert d["vip_count"] == 0 and d["service_count"] == 0 and d["account_count"] == 1 and d["env"] == "測試"
    for name, r in rows.items():                  # 跟舊 /hosts 同一份計數
        o = old[name]
        assert (r["system_count"], r["vip_count"], r["service_count"], r["account_count"], r["records"]) == \
            (o["system_count"], o["vip_count"], o["service_count"], o["account_count"], o["reg_count"]), name


def test_機房環境條件也套到匯出(conn):
    r = v2_hosts.listing(conn, "all", size=0, detail=True, envs="測試")
    assert [x["name"] for x in r["rows"]] == ["db01"]
