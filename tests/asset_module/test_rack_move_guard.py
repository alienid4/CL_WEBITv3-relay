"""拖拉搬移／調高度（整個箱子一起搬、目標有人就拒絕、高度更正）與寫入守門（登入停用時可要求管理密碼）。全部假資料。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import auth  # noqa: E402
import db  # noqa: E402
import rack_layout as rl  # noqa: E402
import write_guard as wg  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn):
    def hw(serial, host, ip, rack, model, hw_serial=None):
        db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location="01_甲機房",
                           rack_no=rack, device_model=model, hw_serial=hw_serial)
    hw("F-1", "FakeStore-FS5300", "10.99.5.154", "B10/35U", "IBM FS5300", hw_serial="78F21WV")   # 3 筆＝同一台
    hw("F-2", "FakeStore-FS5300", "10.99.5.155", "B10/35U", "IBM FS5300")
    hw("F-3", "FakeStore-FS5300", "10.99.5.156", "B10/35U", "IBM FS5300")
    hw("A-1", "app01", "10.99.6.1", "B10/20U", "DELL R750")
    hw("A-2", "app02", "10.99.6.2", "B11/10U", "DELL R750")
    conn.commit()


def _pos(conn, serial):
    m = next(x for x in rl.machines(conn) if x["asset_serial"] == serial)
    return m["rack"], m["u_start"], m["u_end"]


def test_整箱搬移_成員跟著走_目標有人拒絕(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        before = rl.datacenter(conn)["total_racked"]
        key = next(x["key"] for x in rl.machines(conn) if x["asset_serial"] == "F-2")
        with pytest.raises(rl.Occupied) as e:
            rl.move_box(conn, key, "甲機房", "B10", 20, 1, "拖拉更正", "未登入@10.99.0.1")
        assert "app01" in str(e.value)                                    # 講出是誰佔著
        with pytest.raises(ValueError):
            rl.move_box(conn, key, "甲機房", "Z9", 1, 1, "x", "u")           # 沒有這一櫃
        with pytest.raises(ValueError):
            rl.move_box(conn, key, "甲機房", "B11", 30, 1, "", "u")          # 備註必填（沿用位置更正規則）
        r = rl.move_box(conn, key, "甲機房", "B11", 30, 1, "拖拉更正 2026-10-25", "未登入@10.99.0.1")
        assert len(r["moved"]) == 3                                       # 三筆一起搬
        assert {_pos(conn, s) for s in ("F-1", "F-2", "F-3")} == {("B11", 30, 30)}
        assert rl.datacenter(conn)["total_racked"] == before              # 台數不變
        rows = [x for x in rl.pending_cia_rows(conn) if x["更正類型"] == "位置更正"]
        assert len(rows) == 3 and all(x["盤點人"] == "未登入@10.99.0.1" for x in rows)
        # 調高度：同櫃、35U → 30-31U（高度變了）→ 高度更正
        r2 = rl.move_box(conn, key, "甲機房", "B11", 30, 2, "拖拉更正 2026-10-25", "u")
        assert (r2["u_start"], r2["u_end"]) == (30, 31)
    finally:
        conn.close()


def test_同櫃只改高度_標高度更正(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        key = next(x["key"] for x in rl.machines(conn) if x["asset_serial"] == "A-2")
        rl.move_box(conn, key, "甲機房", "B11", 10, 2, "拖拉調高度", "u")
        row = next(x for x in rl.pending_cia_rows(conn) if x["資產序號"] == "A-2")
        assert row["更正類型"] == "高度更正" and row["新U位"] == "10-11U"
    finally:
        conn.close()


class _Req:
    def __init__(self, pw=None, ip="10.99.0.9"):
        self.headers = {"x-admin-password": pw} if pw else {}
        self.client = type("C", (), {"host": ip})()


def test_寫入守門_預設關_打開後要管理密碼_錯太多擋(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    try:
        monkeypatch.setenv("WEBIT3_DISABLE_AUTH", "1")
        conn.execute("DELETE FROM users WHERE username = 'admin'")
        conn.execute("INSERT INTO users (username, password_hash) VALUES ('admin', ?)", (auth.hash_password("right-pass"),))
        conn.commit()
        assert wg.enabled(conn) is False
        wg.check(conn, _Req())                                             # 關著：直接放行
        wg.set_enabled(conn, True)
        with pytest.raises(wg.NeedPassword):
            wg.check(conn, _Req())                                         # 沒帶密碼
        wg.check(conn, _Req("right-pass"))                                 # 對的密碼
        for _ in range(wg.MAX_FAILS):
            with pytest.raises(wg.NeedPassword):
                wg.check(conn, _Req("wrong"))
        with pytest.raises(wg.TooManyFails):
            wg.check(conn, _Req("right-pass"))                             # 同一來源錯太多次：先擋
        wg.check(conn, _Req("right-pass", ip="10.99.0.10"))                # 別的來源不受影響
        assert "right-pass" not in str(conn.execute("SELECT * FROM write_guard_fail").fetchall())   # 明文不落地
        assert wg.actor(None, _Req(ip="10.99.0.9")) == "未登入@10.99.0.9"
        monkeypatch.delenv("WEBIT3_DISABLE_AUTH")
        wg.check(conn, _Req())                                             # 有登入（沒停用）時不要求
    finally:
        conn.close()


def test_限流看真正連線位址_偽造XFF繞不過_有總上限(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    try:
        monkeypatch.setenv("WEBIT3_DISABLE_AUTH", "1")
        conn.execute("DELETE FROM users WHERE username = 'admin'")
        conn.execute("INSERT INTO users (username, password_hash) VALUES ('admin', ?)", (auth.hash_password("right-pass"),))
        conn.commit()
        wg.set_enabled(conn, True)
        for i in range(wg.MAX_FAILS):
            r = _Req("wrong")
            r.headers["x-forwarded-for"] = f"10.99.1.{i}"                  # 每次換一個偽造來源
            with pytest.raises(wg.NeedPassword):
                wg.check(conn, r)
        r = _Req("right-pass")
        r.headers["x-forwarded-for"] = "10.99.1.200"
        with pytest.raises(wg.TooManyFails):
            wg.check(conn, r)                                               # 同一個真正來源：擋
        monkeypatch.setattr(wg, "MAX_FAILS_ALL", wg.MAX_FAILS + 1)
        with pytest.raises(wg.NeedPassword):
            wg.check(conn, _Req("wrong", ip="10.99.0.20"))
        with pytest.raises(wg.TooManyFails):
            wg.check(conn, _Req("right-pass", ip="10.99.0.21"))            # 換真正來源也一樣：總數到頂就擋
    finally:
        conn.close()


def test_admin不存在時不能打開(tmp_path):
    conn = _conn(tmp_path)
    try:
        conn.execute("DELETE FROM users WHERE username = 'admin'")
        conn.commit()
        assert wg.admin_exists(conn) is False
        conn.execute("INSERT INTO users (username, password_hash) VALUES ('admin', ?)", (auth.hash_password("p"),))
        assert wg.admin_exists(conn) is True
    finally:
        conn.close()


def test_機櫃寫入端點都有守門():
    import re
    base = ROOT / "APP" / "asset-module" / "backend" / "routers"
    missing = []
    for f in ("arch.py", "overlaps.py", "rackdiagram.py"):
        src = (base / f).read_text(encoding="utf-8")
        for m in re.finditer(r'@router\.(put|post|delete)\("(/api/(racks|rack-diagram/rows)[^"]*)"\)\s*\ndef (\w+)\(', src):
            body = src[m.end():]
            nxt = body.find("@router.")
            body = body if nxt < 0 else body[:nxt]
            if "write_guard.check" not in body and "_guard(conn, request)" not in body:
                missing.append(m.group(2))
    assert missing == []


def test_搬移保持成員位移_調高度只改這台_沒動拒絕_預留擋(tmp_path):
    import overlap_queue as oq
    conn = _conn(tmp_path)
    try:
        def hw(serial, host, rack, model):
            db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=None, physical_location="01_甲機房",
                               rack_no=rack, device_model=model)
        hw("P-1", "frame01", "C1/10-13U", "IBM S922")
        hw("P-2", "lpar01", "C1/12U", "IBM S922 LPAR")      # 畫在同一台框內、自己的 U 段不同
        hw("Q-1", "other01", "C2/1U", "X")
        conn.commit()
        ms = {x["asset_serial"]: x["key"] for x in rl.machines(conn)}
        oq.decide(conn, "甲機房", "C1", [ms["P-1"], ms["P-2"]], "merge", "alice", "同一台框")
        with pytest.raises(ValueError, match="沒有改變"):
            rl.move_box(conn, ms["P-1"], "甲機房", "C1", 10, 4, "x", "u")
        rl.move_box(conn, ms["P-1"], "甲機房", "C1", 20, 4, "拖拉更正", "u")
        assert _pos(conn, "P-1") == ("C1", 20, 23) and _pos(conn, "P-2") == ("C1", 22, 22)   # 位移 +2 保持、高度 1U 不變
        rl.move_box(conn, ms["P-1"], "甲機房", "C1", 20, 5, "拖拉調高度", "u")
        assert _pos(conn, "P-1") == ("C1", 20, 24) and _pos(conn, "P-2") == ("C1", 22, 22)   # 只改這台
        rl.reserve(conn, "甲機房", "C2", 30, 2, "新設備", "2099-12-31", "u")
        with pytest.raises(rl.Occupied, match="預留"):
            rl.move_box(conn, ms["P-1"], "甲機房", "C2", 28, 5, "x", "u")
        with pytest.raises(ValueError, match="超出"):
            rl.move_box(conn, ms["P-1"], "甲機房", "C1", rl.rack_height(conn), 5, "x", "u")
    finally:
        conn.close()
