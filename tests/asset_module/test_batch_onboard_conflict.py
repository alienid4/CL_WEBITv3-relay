"""同一個 IP 在資產表登記多筆而且互相矛盾時，不自動納管（A13，2026-09-08）。

查 221 真實資料：同一個 IP 有多筆的共 410 組、933 筆。

⚠️ **多筆不等於重複登記**（2026-09-09 更正）：CIA 清冊的單位是「每個服務／VIP
一筆」，同一台機器跑三個服務就有三筆——那是正常資料。所以這裡守的**不是**
「有沒有重複」，而是「**這些列對同一台機器的描述有沒有互相打架**」：

    環境別互相矛盾 20 組（15 組 備援/正式、**5 組 正式/測試**）
    作業系統不一致 58 組

一台機器只會有一個環境別、一種作業系統。對不起來就是資料有問題。

而原本判環境別的查詢是 `WHERE ip = ? LIMIT 1`，**沒有 ORDER BY**——SQLite 回哪
一列不保證。挑到「測試」那一列，正式機就會被自動納管；挑到「正式」那一列，
同一台機器換個時間又變成不能跑。兩種都是錯的，而且是隨機的，最難查。

使用者 2026-09-08 拍板：「第一個答案就是人要確認」。
**資料自己在打架的時候，系統挑一個等於幫忙猜。**
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import batch_onboard_service as svc  # noqa: E402
import db  # noqa: E402

RHEL = "Red Hat Enterprise Linux 8 (64-bit)"
IP = "10.99.5.10"


def _conn(tmp):
    path = Path(tmp) / "t.db"
    db.init_db(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO scan_history (scan_time, ip, scan_ok, open_ports) "
                 "VALUES ('2026-09-08 10:00:00', ?, 1, '22')", (IP,))
    conn.commit()
    return conn


def _hw(conn, serial, **kw):
    db.insert_hardware(conn, asset_serial=serial, ip=IP, **kw)


def test_環境別矛盾就不自動跑():
    """正式／測試同時登記在同一個 IP——這種一定要人看過。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _hw(conn, "HW-A", environment="正式", os=RHEL)
            _hw(conn, "HW-B", environment="測試", os=RHEL)
            conn.commit()
            out = svc.classify_targets(conn, [IP])
            assert out["run"] == [], "資料矛盾卻自動納管了"
            assert [t["ip"] for t in out["unknown"]] == [IP]
            assert "環境別不一致" in out["unknown"][0]["reason"]
            assert "正式" in out["unknown"][0]["reason"]
            assert "測試" in out["unknown"][0]["reason"]
        finally:
            conn.close()


def test_作業系統矛盾也不自動跑():
    """一筆寫 Linux、一筆寫網路設備——無法確認這台是什麼。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _hw(conn, "HW-A", environment="測試", os=RHEL)
            _hw(conn, "HW-B", environment="測試", os="網路設備")
            conn.commit()
            out = svc.classify_targets(conn, [IP])
            assert out["run"] == []
            assert "作業系統不一致" in out["unknown"][0]["reason"]
        finally:
            conn.close()


def test_填法不同但同一種OS不算矛盾():
    """`RedHat 8.5` 與 `Red Hat Enterprise Linux 8 (64-bit)` 是同一種東西。

    比對走**分類後**的結果，不是比字串——不然光是填法不同就會把能跑的擋掉，
    那會讓這道保護被當成雜訊，最後被人關掉。
    """
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _hw(conn, "HW-A", environment="測試", os=RHEL)
            _hw(conn, "HW-B", environment="測試", os="RedHat 8.5")
            conn.commit()
            out = svc.classify_targets(conn, [IP])
            assert [t["ip"] for t in out["run"]] == [IP], out
        finally:
            conn.close()


def test_多筆但完全一致照樣可以跑():
    """同一台機器跑多個服務就會有多筆——那是正常資料，沒有矛盾，不該被擋。

    這條測試比看起來重要：擋錯了，正常的機器會全部跑進「要你確認」，
    這道保護就會被當成雜訊然後被關掉。
    """
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _hw(conn, "HW-A", environment="測試", os=RHEL)
            _hw(conn, "HW-B", environment="測試", os=RHEL)
            _hw(conn, "HW-C", environment="測試", os=RHEL)
            conn.commit()
            out = svc.classify_targets(conn, [IP])
            assert [t["ip"] for t in out["run"]] == [IP], out
        finally:
            conn.close()


def test_矛盾的機器不會被誤放進設備桶():
    """矛盾要進「要你確認」，不是進「不納管（設備）」。

    兩者要做的事完全不同：設備是**永遠不要碰**，矛盾是**看一眼就能決定**。
    混在一起會讓人以為那台是交換器，再也不會回頭看。
    """
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            _hw(conn, "HW-A", environment="正式", os=RHEL)
            _hw(conn, "HW-B", environment="測試", os=RHEL)
            conn.commit()
            out = svc.classify_targets(conn, [IP])
            assert out["excluded"] == []
            assert len(out["unknown"]) == 1
        finally:
            conn.close()
