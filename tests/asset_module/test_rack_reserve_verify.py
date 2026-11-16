"""W221-001（Codex 38）：櫃內有未定位設備時，預留須「現場確認＋備註」；無未定位的櫃行為不變。全部用假庫，不碰正式站。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import rack_layout as rl  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, serial, rack):
    db.insert_hardware(conn, asset_serial=serial, hostname=serial.lower(), ip=f"10.99.9.{ord(serial[-1]) % 200 + 1}",
                       physical_location="01_甲機房", rack_no=rack, device_model="Dell R750", api_id="N-920", asset_name="上架系統")


def _seed(conn):
    _hw(conn, "B01", "A2/20U")      # A2：1 台有 U、1 台沒寫 U → 未定位
    _hw(conn, "B02", "A2")
    _hw(conn, "C01", "A3/10U")      # A3：全部有 U
    conn.commit()


def _args(rack):
    return dict(room="甲機房", rack=rack, u_start=1, u_height=1, label="新設備", expires_at="2099-01-01", by="tester")


def test_placement_未定位櫃標示不確定與未定位數(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        by = {x["rack"]: x for x in rl.placement(conn, u=1, qty=1, rooms=["甲機房"])["racks"]}
        assert by["A2"]["fits_state"] == "unsure" and by["A2"]["unplaced_count"] == 1 and by["A2"]["uncertain_u"]
        assert by["A3"]["fits_state"] == "yes" and by["A3"]["unplaced_count"] == 0 and not by["A3"]["uncertain_u"]
    finally:
        conn.close()


def test_未定位櫃_無確認或無備註_預留被擋且不寫入(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        for kw in ({}, {"confirm_verified": True}, {"note": "已看過"}, {"confirm_verified": True, "note": "   "}):
            with pytest.raises(rl.NeedVerification) as e:
                rl.reserve(conn, **_args("A2"), **kw)
            assert "1 台沒寫 U 位" in str(e.value) and "空位未核實" in str(e.value)
        assert rl.reservations(conn) == []
    finally:
        conn.close()


def test_未定位櫃_有確認加備註_成功並記錄(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = rl.reserve(conn, **_args("A2"), confirm_verified=True, note="10/31 王某現場看過")
        assert r["id"]
        row = rl.reservations(conn)[0]
        assert row["note"] == rl.VERIFIED_NOTE_PREFIX + "10/31 王某現場看過" and row["created_by"] == "tester"
    finally:
        conn.close()


def test_無未定位的櫃_行為與原本相同(tmp_path):
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = rl.reserve(conn, **_args("A3"), note=None)
        assert r["id"]
        assert rl.reservations(conn)[0]["note"] is None
        r2 = rl.reserve(conn, **dict(_args("A3"), u_start=5), confirm_verified=True, note="備註")   # 多帶確認也不改寫備註
        assert next(x for x in rl.reservations(conn) if x["id"] == r2["id"])["note"] == "備註"
    finally:
        conn.close()


def test_端點_未確認409_確認後成功_非管理員403(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from routers import arch
    import write_guard
    conn = _conn(tmp_path)
    try:
        _seed(conn)
        monkeypatch.setattr(write_guard, "check", lambda *a, **k: None)
        monkeypatch.setattr(write_guard, "actor", lambda *a, **k: "tester")

        def body(**k):
            return arch.ReservationBody(room="甲機房", rack="A2", u_start=1, u_height=1, label="x", expires_at="2099-01-01", **k)
        monkeypatch.setattr(arch, "_can_edit_nav", lambda s, c: True)
        with pytest.raises(HTTPException) as e:
            arch.racks_reservation_create_endpoint(body(), None, None, conn)
        assert e.value.status_code == 409 and "空位未核實" in e.value.detail
        assert rl.reservations(conn) == []
        ok = arch.racks_reservation_create_endpoint(body(confirm_verified=True, note="現場看過"), None, None, conn)
        assert ok["id"]
        monkeypatch.setattr(arch, "_can_edit_nav", lambda s, c: False)
        with pytest.raises(HTTPException) as e2:
            arch.racks_reservation_create_endpoint(body(confirm_verified=True, note="x"), None, None, conn)
        assert e2.value.status_code == 403
    finally:
        conn.close()
