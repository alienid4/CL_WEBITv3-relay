"""盤點主機清單＋納管後自動收集（2026-09-16）。

使用者兩句話決定了這兩件事：

- 「我只知道四台? 不知道哪四台，應該是先列出哪幾台，我再進去點後才是那一台的全部帳號資訊」
  → 補了適用範圍：**「每個盤點都是這概念」**（服務、軟體、EOS 都套）
- 「我已經納管了，也收集規格，但為什麼軟體跟帳號都沒有收集到」
  → 「以後納管後 要自動跑一次，收集全部 是手動狀態」

要守的：

1. 主機清單只列**實際收到資料的主機**——不可以用 hardware 全表 LEFT JOIN 假裝每台都盤點過，
   那會讓「盤點了 4 台」變成「盤點了 4789 台其中 4785 台是 0 筆」
2. 收到資料但資產庫查不到的 IP 要標出來，不可以靜默當成已登記
3. EOS 只列查得到日期的——「沒有資料」不等於「還在支援」
4. 納管後自動收集：四樣各自獨立，**一樣失敗不影響其他樣**，也不會把納管標成失敗
5. 同一台不重複啟動（連按兩次納管）
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import inventory_hosts as ih  # noqa: E402
import post_onboard  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="A-1", hostname="h1", ip="192.0.2.1",
                       os="RedHat 9.4", environment="正式", physical_location="內湖機房",
                       asset_status="使用中")
    db.insert_hardware(c, asset_serial="A-2", hostname="h2", ip="192.0.2.2",
                       os="Windows Server 2019", environment="測試", asset_status="使用中")
    c.commit()
    try:
        yield c
    finally:
        c.close()


# ===== 服務／軟體的主機清單 =====

def _add_service(conn, ip, port, exposure="all", guess_source="process", is_infra=0, serial=None):
    conn.execute(
        "INSERT INTO host_service (ip, asset_serial, proto, port, exposure, guess_source, "
        "is_infra, source, first_seen, last_seen) VALUES (?,?,'tcp',?,?,?,?,'ssh_ss',?,?)",
        (ip, serial, port, exposure, guess_source, is_infra,
         "2026-09-16 10:00:00", "2026-09-16 10:00:00"))


def test_服務清單只列收到資料的主機(conn):
    _add_service(conn, "192.0.2.1", 22, serial="A-1")
    _add_service(conn, "192.0.2.1", 443, serial="A-1")
    conn.commit()
    rows = ih.hosts(conn, "service")
    assert len(rows) == 1, "A-2 沒收到服務，不可以用 LEFT JOIN 塞一筆 0 進來"
    r = rows[0]
    assert r["ip"] == "192.0.2.1" and r["items"] == 2
    assert r["hostname"] == "h1" and r["physical_location"] == "內湖機房"
    assert r["registered"] is True
    assert r["collected_at"] == "2026-09-16 10:00:00"


def test_服務清單帶對外曝露與猜測數(conn):
    _add_service(conn, "192.0.2.1", 22, exposure="all", guess_source="process")
    _add_service(conn, "192.0.2.1", 3306, exposure="localhost", guess_source="port")
    _add_service(conn, "192.0.2.1", 123, exposure="all", guess_source="port", is_infra=1)
    conn.commit()
    r = ih.hosts(conn, "service")[0]
    assert r["exposed"] == 2 and r["guessed"] == 2 and r["infra"] == 1


def test_收到資料但資產庫沒這個IP要標出來(conn):
    _add_service(conn, "203.0.113.9", 22)
    conn.commit()
    r = [x for x in ih.hosts(conn, "service") if x["ip"] == "203.0.113.9"][0]
    assert r["registered"] is False, "不可以靜默當成已登記"
    assert r["hostname"] is None


def test_軟體清單(conn):
    conn.execute("INSERT INTO host_package (ip, asset_serial, name, version, source, "
                 "first_seen, last_seen) VALUES "
                 "('192.0.2.2','A-2','openssl','3.0.7','rpm','2026-09-16','2026-09-16 11:00:00')")
    conn.commit()
    rows = ih.hosts(conn, "software")
    assert len(rows) == 1 and rows[0]["ip"] == "192.0.2.2" and rows[0]["items"] == 1


def test_沒收過就回空清單不是爆掉(conn):
    assert ih.hosts(conn, "service") == []
    assert ih.hosts(conn, "software") == []


def test_匯出表頭與資料對得起來(conn):
    _add_service(conn, "192.0.2.1", 22)
    conn.commit()
    hdr = ih.headers("service")
    rows = ih.export_rows(conn, "service")
    assert len(rows) == 1 and len(rows[0]) == len(hdr)
    assert hdr[0] == "IP" and hdr[-1] == "最後盤點"


# ===== EOS =====

def test_EOS只列查得到日期的(conn):
    rows = ih.eos_hosts(conn)
    for r in rows:
        assert r["os_eos"] or r["hw_eos"], "查不到日期的不該出現——沒有資料不等於還在支援"


def test_EOS排除退役資產(conn):
    db.insert_hardware(conn, asset_serial="A-9", hostname="old", ip="192.0.2.9",
                       os="RedHat 6.5", asset_status="報廢")
    conn.commit()
    assert all(r["asset_serial"] != "A-9" for r in ih.eos_hosts(conn))


# ===== 納管後自動收集 =====

def test_一樣失敗不影響其他樣(conn, monkeypatch):
    calls = []

    def fake(conn_, job, serial):
        calls.append(job)
        if job == "account":
            raise PermissionError("需要 root")
        return True, "收到了"

    monkeypatch.setattr(post_onboard, "_collect_one",
                        lambda c, j, s: fake(c, j, s) if j != "account" else (False, "需要 root"))
    results = post_onboard.run_now(conn, "A-1", by="tester")
    assert [r["job"] for r in results] == list(post_onboard.JOBS), "四樣都要跑到"
    assert [r["ok"] for r in results] == [True, True, True, False]
    assert results[-1]["message"] == "需要 root"


def test_結果要記進採集紀錄_講清楚哪一樣沒收到(conn, monkeypatch):
    monkeypatch.setattr(post_onboard, "_collect_one",
                        lambda c, j, s: (j != "software", "收到了" if j != "software" else "沒有收到資料"))
    post_onboard.run_now(conn, "A-1", by="tester")
    import collect_log

    rec = collect_log.recent(conn, kinds=["post_onboard"])[0]
    assert rec["kind_label"] == "納管後自動收集"
    assert "軟體" in rec["message"] and "沒收到" in rec["message"]
    assert rec["ok"] == 0, "有一樣沒收到就不算全綠"


def test_全部收到才算成功(conn, monkeypatch):
    monkeypatch.setattr(post_onboard, "_collect_one", lambda c, j, s: (True, "收到了"))
    post_onboard.run_now(conn, "A-1")
    import collect_log

    assert collect_log.recent(conn, kinds=["post_onboard"])[0]["ok"] == 1


def test_收集本身丟例外也不往外炸(conn, monkeypatch):
    def boom(conn_, only_serial=None, trigger=None, **kw):
        raise OSError("連不上")

    import service_inventory

    monkeypatch.setattr(service_inventory, "collect_services", boom)
    ok, msg = post_onboard._collect_one(conn, "service", "A-1")
    assert ok is False and "連不上" in msg


def test_同一台不重複啟動(monkeypatch):
    monkeypatch.setattr(post_onboard, "_running", {"A-1"})
    assert post_onboard.start(None, "A-1") is False
    assert post_onboard.start(None, "") is False, "沒有序號就不要跑"


# ===== 帳號盤點主機清單：不可以因為欄位名寫錯就整支壞掉（2026-09-17）=====
#
# 踩過的坑：SQL 寫了 `a.collected_at`，但 host_account 的時間欄是 `last_seen`
# → 整支 500 → 前端 .catch 把錯誤吞掉 → 畫面顯示「0 台／這一輪沒有收到任何主機的
# 帳號資料」，但實際上有 7 台。**程式壞掉被顯示成「沒有資料」是最糟的失敗方式。**


def test_帳號盤點主機清單_真的查得出來(conn):
    import account_inventory as ai

    for u, uid, sudo, never in (("root", 0, 1, 0), ("alice", 1001, 1, 0), ("bob", 1002, 0, 1)):
        conn.execute(
            "INSERT INTO host_account (ip, asset_serial, username, uid, kind, is_sudoer, "
            "never_logged_in, source, first_seen, last_seen) "
            "VALUES ('192.0.2.1','A-1',?,?,'human',?,?,'ssh','2026-09-17','2026-09-17 01:20:17')",
            (u, uid, sudo, never))
    conn.commit()
    rows = ai.inventoried_hosts(conn)
    assert len(rows) == 1, "有資料就要列得出來，不可以回 0 台"
    r = rows[0]
    assert r["ip"] == "192.0.2.1" and r["hostname"] == "h1"
    assert r["accounts"] == 3 and r["human"] == 3 and r["sudoers"] == 2 and r["never_login"] == 1
    assert r["collected_at"] == "2026-09-17 01:20:17", "時間欄是 last_seen，不是 collected_at"


def test_時間欄名稱不寫死(conn):
    import account_inventory as ai

    assert ai._account_time_col(conn) == "last_seen"


def test_已消失的帳號不算進去(conn):
    import account_inventory as ai

    conn.execute("INSERT INTO host_account (ip, username, kind, source, first_seen, last_seen) "
                 "VALUES ('192.0.2.1','ghost','human','ssh','2026-01-01','2026-01-01')")
    conn.execute("INSERT INTO host_account (ip, username, kind, source, first_seen, last_seen, gone_at) "
                 "VALUES ('192.0.2.1','deleted','human','ssh','2026-01-01','2026-01-01','2026-09-01')")
    conn.commit()
    r = ai.inventoried_hosts(conn)[0]
    assert r["accounts"] == 1, "gone_at 有值＝那個帳號已經不在了，不該再算進現況"


# ===== 部門與窗口＋帳號反查（2026-09-17 使用者）=====
#
# 「這個還缺一個很重要的欄位，對應部門與對應窗口。沒這個以後怎麼找窗口盤點」
# 「帳號 我想要找 例如 webit3 有哪幾台有這帳號」
#
# 盤點的下一步是「去找人」。清單沒有窗口就只能看不能用；
# 而停用離職者帳號、確認服務帳號佈到哪些機器，都是從帳號反查主機。


def _acct(conn, ip, user, uid=1001, sudo=0, nopw=0, never=0, gone=None, serial=None):
    conn.execute(
        "INSERT INTO host_account (ip, asset_serial, username, uid, kind, is_sudoer, "
        "sudo_nopasswd, never_logged_in, source, first_seen, last_seen, gone_at) "
        "VALUES (?,?,?,?,'service',?,?,?,'ssh','2026-09-17','2026-09-17 01:20:17',?)",
        (ip, serial, user, uid, sudo, nopw, never, gone))


def test_主機清單帶部門與窗口(conn):
    import account_inventory as ai

    conn.execute("UPDATE hardware SET usage_unit='證券資訊部', user_name='王小明', "
                 "custodian='李小華' WHERE asset_serial='A-1'")
    _acct(conn, "192.0.2.1", "webit3scan", serial="A-1")
    conn.commit()
    r = ai.inventoried_hosts(conn)[0]
    assert r["department"] == "證券資訊部"
    assert r["contact"] == "王小明" and r["custodian"] == "李小華"
    assert "使用單位（部門）" in ai.HOST_EXPORT_HEADERS
    assert "使用者（窗口）" in ai.HOST_EXPORT_HEADERS
    assert len(ai.hosts_export_rows(conn)[0]) == len(ai.HOST_EXPORT_HEADERS)


def test_反查帳號在哪幾台(conn):
    import account_inventory as ai

    conn.execute("UPDATE hardware SET usage_unit='資訊架構部', user_name='張三' "
                 "WHERE asset_serial='A-1'")
    _acct(conn, "192.0.2.1", "webit3scan", sudo=1, serial="A-1")
    _acct(conn, "192.0.2.2", "webit3scan", serial="A-2")
    _acct(conn, "192.0.2.2", "oracle")
    conn.commit()
    rows = ai.hosts_with_account(conn, "webit3scan")
    assert [r["ip"] for r in rows] == ["192.0.2.1", "192.0.2.2"]
    assert rows[0]["department"] == "資訊架構部" and rows[0]["contact"] == "張三",         "反查結果要帶窗口——下一步就是去找人"
    assert rows[0]["is_sudoer"] == 1
    assert len(ai.hosts_with_account(conn, "oracle")) == 1


def test_反查大小寫不敏感_空字串回空(conn):
    import account_inventory as ai

    _acct(conn, "192.0.2.1", "WebIT3Scan")
    conn.commit()
    assert len(ai.hosts_with_account(conn, "webit3scan")) == 1
    assert ai.hosts_with_account(conn, "  ") == []


def test_反查不列已消失的帳號(conn):
    import account_inventory as ai

    _acct(conn, "192.0.2.1", "leaver")
    _acct(conn, "192.0.2.2", "leaver", gone="2026-09-01")
    conn.commit()
    rows = ai.hosts_with_account(conn, "leaver")
    assert [r["ip"] for r in rows] == ["192.0.2.1"], "已刪除的帳號不該還算「這台有這個帳號」"


def test_反查_收到帳號但資產庫沒這個IP要標出來(conn):
    import account_inventory as ai

    _acct(conn, "203.0.113.9", "webit3scan")
    conn.commit()
    r = ai.hosts_with_account(conn, "webit3scan")[0]
    assert r["registered"] is False and r["hostname"] is None


def test_反查匯出欄位對得上(conn):
    import account_inventory as ai

    _acct(conn, "192.0.2.1", "webit3scan")
    conn.commit()
    rows = ai.account_hosts_export_rows(conn, "webit3scan")
    assert len(rows) == 1 and len(rows[0]) == len(ai.ACCOUNT_HOSTS_HEADERS)


# ===== 輔助程式：名稱服務掛住時不可以卡死、也不可以靜默少帳號 =====


def test_輔助程式有NSS退路與註記():
    import account_collector as ac

    s = ac.ACCOUNT_HELPER_SCRIPT
    assert "timeout 15 getent passwd" in s, "NSS 掛住時要有時間上限，不然整個收集卡死"
    assert "getent -s files passwd" in s, "要能退回只讀本機 /etc/passwd"
    assert "=== NOTE" in s, "退回來的時候必須標明，不可以靜默少掉網域帳號"


def test_NOTE段落解析得出來():
    import account_collector as ac

    raw = "\n".join([
        "=== NOTE",
        "名稱服務(NSS)沒有回應，已退回只讀本機 /etc/passwd",
        "=== SHADOW",
        "ACCT root :: root P :: ",
        "=== SUDOERS",
        "=== KEYS",
        "KEYS root 0",
        "=== END",
        "",
    ])
    out = ac.split_helper_output(raw)
    assert out is not None
    assert "名稱服務" in out["NOTE"]
    assert "ACCT root" in out["SHADOW"]
