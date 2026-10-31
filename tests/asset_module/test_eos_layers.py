"""Codex 64／004：EOS 台數單位＝machine_key；每個項目揭露底下機器的層次（整機／元件／管理口），
不把整套 EOS 複製到元件；關聯無法判斷時標 unknown。用真的 routers.vulns.eos_summary＋虛構父子關聯。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import eos  # noqa: E402
import normalize  # noqa: E402
import overlap_queue  # noqa: E402
import rack_layout  # noqa: E402
from routers import vulns  # noqa: E402

LOC = "01_Fixture Room"


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)

    def hw(serial, host, ip, **kw):
        kw.setdefault("physical_location", LOC)
        db.insert_hardware(c, asset_serial=serial, hostname=host, ip=ip, **kw)

    # 虛構整套：兩個同框一體機元件（人工合併為同一台）＋一台伺服器與掛在它底下的管理口＋一台無機櫃資料的主機
    hw("FX-EX1", "fx-exa1", "192.0.2.21", os="Oracle Linux 8", device_model="Oracle Exadata X10M", rack_no="M12/1-10U")
    hw("FX-EX2", "fx-exa2", "192.0.2.22", os="Oracle Linux 8", device_model="Oracle Exadata X10M", rack_no="M12/1-10U")
    hw("FX-SRV", "fx-srv", "192.0.2.31", os="Red Hat Enterprise Linux 8", device_model="HPE DL380 Gen10", rack_no="M10/5-6U")
    hw("FX-ILO", "fx-srv-ilom", "192.0.2.32", os="iLO 5", device_model="HPE iLO", rack_no="M10/5-6U")
    hw("FX-LONE", "fx-lone", "192.0.2.41", os="Red Hat Enterprise Linux 9", physical_location=None)
    c.commit()
    exa = [m for m in rack_layout.machines(c) if m["asset_serial"] in ("FX-EX1", "FX-EX2")]
    overlap_queue.decide(c, exa[0]["room"], exa[0]["rack"], [m["key"] for m in exa], "merge", "tester")
    return c


def _all_items(res):
    out = []
    for b in ("host_os", "firmware", "software", "insufficient", "other", "hardware"):
        out += res[b]["items"]
    return out


def test_eos項目揭露層次_整機元件管理口各自一台_加總等於count(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    # 不同機型各給不同的虛構 EOS 日期（整套元件 Exadata 2030-01-01、其他 2031-02-01），並在下面斷言各自保留、不互相複製
    def _fake(canon):
        d = "2030-01-01" if "exadata" in canon.lower() else "2031-02-01"
        return {"eos_date": d, "source_url": "https://example.invalid/fixture", "confidence": "confirmed", "note": None}

    monkeypatch.setattr(eos, "lookup_hardware_eos", _fake)
    res = vulns.eos_summary(session=None, conn=c)
    assert res["meta"]["unit"].startswith("machine_key")
    items = _all_items(res)
    assert items and all("layers" in i and "layer_basis" in i for i in items)
    for i in items:
        assert sum(i["layers"].values()) == i["count"], i["name"]      # 層次加總＝台數，不另算一套
    roles = {r for i in items for r in i["layers"]}
    assert "appliance" in roles                                          # 人工合併的 Exadata 元件被看見
    assert "mgmt_attached" in roles                                      # 掛在本體的管理口被看見
    # 元件逐台各算一台（不被併成 1），也不因為屬於「整套」就複製整套 EOS 到元件
    # （同一台會同時出現在「作業系統」與「硬體型號」兩個分頁，各分頁分開看；不跨分頁相加）
    hw_exa = [i for i in res["hardware"]["items"] if i["layers"].get("appliance")]
    assert sum(i["layers"]["appliance"] for i in hw_exa) == 2
    assert {i["eos_date"] for i in hw_exa} == {"2030-01-01"}
    others = [i for i in res["hardware"]["items"] if not i["layers"].get("appliance")]
    assert others and {i["eos_date"] for i in others} == {"2031-02-01"}      # 其他機型保留自己的日期


def test_對帳橋算不出來_全部標unknown_不擋EOS(tmp_path, monkeypatch):
    c = _conn(tmp_path)
    import count_bridge

    def boom(*a, **k):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(count_bridge, "compute", boom)
    res = vulns.eos_summary(session=None, conn=c)
    items = _all_items(res)
    assert items
    for i in items:
        assert set(i["layers"]) == {"unknown"} and i["layers"]["unknown"] == i["count"]


def test_eos_summary在唯讀連線與缺表庫下不寫庫_構成與計數同可寫路徑(tmp_path):
    """Codex 73：layers 會呼叫 count_bridge；它曾在讀取時建 count_role_override 表（唯讀連線拋錯、被 summary 吞成 unknown）。
    現在 mode=ro＋query_only＋trace：沒有非 SELECT 語句、庫檔位元組不變、layers 不是全 unknown、count 與可寫路徑一致。"""
    import hashlib
    import sqlite3

    c = _conn(tmp_path)
    w_res = vulns.eos_summary(session=None, conn=c)
    p = tmp_path / "t.db"
    for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'rack_%' OR name LIKE 'model_spec%' "
                          "OR name LIKE 'count_role%' OR name LIKE 'scan_coverage%' OR name LIKE 'collect_dispatch%')").fetchall():
        c.execute(f'DROP TABLE IF EXISTS "{n}"')
    c.commit()
    c.close()
    import fleet_cache
    fleet_cache.clear()                                             # 清跨請求快取，避免吃到可寫路徑的結果
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    ro = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    ro.execute("PRAGMA query_only=ON")
    st: list[str] = []
    ro.set_trace_callback(st.append)
    r_res = vulns.eos_summary(session=None, conn=ro)
    ro.close()
    bad = [x for x in st if not x.lstrip().upper().startswith(("SELECT", "PRAGMA", "WITH", "BEGIN", "COMMIT"))]
    assert not bad, bad[:3]
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before
    for b in ("host_os", "firmware", "software", "insufficient", "other", "hardware"):
        wi = {i["name"]: i["count"] for i in w_res[b]["items"]}
        ri = {i["name"]: i["count"] for i in r_res[b]["items"]}
        assert wi == ri, b                                           # 計數不變
    items = [i for b in ("host_os", "hardware") for i in r_res[b]["items"]]
    assert any(set(i["layers"]) - {"unknown"} for i in items)        # 構成不是整批 unknown
