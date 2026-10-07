"""掃描範圍與涵蓋紀錄：修正「沒掃過被算成失聯」（2026-09-16）。

使用者拿 `ping 10.92.194.65` 的成功輸出問「怎麼會判失聯?」。查證後的真因有兩個：

1. 掃描範圍讀的是 `connections`，而那張表是空的 → 只 fallback 掃本機一段，
   其餘網段根本沒掃，卻全被算成「不在最新掃描的存活清單裡」＝失聯
2. 掃描只探 TCP 22/445/3389/5985，ping 得通但這四個埠都關的機器一樣被判死

使用者決定：掃描範圍「全部（182 段）列出來，但可以由我來選」、「每週一次，時間我再指定」。

要守的：
1. `list_scope` **不替使用者過濾**——建議排除只是旗標
2. 勾選同步成 connections；沒勾的**停用不刪**（保留上次掃描時間）
3. 每次掃描記下涵蓋了哪些網段；掃描失敗的那段 ok=0，不算掃過
4. 沒涵蓋到的 IP 判「未涵蓋」不是「失聯」；**沒有涵蓋紀錄時維持舊行為**
5. TCP 全不通但 ICMP 通 → 仍算活著（回空的開放埠清單）
6. 每週排程：星期幾 + 時間，時間格式要擋
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state as ms  # noqa: E402
import net_scan  # noqa: E402
import pipeline  # noqa: E402
import scan_scope  # noqa: E402
import scan_service  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    c.execute(
        "INSERT INTO network_segment (cidr, raw_cidr, location, purpose_desc, environment, "
        "category, scan_excluded, scan_note, row_no, imported_at) VALUES "
        "('192.0.2.0/24','192.0.2.0/24','內湖機房','SERVER','正式','SERVER',0,NULL,1,'2026-09-16'),"
        "('198.51.100.0/24','198.51.100.0/24','板橋機房','員工電腦','正式','PC',1,'建議排除掃描',2,'2026-09-16'),"
        "(NULL,'寫壞的那一列','某處','?','測試','?',0,NULL,3,'2026-09-16')")
    c.commit()
    try:
        yield c
    finally:
        c.close()


# ===== 範圍設定 =====

def test_列出全部不替使用者過濾(conn):
    s = scan_scope.list_scope(conn)
    assert s["total_segments"] == 3, "建議排除的、解析不掉的都要列出來"
    assert s["recommended_exclude"] == 1 and s["unparsable"] == 1
    excluded = [i for i in s["items"] if i["recommended_exclude"]][0]
    assert excluded["exclude_note"] == "建議排除掃描"
    assert excluded["in_scope"] is False
    assert s["in_scope_segments"] == 0


def test_位址數要算得出來(conn):
    s = scan_scope.list_scope(conn)
    by_cidr = {i["cidr"]: i for i in s["items"]}
    assert by_cidr["192.0.2.0/24"]["addresses"] == 254
    assert by_cidr[None]["addresses"] == 0


def test_勾選同步成掃描來源(conn):
    ids = [i["id"] for i in scan_scope.list_scope(conn)["items"] if i["cidr"]]
    out = scan_scope.apply_scope(conn, ids)
    assert sorted(out["added"]) == ["192.0.2.0/24", "198.51.100.0/24"]
    assert out["addresses"] == 254 * 2
    s = scan_scope.list_scope(conn)
    assert s["in_scope_segments"] == 2 and s["in_scope_addresses"] == 508
    # 這才是重點：掃描引擎讀得到了
    import run_real_scan

    assert {src.cidr for src in run_real_scan.scan_targets(conn)} == {"192.0.2.0/24", "198.51.100.0/24"}


def test_取消勾選是停用不是刪除(conn):
    items = [i for i in scan_scope.list_scope(conn)["items"] if i["cidr"]]
    keep = items[0]["id"]
    scan_scope.apply_scope(conn, [i["id"] for i in items])
    out = scan_scope.apply_scope(conn, [keep])
    assert out["disabled"] == ["198.51.100.0/24"]
    rows = conn.execute("SELECT target, enabled FROM connections ORDER BY target").fetchall()
    assert len(rows) == 2, "停用不刪除——刪掉就沒人記得曾經掃過它"
    assert dict(rows)["198.51.100.0/24"] == 0
    # 再勾回來是恢復，不是又新增一筆
    again = scan_scope.apply_scope(conn, [i["id"] for i in items])
    assert again["re_enabled"] == ["198.51.100.0/24"] and not again["added"]
    assert conn.execute("SELECT COUNT(*) FROM connections").fetchone()[0] == 2


# ===== 涵蓋紀錄 =====

class FakeSource:
    def __init__(self, cidr, name):
        self.cidr = cidr
        self.name = name


def test_記錄涵蓋範圍並判定是否涵蓋(conn):
    scan_scope.record_coverage(conn, "2026-09-16 01:00:00",
                              [FakeSource("192.0.2.0/24", "A"), FakeSource("203.0.113.0/24", "B")])
    nets = scan_scope.covered_networks(conn, "2026-09-16 01:00:00")
    assert len(nets) == 2
    assert scan_scope.is_covered("192.0.2.5", nets) is True
    assert scan_scope.is_covered("198.51.100.5", nets) is False


def test_掃描失敗的那段不算掃過(conn):
    scan_scope.record_coverage(conn, "T1", [FakeSource("192.0.2.0/24", "A")])
    scan_scope.mark_failed(conn, "T1", "A")
    nets = scan_scope.covered_networks(conn, "T1")
    assert nets == [], "掃描失敗不等於掃過了，那段的機器不該被判成失聯"


def test_沒有涵蓋紀錄時維持舊行為(conn):
    nets = scan_scope.covered_networks(conn, "不存在的時間")
    assert nets == []
    assert scan_scope.is_covered("192.0.2.5", nets) is True, \
        "沒有證據時不可以把全站改標未涵蓋"


# ===== 狀態判定 =====

def test_沒掃過判未涵蓋而不是失聯():
    assert ms.classify(True, seen_in_scan=False, collect_ok=None, covered=False) == ms.NOT_COVERED
    assert ms.classify(True, seen_in_scan=False, collect_ok=None, covered=True) == ms.LOST
    assert ms.NOT_COVERED in ms.NEEDS_ACTION_STATES, "沒掃過仍然要處理（去加掃描範圍）"
    assert ms.NEXT_ACTION[ms.NOT_COVERED]
    assert "not_covered" in pipeline.STAGE_INDEX
    assert "not_covered" in pipeline.TODO_STAGES


def test_退役與豁免優先於未涵蓋():
    assert ms.classify(True, False, None, retired=True, covered=False) == ms.RETIRED
    assert ms.classify(True, False, None, exempt=True, covered=False) == ms.EXEMPT


def test_統計把未涵蓋跟失聯分開(tmp_path):
    p = tmp_path / "s.db"
    db.init_db(p)
    c = db.get_connection(p)
    try:
        db.insert_hardware(c, asset_serial="IN-1", hostname="in1", ip="192.0.2.10", asset_status="使用中")
        db.insert_hardware(c, asset_serial="OUT-1", hostname="out1", ip="198.51.100.10", asset_status="使用中")
        c.execute("INSERT INTO scan_history (hostname, ip, scan_ok, scan_time) "
                  "VALUES ('other','192.0.2.99',1,'2026-09-16 01:00:00')")
        c.commit()
        scan_scope.record_coverage(c, "2026-09-16 01:00:00", [FakeSource("192.0.2.0/24", "A")])
        s = ms.summarize(c)
    finally:
        c.close()
    assert s["counts"][ms.LOST] == 1, "掃了卻沒回應的那台＝失聯"
    assert s["counts"][ms.NOT_COVERED] == 1, "網段沒掃到的那台＝未涵蓋"
    assert s["not_covered_total"] == 1 and s["covered_segments"] == 1


# ===== 掃描本身 =====

def test_TCP不通但ping通仍算活著(monkeypatch):
    monkeypatch.setattr(net_scan, "host_ping_alive", lambda ip, timeout=1.0: True)
    # 所有 port 都逾時
    monkeypatch.setattr(net_scan.socket, "socket", _timeout_socket())
    assert net_scan._probe_host("192.0.2.65") == [], "ping 得通就是活著，只是沒有開放埠"


def test_TCP不通ping也不通才算掃不到(monkeypatch):
    monkeypatch.setattr(net_scan, "host_ping_alive", lambda ip, timeout=1.0: False)
    monkeypatch.setattr(net_scan.socket, "socket", _timeout_socket())
    assert net_scan._probe_host("192.0.2.66") is None


def test_ping工具不存在時回False不可假裝活著(monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise FileNotFoundError("no ping")
    monkeypatch.setattr(subprocess, "run", boom)
    assert net_scan.host_ping_alive("192.0.2.1") is False,         "工具缺席不可以說成活著——那會製造假資料"


def _timeout_socket():
    class S:
        def __init__(self, *a, **k):
            pass

        def settimeout(self, t):
            pass

        def connect_ex(self, addr):
            return 110          # ETIMEDOUT

        def close(self):
            pass
    return S


# ===== 每週排程 =====

def test_每週排程_星期幾與時間(conn):
    scan_service.set_schedule(conn, True, "weekly", "02:30", 6, weekday=6)
    s = scan_service.get_schedule(conn)
    assert s["mode"] == "weekly" and s["time"] == "02:30"
    assert s["weekday"] == 6 and s["weekday_label"] == "週日"


def test_每週排程_要指定星期幾_時間格式要擋(conn):
    with pytest.raises(ValueError):
        scan_service.set_schedule(conn, True, "weekly", "02:30", 6, weekday=None)
    with pytest.raises(ValueError):
        scan_service.set_schedule(conn, True, "weekly", "25:00", 6, weekday=1)
    with pytest.raises(ValueError):
        scan_service.set_schedule(conn, True, "亂寫", "02:30", 6)


def test_每週排程_只在那天到點才觸發(conn, monkeypatch):
    scan_service.set_schedule(conn, True, "weekly", "02:00", 6, weekday=6)   # 週日
    monkeypatch.setattr(scan_service, "get_last_schedule_run_time", lambda c: None)
    sunday_late = datetime(2026, 9, 20, 3, 0)      # 2026-09-20 是週日
    saturday_late = datetime(2026, 9, 19, 3, 0)
    sunday_early = datetime(2026, 9, 20, 1, 0)
    assert sunday_late.weekday() == 6
    assert scan_service._due(conn, sunday_late) is True
    assert scan_service._due(conn, saturday_late) is False, "不是那一天不跑"
    assert scan_service._due(conn, sunday_early) is False, "還沒到時間不跑"


def test_每週排程_同一週不重複跑(conn, monkeypatch):
    scan_service.set_schedule(conn, True, "weekly", "02:00", 6, weekday=6)
    monkeypatch.setattr(scan_service, "get_last_schedule_run_time",
                        lambda c: datetime(2026, 9, 20, 2, 1))
    assert scan_service._due(conn, datetime(2026, 9, 20, 5, 0)) is False
    assert scan_service._due(conn, datetime(2026, 9, 27, 2, 1)) is True, "下一週要再跑"


def test_每日排程行為沒被動到(conn, monkeypatch):
    scan_service.set_schedule(conn, True, "daily", "01:00", 6)
    monkeypatch.setattr(scan_service, "get_last_schedule_run_time", lambda c: None)
    assert scan_service._due(conn, datetime(2026, 9, 16, 2, 0)) is True
    assert scan_service._due(conn, datetime(2026, 9, 16, 0, 30)) is False
