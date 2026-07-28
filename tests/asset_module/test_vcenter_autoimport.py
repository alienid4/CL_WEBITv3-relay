"""S19 VC 自動匯入（方案 B）：監看資料夾、抓最新、不重抓、半寫入保護、鮮度燈。

這支負責「今晚的 RVTools 匯出到底進來了沒」。要守的：只抓最新且寫完的檔、同一份不重匯、
還在寫的檔先跳過、太久沒新檔要示警。用注入的 importer，測邏輯不必真的解析 Excel。
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import vcenter_autoimport as vc  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _touch(directory, name, minutes_ago=10):
    """在資料夾放一個 .xlsx，並把 mtime 設成 N 分鐘前（預設夠久＝寫完了）。"""
    p = Path(directory) / name
    p.write_bytes(b"fake xlsx bytes")
    when = time.time() - minutes_ago * 60
    os.utime(p, (when, when))
    return p


def _counting_importer():
    """假 importer：記錄被匯了哪些檔，回固定摘要。"""
    seen = []

    def imp(conn, path):
        seen.append(path.name)
        return {"total_vms": 3, "inserted": 2, "updated": 1, "pending_review": 0, "errors": []}

    imp.seen = seen
    return imp


# ===== 抓最新 =====

def test_抓資料夾裡最新的檔():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        folder = Path(tmp) / "export"; folder.mkdir()
        try:
            _touch(folder, "old.xlsx", minutes_ago=120)
            _touch(folder, "new.xlsx", minutes_ago=10)
            imp = _counting_importer()
            r = vc.pickup(conn, directory=str(folder), importer=imp)
            assert r["status"] == "imported"
            assert imp.seen == ["new.xlsx"]      # 只抓最新那份
        finally:
            conn.close()


def test_同一份不重複匯():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        folder = Path(tmp) / "export"; folder.mkdir()
        try:
            _touch(folder, "export1.xlsx", minutes_ago=10)
            imp = _counting_importer()
            assert vc.pickup(conn, directory=str(folder), importer=imp)["status"] == "imported"
            # 第二次抓：沒有新檔
            r2 = vc.pickup(conn, directory=str(folder), importer=imp)
            assert r2["status"] == "already_current"
            assert imp.seen == ["export1.xlsx"]   # 沒有再匯一次
        finally:
            conn.close()


def test_出現更新的檔會再抓():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        folder = Path(tmp) / "export"; folder.mkdir()
        try:
            _touch(folder, "day1.xlsx", minutes_ago=10)
            imp = _counting_importer()
            vc.pickup(conn, directory=str(folder), importer=imp)
            # 隔天新匯出一份（更新）
            _touch(folder, "day2.xlsx", minutes_ago=5)
            r = vc.pickup(conn, directory=str(folder), importer=imp)
            assert r["status"] == "imported"
            assert imp.seen == ["day1.xlsx", "day2.xlsx"]
        finally:
            conn.close()


# ===== 半寫入保護 =====

def test_還在寫的檔先跳過():
    """mtime 太新（可能還在寫）不抓，避免讀到寫一半的檔。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        folder = Path(tmp) / "export"; folder.mkdir()
        try:
            _touch(folder, "writing.xlsx", minutes_ago=0)   # 剛剛才動過
            imp = _counting_importer()
            r = vc.pickup(conn, directory=str(folder), importer=imp)
            assert r["status"] == "no_file"     # 靜置不夠久，這輪不抓
            assert imp.seen == []
        finally:
            conn.close()


# ===== 沒資料夾／沒檔 =====

def test_沒設資料夾():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            assert vc.pickup(conn, directory="")["status"] == "no_dir"
        finally:
            conn.close()


def test_資料夾空的():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        folder = Path(tmp) / "empty"; folder.mkdir()
        try:
            assert vc.pickup(conn, directory=str(folder))["status"] == "no_file"
        finally:
            conn.close()


# ===== 鮮度燈 =====

def test_鮮度燈_未啟用回off():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            assert vc.health(conn)["status"] == "off"
        finally:
            conn.close()


def test_鮮度燈_近期有檔綠燈():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        folder = Path(tmp) / "export"; folder.mkdir()
        try:
            _touch(folder, "fresh.xlsx", minutes_ago=30)
            vc.set_config(conn, True, str(folder), 36)
            h = vc.health(conn)
            assert h["status"] == "green"
            assert h["newest_file"] == "fresh.xlsx"
        finally:
            conn.close()


def test_鮮度燈_太久沒檔黃燈():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        folder = Path(tmp) / "export"; folder.mkdir()
        try:
            _touch(folder, "stale.xlsx", minutes_ago=60 * 48)   # 48 小時前
            vc.set_config(conn, True, str(folder), 36)          # 門檻 36 小時
            h = vc.health(conn)
            assert h["status"] == "yellow"
            assert "超過" in h["reason"]
        finally:
            conn.close()


def test_鮮度燈_資料夾不存在紅燈():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            vc.set_config(conn, True, str(Path(tmp) / "nonexist"), 36)
            assert vc.health(conn)["status"] == "red"
        finally:
            conn.close()


# ===== tick：停用不動作 =====

def test_tick_停用時不抓():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        folder = Path(tmp) / "export"; folder.mkdir()
        try:
            _touch(folder, "x.xlsx", minutes_ago=10)
            vc.set_config(conn, False, str(folder), 36)   # 關閉
            assert vc.tick(conn)["status"] == "disabled"
        finally:
            conn.close()


def test_匯入失敗不炸_回error並記錄():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        folder = Path(tmp) / "export"; folder.mkdir()
        try:
            _touch(folder, "bad.xlsx", minutes_ago=10)

            def boom(conn, path):
                raise ValueError("不是 RVTools 格式")

            r = vc.pickup(conn, directory=str(folder), importer=boom)
            assert r["status"] == "error" and "RVTools" in r["error"]
            # 失敗的檔沒被記成「已處理」，修好後同一份還會再試
            assert vc.get_config(conn)["last_file"] == ""
        finally:
            conn.close()
