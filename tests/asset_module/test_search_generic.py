"""全文搜尋：多關鍵字、同義詞、通用掃表。

2026-08-27 使用者的批評：「**全文代表 DB 我都可以查，不是我說一個案例多一個功能，
沒說就查不到**」。原本 `/api/search` 是寫死九個欄位，等於「我事先想到的清單」。

這裡守的四件事：
1. 空白拆字後 **AND**（`板橋 oob` 以前是 0 筆，因為整串當一個關鍵字）
2. 同義詞要**展開**，而且要**講出來展開了哪些詞**（不可以安靜地擴大範圍）
3. **敏感表絕對不掃**（sessions 裡是登入 token）
4. 「查不到」要能分辨是**哪個字**沒命中——「查不到」跟「沒有」是兩件事
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import search_terms  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    conn = db.get_connection(p)
    db.insert_hardware(conn, asset_serial="HW-1", hostname="SECOOB100-061",
                       ip="10.99.194.61", physical_location="01_板橋機房",
                       environment="正式", asset_purpose="DELL R740 iDRAC",
                       asset_status="在用")
    db.insert_hardware(conn, asset_serial="HW-2", hostname="ESXI018-01",
                       ip="10.99.18.1", physical_location="00_敦南總公司",
                       environment="測試", asset_purpose="VMware ESXi",
                       asset_status="在用")
    db.insert_hardware(conn, asset_serial="HW-3", hostname="SECSVR198-100T",
                       ip="10.99.198.100", physical_location="01_板橋機房",
                       environment="正式", asset_purpose="FTP Server",
                       asset_status="在用")
    conn.commit()
    return conn


# ===== 關鍵字解析 =====

def test_空白拆字後是AND不是整串比對():
    """`板橋 oob` 以前是 0 筆——因為整串當一個關鍵字，
    而沒有任何欄位同時包含「板橋 oob」這七個字元（含中間空白）。"""
    parsed = search_terms.parse_query("板橋 oob ftp")
    assert [p["term"] for p in parsed] == ["板橋", "oob", "ftp"]


def test_同義詞要展開且原字排第一():
    """原字排第一，畫面顯示時人才看得出「哪個是我打的、哪些是系統加的」。"""
    p = search_terms.parse_query("測試")[0]
    assert p["match"][0] == "測試"
    assert "test" in p["match"] and "uat" in p["match"]


def test_沒有同義詞的字就只比對自己_不要猜():
    p = search_terms.parse_query("zzzz")[0]
    assert p["match"] == ["zzzz"]


def test_同義詞組裡任一個字都能觸發整組():
    """使用者可能打「測試區」也可能打「uat」，兩個都要展開成同一組。"""
    for word in ("測試區", "uat", "test"):
        m = search_terms.parse_query(word)[0]["match"]
        assert "測試" in m, f"打「{word}」應該要展開到整組"


# ===== 安全：絕對不掃的表 =====

def test_敏感表絕對不在掃描範圍內():
    """⚠️ `sessions` 裡是**登入 token**、`users` 是密碼雜湊、
    `app_settings` 有真實對照表。掃到它們不是「設定不當」，是外洩。

    這條寫死在程式碼裡不放設定檔——設定檔會被人改。
    """
    for t in ("sessions", "users", "app_settings", "host_api_key"):
        assert t in search_terms.EXCLUDE_TABLES, f"{t} 必須排除"

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            names = {t for t, _ in search_terms.searchable_tables(conn)}
            assert not (names & search_terms.EXCLUDE_TABLES)
            assert "hardware" in names, "前提：正常的表要在裡面"
        finally:
            conn.close()


def test_長文與雜湊欄位不掃():
    """單據全文有專屬入口，混進通用搜尋會洗版；雜湊欄位命中了對人也沒意義。"""
    assert "full_text" in search_terms.EXCLUDE_COLUMNS
    assert "key_hash" in search_terms.EXCLUDE_COLUMNS


# ===== 通用掃表 =====

def test_多關鍵字要同時命中才算():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            tables = search_terms.searchable_tables(conn)
            r = search_terms.scan(conn, search_terms.parse_query("板橋 ftp"), tables)
            assert r["total"] == 1, "只有 HW-3 同時是板橋又是 FTP"

            r2 = search_terms.scan(conn, search_terms.parse_query("板橋"), tables)
            assert r2["total"] == 2, "板橋有兩台"
        finally:
            conn.close()


def test_同義詞真的擴大到命中():
    """HW-2 的環境別是「測試」，用 uat 也要找得到。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            tables = search_terms.searchable_tables(conn)
            assert search_terms.scan(conn, search_terms.parse_query("uat"), tables)["total"] >= 1
            # oob 的同義詞含 idrac，HW-1 的用途寫 iDRAC
            assert search_terms.scan(conn, search_terms.parse_query("oob"), tables)["total"] >= 1
        finally:
            conn.close()


def test_查不到時要看得出是哪個字沒命中():
    """這是「查不到 ≠ 沒有」的具體實作。搜「不存在的東西 板橋」得 0 筆時，
    人要能看出是前者 0 筆、後者 2 筆，才知道該改哪個字重搜。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            tables = search_terms.searchable_tables(conn)
            r = search_terms.scan(conn, search_terms.parse_query("不存在的東西 板橋"), tables)
            assert r["total"] == 0
            assert r["term_hits"]["不存在的東西"] == 0
            assert r["term_hits"]["板橋"] == 2, "另一個字是有命中的，要分得出來"
        finally:
            conn.close()


def test_要講出命中在哪一個欄位():
    """搜 esxi 命中「主機名」跟命中「資產用途」是兩種不同的東西
    （一個是機器本身叫這個，一個是它的用途描述提到）。
    混在一起人要逐筆點開才知道為什麼中。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            tables = search_terms.searchable_tables(conn)
            r = search_terms.scan(conn, search_terms.parse_query("ftp"), tables)
            g = next(x for x in r["groups"] if x["table"] == "hardware")
            fields = {m["field"] for s in g["samples"] for m in s["matched"]}
            assert "asset_purpose" in fields
        finally:
            conn.close()


def test_串接欄位不可以讓跨欄位假命中():
    """⚠️ 欄位值直接相黏會製造假命中：A 欄結尾「板」＋B 欄開頭「橋」
    黏起來就變成「板橋」。所以中間一定要有分隔符。

    這條測試會在有人把 SEP 改成空字串時紅——那個改動不會報錯，
    只會安靜地開始多出對不起來的筆數。
    """
    assert search_terms.SEP, "SEP 不可以是空字串"
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        try:
            conn.execute(
                "UPDATE hardware SET asset_purpose = ?, custodian = ? WHERE asset_serial = ?",
                ("結尾是板", "橋開頭", "HW-2"))
            conn.commit()
            tables = search_terms.searchable_tables(conn)
            r = search_terms.scan(conn, search_terms.parse_query("板橋"), tables)
            hw = next((g for g in r["groups"] if g["table"] == "hardware"), None)
            assert hw is not None
            assert hw["count"] == 2, "HW-2 的「板」「橋」分在兩欄，不該被算成命中板橋"
        finally:
            conn.close()


def test_空查詢不會掃全庫():
    assert search_terms.parse_query("") == []
    assert search_terms.parse_query("   ") == []
