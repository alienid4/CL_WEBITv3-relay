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

加總＝下鑽不受影響：同一份資料指紋下，卡片與下鑽拿的是同一份計算結果。
"""
from __future__ import annotations

import copy
import functools
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
#: 每把鍵一個的「正在算」鎖（單飛用）。鍵的數量有限（函式名＋參數），不會越長越大
_FLIGHT: dict = {}


def _flight_lock(key):
    with _LOCK:
        lk = _FLIGHT.get(key)
        if lk is None:
            lk = _FLIGHT[key] = threading.RLock()
        return lk
#: (函式名, 參數) → (指紋, 結果)。每個鍵只留最新一份，不會越長越大。
_STORE: dict = {}
#: 命中／重算次數（給測試與排查看）
STATS = {"hit": 0, "miss": 0, "bypass": 0}


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
            STATS["miss"] += 1
            before = conn.total_changes
            val = fn(conn, *args, **kwargs)
            # 算的過程寫了資料庫（有副作用）→ 不存；算完檔案又變了（別人剛好寫入）→ 也不存
            if (conn.total_changes == before and not conn.in_transaction and fingerprint(conn) == fp
                    and not _recently_written(fp[0])):
                with _LOCK:
                    _STORE[key] = (fp, _freeze(val))
            return val

    wrapper.__wrapped_uncached__ = fn
    return wrapper


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
