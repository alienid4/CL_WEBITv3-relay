"""弱點報告解析與逐台對照（2026-09-21 使用者：
「以後每一台就可以看到有修過什麼弱點了吧? 多一個弱點管理紀錄」）。
"""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import vuln_import  # noqa: E402

HEAD = ["Plugin ID", "Severity", "Host", "Protocol", "Port", "Name",
        "修補期限", "負責單位", "負責人", "結案狀態", "結案日期"]


def _xlsx(rows, title="1-系統弱點掃描弱點"):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    db.insert_hardware(c, asset_serial="HW-1", hostname="SRV-A", ip="10.99.1.1",
                       os="RHEL 9", asset_status="使用中")
    c.commit()
    return c


def test_未結案不可以被判成已結案(tmp_path):
    """「未結案」這三個字裡面有「結案」。只做子串比對會把所有未結案都判成已結案——
    畫面會變成「弱點都修完了」，那是最危險的誤讀。2026-09-21 實測時抓到。"""
    assert vuln_import._is_closed("未結案") == 0
    assert vuln_import._is_closed("已結案") == 1
    assert vuln_import._is_closed("處理中") == 0
    assert vuln_import._is_closed("") == 0, "沒填不算結案，不替人填答案"


def test_同弱點不同埠是兩筆(tmp_path):
    """比對鍵是 Plugin ID + Host + Protocol + Port。只用「主機＋弱點名稱」的話，
    443 與 8443 會被併成一筆，「修好了幾個」直接算錯。"""
    c = _conn(tmp_path)
    vuln_import.import_workbook(c, _xlsx([
        [12345, "High", "10.99.1.1", "tcp", 443, "OpenSSL 過舊", "2026-10-01", "資訊部", "張三", "未結案", None],
        [12345, "High", "10.99.1.1", "tcp", 8443, "OpenSSL 過舊", "2026-10-01", "資訊部", "張三", "未結案", None],
    ]), "w.xlsx", "tester")
    r = vuln_import.by_asset(c, "HW-1")
    assert len(r["open"]) == 2, "同弱點不同埠是兩筆，不可以合併"


def test_主機名與IP都要對得回資產(tmp_path):
    c = _conn(tmp_path)
    vuln_import.import_workbook(c, _xlsx([
        [1, "High", "10.99.1.1", "tcp", 443, "用 IP 寫的", "2026-10-01", "X", "Y", "未結案", None],
        [2, "Low", "SRV-A", "tcp", 22, "用主機名寫的", "2026-10-01", "X", "Y", "已結案", "2026-09-10"],
        [3, "Low", "10.99.9.9", "tcp", 80, "清冊查無此主機", "2026-10-01", "X", "Y", "未結案", None],
    ]), "w.xlsx", "tester")
    r = vuln_import.by_asset(c, "HW-1")
    assert len(r["open"]) == 1 and len(r["closed"]) == 1
    un = vuln_import.unmatched_hosts(c)
    assert [u["host"] for u in un] == ["10.99.9.9"], "對不到的要列得出來，不可以默默丟掉"
    assert un[0]["open_n"] == 1


def test_沒有報告_與_不在報告裡_要分得出來(tmp_path):
    """一台從沒被掃過的機器如果顯示成「沒有弱點」，人會以為它很乾淨。"""
    c = _conn(tmp_path)
    r = vuln_import.by_asset(c, "HW-1")
    assert r["has_report"] is False and r["in_report"] is False

    db.insert_hardware(c, asset_serial="HW-2", hostname="SRV-B", ip="10.99.1.2",
                       os="RHEL 9", asset_status="使用中")
    c.commit()
    vuln_import.import_workbook(c, _xlsx([
        [1, "High", "10.99.1.1", "tcp", 443, "只有 A 有", "2026-10-01", "X", "Y", "未結案", None],
    ]), "w.xlsx", "tester")
    b = vuln_import.by_asset(c, "HW-2")
    assert b["has_report"] is True and b["in_report"] is False, "有報告但不在裡面，是第三種狀態"


def test_差異要分三類_不可以把沒掃到當成修好了(tmp_path):
    """一筆弱點從清單消失有兩種可能：修好了，或這次根本沒掃那台。
    混在一起報「已修復」是假的——那會讓人以為問題解決了。"""
    c = _conn(tmp_path)
    vuln_import.import_workbook(c, _xlsx([
        [1, "High", "10.99.1.1", "tcp", 443, "A 的弱點一", "2026-10-01", "X", "Y", "未結案", None],
        [2, "High", "10.99.1.1", "tcp", 8443, "A 的弱點二", "2026-10-01", "X", "Y", "未結案", None],
        [3, "Low", "SRV-B", "tcp", 22, "B 的弱點", "2026-10-01", "X", "Y", "未結案", None],
    ]), "舊.xlsx", "tester")
    vuln_import.import_workbook(c, _xlsx([
        [1, "High", "10.99.1.1", "tcp", 443, "A 的弱點一", "2026-10-01", "X", "Y", "未結案", None],
        [9, "High", "10.99.1.1", "tcp", 80, "A 的新弱點", "2026-12-01", "X", "Y", "未結案", None],
    ]), "新.xlsx", "tester")
    d = vuln_import.diff(c, 2, 1)["counts"]
    assert d["added"] == 1, "port 80 是新的"
    assert d["fixed"] == 1, "8443 不見了，而 A 這次有出現＝有掃到它，才算修好"
    assert d["unknown"] == 1, "SRV-B 整台沒出現，不可以算成修好"


def test_匯入失敗不可以留下半批(tmp_path):
    """匯入到一半掛掉，那半批被當成「本期」拿去比，差異整個是錯的。"""
    c = _conn(tmp_path)
    try:
        vuln_import.import_workbook(c, b"this is not an xlsx", "壞檔.xlsx", "tester")
    except Exception:
        pass
    assert vuln_import.latest_batch(c) is None, "失敗的批次不可以是 ready"
    bs = vuln_import.batches(c)
    assert bs and bs[0]["status"] == "failed" and bs[0]["note"], "要留下失敗痕跡與原因"
