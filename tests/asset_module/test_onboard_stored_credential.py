"""Q96：手動／批次 Linux·AIX 納管可以挑「已設定的 SSH 憑證」。

守的東西：
1. 挑了憑證 → 伺服器端取帳密交給引擎，前端只送名稱
2. 適用範圍不含目標 IP → 拒絕，引擎不被呼叫
3. VMware（ESXi／vCenter）→ 拒絕，引擎不被呼叫（天條）
4. 每次使用寫 credential_use_audit，內容不含密碼
5. 自己打帳密的老路徑不變
6. 憑證列表 API 永不回密碼；Windows／類型不對不能用
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
FE = ROOT / "APP" / "asset-module" / "frontend"

import api  # noqa: E402
import auth  # noqa: E402
import credential_store as cs  # noqa: E402
import db  # noqa: E402
import manage_state  # noqa: E402
import onboard_engine  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FAKE_PW = "FAKE-TEST-PW-not-real-4471"
TYPED_PW = "FAKE-TYPED-PW-not-real-0912"
IN_SCOPE = "10.99.1.20"
OUT_SCOPE = "10.99.2.20"
ESXI = "10.99.1.30"
LOGIN_PW = "s3cure-pass!"


def _setup(tmp, monkeypatch):
    kp = str(Path(tmp) / "k.key")
    monkeypatch.setattr(cs, "KEY_PATH_DEFAULT", kp)
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)

    def _override():
        conn = db.get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()

    api.app.dependency_overrides[api.get_db] = _override
    client = TestClient(api.app)
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "admin", auth.hash_password(LOGIN_PW))
        db.insert_hardware(conn, asset_serial="A1", ip=IN_SCOPE, os="Red Hat Enterprise Linux 8")
        db.insert_hardware(conn, asset_serial="A2", ip=OUT_SCOPE, os="Red Hat Enterprise Linux 8")
        db.insert_hardware(conn, asset_serial="V1", ip=ESXI, os="VMware ESXi 7.0")
        cs.save(conn, "ssh-lab", "ssh", "svc-boot", FAKE_PW, scope="10.99.1.", key_path=kp)
        cs.save(conn, "win-lab", "winrm", "svc-win", FAKE_PW, scope="10.99.1.", key_path=kp)
        cs.save(conn, "ssh-cidr", "ssh", "svc-cidr", FAKE_PW, scope="10.99.2.0/24", key_path=kp)
        conn.commit()
    finally:
        conn.close()
    assert client.post("/api/auth/login", json={"username": "admin", "password": LOGIN_PW}).status_code == 200

    calls: list = []

    def fake_onboard(**kw):
        calls.append(kw)
        return onboard_engine.OnboardResult(True, "execute", "納管腳本執行完成", "")

    monkeypatch.setattr(onboard_engine, "resolve_collector_ip", lambda conn: "10.99.0.221")
    monkeypatch.setattr(onboard_engine, "onboard", fake_onboard)
    monkeypatch.setattr(manage_state, "refresh_collect_status", lambda conn, **kw: {})
    monkeypatch.setattr(manage_state, "collect_facts_into_assets", lambda conn, **kw: {"updated": 1})
    return client, db_path, calls


def _post(client, **body):
    return client.post("/api/onboard", json={"platform": "linux", **body})


def _dump(db_path) -> str:
    conn = db.get_connection(db_path)
    try:
        out = []
        for t in ("credential_use_audit", "onboard_audit"):
            out += [str(tuple(r)) for r in conn.execute(f"SELECT * FROM {t}")]
        return "\n".join(out)
    finally:
        conn.close()


def test_挑憑證_伺服器端取帳密交給引擎_前端不送密碼():
    with tempfile.TemporaryDirectory() as tmp:
        import pytest
        mp = pytest.MonkeyPatch()
        try:
            client, db_path, calls = _setup(tmp, mp)
            r = _post(client, ip=IN_SCOPE, credential="ssh-lab")
            assert r.status_code == 200, r.text
            assert len(calls) == 1
            assert calls[0]["username"] == "svc-boot" and calls[0]["password"] == FAKE_PW
            assert FAKE_PW not in r.text
        finally:
            mp.undo()
            api.app.dependency_overrides.clear()


def test_每次使用留稽核_不含密碼():
    with tempfile.TemporaryDirectory() as tmp:
        import pytest
        mp = pytest.MonkeyPatch()
        try:
            client, db_path, calls = _setup(tmp, mp)
            _post(client, ip=IN_SCOPE, credential="ssh-lab")
            conn = db.get_connection(db_path)
            rows = [dict(x) for x in conn.execute("SELECT * FROM credential_use_audit")]
            conn.close()
            assert len(rows) == 1
            assert rows[0]["credential_name"] == "ssh-lab" and rows[0]["target_ip"] == IN_SCOPE and rows[0]["ok"] == 1
            assert FAKE_PW not in _dump(db_path)
        finally:
            mp.undo()
            api.app.dependency_overrides.clear()


def test_適用範圍不含目標_拒絕且引擎不被呼叫():
    with tempfile.TemporaryDirectory() as tmp:
        import pytest
        mp = pytest.MonkeyPatch()
        try:
            client, db_path, calls = _setup(tmp, mp)
            r = _post(client, ip=OUT_SCOPE, credential="ssh-lab")
            assert r.status_code == 400 and "適用範圍" in r.json()["detail"]
            assert calls == []
            conn = db.get_connection(db_path)
            a = conn.execute("SELECT ok, message FROM credential_use_audit").fetchone()
            conn.close()
            assert a["ok"] == 0 and "拒用" in a["message"]
            # CIDR 範圍內的可以用
            assert _post(client, ip=OUT_SCOPE, credential="ssh-cidr").status_code == 200
        finally:
            mp.undo()
            api.app.dependency_overrides.clear()


def test_VMware_拒絕_天條():
    with tempfile.TemporaryDirectory() as tmp:
        import pytest
        mp = pytest.MonkeyPatch()
        try:
            client, db_path, calls = _setup(tmp, mp)
            r = _post(client, ip=ESXI, credential="ssh-lab")
            assert r.status_code == 400 and "VMware" in r.json()["detail"]
            assert calls == []
            assert FAKE_PW not in _dump(db_path)
        finally:
            mp.undo()
            api.app.dependency_overrides.clear()


def test_類型不對或不存在_拒絕():
    with tempfile.TemporaryDirectory() as tmp:
        import pytest
        mp = pytest.MonkeyPatch()
        try:
            client, db_path, calls = _setup(tmp, mp)
            assert _post(client, ip=IN_SCOPE, credential="win-lab").status_code == 400
            assert _post(client, ip=IN_SCOPE, credential="不存在").status_code == 400
            assert _post(client, ip=IN_SCOPE, credential="ssh-lab", platform="windows").status_code == 400
            assert calls == []
        finally:
            mp.undo()
            api.app.dependency_overrides.clear()


def test_自己打帳密的老路徑不變():
    with tempfile.TemporaryDirectory() as tmp:
        import pytest
        mp = pytest.MonkeyPatch()
        try:
            client, db_path, calls = _setup(tmp, mp)
            r = _post(client, ip=IN_SCOPE, username="sysinfra", password=TYPED_PW)
            assert r.status_code == 200
            assert calls[0]["username"] == "sysinfra" and calls[0]["password"] == TYPED_PW
            conn = db.get_connection(db_path)
            n = conn.execute("SELECT COUNT(*) FROM credential_use_audit").fetchone()[0]
            conn.close()
            assert n == 0                                   # 沒用憑證庫就不寫憑證稽核
            assert TYPED_PW not in _dump(db_path)
            # 兩者都沒給 → 400，不是拿空密碼去登入
            assert _post(client, ip=IN_SCOPE).status_code == 400
            assert len(calls) == 1
        finally:
            mp.undo()
            api.app.dependency_overrides.clear()


def test_憑證列表API永不回密碼():
    with tempfile.TemporaryDirectory() as tmp:
        import pytest
        mp = pytest.MonkeyPatch()
        try:
            client, db_path, calls = _setup(tmp, mp)
            r = client.get("/api/credentials")
            assert r.status_code == 200
            assert FAKE_PW not in r.text and "secret" not in r.text.replace("has_secret", "")
            assert {c["name"] for c in r.json()} >= {"ssh-lab", "win-lab"}
        finally:
            mp.undo()
            api.app.dependency_overrides.clear()


def test_scope_allows():
    assert cs.scope_allows(None, "10.99.1.5") and cs.scope_allows("", "10.99.1.5")
    assert cs.scope_allows("10.99.1.", "10.99.1.5") and not cs.scope_allows("10.99.1.", "10.99.11.5")
    assert cs.scope_allows("10.99.1.0/24", "10.99.1.200") and not cs.scope_allows("10.99.1.0/24", "10.99.2.1")
    assert not cs.scope_allows("not/a-cidr", "10.99.1.5")


# ---- 前端原始碼守門（同 test_v2_batch6_role_shells 的做法）
def _src(p):
    return (FE / p).read_text(encoding="utf-8")


def test_前端_單台與批次都有憑證下拉():
    for f in ("components/OnboardModal.vue", "components/BatchOnboardModal.vue"):
        s = _src(f)
        assert "'/api/credentials'" in s and "c.kind === 'ssh'" in s
        assert "credential: " in s and "v-model=\"credential\"" in s
        assert "<InfoNote>" in s and "適用" in s
        assert "password: cred.password" not in s or "credential.value" in s
    # 挑了憑證就不送帳密
    s = _src("components/OnboardModal.vue")
    assert "{ ip: props.ip, platform: cred.platform, credential: credential.value }" in s
    b = _src("components/BatchOnboardModal.vue")
    assert "credential: usedCredential" in b and "onboardOne(r, password, useCred)" in b
