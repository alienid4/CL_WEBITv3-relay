"""2.060.x V2 待答②（11-10 自決）：業務系統改分級（PUT /api/systems/class）限管理員；一般帳號 403。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import access_policy  # noqa: E402

ROLE_AWARE_TESTS = True     # 要測一般帳號被擋，不吃測試環境預設建成 admin


def test_改分級在管理員寫入清單_不在一般寫入清單():
    assert ("PUT", "/api/systems/class") in access_policy.ADMIN_WRITE
    assert ("PUT", "/api/systems/class") not in access_policy.USER_WRITE


def test_API_一般帳號403_管理員可以(tmp_path):
    from test_eos_notify import _client, _login
    api, client, _ = _client(tmp_path)
    try:
        _login(client, "bob")
        r = client.put("/api/systems/class", json={"api_id": "A01", "sys_class": ""})
        assert r.status_code == 403, r.text
        _login(client, "admin")
        r = client.put("/api/systems/class", json={"api_id": "A01", "sys_class": ""})
        assert r.status_code != 403, r.text
    finally:
        api.app.dependency_overrides.clear()
