"""組態檢核（9-2）總覽數字下鑽：點進去列出來的筆數，必須等於畫面上那個數字。

使用者 2026-10-21：「數字不能下鑽」。卡片、平台表每一格都要點得進去看是哪幾台／哪幾條。
主機層級的卡片前端直接篩 /api/config-audit 的 items（跟 summary 同一份 runs()）；
平台表的四態條數走 /api/config-audit/items（items_across），這裡守的是：

1. 每個平台 × 每一態，下鑽筆數 == summary.by_platform 那一格
2. 認不得的結果值（tally 歸進 Error）下鑽 Error 也要列得到，不可以少一條
3. 只算每台最新一份（跟 summary 同口徑），舊的那份不可以混進來
4. 「結果不可信」那幾台 == 有 Error 條目的那幾台
5. 路由 /items 要排在 /{run_id} 前面，不然會被吃成 run_id 回 422
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_audit as ca  # noqa: E402
import db  # noqa: E402
from test_config_audit import AIX, LINUX, WINDOWS  # noqa: E402

# 第二台 Linux：多一條認不得的結果值（要被算成 Error）
LINUX2 = (LINUX.replace("SECSVR198-011T", "SECSVR198-012T").replace("192.0.2.11", "192.0.2.13")
          + "TWGCB-01-012-0099;???;系統設定與維護;奇怪的值;x;y\n"
          + "TWGCB-01-012-0100;Error;系統設定與維護;讀不到;x;權限不足\n")


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="A-1", hostname="sec01", ip="192.0.2.12",
                       os="AIX 7.2", asset_status="使用中")
    c.commit()
    try:
        yield c
    finally:
        c.close()


def _load(conn):
    ca.import_report(conn, AIX.encode(), "aix_old.txt", "t")   # 舊的一份，會被下一份蓋掉
    ca.import_report(conn, AIX.encode(), "aix.txt", "t")
    ca.import_report(conn, LINUX.encode(), "linux.txt", "t")
    ca.import_report(conn, LINUX2.encode(), "linux2.txt", "t")
    ca.import_report(conn, WINDOWS.encode("utf-8"), "win.txt", "t")


def test_平台表每一格下鑽筆數等於那個數字(conn):
    _load(conn)
    s = ca.summary(conn)
    assert {p["platform"] for p in s["by_platform"]} == {"AIX", "Linux", "Windows"}
    for p in s["by_platform"]:
        for res in ca.RESULTS:
            got = ca.items_across(conn, p["platform"], res)
            assert len(got) == p[res], f"{p['platform']}・{res}：下鑽 {len(got)} 條，數字是 {p[res]}"
            assert all(it["platform"] == p["platform"] and it["bucket"] == res for it in got)


def test_認不得的結果值下鑽未查到也要列得到(conn):
    _load(conn)
    lin = next(p for p in ca.summary(conn)["by_platform"] if p["platform"] == "Linux")
    errs = ca.items_across(conn, "Linux", "Error")
    assert lin["Error"] == 2 and len(errs) == 2
    assert {it["result"] for it in errs} == {"???", "Error"}, "原始值照實給，分桶歸未查到"


def test_只算每台最新一份(conn):
    _load(conn)
    got = ca.items_across(conn, "AIX")
    assert len(got) == 4, "AIX 匯了兩次，只能算最新那份的 4 條"
    assert {it["run_id"] for it in got} == {r["id"] for r in ca.runs(conn) if r["platform"] == "AIX"}


def test_不分平台的總條數等於各主機條數加總(conn):
    _load(conn)
    assert len(ca.items_across(conn)) == sum(r["total"] for r in ca.runs(conn))


def test_結果不可信的主機就是有未查到條目的主機(conn):
    _load(conn)
    s = ca.summary(conn)
    untrust = {r["id"] for r in ca.runs(conn) if not r["trustworthy"]}
    assert len(untrust) == s["untrustworthy"] == 2
    assert {it["run_id"] for it in ca.items_across(conn, None, "Error")} == untrust


def test_卡片主機層級篩選數字對得起來(conn):
    """前端卡片下鑽用 runs() 篩，條件要跟 summary() 一樣。"""
    _load(conn)
    s = ca.summary(conn)
    rs = ca.runs(conn)
    assert len(rs) == s["hosts"]
    assert sum(1 for r in rs if not r.get("asset_serial")) == s["unmatched"]
    assert sum(1 for r in rs if r.get("completeness") == "無完成標記") == s["no_end_marker"]
    for p in s["by_platform"]:
        assert sum(1 for r in rs if (r.get("platform") or "未知") == p["platform"]) == p["hosts"]


def test_沒有資料的平台或態回空清單不是錯(conn):
    _load(conn)
    assert ca.items_across(conn, "HP-UX", "Error") == []


def test_不認得的結果篩選要報錯(conn):
    with pytest.raises(ValueError):
        ca.items_across(conn, None, "Bogus")


def test_items路由排在run_id前面():
    import api

    paths = [getattr(r, "path", "") for r in api.app.routes]
    assert "/api/config-audit/items" in paths
    assert paths.index("/api/config-audit/items") < paths.index("/api/config-audit/{run_id}")
