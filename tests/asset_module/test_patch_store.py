"""弱點彙總（CL_Patch）搬上系統版的共享儲存（2026-09-21）。

使用者：「CL_Patch 是小工具，我們目前已經在用了，我只是要轉移到系統上，
變系統版不是單機版」——所以這裡測的是「搬家之後該共享的有沒有真的共享」，
不是重新驗那支工具的分析邏輯（那部分一行都沒動）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import patch_store  # noqa: E402
import pytest  # noqa: E402


def _conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def test_共享項目要留下是誰在什麼時候更新的(tmp_path):
    """系統版的重點不只是「大家看同一份」，還要看得出「這份是誰換掉的」——
    不然使用者不知道自己看的是不是同事剛剛覆蓋掉的那一份。"""
    c = _conn(tmp_path)
    assert patch_store.get(c, "workbook") is None, "還沒人匯入過不是錯誤，回 None"

    patch_store.put(c, "workbook", b"PK\x03\x04fake",
                    content_type="application/octet-stream",
                    file_name="弱點彙總報告.xlsx", by="alice")
    row = patch_store.get(c, "workbook")
    assert bytes(row["body"]) == b"PK\x03\x04fake"
    assert row["file_name"] == "弱點彙總報告.xlsx" and row["updated_by"] == "alice"

    # 後匯入的覆蓋前一份，而且換人要看得出來
    patch_store.put(c, "workbook", b"PK\x03\x04newer", file_name="新版.xlsx", by="bob")
    row = patch_store.get(c, "workbook")
    assert bytes(row["body"]) == b"PK\x03\x04newer" and row["updated_by"] == "bob"


def test_只收白名單內的項目(tmp_path):
    """不開放任意 key：否則這張表會變成前端想塞什麼就塞什麼的垃圾桶。"""
    c = _conn(tmp_path)
    with pytest.raises(ValueError, match="不認得"):
        patch_store.put(c, "whatever", b"x")
    with pytest.raises(ValueError):
        patch_store.get(c, "../etc/passwd")


def test_太大要明講不可以靜默失敗(tmp_path):
    """那支工具原本吃過『配額耗盡靜默失敗、歷史悄悄停在舊的一期』的虧
    （見 history.js 註解），搬上來不可以重蹈覆轍。"""
    c = _conn(tmp_path)
    too_big = b"x" * (patch_store.MAX_BYTES + 1)
    with pytest.raises(ValueError, match="超過上限"):
        patch_store.put(c, "workbook", too_big)
    assert patch_store.get(c, "workbook") is None, "失敗就是沒存，不可以留半份"


def test_狀態表每一項都要報告有沒有(tmp_path):
    c = _conn(tmp_path)
    patch_store.put(c, "history", b"[]", by="alice")
    items = {i["key"]: i for i in patch_store.status(c)["items"]}
    assert set(items) == set(patch_store.SHARED_KEYS), "每一項都要列出來，有沒有都要說"
    assert items["history"]["present"] and items["history"]["updated_by"] == "alice"
    assert not items["workbook"]["present"] and items["workbook"]["updated_at"] is None


def test_清除是真的清掉(tmp_path):
    c = _conn(tmp_path)
    patch_store.put(c, "mail_log", b"[]", by="alice")
    patch_store.delete(c, "mail_log")
    assert patch_store.get(c, "mail_log") is None
