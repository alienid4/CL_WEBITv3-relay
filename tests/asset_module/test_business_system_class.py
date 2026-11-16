"""系統類別匯入：只更新類別、整份取代、不猜也不新建（2026-09-10）。

使用者給了 APID.xlsx（項次／系統類別／APID／系統別，89 個系統），要照它分第一／二／三類。

最要守的一條：**不能動到 AP 部門與負責人**。現有的對照表匯入是整列覆寫，
拿這份只有類別的檔案走那條路，82 個系統的部門與負責人會被清空。
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import business_system as bs  # noqa: E402
import db  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    for api, name, dept, owner in (("N-001", "甲系統", "證券資訊部", "某甲"),
                                   ("N-002", "乙系統", "數位平台部", "某乙"),
                                   ("N-003", "丙系統", "資訊架構部", "某丙")):
        c.execute("INSERT INTO business_system (api_id, name, ap_department, ap_owner) "
                  "VALUES (?,?,?,?)", (api, name, dept, owner))
    c.commit()
    yield c
    c.close()


def _xlsx(tmp_path, rows, header=("項次", "系統類別", "APID", "系統別")):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    p = tmp_path / "APID.xlsx"
    wb.save(p)
    return p


def _row(c, api):
    return c.execute("SELECT * FROM business_system WHERE api_id = ?", (api,)).fetchone()


def test_只更新類別_部門與負責人不能被清空(conn, tmp_path):
    p = _xlsx(tmp_path, [(1, "S1-第一類系統軟體（核心）", "N-001", "甲系統")])
    bs.import_class_file(p, conn)
    r = _row(conn, "N-001")
    assert r["sys_class"] == "S1-第一類系統軟體（核心）", "原字串照存，核心這種資訊不丟"
    assert r["ap_department"] == "證券資訊部", "部門被清空了——走錯匯入路徑"
    assert r["ap_owner"] == "某甲", "負責人被清空了——走錯匯入路徑"
    assert r["name"] == "甲系統"


def test_整份取代_不在新清單的回到未分級(conn, tmp_path):
    bs.import_class_file(_xlsx(tmp_path, [(1, "S2-第二類系統軟體", "N-002", "乙")]), conn)
    assert _row(conn, "N-002")["sys_class"] == "S2-第二類系統軟體"
    # 新清單拿掉 N-002
    out = bs.import_class_file(_xlsx(tmp_path, [(1, "S3-第三類系統軟體", "N-003", "丙")]), conn)
    assert _row(conn, "N-002")["sys_class"] is None, "舊類別留著會跟清單對不起來"
    assert _row(conn, "N-003")["sys_class"] == "S3-第三類系統軟體"
    assert out["unrated_after"] == 2


def test_同一個代碼兩種類別_不猜而且要列出來(conn, tmp_path):
    p = _xlsx(tmp_path, [(1, "S1-第一類系統軟體", "N-001", "甲"),
                         (2, "S2-第二類系統軟體", "N-001", "甲")])
    out = bs.import_class_file(p, conn)
    assert _row(conn, "N-001")["sys_class"] is None
    assert "N-001" in out["conflicts"]


def test_清單有對照表沒有的代碼_不新建但要具名回報(conn, tmp_path):
    p = _xlsx(tmp_path, [(1, "S1-第一類系統軟體", "N-999", "不存在")])
    out = bs.import_class_file(p, conn)
    assert out["not_in_table"] == ["N-999"]
    assert _row(conn, "N-999") is None, "新建會長出一個沒有部門也沒有機器的空系統"


def test_缺類別欄要講清楚表頭是什麼(conn, tmp_path):
    p = _xlsx(tmp_path, [(1, "N-001", "甲")], header=("項次", "APID", "系統別"))
    with pytest.raises(ValueError, match="系統類別"):
        bs.import_class_file(p, conn)


def test_手動分級覆寫_匯入不沖掉_可清除(conn, tmp_path):
    import system_stats as ss
    # N-001 分級表判第一類；手動改成第二類
    bs.import_class_file(_xlsx(tmp_path, [(1, "S1-第一類系統軟體（核心）", "N-001", "甲系統")]), conn)
    bs.set_system_class(conn, "N-001", "第二類")
    c = ss._system_classes(conn)["N-001"]
    assert c["cls"] == "第二類" and c["override"] == "第二類" and c["computed"] == "第一類"

    # 分級表整份重匯（還是把 N-001 判第一類）——覆寫不能被沖掉
    bs.import_class_file(_xlsx(tmp_path, [(1, "S1-第一類系統軟體", "N-001", "甲系統")]), conn)
    assert ss._system_classes(conn)["N-001"]["cls"] == "第二類"

    # 未分級的 N-002 手動指定第三類
    bs.set_system_class(conn, "N-002", "第三類")
    assert ss._system_classes(conn)["N-002"]["cls"] == "第三類"

    # 清除（傳未分級）→ 回到分級表判定（N-001 回第一類）
    bs.set_system_class(conn, "N-001", "未分級")
    assert ss._system_classes(conn)["N-001"]["cls"] == "第一類"


def test_手動分級_不認得的值會擋(conn):
    with pytest.raises(ValueError):
        bs.set_system_class(conn, "N-001", "第四類")
