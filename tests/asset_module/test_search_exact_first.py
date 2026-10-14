"""搜尋：完全相同的要排最前面。

2026-09-23 公司機（10.92.198.14）實際踩到：使用者搜 `10.99.18.16`，
下拉只列出 `.161` `.162` `.163`，**那台 10.99.18.16 沒有出現**。

真因：`LIKE '%10.99.18.16%'` 同時命中前綴，而排序只按主機名——
`SECSVR018-161T` 的大寫 S（0x53）排在 `test1T` 的小寫 t（0x74）前面，
LIMIT 一切就把他要的那台切掉了。

**使用者的感受是「明明有卻搜不到」，那比「查無此項」更傷信任**——
查無此項至少是一個明確的答案，這個是系統對他說謊。
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        conn = db.get_connection(p)
        # 重現當天的資料形狀：一台 .16，外加三台前綴會命中的 .16x，
        # 而且 .16x 的主機名在 ASCII 排序上都排在 .16 那台前面。
        db.insert_hardware(conn, asset_serial="A-TEST1T", hostname="test1T",
                           ip="10.99.18.16", asset_status="使用中")
        for n in (161, 162, 163):
            db.insert_hardware(conn, asset_serial=f"A-{n}", hostname=f"SECSVR018-{n}T",
                               ip=f"10.99.18.{n}", asset_status="使用中")
        conn.commit()
        conn.close()

        def _get_db():
            c = db.get_connection(p)
            try:
                yield c
            finally:
                c.close()

        api.app.dependency_overrides[api.get_db] = _get_db
        api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester"}
        try:
            yield TestClient(api.app, raise_server_exceptions=False)
        finally:
            api.app.dependency_overrides.pop(api.get_db, None)
            api.app.dependency_overrides.pop(api.require_auth, None)


def _assets(client, q, limit=3):
    """回資產那一組的 (主機名, 副標) ——副標裡帶 IP。

    端點回的是給下拉用的 title／subtitle，沒有獨立的 ip 欄位；
    斷言要照**畫面實際看得到的東西**寫，不要照我以為的結構寫。
    """
    r = client.get("/api/search", params={"q": q, "limit": limit})
    assert r.status_code == 200
    for g in r.json()["groups"]:
        if g.get("key") == "assets":
            return [(x.get("title"), x.get("subtitle") or "") for x in g["items"]]
    return []


def test_完全相同的IP要排第一(client):
    """limit 故意壓到 3：前綴命中有 3 台，只要排序不對，那台就會被擠掉。

    這正是使用者當天看到的畫面——列了三台 .16x，就是沒有他要的 .16。
    """
    rows = _assets(client, "10.99.18.16", limit=3)
    assert rows, "完全沒有結果"
    assert rows[0][0] == "test1T", f"完全相同的沒排第一：{rows}"
    assert "10.99.18.16" in rows[0][1]


def test_前綴命中仍然找得到(client):
    """修「精確優先」不可以把前綴命中弄不見——那些也是使用者要的。"""
    names = {n for n, _ in _assets(client, "10.99.18.16", limit=8)}
    assert names >= {"test1T", "SECSVR018-161T", "SECSVR018-162T", "SECSVR018-163T"}


def test_主機名完全相同也要排第一(client):
    """IP 以外的欄位同理：打全名就是指名要那一台。"""
    rows = _assets(client, "test1T", limit=3)
    assert rows and rows[0][0] == "test1T"


def test_沒有完全相同時不影響原本的順序(client):
    """只有前綴／中間命中的情況，維持既有可預期的主機名排序，不要亂動。"""
    rows = _assets(client, "10.99.18.1", limit=8)
    assert rows, "前綴搜尋不該變成沒有結果"
