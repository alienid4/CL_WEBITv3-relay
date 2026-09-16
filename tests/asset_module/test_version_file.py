"""version.json 必須是乾淨 UTF-8（無 BOM）且解析得出版號。

為什麼需要這支測試：在 Windows 用 PowerShell 的 Set-Content 改這個檔會塞進 UTF-8 BOM，
json 解析就炸，/api/version 安靜地回 "?"——服務照常活著、測試照樣全綠，
只有畫面右上角的版號變成問號，很容易上線好幾天沒人發現。實際踩過一次。
"""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

VERSION_PATH = BACKEND / "version.json"


def test_version檔沒有BOM():
    raw = VERSION_PATH.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), (
        "version.json 有 UTF-8 BOM——多半是被 PowerShell 的 Set-Content/Out-File 改過。"
        "改用不帶 BOM 的方式寫入，否則 /api/version 會回 '?'"
    )


def test_version檔解析得出版號():
    data = json.loads(VERSION_PATH.read_text(encoding="utf-8"))
    assert data["version"], "version 欄位不可為空"
    # 版號要是 x.y.z 這種可比較的形式，不然「有沒有 bump」根本判不出來
    parts = data["version"].split(".")
    assert len(parts) >= 2 and all(p.isdigit() for p in parts), \
        f"版號格式怪異：{data['version']}"


def test_api回得出真版號而不是問號():
    """直接呼叫 api 的讀取路徑，確認它拿得到值——這才是畫面實際走的那條路。"""
    import api

    info = api.version_endpoint()
    assert info["version"] != "?", "/api/version 回 '?' 代表 version.json 讀不到或解析失敗"
