"""CORS：內網同主機的前端埠要自動放行，公網／錯埠一律不放行。

2026-09-09 公司機器實際踩到：`deploy.sh` 的 `API_HOST` 預設是家裡那台，
公司部署沒設對 → 公司的前端網址不在 CORS 白名單 → 所有跨源匯出下載被瀏覽器
擋掉，畫面只寫「匯出失敗」。**匯出端點本身沒有 bug。**
永久修：私有網段（RFC1918）＋localhost 的前端埠自動放行。

這個測試釘住「放行哪些、不放行哪些」，免得日後被改鬆（放行公網）或改壞（又擋內網）。

⚠️ **這裡的 IP 一律用不會出現在真實環境的範例值。**
`tests/` 會被同步到公開的 relay 倉庫，去識別化替換表會把真實內網 IP 換成
`YOUR_SERVER_IP` 這類佔位字串——那不是合法 IP，正則當然不會過，於是
「原始碼測試綠、去識別化產出物測試紅」。2026-09-09 這個檔就是這樣把 CI 弄紅的
（原本寫了公司與家裡的實 IP）。同樣的坑 `test_onboard_no_sudo.py` 也記過一次。

**規則**：這個檔案裡不要出現任何真實環境的 IP。要測「私有網段」就用
10.0.0.x / 172.16-31.x / 192.168.99.x 這種明顯是範例的值。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import api  # noqa: E402


def _match(origin: str) -> bool:
    return re.match(api._allowed_origin_regex, origin) is not None


def test_內網同主機的前端埠要放行():
    """三段私有網段都要涵蓋——公司在 10.x、家裡在 192.168.x，將來可能有 172.x。"""
    assert _match("http://10.0.0.14:3000")          # 10.0.0.0/8
    assert _match("http://192.168.99.21:3000")      # 192.168.0.0/16
    assert _match("http://172.16.0.5:3000")         # 172.16-31 私有段（下界）
    assert _match("http://172.31.255.254:3000")     # 同上（上界）
    assert _match("http://127.0.0.1:3000")
    assert _match("http://localhost:3000")


def test_公網或錯埠或非私有段一律不放行():
    assert not _match("http://10.0.0.14:8081")      # 後端埠不是前端來源
    assert not _match("http://8.8.8.8:3000")        # 公網 IP
    assert not _match("http://172.32.0.1:3000")     # 172.32 已超出私有段
    assert not _match("http://11.0.0.1:3000")       # 11.x 不是私有段
    assert not _match("http://evil.example.com:3000")
    assert not _match("https://10.0.0.14:3000")     # 這些部署都走 http


def test_測試檔本身不可以出現真實環境的IP():
    """守住這個檔案自己——2026-09-09 就是因為寫了實 IP 把 relay CI 弄紅。

    去識別化會把真實內網 IP 換成佔位字串，換完就不是合法 IP，正則不會過。
    症狀是「本機綠、CI 紅」，而 CI 的錯誤訊息只說 assert False，很難聯想。
    """
    text = Path(__file__).read_text(encoding="utf-8")
    # 前綴用組的，不要寫成字面值——不然這條測試會抓到自己（第一版就是這樣紅的）
    prefixes = [".".join(p) + "." for p in (("192", "168", "1"), ("10", "92"), ("10", "93"))]
    for bad in prefixes:
        assert bad not in text, (
            f"這個檔案出現了真實環境的 IP 前綴 {bad!r}——"
            "去識別化會把它換成佔位字串，relay CI 會紅。請改用範例網段")
