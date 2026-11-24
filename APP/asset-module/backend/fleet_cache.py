"""全機隊計算的跨請求快取（2026-10-21 使用者：「2-1、3-1 都卡一下才有資料」「我也沒有要重新統計」）。

量測（221，4,785 筆資產）：資產查詢只要 50 列，卻每次把整個機隊重算一遍——
健康檢查 1.2 秒、OS 分類 0.25 秒、四態統計 0.14 秒…；2-1 收集結果 0.44 秒（逐台 OS 大類）。
這些結果只跟資料庫內容（＋設定檔、＋時間）有關，**資料沒變就沒有理由重算**。

怎麼知道「資料沒變」：看資料庫檔案本身（不是靠記得「誰寫過」——收集、匯入、排程、
命令列工具都可能寫，漏一個就是錯的數字）。SQLite WAL 模式下，任何一筆提交都會改到
主檔或 -wal 檔（大小、修改時間、WAL 表頭的 checkpoint 序號與 salt）。只讀的請求不會動到
這兩個檔（221 實測：連打 10 支 GET，主檔與 -wal 都沒變）。

保守的地方（寧可重算，不可給舊數字）：
  * 呼叫的這條連線自己寫過（total_changes > 0）或交易還沒結束 → 不用快取、也不存
    （自己寫了還沒落到檔案的東西，檔案指紋看不到）
  * 函式算的過程中寫了資料庫 → 不存（有副作用的函式每次照跑）
  * 記憶體資料庫／拿不到檔案路徑 → 不快取
  * 時間相關的判斷（EOS 剩幾天、7 天內沒連上…）→ 指紋含 5 分鐘時間格，最多晚 5 分鐘反映
  * 設定檔（backend/*.json）改了、台數口徑環境變數（WEBIT3_MACHINE_KEY）改了 → 指紋含它們
  * WAL 剛被寫過（0.2 秒內，寫入正在進行）→ 不存
  * 結果還取決於資料庫以外狀態的（收集正在跑）→ skip_if 照算
  * 回傳的是深拷貝：呼叫端改了結果也不會污染下一個人拿到的（實測拷貝約 0.02～0.04 秒）
  * WEBIT3_FLEET_CACHE=0 整個關掉（照舊每次重算）
  * 請求範圍快照（2.069.1，使用者：「正式機主機頁要等 10 秒」）：資料庫每分鐘都有收集在寫，跨請求的指紋在一個請求
    還沒算完時就變了，同一頁的十幾次全機隊計算變成每次都重算（221 實測一個請求重算 10 次、各 3 秒）。
    request_scope() 讓「同一個請求、同一條連線、中途沒有自己寫過」的重複呼叫共用同一份結果：
    這個請求看到的是一份一致的快照（卡片與下鑽更不可能不一致），請求結束就丟掉，不會留給下一個請求。

  * 主機頁「限時舊值」層（2.069.4，使用者：「正式機主機頁 summary 8 秒、profile 5 秒，目標 2 秒內」）：
    正式機收集每分鐘都在寫庫，上面的指紋一直變，跨請求快取永遠命不中，每次開主機頁都要把全機隊算 9 種各一遍
    （量測 6 千台：summary 約 6 秒、profile 約 3.5 秒）。主機履歷頁的端點（只有它們）用 stale_ok() 明確選擇
    「接受最多 STALE_MAX_AGE_S（120）秒前算好的全機隊結果」：
      - 結果仍是「某一刻的完整計算」，不是估的、不是增量改的；口徑與即時計算同一份程式，資料庫一時沒動時完全相等
      - 回應帶 fleet_as_of／fleet_age_s 明講「機隊級數字是幾秒前的」，不當成現在的數字
      - 任何非 GET 的請求（使用者按儲存、匯入、改設定）一律清空這一層（invalidate_recent），改完立刻看得到
      - 只在這一層被用到的鍵才會存、才會由背景執行緒定時重算（refresh_hot）；其他頁面行為完全不變
      - 超過期限、設定檔改了、台數口徑環境變數改了、換資料庫、這條連線自己寫過 → 一律回到即時計算

加總＝下鑽不受影響：同一份資料指紋下，卡片與下鑽拿的是同一份計算結果。
"""
from __future__ import annotations

import contextlib
import contextvars
import copy
import functools
import math
import os
import pickle
import sqlite3
import threading
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent
#: 時間格（秒）：時間相關的判斷最多晚這麼久反映
TIME_BUCKET_S = 300

_LOCK = threading.Lock()
#: 請求範圍快照（見模組說明）：None＝沒有範圍；範圍內是 {"conn": id, "changes": 進範圍時的 total_changes, "store": {鍵: 凍結結果}}
_SCOPE: contextvars.ContextVar = contextvars.ContextVar("fleet_cache_scope", default=None)


@contextlib.contextmanager
def request_scope(conn):
    """包住「一個請求」的全部讀取：範圍內同一條連線的重複全機隊計算只算一次（結果拷貝給每個呼叫端）。
    已在範圍內（巢狀）、快取關閉、拿不到連線識別時什麼都不做。不跨請求、不跨執行緒（contextvars）。"""
    if not enabled() or _SCOPE.get() is not None:
        yield
        return
    try:
        changes = conn.total_changes
    except (AttributeError, sqlite3.Error):
        yield
        return
    tok = _SCOPE.set({"conn": id(conn), "changes": changes, "store": {}})
    try:
        yield
    finally:
        _SCOPE.reset(tok)
#: 每把鍵一個的「正在算」鎖（單飛用）。鍵的數量有限（函式名＋參數），不會越長越大
_FLIGHT: dict = {}


def _flight_lock(key):
    with _LOCK:
        lk = _FLIGHT.get(key)
        if lk is None:
            lk = _FLIGHT[key] = threading.RLock()
        return lk

#: ---- 限時舊值層（見模組說明）----
#: 最多接受多久以前算好的機隊結果（秒）。**預設 0＝關閉**（2026-11-20 GPT 審查：金融業鐵律是「寧可重算不可給舊數字」，
#: 這一層在前端顯示資料時間、版本一致性、單台版計算完成前不得預設啟用；要試用才設 WEBIT3_HOST_PAGE_STALE_S=秒數，
#: 且需要風險擁有者核准）。只接受有限、非負、不超過 STALE_CEILING_S 的值；非法值一律當 0（fail closed）。
STALE_MAX_AGE_S = 0.0
STALE_CEILING_S = 600.0
#: 背景執行緒每隔多久看一次（秒）、一把鍵的結果多老了才重算（秒）、多久沒人看就不再重算（秒）。
#: 重算一輪約 5～6 秒 CPU：每把鍵約 45～75 秒重算一次，頁面看到的最大年紀約 85 秒，小於 STALE_MAX_AGE_S，
#: 所以有人在看的時候頁面幾乎不會碰到過期（過期就自己即時算，只是慢）。
REFRESH_EVERY_S = 30.0
REFRESH_MIN_AGE_S = 45.0
HOT_IDLE_S = 900.0
_STALE: contextvars.ContextVar = contextvars.ContextVar("fleet_cache_stale", default=None)
_FORCE_FRESH: contextvars.ContextVar = contextvars.ContextVar("fleet_cache_force_fresh", default=False)
#: (函式名, 參數) → (算的起點時間, 資料庫路徑, 設定簽章, 凍結結果)
_RECENT: dict = {}
#: (函式名, 參數) → 背景重算要的東西（函式、參數、庫路徑、最後被看的時間）
_HOT: dict = {}
#: 清空計數：算的途中有人寫了（epoch 變了）就不存，免得把改之前的結果存成「新的」
_EPOCH = [0]


def stale_max_age() -> float:
    raw = os.environ.get("WEBIT3_HOST_PAGE_STALE_S")
    if raw is None:
        return STALE_MAX_AGE_S
    try:
        v = float(raw)
    except ValueError:
        return 0.0                                   # 寫錯字：關掉（fail closed），不要退回任何舊值
    if not math.isfinite(v) or v < 0 or v > STALE_CEILING_S:
        return 0.0
    return v


@contextlib.contextmanager
def stale_ok(max_age: float | None = None):
    """主機履歷頁端點專用：範圍內接受「最多 max_age 秒前」算好的全機隊結果（見模組說明）。
    產生的 report（used／oldest_built／max_age_s）給呼叫端寫進回應。快取關閉或 max_age<=0 時什麼都不做。"""
    age = stale_max_age() if max_age is None else float(max_age)
    rep = {"used": 0, "oldest_built": None, "max_age_s": 0.0}
    if not enabled() or age <= 0 or _STALE.get() is not None:
        yield rep
        return
    tok = _STALE.set({"max_age": age, "rep": rep})
    try:
        yield rep
    finally:
        _STALE.reset(tok)


def scope_memo(conn, name: str, builder):
    """請求範圍內的衍生物件備忘（例：從全機隊結果建的 key→列索引）：同一請求同一條連線只建一次，之後直接拿
    **同一個物件**（不拷貝——呼叫端只能讀、不可改）。沒有範圍、連線自己寫過、交易沒結束 → 不備忘，照 builder() 現算。"""
    sc = _SCOPE.get()
    if sc is None or sc["conn"] != id(conn) or not enabled():
        return builder()
    try:
        if _dirty(conn) or conn.total_changes != sc["changes"]:
            return builder()
    except sqlite3.Error:
        return builder()
    memo = sc.setdefault("memo", {})
    if name not in memo:
        memo[name] = builder()
    return memo[name]


def scope_store(conn, name: str) -> dict | None:
    """請求範圍內的「可變備忘盒」（dict）：只在有效的請求範圍內回傳（同 scope_memo 的條件），否則 None＝呼叫端不要快取。
    給「依參數分多筆存」的函式用（例：collected_os.measured_for_rows 依登記組存）；盒子隨範圍結束消失，不跨請求、不靠 id(conn) 當跨請求鍵。"""
    sc = _SCOPE.get()
    if sc is None or sc["conn"] != id(conn) or not enabled():
        return None
    try:
        if _dirty(conn) or conn.total_changes != sc["changes"]:
            return None
    except sqlite3.Error:
        return None
    return sc.setdefault("memo", {}).setdefault(name, {})


def invalidate_recent() -> None:
    """清空限時舊值層（任何非 GET 的請求前後都要呼叫）；並讓算到一半的結果不要存。"""
    with _LOCK:
        _EPOCH[0] += 1
        _RECENT.clear()


def _cfg_sig(fp) -> tuple:
    return (fp[3], fp[5])        # 設定檔修改時間＋台數口徑環境變數（見 fingerprint）


def _recent_get(key, path, cfg, max_age):
    with _LOCK:
        hit = _RECENT.get(key)
    if hit is None:
        return None
    built, hpath, hcfg, frozen = hit
    if hpath != path or hcfg != cfg or (time.time() - built) > max_age or (time.time() - built) < 0:
        return None
    return built, frozen


def _recent_put(key, path, cfg, built, frozen, epoch) -> None:
    with _LOCK:
        if epoch == _EPOCH[0]:
            _RECENT[key] = (built, path, cfg, frozen)
            STATS["recent_store"] += 1
#: (函式名, 參數) → (指紋, 結果)。每個鍵只留最新一份，不會越長越大。
_STORE: dict = {}
#: 命中／重算次數（給測試與排查看）
STATS = {"hit": 0, "miss": 0, "bypass": 0, "scope_hit": 0, "stale_hit": 0, "recent_store": 0, "refresh": 0}


def enabled() -> bool:
    return os.environ.get("WEBIT3_FLEET_CACHE", "1").strip() != "0"


def _stat(p: str):
    try:
        st = os.stat(p)
        return (st.st_ino, st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _wal_state(p: str):
    """-wal 檔的狀態。**空的 WAL（0 bytes）＝沒有任何提交，內容等於主檔**，當作不存在：
    每條新連線都會重新建一個空 WAL（新的 inode、新的時間），算進指紋的話每個請求都會變，
    快取永遠不會命中（test_fleet_cache 抓到的）。有內容時才看大小、時間與表頭。"""
    try:
        st = os.stat(p)
    except OSError:
        return None
    if st.st_size == 0:
        return None
    # WAL 重設後會在原檔原大小上覆寫，提交之間只剩修改時間能分（Linux 時間粒度約 4～10ms）——
    # 再加 -shm 的 wal-index 表頭（iChange／mxFrame，每次提交都會變），獨立審查 #1
    return (st.st_size, st.st_mtime_ns, _wal_header(p), _shm_header(p[:-4] + "-shm"))


def _shm_header(p: str):
    try:
        with open(p, "rb") as f:
            return f.read(96)
    except OSError:
        return None


#: WAL 剛被寫過（這麼多秒內）就不存快取：寫入正在進行（收集一輪邊收邊寫）時，
#: 指紋的時間粒度追不上，乾脆照算（獨立審查 #1 的保險）
RECENT_WRITE_S = 0.2


def _recently_written(path: str) -> bool:
    try:
        st = os.stat(path + "-wal")
    except OSError:
        return False
    return st.st_size > 0 and (time.time() - st.st_mtime) < RECENT_WRITE_S


def _wal_header(p: str):
    """WAL 表頭（checkpoint 序號＋salt）：WAL 重新開始時一定會變，即使大小與時間沒變。"""
    try:
        with open(p, "rb") as f:
            return f.read(32)
    except OSError:
        return None


def _config_mtimes() -> tuple:
    out = []
    for p in sorted(_BACKEND_DIR.glob("*.json")):
        try:
            out.append((p.name, p.stat().st_mtime_ns))
        except OSError:
            out.append((p.name, None))
    return tuple(out)


def fingerprint(conn) -> tuple | None:
    """這個資料庫目前內容的指紋；拿不到（記憶體庫、非 SQLite 連線）回 None＝不快取。"""
    try:
        path = conn.execute("PRAGMA database_list").fetchone()[2]
    except (sqlite3.Error, AttributeError, TypeError, IndexError):
        return None
    if not path:
        return None
    return (path, _stat(path), _wal_state(path + "-wal"),
            _config_mtimes(), int(time.time() // TIME_BUCKET_S),
            # 台數口徑的環境變數覆寫（machine_identity.ENV_OVERRIDE）會改變算法本身
            os.environ.get("WEBIT3_MACHINE_KEY", ""))


def _dirty(conn) -> bool:
    try:
        return bool(conn.in_transaction) or conn.total_changes > 0
    except (AttributeError, sqlite3.Error):
        return True


def cached(fn=None, *, skip_if=None):
    """裝在「只吃 conn（＋可雜湊參數）、讀全機隊、回結果」的函式上。說明見模組開頭。

    skip_if(conn) 回 True 時這次照算、不存：給結果還取決於資料庫以外狀態的情況
    （例：收集正在跑——進度與「是否還活著」看的是現在時間與記憶體裡的停止旗標）。"""
    if fn is None:
        return lambda f: cached(f, skip_if=skip_if)
    name = f"{fn.__module__}.{fn.__qualname__}"

    @functools.wraps(fn)
    def wrapper(conn, *args, **kwargs):
        sc = _SCOPE.get()
        if sc is None or sc["conn"] != id(conn) or not enabled():
            return _inner(conn, *args, **kwargs)
        # 範圍內：自己寫過／交易沒結束／結果取決於資料庫以外狀態 → 照原本的保守路線，不用也不存範圍快照
        try:
            clean = (not _dirty(conn)) and conn.total_changes == sc["changes"]                 and not (skip_if is not None and _safe_skip(skip_if, conn))
            skey = (name, args, tuple(sorted(kwargs.items())))
            hash(skey)
        except (TypeError, sqlite3.Error):
            clean = False
        if not clean:
            return _inner(conn, *args, **kwargs)
        hit = sc["store"].get(skey)
        if hit is not None:
            STATS["scope_hit"] += 1
            return _thaw(hit)
        before = conn.total_changes
        val = _inner(conn, *args, **kwargs)
        # 算的途中寫了資料庫就不存；算完 skip_if（收集正在跑等資料庫以外的狀態）變成要跳過，也不存
        if conn.total_changes == before and not conn.in_transaction                 and not (skip_if is not None and _safe_skip(skip_if, conn)):
            sc["store"][skey] = _freeze(val)
        return val

    def _inner(conn, *args, **kwargs):
        if not enabled() or _dirty(conn) or (skip_if is not None and _safe_skip(skip_if, conn)):
            STATS["bypass"] += 1
            return fn(conn, *args, **kwargs)
        fp = fingerprint(conn)
        try:
            key = (name, args, tuple(sorted(kwargs.items())))
            hash(key)
        except TypeError:
            fp = None
        if fp is None:
            STATS["bypass"] += 1
            return fn(conn, *args, **kwargs)
        with _LOCK:
            hit = _STORE.get(key)
        if hit is not None and hit[0] == fp:
            STATS["hit"] += 1
            return _thaw(hit[1])
        st = _STALE.get()
        if st is not None:
            # 限時舊值層（主機履歷頁專用）：記下這把鍵（背景重算用）；有夠新的就直接拿
            _remember_hot(key, wrapper, args, kwargs, fp[0])
            got = None if _FORCE_FRESH.get() else _recent_get(key, fp[0], _cfg_sig(fp), st["max_age"])
            if got is not None:
                return _use_stale(st, got)
        # 單飛（2.007.48，使用者：「公司正式機搜尋很慢」）：快取是冷的（剛有寫入／過了 5 分鐘時間格）時，
        # 打字連送好幾個搜尋，每個都各自把整個機隊重算一遍（本機 6 千台約 1.2 秒 × 同時 N 個）。
        # 同一把鍵同一時間只讓一個去算，其他的等它算完直接拿結果；拿不到（算的人沒存、指紋又變了）才自己算。
        # 用可重入鎖：函式算的途中又呼叫自己（同鍵）不會卡死。
        with _flight_lock(key):
            with _LOCK:
                hit = _STORE.get(key)
            if hit is not None and hit[0] == fp:
                STATS["hit"] += 1
                return _thaw(hit[1])
            if st is not None and not _FORCE_FRESH.get():
                got = _recent_get(key, fp[0], _cfg_sig(fp), st["max_age"])
                if got is not None:
                    return _use_stale(st, got)
            STATS["miss"] += 1
            before = conn.total_changes
            t0, ep = time.time(), _EPOCH[0]
            val = fn(conn, *args, **kwargs)
            clean = conn.total_changes == before and not conn.in_transaction
            frozen = None
            # 算的過程寫了資料庫（有副作用）→ 不存；算完檔案又變了（別人剛好寫入）→ 也不存
            if clean and fingerprint(conn) == fp and not _recently_written(fp[0]):
                frozen = _freeze(val)
                with _LOCK:
                    _STORE[key] = (fp, frozen)
            if st is not None and clean and not (skip_if is not None and _safe_skip(skip_if, conn)):
                # 限時舊值層：不管檔案有沒有在變都存（起點時間＝這份結果代表的時刻）；有人寫入過（epoch 變了）就不存
                _recent_put(key, fp[0], _cfg_sig(fp), t0, frozen if frozen is not None else _freeze(val), ep)
            return val

    wrapper.__wrapped_uncached__ = fn
    return wrapper


def _use_stale(st, got):
    built, frozen = got
    rep = st["rep"]
    rep["used"] += 1
    rep["oldest_built"] = built if rep["oldest_built"] is None else min(rep["oldest_built"], built)
    rep["max_age_s"] = max(rep["max_age_s"], round(time.time() - built, 1))
    STATS["stale_hit"] += 1
    return _thaw(frozen)


def _remember_hot(key, wrapper, args, kwargs, path) -> None:
    """限時舊值層用過的鍵：背景重算（refresh_hot）靠它知道要重算哪幾把。重算自己不算「有人在看」。"""
    if _FORCE_FRESH.get():
        return
    with _LOCK:
        _HOT[key] = {"fn": wrapper, "args": args, "kwargs": kwargs, "path": path, "seen": time.time()}
    _maybe_start_refresher()


def refresh_hot() -> int:
    """把近期有人用 stale_ok 看的那幾把全機隊計算重算一遍（背景執行緒每 REFRESH_EVERY_S 秒呼叫一次；測試直接呼叫）。
    超過 HOT_IDLE_S 沒人看的鍵不再重算（沒人看主機頁時背景完全不花 CPU）。回重算了幾把。"""
    if not enabled() or stale_max_age() <= 0:
        return 0
    now = time.time()
    with _LOCK:
        for k in [k for k, v in _HOT.items() if now - v["seen"] > HOT_IDLE_S]:
            del _HOT[k]
        due = []
        for k, v in _HOT.items():
            rec = _RECENT.get(k)
            if rec is None or now - rec[0] >= REFRESH_MIN_AGE_S:
                due.append((k, dict(v)))
    if not due:
        return 0
    import db as _db

    n = 0
    by_path: dict = {}
    for k, v in due:
        by_path.setdefault(v["path"], []).append(v)
    for path, items in by_path.items():
        try:
            conn = _db.get_connection(Path(path))
        except Exception:  # noqa: BLE001 - 庫不見了／開不了就跳過，下一輪再試
            STATS["refresh_err"] = STATS.get("refresh_err", 0) + 1
            continue
        t1 = _FORCE_FRESH.set(True)
        t2 = _STALE.set({"max_age": 0.0, "rep": {"used": 0, "oldest_built": None, "max_age_s": 0.0}})
        try:
            with request_scope(conn):
                for v in items:
                    try:
                        v["fn"](conn, *v["args"], **v["kwargs"])
                        n += 1
                    except Exception:  # noqa: BLE001 - 一把算不出來不拖累其他把；舊值到期後頁面自己即時算並顯示錯誤
                        STATS["refresh_err"] = STATS.get("refresh_err", 0) + 1
        finally:
            _STALE.reset(t2)
            _FORCE_FRESH.reset(t1)
            conn.close()
    STATS["refresh"] += n
    return n


_REFRESHER = {"thread": None}


def _refresher_wanted() -> bool:
    if stale_max_age() <= 0:
        return False                                    # 舊值層關著就不需要背景重算（預設）
    v = os.environ.get("WEBIT3_FLEET_REFRESH")
    if v is not None:
        return v.strip() == "1"
    return os.environ.get("ASSET_SCHEDULER") == "1"     # 舊值層開啟時：正式服務（有排程器的那個行程）預設開；測試與命令列不開


def _maybe_start_refresher() -> None:
    if _REFRESHER["thread"] is not None or not _refresher_wanted():
        return
    with _LOCK:
        if _REFRESHER["thread"] is not None:
            return

        def loop():
            while True:
                time.sleep(REFRESH_EVERY_S)
                try:
                    refresh_hot()
                except Exception:  # noqa: BLE001 - 背景執行緒不能因為一次失敗就死掉
                    STATS["refresh_err"] = STATS.get("refresh_err", 0) + 1

        t = threading.Thread(target=loop, name="fleet-cache-refresh", daemon=True)
        _REFRESHER["thread"] = t
        t.start()


def _freeze(val):
    """存進快取的副本。2.006.2：用 pickle 位元組（比 deepcopy 快約 5 倍——主機履歷一次請求會命中十幾次，
    deepcopy 每次 0.1～0.6 秒，加起來比不快取還慢）；有不能 pickle 的東西（sqlite3.Row 等）才退回 deepcopy。"""
    try:
        return ("pickle", pickle.dumps(val, protocol=pickle.HIGHEST_PROTOCOL))
    except Exception:  # noqa: BLE001 - 不能 pickle 的結果照舊用深拷貝，行為不變
        return ("copy", copy.deepcopy(val))


def _thaw(frozen):
    kind, data = frozen
    return pickle.loads(data) if kind == "pickle" else copy.deepcopy(data)


def _safe_skip(skip_if, conn) -> bool:
    try:
        return bool(skip_if(conn))
    except Exception:  # noqa: BLE001 - 判斷不出來就當要跳過（寧可重算）
        return True


def clear() -> None:
    with _LOCK:
        _STORE.clear()
        _RECENT.clear()
        _HOT.clear()
        _EPOCH[0] += 1
