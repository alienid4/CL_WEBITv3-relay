"""每一種匯入都要配一個匯出（2026-09-09 使用者要求）。

守的重點不是「能不能匯出」，是**匯出的東西有沒有誠實**：

1. 沒有原始檔就要回「沒有」，**不可以拿重新產生的檔冒充**——那會讓人拿它去跟
   來源系統對帳，對不起來卻找不到原因
2. 只留最新一份（使用者選的），舊的要真的被刪掉
3. dump 讀壞的時候要講清楚是**哪一種壞**：拿錯檔？檔損毀？版本不合？
   只說「讀取失敗」對使用者沒有用
"""
import gzip
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import import_export as ie  # noqa: E402


@pytest.fixture()
def conn(monkeypatch, tmp_path):
    path = tmp_path / "asset.db"
    db.init_db(path)
    monkeypatch.setattr(db, "get_db_path", lambda: path)
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _record(c, source, key, payload):
    c.execute("INSERT INTO source_record (source, source_key, payload) VALUES (?,?,?)",
              (source, key, json.dumps(payload, ensure_ascii=False)))
    c.commit()


# ---------- 原始檔 ----------

def test_沒有原始檔要回沒有_不可以生一個假的(conn):
    assert ie.original_for("dynassets") is None, \
        "沒存過原始檔卻回了東西——拿重產的檔冒充原始檔會害人對帳對不出來"


def test_只留最新一份_舊的要真的被刪掉(conn):
    ie.save_original("dynassets", "第一次.csv", b"old")
    ie.save_original("dynassets", "第二次.csv", b"new")
    d = ie.archive_root() / "dynassets"
    files = sorted(p.name for p in d.iterdir())
    assert files == ["第二次.csv"], f"舊檔沒被刪：{files}"
    assert ie.original_for("dynassets").read_bytes() == b"new"


def test_原始檔內容一個位元都不能變(conn):
    """原始檔的意義就是「當初送進來的那個檔」。動過就不是原始檔了。"""
    raw = "ip,主機名\n10.0.0.1,測試機\n".encode("utf-8")
    ie.save_original("rvtools", "x.csv", raw)
    assert ie.original_for("rvtools").read_bytes() == raw


def test_檔名有路徑也不會寫到別的目錄去(conn):
    """上傳的檔名是外面來的，不能直接拿去接路徑。"""
    ie.save_original("dynassets", "../../evil.csv", b"x")
    p = ie.original_for("dynassets")
    assert p.parent == ie.archive_root() / "dynassets", p


def test_不認得的來源要擋掉(conn):
    with pytest.raises(ValueError):
        ie.save_original("不存在的來源", "a.csv", b"x")


# ---------- Excel／表格 ----------

def test_dynassets匯出用當初匯入的欄位(conn):
    _record(conn, "dynassets", "k1", {"ip": "10.0.0.1", "hostname": "A"})
    _record(conn, "dynassets", "k2", {"ip": "10.0.0.2", "hostname": "B", "mac": "aa:bb"})
    headers, rows = ie.export_rows(conn, "dynassets")
    assert headers == ["hostname", "ip", "mac"], \
        "欄位要取所有列的聯集——只看第一列會漏掉後面才出現的欄位"
    assert len(rows) == 2
    assert rows[0] == ["A", "10.0.0.1", ""], "缺的欄位要留空，不是漏掉整格"


def test_CIA清冊沒有原始列_匯的是系統欄位(conn):
    """excel_import 不寫 source_record，所以只能從 hardware 產。

    這不是缺陷，是事實——但它代表 CIA 的匯出**不等於**原始 Excel，
    模組說明裡有寫清楚，這條測試把那個事實固定下來。
    """
    db.insert_hardware(conn, asset_serial="HW-1", ip="10.0.0.9", hostname="X")
    conn.commit()
    headers, rows = ie.export_rows(conn, "cia_excel")
    assert "asset_serial" in headers and "ip" in headers
    assert len(rows) == 1


def test_沒資料也要回欄位不是回空(conn):
    headers, rows = ie.export_rows(conn, "dynassets")
    assert rows == []
    assert isinstance(headers, list)


# ---------- dump ----------

def test_dump可以原樣讀回來(conn):
    _record(conn, "dynassets", "k1", {"ip": "10.0.0.1", "hostname": "A"})
    body = ie.read_dump(ie.build_dump(conn, "dynassets"))
    assert body["source"] == "dynassets"
    assert body["row_count"] == 1
    assert body["headers"] == ["hostname", "ip"]
    assert body["rows"] == [["A", "10.0.0.1"]]


def test_拿錯檔跟檔壞掉要講不同的話(conn):
    """「讀取失敗」對使用者沒有用——他要知道是拿錯檔還是檔損毀。"""
    with pytest.raises(ValueError, match="不是 dump 檔"):
        ie.read_dump("這根本不是 gzip".encode("utf-8"))

    with pytest.raises(ValueError, match="壞了"):
        ie.read_dump(gzip.compress("{ 不是合法 json".encode("utf-8")))

    with pytest.raises(ValueError, match="不是本系統"):
        ie.read_dump(gzip.compress(json.dumps({"magic": "別人的"}).encode()))


def test_版本不合要明講版本(conn):
    bad = gzip.compress(json.dumps(
        {"magic": ie.DUMP_MAGIC, "version": 999, "headers": [], "rows": []}).encode())
    with pytest.raises(ValueError, match="999"):
        ie.read_dump(bad)


def test_dump是壓縮過的(conn):
    """重複性高的資料要壓得下來，不然 email 帶不走——這是這個格式的用途之一。"""
    for i in range(300):
        _record(conn, "dynassets", f"k{i}",
                {"ip": f"10.0.0.{i % 255}", "hostname": f"HOST-{i}", "note": "一樣的說明文字" * 10})
    raw = ie.build_dump(conn, "dynassets")
    plain = json.dumps(ie.read_dump(raw), ensure_ascii=False).encode("utf-8")
    assert len(raw) < len(plain) / 3, f"壓縮率太差：{len(raw)} vs {len(plain)}"
