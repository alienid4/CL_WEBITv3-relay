"""S5：後端 REST API 層（D29 API優先設計）。

原為單一巨型模組（372 路由）。為避開大檔關卡並便於維護，已依網域拆分：
模組級共用狀態移到 _app_shared.py，372 路由拆進 routers/ 各 APIRouter 模組。
本檔保留 app=FastAPI()、middleware、CORS、startup，並把所有路由掛回 app。
公開 API 路徑／方法／行為與拆分前逐位元組一致。

相容性（讓既有測試與既有程式不用改）：
  1. 路由函式 re-export 回本模組，api.<handler> 仍可直接取用。
  2. 路由直接 append 進 app.router.routes（維持拆分前「扁平 APIRoute 列表」結構），
     列舉 app.routes 的測試照樣數得到。
  3. 本模組裝了會傳播的 __setattr__：對 api.<global> 做 monkeypatch（例如
     api.get_db_path / api.MAPPING_PATH）會同步寫進 _app_shared 與各 router 模組，
     讓路由函式（在自己模組命名空間解析該名字）看得到修改——等同拆分前同一個模組。
"""
from __future__ import annotations

import sys as _sys
import types as _types
import importlib as _importlib

from _app_shared import *  # noqa: F401,F403

import access_control  # noqa: E402
import access_policy  # noqa: E402



app = FastAPI(title="資產盤點模組 API")

# Q42（2026-10-17）：回應壓縮。/api/manage-state 一次 1.16MB（4,788 筆），gzip 後約 86KB；
# 使用者經 Tailscale／公司網路時，傳輸本身就是秒級，而瀏覽器同一主機只開 6 條連線，
# 大回應卡住連線，後面十幾支 API 只能排隊。壓縮等級 5：比預設 9 快很多、體積只差一點。
# 放在最內層（第一個註冊，緊貼路由）：外面那幾層 @app.middleware 會把回應改成串流，
# 包在它們外面就看不到 Content-Length，minimum_size 失效、連 70B 的小回應也壓。小於 1KB 的不壓。
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402

app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)

@contextlib.contextmanager
def _middleware_db():
    """Middleware 專用的 DB 連線。

    ⚠️ **不能直接 get_connection()。** middleware 跑在 FastAPI 依賴系統之外，
    直接開連線拿到的是「預設路徑那個 DB」，不是「這個 app 實際在用的 DB」。
    兩者不同時（測試會覆寫 get_db、未來若要一個行程服務多個 DB 也一樣），
    紀錄會被安靜地寫到另一個檔案裡——**功能看起來正常，紀錄卻在別的地方**，
    是那種要出事才會發現的錯。2026-08-26 寫測試時當場踩到。

    所以走跟路由同一條路：有覆寫就用覆寫的，沒有才用預設。
    """
    factory = app.dependency_overrides.get(get_db, get_db)
    gen = factory()
    conn = next(gen)
    try:
        yield conn
    finally:
        gen.close()          # 觸發 get_db 的 finally，由它負責 close



# ===== Q91：CSRF（double-submit，綁 session）＋ 匯出紀錄／檔案下載的最後防線 =====
# 註冊順序有意義：這兩層寫在「操作紀錄」middleware 之前＝比它更內側，
# 所以被擋下的請求（403 csrf／403 權限）一樣會被操作紀錄記到。

@app.middleware("http")
async def _csrf_middleware(request: Request, call_next):
    """會改東西的請求（POST/PUT/PATCH/DELETE）帶了 session cookie，就必須帶對的 X-CSRF-Token。

    token = HMAC(session token)：跨站攻擊者能讓瀏覽器帶出 cookie，但讀不到 cookie 值，也不能在跨站請求
    加自訂標頭（會先被 CORS 預檢擋掉），所以拿不到、也送不出正確的 token。
    不檢查的：①安全方法 ②auth.CSRF_EXEMPT（登入）③**根本沒帶 session cookie** 的請求
    （沒有環境憑證可被借用，例如 push agent 用 X-Agent-Key；沒登入的人本來就是 401）。
    """
    if (request.method in auth.CSRF_SAFE_METHODS or not request.url.path.startswith("/api/")
            or (request.method, request.url.path) in auth.CSRF_EXEMPT):
        return await call_next(request)
    cookie = request.cookies.get(auth.SESSION_COOKIE_NAME)
    if not auth.csrf_ok(cookie, request.headers.get(auth.CSRF_HEADER_NAME)):
        # 登入停用＋過期／殘留的 session cookie（例如還原資料庫之後）：這時伺服器本來就把請求當 admin 放行，
        # CSRF 沒有東西可保護；若在這裡擋，瀏覽器會卡在 403 直到手動清 cookie。cookie 有效時（真的登入著）仍照常檢查。
        try:
            with _middleware_db() as _c:
                if auth_disabled(_c) and auth.resolve_session(_c, cookie) is None:
                    return await call_next(request)
        except Exception:  # noqa: BLE001 - 查不出來就維持擋下（fail-closed）
            pass
        return JSONResponse(status_code=403, content={
            "detail": "安全檢查未通過（缺少或不符的 CSRF token）。請重新整理頁面後再試一次；若仍失敗請重新登入。",
            "code": "csrf"})
    return await call_next(request)


#: Q91 M4：整份清單型的 JSON 讀取（不是下載檔，但等同把清冊整批拿走）。回傳超過門檻列數就寫一筆 export_log（export_name='bulk-json'）。
_BULK_JSON_THRESHOLD = {
    "/api/accounts": 200, "/api/accounts/findings": 200, "/api/accounts/hosts": 200, "/api/accounts/sudo-rules": 200,
    "/api/accounts/by-username": 200, "/api/accounts/inventoried-hosts": 200, "/api/assets": 2000,
}


def _json_row_count(raw: bytes, encoding: str | None) -> int | None:
    import gzip
    import json as _json
    try:
        if encoding and "gzip" in encoding:
            raw = gzip.decompress(raw)
        data = _json.loads(raw)
    except Exception:  # noqa: BLE001 - 不是 JSON／解不開：算不出列數就不記（不影響回應）
        return None
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        lens = [len(v) for v in data.values() if isinstance(v, list)]
        return max(lens) if lens else None
    return None


@app.middleware("http")
async def _bulk_json_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.method != "GET" or request.url.path not in _BULK_JSON_THRESHOLD or response.status_code != 200:
        return response
    chunks = [c async for c in response.body_iterator]
    raw = b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks)
    rebuilt = Response(content=raw, status_code=response.status_code, media_type=response.media_type)
    rebuilt.raw_headers = [(k, v) for k, v in response.raw_headers]       # 原標頭照搬（含 Set-Cookie、Content-Length）
    n = _json_row_count(raw, response.headers.get("content-encoding"))
    if n is not None and n > _BULK_JSON_THRESHOLD[request.url.path]:
        try:
            with _middleware_db() as conn:
                session = auth.resolve_session(conn, request.cookies.get(auth.SESSION_COOKIE_NAME))
                if session is None and auth_disabled(conn):
                    username, level = _anonymous_session()["username"], "admin"
                elif session is None:
                    username, level = None, None
                else:
                    username, level = session["username"], auth.session_level(session)
                access_control.log_export(
                    conn, username=username, level=level, ip=activity.client_ip(request), method="GET",
                    path=request.url.path, name="bulk-json", filename=None, rows=n,
                    summary=access_control.safe_query_summary(request.url.query), status=200)
        except Exception as exc:  # noqa: BLE001 - 這類只是「大量讀取」的軌跡，記不進去不擋畫面
            print(f"[export_log] bulk-json 寫入失敗：{type(exc).__name__}: {exc}")
    return rebuilt


@app.middleware("http")
async def _export_audit_middleware(request: Request, call_next):
    """每一次檔案下載／匯出都寫一筆 export_log（誰、何時、哪支、幾列、條件）。

    也是「viewer 不能拿檔案」的最後防線：access_policy.EXPORT_LEVEL 已在路由層擋掉登記過的匯出，
    但若有人新增了下載端點卻忘了登記，回應帶 attachment 時這裡仍會把只能看的帳號擋下（測試另會抓出沒登記的端點）。
    被拒絕的匯出（403）也留一筆，稽核才看得到「誰想拿而被擋」。
    """
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/api/"):
        return response
    route = request.scope.get("route")
    template = getattr(route, "path", path)
    is_file = response.status_code < 400 and access_control.is_attachment(response)
    denied_export = response.status_code == 403 and (request.method, template) in access_policy.EXPORT_LEVEL
    if not (is_file or denied_export):
        return response
    try:
        with _middleware_db() as conn:
            session = auth.resolve_session(conn, request.cookies.get(auth.SESSION_COOKIE_NAME))
            if denied_export and session is None:
                return response       # 沒有有效 session 的人被擋：不寫紀錄（否則假 cookie 能讓表無限長大）
            if session is None and auth_disabled(conn):
                username, level = _anonymous_session()["username"], "admin"
            elif session is None:
                username, level = None, None
            else:
                username, level = session["username"], auth.session_level(session)
            if is_file and level == "viewer":
                if hasattr(response, "body_iterator"):
                    await response.body_iterator.aclose()
                access_control.log_export(
                    conn, username=username, level=level, ip=activity.client_ip(request), method=request.method,
                    path=template, name="(被拒絕：只能看的帳號不能下載)", filename=None, rows=None,
                    summary=None, status=403)
                return JSONResponse(status_code=403, content={
                    "detail": "您的帳號權限是「只能看」，不能下載或匯出檔案。需要的話請管理員在系統設定調整。"})
            st = request.state
            rows = getattr(st, "export_rows", None)
            if rows is None:
                hdr = response.headers.get("x-row-count")
                rows = int(hdr) if hdr and hdr.isdigit() else None
            summary = getattr(st, "export_summary", None) or response.headers.get("x-export-summary") or ""
            qs = access_control.safe_query_summary(request.url.query)
            if qs:
                summary = f"{summary}；條件：{qs}" if summary else f"條件：{qs}"
            access_control.log_export(
                conn, username=username, level=level, ip=activity.client_ip(request), method=request.method,
                path=template,
                name=(getattr(st, "export_name", None) or template) if is_file else "(被拒絕：權限不足)",
                filename=access_control.filename_from_disposition(response.headers.get("content-disposition")),
                rows=rows, summary=summary, status=response.status_code)
    except Exception as exc:  # noqa: BLE001 - 下載一定要留紀錄：寫不進去／查不出身分就不放行（fail-closed）
        print(f"[export_log] 寫入失敗：{type(exc).__name__}: {exc}")
        if is_file:
            if hasattr(response, "body_iterator"):
                try:
                    await response.body_iterator.aclose()
                except Exception:  # noqa: BLE001 - 已經要回 500，關閉失敗不影響結果
                    pass
            return JSONResponse(status_code=500, content={
                "detail": "下載紀錄寫入失敗，為了留下稽核軌跡這次下載已取消。請稍後再試或通知管理員。"})
    return response


#: 用 POST 只是因為參數太長放不進網址、實際上是**唯讀查詢**的端點——不記進操作紀錄
#: （不然每開一次 2-1 頁面就多一筆「POST」，真正改東西的紀錄會被淹掉）。只放唯讀的。
_READONLY_POST_PATHS = frozenset({"/api/onboard/audit/latest-by-ip"})


@app.middleware("http")
async def _activity_log_middleware(request: Request, call_next):
    """記錄會改東西的請求（非 GET）。

    為什麼用 middleware 而不是跟心跳一樣掛 require_auth：**要記結果**。
    依賴在路由執行「之前」跑，拿不到狀態碼；而「誰試了什麼但失敗了」正是
    稽核最想看的一種（403/409/500 都要留）。

    為什麼只記非 GET：GET 佔九成以上流量而稽核價值低，全記會讓這張表一天長
    好幾萬列，真正要查「誰改了這台」的時候反而被淹掉。讀取類真的要留痕的地方
    已經各自有專屬表（doc_download_audit／credential_use_audit）。

    ⚠️ 不讀也不記 request body：body 裡可能有密碼與真實資料，複製一份進另一張表
    只是多開一個外洩面。而且讀了 body 會把 stream 吃掉，後面的路由就收不到了。
    """
    import time

    if (request.method == "GET" or not request.url.path.startswith("/api/")
            or request.url.path in _READONLY_POST_PATHS):
        return await call_next(request)

    t0 = time.perf_counter()
    response = await call_next(request)
    took = int((time.perf_counter() - t0) * 1000)

    # 登入相關由各自的端點記（那裡才知道是誰、成功還失敗），這裡不要重複記一次
    if request.url.path in ("/api/auth/login", "/api/auth/logout"):
        return response

    try:
        with _middleware_db() as conn:
            session = auth.resolve_session(conn, request.cookies.get(auth.SESSION_COOKIE_NAME))
            activity.log(
                conn,
                username=session["username"] if session else None,
                ip=activity.client_ip(request),
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=took,
            )
    except Exception:  # noqa: BLE001 - 記錄失敗不可以影響請求本身
        pass
    return response



@app.middleware("http")
async def _unhandled_error_as_json(request: Request, call_next):
    """未預期的例外一律回 JSON 500，而且**放在 CORS 內側**。

    2026-09-14 公司機按匯出失敗，瀏覽器只顯示「被 CORS 擋掉」：Starlette 對未處理例外的
    500 是在最外層產生的，**不經過 CORSMiddleware**，回應沒有 Access-Control-Allow-Origin，
    前端連錯誤內容都讀不到——真正的錯被 CORS 訊息蓋掉，人只能猜。
    這裡在 CORS 內側接住，回錯誤型別與訊息，畫面 toast 才講得出是什麼錯；
    完整 traceback 照印到服務日誌。

    註冊順序有意義：必須寫在 add_middleware(CORSMiddleware) **之前**（後加的在外層）。
    """
    try:
        return await call_next(request)
    except Exception as exc:  # noqa: BLE001 - 就是要接住所有沒被處理的例外
        import traceback

        traceback.print_exc()
        return JSONResponse(status_code=500,
                            content={"detail": f"伺服器錯誤：{type(exc).__name__}: {exc}"[:500]})



app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_origin_regex=_allowed_origin_regex,  # 內網同主機前端埠自動放行（見上方註解）
    allow_credentials=True,  # 帶cookie（session_token）必須開這個，前端fetch才要用credentials:"include"
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- 掛載網域路由（見 routers/）----
from routers import assets, accounts, onboard, collect_scan, reports, vulns, arch, imports, config_audit, system, rebuild, schedule, digest, risk, incident, rackdiagram, overlaps, countbridge, rackaudit, v2, transfer, integrations, access

_ROUTER_MODS = [assets, accounts, onboard, collect_scan, reports, vulns, arch, imports, config_audit, system, rebuild, schedule, digest, risk, incident, rackdiagram, overlaps, countbridge, rackaudit, v2, transfer, integrations, access]
_ROUTER_NAMES = ['assets', 'accounts', 'onboard', 'collect_scan', 'reports', 'vulns', 'arch', 'imports', 'config_audit', 'system', 'rebuild', 'schedule', 'digest', 'risk', 'incident', 'rackdiagram', 'overlaps', 'countbridge', 'rackaudit', 'v2', 'transfer', 'integrations', 'access']

# 把各 router 收集到的 @router.<method>(...) 原樣轉成 app.<method>(...)(func)，
# 等同拆分前的 @app.<method>——路由直接註冊在 app 上：app.routes 扁平、
# dependency override 以 app 為來源。各網域第一段路徑互不重疊，跨網域註冊順序不影響比對。
# Q91：每支 /api 端點依 access_policy 登記的等級，自動加上一道 require_level(...) 路由依賴
# （公開／agent 自己驗證的端點不加）。沒登記的非 GET 一律當 admin（fail-closed），測試會抓出來。
for _m in _ROUTER_MODS:
    for _method, _args, _kwargs, _func in _m.router.collected:
        _path = _args[0] if _args else _kwargs.get("path", "")
        _lvl = access_policy.level_for(_method, _path) if str(_path).startswith("/api/") else None
        if _lvl is not None:
            _kwargs = {**_kwargs, "dependencies": [*(_kwargs.get("dependencies") or []), Depends(require_level(_lvl))]}
        getattr(app, _method)(*_args, **_kwargs)(_func)

# 把路由函式 re-export 回本模組，讓 api.<handler> 仍可直接取用（既有測試會這樣叫）。
for _m in _ROUTER_MODS:
    for _n in getattr(_m, '__all__', ()):
        globals()[_n] = getattr(_m, _n)




@app.on_event("startup")
def _start_scheduler():
    # 只在正式服務啟動時開排程器（webit3-api.service 設 ASSET_SCHEDULER=1）；
    # 測試/開發匯入 app 時不啟動，避免背景亂掃。
    if os.environ.get("ASSET_SCHEDULER") == "1":
        # 上一個行程留下的「收集中」孤兒列收尾（2026-10-20：裝 patch 重啟後 2-1 一直亮「收集中…」）。
        # 要在排程器開跑前做，不然每週那一輪可能剛好開出一列新的 running 被誤收。
        try:
            import collect_dispatch as _cd
            _c = get_connection()
            try:
                _n = _cd.finalize_orphans(_c)
                if _n:
                    print(f"[collect_dispatch] {_n} 輪收集在服務重啟時被中斷，已標成「{_cd.INTERRUPTED_BY_RESTART}」")
                # 納管後收集的隊伍只在記憶體：重啟清掉的那幾台記進紀錄、留給「重新排入」
                import post_onboard as _po
                _lost = _po.recover_after_restart(_c)
                if _lost:
                    print(f"[post_onboard] 服務重啟：{len(_lost)} 台排隊被清掉，已記錄")
            finally:
                _c.close()
        except Exception as exc:  # noqa: BLE001 - 收尾失敗不可以擋住服務啟動
            print(f"[collect_dispatch] 中斷收尾失敗（不影響服務）：{exc}")
        scan_service.start_scheduler()
        # 檔案傳送（Q94）：重啟時還在跑的傳送收尾，並開一條每分鐘看「等待允許時段」的背景執行緒。
        # 模組關著時什麼都不會動（resumer 每次都先問模組開關與登入狀態）。
        try:
            import transfer_service as _ts
            _c = get_connection()
            try:
                _ts.recover_after_restart(_c)
            finally:
                _c.close()
            _ts.start_resumer(get_db_path)
        except Exception as exc:  # noqa: BLE001 - 這個模組出事不可以擋住整個服務啟動
            print(f"[transfer] 啟動收尾失敗（不影響服務）：{exc}")
        # 稽核表外部摘要：啟動時先驗證再留存一次（失敗只印出來，不影響服務啟動）。
        try:
            import audit_digest as _ad
            _c = get_connection()
            try:
                _v = _ad.verify(_c)
                if _v["status"] in ("tampered", "log_broken"):
                    print(f"[audit_digest] 警告：{_v['message']}")
                _ad.retain(_c, force=True)
            finally:
                _c.close()
        except Exception as exc:  # noqa: BLE001 - 摘要失敗不可以擋住服務啟動
            print(f"[audit_digest] 啟動留存失敗（不影響服務）：{exc}")
        # 清掉超過保留期的操作紀錄。跟排程器同一個條件：只有正式服務會做，
        # 測試匯入 app 時不要動任何人的資料。
        try:
            conn = get_connection()
            try:
                n = activity.purge_old(conn)
                if n:
                    print(f"[activity] 清掉 {n} 筆超過 {activity.RETAIN_DAYS} 天的操作紀錄")
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - 清理失敗不可以擋住服務啟動
            print(f"[activity] 清理舊紀錄失敗（不影響服務）：{exc}")

# ---- 讓 api.<global> 的 monkeypatch 傳播到各模組（相容拆分前的單模組行為）----
class _PropagatingModule(_types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name.startswith('_'):
            return
        try:
            targets = [_importlib.import_module('_app_shared')]
            for _d in _ROUTER_NAMES:
                targets.append(_importlib.import_module('routers.' + _d))
        except Exception:
            return
        for _t in targets:
            if hasattr(_t, name):
                setattr(_t, name, value)

_sys.modules[__name__].__class__ = _PropagatingModule
