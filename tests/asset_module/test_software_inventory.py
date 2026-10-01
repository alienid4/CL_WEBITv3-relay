"""軟體盤點：rpm -qa／Windows 程式清單 → host_package，新裝／移除記在 package_change。

守的線：
1. **第一次盤點不算異動**——那是「本來就在」，不是「剛裝」（使用者：從現在開始記錄）。
2. **收集失敗不能被當成「全部移除」**——rpm 機器不可能零套件。
3. **沒有 rpm ≠ 沒裝軟體**——Debian 類標不支援。
4. **不可以用 Win32_Product**——查它會觸發 MSI 一致性檢查，是會改動目標機的副作用。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import software_collector as sc  # noqa: E402
import software_inventory as si  # noqa: E402

IP = "192.0.2.10"


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    conn.execute("INSERT INTO hardware (asset_serial, ip, collect_ok) VALUES ('HW-T1', ?, 1)", (IP,))
    conn.commit()
    return conn


def _rpm(*pkgs):
    lines = ["SRC=rpm"]
    for name, ver in pkgs:
        lines.append(f"PKG\t{name}\t{ver}\tx86_64\t1700000000\tRocky Enterprise Software Foundation")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def test_解析rpm輸出():
    src, pkgs = sc.parse_packages(_rpm(("openssh-server", "8.7p1-38.el9"), ("openssl", "3.0.7-27.el9")))
    assert src == "rpm"
    by = {p["name"]: p for p in pkgs}
    assert by["openssh-server"]["version"] == "8.7p1-38.el9"
    assert by["openssl"]["arch"] == "x86_64"
    assert by["openssl"]["installed_at"] and by["openssl"]["installed_at"].startswith("2023-")


def test_解析Windows程式清單():
    out = ("SRC=registry\r\n"
           "PKG\t7-Zip 23.01 (x64)\t23.01\tx64\t20240115\tIgor Pavlov\r\n"
           "PKG\tMicrosoft 365 Apps for enterprise - zh-tw\t16.0.17328.20162\tx64\t\tMicrosoft Corporation\r\n"
           "PKG\t7-Zip 23.01 (x64)\t23.01\tx64\t20240115\tIgor Pavlov\r\n")   # 64/32 機碼重複登記
    src, pkgs = sc.parse_packages(out)
    assert src == "registry"
    assert len(pkgs) == 2, "同名同版同架構只能留一筆"
    by = {p["name"]: p for p in pkgs}
    assert by["7-Zip 23.01 (x64)"]["installed_at"] == "2024-01-15"
    assert by["Microsoft 365 Apps for enterprise - zh-tw"]["installed_at"] is None, \
        "不知道安裝日期就留空，不能猜"


def test_沒有SRC行代表指令沒跑起來():
    src, pkgs = sc.parse_packages("bash: rpm: command not found\n")
    assert src is None and pkgs == []


def test_Linux指令排除gpg_pubkey且不需要sudo():
    assert "gpg-pubkey" in sc.LINUX_CMD
    assert "sudo" not in sc.LINUX_CMD, "rpm -qa 一般帳號就讀得到，不該要權限"


def test_Windows不可以用Win32_Product():
    """Win32_Product 查詢會讓 MSI 對每個程式做一致性檢查、可能自動修復——那不是唯讀。"""
    assert "Win32_Product" not in sc.WINDOWS_PS
    assert "WOW6432Node" in sc.WINDOWS_PS, "只讀 64 位元機碼會漏掉 32 位元程式"


# ---------------------------------------------------------------------------
# 寫入：第一次不算異動、之後才記新裝／移除
# ---------------------------------------------------------------------------

def test_第一次盤點不寫異動():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            r = si.collect_software(conn, runner=lambda h, c: _rpm(("bash", "5.1"), ("openssl", "3.0.7")))
            assert r["hosts"][0]["baseline"] is True
            assert conn.execute("SELECT COUNT(*) FROM host_package").fetchone()[0] == 2
            assert conn.execute("SELECT COUNT(*) FROM package_change").fetchone()[0] == 0
        finally:
            conn.close()


def test_第二輪記錄新裝移除與升級():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            si.collect_software(conn, runner=lambda h, c: _rpm(("bash", "5.1"), ("openssl", "3.0.7")))
            r = si.collect_software(conn, runner=lambda h, c: _rpm(("bash", "5.1"), ("openssl", "3.0.8"),
                                                                    ("p7zip", "16.02")))
            h = r["hosts"][0]
            assert h["baseline"] is False
            assert h["added"] == 2 and h["removed"] == 1     # openssl 升級＝舊版移除＋新版新增
            ch = {(c["name"], c["version"], c["change"]) for c in si.list_changes(conn, 30)}
            assert ch == {("openssl", "3.0.7", "removed"), ("openssl", "3.0.8", "added"),
                          ("p7zip", "16.02", "added")}
            live = {p["name"]: p["version"] for p in si.host_packages(conn, IP)}
            assert live == {"bash": "5.1", "openssl": "3.0.8", "p7zip": "16.02"}
        finally:
            conn.close()


def test_收到零個套件不可以把整台標成移除():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            si.collect_software(conn, runner=lambda h, c: _rpm(("bash", "5.1")))
            r = si.collect_software(conn, runner=lambda h, c: "SRC=rpm\n")
            assert r["failed"], "零套件要當失敗"
            assert conn.execute(
                "SELECT COUNT(*) FROM host_package WHERE gone_at IS NOT NULL").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM package_change").fetchone()[0] == 0
        finally:
            conn.close()


def test_沒有rpm標不支援不是失敗():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            r = si.collect_software(conn, runner=lambda h, c: "SRC=none\n")
            assert r["unsupported"] and not r["failed"]
            run = conn.execute("SELECT * FROM software_collect_runs").fetchone()
            assert run["unsupported_count"] == 1
        finally:
            conn.close()


def test_彙總與下鑽對得起來():
    """表格數字要能點進去：總覽說 2 個版本，下鑽就要剛好看到那 2 個版本、那幾台。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            conn.execute("INSERT INTO hardware (asset_serial, ip, collect_ok) VALUES ('HW-T2', '192.0.2.11', 1)")
            conn.commit()
            outs = {IP: _rpm(("openssl", "3.0.7")), "192.0.2.11": _rpm(("openssl", "3.0.8"))}
            si.collect_software(conn, runner=lambda h, c: outs[h])
            row = {r["name"]: r for r in si.list_software(conn)}["openssl"]
            assert row["version_count"] == 2 and row["host_count"] == 2
            vers = {v["version"]: v["host_count"] for v in si.software_versions(conn, "openssl")}
            assert vers == {"3.0.7": 1, "3.0.8": 1}
            assert [h["ip"] for h in si.software_hosts(conn, "openssl", "3.0.8")] == ["192.0.2.11"]
        finally:
            conn.close()


def test_沒有兩個端點搶同一個路徑():
    """FastAPI 同 method＋路徑先註冊先贏，後面那個被靜默蓋掉、不報錯。
    v1.92.0 的軟體清單用了 /api/software，撞到 CIA 清冊早就在用的同名端點，
    前端拿到的是別人的資料格式，2-10 整頁白掉。這條擋住下一次。"""
    import api
    from collections import Counter

    seen = Counter()
    for r in api.app.routes:
        for m in getattr(r, "methods", None) or ():
            seen[(m, getattr(r, "path", ""))] += 1
    dup = [f"{m} {p}" for (m, p), n in seen.items() if n > 1 and m != "HEAD"]
    assert not dup, "以下端點重複註冊（後面的永遠不會被呼叫到）：\n" + "\n".join(dup)


def test_清空盤點資料會清到軟體盤點():
    import api

    for t in ("host_package", "package_change", "software_collect_runs"):
        assert t in api._RESET_TABLES
