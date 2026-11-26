"""退役／報廢主機：後端每個登入入口都擋（D1，2.069.3 之後補）。

單一擋點＝onboard_eligibility.login_targets（login_target_refusal 呼叫它）＋retired_targets（batch-probe、precheck 共用）；
收集類（post-collect）擋在 post_onboard._platform_skip。守四件事：
1. 只有退役／報廢登記的 IP：納管、取消納管、Windows 納管、驗證、批次試密碼、預檢都零次連線。
2. 同 IP 至少一筆現役：不擋（現役優先，同 manage_state.serial_for_ip）。
3. 資產庫沒有這個 IP（新設備首次納管）：不擋。
4. 判定本身出錯：fail-closed。
例子 IP 一律 10.99.x。
"""
import sys
from pathlib import Path

import pytest

from test_v2_port_b23 import ROOT

sys.path.insert(0, str(ROOT / "tests" / "asset_module"))
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

from test_v2_port_b23_review4 import env  # noqa: E402,F401  (fixture：假 onboard／revoke／win，記每一次連線)

RET = "10.99.0.21"      # 只有報廢登記
MIX = "10.99.0.22"      # 一筆停用＋一筆使用中
NEW = "10.99.0.23"      # 資產庫沒有這個 IP
LIVE = "10.99.0.12"     # _setup 沒登記（同 NEW），另用 MIX 驗現役


@pytest.fixture
def env2(env):
    client, tried, tmp = env
    import db

    conn = db.get_connection(Path(tmp) / "t.db")
    db.insert_hardware(conn, asset_serial="R-1", ip=RET, os="Red Hat Enterprise Linux 7", hostname="old-example-01", asset_status="報廢")
    db.insert_hardware(conn, asset_serial="M-1", ip=MIX, os="Red Hat Enterprise Linux 7", hostname="mix-example-01", asset_status="停用")
    db.insert_hardware(conn, asset_serial="M-2", ip=MIX, os="Red Hat Enterprise Linux 9", hostname="mix-example-02", asset_status="使用中")
    conn.commit()
    conn.close()
    return client, tried


def _post_all(client, ip, verify=True):
    """verify=False：不退役的 IP 會真的走到收集連線（等逾時很慢），只有「應該被擋」的測試才帶 verify。"""
    return {
        "onboard": client.post("/api/onboard", json={"ip": ip, "platform": "linux", "username": "root", "password": "x1x"}),
        "revoke": client.post("/api/onboard/revoke", json={"ip": ip, "platform": "linux", "username": "root",
                                                           "password": "x1x", "confirm": "YES"}),
        "windows": client.post("/api/onboard/windows", json={"ip": ip}),
        **({"verify": client.post("/api/onboard/verify", json={"ip": ip, "platform": "linux"})} if verify else {}),
    }


def test_退役報廢_每個入口零次連線(env2):
    client, tried = env2
    out = _post_all(client, RET)
    for name in ("onboard", "revoke", "windows"):
        assert out[name].status_code == 400, (name, out[name].text)
        assert "退役／報廢" in out[name].text and "CIA" in out[name].text, (name, out[name].text)
    v = out["verify"].json()
    assert v["ok"] is False and "退役／報廢" in v["error"]
    bp = client.post("/api/onboard/batch-probe", json={"ips": [RET], "username": "root", "passwords": ["x1x"]})
    assert bp.status_code == 200
    row = bp.json()["results"][0]
    assert row["retired"] is True and row["matched_password_index"] is None and "退役／報廢" in row["error"]
    pc = client.post("/api/onboard/precheck", json={"ips": [RET], "username": "root", "password": "x1x"})
    assert pc.status_code == 200
    prow = pc.json()["results"][0]
    assert prow["retired"] is True and prow["onboardable"] is False and "退役／報廢" in prow["reason"]
    assert pc.json()["counts"]["不會納管"] == 1
    assert tried == [], tried


def test_同IP有現役_不擋(env2):
    client, tried = env2
    out = _post_all(client, MIX, verify=False)
    assert out["onboard"].status_code == 200 and out["windows"].status_code == 200   # 假連線回 ok=False 但不是 400 擋下
    assert ("onboard", MIX) in tried and ("win", MIX) in tried and ("revoke", MIX) in tried
    bp = client.post("/api/onboard/batch-probe", json={"ips": [MIX], "username": "root", "passwords": ["x1x"]}).json()
    assert not bp["results"][0].get("retired")
    pc = client.post("/api/onboard/precheck", json={"ips": [MIX], "username": "root", "password": "x1x"}).json()
    assert not pc["results"][0].get("retired")
    assert tried.count(MIX) == 2      # batch-probe＋precheck 各連一次


def test_資產庫沒有這個IP_新設備首次納管不擋(env2):
    client, tried = env2
    out = _post_all(client, NEW, verify=False)
    assert out["onboard"].status_code == 200 and out["windows"].status_code == 200
    assert ("onboard", NEW) in tried and ("win", NEW) in tried


def test_混批_退役那台不登_其餘照常(env2):
    client, tried = env2
    bp = client.post("/api/onboard/batch-probe", json={"ips": [RET, NEW], "username": "root", "passwords": ["x1x"]}).json()
    assert [bool(r.get("retired")) for r in bp["results"]] == [True, False]
    assert tried == [NEW]
    tried.clear()
    pc = client.post("/api/onboard/precheck", json={"ips": [RET, NEW], "username": "root", "password": "x1x"}).json()
    assert sorted(bool(r.get("retired")) for r in pc["results"]) == [False, True]
    assert tried == [NEW]


def test_判定出錯_fail_closed(env2, monkeypatch):
    client, tried = env2
    import onboard_eligibility as oe

    def boom(conn, ips):
        raise RuntimeError("x")
    monkeypatch.setattr(oe, "_hw_rows_by_ips", boom)
    r = client.post("/api/onboard", json={"ip": NEW, "platform": "linux", "username": "root", "password": "x1x"})
    assert r.status_code == 400 and oe.ELIGIBILITY_FAIL_ERROR in r.text and "RuntimeError" not in r.text   # 固定字樣，不洩露例外類型
    assert tried == []


def test_收集全部_退役報廢略過(env2):
    client, tried = env2
    import post_onboard

    # _platform_skip 每一樣都要略過（六樣一律不收）；現役那筆不受影響
    from api import get_db
    gen = client.app.dependency_overrides[get_db]()
    conn = next(gen)
    try:
        assert all("退役／報廢" in (post_onboard._platform_skip(conn, j, "R-1") or "") for j in post_onboard.JOBS)
        assert all("退役／報廢" not in (post_onboard._platform_skip(conn, j, "M-2") or "") for j in post_onboard.JOBS)
    finally:
        gen.close()
