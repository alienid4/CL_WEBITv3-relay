"""納管失敗清單＋Windows 誤判（2026-09-17）。

使用者：「這些帳號納管失敗，我以後要怎麼查那些納管失敗過，他的 root 密碼可能不是預設密碼」
      「結果應該寫 納管失敗」
      「同時有 3389 和 22 port 的，應該要判斷為它是 Windows」

要守的：

1. 只回**每台最後一次**的結果——上週失敗今天成功的不該再出現在待處理裡
2. 失敗原因要分得出「下一步不同」的類別；認不出來歸「其他」並原樣附訊息
3. 清單要帶**部門與窗口**——這張表的用途就是「去找誰要密碼」
4. 已經試過納管而失敗的，狀態講「納管失敗」不是「待佈身分」
5. 3389／5985 只有 Windows 會開，證據力蓋過通用 OpenSSH banner；**445 不算**（Samba）
6. 通用 OpenSSH banner **不可以說「確定」**——AIX 的 banner 一模一樣
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import fingerprint  # noqa: E402
import onboard_failures as of  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="A-1", hostname="h1", ip="192.0.2.1",
                       asset_status="使用中", usage_unit="證券資訊部", user_name="王小明",
                       custodian="李小華", physical_location="內湖機房")
    c.commit()
    try:
        yield c
    finally:
        c.close()


def _audit(conn, ip, ok, stage="connect", message="", when="2026-09-17 10:00:00"):
    conn.execute(
        "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, triggered_by, "
        "ok, stage, message, created_at) VALUES (?,'linux','sysinfra','manual','tester',?,?,?,?)",
        (ip, 1 if ok else 0, stage, message, when))


def test_只看最後一次_後來成功的不列(conn):
    _audit(conn, "192.0.2.1", False, message="Permission denied", when="2026-09-16 10:00:00")
    _audit(conn, "192.0.2.1", True, stage="execute", message="已佈好", when="2026-09-17 10:00:00")
    conn.commit()
    assert of.current_failures(conn) == [], "上週失敗今天成功了，不該還在待處理裡"
    assert len(of.current_failures(conn, include_resolved=True)) == 1


def test_失敗清單帶部門與窗口(conn):
    _audit(conn, "192.0.2.1", False, message="Permission denied (publickey,password).")
    conn.commit()
    r = of.current_failures(conn)[0]
    assert r["reason"] == of.REASON_CREDENTIAL
    assert r["department"] == "證券資訊部" and r["contact"] == "王小明"
    assert "密碼不是預設值" in r["next_step"], "分類存在的理由就是講出下一步"
    assert r["hostname"] == "h1" and r["physical_location"] == "內湖機房"


def test_失敗次數與第一次失敗時間(conn):
    _audit(conn, "192.0.2.1", False, message="Permission denied", when="2026-09-15 08:00:00")
    _audit(conn, "192.0.2.1", False, message="Permission denied", when="2026-09-16 08:00:00")
    _audit(conn, "192.0.2.1", False, message="Permission denied", when="2026-09-17 08:00:00")
    conn.commit()
    r = of.current_failures(conn)[0]
    assert r["fail_count"] == 3 and r["first_failed_at"] == "2026-09-15 08:00:00"
    assert r["last_tried_at"] == "2026-09-17 08:00:00"


@pytest.mark.parametrize("stage,msg,expect", [
    ("connect", "Permission denied (publickey,password).", of.REASON_CREDENTIAL),
    ("connect", "登入被拒——帳號或密碼不對", of.REASON_CREDENTIAL),
    ("connect", "Connection timed out", of.REASON_UNREACHABLE),
    ("connect", "Connection refused", of.REASON_UNREACHABLE),
    ("connect", "儲存設備——不納管。依據：資產登記的作業系統", of.REASON_NOT_ONBOARDABLE),
    ("execute", "腳本第 12 行失敗", of.REASON_SCRIPT),
    ("connect", "某種沒看過的錯", of.REASON_OTHER),
])
def test_原因分類(stage, msg, expect):
    assert of.classify_reason(stage, msg) == expect


def test_摘要點出密碼問題幾台(conn):
    _audit(conn, "192.0.2.1", False, message="Permission denied")
    _audit(conn, "192.0.2.9", False, message="Connection timed out")
    conn.commit()
    s = of.summary(conn)
    assert s["total"] == 2 and s["credential"] == 1
    assert s["by_reason"][of.REASON_CREDENTIAL] == 1


def test_匯出欄位對得上(conn):
    _audit(conn, "192.0.2.1", False, message="Permission denied")
    conn.commit()
    rows = of.export_rows(conn)
    assert len(rows) == 1 and len(rows[0]) == len(of.EXPORT_HEADERS)


# ===== 平台判定 =====

def test_有3389就是Windows_即使banner是通用OpenSSH():
    r = fingerprint.onboard_method(open_ports=[22, 445, 3389, 5985],
                                   banner="SSH-2.0-OpenSSH_9.7")
    assert r["method"] == fingerprint.ONBOARD_WINDOWS
    assert "只有 Windows 會開" in r["evidence"]


def test_只有445不算Windows證據():
    # Linux 裝 Samba 一樣開 445——拿它當 Windows 證據會誤判檔案伺服器
    r = fingerprint.onboard_method(open_ports=[22, 445], banner="SSH-2.0-OpenSSH_9.7")
    assert r["method"] == fingerprint.ONBOARD_LINUX


def test_通用OpenSSH不可以說確定_因為AIX一模一樣():
    r = fingerprint.onboard_method(open_ports=[22], banner="SSH-2.0-OpenSSH_9.7")
    assert r["method"] == fingerprint.ONBOARD_LINUX
    assert r["confidence"] == "likely", "AIX 的 banner 跟 Linux 一樣，不可以說確定"
    assert "AIX" in r["evidence"]


def test_banner講出OS名字才算確定():
    r = fingerprint.onboard_method(open_ports=[22], banner="SSH-2.0-OpenSSH_8.9p1 Ubuntu-3")
    assert r["confidence"] == "confirmed"
    r2 = fingerprint.onboard_method(open_ports=[22], banner="SSH-2.0-OpenSSH_for_Windows_8.1")
    assert r2["method"] == fingerprint.ONBOARD_WINDOWS and r2["confidence"] == "confirmed"
