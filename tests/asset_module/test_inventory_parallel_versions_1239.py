"""Codex 1239：軟體盤點「同一台並存兩個版本」時，快照差異不可漏掉其中一版。

以前 _rows 用 (主機, 名稱（架構）) 當 dict 鍵，後一列把前一列蓋掉：
1.0 的廠商改了 → 差異 0；主機明細也只看得到一個版本。
比對契約：兩次都只有一列＝照舊一對一（升版＝變更）；任一次有並存版本＝依版本配對，
項目名帶版本，每版各自一筆。全部假資料（IP 一律 10.99.x）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import inventory_flow as inv  # noqa: E402

IP = "10.99.3.1"


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _pkg(conn, name, version, vendor="V", ip=IP):
    conn.execute("INSERT INTO host_package (ip, asset_serial, name, version, arch, vendor, source) VALUES (?,?,?,?,?,?,?)",
                 (ip, "S-1", name, version, "x86_64", vendor, "ssh_rpm"))


def _two_tasks(conn, mutate):
    inv.create_task(conn, "software", "t")
    mutate()
    conn.commit()
    return inv.create_task(conn, "software", "t")


def test_並存版本其中一版廠商改了要列出差異(tmp_path):
    conn = _conn(tmp_path)
    _pkg(conn, "java", "1.0", vendor="Old")
    _pkg(conn, "java", "2.0", vendor="Same")
    conn.commit()
    t2 = _two_tasks(conn, lambda: conn.execute(
        "UPDATE host_package SET vendor='New' WHERE name='java' AND version='1.0'"))
    rows = inv.diffs(conn, "software", t2["id"])["rows"]
    assert t2["diff_count"] == 1 == len(rows)
    assert rows[0]["kind"] == "變更"
    assert rows[0]["item"] == "java（x86_64） 1.0"
    assert "Old" in rows[0]["before"] and "New" in rows[0]["after"]
    rep = {x["k"]: x["v"] for x in inv.report(conn, "software", t2["id"])["lines"]}
    assert rep["差異"] == 1


def test_主機明細要看得到兩個版本_加總等於明細(tmp_path):
    conn = _conn(tmp_path)
    _pkg(conn, "java", "1.0")
    _pkg(conn, "java", "2.0")
    conn.commit()
    t = inv.create_task(conn, "software", "t")
    host = inv.result(conn, "software", t["id"], host=IP)
    assert sorted(r["version"] for r in host["rows"]) == ["1.0", "2.0"]
    unit = inv.result(conn, "software", t["id"])
    assert unit["rows"][0]["count"] == host["total"] == 2


def test_並存版本新增一版與移除一版各一筆(tmp_path):
    conn = _conn(tmp_path)
    _pkg(conn, "java", "1.0")
    _pkg(conn, "java", "2.0")
    conn.commit()

    def m():
        conn.execute("DELETE FROM host_package WHERE version='1.0'")
        _pkg(conn, "java", "3.0")
    t2 = _two_tasks(conn, m)
    got = sorted((r["kind"], r["item"]) for r in inv.diffs(conn, "software", t2["id"])["rows"])
    assert got == [("新增", "java（x86_64） 3.0"), ("移除", "java（x86_64） 1.0")]
    # 差異鍵不可撞（每筆各自可處理）
    keys = [r["diff_key"] for r in inv.diffs(conn, "software", t2["id"])["rows"]]
    assert len(keys) == len(set(keys))


def test_單一版本升版照舊算一筆變更_項目名不帶版本(tmp_path):
    """反向：沒有並存版本時行為不變（既有差異鍵與處理狀態不受影響）。"""
    conn = _conn(tmp_path)
    _pkg(conn, "openssl", "3.0.1")
    conn.commit()
    t2 = _two_tasks(conn, lambda: conn.execute("UPDATE host_package SET version='3.0.2'"))
    rows = inv.diffs(conn, "software", t2["id"])["rows"]
    assert [(r["kind"], r["item"]) for r in rows] == [("變更", "openssl（x86_64）")]


def test_並存版本都沒變就沒有差異(tmp_path):
    conn = _conn(tmp_path)
    _pkg(conn, "java", "1.0")
    _pkg(conn, "java", "2.0")
    conn.commit()
    t2 = _two_tasks(conn, lambda: None)
    assert t2["diff_count"] == 0


# ===== 審查補（P2／P3）：舊任務的差異數、處理狀態孤兒、同項目多列 =====

def test_舊任務存的差異數過期_清單重算後等於下鑽且待處理不為負(tmp_path):
    """模擬規則改版前建的任務：存的 diff_count 是舊算法（0），且有一筆舊鍵的處理狀態（現在已不存在的差異）。"""
    conn = _conn(tmp_path)
    _pkg(conn, "java", "1.0", vendor="Old")
    _pkg(conn, "java", "2.0", vendor="Same")
    conn.commit()
    t2 = _two_tasks(conn, lambda: conn.execute(
        "UPDATE host_package SET vendor='New' WHERE name='java' AND version='1.0'"))
    tid = t2["id"]
    # 舊版：diff_count 存 0、沒有規則版本；舊鍵（不帶版本）被標過「已處理」
    conn.execute("UPDATE inv_task SET diff_count = 0, diff_done = NULL, diff_ver = NULL WHERE id = ?", (tid,))
    conn.execute("INSERT INTO inv_task_diff_state (task_id, diff_key, status, updated_at) VALUES (?,?,?,?)",
                 (tid, f"{IP}::java（x86_64）::changed", "已處理", "2026-11-01"))
    conn.commit()
    lst = next(x for x in inv.list_tasks(conn, "software") if x["id"] == tid)
    d = inv.diffs(conn, "software", tid)["rows"]
    assert lst["diff_count"] == len(d) == 1
    assert lst["diff_open"] == sum(1 for x in d if x["status"] == "待處理") == 1
    assert lst["diff_open"] >= 0
    # 存回去了：下次讀不用再重算
    row = conn.execute("SELECT diff_count, diff_ver FROM inv_task WHERE id = ?", (tid,)).fetchone()
    assert row["diff_count"] == 1 and row["diff_ver"] == inv.DIFF_VER


def test_處理一筆之後清單待處理跟著減(tmp_path):
    conn = _conn(tmp_path)
    _pkg(conn, "java", "1.0", vendor="Old")
    _pkg(conn, "java", "2.0", vendor="Old")
    conn.commit()
    t2 = _two_tasks(conn, lambda: conn.execute("UPDATE host_package SET vendor='New'"))
    d = inv.diffs(conn, "software", t2["id"])["rows"]
    assert len(d) == 2
    inv.set_status(conn, "software", t2["id"], d[0]["diff_key"], "已處理", None, "t")
    lst = next(x for x in inv.list_tasks(conn, "software") if x["id"] == t2["id"])
    assert (lst["diff_count"], lst["diff_open"]) == (2, 1)


def _svc(conn, port, process, bind=None, ip=IP):
    conn.execute("INSERT INTO host_service (ip, asset_serial, proto, port, bind_addr, exposure, process, source, "
                 "first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (ip, "S-1", "tcp", port, bind, "all", process, "test", "2026-01-01", "2026-01-01"))


def test_服務同埠多列_bind空_每列都配到且差異鍵不重複(tmp_path):
    conn = _conn(tmp_path)
    _svc(conn, 8080, "a")
    _svc(conn, 8080, "b")
    conn.commit()
    inv.create_task(conn, "service", "t")
    conn.execute("UPDATE host_service SET process = 'c' WHERE process = 'a'")
    _svc(conn, 8080, "d")
    conn.commit()
    t2 = inv.create_task(conn, "service", "t")
    rows = inv.diffs(conn, "service", t2["id"])["rows"]
    # a→c 一筆變更、d 一筆新增；b 沒變不列
    assert sorted(r["kind"] for r in rows) == ["新增", "變更"]
    keys = [r["diff_key"] for r in rows]
    assert len(keys) == len(set(keys))
    # 主機明細三列都在（加總＝下鑽）
    assert len(inv.result(conn, "service", t2["id"], host=IP)["rows"]) == 3


def test_新服務同埠兩列都新增_差異鍵加序號不撞(tmp_path):
    conn = _conn(tmp_path)
    _svc(conn, 22, "sshd")
    conn.commit()
    inv.create_task(conn, "service", "t")
    _svc(conn, 9000, "x")
    _svc(conn, 9000, "y")
    conn.commit()
    t2 = inv.create_task(conn, "service", "t")
    rows = inv.diffs(conn, "service", t2["id"])["rows"]
    assert [r["kind"] for r in rows] == ["新增", "新增"]
    keys = [r["diff_key"] for r in rows]
    assert len(set(keys)) == 2 and any(k.endswith("#2") for k in keys)
    for r in rows:                        # 兩筆各自可處理
        inv.set_status(conn, "service", t2["id"], r["diff_key"], "已處理", None, "t")
    lst = next(x for x in inv.list_tasks(conn, "service") if x["id"] == t2["id"])
    assert lst["diff_open"] == 0


def test_舊任務有存數字但規則版本舊_也要重算(tmp_path):
    """審查 P3-1：diff_done 有值、diff_ver 是舊版（不是 NULL）也要重算；守住以後 DIFF_VER 升版。"""
    conn = _conn(tmp_path)
    _pkg(conn, "java", "1.0", vendor="Old")
    _pkg(conn, "java", "2.0", vendor="Same")
    conn.commit()
    t2 = _two_tasks(conn, lambda: conn.execute(
        "UPDATE host_package SET vendor='New' WHERE name='java' AND version='1.0'"))
    tid = t2["id"]
    conn.execute("UPDATE inv_task SET diff_count = 0, diff_done = 0, diff_ver = ? WHERE id = ?", (inv.DIFF_VER - 1, tid))
    conn.commit()
    lst = next(x for x in inv.list_tasks(conn, "software") if x["id"] == tid)
    assert lst["diff_count"] == len(inv.diffs(conn, "software", tid)["rows"]) == 1


def test_補欄遇到另一個請求已補好_不報錯(tmp_path):
    """審查 P3-2：首次上線兩個請求同時 ALTER，後到的遇到 duplicate column 要略過，不回 500。"""
    import sqlite3
    conn = _conn(tmp_path)
    real = conn.execute

    class Racy:
        def __init__(self, c):
            self.c = c
            self.fired = False

        def execute(self, sql, *a):
            if sql.startswith("PRAGMA table_info(inv_task)") and not self.fired:
                self.fired = True
                return iter([])                       # 假裝還沒看到新欄位 → 會去 ALTER，但欄位其實已存在
            return real(sql, *a)

    inv.ensure_tables(conn)                            # 先補好
    inv.ensure_tables(Racy(conn))                      # 第二個請求：拿到舊的欄位清單，ALTER 撞 duplicate column
    cols = {r[1] for r in conn.execute("PRAGMA table_info(inv_task)")}
    assert {"diff_done", "diff_ver"} <= cols
    assert sqlite3
