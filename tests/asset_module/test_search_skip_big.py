"""搜尋略過大表時，**一定要講出來**。

2026-09-24 實測：通用掃表把每張表每一列讀進 Python 比對，
90 萬列的軟體清單讓整支 /api/search 要 8.6～10 秒。加門檻略過後降到 0.55 秒。

但**「快」不可以用「靜靜漏東西」換**。使用者昨天用同一個關鍵字找得到、
今天找不到，第一個念頭是「資料掉了」——那會毀掉他對整個系統的信任。

所以這組測試守的不是效能，是**誠實**：
  1. 略過了就要出現在結果裡，而且**有略過就出現**（不是沒結果時才講）
  2. 要講**幾筆**、**門檻多少**、**去哪裡找**
  3. 表名要換成人看得懂的字
  4. 沒超過門檻的表**照樣要掃**——門檻不可以變成「大家都不掃了」
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402
import db  # noqa: E402
import search_terms  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

BIG = 120        # 測試用的小門檻，免得要造幾十萬列才跑得到


@pytest.fixture()
def conn():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.db"
        db.init_db(p)
        c = db.get_connection(p)
        c.execute("INSERT INTO hardware (asset_serial, hostname, ip, asset_status) "
                  "VALUES ('A-1','web01','10.99.1.1','使用中')")
        c.executemany("INSERT INTO software (asset_serial, asset_name) VALUES (?,?)",
                      [("A-1", f"套件-{i}") for i in range(BIG + 10)])
        c.commit()
        try:
            yield c, p
        finally:
            c.close()


def test_超過門檻的表要略過並回報(conn):
    c, _ = conn
    tables = search_terms.searchable_tables(c)
    got = search_terms.scan(c, search_terms.parse_query("套件"), tables,
                            big_table_rows=BIG)
    names = {s["table"] for s in got["skipped"]}
    assert "software" in names, "大表沒被略過"
    row = next(s for s in got["skipped"] if s["table"] == "software")
    assert row["rows"] == BIG + 10, "沒有講出實際筆數"
    assert got["big_table_rows"] == BIG, "沒有把門檻值一起回出去"


def test_沒超過門檻的照樣要掃(conn):
    """門檻不可以變成『大家都不掃了』——那是另一種靜靜漏東西。"""
    c, _ = conn
    tables = search_terms.searchable_tables(c)
    got = search_terms.scan(c, search_terms.parse_query("web01"), tables,
                            big_table_rows=BIG)
    assert "hardware" not in {s["table"] for s in got["skipped"]}


def test_畫面要看得到略過了什麼(conn):
    """只有後端知道不算數：值班看的是畫面。

    這一條也順便釘住「有略過就出現」——這次搜尋是有結果的（web01 找得到），
    略過的提示**仍然要在**。
    """
    c, p = conn

    def _get_db():
        x = db.get_connection(p)
        try:
            yield x
        finally:
            x.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "t"}
    try:
        old = search_terms.BIG_TABLE_ROWS
        search_terms.BIG_TABLE_ROWS = BIG
        try:
            cl = TestClient(api.app, raise_server_exceptions=False)
            groups = cl.get("/api/search", params={"q": "web01"}).json()["groups"]
        finally:
            search_terms.BIG_TABLE_ROWS = old
    finally:
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)

    skipped = [g for g in groups if g.get("key") == "skipped"]
    assert skipped, "略過了卻沒有在畫面上講——那就是靜靜漏東西"
    item = skipped[0]["items"][0]
    # 表名要換成人看得懂的字：`software` 對使用者沒有意義
    assert "軟體清單" in item["title"], item["title"]
    assert f"{BIG + 10:,}" in item["title"], "沒講幾筆"
    assert f"{BIG:,}" in item["subtitle"], "沒講門檻是多少"
    assert "軟體盤點" in item["subtitle"], "沒告訴他去哪裡找"
