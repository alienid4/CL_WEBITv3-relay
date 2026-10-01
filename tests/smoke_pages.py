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
    "/collect-account",
    "/golive", "/drift", "/accounts", "/account-matrix", "/account-ops",
    "/services", "/eos", "/topology", "/settings",
    # 2026-09-20 教訓：新頁沒加進這份清單＝那頁完全沒有防護。
    # 當天 /reports/scan-gaps 的標註面板把第二個 <tr> 寫在 v-for 外面，
    # pytest 全綠、nuxt build 也綠，公司打開卻整頁空白（console 連噴
    # Cannot read properties of undefined）。這支就是專門擋這種的，它卻沒被叫到。
    "/number-check", "/reports/scan-gaps", "/reports/host-sources",
    "/reports/system-group", "/reports/physical-distribution", "/reports/system-overview",
    "/reports/business-systems", "/reports/classify", "/reports/monthly",
    "/hosts", "/software", "/relocation", "/batch-onboard", "/san", "/hmc",
    "/anomalies", "/distribution", "/features", "/blast",
    # 2026-09-21：/architecture 一直不在清單裡，所以它白頁過兩次都沒被擋下來。
    # /ocp 是它的第二＋三層（版本／風險／逐台狀態），一起顧。
    "/architecture", "/ocp",
    "/patch",
    "/config-audit",          # 9-2 組態檢核（B-15）
    "/health-check",          # 9-3 主機自我檢查——一直漏在清單外，補上
    # 分頁是 client 端切換的，但 ?tab= 進來時 SSR 就會渲染那一塊，
    # 所以帶參數巡才真的有巡到「寄信設定」那一頁的內容（B-19）。
    "/settings?tab=mail",
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


# [B-20] 有些頁不是「回 200 就好」：
#   ・/patch 的內容必須是**包裝頁**（Nuxt 那一頁），不是被 public/ 底下的靜態檔蓋掉
#     （2026-09-21 在 221 踩到：public/patch/ 蓋掉路由 → 工具的相對路徑腳本全部 404）
#   ・包裝頁要載得動的靜態資源，自己也要回 200
MUST_CONTAIN = {
    "/patch": ("/patch-app/index.html",),      # iframe 指向靜態包＝包裝頁真的有渲染
}
MUST_LOAD = ["/patch-app/index.html", "/patch-app/js/store.js", "/patch-app/js/main.js"]


def check(opener, base: str, path: str, cookie: str = "") -> tuple[str, int, str]:
    headers = {"Cookie": cookie} if cookie else {}
    req = urllib.request.Request(base + path, method="GET", headers=headers)
    try:
        with opener.open(req, timeout=30) as r:
            body = ""
            need = MUST_CONTAIN.get(path)
            if need:
                body = r.read(400_000).decode("utf-8", "ignore")
                missing = [s for s in need if s not in body]
                if missing:
                    # 用 599 這個「不是真的 HTTP 狀態」標示：回了 200 但內容不對
                    return path, 599, "頁面回 200 但內容不對，少了：" + "、".join(missing)
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
    # 部署腳本用：直接給已知可用的 session cookie（221 有 /opt/webit3/.verify_token），
    # 不必在腳本裡塞密碼。沒有帳密也沒有 token 就照舊判失敗（避免假綠燈）。
    ap.add_argument("--token", help="session_token（免帳密，部署後驗證用）")
    args = ap.parse_args()

    opener = _opener()
    cookie = ""
    if args.token:
        cookie = args.token.strip()
        print("使用 session token（免登入）")
    elif args.api and args.user and args.password:
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

    # [B-20] 包裝頁要用到的靜態資源自己也要能載（相對路徑被解析錯會全部 404）
    for path in MUST_LOAD:
        p, code, detail = check(opener, args.base.rstrip("/"), path, cookie)
        mark = "ok " if 200 <= code < 300 else "壞 "
        if not (200 <= code < 300):
            bad.append((p, code, detail or "靜態資源載不到"))
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
