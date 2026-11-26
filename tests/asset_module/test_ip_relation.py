"""共用 IP／VIP 待核清單：訊號只排序、不改台數；人工判定可標、標過不再出現、成員變了才重現；讀取不寫庫。"""
import hashlib
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import ip_relation as ipr  # noqa: E402
import system_stats  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)

    def hw(serial, host, ip, **kw):
        db.insert_hardware(c, asset_serial=serial, hostname=host, ip=ip, **kw)

    hw("D-1", "db-node1", "192.0.2.11", device_model="Fixture Server")
    hw("D-2", "db-node2", "192.0.2.12", device_model="Fixture Server")
    hw("D-3", "db-scan", "192.0.2.50", asset_purpose="RAC SCAN")            # 名稱／用途含 scan
    hw("S-1", "app-a", "192.0.2.77", device_model="Fixture Server")          # 兩台不同主機共用一個 IP（登錄錯誤或 VIP）
    hw("S-2", "app-b", "192.0.2.77", device_model="Fixture Server")
    hw("L-1", "lb-1", "192.0.2.90", device_model="F5 BIG-IP i2600")          # F5
    hw("V-1", "web-1", "192.0.2.31", big_ip_vip="192.0.2.200")               # VIP 欄有值（只有一台＝已登記的 VIP，不需待核）
    hw("V-2", "ora-1", "192.0.2.41", big_ip_vip="192.0.2.210")               # 同一個 VIP 掛兩台（RAC／叢集）→ 待核
    hw("V-3", "ora-2", "192.0.2.42", big_ip_vip="192.0.2.210")
    hw("N-1", "web-2", "192.0.2.32", big_ip_vip="無")                         # 「無」不算
    hw("B-1", "", "192.0.2.77")                                               # 空主機名：不算另一台
    hw("R-1", "old-a", "192.0.2.88", asset_status="報廢")
    hw("R-2", "old-b", "192.0.2.88")
    c.commit()
    return c, p


def _by_key(res):
    return {x["item_key"]: x for x in res["pending"] + res["confirmed"] + res["dismissed"]}


def test_訊號只排序_各類都有命中理由(tmp_path):
    c, _ = _conn(tmp_path)
    k = _by_key(ipr.listing(c))
    assert "ip:192.0.2.77" in k and {s["code"] for s in k["ip:192.0.2.77"]["signals"]} == {"shared_ip"}
    codes = lambda key: {s["code"] for s in k[key]["signals"]}  # noqa: E731
    mk = lambda host, ip: "mk:" + system_stats.machine_key(host, ip, "x")  # noqa: E731
    assert any("lb_model" in codes(i) for i in k if i.startswith("mk:"))
    assert "ip:192.0.2.210" in k and codes("ip:192.0.2.210") == {"vip_shared"}   # 同一 VIP 掛兩台
    assert "ip:192.0.2.200" not in k and not any(any(m["serial"] == "V-1" for m in it["machines"]) for it in k.values())   # 單獨 VIP 欄不列待核
    assert ipr.listing(c)["registered_vip"] >= 3
    assert any("name_keyword" in codes(i) for i in k if i.startswith("mk:"))
    for it in k.values():
        assert it["signals"] and all(s["label"] and s["detail"] for s in it["signals"])


def test_無值VIP_空主機名_退役列不產生訊號(tmp_path):
    c, _ = _conn(tmp_path)
    k = _by_key(ipr.listing(c))
    assert "ip:192.0.2.88" not in k                                           # 退役列不拿來當共用證據
    assert not any(any(m["serial"] == "N-1" for m in it["machines"]) for it in k.values())   # vip 欄「無」
    assert len(k["ip:192.0.2.77"]["machines"]) == 2                           # 空主機名那筆不算另一台


def test_不改台數也不寫庫(tmp_path):
    c, p = _conn(tmp_path)
    before_rows = c.execute("SELECT count(*) FROM hardware").fetchone()[0]
    c.close()
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    ro = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    ro.execute("PRAGMA query_only=ON")
    st: list[str] = []
    ro.set_trace_callback(st.append)
    res = ipr.listing(ro)
    ro.close()
    assert res["counts"]["pending"] >= 4
    assert not [x for x in st if not x.lstrip().upper().startswith(("SELECT", "PRAGMA", "WITH", "BEGIN", "COMMIT"))]
    assert hashlib.sha256(p.read_bytes()).hexdigest() == h
    c2 = db.get_connection(p)
    assert c2.execute("SELECT count(*) FROM hardware").fetchone()[0] == before_rows


def test_判定_標過不再出現_復原回來_成員變了重現(tmp_path):
    c, _ = _conn(tmp_path)
    key = "ip:192.0.2.77"
    ipr.review(c, key, "not_service", None, None, "u")
    r = ipr.listing(c)
    assert key not in {x["item_key"] for x in r["pending"]} and key in {x["item_key"] for x in r["dismissed"]}
    rid = _by_key(r)[key]["review"]["id"]
    ipr.undo(c, rid, "u", "誤標")
    assert key in {x["item_key"] for x in ipr.listing(c)["pending"]}
    ipr.review(c, key, "not_service", None, None, "u")
    db.insert_hardware(c, asset_serial="S-3", hostname="app-c", ip="192.0.2.77")      # 又多一台共用 → 指紋變
    c.commit()
    again = {x["item_key"]: x for x in ipr.listing(c)["pending"]}
    assert key in again and again[key].get("reopened") and "變動" in again[key]["reopened_note"]


def test_共用服務IP要選關係類型_資料錯誤要寫說明_不在範圍的項目擋下(tmp_path):
    c, _ = _conn(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        ipr.review(c, "ip:192.0.2.77", "shared_service", None, None, "u")
    with pytest.raises(ValueError):
        ipr.review(c, "ip:192.0.2.77", "shared_service", "亂寫", None, "u")
    with pytest.raises(ValueError):
        ipr.review(c, "ip:192.0.2.77", "data_error", None, "", "u")
    with pytest.raises(LookupError):
        ipr.review(c, "ip:203.0.113.9", "not_service", None, None, "u")
    ipr.review(c, "ip:192.0.2.77", "shared_service", "資料庫 VIP", "x", "u")
    got = _by_key(ipr.listing(c))["ip:192.0.2.77"]
    assert got["review"]["relation_type"] == "資料庫 VIP" and got["verdict_label"] == "共用服務 IP"
    assert ipr.listing(c)["counts"]["confirmed"] == 1
