"""一台主機掛了哪些入口（VIP），以及同一入口後面還有誰（2026-09-18）。

使用者：「一個 IP 綁多 VIP 這種怎麼呈現，你要想辦法」。221 實測 VIP 跟主機是多對多，
所以每個入口要帶「這台上的服務」和「同一入口後面的其他主機」。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import vip_view  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    ins = lambda **k: db.insert_hardware(c, asset_status=k.pop("asset_status", "使用中"), **k)
    # 這台：同 IP 三筆登記，兩個 VIP
    ins(asset_serial="A-1", hostname="web1", ip="192.0.2.10", big_ip_vip="198.51.100.28", asset_name="期貨官網")
    ins(asset_serial="A-2", hostname="web1", ip="192.0.2.10", big_ip_vip="198.51.100.28", asset_name="財管交易")
    ins(asset_serial="A-3", hostname="web1", ip="192.0.2.10", big_ip_vip="198.51.100.34", asset_name="證券官網")
    # 同入口的夥伴
    ins(asset_serial="B-1", hostname="web2", ip="192.0.2.11", big_ip_vip="198.51.100.28", asset_name="期貨官網")
    # 已報廢、但也登記同一個 VIP → 不可以算備援
    ins(asset_serial="Z-9", hostname="old", ip="192.0.2.99", big_ip_vip="198.51.100.28", asset_status="報廢")
    # 叢集 IP、填錯欄位、無
    ins(asset_serial="A-4", hostname="web1", ip="192.0.2.10", big_ip_vip="198.51.100.187-w-cluster", asset_name="SQL")
    ins(asset_serial="A-5", hostname="web1", ip="192.0.2.10", big_ip_vip="N-030", asset_name="誤填")
    ins(asset_serial="A-6", hostname="web1", ip="192.0.2.10", big_ip_vip="無", asset_name="沒VIP")
    c.commit()
    return c


def test_每個入口帶這台的服務與同池主機(tmp_path):
    c = _conn(tmp_path)
    r = vip_view.host_vips(c, "web1", "192.0.2.10")
    v = {x["vip"]: x for x in r["vips"]}
    assert set(v) == {"198.51.100.28", "198.51.100.34", "198.51.100.187"}
    e28 = v["198.51.100.28"]
    assert [s["name"] for s in e28["services_here"]] == ["期貨官網", "財管交易"]
    assert e28["pool_size"] == 2, "web1＋web2，報廢那台不算"
    assert e28["pool"][0]["is_self"], "自己排第一"
    assert e28["retired_also"] == 1, "報廢那台要另外講出來，不吞掉"
    assert v["198.51.100.34"]["pool_size"] == 1, "CIA 登記上只有這台"


def test_叢集IP與填錯欄位要分開(tmp_path):
    c = _conn(tmp_path)
    r = vip_view.host_vips(c, "web1", "192.0.2.10")
    v = {x["vip"]: x for x in r["vips"]}
    assert v["198.51.100.187"]["kind"] == vip_view.KIND_CLUSTER
    assert [m["value"] for m in r["misfiled"]] == ["N-030"]
    assert r["misfiled"][0]["kind"] == vip_view.KIND_APID
    assert "無" not in v, "「無」是沒有 VIP，不是一個入口"


def test_報廢那筆不算這台的入口(tmp_path):
    """同 IP 同主機名，但報廢的登記是「上一台」（v1.213.1 規則），它的 VIP 不屬於這台。"""
    c = _conn(tmp_path)
    db.insert_hardware(c, asset_serial="A-7", hostname="web1", ip="192.0.2.10",
                       big_ip_vip="198.51.100.77", asset_status="報廢")
    c.commit()
    r = vip_view.host_vips(c, "web1", "192.0.2.10")
    assert "198.51.100.77" not in {x["vip"] for x in r["vips"]}


def test_沒有主機名或IP就不猜(tmp_path):
    c = _conn(tmp_path)
    assert vip_view.host_vips(c, "", "192.0.2.10")["vips"] == []
    assert vip_view.host_vips(c, "web1", "0.0.0.0")["vips"] == []
