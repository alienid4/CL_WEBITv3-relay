"""V2 移植第 17 批（2.052.0）：V65 5.1 機房平面圖的只讀四項＋跨角度搜尋併進「找設備」框。全部假資料。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "APP" / "asset-module" / "frontend"
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import rack_layout as rl  # noqa: E402


def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn):
    for s, h, ip, rack in (("A-1", "app01", "10.99.6.1", "B1/30-31U"), ("A-3", "app02", "10.99.6.2", "B1/20U"),
                           ("C-1", "app03", "10.99.6.3", "B2/5U")):
        db.insert_hardware(conn, asset_serial=s, hostname=h, ip=ip, physical_location="01_甲機房", rack_no=rack,
                           device_model="DELL R750")
    conn.commit()


def _ours(vn):
    return [r for r in vn.RESULTS if r["page"] == "5.1 機房平面圖" and ("超過一年未盤" in r["name"] or "儲存設備頁籤" in r["name"])]


def test_從沒盤過有清單_數字等於清單_而且是未盤的子集(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        room = next(r for r in rl.datacenter(conn)["rooms"] if r["room"] == "甲機房")
        assert len(room["audit_stale_keys"]) == 3                         # 都沒盤過
        assert room["audit_never_keys"] == room["audit_stale_keys"] and room["audit_never_count"] == 3
        per_rack = sum(sum(1 for k in t["keys"] if k in set(room["audit_stale_keys"])) for t in room["racks"])
        assert per_rack == 3
    finally:
        conn.close()


def test_verify_numbers_未盤與儲存設備不變式_綠與突變紅(tmp_path, monkeypatch):
    import verify_numbers as vn
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        vn.RESULTS.clear()
        vn.check_v2_batch2(conn)
        got = _ours(vn)
        assert len(got) == 2 and all(r["ok"] for r in got), got
        real = rl.datacenter

        def broken(c, *a, **kw):
            d = real(c, *a, **kw)
            for r in d["rooms"]:
                if r.get("audit_never_keys"):
                    r["audit_never_keys"] = r["audit_never_keys"][1:]        # 數字還是 3、清單少一筆
            return d
        monkeypatch.setattr(rl, "datacenter", broken)
        vn.RESULTS.clear()
        vn.check_v2_batch2(conn)
        bad = [r["name"] for r in _ours(vn) if not r["ok"]]
        assert any("超過一年未盤" in n for n in bad), bad
    finally:
        conn.close()


def test_儲存設備不變式_對不上會紅(tmp_path, monkeypatch):
    import storage_capacity
    import verify_numbers as vn
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        monkeypatch.setattr(storage_capacity, "room_summary", lambda c: [{"room": "甲機房", "capacity_mib": 1.0, "in_use_mib": 0.0,
                                                                           "volume_count": 1, "near_full_count": 0}])
        vn.RESULTS.clear()
        vn.check_v2_batch2(conn)
        bad = [r["name"] for r in _ours(vn) if not r["ok"]]
        assert any("儲存設備頁籤" in n for n in bad), bad
    finally:
        conn.close()


def test_平面圖頁_四項只讀與跨角度搜尋併在找設備框():
    d = _src("pages/v2/datacenter.vue")
    # 1 超過一年未盤＋下鑽（機房與每櫃）
    assert "stale: '超過一年未盤', never: '從沒盤過'" in d and "drillUrl('stale')" in d and "drillUrl('never')" in d
    assert "drillUrl('stale', r.rack)" in d
    # 2 儲存設備頁籤（同一支 /api/storage/rooms，表格是 V2Table）
    assert "{ k: 'storage', label: '儲存設備' }" in d and "'/api/storage/rooms'" in d and "tab === 'storage'" in d
    # 3 平面圖匯出 Excel（限管理員，跟後端 admin 匯出一致）
    assert "/api/rack-diagram/export.xlsx?room=" in d and 'v-if="isAdmin"' in d
    # 4 電力、資料品質上色
    assert '<option value="power">顏色依：電力</option>' in d and '<option value="dq">顏色依：資料品質</option>' in d
    # 跨角度搜尋：同一個 sq，不另開搜尋框
    assert "'/api/racks/search'" in d and "watch(sq," in d
    assert d.count('type="search"') == 1
    # 寫入（批次標記）2.059.3 才搬進來，而且只走預覽＋確認那一條（見 test_v2_datacenter_bulk.py）
    assert d.count("'/api/racks/state/bulk'") == 1 and "data-test=\"bulk-confirm\"" in d
