#!/usr/bin/env python3
"""部署後冒煙測試：登入後把每一頁都打一次，看有沒有頁面壞掉。

**為什麼需要這支**（2026-08-15 使用者問「改 A 死 B 怎麼預防」時做的）：
後端有 490 個自動測試，改壞了會立刻紅燈；但**前端一個測試都沒有**，而
`nuxt build` 通過不代表頁面打得開——Vue 樣板是執行期才解析變數的。

今天就發生一次：把 vCenter 那塊從系統設定搬走之後，設定頁還留著 `vc.enabled`、
`vcHealth.status` 這些已經不存在的參考。build 一路綠燈，但實際打開設定頁會炸。
那次是靠人工 grep 抓到的——靠紀律不靠工具，遲早會漏。

**這支抓得到什麼、抓不到什麼**（講清楚，免得誤以為有它就安全）：
  抓得到：SSR 期間丟例外的頁面（存取 undefined 的屬性、composable 用錯…）→ 5xx
  抓不到：畫面渲染出來但內容是空的、按鈕點下去才壞、樣式跑版
所以它是**下限**不是保證：能過不代表沒問題，不能過就一定有問題。

用法：
    python tests/smoke_pages.py --base http://YOUR_SERVER_IP:3000 \\
        --api http://YOUR_SERVER_IP:8000 --user admin --password '...'
**一定要帶帳密**：沒帶的話每一頁都會被導去 /login，等於什麼都沒驗到，所以判失敗。

實測驗證過它真的有效（2026-08-15）：在 drift.vue 的樣板植入
`{{ removedObject.status }}`（重現當天 vCenter 搬移後留下的殘存參考），
`nuxt build` 回報 **0 個錯誤**，這支回報 **/drift HTTP 500**。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

# 要巡的頁面。**新增頁面時要記得加進來**——漏了就等於那頁沒有任何防護。
# 帶參數的動態頁（/assets/{serial}）用一個已知存在的值，找不到就跳過，
# 不要因為測試資料不同就整包紅燈。
STATIC_PAGES = [
    "/", "/issues", "/scan-results", "/assets", "/assets/new",
    "/documents", "/segments", "/data-quality", "/import", "/adopt", "/pipeline",
    "/golive", "/drift", "/accounts", "/account-matrix", "/account-ops",
    "/services", "/eos", "/topology", "/settings",
]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不要跟著轉址走。

    第一版沒擋，urllib 預設會自動跟著 302 跑到 /login 拿到 200，於是整份報告
    「19 頁全綠」——但那 19 個 200 全部是同一個登入頁，一頁都沒真的渲染到。
    測試自己給假綠燈比沒有測試更危險，所以這裡明確擋掉，讓轉址如實顯示成 302。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()), _NoRedirect
    )


def login(opener, api: str, user: str, password: str) -> str:
    """登入並回傳可直接帶著走的 Cookie 標頭字串。

    回傳字串而不是靠 cookie jar 自動帶：session cookie 綁在 API 的網域上，
    前端如果是另一個主機/埠（開發時很常見），jar 就不會把它帶過去，
    結果每一頁都被導去登入頁——測試看起來全紅，其實只是 cookie 沒送到。
    """
    body = json.dumps({"username": user, "password": password}).encode()
    req = urllib.request.Request(
        f"{api}/api/auth/login", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with opener.open(req, timeout=20) as r:
            if r.status != 200:
                return ""
            jar = next(h for h in opener.handlers
                       if isinstance(h, urllib.request.HTTPCookieProcessor)).cookiejar
            return "; ".join(f"{c.name}={c.value}" for c in jar)
    except urllib.error.HTTPError as e:
        print(f"  登入失敗：HTTP {e.code}")
        return ""
    except OSError as e:
        print(f"  登入失敗：{e}")
        return ""


def check(opener, base: str, path: str, cookie: str = "") -> tuple[str, int, str]:
    headers = {"Cookie": cookie} if cookie else {}
    req = urllib.request.Request(base + path, method="GET", headers=headers)
    try:
        with opener.open(req, timeout=30) as r:
            return path, r.status, ""
    except urllib.error.HTTPError as e:
        # 5xx＝頁面渲染時炸了，這就是要抓的東西；3xx 是被擋在登入外（沒真的渲染）
        detail = ""
        try:
            detail = e.read(400).decode("utf-8", "ignore").replace("\n", " ")[:200]
        except Exception:  # noqa: BLE001 - 讀不到內文不影響判定
            pass
        return path, e.code, detail
    except OSError as e:
        return path, 0, str(e)


def main() -> int:
    # Windows 主控台預設 cp950，印 ✔/✘ 會直接丟 UnicodeEncodeError——
    # 測試自己因為輸出編碼而崩潰，會被誤讀成「測試環境有問題」而不是「頁面壞了」。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 不支援就算了，不值得為此中斷
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="前端網址，例：http://YOUR_SERVER_IP:3000")
    ap.add_argument("--api", help="後端網址（要登入才給）")
    ap.add_argument("--user")
    ap.add_argument("--password")
    args = ap.parse_args()

    opener = _opener()
    cookie = ""
    if args.api and args.user and args.password:
        cookie = login(opener, args.api, args.user, args.password)
        print(f"登入 {args.user}：{'成功' if cookie else '失敗'}")
    logged_in = bool(cookie)

    bad, redirected = [], []
    for path in STATIC_PAGES:
        p, code, detail = check(opener, args.base.rstrip("/"), path, cookie)
        mark = "ok "
        if code >= 500 or code == 0:
            mark = "壞 "
            bad.append((p, code, detail))
        elif code in (301, 302, 303, 307, 308):
            mark = "轉 "
            redirected.append(p)
        print(f"  {mark} {code:>3}  {p}")

    print()
    if bad:
        print(f"✘ {len(bad)} 頁壞掉：")
        for p, code, detail in bad:
            print(f"    {p}  HTTP {code}  {detail}")
        return 1

    if redirected:
        # 被導去登入＝那一頁根本沒渲染。帶了帳密還被導轉更嚴重（登入沒生效），
        # 這種情況一定要算失敗，否則整份報告會是空心的綠燈。
        print(f"✘ {len(redirected)} 頁被導轉（沒有真的渲染到）：{', '.join(redirected)}")
        if logged_in:
            print("   已登入卻仍被導轉，登入狀態可能沒帶進請求。")
        else:
            print("   沒帶 --user/--password，等於什麼都沒驗到。")
        return 1

    print(f"✔ {len(STATIC_PAGES)} 頁都有渲染出來且沒有 5xx"
          f"{'（已登入）' if logged_in else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
