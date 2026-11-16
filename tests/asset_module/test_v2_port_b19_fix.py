"""第 19 批（V20 資產編輯，⚠D4）獨立審查修正（2.055.1）：手打的 PUT /api/assets/{序號} 帶 created_at／updated_at
不可以寫進去（以前只擋 id、asset_serial）；舊頁的 ?edit=1 不再開就地編輯（只剩 V2 表單一條寫入路）。全部假資料。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_PUT不能改建立更新時間_也不能改序號(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="HW-T-1", hostname="fake01", ip="10.99.1.1")
    c.execute("UPDATE hardware SET created_at = '2026-01-01 00:00:00' WHERE asset_serial = 'HW-T-1'")
    c.commit()
    db.create_user(c, "tester", auth.hash_password("test-password-123"))
    c.close()

    def _get_db():
        cc = db.get_connection(p)
        try:
            yield cc
        finally:
            cc.close()
    api.app.dependency_overrides[api.get_db] = _get_db
    try:
        cl = TestClient(api.app)
        assert cl.post("/api/auth/login", json={"username": "tester", "password": "test-password-123"}).status_code == 200
        r = cl.put("/api/assets/HW-T-1", json={"fields": {"remark": "x", "created_at": "1999-09-09 00:00:00",
                                                          "updated_at": "1999-09-09 00:00:00"}})
        assert r.status_code == 200, r.text
        c = db.get_connection(p)
        row = c.execute("SELECT * FROM hardware WHERE asset_serial = 'HW-T-1'").fetchone()
        c.close()
        assert row["remark"] == "x"
        assert row["created_at"] == "2026-01-01 00:00:00"
        assert row["updated_at"] and not row["updated_at"].startswith("1999")
    finally:
        api.app.dependency_overrides.clear()


def test_db層第二道_時間與主鍵一律丟掉(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        db.insert_hardware(c, asset_serial="HW-T-2", hostname="fake02")
        c.commit()
        assert db.update_hardware(c, "HW-T-2", {"created_at": "1999", "updated_at": "1999", "id": 99, "asset_serial": "X"}) == 0
        row = c.execute("SELECT * FROM hardware WHERE hostname = 'fake02'").fetchone()
        assert row["asset_serial"] == "HW-T-2" and row["id"] != 99 and row["created_at"] != "1999"
    finally:
        c.close()


def test_舊頁edit等於1不再開就地編輯():
    o = (ROOT / "APP" / "asset-module" / "frontend" / "pages" / "assets" / "[serial].vue").read_text(encoding="utf-8")
    code = "\n".join(line for line in o.splitlines() if not line.strip().startswith("//"))
    assert "route.query.edit === '1'" not in code
    assert code.count("startEdit()") == 1 and "function startEdit()" in code and '@click="startEdit"' not in code   # 只剩定義，沒有人呼叫
