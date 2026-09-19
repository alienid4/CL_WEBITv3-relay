"""方案B：一批主機、同一帳號、多組候選密碼，回報「哪一組通」。

守的是這支功能存在的理由與底線：
1. 不落地——回傳值裡不能出現密碼本身，只能有「第幾組密碼對」
2. 密碼依序試，第一組通了就停——不是每組都打一輪（密碼噴灑風險）
3. 加總（matched/unmatched）要跟逐台結果對得起來
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import onboard_engine as eng  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PW = "test-password-123"


def _client(tmp):
    db_path = Path(tmp) / "t.db"
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    try:
        db.create_user(conn, "tester", auth.hash_password(_PW))
        conn.commit()
    finally:
        conn.close()

    def _override():
        c = db.get_connection(db_path)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _override
    client = TestClient(api.app)
    assert client.post("/api/auth/login",
                       json={"username": "tester", "password": _PW}).status_code == 200
    return client


def _fake_runner(correct: dict[str, tuple[str, str]]):
    """correct: {ip: (username, 正確密碼)}。密碼對就回 linux banner，不對就回空字串
    （模擬 SSH 登入被拒——parse_probe 認不出任何平台關鍵字）。"""
    def runner(host, username, password):
        u, pw = correct.get(host, (None, None))
        if username == u and password == pw:
            return "Linux\n1000\nHASSUDO"
        return "Permission denied, please try again."
    return runner


# ===== 邏輯層 =====

def test_第二組密碼才對_回報index是1不是0():
    runner = _fake_runner({"10.99.0.1": ("root", "B密碼")})
    out = eng.batch_probe_credentials(
        [{"ip": "10.99.0.1"}], username="root", passwords=["A密碼", "B密碼"], runner=runner)
    assert out[0]["matched_password_index"] == 1
    assert out[0]["platform"] == "linux"
    assert out[0]["error"] is None


def test_兩組密碼都不對_回None跟錯誤原因():
    runner = _fake_runner({})   # 沒有任何一台的正確密碼登記，全部登入失敗
    out = eng.batch_probe_credentials(
        [{"ip": "10.99.0.2"}], username="root", passwords=["A密碼", "B密碼"], runner=runner)
    assert out[0]["matched_password_index"] is None
    assert out[0]["platform"] is None
    assert out[0]["error"]


def test_密碼對了就停_不會把第二組也打一次():
    """安全考量：對了就停，不要每組都打——那是密碼噴灑的形狀。"""
    calls: list[str] = []

    def runner(host, username, password):
        calls.append(password)
        return "Linux\n0\nHASSUDO" if password == "A密碼" else "Permission denied"

    eng.batch_probe_credentials(
        [{"ip": "10.99.0.3"}], username="root", passwords=["A密碼", "B密碼"], runner=runner)
    assert calls == ["A密碼"], "第一組就成功，不該再打第二組"


def test_回傳值裡不能出現密碼本身():
    """不落地的核心保證：結果只能講「第幾組」，不能講密碼是什麼。"""
    runner = _fake_runner({"10.99.0.4": ("root", "超級機密密碼XYZ")})
    out = eng.batch_probe_credentials(
        [{"ip": "10.99.0.4"}], username="root", passwords=["A", "超級機密密碼XYZ"], runner=runner)
    import json
    dumped = json.dumps(out, ensure_ascii=False)
    assert "超級機密密碼XYZ" not in dumped
    assert out[0]["matched_password_index"] == 1


def test_一批多台各自比對各自的密碼():
    runner = _fake_runner({
        "10.99.0.5": ("root", "A"),
        "10.99.0.6": ("root", "B"),
    })
    out = eng.batch_probe_credentials(
        [{"ip": "10.99.0.5"}, {"ip": "10.99.0.6"}, {"ip": "10.99.0.7"}],
        username="root", passwords=["A", "B"], runner=runner)
    by_ip = {r["ip"]: r["matched_password_index"] for r in out}
    assert by_ip["10.99.0.5"] == 0
    assert by_ip["10.99.0.6"] == 1
    assert by_ip["10.99.0.7"] is None   # 沒登記正確密碼，兩組都不對


# ===== API 層 =====

def test_端點沒給ip或密碼就報錯():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            r = client.post("/api/onboard/batch-probe",
                            json={"ips": [], "username": "root", "passwords": ["a"]})
            assert r.status_code == 400
            r2 = client.post("/api/onboard/batch-probe",
                             json={"ips": ["10.99.0.1"], "username": "root", "passwords": []})
            assert r2.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_一次超過100台被擋下():
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            ips = [f"10.99.{i // 256}.{i % 256}" for i in range(101)]
            r = client.post("/api/onboard/batch-probe",
                            json={"ips": ips, "username": "root", "passwords": ["a"]})
            assert r.status_code == 400
        finally:
            api.app.dependency_overrides.clear()


def test_端點回傳的加總跟逐台結果對得起來(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        client = _client(tmp)
        try:
            def fake_batch(targets, username, passwords, **kw):
                return [
                    {"ip": t["ip"], "matched_password_index": (0 if i % 2 == 0 else None),
                     "platform": "linux" if i % 2 == 0 else None, "uid": 0, "has_sudo": True,
                     "error": None if i % 2 == 0 else "登入失敗"}
                    for i, t in enumerate(targets)
                ]
            import onboard_engine
            monkeypatch.setattr(onboard_engine, "batch_probe_credentials", fake_batch)

            r = client.post("/api/onboard/batch-probe", json={
                "ips": ["10.99.0.1", "10.99.0.2", "10.99.0.3", "10.99.0.4"],
                "username": "root", "passwords": ["A", "B"]})
            assert r.status_code == 200
            body = r.json()
            assert body["total"] == 4
            assert body["matched"] == 2
            assert body["unmatched"] == 2
            assert len(body["results"]) == 4
        finally:
            api.app.dependency_overrides.clear()
