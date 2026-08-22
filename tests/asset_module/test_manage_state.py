"""納管四態：未登記／未納管／已納管／失聯。

使用者 2026-07-19 指出的關鍵區別：**納管狀態與資產狀態是兩條各自獨立的軸**。
一台機器可以同時是「使用中」（業務狀態）而且「連不進去」（納管狀態）——
兩句都對、都有用，混成一欄就會丟掉其中一個資訊。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import manage_state as ms  # noqa: E402


# ===== 判定核心（純函式，不碰 DB 不碰網路）=====

def test_四態判定():
    # 沒登記＝未登記，不管掃不掃得到、收不收得到
    assert ms.classify(registered=False, seen_in_scan=True, collect_ok=1) == ms.UNREGISTERED
    # 登記了但這次掃不到＝失聯
    assert ms.classify(registered=True, seen_in_scan=False, collect_ok=1) == ms.LOST
    # 登記＋掃得到＋收得到＝已納管
    assert ms.classify(registered=True, seen_in_scan=True, collect_ok=1) == ms.ONBOARDED
    # 登記＋掃得到，但收不到（試過失敗）或還沒試＝未納管
    assert ms.classify(registered=True, seen_in_scan=True, collect_ok=0) == ms.NOT_ONBOARDED
    assert ms.classify(registered=True, seen_in_scan=True, collect_ok=None) == ms.NOT_ONBOARDED


def test_失聯優先於已納管():
    """曾經收得到、但現在掃不到，要顯示失聯不是已納管——
    顯示成已納管會讓人以為一切正常，錯過「機器不見了」這件事。"""
    assert ms.classify(True, seen_in_scan=False, collect_ok=1) == ms.LOST


def test_每一態都有明確的下一步():
    for s in ms.ALL_STATES:
        assert s in ms.NEXT_ACTION and ms.NEXT_ACTION[s]


# ===== 試連（注入假 runner，不打真網路）=====

def test_試連成功與失敗都要保留原因():
    ok, err = ms.probe_collect("1.2.3.4", "/k", runner=lambda h: (True, None))
    assert ok and err is None
    ok, err = ms.probe_collect("1.2.3.4", "/k",
                               runner=lambda h: (False, "Permission denied (publickey)"))
    assert not ok
    # 原因要原樣保留：「Permission denied」跟「Connection timed out」要做的事完全不同
    assert "Permission denied" in err


# ===== 全站統計：四態互斥且窮盡 =====

def _seed(db_path):
    conn = db.get_connection(db_path)
    try:
        # 已納管：登記＋掃得到＋收得到
        db.insert_hardware(conn, asset_serial="A-OK", hostname="ok1", ip="10.0.0.1",
                           environment="正式")
        # 未納管：登記＋掃得到，但收不到
        db.insert_hardware(conn, asset_serial="A-NO", hostname="no1", ip="10.0.0.2",
                           environment="正式")
        # 失聯：登記了但掃不到
        db.insert_hardware(conn, asset_serial="A-LOST", hostname="lost1", ip="10.0.0.3",
                           environment="正式")
        conn.execute("UPDATE hardware SET collect_ok=1 WHERE asset_serial='A-OK'")
        conn.execute("UPDATE hardware SET collect_ok=0 WHERE asset_serial='A-NO'")
        for ip, hn in (("10.0.0.1", "ok1"), ("10.0.0.2", "no1"), ("10.0.0.9", "")):
            conn.execute(
                "INSERT INTO scan_history (scan_time, ip, hostname, scan_ok) "
                "VALUES ('2026-07-19 10:00:00', ?, ?, 1)", (ip, hn))
        conn.commit()
    finally:
        conn.close()


def test_四態互斥且窮盡_加總等於全部已知機器():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db.init_db(db_path)
        _seed(db_path)
        conn = db.get_connection(db_path)
        try:
            s = ms.summarize(conn)
        finally:
            conn.close()

    c = s["counts"]
    assert c[ms.ONBOARDED] == 1       # A-OK
    assert c[ms.NOT_ONBOARDED] == 1   # A-NO
    assert c[ms.LOST] == 1            # A-LOST（登記了但沒掃到）
    assert c[ms.UNREGISTERED] == 1    # 10.0.0.9 掃到但沒登記

    # 窮盡：加總＝所有知道的機器（3 台登記 + 1 台只掃到）
    assert s["total_known"] == 4
    # 互斥：每台只出現一次
    assert len(s["items"]) == 4


def test_收集狀態欄位遷移到既有DB():
    """既有 DB（含 221 正式庫）已經有 hardware，光改 schema.sql 不會補欄位。"""
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "old.db"
        conn = sqlite3.connect(db_path)
        # 模擬「加收集欄位之前」的舊表：其餘欄位比照正式庫（schema 的索引會參照到
        # environment，造得太精簡反而測不到真正的遷移情境）
        conn.execute(
            "CREATE TABLE hardware (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "asset_serial TEXT NOT NULL, hostname TEXT, ip TEXT, environment TEXT, "
            "asset_status TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute("INSERT INTO hardware (asset_serial) VALUES ('OLD-1')")
        conn.commit()
        conn.close()

        db.init_db(db_path)

        conn = db.get_connection(db_path)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
            assert {"collect_ok", "collect_checked_at", "collect_error"} <= cols
            assert conn.execute("SELECT COUNT(*) FROM hardware").fetchone()[0] == 1
        finally:
            conn.close()


def test_失敗原因要濾掉SSH雜訊只留真原因():
    """實測踩到：.221/.224 的失敗原因顯示成「Warning: Permanently added ...」，
    那只是首次連線加 host key 的提示，真正的原因（帳號不存在）反而被蓋掉。

    「Permission denied」（去佈納管腳本）跟「Connection timed out」（去查機器）
    要做的事完全不同——顯示成無關的警告等於叫使用者自己猜。
    """
    raw = (
        "Warning: Permanently added '192.168.1.224' (ED25519) to the list of known hosts.\n"
        "webit3scan@192.168.1.224: Permission denied (publickey,password).\n"
    )
    msg = ms._clean_ssh_error(raw)
    assert "Permanently added" not in msg
    assert "Permission denied" in msg

    # 只有雜訊、沒有真原因時要講人話，不能回空字串
    only_noise = "Warning: Permanently added 'x' (ED25519) to the list of known hosts.\n"
    assert ms._clean_ssh_error(only_noise) == "無回應（連得上但沒有輸出）"

    # 真正的逾時原因要完整保留
    timeout = "ssh: connect to host 10.99.0.1 port 22: Connection timed out"
    assert "Connection timed out" in ms._clean_ssh_error(timeout)


# ===== 組成統計：儀表板該回答的問題 =====

def test_平台歸類_真OS優先於推測():
    """同一個平台在資料裡有各種寫法，不歸類就統計不出「我有幾台 Windows」。"""
    assert ms.platform_of("Rocky Linux 9.7") == "Linux(其他)"
    assert ms.platform_of("Ubuntu 22.04") == "Linux(其他)"
    assert ms.platform_of("Microsoft Windows Server 2022") == "Windows"
    assert ms.platform_of("AIX 7.2") == "AIX/Unix"
    # 真 OS 優先於掃描推測：第一個非空的候選勝出
    assert ms.platform_of("Rocky Linux 9.7", "Windows（RDP 3389）") == "Linux(其他)"
    # 沒有真 OS 時退回推測——推測比空白有用
    assert ms.platform_of(None, "Windows（RDP 3389）") == "Windows"
    # 都認不出來要誠實回未知，不可亂猜一個
    assert ms.platform_of(None, None) == "未知"


def test_平台歸類_Linux依發行版拆四大宗加其他():
    """RHEL/CentOS/Debian/Oracle Linux 資安支援週期各不相同，混成單一「Linux」
    看不出真正組成（使用者 2026-08-11 要求）。CoreOS 不是 RHEL，不可被 RHEL
    規則誤吃——RHEL 要用完整字樣（"red hat enterprise linux"）比對，不能只比「red hat」。"""
    assert ms.platform_of("Red Hat Enterprise Linux 8.10") == "RHEL"
    assert ms.platform_of("RHEL 9") == "RHEL"
    assert ms.platform_of("CentOS 7.9.2009") == "CentOS"
    assert ms.platform_of("Debian 11.7") == "Debian"
    assert ms.platform_of("Oracle Linux 8.10") == "Oracle Linux"
    assert ms.platform_of("Red Hat CoreOS 4.12") == "Linux(其他)"
    assert ms.platform_of("Rocky Linux 9.7") == "Linux(其他)"
    assert ms.platform_of("SUSE Linux Enterprise 12") == "Linux(其他)"
    assert ms.platform_of("某種沒聽過的東西") == "未知"


def test_組成統計_虛實判定容忍混形態():
    """is_vm 在真實資料裡混了 0/1 與 'VM' 字串（納管表單存字串），
    不統一判定就會把同樣是虛擬機的機器分到兩邊。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            db.insert_hardware(conn, asset_serial="C-1", ip="10.0.1.1", os="Rocky Linux 9.7",
                               is_vm=1, environment="正式", asset_status="使用中")
            db.insert_hardware(conn, asset_serial="C-2", ip="10.0.1.2", os=None,
                               is_vm="VM", environment="測試", asset_status="使用中")
            db.insert_hardware(conn, asset_serial="C-3", ip="10.0.1.3", os=None,
                               is_vm=0, environment="正式", asset_status="停用")
            conn.execute(
                "INSERT INTO scan_history (scan_time, ip, os_guess, scan_ok) "
                "VALUES ('2026-07-19 11:00:00','10.0.1.2','Windows（RDP 3389）',1)")
            conn.commit()
            c = ms.composition(conn)
        finally:
            conn.close()

    # C-3 是「停用」＝退役資產，不算進有效統計（total）；total_all/retired_count 才看得到它
    assert c["total"] == 2
    assert c["total_all"] == 3
    assert c["retired_count"] == 1
    assert c["by_platform"]["Linux(其他)"] == 1  # C-1 真 OS（Rocky 不屬於四大宗，歸其他）
    assert c["by_platform"]["Windows"] == 1      # C-2 靠掃描推測
    assert "未知" not in c["by_platform"]         # C-3 退役已排除，不再貢獻「未知」
    # 'VM' 字串與 1 都要算成虛擬機
    assert c["by_virtualization"]["虛擬機"] == 2
    assert c["by_virtualization"].get("實體機", 0) == 0   # C-3(實體機) 退役已排除
    assert c["by_environment"] == {"正式": 1, "測試": 1}
    # 誠實揭露：幾台的 OS 是真收到的、幾台是猜的（只算有效資產）
    assert c["os_from_facts"] == 1 and c["os_guessed"] == 1
    # by_status 是唯一算全部（含退役）的欄位，才看得出退役有幾台
    assert c["by_status"] == {"使用中": 2, "停用": 1}


def test_組成統計_平台下鑽看得到版本明細():
    """點「Windows」要能展開看 2019/2022 各幾台，不是只有一個總數——
    使用者 2026-08-11 要求版本整理／平台下鑽。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "t.db"
        db.init_db(db_path)
        conn = db.get_connection(db_path)
        try:
            db.insert_hardware(conn, asset_serial="D-1", ip="10.0.2.1",
                               os="Windows Server 2022 Standard", asset_status="使用中")
            db.insert_hardware(conn, asset_serial="D-2", ip="10.0.2.2",
                               os="Windows Server 2019 Standard", asset_status="使用中")
            db.insert_hardware(conn, asset_serial="D-3", ip="10.0.2.3",
                               os="Rocky Linux 9.7", asset_status="使用中")
            # 沒有真 OS，只有掃描推測：平台下鑽也要能誠實顯示「這台猜是什麼」
            db.insert_hardware(conn, asset_serial="D-4", ip="10.0.2.4", os=None, asset_status="使用中")
            conn.execute(
                "INSERT INTO scan_history (scan_time, ip, os_guess, scan_ok) "
                "VALUES ('2026-08-11 09:00:00','10.0.2.4','Windows（RDP 3389）',1)")
            conn.commit()
            c = ms.composition(conn)
        finally:
            conn.close()

    assert c["by_platform"]["Windows"] == 3   # D-1/D-2 真OS + D-4 推測
    assert c["by_platform_os"]["Windows"]["Windows Server 2022"] == 1
    assert c["by_platform_os"]["Windows"]["Windows Server 2019"] == 1
    assert c["by_platform_os"]["Windows"]["Windows（RDP 3389）（推測）"] == 1
    assert c["by_platform_os"]["Linux(其他)"]["Rocky Linux 9.7"] == 1


def test_本機不需要SSH就能收集():
    """收集器就跑在某台機器上，要它「SSH 自己」才收得到資料是多此一舉——
    還得替自己建收集帳號、佈自己的公鑰，平白多一份維護與失敗點。
    使用者一句「你不是已經可以用 FACTS 了嗎」點出這件事。"""
    import os
    import pytest

    ips = ms.local_ips()
    assert "127.0.0.1" in ips, "本機位址至少要含 loopback"

    # 選路是核心邏輯，任何平台都要對：本機走本機、遠端走 SSH
    local_fn = ms._runner_for("127.0.0.1", "/no/such/key")
    remote_fn = ms._runner_for("203.0.113.9", "/no/such/key")
    assert local_fn is not remote_fn

    # 實際執行只在 POSIX 驗（收集指令本來就是 Linux 指令，Windows 上跑沒有意義）
    if os.name != "posix":
        pytest.skip("本機收集走 bash，正式環境是 Linux；開發機為 Windows 故略過執行驗證")
    assert local_fn("127.0.0.1", "echo x").strip() == "x"
