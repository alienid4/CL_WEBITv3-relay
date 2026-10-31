"""收集「沒收到」時要講得出真正的原因，不是「可能……或……」。

BOSS 2026-09-23 指出：服務那條的理由跟其他三類**不同層**。查下去確實如此——
那句「這一輪沒收到（這台可能不在可收集清單、或被排除）」不是後端給的理由，
是**前端的預設字串**：後端連 candidates 都是 0（這台根本沒進迴圈），
沒有 failed、沒有 unsupported，前端無話可說就填了這句。

而「這台為什麼沒進迴圈」是查得出來的事實：
序號不存在／沒有 IP／還沒納管——**三種要做的事完全不同**。
查得出來卻寫「可能」，跟顯示 0 是同一種病。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state as ms  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        yield c
    finally:
        c.close()


def _add(c, serial, ip=None, collect_ok=0, host=None):
    db.insert_hardware(c, asset_serial=serial, hostname=host or serial,
                       ip=ip, os="AIX 7.2")
    c.execute("UPDATE hardware SET collect_ok = ? WHERE asset_serial = ?",
              (collect_ok, serial))
    c.commit()


# ═══ 三種「沒進迴圈」要分得開 ═══════════════════════════════════════

def test_序號不存在要直說(conn):
    why = ms.why_not_collectable(conn, "NO-SUCH-1")
    assert why and "找不到" in why


def test_沒有ip要說沒有ip(conn):
    """連要連去哪裡都不知道，跟「連不上」是兩件事。"""
    _add(conn, "A-1", ip=None, collect_ok=0)
    why = ms.why_not_collectable(conn, "A-1")
    assert why and "沒有可用的 IP" in why
    assert "納管" not in why, "沒有 IP 卻叫人去納管，會讓人跑錯方向"


def test_還沒納管要說還沒納管(conn):
    _add(conn, "A-2", ip="10.0.0.2", collect_ok=0)
    why = ms.why_not_collectable(conn, "A-2")
    assert why and "還沒納管" in why
    assert "collect_ok=0" in why          # 依據要講出來，不要只說結論


def test_納管好的就不該有理由(conn):
    _add(conn, "A-3", ip="10.0.0.3", collect_ok=1)
    assert ms.why_not_collectable(conn, "A-3") is None


def test_同一台只要有一筆納管就算收得到(conn):
    """跟 collect_targets_sql 同一條規則。

    兩邊規則不一致的話，會出現「收集器說收得到、這支說收不到」的兩套答案——
    使用者看到的就是自相矛盾的畫面。
    """
    # 「同一台」的判準是 machine_key（主機名／IP／序號），所以兩筆要同主機名——
    # 只有 IP 相同不算，IP 會回收再分配給別台。
    _add(conn, "DYN-9", ip="10.0.0.9", collect_ok=0, host="aixhost9")
    _add(conn, "CIA-9", ip="10.0.0.9", collect_ok=1, host="aixhost9")
    # 從帳外那筆按收集也要算收得到（2026-09-20 使用者踩過）
    assert ms.why_not_collectable(conn, "DYN-9") is None


# ═══ 四個收集器都要回 skipped ═══════════════════════════════════════

def test_服務收集沒進迴圈要回原因(conn):
    import service_inventory

    _add(conn, "A-4", ip="10.0.0.4", collect_ok=0)
    r = service_inventory.collect_services(conn, runner=lambda h, c: "",
                                           only_serial="A-4")
    assert r["candidates"] == 0
    assert r["skipped"] and "還沒納管" in r["skipped"][0]["reason"]


def test_軟體收集沒進迴圈要回原因(conn):
    import software_inventory

    _add(conn, "A-5", ip="10.0.0.5", collect_ok=0)
    r = software_inventory.collect_software(conn, runner=lambda h, c: "",
                                            only_serial="A-5")
    assert r["skipped"] and "還沒納管" in r["skipped"][0]["reason"]


def test_帳號收集沒進迴圈要回原因(conn):
    import account_inventory

    _add(conn, "A-6", ip="10.0.0.6", collect_ok=0)
    r = account_inventory.collect_accounts(conn, runner=lambda h, c: "",
                                           only_serial="A-6")
    assert r["skipped"] and "還沒納管" in r["skipped"][0]["reason"]


def test_硬體收集沒進迴圈要回原因(conn):
    import host_spec_collector

    _add(conn, "A-7", ip="10.0.0.7", collect_ok=0)
    r = host_spec_collector.run_collection(conn, only_serial="A-7")
    assert r["total"] == 0
    assert r["skipped"] and "還沒納管" in r["skipped"][0]["reason"]


# ═══ 序號兩個來源要對得起來 ═════════════════════════════════════════

def test_同一台的兩種序號寫法不可以誤報成矛盾():
    """AIX 的 `uname -u` 給 systemid（帶 `IBM,02` 前綴），

    `prtconf` 給裸序號。直接比字串會把同一台的兩種寫法報成矛盾——
    假警報會讓真正的矛盾被淹掉。
    """
    import api

    v = api._serial_view({"hw_serial": "IBM,0206781A091"}, {"serial": "06781A091"})
    assert v["conflict"] is False
    v2 = api._serial_view({"hw_serial": "06781A091"}, {"serial": "IBM,0206781A091"})
    assert v2["conflict"] is False


def test_真的不一樣要標矛盾而且不挑邊():
    import api

    v = api._serial_view({"hw_serial": "ABC123456"}, {"serial": "XYZ987654"})
    assert v["conflict"] is True
    assert len(v["sources"]) == 2               # 兩個都列出來
    assert "不替你挑邊" in v["note"]


def test_太短的序號不可以用結尾比():
    """`123` 是一堆序號的結尾，那樣比會把不同機器判成同一台。"""
    import api

    v = api._serial_view({"hw_serial": "123"}, {"serial": "999123"})
    assert v["conflict"] is True


def test_只有一邊有值要講另一邊為什麼沒有():
    import api

    v = api._serial_view({"hw_serial": ""}, {"serial": "IBM,06781A091"})
    assert v["value"] == "IBM,06781A091"
    assert v["conflict"] is False
    assert "沒有收到" in v["note"]
    assert "沒有第二個來源可以對帳" in v["note"]


def test_兩邊都沒有不可以說這台沒有序號():
    import api

    v = api._serial_view({"hw_serial": ""}, None)
    assert v["value"] is None
    assert "不是這台沒有序號" in v["note"]
